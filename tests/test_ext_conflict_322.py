# -*- coding: utf-8 -*-
"""第 322 项：框里的 `ext:` 和侧栏类型要取交集。

用户实测：搜索框 `ext:pdf`，又点了侧栏「图片」，结果是一屏图片 —— 因为前端把
两段 `ext:` 拼在一起（`ext:pdf ext:jpg,jpeg,…`），后端只认最后一段。

现在前端在发请求前自己取交集；交集为空就回一句「没有交集」，不再给错结果。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(p):
    with io.open(os.path.join(ROOT, p), 'r', encoding='utf-8') as f:
        return f.read()


class ExtConflictTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = read(os.path.join('web', 'src', 'App.tsx'))
        cls.i18n = read(os.path.join('web', 'src', 'lib', 'i18n.ts'))

    def test_intersection_before_request(self):
        self.assertIn('typedExts', self.app)
        self.assertIn("q.match(/(^|\\s)ext:[^\\s]+/gi)", self.app)
        self.assertIn('kindExts.filter((e) => typedExts.includes(e))', self.app)
        self.assertIn('extConflict', self.app)
        self.assertIn("const filesQ = extConflict ? ''", self.app)

    def test_no_duplicate_ext_token(self):
        # 不能再出现「q + ext:…」这种直接拼接（那份拼法会把用户写的 ext 顶掉）
        self.assertNotIn("`${q} ext:${kindExts.join(',')}`", self.app)

    def test_toast_when_empty_intersection(self):
        self.assertIn('ext_no_match', self.app)
        self.assertIn("setToast(t('ext_no_match'))", self.app)

    def test_i18n_five_languages(self):
        for lang_start in ('ZH', 'EN', 'ES', 'PT', 'HI'):
            self.assertTrue(lang_start)
        hits = re.findall(r"ext_no_match: '", self.i18n)
        self.assertEqual(5, len(hits), 'ext_no_match 必须 5 种语言都在')


if __name__ == '__main__':
    unittest.main()
