# -*- coding: utf-8 -*-
"""第 368 项：标题栏最上面拖不出缩放箭头 —— 顶边热区被 WebView2 宿主面板吃掉。

真因：客户区顶边归零后，顶部那 6px 是「客户区」，靠 `_child_hitproc` 回 HTTRANSPARENT
把命中让给父窗；但黑名单里只挂了 Chrome_ / Intermediate D3D，漏了 z 序最上面的
`WindowsForms10.Window.*` 宿主面板 —— 它回 HTCLIENT，父窗的 WM_NCHITTEST 从没被问到，
于是顶部没有缩放热区（左右下还在非客户区，所以只有顶边不行）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app_window


class ChildGuardPrefixTest(unittest.TestCase):
    def test_winforms_host_panel_is_guarded(self):
        # 实测类名形如 WindowsForms10.Window.8.app.0.aec740_r38_ad1
        cls = 'WindowsForms10.Window.8.app.0.aec740_r38_ad1'
        self.assertTrue(cls.startswith(app_window._GUARD_PREFIXES))

    def test_webview_classes_still_guarded(self):
        for cls in ('Chrome_WidgetWin_0', 'Chrome_WidgetWin_1',
                    'Chrome_RenderWidgetHostHWND', 'Intermediate D3D Window'):
            self.assertTrue(cls.startswith(app_window._GUARD_PREFIXES), cls)

    def test_unrelated_windows_are_not_guarded(self):
        for cls in ('MemexClipWatchWnd', 'SecondBrainTrayWnd', 'GDI+ Hook Window Class'):
            self.assertFalse(cls.startswith(app_window._GUARD_PREFIXES), cls)

    def test_guard_loop_registered_once_per_window(self):
        app_window._GUARD_LOOP['parents'].clear()
        app_window.start_child_guard_loop(1234)
        app_window.start_child_guard_loop(1234)      # 第二次不该再起线程
        self.assertEqual({1234}, set(app_window._GUARD_LOOP['parents']))
        self.assertEqual(1, len([t for t in __import__('threading').enumerate()
                                 if t.name == 'child-hit-guard']))


if __name__ == '__main__':
    unittest.main()
