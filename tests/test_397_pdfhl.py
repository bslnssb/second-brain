# -*- coding: utf-8 -*-
"""第 397 项：PDF / Word / PPT 页图上的命中高亮 + 多关键词。

三件事在真机上被验证过（探针 tmp/_probe397.py + tmp/_probe398.py）：
1. 有文本层的 PDF 能用 `search_for` 拿到矩形（高亮画得出来）；
2. 但 `/api/pdfmeta` 从来没有给 PDF 回过命中页 → 详情栏永远停在第 1 页，
   于是「命中词在文档里」却一个高亮都看不到；
3. `make_thumb` 的高亮词直接按空格切，把 `ext:pdf` 这种语法词也丢给 `search_for`。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import brain
import doc_render


class TestTokens(unittest.TestCase):
    def test_syntax_and_exclude_are_dropped(self):
        # 语法项不参与高亮/定位；-排除词也不该去搜
        self.assertEqual(doc_render._q_tokens('深信服 ext:pdf -表格 防火墙'),
                         ['深信服', '防火墙'])

    def test_ext_with_space(self):
        self.assertEqual(doc_render._q_tokens('深信服 ext: docx 防火墙'), ['深信服', '防火墙'])

    def test_up_to_eight_keywords(self):
        q = ' '.join('k%d' % i for i in range(12))
        self.assertEqual(len(doc_render._q_tokens(q)), 8)

    def test_empty(self):
        self.assertEqual(doc_render._q_tokens(''), [])


class _FakePage:
    def __init__(self, text):
        self._t = text

    def get_text(self, _mode='text'):
        return self._t


class _FakeDoc:
    def __init__(self, pages):
        self._pages = [_FakePage(t) for t in pages]

    def __len__(self):
        return len(self._pages)

    def __getitem__(self, i):
        return self._pages[i]

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def close(self):
        pass


class TestPdfHitPage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='memex397-')
        self.pdf = os.path.join(self.dir, 'a.pdf')
        with open(self.pdf, 'wb') as f:
            f.write(b'%PDF-1.4\n%fake\n')
        brain._PAGE_CACHE.clear()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        brain._PAGE_CACHE.clear()

    def _open(self, pages):
        return mock.patch('pdf_engine.open', lambda path, password=None: _FakeDoc(pages))

    def test_hit_on_last_page_beyond_30(self):
        # 31 页、词在第 31 页：以前只扫前 30 页 → 永远定位不到
        pages = ['nothing %d' % i for i in range(30)] + ['trust 深信服 here']
        with self._open(pages):
            self.assertEqual(brain.pdf_hit_page(self.pdf, '深信服'), 31)

    def test_multi_keyword_any_match(self):
        pages = ['aaa', 'bbb 防火墙 ccc', 'ddd']
        with self._open(pages):
            self.assertEqual(brain.pdf_hit_page(self.pdf, '深信服 防火墙'), 2)

    def test_syntax_token_ignored(self):
        pages = ['ext: pdf', '介绍 深信服 产品']
        with self._open(pages):
            # 语法项不能把「ext: pdf」这页当命中
            self.assertEqual(brain.pdf_hit_page(self.pdf, 'ext: pdf 深信服'), 2)

    def test_scanned_returns_zero(self):
        with self._open(['', '', '']):
            self.assertEqual(brain.pdf_hit_page(self.pdf, '深信服'), 0)

    def test_cache(self):
        with self._open(['', 'hit 深信服']) as _p:
            self.assertEqual(brain.pdf_hit_page(self.pdf, '深信服'), 2)
        # 第二次即便 open 会炸，也该走缓存
        boom = mock.patch('pdf_engine.open', side_effect=RuntimeError('boom'))
        with boom:
            self.assertEqual(brain.pdf_hit_page(self.pdf, '深信服'), 2)

    def test_non_pdf_is_zero(self):
        self.assertEqual(brain.pdf_hit_page(os.path.join(self.dir, 'a.docx'), 'x'), 0)


class TestPdfFirstHit(unittest.TestCase):
    """导出后的 PDF 里补找命中页：Word 文本框 / PPT / Excel 全靠这一手。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='memex397b-')
        self.pdf = os.path.join(self.dir, 'head.pdf')
        with open(self.pdf, 'wb') as f:
            f.write(b'%PDF-1.4\n%fake\n')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_finds_page(self):
        pages = ['aaa', 'bbb', '这里提到 深信服']
        with mock.patch('pdf_engine.open', lambda path, password=None: _FakeDoc(pages)):
            self.assertEqual(doc_render._pdf_first_hit(self.pdf, ['深信服']), 3)

    def test_multi_keyword(self):
        pages = ['aaa', '防火墙 配置']
        with mock.patch('pdf_engine.open', lambda path, password=None: _FakeDoc(pages)):
            self.assertEqual(doc_render._pdf_first_hit(self.pdf, ['深信服', '防火墙']), 2)

    def test_no_token_no_scan(self):
        self.assertEqual(doc_render._pdf_first_hit(self.pdf, []), 0)

    def test_missing_pdf(self):
        self.assertEqual(doc_render._pdf_first_hit(os.path.join(self.dir, 'nope.pdf'), ['x']), 0)


if __name__ == '__main__':
    unittest.main()
