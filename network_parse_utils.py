import re

MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")
_INVALID_BT_NAMES = {
    "",
    "not available",
    "unknown",
    "[unknown]",
    "none",
    "n/a",
}


def is_valid_bt_mac(mac):
    return bool(MAC_RE.fullmatch((mac or "").strip()))


def normalize_bt_name(name):
    value = " ".join((name or "").strip().split())
    if value.lower() in _INVALID_BT_NAMES:
        return ""
    return value


def resolve_bt_display_name(mac, alias=None, name=None, listed_name=None):
    for candidate in (alias, name, listed_name):
        normalized = normalize_bt_name(candidate)
        if normalized:
            return normalized
    return (mac or "").strip()


def parse_bluetoothctl_devices(output):
    devices = []
    seen = set()
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^Device\s+([0-9A-Fa-f:]{17})(?:\s+(.*))?$", line)
        if not match:
            continue
        mac = match.group(1).upper()
        if not is_valid_bt_mac(mac) or mac in seen:
            continue
        seen.add(mac)
        devices.append((mac, normalize_bt_name(match.group(2))))
    return devices


def _nmcli_unescape(value):
    out = []
    escaped = False
    for ch in value:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    if escaped:
        out.append("\\")
    return "".join(out)


def parse_nmcli_connection_show(output):
    rows = []
    for raw_line in (output or "").splitlines():
        line = raw_line.rstrip("\n")
        if not line:
            continue
        idx = None
        escaped = False
        for i, ch in enumerate(line):
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == ":":
                idx = i
                break
        if idx is None:
            continue
        name = _nmcli_unescape(line[:idx]).strip()
        conn_type = _nmcli_unescape(line[idx + 1 :]).strip()
        rows.append((name, conn_type))
    return rows


def parse_wpa_cli_list_networks(output):
    networks = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("network id") or lower.startswith("selected interface"):
            continue

        parts = raw_line.rstrip("\n").split("\t")
        if len(parts) >= 4 and parts[0].strip().isdigit():
            net_id = parts[0].strip()
            ssid = parts[1].strip()
            bssid = parts[2].strip()
            flags = "\t".join(parts[3:]).strip()
            networks.append({"id": net_id, "ssid": ssid, "bssid": bssid, "flags": flags})
            continue

        match = re.match(r"^\s*(\d+)\s+(.+?)\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}|any)\s*(\[.*\])?\s*$", raw_line)
        if match:
            networks.append(
                {
                    "id": match.group(1).strip(),
                    "ssid": match.group(2).strip(),
                    "bssid": match.group(3).strip(),
                    "flags": (match.group(4) or "").strip(),
                }
            )
    return networks


def is_probably_invalid_ssid(value):
    ssid = (value or "").strip()
    if not ssid:
        return True
    lowered = ssid.lower()
    if lowered in {"any", "ssid", "network", "interface", "wlan0", "network id"}:
        return True
    if lowered.startswith("selected interface"):
        return True
    return False
