# -*- coding: utf-8 -*-
"""批量 OCR 队列（第 143 项）的单元测试。

队列是「后台线程 + 断点续跑 + 授权门控」，最容易坏的恰恰不是 OCR 准不准，
而是状态：暂停后有没有漏跑、崩溃重启后会不会偷偷吃满 CPU、失败项能不能重试。
所以这里完全不碰真实 OCR / 真实数据库，全部用注入的假 runner / sink 跑状态机。

另外单独验一条：`ocr_pdf` 加了 pages 参数之后，**免费索引的默认行为
必须还是前 6 页**。改免费功能的核心函数不留这条断言，将来没人敢动它。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain  # noqa: E402
import ocr_batch as Q  # noqa: E402


def wait_until(q, pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        s = q.status()
        if pred(s):
            return s
        time.sleep(0.01)
    return q.status()


class QueueStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._stop_all)      # LIFO：先停线程再删临时目录
        self.path = os.path.join(self.tmp.name, 'sub', 'q.json')
        self.sunk = []
        self.queues = []

    def _stop_all(self):
        """后台线程还活着时删临时目录，Windows 会报「目录不是空的」。"""
        for q in self.queues:
            try:
                q.clear()
            except Exception:
                pass
        for q in self.queues:
            try:
                if q._thread:
                    q._thread.join(0.5)
            except Exception:
                pass

    def mk(self, **kw):
        kw.setdefault('runner', lambda p, cb: {'text': '识别出的正文'})
        kw.setdefault('sink', lambda p, t: self.sunk.append((p, t)))
        kw.setdefault('sleep', lambda _s: time.sleep(0.01))
        q = Q.Queue(self.path, **kw)
        self.queues.append(q)
        return q

    # ------------------------------------------------------------ 入队
    def test_add_dedups_and_filters(self):
        q = self.mk(allowed=lambda p: p.endswith('.pdf'), auto_start=False)
        n = q.add(paths=['a.pdf', 'A.PDF', 'b.pdf', 'c.txt', '', None])
        self.assertEqual(n, 2)               # normcase 去重 + 后缀过滤
        self.assertEqual(q.status()['total'], 2)
        self.assertEqual(q.status()['run'], 'idle')
        q.resume()
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual((s['done'], s['pct']), (2, 100))

    def test_add_expands_dirs_and_respects_cap(self):
        q = self.mk(expand=lambda d: ['%s/%d.pdf' % (d, i) for i in range(30)],
                    max_items=5)
        self.assertEqual(q.add(dirs=['D:/x']), 5)
        self.assertEqual(q.add(paths=['more.pdf']), 0)   # 满了就别再塞
        self.assertEqual(q.status()['total'], 5)

    def test_expand_failure_does_not_break_add(self):
        q = self.mk(expand=lambda d: (_ for _ in ()).throw(OSError('gone')))
        self.assertEqual(q.add(dirs=['D:/x'], paths=['a.pdf']), 1)

    def test_status_only_returns_tail(self):
        q = self.mk(max_items=Q.STATUS_TAIL + 10)
        q.add(paths=['%d.pdf' % i for i in range(Q.STATUS_TAIL + 5)])
        self.assertEqual(len(q.status()['items']), Q.STATUS_TAIL)

    # ------------------------------------------------------------ 结果分类
    def test_runner_error_marks_failed(self):
        q = self.mk(runner=lambda p, cb: {'err': 'pymupdf 炸了'})
        q.add(paths=['a.pdf'])
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual((s['failed'], s['done']), (1, 0))
        self.assertEqual(s['items'][0]['err'], 'pymupdf 炸了')

    def test_empty_text_marks_failed_with_reason(self):
        q = self.mk(runner=lambda p, cb: {'text': '   '})
        q.add(paths=['a.pdf'])
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual(s['failed'], 1)
        self.assertEqual(s['items'][0]['err'], '没识别出文字')

    def test_skip_is_not_a_failure(self):
        q = self.mk(runner=lambda p, cb: {'skip': '已有文字层，不需要 OCR'})
        q.add(paths=['a.pdf'])
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual((s['skipped'], s['failed']), (1, 0))
        self.assertEqual(self.sunk, [])

    def test_sink_error_marks_failed_and_keeps_going(self):
        calls = []

        def runner(p, cb):
            calls.append(p)
            return {'text': 'x'}

        q = self.mk(runner=runner,
                    sink=lambda p, t: 'database is locked' if p == 'a.pdf' else None)
        q.add(paths=['a.pdf', 'b.pdf'])
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual((s['failed'], s['done']), (1, 1))
        self.assertEqual(calls, ['a.pdf', 'b.pdf'])

    def test_runner_exception_is_caught(self):
        def runner(p, cb):
            raise RuntimeError('boom')

        q = self.mk(runner=runner)
        q.add(paths=['a.pdf'])
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual(s['failed'], 1)
        self.assertIn('boom', s['items'][0]['err'])

    def test_page_progress_is_reported(self):
        def runner(p, cb):
            cb(1, 3)
            cb(2, 3)
            cb(3, 3)
            return {'text': 'x'}

        q = self.mk(runner=runner)
        q.add(paths=['a.pdf'])
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual(s['items'][0]['pages'], 3)   # 完成时把页数写进条目

    # ------------------------------------------------------------ 暂停 / 取消 / 重试
    def test_pause_then_resume_does_not_lose_items(self):
        gate, started = threading.Event(), threading.Event()

        def runner(p, cb):
            started.set()
            gate.wait(2)
            return {'text': 'x'}

        q = self.mk(runner=runner)
        q.add(paths=['a.pdf', 'b.pdf'])
        self.assertTrue(started.wait(1))
        q.pause()
        gate.set()
        s = wait_until(q, lambda x: x['done'] == 1 and x['run'] == 'paused')
        self.assertEqual(s['pending'], 1)
        q.resume()
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual(s['done'], 2)

    def test_double_resume_starts_only_one_worker(self):
        started, gate = threading.Event(), threading.Event()

        def runner(p, cb):
            started.set()
            gate.wait(2)
            return {'text': 'x'}

        q = self.mk(runner=runner, auto_start=False)
        q.add(paths=['a.pdf'])
        q.resume()
        q.resume()                       # 手快连点两下
        self.assertTrue(started.wait(1))
        self.assertEqual(q.status()['run'], 'running')
        gate.set()
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual(s['done'], 1)

    def test_cancel_marks_rest_skipped(self):
        q = self.mk(runner=lambda p, cb: {'text': 'x'}, auto_start=False)
        q.add(paths=['a.pdf', 'b.pdf', 'c.pdf'])
        q.cancel()
        s = q.status()
        self.assertEqual(s['skipped'], 3)
        self.assertEqual(s['run'], 'paused')

    def test_retry_failed_requeues_only_failed(self):
        q = self.mk(runner=lambda p, cb: {'err': 'x'} if p == 'a.pdf' else {'text': 'y'})
        q.add(paths=['a.pdf', 'b.pdf'])
        wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual(q.status()['failed'], 1)
        q.runner = lambda p, cb: {'text': 'y'}
        self.assertEqual(q.retry_failed(), 1)
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual((s['done'], s['failed']), (2, 0))

    def test_clear_resets_and_bumps_generation(self):
        q = self.mk(runner=lambda p, cb: {'text': 'x'})
        q.add(paths=['a.pdf', 'b.pdf'])
        wait_until(q, lambda x: x['run'] == 'done')
        q.clear()
        s = q.status()
        self.assertEqual((s['total'], s['run']), (0, 'idle'))
        with open(self.path, encoding='utf-8') as f:
            self.assertEqual(json.load(f)['gen'], 1)

    # ------------------------------------------------------------ 让路 / 授权
    def test_may_run_waits_instead_of_dropping(self):
        ok = {'v': False}
        q = self.mk(may_run=lambda: ok['v'])
        q.add(paths=['a.pdf'])
        time.sleep(0.08)
        s = q.status()
        self.assertEqual(s['pending'], 1)
        self.assertIn('有索引任务', s['note'])
        ok['v'] = True
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual(s['done'], 1)

    def test_authorization_loss_pauses_with_note(self):
        q = self.mk(authorized=lambda: False)
        q.add(paths=['a.pdf'])
        s = wait_until(q, lambda x: x['run'] == 'paused')
        self.assertIn('授权', s['note'])
        self.assertEqual(s['pending'], 1)
        self.assertEqual(self.sunk, [])

    # ------------------------------------------------------------ 持久化
    def test_crash_recovery_is_paused_and_keeps_breakpoint(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'v': 1, 'run': 'running', 'gen': 2,
                       'items': [{'p': 'a.pdf', 'st': 'done', 'chars': 3, 'pages': 1},
                                 {'p': 'b.pdf', 'st': 'running'},
                                 {'p': 'c.pdf', 'st': 'pending'}]}, f)
        q = self.mk()
        s = q.status()
        self.assertEqual(s['run'], 'paused')
        self.assertIn('接着跑', s['note'])
        self.assertEqual((s['done'], s['pending']), (1, 2))
        q.runner = lambda p, cb: {'text': 'x'}
        q.resume()
        s = wait_until(q, lambda x: x['run'] == 'done')
        self.assertEqual(s['done'], 3)

    def test_corrupt_state_file_falls_back_to_blank(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{not json at all')
        q = self.mk()
        self.assertEqual((q.status()['total'], q.status()['run']), (0, 'idle'))

    def test_save_is_atomic_no_tmp_left(self):
        q = self.mk(runner=lambda p, cb: {'text': 'x'})
        q.add(paths=['a.pdf'])
        wait_until(q, lambda x: x['run'] == 'done')
        self.assertFalse(os.path.exists(self.path + '.tmp'))
        self.assertTrue(os.path.isfile(self.path))


class FakePixmap:
    def tobytes(self, fmt):
        return b'png'


class FakePage:
    def __init__(self):
        self.rect = types.SimpleNamespace(width=600, height=800)

    def get_pixmap(self, matrix, alpha):
        return FakePixmap()

    def get_text(self):
        return ''


class FakeDoc:
    needs_pass = False
    page_count = 10

    def __init__(self):
        self.pages = [FakePage() for _ in range(10)]
        self.closed = False

    def __iter__(self):
        return iter(self.pages)

    def __getitem__(self, i):
        return self.pages[i]

    def close(self):
        self.closed = True


class OcrPdfCompatTest(unittest.TestCase):
    """改了免费索引的核心抽取函数，必须证明默认行为一个字没变。"""

    def setUp(self):
        self.docs = []
        self.calls = []

        def fake_open(_p):
            d = FakeDoc()
            self.docs.append(d)
            return d

        mod = types.ModuleType('pymupdf')
        mod.open = fake_open
        mod.Matrix = lambda a, b: (a, b)
        self.patcher = mock.patch.dict(sys.modules, {'pymupdf': mod})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

        def fake_ocr(_png):
            self.calls.append(1)
            return ([[None, '第七条 违约金']], None)

        p2 = mock.patch.object(brain, '_get_ocr', return_value=fake_ocr)
        p2.start()
        self.addCleanup(p2.stop)

    def test_default_still_caps_at_OCR_PAGES(self):
        text = brain.ocr_pdf(Path('x.pdf'))
        self.assertEqual(len(self.calls), brain.OCR_PAGES)
        self.assertIn('违约金', text)
        self.assertTrue(self.docs[0].closed)

    def test_pages_none_means_all_pages_and_reports_progress(self):
        seen = []
        brain.ocr_pdf(Path('x.pdf'), pages=None, budget=None,
                      on_page=lambda i, n: seen.append((i, n)))
        self.assertEqual(len(self.calls), 10)
        self.assertEqual(seen[0], (1, 10))
        self.assertEqual(seen[-1], (10, 10))

    def test_default_signature_keeps_backward_compatible_budget(self):
        self.assertEqual(brain.ocr_pdf.__defaults__,
                         (brain.OCR_BUDGET, brain.OCR_PAGES, None))


class BrainAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_allowed_rejects_other_extensions_and_missing(self):
        p = Path(self.tmp.name) / 'a.pdf'
        p.write_bytes(b'x')
        self.assertTrue(brain._ocrq_allowed(str(p)))
        self.assertFalse(brain._ocrq_allowed(str(p) + '.docx'))
        self.assertFalse(brain._ocrq_allowed(str(Path(self.tmp.name) / 'none.pdf')))

    def test_expand_skips_un_and_requires_real_dir(self):
        self.assertEqual(brain._ocrq_expand('\\\\server\\share'), [])
        self.assertEqual(brain._ocrq_expand(''), [])
        d = Path(self.tmp.name) / 'case'
        d.mkdir()
        (d / 'a.pdf').write_bytes(b'x')
        (d / 'b.txt').write_bytes(b'x')
        (d / '.hidden').mkdir()
        (d / '.hidden' / 'c.pdf').write_bytes(b'x')
        got = brain._ocrq_expand(str(d))
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].endswith('a.pdf'))

    def test_sink_writes_searchable_row_and_meta(self):
        """三张表必须成对写；写完这一刻正文就该能被 FTS 搜到。"""
        con = sqlite3.connect(':memory:')
        con.execute("CREATE VIRTUAL TABLE docs USING fts5("
                    "path UNINDEXED, ext UNINDEXED, body, tokenize='trigram')")
        con.execute('CREATE TABLE bigram(gram TEXT NOT NULL, rid INTEGER NOT NULL, '
                    'PRIMARY KEY(gram,rid)) WITHOUT ROWID')
        con.execute('CREATE TABLE meta(path TEXT PRIMARY KEY, indexed_at REAL, '
                    'size INTEGER, mtime REAL, reason TEXT)')
        p = Path(self.tmp.name) / '合同.pdf'
        p.write_bytes(b'x')

        class _Rids(dict):
            pass

        rids = _Rids()
        with mock.patch.object(brain, 'conn', return_value=con), \
             mock.patch.object(brain, '_OCRQ_RIDS', rids), \
             mock.patch.object(brain, '_bigram_add', return_value=None):
            self.assertIsNone(brain._ocrq_sink(str(p), '逾期一日按万分之三支付违约金'))
        hit = con.execute("SELECT rowid FROM docs WHERE docs MATCH ?",
                          ('"万分之三"',)).fetchone()
        self.assertIsNotNone(hit)
        row = con.execute('SELECT reason, size FROM meta WHERE path=?', (str(p),)).fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(row[1], 1)
        con.close()

    def test_pdf_text_chars_detects_text_layer(self):
        with mock.patch.object(brain, '_pdf_text_chars', return_value=500):
            r = brain._ocrq_run('x.pdf', lambda i, n: None)
        self.assertIn('文字层', r['skip'])


if __name__ == '__main__':
    unittest.main()
