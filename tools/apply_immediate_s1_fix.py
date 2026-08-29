from pathlib import Path

path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt')
text = path.read_text()

old_decl = '''    private var afReleaseJob: Job? = null
    private var afTriggerJob: Job? = null
    private var afGeneration = 0L
'''
new_decl = '''    private var afReleaseJob: Job? = null
    private var afGeneration = 0L
'''
if old_decl not in text:
    raise SystemExit('AF job declaration block not found')
text = text.replace(old_decl, new_decl, 1)

start = text.index('    private fun scheduleAutofocusAfterAreaMove(camera: SonyPtpCamera, generation: Long) {')
end = text.index('\n    private fun beginControlWrite(): Long', start)
new_release = '''    private fun scheduleAutofocusRelease(camera: SonyPtpCamera, generation: Long) {
        afReleaseJob?.cancel()
        afReleaseJob = scope.launch(Dispatchers.IO) {
            // Keep S1 held long enough for the body to complete normal AF, but
            // never make the caller wait for release. A newer tap cancels this
            // job and explicitly releases the old S1 before moving its point.
            delay(280)
            priorityControlIntents.incrementAndGet()
            try {
                controlWriteMutex.withLock {
                    if (generation != afGeneration || ptpCamera !== camera || !afHalfPressHeld) {
                        return@withLock
                    }
                    val releaseEpoch = beginControlWrite()
                    try {
                        camera.setAutofocusPressed(false)
                        afHalfPressHeld = false
                    } catch (e: Exception) {
                        Log.w(TAG, "AF half-press release failed: ${e.message}")
                    } finally {
                        endControlWrite(releaseEpoch)
                    }
                }
            } finally {
                priorityControlIntents.decrementAndGet()
            }
        }
    }
'''
text = text[:start] + new_release + text[end:]

text = text.replace('                        afTriggerJob?.cancel()\n                        afReleaseJob?.cancel()\n', '                        afReleaseJob?.cancel()\n', 1)

old_tail = '''                        synchronized(afStateLock) {
                            pendingAfFrameLatency = PendingAfFrameLatency(
                                generation = generation,
                                x = safeX,
                                y = safeY,
                                requestedAtMs = requestedAtMs,
                                ackAtMs = ackAtMs
                            )
                        }

                        val message = "AF MOVE | $prepDebug | x=$safeX y=$safeY | D2DC=OK | " +
                            "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${move.queueWaitMs}ms " +
                            "wire+ack=${wireAndAckMs}ms ack=${ackMs}ms | waiting FRAME"
                        Log.d(TAG, message)
                        _events.emit(CameraEvent.FocusDebug(message))
                        _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))

                        // AF is deliberately a second operation. The 90ms gap lets
                        // the moved Spot-S frame reach Live View first on responsive
                        // bodies while still starting autofocus promptly.
                        scheduleAutofocusAfterAreaMove(camera, generation)
                        CameraOperationResult.SuccessWithData(message)
'''
new_tail = '''                        synchronized(afStateLock) {
                            pendingAfFrameLatency = PendingAfFrameLatency(
                                generation = generation,
                                x = safeX,
                                y = safeY,
                                requestedAtMs = requestedAtMs,
                                ackAtMs = ackAtMs
                            )
                        }

                        // Do not insert a frame delay between moving the AF area and
                        // S1. The previous 90 ms delay imposed an artificial >100 ms
                        // floor on the first camera-returned focus frame. Keep D2DC
                        // and S1 in the same high-priority control lane so Live View
                        // cannot slip another PTP transaction between them.
                        val s1StartedMs = System.currentTimeMillis()
                        val pressResult = camera.setAutofocusPressed(true)
                        val s1AckAtMs = System.currentTimeMillis()
                        val s1Ms = s1AckAtMs - s1StartedMs
                        afHalfPressHeld = pressResult.isSuccess

                        val message = "AF MOVE+S1 | $prepDebug | x=$safeX y=$safeY | D2DC=OK | " +
                            "S1=${PtpConstants.responseCodeName(pressResult.responseCode)} | " +
                            "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${move.queueWaitMs}ms " +
                            "wire+ack=${wireAndAckMs}ms ack=${ackMs}ms s1=${s1Ms}ms | waiting FRAME"
                        Log.d(TAG, message)
                        _events.emit(CameraEvent.FocusDebug(message))
                        _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))

                        if (!pressResult.isSuccess) {
                            return@withLock CameraOperationResult.Failure(message)
                        }
                        scheduleAutofocusRelease(camera, generation)
                        CameraOperationResult.SuccessWithData(message)
'''
if old_tail not in text:
    raise SystemExit('setAfPoint tail block not found')
text = text.replace(old_tail, new_tail, 1)

# Cleanup any remaining trigger-job cancellation in teardown from the prior design.
text = text.replace('        afTriggerJob?.cancel()\n', '')

path.write_text(text)
Path(__file__).unlink()
