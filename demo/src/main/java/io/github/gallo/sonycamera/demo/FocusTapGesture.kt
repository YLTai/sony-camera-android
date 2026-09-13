package io.github.gallo.sonycamera.demo

import androidx.compose.foundation.gestures.PressGestureScope
import androidx.compose.ui.geometry.Offset

/** Dispatch before double-tap recognition, but only after a non-cancelled release. */
internal suspend fun PressGestureScope.focusOnSuccessfulRelease(
    position: Offset,
    onFocus: (Offset) -> Unit
) {
    if (tryAwaitRelease()) onFocus(position)
}
