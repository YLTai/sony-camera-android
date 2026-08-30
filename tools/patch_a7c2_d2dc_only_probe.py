from pathlib import Path

path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt")
text = path.read_text()
original = text

old = '''                        if (camera.supportsRemoteTouch()) {
'''
new = '''                        // Diagnostic isolation for ILCE-7CM2: D2E4 now ACKs in ~5 ms and
                        // FocalFrameInfo reaches the target in ~100 ms, yet the body LCD AF
                        // frame is still visibly ~0.5 s late.  Temporarily bypass Remote Touch
                        // on this body so one tap can measure D2DC by itself, without S1.
                        val d2dcOnlyProbe = camera.deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true
                        if (!d2dcOnlyProbe && camera.supportsRemoteTouch()) {
'''
if text.count(old) != 1:
    raise SystemExit(f"remote-touch branch marker count={text.count(old)}")
text = text.replace(old, new, 1)

marker = '''                        val s1StartedMs = System.currentTimeMillis()
'''
insert = '''                        if (d2dcOnlyProbe) {
                            // Controlled experiment: do not synthesize a focus result and do
                            // not press S1.  The only camera-side action is D2DC, so the user's
                            // observation of the real body AF frame tells us whether the ~0.5 s
                            // delay belongs to AF-area relocation or to the focus/touch action.
                            synchronized(afStateLock) {
                                pendingAfFrameLatency = PendingAfFrameLatency(
                                    generation = generation,
                                    x = safeX,
                                    y = safeY,
                                    requestedAtMs = requestedAtMs,
                                    ackAtMs = ackAtMs,
                                    commandDoneAtMs = ackAtMs,
                                    path = "D2DC-only",
                                    s1Ms = null,
                                    baseline = baseline,
                                    prepDebug = prepDebug
                                )
                            }
                            val message = "AF D2DC ONLY x=$safeX y=$safeY\\n" +
                                "$prepDebug\\n" +
                                "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${move.queueWaitMs}ms " +
                                "wire+ack=${wireAndAckMs}ms ack=${ackMs}ms NO-S1"
                            Log.d(TAG, message.replace('\\n', ' '))
                            _events.emit(CameraEvent.FocusDebug(message))
                            return@withLock CameraOperationResult.SuccessWithData(message)
                        }

                        val s1StartedMs = System.currentTimeMillis()
'''
if text.count(marker) != 1:
    raise SystemExit(f"S1 marker count={text.count(marker)}")
text = text.replace(marker, insert, 1)

if text == original:
    raise SystemExit("no changes")
path.write_text(text)
