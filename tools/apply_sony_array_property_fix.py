from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SONY = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"

sony = SONY.read_text()

new_parser = r'''    private fun findSonyScalarEnumProperty(
        data: ByteArray,
        propertyCode: Int,
        dataType: Int
    ): SonyScalarEnumProperty? {
        val isArray = (dataType and 0x4000) != 0
        val elementType = dataType and 0xBFFF
        val size = scalarSize(elementType)
        if (size == 0 || data.size < 15) return null

        fun u32At(offset: Int): Long? {
            if (offset < 0 || offset + 4 > data.size) return null
            return (data[offset].toLong() and 0xFF) or
                ((data[offset + 1].toLong() and 0xFF) shl 8) or
                ((data[offset + 2].toLong() and 0xFF) shl 16) or
                ((data[offset + 3].toLong() and 0xFF) shl 24)
        }

        data class ParsedValue(val first: Long?, val nextOffset: Int)

        fun readValue(offset: Int): ParsedValue? {
            if (!isArray) {
                val value = readUnsignedScalar(data, offset, size) ?: return null
                return ParsedValue(value, offset + size)
            }

            val countLong = u32At(offset) ?: return null
            if (countLong > 1024L) return null
            val count = countLong.toInt()
            val valuesOffset = offset + 4
            val end = valuesOffset + count * size
            if (end < valuesOffset || end > data.size) return null
            val first = if (count > 0) readUnsignedScalar(data, valuesOffset, size) else null
            return ParsedValue(first, end)
        }

        // Sony 0x9209 is a concatenation of vendor descriptors. Search for the
        // requested header but decode its default/current values according to
        // the PTP array bit (0x4000). Camera Remote SDK exposes many modern
        // properties, including Remote Touch, as UInt8Array/UInt16Array.
        for (base in 8 until data.size - 6) {
            if (u16(data, base) != propertyCode || u16(data, base + 2) != dataType) continue
            val getSet = data.getOrNull(base + 4)?.toInt()?.and(0xFF) ?: continue
            val enabled = data.getOrNull(base + 5)?.toInt()?.and(0xFF) ?: continue
            if (enabled !in 0..2) continue

            val defaultValue = readValue(base + 6) ?: continue
            val currentValue = readValue(defaultValue.nextOffset) ?: continue
            var offset = currentValue.nextOffset
            val form = data.getOrNull(offset)?.toInt()?.and(0xFF) ?: continue
            if (form !in 0..2) continue
            offset += 1

            var values = emptyList<Long>()
            when (form) {
                1 -> {
                    // Sony encodes range members as element scalars even when
                    // the property's SDK datatype is an array.
                    if (offset + size * 3 > data.size) continue
                    offset += size * 3
                }
                2 -> {
                    if (offset + 2 > data.size) continue
                    val count = u16(data, offset)
                    offset += 2
                    if (count !in 1..512 || offset + count * size > data.size) continue
                    values = List(count) { index ->
                        readUnsignedScalar(data, offset + index * size, size) ?: 0L
                    }
                    offset += count * size

                    // 2024+ Sony bodies may append a second candidate list that
                    // supersedes the first. A real next property code is >=0x5000,
                    // while this secondary count is below 0x0200.
                    if (offset + 2 <= data.size) {
                        val secondaryCount = u16(data, offset)
                        if (secondaryCount in 1..511 &&
                            offset + 2 + secondaryCount * size <= data.size
                        ) {
                            val secondaryOffset = offset + 2
                            values = List(secondaryCount) { index ->
                                readUnsignedScalar(data, secondaryOffset + index * size, size) ?: 0L
                            }
                        }
                    }
                }
            }

            val writable = (getSet and 0x80) != 0 || enabled == 1
            return SonyScalarEnumProperty(
                propertyCode = propertyCode,
                dataType = dataType,
                currentValue = currentValue.first,
                enumValues = values,
                writable = writable,
                getSetState = getSet,
                enabledState = enabled
            )
        }
        return null
    }

'''

sony, count = re.subn(
    r'    private fun findSonyScalarEnumProperty\(.*?\n    /\*\* Find a scalar Sony property without assuming its wire integer width\. \*/\n',
    new_parser + '    /** Find a scalar Sony property without assuming its wire integer width. */\n',
    sony,
    count=1,
    flags=re.S,
)
assert count == 1, "failed to replace Sony property parser"

old_types = '''        val types = intArrayOf(0x0002, 0x0004, 0x0006, 0x0001, 0x0003, 0x0005)'''
new_types = '''        val types = intArrayOf(
            0x4002, 0x4004, 0x4006, 0x4001, 0x4003, 0x4005,
            0x0002, 0x0004, 0x0006, 0x0001, 0x0003, 0x0005
        )'''
assert old_types in sony, "AnyType list not found"
sony = sony.replace(old_types, new_types, 1)

old_size = '''        val size = scalarSize(descriptor.dataType)
        val payload = ByteBuffer.allocate(size).order(ByteOrder.LITTLE_ENDIAN).apply {'''
new_size = '''        // 0x9209 may describe a selectable setting as an array type, but Sony's
        // 0x9205 SetControlDeviceA takes one selected ELEMENT encoded at the
        // element width. Strip the PTP array bit before building the payload.
        val elementType = descriptor.dataType and 0xBFFF
        val size = scalarSize(elementType)
        require(size > 0) { "Unsupported Sony property type 0x${descriptor.dataType.toString(16)}" }
        val payload = ByteBuffer.allocate(size).order(ByteOrder.LITTLE_ENDIAN).apply {'''
assert old_size in sony, "setSonyScalarProperty size block not found"
sony = sony.replace(old_size, new_size, 1)

fallback_pattern = re.compile(
    r'''        // Only after Live View is active and the bounded D284 refresh still says\n'''
    r'''        // Disabled do we prepare the compatibility AF-area path\.\n'''
    r'''        var focusArea = findGenericSettingDescriptor\(data, CameraSetting\.FOCUS_AREA\).*?'''
    r'''        return monitorAfDebugState\n''',
    re.S,
)
new_fallback = '''        // Remote Touch is unavailable in this camera state. Do not force an old
        // numeric FocusArea value here: modern Sony bodies expose FocusArea as an
        // array-typed property and its wire enum is not an ordinal. Sony's sample
        // AF Area Position action itself selects the movable Spot-S semantics.
        // Keep D2DC+S1 as a compatibility path without a speculative setting write.
        val focusArea = findGenericSettingDescriptor(data, CameraSetting.FOCUS_AREA)
        monitorAfPrepared = true
        monitorAfDebugState = buildString {
            append("AF AREA direct fallback")
            append("\\n").append(stateLine)
            append("\\nlegacyAreaRaw=").append(focusArea?.currentValue ?: -1)
            append(" (no forced write)")
        }
        return monitorAfDebugState
'''
sony, count = fallback_pattern.subn(new_fallback, sony, count=1)
assert count == 1, "failed to replace AF-area fallback"

SONY.write_text(sony)
Path(__file__).unlink()
