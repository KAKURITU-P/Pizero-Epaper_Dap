import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_device_utils import (  # noqa: E402
    asoundrc_uses_usb_otg,
    get_audio_buffer_size,
    get_usb_otg_sample_rate,
    resolve_usb_otg_device,
)


CARDS_WITH_USB = """ 0 [b1            ]: bcm2835 - b1
                      bcm2835 ALSA
 1 [Device         ]: USB-Audio - USB Audio Device
                      Generic USB Audio Device at usb-3f980000.usb-1.2, full speed
"""

CARDS_WITHOUT_USB = """ 0 [b1            ]: bcm2835 - b1
                      bcm2835 ALSA
"""


class ResolveDeviceTests(unittest.TestCase):
    def test_env_takes_priority(self):
        self.assertEqual(resolve_usb_otg_device("plughw:2,0", CARDS_WITH_USB), "plughw:2,0")

    def test_detects_usb_card_id(self):
        self.assertEqual(
            resolve_usb_otg_device(None, CARDS_WITH_USB),
            "plughw:CARD=Device,DEV=0",
        )

    def test_falls_back_when_no_usb(self):
        self.assertEqual(resolve_usb_otg_device(None, CARDS_WITHOUT_USB), "plughw:1,0")

    def test_falls_back_when_proc_unreadable(self):
        # /procが無い環境(Windowsなど)でも例外にならずフォールバックする
        self.assertEqual(resolve_usb_otg_device(None, None), "plughw:1,0")


class AsoundrcDetectionTests(unittest.TestCase):
    def test_detects_resolved_device(self):
        content = "slave.pcm { ... plughw:CARD=Device,DEV=0 ... }"
        self.assertTrue(asoundrc_uses_usb_otg(content, "plughw:CARD=Device,DEV=0"))

    def test_detects_card_style_even_without_match(self):
        self.assertTrue(asoundrc_uses_usb_otg("plughw:CARD=Other", "plughw:1,0"))

    def test_detects_env_device(self):
        content = "slave.pcm plughw:3,0"
        self.assertTrue(asoundrc_uses_usb_otg(content, "plughw:1,0", "plughw:3,0"))

    def test_rejects_pwm_config(self):
        self.assertFalse(asoundrc_uses_usb_otg("pcm.!default { type plug }", "plughw:1,0"))


class AudioBufferSizeTests(unittest.TestCase):
    def test_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_audio_buffer_size(None), 16384)

    def test_valid_value(self):
        self.assertEqual(get_audio_buffer_size("8192"), 8192)

    def test_too_small_or_large_falls_back(self):
        self.assertEqual(get_audio_buffer_size("999"), 16384)
        self.assertEqual(get_audio_buffer_size("70000"), 16384)

    def test_invalid_falls_back(self):
        self.assertEqual(get_audio_buffer_size("abc"), 16384)
        self.assertEqual(get_audio_buffer_size(None), 16384)


class UsbSampleRateTests(unittest.TestCase):
    STREAM0 = """USB Audio Device
    Playback:
      Status: Running
      Interface 2
        Rates: 44100 - 48000
    Capture:
      Rates: 48000
    """

    def test_env_override(self):
        rate = get_usb_otg_sample_rate("plughw:CARD=Device,DEV=0", "48000", self.STREAM0)
        self.assertEqual(rate, 48000)

    def test_env_override_out_of_range_is_ignored(self):
        rate = get_usb_otg_sample_rate("plughw:CARD=Device,DEV=0", "999999", self.STREAM0)
        self.assertEqual(rate, 44100)

    def test_native_rate_from_stream_text(self):
        # 44100が優先される(preferred順)
        rate = get_usb_otg_sample_rate("plughw:CARD=Device,DEV=0", None, self.STREAM0)
        self.assertEqual(rate, 44100)

    def test_native_rate_skips_unsupported_preferred(self):
        stream0 = "Playback:\n        Rates: 48000 - 96000\n"
        rate = get_usb_otg_sample_rate("plughw:CARD=Device,DEV=0", None, stream0)
        self.assertEqual(rate, 48000)

    def test_device_without_card_name(self):
        self.assertEqual(get_usb_otg_sample_rate("plughw:1,0", None, self.STREAM0), 44100)


if __name__ == "__main__":
    unittest.main()
