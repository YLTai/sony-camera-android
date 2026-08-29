from pathlib import Path

path = Path('sonycamera/src/main/java/io/github/gallo/sonycamera/ptp/SonyPtpCamera.kt')
text = path.read_text()
old = '''        // Aperture limits are lens-specific. Prefer explicit descriptor range\n        // bounds; enum-only lenses still provide authoritative first/last F values.\n        val minimum = if (descriptor.setting == CameraExposureSetting.APERTURE) {\n            (descriptor.rangeMin ?: raws.minOrNull())?.let { raw ->\n                CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))\n            }\n        } else null\n        val maximum = if (descriptor.setting == CameraExposureSetting.APERTURE) {\n            (descriptor.rangeMax ?: raws.maxOrNull())?.let { raw ->\n                CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))\n            }\n        } else null'''
new = '''        // Aperture limits must come from the camera/lens descriptor itself.\n        // Never derive MIN/MAX from the generic compatibility choice table: if\n        // Sony does not report a range or enum, leave the bound unknown in UI.\n        val authoritativeApertureValues = descriptor.enumValues.distinct()\n        val minimum = if (descriptor.setting == CameraExposureSetting.APERTURE) {\n            (descriptor.rangeMin ?: authoritativeApertureValues.minOrNull())?.let { raw ->\n                CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))\n            }\n        } else null\n        val maximum = if (descriptor.setting == CameraExposureSetting.APERTURE) {\n            (descriptor.rangeMax ?: authoritativeApertureValues.maxOrNull())?.let { raw ->\n                CameraExposureOption(raw, formatExposureValue(descriptor.setting, raw))\n            }\n        } else null'''
if old not in text:
    raise SystemExit('target aperture-bound block not found')
path.write_text(text.replace(old, new, 1))
print('Aperture MIN/MAX now use only camera-reported range or enum values')
