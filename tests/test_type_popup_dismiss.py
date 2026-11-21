# -*- coding: utf-8 -*-
"""第 244 项：文件夹里打字唤出的快搜窗 —— 不置顶 + 点空白处收起。

**用户两条原话（截图反馈）**
1. 「在 windows 文件夹里弹出来的简要搜索框应该跟 windows 文件夹一样，
   不能跳到最高，其他的移动过去，还会被这个简要搜索栏挡住了。」
2. 「我停止搜索并点击了一下文件夹的空白处，这个简要搜索栏就应该要消失，
   而不是一直在那里占地方，有需要的时候我再随便打字他再出来。」

**为什么以前没抓到**（两个真因叠在一起）
· `show_mini()` 无条件调 `topmost(hwnd)` → 设 HWND_TOPMOST，快搜窗永远钉在
  所有程序前面。切到别的窗口、把别的窗口移过来，还是被它挡着。
· `on_mini_blur()` 第 205 项为**文件对话框吸附**做了「dock 模式失焦一律
  不收起」—— 打字唤出也走 `dock=True`，于是点了文件夹空白处小窗赖着不走。

修法：按**来源**分流。`origin='type'`（打字）→ 不置顶（`topmost(pin=False)`）、
失焦即收起；`origin='dialog'` / 热键 / 托盘 → 保持第 205 项原行为。
另外 `_auto_loop` 每 0.15s 用纯函数兜一次底 —— WebView2 跨进程切焦点时
偶尔不发 `window.blur`，只靠前端事件会「假绿」。

跑法：py -3.12 -m unittest tests.test_type_popup_dismiss -v
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app_window  # noqa: E402

App = app_window.App


def make_app(**kw):
    """不跑 __init__（会开真窗口），只造一个够用的壳。"""
    a = App.__new__(App)
    a.hwnd_mini = 12345
    a.mini = mock.MagicMock()
    a.mini_visible = False
    a._mini_mode = None
    a._mini_origin = ''
    a._type_guard = 0.0
    a._auto_dlg = 0
    a._auto_muted = {}
    a._dock = None
    a._alive = False
    for k, v in kw.items():
        setattr(a, k, v)
    return a


class TestTypeMiniHideRule(unittest.TestCase):
    """_auto_loop 的兜底判据（纯函数打表）。"""

    def test_foreground_is_self_keeps_open(self):
        self.assertFalse(App._type_mini_should_hide(
            'type', True, 0, True), '还在快搜窗里打字，不许收起')

    def test_click_blank_area_hides(self):
        self.assertTrue(App._type_mini_should_hide(
            'type', True, 0, False), '点了文件夹空白处，前台不再是自己 → 收起')

    def test_initialising_is_protected(self):
        self.assertFalse(App._type_mini_should_hide(
            'type', True, time.time() + 10, False),
            'WebView2 起焦点 + replay 按键的初始化期，blur 是副作用')

    def test_dialog_dock_never_autohides(self):
        self.assertFalse(App._type_mini_should_hide(
            'dialog', True, 0, False), '第 205 项：文件对话框吸附失焦不收起')

    def test_hotkey_and_tray_never_autohide(self):
        for origin in ('', 'hotkey', 'tray'):
            self.assertFalse(App._type_mini_should_hide(
                origin, True, 0, False))

    def test_not_visible_keeps_quiet(self):
        self.assertFalse(App._type_mini_should_hide('type', False, 0, False))


class TestShowMiniOrigin(unittest.TestCase):
    """show_mini 的 Z 序：只有打字唤出不置顶，其它来源照旧。"""

    def _patch(self, a, origin, near=999, dock=True):
        with mock.patch.object(app_window, 'topmost', return_value=True) as tm, \
                mock.patch.object(app_window, '_log_always'), \
                mock.patch.object(app_window, 'foreground_desc', return_value=''), \
                mock.patch.object(App, 'place_mini_near'), \
                mock.patch.object(App, 'place_mini'):
            a.show_mini(near_wnd=near, dock=dock, origin=origin)
        return tm

    def test_type_origin_is_not_topmost(self):
        a = make_app(_auto_dlg=777)
        tm = self._patch(a, 'type')
        self.assertTrue(tm.called)
        self.assertIs(tm.call_args.kwargs.get('pin'), False,
                      '打字唤出必须 HWND_NOTOPMOST，不能钉在所有窗口前面')
        self.assertEqual(a._mini_origin, 'type')
        self.assertEqual(a._auto_dlg, 0,
                         '不能继承上次的对话框状态，否则 hide 时会把焦点抢回旧对话框')
        self.assertGreater(a._type_guard, time.time())

    def test_docked_origins_are_not_topmost(self):
        """文件对话框吸附或打字唤出：绝不置顶（pin=False），其他窗口移过来可以遮挡。"""
        for origin in ('type', 'dialog', ''):
            a = make_app()
            tm = self._patch(a, origin, near=999, dock=True)
            self.assertIs(tm.call_args.kwargs.get('pin'), False, origin)

    def test_standalone_origins_stay_topmost(self):
        """双击 Ctrl 热键 / 托盘唤出的大窗：保持置顶（pin=True）。"""
        for origin in ('hotkey', 'tray', ''):
            a = make_app()
            tm = self._patch(a, origin, near=0, dock=False)
            self.assertIs(tm.call_args.kwargs.get('pin'), True, origin)

    def test_topmost_failure_retry_keeps_pin(self):
        """抢焦点失败会 hide/show 重试，重试那次也不能把窗口钉回最顶。"""
        a = make_app()
        with mock.patch.object(app_window, 'topmost', return_value=False) as tm, \
                mock.patch.object(app_window, '_log_always'), \
                mock.patch.object(app_window, 'foreground_desc', return_value=''), \
                mock.patch.object(App, 'place_mini_near'), \
                mock.patch.object(App, 'place_mini'):
            a.show_mini(near_wnd=999, dock=True, origin='type')
        self.assertEqual(tm.call_count, 2)
        for c in tm.call_args_list:
            self.assertIs(c.kwargs.get('pin'), False)


class TestBlurDismiss(unittest.TestCase):
    """点文件夹空白处（前端 blur）→ 真收起；对话框吸附不受影响。"""

    def test_type_blur_hides(self):
        a = make_app(mini_visible=True, _mini_origin='type',
                     _dock={'hwnd': 999}, _type_guard=0.0)
        with mock.patch.object(app_window, '_log'):
            self.assertTrue(a.on_mini_blur())
        self.assertFalse(a.mini_visible)
        a.mini.hide.assert_called_once()

    def test_guard_blocks_init_blur(self):
        a = make_app(mini_visible=True, _mini_origin='type',
                     _dock={'hwnd': 999}, _type_guard=time.time() + 100)
        with mock.patch.object(app_window, '_log'):
            self.assertFalse(a.on_mini_blur())
        self.assertTrue(a.mini_visible)

    def test_dialog_dock_still_keeps_open(self):
        """第 205 项不能回归：点对话框文件名框 / 拖对话框都不收起。"""
        a = make_app(mini_visible=True, _mini_origin='dialog',
                     _dock={'hwnd': 999})
        with mock.patch.object(app_window, '_log'):
            self.assertFalse(a.on_mini_blur())
        self.assertTrue(a.mini_visible)

    def test_guard_cleared_after_replay(self):
        """打字送完键后 guard 归零 —— 否则点空白处会被初始化保护期挡住。"""
        a = make_app(_type_guard=time.time() + 100)
        tw = mock.MagicMock()
        tw.release.return_value = []
        a.typewatch = tw
        a._mini_origin = ''
        with mock.patch.object(App, '_type_active', return_value=True), \
                mock.patch.object(App, 'show_mini'), \
                mock.patch.object(App, '_mini_params', return_value={}), \
                mock.patch.object(app_window, 'foreground_is', return_value=True), \
                mock.patch.object(app_window.quick_switch, 'folder_for', return_value='D:\\x'), \
                mock.patch.object(app_window, '_log'):
            a._on_type({'hwnd': 999})
        self.assertEqual(a._type_guard, 0.0)


class TestWiring(unittest.TestCase):
    """代码在 ≠ 真接上：钉调用形态（跨层问题最容易两边各自绿）。"""

    def test_on_type_passes_origin(self):
        src = (ROOT / 'app_window.py').read_text(encoding='utf-8')
        self.assertIn("self.show_mini(near_wnd=hwnd, dock=True, origin='type')", src)

    def test_auto_loop_has_poll_fallback(self):
        src = (ROOT / 'app_window.py').read_text(encoding='utf-8')
        self.assertIn('App._type_mini_should_hide(', src)
        self.assertIn("'type mini: 前台已不是自己 → 收起'", src)

    def test_show_mini_uses_pin_kwarg(self):
        src = (ROOT / 'app_window.py').read_text(encoding='utf-8')
        self.assertIn("pin = (origin != 'type')", src)
        self.assertIn('topmost(self.hwnd_mini, pin=pin)', src)

    def test_old_unconditional_topmost_is_gone(self):
        """旧写法 `if not topmost(self.hwnd_mini):` 一旦复活，置顶就回来了。"""
        src = (ROOT / 'app_window.py').read_text(encoding='utf-8')
        self.assertNotIn('if not topmost(self.hwnd_mini):', src)


class TestDockZOrderSync(unittest.TestCase):
    """第 257/258 项：吸附快搜窗在对话框活跃/拖动时保持在非置顶层顶端（不躲在其他文件夹下面）。"""

    def test_active_dialog_follow_uses_hwnd_top(self):
        import ctypes
        from ctypes import wintypes
        a = make_app(mini_visible=True, hwnd_mini=12345,
                     _dock={'hwnd': 999, 'rect': (10, 10, 100, 100), 'mode': 'dlg', 'w': 200, 'h': 200},
                     _drag_move=True)
        a._follow_lock = mock.MagicMock()
        a._follow_lock.acquire.return_value = True
        a._rescale_lock = mock.MagicMock()
        a._rescale_lock.acquire.return_value = False
        a._rescale_ts = 0.0
        fake_u = mock.MagicMock()
        fake_u.IsWindow.return_value = True

        def fake_gwr(h, ptr):
            r = wintypes.RECT(20, 20, 110, 110)
            ctypes.memmove(ptr, ctypes.byref(r), ctypes.sizeof(r))
            return True

        fake_u.GetWindowRect.side_effect = fake_gwr

        with mock.patch.object(app_window, '_win', return_value=(fake_u, wintypes)), \
                mock.patch.object(app_window.quick_switch, 'is_foreground_dialog', return_value=True), \
                mock.patch.object(app_window, '_log'):
            a._follow_anchor()

        self.assertTrue(fake_u.SetWindowPos.called)
        args, _ = fake_u.SetWindowPos.call_args
        insert_after = args[1]
        self.assertIsInstance(insert_after, ctypes.c_void_p, '活跃/拖动时传 c_void_p(0) HWND_TOP')
        flags = args[6]
        self.assertEqual(flags & 0x4, 0, '活跃/拖动时不能加 SWP_NOZORDER，必须浮在顶端')


if __name__ == '__main__':
    unittest.main()
