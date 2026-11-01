#!/usr/bin/env python3
"""Local-only HTTP API for the authenticated DAP management page."""

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
AP_CONFIG = BASE_DIR / "ap_config.json"
PAGE_FILE = BASE_DIR / "web_admin" / "index.html"
WIFI_JOB = {"state": "idle", "ssid": "", "message": ""}
WIFI_JOB_LOCK = threading.Lock()
SSID_MAX_BYTES = 32


def run(command, timeout=8):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def command_output(command, timeout=8):
    try:
        result = run(command, timeout)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def ap_is_active():
    try:
        return run(["pgrep", "-x", "hostapd"], timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def read_active_ap_config():
    try:
        active = {}
        for line in Path("/tmp/dap_hostapd.conf").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"ssid", "wpa_passphrase", "country_code", "channel"}:
                active[key] = value
        return active
    except OSError:
        return {}


def ap_restart_required(config, active):
    if not active:
        return False
    runtime = read_active_ap_config()
    return bool(runtime and (
        runtime.get("ssid") != config["ssid"]
        or runtime.get("wpa_passphrase") != config["password"]
        or runtime.get("country_code") != config["country"]
        or runtime.get("channel") != str(config["channel"])
    ))


def read_ap_config():
    try:
        value = json.loads(AP_CONFIG.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("設定形式が不正です")
        return {
            "ssid": str(value.get("ssid", "PiZero-DAP")),
            "password": str(value.get("password", "dap-connect-1234")),
            "country": str(value.get("country", "JP")),
            "channel": int(value.get("channel", 6)),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"AP設定を読み込めません: {error}") from error


def wifi_escape(value):
    return re.sub(r'([\\;,\"])', r"\\\1", value)


def wifi_qr_svg(config):
    import qrcode
    import qrcode.image.svg

    payload = (
        f"WIFI:T:WPA;S:{wifi_escape(config['ssid'])};"
        f"P:{wifi_escape(config['password'])};;"
    )
    qr = qrcode.QRCode(box_size=5, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    output = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(output)
    return output.getvalue()


def run_wifi_helper(request, timeout=30):
    result = subprocess.run(
        ["sudo", "-n", "/usr/local/sbin/dap-web-wifi"],
        input=json.dumps(request, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError((result.stderr or "Wi-Fi操作ヘルパーの応答を読めません").strip()) from error
    if result.returncode != 0 or not response.get("ok"):
        raise RuntimeError(response.get("error") or result.stderr.strip() or "Wi-Fi操作に失敗しました")
    return response


def scan_wifi():
    if ap_is_active():
        raise RuntimeError("APモード中はWi-Fi一覧を検索できません。APをOFFにしてください")
    return run_wifi_helper({"action": "scan"}, timeout=35).get("networks", [])


def connect_wifi_worker(ssid, security, password):
    global WIFI_JOB
    try:
        result = run_wifi_helper({"action": "connect", "ssid": ssid, "security": security, "password": password}, timeout=60)
        with WIFI_JOB_LOCK:
            WIFI_JOB = {"state": "success", "ssid": ssid, "message": result.get("message", "接続先を保存しました")}
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        with WIFI_JOB_LOCK:
            WIFI_JOB = {"state": "error", "ssid": ssid, "message": str(error)}


def start_wifi_connect(ssid, security, password):
    global WIFI_JOB
    with WIFI_JOB_LOCK:
        if WIFI_JOB["state"] == "running":
            raise RuntimeError("別のWi-Fi接続処理が進行中です")
        WIFI_JOB = {"state": "running", "ssid": ssid, "message": "接続しています"}
    threading.Thread(target=connect_wifi_worker, args=(ssid, security, password), daemon=True).start()


def save_ap_config(config):
    ssid = config.get("ssid", "")
    password = config.get("password", "")
    country = config.get("country", "JP")
    if not isinstance(country, str):
        raise ValueError("国コードは2文字で指定してください")
    country = country.upper()
    try:
        channel = int(config.get("channel", 6))
    except (TypeError, ValueError) as error:
        raise ValueError("チャンネルは1〜13の整数にしてください") from error
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= SSID_MAX_BYTES or "\n" in ssid or "\r" in ssid:
        raise ValueError("SSIDはUTF-8で1〜32バイトにしてください")
    if not isinstance(password, str) or not 8 <= len(password) <= 63 or any(ord(char) < 32 or ord(char) > 126 for char in password):
        raise ValueError("パスワードは印字可能なASCIIで8〜63文字にしてください")
    if not re.fullmatch(r"[A-Z]{2}", country) or not 1 <= channel <= 13:
        raise ValueError("国コードまたはチャンネルが不正です")
    content = {"ssid": ssid, "password": password, "country": country, "channel": channel}
    temp_fd, temp_name = tempfile.mkstemp(prefix=".ap_config.", dir=AP_CONFIG.parent)
    try:
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_file:
            json.dump(content, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
        os.replace(temp_name, AP_CONFIG)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return content


def get_status():
    try:
        storage = shutil.disk_usage("/home/pi/music")
        storage_info = {"used": storage.used, "total": storage.total,
                        "percent": round(storage.used * 100 / storage.total, 1) if storage.total else 0}
    except OSError:
        storage_info = None
    try:
        battery = Path("/tmp/battery_status").read_text(encoding="ascii").strip()
    except OSError:
        battery = "--%"
    bluetooth = command_output(["systemctl", "is-active", "bluetooth.service"], timeout=4) or "unknown"
    bluetooth_show = command_output(["bluetoothctl", "show"], timeout=5)
    bluetooth_powered = "不明"
    power_match = re.search(r"^\s*Powered:\s*(yes|no)\s*$", bluetooth_show, re.MULTILINE | re.IGNORECASE)
    if power_match:
        bluetooth_powered = "ON" if power_match.group(1).lower() == "yes" else "OFF"
    bluealsa = command_output(["systemctl", "is-active", "bluealsa.service"], timeout=4) or "unknown"
    bluetooth_devices = command_output(["bluetoothctl", "devices", "Connected"], timeout=5)
    wifi_ssid = command_output(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", "wlan0"])
    ip_result = command_output(["ip", "-4", "-o", "addr", "show", "dev", "wlan0"])
    ip_match = re.search(r"\binet\s+(\d+(?:\.\d+){3})/", ip_result)
    codec_info = command_output(["bluealsa-cli", "status"], timeout=5)
    return {
        "ap_active": ap_is_active(),
        "wifi_ssid": wifi_ssid if wifi_ssid and wifi_ssid != "--" else "未接続",
        "wifi_ip": ip_match.group(1) if ip_match else "—",
        "battery": battery,
        "storage": storage_info,
        "bluetooth": bluetooth,
        "bluetooth_powered": bluetooth_powered,
        "bluetooth_devices": bluetooth_devices,
        "bluealsa": bluealsa,
        "codec_info": codec_info,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "DAPAdmin/1.0"

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 16384:
            raise ValueError("リクエストサイズが不正です")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSONオブジェクトを指定してください")
        return value

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                body = PAGE_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/status":
                self.send_json(get_status())
            elif path == "/api/ap":
                config = read_ap_config()
                config["active"] = ap_is_active()
                config["restart_required"] = ap_restart_required(config, config["active"])
                self.send_json(config)
            elif path == "/api/ap/qr.svg":
                config = read_ap_config()
                if ap_is_active():
                    active_config = read_active_ap_config()
                    config["ssid"] = active_config.get("ssid", config["ssid"])
                    config["password"] = active_config.get("wpa_passphrase", config["password"])
                body = wifi_qr_svg(config)
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/wifi/scan":
                self.send_json({"networks": scan_wifi()})
            elif path == "/api/wifi/job":
                with WIFI_JOB_LOCK:
                    self.send_json(dict(WIFI_JOB))
            else:
                self.send_json({"error": "見つかりません"}, 404)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, ImportError) as error:
            self.send_json({"error": str(error)}, 400)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/ap":
                saved = save_ap_config(payload)
                saved["active"] = ap_is_active()
                saved["restart_required"] = ap_restart_required(saved, saved["active"])
                self.send_json(saved)
            elif path == "/api/wifi/connect":
                ssid = payload.get("ssid")
                security = payload.get("security", "")
                password = payload.get("password", "")
                if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= SSID_MAX_BYTES:
                    raise ValueError("SSIDはUTF-8で1〜32バイトにしてください")
                if not isinstance(password, str):
                    raise ValueError("パスワードが不正です")
                if password and (not 8 <= len(password) <= 63 or any(ord(char) < 32 or ord(char) > 126 for char in password)):
                    raise ValueError("Wi-Fiパスワードは印字可能なASCIIで8〜63文字にしてください")
                if not isinstance(security, str):
                    raise ValueError("Wi-Fi暗号方式が不正です")
                start_wifi_connect(ssid, security, password)
                self.send_json({"state": "running", "ssid": ssid}, 202)
            else:
                self.send_json({"error": "見つかりません"}, 404)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)

    def log_message(self, format_string, *args):
        print(f"web-admin: {self.address_string()} {format_string % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8080), Handler)
    server.serve_forever()
