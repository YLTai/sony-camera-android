from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
screen_path = root / "demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt"
sony_path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
manager_path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt"
transport_path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/PtpTransport.kt"

screen = screen_path.read_text()
sony = sony_path.read_text()
manager = manager_path.read_text()
transport = transport_path.read_text()

# ---------------------------------------------------------------------------
# AF UI: remove the optimistic marker added in the previous round. Keep only
# latest-target conflation so rapid taps do not create an unbounded queue.
# The visible focus frame should now come from the camera's real live-view
# FocalFrameInfo, making latency measurable instead of hidden by UI prediction.
# ---------------------------------------------------------------------------
screen = screen.replace(
'''            var afBusy by remember { mutableStateOf(false) }\n            var queuedAfPoint by remember { mutableStateOf<Pair<Int, Int>?>(null) }\n            var optimisticAfPoint by remember { mutableStateOf<Offset?>(null) }\n            var afRequestJob by remember { mutableStateOf<Job?>(null) }\n''',
'''            var afBusy by remember { mutableStateOf(false) }\n            var queuedAfPoint by remember { mutableStateOf<Pair<Int, Int>?>(null) }\n            var afRequestJob by remember { mutableStateOf<Job?>(null) }\n''')

screen = screen.replace(
'''                        is CameraEvent.FocusFramesUpdated -> {\n                            focusFrames = event.info.frames\n                            // Do not let a delayed camera event pull the marker back to\n                            // the previous AF point while a newer tap is being shown.\n                            if (optimisticAfPoint == null) {\n                                focusPoint = preferredFocusPivot(event.info.frames)\n                            }\n                        }\n''',
'''                        is CameraEvent.FocusFramesUpdated -> {\n                            focusFrames = event.info.frames\n                            focusPoint = preferredFocusPivot(event.info.frames)\n                        }\n''')

screen = screen.replace(
'''                    queuedAfPoint = null\n                    optimisticAfPoint = null\n                    afRequestJob?.cancel()\n''',
'''                    queuedAfPoint = null\n                    afRequestJob?.cancel()\n''')

old_request = '''            fun requestAf(x: Int, y: Int) {\n                if (state !is CameraConnectionState.Ready) return\n                val targetX = x.coerceIn(0, 639)\n                val targetY = y.coerceIn(0, 479)\n                val point = Offset(targetX / 639f, targetY / 479f)\n\n                // Give immediate visual feedback. USB remains strictly serialized:\n                // while one setAfPoint is in flight, additional taps only replace\n                // this single queued target instead of starting concurrent writes.\n                focusPoint = point\n                optimisticAfPoint = point\n                queuedAfPoint = targetX to targetY\n                scope.launch {\n                    delay(900)\n                    if (optimisticAfPoint == point) optimisticAfPoint = null\n                }\n\n                if (afRequestJob?.isActive == true) return\n                afRequestJob = scope.launch {\n                    while (true) {\n                        val target = queuedAfPoint ?: break\n                        queuedAfPoint = null\n                        afBusy = true\n                        val result = camera.setAfPoint(target.first, target.second)\n                        if (result is CameraOperationResult.Failure) lastError = result.message\n                    }\n                    afBusy = false\n                }\n            }\n'''
new_request = '''            fun requestAf(x: Int, y: Int) {\n                if (state !is CameraConnectionState.Ready) return\n                val targetX = x.coerceIn(0, 639)\n                val targetY = y.coerceIn(0, 479)\n\n                // Keep at most one latest target while a USB control is in flight.\n                // We intentionally do NOT draw an optimistic focus frame here: the\n                // monitor now reflects only the camera's returned FocalFrameInfo.\n                focusPoint = Offset(targetX / 639f, targetY / 479f)\n                queuedAfPoint = targetX to targetY\n                if (afRequestJob?.isActive == true) return\n                afRequestJob = scope.launch {\n                    while (true) {\n                        val target = queuedAfPoint ?: break\n                        queuedAfPoint = null\n                        afBusy = true\n                        val result = camera.setAfPoint(target.first, target.second)\n                        if (result is CameraOperationResult.Failure) lastError = result.message\n                    }\n                    afBusy = false\n                }\n            }\n'''
assert old_request in screen, "requestAf block changed"
screen = screen.replace(old_request, new_request)

screen = screen.replace('''                    afBusy = afBusy,\n                    optimisticAfPoint = optimisticAfPoint,\n                    onAfPoint = ::requestAf,\n''', '''                    afBusy = afBusy,\n                    onAfPoint = ::requestAf,\n''')
screen = screen.replace('''    afBusy: Boolean,\n    optimisticAfPoint: Offset?,\n    onAfPoint: (Int, Int) -> Unit,\n''', '''    afBusy: Boolean,\n    onAfPoint: (Int, Int) -> Unit,\n''')
screen = screen.replace('''                FocusAreaSelectionOverlay(source, containerSize, focusAreaRaw, focusFrames, Modifier.fillMaxSize())\n                optimisticAfPoint?.let { point ->\n                    OptimisticFocusPointOverlay(source, containerSize, point, Modifier.fillMaxSize())\n                }\n                if (focusFrames.isNotEmpty() && containerSize != IntSize.Zero) {\n''', '''                FocusAreaSelectionOverlay(source, containerSize, focusAreaRaw, focusFrames, Modifier.fillMaxSize())\n                if (focusFrames.isNotEmpty() && containerSize != IntSize.Zero) {\n''')

screen, removed_overlay = re.subn(
    r'''\n@Composable\nprivate fun OptimisticFocusPointOverlay\([\s\S]*?\n}\n\n@Composable\nprivate fun FocusAreaSelectionOverlay''',
    '''\n@Composable\nprivate fun FocusAreaSelectionOverlay''',
    screen,
    count=1,
)
assert removed_overlay == 1, "optimistic overlay block not found"

# ---------------------------------------------------------------------------
# AF protocol: Sony Camera Remote Command models AF Area Position as its own
# control. Do not synthesize a shutter half-press after moving D2DC. Half-press
# remains part of capture/focus actions, not coordinate placement.
# ---------------------------------------------------------------------------
old_af = '''    /**\n     * Move the Sony logical AF target and immediately trigger a short AF\n     * half-press. A7C II uses a 640x480 logical grid for D2DC.\n     */\n    fun setAfPoint(x: Int, y: Int): String = commandAfPoint("AF TARGET", x, y)\n\n    /** Diagnostic convenience entry point retained by the demo. */\n    fun testAfCenter(): String = commandAfPoint("AF CENTER TEST", 320, 240)\n\n    private fun commandAfPoint(label: String, x: Int, y: Int): String {\n        val safeX = x.coerceIn(0, 639)\n        val safeY = y.coerceIn(0, 479)\n        val setResult = setAfAreaPosition(safeX, safeY)\n        Thread.sleep(120)\n        val pressResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 2)\n        Thread.sleep(450)\n        val releaseResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 1)\n        return buildString {\n            append(label).append(" x=").append(safeX).append(" y=").append(safeY)\n            append(" | D2DC/9207=")\n            append(PtpConstants.responseCodeName(setResult.responseCode))\n            append(" | halfPress=")\n            append(PtpConstants.responseCodeName(pressResult.responseCode))\n            append(" | release=")\n            append(PtpConstants.responseCodeName(releaseResult.responseCode))\n        }\n    }\n'''
new_af = '''    /**\n     * Move the Sony logical AF target. Camera Remote Command exposes AF Area\n     * Position (0xD2DC) as a standalone control; moving the target must not\n     * implicitly press the shutter. A7C II uses a 640x480 logical grid.\n     */\n    fun setAfPoint(x: Int, y: Int): String = commandAfPoint("AF TARGET", x, y)\n\n    /** Diagnostic convenience entry point retained by the demo. */\n    fun testAfCenter(): String = commandAfPoint("AF CENTER TEST", 320, 240)\n\n    private fun commandAfPoint(label: String, x: Int, y: Int): String {\n        val safeX = x.coerceIn(0, 639)\n        val safeY = y.coerceIn(0, 479)\n        val setResult = setAfAreaPosition(safeX, safeY)\n        return buildString {\n            append(label).append(" x=").append(safeX).append(" y=").append(safeY)\n            append(" | D2DC/9207=")\n            append(PtpConstants.responseCodeName(setResult.responseCode))\n        }\n    }\n'''
assert old_af in sony, "AF command block changed"
sony = sony.replace(old_af, new_af)

# ---------------------------------------------------------------------------
# Transport: PTP is a single serialized command channel. Make arbitration fair
# so a user control already waiting behind one GetObject is served before the
# live-view loop can immediately reacquire the transport for another frame.
# ---------------------------------------------------------------------------
assert 'private val lock = ReentrantLock()' in transport
transport = transport.replace(
    'private val lock = ReentrantLock()',
    'private val lock = ReentrantLock(true) // fair: waiting user controls run before the next live-view poll'
)

# ---------------------------------------------------------------------------
# Manager AF path: use the same user-control serialization/telemetry quieting
# as exposure and settings. This prevents a telemetry poll from being started
# while the AF-area control is pending.
# ---------------------------------------------------------------------------
old_manager_af = '''    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult = withContext(Dispatchers.IO) {\n        val camera = ptpCamera\n            ?: return@withContext CameraOperationResult.Failure("Camera not connected")\n        val safeX = x.coerceIn(0, 639)\n        val safeY = y.coerceIn(0, 479)\n        try {\n            val message = camera.setAfPoint(safeX, safeY)\n            _events.emit(CameraEvent.FocusDebug(message))\n            _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))\n            CameraOperationResult.SuccessWithData(message)\n        } catch (e: Exception) {\n            Log.e(TAG, "AF target command failed", e)\n            val message = "AF TARGET exception: ${e.message ?: e.javaClass.simpleName}"\n            _events.emit(CameraEvent.FocusDebug(message))\n            CameraOperationResult.Failure(message)\n        }\n    }\n'''
new_manager_af = '''    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult = withContext(Dispatchers.IO) {\n        controlWriteMutex.withLock {\n            val camera = ptpCamera\n                ?: return@withLock CameraOperationResult.Failure("Camera not connected")\n            val safeX = x.coerceIn(0, 639)\n            val safeY = y.coerceIn(0, 479)\n            val epoch = beginControlWrite()\n            try {\n                val started = System.currentTimeMillis()\n                val message = camera.setAfPoint(safeX, safeY)\n                Log.d(TAG, "AF area position command completed in ${System.currentTimeMillis() - started}ms")\n                _events.emit(CameraEvent.FocusDebug(message))\n                _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))\n                CameraOperationResult.SuccessWithData(message)\n            } catch (e: Exception) {\n                Log.e(TAG, "AF target command failed", e)\n                val message = "AF TARGET exception: ${e.message ?: e.javaClass.simpleName}"\n                _events.emit(CameraEvent.FocusDebug(message))\n                CameraOperationResult.Failure(message)\n            } finally {\n                endControlWrite(epoch)\n            }\n        }\n    }\n'''
assert old_manager_af in manager, "manager AF block changed"
manager = manager.replace(old_manager_af, new_manager_af)

# Keep the diagnostic center action on the same control lane too.
old_test = '''    override suspend fun testAfCenter(): CameraOperationResult = withContext(Dispatchers.IO) {\n        val camera = ptpCamera\n            ?: return@withContext CameraOperationResult.Failure("Camera not connected")\n        try {\n            val message = camera.testAfCenter()\n            _events.emit(CameraEvent.FocusDebug(message))\n            _events.emit(CameraEvent.AfTargetUpdated(320, 240))\n            CameraOperationResult.SuccessWithData(message)\n        } catch (e: Exception) {\n            Log.e(TAG, "AF center test failed", e)\n            val message = "AF CENTER TEST exception: ${e.message ?: e.javaClass.simpleName}"\n            _events.emit(CameraEvent.FocusDebug(message))\n            CameraOperationResult.Failure(message)\n        }\n    }\n'''
new_test = '''    override suspend fun testAfCenter(): CameraOperationResult = withContext(Dispatchers.IO) {\n        controlWriteMutex.withLock {\n            val camera = ptpCamera\n                ?: return@withLock CameraOperationResult.Failure("Camera not connected")\n            val epoch = beginControlWrite()\n            try {\n                val message = camera.testAfCenter()\n                _events.emit(CameraEvent.FocusDebug(message))\n                _events.emit(CameraEvent.AfTargetUpdated(320, 240))\n                CameraOperationResult.SuccessWithData(message)\n            } catch (e: Exception) {\n                Log.e(TAG, "AF center test failed", e)\n                val message = "AF CENTER TEST exception: ${e.message ?: e.javaClass.simpleName}"\n                _events.emit(CameraEvent.FocusDebug(message))\n                CameraOperationResult.Failure(message)\n            } finally {\n                endControlWrite(epoch)\n            }\n        }\n    }\n'''
assert old_test in manager, "test AF block changed"
manager = manager.replace(old_test, new_test)

# ---------------------------------------------------------------------------
# Connection: mirror Sony's documented/sample separation of connection and
# live-view operations. Successful PTP+SDIO handshake means connected. Do not
# require a live-view frame and do not tear down/reopen a healthy session just
# because the first GetObject is late.
# ---------------------------------------------------------------------------
manager = manager.replace('        private const val FIRST_LIVEVIEW_PROBE_MS = 4_000L\n', '')

old_connect = '''            // The vendor handshake alone is not enough to declare success on a7C II.\n            // A stale remote owner can accept SDIO commands yet deny 0xFFFFC002 forever.\n            // Verify the real live-view object first. If that fails, reproduce the useful\n            // part of a clean Monitor+ open/close cycle: release priority, CloseSession,\n            // then reopen and run the complete protocol-3 handshake exactly once.\n            var remoteReady = localCamera.initSonyExtension() && probeLiveViewReady(localCamera)\n            if (!remoteReady) {\n                Log.w(TAG, "Sony remote session not stream-ready — recycling PC Remote session once")\n                runCatching { localCamera.endSession() }\n                    .onFailure { Log.w(TAG, "Remote-session release failed: ${it.message}") }\n                runCatching { localCamera.flushAndResetPipe() }\n                delay(350)\n\n                localCamera = SonyPtpCamera(transport)\n                var reopened = localCamera.openSession()\n                if (!reopened) {\n                    Log.w(TAG, "Recycled OpenSession failed — using one class-reset recovery")\n                    transport.recoverAfterFailedOpenSession(localIface.id)\n                    localCamera = SonyPtpCamera(transport)\n                    reopened = localCamera.openSession()\n                }\n\n                if (reopened) {\n                    if (!localCamera.getDeviceInfo()) {\n                        Log.w(TAG, "Could not refresh device info after remote-session recycle")\n                    }\n                    remoteReady = localCamera.initSonyExtension() && probeLiveViewReady(localCamera)\n                }\n            }\n\n            if (!remoteReady) {\n                _connectionState.value = CameraConnectionState.Error(\n                    "a7C II PC Remote session did not become stream-ready. Set USB Connection Mode to PC Remote, USB LUN to Single, and Network > PC Remote Function > PC Remote to Off, then reconnect."\n                )\n                return@withContext\n            }\n\n            // Commit only after a real live-view dataset has been observed. Property\n            // discovery is intentionally deferred until the stream has warmed up.\n            usbDevice = device\n            usbConnection = localConn\n            ptpInterface = localIface\n            ptpCamera = localCamera\n            committed = true\n\n            _cameraName.value = localCamera.deviceName ?: "Sony a7C II (USB)"\n            Log.d(TAG, "USB camera stream-ready: ${localCamera.deviceName}")\n            _connectionState.value = CameraConnectionState.Ready\n\n            Log.d(TAG, "Auto-starting verified USB liveview...")\n            startLiveview()\n'''
new_connect = '''            // Sony Camera Remote Command treats connection setup and live-view\n            // retrieval as separate operations. Once OpenSession + the documented\n            // SDIO vendor handshake succeeds, publish the device as connected. Do\n            // not recycle a valid session merely because the first live-view object\n            // is late; that speculative reopen path was a major source of long\n            // "Camera Initializing" stalls on the a7C II.\n            val handshakeStarted = System.currentTimeMillis()\n            val remoteReady = localCamera.initSonyExtension()\n            Log.d(TAG, "Sony SDIO handshake completed=${remoteReady} in " +\n                    "${System.currentTimeMillis() - handshakeStarted}ms")\n            if (!remoteReady) {\n                _connectionState.value = CameraConnectionState.Error(\n                    "Sony PC Remote handshake failed. Close other camera-control apps, verify PC Remote USB mode, then reconnect."\n                )\n                return@withContext\n            }\n\n            usbDevice = device\n            usbConnection = localConn\n            ptpInterface = localIface\n            ptpCamera = localCamera\n            committed = true\n\n            _cameraName.value = localCamera.deviceName ?: "Sony a7C II (USB)"\n            Log.d(TAG, "USB camera connected: ${localCamera.deviceName}; starting liveview separately")\n            _connectionState.value = CameraConnectionState.Ready\n\n            // Live view is a post-connect operation, matching Sony's sample/API\n            // model. The UI can now distinguish a connected camera waiting for\n            // frames from a camera still stuck in the handshake.\n            startLiveview()\n'''
assert old_connect in manager, "connect recycle block changed"
manager = manager.replace(old_connect, new_connect)

manager, probe_removed = re.subn(
    r'''\n    private suspend fun probeLiveViewReady\(camera: SonyPtpCamera\): Boolean \{[\s\S]*?\n    }\n\n    private fun hasPtpInterface''',
    '''\n    private fun hasPtpInterface''',
    manager,
    count=1,
)
assert probe_removed == 1, "probeLiveViewReady block not found"

# Do not convert a live-view-only startup failure into a connection teardown.
old_fatal = '''                        if (!hasEverGottenFrame && sinceStart > neverGotFrameFatalMs) {\n                            Log.e(TAG, "Liveview never produced a frame in ${sinceStart}ms — releasing stale USB session")\n                            isLiveviewActive = false\n                            scope.launch { handleFatalConnectionLoss("Liveview did not recover") }\n                            break\n                        }\n'''
new_fatal = '''                        if (!hasEverGottenFrame && sinceStart > neverGotFrameFatalMs) {\n                            Log.e(TAG, "Liveview never produced a frame in ${sinceStart}ms; keeping the established PC Remote session")\n                            isLiveviewActive = false\n                            scope.launch {\n                                _events.emit(CameraEvent.Error(\n                                    "Camera is connected, but Live View did not start. Disconnect and reconnect if the camera remains idle."\n                                ))\n                            }\n                            break\n                        }\n'''
assert old_fatal in manager, "liveview fatal block changed"
manager = manager.replace(old_fatal, new_fatal)

screen_path.write_text(screen)
sony_path.write_text(sony)
manager_path.write_text(manager)
transport_path.write_text(transport)
Path(__file__).unlink()
