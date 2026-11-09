"""CPU使用率・SoC温度の計測と、グラフ座標の計算(副作用なし)。

dap_daemon.py から使い、ハードウェアがなくても単体テストできるように分離している。
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

CpuTimes = tuple[int, int]  # (busy, total) のジフィ数


def parse_cpu_times(proc_stat_text: str) -> Optional[CpuTimes]:
    """``/proc/stat`` の先頭 ``cpu`` 行から (busy, total) を返す。

    user nice system idle iowait irq softirq steal の8項目を使う。guest系は
    user/niceに既に含まれるため加えない。idleとiowaitは「使用中」に数えない。
    """
    for line in (proc_stat_text or "").splitlines():
        if not line.startswith("cpu "):
            continue
        try:
            values = [int(part) for part in line.split()[1:9]]
        except ValueError:
            return None
        if len(values) < 4:
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        return total - idle, total
    return None


def cpu_usage_percent(previous: Optional[CpuTimes], current: Optional[CpuTimes]) -> Optional[float]:
    """2回の計測値の差からCPU使用率(0〜100)を返す。計算できなければNone。"""
    if previous is None or current is None:
        return None
    busy = current[0] - previous[0]
    total = current[1] - previous[1]
    if total <= 0 or busy < 0:
        return None
    return max(0.0, min(100.0, busy * 100.0 / total))


def parse_temperature_celsius(text: str) -> Optional[float]:
    """``thermal_zone0/temp``(ミリ度)を摂氏にする。範囲外・不正値はNone。"""
    try:
        value = int((text or "").strip())
    except ValueError:
        return None
    celsius = value / 1000.0 if abs(value) >= 1000 else float(value)
    return celsius if -40.0 <= celsius <= 150.0 else None


def summarize(values: Iterable[Optional[float]]) -> Optional[dict]:
    """None以外の値から、現在・平均・最大・件数を返す。空ならNone。"""
    items = [float(value) for value in values if value is not None]
    if not items:
        return None
    return {
        "current": items[-1],
        "average": sum(items) / len(items),
        "peak": max(items),
        "count": len(items),
    }


def graph_points(
    values: Sequence[float],
    box: tuple[int, int, int, int],
    slots: int = 60,
    y_min: float = 0.0,
    y_max: float = 100.0,
) -> list[tuple[int, int]]:
    """値の列を、箱 ``(x0, y0, x1, y1)`` 内のピクセル座標に変換する。

    新しい値が右端に来る。値が ``slots`` 未満なら左側を空ける。範囲外の値は丸める。
    """
    if not values or slots < 1 or y_max <= y_min:
        return []
    x0, y0, x1, y1 = box
    items = list(values)[-slots:]
    step = (x1 - x0) / (slots - 1) if slots > 1 else 0.0
    height = y1 - y0
    points = []
    for index, value in enumerate(items):
        slot = slots - len(items) + index
        ratio = (min(max(float(value), y_min), y_max) - y_min) / (y_max - y_min)
        points.append((x0 + int(round(slot * step)), y1 - int(round(ratio * height))))
    return points
