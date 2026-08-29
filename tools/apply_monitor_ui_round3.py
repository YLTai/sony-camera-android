from pathlib import Path
p = Path("demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt")
s = p.read_text()
old = '''    var dragRemainder by remember(title) { mutableStateOf(0f) }\n    var dragging by remember(title) { mutableStateOf(false) }\n    var pendingRaw by remember(title) { mutableStateOf<Long?>(null) }\n\n    LaunchedEffect(currentRaw, options, dragging) {\n'''
new = '''    var dragRemainder by remember(title) { mutableStateOf(0f) }\n    var dragging by remember(title) { mutableStateOf(false) }\n    var pendingRaw by remember(title) { mutableStateOf<Long?>(null) }\n    val latestCurrentRaw = rememberUpdatedState(currentRaw)\n\n    LaunchedEffect(currentRaw, options, dragging) {\n'''
if old not in s: raise RuntimeError("dial state block not found")
s = s.replace(old, new, 1)
s = s.replace('''                                    if (selectedRaw != null && selectedRaw != currentRaw) {\n''', '''                                    if (selectedRaw != null && selectedRaw != latestCurrentRaw.value) {\n''', 1)
s = s.replace('''                                    val index = options.indexOfFirst { it.rawValue == currentRaw }\n                                    if (index >= 0) previewIndex = index\n                                }\n                            )\n''', '''                                    val index = options.indexOfFirst { it.rawValue == latestCurrentRaw.value }\n                                    if (index >= 0) previewIndex = index\n                                }\n                            )\n''', 1)
p.write_text(s)
Path(__file__).unlink()
print("Hardened dial current-value tracking")
