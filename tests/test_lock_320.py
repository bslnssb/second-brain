# -*- coding: utf-8 -*-
"""第 320 项：库被长任务占住时，别让界面干等 90 秒。

现场（11-19 10:5x）：所有 /api 都 90 秒无响应，只有不走 `con_lock` 的
`/api/ping` 还是毫秒级。原因是每个 DB 读写都排在同一把全局锁后面，
一个长任务把锁攥住，所有 HTTP 线程就一起僵住 —— 用户看到的是
「搜什么都转圈、ext:pdf 一条 PDF 都没有」（第 319 项就是被这个拖累的）。

这一项做三件事：
1. `db_lock(where, timeout)`：等锁超过 timeout 就 `LockBusy`，并把现场写进
   debug.log（`DB_LOCK_BUSY`）；等锁超过 3 秒也记一行（`DB_LOCK_SLOW`）。
2. `/api/files`、正文那一路 `/api`、`/api/stats` 换成带超时的锁；等不到时
   回 `st='busy'` / `busy: true`，而不是把空列表当成「没有结果」。
3. 前端看到 busy：提示「索引在忙」，2.5 秒后自动重试**一次**。
"""
import io
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brain  # noqa: E402


def read(name):
    with io.open(os.path.join(ROOT, name), 'r', encoding='utf-8') as f:
        return f.read()


class DbLockRuntimeTest(unittest.TestCase):
    def test_raises_busy_instead_of_waiting_forever(self):
        """别的线程攥着锁（RLock 对同线程可重入，所以必须换个线程）→ 立刻 LockBusy。"""
        held = threading.Event()
        done = threading.Event()

        def hog():
            brain.con_lock.acquire()
            held.set()
            try:
                done.wait(3.0)
            finally:
                brain.con_lock.release()

        th = threading.Thread(target=hog, daemon=True)
        th.start()
        held.wait(2.0)
        try:
            t0 = time.time()
            with self.assertRaises(brain.LockBusy):
                with brain.db_lock('test', 0.3):
                    pass
            self.assertLess(time.time() - t0, 3.0, '等锁超时后必须立刻放弃，不能继续挂着')
        finally:
            done.set()
            th.join(3.0)

    def test_releases_lock_after_normal_use(self):
        with brain.db_lock('test-ok', 5.0):
            pass
        # 放干净了：下一次拿锁不该超时
        with brain.db_lock('test-again', 5.0):
            pass
        self.assertFalse(brain.con_lock._is_owned(), 'db_lock 用完必须把锁放掉')

    def test_lock_is_reentrant_like_con_lock(self):
        # 同一线程里嵌套（有些函数内部又开一层 with db_lock）不能自己把自己锁死
        with brain.db_lock('outer', 5.0):
            with brain.db_lock('inner', 5.0):
                pass


class DbLockSourceContractTest(unittest.TestCase):
    def test_timeout_and_slow_log_present(self):
        b = read('brain.py')
        self.assertIn("con_lock.acquire(timeout=self.timeout)", b)
        self.assertIn('DB_LOCK_BUSY', b)
        self.assertIn('DB_LOCK_SLOW', b)
        self.assertIn('if waited > 3:', b)

    def test_api_files_marks_busy(self):
        b = read('brain.py')
        # 第 327 项：三条搜索路径都改成只读连接（不抢 con_lock），
        # 但 busy 回落语义保留 —— 前端那段重试逻辑还在用。
        self.assertIn("with ro_db('/api/files')", b)
        self.assertIn("with ro_db('/api/files:syn')", b)
        self.assertIn("with ro_db('/api/files:recent')", b)
        self.assertNotIn("with db_lock('/api/files')", b)
        self.assertGreaterEqual(b.count("st = 'busy'"), 2)

    def test_body_route_marks_busy(self):
        b = read('brain.py')
        self.assertIn("with ro_db('/api:body')", b)
        self.assertIn("'q': q, 'busy': busy", b)

    def test_stats_keeps_old_cache_when_busy(self):
        b = read('brain.py')
        seg = b[b.find("elif self.path.startswith('/api/stats'):"):]
        seg = seg[:seg.find("elif self.path.startswith('/api/settings'):")]
        self.assertIn("ro_db('/api/stats')", seg)
        self.assertIn('except LockBusy:', seg)

    def test_frontend_handles_busy_and_retries_once(self):
        app = read(os.path.join('web', 'src', 'App.tsx'))
        self.assertIn("f.busy || f.st === 'busy' || b.busy", app)
        self.assertIn("busyRetry", app)
        self.assertIn("setTimeout(() => setReload((n) => n + 1), 2500)", app)
        i18n = read(os.path.join('web', 'src', 'lib', 'i18n.ts'))
        self.assertEqual(i18n.count('db_busy:'), 5, 'db_busy 要在 5 种语言里都给文案')


if __name__ == '__main__':
    unittest.main()
