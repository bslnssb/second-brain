#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memex · 桌面窗口启动器

两个窗口：
  快搜窗（/mini）  双击 Ctrl 弹出，一个输入框 + 九行结果，Ctrl+1~9 直接开，Esc 收起
  主窗口（/）      原来的完整界面：正文检索、类型/路径过滤、设置、索引维护

快搜窗按 Listary 的习惯做：无边框、圆角（Win11 的 DWM 圆角）、常驻置顶、
失焦自动收起。主窗口关掉只是隐藏，程序在托盘里等着，这样双击 Ctrl 随时能用。

带参数时走命令行（update / selftest / search …），方便打包成 exe 后复用同一入口。
"""
import ctypes
import app_entries
import ctypes.wintypes  # 第 216 项：ctypes.wintypes 是懒加载属性，模块级导入一次，
                        # 之后任何路径的 ctypes.wintypes.RECT() 都不会再 AttributeError
                        #（titlebar_restore 在测试假 _win_ptr_funcs 下踩过：全量跑绿、单跑红）

# 高 DPI 适配：必须在 import webview / 创建任何窗口之前设置。
# 旧逻辑用 SetProcessDPIAware（System DPI Aware）——在多显示器 / 150% 缩放下，
# WebView2 会用错误比例渲染，界面元素被整体放大、发虚（表现就是「窗口里一切都很大」）。
# 改为 Per-Monitor v2：WebView2 在窗口所在显示器上按真实 DPI 渲染，内容清晰、大小正常。
#
# 【2026-11-07 实测的坑】**参数必须用 c_void_p(-4)，不能用 c_int(-4)**。
# 这个 API 收的是 DPI_AWARENESS_CONTEXT —— 一个 64 位指针，-4 要写成 0xFFFF...FC。
# 传 c_int 时只给了低 32 位、高 32 位没符号扩展，等于传了 0x00000000FFFFFFFC，
# 调用**返回 False 静默失败**（不抛异常，所以原来那层 try/except 一点用都没有）。
# 实测：c_int(-4) → False，GetSystemMetrics(0)=2560（虚拟化后的逻辑值）；
#       c_void_p(-4) → True，GetSystemMetrics(0)=3840（真实物理值）。
# 失败之后进程就是 DPI-unaware：整个窗口被系统当 96dpi 位图再拉伸一次，
# 界面发虚；而且所有尺寸计算拿到的都是虚拟化数值，算出来的位置全偏。
_DPI_MODE = 'none'
try:
    if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        _DPI_MODE = 'per-monitor-v2'
except Exception:
    pass
if _DPI_MODE == 'none':
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:            # PROCESS_PER_MONITOR_DPI_AWARE
            _DPI_MODE = 'per-monitor-v1'
    except Exception:
        pass
if _DPI_MODE == 'none':
    try:
        if ctypes.windll.user32.SetProcessDPIAware():                       # System DPI Aware（最后兜底）
            _DPI_MODE = 'system'
    except Exception:
        pass


import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
import error_report      # 第 277 项：运行标记 / 前端异常 / 指标都要它；缺这行全是 NameError（都被 try 吞了）
from pathlib import Path
from urllib.parse import quote

import quick_switch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# brain.cli 能认的子命令：这些一律走命令行，不当作搜索词。
# 没列在里面的裸参数（如 SecondBrain.exe 报销）视为「带关键词开窗，直接出结果」，
# 是配合 Listary / PowerToys Run 这类启动器的入口。
_CLI_CMDS = {'index', 'update', 'search', 'prune', 'serve', 'repair',
             'selftest', 'eval', '_extract', 'scan'}

MINI_W = 760                 # 快搜窗宽度（逻辑像素，CSS px 同尺度）
MINI_H = 124                 # 初始高度：一行输入框 + 一行提示
# 第 198/201 项：贴在文件夹右下角 / 另存为下方的「小窗」尺寸。
# 用户连着两次说「太大了」「还是太长」，所以宽度不再跟着窗口比例放大：
# 固定 420 封顶（Listary 的贴角窗约 435），窗口更窄时再缩，绝不反过来变大。
MINI_W_SMALL = 420
MINI_W_DLG = 440            # 另存为对话框宽一档：那一行要放路径 + 按钮区
MINI_H_SMALL = 92
MINI_W_MIN = 300
MAXH_MIN = 150
MAXH_CAP = 360

# Win32 SetWindowPos 旗标（模块级）。229 项七轮教训：_follow_anchor 的调用处
# 引用了 SWP_ASYNCWINDOWPOS，局部赋值行却没定义它 → 每次 NameError，而异常
# 被 _log（SB_DEBUG 门控）吞掉，跟随死了三天没人看见。提到模块级 + 单测钉住。
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_ASYNCWINDOWPOS = 0x1, 0x4, 0x10, 0x4000
MINI_TITLE = 'Memex · 快搜'
MAIN_TITLE = 'Memex'


def icon_file():
    """找 assets/icon.ico：源码目录 / 打包后的 _MEIPASS / 解包目录都试一遍。

    和 tray.py 的 _icon_path() 是同一个顺序，两个地方别写成不一样。"""
    cands = [HERE / 'assets' / 'icon.ico']
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        cands.append(Path(meipass) / 'assets' / 'icon.ico')
    if getattr(sys, 'frozen', False):
        cands.append(Path(sys.executable).parent / 'assets' / 'icon.ico')
    cands.append(HERE / '_internal' / 'assets' / 'icon.ico')
    for p in cands:
        try:
            if p.is_file():
                return str(p)
        except OSError:
            pass
    return None


def apply_window_icon(hwnd, ico=None):
    """把窗口图标换成我们自己的：标题栏 / Alt-Tab / 任务栏三处都读它。

    pywebview 的 create_window 没有 icon 参数（WinForms 外壳不转发），所以只能建完窗
    再 WM_SETICON。

    ⚠️ 别再写 LoadImageW(..., 0, 0)：对多尺寸 .ico 它拿的是**第一张 16x16**，
    不是注释里想当然的「系统按 DPI 挑 256」（2026-11-12 用 GetIconInfo 实测）。
    也别把小图标写死 16：150% DPI 的任务栏要 24/32，拿 16 放大必糊。
    这里按 GetDpiForWindow 选 ico 里最接近目标物理像素的那一档。"""
    ico = ico or icon_file()
    if not hwnd or not ico:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        LR_LOADFROMFILE, IMAGE_ICON, WM_SETICON = 0x10, 1, 0x0080
        u.LoadImageW.restype = ctypes.c_void_p
        try:
            u.GetDpiForWindow.restype = ctypes.c_uint
            dpr = (u.GetDpiForWindow(ctypes.c_void_p(hwnd)) or 96) / 96.0
        except Exception:
            dpr = 1.0
        small_sz = min((16, 20, 24, 32, 40, 48), key=lambda x: abs(x - 16 * dpr))
        big_sz = min((32, 40, 48, 64, 96, 128, 256), key=lambda x: abs(x - 32 * dpr))
        big = u.LoadImageW(0, ico, IMAGE_ICON, big_sz, big_sz, LR_LOADFROMFILE)
        small = u.LoadImageW(0, ico, IMAGE_ICON, small_sz, small_sz, LR_LOADFROMFILE)
        ok = False
        if big:
            u.SendMessageW(hwnd, WM_SETICON, 1, ctypes.c_void_p(big))
            ok = True
        if small:
            u.SendMessageW(hwnd, WM_SETICON, 0, ctypes.c_void_p(small))
        return ok
    except Exception as e:
        _log(f'apply_window_icon FAILED: {e}')
        return False

DEBUG = bool(os.environ.get('SB_DEBUG'))


def _log(msg):
    """窗口显隐的时序问题只能靠时间戳看。正常启动不开，排查时设 SB_DEBUG=1。

    **必须写到 _log_path()，不能用相对路径 `debug.log`**：从快捷方式、开机自启、
    计划任务启动时当前目录是 System32 或用户目录，相对路径要么写不进去（异常被吞），
    要么写到用户根本找不到的地方 —— 排查时「开了 SB_DEBUG 却没有日志」就是它。
    """
    if not DEBUG:
        return
    now = time.time()
    stamp = '%s.%03d' % (time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)),
                         int(now * 1000) % 1000)
    try:
        p = _log_path()
        try:
            if p.exists() and p.stat().st_size > _LOG_MAX:
                p.replace(p.with_suffix('.log.1'))
        except Exception:
            pass
        with open(p, 'a', encoding='utf-8') as f:
            f.write('[%s] %s%s' % (stamp, msg, chr(10)))
    except Exception:
        try:
            print('[%s] %s' % (stamp, msg), flush=True)
        except Exception:
            pass


_LOG_MAX = 512 * 1024
_START_ERR = ['']          # main() 与 _start_server() 共享：启动阶段的异常


def _log_path():
    try:
        return brain.app_dir() / 'debug.log'
    except Exception:
        return 'debug.log'


def _log_always(msg):
    """出问题时要留痕：不看 SB_DEBUG 也写，超过 512KB 滚动一份 .1。"""
    try:
        p = _log_path()
        try:
            if p.exists() and p.stat().st_size > _LOG_MAX:
                p.replace(p.with_suffix('.log.1'))
        except Exception:
            pass
        with open(p, 'a', encoding='utf-8') as f:
            f.write('[%s] %s%s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg, chr(10)))
    except Exception:
        pass


def _metric(feature, status='ok', sub=None, err=''):
    """第 277 项：窗口侧的埋点（热键/悬浮球/更新/托盘）。

    统计失败绝不能影响主流程，所以这里永远不抛。
    """
    try:
        import feature_metrics
        feature_metrics.add(feature, status, sub=sub, err=str(err or '')[:120])
    except Exception:
        pass


def _startup_report(text):
    """启动/初始化失败时先落一份本机报告（第 162 项）。

    界面都起不来时，设置页里的「报告我看到的问题」根本不可达；这里直接写
    UserFeedback 报告，下次正常启动会随其他待发报告一起补发。
    """
    try:
        import error_report
        if not (getattr(error_report, '_CTX', {}) or {}).get('dir'):
            error_report.configure(brain.app_dir(), getattr(brain, 'VERSION', ''))
        rid = error_report.save_feedback('启动/初始化失败：' + str(text)[:1200], True)
        if rid:
            _log_always('startup report saved: %s' % rid)
        return rid or ''
    except Exception as e:
        _log_always('startup report failed: %r' % (e,))
        return ''


def _fatal(title, text):
    """窗口起不来时别让用户对着空气——弹一个能读的框，并把详情写进日志。"""
    _log_always('FATAL %s: %s' % (title, text))
    rid = _startup_report('%s\n%s' % (title, text))
    if rid:
        text = '%s\n\n已保存本机报告 %s，下次正常启动会自动补发。' % (text[:1100], rid)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, ('%s%s%s') % (text[:1200], chr(10), chr(10) + '日志：' + str(_log_path())),
                                         title, 0x10)
    except Exception:
        print(text)


def _die(msg):
    """pythonw 启动时没有控制台：报错必须弹窗，否则用户看到的就是「双击没反应」。
    最常见的就是依赖没装全（pypdf / pymupdf / rapidocr）。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, 'Memex', 0x10)
    except Exception:
        print(msg)
    _startup_report(msg)
    sys.exit(1)


try:
    import brain  # noqa: E402
except Exception as e:
    _die(f'启动失败：{type(e).__name__}: {e}\n\n'
         f'Startup failed — a dependency is missing.\n'
         f'请在本目录命令行执行：\n  pip install -r requirements.txt')

try:
    import webview  # 必须模块级导入：Api 的方法在别的线程里调用它
except Exception:
    webview = None

BASE_PORT = 8765
PORT_TRIES = 12


def _port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(('127.0.0.1', port)) == 0


def _is_ours(port, data_dir=None):
    """端口上跑的是不是**这个用户**的这一份应用。

    以前只认 app 名就够了，因为一台机器只有一个用户在跑。多用户会话（快速用户
    切换、远程桌面服务器上的多个会话）打破了这个前提：另一个用户的实例同样监听
    8765、同样自称 second-brain，第二个用户一启动就把它当成「自己已经在跑」——
    复用别人的服务，看到的是别人的索引和文件列表。所以有 data_dir 时再比一次。
    """
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=0.8) as r:
            v = json.loads(r.read().decode('utf-8'))
            if v.get('app') != 'second-brain':
                return False
            if data_dir:
                try:
                    a = os.path.normcase(os.path.abspath(str(v.get('dir') or '')))
                    b = os.path.normcase(os.path.abspath(str(data_dir)))
                except Exception:
                    return True          # 比不了就当是自己的，别把正常单用户搞坏
                if a != b:
                    _log('port %d 属于另一个数据目录（%s），不复用' % (port, a))
                    return False
            return True
    except Exception:
        return False


def _data_dir():
    try:
        return str(brain.app_dir())
    except Exception:
        return ''


def _pick_port():
    """找一个能用的端口：空闲的优先，其次复用已在运行的本应用实例。
    全部被占返回 0。原实现直接用 8765，被别的程序占用时会直接抛异常退出。"""
    d = _data_dir()
    for port in range(BASE_PORT, BASE_PORT + PORT_TRIES):
        if not _port_busy(port):
            return port
        if _is_ours(port, d):
            return port
    return 0


def _start_server(port):
    if _port_busy(port):
        if _is_ours(port, _data_dir()):
            return None          # 本用户的实例已经在跑，直接复用
        # 端口在这几毫秒里被别的程序（或别的用户）抢走了：不能再假装复用，
        # 否则窗口会去连别人的服务，显示的是别人的索引。
        _START_ERR[0] = '端口 %d 在启动瞬间被其他程序占用' % port
        return None
    try:
        srv = brain.make_server(port)
    except Exception:
        # 第 239 项：这里原本有一句局部 `import traceback`，它把整个函数的
        # traceback 变成了局部名 —— 于是下面「启动监听失败」那个 except 里
        # 一用就抛 UnboundLocalError，把真正的失败原因（NameError 等）吃掉，
        # 用户看到的弹窗是「窗口启动失败：UnboundLocalError: traceback」，
        # 完全没法查。模块顶部已经 import traceback，别再在函数里重复导。
        _START_ERR[0] = traceback.format_exc()
        _log_always('make_server FAILED (port %d):%s%s' % (port, chr(10), _START_ERR[0]))
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # 桌面模式同样要开实时监听：用户不会去点「更新」，新建的文件必须自己进库
    try:
        brain.start_watcher()
    except Exception:
        # 监听起不来不该拦着用户用软件（他还能点「更新」），所以只记日志。
        _log_always('start_watcher FAILED:%s' % traceback.format_exc())
    return srv


# ------------------------------------------------------------------ Win32 小工具
def _win():
    """懒加载 win32 相关；拿不到就返回 None，调用方全部走「没有就算了」的分支"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.WinDLL('user32', use_last_error=True)
        return u, wt
    except Exception:
        return None, None


def find_hwnd(title, pid=None):
    """按标题找窗口句柄（pywebview 没暴露 HWND，绕一圈拿到原生句柄）。

    ⚠️ **必须校验进程归属**（第 181 项，踩过真坑）：以前只比标题，机器上同时
    跑着两个实例时（用户那份 + 调试那份）会抓到**别人**的窗口 —— 实测把用户窗口
    的 WS_CAPTION 摘掉了，自己那份却还留着系统标题栏，前端照着 ping 又画一条，
    于是屏幕上出现两条标题栏，而且「日志说摘成功、肉眼却是坏的」。
    默认只认本进程；要跨进程拿句柄时显式传 pid。
    """
    try:
        import win32gui
    except Exception:
        return None
    me = os.getpid() if pid is None else int(pid)
    u = ctypes.windll.user32
    u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    hits = []

    def owner(h):
        try:
            p = ctypes.c_ulong(0)
            u.GetWindowThreadProcessId(ctypes.c_void_p(h), ctypes.byref(p))
            return p.value
        except Exception:
            return 0

    def cb(h, _):
        if win32gui.GetWindowText(h) != title or not win32gui.IsWindow(h):
            return True
        o = owner(h)
        if o and o != me:
            return True
        hits.append(h)
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        return None
    return hits[0] if hits else None


def wait_hwnd(title, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        h = find_hwnd(title)
        if h:
            return h
        time.sleep(0.08)
    return None


def _monitor_scale(hmon):
    """这块显示器的缩放比例（1.5 = 150%），拿不到返回 0。

    用 GetScaleFactorForMonitor 而不是 GetDpiForMonitor：后者对 Per-Monitor v2
    进程返回的是「系统 DPI」，实测在 150% 的屏上也给 96 —— 用它换算等于没量。
    """
    try:
        sh = ctypes.WinDLL('shcore', use_last_error=True)
        sf = ctypes.c_int(0)
        if sh.GetScaleFactorForMonitor(ctypes.c_void_p(hmon), ctypes.byref(sf)) == 0 \
                and sf.value:
            return sf.value / 100.0
    except Exception:
        pass
    return 0.0


def screen_metrics():
    """主屏 (物理宽, 物理高, dpr)。

    **别用 GetSystemMetrics ÷ GetDpiForSystem**：后者给的是「登录会话建立那一刻」的
    系统 DPI，用户登录后把缩放从 100% 调到 150%，它仍然是 96（本机实测 96 vs 真实 144）。
    于是逻辑屏宽被算大 1.5 倍，居中的窗口就偏到右边（150% 屏上实测偏 640 逻辑像素）。
    """
    u, wt = _win()
    if not u:
        return 1920, 1080, 1.0
    try:
        hmon = u.MonitorFromPoint(wt.POINT(0, 0), 2)      # MONITOR_DEFAULTTONEAREST
        dpr = _monitor_scale(hmon) or window_dpr(None) or 1.0
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1)), float(dpr)
    except Exception:
        return 1920, 1080, 1.0


def mini_origin(hwnd=None):
    """快搜窗出现的位置：屏幕水平居中，垂直约 17% —— 视线自然落点，
    底部还留得下九行结果。返回的是逻辑像素，和 create_window 的单位一致。"""
    pw, ph, dpr = screen_metrics()
    if hwnd:
        # 窗口已存在时以它自己的 DPI 为准：跨屏拖动后只有 GetDpiForWindow 是最新的
        try:
            dpr = window_dpr(hwnd) or dpr
        except Exception:
            pass
    sw, sh = pw / dpr, ph / dpr
    return max(0, int((sw - MINI_W) // 2)), max(0, int(sh * 0.17))



# --- 多显示器：快搜窗要出现在鼠标所在的那块屏上 -----------------------
# 单屏用户永远走上面 mini_origin 的老路（零改动）；只有鼠标离开主显示器时，
# 才改用下面这套「按物理像素定位」的逻辑。否则双屏用户在主屏旁边的副屏上干活，
# 双击 Ctrl 之后窗口弹在另一块屏的中央 —— 他的感受就是「快捷键没反应」。
MONITOR_DEFAULTTONEAREST = 0x00000002


def _monitor_info(hmon):
    """取显示器信息：返回 (left, top, right, bottom) 物理像素，失败返回 None。"""
    u, wt = _win()
    if not u or not hmon:
        return None

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT),
                    ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]

    try:
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if not u.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
            return None
        r = mi.rcMonitor
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _monitor_work(hmon):
    """显示器**工作区**（排除任务栏）：(left, top, right, bottom)，失败 None。

    和 `_monitor_info` 的区别只在取 `rcWork` 而不是 `rcMonitor` ——
    贴边半屏必须按工作区算，否则下半截会被任务栏盖住。
    """
    u, wt = _win()
    if not u or not hmon:
        return None

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT),
                    ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]

    try:
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if not u.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
            return None
        r = mi.rcWork
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def cursor_monitor_rect():
    """鼠标所在显示器的物理矩形；拿不到返回 None。"""
    u, wt = _win()
    if not u:
        return None
    try:
        pt = wt.POINT()
        if not u.GetCursorPos(ctypes.byref(pt)):
            return None
        hmon = u.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        return _monitor_info(hmon)
    except Exception:
        return None


def primary_monitor_rect():
    """主显示器的物理矩形；拿不到返回 None。"""
    u, wt = _win()
    if not u:
        return None
    try:
        hmon = u.MonitorFromPoint(wt.POINT(0, 0), MONITOR_DEFAULTTONEAREST)
        return _monitor_info(hmon)
    except Exception:
        return None


def mini_origin_in(rect, dpr):
    """在指定显示器（物理矩形 rect + 该屏缩放 dpr）里算快搜窗的逻辑坐标。

    抽成纯函数是为了能测：单屏时 rect=(0,0,W,H)，结果和 mini_origin 完全一致；
    副屏在左/上方时 rect 的 left/top 是负数，坐标也要跟着为负，不能 max 到 0。
    """
    left, top, right, bottom = rect
    sw = (right - left) / dpr
    sh = (bottom - top) / dpr
    x = int(round((sw - MINI_W) / 2)) + int(round(left / dpr))
    y = int(round(sh * 0.17)) + int(round(top / dpr))
    return x, y


def calibrate_mini_width():
    """按真实屏幕宽收窄快搜窗。

    设计宽 760 逻辑像素。可是「1920×1080 + 250% 缩放」只剩 768 逻辑宽、
    「1920×1080 + 300%」只剩 640 —— 窗口比屏幕还宽，右边被切掉，用户看到的是
    「搜出来的标题只剩半句」。这类组合在触屏本和远程桌面上比 4K 常见得多。

    正常屏幕（逻辑宽 ≥ 800）**不动 MINI_W**，截图基准和 DOM 尺寸都保持原样。
    """
    global MINI_W
    try:
        import env_check
        scr = env_check.logical_screens()
        prim = next((s for s in scr if s.get('primary')), scr[0] if scr else None)
        if not prim:
            return MINI_W
        avail = int(prim.get('logical_full_w') or 0)
        if avail <= 0:
            return MINI_W
        want = MINI_W if avail >= MINI_W + 40 else max(420, avail - 32)
        if want != MINI_W:
            _log('calibrate_mini_width: 屏逻辑宽 %d → 快搜窗 %d（原 %d）'
                 % (avail, want, MINI_W))
            MINI_W = want
    except Exception as e:
        _log('calibrate_mini_width FAILED: %r' % (e,))
    return MINI_W


def system_dark():
    """跟随系统主题：注册表 AppsUseLightTheme=0 就是深色。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
        v, _ = winreg.QueryValueEx(k, 'AppsUseLightTheme')
        return int(v) == 0
    except Exception:
        return False


_LAST_THEME = {'dark': None, 'bg': None}


def effective_dark():
    """界面主题（auto/dark/light）→ 现在到底是不是深色。

    第 105 项把 settings.json 的读取收口成 _settings_read() 时这里漏改了，
    AttributeError 被 except 吞成「读不到设置」→ theme=dark 时标题栏永远不变深
    （2026-11-07 用户切深色才发现）。"""
    if _LAST_THEME.get('dark') is not None:
        return bool(_LAST_THEME['dark'])
    try:
        import brain as _b
        s = _b._settings_read() or {}
        th = s.get('theme', 'auto')
        pal = s.get('palette', 'auto')
    except Exception:
        th = 'auto'
        pal = 'auto'
    if th == 'dark' or pal == 'dark':
        return True
    if th == 'light' or pal == 'light':
        return False
    if pal in ('navy', 'forest', 'charcoal', 'ember', 'dusk', 'deep'):
        return True
    return system_dark()


# 界面底色的两个真值，必须和 web/src/index.css 里的 --sb-bg 保持一致。
# 为什么要从 Python 这边也知道：摘掉系统标题栏后，窗口还留着 WS_THICKFRAME 的
# 7px 缩放边框，那圈**由 Form 的 BackColor 填**（pywebview 默认 #FFFFFF）。
# 于是标题栏是浅灰、外面一圈纯白 —— 用户看到的就是「两层颜色」（第 184 项）。
UI_BG_LIGHT = (244, 246, 250)      # --sb-bg: 244 246 250
UI_BG_DARK = (24, 27, 33)          # [data-theme='dark'] --sb-bg: 24 27 33


def ui_bg_rgb(dark=None):
    """界面底色（RGB 三元组）。dark 传入时以它为准，否则读配置/系统。

    为什么要能传：页面实际用哪套色，前端才知道（它认 localStorage.sb_theme，
    还会跟系统 auto）。后端读 settings.json 只是**启动时**的猜测，两者平时一致、
    用户在页面里改主题那一刻不一致 —— 所以运行期一律用前端报过来的值。
    """
    try:
        d = effective_dark() if dark is None else bool(dark)
    except Exception:
        d = False
    return UI_BG_DARK if d else UI_BG_LIGHT


def ui_bg_hex(dark=None):
    r, g, b = ui_bg_rgb(dark)
    return '#%02X%02X%02X' % (r, g, b)


def _hex_rgb(x):
    """'#RRGGBB' → (r, g, b)。前端报上来的自定义配色底色用这个解析，坏值一律 None。"""
    try:
        t = str(x).strip().lstrip('#')
        if len(t) != 6:
            return None
        return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
    except Exception:
        return None


def _dwm_set(hwnd, attr, value):
    """一条 DwmSetWindowAttribute，失败/老系统一律当没设。"""
    try:
        import ctypes
        v = ctypes.c_int(int(value))
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), int(attr), ctypes.byref(v), 4) == 0
    except Exception:
        return False


def _colorref(r, g, b):
    return (int(b) << 16) | (int(g) << 8) | int(r)


# 已经设过一次的 (hwnd, 色) —— 只用来省掉重复的 RedrawWindow，不靠它判断「系统有没有重置」
_FRAME_APPLIED = {'key': None}


def apply_frame(hwnd, dark=None, bg=None, redraw=True):
    """第 342 项：把窗口顶部那条 caption 带 + 四周边框刷成界面底色。

    根因（2026-11-19 量出来的，DPR 150% 的机器上）：
      · `GetWindowRect` 比 `GetClientRect` 四边各多 **10px**（≈6.7 CSS px），
        顶边那 10px 就是 DWM 画的 caption 带，**不在网页里**，遮罩也盖不到；
      · 它是系统色，默认白/浅灰 —— 深色主题下就是「标题上面还有一层白」；
      · 写 `DWMWA_CAPTION_COLOR(35)` 能压住，但**会被系统重置**：
        切深色（属性 20/19）、窗口激活/失活、缩放变化都会把它打回默认白，
        所以用户「切换主题也不行」—— 设一次不够，得有人一直盯着（见 _frame_keeper）。

    另外两条：
      · `DWMWA_BORDER_COLOR(34)` 同样刷底色（系统开着「标题栏显示强调色」时边框会是彩色）；
      · `DWMWA_SYSTEMBACKDROP_TYPE(38) = DWMSBT_NONE`：不要 Mica/亚克力，
        否则这条带会映出壁纸渐变（截图里那条蓝色斜纹带就是这么来的）。
    ⚠️ 不要设 `DWMWA_NCRENDERING_POLICY(2) = DWMNCRP_DISABLED`：实测那样反而多出一条白带。
    """
    if not hwnd:
        return False
    if dark is None and _LAST_THEME.get('dark') is not None:
        dark = _LAST_THEME['dark']
    if bg is None and _LAST_THEME.get('bg') is not None:
        bg = _LAST_THEME['bg']
    r, g, b = bg if bg else ui_bg_rgb(dark)
    cr = _colorref(r, g, b)
    key = (int(hwnd), cr)
    first = _FRAME_APPLIED['key'] != key
    # 先关掉 Mica / 亚克力：它会让这条带透出壁纸
    _dwm_set(hwnd, 38, 1)
    ok_cap = _dwm_set(hwnd, 35, cr)          # 顶部 caption 带
    ok_bd = _dwm_set(hwnd, 34, cr)           # 最外 1px 描边
    if redraw and first:
        try:
            import ctypes
            ctypes.windll.user32.RedrawWindow(
                ctypes.c_void_p(hwnd), None, None, _RDW_FRAME | _RDW_INVALIDATE)
        except Exception:
            pass
    if first:
        _FRAME_APPLIED['key'] = key
        _log('apply_frame hwnd=%s rgb=%s,%s,%s cap=%s border=%s' % (hwnd, r, g, b, ok_cap, ok_bd))
    return ok_cap or ok_bd


def frame_invisible(hwnd, bg=None):
    """旧名保留（第 342 项）：等价于 apply_frame(hwnd, bg=bg)。"""
    return apply_frame(hwnd, bg=bg)


def paint_frame(hwnd, dark=None, bg=None):
    """第 184 项的入口，现由 apply_frame 实现（第 342 项合并，避免两套逻辑打架）。"""
    return apply_frame(hwnd, dark, bg)


def set_window_background(win, rgb=None):
    """第 359 项：把窗口（WinForms Form + 里面的浏览器控件）背景刷成界面底色。

    为什么非要有这一步：快搜窗是 frameless，`soft_shadow()` 又把 DWM 边框扩进
    客户区 1px，`install_child_hit_guard()` 还让 WebView2 四周让出几像素（拖边缘
    要用）。于是窗口最外圈那一圈（DPI 150% 实测 8~11px）**不在网页里**，画的是
    WinForms 的 BackColor。mini 建窗写的是 '#FFFFFF' —— 用户把配色换成 cyber /
    night 之后，右侧就挂着一条白边，网页里怎么改 CSS 都盖不到它。

    ⚠️ 跨线程：通常从 JS API 线程调用；WinForms 属性赋值跨线程不抛异常，
    这里也一律吞掉失败 —— 刷不上最多留一条细边，不能把 set_dark 带崩。
    """
    if win is None:
        return False
    try:
        from System.Drawing import Color
        r, g, b = rgb if rgb else ui_bg_rgb()
        col = Color.FromArgb(int(r), int(g), int(b))
        native = getattr(win, 'native', None)
        if native is None:
            return False
        native.BackColor = col
        try:
            for c in native.Controls:
                try:
                    c.BackColor = col
                except Exception:
                    pass
        except Exception:
            pass
        # 第 363 项：只改 BackColor 不够 —— 那圈非客户区是 DWM 合成的旧表面，
        # 建窗时画成白的，之后窗口长高只补刷新增的那段，原来那段仍是白的。
        # 这里显式把 frame 标脏重画一遍（RDW_FRAME 只碰边框，不动网页）。
        try:
            import ctypes
            ctypes.windll.user32.RedrawWindow(
                ctypes.c_void_p(getattr(native, 'Handle', 0) or 0), None, None,
                0x0001 | 0x0004 | 0x0100)      # INVALIDATE|ERASE|FRAME
        except Exception:
            pass
        return True
    except Exception as e:
        _log('set_window_background skipped: %r' % (e,))
        return False


_KEEPER_STOP = threading.Event()
_KEEPER_TH = {'t': None}


def _frame_keeper():
    """每 3 秒把 caption / 边框色重设一遍（第 342 项）。

    为什么需要循环：Windows 自己会把这些 DWM 属性重置回默认（切深色、激活切换、
    DPI 变化都会），设一次只能好一会儿。这里只重设属性、不重画（值没变时 DWM 不动）。
    """
    while not _KEEPER_STOP.is_set():
        try:
            d = _LAST_THEME.get('dark')
            bg = _LAST_THEME.get('bg')
            for title in (MAIN_TITLE, MINI_TITLE):
                hw = find_hwnd(title)
                if hw:
                    apply_frame(hw, d, bg, redraw=False)
        except Exception:
            pass
        _KEEPER_STOP.wait(3.0)


def start_frame_keeper():
    """起一个后台线程盯着窗口外框颜色；重复调用无副作用。"""
    t = _KEEPER_TH.get('t')
    if t and t.is_alive():
        return True
    _KEEPER_STOP.clear()
    t = threading.Thread(target=_frame_keeper, name='frame-keeper', daemon=True)
    _KEEPER_TH['t'] = t
    t.start()
    _log('frame keeper started')
    return True


def stop_frame_keeper():
    _KEEPER_STOP.set()


def dark_caption(hwnd, dark=True):
    """把系统标题栏刷成深色（Win10 1809+ 用属性 19，Win11 用 20）。

    内容区已经是深色、标题栏还是白的那一条，是最扎眼的不一致。
    设不上就保持系统原样，不影响功能。"""
    if not hwnd:
        return False
    try:
        import ctypes
        v = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            try:
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(v), 4) == 0:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False



# ── 自绘标题栏（第 180 项）──────────────────────────────────────────────
# WorkBuddy / VS Code 那类 Electron 应用有 `titleBarStyle: hidden` 一行开关就能把
# 按钮摆进标题栏；pywebview + WinForms 没有这个开关，等效做法只有一个：
# **把系统画的标题栏摘掉**（WS_CAPTION），客户区自然顶到窗口最上面，标题栏由 HTML 画。
# 不要再去非客户区里画（第 115 / 121 项三版全撤：DWM 会盖掉、打包后行为不一致）。
#
# 关键取舍 —— 只摘 WS_CAPTION，**保留** WS_THICKFRAME / WS_MAXIMIZEBOX /
# WS_MINIMIZEBOX / WS_SYSMENU：
#   · 拖动走 WM_NCLBUTTONDOWN(HTCAPTION)，由系统接管，Aero Snap / 双击最大化都在；
#   · 边框缩放、DWM 阴影、任务栏缩略图都还是系统的；
#   · 窗口仍是 WS_OVERLAPPED，最大化不会盖住任务栏（换成 WS_POPUP 才会）。
_WS_CAPTION = 0x00C00000
_GWL_STYLE = -16
# SetWindowPos 标志位（用到的每个都单独命名：只写一个合成值 _SWP_FLAGS
# 会让别的调用处误用未定义名字 —— 第 183 项就踩了，NameError 被 except 吞掉，
# 现象是「拖动完全没反应」而日志一片正常）
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_SWP_SHOWWINDOW = 0x0040
# RedrawWindow：改完非客户区（描边 / 缩放边框底色）必须让它立刻重画
_RDW_INVALIDATE = 0x0001
_RDW_UPDATENOW = 0x0100
_RDW_FRAME = 0x0400
_SWP_FLAGS = _SWP_FRAMECHANGED | _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER
_WM_NCLBUTTONDOWN, _HTCAPTION = 0x00A1, 2
_SW_MINIMIZE, _SW_MAXIMIZE, _SW_RESTORE = 6, 3, 9
_WM_SYSCOMMAND = 0x0112
_SC_MINIMIZE, _SC_MAXIMIZE, _SC_RESTORE = 0xF020, 0xF030, 0xF120


# ——— 第 343 项：把「非客户区」整块吃掉，顶部 caption 带从根上不存在 ———
# 为什么会有那条带：窗口带着 WS_THICKFRAME（可缩放边框，实测 style=0x160f0000 含 0x00040000），
# 系统就给它留了 10px 非客户区（DPR 150% 下），顶边那 10px 被 DWM 按 caption 色画出来。
# 官方解法是：子类化窗口过程，收 WM_NCCALCSIZE(wParam=TRUE) 时直接 return 0 ——
# 「客户区占满整窗」。这样非客户区厚度 = 0，**没有带子可画**，也就不需要一直盯着改颜色。
# 缩放/贴边/阴影都保留：WS_THICKFRAME 没摘，DefWindowProc 的命中测试照旧生效。
# 唯一要照顾的是最大化：带 frame 的窗口最大化时窗口矩形会比工作区各边多出那 10px，
# 这时必须交回默认处理，否则内容会被裁掉一圈。
_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
_WM_SIZE = 0x0005
_WM_ACTIVATE = 0x0006
_WM_DPICHANGED = 0x02E0
_WM_THEMECHANGED = 0x031A
_HTTRANSPARENT = -1
_HTLEFT, _HTRIGHT, _HTTOP, _HTTOPLEFT, _HTTOPRIGHT = 10, 11, 12, 13, 14
_HTBOTTOM, _HTBOTTOMLEFT, _HTBOTTOMRIGHT = 15, 16, 17
_GWLP_WNDPROC = -4
_NC = {'proc': None, 'child_proc': None, 'old': {}, 'ours': set()}
_CHILD_NC = {}          # child hwnd -> (old_proc, our_proc, parent_hwnd)

# ——— 第 363 项：把快搜窗那圈非客户区涂成实色 ———
# 现象：深色配色下快搜窗右边挂一条白带，而且只到某个高度就断掉。
# 量出来：那 11px 宽的条在客户区之外（客户区 1118×463 / 窗口 1140×474），
# 走的是 DWM 的玻璃边框 —— 里面什么都没画，**背后是什么就透什么**：
# 用户背后那个白窗口就是那条白带，断掉的位置正好是两个窗口的重叠边界。
# 解法：① 撤掉 DWM 扩进客户区的玻璃面（DwmExtendFrameIntoClientArea 全 0）；
#        ② 自己处理 WM_NCPAINT，用 GDI 把那三条边刷成当前底色 → 不透明、跟主题走。
_MATTE = {}             # hwnd -> COLORREF
_WM_NCPAINT = 0x0085


def _paint_matte_frame(hwnd):
    """把窗口左/右/下三条非客户边刷成 _MATTE[hwnd] 里的颜色（物理像素）。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32
    color = _MATTE[int(hwnd)]
    wr = wt.RECT()
    u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(wr))
    ww, wh = wr.right - wr.left, wr.bottom - wr.top
    b = _frame_insets(hwnd)
    hdc = u.GetWindowDC(ctypes.c_void_p(hwnd))
    if not hdc:
        return False
    brush = g.CreateSolidBrush(color)
    # GetWindowDC 的原点是窗口左上角 → 这里的矩形必须用**窗口内坐标**（0,0 起）
    bands = (
        wt.RECT(0, 0, b, wh),                    # 左
        wt.RECT(ww - b, 0, ww, wh),              # 右
        wt.RECT(0, wh - b, ww, wh),              # 下
    )
    for r in bands:
        u.FillRect(hdc, ctypes.byref(r), brush)      # FillRect 是 user32 的导出函数
    g.DeleteObject(brush)
    u.ReleaseDC(ctypes.c_void_p(hwnd), hdc)
    _log_always('ncpaint hwnd=%s color=%s b=%s' % (hwnd, color, b))
    return True


def matte_frame(hwnd, rgb=None):
    """第 363 项：给一扇窗关掉玻璃边框 + 记下底色（幂等，重复调用只更新颜色）。"""
    if not hwnd:
        return False
    key = int(hwnd)
    rgb = rgb if rgb else (_LAST_THEME.get('bg') or ui_bg_rgb())
    if isinstance(rgb, str):                 # 前端报上来的是 '#RRGGBB'
        rgb = _hex_rgb(rgb)
    if not rgb:
        rgb = ui_bg_rgb()
    _MATTE[key] = _colorref(rgb[0], rgb[1], rgb[2])
    _log_always('matte_frame hwnd=%s rgb=%s color=%s' % (key, rgb, _MATTE[key]))
    try:
        import ctypes
        m = _MARGINS(0, 0, 0, 0)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(ctypes.c_void_p(key), ctypes.byref(m))
        # pywebview 建窗时给 shadow 窗设过 DWMWA_NCRENDERING_POLICY=DISABLED。
        # 关掉 NC 渲染，那圈非客户区就永远没人画 —— 直接透出背后的窗口（白条）。
        # 这里改回 ENABLED，让 WM_NCPAINT 回到我们手里，由 _paint_matte_frame 刷色。
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                              ctypes.c_void_p, ctypes.c_uint]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        dwm.DwmExtendFrameIntoClientArea.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        v_en = ctypes.c_int(1)                           # DWMNCRP_ENABLED
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(key), 2, ctypes.byref(v_en), 4)
        # 最外那 2px 是 DWM 自己的边框色（自绘盖不到），一并刷成同一底色
        v_bd = ctypes.c_int(_MATTE[key])
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(key), 34, ctypes.byref(v_bd), 4)  # DWMWA_BORDER_COLOR
        # DWM 缓存着旧表面：SWP_FRAMECHANGED 让它重算，再连 frame 一起标脏立即刷
        u = ctypes.windll.user32
        u.SetWindowPos(ctypes.c_void_p(key), None, 0, 0, 0, 0,
                       0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        u.RedrawWindow(
            ctypes.c_void_p(key), None, None, 0x0001 | 0x0004 | 0x0100 | 0x0400)   # +UPDATENOW
        # 系统不一定给我们发 WM_NCPAINT（缓存表面还在），换色后自己补一遍
        _paint_matte_frame(key)
    except Exception as e:
        _log('matte_frame failed: %r' % (e,))
        return False
    return True


def _resize_border(hwnd):
    """边缘拖拽热区有多宽（物理像素）：按 DPI 取 6 个 CSS px。"""
    try:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd)) or 96
        return max(4, int(round(6 * dpi / 96.0)))
    except Exception:
        return 8


def _frame_insets(hwnd):
    """系统给的边框厚度（物理像素）：SM_CXSIZEFRAME + SM_CXPADDEDBORDER，按 DPI 取。"""
    import ctypes
    u = ctypes.windll.user32
    try:
        dpi = u.GetDpiForWindow(ctypes.c_void_p(hwnd)) or 96
        sm = getattr(u, 'GetSystemMetricsForDpi', None)
        if sm:
            return (sm(32, dpi) + sm(92, dpi)) or 10        # CXSIZEFRAME=32, CXPADDEDBORDER=92
    except Exception:
        pass
    return (u.GetSystemMetrics(32) + u.GetSystemMetrics(92)) or 10


def _hit_test(hwnd, lparam):
    """第 343 项：客户区铺满整窗后，边缘缩放要自己给 HT* 码（否则整窗都是 HTCLIENT）。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    if u.IsZoomed(ctypes.c_void_p(hwnd)):
        return None                       # 最大化时没有边缘可拖
    x = ctypes.c_short(lparam & 0xFFFF).value
    y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
    wr = wt.RECT()
    u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(wr))
    b = _resize_border(hwnd)
    left = x <= wr.left + b
    right = x >= wr.right - b
    top = y <= wr.top + b
    bottom = y >= wr.bottom - b
    if top and left:
        return _HTTOPLEFT
    if top and right:
        return _HTTOPRIGHT
    if bottom and left:
        return _HTBOTTOMLEFT
    if bottom and right:
        return _HTBOTTOMRIGHT
    if left:
        return _HTLEFT
    if right:
        return _HTRIGHT
    if top:
        return _HTTOP
    if bottom:
        return _HTBOTTOM
    return None




# 第 375 项：子窗转发非客户区按下时要用到的消息号 / 光标资源号（IDC_*）。
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONDBLCLK = 0x0203
_WM_NCLBUTTONDBLCLK = 0x00A3
_WM_SETCURSOR = 0x0020
_RESIZE_CURSOR = {_HTTOP: 32645, _HTBOTTOM: 32645,           # IDC_SIZENS
                  _HTLEFT: 32644, _HTRIGHT: 32644,           # IDC_SIZEWE
                  _HTTOPLEFT: 32642, _HTBOTTOMRIGHT: 32642,  # IDC_SIZENWSE
                  _HTTOPRIGHT: 32643, _HTBOTTOMLEFT: 32643}  # IDC_SIZENESW


def _cursor_pt():
    """当前鼠标的屏幕坐标（物理像素）。"""
    import ctypes
    import ctypes.wintypes as wt
    p = wt.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(p)):
        return None
    return (p.x, p.y)


# 需要挂「边缘让路」的子窗类名前缀。
# 第 368 项：原来只认 Chrome_ / Intermediate D3D，漏了 **WebView2 的 WinForms 宿主面板**
# （`WindowsForms10.Window.*`）。它才是 z 序最上面的那个：客户区顶边归零后，顶部 6px
# 的缩放热区全被它吃掉（回 HTCLIENT），父窗的 WM_NCHITTEST 根本没被问到 → 鼠标在
# 标题栏最上面拖不出缩放箭头，上下也就拉不动。左右下能动，是因为那三条边还在非客户区。
# 第 375 项：还漏了真正吃鼠标的那个 —— `Chrome_RenderWidgetHostHWND`（渲染宿主，
# 它才是 z 序最上面、直接收 WM_LBUTTONDOWN 的窗口；`Chrome_` 前缀匹配不到它，
# 名字里是 `Chrome_RenderWidget...`，下划线位置不一样）。少了它，按在顶部热区上
# 会被当成「拖标题栏」，窗口整体跟着鼠标往下走而不是改高度。
_GUARD_PREFIXES = ('Chrome_', 'Chrome_RenderWidgetHostHWND', 'Intermediate D3D',
                   'WindowsForms10.Window')


def _child_hitproc(hwnd, msg, wparam, lparam):
    """WebView2 的子窗口盖住了整块客户区，边缘那几像素要让回给父窗口测命中。

    第 375 项：HTTRANSPARENT 只对**同一线程**的窗口有效。WebView2 的宿主面板
    （WindowsForms10.Window.* / Chrome_*）是另一个线程的窗口，系统不会因为我们
    回了 HTTRANSPARENT 就把命中转给父窗 —— 于是父窗的 _hit_test 永远轮不到，
    标题栏最上面那条 6px 热区按下去一点反应都没有（左右下能动，是因为那三边
    本来就在真正的非客户区里，压根不经过子窗）。

    这里替父窗把这一下翻译成「非客户区按下 / 双击」：父窗的 DefWindowProc 收到
    就进系统自己的缩放 / 最大化循环，鼠标本来就按着，一路拖过去和原生边框一样。
    顺带把热区里的鼠标形状自己给（否则「能拉」也看不出来）。
    """
    rec = _CHILD_NC.get(int(hwnd))
    try:
        if rec and rec[2]:
            if msg == _WM_NCHITTEST:
                if _hit_test(rec[2], lparam) is not None:
                    return _HTTRANSPARENT
            elif msg == _WM_SETCURSOR:
                _pt = _cursor_pt()
                if _pt is not None:
                    code = _hit_test(rec[2], (_pt[1] << 16) | (_pt[0] & 0xFFFF))
                    cur = _RESIZE_CURSOR.get(code)
                    if cur:
                        ctypes.windll.user32.SetCursor(
                            ctypes.windll.user32.LoadCursorW(0, cur))
                        return 1
    except Exception:
        pass
    return _NC_CALL(rec[0] if rec else None, hwnd, msg, wparam, lparam)


def install_child_hit_guard(parent):
    """把 WebView2 的各个子窗口挂上「边缘让路」的过程（重复调用只补新的）。"""
    if not parent:
        return 0
    try:
        import ctypes
        if _NC['child_proc'] is None:
            proto = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                                      ctypes.c_ssize_t, ctypes.c_ssize_t)
            _NC['child_proc'] = proto(_child_hitproc)
        ptr = ctypes.cast(_NC['child_proc'], ctypes.c_void_p).value
        u = ctypes.windll.user32
        found = []

        def visit(h, _):
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(ctypes.c_void_p(h), cls, 256)
            if cls.value.startswith(_GUARD_PREFIXES):
                found.append(int(h))
            return True

        u.EnumChildWindows(ctypes.c_void_p(parent),
                           ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                              ctypes.c_void_p)(visit), None)
        added = 0
        for ch in found:
            if ch in _CHILD_NC:
                continue
            old = _NC_GET(ctypes.c_void_p(ch), _GWLP_WNDPROC)
            if not old:
                continue
            _CHILD_NC[ch] = (old, _NC['child_proc'], int(parent))
            _NC_SET(ctypes.c_void_p(ch), _GWLP_WNDPROC, ptr)
            added += 1
        if added:
            _log_always('child hit guard: +%d (total %d)' % (added, len(_CHILD_NC)))
        start_child_guard_loop(parent)      # 第 368 项：以后新造的子窗也要补挂
        return added
    except Exception as e:
        _log_always('child hit guard failed: %r' % (e,))
        return 0


_GUARD_LOOP = {'parents': set(), 'lock': threading.Lock()}


def start_child_guard_loop(parent):
    """每 8 秒补挂一次。WebView2 会在导航/新建窗口/崩溃恢复时重造子窗，
    建窗时挂一次不够：后来出现的宿主面板没人管，顶部那 6px 缩放热区又会失效。"""
    if not parent:
        return
    key = int(parent)
    with _GUARD_LOOP['lock']:
        if key in _GUARD_LOOP['parents']:
            return
        _GUARD_LOOP['parents'].add(key)

    def loop():
        while True:
            time.sleep(8)
            try:
                install_child_hit_guard(key)
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True, name='child-hit-guard').start()



def _work_rect(hwnd):
    """取窗口所在显示器的「工作区」（不含任务栏）。第 343 项最大化时要用。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint32), ('rcMonitor', wt.RECT),
                    ('rcWork', wt.RECT), ('dwFlags', ctypes.c_uint32)]

    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    mon = u.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)     # MONITOR_DEFAULTTONEAREST
    if not mon:
        return None
    if not u.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
        return None
    return mi.rcWork


def _nc_wndproc(hwnd, msg, wparam, lparam):
    old = _NC['old'].get(int(hwnd))
    try:
        if msg == _WM_NCPAINT and int(hwnd) in _MATTE:
            # 第 363 项：快搜窗那圈非客户区不画的话，DWM 会当玻璃区合成 ——
            # 于是「后面是什么就透出什么」：深色界面上挂一条白边，其实就是背后
            # 那个白窗口。这里自己用 GDI 把那三条边涂成界面底色，彻底不透明。
            try:
                _paint_matte_frame(int(hwnd))
                return 0
            except Exception as _e:
                _log_always('ncpaint FAILED: %r' % (_e,))
        if msg == _WM_NCCALCSIZE and wparam == 1:
            import ctypes
            import ctypes.wintypes as wt
            if ctypes.windll.user32.IsZoomed(ctypes.c_void_p(hwnd)):
                # 最大化：带 WS_THICKFRAME 的窗口在最大化时会比工作区各边多出
                # 10px（客户区原点能到 (0,0)，说明窗口矩形已经越界）。
                # 直接把「建议的客户区」改成工作区再交回去，页面就不会被裁掉一圈。
                work = _work_rect(hwnd)
                if work is not None and lparam:
                    rc = ctypes.cast(lparam, ctypes.POINTER(wt.RECT)).contents
                    rc.left, rc.top, rc.right, rc.bottom = (
                        work.left, work.top, work.right, work.bottom)
                    return 0
                return _NC_CALL(old, hwnd, msg, wparam, lparam)
            # 非最大化：**只把顶边归零**，左右下仍留系统自己的边框。
            # 顶边那 10px 就是 caption 带（DWM 画的），归零后它彻底不存在；
            # 左右下留着，边缘拖拽缩放交给 DefWindowProc（WebView 铺满客户区，
            # 跨进程子窗吃不住命中测试，靠「自己算热区」那条路走不通）。
            b = _frame_insets(hwnd)
            if b > 0 and lparam:
                rc = ctypes.cast(lparam, ctypes.POINTER(wt.RECT)).contents
                rc.left += b
                rc.right -= b
                rc.bottom -= b
                # rc.top 不动 → 客户区顶到窗口最上沿
            return 0
        if msg == _WM_NCHITTEST:
            # 非客户区归零后 DefWindowProc 一律回 HTCLIENT → 边缘拖不动，
            # 所以这里自己算那圈 6px 的缩放热区。
            code = _hit_test(hwnd, lparam)
            if code is not None:
                return code
    except Exception:
        pass
    res = _NC_CALL(old, hwnd, msg, wparam, lparam)
    if msg in (_WM_ACTIVATE, _WM_SIZE, _WM_DPICHANGED, _WM_THEMECHANGED):
        # 第 343 项：系统会在激活切换 / 缩放 / DPI 变化 / 换主题时把边框色打回默认。
        # 顶边已经归零（那条 caption 带不存在了），但左/右/下还留着 10px 边框，
        # 颜色不对就会变成三条白边 —— 这里按事件补刷，**不再需要每 3 秒轮询的线程**。
        try:
            apply_frame(hwnd, redraw=(msg == _WM_DPICHANGED))
            # 第 363 项：窗口长高时新露出来的那圈边框会用当前底色补刷，**原来那段**
            # 却保留建窗时的旧色（深色界面下就是顶部那段白边）。这里连 frame 一起标脏。
            if msg == _WM_SIZE:
                ctypes.windll.user32.RedrawWindow(
                    ctypes.c_void_p(hwnd), None, None, 0x0001 | 0x0004 | 0x0100)
        except Exception:
            pass
    return res



def _nc_make_proc():
    import ctypes
    proto = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                              ctypes.c_ssize_t, ctypes.c_ssize_t)
    return proto(_nc_wndproc)


import ctypes  # noqa: E402  （模块级 import 放这儿是为了上面那段 ctypes 用得上）

_NC_CALL = ctypes.windll.user32.CallWindowProcW
_NC_CALL.restype = ctypes.c_ssize_t
_NC_CALL.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                     ctypes.c_ssize_t, ctypes.c_ssize_t]
_NC_SET = ctypes.windll.user32.SetWindowLongPtrW
_NC_SET.restype = ctypes.c_void_p
_NC_SET.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
_NC_GET = ctypes.windll.user32.GetWindowLongPtrW
_NC_GET.restype = ctypes.c_void_p
_NC_GET.argtypes = [ctypes.c_void_p, ctypes.c_int]


def install_nc_guard(hwnd, force=False):
    """第 343 项：挂上 WM_NCCALCSIZE 子类化，把 10px 非客户区归零（重复调用安全）。

    ⚠️ WinForms 在窗体第一次真正 Show / 重建句柄时会把自己的窗口过程装回去，
    我们建窗时挂的那份会被顶掉（第 343 项实测：装完内缩还是 10px）。
    所以调用方不能信一次返回值，要用 `frame_insets() > 0` 判断，必要时 force=True 重挂。
    """
    if not hwnd:
        return False
    key = int(hwnd)
    if key in _NC['ours'] and not force:
        return True
    try:
        if _NC['proc'] is None:
            _NC['proc'] = _nc_make_proc()
        ptr = ctypes.cast(_NC['proc'], ctypes.c_void_p).value    # 传整数，别传 c_void_p 对象
        old = _NC_GET(ctypes.c_void_p(hwnd), _GWLP_WNDPROC)
        if not old:
            return False
        # 已经是我们的过程（force 重挂场景）就别把「自己」记成旧过程，否则递归到死
        if key in _NC['ours'] and old == ptr and key in _NC['old']:
            old = _NC['old'][key]
        _NC['old'][key] = old
        _NC_SET(ctypes.c_void_p(hwnd), _GWLP_WNDPROC, ptr)
        _NC['ours'].add(key)
        # 样式没变也要让系统重算一次客户区
        try:
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, _SWP_FLAGS)
        except Exception:
            pass
        _log_always('nc_guard installed hwnd=%s force=%s insets=%s' % (
            hwnd, force, frame_insets(hwnd)))
        return True
    except Exception as e:
        _log_always('nc_guard failed: %r' % (e,))
        return False



def snap_zone(rect, work, tol=6):
    """第 289 项：这扇窗现在落在哪个吸附区？返回 'max' / 'left' / 'right' / None。

    rect / work 都是 (left, top, right, bottom)。判定规则从「越过就算」改成
    **「只认正好贴住的那条容差带」**：
      · 顶边：|top - work.top| <= tol          → 最大化
      · 左边缘：|left - work.left| <= tol      → 左半屏
      · 右边缘：|right - work.right| <= tol    → 右半屏
    这样把窗口往屏幕外推（right > work.right + tol）就**不再**被吸回来 ——
    用户「临时挪开一半」的动作得以保留，只有真正贴边才吸附。
    """
    try:
        rl, rt, rr, rb = rect
        wl, wt, wr, wb = work
        if abs(int(rt) - int(wt)) <= tol:
            return 'max'
        if abs(int(rl) - int(wl)) <= tol:
            return 'left'
        if abs(int(rr) - int(wr)) <= tol:
            return 'right'
    except Exception:
        pass
    return None


def _win_ptr_funcs():
    """64 位必须用 GetWindowLongPtrW：不设 restype 会被截成 32 位，样式位就乱了。"""
    import ctypes
    import ctypes.wintypes          # titlebar_* 要用 RECT，别指望别的函数先导入
    u = ctypes.windll.user32
    get = getattr(u, 'GetWindowLongPtrW', None) or u.GetWindowLongW
    put = getattr(u, 'SetWindowLongPtrW', None) or u.SetWindowLongW
    get.restype = ctypes.c_ssize_t
    get.argtypes = [ctypes.c_void_p, ctypes.c_int]
    put.restype = ctypes.c_ssize_t
    put.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.IsZoomed.argtypes = [ctypes.c_void_p]
    u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return ctypes, u, get, put


def caption_off(hwnd, off=True):
    """摘掉 / 恢复系统标题栏。返回 True 表示样式位确实处于目标状态。"""
    if not hwnd:
        return False
    try:
        ctypes, u, get, put = _win_ptr_funcs()
        h = ctypes.c_void_p(hwnd)
        style = get(h, _GWL_STYLE)
        _log('caption_off enter hwnd=%s style=%s off=%s' % (hex(hwnd), hex(style), off))
        if not style:
            return False
        want = (style & ~_WS_CAPTION) if off else (style | _WS_CAPTION)
        if want != style:
            put(h, _GWL_STYLE, want)
            u.SetWindowPos(h, None, 0, 0, 0, 0, _SWP_FLAGS)
            try:
                u.RedrawWindow(h, None, None,
                               _RDW_FRAME | _RDW_INVALIDATE)
            except Exception:
                pass
        after = get(h, _GWL_STYLE)
        if want != style:
            _log('caption_off hwnd=%s before=%s want=%s after=%s off=%s'
                 % (hex(hwnd), hex(style), hex(want), hex(after), off))
        return bool(after & _WS_CAPTION) != bool(off)
    except Exception as e:
        _log('caption_off failed: %r' % (e,))
        return False


def recheck_caption(hwnd, delay=0.0):
    """过一会儿再确认一次系统标题栏真的没回来（第 181 项）。

    为什么需要：WinForms 会在窗口布局 / DPI 变化后按 `FormBorderStyle`
    重新写回 `WS_CAPTION`，时机**晚于** after_start —— 源码运行时实测：
    日志已经打过 stripped，一秒后读样式位又是 True，前端于是画出第二条标题栏。
    这里只做「读一次、不对就重摘」，幂等、无副作用。
    """
    try:
        if delay:
            time.sleep(delay)
        if not hwnd:
            return False
        return caption_off(hwnd, True)
    except Exception as e:
        _log('recheck_caption failed: %r' % (e,))
        return False


def caption_drag(hwnd):
    """按住自绘标题栏拖动：把这一下当成「按住系统标题栏」，后面交给系统。
    用 SendMessage 而不是 PostMessage —— 拖动循环要在这条消息里跑完。"""
    if not hwnd:
        return False
    try:
        ctypes, u, _g, _p = _win_ptr_funcs()
        u.ReleaseCapture()
        u.SendMessageW(ctypes.c_void_p(hwnd), _WM_NCLBUTTONDOWN, _HTCAPTION, 0)
        return True
    except Exception as e:
        _log('caption_drag failed: %r' % (e,))
        return False


def window_cmd(hwnd, cmd):
    """自绘标题栏三键：min / max（切换）/ restore。

    使用 PostMessage 发送 WM_SYSCOMMAND（_SC_MINIMIZE / _SC_MAXIMIZE / _SC_RESTORE）：
    异步非阻塞，不占用/不等待 UI 线程消息循环，杜绝跨线程 ShowWindow 导致的死锁与假死（未响应）。
    """
    if not hwnd:
        return False
    try:
        ctypes, u, _g, _p = _win_ptr_funcs()
        h = ctypes.c_void_p(hwnd)
        if cmd == 'min':
            u.PostMessageW(h, _WM_SYSCOMMAND, _SC_MINIMIZE, 0)
        elif cmd == 'max':
            is_max = bool(u.IsZoomed(h))
            u.PostMessageW(h, _WM_SYSCOMMAND, _SC_RESTORE if is_max else _SC_MAXIMIZE, 0)
        elif cmd == 'restore':
            u.PostMessageW(h, _WM_SYSCOMMAND, _SC_RESTORE, 0)
        else:
            return False
        return True
    except Exception as e:
        _log_always('window_cmd(%s) failed: %r' % (cmd, e))
        return False


def window_maximized(hwnd):
    try:
        ctypes, u, _g, _p = _win_ptr_funcs()
        return bool(hwnd) and bool(u.IsZoomed(ctypes.c_void_p(hwnd)))
    except Exception:
        return False


# ---------------------------------------------------------------- pywebview 门闩
# 第 267 项：pywebview 的 Window.show() / load_url() / evaluate_js() 全被
# _api_call() 包着，进来先 event.wait(20)。其中 evaluate_js 等的是
# events._pywebviewready —— 它只在 window.py:285 的 load_url() 里被 clear()，
# 靠页面重载时注入的 JS 再置回去。一旦某次重载没把它置回来，之后**每一次**
# evaluate_js 都空等 20 秒、再抛 WebViewException: Main window failed to start。
# 真机日志（13:14:32 begin → 13:14:52 报错，连续四次）就是这个形状：
# 托盘「设置 / 关于 / 反馈」各点一次，20 秒后掉异常，面板**永远打不开**；
# 而每次点击的线程 20 秒后还会再 show() 一次，用户关掉的详情栏又被顶回来。
#
# 修法不是去等门闩，而是绕开它：直接调 winforms 后端（BrowserView.instances），
# 与 pywebview 内部同一套实现，只是不经过那层事件等待。

def _pv_backend():
    try:
        from webview.platforms import winforms as _wf
        if hasattr(_wf, 'BrowserView'):
            return _wf
    except Exception:
        pass
    return None


def _pv_has(wf, uid):
    """后端里真有这个 uid 的窗口实例吗（没有就别假装成功）。"""
    try:
        return bool(wf) and uid in getattr(wf.BrowserView, 'instances', {})
    except Exception:
        return False


def _ui_set_visible(ctrl, tag=''):
    """把「WebView2 控件 Visible=true」送回 WinForms UI 线程去做。

    第 329 项：14:36 真机死锁。托盘线程（on_menu → show_main）在这里**直接**写
    `ctrl.Visible = True`；跨线程访问 WinForms 控件要等 UI 线程来取，UI 线程那会儿
    正忙着建窗/合成，两边互等。现场 py-spy 抓得清清楚楚：
      MainThread        → create_window (winforms.py:834)
      Thread-205 on_menu→ _force_webview_visible (app_window.py:3083)   ← 卡在这
    窗口 Responding=False、8765 不再接受连接，用户看到的就是「一点开详情页整台机器
    卡死，鼠标键盘都不动」，只能强杀进程。

    改成 BeginInvoke：只往 UI 线程排队，**从不同步等它**；UI 忙就晚一点显示，绝不
    把调用方（托盘线程 / HTTP 线程）挂死。
    """
    def _apply():
        try:
            ctrl.Visible = True
        except Exception as e:
            _log('force webview visible (%s) apply FAILED: %r' % (tag, e))

    try:
        from System import Action        # pythonnet（只在这条路径上要）
        if getattr(ctrl, 'InvokeRequired', True):
            ctrl.BeginInvoke(Action(_apply))
        else:
            _apply()
        return True
    except Exception as e:
        _log('force webview visible (%s) marshal FAILED: %r' % (tag, e))
        return False


def pv_show(win):
    """显示窗口（绕过 _shown_call）。成功返回 True。"""
    if win is None:
        return False
    uid = str(getattr(win, 'uid', '') or '')
    wf = _pv_backend()
    if _pv_has(wf, uid):
        try:
            wf.show(uid)
            return True
        except Exception as e:
            _log_always('pv_show backend FAILED: %r' % (e,))
    try:
        win.show()
        return True
    except Exception as e:
        # 两条路都失败 → 交给 show_main 的兜底（把快搜窗放回来），别静默吞掉
        _log_always('pv_show pywebview FAILED: %r' % (e,))
        raise


def pv_hide(win):
    if win is None:
        return False
    uid = str(getattr(win, 'uid', '') or '')
    wf = _pv_backend()
    if _pv_has(wf, uid):
        try:
            wf.hide(uid)
            return True
        except Exception as e:
            _log_always('pv_hide backend FAILED: %r' % (e,))
    try:
        win.hide()
        return True
    except Exception as e:
        _log_always('pv_hide pywebview FAILED: %r' % (e,))
        return False


def pv_load_url(win, url):
    """整页导航（绕过 _loaded/_ready 门闩，也不清 pywebview 的 ready 事件）。"""
    if win is None:
        return False
    uid = str(getattr(win, 'uid', '') or '')
    wf = _pv_backend()
    if _pv_has(wf, uid):
        try:
            wf.load_url(url, uid)
            try:
                win.real_url = url
            except Exception:
                pass
            return True
        except Exception as e:
            _log_always('pv_load_url backend FAILED: %r' % (e,))
    try:
        win.load_url(url)
        return True
    except Exception as e:
        _log_always('pv_load_url pywebview FAILED: %r' % (e,))
        return False


def pv_eval(win, script):
    """执行 JS（绕过 _pywebviewready 门闩）。供「导航后补一刀」这类场景用。"""
    if win is None:
        return None
    uid = str(getattr(win, 'uid', '') or '')
    wf = _pv_backend()
    if _pv_has(wf, uid):
        try:
            return wf.evaluate_js(script, uid, True)
        except Exception as e:
            _log_always('pv_eval backend FAILED: %r' % (e,))
    try:
        return win.evaluate_js(script)
    except Exception as e:
        _log_always('pv_eval pywebview FAILED: %r' % (e,))
        return None


def _bump_hide(app):
    """用户关窗：作废在飞/排队的 show_main（第 267 项）。

    放进模块函数而不是 App 方法，是因为 preflight 会用 `App.__new__` 造壳
    直接调 show_main/hide 路径，壳上没有 `_hide_seq` 字段。"""
    try:
        app._want_main = False
    except Exception:
        pass
    try:
        app._hide_seq = int(getattr(app, '_hide_seq', 0) or 0) + 1
    except Exception:
        pass
    return int(getattr(app, '_hide_seq', 0) or 0)


def pv_heal_gate(win, tries=12, interval=0.25):
    """门闩被清空、页面其实活着时，用后端探针证明一次再把事件补回去。

    只补 `_pywebviewready`：它是「页面已就绪」的缓存，不是业务状态；
    页面真死了探针拿不到 readyState，就不会乱补。"""
    if not str(getattr(win, 'uid', '') or ''):
        return False                       # 没 uid 的是测试替身，别去探
    ev = getattr(getattr(win, 'events', None), '_pywebviewready', None)
    if ev is None:
        return False
    for _ in range(max(1, int(tries))):
        if ev.is_set():
            return True
        time.sleep(interval)
        try:
            state = pv_eval(win, 'document.readyState')
        except Exception:
            state = None
        if state in ('interactive', 'complete'):
            ev.set()
            _log_always('ready 门闩曾被清空：页面实测可用（readyState=%s），已重设' % state)
            return True
    return bool(ev.is_set())


def round_corners(hwnd):
    """Win11 的 DWM 圆角；Win10 上这个属性号不被识别，返回非 0，忽略即可。
    frameless 窗口是直角矩形，光靠 CSS 圆角会在四角留白，必须让系统裁。"""
    if not hwnd:
        return False
    try:
        import ctypes
        v = ctypes.c_int(2)          # DWMWCP_ROUND
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(v), 4) == 0
    except Exception:
        return False


class _MARGINS(ctypes.Structure):
    _fields_ = [('l', ctypes.c_int), ('r', ctypes.c_int),
                ('t', ctypes.c_int), ('b', ctypes.c_int)]


def soft_shadow(hwnd):
    """给无边框窗口要一圈系统投影。

    FormBorderStyle.None 的窗口 DWM 默认不画阴影，贴在桌面上像一张纸片 ——
    这正是「小窗」最缺的那点质感。把 frame 扩 1px 进客户区（内容位置一点没动），
    DWM 就按带边框窗口对待，投出 Win11 那种柔和的光。

    这是个纯增益项：属性设不上就只是没阴影，窗口照常工作。"""
    if not hwnd:
        return False
    try:
        m = _MARGINS(1, 1, 1, 1)
        return ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m)) == 0
    except Exception:
        return False


def frame_insets(hwnd):
    """第 343 项：**顶边**非客户区有多厚（只有它是那条 caption 带）。

    第 343 项之后左右下仍留系统边框（那是拖拽缩放用的，正常），所以这里不能再取
    max(...) —— 否则永远 >0，重挂循环会一直以为「归零没成功」，白跑六轮
    （第 96 次部署实测 `nc_guard ... force=True insets=11` 连打 6 次就是这个 bug）。
    """
    if not hwnd:
        return 0
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        wr = wt.RECT()
        u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(wr))
        pt = wt.POINT(0, 0)
        u.ClientToScreen(ctypes.c_void_p(hwnd), ctypes.byref(pt))
        return max(0, pt.y - wr.top)
    except Exception:
        return 0


def main_window_size(sw, sh, saved=None):
    """主窗口默认大小：跟着屏幕走，而不是写死一个数。

    以前按 40%/52% 再夹到 980×660，在 1080p 上只有 860×560 —— 一屏能放下的东西
    偏偏只给一小块。现在按「宽 55% / 高 66%」算，再夹到 [960×640, 1600×1000]，
    并且永远不超过屏幕的 92%×90%。1K / 2K / 4K 都能得到合适的一块。

    saved 是上次用户自己拖出来的尺寸（settings.json 的 win_w/win_h），
    只要它还落在屏幕内就优先用它 —— 别每次启动都把人调好的窗口重置。
    """
    try:
        sw, sh = int(sw), int(sh)
    except Exception:
        sw, sh = 1920, 1080
    if saved:
        try:
            w, h = int(saved[0]), int(saved[1])
            # 第 206 项：下限必须与 _remember_size() 写入条件（700/480）一致。
            # 以前写 1100/560，用户把窗口拖成 886×836 后每次启动都被当成「不合理」
            # 丢掉，强行回到 1306×900 —— 这就是「拖不动 / 拖了没用」的真根因。
            # 上限只防「比屏幕还大」的坏数据，不再把用户拖大的尺寸压回 1400×900。
            if 700 <= w <= sw + 40 and 480 <= h <= sh + 40:
                return min(w, 1600), min(h, 1300)
        except Exception:
            pass
    # 第 206 项：默认改成「窄一点、高一点」—— 用户给的图 2 是 886×836，
    # 而老默认 1306×900 是宽扁的图 1。新装用户也给接近图 2 的比例。
    mw = int(min(max(980, sw * 0.40), 1150))
    mh = int(min(max(880, sh * 0.60), 1050))
    # 可用空间优先于「合适尺寸」：留 8% 宽 / 10% 高给任务栏和边距。
    # 2026-11-07 修：原来下限写死 `max(520, ...)`，1366×768 笔记本设 150% 时
    # 逻辑屏只有 910×512、扣掉任务栏可用 472 —— 520 的硬下限直接把窗口顶出屏幕，
    # 标题栏跑到屏幕外，用户既拖不动也点不到「关闭」，只能任务管理器杀掉。
    # 这类「小屏 + 高缩放」在触屏本和老花眼用户里很常见，比 4K 更容易踩到。
    max_w = max(640, int(sw * 0.92))
    max_h = max(420, int(sh * 0.90))
    mw = min(max(760, mw), max_w)
    mh = min(max(420, mh), max_h)
    return mw, mh


def main_min_size(mw, mh):
    """用户能把主窗拖到的最小尺寸。

    同样不能超过屏幕可用空间：下限比屏幕还高时，窗口最小也超出可视区，
    等于「窗口关不掉」。正常尺寸下结果和旧常量一致（760×520）。

    第 377 项：宽度下限从 560/0.62 抬到 930。原因是界面里有两条固定宽的竖栏 ——
    左侧筛选 172 + 详情面板 300，加列表四列**能正常显示内容**的宽度（名称 ≥160、
    类型 76、大小 70、修改时间 118、图标列 16、列间距 24 ≈ 464）和内边距 24，
    合计 **≈960px**。实测过：把窗口缩到 806px 宽、详情开着，修改时间列被切成
    「2026/」、类型列变成「文档 · C…」，也就是用户说的「跟截图不一样」。
    所以下限取 930，再窄就拖不进去；屏幕本身就比 930 窄的（1366×768@150% 逻辑屏
    910、窗口 837）按默认尺寸锁住，不硬顶出屏幕。
    """
    w = min(int(mw), max(930, int(mw * 0.62)))
    h = max(360, min(520, int(mh * 0.62)))
    return w, h


def env_snapshot(hwnd=None):
    """一行环境描述（系统版本 / 物理分辨率 / 显示器数量 / 缩放）。

    写进 debug.log 的启动行。用户报「窗口不对、看不到、太大太小」时，第一件事
    永远是问分辨率多少、缩放多少、几块屏 —— 这一行把答案直接留在现场，
    省掉一轮来回。用 _log_always 写，不依赖 SB_DEBUG。
    """
    bits = []
    try:
        import platform
        bits.append('win=%s/%s' % (platform.release(), platform.version()))
    except Exception:
        pass
    u, wt = _win()
    if u:
        try:
            bits.append('screen=%dx%d' % (int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))))
        except Exception:
            pass
        try:
            n = [0]
            PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.POINTER(wt.RECT), ctypes.c_double)

            def _cb(hmon, hdc, lprc, data):
                n[0] += 1
                return 1
            u.EnumDisplayMonitors(None, None, PROC(_cb), None)
            bits.append('monitors=%d' % n[0])
        except Exception:
            pass
        try:
            bits.append('dpr=%.2f' % window_dpr(hwnd))
            sw, sh = screen_logical_size(hwnd)
            bits.append('logical=%dx%d' % (sw, sh))
        except Exception:
            pass
    return ' '.join(bits)


def window_dpr(hwnd=None):
    """窗口所在显示器的缩放比：96→1.0、120→1.25、144→1.5、192→2.0。

    只信 `GetDpiForWindow`。**别用 `GetDpiForSystem`**：它返回的是「登录会话建立时」
    的系统 DPI，用户登录后再改缩放不会更新（本机实测返回 96，而窗口真实是 144）。
    `GetDpiForMonitor` 同理不可靠——MSDN 明确它不适用于 Per-Monitor v2 应用，
    本机对 150% 的屏也返回 96。所以拿不到窗口句柄时只能退回系统值，但那只是估值。
    """
    u, _ = _win()
    if not u:
        return 1.0
    try:
        dpi = 0
        if hwnd:
            dpi = int(u.GetDpiForWindow(ctypes.c_void_p(hwnd)) or 0)
        if not dpi:
            dpi = int(u.GetDpiForSystem() or 96)
        return (dpi or 96) / 96.0
    except Exception:
        return 1.0


def screen_logical_size(hwnd=None):
    """逻辑像素的屏幕尺寸（100% 缩放时等于分辨率本身）。

    GetSystemMetrics 在 Per-Monitor v2 进程里给的是物理像素，所以必须除以窗口
    真实 DPR；传 hwnd 才算得准，不传只能用系统 DPI 估（见 window_dpr 的说明）。
    pywebview 的 move/resize 收的都是逻辑像素，两者单位必须一致，否则窗口会偏到屏幕外。
    """
    u, wt = _win()
    if not u:
        try:
            s = webview.screens[0]
            return s.width, s.height
        except Exception:
            return 1920, 1080
    try:
        sc = window_dpr(hwnd)
        return int(u.GetSystemMetrics(0) / sc), int(u.GetSystemMetrics(1) / sc)
    except Exception:
        return 1920, 1080


def get_client_size(hwnd):
    """客户区（不含标题栏/边框）的物理像素尺寸。CSS 视口 = 这个值 / dpr。"""
    u, wt = _win()
    if not u or not hwnd:
        return 0, 0
    try:
        rc = wt.RECT()
        u.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rc))
        return int(rc.right - rc.left), int(rc.bottom - rc.top)
    except Exception:
        return 0, 0


def calc_window_size(css_w, css_h, dpr, style, exstyle):
    """由客户区尺寸（CSS/逻辑像素）算出整窗物理尺寸（含标题栏与边框）。

    单独抽出来是为了能单测：它的结果只取决于传给它的 dpr，所以 96/120/144/168/192/240
    六档缩放、1K/2K/4K 各种分辨率都能在没有对应硬件的机器上验证（tests/test_dpi_fit.py）。
    """
    u, wt = _win()
    css_w, css_h = max(1, int(css_w)), max(1, int(css_h))
    tw = int(round(css_w * dpr))
    th = int(round(css_h * dpr))
    if not u:
        return tw, th
    rc = wt.RECT(0, 0, tw, th)
    try:
        try:
            u.AdjustWindowRectExForDpi(ctypes.byref(rc), style, False, exstyle,
                                       int(round(dpr * 96)))
        except Exception:
            # Win10 1607 之前没有 *ForDpi 版本（那时也不会有 >100% 的混合缩放）
            u.AdjustWindowRectEx(ctypes.byref(rc), style, False, exstyle)
    except Exception:
        return tw, th
    return int(rc.right - rc.left), int(rc.bottom - rc.top)


def set_client_size(hwnd, css_w, css_h):
    """把客户区精确调成 css_w × css_h（CSS/逻辑像素），返回是否真的动了。

    两个关键点，都是踩过坑换来的：
    1. **用 SetWindowPos，不用 pywebview 的 resize()**。后者走 WinForms 的 Bounds
       赋值，会把刚 hide 掉的窗口重新显示出来；SetWindowPos 带 SWP_NOACTIVATE
       只改几何，隐藏的窗口不会冒头，可见的窗口也不会抢焦点。
    2. **按客户区算，不按整窗算**。WinForms 的 Width/Height 含标题栏和边框，
       直接照抄会让 CSS 视口少十几个像素；AdjustWindowRectExForDpi 能把
       「客户区尺寸 + 样式 → 整窗尺寸」在不同 DPI 下算准（96/120/144/168/192/240 通用）。
    """
    u, wt = _win()
    if not u or not hwnd:
        return False
    try:
        dpr = window_dpr(hwnd)
        style = u.GetWindowLongW(ctypes.c_void_p(hwnd), -16) & 0xFFFFFFFF      # GWL_STYLE
        exstyle = u.GetWindowLongW(ctypes.c_void_p(hwnd), -20) & 0xFFFFFFFF    # GWL_EXSTYLE
        w, h = calc_window_size(css_w, css_h, dpr, style, exstyle)
        SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x2, 0x4, 0x10, 0x200
        u.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, w, h,
                       SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        return True
    except Exception as e:
        _log('set_client_size FAILED: %r' % (e,))
        return False


def needs_fit(cur_w, cur_h, tw, th):
    """CSS 视口（cur）和设计尺寸（target）差得够不够多，值得动窗口。

    容差分两级，故意放得很宽：
      · 像素差在「标题栏 + 边框」量级以内（实测宽 ~15、高 ~37，各主题略有差别）→ 不算问题；
      · 或者相对偏差 ≤5%。
    这样 100% 缩放（视口只比设计少一圈边框）和 150% 缩放（pywebview 已经正确放大，
    同样只差一圈边框）都会落进「不用动」，绝大多数用户启动时这里等于空操作。
    只有真算错——比如某天 pywebview 的换算失效，150% 下视口掉成 843×569——才会返回 True。
    """
    dx, dy = abs(cur_w - tw), abs(cur_h - th)
    dev = max(dx / max(1, tw), dy / max(1, th))
    return not ((dx <= 24 and dy <= 48) or dev <= 0.05)


def notify_resize(hwnd):
    """给窗口补发一条 WM_SIZE，让 WebView2 按当前客户区重建渲染表面。

    只发消息、不改几何：同尺寸的 resize() 在 WinForms 上等于再赋一次 Bounds，
    会把可见性 / 焦点一起牵动，代价比收益大。
    """
    u, _ = _win()
    if not u or not hwnd:
        return False
    try:
        cw, ch = get_client_size(hwnd)
        if cw <= 0 or ch <= 0:
            return False
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_void_p, ctypes.c_void_p]
        u.SendMessageW.restype = ctypes.c_ssize_t
        WM_SIZE, SIZE_RESTORED = 0x0005, 0
        u.SendMessageW(ctypes.c_void_p(hwnd), WM_SIZE, ctypes.c_void_p(SIZE_RESTORED),
                       ctypes.c_void_p(((ch & 0xFFFF) << 16) | (cw & 0xFFFF)))
        return True
    except Exception as e:
        _log('notify_resize FAILED: %r' % (e,))
        return False


def topmost(hwnd, tries=12, pin=True):
    """把快搜窗拉到最前并抢到键盘焦点。

    这件事比看上去难，三个坑叠在一起：
    1. pywebview 的 show() 只是把动作排进 GUI 线程队列就返回，紧接着设前台会打在
       「窗口还没显示」的空档上 —— 所以要重试。
    2. 窗口已经显示、只是被别的程序盖住时，WinForms 的 Activate() 会被前台锁定规则拒掉。
    3. 真正管用的是「模拟一次 Alt 抬起」：系统看到本线程刚发过输入，就放行
       SetForegroundWindow（老牌做法，Wox / PowerToys Run 都用）。单按 Alt 本身
       不产生字符，焦点马上被我们抢走，副作用是看不见的。

    抢不到就返回 False —— 调用方需要知道，因为「按键打到别的程序里」比不弹窗更糟。

    ⚠️ pin=True 会设 HWND_TOPMOST，**只给快搜窗（mini）用**。
    主窗口必须传 pin=False：第 192 项实测，show_main 复用 topmost(hwnd) 会把
    详细搜索页永久钉在所有程序前面，用户想用别的软件只能先关掉或缩小它。
    pin=False 会主动设 HWND_NOTOPMOST，顺手把历史遗留的置顶状态也清掉；
    抢前台（BringWindowToTop / Alt 解锁 / SetForegroundWindow）完全保留。
    """
    if not hwnd:
        return False
    try:
        import win32api
        import win32con
        import win32gui
        import win32process
    except Exception:
        return False
    for i in range(max(1, tries)):
        try:
            after = win32con.HWND_TOPMOST if pin else win32con.HWND_NOTOPMOST
            win32gui.SetWindowPos(hwnd, after, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            if i == 0:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            # AttachThreadInput：把自己的输入队列挂到当前前台线程上，绕过前台锁定
            fg = win32gui.GetForegroundWindow()
            tids = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
            tid_fg, pid_fg = tids[0], tids[1]
            tid_me = win32api.GetCurrentThreadId()
            attached = False
            try:
                if tid_fg and tid_fg != tid_me and pid_fg != os.getpid():
                    win32process.AttachThreadInput(tid_me, tid_fg, True)
                    attached = True
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            finally:
                if attached:
                    try:
                        win32process.AttachThreadInput(tid_me, tid_fg, False)
                    except Exception:
                        pass
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            if i in (2, 6):
                try:      # Alt 解锁：让系统认为本线程刚发过用户输入
                    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                if win32gui.GetForegroundWindow() == hwnd:
                    return True
        except Exception:
            pass
        time.sleep(0.04)
    return False


def foreground_is(hwnd):
    if not hwnd:
        return False
    try:
        import win32gui
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _dialog_kind(which):
    """原生对话框类型常量（第 241 项）。

    pywebview 新的 `webview.FileDialog` 枚举是正式 API；
    旧的 `webview.FOLDER_DIALOG` / `OPEN_DIALOG` 已标记 deprecated
    （调用时还会往 stderr 打一行警告），将来会删掉。这里优先用新的，
    旧的只作兜底。
    """
    try:
        from webview import FileDialog
        return FileDialog.FOLDER if which == 'FOLDER' else FileDialog.OPEN
    except Exception:
        if webview is None:
            return 20 if which == 'FOLDER' else 10
        return (webview.FOLDER_DIALOG if which == 'FOLDER'
                else webview.OPEN_DIALOG)


def _is_visible(hwnd):
    """窗口是不是已显示出来（IsWindowVisible）。用来判断 pywebview 的 show() 有没有生效——
    hide 之后再 show，.NET Form.Visible 偶尔卡在 False，需要 Win32 ShowWindow 兜底。"""
    if not hwnd:
        return False
    try:
        import win32gui
        return bool(win32gui.IsWindowVisible(hwnd))
    except Exception:
        return False


def revive_webview(hwnd, quiet=False):
    """把 hide 之后没跟着 show 的 WebView2 渲染子窗口重新显示（治「整块白窗口」）。

    `--silent` 启动会先 hide 整个主窗口，连 WebView2 的 Chrome_WidgetWin_* 一起
    设成 WS_VISIBLE=0。之后 pywebview 的 Form.Show() / Win32 SW_RESTORE 只恢复
    WinForms 外壳，Chromium 的渲染层还藏着 —— 用户看到的是一张白底板，而服务、
    页面、静态资源其实全都正常（无头浏览器打开同一个 URL 能完整渲染）。
    2026-11-06 实测：silent 启动后从托盘打开，主窗口 4 个子窗口里 3 个是 hidden。

    只在真的发现隐藏子窗口时才动作并留一行日志，正常显示不产生任何副作用。
    quiet=True 供轮询自愈用：每轮都写日志会把 debug.log 灌满。"""
    if not hwnd:
        return 0
    try:
        u = ctypes.windll.user32
        u.SetWindowPos.restype = ctypes.c_int
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_uint]
        PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        cls = ctypes.create_unicode_buffer(256)
        hidden = []

        def _cb(h, _l):
            hh = ctypes.c_void_p(h or 0)
            u.GetClassNameW(hh, cls, 256)
            name = cls.value
            if (name.startswith('Chrome_') or 'D3D' in name) and not u.IsWindowVisible(hh):
                hidden.append(h)
            return 1

        u.EnumChildWindows(ctypes.c_void_p(hwnd), PROC(_cb), ctypes.c_void_p(0))
        if not hidden:
            return 0
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED：
        # FRAMECHANGED 会强制父类向 WebView2 发一次 WM_SIZE，让它重建渲染表面——
        # 只 ShowWindow 不够，WebView2 在下次合成时又会把刚 show 出来的子窗口藏回。
        for h in hidden:
            u.ShowWindow(ctypes.c_void_p(h), 5)          # SW_SHOW
        u.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                      0x1 | 0x2 | 0x20 | 0x40)
        # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
        u.RedrawWindow(ctypes.c_void_p(hwnd), None, None, 0x1 | 0x4 | 0x80 | 0x100)
        if not quiet:
            _log_always('白屏自愈：唤醒 %d 个隐藏的 WebView2 子窗口' % len(hidden))
        return len(hidden)
    except Exception:
        _log_always('白屏自愈失败\n' + traceback.format_exc())
        return 0


def _render_children_visible(hwnd):
    """主窗里 WebView2 的渲染子窗口是不是**全部**可见（False = 白屏现场）。

    白屏的判定标准只能是渲染层：外壳（WindowsForms10.Window…）可见而
    Chrome_WidgetWin_* / D3D 全隐藏时，用户看到的是一块白板。"""
    if not hwnd:
        return False
    try:
        u = ctypes.windll.user32
        if not u.IsWindowVisible(ctypes.c_void_p(hwnd)):
            return False
        PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        cls = ctypes.create_unicode_buffer(256)
        seen = []

        def _cb(h, _l):
            u.GetClassNameW(ctypes.c_void_p(h), cls, 256)
            n = cls.value
            if n.startswith('Chrome_') or 'D3D' in n:
                seen.append(bool(u.IsWindowVisible(ctypes.c_void_p(h))))
            return 1

        u.EnumChildWindows(ctypes.c_void_p(hwnd), PROC(_cb), None)
        return bool(seen) and all(seen)
    except Exception:
        return False


def _late_revive_loop(app, timeout=10.0, interval=0.3):
    """show 之后的轮询式自愈：最多 10 秒，每 0.3 秒唤醒一次渲染层，全可见才收工。

    只补一两次不够：开机的头几秒 IO 忙（bigram 回填 / 首启扫描）时 WebView2 渲染层
    初始化慢，固定拍数的唤醒会刚好错开，用户对着白窗等到自己放弃。正常场景第一轮
    就全可见、立即退出，没有副作用。用户中途收起窗口就停止（别把窗口硬拉回来）。"""
    rounds = 0
    end = time.time() + timeout
    while time.time() < end:
        hwnd = app.hwnd_main or find_hwnd(MAIN_TITLE)
        if hwnd and not _is_visible(hwnd):
            return
        if _render_children_visible(hwnd):
            if rounds > 1:
                _log('白屏自愈：轮询 %d 轮后渲染层恢复' % rounds)
            return
        revive_webview(hwnd, quiet=True)
        app._force_webview_visible()
        rounds += 1
        time.sleep(interval)
    _log_always('白屏自愈：轮询 %.0f 秒后渲染子窗仍不可见（窗口可能仍是白板）' % timeout)


# --------------------------------------------------------------- 标题栏「快搜」按钮
# 主窗标题栏在最小化按钮左边放一枚放大镜小窗（WS_POPUP+TOOLWINDOW+NOACTIVATE，
# owner = 主窗）：点击收起详情窗、唤出快搜窗。它是独立窗口，DWM 盖不到它的绘制。
#
# 位置用「二维 hit-test 扫描」校准：对主窗标题区做行列扫描，收集 HTMINBUTTON /
# HTMAXBUTTON / HTCLOSE 的 bounding box——**不猜按钮条在哪一行**（实测条高随 DPI/
# 主题在 6~52px 漂移，v1 的单行扫描扫不全按钮、rel 偏移盖住了原生最小化键）。
# 扫描结果记成「相对右缘」偏移，挪窗/缩放不失效；DPI 变化 / 激活变化时重校准，
# 每次同步位置前用单点探针自校正（rel 预期的缩小键中心必须是 HTMINBUTTON）。
# 图标是 GDI 自绘放大镜（圆环+柄）——字体名经 CreateFontW 解析失败会画成
# fallback emoji（圆脸），零字体依赖才稳。
# 点击动作丢后台线程（WndProc 里同步调 pywebview 的 show/hide 实测卡死 100 秒），
# hide 之后再用 Win32 SW_HIDE 兜底——后台线程调 pywebview 的 main.hide() 在
# 「可见中的主窗」上实测无效（v1 的验证是在隐藏窗口上做的，假阳性）。
_WM_NCHITTEST, _WM_NCLBUTTONDOWN = 0x0084, 0x00A1
_WM_NCACTIVATE, _WM_MOVE, _WM_SIZE = 0x0086, 0x0003, 0x0005
_WM_DPICHANGED = 0x02E0
_HTMENU, _HTCAPTION, _HTMINBUTTON, _HTMAXBUTTON, _HTCLOSE = 5, 2, 8, 9, 20
_SW_HIDE, _SW_SHOWNOACTIVATE = 0, 8
_GWLP_WNDPROC = -4


class _RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


_SUBCLASSPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_uint,
                                   ctypes.c_size_t, ctypes.c_ssize_t)
_BTNPROC_REF = None          # 按钮窗口过程，同上
_CAPBTN = {'installed': False, 'app': None, 'rel': None, 'hot': False,
           'busy': False, 'btn': 0, 'shown': False}

# 这些 user32/gdi32 函数的 argtypes 是进程级共享状态：lp 一律用 c_void_p（int 和
# 实例都收），收紧成 c_ssize_t 会把 apply_window_icon 之类传 c_void_p 的调用方弄炸
# （第 117 项的图标消失事故）。
u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
u32.DefWindowProcW.restype = ctypes.c_ssize_t
u32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                               ctypes.c_size_t, ctypes.c_ssize_t]








def foreground_desc():
    """谁占了前台——排查「窗口莫名其妙失焦」时唯一有用的线索"""
    try:
        import win32gui
        h = win32gui.GetForegroundWindow()
        if not h:
            return 'None'
        return f'{win32gui.GetWindowText(h)[:40]!r} cls={win32gui.GetClassName(h)} pid={h}'
    except Exception:
        return '?'


def cursor_inside(hwnd):
    """鼠标是不是还停在这个窗口上。用来区分「用户点了别处」和「窗口自己重排导致的瞬时失焦」——
    搜索结果出来时窗口会变高，WebView2 重新布局会短暂丢焦点，
    这时候要是照着失焦就关窗，用户体验就是「刚打完字窗口没了」。"""
    if not hwnd:
        return False
    try:
        import win32api
        import win32gui
        x, y = win32api.GetCursorPos()
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return l <= x < r and t <= y < b
    except Exception:
        return False


# ------------------------------------------------------------------ 页面 API
class Api:
    """主窗口的原生能力：window.pywebview.api.pick_folder()

    注意：pywebview 会把 js_api 对象的**所有公开属性**递归展开成 JS 函数表。
    窗口对象挂在 self.win 上就会被一路递归进 .NET 的 AccessibilityObject（几百层，
    直接 RecursionError，启动日志里那一长串报错就是它）。所以内部引用一律下划线开头。"""

    def __init__(self, win=None):
        self._win = win
        self._app = None           # App 注入：原生对话框后要调 App._force_webview_visible 自愈
        self._open_mini = None      # App 注入：唤出快搜窗（欢迎页按钮用）
        self._hide_main = None      # App 注入：收起详情窗（顶栏/标题栏「快搜」按钮用）

    def set_dark(self, dark, bg=None):
        """页面切主题时同步系统标题栏，免得「黑内容 + 白标题栏」。

        bg 是这套配色的底色（'#RRGGBB'，第 191 项）：暖纸、夜航蓝这些自定义配色
        既不是浅色也不是深色，只靠 dark 布尔值刷出来的框永远差一截。
        """
        try:
            self._last_dark = bool(dark)
            self._last_bg = _hex_rgb(bg) if bg else None
            _LAST_THEME['dark'] = bool(dark)
            _LAST_THEME['bg'] = self._last_bg
            _hw = find_hwnd(MAIN_TITLE)
            ok = dark_caption(_hw, bool(dark))
            # 深色开关会把 caption 色重置回系统值 → 顶部白带回来（第 184 项）。
            # 这里必须用前端报来的 dark：页面认的是 localStorage.sb_theme，
            # 用配置猜会在用户切主题那一刻对不上（深框套浅页）。
            paint_frame(_hw, bool(dark), _hex_rgb(bg))
            # 第 359 项：mini 那圈白边在网页之外，得单独刷窗口背景
            try:
                _app = getattr(self, '_app', None)
                _rgb = self._last_bg or ui_bg_rgb(bool(dark))
                set_window_background(getattr(_app, 'mini', None), _rgb)
                set_window_background(getattr(_app, 'main', None), _rgb)
                matte_frame(getattr(_app, 'hwnd_mini', 0), _rgb)
            except Exception as e:
                _log('set_dark backcolor skipped: %r' % (e,))
            _log('set_dark(%s, %s) → %s' % (bool(dark), bg, ok))
            return ok
        except Exception:
            return False

    def open_mini(self):
        """欢迎页「打开快搜窗口」：交给原生去唤，别让 JS 猜窗口在哪。"""
        try:
            if self._open_mini:
                self._open_mini()
            return True
        except Exception:
            return False

    def hide_main(self):
        """详情窗收起（顶栏「快搜」按钮的后半句）。以前桥上没有这个方法，
        App.tsx 里 `setTimeout(api.hide_main(), 120)` 一直是静默空操作。"""
        try:
            if self._hide_main:
                self._hide_main()
            return True
        except Exception:
            return False

    def read_clipboard_image(self):
        """读取剪贴板图像。"""
        try:
            import snip_ocr
            return snip_ocr.read_clipboard_image()
        except Exception as e:
            return {'has_image': False, 'error': str(e)}

    def ocr_image(self, b64_or_data_url=None):
        """对传入图像执行离线 OCR 识别。"""
        try:
            import snip_ocr
            return snip_ocr.ocr_base64_image(b64_or_data_url)
        except Exception as e:
            return {'ok': False, 'text': '', 'error': str(e)}

    def get_clipboard_history(self, kind='all', q='', limit=60, offset=0):
        try:
            import clipboard_mgr
            return {'ok': True, 'items': clipboard_mgr.list_history(kind=kind, q=q, limit=limit, offset=offset)}
        except Exception as e:
            return {'ok': False, 'items': [], 'error': str(e)}

    def copy_clipboard_item(self, item_id):
        try:
            import clipboard_mgr
            ok, msg = clipboard_mgr.copy_item(int(item_id))
            return {'ok': ok, 'msg': msg}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    def delete_clipboard_item(self, item_id):
        try:
            import clipboard_mgr
            return {'ok': clipboard_mgr.delete_item(int(item_id))}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def clear_clipboard_history(self):
        try:
            import clipboard_mgr
            return {'ok': clipboard_mgr.clear_history()}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def translate_text(self, text, src_lang='auto', tgt_lang='auto'):
        try:
            import translator
            return translator.translate_text(text, src_lang=src_lang, tgt_lang=tgt_lang)
        except Exception as e:
            return {'ok': False, 'msg': str(e), 'results': []}

    def trigger_snip(self, ocr=False, translate=False):
        """主窗口触发屏幕截图。ocr=False: 纯截图拷入剪贴板; ocr=True: 截图并 OCR 提取;
        translate=True: 截图 → OCR → 翻译，译文贴在选区原位（卡片自己关，主窗不动）。
        """
        try:
            import snip_ocr
            _hw = getattr(self._app, 'hwnd_main', None) or find_hwnd(MAIN_TITLE)

            def on_done(st):
                try:
                    # 翻译模式：卡片是置顶原生窗口，主窗一 show 就会把它顶掉（Deactivate 即关），
                    # 所以这里什么都不做，用户关掉卡片后自己回主窗。
                    if translate:
                        return
                    if self._app and hasattr(self._app, 'show_main'):
                        self._app.show_main()
                    elif _hw:
                        user32.ShowWindow(_hw, 9)  # SW_RESTORE
                        user32.SetForegroundWindow(_hw)
                        try:
                            d = getattr(self, '_last_dark', effective_dark())
                            b = getattr(self, '_last_bg', None)
                            dark_caption(_hw, d)
                            paint_frame(_hw, d, b)
                        except Exception:
                            pass
                    if self._win:
                        self._win.evaluate_js('window.__syncTheme && window.__syncTheme()')
                        if (st or {}).get('status') == 'done':
                            import json
                            st_json = json.dumps(st, ensure_ascii=False)
                            self._win.evaluate_js(f'window.__onSnipDone && window.__onSnipDone({st_json})')
                except Exception as e:
                    _log('Api.trigger_snip on_done: %r' % (e,))

            if translate:
                return snip_ocr.trigger_snip(on_done=on_done, do_ocr=True, do_translate=True)
            if self._app and hasattr(self._app, '_win32_hide_main'):
                self._app._safe_hide_main()
                self._app._win32_hide_main()
            elif _hw:
                user32.ShowWindow(_hw, 0)  # SW_HIDE
            ok = snip_ocr.trigger_snip(on_done=on_done, do_ocr=bool(ocr))
            return ok
        except Exception as e:
            _log('Api.trigger_snip: %r' % (e,))
            return False

    # ── 自绘标题栏（第 180 项）──────────────────────────────────────
    # 前端只负责画；窗口动作全在这里做。拖动必须交给系统（WM_NCLBUTTONDOWN），
    # 自己按像素 MoveWindow 会丢掉 Aero Snap、还会和 WebView2 的刷新打架。
    def titlebar_drag(self):
        """按住标题栏拖动（系统接管，含贴边吸附）。"""
        try:
            return caption_drag(find_hwnd(MAIN_TITLE))
        except Exception:
            return False

    def titlebar_resize_start(self, edge='top'):
        """第 375 项：标题栏最上面那条（顶边 6px）是缩放热区，但它落在**客户区**里 ——
        按下去先被 WebView2 的渲染窗接走（跨进程子窗，HTTRANSPARENT 无效），父窗的
        命中测试轮不到，系统的缩放循环也起不来。之前试过在子窗里转发
        WM_NCLBUTTONDOWN(HTTOP)，这层窗口栈（WinForms 宿主）会把它当成拖动。

        所以自己来：起手记下光标和窗口矩形，拖动时按光标真值改 top/height，
        和 titlebar_move（第 217/218 项）同一套思路 —— 不依赖 JS 的 screenY
        （WebView2 里那个值拖动期间会冻结）。
        """
        try:
            hwnd = self._tb_hwnd()
            if not hwnd:
                return None
            ctypes, u, _g, _p = _win_ptr_funcs()
            h = ctypes.c_void_p(hwnd)
            if u.IsZoomed(h):
                return None                     # 最大化时顶边不是缩放区
            pt = ctypes.wintypes.POINT()
            if not u.GetCursorPos(ctypes.byref(pt)):
                return None
            r = ctypes.wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            dpi = u.GetDpiForWindow(h) or 96
            self._rz_origin = {
                'edge': edge or 'top',
                'cx': int(pt.x), 'cy': int(pt.y),
                'left': int(r.left), 'top': int(r.top),
                'right': int(r.right), 'bottom': int(r.bottom),
                'min_h': int(round(480 * dpi / 96.0)),
            }
            _log_always('resize_start(%s) cursor=(%d,%d) win=(%d,%d,%d,%d)' % (
                edge, pt.x, pt.y, r.left, r.top, r.right, r.bottom))
            return dict(self._rz_origin)
        except Exception as e:
            _log_always('titlebar_resize_start failed: %r' % (e,))
            return None

    def titlebar_resize_move(self):
        """拖动中按帧调用：只改 top / height，左右不动（左右下本来就在真非客户区里）。"""
        st = getattr(self, '_rz_origin', None)
        if not st:
            return False
        try:
            hwnd = self._tb_hwnd()
            if not hwnd:
                return False
            ctypes, u, _g, _p = _win_ptr_funcs()
            h = ctypes.c_void_p(hwnd)
            pt = ctypes.wintypes.POINT()
            if not u.GetCursorPos(ctypes.byref(pt)):
                return False
            edge = st['edge']
            new_top, new_bottom = st['top'], st['bottom']
            if edge == 'top':
                new_top = st['top'] + (int(pt.y) - st['cy'])
            elif edge == 'bottom':
                new_bottom = st['bottom'] + (int(pt.y) - st['cy'])
            # 下限：高度不能小于 480 CSS px（和「记住尺寸」的阈值一致）
            if new_bottom - new_top < st['min_h']:
                if edge == 'top':
                    new_top = new_bottom - st['min_h']
                else:
                    new_bottom = new_top + st['min_h']
            # 上限：别把标题栏拖出工作区（拖上去就够不着了）
            try:
                work = _work_rect(hwnd)
                if work is not None and new_top < work.top:
                    new_top = work.top
                    if new_bottom - new_top < st['min_h']:
                        new_bottom = new_top + st['min_h']
            except Exception:
                pass
            w = st['right'] - st['left']
            hh = new_bottom - new_top
            u.SetWindowPos(h, None, st['left'], new_top, w, hh,
                           _SWP_NOZORDER | _SWP_NOACTIVATE)
            return True
        except Exception as e:
            _log_always('titlebar_resize_move failed: %r' % (e,))
            return False

    def titlebar_resize_end(self):
        self._rz_origin = None
        try:
            self._remember_size()
        except Exception:
            pass
        return True

    def titlebar_drag_start(self):
        """第 217 项：拖动开始 —— 记住「光标 ↔ 窗口」的相对关系。

        为什么不用 JS 的 e.screenX：WebView2 无边框窗口里 pointer 事件的
        screenX/screenY 在拖动期间会冻结（dx 恒 0 → 永远不发帧，216 项
        日志实锤：rect 每次都到、move 一帧不到）。改成 Python 侧
        GetCursorPos 读系统真值，窗口跟着真光标走，JS 只报
        「开始/拖动中/松手」三个事件。最大化时先还原（SetWindowPos 对
        最大化窗口被忽略），并把窗口横向摆到光标下方（沿用 182 项手感）。"""
        try:
            hwnd = self._tb_hwnd()
            if not hwnd:
                return None
            ctypes, u, _g, _p = _win_ptr_funcs()
            h = ctypes.c_void_p(hwnd)
            pt = ctypes.wintypes.POINT()
            if not u.GetCursorPos(ctypes.byref(pt)):
                return None
            if u.IsZoomed(h):
                u.ShowWindow(h, _SW_RESTORE)
                for _ in range(50):
                    time.sleep(0.01)
                    if not u.IsZoomed(h):
                        break
                r = ctypes.wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                w = int(r.right - r.left)
                x = max(int(pt.x) - w // 2, 8)
                wa = _monitor_work(u.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST))
                if wa:
                    x = max(wa[0] + 8, min(x, wa[2] - w - 8))
                u.SetWindowPos(h, None, x, int(r.top), 0, 0,
                               _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
            r = ctypes.wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            self._drag_origin = {'cx': int(pt.x), 'cy': int(pt.y),
                                 'wx': int(r.left), 'wy': int(r.top)}
            _log_always('drag_start cursor=(%d,%d) win=(%d,%d) %dx%d'
                        % (pt.x, pt.y, r.left, r.top,
                           r.right - r.left, r.bottom - r.top))
            return dict(self._drag_origin)
        except Exception as e:
            _log_always('titlebar_drag_start failed: %r' % (e,))
            return None

    def titlebar_drag_move(self):
        """第 217 项：拖动中（前端 16ms 一调）—— 按光标位移挪窗口。

        坐标全部来自 GetCursorPos（OS 真值），前端只报「还在拖」，
        彻底绕开 e.screenX 冻结 / DPI 单位换算这两类坑。"""
        try:
            drag = getattr(self, '_drag_origin', None)
            if not drag:
                return False
            ctypes, u, _g, _p = _win_ptr_funcs()
            pt = ctypes.wintypes.POINT()
            if not u.GetCursorPos(ctypes.byref(pt)):
                return False
            dx = int(pt.x) - drag['cx']
            dy = int(pt.y) - drag['cy']
            if abs(dx) + abs(dy) < 2:
                return True
            ok = self.titlebar_move(drag['wx'] + dx, drag['wy'] + dy)
            if not ok:
                self._drag_origin = None   # 挪不动（窗口没了等）：结束本次拖动
            return ok
        except Exception as e:
            _log_always('titlebar_drag_move failed: %r' % (e,))
            return False

    def titlebar_cmd(self, cmd):
        """min / max / close。close 走 pywebview 的 destroy，让 closing 钩子照常跑。"""
        try:
            c = str(cmd or '')
            hwnd = self._tb_hwnd()
            if c in ('min', 'close') and getattr(self, '_app', None):
                try:
                    self._app._remember_size()
                except Exception:
                    pass
            if c == 'close':
                if self._win is not None:
                    try:
                        self._win.destroy()
                        return True
                    except Exception as e:
                        # 第 267 项：destroy 也是 @_shown_call 包着的，异常时别让
                        # 「点关闭没反应」复现 —— 直接走 hide_main 收进托盘。
                        _log_always('titlebar_cmd close: destroy 失败，改直接隐藏: %r' % (e,))
                        app = getattr(self, '_app', None)
                        if app is not None:
                            app.hide_main()
                        return True
                return False
            _log_always('titlebar_cmd(%r) hwnd=%s' % (c, hwnd))
            return window_cmd(hwnd, c)
        except Exception as e:
            _log_always('titlebar_cmd(%r) failed: %r' % (cmd, e))
            return False

    def titlebar_state(self):
        """最大化状态 → 前端换 □ / ❐ 图标。"""
        try:
            hwnd = self._tb_hwnd()
            return {'max': window_maximized(hwnd)}
        except Exception:
            return {'max': False}

    def titlebar_rect(self):
        """自绘标题栏拖动：返回窗口当前屏幕坐标（第 182 项）。

        为什么不用 `WM_NCLBUTTONDOWN(HTCAPTION)` 那套（第 180 项的做法）：
        它依赖系统进入 modal 拖动循环，在 pywebview 的 JS 桥线程里**实测完全不动**
        （SendMessage 被封送到 UI 线程后拿不到拖动循环，Windows 直接吞掉）。
        改成前端逐帧报坐标 + 这里 SetWindowPos，100% 可控、不依赖消息循环。
        """
        try:
            hwnd = self._tb_hwnd()
            if not hwnd:
                _log_always('titlebar_rect: find_hwnd 落空（标题=%r）' % MAIN_TITLE)
                return None
            ctypes, u, _g, _p = _win_ptr_funcs()
            r = ctypes.wintypes.RECT()
            if not u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
                return None
            zoomed = bool(u.IsZoomed(ctypes.c_void_p(hwnd)))
            # 每次拖动起手指调一次，落一行：rect 与 max 状态是排查「拖不动」的第一现场
            _log_always('titlebar_rect -> (%d,%d) %dx%d max=%s'
                        % (r.left, r.top, r.right - r.left, r.bottom - r.top, zoomed))
            return {'x': int(r.left), 'y': int(r.top), 'max': zoomed}
        except Exception as e:
            # 用 _log_always：这类异常（名字写错、结构缺失）会被吞成「功能没反应」，
            # 只在调试日志里可见等于没有 —— 必须落到常规错误日志
            _log_always('titlebar_rect failed: %r' % (e,))
            return None

    def titlebar_dbg(self, x0=None, y0=None, sx=None, sy=None):
        """第 216 项拖动诊断：前端 onMove 首次越过 3px 阈值时把原始坐标过桥。

        用途：日志里「titlebar_dbg 有而 titlebar_move 无」= rAF 被冻结
        （WebView2 对判定为遮挡的窗口会停掉 requestAnimationFrame，换成
        setTimeout 之前就是这症状）；「titlebar_dbg 也没有」= e.screenX
        冻结（dx 恒 0，3px 阈值永远过不去）。一-log分明。"""
        _log_always('titlebar_dbg start=(%s,%s) now=(%s,%s)' % (x0, y0, sx, sy))
        return True

    def titlebar_restore(self, mouse_x=None):
        """拖动开始时如果在最大化状态：先还原，再把窗口摆到鼠标下方（第 182 项）。

        为什么必须处理：**最大化窗口的 SetWindowPos 会被系统忽略**，
        用户在上面拖就会觉得「完全拖不动」——而这正是最常出现的状态。
        还原后按鼠标在标题栏的水平位置反推窗口左上角，人手感接近原生。
        """
        try:
            hwnd = self._tb_hwnd()
            _log_always('titlebar_restore called mouse_x=%s hwnd=%s' % (mouse_x, hwnd))
            if not hwnd:
                return None
            ctypes, u, _g, _p = _win_ptr_funcs()
            h = ctypes.c_void_p(hwnd)
            if u.IsZoomed(h):
                u.ShowWindow(h, _SW_RESTORE)
                # 第 216 项：还原是异步的 —— 固定 sleep 20ms 后读到的经常还是
                # 最大化矩形，拿它算 w/x 全错，拖动目标就飞了。轮询到真还原。
                for _ in range(30):
                    time.sleep(0.01)
                    if not u.IsZoomed(h):
                        break
                _log_always('titlebar_restore: 还原 %s'
                            % ('完成' if not u.IsZoomed(h) else '超时'))
            r = ctypes.wintypes.RECT()
            if not u.GetWindowRect(h, ctypes.byref(r)):
                return None
            w = int(r.right - r.left)
            x = int(r.left)
            if mouse_x is not None:
                # 鼠标落在标题栏中间，手感对齐原生「抓住哪就从哪起」
                x = int(mouse_x) - w // 2
            # 夹回工作区：从最大化拖出时鼠标靠边，上面算出来会有一半在屏幕外
            # （实测 x=-361），看着像窗口「跑丢」了
            wa = _monitor_work(u.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST))
            if wa:
                x = max(wa[0] + 8, min(x, wa[2] - w - 8))
            u.SetWindowPos(h, None, x, int(r.top), 0, 0,
                           _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
            _log_always('titlebar_restore -> (%d,%d) w=%d mouse_x=%s'
                        % (x, int(r.top), w, mouse_x))
            return {'x': x, 'y': int(r.top)}
        except Exception as e:
            _log_always('titlebar_restore failed: %r' % (e,))
            return None

    def _tb_hwnd(self):
        """拖动链路的主窗句柄：优先用 rect 探明并缓存的那一个（第 216 项）。

        为什么不再每次 find_hwnd(MAIN_TITLE)：进程里可能短暂存在第二个
        同名 'Memex' 窗口（托盘/WebView2 辅助窗），EnumWindows 按 Z 序返回
        hits[0] —— 撞上谁就用谁，拖动就时好时坏。rect 每次拖动起手指都会
        先探一次主窗（rect 日志证明拿到的就是真窗），把它缓存下来给
        move/restore 复用；句柄失效（IsWindow=0）才重新找。
        """
        cached = getattr(self, '_tb_hwnd_cached', 0)
        try:
            u, _wt = _win()
            if cached and u and u.IsWindow(ctypes.c_void_p(cached)):
                return cached
        except Exception:
            pass
        hwnd = find_hwnd(MAIN_TITLE)
        if hwnd:
            self._tb_hwnd_cached = hwnd
        return hwnd

    def titlebar_move(self, x, y):
        """把窗口挪到屏幕坐标 (x, y)，尺寸不变（第 182 项）。

        拖动时前端按帧调用，所以这必须是「最轻」的一条：
        只 SetWindowPos，不重新布局、不动焦点、不触发 WebView2 刷新。
        """
        try:
            hwnd = self._tb_hwnd()
            if not hwnd:
                _log_always('titlebar_move(%s,%s): find_hwnd 落空' % (x, y))
                return False
            ctypes, u, _g, _p = _win_ptr_funcs()
            h = ctypes.c_void_p(hwnd)
            # 第 216 项：最大化窗口的 SetWindowPos 会被系统静默忽略（返回 True 但不动）
            # —— 182 项文档写明的坑。JS 侧的 restore 是异步的，可能没赶上第一帧；
            # 这里每帧自愈：发现还处于最大化就先还原（轮询到真还原），再挪。
            zoomed = bool(u.IsZoomed(h))
            if zoomed:
                u.ShowWindow(h, _SW_RESTORE)
                for _ in range(50):
                    time.sleep(0.01)
                    if not u.IsZoomed(h):
                        break
                _log_always('titlebar_move: 拖动时最大化 → 已还原 (%s)'
                            % ('完成' if not u.IsZoomed(h) else '超时'))
            ok = bool(u.SetWindowPos(h, None, int(x), int(y), 0, 0,
                                     _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE))
            # 第 216 项排查期曾每帧都记；拖动已确认修好（218 项），改回
            # 「首次成功记一条 + 失败必记」，日志量回落。
            if ok and not getattr(self, '_tb_move_logged', False):
                self._tb_move_logged = True
                _log_always('titlebar_move 首次调用 → %s,%s' % (x, y))
            if not ok:
                _log_always('titlebar_move(%s,%s) SetWindowPos FAILED' % (x, y))
            return ok
        except Exception as e:
            _log_always('titlebar_move(%s,%s) failed: %r' % (x, y, e))
            return False

    def titlebar_snap(self):
        """松手时贴边吸附（第 182 项）：顶上最大化、左右各半屏。

        摘掉 WS_CAPTION 后系统不再提供 Aero Snap，而拖动改成了自己 SetWindowPos，
        所以这两条得自己补。只在「贴着边缘松手」时动手，平时什么都不做。
        容差 6px：太小了用户拖不准，太大了窗口从边缘弹开很突兀。
        第 289 项：只认「正好贴住」的容差带 —— 拖出屏幕（想临时藏一部分）不吸附。
        """
        try:
            hwnd = self._tb_hwnd()
            if not hwnd:
                return False
            ctypes, u, _g, _p = _win_ptr_funcs()
            h = ctypes.c_void_p(hwnd)
            r = ctypes.wintypes.RECT()
            if not u.GetWindowRect(h, ctypes.byref(r)):
                return False
            hmon = u.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST)
            wa = _monitor_work(hmon)
            if not wa:
                return False
            wl, wt_, wr, wb = wa
            # 第 289 项：只认「正好贴住边缘」的容差带；拖出屏幕不再吸回半屏
            zone = snap_zone((int(r.left), int(r.top), int(r.right), int(r.bottom)),
                             (wl, wt_, wr, wb), tol=6)
            sw = _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_SHOWWINDOW
            if zone == 'max':
                u.ShowWindow(h, _SW_MAXIMIZE)
                return True
            if zone == 'left':
                u.SetWindowPos(h, None, wl, wt_, (wr - wl) // 2, wb - wt_, sw)
                return True
            if zone == 'right':
                half = (wr - wl) // 2
                u.SetWindowPos(h, None, wl + half, wt_, half, wb - wt_, sw)
                return True
            return False
        except Exception as e:
            _log_always('titlebar_snap failed: %r' % (e,))
            return False

    def _titlebar_guard(self):
        """常驻守卫（第 181 项）：WinForms 会把 WS_CAPTION 写回来，且不止一次。

        实测：日志已经打过 stripped，1 秒后读样式位又是 True；触发点不确定
        （窗口 Show 之后、WebView2 布局完成、DPI 变化、用户缩放窗口……）。
        「摘一次 + 延迟两次」覆盖不全，只能持续盯：每秒读一次样式位，
        真被写回就重摘。

        成本：平时只有一次 GetWindowLongPtr（微秒级，不碰窗口、不重绘）；
        只有发现被写回才 SetWindowLongPtr + SetWindowPos。窗口销毁即退出。
        """
        import ctypes
        hwnd = self.hwnd_main
        if not hwnd:
            return
        prev_had_caption = False
        while True:
            time.sleep(1.0)
            try:
                if not self.custom_titlebar:
                    return
                if not ctypes.windll.user32.IsWindow(ctypes.c_void_p(hwnd)):
                    return
                # 先看样式位：.NET 每次写回 CAPTION，顶部那条 caption 带也会跟着
                # 变回系统色（白）。所以「写回过」就要重刷外框底色（第 184 项）。
                try:
                    _g = getattr(ctypes.windll.user32, 'GetWindowLongPtrW')
                    _g.restype = ctypes.c_ssize_t
                    _g.argtypes = [ctypes.c_void_p, ctypes.c_int]
                    had = bool(_g(ctypes.c_void_p(hwnd), -16) & _WS_CAPTION)
                except Exception:
                    had = False
                if had:
                    if not caption_off(hwnd, True):
                        self.custom_titlebar = False
                        _log_always('custom titlebar: 守卫发现样式被写回且摘不回来，退回系统标题栏')
                        try:
                            import brain as _b
                            _b.TITLEBAR_ACTIVE = False
                        except Exception:
                            pass
                        return
                    paint_frame(hwnd)
                prev_had_caption = had
            except Exception as e:
                _log('titlebar guard: %r' % (e,))
                return

    # ── 原生文件对话框（第 241 项）────────────────────────────────
    # pywebview 的 JS API 是**另起线程**执行的（webview/util.py: `Thread(target=_call)`），
    # 而 winforms 后端的 create_file_dialog 是**唯一没有 Invoke 回 UI 线程**的窗口操作
    # （set_title / hide / show / destroy_window / create_confirmation_dialog 全都有
    # `if self.InvokeRequired: self.Invoke(...)`）。
    # 于是原生对话框跑在 MTA 后台线程上，owner 却是 UI 线程的主窗 ——
    # 模态对话框会跨线程 disable/enable 这个 WebView2 宿主，控制器把渲染层藏起来，
    # 用户看到的就是「点上传文件夹 → 主窗白屏」。概率性 = 取决于线程时序。
    #
    # 修法：自己把对话框 Invoke 回 UI 线程再弹，并返回后自愈渲染层。
    def _ui_dialog(self, dialog_type):
        """在 WinForms UI 线程上弹原生对话框，返回路径列表（取消/失败 = []）。

        不直接调 `self._win.create_file_dialog`：那个入口带着 @_shown_call，
        内部会等 events.shown（主窗 hidden 时永不置位 → 卡满 20 秒），
        而且最终还是落到没有 Invoke 的那个后端函数上。
        """
        win = self._win
        if win is None:
            return []
        try:
            uid = win.uid
        except Exception:
            return []
        try:
            from webview.platforms import winforms as _wf
        except Exception:
            return []
        form = None
        try:
            form = _wf.BrowserView.instances.get(uid)
        except Exception:
            form = None
        if form is None:
            return []

        out = []

        def _run():
            try:
                picked = _wf.create_file_dialog(dialog_type, '', False, '', (), uid)
                if picked:
                    out.extend(list(picked))
            except Exception as e:
                _log_always('原生对话框失败: %r' % (e,))

        try:
            from System import Func, Type
        except Exception:
            Func = Type = None
        try:
            if Func is not None and hasattr(form, 'InvokeRequired'):
                if form.InvokeRequired:
                    # Invoke 是同步的：UI 线程弹完（用户选完/取消）才返回，
                    # 这里等的就是这个结果，不会丢。
                    form.Invoke(Func[Type](_run))
                else:
                    _run()
            else:
                _run()
        except Exception as e:
            _log_always('原生对话框调度失败: %r' % (e,))
            return []
        return out

    def _after_native_dialog(self):
        """原生对话框关掉后：主窗可能被跨线程 disable 过，检查渲染层并自愈。

        这是白屏的**兜底**：即使前面的调度仍有遗漏，只要渲染子窗被藏了就唤醒它，
        顺便把 WebView2 控件自身的 Visible 设回 true（第 109/111 项的根因修法）。
        """
        try:
            hwnd = self._win32_hwnd_main()
            if not hwnd:
                return
            if _render_children_visible(hwnd):
                return
            _log_always('原生对话框后检测到渲染层被藏，执行自愈')
            revive_webview(hwnd)
            try:
                app = self._app
                if app is not None and hasattr(app, '_force_webview_visible'):
                    app._force_webview_visible()
            except Exception:
                pass
        except Exception as e:
            _log('after_native_dialog: %r' % (e,))

    def _win32_hwnd_main(self):
        """主窗 HWND（优先缓存，兜底按标题找）。"""
        try:
            h = self.hwnd_main
            if h:
                return h
        except Exception:
            pass
        try:
            return find_hwnd(MAIN_TITLE)
        except Exception:
            return None

    def pick_folder(self):
        if webview is None or self._win is None:
            return ''
        try:
            picked = self._ui_dialog(_dialog_kind('FOLDER'))
            self._after_native_dialog()
            if picked:
                return picked[0]
        except Exception:
            pass
        return ''

    def pick_file(self):
        """选一个文件拿完整路径（反查用：从浏览器拖进来的文件只有名字，没有路径）。"""
        if webview is None or self._win is None:
            return ''
        try:
            picked = self._ui_dialog(_dialog_kind('OPEN'))
            self._after_native_dialog()
            if picked:
                return picked[0]
        except Exception:
            pass
        return ''

    def open_path(self, p):
        """打开一个结果（文件、文件夹，或者启动 .exe/.lnk）。

        HTTP 侧的 /open 已经把可执行类拒掉了（那几道门对本地进程可伪造），
        主窗口双击应用必须从这个原生桥走；白名单口径不变：只能是本次搜索结果里出现过的路径。"""
        p = str(p or '')
        if not p or not brain.ev_recent(p):
            return False
        try:
            app_entries.launch(p)
        except Exception:
            return False
        try:
            with brain.con_lock:
                brain.record_open(brain.conn(), p, os.path.isdir(p))
        except Exception:
            pass
        return True

        try:
            # os.startfile(p, 'properties') 内部缺 SEE_MASK_INVOKEIDLIST，
            # 会报 WinError 1155（无关联）——必须用 ShellExecuteExW 直调。
            import ctypes
            from ctypes import wintypes

            class _SEI(ctypes.Structure):
                _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG),
                            ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
                            ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                            ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
                            ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
                            ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                            ('dwHotKey', wintypes.DWORD), ('hIconOrMonitor', wintypes.HANDLE),
                            ('hProcess', wintypes.HANDLE)]
            np = os.path.normpath(p)
            sei = _SEI()
            sei.cbSize = ctypes.sizeof(_SEI)
            sei.fMask = 0x0000000C              # SEE_MASK_INVOKEIDLIST
            sei.lpVerb = 'properties'
            sei.lpFile = np
            sei.nShow = 1                       # SW_SHOWNORMAL
            ret = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
            return bool(ret)
        except Exception:
            return False

    def reveal(self, p):
        """在资源管理器里选中这个文件（主窗口 Ctrl+Enter）。

        footer 一直写着「Ctrl+Enter 定位」，但主窗口以前根本没接这个动作——
        按下去走的是 open_path（直接打开）。补上原生桥。

        第 319 项：**不再要求 ev_recent 白名单**。定位只是「在资源管理器里选中」，
        不执行任何东西，安全面上跟 open_path（会跑 .exe）不是一个量级；
        而白名单 5 分钟就过期、还会被下一次搜索整体替换，用户右键「定位」
        得到的却是「浏览器里没有『定位』」这句完全指错方向的话（11-19 实测截图）。
        现在只要路径真的存在就放行。"""
        p = str(p or '')
        if not p:
            return False
        try:
            np = os.path.normpath(p)
        except Exception:
            return False
        if not (os.path.exists(np) or brain.ev_recent(p)):
            return False
        try:
            import subprocess
            subprocess.Popen(['explorer', '/select,', np])
            return True
        except Exception:
            return False


class MiniApi:
    """快搜窗的原生能力。打开文件必须过 brain.ev_recent 的白名单——
    os.startfile 对 .exe 就是「运行」，页面能自己拼路径就等于开后门。"""

    def __init__(self, app):
        self._app = app          # 下划线开头：别让 pywebview 递归展开整个 App

    def resize(self, h):
        """返回 True 表示窗口真的按这个高度改过了。

        窗口还藏着的时候不能 resize（pywebview 会把隐藏窗口弹出来），所以这里要如实
        告诉页面「这次没改成」——页面那边靠返回值决定要不要记住这个高度，否则一旦在
        隐藏态算过一次，之后每次打开都按那个旧高度显示，最后一行被切掉。"""
        try:
            if not self._app.mini_visible:
                return False
            self._app.resize_mini(int(h))
            return True
        except Exception:
            return False

    def hide(self):
        self._app.hide_mini()
        return True

    def blurred(self):
        """页面报告失焦时调用。这里不直接关窗，先分两种情况：
        鼠标还在窗口上 → 是自己 resize 引起的瞬时失焦，把焦点抢回来接着打字；
        鼠标在窗口外 → 用户真的点了别处，收起。"""
        return self._app.on_mini_blur()

    def open_path(self, p):
        p = str(p or '')
        ok = False
        if p and brain.ev_recent(p):
            try:
                app_entries.launch(p)
                ok = True
            except Exception:
                ok = False
        if ok:
            self._remember(p)
        self._app.hide_mini()
        return ok

    def _remember(self, p):
        """记一次打开。快搜窗下次弹出时的「最近打开」和结果排序都读这份记录。"""
        try:
            with brain.con_lock:
                brain.record_open(brain.conn(), p, os.path.isdir(p))
        except Exception:
            pass

    def properties(self, p):
        """弹出 Windows 文件属性对话框（快搜窗右键菜单）。
        os.startfile 的 operation 参数（3.10+）走 ShellExecuteEx 的 properties
        verb，立即返回不阻塞；白名单与 open_path/reveal 一致。"""
        p = str(p or '')
        if not p or not brain.ev_recent(p):
            return False
        # 第 123 项这个函数体被误删过一次（只剩白名单校验，右键「属性」点了没反应）。
        # ShellExecuteEx 的 properties verb 立即返回，不会阻塞快搜窗。
        try:
            os.startfile(os.path.normpath(p), 'properties')
            return True
        except Exception as e:
            _log('properties FAILED: %r' % (e,))
            return False

    def reveal(self, p):
        """在资源管理器里定位这个文件（快搜窗里 Ctrl+Enter 的备用动作）"""
        p = str(p or '')
        ok = False
        if p and brain.ev_recent(p):
            try:
                import subprocess
                subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])
                ok = True
            except Exception:
                ok = False
        return ok

    def detail(self, q):
        self._app.hide_mini()
        self._app.show_main(q)
        return True

    # ── 原生文件对话框（第 241 项）────────────────────────────────
    # 快搜窗的设置页有「➕ 手动添加文件夹」，页面里调的是 `api.pick_folder()`。
    # 以前 MiniApi **没有**这个方法 → JS 侧 `api.pick_folder` 是 undefined，
    # 一调就 TypeError，只能退回 prompt() 手输路径；桌面版明明能弹原生选择框。
    # 这里转给主窗的 Api（同一套 UI 线程调度 + 渲染层自愈）。
    def pick_folder(self):
        try:
            api = getattr(self._app, 'main_api', None)
            if api is not None and hasattr(api, 'pick_folder'):
                return api.pick_folder()
        except Exception as e:
            _log('MiniApi.pick_folder: %r' % (e,))
        return ''

    def pick_file(self):
        try:
            api = getattr(self._app, 'main_api', None)
            if api is not None and hasattr(api, 'pick_file'):
                return api.pick_file()
        except Exception as e:
            _log('MiniApi.pick_file: %r' % (e,))
        return ''

    def read_clipboard_image(self):
        """快搜窗读取剪贴板图像。"""
        try:
            import snip_ocr
            return snip_ocr.read_clipboard_image()
        except Exception as e:
            return {'has_image': False, 'error': str(e)}

    def ocr_image(self, b64_or_data_url=None):
        """快搜窗对传入图像执行离线 OCR 识别。"""
        try:
            import snip_ocr
            return snip_ocr.ocr_base64_image(b64_or_data_url)
        except Exception as e:
            return {'ok': False, 'text': '', 'error': str(e)}

    def get_clipboard_history(self, kind='all', q='', limit=60, offset=0):
        try:
            import clipboard_mgr
            return {'ok': True, 'items': clipboard_mgr.list_history(kind=kind, q=q, limit=limit, offset=offset)}
        except Exception as e:
            return {'ok': False, 'items': [], 'error': str(e)}

    def copy_clipboard_item(self, item_id):
        try:
            import clipboard_mgr
            ok, msg = clipboard_mgr.copy_item(int(item_id))
            return {'ok': ok, 'msg': msg}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    def delete_clipboard_item(self, item_id):
        try:
            import clipboard_mgr
            return {'ok': clipboard_mgr.delete_item(int(item_id))}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def clear_clipboard_history(self):
        try:
            import clipboard_mgr
            return {'ok': clipboard_mgr.clear_history()}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def translate_text(self, text, src_lang='auto', tgt_lang='auto'):
        try:
            import translator
            return translator.translate_text(text, src_lang=src_lang, tgt_lang=tgt_lang)
        except Exception as e:
            return {'ok': False, 'msg': str(e), 'results': []}

    def trigger_snip(self, ocr=False, translate=False):
        """快搜窗触发屏幕截图。ocr=False: 纯截图拷入剪贴板; ocr=True: 截图并 OCR 提取;
        translate=True: 截图 → OCR → 翻译，译文贴在选区原位（快搜窗让位，不再抢焦点）。"""
        try:
            import snip_ocr
            was_mini = bool(getattr(self._app, 'mini_visible', False))

            def on_done(st):
                # 翻译模式：卡片是置顶原生窗口，把快搜窗 show 回来会激活它 → 卡片立刻关，
                # 所以这条路径不还原快搜窗（用户复制完按热键再叫出来即可）。
                if translate:
                    return
                if was_mini:
                    try:
                        self._app._from_snip = True
                        self._app.show_mini()
                        wnd = getattr(self._app, 'mini', None)
                        if wnd and (st or {}).get('status') == 'done':
                            import json
                            st_json = json.dumps(st, ensure_ascii=False)
                            wnd.evaluate_js(f'window.__onSnipDone && window.__onSnipDone({st_json})')
                    except Exception as e:
                        _log('MiniApi.trigger_snip on_done: %r' % (e,))

            if was_mini:
                self._app.hide_mini()
            ok = snip_ocr.trigger_snip(on_done=on_done, do_ocr=bool(ocr),
                                      do_translate=bool(translate))
            return ok
        except Exception as e:
            _log('MiniApi.trigger_snip: %r' % (e,))
            return False


# ------------------------------------------------------------------ 命名事件（跨进程激活第一个实例）
def _kernel32_event():
    """用 ctypes 直接调 kernel32 建/开命名事件，不依赖 win32event。

    关键：打包 exe 里 pywin32 的 win32event 不在 hiddenimports 中，import 失败会让
    整个「命名事件激活」机制退化成跨进程 ShowWindow 兜底——而那个会被 WebView2 渲染层
    重新藏回，白屏依旧。ctypes 调 kernel32 是内建的，不存在这个依赖问题。"""
    try:
        k = ctypes.windll.kernel32
        k.CreateEventW.restype = ctypes.c_void_p
        k.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        k.OpenEventW.restype = ctypes.c_void_p
        k.OpenEventW.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_wchar_p]
        k.SetEvent.restype = ctypes.c_int
        k.SetEvent.argtypes = [ctypes.c_void_p]
        k.WaitForSingleObject.restype = ctypes.c_uint
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k.CloseHandle.restype = ctypes.c_int
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        return k
    except Exception:
        return None


# ------------------------------------------------------------------ 应用
class App:
    def __init__(self, url, main_url=None, silent=False, start_mini=False):
        self.url = url
        self.main_url = main_url or (url + '/')
        self.silent = silent
        self.start_mini = start_mini
        self.mini = None
        self.main = None
        self.main_api = None
        self.mini_api = MiniApi(self)
        self.hwnd_mini = None
        # 窗口是否摘了系统标题栏（第 180 项）：after_start 里定，别的代码只读
        self.custom_titlebar = False
        self.hwnd_main = None
        self.tray = None
        self.mini_visible = False
        self.hotkey = None
        self.floating_ball_mgr = None
        self._hide_hint_given = False
        self._want_main = False      # 用户主动要求显示主窗（silent 守卫据此让路）
        self._lock = threading.Lock()
        # 第 267 项：托盘面板「点了没反应 / 关了又弹出来」的两把锁
        # ① _show_lock：同一时刻只允许一次 show_main 在飞（后台线程各自跑会互相顶窗口）
        # ② _hide_seq：用户每关一次主窗就 +1，在飞的 show_main 发现序号变了就放弃，
        #    不再「20 秒后醒来」把窗口重新弹出来。
        self._show_lock = threading.Lock()
        self._hide_seq = 0
        self._panel_lock = threading.Lock()
        self._panel_req = None
        self._panel_worker_on = False
        # 第 197 项：自动弹出 / 文件夹里直接打字
        self.typewatch = None
        self.clickwatch = None          # 第 307 项：鼠标钩子（点文件夹让对话框跟过去）
        self._alive = True
        self._auto_ok = True
        self._type_ok = True
        self._auto_last = 0
        self._auto_dlg = 0
        self._auto_muted = {}
        # 第 198 项：贴角模式。记录锚点（右/下边、宽度、dpr），
        # 每次长高都按它重算 y —— 底边钉住、往上长，不会顶出窗口。
        self._dock = None
        # 第 229 项二轮：钩子线程 / 0.15s 轮询 / 拖动追逐线程三处都会调
        # _follow_anchor，不加锁就是「旧位置后写覆盖新位置」的乱序抖动。
        self._follow_lock = threading.Lock()
        self._rescale_lock = threading.Lock()
        self._rescale_ts = 0.0
        self._drag_move = False

    # -- 窗口 ---------------------------------------------------------
    def create(self):
        # 位置必须在创建时给定：pywebview 的 move()/resize() 会让隐藏窗口变可见
        # （底层是 winforms 的 Location/Size 赋值，会顺手把窗体 Show 出来）。
        # 实测过：对 hidden 窗口调一次 move，IsWindowVisible 就从 0 变 1。
        # 屏幕逻辑宽不够时先把快搜窗收窄（这一步只改尺寸常量，不碰窗口）。
        calibrate_mini_width()
        x, y = mini_origin()
        self.mini = webview.create_window(
            MINI_TITLE, f'{self.url}/mini',
            width=MINI_W, height=MINI_H, x=x, y=y,
            frameless=True, on_top=True, resizable=False, easy_drag=False,
            # 第 359 项：frameless + 扩边框 + 子窗口让路之后，窗口最外圈有几像素
            # 不归网页画、只认 WinForms BackColor。以前写死 '#FFFFFF'，换成深色
            # 配色后右边就挂一条白边；这里至少让启动时和主题一致（运行期见 set_dark）。
            hidden=True, background_color=ui_bg_hex(), js_api=self.mini_api,
            text_select=False, focus=True, min_size=(MINI_W_MIN, 88),
        )
        self.main_api = Api()
        self.main_api._app = self      # 原生对话框后要调 App._force_webview_visible 自愈（第 241 项）
        self.main_api._open_mini = lambda: self.show_mini()
        self.main_api._hide_main = self.hide_main
        sw, sh = screen_logical_size()
        saved = None
        try:
            import brain as _b
            _st = _b._settings_read()
            if _st.get('win_w') and _st.get('win_h'):
                saved = (int(_st['win_w']), int(_st['win_h']))
        except Exception:
            saved = None
        # 逻辑屏尺寸取「物理 ÷ 真实缩放」：screen_metrics 的 dpr 来自
        # GetScaleFactorForMonitor，比 GetDpiForSystem 可靠（后者可能是登录时的旧值）
        _pw, _ph, _dpr = screen_metrics()
        if _pw > 0 and _ph > 0:
            sw, sh = _pw / _dpr, _ph / _dpr
        MW, MH = main_window_size(sw, sh, saved)
        self._main_w, self._main_h = MW, MH
        # 主窗初始位置：自己算居中，**别交给 WinForms 的 CenterScreen**。
        # 实测（3840×2160 @150%）CenterScreen 在 Per-Monitor v2 进程里不生效 ——
        # 普通启动落在 (38,38)（左上角起，右边空出半个屏幕）；silent 启动再唤出时
        # 还会恢复到 (-317,298)，左边缘 317px 跑到屏幕外。两处都是「用户以为窗口
        # 没开 / 拖不回来」的来源。这里给逻辑像素，pywebview（winforms.py 第 214-220 行）
        # 走 StartPosition=Manual + Location=x*scale，各档缩放同一套换算。
        _mx = max(0, int((sw - MW) // 2))
        _my = max(0, int((sh - MH) // 2))
        if _my + MH > sh:                       # 任务栏很高时别把窗口底推出屏
            _my = max(0, int(sh - MH))
        # create_window 收的是逻辑像素，打包里的 pywebview 自己会 ×DPR 换成物理
        # （winforms.py: self.Size = initial_width * self._scale），所以这里给逻辑值就对了；
        # 100% / 125% / 150% / 200% 走的是同一条换算，不需要为某档缩放单独写分支。
        # 万一某个版本的换算不对，_fit_main_size 会用客户区实测值兜底校正。
        # 主窗口【不能】hidden 创建：pywebview 的 show()/load_url() 都带
        # @_shown_call，要先等 WinForms 的 Form.Shown 事件置位；hidden 窗口永远不触发
        # 这个事件，于是「从快搜窗点展示更多 → show_main」会卡满 20 秒再抛
        # WebViewException，最后被旧的兜底逻辑丢进系统浏览器。
        # silent（开机自启）时不显示窗口，改为在 after_start 等窗口真正 shown 之后再 hide。
        # background_color 决定 WinForms 的 BackColor —— 摘掉标题栏后，WS_THICKFRAME
        # 留下的那圈 7px 缩放边框就是拿它填的。pywebview 默认 #FFFFFF，于是窗口四周
        # 有一圈纯白，标题栏却是浅灰，看着像「两层」（第 184 项）。
        self.main = webview.create_window(
            MAIN_TITLE, self.main_url,
            width=MW, height=MH, x=_mx, y=_my,
            min_size=main_min_size(MW, MH),
            text_select=True, js_api=self.main_api, hidden=False,
            background_color=ui_bg_hex(),
        )
        self.main_api._win = self.main
        self.main.events.closing += self._on_main_closing
        self.main.events.resized += self._on_main_resized

    def _late_dark_caption(self, tries=6):
        """窗口刚建好时 DWM 还不接受属性；等一会儿再刷，并随每次显示重刷。

        第 343 项：顺手确认「非客户区归零」到底成没成 —— WinForms 在窗体第一次真正
        Show / 重建句柄时会把窗口过程装回去，建窗时挂的那份就被顶掉了（实测内缩还是 10px）。
        这里用 `frame_insets() > 0` 判断、必要时 force 重挂，最多试 tries 次。
        """
        settled = False
        for _ in range(tries):
            time.sleep(0.6)
            try:
                hw = self.hwnd_main or find_hwnd(MAIN_TITLE)
                ok = bool(hw) and dark_caption(hw, effective_dark())
                if hw:
                    # 深色开关会重置 caption 色 → 顶部白带回来（第 184 项），必须补刷
                    paint_frame(hw)
                    try:
                        if frame_insets(hw) > 0:
                            install_nc_guard(hw, force=True)
                            _log('nc retry: insets now %s' % frame_insets(hw))
                    except Exception as e:
                        _log('nc retry failed: %r' % (e,))
                    try:
                        install_child_hit_guard(hw)
                    except Exception:
                        pass
                if ok and hw and frame_insets(hw) == 0 and not settled:
                    settled = True
                    _log('dark caption + nc guard settled (%s)' % effective_dark())
            except Exception as e:
                _log('dark caption retry failed: %r' % (e,))
        if not settled:
            _log('dark caption gave up')

    def _remember_size(self):
        """把用户拖出来的窗口大小记住，下次按这个开（不会因为换分辨率炸掉）。"""
        try:
            hwnd = self.hwnd_main or find_hwnd(MAIN_TITLE)
            u, wt = _win()
            if hwnd and u:
                try:
                    if u.IsZoomed(ctypes.c_void_p(hwnd)) or u.IsIconic(ctypes.c_void_p(hwnd)):
                        _log('remember_size: 窗口最大化/最小化中，不覆盖保存普通尺寸')
                        return
                except Exception:
                    pass
            w, h = 0, 0
            if hwnd and u and wt:
                try:
                    rc = wt.RECT()
                    if u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rc)):
                        # ⚠️ 这里存的必须是**逻辑（CSS）像素**：建窗时 `main_window_size`
                        # 拿它和逻辑屏尺寸比。历史上 `window_dpr(hwnd)` 在这条路径上
                        # 会返回 1.0（本机 150% 屏实测），于是把物理 1717×1456 原样写进
                        # settings，下次启动当成 CSS 又乘 1.5 → 窗口一次比一次大
                        # （第 96/97 次实测落在 2576×2184）。改用 screen_metrics 的 dpr
                        # （它就是居中用的那个，日志里稳定 1.50），再拿 window_dpr 兜底。
                        dpr = 0
                        try:
                            dpr = float(screen_metrics()[2] or 0)
                        except Exception:
                            dpr = 0
                        if dpr <= 0:
                            dpr = window_dpr(hwnd) or 1.0
                        w = int(round((rc.right - rc.left) / dpr))
                        h = int(round((rc.bottom - rc.top) / dpr))
                        _log('remember_size: 物理 %sx%s / dpr %.2f → 存 %sx%s' % (
                            rc.right - rc.left, rc.bottom - rc.top, dpr, w, h))
                except Exception:
                    pass
            if not (w and h) and self.main:
                try:
                    w, h = int(self.main.width), int(self.main.height)
                except Exception:
                    pass
            if 700 <= int(w) <= 4000 and 480 <= int(h) <= 3000:
                self._main_w, self._main_h = int(w), int(h)
                import brain as _b
                _b._settings_mark('win_w', int(w))
                _b._settings_mark('win_h', int(h))
                _log_always('remember_size: 记住用户窗口尺寸 %dx%d' % (int(w), int(h)))
        except Exception as e:
            _log('remember_size FAILED: %r' % (e,))

    def _on_main_resized(self, *args, **kwargs):
        """用户拖拽边框调整大小时：防抖 250ms 自动落盘记住尺寸，绝不漏掉。"""
        try:
            t = getattr(self, '_resize_timer', None)
            if t is not None:
                t.cancel()
        except Exception:
            pass
        try:
            self._resize_timer = threading.Timer(0.25, self._remember_size)
            self._resize_timer.daemon = True
            self._resize_timer.start()
        except Exception:
            pass

    def _on_main_closing(self):
        """主窗口「关闭」= 收进托盘。托盘没起来就老老实实退出，不留一个看不见的进程。

        不在 closing 事件里直接调 hide()：WinForms 的 FormClosing 里同步调 Hide()
        偶尔会让消息循环卡住，之后 show() 也不响应。改成延迟 50ms 再 hide，
        让 FormClosing 先走完 cancel 流程。"""
        _log(f'_on_main_closing tray={self.tray} ok={getattr(self.tray,"ok",None)}')
        self._remember_size()
        # 第 267 项：用户自己点了关闭 —— 立刻作废所有在飞/排队的 show_main。
        _bump_hide(self)
        if self.tray is not None and self.tray.ok:
            threading.Timer(0.05, self._safe_hide_main).start()
            self._hint_once()
            _log('_on_main_closing → return False (收进托盘)')
            return False
        _log('_on_main_closing → return True (真退出)')
        return True

    def _hide_after_shown(self):
        """silent 启动：等 pywebview 的 shown 事件置位（WebView2 也就绪）再隐藏主窗口。

        hidden=True 创建的 WinForms 窗口不触发 Form.Shown，events.shown 永远不置位，
        之后所有 show() / load_url() / evaluate_js() 都会卡满 20 秒再抛
        WebViewException —— 这正是「点展示更多却跳到浏览器」的根因之一。"""
        try:
            if self.main.events.shown.wait(10):
                self._safe_hide_main()
                # 只记「该隐藏时到底隐藏了没有」：白屏排查时这一行比任何猜测都直接。
                _log('hide_after_shown: visible=%s' % _is_visible(self.hwnd_main))
                # 隐藏动作是排队进 GUI 线程的，后面排队的 resize / 属性赋值会把它顶回来
                # （见 after_start 的注释）。这里等一拍复查一次，再挂一个 10 秒守卫。
                time.sleep(0.35)
                if _is_visible(self.hwnd_main):
                    _log_always('静默启动：hide 之后窗口又被顶出来，重新收起')
                    self._safe_hide_main()
                    self._win32_hide_main()
                threading.Thread(target=self._silent_hide_guard, daemon=True).start()
            else:
                _log_always('主窗口 shown 事件超时，silent 模式未隐藏窗口')
        except Exception:
            _log_always('_hide_after_shown 失败\n' + traceback.format_exc())

    def _win32_hide_main(self):
        """Win32 直接 SW_HIDE：跨线程必生效，渲染层会级联藏起来（第 109/121 项验证过）。"""
        try:
            _bump_hide(self)
            hwnd = self.hwnd_main or find_hwnd(MAIN_TITLE)
            if hwnd:
                ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 0)
        except Exception:
            pass

    def _silent_hide_guard(self, seconds=10.0, interval=0.5):
        """静默启动后盯 10 秒：窗口要是又自己冒出来，立刻收回去。

        开机的头几秒 DPI 通知 / 渲染层初始化 / 排队中的 resize 都可能把刚隐藏的主窗
        顶出来；用户此时看到的是一块白板（渲染层还没跟上）。这条守卫不看原因只看
        结果——只要 silent 且用户没主动要求显示，主窗可见就收起。
        用户从托盘 / 双击图标打开后 `_want_main` 为 True，守卫自动让路。"""
        end = time.time() + seconds
        while time.time() < end:
            time.sleep(interval)
            if self._want_main:
                return
            hwnd = self.hwnd_main or find_hwnd(MAIN_TITLE)
            if hwnd and _is_visible(hwnd):
                _log_always('静默启动守卫：主窗又冒出来了，重新收起')
                self._safe_hide_main()
                self._win32_hide_main()

    def _safe_hide_main(self):
        """在 closing 事件之外 hide 主窗口，避开 WinForms FormClosing 的时序陷阱。"""
        # 第 267 项：同 hide_main —— 关窗要作废在飞的 show_main，否则会被顶回来。
        _bump_hide(self)
        try:
            if pv_hide(self.main):
                _log_always('_safe_hide_main ok')   # exe 模式 _log 不落盘——hide 是否执行是白屏排查的第一线索
            else:
                _log_always('_safe_hide_main: pv_hide 返回 False')
        except Exception as e:
            _log(f'_safe_hide_main FAILED: {e}')

    def _hint_once(self):
        if self._hide_hint_given:
            return
        self._hide_hint_given = True
        # 不要在这里调 self.main.evaluate_js —— 关闭中 WebView2 可能还在加载页面，
        # evaluate_js 会同步等到页面 ready，实测能卡 20 秒（用户感知「点了关闭半天才关掉」）。
        # 窗口马上要隐藏，toast 没人看，直接用托盘气泡提示就够了。
        try:                     # 窗口都隐藏了就没人看得见提示，用托盘气泡更实在
            import win32api      # noqa: F401
            import win32con
            import win32gui
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_MODIFY,
                (self.tray._hwnd, 0, win32gui.NIF_INFO, 0, None, '',
                 'Memex', '已收进托盘：双击 Ctrl 唤出快搜，托盘图标打开详细搜索',
                 win32con.NIIF_INFO, 6000))
        except Exception:
            pass

    def _fallback_browser(self, reason):
        """WebView2 初始化失败的降级：藏掉白窗、用默认浏览器打开界面，
        服务与托盘照常运行（与 webview.start 失败的 FATAL 路径同款体验）。"""
        try:
            self._safe_hide_main()          # pywebview 层（后台线程调可能卡/无效）
        except Exception:
            pass
        # Win32 兜底：从看门狗线程调 pywebview 的 hide 实测可能卡 20 秒或无效
        # （第 124 项同款根因）——ShowWindow 跨线程必生效，白窗必须真消失。
        try:
            hwnd = self.hwnd_main or find_hwnd(MAIN_TITLE)
            if hwnd:
                ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 0)   # SW_HIDE
        except Exception:
            pass
        try:
            import webbrowser
            webbrowser.open(self.url)
            _log_always('webview 不可用（' + reason + '），已用浏览器打开界面')
        except Exception:
            _log_always('webview 不可用且浏览器降级失败：' + reason)

    def _watch_loaded_or_fallback(self):
        """WebView2 初始化失败（0x700AA profile 锁等）时 pywebview 只打印
        「initialization failed」而不抛异常——窗口白屏挂着没人知道。这里等
        loaded 事件，等不到就降级浏览器（服务与托盘不受影响）。

        **超时不等于坏了**：机械硬盘 + 杀毒软件全盘扫描 + 首次启动的回填线程
        叠加时，二三十秒加载不完很正常。旧版固定等 10 秒、时间一到就藏窗口开浏览器，
        这类机器上用户看到的是「程序一闪，浏览器里打开一个网页」——比白屏更让人懵。
        现在的判据是渲染层：渲染子窗口活着就继续等（最多再等 60 秒），
        真白（渲染子窗全隐藏）才降级。"""
        try:
            if self.main and self.main.events.loaded.wait(30):
                return                                # 正常加载
        except Exception:
            return                                    # 事件机制异常，不动
        hwnd = self.hwnd_main or find_hwnd(MAIN_TITLE)
        try:
            if hwnd and _render_children_visible(hwnd):
                _log_always('页面加载超过 30 秒，但渲染层正常 —— 继续等（慢机器/杀软扫描）')
                try:
                    if self.main.events.loaded.wait(60):
                        return
                except Exception:
                    return
            if hwnd and _is_visible(hwnd):
                self._fallback_browser('WebView2 初始化失败（90 秒内没有渲染）')
        except Exception:
            pass

    def _tray_tip_loop(self):
        """第 224 项：扫描/索引时把托盘悬停提示换成实时进度，结束恢复。

        每 2s 读一次 brain.PROG（state/phase/done/total —— 索引管线现成的
        进度状态），只在文本变化时才打 NIM_MODIFY。扫描让位给主流程：
        循环里任何异常都吞掉，线程永不干扰主程序。"""
        last = None
        names = {'discover': '扫描文件', 'names': '扫描文件名', 'index': '建正文索引'}
        while self._alive:
            try:
                import brain as _b
                pr = getattr(_b, 'PROG', None) or {}
                if pr.get('state') == 'busy':
                    name = names.get(pr.get('phase') or '', '索引更新中')
                    done, total = pr.get('done', 0), pr.get('total', 0)
                    tip = ('Memex（%s %s/%s）' % (name, done, total)) if total else ('Memex（%s…）' % name)
                else:
                    tip = 'Memex（双击 Ctrl 唤出快搜）'
                if tip != last and self.tray is not None:
                    self.tray.set_tip(tip)
                    last = tip
            except Exception:
                pass
            time.sleep(2)

    def after_start(self):
        self.hwnd_mini = wait_hwnd(MINI_TITLE)
        self.hwnd_main = wait_hwnd(MAIN_TITLE)
        # 第 277 项：走到这里说明 WebView2 窗口真的起来了。运行标记推进到 ready，
        # 下次启动若发现停在 boot/ui，报告里就能看出「死在启动的哪一步」。
        try:
            error_report.mark_stage('ready')
            import breadcrumb
            breadcrumb.add('app', ev='ready')
        except Exception:
            pass
        # 环境快照：用户报「窗口不对/看不到」时，这一行就是现场数据。
        try:
            _log_always('[env] %s silent=%s target=%dx%d'
                        % (env_snapshot(self.hwnd_main), self.silent,
                           int(self._main_w), int(self._main_h)))
        except Exception:
            pass
        threading.Thread(target=self._watch_loaded_or_fallback, daemon=True).start()
        # 第 224 项：托盘悬停提示跟随索引进度（扫描/建索引时显示 N/M，结束恢复）
        threading.Thread(target=self._tray_tip_loop, daemon=True).start()
        # 屏幕截图与 RapidOCR 离线模型后台预热（消除首次使用时的 2~3 秒动态加载延迟）
        def _bg_prewarm_snip():
            time.sleep(2.0)
            try:
                import snip_ocr
                snip_ocr.prewarm()
            except Exception as e:
                _log('snip prewarm error: %r' % (e,))
        threading.Thread(target=_bg_prewarm_snip, daemon=True, name='snip-prewarm').start()
        # silent 模式主窗口按非 hidden 创建（见 create 里的说明），等 Form.Shown
        # 真正置位、events.shown 可用之后再收起来。用事件而不是固定 sleep：
        # 窗口只露出一瞬间，开机自启不会闪一大下。
        if self.silent:
            threading.Thread(target=self._hide_after_shown, daemon=True).start()
        # 【别在"马上要隐藏"的窗口上调 pywebview 的 resize()】它走 WinForms 的
        # Bounds 赋值，会把刚 hide 掉的窗口重新显示出来；而 WebView2 渲染层不跟
        # （没有真正的尺寸变化 → 收不到 WM_SIZE → 控制器仍认为窗口不可见）→
        # 外壳显示、内容全白。2026-11-07 实测：150% 缩放（dpr=1.5）下开机自启
        # 100% 必现，日志形如
        #   fit_main_dpi: dpr=1.50 target=1280x892 visible_before=True
        #   _safe_hide_main ok
        #   hide_after_shown: visible=True    ← 隐藏后 2ms 又被顶回来
        # 现在尺寸校验收口在 _fit_main_size：它用 SetWindowPos（不改可见性）且
        # 只在 CSS 视口真的偏了才动手，所以静默启动也能安全调用 —— 不再需要
        # 「非 silent 才修正」这种按缩放/启动方式分叉的逻辑。
        # 自绘标题栏（第 180 项）：先摘系统标题栏，再让 _fit_main_size 按新的
        # 客户区重算 —— 反过来的话窗口会被撑高一个标题栏的高度。
        self.custom_titlebar = False
        if self._titlebar_enabled():
            self.custom_titlebar = caption_off(self.hwnd_main, True)
            if self.custom_titlebar:
                round_corners(self.hwnd_main)
                # 第 343 项：把 10px 非客户区整块吃掉 —— 客户区占满整窗后，
                # 顶部那条 caption 带**根本不存在**，不用再靠定时刷颜色去压。
                install_nc_guard(self.hwnd_main)
                paint_frame(self.hwnd_main)
                frame_invisible(self.hwnd_main)
                _log('custom titlebar: WS_CAPTION stripped')
            else:
                _log('custom titlebar: 摘除失败，退回系统标题栏')
        self._fit_main_size(initial=True)   # 只有启动这一趟允许校正 DPI 换算
        # ⚠️ 必须在 _fit_main_size **之后**再确认一次样式位。
        # 实测（第 181 项）：日志已经打过 stripped，可 fit 里一旦真的 resize，
        # WinForms 会按 FormBorderStyle 把 WS_CAPTION 原样写回来 ——
        # 窗口又有了系统标题栏，而前端照着 ping 照画 HTML 标题栏 → **两条标题栏**。
        # 判断「到底摘没摘」只能读样式位，不能信第一次的返回值。
        if self.custom_titlebar:
            if not caption_off(self.hwnd_main, True):
                self.custom_titlebar = False
                _log('custom titlebar: fit 后样式被写回，退回系统标题栏')
            else:
                # 常驻守卫（不是「延迟两次」）：实测写回不止一次，resize / DPI /
                # 布局变化都会重新触发，所以只能持续盯
                try:
                    threading.Thread(target=self._titlebar_guard, daemon=True).start()
                except Exception:
                    pass
        # 把「实际」结果告诉 brain：/api/ping.titlebar 必须反映窗口现状，
        # 不能读配置 —— 用户改了开关没重启时，配置是新值、窗口是旧样式，
        # 只看配置会出现双标题栏（第 180 项）。
        try:
            import brain as _b
            _b.TITLEBAR_ACTIVE = bool(self.custom_titlebar)
        except Exception:
            pass
        self._dbg_autohelp()
        round_corners(self.hwnd_mini)
        soft_shadow(self.hwnd_mini)
        # 第 363 项：快搜窗那圈边框不要玻璃（玻璃=透出背后的窗口=白边）
        matte_frame(self.hwnd_mini, _LAST_THEME.get('bg') or ui_bg_rgb())
        # 第 343 项：快搜窗同样把非客户区归零，顶部才不会有 caption 带
        install_nc_guard(self.hwnd_mini)
        frame_invisible(self.hwnd_mini)
        # 第 359 项：mini 的窗口底色（那圈白边）也要跟着当前主题走
        _bg_fix = _LAST_THEME.get('bg') or ui_bg_rgb()
        set_window_background(self.mini, _bg_fix)
        set_window_background(self.main, _bg_fix)
        # 子窗口（WebView2）要把边缘几像素让出来，否则边缘拖不动
        install_child_hit_guard(self.hwnd_main)
        install_child_hit_guard(self.hwnd_mini)
        # 第 342/343 项：顶边已经归零 → 那条 caption 带**不存在**；
        # 左右下的边框色改由 `_nc_wndproc` 在 WM_ACTIVATE / WM_SIZE / WM_DPICHANGED /
        # WM_THEMECHANGED 时按事件补刷（apply_frame 幂等）。
        # 所以这里**不再起那个每 3 秒轮询的守护线程** —— 用户明确不要多出来的后台开销。
        # 两扇窗都换图标（标题栏 / Alt-Tab / 任务栏读的是同一个 WM_SETICON）
        apply_window_icon(self.hwnd_main)
        try:
            threading.Thread(target=self._late_dark_caption, daemon=True).start()
        except Exception:
            pass
        apply_window_icon(self.hwnd_mini)
        if self.start_mini:
            self.show_mini()
        self.hotkey = self._install_hotkey()
        try:
            self._sync_floating_ball()
        except Exception:
            pass
        self.start_tray()
        self._start_activate_listener()
        self._start_auto_watch()

    def _start_activate_listener(self):
        """让第一个实例监听一个命名事件：第二个实例启动时只 SetEvent，
        本进程在自己的线程里跑 show_main()，WebView2 才会正确同步可见性。

        跨进程直接 ShowWindow 会被渲染层（msedgewebview2.exe）立刻重新藏回，
        正是「silent 启动后唤醒仍白屏」的根因。只有第一个实例自己 show()，
        WebView2 的控制器才认为窗口可见、不再重藏渲染子窗口。

        事件底层走 ctypes 调 kernel32，不依赖 win32event（打包 exe 里它不在
        hiddenimports，import 会失败，导致这个监听器根本起不来）。"""
        k = _kernel32_event()
        if k is None:
            _log('activate listener: kernel32 事件接口不可用，跳过（将退回 ShowWindow 兜底）')
            return
        try:
            self._act_event = k.CreateEventW(None, 0, 0, 'SecondBrain_Activate_v1')
        except Exception as e:
            _log('activate listener: 创建命名事件失败：%r' % (e,))
            return
        if not self._act_event:
            _log('activate listener: CreateEventW 返回空句柄')
            return

        def loop():
            while True:
                try:
                    r = k.WaitForSingleObject(self._act_event, 0xFFFFFFFF)  # INFINITE
                except Exception:
                    break
                if r == 0:   # WAIT_OBJECT_0
                    try:
                        self.show_main()
                    except Exception:
                        _log_always('activate listener: show_main 失败\n'
                                    + traceback.format_exc())

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self._act_thread = t
        _log('activate listener: 命名事件 SecondBrain_Activate_v1 已监听')

    def _force_webview_visible(self, target='all'):
        """把 WebView2 控件自身的 Visible 设回 true（根因修复）。

        silent 启动隐藏主窗口时，WebView2 控制器的 IsVisible 被设成 false，渲染层
        的 Chrome_* 子窗口随之隐藏；光对外层子窗口 ShowWindow 会被控制器在下一次
        合成时重新藏回，所以必须直接动控件。pywebview 的 Window.native 是
        BrowserForm，其 .webview 就是 .NET 的 WebView2 控件。属性名跨版本可能变，
        整段包在 try 里，失败就退回 revive_webview 的治标逻辑。"""
        targets = []
        if target in ('all', 'main') and getattr(self, 'main', None):
            targets.append(('main', self.main))
        if target in ('all', 'mini') and getattr(self, 'mini', None):
            targets.append(('mini', self.mini))
        for tag, win in targets:
            try:
                impl = getattr(win, 'native', None)
                if impl is None:
                    continue
                ctrl = getattr(impl, 'webview', None)
                if ctrl is None:
                    continue
                _ui_set_visible(ctrl, tag)
            except Exception as e:
                _log('force webview visible (%s) FAILED: %r' % (tag, e))

    def _late_revive_mini_loop(self, timeout=3.0, interval=0.15):
        """快搜窗 show 后的短轮询自愈：确保 WebView2 渲染层子窗全部可见。"""
        end = time.time() + timeout
        while time.time() < end:
            if not getattr(self, 'mini_visible', False) or not getattr(self, 'hwnd_mini', 0):
                return
            if _render_children_visible(self.hwnd_mini):
                return
            revive_webview(self.hwnd_mini, quiet=True)
            self._force_webview_visible(target='mini')
            time.sleep(interval)

    def _fit_main_size(self, initial=False):
        """保证主窗口的 CSS 视口 ≈ 设计尺寸（_main_w × _main_h），**所有缩放档同一套逻辑**。

        为什么不再按 DPR 分支：旧实现是「dpr==1 直接 return，dpr>1 就 resize」，于是
        只有非 100% 的机器才会走进 resize 那条路——也正是这条路会把刚隐藏的窗口顶回来
        （WinForms 的 Bounds 赋值 → 白屏）。100% 的机器永远测不到那个 bug，反过来
        一旦哪天 pywebview 的换算变了，100% 的机器反而可能踩到没人测过的新路径。

        现在的做法跟缩放在几档无关：
          · 读客户区的物理像素，除以窗口真实 DPR → 得到「现在真实的 CSS 视口」；
          · 和设计尺寸比，偏差在 5% 以内就什么都不做（100% 缩放正常时就是这一步，
            连标题栏那十几个像素的差都不足以触发）；
          · 真偏了才用 set_client_size（SetWindowPos）校正 —— 不改变窗口可见性，
            所以隐藏状态下调用也安全，不会再白屏。
        """
        hwnd = self.hwnd_main or find_hwnd(MAIN_TITLE)
        if not hwnd:
            return
        try:
            dpr = window_dpr(hwnd)
            cw, ch = get_client_size(hwnd)
            if cw <= 0 or ch <= 0:
                return
            cur_w, cur_h = cw / dpr, ch / dpr
            tw, th = int(self._main_w), int(self._main_h)
            dev = max(abs(cur_w - tw) / max(1, tw), abs(cur_h - th) / max(1, th))
            # 用 _log_always：每次启动只打 1~2 行，但用户报「字太大/窗口不对」时，
            # 这行里就有 dpr、真实 CSS 视口和目标尺寸，比问一轮话快得多。
            _log_always('fit_main_size: dpr=%.2f css=%.0fx%.0f target=%dx%d dev=%.1f%% visible=%s'
                        % (dpr, cur_w, cur_h, tw, th, dev * 100, _is_visible(hwnd)))
            # 容差见 needs_fit：100% / 150% 的正常机器都返回 False，什么都不做。
            if not needs_fit(cur_w, cur_h, tw, th):
                return
            if not initial:
                # 第 206 项：窗口已经用过，尺寸再和设计值不一样，只可能是用户
                # 自己拖出来的。以前这里无条件 set_client_size()，用户每从托盘
                # 唤出一次窗口就被拉回 1306×900，等于「拖了白拖」。
                # 现在改成：把设计值更新成用户尺寸并记住，绝不反向改窗口。
                self._main_w, self._main_h = int(cur_w), int(cur_h)
                try:
                    import brain as _b
                    _b._settings_mark('win_w', int(cur_w))
                    _b._settings_mark('win_h', int(cur_h))
                except Exception:
                    pass
                _log_always('fit_main_size: 保留用户尺寸 %dx%d（不再拉回 %dx%d）'
                            % (int(cur_w), int(cur_h), tw, th))
                return
            ok = set_client_size(hwnd, tw, th)
            _log_always('fit_main_size: %s → client=%s visible_after=%s'
                        % ('resized' if ok else 'FAILED', get_client_size(hwnd), _is_visible(hwnd)))
        except Exception as e:
            _log('fit_main_size FAILED: %r' % (e,))

    def _move_hwnd(self, hwnd, x, y):
        """物理像素 SetWindowPos（只定位不改尺寸），mini.move() 的替代品。

        第 212 项实锤：pywebview 的 winforms move() 给 SetWindowPos 传
        **None 当 cx/cy**，而本进程给 user32.SetWindowPos 设了 c_int argtypes
        （全局共享，_win_ptr_funcs / 检查隐藏子窗口两处）—— None 一进来就是
        ArgumentError argument 5，所以 mini.move() 在本进程**从来没成功过**，
        place_mini 的兜底一直是坏的（窗口会停在上一次显示的位置）。
        这里统一走自己的 ctypes 调用，不再依赖 pywebview 的 move。"""
        u, _wt = _win()
        if not u or not hwnd:
            return False
        try:
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
            return bool(u.SetWindowPos(ctypes.c_void_p(hwnd), None, int(x), int(y),
                                       0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))
        except Exception as e:
            _log_always('_move_hwnd FAILED: %r' % (e,))
            return False

    def _place_main_on_cursor(self):
        """第 308 项：主窗口出现在**鼠标所在的那块屏**（多显示器 / 混合 DPI）。

        历史行为：建窗时按主屏算居中，副屏用户点托盘/悬浮球，窗口弹到另一块屏，
        感觉像「没打开」。这里只在两种情况下动窗口：
          ① 窗口所在的显示器 ≠ 鼠标所在显示器；
          ② 窗口明显不在自己那块屏的工作区里（比如 DPI 切换后被放大出界）。
        用户自己摆好的位置（同屏且在区内）一律不动，避免「每次显示都跳」。
        """
        try:
            u, wt = _win()
            hwnd = self.hwnd_main or find_hwnd(MAIN_TITLE)
            if not u or not hwnd:
                return
            pt = wt.POINT()
            if not u.GetCursorPos(ctypes.byref(pt)):
                return
            hm_cursor = u.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
            work = _monitor_work(hm_cursor)
            if not work:
                return
            hm_win = u.MonitorFromWindow(ctypes.c_void_p(hwnd), MONITOR_DEFAULTTONEAREST)
            wr = wt.RECT()
            u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(wr))
            w = int(wr.right - wr.left)
            h = int(wr.bottom - wr.top)
            if w <= 0 or h <= 0:
                return
            L, T, R, B = work
            inside = (wr.left >= L and wr.top >= T and wr.right <= R and wr.bottom <= B)
            same = bool(hm_win) and int(hm_win) == int(hm_cursor)
            if same and inside:
                return
            x = L + max(0, (R - L - w) // 2)
            y = T + max(0, (B - T - h) // 2)
            x = max(L, min(x, R - w))
            y = max(T, min(y, B - h))
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
            u.SetWindowPos(ctypes.c_void_p(hwnd), None, int(x), int(y), 0, 0,
                           SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            _log_always('place_main: cursor=(%d,%d) work=%s same=%s inside=%s -> (%d,%d) %dx%d'
                        % (int(pt.x), int(pt.y), work, same, inside, x, y, w, h))
            notify_resize(hwnd)
        except Exception as e:
            _log('place_main FAILED: %r' % (e,))

    def place_mini(self, tray=False):
        """双击 Ctrl（桌面上）的落点：跟着鼠标附近弹，别固定在中下。

        用户 23:44 反馈：「在桌面启动的应该跟随鼠标附近弹出或者居中以上弹出，
        而不是固定一个中下地方」。以前用 `mini.move(x, y)`（逻辑像素）走
        pywebview 的 DPI 换算，4K@150% 下容易落到屏幕下半截；这里统一按
        **物理像素** SetWindowPos，只定位不改尺寸（SWP_NOSIZE）。
        拿不到鼠标 / 显示器信息时回退到 mini_origin()（居中 + 17% 高度）。

        tray=True（第 212~215 项）：托盘点击的落点 —— **屏幕正中**（水平垂直
        都居中）。迭代史：贴鼠标/右下角挡托盘图标（212）→ 偏上视线竖跳（213）→
        底边贴任务栏会盖住任务栏图标（214）→ 用户拍板「就放中间」。
        双击 Ctrl「鼠标在哪弹在哪」的特性不受影响（tray=False 路径原样）。
        """
        try:
            u, wt = _win()
            hwnd = self.hwnd_mini
            if not u or not hwnd:
                x, y = mini_origin(hwnd)
                self._move_hwnd(self.hwnd_mini, x, y)
                return
            wr = wt.RECT()
            u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(wr))
            dpr = window_dpr(hwnd) or 1.0
            w = int(wr.right - wr.left) or int(round(MINI_W * dpr))
            h = int(wr.bottom - wr.top) or int(round(MINI_H * dpr))
            pt = wt.POINT()
            got = False
            try:
                got = bool(u.GetCursorPos(ctypes.byref(pt)))
            except Exception:
                pass
            work = None
            if got:
                try:
                    hmon = u.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
                    work = _monitor_work(hmon)
                except Exception:
                    pass
            if not work:
                work = cursor_monitor_rect() or primary_monitor_rect()
            if not work:
                x, y = mini_origin(hwnd)
                self._move_hwnd(hwnd, x, y)
                return
            L, T, R, B = work
            gap = int(round(18 * dpr))
            if tray:
                # 第 215 项：托盘点击 = 屏幕正中（水平垂直都居中）。
                # 迭代：212 右下角挡图标、213 偏上视线跳、214 底边贴任务栏
                # 会盖住任务栏图标 —— 用户拍板「就放中间」。
                x = L + max(0, (R - L - w) // 2)
                y = T + max(0, (B - T - h) // 2)
            elif got:
                x = int(pt.x) + gap
                y = int(pt.y) + gap
                # 右下放不下就翻到鼠标左上，始终留在工作区里
                if x + w > R - gap:
                    x = int(pt.x) - w - gap
                if y + h > B - gap:
                    y = int(pt.y) - h - gap
            else:
                x = L + max(0, (R - L - w) // 2)
                y = T + int(round((B - T) * 0.17))
            x = max(L + gap, min(x, R - w - gap))
            y = max(T + gap, min(y, B - h - gap))
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
            ok = bool(u.SetWindowPos(ctypes.c_void_p(hwnd), None, x, y, 0, 0,
                                     SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))
            _log_always('place_mini: cursor=(%s,%s) work=%s -> (%d,%d) %dx%d tray=%s ok=%s'
                        % (int(pt.x) if got else '-', int(pt.y) if got else '-',
                           work, x, y, w, h, tray, ok))
        except Exception as e:
            _log_always('place_mini FAILED: %r' % (e,))
            try:
                x, y = mini_origin(self.hwnd_mini)
                self._move_hwnd(self.hwnd_mini, x, y)
            except Exception:
                pass

    def _place_mini_on(self, rect):
        """把快搜窗摆到 rect 这块显示器上（物理像素定位）。

        为什么不用 `self.mini.move()`：pywebview 的坐标是「逻辑像素 × 窗口当前
        scale」，而混合 DPI 的多屏里每块屏的 scale 不一样，拿主屏的 scale 算副屏
        的坐标必然偏（偏得多了窗口就落到屏幕外，看起来像「快捷键失灵」）。
        这里先按物理像素把窗口挪进目标屏（不改尺寸），让 `GetDpiForWindow` 拿到
        那块屏真实的 DPI，再按 DPI 精确摆位。
        """
        hwnd = self.hwnd_mini
        u, wt = _win()
        if not hwnd or not u:
            return
        SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
        left, top, right, bottom = rect
        # ① 先落进目标屏（尺寸不动），窗口 DPI 会跟着变成那块屏的
        u.SetWindowPos(ctypes.c_void_p(hwnd), None, left + 40, top + 40, 0, 0,
                       SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        dpr = window_dpr(hwnd)
        wr = wt.RECT()
        u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(wr))
        h = int(wr.bottom - wr.top) or int(MINI_H * dpr)
        w = int(round(MINI_W * dpr))
        x = left + max(0, (right - left - w) // 2)
        y = top + int(round((bottom - top) * 0.17))
        u.SetWindowPos(ctypes.c_void_p(hwnd), None, x, y, w, h,
                       SWP_NOZORDER | SWP_NOACTIVATE)
        _log('place_mini: 副屏 dpr=%.2f rect=%s → (%d,%d) %dx%d'
             % (dpr, rect, x, y, w, h))

    # -- 快搜窗 -------------------------------------------------------
    def toggle_mini(self):
        if self.mini_visible and foreground_is(self.hwnd_mini):
            _log('hotkey → hide')
            self.hide_mini()
        else:
            _log('hotkey → show')
            self.show_mini()

    def show_mini(self, near_wnd=0, dock=False, tray=False, origin=''):
        """near_wnd / dock：贴着这个窗口摆成小窗（第 198 项）。

        dock=False 是双击 Ctrl 的老行为：大窗、鼠标附近。
        tray=True（第 212 项）：托盘点击 —— 右下角贴边落点，别甩到屏幕左边。
        origin='type'（第 244 项）：在 Windows 文件夹里**打字**唤出。这种要
        「跟资源管理器一样」：不置顶、点文件夹空白处就收起、再打字再出来。
        文件对话框吸附（origin='dialog'）仍保留第 205 项的失焦不收起。"""
        try:
            if not self.hwnd_mini:
                self.hwnd_mini = wait_hwnd(MINI_TITLE, 2.0)
            _log_always(f'show_mini begin near={near_wnd} dock={dock} tray={tray} origin={origin} hwnd={self.hwnd_mini}')
            self.mini.show()
            self.mini_visible = True
            self._mini_origin = origin
            # 第 343 项：快搜窗显示后同样确认一次非客户区归零（WinForms 会重装窗口过程）
            try:
                hm = self.hwnd_mini or find_hwnd(MINI_TITLE)
                if hm and frame_insets(hm) > 0:
                    install_nc_guard(hm, force=True)
                if hm:
                    install_child_hit_guard(hm)
                    # 第 359 项：显示前把窗口底色按当前配色刷一遍（重装过程/DWM 重置
                    # 都可能把那一圈打回默认白）
                    set_window_background(self.mini, _LAST_THEME.get('bg') or ui_bg_rgb())
                    matte_frame(hm, _LAST_THEME.get('bg') or ui_bg_rgb())
            except Exception:
                pass
            # 第 244 项：打字唤出的头 2 秒是 WebView2 起焦点 + replay 按键的窗口，
            # 这段时间里的 blur 是初始化副作用，不能当成「用户点了空白处」。
            # _on_type() 把按键送完会立刻把 guard 归零，用户随时可以点空白处收起。
            if origin == 'type':
                self._type_guard = time.time() + 2.0
                # 不继承上一次「文件对话框」残留的 _auto_dlg：否则 hide_mini()
                # 会写 30 秒静音、还把焦点抢回那个旧对话框（用户明明点的是
                # 资源管理器空白处）。
                self._auto_dlg = 0
            self._mini_mode = 'dock' if (near_wnd and dock) else 'large'
            if near_wnd and dock:
                self.place_mini_near(near_wnd)
            else:
                self._dock = None
                if near_wnd:
                    self.place_mini_near(near_wnd)
                else:
                    # 从「贴角小窗」切回双击 Ctrl 的大窗：宽度必须显式还原，
                    # 否则前端算出来的高度没变时 resize() 会提前 return，永远停在 460。
                    # 第 212 项：resize 失败不许连累 place_mini —— 以前它俩共用一个
                    # try，resize 一抛 place_mini 就被跳过，窗口停在上次的位置
                    #（托盘点右下角、弹在左下角就是这么来的）。
                    try:
                        self.mini.resize(MINI_W, MINI_H)
                    except Exception as e:
                        _log_always('mini resize non-fatal: %r' % (e,))
                    self.place_mini(tray=tray)   # 位置矫正放在 show 之后：move 本身会触发显示
            # 第 244 项：打字唤出 / 文件对话框吸附的快搜窗**不置顶**（跟触发窗口同一 Z 序）。
            # topmost(pin=False) 会主动设 HWND_NOTOPMOST，但抢前台完全保留 ——
            # 打字必须把焦点拿到搜索框，否则按键会打进资源管理器。
            # 只有热键唤出的大窗 / 托盘点击才置顶。
            is_docked = bool(near_wnd or dock or getattr(self, '_dock', None))
            pin = (origin != 'type') and not bool(dock or near_wnd or origin == 'dialog' or is_docked)
            # 治白屏：WebView2 渲染层子窗口唤醒 + 控件 Visible 设回 True
            self._force_webview_visible(target='mini')
            revive_webview(self.hwnd_mini, quiet=True)
            threading.Thread(target=self._late_revive_mini_loop, daemon=True).start()
            if not topmost(self.hwnd_mini, pin=pin):
                _log_always(f'show_mini focus FAIL fg={foreground_desc()}')
                time.sleep(0.04)
                topmost(self.hwnd_mini, tries=3, pin=pin)
            try:
                from_snip = bool(getattr(self, '_from_snip', False))
                self._from_snip = False
                js_arg = '{from_snip: true}' if from_snip else '{}'
                self.mini.evaluate_js(f'window.__onShow&&window.__onShow({js_arg})')
            except Exception:
                pass
            _log('show_mini done')
        except Exception as e:
            _log(f'show_mini EXCEPTION: {type(e).__name__}: {e}')

    def _drop_dock(self):
        """锚点窗口已销毁：收起贴角小窗，但**不**写 30 秒静音。

        用户关掉「选择文件」再开一次，应该立刻重新吸附，不该被 hide_mini()
        的 _auto_muted 挡住半分钟。"""
        _log('drop dock: anchor gone')
        try:
            self.mini.hide()
        except Exception:
            pass
        self.mini_visible = False
        self._dock = None
        self._auto_dlg = 0

    def hide_mini(self):
        _log('hide_mini')
        try:
            self.mini.hide()
        except Exception:
            pass
        self.mini_visible = False
        self._dock = None
        # 用户自己收起的（Esc / 点了别处 / 跳完目录）：这个对话框别再自动弹回来。
        # 30 秒后解除，够他重新点一次「另存为」再进来。
        if self._auto_dlg:
            try:
                self._auto_muted[self._auto_dlg] = time.time() + 30
            except Exception:
                pass
            try:
                quick_switch.focus_window(self._auto_dlg)
            except Exception:
                pass
            self._auto_dlg = 0

    def on_mini_blur(self):
        """失焦处理：见 MiniApi.blurred 的说明。返回 True 表示真的收起了。

        「鼠标还在窗口上」只在刚 resize 完的一小段时间里算数——那时候的失焦是
        WebView2 重排的副作用。超过这个窗口还失焦，就说明用户是按键切走的
        （Alt+Tab / Win），这时候赖着不收反而烦人，必须收起。"""
        if not self.mini_visible:
            return False
        # 第 244 项：**在文件夹里打字唤出**的快搜窗，用户停止输入、点了文件夹
        # 空白处（资源管理器重新拿回焦点）就收起 —— 不能一直占着一块地方。
        # 再随便打几个字会走 _on_type → show_mini(origin='type') 重新弹出。
        # 头 2 秒是 WebView2 起焦点 + replay 按键的初始化期，那段 blur 不是用户
        # 点的，忽略（_on_type 送完键会把 guard 归零）。
        if getattr(self, '_mini_origin', '') == 'type':
            if time.time() < getattr(self, '_type_guard', 0):
                _log('blur → 打字唤出初始化期，忽略')
                return False
            _log('blur → 打字唤出，点空白处收起')
            self.hide_mini()
            return True
        # 第 205 项：贴角（吸附）模式失焦**一律不收起**。
        # 用户点对话框里的文件名框、或者拖动对话框，WebView2 都会先收到 blur；
        # 以前直接 hide_mini()，于是「一点就没、一拖就没」，吸附根本无从谈起。
        # 不再 topmost 抢焦点，避免和用户正在点的窗口来回拉扯。
        # Esc / 点结果跳目录 / 锚点窗口被销毁仍照常收起。
        if self._dock:
            _log('blur → dock 模式，保持吸附不收起')
            return False
        time.sleep(0.14)               # 等焦点切换稳定，别被中间态骗了
        if foreground_is(self.hwnd_mini):
            _log('blur → 前台还是自己，忽略')
            return False
        since = time.time() - getattr(self, '_resize_ts', 0)
        _log(f'blur since_resize={since:.2f} cursor_in={cursor_inside(self.hwnd_mini)} '
             f'fg={foreground_desc()}')
        if since < 0.7 or (since < 1.6 and cursor_inside(self.hwnd_mini)):
            topmost(self.hwnd_mini, tries=5, pin=(not bool(self._dock)))   # resize 的副作用，抢回来
            return False
        self.hide_mini()
        return True

    def resize_mini(self, h):
        if not self.mini_visible:
            return                     # 隐藏状态下 resize 会让窗口自己蹦出来
        h = max(88, min(int(h), 1000))
        dock = self._dock
        if dock:
            # 贴角小窗：宽度是 place_mini_near 按触发窗口算出来的，
            # 高度也不许超过那个窗口的可用高度（内容再多就靠行数上限挡）。
            w = int(dock.get('wcss') or MINI_W_SMALL)
            h = min(h, int(dock.get('maxh') or 1000))
        else:
            # 第 202 项：贴角模式下 _dock 偶尔为空（hide_mini/fallback），
            # 旧代码直接回退 MINI_W=760，会把刚收窄的小窗又撑成半屏。
            w = MINI_W if getattr(self, '_mini_mode', 'large') == 'large' else MINI_W_SMALL
        _log(f'resize → {h} w={w}')
        try:
            self.mini.resize(w, h)
        except Exception:
            return
        if dock:
            self._dock_move(h)
        # 改窗口尺寸会让 WebView2 重新布局，实测有约三分之一概率把激活状态弄丢，
        # 表现为「结果刚出来，窗口就自己关了」（失焦逻辑干的）。
        # 记个时间窗，这段时间内的失焦一律当作 resize 的副作用抢回来。
        self._resize_ts = time.time()

        def _refocus():
            time.sleep(0.12)
            if self.mini_visible and not foreground_is(self.hwnd_mini):
                _log('refocus 抢焦点')
                topmost(self.hwnd_mini, tries=4, pin=(not bool(self._dock)))
        threading.Thread(target=_refocus, daemon=True).start()

    def _ui_hang_watchdog(self):
        """窗口消息循环卡死时，把线程栈落进 debug.log。

        第 329 项：14:36 那次死锁（`ctrl.Visible` 跨线程），事后要靠 py-spy 挂进
        活着的进程才抓到现场。这里每 3 秒给主窗发一条 WM_NULL（SMTO_ABORTIFHUNG），
        连续两次超时就 dump 一份 —— 下次再「点了就卡死」，日志里直接有答案。
        """
        import ctypes
        last = 0.0
        streak = 0
        while True:
            try:
                time.sleep(3)
                hwnd = int(getattr(self, 'hwnd_main', 0) or 0)
                if not hwnd:
                    hwnd = int(find_hwnd(MAIN_TITLE) or 0)
                    if hwnd:
                        self.hwnd_main = hwnd
                if not hwnd:
                    streak = 0
                    continue
                res = ctypes.c_size_t(0)
                ok = ctypes.windll.user32.SendMessageTimeoutW(
                    ctypes.c_void_p(hwnd), 0, 0, 0,
                    0x0001 | 0x0002,        # SMTO_BLOCK | SMTO_ABORTIFHUNG
                    2000, ctypes.byref(res))
                if ok:
                    streak = 0
                    continue
                streak += 1
                if streak >= 2 and time.time() - last > 120:
                    last = time.time()
                    _log_always('UI 无响应：连续 %d 次 WM_NULL 超时（hwnd=%d），dump 线程栈'
                                % (streak, hwnd))
                    try:
                        import brain as _bb
                        _bb._dump_thread_stacks('UI 无响应 hwnd=%d' % hwnd)
                    except Exception as e:
                        _log('ui hang dump FAILED: %r' % (e,))
            except Exception:
                pass

    # -- 主窗口 -------------------------------------------------------
    def show_main(self, q=None, panel=None, focus=None, lock_timeout=0):
        """从快搜窗跳过来：show 主窗口 + 设搜索词。

        这里【绝不能】在出错时退回 webbrowser.open。那是「窗口组件加载失败就退到
        浏览器模式」的老兜底，被滥用在这里之后，用户点一下「展示更多 …」就会
        冒出一个浏览器标签打开 127.0.0.1:8765/web/ —— 明明装的是 exe，看起来
        却像突然退化成网页，而且真正的异常被彻底掩盖。出错只记日志，界面必须
        留在桌面窗口里。

        新界面是 React，只认 /web/?q= 与 ?panel=。旧模板那句
        `qEl.value=…;go()` 在 React 里必然 ReferenceError，别再注入它。

        第 267 项：外面加两层面板防护 ——
        ① `_show_lock`：同一时刻只许一次在飞，托盘三连点不会变成三条线程互顶；
        ② `_hide_seq`：进来先记下"用户关过几次窗"，中途用户把详情栏关了，
           本次就不再导航、也不再把窗口按上去（这正是「关了又自己弹出来」）。
        """
        seq = int(getattr(self, '_hide_seq', 0) or 0)
        lock = getattr(self, '_show_lock', None)
        if lock is None:
            lock = threading.Lock()
            self._show_lock = lock
        if not lock.acquire(blocking=False):
            # 托盘 worker 带 lock_timeout：它已经把这次面板请求从队列里取走了，
            # 直接跳过就等于把这次点击丢了 —— 短等一次，别让「点了没反应」以新形式复现。
            got = False
            if lock_timeout:
                try:
                    got = lock.acquire(timeout=float(lock_timeout))
                except Exception:
                    got = False
            if not got:
                _log_always('show_main: 已有一次显示在进行，跳过这次重复触发（panel=%r）' % (panel,))
                return
        try:
            if seq != int(getattr(self, '_hide_seq', 0) or 0) or not getattr(self, '_alive', True):
                _log_always('show_main: 排队期间用户已关闭窗口/程序退出，放弃本次显示')
                return
            self._show_main_once(q, panel, focus, seq)
            # 第 343 项：WinForms 有可能在显示时把窗口过程装回去，显示完再确认一次
            try:
                hw = self.hwnd_main or find_hwnd(MAIN_TITLE)
                if hw and frame_insets(hw) > 0:
                    install_nc_guard(hw, force=True)
                if hw:
                    install_child_hit_guard(hw)
            except Exception:
                pass
        finally:
            lock.release()

    def _show_main_once(self, q=None, panel=None, focus=None, seq=0):
        """show_main 的实现体（只在拿到 _show_lock 后被调用）。"""
        qs = []
        if q:
            qs.append('q=' + quote(q))
        if panel:
            qs.append('panel=' + panel)
        if focus:
            # 第 175 项：托盘「反馈 / 报错」要直接摊到错误日志那一块，
            # 否则用户点完只看到设置页，还得自己去「更多设置」里翻。
            qs.append('focus=' + focus)
        rel = '/web/' + ('?' + '&'.join(qs) if qs else '')
        target = self.url + rel

        # ① 显示窗口。换深色标题栏、抢前台这类非关键动作各自单独吞异常，
        #    别让它们把整个流程带进一个「统一 except」再触发跳浏览器。
        self._want_main = True
        try:
            hwnd = self.hwnd_main or find_hwnd(MAIN_TITLE)
            if not hwnd:
                hwnd = wait_hwnd(MAIN_TITLE, 2.0)
                if hwnd:
                    self.hwnd_main = hwnd
            _log_always(f'show_main begin q={q!r} hwnd={hwnd} '
                 f'visible={_is_visible(hwnd)} loaded={getattr(self, "_main_loaded", False)}')

            # 第 267 项：走后端直连显示，不用 Window.show() 那层 _shown_call 门闩。
            if not pv_show(self.main):
                _log_always('show_main: pv_show 失败，退回 Win32 兜底显示')

            # pywebview 的 show() 在窗口被 hide 过之后偶尔不生效（Form.Visible 卡
            # 在 False）。用 Win32 ShowWindow 兜底，直接对原生句柄发 SW_SHOW。
            if hwnd:
                try:
                    import win32con
                    import win32gui
                    # 最小化窗口的 IsWindowVisible() 仍返回 True，Form.Show() 也不会
                    # 还原它（系统把窗口挪到 -32000,-32000 屏幕外）。点托盘/任务栏
                    # 图标时日志「show_main 成功、visible_now=True、fg 是自己」全绿，
                    # 屏幕却毫无变化 —— 2026-11-12 实测 iconic=True 复现。
                    # 必须先 SW_RESTORE，否则下面的 topmost / 抢前台全落在屏幕外。
                    if win32gui.IsIconic(hwnd):
                        _log_always('show_main: 窗口处于最小化，先 SW_RESTORE 拉回屏幕')
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    if not win32gui.IsWindowVisible(hwnd):
                        _log('show_main: Form.Show() 没生效，用 Win32 SW_SHOW 兜底')
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                except Exception as e:
                    _log(f'show_main: SW_SHOW fallback FAILED: {e}')
                try:
                    dark_caption(hwnd, effective_dark())
                    paint_frame(hwnd)        # 同上：每次显示都把外框底色压回去
                except Exception as e:
                    _log('dark caption on show failed: %r' % (e,))
                # 让 WebView2 重建渲染表面：给它补发一条 WM_SIZE。
                # 旧代码用 `self.main.resize(self.width, self.height)` 干这件事，但尺寸
                # 根本没变 —— 等于让 WinForms 再赋一次 Bounds，副作用（可见性/焦点）比
                # 收益大。SendMessage 只通知、不动几何，确定性更高。
                notify_resize(hwnd)
                # 尺寸兜底校正：正常机器这里 dev<=5% 直接返回，不产生任何窗口操作。
                self._fit_main_size()
                # 第 308 项：多显示器 / 混合 DPI 下，把窗口放到鼠标所在那块屏
                self._place_main_on_cursor()
                # 外壳 show 起来了，WebView2 的渲染子窗口却可能还藏着（silent 启动
                # 必现）：不唤醒的话用户看到的就是一整块白窗口。
                revive_webview(hwnd)
                # 根因：WebView2 渲染窗口的可见性由控件自身的 Visible（即
                # CoreWebView2Controller.IsVisible）决定，表单隐藏时它被设成 false，
                # 对外层子窗口 ShowWindow 只是治标、控制器下次合成又会藏回。
                # 必须把 WebView2 控件的 Visible 显式设回 true，渲染层才真正显示。
                self._force_webview_visible()
        except Exception:
            _log_always('show_main: 显示桌面主窗口失败\n' + traceback.format_exc())
            # MiniApi.detail() 进来之前已经把快搜窗藏了。主窗口起不来时不能让
            # 界面变成一片空，把快搜窗放回来，用户至少还能继续搜、再试一次。
            try:
                self.show_mini()
            except Exception:
                _log_always('show_main: 恢复快搜窗也失败\n' + traceback.format_exc())
            return

        # ② 导航带上搜索词。
        #    第 267 项：不论首屏还是后续，一律走后端 pv_load_url 整页导航。
        #    原来后续那次走 `self.main.evaluate_js('location.href=…')`，
        #    它被 pywebview 的 `_pywebviewready` 门闩挡着：门闩一旦被清空，
        #    每次托盘点击都空等 20 秒再抛异常 → 面板永远打不开。
        #    React 只认 URL 参数，整页导航本来就是最确定的一条路（第 250/263 项同款）。
        if seq != int(getattr(self, '_hide_seq', 0) or 0) or not self._want_main:
            _log_always('show_main: 显示途中用户已关掉详情栏，放弃导航（不再顶窗口）')
            return
        try:
            if not getattr(self, '_main_loaded', False):
                pv_load_url(self.main, target)
                self._main_loaded = True
            elif panel or rel != getattr(self, '_main_target', ''):
                # panel 必须每次都重新导航：React 的 ?panel= 只在挂载时读一次，
                # 用户关掉抽屉再点同一个托盘菜单，URL 没变也得强制重载才打得开。
                pv_load_url(self.main, target)
            self._main_target = rel
            # 导航后补一刀：页面若因重载把 ready 事件丢了，用探针实测后补回去，
            # 免得别的代码路径（evaluate_js）又回到「空等 20 秒」。
            threading.Thread(target=pv_heal_gate, args=(self.main,), daemon=True).start()
        except Exception:
            _log_always('show_main: 主窗口导航失败（窗口已显示，可点托盘「打开详细搜索」）\n'
                        + traceback.format_exc())

        # ③ load_url / 整页导航可能重建渲染窗口，等它稳定后再补一次唤醒，
        #    避免刚显示的窗口又被新建的 WebView2 视图盖成空白。WebView2 的
        #    合成是异步的：渲染层初始化慢（后台 IO 忙）时固定两拍不够，
        #    改成轮询式自愈（最多 10 秒，渲染子窗全可见才收工）。
        def _late_revive():
            _late_revive_loop(self)

        threading.Thread(target=_late_revive, daemon=True).start()

        # ④ 抢前台，失败也不影响前面几步。
        #    ⚠️ 主窗口绝不能 pin：那会把它永久钉在所有程序前面（第 192 项）。
        try:
            ok = topmost(hwnd, pin=False)
            _log_always(f'show_main topmost={ok} visible_now={_is_visible(hwnd)} '
                 f'fg={foreground_desc()}')
        except Exception:
            _log_always('show_main: 抢前台失败\n' + traceback.format_exc())

    def hide_main(self):
        # 第 267 项：只要用户关窗，就把 `_want_main` 放掉、把 `_hide_seq` +1。
        # 在飞/排队的 show_main 看到序号变了会主动放弃，不会再「20 秒后醒来」
        # 把刚关掉的详情栏顶回来。
        _bump_hide(self)
        try:
            self._remember_size()
        except Exception:
            pass
        pv_hide(self.main)

    # -- 钩子 / 托盘 / 悬浮球 -------------------------------------------
    def _install_hotkey(self):
        """装热键前先看设置：用户可能在前端关掉了快捷键。

        设置存在 settings.json 的 hotkey 与 hotkey_mode 字段，默认开（double_ctrl）。
        关掉后 stop hook，再次开启重新 start。前端 /api/settings 或 /api/hotkey 读写。"""
        if not self._hotkey_enabled():
            _log('快捷键已被设置关闭，不安装')
            self._hk_paused = True
            return None
        self._hk_paused = False
        try:
            import hotkey
        except Exception:
            return None
        try:
            mode = self._hotkey_mode()
            hk = hotkey.start_hotkey(self._hotkey_fired, mode=mode)
            if hk is None:
                print('全局快捷键没能装上（不影响其它功能）')
            return hk
        except Exception as e:
            print('热键安装失败:', e)
            return None

    @staticmethod
    def _settings_path():
        # 必须跟 brain.app_dir() 一致：打包后 __file__ 落在 _internal 里，
        # 以前写 HERE/settings.json 会写进 _internal，而 brain 从 exe 旁边读，
        # 表现为"关了热键重启又自己开了"。开发态两者本来就是同一个目录。
        import brain
        return brain.app_dir() / 'settings.json'

    @staticmethod
    def _titlebar_enabled():
        """自绘标题栏（第 180 项）。默认开：原生标题栏那种「按钮在窗口最左上、
        操作在内容区中间」的割裂感用户明确不要。读不到配置也当开。"""
        import json as _j
        try:
            p = App._settings_path()
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
                return bool(d.get('titlebar', True))
        except Exception:
            pass
        return True

    @staticmethod
    def _hotkey_enabled():
        """读 settings.json 的 hotkey 字段。默认开；文件不存在或格式错也开。"""
        import json as _j
        try:
            p = App._settings_path()
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
                return d.get('hotkey', True)
        except Exception:
            pass
        return True

    @staticmethod
    def _hotkey_mode():
        """读 settings.json 的 hotkey_mode 字段。'double_ctrl' | 'alt_space' | 'ctrl_space'。"""
        import json as _j
        try:
            p = App._settings_path()
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
                return d.get('hotkey_mode', 'double_ctrl')
        except Exception:
            pass
        return 'double_ctrl'

    @staticmethod
    def _set_hotkey(on):
        import json as _j
        p = App._settings_path()
        d = {}
        try:
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
        except Exception:
            pass
        d['hotkey'] = bool(on)
        p.write_text(_j.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')

    def set_hotkey(self, on):
        """前端调 /api/hotkey 切换时，通过 js_api 调到这里，实时启停热键。"""
        _log(f'set_hotkey on={on}')
        App._set_hotkey(on)
        mode = self._hotkey_mode()
        if on and self.hotkey is None:
            # 重新装
            import hotkey
            try:
                self.hotkey = hotkey.start_hotkey(self._hotkey_fired, mode=mode)
                self._hk_paused = False
            except Exception as e:
                _log(f'set_hotkey re-install FAILED: {e}')
                return False
            return self.hotkey is not None
        if not on and self.hotkey is not None:
            try:
                self.hotkey.stop()
            except Exception:
                pass
            self.hotkey = None
            self._hk_paused = True
            return True
        if on and self.hotkey is not None:
            self.hotkey.set_mode(mode)
        return True

    # -- 桌面悬浮球 ----------------------------------------------------
    @staticmethod
    def _floating_ball_enabled():
        import json as _j
        try:
            p = App._settings_path()
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
                return bool(d.get('floating_ball', False))
        except Exception:
            pass
        return False

    @staticmethod
    def _floating_ball_pos():
        import json as _j
        try:
            p = App._settings_path()
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
                return d.get('floating_ball_x', -1), d.get('floating_ball_y', -1)
        except Exception:
            pass
        return -1, -1

    @staticmethod
    def _save_floating_ball_pos(x, y):
        import json as _j
        try:
            p = App._settings_path()
            d = {}
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
            d['floating_ball_x'] = int(x)
            d['floating_ball_y'] = int(y)
            p.write_text(_j.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

    @staticmethod
    def _set_floating_ball(enabled):
        import json as _j
        try:
            p = App._settings_path()
            d = {}
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
            d['floating_ball'] = bool(enabled)
            p.write_text(_j.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

    def _sync_floating_ball(self, force=False):
        """按 settings.json 把悬浮球拉起 / 收起。

        第 269 项：以前每 ~2.4 秒才跑一次（埋在 `_auto_loop` 的 `tick % 16` 块里），
        用户在设置里点开关后要等好几秒球才出现 / 消失，感觉「很卡」。
        现在 `_auto_loop` 每拍都调这里，函数自己用 settings.json 的 mtime 做闸门：
        文件没变只花一次 stat（微秒级），变了才真读。开关 ~0.15 秒内生效。
        """
        want_ball = self._floating_ball_enabled()
        try:
            import floating_ball
            try:
                floating_ball.set_logger(_log_always)
            except Exception:
                pass
        except Exception:
            return
        if not self.floating_ball_mgr:
            self.floating_ball_mgr = floating_ball.get_manager(
                on_click=self._on_ball_click,
                on_snip=self._on_ball_snip,
                on_detail=self._on_ball_detail,
                on_translate=self._on_ball_translate,
                on_settings=self._on_ball_settings,
                on_disable=self._on_ball_disable,
                on_pos_changed=App._save_floating_ball_pos
            )
        is_running = self.floating_ball_mgr.is_running()
        self._ball_last_want = bool(want_ball)
        if want_ball and not is_running:
            x, y = self._floating_ball_pos()
            _log_always(f'floating_ball: 拉起 x={x} y={y}')
            self.floating_ball_mgr.start(x=x, y=y)
        elif not want_ball and is_running:
            _log_always('floating_ball: 收起')
            self.floating_ball_mgr.stop()

    def _sync_home_compact(self):
        """第 275 项：把 settings 里的 qs_home_compact 推给已开着的快搜窗。

        快搜窗的 HTML 是开窗那一刻从 /mini 拉的，值已经落进模板里了；窗口本身
        是复用的，用户改完设置如果不推这一下，就得等下次重启才看到效果 ——
        那正是本项目最烦的「改了没生效」。所以这里跟悬浮球一样，用 mtime 做闸门。
        """
        try:
            stamp = App._settings_path().stat().st_mtime_ns
        except Exception:
            stamp = 0
        if stamp == getattr(self, '_hc_stamp', None):
            return
        self._hc_stamp = stamp
        on = False
        cnt = 4
        show = True
        try:
            import json as _j
            p = App._settings_path()
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
                on = bool(d.get('qs_home_compact', False))
                show = bool(d.get('qs_home_show', True))
                try:
                    cnt = max(1, min(10, int(d.get('qs_home_count', 4) or 4)))
                except Exception:
                    cnt = 4
        except Exception:
            on = False
            cnt = 4
            show = True
        if getattr(self, '_hc_last', None) == (on, cnt, show):
            return
        self._hc_last = (on, cnt, show)
        wnd = getattr(self, 'mini', None)
        if wnd is None:
            return
        try:
            wnd.evaluate_js('window.__setHomeShow&&window.__setHomeShow(%s)'
                            % ('true' if show else 'false'))
            wnd.evaluate_js('window.__setHomeCompact&&window.__setHomeCompact(%s)'
                            % ('true' if on else 'false'))
            wnd.evaluate_js('window.__setHomeCount&&window.__setHomeCount(%d)' % cnt)
        except Exception as e:
            _log_always(f'home_compact push failed: {type(e).__name__}: {e}')

    def _sync_floating_ball_fast(self):
        """每拍调用的薄壳：settings.json 没变就只 stat 一次，什么都不做。"""
        try:
            stamp = App._settings_path().stat().st_mtime_ns
        except Exception:
            stamp = 0
        if not getattr(self, '_ball_seen', False) or stamp != getattr(self, '_ball_stamp', None):
            self._ball_stamp = stamp
            self._ball_seen = True
            self._sync_floating_ball()
            return
        # 文件没变：只在「该在却没在」时补一次（STA 线程被异常带走等）
        mgr = self.floating_ball_mgr
        if getattr(self, '_ball_last_want', False) and mgr is not None and not mgr.is_running():
            _log_always('floating_ball: 发现掉了，重新拉起')
            self._sync_floating_ball(force=True)

    def _on_ball_click(self):
        """悬浮球单击：快搜栏没开就开，已在前台就收。

        第 269 项：以前这里直接接 `toggle_mini`，而 `toggle_mini` 靠
        `self.mini_visible` 这个缓存值判断；万一缓存和真实可见性对不上，
        点一下就被判成「已经开着 → 收起」，看起来就是点了没反应。
        现在以**真实前台**为准，并把每一步打进 debug.log（`_log_always`），
        下次真机再出问题不用猜。"""
        try:
            vis = bool(self.mini_visible)
            fg = bool(self.hwnd_mini) and foreground_is(self.hwnd_mini)
            _log_always(f'ball click: mini_visible={vis} fg={fg} hwnd={self.hwnd_mini}')
            if vis and fg:
                self.hide_mini()
            else:
                self.show_mini()
        except Exception as e:
            _log_always(f'ball click EXCEPTION: {type(e).__name__}: {e}')
            raise

    def _on_ball_snip(self):
        try:
            import snip_ocr
            snip_ocr.trigger_snip(on_done=None, do_ocr=False)
        except Exception as e:
            _log('ball snip error: %r' % (e,))

    def _on_ball_detail(self):
        """悬浮球右键「详细搜索框」：直接开主窗口（第 287 项）。

        以前悬浮球只能唤快搜，想开主窗口得先在快搜里点「展示更多」，两步。
        这里不带 q：主窗口有自己记住的最近搜索词，别用快搜里那半截输入覆盖它。
        """
        try:
            _log_always('ball detail → show_main')
            self.show_main()
        except Exception as e:
            _log('ball detail FAILED: %r' % (e,))

    def _on_ball_translate(self):
        """悬浮球右键「聚合翻译」（第 287 项）：主窗口 + ?panel=translate。

        聚合翻译本身是主窗口里的弹层（frontend TranslateModal），不是独立窗口；
        这里走和第 267 项托盘面板同一条 open_panel 通道（单飞 worker + 最新请求胜出），
        页面自己在启动时把弹层摊开 —— 不再用 evaluate_js 注入。
        """
        try:
            _log_always('ball translate → open_panel(translate)')
            self.open_panel('translate')
        except Exception as e:
            _log('ball translate FAILED: %r' % (e,))

    def _on_ball_settings(self):
        try:
            self.show_main()
        except Exception:
            pass

    def _on_ball_disable(self):
        try:
            App._set_floating_ball(False)
            if self.floating_ball_mgr:
                self.floating_ball_mgr.stop()
        except Exception:
            pass

    def _hotkey_fired(self):
        with self._lock:
            self.toggle_mini()

    # -- 第 197 项：自动弹出 + 直接打字 -------------------------------
    @staticmethod
    def _auto_prefs():
        """settings.json 里的 auto_popup / type_search，默认都开。

        每 2.4 秒重读一次，用户在设置里一关就立刻停手，不用重启程序。
        """
        import json as _j
        try:
            p = App._settings_path()
            if p.exists():
                d = _j.loads(p.read_text(encoding='utf-8'))
                return bool(d.get('auto_popup', True)), bool(d.get('type_search', True))
        except Exception:
            pass
        return True, True

    @staticmethod
    def _qs_style():
        """第 245 项：弹窗样式定稿，只剩方案1（rich，带正文片段的卡片）。

        方案2（Listary 式紧凑表）已下线。保留这个函数是为了不改动
        _mini_params / dock rect 日志的调用形态；**永远返回 'rich'**，
        老 settings.json 里残留的 'tbl' 也不会让下线样式复活。
        """
        return 'rich'

    def _start_auto_watch(self):
        """一个后台线程盯「文件对话框出现了没」，外加装打字钩子。

        为什么轮询而不挂钩子：文件对话框是别的进程建的，SetWinEventHook 要跨进程
        回调，对方一卡事件就丢。0.4 秒一拍 EnumWindows 的开销实测可以忽略。
        """
        try:
            self._auto_ok, self._type_ok = App._auto_prefs()
        except Exception:
            pass
        try:
            threading.Thread(target=self._auto_loop, daemon=True,
                             name='sb-autowatch').start()
        except Exception as e:
            _log('auto watch FAILED: %r' % (e,))
        self._install_typewatch()
        self._install_anchor_hook()
        self._install_clickwatch()

    def _install_typewatch(self):
        try:
            import typewatch
        except Exception as e:
            _log('typewatch import FAILED: %r' % (e,))
            return
        try:
            self.typewatch = typewatch.start(self._on_type, self._type_active,
                                            is_dialog=quick_switch.is_file_dialog)
            _log('typewatch ok=%s' % bool(self.typewatch))
        except Exception as e:
            self.typewatch = None
            _log('typewatch install FAILED: %r' % (e,))

    def _install_clickwatch(self):
        """第 307 项：在资源管理器文件区点一下，已开着的文件对话框跟到那个目录。

        用户原话：「在另存为或者选择文件夹的时候，点击另一个已经打开了文件夹，
        选择文件夹这里就自动跳转选择那个文件夹的路径，就不用去找了。」

        判定放在 follow_click 里（只认 CabinetWClass/ExploreWClass 的**文件区**，
        工具栏/地址栏/状态栏都不算），跳转复用 quick_switch.jump()。
        """
        try:
            import follow_click
        except Exception as e:
            _log('follow_click import FAILED: %r' % (e,))
            return
        try:
            self.clickwatch = follow_click.start(self._on_folder_click,
                                                 enabled=self._blank_jump_on)
            _log('clickwatch ok=%s' % bool(self.clickwatch))
        except Exception as e:
            self.clickwatch = None
            _log('clickwatch install FAILED: %r' % (e,))

        # 第 329 项：UI 卡死看门狗（只写日志，不弹窗、不杀进程）
        try:
            threading.Thread(target=self._ui_hang_watchdog, daemon=True,
                             name='ui-hang-watch').start()
        except Exception as e:
            _log('ui hang watchdog start FAILED: %r' % (e,))

    @staticmethod
    def _blank_jump_on():
        """设置 → 文件搜索 → 「点一下对话框跟过去」。读不到就按开（出厂默认开）。"""
        try:
            import json as _j
            p = App._settings_path()
            if p.exists():
                return bool(_j.loads(p.read_text(encoding='utf-8')).get('blank_jump', True))
        except Exception:
            pass
        return True

    def _on_folder_click(self, hwnd):
        """在资源管理器里点了一下 → 若有开着的文件对话框，把它挪到当前目录。"""
        try:
            if not self._blank_jump_on():
                return
            path = quick_switch.window_folder(int(hwnd or 0))
            if not path:
                return
            dlg, _fe, _ae = quick_switch.find_dialog()
            if not dlg:
                return
            import follow_click
            if not follow_click.should_jump(path, quick_switch.dialog_dir(dlg)):
                return
            res = quick_switch.jump(path)
            _log('blank_jump: %s → %s (%s)' % (path, (res or {}).get('mode'),
                                               (res or {}).get('ok')))
        except Exception as e:
            _log('blank_jump FAILED: %r' % (e,))

    def _install_anchor_hook(self):
        """第 229 项：全局位置事件钩子（零抖动跟随的主力，对标 Listary）。

        Windows 只支持全局挂（不限于单窗口），回调首行按 hwnd 过滤，非锚点
        事件一次比较就返回。钩子线程常驻消息循环；quit() 里 Unhook + WM_QUIT。"""
        try:
            threading.Thread(target=self._anchor_hook_thread, daemon=True,
                             name='sb-anchorhook').start()
        except Exception as e:
            _log('anchor hook start FAILED: %r' % (e,))

    def _anchor_hook_thread(self):
        import ctypes
        from ctypes import wintypes as _wt
        u = ctypes.windll.user32
        EVENT_OBJECT_LOCATIONCHANGE = 0x8004
        EVENT_SYSTEM_MOVESIZESTART = 0x000A
        EVENT_SYSTEM_MOVESIZEEND = 0x000B
        WINEVENT_OUTOFCONTEXT = 0
        OBJID_WINDOW = 0
        WINEVENTPROC = ctypes.WINFUNCTYPE(
            None, ctypes.c_void_p, _wt.DWORD, _wt.HWND,
            _wt.LONG, _wt.LONG, _wt.DWORD, _wt.DWORD, _wt.DWORD)
        app = self

        def cb(_hook, _ev, hwnd, idobj, _idchild, _tid, _tevt, _tms):
            try:
                # 拖动期间是事件风暴：第一行过滤必须比 _follow_anchor 还早
                if not hwnd or idobj != OBJID_WINDOW:
                    return
                d = app._dock
                if not d or int(hwnd or 0) != int(d.get('hwnd') or 0):
                    return
                if _ev in (EVENT_SYSTEM_MOVESIZESTART, EVENT_SYSTEM_MOVESIZEEND):
                    # 模态移动循环开/关：追逐线程的开关（见 _on_drag_move_toggle）
                    app._on_drag_move_toggle(_ev == EVENT_SYSTEM_MOVESIZESTART)
                    return
                app._follow_anchor()
            except Exception:
                pass

        hook_cb = WINEVENTPROC(cb)
        self._anchor_cb = hook_cb          # 防 GC：回调必须比钩子活得久
        hook = u.SetWinEventHook(EVENT_OBJECT_LOCATIONCHANGE,
                                 EVENT_OBJECT_LOCATIONCHANGE, None,
                                 hook_cb, 0, 0, WINEVENT_OUTOFCONTEXT)
        self._anchor_hook = int(hook or 0)
        hook2 = u.SetWinEventHook(EVENT_SYSTEM_MOVESIZESTART,
                                  EVENT_SYSTEM_MOVESIZEEND, None,
                                  hook_cb, 0, 0, WINEVENT_OUTOFCONTEXT)
        self._anchor_hook2 = int(hook2 or 0)
        self._anchor_tid = int(ctypes.windll.kernel32.GetCurrentThreadId())
        if not hook:
            _log('anchor hook SetWinEventHook FAILED')
            return
        _log_always('anchor hook ok: LOCATIONCHANGE+MOVESIZE tid=%s'
                    % self._anchor_tid)
        msg = _wt.MSG()
        lp = ctypes.byref(msg)
        while u.GetMessageW(lp, None, 0, 0) > 0:
            u.TranslateMessage(lp)
            u.DispatchMessageW(lp)
        _log('anchor hook loop exit')

    def _on_drag_move_toggle(self, started):
        """第 229 项二轮：用户开始/结束**拖动**锚点窗口（模态移动循环）。

        WinEvent 不保证送达（系统忙会丢），只靠 LOCATIONCHANGE 跟随仍可能一顿
        一顿。拖动期间开一个 ~6ms 的追逐线程用 GetWindowRect 硬追，确定性
        ~150Hz 采样 —— Listary 手感的关键在这段；MOVESIZEEND 后收尾再贴一次。"""
        self._drag_move = started
        try:
            _log_always('drag chase %s: hwnd=%s'
                        % ('start' if started else 'end',
                           (self._dock or {}).get('hwnd')))
        except Exception:
            pass
        if started:
            try:
                if self.mini_visible and self.hwnd_mini:
                    u, _ = _win()
                    if u:
                        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
                        u.SetWindowPos(ctypes.c_void_p(self.hwnd_mini), ctypes.c_void_p(0), 0, 0, 0, 0,
                                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                threading.Thread(target=self._chase_thread, daemon=True,
                                 name='sb-chase').start()
            except Exception as e:
                _log('chase start FAILED: %r' % (e,))

    def _chase_thread(self):
        while self._drag_move and self._alive:
            try:
                self._follow_anchor()
            except Exception:
                pass
            time.sleep(0.006)
        # 收尾再贴一次：MOVESIZEEND 事件可能抢在最后一次摆位之前到
        try:
            self._follow_anchor()
        except Exception:
            pass

    def _type_active(self):
        """钩子每按一个键都会问一次，必须无阻塞：只读内存里的开关。"""
        return bool(self._type_ok and self._alive and not self.mini_visible)

    @staticmethod
    def _type_mini_should_hide(origin, visible, guard, foreground_is_mini):
        """第 244 项：打字唤出的快搜窗该不该因「前台已不是自己」收起。

        为什么不只靠页面的 blur 事件：WebView2 在跨进程焦点切换时偶尔不发
        `window.blur`（用户点了资源管理器空白处、或者 Alt+Tab 到别的程序，
        小窗还赖在屏幕上）。所以 _auto_loop 每 0.15 秒用这条纯函数兜一次底。
        纯函数是为了能单测打表，不依赖真实窗口。
        """
        return (origin == 'type' and bool(visible)
                and time.time() >= (guard or 0)
                and not foreground_is_mini)

    def _auto_loop(self):
        tick = 0
        dlg = 0
        while self._alive:
            # 第 205 项：拖动对话框时要「紧紧跟着」，0.4s 一拍肉眼能看出拖影，
            # 改成 0.15s；跟随每拍都做，开销只有两次 GetWindowRect。
            time.sleep(0.15)
            tick += 1
            try:
                self._follow_anchor()
            except Exception:
                pass
            if self.mini_visible and getattr(self, '_dock', None) and self.hwnd_mini:
                try:
                    d_hwnd = self._dock.get('hwnd')
                    is_active = bool(getattr(self, '_drag_move', False)
                                     or (d_hwnd and (quick_switch.is_foreground_dialog(d_hwnd) or foreground_is(d_hwnd)))
                                     or foreground_is(self.hwnd_mini))
                    u, _ = _win()
                    if u:
                        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
                        after = ctypes.c_void_p(-1) if is_active else ctypes.c_void_p(-2)
                        u.SetWindowPos(ctypes.c_void_p(self.hwnd_mini), after, 0, 0, 0, 0,
                                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                except Exception:
                    pass
            # 第 244 项：打字唤出的小窗，前台不再是自己（点了文件夹空白处 /
            # 切到别的程序）就收起。放在 re-dock 之前，别让对话框吸附逻辑把
            # 一个该收起的窗口又贴回去。
            try:
                if App._type_mini_should_hide(
                        getattr(self, '_mini_origin', ''), self.mini_visible,
                        getattr(self, '_type_guard', 0),
                        foreground_is(self.hwnd_mini)):
                    _log('type mini: 前台已不是自己 → 收起')
                    # 别让 hide_mini() 顺手写 30 秒静音、把焦点抢回某个旧对话框：
                    # 用户点的是文件夹空白处，下次打字必须能立刻再弹出来。
                    self._auto_dlg = 0
                    self.hide_mini()
            except Exception:
                pass
            if tick % 16 == 1:                   # 约 2.4 秒刷一次开关
                try:
                    self._auto_ok, self._type_ok = App._auto_prefs()
                except Exception:
                    pass
                try:
                    mode = self._hotkey_mode()
                    on = self._hotkey_enabled()
                    if not on and self.hotkey is not None:
                        self.hotkey.stop()
                        self.hotkey = None
                        self._hk_paused = True
                    elif on and self.hotkey is None:
                        import hotkey
                        self.hotkey = hotkey.start_hotkey(self._hotkey_fired, mode=mode)
                        self._hk_paused = False
                    elif on and self.hotkey is not None and getattr(self.hotkey, 'mode', None) != mode:
                        self.hotkey.set_mode(mode)
                except Exception:
                    pass
            # 第 269 项：悬浮球开关要「点一下就动」。以前和上面的热键同步一起
            # 压在 `tick % 16`（约 2.4 秒）里，用户点完设置要等几秒才看到球，
            # 观感就是卡。现在每拍都过一遍（settings.json 没变只 stat 一次）。
            try:
                self._sync_floating_ball_fast()
            except Exception:
                pass
            # 第 275 项：快搜窗「最近记录紧凑显示」开关也要点完就变。
            # 同样只在 settings.json 的 mtime 变了才 push（没变只 stat 一次）。
            try:
                self._sync_home_compact()
            except Exception:
                pass
            # find_dialog() 要枚举顶层窗口 + 递归查子窗口，降频到 0.3 秒一次
            if tick % 2 == 0:
                try:
                    dlg = quick_switch.find_dialog()[0]
                except Exception:
                    dlg = 0
            if dlg != self._auto_last:
                # 对话框换了 / 没了：让已经开着的快搜窗刷新提示条与推荐
                if self.mini_visible:
                    try:
                        self.mini.evaluate_js('window.__onDialog&&window.__onDialog()')
                    except Exception:
                        pass
                self._auto_last = dlg
            if self.mini_visible and tick % 14 == 0:   # 约 2.1 秒重拉候选目录
                try:
                    self.mini.evaluate_js('window.__onDirs&&window.__onDirs()')
                except Exception:
                    pass
            # 第 229 项六轮：mini 还开着但吸附在**别的**对话框上 —— 用户换了新
            # 对话框（旧的可能没关），锚点还在旧窗口上，拖新对话框小窗纹丝不动。
            # 六轮补刀：_dock 为空的**大窗**（双击 Ctrl 弹的快搜窗）也吃这条 ——
            # 用户 15:48 双击 Ctrl 弹大窗、16:0x 打开文件夹对话框，auto-popup 因
            # 「mini 已开」被跳过，大窗不跟随 = 「拖对话框不动弹」的另一半。
            # 前台是新对话框 → 换锚重贴/就地吸附，Listary 同款行为。
            if (dlg and self.mini_visible
                    and (not self._dock
                         or int(self._dock.get('hwnd') or 0) != int(dlg))):
                try:
                    if quick_switch.is_foreground_dialog(dlg):
                        _log_always('re-dock: anchor %s -> %s'
                                    % (self._dock.get('hwnd'), dlg))
                        self._auto_dlg = dlg
                        self.place_mini_near(dlg)
                        try:
                            self.mini.evaluate_js(
                                'window.__rescale&&window.__rescale(%s)'
                                % json.dumps({'maxh': self._dock.get('maxh')},
                                             ensure_ascii=False))
                        except Exception:
                            pass
                except Exception:
                    pass
            if not self._auto_ok or not dlg or self.mini_visible:
                continue
            if self._auto_muted.get(dlg, 0) > time.time():
                continue
            try:
                if not quick_switch.is_foreground_dialog(dlg):
                    continue
            except Exception:
                continue
            _log('auto popup: dialog=%s' % dlg)
            self._auto_dlg = dlg
            self.show_mini(near_wnd=dlg, dock=True, origin='dialog')
            try:
                self.mini.evaluate_js(
                    'window.__autoShow&&window.__autoShow(%s)'
                    % json.dumps(self._mini_params(), ensure_ascii=False))
            except Exception:
                pass

    def _mini_params(self, **kw):
        """宿主推给页面的参数：小窗标记 + 可用高度上限 + 固定样式（第 199/245 项）。"""
        d = self._dock or {}
        out = {'small': True, 'style': App._qs_style()}
        if d.get('maxh'):
            out['maxh'] = int(d['maxh'])
        out.update(kw)
        return out

    def _on_type(self, ev):
        """在文件夹里直接打字：弹快搜窗，把刚才那几个键送进去。

        跑在钩子的消费线程里，可以慢；钩子那边正在等 release()，最多等 2 秒。
        """
        tw = self.typewatch
        try:
            hwnd = int((ev or {}).get('hwnd') or 0)
            if not self._type_active():
                if tw:
                    tw.release()
                return
            scope = ''
            try:
                scope = quick_switch.folder_for(hwnd)
            except Exception:
                scope = ''
            _log('type -> show mini scope=%r origin=type' % (scope or ''))
            self.show_mini(near_wnd=hwnd, dock=True, origin='type')
            try:
                self.mini.evaluate_js(
                    'window.__autoShow&&window.__autoShow(%s)'
                    % json.dumps(self._mini_params(scope=scope or ''),
                                 ensure_ascii=False))
            except Exception:
                pass
            # 等焦点真的落到快搜窗：show() 到 WebView2 拿到焦点要一两百毫秒，
            # 这期间按的键钩子还在替我们吞着，所以不会漏字。
            ok = False
            for _ in range(60):
                if foreground_is(self.hwnd_mini):
                    ok = True
                    break
                time.sleep(0.02)
            keys = tw.release() if tw else []
            # 第 244 项：按键送完就立刻解除「初始化保护」，此后用户点文件夹
            # 空白处（blur）马上收起；没送完之前不收，避免打字被打断。
            # 焦点没抢到时留 5 秒，让用户有机会点一下小窗，别一闪就没。
            self._type_guard = 0.0 if ok else time.time() + 5.0
            if not keys:
                return
            import typewatch
            if ok:
                typewatch.replay(keys)
                _log('type replay %d keys' % len(keys))
            else:
                # 焦点没抢过来就别注入，否则字会打进资源管理器（还会选中一堆文件）。
                # 退化成「把能还原的字符填进搜索框」，剩下的用户接着打。
                txt = typewatch.keys_to_text(keys)
                _log('type focus FAILED, fallback text=%r' % txt)
                if txt:
                    try:
                        self.mini.evaluate_js(
                            'window.__autoQuery&&window.__autoQuery(%s)'
                            % json.dumps(txt, ensure_ascii=False))
                    except Exception:
                        pass
        except Exception as e:
            _log('on_type FAILED: %r' % (e,))
            try:
                if tw:
                    tw.release()
            except Exception:
                pass

    def _work_rect(self):
        """屏幕工作区（排除任务栏），物理像素；拿不到返回 None。"""
        try:
            u, wt = _win()
            if not u:
                return None
            r = wt.RECT()
            # SPI_GETWORKAREA = 0x0030
            if u.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
                return (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            pass
        try:
            return cursor_monitor_rect() or primary_monitor_rect()
        except Exception:
            return None

    def _dock_xy(self, actual_w, actual_h):
        """按锚点窗口当前 rect 算小窗位置。对话框=外部下方居中；文件夹=右下角贴角。

        第 202 项：Listary 那种「长在文件夹上」的手感 —— 不抢戏、水平居中、
        在对话框**外面**；文件夹移动时 `_follow_anchor()` 会带着它一起走。
        """
        d = self._dock or {}
        left, top, right, bottom = d.get('rect') or (0, 0, 0, 0)
        padx = int(d.get('padx') or 0)
        pady = int(d.get('pady') or 0)
        gap = int(d.get('gap') or 0)
        if d.get('mode') == 'dlg':
            x = int(round(left + (right - left - actual_w) / 2.0))
            work = self._work_rect()
            if work:
                L, T, R, B = work
                fits_below = bool(bottom + actual_h + gap <= B)
                fits_above = bool(top - actual_h - gap >= T)
                if fits_below:
                    y = bottom + gap
                elif fits_above:
                    y = top - actual_h - gap
                else:
                    y = max(top + gap, bottom - actual_h - pady)
                x = max(L + gap, min(x, R - actual_w - gap))
                y = max(T + gap, min(y, B - actual_h - gap))
            else:
                fits_below = d.get('fits_below', True)
                if fits_below:
                    y = bottom + gap
                else:
                    y = max(top + gap, bottom - actual_h - pady)
                x = max(left + padx, min(x, right - actual_w - padx))
        else:
            x = max(left + padx, right - padx - actual_w)
            y = max(top + pady, bottom - pady - actual_h)
            # 硬钳制：窗口实测宽度再离谱，右边界也不许越出触发窗口
            x = min(x, right - padx - actual_w)
        # 副屏在左/上方时坐标可以是负数，不能 max(0) 截到主屏去
        return int(x), int(y)

    def _rescale_dock(self, d):
        """第 229 项：锚点对话框尺寸变化 → 宽度=对话框宽×0.5（上限 720）、
        高度上限重算，页面 __rescale 重排行数。宽差 <16 css px 不动，防拖拽抖动。

        七轮起**在跟随锁外**跑，且 120ms 自节流 + 独立锁（同一时刻只允许一个
        rescale 在飞）—— 里面有 mini.resize / evaluate_js 跨线程调用，可能长时间
        阻塞，绝不能让它拖死跟随本身。begin/end 都留 _log_always 实锤。"""
        try:
            now = time.time()
            if now - self._rescale_ts < 0.12:
                return
            if not self._rescale_lock.acquire(False):
                return
            self._rescale_ts = now
            _log_always('dock rescale begin: hwnd=%s' % (d.get('hwnd'),))
            left, top, right, bottom = d.get('rect') or (0, 0, 0, 0)
            dpr = d.get('dpr') or window_dpr(self.hwnd_mini) or 1.0
            is_dlg = d.get('mode') == 'dlg'
            win_w_css = (right - left) / dpr
            avail_h = max(200.0, (bottom - top - 2 * int(d.get('pady') or 0)) / dpr)
            if is_dlg:
                w_css = int(round(max(MINI_W_MIN, min(720, win_w_css * 0.5,
                                                      win_w_css - 28))))
            else:
                w_css = int(round(max(MINI_W_MIN, min(MINI_W_SMALL, win_w_css - 28))))
            maxh_css = int(round(min(MAXH_CAP, max(MAXH_MIN,
                                                   avail_h * (0.72 if is_dlg else 0.50)))))
            if abs(w_css - int(d.get('wcss') or 0)) < 16 \
                    and maxh_css == int(d.get('maxh') or 0):
                return
            d['wcss'] = w_css
            d['maxh'] = maxh_css
            try:
                self.mini.resize(w_css, MINI_H_SMALL)
            except Exception:
                pass
            wr = wt.RECT()
            u.GetWindowRect(ctypes.c_void_p(self.hwnd_mini), ctypes.byref(wr))
            d['w'] = int(wr.right - wr.left) or d.get('w')
            d['h'] = int(wr.bottom - wr.top) or d.get('h')
            try:
                self.mini.evaluate_js('window.__rescale&&window.__rescale(%s)'
                                      % json.dumps({'maxh': maxh_css}, ensure_ascii=False))
            except Exception:
                pass
            _log_always('dock rescale: wcss=%s maxh=%s' % (w_css, maxh_css))
        except Exception as e:
            _log('rescale FAILED: %r' % (e,))
        finally:
            try:
                self._rescale_lock.release()
            except Exception:
                pass

    def _follow_anchor(self):
        """锚点窗口移动/缩放时，小窗跟着走（尺寸变化交给 _rescale_dock）。

        第 229 项：跟随主力换成 SetWinEventHook(EVENT_OBJECT_LOCATIONCHANGE)
        全局位置钩子 —— 拖动对话框时每帧都有事件，小窗逐帧贴上去（Listary 手感）；
        0.15s 轮询保留作兜底（rect 没变就什么也不做）。227① 曾写过头一版钩子，
        被 cp 覆盖回退后没进 63 次重打的 exe（CArchive 解剖实锤），这次连同
        preflight 守卫一起补上，防再丢。
        """
        d = self._dock
        if not d or not self.mini_visible or not self.hwnd_mini:
            return
        # 第 229 项二轮 ①：读-算-写必须原子。钩子线程 / 0.15s 轮询 / 追逐线程
        # 三处并发进来，不加锁就会出现「读旧 rect 的写者后到、把位置拽回去」的
        # 乱序抖动；拿不到锁直接放弃（下一个事件马上就来，不值得排队等旧活）。
        if not self._follow_lock.acquire(False):
            _log('follow skip: lock busy')
            return
        rescale_needed = False
        try:
            u, wt = _win()
            if not u:
                return
            if not u.IsWindow(ctypes.c_void_p(d['hwnd'])):
                self._drop_dock()
                return
            r = wt.RECT()
            if not u.GetWindowRect(ctypes.c_void_p(d['hwnd']), ctypes.byref(r)):
                return
            rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
            old = d.get('rect')
            if rect == old:
                return
            d['rect'] = rect
            # 第 229 项七轮：只**标记**尺寸变化，重算挪到锁外做 —— rescale 里有
            # mini.resize / evaluate_js 这类跨线程调用，在锁内一旦阻塞，锁就永远
            # 不放，钩子/轮询/追逐三条跟随路一起冻结 = 「小窗留在原地不动」
            # （16:28 用户拖动实测：chase 正常触发但小窗纹丝不动，第一嫌疑）。
            if old and (rect[2] - rect[0] != old[2] - old[0]
                        or rect[3] - rect[1] != old[3] - old[1]):
                rescale_needed = True
            if d.get('mode') == 'dlg':
                work = self._work_rect()
                if work:
                    maxh_px = int(round(int(d.get('maxh') or 300) * float(d.get('dpr') or 1.0)))
                    d['fits_below'] = bool(rect[3] + maxh_px <= work[3] - int(d.get('gap') or 0))
            wr = wt.RECT()
            u.GetWindowRect(ctypes.c_void_p(self.hwnd_mini), ctypes.byref(wr))
            aw = int(wr.right - wr.left) or int(d.get('w') or 0)
            ah = int(wr.bottom - wr.top) or int(d.get('h') or 0)
            x, y = self._dock_xy(aw, ah)
            # ② SWP_ASYNCWINDOWPOS：mini 归 pywebview GUI 线程所有，跨线程
            #   SetWindowPos 默认要等对方处理 WM_WINDOWPOSCHANGING，GUI 一卡
            #   回调就排队堆旧位置 —— 异步投递立即返回。
            # Z 序同步：拖动中或当前锚点/自己处于前台时，浮在非置顶层顶端（HWND_TOP，不设 SWP_NOZORDER），
            # 保证拖动遇到其他普通窗口时不被压在底下看不见；被第三方窗口主动激活遮挡时仍能正常被盖住。
            is_active = bool(getattr(self, '_drag_move', False)
                             or quick_switch.is_foreground_dialog(d.get('hwnd'))
                             or foreground_is(d.get('hwnd'))
                             or foreground_is(self.hwnd_mini))
            HWND_TOPMOST = ctypes.c_void_p(-1)
            HWND_NOTOPMOST = ctypes.c_void_p(-2)
            if is_active:
                u.SetWindowPos(ctypes.c_void_p(self.hwnd_mini), HWND_TOPMOST, x, y, 0, 0,
                               SWP_NOSIZE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
            else:
                u.SetWindowPos(ctypes.c_void_p(self.hwnd_mini), HWND_NOTOPMOST, x, y, 0, 0,
                               SWP_NOSIZE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
            _log('follow anchor: %s -> (%d,%d)' % (rect, x, y))
        except Exception as e:
            _log('follow_anchor FAILED: %r' % (e,))
        finally:
            self._follow_lock.release()
        if rescale_needed:
            # 锁外重算：就算 pywebview 调用卡住，也只是这一帧的缩放晚了，
            # 跟随本身（钩子/轮询/追逐）照常跑
            try:
                self._rescale_dock(d)
            except Exception as e:
                _log('rescale dispatch FAILED: %r' % (e,))

    def place_mini_near(self, hwnd):
        """小窗贴到触发窗口上：文件对话框=外部正下方居中；文件夹=右下角贴角。

        第 202 项改动（QA 报告 P0-A）：
        - `create_window(min_size=(MINI_W,96))` 会把 Form.MinimumSize 锁成
          1140x144 physical @1.5，420/440 的 resize 全被钳回 760 —— 已把
          min_size 降到 (MINI_W_MIN,88)。
        - 尺寸**只有 `mini.resize()` 一个写入者**，SetWindowPos 只定位（SWP_NOSIZE）；
          设置后读回真实尺寸，用它算 x，保证 `x + 实际宽 <= right - padx`。
        """
        try:
            u, wt = _win()
            if not u or not hwnd or not self.hwnd_mini:
                return
            r = wt.RECT()
            if not u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
                raise RuntimeError('GetWindowRect failed')
            left, top, right, bottom = int(r.left), int(r.top), int(r.right), int(r.bottom)
            dpr = window_dpr(self.hwnd_mini) or 1.0
            win_w_css = (right - left) / dpr
            win_h_css = (bottom - top) / dpr
            # 太小的窗口不贴角：回到桌面模式，顺手把尺寸还原成大窗，
            # 免得「页面是小窗样式、窗口还是上一次的尺寸」（QA BUG-02）。
            if win_w_css < MINI_W_MIN + 40 or win_h_css < 220:
                self._dock = None
                self._mini_mode = 'large'
                try:
                    self.mini.resize(MINI_W, MINI_H)
                except Exception:
                    pass
                self.place_mini()
                return
            padx = int(round(14 * dpr))
            try:
                is_dlg = bool(quick_switch.is_file_dialog(hwnd))
            except Exception:
                is_dlg = False
            pady = int(round((106 if is_dlg else 42) * dpr))
            gap = 0        # 第 205 项：零间隙，视觉上「长在对话框底边上」
            # 第 229 项（227③ 恢复）：对话框模式宽度 = 对话框宽 × 0.5（上限 720），
            # 水平居中、底边贴对话框下沿；资源管理器文件夹仍用 MINI_W_SMALL。
            if is_dlg:
                w_css = int(round(max(MINI_W_MIN, min(720, win_w_css * 0.5,
                                                      win_w_css - 28))))
            else:
                w_css = int(round(max(MINI_W_MIN, min(MINI_W_SMALL, win_w_css - 28))))
            avail_h = max(200.0, (bottom - top - 2 * pady) / dpr)
            maxh_css = int(round(min(MAXH_CAP,
                                     max(MAXH_MIN, avail_h * (0.72 if is_dlg else 0.50)))))
            maxh_px = int(round(maxh_css * dpr))
            work = self._work_rect()
            B = work[3] if work else bottom + maxh_px + 100
            fits_below = bool(bottom + maxh_px <= B - gap)
            self._mini_mode = 'dock'
            # 唯一尺寸入口：pywebview.resize 收逻辑像素，内部自己乘 dpr
            try:
                self.mini.resize(w_css, MINI_H_SMALL)
            except Exception:
                pass
            # 读回实际尺寸（MinimumSize / DWM 都可能改它），后面一律按实测值定位
            wr = wt.RECT()
            u.GetWindowRect(ctypes.c_void_p(self.hwnd_mini), ctypes.byref(wr))
            aw = int(wr.right - wr.left) or int(round(w_css * dpr))
            ah = int(wr.bottom - wr.top) or int(round(MINI_H_SMALL * dpr))
            self._dock = {'hwnd': hwnd, 'rect': (left, top, right, bottom),
                          'mode': 'dlg' if is_dlg else 'folder',
                          'wcss': w_css, 'w': aw, 'h': ah, 'dpr': dpr,
                          'maxh': maxh_css, 'padx': padx, 'pady': pady, 'gap': gap,
                          'fits_below': fits_below}
            x, y = self._dock_xy(aw, ah)
            SWP_NOSIZE, SWP_NOACTIVATE = 0x1, 0x10
            u.SetWindowPos(ctypes.c_void_p(self.hwnd_mini), ctypes.c_void_p(0), x, y, 0, 0,
                           SWP_NOSIZE | SWP_NOACTIVATE)
            try:
                wr2 = wt.RECT()
                u.GetWindowRect(ctypes.c_void_p(self.hwnd_mini), ctypes.byref(wr2))
                _log_always('dock rect %.0fx%.0f css (physical %dx%d dpr=%.2f '
                            'style=%s dlg=%s mode=%s x=%d y=%d)'
                            % ((wr2.right - wr2.left) / (dpr or 1.0),
                               (wr2.bottom - wr2.top) / (dpr or 1.0),
                               wr2.right - wr2.left, wr2.bottom - wr2.top, dpr,
                               App._qs_style(), is_dlg, self._dock['mode'],
                               int(wr2.left), int(wr2.top)))
            except Exception:
                pass
            _log('place_mini_near: (%d,%d) %dx%d dlg=%s for %s'
                 % (x, y, aw, ah, is_dlg, hwnd))
        except Exception as e:
            _log('place_mini_near FAILED: %r' % (e,))
            try:
                self._dock = None
                self._mini_mode = 'large'
                self.mini.resize(MINI_W, MINI_H)
                self.place_mini()
            except Exception:
                pass

    def _dock_move(self, h_css):
        """高度变了就按锚点重摆；对话框仍在外部下方居中，文件夹仍贴右下。"""
        d = self._dock
        if not d:
            return
        try:
            u, _wt = _win()
            if not u or not self.hwnd_mini:
                return
            hpx = int(round(int(h_css) * (d.get('dpr') or 1.0)))
            d['h'] = hpx
            x, y = self._dock_xy(int(d.get('w') or 0), hpx)
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
            is_active = bool(getattr(self, '_drag_move', False)
                             or quick_switch.is_foreground_dialog(d.get('hwnd'))
                             or foreground_is(d.get('hwnd'))
                             or foreground_is(self.hwnd_mini))
            HWND_TOPMOST = ctypes.c_void_p(-1)
            HWND_NOTOPMOST = ctypes.c_void_p(-2)
            if is_active:
                u.SetWindowPos(ctypes.c_void_p(self.hwnd_mini), HWND_TOPMOST, x, y, 0, 0,
                               SWP_NOSIZE | SWP_NOACTIVATE)
            else:
                u.SetWindowPos(ctypes.c_void_p(self.hwnd_mini), HWND_NOTOPMOST, x, y, 0, 0,
                               SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception as e:
            _log('dock_move FAILED: %r' % (e,))

    def start_tray(self):
        try:
            import tray
        except Exception:
            return
        try:
            self.tray = tray.Tray(
                # 「打开详细搜索」带上快搜窗最后在搜的词，接着刚才的继续查
                on_main=lambda: self.show_main(brain.LAST_Q or None),
                # 第 212~215 项：托盘点击带 tray=True —— 屏幕正中弹出，
                # 不贴鼠标、不占右下角、不压任务栏。
                on_mini=lambda: self.show_mini(tray=True),
                on_quit=self.quit,
                on_settings=self.tray_settings,
                on_about=self.tray_about,
                on_feedback=self.tray_feedback,
                on_help=self.tray_help,
                tip='Memex（双击 Ctrl 唤出快搜）',
            )
            if not self.tray.start():
                self.tray = None
        except Exception:
            self.tray = None
        try:
            _start_auto_update(self)
        except Exception as e:
            _log('start auto-update failed: %r' % (e,))
        try:
            import clipboard_mgr
            clipboard_mgr.start_listener()
        except Exception as e:
            _log('start clipboard listener failed: %r' % (e,))

    def _dbg_autohelp(self):
        """临时诊断：SB_AUTOHELP=1 时 8 秒后自己点一次「使用帮助」，把 traceback 写进日志。"""
        import os
        if os.environ.get('SB_AUTOHELP') != '1':
            return

        def run():
            import traceback
            time.sleep(8.0)
            _log('AUTOHELP: firing tray_help')
            try:
                self.tray_help()
                _log('AUTOHELP: returned ok')
            except Exception:
                _log('AUTOHELP EXC: ' + traceback.format_exc().replace(chr(10), ' | '))
        threading.Thread(target=run, daemon=True).start()

    def open_panel(self, name, focus=None):
        """托盘里点「设置 / 关于 / 使用帮助 / 反馈」：把主窗口叫出来并摊开对应面板。

        以前的做法是 show_main() 之后 evaluate_js 注入——那条路有两个坑：
        页面没加载完注入必然失败；加载中调 evaluate_js 还会把线程卡住。
        现在面板走 URL 参数，页面自己在启动时摊开，稳。

        第 267 项：托盘每点一次就是一条独立线程，各自 show_main() + 导航。
        用户连点三下（设置→关于→反馈）会变成三条线程抢同一个窗口，
        谁最后醒来谁把窗口按上去 —— 表现为「设置点了没反应，接着关掉详情栏
        又自己弹回来」。这里改成**单飞 worker + 最新请求胜出**：
        只留一条工作线程，后来的请求覆盖前一条，跑完再看一眼有没有新请求。
        """
        with self._panel_lock:
            self._panel_req = (name, focus)
            already = self._panel_worker_on
            self._panel_worker_on = True
        if already:
            return
        threading.Thread(target=self._panel_worker, daemon=True,
                         name='tray-panel').start()

    def _panel_worker(self):
        while True:
            with self._panel_lock:
                req = self._panel_req
                self._panel_req = None
                if req is None:
                    self._panel_worker_on = False
                    return
            name, focus = req
            try:
                self.show_main(panel=name, focus=focus, lock_timeout=3.0)
            except Exception:
                _log_always('panel_worker show_main FAILED\n' + traceback.format_exc())

    # _main_js() 已删：托盘三个面板早在 v0.9.46 就全改成 open_panel() 走 URL 参数，
    # 这个「show_main() + evaluate_js 注入」的应急口子没有任何调用者，而且无参
    # show_main() 会把当前搜索词清掉 —— 留着只会等哪天有人误用。

    def tray_settings(self):
        # 设置抽屉就是主窗口里那个，别在托盘里另开一个——两处状态会打架。
        # 注意面板名要跟 React 那边认的一致（settings），写 'cfg' 是旧模板的参数名，
        # 新界面不认，点了就「什么都没发生」。
        self.open_panel('settings')

    def tray_help(self):
        # 快捷键太多，塞不进菜单，统一放进主窗口里的帮助面板
        self.open_panel('help')

    def tray_about(self):
        self.open_panel('about')

    def tray_balloon(self, text, title='Memex'):
        """托盘气泡：不依赖 WebView，界面白屏时也能给出反馈。"""
        try:
            import win32con
            import win32gui
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_MODIFY,
                (self.tray._hwnd, 0, win32gui.NIF_INFO, 0, None, '',
                 title, str(text)[:220], win32con.NIIF_INFO, 6000))
        except Exception:
            pass

    def tray_feedback(self):
        """托盘「反馈 / 报错」（第 162 项）。

        这是界面白屏、点不动时唯一还能点的入口：先在本机落一份 UserFeedback 报告
        （环境信息 + debug.log 尾部），再尽力打开设置页让用户补一句描述。
        两条路都失败时至少气泡告诉用户「已记录」。
        """
        rid = ''
        try:
            import error_report
            rid = error_report.save_feedback('用户从托盘「反馈 / 报错」发起（未填写描述）', True) or ''
        except Exception as e:
            _log(f'tray_feedback save failed: {e!r}')
        opened = False
        try:
            # 第 181 项：反馈/报错已从设置里搬进独立弹窗，传 panel 反而会
            # 先把设置摊开再叠一层，所以只传 focus
            self.open_panel(None, 'feedback')
            opened = True
        except Exception as e:
            _log(f'tray_feedback open_panel failed: {e!r}')
        if rid and opened:
            self.tray_balloon('已记录一份反馈报告，可在设置里补充一句描述')
        elif rid:
            self.tray_balloon('已记录一份反馈报告（界面暂时打不开也没关系）')
        else:
            self.tray_balloon('记录反馈失败，请稍后再试')

    def quit(self):
        """退出：每一步都计时（第 163 项）。

        实测用户右键退出要等 1~2 秒。这里先量三段：取消热键、删托盘图标、关数据库。
        关库放到后台并最多等 300ms —— SQLite WAL 本身崩溃安全，没必要为了 checkpoint
        让用户盯着图标等；超时就直接退出，日志里会留下 done=False 供排查。
        """
        self._alive = False
        # 第 277 项：正常退出要把运行标记写成 clean，否则下次启动会误判成崩溃。
        # 顺带把指标与面包屑落盘 —— 托盘退出后进程就没了，内存里的计数不写就丢。
        try:
            error_report.mark_clean()
        except Exception:
            pass
        try:
            import feature_metrics
            feature_metrics.flush(True)
        except Exception:
            pass
        try:
            import breadcrumb
            breadcrumb.persist({'exit': 'clean'})
        except Exception:
            pass
        try:
            self._remember_size()
        except Exception:
            pass
        try:
            if self.typewatch:
                self.typewatch.stop()
        except Exception:
            pass
        try:
            if getattr(self, 'clickwatch', None):
                self.clickwatch.stop()
        except Exception:
            pass
        # 第 229 项：摘掉位置钩子并结束它的消息循环（daemon 线程，进程退也会回收）
        try:
            import ctypes as _ct
            _u = _ct.windll.user32
            if getattr(self, '_anchor_hook', 0):
                _u.UnhookWinEvent(_ct.c_void_p(self._anchor_hook))
            if getattr(self, '_anchor_hook2', 0):
                _u.UnhookWinEvent(_ct.c_void_p(self._anchor_hook2))
            if getattr(self, '_anchor_tid', 0):
                _u.PostThreadMessageW(self._anchor_tid, 0x0012, 0, 0)   # WM_QUIT
        except Exception:
            pass
        t0 = time.perf_counter()
        marks = []

        def mark(name):
            marks.append('%s=%.0fms' % (name, (time.perf_counter() - t0) * 1000))

        try:
            if self.hotkey:
                self.hotkey.stop()
            mark('hotkey')
            if getattr(self, 'floating_ball_mgr', None):
                self.floating_ball_mgr.stop()
            mark('floating_ball')
            if self.tray:
                self.tray.quit()
            mark('tray')
        except Exception as e:
            marks.append('tray/hotkey err=%r' % (e,))

        done = threading.Event()

        def _close_db():
            try:
                brain.conn().close()
            except Exception:
                pass
            done.set()

        try:
            threading.Thread(target=_close_db, name='quit-close-db',
                             daemon=True).start()
            closed = done.wait(0.3)
        except Exception:
            closed = False
        mark('db')
        try:
            _log_always('quit timing: %s db_done=%s' % (' '.join(marks), closed))
        except Exception:
            pass
        os._exit(0)


# ------------------------------------------------------------------ 入口
def _start_prune_after_boot():
    """启动 5 秒后跑一次 prune：把搜索结果里那些已不存在的路径（C 盘旧位置等）
    从索引里彻底清掉。竞品 Listary/Everything 启动时都会做这事——结果必须可点。

    失败也不影响主程序：只是一次后台清理，没成下次还会再做。"""
    def _do():
        try:
            time.sleep(5)
            # 不套 con_lock：prune 自己开连接、自己提交，套上这把锁只会让
            # 启动后头几秒的搜索全排队（实测冻结 3~5 秒）
            gone = brain.prune()
            if gone:
                _log(f'boot prune: removed {gone} stale paths')
        except Exception as e:
            _log(f'boot prune FAILED: {e}')
    threading.Thread(target=_do, daemon=True).start()


_MUTEX_NAME = 'Local\\SecondBrain.Desktop.SingleInstance.v2'
# .v2 后缀：第 121/122 项排查期间，一个 GIL 死锁的测试进程持着旧名的互斥锁不放，
# 导致零实例下 _single_instance 也误报「已在运行」。换名绕开；旧名随幽灵进程退出自然消失。
_MUTEX_H = None


def _activate_running():
    """把已经在跑的那份叫到前面来。

    优先用命名事件通知第一个实例**自己**跑 show_main()——只有它自己的线程里
    show() 才能让 WebView2 正确同步可见性。跨进程 ShowWindow 会被渲染层重新藏回，
    反而留下白屏。事件底层走 ctypes 调 kernel32（不依赖 win32event，打包 exe 里
    也能用）；事件机制不可用时才退回裸 Win32 兜底。

    命名事件可能在第二个实例启动那一刻还没被第一个实例建好（after_start 还没跑完），
    所以 OpenEventW+SetEvent 重试几次——否则会退化成无效的 ShowWindow 兜底。自动
    重置事件即使「信号发出时还没人 Wait」，等监听器一 Wait 也会立刻返回，不会丢。"""
    k = _kernel32_event()
    if k is not None:
        EVENT_ALL_ACCESS = 0x1F0003
        for _ in range(6):
            try:
                h = k.OpenEventW(EVENT_ALL_ACCESS, 0, 'SecondBrain_Activate_v1')
                if h:
                    k.SetEvent(h)
                    k.CloseHandle(h)
                    _log('activate running: 已 SetEvent 通知第一个实例自显示')
                    return True
            except Exception as e:
                _log('activate running: 命名事件通知失败：%r' % (e,))
            time.sleep(0.4)           # 第一个实例的监听器可能还没建好事件
    # 兜底：命名事件不可用（极旧环境 / 第一个实例还没起好监听器）时，跨进程直接显示，
    # 并唤醒被 silent hide 掉的 WebView2 子窗口。
    try:
        import ctypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = ctypes.c_void_p     # 64 位下别把 HWND 截成 int
        for title in ('Memex', 'Memex（双击 Ctrl 唤出快搜）'):
            hwnd = u.FindWindowW(None, title)
            if hwnd:
                u.ShowWindow(ctypes.c_void_p(hwnd), 9)   # SW_RESTORE
                revive_webview(hwnd)
                u.SetForegroundWindow(ctypes.c_void_p(hwnd))
                return True
    except Exception as e:
        _log(f'activate existing FAILED: {e}')
    return False


def _single_instance():
    """True = 第一个实例，继续；False = 已经有一个在跑（已尝试叫到前面）。

    之前只靠端口判断，第二个实例可能给页面注入自己的 js_api，结果「搜得到点不动」。
    SB_ALLOW_MULTI=1 可以绕过（调试/测试用）。"""
    if os.environ.get('SB_ALLOW_MULTI') == '1':
        return True
    global _MUTEX_H
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k = ctypes.WinDLL('kernel32', use_last_error=True)   # 让 ctypes 保存该次调用的
        h = k.CreateMutexW(None, False, _MUTEX_NAME)          # LastError，不被中间调用污染
        if not h:
            return True
        if ctypes.get_last_error() == 183:    # ERROR_ALREADY_EXISTS
            _log('single instance: another copy is running')
            _activate_running()
            return False
        _MUTEX_H = h                           # 保持句柄，进程退出自动释放
        return True
    except Exception as e:
        _log(f'single instance FAILED: {e}')
        return True


def _clean_webview_cache(profile):
    """启动时清掉 WebView2 的缓存目录（体积评估第 3 节 #5 / 5′）。

    实测 `%LOCALAPPDATA%\\Memex\\App\\webview-profile` 72.7 MB，构成：
    - 纯缓存（Cache / Code Cache / ShaderCache…）：≈9 MB，删了只多一次本地静态文件重读；
    - WebView2 自己下载的组件（component_crx_cache 21.4 / WidevineCdm 21.9 /
      Subresource Filter 12.3）：≈55 MB。界面是本地 127.0.0.1 的 HTML，不用 DRM、
      不需要广告过滤规则，所以这些也能删 —— 前提是启动参数里带了
      `--disable-component-update`（见 _component_update_off），否则它下次又下载回来。

    只删这些「可再生」目录，**绝不碰** Cookies / Local Storage / Network /
    Preferences —— 删了等于用户每次重启都掉登录态和界面语言。
    目录可能正被 WebView2 子进程占用（先跑 _cleanup_orphan_webview 再调这里），
    删不掉的单文件忽略即可，不能因此让启动失败。
    """
    import shutil

    base = Path(profile) / 'EBWebView'
    if not base.is_dir():
        return 0
    # WebView2（Chromium）缓存目录名：老版本在 EBWebView/Default 下，
    # 着色器缓存在 EBWebView 根，两处都扫一遍。
    names = ('Cache', 'Code Cache', 'GPUCache', 'GPUPersistentCache',
             'GrShaderCache', 'ShaderCache', 'DawnCache', 'DawnGraphiteCache',
             'DawnWebGPUCache', 'CacheStorage', 'ScriptCache')
    # 5′：WebView2 自下载的组件。带空格的名字原样匹配，别写成前缀/通配。
    comps = ('component_crx_cache', 'extensions_crx_cache', 'WidevineCdm',
             'Subresource Filter', 'hyphen-data')
    freed = 0
    for root in (base, base / 'Default'):
        if not root.is_dir():
            continue
        for name in names + comps:
            p = root / name
            if not p.exists():
                continue
            try:
                if p.is_dir():
                    for dirpath, _dirs, files in os.walk(p):
                        for f in files:
                            try:
                                freed += os.path.getsize(os.path.join(dirpath, f))
                            except OSError:
                                pass
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    try:
                        freed += p.stat().st_size
                    except OSError:
                        pass
                    p.unlink(missing_ok=True)
            except Exception:
                continue
    return freed


def _component_update_off():
    """5′：别让 WebView2 自己下载组件（体积评估 #5 的第二段）。

    `component_crx_cache` 21.4 MB / `WidevineCdm` 21.9 MB / `Subresource Filter` 12.3 MB
    都是 WebView2 的组件更新器悄悄下载的。界面是本地 127.0.0.1 的 HTML，不用 DRM、
    不要广告过滤规则，所以关掉更新器 + 启动时清一次，这 ≈55 MB 就不会再长回来。

    走 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 环境变量（本项目的 compat 模式一直用它
    追加 --disable-gpu，说明这条通道可靠）；必须在 webview.start() 之前设置。
    """
    try:
        cur = os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS') or ''
        if '--disable-component-update' not in cur:
            os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = (
                cur + ' --disable-component-update').strip()
    except Exception:
        pass


def _cleanup_orphan_webview():
    """结束「父进程已死」的 msedgewebview2（第 125 项）。

    强杀 SecondBrain.exe 后它的 WebView2 子进程孤儿化，锁着 webview-profile
    数据目录——下一次启动的 WebView2 环境创建报 0x700AA（资源在使用中）→ 白屏。
    Toolhelp32 快照枚举进程：msedgewebview2 且父进程 PID 不在快照中（父已死）
    的就结束掉。系统组件（SearchHost 等）的 webview 父进程活着，不受影响。
    快照里父 PID 存在但实际是不同名进程的场景极少（PID 复用窗口期），代价只是
    少清一个孤儿，下一次启动还会再清。"""
    if os.name != 'nt':
        return
    try:
        TH32CS_SNAPPROCESS = 0x2
        k32 = ctypes.windll.kernel32
        class _PE(ctypes.Structure):
            _fields_ = [('dwSize', ctypes.c_uint32), ('cntUsage', ctypes.c_uint32),
                        ('th32ProcessID', ctypes.c_uint32),
                        ('th32DefaultHeapID', ctypes.c_size_t),
                        ('th32ModuleID', ctypes.c_uint32), ('cntThreads', ctypes.c_uint32),
                        ('th32ParentProcessID', ctypes.c_uint32),
                        ('pcPriClassBase', ctypes.c_long), ('dwFlags', ctypes.c_uint32),
                        ('szExeFile', ctypes.c_wchar * 260)]
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == ctypes.c_void_p(-1).value or not snap:
            return
        pe = _PE()
        pe.dwSize = ctypes.sizeof(_PE)
        procs = {}
        if k32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                procs[pe.th32ProcessID] = (pe.szExeFile, pe.th32ParentProcessID)
                if not k32.Process32NextW(snap, ctypes.byref(pe)):
                    break
        k32.CloseHandle(ctypes.c_void_p(snap))
        killed = 0
        for pid, (name, ppid) in procs.items():
            if name.lower() != 'msedgewebview2.exe':
                continue
            if ppid in procs:                      # 父进程活着 → 不是孤儿
                continue
            h = k32.OpenProcess(0x0001, False, pid)   # PROCESS_TERMINATE
            if h:
                k32.TerminateProcess(ctypes.c_void_p(h), 1)
                k32.CloseHandle(ctypes.c_void_p(h))
                killed += 1
        if killed:
            _log_always('orphan webview 清理：结束 %d 个父进程已死的 msedgewebview2' % killed)
    except Exception as e:
        _log_always('orphan webview 清理失败：%r' % (e,))


def main(query=None, show_settings=False, lang=None, silent=False, start_mini=False):
    """开窗口。

    query          非空时主窗口带 ?q=<关键词> 打开，直接出结果
    show_settings  为真时带 ?cfg=1 打开，直接展开设置面板
    lang           'zh' / 'en' 时强制界面语言（否则跟浏览器/navigator.language）
    silent         后台常驻：不显示任何窗口，只等双击 Ctrl
    start_mini     启动后直接弹出快搜窗

    这几个参数让 `SecondBrain.exe 关键词`、`SecondBrain.exe --settings`、
    `SecondBrain.exe --silent` 能从快捷方式 / 开机自启直达，省掉双击后再操作。
    """
    if not _single_instance():
        print('Memex已经在运行了，已把窗口叫到前面。')
        return

    # 第 277 项：只有真正的 UI 进程才做「上次是否异常退出」检查与本次运行标记。
    # 测试 / CLI 导入 brain 时不会走到这里，所以不会制造假报告。
    try:
        # 第 277 项：要先注入数据目录再 arm —— 装机版实测「run_state.json 从没落盘」，
        # 因为 brain 的 bootstrap 要到更后面才 configure，此时 _CTX['dir'] 还是 None，
        # arm_run_guard / mark_stage 全是空操作，异常退出检查等于没装。
        if not (getattr(error_report, '_CTX', {}) or {}).get('dir'):
            error_report.configure(brain.app_dir(), getattr(brain, 'APP_VERSION', ''))
        rid = error_report.arm_run_guard()
        error_report.mark_stage('ui')
        if rid:
            _log_always('prev run abnormal report: %s' % rid)
    except Exception as e:
        _log(f'run guard failed: {e!r}')
    try:
        import feature_metrics
        feature_metrics.configure(brain.app_dir(), autostart=True)
    except Exception as e:
        _log(f'metrics configure failed: {e!r}')
    try:
        import breadcrumb
        breadcrumb.configure(brain.app_dir(), keep=True)
        breadcrumb.add('app', ev='start', silent=bool(silent))
    except Exception:
        pass

    port = _pick_port()
    if not port:
        print(f'端口 {BASE_PORT}~{BASE_PORT + PORT_TRIES - 1} 都被占用，无法启动。')
        print('请关掉占用端口的程序后重试。')
        return
    # 环境快照先落日志：万一后面窗口起不来，这条就是唯一能看的东西
    _env_info = _env_boot_probe()
    _apply_render_fallback(_env_info)
    url = f'http://127.0.0.1:{port}'
    srv = _start_server(port)
    if srv is None and _START_ERR[0]:
        tail = _START_ERR[0].strip().splitlines()[-1]
        _fatal('Memex启动失败',
               ('本地服务没能起来，所以窗口里只会显示「127.0.0.1 未发送任何数据」。'
                + chr(10) + chr(10) + '错误：' + tail
                + chr(10) + chr(10) + '常见原因：程序目录写不进去（只读位置 / 安全软件锁定 / 解压不完整）。'
                + '把整个文件夹挪到桌面或文档下面再试一次。'))
        return
    # 第 299 项：按 settings.json 的 proxy_mode 同步环境变量（默认跟随系统代理）。
    # 只影响外网请求；本机 127.0.0.1 的接口由各自的直连 opener 负责，不走这里。
    try:
        import netproxy
        netproxy.refresh()
    except Exception as e:
        _log(f'netproxy refresh failed: {e!r}')
    brain.start_pinyin_backfill()   # 老库补拼音（11 万条十几秒），后台跑，不挡窗口
    _start_prune_after_boot()       # 启动后台清理失效路径，跟 Listary/Everything 启动重扫一个思路

    params = []
    if query:
        params.append('q=' + quote(query))
    if show_settings:
        params.append('cfg=1')
    if lang in ('zh', 'en'):
        params.append('lang=' + lang)
    target = url + ('/?' + '&'.join(params) if params else '/')

    if webview is None:
        webbrowser.open(target)
        print(f'未安装 pywebview，已用浏览器打开：{target}')
        # 浏览器模式下必须让主线程别退出，否则服务会被回收
        threading.Event().wait()
        return

    # 给这个进程一个自己的 AppUserModelID：不然源码版跑起来，任务栏把它归到
    # python.exe 名下，显示的是 Python 的图标（用户反馈过「图标太丑」就是那张）。
    # 必须在建窗之前设，窗口一创建 Windows 就把任务栏按钮绑到这个 ID 上。
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'SecondBrain.Desktop')
    except Exception as e:
        _log(f'AppUserModelID FAILED: {e}')
    app = App(url, main_url=target, silent=silent, start_mini=start_mini)
    try:
        app.create()
        # pywebview 的 start() 默认 private_mode=True —— cookies 与 localStorage
        # 一律不落盘。症状就是「每次重启都弹欢迎页」，而且语言、深色模式、
        # 排序、列表/缩略图这些偏好其实每次都在重置，只是没欢迎页那么显眼。
        # 关掉私有模式，并把 WebView2 的 profile 固定到数据目录：app_dir() 已处理
        # 「程序目录只读 → 退到 %LOCALAPPDATA%\SecondBrain\data」的回退。
        # 窗口 URL 都是 http://127.0.0.1，不是本地文件，所以不会额外起 pywebview
        # 自带的 global server。
        try:
            import brain as _b
            _profile = _b.app_dir() / 'webview-profile'
            _profile.mkdir(parents=True, exist_ok=True)
            _storage = str(_profile)
        except Exception:
            _storage = None
        _cleanup_orphan_webview()          # 先清上一实例强杀留下的孤儿 webview
        _component_update_off()             # 5′：别再让 WebView2 自己下载组件（≈55 MB）
        # 体积评估 #5/5′：缓存与自下载组件每次启动清一遍（实测 72.7 MB 里 ≈64 MB 可再生）
        try:
            _freed = _clean_webview_cache(_profile)
            if _freed > 0:
                # 用 _log_always：这是「磁盘为什么小了」的唯一凭据，别随调试开关消失
                _log_always('webview cache cleared: %.1f MB' % (_freed / 1048576.0))
        except Exception as e:
            _log_always('webview cache clean skipped: %r' % (e,))
        webview.start(func=app.after_start, debug=False,
                      private_mode=False, storage_path=_storage)
    except Exception as e:
        # pythonnet（pywebview 的 WinForms 后端）在两种环境下会加载失败：
        #   1) 程序放在带 Low 完整性标签的目录里（例如 D:\work\项目\…）；
        #   2) 机器缺 .NET / WebView2 运行时。
        # 此时窗口开不出来，但本地服务是好的 —— 退到浏览器模式，至少能继续用。
        _log(f'webview.start FAILED: {type(e).__name__}: {e}')
        try:
            webbrowser.open(target)
        except Exception:
            pass
        _fatal('Memex：窗口启动失败，已改用浏览器打开',
               ('窗口组件（WebView2 / .NET）没能加载，界面已经在默认浏览器里打开：'
                + chr(10) + target
                + chr(10) + chr(10) + '错误：' + f'{type(e).__name__}: {e}'
                + chr(10) + chr(10)
                + '两条常见原因：'
                + chr(10) + '1) 程序所在目录被标成了「低完整性」（沙箱/网盘同步目录常见）——'
                + '把整个文件夹复制到 D:\\ 下再双击；'
                + chr(10) + '2) 缺 WebView2 运行时 —— https://developer.microsoft.com/microsoft-edge/webview2/'
                + chr(10) + chr(10) + '这个窗口点确定后，程序会继续在托盘里运行。'))
        threading.Event().wait()


def _parse_window_args(argv):
    """从 argv 里抽出窗口启动用的标志：--settings、--lang xx / --lang=xx、
    --silent、--mini。其余非标志 token 原样留在 rest 里，由调用方决定是子命令还是搜索词。

    返回 (rest, show_settings, lang, silent, start_mini)。"""
    show_settings = False
    lang = None
    silent = False
    start_mini = False
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--settings', '-s', '/settings'):
            show_settings = True
        elif a in ('--silent', '--tray', '--background'):
            silent = True
        elif a in ('--mini', '--quick'):
            start_mini = True
        elif a == '--lang':
            if i + 1 < len(argv):
                lang = argv[i + 1]
                i += 1
        elif a.startswith('--lang='):
            lang = a.split('=', 1)[1]
        else:
            rest.append(a)
        i += 1
    lang = lang if lang in ('zh', 'en') else None
    return rest, show_settings, lang, silent, start_mini


def _attach_console():
    """打包后的 exe 是窗口程序（console=False）。从 cmd 里调用 index/update 这类子命令时，
    输出无处可去，进度一条都看不到。这里把标准输出接回父控制台；
    双击启动（没有父控制台）时 AttachConsole 失败，静默跳过即可。"""
    if getattr(sys, 'frozen', False) is False or sys.platform != 'win32':
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return
        # 已经有 stdout（重定向到文件/管道）就别抢，只在真的没有时接回控制台
        for name in ('stdout', 'stderr'):
            if getattr(sys, name, None) is None:
                try:
                    setattr(sys, name,
                            open('CONOUT$', 'w', encoding='utf-8', errors='replace',
                                 buffering=1))
                except Exception:
                    pass
    except Exception:
        pass


def _apply_render_fallback(info):
    """按环境自动关掉 WebView2 的硬件加速（而不是让用户自己去查参数）。

    两种环境没有可靠的 GPU 合成：远程桌面会话、只装了「Microsoft Basic Display
    Adapter」的机器（虚拟机、没装显卡驱动）。WebView2 在这两种环境里的典型表现是
    黑块、白块、整片不刷新 —— 用户描述通常就是「窗口是黑的」。

    关掉硬件加速只是慢一点（软渲染），但至少能用。设置必须在 webview.start()
    之前生效，所以放在启动流程里、窗口创建之前。
    """
    try:
        import env_check
        p = env_check.plan(info or {})
        if not p.get('disable_gpu'):
            return False
        cur = os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS') or ''
        if '--disable-gpu' in cur:
            return False
        os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = (cur + ' --disable-gpu').strip()
        _log_always('渲染降级：远程桌面/无硬件加速环境，已关闭 WebView2 GPU')
        return True
    except Exception as e:
        _log('render fallback FAILED: %r' % (e,))
        return False


def _env_boot_probe():
    """启动时做一次轻量环境探测，结论写进日志。

    它**不修任何东西**（该修的各模块自己已经做了），只负责在日志里留下证据：
    用户报「打不开」时，第一行就能看出到底是「没装 WebView2」还是「磁盘满了」，
    不用来回猜着问。light=True 跳过 quick_check，耗时可忽略。
    """
    try:
        import env_check
        info = env_check.probe_all(light=True)
        prim = info.get('primary') or {}
        _log_always('[env-boot] webview2=%s win=%s screen=%dx%d@%d%% monitors=%d '
                    'data=%s(%s) free=%dMB db=%s(%.1fMB)%s'
                    % (info.get('webview2') or 'MISSING', info.get('windows'),
                       prim.get('phys_w', 0), prim.get('phys_h', 0),
                       prim.get('scale', 0), len(info.get('screens') or []),
                       info.get('data_dir'),
                       'rw' if info.get('data_writable') else 'RO',
                       info.get('free_mb', -1), info.get('db_state'),
                       info.get('db_mb', 0),
                       ' remote' if info.get('remote_session') else ''))
        for it in env_check.judge(info):
            if it['level'] == 'FAIL':
                _log_always('[env-boot:FAIL] %s | %s | %s'
                            % (it['title'], it.get('detail', ''), it.get('fix', '')))
            elif it['level'] == 'WARN':
                _log_always('[env-boot:WARN] %s | %s'
                            % (it['title'], it.get('detail', '')))
        return info
    except Exception:
        _log_always('[env-boot] 自检失败：%s' % traceback.format_exc()[-400:])
        return {}


def _run_window(query, show_settings, lang, silent=False, start_mini=False):
    try:
        main(query=query, show_settings=show_settings, lang=lang,
             silent=silent, start_mini=start_mini)
    except Exception as e:
        import traceback
        traceback.print_exc()
        # 窗口没起来时先判断「是不是根本没有 WebView2」：这是外行用户最可能遇到的
        # 一种完全空白，原来那句「通常是缺少 WebView2」只是让用户自己去找，
        # 现在直接把下载页递到他手上（点确定就打开浏览器）。
        wv = ''
        try:
            import env_check
            wv = env_check.webview2_version()
        except Exception:
            pass
        if not wv:
            try:
                r = ctypes.windll.user32.MessageBoxW(
                    None,
                    ('Memex需要「WebView2 运行时」，这台电脑上没找到。'
                     + chr(10) * 2
                     + '点「确定」会打开微软官方下载页，装好后重新打开Memex即可。'
                     + chr(10)
                     + '（安装包很小，约 2 MB；装一次，以后都不用再装。）'),
                    'Memex · 缺少 WebView2 运行时', 0x01)
                if r == 1:
                    os.startfile('https://go.microsoft.com/fwlink/p/?LinkId=2124703')
                    return
            except Exception:
                pass
            return
        _die(f'窗口启动失败：{type(e).__name__}: {e}\n\n'
             f'Window failed to start.\n'
             f'WebView2 已安装（{wv}），问题可能出在显卡驱动或安全软件。\n'
             f'可以试试：1) 更新显卡驱动  2) 用 --diagnose 生成环境报告\n'
             f'{chr(10)}日志：{_log_path()}')


def _diagnose():
    """`--diagnose`：跑一遍环境自检，把报告写下来并打开给人看。

    exe 是窗口程序（console=False），控制台输出用户根本看不见 —— 所以报告必须落成
    文件、再把文件打开。用户要做的只有一件事：把这份报告发过来。
    这份报告里有分辨率、缩放、显示器数、WebView2 版本、数据目录、索引库状态，
    等于把「现场」搬到开发机上，省掉来回问「你是什么系统、多少分辨率」。
    """
    _attach_console()
    try:
        import env_check
    except Exception as e:
        _die('环境自检模块缺失：%r' % (e,))
        return
    try:
        info = env_check.probe_all()
        issues = env_check.judge(info)
        text = env_check.render(info, issues)
    except Exception:
        import traceback
        _fatal('环境自检失败', traceback.format_exc()[-1200:])
        return
    print(text)
    out = None
    try:
        import brain as _b
        d = Path(_b.app_dir())
        d.mkdir(parents=True, exist_ok=True)
        out = d / 'env_report.txt'
        out.write_text(text, encoding='utf-8')
        (d / 'env_report.json').write_text(
            json.dumps({'info': info, 'issues': issues, 'plan': env_check.plan(info)},
                       ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        out = None
    if getattr(sys, 'frozen', False):
        fails = [i for i in issues if i['level'] == 'FAIL']
        warns = [i for i in issues if i['level'] == 'WARN']
        lines = ['%s %s' % ('×' if i['level'] == 'FAIL' else '!', i['title'])
                 for i in (fails + warns)[:6]]
        head = ('发现 %d 项不可用、%d 项要注意' % (len(fails), len(warns))
                if (fails or warns) else '本机环境正常，没发现问题')
        body = head + ((chr(10) * 2 + chr(10).join(lines)) if lines else '')
        if out:
            body += chr(10) * 2 + '完整报告：' + str(out)
        try:
            ctypes.windll.user32.MessageBoxW(None, body, 'Memex · 环境自检', 0x40)
        except Exception:
            pass
        if out:
            try:
                os.startfile(str(out))
            except Exception:
                pass
    return out


def _feedback_cli(argv):
    """无界面反馈（第 164 项）：`SecondBrain.exe --feedback "文字"`。

    界面白屏、WebView 起不来时，设置页里的入口全不可达；这条只依赖 Python 层，
    双击安装目录里的 `报告问题.bat` 就能把描述 + 环境信息 + debug.log 尾部发出去。
    """
    _attach_console()
    text = ''
    if '--feedback' in argv:
        i = argv.index('--feedback')
        rest = [a for a in argv[i + 1:] if not a.startswith('--')]
        text = ' '.join(rest).strip()
    if not text:
        try:
            print('Memex · 报告问题')
            print('  界面打不开 / 白屏 / 点了没反应时用这里。')
            print('  描述一句话（可留空直接回车，会附环境信息与最近日志）：')
            text = input('> ').strip()
        except Exception:
            text = ''
    text = text or '界面异常（用户从命令行发起，未填写描述）'
    try:
        import error_report
        if not (getattr(error_report, '_CTX', {}) or {}).get('dir'):
            error_report.configure(brain.app_dir(), getattr(brain, 'VERSION', ''))
        rid = error_report.save_feedback(text, True)
        if not rid:
            print('[x] 报告没能保存，请检查数据目录是否可写')
            return 2
        ok, err = error_report.send_one(rid)
        if ok:
            print('[√] 已发送，感谢反馈')
            return 0
        print('[!] 已保存在本机：%s' % rid)
        print('    暂时没发出去（%s），下次正常启动会自动补发。' % (err or '未配置接收端'))
        return 0
    except Exception as e:
        print('[x] 反馈失败：%s: %s' % (type(e).__name__, e))
        return 3


def _start_auto_update(app):
    """后台自动更新（第 164 项）：启动 45s 后查一次，之后每 6 小时一次。

    作者推一个 Release 即可，客户端自己发现并下载；用户只需在设置里点重启更新。
    """
    def work():
        # 用户要求：启动后先查一次，再进入 6 小时间隔；不要等 45 秒
        time.sleep(8)
        while True:
            try:
                info = brain.check_update()
                if info.get('ok') and info.get('newer'):
                    ver = str(info.get('latest') or info.get('version') or '')
                    _log_always('auto-update: 发现 %s，开始后台下载' % ver)
                    r = brain.download_update(info)
                    if r.get('ok'):
                        _metric('update', 'ok', 'download')
                        _log_always('auto-update: %s 已下载，等用户重启' % ver)
                        try:
                            app.tray_balloon('新版本 %s 已下载，设置里点「重启更新」生效' % ver)
                        except Exception:
                            pass
                    else:
                        # 第 277 项：更新下载失败以前只有一行 debug.log，
                        # 用户只会说「一直更不了」，看不出是 404 还是被墙。
                        _metric('update', 'fail', 'download', r.get('k') or r)
                        _log_always('auto-update: 下载失败 %r' % (r.get('k') or r,))
                elif info.get('ok'):
                    _metric('update', 'ok', 'check')
                else:
                    _metric('update', 'fail', 'check', info.get('err') or 'no-info')
            except Exception as e:
                _metric('update', 'fail', 'check', e)
                _log('auto-update failed: %r' % (e,))
            time.sleep(6 * 3600)
    threading.Thread(target=work, name='auto-update', daemon=True).start()


def _uninstall_cli(quiet=False):
    """`Memex.exe --uninstall`：确认后用外部脚本删程序（数据默认保留）。"""
    import ctypes
    try:
        import uninstall
    except Exception as e:
        print('[x] 卸载模块缺失：%r' % (e,))
        return 2
    if not quiet:
        r = ctypes.windll.user32.MessageBoxW(
            None,
            ('要卸载 Memex 吗？' + chr(10) * 2
             + '程序文件和快捷方式会被删除；' + chr(10)
             + '搜索数据（索引库、设置、备份）会保留在原目录，重新安装后可继续用。'
             + chr(10) * 2 + '点「是」开始卸载，点「否」取消。'),
            'Memex · 卸载', 0x04 | 0x20 | 0x100)   # YESNO + 问号图标 + 前台
        if r != 6:      # IDYES
            return 0
    ok, script = uninstall.run_uninstall(app=brain.app_dir(), keep_data=True)
    if not ok:
        if not quiet:
            ctypes.windll.user32.MessageBoxW(
                None, '没能启动卸载程序，请手动删除安装目录：' + chr(10)
                + brain.app_dir(), 'Memex · 卸载失败', 0x10)
        return 1
    return 0


def _boot():
    argv = sys.argv[1:]
    # 第 370 项：免打包调试通道 —— %LOCALAPPDATA%\Memex\App\dev_override\<name>.py
    # 存在就顶替内置模块（只限卡片/框选/翻译这几个）。必须在所有分支之前装上，
    # 否则后面 `import snip_card` 拿到的是打进 exe 的旧那份。
    try:
        import dev_override
        _log('dev_override: %r' % (dev_override.boot(),))
    except Exception as _e:
        _log('dev_override boot failed: %r' % (_e,))
    # 第 302 项：安装目录里放一个 Uninstall.exe（和 Memex.exe 同一份二进制，
    # 只是文件名不同）。用户双击它就等于 Memex.exe --uninstall —— 不必记参数，
    # 也不依赖「设置 → 应用」能不能找到。判断必须在登记注册表之前，
    # 否则卸载器一启动又把卸载项写回去了。
    _as_uninstaller = os.path.basename(sys.executable).lower().startswith('uninstall')
    # 第 296 项：在「设置 → 应用」里登记卸载项（只对打包后的安装版生效）。
    # 位置必须在所有分支之前 —— 静默启动 / 托盘 / 命令行子命令都不会经过 _run_window，
    # 第一轮放在 _run_window 里，结果注册表一条都没写进去。
    if getattr(sys, 'frozen', False) and not _as_uninstaller:
        try:
            import uninstall
            uninstall.ensure_uninstall_entry(getattr(brain, 'APP_VERSION', '') or '')
        except Exception as _e:
            _log('uninstall entry failed: %r' % (_e,))
    if _as_uninstaller and '--uninstall' not in argv:
        argv.append('--uninstall')
    if '--uninstall' in argv:
        # 第 296 项：控制面板「卸载」按钮走这里（--quiet 时不弹确认框）
        sys.exit(_uninstall_cli(quiet=('--quiet' in argv)))
    if '--feedback' in argv:
        sys.exit(_feedback_cli(argv))
    if any(a in ('--diagnose', '/diagnose', '-diagnose') for a in argv):
        _diagnose()
        return
    rest, show_settings, lang, silent, start_mini = _parse_window_args(argv)
    # 第一个剩余 token 是已知子命令（update / search / selftest…）→ 走命令行
    if rest and not show_settings and not lang and not silent and rest[0] in _CLI_CMDS:
        _attach_console()
        try:
            brain.cli(rest)
        except SystemExit:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            _die(f'命令执行失败：{type(e).__name__}: {e}\n\n'
                 f'Command failed. Run it from a terminal to see the full traceback.')
    elif rest and rest[0].startswith('-'):
        # 不认识的标志：交给 cli 报 usage，而不是当作搜索词
        _attach_console()
        brain.cli(rest)
    elif argv:
        # 裸关键词 / --settings / --lang / --silent / --mini → 开窗口
        query = ' '.join(rest) if rest else None
        _run_window(query, show_settings, lang, silent, start_mini)
    else:
        _run_window(None, False, None)


if __name__ == '__main__':
    try:
        _boot()
    except SystemExit:
        raise
    except Exception:
        import traceback
        _fatal('Memex启动失败', traceback.format_exc()[-1200:])
        sys.exit(3)
