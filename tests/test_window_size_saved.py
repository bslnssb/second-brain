# -*- coding: utf-8 -*-
"""第 206 项：主窗口尺寸必须尊重用户拖出来的值（不能每次启动被丢掉）。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import app_window as aw
except Exception:
    aw = None


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestMainWindowSize(unittest.TestCase):
    def test_saved_small_narrow_size_is_respected(self):
        """用户拖成 886×836（截图 2）后，下次启动必须还是 886×836。"""
        self.assertEqual((886, 836), aw.main_window_size(2560, 1440, (886, 836)))

    def test_saved_larger_size_is_not_clamped_back(self):
        self.assertEqual((1500, 1100), aw.main_window_size(2560, 1440, (1500, 1100)))

    def test_saved_bad_data_ignored(self):
        w, h = aw.main_window_size(2560, 1440, (600, 300))
        self.assertGreaterEqual(w, 760)
        self.assertGreaterEqual(h, 520)

    def test_default_is_narrow_and_tall(self):
        """新装用户的默认不再是 1306×900 那种宽扁窗口。"""
        w, h = aw.main_window_size(2560, 1440)
        self.assertLessEqual(w, 1150)
        self.assertGreaterEqual(h, 860)
        self.assertLessEqual(w, 2560)
        self.assertLessEqual(h, 1440)

    def test_small_high_dpi_never_exceeds_screen(self):
        """1366×768@150% 的逻辑屏只有 910×512，窗口不能顶出屏幕。"""
        for sw, sh in ((910, 512), (1024, 640), (1280, 720)):
            with self.subTest(screen=(sw, sh)):
                w, h = aw.main_window_size(sw, sh)
                self.assertLessEqual(w, sw)
                self.assertLessEqual(h, sh)
                self.assertLessEqual(h, max(sh - 40, 420))
                minw, minh = aw.main_min_size(w, h)
                self.assertLessEqual(minw, w)
                self.assertLessEqual(minh, h)

    def test_min_size_smaller_than_default(self):
        w, h = aw.main_window_size(2560, 1440)
        minw, minh = aw.main_min_size(w, h)
        self.assertLess(minw, w)
        self.assertLess(minh, h)


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestSWPFlags(unittest.TestCase):
    """第 229 项七轮：SWP 旗标必须是模块级且值正确 —— _follow_anchor 引用了
    SWP_ASYNCWINDOWPOS 而没人定义它的 NameError 被 SB_DEBUG 门控吞了三天。"""

    def test_flags_exist_and_values(self):
        self.assertEqual(0x1, aw.SWP_NOSIZE)
        self.assertEqual(0x4, aw.SWP_NOZORDER)
        self.assertEqual(0x10, aw.SWP_NOACTIVATE)
        self.assertEqual(0x4000, aw.SWP_ASYNCWINDOWPOS)


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestRememberSize(unittest.TestCase):
    """第 252 项：主窗尺寸保存完整覆盖 —— 拖动实时保存、右键退出不丢失、最大化不覆盖。"""

    def test_remember_size_saves_to_settings(self):
        from unittest.mock import MagicMock, patch

        class DummyApp:
            def __init__(self):
                self.hwnd_main = None
                self.main = MagicMock()
                self.main.width = 1050
                self.main.height = 850
                self._main_w = 980
                self._main_h = 880

            _remember_size = aw.App._remember_size

        app = DummyApp()
        saved = {}
        with patch('brain._settings_mark', side_effect=lambda k, v: saved.update({k: v})):
            app._remember_size()
        self.assertEqual(1050, saved.get('win_w'))
        self.assertEqual(850, saved.get('win_h'))
        self.assertEqual(1050, app._main_w)
        self.assertEqual(850, app._main_h)

    def test_remember_size_skips_when_maximized(self):
        from unittest.mock import MagicMock, patch

        class DummyApp:
            def __init__(self):
                self.hwnd_main = 12345
                self.main = MagicMock()
                self.main.width = 2560
                self.main.height = 1440

            _remember_size = aw.App._remember_size

        app = DummyApp()
        mock_u = MagicMock()
        mock_u.IsZoomed.return_value = 1
        mock_u.IsIconic.return_value = 0
        saved = {}
        with patch.object(aw, '_win', return_value=(mock_u, MagicMock())):
            with patch('brain._settings_mark', side_effect=lambda k, v: saved.update({k: v})):
                app._remember_size()
        self.assertEqual({}, saved)


if __name__ == '__main__':
    unittest.main()
