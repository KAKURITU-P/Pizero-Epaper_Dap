import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

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
import pygame
from PIL import Image, ImageDraw, ImageFont
import waveshare_epd.epd2in13_V4 as epd2in13
from gpiozero import Button

# --- mutagen によるメタデータ取得 ---
try:
    import mutagen
    from mutagen.easyid3 import EasyID3
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# --- オーディオ出力状態確認 ---
asoundrc_path = os.path.expanduser("~/.asoundrc")
if os.path.exists(asoundrc_path):
    audio_output_mode = "BT"
else:
    audio_output_mode = "PWM"

connected_bt_mac = "25:02:27:B5:81:BE"    # MOONDROP Ultrasonic

# --- ALSA Cライブラリ読み込み ---
try:
    asound = ctypes.cdll.LoadLibrary('libasound.so.2')
except Exception:
    asound = None

# --- 1. Pygame オーディオ初期化 ---
os.environ['SDL_AUDIODRIVER'] = 'alsa'

volume = 0.7

def safe_init_audio():
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()

        if asound:
            try:
                asound.snd_config_update_free_global()
            except Exception:
                pass

        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
        pygame.mixer.music.set_volume(volume)
    except Exception as e:
        print(f"Audio init failed ({e}).")

safe_init_audio()

# --- 2. e-Paper 初期化 ---
epd = epd2in13.EPD()
epd.init()

base_image = Image.new('1', (epd.height, epd.width), 255)
epd.displayPartBaseImage(epd.getbuffer(base_image))

def clean_shutdown_display(message="Power Off..."):
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
        time.sleep(0.5)
        epd.Clear()
        epd.sleep()
    except Exception:
        pass

def handle_signal(sig, frame):
    clean_shutdown_display("Shutting down...")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

# --- 3. フォント設定 ---
def find_japanese_font():
    font_candidates = [
        "/usr/share/fonts/truetype/takao-gothic/TakaoGothic.ttf",
        "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"
    ]
    for path in font_candidates:
        if os.path.exists(path):
            return path
    found = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True) + \
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

# --- 4. 音楽ライブラリ ---
MUSIC_DIR = os.path.expanduser("~/music")
if not os.path.exists(MUSIC_DIR):
    os.makedirs(MUSIC_DIR, exist_ok=True)

playlist_original = []
playlist = []
albums_dict = {}
selected_album = None
current_track_idx = 0

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

reload_music_library()

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

# --- 5. 画面状態 ---
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

menu_partial_count = 0
last_button_time = 0.0
DEBOUNCE_TIME = 0.2

state_lock = threading.Lock()

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

def debounce():
    global last_button_time
    now = time.time()
    if now - last_button_time < DEBOUNCE_TIME:
        return False
    last_button_time = now
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

# --- 6. 描画ワーカー ---
display_queue = queue.Queue()

def request_display_update(is_full_refresh=False):
    with display_queue.mutex:
        display_queue.queue.clear()
    display_queue.put(is_full_refresh)

def display_worker():
    global menu_partial_count
    while True:
        try:
            is_full_refresh = display_queue.get()
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

            if scr != "PLAY":
                if is_full_refresh:
                    menu_partial_count = 0
                else:
                    if menu_partial_count >= 5:
                        is_full_refresh = True
                        menu_partial_count = 0
                    else:
                        menu_partial_count += 1

            image = Image.new('1', (epd.height, epd.width), 255)
            draw = ImageDraw.Draw(image)

            if scr == "PLAY":
                state_str = "> PLAY" if playing else "|| PAUSE"
                vol_str = f"VOL: {int(cur_vol * 100)}%"

                draw.text((8, 2), state_str, font=font_main, fill=0)
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
                elif scr == "MENU_TRACKS": header_text = truncate_by_width(f"アルバム > {sel_alb}", font_main, 230) if sel_alb else "アルバム"
                elif scr == "MENU_BT": header_text = "接続設定 > Bluetooth"
                elif scr == "MENU_BT_PAIRED": header_text = "Bluetooth > 登録済み"
                elif scr == "MENU_BT_SCAN": header_text = "Bluetooth > 新規検索"
                elif scr == "MENU_WIFI": header_text = "接続設定 > Wi-Fi"
                elif scr == "MENU_CONN": header_text = "メニュー > 接続設定"

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

            if is_full_refresh:
                epd.init()
                epd.display(epd.getbuffer(image))
            else:
                epd.displayPartial(epd.getbuffer(image))

            display_queue.task_done()
        except Exception:
            time.sleep(0.1)

threading.Thread(target=display_worker, daemon=True).start()

# --- オーディオ出力切替 ---
def set_audio_output(mode, mac=None):
    global audio_output_mode, status_message
    
    status_message = f"切り替え中: {mode}"
    request_display_update(is_full_refresh=False)
    time.sleep(0.3)

    if mode == "BT":
        status_message = "Bluetooth準備中..."
        request_display_update(is_full_refresh=False)
        subprocess.run(["sudo", "systemctl", "restart", "bluealsa"], check=False)
        subprocess.run(["sudo", "systemctl", "restart", "bluez-alsa"], check=False)
        time.sleep(1.0)
        if mac:
            subprocess.run(f"echo 'connect {mac}' | bluetoothctl", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)

    if mode == "PWM":
        if os.path.exists(asoundrc_path):
            try: os.remove(asoundrc_path)
            except Exception: pass
        audio_output_mode = "PWM"

    elif mode == "BT" and mac:
        config_content = f"""pcm.!default {{
    type plug
    slave.pcm {{
        type bluealsa
        device "{mac}"
        profile "a2dp"
    }}
}}
ctl.!default {{
    type bluealsa
}}
"""
        try:
            with open(asoundrc_path, "w") as f:
                f.write(config_content)
        except Exception: pass
        audio_output_mode = "BT"

    status_message = "音声出力リセット中..."
    request_display_update(is_full_refresh=False)
    time.sleep(0.5)

    safe_init_audio()

    status_message = ""
    update_menu_items()
    request_display_update(is_full_refresh=True)

# --- 7. Bluetooth / Wi-Fi / AP 関連 ---
def get_rfkill_status():
    wifi_enabled, bt_enabled = True, True
    try:
        res = subprocess.check_output(["rfkill", "list"], text=True)
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
        p = subprocess.Popen(["sudo", "rfkill", action, target])
        p.wait(timeout=2)
        time.sleep(0.2)
    except Exception: pass

def get_ap_status():
    try:
        res = subprocess.run(["systemctl", "is-active", "hostapd"], capture_output=True, text=True)
        return res.stdout.strip() == "active"
    except Exception:
        return False

def toggle_ap_mode():
    is_active = get_ap_status()
    try:
        if is_active:
            subprocess.run(["sudo", "systemctl", "stop", "hostapd"], check=False)
            subprocess.run(["sudo", "systemctl", "stop", "dnsmasq"], check=False)
            subprocess.run(["sudo", "ip", "addr", "del", "192.168.4.1/24", "dev", "wlan0"], check=False)
            subprocess.run(["sudo", "systemctl", "restart", "NetworkManager"], check=False)
            subprocess.run(["sudo", "systemctl", "restart", "wpa_supplicant"], check=False)
        else:
            subprocess.run(["sudo", "systemctl", "stop", "wpa_supplicant"], check=False)
            subprocess.run(["sudo", "pkill", "-9", "wpa_supplicant"], check=False)
            subprocess.run(["sudo", "rfkill", "unblock", "wlan"], check=False)
            subprocess.run(["sudo", "ip", "link", "set", "wlan0", "down"], check=False)
            subprocess.run(["sudo", "ip", "addr", "flush", "dev", "wlan0"], check=False)
            subprocess.run(["sudo", "ip", "link", "set", "wlan0", "up"], check=False)
            
            subprocess.run(["sudo", "ip", "addr", "add", "192.168.4.1/24", "dev", "wlan0"], check=False)
            subprocess.run(["sudo", "systemctl", "start", "dnsmasq"], check=False)
            subprocess.run(["sudo", "systemctl", "start", "hostapd"], check=False)
        time.sleep(1)
    except Exception: pass

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
        request_display_update(is_full_refresh=False)

    threading.Thread(target=_do_connect, daemon=True).start()

def get_track_duration_sec(filepath):
    if not filepath or not os.path.exists(filepath): return 0
    if filepath.lower().endswith('.wav'):
        try:
            with contextlib.closing(wave.open(filepath, 'r')) as f:
                return int(f.getnframes() / float(f.getframerate()))
        except Exception: pass
    if HAS_MUTAGEN:
        try:
            audio = mutagen.File(filepath)
            if audio and audio.info and hasattr(audio.info, 'length'):
                return int(audio.info.length)
        except Exception: pass
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
    devs = [("25:02:27:B5:81:BE", "Ultrasonic")]
    try:
        res = subprocess.check_output(["bluetoothctl", "paired-devices"], text=True, errors='ignore')
        for line in res.strip().splitlines():
            parts = line.split(' ', 2)
            if len(parts) >= 3 and not any(d[0] == parts[1] for d in devs):
                devs.append((parts[1], parts[2].strip()))
    except Exception: pass
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

def connect_bt_device(mac, name):
    def _do_pair_and_connect():
        global status_message
        disp_name = name[:10]
        status_message = f"接続中: {disp_name}"
        request_display_update(is_full_refresh=False)
        try:
            p = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            commands = f"power on\nagent on\ndefault-agent\npair {mac}\ntrust {mac}\nconnect {mac}\nquit\n"
            p.communicate(input=commands, timeout=12)
            status_message = f"接続完了: {disp_name}"
            request_display_update(is_full_refresh=False)
            time.sleep(1.5)
            set_audio_output("BT", mac)
        except Exception:
            status_message = "接続失敗"
            request_display_update(is_full_refresh=False)
            time.sleep(2)
            status_message = ""
            request_display_update(is_full_refresh=False)
    threading.Thread(target=_do_pair_and_connect, daemon=True).start()

def update_menu_items():
    global menu_items, cursor_idx, scroll_offset, bt_paired_devices, bt_scanned_devices, saved_wifi_ssids
    cursor_idx = 0
    scroll_offset = 0

    if current_screen == "MENU_TOP":
        menu_items = [
            f"シャッフル: {'ON' if is_shuffle else 'OFF'}",
            "アルバム",
            "接続設定",
            "システム設定"
        ]
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
        items = ["../ (戻る)", f"Wi-Fi機能: {'ON' if wifi_on else 'OFF'}"]
        if wifi_on:
            items.extend([f"接続: {ssid}" for ssid in saved_wifi_ssids] if saved_wifi_ssids else ["(記憶されたWi-Fiなし)"])
        else: items.append("(Wi-Fi OFF中)")
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
        items.extend([f"接続: {name}" for _, name in bt_paired_devices])
        menu_items = items

    elif current_screen == "MENU_BT_SCAN":
        items = ["../ (戻る)", "検索開始"]
        items.extend([f"ペアリング: {name}" for _, name in bt_scanned_devices])
        menu_items = items

    elif current_screen == "MENU_SYS":
        menu_items = [
            "../ (戻る)",
            "曲ライブラリ再読み込み",
            f"IP: {get_ip_address()}",
            "ライブラリ表示",
            "アプリ再起動",
            "再起動",
            "シャットダウン"
        ]
    elif current_screen == "MENU_ALBUMS":
        album_list = sorted(list(albums_dict.keys()))
        menu_items = ["../ (戻る)"] + [f"[{alb}]" for alb in album_list]
    elif current_screen == "MENU_TRACKS":
        track_paths = albums_dict.get(selected_album, [])
        menu_items = ["../ (戻る)"] + [os.path.splitext(os.path.basename(p))[0] for p in track_paths]

def reset_inactivity_timer():
    global last_user_action_time
    last_user_action_time = time.time()

def play_current_track():
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

        try:
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            is_playing = True
        except Exception as e:
            print(f"Play error: {e}")

def toggle_shuffle():
    global is_shuffle, playlist, current_track_idx
    is_shuffle = not is_shuffle
    current_song = playlist[current_track_idx] if playlist else None
    playlist = random.sample(playlist_original, len(playlist_original)) if is_shuffle else list(playlist_original)
    if current_song and current_song in playlist:
        current_track_idx = playlist.index(current_song)

# --- 8. ボタンイベントハンドラ ---
def on_btn_menu_or_select():
    if not debounce(): return
    global current_screen, selected_album, current_track_idx, playlist, status_message
    with state_lock:
        reset_inactivity_timer()

        if current_screen == "PLAY":
            current_screen = "MENU_TOP"
            update_menu_items()
            request_display_update(is_full_refresh=True)
        else:
            if cursor_idx < len(menu_items):
                selected = menu_items[cursor_idx]

                if selected.startswith("../"):
                    if current_screen in ["MENU_CONN", "MENU_ALBUMS", "MENU_SYS"]: current_screen = "MENU_TOP"
                    elif current_screen in ["MENU_WIFI", "MENU_BT"]: current_screen = "MENU_CONN"
                    elif current_screen in ["MENU_BT_PAIRED", "MENU_BT_SCAN"]: current_screen = "MENU_BT"
                    elif current_screen == "MENU_TRACKS": current_screen = "MENU_ALBUMS"
                    else: current_screen = "MENU_TOP"
                    update_menu_items()
                    request_display_update(is_full_refresh=True)
                    return

                if current_screen == "MENU_TOP":
                    if selected.startswith("シャッフル:"):
                        toggle_shuffle()
                        update_menu_items()
                        request_display_update(is_full_refresh=False)
                    elif selected == "アルバム":
                        current_screen = "MENU_ALBUMS"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)
                    elif selected == "接続設定":
                        current_screen = "MENU_CONN"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)
                    elif selected == "システム設定":
                        current_screen = "MENU_SYS"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)

                elif current_screen == "MENU_CONN":
                    if selected == "Wi-Fi設定":
                        current_screen = "MENU_WIFI"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)
                    elif selected == "Bluetooth設定":
                        current_screen = "MENU_BT"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)
                    elif selected.startswith("APモード:"):
                        toggle_ap_mode()
                        update_menu_items()
                        request_display_update(is_full_refresh=False)

                elif current_screen == "MENU_WIFI":
                    if selected.startswith("Wi-Fi機能:"):
                        toggle_rfkill("wifi")
                        update_menu_items()
                        request_display_update(is_full_refresh=False)
                    elif selected.startswith("接続:"):
                        connect_to_wifi(selected.replace("接続: ", "").strip())

                elif current_screen == "MENU_BT":
                    if selected.startswith("Bluetooth機能:"):
                        toggle_rfkill("bt")
                        update_menu_items()
                        request_display_update(is_full_refresh=False)
                    elif selected.startswith("出力先:"):
                        new_mode = "BT" if audio_output_mode == "PWM" else "PWM"
                        set_audio_output(new_mode, connected_bt_mac)
                    elif selected == "登録済みデバイス":
                        current_screen = "MENU_BT_PAIRED"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)
                    elif selected == "新規デバイスの検索":
                        current_screen = "MENU_BT_SCAN"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)

                elif current_screen == "MENU_BT_PAIRED":
                    if selected.startswith("接続:"):
                        dev_name = selected.replace("接続: ", "").strip()
                        for mac, name in bt_paired_devices:
                            if name == dev_name:
                                connect_bt_device(mac, name)
                                break

                elif current_screen == "MENU_BT_SCAN":
                    if selected == "検索開始":
                        trigger_bt_scan()
                    elif selected.startswith("ペアリング:"):
                        dev_name = selected.replace("ペアリング: ", "").strip()
                        for mac, name in bt_scanned_devices:
                            if name == dev_name:
                                connect_bt_device(mac, name)
                                break

                elif current_screen == "MENU_ALBUMS":
                    album_list = sorted(list(albums_dict.keys()))
                    chosen_idx = cursor_idx - 1
                    if 0 <= chosen_idx < len(album_list):
                        selected_album = album_list[chosen_idx]
                        current_screen = "MENU_TRACKS"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)

                elif current_screen == "MENU_TRACKS":
                    track_paths = albums_dict.get(selected_album, [])
                    chosen_idx = cursor_idx - 1
                    if 0 <= chosen_idx < len(track_paths):
                        playlist = track_paths
                        current_track_idx = chosen_idx
                        play_current_track()
                        current_screen = "PLAY"
                        request_display_update(is_full_refresh=True)

                elif current_screen == "MENU_SYS":
                    if selected == "曲ライブラリ再読み込み":
                        reload_music_library()
                        request_display_update(is_full_refresh=False)
                    elif selected == "ライブラリ表示":
                        status_message = "MIT License\n(c) kakuritsu\nTwitter:@KAKURITU_P"
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
    if not debounce(): return
    global current_screen, is_playing, track_paused_time, pause_start_time, paused_duration
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "PLAY":
            if is_playing:
                pause_start_time = time.time()
                track_paused_time = get_current_sec()
                try: pygame.mixer.music.pause()
                except Exception: pass
                is_playing = False
            else:
                if pause_start_time > 0:
                    paused_duration += (time.time() - pause_start_time)
                    pause_start_time = 0.0
                    try: pygame.mixer.music.unpause()
                    except Exception: pass
                else:
                    play_current_track()
                is_playing = True
            request_display_update(is_full_refresh=False)
        else:
            current_screen = "PLAY"
            request_display_update(is_full_refresh=True)

def on_btn_up_action():
    if not debounce(): return
    global volume, cursor_idx, scroll_offset
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "PLAY":
            volume = min(volume + 0.05, 1.0)
            if pygame.mixer.get_init(): pygame.mixer.music.set_volume(volume)
            request_display_update(is_full_refresh=False)
        else:
            if cursor_idx > 0:
                cursor_idx -= 1
                if cursor_idx < scroll_offset: scroll_offset = cursor_idx
                request_display_update(is_full_refresh=False)

def on_btn_down_action():
    if not debounce(): return
    global volume, cursor_idx, scroll_offset
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "PLAY":
            volume = max(volume - 0.05, 0.0)
            if pygame.mixer.get_init(): pygame.mixer.music.set_volume(volume)
            request_display_update(is_full_refresh=False)
        else:
            if cursor_idx < len(menu_items) - 1:
                cursor_idx += 1
                if cursor_idx >= scroll_offset + 5: scroll_offset = cursor_idx - 4
                request_display_update(is_full_refresh=False)

def on_btn_prev():
    if not debounce(): return
    global current_track_idx
    with state_lock:
        reset_inactivity_timer()
        if playlist:
            if is_playing and get_current_sec() > 3:
                play_current_track()
            else:
                current_track_idx = (current_track_idx - 1) % len(playlist)
                play_current_track()
            request_display_update(is_full_refresh=True)

def on_btn_next():
    if not debounce(): return
    global current_track_idx
    with state_lock:
        reset_inactivity_timer()
        if playlist:
            current_track_idx = (current_track_idx + 1) % len(playlist)
            play_current_track()
            request_display_update(is_full_refresh=True)

# --- 9. GPIO割り当て ---
btn_up_act      = Button(22)
btn_down_act    = Button(27)
btn_menu_select = Button(5)
btn_prev        = Button(12)
btn_next        = Button(16)
btn_play_back   = Button(20)

btn_up_act.when_pressed      = on_btn_up_action
btn_down_act.when_pressed    = on_btn_down_action
btn_menu_select.when_pressed = on_btn_menu_or_select
btn_prev.when_pressed        = on_btn_prev
btn_next.when_pressed        = on_btn_next
btn_play_back.when_pressed   = on_btn_play_or_back

# --- 10. メインループ ---
try:
    request_display_update(is_full_refresh=True)
    while True:
        time.sleep(0.5)
        now = time.time()

        with state_lock:
            cur_sec = get_current_sec()

            if current_screen == "PLAY" and is_playing and total_duration_sec > 0:
                progress_ratio = min(1.0, cur_sec / float(total_duration_sec))
                current_segment = int(progress_ratio * 8)

                if current_segment != last_drawn_segment:
                    last_drawn_segment = current_segment
                    request_display_update(is_full_refresh=False)

            if current_screen != "PLAY" and (now - last_user_action_time > 10):
                current_screen = "PLAY"
                request_display_update(is_full_refresh=True)

            if is_playing and total_duration_sec > 0 and cur_sec >= total_duration_sec:
                if playlist:
                    current_track_idx = (current_track_idx + 1) % len(playlist)
                    play_current_track()
                    request_display_update(is_full_refresh=True)

except KeyboardInterrupt:
    clean_shutdown_display("Power Off...")
    sys.exit(0)
