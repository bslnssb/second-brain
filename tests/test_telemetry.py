# -*- coding: utf-8 -*-
"""第 277 项：本机指标 / 面包屑 / 错误收集新入口。

这些测试盯的是「统计和取证不能被功能本身拖垮」，以及隐私边界：
只落计数与枚举，绝不落搜索词、路径、剪贴板内容。
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import breadcrumb
import error_report as E
import feature_metrics as M


class MetricsTest(unittest.TestCase):
    def setUp(self):
        M.reset()
        self.d = Path(tempfile.mkdtemp())
        M.configure(self.d)

    def tearDown(self):
        M.reset()

    def test_add_and_snapshot(self):
        for _ in range(10):
            M.add('translate', 'ok', ms=50)
        M.add('translate', 'fail', ms=900, err='timeout')
        snap = M.snapshot()
        row = next(r for r in snap['features'] if r['name'] == 'translate')
        self.assertEqual(11, row['calls'])
        self.assertEqual(10, row['ok'])
        self.assertEqual(1, row['fail'])
        self.assertAlmostEqual(1 / 11.0, row['bad_rate'], places=3)
        self.assertGreater(row['p95'], 0)
        self.assertEqual(1, row['h24']['bad'])

    def test_flush_writes_atomically_and_reloads(self):
        M.add('ocr', 'empty', ms=300)
        self.assertTrue(M.flush(True))
        p = self.d / M.CONF_NAME
        self.assertTrue(p.is_file())
        raw = json.loads(p.read_text(encoding='utf-8'))
        self.assertEqual(1, raw['features']['ocr']['empty'])
        # 重新 configure 应从磁盘读回
        M.reset()
        M.configure(self.d)
        row = next(r for r in M.snapshot()['features'] if r['name'] == 'ocr')
        self.assertEqual(1, row['calls'])

    def test_feature_table_is_bounded(self):
        for i in range(M.MAX_FEATURES + 8):
            M.add('f%d' % i)
        self.assertLessEqual(len(M.snapshot()['features']), M.MAX_FEATURES)

    def test_subs_are_bounded_and_truncated(self):
        for i in range(M.MAX_SUBS + 6):
            M.add('translate', sub='engine-%02d-xxxxxxxxxxxxxxxx' % i)
        row = next(r for r in M.snapshot()['features'] if r['name'] == 'translate')
        self.assertLessEqual(len(row['subs']), M.MAX_SUBS)
        for k in row['subs']:
            self.assertLessEqual(len(k), 24)

    def test_timer_records_fail_on_exception(self):
        with self.assertRaises(RuntimeError):
            with M.timer('index', sub='content'):
                raise RuntimeError('boom')
        row = next(r for r in M.snapshot()['features'] if r['name'] == 'index')
        self.assertEqual(1, row['fail'])

    def test_bad_input_never_raises(self):
        self.assertTrue(M.add(None, 'weird', ms='abc', sub=object(), err=None))
        M.note(None, None)
        M.count(None, None)


class BreadcrumbTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        breadcrumb.configure(self.d)

    def tearDown(self):
        breadcrumb.clear()

    def test_keeps_enum_drops_content(self):
        breadcrumb.add('panel', v='translate')
        breadcrumb.add('search', q='季度财报 机密')
        breadcrumb.add('open', path=r'C:\Users\x\secret.docx')
        items = breadcrumb.snapshot()
        self.assertEqual(3, len(items))          # 动作名保留（有用），值必须被丢
        self.assertEqual('panel', items[0]['a'])
        self.assertEqual('translate', items[0]['v'])
        for it in items[1:]:
            self.assertNotIn('q', it)
            self.assertNotIn('path', it)
        self.assertNotIn('机密', json.dumps(items, ensure_ascii=False))

    def test_ring_is_capped(self):
        for i in range(breadcrumb.BUF_MAX + 10):
            breadcrumb.add('act', n=i)
        self.assertEqual(breadcrumb.BUF_MAX, len(breadcrumb.snapshot()))

    def test_persist_and_load(self):
        breadcrumb.add('panel', v='about')
        self.assertTrue(breadcrumb.persist({'exit': 'clean'}))
        raw = json.loads((self.d / breadcrumb.PERSIST_NAME).read_text(encoding='utf-8'))
        self.assertEqual('clean', raw['exit'])
        self.assertEqual(1, len(breadcrumb.load()))


class ReportCaptureTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        E.configure(self.d, 'v9-test')
        E.clear_reports()
        E.save_conf({'collect': True, 'auto': False, 'include_paths': False})
        E._CTX['guard'] = False
        E._CTX['prev_checked'] = False

    def test_same_signature_merges_count(self):
        for _ in range(3):
            E.save_report({'v': 1, 'kind': 'X', 'where': 'u', 'message': 'm',
                           'trace': 'File "a.py", line 3\n'})
        items = E.list_reports()
        self.assertEqual(1, len(items))
        rep = E.get_report(items[0]['id'])
        self.assertEqual(3, rep['count'])
        self.assertTrue(rep['sig'])

    def test_different_signature_kept_separately(self):
        E.save_report({'v': 1, 'kind': 'X', 'where': 'u', 'trace': 'File "a.py", line 1'})
        E.save_report({'v': 1, 'kind': 'Y', 'where': 'u', 'trace': 'File "a.py", line 1'})
        self.assertEqual(2, len(E.list_reports()))

    def test_capture_api_strips_query_string(self):
        try:
            raise ValueError('boom in handler')
        except ValueError:
            rid = E.capture_api('/api/translate?q=%E7%A7%98%E5%AF%86&lang=zh')
        self.assertTrue(rid)
        rep = E.get_report(rid)
        self.assertEqual('api:/api/translate', rep['where'])
        raw = json.dumps(rep, ensure_ascii=False)
        self.assertNotIn('q=', raw)
        self.assertNotIn('秘密', raw)

    def test_save_js_keeps_no_input_content(self):
        rid = E.save_js({'kind': 'RenderError', 'message': 'x is not a function',
                         'stack': 'at App (main.tsx:1)\nat y (ui.tsx:2)',
                         'route': '/settings?q=机密'})
        self.assertTrue(rid)
        rep = E.get_report(rid)
        self.assertTrue(rep['where'].startswith('web:'))
        self.assertNotIn('?', rep['where'])
        self.assertNotIn('机密', json.dumps(rep, ensure_ascii=False))

    def test_abnormal_exit_report_and_clean_run(self):
        run = self.d / E.RUN_NAME
        run.write_text(json.dumps({'pid': 999, 'start': time.time() - 30,
                                   'stage': 'ready', 'clean': False}), encoding='utf-8')
        (self.d / breadcrumb.PERSIST_NAME).write_text(
            json.dumps({'items': [{'t': '10:00:00', 'a': 'panel', 'v': 'about'}]}),
            encoding='utf-8')
        E._CTX['guard'] = True
        E._CTX['prev_checked'] = False
        rid = E.check_prev_run()
        self.assertTrue(rid)
        rep = E.get_report(rid)
        self.assertEqual('AbnormalExit', rep['kind'])
        self.assertEqual('ready', rep['env']['last_stage'])
        self.assertEqual(1, len(rep['breadcrumb']))

        # 正常退出后不应再报
        E.mark_clean()
        E.clear_reports()
        E._CTX['prev_checked'] = False
        self.assertEqual('', E.check_prev_run())

    def test_backoff_skips_send(self):
        (self.d / E.INGEST_NAME).write_text(
            json.dumps({'enabled': True, 'url': 'https://example.com/r'}), encoding='utf-8')
        E.save_conf({'next_try': time.time() + 120, 'fail_streak': 1})
        E.save_report({'v': 1, 'kind': 'B'})
        with mock.patch.object(E, '_post') as m:
            r = E.send_pending()
        self.assertTrue(r.get('skipped'))
        self.assertEqual(0, r['sent'])
        self.assertFalse(m.called)

    def test_status_exposes_spool(self):
        E.save_report({'v': 1, 'kind': 'S'})
        st = E.status()
        self.assertEqual(1, st['spool']['count'])
        self.assertIn('send_fail', st['spool'])
        self.assertNotIn('url', st['conf'])


class EnvProbeTest(unittest.TestCase):
    def test_translate_probe_reports_all_down(self):
        import env_check as ec
        with mock.patch.object(ec, '_tcp', return_value=(False, 'timed out')):
            out = ec._probe_translate(deep=True)
        self.assertEqual(0, out['ok_n'])
        self.assertFalse(out['usable'])

    def test_translate_probe_counts_up(self):
        import env_check as ec
        with mock.patch.object(ec, '_tcp', return_value=(True, 20)):
            out = ec._probe_translate(deep=True)
        self.assertEqual(out['n'], out['ok_n'])
        self.assertTrue(out['usable'])

    def test_translate_probe_light_does_no_network(self):
        import env_check as ec
        with mock.patch.object(ec, '_tcp') as m:
            ec._probe_translate(deep=False)
        self.assertFalse(m.called)

    def test_index_health_on_fake_db(self):
        import sqlite3
        import env_check as ec
        d = Path(tempfile.mkdtemp()) / 'brain.db'
        con = sqlite3.connect(d)
        con.executescript('CREATE TABLE docs(id INTEGER); CREATE TABLE files(p TEXT);'
                          'CREATE TABLE meta(reason TEXT);')
        con.execute('INSERT INTO docs VALUES (1)')
        con.execute('INSERT INTO files VALUES ("a.txt")')
        con.execute('INSERT INTO meta VALUES ("无文本")')
        con.commit()
        con.close()
        out = ec._probe_index_health(d)
        self.assertTrue(out['ok'])
        self.assertEqual(1, out['docs'])
        self.assertEqual(1, out['files'])

    def test_judge_tolerates_new_sections(self):
        import env_check as ec
        info = {'data_writable': True, 'screens': [], 'db_state': 'missing',
                'free_port': 8765, 'ports': {}, 'translate': {'ok_n': 0, 'n': 5},
                'ocr': {'import': False, 'err': 'no onnx'},
                'index_health': {'state': 'missing'}, 'clipboard': {},
                'hotkey': {}, 'update': {}, 'license': {'state': 'free'},
                'autostart': {}, 'resources': {}, 'capture': {}, 'libsets': {},
                'reporter': {}}
        issues = ec.judge(info, 'zh')
        keys = {i['key'] for i in issues}
        self.assertIn('translate', keys)
        self.assertIn('ocr', keys)


if __name__ == '__main__':
    unittest.main()
