# -*- coding: utf-8 -*-
"""第 391 项：转换看门狗 —— 一份文档卡死，不能把整个预览队列堵死。

真机事故：点了一份 19MB 的《服务操作手册》，WPS 导出进程 100% CPU 跑了 500 多秒不返回，
转换是单线程 inline 干的 → 之后点任何 docx 都只显示「首次预览正在排版转换」，永远不出图。
"""

import os
import shutil
import tempfile
import threading
import time
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
        self.dir = tempfile.mkdtemp(prefix='memex391-')
        self.out = os.path.join(self.dir, 'x.pdf')
        self._old_timeout = doc_render.CONV_TIMEOUT

    def tearDown(self):
        doc_render.CONV_TIMEOUT = self._old_timeout
        with doc_render._LOCK:
            doc_render._TASKS.clear()
            doc_render._INFLIGHT.clear()
        doc_render.clear_failure()
        doc_render.WORKER_AUTOSTART = self._old_auto
        shutil.rmtree(self.dir, ignore_errors=True)


class TestRunConvert(Base):
    def test_hang_times_out_and_kills_engine(self):
        doc_render.CONV_TIMEOUT = 0.3
        killed = []

        def fake_kill(kind):
            killed.append(kind)
            return [1234]

        def fake_convert(kind, src, out, box):
            time.sleep(3)                 # 假装 WPS 卡在导出里
            return True

        with open(self.out, 'wb') as f:
            f.write(b'%PDF-1.4 partial')  # 半截产物不能被当成「已转好」
        box = {'mode': 'head', 'q': '等保'}
        with mock.patch.object(doc_render, '_convert_one', fake_convert), \
                mock.patch.object(doc_render, '_kill_engine', fake_kill):
            t0 = time.time()
            ok = doc_render._run_convert('word', os.path.join(self.dir, 'a.docx'), self.out, box)
            self.assertFalse(ok)
            self.assertLess(time.time() - t0, 2.0, '超时必须按时返回，不能跟着卡死')
            self.assertTrue(box.get('_abort'), '要打标记，让被丢下的线程别再起新引擎')
            self.assertTrue(box.get('timeout'))
            self.assertIn('超时', box['err'])
            self.assertEqual(killed, ['word'])
            self.assertFalse(os.path.exists(self.out), '半截 PDF 要删掉')

    def test_normal_returns_true(self):
        doc_render.CONV_TIMEOUT = 5.0

        def fake_convert(kind, src, out, box):
            return True

        box = {'mode': 'head'}
        with mock.patch.object(doc_render, '_convert_one', fake_convert):
            self.assertTrue(doc_render._run_convert('word', 'a.docx', self.out, box))

    def test_queue_continues_after_one_hang(self):
        """卡死的那一份被放弃之后，下一份照样能转 —— 这就是本次修复的核心。"""
        doc_render.CONV_TIMEOUT = 0.3
        calls = []

        def fake_convert(kind, src, out, box):
            calls.append(src)
            if box.get('mode') == 'hang':
                time.sleep(3)
            return True

        with mock.patch.object(doc_render, '_convert_one', fake_convert), \
                mock.patch.object(doc_render, '_kill_engine', lambda kind: []):
            self.assertFalse(doc_render._run_convert('word', 'bad.docx', self.out,
                                                    {'mode': 'hang'}))
            self.assertTrue(doc_render._run_convert('word', 'good.docx', self.out,
                                                    {'mode': 'head'}))
        self.assertEqual(calls[:2], ['bad.docx', 'good.docx'])

    def test_aborted_box_does_not_spawn_engine(self):
        """看门狗放弃后，被丢下的那条线程不许再 _app_for 起一个新引擎。"""
        def boom(kind):
            raise AssertionError('不该再去拿引擎')

        box = {'mode': 'head', '_abort': True}
        with mock.patch.object(doc_render, '_app_for', boom):
            self.assertFalse(doc_render._convert_one('word', 'a.docx', self.out, box))


class TestFailureCooldown(Base):
    def test_timeout_uses_long_cooldown(self):
        doc_render._FAILED[doc_render._norm('a.docx')] = (time.time(), '超时', 300.0)
        self.assertTrue(doc_render._fail_cool(doc_render._norm('a.docx')))
        doc_render._FAILED[doc_render._norm('b.docx')] = (time.time() - 100, '超时', 60.0)
        self.assertFalse(doc_render._fail_cool(doc_render._norm('b.docx')))

    def test_status_for_reports_failed(self):
        p = os.path.join(self.dir, 'a.docx')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('x')
        doc_render._FAILED[doc_render._norm(p)] = (time.time(), '超时', 300.0)
        self.assertEqual(doc_render.status_for(p, self.dir), 'failed')

    def test_status_not_failed_for_plain_pending(self):
        p = os.path.join(self.dir, 'b.docx')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('x')
        doc_render.pdf_page_source(p, self.dir, 0, '等保')
        self.assertEqual(doc_render.status_for(p, self.dir), 'pending')


class TestEngineKill(Base):
    def test_kill_includes_late_spawned_children(self):
        """WPS 的文档子进程是打开文档时才生的，杀的时候要把「实例起来后新出现的」一起带上。"""
        doc_render._APPS['word'] = {'app': object(), 'last': time.time(),
                                    'prog': 'Word.Application', 'pids': (111,),
                                    'spawn_ts': time.time()}
        ran = []

        def fake_run(args, **kw):
            ran.append(args)
            return mock.Mock(returncode=0)

        with mock.patch.object(doc_render.subprocess, 'run', fake_run), \
                mock.patch.object(doc_render, '_engine_pids', lambda since=None: {222}):
            killed = doc_render._kill_engine('word')
        self.assertEqual(sorted(killed), [111, 222])

    def test_kill_uses_recorded_pids_only(self):
        """只杀「这个实例起来之后新出现的」进程，用户自己的 WPS 不动。"""
        doc_render._APPS['word'] = {'app': object(), 'last': time.time(),
                                    'prog': 'Word.Application',
                                    'pids': (111, 222), 'spawn_ts': time.time()}
        ran = []

        def fake_run(args, **kw):
            ran.append(args)
            return mock.Mock(returncode=0)

        with mock.patch.object(doc_render.subprocess, 'run', fake_run), \
                mock.patch.object(doc_render, '_engine_pids', lambda since=None: set()):
            killed = doc_render._kill_engine('word')
        self.assertEqual(sorted(killed), [111, 222])
        self.assertNotIn('word', doc_render._APPS, '实例记录要扔掉，下次重新起')
        self.assertEqual(len(ran), 2)
        for a in ran:
            self.assertIn('/F', a)

    def test_kill_without_entry_is_noop(self):
        self.assertEqual(doc_render._kill_engine('word'), [])

    def test_engine_pids_smoke(self):
        self.assertIsInstance(doc_render._engine_pids(), set)
        self.assertIsInstance(doc_render._engine_pids(time.time()), set)


class TestUnsupportedDoc(Base):
    """受保护文档（WPS「工具箱授权浏览」）：页数离谱 + 导出来是乱码，不该当成功。"""

    def _fake_app(self, pages, exported=None):
        doc = mock.Mock()
        doc.ComputeStatistics.return_value = pages
        doc.ExportAsFixedFormat.side_effect = exported or (lambda *a, **k: None)
        app = mock.Mock()
        app.Documents.Open.return_value = doc
        return app, doc

    def test_absurd_page_count_is_unsupported(self):
        app, doc = self._fake_app(101560)
        box = {'mode': 'head', 'q': '等保'}
        with self.assertRaises(doc_render._Unsupported):
            doc_render._convert_word(app, 'a.docx', self.out, box)
        doc.ExportAsFixedFormat.assert_not_called()   # 不许再白导那 25 秒
        doc.Close.assert_called_once()                # 文档要关上

    def test_sane_page_count_still_converts(self):
        app, doc = self._fake_app(180)
        box = {'mode': 'head', 'q': ''}
        doc_render._convert_word(app, 'a.docx', self.out, box)
        self.assertEqual(box['pages'], 180)
        doc.ExportAsFixedFormat.assert_called_once()

    def test_unsupported_is_not_retried(self):
        calls = []

        def boom(app, src, out, box):
            calls.append(src)
            raise doc_render._Unsupported('页数异常')

        box = {'mode': 'head'}
        with mock.patch.dict(doc_render._CONVERTERS, {'word': boom}), \
                mock.patch.object(doc_render, '_app_for', lambda kind: object()), \
                mock.patch.object(doc_render, '_drop_app') as drop:
            ok = doc_render._convert_one('word', 'a.docx', self.out, box)
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1, '注定预览不了的不该换引擎再试一遍')
        self.assertTrue(box.get('unsupported'))
        drop.assert_not_called()                      # 引擎没坏，不用扔

    def test_unsupported_uses_long_cooldown(self):
        self.assertEqual(doc_render._fail_cooldown({'unsupported': True}),
                         doc_render.TIMEOUT_COOLDOWN)
        self.assertEqual(doc_render._fail_cooldown({'timeout': True}),
                         doc_render.TIMEOUT_COOLDOWN)
        self.assertEqual(doc_render._fail_cooldown({}), doc_render.FAIL_COOLDOWN)

    def test_total_pages_ignores_absurd_meta(self):
        p = os.path.join(self.dir, 'a.docx')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('x')
        doc_render._write_meta(p, self.dir, 101560, 8, 1, 0, '等保')
        self.assertEqual(doc_render.total_pages(p, self.dir, '等保'), 0)


    def test_drop_app_never_blocks(self):
        """Quit 卡住时不能把转换线程拖住（真机就是这样又卡了一次队列）。"""
        class SlowApp:
            def Quit(self):
                time.sleep(2)

        doc_render._APPS['word'] = {'app': SlowApp(), 'last': time.time(),
                                    'prog': 'Word.Application', 'pids': (), 'spawn_ts': 0}
        t0 = time.time()
        doc_render._drop_app('word')
        self.assertLess(time.time() - t0, 0.5)
        self.assertNotIn('word', doc_render._APPS)


if __name__ == '__main__':
    unittest.main()
