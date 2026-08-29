package io.github.gallo.sonycamera.ptp

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import io.github.gallo.sonycamera.CameraExposureOption
import io.github.gallo.sonycamera.CameraExposureProperty
import io.github.gallo.sonycamera.CameraExposureSetting
import io.github.gallo.sonycamera.CameraExposureState
import io.github.gallo.sonycamera.CameraSetting
import io.github.gallo.sonycamera.CameraSettingOption
import io.github.gallo.sonycamera.CameraSettingProperty
import io.github.gallo.sonycamera.CameraSettingsState
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

        // SessionAlreadyOpen usually means a previous PC-Remote owner survived.
        // Release Sony priority too; a plain CloseSession can leave the remote
        // ownership state that makes the next host look connected but deny liveview.
        if (response.responseCode == 0x201E) {
            Log.w(TAG, "PTP session already open — releasing stale Sony remote ownership")
            endSession()
            Thread.sleep(250)
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

        // Sony Camera Remote Command retries 0x9202 during cold connection:
        // the body can initially return an empty capability list while the
        // vendor session is still becoming ready. Keep this retry bounded.
        var extV3: PtpDataResponse? = null
        var extV3Attempts = 0
        if (preferProtocol3) {
            for (attempt in 1..5) {
                extV3Attempts = attempt
                val candidate = transport.sendCommandWithDataShortTimeout(
                    PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO,
                    900,
                    0x012C,
                    1
                )
                extV3 = candidate
                Log.d(TAG, "GetExtDeviceInfo PTP3 attempt $attempt/5: " +
                        "${PtpConstants.responseCodeName(candidate.responseCode)}, ${candidate.dataSize}B")
                if (candidate.isSuccess && candidate.dataSize > 0) break
                Thread.sleep(100)
            }
        }

        val useProtocol3 = extV3?.isSuccess == true && extV3.dataSize > 0
        if (preferProtocol3 && !useProtocol3) {
            Log.e(TAG, "a7C II protocol-3 device-info request failed; refusing protocol-2 fallback")
            return false
        }
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

        sonyExtensionDebug = buildString {
            append("ext=").append(selectedProtocol)
            if (preferProtocol3) {
                append(" v3=")
                append(PtpConstants.responseCodeName(extV3?.responseCode ?: 0))
                append("/").append(extV3?.dataSize ?: 0).append("B")
                append(" attempts=").append(extV3Attempts)
                append(" sdio3=")
                append(PtpConstants.responseCodeName(r3?.responseCode ?: 0))
            }
            append(" extInfo=")
            append(PtpConstants.responseCodeName(extInfo.responseCode))
            append("/").append(extInfo.dataSize).append("B")
        }

        // Acquire host control immediately after phase 3. The response can be late
        // on Sony bodies, so the manager uses a successful live-view fetch as the
        // authoritative readiness check instead of trusting this ACK alone.
        val priority = setControlDeviceA(PtpConstants.PROP_SONY_PRIORITY_MODE, 1)
        Log.d(TAG, "PriorityMode=1: ${PtpConstants.responseCodeName(priority.responseCode)}")
        if (preferProtocol3) Thread.sleep(250)
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

    /** Move the Sony logical AF target on the a7C II 640x480 logical grid. */
    fun setAfPoint(x: Int, y: Int): String = commandAfPoint("AF TARGET", x, y)

    /** Explicit autofocus trigger used after an AF-area position update. */
    fun setAutofocusPressed(pressed: Boolean): PtpResponse =
        setControlDeviceB(PtpConstants.PROP_SONY_SHUTTER_HALF_PRESS, if (pressed) 2 else 1)

    /** Diagnostic convenience entry point retained by the demo. */
    fun testAfCenter(): String = commandAfPoint("AF CENTER TEST", 320, 240)

    private fun commandAfPoint(label: String, x: Int, y: Int): String {
        val safeX = x.coerceIn(0, 639)
        val safeY = y.coerceIn(0, 479)
        val setResult = setAfAreaPosition(safeX, safeY)
        return buildString {
            append(label).append(" x=").append(safeX).append(" y=").append(safeY)
            append(" | D2DC/9207=")
            append(PtpConstants.responseCodeName(setResult.responseCode))
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
        val all = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            700
        )
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

    // ── Exposure controls ───────────────────────────────────────────────

    private data class ExposureDescriptor(
        val setting: CameraExposureSetting,
        val propertyCode: Int,
        val dataType: Int,
        val writable: Boolean,
        val initialValue: Long?,
        val enumValues: List<Long>,
        val rangeMin: Long?,
        val rangeMax: Long?
    )

    data class ExposureAdjustmentResult(
        val state: CameraExposureState,
        val success: Boolean,
        val message: String? = null
    )

    private val exposureDescriptors = linkedMapOf<CameraExposureSetting, ExposureDescriptor>()
    @Volatile private var exposureDescriptorsProbed = false
    @Volatile private var lastExposureState: CameraExposureState? = null

    /**
     * Discover exposure controls once per PTP session.
     *
     * Modern Sony protocol-3 bodies expose FNumber / Shutter / ISO in
     * GetAllDevicePropData (0x9209), but may not support 0x9204 or 0x9206.
     * Treat 0x9209 as the primary discovery/readback source and retain the
     * older per-property operations only as compatibility fallbacks.
     */
    @Synchronized
    private fun ensureExposureDescriptors(force: Boolean = false) {
        if (exposureDescriptorsProbed && !force) return
        exposureDescriptors.clear()

        val snapshot = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            2_000
        )
        val snapshotData = if (snapshot.isSuccess) snapshot.data else ByteArray(0)

        val candidates = linkedMapOf(
            CameraExposureSetting.APERTURE to intArrayOf(PtpConstants.PROP_PTP_F_NUMBER),
            CameraExposureSetting.SHUTTER_SPEED to intArrayOf(
                PtpConstants.PROP_SONY_SHUTTER_SPEED,
                PtpConstants.PROP_SONY_SHUTTER_SPEED_ALT
            ),
            CameraExposureSetting.ISO to intArrayOf(
                PtpConstants.PROP_SONY_ISO,
                PtpConstants.PROP_SONY_ISO_ALT
            )
        )

        candidates.forEach { (setting, ids) ->
            val knownType = when (setting) {
                CameraExposureSetting.APERTURE -> 0x0004 // UINT16, f-number x100
                CameraExposureSetting.SHUTTER_SPEED,
                CameraExposureSetting.ISO -> 0x0006 // UINT32
            }

            var fromSnapshot: ExposureDescriptor? = null
            for (propertyCode in ids) {
                val offset = findSonyPropertyOffset(snapshotData, propertyCode, knownType) ?: continue

                // 0x9209 contains full DevicePropDesc-shaped records on protocol-3
                // bodies. Parse the form section as well as the current value so a
                // zoom lens can report its real F-number floor/ceiling and choices.
                fromSnapshot = parseExposureDescriptor(snapshotData, setting, propertyCode)
                if (fromSnapshot == null) {
                    val writable = (snapshotData.getOrNull(offset + 4)?.toInt()?.and(0xFF) ?: 0) != 0
                    val seed = ExposureDescriptor(
                        setting = setting,
                        propertyCode = propertyCode,
                        dataType = knownType,
                        writable = writable,
                        initialValue = null,
                        enumValues = emptyList(),
                        rangeMin = null,
                        rangeMax = null
                    )
                    fromSnapshot = seed.copy(
                        initialValue = readCurrentFromAllProperties(snapshotData, seed)
                    )
                }
                break
            }

            // Some 0x9209 snapshots carry only current value while the per-control
            // descriptor contains the lens range/enum. Enrich aperture once at init
            // when the aggregate record does not expose either form.
            if (setting == CameraExposureSetting.APERTURE &&
                fromSnapshot != null &&
                fromSnapshot.enumValues.size < 2 &&
                fromSnapshot.rangeMin == null &&
                fromSnapshot.rangeMax == null
            ) {
                val controlDesc = transport.sendCommandWithDataShortTimeout(
                    PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC,
                    1_200,
                    fromSnapshot.propertyCode
                )
                if (controlDesc.isSuccess && controlDesc.data.isNotEmpty()) {
                    parseExposureDescriptor(controlDesc.data, setting, fromSnapshot.propertyCode)?.let { rich ->
                        fromSnapshot = rich.copy(
                            writable = rich.writable || fromSnapshot.writable,
                            initialValue = fromSnapshot.initialValue ?: rich.initialValue
                        )
                    }
                }
            }

            val descriptor = fromSnapshot ?: probeExposureDescriptor(setting, ids)
            descriptor?.let {
                exposureDescriptors[setting] = it
                Log.d(
                    TAG,
                    "Exposure ${setting.name}: prop=0x${it.propertyCode.toString(16)} " +
                        "type=0x${it.dataType.toString(16)} writable=${it.writable} " +
                        "choices=${it.enumValues.size} current=${it.initialValue} " +
                        "range=${it.rangeMin ?: "?"}..${it.rangeMax ?: "?"} " +
                        "source=${if (fromSnapshot != null) "9209" else "legacy"}"
                )
            }
        }
        exposureDescriptorsProbed = true
    }

    private fun findSonyPropertyOffset(data: ByteArray, propertyCode: Int, dataType: Int): Int? {
        if (data.size < 6) return null
        for (i in 0 until data.size - 5) {
            if (u16(data, i) == propertyCode && u16(data, i + 2) == dataType) return i
        }
        return null
    }

    private fun probeExposureDescriptor(
        setting: CameraExposureSetting,
        candidates: IntArray
    ): ExposureDescriptor? {
        val fallbackType = when (setting) {
            CameraExposureSetting.APERTURE -> 0x0004 // UINT16
            CameraExposureSetting.SHUTTER_SPEED,
            CameraExposureSetting.ISO -> 0x0006 // UINT32
        }

        for (propertyCode in candidates) {
            val descResponse = transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC,
                1200,
                propertyCode
            )
            if (descResponse.isSuccess && descResponse.data.isNotEmpty()) {
                parseExposureDescriptor(descResponse.data, setting, propertyCode)?.let { return it }
            }

            // Some bodies return a sparse/empty ControlDeviceDesc but still
            // support the property value. A successful direct read is enough
            // to keep the known-safe exposure id as a fallback.
            val valueResponse = transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
                900,
                propertyCode
            )
            if (valueResponse.isSuccess && valueResponse.data.isNotEmpty()) {
                val size = scalarSize(fallbackType)
                val current = readUnsignedScalar(valueResponse.data, 0, size)
                return ExposureDescriptor(
                    setting = setting,
                    propertyCode = propertyCode,
                    dataType = fallbackType,
                    writable = true,
                    initialValue = current,
                    enumValues = emptyList(),
                    rangeMin = null,
                    rangeMax = null
                )
            }
        }
        return null
    }

    private fun parseExposureDescriptor(
        data: ByteArray,
        setting: CameraExposureSetting,
        expectedCode: Int
    ): ExposureDescriptor? {
        // This parser is shared by the small 0x9206 response and the complete
        // 0x9209 snapshot. Scan the whole blob; structural validation below rejects
        // accidental occurrences of the property code inside another value.
        if (data.size < 5) return null
        val starts = (0 until (data.size - 4)).filter { offset ->
            u16(data, offset) == expectedCode
        }
        if (starts.isEmpty()) return null

        data class Candidate(
            val descriptor: ExposureDescriptor,
            val score: Int
        )

        val parsed = mutableListOf<Candidate>()
        for (base in starts) {
            val type = u16(data, base + 2)
            val expectedType = when (setting) {
                CameraExposureSetting.APERTURE -> 0x0004
                CameraExposureSetting.SHUTTER_SPEED,
                CameraExposureSetting.ISO -> 0x0006
            }
            if (type != expectedType) continue
            val size = scalarSize(type)
            if (size !in 1..4) continue
            val getSet = data[base + 4].toInt() and 0xFF

            // Standard PTP layout and Sony's extra-flag layout are both seen
            // in the wild. Pick the one whose form section is structurally valid.
            for (sonyExtraFlag in listOf(false, true)) {
                val currentOffset = base + 5 + size + if (sonyExtraFlag) 1 else 0
                if (currentOffset + size > data.size) continue
                val current = readUnsignedScalar(data, currentOffset, size)
                val formOffset = currentOffset + size
                val formFlag = if (formOffset < data.size) data[formOffset].toInt() and 0xFF else 0
                if (formFlag !in 0..2) continue

                var enumValues = emptyList<Long>()
                var rangeMin: Long? = null
                var rangeMax: Long? = null
                var structurallyValid = true
                when (formFlag) {
                    1 -> {
                        val start = formOffset + 1
                        if (start + size * 3 <= data.size) {
                            rangeMin = readUnsignedScalar(data, start, size)
                            rangeMax = readUnsignedScalar(data, start + size, size)
                        } else structurallyValid = false
                    }
                    2 -> {
                        val countOffset = formOffset + 1
                        if (countOffset + 2 <= data.size) {
                            val count = u16(data, countOffset)
                            val valuesOffset = countOffset + 2
                            if (count in 1..512 && valuesOffset + count * size <= data.size) {
                                enumValues = List(count) { index ->
                                    readUnsignedScalar(data, valuesOffset + index * size, size) ?: 0L
                                }
                            } else structurallyValid = false
                        } else structurallyValid = false
                    }
                }
                if (!structurallyValid) continue

                var score = 2
                if (getSet != 0) score += 2
                if (enumValues.size > 1) score += 3
                if (rangeMin != null && rangeMax != null) score += 2
                if (current != null && isPlausibleExposureRaw(setting, current)) score += 2
                if (sonyExtraFlag) score += 1 // 9206/9209 Sony data commonly uses this layout.

                parsed += Candidate(
                    ExposureDescriptor(
                        setting = setting,
                        propertyCode = expectedCode,
                        dataType = type,
                        writable = getSet != 0,
                        initialValue = current,
                        enumValues = enumValues,
                        rangeMin = rangeMin,
                        rangeMax = rangeMax
                    ),
                    score
                )
            }
        }
        return parsed.maxByOrNull { it.score }?.descriptor
    }

    /** Read aperture, shutter and ISO with one 0x9209 round trip. */
    fun readExposureState(forceDescriptorProbe: Boolean = false): CameraExposureState {
        ensureExposureDescriptors(forceDescriptorProbe)
        val all = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)
        val allData = if (all.isSuccess) all.data else ByteArray(0)

        // Refresh descriptor forms from the live 0x9209 snapshot. This matters for
        // variable-aperture zoom lenses: the valid F-number floor can change when
        // focal length changes without reconnecting the USB session. Preserve any
        // richer init-time form when a later snapshot is sparse.
        if (allData.isNotEmpty()) {
            exposureDescriptors.toMap().forEach { (setting, previous) ->
                parseExposureDescriptor(allData, setting, previous.propertyCode)?.let { latest ->
                    exposureDescriptors[setting] = latest.copy(
                        writable = latest.writable || previous.writable,
                        initialValue = latest.initialValue ?: previous.initialValue,
                        enumValues = if (latest.enumValues.size >= 2) latest.enumValues else previous.enumValues,
                        rangeMin = latest.rangeMin ?: previous.rangeMin,
                        rangeMax = latest.rangeMax ?: previous.rangeMax
                    )
                }
            }
        }

        fun current(descriptor: ExposureDescriptor?): Long? {
            descriptor ?: return null
            val fromAll = if (allData.isNotEmpty()) {
                readCurrentFromAllProperties(allData, descriptor)
            } else null
            if (fromAll != null) return fromAll

            val direct = transport.sendCommandWithData(
                PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
                descriptor.propertyCode
            )
            if (!direct.isSuccess || direct.data.isEmpty()) return descriptor.initialValue
            return readUnsignedScalar(direct.data, 0, scalarSize(descriptor.dataType))
                ?: descriptor.initialValue
        }

        val apertureDesc = exposureDescriptors[CameraExposureSetting.APERTURE]
        val shutterDesc = exposureDescriptors[CameraExposureSetting.SHUTTER_SPEED]
        val isoDesc = exposureDescriptors[CameraExposureSetting.ISO]

        val state = CameraExposureState(
            aperture = buildExposureProperty(apertureDesc, current(apertureDesc)),
            shutterSpeed = buildExposureProperty(shutterDesc, current(shutterDesc)),
            iso = buildExposureProperty(isoDesc, current(isoDesc))
        )
        lastExposureState = state
        return state
    }

    /** Step one setting by one camera-supported value and verify the write. */
    fun adjustExposure(
        setting: CameraExposureSetting,
        direction: Int
    ): ExposureAdjustmentResult {
        val before = readExposureState()
        val property = before.property(setting)
        val descriptor = exposureDescriptors[setting]
        if (descriptor == null || !property.writable || property.current == null) {
            return ExposureAdjustmentResult(before, false, "${settingLabel(setting)} is not adjustable in this camera mode")
        }
        if (property.options.size < 2) {
            return ExposureAdjustmentResult(before, false, "No selectable ${settingLabel(setting)} steps reported by camera")
        }

        val currentRaw = property.current.rawValue
        var index = property.options.indexOfFirst { it.rawValue == currentRaw }
        if (index < 0) index = 0
        val targetIndex = (index + direction.coerceIn(-1, 1)).coerceIn(0, property.options.lastIndex)
        if (targetIndex == index) return ExposureAdjustmentResult(before, true)

        val target = property.options[targetIndex]
        val response = setExposureRaw(descriptor, target.rawValue)
        Thread.sleep(160)
        var after = readExposureState()
        if (after.property(setting).current?.rawValue != target.rawValue) {
            // Sony can acknowledge configuration writes before the property
            // snapshot changes. Give it one short second chance.
            Thread.sleep(220)
            after = readExposureState()
        }

        val applied = after.property(setting).current?.rawValue == target.rawValue
        if (applied) return ExposureAdjustmentResult(after, true)

        val reason = if (response.isSuccess) {
            "Camera did not apply ${target.label}"
        } else {
            "Camera rejected ${settingLabel(setting)} change (${PtpConstants.responseCodeName(response.responseCode)})"
        }
        return ExposureAdjustmentResult(after, false, reason)
    }


    /**
     * Set one exact value selected from the Sony-style UI selector.
     *
     * Interactive writes must not synchronously perform a full 9209 read before
     * and after every detent. Send the Sony control immediately, publish an
     * optimistic state from the last authoritative snapshot, then let the fast
     * background telemetry poll confirm/correct it.
     */
    fun setExposureValue(
        setting: CameraExposureSetting,
        rawValue: Long
    ): ExposureAdjustmentResult {
        ensureExposureDescriptors()
        val descriptor = exposureDescriptors[setting]
        val before = lastExposureState ?: readExposureState()
        if (descriptor == null) {
            return ExposureAdjustmentResult(before, false, "${settingLabel(setting)} is unavailable")
        }
        val property = before.property(setting)
        if (!property.writable || property.current == null) {
            return ExposureAdjustmentResult(before, false, "${settingLabel(setting)} is locked in this camera mode")
        }

        val response = setExposureRaw(descriptor, rawValue)
        if (!response.isSuccess) {
            return ExposureAdjustmentResult(
                before,
                false,
                "Camera rejected ${settingLabel(setting)} (${PtpConstants.responseCodeName(response.responseCode)})"
            )
        }

        val target = property.options.firstOrNull { it.rawValue == rawValue }
            ?: CameraExposureOption(rawValue, formatExposureValue(setting, rawValue))
        val optimisticProperty = property.copy(current = target)
        val optimistic = when (setting) {
            CameraExposureSetting.APERTURE -> before.copy(aperture = optimisticProperty)
            CameraExposureSetting.SHUTTER_SPEED -> before.copy(shutterSpeed = optimisticProperty)
            CameraExposureSetting.ISO -> before.copy(iso = optimisticProperty)
        }
        lastExposureState = optimistic
        return ExposureAdjustmentResult(optimistic, true)
    }

    private fun setExposureRaw(descriptor: ExposureDescriptor, value: Long): PtpResponse {
        val size = scalarSize(descriptor.dataType)
        val data = ByteBuffer.allocate(size).order(ByteOrder.LITTLE_ENDIAN).apply {
            when (size) {
                1 -> put((value and 0xFF).toByte())
                2 -> putShort((value and 0xFFFF).toShort())
                4 -> putInt((value and 0xFFFFFFFFL).toInt())
            }
        }.array()

        val sony = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_A,
            data,
            descriptor.propertyCode
        )
        if (sony.isSuccess) return sony

        // FNumber is a standard PTP property as well. A few bodies expose it
        // through 9206 but expect the standard SetDevicePropValue write path.
        if (descriptor.propertyCode == PtpConstants.PROP_PTP_F_NUMBER) {
            return transport.sendCommandWithDataOut(
                PtpConstants.OP_SET_DEVICE_PROP_VALUE,
                data,
                descriptor.propertyCode
            )
        }
        return sony
    }

    private fun buildExposureProperty(
        descriptor: ExposureDescriptor?,
        currentRaw: Long?
    ): CameraExposureProperty {
        if (descriptor == null) {
            return CameraExposureProperty(current = null, options = emptyList(), writable = false)
        }

        val raws = buildRawChoices(descriptor, currentRaw)
        val options = raws.map { raw ->
            CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))
        }
        val current = currentRaw?.let { raw ->
            options.firstOrNull { it.rawValue == raw }
                ?: CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))
        }
        // Aperture limits must come from the camera/lens descriptor itself.
        // Never derive MIN/MAX from the generic compatibility choice table: if
        // Sony does not report a range or enum, leave the bound unknown in UI.
        val authoritativeApertureValues = descriptor.enumValues.distinct()
        val minimum = if (descriptor.setting == CameraExposureSetting.APERTURE) {
            (descriptor.rangeMin ?: authoritativeApertureValues.minOrNull())?.let { raw ->
                CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))
            }
        } else null
        val maximum = if (descriptor.setting == CameraExposureSetting.APERTURE) {
            (descriptor.rangeMax ?: authoritativeApertureValues.maxOrNull())?.let { raw ->
                CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))
            }
        } else null
        return CameraExposureProperty(
            current = current,
            options = options,
            writable = descriptor.writable && options.size > 1,
            minimum = minimum,
            maximum = maximum
        )
    }

    private fun buildRawChoices(
        descriptor: ExposureDescriptor,
        currentRaw: Long?
    ): List<Long> {
        var values = descriptor.enumValues.distinct()
        if (descriptor.setting == CameraExposureSetting.ISO && values.isNotEmpty()) {
            values = values.filter { isUsefulIsoValue(it) }
        }
        if (values.size < 2) {
            values = canonicalValues(descriptor.setting).filter { value ->
                val aboveMin = descriptor.rangeMin?.let { value >= it } ?: true
                val belowMax = descriptor.rangeMax?.let { value <= it } ?: true
                aboveMin && belowMax
            }
        }

        val mutable = values.toMutableList()
        if (currentRaw != null && currentRaw !in mutable) mutable += currentRaw
        return when (descriptor.setting) {
            CameraExposureSetting.APERTURE -> mutable.distinct().sorted()
            CameraExposureSetting.ISO -> mutable.distinct().sortedWith(compareBy { isoSortKey(it) })
            CameraExposureSetting.SHUTTER_SPEED -> mutable.distinct().sortedByDescending { shutterSeconds(it) }
        }
    }

    private fun canonicalValues(setting: CameraExposureSetting): List<Long> = when (setting) {
        CameraExposureSetting.APERTURE -> listOf(
            100, 110, 120, 140, 160, 180, 200, 220, 250, 280, 320, 350,
            400, 450, 500, 560, 630, 710, 800, 900, 1000, 1100, 1300, 1400,
            1600, 1800, 2000, 2200, 2500, 2900, 3200, 3600, 4000, 4500,
            5100, 5700, 6400
        ).map(Int::toLong)

        CameraExposureSetting.ISO -> listOf(
            0x00FFFFFFL,
            50L, 64L, 80L, 100L, 125L, 160L, 200L, 250L, 320L, 400L, 500L,
            640L, 800L, 1000L, 1250L, 1600L, 2000L, 2500L, 3200L, 4000L,
            5000L, 6400L, 8000L, 10000L, 12800L, 16000L, 20000L, 25600L,
            32000L, 40000L, 51200L, 64000L, 80000L, 102400L, 128000L,
            160000L, 204800L, 256000L, 320000L, 409600L
        )

        CameraExposureSetting.SHUTTER_SPEED -> buildList {
            listOf(30, 25, 20, 15, 13, 10, 8, 6, 5, 4, 3, 2).forEach { add(packShutter(it, 1)) }
            add(packShutter(1, 1))
            listOf(
                2, 3, 4, 5, 6, 8, 10, 13, 15, 20, 25, 30, 40, 50, 60, 80,
                100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1000, 1250,
                1600, 2000, 2500, 3200, 4000, 5000, 6400, 8000
            ).forEach { denominator -> add(packShutter(1, denominator)) }
        }
    }

    private fun packShutter(numerator: Int, denominator: Int): Long =
        ((numerator.toLong() and 0xFFFF) shl 16) or (denominator.toLong() and 0xFFFF)

    private fun shutterSeconds(raw: Long): Double {
        val numerator = (raw ushr 16) and 0xFFFF
        val denominator = raw and 0xFFFF
        if (numerator == 0L || denominator == 0L) return Double.POSITIVE_INFINITY
        return numerator.toDouble() / denominator.toDouble()
    }

    private fun formatExposureValue(setting: CameraExposureSetting, raw: Long): String = when (setting) {
        CameraExposureSetting.APERTURE -> {
            if (raw <= 0L) "--"
            else {
                val value = raw.toDouble() / 100.0
                val text = if (raw % 100L == 0L) value.toInt().toString() else "%.1f".format(value)
                "F$text"
            }
        }
        CameraExposureSetting.ISO -> {
            when {
                raw == 0x00FFFFFFL -> "AUTO"
                raw in 25L..409600L -> raw.toString()
                (raw and 0x00FFFFFFL) in 25L..409600L -> (raw and 0x00FFFFFFL).toString()
                else -> "--"
            }
        }
        CameraExposureSetting.SHUTTER_SPEED -> {
            val numerator = (raw ushr 16) and 0xFFFF
            val denominator = raw and 0xFFFF
            when {
                raw == 0L -> "BULB"
                numerator == 0L || denominator == 0L -> "--"
                numerator >= denominator -> {
                    val seconds = numerator.toDouble() / denominator.toDouble()
                    if (kotlin.math.abs(seconds - seconds.toInt()) < 0.05) "${seconds.toInt()}\""
                    else "%.1f\"".format(seconds)
                }
                numerator == 1L -> "1/$denominator"
                else -> "$numerator/$denominator"
            }
        }
    }

    private fun isUsefulIsoValue(raw: Long): Boolean =
        raw == 0x00FFFFFFL || raw in 25L..409600L

    private fun isoSortKey(raw: Long): Long = if (raw == 0x00FFFFFFL) Long.MIN_VALUE else raw

    private fun settingLabel(setting: CameraExposureSetting): String = when (setting) {
        CameraExposureSetting.APERTURE -> "aperture"
        CameraExposureSetting.SHUTTER_SPEED -> "shutter speed"
        CameraExposureSetting.ISO -> "ISO"
    }

    private fun readCurrentFromAllProperties(
        data: ByteArray,
        descriptor: ExposureDescriptor
    ): Long? {
        val size = scalarSize(descriptor.dataType)
        if (size !in 1..4) return null
        for (i in 0 until data.size - 5) {
            if (u16(data, i) != descriptor.propertyCode) continue
            if (u16(data, i + 2) != descriptor.dataType) continue

            // Sony GetAllDevicePropData commonly inserts one flag byte between
            // default and current; retain the standard PTP layout as fallback.
            val sonyCurrent = readUnsignedScalar(data, i + 6 + size, size)
            if (sonyCurrent != null && isPlausibleExposureRaw(descriptor.setting, sonyCurrent)) {
                return sonyCurrent
            }

            val standardCurrent = readUnsignedScalar(data, i + 5 + size, size)
            if (standardCurrent != null && isPlausibleExposureRaw(descriptor.setting, standardCurrent)) {
                return standardCurrent
            }
        }
        return null
    }

    private fun isPlausibleExposureRaw(setting: CameraExposureSetting, raw: Long): Boolean = when (setting) {
        CameraExposureSetting.APERTURE -> raw in 80L..10000L
        CameraExposureSetting.ISO -> {
            val low24 = raw and 0x00FFFFFFL
            raw == 0x00FFFFFFL || low24 in 25L..819200L
        }
        CameraExposureSetting.SHUTTER_SPEED -> {
            if (raw == 0L) true // BULB
            else {
                val numerator = (raw ushr 16) and 0xFFFF
                val denominator = raw and 0xFFFF
                numerator > 0L && denominator > 0L
            }
        }
    }

    private fun scalarSize(dataType: Int): Int = when (dataType) {
        0x0001, 0x0002 -> 1
        0x0003, 0x0004 -> 2
        0x0005, 0x0006 -> 4
        else -> 0
    }

    private fun readUnsignedScalar(data: ByteArray, offset: Int, size: Int): Long? {
        if (offset < 0 || size !in listOf(1, 2, 4) || offset + size > data.size) return null
        return when (size) {
            1 -> (data[offset].toInt() and 0xFF).toLong()
            2 -> (ByteBuffer.wrap(data, offset, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF).toLong()
            4 -> ByteBuffer.wrap(data, offset, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong() and 0xFFFFFFFFL
            else -> null
        }
    }

    private fun u16(data: ByteArray, offset: Int): Int {
        if (offset < 0 || offset + 2 > data.size) return -1
        return (data[offset].toInt() and 0xFF) or ((data[offset + 1].toInt() and 0xFF) shl 8)
    }


    // ── Generic camera settings (Sony/PTP protocol 3) ───────────────────

    private data class GenericSettingDescriptor(
        val setting: CameraSetting,
        val propertyCode: Int,
        val dataType: Int,
        val currentValue: Long?,
        val enumValues: List<Long>,
        val writable: Boolean
    )

    data class CameraSettingAdjustmentResult(
        val state: CameraSettingsState,
        val success: Boolean,
        val message: String? = null
    )

    /**
     * Read AF mode/area, white balance, metering, drive and EV from one 9209
     * snapshot. Unknown values are preserved as hex labels instead of guessed.
     */
    fun readCameraSettingsState(): CameraSettingsState {
        val response = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            900
        )
        val data = if (response.isSuccess) response.data else ByteArray(0)

        fun prop(setting: CameraSetting): CameraSettingProperty {
            val descriptor = findGenericSettingDescriptor(data, setting)
                ?: return CameraSettingProperty(null, emptyList(), false)
            var values = descriptor.enumValues.distinct()
            if (values.size < 2) {
                values = fallbackCameraSettingValues(setting)
            }
            if (setting == CameraSetting.EXPOSURE_COMPENSATION && values.isNotEmpty()) {
                values = values.sortedBy { raw ->
                    (raw and 0xFFFF).toInt().toShort().toInt()
                }
            }
            val current = descriptor.currentValue
            val mutable = values.toMutableList()
            if (current != null && current !in mutable) mutable += current
            val options = mutable.distinct().map { raw ->
                CameraSettingOption(raw, formatCameraSettingValue(setting, raw))
            }
            val currentOption = current?.let { raw ->
                options.firstOrNull { it.rawValue == raw }
                    ?: CameraSettingOption(raw, formatCameraSettingValue(setting, raw))
            }
            return CameraSettingProperty(
                current = currentOption,
                options = options,
                writable = descriptor.writable && options.size > 1
            )
        }

        return CameraSettingsState(
            focusMode = prop(CameraSetting.FOCUS_MODE),
            focusArea = prop(CameraSetting.FOCUS_AREA),
            whiteBalance = prop(CameraSetting.WHITE_BALANCE),
            meteringMode = prop(CameraSetting.METERING_MODE),
            driveMode = prop(CameraSetting.DRIVE_MODE),
            exposureCompensation = prop(CameraSetting.EXPOSURE_COMPENSATION)
        )
    }

    fun setCameraSettingValue(
        setting: CameraSetting,
        rawValue: Long
    ): CameraSettingAdjustmentResult {
        val snapshot = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            2_000
        )
        val descriptor = if (snapshot.isSuccess) {
            findGenericSettingDescriptor(snapshot.data, setting)
        } else null
        val before = readCameraSettingsState()
        if (descriptor == null) {
            return CameraSettingAdjustmentResult(before, false, "${cameraSettingName(setting)} is unavailable")
        }

        val response = setGenericSettingRaw(descriptor, rawValue)
        Thread.sleep(180)
        var after = readCameraSettingsState()
        if (after.property(setting).current?.rawValue != rawValue) {
            Thread.sleep(260)
            after = readCameraSettingsState()
        }
        val applied = after.property(setting).current?.rawValue == rawValue
        return if (applied) {
            CameraSettingAdjustmentResult(after, true)
        } else {
            CameraSettingAdjustmentResult(
                after,
                false,
                if (response.isSuccess) "Camera did not apply ${formatCameraSettingValue(setting, rawValue)}"
                else "Camera rejected ${cameraSettingName(setting)} (${PtpConstants.responseCodeName(response.responseCode)})"
            )
        }
    }

    private fun findGenericSettingDescriptor(
        data: ByteArray,
        setting: CameraSetting
    ): GenericSettingDescriptor? {
        val candidates: IntArray
        val dataType: Int
        when (setting) {
            CameraSetting.FOCUS_MODE -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_FOCUS_MODE, PtpConstants.PROP_SONY_FOCUS_MODE)
                dataType = 0x0004
            }
            CameraSetting.FOCUS_AREA -> {
                candidates = intArrayOf(PtpConstants.PROP_SONY_FOCUS_AREA)
                dataType = 0x0004
            }
            CameraSetting.WHITE_BALANCE -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_WHITE_BALANCE)
                dataType = 0x0004
            }
            CameraSetting.METERING_MODE -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_EXPOSURE_METERING_MODE)
                dataType = 0x0004
            }
            CameraSetting.DRIVE_MODE -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_STILL_CAPTURE_MODE)
                dataType = 0x0004
            }
            CameraSetting.EXPOSURE_COMPENSATION -> {
                candidates = intArrayOf(PtpConstants.PROP_PTP_EXPOSURE_BIAS_COMPENSATION)
                dataType = 0x0003 // INT16; keep the raw 16-bit value for writes.
            }
        }

        val size = scalarSize(dataType)
        if (data.size < 5 + size * 2) return null
        for (propertyCode in candidates) {
            for (base in 0 until data.size - 4) {
                if (u16(data, base) != propertyCode || u16(data, base + 2) != dataType) continue
                val getSet = data.getOrNull(base + 4)?.toInt()?.and(0xFF) ?: 0

                data class Parsed(val current: Long?, val values: List<Long>, val score: Int)
                val parsed = mutableListOf<Parsed>()
                for (sonyExtraFlag in listOf(true, false)) {
                    val currentOffset = base + 5 + size + if (sonyExtraFlag) 1 else 0
                    if (currentOffset + size > data.size) continue
                    val current = readUnsignedScalar(data, currentOffset, size)
                    val formOffset = currentOffset + size
                    val formFlag = data.getOrNull(formOffset)?.toInt()?.and(0xFF) ?: continue
                    if (formFlag !in 0..2) continue
                    var values = emptyList<Long>()
                    var valid = true
                    when (formFlag) {
                        1 -> {
                            // Range properties are uncommon for these controls; the current
                            // value is still useful even when we do not synthesize each step.
                            if (formOffset + 1 + size * 3 > data.size) valid = false
                        }
                        2 -> {
                            val countOffset = formOffset + 1
                            if (countOffset + 2 > data.size) valid = false
                            else {
                                val count = u16(data, countOffset)
                                val valuesOffset = countOffset + 2
                                if (count !in 1..512 || valuesOffset + count * size > data.size) {
                                    valid = false
                                } else {
                                    values = List(count) { index ->
                                        readUnsignedScalar(data, valuesOffset + index * size, size) ?: 0L
                                    }
                                }
                            }
                        }
                    }
                    if (!valid) continue
                    var score = 2
                    if (sonyExtraFlag) score += 2
                    if (values.size > 1) score += 4
                    if (current != null && (values.isEmpty() || current in values)) score += 3
                    parsed += Parsed(current, values, score)
                }
                val best = parsed.maxByOrNull { it.score } ?: continue
                // Sony has historically marked several remotely-settable controls as
                // readonly. A real enum plus one of our known camera-control ids is
                // enough to offer the selector; failed writes are verified and reported.
                val writable = getSet != 0 || best.values.size > 1
                return GenericSettingDescriptor(
                    setting = setting,
                    propertyCode = propertyCode,
                    dataType = dataType,
                    currentValue = best.current,
                    enumValues = best.values,
                    writable = writable
                )
            }
        }
        return null
    }

    private fun setGenericSettingRaw(
        descriptor: GenericSettingDescriptor,
        value: Long
    ): PtpResponse {
        val size = scalarSize(descriptor.dataType)
        val payload = ByteBuffer.allocate(size).order(ByteOrder.LITTLE_ENDIAN).apply {
            when (size) {
                1 -> put((value and 0xFF).toByte())
                2 -> putShort((value and 0xFFFF).toShort())
                4 -> putInt((value and 0xFFFFFFFFL).toInt())
            }
        }.array()
        val sony = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_A,
            payload,
            descriptor.propertyCode
        )
        if (sony.isSuccess) return sony
        if (descriptor.propertyCode in 0x5000..0x5FFF) {
            return transport.sendCommandWithDataOut(
                PtpConstants.OP_SET_DEVICE_PROP_VALUE,
                payload,
                descriptor.propertyCode
            )
        }
        return sony
    }

    private fun fallbackCameraSettingValues(setting: CameraSetting): List<Long> = when (setting) {
        CameraSetting.FOCUS_MODE -> listOf(1L, 2L, 0x8004L, 0x8005L, 0x8006L)
        CameraSetting.WHITE_BALANCE -> listOf(
            2L, 4L, 0x8011L, 0x8010L, 6L,
            0x8001L, 0x8002L, 0x8003L, 0x8004L,
            7L, 0x8030L, 0x8012L, 0x8020L, 0x8021L, 0x8022L
        )
        CameraSetting.METERING_MODE -> listOf(4L, 0x8001L, 0x8002L, 0x8004L, 0x8005L, 0x8003L, 0x8006L)
        CameraSetting.DRIVE_MODE -> listOf(1L, 2L, 0x8015L, 0x8012L, 0x8010L, 0x800DL, 0x800BL, 0x800CL)
        // Focus-area raw codes vary by Sony generation, so never guess them.
        CameraSetting.FOCUS_AREA -> emptyList()
        // EV is normally returned as an enum by Sony; avoid offering unsupported steps.
        CameraSetting.EXPOSURE_COMPENSATION -> emptyList()
    }

    private fun formatCameraSettingValue(setting: CameraSetting, raw: Long): String = when (setting) {
        CameraSetting.FOCUS_MODE -> when (raw and 0xFFFF) {
            1L -> "MF"
            2L -> "AF-S"
            0x8004L -> "AF-C"
            0x8005L -> "AF-A"
            0x8006L -> "DMF"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
        CameraSetting.FOCUS_AREA -> when (raw and 0xFFFF) {
            1L -> "REGIST"; 2L -> "WIDE"; 3L -> "ZONE"; 4L -> "CENTER"
            5L -> "SPOT S"; 6L -> "SPOT M"; 7L -> "SPOT L"; 8L -> "EXPAND"
            9L -> "TRACK ALL"; 10L -> "TRACK SEL"; 11L -> "TRACK AREA"
            12L -> "TRACK S"; 13L -> "TRACK M"; 14L -> "TRACK L"; 15L -> "TRACK SUBJ"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
        CameraSetting.WHITE_BALANCE -> when (raw and 0xFFFF) {
            2L -> "AWB"
            4L -> "DAYLIGHT"
            0x8011L -> "SHADE"
            0x8010L -> "CLOUDY"
            6L -> "TUNGSTEN"
            0x8001L -> "FL WARM"
            0x8002L -> "FL COOL"
            0x8003L -> "FL DAY W"
            0x8004L -> "FL DAY"
            7L -> "FLASH"
            0x8030L -> "UNDERWATER"
            0x8012L -> "KELVIN"
            0x8020L -> "CUSTOM 1"
            0x8021L -> "CUSTOM 2"
            0x8022L -> "CUSTOM 3"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
        CameraSetting.METERING_MODE -> when (raw and 0xFFFF) {
            4L -> "CENTER SPOT"
            0x8001L -> "MULTI"
            0x8002L -> "CENTER"
            0x8004L -> "SPOT STD"
            0x8005L -> "SPOT LARGE"
            0x8003L -> "ENTIRE AVG"
            0x8006L -> "HIGHLIGHT"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
        CameraSetting.DRIVE_MODE -> when (raw and 0xFFFF) {
            1L -> "SINGLE"
            2L -> "CONT. HIGH"
            0x8015L -> "CONT. MID"
            0x8012L -> "CONT. LOW"
            0x8010L -> "CONT. HI+"
            0x800DL -> "TIMER 2s"
            0x800BL -> "TIMER 5s"
            0x800CL -> "TIMER 10s"
            else -> "0x%04X".format(raw and 0xFFFF)
        }
        CameraSetting.EXPOSURE_COMPENSATION -> {
            val signed = (raw and 0xFFFF).toInt().toShort().toInt()
            val ev = signed / 1000.0
            when {
                signed == 0 -> "±0.0"
                signed > 0 -> "+%.1f".format(ev)
                else -> "%.1f".format(ev)
            }
        }
    }

    private fun cameraSettingName(setting: CameraSetting): String = when (setting) {
        CameraSetting.FOCUS_MODE -> "focus mode"
        CameraSetting.FOCUS_AREA -> "focus area"
        CameraSetting.WHITE_BALANCE -> "white balance"
        CameraSetting.METERING_MODE -> "metering mode"
        CameraSetting.DRIVE_MODE -> "drive mode"
        CameraSetting.EXPOSURE_COMPENSATION -> "exposure compensation"
    }

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
     * Wait for Sony's RAM photo-transfer flag (D215 high byte 0x80) to clear.
     * libgphoto2 observes the value transition from e.g. 0x8001 to 0x0001
     * after the 0xFFFFC001 object has been consumed. Restarting liveview before
     * that transition can make the camera reject 0xFFFFC002 for several seconds.
     */
    fun waitForCaptureIdle(maxWaitMs: Long = 3_500L): Boolean {
        val started = System.currentTimeMillis()
        var clearSamples = 0
        var last: PhotoQueueStatus? = null

        while (System.currentTimeMillis() - started < maxWaitMs) {
            last = getPhotoTransferQueueStatus()
            if (last != null && !last.photoAvailable) {
                clearSamples++
                // Require two reads so we do not race a transient property update.
                if (clearSamples >= 2) {
                    Log.d(TAG, "Capture queue idle: raw=0x${last.rawValue.toString(16)} count=${last.queuedCount}")
                    return true
                }
            } else {
                clearSamples = 0
            }
            Thread.sleep(120)
        }

        val lastLabel = last?.rawValue?.let { value -> "0x${value.toString(16)}" } ?: "n/a"
        Log.w(TAG, "Capture queue did not become idle within ${maxWaitMs}ms (last=$lastLabel)")
        return false
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
