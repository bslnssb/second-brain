# -*- coding: utf-8 -*-
"""brain.py 第二批纯函数单测（版本解析 / 缩写 / 分组 / FTS 查询构造 / 范围排除）。

与 test_brain_pure.py 互补：那批覆盖白名单/指纹/排序，这批覆盖检索与更新链路的
纯逻辑。不碰数据库、不联网、不启窗口。

跑法：python -m unittest discover -s tests -p "test_*.py" -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402


class VerTupleTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(brain.ver_tuple('v0.9.48'), (0, 9, 48))
        self.assertEqual(brain.ver_tuple('0.10.2'), (0, 10, 2))
        self.assertEqual(brain.ver_tuple('v6.5'), (6, 5, 0))

    def test_empty(self):
        self.assertEqual(brain.ver_tuple(''), (0, 0, 0))
        self.assertEqual(brain.ver_tuple(None), (0, 0, 0))

    def test_not_string_compare(self):
        # 不按字符串比，否则 0.10 会被判成比 0.9 旧
        self.assertGreater(brain.ver_tuple('v0.10.0'), brain.ver_tuple('v0.9.99'))


class VerFromJsonTest(unittest.TestCase):
    def test_missing_version(self):
        self.assertIsNone(brain._ver_from_json({}))

    def test_tag_normalized_with_v(self):
        out = brain._ver_from_json({'tag': '0.9.60'})
        self.assertIsNotNone(out)
        self.assertEqual(out['latest'], 'v0.9.60')

    def test_newer_vs_current(self):
        out = brain._ver_from_json({'version': '9.9.9'})
        self.assertTrue(out['newer'])

    def test_same_as_current_not_newer(self):
        out = brain._ver_from_json({'version': brain.APP_VERSION})
        self.assertFalse(out['newer'])

    def test_legacy_line_flagged(self):
        out = brain._ver_from_json({'version': '6.5'})
        self.assertTrue(out['legacy'])
        self.assertFalse(out['newer'])

    def test_asset_extracted(self):
        out = brain._ver_from_json({
            'version': '0.9.60',
            'assets': [{'name': 'SecondBrain-Setup.zip', 'url': 'http://x', 'size': 1}],
        })
        self.assertIsNotNone(out['asset'])
        self.assertEqual(out['asset']['name'], 'SecondBrain-Setup.zip')


class AcrTest(unittest.TestCase):
    def test_known(self):
        self.assertEqual(brain._acr('DeepSeek Harness'), 'dsh')
        self.assertEqual(brain._acr('Visual Studio Code'), 'vsc')
        self.assertEqual(brain._acr('WeChat'), 'wc')

    def test_empty_and_single(self):
        self.assertEqual(brain._acr(''), '')
        self.assertEqual(brain._acr('a'), '')
        self.assertEqual(brain._acr('iPhone'), '')   # 单字母缩写被丢弃

    def test_all_caps_single_letter_acronym_dropped(self):
        # 全大写词只取首字母 -> 'a' 单字母，被 2..24 长度门槛丢掉的设定是故意的
        self.assertEqual(brain._acr('ABC'), '')


class NoiseDirTest(unittest.TestCase):
    def test_noise(self):
        self.assertTrue(brain._noise_dir('E:\\SteamLibrary\\steamapps'))
        self.assertTrue(brain._noise_dir('D:\\游戏\\x'))

    def test_clean(self):
        self.assertFalse(brain._noise_dir('D:\\Photos'))


class GroupKeyTest(unittest.TestCase):
    def test_subdir_under_home(self):
        key, noise = brain._group_key(
            r'C:\Users\X\Documents\Projects\report.docx',
            r'C:\Users\X\Documents')
        self.assertEqual(key, r'C:\Users\X\Documents\Projects')
        self.assertFalse(noise)

    def test_other_drive(self):
        key, noise = brain._group_key(
            r'D:\SteamLibrary\steamapps\game.exe',
            r'C:\Users\X\Documents')
        self.assertEqual(key, r'D:\SteamLibrary')
        self.assertTrue(noise)   # steam 命中噪音词


class FtsTest(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(brain._fts_tokens('a b  c'), ['a', 'b', 'c'])
        self.assertEqual(brain._fts_tokens('   '), [])

    def test_query_wraps_and_and(self):
        self.assertEqual(brain._fts_query('简历'), '"简历"')
        self.assertEqual(brain._fts_query('a b'), '"a" AND "b"')
        self.assertEqual(brain._fts_query('node.js'), '"node.js"')

    def test_query_strips_surrounding_quotes(self):
        self.assertEqual(brain._fts_query('"quoted"'), '"quoted"')

    def test_query_escapes_inner_quote_single_token(self):
        # 单 token 内的引号必须被转义成 FTS5 的 ""，不能变成语法
        self.assertEqual(brain._fts_query('a"b'), '"a""b"')

    def test_query_tokenizes_on_space_not_injection(self):
        # 含空格的输入按空白切成多个短语并各自加引号，绝不参与 SQL 拼接
        self.assertEqual(brain._fts_query('a" OR 1=1 --'),
                         '"a" AND "OR" AND "1=1" AND "--"')
        # 关键安全属性：返回里没有任何未转义的 FTS 语法能让输入变成查询指令
        self.assertNotIn(' OR ', brain._fts_query('a" OR 1=1 --'))

    def test_need_like_short_tokens(self):
        self.assertTrue(brain._need_like('ab'))        # 长度 <3
        self.assertTrue(brain._need_like(''))          # 空
        self.assertFalse(brain._need_like('abc'))
        self.assertTrue(brain._need_like('中文'))       # 两字中文走 LIKE 降级


class ExtClauseTest(unittest.TestCase):
    def test_dotted(self):
        self.assertEqual(brain._ext_clause('.pdf'), (' AND ext = ?', ['.pdf']))
        self.assertEqual(brain._ext_clause('PDF'), (' AND ext = ?', ['.pdf']))

    def test_empty(self):
        self.assertEqual(brain._ext_clause(''), ('', []))

    def test_multi(self):
        sql, args = brain._ext_clause('pdf,txt')
        self.assertEqual(sql, ' AND ext IN (?,?)')
        self.assertEqual(args, ['.pdf', '.txt'])
        sql2, args2 = brain._ext_clause('.pdf, .txt')
        self.assertEqual(args2, ['.pdf', '.txt'])


class LikeEscTest(unittest.TestCase):
    def test_wildcards_escaped(self):
        self.assertEqual(brain._like_esc('a_b%c'), 'a\\_b\\%c')
        self.assertEqual(brain._like_esc('%'), '\\%')


if __name__ == '__main__':
    unittest.main()
