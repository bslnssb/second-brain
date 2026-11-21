"""第 332 项：正文按块分页（详情灯箱里 ← → 先翻页、翻到头才换文件）。"""
import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brain  # noqa: E402


def make_con(body):
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE docs (path TEXT, body TEXT)')
    con.execute('INSERT INTO docs VALUES (?, ?)', (r'C:\x\a.docx', body))
    return con


class PeekPagingTest(unittest.TestCase):
    def setUp(self):
        brain._PEEK_CACHE.clear()

    def test_chunk_pages(self):
        body = '字' * 3000
        con = make_con(body)
        r1 = brain.peek_body(con, r'C:\x\a.docx', page=1, chunk=900)
        self.assertTrue(r1['ok'])
        self.assertEqual(1, r1['page'])
        self.assertEqual(4, r1['pages'])
        self.assertEqual(900, len(r1['text']))
        r4 = brain.peek_body(con, r'C:\x\a.docx', page=4, chunk=900)
        self.assertEqual(4, r4['page'])
        self.assertEqual(300, len(r4['text']))

    def test_page_clamped(self):
        con = make_con('字' * 1000)
        r = brain.peek_body(con, r'C:\x\a.docx', page=99, chunk=500)
        self.assertEqual(2, r['pages'])
        self.assertEqual(2, r['page'])

    def test_short_body_single_page(self):
        con = make_con('很短的一段正文')
        r = brain.peek_body(con, r'C:\x\a.docx', page=1, chunk=900)
        self.assertEqual(1, r['pages'])
        self.assertEqual('很短的一段正文', r['text'])

    def test_mark_hit_inside_chunk(self):
        # 第 2 块 = 字符 900~1799，命中词故意放在 1400 附近
        body = ('前缀' * 700) + '关键词' + ('后缀' * 400)
        con = make_con(body)
        r = brain.peek_body(con, r'C:\x\a.docx', q='关键词', page=2, chunk=900)
        self.assertTrue(r['matched'])
        self.assertIn('关键词', r['text'])

    def test_no_body(self):
        con = make_con('')
        r = brain.peek_body(con, r'C:\x\a.docx', page=1)
        self.assertFalse(r['ok'])
        self.assertEqual('no_body', r['why'])

    def test_default_path_unchanged(self):
        """不传 page 时还是老行为：给一段 360 字，但附带 pages 让前端知道能不能翻。"""
        con = make_con('字' * 5000)
        r = brain.peek_body(con, r'C:\x\a.docx')
        self.assertEqual(360, len(r['text']))
        self.assertGreaterEqual(r['pages'], 5)


if __name__ == '__main__':
    unittest.main()
