import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from battery_utils import get_battery_info, get_battery_percent  # noqa: E402


class BatteryInfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "battery_status")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(self.path, "w", encoding="ascii") as f:
            f.write(text)

    def test_missing_file_returns_placeholder(self):
        self.assertEqual(get_battery_info(self.path), ("--%", ""))

    def test_normal_voltage_and_percent(self):
        self.write("4.02V/95%\n")
        pct, volt = get_battery_info(self.path)
        self.assertEqual((pct, volt), ("95%", "4.02V"))

    def test_negative_voltage_is_hidden(self):
        self.write("-0.50/3%")
        pct, volt = get_battery_info(self.path)
        self.assertEqual(pct, "3%")
        self.assertEqual(volt, "")

    def test_shutdown_text_has_no_separator(self):
        self.write("SHUTTING DOWN")
        self.assertEqual(get_battery_info(self.path), ("SHUTTING DOWN", ""))

    def test_error_marker(self):
        self.write("-/--%")
        # "/"区切りとして解析されるため、電圧は非表示・%側は"--%"のまま
        self.assertEqual(get_battery_info(self.path), ("--%", ""))

    def test_percent_parsing(self):
        self.write("4.10V/100%")
        self.assertEqual(get_battery_percent(self.path), 100.0)

    def test_percent_parsing_no_digits(self):
        self.write("SHUTTING DOWN")
        self.assertIsNone(get_battery_percent(self.path))

    def test_percent_parsing_error_marker(self):
        self.write("-/--%")
        self.assertIsNone(get_battery_percent(self.path))


if __name__ == "__main__":
    unittest.main()
