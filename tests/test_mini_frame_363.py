# -*- coding: utf-8 -*-
"""第 363 项：快搜窗那圈白边的回归测试（静态断言）。

真机像素验收要启动 GUI，见 tmp\\shot363.py / tmp\\mini_ring_363.py。
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / 'app_window.py'
SRC = APP.read_text(encoding='utf-8')


class TestMiniFrame363(unittest.TestCase):
    def test_backcolor_and_frame_redraw(self):
        seg = SRC.split('def set_window_background')[1].split('def ')[0]
        self.assertIn('RedrawWindow', seg)
        self.assertIn('0x0001 | 0x0004 | 0x0100', SRC)
        self.assertIn('native.BackColor = col', SRC)
        self.assertIn("background_color=ui_bg_hex(),", SRC)

    def test_wm_size_repaints_frame(self):
        wm_size = SRC.split('if msg in (_WM_ACTIVATE, _WM_SIZE, _WM_DPICHANGED, _WM_THEMECHANGED)')[1][:900]
        self.assertIn('if msg == _WM_SIZE', wm_size)
        self.assertIn('RedrawWindow', wm_size)

    def test_both_windows_follow_theme(self):
        self.assertIn("set_window_background(getattr(_app, 'mini', None), _rgb)", SRC)
        self.assertIn("set_window_background(getattr(_app, 'main', None), _rgb)", SRC)
        self.assertIn("set_window_background(self.mini, _LAST_THEME.get", SRC)

    def test_helpers_not_deleted(self):
        self.assertIn('def _resize_border', SRC)
        self.assertIn('def _frame_insets', SRC)
        # 「客户区铺满整窗」那版会打乱布局，已整体移除，别又回来
        self.assertNotIn('mark_full_client', SRC)
        self.assertNotIn('_FULL_CLIENT', SRC)

    def test_matte_frame_mechanism(self):
        """DWM 那圈 11px 非客户区不在网页里，只能原生自己刷。"""
        self.assertIn('def matte_frame', SRC)
        self.assertIn('_MATTE[key] = _colorref', SRC)
        # 撤玻璃，否则那圈透出背后的窗口（用户看到的白条就是背后的白窗口）
        self.assertIn('_MARGINS(0, 0, 0, 0)', SRC)
        self.assertIn('DwmExtendFrameIntoClientArea(ctypes.c_void_p(key), ctypes.byref(m))', SRC)
        # pywebview 建窗时把 NCRENDERING 关了 → 不发 WM_NCPAINT，必须改回来
        self.assertIn('DWMWA_NCRENDERING_POLICY', SRC)
        # 最外 2px 是 DWM 自己的边框色，自绘盖不到
        self.assertIn('DWMWA_BORDER_COLOR', SRC)
        self.assertIn('if msg == _WM_NCPAINT and int(hwnd) in _MATTE', SRC)

    def test_paint_uses_user32_fillrect_and_local_coords(self):
        self.assertIn('def _paint_matte_frame', SRC)
        # FillRect 是 user32 的导出函数；写成 gdi32 会 AttributeError 被静默吞掉（踩过）
        self.assertIn('u.FillRect(', SRC)
        self.assertNotIn('g.FillRect(', SRC)
        # GetWindowDC 的原点是窗口左上角 → 必须用窗口内坐标
        self.assertIn('wt.RECT(0, 0, b, wh)', SRC)
        self.assertIn('wt.RECT(ww - b, 0, ww, wh)', SRC)

    def test_recolor_repaints_immediately(self):
        seg = SRC.split('def matte_frame')[1][:2000]
        self.assertIn('RedrawWindow', seg)
        self.assertIn('_paint_matte_frame(key)', seg)

    def test_mini_page_reports_own_bg(self):
        self.assertIn('syncMiniDark', (ROOT / 'brain.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
