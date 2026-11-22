# -*- coding: utf-8 -*-
"""第 399/400 项：扫描件按页 OCR 定位 + 页图高亮按行分组。

背景：Word 导出的 PDF 里「一行就是一个 run」，PDFium 给的 (起始字, 个数) 有时跨行，
以前把这段字框并成一个矩形 → 页面上出现一块盖两三行的黄底，看着就是「错误命中」。
"""

import unittest
from unittest import mock

import brain
import pdf_engine


class _Searcher:
    def __init__(self, hits):
        self._hits = list(hits)

    def get_next(self):
        return self._hits.pop(0) if self._hits else None

    def close(self):
        pass


class _TextPage:
    def __init__(self, boxes, hits):
        self._boxes = boxes          # [(l, b, r, t)] PDFium 左下原点
        self._hits = hits

    def search(self, _needle):
        return _Searcher(self._hits)

    def get_charbox(self, i):
        return self._boxes[i]

    def get_text_range(self):
        return ''


class _PdfPage:
    def __init__(self, boxes, hits, h=800):
        self._tp = _TextPage(boxes, hits)
        self._h = h

    def get_width(self):
        return 600

    def get_height(self):
        return self._h

    def get_textpage(self):
        return self._tp


class TestSearchRectGrouping(unittest.TestCase):
    def test_cross_line_match_splits_into_two_rects(self):
        # 4 个字：前两个在第 1 行（top=700），后两个在第 2 行（top=680）
        boxes = [(100, 700, 120, 720), (120, 700, 140, 720),
                 (100, 680, 120, 700), (120, 680, 140, 700)]
        page = pdf_engine.Page(_PdfPage(boxes, [(0, 4)]), None)
        rects = page.search_for('网络安全')
        self.assertEqual(len(rects), 2, rects)       # 一行一个，不再并成一大块
        for r in rects:
            self.assertLess(r.height, 30)             # 每个只有一行高（20pt）
        self.assertEqual(int(rects[0].y0), 80)        # 800 - 720
        self.assertEqual(int(rects[1].y0), 100)       # 800 - 700

    def test_same_line_stays_one_rect(self):
        boxes = [(100, 700, 120, 720), (120, 700, 140, 720)]
        page = pdf_engine.Page(_PdfPage(boxes, [(0, 2)]), None)
        rects = page.search_for('网络')
        self.assertEqual(len(rects), 1)
        self.assertEqual(int(rects[0].width), 40)

    def test_empty_needle(self):
        page = pdf_engine.Page(_PdfPage([(0, 0, 1, 1)], []), None)
        self.assertEqual(page.search_for(''), [])


class TestOcrHelpers(unittest.TestCase):
    def _map(self):
        return {'total': 3, 'done': False, 'pages': {
            '1': {'lines': [[0.1, 0.1, 0.5, 0.15, '封面页 目录']]},
            '2': {'lines': [[0.1, 0.2, 0.9, 0.25, '这里提到 深信服 安全'],
                            [0.1, 0.3, 0.9, 0.35, '无关的一行']]},
        }}

    def test_map_search_by_token(self):
        self.assertEqual(brain._ocr_map_search(self._map(), ['深信服']), 2)

    def test_map_search_by_context_wins(self):
        m = self._map()
        m['pages']['3'] = {'lines': [[0.1, 0.1, 0.9, 0.15, '天融信 深信服 型号 DAS']]}
        self.assertEqual(brain._ocr_map_search(m, ['深信服'], '天融信深信服型号DAS'), 3)

    def test_map_search_none(self):
        self.assertEqual(brain._ocr_map_search(self._map(), ['不存在的词']), 0)
        self.assertEqual(brain._ocr_map_search({'pages': {}}, ['x']), 0)

    def test_line_rects_are_sub_boxes(self):
        with mock.patch.object(brain, '_ocr_side', lambda p: ('k', None)):
            brain._OCR_MAP['k'] = self._map()
            try:
                rects = brain._ocr_line_rects('x.pdf', 2, ['深信服'])
            finally:
                brain._OCR_MAP.pop('k', None)
        self.assertEqual(len(rects), 1)
        a, b, c, d = rects[0]
        self.assertGreaterEqual(a, 0.1)
        self.assertLess(c, 0.9)          # 只框命中词那一截，不是整行
        self.assertAlmostEqual(d - b, 0.05, places=3)

    def test_line_rects_missing_page(self):
        with mock.patch.object(brain, '_ocr_side', lambda p: ('k2', None)):
            brain._OCR_MAP['k2'] = self._map()
            try:
                self.assertEqual(brain._ocr_line_rects('x.pdf', 9, ['深信服']), [])
            finally:
                brain._OCR_MAP.pop('k2', None)

    def test_ocr_hit_page_reports_busy(self):
        """刚起跑（总页数都还不知道）也要说「还在跑」，否则前端会当成「没辙」停止轮询。"""
        m = {'total': 0, 'done': False, 'pages': {}}
        with mock.patch.object(brain, '_ocr_side', lambda p: ('k3', None)), \
                mock.patch.object(brain, '_ocr_load', lambda p: m), \
                mock.patch.object(brain.threading, 'Thread', lambda *a, **k: mock.Mock()):
            hit, done, total, busy, _full = brain.ocr_hit_page('x.pdf', ['深信服'])
            self.assertEqual((hit, done), (0, 0))
            self.assertTrue(busy)
            brain._OCR_JOBS.clear()

    def test_ocr_hit_page_returns_hit(self):
        m = {'total': 3, 'done': False,
             'pages': {'2': {'lines': [[0.1, 0.2, 0.9, 0.25, '这里提到 深信服']]}}}
        with mock.patch.object(brain, '_ocr_side', lambda p: ('k4', None)), \
                mock.patch.object(brain, '_ocr_load', lambda p: m):
            self.assertEqual(brain.ocr_hit_page('x.pdf', ['深信服']), (2, 1, 3, False, False))


class TestThumbVersion(unittest.TestCase):
    """第 403 项：扫描件第一张图常常在 OCR 跑完之前就发出去（没框），浏览器
    还会按 max-age=86400 记住它 —— 之后 OCR 认出命中页也不会更新。把「高亮数据
    版本」并进缓存键，有进展就是一个新 key，自然会重渲一张带框的。"""

    def test_hv_changes_disk_key(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
            f.write(b'%PDF-1.4\n')
            f.flush()
            a = brain._thumb_disk_path(f.name, 900, 1200, 0, ['陈雅彬'], '')
            b = brain._thumb_disk_path(f.name, 900, 1200, 0, ['陈雅彬'], 'h1')
            c = brain._thumb_disk_path(f.name, 900, 1200, 0, ['陈雅彬'], 'o1')
            self.assertNotEqual(a, b)
            self.assertNotEqual(b, c)


if __name__ == '__main__':
    unittest.main()
