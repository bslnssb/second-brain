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
IDI_SETTINGS = 1004
IDI_ABOUT = 1005
IDI_FEEDBACK = 1006
IDI_HELP = 1007

_DEBUG = bool(os.environ.get('SB_DEBUG'))



# 托盘右键菜单的文案：以前硬编码中文，用户把界面切成英文后菜单还是中文。
# 这里按 brain.boot_lang() 现取，每次弹菜单时重新算（改语言后下一次右键就变）。
TRAY_L = {
    'zh': {'main': '打开详细搜索', 'mini': '快搜窗口（双击 Ctrl）', 'settings': '设置…',
           'about': '关于第二大脑', 'help': '使用帮助', 'feedback': '反馈 / 报错', 'quit': '退出'},
    'en': {'main': 'Open the search window', 'mini': 'Quick search (double Ctrl)', 'settings': 'Settings…',
           'about': 'About', 'help': 'Help', 'feedback': 'Feedback / report a bug', 'quit': 'Quit'},
    'es': {'main': 'Abrir la ventana de búsqueda', 'mini': 'Búsqueda rápida (doble Ctrl)', 'settings': 'Ajustes…',
           'about': 'Acerca de', 'help': 'Ayuda', 'feedback': 'Comentarios / informar error', 'quit': 'Salir'},
    'pt': {'main': 'Abrir a janela de busca', 'mini': 'Busca rápida (Ctrl duplo)', 'settings': 'Configurações…',
           'about': 'Sobre', 'help': 'Ajuda', 'feedback': 'Comentários / relatar erro', 'quit': 'Sair'},
    'hi': {'main': 'खोज विंडो खोलें', 'mini': 'क्विक खोज (डबल Ctrl)', 'settings': 'सेटिंग्स…',
           'about': 'परिचय', 'help': 'सहायता', 'feedback': 'प्रतिक्रिया / बग रिपोर्ट', 'quit': 'बाहर निकलें'},
}


def tray_labels():
    """当前界面对应的托盘菜单文案；拿不到语言就退回中文。"""
    lang = 'zh'
    try:
        import brain
        lang = brain.boot_lang() or 'zh'
    except Exception:
        pass
    return TRAY_L.get(lang, TRAY_L['zh'])


def _tlog(msg):
    """托盘消息循环在自己的线程里，print 会被吞；和 app_window 共用 debug.log。"""
    if not _DEBUG:
        return
    line = f'[{time.strftime("%H:%M:%S")}.{int(time.time() * 1000) % 1000:03d}] [tray] {msg}'
    try:
        # 写到和 app_window 同目录的 debug.log
        base = (os.path.dirname(sys.executable if getattr(sys, 'frozen', False)
                                else os.path.abspath(__file__)))
        with open(os.path.join(base, 'debug.log'), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        try:
            print(line, flush=True)
        except Exception:
            pass


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

    on_main()      左键单击 / 菜单「详细搜索」
    on_mini()      菜单「快搜窗口（双击 Ctrl）」
    on_settings()  菜单「设置…」
    on_about()     菜单「关于」
    on_feedback()  菜单「反馈」
    on_help()      菜单「使用帮助」
    on_quit()      菜单「退出」（调用方负责真正结束进程）
    """

    def __init__(self, on_main, on_mini=None, on_quit=None, tip='第二大脑',
                 on_settings=None, on_about=None, on_feedback=None, on_help=None):
        self.on_main = on_main
        self.on_mini = on_mini
        self.on_quit = on_quit
        self.on_settings = on_settings
        self.on_about = on_about
        self.on_feedback = on_feedback
        self.on_help = on_help
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
                elif cmd == IDI_SETTINGS and self.on_settings:
                    self.on_settings()
                elif cmd == IDI_ABOUT and self.on_about:
                    self.on_about()
                elif cmd == IDI_HELP and self.on_help:
                    self.on_help()
                elif cmd == IDI_FEEDBACK and self.on_feedback:
                    self.on_feedback()
                elif cmd == IDI_QUIT and self.on_quit:
                    self.on_quit()
            except Exception:
                pass

        def _force_foreground(hwnd):
            """把指定 hwnd 提到前台。

            TrackPopupMenu 有个 Win32 老毛病：调用线程若不在前台，菜单弹出后会立刻
            消失（看上去就是「什么都没有」）。标准修法是先 SetForegroundWindow + 弹完后
            PostMessage(WM_NULL)，但对**隐藏的 0×0 消息窗口**调用 SetForegroundWindow
            经常被前台锁定规则拒掉（返回 True 但实际没切）。

            所以这里走两步：
            1) 先模拟一次 Alt 键按下/抬起 —— 系统看到本线程刚发过用户输入，
               就放行 SetForegroundWindow（Wox / PowerToys Run 都用这招）；
            2) 再 SetForegroundWindow，并验证 GetForegroundWindow 是不是自己。
            任一步失败都记日志，便于诊断。"""
            try:
                import win32api
                import win32con
                import win32gui
                # Alt 解锁
                try:
                    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                except Exception as e:
                    _tlog(f'force_fg alt FAILED: {e}')
                ok = win32gui.SetForegroundWindow(hwnd)
                fg = win32gui.GetForegroundWindow()
                _tlog(f'force_fg SetFg={ok} fg_now={fg} hwnd={hwnd}')
                return ok
            except Exception as e:
                _tlog(f'force_fg EXCEPTION: {e}')
                return False

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                if lparam == win32con.WM_LBUTTONUP:
                    _tlog('LBUTTONUP → on_main')
                    threading.Thread(target=on_menu, args=(IDI_MAIN,), daemon=True).start()
                elif lparam == win32con.WM_RBUTTONUP:
                    _tlog('RBUTTONUP → building menu')
                    try:
                        menu = win32gui.CreatePopupMenu()
                        L = tray_labels()
                        win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_MAIN, L['main'])
                        # 菜单里写上唤出方式：很多人是"想退出时"才右键托盘的，
                        # 顺手在这里看见「双击 Ctrl」，等于把唯一一个隐形用法摆到眼前。
                        win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_MINI, L['mini'])
                        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, '')
                        win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_SETTINGS, L['settings'])
                        win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_ABOUT, L['about'])
                        win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_HELP, L['help'])
                        win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_FEEDBACK, L['feedback'])
                        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, '')
                        win32gui.AppendMenu(menu, win32con.MF_STRING, IDI_QUIT, L['quit'])
                        x, y = win32gui.GetCursorPos()
                        # 隐藏的 tray hwnd 调 SetForegroundWindow 经常被前台锁定规则拒掉，
                        # 菜单就会"闪一下就消失"——用 Alt 解锁 + 强制前台来兜住。
                        _force_foreground(hwnd)
                        cmd = win32gui.TrackPopupMenu(
                            menu, win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON |
                            win32con.TPM_RETURNCMD,
                            x, y, 0, hwnd, None)
                        win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
                        win32gui.DestroyMenu(menu)
                        _tlog(f'TrackPopupMenu cmd={cmd}')
                        if cmd:
                            # 关键：菜单回调必须**立刻返回**，否则托盘消息循环被占住，
                            # 下一次右键会像卡死一样（用户实测：点了帮助，托盘僵了好一会儿）。
                            # 真正干活（show 主窗口 + evaluate_js）丢到后台线程。
                            t = threading.Thread(target=on_menu, args=(cmd,), daemon=True)
                            t.start()
                    except Exception as e:
                        _tlog(f'RBUTTONUP EXCEPTION: {e}')
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
        """退出时先同步删托盘图标，再 PostMessage(WM_CLOSE) 让消息循环收尾。

        如果只 PostMessage(WM_CLOSE) 然后调用方立刻 os._exit(0)，托盘线程来不及
        处理 WM_DESTROY 里的 Shell_NotifyIcon(NIM_DELETE)，图标就会变成「幽灵」——
        要等系统回收才消失，用户看到的就是「点了退出，图标还挂着两秒才没」。
        先在当前线程同步删掉图标，用户立刻看到图标消失，体感是秒退。"""
        try:
            import win32con
            import win32gui
            if self._hwnd:
                try:                     # 先同步删图标，不等消息循环
                    win32gui.Shell_NotifyIcon(
                        win32gui.NIM_DELETE, (self._hwnd, 0, 0, 0, None, ''))
                except Exception:
                    pass
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
