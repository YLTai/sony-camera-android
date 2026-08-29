from pathlib import Path

CAMERA = Path("demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt")


def replace_once(old: str, new: str) -> None:
    text = CAMERA.read_text()
    if old not in text:
        raise RuntimeError(f"Expected block not found: {old[:160]!r}")
    CAMERA.write_text(text.replace(old, new, 1))


# Latest-wins UI jobs prevent throttled live dial writes from accumulating behind
# one another if the camera needs longer than one sampling interval to respond.
replace_once(
    "import kotlinx.coroutines.Dispatchers\nimport kotlinx.coroutines.delay\n",
    "import kotlinx.coroutines.Dispatchers\nimport kotlinx.coroutines.Job\nimport kotlinx.coroutines.delay\n",
)

replace_once(
    """            val context = LocalContext.current\n            val scope = rememberCoroutineScope()\n            val state by camera.connectionState.collectAsStateWithLifecycle()\n""",
    """            val context = LocalContext.current\n            val scope = rememberCoroutineScope()\n            val exposureWriteJobs = remember { mutableMapOf<CameraExposureSetting, Job>() }\n            val settingWriteJobs = remember { mutableMapOf<CameraSetting, Job>() }\n            val state by camera.connectionState.collectAsStateWithLifecycle()\n""",
)

replace_once(
    """            fun setExposure(setting: CameraExposureSetting, raw: Long) {\n                scope.launch {\n                    val result = camera.setExposure(setting, raw)\n                    if (result is CameraOperationResult.Failure) lastError = result.message\n                }\n            }\n\n            fun setCameraSetting(setting: CameraSetting, raw: Long) {\n                scope.launch {\n                    val result = camera.setCameraSetting(setting, raw)\n                    if (result is CameraOperationResult.Failure) lastError = result.message\n                }\n            }\n""",
    """            fun setExposure(setting: CameraExposureSetting, raw: Long) {\n                exposureWriteJobs[setting]?.cancel()\n                exposureWriteJobs[setting] = scope.launch {\n                    val result = camera.setExposure(setting, raw)\n                    if (result is CameraOperationResult.Failure) lastError = result.message\n                }\n            }\n\n            fun setCameraSetting(setting: CameraSetting, raw: Long) {\n                settingWriteJobs[setting]?.cancel()\n                settingWriteJobs[setting] = scope.launch {\n                    val result = camera.setCameraSetting(setting, raw)\n                    if (result is CameraOperationResult.Failure) lastError = result.message\n                }\n            }\n""",
)

text = CAMERA.read_text()
start_marker = "@Composable\nprivate fun DialSelectorPanel("
end_marker = "\n@Composable\nprivate fun OptionSelectorPanel("
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise RuntimeError("DialSelectorPanel markers not found")

new_dial = r'''@Composable
private fun DialSelectorPanel(
    title: String,
    currentRaw: Long?,
    options: List<SelectorOption>,
    writable: Boolean,
    minimumLabel: String? = null,
    maximumLabel: String? = null,
    onSelect: (Long) -> Unit,
    modifier: Modifier = Modifier
) {
    val initialIndex = options.indexOfFirst { it.rawValue == currentRaw }.let { if (it >= 0) it else 0 }
    var dialPosition by remember(title, options) { mutableStateOf(initialIndex.toFloat()) }
    var dragging by remember(title) { mutableStateOf(false) }
    var pendingRaw by remember(title) { mutableStateOf<Long?>(null) }
    var liveTargetRaw by remember(title) { mutableStateOf<Long?>(null) }
    var lastStreamedRaw by remember(title) { mutableStateOf<Long?>(null) }
    val latestCurrentRaw = rememberUpdatedState(currentRaw)
    val latestOnSelect = rememberUpdatedState(onSelect)

    LaunchedEffect(currentRaw, options, dragging) {
        if (!dragging) {
            if (pendingRaw == currentRaw) pendingRaw = null
            if (pendingRaw == null) {
                val index = options.indexOfFirst { it.rawValue == currentRaw }
                if (index >= 0) dialPosition = index.toFloat()
            }
        }
    }

    // During a drag we do send camera commands so a small adjustment can be
    // judged on the live image. Sampling is deliberately conflated: every
    // 150 ms we send only the newest detent, never all detents crossed since
    // the previous sample. The caller also cancels any older queued UI write.
    LaunchedEffect(dragging, options) {
        if (!dragging || options.isEmpty()) return@LaunchedEffect
        while (true) {
            delay(150)
            val target = liveTargetRaw ?: continue
            if (target != lastStreamedRaw && target != latestCurrentRaw.value) {
                lastStreamedRaw = target
                pendingRaw = target
                latestOnSelect.value(target)
            }
        }
    }

    // If a command is rejected or the camera never echoes the selected value,
    // eventually return to the authoritative camera state. Stale poll results
    // are still ignored while pending, so an old snapshot cannot pull the dial
    // backwards immediately after the user's gesture.
    LaunchedEffect(pendingRaw) {
        val pending = pendingRaw ?: return@LaunchedEffect
        delay(3_000)
        if (pendingRaw == pending && latestCurrentRaw.value != pending) {
            pendingRaw = null
            val index = options.indexOfFirst { it.rawValue == latestCurrentRaw.value }
            if (index >= 0) dialPosition = index.toFloat()
        }
    }

    Surface(
        modifier = modifier.fillMaxWidth(0.72f).widthIn(max = 690.dp),
        color = SonyPanel,
        shape = RoundedCornerShape(3.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, Color.White.copy(alpha = 0.24f)),
        shadowElevation = 8.dp
    ) {
        Column(Modifier.padding(horizontal = 11.dp, vertical = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, color = Color.White, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.width(10.dp))
                Text(
                    if (writable) "TURN / SWIPE DIAL" else "DISPLAY ONLY",
                    color = if (writable) Color.White.copy(alpha = 0.44f) else Accent,
                    fontSize = 7.sp,
                    fontWeight = FontWeight.Bold
                )
                if (minimumLabel != null || maximumLabel != null) {
                    Spacer(Modifier.width(12.dp))
                    Text(
                        listOfNotNull(
                            minimumLabel?.let { "MIN  $it" },
                            maximumLabel?.let { "MAX  $it" }
                        ).joinToString("     "),
                        color = Color.White.copy(alpha = 0.68f),
                        fontSize = 8.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }
            Spacer(Modifier.height(5.dp))

            if (options.isEmpty()) {
                Text("No adjustable steps reported by camera", color = Color.White.copy(alpha = 0.55f), fontSize = 11.sp)
            } else {
                val selectedIndex = (dialPosition + 0.5f).toInt().coerceIn(0, options.lastIndex)
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(78.dp)
                        .background(Color.Black.copy(alpha = 0.28f), RoundedCornerShape(2.dp))
                        .clipToBounds()
                        .pointerInput(options, writable) {
                            if (!writable || options.size < 2) return@pointerInput
                            val dragStep = 72.dp.toPx()
                            detectHorizontalDragGestures(
                                onDragStart = {
                                    dragging = true
                                    val index = (dialPosition + 0.5f).toInt().coerceIn(0, options.lastIndex)
                                    liveTargetRaw = options[index].rawValue
                                    lastStreamedRaw = latestCurrentRaw.value
                                },
                                onHorizontalDrag = { change, dragAmount ->
                                    change.consume()
                                    dialPosition = (dialPosition - dragAmount / dragStep)
                                        .coerceIn(0f, options.lastIndex.toFloat())
                                    val index = (dialPosition + 0.5f).toInt().coerceIn(0, options.lastIndex)
                                    val target = options[index].rawValue
                                    liveTargetRaw = target
                                    pendingRaw = if (target == latestCurrentRaw.value) null else target
                                },
                                onDragEnd = {
                                    val finalIndex = (dialPosition + 0.5f).toInt().coerceIn(0, options.lastIndex)
                                    val finalRaw = options[finalIndex].rawValue
                                    dialPosition = finalIndex.toFloat()
                                    dragging = false
                                    liveTargetRaw = null
                                    if (finalRaw != latestCurrentRaw.value) {
                                        pendingRaw = finalRaw
                                        if (finalRaw != lastStreamedRaw) {
                                            lastStreamedRaw = finalRaw
                                            latestOnSelect.value(finalRaw)
                                        }
                                    } else {
                                        pendingRaw = null
                                    }
                                },
                                onDragCancel = {
                                    dragging = false
                                    liveTargetRaw = null
                                    pendingRaw = null
                                    val index = options.indexOfFirst { it.rawValue == latestCurrentRaw.value }
                                    if (index >= 0) dialPosition = index.toFloat()
                                }
                            )
                        },
                    contentAlignment = Alignment.Center
                ) {
                    // The scale is tied to the same continuous dialPosition as
                    // the labels. Two minor ticks equal one camera detent, so
                    // both labels and tick marks roll under the fixed red index.
                    Canvas(Modifier.fillMaxSize()) {
                        val center = size.width / 2f
                        val minorSpacing = 44.dp.toPx()
                        val absoluteTick = dialPosition * 2f
                        val baseTick = absoluteTick.toInt()
                        val bottom = size.height
                        for (tick in (baseTick - 12)..(baseTick + 12)) {
                            val x = center + (tick - absoluteTick) * minorSpacing
                            if (x < 0f || x > size.width) continue
                            val major = tick % 2 == 0
                            val h = if (major) 15.dp.toPx() else 9.dp.toPx()
                            drawLine(
                                color = Color.White.copy(alpha = if (major) 0.34f else 0.18f),
                                start = Offset(x, bottom - h),
                                end = Offset(x, bottom),
                                strokeWidth = 1.dp.toPx()
                            )
                        }
                        drawLine(
                            color = Accent,
                            start = Offset(center, 0f),
                            end = Offset(center, bottom),
                            strokeWidth = 2.dp.toPx()
                        )
                    }

                    // Position each label from the continuous dial coordinate
                    // rather than snapping a five-item Row at every detent.
                    // This removes the strong magnetic feel during slow drags.
                    for (index in (selectedIndex - 3)..(selectedIndex + 3)) {
                        val option = options.getOrNull(index) ?: continue
                        val distance = index.toFloat() - dialPosition
                        val absDistance = kotlin.math.abs(distance)
                        if (absDistance > 3.2f) continue
                        val isCenter = absDistance < 0.5f
                        Text(
                            text = option.label,
                            color = if (isCenter) Color.White else Color.White.copy(
                                alpha = when {
                                    absDistance < 1.35f -> 0.50f
                                    absDistance < 2.35f -> 0.24f
                                    else -> 0.12f
                                }
                            ),
                            fontSize = if (isCenter) 20.sp else if (absDistance < 1.35f) 11.sp else 9.sp,
                            lineHeight = if (isCenter) 23.sp else 13.sp,
                            fontWeight = if (isCenter) FontWeight.Bold else FontWeight.Medium,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            textAlign = TextAlign.Center,
                            modifier = Modifier
                                .align(Alignment.Center)
                                .width(82.dp)
                                .graphicsLayer { translationX = distance * 88.dp.toPx() }
                        )
                    }
                }
            }
        }
    }
}
'''

CAMERA.write_text(text[:start] + new_dial + text[end:])

# One-shot patch file; the workflow commits the source change and deletion together.
Path(__file__).unlink()
print("Applied live/conflated continuous dial interaction patch")
