# -*- coding: utf-8 -*-
"""第 289 项回归：贴边吸附只认「正好贴住」的容差带，拖出屏幕不吸。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = (0, 0, 2560, 1392)      # 主屏工作区（逻辑像素）


class SnapZone289(unittest.TestCase):
    def setUp(self):
        from app_window import snap_zone
        self.zone = staticmethod(snap_zone).__func__

    def test_still_snaps_when_flush(self):
        # 右边缘正好贴住 → 右半屏（保留原有手感）
        self.assertEqual(self.zone((2560 - 1200, 100, 2560, 1200), WORK), 'right')
        # 左边缘贴住 → 左半屏
        self.assertEqual(self.zone((0, 100, 1200, 1200), WORK), 'left')
        # 顶边贴住 → 最大化
        self.assertEqual(self.zone((100, 0, 1300, 1200), WORK), 'max')

    def test_tolerance_band(self):
        self.assertEqual(self.zone((2560 - 1200 - 4, 100, 2560 - 4, 1200), WORK), 'right')
        self.assertEqual(self.zone((2566 - 1200, 100, 2566, 1200), WORK), 'right')

    def test_no_snap_when_dragged_offscreen(self):
        # 想临时藏一部分：右边缘越过屏幕 30px / 300px → 都不许吸
        self.assertIsNone(self.zone((2560 + 30 - 1200, 100, 2560 + 30, 1200), WORK))
        self.assertIsNone(self.zone((2560 + 300 - 1200, 100, 2560 + 300, 1200), WORK))
        # 往左藏、往上藏同理
        self.assertIsNone(self.zone((-300, 100, 900, 1200), WORK))
        self.assertIsNone(self.zone((100, -200, 1300, 1000), WORK))

    def test_untouched_window_not_snapped(self):
        self.assertIsNone(self.zone((300, 200, 1500, 1300), WORK))

    def test_multi_monitor_right_monitor(self):
        # 副屏工作区 (2560,0)-(5120,1392)：贴副屏右缘要算副屏自己的 wr
        self.assertEqual(self.zone((5120 - 1000, 50, 5120, 1300), (2560, 0, 5120, 1392)), 'right')
        self.assertIsNone(self.zone((5120 + 400 - 1000, 50, 5120 + 400, 1300), (2560, 0, 5120, 1392)))


if __name__ == '__main__':
    unittest.main()
