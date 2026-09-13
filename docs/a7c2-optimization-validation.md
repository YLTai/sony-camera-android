# A7C II latency and USB reliability validation

## Scope and limitations

This change targets host-side defects found in the source. No ILCE-7CM2, lens, phone, or USB capture hardware was attached to the development session. There is **no measured before/after camera latency, connection-success rate, FPS improvement, or Monitor+ comparison**. Keep the PR in draft until the checks below have been performed on a real camera.

The existing Sony priority-mode handshake and the D2E4 wire encoding are deliberately unchanged. This is not a new reverse-engineered protocol or an attempt to emulate Monitor+.

## What changed

### Dispatch focus at successful release, not after double-tap arbitration

`PreviewPane` previously supplied both `onTap` and `onDoubleTap` to `detectTapGestures`. Single-tap autofocus therefore waited for double-tap recognition to expire before it even entered `requestAf`. The old AF command timer started after this wait and could not diagnose it.

The focus action now runs from `onPress` after `tryAwaitRelease()` returns true. A cancelled gesture does not focus, and a pointer held down does not start AF prematurely. Coordinate mapping and double-tap magnification are retained. **UX tradeoff: a double tap can also focus at the tapped location.** Immediate single-tap focus cannot also retroactively suppress the first tap when a second tap arrives. Actual gesture recognition and zoom behavior still require device testing; the JVM tests exercise the extracted release helper, not a full Compose pointer-input instrumentation test.

### Remove diagnostic A/B behavior from ordinary taps

The ILCE-7CM2 path alternated D2DC-only and D2E4 between taps. D2DC-only merely moved the AF area and intentionally omitted S1; it was not equivalent to remote-touch AF. The normal path now consistently uses prepared Remote Touch when the camera reports it available, with D2DC+S1 as the compatibility fallback. Busy, timeout, and general errors do not automatically dispatch a second, different AF operation: a timed-out command may still execute on the camera.

Queued requests are rejected after a camera-session change. AF bookkeeping is cleared on cancellation, control priority counters remain balanced across disconnect, and debug events no longer suspend a control transaction when an event subscriber is slow. Dropping a debug event under backpressure is preferable to holding the camera control lock.

### Serialize connection attempts and teardown

A lifecycle mutex covers the entire connection attempt and rollback. Reconnection waits for the previous teardown to actually finish, rather than proceeding after an arbitrary 1.8-second join timeout. Duplicate attach/connect requests do not restart an active handshake. Late permission responses are ignored after disconnect; the USB permission is checked again before connecting. Cooperative cancellation checkpoints prevent a cancelled, blocking handshake from publishing Ready. Failed initialization releases any acquired Sony session/priority, claimed interface, and connection. Teardown waits for live view and serialized AF controls before closing the old session.

USB I/O remains synchronous: cancellation is observed between bounded I/O operations, not by interrupting an in-flight USB transfer. A slow teardown can intentionally delay reconnection rather than corrupt a new session. Elapsed-time measurements and watchdog intervals now use a monotonic clock.

### Parse PTP containers independently of USB transfers

The shared reader preserves fragmented headers/payloads and a response coalesced after a data container. It matches transaction IDs in normal and short-timeout paths, drains late containers without treating them as the current result, and rejects partial payloads and malformed container sizes. Short positive writes are errors, not successful command transmission.

Reads share one monotonic input budget, including stale data and ACK handling; this is **not** an end-to-end bound on queue time plus OUT transfers plus input. Zero is never sent as a computed USB timeout, since Android interprets zero as infinite. The reader reuses a 16 KiB USB buffer, compatible with API 26/27 transfer-size limits, and caps data-container allocation at 128 MiB. Recovery drains stop on the first empty/error transfer and have time/count bounds. Hardware validation should include the throughput impact of the smaller transfer buffer and the total read deadline.

## Automated verification

Run with JDK 17 and Android SDK 35:

```sh
./gradlew :sonycamera:testDebugUnitTest :demo:testDebugUnitTest :demo:assembleDebug --stacktrace
```

The PR adds 22 JVM tests:

- 11 reader/deadline cases: split headers, split payloads, continuation after timeout, coalesced ACKs, invalid sizes, invalid parameter alignment, zero-length packets, failed reads, reset, timeout rounding, and rejection of infinite timeout.
- 8 mocked transport cases: stale response/data handling, transaction matching, coalesced response, truncated and late data, positive short writes, no data phase after a short command, and bounded empty-pipe flushing.
- 3 release-helper cases: immediate dispatch on successful release without advancing the double-tap clock, cancelled press, and disposed gesture.

CI runs both test suites and packages a debug APK. The workflow retains test reports and the matching source archive. Passing JVM tests do not validate a real USB device, Android permission broadcasts, reconnection timing, or camera focusing behavior. Refer to the PR's latest CI result for executed test/build status.

## Hardware acceptance matrix (not yet executed)

| Check | Procedure | Record / acceptance criterion |
| --- | --- | --- |
| Cold connect | 20 trials from camera PC Remote mode; vary app-first/camera-first startup | Success count and time to first valid frame; no duplicate handshake |
| Rapid retry | Repeated Connect, disconnect during OpenSession and vendor init, then reconnect | No stale Ready, leaked interface, or CloseSession arriving in the new session |
| Permission | Allow, deny, unplug while dialog is open, then allow an old dialog | No connection resurrected after explicit disconnect; a fresh request can succeed |
| Cable interruption | Brief replug and replug after the grace window, including a USB hub | Correct state transitions, first-frame time, no two active sessions |
| AF | At least 30 alternating near/far taps; verify every tap actually focuses | Median and p95 for tap-to-ACK and tap-to-visible-focus; verify D2E4/fallback path |
| Gesture | Single tap, double tap, drag, long press, navigation/disposal while pressed | No double-tap wait for focus; zoom retained; cancelled gestures do not focus |
| Stress | Rapid taps while changing exposure, then disconnect during held S1 | Latest queued tap wins; S1 releases; reconnect remains usable |
| Preview/capture | 5-minute live view, tools/LUT on and off, full-resolution JPEG capture | FPS, stalls, memory, CPU, and successful post-capture preview resume |
| Compatibility | USB 2 / USB 3 paths and API 26/27 when available | No new timeouts from the input budget; acceptable frame throughput |

For a fair Monitor+ comparison use the same body, firmware, lens, focus mode, lighting, scene, phone, cable and connection type. Record with an external high-frame-rate camera and report both median and p95. Do not substitute command ACK, a drawn focus rectangle, or JVM scheduler time for optical focus completion. Record which debug timestamp starts at gesture release versus after dispatch, and retain the raw observations rather than claiming a fixed latency reduction from source inspection alone.

## References

- Android USB transfer contract: https://developer.android.com/reference/android/hardware/usb/UsbDeviceConnection
- Cooperative cancellation checkpoints: https://kotlinlang.org/api/kotlinx.coroutines/kotlinx-coroutines-core/kotlinx.coroutines/ensure-active.html
- AndroidX tap detector implementation: https://android.googlesource.com/platform/frameworks/support/+/androidx-main/compose/foundation/foundation/src/commonMain/kotlin/androidx/compose/foundation/gestures/TapGestureDetector.kt
