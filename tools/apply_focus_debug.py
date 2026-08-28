from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    s = text.find(start)
    if s < 0:
        raise RuntimeError(f"{label}: start marker not found")
    e = text.find(end, s)
    if e < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:s] + replacement + text[e:]


# 1) Public event surface: add a diagnostic event that is emitted even if
#    the focus-area value cannot be parsed.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/CameraConnectionManager.kt"
text = read(path)
text = replace_once(
    text,
    "    data class FocusAreaUpdated(val rawValue: Int) : CameraEvent()\n",
    "    data class FocusAreaUpdated(val rawValue: Int) : CameraEvent()\n\n"
    "    /** Diagnostic information from the Sony focus-area probe. */\n"
    "    data class FocusDebug(val message: String) : CameraEvent()\n",
    "CameraEvent.FocusDebug",
)
write(path, text)


# 2) Sony protocol: prefer direct GetDevicePropertyValue (0x9204) for D22C.
#    If the body rejects that call, fall back to GetAllDevicePropData (0x9209)
#    and try both standard PTP and Sony-flagged offsets. Always return debug
#    text so a real camera immediately tells us what it is sending.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
text = read(path)
start = "    fun getFocusAreaCode(): Int? {"
end = "    // ── Sony Photo Transfer Queue ──"
replacement = r'''    data class FocusAreaProbe(
        val focusAreaCode: Int?,
        val debug: String
    )

    /**
     * Probe Sony Focus Area (0xD22C) with diagnostics.
     *
     * First try Sony GetDevicePropertyValue (0x9204), which avoids depending
     * on the generation-specific layout of the 0x9209 aggregate property blob.
     * Older bodies/firmware may reject 0x9204 for this property, so we then
     * scan 0x9209 and test both standard PTP and Sony's extra-flag layout.
     */
    fun probeFocusArea(): FocusAreaProbe {
        val knownValues = setOf(
            0x0001, 0x0002, 0x0003,
            0x0101, 0x0102, 0x0103, 0x0104,
            0x0201, 0x0202, 0x0203, 0x0204, 0x0205, 0x0206, 0x0207
        )

        // Fast/direct path. sendCommandWithData strips the PTP container, so
        // response.data is the property payload itself.
        val direct = transport.sendCommandWithData(
            PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
            PtpConstants.PROP_SONY_FOCUS_AREA
        )
        val directValue = when {
            direct.data.size >= 2 -> ByteBuffer.wrap(direct.data, 0, 2)
                .order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
            direct.data.size == 1 -> direct.data[0].toInt() and 0xFF
            else -> null
        }
        val directSummary = "9204=${PtpConstants.responseCodeName(direct.responseCode)} " +
            "${direct.dataSize}B value=${directValue?.let { "0x%04X".format(it) } ?: "n/a"}"

        if (direct.isSuccess && directValue in knownValues) {
            return FocusAreaProbe(directValue, directSummary).also {
                Log.d(TAG, "AF probe: ${it.debug}")
            }
        }

        // Fallback for Sony generation-2 bodies: inspect the aggregate blob.
        val all = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)
        if (!all.isSuccess || all.data.size < 8) {
            val result = FocusAreaProbe(
                null,
                "$directSummary; 9209=${PtpConstants.responseCodeName(all.responseCode)} ${all.dataSize}B"
            )
            Log.d(TAG, "AF probe: ${result.debug}")
            return result
        }

        val data = all.data
        val hits = mutableListOf<String>()
        var selected: Int? = null

        fun readValue(offset: Int, size: Int): Int? {
            if (offset < 0 || offset + size > data.size) return null
            return when (size) {
                1 -> data[offset].toInt() and 0xFF
                2 -> ByteBuffer.wrap(data, offset, 2)
                    .order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
                4 -> ByteBuffer.wrap(data, offset, 4)
                    .order(ByteOrder.LITTLE_ENDIAN).int
                else -> null
            }
        }

        for (offset in 0 until data.size - 4) {
            val code = (data[offset].toInt() and 0xFF) or
                ((data[offset + 1].toInt() and 0xFF) shl 8)
            if (code != PtpConstants.PROP_SONY_FOCUS_AREA) continue

            val dataType = (data[offset + 2].toInt() and 0xFF) or
                ((data[offset + 3].toInt() and 0xFF) shl 8)
            val valueSize = when (dataType) {
                1, 2 -> 1
                3, 4 -> 2
                5, 6 -> 4
                else -> 0
            }

            val standard = if (valueSize > 0) readValue(offset + 5 + valueSize, valueSize) else null
            val sonyFlagged = if (valueSize > 0) readValue(offset + 6 + valueSize, valueSize) else null

            if (selected == null) {
                selected = when {
                    standard in knownValues -> standard
                    sonyFlagged in knownValues -> sonyFlagged
                    else -> null
                }
            }

            val from = (offset - 2).coerceAtLeast(0)
            val to = (offset + 16).coerceAtMost(data.size)
            val bytes = data.copyOfRange(from, to)
                .joinToString(" ") { "%02X".format(it.toInt() and 0xFF) }
            hits += "D22C@$offset type=0x%04X std=%s sony=%s bytes=%s".format(
                dataType,
                standard?.let { "0x%04X".format(it) } ?: "n/a",
                sonyFlagged?.let { "0x%04X".format(it) } ?: "n/a",
                bytes
            )
            if (hits.size >= 2) break
        }

        val debug = buildString {
            append(directSummary)
            append("; 9209=OK ${all.dataSize}B")
            if (hits.isEmpty()) append("; D22C NOT FOUND")
            else append("; ").append(hits.joinToString(" | "))
        }
        val result = FocusAreaProbe(selected, debug)
        Log.d(TAG, "AF probe: ${result.debug}")
        return result
    }

    /** Backwards-compatible convenience accessor. */
    fun getFocusAreaCode(): Int? = probeFocusArea().focusAreaCode

'''
text = replace_between(text, start, end, replacement, "Sony focus probe")
write(path, text)


# 3) USB liveview loop: emit debug on every probe, not only when parsing succeeds.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt"
text = read(path)
old = '''                        if (focusPollNow - lastFocusAreaPollTime >= FOCUS_AREA_POLL_INTERVAL_MS) {
                            lastFocusAreaPollTime = focusPollNow
                            ptpCamera?.getFocusAreaCode()?.let { _events.emit(CameraEvent.FocusAreaUpdated(it)) }
                        }
'''
new = '''                        if (focusPollNow - lastFocusAreaPollTime >= FOCUS_AREA_POLL_INTERVAL_MS) {
                            lastFocusAreaPollTime = focusPollNow
                            ptpCamera?.probeFocusArea()?.let { probe ->
                                _events.emit(CameraEvent.FocusDebug(probe.debug))
                                probe.focusAreaCode?.let { _events.emit(CameraEvent.FocusAreaUpdated(it)) }
                            }
                        }
'''
text = replace_once(text, old, new, "USB focus polling")
write(path, text)


# 4) Demo UI: always show AF DEBUG when Ready, so even a complete protocol
#    failure is visible on-screen. Include frame count/dimensions and raw probe.
path = "demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt"
text = read(path)
text = replace_once(
    text,
    "            var focusAreaCode by remember { mutableStateOf<Int?>(null) }\n",
    "            var focusAreaCode by remember { mutableStateOf<Int?>(null) }\n"
    "            var focusDebug by remember { mutableStateOf(\"waiting for first AF probe…\") }\n"
    "            var focusEventCount by remember { mutableStateOf(0) }\n"
    "            var liveFrameCount by remember { mutableStateOf(0L) }\n",
    "CameraScreen debug state",
)
text = replace_once(
    text,
    "                camera.liveviewFrames.collect { frame = it }\n",
    "                camera.liveviewFrames.collect { bitmap ->\n"
    "                    frame = bitmap\n"
    "                    liveFrameCount++\n"
    "                }\n",
    "CameraScreen frame counter",
)
text = replace_once(
    text,
    "                        is CameraEvent.FocusAreaUpdated -> focusAreaCode = event.rawValue\n",
    "                        is CameraEvent.FocusAreaUpdated -> {\n"
    "                            focusAreaCode = event.rawValue\n"
    "                            focusEventCount++\n"
    "                        }\n"
    "                        is CameraEvent.FocusDebug -> focusDebug = event.message\n",
    "CameraScreen FocusDebug event",
)
text = replace_once(
    text,
    "                // ── Shutter flash ─────────────────────────────────────────────\n",
    '''                // Always-visible diagnostic panel while connected. This is
                // intentionally shown even when D22C cannot be parsed.
                if (state is CameraConnectionState.Ready) {
                    FocusDebugPanel(
                        frame = f,
                        liveFrameCount = liveFrameCount,
                        focusAreaCode = focusAreaCode,
                        focusEventCount = focusEventCount,
                        debug = focusDebug,
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .systemBarsPadding()
                            .fillMaxWidth()
                            .padding(start = 12.dp, end = 12.dp, top = 66.dp)
                    )
                }

                // ── Shutter flash ─────────────────────────────────────────────
''',
    "CameraScreen debug panel placement",
)
insert_marker = "@Composable\nprivate fun FocusPointOverlay(modifier: Modifier = Modifier) {"
panel = r'''@Composable
private fun FocusDebugPanel(
    frame: Bitmap?,
    liveFrameCount: Long,
    focusAreaCode: Int?,
    focusEventCount: Int,
    debug: String,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(Color.Black.copy(alpha = 0.72f))
            .padding(horizontal = 10.dp, vertical = 7.dp)
    ) {
        Text(
            "AF DEBUG",
            color = Color(0xFFFFD166),
            fontSize = 10.sp,
            fontWeight = FontWeight.Bold
        )
        Text(
            "frames=$liveFrameCount  size=${frame?.width ?: 0}x${frame?.height ?: 0}  " +
                "areaEvents=$focusEventCount",
            color = Color.White,
            fontSize = 9.sp
        )
        Text(
            "area=${focusAreaCode?.let { focusAreaLabel(it) + " / 0x%04X".format(it) } ?: "NONE"}",
            color = if (focusAreaCode == null) Color(0xFFFFC857) else Color(0xFF36D399),
            fontSize = 9.sp
        )
        Text(
            debug,
            color = Color.White.copy(alpha = 0.78f),
            fontSize = 8.sp,
            maxLines = 4
        )
    }
}

'''
text = replace_once(text, insert_marker, panel + insert_marker, "FocusDebugPanel function")
write(path, text)


# Remove the one-shot machinery from the resulting source commit.
Path(".github/workflows/apply-focus-debug-once.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)

print("Focus debug patch applied successfully")
