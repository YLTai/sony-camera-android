from pathlib import Path
p = Path('demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt')
s = p.read_text()
s = s.replace('if (isSelected) "SELECTED  •  HOLD X" else ".CUBE"', 'if (isSelected) "SELECTED" else ".CUBE"')
old = '''private fun nextPeakingLevel(level: PeakingLevel): PeakingLevel = when (level) {
    PeakingLevel.OFF -> PeakingLevel.MID
    PeakingLevel.LOW -> PeakingLevel.MID
    PeakingLevel.MID -> PeakingLevel.HIGH
    PeakingLevel.HIGH -> PeakingLevel.LOW
}'''
new = '''private fun nextPeakingLevel(level: PeakingLevel): PeakingLevel = when (level) {
    PeakingLevel.OFF -> PeakingLevel.MID
    PeakingLevel.MID -> PeakingLevel.HIGH
    PeakingLevel.HIGH -> PeakingLevel.LOW
    PeakingLevel.LOW -> PeakingLevel.OFF
}'''
if old not in s:
    raise SystemExit('peaking cycle marker not found')
s = s.replace(old, new, 1)
p.write_text(s)
print('monitor toggle polish applied')
