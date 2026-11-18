import time
from gpiozero import Button

# 各ボタンのピン定義（画像仕様準拠）
buttons = {
    "VOL UP (音量UP)": Button(22),       # Pin 15
    "VOL DOWN (音量DOWN)": Button(27),   # Pin 13
    "MODE (画面/モード切替)": Button(5), # Pin 29
    "PREV / UP (曲戻し/上)": Button(12), # Pin 32
    "NEXT / DOWN (曲送り/下)": Button(16),# Pin 36
    "PLAY / SELECT (再生/決定)": Button(20) # Pin 38
}

print("=== GPIO ボタン入力テスト開始 ===")
print("ボタンを押すと画面に名前が表示されます。終了するには Ctrl+C を押してください。\n")

def make_handler(name):
    return lambda: print(f"[PUSHED] {name}")

for name, btn in buttons.items():
    btn.when_pressed = make_handler(name)

try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nテストを終了します。")
