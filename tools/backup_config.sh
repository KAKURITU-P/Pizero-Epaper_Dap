#!/bin/bash
# DAP の設定・データの軽量バックアップ。
# SD イメージ全体のバックアップとは別に、設定類だけをすばやく保存する。
# 使い方: bash tools/backup_config.sh [保存先ディレクトリ]   (既定: $HOME)
set -euo pipefail

DEST="${1:-$HOME}"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$DEST/dap_config_$STAMP.tar.gz"

cd /home/pi/dap
files=()
for f in settings.json play_data.json resume.json favorites.json ap_config.json; do
    [ -f "$f" ] && files+=("$f")
done
if [ ${#files[@]} -eq 0 ]; then
    echo "バックアップ対象がありません" >&2
    exit 1
fi

# ap_config.json には AP のパスワードが含まれるため扱いに注意
tar czf "$OUT" "${files[@]}"
echo "Saved: $OUT ($(du -h "$OUT" | cut -f1))  files: ${files[*]}"
