package io.github.gallo.sonycamera.demo

import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.math.roundToInt

internal data class StoredLut(
    val id: String,
    val displayName: String,
    val file: File
)

internal data class CubeLut(
    val title: String,
    val size: Int,
    val domainMin: FloatArray,
    val domainMax: FloatArray,
    /** Packed 0xRRGGBB entries. .cube ordering has R changing fastest. */
    val entries: IntArray
)

internal object LutLibrary {
    private const val LUT_DIR = "luts"

    fun list(context: Context): List<StoredLut> {
        val directory = File(context.filesDir, LUT_DIR)
        if (!directory.exists()) return emptyList()
        return directory.listFiles()
            ?.filter { it.isFile && it.extension.equals("cube", ignoreCase = true) }
            ?.sortedBy { it.name.lowercase() }
            ?.map { file ->
                StoredLut(
                    id = file.name,
                    displayName = file.nameWithoutExtension.replace('_', ' '),
                    file = file
                )
            }
            ?: emptyList()
    }

    fun import(context: Context, uri: Uri): Result<StoredLut> = runCatching {
        val resolver = context.contentResolver
        val bytes = resolver.openInputStream(uri)?.use { input -> input.readBytes() }
            ?: error("Unable to read LUT file")
        if (bytes.size > 12 * 1024 * 1024) error("LUT file is too large")
        val text = bytes.toString(Charsets.UTF_8)
        val parsed = parseCubeLut(text)

        val directory = File(context.filesDir, LUT_DIR).apply { mkdirs() }
        val base = sanitizeFileName(parsed.title.ifBlank { "Imported LUT" })
        var file = File(directory, "$base.cube")
        var suffix = 2
        while (file.exists()) {
            file = File(directory, "$base $suffix.cube")
            suffix++
        }
        file.writeBytes(bytes)
        StoredLut(file.name, parsed.title.ifBlank { file.nameWithoutExtension }, file)
    }

    fun delete(lut: StoredLut): Boolean = lut.file.delete()

    fun load(lut: StoredLut): Result<CubeLut> = runCatching {
        parseCubeLut(lut.file.readText())
    }

    private fun sanitizeFileName(input: String): String {
        val cleaned = input
            .replace(Regex("[\\/:*?\"<>|]"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()
        return cleaned.ifBlank { "Imported LUT" }.take(80)
    }
}

/**
 * Parse the common 3D .cube subset used by Resolve / Premiere / monitor LUTs.
 * 1D shaper-only LUTs are deliberately rejected for the first implementation.
 */
internal fun parseCubeLut(text: String): CubeLut {
    var title = "Imported LUT"
    var size = 0
    val domainMin = floatArrayOf(0f, 0f, 0f)
    val domainMax = floatArrayOf(1f, 1f, 1f)
    val colors = ArrayList<Int>()

    text.lineSequence().forEachIndexed { index, rawLine ->
        val line = rawLine.substringBefore('#').trim()
        if (line.isEmpty()) return@forEachIndexed
        when {
            line.startsWith("TITLE", ignoreCase = true) -> {
                title = line.substringAfter("TITLE", "")
                    .trim()
                    .removeSurrounding("\"")
                    .ifBlank { title }
            }
            line.startsWith("LUT_1D_SIZE", ignoreCase = true) -> {
                error("1D LUTs are not supported yet (line ${index + 1})")
            }
            line.startsWith("LUT_3D_SIZE", ignoreCase = true) -> {
                size = line.substringAfter("LUT_3D_SIZE").trim().toIntOrNull()
                    ?: error("Invalid LUT_3D_SIZE at line ${index + 1}")
                if (size !in 2..65) error("3D LUT size $size is unsupported; use 2-65")
            }
            line.startsWith("DOMAIN_MIN", ignoreCase = true) -> {
                parseVector(line.substringAfter("DOMAIN_MIN"), domainMin, index)
            }
            line.startsWith("DOMAIN_MAX", ignoreCase = true) -> {
                parseVector(line.substringAfter("DOMAIN_MAX"), domainMax, index)
            }
            line.first().isDigit() || line.first() == '-' || line.first() == '.' -> {
                val values = line.split(Regex("\\s+")).mapNotNull { it.toFloatOrNull() }
                if (values.size < 3) error("Invalid LUT value at line ${index + 1}")
                val r = (values[0].coerceIn(0f, 1f) * 255f).roundToInt()
                val g = (values[1].coerceIn(0f, 1f) * 255f).roundToInt()
                val b = (values[2].coerceIn(0f, 1f) * 255f).roundToInt()
                colors += (r shl 16) or (g shl 8) or b
            }
        }
    }

    if (size == 0) error("LUT_3D_SIZE is missing")
    val expected = size * size * size
    if (colors.size != expected) {
        error("LUT contains ${colors.size} entries; expected $expected for ${size}³")
    }
    for (i in 0..2) {
        if (domainMax[i] <= domainMin[i]) error("Invalid LUT domain")
    }
    return CubeLut(title, size, domainMin, domainMax, colors.toIntArray())
}

private fun parseVector(text: String, target: FloatArray, lineIndex: Int) {
    val values = text.trim().split(Regex("\\s+")).mapNotNull { it.toFloatOrNull() }
    if (values.size < 3) error("Invalid LUT domain at line ${lineIndex + 1}")
    target[0] = values[0]
    target[1] = values[1]
    target[2] = values[2]
}

/**
 * Keeps LUT processing off the UI thread. MutableStateFlow conflates incoming
 * camera frames: if processing is slower than live view, old frames are
 * intentionally discarded so monitor latency stays low.
 */
@Composable
internal fun rememberLutProcessedFrame(
    source: Bitmap?,
    lut: CubeLut?,
    enabled: Boolean,
    intensity: Float
): Bitmap? {
    val latestSource = remember { MutableStateFlow<Bitmap?>(null) }
    var output by remember { mutableStateOf<Bitmap?>(null) }
    SideEffect { latestSource.value = source }

    LaunchedEffect(lut, enabled, intensity) {
        if (!enabled || lut == null) {
            output = null
            return@LaunchedEffect
        }
        latestSource.filterNotNull().collect { bitmap ->
            output = withContext(Dispatchers.Default) {
                applyCubeLut(bitmap, lut, intensity.coerceIn(0f, 1f))
            }
            // Target roughly 8-10 processed preview updates/s on midrange phones.
            delay(70)
        }
    }
    return if (enabled && lut != null) output ?: source else source
}

private fun applyCubeLut(source: Bitmap, lut: CubeLut, intensity: Float): Bitmap {
    if (intensity <= 0.001f) return source
    val width = source.width
    val height = source.height
    val pixels = IntArray(width * height)
    source.getPixels(pixels, 0, width, 0, 0, width, height)

    val n = lut.size
    val maxIndex = n - 1
    val minR = lut.domainMin[0]
    val minG = lut.domainMin[1]
    val minB = lut.domainMin[2]
    val spanR = lut.domainMax[0] - minR
    val spanG = lut.domainMax[1] - minG
    val spanB = lut.domainMax[2] - minB
    val inverse = 1f - intensity

    for (i in pixels.indices) {
        val color = pixels[i]
        val a = color ushr 24
        val srcR = (color ushr 16) and 0xFF
        val srcG = (color ushr 8) and 0xFF
        val srcB = color and 0xFF

        val rf = (((srcR / 255f) - minR) / spanR).coerceIn(0f, 1f)
        val gf = (((srcG / 255f) - minG) / spanG).coerceIn(0f, 1f)
        val bf = (((srcB / 255f) - minB) / spanB).coerceIn(0f, 1f)

        // Nearest lookup is intentionally used for the real-time first version.
        // It is much cheaper than trilinear interpolation and 33/65³ monitor
        // LUTs remain visually smooth at live-view resolution.
        val rIndex = (rf * maxIndex).roundToInt().coerceIn(0, maxIndex)
        val gIndex = (gf * maxIndex).roundToInt().coerceIn(0, maxIndex)
        val bIndex = (bf * maxIndex).roundToInt().coerceIn(0, maxIndex)
        val mapped = lut.entries[(bIndex * n + gIndex) * n + rIndex]
        val lutR = (mapped ushr 16) and 0xFF
        val lutG = (mapped ushr 8) and 0xFF
        val lutB = mapped and 0xFF

        val outR = (srcR * inverse + lutR * intensity).roundToInt().coerceIn(0, 255)
        val outG = (srcG * inverse + lutG * intensity).roundToInt().coerceIn(0, 255)
        val outB = (srcB * inverse + lutB * intensity).roundToInt().coerceIn(0, 255)
        pixels[i] = (a shl 24) or (outR shl 16) or (outG shl 8) or outB
    }
    return Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
}
