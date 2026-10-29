# SBC / AAC 対応 BlueALSA

標準の Raspbian Trixie `bluez-alsa-utils` はこの環境で SBC のみを公開します。AAC を使うには、FDK-AAC を含めて BlueALSA をビルドする必要があります。BlueALSA の AAC 対応は `--enable-aac` で有効化されます。

`tools/install_bluealsa_aac.sh` は、Pi 上で FDK-AAC v2.0.3 と BlueALSA v4.3.1 をビルドし、`/opt/dap-bluealsa-aac` に隔離して導入します。既存の Raspbian パッケージは削除・上書きしません。systemd の drop-in で `bluealsa.service` の起動先を切り替えるため、DAP からのサービス操作は従来どおりです。インストール中にサービスを再起動するため、接続中のイヤホンはいったん切断されます。

Pi で SSH が切れてもビルドが続き、あとから出力を確認できるよう、`tmux` セッションで実行します。

```bash
sudo apt-get install -y tmux
tmux new -s bluealsa-aac
cd /home/pi/dap
bash tools/install_bluealsa_aac.sh
```

SSH が切れたら再接続し、`tmux attach -t bluealsa-aac` で戻ります。ログは `/home/pi/bluealsa-aac-install.log` にも追記されます。実行中に tmux を抜けるには `Ctrl+B` のあと `d` を押します。

ビルド後、次の出力に `A2DP-source : SBC AAC` が含まれることを確認してください。

```bash
bluealsa-cli status
```

イヤホンを再接続すると、BlueALSA が相手側と交渉して SBC または AAC を選びます。接続後の PCM パスは `bluealsa-cli list-pcms` で確認できます。コーデックの選択肢と現在値は次で確認できます。

```bash
bluealsa-cli codec /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsrc/sink
```

必要なら AAC を選択できます。再生中の codec 変更は PCM を終了させるので、再生を止めてから実行してください。

```bash
bluealsa-cli codec /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsrc/sink AAC
```

`XX_...` は `list-pcms` に表示された実際のパスに置き換えます。サービス状態は次で確認します。

```bash
sudo systemctl status bluealsa --no-pager
sudo journalctl -u bluealsa -b --no-pager
```

標準パッケージ版へ戻すには、drop-in と専用 `/opt` ディレクトリを削除して標準サービスを再起動します。

```bash
sudo bash /home/pi/dap/tools/remove_bluealsa_aac.sh
```
