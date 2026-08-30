from pathlib import Path

path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt')
text = path.read_text()

old_enum = '''                2 -> {
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
'''
new_enum = '''                2 -> {
                    if (offset + 2 > data.size) continue

                    // PTP3 enumeration descriptors contain TWO candidate lists:
                    //   1) values valid for Set
                    //   2) values valid for Get/Set
                    // A Get-only property such as D284 legitimately has ZERO
                    // entries in the first list. Treating count=0 as malformed was
                    // why RemoteTouchOperationEnableStatus disappeared on a7C II.
                    val setCount = u16(data, offset)
                    offset += 2
                    if (setCount !in 0..512 || offset + setCount * size > data.size) continue
                    val setValues = List(setCount) { index ->
                        readUnsignedScalar(data, offset + index * size, size) ?: 0L
                    }
                    offset += setCount * size

                    var getSetValues = emptyList<Long>()
                    if (offset + 2 <= data.size) {
                        val getSetCount = u16(data, offset)
                        // A following Sony property header is >=0x5000, so only a
                        // bounded small value can be the PTP3 second-list count.
                        if (getSetCount in 0..512 &&
                            offset + 2 + getSetCount * size <= data.size
                        ) {
                            offset += 2
                            getSetValues = List(getSetCount) { index ->
                                readUnsignedScalar(data, offset + index * size, size) ?: 0L
                            }
                            offset += getSetCount * size
                        }
                    }
                    values = if (getSetValues.isNotEmpty()) getSetValues else setValues
                }
            }

            // PTP3 GetSet is 0x00=Get and 0x01=Get/Set. IsEnabled describes
            // whether the property is currently usable; it does not make a
            // Get-only status writable.
            val writable = getSet != 0 && enabled == 1
'''
if old_enum not in text:
    raise SystemExit('enum parser block not found')
text = text.replace(old_enum, new_enum, 1)

start_marker = '''        // Sony Camera Remote SDK documents Remote Touch as its own operation:
'''
end_marker = '''        if (remoteTouchSupported) {
'''
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit('remote touch preparation block not found')

new_prep = '''        // a7C II uses the D-range Remote Touch controls. Sony's PTP3 function
        // matrix explicitly supports D284 + D2E4 on ILCE-7CM2, while E083 is
        // NOT supported on this body. D284 is the authoritative Get-only gate.
        // D047/D283 remain visible so we can verify the camera's local touch mode
        // (for example D283=9 means Touch Focus + Touch AE OFF).
        val touchBeforeProp = property(PtpConstants.PROP_SONY_TOUCH_OPERATION)
        val touchFunctionBeforeProp = property(PtpConstants.PROP_SONY_FUNCTION_OF_TOUCH_OPERATION)
        val remoteEnableBeforeProp = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS)

        val touchAfterProp = touchBeforeProp
        val touchFunctionAfterProp = touchFunctionBeforeProp
        val remoteEnable = remoteEnableBeforeProp

        val touchBefore = touchBeforeProp?.currentValue
        val touchFunctionBefore = touchFunctionBeforeProp?.currentValue
        val remoteEnableBefore = remoteEnableBeforeProp?.currentValue
        val touchAfter = touchAfterProp?.currentValue
        val touchFunctionAfter = touchFunctionAfterProp?.currentValue
        val remoteEnableAfter = remoteEnable?.currentValue

        // Do not gate a7C II Remote Touch on E083: Sony's function list says the
        // ILCE-7CM2 does not expose FunctionOfRemoteTouchOperation. D284 alone is
        // the documented execution-enable status for D2E4.
        remoteTouchSupported = remoteEnableAfter == 1L

        fun transition(before: Long?, after: Long?): String = when {
            before == null && after == null -> "na"
            before == after -> (after ?: -1L).toString()
            else -> "${before ?: -1}>${after ?: -1}"
        }
        fun descriptorMeta(descriptor: SonyScalarEnumProperty?, target: Long? = null): String {
            if (descriptor == null) return "missing"
            val candidateState = when {
                target == null -> ""
                descriptor.enumValues.isEmpty() -> " cand=?"
                target in descriptor.enumValues -> " cand=Y"
                else -> " cand=N"
            }
            return "t=0x${descriptor.dataType.toString(16)} gs=0x${descriptor.getSetState.toString(16)} " +
                "en=${descriptor.enabledState} w=${if (descriptor.writable) 1 else 0}$candidateState"
        }

        val touchState = "TO=${transition(touchBefore, touchAfter)} ${descriptorMeta(touchAfterProp, 2L)}"
        // Touch Focus is represented by 3, 8 or 9 on current Sony bodies; keep
        // the raw value visible rather than pretending only value 3 is valid.
        val touchFocusState = if (touchFunctionAfter in setOf(3L, 8L, 9L)) "focus=Y" else "focus=N"
        val touchFunctionState = "TF=${transition(touchFunctionBefore, touchFunctionAfter)} $touchFocusState ${descriptorMeta(touchFunctionAfterProp)}"
        val remoteEnableState = "RT=${transition(remoteEnableBefore, remoteEnableAfter)} ${descriptorMeta(remoteEnable, 1L)}"
        val stateLine = listOf(
            touchState,
            touchFunctionState,
            "RF=unsupported-on-ILCE-7CM2",
            remoteEnableState,
            "PTP3OPT=${if (sonyPtp3DevicePropertyOptionEnabled) 1 else 0}"
        ).joinToString("\\n")

'''
text = text[:start] + new_prep + text[end:]

# The success/fallback block below still refers to stateLine and no longer needs
# the removed E083/direct-probe locals. Update the success label for clarity.
text = text.replace('monitorAfDebugState = "AF RT SpotAF ready\\n$stateLine"',
                    'monitorAfDebugState = "AF RT D284 ready\\n$stateLine"', 1)

# Update the method documentation so future work does not reintroduce E083.
text = text.replace(
'''     * Preferred: Sony Remote Touch D2E4 with D284=Enable and E083=Spot AF.
     * Fallback: Sony sample-app AF Area Position semantics — preselect Spot S
     * (Flexible Spot S) and move D2DC, then the manager triggers S1 separately.
''',
'''     * Preferred on a7C II: Sony Remote Touch D2E4 when D284=Enable.
     * ILCE-7CM2 does not expose E083; never require it on this body.
     * Fallback: move D2DC, then the manager triggers S1 separately.
''', 1)

path.write_text(text)
Path(__file__).unlink()
