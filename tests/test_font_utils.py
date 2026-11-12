import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from font_utils import find_japanese_font, get_font
    HAS_PIL = True
except ImportError:
    # Pillow未導入の環境ではスキップ(実機のPiには導入済み)
    HAS_PIL = False


@unittest.skipUnless(HAS_PIL, "Pillow is not installed")
class FontUtilsTests(unittest.TestCase):
    def test_missing_font_path_falls_back_to_default(self):
        font = get_font(None, 14)
        self.assertIsNotNone(font)

    def test_invalid_font_path_falls_back_to_default(self):
        font = get_font("/nonexistent/font.ttf", 14)
        self.assertIsNotNone(font)

    def test_default_font_supports_getlength_or_getbbox(self):
        font = get_font(None, 14)
        self.assertTrue(
            hasattr(font, "getlength") or hasattr(font, "getbbox"),
            "fallback font must be usable with truncate_by_width",
        )

    def test_find_japanese_font_returns_none_or_existing_path(self):
        path = find_japanese_font()
        self.assertTrue(path is None or os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
