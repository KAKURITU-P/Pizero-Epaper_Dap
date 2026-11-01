#!/usr/bin/python3
"""Restricted root helper for DAP web Wi-Fi scan and connection actions."""

import json
import hashlib
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
    security = request.get("security", "")
    password = request.get("password", "")
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= SSID_MAX_BYTES:
        raise ValueError("SSIDはUTF-8で1〜32バイトにしてください")
    if not isinstance(password, str):
        raise ValueError("Wi-Fiパスワードが不正です")
    if not isinstance(security, str):
        raise ValueError("Wi-Fi暗号方式が不正です")
    if password and (not 8 <= len(password) <= 63 or any(ord(char) < 32 or ord(char) > 126 for char in password)):
        raise ValueError("Wi-Fiパスワードは印字可能なASCIIで8〜63文字にしてください")
    security_upper = security.upper()
    if not security_upper or security_upper in {"--", "NONE"}:
        key_mgmt = "none"
        if password:
            raise ValueError("オープンネットワークではパスワードを空欄にしてください")
    elif "WPA3" in security_upper and "WPA2" not in security_upper:
        key_mgmt = "sae"
    elif "WPA2" in security_upper or "WPA" in security_upper:
        key_mgmt = "wpa-psk"
    else:
        raise ValueError("このWi-Fi暗号方式には対応していません。WPA2/WPA3-Personalまたはオープンネットワークを選んでください")
    if key_mgmt != "none" and not password:
        raise ValueError("Wi-Fiパスワードを入力してください")

    profile_id = "DAP-Web-WiFi-" + hashlib.sha256(ssid.encode("utf-8")).hexdigest()[:12]
    profile = run([NMCLI, "-g", "UUID", "connection", "show", "id", profile_id], 8)
    settings = [
        "connection.id", profile_id,
        "connection.interface-name", "wlan0",
        "connection.autoconnect", "yes",
        "802-11-wireless.ssid", ssid,
        "wifi-sec.key-mgmt", key_mgmt,
        "ipv4.method", "auto",
        "ipv6.method", "auto",
    ]
    if key_mgmt != "none":
        settings.extend(["wifi-sec.psk", password])
    else:
        settings.extend(["wifi-sec.psk", ""])
    if profile.returncode == 0 and profile.stdout.strip():
        profile_uuid = profile.stdout.strip().splitlines()[0]
        result = run([NMCLI, "connection", "modify", "uuid", profile_uuid, *settings], 10)
    else:
        result = run([NMCLI, "connection", "add", "type", "wifi", *settings], 10)
    if result.returncode != 0:
        raise RuntimeError(error_from(result, "Wi-Fiプロファイルを作成できませんでした"))
    if profile.returncode != 0 or not profile.stdout.strip():
        lookup = run([NMCLI, "-g", "UUID", "connection", "show", "id", profile_id], 8)
        if lookup.returncode != 0 or not lookup.stdout.strip():
            raise RuntimeError(error_from(lookup, "作成したWi-Fiプロファイルを取得できませんでした"))
        profile_uuid = lookup.stdout.strip().splitlines()[0]
    result = run([NMCLI, "--wait", "45", "connection", "up", "uuid", profile_uuid, "ifname", "wlan0"], 55)
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
