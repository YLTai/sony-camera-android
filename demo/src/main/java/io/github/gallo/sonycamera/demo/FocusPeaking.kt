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
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.withContext
import kotlin.math.abs

/**
 * Lightweight red focus peaking generated from the live-view JPEG on-device.
 * The StateFlow intentionally conflates incoming frames while the previous mask
 * is being calculated so this assist feature never queues work behind liveview.
 */
@Composable
internal fun FocusPeakingOverlay(
    source: Bitmap?,
    enabled: Boolean,
    modifier: Modifier = Modifier
) {
    val latestSource = remember { MutableStateFlow<Bitmap?>(null) }
    var overlay by remember { mutableStateOf<Bitmap?>(null) }

    SideEffect { latestSource.value = source }

    LaunchedEffect(enabled) {
        if (!enabled) {
            overlay = null
            return@LaunchedEffect
        }
        latestSource.filterNotNull().collect { bitmap ->
            overlay = withContext(Dispatchers.Default) { createPeakingMask(bitmap) }
        }
    }

    if (enabled) {
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

private fun createPeakingMask(source: Bitmap): Bitmap {
    val width = source.width
    val height = source.height
    if (width < 3 || height < 3) {
        return Bitmap.createBitmap(width.coerceAtLeast(1), height.coerceAtLeast(1), Bitmap.Config.ARGB_8888)
    }

    val pixels = IntArray(width * height)
    val output = IntArray(width * height)
    source.getPixels(pixels, 0, width, 0, 0, width, height)

    fun luma(color: Int): Int {
        val r = (color ushr 16) and 0xFF
        val g = (color ushr 8) and 0xFF
        val b = color and 0xFF
        return (r * 77 + g * 150 + b * 29) ushr 8
    }

    // Laplacian + axial gradient: crisp high-frequency detail scores much
    // higher than soft transitions, which is exactly what focus peaking needs.
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

            if (score > 150 && c > 12) {
                val alpha = (135 + (score - 150) / 2).coerceIn(135, 235)
                output[i] = (alpha shl 24) or 0x00FF2F2F
            }
        }
    }

    return Bitmap.createBitmap(output, width, height, Bitmap.Config.ARGB_8888)
}
