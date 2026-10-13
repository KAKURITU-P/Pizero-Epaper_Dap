#!/usr/bin/env python3
"""Disable Waveshare Raspberry Pi EPD PWR GPIO use (GPIO18) for PWM audio sharing."""
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("Usage: patch_waveshare_epd_gpio18_pwr.py /path/to/epdconfig.py")

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f"epdconfig.py not found: {path}")

source = path.read_text(encoding="utf-8")
newline = "\r\n" if "\r\n" in source else "\n"
source = source.replace("\r\n", "\n")
start = source.find("class RaspberryPi:")
end = source.find("\nclass JetsonNano:", start)
if start < 0 or end < 0:
    raise SystemExit("Could not locate RaspberryPi class safely; no changes made")
block = source[start:end]
if "PWR_PIN" not in block and "GPIO_PWR_PIN" not in block:
    print("already patched")
    raise SystemExit(0)

updated = block
patterns = [
    (r"(?m)^    PWR_PIN\s*=\s*18\s*\n", 1),
    (r"(?m)^        self\.GPIO_PWR_PIN\s*=\s*gpiozero\.LED\(self\.PWR_PIN\)\s*\n", 1),
    (r"(?m)^        elif pin == self\.PWR_PIN:\n(?:            .*\n)+", 2),
    (r"(?m)^        self\.GPIO_PWR_PIN\.on\(\)\s*\n", 1),
    (r"(?m)^        self\.GPIO_PWR_PIN\.off\(\)\s*\n", 1),
    (r"(?m)^            self\.GPIO_PWR_PIN\.close\(\)\s*\n", 1),
]
for pattern, expected in patterns:
    updated, count = re.subn(pattern, "", updated)
    if count != expected:
        raise SystemExit(f"Unexpected match count ({count}/{expected}) for {pattern}; no changes made")
if "PWR_PIN" in updated or "GPIO_PWR_PIN" in updated:
    raise SystemExit("PWR references remain in RaspberryPi class; no changes made")

backup = path.with_name(f"{path.name}.backup-{datetime.now():%Y%m%d-%H%M%S}")
shutil.copy2(path, backup)
source = source[:start] + updated + source[end:]
path.write_text(source.replace("\n", newline), encoding="utf-8", newline="")
print(f"patched; backup: {backup}")