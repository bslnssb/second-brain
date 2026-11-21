# -*- coding: utf-8 -*-
"""第 339 项：剪贴板里「复制了一个图片文件」会显示成「1 个项目 + 📁」，用户看不出那是图片。
现在给它补缩略图，并按图片显示（badge「图片 w×h」），「图片」这一栏也能筛到。"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import clipboard_mgr
from PIL import Image


class TestClipFileThumb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='memex_clip339_')
        self.db = os.path.join(self.tmp, 'clip.db')
        self.thumbs = Path(self.tmp) / 'thumbs'
        self.thumbs.mkdir(parents=True, exist_ok=True)
        clipboard_mgr.set_db_path(self.db)
        clipboard_mgr._THUMB_DIR = self.thumbs
        clipboard_mgr._LAST_HASH = None
        clipboard_mgr.init_db()
        # 一张真图片文件，模拟「在资源管理器里 Ctrl+C 一张 png」
        self.img = Path(self.tmp) / 'snip.png'
        Image.new('RGB', (248, 503), (200, 30, 30)).save(str(self.img), format='PNG')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_has_thumb(self):
        con = sqlite3.connect(self.db)
        cols = {r[1] for r in con.execute('PRAGMA table_info(clipboard_history)')}
        con.close()
        self.assertIn('thumb', cols)

    def test_image_file_gets_thumb_and_image_badge(self):
        fn, dim = clipboard_mgr.file_thumb(str(self.img))
        self.assertTrue(fn and fn.endswith('.png'))
        self.assertTrue((self.thumbs / fn).is_file())
        self.assertEqual('248×503', dim)
        clipboard_mgr.record_clip('file', json.dumps([str(self.img)]), 'snip.png', dim, 'h339', thumb=fn)
        row = clipboard_mgr.list_history(kind='all')[0]
        self.assertEqual('file', row['kind'])
        self.assertEqual(fn, row['thumb'])
        self.assertEqual('snip.png', row['preview'])
        self.assertTrue(row['detail'].startswith('图片'))

    def test_non_image_file_stays_item_count(self):
        txt = Path(self.tmp) / 'a.txt'
        txt.write_text('x', encoding='utf-8')
        self.assertEqual((None, ''), clipboard_mgr.file_thumb(str(txt)))
        clipboard_mgr.record_clip('file', json.dumps([str(txt)]), 'a.txt', '1 个项目', 'h339b')
        row = clipboard_mgr.list_history(kind='all')[0]
        self.assertEqual('', row['thumb'])
        self.assertEqual('1 个项目', row['detail'])

    def test_image_tab_includes_image_file(self):
        fn, dim = clipboard_mgr.file_thumb(str(self.img))
        clipboard_mgr.record_clip('file', json.dumps([str(self.img)]), 'snip.png', dim, 'h339c', thumb=fn)
        self.assertEqual(1, len(clipboard_mgr.list_history(kind='image')))

    def test_backfill_old_rows(self):
        # 模拟改造前的老条目：没有 thumb、detail 还是「1 个项目」
        con = sqlite3.connect(self.db)
        con.execute('DELETE FROM clipboard_history')
        con.execute("""INSERT INTO clipboard_history (kind, content, preview, detail, hash, created_at, pinned)
                       VALUES ('file', ?, 'snip.png', '1 个项目', 'old339', 1, 0)""",
                    (json.dumps([str(self.img)]),))
        con.commit()
        con.close()
        rows = clipboard_mgr.list_history(kind='all')
        self.assertTrue(rows[0]['thumb'])
        self.assertTrue(rows[0]['detail'].startswith('图片'))


if __name__ == '__main__':
    unittest.main()
