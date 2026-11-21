# -*- coding: utf-8 -*-
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from PIL import Image

import clipboard_mgr


class TestClipboardMgr(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='memex_clip_test_')
        self.db_path = Path(self.tmpdir) / 'test_clip.db'
        self.thumb_dir = Path(self.tmpdir) / 'thumbs'
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        clipboard_mgr.set_db_path(self.db_path)
        clipboard_mgr._THUMB_DIR = self.thumb_dir
        clipboard_mgr._LAST_HASH = None
        clipboard_mgr.init_db()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_text_and_query(self):
        clipboard_mgr.record_clip('text', 'Hello World Memex', 'Hello World Memex', '18 字符', 'h1')
        rows = clipboard_mgr.list_history(kind='all')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'text')
        self.assertEqual(rows[0]['preview'], 'Hello World Memex')
        # 读的时候按内容重算字符数（第 281 项：detail 不再用写库时那句中文），
        # 'Hello World Memex' 是 17 个字符，所以这里钉 17 而不是写库时传的 18。
        self.assertEqual(rows[0]['detail'], '17 字符')

    def test_dedup_and_bump(self):
        clipboard_mgr.record_clip('text', 'A', 'A', '1 字符', 'hA')
        t1 = clipboard_mgr.list_history()[0]['created_at']
        time.sleep(0.01)
        # Duplicate record with same hash should bump time
        clipboard_mgr.record_clip('text', 'A', 'A', '1 字符', 'hA')
        rows = clipboard_mgr.list_history()
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0]['created_at'], t1)

    def test_record_image(self):
        im = Image.new('RGB', (100, 50), color=(255, 0, 0))
        fn = clipboard_mgr._save_image_thumb(im, 'imghash1234567890')
        self.assertTrue((self.thumb_dir / fn).is_file())

        clipboard_mgr.record_clip('image', fn, '图片 [100×50]', '100×50', 'imghash1234567890')
        rows = clipboard_mgr.list_history(kind='image')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'image')
        self.assertEqual(rows[0]['detail'], '100×50')

    def test_record_files(self):
        paths = [r'C:\test\a.txt', r'C:\test\b.png']
        content = json.dumps(paths)
        clipboard_mgr.record_clip('file', content, 'a.txt 等 2 个项目', '2 个项目', 'filehash1')
        rows = clipboard_mgr.list_history(kind='file')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'file')
        self.assertIn('2 个项目', rows[0]['detail'])

    def test_search_and_filter(self):
        clipboard_mgr.record_clip('text', 'Python 3.12 知识库', 'Python 3.12 知识库', '14 字符', 'h1')
        clipboard_mgr.record_clip('text', 'TypeScript 极客教程', 'TypeScript 极客教程', '15 字符', 'h2')

        # Filter by query
        res = clipboard_mgr.list_history(q='Python')
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]['preview'], 'Python 3.12 知识库')

        # Filter by kind
        res_img = clipboard_mgr.list_history(kind='image')
        self.assertEqual(len(res_img), 0)

    def test_delete_and_clear(self):
        clipboard_mgr.record_clip('text', 'Item 1', 'Item 1', '6 字符', 'h1')
        clipboard_mgr.record_clip('text', 'Item 2', 'Item 2', '6 字符', 'h2')
        rows = clipboard_mgr.list_history()
        self.assertEqual(len(rows), 2)

        # Delete single item
        first_id = rows[0]['id']
        ok = clipboard_mgr.delete_item(first_id)
        self.assertTrue(ok)
        self.assertEqual(len(clipboard_mgr.list_history()), 1)

        # Clear history
        clipboard_mgr.clear_history()
        self.assertEqual(len(clipboard_mgr.list_history()), 0)

    def test_fav_and_pinned(self):
        clipboard_mgr.record_clip('text', '常用脚本命令', '常用脚本命令', '6 字符', 'h_fav1')
        clipboard_mgr.record_clip('text', '普通临时复制', '普通临时复制', '6 字符', 'h_tmp')
        rows = clipboard_mgr.list_history()
        self.assertEqual(len(rows), 2)
        fav_id = [r['id'] for r in rows if r['content'] == '常用脚本命令'][0]

        # 切换收藏/置顶
        ok, val = clipboard_mgr.toggle_pin(fav_id)
        self.assertTrue(ok)
        self.assertEqual(val, 1)

        # 筛选收藏/fav
        fav_rows = clipboard_mgr.list_history(kind='fav')
        self.assertEqual(len(fav_rows), 1)
        self.assertEqual(fav_rows[0]['content'], '常用脚本命令')
        self.assertTrue(fav_rows[0]['pinned'])

        # 清空普通记录时，收藏项不会被删除
        clipboard_mgr.clear_history()
        rows_after_clear = clipboard_mgr.list_history()
        self.assertEqual(len(rows_after_clear), 1)
        self.assertEqual(rows_after_clear[0]['content'], '常用脚本命令')

        # 取消收藏
        ok, val = clipboard_mgr.toggle_pin(fav_id)
        self.assertTrue(ok)
        self.assertEqual(val, 0)
        self.assertEqual(len(clipboard_mgr.list_history(kind='fav')), 0)

    def test_listener_background_capture(self):
        try:
            import win32clipboard as clip
            import win32con
        except Exception:
            return  # skip on environments without win32clipboard
        
        clipboard_mgr.start_listener()
        time.sleep(0.2)
        self.assertTrue(clipboard_mgr._RUNNING)

        test_msg = f"Unit test auto capture {time.time()}"
        clip.OpenClipboard()
        clip.EmptyClipboard()
        clip.SetClipboardData(win32con.CF_UNICODETEXT, test_msg)
        clip.CloseClipboard()

        # Wait for worker thread debounce (0.08s) + write
        time.sleep(0.3)
        history = clipboard_mgr.list_history()
        clipboard_mgr.stop_listener()
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]['preview'], test_msg)


if __name__ == '__main__':
    unittest.main()
