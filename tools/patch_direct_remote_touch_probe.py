from pathlib import Path

path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt')
text = path.read_text()

anchor = '''    private fun setSonyScalarProperty(
        descriptor: SonyScalarEnumProperty,
        value: Long
    ): PtpResponse {
        // 0x9209 may describe a selectable setting as an array type, but Sony's
        // 0x9205 SetControlDeviceA takes one selected ELEMENT encoded at the
        // element width. Strip the PTP array bit before building the payload.
        val elementType = descriptor.dataType and 0xBFFF
        val size = scalarSize(elementType)
        require(size > 0) { "Unsupported Sony property type 0x${descriptor.dataType.toString(16)}" }
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
'''

insert = anchor + '''
    /**
     * Probe one Sony property outside the aggregate 0x9209 snapshot.
     *
     * Modern Sony bodies can omit SDK-facing properties from GetAllDevicePropData
     * even though their dedicated Device/Control descriptor command still exposes
     * them. Keep this diagnostic read-only: it tells us which command family owns
     * E083/D284/D2E4 without changing camera state.
     */
    private fun probeSonyPropertyDirect(propertyCode: Int, preferControl: Boolean): String {
        fun descriptorSummary(response: PtpDataResponse): String {
            val result = PtpConstants.responseCodeName(response.responseCode)
            if (!response.isSuccess || response.data.isEmpty()) return "$result/${response.dataSize}B"
            val data = response.data
            if (data.size >= 6 && u16(data, 0) == propertyCode) {
                val type = u16(data, 2)
                val getSet = data[4].toInt() and 0xFF
                val enabled = data[5].toInt() and 0xFF
                return "OK/${data.size}B:t=0x${type.toString(16)},gs=0x${getSet.toString(16)},en=$enabled"
            }
            val head = data.take(10).joinToString("") { "%02x".format(it.toInt() and 0xFF) }
            return "OK/${data.size}B:h=$head"
        }

        fun valueSummary(response: PtpDataResponse): String {
            val result = PtpConstants.responseCodeName(response.responseCode)
            if (!response.isSuccess || response.data.isEmpty()) return "$result/${response.dataSize}B"
            val data = response.data
            val head = data.take(12).joinToString("") { "%02x".format(it.toInt() and 0xFF) }
            val numeric = when (data.size) {
                1 -> (data[0].toInt() and 0xFF).toLong()
                2 -> u16(data, 0).toLong()
                4 -> (data[0].toLong() and 0xFF) or
                    ((data[1].toLong() and 0xFF) shl 8) or
                    ((data[2].toLong() and 0xFF) shl 16) or
                    ((data[3].toLong() and 0xFF) shl 24)
                else -> null
            }
            return if (numeric != null) "OK/${data.size}B:$numeric[$head]" else "OK/${data.size}B:$head"
        }

        val firstOp = if (preferControl) {
            PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC
        } else {
            PtpConstants.OP_SONY_GET_DEVICE_PROP_DESC
        }
        val secondOp = if (preferControl) {
            PtpConstants.OP_SONY_GET_DEVICE_PROP_DESC
        } else {
            PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC
        }

        val first = transport.sendCommandWithDataShortTimeout(firstOp, 400, propertyCode)
        var value: PtpDataResponse? = null
        if (first.isSuccess && firstOp == PtpConstants.OP_SONY_GET_DEVICE_PROP_DESC) {
            value = transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
                350,
                propertyCode
            )
        }

        val second = if (!first.isSuccess || first.data.isEmpty()) {
            transport.sendCommandWithDataShortTimeout(secondOp, 400, propertyCode)
        } else null
        if (value == null && second?.isSuccess == true &&
            secondOp == PtpConstants.OP_SONY_GET_DEVICE_PROP_DESC
        ) {
            value = transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_GET_DEVICE_PROP_VALUE,
                350,
                propertyCode
            )
        }

        val primaryLabel = if (firstOp == PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC) "B" else "A"
        val secondaryLabel = if (secondOp == PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC) "B" else "A"
        return buildString {
            append("0x").append(propertyCode.toString(16).uppercase())
            append(" ").append(primaryLabel).append("=").append(descriptorSummary(first))
            if (second != null) {
                append(" ").append(secondaryLabel).append("=").append(descriptorSummary(second))
            }
            if (value != null) append(" V=").append(valueSummary(value))
        }
    }
'''

if anchor not in text:
    raise SystemExit('setSonyScalarProperty anchor not found')
text = text.replace(anchor, insert, 1)

old = '''        val remoteFunctionWrite = writeEnumTarget(remoteFunctionBeforeProp, 2L) // Spot_AF

        // SetDeviceProperty is asynchronous in Sony's SDK model. A successful
        // transport ACK is not proof that the camera-side state has changed, so
        // give E083/D284 a short bounded settle window and re-read 0x9209.
        var settleReads = 0
        var remoteFunction = remoteFunctionBeforeProp
        var remoteEnable = remoteEnableBeforeProp
        for (attempt in 1..6) {
            if (remoteFunctionWrite != null || attempt > 1) {
                Thread.sleep(if (attempt == 1) 80L else 120L)
            }
            if (refreshProperties()) settleReads += 1
            remoteFunction = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION)
            remoteEnable = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS)
            if (remoteFunction?.currentValue == 2L && remoteEnable?.currentValue == 1L) break
        }
'''

new = '''        val directProbeLines = mutableListOf<String>()
        val aggregateRemoteMissing = remoteFunctionBeforeProp == null && remoteEnableBeforeProp == null
        if (aggregateRemoteMissing) {
            // 0x9209 has already told us nothing about the Remote Touch pair.
            // Bypass it and ask the dedicated Sony command families directly.
            directProbeLines += probeSonyPropertyDirect(
                PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION,
                preferControl = false
            )
            directProbeLines += probeSonyPropertyDirect(
                PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS,
                preferControl = false
            )
            directProbeLines += probeSonyPropertyDirect(
                PtpConstants.PROP_SONY_REMOTE_TOUCH_OPERATION,
                preferControl = true
            )
        }

        val remoteFunctionWrite = writeEnumTarget(remoteFunctionBeforeProp, 2L) // Spot_AF

        // SetDeviceProperty is asynchronous in Sony's SDK model. Only perform
        // the settle loop when the aggregate snapshot actually exposed at least
        // one Remote Touch property. Re-reading the same missing 0x9209 entries
        // six times only steals transport time and cannot make them materialize.
        var settleReads = 0
        var remoteFunction = remoteFunctionBeforeProp
        var remoteEnable = remoteEnableBeforeProp
        if (!aggregateRemoteMissing) {
            for (attempt in 1..6) {
                if (remoteFunctionWrite != null || attempt > 1) {
                    Thread.sleep(if (attempt == 1) 80L else 120L)
                }
                if (refreshProperties()) settleReads += 1
                remoteFunction = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_FUNCTION)
                remoteEnable = property(PtpConstants.PROP_SONY_REMOTE_TOUCH_ENABLE_STATUS)
                if (remoteFunction?.currentValue == 2L && remoteEnable?.currentValue == 1L) break
            }
        }
'''

if old not in text:
    raise SystemExit('settle-loop anchor not found')
text = text.replace(old, new, 1)

old2 = '''        val stateLine = listOf(
            touchState,
            touchFunctionState,
            remoteFunctionState,
            remoteEnableState,
            actionState,
            "reads=$settleReads"
        ).joinToString("\\n")
'''
new2 = '''        val stateLine = buildList {
            add(touchState)
            add(touchFunctionState)
            add(remoteFunctionState)
            add(remoteEnableState)
            add(actionState)
            add("reads=$settleReads")
            addAll(directProbeLines)
        }.joinToString("\\n")
'''
if old2 not in text:
    raise SystemExit('stateLine anchor not found')
text = text.replace(old2, new2, 1)

path.write_text(text)
Path(__file__).unlink()
