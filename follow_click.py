# -*- coding: utf-8 -*-
"""第 307 项：在资源管理器里点一下（尤其点空白处），让开着的文件对话框跟过去。

用户原话：「在另存为或者选择文件夹的时候，点击另一个已经打开了文件夹，
选择文件夹这里就自动跳转选择那个文件夹的路径，就不用去找了。」

设计取舍
--------
* 只在**资源管理器文件窗口的内容区**里点才动：工具栏、地址栏、状态栏、
  侧边导航都不算 —— 那些地方天天点，跟着跳会变成骚扰。
* 只动**已经存在的文件对话框**（`quick_switch.find_dialog`），不新建窗口；
  跳转复用 `quick_switch.jump()`，和快搜窗里点「已打开的文件夹」是同一条路。
* 目标目录和对话框当前目录一样就什么都不做（避免每点一下就刷一次）。
* 鼠标钩子回调只做「记下坐标 + 排队」，COM 枚举与 Win32 写对话框全放到
  工作线程 —— 钩子里慢一点，整台机器的鼠标都会顿。
"""
import ctypes
import os
import queue
import threading
import time
from ctypes import wintypes as wt

user32 = ctypes.WinDLL('user32', use_last_error=True) if hasattr(ctypes, 'WinDLL') else None

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
GA_ROOT = 2
EXPLORER_CLASSES = ('CabinetWClass', 'ExploreWClass')
# Win11 的文件区；Win10/旧版是 SHELLDLL_DefView 里的 SysListView32
VIEW_CLASSES = ('DUIViewWndClassName', 'SHELLDLL_DefView')


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('pt', wt.POINT), ('mouseData', wt.DWORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_int), ('top', ctypes.c_int),
                ('right', ctypes.c_int), ('bottom', ctypes.c_int)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

if user32 is not None:
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wt.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.UnhookWindowsHookEx.restype = wt.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), ctypes.c_void_p, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = wt.BOOL
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.WindowFromPoint.argtypes = [wt.POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wt.BOOL
    user32.EnumChildWindows.argtypes = [wt.HWND, ctypes.c_void_p, wt.LPARAM]
    user32.EnumChildWindows.restype = wt.BOOL
    user32.GetParent.argtypes = [wt.HWND]
    user32.GetParent.restype = wt.HWND
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short


def _cls(hwnd):
    try:
        b = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, b, 256)
        return b.value
    except Exception:
        return ''


def in_rect(r, x, y):
    """点是否落在这个矩形里。r 是 (l, t, r, b)。"""
    if not r:
        return False
    l, t, rr, bb = r
    return l <= x < rr and t <= y < bb


def is_explorer_class(cls):
    """只有资源管理器的文件窗口才算；#32770（对话框）与浏览器不算。"""
    return (cls or '') in EXPLORER_CLASSES


def should_jump(path, dlg_dir=''):
    """目标目录与对话框当前目录相同就不折腾。纯函数，便于单测。"""
    raw = str(path or '').strip().strip('"')
    p = os.path.normcase(os.path.normpath(raw))
    d = os.path.normcase(os.path.normpath(str(dlg_dir or '').strip().strip('"')))
    if not p or not os.path.isdir(raw):
        return False
    return p != d


def _children(hwnd):
    kids = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(ch, _):
        kids.append(int(ch))
        return True

    try:
        user32.EnumChildWindows(hwnd, cb, 0)
    except Exception:
        pass
    return kids


def view_rect(hwnd):
    """资源管理器文件区（放文件图标那块）的屏幕矩形；找不到返回 None。

    找 DUIViewWndClassName / SHELLDLL_DefView 的直属结构，不认 DirectUIHWND
    本身 —— Win11 的工具栏也是 DirectUIHWND，按它判断会把点「新建」也当成点文件区。
    """
    for ch in _children(hwnd):
        if _cls(ch) in VIEW_CLASSES:
            r = RECT()
            if user32.GetWindowRect(ch, ctypes.byref(r)):
                return (r.left, r.top, r.right, r.bottom)
    return None


def explorer_at(x, y):
    """(hwnd, path-friendly 标记) —— 这个点是不是落在某个资源管理器文件窗口的文件区里。

    返回顶层窗口 hwnd；不是就返回 0。
    """
    try:
        pt = wt.POINT(int(x), int(y))
        child = user32.WindowFromPoint(pt)
        if not child:
            return 0
        root = int(user32.GetAncestor(child, GA_ROOT) or 0)
        if not root or not is_explorer_class(_cls(root)):
            return 0
        r = view_rect(root)
        if r and in_rect(r, x, y):
            return root
    except Exception:
        pass
    return 0


def modifiers_down():
    """按着 Ctrl/Shift/Alt 的多选点击不跟 —— 那是用户在挑东西。"""
    try:
        for vk in (0x11, 0x10, 0x12):   # Ctrl / Shift / Alt
            if user32.GetAsyncKeyState(vk) & 0x8000:
                return True
    except Exception:
        pass
    return False


class ClickWatcher(object):
    """全局鼠标钩子。命中后把 (hwnd, x, y) 丢进队列，由工作线程去做跳转。"""

    def __init__(self, on_hit, enabled=None, delay=0.25):
        self.on_hit = on_hit            # callback(hwnd) -> None，在工作线程里跑
        self.enabled = enabled or (lambda: True)
        self.delay = delay
        self._q = queue.Queue(maxsize=8)
        self._hook = None
        self._proc = None
        self._ok = False
        self._ready = threading.Event()
        self._stop = False
        self.error = ''

    # -- 钩子 ---------------------------------------------------------
    def _on_mouse(self, nCode, wParam, lParam):
        try:
            if nCode == 0 and wParam == WM_LBUTTONUP and self.enabled() and not modifiers_down():
                # lParam 是内核给的地址，必须先包成 c_void_p 再 cast；
                # 直接 cast(int) 会抛 TypeError，异常被吞掉 → 钩子「装上了但永远不触发」。
                ms = ctypes.cast(ctypes.c_void_p(int(lParam)),
                                 ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                hwnd = explorer_at(ms.pt.x, ms.pt.y)
                if hwnd:
                    try:
                        self._q.put_nowait((hwnd, time.monotonic()))
                    except queue.Full:
                        pass
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _run(self):
        try:
            self._proc = HOOKPROC(self._on_mouse)
            self._hook = user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._proc, ctypes.c_void_p(0), 0)
        except Exception as e:
            self.error = '%s: %s' % (type(e).__name__, e)
            self._ready.set()
            return
        if not self._hook:
            self.error = 'set-hook-failed (%s)' % ctypes.get_last_error()
            self._ready.set()
            return
        self._ok = True
        self._ready.set()
        msg = wt.MSG()
        while not self._stop and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._ok = False

    def _consume(self):
        while not self._stop:
            try:
                hwnd, _t = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            # 等焦点切过去再动：点击瞬间资源管理器还在抢前台，
            # 这时写对话框会被下一次焦点变化打断。
            time.sleep(self.delay)
            try:
                fg = int(user32.GetForegroundWindow() or 0)
                if int(user32.GetAncestor(fg, GA_ROOT) or fg) != hwnd:
                    continue
            except Exception:
                continue
            try:
                self.on_hit(hwnd)
            except Exception:
                import traceback
                traceback.print_exc()

    def start(self):
        if user32 is None:
            self.error = 'not-windows'
            return None
        threading.Thread(target=self._run, daemon=True, name='fc-hook').start()
        self._ready.wait(3.0)
        if not self._ok:
            return None
        threading.Thread(target=self._consume, daemon=True, name='fc-fire').start()
        return self

    def stop(self):
        self._stop = True
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
        self._ok = False


def start(on_hit, enabled=None, delay=0.25):
    w = ClickWatcher(on_hit, enabled=enabled, delay=delay)
    return w.start()
