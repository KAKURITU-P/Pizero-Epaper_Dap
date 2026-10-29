# Pizero-Epaper_Dap

Raspberry Pi Zero WH と 2.13 インチ電子ペーパー（Waveshare）を使用した自作ポータブルデジタルオーディオプレーヤー（DAP）。

電子ペーパーによる省電力・高コントラストな画面表示と、GPIO 直結物理ボタンによるスムーズな操作性を実現しています。

---
## 最新の更新／修正／機能追加などは[最新のリリース](https://github.com/KAKURITU-P/Pizero-Epaper_Dap/releases/)を確認してください

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
  - 隠し機能（時計画面・EQ）の解放状態とEQ調整値を `settings.json` に保存し、再起動後に復元
  - アルバム内のお気に入り直下に再生履歴を表示（最近再生した最大 30 曲、`play_data.json` に保存）
  - 自動スリープ機能（メニュー画面で 10 秒間無操作の場合、PLAY 画面へ自動復帰）
- **システム・省電力・通信管理**
  - **rfkill 制御:** UI 上から Wi-Fi および Bluetooth の ON/OFF を個別切り替え可能
  - **Bluetooth 音声出力:** `bluetoothctl` による周辺機器の自動スキャン、ペアリング、自動接続対応
  - UI 上での IP アドレス確認、曲ライブラリの再読み込み、再起動、シャットダウン、解放済み隠し機能の削除操作に対応

---

## ハードウェア構成(制作者の環境)

| パーツ | 型番 / 仕様 |
| :--- | :--- |
| **SBC** | Raspberry Pi Zero WH |
| **OS** | Raspberry Pi OS (Trixie) |
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

### 1. 依存ライブラリのインストール

```bash
sudo apt update
sudo apt install -y python3-pip python3-pygame python3-pil python3-gpiozero python3-mutagen bluetooth bluez bluez-alsa-utils libasound2-plugin-equal

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

本プロジェクトのソースコードは [MIT License](https://github.com/KAKURITU-P/Pizero-Epaper_Dap/blob/main/LICENSE.md) のもとで公開されています。


### GPIO18 とPWM音声を併用する場合

GPIO18/19をPWM音声出力に割り当てる構成では、WaveshareドライバーがGPIO18をPWR制御に使わないようにします。`setup.sh` は `/home/pi/dap/waveshare_epd/epdconfig.py` が存在する場合に、追跡対象の `tools/patch_waveshare_epd_gpio18_pwr.py` を適用します。ドライバーを後から配置した場合は次を実行してください。

```bash
python3 /home/pi/dap/tools/patch_waveshare_epd_gpio18_pwr.py /home/pi/dap/waveshare_epd/epdconfig.py
```

この設定は、画面基板がGPIO18のPWR制御なしで動作することを前提とします。

### Bluetoothイヤホンの登録と接続

Bluetooth と Wi-Fi は同時に有効にしない運用です。「Bluetooth機能」を ON にすると Wi-Fi が自動で OFF になります。Wi-Fi 経由の SSH 接続も切れるため、以降は本体のボタンで操作してください。Bluetooth を OFF に戻すと、Bluetooth を ON にする前の Wi-Fi 状態に戻ります。

初めて使うイヤホンは、次の順で登録します。

1. イヤホンをケースから出すなどして、メーカー指定の方法でペアリングモードにします（スマートフォン等に接続済みなら、そちらを切断してください）。
2. DAP の「接続設定」→「Bluetooth設定」→「新規デバイスの検索」→「検索開始」を選びます。検索中は約 8 秒待ちます。
3. 表示されたイヤホンを選びます。登録済み機器は「登録済みデバイス」から選ぶと再ペアリングせずに接続します。
4. 「接続成功」と表示され、イヤホンから音が出ることを確認します。

検索に出ない場合は、イヤホンのペアリングモードをやり直してから再検索してください。PIN 入力やパスキー確認を要求する機器は、本体 UI から入力できないため接続できない場合があります。接続に失敗した場合は「出力先」を PWM に戻してください。接続前から Wi-Fi が OFF なら OFF のままです。

### DAP操作時のsudoパスワード

`setup.sh` は、DAPサービスが使用する無線切替・BlueALSA制御・アプリ再起動などに限り、パスワードなしでsudoを実行できる設定を `/etc/sudoers.d/dap` に追加します。SSHでの一般的な管理コマンド（パッケージ更新など）には引き続きパスワードが必要です。既存環境では次を一度実行してください。

```bash
sudo bash /home/pi/dap/tools/configure_dap_sudoers.sh
```

GitHub更新では、更新処理の前に画面の全画面更新を要求します。全画面更新時にドライバーがパネルのRSTを動作させてからBUSY状態を確認します。BUSYは入力信号のため、ソフトウェアから直接解除はできません。配線や接触不良がある場合は、接続の確認も必要です。

### AP モード

「接続設定」→「APモード: OFF」を選ぶと、DAP が `PiZero-DAP` という WPA2 アクセスポイントを起動します。スマートフォンや PC から接続し、ネットワーク設定や対応するローカル機能には `192.168.4.1` を使用してください。AP モードはインターネット接続を提供しません。AP を OFF にすると NetworkManager を再起動し、AP 起動前の Wi-Fi の ON/OFF 状態に戻します。

SSID、パスワード、国コード、チャンネルは、DAP と同じディレクトリにある `ap_config.json` で設定します。初期値は `PiZero-DAP` / `dap-connect-1234` / `JP` / チャンネル `6` です。運用前にパスワードを変更してください。パスワードは印字可能な ASCII 文字で 8〜63 文字、SSID は UTF-8 で 1〜32 バイトにしてください。設定変更後に AP を再起動すると反映されます。

```json
{
  "ssid": "PiZero-DAP",
  "password": "dap-connect-1234",
  "country": "JP",
  "channel": 6
}
```

### BlueALSA手動接続の確認

Bluetooth音声出力にはBlueALSAを使用します。接続完了後はBluetoothの接続状態だけでなく、対象機器のA2DP用BlueALSA PCMがALSAへ公開されたことを確認してから、`.asoundrc` とPygameの音声出力を切り替えます。

Bluetooth機器の接続は手動操作です。起動時の自動接続、切断後のバックグラウンド自動再接続、PIN入力には対応していません。

Bluetooth出力へ切り替える場合は、Wi-Fiを先にOFFにしてください。本プロジェクトでは、BluetoothとWi-Fiを同時に有効化しない既存方針を維持しています。

実機では、次のコマンドでBlueALSAの状態を確認できます。

```bash
sudo systemctl is-active bluealsa
bluetoothctl info AA:BB:CC:DD:EE:FF
aplay -L | grep -i bluealsa
```

`AA:BB:CC:DD:EE:FF` は実際のBluetoothイヤホンのMACアドレスに置き換えてください。

必要なパッケージは次のとおりです。

```bash
sudo apt update
sudo apt install -y bluetooth bluez bluez-alsa-utils alsa-utils libasound2-plugin-equal
