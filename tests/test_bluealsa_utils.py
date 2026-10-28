import unittest

from bluealsa_utils import (
    bluealsa_pcm_candidates,
    bluealsa_pcm_name,
    build_bluealsa_asoundrc,
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
        self.assertIn('interface "hci0"', config)
        self.assertIn('device "AA:BB:CC:DD:EE:FF"', config)
        self.assertIn('profile "a2dp"', config)
        self.assertIn('slave.pcm "equal"', config)

    def test_pcm_candidates(self):
        candidates = bluealsa_pcm_candidates(self.MAC)
        self.assertEqual(candidates[0], bluealsa_pcm_name(self.MAC))
        self.assertIn("bluealsa", candidates)

    def test_pcm_list_detection(self):
        output = """null\n    Discard all samples\nbluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp\n    BT audio\n"""
        self.assertTrue(pcm_list_contains_device(output, self.MAC))
        self.assertTrue(pcm_list_contains_any(output, bluealsa_pcm_candidates(self.MAC)))
        self.assertFalse(pcm_list_contains_device(output, "11:22:33:44:55:66"))


if __name__ == "__main__":
    unittest.main()
