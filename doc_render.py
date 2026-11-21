# -*- coding: utf-8 -*-
"""doc_render.py — 把 Office 文档转成 PDF，好在详情栏里当页图看（第 385 项）。

**为什么必须借 Office/WPS**：
    用户的 docx 大量来自 WPS 模板，正文全塞在文本框里（document.xml 里 `w:pict` 74 处、
    `v:shape` 126 处、`w:txbxContent` 100 处）。纯前端渲染器（docx-preview）只认
    `w:p`/`w:tbl`，遇到这种文档整页**空白** —— 实测截图就是这样。
    要真画出文本框、图表、SmartArt、公式，只能让真正的排版引擎来排，然后把 PDF 渲成页图。

**做法**：
    1. 按「路径 + mtime + size」算缓存键，转好的 PDF 落在 `<数据目录>/preview_cache/`；
    2. COM 依次尝试 MS Office 与 WPS（本机实测只有 WPS 的 `Kwps.Application` 能起）；
    3. 转换放在子线程里跑，超时就放弃（绝不把 HTTP 线程拖死，也绝不把 WPS 卡在那里当僵尸）；
    4. 拿不到引擎（mac/linux，或者机器上没装 Office/WPS）就返回 None，前端回落到纯文本。

只做「转 PDF」这一件事；出页图仍复用 brain.make_thumb 的 PDF 分支（页码、高亮都现成）。
"""

import hashlib
import os
import shutil
import threading
import time

# 扩展名 → 用哪个引擎
OFFICE_PDF = {
    '.doc': 'word', '.docx': 'word', '.rtf': 'word', '.wps': 'word', '.wpt': 'word',
    '.xls': 'excel', '.xlsx': 'excel', '.et': 'excel', '.ett': 'excel', '.csv': None,
    '.ppt': 'ppt', '.pptx': 'ppt', '.dps': 'ppt', '.dpt': 'ppt',
}

# 引擎候选顺序：先真 Office，再 WPS（两个都装时 Office 的排版更接近「原样」）
PROGIDS = {
    'word': ('Word.Application', 'Kwps.Application'),
    'excel': ('Excel.Application', 'Ket.Application'),
    'ppt': ('PowerPoint.Application', 'Kwpp.Application'),
}

PPT_SAVE_PDF = 32       # ppSaveAsPDF
WD_FORMAT_PDF = 17      # wdFormatPDF
XL_TYPE_PDF = 0         # xlTypePDF

CONVERT_TIMEOUT = 25.0  # 秒；超时这条请求就先放弃（结果会落在磁盘缓存，下次直接命中）
_CVT_LOCK = threading.Lock()      # 同一时刻只允许一个转换（两个 Office 进程抢焦点很容易弹窗）
_FAILED = {}                      # 路径 → (时间, 原因)，失败冷却 5 分钟
FAIL_COOLDOWN = 300.0


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path or '')))


def is_office(path):
    return os.path.splitext(str(path or ''))[1].lower() in OFFICE_PDF


def cache_dir(base):
    d = os.path.join(str(base), 'preview_cache')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:                    # noqa: BLE001
        return None
    return d


def cache_path(path, base):
    """转换结果的落盘位置。「mtime+size」进键 → 文件改了自动换新 PDF，不用手动清缓存。"""
    d = cache_dir(base)
    if not d:
        return None
    try:
        st = os.stat(path)
        stamp = '%d:%d' % (int(st.st_mtime), st.st_size)
    except OSError:
        stamp = 'na'
    h = hashlib.sha1(('%s|%s' % (_norm(path), stamp)).encode('utf-8')).hexdigest()
    return os.path.join(d, h + '.pdf')


def _sweep(base, keep=200, max_age_days=7):
    """缓存自清：太久没人看、或者文件数太多时，删最老的那批（转换产物，删了能再转）。"""
    d = cache_dir(base)
    if not d:
        return
    try:
        items = [(os.path.getmtime(os.path.join(d, f)), os.path.join(d, f))
                 for f in os.listdir(d) if f.lower().endswith('.pdf')]
    except OSError:
        return
    items.sort()
    cutoff = time.time() - max_age_days * 86400
    victims = [p for (t, p) in items if t < cutoff]
    extra = len(items) - keep
    if extra > 0:
        victims += [p for (_t, p) in items[:extra] if p not in victims]
    for p in victims:
        try:
            os.remove(p)
        except OSError:
            pass


def _co():
    """COM 每个线程都要自己 CoInitialize，主线程的不算数。"""
    try:
        import pythoncom
        pythoncom.CoInitialize()
        return pythoncom
    except Exception:                    # noqa: BLE001
        return None


def _convert_word(app, src, out):
    app.Visible = False
    try:
        app.DisplayAlerts = 0
    except Exception:                    # noqa: BLE001
        pass
    doc = app.Documents.Open(src, ReadOnly=True, AddToRecentFiles=False)
    try:
        doc.SaveAs(out, FileFormat=WD_FORMAT_PDF)
    finally:
        doc.Close(False)


def _convert_excel(app, src, out):
    try:
        app.Visible = False
    except Exception:                    # noqa: BLE001
        pass
    try:
        app.DisplayAlerts = False
    except Exception:                    # noqa: BLE001
        pass
    wb = app.Workbooks.Open(src, ReadOnly=True, UpdateLinks=0)
    try:
        wb.ExportAsFixedFormat(XL_TYPE_PDF, out)
    finally:
        wb.Close(False)


def _convert_ppt(app, src, out):
    try:
        app.Visible = False           # PowerPoint 一般不允许隐藏，包一层
    except Exception:                 # noqa: BLE001
        pass
    pres = app.Presentations.Open(src, ReadOnly=True, WithWindow=False)
    try:
        pres.SaveAs(out, PPT_SAVE_PDF)
    finally:
        pres.Close()


_CONVERTERS = {'word': _convert_word, 'excel': _convert_excel, 'ppt': _convert_ppt}


def _run_convert(kind, src, out, box):
    """子线程里真干活：echo 结果到 box['ok']。"""
    import win32com.client as client
    for prog in PROGIDS[kind]:
        app = None
        try:
            app = client.DispatchEx(prog)
            _CONVERTERS[kind](app, src, out)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                box['ok'] = True
                box['engine'] = prog
                return
        except Exception as e:           # noqa: BLE001
            box['err'] = '%s: %s' % (prog, str(e)[:160])
        finally:
            if app is not None:
                try:
                    app.Quit()
                except Exception:        # noqa: BLE001
                    pass


def pdf_for(path, base):
    """Office 文档 → PDF 路径（带缓存）。拿不到引擎 / 转换失败返回 None。

    注意：调用方必须保证 `path` 是本地已索引文件（这个模块不做权限判断）。
    """
    if not path or not os.path.exists(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return path
    if ext not in OFFICE_PDF or not OFFICE_PDF[ext]:
        return None

    out = cache_path(path, base)
    if not out:
        return None
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out

    key = _norm(path)
    fail = _FAILED.get(key)
    if fail and time.time() - fail[0] < FAIL_COOLDOWN:
        return None

    if not _CVT_LOCK.acquire(timeout=CONVERT_TIMEOUT):
        return None                       # 前面那单还没完，这次先不给（前端会重试）
    try:
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out                        # 排队期间别人已经转好了
        box = {}
        kind = OFFICE_PDF[ext]

        def _target():
            _co()
            try:
                _run_convert(kind, path, out, box)
            except Exception as e:            # noqa: BLE001
                box['err'] = str(e)[:160]

        th = threading.Thread(target=_target, daemon=True)
        th.start()
        th.join(CONVERT_TIMEOUT)
        if th.is_alive():
            box['err'] = '转换超时（%ds）' % int(CONVERT_TIMEOUT)
        if box.get('ok'):
            _sweep(base)
            return out
        _FAILED[key] = (time.time(), box.get('err') or 'unknown')
        try:
            import traceback
            print('[i] 文档转 PDF 失败：%s → %s' % (os.path.basename(path), box.get('err')), flush=True)
            traceback.print_exc()
        except Exception:                     # noqa: BLE001
            pass
        return None
    finally:
        _CVT_LOCK.release()


def clear_failure(path=None):
    """单测/排障用：把失败冷却清掉。"""
    if path is None:
        _FAILED.clear()
    else:
        _FAILED.pop(_norm(path), None)


def cache_stats(base):
    d = cache_dir(base)
    if not d:
        return {'files': 0, 'bytes': 0}
    n = b = 0
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.isfile(p):
            n += 1
            try:
                b += os.path.getsize(p)
            except OSError:
                pass
    return {'files': n, 'bytes': b}


def cleanup(base):
    """设置页「清理预览缓存」用。"""
    d = cache_dir(base)
    if not d:
        return 0
    n = 0
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.isfile(p):
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
    return n
