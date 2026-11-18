import numpy as np
import pygame
import time

# オーディオ設定
sample_rate = 44100
duration = 1.0  # 1秒の波形データを作成してループ
frequency = 440.0  # 440Hz (A4音)

# 正弦波の生成 (LとRにまったく同じ振幅・位相の波形を入れる)
t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
waveform = (np.sin(2 * np.pi * frequency * t) * 32767).astype(np.int16)

# ステレオデータ化 (2チャンネルに同じデータをコピー)
stereo_waveform = np.column_stack((waveform, waveform))

# Pygame オーディオ初期化
pygame.mixer.pre_init(frequency=sample_rate, size=-16, channels=2, buffer=1024)
pygame.init()

# サウンドオブジェクト作成と再生
sound = pygame.sndarray.make_sound(stereo_waveform)
sound.play(loops=-1)

print("--- L/R 音量調整用テスト信号を出力中 (440Hz Sine Wave) ---")
print("終了するには Ctrl+C を押してください...")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n停止しました。")
    pygame.quit()
