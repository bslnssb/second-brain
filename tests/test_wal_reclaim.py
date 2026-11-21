# -*- coding: utf-8 -*-
"""体积评估 #1：WAL 回收。

实测 brain.db-wal 涨到 60.9 MB，文档却承诺「超过 8 MB 自动收」。两个原因：
1. wal_autocheckpoint 只合并帧，不缩小文件 —— 需要 journal_size_limit；
2. 长会话里常常等不到一次干净的 checkpoint 机会 —— 需要定时 TRUNCATE。
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain


class WalTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.old_db = brain.DB
        brain.DB = str(Path(self.td.name) / 'wal.db')
        self.con = brain.init_db()

    def tearDown(self):
        try:
            self.con.close()
        except Exception:
            pass
        brain.DB = self.old_db
        self.td.cleanup()

    def test_journal_size_limit_is_set(self):
        limit = self.con.execute('PRAGMA journal_size_limit').fetchone()[0]
        self.assertEqual(limit, 8 * 1024 * 1024)

    def test_autocheckpoint_matches_limit(self):
        """2000 页 × 4 KB ≈ 8 MB，和 journal_size_limit 对齐，别一个收一个不收。"""
        pages = self.con.execute('PRAGMA wal_autocheckpoint').fetchone()[0]
        self.assertEqual(pages, 2000)
        self.assertLessEqual(pages * 4096, 8 * 1024 * 1024 + 4096)

    def test_big_burst_is_capped_after_tidy(self):
        """autocheckpoint 关掉后灌 20 MB+，收一次之后 wal 必须回到 8 MB 以内。

        正常运行时 autocheckpoint 会边写边并，WAL 长不大（这就是「逐笔合并」和
        「文件不缩」的区别）；把自动合并关掉才能造出历史上那种几十 MB 的现场。
        """
        self.con.execute('PRAGMA wal_autocheckpoint=0')
        self.con.execute('CREATE TABLE IF NOT EXISTS __wal_test(k TEXT PRIMARY KEY, body TEXT)')
        body = ('wal-reclaim-' * 400)  # ≈4.8 KB/行，60 行就够 8 MB
        for i in range(2200):
            self.con.execute(
                'INSERT OR REPLACE INTO __wal_test(k, body) VALUES (?, ?)',
                ('k%05d' % i, body))
            if i % 200 == 0:
                self.con.commit()
        self.con.commit()
        wal = Path(brain.DB + '-wal')
        self.assertTrue(wal.exists())
        before, after = brain.wal_tidy_once()
        self.assertGreater(before, 8 * 1024 * 1024)
        self.assertLessEqual(after, 8 * 1024 * 1024)
        self.assertGreater(before - after, 1024 * 1024)

    def test_small_wal_left_alone(self):
        self.con.execute('INSERT INTO meta(path, indexed_at) VALUES (?, 1.0)', ('x',))
        self.con.commit()
        wal = Path(brain.DB + '-wal')
        before = wal.stat().st_size if wal.exists() else 0
        b, a = brain.wal_tidy_once()
        self.assertEqual(b, a)
        self.assertLessEqual(b, 8 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
