from pathlib import Path
import re

# ---- SonyPtpCamera: generic property model + exact exposure writes ----------
path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt')
text = path.read_text()

old_import = '''import io.github.gallo.sonycamera.CameraExposureState
import java.nio.ByteBuffer'''
new_import = '''import io.github.gallo.sonycamera.CameraExposureState
import io.github.gallo.sonycamera.CameraSetting
import io.github.gallo.sonycamera.CameraSettingOption
import io.github.gallo.sonycamera.CameraSettingProperty
import io.github.gallo.sonycamera.CameraSettingsState
import java.nio.ByteBuffer'''
if old_import not in text:
    raise SystemExit('Sony imports marker not found')
text = text.replace(old_import, new_import, 1)

exact_exposure = r'''
    /** Set one exact value selected from the Sony-style UI selector. */
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
marker = '    private fun setExposureRaw(descriptor: ExposureDescriptor, value: Long): PtpResponse {'
if marker not in text:
    raise SystemExit('setExposureRaw marker not found')
text = text.replace(marker, exact_exposure + marker, 1)

settings_section = r'''
    // ── Generic camera settings (Sony/PTP protocol 3) ───────────────────

    private data class GenericSettingDescriptor(
        val setting: CameraSetting,
        val propertyCode: Int,
        val dataType: Int,
        val currentValue: Long?,
        val enumValues: List<Long>,
        val writable: Boolean
    )

    data class CameraSettingAdjustmentResult(
        val state: CameraSettingsState,
        val success: Boolean,
        val message: String? = null
    )

    /**
     * Read AF mode/area, white balance, metering, drive and EV from one 9209
     * snapshot. Unknown values are preserved as hex labels instead of guessed.
     */
    fun readCameraSettingsState(): CameraSettingsState {
        val response = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            2_000
        )
        val data = if (response.isSuccess) response.data else ByteArray(0)

        fun prop(setting: CameraSetting): CameraSettingProperty {
            val descriptor = findGenericSettingDescriptor(data, setting)
                ?: return CameraSettingProperty(null, emptyList(), false)
            var values = descriptor.enumValues.distinct()
            if (values.size < 2) {
                values = fallbackCameraSettingValues(setting)
            }
            val current = descriptor.currentValue
            val mutable = values.toMutableList()
            if (current != null && current !in mutable) mutable += current
            val options = mutable.distinct().map { raw ->
                CameraSettingOption(raw, formatCameraSettingValue(setting, raw))
            }
            val currentOption = current?.let { raw ->
                options.firstOrNull { it.rawValue == raw }
                    ?: CameraSettingOption(raw, formatCameraSettingValue(setting, raw))
            }
            return CameraSettingProperty(
                current = currentOption,
                options = options,
                writable = descriptor.writable && options.size > 1
            )
        }

        return CameraSettingsState(
            focusMode = prop(CameraSetting.FOCUS_MODE),
            focusArea = prop(CameraSetting.FOCUS_AREA),
            whiteBalance = prop(CameraSetting.WHITE_BALANCE),
            meteringMode = prop(CameraSetting.METERING_MODE),
            driveMode = prop(CameraSetting.DRIVE_MODE),
            exposureCompensation = prop(CameraSetting.EXPOSURE_COMPENSATION)
        )
    }

    fun setCameraSettingValue(
        setting: CameraSetting,
        rawValue: Long
    ): CameraSettingAdjustmentResult {
        val snapshot = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            2_000
        )
        val descriptor = if (snapshot.isSuccess) {
            findGenericSettingDescriptor(snapshot.data, setting)
        } else null
        val before = readCameraSettingsState()
        if (descriptor == null) {
            return CameraSettingAdjustmentResult(before, false, "${cameraSettingName(setting)} is unavailable")
        }

        val response = setGenericSettingRaw(descriptor, rawValue)
        Thread.sleep(180)
        var after = readCameraSettingsState()
        if (after.property(setting).current?.rawValue != rawValue) {
            Thread.sleep(260)
            after = readCameraSettingsState()
        }
        val applied = after.property(setting).current?.rawValue == rawValue
        return if (applied) {
            CameraSettingAdjustmentResult(after, true)
        } else {
            CameraSettingAdjustmentResult(
                after,
                false,
                if (response.isSuccess) "Camera did not apply ${formatCameraSettingValue(setting, rawValue)}"
                else "Camera rejected ${cameraSettingName(setting)} (${PtpConstants.responseCodeName(response.responseCode)})"
            )
        }
    }

    private fun findGenericSettingDescriptor(
        data: ByteArray,
        setting: CameraSetting
    ): GenericSettingDescriptor? {
        val candidates: IntArray
        val dataType: Int
        when (setting) {
            CameraSetting.FOCUS_MODE -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_FOCUS_MODE, PtpConstants.PROP_SONY_FOCUS_MODE)
                dataType = 0x0004
            }
            CameraSetting.FOCUS_AREA -> {
                candidates = intArrayOf(PtpConstants.PROP_SONY_FOCUS_AREA)
                dataType = 0x0004
            }
            CameraSetting.WHITE_BALANCE -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_WHITE_BALANCE)
                dataType = 0x0004
            }
            CameraSetting.METERING_MODE -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_EXPOSURE_METERING_MODE)
                dataType = 0x0004
            }
            CameraSetting.DRIVE_MODE -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_STILL_CAPTURE_MODE)
                dataType = 0x0004
            }
            CameraSetting.EXPOSURE_COMPENSATION -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_EXPOSURE_BIAS_COMPENSATION)
                dataType = 0x0003 // INT16; keep the raw 16-bit value for writes.
            }
        }

        val size = scalarSize(dataType)
        if (data.size < 5 + size * 2) return null
        for (propertyCode in candidates) {
            for (base in 0 until data.size - 4) {
                if (u16(data, base) != propertyCode || u16(data, base + 2) != dataType) continue
                val getSet = data.getOrNull(base + 4)?.toInt()?.and(0xFF) ?: 0

                data class Parsed(val current: Long?, val values: List<Long>, val score: Int)
                val parsed = mutableListOf<Parsed>()
                for (sonyExtraFlag in listOf(true, false)) {
                    val currentOffset = base + 5 + size + if (sonyExtraFlag) 1 else 0
                    if (currentOffset + size > data.size) continue
                    val current = readUnsignedScalar(data, currentOffset, size)
                    val formOffset = currentOffset + size
                    val formFlag = data.getOrNull(formOffset)?.toInt()?.and(0xFF) ?: continue
                    if (formFlag !in 0..2) continue
                    var values = emptyList<Long>()
                    var valid = true
                    when (formFlag) {
                        1 -> {
                            // Range properties are uncommon for these controls; the current
                            // value is still useful even when we do not synthesize each step.
                            if (formOffset + 1 + size * 3 > data.size) valid = false
                        }
                        2 -> {
                            val countOffset = formOffset + 1
                            if (countOffset + 2 > data.size) valid = false
                            else {
                                val count = u16(data, countOffset)
                                val valuesOffset = countOffset + 2
                                if (count !in 1..512 || valuesOffset + count * size > data.size) {
                                    valid = false
                                } else {
                                    values = List(count) { index ->
                                        readUnsignedScalar(data, valuesOffset + index * size, size) ?: 0L
                                    }
                                }
                            }
                        }
                    }
                    if (!valid) continue
                    var score = 2
                    if (sonyExtraFlag) score += 2
                    if (values.size > 1) score += 4
                    if (current != null && (values.isEmpty() || current in values)) score += 3
                    parsed += Parsed(current, values, score)
                }
                val best = parsed.maxByOrNull { it.score } ?: continue
                // Sony has historically marked several remotely-settable controls as
                // readonly. A real enum plus one of our known camera-control ids is
                // enough to offer the selector; failed writes are verified and reported.
                val writable = getSet != 0 || best.values.size > 1
                return GenericSettingDescriptor(
                    setting = setting,
                    propertyCode = propertyCode,
                    dataType = dataType,
                    currentValue = best.current,
                    enumValues = best.values,
                    writable = writable
                )
            }
        }
        return null
    }

    private fun setGenericSettingRaw(
        descriptor: GenericSettingDescriptor,
        value: Long
    ): PtpResponse {
        val size = scalarSize(descriptor.dataType)
        val payload = ByteBuffer.allocate(size).order(ByteOrder.LITTLE_ENDIAN).apply {
            when (size) {
                1 -> put((value and 0xFF).toByte())
                2 -> putShort((value and 0xFFFF).toShort())
                4 -> putInt((value and 0xFFFFFFFFL).toInt())
            }
        }.array()
        val sony = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_A,
            payload,
            descriptor.propertyCode
        )
        if (sony.isSuccess) return sony
        if (descriptor.propertyCode in 0x5000..0x5FFF) {
            return transport.sendCommandWithDataOut(
                PtpConstants.OP_SET_DEVICE_PROP_VALUE,
                payload,
                descriptor.propertyCode
            )
        }
        return sony
    }

    private fun fallbackCameraSettingValues(setting: CameraSetting): List<Long> = when (setting) {
        CameraSetting.FOCUS_MODE -> listOf(1L, 2L, 0x8004L, 0x8005L, 0x8006L)
        CameraSetting.WHITE_BALANCE -> listOf(
            2L, 4L, 0x8011L, 0x8010L, 6L,
            0x8001L, 0x8002L, 0x8003L, 0x8004L,
            7L, 0x8030L, 0x8012L, 0x8020L, 0x8021L, 0x8022L
        )
        CameraSetting.METERING_MODE -> listOf(4L, 0x8001L, 0x8002L, 0x8004L, 0x8005L, 0x8003L, 0x8006L)
        CameraSetting.DRIVE_MODE -> listOf(1L, 2L, 0x8015L, 0x8012L, 0x8010L, 0x800DL, 0x800BL, 0x800CL)
        // Focus-area raw codes vary by Sony generation, so never guess them.
        CameraSetting.FOCUS_AREA -> emptyList()
        // EV is normally returned as an enum by Sony; avoid offering unsupported steps.
        CameraSetting.EXPOSURE_COMPENSATION -> emptyList()
    }

    private fun formatCameraSettingValue(setting: CameraSetting, raw: Long): String = when (setting) {
        CameraSetting.FOCUS_MODE -> when (raw and 0xFFFF) {
            1L -> "MF"
            2L -> "AF-S"
            0x8004L -> "AF-C"
            0x8005L -> "AF-A"
            0x8006L -> "DMF"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
        CameraSetting.FOCUS_AREA -> "0x%04X".format(raw and 0xFFFF)
        CameraSetting.WHITE_BALANCE -> when (raw and 0xFFFF) {
            2L -> "AWB"
            4L -> "DAYLIGHT"
            0x8011L -> "SHADE"
            0x8010L -> "CLOUDY"
            6L -> "TUNGSTEN"
            0x8001L -> "FL WARM"
            0x8002L -> "FL COOL"
            0x8003L -> "FL DAY W"
            0x8004L -> "FL DAY"
            7L -> "FLASH"
            0x8030L -> "UNDERWATER"
            0x8012L -> "KELVIN"
            0x8020L -> "CUSTOM 1"
            0x8021L -> "CUSTOM 2"
            0x8022L -> "CUSTOM 3"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
        CameraSetting.METERING_MODE -> when (raw and 0xFFFF) {
            4L -> "CENTER SPOT"
            0x8001L -> "MULTI"
            0x8002L -> "CENTER"
            0x8004L -> "SPOT STD"
            0x8005L -> "SPOT LARGE"
            0x8003L -> "ENTIRE AVG"
            0x8006L -> "HIGHLIGHT"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
        CameraSetting.DRIVE_MODE -> when (raw and 0xFFFF) {
            1L -> "SINGLE"
            2L -> "CONT. HIGH"
            0x8015L -> "CONT. MID"
            0x8012L -> "CONT. LOW"
            0x8010L -> "CONT. HI+"
            0x800DL -> "TIMER 2s"
            0x800BL -> "TIMER 5s"
            0x800CL -> "TIMER 10s"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
        CameraSetting.EXPOSURE_COMPENSATION -> {
            val signed = (raw and 0xFFFF).toInt().toShort().toInt()
            val ev = signed / 1000.0
            when {
                signed == 0 -> "±0.0"
                signed > 0 -> "+%.1f".format(ev)
                else -> "%.1f".format(ev)
            }
        }
    }

    private fun cameraSettingName(setting: CameraSetting): String = when (setting) {
        CameraSetting.FOCUS_MODE -> "focus mode"
        CameraSetting.FOCUS_AREA -> "focus area"
        CameraSetting.WHITE_BALANCE -> "white balance"
        CameraSetting.METERING_MODE -> "metering mode"
        CameraSetting.DRIVE_MODE -> "drive mode"
        CameraSetting.EXPOSURE_COMPENSATION -> "exposure compensation"
    }

'''
photo_marker = '    // ── Sony Photo Transfer Queue ──'
if photo_marker not in text:
    raise SystemExit('photo marker not found')
text = text.replace(photo_marker, settings_section + photo_marker, 1)
path.write_text(text)

# ---- UsbCameraConnectionManager ---------------------------------------------
path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt')
text = path.read_text()
text = text.replace(
    'import io.github.gallo.sonycamera.CameraOperationResult\n',
    'import io.github.gallo.sonycamera.CameraOperationResult\nimport io.github.gallo.sonycamera.CameraSetting\n',
    1
)
text = text.replace(
    'private const val EXPOSURE_POLL_INTERVAL_MS = 1_200L',
    'private const val EXPOSURE_POLL_INTERVAL_MS = 1_200L\n        private const val SETTINGS_POLL_INTERVAL_MS = 2_200L',
    1
)
text = text.replace(
    'var lastExposurePollTime = 0L',
    'var lastExposurePollTime = 0L\n            var lastSettingsPollTime = 0L',
    1
)
exposure_block = '''                        if (exposurePollNow - lastExposurePollTime >= EXPOSURE_POLL_INTERVAL_MS) {
                            lastExposurePollTime = exposurePollNow
                            ptpCamera?.readExposureState()?.let { exposure ->
                                _events.emit(CameraEvent.ExposureUpdated(exposure))
                            }
                        }
'''
if exposure_block not in text:
    raise SystemExit('manager exposure poll block not found')
settings_poll = exposure_block + '''
                        if (exposurePollNow - lastSettingsPollTime >= SETTINGS_POLL_INTERVAL_MS) {
                            lastSettingsPollTime = exposurePollNow
                            ptpCamera?.readCameraSettingsState()?.let { settings ->
                                _events.emit(CameraEvent.CameraSettingsUpdated(settings))
                            }
                        }
'''
text = text.replace(exposure_block, settings_poll, 1)
method_marker = '''    override suspend fun takePhoto(): CameraOperationResult = try {'''
new_methods = '''    override suspend fun setExposure(
        setting: CameraExposureSetting,
        rawValue: Long
    ): CameraOperationResult = withContext(Dispatchers.IO) {
        val camera = ptpCamera
            ?: return@withContext CameraOperationResult.Failure("Camera not connected")
        val result = camera.setExposureValue(setting, rawValue)
        _events.emit(CameraEvent.ExposureUpdated(result.state))
        if (result.success) CameraOperationResult.Success
        else CameraOperationResult.Failure(result.message ?: "Exposure change failed")
    }

    override suspend fun setCameraSetting(
        setting: CameraSetting,
        rawValue: Long
    ): CameraOperationResult = withContext(Dispatchers.IO) {
        val camera = ptpCamera
            ?: return@withContext CameraOperationResult.Failure("Camera not connected")
        val result = camera.setCameraSettingValue(setting, rawValue)
        _events.emit(CameraEvent.CameraSettingsUpdated(result.state))
        if (result.success) CameraOperationResult.Success
        else CameraOperationResult.Failure(result.message ?: "Camera setting change failed")
    }

'''
if method_marker not in text:
    raise SystemExit('manager takePhoto marker not found')
text = text.replace(method_marker, new_methods + method_marker, 1)
path.write_text(text)

# ---- CameraConnectionService binder ----------------------------------------
path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/service/CameraConnectionService.kt')
text = path.read_text()
text = text.replace(
    'import io.github.gallo.sonycamera.CameraExposureSetting\n',
    'import io.github.gallo.sonycamera.CameraExposureSetting\nimport io.github.gallo.sonycamera.CameraSetting\n',
    1
)
old = '''        suspend fun adjustExposure(setting: CameraExposureSetting, direction: Int) =
            engine.adjustExposure(setting, direction)
        fun isReady() = engine.isReady()'''
new = '''        suspend fun adjustExposure(setting: CameraExposureSetting, direction: Int) =
            engine.adjustExposure(setting, direction)
        suspend fun setExposure(setting: CameraExposureSetting, rawValue: Long) =
            engine.setExposure(setting, rawValue)
        suspend fun setCameraSetting(setting: CameraSetting, rawValue: Long) =
            engine.setCameraSetting(setting, rawValue)
        fun isReady() = engine.isReady()'''
if old not in text:
    raise SystemExit('service binder marker not found')
text = text.replace(old, new, 1)
path.write_text(text)

# ---- CameraConnectionClient proxy -----------------------------------------
path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/service/CameraConnectionClient.kt')
text = path.read_text()
text = text.replace(
    'import io.github.gallo.sonycamera.CameraOperationResult\n',
    'import io.github.gallo.sonycamera.CameraOperationResult\nimport io.github.gallo.sonycamera.CameraSetting\n',
    1
)
marker = '''    override fun isReady(): Boolean = binderFlow.value?.isReady() == true'''
methods = '''    override suspend fun setExposure(
        setting: CameraExposureSetting,
        rawValue: Long
    ): CameraOperationResult =
        binderFlow.value?.setExposure(setting, rawValue)
            ?: CameraOperationResult.Failure("Camera not connected")

    override suspend fun setCameraSetting(
        setting: CameraSetting,
        rawValue: Long
    ): CameraOperationResult =
        binderFlow.value?.setCameraSetting(setting, rawValue)
            ?: CameraOperationResult.Failure("Camera not connected")

'''
if marker not in text:
    raise SystemExit('client marker not found')
text = text.replace(marker, methods + marker, 1)
path.write_text(text)

print('monitor v2 backend patched')
