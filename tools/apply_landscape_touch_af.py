from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


# ── Public camera API ────────────────────────────────────────────────────────
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/CameraConnectionManager.kt"
text = read(path)
text = replace_once(
    text,
    "    /** Diagnostic information from the Sony focus-area probe. */\n    data class FocusDebug(val message: String) : CameraEvent()\n",
    "    /** Diagnostic information from the Sony focus-area probe. */\n"
    "    data class FocusDebug(val message: String) : CameraEvent()\n\n"
    "    /** Last AF target accepted by the app-side Sony control path. */\n"
    "    data class AfTargetUpdated(val x: Int, val y: Int) : CameraEvent()\n",
    "CameraEvent.AfTargetUpdated",
)
text = replace_once(
    text,
    "    /** Diagnostic: write Sony AF Area Position to the protocol center (320, 240). */\n    suspend fun testAfCenter(): CameraOperationResult\n",
    "    /** Move the Sony AF target on its 640x480 logical focus grid. */\n"
    "    suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult\n\n"
    "    /** Diagnostic: write Sony AF Area Position to the protocol center (320, 240). */\n"
    "    suspend fun testAfCenter(): CameraOperationResult\n",
    "CameraConnectionManager.setAfPoint",
)
write(path, text)


# ── Client proxy ─────────────────────────────────────────────────────────────
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/service/CameraConnectionClient.kt"
text = read(path)
text = replace_once(
    text,
    "    override suspend fun testAfCenter(): CameraOperationResult =\n        binderFlow.value?.testAfCenter()\n            ?: CameraOperationResult.Failure(\"Camera not connected\")\n",
    "    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult =\n"
    "        binderFlow.value?.setAfPoint(x, y)\n"
    "            ?: CameraOperationResult.Failure(\"Camera not connected\")\n\n"
    "    override suspend fun testAfCenter(): CameraOperationResult =\n"
    "        binderFlow.value?.testAfCenter()\n"
    "            ?: CameraOperationResult.Failure(\"Camera not connected\")\n",
    "CameraConnectionClient.setAfPoint",
)
write(path, text)


# ── Service binder ───────────────────────────────────────────────────────────
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/service/CameraConnectionService.kt"
text = read(path)
text = replace_once(
    text,
    "        suspend fun takePhoto() = engine.takePhoto()\n        suspend fun testAfCenter() = engine.testAfCenter()\n",
    "        suspend fun takePhoto() = engine.takePhoto()\n"
    "        suspend fun setAfPoint(x: Int, y: Int) = engine.setAfPoint(x, y)\n"
    "        suspend fun testAfCenter() = engine.testAfCenter()\n",
    "CameraConnectionService.setAfPoint",
)
write(path, text)


# ── USB manager ──────────────────────────────────────────────────────────────
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt"
text = read(path)
old = '''    override suspend fun testAfCenter(): CameraOperationResult = withContext(Dispatchers.IO) {
        val camera = ptpCamera
            ?: return@withContext CameraOperationResult.Failure("Camera not connected")
        try {
            val message = camera.testAfCenter()
            _events.emit(CameraEvent.FocusDebug(message))
            CameraOperationResult.SuccessWithData(message)
        } catch (e: Exception) {
            Log.e(TAG, "AF center test failed", e)
            val message = "AF CENTER TEST exception: ${e.message ?: e.javaClass.simpleName}"
            _events.emit(CameraEvent.FocusDebug(message))
            CameraOperationResult.Failure(message)
        }
    }
'''
new = '''    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult = withContext(Dispatchers.IO) {
        val camera = ptpCamera
            ?: return@withContext CameraOperationResult.Failure("Camera not connected")
        val safeX = x.coerceIn(0, 639)
        val safeY = y.coerceIn(0, 479)
        try {
            val message = camera.setAfPoint(safeX, safeY)
            _events.emit(CameraEvent.FocusDebug(message))
            _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
            CameraOperationResult.SuccessWithData(message)
        } catch (e: Exception) {
            Log.e(TAG, "AF target command failed", e)
            val message = "AF TARGET exception: ${e.message ?: e.javaClass.simpleName}"
            _events.emit(CameraEvent.FocusDebug(message))
            CameraOperationResult.Failure(message)
        }
    }

    override suspend fun testAfCenter(): CameraOperationResult = withContext(Dispatchers.IO) {
        val camera = ptpCamera
            ?: return@withContext CameraOperationResult.Failure("Camera not connected")
        try {
            val message = camera.testAfCenter()
            _events.emit(CameraEvent.FocusDebug(message))
            _events.emit(CameraEvent.AfTargetUpdated(320, 240))
            CameraOperationResult.SuccessWithData(message)
        } catch (e: Exception) {
            Log.e(TAG, "AF center test failed", e)
            val message = "AF CENTER TEST exception: ${e.message ?: e.javaClass.simpleName}"
            _events.emit(CameraEvent.FocusDebug(message))
            CameraOperationResult.Failure(message)
        }
    }
'''
text = replace_once(text, old, new, "UsbCameraConnectionManager.setAfPoint")
write(path, text)


# ── Sony PTP command ─────────────────────────────────────────────────────────
path = "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
text = read(path)
old = '''    /**
     * Diagnostic for A7C II: move the Sony logical AF position to the center,
     * then briefly half-press AF. The camera must be in an AF-capable focus
     * mode and normally a Spot/Flexible-Spot style focus area for D2DC to have
     * a visible effect.
     */
    fun testAfCenter(): String {
        val setResult = setAfAreaPosition(320, 240)
        Thread.sleep(120)
        val pressResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 2)
        Thread.sleep(450)
        val releaseResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 1)
        return buildString {
            append("AF CENTER TEST x=320 y=240")
            append(" | D2DC/9207=")
            append(PtpConstants.responseCodeName(setResult.responseCode))
            append(" | halfPress=")
            append(PtpConstants.responseCodeName(pressResult.responseCode))
            append(" | release=")
            append(PtpConstants.responseCodeName(releaseResult.responseCode))
        }
    }
'''
new = '''    /**
     * Move the Sony logical AF target and immediately trigger a short AF
     * half-press. A7C II uses a 640x480 logical grid for D2DC.
     */
    fun setAfPoint(x: Int, y: Int): String = commandAfPoint("AF TARGET", x, y)

    /** Diagnostic convenience entry point retained by the demo. */
    fun testAfCenter(): String = commandAfPoint("AF CENTER TEST", 320, 240)

    private fun commandAfPoint(label: String, x: Int, y: Int): String {
        val safeX = x.coerceIn(0, 639)
        val safeY = y.coerceIn(0, 479)
        val setResult = setAfAreaPosition(safeX, safeY)
        Thread.sleep(120)
        val pressResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 2)
        Thread.sleep(450)
        val releaseResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 1)
        return buildString {
            append(label).append(" x=").append(safeX).append(" y=").append(safeY)
            append(" | D2DC/9207=")
            append(PtpConstants.responseCodeName(setResult.responseCode))
            append(" | halfPress=")
            append(PtpConstants.responseCodeName(pressResult.responseCode))
            append(" | release=")
            append(PtpConstants.responseCodeName(releaseResult.responseCode))
        }
    }
'''
text = replace_once(text, old, new, "SonyPtpCamera.setAfPoint")
write(path, text)


# ── Full landscape camera UI ─────────────────────────────────────────────────
camera_screen = r'''package io.github.gallo.sonycamera.demo

import android.graphics.Bitmap
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.matchParentSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.gallo.sonycamera.CameraConnectionState
import io.github.gallo.sonycamera.CameraEvent
import io.github.gallo.sonycamera.CameraOperationResult
import io.github.gallo.sonycamera.service.CameraConnectionClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.min

private val Accent = Color(0xFFFF5A3C)
private val AfGreen = Color(0xFF36D399)
private val DebugGold = Color(0xFFFFD166)
private val Ink = Color(0xFF08090B)
private val Rail = Color(0xFF111216)

/** Landscape-first camera controller with touch AF. */
@Composable
fun CameraScreen(camera: CameraConnectionClient) {
    MaterialTheme(colorScheme = darkColorScheme(primary = Accent, background = Ink)) {
        Surface(color = Ink, modifier = Modifier.fillMaxSize()) {
            val scope = rememberCoroutineScope()
            val state by camera.connectionState.collectAsStateWithLifecycle()
            val name by camera.cameraName.collectAsStateWithLifecycle()

            var frame by remember { mutableStateOf<Bitmap?>(null) }
            var captured by remember { mutableStateOf<Bitmap?>(null) }
            var flash by remember { mutableStateOf(false) }
            var lastError by remember { mutableStateOf<String?>(null) }
            var focusAreaCode by remember { mutableStateOf<Int?>(null) }
            var focusDebug by remember { mutableStateOf("waiting for first AF probe…") }
            var focusEventCount by remember { mutableStateOf(0) }
            var liveFrameCount by remember { mutableStateOf(0L) }
            var afTargetX by remember { mutableStateOf<Int?>(null) }
            var afTargetY by remember { mutableStateOf<Int?>(null) }
            var afBusy by remember { mutableStateOf(false) }

            LaunchedEffect(camera) {
                camera.liveviewFrames.collect { bitmap ->
                    frame = bitmap
                    liveFrameCount++
                }
            }

            LaunchedEffect(camera) {
                camera.events.collect { event ->
                    when (event) {
                        is CameraEvent.PhotoCaptured -> captured = event.bitmap
                        is CameraEvent.ShutterFired -> flash = true
                        is CameraEvent.FocusAreaUpdated -> {
                            focusAreaCode = event.rawValue
                            focusEventCount++
                        }
                        is CameraEvent.FocusDebug -> focusDebug = event.message
                        is CameraEvent.AfTargetUpdated -> {
                            afTargetX = event.x
                            afTargetY = event.y
                        }
                        is CameraEvent.Error -> lastError = event.message
                        is CameraEvent.ConnectionLost -> lastError = "Connection lost"
                    }
                }
            }

            LaunchedEffect(state) {
                if (state !is CameraConnectionState.Ready) {
                    focusAreaCode = null
                    afTargetX = null
                    afTargetY = null
                }
            }

            LaunchedEffect(flash) {
                if (flash) {
                    delay(60)
                    flash = false
                }
            }

            fun requestAf(x: Int, y: Int, centerTest: Boolean = false) {
                if (afBusy || state !is CameraConnectionState.Ready) return
                afTargetX = x.coerceIn(0, 639)
                afTargetY = y.coerceIn(0, 479)
                afBusy = true
                scope.launch {
                    val result = if (centerTest) camera.testAfCenter() else camera.setAfPoint(x, y)
                    if (result is CameraOperationResult.Failure) lastError = result.message
                    afBusy = false
                }
            }

            Box(Modifier.fillMaxSize()) {
                Row(Modifier.fillMaxSize()) {
                    LeftRail(
                        state = state,
                        cameraName = name,
                        liveFrameCount = liveFrameCount,
                        frame = frame,
                        focusAreaCode = focusAreaCode,
                        focusEventCount = focusEventCount,
                        focusDebug = focusDebug,
                        afTargetX = afTargetX,
                        afTargetY = afTargetY,
                        onConnect = { lastError = null; camera.connectToCamera() },
                        onDisconnect = { camera.disconnect() },
                        modifier = Modifier.width(218.dp).fillMaxHeight()
                    )

                    PreviewPane(
                        state = state,
                        frame = frame,
                        afTargetX = afTargetX,
                        afTargetY = afTargetY,
                        afBusy = afBusy,
                        onAfPoint = { x, y -> requestAf(x, y) },
                        modifier = Modifier.weight(1f).fillMaxHeight()
                    )

                    RightRail(
                        state = state,
                        afBusy = afBusy,
                        onAfCenter = { requestAf(320, 240, centerTest = true) },
                        onCapture = { scope.launch { camera.takePhoto() } },
                        onConnect = { lastError = null; camera.connectToCamera() },
                        modifier = Modifier.width(152.dp).fillMaxHeight()
                    )
                }

                val flashAlpha by animateFloatAsState(
                    targetValue = if (flash) 0.85f else 0f,
                    animationSpec = tween(durationMillis = if (flash) 0 else 220),
                    label = "flash"
                )
                if (flashAlpha > 0f) {
                    Box(Modifier.fillMaxSize().background(Color.White.copy(alpha = flashAlpha)))
                }

                lastError?.let { msg ->
                    LaunchedEffect(msg) { delay(3500); lastError = null }
                    Text(
                        text = msg,
                        color = Color.White,
                        fontSize = 13.sp,
                        modifier = Modifier
                            .align(Alignment.TopCenter)
                            .padding(top = 18.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(Accent.copy(alpha = 0.92f))
                            .padding(horizontal = 14.dp, vertical = 8.dp)
                    )
                }

                AnimatedVisibility(
                    visible = captured != null,
                    enter = fadeIn(),
                    exit = fadeOut()
                ) {
                    captured?.let { shot ->
                        CapturedReview(shot, onDismiss = { captured = null })
                    }
                }
            }
        }
    }
}

@Composable
private fun PreviewPane(
    state: CameraConnectionState,
    frame: Bitmap?,
    afTargetX: Int?,
    afTargetY: Int?,
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
                    val sonyX = (nx * 639f).toInt().coerceIn(0, 639)
                    val sonyY = (ny * 479f).toInt().coerceIn(0, 479)
                    onAfPoint(sonyX, sonyY)
                }
            }
    ) {
        if (frame != null) {
            Image(
                bitmap = frame.asImageBitmap(),
                contentDescription = "Live view — tap to focus",
                contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize()
            )

            if (afTargetX != null && afTargetY != null && containerSize != IntSize.Zero) {
                AfTargetOverlay(
                    bitmap = frame,
                    containerSize = containerSize,
                    x = afTargetX,
                    y = afTargetY,
                    modifier = Modifier.matchParentSize()
                )
            }

            Text(
                text = if (afBusy) "Focusing…" else "Tap preview to focus",
                color = Color.White,
                fontSize = 11.sp,
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = 10.dp)
                    .clip(RoundedCornerShape(14.dp))
                    .background(Color.Black.copy(alpha = 0.55f))
                    .padding(horizontal = 12.dp, vertical = 6.dp)
            )
        } else {
            PreviewPlaceholder(state)
        }
    }
}

@Composable
private fun AfTargetOverlay(
    bitmap: Bitmap,
    containerSize: IntSize,
    x: Int,
    y: Int,
    modifier: Modifier = Modifier
) {
    Canvas(modifier = modifier) {
        val rect = fittedImageRect(containerSize, bitmap.width, bitmap.height)
        if (rect.width <= 0f || rect.height <= 0f) return@Canvas

        val px = rect.left + rect.width * (x.coerceIn(0, 639) / 639f)
        val py = rect.top + rect.height * (y.coerceIn(0, 479) / 479f)

        // Visual target area: 64x48 units on Sony's 640x480 logical grid.
        // This is the app-commanded target box, not a camera-readback of D22C.
        val areaW = rect.width * (64f / 640f)
        val areaH = rect.height * (48f / 480f)
        val left = (px - areaW / 2f).coerceIn(rect.left, rect.right - areaW)
        val top = (py - areaH / 2f).coerceIn(rect.top, rect.bottom - areaH)

        drawRect(
            color = AfGreen,
            topLeft = Offset(left, top),
            size = Size(areaW, areaH),
            style = Stroke(width = 2.5.dp.toPx())
        )
        drawCircle(AfGreen, radius = 4.dp.toPx(), center = Offset(px, py))
        drawLine(
            color = AfGreen,
            start = Offset(px - 10.dp.toPx(), py),
            end = Offset(px + 10.dp.toPx(), py),
            strokeWidth = 1.5.dp.toPx()
        )
        drawLine(
            color = AfGreen,
            start = Offset(px, py - 10.dp.toPx()),
            end = Offset(px, py + 10.dp.toPx()),
            strokeWidth = 1.5.dp.toPx()
        )
    }
}

private fun fittedImageRect(container: IntSize, imageWidth: Int, imageHeight: Int): Rect {
    if (container.width <= 0 || container.height <= 0 || imageWidth <= 0 || imageHeight <= 0) {
        return Rect.Zero
    }
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
private fun LeftRail(
    state: CameraConnectionState,
    cameraName: String?,
    liveFrameCount: Long,
    frame: Bitmap?,
    focusAreaCode: Int?,
    focusEventCount: Int,
    focusDebug: String,
    afTargetX: Int?,
    afTargetY: Int?,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier.background(Rail).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        StatusBlock(state, cameraName)

        Text("AF TARGET", color = DebugGold, fontSize = 10.sp, fontWeight = FontWeight.Bold)
        Text(
            if (afTargetX != null && afTargetY != null) "X $afTargetX   Y $afTargetY" else "Tap preview to set",
            color = Color.White,
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold
        )
        Text(
            "Area: ${focusAreaCode?.let(::focusAreaLabel) ?: "commanded Spot target"}",
            color = Color.White.copy(alpha = 0.68f),
            fontSize = 10.sp
        )

        Box(Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(alpha = 0.10f)))

        Text("AF DEBUG", color = DebugGold, fontSize = 10.sp, fontWeight = FontWeight.Bold)
        Text(
            "frames=$liveFrameCount  ${frame?.width ?: 0}x${frame?.height ?: 0}  areaEvents=$focusEventCount",
            color = Color.White.copy(alpha = 0.85f),
            fontSize = 9.sp
        )
        Text(
            focusDebug,
            color = Color.White.copy(alpha = 0.58f),
            fontSize = 8.sp,
            maxLines = 9
        )

        Spacer(Modifier.weight(1f))

        when (state) {
            is CameraConnectionState.Ready -> TextButton(onClick = onDisconnect, modifier = Modifier.fillMaxWidth()) {
                Text("Disconnect", color = Color.White.copy(alpha = 0.72f))
            }
            is CameraConnectionState.Disconnected,
            is CameraConnectionState.Error -> Button(
                onClick = onConnect,
                colors = ButtonDefaults.buttonColors(containerColor = Accent),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Connect")
            }
            else -> CircularProgressIndicator(
                color = Accent,
                modifier = Modifier.align(Alignment.CenterHorizontally).size(28.dp)
            )
        }
    }
}

@Composable
private fun RightRail(
    state: CameraConnectionState,
    afBusy: Boolean,
    onAfCenter: () -> Unit,
    onCapture: () -> Unit,
    onConnect: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier.background(Rail).padding(horizontal = 12.dp, vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        if (state is CameraConnectionState.Ready) {
            Button(
                onClick = onAfCenter,
                enabled = !afBusy,
                colors = ButtonDefaults.buttonColors(containerColor = Color.White.copy(alpha = 0.13f)),
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth().height(44.dp)
            ) {
                Text("AF CENTER", color = Color.White, fontSize = 10.sp, fontWeight = FontWeight.Bold)
            }

            Spacer(Modifier.weight(1f))
            ShutterButton(onClick = onCapture)
            Text("CAPTURE", color = Color.White.copy(alpha = 0.55f), fontSize = 9.sp)
            Spacer(Modifier.weight(1f))
        } else if (state is CameraConnectionState.Disconnected || state is CameraConnectionState.Error) {
            Spacer(Modifier.weight(1f))
            Button(
                onClick = onConnect,
                colors = ButtonDefaults.buttonColors(containerColor = Accent),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Connect", fontSize = 11.sp)
            }
            Spacer(Modifier.weight(1f))
        } else {
            Spacer(Modifier.weight(1f))
            CircularProgressIndicator(color = Accent, modifier = Modifier.size(30.dp))
            Spacer(Modifier.weight(1f))
        }
    }
}

@Composable
private fun StatusBlock(state: CameraConnectionState, cameraName: String?) {
    val (dot, label) = when (state) {
        is CameraConnectionState.Ready -> AfGreen to (cameraName ?: "Connected")
        is CameraConnectionState.Connecting -> DebugGold to "Connecting"
        is CameraConnectionState.Initializing -> DebugGold to "Initializing"
        is CameraConnectionState.Scanning -> DebugGold to "Scanning"
        is CameraConnectionState.Error -> Accent to "Error"
        is CameraConnectionState.Disconnected -> Color(0xFF7A7A85) to "Disconnected"
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(9.dp).clip(CircleShape).background(dot))
        Spacer(Modifier.width(8.dp))
        Text(label, color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
    }
}

private fun focusAreaLabel(code: Int): String = when (code) {
    0x0001 -> "Wide"
    0x0002 -> "Zone"
    0x0003 -> "Center"
    0x0101 -> "Flexible Spot S"
    0x0102 -> "Flexible Spot M"
    0x0103 -> "Flexible Spot L"
    0x0104 -> "Expand Flexible Spot"
    0x0201 -> "Lock-on: Wide"
    0x0202 -> "Lock-on: Zone"
    0x0203 -> "Lock-on: Center"
    0x0204 -> "Lock-on: Flexible Spot S"
    0x0205 -> "Lock-on: Flexible Spot M"
    0x0206 -> "Lock-on: Flexible Spot L"
    0x0207 -> "Lock-on: Expand Flexible Spot"
    else -> "Unknown 0x${code.toString(16).uppercase().padStart(4, '0')}"
}

@Composable
private fun PreviewPlaceholder(state: CameraConnectionState) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        when (state) {
            is CameraConnectionState.Ready ->
                Text("Waiting for live view…", color = Color.White.copy(alpha = 0.6f), fontSize = 15.sp)
            is CameraConnectionState.Connecting,
            is CameraConnectionState.Initializing,
            is CameraConnectionState.Scanning -> CircularProgressIndicator(color = Accent)
            is CameraConnectionState.Error -> Text(
                state.message,
                color = Color.White.copy(alpha = 0.75f),
                fontSize = 13.sp,
                modifier = Modifier.padding(horizontal = 30.dp)
            )
            is CameraConnectionState.Disconnected -> Text(
                "Plug in a Sony camera over USB",
                color = Color.White.copy(alpha = 0.65f),
                fontSize = 14.sp
            )
        }
    }
}

@Composable
private fun ShutterButton(onClick: () -> Unit) {
    var pressed by remember { mutableStateOf(false) }
    val scale by animateFloatAsState(if (pressed) 0.9f else 1f, label = "shutterScale")
    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier
            .size(90.dp)
            .clip(CircleShape)
            .background(Color.White.copy(alpha = 0.18f))
            .clickable {
                pressed = true
                onClick()
            }
    ) {
        Box(
            Modifier
                .size((74 * scale).dp)
                .clip(CircleShape)
                .background(Color.White)
        )
    }
    LaunchedEffect(pressed) {
        if (pressed) {
            delay(120)
            pressed = false
        }
    }
}

@Composable
private fun CapturedReview(shot: Bitmap, onDismiss: () -> Unit) {
    LaunchedEffect(shot) { delay(4000); onDismiss() }
    Box(
        Modifier.fillMaxSize().background(Color.Black).clickable(onClick = onDismiss),
        contentAlignment = Alignment.Center
    ) {
        Image(
            bitmap = shot.asImageBitmap(),
            contentDescription = "Captured photo",
            contentScale = ContentScale.Fit,
            modifier = Modifier.fillMaxSize().padding(8.dp)
        )
        Text(
            "Captured · tap to dismiss",
            color = Color.White,
            fontSize = 12.sp,
            fontFamily = FontFamily.SansSerif,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = 18.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(Color.White.copy(alpha = 0.14f))
                .padding(horizontal = 14.dp, vertical = 8.dp)
        )
    }
}
'''
write("demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt", camera_screen)


# ── Forced landscape + immersive fullscreen ─────────────────────────────────
main_activity = r'''package io.github.gallo.sonycamera.demo

import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import io.github.gallo.sonycamera.service.CameraConnectionClient

/** Single-activity landscape fullscreen camera controller. */
class MainActivity : ComponentActivity() {

    private val camera: CameraConnectionClient
        get() = (application as DemoApp).camera

    private val requestNotificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* ignored */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        enableEdgeToEdge()
        enterImmersiveMode()

        maybeRequestNotificationPermission()
        forwardUsbAttachIntent(intent)

        setContent {
            CameraScreen(camera = camera)
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) enterImmersiveMode()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        forwardUsbAttachIntent(intent)
    }

    private fun enterImmersiveMode() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    private fun forwardUsbAttachIntent(intent: Intent?) {
        if (intent?.action != UsbManager.ACTION_USB_DEVICE_ATTACHED) return
        val device: UsbDevice = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE) ?: return
        camera.onUsbDeviceAttached(device)
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) {
            requestNotificationPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
    }
}
'''
write("demo/src/main/java/io/github/gallo/sonycamera/demo/MainActivity.kt", main_activity)

manifest = r'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <application
        android:name=".DemoApp"
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="@string/app_name"
        android:roundIcon="@mipmap/ic_launcher"
        android:supportsRtl="true"
        android:theme="@style/Theme.SonyCameraDemo">

        <activity
            android:name=".MainActivity"
            android:configChanges="orientation|screenSize|keyboardHidden"
            android:exported="true"
            android:launchMode="singleTop"
            android:screenOrientation="sensorLandscape"
            android:theme="@style/Theme.SonyCameraDemo">

            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>

            <intent-filter>
                <action android:name="android.hardware.usb.action.USB_DEVICE_ATTACHED" />
            </intent-filter>
            <meta-data
                android:name="android.hardware.usb.action.USB_DEVICE_ATTACHED"
                android:resource="@xml/sony_usb_device_filter" />
        </activity>
    </application>

</manifest>
'''
write("demo/src/main/AndroidManifest.xml", manifest)


# Remove one-shot machinery from the source commit produced by the workflow.
Path(".github/workflows/apply-landscape-touch-af-once.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)

print("Landscape touch AF patch applied")
