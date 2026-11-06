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
try:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_int(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except Exception:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)                       # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
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
    再 WM_SETICON。小图标给 16，大图标给 0（让系统按 DPI 自己挑 256 那档）。"""
    ico = ico or icon_file()
    if not hwnd or not ico:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        LR_LOADFROMFILE, IMAGE_ICON, WM_SETICON = 0x10, 1, 0x0080
        u.LoadImageW.restype = ctypes.c_void_p
        big = u.LoadImageW(0, ico, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        small = u.LoadImageW(0, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
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
    """窗口显隐的时序问题只能靠时间戳看。正常启动不开，排查时设 SB_DEBUG=1。"""
    if DEBUG:
        line = f'[{time.strftime("%H:%M:%S")}.{int(time.time() * 1000) % 1000:03d}] {msg}'
        try:
            with open('debug.log', 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            print(line, flush=True)


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


def _fatal(title, text):
    """窗口起不来时别让用户对着空气——弹一个能读的框，并把详情写进日志。"""
    _log_always('FATAL %s: %s' % (title, text))
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


def _is_ours(port):
    """端口上跑的是不是本应用（而不是恰好占了同一端口的别的程序）"""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=0.8) as r:
            return json.loads(r.read().decode('utf-8')).get('app') == 'second-brain'
    except Exception:
        return False


def _pick_port():
    """找一个能用的端口：空闲的优先，其次复用已在运行的本应用实例。
    全部被占返回 0。原实现直接用 8765，被别的程序占用时会直接抛异常退出。"""
    for port in range(BASE_PORT, BASE_PORT + PORT_TRIES):
        if not _port_busy(port):
            return port
        if _is_ours(port):
            return port
    return 0


def _start_server(port):
    if _port_busy(port):
        return None          # 已有实例在跑，直接复用
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


def find_hwnd(title):
    """按标题找本进程的窗口句柄（pywebview 没暴露 HWND，绕一圈拿到原生句柄）"""
    try:
        import win32gui
    except Exception:
        return None
    hits = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == title and win32gui.IsWindow(h):
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


def mini_origin(hwnd=None):
    """快搜窗出现的位置：屏幕水平居中，垂直约 17% —— 视线自然落点，
    底部还留得下九行结果。返回的是逻辑像素，和 create_window 的单位一致。"""
    sw, sh = screen_logical_size(hwnd)
    return max(0, (sw - MINI_W) // 2), max(0, int(sh * 0.17))


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
    """界面主题（auto/dark/light）→ 现在到底是不是深色。"""
    try:
        import brain as _b
        th = (_b._settings_dict() or {}).get('theme', 'auto')
    except Exception:
        th = 'auto'
    if th == 'dark':
        return True
    if th == 'light':
        return False
    return system_dark()


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
    mw = max(760, min(mw, int(sw * 0.92)))
    mh = max(520, min(mh, int(sh * 0.90)))
    return mw, mh


def screen_logical_size(hwnd=None):
    """逻辑像素的屏幕尺寸。pywebview 的 move/resize 用逻辑像素（会乘 DPI 缩放），
    而 GetSystemMetrics 给的是物理像素——150% 缩放下这两个差 1.5 倍，直接用会偏到屏幕外。"""
    u, wt = _win()
    if not u:
        try:
            s = webview.screens[0]
            return s.width, s.height
        except Exception:
            return 1920, 1080
    try:
        dpi = u.GetDpiForWindow(hwnd) if hwnd else u.GetDpiForSystem()
        if not dpi:
            dpi = 96
        sc = dpi / 96.0
        return int(u.GetSystemMetrics(0) / sc), int(u.GetSystemMetrics(1) / sc)
    except Exception:
        return 1920, 1080


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


def revive_webview(hwnd):
    """把 hide 之后没跟着 show 的 WebView2 渲染子窗口重新显示（治「整块白窗口」）。

    `--silent` 启动会先 hide 整个主窗口，连 WebView2 的 Chrome_WidgetWin_* 一起
    设成 WS_VISIBLE=0。之后 pywebview 的 Form.Show() / Win32 SW_RESTORE 只恢复
    WinForms 外壳，Chromium 的渲染层还藏着 —— 用户看到的是一张白底板，而服务、
    页面、静态资源其实全都正常（无头浏览器打开同一个 URL 能完整渲染）。
    2026-11-06 实测：silent 启动后从托盘打开，主窗口 4 个子窗口里 3 个是 hidden。

    只在真的发现隐藏子窗口时才动作并留一行日志，正常显示不产生任何副作用。
    """
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
        _log_always('白屏自愈：唤醒 %d 个隐藏的 WebView2 子窗口' % len(hidden))
        return len(hidden)
    except Exception:
        _log_always('白屏自愈失败\n' + traceback.format_exc())
        return 0


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

    def set_dark(self, dark):
        """页面切主题时同步系统标题栏，免得「黑内容 + 白标题栏」。"""
        try:
            return dark_caption(find_hwnd(MAIN_TITLE), bool(dark))
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
        self.hwnd_main = None
        self.tray = None
        self.mini_visible = False
        self.hotkey = None
        self._hide_hint_given = False
        self._lock = threading.Lock()

    # -- 窗口 ---------------------------------------------------------
    def create(self):
        # 位置必须在创建时给定：pywebview 的 move()/resize() 会让隐藏窗口变可见
        # （底层是 winforms 的 Location/Size 赋值，会顺手把窗体 Show 出来）。
        # 实测过：对 hidden 窗口调一次 move，IsWindowVisible 就从 0 变 1。
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
        sw, sh = screen_logical_size()
        saved = None
        try:
            import brain as _b
            _st = _b._settings_dict()
            if _st.get('win_w') and _st.get('win_h'):
                saved = (int(_st['win_w']), int(_st['win_h']))
        except Exception:
            saved = None
        MW, MH = main_window_size(sw, sh, saved)
        self._main_w, self._main_h = MW, MH
        # WinForms 外壳把 create_window 的 width 当作物理像素，不随 DPI 放大；
        # 但 WebView2 在 125%/150% 屏上按窗口真实 DPI 渲染 CSS（DPR>1），CSS 视口被
        # 缩成「物理/DPI」，比设计窄，字号/图标就显得过大。这里先用逻辑尺寸建窗，
        # after_start 里再按实际 DPI 把物理尺寸补成「逻辑 × DPR」（见 _fit_main_dpi）。
        # 主窗口【不能】hidden 创建：pywebview 的 show()/load_url() 都带
        # @_shown_call，要先等 WinForms 的 Form.Shown 事件置位；hidden 窗口永远不触发
        # 这个事件，于是「从快搜窗点展示更多 → show_main」会卡满 20 秒再抛
        # WebViewException，最后被旧的兜底逻辑丢进系统浏览器。
        # silent（开机自启）时不显示窗口，改为在 after_start 等窗口真正 shown 之后再 hide。
        self.main = webview.create_window(
            MAIN_TITLE, self.main_url,
            width=MW, height=MH, min_size=(max(760, int(MW * 0.62)), max(520, int(MH * 0.62))),
            text_select=True, js_api=self.main_api, hidden=False,
        )
        self.main_api._win = self.main
        self.main.events.closing += self._on_main_closing

    def _late_dark_caption(self, tries=6):
        """窗口刚建好时 DWM 还不接受属性；等一会儿再刷，并随每次显示重刷。"""
        for _ in range(tries):
            time.sleep(0.6)
            try:
                hw = self.hwnd_main or find_hwnd(MAIN_TITLE)
                if hw and dark_caption(hw, effective_dark()):
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
            else:
                _log_always('主窗口 shown 事件超时，silent 模式未隐藏窗口')
        except Exception:
            _log_always('_hide_after_shown 失败\n' + traceback.format_exc())

    def _safe_hide_main(self):
        """在 closing 事件之外 hide 主窗口，避开 WinForms FormClosing 的时序陷阱。"""
        try:
            self.main.hide()
            _log('_safe_hide_main ok')
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

    def after_start(self):
        self.hwnd_mini = wait_hwnd(MINI_TITLE)
        self.hwnd_main = wait_hwnd(MAIN_TITLE)
        # silent 模式主窗口按非 hidden 创建（见 create 里的说明），等 Form.Shown
        # 真正置位、events.shown 可用之后再收起来。用事件而不是固定 sleep：
        # 窗口只露出一瞬间，开机自启不会闪一大下。
        if self.silent:
            threading.Thread(target=self._hide_after_shown, daemon=True).start()
        self._fit_main_dpi()
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

    def _fit_main_dpi(self):
        """高 DPI 修正：pywebview 基于 .NET WinForms，create_window 的 width 被当作
        物理像素，不会按显示器 DPI 自动放大；而 WebView2 在 125%/150% 屏上以窗口真实
        DPI（DPR>1）渲染 CSS，于是 CSS 视口被缩成「物理/DPI」、比设计窄，字号/图标相对
        就显得过大（即用户看到的「一切都被放大了」）。

        这里把窗口物理尺寸补成「逻辑尺寸 × DPR」，让 CSS 视口回到设计宽度，内容比例
        恢复正常。dpr==1 时无需调整，直接跳过。"""
        if not self.hwnd_main:
            return
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(self.hwnd_main) or 96
            dpr = dpi / 96.0
            if dpr == 1.0:
                return
            # 实测（150% 屏）：create_window 的尺寸被 WinForms 当物理像素，而 resize()
            # 自己还会再乘一次 DPR —— 所以这里传「逻辑目标」，CSS 视口才会等于 _main_w。
            # 旧代码传 _main_w*dpr，等于乘了两次，视口反而被撑到 1.5 倍宽。
            self.main.resize(int(self._main_w), int(self._main_h))
        except Exception:
            pass

    def place_mini(self):
        try:
            x, y = mini_origin(self.hwnd_mini)
            self.mini.move(x, y)
        except Exception:
            pass

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
    def show_main(self, q=None, panel=None):
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
        rel = '/web/' + ('?' + '&'.join(qs) if qs else '')
        target = self.url + rel

        # ① 显示窗口。换深色标题栏、抢前台这类非关键动作各自单独吞异常，
        #    别让它们把整个流程带进一个「统一 except」再触发跳浏览器。
        try:
            hwnd = self.hwnd_main or find_hwnd(MAIN_TITLE)
            if not hwnd:
                hwnd = wait_hwnd(MAIN_TITLE, 2.0)
                if hwnd:
                    self.hwnd_main = hwnd
            _log(f'show_main begin q={q!r} hwnd={hwnd} '
                 f'visible={_is_visible(hwnd)} loaded={getattr(self, "_main_loaded", False)}')

            self.main.show()

            # pywebview 的 show() 在窗口被 hide 过之后偶尔不生效（Form.Visible 卡
            # 在 False）。用 Win32 ShowWindow 兜底，直接对原生句柄发 SW_SHOW。
            if hwnd:
                try:
                    import win32con
                    import win32gui
                    if not win32gui.IsWindowVisible(hwnd):
                        _log('show_main: Form.Show() 没生效，用 Win32 SW_SHOW 兜底')
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                except Exception as e:
                    _log(f'show_main: SW_SHOW fallback FAILED: {e}')
                try:
                    dark_caption(hwnd, effective_dark())
                except Exception as e:
                    _log('dark caption on show failed: %r' % (e,))
                # Win32 强制重排一次：让 WebView2 收到 WM_SIZE 重建渲染表面，
                # 否则它会在下次合成时把刚 show 出来的渲染子窗口又藏回（白屏）。
                try:
                    self.main.resize(self.main.width, self.main.height)
                except Exception:
                    pass
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
        #    合成是异步的，单点唤醒容易被它自己的布局循环盖掉，所以分两拍。
        def _late_revive():
            for delay in (0.3, 0.8):
                time.sleep(delay)
                revive_webview(hwnd)
                self._force_webview_visible()

        threading.Thread(target=_late_revive, daemon=True).start()

        # ④ 抢前台，失败也不影响前面几步
        try:
            ok = topmost(hwnd)
            _log(f'show_main topmost={ok} visible_now={_is_visible(hwnd)} '
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

    def open_panel(self, name):
        """托盘里点「设置 / 关于 / 使用帮助」：把主窗口叫出来并直接摊开对应的面板。

        以前的做法是 show_main() 之后 evaluate_js 注入——那条路有两个坑：
        页面没加载完注入必然失败；加载中调 evaluate_js 还会把线程卡住。
        现在面板走 URL 参数，页面自己在启动时摊开，稳。
        """
        try:
            self.show_main(panel=name)
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

    def tray_feedback(self):
        """反馈：打开 GitHub 的 issues 页。
        没有网络/没装浏览器就退回复制联系方式，别让按钮点了没反应。"""
        url = 'https://github.com/%s/issues' % getattr(brain, 'REPO_SLUG', 'bslnssb/second-brain')
        try:
            import webbrowser
            ok = webbrowser.open(url)
        except Exception:
            ok = False
        if not ok:
            try:
                import tkinter as tk
                r = tk.Tk(); r.withdraw()
                r.clipboard_clear(); r.clipboard_append(url); r.update(); r.destroy()
            except Exception:
                pass

    def quit(self):
        try:
            if self.hotkey:
                self.hotkey.stop()
            if self.tray:
                self.tray.quit()
        except Exception:
            pass
        try:
            brain.conn().close()
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


_MUTEX_NAME = 'Local\\SecondBrain.Desktop.SingleInstance'
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
        h = k.CreateMutexW(None, False, _MUTEX_NAME)
        if not h:
            return True
        if k.GetLastError() == 183:            # ERROR_ALREADY_EXISTS
            _log('single instance: another copy is running')
            _activate_running()
            return False
        _MUTEX_H = h                           # 保持句柄，进程退出自动释放
        return True
    except Exception as e:
        _log(f'single instance FAILED: {e}')
        return True


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


def _run_window(query, show_settings, lang, silent=False, start_mini=False):
    try:
        main(query=query, show_settings=show_settings, lang=lang,
             silent=silent, start_mini=start_mini)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _die(f'窗口启动失败：{type(e).__name__}: {e}\n\n'
             f'Window failed to start.\n'
             f'通常是缺少 WebView2 运行时，可从以下地址安装：\n'
             f'https://developer.microsoft.com/microsoft-edge/webview2/')


def _boot():
    argv = sys.argv[1:]
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
