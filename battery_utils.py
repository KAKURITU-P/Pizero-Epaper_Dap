"""battery-monitor.service が公開するバッテリー状態ファイルの読み取り。"""

import os
import re

# battery_monitor.py が書き込むステータスファイル。形式は "4.02V/95%" など。
BATTERY_STATUS_PATH = "/tmp/battery_status"


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
