# -*- coding: utf-8 -*-
"""第 405 项：CSV/TSV 详情预览 + pptx 幻灯片文字当主预览。"""

import io
import os
import unittest

import brain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCsvRaw(unittest.TestCase):
    def test_raw_whitelist(self):
        self.assertIn('csv', brain._RAW_OK_EXT)
        self.assertIn('tsv', brain._RAW_OK_EXT)
        self.assertIn('text/csv', brain._RAW_MIME['csv'])

    def test_frontend_wired(self):
        doc = io.open(os.path.join(ROOT, 'web/src/ui/docview.tsx'), encoding='utf-8').read()
        self.assertIn("'csv', 'tsv'", doc)
        self.assertIn('parseDelimited', doc)
        self.assertIn("TextDecoder('gbk')", doc)
        panel = io.open(os.path.join(ROOT, 'web/src/ui/panels.tsx'), encoding='utf-8').read()
        self.assertIn("const SHEET_EXT = ['xls', 'xlsx', 'et', 'csv', 'tsv']", panel)
        # pptx 不再等页图失败才铺文字
        self.assertIn("const INLINE_PRIMARY = [...SHEET_EXT, 'pptx']", panel)


if __name__ == '__main__':
    unittest.main()
