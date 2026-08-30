from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
text = path.read_text()

broken = '''        monitorAfDebugState = buildString {
            append("AF AREA direct fallback")
            append("
").append(stateLine)
            append("
legacyAreaRaw=").append(focusArea?.currentValue ?: -1)
            append(" (no forced write)")
        }
'''
fixed = '''        monitorAfDebugState = buildString {
            append("AF AREA direct fallback")
            append("\\n").append(stateLine)
            append("\\nlegacyAreaRaw=").append(focusArea?.currentValue ?: -1)
            append(" (no forced write)")
        }
'''
if broken not in text:
    raise SystemExit("broken fallback string block not found")
path.write_text(text.replace(broken, fixed, 1))
Path(__file__).unlink()
