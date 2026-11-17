# Pizero-Epaper_Dap

Raspberry Pi Zero W / 2 W と 2.13 インチ電子ペーパー（Waveshare 2.13inch e-Paper HAT V4）を使用した自作ポータブルデジタルオーディオプレーヤー（DAP）。

電子ペーパーによる省電力・高コントラストな画面と GPIO 直結の物理ボタンで操作する、再生に集中できる小型プレーヤーです。

最新の更新内容は[リリース一覧](https://github.com/KAKURITU-P/Pizero-Epaper_Dap/releases/)を参照してください。

---

## 特徴

### 表示（e-Paper 2.13inch V4）

- **PLAY 画面**：曲名・アーティスト名・音量（%）・再生状態・再生時間・バッテリー残量を表示
- **プログレスバー**：8 段階の部分更新（Partial Refresh）でスムーズに描画
- **残像対策**：メニュー操作や画面切替時は全画面リフレッシュ（Full Refresh）
- **テキスト自動調整**：画面幅を超える長い曲名・アーティスト名は末尾を `…` に省略

### 再生

- MP3 / FLAC / WAV 等に対応（mutagen によるタグ解析）
- シャッフル再生・お気に入り（★マーク。次曲ボタン長押しでトグル）
- **スマート曲戻し**：再生開始から 3 秒以上経過していれば曲の頭出し、3 秒未満なら前の曲へ
- 再生履歴（最大 30 曲）をアルバム内のお気に入りの下に表示
- 無操作 10 秒でメニューから PLAY 画面へ自動復帰

### メニュー構成

| 階層 | 内容 |
| :--- | :--- |
| **お気に入り** | 最上位の先頭に配置。ドラッグ&ドロップで並べ替え（Web 管理画面） |
| **再生設定** | 出力先（PWM / USB OTG / BT）、シャッフル・リピート |
| **接続設定** | Bluetooth設定、APモード、Wi-Fi の ON/OFF |
| **システム設定** | 電源メニュー（再起動・シャットダウン）、曲ライブラリ再読み込み、隠し機能 |

### Bluetooth 音声

- **AVRCP 対応**：BT イヤホン側の再生/一時停止・曲送り・曲戻しボタンをそのまま受付（BlueZ が生成する AVRCP uinput デバイスを監視）
- A2DP 出力は BlueALSA 使用（標準は SBC。[AAC 対応ビルド](docs/BLUEALSA_AAC.md)も選択可）
- ペアリング・接続は本体 UI から操作（検索 → 選択 → 接続）
- BT 再生中はバッファ拡張とコーデックキャッシュで瞬断に耐性を持たせ、イヤホン切断時は自動再接続を試行

### Web 管理画面

- 音楽ライブラリの閲覧・お気に入りの**ドラッグ&ドロップ並べ替え**
- 状態カード（Wi-Fi / Bluetooth / バッテリー / 稼働時間 / SoC 温度・負荷 / メモリ / ストレージ）
- Wi-Fi 接続先の追加・AP 設定（QR コード表示）・音楽ファイルのアップロード（DUFS 経由）
- AP 設定は本体の Git 更新でも初期化されないよう保護

### 電源・省電力

- **rfkill 制御**：Wi-Fi と Bluetooth を UI 上から個別 ON/OFF
- **スリープタイマー**（30 分 / 1 時間 / 2 時間）
- バッテリー残量表示と低電圧時の自動シャットダウン（MCP3425 監視サービス）

---

## ハードウェア構成（制作者の環境）

| パーツ | 型番 / 仕様 |
| :--- | :--- |
| **SBC** | Raspberry Pi Zero W（Zero 2 W でも動作確認済み） |
| **OS** | Raspberry Pi OS (Trixie) |
| **ディスプレイ** | Waveshare 2.13inch e-Paper HAT (V4)（250×122） |
| **ボタン** | タクトスイッチ × 6（GPIO 接続） |
| **バッテリー監視** | MCP3425（I2C bus 3、アドレス 0x68） |
| **オーディオ出力** | ALSA アナログ（PWM）/ USB OTG AUDIO / Bluetooth A2DP |

配線は `docs/回路図.pdf` を参照してください。

### GPIO ピン割り当て

| ピン (BCM) | 機能 |
| :--- | :--- |
| **GPIO 22** | 音量 UP / メニューカーソル上 |
| **GPIO 27** | 音量 DOWN / メニューカーソル下 |
| **GPIO 5** | メニューを開く / 決定 |
| **GPIO 20** | 再生・一時停止 / 階層を戻る |
| **GPIO 12** | 前の曲（スマート曲戻し）/ メニュー画面では階層を戻る |
| **GPIO 16** | 次の曲 / 長押しでお気に入りトグル |

### バッテリー監視の有効化

I2C bus 3 は `raspi-config` では有効にならないため、`/boot/firmware/config.txt` に追加して再起動してください（標準 HAT 配線：MCP3425 は GPIO23/4 接続）。

```
dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=4
```

ハード I2C bus 3 に配線した場合は `dtparam=i2c3=on` で代用できます。未接続・読み取り失敗時は残量表示が `--%` になります。

---

## ディレクトリ構成

```text
.
├── dap_daemon.py         # DAP メイン制御デーモン（Pygame / GPIO / e-Paper / AVRCP）
├── battery_monitor.py    # バッテリー電圧の定期記録サービス
├── battery_utils.py      # バッテリー状態ファイルの読み取り
├── audio_device_utils.py # USB オーディオデバイス検出・サンプルレート選択
├── bluealsa_utils.py     # BlueALSA / ALSA 設定生成ユーティリティ
├── font_utils.py         # フォント検出と読み込み
├── icon_utils.py         # スプライトシートからのアイコン切り出し
├── render_utils.py       # 表示用テキスト整形（幅合わせ省略・時間・サイズ）
├── system_stats_utils.py # SoC 温度・負荷・メモリ等の取得
├── network_parse_utils.py# Wi-Fi / Bluetooth / AP の状態解析
├── version_utils.py      # git タグから表示用バージョンを取得
├── web_admin_server.py   # Web 管理画面 API
├── web_admin/            # Web 管理画面
├── dbus/                 # AVRCP 用 D-Bus ポリシー
├── setup.sh              # 初回セットアップ（依存関係・ドライバー・systemd）
├── ap_config.json.sample # AP の既定設定（実ファイルは setup.sh が生成）
├── tools/                # セットアップ・保守用スクリプト
├── docs/                 # BlueALSA の追加手順・回路図
├── tests/                # ユーティリティの単体テスト
├── stereo_test.py        # L/R ステレオ位相・チャンネル確認用ツール
├── gpio_test.py          # GPIO ボタン入力確認用テストスクリプト
└── gpio_check.py         # GPIO ピン状態チェックツール
```

---

## セットアップ手順

対応環境は Raspberry Pi Zero W / 2 W + Raspberry Pi OS (Trixie-32bit) です。アプリは `/home/pi/dap`、音楽は `/home/pi/music`、実行ユーザーは `pi` を前提とします。

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/KAKURITU-P/Pizero-Epaper_Dap.git /home/pi/dap
cd /home/pi/dap
bash setup.sh
```

`setup.sh` は依存パッケージの導入、Waveshare 2.13inch V4 ドライバーの取得と GPIO18 電源制御パッチの適用、制限付き sudo 設定、DAP／バッテリー監視／Web 管理の systemd サービス、Nginx、AVRCP 用 D-Bus ポリシーを登録します。再実行しても既存のドライバー・AP 設定・管理画面パスワードを保持し、Python パッケージを pip でシステムに直接導入しません。

SPI と I2C をまだ有効にしていない場合は `sudo raspi-config` → `Interface Options` で有効にして再起動します。

```bash
sudo raspi-config
sudo reboot
```

再起動後、音楽ファイルを `/home/pi/music` にコピーし、サービスを確認します。

```bash
sudo systemctl status dap.service --no-pager
sudo systemctl status battery-monitor.service --no-pager
```

AP を使う前に `/home/pi/dap/ap_config.json` の初期パスワードを変更してください。

---

## 使い方

### Bluetooth イヤホン

Wi-Fi と Bluetooth は独立して ON/OFF できます。初めて使うイヤホンは次の順で登録します。

1. イヤホンをメーカー指定の方法でペアリングモードにする（他機器に接続済みなら切断）
2. 「接続設定」→「Bluetooth設定」→「新規デバイスの検索」→「検索開始」（約 8 秒待機）
3. 表示されたイヤホンを選ぶ。登録済み機器は「登録済みデバイス」から再ペアリングなしで接続
4. 「接続成功」を確認

接続後は「再生設定」→「出力先」を BT に切り替えます。以後、**イヤホン側のボタンで再生/一時停止・曲送り・曲戻しが操作できます**（AVRCP 非対応機種ではボタンは使えません）。

PIN 入力やパスキー確認を要求する機器は本体 UI から入力できないため接続できません。接続に失敗した場合は出力先を PWM に戻してください。

> **Wi-Fi と Bluetooth の同時使用について**：2.4 GHz 帯の混雑状況により Wi-Fi の速度低下や BT 音声の途切れが起きることがあります（Pi Zero W の無線チップは Wi-Fi / BT 共存の制約があります）。再生中の大容量 Wi-Fi 通信を避けるか、Wi-Fi を 5 GHz 帯に移すか、一時的に OFF にしてください。

### Web 管理画面

ブラウザーで `http://<PiのIPアドレス>/` を開きます。ユーザー名は `pi`、初期パスワードはセットアップ完了時に一度だけ表示されます。再設定は次のとおりです。

```bash
sudo htpasswd -m /etc/nginx/dap-admin.htpasswd pi
```

音楽ファイルのアップロードと削除は DUFS を使います。インストール済みなら、LAN へ直接公開せず Nginx の認証を通す次の起動オプションを推奨します。管理画面の「DUFSを開く」は `/files/` へ別タブで接続します。

```bash
dufs --bind 127.0.0.1 --port 8000 --path-prefix /files --allow-upload --allow-delete /home/pi/music
```

追加した音楽を本体の一覧に反映するには、本体メニューの「曲ライブラリ再読み込み」を実行してください。

管理画面は HTTP で提供するため、インターネットへ公開せず信頼できる LAN または AP 内で使用してください。

### AP モード

「接続設定」→「APモード: OFF」を選ぶと、DAP が `PiZero-DAP` という WPA2 アクセスポイントを起動します。スマートフォンや PC から接続し、`192.168.4.1` で管理画面等にアクセスできます（インターネット接続は提供しません）。

SSID・パスワード・国コード・チャンネルは `ap_config.json` で設定します。初期値は `PiZero-DAP` / `dap-connect-1234` / `JP` / チャンネル `6` です。**運用前にパスワードを変更してください**。パスワードは印字可能な ASCII 文字で 8〜63 文字、SSID は UTF-8 で 1〜32 バイトです。変更後、AP を再起動すると反映されます。

```json
{
  "ssid": "PiZero-DAP",
  "password": "dap-connect-1234",
  "country": "JP",
  "channel": 6
}
```

---

## 補足

### GPIO18 と PWM 音声の併用

GPIO18/19 を PWM 音声出力に使う構成では、Waveshare ドライバーが GPIO18 を PWR 制御に使わないようにします。`setup.sh` はこのパッチ（`tools/patch_waveshare_epd_gpio18_pwr.py`）を自動適用します。ドライバーを手動更新した場合は再実行してください。

```bash
python3 /home/pi/dap/tools/patch_waveshare_epd_gpio18_pwr.py /home/pi/dap/waveshare_epd/epdconfig.py
```

### DAP 操作時の sudo パスワード

`setup.sh` は DAP サービスが使う無線切替・Bluetooth/BlueALSA 制御・アプリ再起動などに限り、パスワードなしの sudo を `/etc/sudoers.d/dap` に追加します。SSH での一般的な管理コマンドには引き続きパスワードが必要です。既存環境でコード更新後に権限設定を反映する場合：

```bash
sudo bash /home/pi/dap/tools/configure_dap_sudoers.sh
```

### GitHub 更新時の全画面更新

GitHub 更新では、処理の前に画面の全画面更新を要求します。配線や接触不良がある場合は、ドライバーがパネルの RST を動作させてから BUSY 状態のまま終了することがあるため、接続の確認も行ってください。

### BlueALSA の状態確認

```bash
sudo systemctl is-active bluealsa
bluetoothctl info AA:BB:CC:DD:EE:FF
aplay -L | grep -i bluealsa
```

`AA:BB:CC:DD:EE:FF` は実際の Bluetooth イヤホンの MAC アドレスに置き換えてください。Bluetooth 機器の接続は手動操作で、選択した機器のパスキー承認のみ自動化します（PIN 入力や切断後の自動再ペアリングは非対応。イヤホン側の自動再接続には対応）。必要なパッケージは `bluetooth bluez bluez-alsa-utils alsa-utils libasound2-plugin-equal python3-dbus python3-gi` です。

Raspbian Trixie の標準 BlueALSA は SBC のみです。AAC 対応ビルドの導入と標準版への戻し方は [SBC / AAC 対応 BlueALSA](docs/BLUEALSA_AAC.md) を参照してください。

---

## ライセンス

本プロジェクトのソースコードは [MIT License](LICENSE.md) のもとで公開されています。
