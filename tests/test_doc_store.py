# -*- coding: utf-8 -*-
"""v2 存储层（contentless FTS + doc_rec zstd）的契约测试。

体积评估 #2/#3：docs 不再存正文和路径，正文只在 doc_rec 里压缩存一份。
风险点全在这儿：rowid 与 doc_rec.id 必须一一对齐、删改要成对、片段要能高亮。
"""
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import brain


class DocStoreV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sb-test-docstore-')
        self.dir = pathlib.Path(self.tmp)
        self._old_db, self._old_dir = brain.DB, list(brain._DATA_DIR)
        os.environ['SB_NO_MAINT'] = '1'
        os.environ['SB_NO_WATCH'] = '1'
        brain._DATA_DIR[:] = [self.dir]
        brain.DB = self.dir / 'brain.db'
        brain._BIGRAM_READY[0], brain._BIGRAM_READY[1] = False, 0.0

    def tearDown(self):
        brain.DB = self._old_db
        brain._DATA_DIR[:] = self._old_dir
        brain._BIGRAM_READY[0], brain._BIGRAM_READY[1] = False, 0.0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add(self, con, path, text):
        rid = brain.doc_insert(con, path, os.path.splitext(path)[1], text)
        brain._bigram_add(con, rid, text)
        return rid

    def test_schema_is_v2(self):
        con = brain.init_db()
        self.assertEqual(brain.doc_mode(con), 'v2')
        self.assertTrue(con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='doc_rec'").fetchone())
        con.close()

    def test_body_survives_roundtrip(self):
        con = brain.init_db()
        text = '会议纪要：下周三上线，负责人老张。' * 200
        rid = self._add(con, 'D:\\笔记\\会.md', text)
        con.commit()
        self.assertEqual(brain.doc_body_get(con, rid), text)
        self.assertEqual(brain.doc_body_get(con, None, 'D:\\笔记\\会.md'), text)
        # 正文是压缩存的，不是明文
        raw = con.execute('SELECT z FROM doc_rec WHERE id=?', (rid,)).fetchone()[0]
        self.assertLess(len(raw), len(text.encode()) / 2)
        con.close()

    def test_latin_query_still_hits(self):
        con = brain.init_db()
        self._add(con, 'D:\\code\\a.md', 'we use Python and sqlite for indexing')
        con.commit()
        hits = brain.search_rows(con, 'Python', limit=5)
        self.assertEqual([p for p, _ in hits], ['D:\\code\\a.md'])
        self.assertIn(brain.SNIP_OPEN, str(hits[0][1]))
        con.close()

    def test_cjk_long_query_hits_and_marks(self):
        con = brain.init_db()
        self._add(con, 'D:\\资料\\b.md', '合同条款：乙方须在三十日内交付。')
        con.commit()
        hits = brain.search_rows(con, '三十日内', limit=5)
        self.assertEqual(len(hits), 1)
        self.assertIn('三十日内', str(hits[0][1]))
        con.close()

    def test_short_query_scans_and_filters_ext(self):
        con = brain.init_db()
        self._add(con, 'D:\\x\\a.md', 'AI 助手说明')
        self._add(con, 'D:\\x\\b.txt', 'AI 助手说明')
        con.commit()
        hits = brain.search_rows(con, 'AI', limit=5, ext='md')
        self.assertEqual([p for p, _ in hits], ['D:\\x\\a.md'])
        con.close()

    def test_delete_cleans_both_tables(self):
        con = brain.init_db()
        rid = self._add(con, 'D:\\x\\c.md', '待删除的正文 微信记录')
        con.commit()
        brain._doc_del(con, {os.path.normcase('D:\\x\\c.md'): rid}, 'D:\\x\\c.md')
        con.commit()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM doc_rec').fetchone()[0], 0)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 0)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM bigram').fetchone()[0], 0)
        self.assertEqual(brain.search_rows(con, '待删除', limit=5), [])
        con.close()

    def test_replace_body_keeps_same_rid(self):
        con = brain.init_db()
        rid = self._add(con, 'D:\\x\\d.md', 'A' * 300)
        con.commit()
        brain.doc_replace_body(con, rid, 'A' * 100)
        con.commit()
        self.assertEqual(brain.doc_body_get(con, rid), 'A' * 100)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 1)
        con.close()

    def test_doc_rids_and_all_rows(self):
        con = brain.init_db()
        r1 = self._add(con, 'D:\\x\\e.md', '甲')
        r2 = self._add(con, 'D:\\y\\f.md', '乙')
        con.commit()
        self.assertEqual(brain._doc_rids(con), {'D:\\x\\e.md': r1, 'D:\\y\\f.md': r2})
        self.assertEqual([r for r, _ in brain.doc_all_rows(con)], [r1, r2])
        con.close()


class LegacyMigrationTest(unittest.TestCase):
    """老结构（docs_content 存明文）→ 压缩结构的就地迁移。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sb-test-migrate-')
        self.dir = pathlib.Path(self.tmp)
        self._old_db, self._old_dir = brain.DB, list(brain._DATA_DIR)
        os.environ['SB_NO_MAINT'] = '1'
        os.environ['SB_NO_WATCH'] = '1'
        brain._DATA_DIR[:] = [self.dir]
        brain.DB = self.dir / 'brain.db'
        brain._BIGRAM_READY[0], brain._BIGRAM_READY[1] = False, 0.0
        con = brain.init_db()
        con.execute("DROP TABLE IF EXISTS doc_rec")     # 造一份老结构
        con.execute("DROP TABLE IF EXISTS docs")
        con.execute("""CREATE VIRTUAL TABLE docs USING fts5(
                       path UNINDEXED, ext UNINDEXED, body, tokenize='trigram')""")
        con.commit()
        self.con = con

    def tearDown(self):
        self.con.close()
        brain.DB = self._old_db
        brain._DATA_DIR[:] = self._old_dir
        brain._BIGRAM_READY[0], brain._BIGRAM_READY[1] = False, 0.0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migrates_and_keeps_search(self):
        con = self.con
        for p, t in (('D:\\a\\一.md', 'Python 项目说明：微信聊天记录备份。'),
                     ('D:\\a\\二.md', '合同与报价单模板。')):
            con.execute('INSERT INTO docs VALUES(?,?,?)', (p, '.md', t))
        con.commit()
        self.assertEqual(brain.doc_mode(con), 'v1')

        self.assertTrue(brain.compact_legacy_db(con))
        self.assertEqual(brain.doc_mode(con), 'v2')
        self.assertFalse(con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='docs_content'").fetchone())
        self.assertEqual(con.execute('SELECT COUNT(*) FROM doc_rec').fetchone()[0], 2)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 2)
        # 老库的备份留在数据目录里
        self.assertTrue(list(self.dir.glob('brain.db.pre-compact-*')))
        # 迁移后的 docs 必须是 contentless + detail=full：detail=column 那条已作废
        # （trigram 下 detail!=full 不支持短语，MATCH 'Python' 直接报错），
        # 也不许退化成「两份正文」的半吊子结构。
        ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='docs'").fetchone()[0]
        ddl = ' '.join(ddl.split()).replace('"', "'")
        self.assertIn('detail=full', ddl)
        self.assertIn("content=''", ddl)
        self.assertIn('contentless_delete=1', ddl)
        self.assertNotIn('detail=column', ddl)
        # 正文只存一份：contentless 的 docs 读不回任何正文（内容在 doc_rec 的 zstd blob 里）
        self.assertEqual(con.execute(
            "SELECT COUNT(*) FROM docs WHERE body IS NOT NULL").fetchone()[0], 0)

        self.assertEqual([p for p, _ in brain.search_rows(con, 'Python', limit=5)],
                         ['D:\\a\\一.md'])
        body = brain.doc_body_get(con, None, 'D:\\a\\二.md')
        self.assertIn('合同', body)

    def test_noop_on_v2(self):
        con = self.con
        con.execute('INSERT INTO docs VALUES(?,?,?)', ('D:\\a\\三.md', '.md', '正文'))
        con.commit()
        brain.compact_legacy_db(con)
        self.assertFalse(brain.compact_legacy_db(con))   # 第二次是空操作


if __name__ == '__main__':
    unittest.main()
