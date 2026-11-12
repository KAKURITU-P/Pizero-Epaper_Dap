"""Git metadata から表示用バージョンを取得するユーティリティ。"""

import os
import re
import subprocess


def get_git_short_hash(base_dir):
    """Return the running checkout's short commit ID, if Git metadata exists."""
    try:
        result = subprocess.run(
            ["git", "-C", base_dir, "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        commit = result.stdout.strip()
        if re.fullmatch(r"[0-9a-fA-F]{7,12}", commit):
            return commit
    except (OSError, subprocess.SubprocessError):
        pass
    return "local"


def get_display_version(base_dir):
    """表示用バージョン文字列を返す。

    - タグ(例: V1.1.3)が取得できれば「タグ+先行コミット数+ハッシュ」
      (例: V1.1.3-2-g1a2b3c4)。タグを打てば表示が自動で追従する。
    - gitが使えるがタグが無い環境では短縮ハッシュ。
    - zip配布などgit metadataが無い環境では local。
    """
    try:
        result = subprocess.run(
            ["git", "-C", base_dir, "describe", "--tags", "--match", "V*"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        version = result.stdout.strip()
        if re.fullmatch(r"V[0-9A-Za-z.\-]+", version):
            return version
    except (OSError, subprocess.SubprocessError):
        pass
    return get_git_short_hash(base_dir)
