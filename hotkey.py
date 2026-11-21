#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hotkey.py — 全局「双击 Ctrl」热键（纯 ctypes，不引第三方库）

为什么不用 RegisterHotKey：那个 API 只认「修饰键 + 主键」的固定组合，
双击同一个键不在它的表达能力里。所以这里挂 WH_KEYBOARD_LL 低级键盘钩子，
自己判断「两次干净的 Ctrl 抬起」。

代价是钩子必须在 LowLevelHooksTimeout（默认 300ms）内返回，否则系统会把钩子
静默摘掉——所以回调里只做时间戳比较，真正的活儿（显示窗口）丢进队列由另一个线程做。

「干净」的定义：从按下 Ctrl 到抬起之间，没有按过任何别的键。
否则 Ctrl+C、Ctrl+V 之后紧跟一次单击 Ctrl 就会被误判成双击。
打字打断时把上一次的计时清零，这样「Ctrl、Ctrl+C、Ctrl」不会连成一次双击。
"""
import ctypes
import ctypes.wintypes as wt
import queue
import threading
import time

# 第 277 项：热键埋点。低级键盘钩子被杀软拦掉时只会静默 return None。
try:
    import feature_metrics as _metrics
except Exception:                                     # pragma: no cover
    _metrics = None


def _hk(status, mode=None, err=''):
    if _metrics is None:
        return
    try:
        _metrics.add('hotkey', status, sub=str(mode or '')[:12], err=str(err)[:120])
    except Exception:
        pass

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
VK_CONTROL, VK_LCONTROL, VK_RCONTROL = 0x11, 0xA2, 0xA3
VK_SPACE = 0x20
VK_MENU = 0x12
HC_ACTION = 0


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

if hasattr(ctypes, 'WinDLL'):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    # 64 位下 HHOOK / HMODULE 是指针：不声明 restype 会被截成 int32，
    # 后面 UnhookWindowsHookEx 拿到一个坏句柄，钩子就永远留在系统里。
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
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
else:                                    # 非 Windows：导入即可，start() 会直接返回 None
    user32 = kernel32 = None


class GlobalHotkey:
    """启动后常驻：全局热键 → callback()。

    支持三种唤出模式：
    - 'double_ctrl': 双击 Ctrl（默认）
    - 'alt_space': Alt + Space（拦截系统菜单）
    - 'ctrl_space': Ctrl + Space
    """

    def __init__(self, callback, mode='double_ctrl', gap=0.5, cooldown=0.35):
        self.callback = callback
        self.mode = mode or 'double_ctrl'
        self.gap = gap
        self.cooldown = cooldown
        self._q = queue.SimpleQueue()
        self._hook = None
        self._proc = None                  # 必须留引用：回调对象被回收后系统会崩
        self._state = {'ctrl': False, 'dirty': False, 'last': 0.0, 'fired': 0.0}
        self.ok = False
        self.error = ''

    def set_mode(self, mode):
        self.mode = mode or 'double_ctrl'
        self._state = {'ctrl': False, 'dirty': False, 'last': 0.0, 'fired': 0.0}

    # ---- 钩子回调（必须在 1ms 级返回） ----
    def _on_key(self, nCode, wParam, lParam):
        try:
            if nCode == HC_ACTION:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = kb.vkCode
                st = self._state
                now = time.monotonic()
                mode = self.mode

                if mode == 'alt_space':
                    # Alt+Space 检测
                    if vk == VK_SPACE and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        alt_down = bool(kb.flags & 0x20) or bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
                        if alt_down:
                            if now - st['fired'] > self.cooldown:
                                st['fired'] = now
                                self._q.put(1)
                            return 1  # 拦截 Alt+Space，防止触发系统窗口菜单

                elif mode == 'ctrl_space':
                    # Ctrl+Space 检测
                    if vk == VK_SPACE and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                        if ctrl_down:
                            if now - st['fired'] > self.cooldown:
                                st['fired'] = now
                                self._q.put(1)
                            return 1  # 拦截 Ctrl+Space

                else:
                    # 默认 'double_ctrl'
                    if vk in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL):
                        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                            if not st['ctrl']:
                                st['dirty'] = False
                            st['ctrl'] = True
                        elif wParam in (WM_KEYUP, WM_SYSKEYUP) and st['ctrl']:
                            st['ctrl'] = False
                            if st['dirty']:
                                st['last'] = 0.0          # 中间打过字，这一对不算
                            else:
                                if 0 < now - st['last'] <= self.gap:
                                    st['last'] = 0.0
                                    if now - st['fired'] > self.cooldown:
                                        st['fired'] = now
                                        self._q.put(1)
                                else:
                                    st['last'] = now
                    elif wParam in (WM_KEYDOWN, WM_SYSKEYDOWN) and st['ctrl']:
                        st['dirty'] = True
        except Exception:
            pass                          # 钩子里绝不能抛异常，否则整条输入链都会受影响
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _run(self):
        """安装 + 泵消息必须在**同一个长期存活的线程**里。

        低级键盘钩子的回调由系统投递到「安装钩子的那个线程」的消息队列；
        安装线程一退出，钩子就静默失效（SetWindowsHookEx 当时仍然返回成功，
        所以要过一会儿按键没反应才发现）。最初把安装放在调用者线程、消息泵放子线程，
        在命令行里跑得好好的，一进 GUI 程序（安装线程是 pywebview 的一次性回调线程）
        就再也收不到事件。"""
        try:
            self._proc = HOOKPROC(self._on_key)
            self._hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        except Exception as e:
            self.error = f'{type(e).__name__}: {e}'
            self._ready.set()
            return
        if not self._hook:
            self.error = f'set-hook-failed ({ctypes.get_last_error()})'
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
            self._q.get()
            try:
                self.callback()
            except Exception:
                import traceback
                traceback.print_exc()

    def start(self):
        if user32 is None:
            self.error = 'not-windows'
            _hk('fail', self.mode, 'not-windows')
            return None
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name='hk-hook').start()
        self._ready.wait(3.0)
        if not self.ok:
            # 第 277 项：装不上低级键盘钩子（被杀软拦、别的程序抢先）是静默失败，
            # 用户只会觉得「双击 Ctrl 没反应」。
            _hk('fail', self.mode, self.error or 'hook-failed')
            return None
        threading.Thread(target=self._consume, daemon=True, name='hk-fire').start()
        _hk('ok', self.mode)
        return self

    def stop(self):
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
        self.ok = False


DoubleCtrl = GlobalHotkey


def start_hotkey(callback, mode='double_ctrl', gap=0.5):
    """便捷入口：装好全局热键并返回句柄；装不上返回 None（调用方忽略即可，不影响其他功能）。"""
    return GlobalHotkey(callback, mode=mode, gap=gap).start()


def start_double_ctrl(callback, gap=0.5):
    """向下兼容旧接口。"""
    return start_hotkey(callback, mode='double_ctrl', gap=gap)
