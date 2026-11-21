# -*- coding: utf-8 -*-
"""第 310 项：网络搜索加抖音 / 小红书 / TikTok，并按「国内 → 国外」排序。

用户原话：「增加抖音和tiktok和小红书，然后排序下，中国人会用的软件和外国人会用的软件」。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brain  # noqa: E402


CN_KEYS = ('b', 'dy', 'xhs', 'bi')
ABROAD_KEYS = ('g', 'yt', 'tt', 'bing', 'wiki', 'gh')


class WebEnginesTest(unittest.TestCase):
    def test_new_engines_in_defaults(self):
        keys = [e['key'] for e in brain.DEFAULT_WEB_SEARCH]
        for k in ('dy', 'xhs', 'tt'):
            self.assertIn(k, keys)
        for e in brain.DEFAULT_WEB_SEARCH:
            self.assertIn('{query}', e['url'])
            self.assertTrue(e['url'].startswith('https://'), e['url'])

    def test_order_cn_then_abroad(self):
        keys = [e['key'] for e in brain.DEFAULT_WEB_SEARCH]
        cn = [k for k in keys if k in CN_KEYS]
        abroad = [k for k in keys if k in ABROAD_KEYS]
        self.assertEqual(cn + abroad, keys, '国内引擎必须排在国外引擎前面')

    def test_factory_config_gets_reordered_and_new(self):
        # 老配置：只有那 7 条、老顺序
        old = [{'key': 'g', 'title': 'Google', 'url': 'https://www.google.com/search?q={query}', 'enabled': True},
               {'key': 'b', 'title': 'Baidu', 'url': 'https://www.baidu.com/s?wd={query}', 'enabled': True},
               {'key': 'gh', 'title': 'GitHub', 'url': 'https://github.com/search?q={query}', 'enabled': True},
               {'key': 'bing', 'title': 'Bing', 'url': 'https://www.bing.com/search?q={query}', 'enabled': True},
               {'key': 'wiki', 'title': 'Wikipedia', 'url': 'https://zh.wikipedia.org/w/index.php?search={query}', 'enabled': True},
               {'key': 'bi', 'title': 'Bilibili', 'url': 'https://search.bilibili.com/all?keyword={query}', 'enabled': True},
               {'key': 'yt', 'title': 'YouTube', 'url': 'https://www.youtube.com/results?search_query={query}', 'enabled': True}]
        eff = brain._web_search_effective(old)
        keys = [e['key'] for e in eff]
        self.assertEqual(keys, list(CN_KEYS) + list(ABROAD_KEYS))
        self.assertIn('dy', keys)
        self.assertIn('xhs', keys)
        self.assertIn('tt', keys)

    def test_deleted_old_engine_not_resurrected(self):
        # 用户删掉 Bing，但没改过别的：可以重排、可以补新引擎，但不能把 Bing 复活
        old = [{'key': 'g', 'title': 'Google', 'url': 'https://www.google.com/search?q={query}', 'enabled': True},
               {'key': 'b', 'title': 'Baidu', 'url': 'https://www.baidu.com/s?wd={query}', 'enabled': False}]
        keys = [e['key'] for e in brain._web_search_effective(old)]
        self.assertNotIn('bing', keys)
        dy = [e for e in brain._web_search_effective(old) if e['key'] == 'dy']
        self.assertTrue(dy and dy[0]['enabled'])

    def test_custom_config_kept_order(self):
        # 用户自己加过引擎 / 改过标题：顺序一条不动，只在后面追加新引擎
        mine = [{'key': 'ddg', 'title': 'DuckDuckGo', 'url': 'https://duckduckgo.com/?q={query}', 'enabled': True},
                {'key': 'g', 'title': '谷歌（我的）', 'url': 'https://www.google.com/search?q={query}', 'enabled': True}]
        eff = brain._web_search_effective(mine)
        self.assertEqual([e['key'] for e in eff[:2]], ['ddg', 'g'])
        self.assertEqual(eff[0]['title'], 'DuckDuckGo')
        self.assertEqual(eff[1]['title'], '谷歌（我的）')
        for k in ('dy', 'xhs', 'tt'):
            self.assertIn(k, [e['key'] for e in eff])

    def test_empty_config_falls_back_to_defaults(self):
        self.assertEqual(len(brain._web_search_effective([])), len(brain.DEFAULT_WEB_SEARCH))

    def test_url_builds_like_a_leader(self):
        # web_search_url 按 key 拼，抖音/小红书/TikTok 都要能拼出合法 https 链接
        for k in ('dy', 'xhs', 'tt'):
            u = brain.web_search_url(k, '小猫')
            self.assertTrue(u and u.startswith('https://'), '%s -> %r' % (k, u))
            self.assertNotIn('{query}', u)


if __name__ == '__main__':
    unittest.main()
