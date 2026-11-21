# -*- coding: utf-8 -*-
"""第 327 项：左栏「文件夹」不再是 0 + 左栏三块可在设置里关掉。

用户贴的截图：类型列表里「文件夹」后面没有数字（看着就是 0），而「全部」68652
其实只算了文件 —— 点「全部」和点「文件夹」看到的不是同一批东西，数字也对不上。
这里守两件事：
1. facets 把 dirs 也算进 kinds 和 total（关键词要能收窄它，ext 语法下文件夹为 0）；
2. 左栏三块（类型 / 文件夹分组 / 时间）各有一个设置开关，前后端键名必须一致。
"""
import io
import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def read(name):
    with io.open(os.path.join(ROOT, name), 'r', encoding='utf-8') as f:
        return f.read()


def mkdb():
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE files (path TEXT, name TEXT, dir TEXT, ext TEXT, '
                'size INTEGER, mtime REAL, descr TEXT, py TEXT, ini TEXT)')
    con.executemany(
        'INSERT INTO files (path,name,dir,ext,size,mtime,descr,py,ini) VALUES (?,?,?,?,?,?,?,?,?)',
        [(r'C:\x\a.pdf', 'a.pdf', r'C:\x', '.pdf', 1, 1.0, None, None, None),
         (r'C:\x\b.docx', 'b.docx', r'C:\x', '.docx', 1, 1.0, None, None, None)])
    con.execute('CREATE TABLE dirs (path TEXT, name TEXT, parent TEXT, py TEXT, ini TEXT, mtime REAL)')
    con.executemany('INSERT INTO dirs (path,name,parent) VALUES (?,?,?)',
                    [(r'C:\x\报告集', '报告集', r'C:\x'),
                     (r'C:\x\图片库', '图片库', r'C:\x'),
                     (r'C:\x\报告备份', '报告备份', r'C:\x')])
    return con


class FolderCountTest(unittest.TestCase):
    def setUp(self):
        import brain

        self.brain = brain
        # facets 现在带 30 秒模块级缓存，测试同进程跑时会把上一个用例的库结果带过来
        self.brain._FACETS_CACHE.clear()
        self.con = mkdb()

    def tearDown(self):
        self.brain._FACETS_CACHE.clear()

    def test_dir_kind_counted(self):
        f = self.brain.facets(self.con, {'q': ''})
        kinds = {x['k']: x['n'] for x in f['kinds']}
        self.assertEqual(3, kinds.get('dir'))
        # 2 个文件 + 3 个文件夹
        self.assertEqual(5, f['total'])

    def test_keyword_narrows_folders_too(self):
        f = self.brain.facets(self.con, {'q': '报告'})
        kinds = {x['k']: x['n'] for x in f['kinds']}
        self.assertEqual(2, kinds.get('dir'))
        self.assertEqual(2, f['total'])

    def test_ext_filter_excludes_folders(self):
        f = self.brain.facets(self.con, {'q': '', 'ext': 'pdf'})
        kinds = {x['k']: x['n'] for x in f['kinds']}
        self.assertNotIn('dir', kinds)
        self.assertEqual(1, f['total'])


class DirListTest(unittest.TestCase):
    """第 327 项第二半：点「文件夹」要真的有东西。"""

    def setUp(self):
        import brain

        self.brain = brain
        self.con = mkdb()

    def test_lists_dirs_not_files(self):
        rows = self.brain.dir_rows(self.con, '', 10)
        self.assertEqual(3, len(rows))
        self.assertTrue(all(r['t'] == 'dir' for r in rows))
        self.assertIn('报告集', [r['n'] for r in rows])

    def test_keyword_narrows(self):
        rows = self.brain.dir_rows(self.con, '报告', 10)
        self.assertEqual(2, len(rows))

    def test_prefix_filter(self):
        rows = self.brain.dir_rows(self.con, '', 10, r'C:\x')
        self.assertEqual(3, len(rows))


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = read('brain.py')
        cls.app = read(os.path.join('web', 'src', 'App.tsx'))
        cls.panels = read(os.path.join('web', 'src', 'ui', 'panels.tsx'))
        cls.api = read(os.path.join('web', 'src', 'api.ts'))
        cls.i18n = read(os.path.join('web', 'src', 'lib', 'i18n.ts'))

    def test_backend_counts_dirs(self):
        self.assertIn('SELECT COUNT(*) FROM dirs', self.b)
        self.assertIn("'dir', 'doc', 'img'", self.b)

    def test_dirs_endpoint(self):
        self.assertIn("self.path.startswith('/api/dirs')", self.b)
        self.assertIn("ro_db('/api/dirs')", self.b)
        self.assertIn('/api/dirs?q=', self.app)

    def test_settings_keys_whitelisted(self):
        # 一次循环收这几个键（第 338 项把 sb_counts 也加进来了）
        self.assertTrue("for _sbk in ('sb_types', 'sb_folders', 'sb_time', 'sb_counts')" in self.b)
        self.assertTrue("patch[_sbk] = bool(req[_sbk])" in self.b)

    def test_frontend_switches(self):
        self.assertIn('sb_types', self.api)
        self.assertIn('sb.types &&', self.app)
        self.assertIn('sb.folders &&', self.app)
        self.assertIn('sb.time &&', self.app)
        self.assertIn('onSettings?.(p)', self.panels)

    def test_i18n_keys_in_all_five(self):
        for k in ('nav_sidebar', 'sb_group', 'sb_types', 'sb_folders', 'sb_time'):
            self.assertEqual(5, self.i18n.count(k + ':'), k)


if __name__ == '__main__':
    unittest.main()
