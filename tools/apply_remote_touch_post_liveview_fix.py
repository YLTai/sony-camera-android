from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SONY = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
MANAGER = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt"
UI = ROOT / "demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt"

sony = SONY.read_text()
manager = MANAGER.read_text()
ui = UI.read_text()

# 1) Do not freeze Remote Touch readiness before Live View has actually started.
old_init = '''        if (preferProtocol3) {
            Thread.sleep(250)
            // Prepare the exact Sony monitor-touch state before Live View starts so
            // the first user tap does not pay a property-read / mode-switch penalty.
            // Failure is non-fatal: prepareMonitorTapAf() records an AF-Area fallback.
            val afPrep = prepareMonitorTapAf()
            Log.d(TAG, "Monitor AF preparation: $afPrep")
        }
'''
new_init = '''        if (preferProtocol3) {
            Thread.sleep(250)
            // D284 is a live camera status. Do not cache a Disabled value before
            // Live View has produced a frame; the manager performs the one-shot
            // Remote Touch preparation immediately after the first live frame.
            Log.d(TAG, "Monitor AF preparation deferred until first Live View frame")
        }
'''
assert old_init in sony, "init monitor-AF block not found"
sony = sony.replace(old_init, new_init, 1)

# 2) Keep the raw Sony descriptor access byte in diagnostics.
old_data = '''        val enumValues: List<Long>,
        val writable: Boolean,
        val enabledState: Int
'''
new_data = '''        val enumValues: List<Long>,
        val writable: Boolean,
        val getSetState: Int,
        val enabledState: Int
'''
assert old_data in sony, "SonyScalarEnumProperty fields not found"
sony = sony.replace(old_data, new_data, 1)

old_ctor = '''                currentValue = current,
                enumValues = values,
                writable = writable,
                enabledState = enabled
'''
new_ctor = '''                currentValue = current,
                enumValues = values,
                writable = writable,
                getSetState = getSet,
                enabledState = enabled
'''
assert old_ctor in sony, "SonyScalarEnumProperty constructor not found"
sony = sony.replace(old_ctor, new_ctor, 1)

# 3) Replace the a7C II Remote Touch preparation with the SDK-aligned state flow:
#    E083 Spot AF is the setting; D284 is the authoritative enable status.
#    D047/D283 are displayed for diagnosis but are not undocumented hard gates.
start = sony.index('    @Synchronized\n    fun prepareMonitorTapAf(): String {')
end = sony.index('    @Synchronized\n    fun invalidateMonitorTapAf()', start)
new_prepare = r'''    @Synchronized
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
        fun property(code: Int): SonyScalarEnumProperty? =
            findSonyScalarEnumPropertyAnyType(data, code)

        fun refreshProperties(): Boolean {
            val verify = transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
                500
            )
            if (!verify.isSuccess || verify.data.isEmpty()) return false
            data = verify.data
            return true
        }

        fun writeEnumTarget(
            descriptor: SonyScalarEnumProperty?,
            target: Long
        ): PtpResponse? {
            if (descriptor == null || descriptor.currentValue == target) return null
            // Follow the SDK sample rule: check the property's enable flag and
            // choose only a value advertised in the current candidate list.
            if (!descriptor.writable) return null
            if (descriptor.enumValues.isNotEmpty() && target !in descriptor.enumValues) return null
            return setSonyScalarProperty(descriptor, target)
        }

        // Sony Camera Remote SDK documents Remote Touch as its own operation:
        // E083 selects the Remote Touch function and D284 is the authoritative
        // enable status for D2E4. D047/D283 are the camera body's local touch
        // settings; keep them visible for diagnosis, but do not invent them as
        // mandatory Remote Touch gates when the SDK does not state that linkage.
        val touchBeforeProp = property(PtpConstants.PROP_SONY_TOUCH_OPERATION)
        val touchFunctionBeforeProp = property(PtpConstants.PROP_SONY_FUNCTION_OF_TOUCH_OPERATION)
        val remoteFunctionBeforeProp = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION)
        val remoteEnableBeforeProp = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS)

        val remoteFunctionWrite = writeEnumTarget(remoteFunctionBeforeProp, 2L) // Spot_AF

        // SetDeviceProperty is asynchronous in Sony's SDK model. A successful
        // transport ACK is not proof that the camera-side state has changed, so
        // give E083/D284 a short bounded settle window and re-read 0x9209.
        var settleReads = 0
        var remoteFunction = remoteFunctionBeforeProp
        var remoteEnable = remoteEnableBeforeProp
        for (attempt in 1..6) {
            if (remoteFunctionWrite != null || attempt > 1) {
                Thread.sleep(if (attempt == 1) 80L else 120L)
            }
            if (refreshProperties()) settleReads += 1
            remoteFunction = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION)
            remoteEnable = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS)
            if (remoteFunction?.currentValue == 2L && remoteEnable?.currentValue == 1L) break
        }

        val touchAfterProp = property(PtpConstants.PROP_SONY_TOUCH_OPERATION)
        val touchFunctionAfterProp = property(PtpConstants.PROP_SONY_FUNCTION_OF_TOUCH_OPERATION)
        val actionProp = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_OPERATION)

        val touchBefore = touchBeforeProp?.currentValue
        val touchFunctionBefore = touchFunctionBeforeProp?.currentValue
        val remoteFunctionBefore = remoteFunctionBeforeProp?.currentValue
        val remoteEnableBefore = remoteEnableBeforeProp?.currentValue
        val touchAfter = touchAfterProp?.currentValue
        val touchFunctionAfter = touchFunctionAfterProp?.currentValue
        val remoteFunctionAfter = remoteFunction?.currentValue
        val remoteEnableAfter = remoteEnable?.currentValue

        remoteTouchSupported = remoteFunctionAfter == 2L && remoteEnableAfter == 1L

        fun transition(before: Long?, after: Long?): String = when {
            before == null && after == null -> "na"
            before == after -> (after ?: -1L).toString()
            else -> "${before ?: -1}>${after ?: -1}"
        }
        fun writeResult(result: PtpResponse?): String =
            result?.let { ":${PtpConstants.responseCodeName(it.responseCode)}" } ?: ""
        fun descriptorMeta(descriptor: SonyScalarEnumProperty?): String {
            if (descriptor == null) return "[missing]"
            val candidates = if (descriptor.enumValues.isEmpty()) {
                "-"
            } else {
                descriptor.enumValues.joinToString(",")
            }
            return "[t=0x${descriptor.dataType.toString(16)} gs=0x${descriptor.getSetState.toString(16)} " +
                "en=${descriptor.enabledState} w=${if (descriptor.writable) 1 else 0} vals=$candidates]"
        }

        val touchState = "TO=${transition(touchBefore, touchAfter)}${descriptorMeta(touchAfterProp)}"
        val touchFunctionState = "TF=${transition(touchFunctionBefore, touchFunctionAfter)}${descriptorMeta(touchFunctionAfterProp)}"
        val remoteFunctionState = "RF=${transition(remoteFunctionBefore, remoteFunctionAfter)}${writeResult(remoteFunctionWrite)}${descriptorMeta(remoteFunction)}"
        val remoteEnableState = "RT=${transition(remoteEnableBefore, remoteEnableAfter)}${descriptorMeta(remoteEnable)}"
        val actionState = "ACT=${actionProp?.currentValue ?: -1}${descriptorMeta(actionProp)}"
        val stateLine = "$touchState $touchFunctionState $remoteFunctionState $remoteEnableState $actionState reads=$settleReads"

        if (remoteTouchSupported) {
            monitorAfPrepared = true
            monitorAfDebugState = "AF RT SpotAF ready\n$stateLine"
            return monitorAfDebugState
        }

        // Only after Live View is active and the bounded D284 refresh still says
        // Disabled do we prepare the compatibility AF-area path.
        var focusArea = findGenericSettingDescriptor(data, CameraSetting.FOCUS_AREA)
        var spotWrite: PtpResponse? = null
        if (focusArea != null && focusArea.currentValue != 5L) {
            spotWrite = setGenericSettingRaw(focusArea, 5L)
            if (spotWrite.isSuccess && refreshProperties()) {
                focusArea = findGenericSettingDescriptor(data, CameraSetting.FOCUS_AREA)
            }
        }

        val spotReady = focusArea?.currentValue == 5L || spotWrite?.isSuccess == true
        monitorAfPrepared = spotReady
        monitorAfDebugState = buildString {
            append("AF AREA SpotS ").append(if (spotReady) "ready" else "NOT READY")
            append("\n").append(stateLine)
            append("\narea=").append(focusArea?.currentValue ?: -1)
            if (spotWrite != null) {
                append(" sset=").append(PtpConstants.responseCodeName(spotWrite.responseCode))
            }
        }
        return monitorAfDebugState
    }

'''
sony = sony[:start] + new_prepare + sony[end:]

# 4) Keep the preparation state attached to the later AF FRAME latency message,
#    so it cannot be hidden by the second FocusDebug event.
old_pending = '''        val path: String,
        val s1Ms: Long?,
        val baseline: CameraFocusFrameInfo?,
        var firstGeometryChangeAtMs: Long? = null
'''
new_pending = '''        val path: String,
        val s1Ms: Long?,
        val baseline: CameraFocusFrameInfo?,
        val prepDebug: String,
        var firstGeometryChangeAtMs: Long? = null
'''
assert old_pending in manager, "PendingAfFrameLatency fields not found"
manager = manager.replace(old_pending, new_pending, 1)

old_frame_ok = '''                    "AF FRAME ${claimed.path} x=${claimed.x} y=${claimed.y}\\n" +
                        "ack=${ackMs}ms$s1Text cmd=${commandMs}ms change=${changedMs?.let { "${it}ms" } ?: "n/a"}\\n" +
'''
new_frame_ok = '''                    "AF FRAME ${claimed.path} x=${claimed.x} y=${claimed.y}\\n" +
                        "${claimed.prepDebug}\\n" +
                        "ack=${ackMs}ms$s1Text cmd=${commandMs}ms change=${changedMs?.let { "${it}ms" } ?: "n/a"}\\n" +
'''
assert old_frame_ok in manager, "matched AF FRAME message not found"
manager = manager.replace(old_frame_ok, new_frame_ok, 1)

old_frame_timeout = '''                    "AF FRAME ${claimed.path} x=${claimed.x} y=${claimed.y}\\n" +
                        "ack=${ackMs}ms$s1Text cmd=${commandMs}ms change=${changedMs?.let { "${it}ms" } ?: "n/a"}\\n" +
                        "target=>2000ms$nearestText"
'''
new_frame_timeout = '''                    "AF FRAME ${claimed.path} x=${claimed.x} y=${claimed.y}\\n" +
                        "${claimed.prepDebug}\\n" +
                        "ack=${ackMs}ms$s1Text cmd=${commandMs}ms change=${changedMs?.let { "${it}ms" } ?: "n/a"}\\n" +
                        "target=>2000ms$nearestText"
'''
assert old_frame_timeout in manager, "timeout AF FRAME message not found"
manager = manager.replace(old_frame_timeout, new_frame_timeout, 1)

old_live_flag = '''            var hasEverGottenFrame = false
            var pipeRecoveryAttempts = 0
'''
new_live_flag = '''            var hasEverGottenFrame = false
            var monitorAfPostLiveViewPrepared = false
            var pipeRecoveryAttempts = 0
'''
assert old_live_flag in manager, "liveview flags not found"
manager = manager.replace(old_live_flag, new_live_flag, 1)

# Prepare Remote Touch exactly once after the first actual Live View frame.
anchor = '''                        lastFrameTime = System.currentTimeMillis()

                        // The Sony USB transport is strictly serial. Do not perform property
'''
post_live = '''                        lastFrameTime = System.currentTimeMillis()

                        if (!monitorAfPostLiveViewPrepared) {
                            monitorAfPostLiveViewPrepared = true
                            val camera = ptpCamera
                            if (camera != null) {
                                controlWriteMutex.withLock {
                                    val prepEpoch = beginControlWrite()
                                    try {
                                        camera.invalidateMonitorTapAf()
                                        val postLiveViewPrep = camera.prepareMonitorTapAf()
                                        Log.d(TAG, "Remote Touch post-LiveView prep: ${postLiveViewPrep.replace('\\n', ' ')}")
                                        _events.emit(
                                            CameraEvent.FocusDebug(
                                                "RTSTATE POST-LIVEVIEW\\n$postLiveViewPrep"
                                            )
                                        )
                                    } finally {
                                        endControlWrite(prepEpoch)
                                    }
                                }
                            }
                        }

                        // The Sony USB transport is strictly serial. Do not perform property
'''
assert anchor in manager, "post-liveview insertion anchor not found"
manager = manager.replace(anchor, post_live, 1)

# Both latency records retain the preparation snapshot used for that tap.
needle = '''                                        s1Ms = null,
                                        baseline = baseline
'''
replacement = '''                                        s1Ms = null,
                                        baseline = baseline,
                                        prepDebug = prepDebug
'''
assert needle in manager, "RT latency constructor not found"
manager = manager.replace(needle, replacement, 1)

needle = '''                                s1Ms = s1Ms,
                                baseline = baseline
'''
replacement = '''                                s1Ms = s1Ms,
                                baseline = baseline,
                                prepDebug = prepDebug
'''
assert needle in manager, "fallback latency constructor not found"
manager = manager.replace(needle, replacement, 1)

# Keep the diagnostic visible long enough to read/screenshot after a tap.
old_ui = '''            LaunchedEffect(focusDebug) {
                if (focusDebug != null) {
                    delay(5000)
                    focusDebug = null
                }
            }
'''
new_ui = '''            LaunchedEffect(focusDebug) {
                if (focusDebug != null) {
                    delay(15000)
                    focusDebug = null
                }
            }
'''
assert old_ui in ui, "focus debug timeout block not found"
ui = ui.replace(old_ui, new_ui, 1)

SONY.write_text(sony)
MANAGER.write_text(manager)
UI.write_text(ui)

# One-shot patch script: remove itself from the functional commit.
Path(__file__).unlink()
