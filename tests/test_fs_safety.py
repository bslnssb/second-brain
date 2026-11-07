# -*- coding: utf-8 -*-
"""文件系统的「环境差异」测试：联接点、长路径、权限。

用户那边的目录结构和开发机不一样：可能挂着 OneDrive、有网络驱动器、
自己建过目录联接（junction）把 D 盘的大目录挂到桌面下。
`os.walk(followlinks=False)` 只挡符号链接，**挡不住 junction** ——
指向上层的 junction 会成环，让索引永远跑不完（用户看到「卡住 + 风扇狂转」）。
这些测试用 Windows 自带的几个自引用 junction 来验证剪枝逻辑。
"""
from __future__ import annotations

import os
import unittest

try:
    import brain
except Exception:  # pragma: no cover
    brain = None

# Windows 自带的自引用/回环 junction（Win10/11 都存在）
SYSTEM_JUNCTIONS = [
    r'C:\Users\All Users',
    r'C:\Documents and Settings',
    r'C:\Users\Default User',
    r'C:\ProgramData\Application Data',
    r'C:\Users\Public\Documents\Application Data',
]


@unittest.skipIf(brain is None, 'brain 导入失败')
class TestReparsePoint(unittest.TestCase):
    def test_system_junctions_detected(self):
        """系统自带的 junction 必须被判成 True，否则扫描会跟着它们转圈。"""
        checked = 0
        for p in SYSTEM_JUNCTIONS:
            if not (os.path.exists(p) or os.path.islink(p)):
                continue          # 个别系统版本没有这一条
            checked += 1
            with self.subTest(path=p):
                self.assertTrue(brain._is_reparse_dir(p),
                                'junction 没被识别：%s' % p)
        if checked == 0:
            self.skipTest('这台机器没有可用的系统 junction')

    def test_plain_dirs_are_false(self):
        for p in (r'C:\Windows', r'C:\Users', os.path.dirname(os.path.abspath(__file__))):
            if os.path.isdir(p):
                with self.subTest(path=p):
                    self.assertFalse(brain._is_reparse_dir(p))

    def test_missing_path_is_false_not_raise(self):
        """不存在的路径/无权限路径只能返回 False，不能抛异常打断整场扫描。"""
        self.assertFalse(brain._is_reparse_dir(r'C:\__no_such_dir_sb_test__'))
        self.assertFalse(brain._is_reparse_dir(''))
        self.assertFalse(brain._is_reparse_dir('Z:\\nope\\nope'))

    def test_file_is_not_dir_but_no_crash(self):
        p = os.path.abspath(__file__)
        self.assertFalse(brain._is_reparse_dir(p))


@unittest.skipIf(brain is None, 'brain 导入失败')
class TestScanGuards(unittest.TestCase):
    def test_skip_dirs_cover_system_noise(self):
        """必须跳过这些目录：否则要么扫到系统垃圾，要么踩到虚拟设备。"""
        must = {'windows', 'appdata', '$recycle.bin', 'node_modules',
                'system volume information', 'crossdevice'}
        for d in must:
            self.assertIn(d, brain.NAMES_SKIP_DIRS, '扫描跳过列表缺 %s' % d)

    def test_program_files_not_skipped_in_name_index(self):
        """文件名索引要能搜到 chrome.exe / Weixin.exe，所以不能跳过 Program Files。"""
        self.assertNotIn('program files', brain.NAMES_SKIP_DIRS)
        self.assertNotIn('program files (x86)', brain.NAMES_SKIP_DIRS)


if __name__ == '__main__':
    unittest.main()
