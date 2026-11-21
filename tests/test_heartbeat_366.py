# -*- coding: utf-8 -*-
"""第 366 项：只读心跳不算用户活动 + 手动整理入口的进度对象。

为什么值得单独测：线上库一直停在 v1，根因就是 `_touch_activity()` 被前端 5 秒轮询
（`/api/ocr_batch`）喂着，326 的空闲判断永不成立。这条回归如果被改坏，
迁移又会悄悄停摆，而且没有任何界面提示。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brain


class HeartbeatWhitelistTest(unittest.TestCase):
    def test_polled_reads_are_heartbeat(self):
        for p in ('/api/ocr_batch', '/api/index/verify', '/api/update_status',
                  '/api/snip/result', '/api/ping', '/api/compact'):
            self.assertTrue(brain._is_heartbeat(p), p)

    def test_query_string_ignored(self):
        self.assertTrue(brain._is_heartbeat('/api/ocr_batch?_t=123'))
        self.assertTrue(brain._is_heartbeat('/api/compact?v=2'))

    def test_real_uses_are_activity(self):
        for p in ('/api', '/api?q=abc', '/api/files', '/api/stats',
                  '/api/settings', '/api/wall/image', '/api/mini'):
            self.assertFalse(brain._is_heartbeat(p), p)

    def test_prefix_does_not_leak(self):
        # 只认整段：/api/pingpong 这种不能被误判成心跳，否则真正的请求会被漏记活动
        self.assertFalse(brain._is_heartbeat('/api/pingpong'))
        self.assertFalse(brain._is_heartbeat('/api/ocr_batch_add'))

    def test_trailing_slash_ok(self):
        self.assertTrue(brain._is_heartbeat('/api/compact/'))


class CompactStateTest(unittest.TestCase):
    def test_default_state_is_idle(self):
        self.assertEqual('idle', brain.COMPACT['state'])
        self.assertIn('before', brain.COMPACT)
        self.assertIn('after', brain.COMPACT)

    def test_job_busy_includes_compact(self):
        saved = dict(brain.COMPACT)
        try:
            brain.COMPACT['state'] = 'busy'
            self.assertTrue(brain._job_busy())
        finally:
            brain.COMPACT.clear()
            brain.COMPACT.update(saved)

    def test_prog_updates_fields(self):
        saved = dict(brain.COMPACT)
        try:
            brain._compact_prog(phase='compress', done=10, total=200,
                                k='maint_compact_compress', a={})
            self.assertEqual('compress', brain.COMPACT['phase'])
            self.assertEqual(10, brain.COMPACT['done'])
            self.assertEqual(200, brain.COMPACT['total'])
        finally:
            brain.COMPACT.clear()
            brain.COMPACT.update(saved)


class IdleLoopOrderTest(unittest.TestCase):
    """`_compact_loop` 里「活动<600 就先撤」必须排在 trim 之前。

    原来 trim 写在「活动<600」的分支里：用户正用着才 trim、真空闲反而不 trim，
    和 _trim_working_set 的 docstring（空闲时还工作集）正好相反。这条用源码顺序守住。"""

    def test_trim_happens_only_when_idle(self):
        import io
        src = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'brain.py'), encoding='utf-8').read()
        i_loop = src.index('def _compact_loop')
        body = src[i_loop:i_loop + 4000]
        i_idle = body.index("if time.time() - _ACTIVITY['t'] < 600:")
        i_trim = body.index('_trim_working_set()')
        self.assertLess(i_idle, i_trim)
        # <600 分支里必须先 continue（撤出去），trim 不能落在它前面
        seg = body[i_idle:i_trim]
        self.assertIn('continue', seg)


if __name__ == '__main__':
    unittest.main()
