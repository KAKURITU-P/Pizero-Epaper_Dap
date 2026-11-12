"""スプライトシートから再生モード・出力アイコンや猫時計の船画像を切り出す。"""

import os

from PIL import Image, ImageDraw, ImageOps


def load_playback_mode_icons(sheet_path):
    """Load playback mode and audio output glyphs from the sprite sheet."""
    try:
        sheet = Image.open(sheet_path).convert("1")
        boxes = {}
        if sheet.width < 55 or sheet.height < 36:
            raise ValueError(f"sprite sheet is too small: {sheet.size}")

        # The left column contains playback modes.  The middle column has PWM
        # on the first row, then a 2x2 BT/codec sprite:
        # BT | SBC
        # AAC | other
        # The right column has the USB OTG output icon.
        boxes.update({
            "shuffle": (1, 1, 18, 12),
            "repeat_all": (1, 13, 18, 24),
            "repeat_one": (1, 25, 18, 36),
            "pwm": (19, 1, 36, 12),
            "bt": (19, 13, 28, 24),
            "sbc": (28, 13, 36, 24),
            "aac": (19, 25, 28, 36),
            "other": (28, 25, 36, 36),
            "usb": (37, 1, 54, 12),
        })

        icons = {}
        for name, box in boxes.items():
            icon = sheet.crop(box)
            icon_size = {
                "shuffle": (17, 11),
                "repeat_all": (17, 11),
                "repeat_one": (17, 11),
                "pwm": (17, 11),
                "bt": (9, 11),
                "sbc": (8, 11),
                "aac": (8, 11),
                "other": (8, 11),
                "usb": (17, 11),
            }[name]
            if icon.size != icon_size:
                icon = icon.resize(icon_size, Image.Resampling.NEAREST)
            icons[name] = icon

        for codec in ("aac", "sbc", "other"):
            if "bt" in icons and codec in icons:
                combined = Image.new("1", (17, 11), 1)
                combined.paste(icons["bt"], (0, 0))
                combined.paste(icons[codec], (9, 0))
                icons[codec] = combined
        if "pwm" not in icons:
            fallback = Image.new("1", (17, 11), 1)
            fallback_draw = ImageDraw.Draw(fallback)
            fallback_draw.line(
                [(1, 4), (5, 4), (5, 10), (8, 10), (8, 4), (12, 4),
                 (12, 10), (15, 10), (15, 4), (19, 4)],
                fill=0, width=1
            )
            icons.setdefault("pwm", fallback)

        return icons
    except (OSError, ValueError) as error:
        print(f"再生モードアイコンを読み込めません: {error}")
        return {}


def get_cropped_ship(ships_path, ship_index, target_height):
    """猫時計用の船画像を切り出して黒シルエットとマスクを返す。"""
    if not os.path.exists(ships_path):
        return None, None

    try:
        img = Image.open(ships_path).convert("RGBA")
        crops = [
            (30,  15, 480, 90),
            (30, 100, 480, 175),
            (30, 185, 480, 260),
            (30, 270, 480, 345),
            (30, 355, 480, 430),
            (30, 440, 480, 505)
        ]
        if not (0 <= ship_index < len(crops)):
            ship_index = 0

        crop_box = crops[ship_index]
        cropped = img.crop(crop_box)

        bbox = cropped.getbbox()
        if bbox:
            cropped = cropped.crop(bbox)

        aspect_ratio = cropped.width / float(cropped.height)
        new_w = int(target_height * aspect_ratio)
        resized = cropped.resize((new_w, target_height), Image.Resampling.LANCZOS)
        resized = ImageOps.mirror(resized)

        alpha = resized.getchannel("A")
        black_shape = Image.eval(alpha, lambda p: 0 if p > 128 else 255).convert("1")
        mask = alpha.point(lambda p: 255 if p > 128 else 0, mode="1")

        return black_shape, mask
    except Exception as e:
        print(f"Ship crop error ({ship_index}): {e}")
        return None, None
