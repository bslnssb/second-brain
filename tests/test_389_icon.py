# -*- coding: utf-8 -*-
"""第 389 项：图标被旧脚本覆盖成「青绿扁平方块」的回归测试。

2026-11-21 22:06 的「清场」提交里，`gen_icon.py`（旧版绘制逻辑）被重跑了一次，
把 2026-11-14 起的「放大镜 + M」品牌图标覆盖成旧样子；它「沿用旧底色」取的
(3,24) 这一点在品牌图标里正好落在青绿镜环上，于是整张变青绿（截图里那个方块）。

这里钉死三件事：
  1. 正式资源必须是多尺寸品牌图标（体积、透明角、橙色把手都在）；
  2. `gen_icon.py` 默认拒绝执行，必须显式 --force；
  3. `icon_preview.png`（关于页用）跟 ico 同源，不是旧的 2KB 版。
"""

import io
import os
import unittest

from PIL import Image, IcoImagePlugin

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ICON = os.path.join(ROOT, 'assets', 'icon.ico')
PREVIEW = os.path.join(ROOT, 'assets', 'icon_preview.png')
GEN = os.path.join(ROOT, 'gen_icon.py')


def _read(p):
    with io.open(p, encoding='utf-8') as f:
        return f.read()


class TestBrandIcon(unittest.TestCase):
    def test_icon_has_all_sizes(self):
        ico = IcoImagePlugin.IcoFile(open(ICON, 'rb'))
        sizes = set(ico.sizes())
        for s in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
            self.assertIn((s, s), sizes, '缺 %dx%d 这一档' % (s, s))

    def test_icon_is_transparent_corner_brand_mark(self):
        """品牌图标是「圆角放大镜」：四角透明，镜环青绿，把手橙黄。"""
        ico = IcoImagePlugin.IcoFile(open(ICON, 'rb'))
        im = ico.getimage((256, 256)).convert('RGBA')
        self.assertEqual(im.getpixel((2, 2))[3], 0, '四角必须透明（扁平方块会填满）')
        colored = [p for p in im.getdata() if p[3] > 200]
        teal = sum(1 for p in colored if p[1] > 150 and p[2] > 150 and p[0] < 80)
        orange = sum(1 for p in colored if p[0] > 200 and 90 < p[1] < 190 and p[2] < 110)
        self.assertGreater(teal, 1000, '镜环青绿不见了')
        self.assertGreater(orange, 200, '橙色把手不见了')

    def test_preview_matches_brand_size(self):
        self.assertGreater(os.path.getsize(PREVIEW), 20000, '关于页还在用旧版小图')
        im = Image.open(PREVIEW)
        self.assertEqual(im.size, (256, 256))

    def test_gen_icon_refuses_without_force(self):
        src = _read(GEN)
        self.assertIn("if '--force' not in sys.argv:", src)
        self.assertIn('raise SystemExit(2)', src)
        self.assertIn('gen_logo_20261114.py', src)


if __name__ == '__main__':
    unittest.main()
