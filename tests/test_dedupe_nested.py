# -*- coding: utf-8 -*-
"""同名嵌套 exe 去重（2026-11-13，交接第 186 项）。

现象：用户从第二大脑点「豆包」，弹出一个**豆包自己的**错误框
`Can't find main exe file`。

定位过程（可复现）：
  1. `D:\\Doubao\\Doubao.exe` 是 1.8MB 的 launcher，读同目录 `Doubao.ini`（内容就是 `app`）
     再去找 `app\\Doubao.exe`；
  2. 豆包的安装目录里躺着**三份同名 exe**：外层 launcher、`app\\Doubao.exe`、
     `app\\launcher\\Doubao.exe`（后两个是 launcher 的副本）；
  3. 打开 `app\\launcher\\Doubao.exe`（实测）→ 它去自己目录找 `app\\` 子目录 → 没有 →
     弹「错误 / Can't find main exe file」，与用户截图一字不差；
  4. 外层 `D:\\Doubao\\Doubao.exe` 在**任何 cwd** 下都能正常启动（已实测，cwd 不是原因）。

所以修的是「结果里出现三条一模一样的『豆包』，用户点了其中一条坏的」。
"""
import os
import unittest

import brain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app(p, ext='.exe', alts=0):
    return {'p': p, 'n': os.path.basename(p), 'e': ext, 't': 'file', 'alts': alts}


class TestDedupeNested(unittest.TestCase):
    def test_doubao_three_entries_become_one(self):
        items = [
            app(r'D:\Doubao\Doubao.exe'),
            app(r'D:\Doubao\app\Doubao.exe'),
            app(r'D:\Doubao\app\launcher\Doubao.exe'),
        ]
        out = brain._dedupe_nested(items)
        self.assertEqual(1, len(out))
        self.assertEqual(r'D:\Doubao\Doubao.exe', out[0]['p'])
        self.assertEqual(2, out[0]['alts'])

    def test_same_name_in_unrelated_dirs_survive(self):
        """同名但不同安装目录的两条都要留着 —— 它们真是两个程序。"""
        items = [app(r'C:\Python312\python.exe'), app(r'D:\tools\python.exe')]
        self.assertEqual(2, len(brain._dedupe_nested(items)))

    def test_non_app_files_untouched(self):
        """只动可执行文件：两个 readme.txt 不该被合掉。"""
        items = [
            {'p': r'D:\a\readme.txt', 'n': 'readme.txt', 'e': '.txt', 't': 'file'},
            {'p': r'D:\a\b\readme.txt', 'n': 'readme.txt', 'e': '.txt', 't': 'file'},
        ]
        self.assertEqual(2, len(brain._dedupe_nested(items)))

    def test_outer_kept_even_when_inner_comes_first(self):
        """顺序反了也要留外层：深层那条只是实现细节。"""
        items = [
            app(r'D:\Doubao\app\launcher\Doubao.exe'),
            app(r'D:\Doubao\Doubao.exe'),
        ]
        out = brain._dedupe_nested(items)
        self.assertEqual(1, len(out))
        self.assertEqual(r'D:\Doubao\Doubao.exe', out[0]['p'])

    def test_merge_same_target_calls_dedupe(self):
        """接在 merge_same_target 后面 —— 不然 lnk 合并完还是三条同名。"""
        with open(os.path.join(ROOT, 'brain.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('out = _dedupe_nested(out)', src)


if __name__ == '__main__':
    unittest.main()
