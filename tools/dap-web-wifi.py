#!/usr/bin/python3
"""Restricted root helper for DAP web Wi-Fi scan and connection actions."""

import json
import re
import subprocess
import sys

NMCLI = "/usr/bin/nmcli"
RFKILL = "/usr/sbin/rfkill"
SSID_MAX_BYTES = 32


def emit(value, status=0):
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(status)


def run(command, timeout):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def error_from(result, fallback):
    return (result.stderr or result.stdout or fallback).strip()


def ensure_wifi_ready():
    if run(["/usr/bin/pgrep", "-x", "hostapd"], 3).returncode == 0:
        raise RuntimeError("APモードをOFFにしてからWi-Fiを操作してください")
    unblock = run([RFKILL, "unblock", "wlan"], 10)
    if unblock.returncode != 0:
        raise RuntimeError(error_from(unblock, "Wi-Fi無線を有効にできません"))
    radio = run([NMCLI, "radio", "wifi", "on"], 10)
    if radio.returncode != 0:
        raise RuntimeError(error_from(radio, "Wi-FiをONにできません"))


def parse_fields(line):
    fields, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def scan_networks():
    result = run([
        NMCLI, "-t", "--escape", "yes", "-f", "SSID,SECURITY,SIGNAL",
        "device", "wifi", "list", "ifname", "wlan0", "--rescan", "yes",
    ], 25)
    if result.returncode != 0:
        raise RuntimeError(error_from(result, "Wi-Fi検索に失敗しました"))
    networks, seen = [], set()
    for line in result.stdout.splitlines():
        fields = parse_fields(line)
        if len(fields) < 3 or not fields[0].strip():
            continue
        ssid, security = fields[0].strip(), fields[1].strip()
        try:
            signal = max(0, min(100, int(fields[2])))
        except ValueError:
            signal = 0
        if ssid in seen:
            continue
        seen.add(ssid)
        networks.append({"ssid": ssid, "security": security, "signal": signal})
    networks.sort(key=lambda item: (-item["signal"], item["ssid"].casefold()))
    return networks


def connect_network(request):
    ssid = request.get("ssid")
    password = request.get("password", "")
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= SSID_MAX_BYTES:
        raise ValueError("SSIDはUTF-8で1〜32バイトにしてください")
    if not isinstance(password, str):
        raise ValueError("Wi-Fiパスワードが不正です")
    if password and (not 8 <= len(password) <= 63 or any(ord(char) < 32 or ord(char) > 126 for char in password)):
        raise ValueError("Wi-Fiパスワードは印字可能なASCIIで8〜63文字にしてください")
    command = [NMCLI, "--wait", "45", "device", "wifi", "connect", ssid]
    if password:
        command.extend(["password", password])
    command.extend(["ifname", "wlan0"])
    result = run(command, 55)
    if result.returncode != 0:
        raise RuntimeError(error_from(result, "Wi-Fi接続に失敗しました"))
    return {"ok": True, "ssid": ssid, "message": "接続先を保存しました"}


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("JSONオブジェクトを指定してください")
        action = request.get("action")
        if action not in {"scan", "connect"}:
            raise ValueError("許可されていないWi-Fi操作です")
        ensure_wifi_ready()
        if action == "scan":
            emit({"ok": True, "networks": scan_networks()})
        emit(connect_network(request))
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        emit({"ok": False, "error": str(error)}, 1)


if __name__ == "__main__":
    main()
