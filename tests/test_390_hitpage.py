# -*- coding: utf-8 -*-
"""第 390 项：Office 详情只准备「关键词命中的那一页」，不要再从第 1 页翻起。

产品判断来自用户：180 页的 docx 他只是被搜索结果带到那一页，要看全文会自己打开文件。
所以：Word 里用 Find 定出命中页（实测 0.01s/词）→ 只导命中页 ±1 → 详情栏直接停在那儿。
"""

import os
import shutil
import tempfile
import unittest

import doc_render


def _touch(p, text='x'):
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


class _Find:
    def __init__(self, hit_word, page):
        self.Text = ''
        self._hit = hit_word
        self._page = page

    def ClearFormatting(self):
        pass

    def Execute(self):
        return self.Text.lower() == self._hit.lower()


class _Rng:
    def __init__(self, hit_word, page):
        self.Find = _Find(hit_word, page)
        self._page = page

    def Information(self, kind):
        return self._page if kind == 3 else 0


class _ContentDoc:
    """只实现 _find_page 用到的三个动作（Content.Find / Execute / Information）。"""

    def __init__(self, hit_word='安全', page=8):
        self.Content = _Rng(hit_word, page)

    def ComputeStatistics(self, n):
        return 180

    def ExportAsFixedFormat(self, *a):
        self.exported = a

    def Close(self, *a):
        pass


class _BadFind:
    Text = ''

    def ClearFormatting(self):
        pass

    def Execute(self):
        return False


class _BadContent:
    Find = _BadFind()


class _BadDoc:
    """文本框/形状里的字：Word Find 搜不到 → Execute 返回 False。"""

    def __init__(self):
        self.Content = _BadContent()
        self.exported = None

    def ComputeStatistics(self, n):
        return 180

    def ExportAsFixedFormat(self, *a):
        self.exported = a

    def Close(self, *a):
        pass


class _FakeApp:
    Visible = True
    DisplayAlerts = 1

    def __init__(self, doc):
        class _Docs:
            @staticmethod
            def Open(*a, **kw):
                return doc

        self.Documents = _Docs()


class Base(unittest.TestCase):
    def setUp(self):
        self._old_auto = doc_render.WORKER_AUTOSTART
        doc_render.WORKER_AUTOSTART = False
        doc_render.clear_failure()
        with doc_render._LOCK:
            doc_render._TASKS.clear()
            doc_render._INFLIGHT.clear()
        self.dir = tempfile.mkdtemp(prefix='memex390-')
        self.out = os.path.join(self.dir, 'h.pdf')
        with open(self.out, 'wb') as f:
            f.write(b'%PDF-1.4')

    def tearDown(self):
        with doc_render._LOCK:
            doc_render._TASKS.clear()
            doc_render._INFLIGHT.clear()
        doc_render.clear_failure()
        doc_render.WORKER_AUTOSTART = self._old_auto
        shutil.rmtree(self.dir, ignore_errors=True)

    def _meta(self, pages=180, head=3, frm=7, hit=8, q='安全'):
        p = _touch(os.path.join(self.dir, 'a.docx'))
        _full, hp, _mp = doc_render._side_paths(p, self.dir)
        with open(hp, 'wb') as f:
            f.write(b'%PDF-1.4 seg')
        doc_render._write_meta(p, self.dir, pages, head, frm, hit, q)
        return p, hp


class TestTokens(unittest.TestCase):
    def test_strips_syntax_and_prefix(self):
        self.assertEqual(doc_render._q_tokens('ext: docx 安全 -排除 等保 方案 多余'),
                         ['安全', '等保', '方案', '多余'])

    def test_empty(self):
        self.assertEqual(doc_render._q_tokens(''), [])
        self.assertEqual(doc_render._q_tokens(None), [])

    def test_dedupe(self):
        self.assertEqual(doc_render._q_tokens('方案 方案 方案'), ['方案'])


class TestFindPage(unittest.TestCase):
    def test_finds_page(self):
        doc = _ContentDoc('安全', 8)
        self.assertEqual(doc_render._find_page(doc, '方案 安全'), 8, '第一个搜不到就试下一个')

    def test_not_found(self):
        self.assertEqual(doc_render._find_page(_BadDoc(), '安全'), 0)

    def test_broken_doc_does_not_raise(self):
        class _Boom:
            @property
            def Content(self):
                raise RuntimeError('RPC 服务器不可用')

        self.assertEqual(doc_render._find_page(_Boom(), '安全'), 0)


class TestConvertWindow(Base):
    def test_hit_exports_hit_window(self):
        doc = _ContentDoc('安全', 8)
        box = {'mode': 'head', 'q': '安全'}
        doc_render._convert_word(_FakeApp(doc), 'x.docx', self.out, box)
        self.assertEqual(box['hit'], 8)
        self.assertEqual(box['from'], 7)
        self.assertEqual(box['head_pages'], 3, '命中页 ±1 = 3 页')
        self.assertEqual(doc.exported[5:7], (7, 9))

    def test_hit_on_last_page_clamped(self):
        doc = _ContentDoc('安全', 180)
        box = {'mode': 'head', 'q': '安全'}
        doc_render._convert_word(_FakeApp(doc), 'x.docx', self.out, box)
        self.assertEqual(doc.exported[5:7], (179, 180), '不能超过总页数')

    def test_no_hit_falls_back_to_first_pages(self):
        doc = _BadDoc()
        box = {'mode': 'head', 'q': '安全'}
        doc_render._convert_word(_FakeApp(doc), 'x.docx', self.out, box)
        self.assertEqual((box['hit'], box['from']), (0, 1))
        self.assertEqual(box['head_pages'], doc_render.HEAD_PAGES)
        self.assertEqual(doc.exported[5:7], (1, doc_render.HEAD_PAGES))

    def test_full_mode_exports_all(self):
        doc = _ContentDoc('安全', 8)
        box = {'mode': 'full', 'q': '安全'}
        doc_render._convert_word(_FakeApp(doc), 'x.docx', self.out, box)
        self.assertEqual(len(doc.exported), 2, '整份导出不带 Range')
        self.assertEqual(box['head_pages'], 0)


class TestPageSource(Base):
    def test_maps_page_into_segment(self):
        p, hp = self._meta()
        for pg, idx in ((6, 0), (7, 1), (8, 2)):
            self.assertEqual(doc_render.pdf_page_source(p, self.dir, pg, '安全'), (hp, idx))

    def test_page_zero_out_of_segment_does_not_burn_full(self):
        """前端还不知道命中页时先按第 1 页要图 —— 这一下**不许**触发整份导出。"""
        p, _hp = self._meta()
        self.assertEqual(doc_render.pdf_page_source(p, self.dir, 0, '安全'), (None, None))
        with doc_render._LOCK:
            keys = [t[0] for t in doc_render._TASKS]
        self.assertNotIn(doc_render._norm(p) + '|full', keys)

    def test_user_pages_out_of_segment_queues_full(self):
        p, _hp = self._meta()
        self.assertEqual(doc_render.pdf_page_source(p, self.dir, 5, '安全'), (None, None))
        with doc_render._LOCK:
            keys = [t[0] for t in doc_render._TASKS]
        self.assertIn(doc_render._norm(p) + '|full', keys, '真翻到段外才补整份')

    def test_new_query_re_segments(self):
        p, _hp = self._meta(q='安全')
        doc_render.pdf_page_source(p, self.dir, 0, '等保')
        with doc_render._LOCK:
            keys = [t[0] for t in doc_render._TASKS]
        self.assertIn(doc_render._norm(p) + '|head', keys, '换了关键词要按新词重定位')

        # 队列里还没开工的，q 要能换新（前端先拿旧 q 排上、pdfmeta 后到，很常见）
        doc_render._enqueue(doc_render._norm(p) + '|head', p, _hp, 'word', self.dir, 'head', '真的词')
        with doc_render._LOCK:
            qs = [t[6] for t in doc_render._TASKS if t[0] == doc_render._norm(p) + '|head']
        self.assertEqual(qs, ['真的词'])

    def test_full_pdf_wins(self):
        p, hp = self._meta()
        full = doc_render.cache_path(p, self.dir)
        with open(full, 'wb') as f:
            f.write(b'%PDF-1.4 full')
        self.assertEqual(doc_render.pdf_page_source(p, self.dir, 50, '安全'), (full, 50))

    def test_full_pdf_without_hit_still_locates(self):
        """整份在手但 meta 没记这个词的命中页（老缓存）：照样要跑一趟定位，否则停在第 1 页。"""
        p = _touch(os.path.join(self.dir, 'f.docx'))
        full = doc_render.cache_path(p, self.dir)
        with open(full, 'wb') as f:
            f.write(b'%PDF-1.4 full')
        self.assertEqual(doc_render.pdf_page_source(p, self.dir, 0, '等保'), (full, 0))
        with doc_render._LOCK:
            keys = [t[0] for t in doc_render._TASKS]
        self.assertIn(doc_render._norm(p) + '|head', keys)

    def test_no_query_does_not_burn_conversion(self):
        p = _touch(os.path.join(self.dir, 'g.docx'))
        full = doc_render.cache_path(p, self.dir)
        with open(full, 'wb') as f:
            f.write(b'%PDF-1.4 full')
        self.assertEqual(doc_render.pdf_page_source(p, self.dir, 0, ''), (full, 0))
        self.assertEqual(doc_render.queue_len()[1], 0, '没关键词就别转，省得白烧')

    def test_stale_segment_still_serves_then_re_segments(self):
        """旧缓存是按别的词定位的：要的页碰巧还在老段里也要重导，不能一直错下去。"""
        p, hp = self._meta(head=8, frm=1, hit=0, q='')
        got = doc_render.pdf_page_source(p, self.dir, 3, '等保')
        self.assertEqual(got, (hp, 3), '重导期间先照旧给图，别白屏')
        with doc_render._LOCK:
            keys = [t[0] for t in doc_render._TASKS]
        self.assertIn(doc_render._norm(p) + '|head', keys)


class TestHitInfo(Base):
    def test_hit_matches_query(self):
        p, _hp = self._meta(hit=8, q='安全')
        self.assertEqual(doc_render.hit_info(p, self.dir, '安全'), (8, True))
        self.assertEqual(doc_render.hit_page(p, self.dir, '安全'), 8)

    def test_hit_for_other_query_is_not_trusted(self):
        p, _hp = self._meta(hit=8, q='安全')
        self.assertEqual(doc_render.hit_info(p, self.dir, '等保'), (0, False),
                         '换了关键词不能拿旧 hit 乱跳页')

    def test_no_meta(self):
        p = _touch(os.path.join(self.dir, 'b.docx'))
        self.assertEqual(doc_render.hit_info(p, self.dir, '安全'), (0, False))


if __name__ == '__main__':
    unittest.main()
