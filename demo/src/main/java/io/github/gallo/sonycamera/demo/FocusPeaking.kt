package io.github.gallo.sonycamera.demo

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.withContext
import kotlin.math.abs
import kotlin.math.max

enum class PeakingLevel(val label: String, internal val threshold: Int?) {
    OFF("OFF", null),
    LOW("LOW", 195),
    MID("MID", 150),
    HIGH("HIGH", 108)
}

/**
 * Red focus peaking generated from the original live-view frame on-device.
 * LOW is selective, HIGH shows more edges. Incoming frames are conflated so
 * analysis never queues old frames behind the monitor display.
 */
@Composable
internal fun FocusPeakingOverlay(
    source: Bitmap?,
    level: PeakingLevel,
    modifier: Modifier = Modifier
) {
    val latestSource = remember { MutableStateFlow<Bitmap?>(null) }
    var overlay by remember { mutableStateOf<Bitmap?>(null) }

    SideEffect { latestSource.value = source }

    LaunchedEffect(level) {
        val threshold = level.threshold
        if (threshold == null) {
            overlay = null
            return@LaunchedEffect
        }
        latestSource.filterNotNull().collectLatest { bitmap ->
            overlay = withContext(Dispatchers.Default) { createPeakingMask(bitmap, threshold) }
        }
    }

    if (level != PeakingLevel.OFF) {
        overlay?.let { mask ->
            Image(
                bitmap = mask.asImageBitmap(),
                contentDescription = "Red focus peaking",
                contentScale = ContentScale.Fit,
                modifier = modifier
            )
        }
    }
}

private fun createPeakingMask(source: Bitmap, threshold: Int): Bitmap {
    val sample = if (source.width >= 640 || source.height >= 480) 2 else 1
    val width = max(1, source.width / sample)
    val height = max(1, source.height / sample)
    if (width < 3 || height < 3) {
        return Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
    }

    val fullPixels = IntArray(source.width * source.height)
    source.getPixels(fullPixels, 0, source.width, 0, 0, source.width, source.height)
    val pixels = IntArray(width * height)
    for (y in 0 until height) {
        val sy = (y * sample).coerceAtMost(source.height - 1)
        val sourceRow = sy * source.width
        val row = y * width
        for (x in 0 until width) {
            val sx = (x * sample).coerceAtMost(source.width - 1)
            pixels[row + x] = fullPixels[sourceRow + sx]
        }
    }
    val output = IntArray(width * height)

    fun luma(color: Int): Int {
        val r = (color ushr 16) and 0xFF
        val g = (color ushr 8) and 0xFF
        val b = color and 0xFF
        return (r * 77 + g * 150 + b * 29) ushr 8
    }

    for (y in 1 until height - 1) {
        val row = y * width
        for (x in 1 until width - 1) {
            val i = row + x
            val c = luma(pixels[i])
            val left = luma(pixels[i - 1])
            val right = luma(pixels[i + 1])
            val up = luma(pixels[i - width])
            val down = luma(pixels[i + width])

            val gradient = abs(right - left) + abs(down - up)
            val laplacian = abs(c * 4 - left - right - up - down)
            val score = gradient + laplacian * 2

            if (score > threshold && c > 12) {
                val alpha = (125 + (score - threshold) / 2).coerceIn(125, 235)
                output[i] = (alpha shl 24) or 0x00FF2F2F
            }
        }
    }

    return Bitmap.createBitmap(output, width, height, Bitmap.Config.ARGB_8888)
}
