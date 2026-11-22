#!/usr/bin/python3
"""Restricted root helper for DAP web Wi-Fi profile management and scanning."""

import json
import hashlib
import subprocess
import sys

NMCLI = "/usr/bin/nmcli"
RFKILL = "/usr/sbin/rfkill"
WLAN_IFACE = "wlan0"
SSID_MAX_BYTES = 32
PROFILE_PREFIX = "DAP-Web-WiFi-"


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
        "device", "wifi", "list", "ifname", WLAN_IFACE, "--rescan", "yes",
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


def profile_ssid(name):
    """プロファイル名から実際のSSIDを取得する(失敗時はプロファイル名)。"""
    detail = run([NMCLI, "-g", "802-11-wireless.ssid", "connection", "show", "id", name], 8)
    if detail.returncode == 0 and detail.stdout.strip():
        return detail.stdout.strip().splitlines()[0]
    return name


def list_profiles(request):
    """保存済みWi-Fiプロファイルの一覧(wifiタイプのみ)を返す。APモード中も使用可。"""
    result = run([NMCLI, "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"], 10)
    if result.returncode != 0:
        raise RuntimeError(error_from(result, "Wi-Fiプロファイル一覧を取得できませんでした"))
    profiles, seen = [], set()
    for line in result.stdout.splitlines():
        fields = parse_fields(line)
        if len(fields) < 3:
            continue
        name, conn_type, device = fields[0], fields[1], fields[2]
        if "wireless" not in conn_type or not name or name in seen:
            continue
        seen.add(name)
        profiles.append({
            "name": name,
            "ssid": profile_ssid(name),
            "active": bool(device.strip()),
        })
    profiles.sort(key=lambda item: (not item["active"], item["ssid"].casefold()))
    return profiles


def require_wifi_profile(name):
    """対象プロファイルが存在しwifiタイプであることを確認する。"""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("プロファイル名が指定されていません")
    result = run([NMCLI, "-t", "-f", "NAME,TYPE", "connection", "show", "id", name.strip()], 8)
    if result.returncode != 0:
        raise ValueError("指定のWi-Fiプロファイルが見つかりません")
    for line in result.stdout.splitlines():
        fields = parse_fields(line)
        if len(fields) >= 2 and fields[0] == name.strip() and "wireless" in fields[1]:
            return name.strip()
    raise ValueError("指定のプロファイルはWi-Fiではありません")


def resolve_key_mgmt(security, password):
    """暗号方式の表記からNMのkey-mgmtを決める(open/WPA2/WPA3)。"""
    if not isinstance(security, str):
        raise ValueError("Wi-Fi暗号方式が不正です")
    security_upper = security.upper()
    if not security_upper or security_upper in {"--", "NONE", "OPEN"}:
        if password:
            raise ValueError("オープンネットワークではパスワードを空欄にしてください")
        return "none"
    if "WPA3" in security_upper and "WPA2" not in security_upper:
        return "sae"
    if "WPA2" in security_upper or "WPA" in security_upper:
        return "wpa-psk"
    raise ValueError("このWi-Fi暗号方式には対応していません。WPA2/WPA3-Personalまたはオープンネットワークを選んでください")


def validate_ssid(ssid):
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= SSID_MAX_BYTES:
        raise ValueError("SSIDはUTF-8で1〜32バイトにしてください")
    return ssid


def validate_password_text(password):
    if not isinstance(password, str):
        raise ValueError("Wi-Fiパスワードが不正です")
    if not 8 <= len(password) <= 63 or any(ord(char) < 32 or ord(char) > 126 for char in password):
        raise ValueError("Wi-Fiパスワードは印字可能なASCIIで8〜63文字にしてください")
    return password


def save_profile(request):
    """SSID+パスワードからプロファイルを作成/更新する(無線操作なし・APモード中も可)。"""
    ssid = validate_ssid(request.get("ssid"))
    security = request.get("security", "")
    password = request.get("password", "")
    key_mgmt = resolve_key_mgmt(security, password)
    if key_mgmt != "none":
        password = validate_password_text(password)

    profile_id = PROFILE_PREFIX + hashlib.sha256(ssid.encode("utf-8")).hexdigest()[:12]
    existing = run([NMCLI, "-g", "connection.uuid", "connection", "show", "id", profile_id], 8)
    settings = [
        "connection.id", profile_id,
        "connection.interface-name", WLAN_IFACE,
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
    if existing.returncode == 0 and existing.stdout.strip():
        profile_uuid = existing.stdout.strip().splitlines()[0]
        result = run([NMCLI, "connection", "modify", "uuid", profile_uuid, *settings], 10)
        message = f"{ssid} を更新しました"
    else:
        result = run([NMCLI, "connection", "add", "type", "wifi", *settings], 10)
        message = f"{ssid} を保存しました"
    if result.returncode != 0:
        raise RuntimeError(error_from(result, "Wi-Fiプロファイルを保存できませんでした"))
    return {"ok": True, "name": profile_id, "message": message}


def change_password(request):
    """保存済みプロファイルのパスワードを変更する(APモード中も可)。"""
    name = require_wifi_profile(request.get("name"))
    password = validate_password_text(request.get("password"))
    result = run([NMCLI, "connection", "modify", "id", name,
                  "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password], 10)
    if result.returncode != 0:
        raise RuntimeError(error_from(result, "パスワードを変更できませんでした"))
    return {"ok": True, "message": f"{profile_ssid(name)} のパスワードを更新しました"}


def delete_profile(request):
    """保存済みWi-Fiプロファイルを削除する(APモード中も可)。"""
    name = require_wifi_profile(request.get("name"))
    ssid = profile_ssid(name)
    result = run([NMCLI, "connection", "delete", "id", name], 10)
    if result.returncode != 0:
        raise RuntimeError(error_from(result, "プロファイルを削除できませんでした"))
    return {"ok": True, "message": f"{ssid} を削除しました"}


def show_profile(request):
    """保存済みプロファイルのパスワードを返す(APモード中も可)。"""
    name = require_wifi_profile(request.get("name"))
    result = run([NMCLI, "-s", "-t", "-f",
                  "802-11-wireless-security.key-mgmt,802-11-wireless-security.psk",
                  "connection", "show", "id", name], 8)
    if result.returncode != 0:
        raise RuntimeError(error_from(result, "パスワードを取得できませんでした"))
    key_mgmt, psk = "", ""
    for line in result.stdout.splitlines():
        fields = parse_fields(line)
        if len(fields) < 2:
            continue
        if fields[0].endswith("key-mgmt"):
            key_mgmt = fields[1]
        elif fields[0].endswith("psk"):
            psk = fields[1]
    if key_mgmt == "none" or not psk:
        return {"ok": True, "psk": "", "open": True}
    return {"ok": True, "psk": psk, "open": False}


def activate_profile(request):
    """保存済みプロファイルへ接続する(APモード中は不可)。"""
    name = require_wifi_profile(request.get("name"))
    ensure_wifi_ready()
    result = run([NMCLI, "--wait", "45", "connection", "up", "id", name,
                  "ifname", WLAN_IFACE], 55)
    if result.returncode != 0:
        raise RuntimeError(error_from(result, "Wi-Fi接続に失敗しました"))
    return {"ok": True, "message": f"{profile_ssid(name)} に接続しました"}


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("JSONオブジェクトを指定してください")
        action = request.get("action")
        actions = {
            "scan": scan_networks,
            "list": list_profiles,
            "add": save_profile,
            "password": change_password,
            "delete": delete_profile,
            "show": show_profile,
            "activate": activate_profile,
        }
        if action not in actions:
            raise ValueError("許可されていないWi-Fi操作です")
        if action == "scan":
            # スキャンだけはAPモードと排他。プロファイル管理はAPモード中でも許可する。
            ensure_wifi_ready()
            emit({"ok": True, "networks": scan_networks()})
        emit(actions[action](request))
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        emit({"ok": False, "error": str(error)}, 1)


if __name__ == "__main__":
    main()
