#!/usr/bin/env bash
set -euo pipefail

# Build AAC-enabled BlueALSA in an isolated prefix. The distro package remains.
PREFIX="/opt/dap-bluealsa-aac"
BLUEALSA_VERSION="v4.3.1"
FDK_AAC_VERSION="v2.0.3"
OVERRIDE="/etc/systemd/system/bluealsa.service.d/10-dap-aac.conf"
BUILD_DIR="$(mktemp -d /var/tmp/dap-bluealsa-aac.XXXXXX)"
OVERRIDE_INSTALLED=0
SUDO_KEEPALIVE_PID=""
LOG_FILE="$HOME/bluealsa-aac-install.log"

exec > >(tee -a "$LOG_FILE") 2>&1
echo "Build log: $LOG_FILE"

cleanup() {
    if [[ -n "$SUDO_KEEPALIVE_PID" ]]; then
        kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
    fi
    rm -rf -- "$BUILD_DIR"
}
trap cleanup EXIT

if [[ "${EUID}" -eq 0 ]]; then
    echo "Run as the normal DAP user; this script uses sudo for system installation."
    exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required."
    exit 1
fi
if [[ -e "$OVERRIDE" ]]; then
    echo "Refusing to overwrite existing systemd drop-in: $OVERRIDE"
    exit 1
fi

sudo -v
(
    while kill -0 "$$" 2>/dev/null; do
        sudo -n -v || exit
        sleep 60
    done
) &
SUDO_KEEPALIVE_PID=$!

sudo apt-get update
sudo apt-get install -y \
    autoconf automake build-essential git libasound2-dev libbluetooth-dev \
    libbsd-dev libdbus-1-dev libglib2.0-dev libtool libudev-dev libsbc-dev \
    pkg-config

echo "Building FDK-AAC ${FDK_AAC_VERSION}..."
git clone --depth 1 --branch "$FDK_AAC_VERSION" \
    https://github.com/mstorsjo/fdk-aac.git "$BUILD_DIR/fdk-aac"
(
    cd "$BUILD_DIR/fdk-aac"
    autoreconf -fiv
    ./configure --prefix="$PREFIX" --disable-static --enable-shared
    make -j1
    sudo make install
)

echo "Building BlueALSA ${BLUEALSA_VERSION} with SBC and AAC..."
git clone --depth 1 --branch "$BLUEALSA_VERSION" \
    https://github.com/Arkq/bluez-alsa.git "$BUILD_DIR/bluez-alsa"
(
    cd "$BUILD_DIR/bluez-alsa"
    autoreconf --install --force
    PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
        LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        ./configure --prefix="$PREFIX" --enable-aac --disable-manpages
    make -j1
    sudo make install
)

sudo install -d -m 0755 "$(dirname "$OVERRIDE")"
sudo tee "$OVERRIDE" >/dev/null <<EOF
[Service]
ExecStart=
ExecStart=$PREFIX/bin/bluealsa -S -p a2dp-source -p a2dp-sink
Environment=LD_LIBRARY_PATH=$PREFIX/lib
EOF
OVERRIDE_INSTALLED=1

rollback_override() {
    if [[ "$OVERRIDE_INSTALLED" -eq 1 ]]; then
        sudo rm -f -- "$OVERRIDE"
        sudo systemctl daemon-reload
        sudo systemctl restart bluealsa || true
        OVERRIDE_INSTALLED=0
    fi
}

sudo systemctl daemon-reload
if ! sudo systemctl restart bluealsa; then
    echo "AAC-enabled BlueALSA failed to restart; restoring the distro service."
    rollback_override
    exit 1
fi
sleep 2
if ! sudo systemctl is-active --quiet bluealsa; then
    echo "AAC-enabled BlueALSA is not active; restoring the distro service."
    sudo journalctl -u bluealsa -n 40 --no-pager || true
    rollback_override
    exit 1
fi

echo "BlueALSA is active. Report the output of: bluealsa-cli status"
bluealsa-cli status
echo "To restore the distro version: sudo bash /home/pi/dap/tools/remove_bluealsa_aac.sh"
