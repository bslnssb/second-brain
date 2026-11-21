# -*- coding: utf-8 -*-
"""第 388 项：Office 首屏先出「头一份」（前 8 页），整份在后台补。

实测 180 页 docx：整份导出 6.8s，只导前 8 页 1.06s。详情栏只看得到第 1 页，
所以没必要让用户等整份；页数用 ComputeStatistics 单独记（0.4s），翻页器照样有 1 / 180。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import doc_render


def _touch(p, text='x'):
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


class TestHeadFirst(unittest.TestCase):
    def setUp(self):
        self._old_auto = doc_render.WORKER_AUTOSTART
        doc_render.WORKER_AUTOSTART = False
        doc_render.clear_failure()
        with doc_render._LOCK:
            doc_render._TASKS.clear()
            doc_render._INFLIGHT.clear()
        self.dir = tempfile.mkdtemp(prefix='memex388-')

    def tearDown(self):
        with doc_render._LOCK:
            doc_render._TASKS.clear()
            doc_render._INFLIGHT.clear()
        doc_render.clear_failure()
        doc_render.WORKER_AUTOSTART = self._old_auto
        shutil.rmtree(self.dir, ignore_errors=True)

    def _doc(self, name='a.docx'):
        return _touch(os.path.join(self.dir, name))

    def _make_head(self, p, pages=180, head=8):
        _full, hp, _mp = doc_render._side_paths(p, self.dir)
        with open(hp, 'wb') as f:
            f.write(b'%PDF-1.4 head')
        doc_render._write_meta(p, self.dir, pages, head)
        return hp

    def test_first_page_uses_head(self):
        p = self._doc()
        hp = self._make_head(p)
        self.assertEqual(doc_render.pdf_for(p, self.dir, pg=0), hp)
        self.assertEqual(doc_render.status_for(p, self.dir), 'ok')

    def test_page_inside_head_still_head(self):
        p = self._doc()
        hp = self._make_head(p)
        self.assertEqual(doc_render.pdf_for(p, self.dir, pg=7), hp)

    def test_page_beyond_head_queues_full(self):
        p = self._doc()
        self._make_head(p)
        self.assertIsNone(doc_render.pdf_for(p, self.dir, pg=9), '第 10 页头一份里没有')
        queued, inflight = doc_render.queue_len()
        self.assertEqual(inflight, 1)
        with doc_render._LOCK:
            keys = [t[0] for t in doc_render._TASKS]
        self.assertIn(doc_render._norm(p) + '|full', keys, '应排一份整份的活')

    def test_total_pages_from_meta(self):
        p = self._doc()
        self._make_head(p, pages=180, head=8)
        self.assertEqual(doc_render.total_pages(p, self.dir), 180)

    def test_short_doc_head_becomes_full(self):
        """页数不超过 8 页：头一份就是整份，直接复制，不再导第二遍。"""
        p = self._doc()
        _full, hp, _mp = doc_render._side_paths(p, self.dir)
        with open(hp, 'wb') as f:
            f.write(b'%PDF-1.4 small')
        doc_render._finish_head(p, self.dir, hp, 'word', 3, 3)
        full = doc_render.cache_path(p, self.dir)
        self.assertTrue(os.path.exists(full), '短文档应直接落成整份')
        self.assertEqual(doc_render._read_meta(p, self.dir)['head'], 0)
        self.assertEqual(doc_render.pdf_for(p, self.dir, pg=99), full)

    def test_long_doc_head_no_longer_queues_full(self):
        """第 390 项改了：长文档只留命中页那一段，不再自动补整份（用户要看全文会自己打开文件）。"""
        p = self._doc()
        _full, hp, _mp = doc_render._side_paths(p, self.dir)
        with open(hp, 'wb') as f:
            f.write(b'%PDF-1.4 long')
        doc_render._finish_head(p, self.dir, hp, 'word', 180, 3, 8, 8, '安全')
        meta = doc_render._read_meta(p, self.dir)
        self.assertEqual((meta['pages'], meta['head']), (180, 3))
        self.assertEqual((meta['from'], meta['hit'], meta['q']), (8, 8, '安全'))
        self.assertEqual(doc_render.queue_len()[1], 0, '不再自动导整份')

    def test_excel_goes_straight_to_full(self):
        """Excel/PPT 不支持分段导出，第一次就排整份，别白跑一趟头一份。"""
        p = self._doc('b.xlsx')
        self.assertIsNone(doc_render.pdf_for(p, self.dir))
        with doc_render._LOCK:
            keys = [t[0] for t in doc_render._TASKS]
        self.assertIn(doc_render._norm(p) + '|full', keys)
        self.assertNotIn(doc_render._norm(p) + '|head', keys)


class TestHeadExport(unittest.TestCase):
    """_convert_word 的分段导出：页数 > HEAD_PAGES 时只导前几页。"""

    def test_word_exports_head_range(self):
        calls = []

        class FakeDoc:
            def ComputeStatistics(self, n):
                return 180

            def ExportAsFixedFormat(self, *a, **kw):
                calls.append(a)

            def Close(self, *a):
                pass

        class FakeApp:
            Visible = True
            DisplayAlerts = 1

            def __init__(self):
                self.Documents = mock.Mock()
                self.Documents.Open = lambda *a, **kw: FakeDoc()

        tmp = tempfile.mkdtemp(prefix='memex388w-')
        out = os.path.join(tmp, 'h.pdf')
        try:
            with open(out, 'wb') as f:
                f.write(b'%PDF-1.4')
            box = {'mode': 'head'}
            doc_render._convert_word(FakeApp(), 'x.docx', out, box)
            self.assertEqual(box['pages'], 180)
            self.assertEqual(box['head_pages'], doc_render.HEAD_PAGES)
            self.assertEqual(calls[0][4], doc_render.WD_EXPORT_FROM_TO)
            self.assertEqual(calls[0][5:7], (1, doc_render.HEAD_PAGES))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_word_small_doc_exports_all(self):
        calls = []

        class FakeDoc:
            def ComputeStatistics(self, n):
                return 3

            def ExportAsFixedFormat(self, *a, **kw):
                calls.append(a)

            def Close(self, *a):
                pass

        class FakeApp:
            Visible = True
            DisplayAlerts = 1

            def __init__(self):
                self.Documents = mock.Mock()
                self.Documents.Open = lambda *a, **kw: FakeDoc()

        tmp = tempfile.mkdtemp(prefix='memex388s-')
        out = os.path.join(tmp, 'h.pdf')
        try:
            with open(out, 'wb') as f:
                f.write(b'%PDF-1.4')
            box = {'mode': 'head'}
            doc_render._convert_word(FakeApp(), 'x.docx', out, box)
            self.assertEqual(len(calls[0]), 2, '短文档整份导，不带 Range')
            self.assertEqual(box['head_pages'], 3, '整份就是 3 页')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
