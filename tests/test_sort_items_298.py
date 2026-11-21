# -*- coding: utf-8 -*-
"""第 298 项：/api/files 的 sort_items 在「名称/时间/大小」排序下也要让命中的应用先上。

用户截图：搜索「微信」、排序停在「名称」，微信.lnk（ab=92）被排到第 17 行，
前面全是正文命中的 pdf/xlsx。前端 sortItems 与后端 sort_items 两处都要钉住应用，
否则快搜窗/主窗口行为不一致。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import brain  # noqa: E402


class SortItems298Test(unittest.TestCase):
    def setUp(self):
        self.items = [
            {'n': '网安行业互助表.xlsx', 'p': 'D:\\a\\网安行业互助表.xlsx', 'm': 10, 's': 100},
            {'n': '微信.lnk', 'p': 'C:\\Menu\\微信.lnk', 'm': 1, 's': 1, 'ab': 92, 'app': 1},
            {'n': '保密安全重点注意事项.pdf', 'p': 'D:\\a\\保密.pdf', 'm': 999, 's': 900},
        ]

    def test_01_name_pins_app(self):
        out = brain.sort_items(self.items, 'name')
        self.assertEqual('微信.lnk', out[0]['n'])

    def test_02_new_old_big_pin_app(self):
        for s in ('new', 'old', 'big'):
            self.assertEqual('微信.lnk', brain.sort_items(self.items, s)[0]['n'], s)

    def test_03_rest_still_sorted(self):
        out = brain.sort_items(self.items, 'new')
        self.assertEqual(['保密安全重点注意事项.pdf', '网安行业互助表.xlsx'],
                         [x['n'] for x in out[1:]])

    def test_04_no_ab_no_jump(self):
        items = [{'n': 'b.txt', 'p': 'C:\\x\\b.txt', 'm': 1},
                 {'n': 'a.txt', 'p': 'C:\\x\\a.txt', 'm': 2}]
        self.assertEqual(['a.txt', 'b.txt'], [x['n'] for x in brain.sort_items(items, 'name')])

    def test_05_rel_untouched(self):
        out = brain.sort_items(self.items, 'rel')
        self.assertEqual([x['n'] for x in self.items], [x['n'] for x in out],
                         'rel 不在这里重排，交给 mini_search 的 ab/r 顺序')


if __name__ == '__main__':
    unittest.main()
