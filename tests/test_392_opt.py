# -*- coding: utf-8 -*-
"""第 392 项：两笔「不抢用户 CPU」的优化。

1. 引擎进程降到 BELOW_NORMAL：后台重排版不许跟用户抢核（实测转换能吃满 1 个核的 80~100%）。
2. 网格视图不再为 Office 文档排转换：一屏 20 个格子 = 20 次重排版，而网格只值 32px 小图。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import doc_render


class Base(unittest.TestCase):
    def setUp(self):
        self._old_auto = doc_render.WORKER_AUTOSTART
        doc_render.WORKER_AUTOSTART = False
        doc_render.clear_failure()
        with doc_render._LOCK:
            doc_render._TASKS.clear()
            doc_render._INFLIGHT.clear()
        doc_render._APPS.clear()
        self.dir = tempfile.mkdtemp(prefix='memex392-')

    def tearDown(self):
        with doc_render._LOCK:
            doc_render._TASKS.clear()
            doc_render._INFLIGHT.clear()
        doc_render._APPS.clear()
        doc_render.clear_failure()
        doc_render.WORKER_AUTOSTART = self._old_auto
        shutil.rmtree(self.dir, ignore_errors=True)


class TestPriority(Base):
    def test_empty_is_noop(self):
        self.assertEqual(doc_render._lower_priority([]), 0)
        self.assertEqual(doc_render._lower_priority(None), 0)

    def test_engine_procs_merges_recorded_and_new(self):
        doc_render._APPS['word'] = {'app': object(), 'last': 0, 'prog': 'Kwps.Application',
                                    'pids': (111,), 'spawn_ts': 123.0}
        with mock.patch.object(doc_render, '_engine_pids', lambda since=None: {222}):
            self.assertEqual(sorted(doc_render._engine_procs('word')), [111, 222])

    def test_app_for_lowers_priority_on_spawn(self):
        class FakeApp:
            Visible = True
            DisplayAlerts = 1

        calls = []

        def lower(pids):
            calls.append(sorted(pids))
            return len(calls[0])

        with mock.patch('win32com.client.DispatchEx', lambda prog: FakeApp()), \
                mock.patch.object(doc_render, '_engine_pids',
                                  lambda since=None: {4321}), \
                mock.patch.object(doc_render, '_lower_priority', lower):
            app = doc_render._app_for('word')
            self.assertIsNotNone(app)
            doc_render._lower_priority([4321])
        self.assertEqual(calls[0], [4321])
        self.assertEqual(calls[1], [4321])

    def test_convert_lowers_before_work(self):
        """每次开工前再降一次：复用的实例可能中途被别的逻辑改回去。"""
        doc_render._APPS['word'] = {'app': object(), 'last': 0, 'prog': 'Kwps.Application',
                                    'pids': (1,), 'spawn_ts': 0}
        seen = []

        def conv(app, src, out, box):
            seen.append('conv')
            with open(out, 'wb') as f:
                f.write(b'%PDF-1.4')

        with mock.patch.dict(doc_render._CONVERTERS, {'word': conv}), \
                mock.patch.object(doc_render, '_lower_priority',
                                  lambda pids: seen.append('lower')):
            box = {'mode': 'head'}
            ok = doc_render._convert_one('word', 'a.docx', os.path.join(self.dir, 'o.pdf'), box)
        self.assertTrue(ok)
        self.assertEqual(seen[:2], ['lower', 'conv'])


class TestGridNoConvert(Base):
    def _mk(self, name='a.docx'):
        p = os.path.join(self.dir, name)
        with open(p, 'wb') as f:
            f.write(b'PK\x03\x04fake')
        return p

    def test_grid_thumb_only_reads_cache(self):
        import brain
        p = self._mk()
        with mock.patch.object(doc_render, 'pdf_page_cached',
                               mock.Mock(return_value=(None, None))) as cached, \
                mock.patch.object(doc_render, 'pdf_page_source',
                                  mock.Mock(return_value=(None, None))) as src:
            th = brain.make_thumb(p, 320, 400)
        self.assertIsNone(th)
        self.assertTrue(cached.called, '网格应当只查缓存')
        self.assertFalse(src.called, '网格不该走会排队的路径')
        with doc_render._LOCK:
            self.assertEqual(doc_render._TASKS, [], '网格不许排转换')

    def test_detail_thumb_still_queues(self):
        import brain
        p = self._mk()
        with mock.patch.object(doc_render, 'pdf_page_cached') as cached:
            brain.make_thumb(p, 900, 1200)
        self.assertFalse(cached.called, '详情栏该走会排队的路径')
        with doc_render._LOCK:
            keys = [t[0] for t in doc_render._TASKS]
        self.assertTrue(any(k.endswith('|head') for k in keys), keys)


    def test_priority_watch_keeps_lowering_during_conversion(self):
        """WPS 打开文档时才起的子进程不继承优先级，转换过程中要一直盯着降。"""
        import time as _t
        calls = []

        def conv(app, src, out, box):
            _t.sleep(1.6)
            with open(out, 'wb') as f:
                f.write(b'%PDF-1.4')
            return True

        def lower(pids):
            calls.append(pids)

        with mock.patch.dict(doc_render._CONVERTERS, {'word': conv}), \
                mock.patch.object(doc_render, '_app_for', lambda kind: object()), \
                mock.patch.object(doc_render, '_engine_procs', lambda kind: {9}), \
                mock.patch.object(doc_render, '_lower_priority', lower):
            ok = doc_render._run_convert('word', 'a.docx', os.path.join(self.dir, 'o.pdf'),
                                         {'mode': 'head'})
            _t.sleep(0.3)                 # 让 watcher 收到停止信号
        self.assertTrue(ok)
        self.assertGreaterEqual(len(calls), 2, '转换途中要再降一次（1 秒一轮）')


if __name__ == '__main__':
    unittest.main()
