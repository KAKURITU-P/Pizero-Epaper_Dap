#!/usr/bin/env python3
import os
import time
import signal
import logging
from pathlib import Path
from smbus2 import SMBus, i2c_msg

ADDR = 0x68
DIVIDER_RATIO = 4.11 / 1.45
STATUS_FILE = Path("/tmp/battery_status")
stopping = False

def stop_handler(signum, frame):
    global stopping
    stopping = True

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

def main():
    global stopping
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    logging.basicConfig(level=logging.INFO)

    while not stopping:
        try:
            with SMBus(1) as bus:
                adc_v = read_voltage(bus)
            if not 0 <= adc_v <= 2.048:
                raise ValueError(f"ADC input out of range: {adc_v:.3f} V")
            battery_v = adc_v * DIVIDER_RATIO
            # 概算表示。1セルLi-ion/LiPoを想定
            percent = max(0, min(100, round((battery_v - 3.3) / (4.1 - 3.3) * 100)))
            publish(f"{battery_v:.2f}V/{percent}%")
            logging.info("ADC %.4f V, battery %.2f V, %d%%",
                         adc_v, battery_v, percent)
        except Exception:
            logging.exception("Battery reading failed")
            publish("-/--%")
        for _ in range(50):
            if stopping:
                break
            time.sleep(0.1)

if __name__ == "__main__":
    main()
