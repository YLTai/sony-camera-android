from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SONY = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
UI = ROOT / "demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt"

sony = SONY.read_text()
ui = UI.read_text()

old = '''        fun descriptorMeta(descriptor: SonyScalarEnumProperty?): String {
            if (descriptor == null) return "[missing]"
            val candidates = if (descriptor.enumValues.isEmpty()) {
                "-"
            } else {
                descriptor.enumValues.joinToString(",")
            }
            return "[t=0x${descriptor.dataType.toString(16)} gs=0x${descriptor.getSetState.toString(16)} " +
                "en=${descriptor.enabledState} w=${if (descriptor.writable) 1 else 0} vals=$candidates]"
        }

        val touchState = "TO=${transition(touchBefore, touchAfter)}${descriptorMeta(touchAfterProp)}"
        val touchFunctionState = "TF=${transition(touchFunctionBefore, touchFunctionAfter)}${descriptorMeta(touchFunctionAfterProp)}"
        val remoteFunctionState = "RF=${transition(remoteFunctionBefore, remoteFunctionAfter)}${writeResult(remoteFunctionWrite)}${descriptorMeta(remoteFunction)}"
        val remoteEnableState = "RT=${transition(remoteEnableBefore, remoteEnableAfter)}${descriptorMeta(remoteEnable)}"
        val actionState = "ACT=${actionProp?.currentValue ?: -1}${descriptorMeta(actionProp)}"
        val stateLine = "$touchState $touchFunctionState $remoteFunctionState $remoteEnableState $actionState reads=$settleReads"
'''
new = '''        fun descriptorMeta(descriptor: SonyScalarEnumProperty?, target: Long? = null): String {
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
        val touchFunctionState = "TF=${transition(touchFunctionBefore, touchFunctionAfter)} ${descriptorMeta(touchFunctionAfterProp, 3L)}"
        val remoteFunctionState = "RF=${transition(remoteFunctionBefore, remoteFunctionAfter)}${writeResult(remoteFunctionWrite)} ${descriptorMeta(remoteFunction, 2L)}"
        val remoteEnableState = "RT=${transition(remoteEnableBefore, remoteEnableAfter)} ${descriptorMeta(remoteEnable, 1L)}"
        val actionState = "ACT=${actionProp?.currentValue ?: -1} ${descriptorMeta(actionProp)}"
        val stateLine = listOf(
            touchState,
            touchFunctionState,
            remoteFunctionState,
            remoteEnableState,
            actionState,
            "reads=$settleReads"
        ).joinToString("\\n")
'''
assert old in sony, "remote touch diagnostic formatting block not found"
sony = sony.replace(old, new, 1)

old_ui = '''                        maxLines = 6,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .padding(start = 10.dp, top = if (menusVisible) 64.dp else 10.dp)
                            .widthIn(max = 420.dp)
'''
new_ui = '''                        maxLines = 14,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .padding(start = 10.dp, top = if (menusVisible) 64.dp else 10.dp)
                            .widthIn(max = 620.dp)
'''
assert old_ui in ui, "focus debug UI block not found"
ui = ui.replace(old_ui, new_ui, 1)

SONY.write_text(sony)
UI.write_text(ui)
Path(__file__).unlink()
