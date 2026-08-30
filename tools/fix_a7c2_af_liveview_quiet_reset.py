from pathlib import Path

path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt")
text = path.read_text()
original = text

bad = '''                        postCaptureResumeDeadlineMs = 0L
        afLiveviewQuietUntilMs = 0L
                        lastFrameTime = System.currentTimeMillis()
'''
good = '''                        postCaptureResumeDeadlineMs = 0L
                        lastFrameTime = System.currentTimeMillis()
'''
if text.count(bad) != 1:
    raise SystemExit(f"bad liveview reset count={text.count(bad)}")
text = text.replace(bad, good, 1)

old = '''    private fun closeUsbResources() {
        isLiveviewActive = false
        liveviewJob?.cancel()
'''
new = '''    private fun closeUsbResources() {
        isLiveviewActive = false
        afLiveviewQuietUntilMs = 0L
        liveviewJob?.cancel()
'''
if text.count(old) != 1:
    raise SystemExit(f"cleanup marker count={text.count(old)}")
text = text.replace(old, new, 1)

if text == original:
    raise SystemExit("no changes")
path.write_text(text)
Path(__file__).unlink()
