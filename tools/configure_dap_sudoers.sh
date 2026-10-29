#!/bin/bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this script with sudo: sudo bash $0" >&2
    exit 1
fi

sudoers_tmp="$(mktemp)"
trap 'rm -f "$sudoers_tmp"' EXIT

cat >"$sudoers_tmp" <<'EOF'
# Allow the DAP service account to run only the privileged commands used by
# dap_daemon.py. Interactive sudo for other commands still requires a password.
Cmnd_Alias DAP_RFKILL = \
    /usr/sbin/rfkill list, \
    /usr/sbin/rfkill block wlan, \
    /usr/sbin/rfkill unblock wlan, \
    /usr/sbin/rfkill block bluetooth, \
    /usr/sbin/rfkill unblock bluetooth

Cmnd_Alias DAP_SYSTEMCTL = \
    /usr/bin/systemctl start bluealsa, \
    /usr/bin/systemctl stop bluealsa, \
    /usr/bin/systemctl restart bluealsa, \
    /usr/bin/systemctl restart dap, \
    /usr/bin/systemctl restart dap.service, \
    /usr/bin/systemctl stop NetworkManager, \
    /usr/bin/systemctl restart NetworkManager, \
    /usr/bin/systemctl restart avahi-daemon

Cmnd_Alias DAP_PROCESS = \
    /usr/bin/pkill -TERM hostapd, \
    /usr/bin/pkill -TERM dnsmasq, \
    /usr/bin/pkill -TERM wpa_supplicant

Cmnd_Alias DAP_NETWORK = \
    /usr/sbin/ip link set wlan0 down, \
    /usr/sbin/ip addr flush dev wlan0, \
    /usr/sbin/ip link set wlan0 up, \
    /usr/sbin/ip addr add 192.168.4.1/24 dev wlan0, \
    /usr/sbin/dnsmasq -C /tmp/dnsmasq_ap.conf, \
    /usr/sbin/hostapd -B /tmp/dap_hostapd.conf, \
    /usr/sbin/wpa_cli -i wlan0 list_networks, \
    /usr/sbin/wpa_cli -i wlan0 select_network *

Cmnd_Alias DAP_POWER = \
    /usr/sbin/reboot, \
    /usr/sbin/shutdown -h now

pi ALL=(root) NOPASSWD: DAP_RFKILL, DAP_SYSTEMCTL, DAP_PROCESS, DAP_NETWORK, DAP_POWER
EOF

chown root:root "$sudoers_tmp"
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp"
install -o root -g root -m 0440 "$sudoers_tmp" /etc/sudoers.d/dap
visudo -c

echo "Installed restricted passwordless sudo rules for the DAP service."
