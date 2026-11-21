# -*- coding: utf-8 -*-
"""回归：第 288 项 —— 错误报告带环境体检结论（20 条），五语言标题，且不重测。

要点：
  1. 有快照时 `check` 段必须有 items（>=15 条）与 ok/warn/fail 计数；
  2. 报告里 `check` 的标题语言跟随 brain.boot_lang()，英文界面不能出现中文标题；
  3. markdown 里必须有「环境体检」段，且五语言都有； 
  4. 没有快照时不能卡住（返回空 dict，并起后台线程补探测）。
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class ReportEnvCheck288(unittest.TestCase):
    def setUp(self):
        import env_check
        import brain
        # 造一份真快照（light 探测 ~3.4s，只在本类里跑一次）
        if not env_check.snapshot()[0]:
            env_check.probe_all(data_dir=str(brain.app_dir()),
                                db_path=str(brain.DB), light=True)

    def test_check_block_present(self):
        import error_report as er
        rep = er.build('t', 'UserFeedback', Exception('x'), None)
        ck = rep.get('check') or {}
        self.assertGreaterEqual(len(ck.get('items') or []), 15, '体检条目太少')
        for k in ('ok', 'warn', 'fail', 'age_s'):
            self.assertIn(k, ck)
        # 每条都有 level/key/title
        for it in ck['items']:
            self.assertIn(it['level'], ('OK', 'WARN', 'FAIL', 'INFO'))
            self.assertTrue(it['key'])

    def test_lang_follows_boot_lang(self):
        import error_report as er
        import brain
        with mock.patch.object(brain, 'boot_lang', return_value='en'):
            ck = er._env_check_block()
        self.assertEqual(ck.get('lang'), 'en')
        titles = ' '.join(it['title'] for it in ck['items'])
        # 只查「标签」里的中文：路径 / 文件夹名本来就是中文，不算漏翻
        for word in ('运行时', '数据目录', '磁盘剩余', '索引库', '聚合翻译',
                     '未授权', '开机自启', '索引库完好'):
            self.assertNotIn(word, titles, '英文界面下还有中文标签：%s' % word)
        self.assertIn('WebView2', titles)

    def test_markdown_section_all_langs(self):
        import error_report as er
        rep = er.build('t', 'UserFeedback', Exception('x'), None)
        md_zh = er.markdown(rep, 'zh-CN')
        self.assertIn('## 环境体检', md_zh)
        self.assertIn('OK', md_zh)
        for lang in ('en', 'es', 'pt', 'hi'):
            head = er._MD_L[er._md_lang(lang)]['check']
            self.assertTrue(head.startswith('##'), lang)
            md = er.markdown(rep, lang)
            # 五语言都要真的用上自己的标题（不是漏成中文）
            if lang != 'zh':
                self.assertNotIn('## 环境体检', md, '%s 走了中文标题' % lang)

    def test_no_snapshot_does_not_block(self):
        import error_report as er
        import env_check
        kept = dict(env_check._SNAP)
        try:
            env_check._SNAP.update({'t': 0.0, 'info': None})
            with mock.patch.object(er, '_check_worker') as w:
                t0 = __import__('time').time()
                got = er._env_check_block()
                dt = __import__('time').time() - t0
            self.assertEqual(got, {})
            self.assertLess(dt, 0.5, '没有快照时不该现跑探测')
            self.assertTrue(w.called)
        finally:
            env_check._SNAP.update(kept)

    def test_payload_size_ok(self):
        import error_report as er
        rep = er.build('t', 'UserFeedback', Exception('x'), None)
        size = len(json.dumps(rep, ensure_ascii=False).encode())
        self.assertLess(size, 30000, '报告 %d 字节，超过接收端 32KB 上限' % size)


if __name__ == '__main__':
    unittest.main()
