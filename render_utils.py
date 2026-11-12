"""電子ペーパー表示用のテキスト整形ユーティリティ(状態に依存しない純粋関数)。"""


def truncate_by_width(text, font, max_px):
    def get_text_width(t):
        if hasattr(font, 'getlength'):
            return font.getlength(t)
        elif hasattr(font, 'getbbox'):
            return font.getbbox(t)[2]
        return len(t) * 8

    if get_text_width(text) <= max_px:
        return text

    ellipsis = "…"
    ellipsis_w = get_text_width(ellipsis)
    target_w = max_px - ellipsis_w

    truncated = ""
    for char in text:
        if get_text_width(truncated + char) > target_w:
            break
        truncated += char

    return truncated + ellipsis


def format_time_str(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def format_size_bytes(value):
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024.0 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024.0
    return f"{amount:.1f} TB"


def format_ram_bytes(value, total):
    """RAM平均の表示用。0.1MB単位だと5秒ごとに表示が変わり画面更新が増えるため、
    1GB未満は整数MBで表示する(変化が目に見える程度のときだけ更新される)。"""
    if total >= 1024 ** 3:
        return format_size_bytes(value)
    return f"{value / (1024 ** 2):.0f} MB"
