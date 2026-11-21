# -*- coding: utf-8 -*-
"""第 197 项：在资源管理器里「直接打字」就能搜。

背景：用户打开一个文件夹，想找东西时得先点一下右上角的搜索框、或者干脆
一层层翻。Listary 那种体验是「直接把键盘当搜索框」——手不离键，打字即搜。

这里挂一个 WH_KEYBOARD_LL 低级键盘钩子，只在**同时满足**下面几条时才动手：

  1. 设置了「文件夹里直接打字」是开的；
  2. 前台是资源管理器文件夹窗口（CabinetWClass / ExploreWClass）或标准文件
     对话框（#32770）——别的程序里打字一律放过；
  3. 当前焦点**不在**输入框上（地址栏 / 搜索框 / 重命名框都是 Edit 一类）。
     否则用户是在正常输入，抢他的键属于破坏；
  4. 没有按 Ctrl / Alt / Win（那是快捷键，不是打字）。

命中后吞掉这个键，把「开头这几个字」暂存起来，回调 App 去弹快搜窗；
App 把焦点抢过去之后调 release() 拿回缓冲区，再用 replay() 把这几个键
注入到**我们自己**的窗口里。这样拼音输入法也能正常从第一个字母开始拼字——
如果只把第一个字母丢给 JS 填进搜索框，IME 从第二个字母才开始组词，
「zhongjian」会变成字面上的 z + 中间。

注入的键带 dwExtraInfo 标记，钩子认出来直接放过，不会自激。
不能在钩子里做这些事：低级键盘钩子必须在 LowLevelHooksTimeout（默认 300ms）
内返回，超时会被系统静默摘掉。所以钩子里只做几次很快的 Win32 查询 + 入队。
"""
from __future__ import annotations

import plat.win  # noqa: F401  ← 第 382 项：非 Windows 上给 ctypes 补 Win32 占位，
# 让模块「能 import」；功能本身仍要靠 P3~P5 的真实现，现在调用等于空转。
import ctypes
import ctypes.wintypes as wt
import queue
import threading
import time

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
HC_ACTION = 0

VK_BACK, VK_TAB, VK_RETURN, VK_ESCAPE, VK_SPACE = 0x08, 0x09, 0x0D, 0x1B, 0x20
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
KEYEVENTF_KEYUP = 0x0002

# 注入标记：钩子看到这个 dwExtraInfo 就知道「这是自己刚吐出来的」，必须放过
TAG = 0x53425A31          # 'SBZ1'

FOLDER_CLASSES = ('CabinetWClass', 'ExploreWClass', '#32770')
TEXT_CLASSES = ('Edit', 'ComboBox', 'ComboBoxEx32', 'RichEdit20W', 'RichEdit50W',
                'SearchBox', 'RICHEDIT50W')


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('flags', wt.DWORD),
                ('hwndActive', wt.HWND), ('hwndFocus', wt.HWND),
                ('hwndCapture', wt.HWND), ('hwndMenuOwner', wt.HWND),
                ('hwndMoveSize', wt.HWND), ('hwndCaret', wt.HWND),
                ('rcCaret', wt.RECT)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

if hasattr(ctypes, 'WinDLL'):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wt.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.UnhookWindowsHookEx.restype = wt.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), ctypes.c_void_p, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = wt.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(GUITHREADINFO)]
    user32.GetGUIThreadInfo.restype = wt.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
    user32.keybd_event.restype = None
    user32.GetKeyboardLayout.argtypes = [wt.DWORD]
    user32.GetKeyboardLayout.restype = ctypes.c_void_p
    user32.ToUnicodeEx.argtypes = [wt.UINT, wt.UINT, ctypes.POINTER(wt.BYTE),
                                   wt.LPWSTR, ctypes.c_int, wt.UINT, ctypes.c_void_p]
    user32.ToUnicodeEx.restype = ctypes.c_int
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
else:                                    # 非 Windows：导入不报错，start() 返回 None
    user32 = kernel32 = None


# ------------------------------------------------------------------ 判定（可单测）
def is_printable(vk):
    """能不能是一个字的开头：字母 / 数字 / 常见符号。

    空格、退格单独算「输入过程中的键」（见 is_typing_key）：空格在 Explorer 里是
    预览快捷键，但当它是「my report」中间那个空格时又不能漏，所以在缓冲期才收。
    """
    if 0x30 <= vk <= 0x39:               # 0-9
        return True
    if 0x41 <= vk <= 0x5A:               # A-Z
        return True
    if vk in (0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE, 0xE2):
        return True                      # ; = , - . / ` [ \ ] ' 以及 OEM102
    return False


def is_typing_key(vk):
    return is_printable(vk) or vk in (VK_SPACE, VK_BACK)


def is_text_class(cls):
    """焦点在输入框里时不能抢。地址栏 / 搜索框 / 重命名框都是 Edit 一类。"""
    cls = cls or ''
    if cls in TEXT_CLASSES:
        return True
    return 'Edit' in cls or 'EDIT' in cls


def should_capture(vk, ctrl, alt, win, fg_class, focus_class, active,
                   focus_ratio=1.0, has_caret=False):
    """钩子的完整判定。抽成纯函数，单测直接打表。

    第 198 项补的两条，专治「想用 Windows 自带搜索却被劫持」：
    Win11 资源管理器的搜索框是 DirectUI 自绘的，**没有 Edit 子窗口**，焦点
    落在那一小条 DirectUIHWND 上，光看类名根本拦不住。所以再加：

      - focus_ratio：焦点控件面积 / 窗口面积。实测 Win11 资源管理器的
        搜索区是窗口右侧一条 264×531 的 DirectUIHWND（占比 0.25），文件列表
        是整窗那块（占比 1.0）。所以阈值取 0.42：小条一律不抢，大块才抢。
      - has_caret：有输入光标就说明正在文本里编辑，放过。
      - 拿不到焦点控件（hwndFocus=0）时按 ratio=0 处理，同样不抢 ——
        抢错的代价是把用户正在打的字弄丢，比漏抢一次严重得多。
    """
    if not active:
        return False
    if fg_class not in FOLDER_CLASSES:
        return False
    if is_text_class(focus_class):
        return False
    if ctrl or alt or win:
        return False
    if has_caret:
        return False
    if focus_ratio < 0.42:
        return False
    return is_printable(vk)


def _down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class TypeWatch:
    """常驻低级键盘钩子：文件夹里打字 -> on_type({'hwnd':..., 'n': 键数})。

    on_type 在**另一个线程**里执行（钩子线程不能等窗口显示），它应该：
      1. 弹快搜窗（带 scope）；
      2. 焦点确认后调 release() 拿回按键，再 replay()。
    如果 on_type 里出错没调 release，2 秒后自动放行，绝不把用户键盘卡住。
    """

    def __init__(self, on_type, is_active, hold_ms=2000, is_dialog=None):
        self.on_type = on_type
        self.is_active = is_active
        # 第 200 项：前台是 #32770 时，还要确认它真是「打开/另存为」对话框。
        # 下载器、播放器的业务对话框也是 #32770，在里面敲字不该被我们抢走。
        self.is_dialog = is_dialog
        self.hold_s = max(0.2, hold_ms / 1000.0)
        self._q = queue.SimpleQueue()
        self._hook = None
        self._proc = None
        self._lock = threading.Lock()
        self._buf = []
        self._hold_until = 0.0
        self._captured_hwnd = 0
        self._consumed = 0
        self.ok = False
        self.error = ''

    # -- 钩子侧（必须极快） -------------------------------------------
    def _on_key(self, nCode, wParam, lParam):
        try:
            if nCode != HC_ACTION:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if kb.dwExtraInfo == TAG:                  # 自己注入的，放过
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            up = wParam in (WM_KEYUP, WM_SYSKEYUP)
            if not (down or up):
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            holding = time.monotonic() < self._hold_until
            if holding and self._buf:
                # QA BUG-06：缓冲期内若修饰键正按着，整个组合一律放行。
                # 否则 Ctrl+A 的 Ctrl 被放过、字母 A 却被吞进快搜窗，
                # 用户原本想全选，结果原窗口快捷键失效。
                if (_down(VK_CONTROL) or _down(VK_MENU)
                        or _down(VK_LWIN) or _down(VK_RWIN)):
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)
                # 缓冲期：打字过程中的键继续吃掉，别漏进 Explorer 触发它自己的
                # 首字母跳转（否则会顺手选中一个文件）。
                if down and is_typing_key(kb.vkCode):
                    self._buf.append((kb.vkCode, kb.scanCode))
                    return 1
                if kb.vkCode == VK_ESCAPE and down:
                    self._buf = []
                    self._hold_until = 0.0
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            if not down:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            if not is_printable(kb.vkCode):
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            if not self.is_active():
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            fg = user32.GetForegroundWindow()
            fg_cls = _cls(fg)
            if fg_cls not in FOLDER_CLASSES:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            if fg_cls == '#32770' and self.is_dialog:
                # 普通业务对话框（下载器/播放器）不是文件对话框，别抢它的键。
                try:
                    if not self.is_dialog(fg):
                        return user32.CallNextHookEx(None, nCode, wParam, lParam)
                except Exception:
                    pass
            foc_cls, ratio, caret = _focus_state(fg)
            if not should_capture(kb.vkCode, _down(VK_CONTROL), _down(VK_MENU),
                                  _down(VK_LWIN) or _down(VK_RWIN), fg_cls, foc_cls, True,
                                  ratio, caret):
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            self._captured_hwnd = int(fg or 0)
            self._buf = [(kb.vkCode, kb.scanCode)]
            self._hold_until = time.monotonic() + self.hold_s
            self._q.put(int(fg or 0))
            return 1                                   # 吃掉，Explorer 不再自己跳
        except Exception:
            pass                                       # 钩子里绝不能抛
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    # -- 线程 ---------------------------------------------------------
    def _run(self):
        try:
            self._proc = HOOKPROC(self._on_key)
            self._hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        except Exception as e:
            self.error = '%s: %s' % (type(e).__name__, e)
            self._ready.set()
            return
        if not self._hook:
            self.error = 'set-hook-failed (%s)' % ctypes.get_last_error()
            self._ready.set()
            return
        self.ok = True
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.ok = False

    def _consume(self):
        while True:
            hwnd = self._q.get()
            try:
                self.on_type({'hwnd': hwnd, 'watch': self})
            except Exception:
                import traceback
                traceback.print_exc()
                self._hold_until = 0.0

    def start(self):
        if user32 is None:
            self.error = 'not-windows'
            return None
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name='tw-hook').start()
        self._ready.wait(3.0)
        if not self.ok:
            return None
        threading.Thread(target=self._consume, daemon=True, name='tw-fire').start()
        return self

    def stop(self):
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
        self.ok = False
        self._buf = []

    # -- 给 App 用 ----------------------------------------------------
    def release(self):
        """结束缓冲，返回这段时间吞掉的按键 [(vk, scan), ...]。

        调用方要保证「窗口已经拿到焦点」再 replay，否则这些字会被注入到
        Explorer 里，用户看到的是文件被选中，而不是搜索框里有字。"""
        with self._lock:
            keys = list(self._buf)
            self._buf = []
            self._hold_until = 0.0
        return keys

    @property
    def captured_hwnd(self):
        return self._captured_hwnd


def _cls(hwnd):
    try:
        b = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, b, 256)
        return b.value
    except Exception:
        return ''


def _focus_state(fg):
    """返回 (焦点控件类名, 焦点面积占比, 有没有输入光标)。

    GetFocus 只能看自己线程，必须走 GetGUIThreadInfo。拿不到就返回
    ('', 0.0, False) —— 调用方会当成「不抢」，宁可漏也不误抢。
    """
    try:
        tid = user32.GetWindowThreadProcessId(fg, None)
        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            return '', 0.0, False
        fh = int(gti.hwndFocus or 0)
        if not fh:
            return '', 0.0, bool(gti.hwndCaret)
        cls = _cls(fh)
        caret = bool(gti.hwndCaret)
        fa = _area(fh)
        wa = _area(fg)
        ratio = (fa / wa) if (fa and wa) else 0.0
        return cls, ratio, caret
    except Exception:
        return '', 0.0, False


def _area(hwnd):
    try:
        r = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return 0
        w = int(r.right) - int(r.left)
        h = int(r.bottom) - int(r.top)
        return max(0, w) * max(0, h)
    except Exception:
        return 0


def replay(keys):
    """把按键注入到当前焦点窗口（调用前必须确认焦点是我们的快搜窗）。"""
    if user32 is None or not keys:
        return 0
    n = 0
    for vk, scan in keys:
        try:
            user32.keybd_event(vk, scan, 0, TAG)
            user32.keybd_event(vk, scan, KEYEVENTF_KEYUP, TAG)
            n += 1
            time.sleep(0.008)          # 太密会被 WebView2 合并，漏字
        except Exception:
            break
    return n


def keys_to_text(keys):
    """兜底：焦点抢不过来时，把 ASCII 按键还原成文本，用 JS 填进搜索框。

    只覆盖英文/数字/常见符号；中文靠 IME，还原不出来（这也是主路径选注入的原因）。
    """
    if user32 is None or not keys:
        return ''
    out = []
    kl = user32.GetKeyboardLayout(0)
    state = (wt.BYTE * 256)()
    for vk, scan in keys:
        try:
            buf = ctypes.create_unicode_buffer(8)
            n = user32.ToUnicodeEx(vk, scan, state, buf, 8, 0, kl)
            if n == 1 and buf.value.isprintable():
                out.append(buf.value)
        except Exception:
            pass
    return ''.join(out)


def start(on_type, is_active, hold_ms=2000, is_dialog=None):
    return TypeWatch(on_type, is_active, hold_ms=hold_ms, is_dialog=is_dialog).start()
