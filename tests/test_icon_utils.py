import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from icon_utils import get_cropped_ship, load_playback_mode_icons
    HAS_PIL = True
except ImportError:
    # Pillow未導入の環境ではスキップ(実機のPiには導入済み)
    HAS_PIL = False


@unittest.skipUnless(HAS_PIL, "Pillow is not installed")
class PlaybackIconsTests(unittest.TestCase):
    def make_sheet(self, width=60, height=40):
        img = Image.new("1", (width, height), 1)
        return img

    def test_missing_sheet_returns_empty_dict(self):
        self.assertEqual(load_playback_mode_icons("/nonexistent/sheet.png"), {})

    def test_too_small_sheet_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sheet.png")
            self.make_sheet(50, 30).save(path)
            self.assertEqual(load_playback_mode_icons(path), {})

    def test_valid_sheet_returns_all_icons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sheet.png")
            self.make_sheet().save(path)
            icons = load_playback_mode_icons(path)
        expected = {"shuffle", "repeat_all", "repeat_one", "pwm",
                    "bt", "sbc", "aac", "other", "usb"}
        self.assertTrue(expected.issubset(icons.keys()))

    def test_codec_icons_combine_bt_sprite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sheet.png")
            self.make_sheet().save(path)
            icons = load_playback_mode_icons(path)
        for codec in ("aac", "sbc", "other"):
            self.assertEqual(icons[codec].size, (17, 11))


@unittest.skipUnless(HAS_PIL, "Pillow is not installed")
class CroppedShipTests(unittest.TestCase):
    def make_ships_image(self, path):
        # 実画像と同じ比率(500x520程度)のRGBA画像で代用する
        img = Image.new("RGBA", (500, 520), (0, 0, 0, 0))
        img.save(path)

    def test_missing_file_returns_none_pair(self):
        self.assertEqual(get_cropped_ship("/nonexistent/ships.png", 0, 20), (None, None))

    def test_valid_image_returns_shape_and_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ships.png")
            self.make_ships_image(path)
            shape, mask = get_cropped_ship(path, 0, 20)
        self.assertIsNotNone(shape)
        self.assertIsNotNone(mask)
        self.assertEqual(mask.size, shape.size)

    def test_invalid_index_falls_back_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ships.png")
            self.make_ships_image(path)
            # 範囲外のindexでも例外にならず0番相当を返す
            shape, mask = get_cropped_ship(path, 99, 20)
        self.assertIsNotNone(shape)


if __name__ == "__main__":
    unittest.main()
