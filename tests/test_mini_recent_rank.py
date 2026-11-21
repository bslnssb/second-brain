# -*- coding: utf-8 -*-
"""`_recent_map` 得分范围的回归测试（2026-11-15 用户实测：搜 ayu 时应用没排第一）。

背景：快搜窗把「最近使用」当作正向加成：

    s += 0.30 * rec.get(os.path.normcase(it['p']), 0.0)   # brain.py mini_search._rel

注释承诺「最多 0.30，压不过名字像的差距」。但老实现是

    out[path] = (hits or 1) - i * 0.05

`i` 是 recent 表按 last 倒序的序号。表涨到 459 行后，尾部行变成大负数
（hits=3、i=412 → -17.6），乘 0.30 = -5.28，把 1.45 的正分压成负分。
结果「开过但不算最近」的应用被踢到最后：搜 ayu 时 AyuGram.lnk 掉到第 7 位，
排在 D:\\AyuGram 文件夹和几个 ayu-*.js 后面 —— 与「越用越准」完全相反。

这个 bug 已经出现过两次（先前是微信），所以固化成测试。

跑法：python -m unittest tests.test_mini_recent_rank -v
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402


def _mem_db(rows):
    """rows: [(path, hits, last)]，last 越大越新。"""
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE recent(path TEXT PRIMARY KEY, name TEXT, isdir INT,'
                ' hits INT, last REAL)')
    con.executemany('INSERT INTO recent(path,name,isdir,hits,last) VALUES(?,?,0,?,?)',
                    [(p, p.rsplit('\\', 1)[-1], h, l) for p, h, l in rows])
    con.commit()
    return con


class TestRecentWeightRange(unittest.TestCase):
    """得分必须落在 (0, 1]，否则乘 0.30 之后会破坏排序。"""

    def test_no_negative_weight_on_large_table(self):
        # 500 行：模拟 recent 表已经积累了很多记录
        rows = [('C:\\x\\f%03d.txt' % i, 1, 1000.0 - i) for i in range(500)]
        w = brain._recent_map(_mem_db(rows))
        self.assertEqual(len(w), 500)
        bad = {k: v for k, v in w.items() if v <= 0 or v > 1.0}
        self.assertFalse(bad, '得分越界（必须 0 < w <= 1）：%r' % list(bad.items())[:3])

    def test_app_with_few_hits_far_down_is_still_positive(self):
        # 真实复现：AyuGram.lnk hits=3、排在 412 位（老实现算出 -17.6）
        rows = [('C:\\x\\pad%03d.txt' % i, 1, 2000.0 - i) for i in range(412)]
        rows.append(('C:\\Menu\\AyuGram.lnk', 3, 100.0))
        rows += [('C:\\x\\old%03d.txt' % i, 1, 50.0 - i) for i in range(87)]
        w = brain._recent_map(_mem_db(rows))
        v = w[brain.os.path.normcase('C:\\Menu\\AyuGram.lnk')]
        self.assertGreater(v, 0, '开过 3 次的应用不该得到非正权重')
        self.assertLessEqual(v, 1.0)

    def test_newest_gets_full_and_boost_cap(self):
        rows = [('C:\\a.lnk', 1, 1000.0), ('C:\\b.lnk', 1, 900.0)]
        w = brain._recent_map(_mem_db(rows))
        ka, kb = brain.os.path.normcase('C:\\a.lnk'), brain.os.path.normcase('C:\\b.lnk')
        # 最新一条 = fresh 1.0 + freq 0.2 → 0.6；关键是不超过 1.0
        self.assertAlmostEqual(w[ka], 0.6, places=6)
        self.assertGreater(w[ka], w[kb], '越新的应该得分越高')
        # mini_search._rel 里是 0.30 * w，必须 <= 0.30（注释承诺的加成上限）
        self.assertLessEqual(0.30 * max(w.values()), 0.30 + 1e-9)

    def test_more_hits_wins_at_same_position(self):
        rows = [('C:\\a.lnk', 1, 1000.0), ('C:\\b.lnk', 9, 1000.0)]
        w = brain._recent_map(_mem_db(rows))
        ka, kb = brain.os.path.normcase('C:\\a.lnk'), brain.os.path.normcase('C:\\b.lnk')
        self.assertGreater(w[kb], w[ka], '同一位置下开得多的应该得分更高')

    def test_empty_table(self):
        self.assertEqual(brain._recent_map(_mem_db([])), {})


if __name__ == '__main__':
    unittest.main()
