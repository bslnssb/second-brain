#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二大脑 · 桌面窗口启动器

两个窗口：
  快搜窗（/mini）  双击 Ctrl 弹出，一个输入框 + 九行结果，Ctrl+1~9 直接开，Esc 收起
  主窗口（/）      原来的完整界面：正文检索、类型/路径过滤、设置、索引维护

快搜窗按 Listary 的习惯做：无边框、圆角（Win11 的 DWM 圆角）、常驻置顶、
失焦自动收起。主窗口关掉只是隐藏，程序在托盘里等着，这样双击 Ctrl 随时能用。

带参数时走命令行（update / selftest / search …），方便打包成 exe 后复用同一入口。
"""
import ctypes

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
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# brain.cli 能认的子命令：这些一律走命令行，不当作搜索词。
# 没列在里面的裸参数（如 SecondBrain.exe 报销）视为「带关键词开窗，直接出结果」，
# 是配合 Listary / PowerToys Run 这类启动器的入口。
_CLI_CMDS = {'index', 'update', 'search', 'prune', 'serve', 'repair',
             'selftest', 'eval', '_extract', 'scan'}

MINI_W = 760                 # 快搜窗宽度（逻辑像素，CSS px 同尺度）
MINI_H = 124                 # 初始高度：一行输入框 + 一行提示
MINI_TITLE = '第二大脑 · 快搜'
MAIN_TITLE = 'Second Brain · 第二大脑'


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
        ctypes.windll.user32.MessageBoxW(None, msg, 'Second Brain · 第二大脑', 0x10)
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
        import traceback
        _START_ERR[0] = traceback.format_exc()
        _log_always('make_server FAILED (port %d):%s%s' % (port, chr(10), _START_ERR[0]))
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # 桌面模式同样要开实时监听：用户不会去点「更新」，新建的文件必须自己进库
    try:
        brain.start_watcher()
    except Exception:
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


def effective_dark():
    """界面主题（auto/dark/light）→ 现在到底是不是深色。

    第 105 项把 settings.json 的读取收口成 _settings_read() 时这里漏改了，
    AttributeError 被 except 吞成「读不到设置」→ theme=dark 时标题栏永远不变深
    （2026-11-07 用户切深色才发现）。"""
    try:
        import brain as _b
        th = (_b._settings_read() or {}).get('theme', 'auto')
    except Exception:
        th = 'auto'
    if th == 'dark':
        return True
    if th == 'light':
        return False
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


def paint_frame(hwnd, dark=None, bg=None):
    """把窗口外框刷成界面底色（第 184 项）。

    这里要治的是**三个不同来源**的白，缺一个都还会剩一条白边：
    1. `DWMWA_BORDER_COLOR`（34）—— 最外那 1px 描边；
    2. `DWMWA_CAPTION_COLOR`（35）—— 顶部那条「标题带」。样式位摘了 WS_CAPTION
       之后，WinForms/DWM 仍会按 FormBorderStyle 在顶部留一条 caption 带，
       **它的颜色不受 Form.BackColor 影响**，实测只能用它压（设成界面底色后白带消失）；
    3. 左右/下三边的填充 —— 由 Form.BackColor 决定，在 create_window 里给
       `background_color` 就定下来了。
    Win10 不认 34/35，返回非 0，忽略即可。
    """
    if not hwnd:
        return False
    # bg 优先：主题配色（暖纸 / 夜航蓝…）不是标准深浅色，只有前端知道确切底色
    r, g, b = bg if bg else ui_bg_rgb(dark)
    try:
        import ctypes
        # COLORREF 是 0x00BBGGRR
        v = ctypes.c_int((b << 16) | (g << 8) | r)
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 34, ctypes.byref(v), 4)
        # 顶部白带（caption 带）：不设这条，标题栏上方永远压着一条 8-9px 的白
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 35, ctypes.byref(v), 4)
        try:
            # 改完描边要立刻重画这一帧，否则滚动/拖动时旧的白边才会被覆盖掉
            u = ctypes.windll.user32
            u.RedrawWindow(ctypes.c_void_p(hwnd), None, None,
                           _RDW_FRAME | _RDW_INVALIDATE | _RDW_UPDATENOW)
        except Exception:
            pass
        return hr == 0
    except Exception as e:
        _log('paint_frame failed: %r' % (e,))
        return False


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
        # 返回**读回来的真实状态**：WinForms 会在 resize 时按 FormBorderStyle
        # 把样式写回去，所以「发过 SetWindowLongPtr」不等于「现在是目标状态」。
        try:
            u.RedrawWindow(h, None, None,
                           _RDW_FRAME | _RDW_INVALIDATE | _RDW_UPDATENOW)
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
    """自绘标题栏三键：min / max（切换）/ restore。"""
    if not hwnd:
        return False
    try:
        ctypes, u, _g, _p = _win_ptr_funcs()
        h = ctypes.c_void_p(hwnd)
        if cmd == 'min':
            u.ShowWindow(h, _SW_MINIMIZE)
        elif cmd == 'max':
            u.ShowWindow(h, _SW_RESTORE if u.IsZoomed(h) else _SW_MAXIMIZE)
        elif cmd == 'restore':
            u.ShowWindow(h, _SW_RESTORE)
        else:
            return False
        return True
    except Exception as e:
        _log('window_cmd(%s) failed: %r' % (cmd, e))
        return False


def window_maximized(hwnd):
    try:
        ctypes, u, _g, _p = _win_ptr_funcs()
        return bool(hwnd) and bool(u.IsZoomed(ctypes.c_void_p(hwnd)))
    except Exception:
        return False

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
            # 存的尺寸限制在「新默认的合理区间」：太窄（旧版 760/1040）和太宽（0.9.23 那版
            # 默认 1840、被 remember 记下来的）都收进来，否则用户看不到新默认
            if 1100 <= w <= sw + 40 and 560 <= h <= sh + 40:
                return min(w, 1400), min(h, 900)
        except Exception:
            pass
    # 「宽一点」但没有占满：默认屏宽 50%、高度 62%，夹到 [1180×720, 1400×900]。
    # 上一版给到 72%/1840，2560 屏上直接 1840 —— 用户的原话是「我只让你宽一半，这宽了两三倍」。
    mw = int(min(max(1180, sw * 0.50), 1400))
    mh = int(min(max(720, sh * 0.62), 900))
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
    """
    w = max(560, min(760, int(mw * 0.62)))
    h = max(360, min(520, int(mh * 0.62)))
    return min(w, int(mw)), min(h, int(mh))


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


def topmost(hwnd, tries=12):
    """把快搜窗拉到最前并抢到键盘焦点。

    这件事比看上去难，三个坑叠在一起：
    1. pywebview 的 show() 只是把动作排进 GUI 线程队列就返回，紧接着设前台会打在
       「窗口还没显示」的空档上 —— 所以要重试。
    2. 窗口已经显示、只是被别的程序盖住时，WinForms 的 Activate() 会被前台锁定规则拒掉。
    3. 真正管用的是「模拟一次 Alt 抬起」：系统看到本线程刚发过输入，就放行
       SetForegroundWindow（老牌做法，Wox / PowerToys Run 都用）。单按 Alt 本身
       不产生字符，焦点马上被我们抢走，副作用是看不见的。

    抢不到就返回 False —— 调用方需要知道，因为「按键打到别的程序里」比不弹窗更糟。
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
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            if i == 0:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            # AttachThreadInput：把自己的输入队列挂到当前前台线程上，绕过前台锁定
            fg = win32gui.GetForegroundWindow()
            tid_fg = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
            tid_me = win32api.GetCurrentThreadId()
            attached = False
            try:
                if tid_fg and tid_fg != tid_me:
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
        self._open_mini = None      # App 注入：唤出快搜窗（欢迎页按钮用）
        self._hide_main = None      # App 注入：收起详情窗（顶栏/标题栏「快搜」按钮用）

    def set_dark(self, dark, bg=None):
        """页面切主题时同步系统标题栏，免得「黑内容 + 白标题栏」。

        bg 是这套配色的底色（'#RRGGBB'，第 191 项）：暖纸、夜航蓝这些自定义配色
        既不是浅色也不是深色，只靠 dark 布尔值刷出来的框永远差一截。
        """
        try:
            _hw = find_hwnd(MAIN_TITLE)
            ok = dark_caption(_hw, bool(dark))
            # 深色开关会把 caption 色重置回系统值 → 顶部白带回来（第 184 项）。
            # 这里必须用前端报来的 dark：页面认的是 localStorage.sb_theme，
            # 用配置猜会在用户切主题那一刻对不上（深框套浅页）。
            paint_frame(_hw, bool(dark), _hex_rgb(bg))
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

    # ── 自绘标题栏（第 180 项）──────────────────────────────────────
    # 前端只负责画；窗口动作全在这里做。拖动必须交给系统（WM_NCLBUTTONDOWN），
    # 自己按像素 MoveWindow 会丢掉 Aero Snap、还会和 WebView2 的刷新打架。
    def titlebar_drag(self):
        """按住标题栏拖动（系统接管，含贴边吸附）。"""
        try:
            return caption_drag(find_hwnd(MAIN_TITLE))
        except Exception:
            return False

    def titlebar_cmd(self, cmd):
        """min / max / close。close 走 pywebview 的 destroy，让 closing 钩子照常跑。"""
        try:
            c = str(cmd or '')
            if c == 'close':
                if self._win is not None:
                    self._win.destroy()
                    return True
                return False
            return window_cmd(find_hwnd(MAIN_TITLE), c)
        except Exception as e:
            _log('titlebar_cmd(%r) failed: %r' % (cmd, e))
            return False

    def titlebar_state(self):
        """最大化状态 → 前端换 □ / ❐ 图标。"""
        try:
            return {'max': window_maximized(find_hwnd(MAIN_TITLE))}
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
            hwnd = find_hwnd(MAIN_TITLE)
            if not hwnd:
                return None
            ctypes, u, _g, _p = _win_ptr_funcs()
            r = ctypes.wintypes.RECT()
            if not u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
                return None
            return {'x': int(r.left), 'y': int(r.top),
                    'max': bool(u.IsZoomed(ctypes.c_void_p(hwnd)))}
        except Exception as e:
            # 用 _log_always：这类异常（名字写错、结构缺失）会被吞成「功能没反应」，
            # 只在调试日志里可见等于没有 —— 必须落到常规错误日志
            _log_always('titlebar_rect failed: %r' % (e,))
            return None

    def titlebar_restore(self, mouse_x=None):
        """拖动开始时如果在最大化状态：先还原，再把窗口摆到鼠标下方（第 182 项）。

        为什么必须处理：**最大化窗口的 SetWindowPos 会被系统忽略**，
        用户在上面拖就会觉得「完全拖不动」——而这正是最常出现的状态。
        还原后按鼠标在标题栏的水平位置反推窗口左上角，人手感接近原生。
        """
        try:
            hwnd = find_hwnd(MAIN_TITLE)
            if not hwnd:
                return None
            ctypes, u, _g, _p = _win_ptr_funcs()
            h = ctypes.c_void_p(hwnd)
            if u.IsZoomed(h):
                u.ShowWindow(h, _SW_RESTORE)
                time.sleep(0.02)
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
            return {'x': x, 'y': int(r.top)}
        except Exception as e:
            _log_always('titlebar_restore failed: %r' % (e,))
            return None

    def titlebar_move(self, x, y):
        """把窗口挪到屏幕坐标 (x, y)，尺寸不变（第 182 项）。

        拖动时前端按帧调用，所以这必须是「最轻」的一条：
        只 SetWindowPos，不重新布局、不动焦点、不触发 WebView2 刷新。
        """
        try:
            hwnd = find_hwnd(MAIN_TITLE)
            if not hwnd:
                return False
            ctypes, u, _g, _p = _win_ptr_funcs()
            u.SetWindowPos(ctypes.c_void_p(hwnd), None, int(x), int(y), 0, 0,
                           _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
            # 只记第一次：这一行能区分「前端没调到」和「调到了但窗口不动」
            if not getattr(self, '_tb_move_logged', False):
                self._tb_move_logged = True
                _log_always('titlebar_move 首次调用 → %s,%s' % (x, y))
            return True
        except Exception as e:
            _log_always('titlebar_move(%s,%s) failed: %r' % (x, y, e))
            return False

    def titlebar_snap(self):
        """松手时贴边吸附（第 182 项）：顶上最大化、左右各半屏。

        摘掉 WS_CAPTION 后系统不再提供 Aero Snap，而拖动改成了自己 SetWindowPos，
        所以这两条得自己补。只在「贴着边缘松手」时动手，平时什么都不做。
        容差 6px：太小了用户拖不准，太大了窗口从边缘弹开很突兀。
        """
        try:
            hwnd = find_hwnd(MAIN_TITLE)
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
            tol = 6
            sw = _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_SHOWWINDOW
            if int(r.top) <= wt_ + tol:
                u.ShowWindow(h, _SW_MAXIMIZE)
                return True
            if int(r.left) <= wl + tol:
                u.SetWindowPos(h, None, wl, wt_, (wr - wl) // 2, wb - wt_, sw)
                return True
            if int(r.right) >= wr - tol:
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
                if not caption_off(hwnd, True):
                    self.custom_titlebar = False
                    _log('custom titlebar: 守卫发现样式被写回且摘不回来，退回系统标题栏')
                    try:
                        import brain as _b
                        _b.TITLEBAR_ACTIVE = False
                    except Exception:
                        pass
                    return
                if had or not prev_had_caption:
                    # 写回刚发生 / 第一轮：外框底色与 caption 带要重新压一遍
                    paint_frame(hwnd)
                prev_had_caption = had
            except Exception as e:
                _log('titlebar guard: %r' % (e,))
                return

    def pick_folder(self):
        if webview is None or self._win is None:
            return ''
        try:
            picked = self._win.create_file_dialog(webview.FOLDER_DIALOG)
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
            picked = self._win.create_file_dialog(webview.OPEN_DIALOG)
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
            os.startfile(p)
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
        按下去走的是 open_path（直接打开）。补上原生桥。"""
        p = str(p or '')
        if not p or not brain.ev_recent(p):
            return False
        try:
            import subprocess
            subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])
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
                os.startfile(p)
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
        self._hide_hint_given = False
        self._want_main = False      # 用户主动要求显示主窗（silent 守卫据此让路）
        self._lock = threading.Lock()

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
            hidden=True, background_color='#FFFFFF', js_api=self.mini_api,
            text_select=False, focus=True, min_size=(MINI_W, 96),
        )
        self.main_api = Api()
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

    def _late_dark_caption(self, tries=6):
        """窗口刚建好时 DWM 还不接受属性；等一会儿再刷，并随每次显示重刷。"""
        for _ in range(tries):
            time.sleep(0.6)
            try:
                hw = self.hwnd_main or find_hwnd(MAIN_TITLE)
                ok = bool(hw) and dark_caption(hw, effective_dark())
                if hw:
                    # 深色开关会重置 caption 色 → 顶部白带回来（第 184 项），必须补刷
                    paint_frame(hw)
                if ok:
                    _log('dark caption applied (%s)' % effective_dark())
                    return
            except Exception as e:
                _log('dark caption retry failed: %r' % (e,))
        _log('dark caption gave up')

    def _remember_size(self):
        """把用户拖出来的窗口大小记住，下次按这个开（不会因为换分辨率炸掉）。"""
        try:
            import brain as _b
            w, h = self.main.width, self.main.height
            if 700 <= int(w) <= 4000 and 480 <= int(h) <= 3000:
                _b._settings_mark('win_w', int(w))
                _b._settings_mark('win_h', int(h))
        except Exception:
            pass

    def _on_main_closing(self):
        """主窗口「关闭」= 收进托盘。托盘没起来就老老实实退出，不留一个看不见的进程。

        不在 closing 事件里直接调 hide()：WinForms 的 FormClosing 里同步调 Hide()
        偶尔会让消息循环卡住，之后 show() 也不响应。改成延迟 50ms 再 hide，
        让 FormClosing 先走完 cancel 流程。"""
        _log(f'_on_main_closing tray={self.tray} ok={getattr(self.tray,"ok",None)}')
        self._remember_size()
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
        try:
            self.main.hide()
            _log_always('_safe_hide_main ok')   # exe 模式 _log 不落盘——hide 是否执行是白屏排查的第一线索
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
                 '第二大脑', '已收进托盘：双击 Ctrl 唤出快搜，托盘图标打开详细搜索',
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

    def after_start(self):
        self.hwnd_mini = wait_hwnd(MINI_TITLE)
        self.hwnd_main = wait_hwnd(MAIN_TITLE)
        # 环境快照：用户报「窗口不对/看不到」时，这一行就是现场数据。
        try:
            _log_always('[env] %s silent=%s target=%dx%d'
                        % (env_snapshot(self.hwnd_main), self.silent,
                           int(self._main_w), int(self._main_h)))
        except Exception:
            pass
        threading.Thread(target=self._watch_loaded_or_fallback, daemon=True).start()
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
                paint_frame(self.hwnd_main)
                _log('custom titlebar: WS_CAPTION stripped')
            else:
                _log('custom titlebar: 摘除失败，退回系统标题栏')
        self._fit_main_size()
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
        self.start_tray()
        self._start_activate_listener()

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

    def _force_webview_visible(self):
        """把 WebView2 控件自身的 Visible 设回 true（根因修复）。

        silent 启动隐藏主窗口时，WebView2 控制器的 IsVisible 被设成 false，渲染层
        的 Chrome_* 子窗口随之隐藏；光对外层子窗口 ShowWindow 会被控制器在下一次
        合成时重新藏回，所以必须直接动控件。pywebview 的 Window.native 是
        BrowserForm，其 .webview 就是 .NET 的 WebView2 控件。属性名跨版本可能变，
        整段包在 try 里，失败就退回 revive_webview 的治标逻辑。"""
        try:
            impl = getattr(self.main, 'native', None)
            if impl is None:
                return
            ctrl = getattr(impl, 'webview', None)
            if ctrl is None:
                return
            ctrl.Visible = True
        except Exception as e:
            _log('force webview visible FAILED: %r' % (e,))

    def _fit_main_size(self):
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
            ok = set_client_size(hwnd, tw, th)
            _log_always('fit_main_size: %s → client=%s visible_after=%s'
                        % ('resized' if ok else 'FAILED', get_client_size(hwnd), _is_visible(hwnd)))
        except Exception as e:
            _log('fit_main_size FAILED: %r' % (e,))

    def place_mini(self):
        """把快搜窗摆到鼠标所在的那块屏上（单屏时就是原来的居中 + 17%）。"""
        try:
            cur = cursor_monitor_rect()
            prim = primary_monitor_rect()
            if cur and prim and cur != prim:
                self._place_mini_on(cur)
            else:
                x, y = mini_origin(self.hwnd_mini)
                self.mini.move(x, y)
        except Exception as e:
            _log('place_mini FAILED: %r' % (e,))

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

    def show_mini(self):
        try:
            if not self.hwnd_mini:
                self.hwnd_mini = wait_hwnd(MINI_TITLE, 2.0)
            _log(f'show_mini begin mini={self.mini} hwnd={self.hwnd_mini}')
            self.mini.show()
            self.mini_visible = True
            self.place_mini()          # 位置矫正放在 show 之后：move 本身会触发显示
            if not topmost(self.hwnd_mini):
                _log(f'show_mini topmost FAIL fg={foreground_desc()}')
                # 抢焦点失败时最坏的情况是「用户以为在搜索框里打字，其实打进了别的程序」。
                # 真正 Hide 一次再 Show 走的是系统「激活新显示窗口」的路径，一定拿得到。
                try:
                    self.mini.hide()
                    time.sleep(0.06)
                    self.mini.show()
                    self.place_mini()
                    _log(f'retry topmost ok={topmost(self.hwnd_mini)} fg={foreground_desc()}')
                    self.mini_visible = True
                except Exception:
                    pass
            try:
                self.mini.evaluate_js('window.__onShow&&window.__onShow()')
            except Exception:
                pass
            _log('show_mini done')
        except Exception as e:
            _log(f'show_mini EXCEPTION: {type(e).__name__}: {e}')

    def hide_mini(self):
        _log('hide_mini')
        try:
            self.mini.hide()
        except Exception:
            pass
        self.mini_visible = False

    def on_mini_blur(self):
        """失焦处理：见 MiniApi.blurred 的说明。返回 True 表示真的收起了。

        「鼠标还在窗口上」只在刚 resize 完的一小段时间里算数——那时候的失焦是
        WebView2 重排的副作用。超过这个窗口还失焦，就说明用户是按键切走的
        （Alt+Tab / Win），这时候赖着不收反而烦人，必须收起。"""
        if not self.mini_visible:
            return False
        time.sleep(0.14)               # 等焦点切换稳定，别被中间态骗了
        if foreground_is(self.hwnd_mini):
            _log('blur → 前台还是自己，忽略')
            return False
        since = time.time() - getattr(self, '_resize_ts', 0)
        _log(f'blur since_resize={since:.2f} cursor_in={cursor_inside(self.hwnd_mini)} '
             f'fg={foreground_desc()}')
        if since < 0.7 or (since < 1.6 and cursor_inside(self.hwnd_mini)):
            topmost(self.hwnd_mini, tries=5)   # resize 的副作用，抢回来
            return False
        self.hide_mini()
        return True

    def resize_mini(self, h):
        if not self.mini_visible:
            return                     # 隐藏状态下 resize 会让窗口自己蹦出来
        h = max(96, min(int(h), 1000))
        _log(f'resize → {h}')
        try:
            self.mini.resize(MINI_W, h)
        except Exception:
            return
        # 改窗口尺寸会让 WebView2 重新布局，实测有约三分之一概率把激活状态弄丢，
        # 表现为「结果刚出来，窗口就自己关了」（失焦逻辑干的）。
        # 记个时间窗，这段时间内的失焦一律当作 resize 的副作用抢回来。
        self._resize_ts = time.time()

        def _refocus():
            time.sleep(0.12)
            if self.mini_visible and not foreground_is(self.hwnd_mini):
                _log('refocus 抢焦点')
                topmost(self.hwnd_mini, tries=4)
        threading.Thread(target=_refocus, daemon=True).start()

    # -- 主窗口 -------------------------------------------------------
    def show_main(self, q=None, panel=None, focus=None):
        """从快搜窗跳过来：show 主窗口 + 设搜索词。

        这里【绝不能】在出错时退回 webbrowser.open。那是「窗口组件加载失败就退到
        浏览器模式」的老兜底，被滥用在这里之后，用户点一下「展示更多 …」就会
        冒出一个浏览器标签打开 127.0.0.1:8765/web/ —— 明明装的是 exe，看起来
        却像突然退化成网页，而且真正的异常被彻底掩盖。出错只记日志，界面必须
        留在桌面窗口里。

        新界面是 React，只认 /web/?q= 与 ?panel=。旧模板那句
        `qEl.value=…;go()` 在 React 里必然 ReferenceError，别再注入它。
        """
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

            self.main.show()

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

        # ② 导航带上搜索词。第一次必须用 load_url（此时 _pywebviewready 还没置位，
        #    evaluate_js 会一直等到超时）；之后改用整页导航，React 会读 ?q= 直接出结果。
        try:
            if not getattr(self, '_main_loaded', False):
                self.main.load_url(target)
                self._main_loaded = True
            elif panel or rel != getattr(self, '_main_target', ''):
                # panel 必须每次都重新导航：React 的 ?panel= 只在挂载时读一次，
                # 用户关掉抽屉再点同一个托盘菜单，URL 没变也得强制重载才打得开。
                import json as _j
                self.main.evaluate_js('location.href=' + _j.dumps(rel))
            self._main_target = rel
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

        # ④ 抢前台，失败也不影响前面几步
        try:
            ok = topmost(hwnd)
            _log_always(f'show_main topmost={ok} visible_now={_is_visible(hwnd)} '
                 f'fg={foreground_desc()}')
        except Exception:
            _log_always('show_main: 抢前台失败\n' + traceback.format_exc())

    def hide_main(self):
        try:
            self.main.hide()
        except Exception:
            pass

    # -- 钩子 / 托盘 ---------------------------------------------------
    def _install_hotkey(self):
        """装热键前先看设置：用户可能在前端关掉了双击 Ctrl。

        设置存在 settings.json 的 hotkey 字段，默认开。关掉后 stop hook，
        再次开启重新 start。前端 /api/hotkey 读写这个文件。"""
        if not self._hotkey_enabled():
            _log('双击 Ctrl 热键已被设置关闭，不安装')
            self._hk_paused = True
            return None
        self._hk_paused = False
        try:
            import hotkey
        except Exception:
            return None
        try:
            hk = hotkey.start_double_ctrl(self._hotkey_fired)
            if hk is None:
                print('双击 Ctrl 热键没能装上（不影响其它功能）')
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
        if on and self.hotkey is None:
            # 重新装
            import hotkey
            try:
                self.hotkey = hotkey.start_double_ctrl(self._hotkey_fired)
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

    def _hotkey_fired(self):
        with self._lock:
            self.toggle_mini()

    def start_tray(self):
        try:
            import tray
        except Exception:
            return
        try:
            self.tray = tray.Tray(
                # 「打开详细搜索」带上快搜窗最后在搜的词，接着刚才的继续查
                on_main=lambda: self.show_main(brain.LAST_Q or None),
                on_mini=lambda: self.show_mini(),
                on_quit=self.quit,
                on_settings=self.tray_settings,
                on_about=self.tray_about,
                on_feedback=self.tray_feedback,
                on_help=self.tray_help,
                tip='第二大脑（双击 Ctrl 唤出快搜）',
            )
            if not self.tray.start():
                self.tray = None
        except Exception:
            self.tray = None
        try:
            _start_auto_update(self)
        except Exception as e:
            _log('start auto-update failed: %r' % (e,))

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
        """托盘里点「设置 / 关于 / 使用帮助」：把主窗口叫出来并直接摊开对应的面板。

        以前的做法是 show_main() 之后 evaluate_js 注入——那条路有两个坑：
        页面没加载完注入必然失败；加载中调 evaluate_js 还会把线程卡住。
        现在面板走 URL 参数，页面自己在启动时摊开，稳。
        """
        try:
            self.show_main(panel=name, focus=focus)
        except Exception as e:
            _log(f'open_panel({name}) FAILED: {e}')

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

    def tray_balloon(self, text, title='第二大脑'):
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
        t0 = time.perf_counter()
        marks = []

        def mark(name):
            marks.append('%s=%.0fms' % (name, (time.perf_counter() - t0) * 1000))

        try:
            if self.hotkey:
                self.hotkey.stop()
            mark('hotkey')
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
        for title in ('Second Brain · 第二大脑', '第二大脑（双击 Ctrl 唤出快搜）'):
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
        print('第二大脑已经在运行了，已把窗口叫到前面。')
        return
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
        _fatal('第二大脑启动失败',
               ('本地服务没能起来，所以窗口里只会显示「127.0.0.1 未发送任何数据」。'
                + chr(10) + chr(10) + '错误：' + tail
                + chr(10) + chr(10) + '常见原因：程序目录写不进去（只读位置 / 安全软件锁定 / 解压不完整）。'
                + '把整个文件夹挪到桌面或文档下面再试一次。'))
        return
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
        _fatal('第二大脑：窗口启动失败，已改用浏览器打开',
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
                    ('第二大脑需要「WebView2 运行时」，这台电脑上没找到。'
                     + chr(10) * 2
                     + '点「确定」会打开微软官方下载页，装好后重新打开第二大脑即可。'
                     + chr(10)
                     + '（安装包很小，约 2 MB；装一次，以后都不用再装。）'),
                    '第二大脑 · 缺少 WebView2 运行时', 0x01)
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
            ctypes.windll.user32.MessageBoxW(None, body, '第二大脑 · 环境自检', 0x40)
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
            print('第二大脑 · 报告问题')
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
                        _log_always('auto-update: %s 已下载，等用户重启' % ver)
                        try:
                            app.tray_balloon('新版本 %s 已下载，设置里点「重启更新」生效' % ver)
                        except Exception:
                            pass
                    else:
                        _log_always('auto-update: 下载失败 %r' % (r.get('k') or r,))
            except Exception as e:
                _log('auto-update failed: %r' % (e,))
            time.sleep(6 * 3600)
    threading.Thread(target=work, name='auto-update', daemon=True).start()


def _boot():
    argv = sys.argv[1:]
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
        _fatal('第二大脑启动失败', traceback.format_exc()[-1200:])
        sys.exit(3)
