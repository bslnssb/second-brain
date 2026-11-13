# -*- coding: utf-8 -*-
"""生成应用图标：深蓝圆角方块 + 白色放大镜，与现网视觉一致。

为什么每个尺寸单独画：以前是把 256px 大图交给 Pillow 缩小生成各档，
16/20/24 下「双环 + 细把手」的抗锯齿糊成一团；再被 150% DPI 的任务栏
放大一次就彻底看不清。小尺寸只保留一根粗圆环 + 一根粗把手，
≥32 才加内圈细节。

另外手写 ICO 目录把各尺寸的 PNG 逐张塞进去（Pillow 的 save(sizes=...)
只会从一张大图缩放，做不到逐尺寸定制）。
"""
import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, IcoImagePlugin

OUT = Path(__file__).resolve().parent / 'assets'
OUT.mkdir(exist_ok=True)
FG = (255, 255, 255, 255)

# 沿用旧图标的底色，避免这次「顺手」改了品牌色
try:
    _old = IcoImagePlugin.IcoFile(open(OUT / 'icon.ico', 'rb'))
    _src = _old.getimage((48, 48)).convert('RGBA')
    BG = _src.getpixel((3, 24))
    if BG[3] < 200:
        raise ValueError('not opaque')
except Exception:
    BG = (30, 42, 91, 255)

SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def draw(s: int) -> Image.Image:
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = 0 if s <= 24 else max(1, round(s * 0.04))
    d.rounded_rectangle([pad, pad, s - 1 - pad, s - 1 - pad],
                        radius=max(2, round(s * 0.22)), fill=BG)

    w = max(2, round(s * (0.09 if s <= 24 else 0.075)))
    cx = cy = round(s * 0.41)
    r = round(s * 0.21)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=FG, width=w)

    if s >= 32:                       # 小尺寸不要内圈，否则 16px 下只剩一坨
        r2 = max(1, r - w - max(1, round(s * 0.05)))
        d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2],
                  outline=FG, width=max(1, w // 2))

    x0, y0 = round(cx + r * 0.72), round(cy + r * 0.72)
    x1, y1 = round(s * 0.84), round(s * 0.84)
    d.line([x0, y0, x1, y1], fill=FG, width=w)
    d.ellipse([x1 - w // 2, y1 - w // 2, x1 + w // 2, y1 + w // 2], fill=FG)
    return im


pngs = []
for s in SIZES:
    buf = io.BytesIO()
    draw(s).save(buf, format='PNG')
    pngs.append((s, buf.getvalue()))

entries, blobs = b'', b''
off = 6 + 16 * len(pngs)
for s, blob in pngs:
    dim = 0 if s >= 256 else s
    entries += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(blob), off)
    blobs += blob
    off += len(blob)

(OUT / 'icon.ico').write_bytes(struct.pack('<HHH', 0, 1, len(pngs)) + entries + blobs)
draw(256).save(OUT / 'icon_preview.png')
print('图标已生成：%s' % (OUT / 'icon.ico'))
print('尺寸档：%s' % ', '.join('%dx%d' % (s, s) for s in SIZES))
print('底色：%s' % (BG,))
