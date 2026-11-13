# -*- coding: utf-8 -*-
"""自绘标题栏开关（第 180 项）：/api/ping 报的必须是「窗口实际状态」。

为什么单独盯这一条：窗口样式只在启动时改，配置值随时可变。若前端照配置画、
窗口还是旧样式，就会出现**两条标题栏叠着**（配置已改）或**一条都没有**
（配置关了但系统标题栏已摘）。所以 TITLEBAR_ACTIVE 一旦有值就必须压过配置。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import brain


class TestTitlebarFlag(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self._orig = brain.TITLEBAR_ACTIVE

    def tearDown(self):
        brain.TITLEBAR_ACTIVE = self._orig
        self._td.cleanup()

    def _cfg(self, **kw):
        (self.dir / 'settings.json').write_text(
            json.dumps(kw), encoding='utf-8')

    def _ask(self):
        with mock.patch.object(brain, 'app_dir', lambda: self.dir):
            return brain.titlebar_on()

    def test_active_state_wins_over_config(self):
        """窗口真实状态压过配置：配置说开、窗口其实没摘=不画。"""
        self._cfg(titlebar=True)
        brain.TITLEBAR_ACTIVE = False
        self.assertFalse(self._ask())

    def test_active_state_on_even_if_config_off(self):
        """反向同理：窗口已摘、配置刚被改成关 —— 仍要画，否则死窗口。"""
        self._cfg(titlebar=False)
        brain.TITLEBAR_ACTIVE = True
        self.assertTrue(self._ask())

    def test_fallback_to_config_before_window_ready(self):
        """窗口还没起来（None）= 按配置走。"""
        brain.TITLEBAR_ACTIVE = None
        self._cfg(titlebar=False)
        self.assertFalse(self._ask())

    def test_default_on_when_settings_missing(self):
        """配置读不到也当开：默认体验是自绘标题栏，不能因缺文件变原生。"""
        brain.TITLEBAR_ACTIVE = None
        self.assertTrue(self._ask())


if __name__ == '__main__':
    unittest.main()


class TestTitlebarWin32(unittest.TestCase):
    """标题栏拖动链路（第 182/183 项）：必须真的把坐标传给 SetWindowPos。

    为什么要有这组：上一版漏定义了 `_SWP_NOSIZE` 等标志位，`titlebar_move` 每次
    都抛 NameError，又被 except 吞成 False —— 日志一片正常、窗口一动不动，
    用户只能说「还是拖不动」。所以这里直接调方法，**让 NameError 变成测试红**。
    """

    def _api(self):
        import app_window as aw
        return aw, aw.Api.__new__(aw.Api)

    def test_move_passes_coords_and_flags(self):
        aw, obj = self._api()
        seen = {}

        class U:
            def SetWindowPos(self, h, after, x, y, cx, cy, flags):
                seen['x'], seen['y'], seen['flags'] = x, y, flags
                return 1

        import ctypes
        with mock.patch.object(aw, 'find_hwnd', return_value=0x1234), \
             mock.patch.object(aw, '_win_ptr_funcs',
                               return_value=(ctypes, U(), None, None)):
            self.assertTrue(obj.titlebar_move(120, 240))
        self.assertEqual((120, 240), (seen['x'], seen['y']))
        # 拖动必须「只移不改尺寸、不抢焦点」——标志位错了窗口会缩放或跳到最前
        for flag in ('_SWP_NOSIZE', '_SWP_NOZORDER', '_SWP_NOACTIVATE'):
            self.assertTrue(getattr(aw, flag), '%s 未定义' % flag)
            self.assertTrue(seen['flags'] & getattr(aw, flag), '%s 没传进 flags' % flag)

    def test_swp_constants_all_defined(self):
        """静态兜底：代码里引用的每个 _SWP_ / _WS_ 名字都必须有定义。

        NameError 只会在运行时被 except 吃掉，静态扫一遍最划算。
        """
        import re
        from pathlib import Path
        src = Path(aw_path()).read_text(encoding='utf-8') if False else \
            (Path(__file__).resolve().parent.parent / 'app_window.py').read_text(encoding='utf-8')
        for prefix in ('_SWP_', '_WS_'):
            used = set(re.findall(r'\b%s[A-Z0-9_]+\b' % prefix, src))
            defined = set(re.findall(r'^(%s[A-Z0-9_]+)\s*=' % prefix, src, re.M))
            self.assertEqual(set(), used - defined,
                             '%s 引用了未定义的常量: %s' % (prefix, sorted(used - defined)))

    def test_restore_unmaximizes_before_moving(self):
        aw, obj = self._api()
        order = []

        class U:
            def IsZoomed(self, h):
                return 1

            def ShowWindow(self, h, cmd):
                order.append(('show', cmd))
                return 1

            def MonitorFromWindow(self, h, flags):
                return 0        # 拿不到显示器：走「不夹取」分支


            def GetWindowRect(self, h, ptr):
                # 还原后的窗口矩形：1920x1080 @ (0,0)
                r = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_long * 4)).contents
                r[0], r[1], r[2], r[3] = 0, 0, 1920, 1080
                return 1

            def SetWindowPos(self, h, after, x, y, cx, cy, flags):
                order.append(('move', x, y))
                return 1

        import ctypes
        with mock.patch.object(aw, 'find_hwnd', return_value=0x1234), \
             mock.patch.object(aw, '_win_ptr_funcs',
                               return_value=(ctypes, U(), None, None)):
            r = obj.titlebar_restore(1000)
        self.assertIsNotNone(r)
        # 必须先 ShowWindow(SW_RESTORE) 再移动，否则移动被系统忽略
        self.assertEqual('show', order[0][0])
        self.assertEqual(aw._SW_RESTORE, order[0][1])
        self.assertEqual('move', order[1][0])


class TestWindowFrameColor(unittest.TestCase):
    """窗口外框底色（第 184 项）：必须跟着界面主题走，不能是白。

    用户的原话是「标题栏这边的颜色，我感觉变成了两层，一个是白色，一个是灰色」。
    根因是摘掉 WS_CAPTION 后，WS_THICKFRAME 留的那圈边框由 Form.BackColor
    填充（pywebview 默认 #FFFFFF），顶部还有一条 caption 带只能靠 DWM 属性压。
    """

    def test_light_and_dark_hex(self):
        import app_window as aw
        self.assertEqual('#F4F6FA', aw.ui_bg_hex(False))
        self.assertEqual('#181B21', aw.ui_bg_hex(True))

    def test_dark_arg_overrides_config(self):
        """传入 dark 时不能再去读配置：页面认 localStorage，后端猜不准。"""
        import app_window as aw
        with mock.patch.object(aw, 'effective_dark', return_value=False):
            self.assertEqual(aw.UI_BG_DARK, aw.ui_bg_rgb(True))
        with mock.patch.object(aw, 'effective_dark', return_value=True):
            self.assertEqual(aw.UI_BG_LIGHT, aw.ui_bg_rgb(False))

    def test_paint_frame_sets_border_and_caption(self):
        """34（描边）和 35（caption 带）缺一不可 —— 只设 34 头顶那条白去不掉。"""
        import ctypes
        import app_window as aw
        seen = []

        class Dwm:
            def DwmSetWindowAttribute(self, hwnd, attr, ptr, size):
                seen.append(attr)
                return 0

        with mock.patch.object(ctypes.windll, 'dwmapi', Dwm(), create=True):
            self.assertTrue(aw.paint_frame(0x1234, False))
        self.assertIn(34, seen)
        self.assertIn(35, seen)
