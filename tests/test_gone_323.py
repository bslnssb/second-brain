# -*- coding: utf-8 -*-
"""第 323 项：列表里的路径要验活，库里死路径要定时清。

用户实测：很多图片的路径早就没了，列表里还挂着，点开根本打不开。
根因：`mini_search` 只验「关键字命中」那几十条，「浏览最近文件」和「纯语法筛选
（ext:pdf 这类）」两条路一条都不验；监听漏事件时库里也会留死行。
"""
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def read(name):
    with io.open(os.path.join(ROOT, name), 'r', encoding='utf-8') as f:
        return f.read()


class FilterAliveTest(unittest.TestCase):
    def test_drops_missing_keeps_existing(self):
        import brain
        d = tempfile.mkdtemp()
        alive = os.path.join(d, 'here.txt')
        with io.open(alive, 'w', encoding='utf-8') as f:
            f.write('x')
        gone = os.path.join(d, 'nope.png')
        items = [{'p': alive, 'n': 'here.txt'}, {'p': gone, 'n': 'nope.png'}]
        out = brain.filter_alive(items, budget=2.0)
        self.assertEqual(1, len(out))
        self.assertEqual(alive, out[0]['p'])
        self.assertIn(os.path.normcase(gone), brain.GONE_SEEN)

    def test_empty_input(self):
        import brain
        self.assertEqual([], brain.filter_alive([]))


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = read('brain.py')

    def test_files_route_filters_recent_and_syntax(self):
        self.assertIn('items = filter_alive(items)', self.b)
        seg = self.b[self.b.find("elif self.path.startswith('/api/files')"):]
        seg = seg[:seg.find("elif self.path.split('?')[0] == '/api/web_search'")]
        self.assertIn('if not q:', seg)
        self.assertIn('items = filter_alive(items)', seg)

    def test_reconcile_thread_started(self):
        self.assertIn("name='gone-reconcile'", self.b)
        self.assertIn('target=_gone_reconcile_loop', self.b)

    def test_reconcile_guards(self):
        seg = self.b[self.b.find('def _reconcile_gone('):]
        seg = seg[:seg.find('def _gone_reconcile_loop(')]
        self.assertIn('os.path.splitdrive(p)[0]', seg)      # 整盘不可达整体跳过
        self.assertIn('二次确认', seg)                        # 一批死太多 → 二次确认再删
        self.assertIn("db_lock('gone:del'", seg)             # 删库走短锁
        self.assertIn('DELETE FROM files WHERE path=?', seg)

    def test_budgeted_pool(self):
        seg = self.b[self.b.find('def filter_alive('):]
        seg = seg[:seg.find('def _reconcile_gone(')]
        self.assertIn('ThreadPoolExecutor', seg)
        self.assertIn('timeout=budget', seg)
        self.assertIn('alive check: %d 条超时未验（按存在处理）', seg)


if __name__ == '__main__':
    unittest.main()
