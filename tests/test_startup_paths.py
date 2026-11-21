# -*- coding: utf-8 -*-
"""启动路径的回归测试（第 239 项：2026-11-16 开机直接起不来）。

现场：开机自启弹出「窗口启动失败：UnboundLocalError: cannot access local variable
'traceback' where it is not associated with a value」。真因是两层叠在一起：

1. **第 236 项改排除规则时误删了 `_watch_roots()` 里的 `home = os.path.expanduser('~')`**
   → 启动即 `NameError: name 'home' is not defined`。
   为什么没人发现：单测从不调 `_watch_roots()`，preflight 也只钉字符串，
   而第 236 项打包后那个进程一直在跑（换 exe 不影响已运行的进程），
   于是新 exe 一次都没被真正启动过。

2. **`app_window._start_server()` 里有一句局部 `import traceback`**
   → 它把整个函数的 traceback 变成局部名，于是「启动监听失败」那个 except 一执行
   就抛 UnboundLocalError，**把真正的 NameError 吃掉**，用户只看到一句无用的提示。

第 2 条已经由 preflight 的 AST 守卫（check_no_shadowed_imports）覆盖，
这里补第 1 条的行为测试 + 一条对 app_window 的静态断言。

跑法：py -3.12 -m unittest tests.test_startup_paths -v
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402


class TestStartupPaths(unittest.TestCase):

    def test_watch_roots_does_not_raise(self):
        """以前这里是 NameError: name 'home' is not defined —— 启动就挂。"""
        roots = brain._watch_roots()
        self.assertIsInstance(roots, list, '监听根必须能算出来（不能抛异常）')
        for r in roots:
            self.assertTrue(str(r).strip())

    def test_watch_roots_contains_user_folders(self):
        """监听根里应该包含桌面/下载/文档（home 拼出来的那些）。"""
        roots = brain._watch_roots()
        low = [str(r).lower() for r in roots]
        self.assertTrue(any('desktop' in r or '桌面' in r for r in low)
                        or any('download' in r or '下载' in r for r in low),
                        '监听根里应有桌面/下载/文档：%r' % roots[:5])

    def test_start_server_has_no_shadowed_traceback(self):
        """_start_server 里不能再出现局部 `import traceback`。"""
        src = (ROOT / 'app_window.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertIn('_start_server', fns)
        fn = fns['_start_server']
        for node in ast.walk(fn):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotEqual(
                        a.name, 'traceback',
                        '_start_server 里又 import traceback 了：局部 import 会把模块级'
                        '那个遮成局部名，别的 except 分支一用就 UnboundLocalError')

    def test_module_level_traceback_import_exists(self):
        """模块顶部必须 import traceback（去掉局部 import 后靠它）。"""
        src = (ROOT / 'app_window.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        names = set()
        for n in tree.body:
            if isinstance(n, ast.Import):
                for a in n.names:
                    names.add((a.asname or a.name).split('.')[0])
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    names.add(a.asname or a.name)
        self.assertIn('traceback', names, 'app_window.py 顶部要 import traceback')


if __name__ == '__main__':
    unittest.main()
