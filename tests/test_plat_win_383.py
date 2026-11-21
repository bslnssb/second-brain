# -*- coding: utf-8 -*-
"""第 382 项 · Win32 导入期兜底的回归测试（plat/win.py）。

守两条：
1. 占位对象本身的行为（链式属性、调用、当类型用的 WINFUNCTYPE）；
2. **在「假装没有 Win32」的环境里，5 个历史遗留模块必须能 import**。
   第 2 条用子进程跑：删掉 ctypes 上的 windll / WINFUNCTYPE / WinDLL / oledll / HRESULT
   之后再 import，能复现「mac 上一 import 就炸」的真实场景，而且不会污染本进程的 ctypes。
"""

import os
import subprocess
import sys
import unittest

import plat.win as win


class TestNullProxy(unittest.TestCase):
    def test_chained_attribute_and_call(self):
        p = win._Null()
        self.assertIsInstance(p.user32, win._Null)
        # 链式任意深，不炸
        self.assertIsInstance(p.user32.GetMessageW.h.whatever, win._Null)
        # 调用返回空对象，可以被 int() 吃掉（代码里 `hwnd = user32.X(); if not hwnd` 这类写法不会 TypeError）
        self.assertEqual(int(p.user32.FindWindowW(None, 'x')), 0)
        self.assertFalse(bool(p.user32))

    def test_setattr_is_swallowed_and_does_not_leak(self):
        p = win._Null()
        q = p.user32
        p.user32.argtypes = [1, 2]          # 模块级 `X.argtypes = [...]` 不许抛异常
        self.assertIsInstance(q, win._Null)  # 但也不能真存下来污染兄弟节点

    def test_func_type_syntax_wraps_and_returns_func(self):
        cb = win._FuncTypeSyntax(int, ())  # 只取语法：拿得到类型、调用时原样返回函数
        f = lambda *a: 0
        self.assertIs(cb(f), f)  # 真 ctypes 写法：CB = WINFUNCTYPE(...); cb = CB(func)


class TestNoWin32Environment(unittest.TestCase):
    """真跑子进程：ctypes 上没有 Win32 名字时，5 个模块仍要 import 得动。"""

    MODULES = ('app_window', 'typewatch', 'follow_click', 'hotkey', 'snip_ocr')

    def test_modules_import_without_win32(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = (
            "import ctypes\n"
            "for n in ('windll','oledll','WinDLL','WINFUNCTYPE','HRESULT','LibraryLoader'):\n"
            "    if hasattr(ctypes, n): delattr(ctypes, n)\n"
            "for m in %r:\n"
            "    __import__(m)\n"
            "import ctypes as c\n"
            "assert hasattr(c, 'windll'), '占位没补上'\n"
            "print('OK')\n" % (self.MODULES,)
        )
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        r = subprocess.run([sys.executable, '-c', code], cwd=root, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(r.returncode, 0, msg='非 Win32 环境下 import 失败：\n' + r.stdout.decode('utf-8', 'replace'))


if __name__ == '__main__':
    unittest.main()
