#!/usr/bin/env python3
import json
import os
import time
import signal
import logging
from pathlib import Path
from smbus2 import SMBus, i2c_msg

ADDR = 0x68
DIVIDER_RATIO = 4.11 / 1.45
STATUS_FILE = Path("/tmp/battery_status")
# 日次サマリー(その日のmin/avg/max残量)を1日1行でSDに追記する。
# バッテリーの経日劣化傾向を追うための記録。
DAILY_CSV = Path("/home/pi/dap/battery_daily.csv")
day_stats = {"date": None, "min": 100.0, "max": 0.0, "sum": 0.0, "count": 0}
# 残量の履歴([[unix時刻, %], ...])。2分ごとに追記し、直近5時間分を保持する。
# /tmp は tmpfs なので再起動で消え、SDカードには書き込まない。
HISTORY_FILE = Path("/tmp/battery_history.json")
HISTORY_INTERVAL_SEC = 120
HISTORY_MAX = 150
stopping = False
last_history_ts = 0   # 履歴へ最後に追記した時刻(2分間隔の制御用)

def append_daily_summary():
    """その日までの min/avg/max をCSVに追記する(1日1行)。"""
    if day_stats["count"] == 0:
        return
    avg = day_stats["sum"] / day_stats["count"]
    try:
        new_file = not DAILY_CSV.exists()
        with DAILY_CSV.open("a", encoding="utf-8") as f:
            if new_file:
                f.write("date,min_percent,avg_percent,max_percent\n")
            f.write(f"{day_stats['date']},{day_stats['min']:.0f},{avg:.1f},{day_stats['max']:.0f}\n")
        logging.info("Daily summary appended: %s", f"{day_stats['date']} min{day_stats['min']:.0f}% avg{avg:.1f}% max{day_stats['max']:.0f}%")
    except OSError as error:
        logging.warning("Daily summary write failed: %s", error)

def update_daily_stats(percent):
    """残量を読むごとに日次統計を更新。日付が変わったら前日分をCSVへ。"""
    if day_stats["date"] is None:
        day_stats["date"] = time.strftime("%Y-%m-%d")
    elif day_stats["date"] != time.strftime("%Y-%m-%d"):
        append_daily_summary()
        day_stats.update(date=time.strftime("%Y-%m-%d"), min=100.0, max=0.0, sum=0.0, count=0)
    day_stats["min"] = min(day_stats["min"], percent)
    day_stats["max"] = max(day_stats["max"], percent)
    day_stats["sum"] += percent
    day_stats["count"] += 1

def stop_handler(signum, frame):
    global stopping
    stopping = True
    append_daily_summary()   # シャットダウン前にも当日分を確定させる

def read_voltage(bus):
    bus.i2c_rdwr(i2c_msg.write(ADDR, [0x88]))  # 16-bit, one-shot, gain x1
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        msg = i2c_msg.read(ADDR, 3)
        bus.i2c_rdwr(msg)
        data = list(msg)
        if not data[2] & 0x80:
            raw = (data[0] << 8) | data[1]
            if raw & 0x8000:
                raw -= 0x10000
            return raw * 2.048 / 32768
        time.sleep(0.01)
    raise TimeoutError("MCP3425 conversion timed out")

def publish(text):
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(text + "\n", encoding="ascii")
    os.replace(tmp, STATUS_FILE)

def append_history(percent, now=None):
    """残量%を履歴JSONに2分ごとに追記する(アトミック書き込み)。"""
    global last_history_ts
    now = time.time() if now is None else now
    if now - last_history_ts < HISTORY_INTERVAL_SEC:
        return
    last_history_ts = now
    try:
        entries = json.loads(HISTORY_FILE.read_text(encoding="ascii"))
        if not isinstance(entries, list):
            entries = []
    except (OSError, ValueError):
        entries = []
    entries.append([int(now), int(percent)])
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries[-HISTORY_MAX:]), encoding="ascii")
    os.replace(tmp, HISTORY_FILE)

def main():
    global stopping
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    logging.basicConfig(level=logging.INFO)

    zero_percent_count = 0   # 0%を連続で検知した回数
    REQUIRED_COUNT = 3       # 何回連続で0%になったらシャットダウンするか
    startup_grace_count = 5  # 起動直後の数回（約12秒間）はシャットダウン判定を免除

    while not stopping:
        try:
            with SMBus(3) as bus:
                adc_v = read_voltage(bus)
            if not 0 <= adc_v <= 2.048:
                raise ValueError(f"ADC input out of range: {adc_v:.3f} V")
            battery_v = adc_v * DIVIDER_RATIO
            
            # 概算表示。1セルLi-ion/LiPoを想定
            percent = max(0, min(100, round((battery_v - 3.3) / (4.1 - 3.3) * 100)))
            publish(f"{battery_v:.2f}V/{percent}%")
            append_history(percent)
            update_daily_stats(percent)
            logging.info("ADC %.4f V, battery %.2f V, %d%%",
                         adc_v, battery_v, percent)

            # 起動直後の猶予期間の処理
            if startup_grace_count > 0:
                startup_grace_count -= 1
                logging.info(f"Startup grace period remaining: {startup_grace_count}")
            else:
                # --- 低残量が3回続いたらシャットダウンする安全装置 ---
                # 0%(3.3V)ギリギリだと負荷変動でブラウンアウトが先に来るため、
                # 3%(約3.32V)で早めに安全停止する。
                if percent <= 3:
                    zero_percent_count += 1
                    logging.warning(f"Low battery detected! Count: {zero_percent_count}/{REQUIRED_COUNT}")

                    if zero_percent_count >= REQUIRED_COUNT:
                        logging.critical("Battery low. Initiating safe shutdown...")
                        publish("SHUTTING DOWN")
                        time.sleep(1)
                        os.system("sudo shutdown -h now")
                        break
                else:
                    # 低残量じゃなくなったらカウンターをリセット
                    if zero_percent_count > 0:
                        zero_percent_count = 0

        except Exception:
            logging.exception("Battery reading failed")
            publish("-/--%")
            
        for _ in range(50):
            if stopping:
                break
            time.sleep(0.1)

    append_daily_summary()   # 停止時にも当日分を確定

if __name__ == "__main__":
    main()
