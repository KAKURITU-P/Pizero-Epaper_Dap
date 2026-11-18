import time
from gpiozero import Button

# 現在のdap_daemon.pyの設定に合わせたピン定義
buttons = {
    "GPIO 5  (Pin 29) [メニュー/決定]": Button(5),
    "GPIO 20 (Pin 38) [再生停止/戻る]": Button(20),
    "GPIO 27 (Pin 13) [上に倒す：曲戻し/上移動]": Button(27),
    "GPIO 22 (Pin 15) [下に倒す：曲送り/下移動]": Button(22),
    "GPIO 12 (Pin 32) [音量 UP]": Button(12),
    "GPIO 16 (Pin 36) [音量 DOWN]": Button(16)
}

print("==========================================")
print("     GPIO 入力検知 リアルタイムテスト")
print("==========================================")
print("スイッチを押すと、反応したGPIOと機能が表示されます。")
print("終了するには Ctrl + C を押してください。\n")

def make_handler(name):
    return lambda: print(f"[INPUT DETECTED] => {name}")

for name, btn in buttons.items():
    btn.when_pressed = make_handler(name)

try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nテストを終了しました。")
