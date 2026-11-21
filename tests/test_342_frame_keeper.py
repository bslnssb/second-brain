# -*- coding: utf-8 -*-
"""第 342 项：窗口顶部那条 10px「caption 带」必须跟主题走，而且被系统重置后要自己补回来。

真机数据（DPR 150% 的这台机器，2026-11-19）：
    GetWindowRect 1715x1384 / GetClientRect 1695x1364 / client 起点比窗口多 10px
    → 四边各 10px 是 DWM 画的非客户区，顶边那 10px 就是用户说的「标题上面还有一层」。
    apply_frame 后：浅色 f4f6fa、深色 181b21，能从浅切深再切回来（见 tmp/verify_342b.py）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_window


class ColorrefTest(unittest.TestCase):
    def test_colorref_is_bbggrr(self):
        self.assertEqual(0xFAF6F4, app_window._colorref(244, 246, 250))

    def test_ui_bg_rgb_light_and_dark(self):
        self.assertEqual((244, 246, 250), app_window.ui_bg_rgb(False))
        self.assertEqual((24, 27, 33), app_window.ui_bg_rgb(True))


class ApplyFrameTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._old = app_window._dwm_set
        app_window._dwm_set = lambda hwnd, attr, value: (
            self.calls.append((hwnd, attr, value)) or True)   # noqa: E731
        self._old_applied = dict(app_window._FRAME_APPLIED)
        app_window._FRAME_APPLIED['key'] = None

    def tearDown(self):
        app_window._dwm_set = self._old
        app_window._FRAME_APPLIED.update(self._old_applied)

    def test_no_hwnd(self):
        self.assertFalse(app_window.apply_frame(None))

    def test_sets_backdrop_caption_border(self):
        ok = app_window.apply_frame(4242, False, (244, 246, 250))
        self.assertTrue(ok)
        attrs = {a: v for (_h, a, v) in self.calls}
        self.assertEqual(1, attrs.get(38))                 # DWMSBT_NONE
        self.assertEqual(0xFAF6F4, attrs.get(35))          # caption → bg
        self.assertEqual(0xFAF6F4, attrs.get(34))          # border → bg
        # 不许设 NCRENDERING_POLICY = DISABLED（会多一条白带）
        self.assertNotIn(2, attrs)

    def test_dark_bg(self):
        app_window.apply_frame(4242, True, (24, 27, 33))
        attrs = {a: v for (_h, a, v) in self.calls}
        self.assertEqual((33 << 16) | (27 << 8) | 24, attrs.get(35))

    def test_paint_frame_delegates(self):
        self.assertTrue(app_window.paint_frame(4242, False, (244, 246, 250)))
        self.assertTrue(any(a == 35 for (_h, a, _v) in self.calls))

    def test_frame_invisible_still_callable(self):
        # 老名字留着，别让别的模块 import 就炸
        self.assertTrue(app_window.frame_invisible(4242))
        self.assertTrue(any(a == 35 for (_h, a, _v) in self.calls))


class NcGuardTest(unittest.TestCase):
    """第 343 项：WM_NCCALCSIZE 归零后，顶部那条带子根本不存在。"""

    def setUp(self):
        self._old_call = app_window._NC_CALL
        self.calls = []
        app_window._NC_CALL = lambda old, h, m, w, l: (
            self.calls.append((m, w)) or 7)

    def tearDown(self):
        app_window._NC_CALL = self._old_call

    def test_zero_nc_when_not_zoomed(self):
        # wParam=TRUE 的 WM_NCCALCSIZE 直接回 0 → 客户区 = 整窗，非客户区厚度 0
        r = app_window._nc_wndproc(0, 0x0083, 1, 0)
        self.assertEqual(0, r)
        self.assertEqual([], self.calls)

    def test_forward_when_zoomed_or_other_msg(self):
        # 别的消息要照常交给原窗口过程，否则窗口直接不可用
        app_window._nc_wndproc(0, 0x000F, 0, 0)          # WM_PAINT
        self.assertEqual(1, len(self.calls))
        app_window._nc_wndproc(0, 0x0083, 0, 0)          # NCCALCSIZE(wParam=0)
        self.assertEqual(2, len(self.calls))

    def test_install_no_hwnd(self):
        self.assertFalse(app_window.install_nc_guard(0))

    def test_install_idempotent(self):
        app_window._NC['ours'].add(1234)
        self.assertTrue(app_window.install_nc_guard(1234))
        self.assertEqual(0, len(self.calls))    # 已经挂过，不再动窗口过程

    def test_frame_insets_no_hwnd(self):
        self.assertEqual(0, app_window.frame_insets(0))

    def test_resize_border_sane(self):
        # 边缘热区按 DPI 算，至少 4px，别宽到把内容挡住
        b = app_window._resize_border(0)
        self.assertTrue(4 <= b <= 24, b)

    def test_child_proc_forwards_without_parent(self):
        self.calls.clear()
        app_window._child_hitproc(0, 0x0084, 0, 0)      # WM_NCHITTEST，没有登记的子窗
        self.assertEqual(1, len(self.calls))


class KeeperFallbackTest(unittest.TestCase):
    """守护线程只在「非客户区还在」时才起（子类化失败的兜底），不是常规路径。"""

    def test_keeper_exists_as_fallback(self):
        self.assertTrue(callable(app_window.start_frame_keeper))
        self.assertTrue(callable(app_window.stop_frame_keeper))

    def test_keeper_not_started_unconditionally(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'app_window.py')
        with open(path, encoding='utf-8') as f:
            src = f.read()
        # 第 343 项之后：顶边归零，caption 带不存在 → 建窗流程里**不许**起轮询线程；
        # 边框色改由 WM_ACTIVATE/WM_SIZE/WM_DPICHANGED/WM_THEMECHANGED 事件补刷。
        body = src[src.index('class App:'):]
        self.assertNotIn('start_frame_keeper()', body)
        self.assertIn('_WM_THEMECHANGED', src)


if __name__ == '__main__':
    unittest.main()
