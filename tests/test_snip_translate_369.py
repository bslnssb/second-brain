# -*- coding: utf-8 -*-
"""第 369 项：截图翻译三入口（Ctrl+点相机 / 右键长按菜单 / 快搜窗悬停浮层）+ 原位译文卡片。"""
import inspect
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import snip_ocr
import snip_overlay
import app_window
import brain


def read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


BRAIN_SRC = read('brain.py')
I18N_SRC = read(os.path.join('web', 'src', 'lib', 'i18n.ts'))
TITLEBAR_SRC = read(os.path.join('web', 'src', 'ui', 'titlebar.tsx'))
APP_SRC = read(os.path.join('web', 'src', 'App.tsx'))


class SnipTranslateBackendTest(unittest.TestCase):
    def test_trigger_accepts_translate(self):
        sig = inspect.signature(snip_ocr.trigger_snip)
        self.assertIn('do_translate', sig.parameters)
        self.assertFalse(sig.parameters['do_translate'].default)

    def test_state_has_translate_fields(self):
        for k in ('do_translate', 'dst_text', 'dst_engine', 'rect'):
            self.assertIn(k, snip_ocr._STATE)

    def test_overlay_records_last_rect(self):
        self.assertTrue(hasattr(snip_overlay, 'LAST_RECT'))
        with open(os.path.join(ROOT, 'snip_overlay.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('LAST_RECT = (int(self.Left + x)', src)

    def test_route_accepts_translate_flag(self):
        self.assertIn("req.get('translate')", BRAIN_SRC)
        self.assertIn("qs['translate']", BRAIN_SRC)
        self.assertIn('do_translate=bool(do_tr)', BRAIN_SRC)

    def test_api_signatures(self):
        for cls in ('Api', 'MiniApi'):
            fn = getattr(getattr(app_window, cls), 'trigger_snip')
            self.assertIn('translate', inspect.signature(fn).parameters, cls)

    def test_translate_mode_skips_clipboard(self):
        # 翻译分支必须在写剪贴板之前 return，否则翻译会顺手污染剪贴板历史
        src = read('snip_ocr.py')
        i_tr = src.index('_run_translate_snip(cropped, on_done)')
        i_cb = src.index('put_image_to_clipboard(cropped)')
        self.assertLess(i_tr, i_cb)


class SnipCardTest(unittest.TestCase):
    def test_card_module(self):
        import snip_card
        self.assertTrue(hasattr(snip_card, 'show_card'))
        self.assertTrue(callable(snip_card.show_card))

    def test_card_has_no_webview(self):
        src = read('snip_card.py')
        self.assertNotIn('webview', src)
        self.assertIn('WinForms', src)


class MiniHoverEntryTest(unittest.TestCase):
    def test_hover_popup_markup(self):
        # 第 369 项修正：胶囊挂在相机键自己的 .snipwrap 上，不再挂整排按钮
        for token in ('actwrap', 'act-pop', 'btnSnipTr', 'id="actWrap"', 'id="snipWrap"'):
            self.assertIn(token, BRAIN_SRC)

    def test_hover_only_on_snip_button(self):
        # 悬停只认 #snipWrap（相机键），否则鼠标划过「详情 / 搜索」也会冒胶囊
        self.assertIn("document.getElementById('snipWrap')", BRAIN_SRC)
        self.assertIn('setTimeout(function(){ snipWrapEl.classList.add', BRAIN_SRC)
        self.assertIn("}, 600);", BRAIN_SRC)
        # 浮层绝对定位：出现时不挤动输入框和按钮
        self.assertIn('.act-pop{display:none;position:absolute', BRAIN_SRC)
        self.assertIn('.snipwrap.hover .act-pop{display:block', BRAIN_SRC)

    def test_mini_dosnip_passes_translate(self):
        self.assertIn('function doSnip(ocr, e, translate)', BRAIN_SRC)
        self.assertIn('api.trigger_snip(withOcr || withTr, withTr)', BRAIN_SRC)
        self.assertIn("body:JSON.stringify({ocr: withOcr || withTr, translate: withTr})", BRAIN_SRC)

    def test_mini_skips_panel_in_translate_mode(self):
        self.assertIn('if(st.do_translate) return;', BRAIN_SRC)


class TitlebarEntryTest(unittest.TestCase):
    def test_menu_and_long_press(self):
        self.assertIn('onSnipTranslate', TITLEBAR_SRC)
        self.assertIn('onContextMenu', TITLEBAR_SRC)
        self.assertIn('setTimeout(() => {', TITLEBAR_SRC)
        # 第 369 项修正：Ctrl+点击入口已砍掉（修饰键不可靠），别再复活
        self.assertNotIn('e.ctrlKey || e.metaKey', TITLEBAR_SRC)

    def test_plain_click_unchanged(self):
        # 普通点击仍然走原来的纯截图回调
        self.assertIn('onSnip?.()', TITLEBAR_SRC)
        self.assertIn('onSnip={triggerSnipPure}', APP_SRC)
        self.assertIn('onSnipTranslate={triggerSnipTranslate}', APP_SRC)

    def test_translate_result_not_shown_by_react(self):
        self.assertIn('if (st.do_translate) return', APP_SRC)

    def test_i18n_keys_five_langs(self):
        for key in ('snip_trans_btn', 'snip_menu_pure', 'snip_menu_trans', 'snip_menu_hint'):
            hits = len(re.findall(r'^\s*%s:' % key, I18N_SRC, re.M))
            self.assertEqual(5, hits, key)


class CardThemeAndEngineTest(unittest.TestCase):
    """卡片跟随主题配色 + 顶栏引擎可切换。"""

    def setUp(self):
        import snip_card
        self.card_mod = snip_card
        self.orig = snip_card._settings
        self.orig_live = snip_card._live_theme

    def tearDown(self):
        self.card_mod._settings = self.orig
        self.card_mod._live_theme = self.orig_live

    def _colors(self, palette, theme='auto', light=1):
        self.card_mod._settings = lambda: {'palette': palette, 'theme': theme}
        self.card_mod._live_theme = lambda: {}
        self.card_mod._system_dark = lambda: light == 0
        return self.card_mod.theme_colors()

    def test_live_theme_overrides_settings(self):
        """网页里能切配色但 settings.json 还是 null：卡片必须按网页报来的那套走。"""
        self.card_mod._settings = lambda: {'palette': None, 'theme': None}
        self.card_mod._system_dark = lambda: False
        self.card_mod._live_theme = lambda: {'palette': None, 'dark': False, 'bg': (240, 248, 243)}
        c = self.card_mod.theme_colors()
        self.assertEqual((240, 248, 243), self._rgb(c['bg']))   # 薄荷绿底色
        # 能反查出配色 id 时，连 accent 也一起对上
        self.card_mod._live_theme = lambda: {'palette': 'mint', 'dark': False, 'bg': (240, 248, 243)}
        c2 = self.card_mod.theme_colors()
        self.assertEqual((22, 140, 96), self._rgb(c2['accent']))
        self.assertEqual((240, 248, 243), self._rgb(c2['bg']))

    @staticmethod
    def _rgb(color):
        return (color.R, color.G, color.B)

    def test_light_palette_not_black(self):
        c = self._colors('light')
        self.assertFalse(c['dark'])
        self.assertEqual((255, 255, 255), self._rgb(c['card']))
        self.assertEqual((26, 34, 51), self._rgb(c['ink']))

    def test_dark_palette(self):
        c = self._colors('dark')
        self.assertTrue(c['dark'])
        self.assertEqual((31, 35, 43), self._rgb(c['card']))   # index.css 里深色 card 就是这三个数

    def test_palette_vars_from_settings_win(self):
        """第 370 项：settings.json 里有了整套颜色，卡片就用它 —— 不再靠 id 反查。

        这正是不让卡片再变白的根因级修法：网页报什么，原生窗口就显示什么，
        用户自定义 7 个变量（对不上任何预设）时也一样。
        """
        self.card_mod._settings = lambda: {
            'palette': 'custom',
            'palette_dark': False,
            'palette_vars': {'bg': '251 247 240', 'card': '255 253 248', 'ink': '40 36 30',
                             'sub': '120 110 96', 'line': '230 222 210', 'accent': '200 120 40'},
        }
        self.card_mod._live_theme = lambda: {}          # 网页还没报深浅也不影响
        self.card_mod._system_dark = lambda: True       # 系统深色也不该压过用户自选
        c = self.card_mod.theme_colors()
        self.assertEqual((251, 247, 240), self._rgb(c['bg']))
        self.assertEqual((200, 120, 40), self._rgb(c['accent']))
        self.assertFalse(c['dark'])

    def test_base_values_match_index_css(self):
        """基础三档必须和 index.css 逐值一致 —— 抄错一个，卡片就和网页差一档色。"""
        css = read('web/src/index.css')
        pairs = re.findall(r'--sb-(bg|card|ink|sub|line|accent):\s*(\d+ \d+ \d+)', css)
        # :root 在前、[data-theme=dark] 在后，顺序是文件里的顺序
        got = {}
        for key, val in pairs:
            got.setdefault(key, []).append(val)
        light = {k: v[0] for k, v in got.items()}
        dark = {k: v[-1] for k, v in got.items()}
        for k, v in self.card_mod.BASE_LIGHT.items():
            self.assertEqual(v, light[k], 'BASE_LIGHT[%s]' % k)
        for k, v in self.card_mod.BASE_DARK.items():
            self.assertEqual(v, dark[k], 'BASE_DARK[%s]' % k)


    def test_auto_follows_system_theme(self):
        self.assertFalse(self._colors('auto', light=1)['dark'])
        self.assertTrue(self._colors('auto', light=0)['dark'])
        # theme 显式指定时压过系统（palette 选 auto 的情况下）
        self.assertTrue(self._colors('auto', theme='dark', light=1)['dark'])
        self.assertFalse(self._colors('auto', theme='light', light=0)['dark'])
        # 自带深浅的配色不看 theme，等同于 palette.ts 的 paletteDark
        self.assertTrue(self._colors('dark', theme='light', light=1)['dark'])

    def test_soft_palette_accent_matches_palette_ts(self):
        c = self._colors('sakura')
        self.assertFalse(c['dark'])
        self.assertEqual((253, 243, 246), self._rgb(c['bg']))
        self.assertEqual((214, 90, 130), self._rgb(c['accent']))

    def test_card_has_no_hardcoded_black(self):
        src = read('snip_card.py')
        self.assertNotIn('Color.FromArgb(18, 18, 20)', src)  # 旧卡片写死的底色
        self.assertIn('theme_colors()', src)
        self.assertIn('PALETTES', src)

    def test_engine_switch_api(self):
        src = read('snip_card.py')
        self.assertIn('on_switch', src)
        self.assertIn('engBtn', src)
        sig = inspect.signature(self.card_mod.show_card)
        for name in ('engines', 'current_engine', 'on_switch'):
            self.assertIn(name, sig.parameters)

    def test_engine_choices_skips_dict_only(self):
        choices = snip_ocr._engine_choices()
        ids = [c['id'] for c in choices]
        self.assertTrue(ids, '至少要有一家免密钥引擎')
        # 词典接口（有道/百度/金山）整句翻不了，2026-11-20 起不再放进卡片下拉
        for bad in ('youdao', 'baidu', 'iciba'):
            self.assertNotIn(bad, ids)
        self.assertIn('google', ids)
        self.assertIn('tencent', ids)

    def test_pop_marks_current_engine(self):
        src = read('snip_card.py')
        self.assertIn("'✓ ' if cur else", src)

    def test_translate_one_signature(self):
        sig = inspect.signature(snip_ocr._translate_one)
        self.assertEqual(['src_text', 'engine_id', 'tgt'], list(sig.parameters))

    def test_switch_callback_wired_in_runner(self):
        src = read('snip_ocr.py')
        self.assertIn('def _translate_one(', src)
        self.assertIn('on_switch=', src)
        self.assertIn('state[\'src\']', src)


class PaletteVarsContractTest(unittest.TestCase):
    """第 370 项：配色只有一个真源 —— 网页把整套颜色报给后端，原生窗口只认这一份。"""

    def test_clean_palette_vars_accepts_six(self):
        clean = brain._clean_palette_vars({'bg': '240 248 243', 'card': '250,255,252',
                                           'ink': '28 52 44', 'sub': '92 118 106',
                                           'line': '214 234 222', 'accent': '22 140 96',
                                           'side': '110 134 122'})
        self.assertEqual('240 248 243', clean['bg'])
        self.assertEqual('250 255 252', clean['card'])   # 逗号也认
        self.assertNotIn('side', clean)                    # 只留卡片用得上的 6 个

    def test_clean_palette_vars_rejects_bad(self):
        ok = {'bg': '1 2 3', 'card': '1 2 3', 'ink': '1 2 3',
              'sub': '1 2 3', 'line': '1 2 3', 'accent': '1 2 3'}
        self.assertIsNone(brain._clean_palette_vars(None))
        self.assertIsNone(brain._clean_palette_vars({}))                     # 少键
        self.assertIsNone(brain._clean_palette_vars(dict(ok, bg='1 2 300')))  # 越界
        self.assertIsNone(brain._clean_palette_vars(dict(ok, bg='1 2')))      # 少一位
        self.assertIsNone(brain._clean_palette_vars(dict(ok, bg='a b c')))    # 非数字
        self.assertIsNone(brain._clean_palette_vars(dict(ok, bg=12)))         # 类型不对
        self.assertIsNotNone(brain._clean_palette_vars(ok))

    def test_settings_post_persists_palette_vars(self):
        src = BRAIN_SRC
        self.assertIn("if 'palette_vars' in req:", src)
        self.assertIn("patch['palette_vars'] = clean", src)
        self.assertIn("if 'palette_dark' in req:", src)

    def test_frontend_posts_whole_palette(self):
        # 只报 id 是不够的：自定义变量和 auto 跟系统深浅，id 都表达不出来
        self.assertIn('palette_vars', APP_SRC)
        self.assertIn('paletteVarsOf', APP_SRC)
        self.assertIn('paletteVarsOf', read(os.path.join('web', 'src', 'lib', 'palette.ts')))


class DevOverrideTest(unittest.TestCase):
    """第 370 项：免打包调试通道。"""

    def setUp(self):
        import dev_override
        self.mod = dev_override
        self.orig_dir = dev_override.dev_dir
        self.orig_allow = dev_override.ALLOW
        self.tmp = tempfile.mkdtemp(prefix='memex_devov_')
        dev_override.dev_dir = lambda: self.tmp
        sys.modules.pop('_devov_probe', None)

    def tearDown(self):
        self.mod.dev_dir = self.orig_dir
        self.mod.ALLOW = self.orig_allow
        sys.modules.pop('_devov_probe', None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_allowlist_is_narrow(self):
        # 允许随便覆盖 brain，等于给自己留一颗哑弹
        for name in ('snip_card', 'snip_ocr'):
            self.assertIn(name, self.mod.ALLOW)
        for name in ('brain', 'app_window', 'translator_ui'):
            self.assertNotIn(name, self.mod.ALLOW)

    def test_install_uses_override_file(self):
        name = 'snip_card'
        self.mod.ALLOW = self.mod.ALLOW + ()
        p = os.path.join(self.tmp, name + '.py')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('MARK = 20261120\n')
        before = sys.modules.get(name)
        try:
            self.assertTrue(self.mod.install(name))
            self.assertEqual(20261120, sys.modules[name].MARK)
            self.assertEqual(p, self.mod.status()[name])
        finally:
            if before is not None:
                sys.modules[name] = before
            else:
                sys.modules.pop(name, None)

    def test_bad_override_falls_back(self):
        name = 'snip_ocr'
        p = os.path.join(self.tmp, name + '.py')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('raise RuntimeError("boom")\n')
        before = sys.modules.get(name)
        self.assertFalse(self.mod.install(name))
        # 半成品必须撤掉：后面的 import 才拿得到内置那把好枪
        self.assertIs(before, sys.modules.get(name))

    def test_disallowed_name_ignored(self):
        p = os.path.join(self.tmp, 'brain.py')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('X = 1\n')
        self.assertFalse(self.mod.install('brain'))

    def test_boot_wired_at_startup(self):
        self.assertIn('dev_override', read('app_window.py'))
        self.assertIn('dev_override.install', read('snip_ocr.py'))


class CardUxFixTest(unittest.TestCase):
    """2026-11-20 用户视角验收：复制没结果时的脏数据、顶栏跳位、抢焦点、失败无原因、硬关。"""

    def test_copy_disabled_until_result(self):
        src = read('snip_card.py')
        # 加载中默认关掉复制键；_on_copy 自己再兜一层，绝不把「翻译中…」写进剪贴板
        self.assertIn('self._set_copy_enabled(False)', src)
        self.assertIn('if not self._dst:', src)
        self.assertNotIn("text = self._dst if self._dst else self.txt.Text", src)
        # 出结果才打开
        self.assertIn('self._set_copy_enabled(bool(self._dst))', src)

    def test_head_layout_has_one_source(self):
        src = read('snip_card.py')
        self.assertIn('def _layout_head(self)', src)
        # 换引擎文字 / 换译成文字 / 改高度 都走同一个布局函数，且不再有第二套坐标
        self.assertGreaterEqual(len(re.findall(r'self\._layout_head\(\)', src)), 3)
        self.assertNotIn('self.ClientSize.Width - 32', src)
        self.assertNotIn('self.ClientSize.Width - 36 - self.engBtn.Width', src)

    def test_dwell_countdown_and_hover_pause(self):
        src = read('snip_card.py')
        self.assertIn('DWELL_SEC', src)
        self.assertIn('self.auto.Interval = 1000', src)      # 每秒倒计时，不再是 60 秒硬关
        self.assertIn('悬停暂停计时', src)
        self.assertIn('self._reset_dwell()', src)             # 有结果时重新给足时间
        self.assertIn('还剩', src)

    def test_focus_not_stolen(self):
        src = read('snip_card.py')
        self.assertIn('GetForegroundWindow', src)
        self.assertIn('SetForegroundWindow', src)
        self.assertIn('def _restore_focus(self)', src)
        sig = inspect.signature(self._form_cls().__init__)
        self.assertIn('prev_fg', sig.parameters)

    @staticmethod
    def _form_cls():
        import snip_card
        return snip_card.SnipCardForm

    def test_failure_reason_plumbed(self):
        """整句失败时要把各家原因带回来 —— 卡片上只写「没返回译文」等于什么都没说。"""
        import translator
        orig = translator.translate_text

        def fake(text, **kw):
            return {'results': [], 'skipped': [{'engine': 'Google', 'code': 'offline'}]}

        translator.translate_text = fake
        try:
            dst, eng, eid, why = snip_ocr._pick_translation('hello world')
        finally:
            translator.translate_text = orig
        self.assertEqual('', dst)
        self.assertIn('Google', why)
        self.assertIn('连不上', why)

    def test_copy_state_is_visible(self):
        # 光 Enabled=False 外观还是蓝底白字，用户照点不误 —— 必须连底色一起换
        src = read('snip_card.py')
        self.assertIn('def _set_copy_enabled(self, on)', src)
        self.assertIn("self.btnCopy.BackColor = self.C['line']", src)

    def test_engine_id_shown_as_name(self):
        # 传进来的若是 'mymemory' 这种 id，顶栏要显示 MyMemory，而不是小写 id
        src = read('snip_card.py')
        self.assertIn('self._engine_name(nm) or nm', src)

    def test_error_path_still_sets_source(self):
        # 翻译挂了也要能看原文，不能让「原文」按钮变成死键
        src = read('snip_ocr.py')
        self.assertIn('card.set_source(src)', src)
        self.assertIn('card.set_error(err or why', src)


class CardUserRound2Test(unittest.TestCase):
    """2026-11-20 第二轮用户视角：重试死路 / Esc 假提示 / 目标语言 / 失败出口 /
    长文读不全 / 拖动钉住 / 历史回看 / 原文改字重翻 / 分步加载 / 切引擎缓存。"""

    def test_retry_same_engine_not_blocked(self):
        src = read('snip_card.py')
        # 以前 `eid == self._cur_id` 直接 return，失败后点当前那家什么都不发生
        self.assertNotIn('if not eid or eid == self._cur_id:', src)
        self.assertIn('def _fire_switch(', src)
        self.assertIn('self._fire_switch(eid, None)', src)

    def test_esc_works_without_focus(self):
        src = read('snip_card.py')
        # 焦点还回去之后卡片收不到按键 —— 必须挂只观察 Esc 的钩子
        self.assertIn('SetWindowsHookExW', src)
        self.assertIn('UnhookWindowsHookEx', src)
        self.assertIn('_install_esc_hook', src)
        self.assertIn('CallNextHookEx', src)       # 不吞键，别的程序照常响应 Esc

    def test_target_language_choices(self):
        import snip_ocr
        ids = [t['id'] for t in snip_ocr.TGT_CHOICES]
        self.assertEqual(['auto', 'zh-CN', 'en', 'ja'], ids)
        src = read('snip_card.py')
        self.assertIn('btnTgt', src)
        self.assertIn('self._pick_tgt', src)
        # on_switch 要带目标语言，不然换了语言还是按旧语言翻
        self.assertIn('self._on_switch(eid, self._tgt, text)', src)

    def test_target_follows_settings(self):
        import brain
        import snip_ocr
        orig = brain._settings_read
        brain._settings_read = lambda: {'trans_target_lang': 'ja'}
        try:
            self.assertEqual('ja', snip_ocr._tgt_lang('auto'))
            self.assertEqual('en', snip_ocr._tgt_lang('en'))
        finally:
            brain._settings_read = orig

    def test_pick_translation_honours_tgt(self):
        import translator
        seen = {}
        orig = translator.translate_text

        def fake(text, src_lang='auto', tgt_lang='auto', engines=None):
            seen['tgt'] = tgt_lang
            return {'results': [{'text': '你好', 'engine': 'G', 'engine_id': 'google'}]}

        translator.translate_text = fake
        try:
            snip_ocr._pick_translation('hello', 'zh-CN')
        finally:
            translator.translate_text = orig
        self.assertEqual('zh-CN', seen.get('tgt'))

    def test_error_state_has_two_exits(self):
        src = read('snip_card.py')
        self.assertIn('self.btnRetry.Visible = err or src_mode', src)
        self.assertIn('self.btnAlt.Visible = err', src)
        self.assertIn('def _on_retry(', src)
        self.assertIn('self._set_state(\'error\')', src)

    def test_long_text_scrollable(self):
        src = read('snip_card.py')
        self.assertIn('def _set_scrollbars(', src)
        self.assertIn('ScrollBars.Vertical', src)
        # 装不下时提示要先说「可滚动」，不能只数倒计时
        self.assertIn("'可滚动 · ' + base", src)

    def test_drag_and_pin(self):
        src = read('snip_card.py')
        self.assertIn('def _head_mouse_down(', src)
        self.assertIn('def _head_mouse_move(', src)
        self.assertIn('def _on_pin(', src)
        self.assertIn('self._pinned', src)

    def test_history_nav(self):
        src = read('snip_card.py')
        self.assertIn('HISTORY_MAX', src)
        self.assertIn('def push_history(', src)
        self.assertIn('def _show_hist(', src)
        self.assertIn('btnPrev', src)

    def test_source_editable_then_retranslate(self):
        src = read('snip_card.py')
        self.assertIn('self.txt.ReadOnly = False', src)     # 看原文时可改
        self.assertIn('self._fire_switch(self._cur_id, text)', src)

    def test_loading_has_ocr_stage(self):
        src = read('snip_ocr.py')
        self.assertIn('识别文字中…', src)
        self.assertIn("card.set_loading('翻译中…')", src)

    def test_engine_results_cached(self):
        src = read('snip_card.py')
        self.assertIn('self._cache[eid]', src)
        self.assertIn('hit = self._cache.get(eid)', src)


if __name__ == '__main__':
    unittest.main()
