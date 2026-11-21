# -*- coding: utf-8 -*-
"""第 294 项 · 网络位置索引（net_index）回归。

守四件事：
  1. 网络路径判定只认 UNC / `//`，本地盘符、`\\?\` 前缀不乱认；
  2. 没有 net_index 位时，含网络位置的保存被拒；有该位时放行；
  3. 断线的网络路径探测**必须快速返回**（不能挂着等 SMB 超时）；
  4. 文件监听根里不许出现网络路径（SMB 监听不可靠）。
"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brain  # noqa: E402


class NetPath294(unittest.TestCase):
    def test_unc_detected(self):
        for p in (r'\\nas\share', r'\\NAS\团队\资料\2026', '//nas/share'):
            self.assertTrue(brain.is_net_path(p), p)

    def test_local_not_net(self):
        for p in (r'D:\docs', 'C:/Users/x', '', None, r'\\?\D:\x'):
            self.assertFalse(brain.is_net_path(p), repr(p))

    def test_dead_unc_fails_fast(self):
        """不可达的 UNC 必须在 ~3s 内判死，不能等 SMB 全过程超时。"""
        t0 = time.time()
        ok = brain._path_alive(r'\\memex-no-such-host-294\share', timeout=3.0)
        self.assertFalse(ok)
        self.assertLess(time.time() - t0, 8.0, '探测没有超时保护，会拖死索引')

    def test_real_dir_alive(self):
        d = tempfile.mkdtemp()
        try:
            self.assertTrue(brain._path_alive(d))
        finally:
            os.rmdir(d)


class NetGate294(unittest.TestCase):
    def _set(self, valid, features, plan='free'):
        brain._LIC['loaded'] = True
        brain._LIC['state'] = {
            'valid': valid, 'reason': 'ok' if valid else 'none', 'edition':
            'pro' if valid else 'free', 'kind': 'pro' if valid else '',
            'name': '', 'ident': '', 'exp': '', 'issued': '',
            'features': features, 'plan': plan, 'days_left': None,
            'code_masked': '', 'clock_warn': False, 'activated': '',
        }

    def test_free_blocks_network(self):
        self._set(False, [])
        self.assertTrue(brain.scope_net_blocked([r'\\nas\share']))

    def test_pro_with_net_index_allows(self):
        self._set(True, ['net_index'], plan='pro')
        self.assertFalse(brain.scope_net_blocked([r'\\nas\share']))

    def test_pro_without_net_index_blocks(self):
        self._set(True, ['export'], plan='pro')
        self.assertTrue(brain.scope_net_blocked([r'\\nas\share']))

    def test_local_dirs_never_blocked(self):
        self._set(False, [])
        self.assertFalse(brain.scope_net_blocked([r'D:\docs']))
        self.assertFalse(brain.scope_net_blocked([]))

    def test_existing_net_dir_not_reblocked(self):
        """已存在的网络位置：降级后改别的设置不该整份保存不了。"""
        self._set(False, [])
        old = [r'\\nas\share']
        self.assertFalse(brain.scope_net_blocked([r'\\nas\share', r'D:\docs'], old))
        # 但新增第二个网络位置仍然要被拦
        self.assertTrue(brain.scope_net_blocked([r'\\nas\share', r'\\nas2\x'], old))


class WatchRoots294(unittest.TestCase):
    def test_watch_roots_skip_network(self):
        """scope.json 里即使写了网络路径，监听根也不能收它。"""
        brain._scope_conf = lambda *a, **k: {'include_dirs': [r'\\nas\share']}
        roots = brain._watch_roots()
        self.assertFalse([r for r in roots if brain.is_net_path(r)],
                         '网络路径进了监听根：SMB 监听会丢事件/阻塞线程')


if __name__ == '__main__':
    unittest.main()
