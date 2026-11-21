# -*- coding: utf-8 -*-
r"""`folder/` 语法：帮助里写了、解析器也认，但**过滤从来没实现过**（第 238 项）。

现场（2026-11-15 用户报大小列空白后做全链路排查时发现）：
- `parse_qsyn('工作\\')` 会把 `folder` 填好、把关键词剥空；
- `syn_ok()` 只看 size/dm/排除，**不看 folder**；
- 于是主窗把这条查询当成「没有关键词」→ 落到「浏览最近 400 条」，筛选等于没写；
- 快搜窗更直接：`q` 被剥空 → `mini_search('')` → 回 0 条，用户以为文件夹里没东西。

纯语法分支（`syntax_only_items`）同样没有 folder 条件，所以 `工作\ ext:pdf` 也不行。

跑法：py -3.12 -m unittest tests.test_folder_syntax -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402

B = chr(92)
NOW = 1_790_000_000.0

ROWS = [
    ('D:' + B + '工作' + B + '报告.txt', '报告.txt', '.txt', NOW, 100.0),
    ('D:' + B + '工作' + B + '报告.pdf', '报告.pdf', '.pdf', NOW, 200.0),
    ('E:' + B + '私人' + B + '报告.txt', '报告.txt', '.txt', NOW, 300.0),
    ('D:' + B + '工作归档' + B + '旧报告.txt', '旧报告.txt', '.txt', NOW - 86400 * 40, 400.0),
]


def _db():
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE files(path TEXT PRIMARY KEY, name TEXT, dir TEXT, ext TEXT,'
                ' size REAL, mtime REAL, descr TEXT, py TEXT, ini TEXT)')
    con.executemany('INSERT INTO files(path,name,dir,ext,size,mtime) VALUES(?,?,?,?,?,?)',
                    [(p, n, os.path.dirname(p), e, s, m) for p, n, e, m, s in ROWS])
    con.commit()
    return con


class TestFolderSyntax(unittest.TestCase):

    def test_parse_folder_takes_keyword_out(self):
        syn = brain.parse_qsyn('工作' + B)
        self.assertEqual(syn['folder'], '工作')
        self.assertEqual(syn['q'], '', '文件夹词不该再当关键词用')

        syn2 = brain.parse_qsyn('报告 工作' + B)
        self.assertEqual(syn2['folder'], '工作')
        self.assertEqual(syn2['q'], '报告')

    def test_syn_ok_filters_by_folder(self):
        syn = brain.parse_qsyn('工作' + B)
        self.assertTrue(brain.syn_ok('D:' + B + '工作' + B + '报告.txt', 100, NOW, syn))
        self.assertFalse(brain.syn_ok('E:' + B + '私人' + B + '报告.txt', 300, NOW, syn),
                         '不在这个文件夹里的结果必须被挡掉')

    def test_syntax_only_items_honours_folder(self):
        con = _db()
        items, total = brain.syntax_only_items(con, brain.parse_qsyn('工作' + B), 400)
        self.assertTrue(items, '纯语法 + 文件夹 必须能查出来（以前是 0 条）')
        self.assertEqual(total, len(items))
        for it in items:
            self.assertIn('工作', it['p'])

    def test_folder_combines_with_ext(self):
        con = _db()
        items, _ = brain.syntax_only_items(con, brain.parse_qsyn('工作' + B + ' ext:pdf'), 400)
        self.assertEqual([it['n'] for it in items], ['报告.pdf'])

    def test_folder_and_keyword_both_apply(self):
        con = _db()
        syn = brain.parse_qsyn('旧 工作' + B)
        self.assertEqual(syn['q'], '旧')
        # 有关键词时不走纯语法分支，但 syn_ok 仍必须挡住文件夹外的
        kept = [p for p, _n, _e, m, s in ROWS
                if brain.syn_ok(p, s, m, syn) and '旧' in p]
        self.assertEqual(len(kept), 1, '关键词 + 文件夹 两层过滤都要生效')
        self.assertIn('工作', kept[0])


if __name__ == '__main__':
    unittest.main()
