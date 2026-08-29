from pathlib import Path

p = Path("demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt")
text = p.read_text()

def rep(old: str, new: str) -> None:
    global text
    if old not in text:
        raise RuntimeError(f"Expected text not found: {old[:140]!r}")
    text = text.replace(old, new, 1)

rep(
    """ * Sony-style virtual control dial for ordered settings. Drag left/right to turn\n * through camera-reported steps; only the final detent is sent over USB so a\n * fast finger movement never queues dozens of PTP writes behind live view.\n""",
    """ * Sony-style virtual control dial for ordered settings. Drag left/right to turn\n * continuously through camera-reported steps. While dragging, the newest detent\n * is sampled and sent at a bounded rate so live-view adjustments stay observable.\n""",
)
rep(
    """            if (target != lastStreamedRaw && target != latestCurrentRaw.value) {\n                lastStreamedRaw = target\n                pendingRaw = target\n                latestOnSelect.value(target)\n            }\n""",
    """            // Compare against what we last commanded, not the latest camera\n            // snapshot. That snapshot may still describe the pre-drag value; if\n            // the user drags away and back, the return command must still be sent.\n            if (target != lastStreamedRaw) {\n                lastStreamedRaw = target\n                pendingRaw = target\n                latestOnSelect.value(target)\n            }\n""",
)
rep(
    """    LaunchedEffect(pendingRaw) {\n        val pending = pendingRaw ?: return@LaunchedEffect\n        delay(3_000)\n""",
    """    LaunchedEffect(pendingRaw, dragging) {\n        if (dragging) return@LaunchedEffect\n        val pending = pendingRaw ?: return@LaunchedEffect\n        delay(3_000)\n""",
)
rep(
    """                                    if (finalRaw != latestCurrentRaw.value) {\n                                        pendingRaw = finalRaw\n                                        if (finalRaw != lastStreamedRaw) {\n                                            lastStreamedRaw = finalRaw\n                                            latestOnSelect.value(finalRaw)\n                                        }\n                                    } else {\n                                        pendingRaw = null\n                                    }\n""",
    """                                    pendingRaw = if (finalRaw == latestCurrentRaw.value) null else finalRaw\n                                    if (finalRaw != lastStreamedRaw) {\n                                        lastStreamedRaw = finalRaw\n                                        latestOnSelect.value(finalRaw)\n                                    }\n""",
)
p.write_text(text)
Path(__file__).unlink()
print("Hardened live dial reverse tracking")
