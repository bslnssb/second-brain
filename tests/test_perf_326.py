# -*- coding: utf-8 -*-
"""第 326 项契约：正文截断上限、数据文件跳过、facets 单次扫描 + 缓存、瘦身线程、缓存上限。"""
import io
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brain  # noqa: E402


def src():
    return io.open(os.path.join(ROOT, 'brain.py'), encoding='utf-8').read()


class BodyCapTest(unittest.TestCase):
    def test_max_body_is_512k(self):
        self.assertEqual(512 * 1024, brain.MAX_BODY)

    def test_txt_is_truncated(self):
        """txt 以前读 3×MAX_BODY 却原样返回 —— 一篇 6MB 密码字典就这么进了库。"""
        self.assertIn("return _decode_text(raw)[:MAX_BODY]", src())

    def test_text_dump_threshold(self):
        self.assertEqual(8 * 1024 * 1024, brain.TEXT_DUMP_MAX)
        self.assertIn('.txt', brain.TEXT_DUMP_EXTS)
        self.assertIn('.csv', brain.TEXT_DUMP_EXTS)

    def test_big_dump_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'dict.txt')
            with io.open(p, 'wb') as f:
                f.write(b'a' * (brain.TEXT_DUMP_MAX + 1024))
            self.assertIsNone(brain.extract_text(brain.Path(p)))

    def test_small_txt_still_extracted(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'note.txt')
            with io.open(p, 'w', encoding='utf-8', newline='') as f:
                f.write('会议纪要 2026 预算')
            out = brain.extract_text(brain.Path(p))
            self.assertTrue(out and '会议纪要' in out)


class FacetsTest(unittest.TestCase):
    def _con(self):
        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE files(path TEXT, name TEXT, dir TEXT, ext TEXT, size REAL, mtime REAL)')
        for name, ext, d in (('报告.pdf', '.pdf', 'D:/a'), ('简历.docx', '.docx', 'D:/a'),
                             ('图.png', '.png', 'D:/b')):
            con.execute('INSERT INTO files VALUES(?,?,?,?,?,?)', (d + '/' + name, name.lower(), d, ext, 1, 1))
        return con

    def test_single_scan_source(self):
        """两条 GROUP BY 合成一条：源码里不该再出现单独 GROUP BY 1 的扩展名查询。"""
        s = src()
        self.assertIn("SELECT lower(ext), dir, COUNT(*) FROM files", s)
        self.assertNotIn("SELECT lower(ext), COUNT(*) FROM files", s)

    def test_counts_and_cache(self):
        con = self._con()
        brain._FACETS_CACHE.clear()
        a = brain.facets(con, {})
        self.assertEqual(3, a['total'])
        self.assertEqual([], a['kinds'][2:])        # 只有 doc / img 两类
        total = sum(k['n'] for k in a['kinds'])
        self.assertEqual(3, total)
        self.assertEqual(2, a['folders'][0]['n'])   # a 目录两篇
        # 第二次必须是缓存命中（同一个对象，不再查库）
        con.close()
        b = brain.facets(con, {})
        self.assertIs(a, b)

    def test_keyword_narrows(self):
        con = self._con()
        brain._FACETS_CACHE.clear()
        out = brain.facets(con, {'q': '报告'})
        self.assertEqual(1, out['total'])
        self.assertEqual('doc', out['kinds'][0]['k'])


class SlimTest(unittest.TestCase):
    def test_thread_and_marker(self):
        s = src()
        self.assertIn('def _slim_docs_once', s)
        self.assertIn("target=_slim_docs_once", s)
        self.assertIn("'slim_docs_v1'", s)
        self.assertIn('substr(body,1,?)', s)

    def test_warm_loop(self):
        s = src()
        self.assertIn('def _facets_warm_loop', s)
        self.assertIn("target=_facets_warm_loop", s)

    def test_peek_cache_capped(self):
        s = src()
        self.assertIn('if len(_PEEK_CACHE) > 200', s)

    def test_synchronous_normal(self):
        self.assertIn("PRAGMA synchronous=NORMAL", src())

    def test_memory_caps(self):
        self.assertEqual(120, brain.MINI_CACHE_MAX)
        s = src()
        self.assertIn('def _trim_working_set', s)
        self.assertIn('SetProcessWorkingSetSize', s)
        self.assertIn('len(_TGT_CACHE) > 8000', s)


class ThumbDiskTest(unittest.TestCase):
    def test_cache_key_carries_page(self):
        p = os.path.join(ROOT, 'brain.py')
        a = brain._thumb_disk_path(p, 900, 1200, 0, ())
        b = brain._thumb_disk_path(p, 900, 1200, 2, ())
        c = brain._thumb_disk_path(p, 900, 1200, 0, ('合同',))
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)

    def test_all_variants_use_disk(self):
        s = src()
        self.assertIn('use_disk = True', s)
        self.assertNotIn('use_disk = (not page) and not _hl', s)
        self.assertIn('pix = None', s)


if __name__ == '__main__':
    unittest.main()
