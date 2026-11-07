# -*- coding: utf-8 -*-
"""正文近实时（_BODYQ / _body_apply / _body_need）的单元测试（第 114 项）。

以前正文只有「手动按范围更新」一条路；watcher 只做文件名层。这次补的正文重索引
队列必须锁住这些行为：去重、未变文件跳过、变更重建、删除清库、Office 锁文件不收。

隔离：brain.DB 指到 tempfile 临时库；extract_text_safe 用 mock 替身，绝不真解析
PDF/OCR，也不碰真实 brain.db / 8765 实例。
跑法：python -m unittest discover -s tests -p "test_*.py"
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402


class BodyReindexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sb-test-body-')
        self.dir = Path(self.tmp.name)
        self._old_dir = list(brain._DATA_DIR)
        self._old_db = brain.DB
        os.environ['SB_NO_MAINT'] = '1'
        os.environ['SB_NO_WATCH'] = '1'
        brain._DATA_DIR[:] = [self.dir]
        brain.DB = self.dir / 'brain.db'
        brain._BODYQ.clear()
        brain._BODYQ_DEL.clear()
        for k in ('pending', 'done', 'last', 'dropped'):
            brain._BODYSTAT[k] = 0 if k != 'last' else ''
        with mock.patch.object(brain, 'extract_text_safe',
                               lambda p: ('微信 聊天 记录 正文', None)):
            con = brain.init_db()
        con.close()

    def tearDown(self):
        brain.DB = self._old_db
        brain._DATA_DIR[:] = self._old_dir
        brain._BODYQ.clear()
        brain._BODYQ_DEL.clear()
        # 不用 tmp.cleanup()：断言失败时连接没来得及关，WinError 32 会把真失败盖掉
        shutil.rmtree(self.tmp.name, ignore_errors=True)

    # ── 队列侧 ────────────────────────────────────────────────

    def test_offer_dedup_and_body_ext_filter(self):
        """同文件重复事件天然去重；非文档类型不进正文队列。"""
        brain._bodyq_offer(
            [('D:\\w\\a.pdf', 0, 0, 0, 1.0, 100.0, None, None, None),
             ('D:\\w\\a.pdf', 0, 0, 0, 1.0, 200.0, None, None, None),   # 后到覆盖 mtime
             ('D:\\w\\movie.mp4', 0, 0, 0, 1.0, 100.0, None, None, None)],
            [])
        self.assertEqual(list(brain._BODYQ), [os.path.normcase('D:\\w\\a.pdf')])
        self.assertEqual(brain._BODYQ[os.path.normcase('D:\\w\\a.pdf')][1], 200.0)
        self.assertEqual(brain._BODYSTAT['pending'], 1)

    def test_offer_delete_wins_over_modify(self):
        """先改后删的竞态：以删除收尾，队列里不许留残件。"""
        brain._bodyq_offer([('D:\\w\\a.txt', 0, 0, 0, 1.0, 100.0, None, None, None)], [])
        brain._bodyq_offer([], ['D:\\w\\a.txt'])
        self.assertEqual(brain._BODYQ, {})
        self.assertIn(os.path.normcase('D:\\w\\a.txt'), brain._BODYQ_DEL)

    def test_offer_recreate_cancels_delete(self):
        """删了又建回来：撤掉删除，按变更处理。"""
        brain._bodyq_offer([], ['D:\\w\\a.txt'])
        brain._bodyq_offer([('D:\\w\\a.txt', 0, 0, 0, 1.0, 100.0, None, None, None)], [])
        self.assertEqual(brain._BODYQ_DEL, set())
        self.assertEqual(len(brain._BODYQ), 1)

    def test_cap_drops_new_events(self):
        """洪水上限：满了丢新事件并计数，不许把内存吃掉。"""
        brain._BODYQ_CAP_TEST = None
        old_cap = brain._BODYQ_CAP
        try:
            brain._BODYQ_CAP = 3
            rows = [('D:\\w\\f%d.txt' % i, 0, 0, 0, 1.0, 100.0 + i, None, None, None)
                    for i in range(5)]
            brain._bodyq_offer(rows, [])
            self.assertEqual(len(brain._BODYQ), 3)
            self.assertEqual(brain._BODYSTAT['dropped'], 2)
        finally:
            brain._BODYQ_CAP = old_cap

    # ── 判定与写库 ────────────────────────────────────────────

    def _mkfile(self, name, body='第一版正文'):
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding='utf-8')
        return str(p)

    def test_body_need_new_changed_unchanged(self):
        con = brain.init_db()
        p = self._mkfile('a.txt')
        st = os.stat(p)
        self.assertTrue(brain._body_need(con, p, st))          # 新文件
        con.execute('INSERT OR REPLACE INTO meta(path, indexed_at, size, mtime, reason) '
                    'VALUES(?,?,?,?,NULL)', (p, time.time(), st.st_size, st.st_mtime))
        con.commit()
        self.assertFalse(brain._body_need(con, p, st))          # 没变
        time.sleep(0.05)
        Path(p).write_text('第二版正文', encoding='utf-8')
        # mtime 容差 ±2s（与 index() 的 _needs 一致）：把修改时间拨到 10s 后才算「真的改过」
        t = time.time() + 10
        os.utime(p, (t, t))
        self.assertTrue(brain._body_need(con, p, os.stat(p)))   # 改过
        con.close()

    def test_body_apply_indexes_and_rereads(self):
        """入队 → 建 docs+meta；文件改了 → 重抽更新。"""
        p = self._mkfile('doc.txt')
        st = os.stat(p)
        brain._BODYQ[os.path.normcase(p)] = (p, st.st_mtime)
        with mock.patch.object(brain, 'extract_text_safe',
                               lambda pth: ('第一版 正文内容', None)):
            brain._body_apply(list(brain._BODYQ.values()), [])
        con = brain.init_db()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 1)
        self.assertIsNone(con.execute('SELECT reason FROM meta').fetchone()[0])
        con.close()
        # 改文件再跑一遍：正文跟着变（_needs 判定为需要重建；mtime 要跳出 ±2s 容差）
        time.sleep(0.05)
        Path(p).write_text('第二版 正文内容', encoding='utf-8')
        t2 = time.time() + 10
        os.utime(p, (t2, t2))
        st2 = os.stat(p)
        brain._BODYQ[os.path.normcase(p)] = (p, st2.st_mtime)
        with mock.patch.object(brain, 'extract_text_safe',
                               lambda pth: ('第二版 正文内容', None)):
            brain._body_apply(list(brain._BODYQ.values()), [])
        con = brain.init_db()
        body = con.execute('SELECT body FROM docs').fetchone()[0]
        self.assertIn('第二版', body)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 1)  # 不许重复行
        con.close()

    def test_body_apply_failure_records_reason(self):
        """抽取失败写 meta.reason（与 index() 的归因约定一致），不留半截 docs 行。"""
        p = self._mkfile('bad.txt')
        st = os.stat(p)
        brain._BODYQ[os.path.normcase(p)] = (p, st.st_mtime)
        with mock.patch.object(brain, 'extract_text_safe',
                               lambda pth: (None, '解析超时')):
            brain._body_apply(list(brain._BODYQ.values()), [])
        con = brain.init_db()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 0)
        self.assertEqual(con.execute('SELECT reason FROM meta').fetchone()[0], '解析超时')
        con.close()

    def test_body_apply_delete_cleans_docs_and_meta(self):
        """删除事件：docs 和 meta 一起清（单文件事件是权威事实，不走 prune 刹车）。"""
        p = self._mkfile('gone.txt')
        st = os.stat(p)
        with mock.patch.object(brain, 'extract_text_safe',
                               lambda pth: ('要被删的正文', None)):
            brain._body_apply([(p, st.st_mtime)], [])
        con = brain.init_db()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 1)
        con.close()
        brain._body_apply([], [os.path.normcase(p)])
        con = brain.init_db()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 0)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM meta').fetchone()[0], 0)
        con.close()

    def test_body_apply_skips_lock_files_and_vanished(self):
        """~$ Office 锁文件不收；事件晚于文件消失就静默跳过。"""
        lock = str(self.dir / '~$缓存.docx')
        Path(lock).write_text('x', encoding='utf-8')
        ghost = str(self.dir / '不存在的文档.pdf')       # 已经没了
        with mock.patch.object(brain, 'extract_text_safe') as m:
            brain._body_apply([(lock, 1.0), (ghost, 1.0)], [])
        m.assert_not_called()
        con = brain.init_db()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 0)
        con.close()

    def test_body_exts_match_extractor(self):
        """正文队列的类型集合要和 _extract_text_impl 支持集对齐（抽查两边）。"""
        for e in ('.pdf', '.docx', '.doc', '.txt', '.md', '.xlsx', '.epub', '.rtf'):
            self.assertIn(e, brain.BODY_EXTS)
        for e in ('.mp4', '.jpg', '.exe', '.zip', '.py'):
            self.assertNotIn(e, brain.BODY_EXTS)


if __name__ == '__main__':
    unittest.main()
