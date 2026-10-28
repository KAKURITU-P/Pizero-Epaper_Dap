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
    """Build an ALSA configuration for equalised BlueALSA A2DP playback."""
    normalized = normalize_mac(mac)
    # Keep the default PCM named ``equal`` for compatibility with the existing
    # pygame setup and EQ controls. The BlueALSA device itself is explicit.
    return f'''pcm.!default {{
    type plug
    slave.pcm "equal"
}}

pcm.equal {{
    type equal
    slave.pcm {{
        type bluealsa
        interface "{interface}"
        device "{normalized}"
        profile "a2dp"
    }}
}}

ctl.equal {{
    type equal
}}
'''


def pcm_list_contains_device(alsa_pcm_list: str, mac: str) -> bool:
    """Return whether ``aplay -L`` output advertises the Bluetooth device."""
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
