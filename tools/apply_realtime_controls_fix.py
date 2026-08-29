from pathlib import Path

root = Path(__file__).resolve().parents[1]
manager_path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt"
sony_path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
ui_path = root / "demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt"
manager = manager_path.read_text()
sony = sony_path.read_text()
ui = ui_path.read_text()

# 1) Interactive telemetry cadence: camera-side changes should surface quickly,
# while stale-read protection remains active around app-originated writes.
manager = manager.replace(
'''        private const val EXPOSURE_POLL_INTERVAL_MS = 900L
        private const val SETTINGS_POLL_INTERVAL_MS = 3_000L
        private const val TELEMETRY_WARMUP_MS = 2_000L
        private const val CONTROL_POLL_QUIET_MS = 1_000L
''',
'''        private const val EXPOSURE_POLL_INTERVAL_MS = 250L
        private const val SETTINGS_POLL_INTERVAL_MS = 900L
        private const val TELEMETRY_WARMUP_MS = 700L
        private const val CONTROL_POLL_QUIET_MS = 220L
''')

old_control = '''    @Volatile private var controlEpoch = 0L
    @Volatile private var telemetryResumeAtMs = 0L

    private fun beginControlWrite(): Long = synchronized(controlEpochLock) {
        controlEpoch += 1L
        telemetryResumeAtMs = Long.MAX_VALUE
        controlEpoch
    }

    private fun endControlWrite(epoch: Long) = synchronized(controlEpochLock) {
        if (controlEpoch == epoch) {
            telemetryResumeAtMs = System.currentTimeMillis() + CONTROL_POLL_QUIET_MS
        }
    }
'''
new_control = '''    @Volatile private var controlEpoch = 0L
    @Volatile private var telemetryResumeAtMs = 0L
    @Volatile private var controlWriteActive = false
    @Volatile private var afHalfPressHeld = false
    private var afReleaseJob: Job? = null
    private var afGeneration = 0L

    private fun beginControlWrite(): Long = synchronized(controlEpochLock) {
        controlEpoch += 1L
        controlWriteActive = true
        telemetryResumeAtMs = Long.MAX_VALUE
        controlEpoch
    }

    private fun endControlWrite(epoch: Long) = synchronized(controlEpochLock) {
        if (controlEpoch == epoch) {
            controlWriteActive = false
            telemetryResumeAtMs = System.currentTimeMillis() + CONTROL_POLL_QUIET_MS
        }
    }
'''
assert old_control in manager, "control epoch block changed"
manager = manager.replace(old_control, new_control)

old_loop = '''            while (isActive && isLiveviewActive) {
                try {
                    val frameStart = System.currentTimeMillis()
                    val liveFrame = ptpCamera?.getLiveViewFrameData()
'''
new_loop = '''            while (isActive && isLiveviewActive) {
                try {
                    // Do not start another GetObject while a user control is waiting.
                    // The PTP transaction already in flight is allowed to finish; then
                    // AF/exposure gets the bus before the next live-view frame.
                    if (controlWriteActive) {
                        delay(2)
                        continue
                    }
                    val frameStart = System.currentTimeMillis()
                    val liveFrame = ptpCamera?.getLiveViewFrameData()
'''
assert old_loop in manager, "liveview loop start changed"
manager = manager.replace(old_loop, new_loop)

manager = manager.replace(
'''                        if (telemetryNow - liveviewStartTime >= TELEMETRY_WARMUP_MS &&
                            telemetryNow >= telemetryResumeAtMs
                        ) {
''',
'''                        if (!controlWriteActive &&
                            telemetryNow - liveviewStartTime >= TELEMETRY_WARMUP_MS &&
                            telemetryNow >= telemetryResumeAtMs
                        ) {
''')

old_af = '''    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult = withContext(Dispatchers.IO) {
        controlWriteMutex.withLock {
            val camera = ptpCamera
                ?: return@withLock CameraOperationResult.Failure("Camera not connected")
            val safeX = x.coerceIn(0, 639)
            val safeY = y.coerceIn(0, 479)
            val epoch = beginControlWrite()
            try {
                val started = System.currentTimeMillis()
                val message = camera.setAfPoint(safeX, safeY)
                Log.d(TAG, "AF area position command completed in ${System.currentTimeMillis() - started}ms")
                _events.emit(CameraEvent.FocusDebug(message))
                _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
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
'''
new_af = '''    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult = withContext(Dispatchers.IO) {
        controlWriteMutex.withLock {
            val camera = ptpCamera
                ?: return@withLock CameraOperationResult.Failure("Camera not connected")
            val safeX = x.coerceIn(0, 639)
            val safeY = y.coerceIn(0, 479)

            // A new tap supersedes the pending release from the previous tap.
            // If that AF press is still held, release it first so the new point
            // receives a genuine new focus trigger rather than merely moving a
            // target under an old half-press.
            afReleaseJob?.cancel()
            afReleaseJob = null
            afGeneration += 1L
            val generation = afGeneration

            val epoch = beginControlWrite()
            try {
                val started = System.currentTimeMillis()
                if (afHalfPressHeld) {
                    camera.setAutofocusPressed(false)
                    afHalfPressHeld = false
                }

                val moveMessage = camera.setAfPoint(safeX, safeY)
                val pressResult = camera.setAutofocusPressed(true)
                afHalfPressHeld = true
                val message = "$moveMessage | AF=${PtpConstants.responseCodeName(pressResult.responseCode)}"
                Log.d(TAG, "AF point+press completed in ${System.currentTimeMillis() - started}ms")
                _events.emit(CameraEvent.FocusDebug(message))
                _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))

                // Keep half-press long enough for AF to run, but release it in a
                // separate job so the tap command itself returns immediately.
                afReleaseJob = scope.launch(Dispatchers.IO) {
                    delay(320)
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
                }
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
'''
assert old_af in manager, "setAfPoint manager block changed"
manager = manager.replace(old_af, new_af)

# Keep teardown from leaving a delayed AF release job around.
old_close = '''    private fun closeUsbResources() {
        isLiveviewActive = false
        liveviewJob?.cancel()
        liveviewJob = null
'''
new_close = '''    private fun closeUsbResources() {
        isLiveviewActive = false
        liveviewJob?.cancel()
        liveviewJob = null
        afReleaseJob?.cancel()
        afReleaseJob = null
        afHalfPressHeld = false
        controlWriteActive = false
'''
assert old_close in manager, "closeUsbResources block changed"
manager = manager.replace(old_close, new_close)

# 2) AF semantics: position is still its own Sony control, while autofocus
# half-press is an explicit operation triggered by the UI tap workflow.
old_af_comment = '''    /**
     * Move the Sony logical AF target. Camera Remote Command exposes AF Area
     * Position (0xD2DC) as a standalone control; moving the target must not
     * implicitly press the shutter. A7C II uses a 640x480 logical grid.
     */
    fun setAfPoint(x: Int, y: Int): String = commandAfPoint("AF TARGET", x, y)
'''
new_af_comment = '''    /** Move the Sony logical AF target on the a7C II 640x480 logical grid. */
    fun setAfPoint(x: Int, y: Int): String = commandAfPoint("AF TARGET", x, y)

    /** Explicit autofocus trigger used after an AF-area position update. */
    fun setAutofocusPressed(pressed: Boolean): PtpResponse =
        setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, if (pressed) 2 else 1)
'''
assert old_af_comment in sony, "Sony AF comment block changed"
sony = sony.replace(old_af_comment, new_af_comment)

# 3) Cache the latest exposure state so high-rate phone writes do not perform a
# full read-before/write/read-after verification cycle for every sampled detent.
marker = '''    private val exposureDescriptors = linkedMapOf<CameraExposureSetting, ExposureDescriptor>()
    @Volatile private var exposureDescriptorsProbed = false
'''
replacement = '''    private val exposureDescriptors = linkedMapOf<CameraExposureSetting, ExposureDescriptor>()
    @Volatile private var exposureDescriptorsProbed = false
    @Volatile private var lastExposureState: CameraExposureState? = null
'''
assert marker in sony, "exposure descriptor field block changed"
sony = sony.replace(marker, replacement)

sony = sony.replace(
'''        val all = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)
''',
'''        val all = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            700
        )
''',
1)

old_return_state = '''        return CameraExposureState(
            aperture = buildExposureProperty(apertureDesc, current(apertureDesc)),
            shutterSpeed = buildExposureProperty(shutterDesc, current(shutterDesc)),
            iso = buildExposureProperty(isoDesc, current(isoDesc))
        )
    }
'''
new_return_state = '''        val state = CameraExposureState(
            aperture = buildExposureProperty(apertureDesc, current(apertureDesc)),
            shutterSpeed = buildExposureProperty(shutterDesc, current(shutterDesc)),
            iso = buildExposureProperty(isoDesc, current(isoDesc))
        )
        lastExposureState = state
        return state
    }
'''
assert old_return_state in sony, "readExposureState return changed"
sony = sony.replace(old_return_state, new_return_state)

old_set_exposure = '''    /** Set one exact value selected from the Sony-style UI selector. */
    fun setExposureValue(
        setting: CameraExposureSetting,
        rawValue: Long
    ): ExposureAdjustmentResult {
        val before = readExposureState()
        val descriptor = exposureDescriptors[setting]
            ?: return ExposureAdjustmentResult(before, false, "${settingLabel(setting)} is unavailable")
        val property = before.property(setting)
        if (!property.writable || property.current == null) {
            return ExposureAdjustmentResult(before, false, "${settingLabel(setting)} is locked in this camera mode")
        }
        val response = setExposureRaw(descriptor, rawValue)
        Thread.sleep(170)
        var after = readExposureState()
        if (after.property(setting).current?.rawValue != rawValue) {
            Thread.sleep(230)
            after = readExposureState()
        }
        val applied = after.property(setting).current?.rawValue == rawValue
        return if (applied) {
            ExposureAdjustmentResult(after, true)
        } else {
            ExposureAdjustmentResult(
                after,
                false,
                if (response.isSuccess) "Camera did not apply ${formatExposureValue(setting, rawValue)}"
                else "Camera rejected ${settingLabel(setting)} (${PtpConstants.responseCodeName(response.responseCode)})"
            )
        }
    }
'''
new_set_exposure = '''    /**
     * Set one exact value selected from the Sony-style UI selector.
     *
     * Interactive writes must not synchronously perform a full 9209 read before
     * and after every detent. Send the Sony control immediately, publish an
     * optimistic state from the last authoritative snapshot, then let the fast
     * background telemetry poll confirm/correct it.
     */
    fun setExposureValue(
        setting: CameraExposureSetting,
        rawValue: Long
    ): ExposureAdjustmentResult {
        ensureExposureDescriptors()
        val descriptor = exposureDescriptors[setting]
        val before = lastExposureState ?: readExposureState()
        if (descriptor == null) {
            return ExposureAdjustmentResult(before, false, "${settingLabel(setting)} is unavailable")
        }
        val property = before.property(setting)
        if (!property.writable || property.current == null) {
            return ExposureAdjustmentResult(before, false, "${settingLabel(setting)} is locked in this camera mode")
        }

        val response = setExposureRaw(descriptor, rawValue)
        if (!response.isSuccess) {
            return ExposureAdjustmentResult(
                before,
                false,
                "Camera rejected ${settingLabel(setting)} (${PtpConstants.responseCodeName(response.responseCode)})"
            )
        }

        val target = property.options.firstOrNull { it.rawValue == rawValue }
            ?: CameraExposureOption(rawValue, formatExposureValue(setting, rawValue))
        val optimisticProperty = property.copy(current = target)
        val optimistic = when (setting) {
            CameraExposureSetting.APERTURE -> before.copy(aperture = optimisticProperty)
            CameraExposureSetting.SHUTTER_SPEED -> before.copy(shutterSpeed = optimisticProperty)
            CameraExposureSetting.ISO -> before.copy(iso = optimisticProperty)
        }
        lastExposureState = optimistic
        return ExposureAdjustmentResult(optimistic, true)
    }
'''
assert old_set_exposure in sony, "setExposureValue block changed"
sony = sony.replace(old_set_exposure, new_set_exposure)

# Keep generic settings responsive enough and cap low-priority telemetry reads.
sony = sony.replace(
'''        val response = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            2_000
        )
        val data = if (response.isSuccess) response.data else ByteArray(0)
''',
'''        val response = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            900
        )
        val data = if (response.isSuccess) response.data else ByteArray(0)
''',
1)

# 4) With fast writes, sample the continuous dial at 100 ms instead of 150 ms.
ui = ui.replace(
'''    // 150 ms we send only the newest detent, never all detents crossed since
''',
'''    // 100 ms we send only the newest detent, never all detents crossed since
''')
ui = ui.replace('''            delay(150)\n''', '''            delay(100)\n''', 1)

manager_path.write_text(manager)
sony_path.write_text(sony)
ui_path.write_text(ui)
Path(__file__).unlink()
