from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str):
    text = path.read_text()
    if old not in text:
        raise SystemExit(f"marker not found in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1))


transport = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/PtpTransport.kt"
sony = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
manager = ROOT / "sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt"

# ── PtpTransport: priority-aware scheduling ────────────────────────────────
replace_once(
    transport,
    "import java.util.concurrent.locks.ReentrantLock\n",
    "import java.util.concurrent.atomic.AtomicInteger\nimport java.util.concurrent.locks.ReentrantLock\n",
)
replace_once(
    transport,
    "    private val lock = ReentrantLock(true) // fair: waiting user controls run before the next live-view poll\n\n    private var transactionId = 0\n",
    "    private val lock = ReentrantLock(true) // fair for operations that intentionally wait\n\n"
    "    // Live View is a disposable producer; user controls are not. A high-priority\n"
    "    // control advertises itself before waiting on the PTP lock so a Live View\n"
    "    // poll that has not started yet can yield instead of barging into the queue.\n"
    "    private val highPriorityWaiters = AtomicInteger(0)\n\n"
    "    private var transactionId = 0\n",
)

text = transport.read_text()
start = text.index("    fun sendCommandWithDataShortTimeout(operationCode: Int, timeoutMs: Int, vararg params: Int): PtpDataResponse = lock.withLock {")
end = text.index("    /**\n     * Send a PTP command with a data payload", start)
short_block = '''    fun sendCommandWithDataShortTimeout(operationCode: Int, timeoutMs: Int, vararg params: Int): PtpDataResponse = lock.withLock {
        sendCommandWithDataShortTimeoutLocked(operationCode, timeoutMs, params)
    }

    /**
     * Low-priority variant used by the continuous Live View producer.
     *
     * Crucially this NEVER queues behind another PTP operation. If telemetry or
     * a user control owns the transport, the caller simply drops this frame and
     * tries again later. That prevents a queued GetObject from sitting in front
     * of a monitor tap in the fair ReentrantLock wait queue.
     */
    fun trySendCommandWithDataShortTimeout(
        operationCode: Int,
        timeoutMs: Int,
        vararg params: Int
    ): PtpDataResponse? {
        if (highPriorityWaiters.get() > 0) return null
        if (!lock.tryLock()) return null
        return try {
            // Untimed tryLock may barge on a fair ReentrantLock. Re-check after
            // acquisition so a control that announced itself during the race wins.
            if (highPriorityWaiters.get() > 0) null
            else sendCommandWithDataShortTimeoutLocked(operationCode, timeoutMs, params)
        } finally {
            lock.unlock()
        }
    }

    private fun sendCommandWithDataShortTimeoutLocked(
        operationCode: Int,
        timeoutMs: Int,
        params: IntArray
    ): PtpDataResponse {
        val txId = nextTransactionId()

        val paramBytes = params.size * 4
        val containerLength = PtpConstants.HEADER_SIZE + paramBytes
        val buffer = ByteBuffer.allocate(containerLength).order(ByteOrder.LITTLE_ENDIAN)

        buffer.putInt(containerLength)
        buffer.putShort(PtpConstants.CONTAINER_TYPE_COMMAND.toShort())
        buffer.putShort(operationCode.toShort())
        buffer.putInt(txId)
        for (param in params) {
            buffer.putInt(param)
        }

        val sent = connection.bulkTransfer(bulkOut, buffer.array(), containerLength, timeoutMs)
        if (sent < 0) {
            return PtpDataResponse(PtpConstants.RESP_GENERAL_ERROR, txId, ByteArray(0))
        }

        val headerBuf = ByteArray(PtpConstants.USB_TRANSFER_BUFFER_SIZE)
        val read = connection.bulkTransfer(bulkIn, headerBuf, headerBuf.size, timeoutMs)

        if (read < PtpConstants.HEADER_SIZE) {
            return PtpDataResponse(PtpConstants.RESP_GENERAL_ERROR, txId, ByteArray(0))
        }

        val bb = ByteBuffer.wrap(headerBuf, 0, read).order(ByteOrder.LITTLE_ENDIAN)
        val totalLength = bb.getInt()
        val type = bb.getShort().toInt() and 0xFFFF
        val code = bb.getShort().toInt() and 0xFFFF
        val responseTxId = bb.getInt()

        if (type == PtpConstants.CONTAINER_TYPE_RESPONSE) {
            return PtpDataResponse(code, responseTxId, ByteArray(0))
        }

        if (type != PtpConstants.CONTAINER_TYPE_DATA) {
            return PtpDataResponse(PtpConstants.RESP_GENERAL_ERROR, txId, ByteArray(0))
        }

        val dataSize = totalLength - PtpConstants.HEADER_SIZE
        val output = ByteArrayOutputStream(dataSize.coerceAtMost(PtpConstants.USB_TRANSFER_BUFFER_SIZE))
        val firstChunkSize = read - PtpConstants.HEADER_SIZE
        if (firstChunkSize > 0) {
            output.write(headerBuf, PtpConstants.HEADER_SIZE, firstChunkSize)
        }

        var totalRead = firstChunkSize
        while (totalRead < dataSize) {
            val chunkRead = connection.bulkTransfer(bulkIn, headerBuf, headerBuf.size, timeoutMs)
            if (chunkRead <= 0) break
            output.write(headerBuf, 0, chunkRead)
            totalRead += chunkRead
        }

        val data = output.toByteArray()
        val response = readResponse(txId, timeoutMs)
        return PtpDataResponse(response.responseCode, responseTxId, data)
    }

'''
transport.write_text(text[:start] + short_block + text[end:])

text = transport.read_text()
start = text.index("    fun sendCommandWithDataOut(operationCode: Int, data: ByteArray, vararg params: Int): PtpResponse = lock.withLock {")
end = text.index("    /**\n     * Send a PTP command with a data-out phase, then receive a data-in phase", start)
dataout_block = '''    fun sendCommandWithDataOut(operationCode: Int, data: ByteArray, vararg params: Int): PtpResponse = lock.withLock {
        sendCommandWithDataOutLocked(operationCode, data, params)
    }

    /**
     * Priority path for latency-sensitive monitor controls such as Sony Remote Touch.
     * The waiter is announced BEFORE blocking on the transport lock, which makes
     * low-priority Live View polling yield immediately rather than queue ahead.
     */
    fun sendHighPriorityCommandWithDataOut(
        operationCode: Int,
        data: ByteArray,
        vararg params: Int
    ): PtpResponse {
        val queuedAtMs = System.currentTimeMillis()
        highPriorityWaiters.incrementAndGet()
        lock.lock()
        val queueWaitMs = System.currentTimeMillis() - queuedAtMs
        return try {
            sendCommandWithDataOutLocked(operationCode, data, params).copy(queueWaitMs = queueWaitMs)
        } finally {
            lock.unlock()
            highPriorityWaiters.decrementAndGet()
        }
    }

    private fun sendCommandWithDataOutLocked(
        operationCode: Int,
        data: ByteArray,
        params: IntArray
    ): PtpResponse {
        val txId = nextTransactionId()

        val paramBytes = params.size * 4
        val cmdLength = PtpConstants.HEADER_SIZE + paramBytes
        val cmdBuffer = ByteBuffer.allocate(cmdLength).order(ByteOrder.LITTLE_ENDIAN)
        cmdBuffer.putInt(cmdLength)
        cmdBuffer.putShort(PtpConstants.CONTAINER_TYPE_COMMAND.toShort())
        cmdBuffer.putShort(operationCode.toShort())
        cmdBuffer.putInt(txId)
        for (param in params) {
            cmdBuffer.putInt(param)
        }

        var sent = connection.bulkTransfer(bulkOut, cmdBuffer.array(), cmdLength, PtpConstants.USB_TIMEOUT_MS)
        if (sent < 0) {
            Log.e(TAG, "DataOut cmd 0x${operationCode.toString(16)} send failed (bulkTransfer=$sent)")
            return PtpResponse(PtpConstants.RESP_GENERAL_ERROR, txId)
        }

        val dataLength = PtpConstants.HEADER_SIZE + data.size
        val dataBuffer = ByteBuffer.allocate(dataLength).order(ByteOrder.LITTLE_ENDIAN)
        dataBuffer.putInt(dataLength)
        dataBuffer.putShort(PtpConstants.CONTAINER_TYPE_DATA.toShort())
        dataBuffer.putShort(operationCode.toShort())
        dataBuffer.putInt(txId)
        dataBuffer.put(data)

        sent = connection.bulkTransfer(bulkOut, dataBuffer.array(), dataLength, PtpConstants.USB_TIMEOUT_MS)
        if (sent < 0) {
            Log.e(TAG, "DataOut data phase send failed (bulkTransfer=$sent)")
            return PtpResponse(PtpConstants.RESP_GENERAL_ERROR, txId)
        }

        return readResponseQuick(txId)
    }

'''
transport.write_text(text[:start] + dataout_block + text[end:])

replace_once(
    transport,
    "data class PtpResponse(\n    val responseCode: Int,\n    val transactionId: Int,\n    val params: IntArray = IntArray(0)\n)",
    "data class PtpResponse(\n    val responseCode: Int,\n    val transactionId: Int,\n    val params: IntArray = IntArray(0),\n    /** Time spent waiting for the shared PTP bus on an explicitly priority call. */\n    val queueWaitMs: Long = 0L\n)",
)

# ── SonyPtpCamera: make Live View non-queueing, Remote Touch priority ─────
replace_once(
    sony,
    "        val response = transport.sendCommandWithDataShortTimeout(\n            PtpConstants.OP_GET_OBJECT,\n            450,\n            PtpConstants.LIVEVIEW_OBJECT_HANDLE\n        )\n",
    "        val response = transport.trySendCommandWithDataShortTimeout(\n            PtpConstants.OP_GET_OBJECT,\n            450,\n            PtpConstants.LIVEVIEW_OBJECT_HANDLE\n        ) ?: return null\n",
)
replace_once(
    sony,
    "        val result = transport.sendCommandWithDataOut(\n            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B,\n            data,\n            PtpConstants.PROP_SONY_REMOTE_TOUCH_OPERATION\n        )\n",
    "        val result = transport.sendHighPriorityCommandWithDataOut(\n            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B,\n            data,\n            PtpConstants.PROP_SONY_REMOTE_TOUCH_OPERATION\n        )\n",
)

# ── Manager: advertise tap intent before dispatcher/mutex wait ─────────────
replace_once(
    manager,
    "import kotlinx.coroutines.withContext\n",
    "import kotlinx.coroutines.withContext\nimport java.util.concurrent.atomic.AtomicInteger\n",
)
replace_once(
    manager,
    "        private const val LIVEVIEW_MIN_FRAME_INTERVAL_MS = 30L // ~33 fps max\n",
    "        private const val LIVEVIEW_MIN_FRAME_INTERVAL_MS = 30L // ~33 fps max\n"
    "        // Always leave a tiny idle bus window after a successful frame so a\n"
    "        // monitor tap can claim PTP before the next GetObject starts.\n"
    "        private const val LIVEVIEW_CONTROL_GAP_MS = 12L\n",
)
replace_once(
    manager,
    "    @Volatile private var controlWriteActive = false\n",
    "    @Volatile private var controlWriteActive = false\n"
    "    private val priorityControlIntents = AtomicInteger(0)\n",
)
replace_once(
    manager,
    "                    if (controlWriteActive) {\n",
    "                    if (priorityControlIntents.get() > 0 || controlWriteActive) {\n",
)
replace_once(
    manager,
    "                        if (!controlWriteActive &&\n",
    "                        if (priorityControlIntents.get() == 0 && !controlWriteActive &&\n",
)
replace_once(
    manager,
    "                        val sleepMs = LIVEVIEW_MIN_FRAME_INTERVAL_MS - elapsed\n                        if (sleepMs > 0) delay(sleepMs)\n",
    "                        val sleepMs = maxOf(\n                            LIVEVIEW_MIN_FRAME_INTERVAL_MS - elapsed,\n                            LIVEVIEW_CONTROL_GAP_MS\n                        )\n                        delay(sleepMs)\n",
)

text = manager.read_text()
start = text.index("    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult = withContext(Dispatchers.IO) {")
end = text.index("    override suspend fun testAfCenter(): CameraOperationResult", start)
af_block = '''    override suspend fun setAfPoint(x: Int, y: Int): CameraOperationResult {
        // Publish the intent on the caller's thread BEFORE hopping to Dispatchers.IO
        // or waiting for controlWriteMutex. This closes the race where Live View
        // checked controlWriteActive=false and queued another GetObject first.
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

                        // ILCE-7CM2 monitor taps use one Sony Remote Touch action.
                        // The transport call is explicitly high-priority; Live View
                        // never queues in front of it and drops a frame if PTP is busy.
                        if (camera.supportsRemoteTouch()) {
                            val touch = camera.executeRemoteTouch(safeX, safeY)
                            if (camera.supportsRemoteTouch()) {
                                val finishedMs = System.currentTimeMillis()
                                val commandMs = finishedMs - commandStartedMs
                                val wireAndAckMs = (commandMs - touch.queueWaitMs).coerceAtLeast(0L)
                                val totalMs = finishedMs - requestedAtMs
                                val message = "REMOTE TOUCH x=$safeX y=$safeY | D2E4/9207=" +
                                    PtpConstants.responseCodeName(touch.responseCode) +
                                    " | dispatch=${dispatchWaitMs}ms bus=${touch.queueWaitMs}ms" +
                                    " wire+ack=${wireAndAckMs}ms total=${totalMs}ms"
                                Log.d(TAG, message)
                                _events.emit(CameraEvent.FocusDebug(message))
                                _events.emit(CameraEvent.AfTargetUpdated(safeX, safeY))
                                return@withLock CameraOperationResult.SuccessWithData(message)
                            }
                            Log.w(TAG, "Remote Touch unsupported by body; using legacy AF fallback")
                        }

                        // Compatibility fallback for bodies without Remote Touch.
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
                        Log.d(TAG, "Legacy AF point+press completed in ${System.currentTimeMillis() - commandStartedMs}ms")
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
        } finally {
            priorityControlIntents.decrementAndGet()
        }
    }

'''
manager.write_text(text[:start] + af_block + text[end:])

replace_once(
    manager,
    "        controlWriteActive = false\n\n        // Cancel any in-flight connect",
    "        controlWriteActive = false\n        priorityControlIntents.set(0)\n\n        // Cancel any in-flight connect",
)

# Keep the patch one-shot so the repository remains clean after Actions commits it.
Path(__file__).unlink()
