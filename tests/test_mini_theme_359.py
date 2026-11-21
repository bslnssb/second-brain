# -*- coding: utf-8 -*-
"""第 359 项：快搜窗「白残留」的两条防线。

用户反馈：把配色换成 cyber（深青）之后，快搜窗右侧还挂着一条白边。
根因有两条，缺一条都会复发：

  1) **网页之内**：MINI_TPL 里 `#cmenu`、`.about`、`.pbtn`、底栏顶边线这些
     都是照着白底写的（`background:#fff` / `#f1f2f6` / `#a7aebc`…）。深色配色
     下右键菜单、关于弹窗、底栏那根线全是白的。
  2) **网页之外**：快搜窗是 frameless + `soft_shadow()` 扩边框 +
     `install_child_hit_guard()` 让 WebView2 四周让出几像素，窗口最外圈
     （DPI 150% 实测 8~11px）画的不是网页，而是 WinForms 的 BackColor；
     建窗时写死 '#FFFFFF'，CSS 怎么改都盖不到它。

所以这里同时断言：
  * 模板里不再有那批浅色常量，弹窗 / 菜单 / 按钮 / 滚动条都走 --sb-* 变量；
  * 建窗底色来自 `ui_bg_hex()`，并且有 `set_window_background()` 在
    set_dark / show_mini 里跟着配色刷。
"""
import pathlib
import re
import unittest

import app_window
import brain

SRC = pathlib.Path(app_window.__file__).read_text(encoding='utf-8')

MINI = brain.MINI_TPL

# 这套浅色常量是「白底时代」留下的，深色配色下会直接发白
BANNED_COLORS = ('#fff3b8', '#fff2b8', '#f1eefc', '#f6f7fb',
                 '#f1f2f6', '#e8eaf3', '#eef1fb', '#3b5bfd',
                 '#a7aebc', '#c2c8d4', '#bcc3d0', '#8b93a5')


class MiniThemeTest(unittest.TestCase):
    def test_no_light_only_colors_left(self):
        for c in BANNED_COLORS:
            self.assertNotIn(c, MINI, '快搜窗模板里还留着白底专用色 %s' % c)
        self.assertNotIn('background:#fff', MINI.replace(' ', ''))

    def test_context_menu_follows_palette(self):
        m = re.search(r'#cmenu\{[^}]*\}', MINI)
        self.assertIsNotNone(m, '#cmenu 样式没了')
        self.assertIn('rgb(var(--sb-card)', m.group(0))
        self.assertIn('rgb(var(--sb-line)', MINI)

    def test_about_dialog_follows_palette(self):
        m = re.search(r'\.about\{[^}]*\}', MINI)
        self.assertIsNotNone(m, '.about 样式没了')
        self.assertIn('rgb(var(--sb-card))', m.group(0))
        for needle in ('.about .aname{', '.about .ameta{', '.about .anote{'):
            seg = MINI[MINI.find(needle):MINI.find(needle) + 160]
            self.assertIn('--sb-', seg, needle + ' 还在用固定色')

    def test_pbtn_has_global_style(self):
        """按钮以前只在 .sb-foot 下有色，关于弹窗的「知道了」掉回系统白底。"""
        self.assertIn('.pbtn{font-size:12px', MINI)
        self.assertIn('.pbtn.blue{background:rgb(var(--sb-accent))', MINI)

    def test_footer_line_uses_vars(self):
        self.assertNotIn('border-top:1px solid #f1f2f6', MINI)
        self.assertIn('border-top:1px solid rgb(var(--sb-line))', MINI)

    def test_scrollbars_are_themed(self):
        """剪贴板 / 翻译结果两块会滚动，系统滚动条在深色配色下就是一条白轨。"""
        self.assertIn('.cb-list::-webkit-scrollbar-thumb', MINI)
        self.assertIn('.tb-results::-webkit-scrollbar-thumb', MINI)


class WindowBackgroundTest(unittest.TestCase):
    def test_mini_window_uses_theme_bg(self):
        self.assertIn('background_color=ui_bg_hex()', SRC)
        self.assertNotIn("background_color='#FFFFFF'", SRC, 'mini 建窗又写死白底了')

    def test_set_window_background_helper(self):
        self.assertTrue(callable(getattr(app_window, 'set_window_background', None)))
        # 没有窗口时必须安全返回，不能把 set_dark / show_mini 带崩
        self.assertFalse(app_window.set_window_background(None, (4, 40, 56)))

    def test_runtime_paths_repaint_window_bg(self):
        self.assertIn("set_window_background(self.mini, _bg_fix)", SRC)      # after_start
        self.assertIn('set_dark backcolor skipped', SRC)                     # set_dark
        self.assertIn("set_window_background(self.mini, _LAST_THEME.get('bg')", SRC)  # show_mini


if __name__ == '__main__':
    unittest.main()
