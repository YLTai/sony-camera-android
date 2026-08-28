package io.github.gallo.sonycamera.demo

import android.graphics.Bitmap
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.gallo.sonycamera.CameraConnectionState
import io.github.gallo.sonycamera.CameraEvent
import io.github.gallo.sonycamera.CameraExposureOption
import io.github.gallo.sonycamera.CameraExposureProperty
import io.github.gallo.sonycamera.CameraExposureSetting
import io.github.gallo.sonycamera.CameraExposureState
import io.github.gallo.sonycamera.CameraFocusFrame
import io.github.gallo.sonycamera.CameraOperationResult
import io.github.gallo.sonycamera.CameraSetting
import io.github.gallo.sonycamera.CameraSettingOption
import io.github.gallo.sonycamera.CameraSettingProperty
import io.github.gallo.sonycamera.CameraSettingsState
import io.github.gallo.sonycamera.service.CameraConnectionClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.min

private val Accent = Color(0xFFFF3B30)
private val AfGreen = Color(0xFF39E58C)
private val Ink = Color(0xFF050506)
private val SonyPanel = Color(0xE6151619)
private val SonyTile = Color(0xD91B1C20)
private val Hairline = Color.White.copy(alpha = 0.22f)

@Composable
fun CameraScreen(camera: CameraConnectionClient) {
    MaterialTheme(colorScheme = darkColorScheme(primary = Accent, background = Ink)) {
        Surface(color = Ink, modifier = Modifier.fillMaxSize()) {
            val context = LocalContext.current
            val scope = rememberCoroutineScope()
            val state by camera.connectionState.collectAsStateWithLifecycle()
            val cameraName by camera.cameraName.collectAsStateWithLifecycle()

            var frame by remember { mutableStateOf<Bitmap?>(null) }
            var capturedThumb by remember { mutableStateOf<Bitmap?>(null) }
            var flash by remember { mutableStateOf(false) }
            var lastError by remember { mutableStateOf<String?>(null) }
            var focusFrames by remember { mutableStateOf<List<CameraFocusFrame>>(emptyList()) }
            var exposure by remember { mutableStateOf<CameraExposureState?>(null) }
            var cameraSettings by remember { mutableStateOf<CameraSettingsState?>(null) }
            var afBusy by remember { mutableStateOf(false) }

            var menusVisible by remember { mutableStateOf(true) }
            var activeExposure by remember { mutableStateOf<CameraExposureSetting?>(null) }
            var activeSetting by remember { mutableStateOf<CameraSetting?>(null) }
            var showLutPanel by remember { mutableStateOf(false) }

            var peakingLevel by remember { mutableStateOf(PeakingLevel.OFF) }
            var zebraThreshold by remember { mutableStateOf<Int?>(null) }
            var histogramEnabled by remember { mutableStateOf(false) }
            var magnification by remember { mutableStateOf(1f) }
            var magnifyPivot by remember { mutableStateOf(Offset(0.5f, 0.5f)) }

            var storedLuts by remember { mutableStateOf(LutLibrary.list(context)) }
            var selectedLut by remember { mutableStateOf<StoredLut?>(storedLuts.firstOrNull()) }
            var cubeLut by remember { mutableStateOf<CubeLut?>(null) }
            var lutEnabled by remember { mutableStateOf(false) }
            var lutIntensity by remember { mutableStateOf(1f) }

            val lutLauncher = rememberLauncherForActivityResult(
                contract = ActivityResultContracts.OpenDocument()
            ) { uri ->
                if (uri != null) {
                    scope.launch {
                        val result = withContext(Dispatchers.IO) { LutLibrary.import(context, uri) }
                        result.onSuccess { imported ->
                            storedLuts = LutLibrary.list(context)
                            selectedLut = storedLuts.firstOrNull { it.id == imported.id } ?: imported
                            lastError = null
                        }.onFailure { error ->
                            lastError = error.message ?: "Could not import LUT"
                        }
                    }
                }
            }

            LaunchedEffect(selectedLut?.id) {
                val selected = selectedLut
                cubeLut = if (selected == null) null else {
                    withContext(Dispatchers.IO) { LutLibrary.load(selected).getOrNull() }
                }
                if (cubeLut != null && selected != null) lutEnabled = true
            }

            val displayFrame = rememberLutProcessedFrame(
                source = frame,
                lut = cubeLut,
                enabled = lutEnabled,
                intensity = lutIntensity
            )

            LaunchedEffect(camera) {
                camera.liveviewFrames.collect { bitmap -> frame = bitmap }
            }
            LaunchedEffect(camera) {
                camera.events.collect { event ->
                    when (event) {
                        is CameraEvent.PhotoCaptured -> capturedThumb = event.bitmap
                        is CameraEvent.ShutterFired -> flash = true
                        is CameraEvent.FocusFramesUpdated -> focusFrames = event.info.frames
                        is CameraEvent.ExposureUpdated -> exposure = event.state
                        is CameraEvent.CameraSettingsUpdated -> cameraSettings = event.state
                        is CameraEvent.Error -> lastError = event.message
                        is CameraEvent.ConnectionLost -> lastError = "Connection lost"
                        else -> Unit
                    }
                }
            }
            LaunchedEffect(state) {
                if (state !is CameraConnectionState.Ready) {
                    focusFrames = emptyList()
                    exposure = null
                    cameraSettings = null
                    activeExposure = null
                    activeSetting = null
                }
            }
            LaunchedEffect(menusVisible) {
                if (!menusVisible) {
                    activeExposure = null
                    activeSetting = null
                    showLutPanel = false
                }
            }
            LaunchedEffect(flash) {
                if (flash) { delay(55); flash = false }
            }
            LaunchedEffect(capturedThumb) {
                if (capturedThumb != null) { delay(1200); capturedThumb = null }
            }

            fun requestAf(x: Int, y: Int) {
                if (afBusy || state !is CameraConnectionState.Ready) return
                afBusy = true
                scope.launch {
                    val result = camera.setAfPoint(x.coerceIn(0, 639), y.coerceIn(0, 479))
                    if (result is CameraOperationResult.Failure) lastError = result.message
                    afBusy = false
                }
            }

            fun setExposure(setting: CameraExposureSetting, raw: Long) {
                scope.launch {
                    val result = camera.setExposure(setting, raw)
                    if (result is CameraOperationResult.Failure) lastError = result.message
                }
            }

            fun setCameraSetting(setting: CameraSetting, raw: Long) {
                scope.launch {
                    val result = camera.setCameraSetting(setting, raw)
                    if (result is CameraOperationResult.Failure) lastError = result.message
                }
            }

            Box(Modifier.fillMaxSize().background(Color.Black)) {
                PreviewPane(
                    state = state,
                    sourceFrame = frame,
                    displayFrame = displayFrame,
                    focusFrames = focusFrames,
                    peakingLevel = peakingLevel,
                    zebraThreshold = zebraThreshold,
                    magnification = magnification,
                    magnifyPivot = magnifyPivot,
                    afBusy = afBusy,
                    onAfPoint = ::requestAf,
                    onMenuVisibility = { menusVisible = it },
                    onMagnificationChange = { zoom, pivot ->
                        magnification = zoom
                        magnifyPivot = pivot
                    },
                    modifier = Modifier.fillMaxSize()
                )

                if (histogramEnabled) {
                    LumaHistogramOverlay(
                        source = frame,
                        enabled = true,
                        modifier = Modifier
                            .align(Alignment.BottomStart)
                            .padding(start = 12.dp, bottom = if (menusVisible) 74.dp else 12.dp)
                    )
                }

                AnimatedVisibility(
                    visible = menusVisible,
                    enter = slideInVertically { -it } + fadeIn(),
                    exit = slideOutVertically { -it } + fadeOut(),
                    modifier = Modifier.align(Alignment.TopCenter)
                ) {
                    SonyTopBar(
                        state = state,
                        cameraName = cameraName,
                        exposure = exposure,
                        activeExposure = activeExposure,
                        onExposureClick = { setting ->
                            activeSetting = null
                            showLutPanel = false
                            activeExposure = if (activeExposure == setting) null else setting
                        },
                        onConnect = { lastError = null; camera.connectToCamera() },
                        onDisconnect = { camera.disconnect() }
                    )
                }

                AnimatedVisibility(
                    visible = menusVisible && state is CameraConnectionState.Ready,
                    enter = slideInVertically { it } + fadeIn(),
                    exit = slideOutVertically { it } + fadeOut(),
                    modifier = Modifier.align(Alignment.BottomCenter)
                ) {
                    CameraSettingsStrip(
                        settings = cameraSettings,
                        activeSetting = activeSetting,
                        onClick = { setting ->
                            activeExposure = null
                            showLutPanel = false
                            activeSetting = if (activeSetting == setting) null else setting
                        }
                    )
                }

                AnimatedVisibility(
                    visible = menusVisible && state is CameraConnectionState.Ready,
                    enter = fadeIn(),
                    exit = fadeOut(),
                    modifier = Modifier.align(Alignment.CenterStart)
                ) {
                    MonitorToolRail(
                        peakingLevel = peakingLevel,
                        zebraThreshold = zebraThreshold,
                        histogramEnabled = histogramEnabled,
                        magnification = magnification,
                        lutEnabled = lutEnabled,
                        lutName = selectedLut?.displayName,
                        onPeaking = { peakingLevel = nextPeakingLevel(peakingLevel) },
                        onZebra = { zebraThreshold = nextZebraThreshold(zebraThreshold) },
                        onHistogram = { histogramEnabled = !histogramEnabled },
                        onMagnify = {
                            magnification = nextMagnification(magnification)
                            if (magnification == 1f) magnifyPivot = Offset(0.5f, 0.5f)
                        },
                        onLut = {
                            activeExposure = null
                            activeSetting = null
                            showLutPanel = !showLutPanel
                        }
                    )
                }

                if (menusVisible) {
                    activeExposure?.let { setting ->
                        exposure?.property(setting)?.let { property ->
                            OptionSelectorPanel(
                                title = exposureTitle(setting),
                                currentRaw = property.current?.rawValue,
                                options = property.options.map { SelectorOption(it.rawValue, it.label) },
                                writable = property.writable,
                                onSelect = { raw -> setExposure(setting, raw) },
                                modifier = Modifier.align(Alignment.TopCenter).padding(top = 78.dp)
                            )
                        }
                    }
                    activeSetting?.let { setting ->
                        cameraSettings?.property(setting)?.let { property ->
                            OptionSelectorPanel(
                                title = cameraSettingTitle(setting),
                                currentRaw = property.current?.rawValue,
                                options = property.options.map { SelectorOption(it.rawValue, it.label) },
                                writable = property.writable,
                                onSelect = { raw -> setCameraSetting(setting, raw) },
                                modifier = Modifier.align(Alignment.TopCenter).padding(top = 78.dp)
                            )
                        }
                    }
                    if (showLutPanel) {
                        LutPanel(
                            storedLuts = storedLuts,
                            selected = selectedLut,
                            enabled = lutEnabled,
                            intensity = lutIntensity,
                            onImport = { lutLauncher.launch(arrayOf("application/octet-stream", "text/plain", "*/*")) },
                            onSelect = { selectedLut = it },
                            onToggle = { lutEnabled = !lutEnabled },
                            onIntensity = { lutIntensity = it },
                            onDelete = { lut ->
                                if (LutLibrary.delete(lut)) {
                                    storedLuts = LutLibrary.list(context)
                                    if (selectedLut?.id == lut.id) {
                                        selectedLut = storedLuts.firstOrNull()
                                        if (selectedLut == null) lutEnabled = false
                                    }
                                }
                            },
                            modifier = Modifier.align(Alignment.TopCenter).padding(top = 78.dp)
                        )
                    }
                }

                if (menusVisible && state is CameraConnectionState.Ready) {
                    ShutterButton(
                        onClick = { scope.launch { camera.takePhoto() } },
                        modifier = Modifier.align(Alignment.CenterEnd).padding(end = 18.dp)
                    )
                }

                capturedThumb?.let { bitmap ->
                    Image(
                        bitmap = bitmap.asImageBitmap(),
                        contentDescription = "Last capture",
                        contentScale = ContentScale.Crop,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(end = 12.dp, bottom = if (menusVisible) 76.dp else 12.dp)
                            .width(96.dp)
                            .height(58.dp)
                            .border(1.dp, Color.White.copy(alpha = 0.45f))
                    )
                }

                if (!menusVisible) {
                    Box(
                        Modifier
                            .align(Alignment.TopCenter)
                            .padding(top = 8.dp)
                            .width(42.dp)
                            .height(3.dp)
                            .background(Color.White.copy(alpha = 0.38f), RoundedCornerShape(2.dp))
                    )
                }

                val flashAlpha by animateFloatAsState(
                    targetValue = if (flash) 0.82f else 0f,
                    animationSpec = tween(durationMillis = if (flash) 0 else 190),
                    label = "captureFlash"
                )
                if (flashAlpha > 0f) {
                    Box(Modifier.fillMaxSize().background(Color.White.copy(alpha = flashAlpha)))
                }

                lastError?.let { message ->
                    LaunchedEffect(message) { delay(3500); lastError = null }
                    Text(
                        text = message,
                        color = Color.White,
                        fontSize = 12.sp,
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .padding(bottom = if (menusVisible) 72.dp else 16.dp)
                            .background(Color.Black.copy(alpha = 0.82f), RoundedCornerShape(3.dp))
                            .border(1.dp, Accent.copy(alpha = 0.75f), RoundedCornerShape(3.dp))
                            .padding(horizontal = 14.dp, vertical = 8.dp)
                    )
                }
            }
        }
    }
}

@Composable
private fun PreviewPane(
    state: CameraConnectionState,
    sourceFrame: Bitmap?,
    displayFrame: Bitmap?,
    focusFrames: List<CameraFocusFrame>,
    peakingLevel: PeakingLevel,
    zebraThreshold: Int?,
    magnification: Float,
    magnifyPivot: Offset,
    afBusy: Boolean,
    onAfPoint: (Int, Int) -> Unit,
    onMenuVisibility: (Boolean) -> Unit,
    onMagnificationChange: (Float, Offset) -> Unit,
    modifier: Modifier = Modifier
) {
    var containerSize by remember { mutableStateOf(IntSize.Zero) }
    var dragTotal by remember { mutableStateOf(0f) }
    val source = sourceFrame

    Box(
        modifier = modifier
            .background(Color.Black)
            .clipToBounds()
            .onSizeChanged { containerSize = it }
            .pointerInput(state, source?.width, source?.height, containerSize, afBusy, magnification, magnifyPivot) {
                val bitmap = source ?: return@pointerInput
                if (state !is CameraConnectionState.Ready) return@pointerInput
                detectTapGestures(
                    onDoubleTap = { tap ->
                        val mapped = mapTapToImage(tap, containerSize, bitmap.width, bitmap.height, magnification, magnifyPivot)
                            ?: return@detectTapGestures
                        val next = nextMagnification(magnification)
                        onMagnificationChange(
                            next,
                            if (next == 1f) Offset(0.5f, 0.5f) else mapped
                        )
                    },
                    onTap = { tap ->
                        if (afBusy) return@detectTapGestures
                        val mapped = mapTapToImage(tap, containerSize, bitmap.width, bitmap.height, magnification, magnifyPivot)
                            ?: return@detectTapGestures
                        onAfPoint((mapped.x * 639f).toInt(), (mapped.y * 479f).toInt())
                    }
                )
            }
            .pointerInput(Unit) {
                detectVerticalDragGestures(
                    onDragStart = { dragTotal = 0f },
                    onVerticalDrag = { _, amount -> dragTotal += amount },
                    onDragEnd = {
                        when {
                            dragTotal < -55f -> onMenuVisibility(false)
                            dragTotal > 55f -> onMenuVisibility(true)
                        }
                        dragTotal = 0f
                    },
                    onDragCancel = { dragTotal = 0f }
                )
            },
        contentAlignment = Alignment.Center
    ) {
        if (source != null && displayFrame != null) {
            val rect = fittedImageRect(containerSize, source.width, source.height)
            val origin = if (containerSize.width > 0 && containerSize.height > 0 && rect.width > 0f) {
                TransformOrigin(
                    pivotFractionX = ((rect.left + rect.width * magnifyPivot.x) / containerSize.width).coerceIn(0f, 1f),
                    pivotFractionY = ((rect.top + rect.height * magnifyPivot.y) / containerSize.height).coerceIn(0f, 1f)
                )
            } else TransformOrigin.Center

            Box(
                Modifier
                    .fillMaxSize()
                    .graphicsLayer {
                        scaleX = magnification
                        scaleY = magnification
                        transformOrigin = origin
                    }
            ) {
                Image(
                    bitmap = displayFrame.asImageBitmap(),
                    contentDescription = "Sony camera live view",
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize()
                )
                ZebraOverlay(
                    source = source,
                    thresholdPercent = zebraThreshold,
                    modifier = Modifier.fillMaxSize()
                )
                FocusPeakingOverlay(
                    source = source,
                    level = peakingLevel,
                    modifier = Modifier.fillMaxSize()
                )
                if (focusFrames.isNotEmpty() && containerSize != IntSize.Zero) {
                    CameraFocusOverlay(
                        bitmap = source,
                        containerSize = containerSize,
                        frames = focusFrames,
                        modifier = Modifier.fillMaxSize()
                    )
                }
            }

            if (magnification > 1f) {
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
            if (afBusy) {
                Text(
                    "AF",
                    color = AfGreen,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 12.dp)
                        .background(Color.Black.copy(alpha = 0.58f), RoundedCornerShape(2.dp))
                        .padding(horizontal = 9.dp, vertical = 4.dp)
                )
            }
        } else {
            PreviewPlaceholder(state)
        }
    }
}

@Composable
private fun SonyTopBar(
    state: CameraConnectionState,
    cameraName: String?,
    exposure: CameraExposureState?,
    activeExposure: CameraExposureSetting?,
    onExposureClick: (CameraExposureSetting) -> Unit,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit
) {
    Box(
        Modifier
            .fillMaxWidth()
            .height(74.dp)
            .background(
                Brush.verticalGradient(
                    listOf(Color.Black.copy(alpha = 0.88f), Color.Black.copy(alpha = 0.60f), Color.Transparent)
                )
            )
            .padding(horizontal = 10.dp, vertical = 6.dp)
    ) {
        CameraIdentity(
            state = state,
            cameraName = cameraName,
            modifier = Modifier.align(Alignment.CenterStart).width(175.dp)
        )

        Row(
            modifier = Modifier.align(Alignment.Center),
            horizontalArrangement = Arrangement.spacedBy(5.dp)
        ) {
            SonyParameterTile(
                label = "F NO.",
                value = exposure?.aperture?.current?.label ?: "--",
                enabled = state is CameraConnectionState.Ready && exposure?.aperture?.current != null,
                active = activeExposure == CameraExposureSetting.APERTURE,
                onClick = { onExposureClick(CameraExposureSetting.APERTURE) }
            )
            SonyParameterTile(
                label = "SHUTTER",
                value = exposure?.shutterSpeed?.current?.label ?: "--",
                enabled = state is CameraConnectionState.Ready && exposure?.shutterSpeed?.current != null,
                active = activeExposure == CameraExposureSetting.SHUTTER_SPEED,
                onClick = { onExposureClick(CameraExposureSetting.SHUTTER_SPEED) }
            )
            SonyParameterTile(
                label = "ISO",
                value = exposure?.iso?.current?.label?.let { if (it == "AUTO") "AUTO" else it } ?: "--",
                enabled = state is CameraConnectionState.Ready && exposure?.iso?.current != null,
                active = activeExposure == CameraExposureSetting.ISO,
                onClick = { onExposureClick(CameraExposureSetting.ISO) }
            )
        }

        Row(
            modifier = Modifier.align(Alignment.CenterEnd).width(175.dp),
            horizontalArrangement = Arrangement.End,
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (state is CameraConnectionState.Ready) {
                SonySmallButton("LINK", true, onDisconnect)
            } else if (state is CameraConnectionState.Disconnected || state is CameraConnectionState.Error) {
                SonySmallButton("CONNECT", true, onConnect, 78.dp)
            } else {
                CircularProgressIndicator(color = Color.White, strokeWidth = 2.dp, modifier = Modifier.size(21.dp))
            }
        }
    }
}

@Composable
private fun CameraIdentity(state: CameraConnectionState, cameraName: String?, modifier: Modifier) {
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        val ready = state is CameraConnectionState.Ready
        Box(Modifier.size(7.dp).background(if (ready) AfGreen else Color.Gray, CircleShape))
        Spacer(Modifier.width(7.dp))
        Column {
            Text(
                cameraName ?: "SONY MONITOR",
                color = Color.White,
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            Text(
                if (ready) "USB  •  LIVE" else connectionLabel(state),
                color = if (ready) AfGreen else Color.White.copy(alpha = 0.55f),
                fontSize = 8.sp,
                fontWeight = FontWeight.Bold
            )
        }
    }
}

@Composable
private fun SonyParameterTile(
    label: String,
    value: String,
    enabled: Boolean,
    active: Boolean,
    onClick: () -> Unit
) {
    Column(
        modifier = Modifier
            .width(98.dp)
            .height(62.dp)
            .background(if (active) Color(0xE6281718) else SonyTile, RoundedCornerShape(3.dp))
            .border(
                if (active) 2.dp else 1.dp,
                if (active) Accent else Hairline,
                RoundedCornerShape(3.dp)
            )
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 5.dp, vertical = 5.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            label,
            color = Color.White.copy(alpha = if (enabled) 0.62f else 0.28f),
            fontSize = 8.sp,
            lineHeight = 10.sp,
            fontWeight = FontWeight.Bold,
            maxLines = 1
        )
        Text(
            value,
            color = Color.White.copy(alpha = if (enabled) 1f else 0.38f),
            fontSize = 19.sp,
            lineHeight = 22.sp,
            fontWeight = FontWeight.Bold,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center
        )
    }
}

@Composable
private fun CameraSettingsStrip(
    settings: CameraSettingsState?,
    activeSetting: CameraSetting?,
    onClick: (CameraSetting) -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(68.dp)
            .background(
                Brush.verticalGradient(listOf(Color.Transparent, Color.Black.copy(alpha = 0.72f), Color.Black.copy(alpha = 0.88f)))
            )
            .padding(bottom = 7.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.Bottom
    ) {
        val entries = listOf(
            CameraSetting.FOCUS_MODE to settings?.focusMode,
            CameraSetting.FOCUS_AREA to settings?.focusArea,
            CameraSetting.WHITE_BALANCE to settings?.whiteBalance,
            CameraSetting.METERING_MODE to settings?.meteringMode,
            CameraSetting.DRIVE_MODE to settings?.driveMode,
            CameraSetting.EXPOSURE_COMPENSATION to settings?.exposureCompensation
        )
        entries.forEachIndexed { index, (setting, property) ->
            if (index > 0) Spacer(Modifier.width(4.dp))
            SonySettingTile(
                title = shortSettingTitle(setting),
                value = property?.current?.label ?: "--",
                enabled = property?.current != null,
                active = activeSetting == setting,
                onClick = { onClick(setting) }
            )
        }
    }
}

@Composable
private fun SonySettingTile(
    title: String,
    value: String,
    enabled: Boolean,
    active: Boolean,
    onClick: () -> Unit
) {
    Column(
        modifier = Modifier
            .width(88.dp)
            .height(52.dp)
            .background(if (active) Color(0xE6281718) else SonyTile, RoundedCornerShape(2.dp))
            .border(if (active) 2.dp else 1.dp, if (active) Accent else Hairline, RoundedCornerShape(2.dp))
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 4.dp, vertical = 4.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.SpaceBetween
    ) {
        Text(title, color = Color.White.copy(alpha = 0.55f), fontSize = 7.sp, lineHeight = 8.sp, fontWeight = FontWeight.Bold)
        Text(
            value,
            color = Color.White.copy(alpha = if (enabled) 0.96f else 0.34f),
            fontSize = 11.sp,
            lineHeight = 14.sp,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center
        )
    }
}

@Composable
private fun MonitorToolRail(
    peakingLevel: PeakingLevel,
    zebraThreshold: Int?,
    histogramEnabled: Boolean,
    magnification: Float,
    lutEnabled: Boolean,
    lutName: String?,
    onPeaking: () -> Unit,
    onZebra: () -> Unit,
    onHistogram: () -> Unit,
    onMagnify: () -> Unit,
    onLut: () -> Unit
) {
    Column(
        modifier = Modifier.padding(start = 10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        SonyToolButton("PEAK", peakingLevel.label, peakingLevel != PeakingLevel.OFF, onPeaking)
        SonyToolButton("ZEBRA", zebraThreshold?.let { "$it%" } ?: "OFF", zebraThreshold != null, onZebra)
        SonyToolButton("HIST", if (histogramEnabled) "ON" else "OFF", histogramEnabled, onHistogram)
        SonyToolButton("MAG", "${magnification.toInt()}×", magnification > 1f, onMagnify)
        SonyToolButton("LUT", if (lutEnabled) (lutName?.take(8) ?: "ON") else "OFF", lutEnabled, onLut)
    }
}

@Composable
private fun SonyToolButton(title: String, value: String, active: Boolean, onClick: () -> Unit) {
    Column(
        modifier = Modifier
            .width(58.dp)
            .height(43.dp)
            .background(Color.Black.copy(alpha = 0.58f), RoundedCornerShape(2.dp))
            .border(1.dp, if (active) Accent.copy(alpha = 0.88f) else Color.White.copy(alpha = 0.24f), RoundedCornerShape(2.dp))
            .clickable(onClick = onClick)
            .padding(vertical = 3.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(title, color = Color.White.copy(alpha = 0.63f), fontSize = 7.sp, lineHeight = 8.sp, fontWeight = FontWeight.Bold)
        Text(value, color = if (active) Color.White else Color.White.copy(alpha = 0.62f), fontSize = 10.sp, lineHeight = 12.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

private data class SelectorOption(val rawValue: Long, val label: String)

@Composable
private fun OptionSelectorPanel(
    title: String,
    currentRaw: Long?,
    options: List<SelectorOption>,
    writable: Boolean,
    onSelect: (Long) -> Unit,
    modifier: Modifier = Modifier
) {
    Surface(
        modifier = modifier.fillMaxWidth(0.78f).widthIn(max = 760.dp),
        color = SonyPanel,
        shape = RoundedCornerShape(3.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, Color.White.copy(alpha = 0.24f)),
        shadowElevation = 8.dp
    ) {
        Column(Modifier.padding(horizontal = 9.dp, vertical = 7.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, color = Color.White, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.width(10.dp))
                Text(
                    if (!writable) "DISPLAY ONLY" else "SELECT CAMERA VALUE",
                    color = if (writable) Color.White.copy(alpha = 0.42f) else Accent,
                    fontSize = 7.sp,
                    fontWeight = FontWeight.Bold
                )
            }
            Spacer(Modifier.height(6.dp))
            if (options.isEmpty()) {
                Text("No selectable values reported by camera", color = Color.White.copy(alpha = 0.55f), fontSize = 11.sp)
            } else {
                LazyRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    items(options, key = { it.rawValue }) { option ->
                        val selected = option.rawValue == currentRaw
                        Box(
                            modifier = Modifier
                                .width(92.dp)
                                .height(48.dp)
                                .background(if (selected) Color(0xE62B1718) else Color(0xE6222327), RoundedCornerShape(2.dp))
                                .border(if (selected) 2.dp else 1.dp, if (selected) Accent else Hairline, RoundedCornerShape(2.dp))
                                .clickable(enabled = writable && !selected) { onSelect(option.rawValue) }
                                .padding(horizontal = 5.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                option.label,
                                color = Color.White.copy(alpha = if (writable || selected) 1f else 0.48f),
                                fontSize = 11.sp,
                                fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
                                textAlign = TextAlign.Center,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun LutPanel(
    storedLuts: List<StoredLut>,
    selected: StoredLut?,
    enabled: Boolean,
    intensity: Float,
    onImport: () -> Unit,
    onSelect: (StoredLut) -> Unit,
    onToggle: () -> Unit,
    onIntensity: (Float) -> Unit,
    onDelete: (StoredLut) -> Unit,
    modifier: Modifier = Modifier
) {
    Surface(
        modifier = modifier.fillMaxWidth(0.72f).widthIn(max = 690.dp),
        color = SonyPanel,
        shape = RoundedCornerShape(3.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, Color.White.copy(alpha = 0.24f)),
        shadowElevation = 8.dp
    ) {
        Column(Modifier.padding(9.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("3D LUT", color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.width(8.dp))
                SonySmallButton(if (enabled) "ON" else "OFF", enabled, onToggle, 48.dp)
                Spacer(Modifier.width(5.dp))
                SonySmallButton("IMPORT .CUBE", true, onImport, 94.dp)
                Spacer(Modifier.width(10.dp))
                Text("${(intensity * 100).toInt()}%", color = Color.White.copy(alpha = 0.72f), fontSize = 10.sp)
                Slider(
                    value = intensity,
                    onValueChange = onIntensity,
                    modifier = Modifier.width(150.dp)
                )
            }
            if (storedLuts.isEmpty()) {
                Text("Import a .cube LUT. LUT affects monitor preview only.", color = Color.White.copy(alpha = 0.55f), fontSize = 9.sp)
            } else {
                LazyRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    items(storedLuts, key = { it.id }) { lut ->
                        val isSelected = lut.id == selected?.id
                        Column(
                            modifier = Modifier
                                .width(116.dp)
                                .height(50.dp)
                                .background(Color(0xE6222327), RoundedCornerShape(2.dp))
                                .border(if (isSelected) 2.dp else 1.dp, if (isSelected) Accent else Hairline, RoundedCornerShape(2.dp))
                                .clickable { onSelect(lut) }
                                .padding(horizontal = 5.dp, vertical = 4.dp),
                            verticalArrangement = Arrangement.Center
                        ) {
                            Text(lut.displayName, color = Color.White, fontSize = 9.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(
                                if (isSelected) "SELECTED  •  HOLD X" else ".CUBE",
                                color = Color.White.copy(alpha = 0.42f),
                                fontSize = 6.sp,
                                maxLines = 1
                            )
                        }
                    }
                }
                // Deliberately keep deletion out of a one-tap tile to avoid accidental
                // file loss. Selected LUT can be deleted by this small explicit action.
                if (selected != null) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                        Text(
                            "DELETE SELECTED",
                            color = Accent.copy(alpha = 0.85f),
                            fontSize = 7.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.clickable { onDelete(selected) }.padding(top = 4.dp, end = 2.dp)
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun SonySmallButton(
    text: String,
    active: Boolean,
    onClick: () -> Unit,
    width: androidx.compose.ui.unit.Dp = 54.dp
) {
    Box(
        modifier = Modifier
            .width(width)
            .height(31.dp)
            .background(Color.Black.copy(alpha = 0.58f), RoundedCornerShape(2.dp))
            .border(1.dp, if (active) Accent.copy(alpha = 0.75f) else Hairline, RoundedCornerShape(2.dp))
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Text(text, color = Color.White, fontSize = 8.sp, fontWeight = FontWeight.Bold, maxLines = 1)
    }
}

@Composable
private fun ShutterButton(onClick: () -> Unit, modifier: Modifier = Modifier) {
    var pressed by remember { mutableStateOf(false) }
    val scale by animateFloatAsState(if (pressed) 0.88f else 1f, label = "shutterScale")
    LaunchedEffect(pressed) { if (pressed) { delay(120); pressed = false } }
    Box(
        modifier = modifier
            .size(72.dp)
            .background(Color.Black.copy(alpha = 0.30f), CircleShape)
            .border(1.dp, Color.White.copy(alpha = 0.50f), CircleShape)
            .clickable { pressed = true; onClick() },
        contentAlignment = Alignment.Center
    ) {
        Box(
            Modifier
                .size((54 * scale).dp)
                .background(Color.White.copy(alpha = 0.86f), CircleShape)
                .border(2.dp, Color.White, CircleShape)
        )
    }
}

@Composable
private fun CameraFocusOverlay(
    bitmap: Bitmap,
    containerSize: IntSize,
    frames: List<CameraFocusFrame>,
    modifier: Modifier = Modifier
) {
    Canvas(modifier = modifier) {
        val imageRect = fittedImageRect(containerSize, bitmap.width, bitmap.height)
        if (imageRect.width <= 0f || imageRect.height <= 0f) return@Canvas
        frames.forEach { frame ->
            if (frame.xDenominator <= 0L || frame.yDenominator <= 0L) return@forEach
            val centerX = imageRect.left + imageRect.width * frame.centerXNormalized
            val centerY = imageRect.top + imageRect.height * frame.centerYNormalized
            val frameWidth = imageRect.width * frame.widthNormalized
            val frameHeight = imageRect.height * frame.heightNormalized
            if (frameWidth <= 0f || frameHeight <= 0f) return@forEach
            val left = centerX - frameWidth / 2f
            val right = centerX + frameWidth / 2f
            val top = centerY - frameHeight / 2f
            val bottom = centerY + frameHeight / 2f
            val color = when (frame.state) {
                0x0002 -> AfGreen
                0x0005 -> Accent
                else -> Color.White.copy(alpha = 0.90f)
            }
            val stroke = 1.6.dp.toPx()
            val corner = (min(frameWidth, frameHeight) * 0.28f).coerceIn(4.dp.toPx(), 18.dp.toPx())
            if (frame.type == 0x0010) {
                drawLine(color, Offset(left, centerY), Offset(right, centerY), stroke)
                drawLine(color, Offset(centerX, top), Offset(centerX, bottom), stroke)
            } else {
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
    }
}

private fun fittedImageRect(container: IntSize, imageWidth: Int, imageHeight: Int): Rect {
    if (container.width <= 0 || container.height <= 0 || imageWidth <= 0 || imageHeight <= 0) return Rect.Zero
    val scale = min(container.width.toFloat() / imageWidth, container.height.toFloat() / imageHeight)
    val width = imageWidth * scale
    val height = imageHeight * scale
    val left = (container.width - width) / 2f
    val top = (container.height - height) / 2f
    return Rect(left, top, left + width, top + height)
}

private fun mapTapToImage(
    tap: Offset,
    container: IntSize,
    imageWidth: Int,
    imageHeight: Int,
    zoom: Float,
    pivot: Offset
): Offset? {
    val rect = fittedImageRect(container, imageWidth, imageHeight)
    if (rect.width <= 0f || rect.height <= 0f) return null
    val screenX = (tap.x - rect.left) / rect.width
    val screenY = (tap.y - rect.top) / rect.height
    val x = pivot.x + (screenX - pivot.x) / zoom.coerceAtLeast(1f)
    val y = pivot.y + (screenY - pivot.y) / zoom.coerceAtLeast(1f)
    if (x !in 0f..1f || y !in 0f..1f) return null
    return Offset(x, y)
}

@Composable
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

private fun nextPeakingLevel(level: PeakingLevel): PeakingLevel = when (level) {
    PeakingLevel.OFF -> PeakingLevel.MID
    PeakingLevel.LOW -> PeakingLevel.MID
    PeakingLevel.MID -> PeakingLevel.HIGH
    PeakingLevel.HIGH -> PeakingLevel.LOW
}

private fun nextZebraThreshold(current: Int?): Int? = when (current) {
    null -> 70
    70 -> 80
    80 -> 90
    90 -> 95
    95 -> 100
    else -> null
}

private fun nextMagnification(current: Float): Float = when {
    current < 1.5f -> 2f
    current < 3f -> 4f
    current < 6f -> 8f
    else -> 1f
}

private fun connectionLabel(state: CameraConnectionState): String = when (state) {
    is CameraConnectionState.Ready -> "LIVE"
    is CameraConnectionState.Connecting -> "CONNECTING"
    is CameraConnectionState.Initializing -> "INITIALIZING"
    is CameraConnectionState.Scanning -> "SCANNING"
    is CameraConnectionState.Error -> "ERROR"
    is CameraConnectionState.Disconnected -> "OFFLINE"
}

private fun exposureTitle(setting: CameraExposureSetting): String = when (setting) {
    CameraExposureSetting.APERTURE -> "F NUMBER"
    CameraExposureSetting.SHUTTER_SPEED -> "SHUTTER SPEED"
    CameraExposureSetting.ISO -> "ISO"
}

private fun shortSettingTitle(setting: CameraSetting): String = when (setting) {
    CameraSetting.FOCUS_MODE -> "AF MODE"
    CameraSetting.FOCUS_AREA -> "AF AREA"
    CameraSetting.WHITE_BALANCE -> "WB"
    CameraSetting.METERING_MODE -> "METER"
    CameraSetting.DRIVE_MODE -> "DRIVE"
    CameraSetting.EXPOSURE_COMPENSATION -> "EV"
}

private fun cameraSettingTitle(setting: CameraSetting): String = when (setting) {
    CameraSetting.FOCUS_MODE -> "FOCUS MODE"
    CameraSetting.FOCUS_AREA -> "FOCUS AREA"
    CameraSetting.WHITE_BALANCE -> "WHITE BALANCE"
    CameraSetting.METERING_MODE -> "METERING MODE"
    CameraSetting.DRIVE_MODE -> "DRIVE MODE"
    CameraSetting.EXPOSURE_COMPENSATION -> "EXPOSURE COMPENSATION"
}
