# -*- coding: utf-8 -*-
"""第 337 项：左栏「文件夹分组」默认关（那一堆顶层目录没啥用，要的人去设置里开）。"""

import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    for enc in ('utf-8', 'utf-8-sig'):
        try:
            with open(os.path.join(HERE, *p), encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise AssertionError('decode failed: %r' % (p,))


class TestSidebarFoldersDefaultOff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = read('brain.py')
        cls.app = read(os.path.join('web', 'src', 'App.tsx'))
        cls.panels = read(os.path.join('web', 'src', 'ui', 'panels.tsx'))

    def test_backend_default_is_false(self):
        # GET /api/settings 给老配置补默认：sb_folders 必须是 False，类型/时间仍是 True
        self.assertIn("d.setdefault('sb_folders', False)", self.b)
        self.assertIn("d.setdefault('sb_types', True)", self.b)
        self.assertIn("d.setdefault('sb_time', True)", self.b)

    def test_frontend_requires_explicit_true(self):
        # 只有 settings 里明确写着 true 才显示；缺键 = 关
        self.assertIn('folders: s.sb_folders === true', self.app)
        self.assertNotIn('folders: s.sb_folders !== false', self.app)

    def test_toggle_reflects_default(self):
        self.assertIn('on={s.sb_folders === true}', self.panels)
        self.assertIn('patch({ sb_folders: s.sb_folders !== true })', self.panels)

    def test_other_two_blocks_keep_default_on(self):
        self.assertIn('types: s.sb_types !== false', self.app)
        self.assertIn('time: s.sb_time !== false', self.app)


if __name__ == '__main__':
    unittest.main()
