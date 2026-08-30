from pathlib import Path

path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt")
text = path.read_text()
original = text


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    text = text.replace(old, new, 1)

replace_once(
    """        private const val CONTROL_POLL_QUIET_MS = 220L\n""",
    """        private const val CONTROL_POLL_QUIET_MS = 220L\n        // Diagnostic only: after an ILCE-7CM2 monitor tap, stop issuing any new\n        // Live View GetObject / background telemetry requests for a short window.\n        // The AF command itself is NOT delayed. This isolates whether sustained\n        // PC-Remote polling is starving the camera body's own AF-frame UI update.\n        private const val AF_BODY_SETTLE_QUIET_MS = 650L\n""",
    "quiet constant",
)

replace_once(
    """    @Volatile private var postCaptureResumeDeadlineMs = 0L\n""",
    """    @Volatile private var postCaptureResumeDeadlineMs = 0L\n    @Volatile private var afLiveviewQuietUntilMs = 0L\n""",
    "quiet state",
)

replace_once(
    """            while (isActive && isLiveviewActive) {\n                try {\n                    // Do not start another GetObject while a user control is waiting.\n""",
    """            while (isActive && isLiveviewActive) {\n                try {\n                    // ILCE-7CM2 AF isolation: the user control is sent immediately,\n                    // but no NEW GetObject starts during the body-settle window.\n                    // Holding the last real frame is intentional; we never draw a\n                    // synthetic AF result.\n                    val afQuietRemainingMs = afLiveviewQuietUntilMs - System.currentTimeMillis()\n                    if (afQuietRemainingMs > 0L) {\n                        delay(minOf(5L, afQuietRemainingMs))\n                        continue\n                    }\n\n                    // Do not start another GetObject while a user control is waiting.\n""",
    "liveview quiet gate",
)

replace_once(
    """            telemetryResumeAtMs = System.currentTimeMillis() + CONTROL_POLL_QUIET_MS\n""",
    """            telemetryResumeAtMs = maxOf(\n                System.currentTimeMillis() + CONTROL_POLL_QUIET_MS,\n                afLiveviewQuietUntilMs\n            )\n""",
    "telemetry quiet extension",
)

replace_once(
    """    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult {\n        val requestedAtMs = System.currentTimeMillis()\n        priorityControlIntents.incrementAndGet()\n""",
    """    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult {\n        val requestedAtMs = System.currentTimeMillis()\n        // Start the quiet window at the actual AF request. Existing in-flight\n        // PTP work may finish, but the Live View producer will not launch another\n        // request. This changes only background load; D2E4 is not delayed.\n        if (ptpCamera?.deviceName?.contains(\"ILCE-7CM2\", ignoreCase = true) == true) {\n            afLiveviewQuietUntilMs = requestedAtMs + AF_BODY_SETTLE_QUIET_MS\n        }\n        priorityControlIntents.incrementAndGet()\n""",
    "AF quiet start",
)

replace_once(
    """                                val message = \"AF RT(D2E4) x=$safeX y=$safeY\\n\" +\n                                    \"$prepDebug\\n\" +\n                                    \"dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${touch.queueWaitMs}ms wire+ack=${wireAndAckMs}ms ack=${ackMs}ms\"\n""",
    """                                val message = \"AF RT(D2E4) QUIET x=$safeX y=$safeY\\n\" +\n                                    \"$prepDebug\\n\" +\n                                    \"dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${touch.queueWaitMs}ms wire+ack=${wireAndAckMs}ms ack=${ackMs}ms \" +\n                                    \"lvQuiet=${AF_BODY_SETTLE_QUIET_MS}ms\"\n""",
    "remote touch debug label",
)

probe_call = """                                startRemoteTouchRuntimeProbe(\n                                    camera = camera,\n                                    generation = generation,\n                                    requestedAtMs = requestedAtMs,\n                                    ackAtMs = touchAckAtMs,\n                                    x = safeX,\n                                    y = safeY\n                                )\n"""
replace_once(
    probe_call,
    """                                // Previous E004/E005/D285 probe stayed static and its 0x9209\n                                // reads add camera load. Do not sample them in this isolation round.\n""",
    "disable runtime probe",
)

replace_once(
    """        postCaptureResumeDeadlineMs = 0L\n""",
    """        postCaptureResumeDeadlineMs = 0L\n        afLiveviewQuietUntilMs = 0L\n""",
    "quiet reset",
)

if text == original:
    raise SystemExit("no changes")
path.write_text(text)
Path(__file__).unlink()
