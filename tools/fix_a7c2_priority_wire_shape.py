from pathlib import Path

path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")
text = path.read_text()
original = text


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    text = text.replace(old, new, 1)

replace_once(
'''            setControlDeviceA(PtpConstants.PROP_SONY_PRIORITY_MODE, 0)
''',
'''            setPriorityModeLegacy(0)
''',
"priority release",
)

replace_once(
'''        val priority = setControlDeviceA(PtpConstants.PROP_SONY_PRIORITY_MODE, 1)
''',
'''        val priority = setPriorityModeLegacy(1)
''',
"priority acquire",
)

marker = '''    /**
     * Send a Sony SetControlDeviceA (0x9205) command with uint8 data payload.
     * Used for configuration values (PriorityMode, etc.).
     */
    private fun setControlDeviceA(propCode: Int, value: Byte): PtpResponse {
'''
insert = '''    /**
     * PriorityMode is part of the proven PC-Remote session handshake. Keep its
     * 0x9205 wire shape exactly as it was before the Remote Touch PTP3 option
     * experiment: one property-code parameter and a UInt8 payload, with NO
     * Device Property Option parameter. The option=1 form remains available for
     * Remote Touch/property operations after the session is established.
     */
    private fun setPriorityModeLegacy(value: Byte): PtpResponse {
        val data = byteArrayOf(value)
        val result = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_A,
            data,
            PtpConstants.PROP_SONY_PRIORITY_MODE
        )
        if (!result.isSuccess) {
            Log.w(TAG, "PriorityMode legacy write($value): " +
                    PtpConstants.responseCodeName(result.responseCode))
        }
        return result
    }

    /**
     * Send a Sony SetControlDeviceA (0x9205) command with uint8 data payload.
     * Used for configuration values after the session handshake.
     */
    private fun setControlDeviceA(propCode: Int, value: Byte): PtpResponse {
'''
replace_once(marker, insert, "priority helper")

if text == original:
    raise SystemExit("no changes")
path.write_text(text)
Path(__file__).unlink()
