#!/usr/bin/env python3
"""Local-only HTTP API for the authenticated DAP management page."""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
AP_CONFIG = BASE_DIR / "ap_config.json"
PAGE_FILE = BASE_DIR / "web_admin" / "index.html"
PHOTO_FILE = BASE_DIR / "ships.png"
WEB_PHOTO_FILE = BASE_DIR / "web.png"
GITHUB_REPOSITORY = "KAKURITU-P/Pizero-Epaper_Dap"
GITHUB_RELEASES_URL = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
GITHUB_IMAGE_URL_PATTERN = re.compile(
    r"https://github\.com/user-attachments/assets/"
    r"[0-9a-fA-F-]+"
)
WIFI_JOB = {"state": "idle", "ssid": "", "message": ""}
WIFI_JOB_LOCK = threading.Lock()
FAVORITES_LOCK = threading.Lock()
SSID_MAX_BYTES = 32
MUSIC_DIR = Path("/home/pi/music")
FAVORITES_FILE = BASE_DIR / "favorites.json"
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
GITHUB_UPDATE = {
    "state": "idle",
    "message": "",
    "remote": "",
    "branch": "",
    "current": {},
    "latest": {},
}
GITHUB_UPDATE_LOCK = threading.Lock()


def get_latest_release_image_url():
    request = Request(
        GITHUB_RELEASES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Pizero-Epaper_Dap-web-admin",
        },
    )
    try:
        with urlopen(request, timeout=8) as response:
            release = json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as error:
        raise RuntimeError(f"最新リリース画像の取得に失敗しました: {error}") from error

    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise RuntimeError("最新リリースの添付ファイル情報が不正です")

    body = release.get("body", "")
    if isinstance(body, str):
        body_image = GITHUB_IMAGE_URL_PATTERN.search(body)
        if body_image:
            return body_image.group(0)

    image_extensions = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        download_url = asset.get("browser_download_url")
        if (
            isinstance(name, str)
            and Path(name).suffix.lower() in image_extensions
            and isinstance(download_url, str)
        ):
            return download_url
    raise RuntimeError("最新リリースに画像の添付ファイルがありません")


def run(command, timeout=8):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def command_output(command, timeout=8):
    try:
        result = run(command, timeout)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def get_github_update():
    with GITHUB_UPDATE_LOCK:
        return dict(GITHUB_UPDATE)


def set_github_update(state, message):
    with GITHUB_UPDATE_LOCK:
        GITHUB_UPDATE.update({"state": state, "message": message})


def git_revision_info(fetch=False):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    def run_git(args, timeout=15):
        return subprocess.run(
            ["git", "-C", str(BASE_DIR), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )

    remote = run_git(["remote", "get-url", "origin"])
    current = run_git(["log", "-1", "--format=%H%x00%h%x00%s"])
    branch = run_git(["branch", "--show-current"])
    if remote.returncode != 0 or current.returncode != 0:
        raise RuntimeError("Gitリポジトリ情報を取得できません")

    def revision(result):
        full, short, subject = result.stdout.strip().split("\x00", 2)
        return {"hash": short, "full_hash": full, "subject": subject}

    info = {
        "remote": remote.stdout.strip(),
        "branch": branch.stdout.strip() or "(detached HEAD)",
        "current": revision(current),
        "latest": None,
    }
    if fetch:
        fetched = run_git(["fetch", "--quiet", "origin", "main"], timeout=120)
        if fetched.returncode != 0:
            raise RuntimeError((fetched.stderr or "GitHubからの取得に失敗しました").strip()[-180:])
        latest = run_git(["log", "-1", "--format=%H%x00%h%x00%s", "FETCH_HEAD"])
        if latest.returncode != 0:
            raise RuntimeError("GitHubの最新コミットを取得できません")
        info["latest"] = revision(latest)
    return info


def refresh_github_info():
    info = git_revision_info(fetch=True)
    with GITHUB_UPDATE_LOCK:
        GITHUB_UPDATE.update(info)
        if info["current"]["full_hash"] == info["latest"]["full_hash"]:
            GITHUB_UPDATE.update({"state": "latest", "message": "GitHubは最新です"})
        else:
            GITHUB_UPDATE.update({"state": "ready", "message": "更新があります"})
    return get_github_update()


def github_update_worker():
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    def run_git(args, timeout=120):
        return subprocess.run(
            ["git", "-C", str(BASE_DIR), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )

    try:
        remote = run_git(["remote", "get-url", "origin"], timeout=15)
        remote_url = remote.stdout.strip().lower().removesuffix(".git")
        if remote.returncode != 0 or "github.com/kakuritu-p/pizero-epaper_dap" not in remote_url:
            raise RuntimeError("登録されたGitHub接続先が想定と異なるため中止しました")

        fetched = run_git(["fetch", "--quiet", "origin", "main"])
        if fetched.returncode != 0:
            raise RuntimeError((fetched.stderr or "GitHubからの取得に失敗しました").strip()[-180:])

        head = run_git(["rev-parse", "HEAD"], timeout=15)
        target = run_git(["rev-parse", "FETCH_HEAD"], timeout=15)
        if head.returncode != 0 or target.returncode != 0:
            raise RuntimeError("更新対象の版を確認できません")
        if head.stdout.strip() == target.stdout.strip():
            refresh_github_info()
            set_github_update("latest", "GitHubは最新です")
            return

        ancestor = run_git(["merge-base", "--is-ancestor", target.stdout.strip(), "HEAD"], timeout=15)
        if ancestor.returncode == 0:
            refresh_github_info()
            set_github_update("latest", "GitHubは最新です")
            return

        local_changes = run_git(["status", "--porcelain", "--untracked-files=all"], timeout=15)
        if local_changes.returncode != 0:
            raise RuntimeError("ローカル変更を確認できません")
        if local_changes.stdout.strip():
            stashed = run_git([
                "stash", "push", "--include-untracked",
                "-m", "DAP local changes before GitHub update",
            ])
            if stashed.returncode != 0:
                detail = (stashed.stderr or stashed.stdout or "ローカル変更を退避できませんでした").strip()[-180:]
                raise RuntimeError(detail)

        merged = run_git(["merge", "--ff-only", "--no-edit", target.stdout.strip()])
        if merged.returncode != 0:
            detail = (merged.stderr or merged.stdout or "安全に更新できませんでした").strip()[-180:]
            raise RuntimeError(detail)

        message = "更新しました。本体と管理画面を再起動します"
        if local_changes.stdout.strip():
            message += "（ローカル変更はGitのstashに退避済み）"
        set_github_update("restarting", message)
        dap_restart = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "dap.service"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        if dap_restart.returncode != 0:
            detail = (dap_restart.stderr or dap_restart.stdout or "本体サービスを再起動できません").strip()[-180:]
            raise RuntimeError(detail)
        time.sleep(1.5)
        os.execv(sys.executable, [sys.executable, str(BASE_DIR / "web_admin_server.py")])
    except Exception as error:
        print(f"GitHub update error: {error}")
        set_github_update("error", f"更新失敗: {str(error)[:120]}")


def start_github_update():
    with GITHUB_UPDATE_LOCK:
        if GITHUB_UPDATE["state"] == "running":
            raise RuntimeError("別の更新処理が進行中です")
        GITHUB_UPDATE.update({"state": "running", "message": "GitHubから更新データを取得しています…"})
    threading.Thread(target=github_update_worker, daemon=True).start()


def get_bluetooth_codec(bluetooth_devices):
    connected_macs = re.findall(
        r"^Device\s+([0-9A-Fa-f:]{17})\s",
        bluetooth_devices,
        re.MULTILINE,
    )
    if not connected_macs:
        return "未接続"
    try:
        tree = run(["busctl", "--system", "tree", "org.bluealsa"], timeout=4)
        if tree.returncode != 0:
            return "取得待ち"
        for mac in connected_macs:
            device_path = f"/dev_{mac.replace(':', '_').lower()}/"
            for line in tree.stdout.splitlines():
                match = re.search(r"(/org/bluealsa(?:/[A-Za-z0-9_]+)+)", line)
                if not match:
                    continue
                pcm_path = match.group(1)
                if device_path not in pcm_path.lower() or not pcm_path.lower().endswith("/a2dpsrc/sink"):
                    continue
                result = run(
                    [
                        "busctl", "--system", "get-property",
                        "org.bluealsa", pcm_path,
                        "org.bluealsa.PCM1", "Codec",
                    ],
                    timeout=4,
                )
                value_type, _, value = result.stdout.strip().partition(" ")
                if result.returncode == 0 and value_type == "s":
                    codec = value.strip().strip('"')
                    if codec:
                        return codec.upper()
    except (OSError, subprocess.SubprocessError):
        return "取得待ち"
    return "A2DP待機中"


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


def music_file_path(relative_path):
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("音楽ファイルを指定してください")
    candidate = (MUSIC_DIR / relative_path).resolve()
    try:
        candidate.relative_to(MUSIC_DIR.resolve())
    except ValueError as error:
        raise ValueError("音楽フォルダー外のパスは指定できません") from error
    if candidate.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS or not candidate.is_file():
        raise ValueError("存在する対応音楽ファイルを指定してください")
    return candidate


def read_favorites():
    try:
        value = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"お気に入りを読み込めません: {error}") from error
    if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
        raise RuntimeError("お気に入りデータの形式が不正です")
    return value


def write_favorites(favorites):
    temp_fd, temp_name = tempfile.mkstemp(prefix=".favorites.", dir=FAVORITES_FILE.parent)
    try:
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_file:
            json.dump(favorites, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
        os.replace(temp_name, FAVORITES_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def get_favorites():
    with FAVORITES_LOCK:
        favorite_order = read_favorites()
    favorite_set = set(favorite_order)
    tracks = []
    track_by_path = {}
    for path in sorted(MUSIC_DIR.rglob("*"), key=lambda item: str(item).lower()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
            absolute = str(path.resolve())
            track = {
                "path": str(path.relative_to(MUSIC_DIR)),
                "name": path.stem,
                "favorite": absolute in favorite_set,
            }
            tracks.append(track)
            track_by_path[absolute] = track
    ordered_favorites = [
        track_by_path[path] for path in favorite_order if path in track_by_path
    ]
    return {"tracks": tracks, "favorites": ordered_favorites}


def update_favorite(relative_path, favorite):
    if not isinstance(favorite, bool):
        raise ValueError("お気に入り状態が不正です")
    path = music_file_path(relative_path)
    absolute = str(path)
    with FAVORITES_LOCK:
        favorites = read_favorites()
        favorites = [item for item in favorites if item != absolute]
        if favorite:
            favorites.append(absolute)
        write_favorites(favorites)
    return {"path": relative_path, "favorite": favorite}


def move_favorite(relative_path, direction):
    path = str(music_file_path(relative_path))
    if direction not in {"up", "down"}:
        raise ValueError("移動方向が不正です")
    with FAVORITES_LOCK:
        favorites = read_favorites()
        try:
            index = favorites.index(path)
        except ValueError as error:
            raise ValueError("指定した曲はお気に入りに登録されていません") from error
        target = index - 1 if direction == "up" else index + 1
        if 0 <= target < len(favorites):
            favorites[index], favorites[target] = favorites[target], favorites[index]
            write_favorites(favorites)
    return {"path": relative_path, "direction": direction}


def save_favorite_order(relative_paths):
    if not isinstance(relative_paths, list) or not all(isinstance(path, str) for path in relative_paths):
        raise ValueError("お気に入り順の形式が不正です")
    favorites = []
    seen = set()
    for relative_path in relative_paths:
        absolute = str(music_file_path(relative_path))
        if absolute not in seen:
            favorites.append(absolute)
            seen.add(absolute)
    with FAVORITES_LOCK:
        write_favorites(favorites)
    return {"favorites": relative_paths}


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
    codec_info = get_bluetooth_codec(bluetooth_devices)
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
            elif path in {"/ships.png", "/web.png"}:
                photo_file = WEB_PHOTO_FILE if path == "/web.png" and WEB_PHOTO_FILE.is_file() else PHOTO_FILE
                body = photo_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/latest-release-image":
                image_url = get_latest_release_image_url()
                self.send_response(302)
                self.send_header("Location", image_url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
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
            elif path == "/api/update":
                self.send_json(get_github_update())
            elif path == "/api/update/check":
                self.send_json(refresh_github_info())
            elif path == "/api/favorites":
                self.send_json(get_favorites())
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
            elif path == "/api/update":
                start_github_update()
                self.send_json(get_github_update(), 202)
            elif path == "/api/favorites":
                if "favorites" in payload:
                    self.send_json(save_favorite_order(payload.get("favorites")))
                elif "move" in payload:
                    self.send_json(move_favorite(payload.get("path"), payload.get("move")))
                else:
                    self.send_json(update_favorite(payload.get("path"), payload.get("favorite")))
            else:
                self.send_json({"error": "見つかりません"}, 404)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)

    def log_message(self, format_string, *args):
        print(f"web-admin: {self.address_string()} {format_string % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8080), Handler)
    server.serve_forever()
