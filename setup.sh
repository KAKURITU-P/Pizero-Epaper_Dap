#!/bin/bash
set -e

echo "=== 1. システムパッケージのアップデート & インストール ==="
sudo apt update
sudo apt install -y \
    python3-pip \
    python3-pygame \
    python3-pil \
    python3-gpiozero \
    bluetooth \
    bluealsa \
    bluez-tools \
    rfkill \
    hostapd \
    dnsmasq \
    network-manager \
    fonts-takao-gothic \
    alsa-utils \
    libasound2-dev

echo "=== 2. Pythonライブラリのインストール ==="
pip3 install mutagen waveshare-epd || pip3 install mutagen --break-system-packages

echo "=== 3. 音楽ディレクトリの作成 ==="
mkdir -p ~/music

echo "=== 4. systemd サービス設定 ==="
if [ -f /home/pi/dap/dap.service ]; then
    sudo cp /home/pi/dap/dap.service /etc/systemd/system/dap.service
    sudo systemctl daemon-reload
    sudo systemctl enable dap.service
    echo "dap.service を登録・有効化しました。"
fi

echo "=== セットアップが完了しました！ ==="
echo "手動で再起動、または 'sudo systemctl start dap' でサービスを開始してください。"
