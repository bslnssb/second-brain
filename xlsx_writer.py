# -*- coding: utf-8 -*-
"""把结果行写成 .xlsx —— 纯标准库，只有 zipfile。

为什么不用 openpyxl：只为一个功能多背十几 MB 依赖、还要在两个 spec 里加
hiddenimports、还要担心打包后的版本漂移，不值。xlsx 本质就是一个 zip 装几个
XML，写起来不到 200 行。这和 `license_core.py` 手写 Ed25519 是同一套判断。

三个刻意的取舍：

1. **字符串用 inlineStr**，省掉 sharedStrings.xml 的索引表。导出量最多几千行，
   去重的收益抵不上多维护一张表 + 多一处出错的地方。
2. **时间写成 "2026-11-07 15:30" 文本**，不写 Excel 日期序列号。ISO 格式按
   字典序排和按时间排结果一样，用户点排序照样对；同时绕开 1900 闰年 bug
   和时区换算 —— 这两样是导出功能最常见的「打开发现日期差一天」。
3. **大小写真数字**，不写 "1.2 MB"。数字才能求和、能排序、能条件格式。

导出的表头冻结 + 自动筛选，几千行也好看。
"""
from __future__ import annotations

import zipfile

# 32KB 上限，超了截断（Excel 单元格上限是 32767 字符，多写一个就整个文件打不开）
_MAX_CELL = 32000


def _esc(s) -> str:
    """XML 转义 + 剔掉 XML 1.0 不允许的控制字符。

    文件名是从磁盘读的，可能带 \\x00-\\x08 这类字符 ——  XML 解析器见到直接
    报错，整个文件就打不开了。这不是理论问题：从旧压缩包解出来的文件名常带。
    """
    s = str(s if s is not None else '')
    if len(s) > _MAX_CELL:
        s = s[:_MAX_CELL]
    s = ''.join(ch for ch in s if ch >= ' ' or ch in '\t\n')
    return (s.replace('&', '&amp;').replace('<', '&lt;')
             .replace('>', '&gt;').replace('"', '&quot;'))


def _col_name(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA。"""
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_name(name: str) -> str:
    """工作表名：≤31 字符，且不能含 []:*?/\\ （Excel 的限制，不是我们的）。"""
    for ch in '[]:*?/\\':
        name = name.replace(ch, '_')
    return (name or 'Sheet1')[:31]


_CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''

_ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''

_WORKBOOK_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

# 两个字体：0 正文、1 表头加粗；cellXfs 里 s="1" 就是加粗
_STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
</fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def _cell(ref: str, v, style: int | None = None) -> str:
    s = ' s="%d"' % style if style else ''
    if isinstance(v, bool):
        v = '是' if v else '否'
    if isinstance(v, (int, float)):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, s, v)
    return ('<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
            % (ref, s, _esc(v)))


def build_xlsx(rows, head=None, sheet='搜索结果', widths=None) -> bytes:
    """rows 是「已经排好列」的二维列表（每格 str/int/float），返回 xlsx 字节。

    故意不接受 dict：列的顺序应该由调用方一眼看清，而不是靠字段名猜。
    """
    head = list(head or [])
    widths = list(widths or [])
    ncol = len(head) or (len(rows[0]) if rows else 1)

    body = []
    r = 1
    if head:
        body.append('<row r="1">%s</row>' % ''.join(
            _cell('%s1' % _col_name(i), h, 1) for i, h in enumerate(head)))
        r = 2
    for row in rows:
        body.append('<row r="%d">%s</row>' % (
            r, ''.join(_cell('%s%d' % (_col_name(i), r), v)
                       for i, v in enumerate(row))))
        r += 1
    last = r - 1

    cols = ''
    if widths:
        cols = '<cols>%s</cols>' % ''.join(
            '<col min="%d" max="%d" width="%g" customWidth="1"/>' % (i + 1, i + 1, w)
            for i, w in enumerate(widths))

    dim = 'A1:%s%d' % (_col_name(max(ncol - 1, 0)), max(last, 1))
    pane = ('<sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
            'activePane="bottomLeft" state="frozen"/></sheetView>') if head else \
           '<sheetView workbookViewId="0"/>'
    filt = ('<autoFilter ref="%s"/>' % dim) if (head and rows) else ''

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="%s"/>'
        '<sheetViews>%s</sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>%s'
        '<sheetData>%s</sheetData>%s'
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        '</worksheet>' % (dim, pane, cols, ''.join(body), filt))

    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>'
          % _esc(_sheet_name(sheet)))

    out = __import__('io').BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', _CONTENT_TYPES)
        z.writestr('_rels/.rels', _ROOT_RELS)
        z.writestr('xl/workbook.xml', wb)
        z.writestr('xl/_rels/workbook.xml.rels', _WORKBOOK_RELS)
        z.writestr('xl/styles.xml', _STYLES)
        z.writestr('xl/worksheets/sheet1.xml', sheet_xml)
    return out.getvalue()


def result_rows(hits, kind_label=None, and_=None):
    """把 /api/files 的结果行转成表格行。

    列：文件名 / 所在文件夹 / 大小 / 修改时间 / 命中位置 / 匹配片段。
    「匹配片段」这一列是给律师和老师用的 —— 导出后能直接看到「逾期一日按万分之三」
    那句话本身，不用再回去一条条点开。
    """
    import os
    import time as _t

    out = []
    for h in hits or []:
        p = str(h.get('p') or '')
        try:
            sz = int(h.get('s') if h.get('s') is not None else h.get('z') or 0)
        except (TypeError, ValueError):
            sz = 0
        m = h.get('m')
        when = ''
        if m:
            try:
                when = _t.strftime('%Y-%m-%d %H:%M', _t.localtime(float(m)))
            except (TypeError, ValueError, OSError):
                when = ''
        snip = (h.get('b') or h.get('snip') or '').replace('\n', ' ').strip()
        where = (kind_label(h) if kind_label else
                 ('正文' if snip else ('文件夹' if h.get('dir') else '文件名')))
        out.append([h.get('n') or os.path.basename(p),
                    os.path.dirname(p) or p,
                    sz, when, where, snip])
    return out


HEAD = ['文件名', '所在文件夹', '大小(字节)', '修改时间', '命中位置', '匹配片段']
WIDTHS = [42, 52, 12, 17, 10, 60]
