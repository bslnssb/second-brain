# -*- coding: utf-8 -*-
"""DPI / 窗口尺寸换算的回归测试（2026-11-07 白屏事件的后续）。

背景：白屏 bug 只在「缩放 ≠ 100%」时才触发，因为老代码里 `if dpr == 1.0: return`
把 100% 的机器整段跳过了。于是绝大多数用户走的那条路反而从来没被测过，而少数
用户走的路又是坑。这里的测试要锁住三件事：

  1. 换算在 96 / 120 / 144 / 168 / 192 / 240 六档缩放下都对（1K/2K/4K 通用）；
  2. `dpr == 1`（也就是 100% 缩放，绝大多数用户）时 CSS 视口正好等于设计尺寸；
  3. 校正窗口尺寸**不会把隐藏的窗口显示出来** —— 这就是白屏的根因，也是
     100% 的机器上如果哪天要走进这条兜底路径时必须成立的性质。

跑法：python -m unittest discover -s tests -p "test_*.py" -v
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import unittest

try:
    import app_window as aw
except Exception as e:      # pragma: no cover - 缺依赖的机器上跳过
    aw = None
    _IMPORT_ERR = e

# WS_OVERLAPPEDWINDOW：和主窗口同族（带标题栏、可缩放、有边框）
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_POPUP = 0x80000000

# 六档常见缩放：100% / 125% / 150% / 175% / 200% / 250%
SCALES = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]

# 「分辨率 × 缩放 → 逻辑屏幕」的常见组合
SCREEN_CASES = [
    ('1920x1080 @100%', 1920, 1080, 1.0),
    ('1920x1080 @125%', 1920, 1080, 1.25),
    ('2560x1440 @100%', 2560, 1440, 1.0),
    ('2560x1440 @125%', 2560, 1440, 1.25),
    ('2560x1440 @150%', 2560, 1440, 1.5),
    ('3840x2160 @100%', 3840, 2160, 1.0),
    ('3840x2160 @150%', 3840, 2160, 1.5),
    ('3840x2160 @200%', 3840, 2160, 2.0),
]


@unittest.skipIf(aw is None, 'app_window 导入失败：%r' % (globals().get('_IMPORT_ERR'),))
class TestNeedsFit(unittest.TestCase):
    """要不要动窗口的判定。这是「100% 用户零风险」的核心：判定必须稳定返回 False。"""

    def test_100_percent_never_fits(self):
        # 100% 缩放：视口只比设计少一圈边框（实测宽 15 / 高 37）
        self.assertFalse(aw.needs_fit(1265, 855, 1280, 892))
        self.assertFalse(aw.needs_fit(1264, 853, 1280, 892))
        self.assertFalse(aw.needs_fit(1180, 720, 1180, 720))

    def test_150_percent_healthy_never_fits(self):
        # 150% 缩放、pywebview 换算正常时的实测值
        self.assertFalse(aw.needs_fit(1265, 855, 1280, 892))

    def test_broken_scale_is_detected(self):
        # 旧代码那种「窗口没被 DPI 放大」的坏情况：843 = 1265/1.5
        self.assertTrue(aw.needs_fit(843, 569, 1280, 892))
        self.assertTrue(aw.needs_fit(1024, 700, 1280, 892))
        self.assertTrue(aw.needs_fit(1920, 1338, 1280, 892))

    def test_tiny_window_is_detected(self):
        # 4K@200% 若换算反了（乘两次），视口会掉成设计的一半
        self.assertTrue(aw.needs_fit(640, 446, 1280, 892))


@unittest.skipIf(aw is None, 'app_window 导入失败：%r' % (globals().get('_IMPORT_ERR'),))
class TestCalcWindowSize(unittest.TestCase):
    def test_all_scales_add_only_frame(self):
        """整窗必须比客户区大（标题栏+边框），但不该大得离谱。"""
        for dpr in SCALES:
            with self.subTest(dpr=dpr):
                w, h = aw.calc_window_size(1280, 892, dpr, WS_OVERLAPPEDWINDOW, 0)
                self.assertGreaterEqual(w, int(1280 * dpr))
                self.assertGreaterEqual(h, int(892 * dpr))
                # 边框+标题栏按物理像素算，150% 下约 39*1.5；给 120*dpr 的宽松上限
                self.assertLessEqual(w - 1280 * dpr, 120 * dpr)
                self.assertLessEqual(h - 892 * dpr, 120 * dpr)

    def test_popup_window_has_thin_frame(self):
        """无边框窗口（快搜窗那种）只差一圈细边，不该按标题栏算。"""
        w, h = aw.calc_window_size(760, 500, 1.5, WS_POPUP, 0)
        self.assertAlmostEqual(w, 760 * 1.5, delta=20)
        self.assertAlmostEqual(h, 500 * 1.5, delta=20)

    def test_hundred_percent_is_exact(self):
        """dpr==1（绝大多数用户）：换算必须只加边框，不做任何缩放。"""
        for cw, ch in [(1180, 720), (1280, 892), (1400, 900)]:
            with self.subTest(size=(cw, ch)):
                w, h = aw.calc_window_size(cw, ch, 1.0, WS_OVERLAPPEDWINDOW, 0)
                self.assertGreaterEqual(w, cw)
                self.assertGreaterEqual(h, ch)
                self.assertLess(w - cw, 60)
                self.assertLess(h - ch, 60)

    def test_screen_matrix_yields_sane_window(self):
        """1K/2K/4K × 各档缩放：算出来的窗口既不超出屏幕，也不是一小块。"""
        for name, sw, sh, dpr in SCREEN_CASES:
            with self.subTest(screen=name):
                lw, lh = int(sw / dpr), int(sh / dpr)     # 逻辑屏幕
                mw, mh = aw.main_window_size(lw, lh)
                self.assertLessEqual(mw, lw)
                self.assertLessEqual(mh, lh)
                self.assertGreaterEqual(mw, 760)
                self.assertGreaterEqual(mh, 520)
                # 窗口的 CSS 视口不该比逻辑屏幕还宽（否则内容被挤出屏幕）
                self.assertLessEqual(mw, lw)


class TestHiddenWindowResize(unittest.TestCase):
    """核心回归：校正尺寸不能把隐藏的窗口显示出来（这就是白屏）。"""

    def setUp(self):
        if aw is None:
            self.skipTest('app_window 导入失败')
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        self.u.CreateWindowExW.restype = ctypes.c_void_p
        self.u.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        # 建一个隐藏的普通窗口（不 ShowWindow → 从一开始就是不可见的）
        self.hwnd = self.u.CreateWindowExW(
            0, 'STATIC', 'dpi-fit-probe', WS_OVERLAPPEDWINDOW,
            40, 40, 400, 300, None, None, None, None)
        if not self.hwnd:
            self.skipTest('CreateWindowExW 失败，无法测隐藏窗口行为')

    def tearDown(self):
        if getattr(self, 'hwnd', None):
            self.u.DestroyWindow(ctypes.c_void_p(self.hwnd))

    def test_resize_keeps_window_hidden(self):
        before = bool(self.u.IsWindowVisible(ctypes.c_void_p(self.hwnd)))
        self.assertFalse(before, '探针窗口不该是可见的')
        ok = aw.set_client_size(self.hwnd, 900, 640)
        self.assertTrue(ok)
        after = bool(self.u.IsWindowVisible(ctypes.c_void_p(self.hwnd)))
        self.assertFalse(after, 'set_client_size 把隐藏窗口显示出来了 —— 这会复现白屏')

    def test_client_size_lands_on_target(self):
        """客户区要落在真实 DPI 的像素上（误差 ≤ 2px）。"""
        dpr = aw.window_dpr(self.hwnd)
        aw.set_client_size(self.hwnd, 900, 640)
        cw, ch = aw.get_client_size(self.hwnd)
        self.assertAlmostEqual(cw, 900 * dpr, delta=2)
        self.assertAlmostEqual(ch, 640 * dpr, delta=2)

    def test_notify_resize_keeps_window_hidden(self):
        aw.notify_resize(self.hwnd)
        self.assertFalse(bool(self.u.IsWindowVisible(ctypes.c_void_p(self.hwnd))))

    def test_unaware_thread_looks_like_100_percent(self):
        """切到 DPI-unaware 线程再**新建**一个窗口，等价于 100% 缩放的机器。

        这里必须新建窗口：GetDpiForWindow 返回的是「窗口」的 DPI，跟当前线程的
        awareness context 无关。只有在一个 unaware 线程里创建的窗口，它自己的 DPI
        才是 96 —— 那才和纯 100% 用户走的算术路径完全一致。

        （2026-11-07 之前这条测试能过，是因为整个进程其实一直是 DPI-unaware 的 bug
        状态，老窗口本来就被虚拟化成 96；进程修成真正的 PMv2 之后就不成立了。）
        """
        u = self.u
        if not hasattr(u, 'SetThreadDpiAwarenessContext'):
            self.skipTest('系统不支持 SetThreadDpiAwarenessContext')
        u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        old = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-1))   # UNAWARE
        h = None
        try:
            h = u.CreateWindowExW(0, 'STATIC', 'dpi-unaware-probe',
                                  WS_OVERLAPPEDWINDOW,
                                  60, 60, 300, 200, None, None, None, None)
            if not h:
                self.skipTest('unaware 窗口创建失败')
            self.assertAlmostEqual(aw.window_dpr(h), 1.0, delta=0.001)
            aw.set_client_size(h, 900, 640)
            cw, ch = aw.get_client_size(h)
            self.assertAlmostEqual(cw, 900, delta=2)
            self.assertAlmostEqual(ch, 640, delta=2)
            self.assertFalse(bool(u.IsWindowVisible(ctypes.c_void_p(h))))
        finally:
            if h:
                u.DestroyWindow(ctypes.c_void_p(h))
            u.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestDprHelpers(unittest.TestCase):
    def test_dpr_is_sane(self):
        dpr = aw.window_dpr(None)
        self.assertGreaterEqual(dpr, 1.0)
        self.assertLessEqual(dpr, 4.0)

    def test_screen_logical_size_never_zero(self):
        w, h = aw.screen_logical_size()
        self.assertGreater(w, 320)
        self.assertGreater(h, 240)


if __name__ == '__main__':
    unittest.main()
