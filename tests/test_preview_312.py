# -*- coding: utf-8 -*-
"""第 312 项（详情面板预览）+ 第 313 项（Office 缩略图回滚）+ 第 385 项（Office 页图）的边界。

312 第一版给 Word/Excel/PPT 加了 Shell 缩略图分支，装机后**主窗直接「未响应」**：
前端缩略图视图对每一格（含 docx/xlsx/pptx）都发 `/api/thumb`，Shell 缩略图处理器
在随机线程里调 COM 会挂住，几格就把 6 条 HTTP 线程占死（`/api/ping` 也不回）。

所以这里钉死三件事：

1. `make_thumb` 对 Office 扩展名必须**秒回 None**，不许再走 Shell；
2. 图片仍走 PIL、PDF 仍走 pymupdf（不能被回滚误伤）；
3. `/api/thumb` 注释写清「别再加 Office」。

第 385 项把第 1 条收窄了：Office 不再一律 None，而是**只走 doc_render（本机 Office/WPS 转 PDF，
带锁 + 超时 + 磁盘缓存）**；转换拿不到就立刻回 None，绝不许回头去碰 Shell 缩略图处理器。
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

    def test_office_without_engine_returns_none_fast(self):
        """第 385 项改口径：Office 现在**允许出图**，但只在「转换能成」时出。

        这里把 doc_render.pdf_for 打成 None（= 机器上没 Office/WPS），必须立刻回 None，
        绝不能退回去调 Shell 缩略图处理器 —— 那才是 312 卡死的真源头。
        """
        from unittest import mock
        d = tempfile.mkdtemp(prefix='memex313_')
        for ext in ('docx', 'xlsx', 'pptx'):
            p = os.path.join(d, 'a.' + ext)
            with io.open(p, 'wb') as f:
                f.write(b'not a real office file')
            with mock.patch.object(brain.doc_render, 'pdf_for', return_value=None):
                t0 = time.time()
                out = brain.make_thumb(p, 320, 400)
                dt = time.time() - t0
            self.assertIsNone(out, ext)
            self.assertLess(dt, 0.3, '%s 用了 %.2fs，说明绕过了 doc_render 去调别的' % (ext, dt))

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
        # 第 385 项：Word/PPT 出页图（后端先转 PDF）。
        # Excel 不在页图名单里 —— 宽表印成 PDF 只有左上角一小块（实测 10 页 9 页空），
        # 所以 xls/xlsx/et 改走前端单元格表格（INLINE_PRIMARY）。
        for e in ('doc', 'docx', 'ppt', 'pptx'):
            self.assertIn("'%s'" % e, body)
        for e in ('xls', 'xlsx', 'et'):
            self.assertNotIn("'%s'" % e, body)
        # 第 386 项后 INLINE_PRIMARY 直接复用 SHEET_EXT（同一份 Excel 家族名单）
        self.assertIn('const INLINE_PRIMARY = SHEET_EXT', src)
        m3 = re.search(r"const SHEET_EXT = \[(.*?)\]", src, re.S)
        self.assertTrue(m3, 'Excel 走表格的名单不见了')
        for e in ('xls', 'xlsx', 'et'):
            self.assertIn("'%s'" % e, m3.group(1))
        m2 = re.search(r'const AUTO_PEEK_EXT = \[(.*?)\]', src, re.S)
        self.assertTrue(m2, '自动读正文的扩展名表不见了')
        for e in ('docx', 'xlsx', 'pptx'):
            self.assertIn("'%s'" % e, m2.group(1))
        self.assertIn('/api/thumb?p=', src)


if __name__ == '__main__':
    unittest.main()
