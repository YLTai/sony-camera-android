from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

# --- PTP constants ---------------------------------------------------------
path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/PtpConstants.kt")
text = path.read_text()
text = replace_once(
    text,
    '''    const val PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS = 0xD284\n    const val PROP_SONY_REMOTE_TOUCH_FUNCTION = 0xE083\n''',
    '''    const val PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS = 0xD284\n    // Runtime status around Remote Touch. D285 tells whether the matching\n    // cancel/up action is currently available; E004/E005 are Sony's native\n    // touch-spot / tracking focus states. They are read-only diagnostics here.\n    const val PROP_SONY_CANCEL_REMOTE_TOUCH_ENABLE_STATUS = 0xD285\n    const val PROP_SONY_FOCUS_TOUCH_SPOT_STATUS = 0xE004\n    const val PROP_SONY_FOCUS_TRACKING_STATUS = 0xE005\n    const val PROP_SONY_REMOTE_TOUCH_FUNCTION = 0xE083\n''',
    "PTP runtime status constants",
)
path.write_text(text)

# --- Sony PTP camera -------------------------------------------------------
path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")
text = path.read_text()

text = replace_once(
    text,
    '''    @Volatile\n    private var monitorAfDebugState = "AF path not prepared"\n\n    @Volatile\n    private var loggedLiveViewDataset = false\n''',
    '''    @Volatile\n    private var monitorAfDebugState = "AF path not prepared"\n\n    /** Camera-native status values surrounding Sony Remote Touch. */\n    data class RemoteTouchRuntimeStatus(\n        val focusTouchSpot: Long?,\n        val focusTracking: Long?,\n        val cancelEnable: Long?\n    )\n\n    @Volatile\n    private var remoteTouchRuntimeStatus: RemoteTouchRuntimeStatus? = null\n\n    @Volatile\n    private var loggedLiveViewDataset = false\n''',
    "runtime status data class",
)

text = replace_once(
    text,
    '''        remoteTouchSupported = false\n        monitorAfPrepared = false\n        monitorAfDebugState = "AF path awaiting camera property state"\n''',
    '''        remoteTouchSupported = false\n        monitorAfPrepared = false\n        remoteTouchRuntimeStatus = null\n        monitorAfDebugState = "AF path awaiting camera property state"\n''',
    "reset runtime status on init",
)

marker = '''    private fun setSonyScalarProperty(\n        descriptor: SonyScalarEnumProperty,\n        value: Long\n    ): PtpResponse {\n'''
insert = '''    private fun parseRemoteTouchRuntimeStatus(data: ByteArray): RemoteTouchRuntimeStatus {\n        fun value(code: Int): Long? = findSonyScalarEnumPropertyAnyType(data, code)?.currentValue\n        return RemoteTouchRuntimeStatus(\n            focusTouchSpot = value(PtpConstants.PROP_SONY_FOCUS_TOUCH_SPOT_STATUS),\n            focusTracking = value(PtpConstants.PROP_SONY_FOCUS_TRACKING_STATUS),\n            cancelEnable = value(PtpConstants.PROP_SONY_CANCEL_REMOTE_TOUCH_ENABLE_STATUS)\n        )\n    }\n\n    /** Last status seen in an already-required 0x9209 snapshot; no USB I/O. */\n    fun cachedRemoteTouchRuntimeStatus(): RemoteTouchRuntimeStatus? = remoteTouchRuntimeStatus\n\n    /**\n     * Read camera-native Remote Touch runtime status without ever queuing for\n     * the PTP transport. This diagnostic may drop a sample while Live View owns\n     * the bus, but it must never sit in front of a user control or restore the\n     * old long telemetry blocking behavior.\n     */\n    fun tryReadRemoteTouchRuntimeStatus(timeoutMs: Int = 60): RemoteTouchRuntimeStatus? {\n        val response = transport.trySendCommandWithDataShortTimeout(\n            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,\n            timeoutMs,\n            *sonyGetAllPropertyParams()\n        ) ?: return null\n        if (!response.isSuccess || response.data.isEmpty()) return null\n        val status = parseRemoteTouchRuntimeStatus(response.data)\n        remoteTouchRuntimeStatus = status\n        return status\n    }\n\n''' + marker
text = replace_once(text, marker, insert, "runtime status parser/read method")

text = replace_once(
    text,
    '''        var data = snapshot.data\n        fun property(code: Int): SonyScalarEnumProperty? =\n''',
    '''        var data = snapshot.data\n        remoteTouchRuntimeStatus = parseRemoteTouchRuntimeStatus(data)\n        fun property(code: Int): SonyScalarEnumProperty? =\n''',
    "cache AF prep runtime status",
)

text = replace_once(
    text,
    '''        val response = getAllSonyProperties(500)\n        val data = if (response.isSuccess) response.data else ByteArray(0)\n\n        fun prop(setting: CameraSetting): CameraSettingProperty {\n''',
    '''        val response = getAllSonyProperties(500)\n        val data = if (response.isSuccess) response.data else ByteArray(0)\n        if (data.isNotEmpty()) {\n            remoteTouchRuntimeStatus = parseRemoteTouchRuntimeStatus(data)\n        }\n\n        fun prop(setting: CameraSetting): CameraSettingProperty {\n''',
    "reuse settings snapshot for runtime cache",
)

path.write_text(text)

# --- USB manager -----------------------------------------------------------
path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt")
text = path.read_text()

text = replace_once(
    text,
    '''    private var afReleaseJob: Job? = null\n    private var afGeneration = 0L\n''',
    '''    private var afReleaseJob: Job? = null\n    private var remoteTouchRuntimeProbeJob: Job? = null\n    private var afGeneration = 0L\n''',
    "runtime probe job field",
)

marker = '''    private fun scheduleAutofocusRelease(camera: SonyPtpCamera, generation: Long) {\n'''
helper = r'''    /**
     * Observe Sony's own Remote Touch state for ~0.9 s after a successful D2E4.
     * Samples use the transport's non-queued tryLock path, so Live View can lose
     * an occasional frame but this diagnostic can never wait ahead of a control.
     * We record the first two value edges because a DOWN/UP-style state may go
     * active quickly and settle only around the user's visible body-LCD delay.
     */
    private fun startRemoteTouchRuntimeProbe(
        camera: SonyPtpCamera,
        generation: Long,
        requestedAtMs: Long,
        ackAtMs: Long,
        x: Int,
        y: Int
    ) {
        remoteTouchRuntimeProbeJob?.cancel()
        val baseline = camera.cachedRemoteTouchRuntimeStatus()
        remoteTouchRuntimeProbeJob = scope.launch(Dispatchers.IO) {
            class EdgeTracker(initial: Long?) {
                private var initialized = initial != null
                private var previous = initial
                var firstAtMs: Long? = null
                    private set
                var secondAtMs: Long? = null
                    private set

                fun observe(value: Long?, elapsedMs: Long) {
                    if (value == null) return
                    if (!initialized) {
                        initialized = true
                        previous = value
                        return
                    }
                    if (value == previous) return
                    if (firstAtMs == null) firstAtMs = elapsedMs
                    else if (secondAtMs == null) secondAtMs = elapsedMs
                    previous = value
                }

                fun debugText(): String = when {
                    firstAtMs == null -> "none"
                    secondAtMs == null -> "${firstAtMs}ms"
                    else -> "${firstAtMs}/${secondAtMs}ms"
                }
            }

            val spotEdges = EdgeTracker(baseline?.focusTouchSpot)
            val trackingEdges = EdgeTracker(baseline?.focusTracking)
            val cancelEdges = EdgeTracker(baseline?.cancelEnable)
            var last = baseline
            var reads = 0
            var misses = 0

            // Let setAfPoint() unwind its control-write finally block first.
            delay(15)
            val deadlineMs = requestedAtMs + 900L
            while (isActive && System.currentTimeMillis() < deadlineMs) {
                if (generation != afGeneration || ptpCamera !== camera) return@launch
                val sampleAtMs = System.currentTimeMillis()
                val sample = camera.tryReadRemoteTouchRuntimeStatus(60)
                if (sample == null) {
                    misses += 1
                } else {
                    reads += 1
                    last = sample
                    val elapsed = sampleAtMs - requestedAtMs
                    spotEdges.observe(sample.focusTouchSpot, elapsed)
                    trackingEdges.observe(sample.focusTracking, elapsed)
                    cancelEdges.observe(sample.cancelEnable, elapsed)
                }
                delay(45)
            }

            if (generation != afGeneration || ptpCamera !== camera) return@launch
            fun value(v: Long?): String = v?.toString() ?: "na"
            val ackMs = ackAtMs - requestedAtMs
            val message = "AF CAM RT(D2E4) x=$x y=$y\n" +
                "ack=${ackMs}ms samples=$reads miss=$misses\n" +
                "cam0 E004=${value(baseline?.focusTouchSpot)} E005=${value(baseline?.focusTracking)} D285=${value(baseline?.cancelEnable)}\n" +
                "camEdge E004=${spotEdges.debugText()} E005=${trackingEdges.debugText()} D285=${cancelEdges.debugText()}\n" +
                "camEnd E004=${value(last?.focusTouchSpot)} E005=${value(last?.focusTracking)} D285=${value(last?.cancelEnable)}"
            Log.d(TAG, message.replace('\n', ' '))
            _events.emit(CameraEvent.FocusDebug(message))
        }
    }

''' + marker
text = replace_once(text, marker, helper, "runtime probe helper")

old_branch = '''                        // a7C II fast path: RemoteTouchOperation (D2E4) with\n                        // FunctionOfRemoteTouchOperation (E083) prepared as Spot AF.\n                        // This is one camera touch action; it does not need a second\n                        // synthetic shutter-half-press transaction.\n                        // Diagnostic isolation for ILCE-7CM2: D2E4 now ACKs in ~5 ms and\n                        // FocalFrameInfo reaches the target in ~100 ms, yet the body LCD AF\n                        // frame is still visibly ~0.5 s late.  Temporarily bypass Remote Touch\n                        // on this body so one tap can measure D2DC by itself, without S1.\n                        val d2dcOnlyProbe = camera.deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true\n                        if (!d2dcOnlyProbe && camera.supportsRemoteTouch()) {\n'''
new_branch = '''                        // a7C II fast path: RemoteTouchOperation (D2E4). The previous\n                        // D2DC-only isolation proved tap -> returned focus geometry -> Compose\n                        // is ~0.14 s, so restore the real Remote Touch path and observe Sony's\n                        // own E004/E005/D285 runtime states after the command instead.\n                        if (camera.supportsRemoteTouch()) {\n'''
text = replace_once(text, old_branch, new_branch, "restore D2E4 branch")

text = replace_once(
    text,
    '''                                _events.emit(CameraEvent.FocusDebug(message))\n                                _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))\n                                return@withLock CameraOperationResult.SuccessWithData(message)\n''',
    '''                                _events.emit(CameraEvent.FocusDebug(message))\n                                _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))\n                                startRemoteTouchRuntimeProbe(\n                                    camera = camera,\n                                    generation = generation,\n                                    requestedAtMs = requestedAtMs,\n                                    ackAtMs = touchAckAtMs,\n                                    x = safeX,\n                                    y = safeY\n                                )\n                                return@withLock CameraOperationResult.SuccessWithData(message)\n''',
    "start runtime probe after D2E4",
)

# Remove the temporary D2DC-only early return; fallback remains D2DC+S1 only
# when D2E4 is unavailable or fails.
start = text.find('''                        if (d2dcOnlyProbe) {\n''')
if start < 0:
    raise SystemExit("remove D2DC-only branch: start marker missing")
end_marker = '''                        val s1StartedMs = System.currentTimeMillis()\n'''
end = text.find(end_marker, start)
if end < 0:
    raise SystemExit("remove D2DC-only branch: end marker missing")
text = text[:start] + text[end:]

text = replace_once(
    text,
    '''        afReleaseJob?.cancel()\n        afReleaseJob = null\n        afHalfPressHeld = false\n''',
    '''        afReleaseJob?.cancel()\n        afReleaseJob = null\n        remoteTouchRuntimeProbeJob?.cancel()\n        remoteTouchRuntimeProbeJob = null\n        afHalfPressHeld = false\n''',
    "cancel runtime probe on close",
)

path.write_text(text)
