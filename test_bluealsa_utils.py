import unittest

from bluealsa_utils import (
    bluealsa_pcm_candidates,
    bluealsa_pcm_name,
    build_bluealsa_asoundrc,
    build_pwm_asoundrc,
    build_usb_otg_asoundrc,
    find_usb_audio_device,
    normalize_mac,
    pcm_list_contains_any,
    pcm_list_contains_device,
)


class BlueAlsaUtilsTest(unittest.TestCase):
    MAC = "aa:bb:cc:dd:ee:ff"

    def test_normalize_mac(self):
        self.assertEqual(normalize_mac(" aa:bb:cc:dd:ee:ff "), "AA:BB:CC:DD:EE:FF")
        with self.assertRaises(ValueError):
            normalize_mac("not-a-mac")

    def test_build_asoundrc_uses_explicit_a2dp_device(self):
        config = build_bluealsa_asoundrc(self.MAC)
        self.assertIn('type bluealsa', config)
        self.assertIn('device "AA:BB:CC:DD:EE:FF"', config)
        self.assertIn('profile "a2dp"', config)
        self.assertIn('type equal', config)
        self.assertIn('slave.pcm "dap_bt_plug"', config)
        self.assertIn('pcm.dap_bt_plug', config)
        self.assertNotIn('rate 48000', config)
        self.assertNotIn('format S16_LE', config)

    def test_pcm_candidates(self):
        candidates = bluealsa_pcm_candidates(self.MAC)
        self.assertEqual(candidates[0], bluealsa_pcm_name(self.MAC))
        self.assertIn("bluealsa", candidates)

    def test_build_pwm_asoundrc_uses_direct_hardware(self):
        config = build_pwm_asoundrc()
        self.assertIn('slave.pcm "plughw:0,0"', config)
        self.assertIn("type equal", config)
        self.assertNotIn("rate 48000", config)
        self.assertNotIn("dap_pwm", config)

    def test_build_usb_otg_asoundrc_uses_usb_hardware(self):
        config = build_usb_otg_asoundrc()
        self.assertIn('slave.pcm "plughw:1,0"', config)
        self.assertIn("type equal", config)

    def test_build_usb_otg_asoundrc_rejects_multiline_device(self):
        with self.assertRaises(ValueError):
            build_usb_otg_asoundrc('plughw:1,0"\n')

    def test_find_usb_audio_device_ignores_card_number(self):
        cards = (
            " 0 [Headphones     ]: bcm2835_headphon - bcm2835 Headphones\n"
            "                      bcm2835 Headphones\n"
            " 1 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi\n"
            "                      vc4-hdmi\n"
            " 2 [Device         ]: USB-Audio - USB Audio Device\n"
            "                      C-Media USB Audio Device at usb-20980000.usb-1, full speed\n"
        )
        self.assertEqual(find_usb_audio_device(cards), "plughw:CARD=Device,DEV=0")

    def test_find_usb_audio_device_returns_none_without_usb(self):
        cards = " 0 [Headphones     ]: bcm2835_headphon - bcm2835 Headphones\n"
        self.assertIsNone(find_usb_audio_device(cards))
        self.assertIsNone(find_usb_audio_device(""))

    def test_pcm_list_detection(self):
        output = """null\n    Discard all samples\nbluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp\n    BT audio\n"""
        self.assertTrue(pcm_list_contains_device(output, self.MAC))
        self.assertTrue(pcm_list_contains_any(output, bluealsa_pcm_candidates(self.MAC)))
        self.assertFalse(pcm_list_contains_device(output, "11:22:33:44:55:66"))


if __name__ == "__main__":
    unittest.main()
