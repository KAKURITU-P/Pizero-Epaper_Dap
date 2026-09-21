# Pizero-Epaper_Dap

Raspberry Pi Zero WH と 2.13 インチ電子ペーパー（Waveshare）を使用した自作ポータブルデジタルオーディオプレーヤー（DAP）。

電子ペーパーによる省電力・高コントラストな画面表示と、GPIO 直結物理ボタンによるスムーズな操作性を実現しています。

---

## 主な特徴

- **電子ペーパー表示（Waveshare 2.13inch V4）**
  - **PLAY 画面:** 曲名、アーティスト名、音量（%）、再生/一時停止状態、再生時間を表示
  - **プログレスバー:** 8 段階の部分更新（Partial Refresh）によるスムーズなプログレス表示
  - **残像対策:** メニュー操作や画面切り替え時は全画面リフレッシュ（Full Refresh）を実行
  - **テキスト自動調整:** 画面幅（230px）を超える長文の曲名・アーティスト名は自動的に末尾を `…` に省記
- **操作性 & 再生機能**
  - GPIO 直結 6 ボタン操作（上 / 下 / メニュー・決定 / 戻る・再生一時停止 / 前曲 / 次曲）
  - **スマート曲戻し:** 再生開始から 3 秒以上経過時は曲の頭出し（00:00）、3 秒未満または停止中は前の曲へ移動
  - mutagen による MP3 / FLAC / WAV 等の ID3 タグ（アーティスト名）自動解析
  - シャッフル再生機能対応
  - 自動スリープ機能（メニュー画面で 10 秒間無操作の場合、PLAY 画面へ自動復帰）
- **システム・省電力・通信管理**
  - **rfkill 制御:** UI 上から Wi-Fi および Bluetooth の ON/OFF を個別切り替え可能
  - **Bluetooth 音声出力:** `bluetoothctl` による周辺機器の自動スキャン、ペアリング、自動接続対応
  - UI 上での IP アドレス確認、曲ライブラリの再読み込み、再起動、シャットダウン操作対応

---

## ハードウェア構成

| パーツ | 型番 / 仕様 |
| :--- | :--- |
| **SBC** | Raspberry Pi Zero WH |
| **ディスプレイ** | Waveshare 2.13inch e-Paper HAT (V4) (250×122 resolution) |
| **ボタン** | タクトスイッチ × 6 (GPIO 接続) |
| **オーディオ出力** | ALSA アナログ出力 / Bluetooth A2DP オーディオ |

### GPIO ピン割り当て

| ピン (BCM) | 機能 |
| :--- | :--- |
| **GPIO 22** | 音量 UP / メニューカーソル上 |
| **GPIO 27** | 音量 DOWN / メニューカーソル下 |
| **GPIO 5** | メニュー開く / 決定 |
| **GPIO 20** | 再生・一時停止 / 戻る |
| **GPIO 12** | 前の曲 (スマート曲戻し) |
| **GPIO 16** | 次の曲 |

---

## 📂 ディレクトリ構成

```text
.
├── dap_daemon.py       # DAP メイン制御デーモン (Pygame / GPIO / EPD)
├── waveshare_epd/      # 電子ペーパー用ドライバライブラリ
├── stereo_test.py      # L/R ステレオ位相・チャンネル確認用ツール
├── gpio_test.py        # GPIO ボタン入力確認用テストスクリプト
├── gpio_check.py       # GPIO ピン状態チェックツール
├── .gitignore          # 音楽ファイル等の除外設定
└── README.md           # 本ドキュメント

```

---

## 🛠️ セットアップ手順

### 1. 依存ライブラリのインストール

```bash
sudo apt update
sudo apt install -y python3-pip python3-pygame python3-pil python3-gpiozero python3-mutagen bluetooth bluez

```

### 2. ライブラリ・音楽フォルダの準備

音楽ファイルは `~/music` ディレクトリ配下に配置します（サブフォルダ構造にも対応）。

```bash
mkdir -p ~/music

```

### 3. systemd による自動起動設定 (任意)

`/etc/systemd/system/dap.service` を作成してデーモン化することで、ラズパイ起動時に自動で DAP が立ち上がります。

```ini
[Unit]
Description=e-Paper DAP Daemon
After=sound.target network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/dap/dap_daemon.py
WorkingDirectory=/home/pi/dap
StandardOutput=inherit
StandardError=inherit
Restart=always
User=pi

[Install]
WantedBy=multi-user.target

```

サービスの有効化と起動：

```bash
sudo systemctl daemon-reload
sudo systemctl enable dap.service
sudo systemctl start dap.service

```

---

## ライセンス

本プロジェクトのソースコードは [MIT License](https://www.google.com/search?q=LICENSE&utm_source=gemini) のもとで公開されています。

