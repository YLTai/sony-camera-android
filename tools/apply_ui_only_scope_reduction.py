from pathlib import Path
import subprocess

BASE = "af41dfe333d4c4f67dc72d6123c952e4760a4642"
PTP = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")
SCREEN = Path("demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt")

# Restore the entire protocol implementation byte-for-byte to the last accepted
# baseline. This deliberately removes both the Drive Mode probing/writes and the
# exposure-descriptor writable-state changes from round 5.
restored = subprocess.check_output(["git", "show", f"{BASE}:{PTP.as_posix()}"])
PTP.write_bytes(restored)

text = SCREEN.read_text()
old_entry = "            CameraSetting.DRIVE_MODE to settings?.driveMode,\n"
if old_entry not in text:
    raise SystemExit("Drive Mode strip entry not found")
text = text.replace(old_entry, "", 1)

old_value = '''                value = property?.current?.label
                    ?: if (setting == CameraSetting.DRIVE_MODE) "USB N/A" else "--",
'''
new_value = '''                value = property?.current?.label ?: "--",
'''
if old_value not in text:
    raise SystemExit("round-5 Drive Mode USB N/A UI block not found")
text = text.replace(old_value, new_value, 1)
SCREEN.write_text(text)

# Self-delete so no patch helper remains in the repository after the commit.
Path(__file__).unlink()
