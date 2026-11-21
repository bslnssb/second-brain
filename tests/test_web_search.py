# -*- coding: utf-8 -*-
"""第 203 项：网络搜索的纯函数边界（不联网、不开浏览器）。"""
import unittest

import brain


class TestWebSearch(unittest.TestCase):
    def test_defaults_are_valid(self):
        # 不依赖用户本机 settings.json（可能被改过），强制走出厂默认
        from unittest.mock import patch
        with patch.object(brain, '_settings_read', return_value={}):
            items = brain.web_search_items()
        self.assertTrue(items)
        for it in items:
            self.assertTrue(it['key'])
            self.assertIn('{query}', it['url'])
            self.assertTrue(it['url'].startswith('https://'))

    def test_reject_bad_schemes_and_missing_placeholder(self):
        bad = [
            {'key': 'x', 'title': 'x', 'url': 'file:///C:/x?q={query}'},
            {'key': 'y', 'title': 'y', 'url': 'javascript:alert({query})'},
            {'key': 'z', 'title': 'z', 'url': 'https://example.com/fixed'},
            {'key': 'bad key', 'title': 'b', 'url': 'https://a.com/?q={query}'},
        ]
        out = brain._normalize_web_search(bad)
        self.assertEqual([], out)

    def test_disabled_engine_is_kept_but_flagged(self):
        v = [{'key': 'w', 'title': 'w', 'url': 'https://a.com/?q={query}',
              'enabled': False}]
        out = brain._normalize_web_search(v)
        self.assertEqual(1, len(out))
        self.assertIs(False, out[0]['enabled'])

    def test_dedup_and_cap(self):
        v = [{'key': 'g', 'title': 'A', 'url': 'https://a.com/?q={query}'},
             {'key': 'g', 'title': 'B', 'url': 'https://b.com/?q={query}'},
             {'key': 'b', 'title': 'C', 'url': 'https://c.com/?q={query}'}]
        out = brain._normalize_web_search(v)
        self.assertEqual(['g', 'b'], [x['key'] for x in out])

    def test_url_encodes_chinese_query(self):
        from unittest.mock import patch
        with patch.object(brain, '_settings_read', return_value={}):
            url = brain.web_search_url('g', '早上好中国')
        self.assertIsNotNone(url)
        self.assertIn('www.google.com', url)
        self.assertNotIn('早上好中国', url)   # 必须 percent-encode
        self.assertIn('%E6%97%A9', url)

    def test_empty_query_returns_none(self):
        self.assertIsNone(brain.web_search_url('g', ''))
        self.assertIsNone(brain.web_search_url('g', '   '))

    def test_unknown_key_returns_none(self):
        from unittest.mock import patch
        with patch.object(brain, '_settings_read', return_value={}):
            self.assertIsNone(brain.web_search_url('no-such-engine', 'abc'))

    def test_invalid_settings_falls_back_to_defaults(self):
        self.assertEqual([], brain._normalize_web_search('not a list'))
        self.assertEqual([], brain._normalize_web_search(None))

    def test_defaults_include_bing_and_keys_unique(self):
        """第 270 项：界面「恢复默认引擎」靠这张出厂表补回被删的条目。

        用户这次删掉的正是 Bing，所以它必须留在默认表里；key 也不能重复，
        否则前端按 key 判重会补错。
        """
        keys = [e['key'] for e in brain.DEFAULT_WEB_SEARCH]
        self.assertIn('bing', keys)
        self.assertEqual(len(keys), len(set(keys)))
        # 默认下发的新增字段不能反过来污染白名单之外的东西
        self.assertTrue(all(e.get('enabled') is True for e in brain.DEFAULT_WEB_SEARCH))

    def test_titles_are_latin_for_arabic_users(self):
        """第 275 项：界面切成阿拉伯语/印地语时，中文标题看不懂。

        出厂表里的标题一律只允许 ASCII（Google / Baidu / Bing / Bilibili…），
        别再冒「百度」「哔哩哔哩」这种只有中文用户才认得的字。
        """
        for e in brain.DEFAULT_WEB_SEARCH:
            self.assertTrue(e['title'].isascii(), '%s 的标题不是拉丁字母：%s'
                            % (e['key'], e['title']))
        titles = [e['title'] for e in brain.DEFAULT_WEB_SEARCH]
        self.assertIn('Baidu', titles)
        self.assertIn('Bilibili', titles)

    def test_old_chinese_titles_get_migrated(self):
        """老 settings.json 里存的是「百度」「哔哩哔哩」，读出来要自动变英文；
        但用户自己起的名字（比如「百度一下」）不许被改。"""
        old = [{'key': 'b', 'title': '百度', 'url': 'https://www.baidu.com/s?wd={query}', 'enabled': True},
               {'key': 'bi', 'title': '哔哩哔哩', 'url': 'https://search.bilibili.com/all?keyword={query}', 'enabled': True},
               {'key': 'mine', 'title': '百度一下', 'url': 'https://www.baidu.com/s?wd={query}', 'enabled': True}]
        out = brain._normalize_web_search(old)
        got = {e['key']: e['title'] for e in out}
        self.assertEqual(got['b'], 'Baidu')
        self.assertEqual(got['bi'], 'Bilibili')
        self.assertEqual(got['mine'], '百度一下')


if __name__ == '__main__':
    unittest.main()
