from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise RuntimeError(f"Expected source block not found in {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1))


CAM = "demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt"
PEAK = "demo/src/main/java/io/github/gallo/sonycamera/demo/FocusPeaking.kt"
USB = "sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt"
CLIENT = "sonycamera/src/main/java/io/github/gallo/sonycamera/service/CameraConnectionClient.kt"
PTP = "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"

replace_once(CAM, '''            var magnification by remember { mutableStateOf(1f) }
            var magnifyPivot by remember { mutableStateOf(Offset(0.5f, 0.5f)) }
''', '''            var magnification by remember { mutableStateOf(1f) }
            var magnifyPivot by remember { mutableStateOf(Offset(0.5f, 0.5f)) }
            var focusPoint by remember { mutableStateOf(Offset(0.5f, 0.5f)) }
''')

replace_once(CAM, '''            LaunchedEffect(camera) {
                camera.liveviewFrames.collect { bitmap -> frame = bitmap }
            }
''', '''            LaunchedEffect(camera) {
                camera.liveviewFrames.collect { bitmap ->
                    if (camera.connectionState.value is CameraConnectionState.Ready) frame = bitmap
                }
            }
''')

replace_once(CAM, '                        is CameraEvent.FocusFramesUpdated -> focusFrames = event.info.frames\n', '''                        is CameraEvent.FocusFramesUpdated -> {
                            focusFrames = event.info.frames
                            focusPoint = preferredFocusPivot(event.info.frames)
                        }
''')

replace_once(CAM, '''                if (state !is CameraConnectionState.Ready) {
                    focusFrames = emptyList()
                    exposure = null
                    cameraSettings = null
                    activeExposure = null
                    activeSetting = null
                }
''', '''                if (state !is CameraConnectionState.Ready) {
                    frame = null
                    focusFrames = emptyList()
                    exposure = null
                    cameraSettings = null
                    activeExposure = null
                    activeSetting = null
                    magnification = 1f
                    magnifyPivot = Offset(0.5f, 0.5f)
                    focusPoint = Offset(0.5f, 0.5f)
                }
''')

replace_once(CAM, '''            fun requestAf(x: Int, y: Int) {
                if (afBusy || state !is CameraConnectionState.Ready) return
                afBusy = true
''', '''            fun requestAf(x: Int, y: Int) {
                if (afBusy || state !is CameraConnectionState.Ready) return
                focusPoint = Offset(x.coerceIn(0, 639) / 639f, y.coerceIn(0, 479) / 479f)
                afBusy = true
''')

replace_once(CAM, '''                    focusFrames = focusFrames,
                    peakingLevel = peakingLevel,
''', '''                    focusFrames = focusFrames,
                    focusAreaRaw = cameraSettings?.focusArea?.current?.rawValue,
                    peakingLevel = peakingLevel,
''')

replace_once(CAM, '''                        onMagnify = {
                            magnification = nextMagnification(magnification)
                            if (magnification == 1f) magnifyPivot = Offset(0.5f, 0.5f)
                        },
''', '''                        onMagnify = {
                            val next = nextMagnification(magnification)
                            magnification = next
                            magnifyPivot = if (next == 1f) Offset(0.5f, 0.5f) else focusPoint
                        },
''')

replace_once(CAM, '''    displayFrame: Bitmap?,
    focusFrames: List<CameraFocusFrame>,
    peakingLevel: PeakingLevel,
''', '''    displayFrame: Bitmap?,
    focusFrames: List<CameraFocusFrame>,
    focusAreaRaw: Long?,
    peakingLevel: PeakingLevel,
''')

replace_once(CAM, '''            val rect = fittedImageRect(containerSize, source.width, source.height)
            val origin = if (containerSize.width > 0 && containerSize.height > 0 && rect.width > 0f) {
                TransformOrigin(
                    pivotFractionX = ((rect.left + rect.width * magnifyPivot.x) / containerSize.width).coerceIn(0f, 1f),
                    pivotFractionY = ((rect.top + rect.height * magnifyPivot.y) / containerSize.height).coerceIn(0f, 1f)
                )
            } else TransformOrigin.Center
''', '''            val rect = fittedImageRect(containerSize, source.width, source.height)
            val translation = magnifyTranslation(containerSize, rect, magnification, magnifyPivot)
''')

replace_once(CAM, '''                    .graphicsLayer {
                        scaleX = magnification
                        scaleY = magnification
                        transformOrigin = origin
                    }
''', '''                    .graphicsLayer {
                        scaleX = magnification
                        scaleY = magnification
                        transformOrigin = TransformOrigin.Center
                        translationX = translation.x
                        translationY = translation.y
                    }
''')

replace_once(CAM, '''                FocusPeakingOverlay(
                    source = source,
                    level = peakingLevel,
                    modifier = Modifier.fillMaxSize()
                )
                if (focusFrames.isNotEmpty() && containerSize != IntSize.Zero) {
''', '''                FocusPeakingOverlay(source, peakingLevel, Modifier.fillMaxSize())
                FocusAreaSelectionOverlay(source, containerSize, focusAreaRaw, focusFrames, Modifier.fillMaxSize())
                if (focusFrames.isNotEmpty() && containerSize != IntSize.Zero) {
''')

replace_once(CAM, '''            if (magnification > 1f) {
                Text(
                    text = "${magnification.toInt()}×",
                    color = Color.White,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(top = 10.dp, end = 10.dp)
                        .background(Color.Black.copy(alpha = 0.55f), RoundedCornerShape(2.dp))
                        .padding(horizontal = 7.dp, vertical = 4.dp)
                )
            }
''', '''            if (magnification > 1f) {
                Text(
                    text = "${magnification.toInt()}×",
                    color = Color.White,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.align(Alignment.TopEnd).padding(top = 10.dp, end = 10.dp)
                        .background(Color.Black.copy(alpha = 0.55f), RoundedCornerShape(2.dp))
                        .padding(horizontal = 7.dp, vertical = 4.dp)
                )
                MagnificationThumbnail(source, magnification, magnifyPivot,
                    Modifier.align(Alignment.TopEnd).padding(top = 42.dp, end = 10.dp))
            }
''')

replace_once(CAM, '''    var dragRemainder by remember { mutableStateOf(0f) }
    var dragging by remember { mutableStateOf(false) }

    LaunchedEffect(currentRaw, options, dragging) {
        if (!dragging) {
            val index = options.indexOfFirst { it.rawValue == currentRaw }
            if (index >= 0) previewIndex = index
        }
    }
''', '''    var dragRemainder by remember { mutableStateOf(0f) }
    var dragging by remember { mutableStateOf(false) }
    var pendingRaw by remember { mutableStateOf<Long?>(null) }

    LaunchedEffect(currentRaw, options) {
        if (!dragging) {
            if (pendingRaw == currentRaw) pendingRaw = null
            if (pendingRaw == null) {
                val index = options.indexOfFirst { it.rawValue == currentRaw }
                if (index >= 0) previewIndex = index
            }
        }
    }
''')

replace_once(CAM, '''                                onHorizontalDrag = { _, dragAmount ->
                                    dragRemainder += dragAmount
                                    val detent = 28.dp.toPx()
                                    while (dragRemainder <= -detent) {
                                        previewIndex = (previewIndex + 1).coerceAtMost(options.lastIndex)
                                        dragRemainder += detent
                                    }
                                    while (dragRemainder >= detent) {
                                        previewIndex = (previewIndex - 1).coerceAtLeast(0)
                                        dragRemainder -= detent
                                    }
                                },
                                onDragEnd = {
                                    dragging = false
                                    dragRemainder = 0f
                                    options.getOrNull(previewIndex)?.let { selected ->
                                        if (selected.rawValue != currentRaw) onSelect(selected.rawValue)
                                    }
                                },
''', '''                                onHorizontalDrag = { _, dragAmount ->
                                    dragRemainder += dragAmount
                                    val detent = 24.dp.toPx()
                                    while (dragRemainder <= -detent) {
                                        val next = (previewIndex + 1).coerceAtMost(options.lastIndex)
                                        if (next != previewIndex) {
                                            previewIndex = next
                                            options[next].let { pendingRaw = it.rawValue; onSelect(it.rawValue) }
                                        }
                                        dragRemainder += detent
                                    }
                                    while (dragRemainder >= detent) {
                                        val next = (previewIndex - 1).coerceAtLeast(0)
                                        if (next != previewIndex) {
                                            previewIndex = next
                                            options[next].let { pendingRaw = it.rawValue; onSelect(it.rawValue) }
                                        }
                                        dragRemainder -= detent
                                    }
                                },
                                onDragEnd = { dragging = false; dragRemainder = 0f },
''')

replace_once(CAM, '                        modifier = Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 8.dp),\n', '''                        modifier = Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 8.dp)
                            .graphicsLayer { translationX = dragRemainder * 2.2f },
''')

helpers = r'''
private fun preferredFocusPivot(frames: List<CameraFocusFrame>): Offset {
    fun CameraFocusFrame.valid() = xDenominator > 0L && yDenominator > 0L
    val frame = frames.firstOrNull { it.valid() && it.state == 0x0002 }
        ?: frames.firstOrNull { it.valid() && it.priority != 0 }
        ?: frames.firstOrNull { it.valid() }
    return frame?.let { Offset(it.centerXNormalized, it.centerYNormalized) } ?: Offset(0.5f, 0.5f)
}

private fun magnifyTranslation(container: IntSize, imageRect: Rect, zoom: Float, pivot: Offset): Offset {
    if (zoom <= 1f || container == IntSize.Zero || imageRect.width <= 0f) return Offset.Zero
    val cx = container.width / 2f
    val cy = container.height / 2f
    val px = imageRect.left + imageRect.width * pivot.x.coerceIn(0f, 1f)
    val py = imageRect.top + imageRect.height * pivot.y.coerceIn(0f, 1f)
    val baseLeft = cx + (imageRect.left - cx) * zoom
    val baseRight = cx + (imageRect.right - cx) * zoom
    val baseTop = cy + (imageRect.top - cy) * zoom
    val baseBottom = cy + (imageRect.bottom - cy) * zoom
    val minX = container.width - baseRight
    val maxX = -baseLeft
    val minY = container.height - baseBottom
    val maxY = -baseTop
    return Offset(
        if (minX <= maxX) (-(px - cx) * zoom).coerceIn(minX, maxX) else 0f,
        if (minY <= maxY) (-(py - cy) * zoom).coerceIn(minY, maxY) else 0f
    )
}

@Composable
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

@Composable
private fun FocusAreaSelectionOverlay(bitmap: Bitmap, containerSize: IntSize, focusAreaRaw: Long?, frames: List<CameraFocusFrame>, modifier: Modifier = Modifier) {
    val code = (focusAreaRaw ?: return) and 0xFFFF
    if (code != 3L && code != 11L) return
    val pivot = preferredFocusPivot(frames)
    Canvas(modifier) {
        val r = fittedImageRect(containerSize, bitmap.width, bitmap.height)
        val w = r.width * 0.36f
        val h = r.height * 0.36f
        val cx = (r.left + r.width * pivot.x).coerceIn(r.left + w / 2f, r.right - w / 2f)
        val cy = (r.top + r.height * pivot.y).coerceIn(r.top + h / 2f, r.bottom - h / 2f)
        val p = Offset(cx - w / 2f, cy - h / 2f)
        drawRect(AfGreen.copy(alpha = 0.09f), p, androidx.compose.ui.geometry.Size(w, h))
        drawRect(AfGreen.copy(alpha = 0.92f), p, androidx.compose.ui.geometry.Size(w, h),
            style = androidx.compose.ui.graphics.drawscope.Stroke(width = 1.6.dp.toPx()))
    }
}

'''
replace_once(CAM, '@Composable\nprivate fun CameraFocusOverlay(\n', helpers + '@Composable\nprivate fun CameraFocusOverlay(\n')

replace_once(CAM, '''@Composable
private fun PreviewPlaceholder(state: CameraConnectionState) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        when (state) {
            is CameraConnectionState.Ready -> Text("Waiting for live view…", color = Color.White.copy(alpha = 0.48f), fontSize = 14.sp)
            is CameraConnectionState.Connecting,
            is CameraConnectionState.Initializing,
            is CameraConnectionState.Scanning -> CircularProgressIndicator(color = Accent)
            is CameraConnectionState.Error -> Text(state.message, color = Color.White.copy(alpha = 0.72f), fontSize = 12.sp)
            is CameraConnectionState.Disconnected -> Text("Connect Sony camera over USB", color = Color.White.copy(alpha = 0.48f), fontSize = 13.sp)
        }
    }
}
''', '''@Composable
private fun PreviewPlaceholder(state: CameraConnectionState) {
    Box(Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
        if (state is CameraConnectionState.Ready) {
            Text("Waiting for live view…", color = Color.White.copy(alpha = 0.48f), fontSize = 14.sp)
        } else {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("NO SIGNAL", color = Color.White, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(7.dp))
                val detail = when (state) {
                    is CameraConnectionState.Error -> state.message
                    is CameraConnectionState.Connecting -> "Camera reconnecting…"
                    is CameraConnectionState.Initializing -> "Camera initializing…"
                    is CameraConnectionState.Scanning -> "Looking for camera…"
                    is CameraConnectionState.Disconnected -> "Camera disconnected"
                    is CameraConnectionState.Ready -> ""
                }
                Text(detail, color = Color.White.copy(alpha = 0.48f), fontSize = 11.sp, textAlign = TextAlign.Center)
            }
        }
    }
}
''')

replace_once(PEAK, 'import kotlinx.coroutines.delay\n', '')
replace_once(PEAK, 'import kotlinx.coroutines.flow.filterNotNull\n', 'import kotlinx.coroutines.flow.collectLatest\nimport kotlinx.coroutines.flow.filterNotNull\n')
replace_once(PEAK, '''        latestSource.filterNotNull().collect { bitmap ->
            overlay = withContext(Dispatchers.Default) { createPeakingMask(bitmap, threshold) }
            delay(45)
        }
''', '''        latestSource.filterNotNull().collectLatest { bitmap ->
            overlay = withContext(Dispatchers.Default) { createPeakingMask(bitmap, threshold) }
        }
''')
replace_once(PEAK, '    val sample = if (source.width >= 900 || source.height >= 600) 2 else 1\n', '    val sample = if (source.width >= 640 || source.height >= 480) 2 else 1\n')

replace_once(USB, '''        private const val EXPOSURE_POLL_INTERVAL_MS = 1_200L
        private const val SETTINGS_POLL_INTERVAL_MS = 2_200L
''', '''        private const val EXPOSURE_POLL_INTERVAL_MS = 120L
        private const val SETTINGS_POLL_INTERVAL_MS = 250L
''')
replace_once(USB, '''    private val _liveviewFrames = MutableSharedFlow<Bitmap>(
        replay = 1,
''', '''    private val _liveviewFrames = MutableSharedFlow<Bitmap>(
        replay = 0,
''')
replace_once(CLIENT, '            .shareIn(scope, SharingStarted.Eagerly, replay = 1)\n', '            .shareIn(scope, SharingStarted.Eagerly, replay = 0)\n')

replace_once(PTP, '        CameraSetting.FOCUS_AREA -> "0x%04X".format(raw and 0xFFFF)\n', '''        CameraSetting.FOCUS_AREA -> when (raw and 0xFFFF) {
            1L -> "REGIST"; 2L -> "WIDE"; 3L -> "ZONE"; 4L -> "CENTER"
            5L -> "SPOT S"; 6L -> "SPOT M"; 7L -> "SPOT L"; 8L -> "EXPAND"
            9L -> "TRACK ALL"; 10L -> "TRACK SEL"; 11L -> "TRACK AREA"
            12L -> "TRACK S"; 13L -> "TRACK M"; 14L -> "TRACK L"; 15L -> "TRACK SUBJ"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
''')

Path(__file__).unlink()
print("Applied a7C II live-view fixes")
