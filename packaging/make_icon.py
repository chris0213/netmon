#!/usr/bin/env python3
"""纯标准库生成 NetMon 应用图标（无 Pillow 等第三方依赖）。

流程：手写 PNG 编码器 + 光栅化一个 lucide 风格的「盾牌 + 信号波纹」线性图标
（圆角蓝底、白色描边），输出 iconset 所需的各尺寸，再用系统 iconutil 转成 .icns。
同时导出一张 512 的 netmon.png 供 tkinter 窗口图标使用。

用法：python3 packaging/make_icon.py
"""

from __future__ import annotations

import math
import struct
import subprocess
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ICONSET = HERE / "NetMon.iconset"
ICNS = HERE / "NetMon.icns"
PNG_512 = HERE / "netmon.png"

# 配色（与 GUI 主题 token 一致：蓝底白线，非紫粉渐变）
BG = (37, 99, 235)        # #2563EB
BG_DARK = (29, 78, 216)   # #1D4ED8
FG = (255, 255, 255)

SIZES = [16, 32, 64, 128, 256, 512, 1024]


def _clamp(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _write_png(path: Path, w: int, h: int, rgb: bytearray) -> None:
    """最简 PNG 编码器：8-bit RGB，zlib 压缩，逐行 filter=0。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def _signed_dist_segment(px: float, py: float, x1: float, y1: float,
                         x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _polygon_dist(px: float, py: float, pts: list[tuple[float, float]]) -> float:
    return min(_signed_dist_segment(px, py, *pts[i], *pts[(i + 1) % len(pts)])
               for i in range(len(pts)))


def _point_in_polygon(px: float, py: float, pts: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xint = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xint:
                inside = not inside
    return inside


# 归一化坐标（0..1）下的图形定义
SHIELD = [(0.50, 0.17), (0.76, 0.27), (0.76, 0.53),
          (0.50, 0.82), (0.24, 0.53), (0.24, 0.27)]
ARC_CX, ARC_CY = 0.50, 0.40
ARC_RADII = (0.135, 0.085)
DOT = (0.50, 0.47, 0.022)


def _sample(nx: float, ny: float, stroke: float) -> tuple:
    """返回该归一化坐标处的 RGB 颜色。"""
    # 背景：轻微对角明暗过渡（同色系，非紫粉渐变）
    t = _clamp((nx + ny) / 2 * 255) / 255.0
    color = _lerp(BG_DARK, BG, t)

    # 盾牌：空心描边
    d = _polygon_dist(nx, ny, SHIELD)
    inside = _point_in_polygon(nx, ny, SHIELD)
    if (inside and d < stroke) or (not inside and d < stroke):
        return FG

    # 信号波纹：两段同心圆弧（仅上半，模拟 WiFi 波纹）
    rr = math.hypot(nx - ARC_CX, ny - ARC_CY)
    ang = math.degrees(math.atan2(-(ny - ARC_CY), nx - ARC_CX))  # 0=right, 90=up
    for r in ARC_RADII:
        if abs(rr - r) < stroke * 0.8 and 20 <= ang <= 160:
            return FG

    # 中心圆点
    if math.hypot(nx - DOT[0], ny - DOT[1]) < DOT[2]:
        return FG
    return color


def render(size: int) -> bytearray:
    rgb = bytearray(size * size * 3)
    ss = 2  # 2x2 超采样抗锯齿
    stroke = 0.016
    for py in range(size):
        for px in range(size):
            r = g = b = 0.0
            for sy in range(ss):
                for sx in range(ss):
                    nx = (px + (sx + 0.5) / ss) / size
                    ny = (py + (sy + 0.5) / ss) / size
                    c = _sample(nx, ny, stroke)
                    r += c[0]; g += c[1]; b += c[2]
            n = ss * ss
            i = (py * size + px) * 3
            rgb[i] = _clamp(r / n)
            rgb[i + 1] = _clamp(g / n)
            rgb[i + 2] = _clamp(b / n)
    return rgb


def main() -> None:
    ICONSET.mkdir(parents=True, exist_ok=True)
    for s in SIZES:
        _write_png(ICONSET / f"icon_{s}x{s}.png", s, s, render(s))
        _write_png(ICONSET / f"icon_{s//2}x{s//2}@2x.png", s // 2, s // 2, render(s // 2))
    _write_png(PNG_512, 512, 512, render(512))

    if subprocess.run(["which", "iconutil"], capture_output=True).returncode == 0:
        subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
        print(f"生成 {ICNS} ({ICNS.stat().st_size} bytes)")
    print(f"生成 {PNG_512} ({PNG_512.stat().st_size} bytes)")
    print("图标尺寸:", ", ".join(f"{s}" for s in SIZES))


if __name__ == "__main__":
    main()
