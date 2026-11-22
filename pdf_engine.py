# -*- coding: utf-8 -*-
"""pdf_engine.py — 用 pypdfium2 顶掉 PyMuPDF（第 352 项）。

**为什么要换**：PyMuPDF 是 AGPL-3.0，闭源卖钱的软件用它必须买 Artifex 商业授权；
pypdfium2 是 Apache-2.0 封装 Google 的 PDFium（BSD-3），可以随商业软件随便分发，
而且 pymupdf 那 38MB 的二进制也跟着没了。

**怎么做**：业务代码里真正用到 PyMuPDF 的地方就 5 处（OCR 位图渲染、总页数、加密归因、
详情栏缩略图 + 高亮坐标、Pro 的词数统计），且正文抽取本来就走 pypdf。这里只实现那一小撮
用的 API（open / needs_pass / authenticate / page_count / len / doc[i] / 遍历 / page.rect /
get_pixmap / search_for / get_text），调用方 `import pdf_engine as pymupdf` 即原样可用。

坐标口径：PyMuPDF 的 Rect 是左上原点 y 向下，PDFium 的 charbox 是左下原点，这里统一换算。
"""
import io

import pypdfium2 as pdfium

__all__ = ['open', 'Matrix', 'Rect', 'Pixmap', 'Page', 'Doc']


class Matrix:
    """只用到缩放：PyMuPDF 的 Matrix(z, z)。"""

    __slots__ = ('scale',)

    def __init__(self, a=1.0, b=None, *_rest, **_kw):
        try:
            self.scale = float(a)
        except Exception:
            self.scale = 1.0


class Rect:
    """左上原点、y 向下，跟 PyMuPDF 一致。"""

    __slots__ = ('x0', 'y0', 'x1', 'y1')

    def __init__(self, x0=0.0, y0=0.0, x1=0.0, y1=0.0):
        self.x0, self.y0, self.x1, self.y1 = float(x0), float(y0), float(x1), float(y1)

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    def __repr__(self):
        return 'Rect(%.2f, %.2f, %.2f, %.2f)' % (self.x0, self.y0, self.x1, self.y1)


class Pixmap:
    """包一层 PIL：tobytes('png') / samples / width / height / n。"""

    __slots__ = ('_im', 'width', 'height', 'n')

    def __init__(self, im):
        self._im = im
        self.width, self.height = im.size
        self.n = len(im.getbands())

    @property
    def samples(self):
        return self._im.tobytes()

    def tobytes(self, fmt='png'):
        buf = io.BytesIO()
        self._im.save(buf, format='PNG' if str(fmt).lower() == 'png' else str(fmt).upper())
        return buf.getvalue()

    def save(self, path):
        self._im.save(str(path))

    def to_pil(self):
        return self._im


class Page:
    def __init__(self, pdf_page, doc):
        self._p = pdf_page
        self._doc = doc
        self._tp = None
        self._closed = False

    # ---- 几何 ----
    @property
    def rect(self):
        try:
            return Rect(0.0, 0.0, self._p.get_width(), self._p.get_height())
        except Exception:
            return Rect(0.0, 0.0, 1.0, 1.0)

    # ---- 文字 ----
    def _textpage(self):
        if self._tp is None:
            self._tp = self._p.get_textpage()
        return self._tp

    def get_text(self, mode='text'):
        try:
            return self._textpage().get_text_range() or ''
        except Exception:
            return ''

    def search_for(self, text, quads=False):
        """返回命中区域的 Rect 列表（左上原点坐标）。

        第 400 项：以前把匹配到的所有字符框并成**一个**矩形 —— PDFium 给的
        (起始字符, 个数) 偶尔跨行（Word 导出的 PDF 里一行就是一个 run），
        一合并就变成覆盖两三行的黄块，看起来像「错误命中」。现在按行分组：
        纵向重叠不足就另起一个矩形，每个矩形只框一行的字。
        """
        needle = str(text or '').strip()
        if not needle:
            return []
        out = []
        try:
            tp = self._textpage()
            h = self.rect.height
            searcher = tp.search(needle)
            try:
                while True:
                    hit = searcher.get_next()
                    if not hit:
                        break
                    idx, cnt = hit
                    cur = None
                    for k in range(idx, idx + max(1, int(cnt))):
                        try:
                            l, b, r, t = tp.get_charbox(k)
                        except Exception:
                            continue
                        if r - l <= 0 and t - b <= 0:
                            continue
                        ty0, ty1 = h - t, h - b
                        if cur is None:
                            cur = [l, ty0, r, ty1]
                            continue
                        ch = max(1.0, ty1 - ty0)
                        overlap = min(ty1, cur[3]) - max(ty0, cur[1])
                        if overlap / min(ch, max(1.0, cur[3] - cur[1])) < 0.5:
                            out.append(Rect(*cur))          # 换行了，先收这一行
                            cur = [l, ty0, r, ty1]
                        else:
                            cur = [min(cur[0], l), min(cur[1], ty0),
                                   max(cur[2], r), max(cur[3], ty1)]
                    if cur is not None:
                        out.append(Rect(*cur))
            finally:
                try:
                    searcher.close()
                except Exception:
                    pass
        except Exception:
            return out
        return out

    # ---- 渲染 ----
    def get_pixmap(self, matrix=None, alpha=False, clip=None, dpi=None, **_kw):
        scale = 1.0
        if matrix is not None:
            scale = getattr(matrix, 'scale', 1.0) or 1.0
        elif dpi:
            scale = float(dpi) / 72.0
        bmp = self._p.render(scale=float(scale))
        try:
            im = bmp.to_pil()
            if not alpha:
                im = im.convert('RGB')
            return Pixmap(im)
        finally:
            try:
                bmp.close()
            except Exception:
                pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._tp is not None:
                self._tp.close()
        except Exception:
            pass
        try:
            self._p.close()
        except Exception:
            pass


class Doc:
    """pymupdf.Document 的最小替身。"""

    def __init__(self, pdf_doc, path, needs_pass=False, password=None):
        self._d = pdf_doc
        self.path = str(path)
        self.needs_pass = bool(needs_pass)
        self._password = password
        self._pages = {}
        self._closed = False

    # ---- 加密 ----
    def authenticate(self, password):
        if not self.needs_pass:
            return True
        try:
            self._d = pdfium.PdfDocument(self.path, password=str(password or ''))
            self.needs_pass = False
            self._password = password
            return True
        except Exception:
            return False

    # ---- 页数 / 取页 ----
    @property
    def page_count(self):
        try:
            return len(self._d)
        except Exception:
            return 0

    def __len__(self):
        return self.page_count

    def __getitem__(self, i):
        i = int(i)
        if i < 0:
            i += self.page_count
        if i < 0 or i >= self.page_count:
            raise IndexError('page out of range')
        pg = self._pages.get(i)
        if pg is None:
            pg = Page(self._d[i], self)
            self._pages[i] = pg
        return pg

    def __iter__(self):
        for i in range(self.page_count):
            yield self[i]

    def get_page_pixmap(self, i, matrix=None, alpha=False):
        return self[i].get_pixmap(matrix=matrix, alpha=alpha)

    # ---- 生命周期 ----
    def close(self):
        if self._closed:
            return
        self._closed = True
        for pg in list(self._pages.values()):
            try:
                pg.close()
            except Exception:
                pass
        self._pages.clear()
        try:
            if self._d is not None:
                self._d.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()
        return False

    def __repr__(self):
        return '<pdf_engine.Doc %r pages=%d>' % (self.path, self.page_count)


def _is_encrypted(path):
    """用 pypdf 判加密（PDFium 只有「密码错」这一个笼统错误，分不出加密和损坏）。"""
    try:
        from pypdf import PdfReader
        return bool(PdfReader(str(path)).is_encrypted)
    except Exception:
        return False


def open(path, password=None):
    """对齐 pymupdf.open 的语义：

    - 正常文件 → Doc；
    - 加密文件（用户密码非空）→ 返回 needs_pass=True 的 Doc，供调用方 authenticate('') 试空密码；
    - 真损坏/读不了 → 抛异常（调用方的 except 分支照旧走「损坏」）。
    """
    try:
        d = (pdfium.PdfDocument(str(path), password=password)
             if password is not None else pdfium.PdfDocument(str(path)))
        return Doc(d, path, needs_pass=False, password=password)
    except Exception as e:
        if _is_encrypted(path):
            return Doc(None, path, needs_pass=True, password=password)
        raise e
