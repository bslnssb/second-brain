# -*- coding: utf-8 -*-
"""第 321 项：抓「谁攥着 con_lock」，并把 `os.stat` 赶出锁。

11-19 11:2x 实测：所有 /api 都回 `DB_LOCK_BUSY`（20 秒等不到锁），
可没人知道锁在谁手上。319/320 只是止损，这一项做两件事：

1. `_lock_watchdog`：每 1 秒试一次锁，连续 20 秒拿不到就把所有线程栈
   写进 debug.log（`=== THREAD DUMP ... ===`）。等锁的线程栈里有 acquire，
   不等的那个就是攥着锁的人。
2. `backfill_dir_mtime`：以前调用点在 `with con_lock:` 里，一圈 4000 个目录
   挨个 `os.stat`，碰上网络盘 / 云盘一个 stat 卡几秒，锁就被攥几分钟。
   现在只锁「读一页路径」「写一批结果」，`os.stat` 全在锁外。
"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with io.open(os.path.join(ROOT, name), 'r', encoding='utf-8') as f:
        return f.read()


class LockWatchdogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = read('brain.py')

    def test_watchdog_dumps_stacks(self):
        self.assertIn('def _lock_watchdog():', self.b)
        self.assertIn('def _dump_thread_stacks(', self.b)
        self.assertIn('_current_frames()', self.b)
        self.assertIn('THREAD DUMP', self.b)
        self.assertIn('con_lock 被占', self.b)

    def test_watchdog_starts_with_server(self):
        self.assertIn('target=_lock_watchdog', self.b)
        self.assertIn('name=\'lock-watchdog\'', self.b)

    def test_backfill_stats_outside_lock(self):
        seg = self.b[self.b.find('def backfill_dir_mtime('):]
        seg = seg[:seg.find('def _dir_rows(')]
        self.assertIn("db_lock('backfill_dir:read'", seg)
        self.assertIn("db_lock('backfill_dir:write'", seg)
        self.assertIn('os.stat(path).st_mtime', seg)
        # os.stat 必须在两个写锁块之间，而不是套在某一层大锁里
        i_read = seg.find("db_lock('backfill_dir:read'")
        i_stat = seg.find('os.stat(path).st_mtime')
        i_write = seg.find("db_lock('backfill_dir:write'")
        self.assertLess(i_read, i_stat)
        self.assertLess(i_stat, i_write)

    def test_boot_maint_does_not_wrap_backfill(self):
        seg = self.b[self.b.find('def _boot_maint'):]
        seg = seg[:seg.find('threading.Thread(target=_boot_maint')]
        self.assertIn('backfill_dir_mtime(conn(), limit=4000)', seg)
        self.assertNotIn("with con_lock:\n\n                    got = backfill_dir_mtime", seg)


if __name__ == '__main__':
    unittest.main()
