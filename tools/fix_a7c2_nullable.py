from pathlib import Path

path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")
text = path.read_text(encoding="utf-8")
old = '''        val areaValue = when {
            areaDirect.isSuccess && (areaDirectValue and 0xFFFF) in knownAreaValues -> areaDirectValue and 0xFFFF
            areaHit?.standardValue != null && (areaHit.standardValue and 0xFFFF) in knownAreaValues -> areaHit.standardValue and 0xFFFF
            areaHit?.sonyFlaggedValue != null && (areaHit.sonyFlaggedValue and 0xFFFF) in knownAreaValues -> areaHit.sonyFlaggedValue and 0xFFFF
            else -> null
        }
'''
new = '''        val areaDirect16 = areaDirectValue?.and(0xFFFF)
        val areaStandard16 = areaHit?.standardValue?.and(0xFFFF)
        val areaSony16 = areaHit?.sonyFlaggedValue?.and(0xFFFF)

        val areaValue = when {
            areaDirect.isSuccess && areaDirect16 != null && areaDirect16 in knownAreaValues -> areaDirect16
            areaStandard16 != null && areaStandard16 in knownAreaValues -> areaStandard16
            areaSony16 != null && areaSony16 in knownAreaValues -> areaSony16
            else -> null
        }
'''
if text.count(old) != 1:
    raise SystemExit(f"Expected one target block, found {text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

Path(".github/workflows/fix-a7c2-nullable-once.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
print("Fixed nullable AF diagnostic parsing")
