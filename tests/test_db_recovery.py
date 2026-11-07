# -*- coding: utf-8 -*-
"""数据库损坏的自动恢复测试（断电/蓝屏之后的真实场景）。

用户那边最常见的「程序打不开」之一：机器突然断电，brain.db 留下半页脏数据，
下次启动每条 SQL 都抛 `database disk image is malformed`，所有接口 500，
界面一直转圈。普通用户不知道该删哪个文件，只能卸载重装（然后还是同一个坏库）。

这里锁住三件事：
  1. 坏库必须被**留档**（改名成 brain.db.bad-时间戳），不直接删；
  2. 之后必须能建出可用的空库（meta/docs 表都在）；
  3. 正常的库不能被误判（不能每次启动都留档一份）。

用子进程跑：`DB` 是 brain 模块导入时就定死的常量，同进程换目录换不干净。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

SNIPPET = (
    'import brain, sys;'
    'c = brain.conn();'
    'print("TABLES:" + ",".join(sorted(r[0] for r in '
    'c.execute("select name from sqlite_master where type=\'table\'"))))'
)


def run_in(directory: str):
    env = dict(os.environ)
    env['SB_DATA_DIR'] = directory
    env.pop('SB_DEBUG', None)
    return subprocess.run([PY, '-c', SNIPPET], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding='utf-8', timeout=120)


class TestDbRecovery(unittest.TestCase):
    def test_corrupt_db_is_quarantined_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'brain.db'
            db.write_text('this is not a sqlite file at all', encoding='utf-8')
            r = run_in(d)
            self.assertEqual(r.returncode, 0, '损坏库让程序直接崩了：%s' % r.stderr[-800:])
            self.assertIn('TABLES:', r.stdout)
            self.assertIn('meta', r.stdout.split('TABLES:', 1)[1])
            leftovers = [p.name for p in Path(d).iterdir() if '.bad-' in p.name]
            self.assertTrue(leftovers, '坏库没有被留档，用户的数据无从追回')
            self.assertTrue(db.exists(), '没有重建出新库')

    def test_healthy_db_is_not_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            r1 = run_in(d)
            self.assertEqual(r1.returncode, 0, r1.stderr[-800:])
            r2 = run_in(d)                     # 第二次启动用同一个库
            self.assertEqual(r2.returncode, 0, r2.stderr[-800:])
            self.assertIn('meta', r2.stdout)
            leftovers = [p.name for p in Path(d).iterdir() if '.bad-' in p.name]
            self.assertEqual(leftovers, [], '正常库被误判成损坏：%s' % leftovers)

    def test_zero_byte_db_is_treated_as_new(self):
        """0 字节的库是「刚建还没写」的常态，不能当损坏处理（否则会一直留档）。"""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'brain.db').write_bytes(b'')
            r = run_in(d)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertIn('meta', r.stdout)
            self.assertEqual([p for p in Path(d).iterdir() if '.bad-' in p.name], [])


if __name__ == '__main__':
    unittest.main()
