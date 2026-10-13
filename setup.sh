#!/bin/bash
set -e

echo "=== 1. システムパッケージのアップデート & インストール ==="
sudo apt update
sudo apt install -y \
    python3-pip \
    python3-pygame \
    python3-pil \
    python3-gpiozero \
    python3-spidev \
    python3-rpi.gpio \
    bluetooth \
    bluealsa \
    bluez-tools \
    rfkill \
    hostapd \
    dnsmasq \
    network-manager \
    fonts-takao-gothic \
    alsa-utils \
    libasound2-dev \
    i2c-tools

echo "=== 2. Pythonライブラリのインストール ==="
pip3 install mutagen || pip3 install mutagen --break-system-packages

echo "=== 3. 音楽ディレクトリの作成 ==="
mkdir -p ~/music

echo "=== 4. Waveshare EPD のGPIO18電源操作を無効化 ==="
EPD_CONFIG="/home/pi/dap/waveshare_epd/epdconfig.py"
PATCHER="/home/pi/dap/tools/patch_waveshare_epd_gpio18_pwr.py"
if [ -f "$EPD_CONFIG" ] && [ -f "$PATCHER" ]; then
    python3 "$PATCHER" "$EPD_CONFIG"
else
    echo "Waveshareドライバー未配置のためスキップしました。配置後にPATCHERを実行してください。"
fi

echo "=== 5. DAPサービス用の制限付きsudo設定 ==="
SUDOERS_SETUP="/home/pi/dap/tools/configure_dap_sudoers.sh"
if [ -f "$SUDOERS_SETUP" ]; then
    sudo bash "$SUDOERS_SETUP"
else
    echo "sudoers設定スクリプトが見つかりません: $SUDOERS_SETUP"
    exit 1
fi

echo "=== 6. systemd サービス設定 ==="
if [ -f /home/pi/dap/dap.service ]; then
    sudo cp /home/pi/dap/dap.service /etc/systemd/system/dap.service
    sudo systemctl daemon-reload
    sudo systemctl enable dap.service
    echo "dap.service を登録・有効化しました。"
fi

echo "=== セットアップが完了しました！ ==="
echo "※ Raspberry Pi 設定で SPI が有効（sudo raspi-config -> Interface Options -> SPI）になっていることを確認してください。"
