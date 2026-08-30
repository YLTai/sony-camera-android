from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
constants_path = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/PtpConstants.kt"
camera_path = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"

constants = constants_path.read_text()
if "PROP_SONY_TOUCH_OPERATION" not in constants:
    needle = "    const val PROP_SONY_AF_AREA_POSITION = 0xD2DC\n"
    replacement = (
        needle
        + "    // Sony touch-focus prerequisites used by Remote Touch.\n"
        + "    const val PROP_SONY_TOUCH_OPERATION = 0xD047\n"
        + "    const val PROP_SONY_FUNCTION_OF_TOUCH_OPERATION = 0xD283\n"
    )
    if needle not in constants:
        raise SystemExit("PtpConstants AF-area marker not found")
    constants = constants.replace(needle, replacement, 1)
    constants_path.write_text(constants)

camera = camera_path.read_text()
if "findSonyScalarEnumPropertyAnyType" not in camera:
    marker = "    private fun setSonyScalarProperty(\n"
    helper = '''    /** Find a scalar Sony property without assuming its wire integer width. */
    private fun findSonyScalarEnumPropertyAnyType(
        data: ByteArray,
        propertyCode: Int
    ): SonyScalarEnumProperty? {
        // Sony SDK enums are small integer values, but different bodies expose
        // individual properties as UINT8/16/32 (and occasionally signed variants).
        // Let the camera descriptor decide the payload width used for the write.
        val types = intArrayOf(0x0002, 0x0004, 0x0006, 0x0001, 0x0003, 0x0005)
        for (type in types) {
            val descriptor = findSonyScalarEnumProperty(data, propertyCode, type)
            if (descriptor != null) return descriptor
        }
        return null
    }

'''
    if marker not in camera:
        raise SystemExit("setSonyScalarProperty marker not found")
    camera = camera.replace(marker, helper + marker, 1)

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
            // Never invent a value the connected camera explicitly omits.
            if (descriptor.enumValues.isNotEmpty() && target !in descriptor.enumValues) return null
            val result = setSonyScalarProperty(descriptor, target)
            if (result.isSuccess) refreshProperties()
            return result
        }

        // Sony's own camera UI requires Touch Operation=On and
        // Function of Touch Operation=Touch Focus before a touch can mean AF.
        // The Remote SDK adds E083=Spot AF on top of those camera-side settings.
        // D284 is a read-only enable STATUS, so evaluate it only after all three
        // prerequisites have been applied and the 0x9209 snapshot refreshed.
        val touchBefore = property(PtpConstants.PROP_SONY_TOUCH_OPERATION)?.currentValue
        val touchFunctionBefore = property(PtpConstants.PROP_SONY_FUNCTION_OF_TOUCH_OPERATION)?.currentValue
        val remoteFunctionBefore = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION)?.currentValue
        val remoteEnableBefore = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS)?.currentValue

        var touch = property(PtpConstants.PROP_SONY_TOUCH_OPERATION)
        val touchWrite = writeEnumTarget(touch, 2L) // CrTouchOperation::On
        touch = property(PtpConstants.PROP_SONY_TOUCH_OPERATION)

        var touchFunction = property(PtpConstants.PROP_SONY_FUNCTION_OF_TOUCH_OPERATION)
        val touchFunctionWrite = writeEnumTarget(touchFunction, 3L) // CrFunctionOfTouchOperation::Focus
        touchFunction = property(PtpConstants.PROP_SONY_FUNCTION_OF_TOUCH_OPERATION)

        var remoteFunction = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION)
        val remoteFunctionWrite = writeEnumTarget(remoteFunction, 2L) // CrFunctionOfRemoteTouchOperation::Spot_AF
        remoteFunction = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION)

        // Always make one final authoritative read after the full sequence. This
        // catches D284 transitions that occur only after the camera processes the
        // preceding property change rather than in the immediate write response.
        refreshProperties()
        touch = property(PtpConstants.PROP_SONY_TOUCH_OPERATION)
        touchFunction = property(PtpConstants.PROP_SONY_FUNCTION_OF_TOUCH_OPERATION)
        remoteFunction = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION)
        val remoteEnable = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS)

        val touchAfter = touch?.currentValue
        val touchFunctionAfter = touchFunction?.currentValue
        val remoteFunctionAfter = remoteFunction?.currentValue
        val remoteEnableAfter = remoteEnable?.currentValue
        remoteTouchSupported = touchAfter == 2L &&
            touchFunctionAfter == 3L &&
            remoteFunctionAfter == 2L &&
            remoteEnableAfter == 1L

        fun transition(before: Long?, after: Long?): String = when {
            before == null && after == null -> "na"
            before == after -> (after ?: -1L).toString()
            else -> "${before ?: -1}>${after ?: -1}"
        }
        fun writeResult(result: PtpResponse?): String =
            result?.let { ":${PtpConstants.responseCodeName(it.responseCode)}" } ?: ""

        val touchState = "TO=${transition(touchBefore, touchAfter)}${writeResult(touchWrite)}"
        val touchFunctionState = "TF=${transition(touchFunctionBefore, touchFunctionAfter)}${writeResult(touchFunctionWrite)}"
        val remoteFunctionState = "RF=${transition(remoteFunctionBefore, remoteFunctionAfter)}${writeResult(remoteFunctionWrite)}"
        val remoteEnableState = "RT=${transition(remoteEnableBefore, remoteEnableAfter)}"

        if (remoteTouchSupported) {
            monitorAfPrepared = true
            monitorAfDebugState = "AF RT SpotAF ready $touchState $touchFunctionState $remoteFunctionState $remoteEnableState"
            return monitorAfDebugState
        }

        // Only if the complete Sony touch-focus chain still leaves D284 disabled
        // do we prepare the older AF-area fallback.
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
            append(" ").append(touchState)
            append(" ").append(touchFunctionState)
            append(" ").append(remoteFunctionState)
            append(" ").append(remoteEnableState)
            append(" area=").append(focusArea?.currentValue ?: -1)
            if (spotWrite != null) {
                append(" sset=").append(PtpConstants.responseCodeName(spotWrite.responseCode))
            }
        }
        return monitorAfDebugState
    }'''

pattern = re.compile(
    r'    @Synchronized\n    fun prepareMonitorTapAf\(\): String \{.*?\n    \}\n\n    @Synchronized\n    fun invalidateMonitorTapAf\(\)',
    re.S,
)
replacement = new_prepare + "\n\n    @Synchronized\n    fun invalidateMonitorTapAf()"
camera, count = pattern.subn(replacement, camera, count=1)
if count != 1:
    raise SystemExit(f"prepareMonitorTapAf replacement count={count}")

camera_path.write_text(camera)
Path(__file__).unlink()
