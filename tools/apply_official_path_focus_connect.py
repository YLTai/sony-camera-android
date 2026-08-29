from pathlib import Path

root = Path(__file__).resolve().parents[1]
sony_path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt"
transport_path = root / "sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/PtpTransport.kt"
sony = sony_path.read_text()
transport = transport_path.read_text()

old = '''        val preferProtocol3 = deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true\n        val extV3 = if (preferProtocol3) {\n            transport.sendCommandWithDataShortTimeout(\n                PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO,\n                2500,\n                0x012C,\n                1\n            )\n        } else null\n\n        val useProtocol3 = extV3?.isSuccess == true && extV3.dataSize > 0\n'''
new = '''        val preferProtocol3 = deviceName?.contains("ILCE-7CM2", ignoreCase = true) == true\n\n        // Sony's Camera Remote Command connect path retries 0x9202 because a\n        // cold body commonly returns an empty capability list while the vendor\n        // session is still coming up. A single attempt is therefore not a\n        // valid connect failure. Keep the retry bounded so Initializing cannot\n        // turn into an unbounded loading state.\n        var extV3: PtpDataResponse? = null\n        var extV3Attempts = 0\n        if (preferProtocol3) {\n            for (attempt in 1..5) {\n                extV3Attempts = attempt\n                val candidate = transport.sendCommandWithDataShortTimeout(\n                    PtpConstants.OP_SONY_SDIO_GET_EXT_DEVICE_INFO,\n                    900,\n                    0x012C,\n                    1\n                )\n                extV3 = candidate\n                Log.d(TAG, "GetExtDeviceInfo PTP3 attempt $attempt/5: " +\n                        "${PtpConstants.responseCodeName(candidate.responseCode)}, ${candidate.dataSize}B")\n                if (candidate.isSuccess && candidate.dataSize > 0) break\n                Thread.sleep(100)\n            }\n        }\n\n        val useProtocol3 = extV3?.isSuccess == true && extV3.dataSize > 0\n'''
assert old in sony, "PTP3 ext-info block changed"
sony = sony.replace(old, new)

old_debug = '''                append(PtpConstants.responseCodeName(extV3?.responseCode ?: 0))\n                append("/").append(extV3?.dataSize ?: 0).append("B")\n                append(" sdio3=")\n'''
new_debug = '''                append(PtpConstants.responseCodeName(extV3?.responseCode ?: 0))\n                append("/").append(extV3?.dataSize ?: 0).append("B")\n                append(" attempts=").append(extV3Attempts)\n                append(" sdio3=")\n'''
assert old_debug in sony, "PTP3 debug block changed"
sony = sony.replace(old_debug, new_debug)

# The method is named ShortTimeout, so its final response phase must honor the\n# same timeout too. Previously a data-bearing handshake response could still\n# fall into the global 5s response timeout and make Camera Initializing linger.\nold_short = '''        val data = output.toByteArray()\n        val response = readResponse(txId)\n        PtpDataResponse(response.responseCode, responseTxId, data)\n    }\n'''
new_short = '''        val data = output.toByteArray()\n        val response = readResponse(txId, timeoutMs)\n        PtpDataResponse(response.responseCode, responseTxId, data)\n    }\n'''
assert old_short in transport, "short-timeout response block changed"
transport = transport.replace(old_short, new_short, 1)

sony_path.write_text(sony)
transport_path.write_text(transport)
Path(__file__).unlink()
