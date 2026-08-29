package io.github.gallo.sonycamera

import android.graphics.Bitmap
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

sealed class CameraConnectionState {
    data object Disconnected : CameraConnectionState()
    data object Scanning : CameraConnectionState()
    data object Connecting : CameraConnectionState()
    data object Initializing : CameraConnectionState()
    data object Ready : CameraConnectionState()
    data class Error(val message: String) : CameraConnectionState()
}

data class CameraFocusFrame(
    val type: Int,
    val state: Int,
    val priority: Int,
    val xNumerator: Long,
    val yNumerator: Long,
    val xDenominator: Long,
    val yDenominator: Long,
    val width: Long,
    val height: Long
) {
    val centerXNormalized: Float get() = normalized(xNumerator, xDenominator)
    val centerYNormalized: Float get() = normalized(yNumerator, yDenominator)
    val widthNormalized: Float get() = normalized(width, xDenominator)
    val heightNormalized: Float get() = normalized(height, yDenominator)

    private fun normalized(value: Long, denominator: Long): Float {
        if (denominator <= 0L) return 0f
        return (value.toDouble() / denominator.toDouble()).toFloat().coerceIn(0f, 1f)
    }
}

data class CameraFocusFrameInfo(
    val version: Int,
    val frames: List<CameraFocusFrame>
)

enum class CameraExposureSetting {
    APERTURE,
    SHUTTER_SPEED,
    ISO
}

data class CameraExposureOption(
    val rawValue: Long,
    val label: String
)

data class CameraExposureProperty(
    val current: CameraExposureOption?,
    val options: List<CameraExposureOption>,
    val writable: Boolean,
    /** Lower adjustable limit reported/derived from the active lens descriptor. */
    val minimum: CameraExposureOption? = null,
    /** Upper adjustable limit reported/derived from the active lens descriptor. */
    val maximum: CameraExposureOption? = null
)

data class CameraExposureState(
    val aperture: CameraExposureProperty,
    val shutterSpeed: CameraExposureProperty,
    val iso: CameraExposureProperty
) {
    fun property(setting: CameraExposureSetting): CameraExposureProperty = when (setting) {
        CameraExposureSetting.APERTURE -> aperture
        CameraExposureSetting.SHUTTER_SPEED -> shutterSpeed
        CameraExposureSetting.ISO -> iso
    }
}

/** Generic camera controls surfaced by Sony protocol-3 property snapshots. */
enum class CameraSetting {
    FOCUS_MODE,
    FOCUS_AREA,
    WHITE_BALANCE,
    METERING_MODE,
    DRIVE_MODE,
    EXPOSURE_COMPENSATION
}

data class CameraSettingOption(
    val rawValue: Long,
    val label: String
)

data class CameraSettingProperty(
    val current: CameraSettingOption?,
    val options: List<CameraSettingOption>,
    /**
     * True means the UI should offer choices. Some Sony bodies mark a control
     * read-only in the descriptor but still accept SetControlDeviceA, so known
     * camera-control properties are allowed to be write-attempted.
     */
    val writable: Boolean
)

data class CameraSettingsState(
    val focusMode: CameraSettingProperty,
    val focusArea: CameraSettingProperty,
    val whiteBalance: CameraSettingProperty,
    val meteringMode: CameraSettingProperty,
    val driveMode: CameraSettingProperty,
    val exposureCompensation: CameraSettingProperty
) {
    fun property(setting: CameraSetting): CameraSettingProperty = when (setting) {
        CameraSetting.FOCUS_MODE -> focusMode
        CameraSetting.FOCUS_AREA -> focusArea
        CameraSetting.WHITE_BALANCE -> whiteBalance
        CameraSetting.METERING_MODE -> meteringMode
        CameraSetting.DRIVE_MODE -> driveMode
        CameraSetting.EXPOSURE_COMPENSATION -> exposureCompensation
    }
}

sealed class CameraEvent {
    data class PhotoCaptured(val bitmap: Bitmap) : CameraEvent()
    data class Error(val message: String) : CameraEvent()
    data object ConnectionLost : CameraEvent()

    data class FocusAreaUpdated(val rawValue: Int) : CameraEvent()
    data class FocusDebug(val message: String) : CameraEvent()
    data class AfTargetUpdated(val x: Int, val y: Int) : CameraEvent()
    data class FocusFramesUpdated(val info: CameraFocusFrameInfo) : CameraEvent()
    data class ExposureUpdated(val state: CameraExposureState) : CameraEvent()
    data class CameraSettingsUpdated(val state: CameraSettingsState) : CameraEvent()

    data object ShutterFired : CameraEvent()
}

sealed class CameraOperationResult {
    data object Success : CameraOperationResult()
    data class SuccessWithData<T>(val data: T) : CameraOperationResult()
    data class Failure(val message: String) : CameraOperationResult()
}

interface CameraConnectionManager {
    val connectionState: StateFlow<CameraConnectionState>
    val cameraName: StateFlow<String?>
    val events: SharedFlow<CameraEvent>
    val liveviewFrames: SharedFlow<Bitmap>

    suspend fun startLiveview(): CameraOperationResult
    suspend fun stopLiveview(): CameraOperationResult
    suspend fun takePhoto(): CameraOperationResult
    suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult
    suspend fun testAfCenter(): CameraOperationResult

    suspend fun adjustExposure(
        setting: CameraExposureSetting,
        direction: Int
    ): CameraOperationResult

    /** Set an exact camera-reported exposure value selected by the UI. */
    suspend fun setExposure(
        setting: CameraExposureSetting,
        rawValue: Long
    ): CameraOperationResult

    /** Set one exact option from a generic Sony/PTP camera setting. */
    suspend fun setCameraSetting(
        setting: CameraSetting,
        rawValue: Long
    ): CameraOperationResult

    fun disconnect()
    fun isReady(): Boolean
}
