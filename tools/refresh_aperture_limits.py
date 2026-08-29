from pathlib import Path

p = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt')
text = p.read_text()

def rep(old, new):
    global text
    if old not in text:
        raise SystemExit('pattern missing: ' + old[:100])
    text = text.replace(old, new, 1)

rep(
'''            val descriptor = fromSnapshot ?: probeExposureDescriptor(setting, ids)\n            descriptor?.let {''',
'''            // Some 0x9209 snapshots carry only current value while the per-control\n            // descriptor contains the lens range/enum. Enrich aperture once at init\n            // when the aggregate record does not expose either form.\n            if (setting == CameraExposureSetting.APERTURE &&\n                fromSnapshot != null &&\n                fromSnapshot.enumValues.size < 2 &&\n                fromSnapshot.rangeMin == null &&\n                fromSnapshot.rangeMax == null\n            ) {\n                val controlDesc = transport.sendCommandWithDataShortTimeout(\n                    PtpConstants.OP_SONY_GET_CONTROL_DEVICE_DESC,\n                    1_200,\n                    fromSnapshot.propertyCode\n                )\n                if (controlDesc.isSuccess && controlDesc.data.isNotEmpty()) {\n                    parseExposureDescriptor(controlDesc.data, setting, fromSnapshot.propertyCode)?.let { rich ->\n                        fromSnapshot = rich.copy(\n                            writable = rich.writable || fromSnapshot.writable,\n                            initialValue = fromSnapshot.initialValue ?: rich.initialValue\n                        )\n                    }\n                }\n            }\n\n            val descriptor = fromSnapshot ?: probeExposureDescriptor(setting, ids)\n            descriptor?.let {'''
)

rep(
'''        val all = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)\n        val allData = if (all.isSuccess) all.data else ByteArray(0)\n\n        fun current(descriptor: ExposureDescriptor?): Long? {''',
'''        val all = transport.sendCommandWithData(PtpConstants.OP_SONY_GET_ALL_DEVICE_PROP_DATA)\n        val allData = if (all.isSuccess) all.data else ByteArray(0)\n\n        // Refresh descriptor forms from the live 0x9209 snapshot. This matters for\n        // variable-aperture zoom lenses: the valid F-number floor can change when\n        // focal length changes without reconnecting the USB session. Preserve any\n        // richer init-time form when a later snapshot is sparse.\n        if (allData.isNotEmpty()) {\n            exposureDescriptors.toMap().forEach { (setting, previous) ->\n                parseExposureDescriptor(allData, setting, previous.propertyCode)?.let { latest ->\n                    exposureDescriptors[setting] = latest.copy(\n                        writable = latest.writable || previous.writable,\n                        initialValue = latest.initialValue ?: previous.initialValue,\n                        enumValues = if (latest.enumValues.size >= 2) latest.enumValues else previous.enumValues,\n                        rangeMin = latest.rangeMin ?: previous.rangeMin,\n                        rangeMax = latest.rangeMax ?: previous.rangeMax\n                    )\n                }\n            }\n        }\n\n        fun current(descriptor: ExposureDescriptor?): Long? {'''
)

p.write_text(text)
print('updated live aperture descriptor refresh')
