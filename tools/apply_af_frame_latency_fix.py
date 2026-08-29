from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sony_path = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
manager_path = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt"

sony = sony_path.read_text()
manager = manager_path.read_text()

# 1) Remote Touch ACK is not AF-area application. Prepare Spot S for the
# AF Area Position path and keep Remote Touch state only as diagnostics.
start = sony.index("    @Synchronized\n    fun prepareMonitorTapAf(): String {")
end = sony.index("    fun monitorAfDebug(): String = monitorAfDebugState", start)
new_prepare = '''    @Synchronized
    fun prepareMonitorTapAf(): String {
        if (monitorAfPrepared) return monitorAfDebugState
        if (deviceName?.contains("ILCE-7CM2", ignoreCase = true) != true) {
            remoteTouchSupported = false
            monitorAfPrepared = true
            monitorAfDebugState = "AF AREA fallback (non-7CM2)"
            return monitorAfDebugState
        }

        var snapshot = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            700
        )
        if (!snapshot.isSuccess || snapshot.data.isEmpty()) {
            remoteTouchSupported = false
            monitorAfPrepared = false
            monitorAfDebugState = "AF prep 9209=${PtpConstants.responseCodeName(snapshot.responseCode)}"
            return monitorAfDebugState
        }

        var data = snapshot.data
        val enable = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS, 0x0002
        )
        val function = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION, 0x0002
        )
        val enableRaw = enable?.currentValue
        val functionRaw = function?.currentValue
        remoteTouchSupported = enableRaw == 1L && functionRaw == 2L

        // For monitor point movement use the same AF Area Position semantics as
        // Sony's RemoteSampleApp: Flexible/Spot S first, then the XY update.
        // Do not mutate FunctionOfRemoteTouchOperation here; D2E4 is a remote
        // touch ACTION and its fast ACK is not proof that the focus-area frame
        // has moved on the camera.
        var focusArea = findGenericSettingDescriptor(data, CameraSetting.FOCUS_AREA)
        var spotWrite: PtpResponse? = null
        if (focusArea != null && focusArea.currentValue != 5L) {
            spotWrite = setGenericSettingRaw(focusArea, 5L)
            if (spotWrite.isSuccess) {
                val verify = transport.sendCommandWithDataShortTimeout(
                    PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
                    500
                )
                if (verify.isSuccess && verify.data.isNotEmpty()) {
                    data = verify.data
                    focusArea = findGenericSettingDescriptor(data, CameraSetting.FOCUS_AREA)
                }
            }
        }

        val spotReady = focusArea?.currentValue == 5L || spotWrite?.isSuccess == true
        monitorAfPrepared = spotReady
        monitorAfDebugState = buildString {
            append("AF AREA SpotS ").append(if (spotReady) "ready" else "NOT READY")
            append(" rtEn=").append(enableRaw ?: -1)
            append(" func=").append(functionRaw ?: -1)
            append(" area=").append(focusArea?.currentValue ?: -1)
            if (spotWrite != null) {
                append(" sset=").append(PtpConstants.responseCodeName(spotWrite.responseCode))
            }
        }
        return monitorAfDebugState
    }

    @Synchronized
    fun invalidateMonitorTapAf() {
        monitorAfPrepared = false
        remoteTouchSupported = false
        monitorAfDebugState = "AF path invalidated"
    }

'''
sony = sony[:start] + new_prepare + sony[end:]

old_move_anchor = '''    /** Move the Sony logical AF target on the a7C II 640x480 logical grid. */
    fun setAfPoint(x: Int, y: Int): String = commandAfPoint("AF TARGET", x, y)
'''
new_move_anchor = '''    /** High-priority AF-area move used by monitor taps. */
    fun moveAfAreaPosition(x: Int, y: Int): PtpResponse = setAfAreaPosition(x, y)

    /** Move the Sony logical AF target on the a7C II 640x480 logical grid. */
    fun setAfPoint(x: Int, y: Int): String = commandAfPoint("AF TARGET", x, y)
'''
if old_move_anchor not in sony:
    raise SystemExit("Sony move anchor not found")
sony = sony.replace(old_move_anchor, new_move_anchor, 1)
sony_path.write_text(sony)

# 2) Manager: measure command ACK separately from the first camera-returned
# FocusFrame at the requested point, and trigger AF after a short one-frame gap.
if "import kotlin.math.abs\n" not in manager:
    manager = manager.replace(
        "import java.util.concurrent.atomic.AtomicInteger\n",
        "import java.util.concurrent.atomic.AtomicInteger\nimport kotlin.math.abs\n",
        1,
    )

old_fields = '''    @Volatile private var afHalfPressHeld = false
    private var afReleaseJob: Job? = null
    private var afGeneration = 0L

    private fun beginControlWrite(): Long = synchronized(controlEpochLock) {
'''
new_fields = '''    @Volatile private var afHalfPressHeld = false
    private var afReleaseJob: Job? = null
    private var afTriggerJob: Job? = null
    private var afGeneration = 0L

    private data class PendingAfFrameLatency(
        val generation: Long,
        val x: Int,
        val y: Int,
        val requestedAtMs: Long,
        val ackAtMs: Long
    )

    private val afStateLock = Any()
    @Volatile private var pendingAfFrameLatency: PendingAfFrameLatency? = null

    private fun focusFramesContainTarget(info: CameraFocusFrameInfo, x: Int, y: Int): Boolean {
        if (info.frames.isEmpty()) return false
        val targetX = x / 639f
        val targetY = y / 479f
        return info.frames.any { frame ->
            val toleranceX = maxOf(0.025f, frame.widthNormalized * 0.75f)
            val toleranceY = maxOf(0.030f, frame.heightNormalized * 0.75f)
            abs(frame.centerXNormalized - targetX) <= toleranceX &&
                abs(frame.centerYNormalized - targetY) <= toleranceY
        }
    }

    private fun observeAfFrameLatency(info: CameraFocusFrameInfo) {
        val pending = synchronized(afStateLock) { pendingAfFrameLatency } ?: return
        val now = System.currentTimeMillis()
        val matched = focusFramesContainTarget(info, pending.x, pending.y)
        val elapsed = now - pending.requestedAtMs
        if (!matched && elapsed < 1_500L) return

        val claimed = synchronized(afStateLock) {
            val current = pendingAfFrameLatency
            if (current?.generation != pending.generation) {
                false
            } else {
                pendingAfFrameLatency = null
                true
            }
        }
        if (!claimed) return

        val ackMs = pending.ackAtMs - pending.requestedAtMs
        if (matched) {
            val frameMs = now - pending.requestedAtMs
            val afterAckMs = now - pending.ackAtMs
            _events.tryEmit(
                CameraEvent.FocusDebug(
                    "AF CAMERA FRAME | x=${pending.x} y=${pending.y} | " +
                        "ack=${ackMs}ms frame=${frameMs}ms afterAck=${afterAckMs}ms"
                )
            )
        } else {
            _events.tryEmit(
                CameraEvent.FocusDebug(
                    "AF CAMERA FRAME | x=${pending.x} y=${pending.y} | " +
                        "ack=${ackMs}ms frame=>1500ms (target not returned)"
                )
            )
        }
    }

    private fun scheduleAutofocusAfterAreaMove(camera: SonyPtpCamera, generation: Long) {
        afTriggerJob?.cancel()
        afTriggerJob = scope.launch(Dispatchers.IO) {
            // Give the camera roughly one Live View frame to publish the moved
            // focus area before S1 starts lens AF. This keeps point movement and
            // autofocus as two distinct Sony operations instead of conflating them.
            delay(90)
            if (generation != afGeneration || ptpCamera !== camera) return@launch

            priorityControlIntents.incrementAndGet()
            var pressed = false
            try {
                controlWriteMutex.withLock {
                    if (generation != afGeneration || ptpCamera !== camera) return@withLock
                    val epoch = beginControlWrite()
                    try {
                        if (afHalfPressHeld) {
                            camera.setAutofocusPressed(false)
                            afHalfPressHeld = false
                        }
                        val press = camera.setAutofocusPressed(true)
                        pressed = press.isSuccess
                        afHalfPressHeld = pressed
                        Log.d(TAG, "Deferred AF S1 generation=$generation: " +
                            PtpConstants.responseCodeName(press.responseCode))
                    } finally {
                        endControlWrite(epoch)
                    }
                }
            } finally {
                priorityControlIntents.decrementAndGet()
            }

            if (!pressed) return@launch
            afReleaseJob?.cancel()
            afReleaseJob = scope.launch(Dispatchers.IO) {
                delay(280)
                priorityControlIntents.incrementAndGet()
                try {
                    controlWriteMutex.withLock {
                        if (generation != afGeneration || ptpCamera !== camera || !afHalfPressHeld) {
                            return@withLock
                        }
                        val releaseEpoch = beginControlWrite()
                        try {
                            camera.setAutofocusPressed(false)
                            afHalfPressHeld = false
                        } catch (e: Exception) {
                            Log.w(TAG, "AF half-press release failed: ${e.message}")
                        } finally {
                            endControlWrite(releaseEpoch)
                        }
                    }
                } finally {
                    priorityControlIntents.decrementAndGet()
                }
            }
        }
    }

    private fun beginControlWrite(): Long = synchronized(controlEpochLock) {
'''
if old_fields not in manager:
    raise SystemExit("Manager field anchor not found")
manager = manager.replace(old_fields, new_fields, 1)

old_focus_emit = '''                            if (cameraInfo != lastFocusFrameInfo) {
                                lastFocusFrameInfo = cameraInfo
                                _events.emit(CameraEvent.FocusFramesUpdated(cameraInfo))
                            }
'''
new_focus_emit = '''                            observeAfFrameLatency(cameraInfo)
                            if (cameraInfo != lastFocusFrameInfo) {
                                lastFocusFrameInfo = cameraInfo
                                _events.emit(CameraEvent.FocusFramesUpdated(cameraInfo))
                            }
'''
if old_focus_emit not in manager:
    raise SystemExit("Focus frame emit anchor not found")
manager = manager.replace(old_focus_emit, new_focus_emit, 1)

set_start = manager.index("    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult {")
set_end = manager.index("    override suspend fun testAfCenter(): CameraOperationResult", set_start)
new_set_af = '''    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult {
        // "total" on the old Remote Touch path only measured command ACK. For
        // point-movement UX that is the wrong metric: the user sees the first
        // Live View FocalFrameInfo carrying the new geometry. Record both.
        val requestedAtMs = System.currentTimeMillis()
        priorityControlIntents.incrementAndGet()
        return try {
            withContext(Dispatchers.IO) {
                controlWriteMutex.withLock {
                    val camera = ptpCamera
                        ?: return@withLock CameraOperationResult.Failure("Camera not connected")
                    val safeX = x.coerceIn(0, 639)
                    val safeY = y.coerceIn(0, 479)
                    val epoch = beginControlWrite()
                    try {
                        val commandStartedMs = System.currentTimeMillis()
                        val dispatchWaitMs = commandStartedMs - requestedAtMs
                        val prepStartedMs = System.currentTimeMillis()
                        val prepDebug = camera.prepareMonitorTapAf()
                        val prepMs = System.currentTimeMillis() - prepStartedMs

                        afTriggerJob?.cancel()
                        afReleaseJob?.cancel()
                        afGeneration += 1L
                        val generation = afGeneration

                        // A stale S1 from the previous tap can keep the camera in an
                        // active focusing state. Release it before moving the next area.
                        if (afHalfPressHeld) {
                            camera.setAutofocusPressed(false)
                            afHalfPressHeld = false
                        }

                        val moveStartedMs = System.currentTimeMillis()
                        val move = camera.moveAfAreaPosition(safeX, safeY)
                        val ackAtMs = System.currentTimeMillis()
                        val wireAndAckMs = (ackAtMs - moveStartedMs - move.queueWaitMs).coerceAtLeast(0L)
                        val ackMs = ackAtMs - requestedAtMs

                        if (!move.isSuccess) {
                            synchronized(afStateLock) { pendingAfFrameLatency = null }
                            val message = "AF MOVE | $prepDebug | x=$safeX y=$safeY | " +
                                "D2DC=${PtpConstants.responseCodeName(move.responseCode)} | ack=${ackMs}ms"
                            _events.emit(CameraEvent.FocusDebug(message))
                            return@withLock CameraOperationResult.Failure(message)
                        }

                        synchronized(afStateLock) {
                            pendingAfFrameLatency = PendingAfFrameLatency(
                                generation = generation,
                                x = safeX,
                                y = safeY,
                                requestedAtMs = requestedAtMs,
                                ackAtMs = ackAtMs
                            )
                        }

                        val message = "AF MOVE | $prepDebug | x=$safeX y=$safeY | D2DC=OK | " +
                            "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${move.queueWaitMs}ms " +
                            "wire+ack=${wireAndAckMs}ms ack=${ackMs}ms | waiting FRAME"
                        Log.d(TAG, message)
                        _events.emit(CameraEvent.FocusDebug(message))
                        _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))

                        // AF is deliberately a second operation. The 90ms gap lets
                        // the moved Spot-S frame reach Live View first on responsive
                        // bodies while still starting autofocus promptly.
                        scheduleAutofocusAfterAreaMove(camera, generation)
                        CameraOperationResult.SuccessWithData(message)
                    } catch (e: Exception) {
                        Log.e(TAG, "AF target command failed", e)
                        val message = "AF TARGET exception: ${e.message ?: e.javaClass.simpleName}"
                        _events.emit(CameraEvent.FocusDebug(message))
                        CameraOperationResult.Failure(message)
                    } finally {
                        endControlWrite(epoch)
                    }
                }
            }
        } finally {
            priorityControlIntents.decrementAndGet()
        }
    }

'''
manager = manager[:set_start] + new_set_af + manager[set_end:]

old_setting = '''                val result = camera.setCameraSettingValue(setting, rawValue)
                _events.emit(CameraEvent.CameraSettingsUpdated(result.state))
                if (result.success) CameraOperationResult.Success
                else CameraOperationResult.Failure(result.message ?: "Camera setting change failed")
'''
new_setting = '''                val result = camera.setCameraSettingValue(setting, rawValue)
                _events.emit(CameraEvent.CameraSettingsUpdated(result.state))
                if (result.success && setting == CameraSetting.FOCUS_AREA) {
                    camera.invalidateMonitorTapAf()
                }
                if (result.success) CameraOperationResult.Success
                else CameraOperationResult.Failure(result.message ?: "Camera setting change failed")
'''
if old_setting not in manager:
    raise SystemExit("Camera setting anchor not found")
manager = manager.replace(old_setting, new_setting, 1)

old_close = '''        afReleaseJob?.cancel()
        afReleaseJob = null
        afHalfPressHeld = false
        controlWriteActive = false
'''
new_close = '''        afTriggerJob?.cancel()
        afTriggerJob = null
        afReleaseJob?.cancel()
        afReleaseJob = null
        afHalfPressHeld = false
        synchronized(afStateLock) { pendingAfFrameLatency = null }
        controlWriteActive = false
'''
if old_close not in manager:
    raise SystemExit("Close anchor not found")
manager = manager.replace(old_close, new_close, 1)

manager_path.write_text(manager)

# One-shot patch: remove itself so no helper script remains in main.
Path(__file__).unlink()
