from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


sony_path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt")
sony = sony_path.read_text()

sony = replace_once(
    sony,
    '''    // ILCE-7CM2 is a protocol-3 body supported by Sony Camera Remote SDK's
    // Remote Touch Operation. Keep this capability session-scoped so an
    // explicit Unsupported response can permanently fall back to legacy D2DC.
    @Volatile
    private var remoteTouchSupported = false
''',
    '''    // Remote Touch is only usable when the CAMERA reports D284=Enable.
    // Sony's SDK explicitly requires that state; model support alone is not
    // sufficient. FunctionOfRemoteTouchOperation (E083) is prepared as Spot AF.
    @Volatile
    private var remoteTouchSupported = false
    @Volatile
    private var monitorAfPrepared = false
    @Volatile
    private var monitorAfDebugState = "AF path not prepared"
''',
    "remote touch state fields"
)

sony = replace_once(
    sony,
    '''        // Sony's SDK exposes RemoteTouchOperation (wire control D2E4) on the
        // a7C II generation. It is the official one-shot "touch the monitor"
        // action; do not emulate that action as D2DC followed by a shutter S1.
        // The PTP3 capability blob differs between generations, so the known
        // ILCE-7CM2 model is the authority here rather than a brittle byte scan.
        remoteTouchSupported = preferProtocol3
        if (remoteTouchSupported) {
            Log.d(TAG, "Remote Touch Operation enabled for ILCE-7CM2 (D2E4/9207)")
        }

''',
    '''        // Do not infer Remote Touch readiness from the model name. Sony's SDK
        // requires RemoteTouchOperationEnableStatus (D284) to be Enable, and the
        // desired FunctionOfRemoteTouchOperation (E083) must be selected first.
        remoteTouchSupported = false
        monitorAfPrepared = false
        monitorAfDebugState = "AF path awaiting camera property state"

''',
    "remove model-only remote touch assumption"
)

sony = replace_once(
    sony,
    '''        val priority = setControlDeviceA(PtpConstants.PROP_SONY_PRIORITY_MODE, 1)
        Log.d(TAG, "PriorityMode=1: ${PtpConstants.responseCodeName(priority.responseCode)}")
        if (preferProtocol3) Thread.sleep(250)
        return true
''',
    '''        val priority = setControlDeviceA(PtpConstants.PROP_SONY_PRIORITY_MODE, 1)
        Log.d(TAG, "PriorityMode=1: ${PtpConstants.responseCodeName(priority.responseCode)}")
        if (preferProtocol3) {
            Thread.sleep(250)
            // Prepare the exact Sony monitor-touch state before Live View starts so
            // the first user tap does not pay a property-read / mode-switch penalty.
            // Failure is non-fatal: prepareMonitorTapAf() records an AF-Area fallback.
            val afPrep = prepareMonitorTapAf()
            Log.d(TAG, "Monitor AF preparation: $afPrep")
        }
        return true
''',
    "prepare monitor AF after priority"
)

insert_marker = '''    /** Whether this session should use Sony's official Remote Touch action. */
    fun supportsRemoteTouch(): Boolean = remoteTouchSupported
'''
helper_block = '''    private data class SonyScalarEnumProperty(
        val propertyCode: Int,
        val dataType: Int,
        val currentValue: Long?,
        val enumValues: List<Long>,
        val writable: Boolean,
        val enabledState: Int
    )

    /**
     * Parse one scalar Sony 0x9209 property using the protocol-3 layout:
     * code/u16, type/u16, getSet/u8, enabled/u8, default, current, form...
     * This is the layout used by Sony Camera Remote Command for the a7C II.
     */
    private fun findSonyScalarEnumProperty(
        data: ByteArray,
        propertyCode: Int,
        dataType: Int
    ): SonyScalarEnumProperty? {
        val size = scalarSize(dataType)
        if (size == 0 || data.size < 8 + 6 + size * 2 + 1) return null
        for (base in 8 until data.size - (6 + size * 2)) {
            if (u16(data, base) != propertyCode || u16(data, base + 2) != dataType) continue
            val getSet = data.getOrNull(base + 4)?.toInt()?.and(0xFF) ?: continue
            val enabled = data.getOrNull(base + 5)?.toInt()?.and(0xFF) ?: continue
            if (enabled !in 0..2) continue

            val currentOffset = base + 6 + size
            val current = readUnsignedScalar(data, currentOffset, size) ?: continue
            val formOffset = currentOffset + size
            val form = data.getOrNull(formOffset)?.toInt()?.and(0xFF) ?: continue
            if (form !in 0..2) continue

            var values = emptyList<Long>()
            when (form) {
                1 -> if (formOffset + 1 + size * 3 > data.size) continue
                2 -> {
                    val countOffset = formOffset + 1
                    if (countOffset + 2 > data.size) continue
                    val count = u16(data, countOffset)
                    val valuesOffset = countOffset + 2
                    if (count !in 1..128 || valuesOffset + count * size > data.size) continue
                    values = List(count) { index ->
                        readUnsignedScalar(data, valuesOffset + index * size, size) ?: 0L
                    }
                }
            }

            // Sony's 0x9209 enabled byte is authoritative for stored settings;
            // 0x80 getSet entries are momentary controls and remain actionable.
            val writable = (getSet and 0x80) != 0 || enabled == 1
            return SonyScalarEnumProperty(
                propertyCode = propertyCode,
                dataType = dataType,
                currentValue = current,
                enumValues = values,
                writable = writable,
                enabledState = enabled
            )
        }
        return null
    }

    private fun setSonyScalarProperty(
        descriptor: SonyScalarEnumProperty,
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
        return transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_A,
            payload,
            descriptor.propertyCode
        )
    }

    /**
     * Prepare the monitor-tap AF path from the camera's actual property state.
     *
     * Preferred: Sony Remote Touch D2E4 with D284=Enable and E083=Spot AF.
     * Fallback: Sony sample-app AF Area Position semantics — preselect Spot S
     * (Flexible Spot S) and move D2DC, then the manager triggers S1 separately.
     */
    @Synchronized
    fun prepareMonitorTapAf(): String {
        if (monitorAfPrepared) return monitorAfDebugState
        if (deviceName?.contains("ILCE-7CM2", ignoreCase = true) != true) {
            remoteTouchSupported = false
            monitorAfPrepared = true
            monitorAfDebugState = "AF AREA fallback (non-7CM2)"
            return monitorAfDebugState
        }

        var snapshot = transport.sendCommandWithDataShortTimeout(
            PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
            700
        )
        if (!snapshot.isSuccess || snapshot.data.isEmpty()) {
            remoteTouchSupported = false
            monitorAfDebugState = "AF prep 9209=${PtpConstants.responseCodeName(snapshot.responseCode)}"
            return monitorAfDebugState
        }

        var data = snapshot.data
        var enable = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS, 0x0002
        )
        var function = findSonyScalarEnumProperty(
            data, PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION, 0x0002
        )
        var functionWrite: PtpResponse? = null

        // Sony SDK: FunctionOfRemoteTouchOperation 2 == Spot AF.
        if (function != null && function.currentValue != 2L && function.writable &&
            (function.enumValues.isEmpty() || 2L in function.enumValues)
        ) {
            functionWrite = setSonyScalarProperty(function, 2L)
            if (functionWrite.isSuccess) {
                val verify = transport.sendCommandWithDataShortTimeout(
                    PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
                    500
                )
                if (verify.isSuccess && verify.data.isNotEmpty()) {
                    data = verify.data
                    enable = findSonyScalarEnumProperty(
                        data, PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS, 0x0002
                    )
                    function = findSonyScalarEnumProperty(
                        data, PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION, 0x0002
                    )
                }
            }
        }

        val enableRaw = enable?.currentValue
        val functionRaw = function?.currentValue
        remoteTouchSupported = enableRaw == 1L && functionRaw == 2L
        if (remoteTouchSupported) {
            monitorAfPrepared = true
            monitorAfDebugState = "RT SPOT ready en=1 func=2"
            return monitorAfDebugState
        }

        // Official RemoteSampleApp's AF Area Position path automatically selects
        // Flexible Spot S before accepting an x/y coordinate. For ILCE-7CM2 the
        // camera-reported focus-area table uses raw 5 for Spot S.
        var focusArea = findGenericSettingDescriptor(data, CameraSetting.FOCUS_AREA)
        var spotWrite: PtpResponse? = null
        if (focusArea != null && focusArea.currentValue != 5L) {
            spotWrite = setGenericSettingRaw(focusArea, 5L)
            if (spotWrite.isSuccess) {
                val verify = transport.sendCommandWithDataShortTimeout(
                    PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA,
                    500
                )
                if (verify.isSuccess && verify.data.isNotEmpty()) {
                    data = verify.data
                    focusArea = findGenericSettingDescriptor(data, CameraSetting.FOCUS_AREA)
                }
            }
        }

        val spotReady = focusArea?.currentValue == 5L || spotWrite?.isSuccess == true
        monitorAfPrepared = true
        monitorAfDebugState = buildString {
            append("AF AREA SpotS fallback")
            append(" rtEn=").append(enableRaw ?: -1)
            append(" func=").append(functionRaw ?: -1)
            if (functionWrite != null) {
                append(" fset=").append(PtpConstants.responseCodeName(functionWrite.responseCode))
            }
            append(" spot=").append(if (spotReady) 1 else 0)
            if (spotWrite != null) {
                append(" sset=").append(PtpConstants.responseCodeName(spotWrite.responseCode))
            }
        }
        return monitorAfDebugState
    }

    fun monitorAfDebug(): String = monitorAfDebugState

    /** Whether this session should use Sony's official Remote Touch action. */
    fun supportsRemoteTouch(): Boolean = remoteTouchSupported
'''
sony = replace_once(sony, insert_marker, helper_block, "insert remote touch preparation")

sony = replace_once(
    sony,
    '''        val result = transport.sendCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B,
            data,
            PtpConstants.PROP_SONY_AF_AREA_POSITION
        )
''',
    '''        val result = transport.sendHighPriorityCommandWithDataOut(
            PtpConstants.OP_SONY_SET_CONTROL_DEVICE_B,
            data,
            PtpConstants.PROP_SONY_AF_AREA_POSITION
        )
''',
    "prioritize D2DC"
)

sony = replace_once(
    sony,
    '''            append(PtpConstants.responseCodeName(setResult.responseCode))
        }
''',
    '''            append(PtpConstants.responseCodeName(setResult.responseCode))
            append(" bus=").append(setResult.queueWaitMs).append("ms")
        }
''',
    "D2DC bus timing"
)

sony = replace_once(
    sony,
    '''        return if (applied) {
            CameraSettingAdjustmentResult(after, true)
''',
    '''        return if (applied) {
            if (setting == CameraSetting.FOCUS_AREA) {
                // A manual focus-area change invalidates the cached monitor-tap
                // preparation. The next tap will restore Sony's required mode.
                monitorAfPrepared = false
            }
            CameraSettingAdjustmentResult(after, true)
''',
    "invalidate AF preparation after focus-area change"
)

sony_path.write_text(sony)

manager_path = Path("sonycamera/src/main/java/io/github/gallo/sonycamera/usb/UsbCameraConnectionManager.kt")
manager = manager_path.read_text()

manager = replace_once(
    manager,
    '''                    try {
                        val commandStartedMs = System.currentTimeMillis()
                        val dispatchWaitMs = commandStartedMs - requestedAtMs

                        // ILCE-7CM2 monitor taps use one Sony Remote Touch action.
''',
    '''                    try {
                        val commandStartedMs = System.currentTimeMillis()
                        val dispatchWaitMs = commandStartedMs - requestedAtMs
                        val prepStartedMs = System.currentTimeMillis()
                        val prepDebug = camera.prepareMonitorTapAf()
                        val prepMs = System.currentTimeMillis() - prepStartedMs

                        // ILCE-7CM2 monitor taps use Remote Touch only when the
                        // camera itself reports D284=Enable and E083=Spot AF.
''',
    "prepare AF path at tap"
)

manager = replace_once(
    manager,
    '''                                val message = "REMOTE TOUCH x=$safeX y=$safeY | D2E4/9207=" +
                                    PtpConstants.responseCodeName(touch.responseCode) +
                                    " | dispatch=${dispatchWaitMs}ms bus=${touch.queueWaitMs}ms" +
                                    " wire+ack=${wireAndAckMs}ms total=${totalMs}ms"
''',
    '''                                val message = "AF RT SPOT | $prepDebug | x=$safeX y=$safeY | " +
                                    "D2E4=${PtpConstants.responseCodeName(touch.responseCode)} | " +
                                    "dispatch=${dispatchWaitMs}ms prep=${prepMs}ms bus=${touch.queueWaitMs}ms " +
                                    "wire+ack=${wireAndAckMs}ms total=${totalMs}ms"
''',
    "remote touch visible diagnostic"
)

manager = replace_once(
    manager,
    '''                        val moveMessage = camera.setAfPoint(safeX, safeY)
                        val pressResult = camera.setAutofocusPressed(true)
                        afHalfPressHeld = true
                        val message = "$moveMessage | AF=${PtpConstants.responseCodeName(pressResult.responseCode)}"
''',
    '''                        val moveMessage = camera.setAfPoint(safeX, safeY)
                        // Surface the actual move as soon as D2DC completes; S1 focus
                        // follows, but no UI/logcat access is required to see the path.
                        _events.emit(CameraEvent.FocusDebug("$prepDebug | $moveMessage | AF starting"))
                        val pressResult = camera.setAutofocusPressed(true)
                        afHalfPressHeld = true
                        val totalMs = System.currentTimeMillis() - requestedAtMs
                        val message = "$prepDebug | $moveMessage | AF=${PtpConstants.responseCodeName(pressResult.responseCode)} | total=${totalMs}ms"
''',
    "fallback diagnostic"
)

manager = replace_once(
    manager,
    '''            _connectionState.value = CameraConnectionState.Ready

            // Live view is a post-connect operation, matching Sony's sample/API
''',
    '''            _connectionState.value = CameraConnectionState.Ready
            _events.emit(CameraEvent.FocusDebug("AF READY | ${localCamera.monitorAfDebug()}"))

            // Live view is a post-connect operation, matching Sony's sample/API
''',
    "emit AF preparation state on connect"
)

manager_path.write_text(manager)

ui_path = Path("demo/src/main/java/io/github/gallo/sonycamera/demo/CameraScreen.kt")
ui = ui_path.read_text()

ui = replace_once(
    ui,
    '''            var lastError by remember { mutableStateOf<String?>(null) }
            var focusFrames by remember { mutableStateOf<List<CameraFocusFrame>>(emptyList()) }
''',
    '''            var lastError by remember { mutableStateOf<String?>(null) }
            var focusDebug by remember { mutableStateOf<String?>(null) }
            var focusFrames by remember { mutableStateOf<List<CameraFocusFrame>>(emptyList()) }
''',
    "focus debug UI state"
)

ui = replace_once(
    ui,
    '''                        is CameraEvent.CameraSettingsUpdated -> cameraSettings = event.state
                        is CameraEvent.Error -> lastError = event.message
''',
    '''                        is CameraEvent.CameraSettingsUpdated -> cameraSettings = event.state
                        is CameraEvent.FocusDebug -> focusDebug = event.message
                        is CameraEvent.Error -> lastError = event.message
''',
    "consume FocusDebug event"
)

ui = replace_once(
    ui,
    '''                    focusPoint = Offset(0.5f, 0.5f)
                    queuedAfPoint = null
''',
    '''                    focusPoint = Offset(0.5f, 0.5f)
                    focusDebug = null
                    queuedAfPoint = null
''',
    "clear focus debug on disconnect"
)

ui = replace_once(
    ui,
    '''            LaunchedEffect(capturedThumb) {
                if (capturedThumb != null) { delay(1200); capturedThumb = null }
            }

            fun requestAf(x: Int, y: Int) {
''',
    '''            LaunchedEffect(capturedThumb) {
                if (capturedThumb != null) { delay(1200); capturedThumb = null }
            }
            LaunchedEffect(focusDebug) {
                if (focusDebug != null) {
                    delay(5000)
                    focusDebug = null
                }
            }

            fun requestAf(x: Int, y: Int) {
''',
    "auto clear focus debug"
)

ui = replace_once(
    ui,
    '''                lastError?.let { message ->
                    LaunchedEffect(message) { delay(3500); lastError = null }
''',
    '''                focusDebug?.let { message ->
                    Text(
                        text = message,
                        color = AfGreen,
                        fontSize = 10.sp,
                        lineHeight = 12.sp,
                        maxLines = 4,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .padding(start = 10.dp, top = if (menusVisible) 64.dp else 10.dp)
                            .widthIn(max = 360.dp)
                            .background(Color.Black.copy(alpha = 0.86f), RoundedCornerShape(3.dp))
                            .border(1.dp, AfGreen.copy(alpha = 0.65f), RoundedCornerShape(3.dp))
                            .padding(horizontal = 9.dp, vertical = 6.dp)
                    )
                }

                lastError?.let { message ->
                    LaunchedEffect(message) { delay(3500); lastError = null }
''',
    "visible focus debug overlay"
)

ui_path.write_text(ui)

# One-shot patch: keep the repository clean after Actions commits the result.
Path(__file__).unlink()
