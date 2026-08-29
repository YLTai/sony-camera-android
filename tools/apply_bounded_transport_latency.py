from pathlib import Path

sony_path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")
sony = sony_path.read_text()

old_live = '''    fun getLiveViewFrameData(): SonyLiveViewFrame? {
        val response = transport.sendCommandWithData(
            PtpConstants.OP_GET_OBJECT,
            PtpConstants.LIVEVIEW_OBJECT_HANDLE
        )
'''
new_live = '''    fun getLiveViewFrameData(): SonyLiveViewFrame? {
        // Live view is a continuous low-priority producer. Never allow one
        // busy frame request to hold the shared PTP transport for the global
        // 5-second timeout while the user is waiting on AF/exposure control.
        val response = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_GET_OBJECT,
            450,
            PtpConstants.LIVEVIEW_OBJECT_HANDLE
        )
'''
assert old_live in sony, "liveview read block changed"
sony = sony.replace(old_live, new_live, 1)

sony = sony.replace(
'''            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            700
        )
        val allData = if (all.isSuccess) all.data else ByteArray(0)
''',
'''            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            500
        )
        val allData = if (all.isSuccess) all.data else ByteArray(0)
''',
1)
sony = sony.replace(
'''                PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
                500,
                descriptor.propertyCode
''',
'''                PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
                350,
                descriptor.propertyCode
''',
1)

old_settings = '''        val response = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            900
        )
        val data = if (response.isSuccess) response.data else ByteArray(0)
'''
new_settings = '''        val response = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            500
        )
        val data = if (response.isSuccess) response.data else ByteArray(0)
'''
assert old_settings in sony, "settings telemetry block changed"
sony = sony.replace(old_settings, new_settings, 1)

sony_path.write_text(sony)
Path(__file__).unlink()
