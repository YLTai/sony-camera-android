package io.github.gallo.sonycamera.demo

import android.graphics.Bitmap
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.gallo.sonycamera.CameraConnectionState
import io.github.gallo.sonycamera.CameraEvent
import io.github.gallo.sonycamera.service.CameraConnectionClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val Accent = Color(0xFFFF5A3C)
private val Ink = Color(0xFF0B0B0E)

/**
 * The entire demo UI: live preview, a status pill, a shutter button, and a
 * captured-photo review overlay. Everything is driven off the library's flows.
 */
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

            // Live-view frames stream in as Bitmaps.
            LaunchedEffect(camera) {
                camera.liveviewFrames.collect { bitmap ->
                    frame = bitmap
                    liveFrameCount++
                }
            }
            // One-shot events: capture, shutter flash, transient errors.
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
                        is CameraEvent.Error -> lastError = event.message
                        is CameraEvent.ConnectionLost -> lastError = "Connection lost"
                    }
                }
            }
            // Never keep stale AF state visible after a disconnect/reconnect transition.
            LaunchedEffect(state) {
                if (state !is CameraConnectionState.Ready) focusAreaCode = null
            }

            // Auto-clear the white flash overlay.
            LaunchedEffect(flash) {
                if (flash) { delay(60); flash = false }
            }

            Box(Modifier.fillMaxSize()) {
                // ── Live preview ──────────────────────────────────────────────
                val f = frame
                if (f != null) {
                    Image(
                        bitmap = f.asImageBitmap(),
                        contentDescription = "Live view",
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize()
                    )
                } else {
                    PreviewPlaceholder(state)
                }

                // ILCE-6600/PTP2 exposes the focus-area MODE via 0xD22C, but not
                // an arbitrary live AF-frame XY coordinate. Center is deterministic.
                focusAreaCode?.let { code ->
                    if (code == 0x0003 && f != null) {
                        FocusPointOverlay(modifier = Modifier.align(Alignment.Center))
                    }

                    FocusAreaPill(
                        code = code,
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .systemBarsPadding()
                            .padding(start = 16.dp, top = 16.dp)
                    )
                }

                // Always-visible diagnostic panel while connected. This is
                // intentionally shown even when D22C cannot be parsed.
                if (state is CameraConnectionState.Ready) {
                    FocusDebugPanel(
                        frame = f,
                        liveFrameCount = liveFrameCount,
                        focusAreaCode = focusAreaCode,
                        focusEventCount = focusEventCount,
                        debug = focusDebug,
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .systemBarsPadding()
                            .fillMaxWidth()
                            .padding(start = 12.dp, end = 12.dp, top = 66.dp)
                    )
                }

                // ── Shutter flash ─────────────────────────────────────────────
                val flashAlpha by animateFloatAsState(
                    targetValue = if (flash) 0.85f else 0f,
                    animationSpec = tween(durationMillis = if (flash) 0 else 220),
                    label = "flash"
                )
                if (flashAlpha > 0f) {
                    Box(
                        Modifier
                            .fillMaxSize()
                            .background(Color.White.copy(alpha = flashAlpha))
                    )
                }

                // ── Top status pill ───────────────────────────────────────────
                StatusPill(
                    state = state,
                    cameraName = name,
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .systemBarsPadding()
                        .padding(top = 16.dp)
                )

                // ── Bottom controls ───────────────────────────────────────────
                Controls(
                    state = state,
                    onConnect = { lastError = null; camera.connectToCamera() },
                    onCapture = { scope.launch { camera.takePhoto() } },
                    onDisconnect = { camera.disconnect() },
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .systemBarsPadding()
                        .padding(bottom = 28.dp)
                )

                // ── Transient error toast ─────────────────────────────────────
                lastError?.let { msg ->
                    LaunchedEffect(msg) { delay(3500); lastError = null }
                    Text(
                        text = msg,
                        color = Color.White,
                        fontSize = 13.sp,
                        modifier = Modifier
                            .align(Alignment.TopCenter)
                            .systemBarsPadding()
                            .padding(top = 72.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(Accent.copy(alpha = 0.92f))
                            .padding(horizontal = 14.dp, vertical = 8.dp)
                    )
                }

                // ── Captured photo review overlay ─────────────────────────────
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
private fun FocusDebugPanel(
    frame: Bitmap?,
    liveFrameCount: Long,
    focusAreaCode: Int?,
    focusEventCount: Int,
    debug: String,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(Color.Black.copy(alpha = 0.72f))
            .padding(horizontal = 10.dp, vertical = 7.dp)
    ) {
        Text(
            "AF DEBUG",
            color = Color(0xFFFFD166),
            fontSize = 10.sp,
            fontWeight = FontWeight.Bold
        )
        Text(
            "frames=$liveFrameCount  size=${frame?.width ?: 0}x${frame?.height ?: 0}  " +
                "areaEvents=$focusEventCount",
            color = Color.White,
            fontSize = 9.sp
        )
        Text(
            "area=${focusAreaCode?.let { focusAreaLabel(it) + " / 0x%04X".format(it) } ?: "NONE"}",
            color = if (focusAreaCode == null) Color(0xFFFFC857) else Color(0xFF36D399),
            fontSize = 9.sp
        )
        Text(
            debug,
            color = Color.White.copy(alpha = 0.78f),
            fontSize = 8.sp,
            maxLines = 4
        )
    }
}

@Composable
private fun FocusPointOverlay(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .size(width = 72.dp, height = 52.dp)
            .border(2.dp, Accent, RoundedCornerShape(6.dp))
    ) {
        Box(
            modifier = Modifier
                .align(Alignment.Center)
                .size(5.dp)
                .background(Accent, CircleShape)
        )
    }
}

@Composable
private fun FocusAreaPill(code: Int, modifier: Modifier = Modifier) {
    val isCenter = code == 0x0003

    Column(
        modifier = modifier
            .clip(RoundedCornerShape(10.dp))
            .background(Color.Black.copy(alpha = 0.62f))
            .padding(horizontal = 12.dp, vertical = 8.dp)
    ) {
        Text(
            text = "AF area: ${focusAreaLabel(code)}",
            color = Color.White,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold
        )
        Text(
            text = if (isCenter) "AF point: center" else "AF point: unavailable over PTP2",
            color = Color.White.copy(alpha = 0.72f),
            fontSize = 10.sp
        )
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
    else -> "Unknown (0x${code.toString(16).uppercase().padStart(4, '0')})"
}

@Composable
private fun PreviewPlaceholder(state: CameraConnectionState) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        when (state) {
            is CameraConnectionState.Ready ->
                Text("Waiting for live view…", color = Color.White.copy(0.6f), fontSize = 15.sp)
            is CameraConnectionState.Connecting,
            is CameraConnectionState.Initializing,
            is CameraConnectionState.Scanning ->
                CircularProgressIndicator(color = Accent)
            is CameraConnectionState.Error ->
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("⚠", fontSize = 40.sp)
                    Spacer(Modifier.height(12.dp))
                    Text(
                        state.message,
                        color = Color.White.copy(0.75f),
                        fontSize = 14.sp,
                        modifier = Modifier.padding(horizontal = 40.dp)
                    )
                }
            is CameraConnectionState.Disconnected ->
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("📷", fontSize = 52.sp)
                    Spacer(Modifier.height(14.dp))
                    Text(
                        "Plug in a Sony camera over USB",
                        color = Color.White.copy(0.65f),
                        fontSize = 15.sp
                    )
                }
        }
    }
}

@Composable
private fun StatusPill(
    state: CameraConnectionState,
    cameraName: String?,
    modifier: Modifier = Modifier
) {
    val (dot, label) = when (state) {
        is CameraConnectionState.Ready -> Color(0xFF36D399) to (cameraName ?: "Connected")
        is CameraConnectionState.Connecting -> Color(0xFFFFC857) to "Connecting"
        is CameraConnectionState.Initializing -> Color(0xFFFFC857) to "Initializing"
        is CameraConnectionState.Scanning -> Color(0xFFFFC857) to "Scanning"
        is CameraConnectionState.Error -> Accent to "Error"
        is CameraConnectionState.Disconnected -> Color(0xFF7A7A85) to "Disconnected"
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .clip(CircleShape)
            .background(Color.Black.copy(alpha = 0.55f))
            .padding(horizontal = 16.dp, vertical = 9.dp)
    ) {
        Box(Modifier.size(9.dp).clip(CircleShape).background(dot))
        Spacer(Modifier.width(9.dp))
        Text(label, color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun Controls(
    state: CameraConnectionState,
    onConnect: () -> Unit,
    onCapture: () -> Unit,
    onDisconnect: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(14.dp),
        modifier = modifier.fillMaxWidth()
    ) {
        when (state) {
            is CameraConnectionState.Ready -> {
                ShutterButton(onClick = onCapture)
                TextButton(onClick = onDisconnect) {
                    Text("Disconnect", color = Color.White.copy(0.7f), fontSize = 13.sp)
                }
            }
            is CameraConnectionState.Disconnected,
            is CameraConnectionState.Error -> {
                Button(
                    onClick = onConnect,
                    colors = ButtonDefaults.buttonColors(containerColor = Accent),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.height(54.dp).width(220.dp)
                ) {
                    Text("Connect camera", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                }
            }
            else -> {
                // Connecting / Initializing / Scanning — a connection is in flight.
                CircularProgressIndicator(color = Accent, modifier = Modifier.size(34.dp))
            }
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
            .size(82.dp)
            .clip(CircleShape)
            .background(Color.White.copy(alpha = 0.18f))
            .clickable {
                pressed = true
                onClick()
            }
    ) {
        // Inner solid disc.
        Box(
            Modifier
                .size((68 * scale).dp)
                .clip(CircleShape)
                .background(Color.White)
        )
    }
    LaunchedEffect(pressed) {
        if (pressed) { delay(120); pressed = false }
    }
}

@Composable
private fun CapturedReview(shot: Bitmap, onDismiss: () -> Unit) {
    // Auto-dismiss after a few seconds; tap to dismiss sooner.
    LaunchedEffect(shot) { delay(4000); onDismiss() }
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black)
            .clickable(onClick = onDismiss),
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
                .systemBarsPadding()
                .padding(bottom = 24.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(Color.White.copy(alpha = 0.14f))
                .padding(horizontal = 14.dp, vertical = 8.dp)
        )
    }
}
