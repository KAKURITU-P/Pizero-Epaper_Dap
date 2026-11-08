"""Pure helpers for configuring and checking BlueALSA A2DP playback.

This module intentionally has no subprocess or filesystem side effects so it can
be tested on a development machine. Runtime code can use these helpers before
reinitialising pygame/ALSA after a manual Bluetooth connection.
"""

from __future__ import annotations

import re
from typing import Iterable


_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")


def normalize_mac(mac: str) -> str:
    """Return an uppercase Bluetooth MAC or raise ValueError."""
    value = (mac or "").strip()
    if not _MAC_RE.fullmatch(value):
        raise ValueError(f"invalid Bluetooth MAC address: {mac!r}")
    return value.upper()


def bluealsa_pcm_name(mac: str, interface: str = "hci0") -> str:
    """Return BlueALSA's conventional A2DP sink PCM name."""
    return f"bluealsa:DEV={normalize_mac(mac)},PROFILE=a2dp"


def bluealsa_pcm_candidates(mac: str, interface: str = "hci0") -> tuple[str, ...]:
    """Return names accepted by common BlueALSA ALSA-plugin versions.

    Older releases expose the short ``bluealsa`` device while newer tools list
    the explicit ``bluealsa:DEV=...,PROFILE=a2dp`` name.  Keeping both makes
    runtime detection tolerant without changing the configured PCM.
    """
    normalized = normalize_mac(mac)
    return (
        bluealsa_pcm_name(normalized, interface),
        f"bluealsa:DEV={normalized}",
        "bluealsa",
    )


def build_bluealsa_asoundrc(mac: str, interface: str = "hci0") -> str:
    """Build an ALSA equalizer route ending in a BlueALSA A2DP PCM."""
    normalized = normalize_mac(mac)
    return f'''pcm.!default {{
    type plug
    slave.pcm "equal"
}}

pcm.equal {{
    type equal
    slave.pcm "dap_bt_plug"
}}

pcm.dap_bt_plug {{
    type plug
    slave.pcm "dap_bt"
}}

pcm.dap_bt {{
    type bluealsa
    device "{normalized}"
    profile "a2dp"
}}

ctl.equal {{
    type equal
}}
'''


def build_pwm_asoundrc() -> str:
    """Build the proven PWM equalizer route."""
    return '''pcm.!default {
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
'''


def build_usb_otg_asoundrc(device: str = "plughw:1,0") -> str:
    """Build the equalizer route for the USB OTG audio device."""
    if not device or any(char in device for char in '\n\r"'):
        raise ValueError("invalid USB OTG ALSA device")
    return f'''pcm.!default {{
    type plug
    slave.pcm "equal"
}}

pcm.equal {{
    type equal
    slave.pcm "{device}"
}}

ctl.equal {{
    type equal
}}
'''


_ALSA_CARD_RE = re.compile(r"^\s*(\d+)\s+\[([^\]]+?)\s*\]:\s*(\S+)")
_ALSA_CARD_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


def find_usb_audio_device(proc_asound_cards: str) -> str | None:
    """Return a stable ALSA device name for the first USB audio card.

    ``proc_asound_cards`` is the text of ``/proc/asound/cards``.  The card
    *id* (for example ``Device``) is used instead of the card number because
    the number changes when other cards, such as HDMI audio, are present.
    """
    for line in (proc_asound_cards or "").splitlines():
        match = _ALSA_CARD_RE.match(line)
        if not match:
            continue
        _number, card_id, driver = match.groups()
        if "usb" in driver.lower() and _ALSA_CARD_ID_RE.fullmatch(card_id):
            return f"plughw:CARD={card_id},DEV=0"
    return None


def pcm_list_contains_device(alsa_pcm_list: str, mac: str) -> bool:
    """Return whether BlueALSA output advertises the Bluetooth device."""
    normalized = normalize_mac(mac)
    compact = normalized.replace(":", "_").lower()
    upper = normalized.upper()
    for line in (alsa_pcm_list or "").splitlines():
        lowered = line.lower()
        if "bluealsa" not in lowered:
            continue
        if upper.lower() in lowered or compact in lowered:
            return True
    return False


def pcm_list_contains_any(alsa_pcm_list: str, names: Iterable[str]) -> bool:
    """Return whether any requested PCM name occurs in ``aplay -L`` output."""
    haystack = (alsa_pcm_list or "").lower()
    return any(name.lower() in haystack for name in names)
