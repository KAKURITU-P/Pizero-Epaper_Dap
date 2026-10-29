#!/usr/bin/env bash
set -euo pipefail

# Build AAC-enabled BlueALSA in an isolated prefix. The distro package remains.
PREFIX="/opt/dap-bluealsa-aac"
BLUEALSA_VERSION="v4.3.1"
FDK_AAC_VERSION="v2.0.3"
OVERRIDE="/etc/systemd/system/bluealsa.service.d/10-dap-aac.conf"
BUILD_DIR="$HOME/.cache/dap-bluealsa-aac-build"
OVERRIDE_INSTALLED=0
SUDO_KEEPALIVE_PID=""
BUILD_SUCCEEDED=0
LOG_FILE="$HOME/bluealsa-aac-install.log"

exec > >(tee -a "$LOG_FILE") 2>&1
echo "Build log: $LOG_FILE"

cleanup() {
    if [[ -n "$SUDO_KEEPALIVE_PID" ]]; then
        kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
    fi
    if [[ "$BUILD_SUCCEEDED" -eq 1 ]]; then
        rm -rf -- "$BUILD_DIR"
    else
        echo "Build workspace retained for retry: $BUILD_DIR"
    fi
}
trap cleanup EXIT
mkdir -p "$BUILD_DIR"

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
if [[ ! -d "$BUILD_DIR/fdk-aac/.git" ]]; then
    rm -rf -- "$BUILD_DIR/fdk-aac"
    git clone --depth 1 --branch "$FDK_AAC_VERSION" \
        https://github.com/mstorsjo/fdk-aac.git "$BUILD_DIR/fdk-aac"
else
    echo "Reusing cached FDK-AAC source and objects."
fi
(
    cd "$BUILD_DIR/fdk-aac"
    autoreconf -fiv
    ./configure --prefix="$PREFIX" --disable-static --enable-shared
    make -j1
    sudo make install
)

echo "Building BlueALSA ${BLUEALSA_VERSION} with SBC and AAC..."
if [[ ! -d "$BUILD_DIR/bluez-alsa/.git" ]]; then
    rm -rf -- "$BUILD_DIR/bluez-alsa"
    git clone --depth 1 --branch "$BLUEALSA_VERSION" \
        https://github.com/Arkq/bluez-alsa.git "$BUILD_DIR/bluez-alsa"
else
    echo "Reusing cached BlueALSA source and objects."
fi
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
EFFECTIVE_EXEC_START="$(sudo systemctl show bluealsa -p ExecStart --value)"
if [[ "$EFFECTIVE_EXEC_START" != *"$PREFIX/bin/bluealsa"* ]]; then
    echo "Another systemd drop-in overrides the AAC BlueALSA executable:"
    echo "$EFFECTIVE_EXEC_START"
    echo "Inspect 'systemctl cat bluealsa' and disable the conflicting ExecStart drop-in."
    rollback_override
    exit 1
fi

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

echo "BlueALSA is active. Checking that AAC is advertised..."
BLUEALSA_STATUS="$(bluealsa-cli status)"
printf '%s\n' "$BLUEALSA_STATUS"
if ! grep -Eq 'A2DP-source[[:space:]]*:[[:space:]].*AAC' <<<"$BLUEALSA_STATUS"; then
    echo "AAC is not advertised; restoring the previous BlueALSA service."
    rollback_override
    exit 1
fi
echo "To restore the distro version: sudo bash /home/pi/dap/tools/remove_bluealsa_aac.sh"
BUILD_SUCCEEDED=1
