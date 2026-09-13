package io.github.gallo.sonycamera.demo

import androidx.compose.foundation.gestures.PressGestureScope
import androidx.compose.ui.geometry.Offset
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class FocusTapGestureTest {
    private class Press(private val released: CompletableDeferred<Boolean>) : PressGestureScope {
        override val density: Float = 1f
        override val fontScale: Float = 1f
        override suspend fun awaitRelease() { check(released.await()) }
        override suspend fun tryAwaitRelease(): Boolean = released.await()
    }

    @Test fun focusIsDispatchedOnReleaseWithoutAdvancingDoubleTapClock() = runTest {
        val release = CompletableDeferred<Boolean>()
        val received = mutableListOf<Offset>()
        launch { Press(release).focusOnSuccessfulRelease(Offset(20f, 30f)) { received.add(it) } }
        runCurrent()
        assertTrue(received.isEmpty())
        release.complete(true)
        runCurrent()
        assertEquals(listOf(Offset(20f, 30f)), received)
        assertEquals(0L, testScheduler.currentTime)
    }

    @Test fun cancelledPressDoesNotFocus() = runTest {
        var called = false
        Press(CompletableDeferred(false)).focusOnSuccessfulRelease(Offset.Zero) { called = true }
        assertFalse(called)
    }

    @Test fun disposedGestureDoesNotFocusLater() = runTest {
        val release = CompletableDeferred<Boolean>()
        var called = false
        val job = launch { Press(release).focusOnSuccessfulRelease(Offset.Zero) { called = true } }
        runCurrent()
        job.cancel()
        release.complete(true)
        runCurrent()
        assertFalse(called)
    }
}
