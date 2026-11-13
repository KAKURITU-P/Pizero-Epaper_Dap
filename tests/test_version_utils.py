import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from version_utils import format_display_version, get_display_version, get_git_short_hash  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FormatDisplayVersionTests(unittest.TestCase):
    def test_exact_tag_stays_as_is(self):
        self.assertEqual(format_display_version("V1.1.5"), "V1.1.5")

    def test_commits_ahead_gets_parenthesized_hash(self):
        self.assertEqual(
            format_display_version("V1.1.4-9-g5a73e5f"),
            "V1.1.4-9 (5a73e5f)",
        )

    def test_long_hash_also_supported(self):
        self.assertEqual(
            format_display_version("V2.0.0-1-g0123456789abcdef"),
            "V2.0.0-1 (0123456789abcdef)",
        )

    def test_unexpected_format_passes_through(self):
        self.assertEqual(format_display_version("weird"), "weird")


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
