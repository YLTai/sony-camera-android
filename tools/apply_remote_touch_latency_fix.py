from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
manager_path = ROOT / 'sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt'
sony_path = ROOT / 'sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt'
ui_path = ROOT / 'demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt'

manager = manager_path.read_text()
sony = sony_path.read_text()
ui = ui_path.read_text()

old_latency = '''    private data class PendingAfFrameLatency(
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
'''

new_latency = '''    private data class PendingAfFrameLatency(
        val generation: Long,
        val x: Int,
        val y: Int,
        val requestedAtMs: Long,
        val ackAtMs: Long,
        val commandDoneAtMs: Long,
        val path: String,
        val s1Ms: Long?,
        val baseline: CameraFocusFrameInfo?,
        var firstGeometryChangeAtMs: Long? = null
    )

    private data class FocusTargetDistance(
        val dxPx: Float,
        val dyPx: Float
    ) {
        val maxErrorPx: Float get() = maxOf(abs(dxPx), abs(dyPx))
    }

    private val afStateLock = Any()
    @Volatile private var pendingAfFrameLatency: PendingAfFrameLatency? = null
    @Volatile private var latestFocusFrameInfo: CameraFocusFrameInfo? = null

    private fun focusGeometryChanged(
        before: CameraFocusFrameInfo?,
        after: CameraFocusFrameInfo
    ): Boolean {
        val oldFrames = before?.frames ?: return false
        if (oldFrames.size != after.frames.size) return true
        return oldFrames.indices.any { index ->
            val old = oldFrames[index]
            val new = after.frames[index]
            old.xNumerator != new.xNumerator || old.yNumerator != new.yNumerator ||
                old.xDenominator != new.xDenominator || old.yDenominator != new.yDenominator ||
                old.width != new.width || old.height != new.height
        }
    }

    private fun nearestFocusTargetDistance(
        info: CameraFocusFrameInfo,
        x: Int,
        y: Int
    ): FocusTargetDistance? {
        if (info.frames.isEmpty()) return null
        val targetX = x / 639f
        val targetY = y / 479f
        return info.frames.map { frame ->
            FocusTargetDistance(
                dxPx = (frame.centerXNormalized - targetX) * 639f,
                dyPx = (frame.centerYNormalized - targetY) * 479f
            )
        }.minByOrNull { it.maxErrorPx }
    }

    private fun observeAfFrameLatency(info: CameraFocusFrameInfo) {
        val pending = synchronized(afStateLock) { pendingAfFrameLatency } ?: return
        val now = System.currentTimeMillis()

        if (pending.firstGeometryChangeAtMs == null && focusGeometryChanged(pending.baseline, info)) {
            synchronized(afStateLock) {
                val current = pendingAfFrameLatency
                if (current?.generation == pending.generation && current.firstGeometryChangeAtMs == null) {
                    current.firstGeometryChangeAtMs = now
                }
            }
        }

        val nearest = nearestFocusTargetDistance(info, pending.x, pending.y)
        // The old metric used 75% of the returned frame size as tolerance, so a
        // stale/large frame could count as the new target long before it moved.
        // Use an absolute camera-grid tolerance instead: 12 px on the 640x480 grid.
        val matched = nearest != null && nearest.maxErrorPx <= 12f
        val elapsed = now - pending.requestedAtMs
        if (!matched && elapsed < 2_000L) return

        val claimed = synchronized(afStateLock) {
            val current = pendingAfFrameLatency
            if (current?.generation != pending.generation) {
                null
            } else {
                pendingAfFrameLatency = null
                current
            }
        } ?: return

        val ackMs = claimed.ackAtMs - claimed.requestedAtMs
        val commandMs = claimed.commandDoneAtMs - claimed.requestedAtMs
        val changedMs = claimed.firstGeometryChangeAtMs?.minus(claimed.requestedAtMs)
        val s1Text = claimed.s1Ms?.let { " s1=${it}ms" } ?: ""
        if (matched && nearest != null) {
            val frameMs = now - claimed.requestedAtMs
            val afterAckMs = now - claimed.ackAtMs
            val afterCommandMs = now - claimed.commandDoneAtMs
            _events.tryEmit(
                CameraEvent.FocusDebug(
                    "AF FRAME ${claimed.path} x=${claimed.x} y=${claimed.y}\\n" +
                        "ack=${ackMs}ms$s1Text cmd=${commandMs}ms change=${changedMs?.let { "${it}ms" } ?: "n/a"}\\n" +
                        "target=${frameMs}ms afterAck=${afterAckMs}ms afterCmd=${afterCommandMs}ms err<=${nearest.maxErrorPx.toInt()}px"
                )
            )
        } else {
            val nearestText = nearest?.maxErrorPx?.toInt()?.let { " nearest=${it}px" } ?: " no-frame"
            _events.tryEmit(
                CameraEvent.FocusDebug(
                    "AF FRAME ${claimed.path} x=${claimed.x} y=${claimed.y}\\n" +
                        "ack=${ackMs}ms$s1Text cmd=${commandMs}ms change=${changedMs?.let { "${it}ms" } ?: "n/a"}\\n" +
                        "target=>2000ms$nearestText"
                )
            )
        }
    }
'''

if old_latency not in manager:
    raise SystemExit('manager latency block not found')
manager = manager.replace(old_latency, new_latency, 1)

old_focus_emit = '''                            observeAfFrameLatency(cameraInfo)
                            if (cameraInfo != lastFocusFrameInfo) {
'''
new_focus_emit = '''                            latestFocusFrameInfo = cameraInfo
                            observeAfFrameLatency(cameraInfo)
                            if (cameraInfo != lastFocusFrameInfo) {
'''
if old_focus_emit not in manager:
    raise SystemExit('focus emit block not found')
manager = manager.replace(old_focus_emit, new_focus_emit, 1)

start = manager.index('    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult {')
end = manager.index('\n    override suspend fun testAfCenter()', start)
new_set_af = '''    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult {
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

                        afReleaseJob?.cancel()
                        afReleaseJob = null
                        afGeneration += 1L
                        val generation = afGeneration

                        // Never carry a fallback S1 hold into a new Remote Touch.
                        if (afHalfPressHeld) {
                            camera.setAutofocusPressed(false)
                            afHalfPressHeld = false
                        }

                        val baseline = latestFocusFrameInfo

                        // a7C II fast path: RemoteTouchOperation (D2E4) with
                        // FunctionOfRemoteTouchOperation (E083) prepared as Spot AF.
                        // This is one camera touch action; it does not need a second
                        // synthetic shutter-half-press transaction.
                        if (camera.supportsRemoteTouch()) {
                            val touchStartedMs = System.currentTimeMillis()
                            val touch = camera.executeRemoteTouch(safeX, safeY)
                            val touchAckAtMs = System.currentTimeMillis()
                            val wireAndAckMs = (touchAckAtMs - touchStartedMs - touch.queueWaitMs).coerceAtLeast(0L)
                            val ackMs = touchAckAtMs - requestedAtMs
                            if (touch.isSuccess) {
                                synchronized(afStateLock) {
                                    pendingAfFrameLatency = PendingAfFrameLatency(
                                        generation = generation,
                                        x = safeX,
                                        y = safeY,
                                        requestedAtMs = requestedAtMs,
                                        ackAtMs = touchAckAtMs,
                                        commandDoneAtMs = touchAckAtMs,
                                        path = "RT(D2E4)",
                                        s1Ms = null,
                                        baseline = baseline
                                    )
                                }
                                val message = "AF RT(D2E4) x=$safeX y=$safeY\\n" +
                                    "$prepDebug\\n" +
                                    "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${touch.queueWaitMs}ms wire+ack=${wireAndAckMs}ms ack=${ackMs}ms"
                                Log.d(TAG, message.replace('\\n', ' '))
                                _events.emit(CameraEvent.FocusDebug(message))
                                _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
                                return@withLock CameraOperationResult.SuccessWithData(message)
                            }
                            Log.w(TAG, "Remote Touch failed (${PtpConstants.responseCodeName(touch.responseCode)}); using D2DC+S1 fallback")
                        }

                        // Compatibility fallback: move AF Area Position first, then
                        // explicitly press S1. This remains available if Remote Touch
                        // is not exposed/enabled by the connected body.
                        val moveStartedMs = System.currentTimeMillis()
                        val move = camera.moveAfAreaPosition(safeX, safeY)
                        val ackAtMs = System.currentTimeMillis()
                        val wireAndAckMs = (ackAtMs - moveStartedMs - move.queueWaitMs).coerceAtLeast(0L)
                        val ackMs = ackAtMs - requestedAtMs

                        if (!move.isSuccess) {
                            synchronized(afStateLock) { pendingAfFrameLatency = null }
                            val message = "AF D2DC FAIL x=$safeX y=$safeY\\n$prepDebug\\n" +
                                "D2DC=${PtpConstants.responseCodeName(move.responseCode)} ack=${ackMs}ms"
                            _events.emit(CameraEvent.FocusDebug(message))
                            return@withLock CameraOperationResult.Failure(message)
                        }

                        val s1StartedMs = System.currentTimeMillis()
                        val pressResult = camera.setAutofocusPressed(true)
                        val s1AckAtMs = System.currentTimeMillis()
                        val s1Ms = s1AckAtMs - s1StartedMs
                        afHalfPressHeld = pressResult.isSuccess

                        if (!pressResult.isSuccess) {
                            synchronized(afStateLock) { pendingAfFrameLatency = null }
                            val message = "AF D2DC+S1 FAIL x=$safeX y=$safeY\\n$prepDebug\\n" +
                                "moveAck=${ackMs}ms s1=${s1Ms}ms ${PtpConstants.responseCodeName(pressResult.responseCode)}"
                            _events.emit(CameraEvent.FocusDebug(message))
                            return@withLock CameraOperationResult.Failure(message)
                        }

                        synchronized(afStateLock) {
                            pendingAfFrameLatency = PendingAfFrameLatency(
                                generation = generation,
                                x = safeX,
                                y = safeY,
                                requestedAtMs = requestedAtMs,
                                ackAtMs = ackAtMs,
                                commandDoneAtMs = s1AckAtMs,
                                path = "D2DC+S1",
                                s1Ms = s1Ms,
                                baseline = baseline
                            )
                        }

                        val message = "AF D2DC+S1 x=$safeX y=$safeY\\n" +
                            "$prepDebug\\n" +
                            "moveAck=${ackMs}ms s1=${s1Ms}ms bus=${move.queueWaitMs}ms wire+ack=${wireAndAckMs}ms"
                        Log.d(TAG, message.replace('\\n', ' '))
                        _events.emit(CameraEvent.FocusDebug(message))
                        _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
                        scheduleAutofocusRelease(camera, generation)
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
manager = manager[:start] + new_set_af + manager[end:]

old_close = '''        synchronized(afStateLock) { pendingAfFrameLatency = null }
        controlWriteActive = false
'''
new_close = '''        synchronized(afStateLock) { pendingAfFrameLatency = null }
        latestFocusFrameInfo = null
        controlWriteActive = false
'''
if old_close not in manager:
    raise SystemExit('close focus reset block not found')
manager = manager.replace(old_close, new_close, 1)

old_prepare = '''        var data = snapshot.data
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
'''
new_prepare = '''        var data = snapshot.data
        val enable = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS, 0x0002
        )
        var function = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION, 0x0002
        )
        val enableRaw = enable?.currentValue
        var functionRaw = function?.currentValue
        var functionWrite: PtpResponse? = null

        // Sony's Remote Touch operation is only the ACTION. Prepare E083 as
        // Spot AF once per session so a monitor click means "focus here" in one
        // D2E4 transaction instead of "move AF area, then synthesize S1".
        if (enableRaw == 1L && function != null && functionRaw != 2L &&
            function.writable && (function.enumValues.isEmpty() || 2L in function.enumValues)
        ) {
            functionWrite = setSonyScalarProperty(function, 2L)
            if (functionWrite.isSuccess) {
                val verifyFunction = transport.sendCommandWithDataShortTimeout(
                    PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
                    500
                )
                if (verifyFunction.isSuccess && verifyFunction.data.isNotEmpty()) {
                    data = verifyFunction.data
                    function = findSonyScalarEnumProperty(
                        data, PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION, 0x0002
                    )
                    functionRaw = function?.currentValue
                }
            }
        }
        remoteTouchSupported = enableRaw == 1L && functionRaw == 2L

        // Keep Spot S prepared as a compatibility fallback for bodies/sessions
        // that do not expose Remote Touch. The a7C II normally takes D2E4 above.
'''
if old_prepare not in sony:
    raise SystemExit('Sony prepare remote touch block not found')
sony = sony.replace(old_prepare, new_prepare, 1)

old_ready = '''        val spotReady = focusArea?.currentValue == 5L || spotWrite?.isSuccess == true
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
'''
new_ready = '''        val spotReady = focusArea?.currentValue == 5L || spotWrite?.isSuccess == true
        monitorAfPrepared = remoteTouchSupported || spotReady
        monitorAfDebugState = buildString {
            if (remoteTouchSupported) append("AF RT SpotAF ready")
            else append("AF AREA SpotS ").append(if (spotReady) "ready" else "NOT READY")
            append(" rtEn=").append(enableRaw ?: -1)
            append(" func=").append(functionRaw ?: -1)
            append(" area=").append(focusArea?.currentValue ?: -1)
            if (functionWrite != null) {
                append(" fset=").append(PtpConstants.responseCodeName(functionWrite.responseCode))
            }
            if (spotWrite != null) {
                append(" sset=").append(PtpConstants.responseCodeName(spotWrite.responseCode))
            }
        }
'''
if old_ready not in sony:
    raise SystemExit('Sony monitor ready block not found')
sony = sony.replace(old_ready, new_ready, 1)

old_ui = '''                        maxLines = 4,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .padding(start = 10.dp, top = if (menusVisible) 64.dp else 10.dp)
                            .widthIn(max = 360.dp)
'''
new_ui = '''                        maxLines = 6,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .padding(start = 10.dp, top = if (menusVisible) 64.dp else 10.dp)
                            .widthIn(max = 420.dp)
'''
if old_ui not in ui:
    raise SystemExit('focus debug UI block not found')
ui = ui.replace(old_ui, new_ui, 1)

manager_path.write_text(manager)
sony_path.write_text(sony)
ui_path.write_text(ui)
Path(__file__).unlink()
