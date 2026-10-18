import smbus2
import sys
import os
import time
import glob
import re
import socket
import random
import subprocess
import threading
import queue
import wave
import signal
import contextlib
import ctypes
import json
import pygame
from PIL import Image, ImageDraw, ImageFont, ImageOps
import waveshare_epd.epd2in13_V4 as epd2in13
from gpiozero import Button
from network_parse_utils import (
    is_valid_bt_mac,
    is_probably_invalid_ssid,
    normalize_bt_name,
    parse_bluetoothctl_devices,
    parse_nmcli_connection_show,
    parse_wpa_cli_list_networks,
    resolve_bt_display_name,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BASE_DIR, "/home/pi/dap"):
    if _path and _path not in sys.path:
        sys.path.append(_path)

def get_git_short_hash():
    """Return the running checkout's short commit ID, if Git metadata exists."""
    try:
        result = subprocess.run(
            ["git", "-C", BASE_DIR, "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        commit = result.stdout.strip()
        if re.fullmatch(r"[0-9a-fA-F]{7,12}", commit):
            return commit
    except (OSError, subprocess.SubprocessError):
        pass
    return "local"

# --- バッテリー情報取得関数 ---
def get_battery_info():
    """バッテリーの % と V を取得"""
    pct_str = "--%"
    volt_str = ""
    try:
        if os.path.exists("/tmp/battery_status"):
            with open("/tmp/battery_status", "r") as f:
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

# --- mutagen によるメタデータ取得 ---
try:
    import mutagen
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# デフォルト接続先 MAC アドレス
connected_bt_mac = "25:02:27:B5:81:BE"

# --- オーディオ出力状態確認 & 初期サービス起動 ---
asoundrc_path = os.path.expanduser("~/.asoundrc")
if os.path.exists(asoundrc_path):
    with open(asoundrc_path, "r") as f:
        content = f.read()
    if "bluealsa" in content:
        audio_output_mode = "BT"
    else:
        audio_output_mode = "PWM"
else:
    audio_output_mode = "PWM"

# 起動時もWi-FiとBluetoothを同時に有効化しない
if audio_output_mode == "BT":
    subprocess.run(["sudo", "rfkill", "block", "wlan"], check=False)
    subprocess.run(["sudo", "rfkill", "unblock", "bluetooth"], check=False)
    subprocess.run(["sudo", "systemctl", "start", "bluealsa"], check=False)
else:
    subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)
    subprocess.run(["sudo", "rfkill", "block", "bluetooth"], check=False)

# --- ALSA Cライブラリ読み込み ---
try:
    asound = ctypes.cdll.LoadLibrary('libasound.so.2')
except Exception:
    asound = None

# --- 1. Pygame オーディオ初期化 ---
os.environ['SDL_AUDIODRIVER'] = 'alsa'

volume = 0.2

def set_cur_volume(vol):
    """0.0~1.0 の線形値を、人間の聴感に合わせた2乗カーブに変換して設定"""
    if pygame.mixer.get_init():
        # 人間の耳に合わせて2乗カーブ（vol ** 2）を適用
        # よりゆるやかにしたい場合は vol ** 3（3乗）にする
        scaled_vol = vol ** 2
        pygame.mixer.music.set_volume(scaled_vol)

def safe_init_audio():
    try:
        if pygame.mixer.get_init():
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
            pygame.mixer.quit()

        if asound:
            try:
                asound.snd_config_update_free_global()
            except Exception:
                pass

        time.sleep(0.5)
        os.environ['SDL_AUDIODRIVER'] = 'alsa'
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=8192)
        set_cur_volume(volume)
        print(f"Audio initialized in [{audio_output_mode}] mode.")
    except Exception as e:
        print(f"Audio init failed ({e}).")
safe_init_audio()

# --- 2. e-Paper 初期化 ---
epd = epd2in13.EPD()

def clean_shutdown_display(message="Power Off..."):
    save_resume_state()
    try:
        pygame.mixer.music.stop()
    except Exception:
        pass

    try:
        epd.init()
        
        img = Image.new('1', (epd.height, epd.width), 255)
        draw = ImageDraw.Draw(img)

        w, h = epd.height, epd.width
        draw.rectangle([0, 0, w, h], fill=255)
        draw.text((20, (h // 2) - 10), message, font=font_title, fill=0)

        epd.display(epd.getbuffer(img))
        time.sleep(1.0)
        
        epd.Clear()
        epd.sleep()
    except Exception as e:
        print(f"Shutdown display error: {e}")

def handle_signal(sig, frame):
    clean_shutdown_display("Shutting down...")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

# --- 3. フォント設定 ---
def find_japanese_font():
    font_candidates = [
        "/usr/share/fonts/truetype/bizud-gothic/BIZUDGothic-Regular.ttf",
        "/usr/share/fonts/truetype/bizud-gothic/BIZUDPGothic-Regular.ttf",
        "/usr/share/fonts/truetype/bizud-gothic/BIZUDGothic-Bold.ttf",
        "/usr/share/fonts/truetype/takao-gothic/TakaoGothic.ttf",
        "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"
    ]
    for path in font_candidates:
        if os.path.exists(path):
            return path

    bizud_fonts = glob.glob("/usr/share/fonts/**/bizud-gothic/*.ttf") + \
                  glob.glob("/usr/share/fonts/**/bizud-gothic/*.otf")
    if bizud_fonts:
        return bizud_fonts[0]

    found = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True) + \
            glob.glob("/usr/share/fonts/**/*.otf", recursive=True) + \
            glob.glob("/usr/share/fonts/**/*.ttc", recursive=True)
    return found[0] if found else None

FONT_PATH = find_japanese_font()

def get_font(size):
    if FONT_PATH:
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            pass
    return ImageFont.load_default()

font_title  = get_font(20)
font_main   = get_font(14)
font_small  = get_font(12)
font_clock  = get_font(30)

# --- 4. 音楽ライブラリ & お気に入り & レジューム ---
MUSIC_DIR = os.path.expanduser("~/music")
DATA_DIR = "/home/pi/dap"
FAV_FILE = os.path.join(DATA_DIR, "favorites.json")
RESUME_FILE = os.path.join(DATA_DIR, "resume.json")
PLAY_DATA_FILE = os.path.join(DATA_DIR, "play_data.json")
PLAYBACK_HISTORY_LIMIT = 30
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
ROOT_REL_DIR = "."

if not os.path.exists(MUSIC_DIR):
    os.makedirs(MUSIC_DIR, exist_ok=True)
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

playlist_original = []
playlist = []
albums_dict = {}
selected_album = None
library_tree = {}
library_menu_entries = []
current_track_idx = 0
favorites_list = []
playback_history = []

# リピートモード: 0: OFF, 1: REPEAT_ONE, 2: REPEAT_ALL
repeat_mode = 0

# スリープタイマー (分)
sleep_timer_minutes = 0
sleep_timer_end_time = 0.0
sleep_timer_pending = False  

def load_favorites():
    global favorites_list
    if os.path.exists(FAV_FILE):
        try:
            with open(FAV_FILE, "r", encoding="utf-8") as f:
                favorites_list = json.load(f)
        except Exception:
            favorites_list = []

def save_favorites():
    try:
        with open(FAV_FILE, "w", encoding="utf-8") as f:
            json.dump(favorites_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Fav save error: {e}")

def load_playback_history():
    global playback_history
    try:
        with open(PLAY_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            history = data.get("history", [])
            if not isinstance(history, list):
                history = []
            playback_history = [path for path in history if isinstance(path, str)]
            last_played = data.get("last_played")
            if isinstance(last_played, str) and last_played not in playback_history:
                playback_history.insert(0, last_played)
            playback_history = playback_history[:PLAYBACK_HISTORY_LIMIT]
    except FileNotFoundError:
        playback_history = []
    except Exception as e:
        playback_history = []
        print(f"Playback history load error: {e}")

def save_playback_history():
    temp_file = PLAY_DATA_FILE + ".tmp"
    try:
        data = {}
        if os.path.exists(PLAY_DATA_FILE):
            with open(PLAY_DATA_FILE, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            if not isinstance(existing_data, dict):
                raise ValueError("play_data.json must contain a JSON object")
            data = existing_data
        data["history"] = playback_history
        if playback_history:
            data["last_played"] = playback_history[0]
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_file, PLAY_DATA_FILE)
    except Exception as e:
        print(f"Playback history save error: {e}")
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass

def get_available_playback_history():
    return [path for path in playback_history if os.path.isfile(path)]

def record_playback_history(filepath):
    """再生した曲を新しい順で保存し、同じ曲の重複を避ける"""
    global playback_history
    updated = [filepath] + [path for path in playback_history if path != filepath]
    updated = updated[:PLAYBACK_HISTORY_LIMIT]
    if updated != playback_history:
        playback_history = updated
        save_playback_history()

# --- お気に入り追加・削除のヘルパー関数 ---
def toggle_favorite_by_path(filepath):
    """指定されたファイルパスをお気に入りに追加/削除する"""
    global favorites_list
    if filepath in favorites_list:
        favorites_list.remove(filepath)
        is_fav = False
    else:
        favorites_list.append(filepath)
        is_fav = True
    save_favorites()
    return is_fav

def show_fav_toast(is_fav):
    """お気に入り変更時の通知メッセージ表示"""
    global status_message
    status_message = "★ Fav 追加" if is_fav else "★ Fav 解除"
    request_display_update(is_full_refresh=False)
    
    def _clear():
        time.sleep(1.5)
        global status_message
        status_message = ""
        request_display_update(is_full_refresh=False)
    threading.Thread(target=_clear, daemon=True).start()

def toggle_favorite_current_track():
    if not playlist or current_track_idx >= len(playlist):
        return False
    return toggle_favorite_by_path(playlist[current_track_idx])

def save_resume_state():
    if not playlist or current_track_idx >= len(playlist):
        return
    try:
        data = {
            "filepath": playlist[current_track_idx],
            "track_idx": current_track_idx
        }
        with open(RESUME_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Resume save error: {e}")

def load_resume_state():
    global current_track_idx
    if os.path.exists(RESUME_FILE):
        try:
            with open(RESUME_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                filepath = data.get("filepath")
                if filepath and filepath in playlist:
                    current_track_idx = playlist.index(filepath)
        except Exception:
            pass

def normalize_rel_dir(rel_dir):
    """MUSIC_DIR基準の相対ディレクトリを正規化し、上位脱出を防ぐ"""
    if not rel_dir or rel_dir in [".", "./"]:
        return ROOT_REL_DIR
    normalized = os.path.normpath(rel_dir)
    if os.path.isabs(normalized) or normalized.startswith(".."):
        return ROOT_REL_DIR
    return normalized

def is_within_music_dir(path):
    music_root = os.path.normpath(MUSIC_DIR)
    candidate = os.path.normpath(path)
    try:
        return os.path.commonpath([music_root, candidate]) == music_root
    except ValueError:
        return False

def has_library_content(rel_dir, content_cache):
    rel_dir = normalize_rel_dir(rel_dir)
    if rel_dir in content_cache:
        return content_cache[rel_dir]
    entry = library_tree.get(rel_dir, {"subdirs": set(), "tracks": []})
    has_content = bool(entry["tracks"])
    if not has_content:
        for child_rel in entry["subdirs"]:
            if has_library_content(child_rel, content_cache):
                has_content = True
                break
    content_cache[rel_dir] = has_content
    return has_content

def get_direct_tracks_in_dir(rel_dir):
    rel_dir = normalize_rel_dir(rel_dir)
    entry = library_tree.get(rel_dir, {"subdirs": set(), "tracks": []})
    return list(entry["tracks"])

def get_tracks_in_subtree(rel_dir):
    rel_dir = normalize_rel_dir(rel_dir)
    if rel_dir == ROOT_REL_DIR:
        return list(playlist_original)

    subtree_tracks = []
    for path in playlist_original:
        rel_path = os.path.normpath(os.path.relpath(path, MUSIC_DIR))
        rel_parent = normalize_rel_dir(os.path.dirname(rel_path))
        if rel_parent == rel_dir or rel_parent.startswith(rel_dir + os.sep):
            subtree_tracks.append(path)
    return subtree_tracks

def get_library_entries(rel_dir, include_folder_play=False):
    rel_dir = normalize_rel_dir(rel_dir)
    entry = library_tree.get(rel_dir, {"subdirs": set(), "tracks": []})

    dir_entries = []
    for child_rel in sorted(entry["subdirs"], key=lambda p: os.path.basename(p).lower()):
        dir_name = os.path.basename(child_rel)
        dir_entries.append({
            "type": "dir",
            "rel_dir": child_rel,
            "display": f"[F] {dir_name}/"
        })

    track_entries = []
    for path in sorted(entry["tracks"], key=lambda p: os.path.basename(p).lower()):
        track_entries.append({
            "type": "track",
            "path": path,
            "display": os.path.splitext(os.path.basename(path))[0]
        })

    entries = dir_entries + track_entries
    if include_folder_play:
        subtree_tracks = get_tracks_in_subtree(rel_dir)
        if subtree_tracks:
            entries.insert(0, {
                "type": "play_folder",
                "rel_dir": rel_dir,
                "display": "[▶] このフォルダ以下を再生"
            })
    return entries

def reload_music_library():
    global playlist_original, playlist, albums_dict, current_track_idx, library_tree

    library_tree = {ROOT_REL_DIR: {"subdirs": set(), "tracks": []}}
    found_files = []
    seen_paths = set()

    for root, dirnames, filenames in os.walk(MUSIC_DIR):
        rel_root = normalize_rel_dir(os.path.relpath(root, MUSIC_DIR))
        library_tree.setdefault(rel_root, {"subdirs": set(), "tracks": []})

        for dirname in dirnames:
            child_rel = normalize_rel_dir(os.path.join(rel_root, dirname))
            library_tree.setdefault(child_rel, {"subdirs": set(), "tracks": []})
            library_tree[rel_root]["subdirs"].add(child_rel)

        for filename in filenames:
            _, ext = os.path.splitext(filename)
            if ext.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
                continue
            full_path = os.path.normpath(os.path.join(root, filename))
            if not is_within_music_dir(full_path):
                continue
            if full_path in seen_paths:
                continue
            seen_paths.add(full_path)
            found_files.append(full_path)
            library_tree[rel_root]["tracks"].append(full_path)

    content_cache = {}
    for rel_dir, entry in list(library_tree.items()):
        entry["subdirs"] = {
            child_rel for child_rel in entry["subdirs"]
            if has_library_content(child_rel, content_cache)
        }
        entry["tracks"].sort(key=lambda p: os.path.basename(p).lower())

    playlist_original = sorted(found_files)
    playlist = list(playlist_original)
    current_track_idx = 0

    albums_dict = {}
    for rel_dir, entry in library_tree.items():
        if entry["tracks"] or entry["subdirs"]:
            albums_dict[rel_dir] = list(entry["tracks"])

load_favorites()
reload_music_library()
load_playback_history()
load_resume_state()

is_playing = False
is_shuffle = False

start_time = 0.0
paused_duration = 0.0
pause_start_time = 0.0
track_paused_time = 0
total_duration_sec = 0
last_drawn_segment = -1

def get_current_sec():
    if not is_playing:
        return int(track_paused_time)
    if start_time == 0:
        return 0
    elapsed = time.time() - start_time - paused_duration
    return max(0, int(elapsed))

# --- 5. 画面状態 & チャタリング対策 ---
current_screen = "PLAY"
last_user_action_time = time.time()
cursor_idx = 0
scroll_offset = 0
menu_items = []

bt_paired_devices = []
bt_scanned_devices = []
saved_wifi_ssids = []
saved_wifi_connections = []
status_message = ""
is_bt_scanning = False

last_btn_times = {}
DEBOUNCE_TIME = 0.25

state_lock = threading.Lock()

# イースターエッグ & EQ 関連変数
easter_click_count = 0
last_easter_click_time = 0.0
is_clock_unlocked = False
is_eq_unlocked = False

eq_bands = ["BASS", "MID", "TREBLE"]
eq_values = {"BASS": 0, "MID": 0, "TREBLE": 0}
eq_cursor = 0

def apply_eq_settings():
    bass = eq_values["BASS"]
    mid = eq_values["MID"]
    treble = eq_values["TREBLE"]

    print(f"[EQ Apply] BASS: {bass}, MID: {mid}, TREBLE: {treble}")

    def db_to_pct(val):
        pct = int(66 + (val * 5.6))
        return max(0, min(100, pct))

    bass_pct = f"{db_to_pct(bass)}%"
    mid_pct = f"{db_to_pct(mid)}%"
    treble_pct = f"{db_to_pct(treble)}%"

    try:
        for band in ["00. 31 Hz", "01. 63 Hz", "02. 125 Hz"]:
            subprocess.run(["amixer", "-D", "equal", "sset", band, bass_pct], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for band in ["03. 250 Hz", "04. 500 Hz", "05. 1 kHz"]:
            subprocess.run(["amixer", "-D", "equal", "sset", band, mid_pct], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for band in ["06. 2 kHz", "07. 4 kHz", "08. 8 kHz", "09. 16 kHz"]:
            subprocess.run(["amixer", "-D", "equal", "sset", band, treble_pct], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    except Exception as e:
        print(f"EQ Control Error: {e}")

random_ships_cache = None
last_cat_clock_minute = -1

def truncate_by_width(text, font, max_px):
    def get_text_width(t):
        if hasattr(font, 'getlength'):
            return font.getlength(t)
        elif hasattr(font, 'getbbox'):
            return font.getbbox(t)[2]
        return len(t) * 8

    if get_text_width(text) <= max_px:
        return text

    ellipsis = "…"
    ellipsis_w = get_text_width(ellipsis)
    target_w = max_px - ellipsis_w

    truncated = ""
    for char in text:
        if get_text_width(truncated + char) > target_w:
            break
        truncated += char

    return truncated + ellipsis

def debounce(btn_name):
    global last_btn_times
    now = time.time()
    last_time = last_btn_times.get(btn_name, 0.0)
    if now - last_time < DEBOUNCE_TIME:
        return False
    last_btn_times[btn_name] = now
    return True

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            res = subprocess.check_output(["ip", "addr", "show", "wlan0"], text=True)
            for line in res.splitlines():
                if "inet " in line:
                    return line.strip().split()[1].split('/')[0]
        except Exception:
            pass
        return "未接続"

def get_bt_codec_info():
    if audio_output_mode != "BT":
        return "PWM出力中"

    if not connected_bt_mac:
        return "未接続"

    mac_path_part = connected_bt_mac.replace(":", "_").lower()
    pcm_path = f"/org/bluealsa/hci0/dev_{mac_path_part}/a2dpsrc/sink"

    try:
        res = subprocess.check_output(
            [
                "gdbus", "call", "--system",
                "--dest", "org.bluealsa",
                "--object-path", pcm_path,
                "--method", "org.freedesktop.DBus.Properties.Get",
                "org.bluealsa.PCM1", "Codec",
            ],
            text=True,
            errors="ignore",
            timeout=1.0,
        )

        parts = res.split("'")
        if len(parts) >= 2 and parts[1].strip():
            return parts[1].strip().upper()

    except (subprocess.SubprocessError, OSError):
        pass

    return "取得待ち"
def get_current_wifi_ssid():
    try:
        res = subprocess.check_output(["iwgetid", "-r"], text=True, errors='ignore')
        ssid = res.strip()
        if ssid:
            return ssid
    except Exception:
        pass
    return None

def is_bt_connected(mac):
    if not is_valid_bt_mac(mac):
        return False
    try:
        res = subprocess.check_output(
            ["bluetoothctl", "info", mac], text=True, errors='ignore', timeout=8
        )
        return any(line.strip() == "Connected: yes" for line in res.splitlines())
    except Exception:
        return False

def get_bt_device_properties(mac):
    """bluetoothctl info のデバイス属性を辞書で返す。"""
    if not is_valid_bt_mac(mac):
        return {}
    try:
        res = subprocess.check_output(
            ["bluetoothctl", "info", mac], text=True, errors='ignore', timeout=8
        )
    except Exception:
        return {}

    properties = {}
    for line in res.splitlines():
        if ":" in line:
            key, value = line.strip().split(":", 1)
            properties[key.strip().lower()] = value.strip()
    return properties

def bt_prop_is_yes(properties, key):
    return (properties.get(key, "") or "").strip().lower() == "yes"

def format_bt_menu_name(name, mac, include_suffix=False):
    base = normalize_bt_name(name) or mac
    if include_suffix and is_valid_bt_mac(mac):
        return f"{base} [{mac[-5:]}]"
    return base

def get_cropped_ship(ship_index, target_height):
    path = "/home/pi/dap/ships.png"
    if not os.path.exists(path):
        return None, None

    try:
        img = Image.open(path).convert("RGBA")
        crops = [
            (30,  15, 480, 90),
            (30, 100, 480, 175),
            (30, 185, 480, 260),
            (30, 270, 480, 345),
            (30, 355, 480, 430),
            (30, 440, 480, 505)
        ]
        if not (0 <= ship_index < len(crops)):
            ship_index = 0

        crop_box = crops[ship_index]
        cropped = img.crop(crop_box)

        bbox = cropped.getbbox()
        if bbox:
            cropped = cropped.crop(bbox)

        aspect_ratio = cropped.width / float(cropped.height)
        new_w = int(target_height * aspect_ratio)
        resized = cropped.resize((new_w, target_height), Image.Resampling.LANCZOS)
        resized = ImageOps.mirror(resized)

        alpha = resized.getchannel("A")
        black_shape = Image.eval(alpha, lambda p: 0 if p > 128 else 255).convert("1")
        mask = alpha.point(lambda p: 255 if p > 128 else 0, mode="1")
        
        return black_shape, mask
    except Exception as e:
        print(f"Ship crop error ({ship_index}): {e}")
        return None, None

def render_cat_clock(image, draw, width, height):
    global random_ships_cache, last_cat_clock_minute
    
    wifi_on, _ = get_rfkill_status()
    current_ssid = get_current_wifi_ssid() if wifi_on else None

    current_min = time.localtime().tm_min
    if random_ships_cache is None or last_cat_clock_minute != current_min:
        last_cat_clock_minute = current_min
        available_presets = [
            [(0, 18, 5),   (4, 8, 115),   (4, 8, 175)],
            [(0, 18, 10),  (2, 14, 120)],
            [(0, 18, 10),  (1, 18, 120)],
            [(1, 18, 5),   (4, 8, 120),   (4, 8, 180)],
            [(1, 18, 10),  (2, 14, 125)],
            [(3, 11, 5),   (4, 8, 95),    (4, 8, 155)],
            [(2, 14, 5),   (3, 11, 105),   (4, 8, 170)]
        ]        
        random_ships_cache = random.choice(available_presets)

    sea_level = height - 8
    draw.rectangle([0, sea_level, width, height], fill=0)
    draw.line([(0, sea_level - 1), (width, sea_level - 1)], fill=255, width=1)

    has_drawn = False
    for idx, target_h, pos_x in random_ships_cache:
        ship_img, mask = get_cropped_ship(idx, target_h)
        if ship_img and mask:
            pos_y = (sea_level - 1) - ship_img.height
            image.paste(ship_img, (pos_x, pos_y), mask)
            has_drawn = True

    if not has_drawn:
        draw.polygon([(20, sea_level - 10), (28, sea_level - 2), (90, sea_level - 2), (100, sea_level - 10)], fill=0)

    pct_str, volt_str = get_battery_info()
    
    draw.rectangle([width - 75, 2, width - 2, 42], outline=0, fill=255)
    if volt_str:
        draw.text((width - 70, 5), volt_str, font=font_small, fill=0)
    draw.text((width - 70, 22), pct_str, font=font_small, fill=0)

    box_x, box_y = 5, 2
    box_w, box_h = 165, 42

    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], outline=0, fill=255)

    if wifi_on and current_ssid:
        draw.rectangle([box_x, box_y, box_x + box_w, box_y + 12], fill=0)
        tag_text = truncate_by_width(f"LINK: {current_ssid}", font_small, box_w - 6)
        draw.text((box_x + 4, box_y + 1), tag_text, font=font_small, fill=255)

        cur_time = time.strftime("%H:%M")
        draw.text((box_x + 12, box_y + 13), cur_time, font=font_clock, fill=0)
    else:
        draw.rectangle([box_x, box_y, box_x + box_w, box_y + 14], fill=0)
        draw.text((box_x + 6, box_y + 1), "[ BASE: KURE ]", font=font_small, fill=255)
        draw.text((box_x + 8, box_y + 18), "LAT 34°14'N", font=font_main, fill=0)

def render_eq_screen(image, draw, width, height):
    draw.text((8, 2), "[ EQUALIZER ]", font=font_main, fill=0)
    draw.line([(0, 18), (width, 18)], fill=0)

    start_x = 25
    bar_width = 32
    spacing = 22
    base_y = 68

    draw.line([(10, base_y), (width - 10, base_y)], fill=0)
    draw.text((2, base_y - 6), "0", font=font_small, fill=0)

    for i, band in enumerate(eq_bands):
        x = start_x + i * (bar_width + spacing)
        val = eq_values[band]
        
        bar_h = val * 4
        if bar_h != 0:
            y1 = base_y
            y2 = base_y - bar_h
            draw.rectangle([x, min(y1, y2), x + bar_width, max(y1, y2)], fill=0)

        if eq_cursor == i:
            draw.polygon([(x + bar_width//2 - 4, 98), (x + bar_width//2 + 4, 98), (x + bar_width//2, 92)], fill=0)

        val_str = f"{'+' if val > 0 else ''}{val}dB"
        draw.text((x - 2, 102), band, font=font_small, fill=0)
        draw.text((x, 22), val_str, font=font_small, fill=0)

    reset_x = start_x + 3 * (bar_width + spacing) - 10
    draw.rectangle([reset_x, 88, reset_x + 42, 108], outline=0, fill=0 if eq_cursor == 3 else 255)
    draw.text((reset_x + 5, 91), "FLAT", font=font_small, fill=255 if eq_cursor == 3 else 0)

# --- 6. 描画ワーカー ---
display_queue = queue.Queue()
pending_full_refresh = False
github_update_in_progress = False

def request_display_update(is_full_refresh=False):
    global pending_full_refresh
    with display_queue.mutex:
        if is_full_refresh:
            pending_full_refresh = True
        display_queue.queue.clear()
    display_queue.put(True)

def _set_github_update_status(message, clear_after=None):
    global status_message
    with state_lock:
        status_message = message
    request_display_update(is_full_refresh=False)

    if clear_after is not None:
        def _clear_status():
            time.sleep(clear_after)
            global status_message
            with state_lock:
                if status_message == message:
                    status_message = ""
            request_display_update(is_full_refresh=False)
        threading.Thread(target=_clear_status, daemon=True).start()


def _github_update_worker():
    global github_update_in_progress
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    def run_git(args, timeout=120):
        return subprocess.run(
            ["git", "-C", BASE_DIR, *args],
            text=True, capture_output=True, timeout=timeout, env=env
        )

    try:
        remote = run_git(["remote", "get-url", "origin"], timeout=15)
        if remote.returncode != 0:
            raise RuntimeError("GitHubの接続先を確認できません")
        remote_url = remote.stdout.strip().lower().removesuffix(".git")
        if "github.com/kakuritu-p/pizero-epaper_dap" not in remote_url:
            raise RuntimeError("登録されたGitHub接続先が想定と異なるため中止しました")

        fetched = run_git(["fetch", "--quiet", "origin", "main"])
        if fetched.returncode != 0:
            raise RuntimeError((fetched.stderr or "取得に失敗しました").strip()[-180:])

        head = run_git(["rev-parse", "HEAD"], timeout=15)
        target = run_git(["rev-parse", "FETCH_HEAD"], timeout=15)
        if head.returncode != 0 or target.returncode != 0:
            raise RuntimeError("更新対象の版を確認できません")
        if head.stdout.strip() == target.stdout.strip():
            _set_github_update_status("GitHubは最新です", clear_after=4)
            return

        already_contains_main = run_git([
            "merge-base", "--is-ancestor", target.stdout.strip(), "HEAD"
        ], timeout=15)
        if already_contains_main.returncode == 0:
            _set_github_update_status("GitHubは最新です", clear_after=4)
            return

        merged = run_git(["merge", "--ff-only", "--no-edit", target.stdout.strip()])
        if merged.returncode != 0:
            detail = (merged.stderr or merged.stdout or "安全に更新できませんでした").strip()[-180:]
            raise RuntimeError(detail)

        patch_script = os.path.join(BASE_DIR, "tools", "patch_waveshare_epd_gpio18_pwr.py")
        epdconfig_path = os.path.join(BASE_DIR, "waveshare_epd", "epdconfig.py")
        if os.path.isfile(patch_script) and os.path.isfile(epdconfig_path):
            patched = subprocess.run(
                [sys.executable, patch_script, epdconfig_path],
                text=True, capture_output=True, timeout=15, env=env
            )
            if patched.returncode != 0:
                raise RuntimeError((patched.stderr or patched.stdout or "画面ドライバーを更新できません").strip()[-180:])

        _set_github_update_status("更新しました。再起動します")
        time.sleep(1.5)
        # Restart this service process in place. Calling systemctl restart from
        # inside the unit can kill this worker/command during the stop phase,
        # and sudo may prompt for a password with no terminal available.
        os.execv(sys.executable, [sys.executable, os.path.join(BASE_DIR, "dap_daemon.py")])
    except Exception as e:
        print(f"GitHub update error: {e}")
        _set_github_update_status(f"更新失敗: {str(e)[:42]}", clear_after=8)
    finally:
        github_update_in_progress = False


def start_github_update():
    global github_update_in_progress, status_message
    if github_update_in_progress:
        return
    github_update_in_progress = True
    status_message = "画面を初期化して更新中..."
    # Full refresh calls epd.init(), which hardware-resets the panel before waiting for BUSY.
    request_display_update(is_full_refresh=True)
    threading.Thread(target=_github_update_worker, daemon=True).start()


def display_worker():
    global pending_full_refresh
    current_epd_mode = None
    partial_refresh_count = 0
    previous_screen = None

    while True:
        try:
            _ = display_queue.get()
            
            with display_queue.mutex:
                is_full = pending_full_refresh
                pending_full_refresh = False

            if partial_refresh_count >= 10:
                is_full = True
                partial_refresh_count = 0

            with state_lock:
                scr = current_screen
                track_idx = current_track_idx
                pl = list(playlist)
                cur_idx = cursor_idx
                sc_off = scroll_offset
                m_items = list(menu_items)
                sel_alb = selected_album
                playing = is_playing
                cur_vol = volume
                stat_msg = status_message

                current_sec = get_current_sec()
                tot_sec = total_duration_sec

            # 画面種別が変わるたび全画面更新し、前画面の残像を消す。
            if scr != previous_screen:
                is_full = True
                partial_refresh_count = 0

            image = Image.new('1', (epd.height, epd.width), 255)
            draw = ImageDraw.Draw(image)

            if scr == "CAT_CLOCK":
                render_cat_clock(image, draw, epd.height, epd.width)

            elif scr == "CAT_EQ":
                render_eq_screen(image, draw, epd.height, epd.width)

            elif scr == "PLAY":
                # モード表示生成 ([1] / [ALL] / [SHUF] 等)
                mode_str = ""
                if repeat_mode == 1: mode_str += " [1]"
                elif repeat_mode == 2: mode_str += " [ALL]"
                if is_shuffle: mode_str += " [SHUF]"

                # スリープタイマー表示生成
                timer_str = ""
                if sleep_timer_end_time > 0:
                    rem_sec = max(0, int(sleep_timer_end_time - time.time()))
                    timer_str = f" T:{rem_sec // 60}m"

                state_str = ("> PLAY" if playing else "|| PAUSE") + mode_str + timer_str
                vol_str = f"VOL: {int(cur_vol * 100)}%"

                draw.text((8, 2), truncate_by_width(state_str, font_main, 160), font=font_main, fill=0)
                draw.text((170, 2), vol_str, font=font_main, fill=0)
                draw.line([(0, 18), (250, 18)], fill=0)

                if pl and track_idx < len(pl):
                    full_path = pl[track_idx]
                    song_filename = os.path.splitext(os.path.basename(full_path))[0]
                    artist_label = get_track_artist_info(full_path)
                    ##if full_path in favorites_list:
                    ##   song_filename = "[★] " + song_filename
                else:
                    song_filename = "曲ファイルがありません"
                    artist_label = "不明なアーティスト"

                disp_title = truncate_by_width(song_filename, font_title, 230)
                disp_artist = truncate_by_width(artist_label, font_small, 230)

                draw.text((8, 23), disp_title, font=font_title, fill=0)
                draw.text((8, 48), disp_artist, font=font_small, fill=0)

                bar_x1, bar_y1 = 8, 70
                bar_x2, bar_y2 = 240, 82
                draw.rectangle([bar_x1, bar_y1, bar_x2, bar_y2], outline=0, fill=255, width=1)

                if tot_sec > 0:
                    progress_ratio = min(1.0, current_sec / float(tot_sec))
                    segment = int(progress_ratio * 8)
                    if segment > 0:
                        max_inner_w = bar_x2 - bar_x1 - 4
                        fill_w = int(max_inner_w * (segment / 8.0))
                        draw.rectangle(
                            [bar_x1 + 2, bar_y1 + 2, bar_x1 + 2 + fill_w, bar_y2 - 2],
                            outline=0, fill=0
                        )

                time_str = format_time_str(tot_sec)
                draw.text((8, 87), time_str, font=font_main, fill=0)

            else:
                header_text = "メニュー"
                if scr == "MENU_SYS": header_text = "メニュー > システム設定"
                elif scr == "MENU_ALBUMS": header_text = "メニュー > ライブラリ"
                elif scr == "MENU_FAVS": header_text = "アルバム > お気に入り"
                elif scr == "MENU_HISTORY": header_text = "アルバム > 再生履歴"
                elif scr == "MENU_TRACKS":
                    rel_label = "ルート" if not sel_alb or sel_alb == ROOT_REL_DIR else sel_alb
                    header_text = truncate_by_width(f"ライブラリ > {rel_label}", font_main, 230)
                elif scr == "MENU_PLAY_SETTINGS": header_text = "メニュー > 再生設定"
                elif scr == "MENU_BT": header_text = "接続設定 > Bluetooth"
                elif scr == "MENU_BT_PAIRED": header_text = "Bluetooth > 登録済み"
                elif scr == "MENU_BT_SCAN": header_text = "Bluetooth > 新規検索"
                elif scr == "MENU_WIFI": header_text = "接続設定 > Wi-Fi"
                elif scr == "MENU_CONN": header_text = "メニュー > 接続設定"
                elif scr == "MENU_SLEEP": header_text = "再生設定 > スリープタイマー"

                draw.text((8, 2), header_text, font=font_main, fill=0)
                draw.line([(0, 18), (250, 18)], fill=0)

                start_y = 20
                line_height = 19
                visible_items = m_items[sc_off : sc_off + 5]
                for i, item in enumerate(visible_items):
                    actual_idx = sc_off + i
                    y = start_y + (i * line_height)
                    prefix = "> " if actual_idx == cur_idx else "  "
                    disp_item = truncate_by_width(f"{prefix}{item}", font_main, 230)
                    draw.text((8, y), disp_item, font=font_main, fill=0)

            if stat_msg:
                lines = stat_msg.split("\n")
                box_h = 18 + (len(lines) * 22)
                box_y1 = max(10, (122 - box_h) // 2)
                draw.rectangle([10, box_y1, 240, box_y1 + box_h], fill=255, outline=0)
                y_offset = box_y1 + 8
                for l in lines:
                    draw.text((15, y_offset), truncate_by_width(l, font_small, 215), font=font_small, fill=0)
                    y_offset += 20

            buf = epd.getbuffer(image)

            if is_full or current_epd_mode is None:
                epd.init()
                epd.display(buf)
                epd.displayPartBaseImage(buf)
                current_epd_mode = "PARTIAL"
                partial_refresh_count = 0
            else:
                if current_epd_mode != "PARTIAL":
                    epd.initPartial()
                    current_epd_mode = "PARTIAL"
                epd.displayPartial(buf)
                partial_refresh_count += 1

            previous_screen = scr
            display_queue.task_done()
        except Exception as e:
            print(f"Display render error: {e}")
            current_epd_mode = None
            time.sleep(0.1)

threading.Thread(target=display_worker, daemon=True).start()

# --- 定期更新スレッド ---
def cat_clock_timer_thread():
    while True:
        time.sleep(10)
        with state_lock:
            if current_screen == "CAT_CLOCK":
                wifi_on, _ = get_rfkill_status()
                if wifi_on:
                    request_display_update(is_full_refresh=False)

threading.Thread(target=cat_clock_timer_thread, daemon=True).start()

# --- 7. Bluetooth 接続 & オーディオ出力切替 ---
def connect_bt_device(mac, name="Unknown"):
    global status_message, connected_bt_mac, audio_output_mode

    if not is_valid_bt_mac(mac):
        print(f"BT接続を中止: 不正なデバイスアドレスです ({mac!r})")
        status_message = "BTアドレス不正"
        request_display_update(is_full_refresh=False)
        return
    mac = mac.upper()

    initial_wifi_on, initial_bt_on = get_rfkill_status()
    if initial_wifi_on:
        print("BT接続を中止: Wi-Fiが有効です")
        status_message = "先にWi-FiをOFFにしてください"
        request_display_update(is_full_refresh=False)
        return

    previous_audio_mode = audio_output_mode
    previous_connected_mac = connected_bt_mac

    def restore_pwm_after_failure():
        global audio_output_mode
        subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)
        subprocess.run(["sudo", "rfkill", "unblock" if initial_wifi_on else "block", "wlan"], check=False)
        subprocess.run(["sudo", "rfkill", "unblock" if initial_bt_on else "block", "bluetooth"], check=False)
        audio_output_mode = "PWM"
        safe_init_audio()

    connected_bt_mac = mac
    status_message = f"接続中: {name[:10]}"
    request_display_update(is_full_refresh=False)

    # 切替時のALSAフリーズ防止のためミキサーを停止
    if pygame.mixer.get_init():
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

    subprocess.run(["sudo", "rfkill", "unblock", "bluetooth"], check=False)
    subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=False)
    subprocess.run(["sudo", "systemctl", "start", "bluealsa"], check=False)
    time.sleep(0.5)

    def run_btctl(*args, timeout=15):
        try:
            result = subprocess.run(
                ["bluetoothctl", *args], capture_output=True, text=True,
                errors="ignore", timeout=timeout, check=False
            )
            output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            if result.returncode != 0 or "failed" in output.lower():
                print(f"bluetoothctl {' '.join(args)}: {output or '失敗'}")
            return result
        except Exception as e:
            print(f"bluetoothctl {' '.join(args)}: {e}")
            return None

    def run_btctl_session(commands, timeout=40):
        """エージェントを維持した同一bluetoothctlセッションでコマンドを実行する。"""
        try:
            result = subprocess.run(
                ["bluetoothctl"],
                input="\n".join([*commands, "quit"]) + "\n",
                capture_output=True, text=True, errors="ignore",
                timeout=timeout, check=False
            )
            output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            if result.returncode != 0 or "failed" in output.lower():
                print(f"bluetoothctl session: {output or '失敗'}")
            return result
        except Exception as e:
            print(f"bluetoothctl session: {e}")
            return None

    run_btctl("power", "on")
    run_btctl("agent", "on")

    properties = get_bt_device_properties(mac)
    requires_user_confirmation = False
    if not bt_prop_is_yes(properties, "paired"):
        # 対話入力不可UIのため KeyboardDisplay を優先し、必要なら NoInputNoOutput にフォールバック。
        pairing_session = run_btctl_session(
            ["power on", "agent on", "agent KeyboardDisplay", "default-agent", f"pair {mac}", f"trust {mac}", f"connect {mac}"],
            timeout=60,
        )
        session_output = ""
        if pairing_session is not None:
            session_output = "\n".join(part for part in (pairing_session.stdout, pairing_session.stderr) if part)
        if not bt_prop_is_yes(get_bt_device_properties(mac), "paired"):
            fallback_session = run_btctl_session(
                ["power on", "agent on", "agent NoInputNoOutput", "default-agent", f"pair {mac}", f"trust {mac}", f"connect {mac}"],
                timeout=60,
            )
            if fallback_session is not None:
                session_output = "\n".join(part for part in (session_output, fallback_session.stdout, fallback_session.stderr) if part)
        lowered_output = session_output.lower()
        if session_output and (
            "confirm passkey" in lowered_output
            or "enter pin code" in lowered_output
            or "request authorization" in lowered_output
            or "authentication" in lowered_output
        ):
            requires_user_confirmation = True
            print("BTペアリング要確認: UI上でPIN/確認入力は非対応のため失敗することがあります")
            print(f"BTペアリングログ: {session_output[-600:]}")
        properties = get_bt_device_properties(mac)

    paired = bt_prop_is_yes(properties, "paired")
    if paired and not bt_prop_is_yes(properties, "trusted"):
        run_btctl("trust", mac)

    if paired and not bt_prop_is_yes(get_bt_device_properties(mac), "connected"):
        run_btctl("connect", mac, timeout=25)

    deadline = time.monotonic() + 12
    while paired and time.monotonic() < deadline and not is_bt_connected(mac):
        time.sleep(0.5)

    connected = paired and is_bt_connected(mac)
    if connected:
        config_content = f"""pcm.!default {{
    type plug
    slave.pcm "equal"
}}

pcm.equal {{
    type equal
    slave.pcm {{
        type bluealsa
        device "{mac}"
        profile "a2dp"
    }}
}}
ctl.equal {{
    type equal
}}
"""
        old_config = None
        config_existed = os.path.exists(asoundrc_path)
        try:
            if config_existed:
                with open(asoundrc_path, "r") as f:
                    old_config = f.read()
            with open(asoundrc_path, "w") as f:
                f.write(config_content)
            subprocess.run(["amixer", "-D", "bluealsa", "sset", "Master", "100%"], check=False)
            audio_output_mode = "BT"
            safe_init_audio()
            apply_eq_settings()
            status_message = "接続成功"
        except Exception as e:
            print(f"BT ALSA設定エラー: {e}")
            audio_output_mode = previous_audio_mode
            connected_bt_mac = previous_connected_mac
            status_message = "音声設定失敗"
            try:
                if config_existed and old_config is not None:
                    with open(asoundrc_path, "w") as f:
                        f.write(old_config)
                elif not config_existed and os.path.exists(asoundrc_path):
                    os.remove(asoundrc_path)
            except Exception as restore_error:
                print(f"ALSA設定の復元エラー: {restore_error}")
            if previous_audio_mode == "PWM":
                restore_pwm_after_failure()
    else:
        status_message = "PIN/確認が必要です" if (not paired and requires_user_confirmation) else ("ペアリング失敗" if not paired else "接続失敗")
        connected_bt_mac = previous_connected_mac
        if previous_audio_mode == "PWM":
            # 接続失敗時はPWMへ戻し、Wi-Fiを戻してSSH再接続を可能にする。
            restore_pwm_after_failure()

    request_display_update(is_full_refresh=True)
    time.sleep(1.5)
    status_message = ""
    update_menu_items()
    request_display_update(is_full_refresh=False)

def set_audio_output(mode, mac=None):
    global audio_output_mode, status_message, connected_bt_mac

    status_message = f"切替中: {mode}"
    request_display_update(is_full_refresh=False)

    if mode == "BT":
        # BTへ切り替えるとWi-Fi接続が切れる。BT開始前にWi-Fiを停止する。
        subprocess.run(["sudo", "rfkill", "block", "wlan"], check=False)
        time.sleep(0.3)
        target_mac = mac or connected_bt_mac
        if target_mac:
            connect_bt_device(target_mac, "Bluetooth")
        else:
            status_message = "MAC未設定"
            request_display_update(is_full_refresh=False)
            time.sleep(1.0)
            status_message = ""

    elif mode == "PWM":
        # PWMへ戻す前にBlueALSAとBluetooth無線を停止する。
        subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)
        subprocess.run(["sudo", "rfkill", "block", "bluetooth"], check=False)
        time.sleep(0.3)

        pwm_config = """pcm.!default {
    type plug
    slave.pcm "equal"
}

pcm.equal {
    type equal
    slave.pcm "plughw:0,0"
}
ctl.equal {
    type equal
}
"""
        try:
            with open(asoundrc_path, "w") as f:
                f.write(pwm_config)
        except Exception as e:
            print(f"asoundrc write error: {e}")

        audio_output_mode = "PWM"
        safe_init_audio()
        apply_eq_settings()
        status_message = "PWMモード設定"
        request_display_update(is_full_refresh=True)
        time.sleep(1.0)
        status_message = ""
        update_menu_items()
        request_display_update(is_full_refresh=False)

# --- 8. Bluetooth / Wi-Fi / AP 関連 ---
def get_rfkill_status():
    wifi_enabled, bt_enabled = True, True
    try:
        res = subprocess.check_output(["sudo", "rfkill", "list"], text=True)
        current_type = None
        for line in res.splitlines():
            line_lower = line.lower()
            if "wlan" in line_lower or "wireless" in line_lower: current_type = "wifi"
            elif "bluetooth" in line_lower: current_type = "bt"

            if "soft blocked: yes" in line_lower:
                if current_type == "wifi": wifi_enabled = False
                elif current_type == "bt": bt_enabled = False
    except Exception: pass
    return wifi_enabled, bt_enabled

def toggle_rfkill(dev_type):
    """Wi-FiとBluetoothを同時に有効化しない"""
    wifi_on, bt_on = get_rfkill_status()
    try:
        if dev_type == "wifi":
            if wifi_on:
                subprocess.run(["sudo", "rfkill", "block", "wlan"], check=False)
            elif bt_on:
                print("Wi-Fiを有効化できません: 先にBluetoothをOFFにしてください")
                return
            else:
                subprocess.run(["sudo", "rfkill", "unblock", "wlan"], check=False)
        else:
            if bt_on:
                subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)
                subprocess.run(["sudo", "rfkill", "block", "bluetooth"], check=False)
            elif wifi_on:
                print("Bluetoothを有効化できません: 先にWi-FiをOFFにしてください")
                return
            else:
                subprocess.run(["sudo", "rfkill", "unblock", "bluetooth"], check=False)
        time.sleep(0.3)
    except Exception as e:
        print(f"[toggle_rfkill] Error: {e}")

def get_ap_status():
    try:
        res = subprocess.run(["pgrep", "-f", "hostapd"], capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False

def toggle_ap_mode():
    def _do_toggle():
        global status_message
        is_active = get_ap_status()
        if not is_active:
            _, bt_on = get_rfkill_status()
            if bt_on:
                print("APモードを中止: 先にBluetoothをOFFにしてください")
                return
        try:
            if is_active:
                status_message = "AP OFF中..."
                request_display_update(is_full_refresh=False)

                subprocess.run(["sudo", "pkill", "-TERM", "hostapd"], check=False)
                subprocess.run(["sudo", "pkill", "-TERM", "dnsmasq"], check=False)
                subprocess.run(["sudo", "systemctl", "restart", "NetworkManager"], check=False)

            else:
                status_message = "AP ON中..."
                request_display_update(is_full_refresh=False)

                subprocess.run(["sudo", "systemctl", "stop", "NetworkManager"], check=False)
                subprocess.run(["sudo", "pkill", "-TERM", "wpa_supplicant"], check=False)
                subprocess.run(["sudo", "pkill", "-TERM", "hostapd"], check=False)
                subprocess.run(["sudo", "pkill", "-TERM", "dnsmasq"], check=False)

                subprocess.run(["sudo", "rfkill", "unblock", "wlan"], check=False)
                subprocess.run(["sudo", "ip", "link", "set", "wlan0", "down"], check=False)
                subprocess.run(["sudo", "ip", "addr", "flush", "dev", "wlan0"], check=False)
                subprocess.run(["sudo", "ip", "link", "set", "wlan0", "up"], check=False)
                subprocess.run(["sudo", "ip", "addr", "add", "192.168.4.1/24", "dev", "wlan0"], check=False)
                time.sleep(0.5)

                dnsmasq_conf = """interface=wlan0
                dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,12h
                dhcp-option=option:router,192.168.4.1
                dhcp-option=option:dns-server,192.168.4.1
                bind-interfaces
                """
                with open("/tmp/dnsmasq_ap.conf", "w") as f:
                    f.write(dnsmasq_conf)

                subprocess.run(["sudo", "dnsmasq", "-C", "/tmp/dnsmasq_ap.conf"], check=False)
                subprocess.Popen(["sudo", "hostapd", "-B", "/etc/hostapd/hostapd.conf"])

            time.sleep(1)
            subprocess.run(["sudo", "systemctl", "restart", "avahi-daemon"], check=False)

            status_message = ""
            update_menu_items()
            request_display_update(is_full_refresh=False)

        except Exception as e:
            print(f"AP Mode toggle error: {e}")

    threading.Thread(target=_do_toggle, daemon=True).start()

def get_saved_wifi_connections():
    merged = {}
    try:
        res = subprocess.check_output(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], text=True, errors="ignore")
        for name, conn_type in parse_nmcli_connection_show(res):
            cleaned = name.strip()
            if conn_type != "802-11-wireless" or cleaned == "Pi-DAP-AP" or is_probably_invalid_ssid(cleaned):
                continue
            merged.setdefault(cleaned, {"ssid": cleaned, "nmcli_id": None, "wpa_id": None})
            merged[cleaned]["nmcli_id"] = name
    except Exception:
        pass

    try:
        res = subprocess.check_output(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], text=True, errors="ignore")
        for network in parse_wpa_cli_list_networks(res):
            ssid_name = (network.get("ssid") or "").strip()
            if ssid_name == "Pi-DAP-AP" or is_probably_invalid_ssid(ssid_name):
                continue
            merged.setdefault(ssid_name, {"ssid": ssid_name, "nmcli_id": None, "wpa_id": None})
            if network.get("id", "").isdigit():
                merged[ssid_name]["wpa_id"] = network["id"]
    except Exception:
        pass

    return list(merged.values())

def get_saved_wifi_ssids():
    return [entry["ssid"] for entry in get_saved_wifi_connections()]

def connect_to_wifi(target):
    def _do_connect():
        global status_message
        target_ssid = ""
        target_nmcli_id = None
        target_wpa_id = None
        if isinstance(target, dict):
            target_ssid = (target.get("ssid") or "").strip()
            target_nmcli_id = (target.get("nmcli_id") or "").strip() or None
            target_wpa_id = (target.get("wpa_id") or "").strip() or None
        else:
            target_ssid = str(target).strip()
            for entry in get_saved_wifi_connections():
                if (entry.get("ssid") or "").strip() == target_ssid:
                    target_nmcli_id = (entry.get("nmcli_id") or "").strip() or None
                    target_wpa_id = (entry.get("wpa_id") or "").strip() or None
                    break

        if not target_ssid:
            return

        _, bt_on = get_rfkill_status()
        if bt_on:
            print("Wi-Fi接続を中止: 先にBluetoothをOFFにしてください")
            return
        status_message = f"接続中: {target_ssid}"
        request_display_update(is_full_refresh=False)
        try:
            subprocess.run(["sudo", "rfkill", "unblock", "wlan"], check=False)
            res = None
            if target_nmcli_id:
                res = subprocess.run(["nmcli", "connection", "up", "id", target_nmcli_id], capture_output=True, text=True)
            if res is None or res.returncode != 0:
                if not target_wpa_id:
                    res_wpa = subprocess.check_output(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], text=True, errors="ignore")
                    for network in parse_wpa_cli_list_networks(res_wpa):
                        if (network.get("ssid") or "").strip() == target_ssid and network.get("id", "").isdigit():
                            target_wpa_id = network["id"]
                            break
                if target_wpa_id:
                    subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "select_network", target_wpa_id], check=False)
        except Exception:
            pass
        time.sleep(3)
        status_message = ""
        update_menu_items()
        request_display_update(is_full_refresh=False)

    threading.Thread(target=_do_connect, daemon=True).start()

def get_track_duration_sec(filepath):
    if not filepath or not os.path.exists(filepath):
        return 0

    if filepath.lower().endswith('.wav'):
        try:
            with contextlib.closing(wave.open(filepath, 'r')) as f:
                return int(f.getnframes() / float(f.getframerate()))
        except Exception:
            pass

    if HAS_MUTAGEN:
        try:
            audio = mutagen.File(filepath)
            if audio and audio.info and hasattr(audio.info, 'length') and audio.info.length > 0:
                return int(audio.info.length)
        except Exception:
            pass

    return 0

def get_track_artist_info(filepath):
    if not filepath or not os.path.exists(filepath): return "不明なアーティスト"
    if HAS_MUTAGEN:
        try:
            audio = mutagen.File(filepath)
            if audio:
                if 'artist' in audio and audio['artist']: return str(audio['artist'][0]).strip()
                elif 'TPE1' in audio and audio['TPE1']: return str(audio['TPE1']).strip()
        except Exception: pass
    rel_path = os.path.relpath(filepath, MUSIC_DIR)
    folder_name = os.path.dirname(rel_path)
    return folder_name if folder_name else "不明なアーティスト"

def format_time_str(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"

# --- Bluetooth 区分処理 ---
def get_bt_paired_devices():
    devices = {}
    for command in (
        ["bluetoothctl", "devices", "Paired"],
        ["bluetoothctl", "paired-devices"],
        ["bluetoothctl", "devices"],
    ):
        try:
            res = subprocess.check_output(
                command, text=True, errors="ignore", timeout=8
            )
        except Exception:
            continue

        parsed = parse_bluetoothctl_devices(res)
        for mac, listed_name in parsed:
            if mac not in devices or (listed_name and not devices[mac]):
                devices[mac] = listed_name
        if devices and command != ["bluetoothctl", "devices"]:
            break

    devs = []
    for mac, listed_name in devices.items():
        properties = get_bt_device_properties(mac)
        if not bt_prop_is_yes(properties, "paired"):
            continue
        name = resolve_bt_display_name(
            mac,
            alias=properties.get("alias"),
            name=properties.get("name"),
            listed_name=listed_name,
        )
        devs.append((mac, name))
    devs.sort(key=lambda item: item[1].lower())
    return devs

def trigger_bt_scan():
    global is_bt_scanning, bt_scanned_devices
    with state_lock:
        if is_bt_scanning:
            return
        is_bt_scanning = True
    def _scan_thread():
        global is_bt_scanning, status_message, bt_scanned_devices
        scanned = []
        wifi_on, _ = get_rfkill_status()
        if wifi_on:
            print("BT検索を中止: Wi-Fiが有効です")
            status_message = "先にWi-FiをOFFにしてください"
            with state_lock:
                is_bt_scanning = False
            request_display_update(is_full_refresh=False)
            return
        status_message = "BT検索中(8秒)..."
        request_display_update(is_full_refresh=False)
        try:
            subprocess.run(["sudo", "rfkill", "unblock", "bluetooth"], check=False)
            bt_service = subprocess.run(["sudo", "systemctl", "is-active", "bluetooth"], capture_output=True, text=True, check=False)
            if bt_service.stdout.strip() != "active":
                subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=False)

            process = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="ignore",
            )
            if process.stdin:
                process.stdin.write("power on\n")
                process.stdin.write("agent on\n")
                process.stdin.write("scan on\n")
                process.stdin.flush()
                time.sleep(8)
                process.stdin.write("devices\n")
                process.stdin.write("scan off\n")
                process.stdin.write("quit\n")
                process.stdin.flush()

            output, _ = process.communicate(timeout=20)
            raw_device_lines = []
            for line in output.splitlines():
                idx = line.find("Device ")
                if idx >= 0:
                    raw_device_lines.append(line[idx:])

            paired_macs = {mac for mac, _ in get_bt_paired_devices()}
            for mac, listed_name in parse_bluetoothctl_devices("\n".join(raw_device_lines)):
                if mac in paired_macs:
                    continue
                props = get_bt_device_properties(mac)
                display_name = resolve_bt_display_name(
                    mac,
                    alias=props.get("alias"),
                    name=props.get("name"),
                    listed_name=listed_name,
                )
                scanned.append((mac, display_name))
            scanned.sort(key=lambda item: item[1].lower())
        except Exception as e:
            print(f"BT検索エラー: {e}")
        with state_lock:
            bt_scanned_devices = list(scanned)
            is_bt_scanning = False
        status_message = ""
        update_menu_items()
        request_display_update(is_full_refresh=False)
    threading.Thread(target=_scan_thread, daemon=True).start()

def get_bt_scanned_devices_snapshot():
    with state_lock:
        return list(bt_scanned_devices)

def update_menu_items(reset_cursor=True):
    global menu_items, cursor_idx, scroll_offset, bt_paired_devices, bt_scanned_devices, saved_wifi_ssids, saved_wifi_connections, library_menu_entries, selected_album
    if reset_cursor:
        cursor_idx = 0
        scroll_offset = 0
    library_menu_entries = []

    if current_screen == "MENU_TOP":
        pct, _ = get_battery_info()
        items = ["アルバム"]
        if is_clock_unlocked:
            items.append("時計画面")
        items.extend([
            "再生設定",
            "接続設定",
            "システム設定",
            f"バッテリー: {pct}"
        ])
        menu_items = items

    elif current_screen == "MENU_PLAY_SETTINGS":
        rep_labels = ["OFF", "ONE", "ALL"]
        items = [
            "../ (戻る)",
            f"シャッフル: {'ON' if is_shuffle else 'OFF'}",
            f"リピート: {rep_labels[repeat_mode]}",
            "スリープタイマー"
        ]
        if is_eq_unlocked:
            items.append("EQ設定")
        menu_items = items

    elif current_screen == "MENU_SLEEP":
        menu_items = [
            "../ (戻る)",
            f"タイマーOFF {'<--' if sleep_timer_minutes == 0 else ''}",
            f"15分 {'<--' if sleep_timer_minutes == 15 else ''}",
            f"30分 {'<--' if sleep_timer_minutes == 30 else ''}",
            f"60分 {'<--' if sleep_timer_minutes == 60 else ''}"
        ]

    elif current_screen == "MENU_FAVS":
        menu_items = ["../ (戻る)"] + [os.path.splitext(os.path.basename(p))[0] for p in favorites_list]

    elif current_screen == "MENU_HISTORY":
        history_paths = get_available_playback_history()
        menu_items = ["../ (戻る)"] + [os.path.splitext(os.path.basename(p))[0] for p in history_paths]

    elif current_screen == "MENU_CONN":
        menu_items = [
            "../ (戻る)",
            "Wi-Fi設定",
            "Bluetooth設定",
            f"APモード: {'ON' if get_ap_status() else 'OFF'}"
        ]
    elif current_screen == "MENU_WIFI":
        wifi_on, _ = get_rfkill_status()
        saved_wifi_connections = get_saved_wifi_connections() if wifi_on else []
        saved_wifi_ssids = [entry["ssid"] for entry in saved_wifi_connections]
        current_ssid = get_current_wifi_ssid() if wifi_on else None

        items = ["../ (戻る)", f"Wi-Fi機能: {'ON' if wifi_on else 'OFF'}"]
        if wifi_on:
            if saved_wifi_connections:
                for entry in saved_wifi_connections:
                    ssid = entry["ssid"]
                    connected_label = " (接続済)" if current_ssid and ssid == current_ssid else ""
                    items.append(f"接続: {ssid}{connected_label}")
            else:
                items.append("(記憶されたWi-Fiなし)")
        else:
            items.append("(Wi-Fi OFF中)")
        menu_items = items

    elif current_screen == "MENU_BT":
        _, bt_on = get_rfkill_status()
        items = ["../ (戻る)", f"Bluetooth機能: {'ON' if bt_on else 'OFF'}", f"出力先: [{audio_output_mode}]"]
        if bt_on:
            items.extend(["登録済みデバイス", "新規デバイスの検索"])
        else:
            items.append("(Bluetooth OFF中)")
        menu_items = items

    elif current_screen == "MENU_BT_PAIRED":
        bt_paired_devices = get_bt_paired_devices()
        items = ["../ (戻る)"]
        if bt_paired_devices:
            name_counts = {}
            for _, name in bt_paired_devices:
                name_counts[name] = name_counts.get(name, 0) + 1
            for mac, name in bt_paired_devices:
                display_name = format_bt_menu_name(name, mac, include_suffix=name_counts.get(name, 0) > 1)
                connected_label = " (接続済)" if (audio_output_mode == "BT" and is_bt_connected(mac)) else ""
                items.append(f"接続: {display_name}{connected_label}")
        else:
            items.append("(登録済み機器なし)")
        menu_items = items

    elif current_screen == "MENU_BT_SCAN":
        items = ["../ (戻る)", "検索開始"]
        scanned_snapshot = get_bt_scanned_devices_snapshot()
        name_counts = {}
        for _, name in scanned_snapshot:
            name_counts[name] = name_counts.get(name, 0) + 1
        for mac, name in scanned_snapshot:
            items.append(f"ペアリング: {format_bt_menu_name(name, mac, include_suffix=name_counts.get(name, 0) > 1)}")
        menu_items = items

    elif current_screen == "MENU_SYS":
        bt_codec = get_bt_codec_info()
        menu_items = [
            "../ (戻る)",
            "曲ライブラリ再読み込み",
            "GitHubから更新",
            "アプリ再起動",
            "ライセンス表示",
            f"IP: {get_ip_address()}",
            f"BTコーデック: {bt_codec}",
            "再起動",
            "シャットダウン"
        ]
    elif current_screen == "MENU_ALBUMS":
        library_menu_entries = get_library_entries(ROOT_REL_DIR, include_folder_play=False)
        menu_items = ["../ (戻る)", "[★ お気に入り]", "[再生履歴]"] + [entry["display"] for entry in library_menu_entries]

    elif current_screen == "MENU_TRACKS":
        browsing_dir = normalize_rel_dir(selected_album)
        selected_album = browsing_dir
        library_menu_entries = get_library_entries(browsing_dir, include_folder_play=True)
        menu_items = ["../ (戻る)"] + [entry["display"] for entry in library_menu_entries]

def reset_inactivity_timer():
    global last_user_action_time
    last_user_action_time = time.time()

def play_current_track(full_refresh=False):
    global is_playing, total_duration_sec, track_paused_time, last_drawn_segment
    global start_time, paused_duration, pause_start_time, status_message
    if playlist:
        if not pygame.mixer.get_init():
            safe_init_audio()
        if not pygame.mixer.get_init():
            is_playing = False
            status_message = "音声デバイスを初期化できません"
            request_display_update(is_full_refresh=False)
            print("Play error: audio mixer is not initialized")
            return

        filepath = playlist[current_track_idx]
        total_duration_sec = get_track_duration_sec(filepath)
        track_paused_time = 0
        last_drawn_segment = -1
        start_time = time.time()
        paused_duration = 0.0
        pause_start_time = 0.0

        try:
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            is_playing = True
            record_playback_history(filepath)
            save_resume_state()
        except Exception as e:
            print(f"Play error: {e}")
            is_playing = False
            status_message = "再生できません"

        request_display_update(is_full_refresh=full_refresh)

def toggle_shuffle():
    global is_shuffle, playlist, current_track_idx
    is_shuffle = not is_shuffle
    current_song = playlist[current_track_idx] if playlist else None
    playlist = random.sample(playlist_original, len(playlist_original)) if is_shuffle else list(playlist_original)
    if current_song and current_song in playlist:
        current_track_idx = playlist.index(current_song)

def toggle_repeat():
    global repeat_mode
    repeat_mode = (repeat_mode + 1) % 3

def set_sleep_timer(mins):
    global sleep_timer_minutes, sleep_timer_end_time, sleep_timer_pending
    sleep_timer_minutes = mins
    sleep_timer_pending = False  # ← 追加（タイマー更新時にフラグをクリア）
    if mins > 0:
        sleep_timer_end_time = time.time() + (mins * 60)
    else:
        sleep_timer_end_time = 0.0

def exit_cat_clock_if_needed():
    global current_screen
    if current_screen == "CAT_CLOCK":
        current_screen = "PLAY"
        request_display_update(is_full_refresh=True)
        return True
    return False

def check_easter_egg_trigger():
    global easter_click_count, last_easter_click_time, current_screen, is_clock_unlocked, is_eq_unlocked
    now = time.time()
    if now - last_easter_click_time > 3.0:
        easter_click_count = 0

    easter_click_count += 1
    last_easter_click_time = now

    if easter_click_count >= 5:
        easter_click_count = 0
        is_clock_unlocked = True
        is_eq_unlocked = True
        current_screen = "CAT_CLOCK"
        request_display_update(is_full_refresh=True)
        return True
    return False

# --- 9. ボタンイベントハンドラ ---
def parse_clean_name(selected_str, prefix):
    raw = selected_str.replace(prefix, "").strip()
    return raw.replace(" (接続済)", "").strip()

def on_btn_menu_or_select():
    if not debounce("btn_menu_select"): return
    global current_screen, selected_album, current_track_idx, playlist, status_message, eq_cursor
    with state_lock:
        reset_inactivity_timer()

        if current_screen == "CAT_EQ":
            if eq_cursor == 3:
                eq_values["BASS"] = 0
                eq_values["MID"] = 0
                eq_values["TREBLE"] = 0
            apply_eq_settings()
            current_screen = "MENU_PLAY_SETTINGS"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
            return

        if exit_cat_clock_if_needed(): return

        if current_screen == "PLAY":
            current_screen = "MENU_TOP"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        else:
            if cursor_idx < len(menu_items):
                selected = menu_items[cursor_idx]

                if selected.startswith("../"):
                    if current_screen == "MENU_TRACKS":
                        browsing_dir = normalize_rel_dir(selected_album)
                        if browsing_dir == ROOT_REL_DIR:
                            current_screen = "MENU_TOP"
                            selected_album = None
                        else:
                            parent_dir = normalize_rel_dir(os.path.dirname(browsing_dir))
                            if parent_dir == ROOT_REL_DIR:
                                current_screen = "MENU_ALBUMS"
                                selected_album = None
                            else:
                                selected_album = parent_dir
                                current_screen = "MENU_TRACKS"
                    elif current_screen in ["MENU_CONN", "MENU_ALBUMS", "MENU_SYS", "MENU_PLAY_SETTINGS"]:
                        current_screen = "MENU_TOP"
                    elif current_screen in ["MENU_WIFI", "MENU_BT"]: current_screen = "MENU_CONN"
                    elif current_screen in ["MENU_BT_PAIRED", "MENU_BT_SCAN"]: current_screen = "MENU_BT"
                    elif current_screen in ["MENU_FAVS", "MENU_HISTORY"]: current_screen = "MENU_ALBUMS"
                    elif current_screen == "MENU_SLEEP": current_screen = "MENU_PLAY_SETTINGS"
                    else: current_screen = "MENU_TOP"
                    update_menu_items(reset_cursor=True)
                    request_display_update(is_full_refresh=True)
                    return

                if current_screen == "MENU_TOP":
                    if selected == "アルバム":
                        selected_album = None
                        current_screen = "MENU_ALBUMS"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    elif selected == "時計画面":
                        current_screen = "CAT_CLOCK"
                        request_display_update(is_full_refresh=True)
                    elif selected == "再生設定":
                        current_screen = "MENU_PLAY_SETTINGS"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    elif selected == "接続設定":
                        current_screen = "MENU_CONN"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    elif selected == "システム設定":
                        current_screen = "MENU_SYS"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    elif selected.startswith("バッテリー:"):
                        update_menu_items(reset_cursor=False)
                        request_display_update(is_full_refresh=False)

                elif current_screen == "MENU_PLAY_SETTINGS":
                    if selected.startswith("シャッフル:"):
                        toggle_shuffle()
                        update_menu_items(reset_cursor=False)
                        request_display_update(is_full_refresh=False)
                    elif selected.startswith("リピート:"):
                        toggle_repeat()
                        update_menu_items(reset_cursor=False)
                        request_display_update(is_full_refresh=False)
                    elif selected == "スリープタイマー":
                        current_screen = "MENU_SLEEP"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    elif selected == "EQ設定":
                        current_screen = "CAT_EQ"
                        eq_cursor = 0
                        request_display_update(is_full_refresh=True)

                elif current_screen == "MENU_SLEEP":
                    if selected.startswith("タイマーOFF"): set_sleep_timer(0)
                    elif selected.startswith("15分"): set_sleep_timer(15)
                    elif selected.startswith("30分"): set_sleep_timer(30)
                    elif selected.startswith("60分"): set_sleep_timer(60)
                    update_menu_items(reset_cursor=False)
                    request_display_update(is_full_refresh=False)

                elif current_screen == "MENU_FAVS":
                    chosen_idx = cursor_idx - 1
                    if 0 <= chosen_idx < len(favorites_list):
                        playlist = favorites_list
                        current_track_idx = chosen_idx
                        play_current_track(full_refresh=True)
                        current_screen = "PLAY"

                elif current_screen == "MENU_HISTORY":
                    history_paths = get_available_playback_history()
                    chosen_idx = cursor_idx - 1
                    if 0 <= chosen_idx < len(history_paths):
                        playlist = list(history_paths)
                        current_track_idx = chosen_idx
                        play_current_track(full_refresh=True)
                        current_screen = "PLAY"

                elif current_screen == "MENU_CONN":
                    if selected == "Wi-Fi設定":
                        current_screen = "MENU_WIFI"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    elif selected == "Bluetooth設定":
                        current_screen = "MENU_BT"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    elif selected.startswith("APモード:"):
                        toggle_ap_mode()
                        update_menu_items(reset_cursor=False)
                        request_display_update(is_full_refresh=False)

                elif current_screen == "MENU_WIFI":
                    if selected.startswith("Wi-Fi機能:"):
                        toggle_rfkill("wifi")
                        update_menu_items(reset_cursor=False)
                        request_display_update(is_full_refresh=False)
                    elif selected.startswith("接続:"):
                        chosen_idx = cursor_idx - 2
                        if 0 <= chosen_idx < len(saved_wifi_connections):
                            connect_to_wifi(dict(saved_wifi_connections[chosen_idx]))

                elif current_screen == "MENU_BT":
                    if selected.startswith("Bluetooth機能:"):
                        toggle_rfkill("bt")
                        update_menu_items(reset_cursor=False)
                        request_display_update(is_full_refresh=False)
                    elif selected.startswith("出力先:"):
                        new_mode = "BT" if audio_output_mode == "PWM" else "PWM"
                        set_audio_output(new_mode, connected_bt_mac)
                    elif selected == "登録済みデバイス":
                        current_screen = "MENU_BT_PAIRED"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    elif selected == "新規デバイスの検索":
                        current_screen = "MENU_BT_SCAN"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)

                elif current_screen == "MENU_BT_PAIRED":
                    if selected.startswith("接続:"):
                        chosen_idx = cursor_idx - 1
                        if 0 <= chosen_idx < len(bt_paired_devices):
                            mac, name = bt_paired_devices[chosen_idx]
                            threading.Thread(target=connect_bt_device, args=(mac, name), daemon=True).start()

                elif current_screen == "MENU_BT_SCAN":
                    if selected == "検索開始":
                        trigger_bt_scan()
                    elif selected.startswith("ペアリング:"):
                        chosen_idx = cursor_idx - 2
                        if 0 <= chosen_idx < len(bt_scanned_devices):
                            mac, name = bt_scanned_devices[chosen_idx]
                            threading.Thread(target=connect_bt_device, args=(mac, name), daemon=True).start()

                elif current_screen == "MENU_ALBUMS":
                    if selected == "[★ お気に入り]":
                        current_screen = "MENU_FAVS"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    elif selected == "[再生履歴]":
                        current_screen = "MENU_HISTORY"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    else:
                        chosen_idx = cursor_idx - 3  # 0: 戻る, 1: お気に入り, 2: 再生履歴
                        if 0 <= chosen_idx < len(library_menu_entries):
                            chosen_entry = library_menu_entries[chosen_idx]
                            if chosen_entry["type"] == "dir":
                                selected_album = chosen_entry["rel_dir"]
                                current_screen = "MENU_TRACKS"
                                update_menu_items(reset_cursor=True)
                                request_display_update(is_full_refresh=True)
                            elif chosen_entry["type"] == "track":
                                direct_tracks = get_direct_tracks_in_dir(ROOT_REL_DIR)
                                if chosen_entry["path"] in direct_tracks:
                                    playlist = direct_tracks
                                    current_track_idx = direct_tracks.index(chosen_entry["path"])
                                    play_current_track(full_refresh=True)
                                    current_screen = "PLAY"

                elif current_screen == "MENU_TRACKS":
                    chosen_idx = cursor_idx - 1
                    if 0 <= chosen_idx < len(library_menu_entries):
                        chosen_entry = library_menu_entries[chosen_idx]
                        if chosen_entry["type"] == "dir":
                            selected_album = chosen_entry["rel_dir"]
                            update_menu_items(reset_cursor=True)
                            request_display_update(is_full_refresh=True)
                        elif chosen_entry["type"] == "play_folder":
                            subtree_tracks = get_tracks_in_subtree(chosen_entry["rel_dir"])
                            if subtree_tracks:
                                playlist = subtree_tracks
                                current_track_idx = 0
                                play_current_track(full_refresh=True)
                                current_screen = "PLAY"
                        elif chosen_entry["type"] == "track":
                            browsing_dir = normalize_rel_dir(selected_album)
                            direct_tracks = get_direct_tracks_in_dir(browsing_dir)
                            if chosen_entry["path"] in direct_tracks:
                                playlist = direct_tracks
                                current_track_idx = direct_tracks.index(chosen_entry["path"])
                                play_current_track(full_refresh=True)
                                current_screen = "PLAY"

                elif current_screen == "MENU_SYS":
                    if selected == "曲ライブラリ再読み込み":
                        reload_music_library()
                        request_display_update(is_full_refresh=False)

                    elif selected == "GitHubから更新":
                        start_github_update()

                    elif selected == "ライセンス表示":
                        if not check_easter_egg_trigger():
                            status_message = f"MIT License\n(c) kakuritsu\nTwitter:@KAKURITU_P\n[V 1.1.0 {get_git_short_hash()}]"
                            request_display_update(is_full_refresh=False)
                            def _clear_status():
                                time.sleep(3.0)
                                global status_message
                                status_message = ""
                                request_display_update(is_full_refresh=False)
                            threading.Thread(target=_clear_status, daemon=True).start()
                    elif selected == "アプリ再起動":
                        clean_shutdown_display("Restarting...")
                        subprocess.run(["sudo", "systemctl", "restart", "dap.service"])
                    elif selected == "再起動":
                        clean_shutdown_display("Rebooting...")
                        subprocess.run(["sudo", "reboot"])
                    elif selected == "シャットダウン":
                        clean_shutdown_display("Power Off...")
                        subprocess.run(["sudo", "shutdown", "-h", "now"])

def on_btn_play_or_back():
    if not debounce("btn_play_back"): return
    global current_screen, is_playing, track_paused_time, pause_start_time, paused_duration
    with state_lock:
        reset_inactivity_timer()
        
        if current_screen == "CAT_EQ":
            if eq_cursor == 3:
                eq_values["BASS"] = 0
                eq_values["MID"] = 0
                eq_values["TREBLE"] = 0
            apply_eq_settings()
            current_screen = "PLAY"
            request_display_update(is_full_refresh=True)
            return

        if exit_cat_clock_if_needed(): return

        if current_screen == "PLAY":
            if is_playing:
                pause_start_time = time.time()
                track_paused_time = get_current_sec()
                try: pygame.mixer.music.pause()
                except Exception: pass
                is_playing = False
                request_display_update(is_full_refresh=False)
            else:
                if pause_start_time > 0 and pygame.mixer.get_init():
                    paused_duration += (time.time() - pause_start_time)
                    pause_start_time = 0.0
                    try:
                        pygame.mixer.music.unpause()
                        is_playing = True
                    except Exception:
                        is_playing = False
                    request_display_update(is_full_refresh=False)
                else:
                    pause_start_time = 0.0
                    play_current_track(full_refresh=True)
        else:
            current_screen = "PLAY"
            request_display_update(is_full_refresh=True)

def on_btn_up_action():
    if not debounce("btn_up_act"): return
    if exit_cat_clock_if_needed(): return
    global volume, cursor_idx, scroll_offset
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "CAT_EQ":
            if eq_cursor < 3:
                band = eq_bands[eq_cursor]
                eq_values[band] = min(6, eq_values[band] + 1)
                request_display_update(is_full_refresh=False)
        elif current_screen == "PLAY":
            volume = min(volume + 0.05, 1.0)
            set_cur_volume(volume)
            request_display_update(is_full_refresh=False)
        else:
            if cursor_idx > 0:
                cursor_idx -= 1
                if cursor_idx < scroll_offset: scroll_offset = cursor_idx
                request_display_update(is_full_refresh=False)

def on_btn_down_action():
    if not debounce("btn_down_act"): return
    if exit_cat_clock_if_needed(): return
    global volume, cursor_idx, scroll_offset
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "CAT_EQ":
            if eq_cursor < 3:
                band = eq_bands[eq_cursor]
                eq_values[band] = max(-6, eq_values[band] - 1)
                request_display_update(is_full_refresh=False)
        elif current_screen == "PLAY":
            volume = max(volume - 0.05, 0.0)
            set_cur_volume(volume)
            request_display_update(is_full_refresh=False)
        else:
            if cursor_idx < len(menu_items) - 1:
                cursor_idx += 1
                if cursor_idx >= scroll_offset + 5: scroll_offset = cursor_idx - 4
                request_display_update(is_full_refresh=False)

def on_btn_prev():
    if not debounce("btn_prev"): return
    if exit_cat_clock_if_needed(): return
    global current_track_idx, eq_cursor
    with state_lock:
        reset_inactivity_timer()
        
        # 1. EQ画面の場合
        if current_screen == "CAT_EQ":
            eq_cursor = (eq_cursor - 1) % 4
            request_display_update(is_full_refresh=False)

        # 2. フォルダ内の曲一覧画面（MENU_TRACKS）の場合 -> カーソル位置の曲をお気に入りに追加/解除
        elif current_screen == "MENU_TRACKS":
            chosen_idx = cursor_idx - 1  # 0番目は "../ (戻る)"
            if 0 <= chosen_idx < len(library_menu_entries):
                entry = library_menu_entries[chosen_idx]
                if entry["type"] == "track":
                    is_fav = toggle_favorite_by_path(entry["path"])
                    show_fav_toast(is_fav)

        # 3. 通常再生時などの曲戻し動作
        elif playlist:
            if is_playing and get_current_sec() > 3:
                play_current_track(full_refresh=True)
            else:
                current_track_idx = (current_track_idx - 1) % len(playlist)
                play_current_track(full_refresh=True)

def handle_next_btn_press():
    """NEXTボタンの長押し検知 (PLAY画面での Fav トグル) ＆ 通常押し (曲送り)"""
    global current_track_idx, eq_cursor
    
    start_t = time.time()
    while btn_next.is_pressed:
        time.sleep(0.05)
        if current_screen == "PLAY" and (time.time() - start_t) >= 1.2:
            with state_lock:
                is_fav = toggle_favorite_current_track()
                show_fav_toast(is_fav)
            return

    # 通常押し（短押し）処理
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "CAT_EQ":
            eq_cursor = (eq_cursor + 1) % 4
            request_display_update(is_full_refresh=False)
        elif playlist:
            current_track_idx = (current_track_idx + 1) % len(playlist)
            play_current_track(full_refresh=True)

def on_btn_next():
    if not debounce("btn_next"): return
    if exit_cat_clock_if_needed(): return
    threading.Thread(target=handle_next_btn_press, daemon=True).start()

# --- 10. GPIO割り当て ---
BOUNCE_SEC = 0.1

btn_up_act      = Button(22, bounce_time=BOUNCE_SEC)
btn_down_act    = Button(27, bounce_time=BOUNCE_SEC)
btn_menu_select = Button(5,  bounce_time=BOUNCE_SEC)
btn_prev        = Button(12, bounce_time=BOUNCE_SEC)
btn_next        = Button(16, bounce_time=BOUNCE_SEC)
btn_play_back   = Button(20, bounce_time=BOUNCE_SEC)

btn_up_act.when_pressed      = on_btn_up_action
btn_down_act.when_pressed    = on_btn_down_action
btn_menu_select.when_pressed = on_btn_menu_or_select
btn_prev.when_pressed        = on_btn_prev
btn_next.when_pressed        = on_btn_next
btn_play_back.when_pressed   = on_btn_play_or_back

# --- 11. メインループ ---
def handle_track_completion():
    global is_playing, current_track_idx

    if sleep_timer_pending:
        clean_shutdown_display("Sleep Timer Off...")
        subprocess.run(["sudo", "shutdown", "-h", "now"])
        return

    if not playlist:
        is_playing = False
        request_display_update(is_full_refresh=False)
        return

    if repeat_mode == 1:
        play_current_track(full_refresh=True)
        return

    if repeat_mode == 2:
        current_track_idx = (current_track_idx + 1) % len(playlist)
        play_current_track(full_refresh=True)
        return

    if current_track_idx < len(playlist) - 1:
        current_track_idx += 1
        play_current_track(full_refresh=True)
    else:
        is_playing = False
        request_display_update(is_full_refresh=False)

try:
    # 起動時のレジューム処理（自動で前回の曲を先頭から準備）
    if playlist:
        filepath = playlist[current_track_idx]
        total_duration_sec = get_track_duration_sec(filepath)
        try:
            pygame.mixer.music.load(filepath)
        except Exception as e:
            print(f"Resume load error: {e}")

    request_display_update(is_full_refresh=True)

    while True:
        time.sleep(0.2)
        now = time.time()

        # --- スリープタイマー監視 ---
        # 時間が来たら即シャットダウンせず、待機フラグを立てる
        if sleep_timer_end_time > 0 and now >= sleep_timer_end_time:
            sleep_timer_pending = True
            sleep_timer_end_time = 0.0  # 二重判定防止

        # 音楽が停止中にタイマー時間が来た場合はそのままシャットダウン
        if sleep_timer_pending and not is_playing:
            clean_shutdown_display("Sleep Timer Off...")
            subprocess.run(["sudo", "shutdown", "-h", "now"])

        # --- 曲の再生・終了監視 ---
        if is_playing:
            with state_lock:
                cur_sec = get_current_sec()
            try:
                mixer_initialized = pygame.mixer.get_init() is not None
                music_busy = pygame.mixer.music.get_busy() if mixer_initialized else False
            except pygame.error as e:
                print(f"Audio monitor error: {e}")
                mixer_initialized = False
                music_busy = False

            if not mixer_initialized:
                is_playing = False
            elif not music_busy:
                handle_track_completion()
            elif total_duration_sec > 0 and cur_sec >= total_duration_sec + 1:
                handle_track_completion()

        with state_lock:
            cur_sec = get_current_sec()

            if current_screen == "PLAY" and is_playing and total_duration_sec > 0:
                progress_ratio = min(1.0, cur_sec / float(total_duration_sec))
                current_segment = int(progress_ratio * 8)

                if current_segment != last_drawn_segment:
                    last_drawn_segment = current_segment
                    request_display_update(is_full_refresh=False)

            if current_screen not in ["PLAY", "CAT_CLOCK", "CAT_EQ"] and (now - last_user_action_time > 10):
                current_screen = "PLAY"
                request_display_update(is_full_refresh=True)

except KeyboardInterrupt:
    clean_shutdown_display("Power Off...")
    sys.exit(0)
