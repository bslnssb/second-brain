"""截图翻译的原地卡片（第 369 项）。

框选完把译文贴回选区原位：一张无边框、置顶的小卡片，不占快搜窗/标题栏空间。
纯 WinForms 原生窗口，没有独立 WebView —— 不开一次就多几十 MB，用户对后台开销零容忍。

第 369 项修正（2026-11-20）：① 配色跟随主题（settings.json 的 palette / theme，
与 web/src/lib/palette.ts 同一张表），不再固定黑底；② 顶栏加了「引擎 ▾」下拉，
可以就地切到别的翻译引擎看不同结果（百度 / 有道 / 腾讯 / DeepL…）。
"""

import threading

CARD_W_MIN = 320
CARD_W_MAX = 720
CARD_H = 250
# 卡片没人碰多久自己消失（秒）。有结果时重新计满，鼠标停在卡片上时暂停。
DWELL_SEC = 90

# 历史留最近几条：连翻几张图时能往回看，只存文本，不留图片
HISTORY_MAX = 5
_HISTORY = []

# 卡片顶栏「译成 ▾」的默认选项（正常由 snip_ocr.TGT_CHOICES 传进来）
DEFAULT_TGT = [
    {'id': 'auto', 'name': '跟随设置'},
    {'id': 'zh-CN', 'name': '中文'},
    {'id': 'en', 'name': '英文'},
    {'id': 'ja', 'name': '日文'},
]


def push_history(src, dst, engine='', engine_id=''):
    """把一条成功的截图翻译记进历史（跨卡片，进程内有效）。"""
    if not dst:
        return
    ent = {'src': src or '', 'dst': dst, 'engine': engine or '', 'engine_id': engine_id or ''}
    if _HISTORY and _HISTORY[-1].get('dst') == dst and _HISTORY[-1].get('src') == ent['src']:
        return
    _HISTORY.append(ent)
    del _HISTORY[:-HISTORY_MAX]

try:
    import clr
    clr.AddReference('System.Windows.Forms')
    clr.AddReference('System.Drawing')
    import System
    from System.Windows.Forms import (
        Form, FormBorderStyle, FormStartPosition, Label, TextBox, Button,
        Panel, Screen, Keys, Application, DialogResult, ScrollBars, BorderStyle,
        Timer, FlatStyle, DockStyle, AnchorStyles, PaintEventArgs, TextRenderer,
        MouseButtons, Cursor
    )
    from System.Drawing import Color, Font, FontStyle, Point, Size, ContentAlignment, Rectangle
    HAS_WINFORMS = True
except Exception:
    HAS_WINFORMS = False


# ---- 配色：必须和 web/src/lib/palette.ts 的 PALETTES 一一对应 ----
# (dark, vars) ；vars=None 表示用基础三档（index.css 的默认值，这里逐值照抄）
BASE_LIGHT = {'bg': '244 246 250', 'card': '255 255 255', 'ink': '26 34 51',
              'sub': '92 104 128', 'line': '226 231 240', 'accent': '37 99 235'}
BASE_DARK = {'bg': '24 27 33', 'card': '31 35 43', 'ink': '232 236 244',
             'sub': '154 164 182', 'line': '52 58 70', 'accent': '110 141 248'}

# 卡片只关心这 6 个（side 是侧栏色，原生窗口用不到）
VAR_KEYS = ('bg', 'card', 'ink', 'sub', 'line', 'accent')

PALETTES = {
    'auto': (None, None),
    'light': (False, None),
    'dark': (True, None),
    'paper': (False, {'bg': '246 240 229', 'card': '255 252 245', 'ink': '58 48 36',
                      'sub': '118 103 82', 'line': '232 221 203', 'accent': '176 118 60'}),
    'amber': (False, {'bg': '248 237 220', 'card': '255 250 240', 'ink': '70 50 30',
                      'sub': '132 102 70', 'line': '238 220 196', 'accent': '190 110 40'}),
    'pure': (False, {'bg': '250 250 251', 'card': '255 255 255', 'ink': '20 22 26',
                     'sub': '105 112 124', 'line': '232 234 238', 'accent': '30 90 220'}),
    'mint': (False, {'bg': '240 248 243', 'card': '250 255 252', 'ink': '28 52 44',
                     'sub': '92 118 106', 'line': '214 234 222', 'accent': '22 140 96'}),
    'blue': (False, {'bg': '240 245 252', 'card': '250 253 255', 'ink': '26 40 64',
                     'sub': '92 108 134', 'line': '214 226 242', 'accent': '44 104 200'}),
    'lilac': (False, {'bg': '245 242 252', 'card': '252 250 255', 'ink': '44 34 66',
                      'sub': '108 96 132', 'line': '228 220 244', 'accent': '116 84 210'}),
    'sakura': (False, {'bg': '253 243 246', 'card': '255 250 252', 'ink': '62 36 48',
                       'sub': '130 96 108', 'line': '244 219 228', 'accent': '214 90 130'}),
    'graphite': (True, {'bg': '30 31 34', 'card': '40 41 45', 'ink': '226 228 232',
                        'sub': '150 154 162', 'line': '58 60 66', 'accent': '150 160 180'}),
    'night': (True, {'bg': '18 24 38', 'card': '26 34 52', 'ink': '226 234 246',
                     'sub': '146 162 190', 'line': '44 56 82', 'accent': '84 140 246'}),
    'contrast': (True, {'bg': '0 0 0', 'card': '14 14 14', 'ink': '255 255 255',
                        'sub': '200 200 200', 'line': '70 70 70', 'accent': '255 214 10'}),
    'black': (True, {'bg': '8 8 9', 'card': '18 18 20', 'ink': '236 236 238',
                     'sub': '150 150 155', 'line': '44 44 48', 'accent': '120 130 250'}),
    'cyber': (True, {'bg': '4 40 56', 'card': '10 52 70', 'ink': '220 246 250',
                     'sub': '120 170 180', 'line': '32 58 66', 'accent': '34 200 220'}),
}


def _rgb(s):
    parts = [int(float(v)) for v in str(s).replace(',', ' ').split()]
    while len(parts) < 3:
        parts.append(0)
    return Color.FromArgb(max(0, min(255, parts[0])),
                          max(0, min(255, parts[1])),
                          max(0, min(255, parts[2])))


def _system_dark():
    """系统当前是不是深色（只认 Windows 的「应用模式」）。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
        try:
            v, _ = winreg.QueryValueEx(k, 'AppsUseLightTheme')
            return int(v) == 0
        finally:
            winreg.CloseKey(k)
    except Exception:
        return False


def _settings():
    try:
        import brain
        return brain._settings_read() or {}
    except Exception:
        return {}


def _live_theme():
    """前端 applyPalette 之后会把「深浅 + 底色」报给原生（Api.set_dark → _LAST_THEME）。

    设置里存的 palette 可能还是 null（等于 auto），但用户真正点的那套只在网页的
    localStorage 里。这里靠底色反查配色 id —— 12 套配色的 bg 各不相同，能对上；
    用户再自定义了 7 个变量（sb_vars）时对不上，就直接用报上来的底色。
    """
    try:
        import app_window
        lt = getattr(app_window, '_LAST_THEME', None) or {}
    except Exception:
        return None
    bg = lt.get('bg')
    dark = lt.get('dark')
    if not bg:
        return {'palette': None, 'dark': dark, 'bg': None}
    rgb = tuple(int(v) for v in tuple(bg)[:3])
    pid = None
    for k, (dk, vars_) in PALETTES.items():
        src = vars_ or (BASE_DARK if dk else BASE_LIGHT)
        want = tuple(int(v) for v in str(src['bg']).split())
        if want == rgb:
            pid = k
            break
    return {'palette': pid, 'dark': dark, 'bg': rgb}


def theme_colors():
    """按用户选的配色算卡片用色。返回 dict(bg, card, ink, sub, line, accent, accent_ink, dark)。

    取色顺序（越靠前越可信）：
    1. `settings.json` 的 `palette_vars` —— 网页在每次配色/系统深浅变化时报上来的**整套具体值**，
       这是唯一真源（自定义变量、auto 跟系统都能表达）；
    2. `_live_theme()` 反查（老版本只报「深浅 + 底色」，留着当兜底，别让旧实例变白卡）；
    3. `settings.json` 的 palette id + 系统深浅。
    """
    st = _settings()
    pv = st.get('palette_vars')
    if isinstance(pv, dict) and pv and all(isinstance(pv.get(k), str) for k in VAR_KEYS):
        base = {k: pv[k] for k in VAR_KEYS}
        c = {k: _rgb(v) for k, v in base.items()}
        dark = st.get('palette_dark')
        c['dark'] = _system_dark() if dark is None else bool(dark)
        c['accent_ink'] = Color.White if _luma(c['accent']) < 0.62 else Color.FromArgb(20, 20, 20)
        return c

    live = _live_theme() or {}
    pid = live.get('palette')
    if pid not in PALETTES:
        pid = str(st.get('palette') or 'auto')
    theme = str(st.get('theme') or 'auto')
    if pid not in PALETTES:
        pid = 'auto'
    dark_flag, vars_ = PALETTES[pid]
    if pid == 'auto':
        dark = _system_dark() if theme in ('', 'auto') else (theme == 'dark')
        if live.get('dark') is not None:
            dark = bool(live['dark'])
    else:
        dark = bool(dark_flag)
    base = dict(BASE_DARK if dark else BASE_LIGHT)
    if vars_:
        base.update(vars_)
    c = {k: _rgb(v) for k, v in base.items()}
    # 自定义底色（网页自己那套变量）：底色对不上任何预设时按报上来的色走
    bg = live.get('bg')
    if bg and not vars_ and pid in ('auto', 'light', 'dark'):
        c['bg'] = Color.FromArgb(255, int(bg[0]), int(bg[1]), int(bg[2]))
    c['dark'] = dark
    c['accent_ink'] = Color.White if _luma(c['accent']) < 0.62 else Color.FromArgb(20, 20, 20)
    return c


def _luma(color):
    return (0.2126 * color.R + 0.7152 * color.G + 0.0722 * color.B) / 255.0


if HAS_WINFORMS:
    class SnipCardForm(Form):
        def __init__(self, rect, loading_text='翻译中…', engines=None,
                     current_engine='', on_switch=None, prev_fg=0,
                     tgt_choices=None, tgt='auto', engine_id=''):
            super().__init__()
            self.rect = rect or (0, 0, 400, 100)
            self._prev_fg = prev_fg
            self._src = ''
            self._dst = ''
            self._pending = None
            self._showing = 'dst'
            self._engines = list(engines or [])
            self._cur_id = str(engine_id or '')
            self._on_switch = on_switch
            self._pop = None
            self._pop_kind = ''
            self._left = DWELL_SEC
            self._state = 'loading'
            # 换引擎时把上一家的结果记着：切回去不用再等一次网络（第 371 项）
            self._cache = {}
            self._hist_pos = -1
            self._scroll = False
            self._pinned = False
            self._closed = False
            self._drag = None
            self._hook = None
            self._hook_proc = None
            self._hook_tid = 0
            self._tgt = str(tgt or 'auto')
            self._tgt_choices = list(tgt_choices or DEFAULT_TGT)

            self.C = theme_colors()

            self.FormBorderStyle = getattr(FormBorderStyle, 'None')
            self.StartPosition = FormStartPosition.Manual
            self.TopMost = True
            self.ShowInTaskbar = False
            try:
                # 关掉 AutoScaleMode：尺寸全按我们算好的像素给，150% DPI 下才不会把下拉压扁
                self.AutoScaleMode = getattr(__import__('System.Windows.Forms',
                                                       fromlist=['AutoScaleMode']).AutoScaleMode, 'None')
            except Exception:
                pass
            self.BackColor = self.C['bg']
            self.KeyPreview = True
            self.Padding = System.Windows.Forms.Padding(0)
            try:
                self.SetStyle(System.Windows.Forms.ControlStyles.OptimizedDoubleBuffer, True)
            except Exception:
                pass

            w = int(self.rect[2]) if len(self.rect) > 2 else 400
            self.ClientSize = Size(max(CARD_W_MIN, min(CARD_W_MAX, w)), CARD_H)
            self._place()

            # 顶部标题条
            self.head = Panel()
            self.head.Dock = DockStyle.Top
            self.head.Height = 34
            self.head.BackColor = self.C['bg']
            self.Controls.Add(self.head)

            # 标题条底部 1px 分隔线（无边框窗口靠它把头和正文分开）
            self.headline = Panel()
            self.headline.Dock = DockStyle.Bottom
            self.headline.Height = 1
            self.headline.BackColor = self.C['line']
            self.head.Controls.Add(self.headline)

            self.lbl = Label()
            self.lbl.Text = '截图翻译 · 译文'
            self.lbl.ForeColor = self.C['ink']
            self.lbl.Font = Font('Microsoft YaHei UI', 8.5, FontStyle.Bold)
            self.lbl.AutoSize = False
            self.lbl.Location = Point(10, 6)
            self.lbl.Size = Size(self.ClientSize.Width - 200, 20)
            self.lbl.TextAlign = ContentAlignment.MiddleLeft
            self.head.Controls.Add(self.lbl)
            # 拖 + 双击钉住：卡片贴在选区下方会挡住你要对照的内容，得能挪开（第 371 项）
            for grip in (self.head, self.lbl):
                try:
                    grip.MouseDown += self._head_mouse_down
                    grip.MouseMove += self._head_mouse_move
                    grip.MouseUp += self._head_mouse_up
                    grip.DoubleClick += self._on_pin
                except Exception:
                    pass

            self.btnClose = Label()
            self.btnClose.Text = ''
            self.btnClose.BackColor = self.C['bg']
            self.btnClose.Size = Size(24, 24)
            self.btnClose.Location = Point(self.ClientSize.Width - 28, 4)
            self.btnClose.Cursor = System.Windows.Forms.Cursors.Hand
            self.btnClose.Click += self._on_close
            # 用画的：用字体渲染 '×' 在 150% 缩放下会被磨成一小段「⌄」，用户根本认不出是关闭
            self.btnClose.Paint += self._paint_close
            self.head.Controls.Add(self.btnClose)

            # 「译成」按钮：截图翻译最常见的缺项是「翻成什么语言」，以前只有引擎可选
            self.btnTgt = Button()
            self.btnTgt.FlatStyle = FlatStyle.Flat
            self.btnTgt.FlatAppearance.BorderSize = 1
            self.btnTgt.FlatAppearance.BorderColor = self.C['line']
            self.btnTgt.BackColor = self.C['bg']
            self.btnTgt.ForeColor = self.C['ink']
            self.btnTgt.Font = Font('Microsoft YaHei UI', 9)
            self.btnTgt.TextAlign = ContentAlignment.MiddleLeft
            self.btnTgt.Cursor = System.Windows.Forms.Cursors.Hand
            self.btnTgt.AutoSize = False
            self.btnTgt.Padding = System.Windows.Forms.Padding(8, 0, 8, 0)
            self.btnTgt.Click += self._toggle_tgt_pop
            self._set_tgt_text(self._tgt)
            self.head.Controls.Add(self.btnTgt)

            # 引擎切换按钮（顶栏，贴近关闭键左边）
            self.engBtn = Button()
            self.engBtn.FlatStyle = FlatStyle.Flat
            self.engBtn.FlatAppearance.BorderSize = 1
            self.engBtn.FlatAppearance.BorderColor = self.C['line']
            self.engBtn.BackColor = self.C['bg']
            self.engBtn.ForeColor = self.C['ink']
            self.engBtn.Font = Font('Microsoft YaHei UI', 9)
            self.engBtn.TextAlign = ContentAlignment.MiddleLeft
            self.engBtn.Cursor = System.Windows.Forms.Cursors.Hand
            # 不用 AutoSize：它在 150% 缩放下给出的高度（实测 38px）比标题条还高，
            # 按钮会被标题条裁掉一半。改成 TextRenderer 量宽 + 按字体算高，尺寸自己算。
            self.engBtn.AutoSize = False
            self.engBtn.Padding = System.Windows.Forms.Padding(8, 0, 10, 0)
            self.engBtn.Click += self._toggle_pop
            self._set_eng_text(current_engine or '自动')
            if not self._engines:
                self.engBtn.Enabled = False
                self.engBtn.Visible = False
            self.head.Controls.Add(self.engBtn)

            # 正文
            self.txt = TextBox()
            self.txt.Multiline = True
            self.txt.ReadOnly = True
            self.txt.ScrollBars = getattr(ScrollBars, 'None')
            self.txt.BorderStyle = getattr(BorderStyle, 'None')
            self.txt.BackColor = self.C['bg']
            self.txt.ForeColor = self.C['ink']
            self.txt.Font = Font('Microsoft YaHei UI', 10.5)
            self.txt.WordWrap = True
            self.txt.Text = loading_text
            self.txt.Location = Point(12, 40)
            self.txt.Size = Size(self.ClientSize.Width - 24, self.ClientSize.Height - 86)
            self.txt.Anchor = (AnchorStyles.Top | AnchorStyles.Left |
                               AnchorStyles.Right | AnchorStyles.Bottom)
            self.Controls.Add(self.txt)

            # 滚动条：深色主题挂 DarkMode_Explorer（第 359 项的白轨教训）
            try:
                if self.C['dark']:
                    self._dark_scrollbar(self.txt.Handle)
            except Exception:
                pass

            # 底部按钮
            self.btnToggle = Button()
            self.btnToggle.Text = '原文'
            self.btnToggle.FlatStyle = FlatStyle.Flat
            self.btnToggle.FlatAppearance.BorderColor = self.C['line']
            self.btnToggle.FlatAppearance.BorderSize = 1
            self.btnToggle.BackColor = self.C['bg']
            self.btnToggle.ForeColor = self.C['sub']
            self.btnToggle.Font = Font('Microsoft YaHei UI', 9)
            self.btnToggle.Size = Size(64, 26)
            self.btnToggle.Location = Point(12, self.ClientSize.Height - 38)
            self.btnToggle.Anchor = (AnchorStyles.Bottom | AnchorStyles.Left)
            self.btnToggle.Click += self._on_toggle
            self.Controls.Add(self.btnToggle)

            self.btnCopy = Button()
            self.btnCopy.Text = '复制译文'
            self.btnCopy.FlatStyle = FlatStyle.Flat
            self.btnCopy.FlatAppearance.BorderSize = 0
            self.btnCopy.BackColor = self.C['accent']
            self.btnCopy.ForeColor = self.C['accent_ink']
            self.btnCopy.Font = Font('Microsoft YaHei UI', 9, FontStyle.Bold)
            self.btnCopy.Size = Size(96, 26)
            self.btnCopy.Location = Point(84, self.ClientSize.Height - 38)
            self.btnCopy.Anchor = (AnchorStyles.Bottom | AnchorStyles.Left)
            # 结果没回来之前不给点：早先点了会把「翻译中…」这三字直接写进剪贴板
            self._set_copy_enabled(False)
            self.btnCopy.Click += self._on_copy
            self.Controls.Add(self.btnCopy)

            # 失败时给两个出口：重试（同一条原文、当前引擎再来一次）/ 换一家。
            # 以前失败只能 Esc 重截整张图，等于让人重做一遍。
            self.btnRetry = Button()
            self.btnRetry.Text = '重试'
            self.btnRetry.FlatStyle = FlatStyle.Flat
            self.btnRetry.FlatAppearance.BorderSize = 0
            self.btnRetry.BackColor = self.C['accent']
            self.btnRetry.ForeColor = self.C['accent_ink']
            self.btnRetry.Font = Font('Microsoft YaHei UI', 9, FontStyle.Bold)
            self.btnRetry.Size = Size(84, 26)
            self.btnRetry.Cursor = System.Windows.Forms.Cursors.Hand
            self.btnRetry.Click += self._on_retry
            self.btnRetry.Visible = False
            self.Controls.Add(self.btnRetry)

            self.btnAlt = Button()
            self.btnAlt.Text = '换一家'
            self.btnAlt.FlatStyle = FlatStyle.Flat
            self.btnAlt.FlatAppearance.BorderColor = self.C['line']
            self.btnAlt.FlatAppearance.BorderSize = 1
            self.btnAlt.BackColor = self.C['bg']
            self.btnAlt.ForeColor = self.C['ink']
            self.btnAlt.Font = Font('Microsoft YaHei UI', 9)
            self.btnAlt.Size = Size(84, 26)
            self.btnAlt.Cursor = System.Windows.Forms.Cursors.Hand
            self.btnAlt.Click += self._open_eng_pop
            self.btnAlt.Visible = False
            self.Controls.Add(self.btnAlt)

            # 上一条 / 下一条：连翻几张图时往回看，不用重截
            self.btnPrev = self._nav_button('‹', self._on_hist_prev)
            self.btnNext = self._nav_button('›', self._on_hist_next)
            self.Controls.Add(self.btnPrev)
            self.Controls.Add(self.btnNext)

            self.hint = Label()
            self.hint.Text = 'Esc 关闭 · 还剩 %d 秒' % DWELL_SEC
            self.hint.ForeColor = self.C['sub']
            self.hint.Font = Font('Microsoft YaHei UI', 8)
            self.hint.AutoSize = False
            self.hint.Size = Size(self.ClientSize.Width - 200, 26)
            self.hint.Location = Point(190, self.ClientSize.Height - 36)
            self.hint.TextAlign = ContentAlignment.MiddleRight
            self.hint.Anchor = (AnchorStyles.Bottom | AnchorStyles.Right)
            self.Controls.Add(self.hint)

            # 跨线程改文字：调用方在 OCR/翻译线程里，直接改控件会崩，交给 WinForms 计时器落地
            self.timer = Timer()
            self.timer.Interval = 120
            self.timer.Tick += self._on_tick
            self.timer.Start()

            self.KeyDown += self._on_key
            self.Shown += self._on_shown
            self.FormClosed += self._on_form_closed
            self.Paint += self._on_paint
            # 自毁计时器：读得慢的人卡片不该「啪」一下没了 ——
            # 改成每秒倒计时、鼠标停在卡片上就暂停、有结果时重新给足时间，右下角实时显示剩余。
            self.auto = Timer()
            self.auto.Interval = 1000
            self.auto.Tick += self._on_autoclose
            self.auto.Start()

            # 底部排布 / 历史按钮初始状态都走同一套函数
            self._sync_nav()
            self._set_state('loading')
            self._layout_foot()

        # ---- 让无边框卡片有一圈 1px 描边（浅色主题下白卡片压在亮截图上会糊掉）----
        def _on_paint(self, sender, e):
            try:
                pen = System.Drawing.Pen(self.C['line'], 1)
                r = self.ClientRectangle
                e.Graphics.DrawRectangle(pen, 0, 0, r.Width - 1, r.Height - 1)
                pen.Dispose()
            except Exception:
                pass

        def _on_shown(self, sender, e):
            try:
                if self.C['dark']:
                    self._dark_scrollbar(self.txt.Handle)
                    self._dark_scrollbar(self.Handle)
            except Exception:
                pass
            # 卡片只是「看结果」的浮层，不该把用户正在敲字的窗口顶下去（输入法都会被切走）
            self._restore_focus()
            # 焦点还回去了，卡片自己收不到 Esc —— 挂个只观察 Esc 的钩子，别让提示变成假话
            self._install_esc_hook()

        def _restore_focus(self):
            """弹出后把前台还给原来的窗口：卡片自己仍是 TopMost，用户点它随时能拿回焦点。"""
            prev = int(getattr(self, '_prev_fg', 0) or 0)
            if not prev:
                return
            try:
                import ctypes
                u = ctypes.windll.user32
                t = Timer()
                t.Interval = 150

                def tick(s, ev):
                    t.Stop()
                    try:
                        if u.IsWindow(ctypes.c_void_p(prev)):
                            u.SetForegroundWindow(ctypes.c_void_p(prev))
                    except Exception:
                        pass

                t.Tick += tick
                t.Start()
                self._focus_timer = t       # 留住引用，不然计时器会被 GC 掉
            except Exception:
                pass

        @staticmethod
        def _dark_scrollbar(hwnd):
            """给原生滚动条套深色主题（Win11 的 DarkMode_Explorer）。"""
            import ctypes
            u = ctypes.windll.user32
            try:
                ux = ctypes.windll.uxtheme
            except Exception:
                return
            try:
                ux.SetWindowTheme(ctypes.c_void_p(int(hwnd)), 'DarkMode_Explorer', None)
                u.SendMessageW(ctypes.c_void_p(int(hwnd)), 0x031A, 0, 0)   # WM_THEMECHANGED
            except Exception:
                pass

        def _paint_close(self, sender, e):
            """画一个清楚的 ✕（2px 线 + 抗锯齿）。比字体渲染'×'稳，DPI 变了样子也不变。"""
            try:
                from System.Drawing.Drawing2D import SmoothingMode
                g = e.Graphics
                g.SmoothingMode = SmoothingMode.AntiAlias
                pen = System.Drawing.Pen(self.C['ink'], 2)
                r = self.btnClose.ClientRectangle
                g.DrawLine(pen, r.X + 7, r.Y + 7, r.Right - 8, r.Bottom - 8)
                g.DrawLine(pen, r.Right - 8, r.Y + 7, r.X + 7, r.Bottom - 8)
                pen.Dispose()
            except Exception:
                pass

        # ---- 顶栏 ----
        def _layout_head(self):
            """顶栏四个控件只在这里摆位置：换文字、改卡片高度都走它。

            早先 `_fit_height` 里另抄了一套旧坐标（-32 / -36-w），结果每次结果回来
            按钮都往左跳 4px，窄卡上还能压到引擎按钮 —— 一个布局只留一处真源。
            """
            try:
                head_h = int(getattr(self.head, 'Height', 34) or 34)
            except Exception:
                head_h = 34
            try:
                cw = int(self.ClientSize.Width)
                x_close = cw - 28
                self.btnClose.Location = Point(x_close, max(2, (head_h - 24) // 2))
                ew = max(64, int(self.engBtn.Width))
                eh = max(24, int(self.engBtn.Height))
                x_eng = x_close - 4 - ew
                self.engBtn.Location = Point(x_eng, max(2, (head_h - eh) // 2))
                tw = max(52, int(self.btnTgt.Width))
                th = max(24, int(self.btnTgt.Height))
                x_tgt = x_eng - 4 - tw
                self.btnTgt.Location = Point(x_tgt, max(2, (head_h - th) // 2))
                # 标题能留多宽就多宽；窄卡上挤不下就干脆收起来，别压到「译成」
                avail = x_tgt - 10 - 8
                if avail >= 64:
                    self.lbl.Visible = True
                    self.lbl.Size = Size(avail, 20)
                    self.lbl.Location = Point(10, max(3, (head_h - 20) // 2))
                else:
                    self.lbl.Visible = False
            except Exception:
                pass

        def _set_tgt_text(self, tid):
            """「译成」按钮：显示目标语言的人话名，宽度按文字实测。"""
            nm = self._tgt_name(tid)
            txt = '%s ▾' % (nm,)
            self._tgt = str(tid or 'auto')
            self.btnTgt.Text = txt
            try:
                sz = TextRenderer.MeasureText(txt, self.btnTgt.Font)
                w = max(52, min(120, int(sz.Width) + 18))
                h = max(24, int(self.btnTgt.Font.Height) + 8)
            except Exception:
                w, h = 60, 26
            self.btnTgt.Size = Size(w, h)
            self._layout_head()

        def _tgt_name(self, tid):
            for t in self._tgt_choices:
                if t.get('id') == tid:
                    return t.get('name') or tid
            return '跟随设置' if not tid or tid == 'auto' else str(tid)

        def _set_copy_enabled(self, on):
            """复制键的可用状态要**看得出来**：光 Enabled=False，外观还是蓝底白字，用户照点不误。"""
            self.btnCopy.Enabled = bool(on)
            if on:
                self.btnCopy.BackColor = self.C['accent']
                self.btnCopy.ForeColor = self.C['accent_ink']
            else:
                self.btnCopy.BackColor = self.C['line']
                self.btnCopy.ForeColor = self.C['sub']

        # ---- 底部：按钮排布 + 状态切换 ----
        def _layout_foot(self):
            """底部这一排也只在这里摆位置（成功 / 失败 / 看原文三种排布）。"""
            try:
                w, h = int(self.ClientSize.Width), int(self.ClientSize.Height)
            except Exception:
                w, h = 400, 250
            y = max(0, h - 38)
            x = 12
            self.btnToggle.Location = Point(x, y)
            x += int(self.btnToggle.Width) + 8
            for b in (self.btnCopy, self.btnRetry, self.btnAlt, self.btnPrev, self.btnNext):
                try:
                    if not b.Visible:
                        continue
                    step = int(b.Width) + (4 if b is self.btnPrev else 8)
                    b.Location = Point(x, y)
                    x += step
                except Exception:
                    pass
            hint_w = max(0, w - 12 - x)
            self.hint.Location = Point(x, h - 36)
            self.hint.Size = Size(hint_w, 26)
            self.hint.Visible = hint_w >= 64

        def _set_state(self, st):
            """loading / result / error / src 四种排布，可见性只在这里切。"""
            self._state = st
            err = (st == 'error')
            src_mode = (st == 'src')
            self.btnCopy.Visible = (st == 'result')
            self.btnRetry.Visible = err or src_mode
            self.btnRetry.Text = '重翻' if src_mode else '重试'
            self.btnRetry.BackColor = self.C['accent'] if err else self.C['bg']
            self.btnRetry.ForeColor = self.C['accent_ink'] if err else self.C['ink']
            self.btnRetry.FlatAppearance.BorderSize = 0 if err else 1
            self.btnAlt.Visible = err
            self._layout_foot()

        def _nav_button(self, txt, handler):
            b = Button()
            b.Text = txt
            b.FlatStyle = FlatStyle.Flat
            b.FlatAppearance.BorderSize = 1
            b.FlatAppearance.BorderColor = self.C['line']
            b.BackColor = self.C['bg']
            b.ForeColor = self.C['ink']
            b.Font = Font('Microsoft YaHei UI', 9, FontStyle.Bold)
            b.Size = Size(24, 26)
            b.Cursor = System.Windows.Forms.Cursors.Hand
            b.Visible = False
            b.Click += handler
            return b

        def _sync_nav(self):
            """有历史才显示 ‹ ›；到两头就禁用（禁用态也要看得出是灰的）。"""
            n = len(_HISTORY)
            show = n > 1 and self._hist_pos >= 0
            try:
                self.btnPrev.Visible = show
                self.btnNext.Visible = show
                self.btnPrev.Enabled = show and self._hist_pos > 0
                self.btnNext.Enabled = show and self._hist_pos < n - 1
                for b in (self.btnPrev, self.btnNext):
                    b.ForeColor = self.C['ink'] if b.Enabled else self.C['sub']
                self._layout_foot()
            except Exception:
                pass

        def _set_eng_text(self, name):
            nm = str(name or '自动')
            if nm in PALETTES or nm in [e.get('id') for e in self._engines]:
                nm = self._engine_name(nm) or nm      # 传进来的是引擎 id 时换成人看的名字
            txt = '%s  ▾' % (self._short_name(nm))
            self.engBtn.Text = txt
            try:
                sz = TextRenderer.MeasureText(txt, self.engBtn.Font)
                w = max(64, min(190, int(sz.Width) + 24))
                h = max(26, int(self.engBtn.Font.Height) + 8)
            except Exception:
                w, h = 96, 28
            self.engBtn.Size = Size(w, h)
            # 标题条跟着按钮长高：34px 的头配 30px 的按钮刚刚好，再高就得跟着抬
            try:
                need = h + 6
                if int(self.head.Height) < need:
                    self.head.Height = need
            except Exception:
                pass
            self._layout_head()

        @staticmethod
        def _short_name(name):
            """顶栏按钮只放短名：全名在正文标题和下拉里都能看到，这里不挤占宽度。"""
            short = str(name or '自动')
            for tail in ('交互翻译', ' 翻译', '翻译', ' 词典', '词典', ' 词霸', '词霸'):
                if short.endswith(tail) and len(short) > len(tail):
                    short = short[:-len(tail)]
                    break
            short = short.strip()
            return short or '自动'

        def _engine_name(self, eid):
            for e in self._engines:
                if e.get('id') == eid:
                    return e.get('name') or eid
            return eid or '自动'

        def _open_eng_pop(self, sender=None, e=None):
            self._toggle_pop(sender, e)

        def _toggle_pop(self, sender, e):
            """下拉用独立无边框小窗：卡片可能只有 150px 高，塞在卡片里会被裁掉几行。"""
            self._open_pop('eng')

        def _toggle_tgt_pop(self, sender, e):
            self._open_pop('tgt')

        def _open_pop(self, kind):
            # 同一个按钮开着再点 = 收起；点了另一个就把旧的关掉重建
            if self._pop is not None and self._pop.Visible:
                try:
                    was = self._pop_kind
                    self._pop.Hide()
                except Exception:
                    was = ''
                if was == kind:
                    return
            # 每次重开都重建：换过引擎之后「✓ 当前」要落在新那家上
            if self._pop is not None:
                try:
                    self._pop.Hide()
                    self._pop.Dispose()
                except Exception:
                    pass
                self._pop = None
            self._build_pop(kind)
            self._place_pop(kind)
            self._pop.Show()
            self._pop.BringToFront()

        def _place_pop(self, kind='eng'):
            try:
                anchor = self.btnTgt if kind == 'tgt' else self.engBtn
                p = self.PointToScreen(Point(anchor.Left, self.head.Height))
                wa = Screen.FromPoint(Point(self.Left, self.Top)).WorkingArea
                w, h = self._pop.Width, self._pop.Height
                x = max(wa.Left, min(int(p.X), wa.Right - w))
                y = int(p.Y) + 2
                if y + h > wa.Bottom:
                    y = int(p.Y) - h - 2
                    if y < wa.Top:
                        y = max(wa.Top, wa.Bottom - h)
                self._pop.Location = Point(x, max(wa.Top, y))
            except Exception:
                pass

        def _build_pop(self, kind='eng'):
            if kind == 'tgt':
                rows = self._tgt_choices[:6]
                cur_id = self._tgt
                on_pick = self._pick_tgt
            else:
                rows = self._engines[:9]
                cur_id = self._cur_id
                on_pick = self._pick_engine
            font = Font('Microsoft YaHei UI', 9)
            # 行高按字体实际高度算：150% DPI 下固定 27 会把最后一行的下缘削掉
            try:
                row_h = max(27, int(font.Height) + 10)
            except Exception:
                row_h = 30
            # 宽度按最长那行实测：引擎名 + 「· 备注」不再被截成「全球」「词库释」
            widest = 0
            for ent in rows:
                label = '%s%s%s' % ('✓ ' if ent.get('id') == cur_id else '   ',
                                    ent.get('name') or ent.get('id'),
                                    ('  ·  ' + ent['note']) if ent.get('note') else '')
                try:
                    sz = TextRenderer.MeasureText(label, font)
                    widest = max(widest, int(sz.Width))
                    row_h = max(row_h, int(sz.Height) + 10)
                except Exception:
                    widest = max(widest, 170)
            w = max(150, min(380, int(widest) + 22))
            h = row_h * len(rows) + 8
            pop = Form()
            pop.FormBorderStyle = getattr(FormBorderStyle, 'None')
            pop.StartPosition = FormStartPosition.Manual
            pop.TopMost = True
            pop.ShowInTaskbar = False
            try:
                pop.AutoScaleMode = getattr(__import__('System.Windows.Forms',
                                                       fromlist=['AutoScaleMode']).AutoScaleMode, 'None')
            except Exception:
                pass
            pop.BackColor = self.C['line']
            pop.ClientSize = Size(w, h)
            pop.Paint += self._on_pop_paint
            pop.Deactivate += lambda s, ev: pop.Hide()
            for i, ent in enumerate(rows):
                b = Button()
                b.FlatStyle = FlatStyle.Flat
                b.FlatAppearance.BorderSize = 0
                b.BackColor = self.C['card']
                cur = ent.get('id') == cur_id
                b.ForeColor = self.C['ink'] if ent.get('ready', True) else self.C['sub']
                b.Font = font
                b.TextAlign = ContentAlignment.MiddleLeft
                b.Text = '%s%s%s' % ('✓ ' if cur else '   ',
                                     ent.get('name') or ent.get('id'),
                                     ('  ·  ' + ent['note']) if ent.get('note') else '')
                b.Size = Size(w - 8, row_h)
                b.Location = Point(4, 4 + i * row_h)
                b.Cursor = System.Windows.Forms.Cursors.Hand
                b.FlatAppearance.MouseOverBackColor = self.C['line']
                if cur:
                    b.BackColor = self.C['bg']
                eid, nm = ent.get('id'), ent.get('name') or ent.get('id')
                b.Click += (lambda s, ev, _id=eid, _nm=nm, _f=on_pick: _f(_id, _nm))
                pop.Controls.Add(b)
            self._pop = pop
            self._pop_kind = kind

        def _on_pop_paint(self, sender, e):
            try:
                pen = System.Drawing.Pen(self.C['line'], 1)
                r = e.Graphics.ClipBounds
                e.Graphics.DrawRectangle(pen, 0, 0,
                                         int(r.Width) - 1, int(r.Height) - 1)
                pen.Dispose()
            except Exception:
                pass

        def _hide_pop(self):
            try:
                if self._pop is not None:
                    self._pop.Hide()
            except Exception:
                pass

        def _enter_loading(self, msg='翻译中…'):
            self._showing = 'dst'
            self.btnToggle.Text = '原文'
            self.txt.ReadOnly = True
            self.lbl.Text = '截图翻译 · 译文'
            self.txt.Text = msg
            self._set_copy_enabled(False)
            self._set_state('loading')
            self._fit_height(msg)

        def _fire_switch(self, eid, text=None):
            """让调用方去翻译：带引擎 id、当前「译成」、以及（可能改过的）原文。"""
            eid = eid or (self._engines[0]['id'] if self._engines else '')
            try:
                if self._on_switch:
                    self._on_switch(eid, self._tgt, text)
                else:
                    self.set_error('没有可用的翻译引擎')
            except Exception:
                self.set_error('切换引擎失败')

        def _pick_engine(self, eid, name):
            self._hide_pop()
            if not eid:
                return
            self._cur_id = eid
            self._set_eng_text(name)
            # 之前翻过这家：直接把结果贴回来，来回对比不用再等一次网络
            hit = self._cache.get(eid)
            if hit:
                self._dst = hit[0]
                self._showing = 'dst'
                self.btnToggle.Text = '原文'
                self.txt.ReadOnly = True
                self.lbl.Text = '截图翻译 · 译文'
                self.txt.Text = self._dst
                self._set_copy_enabled(True)
                self._set_state('result')
                self._reset_dwell()
                self._fit_height(self.txt.Text)
                return
            # 同一家再点一次 = 重试（以前这里直接 return，失败后根本没法原地重来）
            self._enter_loading()
            self._fire_switch(eid, None)

        def _pick_tgt(self, tid, name):
            self._hide_pop()
            if not tid or tid == self._tgt:
                return
            self._tgt = tid
            self._set_tgt_text(tid)
            self._cache.clear()          # 目标语言换了，之前那批译文不算数
            self._enter_loading()
            self._fire_switch(self._cur_id, None)

        def _on_retry(self, sender=None, e=None):
            """失败重试；看原文时这个键变成「重翻」（把用户改过的原文当新源头）。"""
            if self._state == 'src':
                text = (self.txt.Text or '').strip()
                if not text:
                    return
                self._src = text
                self._enter_loading()
                self._fire_switch(self._cur_id, text)
                return
            if not self._src:
                self.set_error('这张图还没识别出文字，重新截一张吧')
                return
            self._enter_loading()
            self._fire_switch(self._cur_id, None)

        # ---- 历史：上一条 / 下一条 ----
        def _on_hist_prev(self, sender=None, e=None):
            if self._hist_pos > 0:
                self._show_hist(self._hist_pos - 1)

        def _on_hist_next(self, sender=None, e=None):
            if self._hist_pos < len(_HISTORY) - 1:
                self._show_hist(self._hist_pos + 1)

        def _show_hist(self, pos):
            if not _HISTORY:
                return
            pos = max(0, min(int(pos), len(_HISTORY) - 1))
            ent = _HISTORY[pos]
            self._hist_pos = pos
            self._src = ent.get('src') or ''
            self._dst = ent.get('dst') or ''
            if ent.get('engine_id'):
                self._cur_id = ent['engine_id']
            if ent.get('engine'):
                self._set_eng_text(ent['engine'])
            self._showing = 'dst'
            self.btnToggle.Text = '原文'
            self.txt.ReadOnly = True
            self.lbl.Text = '截图翻译 · 译文'
            self.txt.Text = self._dst
            self._set_copy_enabled(bool(self._dst))
            self._set_state('result' if self._dst else 'error')
            self._reset_dwell()
            self._fit_height(self.txt.Text)
            self._sync_nav()

        # ---- 拖动卡片 / 双击钉住 ----
        def _head_mouse_down(self, sender, e):
            try:
                if e.Button != MouseButtons.Left:
                    return
                cp = Cursor.Position
                self._drag = (int(cp.X) - int(self.Left), int(cp.Y) - int(self.Top))
            except Exception:
                self._drag = None

        def _head_mouse_move(self, sender, e):
            if not self._drag:
                return
            try:
                cp = Cursor.Position
                self.Location = Point(int(cp.X) - self._drag[0], int(cp.Y) - self._drag[1])
            except Exception:
                pass

        def _head_mouse_up(self, sender, e):
            self._drag = None

        def _on_pin(self, sender=None, e=None):
            """双击标题条：钉住 = 不再自动关（没有安装计数器的长文校对场景）。"""
            self._pinned = not self._pinned
            try:
                self.hint.Text = self._hint_text('已钉住' if self._pinned else '还剩 %d 秒' % max(1, self._left))
            except Exception:
                pass

        # ---- 全局 Esc ----
        def _install_esc_hook(self):
            """Esc 要能关掉卡片，哪怕焦点已经还给了用户原来的窗口。

            第 371 项把焦点还回去之后，卡片收不到按键，右下角却还写着「Esc 关闭」——
            提示就成了假话。这里挂一个只观察 Esc 的键盘钩子，**不吞键**（别的程序
            该响应 Esc 照样响应），卡片该关也关。
            """
            if self._hook:
                return

            def run():
                try:
                    import ctypes
                    from ctypes import wintypes
                    u = ctypes.windll.user32
                    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                                  wintypes.WPARAM, wintypes.LPARAM)

                    def cb(ncode, wparam, lparam):
                        if ncode >= 0 and int(wparam) in (0x0100, 0x0104) and not self._closed:
                            try:
                                vk = ctypes.cast(lparam, ctypes.POINTER(ctypes.c_ulong))[0]
                            except Exception:
                                vk = 0
                            if vk == 0x1B:      # VK_ESCAPE
                                try:
                                    self.BeginInvoke(System.Action(self._on_close_by_esc))
                                except Exception:
                                    pass
                        return u.CallNextHookEx(None, ncode, wparam, lparam)

                    proc = HOOKPROC(cb)
                    self._hook_proc = proc          # 留引用，不然回调会被 GC 掉
                    h = u.SetWindowsHookExW(13, proc, None, 0)
                    if not h:
                        return
                    self._hook = h
                    self._hook_tid = int(u.GetCurrentThreadId())
                    msg = wintypes.MSG()
                    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                        u.TranslateMessage(ctypes.byref(msg))
                        u.DispatchMessageW(ctypes.byref(msg))
                except Exception:
                    pass

            threading.Thread(target=run, daemon=True, name='memex-snip-esc').start()

        def _on_close_by_esc(self):
            if not self._closed:
                self._on_close(None, None)

        def _unhook_esc(self):
            try:
                import ctypes
                u = ctypes.windll.user32
                if self._hook:
                    u.UnhookWindowsHookEx(self._hook)
                    self._hook = None
                if self._hook_tid:
                    u.PostThreadMessageW(self._hook_tid, 0x0012, 0, 0)   # WM_QUIT
                    self._hook_tid = 0
            except Exception:
                pass

        def _on_form_closed(self, sender, e):
            """不管怎么关的（✕ / Esc / 超时 / ShowDialog 结束）都要把钩子摘掉。"""
            self._closed = True
            self._unhook_esc()

        # ---- 定位：优先贴选区正下方，放不下翻到上方，再夹进工作区 ----
        def _place(self):
            try:
                rx, ry, rw, rh = [int(v) for v in self.rect]
            except Exception:
                rx, ry, rw, rh = 0, 0, 400, 100
            screen = Screen.FromRectangle(Rectangle(rx, ry, max(1, rw), max(1, rh)))
            wa = screen.WorkingArea
            w, h = self.ClientSize.Width, self.ClientSize.Height
            x = rx
            y = ry + rh + 8
            if y + h > wa.Bottom:
                y = ry - h - 8
            if y < wa.Top:
                y = ry + rh + 8
            if y + h > wa.Bottom:
                y = max(wa.Top, wa.Bottom - h)
            x = max(wa.Left, min(x, wa.Right - w))
            y = max(wa.Top, min(y, wa.Bottom - h))
            self.Location = Point(x, y)

        # ---- 对外接口（可从任意线程调用）----
        def set_source(self, src):
            self._pending = ('src', src)

        def set_loading(self, msg='翻译中…'):
            self._pending = ('loading', msg)

        def set_result(self, dst, engine='', src=None, engine_id=''):
            self._pending = ('done', (dst or '', engine or '', src, engine_id or ''))

        def set_error(self, msg):
            self._pending = ('err', msg or '翻译失败')

        def _on_tick(self, sender, e):
            p = self._pending
            if not p:
                return
            self._pending = None
            kind, val = p
            try:
                if kind == 'src':
                    self._src = val or ''
                    return
                if kind == 'loading':
                    self._showing = 'dst'
                    self.btnToggle.Text = '原文'
                    self.txt.ReadOnly = True
                    self.txt.Text = str(val)
                    self.lbl.Text = '截图翻译 · 译文'
                    self._set_copy_enabled(False)      # 结果没到，别让人复制走「翻译中…」
                    self._set_state('loading')
                    self._fit_height(self.txt.Text)
                    return
                if kind == 'err':
                    self.txt.ReadOnly = True
                    self.txt.Text = str(val)
                    self.lbl.Text = '截图翻译 · 失败'      # 正文是错误信息，标题不能还写「译文」
                    self._set_copy_enabled(bool(self._dst))
                    # 失败也必须给出口：重试 / 换一家，别让人 Esc 重截
                    self._set_state('error')
                    self._reset_dwell()
                    self._fit_height(self.txt.Text)
                    return
                dst, engine, src, eid = val
                if src:
                    self._src = src
                if eid:
                    self._cur_id = eid
                    self._set_eng_text(engine or self._engine_name(eid))
                    if dst:
                        # 记住这一家的结果：切回来马上能看，不用再等一次网络
                        self._cache[eid] = (dst, engine or self._engine_name(eid))
                self._dst = dst
                self.lbl.Text = '截图翻译 · 译文'
                self.txt.ReadOnly = True
                self.txt.Text = self._dst or '（引擎没有返回译文）'
                self._showing = 'dst'
                self.btnToggle.Text = '原文'
                self._set_copy_enabled(bool(self._dst))
                self._set_state('result' if self._dst else 'error')
                if self._dst and self._src:
                    push_history(self._src, dst, engine, eid)
                    self._hist_pos = len(_HISTORY) - 1
                    self._sync_nav()
                self._reset_dwell()                   # 结果刚到，重新给足阅读时间
                self._fit_height(self.txt.Text)
            except Exception:
                pass

        def _fit_height(self, text):
            """按文字实际高度把卡片长高/压矮：短译文不留一大片空，长译文也能读全。"""
            try:
                w = self.ClientSize.Width - 26
                g = self.txt.CreateGraphics()
                sz = g.MeasureString(text or '', self.txt.Font, w)
                g.Dispose()
                measured = int(sz.Height)
                screen = Screen.FromPoint(Point(self.Left, self.Top))
                wa = screen.WorkingArea
                want = measured + 90
                want = max(150, min(want, int(wa.Height * 0.7)))
                self.ClientSize = Size(self.ClientSize.Width, want)
                if self.Top + want > wa.Bottom:
                    self.Top = max(wa.Top, wa.Bottom - want)
                # 子控件跟着改：正文吃满，底部按钮贴底
                self.txt.Size = Size(self.ClientSize.Width - 24, want - 86)
                # 卡到屏高 70% 还装不下就开竖滚动条 —— 以前超出部分直接读不到
                self._set_scrollbars((measured + 8) > (want - 86))
                self._layout_foot()
                self._layout_head()
                if self._pop is not None and self._pop.Visible:
                    self._place_pop(self._pop_kind)
                self.Invalidate()
            except Exception:
                pass

        def _set_scrollbars(self, on):
            """只在真的装不下时挂滚动条；换过 ScrollBars 句柄会重建，深色主题得重套。"""
            try:
                want = ScrollBars.Vertical if on else getattr(ScrollBars, 'None')
                if bool(self._scroll) == bool(on) and self.txt.ScrollBars == want:
                    return
                self._scroll = bool(on)
                self.txt.ScrollBars = want
                if self.C['dark']:
                    self._dark_scrollbar(self.txt.Handle)
                self.hint.Text = self._hint_text(self._hint_base())
            except Exception:
                pass

        def _on_toggle(self, sender, e):
            if not self._src:
                return
            if self._showing == 'dst':
                self._showing = 'src'
                self.txt.Text = self._src
                self.txt.ReadOnly = False      # 原文可以直接改，改完按「重翻」（OCR 也有错字）
                self.lbl.Text = '截图翻译 · 原文'
                self.btnToggle.Text = '译文'
                self._set_state('src')
            else:
                # 改过的原文要收下，不然切回译文就把用户的修正丢了
                t = (self.txt.Text or '')
                if (not self.txt.ReadOnly) and t.strip():
                    self._src = t
                self._showing = 'dst'
                self.txt.ReadOnly = True
                self.txt.Text = self._dst or '（引擎没有返回译文）'
                self.lbl.Text = '截图翻译 · 译文'
                self.btnToggle.Text = '原文'
                self._set_state('result' if self._dst else 'error')
            self._fit_height(self.txt.Text)

        def _hint_base(self):
            if self._pinned:
                return '已钉住'
            return '还剩 %d 秒' % max(0, self._left)

        def _hint_text(self, base):
            """右下角那行提示：装不下全文时先说「可滚动」，比倒计时更要紧。"""
            return ('可滚动 · ' + base) if self._scroll else base

        def _reset_dwell(self):
            self._left = DWELL_SEC
            try:
                self.hint.Text = self._hint_text(self._hint_base())
            except Exception:
                pass

        def _on_copy(self, sender, e):
            try:
                if not self._dst:
                    return          # 没有译文就什么都不做，绝不把「翻译中…」写进剪贴板
                text = self._dst
                import win32clipboard
                import win32con
                for _ in range(15):
                    try:
                        win32clipboard.OpenClipboard()
                        win32clipboard.EmptyClipboard()
                        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                        win32clipboard.CloseClipboard()
                        break
                    except Exception:
                        import time
                        time.sleep(0.03)
                self.btnCopy.Text = '已复制'
                self.timer2 = Timer()
                self.timer2.Interval = 1200
                self.timer2.Tick += self._reset_copy
                self.timer2.Start()
            except Exception:
                pass

        def _reset_copy(self, sender, e):
            try:
                self.timer2.Stop()
            except Exception:
                pass
            self.btnCopy.Text = '复制译文'

        def _on_key(self, sender, e):
            if e.KeyCode == Keys.Escape:
                self._on_close(None, None)
            elif e.KeyCode == Keys.Enter and e.Control:
                self._on_copy(None, None)

        def _on_autoclose(self, sender, e):
            """每秒跑一次：钉住了不动，鼠标在卡片里就暂停，否则往下数，数到 0 才关。

            旧版是 60 秒硬关，用户读长段落读到一半卡片消失，只能重新截图。
            """
            if self._pinned:
                self.hint.Text = self._hint_text('已钉住 · 双击标题取消')
                return
            try:
                inside = self.ClientRectangle.Contains(self.PointToClient(Cursor.Position))
            except Exception:
                inside = False
            if inside:
                self.hint.Text = self._hint_text('悬停暂停计时')
                return
            self._left -= 1
            if self._left > 0:
                self.hint.Text = self._hint_text('还剩 %d 秒' % self._left)
                return
            try:
                self.auto.Stop()
            except Exception:
                pass
            self._on_close(None, None)

        def _on_close(self, sender, e):
            self._closed = True
            try:
                self.timer.Stop()
            except Exception:
                pass
            try:
                self.auto.Stop()
            except Exception:
                pass
            try:
                if self._pop is not None:
                    self._pop.Hide()
                    self._pop.Dispose()
                    self._pop = None
            except Exception:
                pass
            self._unhook_esc()
            try:
                self.DialogResult = DialogResult.Cancel
            except Exception:
                pass
            try:
                self.Close()
            except Exception:
                pass


class _Card:
    """给非 WinForms 代码用的轻句柄：方法都线程安全（只往卡片里塞 pending）。"""

    def __init__(self, form=None):
        self._form = form

    def set_source(self, src):
        if self._form is not None:
            self._form.set_source(src)

    def set_loading(self, msg='翻译中…'):
        if self._form is not None:
            self._form.set_loading(msg)

    def set_result(self, dst, engine='', src=None, engine_id=''):
        if self._form is not None:
            self._form.set_result(dst, engine, src, engine_id)

    def set_error(self, msg):
        if self._form is not None:
            self._form.set_error(msg)


def show_card(rect, loading_text='翻译中…', engines=None, current_engine='', on_switch=None,
              tgt_choices=None, tgt='auto', engine_id=''):
    """在选区原位弹出译文卡片；返回句柄，调用方之后 set_result() 即可。无 WinForms 时返回空句柄。

    engines: [{'id','name','note','ready'}] —— 顶栏「引擎 ▾」下拉里列的就是它；
    tgt_choices: [{'id','name'}] —— 顶栏「译成 ▾」下拉，auto = 跟随设置里的目标语言；
    on_switch(eid, tgt, text): 换引擎 / 换目标语言 / 改完原文重翻时回调，
        text 非空表示用户改过原文（拿它当新源头），调用方自己去翻译再 set_result。
    """
    if not HAS_WINFORMS:
        return _Card(None)
    holder = {'form': None, 'ready': threading.Event()}
    # 先记下当前前台窗口：卡片弹完再把焦点还回去，别把用户正在敲字的窗口顶掉
    prev_fg = 0
    try:
        import ctypes
        prev_fg = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        prev_fg = 0

    def worker():
        try:
            form = SnipCardForm(rect, loading_text, engines, current_engine, on_switch,
                                prev_fg, tgt_choices, tgt, engine_id)
            holder['form'] = form
            holder['ready'].set()
            form.ShowDialog()
            form.Dispose()
        except Exception:
            holder['ready'].set()

    try:
        import System.Threading as ST
        th = ST.Thread(ST.ThreadStart(worker))
        th.SetApartmentState(ST.ApartmentState.STA)
        th.IsBackground = True
        th.Start()
    except Exception:
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    holder['ready'].wait(6.0)
    return _Card(holder['form'])

