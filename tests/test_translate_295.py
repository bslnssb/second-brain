# -*- coding: utf-8 -*-
"""第 295 项：聚合翻译为什么只剩 MyMemory —— 回归测试。

覆盖三件事：
1. 引擎清单里腾讯那家在（国内免密钥整句引擎）；
2. Google 两种返回形状都能解析（带 sl 时是扁平数组，这是本轮的真 bug）；
3. 跳过原因带 code（前端才能按界面语言显示），代理设置能读出来。
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location('translator295', os.path.join(ROOT, 'translator.py'))
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)


class EngineListTest(unittest.TestCase):
    def test_01_tencent_registered(self):
        ids = tr.ENGINE_IDS
        self.assertIn('tencent', ids)
        self.assertIn('tencent', tr._ENGINE_FETCHERS)
        names = [e['name'] for e in tr.ENGINES if e['id'] == 'tencent']
        self.assertEqual(['腾讯交互翻译'], names)

    def test_02_dict_only_still_dictionary(self):
        # 有道/百度/金山 只打词语，句子不该发给它们
        for k in ('youdao', 'baidu', 'iciba'):
            self.assertIn(k, tr._DICT_ONLY)
        self.assertFalse(tr.looks_like_word('The meeting has been moved to three tomorrow.'))


class GoogleShapeTest(unittest.TestCase):
    """第 295 项的真 bug：指定源语言时 Google 回的是 ['译文']，旧代码只认 [['译文','en']]。"""

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body.encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _patch_open(self, body):
        orig = tr._open
        tr._open = lambda req, timeout=None: GoogleShapeTest._Resp(body)
        self.addCleanup(lambda: setattr(tr, '_open', orig))

    def test_03_flat_array(self):
        self._patch_open('["会议已推迟到明天下午三点。"]')
        out = tr._fetch_google('The meeting has been moved to three o clock tomorrow.',
                               'en', 'zh-CN')
        self.assertEqual('会议已推迟到明天下午三点。', (out or {}).get('text'))

    def test_04_nested_array(self):
        self._patch_open('[["会议已推迟到明天下午三点。","en"]]')
        out = tr._fetch_google('The meeting has been moved to three o clock tomorrow.',
                               'auto', 'zh-CN')
        self.assertEqual('会议已推迟到明天下午三点。', (out or {}).get('text'))

    def test_05_empty_body_is_none(self):
        self._patch_open('[]')
        self.assertIsNone(tr._fetch_google('hello', 'en', 'zh-CN'))


class SkipCodeTest(unittest.TestCase):
    """跳过原因必须带 code —— 前端靠它翻成界面语言，不然英文界面里冒出中文。"""

    def test_06_skipped_has_code(self):
        r = tr.translate_text('The meeting has been moved to three o clock tomorrow.',
                              'en', 'zh-CN', engines=['youdao'])
        self.assertFalse(r['results'])
        codes = [s.get('code') for s in r['skipped']]
        self.assertIn('dict_only', codes)

    def test_07_proxy_settings_key_none_means_direct(self):
        # 'none' / 'direct' 明确要求直连，不能被系统代理覆盖
        if not hasattr(tr, 'proxy_url'):
            self.skipTest('没有代理层')
        tr._PROXY_CACHE.update({'at': 0.0, 'url': '', 'raw': None})
        self.assertIn(tr.proxy_url().lower()[:6], ('', 'http:/', 'https:'))

    def test_08_extra_code_for_cn_direct(self):
        self.assertEqual('cn_direct', tr.EXTRA_CODES.get('国内直连'))


class ActiveEnginesTest(unittest.TestCase):
    """老配置只存了 4 家（用户第 268 项那次保存的），后来新增的引擎必须自动补上。"""

    def test_09_legacy_list_gets_new_engines(self):
        chosen = tr.active_engines({'trans_engines': ['google', 'youdao', 'baidu', 'mymemory']})
        self.assertIn('tencent', chosen, '新引擎不该被老清单挡在外面')
        self.assertIn('deepseek', chosen, '第 274 项的自带密钥也不该被挡')
        self.assertNotIn('iciba', chosen, '用户当时就能看到 iciba 却没勾，保持关')

    def test_10_saved_after_295_stays_as_is(self):
        chosen = tr.active_engines({'trans_engines': ['google'],
                                    'trans_engines_ver': tr.ENGINE_VER})
        self.assertEqual(['google'], chosen, '用户在最新版本里关掉的，不许再补回来')

    def test_11_no_setting_means_default_four(self):
        # 第 346 项：全新安装只开前四个（用户原话「默认只启动前4个」）。
        # 原来这里断言「没设置 = 全开」，十一引擎一起打，首屏等最慢那家。
        self.assertEqual(tr.DEFAULT_ENGINES, tr.active_engines({}))
        self.assertEqual(tr.DEFAULT_ENGINES, tr.active_engines(None))

    def test_12_engine_ver_matches_newest_since(self):
        newest = max(int(e.get('since') or 0) for e in tr.ENGINES)
        self.assertEqual(newest, tr.ENGINE_VER)


if __name__ == '__main__':
    unittest.main()
