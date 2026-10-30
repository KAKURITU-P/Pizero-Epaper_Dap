#!/usr/bin/env bash
set -Eeuo pipefail

# Idempotent first-time setup for the supported Raspberry Pi OS installation.
# Run as user pi from /home/pi/dap; this script invokes sudo only when needed.

readonly DAP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DAP_USER="$(id -un)"
readonly EPD_REPO="https://github.com/waveshare/e-Paper.git"

die() {
    printf 'セットアップエラー: %s\n' "$*" >&2
    exit 1
}

if [[ "${EUID}" -eq 0 ]]; then
    die "rootではなく pi ユーザーで実行してください: cd /home/pi/dap && bash setup.sh"
fi
[[ "$DAP_USER" == "pi" ]] || die "このアプリは /home/pi/dap と pi ユーザーを前提としています (現在: ${DAP_USER})"
[[ "$DAP_DIR" == "/home/pi/dap" ]] || die "リポジトリを /home/pi/dap に配置してください (現在: ${DAP_DIR})"
command -v sudo >/dev/null 2>&1 || die "sudo が見つかりません"
command -v git >/dev/null 2>&1 || die "git が必要です。先に sudo apt install git を実行してください"

sudo -v

echo "=== Raspberry Pi OS パッケージをインストール ==="
sudo apt-get update
sudo apt-get install -y \
    bluetooth \
    bluez \
    bluez-alsa-utils \
    dnsmasq \
    fonts-takao-gothic \
    hostapd \
    i2c-tools \
    libasound2-plugin-equal \
    network-manager \
    python3-gpiozero \
    python3-mutagen \
    python3-numpy \
    python3-pil \
    python3-pygame \
    python3-rpi.gpio \
    python3-smbus2 \
    python3-spidev \
    rfkill
if getent group i2c >/dev/null; then
    sudo usermod -aG i2c "$DAP_USER"
fi

echo "=== Waveshare 2.13-inch V4 ドライバーを準備 ==="
EPD_DIR="$DAP_DIR/waveshare_epd"
EPD_CONFIG="$EPD_DIR/epdconfig.py"
PATCHER="$DAP_DIR/tools/patch_waveshare_epd_gpio18_pwr.py"
if [[ ! -f "$EPD_DIR/epd2in13_V4.py" ]]; then
    if [[ -e "$EPD_DIR" ]]; then
        die "$EPD_DIR はありますが必要な epd2in13_V4.py がありません。内容を退避または削除してから再実行してください"
    fi
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf -- "$tmp_dir"' EXIT
    git clone --depth 1 --filter=blob:none --sparse "$EPD_REPO" "$tmp_dir/e-Paper"
    git -C "$tmp_dir/e-Paper" sparse-checkout set RaspberryPi_JetsonNano/python/lib/waveshare_epd
    cp -a "$tmp_dir/e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd" "$EPD_DIR"
    rm -rf -- "$tmp_dir"
    trap - EXIT
else
    echo "既存の Waveshare ドライバーを使用します。"
fi
[[ -f "$EPD_CONFIG" ]] || die "Waveshare epdconfig.py が見つかりません: $EPD_CONFIG"
python3 "$PATCHER" "$EPD_CONFIG"

echo "=== 音楽フォルダーと AP 設定を確認 ==="
mkdir -p "$HOME/music"
if [[ ! -f "$DAP_DIR/ap_config.json" ]]; then
    cat >"$DAP_DIR/ap_config.json" <<'EOF'
{
  "ssid": "PiZero-DAP",
  "password": "dap-connect-1234",
  "country": "JP",
  "channel": 6
}
EOF
fi
chmod 600 "$DAP_DIR/ap_config.json"
echo "AP設定: $DAP_DIR/ap_config.json (初期パスワードは必ず変更してください)"

echo "=== DAP サービス用 sudo 権限を設定 ==="
sudo bash "$DAP_DIR/tools/configure_dap_sudoers.sh"

echo "=== systemd サービスを登録 ==="
sudo install -o root -g root -m 0644 "$DAP_DIR/dap.service" /etc/systemd/system/dap.service
sudo install -o root -g root -m 0644 "$DAP_DIR/battery-monitor.service" /etc/systemd/system/battery-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable dap.service
sudo systemctl enable --now battery-monitor.service

echo
echo "セットアップが完了しました。"
echo "SPIとI2Cを有効にしていない場合は sudo raspi-config の Interface Options で有効にし、再起動してください。"
echo "バッテリー監視は I2C bus 3 と MCP3425 (0x68) を使用します。配線とバス番号を確認してください。"
echo "APを使う前に ap_config.json の初期パスワードを変更してください。"
echo "DAPを起動するには: sudo systemctl start dap.service"
echo "AAC対応BlueALSAは任意です。手順は docs/BLUEALSA_AAC.md を参照してください。"
