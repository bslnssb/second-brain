# -*- coding: utf-8 -*-
"""第 312 项（详情面板预览）+ 第 313 项（Office 缩略图回滚）的边界。

312 第一版给 Word/Excel/PPT 加了 Shell 缩略图分支，装机后**主窗直接「未响应」**：
前端缩略图视图对每一格（含 docx/xlsx/pptx）都发 `/api/thumb`，Shell 缩略图处理器
在随机线程里调 COM 会挂住，几格就把 6 条 HTTP 线程占死（`/api/ping` 也不回）。

所以这里钉死三件事：

1. `make_thumb` 对 Office 扩展名必须**秒回 None**，不许再走 Shell；
2. 图片仍走 PIL、PDF 仍走 pymupdf（不能被回滚误伤）；
3. `/api/thumb` 注释写清「别再加 Office」，前端详情靠正文首段（`AUTO_PEEK_EXT`）。
"""
import io
import os
import re
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with io.open(os.path.join(ROOT, name), 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def _write_png(path, w=40, h=30, rgb=(30, 120, 220)):
    from PIL import Image
    Image.new('RGB', (w, h), rgb).save(path, format='PNG')


try:
    import brain
    HAS_BRAIN = True
except Exception:            # pragma: no cover
    brain = None
    HAS_BRAIN = False


@unittest.skipUnless(HAS_BRAIN, 'brain.py 起不来（缺依赖）')
class PreviewThumb312(unittest.TestCase):

    def test_no_office_thumb_branch(self):
        """313：make_thumb 里不许再出现 OFFICE_THUMB_EXTS（那是 312 的卡死源头）。"""
        self.assertFalse(hasattr(brain, 'OFFICE_THUMB_EXTS'))
        self.assertFalse(hasattr(brain, 'OFFICE_THUMB_TIMEOUT'))
        seg = _read('brain.py').split('def make_thumb')[1].split('\ndef ')[0]
        self.assertNotIn('OFFICE_THUMB_EXTS', seg)

    def test_office_returns_none_fast(self):
        """docx/xlsx/pptx 必须立刻 None，不能等 Shell 处理器（线程就靠这个不被占死）。"""
        d = tempfile.mkdtemp(prefix='memex313_')
        for ext in ('docx', 'xlsx', 'pptx'):
            p = os.path.join(d, 'a.' + ext)
            with io.open(p, 'wb') as f:
                f.write(b'not a real office file')
            t0 = time.time()
            out = brain.make_thumb(p, 320, 400)
            dt = time.time() - t0
            self.assertIsNone(out, ext)
            self.assertLess(dt, 0.5, '%s 用了 %.2fs，说明又去调 Shell 了' % (ext, dt))

    def test_image_still_takes_pil_branch(self):
        d = tempfile.mkdtemp(prefix='memex312_')
        p = os.path.join(d, 'a.png')
        _write_png(p)
        out = brain.make_thumb(p, 64, 64)
        self.assertTrue(out)
        self.assertTrue(out.startswith(b'\x89PNG'))

    def test_thumb_route_warns_against_office(self):
        src = _read('brain.py')
        seg = src[src.find("self.path.startswith('/api/thumb')"):][:700]
        self.assertIn('313', seg)
        self.assertIn('Office', seg)

    def test_detail_can_ask_for_big_thumb(self):
        """317：详情栏/灯箱要 900×1200 的大图，否则 PDF 首页小字看不清。"""
        src = _read('brain.py')
        self.assertIn("qs.get('big'", src)
        self.assertIn('make_thumb(p, 900, 1200)', src)

    def test_frontend_preview_handles_office_by_text(self):
        src = _read(os.path.join('web', 'src', 'ui', 'panels.tsx'))
        m = re.search(r'const THUMB_IN_DTL = \[(.*?)\]', src, re.S)
        self.assertTrue(m, '详情面板的预览扩展名表不见了')
        body = m.group(1)
        for e in ('png', 'pdf'):
            self.assertIn("'%s'" % e, body)
        # Word/Excel 必须走正文首段，而不是硬画一张图
        self.assertNotIn("'docx'", body)
        m2 = re.search(r'const AUTO_PEEK_EXT = \[(.*?)\]', src, re.S)
        self.assertTrue(m2, '自动读正文的扩展名表不见了')
        for e in ('docx', 'xlsx', 'pptx'):
            self.assertIn("'%s'" % e, m2.group(1))
        self.assertIn('/api/thumb?p=', src)


if __name__ == '__main__':
    unittest.main()
