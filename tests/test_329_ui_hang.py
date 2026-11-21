"""跟 UI 线程和 WebView2 控件相关的两条铁律（第 329 项）。"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    return io.open(os.path.join(ROOT, name), encoding='utf-8').read()


class UiThreadSafetyTest(unittest.TestCase):
    def test_force_webview_visible_never_blocks_background_thread(self):
        """托盘线程绝不能直接写 ctrl.Visible：那次就是这么把整个程序挂死的。"""
        src = read('app_window.py')
        seg = src[src.find('def _force_webview_visible'):]
        seg = seg[:seg.find('def _late_revive_mini_loop')]
        self.assertNotIn('ctrl.Visible = True', seg, '后台线程又直接摸控件了')
        self.assertIn('_ui_set_visible', seg)

    def test_ui_set_visible_marshals(self):
        src = read('app_window.py')
        seg = src[src.find('def _ui_set_visible'):]
        seg = seg[:seg.find('def pv_show')]
        self.assertIn('BeginInvoke', seg, '必须走 BeginInvoke 排队，不能阻塞')
        self.assertIn('InvokeRequired', seg)

    def test_ui_hang_watchdog_wired(self):
        src = read('app_window.py')
        self.assertIn('def _ui_hang_watchdog', src)
        self.assertIn('SendMessageTimeoutW', src)
        self.assertIn("name='ui-hang-watch'", src, '看门狗得真的被启动')

    def test_facets_warm_loop_skips_unchanged(self):
        """第 328/329 项：暖缓存每 60 秒重算一遍会把 GIL 攥 2~3 秒。"""
        src = read('brain.py')
        seg = src[src.find('def _facets_warm_loop'):]
        seg = seg[:seg.find('def _compact_loop')]
        self.assertIn('COUNT(*) FROM files', seg)
        self.assertEqual(1, seg.count('facets(rc, {})'))


if __name__ == '__main__':
    unittest.main()
