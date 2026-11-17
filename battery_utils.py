"""battery-monitor.service が公開するバッテリー状態ファイルの読み取り。"""

import json
import os
import re

# battery_monitor.py が書き込むステータスファイル。形式は "4.02V/95%" など。
BATTERY_STATUS_PATH = "/tmp/battery_status"

# battery_monitor.py が1分ごとに追記する履歴ファイル。[[unix時刻, %], ...]
BATTERY_HISTORY_PATH = "/tmp/battery_history.json"


def get_battery_info(status_path=BATTERY_STATUS_PATH):
    """バッテリーの % と V を取得"""
    pct_str = "--%"
    volt_str = ""
    try:
        if os.path.exists(status_path):
            with open(status_path, "r") as f:
                raw = f.read().strip()
                if "/" in raw:
                    parts = raw.split("/")
                    v_raw = parts[0].strip()
                    p_raw = parts[1].strip()

                    volt_str = v_raw.upper().rstrip('V').strip()
                    if volt_str and not volt_str.startswith("-"):
                        volt_str = volt_str + "V"
                    else:
                        volt_str = ""
                    pct_str = p_raw.strip()
                else:
                    pct_str = raw
    except Exception:
        pass
    return pct_str, volt_str


def get_battery_percent(status_path=BATTERY_STATUS_PATH):
    """ステータス文字列から残量%を数値で返す。読めない場合はNone。"""
    pct_str, _ = get_battery_info(status_path)
    match = re.search(r"-?\d+(?:\.\d+)?", pct_str)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def get_battery_history(history_path=BATTERY_HISTORY_PATH):
    """履歴JSONから残量%のリストを古い順に返す。壊れていれば空リスト。"""
    try:
        with open(history_path, "r") as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    values = []
    for entry in entries:
        try:
            values.append(int(entry[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return values
