import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from render_utils import (  # noqa: E402
    format_ram_bytes,
    format_size_bytes,
    format_time_str,
    truncate_by_width,
)


class WidthFont:
    """getlengthを持つフォントのスタブ(全角1文字=10px, ASCII=5px相当)。"""

    def getlength(self, text):
        return sum(10 if ord(char) > 127 else 5 for char in text)


class LegacyFont:
    """getbboxのみを持つフォントのスタブ。"""

    def getbbox(self, text):
        return (0, 0, len(text) * 8, 12)


class TruncateTests(unittest.TestCase):
    def test_short_text_is_kept(self):
        self.assertEqual(truncate_by_width("abc", WidthFont(), 100), "abc")

    def test_exact_fit_is_kept(self):
        self.assertEqual(truncate_by_width("abcde", WidthFont(), 25), "abcde")

    def test_long_text_gets_ellipsis(self):
        result = truncate_by_width("a" * 20, WidthFont(), 20)
        self.assertTrue(result.endswith("…"))
        # 元の文字列より短く、最大幅に収まること
        self.assertLessEqual(WidthFont().getlength(result), 20)
        self.assertLess(len(result), 21)

    def test_multibyte_text_gets_ellipsis(self):
        result = truncate_by_width("あ" * 10, WidthFont(), 30)
        self.assertTrue(result.endswith("…"))

    def test_legacy_getbbox_font_is_supported(self):
        result = truncate_by_width("abcdef", LegacyFont(), 30)
        self.assertTrue(result.endswith("…"))


class FormatTests(unittest.TestCase):
    def test_format_time_str(self):
        self.assertEqual(format_time_str(0), "00:00")
        self.assertEqual(format_time_str(65), "01:05")
        self.assertEqual(format_time_str(3600), "60:00")

    def test_format_size_bytes(self):
        self.assertEqual(format_size_bytes(0), "0 B")
        self.assertEqual(format_size_bytes(1500), "1.5 KB")
        self.assertEqual(format_size_bytes(2 * 1024 ** 2), "2.0 MB")
        self.assertEqual(format_size_bytes(3 * 1024 ** 3), "3.0 GB")

    def test_format_ram_bytes(self):
        # 1GB以上の総量ではformat_size_bytesに従う
        self.assertEqual(format_ram_bytes(500 * 1024 ** 2, 2 * 1024 ** 3), "500.0 MB")
        # 1GB未満では整数MB
        self.assertEqual(format_ram_bytes(1234 * (1024 ** 2), 512 * (1024 ** 2)), "1234 MB")
        self.assertEqual(format_ram_bytes(1234 * 1024, 512 * (1024 ** 2)), "1 MB")


if __name__ == "__main__":
    unittest.main()
