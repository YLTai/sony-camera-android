from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Public exposure model: surface camera/lens aperture limits to the UI.
# ---------------------------------------------------------------------------
manager = "sonycamera/src/main/java/io/github/gallo/sonycamera/CameraConnectionManager.kt"
replace_once(
    manager,
    '''data class CameraExposureProperty(\n    val current: CameraExposureOption?,\n    val options: List<CameraExposureOption>,\n    val writable: Boolean\n)''',
    '''data class CameraExposureProperty(\n    val current: CameraExposureOption?,\n    val options: List<CameraExposureOption>,\n    val writable: Boolean,\n    /** Lower adjustable limit reported/derived from the active lens descriptor. */\n    val minimum: CameraExposureOption? = null,\n    /** Upper adjustable limit reported/derived from the active lens descriptor. */\n    val maximum: CameraExposureOption? = null\n)'''
)


# ---------------------------------------------------------------------------
# Sony PTP: parse the complete 0x9209 descriptor rather than only current value.
# This makes lens-specific F-number bounds and enum choices authoritative.
# ---------------------------------------------------------------------------
ptp = "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
replace_once(
    ptp,
    '''            var fromSnapshot: ExposureDescriptor? = null\n            for (propertyCode in ids) {\n                val offset = findSonyPropertyOffset(snapshotData, propertyCode, knownType) ?: continue\n                val writable = (snapshotData.getOrNull(offset + 4)?.toInt()?.and(0xFF) ?: 0) != 0\n                val seed = ExposureDescriptor(\n                    setting = setting,\n                    propertyCode = propertyCode,\n                    dataType = knownType,\n                    writable = writable,\n                    initialValue = null,\n                    enumValues = emptyList(),\n                    rangeMin = null,\n                    rangeMax = null\n                )\n                fromSnapshot = seed.copy(\n                    initialValue = readCurrentFromAllProperties(snapshotData, seed)\n                )\n                break\n            }''',
    '''            var fromSnapshot: ExposureDescriptor? = null\n            for (propertyCode in ids) {\n                val offset = findSonyPropertyOffset(snapshotData, propertyCode, knownType) ?: continue\n\n                // 0x9209 contains full DevicePropDesc-shaped records on protocol-3\n                // bodies. Parse the form section as well as the current value so a\n                // zoom lens can report its real F-number floor/ceiling and choices.\n                fromSnapshot = parseExposureDescriptor(snapshotData, setting, propertyCode)\n                if (fromSnapshot == null) {\n                    val writable = (snapshotData.getOrNull(offset + 4)?.toInt()?.and(0xFF) ?: 0) != 0\n                    val seed = ExposureDescriptor(\n                        setting = setting,\n                        propertyCode = propertyCode,\n                        dataType = knownType,\n                        writable = writable,\n                        initialValue = null,\n                        enumValues = emptyList(),\n                        rangeMin = null,\n                        rangeMax = null\n                    )\n                    fromSnapshot = seed.copy(\n                        initialValue = readCurrentFromAllProperties(snapshotData, seed)\n                    )\n                }\n                break\n            }'''
)
replace_once(
    ptp,
    '''                        "choices=${it.enumValues.size} current=${it.initialValue} " +\n                        "source=${if (fromSnapshot != null) "9209" else "legacy"}"''',
    '''                        "choices=${it.enumValues.size} current=${it.initialValue} " +\n                        "range=${it.rangeMin ?: "?"}..${it.rangeMax ?: "?"} " +\n                        "source=${if (fromSnapshot != null) "9209" else "legacy"}"'''
)
replace_once(
    ptp,
    '''        // 9206 normally starts with code/type/getSet, but scan the first few\n        // bytes as newer Sony generations occasionally prepend small metadata.\n        val starts = (0..minOf(12, data.size - 5)).filter { offset ->\n            u16(data, offset) == expectedCode\n        }\n        if (starts.isEmpty()) return null''',
    '''        // This parser is shared by the small 0x9206 response and the complete\n        // 0x9209 snapshot. Scan the whole blob; structural validation below rejects\n        // accidental occurrences of the property code inside another value.\n        if (data.size < 5) return null\n        val starts = (0 until (data.size - 4)).filter { offset ->\n            u16(data, offset) == expectedCode\n        }\n        if (starts.isEmpty()) return null'''
)
replace_once(
    ptp,
    '''            val type = u16(data, base + 2)\n            val size = scalarSize(type)\n            if (size !in 1..4) continue\n            val getSet = data[base + 4].toInt() and 0xFF''',
    '''            val type = u16(data, base + 2)\n            val expectedType = when (setting) {\n                CameraExposureSetting.APERTURE -> 0x0004\n                CameraExposureSetting.SHUTTER_SPEED,\n                CameraExposureSetting.ISO -> 0x0006\n            }\n            if (type != expectedType) continue\n            val size = scalarSize(type)\n            if (size !in 1..4) continue\n            val getSet = data[base + 4].toInt() and 0xFF'''
)
replace_once(
    ptp,
    '''                var score = 2\n                if (getSet != 0) score += 2\n                if (enumValues.size > 1) score += 3\n                if (rangeMin != null && rangeMax != null) score += 1\n                if (sonyExtraFlag) score += 1 // 9206/9209 Sony data commonly uses this layout.''',
    '''                var score = 2\n                if (getSet != 0) score += 2\n                if (enumValues.size > 1) score += 3\n                if (rangeMin != null && rangeMax != null) score += 2\n                if (current != null && isPlausibleExposureRaw(setting, current)) score += 2\n                if (sonyExtraFlag) score += 1 // 9206/9209 Sony data commonly uses this layout.'''
)
replace_once(
    ptp,
    '''        return CameraExposureProperty(\n            current = current,\n            options = options,\n            writable = descriptor.writable && options.size > 1\n        )''',
    '''        // Aperture limits are lens-specific. Prefer explicit descriptor range\n        // bounds; enum-only lenses still provide authoritative first/last F values.\n        val minimum = if (descriptor.setting == CameraExposureSetting.APERTURE) {\n            (descriptor.rangeMin ?: raws.minOrNull())?.let { raw ->\n                CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))\n            }\n        } else null\n        val maximum = if (descriptor.setting == CameraExposureSetting.APERTURE) {\n            (descriptor.rangeMax ?: raws.maxOrNull())?.let { raw ->\n                CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))\n            }\n        } else null\n        return CameraExposureProperty(\n            current = current,\n            options = options,\n            writable = descriptor.writable && options.size > 1,\n            minimum = minimum,\n            maximum = maximum\n        )'''
)
replace_once(
    ptp,
    '''            if (values.size < 2) {\n                values = fallbackCameraSettingValues(setting)\n            }\n            val current = descriptor.currentValue''',
    '''            if (values.size < 2) {\n                values = fallbackCameraSettingValues(setting)\n            }\n            if (setting == CameraSetting.EXPOSURE_COMPENSATION && values.isNotEmpty()) {\n                values = values.sortedBy { raw ->\n                    (raw and 0xFFFF).toInt().toShort().toInt()\n                }\n            }\n            val current = descriptor.currentValue'''
)


# ---------------------------------------------------------------------------
# Monitor tools: add composition guide modes as a screen-space overlay.
# ---------------------------------------------------------------------------
monitor = "demo/src/main/java/io/github/gallo/sonycamera/demo/MonitorTools.kt"
p = Path(monitor)
text = p.read_text()
text += r'''

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
'''
p.write_text(text)


# ---------------------------------------------------------------------------
# Camera UI: composition-guide button + physical-dial style continuous picker.
# ---------------------------------------------------------------------------
screen = "demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt"
replace_once(
    screen,
    'import androidx.compose.foundation.gestures.detectTapGestures\n',
    'import androidx.compose.foundation.gestures.detectTapGestures\nimport androidx.compose.foundation.gestures.detectHorizontalDragGestures\n'
)
replace_once(
    screen,
    '''            var histogramEnabled by remember { mutableStateOf(false) }\n            var magnification by remember { mutableStateOf(1f) }''',
    '''            var histogramEnabled by remember { mutableStateOf(false) }\n            var compositionGuide by remember { mutableStateOf(CompositionGuide.OFF) }\n            var magnification by remember { mutableStateOf(1f) }'''
)
replace_once(
    screen,
    '''                    zebraThreshold = zebraThreshold,\n                    magnification = magnification,''',
    '''                    zebraThreshold = zebraThreshold,\n                    compositionGuide = compositionGuide,\n                    magnification = magnification,'''
)
replace_once(
    screen,
    '''                        histogramEnabled = histogramEnabled,\n                        magnification = magnification,''',
    '''                        histogramEnabled = histogramEnabled,\n                        compositionGuide = compositionGuide,\n                        magnification = magnification,'''
)
replace_once(
    screen,
    '''                        onHistogram = { histogramEnabled = !histogramEnabled },\n                        onMagnify = {''',
    '''                        onHistogram = { histogramEnabled = !histogramEnabled },\n                        onCompositionGuide = { compositionGuide = nextCompositionGuide(compositionGuide) },\n                        onMagnify = {'''
)
replace_once(
    screen,
    '''                            OptionSelectorPanel(\n                                title = exposureTitle(setting),\n                                currentRaw = property.current?.rawValue,\n                                options = property.options.map { SelectorOption(it.rawValue, it.label) },\n                                writable = property.writable,\n                                onSelect = { raw -> setExposure(setting, raw) },\n                                modifier = Modifier.align(Alignment.TopCenter).padding(top = 78.dp)\n                            )''',
    '''                            DialSelectorPanel(\n                                title = exposureTitle(setting),\n                                currentRaw = property.current?.rawValue,\n                                options = property.options.map { SelectorOption(it.rawValue, it.label) },\n                                writable = property.writable,\n                                minimumLabel = if (setting == CameraExposureSetting.APERTURE) property.minimum?.label else null,\n                                maximumLabel = if (setting == CameraExposureSetting.APERTURE) property.maximum?.label else null,\n                                onSelect = { raw -> setExposure(setting, raw) },\n                                modifier = Modifier.align(Alignment.TopCenter).padding(top = 78.dp)\n                            )'''
)
replace_once(
    screen,
    '''                        cameraSettings?.property(setting)?.let { property ->\n                            OptionSelectorPanel(\n                                title = cameraSettingTitle(setting),\n                                currentRaw = property.current?.rawValue,\n                                options = property.options.map { SelectorOption(it.rawValue, it.label) },\n                                writable = property.writable,\n                                onSelect = { raw -> setCameraSetting(setting, raw) },\n                                modifier = Modifier.align(Alignment.TopCenter).padding(top = 78.dp)\n                            )\n                        }''',
    '''                        cameraSettings?.property(setting)?.let { property ->\n                            val selectorOptions = property.options.map { SelectorOption(it.rawValue, it.label) }\n                            if (setting == CameraSetting.EXPOSURE_COMPENSATION) {\n                                DialSelectorPanel(\n                                    title = cameraSettingTitle(setting),\n                                    currentRaw = property.current?.rawValue,\n                                    options = selectorOptions,\n                                    writable = property.writable,\n                                    minimumLabel = selectorOptions.firstOrNull()?.label,\n                                    maximumLabel = selectorOptions.lastOrNull()?.label,\n                                    onSelect = { raw -> setCameraSetting(setting, raw) },\n                                    modifier = Modifier.align(Alignment.TopCenter).padding(top = 78.dp)\n                                )\n                            } else {\n                                OptionSelectorPanel(\n                                    title = cameraSettingTitle(setting),\n                                    currentRaw = property.current?.rawValue,\n                                    options = selectorOptions,\n                                    writable = property.writable,\n                                    onSelect = { raw -> setCameraSetting(setting, raw) },\n                                    modifier = Modifier.align(Alignment.TopCenter).padding(top = 78.dp)\n                                )\n                            }\n                        }'''
)
replace_once(
    screen,
    '''    peakingLevel: PeakingLevel,\n    zebraThreshold: Int?,\n    magnification: Float,''',
    '''    peakingLevel: PeakingLevel,\n    zebraThreshold: Int?,\n    compositionGuide: CompositionGuide,\n    magnification: Float,'''
)
replace_once(
    screen,
    '''            if (magnification > 1f) {\n                Text(''',
    '''            CompositionGuideOverlay(\n                source = source,\n                guide = compositionGuide,\n                modifier = Modifier.fillMaxSize()\n            )\n\n            if (magnification > 1f) {\n                Text('''
)
replace_once(
    screen,
    '''    zebraThreshold: Int?,\n    histogramEnabled: Boolean,\n    magnification: Float,''',
    '''    zebraThreshold: Int?,\n    histogramEnabled: Boolean,\n    compositionGuide: CompositionGuide,\n    magnification: Float,'''
)
replace_once(
    screen,
    '''    onZebra: () -> Unit,\n    onHistogram: () -> Unit,\n    onMagnify: () -> Unit,''',
    '''    onZebra: () -> Unit,\n    onHistogram: () -> Unit,\n    onCompositionGuide: () -> Unit,\n    onMagnify: () -> Unit,'''
)
replace_once(
    screen,
    '''        SonyToolButton("HIST", if (histogramEnabled) "ON" else "OFF", histogramEnabled, onHistogram)\n        SonyToolButton("MAG", "${magnification.toInt()}×", magnification > 1f, onMagnify)''',
    '''        SonyToolButton("HIST", if (histogramEnabled) "ON" else "OFF", histogramEnabled, onHistogram)\n        SonyToolButton("GUIDE", compositionGuide.label, compositionGuide != CompositionGuide.OFF, onCompositionGuide)\n        SonyToolButton("MAG", "${magnification.toInt()}×", magnification > 1f, onMagnify)'''
)
replace_once(
    screen,
    '''private data class SelectorOption(val rawValue: Long, val label: String)\n\n@Composable\nprivate fun OptionSelectorPanel(''',
    r'''private data class SelectorOption(val rawValue: Long, val label: String)

/**
 * Sony-style virtual control dial for ordered settings. Drag left/right to turn
 * through camera-reported steps; only the final detent is sent over USB so a
 * fast finger movement never queues dozens of PTP writes behind live view.
 */
@Composable
private fun DialSelectorPanel(
    title: String,
    currentRaw: Long?,
    options: List<SelectorOption>,
    writable: Boolean,
    minimumLabel: String? = null,
    maximumLabel: String? = null,
    onSelect: (Long) -> Unit,
    modifier: Modifier = Modifier
) {
    var previewIndex by remember(options, currentRaw) {
        val index = options.indexOfFirst { it.rawValue == currentRaw }
        mutableStateOf(if (index >= 0) index else 0)
    }
    var dragRemainder by remember { mutableStateOf(0f) }
    var dragging by remember { mutableStateOf(false) }

    LaunchedEffect(currentRaw, options, dragging) {
        if (!dragging) {
            val index = options.indexOfFirst { it.rawValue == currentRaw }
            if (index >= 0) previewIndex = index
        }
    }

    Surface(
        modifier = modifier.fillMaxWidth(0.72f).widthIn(max = 690.dp),
        color = SonyPanel,
        shape = RoundedCornerShape(3.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, Color.White.copy(alpha = 0.24f)),
        shadowElevation = 8.dp
    ) {
        Column(Modifier.padding(horizontal = 11.dp, vertical = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, color = Color.White, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.width(10.dp))
                Text(
                    if (writable) "TURN / SWIPE DIAL" else "DISPLAY ONLY",
                    color = if (writable) Color.White.copy(alpha = 0.44f) else Accent,
                    fontSize = 7.sp,
                    fontWeight = FontWeight.Bold
                )
                if (minimumLabel != null || maximumLabel != null) {
                    Spacer(Modifier.width(12.dp))
                    Text(
                        listOfNotNull(
                            minimumLabel?.let { "MIN  $it" },
                            maximumLabel?.let { "MAX  $it" }
                        ).joinToString("     "),
                        color = Color.White.copy(alpha = 0.68f),
                        fontSize = 8.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }
            Spacer(Modifier.height(5.dp))

            if (options.isEmpty()) {
                Text("No adjustable steps reported by camera", color = Color.White.copy(alpha = 0.55f), fontSize = 11.sp)
            } else {
                val safeIndex = previewIndex.coerceIn(0, options.lastIndex)
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(78.dp)
                        .background(Color.Black.copy(alpha = 0.28f), RoundedCornerShape(2.dp))
                        .pointerInput(options, writable) {
                            if (!writable || options.size < 2) return@pointerInput
                            detectHorizontalDragGestures(
                                onDragStart = {
                                    dragging = true
                                    dragRemainder = 0f
                                },
                                onHorizontalDrag = { _, dragAmount ->
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
                                onDragCancel = {
                                    dragging = false
                                    dragRemainder = 0f
                                    val index = options.indexOfFirst { it.rawValue == currentRaw }
                                    if (index >= 0) previewIndex = index
                                }
                            )
                        },
                    contentAlignment = Alignment.Center
                ) {
                    Canvas(Modifier.fillMaxSize()) {
                        val center = size.width / 2f
                        val spacing = 28.dp.toPx()
                        val bottom = size.height
                        for (tick in -10..10) {
                            val x = center + tick * spacing
                            if (x < 0f || x > size.width) continue
                            val major = tick % 2 == 0
                            val h = if (major) 15.dp.toPx() else 9.dp.toPx()
                            drawLine(
                                color = Color.White.copy(alpha = if (major) 0.34f else 0.18f),
                                start = Offset(x, bottom - h),
                                end = Offset(x, bottom),
                                strokeWidth = 1.dp.toPx()
                            )
                        }
                        drawLine(
                            color = Accent,
                            start = Offset(center, 0f),
                            end = Offset(center, bottom),
                            strokeWidth = 2.dp.toPx()
                        )
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 8.dp),
                        horizontalArrangement = Arrangement.SpaceEvenly,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        for (delta in -2..2) {
                            val option = options.getOrNull(safeIndex + delta)
                            Box(Modifier.width(92.dp), contentAlignment = Alignment.Center) {
                                Text(
                                    text = option?.label ?: "",
                                    color = if (delta == 0) Color.White else Color.White.copy(alpha = if (kotlin.math.abs(delta) == 1) 0.50f else 0.24f),
                                    fontSize = if (delta == 0) 20.sp else if (kotlin.math.abs(delta) == 1) 11.sp else 9.sp,
                                    lineHeight = if (delta == 0) 23.sp else 13.sp,
                                    fontWeight = if (delta == 0) FontWeight.Bold else FontWeight.Medium,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                    textAlign = TextAlign.Center
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun OptionSelectorPanel('''
)

print("Applied composition guides, camera aperture limits, and dial selectors")
