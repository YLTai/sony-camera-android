from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# 1) Public API: expose a one-shot A7C II AF-center protocol test.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/CameraConnectionManager.kt"
text = read(path)
text = replace_once(
    text,
    "    /** Take a photo and return the captured bitmap. */\n    suspend fun takePhoto(): CameraOperationResult\n\n",
    "    /** Take a photo and return the captured bitmap. */\n    suspend fun takePhoto(): CameraOperationResult\n\n"
    "    /** Diagnostic: write Sony AF Area Position to the protocol center (320, 240). */\n"
    "    suspend fun testAfCenter(): CameraOperationResult\n\n",
    "CameraConnectionManager.testAfCenter",
)
write(path, text)


# 2) Sony PTP: prefer Protocol 3.00 for ILCE-7CM2, safely fall back to 2.00,
#    remember init diagnostics, and add a u32 D2DC SetControlDeviceB test.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
text = read(path)
text = replace_once(
    text,
    "    var serialNumber: String? = null\n        private set\n\n",
    "    var serialNumber: String? = null\n        private set\n\n"
    "    @Volatile\n"
    "    private var sonyExtensionDebug: String = \"ext=not-initialized\"\n\n",
    "Sony extension debug state",
)
old_init = '''        val extInfo = transport.sendCommandWithData(PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO, 0xC8)
        Log.d(TAG, "GetExtDeviceInfo: ${PtpConstants.responseCodeName(extInfo.responseCode)}, ${extInfo.dataSize}B")

        val props = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)
        Log.d(TAG, "GetAllDevicePropData: ${PtpConstants.responseCodeName(props.responseCode)}, ${props.dataSize}B")
'''
new_init = '''        // ILCE-7CM2 / A7C II is a newer Sony body. libgphoto2-style Sony
        // protocol negotiation requests generation 3 (300 / 0x012C) with a
        // second parameter of 1. Older bodies keep the proven 200 / 0x00C8
        // path. If the A7C II rejects protocol 3 or returns no extension data,
        // immediately fall back rather than breaking liveview/capture.
        val preferProtocol3 = deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true
        val extV3 = if (preferProtocol3) {
            transport.sendCommandWithData(
                PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO,
                0x012C,
                1
            )
        } else null

        val useProtocol3 = extV3?.isSuccess == true && extV3.dataSize > 0
        val extInfo = if (useProtocol3) {
            extV3!!
        } else {
            transport.sendCommandWithData(PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO, 0x00C8)
        }
        val selectedProtocol = if (useProtocol3) 300 else 200
        Log.d(TAG, "GetExtDeviceInfo protocol=$selectedProtocol: " +
                "${PtpConstants.responseCodeName(extInfo.responseCode)}, ${extInfo.dataSize}B")

        val props = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)
        Log.d(TAG, "GetAllDevicePropData: ${PtpConstants.responseCodeName(props.responseCode)}, ${props.dataSize}B")

        sonyExtensionDebug = buildString {
            append("ext=").append(selectedProtocol)
            if (preferProtocol3) {
                append(" v3=")
                append(PtpConstants.responseCodeName(extV3?.responseCode ?: 0))
                append("/").append(extV3?.dataSize ?: 0).append("B")
            }
            append(" extInfo=")
            append(PtpConstants.responseCodeName(extInfo.responseCode))
            append("/").append(extInfo.dataSize).append("B")
            append(" init9209=")
            append(PtpConstants.responseCodeName(props.responseCode))
            append("/").append(props.dataSize).append("B")
        }
'''
text = replace_once(text, old_init, new_init, "Protocol 3 init")

# Insert u32 writer/test immediately before existing u16 SetControlDeviceB helper.
marker = '''    /**
     * Send a Sony SetControlDeviceB (0x9207) command with data-out phase.
     * Property code goes as command param, value as uint16 data payload.
     */
    private fun setControlDeviceB(propCode: Int, value: Int): PtpResponse {
'''
insert = '''    /**
     * Write Sony AF Area Position (0xD2DC) as a uint32 through
     * SetControlDeviceB (0x9207). Sony protocol uses a 640x480 logical grid;
     * the packed value is (x << 16) | y.
     */
    private fun setAfAreaPosition(x: Int, y: Int): PtpResponse {
        val safeX = x.coerceIn(0, 639)
        val safeY = y.coerceIn(0, 479)
        val packed = (safeX shl 16) or safeY
        val data = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(packed)
            .array()
        val result = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B,
            data,
            PtpConstants.PROP_SONY_AF_AREA_POSITION
        )
        Log.d(TAG, "Set AF Area Position: x=$safeX y=$safeY packed=0x${packed.toUInt().toString(16)} " +
                "-> ${PtpConstants.responseCodeName(result.responseCode)}")
        return result
    }

    /**
     * Diagnostic for A7C II: move the Sony logical AF position to the center,
     * then briefly half-press AF. The camera must be in an AF-capable focus
     * mode and normally a Spot/Flexible-Spot style focus area for D2DC to have
     * a visible effect.
     */
    fun testAfCenter(): String {
        val setResult = setAfAreaPosition(320, 240)
        Thread.sleep(120)
        val pressResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 2)
        Thread.sleep(450)
        val releaseResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 1)
        return buildString {
            append("AF CENTER TEST x=320 y=240")
            append(" | D2DC/9207=")
            append(PtpConstants.responseCodeName(setResult.responseCode))
            append(" | halfPress=")
            append(PtpConstants.responseCodeName(pressResult.responseCode))
            append(" | release=")
            append(PtpConstants.responseCodeName(releaseResult.responseCode))
        }
    }

'''
text = replace_once(text, marker, insert + marker, "D2DC writer")

# Prefix every live AF probe with the negotiated extension diagnostics.
text = replace_once(
    text,
    '            append("model=").append(deviceName ?: "?")\n',
    '            append("model=").append(deviceName ?: "?")\n'
    '            append(" | ").append(sonyExtensionDebug)\n',
    "Focus probe extension debug",
)
write(path, text)


# 3) USB engine: expose test and push its result into the same debug panel.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt"
text = read(path)
marker = '''    override suspend fun takePhoto(): CameraOperationResult = try {
'''
insert = '''    override suspend fun testAfCenter(): CameraOperationResult = withContext(Dispatchers.IO) {
        val camera = ptpCamera
            ?: return@withContext CameraOperationResult.Failure("Camera not connected")
        try {
            val message = camera.testAfCenter()
            _events.emit(CameraEvent.FocusDebug(message))
            CameraOperationResult.SuccessWithData(message)
        } catch (e: Exception) {
            Log.e(TAG, "AF center test failed", e)
            val message = "AF CENTER TEST exception: ${e.message ?: e.javaClass.simpleName}"
            _events.emit(CameraEvent.FocusDebug(message))
            CameraOperationResult.Failure(message)
        }
    }

'''
text = replace_once(text, marker, insert + marker, "UsbCameraConnectionManager.testAfCenter")
write(path, text)


# 4) Service binder delegation.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/service/CameraConnectionService.kt"
text = read(path)
text = replace_once(
    text,
    "        suspend fun takePhoto() = engine.takePhoto()\n",
    "        suspend fun takePhoto() = engine.takePhoto()\n"
    "        suspend fun testAfCenter() = engine.testAfCenter()\n",
    "CameraBinder.testAfCenter",
)
write(path, text)


# 5) App-side client delegation.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/service/CameraConnectionClient.kt"
text = read(path)
text = replace_once(
    text,
    "    override suspend fun takePhoto(): CameraOperationResult =\n"
    "        binderFlow.value?.takePhoto()\n"
    "            ?: CameraOperationResult.Failure(\"Camera not connected\")\n\n",
    "    override suspend fun takePhoto(): CameraOperationResult =\n"
    "        binderFlow.value?.takePhoto()\n"
    "            ?: CameraOperationResult.Failure(\"Camera not connected\")\n\n"
    "    override suspend fun testAfCenter(): CameraOperationResult =\n"
    "        binderFlow.value?.testAfCenter()\n"
    "            ?: CameraOperationResult.Failure(\"Camera not connected\")\n\n",
    "CameraConnectionClient.testAfCenter",
)
write(path, text)


# 6) Demo: add an explicit AF CENTER TEST button and surface failures.
path = "demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt"
text = read(path)
text = replace_once(
    text,
    "import io.github.gallo.sonycamera.CameraEvent\n",
    "import io.github.gallo.sonycamera.CameraEvent\nimport io.github.gallo.sonycamera.CameraOperationResult\n",
    "CameraScreen import result",
)
text = replace_once(
    text,
    "                    onCapture = { scope.launch { camera.takePhoto() } },\n"
    "                    onDisconnect = { camera.disconnect() },\n",
    "                    onCapture = { scope.launch { camera.takePhoto() } },\n"
    "                    onAfCenterTest = {\n"
    "                        scope.launch {\n"
    "                            when (val result = camera.testAfCenter()) {\n"
    "                                is CameraOperationResult.Failure -> lastError = result.message\n"
    "                                else -> Unit\n"
    "                            }\n"
    "                        }\n"
    "                    },\n"
    "                    onDisconnect = { camera.disconnect() },\n",
    "CameraScreen controls call",
)
text = replace_once(
    text,
    "    onCapture: () -> Unit,\n    onDisconnect: () -> Unit,\n",
    "    onCapture: () -> Unit,\n    onAfCenterTest: () -> Unit,\n    onDisconnect: () -> Unit,\n",
    "Controls signature",
)
text = replace_once(
    text,
    "            is CameraConnectionState.Ready -> {\n                ShutterButton(onClick = onCapture)\n",
    "            is CameraConnectionState.Ready -> {\n"
    "                Button(\n"
    "                    onClick = onAfCenterTest,\n"
    "                    colors = ButtonDefaults.buttonColors(containerColor = Color.White.copy(alpha = 0.14f)),\n"
    "                    shape = RoundedCornerShape(10.dp),\n"
    "                    modifier = Modifier.height(42.dp).width(180.dp)\n"
    "                ) {\n"
    "                    Text(\"AF CENTER TEST\", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)\n"
    "                }\n"
    "                ShutterButton(onClick = onCapture)\n",
    "AF center test button",
)
write(path, text)


# Remove one-shot patch machinery from the final source commit.
Path(".github/workflows/apply-a7c2-v3-af-center-once.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)

print("A7C II protocol 3 + AF center test patch applied")
