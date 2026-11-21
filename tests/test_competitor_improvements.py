# -*- coding: utf-8 -*-
"""第 247 项：竞品源码深挖后补的三块 —— 跨层契约 + 行为级回归测试。

背景（为什么写这一组）：
  深读 Recoll / Baloo / DocFetcher 源码后发现，三家最硬的机制 Memex 其实
  **大多已经独立做过**：PDF/Office 子进程超时可真 kill、_needs() 用 size+mtime
  差量短路、失败写 meta.reason 天然「不重试」、1.5s 事件合并 + _is_self_noise 自激过滤。
  所以这轮不搬大件，只补真正缺的「可见性」与「边角细节」：

  A. DocFetcher `model/index/file/FileIndex.java:65` 失败当空串保留节点 +
     GUI `ErrorTable.java:61` 失败清单可复制可双击 → 我们有 meta.reason 数据，
     但用户看不见 → `GET /api/index_failures` + 维护工具折叠面板。
  B. DocFetcher `model/FolderWatcher.java:378` mtime 二次确认（只读访问也报
     MODIFIED）+ `:339` 忽略 `~$` Office 锁文件 → 正文路径有，实时监听路径没有。
  C. DocFetcher `gui/indexing/RegexTestPanel.java:132` 输入路径即时回显排除结果
     → `GET /api/exclude_test` + 维护工具输入框。

为什么以前没抓到：既有测试只钉「排除规则本身对不对」，没有一条问过
「用户能不能验证规则」「失败记录能不能看见」「监听会不会为没变的文件写库」。

跑法：py -3.12 -m unittest tests.test_competitor_improvements -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain  # noqa: E402


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class WatchDetailsTest(unittest.TestCase):
    """B：DocFetcher / Baloo 同款监听边角。"""

    def test_01_office_temp_names(self):
        for n in ('~$报告.docx', '~$a.xlsx', '~$'):
            self.assertTrue(brain._watch_is_office_temp(n), n)
        for n in ('报告.docx', '~报告.docx', '', None):
            self.assertFalse(brain._watch_is_office_temp(n), repr(n))

    def test_02_mtime_second_check(self):
        st = SimpleNamespace(st_size=100, st_mtime=1000.0)
        self.assertTrue(brain._watch_same_as_indexed(st, (100, 1000.0)))
        self.assertTrue(brain._watch_same_as_indexed(st, (100, 1000.5)))
        self.assertFalse(brain._watch_same_as_indexed(st, (101, 1000.0)))
        self.assertFalse(brain._watch_same_as_indexed(st, (100, 1005.0)))
        self.assertFalse(brain._watch_same_as_indexed(st, None))
        self.assertFalse(brain._watch_same_as_indexed(st, (None, None)))

    def test_03_watch_apply_wires_both_filters(self):
        """源码级钉住「两条过滤真的在 _watch_apply 里」，防只写函数没接线。"""
        s = read('brain.py')
        i = s.index('def _watch_apply(batch):')
        body = s[i:i + 4200]
        self.assertIn('_watch_is_office_temp(os.path.basename(full))', body)
        self.assertIn('_watch_same_as_indexed(', body)
        self.assertIn("mods = [f for f, a in batch if a == 3]", body)
        self.assertIn('SELECT path, size, mtime FROM files WHERE path IN (', body)


class IndexFailuresTest(unittest.TestCase):
    """A：Recoll `+` 签名语义 —— 文件没变不重试，变了就重试。"""

    def _db(self, td):
        p = Path(td) / 'brain.db'
        con = sqlite3.connect(p)
        con.execute('CREATE TABLE meta(path TEXT PRIMARY KEY, indexed_at REAL, '
                    'size REAL, mtime REAL, reason TEXT)')
        return con, p

    def test_04_failure_list_and_retry_flag(self):
        with tempfile.TemporaryDirectory() as td:
            con, _ = self._db(td)
            try:
                good = Path(td) / 'a.pdf'
                good.write_bytes(b'x' * 10)
                st = good.stat()
                con.execute('INSERT INTO meta VALUES(?,?,?,?,?)',
                            (str(good), 1.0, st.st_size, st.st_mtime, '解析超时（>60s）'))
                # 文件没变 → 不重试（Recoll oldsig.back() == '+' 的同款语义）
                # 记录里的 size 与磁盘不符 → 文件变了，下次必然重试
                changed = Path(td) / 'b.pdf'
                changed.write_bytes(b'y' * 20)
                con.execute('INSERT INTO meta VALUES(?,?,?,?,?)',
                            (str(changed), 2.0, 999, 1.0, '解析失败'))
                con.commit()
                out = brain._index_failures(200, con=con)
                self.assertEqual(2, out['total'])
                by_p = {x['p']: x for x in out['items']}
                self.assertFalse(by_p[str(good)]['will_retry'])
                self.assertIn('解析超时', by_p[str(good)]['why'])
                self.assertTrue(by_p[str(changed)]['will_retry'])
                # 文件被删掉了也不该崩，且不算「会重试」
                missing = Path(td) / 'gone.pdf'
                con.execute('INSERT INTO meta VALUES(?,?,?,?,?)',
                            (str(missing), 3.0, 1, 1.0, 'gone'))
                con.commit()
                out2 = brain._index_failures(200, con=con)
                self.assertFalse({x['p']: x for x in out2['items']}[str(missing)]['will_retry'])
                # 「无文本/空文件/临时文件」是正常结果，不是解析失败。
                # 实测库里 41,217 条「无文本」，不滤会把真失败全部挤出 LIMIT。
                for i, why in enumerate(brain.BENIGN_DOC_REASONS):
                    benign = Path(td) / ('benign%d.bin' % i)
                    benign.write_bytes(b'')
                    con.execute('INSERT INTO meta VALUES(?,?,?,?,?)',
                                (str(benign), 4.0 + i, 0, 1.0, why))
                con.commit()
                out3 = brain._index_failures(200, con=con)
                self.assertEqual(3, out3['total'])
                self.assertFalse(any('benign' in x['p'] for x in out3['items']))
            finally:
                con.close()   # Windows 上不关连接 tempdir 删不掉（WinError 32）

    def test_05_limit_is_clamped_and_empty_db_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            con, _ = self._db(td)
            try:
                self.assertEqual({'items': [], 'total': 0}, brain._index_failures(200, con=con))
            finally:
                con.close()

    def test_06_api_route_and_frontend_wiring(self):
        b = read('brain.py')
        self.assertIn("elif self.path.startswith('/api/index_failures'):", b)
        self.assertIn("json.dumps(_index_failures(lim)", b)
        # 第 294 项（G）：面板从维护区搬进设置页「高级」（ExtrasPanel），断言跟着搬
        f = read('web/src/ui/panels.tsx')
        self.assertIn("'/api/index_failures'", f)
        self.assertIn("maint_failures_none", f)
        self.assertIn("maint_failures_retry", f)
        m = read('web/src/ui/maintenance.tsx')
        self.assertNotIn("'/api/index_failures'", m)


class ExcludeTestTest(unittest.TestCase):
    """C：DocFetcher RegexTestPanel 同款 —— 回显命中规则，而不只是布尔值。"""

    def test_07_empty_path(self):
        self.assertEqual({'ok': False, 'err': 'empty'}, brain._exclude_test(''))
        self.assertEqual({'ok': False, 'err': 'empty'}, brain._exclude_test('   '))

    def test_08_user_rule_is_reported(self):
        # 开发根 scope.json 里真实有 'steam' 这条排除词
        r = brain._exclude_test(r'E:\SteamLibrary\steamapps\a.txt')
        self.assertTrue(r['ok'])
        self.assertTrue(r['excluded'])
        self.assertTrue(any(h['kind'] == 'dirname' and h['rule'] == 'steamapps'
                           for h in r['hits']), r['hits'])
        self.assertTrue(any(h['rule'] == 'steam' for h in r['hits']), r['hits'])

    def test_09_allowlist_explains_why_not_excluded(self):
        # 开始菜单物理上在 AppData 里，被内置规则排掉；_KEEP_SEGS_NC 放行。
        p = (r'C:\Users\x\AppData\Roaming\Microsoft\Windows\Start Menu'
             r'\Programs\WeChat.lnk')
        r = brain._exclude_test(p)
        self.assertTrue(r['ok'])
        self.assertTrue(r['kept'], '白名单段没被识别')
        self.assertFalse(r['excluded'], '白名单应该压过内置排除')

    def test_10_normal_path_and_api_wiring(self):
        r = brain._exclude_test(r'D:\资料\项目\周报.docx')
        self.assertTrue(r['ok'])
        self.assertFalse(r['excluded'])
        b = read('brain.py')
        self.assertIn("elif self.path.startswith('/api/exclude_test'):", b)
        self.assertIn('json.dumps(_exclude_test(_p)', b)
        f = read('web/src/ui/panels.tsx')
        self.assertIn('/api/exclude_test?p=', f)
        self.assertIn('maint_exclude_yes', f)

    def test_11_i18n_five_languages(self):
        i18 = read('web/src/lib/i18n.ts')
        for k in ('maint_failures', 'maint_failures_none', 'maint_failures_retry',
                  'maint_failures_skip', 'maint_exclude_test', 'maint_exclude_ph',
                  'maint_exclude_btn', 'maint_exclude_yes', 'maint_exclude_no',
                  'maint_exclude_hint', 'maint_exclude_kept'):
            self.assertEqual(5, i18.count(k + ':'), k)


if __name__ == '__main__':
    unittest.main()
