import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from version_utils import get_display_version, get_git_short_hash  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VersionUtilsTests(unittest.TestCase):
    def test_short_hash_in_real_checkout(self):
        # git metadataがあるリポジトリでは短縮ハッシュかlocalを返す
        value = get_git_short_hash(REPO_ROOT)
        self.assertIsInstance(value, str)
        self.assertTrue(len(value) > 0)

    def test_short_hash_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(get_git_short_hash(tmp), "local")

    def test_display_version_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(get_display_version(tmp), "local")

    def test_display_version_in_real_checkout(self):
        # タグ付きの実リポジトリでは V で始まる版、なければハッシュ/local
        value = get_display_version(REPO_ROOT)
        self.assertIsInstance(value, str)
        self.assertTrue(
            value.startswith("V") or value == "local" or len(value) >= 7,
            f"unexpected version: {value}",
        )


if __name__ == "__main__":
    unittest.main()
