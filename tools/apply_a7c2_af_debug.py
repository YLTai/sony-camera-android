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


def replace_between(text, start, end, replacement, label):
    s = text.find(start)
    if s < 0:
        raise RuntimeError(f"{label}: start marker not found")
    e = text.find(end, s)
    if e < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:s] + replacement + text[e:]


# Add the newer Sony AF-area-position property code.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/PtpConstants.kt"
text = read(path)
text = replace_once(
    text,
    "    const val PROP_SONY_FOCUS_AREA = 0xD22C // Sony Focus Area (PTP2)\n",
    "    const val PROP_SONY_FOCUS_AREA = 0xD22C // Sony Focus Area\n"
    "    const val PROP_SONY_AF_AREA_POSITION = 0xD2DC // AF Area Position (u32 on newer bodies)\n",
    "PtpConstants AF area position",
)
write(path, text)


# Replace the focus probe with a dual D22C + D2DC diagnostic probe.
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
text = read(path)
start = "    data class FocusAreaProbe(\n"
end = "    /** Backwards-compatible convenience accessor. */\n"
replacement = r'''    data class FocusAreaProbe(
        val focusAreaCode: Int?,
        val afAreaPositionRaw: Int?,
        val debug: String
    )

    private data class PropertyBlobHit(
        val offset: Int,
        val dataType: Int,
        val standardValue: Int?,
        val sonyFlaggedValue: Int?,
        val bytes: String
    )

    /**
     * Probe focus-area information with diagnostics for both older and newer
     * Sony bodies. ILCE-7CM2 (A7C II) is newer than the original A6600 target,
     * so we explicitly inspect both Focus Area (0xD22C) and AF Area Position
     * (0xD2DC). We report raw position bits only; we do not label low/high
     * halves as X/Y until the real camera confirms the encoding.
     */
    fun probeFocusArea(): FocusAreaProbe {
        val knownAreaValues = setOf(
            0x0001, 0x0002, 0x0003,
            0x0101, 0x0102, 0x0103, 0x0104,
            0x0201, 0x0202, 0x0203, 0x0204, 0x0205, 0x0206, 0x0207,
            0x0105, 0x0106, 0x0107, 0x0108,
            0x1101, 0x1102, 0x1103,
            0x1201, 0x1202, 0x1203
        )

        fun parseDirectValue(data: ByteArray): Int? = when {
            data.size >= 4 -> ByteBuffer.wrap(data, 0, 4)
                .order(ByteOrder.LITTLE_ENDIAN).int
            data.size >= 2 -> ByteBuffer.wrap(data, 0, 2)
                .order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
            data.size == 1 -> data[0].toInt() and 0xFF
            else -> null
        }

        fun valueSize(dataType: Int): Int = when (dataType) {
            1, 2 -> 1
            3, 4 -> 2
            5, 6 -> 4
            7, 8 -> 8
            else -> 0
        }

        fun readValue(data: ByteArray, offset: Int, size: Int): Int? {
            if (offset < 0 || size !in 1..4 || offset + size > data.size) return null
            return when (size) {
                1 -> data[offset].toInt() and 0xFF
                2 -> ByteBuffer.wrap(data, offset, 2)
                    .order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
                4 -> ByteBuffer.wrap(data, offset, 4)
                    .order(ByteOrder.LITTLE_ENDIAN).int
                else -> null
            }
        }

        fun findBlobHit(data: ByteArray, propertyCode: Int): PropertyBlobHit? {
            for (offset in 0 until data.size - 4) {
                val code = (data[offset].toInt() and 0xFF) or
                    ((data[offset + 1].toInt() and 0xFF) shl 8)
                if (code != propertyCode) continue

                val type = (data[offset + 2].toInt() and 0xFF) or
                    ((data[offset + 3].toInt() and 0xFF) shl 8)
                val size = valueSize(type)
                if (size == 0) continue

                // Two layouts observed across Sony generations:
                // standard PTP: code/type/getSet/default/current
                // Sony variant: code/type/getSet/default/flag/current
                val standard = readValue(data, offset + 5 + size, size)
                val sonyFlagged = readValue(data, offset + 6 + size, size)

                val from = (offset - 2).coerceAtLeast(0)
                val to = (offset + 20).coerceAtMost(data.size)
                val bytes = data.copyOfRange(from, to)
                    .joinToString(" ") { "%02X".format(it.toInt() and 0xFF) }
                return PropertyBlobHit(offset, type, standard, sonyFlagged, bytes)
            }
            return null
        }

        fun fmt16(value: Int?): String = value?.let { "0x%04X".format(it and 0xFFFF) } ?: "n/a"
        fun fmt32(value: Int?): String = value?.let { "0x%08X".format(it) } ?: "n/a"
        fun split32(value: Int?): String {
            if (value == null) return "lo=n/a hi=n/a"
            val lo = value and 0xFFFF
            val hi = (value ushr 16) and 0xFFFF
            return "lo=0x%04X(%d) hi=0x%04X(%d)".format(lo, lo, hi, hi)
        }

        // Try direct Sony property-value reads first.
        val areaDirect = transport.sendCommandWithData(
            PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
            PtpConstants.PROP_SONY_FOCUS_AREA
        )
        val areaDirectValue = parseDirectValue(areaDirect.data)

        val posDirect = transport.sendCommandWithData(
            PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
            PtpConstants.PROP_SONY_AF_AREA_POSITION
        )
        val posDirectValue = parseDirectValue(posDirect.data)

        // Also try Sony GetControlDeviceDesc for D2DC. On some generations
        // control properties are described through 0x9206 instead of 0x9203/0x9204.
        val posControlDesc = transport.sendCommandWithData(
            PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC,
            PtpConstants.PROP_SONY_AF_AREA_POSITION
        )

        // One aggregate read lets us inspect both properties without adding
        // another full 0x9209 transaction.
        val all = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)
        val areaHit = if (all.isSuccess) findBlobHit(all.data, PtpConstants.PROP_SONY_FOCUS_AREA) else null
        val posHit = if (all.isSuccess) findBlobHit(all.data, PtpConstants.PROP_SONY_AF_AREA_POSITION) else null

        val areaValue = when {
            areaDirect.isSuccess && (areaDirectValue and 0xFFFF) in knownAreaValues -> areaDirectValue and 0xFFFF
            areaHit?.standardValue != null && (areaHit.standardValue and 0xFFFF) in knownAreaValues -> areaHit.standardValue and 0xFFFF
            areaHit?.sonyFlaggedValue != null && (areaHit.sonyFlaggedValue and 0xFFFF) in knownAreaValues -> areaHit.sonyFlaggedValue and 0xFFFF
            else -> null
        }

        val positionValue = when {
            posDirect.isSuccess && posDirect.data.size >= 4 -> posDirectValue
            posHit?.standardValue != null -> posHit.standardValue
            posHit?.sonyFlaggedValue != null -> posHit.sonyFlaggedValue
            else -> null
        }

        val debug = buildString {
            append("model=").append(deviceName ?: "?")
            append(" | D22C/9204=")
            append(PtpConstants.responseCodeName(areaDirect.responseCode))
            append(" ").append(areaDirect.dataSize).append("B ").append(fmt16(areaDirectValue))

            append(" | D2DC/9204=")
            append(PtpConstants.responseCodeName(posDirect.responseCode))
            append(" ").append(posDirect.dataSize).append("B ").append(fmt32(posDirectValue))
            append(" ").append(split32(posDirectValue))

            append(" | D2DC/9206=")
            append(PtpConstants.responseCodeName(posControlDesc.responseCode))
            append(" ").append(posControlDesc.dataSize).append("B")
            if (posControlDesc.data.isNotEmpty()) {
                append(" bytes=")
                append(posControlDesc.data.take(20).joinToString(" ") { "%02X".format(it.toInt() and 0xFF) })
            }

            append(" | 9209=")
            append(PtpConstants.responseCodeName(all.responseCode)).append(" ").append(all.dataSize).append("B")
            if (areaHit == null) append(" D22C:not-found")
            else append(" D22C@").append(areaHit.offset)
                .append(" type=0x%04X".format(areaHit.dataType))
                .append(" std=").append(fmt16(areaHit.standardValue))
                .append(" sony=").append(fmt16(areaHit.sonyFlaggedValue))
            if (posHit == null) append(" D2DC:not-found")
            else append(" D2DC@").append(posHit.offset)
                .append(" type=0x%04X".format(posHit.dataType))
                .append(" std=").append(fmt32(posHit.standardValue))
                .append(" sony=").append(fmt32(posHit.sonyFlaggedValue))
                .append(" ").append(split32(positionValue))
        }

        val result = FocusAreaProbe(areaValue, positionValue, debug)
        Log.d(TAG, "AF probe: ${result.debug}")
        return result
    }

'''
text = replace_between(text, start, end, replacement, "Sony dual AF probe")
# Keep convenience accessor compatible.
write(path, text)


# Make debug panel fit the longer A7C II diagnostics and remove A6600-only copy.
path = "demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt"
text = read(path)
text = text.replace(
    "                // ILCE-6600/PTP2 exposes the focus-area MODE via 0xD22C, but not\n"
    "                // an arbitrary live AF-frame XY coordinate. Center is deterministic.\n",
    "                // Focus-area mode (D22C). Position support differs by Sony generation;\n"
    "                // D2DC raw position data is shown in the AF DEBUG panel below.\n"
)
text = text.replace(
    '            text = if (isCenter) "AF point: center" else "AF point: unavailable over PTP2",\n',
    '            text = if (isCenter) "AF point: center" else "AF position: see AF DEBUG",\n'
)
text = replace_once(text, "            maxLines = 4\n", "            maxLines = 7\n", "AF debug maxLines")
write(path, text)

# Remove temporary workflow/script from the resulting source commit.
Path(".github/workflows/apply-a7c2-af-debug-once.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)

print("A7C II AF debug patch applied")
