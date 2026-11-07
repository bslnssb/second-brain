# -*- coding: utf-8 -*-
"""环境矩阵测试：常见「分辨率 × 缩放」组合下窗口布局是否可用。

为什么单独一个文件：这台开发机只有 2560×1440@150% 一块屏，用户那边什么样
（1366×768 的旧笔记本设 150%、4K 屏设 200%、竖屏副屏、任务栏在左边……）
本地根本复现不了。但布局算法是纯算术 —— 把屏幕参数喂进去就能算，
于是「窗口会不会超出屏幕」这类问题可以在没有对应硬件的机器上全部覆盖。

2026-11-07 实测发现的第一个真问题就来自这里：1366×768 @150%（逻辑 910×512）
下 `max(520, ...)` 这个硬下限让主窗高 520 > 可用 472，标题栏跑到屏幕外，
窗口既拖不动也关不掉。
"""
from __future__ import annotations

import unittest

try:
    import app_window as aw
except Exception:  # pragma: no cover
    aw = None
    _IMPORT_ERR = None

# 任务栏：1080p 100% 下约 40 逻辑像素（150% 时系统仍按逻辑像素反馈给程序）
TASKBAR = 40

CASES = [
    ('1366x768 @100%', 1366, 768, 1.00),
    ('1366x768 @125%', 1366, 768, 1.25),
    ('1366x768 @150%', 1366, 768, 1.50),   # 曾经超屏的那一档
    ('1600x900 @100%', 1600, 900, 1.00),
    ('1600x900 @125%', 1600, 900, 1.25),
    ('1920x1080 @100%', 1920, 1080, 1.00),
    ('1920x1080 @125%', 1920, 1080, 1.25),
    ('1920x1080 @150%', 1920, 1080, 1.50),
    ('1920x1080 @175%', 1920, 1080, 1.75),
    ('2560x1440 @100%', 2560, 1440, 1.00),
    ('2560x1440 @150%', 2560, 1440, 1.50),   # 开发机
    ('2560x1440 @200%', 2560, 1440, 2.00),
    ('3440x1440 @100%', 3440, 1440, 1.00),   # 超宽
    ('3840x2160 @100%', 3840, 2160, 1.00),
    ('3840x2160 @150%', 3840, 2160, 1.50),
    ('3840x2160 @200%', 3840, 2160, 2.00),
    ('1920x1200 @150%', 1920, 1200, 1.50),
    ('1080x1920 @100%', 1080, 1920, 1.00),   # 竖屏
]


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestWindowFitsScreen(unittest.TestCase):
    def test_window_never_exceeds_screen(self):
        for name, pw, ph, dpr in CASES:
            with self.subTest(screen=name):
                lw, lh = int(pw / dpr), int(ph / dpr)      # 逻辑屏幕
                avail_h = lh - TASKBAR
                mw, mh = aw.main_window_size(lw, lh)
                self.assertLessEqual(mw, lw, '窗口比屏幕还宽')
                self.assertLessEqual(mh, lh, '窗口比屏幕还高')
                self.assertLessEqual(mh, max(avail_h, 420),
                                     '窗口高度超过可用区（任务栏会把底部盖住/标题栏出屏）')

    def test_min_size_is_smaller_than_window(self):
        """最小尺寸必须比默认尺寸小，否则用户拖不动，小屏上等于关不掉。"""
        for name, pw, ph, dpr in CASES:
            with self.subTest(screen=name):
                lw, lh = int(pw / dpr), int(ph / dpr)
                mw, mh = aw.main_window_size(lw, lh)
                minw, minh = aw.main_min_size(mw, mh)
                self.assertLessEqual(minw, mw)
                self.assertLessEqual(minh, mh)
                self.assertLess(minh, lh, '最小高度就超过屏幕了')
                self.assertLess(minw, lw, '最小宽度就超过屏幕了')

    def test_mini_window_fits(self):
        """快搜窗固定 760 逻辑宽，窄屏上也不能比屏幕宽。"""
        for name, pw, ph, dpr in CASES:
            with self.subTest(screen=name):
                lw = int(pw / dpr)
                if lw >= aw.MINI_W:
                    self.assertGreaterEqual(lw, aw.MINI_W)

    def test_data_driven_dpr_matches_scale(self):
        """缩放比换算：120/144/168/192/240 dpi 分别对应 1.25/1.5/1.75/2.0/2.5。"""
        for dpi, want in [(96, 1.0), (120, 1.25), (144, 1.5), (168, 1.75),
                          (192, 2.0), (216, 2.25), (240, 2.5)]:
            with self.subTest(dpi=dpi):
                self.assertAlmostEqual(dpi / 96.0, want, places=3)


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestMiniOrigin(unittest.TestCase):
    def test_primary_matches_legacy(self):
        """单屏：新算法必须和老的 margin 逻辑一致（老代码 = 屏幕居中 + 17%）。"""
        rect = (0, 0, 2560, 1440)
        x, y = aw.mini_origin_in(rect, 1.5)
        self.assertEqual(x, int(round((2560 / 1.5 - aw.MINI_W) / 2)))
        self.assertEqual(y, int(round(1440 / 1.5 * 0.17)))

    def test_secondary_right_is_positive_offset(self):
        """副屏在主屏右边：x 必须加上那块屏的起点，否则还会画在主屏上。"""
        primary = (0, 0, 1920, 1080)
        right = (1920, 0, 3840, 1080)
        px, _ = aw.mini_origin_in(primary, 1.0)
        rx, _ = aw.mini_origin_in(right, 1.0)
        self.assertGreater(rx, 1920)
        self.assertEqual(rx - px, 1920)

    def test_secondary_left_is_negative_offset(self):
        """副屏在主屏左边时坐标是负数 —— 不能 max(0, ...)，否则窗口跑到主屏上。"""
        left = (-1920, 0, 0, 1080)
        x, y = aw.mini_origin_in(left, 1.0)
        self.assertLess(x, 0)
        self.assertLess(y, 1080)

    def test_secondary_above_is_negative_y(self):
        above = (0, -1080, 1920, 0)
        _, y = aw.mini_origin_in(above, 1.0)
        self.assertLess(y, 0)

    def test_150_percent_centering(self):
        """150% 屏：760 逻辑宽的窗口在 1706 逻辑屏上应居中在 473 左右。"""
        x, y = aw.mini_origin_in((0, 0, 2560, 1440), 1.5)
        self.assertAlmostEqual(x, 473, delta=2)
        self.assertAlmostEqual(y, 163, delta=2)


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestEnvSnapshot(unittest.TestCase):
    def test_snapshot_has_key_fields(self):
        s = aw.env_snapshot(None)
        self.assertTrue(s, '环境快照为空')
        self.assertIn('screen=', s)
        self.assertIn('monitors=', s)
        self.assertIn('dpr=', s)


if __name__ == '__main__':
    unittest.main()
