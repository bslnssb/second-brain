# -*- coding: utf-8 -*-
"""doc_render.py — 把 Office 文档转成 PDF，好在详情栏里当页图看（第 385 项）。

**为什么必须借 Office/WPS**：
    用户的 docx 大量来自 WPS 模板，正文全塞在文本框里（document.xml 里 `w:pict` 74 处、
    `v:shape` 126 处、`w:txbxContent` 100 处）。纯前端渲染器（docx-preview）只认
    `w:p`/`w:tbl`，遇到这种文档整页**空白** —— 实测截图就是这样。
    要真画出文本框、图表、SmartArt、公式，只能让真正的排版引擎来排，然后把 PDF 渲成页图。

**做法（第 387 项重做：后台排队 + 复用引擎）**：
    第 385 项最初是「请求线程里同步等转换，25s 超时」，实测踩了两个坑：
      1. 57MB 的 `0面试题拓展知识.docx` 25s 转不完 → 超时后没收掉后台线程，
         锁却被放掉，前端一重试又起一个 WPS —— 实例越堆越多，整机卡死；
      2. 每份文档都 `DispatchEx` 新起一个 WPS，光启动就 0.8s（实测 1.1s vs 复用 0.3s）。
    现在改成：
      * **一次只跑一个后台 worker**，请求线程只「排队 + 立刻返回 None」，绝不阻塞 HTTP；
      * 队列**只留最新 3 个**（用户在列表里连点，只值得转他现在看的那几个）；
      * **复用同一个引擎实例**（闲置 120s 才 Quit），同一文件不会重复排队；
      * 转好落盘缓存，前端下次请求（或几秒后自动重试）直接命中。
"""

import hashlib
import os
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

APP_IDLE = 120.0        # 引擎实例闲置这么久就 Quit（不让一个后台 WPS 常驻吃内存）
MAX_QUEUE = 3           # 队列只留最新 3 个任务
FAIL_COOLDOWN = 60.0    # 真转换失败（两次都报错）后的冷却；「还在排队」不算失败

WORKER_AUTOSTART = True # 单测把它关掉，避免在测试机上真起 WPS

_FAILED = {}            # key -> (时间, 原因)
_APPS = {}              # kind -> {'app':..., 'last':..., 'prog':...}
_TASKS = []             # [(key, path, out, kind, base)]，从尾部取（最新优先）
_INFLIGHT = set()       # 已排队 / 正在转的 key
_LOCK = threading.Lock()
_WORKER = None
_CO_INIT = threading.local()


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
    """COM 每个线程都要自己 CoInitialize；同一线程只做一次。"""
    if getattr(_CO_INIT, 'done', False):
        return True
    try:
        import pythoncom
        pythoncom.CoInitialize()
        _CO_INIT.done = True
        return True
    except Exception:                    # noqa: BLE001
        return False


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


def _drop_app(kind):
    """把某个引擎实例扔掉（转换报错后换一个干净的，避免坏状态一直传下去）。"""
    with _LOCK:
        ent = _APPS.pop(kind, None)
    if not ent:
        return
    try:
        ent['app'].Quit()
    except Exception:                    # noqa: BLE001
        pass


def _reap_apps():
    """闲置太久的引擎实例主动 Quit，别留个后台 WPS 常驻。"""
    now = time.time()
    for kind, ent in list(_APPS.items()):
        if now - ent['last'] > APP_IDLE:
            _drop_app(kind)


def _app_for(kind):
    """拿一个引擎实例：优先复用，没有就新建（先 MS Office，再 WPS）。"""
    ent = _APPS.get(kind)
    if ent:
        ent['last'] = time.time()
        return ent['app']
    import win32com.client as client
    for prog in PROGIDS[kind]:
        try:
            app = client.DispatchEx(prog)
            try:
                app.Visible = False
            except Exception:             # noqa: BLE001
                pass
            try:
                app.DisplayAlerts = 0
            except Exception:             # noqa: BLE001
                pass
            with _LOCK:
                _APPS[kind] = {'app': app, 'last': time.time(), 'prog': prog}
            return app
        except Exception:                 # noqa: BLE001
            continue
    return None


def _convert_one(kind, src, out, box):
    """真干活。返回 True 表示 out 已经写好。

    第 387 项补：复用的引擎实例可能被外部关掉（实测报 `RPC 服务器不可用`，-2147023174）。
    那种错误是「这一份没转成」而不是「这台机器不能转」，所以先扔掉旧实例、拿一个全新的
    再试一次，两次都失败才算真失败 —— 否则用户那份文档就被 5 分钟冷却按死了。
    """
    for _attempt in (0, 1):
        app = _app_for(kind)
        if app is None:
            box['err'] = '没有可用的 Office/WPS 引擎'
            return False
        try:
            _CONVERTERS[kind](app, src, out)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                box['engine'] = _APPS.get(kind, {}).get('prog')
                return True
            box['err'] = '转换产物为空'
        except Exception as e:            # noqa: BLE001
            box['err'] = str(e)[:160]
        # 这一份没转成：把实例扔掉，下一轮换个干净的（坏状态不留给下一份文件）
        _drop_app(kind)
    return False


def _loop():
    _co()
    while True:
        with _LOCK:
            task = _TASKS.pop() if _TASKS else None
        if task is None:
            _reap_apps()
            time.sleep(0.25)
            continue
        key, path, out, kind, base = task
        box = {}
        try:
            ok = _convert_one(kind, path, out, box)
        except Exception as e:             # noqa: BLE001
            ok = False
            box['err'] = str(e)[:160]
        with _LOCK:
            _INFLIGHT.discard(key)
        if ok:
            _sweep(base)
        else:
            _FAILED[key] = (time.time(), box.get('err') or 'unknown')
            try:
                print('[i] 文档转 PDF 失败：%s → %s' % (os.path.basename(path), box.get('err')),
                      flush=True)
            except Exception:              # noqa: BLE001
                pass


def _ensure_worker():
    global _WORKER
    if not WORKER_AUTOSTART:
        return
    if _WORKER is not None and _WORKER.is_alive():
        return
    _WORKER = threading.Thread(target=_loop, name='doc-render', daemon=True)
    _WORKER.start()


def _enqueue(key, path, out, kind, base):
    """排一个转换任务。同一文件只排一次；队列只留最新的 MAX_QUEUE 个。"""
    with _LOCK:
        if key in _INFLIGHT:
            return
        _INFLIGHT.add(key)
        _TASKS.append((key, path, out, kind, base))
        while len(_TASKS) > MAX_QUEUE:
            old = _TASKS.pop(0)
            _INFLIGHT.discard(old[0])
    _ensure_worker()


def status_for(path, base):
    """给调用方判断「现在到底什么情况」：ok / pending / failed / none。

    pending = 已排队或正在转，**不是失败**，逐帧重试就行（前端就是靠这个自动重试的）。
    """
    if not path or not os.path.exists(path):
        return 'none'
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return 'ok'
    if ext not in OFFICE_PDF or not OFFICE_PDF[ext]:
        return 'none'
    out = cache_path(path, base)
    if out and os.path.exists(out) and os.path.getsize(out) > 0:
        return 'ok'
    key = _norm(path)
    with _LOCK:
        if key in _INFLIGHT:
            return 'pending'
    fail = _FAILED.get(key)
    if fail and time.time() - fail[0] < FAIL_COOLDOWN:
        return 'failed'
    return 'none'


def pdf_for(path, base):
    """Office 文档 → PDF 路径（带缓存）。

    命中缓存直接给路径；没命中就**排进后台队列并立刻返回 None**（绝不阻塞请求线程）。
    前端看到 None 会隔一两秒再要一次，转好了就命中。
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

    _enqueue(key, path, out, OFFICE_PDF[ext], base)
    # 排完队再看一眼：可能刚好被别处转完了（极小概率），顺手给路径
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    return None


def wait_for(path, base, timeout=30.0):
    """排障 / 自测用：把这一份转完再返回（生产路径不用它，请求线程不许等）。"""
    out = pdf_for(path, base)
    if out:
        return out
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.2)
        if not os.path.exists(path):
            return None
        out2 = cache_path(path, base)
        if out2 and os.path.exists(out2) and os.path.getsize(out2) > 0:
            return out2
        if status_for(path, base) == 'failed':
            return None
    return None


def clear_failure(path=None):
    """单测/排障用：把失败冷却清掉。"""
    if path is None:
        _FAILED.clear()
    else:
        _FAILED.pop(_norm(path), None)


def queue_len():
    with _LOCK:
        return len(_TASKS), len(_INFLIGHT)


def shutdown_apps():
    """退出程序时把复用的引擎实例收掉（deploy / 测试用）。"""
    for kind in list(_APPS):
        _drop_app(kind)


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
