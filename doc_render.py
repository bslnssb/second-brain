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
import re
import shutil
import subprocess
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
WD_EXPORT_FROM_TO = 3   # wdExportFromTo：只导一段页

# 第 388 项：详情栏只看得到第 1 页，没必要等整份导完。
# 实测 180 页的 docx：整份导出 6.8s，只导前 8 页 1.06s（打开 + 数页数才 0.7s）。
# 所以先导「头一份」（HEAD_PAGES 页）让首屏先出来，整份在后台接着转。
HEAD_PAGES = 8

APP_IDLE = 120.0        # 引擎实例闲置这么久就 Quit（不让一个后台 WPS 常驻吃内存）
MAX_QUEUE = 3           # 队列只留最新 3 个任务
FAIL_COOLDOWN = 60.0    # 真转换失败（两次都报错）后的冷却；「还在排队」不算失败

# 第 391 项：一份文档能把 WPS 的导出**永久卡住**（实测 19MB 手册让 wps.exe 100% CPU 跑
# 500 秒不返回）。以前转换是单线程 inline 干活，一卡就把整个队列堵死 —— 之后点任何文档
# 都只显示「正在排版转换」，永远不出来（用户截图就是这个）。
# 现在单次转换超过这个时间就认定卡死：杀掉的引擎进程、记失败（冷却长一点免得反复烧）、
# 队列继续往下走。
CONV_TIMEOUT = 25.0
TIMEOUT_COOLDOWN = 300.0   # 卡死过的文件 5 分钟内不再试（不然每点一次就烧 25 秒 CPU）

# 第 391 项之二：受保护的文档（WPS「工具箱授权浏览」目录里那种）导出来的 PDF 里
# 文字是乱码，`ComputeStatistics` 还会回一个离谱的页数（实测 19MB 那份回 101560），
# 前端翻页器就成了「1 / 101560」。页数超过这个上限一律当「这份预览不了」处理：
# 不重试、长冷却，前端直接提示「点打开看原件」。
MAX_SANE_PAGES = 5000

# 第 394 项：用户要求「在这台电脑上感觉不到卡」。转换本身降了优先级（第 392 项），
# 但还有一笔浪费：在结果列表里连按上下键，每换一行前端就要一次页图 → 每份都排一次转换。
# 所以任务入队后先「稳」这么久才准开工 —— 停下来的那一份才值得起一次 WPS（起引擎 3~4 秒）。
SETTLE_DELAY = 0.35

# 我们自己的引擎可能是 WPS 假装的 Word（错误信息里写着 Kingsoft WPS），所以进程名都认
ENGINE_EXES = ('wps.exe', 'winword.exe', 'et.exe', 'excel.exe', 'wpp.exe', 'powerpnt.exe')

WORKER_AUTOSTART = True # 单测把它关掉，避免在测试机上真起 WPS

_FAILED = {}            # key -> (时间, 原因, 冷却秒数)
_APPS = {}              # kind -> {'app':..., 'last':..., 'prog':..., 'pids':()}
_TASKS = []             # [(key, path, out, kind, base)]，从尾部取（最新优先）
_INFLIGHT = set()       # 已排队 / 正在转的 key
_ENQ = {}               # key -> 入队时间（第 394 项：稳 350ms 才开工）
_LOCK = threading.Lock()
_WORKER = None
_CO_INIT = threading.local()


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path or '')))


def is_office(path):
    return os.path.splitext(str(path or ''))[1].lower() in OFFICE_PDF


class _Unsupported(Exception):
    """这份文档注定预览不了（受保护/加密、页数离谱）：不换引擎重试，直接判失败。"""


def _fail_cool(key):
    """这份文件现在是不是还在失败冷却里（卡死的用更长的冷却）。"""
    f = _FAILED.get(key)
    if not f:
        return False
    cool = f[2] if len(f) > 2 else FAIL_COOLDOWN
    return time.time() - f[0] < cool


def _lower_priority(pids):
    """把引擎进程降到「低于正常」优先级：后台重排版不许跟用户抢核（第 392 项）。

    BELOW_NORMAL 而不是 IDLE：IDLE 在机器一忙时会几乎分不到时间片，转换会拖得很久；
    BELOW_NORMAL 只在有别的活要 CPU 时让路，空闲时照样全速。子进程（WPS 开文档时起的
    那个窗口进程）默认继承父进程的优先级，所以只要在「打开文档之前」降一次就够。
    """
    if not pids:
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        PROC_SET_INFORMATION = 0x0200
        BELOW_NORMAL = 0x00004000
        done = 0
        for pid in list(pids):
            try:
                h = k32.OpenProcess(PROC_SET_INFORMATION, False, int(pid))
            except Exception:            # noqa: BLE001
                continue
            if not h:
                continue
            try:
                if k32.SetPriorityClass(h, BELOW_NORMAL):
                    done += 1
            finally:
                k32.CloseHandle(h)
        return done
    except Exception:                    # noqa: BLE001
        return 0


def _engine_procs(kind):
    """这个 kind 现在名下的引擎进程：实例记下的 + 它起来之后新出现的（开文档时才生）。"""
    ent = _APPS.get(kind) or {}
    since = ent.get('spawn_ts')
    pids = set(ent.get('pids') or ())
    pids |= _engine_pids(since) if since else set()
    return pids


def _engine_pids(since=None):
    """当前 WPS / Office 引擎进程的 pid（可选只算 `since` 之后新起的）。

    不依赖 psutil（打包环境里不一定有它）：EnumProcesses + OpenProcess +
    QueryFullProcessImageNameW + GetProcessTimes，全是 kernel32/psapi 自带。
    拿不准就返回空集合 —— 宁可杀不掉，也不能误杀用户自己的 WPS。
    """
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi.dll')
        arr = (wintypes.DWORD * 4096)()
        need = wintypes.DWORD(0)
        if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(need)):
            return set()
        out = set()
        count = min(need.value // ctypes.sizeof(wintypes.DWORD), 4096)
        for i in range(count):
            pid = arr[i]
            if not pid:
                continue
            handle = k32.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                continue
            try:
                buf = ctypes.create_unicode_buffer(600)
                size = wintypes.DWORD(600)
                if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    continue
                if buf.value.rsplit('\\', 1)[-1].lower() not in ENGINE_EXES:
                    continue
                if since is None:
                    out.add(int(pid))
                    continue
                ct, et, kt, ut = (wintypes.FILETIME(), wintypes.FILETIME(),
                                  wintypes.FILETIME(), wintypes.FILETIME())
                if not k32.GetProcessTimes(handle, ctypes.byref(ct), ctypes.byref(et),
                                           ctypes.byref(kt), ctypes.byref(ut)):
                    continue
                born = (ct.dwHighDateTime << 32 | ct.dwLowDateTime) / 1e7 - 11644473600.0
                if born >= since - 1.0:
                    out.add(int(pid))
            finally:
                k32.CloseHandle(handle)
        return out
    except Exception:                    # noqa: BLE001
        return set()


def _kill_engine(kind):
    """卡死时把这批引擎进程强杀（连带子进程）。

    绝不 `app.Quit()`：引擎正卡在导出里，Quit 也是排队，根本回不来。
    只杀「这个实例起的时候之后新出现的」进程，用户自己开着的 WPS 不动。
    """
    with _LOCK:
        ent = _APPS.pop(kind, None)
    if not ent:
        return []
    pids = set(ent.get('pids') or ())
    if ent.get('spawn_ts'):
        # WPS 是多进程的：真正干活的那个子进程是「打开文档时」才生的，不在建实例时
        # 记的 pids 里。所以杀的时候再把「这个实例起来之后新出现的引擎进程」一起算上。
        # 窗口只有几十秒，且这个实例本来就是我们 DispatchEx 起的，正常情况下不会误伤
        # 用户自己的 WPS。
        pids |= _engine_pids(ent['spawn_ts'])
    killed = []
    for pid in sorted(pids):
        try:
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=0x08000000, timeout=15)
            killed.append(pid)
        except Exception:                # noqa: BLE001
            pass
    return killed


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


def _side_paths(path, base):
    """(整份 PDF, 头一份 PDF, 元数据 json)，三者同一 sha1 前缀。"""
    full = cache_path(path, base)
    if not full:
        return None, None, None
    stem = full[:-4]
    return full, stem + '.head.pdf', stem + '.meta.json'


def _read_meta(path, base):
    """读 {pages, head, from, hit, q}。

    pages = 整份页数；head = 现有 PDF 里包含几页（0 表示就是整份）；
    from = 这几页从原始文档的第几页开始（1 基）；hit = 关键词命中的原始页码；
    q = 算这个 hit 时用的查询词（换了词要重算）。
    """
    try:
        _full, _head, meta = _side_paths(path, base)
        if not meta or not os.path.exists(meta):
            return None
        import json
        with open(meta, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return {'pages': int(d.get('pages') or 0), 'head': int(d.get('head') or 0),
                'from': int(d.get('from') or 1), 'hit': int(d.get('hit') or 0),
                'q': str(d.get('q') or '')}
    except Exception:                    # noqa: BLE001
        return None


def _write_meta(path, base, pages, head, frm=1, hit=0, q=''):
    try:
        _full, _head, meta = _side_paths(path, base)
        if not meta:
            return
        import json
        tmp = meta + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'pages': int(pages or 0), 'head': int(head or 0),
                       'from': int(frm or 1), 'hit': int(hit or 0), 'q': str(q or '')}, f)
        os.replace(tmp, meta)
    except Exception:                    # noqa: BLE001
        pass



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


def _q_tokens(q):
    """查询词 → 可用于 Word Find 的词（去掉 ext:/size:/dm:/content: 这些语法）。

    注意兼容「ext: docx」这种冒号后带空格写法：`ext:` 后面的那个值是语法值，
    不是要搜的字，一并跳掉。
    """
    out = []
    skip_next = False
    for t in re.split(r'[\s"]+', str(q or '')):
        t = t.strip()
        if not t:
            continue
        was, skip_next = skip_next, False
        if t.startswith('-'):
            continue
        if re.match(r'(?i)^(ext|type|size|dm|content|内容|c):$', t):
            skip_next = True
            continue
        if was:
            continue
        if re.match(r'(?i)^(ext|type|size|dm|content|内容|c):', t):
            continue
        if t.endswith(('/', '\\')):
            continue
        if t not in out:
            out.append(t)
        if len(out) >= 8:
            break
    return out


def _find_page(doc, q):
    """在 Word 里找关键词落在第几页（1 基）——不导整份也能定位，实测 0.01s/词。

    只用 `doc.Content.Find`（正文层）；文本框/形状里的字 Word Find 搜不到，
    搜不到就返回 0，退回「导前几页」的老行为，不至于让首屏空着。
    """
    for tok in _q_tokens(q):
        try:
            rng = doc.Content
            f = rng.Find
            f.ClearFormatting()
            f.Text = tok
            f.Forward = True
            f.Wrap = 1                    # wdFindContinue
            f.MatchCase = False
            f.MatchAllWordForms = False
            if f.Execute():
                pg = int(rng.Information(3) or 0)   # wdActiveEndPageNumber
                if pg > 0:
                    return pg
        except Exception:                 # noqa: BLE001
            continue
    return 0


def _convert_word(app, src, out, box):
    app.Visible = False
    try:
        app.DisplayAlerts = 0
    except Exception:                    # noqa: BLE001
        pass
    doc = app.Documents.Open(src, ReadOnly=True, AddToRecentFiles=False)
    try:
        total = 0
        try:
            total = int(doc.ComputeStatistics(2))     # wdStatisticPages（不导出也能数）
        except Exception:                # noqa: BLE001
            total = 0
        if total > MAX_SANE_PAGES:
            # 受保护的文档这里会回天量页数（101560 页），而且导出来是乱码：
            # 与其排 25 秒白烧 CPU 再给用户一张废纸，不如直接判「预览不了」
            raise _Unsupported('页数异常(%d)，疑似受保护/加密文档' % total)
        box['pages'] = total
        if box.get('mode') == 'head' and total > HEAD_PAGES:
            # 第 390 项：用户要看的是**命中那一页**，不是第 1 页。
            # 先在 Word 里定位命中页（不导出也能算页码），只导它前后各一页；
            # 搜不到（文本框/形状里的字）才退回「导前 8 页」。
            hit = _find_page(doc, box.get('q') or '')
            frm = max(1, hit - 1) if hit else 1
            to = min(total, frm + 2) if hit else min(total, HEAD_PAGES)
            if not hit:
                frm, to = 1, min(total, HEAD_PAGES)
            doc.ExportAsFixedFormat(out, WD_FORMAT_PDF, False, 0,
                                    WD_EXPORT_FROM_TO, frm, to)
            box['head_pages'] = max(0, to - frm + 1)
            box['from'] = frm
            box['hit'] = hit
        else:
            doc.ExportAsFixedFormat(out, WD_FORMAT_PDF)
            box['head_pages'] = total if box.get('mode') == 'head' else 0
            box['from'] = 1
            box['hit'] = 0
    finally:
        try:
            doc.Close(False)
        except Exception:                # noqa: BLE001
            # 受保护的文档连 Close 都会报错（真机上是 `Open.Close`）；吞掉它，
            # 别把上面那句「这份预览不了」的原因盖掉
            pass


def _convert_excel(app, src, out, box):
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


def _convert_ppt(app, src, out, box):
    try:
        app.Visible = False           # PowerPoint 一般不允许隐藏，包一层
    except Exception:                 # noqa: BLE001
        pass
    pres = app.Presentations.Open(src, ReadOnly=True, WithWindow=False)
    try:
        box['pages'] = int(getattr(pres.Slides, 'Count', 0) or 0)
        pres.SaveAs(out, PPT_SAVE_PDF)
        box['head_pages'] = 0
    finally:
        pres.Close()


_CONVERTERS = {'word': _convert_word, 'excel': _convert_excel, 'ppt': _convert_ppt}


def _drop_app(kind):
    """把某个引擎实例扔掉（转换报错后换一个干净的，避免坏状态一直传下去）。

    第 391 项之二：`Quit()` 本身是 COM 调用，引擎半死的时候它也会一起卡住
    （真机上就在这儿把转换线程吊住，看着像「队列又不干活了」）。所以只做一件事：
    把它交给一条一次性线程去退，转换线程立刻走人。
    """
    with _LOCK:
        ent = _APPS.pop(kind, None)
    if not ent:
        return

    def bye():
        try:
            ent['app'].Quit()
        except Exception:                # noqa: BLE001
            pass

    threading.Thread(target=bye, name='doc-quit', daemon=True).start()


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
            spawn_ts = time.time()
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
                _APPS[kind] = {'app': app, 'last': time.time(), 'prog': prog,
                               # 记下「这次新起了哪些引擎进程」：卡死时只杀这些
                               'pids': tuple(sorted(_engine_pids(spawn_ts))),
                               'spawn_ts': spawn_ts}
            # 第 392 项：引擎一出生就降优先级（子进程会继承），后台排版别抢用户的核
            _lower_priority(_engine_procs(kind))
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
        if box.get('_abort'):            # 看门狗已经放弃了这一份，别再起新引擎
            return False
        app = _app_for(kind)
        if app is None:
            box['err'] = '没有可用的 Office/WPS 引擎'
            return False
        try:
            _lower_priority(_engine_procs(kind))   # 每次开工前再降一次，别让它回到正常优先级
            _CONVERTERS[kind](app, src, out, box)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                box['engine'] = _APPS.get(kind, {}).get('prog')
                return True
            box['err'] = '转换产物为空'
        except _Unsupported as e:        # 注定预览不了：别再换引擎白试一遍
            box['err'] = str(e)[:160]
            box['unsupported'] = True
            return False
        except Exception as e:            # noqa: BLE001
            box['err'] = str(e)[:160]
        if box.get('_abort'):
            return False
        # 这一份没转成：把实例扔掉，下一轮换个干净的（坏状态不留给下一份文件）
        _drop_app(kind)
    return False


def _priority_watch(kind, stop):
    """转换期间盯着引擎进程，新冒出来的也一起降到 BELOW_NORMAL（第 392 项）。

    真机实测：WPS 打开文档时才起的那个子进程**不继承**我们的优先级，回来又是 NORMAL，
    转换的后半段照旧抢占 CPU。所以边转边盯，1 秒看一次，转换结束就收手。
    """
    while not stop.wait(0.9):
        try:
            _lower_priority(_engine_procs(kind))
        except Exception:                # noqa: BLE001
            return


def _run_convert(kind, path, out, box):
    """带看门狗地转一份：超过 CONV_TIMEOUT 就认定引擎卡死，杀掉它、放弃这一份。

    COM 调用本身没法「取消」，所以用一条工作线程 + join 超时来兜：
    join 超时后给 box 打 `_abort`（那条被丢下的线程下一次就退出，不会再起新引擎），
    然后强杀我们自己的引擎进程（卡住的 RPC 会立刻失败，线程也就散了）。
    """
    res = {}
    stop_prio = threading.Event()
    threading.Thread(target=_priority_watch, args=(kind, stop_prio),
                     name='doc-prio', daemon=True).start()

    def work():
        try:
            res['ok'] = _convert_one(kind, path, out, box)
        except Exception as e:            # noqa: BLE001
            box['err'] = str(e)[:160]
            res['ok'] = False

    try:
        th = threading.Thread(target=work, name='doc-conv', daemon=True)
        th.start()
        th.join(CONV_TIMEOUT)
        if th.is_alive():
            box['_abort'] = True
            box['timeout'] = True
            box['err'] = '转换超时（>%d 秒，引擎卡住，已回收）' % int(CONV_TIMEOUT)
            _kill_engine(kind)
            try:
                os.remove(out)               # 别留半截 PDF 被 _ready 当成「已转好」
            except OSError:
                pass
            return False
        return bool(res.get('ok'))
    finally:
        stop_prio.set()


def _fail_cooldown(box):
    """这一份失败后冷却多久：卡死 / 受保护的 5 分钟，普通报错 1 分钟。"""
    if box.get('timeout') or box.get('unsupported'):
        return TIMEOUT_COOLDOWN
    return FAIL_COOLDOWN


def _settled(task):
    """这一份在队列里待够 SETTLE_DELAY 了吗（第 394 项）。"""
    try:
        return time.time() - _ENQ.get(task[0], 0.0) >= SETTLE_DELAY
    except Exception:                    # noqa: BLE001
        return True


def _loop():
    _co()
    while True:
        with _LOCK:
            task = _TASKS[-1] if _TASKS else None
        if task is None:
            _reap_apps()
            time.sleep(0.25)
            continue
        if not _settled(task):
            # 第 394 项：刚排上来的先让它「稳一稳」，用户在列表里连按上下键时
            # 只有停下来的那一份才值得起一次 WPS（起引擎就要 3~4 秒）
            time.sleep(0.05)
            continue
        with _LOCK:
            if _TASKS and _TASKS[-1] is task:
                _TASKS.pop()
                _ENQ.pop(task[0], None)
            else:                        # 这一份已经被更新的挤掉了
                continue
        key, path, out, kind, base, mode, q = task
        qn = ' '.join(_q_tokens(q))
        box = {'mode': mode, 'q': qn}
        try:
            ok = _run_convert(kind, path, out, box)
        except Exception as e:             # noqa: BLE001
            ok = False
            box['err'] = str(e)[:160]
        with _LOCK:
            _INFLIGHT.discard(key)
        if ok:
            pages = int(box.get('pages') or 0)
            head_pages = int(box.get('head_pages') or 0)
            frm = int(box.get('from') or 1)
            hit = int(box.get('hit') or 0)
            # 第 397 项：Word 文本框 / PPT / Excel 用 Find 定不出页 → 拿导出的 PDF
            # 再扫一遍文本层（便宜），把「命中页」补上；否则详情栏永远停在第 1 页，
            # 页图上自然也一个高亮都没有。
            if not hit and _q_tokens(q):
                idx = _pdf_first_hit(out, _q_tokens(q))
                if idx:
                    hit = (frm + idx - 1) if mode == 'head' else idx
            if mode == 'head':
                _finish_head(path, base, out, kind, pages, head_pages, frm, hit, qn)
            else:
                _write_meta(path, base, pages, 0, 1, hit, qn)
            _sweep(base)
        else:
            # 卡死过 / 受保护文档冷却久一点（5 分钟）：不然用户每点一次就白烧 25 秒 CPU
            cool = _fail_cooldown(box)
            _FAILED[_norm(path)] = (time.time(), box.get('err') or 'unknown', cool)
            try:
                print('[i] 文档转 PDF 失败：%s → %s' % (os.path.basename(path), box.get('err')),
                      flush=True)
            except Exception:              # noqa: BLE001
                pass


def _finish_head(path, base, out, kind, pages, head_pages, frm=1, hit=0, q=''):
    """头一份转完。

    - 这段已经覆盖整份（短文档）：直接复制成整份，不再导第二遍；
    - 长文档：**只留命中页那一段**。用户要看的是命中那一页，想看整篇会自己去打开文件，
      所以不再自动排「整份」（那是 6~15 秒纯白烧）；真翻到段外再由 pdf_page_source 按需补。
    """
    full, _head, _meta = _side_paths(path, base)
    if pages and frm == 1 and head_pages >= pages:
        try:
            shutil.copyfile(out, full)
        except Exception:                # noqa: BLE001
            pass
        _write_meta(path, base, pages, 0, 1, hit, q)
        return
    _write_meta(path, base, pages, head_pages or HEAD_PAGES, frm, hit, q)


def _ensure_worker():
    global _WORKER
    if not WORKER_AUTOSTART:
        return
    if _WORKER is not None and _WORKER.is_alive():
        return
    _WORKER = threading.Thread(target=_loop, name='doc-render', daemon=True)
    _WORKER.start()


def _enqueue(key, path, out, kind, base, mode, q=''):
    """排一个转换任务。同一个 key 只排一次；队列只留最新的 MAX_QUEUE 个。"""
    # 先把 worker 拉起来：以前只在排新任务之后才 ensure，worker 万一半路死掉、
    # 而 _INFLIGHT 里还有旧条目，就会一直走「已在排队」这条早退路，永远没人干活
    _ensure_worker()
    with _LOCK:
        if key in _INFLIGHT:
            # 已经在排队、还没开工的，就把查询词换成最新的（前端先按旧 q 排上、
            # pdfmeta 后带着真查询词到的顺序很常见），别让命中页定位用错词
            if q:
                for i, t in enumerate(_TASKS):
                    if t[0] == key and t[6] != q:
                        _TASKS[i] = t[:6] + (q,)
            return
        _INFLIGHT.add(key)
        _ENQ[key] = time.time()          # 第 394 项：从这一刻开始算「稳」
        _TASKS.append((key, path, out, kind, base, mode, q))
        while len(_TASKS) > MAX_QUEUE:
            old = _TASKS.pop(0)
            _INFLIGHT.discard(old[0])
            _ENQ.pop(old[0], None)
    _ensure_worker()


def _ready(path, base):
    """(整份在手? , 头一份在手?)"""
    full, head, _meta = _side_paths(path, base)
    ok_full = bool(full and os.path.exists(full) and os.path.getsize(full) > 0)
    ok_head = bool(head and os.path.exists(head) and os.path.getsize(head) > 0)
    return ok_full, ok_head


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
    ok_full, ok_head = _ready(path, base)
    if ok_full or ok_head:
        return 'ok'
    key = _norm(path)
    with _LOCK:
        if key + '|head' in _INFLIGHT or key + '|full' in _INFLIGHT:
            return 'pending'
    if _fail_cool(key):
        return 'failed'
    return 'none'


def pdf_page_cached(path, base, pg=0, q=''):
    """只吃现成缓存：返回 (含第 pg 页的 PDF, 段内下标)，没有就 (None, None)。

    第 392 项：网格视图给 docx/pptx 出缩略图，会为滚动过的每一份文档排一次转换 ——
    实测这是最隐形的一笔 CPU（一屏 20 个格子 = 20 次重排版）。网格只值一个 32px 的图，
    所以网格这一路**只允许读缓存**，回来再补；详情栏（big=1）才允许排队。
    """
    if not path or not os.path.exists(path):
        return None, None
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return path, int(pg)
    if ext not in OFFICE_PDF or not OFFICE_PDF[ext]:
        return None, None
    full, head, _meta = _side_paths(path, base)
    if not full:
        return None, None
    try:
        if os.path.exists(full) and os.path.getsize(full) > 0:
            return full, int(pg)
        if os.path.exists(head) and os.path.getsize(head) > 0:
            meta = _read_meta(path, base) or {}
            frm = int(meta.get('from') or 1)
            cnt = int(meta.get('head') or 0)
            if cnt and frm - 1 <= int(pg) <= frm - 1 + cnt - 1:
                return head, int(pg) - (frm - 1)
    except OSError:
        pass
    return None, None


def pdf_page_source(path, base, pg=0, q=''):
    """要原始文档第 pg 页（0 基）→ (含这一页的 PDF, 在 PDF 里的下标)；没就绪就排队并返回 (None, None)。

    第 390 项：现在缓存里可能只有「命中页 ±1」那一段（`from` 起、`head` 页），
    所以光给路径不够 —— 调用方要知道这一页在 PDF 里是第几个，否则渲出来是别的页。
    段外才按需补整份（不再在转换结束时自动导整份）。
    """
    if not path or not os.path.exists(path):
        return None, None
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return path, int(pg)
    if ext not in OFFICE_PDF or not OFFICE_PDF[ext]:
        return None, None

    full, head, _meta = _side_paths(path, base)
    if not full:
        return None, None
    ok_full, ok_head = _ready(path, base)
    qn = ' '.join(_q_tokens(q))
    key = _norm(path)
    kind = OFFICE_PDF[ext]
    if ok_full:
        # 整份在手（任何页都能渲），但 meta 里没有「这个词命中第几页」——老缓存、
        # 或者上次是别的关键词。这时候还是得跑一趟 Word：只定位、顺带导那三页，
        # 否则前端拿不到 hit，永远停在第 1 页（真机就是这么踩到的）。
        meta = _read_meta(path, base)
        if qn and (meta or {}).get('q', '') != qn:
            _enqueue(key + '|head', path, head, kind, base, 'head', q)
        return full, int(pg)

    if _fail_cool(key):
        return None, None

    meta = _read_meta(path, base) or {'pages': 0, 'head': 0, 'from': 1}
    frm = meta.get('from') or 1
    cnt = meta.get('head') or 0
    if ok_head:
        # 缓存里的这一段是按**旧查询词**定位的：词一变就重导这一段（1~3 秒），
        # 否则永远停在错的那几页。重导期间先把手上有的页照旧给出去（别白屏），
        # 新段落地后 meta 的 q 就对上了，前端会跳到命中页。
        if qn != (meta.get('q') or ''):
            _enqueue(key + '|head', path, head, kind, base, 'head', q)
        if cnt and frm - 1 <= pg <= frm - 1 + cnt - 1:
            return head, int(pg) - (frm - 1)
        # 段外：翻页才补整份。**首页（pg=0）例外**——那多半是前端「还不知道命中页，
        # 先按第 1 页要图」的那一下；为此把整份导一遍就是纯白烧（180 页 7 秒），
        # 而 meta 一两秒内就会把 hit 页告诉前端，跳过去就行。
        if pg <= 0:
            return None, None
        _enqueue(key + '|full', path, full, kind, base, 'full')
        return None, None

    # 还没有任何产物：word 先导「命中页那一段」，其它格式本来就不支持分段
    if kind == 'word':
        _enqueue(key + '|head', path, head, kind, base, 'head', q)
    else:
        _enqueue(key + '|full', path, full, kind, base, 'full')
    return None, None


def pdf_for(path, base, pg=0, q=''):
    """兼容旧调用：只要路径（要渲染就改用 pdf_page_source 拿下标）。"""
    return pdf_page_source(path, base, pg, q)[0]


def total_pages(path, base, q=''):
    """Office 文档的整份页数（拿不到就 0，并顺手把命中页那一段排上）。

    第 388 项：页数在转换时用 ComputeStatistics 记进 meta.json，翻页器不用等整份导完。
    """
    if not path or not os.path.exists(path):
        return 0
    ext = os.path.splitext(path)[1].lower()
    if ext not in OFFICE_PDF or not OFFICE_PDF[ext]:
        return 0
    meta = _read_meta(path, base)
    if meta and meta.get('pages'):
        n = int(meta['pages'] or 0)
        return n if n <= MAX_SANE_PAGES else 0     # 老缓存里的鬼页数一律不认
    pdf_page_source(path, base, 0, q)          # 没转就排上，前端会重试
    meta = _read_meta(path, base)
    if not meta:
        return 0
    n = int(meta.get('pages') or 0)
    return n if n <= MAX_SANE_PAGES else 0


def _pdf_first_hit(pdf_path, tokens, max_pages=300):
    """在**已经导出**的 PDF 里找第一个命中页（1 基，0=没找到）。

    第 397 项：Word 的 `Content.Find` 搜不到文本框/形状里的字，PPT/Excel 干脆没有
    Find 这一步 —— 结果是 meta 里 hit 永远是 0，详情栏停在第 1 页，用户明明搜到了
    却看不到任何标记。导出的 PDF 本身有文本层，扫它既准又便宜（PDFium 只读不解码）。
    """
    toks = [t.lower() for t in tokens]
    if not toks or not pdf_path or not os.path.exists(pdf_path):
        return 0
    try:
        import pdf_engine as pymupdf
        with pymupdf.open(pdf_path) as doc:
            for i in range(min(len(doc), int(max_pages or 300))):
                try:
                    txt = (doc[i].get_text() or '').lower()
                except Exception:                 # noqa: BLE001
                    continue
                if any(t in txt for t in toks):
                    return i + 1
    except Exception:                             # noqa: BLE001
        return 0
    return 0


def hit_info(path, base, q=''):
    """(关键词命中的原始页码, 是否已按当前查询词算过)。

    `done=False` 表示 meta 里的 hit/段是按**别的查询词**（或者还是旧版本缓存）算的，
    前端据此决定「再等一会儿，转换正在按新词重定位」。
    """
    if not path or not os.path.exists(path):
        return 0, False
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return 0, True
    if ext not in OFFICE_PDF or not OFFICE_PDF[ext]:
        return 0, True
    meta = _read_meta(path, base)
    if not meta:
        return 0, False
    if (meta.get('q') or '') != ' '.join(_q_tokens(q)):
        return 0, False
    return int(meta.get('hit') or 0), True


def hit_page(path, base, q=''):
    """关键词命中的原始页码（1 基，0=没命中/不知道）。

    第 390 项：详情栏要直接停在命中页。meta 里记着算 hit 时用的查询词，
    查询词换了就不再拿旧 hit 糊弄（宁可停在第一段，也不跳错页）。
    """
    return hit_info(path, base, q)[0]



def wait_for(path, base, timeout=30.0, q=''):
    """排障 / 自测用：把这一份转完再返回（生产路径不用它，请求线程不许等）。"""
    out = pdf_for(path, base, 0, q)
    if out:
        return out
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.2)
        if not os.path.exists(path):
            return None
        out2 = pdf_for(path, base, 0, q)
        if out2:
            return out2
        if status_for(path, base) == 'failed':
            return None
    return None


def clear_failure(path=None):
    """单测/排障用：把失败冷却清掉。"""
    if path is None:
        _FAILED.clear()
        _ENQ.clear()
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
