from pathlib import Path

path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt')
text = path.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly 1 match, found {count}')
    text = text.replace(old, new, 1)

# Cache a successfully discovered Drive Mode descriptor. A7C II protocol-3
# bodies may omit 0x5013 from the aggregate 0x9209 layout we currently parse,
# while still exposing it through the targeted Sony/standard PTP descriptor
# and value operations.
replace_once(
'''    private data class GenericSettingDescriptor(
        val setting: CameraSetting,
        val propertyCode: Int,
        val dataType: Int,
        val currentValue: Long?,
        val enumValues: List<Long>,
        val writable: Boolean
    )

    data class CameraSettingAdjustmentResult(''',
'''    private data class GenericSettingDescriptor(
        val setting: CameraSetting,
        val propertyCode: Int,
        val dataType: Int,
        val currentValue: Long?,
        val enumValues: List<Long>,
        val writable: Boolean
    )

    private var cachedDriveModeDescriptor: GenericSettingDescriptor? = null
    private var driveModeProbeAttempted = false

    data class CameraSettingAdjustmentResult(''',
'add drive descriptor cache'
)

# Resolve Drive Mode from 9209 first, but fall back to targeted descriptor/value
# operations. Only Drive Mode gets the extra USB transactions, so normal settings
# telemetry remains one aggregate read every polling interval.
replace_once(
'''        fun prop(setting: CameraSetting): CameraSettingProperty {
            val descriptor = findGenericSettingDescriptor(data, setting)
                ?: return CameraSettingProperty(null, emptyList(), false)
            var values = descriptor.enumValues.distinct()''',
'''        fun prop(setting: CameraSetting): CameraSettingProperty {
            var descriptor = findGenericSettingDescriptor(data, setting)
            if (setting == CameraSetting.DRIVE_MODE) {
                if (descriptor != null) {
                    cachedDriveModeDescriptor = descriptor
                } else {
                    if (cachedDriveModeDescriptor == null && !driveModeProbeAttempted) {
                        driveModeProbeAttempted = true
                        cachedDriveModeDescriptor = probeDriveModeDescriptor()
                    }
                    descriptor = cachedDriveModeDescriptor?.let { cached ->
                        cached.copy(currentValue = readDriveModeCurrent() ?: cached.currentValue)
                    }
                }
            }
            descriptor ?: return CameraSettingProperty(null, emptyList(), false)
            var values = descriptor.enumValues.distinct()''',
'resolve drive mode outside 9209'
)

# The write path must reuse the discovered descriptor when 9209 cannot parse it.
replace_once(
'''        val descriptor = if (snapshot.isSuccess) {
            findGenericSettingDescriptor(snapshot.data, setting)
        } else null
        val before = readCameraSettingsState()
        if (descriptor == null) {''',
'''        var descriptor = if (snapshot.isSuccess) {
            findGenericSettingDescriptor(snapshot.data, setting)
        } else null
        if (setting == CameraSetting.DRIVE_MODE) {
            if (descriptor != null) {
                cachedDriveModeDescriptor = descriptor
            } else {
                if (cachedDriveModeDescriptor == null) {
                    driveModeProbeAttempted = true
                    cachedDriveModeDescriptor = probeDriveModeDescriptor()
                }
                descriptor = cachedDriveModeDescriptor
            }
        }
        val before = readCameraSettingsState()
        if (descriptor == null) {''',
'use cached drive descriptor for writes'
)

# Add targeted a7C II Drive Mode discovery. SonyAlphaUSB and libgphoto2 identify
# Drive Mode as 0x5013. Sony's sub-setting write path is 0x9205, already used by
# setGenericSettingRaw; this fills the missing read/discovery side.
marker = '''    private fun findGenericSettingDescriptor(
        data: ByteArray,
        setting: CameraSetting
    ): GenericSettingDescriptor? {'''
helper = '''    private fun probeDriveModeDescriptor(): GenericSettingDescriptor? {
        val propertyCode = PtpConstants.PROP_PTP_STILL_CAPTURE_MODE

        val sonyDesc = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC,
            1_200,
            propertyCode
        )
        if (sonyDesc.isSuccess && sonyDesc.data.isNotEmpty()) {
            findGenericSettingDescriptor(sonyDesc.data, CameraSetting.DRIVE_MODE)?.let { descriptor ->
                Log.d(TAG, "Drive Mode discovered via Sony 0x9206: choices=${descriptor.enumValues.size} writable=${descriptor.writable}")
                return descriptor.copy(
                    currentValue = descriptor.currentValue ?: readDriveModeCurrent()
                )
            }
        }

        val ptpDesc = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_GET_DEVICE_PROP_DESC,
            1_200,
            propertyCode
        )
        if (ptpDesc.isSuccess && ptpDesc.data.isNotEmpty()) {
            findGenericSettingDescriptor(ptpDesc.data, CameraSetting.DRIVE_MODE)?.let { descriptor ->
                Log.d(TAG, "Drive Mode discovered via standard PTP 0x1014: choices=${descriptor.enumValues.size} writable=${descriptor.writable}")
                return descriptor.copy(
                    currentValue = descriptor.currentValue ?: readDriveModeCurrent()
                )
            }
        }

        val current = readDriveModeCurrent()
        if (current != null) {
            Log.d(TAG, "Drive Mode value is readable without descriptor; enabling known-safe Sony choices")
            return GenericSettingDescriptor(
                setting = CameraSetting.DRIVE_MODE,
                propertyCode = propertyCode,
                dataType = 0x0004,
                currentValue = current,
                enumValues = fallbackCameraSettingValues(CameraSetting.DRIVE_MODE),
                writable = true
            )
        }

        Log.w(TAG, "Drive Mode 0x5013 unavailable via 9209, 9206/9204 and 1014/1015")
        return null
    }

    private fun readDriveModeCurrent(): Long? {
        val propertyCode = PtpConstants.PROP_PTP_STILL_CAPTURE_MODE
        val sony = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
            900,
            propertyCode
        )
        if (sony.isSuccess && sony.data.size >= 2) {
            return readUnsignedScalar(sony.data, 0, 2)
        }

        val standard = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_GET_DEVICE_PROP_VALUE,
            900,
            propertyCode
        )
        if (standard.isSuccess && standard.data.size >= 2) {
            return readUnsignedScalar(standard.data, 0, 2)
        }
        return null
    }

'''
if text.count(marker) != 1:
    raise RuntimeError('drive helper insertion marker not unique')
text = text.replace(marker, helper + marker, 1)

# Correct the basic Sony drive-mode values. The previous three timer values were
# actually continuous self-timer variants, which made fallback writes incorrect.
replace_once(
'''        CameraSetting.DRIVE_MODE -> listOf(1L, 2L, 0x8015L, 0x8012L, 0x8010L, 0x800DL, 0x800BL, 0x800CL)''',
'''        CameraSetting.DRIVE_MODE -> listOf(1L, 2L, 0x8015L, 0x8012L, 0x8010L, 0x8005L, 0x8003L, 0x8004L)''',
'correct drive fallback values'
)

replace_once(
'''            0x8010L -> "CONT. HI+"
            0x800DL -> "TIMER 2s"
            0x800BL -> "TIMER 5s"
            0x800CL -> "TIMER 10s"
            else -> "0x%04X".format(raw and 0xFFFF)''',
'''            0x8010L -> "CONT. HI+"
            0x8005L -> "TIMER 2s"
            0x8003L -> "TIMER 5s"
            0x8004L -> "TIMER 10s"
            0x8018L -> "WB BRKT LO"
            0x8028L -> "WB BRKT HI"
            0x8019L -> "DRO BRKT LO"
            0x8029L -> "DRO BRKT HI"
            0x8008L -> "TIMER 10s ×3"
            0x8009L -> "TIMER 10s ×5"
            0x800EL -> "TIMER 2s ×3"
            0x800FL -> "TIMER 2s ×5"
            0x800CL -> "TIMER 5s ×3"
            0x800DL -> "TIMER 5s ×5"
            0x8337L -> "BRKT C 0.3 ×3"
            0x8537L -> "BRKT C 0.3 ×5"
            0x8937L -> "BRKT C 0.3 ×9"
            0x8357L -> "BRKT C 0.5 ×3"
            0x8557L -> "BRKT C 0.5 ×5"
            0x8957L -> "BRKT C 0.5 ×9"
            0x8377L -> "BRKT C 0.7 ×3"
            0x8577L -> "BRKT C 0.7 ×5"
            0x8977L -> "BRKT C 0.7 ×9"
            0x8311L -> "BRKT C 1.0 ×3"
            0x8511L -> "BRKT C 1.0 ×5"
            0x8911L -> "BRKT C 1.0 ×9"
            0x8321L -> "BRKT C 2.0 ×3"
            0x8521L -> "BRKT C 2.0 ×5"
            0x8331L -> "BRKT C 3.0 ×3"
            0x8531L -> "BRKT C 3.0 ×5"
            0x8336L -> "BRKT S 0.3 ×3"
            0x8536L -> "BRKT S 0.3 ×5"
            0x8936L -> "BRKT S 0.3 ×9"
            0x8356L -> "BRKT S 0.5 ×3"
            0x8556L -> "BRKT S 0.5 ×5"
            0x8956L -> "BRKT S 0.5 ×9"
            0x8376L -> "BRKT S 0.7 ×3"
            0x8576L -> "BRKT S 0.7 ×5"
            0x8976L -> "BRKT S 0.7 ×9"
            0x8310L -> "BRKT S 1.0 ×3"
            0x8510L -> "BRKT S 1.0 ×5"
            0x8910L -> "BRKT S 1.0 ×9"
            0x8320L -> "BRKT S 2.0 ×3"
            0x8520L -> "BRKT S 2.0 ×5"
            0x8330L -> "BRKT S 3.0 ×3"
            0x8530L -> "BRKT S 3.0 ×5"
            else -> "0x%04X".format(raw and 0xFFFF)''',
'expand drive labels'
)

path.write_text(text)
Path('tools/apply_a7c2_drive_mode_usb.py').unlink()
print('Applied a7C II Drive Mode USB discovery/read patch')
