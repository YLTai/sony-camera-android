from pathlib import Path

path = Path("demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt")
text = path.read_text()
original = text


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, found {count}")
    text = text.replace(old, new, 1)

replace_once(
    "import android.graphics.Bitmap\n",
    "import android.graphics.Bitmap\nimport android.os.SystemClock\n",
    "SystemClock import",
)
replace_once(
    "import androidx.compose.runtime.setValue\n",
    "import androidx.compose.runtime.setValue\nimport androidx.compose.runtime.withFrameNanos\n",
    "withFrameNanos import",
)

replace_once(
    '''            var afBusy by remember { mutableStateOf(false) }\n            var queuedAfPoint by remember { mutableStateOf<Pair<Int, Int>?>(null) }\n            var afRequestJob by remember { mutableStateOf<Job?>(null) }\n''',
    '''            var afBusy by remember { mutableStateOf(false) }\n            var queuedAfPoint by remember { mutableStateOf<Pair<Int, Int>?>(null) }\n            var afRequestJob by remember { mutableStateOf<Job?>(null) }\n            // UI-side latency probe. This starts at the actual pointer tap, not\n            // inside the USB manager, so it captures main-thread/event/recompose\n            // delay that the existing PTP ack/metadata timers cannot see.\n            var afTapStartedAtMs by remember { mutableStateOf<Long?>(null) }\n            var afTapTarget by remember { mutableStateOf<Pair<Int, Int>?>(null) }\n            var afUiEventLatencyMs by remember { mutableStateOf<Long?>(null) }\n            var afUiFrameLatencyMs by remember { mutableStateOf<Long?>(null) }\n''',
    "AF UI latency state",
)

replace_once(
    '''                        is CameraEvent.FocusFramesUpdated -> {\n                            focusFrames = event.info.frames\n                            focusPoint = preferredFocusPivot(event.info.frames)\n                        }\n                        is CameraEvent.ExposureUpdated -> exposure = event.state\n                        is CameraEvent.CameraSettingsUpdated -> cameraSettings = event.state\n                        is CameraEvent.FocusDebug -> focusDebug = event.message\n''',
    '''                        is CameraEvent.FocusFramesUpdated -> {\n                            focusFrames = event.info.frames\n                            focusPoint = preferredFocusPivot(event.info.frames)\n\n                            val tapAt = afTapStartedAtMs\n                            val target = afTapTarget\n                            if (tapAt != null && target != null && afUiEventLatencyMs == null) {\n                                val targetX = target.first / 639f\n                                val targetY = target.second / 479f\n                                val nearestErrorPx = event.info.frames.minOfOrNull { frame ->\n                                    maxOf(\n                                        kotlin.math.abs(frame.centerXNormalized - targetX) * 639f,\n                                        kotlin.math.abs(frame.centerYNormalized - targetY) * 479f\n                                    )\n                                }\n                                if (nearestErrorPx != null && nearestErrorPx <= 12f) {\n                                    val eventMs = SystemClock.elapsedRealtime() - tapAt\n                                    afUiEventLatencyMs = eventMs\n                                    focusDebug = focusDebug?.substringBefore("\\nuiEvent=")?.let {\n                                        "$it\\nuiEvent=${eventMs}ms uiFrame=pending"\n                                    }\n\n                                    // Wait for the next Compose frame clock after the camera-returned\n                                    // focus geometry has been assigned to UI state. This is the closest\n                                    // objective phone-side measure of when that real frame can appear.\n                                    withFrameNanos { }\n                                    val frameMs = SystemClock.elapsedRealtime() - tapAt\n                                    afUiFrameLatencyMs = frameMs\n                                    focusDebug = focusDebug?.substringBefore("\\nuiEvent=")?.let {\n                                        "$it\\nuiEvent=${eventMs}ms uiFrame=${frameMs}ms"\n                                    }\n                                }\n                            }\n                        }\n                        is CameraEvent.ExposureUpdated -> exposure = event.state\n                        is CameraEvent.CameraSettingsUpdated -> cameraSettings = event.state\n                        is CameraEvent.FocusDebug -> {\n                            val uiLine = when {\n                                afUiEventLatencyMs != null -> {\n                                    val frameText = afUiFrameLatencyMs?.let { "${it}ms" } ?: "pending"\n                                    "\\nuiEvent=${afUiEventLatencyMs}ms uiFrame=$frameText"\n                                }\n                                afTapStartedAtMs != null -> "\\nuiEvent=pending uiFrame=pending"\n                                else -> ""\n                            }\n                            focusDebug = event.message + uiLine\n                        }\n''',
    "focus event UI timing",
)

replace_once(
    '''                    queuedAfPoint = null\n                    afRequestJob?.cancel()\n                    afRequestJob = null\n                    afBusy = false\n''',
    '''                    queuedAfPoint = null\n                    afRequestJob?.cancel()\n                    afRequestJob = null\n                    afBusy = false\n                    afTapStartedAtMs = null\n                    afTapTarget = null\n                    afUiEventLatencyMs = null\n                    afUiFrameLatencyMs = null\n''',
    "reset UI timing state",
)

replace_once(
    '''                // Keep at most one latest target while a USB control is in flight.\n                // We intentionally do NOT draw an optimistic focus frame here: the\n                // monitor now reflects only the camera's returned FocalFrameInfo.\n                focusPoint = Offset(targetX / 639f, targetY / 479f)\n''',
    '''                // Timestamp the pointer-side request before any coroutine / service /\n                // USB dispatch. This lets debug compare real tap-to-phone-display latency\n                // with the manager's command/metadata latency.\n                afTapStartedAtMs = SystemClock.elapsedRealtime()\n                afTapTarget = targetX to targetY\n                afUiEventLatencyMs = null\n                afUiFrameLatencyMs = null\n\n                // Keep at most one latest target while a USB control is in flight.\n                // We intentionally do NOT draw an optimistic focus frame here: the\n                // monitor now reflects only the camera's returned FocalFrameInfo.\n                focusPoint = Offset(targetX / 639f, targetY / 479f)\n''',
    "pointer tap timestamp",
)

if text == original:
    raise SystemExit("no changes")
path.write_text(text)
