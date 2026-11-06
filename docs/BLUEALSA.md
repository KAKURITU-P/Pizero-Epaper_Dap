# BlueALSA 手動接続用ヘルパー

`bluealsa_utils.py` は、BlueALSA A2DP出力に必要なALSA設定生成と、`aplay -L` の出力から対象Bluetooth機器を検出する処理を副作用なしで提供します。DAP本体は `aplay -L` を優先し、環境により利用できない場合だけ `bluealsa-aplay -L` にフォールバックします。

- MACアドレスを検証・正規化
- `bluealsa:DEV=<MAC>,PROFILE=a2dp` 形式のPCM名を生成
- 48 kHzの直接BlueALSA PCMを使う `.asoundrc` 設定を生成
- BT出力時は`equal`/`plug`を使わず、ノイズを避ける
- BlueALSA PCM一覧から対象機器の有無を判定

今回のコミットでは、APモード、DAFS/Web機能、自動Bluetooth再接続、PIN入力は変更していません。

テストは次のコマンドで実行できます。

```bash
python3 -m unittest discover -s tests -v
```

実機では、手動接続後に次のコマンドでBlueALSA PCMが公開されていることを確認できます。

```bash
bluetoothctl info AA:BB:CC:DD:EE:FF
aplay -L | grep -i bluealsa
systemctl is-active bluealsa
```

`AA:BB:CC:DD:EE:FF` は実際のイヤホンのMACアドレスに置き換えてください。
