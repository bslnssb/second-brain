# -*- coding: utf-8 -*-
"""第 405 项：CSV/TSV 详情预览 + pptx 幻灯片文字当主预览。"""

import io
import os
import unittest

import brain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCsvRaw(unittest.TestCase):
    def test_raw_whitelist(self):
        self.assertIn('csv', brain._RAW_OK_EXT)
        self.assertIn('tsv', brain._RAW_OK_EXT)
        self.assertIn('text/csv', brain._RAW_MIME['csv'])

    def test_frontend_wired(self):
        doc = io.open(os.path.join(ROOT, 'web/src/ui/docview.tsx'), encoding='utf-8').read()
        self.assertIn("'csv', 'tsv'", doc)
        self.assertIn('parseDelimited', doc)
        self.assertIn("TextDecoder('gbk')", doc)
        panel = io.open(os.path.join(ROOT, 'web/src/ui/panels.tsx'), encoding='utf-8').read()
        self.assertIn("const SHEET_EXT = ['xls', 'xlsx', 'et', 'csv', 'tsv']", panel)
        # pptx 不再等页图失败才铺文字
        self.assertIn("const INLINE_PRIMARY = [...SHEET_EXT, 'pptx']", panel)

    def test_index_html_not_cached(self):
        """升级后界面必须是新的：旧 index.html 会指着旧版 app.xxxx.js。"""
        src = io.open(os.path.join(ROOT, 'brain.py'), encoding='utf-8').read()
        self.assertIn("if ctype.startswith('text/html'):", src)
        self.assertIn("self.send_header('Cache-Control', 'no-store')", src)

    def test_office_images_also_ocr(self):
        """第 406 项：docx/pptx 里贴的扫描图，页面上也要能标出来 ——
        OCR 要跑在「转换后的 PDF」上，而不是只认 .pdf 路径。"""
        src = io.open(os.path.join(ROOT, 'brain.py'), encoding='utf-8').read()
        self.assertIn("elif doc_render.is_office(p):", src)
        self.assertIn("'ocr': ocr_prog, 'ocr_done': bool(ocr_done)", src)
        panel = io.open(os.path.join(ROOT, 'web/src/ui/panels.tsx'), encoding='utf-8').read()
        self.assertIn('图片型页面：正在识别第', panel)
        self.assertIn('ocrDone', panel)
        # 第 407 项：hv 必须带已识别页数，命中页之后的页也能重渲出框
        self.assertIn("`&hv=${(hitPg ? 'h' + hitPg : 'o')}-${ocrN}`", panel)

    def test_ocr_fields_initialized(self):
        """文本 PDF 根本走不到 OCR 那段，三个 OCR 字段必须在分支外就有初值（真机 500 过）。"""
        src = io.open(os.path.join(ROOT, 'brain.py'), encoding='utf-8').read()
        i_m = src.index("startswith('/api/pdfmeta')")
        i_def = src.index('ocr_done = False', i_m)
        i_loc = src.index('if p and _local_only(p)', i_m)
        # 初值要在「文件存在且已索引」那个分支之前，否则文本 PDF 一进 pdfmeta 就 500
        self.assertLess(i_def, i_loc)
        self.assertIn('ocr_prog = None', src[i_m:i_def])
        self.assertIn('ocr_n = 0', src[i_def:i_loc])

    def test_ocr_json_is_swept(self):
        """OCR 行框缓存也要能自清（2.4 KB/页，不能永久堆积）。"""
        import tempfile, time as _t
        import doc_render
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, 'preview_cache')
            os.makedirs(sub, exist_ok=True)
            old = _t.time() - 30 * 86400
            for name in ('a.pdf', 'a.ocr.json'):
                p = os.path.join(sub, name)
                io.open(p, 'w', encoding='utf-8').write('{}')
                os.utime(p, (old, old))
            doc_render._sweep(d)
            left = [f for f in os.listdir(sub)]
            self.assertEqual(left, [])

    def test_ocr_data_changes_thumb_key(self):
        """OCR 每识别完一页，页图缓存就该换 key —— 否则「没框」的图会一直顶着。"""
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'a.docx')
            io.open(src, 'wb').write(b'x')
            fp = os.path.join(d, 'x.ocr.json')
            with mock.patch.object(brain, '_ocr_side', lambda p: ('k', fp)):
                a = brain._thumb_disk_path(src, 900, 1200, 0, ['陈雅彬'], 'h1')
                io.open(fp, 'w', encoding='utf-8').write('{}')
                b = brain._thumb_disk_path(src, 900, 1200, 0, ['陈雅彬'], 'h1')
            self.assertNotEqual(a, b)


if __name__ == '__main__':
    unittest.main()
