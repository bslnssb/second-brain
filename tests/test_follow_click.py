# -*- coding: utf-8 -*-
"""第 307 项：点资源管理器让文件对话框跟过去 —— 纯判定的测试。

钩子本身要真鼠标，没法进单测；这里钉住三件容易写错的事：
1. 只有资源管理器文件窗口算，对话框 #32770 和浏览器不算；
2. 只有落在**文件区矩形**里的点算，工具栏/状态栏不算；
3. 目标目录和对话框当前目录相同 → 不折腾。
"""
import os
import unittest

import follow_click


class RectTest(unittest.TestCase):
    def test_inside_and_outside(self):
        r = (10, 20, 110, 220)
        self.assertTrue(follow_click.in_rect(r, 10, 20))
        self.assertTrue(follow_click.in_rect(r, 109, 219))
        # 右/下边界是开区间，跟 Win32 一致
        self.assertFalse(follow_click.in_rect(r, 110, 100))
        self.assertFalse(follow_click.in_rect(r, 50, 220))
        self.assertFalse(follow_click.in_rect(None, 50, 50))


class ClassTest(unittest.TestCase):
    def test_explorer_only(self):
        self.assertTrue(follow_click.is_explorer_class('CabinetWClass'))
        self.assertTrue(follow_click.is_explorer_class('ExploreWClass'))
        # 文件对话框也是 #32770，点它自己不该触发「跳到自己」
        self.assertFalse(follow_click.is_explorer_class('#32770'))
        self.assertFalse(follow_click.is_explorer_class('Chrome_WidgetWin_1'))
        self.assertFalse(follow_click.is_explorer_class(''))


class ShouldJumpTest(unittest.TestCase):
    def setUp(self):
        self.dir_a = os.path.dirname(os.path.abspath(__file__))
        self.dir_b = os.path.dirname(self.dir_a)

    def test_same_dir_no_jump(self):
        self.assertFalse(follow_click.should_jump(self.dir_a, self.dir_a))
        # 大小写/斜杠差异不该被当成另一个目录
        self.assertFalse(follow_click.should_jump(self.dir_a, self.dir_a + os.sep))

    def test_other_dir_jumps(self):
        self.assertTrue(follow_click.should_jump(self.dir_a, self.dir_b))

    def test_nonexistent_never_jumps(self):
        self.assertFalse(follow_click.should_jump(os.path.join(self.dir_a, 'no-such-dir-307')))

    def test_quotes_stripped(self):
        self.assertTrue(follow_click.should_jump('"%s"' % self.dir_a, self.dir_b))
