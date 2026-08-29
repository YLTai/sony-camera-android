package io.github.gallo.sonycamera.demo

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.withContext
import kotlin.math.max
import kotlin.math.roundToInt

private val ToolAccent = Color(0xFFFF4D3D)

/**
 * Monitor-side zebra stripes generated from the original live-view frame.
 * This intentionally runs before any user LUT so the exposure warning keeps
 * describing the camera feed rather than the creative look.
 */
@Composable
internal fun ZebraOverlay(
    source: Bitmap?,
    thresholdPercent: Int?,
    modifier: Modifier = Modifier
) {
    val latestSource = remember { MutableStateFlow<Bitmap?>(null) }
    var overlay by remember { mutableStateOf<Bitmap?>(null) }

    SideEffect { latestSource.value = source }

    LaunchedEffect(thresholdPercent) {
        if (thresholdPercent == null) {
            overlay = null
            return@LaunchedEffect
        }
        latestSource.filterNotNull().collect { bitmap ->
            overlay = withContext(Dispatchers.Default) {
                createZebraMask(bitmap, thresholdPercent)
            }
            // Zebra does not need to be regenerated for every USB frame.
            delay(45)
        }
    }

    if (thresholdPercent != null) {
        overlay?.let { mask ->
            Image(
                bitmap = mask.asImageBitmap(),
                contentDescription = "Zebra exposure warning",
                contentScale = ContentScale.Fit,
                modifier = modifier
            )
        }
    }
}

private fun createZebraMask(source: Bitmap, thresholdPercent: Int): Bitmap {
    val sample = if (source.width >= 900 || source.height >= 600) 2 else 1
    val outWidth = max(1, source.width / sample)
    val outHeight = max(1, source.height / sample)
    val sourcePixels = IntArray(source.width * source.height)
    source.getPixels(sourcePixels, 0, source.width, 0, 0, source.width, source.height)
    val output = IntArray(outWidth * outHeight)

    // 100% zebra should still be useful on an 8-bit JPEG feed, where exact
    // 255 values can be rare after picture-profile processing.
    val threshold = ((thresholdPercent.coerceIn(50, 100) / 100f) * 255f)
        .roundToInt()
        .coerceAtMost(if (thresholdPercent >= 100) 250 else 255)

    for (oy in 0 until outHeight) {
        val sy = (oy * sample).coerceAtMost(source.height - 1)
        val sourceRow = sy * source.width
        val outputRow = oy * outWidth
        for (ox in 0 until outWidth) {
            val sx = (ox * sample).coerceAtMost(source.width - 1)
            val color = sourcePixels[sourceRow + sx]
            val r = (color ushr 16) and 0xFF
            val g = (color ushr 8) and 0xFF
            val b = color and 0xFF
            val luma = (r * 54 + g * 183 + b * 19) ushr 8
            if (luma >= threshold) {
                // Alternating diagonal lines create a conventional zebra look
                // while keeping the underlying image visible.
                val stripe = ((ox + oy) / 4) and 1
                output[outputRow + ox] = if (stripe == 0) 0xD9FFFFFF.toInt() else 0x38000000
            }
        }
    }
    return Bitmap.createBitmap(output, outWidth, outHeight, Bitmap.Config.ARGB_8888)
}

private data class HistogramSnapshot(
    val bins: IntArray,
    val sampledPixels: Int
)

/** Compact luma histogram, calculated from the original (pre-LUT) frame. */
@Composable
internal fun LumaHistogramOverlay(
    source: Bitmap?,
    enabled: Boolean,
    modifier: Modifier = Modifier
) {
    val latestSource = remember { MutableStateFlow<Bitmap?>(null) }
    var histogram by remember { mutableStateOf<HistogramSnapshot?>(null) }

    SideEffect { latestSource.value = source }

    LaunchedEffect(enabled) {
        if (!enabled) {
            histogram = null
            return@LaunchedEffect
        }
        latestSource.filterNotNull().collect { bitmap ->
            histogram = withContext(Dispatchers.Default) { calculateHistogram(bitmap) }
            // 6-8 analysis updates/sec feels continuous but costs much less
            // than analysing every incoming preview frame.
            delay(110)
        }
    }

    if (!enabled) return
    val data = histogram ?: return

    Box(
        modifier = modifier
            .width(188.dp)
            .height(82.dp)
            .clip(RoundedCornerShape(6.dp))
            .background(Color.Black.copy(alpha = 0.62f))
            .padding(horizontal = 7.dp, vertical = 6.dp)
    ) {
        Canvas(Modifier.matchParentSize()) {
            val peak = data.bins.maxOrNull()?.coerceAtLeast(1) ?: 1
            val usableHeight = size.height - 3.dp.toPx()
            val stroke = max(1f, size.width / 256f)

            for (i in 0..255) {
                val x = (i / 255f) * size.width
                // sqrt-ish compression without the expense of a logarithm:
                // a half-height linear component keeps low populations visible.
                val normalized = data.bins[i].toFloat() / peak.toFloat()
                val h = usableHeight * (0.35f * normalized + 0.65f * kotlin.math.sqrt(normalized))
                drawLine(
                    color = Color.White.copy(alpha = 0.82f),
                    start = androidx.compose.ui.geometry.Offset(x, size.height),
                    end = androidx.compose.ui.geometry.Offset(x, size.height - h),
                    strokeWidth = stroke
                )
            }

            val clipLimit = (data.sampledPixels * 0.0025f).roundToInt().coerceAtLeast(2)
            if (data.bins.take(3).sum() > clipLimit) {
                drawLine(
                    ToolAccent,
                    androidx.compose.ui.geometry.Offset(1.dp.toPx(), 0f),
                    androidx.compose.ui.geometry.Offset(1.dp.toPx(), size.height),
                    2.dp.toPx()
                )
            }
            if (data.bins.takeLast(3).sum() > clipLimit) {
                drawLine(
                    ToolAccent,
                    androidx.compose.ui.geometry.Offset(size.width - 1.dp.toPx(), 0f),
                    androidx.compose.ui.geometry.Offset(size.width - 1.dp.toPx(), size.height),
                    2.dp.toPx()
                )
            }
        }
    }
}

private fun calculateHistogram(source: Bitmap): HistogramSnapshot {
    val pixels = IntArray(source.width * source.height)
    source.getPixels(pixels, 0, source.width, 0, 0, source.width, source.height)
    val bins = IntArray(256)
    val stride = when {
        pixels.size > 1_200_000 -> 4
        pixels.size > 500_000 -> 2
        else -> 1
    }
    var sampled = 0
    var i = 0
    while (i < pixels.size) {
        val color = pixels[i]
        val r = (color ushr 16) and 0xFF
        val g = (color ushr 8) and 0xFF
        val b = color and 0xFF
        val luma = (r * 54 + g * 183 + b * 19) ushr 8
        bins[luma]++
        sampled++
        i += stride
    }
    return HistogramSnapshot(bins, sampled)
}


/** Composition guides are monitor-only and never alter the camera image. */
internal enum class CompositionGuide(val label: String) {
    OFF("OFF"),
    THIRDS("3×3"),
    GOLDEN("GOLD"),
    CENTER("CROSS"),
    DIAGONALS("DIAG"),
    SAFE("SAFE")
}

internal fun nextCompositionGuide(current: CompositionGuide): CompositionGuide {
    val values = CompositionGuide.entries
    return values[(current.ordinal + 1) % values.size]
}

/**
 * Draw a composition overlay inside the fitted live-view image rect. Keeping
 * this layer outside PreviewPane's magnification transform means guides remain
 * a stable monitor reference while the operator punches in for focus.
 */
@Composable
internal fun CompositionGuideOverlay(
    source: Bitmap?,
    guide: CompositionGuide,
    modifier: Modifier = Modifier
) {
    if (guide == CompositionGuide.OFF || source == null || source.width <= 0 || source.height <= 0) return

    Canvas(modifier) {
        if (size.width <= 0f || size.height <= 0f) return@Canvas
        val sourceAspect = source.width.toFloat() / source.height.toFloat()
        val canvasAspect = size.width / size.height
        val imageWidth: Float
        val imageHeight: Float
        if (canvasAspect > sourceAspect) {
            imageHeight = size.height
            imageWidth = imageHeight * sourceAspect
        } else {
            imageWidth = size.width
            imageHeight = imageWidth / sourceAspect
        }
        val left = (size.width - imageWidth) / 2f
        val top = (size.height - imageHeight) / 2f
        val right = left + imageWidth
        val bottom = top + imageHeight
        val lineColor = Color.White.copy(alpha = 0.56f)
        val secondary = Color.Black.copy(alpha = 0.42f)
        val thin = 1.dp.toPx()

        fun guideLine(x1: Float, y1: Float, x2: Float, y2: Float) {
            // A subtle dark under-stroke keeps white guides legible on highlights.
            drawLine(secondary, androidx.compose.ui.geometry.Offset(x1, y1), androidx.compose.ui.geometry.Offset(x2, y2), thin * 2.4f)
            drawLine(lineColor, androidx.compose.ui.geometry.Offset(x1, y1), androidx.compose.ui.geometry.Offset(x2, y2), thin)
        }

        when (guide) {
            CompositionGuide.OFF -> Unit
            CompositionGuide.THIRDS -> {
                for (fraction in listOf(1f / 3f, 2f / 3f)) {
                    val x = left + imageWidth * fraction
                    val y = top + imageHeight * fraction
                    guideLine(x, top, x, bottom)
                    guideLine(left, y, right, y)
                }
            }
            CompositionGuide.GOLDEN -> {
                for (fraction in listOf(0.382f, 0.618f)) {
                    val x = left + imageWidth * fraction
                    val y = top + imageHeight * fraction
                    guideLine(x, top, x, bottom)
                    guideLine(left, y, right, y)
                }
            }
            CompositionGuide.CENTER -> {
                val cx = (left + right) / 2f
                val cy = (top + bottom) / 2f
                guideLine(cx, top, cx, bottom)
                guideLine(left, cy, right, cy)
                val arm = minOf(imageWidth, imageHeight) * 0.035f
                guideLine(cx - arm, cy, cx + arm, cy)
                guideLine(cx, cy - arm, cx, cy + arm)
            }
            CompositionGuide.DIAGONALS -> {
                guideLine(left, top, right, bottom)
                guideLine(right, top, left, bottom)
            }
            CompositionGuide.SAFE -> {
                val insetX = imageWidth * 0.05f
                val insetY = imageHeight * 0.05f
                val l = left + insetX
                val r = right - insetX
                val t = top + insetY
                val b = bottom - insetY
                guideLine(l, t, r, t)
                guideLine(r, t, r, b)
                guideLine(r, b, l, b)
                guideLine(l, b, l, t)
            }
        }
    }
}
