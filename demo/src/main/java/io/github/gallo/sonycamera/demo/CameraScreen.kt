package io.github.gallo.sonycamera.demo

import android.graphics.Bitmap
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.gallo.sonycamera.CameraConnectionState
import io.github.gallo.sonycamera.CameraEvent
import io.github.gallo.sonycamera.CameraExposureProperty
import io.github.gallo.sonycamera.CameraExposureSetting
import io.github.gallo.sonycamera.CameraExposureState
import io.github.gallo.sonycamera.CameraFocusFrame
import io.github.gallo.sonycamera.CameraOperationResult
import io.github.gallo.sonycamera.service.CameraConnectionClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.min

private val Accent = Color(0xFFFF4D3D)
private val AfGreen = Color(0xFF3DDC97)
private val Ink = Color(0xFF07080A)
private val Glass = Color(0xCC111319)
private val SoftGlass = Color(0x99111319)

/** Full-screen landscape monitor UI. Controls overlay the image instead of resizing it. */
@Composable
fun CameraScreen(camera: CameraConnectionClient) {
    MaterialTheme(colorScheme = darkColorScheme(primary = Accent, background = Ink)) {
        Surface(color = Ink, modifier = Modifier.fillMaxSize()) {
            val scope = rememberCoroutineScope()
            val state by camera.connectionState.collectAsStateWithLifecycle()
            val cameraName by camera.cameraName.collectAsStateWithLifecycle()

            var frame by remember { mutableStateOf<Bitmap?>(null) }
            var capturedThumb by remember { mutableStateOf<Bitmap?>(null) }
            var flash by remember { mutableStateOf(false) }
            var lastError by remember { mutableStateOf<String?>(null) }
            var focusFrames by remember { mutableStateOf<List<CameraFocusFrame>>(emptyList()) }
            var afBusy by remember { mutableStateOf(false) }
            var peakingEnabled by remember { mutableStateOf(false) }
            var exposure by remember { mutableStateOf<CameraExposureState?>(null) }
            var activeExposure by remember { mutableStateOf<CameraExposureSetting?>(null) }

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
                    activeExposure = null
                }
            }

            LaunchedEffect(flash) {
                if (flash) {
                    delay(55)
                    flash = false
                }
            }

            LaunchedEffect(capturedThumb) {
                if (capturedThumb != null) {
                    delay(1400)
                    capturedThumb = null
                }
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

            fun adjust(setting: CameraExposureSetting, direction: Int) {
                if (state !is CameraConnectionState.Ready) return
                scope.launch {
                    val result = camera.adjustExposure(setting, direction)
                    if (result is CameraOperationResult.Failure) lastError = result.message
                }
            }

            Box(Modifier.fillMaxSize().background(Color.Black)) {
                PreviewPane(
                    state = state,
                    frame = frame,
                    focusFrames = focusFrames,
                    peakingEnabled = peakingEnabled,
                    afBusy = afBusy,
                    onAfPoint = ::requestAf,
                    modifier = Modifier.fillMaxSize()
                )

                MonitorTopBar(
                    state = state,
                    cameraName = cameraName,
                    exposure = exposure,
                    activeExposure = activeExposure,
                    peakingEnabled = peakingEnabled,
                    onExposureClick = { setting ->
                        activeExposure = if (activeExposure == setting) null else setting
                    },
                    onPeakingToggle = { peakingEnabled = !peakingEnabled },
                    onAfCenter = { requestAf(320, 240) },
                    onConnect = { lastError = null; camera.connectToCamera() },
                    onDisconnect = { camera.disconnect() },
                    modifier = Modifier.align(Alignment.TopCenter)
                )

                activeExposure?.let { setting ->
                    ExposureAdjuster(
                        setting = setting,
                        property = exposure?.property(setting),
                        onStep = { direction -> adjust(setting, direction) },
                        onClose = { activeExposure = null },
                        modifier = Modifier
                            .align(Alignment.TopCenter)
                            .padding(top = 66.dp)
                    )
                }

                if (state is CameraConnectionState.Ready) {
                    ShutterButton(
                        onClick = { scope.launch { camera.takePhoto() } },
                        modifier = Modifier
                            .align(Alignment.CenterEnd)
                            .padding(end = 18.dp)
                    )
                }

                capturedThumb?.let { bitmap ->
                    CaptureThumbnail(
                        bitmap = bitmap,
                        modifier = Modifier
                            .align(Alignment.BottomStart)
                            .padding(14.dp)
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
                    LaunchedEffect(message) {
                        delay(3200)
                        lastError = null
                    }
                    Text(
                        text = message,
                        color = Color.White,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium,
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .padding(bottom = 18.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .background(Color.Black.copy(alpha = 0.78f))
                            .border(1.dp, Accent.copy(alpha = 0.7f), RoundedCornerShape(12.dp))
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
    frame: Bitmap?,
    focusFrames: List<CameraFocusFrame>,
    peakingEnabled: Boolean,
    afBusy: Boolean,
    onAfPoint: (Int, Int) -> Unit,
    modifier: Modifier = Modifier
) {
    var containerSize by remember { mutableStateOf(IntSize.Zero) }

    Box(
        modifier = modifier
            .background(Color.Black)
            .onSizeChanged { containerSize = it }
            .pointerInput(state, frame?.width, frame?.height, containerSize, afBusy) {
                val bitmap = frame ?: return@pointerInput
                if (state !is CameraConnectionState.Ready) return@pointerInput
                detectTapGestures { tap ->
                    if (afBusy) return@detectTapGestures
                    val rect = fittedImageRect(containerSize, bitmap.width, bitmap.height)
                    if (!rect.contains(tap)) return@detectTapGestures
                    val nx = ((tap.x - rect.left) / rect.width).coerceIn(0f, 1f)
                    val ny = ((tap.y - rect.top) / rect.height).coerceIn(0f, 1f)
                    onAfPoint((nx * 639f).toInt(), (ny * 479f).toInt())
                }
            },
        contentAlignment = Alignment.Center
    ) {
        if (frame != null) {
            // Fill the entire screen allocation. ContentScale.Fit keeps the complete
            // sensor image visible and centers any unavoidable letterboxing evenly.
            Image(
                bitmap = frame.asImageBitmap(),
                contentDescription = "Sony camera live view",
                contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize()
            )

            FocusPeakingOverlay(
                source = frame,
                enabled = peakingEnabled,
                modifier = Modifier.fillMaxSize()
            )

            if (focusFrames.isNotEmpty() && containerSize != IntSize.Zero) {
                CameraFocusOverlay(
                    bitmap = frame,
                    containerSize = containerSize,
                    frames = focusFrames,
                    modifier = Modifier.fillMaxSize()
                )
            }

            if (afBusy) {
                Text(
                    "AF",
                    color = Color.White,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 14.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(Color.Black.copy(alpha = 0.58f))
                        .padding(horizontal = 10.dp, vertical = 5.dp)
                )
            }
        } else {
            PreviewPlaceholder(state)
        }
    }
}

@Composable
private fun MonitorTopBar(
    state: CameraConnectionState,
    cameraName: String?,
    exposure: CameraExposureState?,
    activeExposure: CameraExposureSetting?,
    peakingEnabled: Boolean,
    onExposureClick: (CameraExposureSetting) -> Unit,
    onPeakingToggle: () -> Unit,
    onAfCenter: () -> Unit,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    modifier: Modifier = Modifier
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(58.dp)
            .background(
                Brush.verticalGradient(
                    listOf(Color.Black.copy(alpha = 0.82f), Color.Black.copy(alpha = 0.38f))
                )
            )
            .padding(horizontal = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Row(
            modifier = Modifier.weight(1f),
            verticalAlignment = Alignment.CenterVertically
        ) {
            val ready = state is CameraConnectionState.Ready
            Box(
                Modifier
                    .size(8.dp)
                    .clip(CircleShape)
                    .background(if (ready) AfGreen else Color(0xFF777A82))
            )
            Spacer(Modifier.width(7.dp))
            Column {
                Text(
                    text = cameraName ?: if (ready) "Sony Camera" else "Monitor",
                    color = Color.White,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Text(
                    text = if (ready) "LIVE" else connectionLabel(state),
                    color = if (ready) AfGreen else Color.White.copy(alpha = 0.55f),
                    fontSize = 8.sp,
                    fontWeight = FontWeight.Bold
                )
            }
        }

        Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            ExposurePill(
                title = "IRIS",
                property = exposure?.aperture,
                active = activeExposure == CameraExposureSetting.APERTURE,
                enabled = state is CameraConnectionState.Ready,
                onClick = { onExposureClick(CameraExposureSetting.APERTURE) }
            )
            ExposurePill(
                title = "SHUTTER",
                property = exposure?.shutterSpeed,
                active = activeExposure == CameraExposureSetting.SHUTTER_SPEED,
                enabled = state is CameraConnectionState.Ready,
                onClick = { onExposureClick(CameraExposureSetting.SHUTTER_SPEED) }
            )
            ExposurePill(
                title = "ISO",
                property = exposure?.iso,
                active = activeExposure == CameraExposureSetting.ISO,
                enabled = state is CameraConnectionState.Ready,
                onClick = { onExposureClick(CameraExposureSetting.ISO) }
            )
        }

        Row(
            modifier = Modifier.weight(1f),
            horizontalArrangement = Arrangement.End,
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (state is CameraConnectionState.Ready) {
                TinyControl(
                    text = "PEAK",
                    active = peakingEnabled,
                    onClick = onPeakingToggle
                )
                Spacer(Modifier.width(5.dp))
                TinyControl(text = "AF-C", active = false, onClick = onAfCenter)
                Spacer(Modifier.width(5.dp))
                TinyControl(text = "LINK", active = true, onClick = onDisconnect)
            } else if (state is CameraConnectionState.Disconnected || state is CameraConnectionState.Error) {
                TinyControl(text = "CONNECT", active = true, onClick = onConnect, width = 66.dp)
            } else {
                CircularProgressIndicator(
                    color = Color.White.copy(alpha = 0.8f),
                    strokeWidth = 2.dp,
                    modifier = Modifier.size(20.dp)
                )
            }
        }
    }
}

@Composable
private fun ExposurePill(
    title: String,
    property: CameraExposureProperty?,
    active: Boolean,
    enabled: Boolean,
    onClick: () -> Unit
) {
    val current = property?.current?.label ?: "--"
    val canClick = enabled && property?.current != null
    val outline = if (active) Accent.copy(alpha = 0.9f) else Color.White.copy(alpha = 0.14f)
    Column(
        modifier = Modifier
            .width(78.dp)
            .height(44.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(if (active) Accent.copy(alpha = 0.15f) else SoftGlass)
            .border(1.dp, outline, RoundedCornerShape(10.dp))
            .clickable(enabled = canClick, onClick = onClick)
            .padding(horizontal = 7.dp, vertical = 4.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(
            title,
            color = Color.White.copy(alpha = if (canClick) 0.54f else 0.28f),
            fontSize = 7.sp,
            fontWeight = FontWeight.Bold
        )
        Text(
            current,
            color = Color.White.copy(alpha = if (canClick) 1f else 0.42f),
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1
        )
    }
}

@Composable
private fun TinyControl(
    text: String,
    active: Boolean,
    onClick: () -> Unit,
    width: androidx.compose.ui.unit.Dp = 48.dp
) {
    Box(
        modifier = Modifier
            .width(width)
            .height(32.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(if (active) Accent.copy(alpha = 0.18f) else SoftGlass)
            .border(
                1.dp,
                if (active) Accent.copy(alpha = 0.65f) else Color.White.copy(alpha = 0.13f),
                RoundedCornerShape(9.dp)
            )
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            color = if (active) Color(0xFFFF6A5D) else Color.White.copy(alpha = 0.84f),
            fontSize = 8.sp,
            fontWeight = FontWeight.Bold
        )
    }
}

@Composable
private fun ExposureAdjuster(
    setting: CameraExposureSetting,
    property: CameraExposureProperty?,
    onStep: (Int) -> Unit,
    onClose: () -> Unit,
    modifier: Modifier = Modifier
) {
    val current = property?.current?.label ?: "--"
    val count = property?.options?.size ?: 0
    val writable = property?.writable == true

    Surface(
        modifier = modifier,
        color = Glass,
        shape = RoundedCornerShape(15.dp),
        shadowElevation = 10.dp,
        border = androidx.compose.foundation.BorderStroke(1.dp, Color.White.copy(alpha = 0.14f))
    ) {
        Row(
            modifier = Modifier.height(62.dp).padding(horizontal = 6.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            StepButton("−", enabled = writable) { onStep(-1) }
            Column(
                modifier = Modifier.width(126.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    settingTitle(setting),
                    color = Color.White.copy(alpha = 0.48f),
                    fontSize = 8.sp,
                    fontWeight = FontWeight.Bold
                )
                Text(current, color = Color.White, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                Text(
                    if (writable) "$count CAMERA STEPS" else "LOCKED IN THIS MODE",
                    color = if (writable) Color.White.copy(alpha = 0.38f) else Accent.copy(alpha = 0.82f),
                    fontSize = 7.sp
                )
            }
            StepButton("+", enabled = writable) { onStep(1) }
            Box(
                modifier = Modifier
                    .padding(start = 3.dp)
                    .size(28.dp)
                    .clip(CircleShape)
                    .clickable(onClick = onClose),
                contentAlignment = Alignment.Center
            ) {
                Text("×", color = Color.White.copy(alpha = 0.55f), fontSize = 16.sp)
            }
        }
    }
}

@Composable
private fun StepButton(text: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .size(48.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Color.White.copy(alpha = if (enabled) 0.10f else 0.035f))
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            color = Color.White.copy(alpha = if (enabled) 0.95f else 0.25f),
            fontSize = 25.sp,
            fontWeight = FontWeight.Light
        )
    }
}

@Composable
private fun ShutterButton(onClick: () -> Unit, modifier: Modifier = Modifier) {
    var pressed by remember { mutableStateOf(false) }
    val scale by animateFloatAsState(if (pressed) 0.88f else 1f, label = "shutterScale")
    LaunchedEffect(pressed) {
        if (pressed) {
            delay(120)
            pressed = false
        }
    }
    Box(
        modifier = modifier
            .size(78.dp)
            .clip(CircleShape)
            .background(Color.Black.copy(alpha = 0.34f))
            .border(1.dp, Color.White.copy(alpha = 0.34f), CircleShape)
            .clickable {
                pressed = true
                onClick()
            },
        contentAlignment = Alignment.Center
    ) {
        Box(
            Modifier
                .size((58 * scale).dp)
                .clip(CircleShape)
                .background(Color.White.copy(alpha = 0.90f))
                .border(2.dp, Color.White, CircleShape)
        )
    }
}

@Composable
private fun CaptureThumbnail(bitmap: Bitmap, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(10.dp))
            .background(Color.Black.copy(alpha = 0.55f))
            .border(1.dp, Color.White.copy(alpha = 0.22f), RoundedCornerShape(10.dp))
            .padding(4.dp)
    ) {
        Image(
            bitmap = bitmap.asImageBitmap(),
            contentDescription = "Last capture",
            contentScale = ContentScale.Crop,
            modifier = Modifier.width(116.dp).height(70.dp).clip(RoundedCornerShape(7.dp))
        )
        Text(
            "CAPTURED",
            color = AfGreen,
            fontSize = 7.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(start = 3.dp, top = 2.dp, bottom = 1.dp)
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
                else -> Color.White.copy(alpha = 0.85f)
            }
            val stroke = 1.7.dp.toPx()
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
    val scale = min(
        container.width.toFloat() / imageWidth.toFloat(),
        container.height.toFloat() / imageHeight.toFloat()
    )
    val width = imageWidth * scale
    val height = imageHeight * scale
    val left = (container.width - width) / 2f
    val top = (container.height - height) / 2f
    return Rect(left, top, left + width, top + height)
}

@Composable
private fun PreviewPlaceholder(state: CameraConnectionState) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        when (state) {
            is CameraConnectionState.Ready -> Text(
                "Waiting for live view…",
                color = Color.White.copy(alpha = 0.48f),
                fontSize = 14.sp
            )
            is CameraConnectionState.Connecting,
            is CameraConnectionState.Initializing,
            is CameraConnectionState.Scanning -> CircularProgressIndicator(color = Accent)
            is CameraConnectionState.Error -> Text(
                state.message,
                color = Color.White.copy(alpha = 0.72f),
                fontSize = 12.sp,
                modifier = Modifier.padding(horizontal = 38.dp)
            )
            is CameraConnectionState.Disconnected -> Text(
                "Connect Sony camera over USB",
                color = Color.White.copy(alpha = 0.48f),
                fontSize = 13.sp
            )
        }
    }
}

private fun connectionLabel(state: CameraConnectionState): String = when (state) {
    is CameraConnectionState.Ready -> "LIVE"
    is CameraConnectionState.Connecting -> "CONNECTING"
    is CameraConnectionState.Initializing -> "INITIALIZING"
    is CameraConnectionState.Scanning -> "SCANNING"
    is CameraConnectionState.Error -> "ERROR"
    is CameraConnectionState.Disconnected -> "OFFLINE"
}

private fun settingTitle(setting: CameraExposureSetting): String = when (setting) {
    CameraExposureSetting.APERTURE -> "APERTURE"
    CameraExposureSetting.SHUTTER_SPEED -> "SHUTTER SPEED"
    CameraExposureSetting.ISO -> "ISO"
}
