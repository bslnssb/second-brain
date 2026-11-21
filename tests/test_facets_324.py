# -*- coding: utf-8 -*-
"""第 324 项：多关键词 AND + 左栏子分类计数（facets）。

用户贴了 gety 的搜索页，四件事要借：类型子分类、文件夹子分类、大预览、多关键词空格 AND。
这个文件守前两件：名字命中必须每个词都出现（以前整串当短语，搜「信息安全 pdf」名字一条不中），
以及 facets 的计数口径（只数名字命中、按文档子类型分组、文件夹按第一层目录聚类）。
"""
import io
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def read(name):
    with io.open(os.path.join(ROOT, name), 'r', encoding='utf-8') as f:
        return f.read()


def mkdb(rows):
    """最小 files 表：只放这几列，够 mini_search / facets 跑。"""
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE files (path TEXT, name TEXT, dir TEXT, ext TEXT, '
                'size INTEGER, mtime REAL, descr TEXT, py TEXT, ini TEXT)')
    con.executemany('INSERT INTO files (path,name,dir,ext,size,mtime,descr,py,ini) '
                    'VALUES (?,?,?,?,?,?,?,?,?)', rows)
    return con


class MultiKeywordTest(unittest.TestCase):
    def setUp(self):
        import brain

        self.brain = brain
        # mini_search 会对 top N 做 exists() 验活（这是第 323 项的行为，不能绕过），
        # 所以测试用真文件：临时目录里建几个空文件即可。
        self.tmp = tempfile.mkdtemp()

    def _mk(self, sub, name):
        d = os.path.join(self.tmp, sub)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with io.open(p, 'w', encoding='utf-8') as f:
            f.write('x')
        return p

    def test_all_tokens_must_hit_name(self):
        a = self._mk('d', '子公司2025营收预测.xlsx')
        self._mk('d', '子公司2025预算.xlsx')
        self._mk('d', 'only子公司.txt')
        con = mkdb([(a, os.path.basename(a), os.path.dirname(a), '.xlsx', 10, 1.0, None, None, None),
                    (os.path.join(self.tmp, 'd', '子公司2025预算.xlsx'),
                     '子公司2025预算.xlsx', os.path.dirname(a), '.xlsx', 10, 1.0, None, None, None),
                    (os.path.join(self.tmp, 'd', 'only子公司.txt'),
                     'only子公司.txt', os.path.dirname(a), '.txt', 10, 1.0, None, None, None)])
        r = self.brain.mini_search(con, '子公司 营收', limit=10, deep=True)
        names = [x['n'] for x in r['items']]
        self.assertEqual(['子公司2025营收预测.xlsx'], names)

    def test_single_token_unchanged(self):
        p = self._mk('d', '营收报告.pdf')
        con = mkdb([(p, '营收报告.pdf', os.path.dirname(p), '.pdf', 10, 1.0, None, None, None)])
        r = self.brain.mini_search(con, '营收', limit=10, deep=False)
        self.assertEqual(1, len(r['items']))

    def test_pf_path_filter_in_sql(self):
        pa = self._mk('a', '预算.pdf')
        pb = self._mk('b', '预算.pdf')
        con = mkdb([(pa, '预算.pdf', os.path.dirname(pa), '.pdf', 10, 1.0, None, None, None),
                    (pb, '预算.pdf', os.path.dirname(pb), '.pdf', 10, 1.0, None, None, None)])
        r = self.brain.mini_search(con, '预算', limit=10, deep=True,
                                   pf=os.path.join(self.tmp, 'b'))
        self.assertEqual([pb], [x['p'] for x in r['items']])


class FacetsTest(unittest.TestCase):
    def setUp(self):
        import brain

        self.brain = brain
        self.con = mkdb([
            (r'C:\x\a.pdf', 'a.pdf', r'C:\x', '.pdf', 1, 1.0, None, None, None),
            (r'C:\x\b.pdf', 'b.pdf', r'C:\x', '.pdf', 1, 1.0, None, None, None),
            (r'C:\x\c.docx', 'c.docx', r'C:\x', '.docx', 1, 1.0, None, None, None),
            (r'C:\y\d.png', 'd.png', r'C:\y', '.png', 1, 1.0, None, None, None),
        ])

    def test_doc_sub_counts(self):
        f = self.brain.facets(self.con, {'q': ''})
        subs = {x['k']: x['n'] for x in f['subs']}
        kinds = {x['k']: x['n'] for x in f['kinds']}
        self.assertEqual(2, subs.get('pdf'))
        self.assertEqual(1, subs.get('word'))
        self.assertEqual(3, kinds.get('doc'))
        self.assertEqual(1, kinds.get('img'))
        self.assertEqual(4, f['total'])

    def test_keyword_narrows_counts(self):
        f = self.brain.facets(self.con, {'q': 'a'})
        self.assertEqual(1, f['total'])
        self.assertEqual(['pdf'], [x['k'] for x in f['subs']])

    def test_ext_syntax_respected(self):
        f = self.brain.facets(self.con, {'q': '', 'ext': 'pdf'})
        self.assertEqual(2, f['total'])
        self.assertEqual([], [x['k'] for x in f['kinds'] if x['k'] != 'doc'])

    def test_folders_grouped_by_first_level(self):
        f = self.brain.facets(self.con, {'q': ''})
        names = {x['name']: x['n'] for x in f['folders']}
        # 没有索引范围文件时退化成「盘符 + 第一层」
        self.assertEqual(3, names.get('x'))
        self.assertEqual(1, names.get('y'))


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = read('brain.py')
        cls.app = read(os.path.join('web', 'src', 'App.tsx'))
        cls.i18n = read(os.path.join('web', 'src', 'lib', 'i18n.ts'))

    def test_facets_route_exists(self):
        self.assertIn("elif self.path.startswith('/api/facets')", self.b)

    def test_doc_subtypes_match_frontend(self):
        """后端 DOC_SUBTYPES 与前端 SUBTYPES 必须逐项一致。"""
        seg = self.b[self.b.find('DOC_SUBTYPES = ('):]
        seg = seg[:seg.find(')', seg.find("('txt'")) + 1]
        for k in ('pdf', 'word', 'excel', 'ppt', 'md', 'txt'):
            self.assertIn("('%s'," % k, seg)
            self.assertIn("['%s'," % k, self.app)

    def test_sidebar_has_sub_rows_and_folders(self):
        self.assertIn("t('sub_' + sk)", self.app)
        self.assertIn("setFolder(on ? '' : (f.p || ''))", self.app)
        self.assertIn('/api/facets', self.app)

    def test_i18n_has_sub_keys(self):
        for k in ('folders', 'sub_pdf', 'sub_word', 'sub_excel', 'sub_ppt', 'sub_md', 'sub_txt'):
            self.assertIn(k + ':', self.i18n)


if __name__ == '__main__':
    unittest.main()
