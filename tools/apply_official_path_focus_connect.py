from pathlib import Path

root = Path(__file__).resolve().parents[1]
sony_path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
transport_path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/PtpTransport.kt"
sony = sony_path.read_text()
transport = transport_path.read_text()

before = '''        val preferProtocol3 = deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true
        val extV3 = if (preferProtocol3) {
            transport.sendCommandWithDataShortTimeout(
                PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO,
                2500,
                0x012C,
                1
            )
        } else null

        val useProtocol3 = extV3?.isSuccess == true && extV3.dataSize > 0
'''
after = '''        val preferProtocol3 = deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true

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
'''
if before not in sony:
    raise SystemExit("PTP3 ext-info block changed")
sony = sony.replace(before, after, 1)

needle = '''                append(PtpConstants.responseCodeName(extV3?.responseCode ?: 0))
                append("/").append(extV3?.dataSize ?: 0).append("B")
                append(" sdio3=")
'''
replacement = '''                append(PtpConstants.responseCodeName(extV3?.responseCode ?: 0))
                append("/").append(extV3?.dataSize ?: 0).append("B")
                append(" attempts=").append(extV3Attempts)
                append(" sdio3=")
'''
if needle not in sony:
    raise SystemExit("PTP3 debug block changed")
sony = sony.replace(needle, replacement, 1)

needle = '''        val data = output.toByteArray()
        val response = readResponse(txId)
        PtpDataResponse(response.responseCode, responseTxId, data)
    }
'''
replacement = '''        val data = output.toByteArray()
        val response = readResponse(txId, timeoutMs)
        PtpDataResponse(response.responseCode, responseTxId, data)
    }
'''
if needle not in transport:
    raise SystemExit("short-timeout response block changed")
transport = transport.replace(needle, replacement, 1)

sony_path.write_text(sony)
transport_path.write_text(transport)
Path(__file__).unlink()
