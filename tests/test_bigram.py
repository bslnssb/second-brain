# -*- coding: utf-8 -*-
"""2 字中文查询 bigram 旁表的单元测试（交接文档第 114 项的回归锁）。

trigram tokenizer 只认 ≥3 字符，「微信」「简历」这类 2 字词永远 MATCH 不中。
旁表 bigram(gram, rid) 按 _cjk_grams 切分、与 docs 行成对维护（_bigram_add /
_doc_del 两个咽喉点），查询在 _search_sql 里按「全 2 字 CJK 且回填完成」路由。

隔离：brain.DB 指到 tempfile 临时库，不碰真实 brain.db。
跑法：python -m unittest discover -s tests -p "test_*.py"
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402


class BigramPureTest(unittest.TestCase):
    """切分与路由判据（纯函数，不起库）。"""

    def test_grams_cjk_pairs(self):
        self.assertEqual(brain._cjk_grams('微信'), ['微信'])
        self.assertEqual(brain._cjk_grams('微信支付'), ['微信', '信支', '支付'])
        self.assertEqual(brain._cjk_grams('安装包，行政人事'), ['安装', '装包', '行政', '政人', '人事'])

    def test_grams_skip_non_cjk(self):
        # ASCII / 标点 / 空白不产生 gram、不参与配对；跨标点的两个汉字不许连成一对
        self.assertEqual(brain._cjk_grams('AI PDF .\n\t'), [])
        self.assertEqual(brain._cjk_grams('AI 帮我做 PDF'), ['帮我', '我做'])
        self.assertEqual(brain._cjk_grams('微信。支付'), ['微信', '支付'])
        self.assertEqual(brain._cjk_grams(''), [])
        self.assertEqual(brain._cjk_grams(None), [])

    def test_grams_dedup_per_doc(self):
        self.assertEqual(brain._cjk_grams('测试测试测试'), ['测试', '试测'])

    def test_is_cjk_word(self):
        self.assertTrue(brain._is_cjk_word('微信'))
        self.assertFalse(brain._is_cjk_word('AI'))
        self.assertFalse(brain._is_cjk_word('中文x'))
        self.assertFalse(brain._is_cjk_word(''))
        self.assertFalse(brain._is_cjk_word('微，信'))


class BigramSearchTest(unittest.TestCase):
    """临时库端到端：写入 / 命中 / pf 过滤 / 删除 / 空表回退 LIKE。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sb-test-bigram-')
        self.dir = Path(self.tmp.name)
        self._old_dir = list(brain._DATA_DIR)
        self._old_db = brain.DB
        os.environ['SB_NO_MAINT'] = '1'
        os.environ['SB_NO_WATCH'] = '1'
        brain._DATA_DIR[:] = [self.dir]
        brain.DB = self.dir / 'brain.db'
        brain._BIGRAM_READY[0], brain._BIGRAM_READY[1] = False, 0.0

    def tearDown(self):
        brain.DB = self._old_db
        brain._DATA_DIR[:] = self._old_dir
        brain._BIGRAM_READY[0], brain._BIGRAM_READY[1] = False, 0.0
        # 不用 tmp.cleanup()：断言失败时连接没来得及关，WinError 32 会把真失败盖掉
        shutil.rmtree(self.tmp.name, ignore_errors=True)

    def _insert_doc(self, con, path, text):
        cur = con.execute('INSERT INTO docs VALUES(?,?,?)',
                          (path, os.path.splitext(path)[1], text))
        brain._bigram_add(con, cur.lastrowid, text)
        return cur.lastrowid

    def test_2char_query_routes_to_bigram_and_hits(self):
        con = brain.init_db()
        self._insert_doc(con, 'D:\\资料\\a.md', '公司规定：微信聊天记录每周备份。')
        self._insert_doc(con, 'D:\\资料\\b.md', '纯英文 body without the keyword.')
        con.execute("INSERT OR REPLACE INTO kv(key, val) VALUES('bigram_done','1')")
        con.commit()
        brain._BIGRAM_READY[1] = 0.0
        sql, args = brain._search_sql('微信', con=con)
        self.assertIn('bigram', sql)                      # 路由到了旁表
        hits = brain.search_rows(con, '微信', limit=10)
        self.assertEqual([p for p, _ in hits], ['D:\\资料\\a.md'])
        # 片段里能定位命中词（substr+instr 模式），不是正文开头
        self.assertIn('微信', str(hits[0][1]))
        con.close()

    def test_pf_filter_still_works(self):
        con = brain.init_db()
        self._insert_doc(con, 'D:\\资料\\a.md', '微信备份说明。')
        self._insert_doc(con, 'D:\\别处\\b.md', '微信备份说明。')
        con.execute("INSERT OR REPLACE INTO kv(key, val) VALUES('bigram_done','1')")
        con.commit()
        brain._BIGRAM_READY[1] = 0.0
        hits = brain.search_rows(con, '微信', limit=10, path_filter='资料')
        self.assertEqual([p for p, _ in hits], ['D:\\资料\\a.md'])
        con.close()

    def test_doc_del_cleans_bigram(self):
        con = brain.init_db()
        rid = self._insert_doc(con, 'D:\\x\\a.md', '微信记录。')
        con.execute("INSERT OR REPLACE INTO kv(key, val) VALUES('bigram_done','1')")
        con.commit()
        rids = brain._doc_rids(con)
        # _doc_rids 契约：键是 docs 里的原始大小写路径（index()/_body_apply 再归一化）
        self.assertEqual(rids, {'D:\\x\\a.md': rid})
        brain._doc_del(con, {os.path.normcase('D:\\x\\a.md'): rid}, 'D:\\x\\a.md')
        con.commit()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM bigram').fetchone()[0], 0)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 0)
        con.close()

    def test_unmarked_table_falls_back_to_like(self):
        """老库回填没跑完（kv 没标记）→ 自动走 LIKE，行为不回退。"""
        con = brain.init_db()
        self._insert_doc(con, 'D:\\x\\a.md', '微信记录。')
        con.commit()
        brain._BIGRAM_READY[1] = 0.0
        sql, _args = brain._search_sql('微信', con=con)
        self.assertNotIn('bigram', sql)
        self.assertIn('LIKE', sql)
        hits = brain.search_rows(con, '微信', limit=10)
        self.assertEqual(len(hits), 1)
        con.close()

    def test_not_ready_even_with_rows(self):
        """旁表有数据但没标记完成 → 仍然 LIKE（回填中途不许路由，见 _bigram_add）。"""
        con = brain.init_db()
        self._insert_doc(con, 'D:\\x\\a.md', '微信记录。')
        con.commit()
        brain._BIGRAM_READY[1] = 0.0
        self.assertFalse(brain._bigram_ready(con))
        sql, _args = brain._search_sql('微信', con=con)
        self.assertNotIn('bigram', sql)
        con.close()


if __name__ == '__main__':
    unittest.main()
