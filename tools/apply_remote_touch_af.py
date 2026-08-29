from pathlib import Path

constants = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/PtpConstants.kt')
text = constants.read_text()
old = '    const val PROP_SONY_AF_AREA_POSITION = 0xD2DC\n    const val PROP_SONY_LIVEVIEW_STATE = 0xD221\n'
new = '''    const val PROP_SONY_AF_AREA_POSITION = 0xD2DC
    // Sony Camera Remote SDK "Remote Touch Operation": execute a touch at
    // x/y in the same 640x480 logical coordinate system used by AF area position.
    // Unlike D2DC this is a momentary control action and can perform Spot AF in
    // one control transaction, matching the SDK's touch-monitoring path.
    const val PROP_SONY_REMOTE_TOUCH_OPERATION = 0xD2E4
    const val PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS = 0xD284
    const val PROP_SONY_REMOTE_TOUCH_FUNCTION = 0xE083
    const val PROP_SONY_LIVEVIEW_STATE = 0xD221
'''
if old not in text:
    raise SystemExit('constants marker not found')
constants.write_text(text.replace(old, new, 1))

sony = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt')
text = sony.read_text()
old = '    @Volatile\n    private var sonyExtensionDebug: String = "ext=not-initialized"\n\n    @Volatile\n    private var loggedLiveViewDataset = false\n'
new = '''    @Volatile
    private var sonyExtensionDebug: String = "ext=not-initialized"

    // ILCE-7CM2 is a protocol-3 body supported by Sony Camera Remote SDK's
    // Remote Touch Operation. Keep this capability session-scoped so an
    // explicit Unsupported response can permanently fall back to legacy D2DC.
    @Volatile
    private var remoteTouchSupported = false

    @Volatile
    private var loggedLiveViewDataset = false
'''
if old not in text:
    raise SystemExit('Sony state marker not found')
text = text.replace(old, new, 1)

old = '        if (!extInfo.isSuccess || extInfo.dataSize == 0) return false\n\n        // libgphoto2 / Sony PC-Remote traces complete SDIOConnect phase 3\n'
new = '''        if (!extInfo.isSuccess || extInfo.dataSize == 0) return false

        // Sony's SDK exposes RemoteTouchOperation (wire control D2E4) on the
        // a7C II generation. It is the official one-shot "touch the monitor"
        // action; do not emulate that action as D2DC followed by a shutter S1.
        // The PTP3 capability blob differs between generations, so the known
        // ILCE-7CM2 model is the authority here rather than a brittle byte scan.
        remoteTouchSupported = preferProtocol3
        if (remoteTouchSupported) {
            Log.d(TAG, "Remote Touch Operation enabled for ILCE-7CM2 (D2E4/9207)")
        }

        // libgphoto2 / Sony PC-Remote traces complete SDIOConnect phase 3
'''
if old not in text:
    raise SystemExit('ext-info marker not found')
text = text.replace(old, new, 1)

marker = '''    /**
     * Write Sony AF Area Position (0xD2DC) as a uint32 through
     * SetControlDeviceB (0x9207). Sony protocol uses a 640x480 logical grid;
     * the packed value is (x << 16) | y.
     */
    private fun setAfAreaPosition(x: Int, y: Int): PtpResponse {
'''
insert = '''    /** Whether this session should use Sony's official Remote Touch action. */
    fun supportsRemoteTouch(): Boolean = remoteTouchSupported

    /**
     * Execute Sony Camera Remote SDK's Remote Touch Operation (D2E4).
     *
     * The SDK defines this control as UInt32 with x in the upper 16 bits and y
     * in the lower 16 bits, on a 640x480 logical monitor. Function-of-Remote-
     * Touch is a separate camera property (Tracking AF / Spot AF / AF Area
     * Select); sending the touch itself is a single SetControlDeviceB action.
     */
    fun executeRemoteTouch(x: Int, y: Int): PtpResponse {
        val safeX = x.coerceIn(0, 639)
        val safeY = y.coerceIn(0, 479)
        val packed = (safeX shl 16) or safeY
        val data = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(packed)
            .array()
        val result = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B,
            data,
            PtpConstants.PROP_SONY_REMOTE_TOUCH_OPERATION
        )
        if (result.responseCode == PtpConstants.RESP_OPERATION_NOT_SUPPORTED ||
            result.responseCode == PtpConstants.RESP_PARAMETER_NOT_SUPPORTED
        ) {
            remoteTouchSupported = false
            Log.w(TAG, "Remote Touch explicitly unsupported; falling back to D2DC/S1")
        } else {
            Log.d(TAG, "Remote Touch: x=$safeX y=$safeY packed=0x${packed.toUInt().toString(16)} " +
                    "-> ${PtpConstants.responseCodeName(result.responseCode)}")
        }
        return result
    }

    /**
     * Write Sony AF Area Position (0xD2DC) as a uint32 through
     * SetControlDeviceB (0x9207). Sony protocol uses a 640x480 logical grid;
     * the packed value is (x << 16) | y.
     */
    private fun setAfAreaPosition(x: Int, y: Int): PtpResponse {
'''
if marker not in text:
    raise SystemExit('AF-area marker not found')
text = text.replace(marker, insert, 1)
sony.write_text(text)

manager = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt')
text = manager.read_text()
start = text.index('    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult = withContext(Dispatchers.IO) {')
end = text.index('    override suspend fun testAfCenter(): CameraOperationResult = withContext(Dispatchers.IO) {', start)
replacement = '''    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult = withContext(Dispatchers.IO) {
        controlWriteMutex.withLock {
            val camera = ptpCamera
                ?: return@withLock CameraOperationResult.Failure("Camera not connected")
            val safeX = x.coerceIn(0, 639)
            val safeY = y.coerceIn(0, 479)
            val epoch = beginControlWrite()
            try {
                val started = System.currentTimeMillis()

                // Match Sony Camera Remote SDK / monitor-style touch control:
                // one D2E4 RemoteTouchOperation performs the touch action at x/y.
                // Do NOT decompose a normal a7C II tap into D2DC + shutter S1;
                // that doubles PTP round trips and is observably less responsive.
                if (camera.supportsRemoteTouch()) {
                    val touch = camera.executeRemoteTouch(safeX, safeY)
                    if (camera.supportsRemoteTouch()) {
                        val elapsed = System.currentTimeMillis() - started
                        val message = "REMOTE TOUCH x=$safeX y=$safeY | D2E4/9207=" +
                            PtpConstants.responseCodeName(touch.responseCode) + " | ${elapsed}ms"
                        Log.d(TAG, message)
                        _events.emit(CameraEvent.FocusDebug(message))
                        _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
                        // A late/missing ACK is not grounds for a second AF action:
                        // Sony controls take effect when the data phase lands. Only
                        // an explicit Unsupported response disables this path.
                        return@withLock CameraOperationResult.SuccessWithData(message)
                    }
                    Log.w(TAG, "Remote Touch unsupported by body; using legacy AF fallback")
                }

                // Compatibility fallback for bodies without Remote Touch.
                // Keep the previous move + S1 behavior isolated here; a7C II
                // should never use it unless the camera explicitly rejects D2E4.
                afReleaseJob?.cancel()
                afReleaseJob = null
                afGeneration += 1L
                val generation = afGeneration
                if (afHalfPressHeld) {
                    camera.setAutofocusPressed(false)
                    afHalfPressHeld = false
                }
                val moveMessage = camera.setAfPoint(safeX, safeY)
                val pressResult = camera.setAutofocusPressed(true)
                afHalfPressHeld = true
                val message = "$moveMessage | AF=${PtpConstants.responseCodeName(pressResult.responseCode)}"
                Log.d(TAG, "Legacy AF point+press completed in ${System.currentTimeMillis() - started}ms")
                _events.emit(CameraEvent.FocusDebug(message))
                _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))

                afReleaseJob = scope.launch(Dispatchers.IO) {
                    delay(320)
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
                }
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

'''
manager.write_text(text[:start] + replacement + text[end:])

Path(__file__).unlink()
