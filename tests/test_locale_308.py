# -*- coding: utf-8 -*-
"""第 308 项：日期格式跟随系统 + 快搜窗多选文案齐五语言。"""
import unittest

import brain


class LocaleTest(unittest.TestCase):
    def test_shape(self):
        d = brain.sys_locale_info()
        self.assertEqual(sorted(d.keys()), ['date', 'locale', 'time'])
        for v in d.values():
            self.assertIsInstance(v, str)

    def test_pattern_has_date_tokens(self):
        d = brain.sys_locale_info()
        # Windows 上一定拿得到；拿不到也不许崩，前端会退回 ISO
        if d['date']:
            self.assertTrue(any(c in d['date'] for c in 'yYdD'),
                            d['date'])
        if d['time']:
            self.assertTrue(any(c in d['time'] for c in 'Hhm'), d['time'])


class MiniSelectTextTest(unittest.TestCase):
    KEYS = ('sel_n', 'copied_one', 'copied_n', 'copied_dir', 'copy_none', 'copy_fail')

    def test_all_langs_have_keys(self):
        for lang, table in brain.MINI_L.items():
            for k in self.KEYS:
                self.assertIn(k, table, '%s 缺 %s' % (lang, k))


if __name__ == '__main__':
    unittest.main()
