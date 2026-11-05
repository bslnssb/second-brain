#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二大脑 · 桌面窗口启动器

两个窗口：
  快搜窗（/mini）  双击 Ctrl 弹出，一个输入框 + 九行结果，Ctrl+1~9 直接开，Esc 收起
  主窗口（/）      原来的完整界面：正文检索、类型/路径过滤、设置、索引维护

快搜窗按大家熟悉的习惯做：无边框、圆角（Win11 的 DWM 圆角）、常驻置顶、
失焦自动收起。主窗口关掉只是隐藏，程序在托盘里等着，这样双击 Ctrl 随时能用。

带参数时走命令行（update / selftest / search …），方便打包成 exe 后复用同一入口。
"""
import ctypes
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# brain.cli 能认的子命令：这些一律走命令行，不当作搜索词。
# 没列在里面的裸参数（如 SecondBrain.exe 报销）视为「带关键词开窗，直接出结果」，
# 是配合 PowerToys Run 这类启动器的入口。
_CLI_CMDS = {'index', 'update', 'search', 'prune', 'serve',
             'selftest', 'eval', '_extract', 'scan'}

MINI_W = 760                 # 快搜窗宽度（逻辑像素，CSS px 同尺度）
MINI_H = 124                 # 初始高度：一行输入框 + 一行提示
MINI_TITLE = '第二大脑 · 快搜'
MAIN_TITLE = 'Second Brain · 第二大脑'

DEBUG = bool(os.environ.get('SB_DEBUG'))


def _log(msg):
    """窗口显隐的时序问题只能靠时间戳看。正常启动不开，排查时设 SB_DEBUG=1。"""
    if DEBUG:
        print(f'[{time.strftime("%H:%M:%S")}.{int(time.time() * 1000) % 1000:03d}] {msg}',
              flush=True)


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
    srv = brain.make_server(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ------------------------------------------------------------------ Win32 小工具
def _win():
    """懒加载 win32 相关；拿不到就返回 None，调用方全部走「没有就算了」的分支"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.WinDLL('user32', use_last_error=True)
        u.SetProcessDPIAware()
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


class MiniApi:
    """快搜窗的原生能力。打开文件必须过 brain.ev_recent 的白名单——
    os.startfile 对 .exe 就是「运行」，页面能自己拼路径就等于开后门。"""

    def __init__(self, app):
        self._app = app          # 下划线开头：别让 pywebview 递归展开整个 App

    def resize(self, h):
        try:
            self._app.resize_mini(int(h))
        except Exception:
            pass
        return True

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
        self.main = webview.create_window(
            MAIN_TITLE, self.main_url,
            width=1360, height=880, min_size=(900, 600),
            text_select=True, js_api=self.main_api, hidden=self.silent,
        )
        self.main_api._win = self.main
        self.main.events.closing += self._on_main_closing

    def _on_main_closing(self):
        """主窗口「关闭」= 收进托盘。托盘没起来就老老实实退出，不留一个看不见的进程。"""
        if self.tray is not None and self.tray.ok:
            self.main.hide()
            self._hint_once()
            return False
        return True

    def _hint_once(self):
        if self._hide_hint_given:
            return
        self._hide_hint_given = True
        try:
            self.main.evaluate_js(
                "window.toast&&window.toast('已收进托盘，双击 Ctrl 随时唤出快搜')")
        except Exception:
            pass
        try:                     # 窗口都隐藏了就没人看得见提示，用托盘气泡更实在
            import win32api
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
        round_corners(self.hwnd_mini)
        soft_shadow(self.hwnd_mini)
        if self.start_mini:
            self.show_mini()
        self.hotkey = self._install_hotkey()
        self.start_tray()

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
            _log('show_mini begin')
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
        except Exception:
            pass

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
    def show_main(self, q=None):
        try:
            target = self.url + ('/?q=' + quote(q) if q else '/')
            self.main.show()
            self.main.load_url(target)
            topmost(self.hwnd_main or find_hwnd(MAIN_TITLE))
            self.hwnd_main = self.hwnd_main or find_hwnd(MAIN_TITLE)
        except Exception:
            try:
                webbrowser.open(self.url)
            except Exception:
                pass

    def hide_main(self):
        try:
            self.main.hide()
        except Exception:
            pass

    # -- 钩子 / 托盘 ---------------------------------------------------
    def _install_hotkey(self):
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
                on_main=lambda: self.show_main(),
                on_mini=lambda: self.show_mini(),
                on_quit=self.quit,
                tip='第二大脑（双击 Ctrl 唤出快搜）',
            )
            if not self.tray.start():
                self.tray = None
        except Exception:
            self.tray = None

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
    port = _pick_port()
    if not port:
        print(f'端口 {BASE_PORT}~{BASE_PORT + PORT_TRIES - 1} 都被占用，无法启动。')
        print('请关掉占用端口的程序后重试。')
        return
    url = f'http://127.0.0.1:{port}'
    _start_server(port)
    brain.start_pinyin_backfill()   # 老库补拼音（11 万条十几秒），后台跑，不挡窗口

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

    app = App(url, main_url=target, silent=silent, start_mini=start_mini)
    app.create()
    webview.start(func=app.after_start, debug=False)


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


if __name__ == '__main__':
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
