# -*- coding: utf-8 -*-
"""搜索语法 parse_qsyn / syn_ok 的单元测试（2026-11-07 用户实测两条 bug）。

背景：用户在主搜索框写 `微信 size>10m` 一条结果都没有。查下来是两个独立问题：

1. 解析器只认 `size:>10m`（冒号），不认 `size>10m`。认不出的片段按设计「原样留在
   关键词里」，于是它变成一个普通词去和文件名匹配 —— 必然 0 条。
2. 就算写对了 `size:>10m`，过滤也没生效：结果字典里尺寸字段叫 `z`
   （`'m': mtime, 'z': size`），而调用方写的是 `it.get('s')` → 恒 None →
   `syn_ok` 的「信息不全就不拦」把条件放行，958 字节的 微信.lnk 照样列出来。

跑法：python -m unittest tests.test_qsyntax -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402


class TestSizeSyntaxNoColon(unittest.TestCase):
    """不带冒号的写法必须被认出来（第 1 条 bug）。"""

    def test_size_gt(self):
        s = brain.parse_qsyn('微信 size>10m')
        self.assertEqual(s['q'], '微信')
        self.assertEqual(s['size'], (10 * 1024 * 1024, None))

    def test_size_gte(self):
        s = brain.parse_qsyn('size>=100k')
        self.assertEqual(s['size'], (100 * 1024, None))

    def test_size_lt(self):
        s = brain.parse_qsyn('size<1g')
        self.assertEqual(s['size'], (None, 1024 ** 3))

    def test_size_no_operator_means_gt(self):
        # size10m 和 size:10m 同义（原实现里 op 为 None 就是 >a）
        s = brain.parse_qsyn('size10m')
        self.assertEqual(s['size'], (10 * 1024 * 1024, None))

    def test_size_range(self):
        s = brain.parse_qsyn('size5m-10m')
        self.assertEqual(s['size'], (5 * 1024 * 1024, 10 * 1024 * 1024))

    def test_colon_form_still_works(self):
        s = brain.parse_qsyn('微信 size:>10m')
        self.assertEqual(s['q'], '微信')
        self.assertEqual(s['size'], (10 * 1024 * 1024, None))


class TestSizeSyntaxNoFalsePositive(unittest.TestCase):
    """别把正常单词吞成语法 —— 宁可搜不到，也不能悄悄改变用户的关键词。"""

    def test_size_inside_word(self):
        for word in ('filesize', 'sizechart', 'resize2'):
            s = brain.parse_qsyn(word)
            self.assertEqual(s['q'], word, word)
            self.assertEqual(s['size'], (None, None), word)

    def test_size_alone(self):
        s = brain.parse_qsyn('size')
        self.assertEqual(s['q'], 'size')
        self.assertEqual(s['size'], (None, None))


class TestDmSyntaxNoColon(unittest.TestCase):
    def test_dm_lt(self):
        s = brain.parse_qsyn('dm<30d')
        self.assertEqual(s['days'], 30)
        self.assertEqual(s['q'], '')

    def test_dm_no_operator(self):
        s = brain.parse_qsyn('dm7d')
        self.assertEqual(s['days'], 7)

    def test_dm_word_not_eaten(self):
        s = brain.parse_qsyn('dmg')
        self.assertEqual(s['q'], 'dmg')
        self.assertIsNone(s['days'])


class TestSizeFieldAlias(unittest.TestCase):
    """结果字典用 z 存尺寸，过滤必须读到它（第 2 条 bug）。"""

    def test_sz_of_reads_z(self):
        self.assertEqual(brain._sz_of({'p': 'a', 'z': 958.0}), 958.0)

    def test_sz_of_prefers_s(self):
        self.assertEqual(brain._sz_of({'s': 10, 'z': 20}), 10)

    def test_syn_ok_rejects_small_file_via_z(self):
        syn = brain.parse_qsyn('size>10m')
        # 958 字节的快捷方式必须被挡掉（用户实测里的 微信.lnk）
        self.assertFalse(brain.syn_ok('C:\\a\\微信.lnk', brain._sz_of({'z': 958.0}), 0, syn))
        # 20 MB 的安装包留下
        self.assertTrue(brain.syn_ok('C:\\a\\Weixin.exe',
                                     brain._sz_of({'z': 20 * 1024 * 1024}), 0, syn))

    def test_syn_ok_keeps_unknown_size(self):
        # 信息不全不拦：宁多给一条，也别因为查不到 size 就凭空吞掉结果
        syn = brain.parse_qsyn('size>10m')
        self.assertTrue(brain.syn_ok('C:\\a\\未知.doc', None, 0, syn))


class TestEndToEndParsing(unittest.TestCase):
    """用户原话那一串：关键词 + 过滤条件要能同时生效。"""

    def test_user_query(self):
        s = brain.parse_qsyn('微信 size>10m')
        self.assertEqual(s['q'], '微信')
        self.assertEqual(s['size'], (10 * 1024 * 1024, None))
        self.assertEqual(s['ext'], None)

    def test_mixed_syntax(self):
        s = brain.parse_qsyn('报告 ext:pdf size<2m dm>7d -草稿 folder/')
        self.assertEqual(s['q'], '报告')
        self.assertEqual(s['ext'], 'pdf')
        self.assertEqual(s['size'], (None, 2 * 1024 * 1024))
        self.assertEqual(s['days'], 7)
        self.assertEqual(s['excl'], ['草稿'])
        self.assertEqual(s['folder'], 'folder')


class TestContentOnlySyntax(unittest.TestCase):
    """c: / 内容: / content: 三写法等价 —— 快搜窗里少打几个字很值钱。

    但 c:\\ 和 c:/ 是盘符路径，绝不能被当成「只搜正文」。
    """

    def test_content_colon(self):
        s = brain.parse_qsyn('content:报告')
        self.assertTrue(s['content'])
        self.assertEqual(s['q'], '报告')

    def test_content_space(self):
        s = brain.parse_qsyn('content: 报告')
        self.assertTrue(s['content'])
        self.assertEqual(s['q'], '报告')

    def test_c_short(self):
        s = brain.parse_qsyn('c:会议纪要')
        self.assertTrue(s['content'])
        self.assertEqual(s['q'], '会议纪要')

    def test_chinese_prefix(self):
        s = brain.parse_qsyn('内容:预算')
        self.assertTrue(s['content'])
        self.assertEqual(s['q'], '预算')

    def test_drive_path_not_eaten(self):
        # 盘符路径绝不能被当成正文开关。c:\ 单独输入时会被 folder/ 规则收走
        # （用户想限定 C 盘），那也是对的，所以这里只断言 content 没被打开。
        for raw in ('c:\\users', 'c:/users', 'c:\\'):
            s = brain.parse_qsyn(raw)
            self.assertFalse(s['content'], raw)
        self.assertIn('c:', brain.parse_qsyn('c:\\users')['q'].lower())

    def test_bare_c_not_eaten(self):
        s = brain.parse_qsyn('c:')
        self.assertFalse(s['content'])


class TestBodySlots(unittest.TestCase):
    """正文命中该占几格 —— 快搜窗只有 9 格，这个分配决定了正文能不能被看见。

    背景：老实现是 room = limit - len(out)，名字满 9 条时 room=0，
    于是搜「报告」这种常见词正文命中永远不会出现（2026-11-07 用户反馈）。
    """

    def test_full_name_list_still_offers_three(self):
        # 名字满屏也必须让出 3 格 —— 这是这条修复的核心
        self.assertEqual(brain._body_slots(9, False), 3)

    def test_six_names(self):
        self.assertEqual(brain._body_slots(6, False), 3)

    def test_few_names_fill_the_screen(self):
        self.assertEqual(brain._body_slots(1, False), 8)
        self.assertEqual(brain._body_slots(0, False), 9)

    def test_app_launch_takes_no_body_slot(self):
        # 搜「微信」想打开的是微信本体，不是提过微信的 20 篇文档
        self.assertEqual(brain._body_slots(9, True), 0)

    def test_never_exceeds_limit(self):
        for n in range(0, 10):
            self.assertLessEqual(brain._body_slots(n, False), 9)


if __name__ == '__main__':
    unittest.main()
