# -*- coding: utf-8 -*-
"""第 194 项：把目录「送进」当前正在使用的打开/保存对话框。

参考的是 Listary 官方《快速切换》演示里交互思路（不是代码、不是资源）：
用户在 Word / 记事本的「另存为」里一层层找目录很慢，正常的搜索工具只能
「打开新窗口」，而用户真正想要的是**当前这个对话框原地换到目标目录**。

这里只做三件事，全部是本机 Win32 消息，不注入 DLL、不挂钩全局键盘、不读写
别的进程内存、不碰任何竞品二进制：

  1. open_folders()  —— 枚举本机已经打开的资源管理器窗口，拿到它们的目录。
  2. has_dialog()    —— 判断有没有可见的标准文件对话框（#32770）。
  3. jump(path)      —— 把目录写进那个对话框的文件名输入框并触发默认按钮，
                        Windows 自己会把地址栏切过去（输入以反斜杠结尾的路径
                        是文件对话框的标准导航行为，不会真的保存文件）。

安全边界在 brain.py 的 /api/dialog_jump：路径必须是已存在的目录，且要么在
「当前打开的文件夹」里、要么是本程序刚搜出来并登记过白名单的路径。
"""
from __future__ import annotations

import os
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

# Win32 常量独立写一份：brain.py 跑的是 HTTP 线程，别为了几个常量去 import
# win32con 之类的大包（打包体积和启动时间都敏感）。
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_COMMAND = 0x0111
BM_CLICK = 0x00F5
IDOK = 1
# 第 229 项：给文件名框补一对回车键（PostMessage 消息级，只进目标窗口的消息队列，
# 不经过任何全局钩子）。文件夹选择对话框里「填 目录\ + 回车」= 原地导航进该目录。
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_RETURN = 0x0D
FILE_DIALOG_CLASS = '#32770'
FOLDER_CLASSES = ('CabinetWClass', 'ExploreWClass')
FILENAME_EDIT_ID = 0x047C  # 1148：标准文件对话框的「文件名」输入框
# 第 200 项：Shell 公共对话框里那个「文件列表」宿主的窗口类。业务对话框（下载器、
# 播放器的自定义窗口）不会创建它，用来把二者分开。
SHELL_VIEW_CLASSES = ('DUIViewWndClassName', 'SHELLDLL_DefView')

# 测试注入点：默认 None = 用真 Win32；set_backend() 换成假实现跑单测。
_BACKEND = None

_MU32 = None


def _msg_u32():
    """单独一个 user32 句柄：_RealBackend 把 SendMessageW 的最后一参声明成了
    LPCWSTR（写文本用），读文本要传缓冲区指针，两种签名不能共用同一个函数对象。"""
    global _MU32
    if _MU32 is None:
        import ctypes
        from ctypes import wintypes
        _MU32 = ctypes.WinDLL('user32', use_last_error=True)
        _MU32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, ctypes.c_void_p]
        _MU32.SendMessageW.restype = ctypes.c_ssize_t
    return _MU32


def set_backend(obj):
    """仅给单元测试用。传 None 恢复真 Win32。"""
    global _BACKEND
    _BACKEND = obj


class _RealBackend:
    """把 win32 调用收在一个小对象里，单测可以直接替换。"""

    def __init__(self):
        import ctypes
        import win32con
        import win32gui
        import win32process
        self.gui = win32gui
        self.con = win32con
        self.proc = win32process
        self._ct = ctypes
        self._u32 = ctypes.windll.user32
        from ctypes import wintypes
        self._u32.SendMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR]
        self._u32.SendMessageW.restype = ctypes.c_long

    def enum_windows(self, cb):
        return self.gui.EnumWindows(cb, None)

    def enum_children(self, hwnd, cb):
        return self.gui.EnumChildWindows(hwnd, cb, None)

    def class_name(self, hwnd):
        return self.gui.GetClassName(hwnd)

    def title(self, hwnd):
        return self.gui.GetWindowText(hwnd)

    def is_window(self, hwnd):
        # IsWindow 比 IsWindowVisible 更狠：窗口一销毁就 False，
        # 用来清掉 Shell 集合里「已经关掉但还挂着」的残留项（第 199 项）。
        return bool(self.gui.IsWindow(hwnd))

    def visible(self, hwnd):
        return bool(self.gui.IsWindowVisible(hwnd))

    def enabled(self, hwnd):
        return bool(self.gui.IsWindowEnabled(hwnd))

    def parent(self, hwnd):
        return self.gui.GetParent(hwnd)

    def ctrl_id(self, hwnd):
        return self.gui.GetDlgCtrlID(hwnd)

    def pid(self, hwnd):
        return self.proc.GetWindowThreadProcessId(hwnd)[1]

    def dlg_item(self, hwnd, item_id):
        return self.gui.GetDlgItem(hwnd, item_id)

    def set_text(self, edit, text):
        self._u32.SendMessageW(edit, WM_SETTEXT, 0, text)

    def click(self, btn):
        self.gui.SendMessage(btn, BM_CLICK, 0, 0)

    def command(self, hwnd, cmd):
        self.gui.PostMessage(hwnd, WM_COMMAND, cmd, 0)

    def key_enter(self, edit):
        """第 229 项：对编辑框 Post 一对 WM_KEYDOWN/WM_KEYUP VK_RETURN。
        探针实测（tmp/probe_dialog_nav_20261114.py）这是唯一能让文件夹选择
        对话框**原地导航**且不关窗的消息路径。"""
        self.gui.PostMessage(edit, WM_KEYDOWN, VK_RETURN, 1)
        self.gui.PostMessage(edit, WM_KEYUP, VK_RETURN, 0xC0000001)

    def foreground(self, hwnd):
        self.gui.SetForegroundWindow(hwnd)

    # -- 第 197 项：定位「这个窗口现在在看哪个目录」 -------------------
    def fg_hwnd(self):
        return self.gui.GetForegroundWindow()

    def is_child(self, parent, child):
        return bool(self._ct.windll.user32.IsChild(parent, child))

    def get_text(self, hwnd, n=1024):
        b = self._ct.create_unicode_buffer(n)
        _msg_u32().SendMessageW(hwnd, WM_GETTEXT, n, self._ct.cast(b, self._ct.c_void_p))
        return b.value


_B = None


def _backend():
    global _B
    if _BACKEND is not None:
        return _BACKEND
    if _B is None:
        _B = _RealBackend()
    return _B


# ------------------------------------------------------------------ 找对话框
def _top_dialogs():
    """按 Z 序（顶→底）返回可见的 #32770 顶层窗口，排除本进程自己的窗口。

    EnumWindows 的顺序就是 Z 序；快搜窗自己是置顶的，但它的窗口类不是 #32770，
    所以第一个命中的通常正是「用户刚才还在用的那个另存为/打开对话框」。
    """
    b = _backend()
    me = os.getpid()
    hits = []

    def cb(hwnd, _):
        try:
            if not b.visible(hwnd):
                return True
            if b.class_name(hwnd) != FILE_DIALOG_CLASS:
                return True
            if b.pid(hwnd) == me:
                return True
            hits.append(hwnd)
        except Exception:
            pass
        return True

    b.enum_windows(cb)
    return hits


def _edit_parents(b, hwnd, limit=5):
    """往上走几层拿父窗口类名。地址栏的 Edit 藏在 ComboBoxEx32 里，要排除。"""
    out = []
    p = b.parent(hwnd)
    for _ in range(limit):
        if not p:
            break
        try:
            out.append(b.class_name(p))
        except Exception:
            break
        p = b.parent(p)
    return out


def _dialog_edits(dlg):
    """找两类输入框：

    - **文件名框**（另存为 / 打开 / 上传文件）：可用、可见，且不在地址栏链里；
    - **地址栏框**（「选择文件夹」没有文件名框，只有地址栏）：在 ComboBoxEx32 链里。

    普通 MessageBox 也是 #32770，但一个 Edit 都没有，顺带被排除掉。
    """
    b = _backend()
    file_edit = 0
    addr_edit = 0

    def cb(hwnd, _):
        nonlocal file_edit, addr_edit
        try:
            if b.class_name(hwnd) != 'Edit':
                return True
            if not b.visible(hwnd) or not b.enabled(hwnd):
                return True
            parents = _edit_parents(b, hwnd)
            cid = b.ctrl_id(hwnd)
            if any('ComboBoxEx32' in c for c in parents):
                # 第 229 项微探针实测：打开/另存为的「文件名框」其实也在
                # ComboBoxEx32 里（历史下拉），控制 ID 仍是 0x047C —— 有这个 ID
                # 的按文件名框算，否则整张打开对话框都进不了 file 分支；
                # 没有控制 ID 的才是真·面包屑地址栏。
                if b.ctrl_id(hwnd) == FILENAME_EDIT_ID:
                    file_edit = file_edit or hwnd
                elif not addr_edit:
                    addr_edit = hwnd
            elif cid == FILENAME_EDIT_ID or not file_edit:
                file_edit = hwnd
        except Exception:
            pass
        return True

    b.enum_children(dlg, cb)
    return file_edit, addr_edit


def _has_shell_view(dlg):
    """对话框里有没有 Shell 的命名空间视图（文件列表那块）。

    第 200 项的根因：以前「`#32770` + 有 Edit」就当成文件对话框，结果下载器的
    「下载文件信息」、播放器的「打开链接」这类普通业务对话框（同样是 #32770、
    同样有输入框）全被误认，快搜窗莫名其妙往外冒。

    标准「打开/另存为/选择文件夹」是 Shell 公共对话框，里面一定嵌着命名空间视图：
    Win10/11 是 `DUIViewWndClassName`，老系统是 `SHELLDLL_DefView`。业务对话框
    不会为了一个路径框去创建 Shell 视图 —— 这是最可靠、也最便宜的判别信号。
    """
    b = _backend()
    hit = []

    def cb(hwnd, _):
        try:
            if b.class_name(hwnd) in SHELL_VIEW_CLASSES:
                hit.append(hwnd)
                return False        # 找到一个就够，别再往下走
        except Exception:
            pass
        return True

    b.enum_children(dlg, cb)
    return bool(hit)


def is_file_dialog(hwnd):
    """这个 hwnd 是不是「真正的打开/另存为/选择文件夹」对话框。

    给打字钩子用：在随便一个业务对话框里敲字，不该被我们抢走。
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return False
    try:
        b = _backend()
        if b.class_name(hwnd) != FILE_DIALOG_CLASS:
            return False
        return _has_shell_view(hwnd)
    except Exception:
        return False


def find_dialog():
    """返回 (对话框, 文件名框, 地址栏框)；找不到返回 (0, 0, 0)。

    三种场景的控件不一样，不能只认文件名框：
    - 另存为 / 打开 / 上传文件：有文件名框（ID 1148），写路径 + IDOK 会导航；
    - **选择文件夹**：没有文件名框，只有地址栏，只能把路径填进地址栏；
    - 老式 SHBrowseForFolder（树形）：两者都没有，返回 no_edit。
    """
    for dlg in _top_dialogs():
        # 第 200 项：必须还嵌着 Shell 命名空间视图，否则就是业务对话框（下载器、
        # 播放器那种），碰都不该碰。
        if not _has_shell_view(dlg):
            continue
        fe, ae = _dialog_edits(dlg)
        if fe or ae:
            return dlg, fe, ae
    return 0, 0, 0


def has_dialog():
    return find_dialog()[0] != 0


def dialog_title():
    dlg = find_dialog()[0]
    if not dlg:
        return ''
    try:
        return _backend().title(dlg)[:80]
    except Exception:
        return ''


def probe():
    """给排障用：把「后端到底看见了什么」原样报出来。

    `skipped` 只列那些「是 #32770 且**没有** Shell 视图」的窗口 —— 下载器、
    播放器的自定义对话框都会落在这里（第 200 项）。QA BUG-05：旧版把其它
    真文件对话框也算进 skipped，和 docstring / 排障语义都不符。
    """
    try:
        dlg, file_edit, addr_edit = find_dialog()
        skipped = []
        shell_candidates = 0
        for h in _top_dialogs():
            try:
                if class_of(h) != FILE_DIALOG_CLASS:
                    continue
                has_shell = _has_shell_view(h)
                if has_shell:
                    shell_candidates += 1
                elif h != dlg:
                    skipped.append({'hwnd': h, 'title': _backend().title(h)[:40]})
            except Exception:
                skipped.append({'hwnd': h, 'title': ''})
        return {
            'ok': True,
            'dialog': bool(dlg),
            'title': dialog_title() if dlg else '',
            'hwnd': dlg,
            'edit': file_edit,          # 兼容旧字段
            'file_edit': file_edit,
            'addr_edit': addr_edit,
            'candidates': shell_candidates,
            'shell_candidates': shell_candidates,
            'skipped': skipped,         # 认成「不是文件对话框」的 #32770
        }
    except Exception as e:
        return {'ok': False, 'dialog': False, 'err': '%s: %s' % (type(e).__name__, e)}


# ------------------------------------------------------------------ 已打开的目录
def _loc_path(w):
    """从一个 Shell 窗口对象取本地目录。取不到返回 ''（IE、控制面板、网盘都会走到这）。"""
    p = ''
    try:
        url = w.LocationURL or ''
        if url.startswith('file:'):
            p = os.path.normpath(url2pathname(unquote(urlparse(url).path)))
    except Exception:
        p = ''
    if not p:
        try:
            p = os.path.normpath(str(w.Document.Folder.Self.Path))
        except Exception:
            p = ''
    return p


def _alive(hwnd):
    """这个 hwnd 现在是不是一个真的、还看得见的窗口。

    第 199 项的 bug：用户把文件夹关了一分钟，快搜窗里还挂着「当前打开的文件夹」。
    Shell 的 Windows() 偶尔会留下已经销毁的残留项（explorer 进程没退，窗口对象
    还在集合里），所以光靠 COM 枚举不够，必须回问一次 Win32。
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return False
    try:
        b = _backend()
        if not b.is_window(hwnd):
            return False
        # 最小化的窗口 IsWindowVisible 仍是 True（只是缩起来了），不算关掉。
        return bool(b.visible(hwnd))
    except Exception:
        # 查不动就当成还开着：宁可多列一条，也别把用户正在用的窗口吞掉。
        return True


def _shell_windows():
    """[(hwnd, 目录)] —— 本机已打开的资源管理器窗口。COM 不可用时返回 []。

    第 197 项开始需要「按 hwnd 精确匹配前台那个窗口」，所以不能再只返回路径列表：
    open_dirs 的推荐要路径，打字接管要的是**当前这一个**窗口的目录。
    """
    out = []
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            wins = win32com.client.Dispatch('Shell.Application').Windows()
            for i in range(wins.Count):
                try:
                    w = wins.Item(i)
                except Exception:
                    continue
                try:
                    hwnd = int(w.HWND or 0)
                except Exception:
                    hwnd = 0
                p = _loc_path(w)
                if p and os.path.isdir(p) and _alive(hwnd):
                    out.append((hwnd, p))
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return []
    return out


def _live_shell_windows():
    """_shell_windows() 再筛一遍「窗口还真的存在」。

    第 199 项的 bug：用户把文件夹关了一分钟，快搜窗里还挂着「当前打开的文件夹」。
    Shell 的 Windows() 偶尔会留下已经销毁的残留项（explorer 进程没退，窗口对象
    还在集合里），所以光靠 COM 枚举不够，必须回问一次 Win32。
    """
    return [(h, p) for h, p in _shell_windows() if _alive(h)]


def open_folders(limit=6):
    """枚举资源管理器窗口当前所在的目录（不扫描磁盘、不读历史记录）。"""
    seen, uniq = set(), []
    for _hwnd, p in _live_shell_windows():
        k = os.path.normcase(p)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
        if len(uniq) >= limit:
            break
    return uniq


# ------------------------------------------------------------------ 目录归属（第 197 项）
def foreground_hwnd():
    try:
        return int(_backend().fg_hwnd() or 0)
    except Exception:
        return 0


def class_of(hwnd):
    """窗口类名（拿不到返回空串）。"""
    try:
        return _backend().class_name(int(hwnd or 0))
    except Exception:
        return ''


def focus_window(hwnd):
    """把某个窗口带回前台（快搜窗收起后，让用户回到刚才那个对话框）。

    SetForegroundWindow 有前台权限限制，失败就算了——用户 Alt+Tab 一下也能回去。
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return False
    try:
        _backend().foreground(hwnd)
        return True
    except Exception:
        return False


def window_folder(hwnd):
    """资源管理器窗口当前所在的目录。按 HWND 精确匹配，不靠「列表第一个」。"""
    hwnd = int(hwnd or 0)
    if not hwnd:
        return ''
    for h, p in _shell_windows():
        if h == hwnd:
            return p
    return ''


def dialog_dir(dlg):
    """文件对话框地址栏里显示的目录。

    另存为对话框的地址栏平时是一串面包屑，但底层 ComboBoxEx32 里的 Edit
    仍然保存着完整路径 —— 读它比去解析面包屑可靠得多。读出来不是已存在目录
    就当没有（比如用户正把地址栏当成编辑框在打字）。
    """
    if not dlg:
        return ''
    try:
        b = _backend()
        _fe, ae = _dialog_edits(dlg)
        if not ae:
            return ''
        txt = (b.get_text(ae) or '').strip().strip('"')
        if not txt:
            return ''
        p = os.path.normpath(txt)
        return p if os.path.isdir(p) else ''
    except Exception:
        return ''


def folder_for(hwnd):
    """这个窗口「现在正在看的目录」。资源管理器 → 该目录；文件对话框 → 地址栏目录。"""
    hwnd = int(hwnd or 0)
    if not hwnd:
        return ''
    try:
        cls = _backend().class_name(hwnd)
    except Exception:
        return ''
    if cls == FILE_DIALOG_CLASS:
        return dialog_dir(hwnd)
    if cls in FOLDER_CLASSES:
        return window_folder(hwnd)
    return ''


def is_foreground_dialog(dlg):
    """对话框是不是「用户此刻正在用的那个」。

    不看这个就会踩坑：用户点了别的程序，另存为还开着但已经在后面，
    这时候弹快搜窗属于打断。焦点在对话框的子控件上也算（输入框拿到焦点时
    前台窗口仍是对话框本身，但捕获/菜单这类情况会是子窗口）。
    """
    dlg = int(dlg or 0)
    if not dlg:
        return False
    try:
        b = _backend()
        fg = int(b.fg_hwnd() or 0)
        if not fg:
            return False
        if fg == dlg:
            return True
        return bool(b.is_child(dlg, fg))
    except Exception:
        return False


# ------------------------------------------------------------------ 跳转
def jump_target(path):
    """把「点中的东西」折算成要跳去的目录：
    目录 → 自己；文件 → 所在文件夹；不存在 → None。"""
    p = os.path.normpath(str(path or '').strip().strip('"'))
    if not p or p in ('.', os.sep):
        return None
    if os.path.isdir(p):
        return p
    if os.path.isfile(p):
        return os.path.dirname(p) or None
    return None


def nav_text(path):
    """写进文件名框的文本。末尾的反斜杠是关键：Windows 会当成「切到这个目录」，
    而不是「把文件存成这个名字」。"""
    p = os.path.normpath(path)
    return p if p.endswith(os.sep) else p + os.sep


def jump(path):
    """执行跳转。调用方（brain.py）已经做过白名单校验，这里只管 Win32。

    三种模式（第 229 项补第三种；探针实测见 tmp/probe_dialog_nav_20261114.py）：
    - mode=file：点中的是**文件** —— 写文件完整路径 + IDOK（文件名框空时），
      对话框直接把该文件当答案（第 228 项 / 227②）；
    - mode=nav：点中的是**目录**且文件名框为空 —— 填「目录\」+ 对文件名框发回车，
      对话框**原地导航**进该目录、保持打开。实测 BM_CLICK IDOK 在文件夹选择
      对话框里会把目录直接当答案关掉，WM_COMMAND IDOK 在 OK 禁用时完全无效；
    - mode=addr：文件名框有用户字（另存为防误覆盖）或没有文件名框（老式地址栏）——
      只填不按，确认动作留给用户。
    """
    raw = os.path.normpath(str(path or '').strip().strip('"'))
    is_file = os.path.isfile(raw)
    target = jump_target(path)
    if not target:
        return {'ok': False, 'reason': 'bad_path'}
    dlg, file_edit, addr_edit = find_dialog()
    if not dlg:
        return {'ok': False, 'reason': 'no_dialog'}
    if not file_edit and not addr_edit:
        return {'ok': False, 'reason': 'no_edit'}
    b = _backend()
    try:
        if not file_edit:
            # 「选择文件夹」对话框只有地址栏：填目录（文件→所在文件夹），
            # 不代按回车/选择按钮 —— 误确认会把当前目录顶掉（196 项的安全降级不动）
            b.set_text(addr_edit, nav_text(os.path.dirname(raw) if is_file else raw))
            try:
                b.foreground(dlg)
            except Exception:
                pass
            return {'ok': True, 'path': target, 'hwnd': dlg, 'mode': 'addr'}
        # 第 228 项：点中的是**文件** → 写文件完整路径（不带尾反斜杠），对话框
        # 直接把该文件当答案（跳到所在位置并选中）。文件名框为空 = 「打开」
        # 对话框 → 补一次 IDOK 免得用户再点「打开」；框里有字 = 「另存为」→
        # 只填不按（Windows 自己会弹覆盖确认，不会误覆盖）。
        # 点中的是**目录** → 写「目录\」+ IDOK，原地进入该目录（196 项行为不变）。
        cur = ''
        try:
            cur = (b.get_text(file_edit) or '').strip()
        except Exception:
            cur = ''
        if is_file:
            # 第 228 项（227②）：文件 → 写完整路径，对话框直接把该文件当答案。
            # 文件名框为空（=「打开」对话框）才补一次 IDOK；有字（=「另存为」）
            # 只填不按，防误覆盖。
            b.set_text(file_edit, raw)
            btn = 0
            try:
                btn = b.dlg_item(dlg, IDOK)
            except Exception:
                btn = 0
            if btn and b.enabled(btn) and not cur:
                b.click(btn)
            else:
                b.command(dlg, IDOK)
        elif cur:
            # 文件名框里有用户字（另存为）：只填不按 —— Windows 自己会弹覆盖
            # 确认，防误覆盖优先于少一次点击（第 228 项取舍不动）。
            b.set_text(file_edit, nav_text(target))
            try:
                b.foreground(dlg)
            except Exception:
                pass
            return {'ok': True, 'path': target, 'hwnd': dlg, 'mode': 'addr'}
        else:
            # 第 229 项探针实测：文件夹选择对话框（微信「选择文件」这种）里
            # BM_CLICK IDOK 会把目录直接当答案关掉对话框（V1/B1）；OK 禁用时
            # WM_COMMAND IDOK 更是完全无效（B3，用户遇到的正是这条）。
            # 填「目录\」再对文件名框 Post 一对回车 = IFileDialog 原地导航
            # 进该目录且对话框保持打开（A1）。
            b.set_text(file_edit, nav_text(target))
            b.key_enter(file_edit)
            try:
                b.foreground(dlg)
            except Exception:
                pass
            return {'ok': True, 'path': target, 'hwnd': dlg, 'mode': 'nav'}
        # mini 窗口收起后把对话框带回前台；SetForegroundWindow 有系统权限限制，
        # 失败也无所谓——用户 Alt+Tab 一下就能看到地址栏已经变了。
        try:
            b.foreground(dlg)
        except Exception:
            pass
        return {'ok': True, 'path': target, 'hwnd': dlg, 'mode': 'file'}
    except Exception as e:
        return {'ok': False, 'reason': 'win32', 'err': '%s: %s' % (type(e).__name__, e)}
