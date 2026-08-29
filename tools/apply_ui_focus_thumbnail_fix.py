from pathlib import Path

path = Path('demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt')
text = path.read_text()

def replace_once(old: str, new: str):
    global text
    if text.count(old) != 1:
        raise SystemExit(f'expected exactly one match, found {text.count(old)}: {old[:120]!r}')
    text = text.replace(old, new, 1)

replace_once(
'''            var cameraSettings by remember { mutableStateOf<CameraSettingsState?>(null) }
            var afBusy by remember { mutableStateOf(false) }

            var menusVisible by remember { mutableStateOf(true) }
''',
'''            var cameraSettings by remember { mutableStateOf<CameraSettingsState?>(null) }
            var afBusy by remember { mutableStateOf(false) }
            var queuedAfPoint by remember { mutableStateOf<Pair<Int, Int>?>(null) }
            var optimisticAfPoint by remember { mutableStateOf<Offset?>(null) }
            var afRequestJob by remember { mutableStateOf<Job?>(null) }

            var menusVisible by remember { mutableStateOf(true) }
''')

replace_once(
'''                        is CameraEvent.FocusFramesUpdated -> {
                            focusFrames = event.info.frames
                            focusPoint = preferredFocusPivot(event.info.frames)
                        }
''',
'''                        is CameraEvent.FocusFramesUpdated -> {
                            focusFrames = event.info.frames
                            // Do not let a delayed camera event pull the marker back to
                            // the previous AF point while a newer tap is being shown.
                            if (optimisticAfPoint == null) {
                                focusPoint = preferredFocusPivot(event.info.frames)
                            }
                        }
''')

replace_once(
'''                    magnification = 1f
                    magnifyPivot = Offset(0.5f, 0.5f)
                    focusPoint = Offset(0.5f, 0.5f)
                }
''',
'''                    magnification = 1f
                    magnifyPivot = Offset(0.5f, 0.5f)
                    focusPoint = Offset(0.5f, 0.5f)
                    queuedAfPoint = null
                    optimisticAfPoint = null
                    afRequestJob?.cancel()
                    afRequestJob = null
                    afBusy = false
                }
''')

replace_once(
'''            fun requestAf(x: Int, y: Int) {
                if (afBusy || state !is CameraConnectionState.Ready) return
                focusPoint = Offset(x.coerceIn(0, 639) / 639f, y.coerceIn(0, 479) / 479f)
                afBusy = true
                scope.launch {
                    val result = camera.setAfPoint(x.coerceIn(0, 639), y.coerceIn(0, 479))
                    if (result is CameraOperationResult.Failure) lastError = result.message
                    afBusy = false
                }
            }
''',
'''            fun requestAf(x: Int, y: Int) {
                if (state !is CameraConnectionState.Ready) return
                val targetX = x.coerceIn(0, 639)
                val targetY = y.coerceIn(0, 479)
                val point = Offset(targetX / 639f, targetY / 479f)

                // Give immediate visual feedback. USB remains strictly serialized:
                // while one setAfPoint is in flight, additional taps only replace
                // this single queued target instead of starting concurrent writes.
                focusPoint = point
                optimisticAfPoint = point
                queuedAfPoint = targetX to targetY
                scope.launch {
                    delay(900)
                    if (optimisticAfPoint == point) optimisticAfPoint = null
                }

                if (afRequestJob?.isActive == true) return
                afRequestJob = scope.launch {
                    while (true) {
                        val target = queuedAfPoint ?: break
                        queuedAfPoint = null
                        afBusy = true
                        val result = camera.setAfPoint(target.first, target.second)
                        if (result is CameraOperationResult.Failure) lastError = result.message
                    }
                    afBusy = false
                }
            }
''')

replace_once(
'''                    magnifyPivot = magnifyPivot,
                    afBusy = afBusy,
                    onAfPoint = ::requestAf,
''',
'''                    magnifyPivot = magnifyPivot,
                    afBusy = afBusy,
                    optimisticAfPoint = optimisticAfPoint,
                    onAfPoint = ::requestAf,
''')

replace_once(
'''    magnifyPivot: Offset,
    afBusy: Boolean,
    onAfPoint: (Int, Int) -> Unit,
''',
'''    magnifyPivot: Offset,
    afBusy: Boolean,
    optimisticAfPoint: Offset?,
    onAfPoint: (Int, Int) -> Unit,
''')

replace_once(
'''            .pointerInput(state, source?.width, source?.height, containerSize, afBusy, magnification) {
''',
'''            .pointerInput(state, source?.width, source?.height, containerSize, magnification) {
''')

replace_once(
'''                    onTap = { tap ->
                        if (afBusy) return@detectTapGestures
                        val mapped = mapTapToImage(
''',
'''                    onTap = { tap ->
                        val mapped = mapTapToImage(
''')

replace_once(
'''                FocusAreaSelectionOverlay(source, containerSize, focusAreaRaw, focusFrames, Modifier.fillMaxSize())
                if (focusFrames.isNotEmpty() && containerSize != IntSize.Zero) {
''',
'''                FocusAreaSelectionOverlay(source, containerSize, focusAreaRaw, focusFrames, Modifier.fillMaxSize())
                optimisticAfPoint?.let { point ->
                    OptimisticFocusPointOverlay(source, containerSize, point, Modifier.fillMaxSize())
                }
                if (focusFrames.isNotEmpty() && containerSize != IntSize.Zero) {
''')

replace_once(
'''                MagnificationThumbnail(source, magnification, magnifyPivot,
                    Modifier.align(Alignment.TopEnd).padding(top = 42.dp, end = 10.dp))
''',
'''                MagnificationThumbnail(source, magnification, magnifyPivot, containerSize,
                    Modifier.align(Alignment.TopEnd).padding(top = 42.dp, end = 10.dp))
''')

replace_once(
'''@Composable
private fun MagnificationThumbnail(source: Bitmap, zoom: Float, pivot: Offset, modifier: Modifier = Modifier) {
    var size by remember { mutableStateOf(IntSize.Zero) }
    val vw = 1f / zoom.coerceAtLeast(1f)
    val left = (pivot.x - vw / 2f).coerceIn(0f, 1f - vw)
    val top = (pivot.y - vw / 2f).coerceIn(0f, 1f - vw)
    Box(modifier.width(144.dp).height(96.dp).background(Color.Black.copy(alpha = 0.72f))
        .border(1.dp, Color.White.copy(alpha = 0.48f)).clipToBounds().onSizeChanged { size = it }) {
        Image(source.asImageBitmap(), "Magnification overview", Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
        Canvas(Modifier.fillMaxSize()) {
            val r = fittedImageRect(size, source.width, source.height)
            drawRect(Accent, Offset(r.left + r.width * left, r.top + r.height * top),
                androidx.compose.ui.geometry.Size(r.width * vw, r.height * vw),
                style = androidx.compose.ui.graphics.drawscope.Stroke(width = 1.5.dp.toPx()))
        }
    }
}
''',
'''private fun magnifyVisibleImageRect(
    container: IntSize,
    imageRect: Rect,
    zoom: Float,
    pivot: Offset
): Rect {
    if (container == IntSize.Zero || imageRect.width <= 0f || imageRect.height <= 0f) {
        return Rect(0f, 0f, 1f, 1f)
    }
    val safeZoom = zoom.coerceAtLeast(1f)
    if (safeZoom <= 1f) return Rect(0f, 0f, 1f, 1f)

    val translation = magnifyTranslation(container, imageRect, safeZoom, pivot)
    val cx = container.width / 2f
    val cy = container.height / 2f
    fun imageX(screenX: Float): Float =
        ((cx + (screenX - translation.x - cx) / safeZoom - imageRect.left) / imageRect.width)
            .coerceIn(0f, 1f)
    fun imageY(screenY: Float): Float =
        ((cy + (screenY - translation.y - cy) / safeZoom - imageRect.top) / imageRect.height)
            .coerceIn(0f, 1f)

    return Rect(
        imageX(0f),
        imageY(0f),
        imageX(container.width.toFloat()),
        imageY(container.height.toFloat())
    )
}

@Composable
private fun MagnificationThumbnail(
    source: Bitmap,
    zoom: Float,
    pivot: Offset,
    previewContainer: IntSize,
    modifier: Modifier = Modifier
) {
    var size by remember { mutableStateOf(IntSize.Zero) }
    val previewImageRect = fittedImageRect(previewContainer, source.width, source.height)
    val viewport = magnifyVisibleImageRect(previewContainer, previewImageRect, zoom, pivot)
    Box(modifier.width(144.dp).height(96.dp).background(Color.Black.copy(alpha = 0.72f))
        .border(1.dp, Color.White.copy(alpha = 0.48f)).clipToBounds().onSizeChanged { size = it }) {
        Image(source.asImageBitmap(), "Magnification overview", Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
        Canvas(Modifier.fillMaxSize()) {
            val r = fittedImageRect(size, source.width, source.height)
            drawRect(
                Accent,
                Offset(r.left + r.width * viewport.left, r.top + r.height * viewport.top),
                androidx.compose.ui.geometry.Size(r.width * viewport.width, r.height * viewport.height),
                style = androidx.compose.ui.graphics.drawscope.Stroke(width = 1.5.dp.toPx())
            )
        }
    }
}

@Composable
private fun OptimisticFocusPointOverlay(
    bitmap: Bitmap,
    containerSize: IntSize,
    point: Offset,
    modifier: Modifier = Modifier
) {
    Canvas(modifier) {
        val r = fittedImageRect(containerSize, bitmap.width, bitmap.height)
        if (r.width <= 0f || r.height <= 0f) return@Canvas
        val cx = r.left + r.width * point.x.coerceIn(0f, 1f)
        val cy = r.top + r.height * point.y.coerceIn(0f, 1f)
        val halfW = 15.dp.toPx()
        val halfH = 11.dp.toPx()
        val corner = 5.dp.toPx()
        val stroke = 1.6.dp.toPx()
        val color = AfGreen.copy(alpha = 0.96f)
        val left = cx - halfW
        val right = cx + halfW
        val top = cy - halfH
        val bottom = cy + halfH
        drawLine(color, Offset(left, top), Offset(left + corner, top), stroke)
        drawLine(color, Offset(left, top), Offset(left, top + corner), stroke)
        drawLine(color, Offset(right, top), Offset(right - corner, top), stroke)
        drawLine(color, Offset(right, top), Offset(right, top + corner), stroke)
        drawLine(color, Offset(left, bottom), Offset(left + corner, bottom), stroke)
        drawLine(color, Offset(left, bottom), Offset(left, bottom - corner), stroke)
        drawLine(color, Offset(right, bottom), Offset(right - corner, bottom), stroke)
        drawLine(color, Offset(right, bottom), Offset(right, bottom - corner), stroke)
    }
}
''')

path.write_text(text)
Path(__file__).unlink()
