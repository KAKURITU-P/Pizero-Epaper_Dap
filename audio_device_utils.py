"""USBオーディオデバイス関連の純粋ユーティリティ(環境値・/proc内容を引数で受ける)。"""

import os
import re

from bluealsa_utils import choose_usb_sample_rate, find_usb_audio_device

# ALSAのUSB音声デバイスが検出できなかった場合のフォールバック。
USB_OTG_FALLBACK_DEVICE = "plughw:1,0"


def resolve_usb_otg_device(usb_device_env, cards_text=None):
    """USB音声デバイスを返す。環境変数 > /proc/asound/cards の自動検出 > フォールバック。

    cards_textがNoneの場合は/proc/asound/cardsを読む(テスト時はテキストを渡す)。
    """
    if usb_device_env:
        return usb_device_env
    if cards_text is None:
        try:
            with open("/proc/asound/cards", "r", encoding="utf-8", errors="replace") as cards_file:
                cards_text = cards_file.read()
        except OSError:
            return USB_OTG_FALLBACK_DEVICE
    detected = find_usb_audio_device(cards_text)
    return detected or USB_OTG_FALLBACK_DEVICE


def asoundrc_uses_usb_otg(content, usb_device, usb_device_env=None):
    """~/.asoundrc が USB OTG 出力向けか判定する(カード番号が変わっても追従)。"""
    return (
        usb_device in content
        or "plughw:CARD=" in content
        or (usb_device_env is not None and usb_device_env in content)
    )


def get_audio_buffer_size(buffer_env=None):
    """pygameのバッファ(サンプル数)。環境変数 DAP_AUDIO_BUFFER で調整できる。"""
    if buffer_env is None:
        buffer_env = os.environ.get("DAP_AUDIO_BUFFER")
    try:
        value = int(buffer_env)
    except (TypeError, ValueError):
        return 16384
    return value if 1024 <= value <= 65536 else 16384


def get_usb_otg_sample_rate(usb_device, sample_rate_env=None, stream0_text=None):
    """USB音声のサンプルレート。環境変数 > デバイスのネイティブ対応レート > 44100。

    デバイスが対応していないレートで開くとALSAがソフトウェアでリサンプルし、
    Pi Zeroでは音切れの原因になるため、対応レートに合わせる。
    stream0_textがNoneの場合は/proc/asound/<card>/stream0を読む。
    """
    if sample_rate_env is None:
        sample_rate_env = os.environ.get("DAP_USB_OTG_SAMPLE_RATE")
    if sample_rate_env:
        try:
            rate = int(sample_rate_env)
            if 8000 <= rate <= 192000:
                return rate
        except ValueError:
            pass
    match = re.search(r"CARD=([A-Za-z0-9_]+)", usb_device or "")
    if match:
        if stream0_text is None:
            try:
                with open(f"/proc/asound/{match.group(1)}/stream0", "r",
                          encoding="utf-8", errors="replace") as stream_file:
                    stream0_text = stream_file.read()
            except OSError:
                return 44100
        return choose_usb_sample_rate(stream0_text, default=44100)
    return 44100
