package io.github.gallo.sonycamera.usb

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.os.Build
import android.util.Log
import io.github.gallo.sonycamera.CameraConnectionManager
import io.github.gallo.sonycamera.CameraConnectionState
import io.github.gallo.sonycamera.CameraEvent
import io.github.gallo.sonycamera.CameraExposureSetting
import io.github.gallo.sonycamera.CameraFocusFrame
import io.github.gallo.sonycamera.CameraFocusFrameInfo
import io.github.gallo.sonycamera.CameraOperationResult
import io.github.gallo.sonycamera.CameraSetting
import io.github.gallo.sonycamera.ptp.PtpConstants
import io.github.gallo.sonycamera.ptp.PtpTransport
import io.github.gallo.sonycamera.ptp.SonyPtpCamera
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.abs

/**
 * USB PTP camera connection engine for Sony cameras.
 *
 * Implements [CameraConnectionManager] using Android's USB Host API and
 * PTP protocol for camera communication. Provides:
 * - USB device detection and permission handling
 * - PTP session management
 * - Liveview frame streaming via [liveviewFrames] flow
 * - Photo capture and download
 *
 * This is NOT a DI singleton — it is instantiated and solely owned by
 * [CameraConnectionService], which manages its lifecycle (foreground
 * service, watchdog, process-death recovery). Call [destroy] to tear down.
 */
class UsbCameraConnectionManager(
    private val context: Context
) : CameraConnectionManager {

    companion object {
        private const val TAG = "UsbCameraManager"
        private const val ACTION_USB_PERMISSION = "io.github.gallo.sonycamera.USB_PERMISSION"
        // Sony USB liveview typically runs ~10-15 fps due to USB bulk transfer overhead.
        // Polling faster than the camera can produce frames just wastes CPU.
        private const val LIVEVIEW_MIN_FRAME_INTERVAL_MS = 30L // ~33 fps max
        // Always leave a tiny idle bus window after a successful frame so a
        // monitor tap can claim PTP before the next GetObject starts.
        private const val LIVEVIEW_CONTROL_GAP_MS = 12L
        private const val EXPOSURE_POLL_INTERVAL_MS = 250L
        private const val SETTINGS_POLL_INTERVAL_MS = 900L
        private const val TELEMETRY_WARMUP_MS = 700L
        private const val CONTROL_POLL_QUIET_MS = 220L
        // How long we hold the UI in "reconnecting" after a USB detach before giving up.
        // Accommodates a bumped cable, a brief USB hub reset, or a camera auto-sleep wake.
        private const val RECONNECT_GRACE_MS = 7_000L
        // How often we poll usbManager.deviceList during the grace window.
        private const val REATTACH_POLL_INTERVAL_MS = 400L
    }

    private val usbManager = context.getSystemService(Context.USB_SERVICE) as UsbManager
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    /**
     * Separate scope for USB teardown launches. Stays alive even after
     * [destroy] cancels the main [scope], so the graceful end-session
     * commands (priority release + PTP CloseSession) always get a chance to
     * reach the camera before the connection is torn down.
     */
    private val teardownScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    // ── State ──
    private val _connectionState = MutableStateFlow<CameraConnectionState>(CameraConnectionState.Disconnected)
    override val connectionState: StateFlow<CameraConnectionState> = _connectionState.asStateFlow()

    private val _cameraName = MutableStateFlow<String?>(null)
    override val cameraName: StateFlow<String?> = _cameraName.asStateFlow()

    private val _events = MutableSharedFlow<CameraEvent>(extraBufferCapacity = 64)
    override val events: SharedFlow<CameraEvent> = _events.asSharedFlow()

    private val _liveviewFrames = MutableSharedFlow<Bitmap>(
        replay = 0,
        extraBufferCapacity = 2,
        onBufferOverflow = kotlinx.coroutines.channels.BufferOverflow.DROP_OLDEST
    )
    override val liveviewFrames: SharedFlow<Bitmap> = _liveviewFrames

    // ── USB resources ──
    private var usbDevice: UsbDevice? = null
    private var usbConnection: UsbDeviceConnection? = null
    private var ptpInterface: UsbInterface? = null
    private var ptpCamera: SonyPtpCamera? = null
    private var liveviewJob: Job? = null
    private var isLiveviewActive = false
    @Volatile private var postCaptureResumeDeadlineMs = 0L

    // Camera control and telemetry share one PTP transport. A telemetry request
    // can already be in flight when the user turns a dial; versioning lets us
    // drop that stale result instead of repainting the UI with the old value.
    private val controlWriteMutex = Mutex()
    private val controlEpochLock = Any()
    @Volatile private var controlEpoch = 0L
    @Volatile private var telemetryResumeAtMs = 0L
    @Volatile private var controlWriteActive = false
    private val priorityControlIntents = AtomicInteger(0)
    @Volatile private var afHalfPressHeld = false
    private var afReleaseJob: Job? = null
    private var remoteTouchRuntimeProbeJob: Job? = null
    private var afGeneration = 0L
    // ILCE-7CM2 diagnostic A/B: alternate the two camera-native wire actions
    // in one session so body-LCD latency can be compared without S1 or fake UI.
    private var afWireProbeD2dcNext = true

    private data class PendingAfFrameLatency(
        val generation: Long,
        val x: Int,
        val y: Int,
        val requestedAtMs: Long,
        val ackAtMs: Long,
        val commandDoneAtMs: Long,
        val path: String,
        val s1Ms: Long?,
        val baseline: CameraFocusFrameInfo?,
        val prepDebug: String,
        var firstGeometryChangeAtMs: Long? = null
    )

    private data class FocusTargetDistance(
        val dxPx: Float,
        val dyPx: Float
    ) {
        val maxErrorPx: Float get() = maxOf(abs(dxPx), abs(dyPx))
    }

    private val afStateLock = Any()
    @Volatile private var pendingAfFrameLatency: PendingAfFrameLatency? = null
    @Volatile private var latestFocusFrameInfo: CameraFocusFrameInfo? = null

    private fun focusGeometryChanged(
        before: CameraFocusFrameInfo?,
        after: CameraFocusFrameInfo
    ): Boolean {
        val oldFrames = before?.frames ?: return false
        if (oldFrames.size != after.frames.size) return true
        return oldFrames.indices.any { index ->
            val old = oldFrames[index]
            val new = after.frames[index]
            old.xNumerator != new.xNumerator || old.yNumerator != new.yNumerator ||
                old.xDenominator != new.xDenominator || old.yDenominator != new.yDenominator ||
                old.width != new.width || old.height != new.height
        }
    }

    private fun nearestFocusTargetDistance(
        info: CameraFocusFrameInfo,
        x: Int,
        y: Int
    ): FocusTargetDistance? {
        if (info.frames.isEmpty()) return null
        val targetX = x / 639f
        val targetY = y / 479f
        return info.frames.map { frame ->
            FocusTargetDistance(
                dxPx = (frame.centerXNormalized - targetX) * 639f,
                dyPx = (frame.centerYNormalized - targetY) * 479f
            )
        }.minByOrNull { it.maxErrorPx }
    }

    private fun observeAfFrameLatency(info: CameraFocusFrameInfo) {
        val pending = synchronized(afStateLock) { pendingAfFrameLatency } ?: return
        val now = System.currentTimeMillis()

        if (pending.firstGeometryChangeAtMs == null && focusGeometryChanged(pending.baseline, info)) {
            synchronized(afStateLock) {
                val current = pendingAfFrameLatency
                if (current?.generation == pending.generation && current.firstGeometryChangeAtMs == null) {
                    current.firstGeometryChangeAtMs = now
                }
            }
        }

        val nearest = nearestFocusTargetDistance(info, pending.x, pending.y)
        // The old metric used 75% of the returned frame size as tolerance, so a
        // stale/large frame could count as the new target long before it moved.
        // Use an absolute camera-grid tolerance instead: 12 px on the 640x480 grid.
        val matched = nearest != null && nearest.maxErrorPx <= 12f
        val elapsed = now - pending.requestedAtMs
        if (!matched && elapsed < 2_000L) return

        val claimed = synchronized(afStateLock) {
            val current = pendingAfFrameLatency
            if (current?.generation != pending.generation) {
                null
            } else {
                pendingAfFrameLatency = null
                current
            }
        } ?: return

        val ackMs = claimed.ackAtMs - claimed.requestedAtMs
        val commandMs = claimed.commandDoneAtMs - claimed.requestedAtMs
        val changedMs = claimed.firstGeometryChangeAtMs?.minus(claimed.requestedAtMs)
        val s1Text = claimed.s1Ms?.let { " s1=${it}ms" } ?: ""
        if (matched && nearest != null) {
            val frameMs = now - claimed.requestedAtMs
            val afterAckMs = now - claimed.ackAtMs
            val afterCommandMs = now - claimed.commandDoneAtMs
            _events.tryEmit(
                CameraEvent.FocusDebug(
                    "AF FRAME ${claimed.path} x=${claimed.x} y=${claimed.y}\n" +
                        "${claimed.prepDebug}\n" +
                        "ack=${ackMs}ms$s1Text cmd=${commandMs}ms change=${changedMs?.let { "${it}ms" } ?: "n/a"}\n" +
                        "target=${frameMs}ms afterAck=${afterAckMs}ms afterCmd=${afterCommandMs}ms err<=${nearest.maxErrorPx.toInt()}px"
                )
            )
        } else {
            val nearestText = nearest?.maxErrorPx?.toInt()?.let { " nearest=${it}px" } ?: " no-frame"
            _events.tryEmit(
                CameraEvent.FocusDebug(
                    "AF FRAME ${claimed.path} x=${claimed.x} y=${claimed.y}\n" +
                        "${claimed.prepDebug}\n" +
                        "ack=${ackMs}ms$s1Text cmd=${commandMs}ms change=${changedMs?.let { "${it}ms" } ?: "n/a"}\n" +
                        "target=>2000ms$nearestText"
                )
            )
        }
    }

    /**
     * Observe Sony's own Remote Touch state for ~0.9 s after a successful D2E4.
     * Samples use the transport's non-queued tryLock path, so Live View can lose
     * an occasional frame but this diagnostic can never wait ahead of a control.
     * We record the first two value edges because a DOWN/UP-style state may go
     * active quickly and settle only around the user's visible body-LCD delay.
     */
    private fun startRemoteTouchRuntimeProbe(
        camera: SonyPtpCamera,
        generation: Long,
        requestedAtMs: Long,
        ackAtMs: Long,
        x: Int,
        y: Int
    ) {
        remoteTouchRuntimeProbeJob?.cancel()
        val baseline = camera.cachedRemoteTouchRuntimeStatus()
        remoteTouchRuntimeProbeJob = scope.launch(Dispatchers.IO) {
            class EdgeTracker(initial: Long?) {
                private var initialized = initial != null
                private var previous = initial
                var firstAtMs: Long? = null
                    private set
                var secondAtMs: Long? = null
                    private set

                fun observe(value: Long?, elapsedMs: Long) {
                    if (value == null) return
                    if (!initialized) {
                        initialized = true
                        previous = value
                        return
                    }
                    if (value == previous) return
                    if (firstAtMs == null) firstAtMs = elapsedMs
                    else if (secondAtMs == null) secondAtMs = elapsedMs
                    previous = value
                }

                fun debugText(): String = when {
                    firstAtMs == null -> "none"
                    secondAtMs == null -> "${firstAtMs}ms"
                    else -> "${firstAtMs}/${secondAtMs}ms"
                }
            }

            val spotEdges = EdgeTracker(baseline?.focusTouchSpot)
            val trackingEdges = EdgeTracker(baseline?.focusTracking)
            val cancelEdges = EdgeTracker(baseline?.cancelEnable)
            var last = baseline
            var reads = 0
            var misses = 0

            // Let setAfPoint() unwind its control-write finally block first.
            delay(15)
            val deadlineMs = requestedAtMs + 900L
            while (isActive && System.currentTimeMillis() < deadlineMs) {
                if (generation != afGeneration || ptpCamera !== camera) return@launch
                val sampleAtMs = System.currentTimeMillis()
                val sample = camera.tryReadRemoteTouchRuntimeStatus(60)
                if (sample == null) {
                    misses += 1
                } else {
                    reads += 1
                    last = sample
                    val elapsed = sampleAtMs - requestedAtMs
                    spotEdges.observe(sample.focusTouchSpot, elapsed)
                    trackingEdges.observe(sample.focusTracking, elapsed)
                    cancelEdges.observe(sample.cancelEnable, elapsed)
                }
                delay(45)
            }

            if (generation != afGeneration || ptpCamera !== camera) return@launch
            fun value(v: Long?): String = v?.toString() ?: "na"
            val ackMs = ackAtMs - requestedAtMs
            val message = "AF CAM RT(D2E4) x=$x y=$y\n" +
                "ack=${ackMs}ms samples=$reads miss=$misses\n" +
                "cam0 E004=${value(baseline?.focusTouchSpot)} E005=${value(baseline?.focusTracking)} D285=${value(baseline?.cancelEnable)}\n" +
                "camEdge E004=${spotEdges.debugText()} E005=${trackingEdges.debugText()} D285=${cancelEdges.debugText()}\n" +
                "camEnd E004=${value(last?.focusTouchSpot)} E005=${value(last?.focusTracking)} D285=${value(last?.cancelEnable)}"
            Log.d(TAG, message.replace('\n', ' '))
            _events.emit(CameraEvent.FocusDebug(message))
        }
    }

    private fun scheduleAutofocusRelease(camera: SonyPtpCamera, generation: Long) {
        afReleaseJob?.cancel()
        afReleaseJob = scope.launch(Dispatchers.IO) {
            // Keep S1 held long enough for the body to complete normal AF, but
            // never make the caller wait for release. A newer tap cancels this
            // job and explicitly releases the old S1 before moving its point.
            delay(280)
            priorityControlIntents.incrementAndGet()
            try {
                controlWriteMutex.withLock {
                    if (generation != afGeneration || ptpCamera !== camera || !afHalfPressHeld) {
                        return@withLock
                    }
                    val releaseEpoch = beginControlWrite()
                    try {
                        camera.setAutofocusPressed(false)
                        afHalfPressHeld = false
                    } catch (e: Exception) {
                        Log.w(TAG, "AF half-press release failed: ${e.message}")
                    } finally {
                        endControlWrite(releaseEpoch)
                    }
                }
            } finally {
                priorityControlIntents.decrementAndGet()
            }
        }
    }

    private fun beginControlWrite(): Long = synchronized(controlEpochLock) {
        controlEpoch += 1L
        controlWriteActive = true
        telemetryResumeAtMs = Long.MAX_VALUE
        controlEpoch
    }

    private fun endControlWrite(epoch: Long) = synchronized(controlEpochLock) {
        if (controlEpoch == epoch) {
            controlWriteActive = false
            telemetryResumeAtMs = System.currentTimeMillis() + CONTROL_POLL_QUIET_MS
        }
    }

    // In-flight connect job. Tracking it lets disconnect / detach cancel a
    // connect attempt that's mid-handshake so its coroutine can run its
    // finally-block cleanup instead of leaking a claimed interface.
    private var connectJob: Job? = null
    private var teardownJob: Job? = null

    // Reconnect bookkeeping. When the cable is physically detached we don't
    // immediately surface ConnectionLost — we hold the UI in "Connecting" for
    // RECONNECT_GRACE_MS so a quickly-reattached cable resumes without a
    // round-trip back through the scanner screen.
    @Volatile private var isAwaitingReattach = false
    private var reconnectTimeoutJob: Job? = null

    // Decode options for liveview display (RGB_565 for efficiency)
    private val liveviewDecodeOptions = BitmapFactory.Options().apply {
        inPreferredConfig = Bitmap.Config.RGB_565
    }

    // ── USB device detection ──
    private val usbReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                ACTION_USB_PERMISSION -> {
                    val device = intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE)
                    val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
                    if (granted && device != null) {
                        Log.d(TAG, "USB permission granted for ${device.deviceName}")
                        connectJob?.cancel()
                        connectJob = scope.launch { connectToDevice(device) }
                    } else {
                        Log.w(TAG, "USB permission denied")
                        _connectionState.value = CameraConnectionState.Error(
                            "USB access not allowed. Unplug the camera, replug, and tap Allow on the prompt."
                        )
                    }
                }
                UsbManager.ACTION_USB_DEVICE_DETACHED -> {
                    val device = intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE)
                    if (device?.vendorId == PtpConstants.SONY_VENDOR_ID) {
                        handleSonyDetached()
                    }
                }
                UsbManager.ACTION_USB_DEVICE_ATTACHED -> {
                    val device = intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE)
                    if (device != null &&
                        device.vendorId == PtpConstants.SONY_VENDOR_ID &&
                        hasPtpInterface(device)
                    ) {
                        handleSonyAttached(device)
                    }
                }
            }
        }
    }

    init {
        val filter = IntentFilter().apply {
            addAction(ACTION_USB_PERMISSION)
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
            addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(usbReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            context.registerReceiver(usbReceiver, filter)
        }
    }

    /**
     * Handle a Sony camera being detached from USB.
     *
     * If we were actively connected, tear down USB resources but hold the
     * connection state at [CameraConnectionState.Connecting] for a grace
     * window so a bumped cable or brief hub reset can re-establish without
     * bouncing the UI back to the scanner. If the grace window expires, we
     * fall through to the original ConnectionLost behavior.
     */
    private fun handleSonyDetached() {
        val wasConnected = _connectionState.value is CameraConnectionState.Ready ||
                _connectionState.value is CameraConnectionState.Initializing
        if (!wasConnected) {
            // Not actively in use — nothing to reconnect to. Existing cleanup is sufficient.
            if (_connectionState.value !is CameraConnectionState.Disconnected) {
                Log.d(TAG, "Sony camera detached while not in use")
                disconnect()
                scope.launch { _events.emit(CameraEvent.ConnectionLost) }
            }
            return
        }

        Log.d(TAG, "Sony camera detached — entering reconnect grace window (${RECONNECT_GRACE_MS}ms)")
        isAwaitingReattach = true
        closeUsbResources()
        _connectionState.value = CameraConnectionState.Connecting

        reconnectTimeoutJob?.cancel()
        reconnectTimeoutJob = scope.launch {
            // Poll usbManager.deviceList for a reattached Sony camera.
            // ACTION_USB_DEVICE_ATTACHED is not reliably delivered to runtime
            // receivers (and often skipped for manifest activity intent-filters
            // when the app is already foreground), so polling is the most
            // robust way to detect a re-plugged cable within the grace window.
            val deadline = System.currentTimeMillis() + RECONNECT_GRACE_MS
            while (isAwaitingReattach && System.currentTimeMillis() < deadline) {
                val reattached = findSonyCamera()
                if (reattached != null) {
                    Log.d(TAG, "Sony camera reattached (poll) — auto-reconnecting")
                    isAwaitingReattach = false
                    connectToCamera(reattached)
                    return@launch
                }
                delay(REATTACH_POLL_INTERVAL_MS)
            }
            if (isAwaitingReattach) {
                isAwaitingReattach = false
                Log.w(TAG, "Reconnect grace window expired — giving up")
                _cameraName.value = null
                _connectionState.value = CameraConnectionState.Disconnected
                _events.emit(CameraEvent.ConnectionLost)
            }
        }
    }

    /**
     * Handle a Sony camera being attached. If we're inside the reconnect
     * grace window from a previous detach, silently reconnect. Otherwise
     * ignore — initial connection is driven by the scanner screen.
     *
     * USB permission is revoked on detach. The manifest intent-filter normally
     * auto-grants it again when Android delivers the attach intent, but that
     * grant can lag a few hundred ms behind the intent itself. We briefly
     * wait for the grant to land before falling back to requestPermission(),
     * which would pop a dialog that defeats the "seamless reconnect" goal.
     */
    private fun handleSonyAttached(device: UsbDevice) {
        if (!isAwaitingReattach) return
        Log.d(TAG, "Sony camera reattached — waiting for permission auto-grant")
        isAwaitingReattach = false
        reconnectTimeoutJob?.cancel()
        reconnectTimeoutJob = scope.launch {
            val deadline = System.currentTimeMillis() + 1500
            while (!usbManager.hasPermission(device) && System.currentTimeMillis() < deadline) {
                delay(100)
            }
            if (usbManager.hasPermission(device)) {
                Log.d(TAG, "USB permission present — auto-reconnecting")
            } else {
                Log.d(TAG, "USB permission not auto-granted — requesting")
            }
            connectToCamera(device)
        }
    }

    /**
     * Invoked by MainActivity when Android delivers a USB_DEVICE_ATTACHED
     * intent (either via onCreate for a cold launch or onNewIntent while
     * running). The attach broadcast is NOT delivered to runtime-registered
     * BroadcastReceivers — only to Activities via manifest intent-filter —
     * so this forwarder is the only way we learn about a reattach.
     */
    fun onUsbDeviceAttached(device: UsbDevice) {
        if (device.vendorId != PtpConstants.SONY_VENDOR_ID) return
        if (!hasPtpInterface(device)) return
        handleSonyAttached(device)
    }

    // ══════════════════════════════════════════════
    // CameraConnectionManager implementation
    // ══════════════════════════════════════════════

    override suspend fun startLiveview(): CameraOperationResult {
        if (ptpCamera == null) return CameraOperationResult.Failure("Camera not connected")
        if (isLiveviewActive) return CameraOperationResult.Success

        isLiveviewActive = true
        liveviewJob = scope.launch(Dispatchers.IO) {
            Log.d(TAG, "Starting USB liveview loop (GetObject 0xFFFFC002)")

            var frameCount = 0L
            var errorCount = 0L
            var lastLogTime = System.currentTimeMillis()
            var lastExposurePollTime = System.currentTimeMillis()
            var lastSettingsPollTime = lastExposurePollTime
            var consecutiveErrors = 0
            var hasEverGottenFrame = false
            var monitorAfPostLiveViewPrepared = false
            var pipeRecoveryAttempts = 0
            var lastFocusFrameInfo: CameraFocusFrameInfo? = null
            // Time-based stall detection: trip pipe recovery when we haven't
            // seen a successful frame in a long time, rather than after a few
            // denials in a row. Normal Sony behavior during zoom / AF bursts
            // is to produce denials between frames; counting them as a stall
            // made the FPS collapse exactly when the camera was busy.
            var lastFrameTime = System.currentTimeMillis()
            val stallTimeoutMs = 2_000L
            val initStallTimeoutMs = 5_000L
            // Wedged-liveview watchdog: after a reconnect, the camera can
            // get into a state where PTP works but liveview produces 100%
            // denials. clearEndpoints doesn't fix it; only a physical
            // unplug does. After NEVER seeing a first frame for this many
            // milliseconds, give up and surface ConnectionLost so the UI
            // can prompt the user to unplug/replug.
            val postCaptureResume = System.currentTimeMillis() < postCaptureResumeDeadlineMs
            val neverGotFrameFatalMs = if (postCaptureResume) 18_000L else 10_000L
            val liveviewStartTime = System.currentTimeMillis()

            while (isActive && isLiveviewActive) {
                try {
                    // Do not start another GetObject while a user control is waiting.
                    // The PTP transaction already in flight is allowed to finish; then
                    // AF/exposure gets the bus before the next live-view frame.
                    if (priorityControlIntents.get() > 0 || controlWriteActive) {
                        delay(2)
                        continue
                    }
                    val frameStart = System.currentTimeMillis()
                    val liveFrame = ptpCamera?.getLiveViewFrameData()
                    val jpeg = liveFrame?.jpeg

                    if (jpeg != null) {
                        val bitmap = BitmapFactory.decodeByteArray(
                            jpeg, 0, jpeg.size, liveviewDecodeOptions
                        )
                        if (bitmap != null) {
                            _liveviewFrames.emit(bitmap)
                        }

                        liveFrame.focusFrameInfo?.let { rawInfo ->
                            val cameraInfo = CameraFocusFrameInfo(
                                version = rawInfo.version,
                                frames = rawInfo.frames.map { rawFrame ->
                                    CameraFocusFrame(
                                        type = rawFrame.type,
                                        state = rawFrame.state,
                                        priority = rawFrame.priority,
                                        xNumerator = rawFrame.xNumerator,
                                        yNumerator = rawFrame.yNumerator,
                                        xDenominator = rawFrame.xDenominator,
                                        yDenominator = rawFrame.yDenominator,
                                        width = rawFrame.width,
                                        height = rawFrame.height
                                    )
                                }
                            )
                            latestFocusFrameInfo = cameraInfo
                            observeAfFrameLatency(cameraInfo)
                            if (cameraInfo != lastFocusFrameInfo) {
                                lastFocusFrameInfo = cameraInfo
                                _events.emit(CameraEvent.FocusFramesUpdated(cameraInfo))
                            }
                        }

                        frameCount++
                        consecutiveErrors = 0
                        hasEverGottenFrame = true
                        pipeRecoveryAttempts = 0
                        postCaptureResumeDeadlineMs = 0L
                        lastFrameTime = System.currentTimeMillis()

                        if (!monitorAfPostLiveViewPrepared) {
                            monitorAfPostLiveViewPrepared = true
                            val camera = ptpCamera
                            if (camera != null) {
                                controlWriteMutex.withLock {
                                    val prepEpoch = beginControlWrite()
                                    try {
                                        camera.invalidateMonitorTapAf()
                                        val postLiveViewPrep = camera.prepareMonitorTapAf()
                                        Log.d(TAG, "Remote Touch post-LiveView prep: ${postLiveViewPrep.replace('\n', ' ')}")
                                        _events.emit(
                                            CameraEvent.FocusDebug(
                                                "RTSTATE POST-LIVEVIEW\n$postLiveViewPrep"
                                            )
                                        )
                                    } finally {
                                        endControlWrite(prepEpoch)
                                    }
                                }
                            }
                        }

                        // The Sony USB transport is strictly serial. Do not perform property
                        // snapshots immediately after the first frame, and never stack exposure +
                        // settings reads in the same frame iteration. App-originated writes already
                        // publish their result immediately; these polls are only for camera-side dials.
                        val telemetryNow = System.currentTimeMillis()
                        if (priorityControlIntents.get() == 0 && !controlWriteActive &&
                            telemetryNow - liveviewStartTime >= TELEMETRY_WARMUP_MS &&
                            telemetryNow >= telemetryResumeAtMs
                        ) {
                            if (telemetryNow - lastSettingsPollTime >= SETTINGS_POLL_INTERVAL_MS) {
                                lastSettingsPollTime = telemetryNow
                                val pollEpoch = controlEpoch
                                val settings = ptpCamera?.readCameraSettingsState()
                                if (settings != null && pollEpoch == controlEpoch &&
                                    System.currentTimeMillis() >= telemetryResumeAtMs
                                ) {
                                    _events.emit(CameraEvent.CameraSettingsUpdated(settings))
                                } else if (settings != null) {
                                    Log.d(TAG, "Discarded stale settings snapshot from control epoch $pollEpoch")
                                }
                            } else if (telemetryNow - lastExposurePollTime >= EXPOSURE_POLL_INTERVAL_MS) {
                                lastExposurePollTime = telemetryNow
                                val pollEpoch = controlEpoch
                                val exposure = ptpCamera?.readExposureState()
                                if (exposure != null && pollEpoch == controlEpoch &&
                                    System.currentTimeMillis() >= telemetryResumeAtMs
                                ) {
                                    _events.emit(CameraEvent.ExposureUpdated(exposure))
                                } else if (exposure != null) {
                                    Log.d(TAG, "Discarded stale exposure snapshot from control epoch $pollEpoch")
                                }
                            }
                        }

                        // Pace: ensure minimum interval between successful frames
                        val elapsed = System.currentTimeMillis() - frameStart
                        val sleepMs = maxOf(
                            LIVEVIEW_MIN_FRAME_INTERVAL_MS - elapsed,
                            LIVEVIEW_CONTROL_GAP_MS
                        )
                        delay(sleepMs)
                    } else {
                        errorCount++
                        consecutiveErrors++

                        // Fatal case: we've never seen a single frame and the
                        // camera has been denying us for too long. This is the
                        // wedged-liveview state that happens after some app
                        // swipe-away → reconnect sequences. Only a physical
                        // unplug clears it; surface ConnectionLost so the
                        // user is prompted to do that.
                        val sinceStart = System.currentTimeMillis() - liveviewStartTime
                        if (!hasEverGottenFrame && sinceStart > neverGotFrameFatalMs) {
                            Log.e(TAG, "Liveview never produced a frame in ${sinceStart}ms; keeping the established PC Remote session")
                            isLiveviewActive = false
                            scope.launch {
                                _events.emit(CameraEvent.Error(
                                    "Camera is connected, but Live View did not start. Disconnect and reconnect if the camera remains idle."
                                ))
                            }
                            break
                        }

                        val timeSinceFrame = System.currentTimeMillis() - lastFrameTime
                        val stallThreshold = if (hasEverGottenFrame) stallTimeoutMs else initStallTimeoutMs
                        if (timeSinceFrame > stallThreshold) {
                            pipeRecoveryAttempts++
                            Log.w(TAG, "Liveview stall (no frame in ${timeSinceFrame}ms), " +
                                    "clearing endpoints (recovery attempt $pipeRecoveryAttempts)")
                            ptpCamera?.flushAndResetPipe()
                            delay(200)
                            lastFrameTime = System.currentTimeMillis()
                            consecutiveErrors = 0
                        }

                        // Poll aggressively during slow-frame bursts so we catch
                        // the next available frame without adding latency.
                        delay(10)
                    }

                    // Log stats every 5 seconds
                    val now = System.currentTimeMillis()
                    if (now - lastLogTime >= 5000) {
                        val elapsed = (now - lastLogTime) / 1000.0
                        val fps = frameCount / elapsed
                        Log.d(TAG, "USB liveview: %.1f fps, %d errors (consecutive=%d, recoveries=%d)".format(
                            fps, errorCount, consecutiveErrors, pipeRecoveryAttempts))
                        frameCount = 0
                        errorCount = 0
                        lastLogTime = now
                    }
                } catch (e: kotlinx.coroutines.CancellationException) {
                    // Must rethrow — swallowing this breaks coroutine cancellation
                    throw e
                } catch (e: Exception) {
                    Log.e(TAG, "Liveview frame error: ${e.message}")
                    delay(200)
                }
            }
            Log.d(TAG, "USB liveview loop ended")
        }

        return CameraOperationResult.Success
    }

    override suspend fun stopLiveview(): CameraOperationResult {
        isLiveviewActive = false
        liveviewJob?.cancel()
        liveviewJob = null
        return CameraOperationResult.Success
    }

    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult {
        val requestedAtMs = System.currentTimeMillis()
        priorityControlIntents.incrementAndGet()
        return try {
            withContext(Dispatchers.IO) {
                controlWriteMutex.withLock {
                    val camera = ptpCamera
                        ?: return@withLock CameraOperationResult.Failure("Camera not connected")
                    val safeX = x.coerceIn(0, 639)
                    val safeY = y.coerceIn(0, 479)
                    val epoch = beginControlWrite()
                    try {
                        val commandStartedMs = System.currentTimeMillis()
                        val dispatchWaitMs = commandStartedMs - requestedAtMs
                        val prepStartedMs = System.currentTimeMillis()
                        val prepDebug = camera.prepareMonitorTapAf()
                        val prepMs = System.currentTimeMillis() - prepStartedMs

                        afReleaseJob?.cancel()
                        afReleaseJob = null
                        afGeneration += 1L
                        val generation = afGeneration

                        // Never carry a fallback S1 hold into a new Remote Touch.
                        if (afHalfPressHeld) {
                            camera.setAutofocusPressed(false)
                            afHalfPressHeld = false
                        }

                        val baseline = latestFocusFrameInfo
                        val a7c2WireProbe = camera.deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true

                        // Controlled same-session A/B test. A uses only AF Area Position
                        // (D2DC): no S1, no Remote Touch, no Live View pause. Sony's own
                        // RemoteSampleApp documents AF Area Position as the direct focus-frame
                        // center move. The next tap uses B (D2E4), then alternates again.
                        if (a7c2WireProbe && afWireProbeD2dcNext) {
                            afWireProbeD2dcNext = false
                            val moveStartedMs = System.currentTimeMillis()
                            val move = camera.moveAfAreaPosition(safeX, safeY)
                            val moveAckAtMs = System.currentTimeMillis()
                            val wireAndAckMs = (moveAckAtMs - moveStartedMs - move.queueWaitMs).coerceAtLeast(0L)
                            val ackMs = moveAckAtMs - requestedAtMs
                            if (!move.isSuccess) {
                                synchronized(afStateLock) { pendingAfFrameLatency = null }
                                val message = "AF A D2DC-ONLY FAIL x=$safeX y=$safeY\n$prepDebug\n" +
                                    "D2DC=${PtpConstants.responseCodeName(move.responseCode)} ack=${ackMs}ms next=B"
                                _events.emit(CameraEvent.FocusDebug(message))
                                return@withLock CameraOperationResult.Failure(message)
                            }
                            synchronized(afStateLock) {
                                pendingAfFrameLatency = PendingAfFrameLatency(
                                    generation = generation,
                                    x = safeX,
                                    y = safeY,
                                    requestedAtMs = requestedAtMs,
                                    ackAtMs = moveAckAtMs,
                                    commandDoneAtMs = moveAckAtMs,
                                    path = "A:D2DC-only",
                                    s1Ms = null,
                                    baseline = baseline,
                                    prepDebug = prepDebug
                                )
                            }
                            val message = "AF A D2DC-ONLY x=$safeX y=$safeY\n" +
                                "$prepDebug\n" +
                                "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${move.queueWaitMs}ms " +
                                "wire+ack=${wireAndAckMs}ms ack=${ackMs}ms NO-S1 next=B"
                            Log.d(TAG, message.replace('\n', ' '))
                            _events.emit(CameraEvent.FocusDebug(message))
                            _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
                            return@withLock CameraOperationResult.SuccessWithData(message)
                        }
                        if (a7c2WireProbe) afWireProbeD2dcNext = true

                        // B path: Sony RemoteTouchOperation (D2E4), also without S1.
                        // a7C II fast path: RemoteTouchOperation (D2E4). The previous
                        // D2DC-only isolation proved tap -> returned focus geometry -> Compose
                        // is ~0.14 s, so restore the real Remote Touch path and observe Sony's
                        // own E004/E005/D285 runtime states after the command instead.
                        if (camera.supportsRemoteTouch()) {
                            val touchStartedMs = System.currentTimeMillis()
                            val touch = camera.executeRemoteTouch(safeX, safeY)
                            val touchAckAtMs = System.currentTimeMillis()
                            val wireAndAckMs = (touchAckAtMs - touchStartedMs - touch.queueWaitMs).coerceAtLeast(0L)
                            val ackMs = touchAckAtMs - requestedAtMs
                            if (touch.isSuccess) {
                                synchronized(afStateLock) {
                                    pendingAfFrameLatency = PendingAfFrameLatency(
                                        generation = generation,
                                        x = safeX,
                                        y = safeY,
                                        requestedAtMs = requestedAtMs,
                                        ackAtMs = touchAckAtMs,
                                        commandDoneAtMs = touchAckAtMs,
                                        path = if (a7c2WireProbe) "B:RT(D2E4)" else "RT(D2E4)",
                                        s1Ms = null,
                                        baseline = baseline,
                                        prepDebug = prepDebug
                                    )
                                }
                                val message = (if (a7c2WireProbe) "AF B RT(D2E4)" else "AF RT(D2E4)") +
                                    " x=$safeX y=$safeY\n" +
                                    "$prepDebug\n" +
                                    "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${touch.queueWaitMs}ms " +
                                    "wire+ack=${wireAndAckMs}ms ack=${ackMs}ms NO-S1" +
                                    if (a7c2WireProbe) " next=A" else ""
                                Log.d(TAG, message.replace('\n', ' '))
                                _events.emit(CameraEvent.FocusDebug(message))
                                _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
                                // Previous E004/E005/D285 probe stayed static and its 0x9209
                                // reads add camera load. Do not sample them in this isolation round.
                                return@withLock CameraOperationResult.SuccessWithData(message)
                            }
                            Log.w(TAG, "Remote Touch failed (${PtpConstants.responseCodeName(touch.responseCode)}); using D2DC+S1 fallback")
                        }

                        // Compatibility fallback: move AF Area Position first, then
                        // explicitly press S1. This remains available if Remote Touch
                        // is not exposed/enabled by the connected body.
                        val moveStartedMs = System.currentTimeMillis()
                        val move = camera.moveAfAreaPosition(safeX, safeY)
                        val ackAtMs = System.currentTimeMillis()
                        val wireAndAckMs = (ackAtMs - moveStartedMs - move.queueWaitMs).coerceAtLeast(0L)
                        val ackMs = ackAtMs - requestedAtMs

                        if (!move.isSuccess) {
                            synchronized(afStateLock) { pendingAfFrameLatency = null }
                            val message = "AF D2DC FAIL x=$safeX y=$safeY\n$prepDebug\n" +
                                "D2DC=${PtpConstants.responseCodeName(move.responseCode)} ack=${ackMs}ms"
                            _events.emit(CameraEvent.FocusDebug(message))
                            return@withLock CameraOperationResult.Failure(message)
                        }

                        val s1StartedMs = System.currentTimeMillis()
                        val pressResult = camera.setAutofocusPressed(true)
                        val s1AckAtMs = System.currentTimeMillis()
                        val s1Ms = s1AckAtMs - s1StartedMs
                        afHalfPressHeld = pressResult.isSuccess

                        if (!pressResult.isSuccess) {
                            synchronized(afStateLock) { pendingAfFrameLatency = null }
                            val message = "AF D2DC+S1 FAIL x=$safeX y=$safeY\n$prepDebug\n" +
                                "moveAck=${ackMs}ms s1=${s1Ms}ms ${PtpConstants.responseCodeName(pressResult.responseCode)}"
                            _events.emit(CameraEvent.FocusDebug(message))
                            return@withLock CameraOperationResult.Failure(message)
                        }

                        synchronized(afStateLock) {
                            pendingAfFrameLatency = PendingAfFrameLatency(
                                generation = generation,
                                x = safeX,
                                y = safeY,
                                requestedAtMs = requestedAtMs,
                                ackAtMs = ackAtMs,
                                commandDoneAtMs = s1AckAtMs,
                                path = "D2DC+S1",
                                s1Ms = s1Ms,
                                baseline = baseline,
                                prepDebug = prepDebug
                            )
                        }

                        val message = "AF D2DC+S1 x=$safeX y=$safeY\n" +
                            "$prepDebug\n" +
                            "moveAck=${ackMs}ms s1=${s1Ms}ms bus=${move.queueWaitMs}ms wire+ack=${wireAndAckMs}ms"
                        Log.d(TAG, message.replace('\n', ' '))
                        _events.emit(CameraEvent.FocusDebug(message))
                        _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
                        scheduleAutofocusRelease(camera, generation)
                        CameraOperationResult.SuccessWithData(message)
                    } catch (e: Exception) {
                        Log.e(TAG, "AF target command failed", e)
                        val message = "AF TARGET exception: ${e.message ?: e.javaClass.simpleName}"
                        _events.emit(CameraEvent.FocusDebug(message))
                        CameraOperationResult.Failure(message)
                    } finally {
                        endControlWrite(epoch)
                    }
                }
            }
        } finally {
            priorityControlIntents.decrementAndGet()
        }
    }

    override suspend fun testAfCenter(): CameraOperationResult = withContext(Dispatchers.IO) {
        controlWriteMutex.withLock {
            val camera = ptpCamera
                ?: return@withLock CameraOperationResult.Failure("Camera not connected")
            val epoch = beginControlWrite()
            try {
                val message = camera.testAfCenter()
                _events.emit(CameraEvent.FocusDebug(message))
                _events.emit(CameraEvent.AfTargetUpdated(320, 240))
                CameraOperationResult.SuccessWithData(message)
            } catch (e: Exception) {
                Log.e(TAG, "AF center test failed", e)
                val message = "AF CENTER TEST exception: ${e.message ?: e.javaClass.simpleName}"
                _events.emit(CameraEvent.FocusDebug(message))
                CameraOperationResult.Failure(message)
            } finally {
                endControlWrite(epoch)
            }
        }
    }

    override suspend fun adjustExposure(
        setting: CameraExposureSetting,
        direction: Int
    ): CameraOperationResult = withContext(Dispatchers.IO) {
        val camera = ptpCamera
            ?: return@withContext CameraOperationResult.Failure("Camera not connected")
        val result = camera.adjustExposure(setting, direction)
        _events.emit(CameraEvent.ExposureUpdated(result.state))
        if (result.success) CameraOperationResult.Success
        else CameraOperationResult.Failure(result.message ?: "Exposure change failed")
    }

    override suspend fun setExposure(
        setting: CameraExposureSetting,
        rawValue: Long
    ): CameraOperationResult = withContext(Dispatchers.IO) {
        controlWriteMutex.withLock {
            val camera = ptpCamera
                ?: return@withLock CameraOperationResult.Failure("Camera not connected")
            val epoch = beginControlWrite()
            try {
                val result = camera.setExposureValue(setting, rawValue)
                _events.emit(CameraEvent.ExposureUpdated(result.state))
                if (result.success) CameraOperationResult.Success
                else CameraOperationResult.Failure(result.message ?: "Exposure change failed")
            } finally {
                endControlWrite(epoch)
            }
        }
    }

    override suspend fun setCameraSetting(
        setting: CameraSetting,
        rawValue: Long
    ): CameraOperationResult = withContext(Dispatchers.IO) {
        controlWriteMutex.withLock {
            val camera = ptpCamera
                ?: return@withLock CameraOperationResult.Failure("Camera not connected")
            val epoch = beginControlWrite()
            try {
                val result = camera.setCameraSettingValue(setting, rawValue)
                _events.emit(CameraEvent.CameraSettingsUpdated(result.state))
                if (result.success && setting == CameraSetting.FOCUS_AREA) {
                    camera.invalidateMonitorTapAf()
                }
                if (result.success) CameraOperationResult.Success
                else CameraOperationResult.Failure(result.message ?: "Camera setting change failed")
            } finally {
                endControlWrite(epoch)
            }
        }
    }

    override suspend fun takePhoto(): CameraOperationResult = try {
        // Hard ceiling on total capture time. The retry logic below bounds
        // itself at ~18.5s (10s + 0.5s + 8s queue waits), so 25s absorbs
        // normal jitter without ever letting a truly stuck call hang the UI.
        kotlinx.coroutines.withTimeout(25_000) {
            takePhotoInner()
        }
    } catch (e: kotlinx.coroutines.TimeoutCancellationException) {
        Log.e(TAG, "takePhoto timed out after 25s")
        CameraOperationResult.Failure("Capture took too long — please try again")
    }

    private suspend fun takePhotoInner(): CameraOperationResult = withContext(Dispatchers.IO) {
        val camera = ptpCamera
            ?: return@withContext CameraOperationResult.Failure("Camera not connected")

        val wasLiveview = isLiveviewActive

        // Sony's capture command sequence must not interleave with liveview
        // GetObject transactions. PtpTransport serialises individual requests,
        // but the shutter sequence contains sleeps between half/full/release;
        // without stopping the producer, a liveview GetObject can slip into
        // those gaps and leave the camera/USB pipe in a bad post-capture state.
        if (wasLiveview) {
            isLiveviewActive = false
            val job = liveviewJob
            liveviewJob = null
            if (job != null) {
                val stopped = kotlinx.coroutines.withTimeoutOrNull(3_000) {
                    job.cancelAndJoin()
                    true
                } ?: false
                if (!stopped) {
                    Log.e(TAG, "Liveview did not stop cleanly before capture")
                    return@withContext CameraOperationResult.Failure(
                        "Live view is busy — please try the photo again"
                    )
                }
            }
            // One short quiet period after the final liveview response.
            delay(80)
        }

        try {
            var shutterSignalled = false
            for (attempt in 1..2) {
                Log.d(TAG, "Capture attempt $attempt/2")

                val captureFired = camera.initiateCapture {
                    if (!shutterSignalled) {
                        shutterSignalled = true
                        _events.tryEmit(CameraEvent.ShutterFired)
                        Log.d(TAG, "Shutter fired — capture transaction isolated from liveview")
                    }
                }
                if (!captureFired) {
                    Log.w(TAG, "Shutter command failed on attempt $attempt")
                    if (attempt < 2) {
                        delay(500)
                        continue
                    }
                    return@withContext CameraOperationResult.Failure(
                        "Camera didn't respond to shutter — please try again"
                    )
                }

                val queueWaitMs = if (attempt == 1) 10_000L else 8_000L
                val fullResJpeg = try {
                    camera.downloadQueuedPhoto(maxWaitMs = queueWaitMs)
                } catch (e: Exception) {
                    Log.w(TAG, "Download error on attempt $attempt: ${e.message}")
                    null
                }

                if (fullResJpeg != null && fullResJpeg.size >= 200_000) {
                    val bitmap = BitmapFactory.decodeByteArray(fullResJpeg, 0, fullResJpeg.size)
                    if (bitmap != null) {
                        Log.d(TAG, "Photo captured (full-res): " +
                                "${fullResJpeg.size / 1024}KB, ${bitmap.width}x${bitmap.height} " +
                                "on attempt $attempt")
                        _events.emit(CameraEvent.PhotoCaptured(bitmap))
                        return@withContext CameraOperationResult.Success
                    }
                    Log.w(TAG, "Full-res JPEG decode failed on attempt $attempt " +
                            "(size=${fullResJpeg.size}B)")
                } else {
                    Log.w(TAG, "Full-res download failed on attempt $attempt " +
                            "(size=${fullResJpeg?.size ?: 0}B)")
                }

                if (attempt < 2) delay(500)
            }

            CameraOperationResult.Failure("Photo didn't save — please try again")
        } catch (e: Exception) {
            Log.e(TAG, "Photo capture error", e)
            CameraOperationResult.Failure("Photo capture failed — please try again")
        } finally {
            if (wasLiveview && ptpCamera === camera && _connectionState.value is CameraConnectionState.Ready) {
                withContext(kotlinx.coroutines.NonCancellable) {
                    // D215 stays at 0x80xx while the SDRAM image is still owned
                    // by the transfer path. Wait for the high bit to clear before
                    // asking for the liveview object again.
                    val idle = withContext(Dispatchers.IO) { camera.waitForCaptureIdle(3_500L) }
                    Log.d(TAG, "Post-capture queue idle=$idle; restarting liveview")
                    delay(if (idle) 250L else 700L)
                    postCaptureResumeDeadlineMs = System.currentTimeMillis() + 18_000L
                    startLiveview()
                }
            }
        }
    }

    /**
     * A fatal stream failure is also a connection failure. Release the old
     * interface/session before telling the UI, otherwise a subsequent Connect
     * tries to claim a USB interface that this same process still owns.
     */
    private fun handleFatalConnectionLoss(reason: String) {
        if (_connectionState.value is CameraConnectionState.Disconnected) return
        Log.e(TAG, "Connection lost: $reason")
        closeUsbResources()
        _cameraName.value = null
        _connectionState.value = CameraConnectionState.Disconnected
        scope.launch { _events.emit(CameraEvent.ConnectionLost) }
    }

    override fun disconnect() {
        Log.d(TAG, "Disconnecting USB camera")
        // User-initiated disconnect always cancels a pending reconnect.
        isAwaitingReattach = false
        reconnectTimeoutJob?.cancel()
        reconnectTimeoutJob = null

        closeUsbResources()
        _cameraName.value = null
        _connectionState.value = CameraConnectionState.Disconnected
    }

    /**
     * Permanently tear down this engine. Called by the owning service in
     * onDestroy: unregisters the USB receiver, releases USB resources, and
     * cancels all coroutines. The instance must not be used afterwards.
     */
    fun destroy() {
        Log.d(TAG, "Destroying USB camera engine")
        isAwaitingReattach = false
        reconnectTimeoutJob?.cancel()
        reconnectTimeoutJob = null
        try {
            context.unregisterReceiver(usbReceiver)
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "usbReceiver already unregistered")
        }
        closeUsbResources()
        _cameraName.value = null
        _connectionState.value = CameraConnectionState.Disconnected
        scope.cancel()
    }

    /**
     * Release USB resources without touching the connection state flow.
     * Used both by [disconnect] (user intent) and the reconnect grace flow
     * (where we want to hold the UI in Connecting while waiting for reattach).
     *
     * Steals the current resource handles into locals, nulls the fields
     * immediately, and does the actual close work on Dispatchers.IO so
     * closeSession()'s USB bulk transfer doesn't block the caller's thread.
     * Since nothing else can use the handles after they're nulled out, doing
     * the close asynchronously is safe.
     */
    private fun closeUsbResources() {
        isLiveviewActive = false
        afWireProbeD2dcNext = true
        liveviewJob?.cancel()
        liveviewJob = null
        afReleaseJob?.cancel()
        afReleaseJob = null
        remoteTouchRuntimeProbeJob?.cancel()
        remoteTouchRuntimeProbeJob = null
        afHalfPressHeld = false
        synchronized(afStateLock) { pendingAfFrameLatency = null }
        latestFocusFrameInfo = null
        controlWriteActive = false
        priorityControlIntents.set(0)

        // Cancel any in-flight connect so its finally-block unwinds the
        // resources it allocated rather than silently committing them after
        // we've already decided to tear down.
        connectJob?.cancel()
        connectJob = null

        val camera = ptpCamera
        val conn = usbConnection
        val iface = ptpInterface
        ptpCamera = null
        usbConnection = null
        ptpInterface = null
        usbDevice = null

        if (camera == null && conn == null) {
            Log.d(TAG, "closeUsbResources: nothing to tear down (camera/conn already null)")
            return
        }

        // Run the teardown on a scope that survives engine.destroy()'s
        // scope.cancel — otherwise endSession can be cancelled before its
        // USB transactions reach the camera.
        teardownJob = teardownScope.launch {
            Log.d(TAG, "USB teardown: ending camera session")
            try {
                // Graceful end: release Sony priority + PTP CloseSession so
                // the camera knows we're done and returns to normal operation.
                camera?.endSession()
            } catch (e: Exception) {
                Log.w(TAG, "endSession during teardown: ${e.message}")
            }
            try {
                if (iface != null) conn?.releaseInterface(iface)
            } catch (e: Exception) {
                Log.w(TAG, "releaseInterface during teardown: ${e.message}")
            }
            try {
                conn?.close()
            } catch (e: Exception) {
                Log.w(TAG, "connection close during teardown: ${e.message}")
            }
        }
    }

    override fun isReady(): Boolean = _connectionState.value is CameraConnectionState.Ready

    // ══════════════════════════════════════════════
    // USB-specific methods
    // ══════════════════════════════════════════════

    /**
     * Scan for attached Sony PTP cameras.
     */
    fun findSonyCamera(): UsbDevice? {
        return usbManager.deviceList.values.firstOrNull { device ->
            device.vendorId == PtpConstants.SONY_VENDOR_ID && hasPtpInterface(device)
        }
    }

    /**
     * Connect to a Sony camera. Requests USB permission if needed.
     */
    fun connectToCamera(device: UsbDevice? = null) {
        val target = device ?: findSonyCamera()
        if (target == null) {
            _connectionState.value = CameraConnectionState.Error(
                "No camera detected. Check the USB cable is plugged in at both ends."
            )
            return
        }

        _connectionState.value = CameraConnectionState.Connecting

        if (usbManager.hasPermission(target)) {
            connectJob?.cancel()
            connectJob = scope.launch { connectToDevice(target) }
        } else {
            Log.d(TAG, "Requesting USB permission for ${target.deviceName}")
            // Explicit intent required on Android 14+ (targeting U+)
            val intent = Intent(ACTION_USB_PERMISSION).apply {
                setPackage(context.packageName)
            }
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE or
                        PendingIntent.FLAG_ALLOW_UNSAFE_IMPLICIT_INTENT
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }
            val permissionIntent = PendingIntent.getBroadcast(context, 0, intent, flags)
            usbManager.requestPermission(target, permissionIntent)
        }
    }

    /**
     * Internal: connect to a USB device after permission is granted.
     *
     * All claimed/opened resources are held in local vars until the handshake
     * fully succeeds; only then do we publish them to fields. Any early return
     * (missing endpoints, openSession failure, cancellation, exception) runs
     * through finally and releases whatever we allocated — no more leaked
     * interfaces or connections on partial failure.
     */
    private suspend fun connectToDevice(device: UsbDevice) = withContext(Dispatchers.IO) {
        var localConn: UsbDeviceConnection? = null
        var localIface: UsbInterface? = null
        var ifaceClaimed = false
        var localCamera: SonyPtpCamera? = null
        var committed = false

        try {
            _connectionState.value = CameraConnectionState.Connecting

            // A previous user disconnect may still be finishing its final
            // CloseSession on another UsbDeviceConnection. Give it a short,
            // bounded chance to finish so old teardown commands cannot land in
            // the middle of this new session.
            teardownJob?.let { previousTeardown ->
                if (previousTeardown.isActive) {
                    Log.d(TAG, "Waiting briefly for previous USB teardown")
                    kotlinx.coroutines.withTimeoutOrNull(1800) { previousTeardown.join() }
                }
            }
            teardownJob = null

            // Log all device interfaces for debugging
            Log.d(TAG, "USB Device: vendor=0x${device.vendorId.toString(16)}, product=0x${device.productId.toString(16)}, class=${device.deviceClass}")
            Log.d(TAG, "  Interfaces: ${device.interfaceCount}")
            for (i in 0 until device.interfaceCount) {
                val intf = device.getInterface(i)
                Log.d(TAG, "  Interface $i: class=${intf.interfaceClass} subclass=${intf.interfaceSubclass} protocol=${intf.interfaceProtocol} endpoints=${intf.endpointCount}")
                for (e in 0 until intf.endpointCount) {
                    val ep = intf.getEndpoint(e)
                    Log.d(TAG, "    Endpoint $e: type=${ep.type} dir=${ep.direction} maxPacket=${ep.maxPacketSize}")
                }
            }

            // Find PTP interface
            localIface = findPtpInterface(device)
            if (localIface == null) {
                _connectionState.value = CameraConnectionState.Error(
                    "Camera USB mode is wrong. On a7C II set USB Connection Mode to 'Remote Shoot (PC Remote)' and USB LUN to 'Single'."
                )
                return@withContext
            }

            // Open connection
            localConn = usbManager.openDevice(device)
            if (localConn == null) {
                _connectionState.value = CameraConnectionState.Error(
                    "Couldn't open the camera. Unplug the USB cable, wait a moment, then plug it back in."
                )
                return@withContext
            }

            // Force-claim the interface. The `true` parameter detaches any kernel
            // driver (e.g., Android's MTP service) that may have auto-claimed it.
            // We may need multiple attempts as the MTP service can re-attach.
            for (attempt in 1..3) {
                if (localConn.claimInterface(localIface, true)) {
                    ifaceClaimed = true
                    Log.d(TAG, "Claimed PTP interface on attempt $attempt")
                    break
                }
                Log.w(TAG, "Failed to claim interface, attempt $attempt/3, retrying...")
                Thread.sleep(500)
            }
            if (!ifaceClaimed) {
                _connectionState.value = CameraConnectionState.Error(
                    "Another app is using the camera. Close other photo apps, unplug the cable, and try again."
                )
                return@withContext
            }

            // Find endpoints
            var bulkIn: UsbEndpoint? = null
            var bulkOut: UsbEndpoint? = null
            var interruptIn: UsbEndpoint? = null

            for (i in 0 until localIface.endpointCount) {
                val ep = localIface.getEndpoint(i)
                when {
                    ep.type == UsbConstants.USB_ENDPOINT_XFER_BULK &&
                            ep.direction == UsbConstants.USB_DIR_IN -> bulkIn = ep
                    ep.type == UsbConstants.USB_ENDPOINT_XFER_BULK &&
                            ep.direction == UsbConstants.USB_DIR_OUT -> bulkOut = ep
                    ep.type == UsbConstants.USB_ENDPOINT_XFER_INT &&
                            ep.direction == UsbConstants.USB_DIR_IN -> interruptIn = ep
                }
            }

            if (bulkIn == null || bulkOut == null) {
                _connectionState.value = CameraConnectionState.Error(
                    "Camera USB mode is wrong. On a7C II set USB Connection Mode to 'Remote Shoot (PC Remote)' and USB LUN to 'Single'."
                )
                return@withContext
            }

            _connectionState.value = CameraConnectionState.Initializing

            // Normal path first: claim -> OpenSession. Do not reset a healthy,
            // freshly-enumerated Sony camera before its first PTP command.
            val transport = PtpTransport(localConn, bulkOut, bulkIn, interruptIn)
            localCamera = SonyPtpCamera(transport)

            if (!localCamera.openSession()) {
                // One bounded recovery only after a genuine OpenSession failure,
                // matching mature PTP clients. No close/reopen loop and no 7.5s
                // blind General-Error sleeps.
                Log.w(TAG, "Initial OpenSession failed — attempting one PTP Device Reset recovery")
                transport.recoverAfterFailedOpenSession(localIface.id)
                localCamera = SonyPtpCamera(transport)
                if (!localCamera.openSession()) {
                    _connectionState.value = CameraConnectionState.Error(
                        "Camera did not accept a PTP session. Close other camera apps and reconnect."
                    )
                    return@withContext
                }
            }

            if (!localCamera.getDeviceInfo()) {
                Log.w(TAG, "Could not get device info, continuing with generic Sony identity")
            }

            // Sony Camera Remote Command treats connection setup and live-view
            // retrieval as separate operations. Once OpenSession + the documented
            // SDIO vendor handshake succeeds, publish the device as connected. Do
            // not recycle a valid session merely because the first live-view object
            // is late; that speculative reopen path was a major source of long
            // "Camera Initializing" stalls on the a7C II.
            val handshakeStarted = System.currentTimeMillis()
            val remoteReady = localCamera.initSonyExtension()
            Log.d(TAG, "Sony SDIO handshake completed=${remoteReady} in " +
                    "${System.currentTimeMillis() - handshakeStarted}ms")
            if (!remoteReady) {
                _connectionState.value = CameraConnectionState.Error(
                    "Sony PC Remote handshake failed. Close other camera-control apps, verify PC Remote USB mode, then reconnect."
                )
                return@withContext
            }

            usbDevice = device
            usbConnection = localConn
            ptpInterface = localIface
            ptpCamera = localCamera
            committed = true

            _cameraName.value = localCamera.deviceName ?: "Sony a7C II (USB)"
            Log.d(TAG, "USB camera connected: ${localCamera.deviceName}; starting liveview separately")
            _connectionState.value = CameraConnectionState.Ready
            _events.emit(CameraEvent.FocusDebug("AF READY | ${localCamera.monitorAfDebug()}"))

            // Live view is a post-connect operation, matching Sony's sample/API
            // model. The UI can now distinguish a connected camera waiting for
            // frames from a camera still stuck in the handshake.
            startLiveview()
        } catch (cancel: kotlinx.coroutines.CancellationException) {
            // Caller (disconnect / detach) is tearing us down. Don't flip to Error —
            // let the cleanup path already in flight set the authoritative state.
            Log.d(TAG, "Connect attempt cancelled")
            throw cancel
        } catch (e: Exception) {
            Log.e(TAG, "USB connection error", e)
            _connectionState.value = CameraConnectionState.Error(
                "Couldn't connect to the camera. Unplug and replug the USB cable, then try again."
            )
        } finally {
            // Release anything we opened if we didn't fully commit. Safe to call
            // on null refs / already-closed handles — each wrapped in try/catch.
            if (!committed) {
                try { localCamera?.closeSession() } catch (e: Exception) { Log.w(TAG, "closeSession rollback: ${e.message}") }
                if (ifaceClaimed && localIface != null && localConn != null) {
                    try { localConn.releaseInterface(localIface) } catch (e: Exception) { Log.w(TAG, "releaseInterface rollback: ${e.message}") }
                }
                try { localConn?.close() } catch (e: Exception) { Log.w(TAG, "connection close rollback: ${e.message}") }
            }
        }
    }

    private fun hasPtpInterface(device: UsbDevice): Boolean = findPtpInterface(device) != null

    private fun interfaceHasBulkPair(iface: UsbInterface): Boolean {
        var bulkIn = false
        var bulkOut = false
        for (e in 0 until iface.endpointCount) {
            val ep = iface.getEndpoint(e)
            if (ep.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
            if (ep.direction == UsbConstants.USB_DIR_IN) bulkIn = true
            if (ep.direction == UsbConstants.USB_DIR_OUT) bulkOut = true
        }
        return bulkIn && bulkOut
    }

    private fun interfaceHasInterruptIn(iface: UsbInterface): Boolean {
        for (e in 0 until iface.endpointCount) {
            val ep = iface.getEndpoint(e)
            if (ep.type == UsbConstants.USB_ENDPOINT_XFER_INT &&
                ep.direction == UsbConstants.USB_DIR_IN) return true
        }
        return false
    }

    private fun findPtpInterface(device: UsbDevice): UsbInterface? {
        val interfaces = (0 until device.interfaceCount).map { device.getInterface(it) }

        // A real PTP control interface needs both bulk directions. Prefer the one
        // that also exposes the interrupt event endpoint; this avoids depending on
        // Android's interface enumeration order when the camera exposes multiple LUNs.
        return interfaces.firstOrNull {
            it.interfaceClass == PtpConstants.USB_CLASS_PTP &&
                interfaceHasBulkPair(it) && interfaceHasInterruptIn(it)
        } ?: interfaces.firstOrNull {
            it.interfaceClass == PtpConstants.USB_CLASS_PTP && interfaceHasBulkPair(it)
        } ?: interfaces.firstOrNull {
            it.interfaceClass == 255 && interfaceHasBulkPair(it)
        }
    }
}
