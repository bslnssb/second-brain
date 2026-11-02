#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tray.py — 任务栏通知区图标（pywin32 实现，约 100 行）

为什么需要它：快搜窗是「双击 Ctrl」唤出的，程序必须常驻后台。常驻却没托盘，
用户把主窗口一关就再也找不到程序，也不知道它还在跑。

托盘是「不顺路也不能崩」的部件：这里所有失败都吞掉并返回 False，
调用方据此退回「关窗即退出」的老行为。
"""
import os
import sys
import threading
import time

WM_TRAY = 0x0400 + 20          # WM_USER + 20
IDI_MAIN = 1001
IDI_MINI = 1002
IDI_QUIT = 1003


def _icon_path():
    """优先用 exe / 脚本旁边的 assets/icon.ico；打包后资源在 _internal 下"""
    cands = []
    base = (os.path.dirname(sys.executable if getattr(sys, 'frozen', False)
                            else os.path.abspath(__file__)))
    cands.append(os.path.join(base, 'assets', 'icon.ico'))
    meipass = getattr(sys, '_MEIPASS', '')
    if meipass:
        cands.append(os.path.join(meipass, 'assets', 'icon.ico'))
    cands.append(os.path.join(base, '_internal', 'assets', 'icon.ico'))
    for p in cands:
        if os.path.isfile(p):
            return p
    return ''


class Tray:
    """start() 成功返回 True；之后 quit() 退出消息循环。

    on_main()  左键单击 / 菜单「详细搜索」
    on_mini()  菜单「快搜」
    on_quit()  菜单「退出」（调用方负责真正结束进程）
    """

    def __init__(self, on_main, on_mini=None, on_quit=None, tip='第二大脑'):
        self.on_main = on_main
        self.on_mini = on_mini
        self.on_quit = on_quit
        self.tip = tip
        self.ok = False
        self._hwnd = None
        self._thread = None
        self._ready = threading.Event()

    def start(self, timeout=3.0):
        try:
            import win32api, win32con, win32gui      # noqa: F401
        except Exception:
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name='tray')
        self._thread.start()
        self._ready.wait(timeout)
        return self.ok

    def _run(self):
        import win32api
        import win32con
        import win32gui

        hinst = win32api.GetModuleHandle(None)
        msg_taskbar = win32api.RegisterWindowMessage('TaskbarCreated')
        icon_file = _icon_path()
        self._hicon = None
        if icon_file:
            try:
                self._hicon = win32gui.LoadImage(
                    0, icon_file, win32con.IMAGE_ICON, 0, 0,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
            except Exception:
                self._hicon = None
        if not self._hicon:
            self._hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

        def add_icon():
            nid = (self._hwnd, 0,
                   win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                   WM_TRAY, self._hicon, self.tip)
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

        def on_menu(cmd):
            try:
                if cmd == IDI_MAIN and self.on_main:
                    self.on_main()
                elif cmd == IDI_MINI and self.on_mini:
                    self.on_mini()
                elif cmd == IDI_QUIT and self.on_quit:
                    self.on_quit()
            except Exception:
                pass

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                if lparam == win32con.WM_LBUTTONUP:
                    on_menu(IDI_MAIN)
                elif lparam == win32con.WM_RBUTTONUP:
                    menu = win32gui.CreatePopupMenu()
                    win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_MAIN, '打开详细搜索')
                    win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_MINI, '快搜窗口')
                    win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, None)
                    win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_QUIT, '退出')
                    x, y = win32gui.GetCursorPos()
                    # 不先把窗口提到前台，菜单会点不掉（Win32 老毛病）
                    win32gui.SetForegroundWindow(hwnd)
                    cmd = win32gui.TrackPopupMenu(
                        menu, win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON,
                        x, y, 0, hwnd, None)
                    win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
                    win32gui.DestroyMenu(menu)
                    if cmd:
                        on_menu(cmd)
                return 0
            if msg == msg_taskbar:
                try:
                    add_icon()          # 资源管理器重启后图标会消失，得补挂
                except Exception:
                    pass
                return 0
            if msg == win32con.WM_DESTROY:
                win32gui.PostQuitMessage(0)
                return 0
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        try:
            wc = win32gui.WNDCLASS()
            wc.hInstance = hinst
            wc.lpszClassName = 'SecondBrainTrayWnd'
            wc.lpfnWndProc = wndproc
            atom = win32gui.RegisterClass(wc)
            self._hwnd = win32gui.CreateWindow(atom, self.tip, 0, 0, 0, 0, 0, 0, 0, hinst, None)
            add_icon()
            self.ok = True
        except Exception:
            self.ok = False
            self._ready.set()
            return
        self._ready.set()
        win32gui.PumpMessages()
        try:
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_DELETE, (self._hwnd, 0, 0, 0, None, ''))
        except Exception:
            pass

    def quit(self):
        try:
            import win32con
            import win32gui
            if self._hwnd:
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
