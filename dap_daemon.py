import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import time
import glob
import socket
import random
import subprocess
import threading
import queue
import wave
import signal
import contextlib
import pygame
from PIL import Image, ImageDraw, ImageFont
import waveshare_epd.epd2in13_V4 as epd2in13
from gpiozero import Button

# --- mutagen によるメタデータ（曲長・アーティスト名）取得 ---
try:
    import mutagen
    from mutagen.easyid3 import EasyID3
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# --- 1. Pygame オーディオ初期化 ---
os.environ['SDL_AUDIODRIVER'] = 'alsa'
pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)

# --- 2. e-Paper 初期化 ---
epd = epd2in13.EPD()
epd.init()

base_image = Image.new('1', (epd.height, epd.width), 255)
epd.displayPartBaseImage(epd.getbuffer(base_image))

# --- 3. 日本語フォント設定 ---
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

# --- 4. 音楽ファイル・ライブラリ構造の整理 ---
MUSIC_DIR = os.path.expanduser("~/music")
if not os.path.exists(MUSIC_DIR):
    os.makedirs(MUSIC_DIR, exist_ok=True)

playlist_original = []
playlist = []
albums_dict = {}
selected_album = None

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

current_track_idx = 0
is_playing = False
is_shuffle = False
volume = 0.7
pygame.mixer.music.set_volume(volume)

# 再生時間管理用
track_paused_time = 0
total_duration_sec = 0
last_drawn_segment = -1

# --- 5. 画面・状態管理変数 ---
current_screen = "PLAY"
last_user_action_time = time.time()
cursor_idx = 0
scroll_offset = 0
menu_items = []
bt_devices = []

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
    except:
        return "未接続"

# --- 5.1 Wi-Fi / Bluetooth rfkill 制御関数 ---
def get_rfkill_status():
    wifi_enabled = True
    bt_enabled = True
    try:
        res = subprocess.check_output(["rfkill", "list"], text=True)
        current_type = None
        for line in res.splitlines():
            line_lower = line.lower()
            if "wlan" in line_lower or "wireless" in line_lower:
                current_type = "wifi"
            elif "bluetooth" in line_lower:
                current_type = "bt"
            
            if "soft blocked: yes" in line_lower:
                if current_type == "wifi":
                    wifi_enabled = False
                elif current_type == "bt":
                    bt_enabled = False
    except Exception:
        pass
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
    except Exception:
        pass

def get_track_duration_sec(filepath):
    if not filepath or not os.path.exists(filepath):
        return 0
    
    if filepath.lower().endswith('.wav'):
        try:
            with contextlib.closing(wave.open(filepath, 'r')) as f:
                frames = f.getnframes()
                rate = f.getframerate()
                return int(frames / float(rate))
        except Exception:
            pass

    if HAS_MUTAGEN:
        try:
            audio = mutagen.File(filepath)
            if audio is not None and audio.info is not None and hasattr(audio.info, 'length'):
                return int(audio.info.length)
        except Exception:
            pass

    return 0

def get_track_artist_info(filepath):
    if not filepath or not os.path.exists(filepath):
        return "不明なアーティスト"

    if HAS_MUTAGEN:
        try:
            audio = mutagen.File(filepath)
            if audio is not None:
                if 'artist' in audio and audio['artist']:
                    return str(audio['artist'][0]).strip()
                elif 'TPE1' in audio and audio['TPE1']:
                    return str(audio['TPE1']).strip()
        except Exception:
            pass

    rel_path = os.path.relpath(filepath, MUSIC_DIR)
    folder_name = os.path.dirname(rel_path)
    if folder_name:
        return folder_name

    return "不明なアーティスト"

def format_time_str(seconds):
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins:02d}:{secs:02d}"

# --- 6. Bluetooth 制御 ---
def get_bt_devices():
    devs = []
    try:
        subprocess.run(["bluetoothctl", "--timeout", "2", "scan", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        res = subprocess.check_output(["bluetoothctl", "devices"]).decode('utf-8', errors='ignore')
        lines = res.strip().split('\n')
        for line in lines:
            parts = line.split(' ', 2)
            if len(parts) >= 3:
                mac = parts[1]
                name = parts[2].strip()
                if mac and name and (mac, name) not in devs:
                    devs.append((mac, name))
    except Exception:
        pass
    return devs

def connect_bt_device(mac):
    def _do_pair_and_connect():
        try:
            commands = [
                "agent on",
                "default-agent",
                f"pair {mac}",
                f"trust {mac}",
                f"connect {mac}"
            ]
            for cmd in commands:
                subprocess.run(["bluetoothctl"] + cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                time.sleep(1)
        except Exception:
            pass
    threading.Thread(target=_do_pair_and_connect, daemon=True).start()

# --- 電子ペーパー終了／画面クリア処理 ---
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
        time.sleep(1.0)
        epd.Clear()
        epd.sleep()
    except Exception:
        pass

def handle_signal(sig, frame):
    clean_shutdown_display("Shutting down...")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

# --- 7. マルチスレッド用描画キュー＆ワーカー ---
display_queue = queue.Queue()

def request_display_update(is_full_refresh=False):
    with display_queue.mutex:
        display_queue.queue.clear()
    display_queue.put(is_full_refresh)

def display_worker():
    global menu_partial_count, last_drawn_segment
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
                
                pos_ms = pygame.mixer.music.get_pos() if playing else track_paused_time
                current_sec = max(0, pos_ms // 1000) if pos_ms >= 0 else 0
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
                    last_drawn_segment = segment
                    
                    if segment > 0:
                        max_inner_w = bar_x2 - bar_x1 - 4
                        fill_w = int(max_inner_w * (segment / 8.0))
                        draw.rectangle(
                            [bar_x1 + 2, bar_y1 + 2, bar_x1 + 2 + fill_w, bar_y2 - 2],
                            outline=0,
                            fill=0
                        )

                time_str = f"Time: {format_time_str(tot_sec)}"
                draw.text((8, 87), time_str, font=font_main, fill=0)

            else:
                header_text = "メニュー"
                if scr == "MENU_SYS": header_text = "メニュー > システム"
                elif scr == "MENU_ALBUMS": header_text = "メニュー > アルバム"
                elif scr == "MENU_TRACKS": header_text = truncate_by_width(f"アルバム > {sel_alb}", font_main, 230) if sel_alb else "アルバム"
                elif scr == "MENU_BT": header_text = "メニュー > Bluetooth"

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

            if is_full_refresh:
                epd.init()
                epd.display(epd.getbuffer(image))
            else:
                epd.displayPartial(epd.getbuffer(image))

            display_queue.task_done()
        except Exception as e:
            time.sleep(0.1)

threading.Thread(target=display_worker, daemon=True).start()

def update_menu_items():
    global menu_items, cursor_idx, scroll_offset, bt_devices
    cursor_idx = 0
    scroll_offset = 0

    if current_screen == "MENU_TOP":
        shuf_str = "ON" if is_shuffle else "OFF"
        menu_items = [f"シャッフル: {shuf_str}", "Bluetooth設定", "システム設定", "アルバム"]
    elif current_screen == "MENU_SYS":
        wifi_on, bt_on = get_rfkill_status()
        wifi_str = "ON" if wifi_on else "OFF"
        bt_str = "ON" if bt_on else "OFF"
        menu_items = [
            "../ (戻る)",
            "曲ライブラリ再読み込み",
            f"Wi-Fi: {wifi_str}",
            f"Bluetooth: {bt_str}",
            f"IPアドレス: {get_ip_address()}",
            "再起動",
            "シャットダウン"
        ]
    elif current_screen == "MENU_ALBUMS":
        album_list = sorted(list(albums_dict.keys()))
        menu_items = ["../ (戻る)"] + [f"[{alb}]" for alb in album_list]
    elif current_screen == "MENU_TRACKS":
        track_paths = albums_dict.get(selected_album, [])
        track_names = [os.path.splitext(os.path.basename(p))[0] for p in track_paths]
        menu_items = ["../ (戻る)"] + track_names
    elif current_screen == "MENU_BT":
        bt_devices = get_bt_devices()
        menu_items = ["../ (戻る)", "機器を検索/更新"] + [f"接続: {name}" for mac, name in bt_devices]

def reset_inactivity_timer():
    global last_user_action_time
    last_user_action_time = time.time()

def play_current_track():
    global is_playing, total_duration_sec, track_paused_time, last_drawn_segment
    if playlist:
        filepath = playlist[current_track_idx]
        total_duration_sec = get_track_duration_sec(filepath)
        track_paused_time = 0
        last_drawn_segment = 0
        pygame.mixer.music.load(filepath)
        pygame.mixer.music.play()
        is_playing = True

def toggle_shuffle():
    global is_shuffle, playlist, current_track_idx
    is_shuffle = not is_shuffle
    current_song = playlist[current_track_idx] if playlist else None
    
    if is_shuffle:
        random.shuffle(playlist)
    else:
        playlist = list(playlist_original)
        
    if current_song and current_song in playlist:
        current_track_idx = playlist.index(current_song)

# --- 8. ボタンイベントハンドラ ---
def on_btn_menu_or_select():
    if not debounce(): return
    global current_screen, selected_album, current_track_idx, playlist
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
                    if current_screen == "MENU_TRACKS":
                        current_screen = "MENU_ALBUMS"
                    else:
                        current_screen = "MENU_TOP"
                    update_menu_items()
                    request_display_update(is_full_refresh=True)
                    return

                if current_screen == "MENU_TOP":
                    if selected.startswith("シャッフル:"):
                        toggle_shuffle()
                        update_menu_items()
                        request_display_update(is_full_refresh=False)
                    elif selected == "Bluetooth設定":
                        current_screen = "MENU_BT"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)
                    elif selected == "システム設定":
                        current_screen = "MENU_SYS"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)
                    elif selected == "アルバム":
                        current_screen = "MENU_ALBUMS"
                        update_menu_items()
                        request_display_update(is_full_refresh=True)

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

                elif current_screen == "MENU_BT":
                    if selected == "機器を検索/更新":
                        update_menu_items()
                        request_display_update(is_full_refresh=False)
                    elif selected.startswith("接続:"):
                        dev_idx = cursor_idx - 2
                        if 0 <= dev_idx < len(bt_devices):
                            mac = bt_devices[dev_idx][0]
                            connect_bt_device(mac)

                elif current_screen == "MENU_SYS":
                    if selected == "曲ライブラリ再読み込み":
                        reload_music_library()
                        request_display_update(is_full_refresh=False)
                    elif selected.startswith("Wi-Fi:"):
                        toggle_rfkill("wifi")
                        update_menu_items()
                        request_display_update(is_full_refresh=False)
                    elif selected.startswith("Bluetooth:"):
                        toggle_rfkill("bt")
                        update_menu_items()
                        request_display_update(is_full_refresh=False)
                    elif selected == "再起動":
                        clean_shutdown_display("Rebooting...")
                        os.system("sudo reboot")
                    elif selected == "シャットダウン":
                        clean_shutdown_display("Power Off...")
                        os.system("sudo shutdown -h now")

def on_btn_play_or_back():
    if not debounce(): return
    global current_screen, is_playing, track_paused_time
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "PLAY":
            if is_playing:
                track_paused_time = pygame.mixer.music.get_pos()
                pygame.mixer.music.pause()
                is_playing = False
            else:
                if track_paused_time > 0:
                    pygame.mixer.music.unpause()
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
            pygame.mixer.music.set_volume(volume)
            request_display_update(is_full_refresh=False)
        else:
            if cursor_idx > 0:
                cursor_idx -= 1
                if cursor_idx < scroll_offset:
                    scroll_offset = cursor_idx
                request_display_update(is_full_refresh=False)

def on_btn_down_action():
    if not debounce(): return
    global volume, cursor_idx, scroll_offset
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "PLAY":
            volume = max(volume - 0.05, 0.0)
            pygame.mixer.music.set_volume(volume)
            request_display_update(is_full_refresh=False)
        else:
            if cursor_idx < len(menu_items) - 1:
                cursor_idx += 1
                if cursor_idx >= scroll_offset + 5:
                    scroll_offset = cursor_idx - 4
                request_display_update(is_full_refresh=False)

def on_btn_prev():
    if not debounce(): return
    global current_track_idx
    with state_lock:
        reset_inactivity_timer()
        if playlist:
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

# --- 9. GPIOピン割り当て ---
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
            if current_screen == "PLAY" and is_playing and total_duration_sec > 0:
                pos_ms = pygame.mixer.music.get_pos()
                current_sec = max(0, pos_ms // 1000) if pos_ms >= 0 else 0
                current_segment = int((current_sec / float(total_duration_sec)) * 8)
                
                if current_segment != last_drawn_segment:
                    request_display_update(is_full_refresh=False)

            if current_screen != "PLAY" and (now - last_user_action_time > 10):
                current_screen = "PLAY"
                request_display_update(is_full_refresh=True)

            if is_playing and not pygame.mixer.music.get_busy() and pygame.mixer.music.get_pos() == -1:
                if playlist:
                    current_track_idx = (current_track_idx + 1) % len(playlist)
                    play_current_track()
                    request_display_update(is_full_refresh=True)

except KeyboardInterrupt:
    clean_shutdown_display("Power Off...")
