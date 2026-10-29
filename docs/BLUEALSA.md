# BlueALSA 手動接続用ヘルパー

`bluealsa_utils.py` は、BlueALSA A2DP出力に必要なALSA設定生成と、`aplay -L` の出力から対象Bluetooth機器を検出する処理を副作用なしで提供します。

- MACアドレスを検証・正規化
- `bluealsa:DEV=<MAC>,PROFILE=a2dp` 形式のPCM名を生成
- 既存の `equal` PCMを維持した `.asoundrc` 設定を生成
- EQ と BlueALSA PCM の間に ALSA `plug` を挟み、再生形式と A2DP のサンプルレート差を変換
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
