from pathlib import Path

screen_path = Path('demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt')
ptp_path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt')

screen = screen_path.read_text()
ptp = ptp_path.read_text()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly 1 match, found {count}')
    return text.replace(old, new, 1)

# 1) Hide guide lines whenever the preview is magnified.
screen = replace_once(
    screen,
    '''            CompositionGuideOverlay(\n                source = source,\n                guide = compositionGuide,\n                modifier = Modifier.fillMaxSize()\n            )\n\n            if (magnification > 1f) {''',
    '''            if (magnification <= 1f) {\n                CompositionGuideOverlay(\n                    source = source,\n                    guide = compositionGuide,\n                    modifier = Modifier.fillMaxSize()\n                )\n            }\n\n            if (magnification > 1f) {''',
    'hide composition guide while magnified'
)

# 2) Exposure tiles must honor the live camera writable flag.
screen = replace_once(
    screen,
    '''                enabled = state is CameraConnectionState.Ready && exposure?.aperture?.current != null,''',
    '''                enabled = state is CameraConnectionState.Ready &&\n                    exposure?.aperture?.current != null && exposure?.aperture?.writable == true,''',
    'aperture tile writable'
)
screen = replace_once(
    screen,
    '''                enabled = state is CameraConnectionState.Ready && exposure?.shutterSpeed?.current != null,''',
    '''                enabled = state is CameraConnectionState.Ready &&\n                    exposure?.shutterSpeed?.current != null && exposure?.shutterSpeed?.writable == true,''',
    'shutter tile writable'
)
screen = replace_once(
    screen,
    '''                enabled = state is CameraConnectionState.Ready && exposure?.iso?.current != null,''',
    '''                enabled = state is CameraConnectionState.Ready &&\n                    exposure?.iso?.current != null && exposure?.iso?.writable == true,''',
    'iso tile writable'
)

# If the user has an exposure selector open and the camera mode changes so that
# parameter becomes read-only, close the selector rather than leaving a dead dial.
screen = replace_once(
    screen,
    '''            LaunchedEffect(menusVisible) {\n                if (!menusVisible) {\n                    activeExposure = null\n                    activeSetting = null\n                    showLutPanel = false\n                }\n            }\n            LaunchedEffect(flash) {''',
    '''            LaunchedEffect(menusVisible) {\n                if (!menusVisible) {\n                    activeExposure = null\n                    activeSetting = null\n                    showLutPanel = false\n                }\n            }\n            LaunchedEffect(exposure, activeExposure) {\n                val selected = activeExposure ?: return@LaunchedEffect\n                if (exposure?.property(selected)?.writable != true) {\n                    activeExposure = null\n                }\n            }\n            LaunchedEffect(flash) {''',
    'close locked exposure selector'
)

# 3) Tighter, explicit two-line camera identity typography.
screen = replace_once(
    screen,
    '''        Column {\n            Text(\n                cameraName ?: "SONY MONITOR",\n                color = Color.White,\n                fontSize = 11.sp,\n                fontWeight = FontWeight.SemiBold,\n                maxLines = 1,\n                overflow = TextOverflow.Ellipsis\n            )\n            Text(\n                if (ready) "USB  •  LIVE" else connectionLabel(state),\n                color = if (ready) AfGreen else Color.White.copy(alpha = 0.55f),\n                fontSize = 8.sp,\n                fontWeight = FontWeight.Bold\n            )\n        }''',
    '''        Column(verticalArrangement = Arrangement.spacedBy(1.dp)) {\n            Text(\n                cameraName ?: "SONY MONITOR",\n                color = Color.White,\n                fontSize = 11.sp,\n                lineHeight = 11.sp,\n                fontWeight = FontWeight.SemiBold,\n                maxLines = 1,\n                overflow = TextOverflow.Ellipsis\n            )\n            Text(\n                if (ready) "USB  •  LIVE" else connectionLabel(state),\n                color = if (ready) AfGreen else Color.White.copy(alpha = 0.55f),\n                fontSize = 8.sp,\n                lineHeight = 9.sp,\n                fontWeight = FontWeight.Bold\n            )\n        }''',
    'camera identity line spacing'
)

# 4) Drive Mode: preserve Sony's official term, but make an unavailable USB
# property self-explanatory instead of a mysterious grey "--" tile.
screen = replace_once(
    screen,
    '''                value = property?.current?.label ?: "--",\n                enabled = property?.current != null,''',
    '''                value = property?.current?.label\n                    ?: if (setting == CameraSetting.DRIVE_MODE) "USB N/A" else "--",\n                enabled = property?.current != null,''',
    'drive unavailable label'
)
screen = replace_once(
    screen,
    '''    CameraSetting.DRIVE_MODE -> "DRIVE"''',
    '''    CameraSetting.DRIVE_MODE -> "DRIVE MODE"''',
    'drive title'
)

# 5) Make dial-number emphasis continuous. Keep text laid out at its maximum
# size and smoothly scale/fade it according to distance from the fixed index.
screen = replace_once(
    screen,
    '''                        val absDistance = kotlin.math.abs(distance)\n                        if (absDistance > 3.2f) continue\n                        val isCenter = absDistance < 0.5f\n                        Text(\n                            text = option.label,\n                            color = if (isCenter) Color.White else Color.White.copy(\n                                alpha = when {\n                                    absDistance < 1.35f -> 0.50f\n                                    absDistance < 2.35f -> 0.24f\n                                    else -> 0.12f\n                                }\n                            ),\n                            fontSize = if (isCenter) 20.sp else if (absDistance < 1.35f) 11.sp else 9.sp,\n                            lineHeight = if (isCenter) 23.sp else 13.sp,\n                            fontWeight = if (isCenter) FontWeight.Bold else FontWeight.Medium,\n                            maxLines = 1,\n                            overflow = TextOverflow.Ellipsis,\n                            textAlign = TextAlign.Center,\n                            modifier = Modifier\n                                .align(Alignment.Center)\n                                .width(82.dp)\n                                .graphicsLayer { translationX = distance * 88.dp.toPx() }\n                        )''',
    '''                        val absDistance = kotlin.math.abs(distance)\n                        if (absDistance > 3.2f) continue\n                        val visualScale = (1f - absDistance * 0.22f).coerceIn(0.45f, 1f)\n                        val visualAlpha = (1f - absDistance * 0.32f).coerceIn(0.12f, 1f)\n                        Text(\n                            text = option.label,\n                            color = Color.White.copy(alpha = visualAlpha),\n                            fontSize = 20.sp,\n                            lineHeight = 23.sp,\n                            fontWeight = FontWeight.SemiBold,\n                            maxLines = 1,\n                            overflow = TextOverflow.Ellipsis,\n                            textAlign = TextAlign.Center,\n                            modifier = Modifier\n                                .align(Alignment.Center)\n                                .width(82.dp)\n                                .graphicsLayer {\n                                    translationX = distance * 88.dp.toPx()\n                                    scaleX = visualScale\n                                    scaleY = visualScale\n                                }\n                        )''',
    'continuous dial label scaling'
)

# 6) On protocol-3 a7C II the GetSet byte in the latest 0x9209 snapshot is
# mode-dependent. Do not permanently OR it with a historical writable=true.
ptp = replace_once(
    ptp,
    '''                    exposureDescriptors[setting] = latest.copy(\n                        writable = latest.writable || previous.writable,\n                        initialValue = latest.initialValue ?: previous.initialValue,''',
    '''                    exposureDescriptors[setting] = latest.copy(\n                        writable = latest.writable,\n                        initialValue = latest.initialValue ?: previous.initialValue,''',
    'dynamic exposure writable flag'
)

screen_path.write_text(screen)
ptp_path.write_text(ptp)
Path('tools/apply_monitor_ui_round5.py').unlink()
print('Applied monitor UI round 5')
