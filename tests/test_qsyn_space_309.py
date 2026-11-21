# -*- coding: utf-8 -*-
"""第 309 项：`ext: pdf` / `ext： pdf` / `ext :pdf` 这类「冒号两边带空白」的写法要照样当语法。

2026-11-18 用户报「信息安全 ext:pdf 搜索不到东西」。实测半角连写没问题，但
冒号后多一个空格（中文输入法常见）时 `ext:` 会掉回关键词、结果 0 条。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brain  # noqa: E402


class ExtSpaceTest(unittest.TestCase):
    def test_space_variants_still_syntax(self):
        for raw in ('信息安全 ext: pdf', '信息安全 ext： pdf',
                    '信息安全 ext :pdf', '信息安全 ext : pdf',
                    '信息安全 ext：pdf'):
            syn = brain.parse_qsyn(raw)
            self.assertEqual(syn['ext'], 'pdf', raw)
            self.assertEqual(syn['q'], '信息安全', raw)

    def test_multiple_exts_with_space(self):
        syn = brain.parse_qsyn('合同 ext: pdf, docx')
        self.assertEqual(syn['ext'], 'pdf,docx')
        self.assertEqual(syn['q'], '合同')

    def test_size_and_dm_with_space(self):
        self.assertEqual(brain.parse_qsyn('a size: >10m')['size'],
                         (10 * 1024 * 1024, None))
        self.assertEqual(brain.parse_qsyn('a dm： 7d')['days'], 7)

    def test_bare_prefix_kept_as_keyword(self):
        # 光写一个 `ext:`（后面什么都没有）仍然当普通关键词，不吞掉
        syn = brain.parse_qsyn('ext:')
        self.assertIsNone(syn['ext'])
        self.assertEqual(syn['q'], 'ext:')

    def test_normal_colon_in_name_untouched(self):
        syn = brain.parse_qsyn('报告：终稿.docx')
        self.assertIsNone(syn['ext'])
        self.assertEqual(syn['q'], '报告：终稿.docx')


if __name__ == '__main__':
    unittest.main()
