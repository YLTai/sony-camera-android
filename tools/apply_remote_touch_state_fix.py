from pathlib import Path

path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")
text = path.read_text()
old = '''        var data = snapshot.data
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
        return monitorAfDebugState
'''
new = '''        var data = snapshot.data
        var enable = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS, 0x0002
        )
        var function = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION, 0x0002
        )
        val enableBefore = enable?.currentValue
        val functionBefore = function?.currentValue
        var functionWrite: PtpResponse? = null

        // RemoteTouchOperationEnableStatus (D284) is a STATUS, not a prerequisite
        // for selecting FunctionOfRemoteTouchOperation (E083). The previous code
        // only attempted E083 when D284 was already enabled, which can deadlock the
        // state machine at rtEn=0 -> never select Spot AF -> rtEn remains 0.
        // Prepare Spot AF whenever the camera exposes E083 and advertises value 2,
        // then refresh BOTH E083 and D284 from a new 0x9209 snapshot.
        if (function != null && function.currentValue != 2L &&
            (function.enumValues.isEmpty() || 2L in function.enumValues)
        ) {
            functionWrite = setSonyScalarProperty(function, 2L)
            if (functionWrite.isSuccess) {
                val verifyFunction = transport.sendCommandWithDataShortTimeout(
                    PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
                    500
                )
                if (verifyFunction.isSuccess && verifyFunction.data.isNotEmpty()) {
                    data = verifyFunction.data
                }
            }
        }

        enable = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS, 0x0002
        )
        function = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION, 0x0002
        )
        val enableAfter = enable?.currentValue
        val functionAfter = function?.currentValue
        remoteTouchSupported = enableAfter == 1L && functionAfter == 2L

        // Do not mutate FocusArea when Remote Touch is ready. Spot/AF-area state can
        // itself change whether Remote Touch is permitted. This was another source of
        // self-inflicted fallback: prepare RT, then immediately switch to Spot S.
        if (remoteTouchSupported) {
            monitorAfPrepared = true
            monitorAfDebugState = buildString {
                append("AF RT SpotAF ready")
                append(" rtEn=").append(enableAfter ?: -1)
                append(" func=").append(functionAfter ?: -1)
                if (enableBefore != enableAfter) {
                    append(" rt:").append(enableBefore ?: -1).append("->").append(enableAfter ?: -1)
                }
                if (functionBefore != functionAfter) {
                    append(" fn:").append(functionBefore ?: -1).append("->").append(functionAfter ?: -1)
                }
                if (functionWrite != null) {
                    append(" fset=").append(PtpConstants.responseCodeName(functionWrite.responseCode))
                }
            }
            return monitorAfDebugState
        }

        // Only after Remote Touch is confirmed unavailable do we prepare the proven
        // AF-area fallback. This keeps compatibility without contaminating RT state.
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
            append(" rtEn=").append(enableAfter ?: -1)
            append(" func=").append(functionAfter ?: -1)
            if (enableBefore != enableAfter) {
                append(" rt:").append(enableBefore ?: -1).append("->").append(enableAfter ?: -1)
            }
            if (functionBefore != functionAfter) {
                append(" fn:").append(functionBefore ?: -1).append("->").append(functionAfter ?: -1)
            }
            append(" area=").append(focusArea?.currentValue ?: -1)
            if (functionWrite != null) {
                append(" fset=").append(PtpConstants.responseCodeName(functionWrite.responseCode))
            }
            if (spotWrite != null) {
                append(" sset=").append(PtpConstants.responseCodeName(spotWrite.responseCode))
            }
        }
        return monitorAfDebugState
'''
if old not in text:
    raise SystemExit("target block not found")
path.write_text(text.replace(old, new, 1))
Path(__file__).unlink()
