# -*- coding: utf-8 -*-
"""第 338 项：左栏「计数数字」默认关（设置 → 左栏 里再开）。"""

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


class TestSidebarCountsDefaultOff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = read('brain.py')
        cls.app = read(os.path.join('web', 'src', 'App.tsx'))
        cls.panels = read(os.path.join('web', 'src', 'ui', 'panels.tsx'))
        cls.api = read(os.path.join('web', 'src', 'api.ts'))
        cls.i18n = read(os.path.join('web', 'src', 'lib', 'i18n.ts'))

    def test_backend_default_false(self):
        self.assertIn("d.setdefault('sb_counts', False)", self.b)
        # PATCH 白名单也要收这个键
        self.assertIn("'sb_time', 'sb_counts'", self.b)

    def test_frontend_requires_explicit_true(self):
        self.assertIn('counts: s.sb_counts === true', self.app)
        self.assertIn('counts: p.sb_counts !== undefined ? p.sb_counts : cur.counts', self.app)

    def test_counts_guarded_in_sidebar(self):
        # 类型行 / 文档子分类 / 文件夹分组 三处数字都要受开关控制
        self.assertEqual(3, self.app.count('sb.counts &&'))

    def test_toggle_in_settings(self):
        self.assertIn('on={s.sb_counts === true}', self.panels)
        self.assertIn('patch({ sb_counts: s.sb_counts !== true })', self.panels)
        self.assertIn('sb_counts?: boolean', self.api)

    def test_i18n_keys_in_all_five(self):
        for k in ('sb_counts', 'sb_counts_label'):
            self.assertEqual(5, self.i18n.count(k + ':'), k)


if __name__ == '__main__':
    unittest.main()
