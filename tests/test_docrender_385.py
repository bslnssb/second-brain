# -*- coding: utf-8 -*-
"""第 385 项 · doc_render（Office → PDF 页图）的纯逻辑部分。

真转换要拉本机 Office/WPS 的 COM，属于「机器相关」，所以这里只钉**不依赖 COM** 的约定：
缓存键、扩展名归类、失败时的 return None 口径、缓存清理。真转换在装机版上手测（见 HANDOFF）。
"""

import os
import tempfile
import time
import unittest

import doc_render


class TestMapping(unittest.TestCase):
    def test_is_office(self):
        for p in ('a.docx', 'b.DOC', 'c.xlsx', 'd.pptx', 'e.wps', 'f.et', 'g.dps'):
            self.assertTrue(doc_render.is_office(p), p)
        for p in ('a.pdf', 'b.txt', 'c.png', 'noext', ''):
            self.assertFalse(doc_render.is_office(p), p)

    def test_kind_mapping(self):
        self.assertEqual(doc_render.OFFICE_PDF['.docx'], 'word')
        self.assertEqual(doc_render.OFFICE_PDF['.xlsx'], 'excel')
        self.assertEqual(doc_render.OFFICE_PDF['.pptx'], 'ppt')
        self.assertIsNone(doc_render.OFFICE_PDF['.csv'])          # csv 不需要转换


class TestCacheKey(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='memex385-')
        self.addCleanup(self._clean)
        self.base = os.path.join(self.dir, 'data')
        os.makedirs(self.base, exist_ok=True)

    def _clean(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_path_is_stable_and_under_cache_dir(self):
        f = os.path.join(self.dir, 'a.docx')
        with open(f, 'wb') as fh:
            fh.write(b'x')
        a = doc_render.cache_path(f, self.base)
        b = doc_render.cache_path(f, self.base)
        self.assertEqual(a, b)
        self.assertTrue(a.endswith('.pdf'))
        self.assertEqual(os.path.dirname(a), os.path.join(self.base, 'preview_cache'))

    def test_key_changes_when_file_changes(self):
        """文件改过（mtime/size 变）必须换新 PDF —— 否则用户看到的永远是旧版面。"""
        f = os.path.join(self.dir, 'b.docx')
        with open(f, 'wb') as fh:
            fh.write(b'1')
        a = doc_render.cache_path(f, self.base)
        time.sleep(0.01)
        with open(f, 'wb') as fh:
            fh.write(b'22')
        os.utime(f, None)
        b = doc_render.cache_path(f, self.base)
        self.assertNotEqual(a, b)


class TestPdfForGuards(unittest.TestCase):
    def test_missing_file(self):
        self.assertIsNone(doc_render.pdf_for(os.path.join(tempfile.gettempdir(), 'no_such_385.docx'),
                                             tempfile.gettempdir()))

    def test_pdf_passthrough(self):
        """PDF 不需要转换：原样返回（make_thumb 的 PDF 分支直接吃）。"""
        f = os.path.join(tempfile.mkdtemp(), 'a.pdf')
        with open(f, 'wb') as fh:
            fh.write(b'%PDF-1.4')
        self.assertEqual(doc_render.pdf_for(f, os.path.dirname(f)), f)

    def test_unknown_ext(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, 'a.txt')
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write('x')
        self.assertIsNone(doc_render.pdf_for(f, d))

    def test_csv_not_converted(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, 'a.csv')
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write('a,b\n1,2\n')
        self.assertIsNone(doc_render.pdf_for(f, d))


class TestCacheMaintenance(unittest.TestCase):
    def test_stats_and_cleanup(self):
        base = tempfile.mkdtemp(prefix='memex385c-')
        self.addCleanup(lambda: __import__('shutil').rmtree(base, ignore_errors=True))
        d = doc_render.cache_dir(base)
        for i in range(3):
            with open(os.path.join(d, 'x%d.pdf' % i), 'wb') as fh:
                fh.write(b'p')
        st = doc_render.cache_stats(base)
        self.assertEqual(st['files'], 3)
        self.assertEqual(doc_render.cleanup(base), 3)
        self.assertEqual(doc_render.cache_stats(base)['files'], 0)


if __name__ == '__main__':
    unittest.main()
