package io.github.gallo.sonycamera.ptp

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Sony-specific PTP camera operations for the a6600 (and similar Alpha models).
 *
 * Wraps [PtpTransport] with high-level operations:
 * - Session management
 * - Device info
 * - Liveview frame capture via GetObject(0xFFFFC002)
 * - Photo capture and download
 * - Device property control
 *
 * Liveview protocol (reverse-engineered from SonyAlphaUSB / libgphoto2):
 *   After SDIO init, camera enters PC Remote mode with liveview always active.
 *   Poll GetObject(handle=0xFFFFC002) to receive JPEG frames (~140KB each).
 *   Response data contains raw JPEG starting at the SOI marker (0xFFD8).
 */
data class SonyFocusFrame(
    val type: Int,
    val state: Int,
    val priority: Int,
    val xNumerator: Long,
    val yNumerator: Long,
    val xDenominator: Long,
    val yDenominator: Long,
    val width: Long,
    val height: Long
)

data class SonyFocusFrameInfo(
    val version: Int,
    val frames: List<SonyFocusFrame>
)

data class SonyLiveViewFrame(
    val jpeg: ByteArray,
    val focusFrameInfo: SonyFocusFrameInfo?
)

class SonyPtpCamera(private val transport: PtpTransport) {

    companion object {
        private const val TAG = "SonyPtpCamera"
        private const val SESSION_ID = 1

        // How long to wait between issuing the full-press shutter command
        // and signalling the UI to flash. The camera takes a moment after
        // the command to actually expose; this delay aligns the visual
        // flash with the real capture instead of leading it.
        private const val SHUTTER_TO_FLASH_DELAY_MS = 150L
    }

    var deviceName: String? = null
        private set
    var serialNumber: String? = null
        private set

    @Volatile
    private var sonyExtensionDebug: String = "ext=not-initialized"

    @Volatile
    private var loggedLiveViewDataset = false

    // Reusable BitmapFactory options for liveview decode
    private val decodeOptions = BitmapFactory.Options().apply {
        inPreferredConfig = Bitmap.Config.RGB_565 // Half memory vs ARGB_8888
        inMutable = true
    }

    // Track consecutive liveview errors for adaptive backoff
    @Volatile
    private var consecutiveLiveviewErrors = 0

    /**
     * Open a PTP session. Must be called before any other operations.
     *
     * Keep this attempt deliberately short. The connection manager owns the
     * one permitted USB Device-Reset recovery, so this method must not hide a
     * 7+ second retry loop behind a Boolean result.
     */
    fun openSession(): Boolean {
        transport.resetTransactionId()
        var response = transport.sendCommand(
            PtpConstants.OP_OPEN_SESSION,
            responseTimeoutMs = 1500,
            params = intArrayOf(SESSION_ID)
        )
        if (response.isSuccess) {
            Log.d(TAG, "PTP session opened")
            return true
        }

        // SessionAlreadyOpen: try the standard CloseSession once, then reopen.
        // If that does not work, the manager will perform one class Device Reset.
        if (response.responseCode == 0x201E) {
            Log.w(TAG, "PTP session already open — closing stale session once")
            closeSession()
            Thread.sleep(120)
            transport.resetTransactionId()
            response = transport.sendCommand(
                PtpConstants.OP_OPEN_SESSION,
                responseTimeoutMs = 1500,
                params = intArrayOf(SESSION_ID)
            )
            if (response.isSuccess) {
                Log.d(TAG, "PTP session opened after stale-session close")
                return true
            }
        }

        Log.e(TAG, "OpenSession failed: $response")
        return false
    }

    /**
     * Close the PTP session (standard PTP CloseSession, no parameters).
     * Keep teardown bounded so a previous disconnect cannot overlap a new
     * connection for many seconds.
     */
    fun closeSession() {
        try {
            val response = transport.sendCommand(
                operationCode = PtpConstants.OP_CLOSE_SESSION,
                responseTimeoutMs = 1200
            )
            Log.d(TAG, "PTP CloseSession: ${PtpConstants.responseCodeName(response.responseCode)}")
        } catch (e: Exception) {
            Log.w(TAG, "Error closing session: ${e.message}")
        }
    }

    /**
     * Gracefully end the PC-Remote session.
     *
     * SDIOConnect(3) is part of Sony's connection initialization sequence on
     * newer protocol-3 bodies; it is not a hang-up command. Teardown only
     * releases host priority and closes the standard PTP session.
     */
    fun endSession() {
        try {
            setControlDeviceA(PtpConstants.PROP_SONY_PRIORITY_MODE, 0)
            Log.d(TAG, "Released Sony priority — camera regains control")
        } catch (e: Exception) {
            Log.w(TAG, "Error releasing Sony priority: ${e.message}")
        }
        closeSession()
    }

    /**
     * Initialize Sony SDIO / PC-Remote connection.
     *
     * Newer protocol-3 Sony bodies require the full 1 -> 2 -> vendor-info -> 3
     * sequence before host priority is asserted. Older a6x00-style bodies keep
     * the proven phase-1/2 path because phase 3 is known to stall on some of
     * them. Every mandatory stage is checked; callers must not publish Ready
     * after a half-completed vendor handshake.
     */
    fun initSonyExtension(): Boolean {
        Log.d(TAG, "Initializing Sony SDIO extension...")

        val r1 = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_SDIO_CONNECT, 2000, 1, 0, 0
        )
        Log.d(TAG, "SDIOConnect(1): ${PtpConstants.responseCodeName(r1.responseCode)}")
        if (!r1.isSuccess) return false

        val r2 = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_SDIO_CONNECT, 2000, 2, 0, 0
        )
        Log.d(TAG, "SDIOConnect(2): ${PtpConstants.responseCodeName(r2.responseCode)}")
        if (!r2.isSuccess) return false

        val preferProtocol3 = deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true
        val extV3 = if (preferProtocol3) {
            transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO,
                2500,
                0x012C,
                1
            )
        } else null

        val useProtocol3 = extV3?.isSuccess == true && extV3.dataSize > 0
        val extInfo = if (useProtocol3) {
            extV3!!
        } else {
            transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO,
                2500,
                0x00C8
            )
        }
        val selectedProtocol = if (useProtocol3) 300 else 200
        Log.d(TAG, "GetExtDeviceInfo protocol=$selectedProtocol: " +
                "${PtpConstants.responseCodeName(extInfo.responseCode)}, ${extInfo.dataSize}B")
        if (!extInfo.isSuccess || extInfo.dataSize == 0) return false

        // libgphoto2 / Sony PC-Remote traces complete SDIOConnect phase 3
        // before asserting PriorityMode. Restrict it to the newer body path so
        // we do not regress older cameras where phase 3 can stall.
        val r3 = if (preferProtocol3) {
            transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_SDIO_CONNECT, 2000, 3, 0, 0
            )
        } else null
        if (r3 != null) {
            Log.d(TAG, "SDIOConnect(3): ${PtpConstants.responseCodeName(r3.responseCode)}")
            if (!r3.isSuccess) return false
            Thread.sleep(150)
        }

        // Property snapshot is useful for diagnostics but is not required to
        // decide whether the transport-level Sony handshake succeeded.
        val props = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA, 2500
        )
        Log.d(TAG, "GetAllDevicePropData: ${PtpConstants.responseCodeName(props.responseCode)}, ${props.dataSize}B")

        sonyExtensionDebug = buildString {
            append("ext=").append(selectedProtocol)
            if (preferProtocol3) {
                append(" v3=")
                append(PtpConstants.responseCodeName(extV3?.responseCode ?: 0))
                append("/").append(extV3?.dataSize ?: 0).append("B")
                append(" sdio3=")
                append(PtpConstants.responseCodeName(r3?.responseCode ?: 0))
            }
            append(" extInfo=")
            append(PtpConstants.responseCodeName(extInfo.responseCode))
            append("/").append(extInfo.dataSize).append("B")
            append(" init9209=")
            append(PtpConstants.responseCodeName(props.responseCode))
            append("/").append(props.dataSize).append("B")
        }

        // Tell camera that USB host has control. Some Sony commands acknowledge
        // slowly/silently, so keep this best-effort after the mandatory SDIO
        // stages rather than treating a quick-response timeout as disconnect.
        setControlDeviceA(PtpConstants.PROP_SONY_PRIORITY_MODE, 1)
        return true
    }

    /**
     * Get device info. Populates [deviceName] and [serialNumber].
     */
    fun getDeviceInfo(): Boolean {
        val response = transport.sendCommandWithData(PtpConstants.OP_GET_DEVICE_INFO)
        if (!response.isSuccess || response.data.isEmpty()) {
            Log.e(TAG, "Failed to get device info: ${PtpConstants.responseCodeName(response.responseCode)}")
            return false
        }
        try {
            parseDeviceInfo(response.data)
            Log.d(TAG, "Device: $deviceName, Serial: $serialNumber")
            return true
        } catch (e: Exception) {
            Log.e(TAG, "Error parsing device info", e)
            return false
        }
    }

    /**
     * Get one Sony live-view dataset.
     *
     * Camera Control PTP 3 defines object 0xFFFFC002 as a dataset whose first
     * four UINT32 values are: image offset, image size, FocalFrameInfo offset,
     * and FocalFrameInfo size. Keeping the two payloads together lets callers
     * render the exact focus-frame geometry returned for this live-view frame.
     * Older protocol-2 bodies fall back to the historical JPEG-SOI scan.
     */
    fun getLiveViewFrameData(): SonyLiveViewFrame? {
        val response = transport.sendCommandWithData(
            PtpConstants.OP_GET_OBJECT,
            PtpConstants.LIVEVIEW_OBJECT_HANDLE
        )

        if (!response.isSuccess) {
            consecutiveLiveviewErrors++
            if (consecutiveLiveviewErrors == 5 || consecutiveLiveviewErrors % 200 == 0) {
                Log.w(TAG, "Liveview: ${PtpConstants.responseCodeName(response.responseCode)} " +
                        "(consecutive=$consecutiveLiveviewErrors)")
            }
            return null
        }

        if (response.data.isEmpty()) return null

        if (consecutiveLiveviewErrors > 5) {
            Log.d(TAG, "Liveview recovered after $consecutiveLiveviewErrors errors")
        }
        consecutiveLiveviewErrors = 0

        return parseLiveViewDataset(response.data)
    }

    /** Compatibility helper for callers that only need the JPEG. */
    fun getLiveViewFrame(): ByteArray? = getLiveViewFrameData()?.jpeg

    /**
     * Get a liveview frame decoded as a Bitmap. Uses RGB_565 for efficiency.
     */
    fun getLiveViewBitmap(): Bitmap? {
        val jpeg = getLiveViewFrame() ?: return null
        return try {
            BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, decodeOptions)
        } catch (e: Exception) {
            null
        }
    }

    /**
     * Pre-warm the camera's shutter-handling pipeline.
     *
     * On a fresh PC-Remote session, the FIRST `SetControlDeviceB(SHUTTER_*)`
     * command takes 8–10 seconds for the a6600 to acknowledge: the camera has
     * to context-switch its firmware out of "stream liveview" into "process
     * shutter", and the OUT endpoint NAKs until that's done. After that one
     * tax is paid, subsequent shutter commands process in ~500ms.
     *
     * Call this once at the end of session init (before auto-starting
     * liveview) so the first *real* capture the user triggers doesn't pay
     * the warm-up cost — the user already expects a brief delay at connect.
     *
     * The cycle is half-press → release. No AF lock is held, no exposure
     * happens, no photo is created. The lens motor may twitch briefly.
     */
    fun prewarmShutter() {
        Log.d(TAG, "Pre-warming shutter pipeline (first command will be slow)…")
        val started = System.currentTimeMillis()
        try {
            // Half-press: this is the slow one — eats the firmware context
            // switch. We don't care about the response (typically a stall →
            // General Error); we just need the camera to do the transition.
            setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 2)
            Thread.sleep(100)
            // Release: clean up so the camera isn't sitting on a half-press.
            setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 1)
        } catch (e: Exception) {
            // Pre-warm is best-effort; never fail the connection over it.
            Log.w(TAG, "Pre-warm errored (non-fatal): ${e.message}")
        }
        val elapsed = System.currentTimeMillis() - started
        Log.d(TAG, "Pre-warm complete in ${elapsed}ms — first capture should be fast")
    }

    /**
     * Initiate photo capture (shutter release).
     *
     * Sony SetControlDeviceB (0x920A) requires a data-out phase:
     *   Command param: property code (e.g., 0xD2C1 shutter half-press)
     *   Data payload:  value (2=press, 1=release)
     *
     * The a6600 stalls the IN endpoint after SetControlDeviceB — it accepts the
     * command but doesn't send a PTP response. We treat a stalled response as
     * success (the command+data were sent OK) and clear the pipe to continue.
     */
    fun initiateCapture(onShutterFired: () -> Unit = {}): Boolean {
        // Half-press shutter for AF
        Log.d(TAG, "Capture: half-press shutter (AF)")
        val afResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 2)
        Thread.sleep(500)

        // Full shutter press. Send the command first, then wait a beat
        // before signalling the UI flash so the visual flash lands closer
        // to the camera's actual exposure moment rather than leading it.
        // Tune SHUTTER_TO_FLASH_DELAY_MS if the flash feels early/late.
        Log.d(TAG, "Capture: full-press shutter")
        val captureResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_FULL_PRESS, 2)
        Thread.sleep(SHUTTER_TO_FLASH_DELAY_MS)
        onShutterFired()

        // Release shutter
        Thread.sleep(200)
        setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_FULL_PRESS, 1)
        setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 1)

        // With correct opcode (0x9207), camera should respond OK.
        // If it still stalls, the command was still sent — check for ObjectAdded event.
        val success = captureResult.isSuccess || afResult.isSuccess
        Log.d(TAG, "Capture commands sent (af=${PtpConstants.responseCodeName(afResult.responseCode)}, " +
                "shutter=${PtpConstants.responseCodeName(captureResult.responseCode)})")
        return success
    }

    /**
     * Write Sony AF Area Position (0xD2DC) as a uint32 through
     * SetControlDeviceB (0x9207). Sony protocol uses a 640x480 logical grid;
     * the packed value is (x << 16) | y.
     */
    private fun setAfAreaPosition(x: Int, y: Int): PtpResponse {
        val safeX = x.coerceIn(0, 639)
        val safeY = y.coerceIn(0, 479)
        val packed = (safeX shl 16) or safeY
        val data = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(packed)
            .array()
        val result = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B,
            data,
            PtpConstants.PROP_SONY_AF_AREA_POSITION
        )
        Log.d(TAG, "Set AF Area Position: x=$safeX y=$safeY packed=0x${packed.toUInt().toString(16)} " +
                "-> ${PtpConstants.responseCodeName(result.responseCode)}")
        return result
    }

    /**
     * Move the Sony logical AF target and immediately trigger a short AF
     * half-press. A7C II uses a 640x480 logical grid for D2DC.
     */
    fun setAfPoint(x: Int, y: Int): String = commandAfPoint("AF TARGET", x, y)

    /** Diagnostic convenience entry point retained by the demo. */
    fun testAfCenter(): String = commandAfPoint("AF CENTER TEST", 320, 240)

    private fun commandAfPoint(label: String, x: Int, y: Int): String {
        val safeX = x.coerceIn(0, 639)
        val safeY = y.coerceIn(0, 479)
        val setResult = setAfAreaPosition(safeX, safeY)
        Thread.sleep(120)
        val pressResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 2)
        Thread.sleep(450)
        val releaseResult = setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, 1)
        return buildString {
            append(label).append(" x=").append(safeX).append(" y=").append(safeY)
            append(" | D2DC/9207=")
            append(PtpConstants.responseCodeName(setResult.responseCode))
            append(" | halfPress=")
            append(PtpConstants.responseCodeName(pressResult.responseCode))
            append(" | release=")
            append(PtpConstants.responseCodeName(releaseResult.responseCode))
        }
    }

    /**
     * Send a Sony SetControlDeviceB (0x9207) command with data-out phase.
     * Property code goes as command param, value as uint16 data payload.
     */
    private fun setControlDeviceB(propCode: Int, value: Int): PtpResponse {
        val data = ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(value.toShort())
            .array()
        val result = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B, data, propCode
        )
        if (!result.isSuccess) {
            Log.w(TAG, "SetControlDeviceB(0x${propCode.toString(16)}, $value): " +
                    PtpConstants.responseCodeName(result.responseCode))
        }
        return result
    }

    /**
     * Send a Sony SetControlDeviceA (0x9205) command with uint8 data payload.
     * Used for configuration values (PriorityMode, etc.).
     */
    private fun setControlDeviceA(propCode: Int, value: Byte): PtpResponse {
        val data = byteArrayOf(value)
        val result = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_A, data, propCode
        )
        if (!result.isSuccess) {
            Log.w(TAG, "SetControlDeviceA(0x${propCode.toString(16)}, $value [u8]): " +
                    PtpConstants.responseCodeName(result.responseCode))
        }
        return result
    }

    /**
     * Send a Sony SetControlDeviceA (0x9205) command with uint16 data payload.
     * Used for properties like StillImageStoreDestination (0xD222).
     */
    private fun setControlDeviceA16(propCode: Int, value: Int): PtpResponse {
        val data = ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(value.toShort())
            .array()
        val result = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_A, data, propCode
        )
        if (!result.isSuccess) {
            Log.w(TAG, "SetControlDeviceA(0x${propCode.toString(16)}, 0x${value.toString(16)} [u16]): " +
                    PtpConstants.responseCodeName(result.responseCode))
        }
        return result
    }

    /**
     * Download an object (captured photo) from the camera by handle.
     */
    fun getObject(objectHandle: Int): ByteArray? {
        val response = transport.sendCommandWithData(PtpConstants.OP_GET_OBJECT, objectHandle)
        Log.d(TAG, "GetObject(0x${objectHandle.toString(16)}): " +
                "${PtpConstants.responseCodeName(response.responseCode)}, ${response.dataSize}B")
        if (!response.isSuccess) return null
        return response.data
    }

    /**
     * Try to download an object with a short timeout. Returns null quickly if no data.
     * Use for probing SDRAM handles without blocking for 10+ seconds.
     */
    fun getObjectQuick(objectHandle: Int, timeoutMs: Int = 2000): ByteArray? {
        val response = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_GET_OBJECT, timeoutMs, objectHandle
        )
        Log.d(TAG, "GetObjectQuick(0x${objectHandle.toString(16)}, ${timeoutMs}ms): " +
                "${PtpConstants.responseCodeName(response.responseCode)}, ${response.dataSize}B")
        if (!response.isSuccess) return null
        if (response.data.isEmpty()) return null
        return response.data
    }

    /**
     * Wait for captured image by detecting a new object on the SD card,
     * then downloading it via standard PTP GetObject.
     *
     * Strategy:
     * 1. Get object handle count before capture
     * 2. Poll GetObjectHandles until a new handle appears
     * 3. Download the new object
     */
    /** Snapshot object handles before capture so we can detect new objects after. */
    fun snapshotObjectHandles(): Pair<Int, Set<Int>>? {
        val storageId = getFirstStorageId() ?: return null
        val handles = getObjectHandles(storageId) ?: return null
        Log.d(TAG, "Snapshot: storage=0x${storageId.toString(16)}, ${handles.size} objects")
        return Pair(storageId, handles.toSet())
    }

    fun waitAndDownloadCapturedImage(
        snapshot: Pair<Int, Set<Int>>?,
        maxWaitMs: Long = 12_000
    ): ByteArray? {
        val startTime = System.currentTimeMillis()
        val storageId = snapshot?.first ?: getFirstStorageId()
        if (storageId == null) {
            Log.e(TAG, "No storage found on camera")
            return null
        }

        val beforeHandles = snapshot?.second ?: emptySet()
        Log.d(TAG, "Waiting for new object (baseline: ${beforeHandles.size} handles)")

        var attempt = 0
        while (System.currentTimeMillis() - startTime < maxWaitMs) {
            attempt++
            Thread.sleep(1000)

            // Poll properties to keep Sony state machine alive
            transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)

            // Check for new object handles
            val currentHandles = getObjectHandles(storageId)
            if (currentHandles != null && beforeHandles != null &&
                currentHandles.size > beforeHandles.size) {
                // New object appeared — find the new handle
                val newHandle = currentHandles.lastOrNull { it !in beforeHandles }
                    ?: currentHandles.last()
                Log.d(TAG, "New object detected: handle=0x${newHandle.toString(16)} " +
                        "(attempt $attempt, ${currentHandles.size - beforeHandles.size} new)")

                // Download it
                val imageData = getObject(newHandle)
                if (imageData != null && imageData.size > 1024) {
                    Log.d(TAG, "Downloaded captured image: ${imageData.size / 1024}KB")
                    return imageData
                }
                Log.w(TAG, "New object download returned ${imageData?.size ?: 0}B")
            }
            Log.d(TAG, "Poll $attempt: ${currentHandles?.size ?: 0} handles")
        }
        Log.w(TAG, "Timeout waiting for new object after $attempt attempts")
        return null
    }

    /**
     * Get the first storage ID from the camera.
     */
    private fun getFirstStorageId(): Int? {
        val response = transport.sendCommandWithData(PtpConstants.OP_GET_STORAGE_IDS)
        if (!response.isSuccess || response.data.size < 8) return null
        val bb = ByteBuffer.wrap(response.data).order(ByteOrder.LITTLE_ENDIAN)
        val count = bb.getInt()
        if (count <= 0) return null
        return bb.getInt()
    }

    /**
     * Get all object handles on a storage.
     */
    private fun getObjectHandles(storageId: Int): List<Int>? {
        val response = transport.sendCommandWithData(
            PtpConstants.OP_GET_OBJECT_HANDLES, storageId, 0, 0
        )
        if (!response.isSuccess || response.data.size < 4) return null
        val bb = ByteBuffer.wrap(response.data).order(ByteOrder.LITTLE_ENDIAN)
        val count = bb.getInt()
        if (count <= 0) return emptyList()
        return List(count.coerceAtMost(response.data.size / 4 - 1)) { bb.getInt() }
    }

    /**
     * Read Sony Focus Area (0xD22C) from GetAllDevicePropData (0x9209).
     *
     * ILCE-6600 exposes the AF-area mode on the PTP2/SDIO-v2 path used by
     * this library. PTP2 does not expose an arbitrary live AF-frame XY
     * coordinate, so callers must not invent one for Zone/Flexible Spot.
     */
    data class FocusAreaProbe(
        val focusAreaCode: Int?,
        val afAreaPositionRaw: Int?,
        val debug: String
    )

    private data class PropertyBlobHit(
        val offset: Int,
        val dataType: Int,
        val standardValue: Int?,
        val sonyFlaggedValue: Int?,
        val bytes: String
    )

    /**
     * Probe focus-area information with diagnostics for both older and newer
     * Sony bodies. ILCE-7CM2 (A7C II) is newer than the original A6600 target,
     * so we explicitly inspect both Focus Area (0xD22C) and AF Area Position
     * (0xD2DC). We report raw position bits only; we do not label low/high
     * halves as X/Y until the real camera confirms the encoding.
     */
    fun probeFocusArea(): FocusAreaProbe {
        val knownAreaValues = setOf(
            0x0001, 0x0002, 0x0003,
            0x0101, 0x0102, 0x0103, 0x0104,
            0x0201, 0x0202, 0x0203, 0x0204, 0x0205, 0x0206, 0x0207,
            0x0105, 0x0106, 0x0107, 0x0108,
            0x1101, 0x1102, 0x1103,
            0x1201, 0x1202, 0x1203
        )

        fun parseDirectValue(data: ByteArray): Int? = when {
            data.size >= 4 -> ByteBuffer.wrap(data, 0, 4)
                .order(ByteOrder.LITTLE_ENDIAN).int
            data.size >= 2 -> ByteBuffer.wrap(data, 0, 2)
                .order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
            data.size == 1 -> data[0].toInt() and 0xFF
            else -> null
        }

        fun valueSize(dataType: Int): Int = when (dataType) {
            1, 2 -> 1
            3, 4 -> 2
            5, 6 -> 4
            7, 8 -> 8
            else -> 0
        }

        fun readValue(data: ByteArray, offset: Int, size: Int): Int? {
            if (offset < 0 || size !in 1..4 || offset + size > data.size) return null
            return when (size) {
                1 -> data[offset].toInt() and 0xFF
                2 -> ByteBuffer.wrap(data, offset, 2)
                    .order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
                4 -> ByteBuffer.wrap(data, offset, 4)
                    .order(ByteOrder.LITTLE_ENDIAN).int
                else -> null
            }
        }

        fun findBlobHit(data: ByteArray, propertyCode: Int): PropertyBlobHit? {
            for (offset in 0 until data.size - 4) {
                val code = (data[offset].toInt() and 0xFF) or
                    ((data[offset + 1].toInt() and 0xFF) shl 8)
                if (code != propertyCode) continue

                val type = (data[offset + 2].toInt() and 0xFF) or
                    ((data[offset + 3].toInt() and 0xFF) shl 8)
                val size = valueSize(type)
                if (size == 0) continue

                // Two layouts observed across Sony generations:
                // standard PTP: code/type/getSet/default/current
                // Sony variant: code/type/getSet/default/flag/current
                val standard = readValue(data, offset + 5 + size, size)
                val sonyFlagged = readValue(data, offset + 6 + size, size)

                val from = (offset - 2).coerceAtLeast(0)
                val to = (offset + 20).coerceAtMost(data.size)
                val bytes = data.copyOfRange(from, to)
                    .joinToString(" ") { "%02X".format(it.toInt() and 0xFF) }
                return PropertyBlobHit(offset, type, standard, sonyFlagged, bytes)
            }
            return null
        }

        fun fmt16(value: Int?): String = value?.let { "0x%04X".format(it and 0xFFFF) } ?: "n/a"
        fun fmt32(value: Int?): String = value?.let { "0x%08X".format(it) } ?: "n/a"
        fun split32(value: Int?): String {
            if (value == null) return "lo=n/a hi=n/a"
            val lo = value and 0xFFFF
            val hi = (value ushr 16) and 0xFFFF
            return "lo=0x%04X(%d) hi=0x%04X(%d)".format(lo, lo, hi, hi)
        }

        // Try direct Sony property-value reads first.
        val areaDirect = transport.sendCommandWithData(
            PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
            PtpConstants.PROP_SONY_FOCUS_AREA
        )
        val areaDirectValue = parseDirectValue(areaDirect.data)

        val posDirect = transport.sendCommandWithData(
            PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
            PtpConstants.PROP_SONY_AF_AREA_POSITION
        )
        val posDirectValue = parseDirectValue(posDirect.data)

        // Also try Sony GetControlDeviceDesc for D2DC. On some generations
        // control properties are described through 0x9206 instead of 0x9203/0x9204.
        val posControlDesc = transport.sendCommandWithData(
            PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC,
            PtpConstants.PROP_SONY_AF_AREA_POSITION
        )

        // One aggregate read lets us inspect both properties without adding
        // another full 0x9209 transaction.
        val all = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)
        val areaHit = if (all.isSuccess) findBlobHit(all.data, PtpConstants.PROP_SONY_FOCUS_AREA) else null
        val posHit = if (all.isSuccess) findBlobHit(all.data, PtpConstants.PROP_SONY_AF_AREA_POSITION) else null

        val areaDirect16 = areaDirectValue?.and(0xFFFF)
        val areaStandard16 = areaHit?.standardValue?.and(0xFFFF)
        val areaSony16 = areaHit?.sonyFlaggedValue?.and(0xFFFF)

        val areaValue = when {
            areaDirect.isSuccess && areaDirect16 != null && areaDirect16 in knownAreaValues -> areaDirect16
            areaStandard16 != null && areaStandard16 in knownAreaValues -> areaStandard16
            areaSony16 != null && areaSony16 in knownAreaValues -> areaSony16
            else -> null
        }

        val positionValue = when {
            posDirect.isSuccess && posDirect.data.size >= 4 -> posDirectValue
            posHit?.standardValue != null -> posHit.standardValue
            posHit?.sonyFlaggedValue != null -> posHit.sonyFlaggedValue
            else -> null
        }

        val debug = buildString {
            append("model=").append(deviceName ?: "?")
            append(" | ").append(sonyExtensionDebug)
            append(" | D22C/9204=")
            append(PtpConstants.responseCodeName(areaDirect.responseCode))
            append(" ").append(areaDirect.dataSize).append("B ").append(fmt16(areaDirectValue))

            append(" | D2DC/9204=")
            append(PtpConstants.responseCodeName(posDirect.responseCode))
            append(" ").append(posDirect.dataSize).append("B ").append(fmt32(posDirectValue))
            append(" ").append(split32(posDirectValue))

            append(" | D2DC/9206=")
            append(PtpConstants.responseCodeName(posControlDesc.responseCode))
            append(" ").append(posControlDesc.dataSize).append("B")
            if (posControlDesc.data.isNotEmpty()) {
                append(" bytes=")
                append(posControlDesc.data.take(20).joinToString(" ") { "%02X".format(it.toInt() and 0xFF) })
            }

            append(" | 9209=")
            append(PtpConstants.responseCodeName(all.responseCode)).append(" ").append(all.dataSize).append("B")
            if (areaHit == null) append(" D22C:not-found")
            else append(" D22C@").append(areaHit.offset)
                .append(" type=0x%04X".format(areaHit.dataType))
                .append(" std=").append(fmt16(areaHit.standardValue))
                .append(" sony=").append(fmt16(areaHit.sonyFlaggedValue))
            if (posHit == null) append(" D2DC:not-found")
            else append(" D2DC@").append(posHit.offset)
                .append(" type=0x%04X".format(posHit.dataType))
                .append(" std=").append(fmt32(posHit.standardValue))
                .append(" sony=").append(fmt32(posHit.sonyFlaggedValue))
                .append(" ").append(split32(positionValue))
        }

        val result = FocusAreaProbe(areaValue, positionValue, debug)
        Log.d(TAG, "AF probe: ${result.debug}")
        return result
    }

    /** Backwards-compatible convenience accessor. */
    fun getFocusAreaCode(): Int? = probeFocusArea().focusAreaCode

    // ── Sony Photo Transfer Queue ──

    data class PhotoQueueStatus(
        val rawValue: Int,
        val queuedCount: Int,
        val photoAvailable: Boolean
    )

    /**
     * Poll all device properties (0x9209) and extract PhotoTransferQueue (0xD215).
     *
     * Instead of sequentially parsing every property (which is fragile due to
     * Sony's proprietary entry format), we scan the raw bytes for the property
     * code 0xD215 and extract the value using standard PTP DevicePropDesc layout:
     *   code(2) + dataType(2) + getSet(1) + default(N) + current(N)
     */
    fun getPhotoTransferQueueStatus(): PhotoQueueStatus? {
        val response = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)
        if (!response.isSuccess || response.data.size < 20) {
            Log.w(TAG, "GetAllDevicePropData failed: ${PtpConstants.responseCodeName(response.responseCode)}")
            return null
        }

        val data = response.data

        // Scan raw bytes for property code 0xD215 (LE: 15 D2)
        for (i in 8 until data.size - 10) { // Skip 8-byte header
            val code = (data[i].toInt() and 0xFF) or ((data[i + 1].toInt() and 0xFF) shl 8)
            if (code != PtpConstants.PROP_SONY_PHOTO_TRANSFER_QUEUE) continue

            // Validate: data type at i+2 should be a known PTP type (1-8)
            val dataType = (data[i + 2].toInt() and 0xFF) or ((data[i + 3].toInt() and 0xFF) shl 8)
            if (dataType !in 1..8) continue

            // Sony DevicePropDesc: code(2) + type(2) + getSet(1) + default(N) + flag(1) + current(N)
            // The extra flag byte between default and current is Sony-specific.
            val valueSize = when (dataType) {
                1, 2 -> 1  // UINT8/INT8
                3, 4 -> 2  // UINT16/INT16
                5, 6 -> 4  // UINT32/INT32
                else -> 2
            }
            val currentOffset = i + 6 + valueSize // code(2)+type(2)+getSet(1)+default(N)+flag(1)
            if (currentOffset + valueSize > data.size) continue

            val value = when (valueSize) {
                1 -> data[currentOffset].toInt() and 0xFF
                2 -> ByteBuffer.wrap(data, currentOffset, 2)
                    .order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
                4 -> ByteBuffer.wrap(data, currentOffset, 4)
                    .order(ByteOrder.LITTLE_ENDIAN).int
                else -> 0
            }

            val count = value and 0xFF
            val available = ((value shr 8) and 0xFF) == 0x80
            return PhotoQueueStatus(value, count, available)
        }

        Log.w(TAG, "Property 0xD215 not found in ${data.size}B response")
        return null
    }

    // ── Sony Image Retrieval ──

    private fun buildSonyImagePayload(imageType: Short): ByteArray {
        val payload = ByteBuffer.allocate(36).order(ByteOrder.LITTLE_ENDIAN)
        payload.putLong(0)             // 8 zero header bytes
        payload.putShort(imageType)    // 0xC001 photo or 0xC002 liveview
        payload.putShort(0xFFFF.toShort())
        payload.putInt(0); payload.putInt(0); payload.putInt(0); payload.putInt(0)
        payload.putShort(0x0001.toShort()); payload.putShort(0x0000.toShort())
        payload.putInt(0x00000003)
        return payload.array()
    }

    data class SonyImageInfo(val numImages: Int, val imageSizeBytes: Int, val imageName: String?)

    fun getSonyImageInfo(): SonyImageInfo? {
        val payload = buildSonyImagePayload(PtpConstants.IMAGE_TYPE_PHOTO)
        val response = transport.sendCommandWithDataOutAndDataIn(
            PtpConstants.OP_GET_OBJECT_INFO, payload
        )
        if (!response.isSuccess || response.data.size < 42) {
            Log.w(TAG, "GetSonyImageInfo failed: ${PtpConstants.responseCodeName(response.responseCode)}, ${response.dataSize}B")
            return null
        }
        try {
            val bb = ByteBuffer.wrap(response.data).order(ByteOrder.LITTLE_ENDIAN)
            bb.position(32)
            val numImages = bb.getShort().toInt() and 0xFFFF
            bb.getInt() // unknown
            val imageSizeBytes = bb.getInt()
            var imageName: String? = null
            if (response.data.size > 83) {
                bb.position(82)
                val nameLen = bb.get().toInt() and 0xFF
                if (nameLen > 0 && bb.remaining() >= nameLen * 2) {
                    imageName = String(CharArray(nameLen) { bb.getShort().toInt().toChar() }).trimEnd('\u0000')
                }
            }
            Log.d(TAG, "SonyImageInfo: numImages=$numImages, size=${imageSizeBytes / 1024}KB, name=$imageName")
            return SonyImageInfo(numImages, imageSizeBytes, imageName)
        } catch (e: Exception) {
            Log.e(TAG, "Error parsing Sony image info", e)
            return null
        }
    }

    fun getSonyImageData(expectedSize: Int = 0): ByteArray? {
        val payload = buildSonyImagePayload(PtpConstants.IMAGE_TYPE_PHOTO)
        val timeout = if (expectedSize > 5_000_000) 30_000 else 15_000
        val response = transport.sendCommandWithDataOutAndDataIn(
            PtpConstants.OP_GET_OBJECT, payload, timeout
        )
        if (!response.isSuccess || response.data.size < 1024) {
            Log.w(TAG, "GetSonyImageData failed: ${PtpConstants.responseCodeName(response.responseCode)}, ${response.dataSize}B")
            return null
        }
        val jpeg = extractJpeg(response.data)
        if (jpeg != null) {
            Log.d(TAG, "Downloaded photo: ${jpeg.size / 1024}KB (expected ${expectedSize / 1024}KB)")
            return jpeg
        }
        Log.d(TAG, "Downloaded photo (raw, no SOI): ${response.data.size / 1024}KB")
        return response.data
    }

    /**
     * Wait for a photo in the transfer queue, then download it.
     * Call after initiateCapture(). Returns full-res JPEG or null.
     */
    fun downloadQueuedPhoto(maxWaitMs: Long = 10_000): ByteArray? {
        val startTime = System.currentTimeMillis()
        var attempt = 0

        // Step 1: Poll queue until photo is ready
        while (System.currentTimeMillis() - startTime < maxWaitMs) {
            attempt++
            Thread.sleep(500)
            val status = getPhotoTransferQueueStatus()
            if (status != null && status.photoAvailable) {
                Log.d(TAG, "Photo queued after $attempt polls (count=${status.queuedCount})")
                break
            }
            if (attempt > (maxWaitMs / 500)) {
                Log.w(TAG, "Timeout waiting for photo in queue " +
                        "(last status: count=${status?.queuedCount ?: "?"}, available=${status?.photoAvailable})")
                return null
            }
        }

        // Step 2: Try multiple download approaches

        // Approach A: GetObjectInfo first (may prepare the transfer), then GetObject
        val infoResp = transport.sendCommandWithData(
            PtpConstants.OP_GET_OBJECT_INFO,
            PtpConstants.PHOTO_OBJECT_HANDLE
        )
        if (infoResp.isSuccess) {
            val objResp = transport.sendCommandWithData(
                PtpConstants.OP_GET_OBJECT,
                PtpConstants.PHOTO_OBJECT_HANDLE
            )
            if (objResp.isSuccess && objResp.data.size > 1024) {
                val jpeg = extractJpeg(objResp.data)
                if (jpeg != null) return jpeg
                return objResp.data
            }
        }

        // Approach B: Sony GetPartialLargeObject (0x9211). Params: handle, offset, maxBytes.
        Log.d(TAG, "GetObject failed, trying GetPartialLargeObject")
        val partialResp = transport.sendCommandWithData(
            PtpConstants.OP_SONY_GET_PARTIAL_LARGE_OBJECT,
            PtpConstants.PHOTO_OBJECT_HANDLE,
            0,          // offset from start
            0x01000000  // max 16MB
        )
        if (partialResp.isSuccess && partialResp.data.size > 1024) {
            val jpeg = extractJpeg(partialResp.data)
            if (jpeg != null) return jpeg
            return partialResp.data
        }

        // Approach C: Plain GetObject without ObjectInfo first
        Log.d(TAG, "GetPartialLargeObject failed, trying plain GetObject")
        val plainResp = transport.sendCommandWithData(
            PtpConstants.OP_GET_OBJECT,
            PtpConstants.PHOTO_OBJECT_HANDLE
        )
        if (plainResp.isSuccess && plainResp.data.size > 1024) {
            val jpeg = extractJpeg(plainResp.data)
            if (jpeg != null) return jpeg
            return plainResp.data
        }

        Log.w(TAG, "All download approaches failed " +
                "(info=${PtpConstants.responseCodeName(infoResp.responseCode)}, " +
                "partial=${PtpConstants.responseCodeName(partialResp.responseCode)}, " +
                "plain=${PtpConstants.responseCodeName(plainResp.responseCode)})")
        return null
    }

    /**
     * Poll for events (non-blocking).
     */
    fun pollEvent(timeoutMs: Int = 100): PtpEvent? = transport.readEvent(timeoutMs)

    /**
     * Wait for an ObjectAdded event (after capture).
     */
    fun waitForObjectAdded(maxWaitMs: Long = 10_000): Int {
        val startTime = System.currentTimeMillis()
        while (System.currentTimeMillis() - startTime < maxWaitMs) {
            val event = pollEvent(500)
            if (event != null && event.eventCode == PtpConstants.EVENT_OBJECT_ADDED) {
                if (event.params.isNotEmpty()) {
                    Log.d(TAG, "ObjectAdded: handle=${event.params[0]}")
                    return event.params[0]
                }
            }
        }
        Log.w(TAG, "Timeout waiting for ObjectAdded event")
        return -1
    }

    /**
     * Flush stale data from the USB pipe and clear endpoints.
     * Call between operations (e.g., after stopping liveview before capture).
     */
    fun flushAndResetPipe() {
        transport.clearEndpoints()
    }

    // ── Private helpers ──

    private fun drainEvents() {
        var drained = 0
        while (drained < 20) {
            val event = transport.readEvent(30) ?: break
            drained++
        }
    }

    private fun parseLiveViewDataset(data: ByteArray): SonyLiveViewFrame? {
        if (data.size >= 16) {
            val header = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
            val imageOffset = header.int.toLong() and 0xFFFFFFFFL
            val imageSize = header.int.toLong() and 0xFFFFFFFFL
            val focusOffset = header.int.toLong() and 0xFFFFFFFFL
            val focusSize = header.int.toLong() and 0xFFFFFFFFL

            val imageEnd = imageOffset + imageSize
            val validImage = imageOffset >= 16L && imageSize >= 3L &&
                    imageOffset <= data.size.toLong() &&
                    imageEnd >= imageOffset && imageEnd <= data.size.toLong()

            if (validImage) {
                val imageStartInt = imageOffset.toInt()
                val imageEndInt = imageEnd.toInt()
                val rawImage = data.copyOfRange(imageStartInt, imageEndInt)
                val jpeg = extractJpeg(rawImage) ?: rawImage

                val focusEnd = focusOffset + focusSize
                val validFocus = focusSize > 0L && focusOffset >= 16L &&
                        focusOffset <= data.size.toLong() &&
                        focusEnd >= focusOffset && focusEnd <= data.size.toLong()
                val focusInfo = if (validFocus) {
                    parseFocalFrameInfo(data, focusOffset.toInt(), focusSize.toInt())
                } else {
                    null
                }

                if (!loggedLiveViewDataset) {
                    loggedLiveViewDataset = true
                    Log.d(
                        TAG,
                        "PTP3 LiveView dataset: image@$imageOffset/${imageSize}B " +
                                "focus@$focusOffset/${focusSize}B " +
                                "frames=${focusInfo?.frames?.size ?: 0} " +
                                "focusVersion=${focusInfo?.version ?: 0}"
                    )
                }
                return SonyLiveViewFrame(jpeg = jpeg, focusFrameInfo = focusInfo)
            }
        }

        // Protocol-2 / legacy fallback: there may be no dataset header.
        val jpeg = extractJpeg(data) ?: return null
        return SonyLiveViewFrame(jpeg = jpeg, focusFrameInfo = null)
    }

    /** Parse Sony Camera Control PTP 3 FocalFrameInfo, little-endian. */
    private fun parseFocalFrameInfo(
        data: ByteArray,
        offset: Int,
        size: Int
    ): SonyFocusFrameInfo? {
        if (offset < 0 || size < 72 || offset + size > data.size) return null

        return try {
            val bb = ByteBuffer.wrap(data, offset, size).slice().order(ByteOrder.LITTLE_ENDIAN)

            fun u16(): Int = bb.short.toInt() and 0xFFFF
            fun u32(): Long = bb.int.toLong() and 0xFFFFFFFFL
            fun skip(count: Int): Boolean {
                if (count < 0 || bb.remaining() < count) return false
                bb.position(bb.position() + count)
                return true
            }

            if (bb.remaining() < 8) return null
            val version = u16()
            if (!skip(6)) return null

            if (!skip(40) || bb.remaining() < 8) return null
            val reservedArrayNum = u16()
            if (!skip(6)) return null

            if (reservedArrayNum > bb.remaining() / 24) return null
            if (!skip(reservedArrayNum * 24)) return null

            if (bb.remaining() < 16) return null
            val xDenominator = u32()
            val yDenominator = u32()
            val frameNum = u16()
            if (!skip(6)) return null

            val readableFrames = minOf(frameNum, bb.remaining() / 24)
            val frames = ArrayList<SonyFocusFrame>(readableFrames)
            repeat(readableFrames) {
                val type = u16()
                val state = u16()
                val priority = bb.get().toInt() and 0xFF
                if (!skip(3)) return null
                val xNumerator = u32()
                val yNumerator = u32()
                val height = u32()
                val width = u32()
                frames += SonyFocusFrame(
                    type = type,
                    state = state,
                    priority = priority,
                    xNumerator = xNumerator,
                    yNumerator = yNumerator,
                    xDenominator = xDenominator,
                    yDenominator = yDenominator,
                    width = width,
                    height = height
                )
            }

            SonyFocusFrameInfo(version = version, frames = frames)
        } catch (e: Exception) {
            Log.w(TAG, "Unable to parse FocalFrameInfo: ${e.message}")
            null
        }
    }

    private fun extractJpeg(data: ByteArray): ByteArray? {
        if (data.size < 3) return null
        val start = findJpegStart(data, 0)
        if (start < 0) return null
        return if (start == 0) data else data.copyOfRange(start, data.size)
    }

    /**
     * Extract the largest embedded JPEG from data that may be a RAW file.
     * Sony ARW files contain a small thumbnail JPEG and a full-size preview JPEG.
     * We find all JPEG starts and pick the one that decodes to the largest image.
     */
    private fun extractLargestJpeg(data: ByteArray): ByteArray? {
        if (data.size < 3) return null

        // Find all JPEG start positions
        val jpegStarts = mutableListOf<Int>()
        var searchFrom = 0
        while (searchFrom < data.size - 2) {
            val pos = findJpegStart(data, searchFrom)
            if (pos < 0) break
            jpegStarts.add(pos)
            searchFrom = pos + 3
        }

        if (jpegStarts.isEmpty()) return null
        if (jpegStarts.size == 1) return data.copyOfRange(jpegStarts[0], data.size)

        Log.d(TAG, "Found ${jpegStarts.size} embedded JPEGs at offsets: ${jpegStarts.joinToString()}")

        // Try bounds-only decode on each to find the largest
        var bestStart = jpegStarts[0]
        var bestPixels = 0
        for (start in jpegStarts) {
            val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(data, start, data.size - start, opts)
            val pixels = opts.outWidth * opts.outHeight
            Log.d(TAG, "  JPEG@$start: ${opts.outWidth}x${opts.outHeight}")
            if (pixels > bestPixels) {
                bestPixels = pixels
                bestStart = start
            }
        }

        Log.d(TAG, "Selected JPEG at offset $bestStart (${bestPixels} pixels)")
        return data.copyOfRange(bestStart, data.size)
    }

    /**
     * Find a valid JPEG start: FFD8FF (SOI followed by a marker).
     */
    private fun findJpegStart(data: ByteArray, from: Int): Int {
        for (i in from until data.size - 2) {
            if (data[i] == 0xFF.toByte() && data[i + 1] == 0xD8.toByte() &&
                data[i + 2] == 0xFF.toByte()) return i
        }
        return -1
    }

    private fun parseDeviceInfo(data: ByteArray) {
        try {
            val bb = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
            bb.position(bb.position() + 8) // Skip version fields
            skipPtpString(bb)              // Vendor extension desc
            bb.position(bb.position() + 2) // Functional mode
            skipPtpArray(bb, 2)            // Operations supported
            skipPtpArray(bb, 2)            // Events supported
            skipPtpArray(bb, 2)            // Device properties supported
            skipPtpArray(bb, 2)            // Capture formats
            skipPtpArray(bb, 2)            // Image formats
            skipPtpString(bb)              // Manufacturer
            deviceName = readPtpString(bb) // Model
            skipPtpString(bb)              // Device version
            serialNumber = readPtpString(bb) // Serial number
        } catch (e: Exception) {
            Log.w(TAG, "Error parsing device info: ${e.message}")
        }
    }

    private fun readPtpString(bb: ByteBuffer): String? {
        if (bb.remaining() < 1) return null
        val numChars = bb.get().toInt() and 0xFF
        if (numChars == 0 || bb.remaining() < numChars * 2) return null
        val chars = CharArray(numChars) { bb.getShort().toInt().toChar() }
        return String(chars).trimEnd('\u0000').ifEmpty { null }
    }

    private fun skipPtpString(bb: ByteBuffer) {
        if (bb.remaining() < 1) return
        val numChars = bb.get().toInt() and 0xFF
        val skip = numChars * 2
        if (bb.remaining() >= skip) bb.position(bb.position() + skip)
    }

    private fun skipPtpArray(bb: ByteBuffer, elementSize: Int) {
        if (bb.remaining() < 4) return
        val count = bb.getInt()
        val skip = count * elementSize
        if (bb.remaining() >= skip) bb.position(bb.position() + skip)
    }
}
