"""e-Paper表示用のフォント検出と読み込み。"""

import glob
import os

from PIL import ImageFont


def find_japanese_font():
    """日本語表示に使えるTTF/OTFを探す。見つからなければNone。"""
    font_candidates = [
        "/usr/share/fonts/truetype/bizud-gothic/BIZUDGothic-Regular.ttf",
        "/usr/share/fonts/truetype/bizud-gothic/BIZUDPGothic-Regular.ttf",
        "/usr/share/fonts/truetype/bizud-gothic/BIZUDGothic-Bold.ttf",
        "/usr/share/fonts/truetype/takao-gothic/TakaoGothic.ttf",
        "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"
    ]
    for path in font_candidates:
        if os.path.exists(path):
            return path

    bizud_fonts = glob.glob("/usr/share/fonts/**/bizud-gothic/*.ttf") + \
                  glob.glob("/usr/share/fonts/**/bizud-gothic/*.otf")
    if bizud_fonts:
        return bizud_fonts[0]

    found = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True) + \
            glob.glob("/usr/share/fonts/**/*.otf", recursive=True) + \
            glob.glob("/usr/share/fonts/**/*.ttc", recursive=True)
    return found[0] if found else None


def get_font(font_path, size):
    """指定パスのフォントを読み込む。読めない場合はPIL内蔵フォントに落ちる。"""
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    return ImageFont.load_default()
