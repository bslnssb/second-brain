# -*- coding: utf-8 -*-
"""apprank.py 的单元测试（常用应用白名单的匹配 / 加权逻辑，之前完全没测试）。

这些函数决定了「搜微信时微信本体排最前、而不是微信聊天记录」这类核心体验，
一旦回归会静默变差，所以锁成单测。

跑法：python -m unittest discover -s tests -p "test_*.py" -v
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import apprank  # noqa: E402

HAS_PYPINYIN = importlib.util.find_spec('pypinyin') is not None


class NormTest(unittest.TestCase):
    def test_fullwidth_to_halfwidth_and_lower(self):
        self.assertEqual(apprank._norm(' ＷｅＣｈａｔ '), 'wechat')
        self.assertEqual(apprank._norm('WeChat'), 'wechat')
        self.assertEqual(apprank._norm('微信'), '微信')

    def test_internal_space_collapsed(self):
        self.assertEqual(apprank._norm('We Chat'), 'wechat')


class MatchTest(unittest.TestCase):
    def test_exact_primary_is_tier0(self):
        res = apprank.match('微信')
        self.assertTrue(res)
        self.assertEqual(res[0][0].name, '微信')
        self.assertEqual(res[0][1], 0)

    def test_one_char_query_matches_nothing_by_prefix(self):
        # 前缀匹配要求 >=2 字，单字只走完全相等；没有任何应用主名是单字
        self.assertEqual(apprank.match('w'), [])

    def test_alias_prefix_is_lower_tier(self):
        res = apprank.match('wech')  # wechat 的前缀
        names = [a.name for a, _ in res]
        self.assertIn('微信', names)
        # 完全相等的档位要压过别名前缀
        self.assertEqual(res[0][1], 3)

    def test_two_apps_ranked_by_tier(self):
        # 搜 qq音乐：qq音乐 主名完全相等(tier0) 要压过 QQ(别名前缀 tier3)
        # 注：白名单里该应用登记名是小写 'qq音乐'，以此为准
        res = apprank.match('qq音乐')
        self.assertEqual(res[0][0].name, 'qq音乐')
        self.assertEqual(res[0][1], 0)


class BoostTest(unittest.TestCase):
    def test_exe_name_hits_full_weight(self):
        b = apprank.boost('微信', 'Weixin.exe',
                          r'C:\Program Files\Tencent\Weixin\Weixin.exe')
        self.assertAlmostEqual(b, 1.00, places=4)

    def test_lnk_with_chinese_name_boosted(self):
        b = apprank.boost('微信', '微信.lnk',
                          r'C:\Users\me\Desktop\微信.lnk')
        # 主名 tier0 -> 1.00，lnk 快捷方式 -0.08
        self.assertAlmostEqual(b, 0.92, places=4)

    def test_non_app_file_not_boosted(self):
        b = apprank.boost('微信', '微信聊天记录.txt',
                          r'D:\备份\微信聊天记录.txt')
        self.assertEqual(b, 0.0)

    def test_unrelated_query_zero(self):
        b = apprank.boost('report', 'report.docx', r'D:\a\report.docx')
        self.assertEqual(b, 0.0)

    def test_qq_exe_matches_qq_not_qqmusic(self):
        # 搜 qq 命中 QQ（tier0）；QQMusic.exe 的 exe 不在 QQ 的 exes 里，不应被误加成 1.0
        b = apprank.boost('qq', 'QQ.exe', r'C:\QQ\QQ.exe')
        self.assertAlmostEqual(b, 1.00, places=4)
        b2 = apprank.boost('qq', 'QQMusic.exe', r'C:\QQMusic\QQMusic.exe')
        # 该文件属于 QQ音乐，对 qq 查询只按别名前缀加权(0.70)，不是 QQ 本体
        self.assertLess(b2, 1.00)
        self.assertGreater(b2, 0.0)


class NameForExeTest(unittest.TestCase):
    def test_known_exe(self):
        self.assertEqual(apprank.name_for_exe('Weixin.exe'), '微信')
        self.assertEqual(apprank.name_for_exe('weixin.exe'), '微信')

    def test_unknown_exe(self):
        self.assertIsNone(apprank.name_for_exe('totally-unknown-xyz.exe'))


class IsAppFileTest(unittest.TestCase):
    def test_app_extensions(self):
        for f in ('Weixin.exe', '微信.lnk', 'run.bat', 'setup.msi', 'x.appref-ms'):
            self.assertTrue(apprank.is_app_file(f), f)
        self.assertFalse(apprank.is_app_file('report.docx'))
        # 白名单里的 exe 即便不带扩展名也算
        self.assertTrue(apprank.is_app_file('qqsc.exe'))


if __name__ == '__main__':
    unittest.main()
