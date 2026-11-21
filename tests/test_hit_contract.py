# -*- coding: utf-8 -*-
"""结果行的字段契约：前端读什么，后端就必须给什么。

背景（2026-11-15 用户实测截图）：侧栏选「视频」后，结果行的「大小」列一片空白，
而库里 size 全都有值。根因不是数据，是**字段名对不上**——
`/api/files` 的四条产出路径里，纯语法筛选（`ext:mp4` 就是这条路）和「空关键词浏览最近」
写的是 `'z': size`，而 React 那边 `fmtSize(it.s)` 读的是 `s`，于是恒为 undefined。
关键词搜索借道的 `mini_search` 也是 `z`。也就是说主窗口**每一行的大小都是空的**，
只是这次选了「视频」才被看见。

为什么单测没抓到：`fmtSize` 是纯函数、`hits.ts` 有单测，但两边都没验证
「接口字典里到底有没有那个键」。这类"接线错"只有契约测试能挡。
所以这里直接调真实产出函数，断言每条结果都带可用的 `s`。

跑法：py -3.12 -m unittest tests.test_hit_contract -v
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

FAKE = [
    ('D:' + B + 'media' + B + 'a.mp4', 'a.mp4', '.mp4', 1_700_000_000.0, 4_770_468_151.0),
    ('D:' + B + 'media' + B + 'b.webm', 'b.webm', '.webm', 1_700_000_100.0, 12_345.0),
    ('D:' + B + 'docs' + B + 'c.pdf', 'c.pdf', '.pdf', 1_700_000_200.0, 958.0),
]


def _db():
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE files(path TEXT PRIMARY KEY, name TEXT, dir TEXT, ext TEXT,'
                ' size REAL, mtime REAL, descr TEXT, py TEXT, ini TEXT)')
    con.executemany('INSERT INTO files(path,name,dir,ext,size,mtime) VALUES(?,?,?,?,?,?)',
                    [(p, n, os.path.dirname(p), e, s, m) for p, n, e, m, s in FAKE])
    con.commit()
    return con


class HitSizeContract(unittest.TestCase):

    def _assert_rows_ok(self, items, label):
        self.assertTrue(items, label + '：一条都没返回，测试本身失效了')
        for it in items:
            self.assertIn('s', it, '%s：缺 s 字段（前端 fmtSize 读的就是它）%r'
                          % (label, it.get('n')))
            self.assertIsNotNone(it.get('s'), '%s：s 为 None %r' % (label, it.get('n')))
            self.assertGreater(it['s'], 0, '%s：s 不是正数字 %r' % (label, it.get('n')))
            self.assertEqual(it['s'], it.get('z'),
                             '%s：s 与 z 不一致（双写必须同步）%r' % (label, it.get('n')))

    def test_syntax_only_items_has_size(self):
        """纯语法筛选（侧栏选类型走的就是这条）—— 用户截图里空的那一列。"""
        con = _db()
        items, _total = brain.syntax_only_items(con, brain.parse_qsyn('ext:mp4,webm'))
        self._assert_rows_ok(items, 'syntax_only_items')

    def test_size_field_matches_real_bytes(self):
        con = _db()
        items, _ = brain.syntax_only_items(con, brain.parse_qsyn('ext:mp4'))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['s'], 4_770_468_151.0, '4.4GB 的影片必须原样带出来')

    def test_sz_of_accepts_both_names(self):
        self.assertEqual(brain._sz_of({'s': 10}), 10)
        self.assertEqual(brain._sz_of({'z': 20}), 20)
        self.assertIsNone(brain._sz_of({}))


if __name__ == '__main__':
    unittest.main()
