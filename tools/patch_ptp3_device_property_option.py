from pathlib import Path
import re

SOURCE = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")
text = SOURCE.read_text()
original = text


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '    @Volatile\n    private var sonyExtensionDebug: String = "ext=not-initialized"\n',
    '    @Volatile\n    private var sonyExtensionDebug: String = "ext=not-initialized"\n\n'
    '    // Camera Control PTP 3 adds a Device Property Option parameter to\n'
    '    // SDIO_SetExtDevicePropValue (0x9205), SDIO_ControlDevice (0x9207),\n'
    '    // and SDIO_GetAllExtDevicePropInfo (0x9209). option=1 is required to\n'
    '    // expose/use extended properties such as FunctionOfRemoteTouchOperation\n'
    '    // (E083). Keep protocol-2 bodies on their proven legacy command shape.\n'
    '    @Volatile\n'
    '    private var sonyPtp3DevicePropertyOptionEnabled = false\n',
    "PTP3 property-option state",
)

replace_once(
    '    @Volatile\n    private var consecutiveLiveviewErrors = 0\n',
    '    @Volatile\n    private var consecutiveLiveviewErrors = 0\n\n'
    '    private fun sonyPropertyOptionParams(code: Int): IntArray =\n'
    '        if (sonyPtp3DevicePropertyOptionEnabled) intArrayOf(code, 1) else intArrayOf(code)\n\n'
    '    private fun sonyGetAllPropertyParams(): IntArray =\n'
    '        if (sonyPtp3DevicePropertyOptionEnabled) intArrayOf(0, 1) else intArrayOf()\n\n'
    '    private fun getAllSonyProperties(timeoutMs: Int): PtpDataResponse =\n'
    '        transport.sendCommandWithDataShortTimeout(\n'
    '            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,\n'
    '            timeoutMs,\n'
    '            *sonyGetAllPropertyParams()\n'
    '        )\n\n'
    '    private fun sendSonySetDeviceProperty(data: ByteArray, code: Int): PtpResponse =\n'
    '        transport.sendCommandWithDataOut(\n'
    '            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_A,\n'
    '            data,\n'
    '            *sonyPropertyOptionParams(code)\n'
    '        )\n\n'
    '    private fun sendSonyControlDevice(\n'
    '        data: ByteArray,\n'
    '        code: Int,\n'
    '        highPriority: Boolean = false\n'
    '    ): PtpResponse = if (highPriority) {\n'
    '        transport.sendHighPriorityCommandWithDataOut(\n'
    '            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B,\n'
    '            data,\n'
    '            *sonyPropertyOptionParams(code)\n'
    '        )\n'
    '    } else {\n'
    '        transport.sendCommandWithDataOut(\n'
    '            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B,\n'
    '            data,\n'
    '            *sonyPropertyOptionParams(code)\n'
    '        )\n'
    '    }\n',
    "PTP3 helpers",
)

replace_once(
    '        if (!extInfo.isSuccess || extInfo.dataSize == 0) return false\n',
    '        if (!extInfo.isSuccess || extInfo.dataSize == 0) return false\n\n'
    '        // The protocol-3 GetExtDeviceInfo request above already asks for\n'
    '        // Device Property Option=1. Carry that same official command shape\n'
    '        // into every subsequent 0x9205/0x9207/0x9209 transaction.\n'
    '        sonyPtp3DevicePropertyOptionEnabled = useProtocol3\n',
    "enable PTP3 option after negotiation",
)

# Every protocol-3 aggregate property read must be 9209(param1=full-data 0,
# param2=Device Property Option 1). Older bodies still get the no-param form.
short_pattern = re.compile(
    r'transport\.sendCommandWithDataShortTimeout\(\s*'
    r'PtpConstants\.OP_SONY_GET_ALL_DEVICE_PROP_DATA,\s*'
    r'([A-Za-z0-9_]+)\s*\)',
    re.MULTILINE,
)
text, short_count = short_pattern.subn(r'getAllSonyProperties(\1)', text)
if short_count < 5:
    raise SystemExit(f"9209 short-timeout replacement count too small: {short_count}")

regular_pattern = re.compile(
    r'transport\.sendCommandWithData\(\s*'
    r'PtpConstants\.OP_SONY_GET_ALL_DEVICE_PROP_DATA\s*\)',
    re.MULTILINE,
)
text, regular_count = regular_pattern.subn(
    'getAllSonyProperties(PtpConstants.USB_TIMEOUT_MS)', text
)
if regular_count < 1:
    raise SystemExit(f"9209 regular replacement count too small: {regular_count}")

# Route every Sony 0x9205 data-out through the protocol-aware wrapper.
set_a_pattern = re.compile(
    r'transport\.sendCommandWithDataOut\(\s*'
    r'PtpConstants\.OP_SONY_SET_CONTROL_DEVICE_A,\s*'
    r'([A-Za-z_][A-Za-z0-9_.]*),\s*'
    r'([A-Za-z_][A-Za-z0-9_.]*)\s*\)',
    re.MULTILINE,
)
text, set_a_count = set_a_pattern.subn(
    r'sendSonySetDeviceProperty(\1, \2)', text
)
if set_a_count < 4:
    raise SystemExit(f"9205 replacement count too small: {set_a_count}")

# Same for Sony 0x9207; monitor AF keeps the existing high-priority transport lane.
high_b_pattern = re.compile(
    r'transport\.sendHighPriorityCommandWithDataOut\(\s*'
    r'PtpConstants\.OP_SONY_SET_CONTROL_DEVICE_B,\s*'
    r'([A-Za-z_][A-Za-z0-9_.]*),\s*'
    r'([A-Za-z_][A-Za-z0-9_.]*)\s*\)',
    re.MULTILINE,
)
text, high_b_count = high_b_pattern.subn(
    r'sendSonyControlDevice(\1, \2, highPriority = true)', text
)
if high_b_count < 2:
    raise SystemExit(f"high-priority 9207 replacement count too small: {high_b_count}")

regular_b_pattern = re.compile(
    r'transport\.sendCommandWithDataOut\(\s*'
    r'PtpConstants\.OP_SONY_SET_CONTROL_DEVICE_B,\s*'
    r'([A-Za-z_][A-Za-z0-9_.]*),\s*'
    r'([A-Za-z_][A-Za-z0-9_.]*)\s*\)',
    re.MULTILINE,
)
text, regular_b_count = regular_b_pattern.subn(
    r'sendSonyControlDevice(\1, \2)', text
)
if regular_b_count < 1:
    raise SystemExit(f"regular 9207 replacement count too small: {regular_b_count}")

replace_once(
    '            append(" extInfo=")\n',
    '            append(" propOpt=").append(if (sonyPtp3DevicePropertyOptionEnabled) 1 else 0)\n'
    '            append(" extInfo=")\n',
    "extension diagnostic option flag",
)

replace_once(
    '            add("reads=$settleReads")\n',
    '            add("PTP3OPT=${if (sonyPtp3DevicePropertyOptionEnabled) 1 else 0}")\n'
    '            add("reads=$settleReads")\n',
    "monitor AF option diagnostic",
)

# Update stale comments so future work does not regress to the no-param PTP2 shape.
text = text.replace(
    'SetControlDeviceB (0x9207). Sony protocol uses a 640x480 logical grid;',
    'SDIO_ControlDevice (0x9207). PTP3 also carries Device Property Option=1; Sony protocol uses a 640x480 logical grid;'
)

if text == original:
    raise SystemExit("patch made no changes")

SOURCE.write_text(text)
print(
    "patched SonyPtpCamera.kt: "
    f"9209-short={short_count}, 9209-regular={regular_count}, "
    f"9205={set_a_count}, 9207-high={high_b_count}, 9207-regular={regular_b_count}"
)

# One-shot patch: source is committed by the workflow, this staging file is not.
Path(__file__).unlink()
