# -*- coding: utf-8 -*-
"""第 267 项：托盘「设置 / 关于 / 反馈与报错」点了没反应、详情栏关了又自己弹出来。

为什么以前没抓到：这两件事都只在「页面重载后 pywebview 的 _pywebviewready
事件没被置回」时才出现。单测各自测纯函数，跨进程的窗口事件门闩没人验；
真机日志（debug.log 13:14:32 → 13:14:52）里是 20 秒整的超时 + 每次点击
20 秒后又 show() 一次，才把症状串起来。

这里钉死三条不变量：
1. 面板导航/显示/隐藏必须走后端直连（pv_load_url / pv_show / pv_hide），
   不许再走 Window.load_url / evaluate_js —— 那两个都被 20 秒门闩套着；
2. 用户关窗必须把 `_hide_seq` +1，在飞的 show_main 看到序号变了就放弃；
3. 门闩被清空、但页面其实活着时，pv_heal_gate 必须把事件补回来。
"""
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import app_window as aw
except Exception:                       # 无 pywebview 环境时跳过
    aw = None


class _FakeEvent:
    def __init__(self, set_=False):
        self._set = bool(set_)

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def clear(self):
        self._set = False


class _FakeWin:
    """记录 pywebview 那层被调用了没有 —— 调了就是走回门闩。"""

    def __init__(self, uid='main'):
        self.uid = uid
        self.real_url = ''
        self.calls = []
        self.events = type('E', (), {})()
        self.events._pywebviewready = _FakeEvent(False)

    def show(self):
        self.calls.append('show')

    def hide(self):
        self.calls.append('hide')

    def load_url(self, url):
        self.calls.append('load_url')

    def evaluate_js(self, script):
        self.calls.append('eval')
        return None


class _FakeBackend:
    """winforms 后端替身：和 pywebview 内部同一套 uid 接口。"""

    class BrowserView:
        instances = {}

    def __init__(self):
        self.calls = []
        self.BrowserView.instances = {}       # 后端里真注册过的窗口 uid

    def register(self, uid='main'):
        self.BrowserView.instances[uid] = object()
        return self

    def show(self, uid):
        self.calls.append(('show', uid))

    def hide(self, uid):
        self.calls.append(('hide', uid))

    def load_url(self, url, uid):
        self.calls.append(('load_url', url))

    def evaluate_js(self, script, uid, parse_json):
        self.calls.append(('eval', script))
        if 'readyState' in script:
            return 'complete'
        return None


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestBackendBypassesGate(unittest.TestCase):
    def setUp(self):
        self.win = _FakeWin()
        self.be = _FakeBackend().register('main')

    def _patch(self):
        return mock.patch.object(aw, '_pv_backend', lambda: self.be)

    def test_pv_load_url_goes_to_backend_not_pywebview(self):
        with self._patch():
            ok = aw.pv_load_url(self.win, 'http://127.0.0.1:8765/web/?panel=settings')
        self.assertTrue(ok)
        self.assertEqual([c for c in self.be.calls if c[0] == 'load_url'],
                         [('load_url', 'http://127.0.0.1:8765/web/?panel=settings')])
        self.assertEqual([], self.win.calls, '不许再走 Window.load_url（它清 ready 事件）')

    def test_pv_show_and_hide_use_backend(self):
        with self._patch():
            self.assertTrue(aw.pv_show(self.win))
            self.assertTrue(aw.pv_hide(self.win))
        self.assertEqual([('show', 'main'), ('hide', 'main')], self.be.calls)
        self.assertEqual([], self.win.calls)

    def test_pv_eval_uses_backend(self):
        with self._patch():
            aw.pv_eval(self.win, '1+1')
        self.assertEqual([('eval', '1+1')], self.be.calls)
        self.assertEqual([], self.win.calls)

    def test_falls_back_to_pywebview_when_no_backend(self):
        with mock.patch.object(aw, '_pv_backend', lambda: None):
            aw.pv_show(self.win)
        self.assertEqual(['show'], self.win.calls)


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestGateHeal(unittest.TestCase):
    def test_heals_when_page_alive(self):
        win = _FakeWin()
        be = _FakeBackend().register('main')
        with mock.patch.object(aw, '_pv_backend', lambda: be):
            ok = aw.pv_heal_gate(win, tries=3, interval=0.01)
        self.assertTrue(ok)
        self.assertTrue(win.events._pywebviewready.is_set())

    def test_does_not_heal_when_page_not_alive(self):
        win = _FakeWin()
        be = _FakeBackend().register('main')
        be.evaluate_js = lambda *a, **k: None      # 页面没响应
        with mock.patch.object(aw, '_pv_backend', lambda: be):
            ok = aw.pv_heal_gate(win, tries=2, interval=0.01)
        self.assertFalse(ok)
        self.assertFalse(win.events._pywebviewready.is_set())


class _BareApp:
    """绕过 __init__ 造一个只带本次测试需要字段的 App 壳。"""

    @staticmethod
    def make():
        app = aw.App.__new__(aw.App)
        app.url = 'http://127.0.0.1:8765'
        app.main = _FakeWin()
        app.mini = _FakeWin('mini')
        app.hwnd_main = 1234
        app._hide_seq = 0
        app._want_main = False
        app._alive = True
        app._show_lock = threading.Lock()
        app._main_loaded = True
        app._main_target = '/web/'
        app.tray = None
        app._remember_size = lambda: None
        app._fit_main_size = lambda initial=False: None
        app._force_webview_visible = lambda: None
        app.show_mini = lambda: None
        app._hint_once = lambda: None
        return app


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestHideBumpsSeq(unittest.TestCase):
    def test_hide_main_clears_want_and_bumps_seq(self):
        app = _BareApp.make()
        app._want_main = True
        with mock.patch.object(aw, 'pv_hide', lambda w: True):
            app.hide_main()
        self.assertFalse(app._want_main)
        self.assertEqual(1, app._hide_seq)

    def test_on_main_closing_bumps_seq(self):
        app = _BareApp.make()
        app._want_main = True
        # 托盘没起来 → 真退出，但序号照样要作废在飞的 show_main
        self.assertTrue(app._on_main_closing())
        self.assertEqual(1, app._hide_seq)

    def test_safe_hide_bumps_seq(self):
        app = _BareApp.make()
        with mock.patch.object(aw, 'pv_hide', lambda w: True):
            app._safe_hide_main()
        self.assertEqual(1, app._hide_seq)


@unittest.skipIf(aw is None, 'app_window 导入失败')
class TestShowMainGuards(unittest.TestCase):
    def test_skips_when_another_show_in_flight(self):
        app = _BareApp.make()
        called = []
        app._show_main_once = lambda *a, **k: called.append(1)
        app._show_lock.acquire()
        try:
            threading.Thread(target=app.show_main, kwargs={'panel': 'settings'},
                             daemon=True).start()
            time.sleep(0.05)
        finally:
            app._show_lock.release()
        self.assertEqual([], called, '已有一次在飞时不能再起第二条显示线程')

    def test_aborts_navigation_when_user_closed_meanwhile(self):
        """显示途中用户点了关闭：不能再导航、不能把窗口重新顶出来。"""
        app = _BareApp.make()
        loaded = []

        def fake_show(w):
            app._hide_seq += 1        # 模拟「窗口刚 show 出来，用户就点了关闭」
            return True

        patches = [
            mock.patch.object(aw, 'pv_show', fake_show),
            mock.patch.object(aw, 'pv_load_url', lambda w, u: loaded.append(u) or True),
            mock.patch.object(aw, 'find_hwnd', lambda *a, **k: 1234),
            mock.patch.object(aw, '_is_visible', lambda h: True),
            mock.patch.object(aw, 'dark_caption', lambda *a, **k: None),
            mock.patch.object(aw, 'paint_frame', lambda *a, **k: None),
            mock.patch.object(aw, 'notify_resize', lambda *a, **k: None),
            mock.patch.object(aw, 'revive_webview', lambda *a, **k: None),
            mock.patch.object(aw, 'topmost', lambda *a, **k: True),
            mock.patch.object(aw, '_late_revive_loop', lambda *a, **k: None),
        ]
        for p in patches:
            p.start()
        try:
            app.show_main(panel='settings')
        finally:
            for p in patches:
                p.stop()
        self.assertEqual([], loaded, '关窗之后不许再导航')
        self.assertEqual('/web/', app._main_target, '目标不许被改成新面板')


if __name__ == '__main__':
    unittest.main()
