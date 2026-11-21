# -*- coding: utf-8 -*-
"""第 327 项（性能二轮）：读路径不再抢写锁。

debug.log 的现场证据（2026-11-19 13:37）：
    DB_LOCK_SLOW /api:body  等锁 5.7s
    DB_LOCK_SLOW /api/files 等锁 5.4s / 5.3s ×3
    === THREAD DUMP con_lock 被占 20s ===
一个「5.6 秒的正文查询」把整把 con_lock 攥住，其它 /api 全排队，前端拿不到结果
就渲染成空列表 —— 用户看到的是「列表空白 + 用着用着突然很卡」。

做法：读走只读连接池（WAL 一写多读，读本来就不需要锁），写仍走 con_lock。
这里守四件事：
1. 只读连接真的是 mode=ro + query_only，不会误写；
2. 只有读的热接口用 ro_db，不再出现 `with db_lock('/api:...')` 的老写法；
3. 真库上手测：另一个进程攥着写事务时，只读连接照样秒回；
4. 实时监听写入撞上 locked 时把这一批塞回队列，不再丢。
"""
import io
import os
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def read(name):
    with io.open(os.path.join(ROOT, name), 'r', encoding='utf-8') as f:
        return f.read()


class RoConnTest(unittest.TestCase):
    def setUp(self):
        import brain

        self.brain = brain
        self.tmp = tempfile.mkdtemp(prefix='sb_ro_')
        self.path = os.path.join(self.tmp, 'ro.db')
        self.old_db = brain.DB
        brain.DB = self.path
        w = sqlite3.connect(self.path, timeout=5)
        w.execute('PRAGMA journal_mode=WAL')
        w.execute('CREATE TABLE t (k TEXT)')
        w.execute("INSERT INTO t VALUES ('hello')")
        w.commit()
        self.w = w
        while not brain._RO_POOL.empty():
            brain._RO_POOL.get_nowait()

    def tearDown(self):
        b = self.brain
        while not b._RO_POOL.empty():
            c = b._RO_POOL.get_nowait()
            try:
                c.close()
            except Exception:
                pass
        try:
            self.w.close()
        except Exception:
            pass
        b.DB = self.old_db
        try:
            for n in os.listdir(self.tmp):
                os.remove(os.path.join(self.tmp, n))
            os.rmdir(self.tmp)
        except Exception:
            pass

    def test_readonly_flags(self):
        c = self.brain._open_ro()
        self.assertEqual(c.execute('PRAGMA query_only').fetchone()[0], 1)
        self.assertGreaterEqual(c.execute('PRAGMA busy_timeout').fetchone()[0], 8000)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM t').fetchone()[0], 1)
        try:
            c.execute("INSERT INTO t VALUES ('nope')")
            self.fail('只读连接居然写成功了')
        except sqlite3.OperationalError:
            pass
        c.close()

    def test_pool_reuse(self):
        c1 = self.brain._ro_acquire()
        self.brain._ro_release(c1)
        c2 = self.brain._ro_acquire()
        self.assertIs(c2, c1)
        self.brain._ro_release(c2)

    def test_reads_while_writer_holds_lock(self):
        """写事务攥着锁时，读必须还是毫秒级 —— 这就是「卡」的根因。"""
        self.w.execute('BEGIN IMMEDIATE')
        self.w.execute("UPDATE t SET k='busy' WHERE k='hello'")
        t0 = time.time()
        c = self.brain._open_ro()
        got = c.execute('SELECT COUNT(*) FROM t').fetchone()[0]
        dt = time.time() - t0
        c.close()
        self.assertEqual(got, 1)
        self.assertLess(dt, 1.0, '只读连接被写锁挡了 %.2fs' % dt)
        self.w.rollback()

    def test_ro_db_falls_back_to_lock(self):
        """只读连接打不开（库还没建 / 权限）时必须退回老路径，且把锁放掉。"""
        b = self.brain
        real_acq, real_conn = b._ro_acquire, b.conn
        b._ro_acquire = lambda: (_ for _ in ()).throw(sqlite3.OperationalError('x'))
        b.conn = lambda: self.w
        try:
            with b.ro_db('fallback') as c:
                self.assertIs(c, self.w)
                self.assertTrue(b.con_lock._is_owned())
            self.assertFalse(b.con_lock._is_owned(), '回退路径用完必须放锁')
        finally:
            b._ro_acquire, b.conn = real_acq, real_conn


class SourceContractTest(unittest.TestCase):
    def test_hot_apis_use_ro(self):
        b = read('brain.py')
        for w in ('/api/files', '/api/files:syn', '/api/files:recent',
                  '/api/facets', '/api:body', '/api/stats', '/api:recent',
                  '/api/peek', '/api/timeline'):
            self.assertIn("ro_db('%s" % w, b, '%s 还在抢写锁' % w)
        self.assertNotIn("with db_lock('/api:body')", b)
        self.assertNotIn("with db_lock('/api/files')", b)

    def test_pool_bounded(self):
        b = read('brain.py')
        self.assertIn('_RO_POOL = queue.Queue(maxsize=6)', b)
        self.assertIn('PRAGMA query_only=ON', b)
        self.assertIn('import queue', b)

    def test_watch_requeues_on_locked(self):
        b = read('brain.py')
        seg = b[b.find('def _watch_flusher'):]
        seg = seg[:seg.find('def _watch_same_as_indexed')]
        self.assertIn("'locked' in str(e).lower()", seg)
        self.assertIn("_WATCH['q'].setdefault(k, v)", seg)

    def test_slim_marks_before_work_and_waits_idle(self):
        b = read('brain.py')
        seg = b[b.find('def _slim_docs_once'):]
        seg = seg[:seg.find('def _facets_warm_loop')]
        self.assertLess(seg.index("_kv_set(con, _SLIM_KV, '1')"),
                        seg.index('slim:trim'), '标记必须先写')
        self.assertIn("_ACTIVITY['t'] < 120", seg)
        self.assertIn('range(60)', seg)

    def test_warm_loop_uses_ro(self):
        b = read('brain.py')
        seg = b[b.find('def _facets_warm_loop'):]
        seg = seg[:seg.find('def _compact_loop')]
        self.assertIn('ro_db', seg)

    def test_warm_loop_skips_when_unchanged(self):
        """第 328 项：数据没变就别每分钟重算一遍（那 2~3 秒攥着 GIL 会卡界面）。"""
        b = read('brain.py')
        seg = b[b.find('def _facets_warm_loop'):]
        seg = seg[:seg.find('def _compact_loop')]
        self.assertIn('COUNT(*) FROM files', seg)
        self.assertIn('COUNT(*) FROM dirs', seg)
        self.assertEqual(1, seg.count('facets(rc, {})'), '应该只在数据变了才重算')

    def test_folder_bucket_memoised(self):
        """归桶要缓存 + roots 只算一次 normcase（py-spy 实测的 CPU 热点）。"""
        b = read('brain.py')
        self.assertIn('_FBUCKET_MEMO', b)
        self.assertIn('def _roots_norm', b)
        seg = b[b.find('def _folder_bucket'):]
        seg = seg[:seg.find('def _fbucket_memo_put')]
        self.assertIn('_FBUCKET_MEMO.get', seg)

    def test_kv_set_commits_on_shared_conn(self):
        """_kv_set 走共享连接时必须自己提交 —— 少这一下，全程序的写库都被锁。"""
        b = read('brain.py')
        seg = b[b.find('def _kv_set('):]
        seg = seg[:seg.find('_BIGRAM_READY = ')]
        self.assertIn('con is _con', seg)
        self.assertIn('con.commit()', seg)

    def test_transaction_leak_watchdog(self):
        b = read('brain.py')
        seg = b[b.find('def _lock_watchdog'):]
        seg = seg[:seg.find('_dump_thread_stacks') + 40]
        self.assertIn('in_transaction', seg)
        self.assertIn('rollback', seg)
        self.assertIn('事务泄漏', seg)


class KvCommitTest(unittest.TestCase):
    def test_commits_shared_conn(self):
        import brain

        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE kv (key TEXT PRIMARY KEY, val TEXT)')
        old = brain._con
        brain._con = con
        try:
            brain._kv_set(con, 'k', 'v')
            self.assertFalse(con.in_transaction, '_kv_set 之后事务还开着')
            self.assertEqual(brain._kv_get(con, 'k'), 'v')
        finally:
            brain._con = old
            con.close()


class FolderBucketTest(unittest.TestCase):
    def test_same_bucket_and_memo(self):
        import brain

        roots = [(r'C:\x\项目', __import__('os').path.normcase(r'C:\x\项目'))]
        a = brain._folder_bucket(r'C:\x\项目\钢铁\内部', roots)
        b = brain._folder_bucket(r'C:\x\项目\钢铁\内部', roots)
        self.assertEqual(a, b)
        self.assertEqual('钢铁', a[0])
        self.assertIn('钢铁', brain._FBUCKET_MEMO[
            __import__('os').path.normcase(r'C:\x\项目\钢铁\内部')][1])

    def test_roots_accept_plain_paths(self):
        import brain

        pairs = brain._roots_norm([r'D:\资料\项目'])
        self.assertEqual(1, len(pairs))
        self.assertTrue(isinstance(pairs[0][1], str))

