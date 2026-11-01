# -*- coding: utf-8 -*-
"""生成应用图标：紫渐变圆角方块 + 🧠（Segoe UI Emoji 彩色渲染，失败则回退"脑"字）"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

S = 256
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))

# 品牌紫渐变（与网页 #4f6ef7 → #7c3aed 同款）
grad = Image.new('RGBA', (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    t = y / S
    r = int(0x4F + (0x7C - 0x4F) * t)
    g = int(0x6E + (0x3A - 0x6E) * t)
    b = int(0xF7 + (0xED - 0xF7) * t)
    gd.line([(0, y), (S, y)], fill=(r, g, b, 255))
mask = Image.new('L', (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([10, 10, S - 10, S - 10], radius=58, fill=255)
img.paste(grad, (0, 0), mask)

d = ImageDraw.Draw(img)
used_fallback = False
try:
    font = ImageFont.truetype(r'C:\Windows\Fonts\seguiemj.ttf', 148)
    d.text((S / 2, S / 2 - 6), '\U0001F9E0', font=font, embedded_color=True, anchor='mm')
except Exception as e:
    print('emoji 渲染失败，回退文字：', e)
    used_fallback = True
    font = ImageFont.truetype(r'C:\Windows\Fonts\msyhbd.ttc', 118)
    d.text((S / 2, S / 2), '脑', font=font, fill=(255, 255, 255, 255), anchor='mm')

out = Path(__file__).parent / 'assets'
out.mkdir(exist_ok=True)
img.save(out / 'icon.ico',
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out / 'icon_preview.png')
print('图标已生成', out / 'icon.ico', '| 模式:', '文字回退' if used_fallback else '彩色emoji')
