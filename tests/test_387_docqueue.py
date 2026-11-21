# -*- coding: utf-8 -*-
"""第 387 项：预览转换改成后台排队（请求线程绝不阻塞）。

用户反馈「搜 ext:word，详情里的图一直不出来，过一会儿出来后又卡得要死」。
真因：25s 同步超时后线程没停、锁却放了，前端一重试就再起一个 WPS，实例越堆越多。
这里只钉**不依赖 COM** 的约定：排队立即返回、同文件不重复排队、队列只留最新 3 个、
「排队中」不算失败（不会被 brain 的 5 分钟失败冷却挡掉）。
"""

import os
import tempfile
import time
import unittest
from unittest import mock

import doc_render


def make_file(d, name, size=16):
    p = os.path.join(d, name)
    with open(p, 'wb') as fh:
        fh.write(b'x' * size)
    return p


class TestNonBlockingQueue(unittest.TestCase):
    def setUp(self):
        doc_render.WORKER_AUTOSTART = False       # 测试机上不真起 WPS
        doc_render.clear_failure()
        with doc_render._LOCK:
            doc_render._TASKS.clear()
            doc_render._INFLIGHT.clear()
        self.dir = tempfile.mkdtemp(prefix='memex387-')
        self.addCleanup(self._clean)

    def _clean(self):
        import shutil
        doc_render.WORKER_AUTOSTART = True
        with doc_render._LOCK:
            doc_render._TASKS.clear()
            doc_render._INFLIGHT.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_uncached_returns_immediately(self):
        """没缓存时 pdf_for 必须立刻返回 None（以前会同步等 25s，把请求线程占死）。"""
        f = make_file(self.dir, 'a.docx')
        t = time.time()
        self.assertIsNone(doc_render.pdf_for(f, self.dir))
        self.assertLess(time.time() - t, 0.1, 'pdf_for 不该阻塞请求线程')

    def test_status_pending_after_call(self):
        f = make_file(self.dir, 'b.docx')
        doc_render.pdf_for(f, self.dir)
        self.assertEqual(doc_render.status_for(f, self.dir), 'pending')

    def test_same_file_queued_once(self):
        f = make_file(self.dir, 'c.docx')
        for _ in range(5):
            doc_render.pdf_for(f, self.dir)
        self.assertEqual(doc_render.queue_len(), (1, 1), '同一份文件不该排 5 次')

    def test_queue_keeps_only_newest_three(self):
        for i in range(6):
            doc_render.pdf_for(make_file(self.dir, 'q%d.docx' % i), self.dir)
            time.sleep(0.01)
        queued, inflight = doc_render.queue_len()
        self.assertEqual(queued, 3, '队列最多留 3 个')
        self.assertEqual(inflight, 3, '被丢掉的任务不能占着 inflight')

    def test_status_of_cached_and_plain(self):
        f = make_file(self.dir, 'cached.docx')
        out = doc_render.cache_path(f, self.dir)
        with open(out, 'wb') as fh:
            fh.write(b'%PDF-1.4')
        self.assertEqual(doc_render.status_for(f, self.dir), 'ok')
        self.assertEqual(doc_render.pdf_for(f, self.dir), out)
        self.assertEqual(doc_render.status_for(os.path.join(self.dir, 'none.docx'), self.dir), 'none')
        p = make_file(self.dir, 'x.pdf')
        self.assertEqual(doc_render.status_for(p, self.dir), 'ok')

    def test_status_while_queued_is_not_failed(self):
        f = make_file(self.dir, 'd.docx')
        doc_render.pdf_for(f, self.dir)
        # _FAILED 里不该出现它 —— 否则就是「图永远不出来」的老毛病
        self.assertNotIn(doc_render._norm(f), doc_render._FAILED)


class TestMakeThumbDoesNotPoisonPending(unittest.TestCase):
    """排队中（还没转好）调 make_thumb，不许写 THUMB_FAIL —— 写了前端重试就被冷却挡死。"""

    def test_pending_writes_no_failure(self):
        import brain

        f = os.path.join(tempfile.mkdtemp(prefix='memex387t-'), 'p.docx')
        with open(f, 'wb') as fh:
            fh.write(b'x')

        with mock.patch.object(doc_render, 'pdf_for', return_value=None), \
                mock.patch.object(doc_render, 'status_for', return_value='pending'):
            self.assertIsNone(brain.make_thumb(f, 900, 1200, 0, ''))
        k_prefix = os.path.normcase(f)
        keys = [k for k in brain.THUMB_FAIL if k.startswith(k_prefix)]
        self.assertEqual([], keys, '排队中不能记失败')

    def test_failure_also_leaves_no_brain_cooldown(self):
        """真失败也不能记 brain 那张 300s 的表 —— 冷却归 doc_render（60s，且会换实例重试）。"""
        import brain

        d = tempfile.mkdtemp(prefix='memex387f-')
        f = os.path.join(d, 'p.docx')
        with open(f, 'wb') as fh:
            fh.write(b'x')
        with mock.patch.object(doc_render, 'pdf_for', return_value=None), \
                mock.patch.object(doc_render, 'status_for', return_value='failed'):
            self.assertIsNone(brain.make_thumb(f, 900, 1200, 0, ''))
        k_prefix = os.path.normcase(f)
        keys = [k for k in brain.THUMB_FAIL if k.startswith(k_prefix)]
        self.assertEqual([], keys, 'brain 不许再按 5 分钟按住这份文件')


class TestTransientEngineFailure(unittest.TestCase):
    """复用的引擎实例被外部关掉时会报 RPC 不可用：必须换新实例重试，不能一次就判死。"""

    def test_retries_with_fresh_instance(self):
        calls = []

        class FakeApp:
            def __init__(self, n):
                self.n = n

        def fake_app_for(kind):
            calls.append(kind)
            return FakeApp(len(calls))

        def fake_conv(app, src, out, box):
            if app.n == 1:                     # 第一次：模拟 RPC 挂掉
                raise RuntimeError("(-2147023174, 'RPC 服务器不可用。', None, None)")
            with open(out, 'wb') as fh:
                fh.write(b'%PDF-1.4')

        tmp = tempfile.mkdtemp(prefix='memex387r-')
        out = os.path.join(tmp, 'o.pdf')
        box = {}
        with mock.patch.object(doc_render, '_app_for', fake_app_for), \
                mock.patch.dict(doc_render._CONVERTERS, {'word': fake_conv}), \
                mock.patch.object(doc_render, '_drop_app', lambda kind: None):
            ok = doc_render._convert_one('word', 'x.docx', out, box)
        self.assertTrue(ok, '第二次尝试应该成功')
        self.assertEqual(2, len(calls), '必须换一个干净实例再来一次')


if __name__ == '__main__':
    unittest.main()
