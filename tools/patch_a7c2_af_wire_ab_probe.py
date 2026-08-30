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
'''        // Diagnostic only: after an ILCE-7CM2 monitor tap, stop issuing any new
        // Live View GetObject / background telemetry requests for a short window.
        // The AF command itself is NOT delayed. This isolates whether sustained
        // PC-Remote polling is starving the camera body's own AF-frame UI update.
        private const val AF_BODY_SETTLE_QUIET_MS = 650L
''',
'',
"remove quiet constant",
)

replace_once(
'''    @Volatile private var postCaptureResumeDeadlineMs = 0L
    @Volatile private var afLiveviewQuietUntilMs = 0L
''',
'''    @Volatile private var postCaptureResumeDeadlineMs = 0L
''',
"remove quiet field",
)

replace_once(
'''    private var remoteTouchRuntimeProbeJob: Job? = null
    private var afGeneration = 0L
''',
'''    private var remoteTouchRuntimeProbeJob: Job? = null
    private var afGeneration = 0L
    // ILCE-7CM2 diagnostic A/B: alternate the two camera-native wire actions
    // in one session so body-LCD latency can be compared without S1 or fake UI.
    private var afWireProbeD2dcNext = true
''',
"add A/B state",
)

replace_once(
'''    private fun endControlWrite(epoch: Long) = synchronized(controlEpochLock) {
        if (controlEpoch == epoch) {
            controlWriteActive = false
            telemetryResumeAtMs = maxOf(
                System.currentTimeMillis() + CONTROL_POLL_QUIET_MS,
                afLiveviewQuietUntilMs
            )
        }
    }
''',
'''    private fun endControlWrite(epoch: Long) = synchronized(controlEpochLock) {
        if (controlEpoch == epoch) {
            controlWriteActive = false
            telemetryResumeAtMs = System.currentTimeMillis() + CONTROL_POLL_QUIET_MS
        }
    }
''',
"restore telemetry quiet handling",
)

replace_once(
'''                    // ILCE-7CM2 AF isolation: the user control is sent immediately,
                    // but no NEW GetObject starts during the body-settle window.
                    // Holding the last real frame is intentional; we never draw a
                    // synthetic AF result.
                    val afQuietRemainingMs = afLiveviewQuietUntilMs - System.currentTimeMillis()
                    if (afQuietRemainingMs > 0L) {
                        delay(minOf(5L, afQuietRemainingMs))
                        continue
                    }

''',
'',
"remove liveview quiet loop",
)

replace_once(
'''        val requestedAtMs = System.currentTimeMillis()
        // Start the quiet window at the actual AF request. Existing in-flight
        // PTP work may finish, but the Live View producer will not launch another
        // request. This changes only background load; D2E4 is not delayed.
        if (ptpCamera?.deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true) {
            afLiveviewQuietUntilMs = requestedAtMs + AF_BODY_SETTLE_QUIET_MS
        }
        priorityControlIntents.incrementAndGet()
''',
'''        val requestedAtMs = System.currentTimeMillis()
        priorityControlIntents.incrementAndGet()
''',
"remove setAfPoint quiet start",
)

marker = '''                        val baseline = latestFocusFrameInfo

                        // a7C II fast path: RemoteTouchOperation (D2E4). The previous
'''
insert = '''                        val baseline = latestFocusFrameInfo
                        val a7c2WireProbe = camera.deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true

                        // Controlled same-session A/B test. A uses only AF Area Position
                        // (D2DC): no S1, no Remote Touch, no Live View pause. Sony's own
                        // RemoteSampleApp documents AF Area Position as the direct focus-frame
                        // center move. The next tap uses B (D2E4), then alternates again.
                        if (a7c2WireProbe && afWireProbeD2dcNext) {
                            afWireProbeD2dcNext = false
                            val moveStartedMs = System.currentTimeMillis()
                            val move = camera.moveAfAreaPosition(safeX, safeY)
                            val moveAckAtMs = System.currentTimeMillis()
                            val wireAndAckMs = (moveAckAtMs - moveStartedMs - move.queueWaitMs).coerceAtLeast(0L)
                            val ackMs = moveAckAtMs - requestedAtMs
                            if (!move.isSuccess) {
                                synchronized(afStateLock) { pendingAfFrameLatency = null }
                                val message = "AF A D2DC-ONLY FAIL x=$safeX y=$safeY\\n$prepDebug\\n" +
                                    "D2DC=${PtpConstants.responseCodeName(move.responseCode)} ack=${ackMs}ms next=B"
                                _events.emit(CameraEvent.FocusDebug(message))
                                return@withLock CameraOperationResult.Failure(message)
                            }
                            synchronized(afStateLock) {
                                pendingAfFrameLatency = PendingAfFrameLatency(
                                    generation = generation,
                                    x = safeX,
                                    y = safeY,
                                    requestedAtMs = requestedAtMs,
                                    ackAtMs = moveAckAtMs,
                                    commandDoneAtMs = moveAckAtMs,
                                    path = "A:D2DC-only",
                                    s1Ms = null,
                                    baseline = baseline,
                                    prepDebug = prepDebug
                                )
                            }
                            val message = "AF A D2DC-ONLY x=$safeX y=$safeY\\n" +
                                "$prepDebug\\n" +
                                "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${move.queueWaitMs}ms " +
                                "wire+ack=${wireAndAckMs}ms ack=${ackMs}ms NO-S1 next=B"
                            Log.d(TAG, message.replace('\\n', ' '))
                            _events.emit(CameraEvent.FocusDebug(message))
                            _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
                            return@withLock CameraOperationResult.SuccessWithData(message)
                        }
                        if (a7c2WireProbe) afWireProbeD2dcNext = true

                        // B path: Sony RemoteTouchOperation (D2E4), also without S1.
                        // a7C II fast path: RemoteTouchOperation (D2E4). The previous
'''
replace_once(marker, insert, "insert D2DC A path")

replace_once(
'''                                        path = "RT(D2E4)",
''',
'''                                        path = if (a7c2WireProbe) "B:RT(D2E4)" else "RT(D2E4)",
''',
"label B metadata path",
)

replace_once(
'''                                val message = "AF RT(D2E4) QUIET x=$safeX y=$safeY\\n" +
                                    "$prepDebug\\n" +
                                    "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${touch.queueWaitMs}ms wire+ack=${wireAndAckMs}ms ack=${ackMs}ms " +
                                    "lvQuiet=${AF_BODY_SETTLE_QUIET_MS}ms"
''',
'''                                val message = (if (a7c2WireProbe) "AF B RT(D2E4)" else "AF RT(D2E4)") +
                                    " x=$safeX y=$safeY\\n" +
                                    "$prepDebug\\n" +
                                    "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${touch.queueWaitMs}ms " +
                                    "wire+ack=${wireAndAckMs}ms ack=${ackMs}ms NO-S1" +
                                    if (a7c2WireProbe) " next=A" else ""
''',
"remove quiet D2E4 debug",
)

replace_once(
'''        isLiveviewActive = false
        afLiveviewQuietUntilMs = 0L
        liveviewJob?.cancel()
''',
'''        isLiveviewActive = false
        afWireProbeD2dcNext = true
        liveviewJob?.cancel()
''',
"reset A/B state on close",
)

if "afLiveviewQuietUntilMs" in text or "AF_BODY_SETTLE_QUIET_MS" in text:
    raise SystemExit("quiet-window symbols remain")
if text == original:
    raise SystemExit("no changes")

path.write_text(text)
Path(__file__).unlink()
