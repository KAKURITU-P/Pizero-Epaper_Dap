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


def format_display_version(describe_output):
    """git describe の出力を表示用に整形する(純粋関数)。

    - タグぴったり: "V1.1.5"          → "V1.1.5"
    - タグから先行: "V1.1.4-9-g5a73e5f" → "V1.1.4-9 (5a73e5f)"
      (git describeの「タグ-先行コミット数-gハッシュ」を画面向けに読みやすく)
    - 上記に一致しない文字列はそのまま返す。
    """
    match = re.fullmatch(r"(.+)-(\d+)-g([0-9a-fA-F]+)", describe_output)
    if match:
        return f"{match.group(1)}-{match.group(2)} ({match.group(3)})"
    return describe_output


def get_display_version(base_dir):
    """表示用バージョン文字列を返す。

    - タグ(例: V1.1.4)が取得できれば「タグ-先行コミット数 (ハッシュ)」
      (例: V1.1.4-9 (5a73e5f))。タグを打てば表示が自動で追従する。
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
            return format_display_version(version)
    except (OSError, subprocess.SubprocessError):
        pass
    return get_git_short_hash(base_dir)
