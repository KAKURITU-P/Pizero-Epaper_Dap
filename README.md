# Pizero-Epaper_Dap

Raspberry Pi Zero WH と 2.13 インチ電子ペーパー（Waveshare）を使用した自作ポータブルデジタルオーディオプレーヤーです。

電子ペーパーによる省電力・高コントラストな画面表示を実現しています。

---

## 主な特徴

- **電子ペーパー表示（Waveshare 2.13inch V4）**
  - **PLAY 画面:** 曲名、アーティスト名、音量（%）、再生/一時停止状態、再生時間を表示
  - **プログレスバー:** 8 段階の部分更新（Partial Refresh）によるスムーズなプログレス表示
  - **残像対策:** メニュー操作や画面切り替え時は全画面リフレッシュ（Full Refresh）を実行
  - **テキスト自動調整:** 画面幅（230px）を超える長文の曲名・アーティスト名は自動的に末尾を `…` に省記
- **操作性 & 再生機能**
  - 6ボタンによる操作（上 / 下 / メニュー・決定 / 戻る・再生一時停止 / 前曲 / 次曲）
  - **スマート曲戻し:** 再生開始から 3 秒以上経過時は曲の頭出し（00:00）、3 秒未満または停止中は前の曲へ移動
  - mutagen による MP3 / FLAC / WAV 等の ID3 タグ（アーティスト名）自動解析
  - シャッフル再生機能対応
  - メニュー画面で 10 秒間無操作の場合、PLAY 画面へ自動復帰
- **システム・省電力・通信管理**
  - **rfkill 制御:** UI 上から Wi-Fi および Bluetooth の ON/OFF を個別切り替え可能
  - **Bluetooth 音声出力:** `bluetoothctl` による周辺機器の自動スキャン、ペアリング、自動接続対応
  - UI 上での IP アドレス確認、曲ライブラリの再読み込み、再起動、シャットダウン操作対応

---

## ハードウェア構成

| パーツ | 型番 / 仕様 |
| :--- | :--- |
| **メインボード** | Raspberry Pi Zero WH |
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

## ディレクトリ構成

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
## セットアップ手順



Raspberry Pi OS インストール後、ターミナルで以下のコマンドを実行して必要なパッケージのインストールと環境構築を行います。

### 1. システムパッケージのアップデートと必要依存関係のインストール

```markdown
# パッケージリストの更新とシステムアップデート
sudo apt update && sudo apt upgrade -y

# 必要なパッケージのインストール (Python, MPD, ALSA, Git, 画像処理関連)
sudo apt install -y \
    python3-pip \
    python3-pil \
    python3-spidev \
    python3-rpi.gpio \
    mpd \
    mpc \
    python3-mpd2 \
    alsa-utils \
    alsa-tools \
    libasound2-plugin-equal \
    git

```

### 2. MPD (Music Player Daemon) の基本設定

MPDの出力先や音楽ディレクトリを設定します。

```bash
# MPD設定ファイルの編集
sudo nano /etc/mpd.conf

```

`mpd.conf` 内の楽曲ディレクトリパスやALSA出力設定を確認・変更します。

```text
music_directory    "/var/lib/mpd/music"
playlist_directory "/var/lib/mpd/playlists"

audio_output {
        type            "alsa"
        name            "My ALSA Device"
        device          "hw:0,0"    # ご使用のオーディオデバイスに合わせて変更
        mixer_type      "software"
}

```

設定後、MPDサービスを再起動します。

```bash
sudo systemctl restart mpd

```

### 3. ALSA Equalizer (`alsaequal`) の有効化

`/etc/asound.conf` または `~/.asoundrc` に以下を記述し、イコライザープラグインを有効化します。

```text
pcm.!default {
    type plug
    slave.pcm "equal"
}

pcm.equal {
    type equal
    slave.pcm "plughw:0,0"  # 出力先デバイス
}

ctl.equal {
    type equal
}

```

### 4. リポジトリのクローンと実行

```bash
git clone https://github.com/KAKURITU-P/Pizero-Epaper_Dap
cd dap/

# デーモンの手動実行テスト
python3 dap_daemon.py

```

---
## 使用パーツリスト（参考までに）

### 使用パーツ（一部）

**Waveshare 2.13inch V4**    [Amazon販売ページ](https://amzn.asia/d/094VnMhz)

**レバースイッチDIP化キット TMHU28** [秋月電子通商製品ページ](https://akizukidenshi.com/catalog/g/g113834/)

**3.5mmステレオミニジャックDIP化キット** [秋月電子通商製品ページ](https://akizukidenshi.com/catalog/g/g105363/)

**アルミ電解コンデンサー100μF16V105℃ ルビコンMH5** [秋月電子通商製品ページ](https://akizukidenshi.com/catalog/g/g105002/)

**カーボン抵抗(炭素皮膜抵抗) 1/4W10kΩ** [秋月電子通商製品ページ](https://akizukidenshi.com/catalog/g/g125103/)

**半固定ボリューム GF063P 500Ω** [秋月電子通商製品ページ](https://akizukidenshi.com/catalog/g/g114900/)

### Q.もっと載せろや！--->A.そのうち更新するわー（）

---
## ギャラリー

### 再生画面（曲の途中）
<img width="1156" height="867" alt="Image" src="https://github.com/user-attachments/assets/ab9f442b-d786-4c72-b20a-8b44a93dedb1" />

### 再生画面（再生し始め）
<img width="1156" height="867" alt="Image" src="https://github.com/user-attachments/assets/5c6b1459-9753-4b52-ba8f-ce7409982444" />

### メニュー画面（TOP）
<img width="1156" height="867" alt="Image" src="https://github.com/user-attachments/assets/a74f7b07-3e58-4b7e-8982-3b9551c0fe24" />

### メニュー画面（アルバム）
<img width="1156" height="867" alt="Image" src="https://github.com/user-attachments/assets/eb3aea0e-858c-4f11-8b25-180a3ad24266" />

### メニュー画面（接続設定）
<img width="1156" height="867" alt="Image" src="https://github.com/user-attachments/assets/98ddfefd-175f-4e34-a417-001f32475309" />

## このソフトウェアは[MITライセンス](https://github.com/KAKURITU-P/Pizero-Epaper_Dap/tree/main?tab=MIT-1-ov-file)でライセンスされています
