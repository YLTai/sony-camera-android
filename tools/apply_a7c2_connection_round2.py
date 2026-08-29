from pathlib import Path

USB = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt")
PTP = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if old not in text:
        raise RuntimeError(f"Expected block not found in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1))


def replace_all(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count == 0:
        raise RuntimeError(f"Expected text not found in {path}: {old!r}")
    path.write_text(text.replace(old, new))
    print(f"replaced {count} occurrences in {path}")


# Keep live-view as the highest-priority PTP traffic. UI-originated setters already
# emit their updated state immediately, so aggressive polling is unnecessary and
# was starving GetObject on the transport's single transaction lock.
replace_once(
    USB,
    """        private const val EXPOSURE_POLL_INTERVAL_MS = 120L\n        private const val SETTINGS_POLL_INTERVAL_MS = 250L\n""",
    """        private const val EXPOSURE_POLL_INTERVAL_MS = 900L\n        private const val SETTINGS_POLL_INTERVAL_MS = 3_000L\n        private const val TELEMETRY_WARMUP_MS = 2_000L\n        private const val FIRST_LIVEVIEW_PROBE_MS = 4_000L\n""",
)

replace_once(
    USB,
    """            var lastExposurePollTime = 0L\n            var lastSettingsPollTime = 0L\n""",
    """            var lastExposurePollTime = System.currentTimeMillis()\n            var lastSettingsPollTime = lastExposurePollTime\n""",
)

replace_once(
    USB,
    """                        // Exposure values change relatively slowly. One Sony 0x9209 snapshot\n                        // supplies aperture, shutter and ISO without shrinking liveview throughput\n                        // with three independent property round trips.\n                        val exposurePollNow = System.currentTimeMillis()\n                        if (exposurePollNow - lastExposurePollTime >= EXPOSURE_POLL_INTERVAL_MS) {\n                            lastExposurePollTime = exposurePollNow\n                            ptpCamera?.readExposureState()?.let { exposure ->\n                                _events.emit(CameraEvent.ExposureUpdated(exposure))\n                            }\n                        }\n\n                        if (exposurePollNow - lastSettingsPollTime >= SETTINGS_POLL_INTERVAL_MS) {\n                            lastSettingsPollTime = exposurePollNow\n                            ptpCamera?.readCameraSettingsState()?.let { settings ->\n                                _events.emit(CameraEvent.CameraSettingsUpdated(settings))\n                            }\n                        }\n""",
    """                        // The Sony USB transport is strictly serial. Do not perform property\n                        // snapshots immediately after the first frame, and never stack exposure +\n                        // settings reads in the same frame iteration. App-originated writes already\n                        // publish their result immediately; these polls are only for camera-side dials.\n                        val telemetryNow = System.currentTimeMillis()\n                        if (telemetryNow - liveviewStartTime >= TELEMETRY_WARMUP_MS) {\n                            if (telemetryNow - lastSettingsPollTime >= SETTINGS_POLL_INTERVAL_MS) {\n                                lastSettingsPollTime = telemetryNow\n                                ptpCamera?.readCameraSettingsState()?.let { settings ->\n                                    _events.emit(CameraEvent.CameraSettingsUpdated(settings))\n                                }\n                            } else if (telemetryNow - lastExposurePollTime >= EXPOSURE_POLL_INTERVAL_MS) {\n                                lastExposurePollTime = telemetryNow\n                                ptpCamera?.readExposureState()?.let { exposure ->\n                                    _events.emit(CameraEvent.ExposureUpdated(exposure))\n                                }\n                            }\n                        }\n""",
)

# Sony's a7C II documentation names Remote Shoot (PC Remote), and their Android
# USB-PC-Remote guidance uses USB LUN Single. Make failures actionable.
replace_all(
    USB,
    "Camera USB mode is wrong. In the camera menu, set USB Connection to 'PC Remote' or 'Auto'.",
    "Camera USB mode is wrong. On a7C II set USB Connection Mode to 'Remote Shoot (PC Remote)' and USB LUN to 'Single'.",
)

# Do not announce Ready until the actual PTP3 live-view object can be fetched.
# If vendor init or first-frame probing fails, explicitly release Sony priority,
# close the stale remote session, then perform one clean full-session recycle.
replace_once(
    USB,
    """            // Mandatory Sony vendor handshake. Never publish Ready after only\n            // part of SDIOConnect succeeded — that produces the long, doomed\n            // liveview wait users were seeing.\n            if (!localCamera.initSonyExtension()) {\n                _connectionState.value = CameraConnectionState.Error(\n                    \"Sony PC Remote initialization failed. Disconnect once and try again.\"\n                )\n                return@withContext\n            }\n\n            // Commit: take ownership of the resources.\n            usbDevice = device\n            usbConnection = localConn\n            ptpInterface = localIface\n            ptpCamera = localCamera\n            committed = true\n\n            _cameraName.value = localCamera.deviceName ?: \"Sony Camera (USB)\"\n            _connectionState.value = CameraConnectionState.Ready\n\n            // Discover exposure controls before liveview begins, then seed the\n            // monitor top bar with the exact current values and choices.\n            _events.emit(CameraEvent.ExposureUpdated(localCamera.readExposureState(forceDescriptorProbe = true)))\n\n            Log.d(TAG, \"USB camera connected: ${localCamera.deviceName}\")\n\n            // Do not touch the shutter during connection. A half-press here\n            // adds another vendor-control transaction exactly when the camera has\n            // just entered PC Remote and can destabilize first-liveview startup.\n\n            // Auto-start liveview — camera is already in PC Remote mode\n            // Auto-start liveview — camera is already in PC Remote mode\n            // with liveview active after SDIO init\n            Log.d(TAG, \"Auto-starting USB liveview...\")\n            startLiveview()\n""",
    """            // The vendor handshake alone is not enough to declare success on a7C II.\n            // A stale remote owner can accept SDIO commands yet deny 0xFFFFC002 forever.\n            // Verify the real live-view object first. If that fails, reproduce the useful\n            // part of a clean Monitor+ open/close cycle: release priority, CloseSession,\n            // then reopen and run the complete protocol-3 handshake exactly once.\n            var remoteReady = localCamera.initSonyExtension() && probeLiveViewReady(localCamera)\n            if (!remoteReady) {\n                Log.w(TAG, \"Sony remote session not stream-ready — recycling PC Remote session once\")\n                runCatching { localCamera.endSession() }\n                    .onFailure { Log.w(TAG, \"Remote-session release failed: ${it.message}\") }\n                runCatching { localCamera.flushAndResetPipe() }\n                delay(350)\n\n                localCamera = SonyPtpCamera(transport)\n                var reopened = localCamera.openSession()\n                if (!reopened) {\n                    Log.w(TAG, \"Recycled OpenSession failed — using one class-reset recovery\")\n                    transport.recoverAfterFailedOpenSession(localIface.id)\n                    localCamera = SonyPtpCamera(transport)\n                    reopened = localCamera.openSession()\n                }\n\n                if (reopened) {\n                    if (!localCamera.getDeviceInfo()) {\n                        Log.w(TAG, \"Could not refresh device info after remote-session recycle\")\n                    }\n                    remoteReady = localCamera.initSonyExtension() && probeLiveViewReady(localCamera)\n                }\n            }\n\n            if (!remoteReady) {\n                _connectionState.value = CameraConnectionState.Error(\n                    \"a7C II PC Remote session did not become stream-ready. Set USB Connection Mode to PC Remote, USB LUN to Single, and Network > PC Remote Function > PC Remote to Off, then reconnect.\"\n                )\n                return@withContext\n            }\n\n            // Commit only after a real live-view dataset has been observed. Property\n            // discovery is intentionally deferred until the stream has warmed up.\n            usbDevice = device\n            usbConnection = localConn\n            ptpInterface = localIface\n            ptpCamera = localCamera\n            committed = true\n\n            _cameraName.value = localCamera.deviceName ?: \"Sony a7C II (USB)\"\n            Log.d(TAG, \"USB camera stream-ready: ${localCamera.deviceName}\")\n            _connectionState.value = CameraConnectionState.Ready\n\n            Log.d(TAG, \"Auto-starting verified USB liveview...\")\n            startLiveview()\n""",
)

# Replace enumeration-order-dependent interface selection with endpoint-aware
# selection. On multi-function/LUN configurations the first class-6 interface is
# not guaranteed to be the PTP control interface we want.
replace_once(
    USB,
    """    private fun hasPtpInterface(device: UsbDevice): Boolean {\n        for (i in 0 until device.interfaceCount) {\n            val iface = device.getInterface(i)\n            // PTP/MTP uses class 6 (Still Image), but some devices use class 255 (vendor-specific)\n            if (iface.interfaceClass == PtpConstants.USB_CLASS_PTP ||\n                iface.interfaceClass == 255) return true\n        }\n        return false\n    }\n\n    private fun findPtpInterface(device: UsbDevice): UsbInterface? {\n        // Prefer class 6 (standard PTP/MTP)\n        for (i in 0 until device.interfaceCount) {\n            val iface = device.getInterface(i)\n            if (iface.interfaceClass == PtpConstants.USB_CLASS_PTP) return iface\n        }\n        // Fallback: first interface with bulk endpoints (vendor-specific PTP)\n        for (i in 0 until device.interfaceCount) {\n            val iface = device.getInterface(i)\n            var hasBulkIn = false\n            var hasBulkOut = false\n            for (e in 0 until iface.endpointCount) {\n                val ep = iface.getEndpoint(e)\n                if (ep.type == UsbConstants.USB_ENDPOINT_XFER_BULK) {\n                    if (ep.direction == UsbConstants.USB_DIR_IN) hasBulkIn = true\n                    if (ep.direction == UsbConstants.USB_DIR_OUT) hasBulkOut = true\n                }\n            }\n            if (hasBulkIn && hasBulkOut) return iface\n        }\n        return null\n    }\n""",
    """    private fun hasPtpInterface(device: UsbDevice): Boolean = findPtpInterface(device) != null\n\n    private fun interfaceHasBulkPair(iface: UsbInterface): Boolean {\n        var bulkIn = false\n        var bulkOut = false\n        for (e in 0 until iface.endpointCount) {\n            val ep = iface.getEndpoint(e)\n            if (ep.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue\n            if (ep.direction == UsbConstants.USB_DIR_IN) bulkIn = true\n            if (ep.direction == UsbConstants.USB_DIR_OUT) bulkOut = true\n        }\n        return bulkIn && bulkOut\n    }\n\n    private fun interfaceHasInterruptIn(iface: UsbInterface): Boolean {\n        for (e in 0 until iface.endpointCount) {\n            val ep = iface.getEndpoint(e)\n            if (ep.type == UsbConstants.USB_ENDPOINT_XFER_INT &&\n                ep.direction == UsbConstants.USB_DIR_IN) return true\n        }\n        return false\n    }\n\n    private fun findPtpInterface(device: UsbDevice): UsbInterface? {\n        val interfaces = (0 until device.interfaceCount).map { device.getInterface(it) }\n\n        // A real PTP control interface needs both bulk directions. Prefer the one\n        // that also exposes the interrupt event endpoint; this avoids depending on\n        // Android's interface enumeration order when the camera exposes multiple LUNs.\n        return interfaces.firstOrNull {\n            it.interfaceClass == PtpConstants.USB_CLASS_PTP &&\n                interfaceHasBulkPair(it) && interfaceHasInterruptIn(it)\n        } ?: interfaces.firstOrNull {\n            it.interfaceClass == PtpConstants.USB_CLASS_PTP && interfaceHasBulkPair(it)\n        } ?: interfaces.firstOrNull {\n            it.interfaceClass == 255 && interfaceHasBulkPair(it)\n        }\n    }\n""",
)

# Add first-frame verification immediately before the USB interface helpers.
replace_once(
    USB,
    """    private fun hasPtpInterface(device: UsbDevice): Boolean = findPtpInterface(device) != null\n""",
    """    private suspend fun probeLiveViewReady(camera: SonyPtpCamera): Boolean {\n        val started = System.currentTimeMillis()\n        var pipeCleared = false\n        var attempts = 0\n        while (System.currentTimeMillis() - started < FIRST_LIVEVIEW_PROBE_MS) {\n            attempts++\n            val frame = camera.getLiveViewFrameData()\n            if (frame?.jpeg?.isNotEmpty() == true) {\n                Log.d(TAG, \"First liveview verified after $attempts attempts / ${System.currentTimeMillis() - started}ms\")\n                return true\n            }\n            if (!pipeCleared && System.currentTimeMillis() - started >= 1_200L) {\n                Log.w(TAG, \"First liveview still denied — clearing endpoints once\")\n                camera.flushAndResetPipe()\n                pipeCleared = true\n            }\n            delay(80)\n        }\n        Log.w(TAG, \"First liveview verification failed after $attempts attempts\")\n        return false\n    }\n\n    private fun hasPtpInterface(device: UsbDevice): Boolean = findPtpInterface(device) != null\n""",
)

# For a7C II there is no useful protocol-2 fallback: if the protocol-3 device-info
# request fails, recycling the remote session is safer than continuing half-initialized.
replace_once(
    PTP,
    """        val useProtocol3 = extV3?.isSuccess == true && extV3.dataSize > 0\n        val extInfo = if (useProtocol3) {\n            extV3!!\n        } else {\n            transport.sendCommandWithDataShortTimeout(\n                PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO,\n                2500,\n                0x00C8\n            )\n        }\n""",
    """        val useProtocol3 = extV3?.isSuccess == true && extV3.dataSize > 0\n        if (preferProtocol3 && !useProtocol3) {\n            Log.e(TAG, \"a7C II protocol-3 device-info request failed; refusing protocol-2 fallback\")\n            return false\n        }\n        val extInfo = if (useProtocol3) {\n            extV3!!\n        } else {\n            transport.sendCommandWithDataShortTimeout(\n                PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO,\n                2500,\n                0x00C8\n            )\n        }\n""",
)

# SessionAlreadyOpen means a previous remote owner/session survived. Release Sony
# priority as well as the standard PTP session before reopening.
replace_once(
    PTP,
    """        // SessionAlreadyOpen: try the standard CloseSession once, then reopen.\n        // If that does not work, the manager will perform one class Device Reset.\n        if (response.responseCode == 0x201E) {\n            Log.w(TAG, \"PTP session already open — closing stale session once\")\n            closeSession()\n            Thread.sleep(120)\n""",
    """        // SessionAlreadyOpen usually means a previous PC-Remote owner survived.\n        // Release Sony priority too; a plain CloseSession can leave the remote\n        // ownership state that makes the next host look connected but deny liveview.\n        if (response.responseCode == 0x201E) {\n            Log.w(TAG, \"PTP session already open — releasing stale Sony remote ownership\")\n            endSession()\n            Thread.sleep(250)\n""",
)

# Remove the nonessential 0x9209 diagnostic from the sensitive startup handshake.
# Acquire priority immediately after protocol-3 phase 3, then allow the a7C II a
# short settle before the manager probes the live-view object.
replace_once(
    PTP,
    """        // Property snapshot is useful for diagnostics but is not required to\n        // decide whether the transport-level Sony handshake succeeded.\n        val props = transport.sendCommandWithDataShortTimeout(\n            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA, 2500\n        )\n        Log.d(TAG, \"GetAllDevicePropData: ${PtpConstants.responseCodeName(props.responseCode)}, ${props.dataSize}B\")\n\n        sonyExtensionDebug = buildString {\n            append(\"ext=\").append(selectedProtocol)\n            if (preferProtocol3) {\n                append(\" v3=\")\n                append(PtpConstants.responseCodeName(extV3?.responseCode ?: 0))\n                append(\"/\").append(extV3?.dataSize ?: 0).append(\"B\")\n                append(\" sdio3=\")\n                append(PtpConstants.responseCodeName(r3?.responseCode ?: 0))\n            }\n            append(\" extInfo=\")\n            append(PtpConstants.responseCodeName(extInfo.responseCode))\n            append(\"/\").append(extInfo.dataSize).append(\"B\")\n            append(\" init9209=\")\n            append(PtpConstants.responseCodeName(props.responseCode))\n            append(\"/\").append(props.dataSize).append(\"B\")\n        }\n\n        // Tell camera that USB host has control. Some Sony commands acknowledge\n        // slowly/silently, so keep this best-effort after the mandatory SDIO\n        // stages rather than treating a quick-response timeout as disconnect.\n        setControlDeviceA(PtpConstants.PROP_SONY_PRIORITY_MODE, 1)\n        return true\n""",
    """        sonyExtensionDebug = buildString {\n            append(\"ext=\").append(selectedProtocol)\n            if (preferProtocol3) {\n                append(\" v3=\")\n                append(PtpConstants.responseCodeName(extV3?.responseCode ?: 0))\n                append(\"/\").append(extV3?.dataSize ?: 0).append(\"B\")\n                append(\" sdio3=\")\n                append(PtpConstants.responseCodeName(r3?.responseCode ?: 0))\n            }\n            append(\" extInfo=\")\n            append(PtpConstants.responseCodeName(extInfo.responseCode))\n            append(\"/\").append(extInfo.dataSize).append(\"B\")\n        }\n\n        // Acquire host control immediately after phase 3. The response can be late\n        // on Sony bodies, so the manager uses a successful live-view fetch as the\n        // authoritative readiness check instead of trusting this ACK alone.\n        val priority = setControlDeviceA(PtpConstants.PROP_SONY_PRIORITY_MODE, 1)\n        Log.d(TAG, \"PriorityMode=1: ${PtpConstants.responseCodeName(priority.responseCode)}\")\n        if (preferProtocol3) Thread.sleep(250)\n        return true\n""",
)

# This patch is intentionally one-shot; the workflow commits the source changes
# and the deletion together so no patcher remains in main.
Path(__file__).unlink()
print("Applied a7C II connection/liveview reliability round 2")
