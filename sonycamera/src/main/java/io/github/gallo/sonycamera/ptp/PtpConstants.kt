package io.github.gallo.sonycamera.ptp

/** PTP and Sony vendor constants used by the wired camera-control transport. */
object PtpConstants {
    const val SONY_VENDOR_ID = 0x054C
    const val USB_CLASS_PTP = 6
    const val USB_SUBCLASS_PTP = 1
    const val USB_PROTOCOL_PTP = 1

    const val CONTAINER_TYPE_COMMAND = 1
    const val CONTAINER_TYPE_DATA = 2
    const val CONTAINER_TYPE_RESPONSE = 3
    const val CONTAINER_TYPE_EVENT = 4

    const val OP_GET_DEVICE_INFO = 0x1001
    const val OP_OPEN_SESSION = 0x1002
    const val OP_CLOSE_SESSION = 0x1003
    const val OP_GET_STORAGE_IDS = 0x1004
    const val OP_GET_OBJECT_HANDLES = 0x1007
    const val OP_GET_OBJECT_INFO = 0x1008
    const val OP_GET_OBJECT = 0x1009
    const val OP_GET_DEVICE_PROP_DESC = 0x1014
    const val OP_GET_DEVICE_PROP_VALUE = 0x1015
    const val OP_SET_DEVICE_PROP_VALUE = 0x1016

    const val OP_SONY_SDIO_CONNECT = 0x9201
    const val OP_SONY_SDIO_GET_EXT_DEVICE_INFO = 0x9202
    const val OP_SONY_GET_DEVICE_PROP_DESC = 0x9203
    const val OP_SONY_GET_DEVICE_PROP_VALUE = 0x9204
    const val OP_SONY_SET_CONTROL_DEVICE_A = 0x9205
    const val OP_SONY_GET_CONTROL_DEVICE_DESC = 0x9206
    const val OP_SONY_SET_CONTROL_DEVICE_B = 0x9207
    const val OP_SONY_GET_ALL_DEVICE_PROP_DATA = 0x9209
    const val OP_SONY_SDIO_OPEN_SESSION = 0x9210
    const val OP_SONY_GET_PARTIAL_LARGE_OBJECT = 0x9211

    const val RESP_OK = 0x2001
    const val RESP_GENERAL_ERROR = 0x2002
    const val RESP_SESSION_NOT_OPEN = 0x2003
    const val RESP_INVALID_TRANSACTION_ID = 0x2004
    const val RESP_OPERATION_NOT_SUPPORTED = 0x2005
    const val RESP_PARAMETER_NOT_SUPPORTED = 0x2006
    const val RESP_INCOMPLETE_TRANSFER = 0x2007
    const val RESP_ACCESS_DENIED = 0x200F
    const val RESP_DEVICE_BUSY = 0x2019
    const val RESP_SONY_NOT_READY = 0xA102

    const val EVENT_OBJECT_ADDED = 0x4002
    const val EVENT_DEVICE_PROP_CHANGED = 0x4006
    const val EVENT_STORE_FULL = 0x400A

    // Standard PTP camera properties exposed by modern Sony protocol-3 bodies.
    const val PROP_PTP_WHITE_BALANCE = 0x5005
    const val PROP_PTP_F_NUMBER = 0x5007
    const val PROP_PTP_FOCUS_MODE = 0x500A
    const val PROP_PTP_EXPOSURE_METERING_MODE = 0x500B
    const val PROP_PTP_EXPOSURE_BIAS_COMPENSATION = 0x5010
    const val PROP_PTP_STILL_CAPTURE_MODE = 0x5013

    // Sony vendor properties.
    const val PROP_SONY_PRIORITY_MODE = 0xD25A
    const val PROP_SONY_SHUTTER_HALF_PRESS = 0xD2C1
    const val PROP_SONY_SHUTTER_FULL_PRESS = 0xD2C2
    const val PROP_SONY_AF_STATUS = 0xD2C7
    const val PROP_SONY_PHOTO_CAPTURE = 0xD2C8
    const val PROP_SONY_EXPOSURE_MODE = 0xD210
    const val PROP_SONY_ISO = 0xD21E
    const val PROP_SONY_ISO_ALT = 0xD226
    const val PROP_SONY_SHUTTER_SPEED = 0xD20D
    const val PROP_SONY_SHUTTER_SPEED_ALT = 0xD229
    const val PROP_SONY_F_NUMBER = 0xD218
    const val PROP_SONY_FOCUS_MODE = 0xD208
    const val PROP_SONY_FOCUS_AREA = 0xD22C
    const val PROP_SONY_AF_AREA_POSITION = 0xD2DC
    // Sony Camera Remote SDK "Remote Touch Operation": execute a touch at
    // x/y in the same 640x480 logical coordinate system used by AF area position.
    // Unlike D2DC this is a momentary control action and can perform Spot AF in
    // one control transaction, matching the SDK's touch-monitoring path.
    const val PROP_SONY_REMOTE_TOUCH_OPERATION = 0xD2E4
    const val PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS = 0xD284
    const val PROP_SONY_REMOTE_TOUCH_FUNCTION = 0xE083
    const val PROP_SONY_LIVEVIEW_STATE = 0xD221
    const val PROP_SONY_STILL_IMAGE_STORE_DEST = 0xD222
    const val PROP_SONY_PHOTO_TRANSFER_QUEUE = 0xD215

    const val LIVEVIEW_OBJECT_HANDLE = 0xFFFFC002.toInt()
    const val PHOTO_OBJECT_HANDLE = 0xFFFFC001.toInt()

    const val IMAGE_TYPE_PHOTO: Short = 0xC001.toShort()
    const val IMAGE_TYPE_LIVEVIEW: Short = 0xC002.toShort()

    const val HEADER_SIZE = 12
    const val USB_TIMEOUT_MS = 5000
    const val USB_TRANSFER_BUFFER_SIZE = 512 * 1024
    const val LIVEVIEW_BUFFER_SIZE = 256 * 1024

    fun isSuccess(responseCode: Int): Boolean = responseCode == RESP_OK

    fun responseCodeName(code: Int): String = when (code) {
        RESP_OK -> "OK"
        RESP_GENERAL_ERROR -> "General Error"
        RESP_SESSION_NOT_OPEN -> "Session Not Open"
        RESP_ACCESS_DENIED -> "Access Denied"
        RESP_DEVICE_BUSY -> "Device Busy"
        RESP_OPERATION_NOT_SUPPORTED -> "Operation Not Supported"
        RESP_SONY_NOT_READY -> "Sony: Not Ready"
        else -> "Unknown (0x${code.toString(16)})"
    }
}
