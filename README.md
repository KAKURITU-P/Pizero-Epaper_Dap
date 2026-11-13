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
  - **Bluetooth 音声出力:** `bluetoothctl` による周辺機器の検索、選択した機器のペアリング・接続に対応
  - UI 上での IP アドレス確認、曲ライブラリの再読み込み、再起動、シャットダウン、解放済み隠し機能の削除操作に対応

---

## ハードウェア構成(制作者の環境)

| パーツ | 型番 / 仕様 |
| :--- | :--- |
| **SBC** | Raspberry Pi Zero WH |
| **OS** | Raspberry Pi OS (Trixie) |
| **ディスプレイ** | Waveshare 2.13inch e-Paper HAT (V4) (250×122 resolution) |
| **ボタン** | タクトスイッチ × 6 (GPIO 接続) |
| **オーディオ出力** | ALSA アナログ出力 / USB OTG AUDIO / Bluetooth A2DP オーディオ |

「接続設定」→「Bluetooth設定」→「出力先」で `PWM` → `USBOTG` → `BT` の順に切り替えます。USB OTG AUDIO は既定で ALSA の `plughw:1,0` を使用し、カード番号が異なる場合は `DAP_USB_OTG_ALSA_DEVICE` 環境変数で変更できます。

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
├── battery_utils.py    # バッテリー状態ファイルの読み取り
├── audio_device_utils.py # USBオーディオデバイス検出・サンプルレート選択
├── font_utils.py       # フォント検出と読み込み
├── icon_utils.py       # スプライトシートからのアイコン・船画像の切り出し
├── render_utils.py     # 表示用テキスト整形(幅合わせ省略・時間・サイズ)
├── version_utils.py    # gitタグから表示用バージョンを取得
├── bluealsa_utils.py   # BlueALSA / ALSA 設定生成ユーティリティ
├── web_admin_server.py # Web管理画面 API
├── web_admin/          # Web管理画面
├── setup.sh            # 初回セットアップ（依存関係・ドライバー・systemd）
├── ap_config.json      # AP の SSID / パスワード / 国コード / チャンネル
├── tools/              # セットアップ・保守用スクリプト
├── docs/               # Bluetooth AAC 等の追加手順
├── waveshare_epd/      # セットアップ時に取得する Waveshare ドライバー
├── tests/              # ユーティリティの単体テスト
├── stereo_test.py      # L/R ステレオ位相・チャンネル確認用ツール
├── gpio_test.py        # GPIO ボタン入力確認用テストスクリプト
├── gpio_check.py       # GPIO ピン状態チェックツール
├── .gitignore          # 音楽ファイル等の除外設定
└── README.md           # 本ドキュメント

```

---

## セットアップ手順

対応環境は Raspberry Pi Zero WH と Raspberry Pi OS (Trixie) です。アプリは `/home/pi/dap`、音楽は `/home/pi/music`、実行ユーザーは `pi` を前提とします。

リポジトリを所定の場所に取得して、セットアップツールを実行します。

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/KAKURITU-P/Pizero-Epaper_Dap.git /home/pi/dap
cd /home/pi/dap
bash setup.sh
```

`setup.sh` は依存パッケージを APT から導入し、Waveshare 2.13-inch V4 ドライバーを取得して GPIO18 電源制御パッチを適用し、制限付き sudo 設定、DAP／バッテリー／Web管理の systemd サービス、Nginxを登録します。再実行しても、既存のドライバーと AP 設定、管理画面パスワードを保持します。Pythonパッケージを `pip` でシステムに直接導入しません。

SPIとI2Cを有効化し、音楽を配置して起動します。SPI/I2Cをまだ有効にしていない場合は `sudo raspi-config` → `Interface Options` で有効にしてから再起動してください。

```bash
sudo raspi-config
sudo reboot
```

再起動後、音楽ファイルを `/home/pi/music` にコピーし、サービスを確認します。

```bash
sudo systemctl status dap.service --no-pager
sudo systemctl start dap.service
sudo systemctl status battery-monitor.service --no-pager
```

セットアップでは `battery-monitor.service` も自動起動に登録します。バッテリー残量表示と低電圧時の安全停止には、I2C bus 3 上の MCP3425 (アドレス `0x68`) と既定の分圧回路が必要です。I2Cを有効にしてから再起動してください。未接続・読み取り失敗時は残量表示が `--%` になります。

APを使う前に `/home/pi/dap/ap_config.json` の初期パスワードを変更してください。Bluetoothの標準セットアップは SBC 対応です。AAC が必要な場合は [SBC / AAC 対応 BlueALSA](docs/BLUEALSA_AAC.md) を参照してください。AACビルドは任意で、Pi上で時間がかかります。

### Web管理画面

`setup.sh` は、Wi-Fi接続先の追加、AP設定の編集と接続用QRコード表示、バッテリー・音楽ストレージ・Bluetooth状態の確認、お気に入り曲の登録・解除を行うWeb管理画面も設定します。セットアップ完了時に初期パスワードが一度だけ表示されます。ユーザー名は `pi` です。パスワードを再設定する場合:

```bash
sudo htpasswd -m /etc/nginx/dap-admin.htpasswd pi
```

Wi-Fi検索と接続は、セットアップで root 所有として配置する `/usr/local/sbin/dap-web-wifi` ヘルパー経由で実行します。sudoers ではこの専用ヘルパーだけを追加許可し、Web管理サービスにNetworkManagerの一般的な管理権限は付与しません。

ブラウザーで `http://<PiのIPアドレス>/` を開きます。管理画面はHTTPで提供するため、インターネットへ公開せず、DAPに接続できる信頼したLANまたはAP内で使用してください。

音楽ファイルのアップロードと削除はDUFSを使います。DUFSがインストール済みなら、`/home/pi/music`を配信し、ローカルだけで待ち受けるよう起動オプションを設定してください。管理画面の「DUFSを開く」は`/files/`へ別タブで接続します。お気に入り曲の登録・解除は管理画面から行えます。

```bash
dufs --bind 127.0.0.1 --port 8000 --path-prefix /files --allow-upload --allow-delete /home/pi/music
```

既存のDUFS systemdサービスを使っている場合は、サービスの起動引数を上記の内容に合わせてください。`--bind 127.0.0.1`でポート8000をLANへ直接公開せず、Nginxの認証を通して利用できます。追加した音楽を本体の一覧に反映するには、本体メニューの「曲ライブラリ再読み込み」を実行してください。

---


## ライセンス

本プロジェクトのソースコードは [MIT License](https://github.com/KAKURITU-P/Pizero-Epaper_Dap/blob/main/LICENSE.md) のもとで公開されています。


### GPIO18 とPWM音声を併用する場合

GPIO18/19をPWM音声出力に割り当てる構成では、WaveshareドライバーがGPIO18をPWR制御に使わないようにします。`setup.sh` はドライバーを取得し、`tools/patch_waveshare_epd_gpio18_pwr.py` を適用します。ドライバーを手動で更新した場合は、必要に応じて次を再実行してください。

```bash
python3 /home/pi/dap/tools/patch_waveshare_epd_gpio18_pwr.py /home/pi/dap/waveshare_epd/epdconfig.py
```

この設定は、画面基板がGPIO18のPWR制御なしで動作することを前提とします。

### Bluetoothイヤホンの登録と接続

Wi-Fi と Bluetooth は独立して ON/OFF できます。同時に使う場合、2.4 GHz 帯の混雑状況によって Wi-Fi の速度低下や Bluetooth 音声の途切れが起きることがあります。その場合は Wi-Fi を 5 GHz 帯へ移すか、片方を一時的に OFF にしてください。Bluetooth を OFF にしても Wi-Fi の状態は変わりません。

初めて使うイヤホンは、次の順で登録します。

1. イヤホンをケースから出すなどして、メーカー指定の方法でペアリングモードにします（スマートフォン等に接続済みなら、そちらを切断してください）。
2. DAP の「接続設定」→「Bluetooth設定」→「新規デバイスの検索」→「検索開始」を選びます。検索中は約 8 秒待ちます。
3. 表示されたイヤホンを選びます。登録済み機器は「登録済みデバイス」から選ぶと再ペアリングせずに接続します。
4. 「接続成功」と表示され、イヤホンから音が出ることを確認します。

検索に出ない場合は、イヤホンのペアリングモードをやり直してから再検索してください。PIN 入力やパスキー確認を要求する機器は、本体 UI から入力できないため接続できない場合があります。接続に失敗した場合は「出力先」を PWM に戻してください。Wi-Fi は接続中もそのまま利用できます。

### DAP操作時のsudoパスワード

`setup.sh` は、DAPサービスが使用する無線切替・Bluetooth/BlueALSA制御・アプリ再起動などに限り、パスワードなしでsudoを実行できる設定を `/etc/sudoers.d/dap` に追加します。SSHでの一般的な管理コマンド（パッケージ更新など）には引き続きパスワードが必要です。既存環境は、コード更新後に新しい権限設定を反映するため次を実行してください。

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

Bluetooth音声出力にはBlueALSAを使用します。PWM/BTともALSAの`equal`経路を使用し、後段を48 kHz・ステレオ・S16_LEへ固定します。接続完了後はBluetoothの接続状態だけでなく、対象機器のA2DP用BlueALSA PCMがALSAへ公開されたことを確認してから、`.asoundrc` とPygameの音声出力を切り替えます。

Bluetooth機器の接続は手動操作です。選択したMACアドレスの機器だけ、確認番号（パスキー）の承認を自動化します。PIN入力や未選択機器の承認、切断後のバックグラウンド自動再接続には対応していません。自動承認には `python3-dbus` と `python3-gi` が必要です。

Bluetooth出力と Wi-Fi は同時に利用できます。2.4 GHz 帯の混雑時は音切れが起きる場合があるため、その場合は 5 GHz Wi-Fi を使うか、Wi-Fi を一時的に OFF にしてください。

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
sudo apt install -y bluetooth bluez bluez-alsa-utils alsa-utils libasound2-plugin-equal python3-dbus python3-gi


Raspbian Trixie の標準 BlueALSA は SBC のみです。AAC 対応ビルドの導入と標準版への戻し方は [SBC / AAC 対応 BlueALSA](docs/BLUEALSA_AAC.md) を参照してください。
