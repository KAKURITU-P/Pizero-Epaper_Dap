#!/usr/bin/env bash
set -euo pipefail

OVERRIDE="/etc/systemd/system/bluealsa.service.d/10-dap-aac.conf"
PREFIX="/opt/dap-bluealsa-aac"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run with sudo: sudo bash $0"
    exit 1
fi

if [[ -f "$OVERRIDE" ]]; then
    rm -f -- "$OVERRIDE"
    rmdir --ignore-fail-on-non-empty "$(dirname "$OVERRIDE")"
    systemctl daemon-reload
    systemctl restart bluealsa
fi

rm -rf -- "$PREFIX"
echo "Restored the packaged BlueALSA service and removed the AAC build."
