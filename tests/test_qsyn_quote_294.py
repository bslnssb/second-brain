# -*- coding: utf-8 -*-
"""第 294 项（H）：后端 parse_qsyn 要跟前端一样，认双引号里的空格。"""
import unittest

import brain


class TestQuotedFolder(unittest.TestCase):
    def test_quoted_folder_with_space(self):
        s = brain.parse_qsyn('"D:\\我的 文件\\" 合同')
        self.assertEqual(s['folder'], 'D:\\我的 文件')
        self.assertEqual(s['q'], '合同')

    def test_quoted_only_folder(self):
        s = brain.parse_qsyn('"D:\\学习 资料\\"')
        self.assertEqual(s['folder'], 'D:\\学习 资料')
        self.assertEqual(s['q'], '')

    def test_plain_path_still_works(self):
        s = brain.parse_qsyn('D:\\学习资料\\ 四级')
        self.assertEqual(s['folder'], 'D:\\学习资料')
        self.assertEqual(s['q'], '四级')

    def test_unbalanced_quote_kept_as_keyword(self):
        # 只有半边引号就不当引号处理，原来的词不能凭空消失
        s = brain.parse_qsyn('"未闭合 词')
        self.assertTrue(s['q'])


if __name__ == '__main__':
    unittest.main()
