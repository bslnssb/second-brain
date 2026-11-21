"""单元测试：snip_ocr 屏幕截图与离线 OCR 识别模块。"""

import os
import sys
import unittest
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import snip_ocr


class TestSnipOcr(unittest.TestCase):
    def test_ocr_image_empty(self):
        res = snip_ocr.ocr_image(None)
        self.assertEqual(res['count'], 0)
        self.assertEqual(res['text'], '')
        self.assertEqual(res['lines'], [])

    def test_ocr_image_synthetic(self):
        # 创建包含文字的测试图
        im = Image.new('RGB', (360, 100), color=(255, 255, 255))
        d = ImageDraw.Draw(im)
        try:
            font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 22)
            d.text((20, 20), '测试截图识别功能', font=font, fill=(0, 0, 0))
        except Exception:
            d.text((20, 20), 'Test Snip OCR 123', fill=(0, 0, 0))

        res = snip_ocr.ocr_image(im)
        self.assertGreaterEqual(res['count'], 1)
        self.assertTrue(len(res['text']) > 0)
        self.assertIn('box', res['lines'][0])
        self.assertIn('score', res['lines'][0])

    def test_snip_overlay_module(self):
        import snip_overlay
        self.assertTrue(hasattr(snip_overlay, 'capture_with_overlay'))
        self.assertTrue(hasattr(snip_overlay, 'HAS_WINFORMS'))

    def test_save_and_thumb(self):
        im = Image.new('RGB', (120, 80), color=(200, 220, 255))
        snip_ocr._process_image(im)

        state = snip_ocr.get_snip_result()
        self.assertTrue(state['has_image'])
        self.assertEqual(state['img_size'], (120, 80))

        thumb = snip_ocr.get_snip_thumb_bytes()
        self.assertIsNotNone(thumb)
        self.assertGreater(len(thumb), 100)

        # 测试另存为
        tmp_dst = ROOT / 'tmp' / 'test_snip_out.png'
        ok, path = snip_ocr.save_snip_image(str(tmp_dst))
        self.assertTrue(ok)
        self.assertTrue(Path(path).is_file())
        try:
            Path(path).unlink()
        except Exception:
            pass

    def test_put_image_to_clipboard_multi_format(self):
        """测试全格式剪贴板写入：同时包含 CF_DIB、PNG 注册格式与 CF_HDROP 文件拖放。"""
        import win32clipboard
        import win32con
        im = Image.new('RGB', (100, 100), color=(120, 160, 200))
        ok = snip_ocr.put_image_to_clipboard(im)
        self.assertTrue(ok)

        png_fmt = win32clipboard.RegisterClipboardFormat('PNG')
        win32clipboard.OpenClipboard()
        try:
            has_dib = win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB)
            has_png = win32clipboard.IsClipboardFormatAvailable(png_fmt)
            has_drop = win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP)
        finally:
            win32clipboard.CloseClipboard()

        self.assertTrue(has_dib, '缺少 CF_DIB 格式')
        self.assertTrue(has_png, '缺少 PNG 格式')
        self.assertTrue(has_drop, '缺少 CF_HDROP 格式')

    def test_read_clipboard_and_ocr_base64(self):
        import base64
        import io
        im = Image.new('RGB', (200, 60), color=(255, 255, 255))
        d = ImageDraw.Draw(im)
        d.text((10, 10), 'Memex Test 888', fill=(0, 0, 0))

        # 测试放入剪贴板再读取
        snip_ocr.put_image_to_clipboard(im)
        cb_res = snip_ocr.read_clipboard_image()
        self.assertTrue(cb_res.get('has_image'), '剪贴板未读取到图片')
        self.assertTrue(cb_res.get('data_url', '').startswith('data:image/png;base64,'))

        # 测试 base64 OCR 识别
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        ocr_res = snip_ocr.ocr_base64_image(f'data:image/png;base64,{b64}')
        self.assertTrue(ocr_res.get('ok'))
        self.assertTrue(len(ocr_res.get('text', '')) > 0)

    def test_prewarm(self):
        snip_ocr.prewarm()
        self.assertIsNotNone(snip_ocr._OCR_ENGINE)


if __name__ == '__main__':
    unittest.main()
