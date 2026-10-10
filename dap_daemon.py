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

sys.path.append("/home/pi/dap")
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

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
        subprocess.run(["sudo", "rfkill", "unblock", "bluetooth"], check=False)
        subprocess.run(["sudo", "systemctl", "start", "bluealsa"], check=False)
        time.sleep(1.0)
    else:
        audio_output_mode = "PWM"
else:
    audio_output_mode = "PWM"

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
        scaled_vol = vol ** 2
        pygame.mixer.music.set_volume(scaled_vol)

def safe_init_audio():
    def _init_thread():
        global is_playing
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

            os.environ['SDL_AUDIODRIVER'] = 'alsa'

            if audio_output_mode == "BT":
                buf_size = 4096
                init_success = False
                # BlueALSA PCM が作成されるまで最大5回リトライ
                for i in range(5):
                    try:
                        time.sleep(1.0)
                        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=buf_size)
                        init_success = True
                        break
                    except Exception as err:
                        print(f"BT Audio init retry {i+1}/5: {err}")
                
                if not init_success:
                    print("[ERROR] BlueALSA device setup failed. Audio init aborted.")
                    return
            else:
                time.sleep(0.3)
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=8192)

            set_cur_volume(volume)
            print(f"Audio initialized in [{audio_output_mode}] mode.")
        except Exception as e:
            print(f"Audio init failed ({e}).")

    threading.Thread(target=_init_thread, daemon=True).start()

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

if not os.path.exists(MUSIC_DIR):
    os.makedirs(MUSIC_DIR, exist_ok=True)
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

playlist_original = []
playlist = []
albums_dict = {}
selected_album = None
current_track_idx = 0
favorites_list = []

# リピートモード: 0: OFF, 1: REPEAT_ONE, 2: REPEAT_ALL
repeat_mode = 0

# スリープタイマー (分)
sleep_timer_minutes = 0
sleep_timer_end_time = 0.0
sleep_timer_pending = False  

# 更新状態管理フラグ
is_updating = False

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

def reload_music_library():
    global playlist_original, playlist, albums_dict, current_track_idx
    extensions = ('*.mp3', '*.wav', '*.ogg', '*.flac', '*.m4a')
    found_files = []
    for ext in extensions:
        found_files.extend(glob.glob(os.path.join(MUSIC_DIR, "**", ext), recursive=True))
        found_files.extend(glob.glob(os.path.join(MUSIC_DIR, ext)))

    playlist_original = sorted(list(set(found_files)))
    playlist = list(playlist_original)
    current_track_idx = 0

    albums_dict = {}
    for filepath in playlist_original:
        rel_dir = os.path.dirname(os.path.relpath(filepath, MUSIC_DIR))
        album_name = rel_dir if rel_dir else "シングル曲"
        if album_name not in albums_dict:
            albums_dict[album_name] = []
        albums_dict[album_name].append(filepath)

load_favorites()
reload_music_library()
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
    
    mac_path_part = connected_bt_mac.replace(":", "_").lower()
    pcm_path = f"/org/bluealsa/hci0/dev_{mac_path_part}/a2dp"
    
    try:
        cmd = [
            "gdbus", "call", "--system",
            "--dest", "org.bluealsa",
            "--object-path", pcm_path,
            "--method", "org.freedesktop.DBus.Properties.Get",
            "org.bluealsa.PCM", "Codec"
        ]
        res = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, errors="ignore")
        if "'" in res:
            codec = res.split("'")[1]
            return codec.upper()
    except Exception:
        pass

    try:
        res = subprocess.check_output(["bluetoothctl", "info", connected_bt_mac], text=True, stderr=subprocess.DEVNULL, errors="ignore")
        if "Connected: yes" in res:
            for line in res.splitlines():
                if "Name:" in line:
                    return line.split(":", 1)[1].strip()
            return "A2DP接続"
    except Exception:
        pass

    return "未接続"

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
    if not mac:
        return False
    try:
        res = subprocess.check_output(["bluetoothctl", "info", mac], text=True, errors='ignore')
        return "Connected: yes" in res
    except Exception:
        return False

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
            image.paste(ship_img, (pos_y), mask)
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

def request_display_update(is_full_refresh=False):
    global pending_full_refresh
    with display_queue.mutex:
        if is_full_refresh:
            pending_full_refresh = True
        display_queue.queue.clear()
    display_queue.put(True)

def display_worker():
    global pending_full_refresh
    current_epd_mode = None
    partial_refresh_count = 0

    while True:
        try:
            _ = display_queue.get()
            
            with display_queue.mutex:
                is_full = pending_full_refresh
                pending_full_refresh = False

            if partial_refresh_count >= 30:
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
                elif scr == "MENU_ALBUMS": header_text = "メニュー > アルバム"
                elif scr == "MENU_FAVS": header_text = "アルバム > お気に入り"
                elif scr == "MENU_TRACKS": header_text = truncate_by_width(f"アルバム > {sel_alb}", font_main, 230) if sel_alb else "アルバム"
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

    connected_bt_mac = mac
    status_message = f"接続中: {name[:10]}"
    request_display_update(is_full_refresh=False)

    if pygame.mixer.get_init():
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

    # equal を挟まず、直接 bluealsa を plug でラップする
    config_content = f"""pcm.!default {{
    type plug
    slave.pcm {{
        type bluealsa
        device "{mac}"
        profile "a2dp"
    }}
}}
"""
    try:
        with open(asoundrc_path, "w") as f:
            f.write(config_content)
    except Exception as e:
        print(f"asoundrc write error: {e}")

    subprocess.run(["sudo", "rfkill", "unblock", "bluetooth"], check=False)
    subprocess.run(["sudo", "systemctl", "restart", "bluealsa"], check=False)
    time.sleep(0.5)

    subprocess.run(["bluetoothctl", "power", "on"], check=False)
    subprocess.run(["bluetoothctl", "agent", "on"], check=False)
    subprocess.run(["bluetoothctl", "default-agent"], check=False)
    time.sleep(0.5)

    subprocess.run(["bluetoothctl", "trust", mac], check=False)
    time.sleep(0.5)

    subprocess.run(["bluetoothctl", "pair", mac], check=False)
    time.sleep(2.0)

    # Bluetooth接続コマンド実行
    subprocess.run(["bluetoothctl", "connect", mac], check=False)
    
    # A2DPストリームがOS側に認識されるまで少し待つ
    time.sleep(3.0)

    # 接続確認
    if is_bt_connected(mac):
        audio_output_mode = "BT"
        try:
            subprocess.run(["amixer", "-D", "bluealsa", "sset", "Master", "100%"], check=False)
        except Exception:
            pass
        
        # 接続が完全に確立されてからオーディオ初期化
        safe_init_audio()
        apply_eq_settings()
        status_message = "接続成功"
    else:
        status_message = "接続失敗"

    request_display_update(is_full_refresh=True)
    time.sleep(1.5)
    status_message = ""
    update_menu_items()
    request_display_update(is_full_refresh=False)

def set_audio_output(mode, mac=None):
    global audio_output_mode, status_message, connected_bt_mac

    status_message = f"切替中: {mode}"
    request_display_update(is_full_refresh=False)

    # サービス切替前に必ず Pygame mixer を完全にシャットダウン
    if pygame.mixer.get_init():
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        pygame.mixer.quit()
        time.sleep(0.5)

    if mode == "BT":
        target_mac = mac or connected_bt_mac
        if target_mac:
            connect_bt_device(target_mac, "Bluetooth")
        else:
            status_message = "MAC未設定"
            request_display_update(is_full_refresh=False)
            time.sleep(1.0)
            status_message = ""

    elif mode == "PWM":
        pwm_config = """pcm.!default {
    type plug
    slave.pcm "hw:0,0"
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
    wifi_on, bt_on = get_rfkill_status()
    target = "wlan" if dev_type == "wifi" else "bluetooth"
    currently_on = wifi_on if dev_type == "wifi" else bt_on
    action = "block" if currently_on else "unblock"
    try:
        subprocess.run(["sudo", "rfkill", action, target], check=False)
        time.sleep(0.3)
    except Exception: pass

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
        try:
            if is_active:
                status_message = "AP OFF中..."
                request_display_update(is_full_refresh=False)

                subprocess.run(["sudo", "pkill", "-9", "hostapd"], check=False)
                subprocess.run(["sudo", "pkill", "-9", "dnsmasq"], check=False)
                subprocess.run(["sudo", "systemctl", "restart", "NetworkManager"], check=False)

            else:
                status_message = "AP ON中..."
                request_display_update(is_full_refresh=False)

                subprocess.run(["sudo", "systemctl", "stop", "NetworkManager"], check=False)
                subprocess.run(["sudo", "pkill", "-9", "wpa_supplicant"], check=False)
                subprocess.run(["sudo", "pkill", "-9", "hostapd"], check=False)
                subprocess.run(["sudo", "pkill", "-9", "dnsmasq"], check=False)

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

def get_saved_wifi_ssids():
    ssids = []
    try:
        res = subprocess.check_output(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], text=True)
        for line in res.strip().splitlines():
            if ":" in line:
                name, conn_type = line.split(":", 1)
                if conn_type == "802-11-wireless" and name != "Pi-DAP-AP":
                    ssids.append(name)
    except Exception:
        pass

    try:
        res = subprocess.check_output(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], text=True)
        lines = res.strip().splitlines()[1:]
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 2:
                ssid_name = parts[1]
                if ssid_name not in ["any", "ssid"] and ssid_name != "Pi-DAP-AP":
                    ssids.append(ssid_name)
    except Exception:
        pass

    return list(set(ssids))

def connect_to_wifi(ssid):
    def _do_connect():
        global status_message
        status_message = f"接続中: {ssid}"
        request_display_update(is_full_refresh=False)
        try:
            subprocess.run(["sudo", "rfkill", "unblock", "wlan"], check=False)
            res = subprocess.run(["nmcli", "connection", "up", ssid], capture_output=True, text=True)
            if res.returncode != 0:
                res_wpa = subprocess.check_output(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], text=True)
                net_id = None
                for line in res_wpa.strip().splitlines()[1:]:
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[1] == ssid:
                        net_id = parts[0]
                        break
                if net_id is not None:
                    subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "select_network", net_id])
        except Exception: pass
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
    devs = []
    try:
        res = subprocess.check_output(["bluetoothctl", "paired-devices"], text=True, errors='ignore')
        for line in res.strip().splitlines():
            parts = line.split(' ', 2)
            if len(parts) >= 3:
                devs.append((parts[1], parts[2].strip()))
    except Exception: pass

    if not devs and connected_bt_mac:
        devs.append((connected_bt_mac, "Default Device"))
    return devs

def trigger_bt_scan():
    global is_bt_scanning, bt_scanned_devices
    if is_bt_scanning: return
    def _scan_thread():
        global is_bt_scanning, status_message, bt_scanned_devices
        is_bt_scanning = True
        status_message = "BT検索中(5秒)..."
        request_display_update(is_full_refresh=False)
        try:
            subprocess.run(["sudo", "rfkill", "unblock", "bluetooth"], check=False)
            subprocess.run(["bluetoothctl", "--timeout", "5", "scan", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            res = subprocess.check_output(["bluetoothctl", "devices"], text=True, errors='ignore')
            paired = [d[0] for d in get_bt_paired_devices()]
            scanned = []
            for line in res.strip().splitlines():
                parts = line.split(' ', 2)
                if len(parts) >= 3 and parts[1] not in paired:
                    scanned.append((parts[1], parts[2].strip()))
            bt_scanned_devices = scanned
        except Exception: pass
        is_bt_scanning = False
        status_message = ""
        update_menu_items()
        request_display_update(is_full_refresh=False)
    threading.Thread(target=_scan_thread, daemon=True).start()

def update_menu_items(reset_cursor=True):
    global menu_items, cursor_idx, scroll_offset, bt_paired_devices, bt_scanned_devices, saved_wifi_ssids
    if reset_cursor:
        cursor_idx = 0
        scroll_offset = 0

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

    elif current_screen == "MENU_CONN":
        menu_items = [
            "../ (戻る)",
            "Wi-Fi設定",
            "Bluetooth設定",
            f"APモード: {'ON' if get_ap_status() else 'OFF'}"
        ]
    elif current_screen == "MENU_WIFI":
        wifi_on, _ = get_rfkill_status()
        saved_wifi_ssids = get_saved_wifi_ssids() if wifi_on else []
        current_ssid = get_current_wifi_ssid() if wifi_on else None

        items = ["../ (戻る)", f"Wi-Fi機能: {'ON' if wifi_on else 'OFF'}"]
        if wifi_on:
            if saved_wifi_ssids:
                for ssid in saved_wifi_ssids:
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
        for mac, name in bt_paired_devices:
            connected_label = " (接続済)" if (audio_output_mode == "BT" and is_bt_connected(mac)) else ""
            items.append(f"接続: {name}{connected_label}")
        menu_items = items

    elif current_screen == "MENU_BT_SCAN":
        items = ["../ (戻る)", "検索開始"]
        items.extend([f"ペアリング: {name}" for _, name in bt_scanned_devices])
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
        album_list = sorted(list(albums_dict.keys()))
        menu_items = ["../ (戻る)", "[★ お気に入り]"] + [f"[{alb}]" for alb in album_list]

    elif current_screen == "MENU_TRACKS":
        track_paths = albums_dict.get(selected_album, [])
        menu_items = ["../ (戻る)"] + [os.path.splitext(os.path.basename(p))[0] for p in track_paths]

def reset_inactivity_timer():
    global last_user_action_time
    last_user_action_time = time.time()
    
def play_current_track(full_refresh=False):
    global is_playing, total_duration_sec, track_paused_time, last_drawn_segment
    global start_time, paused_duration, pause_start_time
    if playlist:
        filepath = playlist[current_track_idx]
        total_duration_sec = get_track_duration_sec(filepath)
        track_paused_time = 0
        last_drawn_segment = -1
        start_time = time.time()
        paused_duration = 0.0
        pause_start_time = 0.0

        def _do_play():
            global is_playing
            try:
                # safe_init_audio() 等でmixerが再初期化中の場合は少し待つ
                retry = 0
                while not pygame.mixer.get_init() and retry < 10:
                    time.sleep(0.2)
                    retry += 1

                if not pygame.mixer.get_init():
                    print("[SKIP] Cannot play: mixer not initialized")
                    is_playing = False
                    return

                pygame.mixer.music.load(filepath)
                pygame.mixer.music.play()
                is_playing = True
                save_resume_state()
            except Exception as e:
                print(f"Play error: {e}")
                is_playing = False # エラー時は確実に再生停止状態に戻す

        # 再生処理を別スレッドで実行（画面フリーズを回避）
        threading.Thread(target=_do_play, daemon=True).start()

        # UI（画面）は待たずに即座に更新リクエストを飛ばす
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
    sleep_timer_pending = False
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
                    if current_screen in ["MENU_CONN", "MENU_ALBUMS", "MENU_SYS", "MENU_PLAY_SETTINGS"]: current_screen = "MENU_TOP"
                    elif current_screen in ["MENU_WIFI", "MENU_BT"]: current_screen = "MENU_CONN"
                    elif current_screen in ["MENU_BT_PAIRED", "MENU_BT_SCAN"]: current_screen = "MENU_BT"
                    elif current_screen in ["MENU_TRACKS", "MENU_FAVS"]: current_screen = "MENU_ALBUMS"
                    elif current_screen == "MENU_SLEEP": current_screen = "MENU_PLAY_SETTINGS"
                    else: current_screen = "MENU_TOP"
                    update_menu_items(reset_cursor=True)
                    request_display_update(is_full_refresh=True)
                    return

                if current_screen == "MENU_TOP":
                    if selected == "アルバム":
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
                        clean_ssid = parse_clean_name(selected, "接続:")
                        connect_to_wifi(clean_ssid)

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
                        dev_name = parse_clean_name(selected, "接続:")
                        for mac, name in bt_paired_devices:
                            if name == dev_name:
                                threading.Thread(target=connect_bt_device, args=(mac, name), daemon=True).start()
                                break

                elif current_screen == "MENU_BT_SCAN":
                    if selected == "検索開始":
                        trigger_bt_scan()
                    elif selected.startswith("ペアリング:"):
                        dev_name = parse_clean_name(selected, "ペアリング:")
                        for mac, name in bt_scanned_devices:
                            if name == dev_name:
                                threading.Thread(target=connect_bt_device, args=(mac, name), daemon=True).start()
                                break

                elif current_screen == "MENU_ALBUMS":
                    if selected == "[★ お気に入り]":
                        current_screen = "MENU_FAVS"
                        update_menu_items(reset_cursor=True)
                        request_display_update(is_full_refresh=True)
                    else:
                        album_list = sorted(list(albums_dict.keys()))
                        chosen_idx = cursor_idx - 2  # 0番目: "../", 1番目: "[★ お気に入り]"
                        if 0 <= chosen_idx < len(album_list):
                            selected_album = album_list[chosen_idx]
                            current_screen = "MENU_TRACKS"
                            update_menu_items(reset_cursor=True)
                            request_display_update(is_full_refresh=True)

                elif current_screen == "MENU_TRACKS":
                    track_paths = albums_dict.get(selected_album, [])
                    chosen_idx = cursor_idx - 1
                    if 0 <= chosen_idx < len(track_paths):
                        playlist = track_paths
                        current_track_idx = chosen_idx
                        play_current_track(full_refresh=True)
                        current_screen = "PLAY"

                elif current_screen == "MENU_SYS":
                    if selected == "曲ライブラリ再読み込み":
                        reload_music_library()
                        request_display_update(is_full_refresh=False)

                    elif selected == "GitHubから更新":
                        # epd.sleep() を呼ばないようにテキストだけ描画して更新
                        try:
                            epd.init()
                            img = Image.new('1', (epd.height, epd.width), 255)
                            draw = ImageDraw.Draw(img)
                            draw.rectangle([0, 0, epd.height, epd.width], fill=255)
                            draw.text((20, (epd.width // 2) - 10), "Updating from GitHub...", font=font_title, fill=0)
                            epd.display(epd.getbuffer(img))
                            time.sleep(0.5)
                        except Exception:
                            pass
                        
                        def _update_and_restart():
                            try:
                                repo_dir = "/home/pi/dap"
                                subprocess.run(["git", "fetch", "origin"], cwd=repo_dir, check=False)
                                subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=repo_dir, check=False)
                            except Exception as e:
                                print(f"Git update failed: {e}")
                            
                            subprocess.run(["sudo", "systemctl", "restart", "dap"])

                        threading.Thread(target=_update_and_restart, daemon=True).start()

                    elif selected == "ライセンス表示":
                        if not check_easter_egg_trigger():
                            status_message = "MIT License\n(c) kakuritsu\nTwitter:@KAKURITU_P\n[V 1.0.3-beta6]"
                            request_display_update(is_full_refresh=False)
                            def _clear_status():
                                time.sleep(3.0)
                                global status_message
                                status_message = ""
                                request_display_update(is_full_refresh=False)
                            threading.Thread(target=_clear_status, daemon=True).start()
                    elif selected == "アプリ再起動":
                        clean_shutdown_display("Restarting...")
                        subprocess.run(["sudo", "systemctl", "restart", "dap"])
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
                if pause_start_time > 0:
                    paused_duration += (time.time() - pause_start_time)
                    pause_start_time = 0.0
                    try: pygame.mixer.music.unpause()
                    except Exception: pass
                    request_display_update(is_full_refresh=False)
                else:
                    play_current_track(full_refresh=True)
                is_playing = True
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

        # 2. アルバムの曲一覧画面（MENU_TRACKS）の場合 -> カーソル位置の曲をお気に入りに追加/解除
        elif current_screen == "MENU_TRACKS":
            track_paths = albums_dict.get(selected_album, [])
            chosen_idx = cursor_idx - 1  # 0番目は "../ (戻る)"
            if 0 <= chosen_idx < len(track_paths):
                target_path = track_paths[chosen_idx]
                is_fav = toggle_favorite_by_path(target_path)
                show_fav_toast(is_fav)

        # 3. 通常再生時などの曲戻し動作
        elif playlist:
            if is_playing and get_current_sec() > 3:
                play_current_track(full_refresh=True)
            else:
                current_track_idx = (current_track_idx - 1) % len(playlist)
                play_current_track(full_refresh=True)

def handle_next_btn_press():
    """NEXTボタンの通常押し (曲送り / EQカーソル移動)"""
    global current_track_idx, eq_cursor
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
    # スレッドを介さず直接実行でも問題ありませんが、既存の構造に合わせて呼び出します
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

        # 1. 状態計算
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

        # 2. スリープタイマー監視
        if sleep_timer_end_time > 0 and now >= sleep_timer_end_time:
            sleep_timer_pending = True
            sleep_timer_end_time = 0.0

        if sleep_timer_pending and not is_playing:
            clean_shutdown_display("Sleep Timer Off...")
            subprocess.run(["sudo", "shutdown", "-h", "now"])

        # 3. 曲の再生・終了監視（cur_sec 定義後に実行）
        if is_playing:
            music_busy = pygame.mixer.music.get_busy()
            
            if not music_busy or (total_duration_sec > 0 and cur_sec >= total_duration_sec + 1):
                if sleep_timer_pending:
                    clean_shutdown_display("Sleep Timer Off...")
                    subprocess.run(["sudo", "shutdown", "-h", "now"])
                
                elif playlist:
                    if repeat_mode == 1:
                        play_current_track(full_refresh=True)
                    elif repeat_mode == 2:
                        current_track_idx = (current_track_idx + 1) % len(playlist)
                        play_current_track(full_refresh=True)
                    else:
                        if current_track_idx < len(playlist) - 1:
                            current_track_idx += 1
                            play_current_track(full_refresh=True)
                        else:
                            is_playing = False
                            request_display_update(is_full_refresh=False)

except KeyboardInterrupt:
    clean_shutdown_display("Power Off...")
    sys.exit(0)
