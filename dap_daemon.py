import smbus2
import sys

try:  # systemd/journaldのパイプでもprintを即時に出力する
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass
import os
import time
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
import shutil
import tempfile
import pygame
from collections import deque
from PIL import Image, ImageDraw, ImageOps
import waveshare_epd.epd2in13_V4 as epd2in13
from gpiozero import Button
try:
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    HAS_DBUS_AGENT = True
except ImportError:
    HAS_DBUS_AGENT = False
from bluealsa_utils import (
    build_bluealsa_asoundrc,
    build_pwm_asoundrc,
    build_usb_otg_asoundrc,
    find_usb_audio_device,
    has_bluetooth_acl_link,
    pcm_list_contains_device,
)
from system_stats_utils import (
    cpu_usage_percent,
    graph_points,
    parse_cpu_times,
    parse_temperature_celsius,
    summarize,
)
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

from battery_utils import get_battery_info, get_battery_percent  # noqa: E402
from audio_device_utils import (  # noqa: E402
    asoundrc_uses_usb_otg,
    get_audio_buffer_size,
    get_usb_otg_sample_rate,
    resolve_usb_otg_device,
)
from font_utils import find_japanese_font, get_font  # noqa: E402
from icon_utils import get_cropped_ship, load_playback_mode_icons  # noqa: E402
from render_utils import (  # noqa: E402
    format_ram_bytes,
    format_size_bytes,
    format_time_str,
    truncate_by_width,
)
from version_utils import get_display_version  # noqa: E402

SHIPS_PNG_PATH = os.path.join(BASE_DIR, "ships.png")

# --- mutagen によるメタデータ取得 ---
try:
    import mutagen
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# 接続先 MAC アドレス。未選択のまま「BTに変更」した場合に他人の機器へ
# ペアリング試行しないよう、既定値は空(=未選択)とする。
connected_bt_mac = ""

# 出力デバイス(USB DAC / Bluetooth)が消えてmixerを止めている間はTrue。
audio_output_lost = False
# 消失時の復元方法: "resume"=消えた位置から再開 / "restart"=曲頭から再生 / None=何もしない
audio_lost_action = None

# --- オーディオ出力状態確認 & 初期サービス起動 ---
asoundrc_path = os.path.expanduser("~/.asoundrc")

def write_text_atomically(path, content):
    """設定ファイルを一時ファイル経由で安全に置き換える。

    一時ファイル名を呼び出しごとに一意にし、複数スレッドからの
    並行書き込みでも互いの一時ファイルを壊さないようにする。
    起動直後のPWM設定書き込みからも使うため、モジュール前方で定義する。
    """
    directory = os.path.dirname(os.path.abspath(path))
    temp_fd, temp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".", dir=directory)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
USB_OTG_ALSA_DEVICE_ENV = os.environ.get("DAP_USB_OTG_ALSA_DEVICE")

USB_OTG_ALSA_DEVICE = resolve_usb_otg_device(USB_OTG_ALSA_DEVICE_ENV)

if os.path.exists(asoundrc_path):
    with open(asoundrc_path, "r") as f:
        content = f.read()
    if "bluealsa" in content:
        audio_output_mode = "BT"
    elif asoundrc_uses_usb_otg(content, USB_OTG_ALSA_DEVICE, USB_OTG_ALSA_DEVICE_ENV):
        audio_output_mode = "USBOTG"
    else:
        audio_output_mode = "PWM"
else:
    audio_output_mode = "PWM"

if audio_output_mode == "PWM":
    try:
        write_text_atomically(asoundrc_path, build_pwm_asoundrc())
    except Exception as error:
        print(f"PWM ALSA設定更新失敗: {error}")

# オーディオ経路は選択状態に合わせる。Wi-Fi / Bluetooth 無線状態は変更しない。
if audio_output_mode == "BT":
    subprocess.run(["sudo", "rfkill", "unblock", "bluetooth"], check=False)
    subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=False)
    subprocess.run(["sudo", "systemctl", "start", "bluealsa"], check=False)
else:
    subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)

# --- ALSA Cライブラリ読み込み ---
try:
    asound = ctypes.cdll.LoadLibrary('libasound.so.2')
except Exception:
    asound = None

# --- 1. Pygame オーディオ初期化 ---
os.environ['SDL_AUDIODRIVER'] = 'alsa'

volume = 0.2
# PLAY画面右端スロットの表示切替用。音量変更時のタイムスタンプ(0=通常のBAT表示)。
volume_changed_at = 0.0

def set_cur_volume(vol):
    """画面音量を指定されたALSA音量値へ変換して設定する。"""
    if pygame.mixer.get_init():
        alsa_levels = (
            0.00, 0.01, 0.02, 0.03, 0.04,
            0.06, 0.08, 0.11, 0.15, 0.20,
            0.25, 0.30, 0.36, 0.42, 0.49,
            0.56, 0.64, 0.72, 0.81, 0.90,
            1.00,
        )
        level_index = max(0, min(20, int(round(vol * 20))))
        scaled_vol = alsa_levels[level_index]
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
        if audio_output_mode == "BT":
            frequency = 48000
        elif audio_output_mode == "USBOTG":
            frequency = get_usb_otg_sample_rate(USB_OTG_ALSA_DEVICE)
        else:
            frequency = 44100
        buffer_size = get_audio_buffer_size()
        pygame.mixer.init(frequency=frequency, size=-16, channels=2, buffer=buffer_size)
        set_cur_volume(volume)
        print(f"Audio initialized in [{audio_output_mode}] mode ({frequency} Hz, buffer {buffer_size}).")
        return True
    except Exception as e:
        print(f"Audio init failed in [{audio_output_mode}] mode ({e}).")
        return False


def read_proc_asound_cards():
    try:
        with open("/proc/asound/cards", "r", encoding="utf-8", errors="replace") as cards_file:
            return cards_file.read()
    except OSError:
        return ""

def usb_audio_present():
    """USB音声カードがALSAに見えているか。DAP_USB_OTG_ALSA_DEVICE指定時は常にTrue。"""
    if USB_OTG_ALSA_DEVICE_ENV:
        return True
    return find_usb_audio_device(read_proc_asound_cards()) is not None

def init_usb_otg_audio(attempts=4, delay=1.0):
    """USB DACを検出してALSA設定を書き、mixerを初期化する。未接続なら待つ。"""
    global USB_OTG_ALSA_DEVICE
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(delay)
        if not usb_audio_present():
            continue
        USB_OTG_ALSA_DEVICE = resolve_usb_otg_device(USB_OTG_ALSA_DEVICE_ENV)
        try:
            write_text_atomically(asoundrc_path, build_usb_otg_asoundrc(USB_OTG_ALSA_DEVICE))
        except Exception as error:
            print(f"USB OTG ALSA設定更新失敗: {error}")
            continue
        if safe_init_audio():
            return True
    return False


if audio_output_mode == "USBOTG":
    # USBモードのまま、DACが見つかり次第使う。見つからなければ待機して自動復帰する。
    if not init_usb_otg_audio(attempts=3, delay=1.0):
        print("USB DACを初期化できません。接続され次第、自動で復帰します。")
        audio_output_lost = True
elif not safe_init_audio() and audio_output_mode == "BT":
    print("Bluetooth音声を初期化できません。接続され次第、自動で復帰します。")
    audio_output_lost = True

# --- 2. e-Paper 初期化 ---
epd = epd2in13.EPD()

# パネル(SPI/GPIO)へ触るのはdisplay_workerとシャットダウン演出だけにし、
# このロックで二重アクセスを防ぐ。BUSYはパネル側の入力信号のため、
# 競合でリフレッシュが中断されると白画面へ上書きできなくなる。
epd_lock = threading.Lock()

# シャットダウン演出は1回だけ実行する。メニュー経由の再起動後にSIGTERMが
# 届いても二重に描画しないよう、進行中/完了をフラグで管理する。
shutdown_display_lock = threading.Lock()
shutdown_display_owner = None   # 描画中スレッドのident(同一スレッド再帰呼び出し検出用)
shutdown_display_done = False
shutdown_exit_requested = False # SIGTERMで描画を中断しないための終了要求フラグ

def clean_shutdown_display(message="Power Off..."):
    global shutdown_display_owner, shutdown_display_done
    if shutdown_display_owner == threading.get_ident():
        # SIGTERMハンドラなど同一スレッドからの再帰呼び出しは実行中の描画に委ねる
        return
    if not shutdown_display_lock.acquire(timeout=30.0):
        print("Shutdown display skipped: another shutdown display is running.")
        return
    try:
        if shutdown_display_done:
            return
        shutdown_display_owner = threading.get_ident()
        # 予約中のsettings.json書き込み(EQ調整など)を終了前に確定させる。
        try:
            flush_pending_settings_save()
        except Exception as error:
            print(f"Settings flush error: {error}")
        save_resume_state()
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

        try:
            with epd_lock:
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
        # 描画に失敗してもパネル状態は不定のため再試行はしない
        shutdown_display_done = True
    finally:
        shutdown_display_owner = None
        shutdown_display_lock.release()

def handle_signal(sig, frame):
    global shutdown_exit_requested
    shutdown_exit_requested = True
    clean_shutdown_display("Shutting down...")
    if shutdown_display_owner == threading.get_ident():
        # 描画を自分で実行中の場合はSystemExitで中断しない。メインループ側で終了する。
        return
    if not shutdown_display_done:
        # 別スレッドが描画中の可能性があるため、完了を待ってから終了する。
        deadline = time.monotonic() + 30.0
        while not shutdown_display_done and time.monotonic() < deadline:
            time.sleep(0.2)
    # 描画を自分で実行していない場合のみここへ届く。即終了する。
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

# --- 3. フォント設定 ---
FONT_PATH = find_japanese_font()

font_title  = get_font(FONT_PATH, 20)
font_main   = get_font(FONT_PATH, 14)
font_small  = get_font(FONT_PATH, 12)
font_clock  = get_font(FONT_PATH, 30)

playback_mode_icons = load_playback_mode_icons(os.path.join(BASE_DIR, "shuffle_repeat.png"))

# --- 4. 音楽ライブラリ & お気に入り & レジューム ---
MUSIC_DIR = os.path.expanduser("~/music")
DATA_DIR = "/home/pi/dap"
FAV_FILE = os.path.join(DATA_DIR, "favorites.json")
RESUME_FILE = os.path.join(DATA_DIR, "resume.json")
PLAY_DATA_FILE = os.path.join(DATA_DIR, "play_data.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
PLAYBACK_HISTORY_LIMIT = 30
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
ROOT_REL_DIR = "."

if not os.path.exists(MUSIC_DIR):
    os.makedirs(MUSIC_DIR, exist_ok=True)
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

playlist_original = []
playlist = []
playlist_base = []
albums_dict = {}
selected_album = None
library_tree = {}
library_menu_entries = []
current_track_idx = 0
favorites_list = []
favorites_file_mtime = None
playback_history = []
is_shuffle = False

# リピートモード: 0: OFF, 1: REPEAT_ONE, 2: REPEAT_ALL
repeat_mode = 0

# スリープタイマー (分)
sleep_timer_minutes = 0
sleep_timer_end_time = 0.0
sleep_timer_pending = False  
battery_shutdown_pending = False

# イースターエッグ & EQ 関連変数
easter_click_count = 0
last_easter_click_time = 0.0
is_clock_unlocked = False
is_eq_unlocked = False
eq_bands = ["BASS", "MID", "TREBLE"]
eq_values = {"BASS": 0, "MID": 0, "TREBLE": 0}
eq_cursor = 0

def load_settings():
    global is_clock_unlocked, is_eq_unlocked, eq_values, is_shuffle, repeat_mode
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("settings.json must contain a JSON object")

        is_clock_unlocked = bool(data.get("is_clock_unlocked", False))
        is_eq_unlocked = bool(data.get("is_eq_unlocked", False))
        saved_eq = data.get("eq_values", {})
        if isinstance(saved_eq, dict):
            for band in eq_bands:
                value = saved_eq.get(band, 0)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    eq_values[band] = max(-6, min(6, int(value)))
        # 再生設定もsettings.jsonに永続化する。レジューム失敗時でも設定を失わない。
        is_shuffle = bool(data.get("is_shuffle", False))
        saved_repeat = data.get("repeat_mode", repeat_mode)
        if isinstance(saved_repeat, int) and not isinstance(saved_repeat, bool) and 0 <= saved_repeat <= 2:
            repeat_mode = saved_repeat
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
        print(f"Settings load error: {e}")

def save_settings():
    temp_file = SETTINGS_FILE + ".tmp"
    try:
        data = {
            "is_clock_unlocked": is_clock_unlocked,
            "is_eq_unlocked": is_eq_unlocked,
            "eq_values": {band: eq_values[band] for band in eq_bands},
            "is_shuffle": is_shuffle,
            "repeat_mode": repeat_mode,
        }
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_file, SETTINGS_FILE)
    except (OSError, TypeError, ValueError) as e:
        print(f"Settings save error: {e}")
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass

# EQ調整のように連打が続く操作でのSD書き込みを避けるため、
# settings.jsonの保存は短い遅延を置いてまとめて行う。
_settings_save_dirty = False
_settings_save_due = 0.0

def schedule_settings_save(delay=1.5):
    """settings.jsonの書き込みをdelay秒後にまとめて実行するよう予約する。"""
    global _settings_save_dirty, _settings_save_due
    _settings_save_dirty = True
    _settings_save_due = time.time() + delay

def flush_pending_settings_save():
    """予約済みのsettings.json書き込みを即時実行する(終了時・メインループから)。"""
    global _settings_save_dirty
    if _settings_save_dirty:
        _settings_save_dirty = False
        save_settings()

def load_favorites():
    global favorites_list, favorites_file_mtime
    if os.path.exists(FAV_FILE):
        try:
            with open(FAV_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not all(isinstance(path, str) for path in data):
                raise ValueError("favorites.json must contain a JSON array of paths")
            favorites_list = data
            favorites_file_mtime = os.stat(FAV_FILE).st_mtime_ns
        except Exception:
            favorites_list = []
            favorites_file_mtime = None
    else:
        favorites_file_mtime = None

def save_favorites():
    global favorites_file_mtime
    temp_file = FAV_FILE + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(favorites_list, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_file, FAV_FILE)
        favorites_file_mtime = os.stat(FAV_FILE).st_mtime_ns
    except Exception as e:
        print(f"Fav save error: {e}")
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass

def sync_favorites():
    """管理画面など別プロセスによるお気に入り更新を取り込む"""
    try:
        mtime = os.stat(FAV_FILE).st_mtime_ns
    except OSError:
        return
    if mtime != favorites_file_mtime:
        load_favorites()

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

# お気に入りの追加・解除はWeb管理画面のみで行う(本体ボタンには割り当てなし)。
# favorites_listはsync_favorites()がWeb側のfavorites.json変更を取り込む。

# --- お気に入り追加・削除のヘルパー関数 ---
def toggle_favorite_by_path(filepath):
    """指定されたファイルパスをお気に入りに追加/削除する(アルバム階層の次曲ボタン用)"""
    global favorites_list
    sync_favorites()
    if filepath in favorites_list:
        favorites_list.remove(filepath)
        is_fav = False
    else:
        favorites_list.append(filepath)
        is_fav = True
    save_favorites()
    return is_fav

# save_resume_stateはstate_lockを取らない経路(watchdogの復帰処理)からも
# 呼ばれるため、一時ファイル競合を避ける専用ロックで直列化する。
resume_save_lock = threading.Lock()

def save_resume_state():
    if not playlist or current_track_idx >= len(playlist):
        return
    # 電源断に備え、他のJSONと同様に一時ファイル+置換でアトミックに書き込む。
    # パスはMUSIC_DIR基準の相対表記・indentなしで書き、曲替えごとの書き込み量を抑える。
    # 再生設定(シャッフル等)はsettings.json側に保存するため、ここには含めない。
    temp_file = RESUME_FILE + ".tmp"
    try:
        with resume_save_lock:
            def _rel(path):
                try:
                    return os.path.relpath(path, MUSIC_DIR)
                except ValueError:
                    return path
            data = {
                "filepath": _rel(playlist[current_track_idx]),
                "track_idx": current_track_idx,
                "playlist": [_rel(path) for path in playlist],
                "playlist_base": [_rel(path) for path in (playlist_base or playlist)],
            }
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, RESUME_FILE)
    except Exception as e:
        print(f"Resume save error: {e}")
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass

def load_resume_state():
    global current_track_idx, playlist, playlist_base
    if os.path.exists(RESUME_FILE):
        try:
            with open(RESUME_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                filepath = data.get("filepath")
                # 再生設定はsettings.jsonに移ったため、ここでは読まない。
                library_paths = set(playlist_original)
                def _restore_path(path):
                    """保存値はMUSIC_DIR基準の相対パス。絶対パス(旧形式)も受ける。"""
                    if not isinstance(path, str):
                        return None
                    absolute = path if os.path.isabs(path) else os.path.normpath(os.path.join(MUSIC_DIR, path))
                    return absolute if absolute in library_paths else None
                saved_playlist = data.get("playlist")
                saved_base = data.get("playlist_base")
                if isinstance(saved_playlist, list):
                    restored = [p for p in (_restore_path(path) for path in saved_playlist) if p]
                    target = _restore_path(filepath)
                    if restored and target in restored:
                        playlist = restored
                        if isinstance(saved_base, list):
                            playlist_base = [p for p in (_restore_path(path) for path in saved_base) if p]
                        if not playlist_base:
                            playlist_base = list(restored)
                        current_track_idx = playlist.index(target)
                        return
                # Older resume files saved only the track path. If that track is
                # favorited, use the favorite list as the best available context.
                target = _restore_path(filepath)
                if target and target in favorites_list:
                    restored_favorites = [path for path in favorites_list if path in library_paths]
                    if target in restored_favorites:
                        playlist = list(restored_favorites)
                        playlist_base = list(restored_favorites)
                        current_track_idx = playlist.index(target)
                        print("Resume restored using favorites playlist (legacy state)")
                        return
                if target and target in playlist:
                    current_track_idx = playlist.index(target)
                    playlist_base = list(playlist)
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
    global playlist_original, playlist, playlist_base, albums_dict, current_track_idx, library_tree

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
    playlist_base = list(playlist_original)
    current_track_idx = 0

    albums_dict = {}
    for rel_dir, entry in library_tree.items():
        if entry["tracks"] or entry["subdirs"]:
            albums_dict[rel_dir] = list(entry["tracks"])

load_favorites()
reload_music_library()
load_playback_history()
load_settings()

is_playing = False
load_resume_state()

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
# menu_entriesはmenu_itemsと同じ長さで、各項目の操作ID(id)と付随データ(data)を
# 持つ。選択処理は表示ラベルではなくidで分岐するため、文言変更に依存しない。
menu_entries = []
# menu_itemsと同じ長さ。お気に入り済みの曲行など太字(二度描き)表示する行のフラグ。
menu_item_bold = []

bt_paired_devices = []
bt_paired_devices_loading = False
bt_connected_macs = set()
bt_scanned_devices = []
saved_wifi_ssids = []
saved_wifi_connections = []
wifi_menu_enabled = True
wifi_menu_current_ssid = None
wifi_menu_loading = False
wifi_menu_loading_since = 0.0
wifi_menu_load_error = False
wifi_menu_load_generation = 0
wifi_connect_in_progress = False
status_message = ""
if audio_output_lost:
    status_message = "USB DAC待機中" if audio_output_mode == "USBOTG" else "BT接続待機中"
is_bt_scanning = False
bt_connect_in_progress = False
audio_transition_in_progress = False
system_ram_usage_samples = deque(maxlen=60)
# CPU使用率(5秒平均と、その間の1秒ごとの最大値)とSoC温度。60点 = 直近5分。
system_cpu_mean_samples = deque(maxlen=60)
system_cpu_peak_samples = deque(maxlen=60)
system_temp_samples = deque(maxlen=60)
system_cpu_temp_snapshot = None
_cpu_prev_times = None
_cpu_tick_values = []
system_resource_snapshot = None
resource_sampled_at = 0.0


def status_notification_expiry_thread():
    """Clear transient status messages five seconds after they change."""
    global status_message
    in_progress_prefixes = (
        "接続中:", "再接続中", "音声デバイス待機中", "切替中:", "更新データを取得中",
        "AP ON中", "AP OFF中", "BT検索中", "Wi-Fi接続中",
    )
    observed_message = ""
    changed_at = time.monotonic()
    while True:
        message = status_message
        now = time.monotonic()
        if message != observed_message:
            observed_message = message
            changed_at = now
        elif message and not message.startswith(in_progress_prefixes) and now - changed_at >= 5.0:
            # 他スレッド(state_lock下)が書き換えた新しいメッセージを
            # 期限切れ扱いで消してしまわないよう、ロック下で最新値を確認してから消す。
            with state_lock:
                if status_message == message:
                    status_message = ""
                    observed_message = ""
                    changed_at = now
                    request_display_update(is_full_refresh=False)
        time.sleep(0.2)


threading.Thread(target=status_notification_expiry_thread, daemon=True).start()

last_btn_times = {}
DEBOUNCE_TIME = 0.25

state_lock = threading.Lock()

def apply_eq_settings():
    bass = eq_values["BASS"]
    mid = eq_values["MID"]
    treble = eq_values["TREBLE"]

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
    except Exception as error:
        print(f"EQ Control Error: {error}")

apply_eq_settings()

random_ships_cache = None
last_cat_clock_minute = -1

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
        s.settimeout(0.5)
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

    try:
        tree = subprocess.check_output(
            [
                "busctl", "--system", "tree", "org.bluealsa",
            ],
            text=True,
            errors="ignore",
            timeout=3.0,
        )

        mac_path_part = connected_bt_mac.replace(":", "_").lower()
        expected_device_path = f"/dev_{mac_path_part}/"
        pcm_paths = []
        for line in tree.splitlines():
            match = re.search(r"(/org/bluealsa(?:/[A-Za-z0-9_]+)+)", line)
            if not match:
                continue
            path = match.group(1)
            if expected_device_path in path.lower() and path.lower().endswith("/a2dpsrc/sink"):
                pcm_paths.append(path)

        if not pcm_paths:
            return "A2DP待機中"

        # busctl prints string properties as: s "SBC"
        for pcm_path in pcm_paths:
            res = subprocess.check_output(
                [
                    "busctl", "--system", "get-property",
                    "org.bluealsa", pcm_path,
                    "org.bluealsa.PCM1", "Codec",
                ],
                text=True,
                errors="ignore",
                timeout=3.0,
            )
            value_type, _, value = res.strip().partition(" ")
            if value_type == "s" and value.strip().startswith('"') and value.strip().endswith('"'):
                codec = value.strip()[1:-1].strip()
                if codec:
                    return codec.upper()

    except (subprocess.SubprocessError, OSError) as error:
        print(f"BTコーデック取得エラー: {error}")

    return "取得待ち"

def sample_system_resource_usage():
    """Update storage usage and a rolling RAM average in the background."""
    global system_resource_snapshot, resource_sampled_at
    sample_time = time.monotonic()
    try:
        disk = shutil.disk_usage("/")
        meminfo = {}
        with open("/proc/meminfo", "r", encoding="ascii") as mem_file:
            for line in mem_file:
                key, _, value = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    meminfo[key] = int(value.strip().split()[0]) * 1024

        total_ram = meminfo["MemTotal"]
        available_ram = meminfo["MemAvailable"]
        used_ram = max(0, total_ram - available_ram)

        with state_lock:
            system_ram_usage_samples.append((used_ram, total_ram))
            sample_count = len(system_ram_usage_samples)
            average_used_ram = sum(sample[0] for sample in system_ram_usage_samples) / sample_count
            average_total_ram = sum(sample[1] for sample in system_ram_usage_samples) / sample_count
            average_ram_percent = (
                average_used_ram * 100.0 / average_total_ram if average_total_ram else 0.0
            )
            system_resource_snapshot = {
                "storage_used": disk.used,
                "storage_total": disk.total,
                "storage_percent": disk.used * 100.0 / disk.total if disk.total else 0.0,
                "ram_average_used": average_used_ram,
                "ram_average_total": average_total_ram,
                "ram_average_percent": average_ram_percent,
                "sample_count": sample_count,
                "sampled_at": sample_time,
            }
            resource_sampled_at = sample_time
    except (OSError, KeyError, ValueError) as error:
        print(f"System resource read error: {error}")
        with state_lock:
            system_resource_snapshot = {"error": str(error), "sampled_at": sample_time}
            resource_sampled_at = sample_time

def read_cpu_times():
    try:
        with open("/proc/stat", "r", encoding="ascii") as stat_file:
            return parse_cpu_times(stat_file.readline())
    except (OSError, ValueError):
        return None

def read_soc_temperature():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="ascii") as temp_file:
            return parse_temperature_celsius(temp_file.read())
    except (OSError, ValueError):
        return None

def sample_cpu_tick():
    """1秒ごとに呼び、/proc/stat の差分からCPU使用率を貯める。"""
    global _cpu_prev_times
    current = read_cpu_times()
    usage = cpu_usage_percent(_cpu_prev_times, current)
    _cpu_prev_times = current
    if usage is not None:
        _cpu_tick_values.append(usage)

def commit_cpu_temp_sample():
    """5秒ごとに呼び、貯めたCPU値の平均・最大と温度を履歴に追加して集計する。"""
    global system_cpu_temp_snapshot
    values = list(_cpu_tick_values)
    _cpu_tick_values.clear()
    temperature = read_soc_temperature()
    with state_lock:
        if values:
            system_cpu_mean_samples.append(sum(values) / len(values))
            system_cpu_peak_samples.append(max(values))
        if temperature is not None:
            system_temp_samples.append(temperature)
        history = list(system_cpu_mean_samples)
        cpu_summary = summarize(history)
        temp_summary = summarize(system_temp_samples)
        peaks = list(system_cpu_peak_samples)
        system_cpu_temp_snapshot = {
            "cpu_history": history,
            "cpu_current": cpu_summary["current"] if cpu_summary else None,
            "cpu_average": cpu_summary["average"] if cpu_summary else None,
            "cpu_peak": max(peaks) if peaks else None,
            "temp_current": temp_summary["current"] if temp_summary else None,
            "temp_average": temp_summary["average"] if temp_summary else None,
            "temp_peak": temp_summary["peak"] if temp_summary else None,
            "sample_count": len(history),
        }

RESOURCE_SCREENS = ("MENU_SYS_RAM_ROM", "MENU_SYS_CPU_TEMP")

# メニュー表示用のIP/コーデックはバックグラウンドで取得してキャッシュする。
# state_lock保持中にbusctlやIP確認を実行すると全スレッドが止まるため。
menu_ip_address = "取得中..."
menu_bt_codec = "取得待ち"

def sample_menu_info_values():
    global menu_ip_address, menu_bt_codec
    try:
        menu_ip_address = get_ip_address()
    except Exception as error:
        print(f"IP address check error: {error}")
    try:
        menu_bt_codec = get_bt_codec_info()
    except Exception as error:
        print(f"BT codec check error: {error}")

def system_resource_monitor_thread():
    """画面を開いていなくても統計を貯め続ける。CPUは1秒、RAM/温度は5秒ごと。"""
    tick = 0
    while True:
        try:
            sample_cpu_tick()
            if tick % 5 == 0:
                sample_menu_info_values()
                sample_system_resource_usage()
                commit_cpu_temp_sample()
                with state_lock:
                    resource_screen_active = current_screen in RESOURCE_SCREENS
                if resource_screen_active:
                    # 表示内容が前回と同じなら display_worker 側で送信が省かれる。
                    request_display_update(is_full_refresh=False)
        except Exception as error:
            print(f"System monitor error: {error}")
        tick += 1
        time.sleep(1)

def render_system_resource_screen(image, draw, stats):
    width = image.width
    header = "システム設定 > RAM/ROM使用量"
    draw.text((8, 2), truncate_by_width(header, font_main, width - 16), font=font_main, fill=0)
    draw.line([(0, 18), (width - 1, 18)], fill=0)

    if not stats or "error" in stats:
        message = "使用量を計測中..." if not stats else "使用量を取得できません"
        draw.text((8, 34), message, font=font_main, fill=0)
        return

    draw.text((8, 22), "ストレージ使用量", font=font_small, fill=0)
    storage_text = (
        f"{format_size_bytes(stats['storage_used'])} / "
        f"{format_size_bytes(stats['storage_total'])}  "
        f"{stats['storage_percent']:.0f}%"
    )
    draw.text((8, 36), truncate_by_width(storage_text, font_small, width - 16), font=font_small, fill=0)
    draw.rectangle([8, 51, width - 9, 63], outline=0, fill=255, width=1)
    storage_fill = int((width - 21) * min(100.0, stats["storage_percent"]) / 100.0)
    if storage_fill > 0:
        draw.rectangle([10, 53, 9 + storage_fill, 61], fill=0)

    count = stats["sample_count"]
    period_label = "直近5分平均" if count >= 60 else f"RAM平均 (測定中 {count}/60)"
    draw.text((8, 69), period_label, font=font_small, fill=0)
    ram_text = (
        f"{format_ram_bytes(stats['ram_average_used'], stats['ram_average_total'])} / "
        f"{format_ram_bytes(stats['ram_average_total'], stats['ram_average_total'])}  "
        f"{stats['ram_average_percent']:.0f}%"
    )
    draw.text((8, 82), truncate_by_width(ram_text, font_small, width - 16), font=font_small, fill=0)
    draw.rectangle([8, 97, width - 9, 109], outline=0, fill=255, width=1)
    ram_fill = int((width - 21) * min(100.0, stats["ram_average_percent"]) / 100.0)
    if ram_fill > 0:
        draw.rectangle([10, 99, 9 + ram_fill, 107], fill=0)

def render_system_cpu_temp_screen(image, draw, stats):
    width = image.width
    header = "システム設定 > CPU使用率/温度"
    draw.text((8, 2), truncate_by_width(header, font_main, width - 16), font=font_main, fill=0)
    draw.line([(0, 18), (width - 1, 18)], fill=0)

    if not stats or not stats.get("cpu_history"):
        draw.text((8, 34), "使用率を計測中...", font=font_main, fill=0)
        return

    def pct(value):
        return "--" if value is None else f"{value:.0f}%"

    def deg(value):
        return "--" if value is None else f"{value:.1f}°C"

    cpu_line = f"CPU {pct(stats['cpu_current'])}  平均{pct(stats['cpu_average'])}  最大{pct(stats['cpu_peak'])}"
    draw.text((8, 21), truncate_by_width(cpu_line, font_small, width - 16), font=font_small, fill=0)
    if stats.get("temp_current") is None:
        temp_line = "温度 取得できません"
    else:
        temp_line = (
            f"温度 {deg(stats['temp_current'])}  平均{deg(stats['temp_average'])}  "
            f"最大{deg(stats['temp_peak'])}"
        )
    draw.text((8, 35), truncate_by_width(temp_line, font_small, width - 16), font=font_small, fill=0)

    # グラフ: 縦0〜100%固定、新しい値が右端。中央の点線が50%。
    box = (8, 50, width - 9, 105)
    draw.rectangle(list(box), outline=0, fill=255, width=1)
    inner = (box[0] + 2, box[1] + 2, box[2] - 2, box[3] - 2)
    middle = (inner[1] + inner[3]) // 2
    for x in range(inner[0], inner[2], 6):
        draw.point((x, middle), fill=0)
    points = graph_points(stats["cpu_history"], inner, slots=60)
    if len(points) >= 2:
        draw.line(points, fill=0, width=1)
    elif points:
        x, y = points[0]
        draw.rectangle([x - 1, y - 1, x + 1, y + 1], fill=0)

    count = stats["sample_count"]
    caption = "直近5分のCPU使用率 (5秒平均)" if count >= 60 else f"CPU使用率 (測定中 {count}/60)"
    draw.text((8, 107), truncate_by_width(caption, font_small, width - 16), font=font_small, fill=0)

def get_current_wifi_ssid():
    try:
        res = subprocess.check_output(
            ["iwgetid", "-r"], text=True, errors="ignore", timeout=3
        )
        ssid = res.strip()
        if ssid:
            return ssid
    except Exception:
        pass
    return None

def is_bluealsa_active():
    """BlueALSAサービスが起動中か確認する。"""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "bluealsa"],
            check=False,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def get_bluealsa_pcm_list(mac=None):
    """ALSAに公開されたBlueALSA PCM一覧を返す。"""
    commands = [["aplay", "-L"], ["bluealsa-aplay", "-L"]]
    normalized_mac = mac.upper() if mac else None
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                errors="ignore",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0 or not result.stdout:
            continue
        if normalized_mac is None or pcm_list_contains_device(result.stdout, normalized_mac):
            return result.stdout
    return ""


def wait_for_bluealsa_pcm(mac, timeout=10.0):
    """対象Bluetooth機器のA2DP PCMが公開されるまで待つ。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pcm_list_contains_device(get_bluealsa_pcm_list(mac), mac):
            return True
        time.sleep(0.5)
    return False


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


def start_bt_confirmation_agent(mac):
    """Register a temporary Agent that confirms only the selected device."""
    if not HAS_DBUS_AGENT:
        print("BT自動承認を利用できません: python3-dbus/python3-giが未導入です")
        return None

    target_mac = mac.upper()
    agent_path = "/org/pizero/dap/agent"
    try:
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        loop = GLib.MainLoop()

        class ConfirmationAgent(dbus.service.Object):
            def _is_target(self, device):
                address = str(device.rsplit("/dev_", 1)[-1]).replace("_", ":").upper()
                return address == target_mac

            def _reject_unmatched(self, device):
                if not self._is_target(device):
                    raise dbus.exceptions.DBusException(
                        "org.bluez.Error.Rejected",
                        "Device is not the selected pairing target",
                    )

            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Release(self):
                pass

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
            def RequestPinCode(self, device):
                self._reject_unmatched(device)
                raise dbus.exceptions.DBusException(
                    "org.bluez.Error.Rejected",
                    "PIN entry is not supported",
                )

            @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
            def DisplayPinCode(self, device, pincode):
                self._reject_unmatched(device)

            @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
            def DisplayPasskey(self, device, passkey, entered):
                self._reject_unmatched(device)

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
            def RequestPasskey(self, device):
                self._reject_unmatched(device)
                raise dbus.exceptions.DBusException(
                    "org.bluez.Error.Rejected",
                    "Passkey entry is not supported",
                )

            @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
            def RequestConfirmation(self, device, passkey):
                self._reject_unmatched(device)
                print(f"BTパスキーを自動承認: {target_mac} ({int(passkey):06d})")

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
            def RequestAuthorization(self, device):
                self._reject_unmatched(device)

            @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
            def AuthorizeService(self, device, uuid):
                self._reject_unmatched(device)

            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Cancel(self):
                pass

        agent = ConfirmationAgent(bus, agent_path)
        manager = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.AgentManager1",
        )
        manager.RegisterAgent(agent_path, "KeyboardDisplay")
        manager.RequestDefaultAgent(agent_path)
        thread = threading.Thread(target=loop.run, daemon=True)
        thread.start()

        def cleanup():
            try:
                manager.UnregisterAgent(agent_path)
            except dbus.exceptions.DBusException as error:
                print(f"BT Agent解除警告: {error}")
            loop.quit()
            agent.remove_from_connection()

        return cleanup
    except (OSError, dbus.exceptions.DBusException) as error:
        print(f"BT自動承認Agentの登録失敗: {error}")
        return None


def format_bt_menu_name(name, mac, include_suffix=False):
    base = normalize_bt_name(name) or mac
    if include_suffix and is_valid_bt_mac(mac):
        return f"{base} [{mac[-5:]}]"
    return base

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
        ship_img, mask = get_cropped_ship(SHIPS_PNG_PATH, idx, target_h)
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
    else:
        draw.rectangle([box_x, box_y, box_x + box_w, box_y + 12], fill=0)
        draw.text((box_x + 6, box_y + 1), "[ RTC ]", font=font_small, fill=255)

    # どちらの状態でも時刻を常時表示する
    cur_time = time.strftime("%H:%M")
    draw.text((box_x + 12, box_y + 13), cur_time, font=font_clock, fill=0)

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
# 部分更新を連続で行える回数。超えると強制full refresh(残像の蓄積をリセット)。
# 画面切替時は常にfull refreshが走るため蓄積は同一画面内に限定される。
PARTIAL_REFRESH_LIMIT = 20
github_update_in_progress = False

def request_display_update(is_full_refresh=False):
    global pending_full_refresh
    with display_queue.mutex:
        if is_full_refresh:
            pending_full_refresh = True
        display_queue.queue.clear()
    display_queue.put(True)

def _set_github_update_status(message):
    global status_message
    with state_lock:
        status_message = message
    request_display_update(is_full_refresh=False)


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
            _set_github_update_status("GitHubは最新です")
            return

        already_contains_main = run_git([
            "merge-base", "--is-ancestor", target.stdout.strip(), "HEAD"
        ], timeout=15)
        if already_contains_main.returncode == 0:
            _set_github_update_status("GitHubは最新です")
            return

        # stashの前にff-only更新が可能か先に判定し、失敗時はstashを戻す。
        # web_admin_server.pyの更新処理と同じ挙動に統一。
        ff_possible = run_git([
            "merge-base", "--is-ancestor", "HEAD", target.stdout.strip()
        ], timeout=15)
        if ff_possible.returncode != 0:
            raise RuntimeError("履歴が分岐しているため安全に更新できませんでした")

        local_changes = run_git(["status", "--porcelain", "--untracked-files=all"], timeout=15)
        if local_changes.returncode != 0:
            raise RuntimeError("ローカル変更を確認できません")
        stashed = False
        if local_changes.stdout.strip():
            stashed_result = run_git([
                "stash", "push", "--include-untracked",
                "-m", "DAP local changes before GitHub update",
            ])
            if stashed_result.returncode != 0:
                detail = (stashed_result.stderr or stashed_result.stdout or "ローカル変更を退避できませんでした").strip()[-180:]
                raise RuntimeError(detail)
            stashed = True

        merged = run_git(["merge", "--ff-only", "--no-edit", target.stdout.strip()])
        if merged.returncode != 0:
            if stashed:
                run_git(["stash", "pop"], timeout=30)
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

        if stashed:
            _set_github_update_status("更新しました(ローカル変更はstashに退避)。再起動します")
        else:
            _set_github_update_status("更新しました。再起動します")
        time.sleep(1.5)
        # Restart this service process in place. Calling systemctl restart from
        # inside the unit can kill this worker/command during the stop phase,
        # and sudo may prompt for a password with no terminal available.
        os.execv(sys.executable, [sys.executable, os.path.join(BASE_DIR, "dap_daemon.py")])
    except Exception as e:
        print(f"GitHub update error: {e}")
        _set_github_update_status(f"更新失敗: {str(e)[:42]}")
    finally:
        github_update_in_progress = False


def start_github_update():
    global github_update_in_progress, status_message
    if github_update_in_progress:
        return
    github_update_in_progress = True
    status_message = "更新データを取得中..."
    # Full refresh calls epd.init(), which hardware-resets the panel before waiting for BUSY.
    request_display_update(is_full_refresh=True)
    threading.Thread(target=_github_update_worker, daemon=True).start()


def display_worker():
    global pending_full_refresh
    current_epd_mode = None
    partial_refresh_count = 0
    previous_screen = None
    last_pushed_frame = None   # 直近にパネルへ送ったバッファ。同じ内容なら再送しない。

    while True:
        try:
            _ = display_queue.get()
            
            with display_queue.mutex:
                is_full = pending_full_refresh
                pending_full_refresh = False

            if partial_refresh_count >= PARTIAL_REFRESH_LIMIT:
                is_full = True
                partial_refresh_count = 0

            with state_lock:
                scr = current_screen
                track_idx = current_track_idx
                pl = list(playlist)
                cur_idx = cursor_idx
                sc_off = scroll_offset
                m_items = list(menu_items)
                m_bold = list(menu_item_bold)
                sel_alb = selected_album
                playing = is_playing
                cur_vol = volume
                output_mode = audio_output_mode
                stat_msg = status_message
                resource_stats = (
                    dict(system_resource_snapshot)
                    if system_resource_snapshot is not None else None
                )
                cpu_temp_stats = (
                    dict(system_cpu_temp_snapshot)
                    if system_cpu_temp_snapshot is not None else None
                )

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

            elif scr == "MENU_SYS_RAM_ROM":
                render_system_resource_screen(image, draw, resource_stats)

            elif scr == "MENU_SYS_CPU_TEMP":
                render_system_cpu_temp_screen(image, draw, cpu_temp_stats)

            elif scr == "PLAY":
                # スリープタイマー表示生成(60分超は時間形式)
                timer_str = ""
                if sleep_timer_end_time > 0:
                    rem_sec = max(0, int(sleep_timer_end_time - time.time()))
                    if rem_sec >= 3600:
                        timer_str = f" T:{rem_sec // 3600}H{(rem_sec % 3600) // 60:02d}"
                    else:
                        timer_str = f" T:{rem_sec // 60}m"

                state_str = "> PLAY" if playing else "|| PAUSE"
                draw.text((8, 2), state_str, font=font_main, fill=0)

                # 中間要素(アイコン/タイマー)を state と BAT/VOL スロットの間に
                # 等間隔で配置する。BAT/VOLは左端186固定で位置が動かない。
                # 要素が少ないときは余白が各ギャップに均等配分されて広がる。
                middle = []
                if is_shuffle and "shuffle" in playback_mode_icons:
                    middle.append(playback_mode_icons["shuffle"])
                if repeat_mode == 1 and "repeat_one" in playback_mode_icons:
                    middle.append(playback_mode_icons["repeat_one"])
                elif repeat_mode == 2 and "repeat_all" in playback_mode_icons:
                    middle.append(playback_mode_icons["repeat_all"])
                if timer_str:
                    middle.append(timer_str.strip())
                if output_mode == "USBOTG":
                    output_icon_name = "usb"
                elif output_mode == "PWM":
                    output_icon_name = "pwm"
                else:
                    codec = get_bt_codec_info().lower()
                    output_icon_name = codec if codec in {"aac", "sbc"} else "other"
                if output_icon_name in playback_mode_icons:
                    middle.append(playback_mode_icons[output_icon_name])

                slot_str = ""
                if time.time() - volume_changed_at < 5.0:
                    slot_str = f"VOL: {int(cur_vol * 100)}%"
                else:
                    battery_pct, _ = get_battery_info()
                    slot_str = "" if battery_pct == "--%" else f"BAT: {battery_pct}"

                state_end = 8 + font_main.getlength(state_str)
                widths = [
                    min(50.0, font_main.getlength(m)) if isinstance(m, str) else m.width
                    for m in middle
                ]
                min_gap = 5.0
                if middle:
                    span = 186 - 5 - state_end - sum(widths)
                    gap = span / (len(middle) + 1)
                    if gap < min_gap:
                        gap = min_gap  # 空きが足りないときは詰めて配置
                    x = state_end + gap
                    for m, w in zip(middle, widths):
                        if x + w > 180:  # BAT/VOLスロット(186-)に食い込む要素は省略
                            break
                        if isinstance(m, str):
                            draw.text((x, 2), truncate_by_width(m, font_main, 50), font=font_main, fill=0)
                        else:
                            image.paste(m, (int(x), 4))
                        x += w + gap
                if slot_str:
                    # 左端固定(VOL/BATで文字幅が変わっても位置が動かない)。
                    # 最長の"VOL: 100%"≈62pxでx=248に収まる。
                    draw.text((186, 2), slot_str, font=font_main, fill=0)
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
                elif scr == "MENU_OUTPUT": header_text = "接続設定 > 出力先設定"
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
                    # お気に入り済みの曲行は太字(1pxずらして二度描き)で識別する
                    if actual_idx < len(m_bold) and m_bold[actual_idx]:
                        draw.text((9, y), disp_item, font=font_main, fill=0)

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

            # 部分更新で、画面種別も前回から変わらず、描画結果が前回と完全に同じなら
            # パネルへ送らない(ちらつき・残像・消費電力・SPI負荷を避ける)。
            # 全画面更新(is_full)や画面切替、初回、エラー後は必ず送る。
            if (
                not is_full
                and current_epd_mode == "PARTIAL"
                and scr == previous_screen
                and last_pushed_frame is not None
                and bytes(buf) == last_pushed_frame
            ):
                display_queue.task_done()
                continue

            with epd_lock:
                if shutdown_display_done:
                    # シャットダウン演出済み。パネルをこれ以上触らず描画ループを終える。
                    display_queue.task_done()
                    break

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

            last_pushed_frame = bytes(buf)
            previous_screen = scr
            display_queue.task_done()
        except Exception as e:
            print(f"Display render error: {e}")
            current_epd_mode = None
            last_pushed_frame = None
            time.sleep(0.1)

threading.Thread(target=display_worker, daemon=True).start()

# --- 定期更新スレッド ---
def cat_clock_timer_thread():
    last_min = -1
    while True:
        time.sleep(1)  # 1秒おきに軽く時刻をチェック
        with state_lock:
            if current_screen == "CAT_CLOCK":
                current_min = time.localtime().tm_min
                # 分が変わったときだけ画面更新をリクエスト
                if current_min != last_min:
                    last_min = current_min
                    request_display_update(is_full_refresh=False)

threading.Thread(target=cat_clock_timer_thread, daemon=True).start()

# --- 6.5 音声出力操作の単一実行点(アクター) ---
# .asoundrc / mixer / 出力モード / BT接続の遷移は audio_worker_thread だけが
# 実行する。UI・watchdogはコマンドを投入するだけで、切替処理の並行実行を
# 構造的に防ぐ(フラグ競合・設定ファイル同時書き込みの温床をなくす)。
audio_command_queue = queue.Queue()
_audio_worker_context = threading.local()

def _in_audio_worker():
    return getattr(_audio_worker_context, "active", False)

def submit_audio_command(command, *args):
    audio_command_queue.put((command, args))

def audio_worker_thread():
    _audio_worker_context.active = True
    while True:
        command = None
        try:
            command, args = audio_command_queue.get()
            if command == "set_output":
                mode = args[0] if args else None
                if mode not in ("PWM", "USBOTG", "BT"):
                    print(f"Audio worker: unknown output mode {mode!r}")
                    continue
                mac = args[1] if len(args) > 1 else None
                set_audio_output(mode, mac=mac)
            elif command == "connect_bt":
                mac = args[0] if args else ""
                name = args[1] if len(args) > 1 else "Unknown"
                _run_bt_connection_job(mac, name)
            elif command == "suspend_lost":
                suspend_audio_for_lost_output(args[0] if args else "音声デバイス切断")
            elif command == "recover_usb":
                recover_usb_otg_audio()
            elif command == "recover_bt":
                recover_bt_audio(args[0] if args else "")
            else:
                print(f"Audio worker: unknown command {command!r}")
        except Exception as error:
            print(f"Audio worker error ({command}): {error}")

# --- 7. Bluetooth 接続 & オーディオ出力切替 ---
def start_bt_connection(mac, name="Unknown", state_lock_held=False):
    """BT接続ジョブを音声ワーカーへ依頼する。重複起動はしない。

    ワーカー内部から呼ばれた場合(出力先BTへの切替など)は
    同一ワーカー上で直列に実行を続ける。
    """
    if _in_audio_worker():
        _run_bt_connection_job(mac, name)
        return
    global bt_connect_in_progress
    if state_lock_held:
        if bt_connect_in_progress:
            return
        bt_connect_in_progress = True
    else:
        with state_lock:
            if bt_connect_in_progress:
                return
            bt_connect_in_progress = True
    submit_audio_command("connect_bt", mac, name)

def _run_bt_connection_job(mac, name="Unknown"):
    """ワーカー上でBT接続を実行する。実行中はbt_connect_in_progressを立てる。"""
    global bt_connect_in_progress
    bt_connect_in_progress = True
    try:
        connect_bt_device(mac, name)
    finally:
        with state_lock:
            bt_connect_in_progress = False

def connect_bt_device(mac, name="Unknown"):
    global status_message, connected_bt_mac, audio_output_mode
    global is_playing, audio_transition_in_progress
    global audio_output_lost, audio_lost_action

    if not is_valid_bt_mac(mac):
        print(f"BT接続を中止: 不正なデバイスアドレスです ({mac!r})")
        status_message = "BTアドレス不正"
        request_display_update(is_full_refresh=False)
        return
    mac = mac.upper()

    _, initial_bt_on = get_rfkill_status()

    previous_audio_mode = audio_output_mode
    previous_connected_mac = connected_bt_mac
    was_playing = is_playing
    audio_transition_in_progress = True
    is_playing = False

    def restore_pwm_after_failure():
        global audio_output_mode
        # 音声初期化が失敗しても、接続確認と追加診断ができるよう
        # BlueALSAとBluetooth接続は維持したまま再生先だけPWMへ戻す。
        subprocess.run(["sudo", "rfkill", "unblock" if initial_bt_on else "block", "bluetooth"], check=False)
        audio_output_mode = "PWM"
        safe_init_audio()

    connected_bt_mac = mac
    # Keep the full device name; the display renderer applies pixel-aware
    # truncation with an ellipsis when the status box is too narrow.
    status_message = f"接続中: {name}"
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
    bluetooth_ready = False
    for _ in range(10):
        service_state = subprocess.run(
            ["systemctl", "is-active", "--quiet", "bluetooth"],
            check=False,
            timeout=5,
        )
        if service_state.returncode == 0:
            bluetooth_ready = True
            break
        time.sleep(0.3)
    if not bluetooth_ready:
        status_message = "Bluetooth起動失敗"
        connected_bt_mac = previous_connected_mac
        if previous_audio_mode == "PWM":
            restore_pwm_after_failure()
        audio_transition_in_progress = False
        if was_playing:
            play_current_track(full_refresh=False)
        request_display_update(is_full_refresh=True)
        return

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

    def pair_and_connect():
        """Pair the selected device, using the MAC-scoped confirmation agent."""
        agent_cleanup = start_bt_confirmation_agent(mac)
        try:
            pairing_commands = ["power on"]
            if agent_cleanup is None:
                pairing_commands.extend(["agent on", "agent KeyboardDisplay", "default-agent"])
            pairing_commands.extend([f"pair {mac}", f"trust {mac}", f"connect {mac}"])
            pairing_session = run_btctl_session(pairing_commands, timeout=60)
            session_output = ""
            if pairing_session is not None:
                session_output = "\n".join(
                    part for part in (pairing_session.stdout, pairing_session.stderr) if part
                )
            if agent_cleanup is None and not bt_prop_is_yes(get_bt_device_properties(mac), "paired"):
                fallback_session = run_btctl_session(
                    [
                        "power on", "agent on", "agent NoInputNoOutput",
                        "default-agent", f"pair {mac}", f"trust {mac}", f"connect {mac}",
                    ],
                    timeout=60,
                )
                if fallback_session is not None:
                    session_output = "\n".join(
                        part for part in (session_output, fallback_session.stdout, fallback_session.stderr)
                        if part
                    )
            return session_output
        finally:
            if agent_cleanup is not None:
                agent_cleanup()

    run_btctl("power", "on")
    run_btctl("agent", "on")

    properties = get_bt_device_properties(mac)
    requires_user_confirmation = False
    if not bt_prop_is_yes(properties, "paired"):
        # 対話入力不可UIのため KeyboardDisplay を優先し、必要なら NoInputNoOutput にフォールバック。
        session_output = pair_and_connect()
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

    connect_result = None
    if paired and not bt_prop_is_yes(get_bt_device_properties(mac), "connected"):
        connect_result = run_btctl("connect", mac, timeout=25)
        connect_output = ""
        if connect_result is not None:
            connect_output = "\n".join(
                part for part in (connect_result.stdout, connect_result.stderr) if part
            ).lower()
        if "br-connection-key-missing" in connect_output:
            print(f"Bluetooth鍵不一致を検出、再ペアリングします: {mac}")
            status_message = "鍵を再生成中..."
            request_display_update(is_full_refresh=False)
            run_btctl("remove", mac, timeout=15)
            time.sleep(0.5)
            session_output = pair_and_connect()
            properties = get_bt_device_properties(mac)
            paired = bt_prop_is_yes(properties, "paired")

    deadline = time.monotonic() + 20
    next_connect_retry = time.monotonic() + 5
    while paired and time.monotonic() < deadline and not is_bt_connected(mac):
        if time.monotonic() >= next_connect_retry:
            run_btctl("connect", mac, timeout=25)
            next_connect_retry = time.monotonic() + 5
        time.sleep(0.5)

    connected = paired and is_bt_connected(mac)
    if connected:
        if not is_bluealsa_active():
            subprocess.run(
                ["sudo", "systemctl", "start", "bluealsa"],
                check=False,
                timeout=10,
            )

        if not is_bluealsa_active():
            print("BlueALSAサービスを起動できません")
            status_message = "BlueALSA起動失敗"
            connected_bt_mac = previous_connected_mac
            if previous_audio_mode == "PWM":
                restore_pwm_after_failure()
        else:
            status_message = "音声デバイス待機中..."
            request_display_update(is_full_refresh=False)

            if not wait_for_bluealsa_pcm(mac, timeout=10.0):
                print(f"BlueALSA PCMが見つかりません: {mac}")
                status_message = "音声デバイス未検出"
                connected_bt_mac = previous_connected_mac
                if previous_audio_mode == "PWM":
                    restore_pwm_after_failure()
            else:
                config_content = build_bluealsa_asoundrc(mac)
                old_config = None
                config_existed = os.path.exists(asoundrc_path)
                try:
                    if config_existed:
                        with open(asoundrc_path, "r", encoding="utf-8") as file:
                            old_config = file.read()
                    write_text_atomically(asoundrc_path, config_content)
                    if asound:
                        try:
                            asound.snd_config_update_free_global()
                        except Exception as error:
                            print(f"ALSA設定再読み込み警告: {error}")
                    audio_output_mode = "BT"
                    connected_bt_mac = mac
                    safe_init_audio()
                    if not pygame.mixer.get_init():
                        raise RuntimeError("Pygame mixerの初期化に失敗しました")
                    audio_output_lost = False
                    audio_lost_action = None
                    # 旧実装ではここで amixer -D equal sset Master を実行していたが、
                    # equalプラグインは帯域コントロールのみでMasterが存在せず
                    # 常に失敗していただけ(音量はpygameのソフト音量で管理)のため削除。
                    apply_eq_settings()
                    audio_transition_in_progress = False
                    if was_playing:
                        play_current_track(full_refresh=False)
                    status_message = "接続成功"
                except Exception as error:
                    print(f"BT ALSA設定エラー ({type(error).__name__}): {error}")
                    audio_output_mode = previous_audio_mode
                    connected_bt_mac = previous_connected_mac
                    status_message = "音声設定失敗"
                    try:
                        if config_existed and old_config is not None:
                            write_text_atomically(asoundrc_path, old_config)
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
            restore_pwm_after_failure()

    audio_transition_in_progress = False
    if was_playing and not is_playing:
        play_current_track(full_refresh=False)
    request_display_update(is_full_refresh=True)
    update_menu_items()
    request_display_update(is_full_refresh=False)

def set_audio_output(mode, mac=None, state_lock_held=False):
    """出力先を切り替える。UI経由では音声ワーカーへ依頼し、実行は直列化する。"""
    global audio_output_mode, status_message, connected_bt_mac
    global is_playing, audio_transition_in_progress, USB_OTG_ALSA_DEVICE
    global audio_output_lost, audio_lost_action

    if not _in_audio_worker():
        submit_audio_command("set_output", mode, mac)
        return

    status_message = f"切替中: {mode}"
    request_display_update(is_full_refresh=False)

    if mode == "BT":
        target_mac = mac or connected_bt_mac
        if target_mac:
            start_bt_connection(target_mac, "Bluetooth")
        else:
            status_message = "MAC未設定"
            request_display_update(is_full_refresh=False)
        return

    # PWM/USBOTGの切替は、旧実装ではUIスレッドがstate_lockを保持したまま
    # 実行していた。ワーカー実行でも同じ排他を保ち、再生系ボタン操作
    # (play_current_trackのmixer再初期化)と重ならないようにする。
    # BT接続はbluetoothctl待ちが長いためロックを保持しない。
    with state_lock:
        if mode not in ("PWM", "USBOTG"):
            return
        was_playing = is_playing
        audio_transition_in_progress = True
        is_playing = False
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.quit()
        except Exception as error:
            print(f"Audio shutdown warning: {error}")

        if asound:
            try:
                asound.snd_config_update_free_global()
            except Exception:
                pass

        # PWMへ戻すときはBlueALSA音声だけ停止し、Bluetooth無線状態は維持する。
        subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)
        time.sleep(0.5)

        pending_lost_action = audio_lost_action if audio_output_lost else None
        audio_output_lost = False
        audio_lost_action = None
        audio_output_mode = mode
        if mode == "USBOTG":
            audio_ok = init_usb_otg_audio(attempts=4, delay=1.0)
        else:
            try:
                write_text_atomically(asoundrc_path, build_pwm_asoundrc())
            except Exception as e:
                print(f"asoundrc write error: {e}")
            audio_ok = safe_init_audio()

        if mode == "USBOTG" and not audio_ok:
            # DAC未接続・認識待ちでもUSBモードのまま待機し、見つかり次第自動で開始する。
            audio_output_lost = True
            audio_lost_action = "restart" if was_playing else None
            status_message = "USB音声初期化失敗" if usb_audio_present() else "USB DAC待機中"
            audio_transition_in_progress = False
        else:
            status_message = f"{mode}モード設定"
            apply_eq_settings()
            audio_transition_in_progress = False
            if was_playing:
                play_current_track(full_refresh=False)
            elif pending_lost_action:
                # 切断中に出力を切り替えた場合は、止まっていた位置から続きを再生する。
                audio_lost_action = pending_lost_action
                restore_playback_after_audio_return()
        request_display_update(is_full_refresh=True)
        update_menu_items()
        request_display_update(is_full_refresh=False)

# --- 8. Bluetooth / Wi-Fi / AP 関連 ---
def get_rfkill_status():
    wifi_enabled, bt_enabled = True, True
    try:
        res = subprocess.check_output(
            ["sudo", "rfkill", "list"], text=True, timeout=3
        )
        current_type = None
        for line in res.splitlines():
            line_lower = line.lower()
            if "wlan" in line_lower or "wireless" in line_lower: current_type = "wifi"
            elif "bluetooth" in line_lower: current_type = "bt"

            if "soft blocked: yes" in line_lower:
                if current_type == "wifi": wifi_enabled = False
                elif current_type == "bt": bt_enabled = False
    except (OSError, subprocess.SubprocessError):
        pass
    return wifi_enabled, bt_enabled

def toggle_rfkill(dev_type):
    """Wi-Fi / Bluetooth の無線状態を互いに独立して切り替える。"""
    global status_message
    wifi_on, bt_on = get_rfkill_status()
    try:
        if dev_type == "wifi":
            action = "block" if wifi_on else "unblock"
            result = subprocess.run(
                ["sudo", "rfkill", action, "wlan"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            new_wifi_on, _ = get_rfkill_status()
            success = result.returncode == 0 and new_wifi_on != wifi_on
            status_message = ("Wi-Fi OFF" if not new_wifi_on else "Wi-Fi ON") if success else "Wi-Fi切替失敗"
        else:
            if bt_on:
                audio_was_bt = audio_output_mode == "BT"
                if audio_was_bt:
                    set_audio_output("PWM")
                else:
                    subprocess.run(
                        ["sudo", "systemctl", "stop", "bluealsa"],
                        capture_output=True, text=True, check=False, timeout=10,
                    )
                result = subprocess.run(
                    ["sudo", "rfkill", "block", "bluetooth"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                _, new_bt_on = get_rfkill_status()
                success = result.returncode == 0 and not new_bt_on
                status_message = "" if success else "BT無効化失敗"
            else:
                unblock = subprocess.run(
                    ["sudo", "rfkill", "unblock", "bluetooth"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                start = subprocess.run(
                    ["sudo", "systemctl", "start", "bluetooth"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                service = subprocess.run(
                    ["sudo", "systemctl", "is-active", "bluetooth"],
                    capture_output=True, text=True, check=False, timeout=5,
                )
                _, new_bt_on = get_rfkill_status()
                service_active = service.returncode == 0 and service.stdout.strip() == "active"
                success = unblock.returncode == 0 and start.returncode == 0 and service_active and new_bt_on
                if success:
                    status_message = ""
                else:
                    details = (start.stderr or service.stderr or unblock.stderr or "").strip()
                    print(f"Bluetooth有効化失敗: rfkill={new_bt_on}, service={service.stdout.strip()}, {details}")
                    status_message = "BT起動失敗" if not service_active else "BT無線切替失敗"
        request_display_update(is_full_refresh=False)
        time.sleep(0.3)
    except Exception as e:
        status_message = f"{dev_type.upper()}切替エラー"
        print(f"[toggle_rfkill] Error: {e}")
        request_display_update(is_full_refresh=False)
ap_previous_wifi_on = None
ap_toggle_in_progress = False


def get_ap_status():
    try:
        # AP is launched with hostapd -B rather than through systemd.
        res = subprocess.run(
            ["pgrep", "-x", "hostapd"], capture_output=True, text=True, timeout=3
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

def toggle_ap_mode(state_lock_held=False):
    global ap_toggle_in_progress
    if state_lock_held:
        if ap_toggle_in_progress:
            return
        ap_toggle_in_progress = True
    else:
        with state_lock:
            if ap_toggle_in_progress:
                return
            ap_toggle_in_progress = True

    def _do_toggle():
        global status_message, ap_previous_wifi_on, ap_toggle_in_progress
        is_active = get_ap_status()

        def run_required(command, timeout=10, allow_returncodes=()):
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode != 0 and result.returncode not in allow_returncodes:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(
                    f"{' '.join(command)} failed ({result.returncode})"
                    + (f": {detail}" if detail else "")
                )
            return result

        try:
            if is_active:
                status_message = "AP OFF中..."
                request_display_update(is_full_refresh=False)

                run_required(["sudo", "pkill", "-TERM", "hostapd"], allow_returncodes=(1,))
                run_required(["sudo", "pkill", "-TERM", "dnsmasq"], allow_returncodes=(1,))
                run_required(["sudo", "ip", "link", "set", "wlan0", "down"])
                run_required(["sudo", "ip", "addr", "flush", "dev", "wlan0"])
                run_required(["sudo", "ip", "link", "set", "wlan0", "up"])
                run_required(["sudo", "systemctl", "restart", "NetworkManager"], timeout=30)
                nm_state = subprocess.run(
                    ["systemctl", "is-active", "--quiet", "NetworkManager"], timeout=5
                )
                if nm_state.returncode != 0:
                    raise RuntimeError("NetworkManagerが起動していません")
                if ap_previous_wifi_on is False:
                    run_required(["sudo", "rfkill", "block", "wlan"])
                ap_previous_wifi_on = None

            else:
                status_message = "AP ON中..."
                request_display_update(is_full_refresh=False)

                ap_previous_wifi_on, _ = get_rfkill_status()

                run_required(["sudo", "systemctl", "stop", "NetworkManager"], timeout=30)
                run_required(["sudo", "pkill", "-TERM", "wpa_supplicant"], allow_returncodes=(1,))
                run_required(["sudo", "pkill", "-TERM", "hostapd"], allow_returncodes=(1,))
                run_required(["sudo", "pkill", "-TERM", "dnsmasq"], allow_returncodes=(1,))

                run_required(["sudo", "rfkill", "unblock", "wlan"])
                run_required(["sudo", "ip", "link", "set", "wlan0", "down"])
                run_required(["sudo", "ip", "addr", "flush", "dev", "wlan0"])
                run_required(["sudo", "ip", "link", "set", "wlan0", "up"])
                run_required(["sudo", "ip", "addr", "add", "192.168.4.1/24", "dev", "wlan0"])
                time.sleep(0.5)

                dnsmasq_conf = """interface=wlan0
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,12h
dhcp-option=option:router,192.168.4.1
dhcp-option=option:dns-server,192.168.4.1
bind-interfaces
"""
                with open("/tmp/dnsmasq_ap.conf", "w", encoding="utf-8") as f:
                    f.write(dnsmasq_conf)

                ap_config_path = os.path.join(BASE_DIR, "ap_config.json")
                try:
                    with open(ap_config_path, "r", encoding="utf-8") as f:
                        ap_config = json.load(f)
                except (OSError, json.JSONDecodeError) as error:
                    raise RuntimeError(f"AP設定を読めません: {error}")
                ssid = str(ap_config.get("ssid", "PiZero-DAP")).strip()
                password = str(ap_config.get("password", "dap-connect-1234"))
                country = str(ap_config.get("country", "JP")).strip().upper()
                channel = int(ap_config.get("channel", 6))
                if not 1 <= len(ssid.encode("utf-8")) <= 32 or "\n" in ssid or "\r" in ssid:
                    raise ValueError("AP SSIDはUTF-8で1〜32バイトにしてください")
                if not 8 <= len(password) <= 63 or any(ord(char) < 32 or ord(char) > 126 for char in password):
                    raise ValueError("APパスワードは印字可能なASCIIで8〜63文字にしてください")
                if not re.fullmatch(r"[A-Z]{2}", country) or not 1 <= channel <= 13:
                    raise ValueError("APのcountry/channel設定が不正です")
                hostapd_conf = (
                    "interface=wlan0\ndriver=nl80211\n"
                    f"country_code={country}\nssid={ssid}\n"
                    f"hw_mode=g\nchannel={channel}\nwpa=2\n"
                    f"wpa_passphrase={password}\n"
                    "wpa_key_mgmt=WPA-PSK\nrsn_pairwise=CCMP\n"
                )
                with open("/tmp/dap_hostapd.conf", "w", encoding="utf-8") as f:
                    f.write(hostapd_conf)
                os.chmod("/tmp/dap_hostapd.conf", 0o600)

                dns_result = subprocess.run(
                    ["sudo", "dnsmasq", "-C", "/tmp/dnsmasq_ap.conf"],
                    check=False, capture_output=True, text=True, timeout=10,
                )
                if dns_result.returncode != 0:
                    raise RuntimeError(f"dnsmasq起動失敗: {dns_result.stderr.strip()}")
                hostapd_result = subprocess.run(
                    ["sudo", "hostapd", "-B", "/tmp/dap_hostapd.conf"],
                    check=False, capture_output=True, text=True, timeout=10,
                )
                if hostapd_result.returncode != 0:
                    subprocess.run(["sudo", "pkill", "-TERM", "dnsmasq"], check=False)
                    raise RuntimeError(f"hostapd起動失敗: {hostapd_result.stderr.strip()}")

                time.sleep(0.5)
                if not get_ap_status():
                    raise RuntimeError("hostapdが起動状態になりません")
                dnsmasq_state = subprocess.run(
                    ["pgrep", "-x", "dnsmasq"], capture_output=True, timeout=3
                )
                if dnsmasq_state.returncode != 0:
                    raise RuntimeError("dnsmasqが起動状態になりません")

            time.sleep(1)
            run_required(["sudo", "systemctl", "restart", "avahi-daemon"], timeout=20)

            status_message = ""
            update_menu_items()
            request_display_update(is_full_refresh=False)

        except Exception as e:
            print(f"AP Mode toggle error: {e}")
            if not is_active:
                subprocess.run(["sudo", "pkill", "-TERM", "hostapd"], check=False)
                subprocess.run(["sudo", "pkill", "-TERM", "dnsmasq"], check=False)
                subprocess.run(["sudo", "ip", "addr", "flush", "dev", "wlan0"], check=False)
                subprocess.run(["sudo", "systemctl", "restart", "NetworkManager"], check=False)
                if ap_previous_wifi_on is False:
                    subprocess.run(["sudo", "rfkill", "block", "wlan"], check=False)
                ap_previous_wifi_on = None
                subprocess.run(["sudo", "systemctl", "restart", "avahi-daemon"], check=False)
            else:
                # Recover the normal Wi-Fi manager if AP shutdown only partly completed.
                subprocess.run(["sudo", "systemctl", "restart", "NetworkManager"], check=False, timeout=30)
            status_message = "APモード失敗"
            update_menu_items(reset_cursor=False)
            request_display_update(is_full_refresh=False)
        finally:
            with state_lock:
                ap_toggle_in_progress = False

    threading.Thread(target=_do_toggle, daemon=True).start()

def get_saved_wifi_connections():
    merged = {}
    try:
        res = subprocess.check_output(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            text=True, errors="ignore", timeout=5,
        )
        for name, conn_type in parse_nmcli_connection_show(res):
            cleaned = name.strip()
            if conn_type != "802-11-wireless" or cleaned == "Pi-DAP-AP" or is_probably_invalid_ssid(cleaned):
                continue
            merged.setdefault(cleaned, {"ssid": cleaned, "nmcli_id": None, "wpa_id": None})
            merged[cleaned]["nmcli_id"] = name
    except Exception:
        pass

    try:
        res = subprocess.check_output(
            ["sudo", "wpa_cli", "-i", "wlan0", "list_networks"],
            text=True, errors="ignore", timeout=5,
        )
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

def _wifi_menu_load_timeout(generation, reason):
    """Release the Wi-Fi menu from a stale loading state if a worker stalls."""
    global wifi_menu_loading, wifi_menu_load_error
    with state_lock:
        if generation != wifi_menu_load_generation or not wifi_menu_loading:
            return
        if time.monotonic() - wifi_menu_loading_since < 30.0:
            return
        wifi_menu_loading = False
        wifi_menu_load_error = True
        print(f"Wi-Fi設定の読み込みが30秒でタイムアウトしました: reason={reason}", flush=True)
        if current_screen == "MENU_WIFI":
            update_menu_items(reset_cursor=False)
            request_display_update(is_full_refresh=True)


def refresh_wifi_menu(state_lock_held=False, reason="manual"):
    """Refresh Wi-Fi menu data asynchronously so GPIO handlers never wait on CLI calls."""
    global wifi_menu_enabled, wifi_menu_current_ssid, wifi_menu_loading
    global wifi_menu_loading_since, wifi_menu_load_error, wifi_menu_load_generation
    global saved_wifi_connections, saved_wifi_ssids

    if state_lock_held:
        if wifi_menu_loading:
            return
        wifi_menu_load_generation += 1
        generation = wifi_menu_load_generation
        wifi_menu_loading = True
        wifi_menu_loading_since = time.monotonic()
        wifi_menu_load_error = False
    else:
        with state_lock:
            if wifi_menu_loading:
                return
            wifi_menu_load_generation += 1
            generation = wifi_menu_load_generation
            wifi_menu_loading = True
            wifi_menu_loading_since = time.monotonic()
            wifi_menu_load_error = False

    def _load():
        global wifi_menu_enabled, wifi_menu_current_ssid, wifi_menu_loading
        global wifi_menu_load_error, saved_wifi_connections, saved_wifi_ssids
        wifi_on, connections, current_ssid = False, [], None
        load_error = False
        try:
            wifi_on, _ = get_rfkill_status()
            connections = get_saved_wifi_connections() if wifi_on else []
            current_ssid = get_current_wifi_ssid() if wifi_on else None
        except Exception as error:
            load_error = True
            print(f"Wi-Fi設定の読み込み失敗: {error}", flush=True)
        with state_lock:
            if generation != wifi_menu_load_generation:
                return
            wifi_menu_enabled = wifi_on
            saved_wifi_connections = connections
            saved_wifi_ssids = [entry["ssid"] for entry in connections]
            wifi_menu_current_ssid = current_ssid
            wifi_menu_loading = False
            wifi_menu_load_error = load_error
            print(
                f"Wi-Fi設定を更新しました: profiles={len(connections)}, "
                f"radio={'on' if wifi_on else 'off'}, screen={current_screen}, "
                f"generation={generation}, reason={reason}",
                flush=True,
            )
            if current_screen == "MENU_WIFI":
                update_menu_items(reset_cursor=False)
                request_display_update(is_full_refresh=True)

    try:
        threading.Thread(target=_load, daemon=True).start()
    except RuntimeError as error:
        with state_lock:
            if generation != wifi_menu_load_generation:
                return
            wifi_menu_loading = False
            wifi_menu_load_error = True
            print(f"Wi-Fi設定の読み込みスレッドを開始できません: {error}", flush=True)
            if current_screen == "MENU_WIFI":
                update_menu_items(reset_cursor=False)
                request_display_update(is_full_refresh=True)
        return

    timer = threading.Timer(30.0, _wifi_menu_load_timeout, args=(generation, reason))
    timer.daemon = True
    timer.start()

def get_saved_wifi_ssids():
    return [entry["ssid"] for entry in get_saved_wifi_connections()]

def connect_to_wifi(target):
    """保存済みWi-Fiへ接続する。呼び出し元(GPIOハンドラ)はstate_lockを保持している。"""
    global wifi_connect_in_progress
    if wifi_connect_in_progress:
        return
    wifi_connect_in_progress = True

    def _do_connect():
        global status_message, wifi_connect_in_progress
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

        status_message = f"接続中: {target_ssid}"
        request_display_update(is_full_refresh=False)
        success = False
        detail = ""
        try:
            unblock = subprocess.run(
                ["sudo", "rfkill", "unblock", "wlan"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            if unblock.returncode != 0:
                raise RuntimeError((unblock.stderr or "Wi-Fiを有効化できません").strip())

            res = None
            if target_nmcli_id:
                res = subprocess.run(
                    ["nmcli", "connection", "up", "id", target_nmcli_id],
                    capture_output=True, text=True, check=False, timeout=25,
                )
            if res is None or res.returncode != 0:
                if not target_wpa_id:
                    res_wpa = subprocess.check_output(
                        ["sudo", "wpa_cli", "-i", "wlan0", "list_networks"],
                        text=True, errors="ignore", timeout=5,
                    )
                    for network in parse_wpa_cli_list_networks(res_wpa):
                        if (network.get("ssid") or "").strip() == target_ssid and network.get("id", "").isdigit():
                            target_wpa_id = network["id"]
                            break
                if target_wpa_id:
                    res = subprocess.run(
                        ["sudo", "wpa_cli", "-i", "wlan0", "select_network", target_wpa_id],
                        capture_output=True, text=True, check=False, timeout=8,
                    )
                    if res.returncode != 0 or "FAIL" in (res.stdout or "").upper():
                        raise RuntimeError((res.stderr or res.stdout or "wpa_supplicantが接続を拒否しました").strip())
                elif res is not None:
                    detail = (res.stderr or res.stdout or "NetworkManager接続失敗").strip()
                else:
                    detail = "保存済みネットワークが見つかりません"

            if not detail:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if get_current_wifi_ssid() == target_ssid:
                        success = True
                        break
                    time.sleep(0.5)
                if not success:
                    detail = "接続を確認できません"
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            detail = str(error).strip()

        status_message = "Wi-Fi接続成功" if success else "Wi-Fi接続失敗"
        if not success:
            print(f"Wi-Fi接続失敗 ({target_ssid}): {detail or '原因不明'}")
        request_display_update(is_full_refresh=False)
        with state_lock:
            should_refresh_menu = current_screen == "MENU_WIFI"
            wifi_connect_in_progress = False
        if should_refresh_menu:
            refresh_wifi_menu(reason="connect-complete")

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

# --- Bluetooth 区分処理 ---
def get_bt_paired_devices():
    global bt_connected_macs
    devices = {}
    connected_macs = set()
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
        if bt_prop_is_yes(properties, "connected"):
            connected_macs.add(mac)
        name = resolve_bt_display_name(
            mac,
            alias=properties.get("alias"),
            name=properties.get("name"),
            listed_name=listed_name,
        )
        devs.append((mac, name))
    devs.sort(key=lambda item: item[1].lower())
    bt_connected_macs = connected_macs
    return devs

def refresh_bt_paired_devices():
    """Load paired-device details away from the GPIO callback/lock path."""
    global bt_paired_devices, bt_paired_devices_loading

    def _load():
        global bt_paired_devices, bt_paired_devices_loading
        try:
            devices = get_bt_paired_devices()
        except Exception as error:
            print(f"登録済みBluetooth機器の取得失敗: {error}")
            devices = []
        with state_lock:
            bt_paired_devices = devices
            bt_paired_devices_loading = False
            if current_screen == "MENU_BT_PAIRED":
                update_menu_items(reset_cursor=False)
                request_display_update(is_full_refresh=True)

    threading.Thread(target=_load, daemon=True).start()

def trigger_bt_scan(state_lock_held=False):
    global is_bt_scanning, bt_scanned_devices
    if state_lock_held:
        # The button handler already holds the non-reentrant state_lock.
        if is_bt_scanning:
            return
        is_bt_scanning = True
    else:
        with state_lock:
            if is_bt_scanning:
                return
            is_bt_scanning = True
    def _scan_thread():
        global is_bt_scanning, status_message, bt_scanned_devices
        scanned = []
        process = None
        status_message = "BT検索中(8秒)..."
        request_display_update(is_full_refresh=False)
        try:
            subprocess.run(
                ["sudo", "rfkill", "unblock", "bluetooth"], check=False, timeout=10
            )
            bt_service = subprocess.run(
                ["sudo", "systemctl", "is-active", "bluetooth"],
                capture_output=True, text=True, check=False, timeout=5,
            )
            if bt_service.stdout.strip() != "active":
                subprocess.run(
                    ["sudo", "systemctl", "start", "bluetooth"],
                    check=False, timeout=10,
                )

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
        finally:
            # bluetoothctl can survive a timeout or an exception during scan.
            # Reap it before allowing another scan to start.
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                    process.communicate(timeout=3)
                except (OSError, subprocess.SubprocessError) as error:
                    print(f"BT検索プロセス終了失敗: {error}")
        with state_lock:
            bt_scanned_devices = list(scanned)
            is_bt_scanning = False
        status_message = ""
        update_menu_items()
        request_display_update(is_full_refresh=False)
    threading.Thread(target=_scan_thread, daemon=True).start()

def get_bt_scanned_devices_snapshot():
    # The scan worker replaces this list atomically instead of mutating it.
    # Do not acquire state_lock here: update_menu_items() can be called from
    # on_btn_menu_or_select() while that same non-reentrant lock is held.
    return list(bt_scanned_devices)

def update_menu_items(reset_cursor=True):
    global menu_items, menu_entries, menu_item_bold, cursor_idx, scroll_offset, bt_paired_devices, bt_scanned_devices, saved_wifi_ssids, saved_wifi_connections, library_menu_entries, selected_album
    sync_favorites()
    if reset_cursor:
        cursor_idx = 0
        scroll_offset = 0
    library_menu_entries = []

    # メニューは表示ラベル(label)と操作ID(id)・付随データ(data)の組で構築する。
    # 選択処理はidで分岐するため、表示文言の変更や他言語化の影響を受けない。
    entries = []

    def add(label, action_id, data=None, bold=False):
        entries.append({"label": label, "id": action_id, "data": data, "bold": bold})

    if current_screen == "MENU_TOP":
        add("★お気に入り", "open_favs")
        add("アルバム", "open_albums")
        add("再生設定", "open_play_settings")
        add("システム設定", "open_sys")
        if is_clock_unlocked:
            add("時計画面", "open_cat_clock")

    elif current_screen == "MENU_PLAY_SETTINGS":
        rep_labels = ["OFF", "ONE", "ALL"]
        add("../ (戻る)", "back", ("MENU_TOP", None))
        add(f"シャッフル: {'ON' if is_shuffle else 'OFF'}", "toggle_shuffle")
        add(f"リピート: {rep_labels[repeat_mode]}", "toggle_repeat")
        add("スリープタイマー", "open_sleep")
        add("出力先設定", "open_output")
        if is_eq_unlocked:
            add("EQ設定", "open_eq")

    elif current_screen == "MENU_SLEEP":
        add("../ (戻る)", "back", ("MENU_PLAY_SETTINGS", None))
        add(f"タイマーOFF {'<--' if sleep_timer_minutes == 0 else ''}", "set_sleep", 0)
        add(f"30分 {'<--' if sleep_timer_minutes == 30 else ''}", "set_sleep", 30)
        add(f"1時間 {'<--' if sleep_timer_minutes == 60 else ''}", "set_sleep", 60)
        add(f"2時間 {'<--' if sleep_timer_minutes == 120 else ''}", "set_sleep", 120)

    elif current_screen == "MENU_FAVS":
        # 削除済みファイルは履歴と同様に表示から除外する(登録自体は残す)。
        available_favs = [path for path in favorites_list if os.path.isfile(path)]
        add("../ (戻る)", "back", ("MENU_TOP", None))
        for path in available_favs:
            add(os.path.splitext(os.path.basename(path))[0], "play_fav", (list(available_favs), path))

    elif current_screen == "MENU_HISTORY":
        history_paths = get_available_playback_history()
        fav_set = set(favorites_list)
        add("../ (戻る)", "back", ("MENU_ALBUMS", None))
        for path in history_paths:
            add(os.path.splitext(os.path.basename(path))[0], "play_history",
                (list(history_paths), path), bold=path in fav_set)

    elif current_screen == "MENU_CONN":
        add("../ (戻る)", "back", ("MENU_SYS", None))
        add("Wi-Fi設定", "open_wifi")
        add("Bluetooth設定", "open_bt")
        add(f"APモード: {'ON' if get_ap_status() else 'OFF'}", "ap_toggle")

    elif current_screen == "MENU_OUTPUT":
        add("../ (戻る)", "back", ("MENU_PLAY_SETTINGS", None))
        add(f"現在のモード: [{audio_output_mode}]", "none")
        add("PWMに変更", "output_pwm")
        add("BTに変更", "output_bt")
        add("USBに変更", "output_usb")

    elif current_screen == "MENU_WIFI":
        add("../ (戻る)", "back", ("MENU_CONN", None))
        add(f"Wi-Fi機能: {'ON' if wifi_menu_enabled else 'OFF'}", "wifi_rfkill")
        if wifi_menu_enabled:
            if saved_wifi_connections:
                for wifi_entry in saved_wifi_connections:
                    ssid = wifi_entry["ssid"]
                    connected_label = " (接続済)" if wifi_menu_current_ssid and ssid == wifi_menu_current_ssid else ""
                    add(f"接続: {ssid}{connected_label}", "wifi_connect", dict(wifi_entry))
            elif wifi_menu_loading:
                add("(読み込み中...)", "none")
            elif wifi_menu_load_error:
                add("(読み込み失敗: 戻って再試行)", "none")
            else:
                add("(記憶されたWi-Fiなし)", "none")
            add(f"IP: {menu_ip_address}", "none")
        else:
            add("(Wi-Fi OFF中)", "none")

    elif current_screen == "MENU_BT":
        _, bt_on = get_rfkill_status()
        add("../ (戻る)", "back", ("MENU_CONN", None))
        add(f"Bluetooth機能: {'ON' if bt_on else 'OFF'}", "bt_rfkill")
        if bt_on:
            add(f"BTコーデック: {menu_bt_codec}", "none")
            add("登録済みデバイス", "open_bt_paired")
            add("新規デバイスの検索", "open_bt_scan")
        else:
            add("(Bluetooth OFF中)", "none")

    elif current_screen == "MENU_BT_PAIRED":
        add("../ (戻る)", "back", ("MENU_BT", None))
        if bt_paired_devices:
            name_counts = {}
            for _, name in bt_paired_devices:
                name_counts[name] = name_counts.get(name, 0) + 1
            for mac, name in bt_paired_devices:
                display_name = format_bt_menu_name(name, mac, include_suffix=name_counts.get(name, 0) > 1)
                connected_label = " (接続済)" if mac in bt_connected_macs else ""
                add(f"接続: {display_name}{connected_label}", "bt_connect", (mac, name))
        elif bt_paired_devices_loading:
            add("(読み込み中...)", "none")
        else:
            add("(登録済み機器なし)", "none")

    elif current_screen == "MENU_BT_SCAN":
        add("../ (戻る)", "back", ("MENU_BT", None))
        add("検索開始", "bt_scan_start")
        scanned_snapshot = get_bt_scanned_devices_snapshot()
        name_counts = {}
        for _, name in scanned_snapshot:
            name_counts[name] = name_counts.get(name, 0) + 1
        for mac, name in scanned_snapshot:
            add(f"ペアリング: {format_bt_menu_name(name, mac, include_suffix=name_counts.get(name, 0) > 1)}", "bt_connect", (mac, name))

    elif current_screen == "MENU_SYS":
        add("../ (戻る)", "back", ("MENU_TOP", None))
        add("接続設定", "open_conn")
        add("電源", "open_power")
        add("GitHubから更新", "github_update")
        add("ライセンス表示", "license")
        if is_clock_unlocked or is_eq_unlocked:
            add("隠し機能を削除", "remove_hidden")
        add("RAM/ROM使用量", "open_resource_ram")
        add("CPU使用率/温度", "open_resource_cpu")

    elif current_screen == "MENU_POWER":
        add("../ (戻る)", "back", ("MENU_SYS", None))
        add("アプリ再起動", "app_restart")
        add("再起動", "sys_reboot")
        add("シャットダウン", "sys_shutdown")

    elif current_screen in RESOURCE_SCREENS:
        add("../ (戻る)", "back", ("MENU_SYS", None))

    elif current_screen == "MENU_ALBUMS":
        library_menu_entries = get_library_entries(ROOT_REL_DIR, include_folder_play=False)
        fav_set = set(favorites_list)
        add("../ (戻る)", "back", ("MENU_TOP", None))
        add("曲ライブラリ再読み込み", "lib_reload")
        add("[再生履歴]", "open_history")
        for lib_entry in library_menu_entries:
            if lib_entry["type"] == "dir":
                add(lib_entry["display"], "album_dir", lib_entry["rel_dir"])
            elif lib_entry["type"] == "track":
                # お気に入り済みの曲は太字(二度描き)で識別する
                add(lib_entry["display"], "play_track", lib_entry["path"],
                    bold=lib_entry["path"] in fav_set)

    elif current_screen == "MENU_TRACKS":
        browsing_dir = normalize_rel_dir(selected_album)
        selected_album = browsing_dir
        library_menu_entries = get_library_entries(browsing_dir, include_folder_play=True)
        fav_set = set(favorites_list)
        parent_dir = normalize_rel_dir(os.path.dirname(browsing_dir))
        if browsing_dir == ROOT_REL_DIR:
            add("../ (戻る)", "back", ("MENU_TOP", None))
        elif parent_dir == ROOT_REL_DIR:
            add("../ (戻る)", "back", ("MENU_ALBUMS", None))
        else:
            add("../ (戻る)", "back", ("MENU_TRACKS", parent_dir))
        for lib_entry in library_menu_entries:
            if lib_entry["type"] == "dir":
                add(lib_entry["display"], "album_dir", lib_entry["rel_dir"])
            elif lib_entry["type"] == "play_folder":
                add(lib_entry["display"], "play_folder", lib_entry["rel_dir"])
            elif lib_entry["type"] == "track":
                add(lib_entry["display"], "play_track", lib_entry["path"],
                    bold=lib_entry["path"] in fav_set)

    menu_entries = entries
    menu_items = [entry["label"] for entry in entries]
    menu_item_bold = [entry["bold"] for entry in entries]

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

# --- 出力デバイス(USB DAC / Bluetooth)の切断検出と自動復帰 ---
@contextlib.contextmanager
def try_state_lock(timeout=2.0):
    acquired = state_lock.acquire(timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            state_lock.release()

def suspend_audio_for_lost_output(message):
    """出力デバイスが消えたとき、再生を一時停止扱いにしてmixerを閉じる。"""
    global is_playing, pause_start_time, track_paused_time
    global audio_output_lost, audio_lost_action, status_message
    global audio_transition_in_progress
    audio_transition_in_progress = True
    try:
        with try_state_lock() as locked:
            if not locked:
                return False
            if is_playing:
                track_paused_time = get_current_sec()
                pause_start_time = time.time()
                is_playing = False
                audio_lost_action = "resume"
            else:
                audio_lost_action = None
            audio_output_lost = True
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
        except Exception as error:
            print(f"Audio shutdown warning: {error}")
        if asound:
            try:
                asound.snd_config_update_free_global()
            except Exception:
                pass
        status_message = message
        request_display_update(is_full_refresh=False)
        return True
    finally:
        audio_transition_in_progress = False

def restore_playback_after_audio_return():
    """出力が戻り、mixerを再初期化した後に再生状態を復元する。"""
    global is_playing, start_time, paused_duration, pause_start_time
    global track_paused_time, audio_lost_action, status_message
    action = audio_lost_action
    audio_lost_action = None
    if not playlist:
        return
    if action == "restart":
        play_current_track(full_refresh=False)
        return
    if pause_start_time <= 0:
        return  # 曲が読み込まれていない停止状態。そのままにする。
    filepath = playlist[current_track_idx]
    position = float(max(0, track_paused_time))
    try:
        pygame.mixer.music.load(filepath)
        try:
            pygame.mixer.music.play(start=position)
        except Exception as seek_error:
            print(f"位置指定の再生に失敗、曲頭から再生します: {seek_error}")
            pygame.mixer.music.play()
            # state_lockは取らない。旧実装の呼び出し経路(UIがstate_lock保持中の
            # 出力切替)ではここで再取得するとデッドロックしていた。
            # 各代入はGILにより原子で、時刻計算の僅かなずれは表示だけの影響。
            start_time = pause_start_time = time.time()
            paused_duration = 0.0
            track_paused_time = 0
        if action == "resume":
            paused_duration += time.time() - pause_start_time
            pause_start_time = 0.0
            is_playing = True
        else:
            pygame.mixer.music.pause()
    except Exception as error:
        print(f"Playback restore error: {error}")
        is_playing = False
        status_message = "再生を復元できません"

def recover_usb_otg_audio():
    global audio_output_lost, status_message, audio_transition_in_progress
    audio_transition_in_progress = True
    try:
        if not init_usb_otg_audio(attempts=3, delay=1.0):
            status_message = "USB音声初期化失敗"
            request_display_update(is_full_refresh=False)
            return False
        apply_eq_settings()
        audio_output_lost = False
        status_message = "USB DAC再接続"
        restore_playback_after_audio_return()
        request_display_update(is_full_refresh=False)
        print("USB DAC reconnected; audio restored.")
        return True
    finally:
        audio_transition_in_progress = False

def bt_link_present():
    try:
        return has_bluetooth_acl_link(os.listdir("/sys/class/bluetooth"))
    except OSError:
        return False

def attempt_bt_reconnect(mac):
    try:
        result = subprocess.run(
            ["bluetoothctl", "connect", mac],
            capture_output=True, text=True, errors="ignore", timeout=9, check=False,
        )
        lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
        print(f"BT再接続試行 {mac}: rc={result.returncode} {lines[-1] if lines else ''}")
    except (OSError, subprocess.SubprocessError) as error:
        print(f"BT再接続試行 {mac}: {error}")

def recover_bt_audio(mac):
    global audio_output_lost, status_message, audio_transition_in_progress
    audio_transition_in_progress = True
    try:
        print(f"BT音声の復帰を開始: {mac}")
        if not is_bluealsa_active():
            print("BlueALSAが停止中のため起動します")
            subprocess.run(["sudo", "systemctl", "start", "bluealsa"], check=False, timeout=10)
        pcm_listed = wait_for_bluealsa_pcm(mac, timeout=6.0)
        if not pcm_listed:
            # aplay -L に出なくても、bluealsa PCMは機器指定で直接開けることがある。
            print("BlueALSA PCMが一覧に見えません。そのまま開けるか試します")
        try:
            write_text_atomically(asoundrc_path, build_bluealsa_asoundrc(mac))
        except Exception as error:
            print(f"BT ALSA設定更新失敗: {error}")
            return False
        if not safe_init_audio():
            status_message = "BT音声初期化失敗" if pcm_listed else "音声デバイス未検出"
            print(f"BT音声を開けませんでした (PCM一覧: {'あり' if pcm_listed else 'なし'})")
            request_display_update(is_full_refresh=False)
            return False
        apply_eq_settings()
        audio_output_lost = False
        status_message = "BT再接続"
        restore_playback_after_audio_return()
        request_display_update(is_full_refresh=False)
        print("Bluetooth reconnected; audio restored.")
        return True
    finally:
        audio_transition_in_progress = False

BT_RECONNECT_WINDOW_SEC = 10.0
# 10秒の集中試行のあとも、イヤホンの電源を入れ直す場合に備えて間隔を空けて再接続を試す。
# 0にすると無効(受け身で待つだけ)。電池消費を抑えるため、合計時間で打ち切る。
BT_BACKGROUND_RETRY_INTERVAL_SEC = 20.0
BT_BACKGROUND_RETRY_TOTAL_SEC = 300.0

def audio_output_watchdog_thread():
    """USB DAC / Bluetoothの切断を検出して一時停止し、戻ったら自動で復帰させる。"""
    global status_message, audio_output_lost, audio_lost_action
    usb_missing = 0
    usb_seen_since = None
    next_usb_retry = 0.0
    bt_missing = 0
    next_bt_confirm = time.monotonic() + 15.0
    bt_window_end = (
        time.monotonic() + BT_RECONNECT_WINDOW_SEC
        if audio_output_lost and audio_output_mode == "BT" else 0.0
    )
    next_bt_attempt = 0.0
    next_bt_background = 0.0
    next_bt_probe = 0.0
    next_bt_recover = 0.0
    bt_gave_up = False

    while True:
        time.sleep(0.5)
        try:
            if audio_transition_in_progress or bt_connect_in_progress:
                usb_missing = 0
                bt_missing = 0
                continue
            mode = audio_output_mode
            now = time.monotonic()

            if audio_output_lost and pygame.mixer.get_init():
                # 再生ボタンなどで既に音声が初期化された。再初期化して止めないよう状態だけ戻す。
                audio_output_lost = False
                audio_lost_action = None
                continue

            if mode == "USBOTG":
                present = usb_audio_present()
                if not audio_output_lost:
                    usb_seen_since = None
                    usb_missing = 0 if present else usb_missing + 1
                    if usb_missing >= 2:  # 約1秒の猶予
                        usb_missing = 0
                        print("USB DAC disconnected; pausing playback.")
                        submit_audio_command("suspend_lost", "USB DAC切断")
                elif not present:
                    usb_seen_since = None
                else:
                    if usb_seen_since is None:
                        usb_seen_since = now
                    elif now - usb_seen_since >= 1.0 and now >= next_usb_retry:
                        # 復帰はワーカーへ依頼。失敗時はnext_usb_retryの間隔で再依頼する。
                        submit_audio_command("recover_usb")
                        usb_seen_since = None
                        next_usb_retry = time.monotonic() + 3.0

            elif mode == "BT" and connected_bt_mac:
                mac = connected_bt_mac
                if not audio_output_lost:
                    bt_gave_up = False
                    lost = False
                    if bt_link_present():
                        bt_missing = 0
                        if now >= next_bt_confirm:
                            # 他のBT機器が接続中でも検出できるよう、低頻度で対象MACを確認する。
                            next_bt_confirm = now + 15.0
                            lost = not is_bt_connected(mac)
                    else:
                        bt_missing += 1
                        if bt_missing >= 3:
                            bt_missing = 0
                            lost = not is_bt_connected(mac)
                            if not lost:
                                next_bt_confirm = time.monotonic() + 15.0
                    if lost and audio_output_mode == "BT":
                        print("Bluetooth disconnected; pausing and trying to reconnect.")
                        submit_audio_command("suspend_lost", "BT切断")
                        bt_window_end = time.monotonic() + BT_RECONNECT_WINDOW_SEC
                        next_bt_attempt = 0.0
                        next_bt_background = bt_window_end + 5.0
                        next_bt_probe = 0.0
                        next_bt_recover = 0.0
                else:
                    # 他のBT機器が接続中でもsysfsの接続一覧は残るため、
                    # 復帰判定は対象MACを直接確認する(再生していないので負荷は問題にならない)。
                    link_back = False
                    if now >= next_bt_probe:
                        next_bt_probe = now + 2.0
                        link_back = is_bt_connected(mac)
                    if not link_back and now < bt_window_end and now >= next_bt_attempt:
                        status_message = "再接続中: BT"
                        request_display_update(is_full_refresh=False)
                        attempt_bt_reconnect(mac)
                        next_bt_attempt = time.monotonic() + 1.0
                        link_back = is_bt_connected(mac)
                    elif (
                        not link_back
                        and BT_BACKGROUND_RETRY_INTERVAL_SEC > 0
                        and bt_window_end <= now < bt_window_end + BT_BACKGROUND_RETRY_TOTAL_SEC
                        and now >= next_bt_background
                    ):
                        # 画面は更新せず静かに再接続を試す(電源を入れ直したイヤホンを拾う)。
                        attempt_bt_reconnect(mac)
                        next_bt_background = time.monotonic() + BT_BACKGROUND_RETRY_INTERVAL_SEC
                        link_back = is_bt_connected(mac)
                    if link_back:
                        if next_bt_recover == 0.0 or time.monotonic() >= next_bt_recover:
                            print(f"BT機器の接続を検出: {mac}")
                        if (
                            audio_output_mode == "BT"
                            and connected_bt_mac == mac
                            and time.monotonic() >= next_bt_recover
                            and not audio_transition_in_progress
                            and not bt_connect_in_progress
                        ):
                            # 復帰はワーカーへ依頼。成否は次ループのaudio_output_lostで
                            # 判定し、失敗時はnext_bt_recoverの間隔で再依頼する。
                            submit_audio_command("recover_bt", mac)
                            next_bt_recover = time.monotonic() + 5.0
                    elif time.monotonic() >= bt_window_end and not bt_gave_up:
                        bt_gave_up = True
                        print(f"BT再接続を{BT_RECONNECT_WINDOW_SEC:.0f}秒試しましたが接続できません。接続され次第、自動で復帰します。")
                        status_message = "BT再接続失敗"
                        request_display_update(is_full_refresh=False)
            else:
                usb_missing = 0
                bt_missing = 0
        except Exception as error:
            print(f"Audio watchdog error: {error}")
            time.sleep(2.0)

def set_playback_playlist(tracks, selected_path=None, start_index=0):
    """Set the active source list, applying shuffle only within that list."""
    global playlist, playlist_base, current_track_idx
    playlist_base = list(tracks)
    playlist = random.sample(playlist_base, len(playlist_base)) if is_shuffle else list(playlist_base)
    if selected_path in playlist:
        current_track_idx = playlist.index(selected_path)
    elif playlist:
        current_track_idx = max(0, min(start_index, len(playlist) - 1))
    else:
        current_track_idx = 0

def toggle_shuffle():
    global is_shuffle, playlist, playlist_base, current_track_idx
    is_shuffle = not is_shuffle
    current_song = playlist[current_track_idx] if playlist else None
    if not playlist_base:
        playlist_base = list(playlist)
    playlist = random.sample(playlist_base, len(playlist_base)) if is_shuffle else list(playlist_base)
    if current_song and current_song in playlist:
        current_track_idx = playlist.index(current_song)
    save_settings()
    save_resume_state()

def toggle_repeat():
    global repeat_mode
    repeat_mode = (repeat_mode + 1) % 3
    save_settings()

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
        save_settings()
        current_screen = "CAT_CLOCK"
        request_display_update(is_full_refresh=True)
        return True
    return False

# --- 9. ボタンイベントハンドラ ---
def on_btn_menu_or_select():
    if not debounce("btn_menu_select"): return
    global current_screen, selected_album, status_message, eq_cursor
    global is_clock_unlocked, is_eq_unlocked
    global bt_paired_devices, bt_paired_devices_loading
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
            return

        if cursor_idx >= len(menu_entries):
            return
        entry = menu_entries[cursor_idx]
        entry_id = entry["id"]
        entry_data = entry["data"]

        if entry_id == "back":
            # data = (遷移先画面, 遷移先で選択するアルバムディレクトリ)
            target_screen, target_album = entry_data
            selected_album = target_album
            current_screen = target_screen
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)

        elif entry_id == "open_albums":
            selected_album = None
            current_screen = "MENU_ALBUMS"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_cat_clock":
            current_screen = "CAT_CLOCK"
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_play_settings":
            current_screen = "MENU_PLAY_SETTINGS"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_conn":
            current_screen = "MENU_CONN"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_sys":
            current_screen = "MENU_SYS"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_output":
            current_screen = "MENU_OUTPUT"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_wifi":
            current_screen = "MENU_WIFI"
            refresh_wifi_menu(state_lock_held=True, reason="open-menu")
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_bt":
            current_screen = "MENU_BT"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_bt_paired":
            current_screen = "MENU_BT_PAIRED"
            bt_paired_devices_loading = True
            bt_paired_devices = []
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
            refresh_bt_paired_devices()
        elif entry_id == "open_bt_scan":
            current_screen = "MENU_BT_SCAN"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_favs":
            current_screen = "MENU_FAVS"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_history":
            current_screen = "MENU_HISTORY"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_sleep":
            current_screen = "MENU_SLEEP"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_eq":
            current_screen = "CAT_EQ"
            eq_cursor = 0
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_resource_ram":
            current_screen = "MENU_SYS_RAM_ROM"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_resource_cpu":
            current_screen = "MENU_SYS_CPU_TEMP"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif entry_id == "open_power":
            current_screen = "MENU_POWER"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)

        elif entry_id == "toggle_shuffle":
            toggle_shuffle()
            update_menu_items(reset_cursor=False)
            request_display_update(is_full_refresh=False)
        elif entry_id == "toggle_repeat":
            toggle_repeat()
            update_menu_items(reset_cursor=False)
            request_display_update(is_full_refresh=False)
        elif entry_id == "set_sleep":
            set_sleep_timer(entry_data)
            update_menu_items(reset_cursor=False)
            request_display_update(is_full_refresh=False)

        elif entry_id == "play_fav":
            fav_playlist, fav_path = entry_data
            set_playback_playlist(fav_playlist, selected_path=fav_path)
            play_current_track(full_refresh=True)
            current_screen = "PLAY"
        elif entry_id == "play_history":
            history_playlist, history_path = entry_data
            set_playback_playlist(history_playlist, selected_path=history_path)
            play_current_track(full_refresh=True)
            current_screen = "PLAY"
        elif entry_id == "play_folder":
            subtree_tracks = get_tracks_in_subtree(entry_data)
            if subtree_tracks:
                set_playback_playlist(subtree_tracks, start_index=0)
                play_current_track(full_refresh=True)
                current_screen = "PLAY"
        elif entry_id == "play_track":
            browsing_dir = normalize_rel_dir(selected_album)
            direct_tracks = get_direct_tracks_in_dir(browsing_dir)
            if entry_data in direct_tracks:
                set_playback_playlist(direct_tracks, selected_path=entry_data)
                play_current_track(full_refresh=True)
                current_screen = "PLAY"
        elif entry_id == "album_dir":
            selected_album = entry_data
            current_screen = "MENU_TRACKS"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)

        elif entry_id == "ap_toggle":
            toggle_ap_mode(state_lock_held=True)
            update_menu_items(reset_cursor=False)
            request_display_update(is_full_refresh=False)
        elif entry_id == "output_pwm":
            set_audio_output("PWM", state_lock_held=True)
        elif entry_id == "output_bt":
            set_audio_output("BT", connected_bt_mac, state_lock_held=True)
        elif entry_id == "output_usb":
            set_audio_output("USBOTG", state_lock_held=True)
        elif entry_id == "wifi_rfkill":
            toggle_rfkill("wifi")
            refresh_wifi_menu(state_lock_held=True, reason="radio-toggle")
            update_menu_items(reset_cursor=False)
            request_display_update(is_full_refresh=False)
        elif entry_id == "wifi_connect":
            connect_to_wifi(entry_data)
        elif entry_id == "bt_rfkill":
            toggle_rfkill("bt")
            update_menu_items(reset_cursor=False)
            request_display_update(is_full_refresh=False)
        elif entry_id == "bt_connect":
            connect_mac, connect_name = entry_data
            start_bt_connection(connect_mac, connect_name, state_lock_held=True)
        elif entry_id == "bt_scan_start":
            trigger_bt_scan(state_lock_held=True)

        elif entry_id == "lib_reload":
            reload_music_library()
            request_display_update(is_full_refresh=False)
        elif entry_id == "github_update":
            start_github_update()
        elif entry_id == "remove_hidden":
            is_clock_unlocked = False
            is_eq_unlocked = False
            save_settings()
            current_screen = "MENU_TOP"
            update_menu_items(reset_cursor=True)
            status_message = "隠し機能を削除しました"
            request_display_update(is_full_refresh=True)
        elif entry_id == "license":
            if not check_easter_egg_trigger():
                # タグ名にVが含まれるため、ここでVを前置きしない(二重表記防止)。
                status_message = f"MIT License\n(c) kakuritsu\nTwitter:@KAKURITU_P\n[{get_display_version(BASE_DIR)}]"
                request_display_update(is_full_refresh=False)
                def _clear_status():
                    time.sleep(3.0)
                    global status_message
                    status_message = ""
                    request_display_update(is_full_refresh=False)
                threading.Thread(target=_clear_status, daemon=True).start()
        elif entry_id == "app_restart":
            clean_shutdown_display("Restarting...")
            subprocess.run(["sudo", "systemctl", "restart", "dap.service"])
        elif entry_id == "sys_reboot":
            clean_shutdown_display("Rebooting...")
            subprocess.run(["sudo", "reboot"])
        elif entry_id == "sys_shutdown":
            clean_shutdown_display("Power Off...")
            subprocess.run(["sudo", "shutdown", "-h", "now"])
        # "none" や未知のIDは選択しても何もしない

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
                save_settings()
            apply_eq_settings()
            current_screen = "PLAY"
            request_display_update(is_full_refresh=True)
            return

        if exit_cat_clock_if_needed(): return

        if current_screen in RESOURCE_SCREENS:
            current_screen = "MENU_SYS"
            update_menu_items(reset_cursor=True)
            request_display_update(is_full_refresh=True)
        elif current_screen == "PLAY":
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
            # メニュー中: 押した瞬間にPLAY画面へ(音楽状態は触らない)
            current_screen = "PLAY"
            request_display_update(is_full_refresh=True)

def on_btn_up_action():
    if not debounce("btn_up_act"): return
    if exit_cat_clock_if_needed(): return
    global volume, volume_changed_at, cursor_idx, scroll_offset
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "CAT_EQ":
            if eq_cursor < 3:
                band = eq_bands[eq_cursor]
                eq_values[band] = min(6, eq_values[band] + 1)
                schedule_settings_save()
                request_display_update(is_full_refresh=False)
        elif current_screen == "PLAY":
            volume = min(volume + 0.05, 1.0)
            volume_changed_at = time.time()
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
    global volume, volume_changed_at, cursor_idx, scroll_offset
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "CAT_EQ":
            if eq_cursor < 3:
                band = eq_bands[eq_cursor]
                eq_values[band] = max(-6, eq_values[band] - 1)
                schedule_settings_save()
                request_display_update(is_full_refresh=False)
        elif current_screen == "PLAY":
            volume = max(volume - 0.05, 0.0)
            volume_changed_at = time.time()
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
    global current_screen, current_track_idx, eq_cursor, selected_album
    with state_lock:
        reset_inactivity_timer()

        # 1. EQ画面の場合: カーソル移動
        if current_screen == "CAT_EQ":
            eq_cursor = (eq_cursor - 1) % 4
            request_display_update(is_full_refresh=False)

        # 2. PLAY画面の場合: 曲戻し(3秒超なら頭出し)
        elif current_screen == "PLAY" and playlist:
            if is_playing and get_current_sec() > 3:
                play_current_track(full_refresh=True)
            else:
                current_track_idx = (current_track_idx - 1) % len(playlist)
                play_current_track(full_refresh=True)

        # 3. メニュー画面の場合: 1階層戻る(「../ (戻る)」と同じ動作。TOPでは戻り先が無いので何もしない)
        else:
            back_entry = next((entry for entry in menu_entries if entry["id"] == "back"), None)
            if back_entry is not None:
                target_screen, target_album = back_entry["data"]
                selected_album = target_album
                current_screen = target_screen
                update_menu_items(reset_cursor=True)
                request_display_update(is_full_refresh=True)

def handle_next_btn_press():
    """NEXTボタン: PLAY=曲送り / EQ=カーソル移動 / アルバム階層=カーソル曲の★トグル"""
    global current_track_idx, eq_cursor, status_message
    with state_lock:
        reset_inactivity_timer()
        if current_screen == "CAT_EQ":
            eq_cursor = (eq_cursor + 1) % 4
            request_display_update(is_full_refresh=False)
        elif current_screen in ("MENU_ALBUMS", "MENU_TRACKS", "MENU_FAVS", "MENU_HISTORY") and cursor_idx < len(menu_entries):
            # アルバム階層では曲送りを「カーソル曲のお気に入りトグル」に使う。
            entry = menu_entries[cursor_idx]
            if entry["id"] in ("play_track", "play_fav", "play_history"):
                path = entry["data"][1] if isinstance(entry["data"], tuple) else entry["data"]
                is_fav = toggle_favorite_by_path(path)
                status_message = "★ Fav 追加" if is_fav else "★ Fav 解除"
                update_menu_items(reset_cursor=False)
                request_display_update(is_full_refresh=False)
        elif current_screen == "PLAY" and playlist:
            current_track_idx = (current_track_idx + 1) % len(playlist)
            play_current_track(full_refresh=True)

def on_btn_next():
    if not debounce("btn_next"): return
    if exit_cat_clock_if_needed(): return
    handle_next_btn_press()

# --- 10. GPIO割り当て ---
BOUNCE_SEC = 0.1

btn_up_act      = Button(22, bounce_time=BOUNCE_SEC)
btn_down_act    = Button(27, bounce_time=BOUNCE_SEC)
btn_menu_select = Button(5,  bounce_time=BOUNCE_SEC)
btn_prev        = Button(12, bounce_time=BOUNCE_SEC)
btn_next        = Button(16, bounce_time=BOUNCE_SEC)
btn_play_back   = Button(20, bounce_time=BOUNCE_SEC)

# コールバック内で例外が飛ぶとgpiozeroの処理スレッドごと落ちて
# 以後の全ボタン入力が死ぬため、個別に捕捉してログだけ残す。
def safe_button(handler):
    def wrapper():
        try:
            handler()
        except Exception as error:
            print(f"Button handler error ({handler.__name__}): {error}")
    wrapper.__name__ = handler.__name__
    return wrapper

btn_up_act.when_pressed      = safe_button(on_btn_up_action)
btn_down_act.when_pressed    = safe_button(on_btn_down_action)
btn_menu_select.when_pressed = safe_button(on_btn_menu_or_select)
btn_prev.when_pressed        = safe_button(on_btn_prev)
btn_next.when_pressed        = safe_button(on_btn_next)
btn_play_back.when_pressed   = safe_button(on_btn_play_or_back)

# --- 11. メインループ ---
def handle_track_completion():
    global is_playing, current_track_idx

    if sleep_timer_pending or battery_shutdown_pending:
        message = "Battery Empty..." if battery_shutdown_pending else "Sleep Timer Off..."
        clean_shutdown_display(message)
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

# 音声ワーカーを先に起動してからwatchdogを始める(投入済みコマンドの処理保証)。
threading.Thread(target=audio_worker_thread, daemon=True).start()
threading.Thread(target=system_resource_monitor_thread, daemon=True).start()
threading.Thread(target=audio_output_watchdog_thread, daemon=True).start()

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
        if shutdown_exit_requested:
            # SIGTERM時に描画中だった場合は描画完了後にここで終了する
            flush_pending_settings_save()
            sys.exit(0)
        now = time.time()
        sync_favorites()
        # 予約済みのsettings.json書き込み(delay後)をここで確実に実行する。
        if _settings_save_dirty and now >= _settings_save_due:
            flush_pending_settings_save()
        # 音量表示の5秒期限切れ → 右端スロットをBATに戻す(部分更新1回)。
        if volume_changed_at and now - volume_changed_at >= 5.0:
            volume_changed_at = 0.0
            request_display_update(is_full_refresh=False)

        # 0%になっても再生中の曲は最後まで再生してから電源を切る。
        battery_percent = get_battery_percent()
        if battery_percent is not None and battery_percent <= 0:
            battery_shutdown_pending = True

        # --- スリープタイマー監視 ---
        # 時間が来たら即シャットダウンせず、待機フラグを立てる
        if sleep_timer_end_time > 0 and now >= sleep_timer_end_time:
            sleep_timer_pending = True
            sleep_timer_end_time = 0.0  # 二重判定防止

        # 音楽が停止中にタイマー時間が来た場合はそのままシャットダウン
        if (sleep_timer_pending or battery_shutdown_pending) and not is_playing:
            message = "Battery Empty..." if battery_shutdown_pending else "Sleep Timer Off..."
            clean_shutdown_display(message)
            subprocess.run(["sudo", "shutdown", "-h", "now"])

        # --- 曲の再生・終了監視 ---
        # watchdogの音声切替(suspend_audio_for_lost_output)と競合しないよう
        # 状態確認から次曲送りまでをstate_lock下で一括して行う。
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
                elif not music_busy and not audio_transition_in_progress:
                    handle_track_completion()
                elif (
                    not audio_transition_in_progress
                    and total_duration_sec > 0
                    and cur_sec >= total_duration_sec + 1
                ):
                    handle_track_completion()

        with state_lock:
            cur_sec = get_current_sec()

            if current_screen == "PLAY" and is_playing and total_duration_sec > 0:
                progress_ratio = min(1.0, cur_sec / float(total_duration_sec))
                current_segment = int(progress_ratio * 8)

                if current_segment != last_drawn_segment:
                    last_drawn_segment = current_segment
                    request_display_update(is_full_refresh=False)

            if current_screen not in ["PLAY", "CAT_CLOCK", "CAT_EQ", *RESOURCE_SCREENS] and (now - last_user_action_time > 10):
                current_screen = "PLAY"
                request_display_update(is_full_refresh=True)

except KeyboardInterrupt:
    clean_shutdown_display("Power Off...")
    sys.exit(0)
