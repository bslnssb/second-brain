# -*- coding: utf-8 -*-
"""第 194 项：Quick Switch（把目录送进打开/保存对话框）。

这块最容易「看起来能用、其实点不中」：Win32 控件认错一个、路径少了末尾反斜杠、
白名单漏一个分支，用户看到的就是「点了没反应」。所以行为全部钉住：

- jump_target / nav_text 的折算规则（文件 → 所在目录、末尾必须带反斜杠）
- 找对话框时排除消息框（#32770 但没有 Edit）和本进程自己的窗口
- 白名单：只有「当前打开的文件夹 / 最近目录 / 刚搜出来的结果」允许跳
- mini 模板与五语言新键都在（防止只改 Python 忘改页面）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402
import quick_switch  # noqa: E402


class FakeBackend:
    """一棵最小窗口树：顶层窗口 [(hwnd, class, title, pid)]，子控件 {parent: [(hwnd, class, ctrl_id)]}"""

    def __init__(self):
        self.top = []
        self.kids = {}
        self.texts = {}
        self.clicked = []
        self.commands = []
        self.keys = []
        self.fg = []
        self.me = os.getpid()
        # 第 200 项：标准文件对话框里嵌着 Shell 命名空间视图。默认为真（模拟
        # 真对话框），测「业务对话框不该被认成文件对话框」时置 False。
        self.shell_view = True
        self._cls = {}
        self._title = {}
        self._pid = {}
        self._cid = {}

    def enum_windows(self, cb):
        for hwnd, cls, title, pid in self.top:
            self._cls[hwnd] = cls
            self._title[hwnd] = title
            self._pid[hwnd] = pid
            cb(hwnd, None)

    def enum_children(self, hwnd, cb):
        # 真实 Win32 的 EnumChildWindows 是递归的（所有后代）；假树也必须递归，
        # 否则 ComboBoxEx32 里的 Edit 根本不会出现（第 196 项踩过）。
        if self.shell_view and self.class_name(hwnd) == '#32770':
            view = 90000 + int(hwnd)
            self._cls[view] = 'DUIViewWndClassName'
            cb(view, None)
        for child, cls, cid in self.kids.get(hwnd, []):
            self._cls[child] = cls
            self._cid[child] = cid
            cb(child, None)
            self.enum_children(child, cb)

    def class_name(self, hwnd):
        if hwnd in self._cls:
            return self._cls[hwnd]
        # 真 Win32 的 GetClassName 不需要先 Enum 一次；假树里补上这个语义，
        # 否则 folder_for() 这类「直接查类名」的函数在测试里永远拿到空串。
        for h, cls, _t, _p in self.top:
            if h == hwnd:
                return cls
        return ''

    def title(self, hwnd):
        return self._title.get(hwnd, '')

    def visible(self, hwnd):
        return True

    def is_window(self, hwnd):
        # 真 Win32：窗口销毁后 IsWindow 就是 False。假树里「在 top 表里」
        # 等价于「还活着」，用它来测「关掉的文件夹要从推荐里消失」。
        return any(h == hwnd for h, _c, _t, _p in self.top)

    def enabled(self, hwnd):
        return True

    def parent(self, hwnd):
        for p, kids in self.kids.items():
            for c, _cls, _cid in kids:
                if c == hwnd:
                    return p
        return 0

    def ctrl_id(self, hwnd):
        return self._cid.get(hwnd, 0)

    def pid(self, hwnd):
        return self._pid.get(hwnd, self.me + 1)

    def dlg_item(self, hwnd, item_id):
        for p, kids in self.kids.items():
            if p != hwnd:
                continue
            for c, _cls, cid in kids:
                if cid == item_id:
                    return c
        return 0

    def set_text(self, edit, text):
        self.texts[edit] = text

    def click(self, btn):
        self.clicked.append(btn)

    def command(self, hwnd, cmd):
        self.commands.append((hwnd, cmd))

    def key_enter(self, edit):
        # 第 229 项：目录导航 = 填「目录\」+ 对文件名框 Post 回车键对
        self.keys.append(edit)

    def foreground(self, hwnd):
        self.fg.append(hwnd)

    # -- 第 197 项：目录归属 -----------------------------------------
    def fg_hwnd(self):
        return getattr(self, 'fg_current', 0)

    def is_child(self, parent, child):
        seen = set()
        stack = [parent]
        while stack:
            p = stack.pop()
            for c, _cls, _cid in self.kids.get(p, []):
                if c == child:
                    return True
                if c not in seen:
                    seen.add(c)
                    stack.append(c)
        return False

    def get_text(self, hwnd, n=1024):
        return self.texts.get(hwnd, '')


def dialog_tree(fb, hwnd=100, edit=101, ok=102, pid=None):
    fb.top = [(hwnd, '#32770', '另存为', pid or (os.getpid() + 99))]
    fb.kids = {hwnd: [(edit, 'Edit', 0x047C), (ok, 'Button', 1)]}
    return hwnd, edit, ok


class TargetTest(unittest.TestCase):
    def test_dir_is_itself(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(os.path.normpath(d), quick_switch.jump_target(d))

    def test_file_goes_to_parent(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'a.docx'
            f.write_text('x', encoding='utf-8')
            self.assertEqual(os.path.normpath(d), quick_switch.jump_target(str(f)))

    def test_missing_or_root_returns_none(self):
        self.assertIsNone(quick_switch.jump_target(''))
        self.assertIsNone(quick_switch.jump_target('Z:\\definitely\\not\\here'))
        self.assertIsNone(quick_switch.jump_target(os.sep))

    def test_quotes_are_stripped(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(os.path.normpath(d), quick_switch.jump_target('"%s"' % d))

    def test_nav_text_has_trailing_sep(self):
        self.assertTrue(quick_switch.nav_text('F:\\a\\b').endswith(os.sep))
        self.assertEqual(quick_switch.nav_text('F:\\a\\b\\'), 'F:\\a\\b' + os.sep)


class FindDialogTest(unittest.TestCase):
    def tearDown(self):
        quick_switch.set_backend(None)

    def test_finds_dialog_with_filename_edit(self):
        fb = FakeBackend()
        hwnd, edit, _ok = dialog_tree(fb)
        quick_switch.set_backend(fb)
        self.assertEqual((hwnd, edit, 0), quick_switch.find_dialog())
        self.assertTrue(quick_switch.has_dialog())
        self.assertEqual('另存为', quick_switch.dialog_title())

    def test_message_box_without_edit_is_ignored(self):
        fb = FakeBackend()
        fb.top = [(1, '#32770', '提示', os.getpid() + 99)]
        fb.kids = {1: []}
        quick_switch.set_backend(fb)
        self.assertEqual((0, 0, 0), quick_switch.find_dialog())

    def test_own_process_dialog_is_ignored(self):
        fb = FakeBackend()
        dialog_tree(fb, hwnd=5, edit=6, ok=7, pid=os.getpid())
        quick_switch.set_backend(fb)
        self.assertEqual((0, 0, 0), quick_switch.find_dialog())

    def test_business_dialog_without_shell_view_is_ignored(self):
        """第 200 项：下载器的「下载文件信息」也是 #32770、也有输入框，但不该弹快搜窗。

        区分信号是 Shell 命名空间视图（真文件对话框里那块文件列表的宿主）。
        """
        fb = FakeBackend()
        fb.shell_view = False
        hwnd = 60
        fb.top = [(hwnd, '#32770', '下载文件信息', os.getpid() + 99)]
        fb.kids = {hwnd: [(61, 'Edit', 0), (62, 'ComboBoxEx32', 0),
                          (63, 'ComboBox', 0), (64, 'Button', 1)]}
        fb.kids[62] = [(621, 'Edit', 0)]
        quick_switch.set_backend(fb)
        self.assertEqual((0, 0, 0), quick_switch.find_dialog())
        self.assertFalse(quick_switch.is_file_dialog(hwnd))

    def test_real_file_dialog_is_recognised(self):
        fb = FakeBackend()
        hwnd, _edit, _ok = dialog_tree(fb)
        quick_switch.set_backend(fb)
        self.assertTrue(quick_switch.is_file_dialog(hwnd))
        self.assertFalse(quick_switch.is_file_dialog(0))

    def test_address_bar_and_filename_edit_are_distinguished(self):
        """地址栏 Edit 在 ComboBoxEx32 里；文件名框不在。两者都要认出来（第 196 项）。"""
        fb = FakeBackend()
        hwnd = 30
        fb.top = [(hwnd, '#32770', '打开', os.getpid() + 99)]
        fb.kids = {hwnd: [(31, 'ComboBoxEx32', 0), (32, 'Edit', 0x047C), (33, 'Button', 1)]}
        fb.kids[31] = [(311, 'Edit', 0)]
        quick_switch.set_backend(fb)
        self.assertEqual((hwnd, 32, 311), quick_switch.find_dialog())

    def test_open_dialog_filename_in_combo_is_file_edit(self):
        """第 229 项微探针：打开/另存为的文件名框在 ComboBoxEx32 里，但控制 ID
        仍是 0x047C —— 有 ID 的按文件名框算，否则整张打开对话框走不了 file 分支。"""
        fb = FakeBackend()
        hwnd = 70
        fb.top = [(hwnd, '#32770', '打开', os.getpid() + 99)]
        fb.kids = {hwnd: [(71, 'ComboBoxEx32', 0), (72, 'Button', 1)]}
        fb.kids[71] = [(711, 'Edit', 0x047C)]
        quick_switch.set_backend(fb)
        self.assertEqual((hwnd, 711, 0), quick_switch.find_dialog())

    def test_pick_folder_has_only_address_bar(self):
        """「选择文件夹」没有文件名框，只有地址栏 —— 不能被当成消息框丢掉。"""
        fb = FakeBackend()
        hwnd = 40
        fb.top = [(hwnd, '#32770', '选择文件夹', os.getpid() + 99)]
        fb.kids = {hwnd: [(41, 'ComboBoxEx32', 0), (42, 'Button', 1)]}
        fb.kids[41] = [(411, 'Edit', 0)]
        quick_switch.set_backend(fb)
        self.assertEqual((hwnd, 0, 411), quick_switch.find_dialog())


class JumpTest(unittest.TestCase):
    def tearDown(self):
        quick_switch.set_backend(None)

    def test_jump_dir_navigates_with_enter(self):
        """第 229 项：目录结果 + 文件名框为空 = 填「目录\」+ 回车键对（原地导航，
        不点 IDOK —— 探针实测文件夹选择对话框里点 IDOK 会直接关掉对话框）。"""
        fb = FakeBackend()
        _hwnd, edit, ok = dialog_tree(fb)
        quick_switch.set_backend(fb)
        with tempfile.TemporaryDirectory() as d:
            r = quick_switch.jump(d)
            self.assertTrue(r['ok'], r)
            self.assertEqual('nav', r['mode'])
            self.assertEqual(quick_switch.nav_text(d), fb.texts[edit])
            self.assertEqual([edit], fb.keys)
            self.assertEqual([], fb.clicked)
            self.assertEqual([], fb.commands)

    def test_jump_file_writes_full_path_and_clicks_ok(self):
        """第 228 项（227②）：文件结果 = 写完整路径，文件名框空时补 IDOK 直接选定。"""
        fb = FakeBackend()
        _hwnd, edit, ok = dialog_tree(fb)
        quick_switch.set_backend(fb)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'a.txt'
            f.write_text('x', encoding='utf-8')
            r = quick_switch.jump(str(f))
            self.assertTrue(r['ok'], r)
            self.assertEqual('file', r['mode'])
            self.assertEqual(str(f), fb.texts[edit])
            self.assertEqual([ok], fb.clicked)
            self.assertEqual([], fb.keys)

    def test_jump_save_as_with_text_fills_only(self):
        """第 228 项取舍：文件名框有用户字（另存为）→ 只填不按、不发回车。"""
        fb = FakeBackend()
        _hwnd, edit, ok = dialog_tree(fb)
        fb.texts[edit] = '报告.docx'
        quick_switch.set_backend(fb)
        with tempfile.TemporaryDirectory() as d:
            r = quick_switch.jump(d)
            self.assertTrue(r['ok'], r)
            self.assertEqual('addr', r['mode'])
            self.assertEqual(quick_switch.nav_text(d), fb.texts[edit])
            self.assertEqual([], fb.clicked)
            self.assertEqual([], fb.keys)

    def test_pick_folder_fills_address_bar_without_confirming(self):
        """选择文件夹：只填地址栏，**绝不点 IDOK**（点了会把当前目录误确认掉）。"""
        fb = FakeBackend()
        hwnd = 50
        fb.top = [(hwnd, '#32770', '选择文件夹', os.getpid() + 99)]
        fb.kids = {hwnd: [(51, 'ComboBoxEx32', 0), (52, 'Button', 1)]}
        fb.kids[51] = [(511, 'Edit', 0)]
        quick_switch.set_backend(fb)
        with tempfile.TemporaryDirectory() as d:
            r = quick_switch.jump(d)
            self.assertTrue(r['ok'], r)
            self.assertEqual('addr', r['mode'])
            self.assertEqual(quick_switch.nav_text(d), fb.texts[511])
            self.assertEqual([], fb.clicked)
            self.assertEqual([], fb.commands)

    def test_jump_without_dialog_reports_reason(self):
        fb = FakeBackend()
        quick_switch.set_backend(fb)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual({'ok': False, 'reason': 'no_dialog'}, quick_switch.jump(d))

    def test_jump_bad_path(self):
        fb = FakeBackend()
        quick_switch.set_backend(fb)
        self.assertEqual({'ok': False, 'reason': 'bad_path'}, quick_switch.jump(''))


class BrainApiTest(unittest.TestCase):
    """白名单是这块的安全边界：页面拼一个路径不能让对话框去任意位置。"""

    def test_rejects_unlisted_dir(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(brain, '_qs_items', return_value=[]):
            r = brain._api_dialog_jump({'p': d})
            self.assertEqual({'ok': False, 'reason': 'not_allowed'}, r)

    def test_allows_open_folder(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(brain, '_qs_items', return_value=[{'p': d, 'n': 'x', 'k': 'open'}]), \
                mock.patch.object(quick_switch, 'jump', return_value={'ok': True, 'path': d}) as j:
            r = brain._api_dialog_jump({'p': d})
            self.assertTrue(r['ok'])
            self.assertEqual(d, j.call_args[0][0])

    def test_allows_recent_result(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(brain, '_qs_items', return_value=[]), \
                mock.patch.object(brain, 'ev_recent', return_value=True), \
                mock.patch.object(quick_switch, 'jump', return_value={'ok': True}):
            self.assertTrue(brain._api_dialog_jump({'p': d})['ok'])

    def test_jump_records_recent(self):
        """第 196 项：跳成功要记一次「最近访问」，下次推荐才排得靠前。"""
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(brain, '_qs_items', return_value=[{'p': d, 'n': 'x', 'k': 'open'}]), \
                mock.patch.object(quick_switch, 'jump', return_value={'ok': True}), \
                mock.patch.object(brain, 'record_open') as rec, \
                mock.patch.object(brain, 'conn', return_value=object()), \
                mock.patch.object(brain, 'con_lock'):
            self.assertTrue(brain._api_dialog_jump({'p': d})['ok'])
            rec.assert_called_once()

    def test_open_dirs_shape(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(brain, '_qs_items', return_value=[{'p': d, 'n': 'x', 'k': 'open'}]), \
                mock.patch.object(brain, 'allow_open'), \
                mock.patch.object(quick_switch, 'probe',
                                  return_value={'ok': True, 'dialog': True, 'title': '另存为'}):
            r = brain._api_open_dirs()
            self.assertEqual([{'p': d, 'n': 'x', 'k': 'open'}], r['items'])
            self.assertTrue(r['dialog'])
            self.assertEqual('另存为', r['title'])


class TemplateTest(unittest.TestCase):
    def test_mini_template_has_merged_home_and_hotkey(self):
        """第 234 项：目录与最近文件合成一块（renderHome），不再有 dlist/dh 两组。

        Ctrl+G / jumpDir / dialog_jump 这些跳转接线必须原样在。"""
        for needle in ('id="rh"', 'function renderHome', 'fetchOpenDirs', 'jumpDir',
                       'jumpCur', "k==='g'", '/api/dialog_jump'):
            self.assertIn(needle, brain.MINI_TPL, needle)
        self.assertNotIn('id="dlist"', brain.MINI_TPL)
        self.assertNotIn('id="dh"', brain.MINI_TPL)

    def test_mini_icon_is_wrapped(self):
        """第 195 项的 bug：少了 <span class="ico"> 包裹，大图标会把整行撑满。"""
        tpl = brain.MINI_TPL
        # 第 234 项：目录行也改走 rowHtml（原来 drow() 里那份 ico({t:'dir'}) 已删）
        self.assertIn("""<span class="ico">'+ico(it)""", tpl)

    def test_mini_follows_palette_and_wallpaper(self):
        for needle in ('--sb-bg', 'applyLook', 'sb_vars', 'body.has-wall', 'id="wallbg"'):
            self.assertIn(needle, brain.MINI_TPL, needle)

    def test_click_jumps_when_dialog_open(self):
        """第 196 项：有「另存为」对话框时，左键/回车必须跳目录，而不是再打开文件。"""
        tpl = brain.MINI_TPL
        self.assertIn('if(HAS_DIALOG){ jumpDir(it.p); return; }', tpl)
        self.assertIn("MT('jm_hint')", tpl)
        self.assertIn("id=\"jm\"", tpl)
        for lang in ('zh', 'en', 'es', 'pt', 'hi'):
            self.assertTrue(brain.MINI_L[lang].get('jm_hint'), lang)

    def test_mini_langs_have_new_keys(self):
        keys = ('dh', 'dh_hint', 'dh_go', 'qs_ok', 'qs_none', 'qs_fail')
        for lang in ('zh', 'en', 'es', 'pt', 'hi'):
            for k in keys:
                self.assertTrue(brain.MINI_L[lang].get(k), '%s.%s 空' % (lang, k))

    def test_react_wiring_present(self):
        api = (ROOT / 'web/src/api.ts').read_text(encoding='utf-8')
        app = (ROOT / 'web/src/App.tsx').read_text(encoding='utf-8')
        self.assertIn('/api/dialog_jump', api)
        self.assertIn('dialogJump', app)
        self.assertIn("t('ctx_dialog')", app)

    def test_mini_home_dock_cap_at_most_two(self):
        """第 255 项：在上传/选择文件等贴角小窗场景（SMALL 模式），「最近访问」最多只列 2 个，
        避免窗口过高遮挡宿主对话框列表；未贴角的大窗保持原行数。"""
        tpl = brain.MINI_TPL
        self.assertIn('if(SMALL) cap=Math.min(cap, 2);', tpl)


class FolderTest(unittest.TestCase):
    """第 197 项：打字接管要知道「用户现在在看哪个文件夹」。"""

    def tearDown(self):
        quick_switch.set_backend(None)

    def test_dialog_dir_reads_address_edit(self):
        fb = FakeBackend()
        with tempfile.TemporaryDirectory() as d:
            hwnd = 200
            fb.top = [(hwnd, '#32770', '另存为', os.getpid() + 99)]
            fb.kids = {hwnd: [(201, 'ComboBoxEx32', 0), (202, 'Button', 1)]}
            fb.kids[201] = [(203, 'Edit', 0)]
            fb.texts[203] = d
            quick_switch.set_backend(fb)
            self.assertEqual(os.path.normpath(d), quick_switch.folder_for(hwnd))

    def test_dialog_dir_ignores_non_dir_text(self):
        fb = FakeBackend()
        hwnd = 210
        fb.top = [(hwnd, '#32770', '另存为', os.getpid() + 99)]
        fb.kids = {hwnd: [(211, 'ComboBoxEx32', 0)]}
        fb.kids[211] = [(212, 'Edit', 0)]
        fb.texts[212] = 'C:\\不存在的目录\\'
        quick_switch.set_backend(fb)
        self.assertEqual('', quick_switch.folder_for(hwnd))

    def test_explorer_folder_matches_hwnd(self):
        fb = FakeBackend()
        quick_switch.set_backend(fb)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(quick_switch, '_shell_windows',
                                  return_value=[(300, d), (301, os.path.dirname(d) or d)]):
            self.assertEqual(os.path.normpath(d), quick_switch.window_folder(300))
            self.assertEqual('', quick_switch.window_folder(399))

    def test_foreground_dialog_requires_focus(self):
        fb = FakeBackend()
        hwnd = 400
        fb.top = [(hwnd, '#32770', '另存为', os.getpid() + 99)]
        fb.kids = {hwnd: [(401, 'Edit', 0x047C)]}
        quick_switch.set_backend(fb)
        fb.fg_current = hwnd
        self.assertTrue(quick_switch.is_foreground_dialog(hwnd))
        fb.fg_current = 401                      # 焦点在子控件上也算
        self.assertTrue(quick_switch.is_foreground_dialog(hwnd))
        fb.fg_current = 999                      # 用户切到别的程序了
        self.assertFalse(quick_switch.is_foreground_dialog(hwnd))
        self.assertFalse(quick_switch.is_foreground_dialog(0))


class TypeWatchTest(unittest.TestCase):
    """打字接管的判定：抢错一次就会毁掉用户正在打的字，所以逐条钉住。"""

    def test_captures_typing_in_folder(self):
        import typewatch as tw
        self.assertTrue(tw.should_capture(0x41, False, False, False,
                                          'CabinetWClass', 'DirectUIHWND', True))

    def test_never_steals_from_text_box(self):
        import typewatch as tw
        for focus in ('Edit', 'ComboBox', 'ComboBoxEx32', 'RichEdit20W', 'SearchBox'):
            self.assertFalse(tw.should_capture(0x41, False, False, False,
                                               'CabinetWClass', focus, True), focus)

    def test_ignores_other_apps_and_hotkeys(self):
        import typewatch as tw
        self.assertFalse(tw.should_capture(0x41, False, False, False, 'Notepad', '', True))
        self.assertFalse(tw.should_capture(0x41, True, False, False,
                                          'CabinetWClass', 'DirectUIHWND', True))
        self.assertFalse(tw.should_capture(0x41, False, False, True,
                                           'CabinetWClass', 'DirectUIHWND', True))
        self.assertFalse(tw.should_capture(0x41, False, False, False,
                                          'CabinetWClass', 'DirectUIHWND', False))

    def test_only_printable_starts_it(self):
        import typewatch as tw
        for vk in (0x1B, 0x0D, 0x09, 0x25, 0x70, 0x20):
            self.assertFalse(tw.should_capture(vk, False, False, False,
                                               'CabinetWClass', 'DirectUIHWND', True), hex(vk))
        for vk in (0x41, 0x5A, 0x30, 0x39, 0xBD, 0xE2):
            self.assertTrue(tw.is_printable(vk), hex(vk))

    def test_never_steals_from_windows_search_box(self):
        """第 198 项：Win11 搜索框/地址栏是自绘 DirectUI，只占窗口顶部一小条。

        用户原话「现在就是想用 windows 文件夹自带的搜索功能都被第二大脑劫持了」，
        所以除了类名，还要看**焦点面积**：小条不抢、大块（文件列表）才抢。"""
        import typewatch as tw
        self.assertFalse(tw.should_capture(0x41, False, False, False,
                                           'CabinetWClass', 'DirectUIHWND', True, 0.05, False))
        self.assertFalse(tw.should_capture(0x41, False, False, False,
                                           'CabinetWClass', 'DirectUIHWND', True, 0.0, False))
        self.assertTrue(tw.should_capture(0x41, False, False, False,
                                          'CabinetWClass', 'DirectUIHWND', True, 0.92, False))
        # 有输入光标＝正在文本里编辑，一律放过
        self.assertFalse(tw.should_capture(0x41, False, False, False,
                                           'CabinetWClass', 'DirectUIHWND', True, 0.9, True))

    def test_buffer_release_roundtrip(self):
        import typewatch as tw
        w = tw.TypeWatch(lambda ev: None, lambda: True)
        w._buf = [(0x41, 30), (0x42, 48)]
        w._hold_until = 9e9
        self.assertEqual([(0x41, 30), (0x42, 48)], w.release())
        self.assertEqual([], w._buf)
        self.assertEqual(0.0, w._hold_until)


class ScopeSearchTest(unittest.TestCase):
    """第 197 项：在「当前文件夹」里搜（不依赖索引，刚建的文件也能搜到）。"""

    def test_finds_inside_folder_without_index(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, '2026年度报告.docx').write_text('x', encoding='utf-8')
            Path(d, '其他.txt').write_text('x', encoding='utf-8')
            sub = Path(d, '报告存档')
            sub.mkdir()
            Path(sub, '季度报告.docx').write_text('x', encoding='utf-8')
            names = [x['n'] for x in brain._scope_items(d, '报告')]
            self.assertIn('2026年度报告.docx', names)
            self.assertIn('季度报告.docx', names)   # 子目录里也算
            self.assertNotIn('其他.txt', names)
            # 名字前缀优先于名字中间命中
            self.assertEqual('报告存档', names[0])
            self.assertEqual('dir', brain._scope_items(d, '报告存档')[0]['t'])

    def test_rejects_empty_query_and_missing_root(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual([], brain._scope_items(d, ''))
            self.assertEqual([], brain._scope_items(d, '   '))
            self.assertEqual([], brain._scope_items(os.path.join(d, 'nope'), 'x'))
            self.assertEqual([], brain._scope_items(d, '.'))


class AliveWindowTest(unittest.TestCase):
    """第 199 项：关掉的文件夹不能再算「当前打开」。"""

    def tearDown(self):
        quick_switch.set_backend(None)
        quick_switch._shell_windows = self._orig

    def setUp(self):
        self._orig = quick_switch._shell_windows

    def test_dead_hwnd_is_not_alive(self):
        fb = FakeBackend()
        fb.top = [(11, 'CabinetWClass', '活着', os.getpid() + 9)]
        quick_switch.set_backend(fb)
        self.assertTrue(quick_switch._alive(11))
        self.assertFalse(quick_switch._alive(99))   # 窗口已销毁
        self.assertFalse(quick_switch._alive(0))

    def test_closed_folder_dropped_from_open_folders(self):
        """Shell 集合里残留了一条已销毁的窗口：不能出现在推荐里。"""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            fb = FakeBackend()
            fb.top = [(21, 'CabinetWClass', '还开着', os.getpid() + 9)]
            quick_switch.set_backend(fb)
            quick_switch._shell_windows = lambda: [(21, a), (404, b)]
            self.assertEqual([a], quick_switch.open_folders())

    def test_mini_tooltips_are_not_hardcoded_chinese(self):
        """第 280 项：用户「这三个按钮的描述都写死了中文硬编码」（截图指截图/剪贴板/翻译三个图标）。

        模板里只能有 __TIP_*__ 占位符，中文原文一个都不许留在模板里。
        """
        for needle in ('title="__TIP_SNIP__"', 'title="__TIP_CLIP__"', 'title="__TIP_TRANS__"'):
            self.assertIn(needle, brain.MINI_TPL, needle)
        # 只盯「title= 里写死中文」这种形态；MINI_L 词典里的文案本来就该有中文
        for banned in ('title="屏幕截图（松手即复制）"', 'title="剪贴板历史"', 'title="聚合翻译"',
                       'title="点击复制到剪贴板"', 'title="点击复制译文"',
                       'title="点击立即翻译 (或按 Enter)"'):
            self.assertNotIn(banned, brain.MINI_TPL, '模板里还留着手写中文：' + banned)

    def test_mini_tips_cover_all_five_languages(self):
        """五种界面语言都得有基础文案，且非中文界面不许和中文一模一样（不许照抄占位）。

        第 281 项后 MINI_TIPS 不只管三个按钮的悬停说明，还兼管快搜窗剪贴板面板的标签
        （cb_* 与 trans_target），所以这里只钉「至少要有哪些键」，多出来的键由
        test_ui_i18n_281 的占位符闭环去管。
        """
        keys = ('snip', 'clip', 'trans', 'run', 'copy_item', 'copy_text',
                'cb_title', 'cb_all', 'cb_fav', 'cb_text', 'cb_image', 'cb_file',
                'cb_clear', 'cb_empty', 'cb_load_fail', 'cb_pin_add', 'cb_pin_del',
                'trans_target')
        for lang in ('zh-CN', 'en', 'es', 'pt', 'hi'):
            tips = brain.MINI_TIPS.get(lang) or {}
            missing = [k for k in keys if k not in tips]
            self.assertEqual([], missing, lang)
            for k in keys:
                self.assertTrue(str(tips[k]).strip(), '%s / %s 是空的' % (lang, k))
        zh = brain.MINI_TIPS['zh-CN']
        for lang in ('en', 'es', 'pt', 'hi'):
            tips = brain.MINI_TIPS[lang]
            for k in ('snip', 'clip', 'trans', 'cb_title', 'cb_empty', 'cb_pin_add'):
                self.assertNotEqual(zh[k], tips[k], '%s 的 %s 还是中文原文' % (lang, k))

    def test_mini_tips_pick_by_lang_code(self):
        """'zh' / 'zh-cn' 都归一成 zh-CN；'en' 拿英文；不认识的按中文退路。"""
        self.assertEqual(brain.MINI_TIPS['zh-CN']['snip'], brain._mini_tips('zh')['snip'])
        self.assertEqual(brain.MINI_TIPS['zh-CN']['snip'], brain._mini_tips('zh-CN')['snip'])
        self.assertEqual(brain.MINI_TIPS['en']['snip'], brain._mini_tips('en')['snip'])
        self.assertEqual(brain.MINI_TIPS['zh-CN']['snip'], brain._mini_tips('xx')['snip'])


if __name__ == '__main__':
    unittest.main()
