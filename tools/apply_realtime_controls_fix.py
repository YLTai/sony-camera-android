from pathlib import Path

path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")
text = path.read_text()

old = '''    fun readExposureState(forceDescriptorProbe: Boolean = false): CameraExposureState {
        ensureExposureDescriptors(forceDescriptorProbe)
        val all = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)
        val allData = if (all.isSuccess) all.data else ByteArray(0)
'''
new = '''    fun readExposureState(forceDescriptorProbe: Boolean = false): CameraExposureState {
        ensureExposureDescriptors(forceDescriptorProbe)
        // Telemetry is low priority. Bound a busy-camera read so it cannot sit
        // on the shared PTP transport for the global 5-second timeout while a
        // user is waiting to move AF or turn an exposure dial.
        val all = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            700
        )
        val allData = if (all.isSuccess) all.data else ByteArray(0)
'''
assert old in text, "readExposureState header changed"
text = text.replace(old, new, 1)

old_direct = '''            val direct = transport.sendCommandWithData(
                PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
                descriptor.propertyCode
            )
'''
new_direct = '''            val direct = transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
                500,
                descriptor.propertyCode
            )
'''
assert old_direct in text, "exposure direct fallback changed"
text = text.replace(old_direct, new_direct, 1)

path.write_text(text)
Path(__file__).unlink()
