#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""floating_ball.py — 桌面常驻悬浮球 / 悬浮窗

功能：
1. 桌面轻量悬浮图标（品牌标，无窗体边框，支持拖拽定位与跨开机记忆）；
2. 单击：唤出 / 收起 Memex 快搜栏；
3. 长点（长按 >= 400ms）：触发屏幕截图；
4. 右键菜单：唤出快搜、屏幕截图、设置、隐藏悬浮球（文案跟随界面语言，第 284 项）；
5. 开关：设置里控制，默认关闭。

第 269 项（2026-11-17）：这个文件原来**整个交互层是死的**，所以用户点悬浮球
一点反应都没有，也画不出 Memex 的图标。真机取证：

* 本机这套 pythonnet **不会回调 Python 子类里的 virtual 覆写**。实测
  `OnPaint / OnMouseDown / OnMouseUp / OnShown / OnHandleCreated / OnResize
  / WndProc / CreateParams` 全部一次都没被调用（探针 tmp/probe_overrides_dead.py）。
  于是旧代码里靠覆写实现的绘制、单击、拖拽、右键菜单、长按判定全是空转；
* 事件订阅（`form.Paint += ...`、`form.MouseDown += ...`、`form.Shown += ...`）
  是能正常回调的 —— 所以改成订阅式接线；
* `TopMost = True` 在 Show 之前设也不生效（实测 exstyle 没有 0x8 TOPMOST），
  球会被普通窗口盖住 —— 改成 Show 之后再设一次并复查；
* Form 在 Sizable 边框下会被夹到最小窗宽（100% ≈136px，150% ≈202px），
  `Width=50` 会被顶成 202x56 —— 改成 Show 之后按回 50x50 并重建圆形 Region。

另外两处用户直接反馈的问题：
* 图标不是 Memex 的 → 改画品牌标（青环 + 白 M + 橙手柄，取色自 assets/icon_preview.png）；
* 设置里开关要等几秒 → `App._sync_floating_ball_fast` 每 0.15 秒过一遍（App 侧改）；
  本文件 `stop()` 也不再 `Join(1500)` 同步阻塞，最多等 400ms。

第 272 项（2026-11-17，用户反馈「你那个太模糊了，同这个当悬浮图标，而且不要把手柄去掉」）：
**手绘那套丢掉，直接渲染官方标志图**。
* 素材：`logo候选/两版实做/B-青橙/标志.png`（1024x1024 RGBA）→ tmp/make_ball_png.py
  抠掉白底、按不透明像素裁紧（含手柄）、LANCZOS 缩到 160x160 → `assets/ball.png`。
  打包时由 second-brain-app.spec 的 assets 白名单带进 `_internal/assets/`。
* 绘制：`Graphics.DrawImage` 高质量双三次缩放，**不再用 Font 画 M、不再用 Pen 描环**
  —— 那套 50px 下必然糊。手绘版降级为 `_paint_fallback`，只在资源缺失时兜底。
* 圆形 Region 改成**按图片 alpha 轮廓扫描线**生成：手柄在圆外，纯圆 Region 会
  把手柄切掉（用户明确要求「不要把手柄去掉」）。取不到资源时才退回 48px 圆。
* 另修一个会弹 .NET 崩溃框的真 bug：`_on_mouse_down` 用了 `Cursor.Position`
  但 import 列表里只有 `Cursors`，**每次按下都抛 NameError**（用户截图
  「name 'Cursor' is not defined」）。同时把所有事件回调套 `_guard`，
  任何绘制/鼠标异常只进 debug.log，绝不再弹「未处理的异常」对话框。
"""

import threading

# 右键菜单文案（第 284 项）：原来四项硬编码中文，切到英文/西语/葡语/印地语界面
# 还是中文。这里按 brain.boot_lang() 现取，每次弹菜单前重刷一遍。
BALL_L = {
    'zh': {'search': '🔍 唤出快搜栏', 'detail': '🔎 详细搜索框', 'snip': '✂️ 屏幕截图 (长点)',
           'translate': '🌐 聚合翻译', 'settings': '⚙️ 设置', 'hide': '❌ 隐藏悬浮球'},
    'en': {'search': '🔍 Open quick search', 'detail': '🔎 Full search window',
           'snip': '✂️ Screenshot (long press)', 'translate': '🌐 Translate',
           'settings': '⚙️ Settings', 'hide': '❌ Hide floating ball'},
    'es': {'search': '🔍 Abrir búsqueda rápida', 'detail': '🔎 Ventana de búsqueda',
           'snip': '✂️ Capturar pantalla (mantener)', 'translate': '🌐 Traducir',
           'settings': '⚙️ Ajustes', 'hide': '❌ Ocultar la bolita'},
    'pt': {'search': '🔍 Abrir busca rápida', 'detail': '🔎 Janela de busca',
           'snip': '✂️ Capturar tela (segurar)', 'translate': '🌐 Traduzir',
           'settings': '⚙️ Configurações', 'hide': '❌ Ocultar a bolinha'},
    'hi': {'search': '🔍 त्वरित खोज खोलें', 'detail': '🔎 विस्तृत खोज विंडो',
           'snip': '✂️ स्क्रीनशॉट (देर तक दबाएँ)', 'translate': '🌐 अनुवाद',
           'settings': '⚙️ सेटिंग्स', 'hide': '❌ फ़्लोटिंग बॉल छिपाएँ'},
}


def ball_labels():
    """当前界面语言的右键菜单文案；拿不到语言就退回中文。"""
    lang = 'zh'
    try:
        import brain
        lang = brain.boot_lang() or 'zh'
    except Exception:
        pass
    return BALL_L.get(lang, BALL_L['zh'])

try:
    import clr
    clr.AddReference('System.Windows.Forms')
    clr.AddReference('System.Drawing')
    clr.AddReference('System.Threading')
    import System
    from System.Windows.Forms import (
        Form, FormBorderStyle, FormStartPosition, Cursors, Cursor, Screen,
        MouseButtons, Application, Timer, ContextMenuStrip, ToolStripMenuItem,
        ControlStyles, AutoScaleMode
    )
    from System.Drawing import (
        Color, SolidBrush, Pen, Rectangle, RectangleF, Point, Size, Font, FontStyle,
        FontFamily, Region, Bitmap, Graphics, StringFormat, StringAlignment,
        GraphicsUnit
    )
    from System.Drawing.Drawing2D import (
        SmoothingMode, GraphicsPath, LinearGradientBrush, LinearGradientMode, LineCap,
        InterpolationMode, PixelOffsetMode, CompositingQuality
    )
    from System.Threading import Thread, ThreadStart, ApartmentState
    HAS_WINFORMS = True
except Exception:
    HAS_WINFORMS = False


# -- 交互参数（纯函数，单测直接钉，不用起窗体） -----------------------------

# 品牌色，与 assets/icon_preview.png 采样一致：青环 (9,189,194)、玻璃 (206,236,250)
# → (176,216,236)、白 M、手柄 #FFD84B → #FF6C17。
# 第 272 项起这些只给 `_paint_fallback` 和背景色用，正常绘制走 assets/ball.png。
BALL_RING = (9, 189, 194)
BALL_RING_HOVER = (34, 211, 216)
BALL_RING_PRESS = (6, 182, 212)
BALL_GLASS_TOP = (208, 237, 250)
BALL_GLASS_BOT = (176, 216, 236)
BALL_M_HI = (250, 253, 255)
BALL_M_SHADOW = (150, 192, 210)
HANDLE_TOP = (255, 216, 75)
HANDLE_BOT = (255, 108, 23)

# 窗口底色 = 青环色。悬浮球被 Region 裁成图标的剪影，这层底色只在图标边缘那圈
# 抗锯齿像素下面露出来；取成环色，边缘就还是环色，不会出现黑色描边。
BALL_BACK = (9, 189, 194)

# 剪影扫描线的 alpha 门限：低于它的像素算窗外（点这里不该砸到球上）。
REGION_ALPHA = 40

BALL_PNG = 'ball.png'

# 球的**逻辑**尺寸。窗口是设备像素的，150% DPI 下 50 设备像素只有 33 逻辑像素
# —— 标志里的 M 和环全被压成一坨，这就是用户说"太模糊"的另一半原因。
# 按 DPI 放大到 50 逻辑像素（144dpi → 75 设备像素），图标才看得清。
BALL_LOGICAL = 50


def ball_size(dpi=96):
    """球窗的**设备像素**边长：50 逻辑像素按 DPI 换算。"""
    try:
        d = float(dpi or 96)
    except (TypeError, ValueError):
        d = 96.0
    if d <= 0:
        d = 96.0
    return max(40, int(round(BALL_LOGICAL * d / 96.0)))


def _ball_png_path():
    """找 assets/ball.png：源码目录 / PyInstaller 解包目录 / 装机目录都试一遍。

    和 app_window._icon_path 同一套查找顺序 —— 打包后 cwd 不可靠，只认绝对路径。
    """
    import os
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parent
    cands = [here / 'assets' / BALL_PNG]
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        cands.append(Path(meipass) / 'assets' / BALL_PNG)
    try:
        cands.append(Path(sys.executable).resolve().parent / '_internal' / 'assets' / BALL_PNG)
        cands.append(Path(sys.executable).resolve().parent / 'assets' / BALL_PNG)
    except Exception:
        pass
    cands.append(here / '_internal' / 'assets' / BALL_PNG)
    for c in cands:
        try:
            if os.path.isfile(str(c)):
                return str(c)
        except Exception:
            continue
    return None


class _BallBitmapCache:
    """assets/ball.png 的进程级缓存 + 按目标尺寸缩放后的副本。

    每次 Paint 都重新 Decode + 缩放的话，拖动时一帧一次，CPU 白烧；这里只留
    最新尺寸那一份（球窗尺寸基本不变）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._src = None
        self._src_path = None
        self._scaled = None
        self._scaled_key = None

    def source(self):
        with self._lock:
            path = _ball_png_path()
            if path is None:
                return None
            if self._src is not None and self._src_path == path:
                return self._src
            try:
                self._src = Bitmap(path)
                self._src_path = path
                _log('ball.png 就绪: %s' % path)
            except Exception as e:
                _log('ball.png 读取失败 %s: %s' % (path, e))
                self._src = None
                return None
            return self._src

    def scaled(self, w, h):
        """缩到 (w,h) 的 ARGB 位图；失败返回 None（调用方走兜底手绘）。"""
        if not HAS_WINFORMS or w <= 0 or h <= 0:
            return None
        src = self.source()
        if src is None:
            return None
        with self._lock:
            if self._scaled is not None and self._scaled_key == (w, h):
                return self._scaled
            try:
                dst = Bitmap(w, h)
                sg = Graphics.FromImage(dst)
                sg.InterpolationMode = InterpolationMode.HighQualityBicubic
                sg.PixelOffsetMode = PixelOffsetMode.HighQuality
                sg.CompositingQuality = CompositingQuality.HighQuality
                sg.SmoothingMode = SmoothingMode.HighQuality
                sg.DrawImage(src, Rectangle(0, 0, w, h))
                sg.Dispose()
                self._scaled = dst
                self._scaled_key = (w, h)
                return dst
            except Exception as e:
                _log('ball.png 缩放失败: %s: %s' % (type(e).__name__, e))
                self._scaled = None
                self._scaled_key = None
                return None


_CACHE = _BallBitmapCache()


def silhouettes(bmp, w, h, alpha_min=REGION_ALPHA):
    """把位图的 alpha 变成扫描线矩形列表 [(x, y, width, 1), ...]。

    悬浮球要一个"手指能点到、又不会被裁掉手柄"的窗口 Region。纯圆 Region 会把
    手柄（在圆外）切掉；这里按每一行不透明像素的连续段拼出来，把手柄一起框住。
    抽成纯函数是为了能单测：不需要起窗体，喂一张内存位图就行。
    """
    runs = []
    if bmp is None:
        return runs
    for y in range(h):
        start = None
        for x in range(w + 1):
            inside = False
            if x < w:
                try:
                    inside = bmp.GetPixel(x, y).A >= alpha_min
                except Exception:
                    inside = False
            if inside and start is None:
                start = x
            elif (not inside) and start is not None:
                runs.append((start, y, x - start, 1))
                start = None
    return runs


def drag_threshold(dpi=96):
    """判定「拖拽」需要的**设备像素**位移：至少 6 个逻辑像素。

    以前写死 4 设备像素，150% DPI 下相当于 2.7 逻辑像素 —— 点一下鼠标的
    自然手抖（3~5 逻辑像素）就把单击吞成拖拽。虽然旧代码的鼠标事件压根没接上，
    这条阈值仍然按 DPI 算，改成交互可用的那一刻起就得是对的。
    """
    try:
        d = float(dpi or 96)
    except (TypeError, ValueError):
        d = 96.0
    if d <= 0:
        return 6.0
    return max(6.0, 6.0 * d / 96.0)


def is_drag(dx, dy, dpi=96):
    """位移够不够判成拖拽。dx/dy 是**设备像素**（Cursor.Position 口径）。"""
    th = drag_threshold(dpi)
    return abs(dx) > th or abs(dy) > th


_LOGGER = None


def set_logger(fn):
    """App 启动时把 _log_always 挂进来；没有就静默（单测/命令行里不刷屏）。"""
    global _LOGGER
    _LOGGER = fn


def _log(msg):
    fn = _LOGGER
    if not fn:
        return
    try:
        fn('ball: %s' % (msg,))
    except Exception:
        pass


def _guard(name, fn):
    """把事件回调包一层：异常只进日志，绝不让 pythonnet 弹「未处理的异常」框。

    第 272 项用户截图就是 `name 'Cursor' is not defined` 那个 .NET 崩溃框 ——
    它来自 MouseDown 事件里的 NameError。事件回调里任何意外都不该炸到用户脸上。
    """

    def wrapper(sender, e):
        try:
            return fn(sender, e)
        except Exception as ex:
            _log('%s EXCEPTION: %s: %s' % (name, type(ex).__name__, ex))
            return None

    return wrapper


if HAS_WINFORMS:
    class FloatingBallForm(Form):
        """桌面悬浮球原生窗口。

        第 269 项：**全部用事件订阅，不用 virtual 覆写** —— 本机 pythonnet 不
        回调 Python 覆写（见文件头），覆写写了等于没写。
        """

        def __init__(self, on_click=None, on_snip=None, on_detail=None, on_translate=None, on_settings=None, on_disable=None, on_pos_changed=None, init_x=-1, init_y=-1):
            super().__init__()
            self._on_click = on_click
            self._on_snip = on_snip
            self._on_detail = on_detail
            self._on_translate = on_translate
            self._on_settings = on_settings
            self._on_disable = on_disable
            self._on_pos_changed = on_pos_changed

            # 先去边框再定尺寸（Sizable 边框会把窗宽夹到最小窗宽）
            self.FormBorderStyle = getattr(FormBorderStyle, 'None')
            self.AutoScaleMode = getattr(AutoScaleMode, 'None')
            self.Size = Size(50, 50)
            self.ShowInTaskbar = False
            self.StartPosition = FormStartPosition.Manual
            self.BackColor = Color.FromArgb(*BALL_BACK)
            self.SetStyle(
                ControlStyles.AllPaintingInWmPaint |
                ControlStyles.UserPaint |
                ControlStyles.DoubleBuffer |
                ControlStyles.ResizeRedraw, True
            )

            # 初始位置：若未指定，默认落在主屏右侧偏上 (靠边 16px)
            try:
                wa = Screen.PrimaryScreen.WorkingArea
                x = init_x if (init_x is not None and init_x >= 0) else (wa.Right - 66)
                y = init_y if (init_y is not None and init_y >= 0) else (wa.Top + int(wa.Height * 0.45))
                x = max(wa.Left, min(x, wa.Right - 50))
                y = max(wa.Top, min(y, wa.Bottom - 50))
                self.Left = int(x)
                self.Top = int(y)
            except Exception:
                self.Left = 100
                self.Top = 100

            # 状态记录
            self._is_down = False
            self._dragged = False
            self._long_pressed = False
            self._hover = False
            self._down_screen_pos = Point(0, 0)
            self._form_start_pos = (self.Left, self.Top)
            self._dpi = 0.0
            self._m_font = None
            self._use_png = None      # None=还没试过；False=资源缺失，走手绘兜底

            # 事件订阅（不是覆写！）—— 统一套 _guard，异常只进 debug.log
            self.Paint += _guard('paint', self._on_paint)
            self.MouseDown += _guard('down', self._on_mouse_down)
            self.MouseMove += _guard('move', self._on_mouse_move)
            self.MouseUp += _guard('up', self._on_mouse_up)
            self.MouseEnter += _guard('enter', self._on_mouse_enter)
            self.MouseLeave += _guard('leave', self._on_mouse_leave)
            self.Shown += _guard('shown', self._on_shown)

            # 长按计时器 (400ms)
            self._long_timer = Timer()
            self._long_timer.Interval = 400
            self._long_timer.Tick += _guard('long', self._on_long_press_tick)

            # 右键菜单
            self._menu = self._build_context_menu()

        # -- 显示后复位：尺寸 / 剪影 Region / 置顶 / 工具窗 -----------------
        def _apply_ball_geometry(self):
            """按 DPI 定尺寸（50 逻辑像素）并重建与图标同形的 Region。

            系统首次显示会用 WM_GETMINMAXINFO 把窗宽夹到最小跟踪尺寸
            （100% ≈136px，150% ≈202px），只设一次 Size 会被顶回去。
            Region 也一样：`self.Region = ...` 这条属性赋值实测没落到 HWND 上
            （GetWindowRgn 返回 0，窗口还是方的），改成直接 SetWindowRgn。
            """
            sz = ball_size(self._dpi_x())
            try:
                self.Size = Size(sz, sz)
            except Exception as e:
                _log('复位尺寸失败: %s: %s' % (type(e).__name__, e))
            self._clamp_to_workarea(sz)
            self._set_circle_region(sz)

        def _clamp_to_workarea(self, sz):
            """放大后别让球半个身子跑出屏幕（默认位置是按 50px 算的）。"""
            try:
                wa = Screen.FromControl(self).WorkingArea
                x, y = int(self.Left), int(self.Top)
                if x + sz > wa.Right - 4:
                    x = wa.Right - sz - 8
                if x < wa.Left + 4:
                    x = wa.Left + 8
                if y + sz > wa.Bottom - 4:
                    y = wa.Bottom - sz - 8
                if y < wa.Top + 4:
                    y = wa.Top + 8
                self.Left, self.Top = x, y
            except Exception as e:
                _log('位置夹回工作区失败: %r' % (e,))

        def _set_circle_region(self, size=None):
            """窗口 Region：优先按 ball.png 的 alpha 剪影，缺资源时退回圆。

            名字沿用第 269 项的 `_set_circle_region`（测试与文档都按它查），
            实际行为已经是"跟图标同形的剪影"—— 这就是为什么手柄不会被切掉。
            """
            try:
                import ctypes
                hwnd = int(self.Handle.ToInt64())
                g = self.CreateGraphics()
                w = int(size or self.Width)
                h = int(size or self.Height)
                path = GraphicsPath()
                bmp = _CACHE.scaled(w, h)
                runs = silhouettes(bmp, w, h) if bmp is not None else []
                if runs:
                    for (x, y, rw, rh) in runs:
                        path.AddRectangle(Rectangle(int(x), int(y), int(rw), int(rh)))
                    _log('剪影 Region：%d 条扫描线 %dx%d' % (len(runs), w, h))
                else:
                    # 兜底：圆（第 269 项那套）
                    path.AddEllipse(1, 1, max(1, w - 2), max(1, h - 2))
                    _log('退回圆形 Region（ball.png 不可用）')
                reg = Region(path)
                hrgn = reg.GetHrgn(g)
                ok = ctypes.windll.user32.SetWindowRgn(
                    ctypes.c_void_p(hwnd), ctypes.c_void_p(int(hrgn.ToInt64())), True)
                reg.Dispose()
                g.Dispose()
                path.Dispose()
                if not ok:
                    _log('Region 设置失败')
            except Exception as e:
                _log('Region 异常: %s: %s' % (type(e).__name__, e))

        def _make_toolwindow(self):
            """WS_EX_TOOLWINDOW：不占 Alt+Tab。属性设不了，直接改扩展样式。"""
            try:
                import ctypes
                hwnd = int(self.Handle.ToInt64())
                u = ctypes.windll.user32
                ex = u.GetWindowLongW(ctypes.c_void_p(hwnd), -20)
                u.SetWindowLongW(ctypes.c_void_p(hwnd), -20, ex | 0x80)
            except Exception as e:
                _log('TOOLWINDOW 设置失败: %r' % (e,))

        def _on_shown(self, sender, args):
            """Show 之后才能生效的三件事：尺寸、置顶、工具窗样式。"""
            self._apply_ball_geometry()
            try:
                # 覆盖别人的置顶：Show 之前设的 TopMost 实测没落到 exstyle 上
                self.TopMost = True
                self.Activate()
            except Exception as e:
                _log('置顶设置失败: %r' % (e,))
            self._make_toolwindow()
            _log('已显示 %dx%d @ (%d,%d) png=%s' % (
                self.Width, self.Height, self.Left, self.Top, _CACHE.source() is not None))
            try:
                self.Invalidate()
            except Exception:
                pass

        # -- 绘制 ----------------------------------------------------
        def _dpi_x(self):
            if self._dpi:
                return self._dpi
            try:
                g = self.CreateGraphics()
                self._dpi = float(g.DpiX) or 96.0
                g.Dispose()
            except Exception:
                self._dpi = 96.0
            return self._dpi

        def _brand_font(self):
            if self._m_font is not None:
                return self._m_font
            # 用**像素**单位而不是磅：球窗只有 50 设备像素，磅会随 DPI 放大糊出球外
            for name in ('Segoe UI', 'Microsoft YaHei UI', 'Arial'):
                try:
                    self._m_font = Font(name, 18.0, FontStyle.Bold, GraphicsUnit.Pixel)
                    break
                except Exception:
                    continue
            return self._m_font

        def _on_paint(self, sender, e):
            """画官方标志：直接把 assets/ball.png 高质量缩放贴上去。

            第 272 项换掉手绘的原因：50px 下手绘的 5.2px Pen 环 + 18px Font M
            必然发糊（用户原话「你那个太模糊了」）。这里 160x160 的源图缩到
            设备像素，双三次 + HighQuality 像素偏移，边缘干净得多。
            """
            g = e.Graphics
            g.SmoothingMode = SmoothingMode.AntiAlias
            try:
                g.Clear(Color.FromArgb(*BALL_BACK))
            except Exception:
                pass

            w, h = self.Width, self.Height
            if self._paint_png(g, w, h):
                self._use_png = True
                self._paint_state_overlay(g, w, h)
                return

            # 资源缺失（打包漏了 assets/ball.png）时兜底：手绘版总比空白球好
            if self._use_png is None:
                self._use_png = False
                _log('ball.png 不可用，退回手绘图标')
            self._paint_fallback(g)

        def _paint_png(self, g, w, h):
            bmp = _CACHE.scaled(w, h)
            if bmp is None:
                return False
            try:
                g.InterpolationMode = InterpolationMode.HighQualityBicubic
                g.PixelOffsetMode = PixelOffsetMode.HighQuality
                g.CompositingQuality = CompositingQuality.HighQuality
                g.DrawImage(bmp, Rectangle(0, 0, w, h))
                return True
            except Exception as ex:
                _log('贴图失败: %s: %s' % (type(ex).__name__, ex))
                return False

        def _paint_state_overlay(self, g, w, h):
            """悬停/按下的反馈：整块轻扫一层白，长按时再套一圈白环。

            第 269 项那套是改渐变和环色；换成图片之后没法改图里的颜色，
            就改成叠色 —— 视觉上等价（亮一点 = 可点）。
            """
            if not (self._hover or self._long_pressed):
                return
            try:
                a = 34 if self._long_pressed else 18
                br = SolidBrush(Color.FromArgb(a, 255, 255, 255))
                g.FillRectangle(br, Rectangle(0, 0, w, h))
                br.Dispose()
                if self._long_pressed:
                    pen = Pen(Color.FromArgb(210, 255, 255, 255), 2.0)
                    g.DrawEllipse(pen, Rectangle(4, 4, max(1, w - 9), max(1, h - 9)))
                    pen.Dispose()
            except Exception as ex:
                _log('状态叠色失败: %s: %s' % (type(ex).__name__, ex))

        def _paint_fallback(self, g):
            """第 269 项的手绘版（仅在 ball.png 缺失时兜底）。

            青环玻璃圆 + 白 M + 橙色手柄。`_long_pressed` 时外圈白脉冲。
            """
            if self._long_pressed:
                glass_top, glass_bot = (226, 247, 253), (190, 228, 245)
            elif self._hover:
                glass_top, glass_bot = (234, 249, 254), (200, 232, 248)
            else:
                glass_top, glass_bot = BALL_GLASS_TOP, BALL_GLASS_BOT
            brush = LinearGradientBrush(Rectangle(1, 1, 48, 48),
                                        Color.FromArgb(*glass_top),
                                        Color.FromArgb(*glass_bot),
                                        LinearGradientMode.Vertical)
            g.FillEllipse(brush, Rectangle(1, 1, 48, 48))
            brush.Dispose()

            # 橙色手柄：先画，再让青环压在上面 —— 就是图标里那根插进玻璃的橙柄
            hb = LinearGradientBrush(Rectangle(26, 26, 20, 20),
                                     Color.FromArgb(*HANDLE_TOP),
                                     Color.FromArgb(*HANDLE_BOT),
                                     LinearGradientMode.ForwardDiagonal)
            hp = Pen(hb, 6.0)
            hp.StartCap = LineCap.Round
            hp.EndCap = LineCap.Round
            g.DrawLine(hp, Point(33, 33), Point(43, 43))
            hp.Dispose()
            hb.Dispose()

            # 青环
            ring_c = BALL_RING_PRESS if self._long_pressed else (
                BALL_RING_HOVER if self._hover else BALL_RING)
            ring = Pen(Color.FromArgb(*ring_c), 5.2)
            g.DrawEllipse(ring, Rectangle(3, 3, 44, 44))
            ring.Dispose()

            # 顶部内侧高光（一点点玻璃反光，别做太重）
            hl = SolidBrush(Color.FromArgb(70, 255, 255, 255))
            g.FillEllipse(hl, Rectangle(10, 6, 18, 11))
            hl.Dispose()

            # 白色 M（先画一层浅灰蓝投影，浅蓝玻璃上才不糊成一片）
            font = self._brand_font()
            if font is not None:
                shadow = SolidBrush(Color.FromArgb(105, *BALL_M_SHADOW))
                hi = SolidBrush(Color.FromArgb(*BALL_M_HI))
                try:
                    sf = StringFormat()
                    sf.Alignment = StringAlignment.Center
                    sf.LineAlignment = StringAlignment.Center
                    g.DrawString('M', font, shadow, RectangleF(-2, 0, 50, 46), sf)
                    g.DrawString('M', font, hi, RectangleF(-2, -1, 50, 46), sf)
                except Exception as ex:
                    _log('M 绘制退回简单版: %r' % (ex,))
                    g.DrawString('M', font, hi, 16, 15)
                shadow.Dispose()
                hi.Dispose()

            # 长按触发：外圈白脉冲
            if self._long_pressed:
                pulse_pen = Pen(Color.FromArgb(230, 255, 255, 255), 2.0)
                g.DrawEllipse(pulse_pen, Rectangle(5, 5, 40, 40))
                pulse_pen.Dispose()

        # -- 右键菜单 -------------------------------------------------
        def _build_context_menu(self):
            L = ball_labels()
            menu = ContextMenuStrip()
            menu.ShowImageMargin = False

            item_search = ToolStripMenuItem(L['search'])
            item_search.Click += lambda s, e: self._fire_click('menu')
            menu.Items.Add(item_search)

            # 第 287 项：详细搜索框（就是主窗口），补上「悬浮球只能开快搜」的缺口
            item_detail = ToolStripMenuItem(L['detail'])
            item_detail.Click += lambda s, e: self._fire_detail()
            menu.Items.Add(item_detail)

            item_snip = ToolStripMenuItem(L['snip'])
            item_snip.Click += lambda s, e: self._fire_snip()
            menu.Items.Add(item_snip)

            menu.Items.Add("-")

            # 第 287 项：聚合翻译不是独立窗口，走主窗口 ?panel=translate 摊开弹层
            item_tr = ToolStripMenuItem(L['translate'])
            item_tr.Click += lambda s, e: self._fire_translate()
            menu.Items.Add(item_tr)

            menu.Items.Add("-")

            item_cfg = ToolStripMenuItem(L['settings'])
            item_cfg.Click += lambda s, e: self._fire_settings()
            menu.Items.Add(item_cfg)

            item_hide = ToolStripMenuItem(L['hide'])
            item_hide.Click += lambda s, e: self._fire_disable()
            menu.Items.Add(item_hide)

            # 第 284 项：留引用，弹菜单前按当前界面语言重刷文案
            self._menu_items = (item_search, item_detail, item_snip, item_tr,
                                item_cfg, item_hide)
            return menu

        def _refresh_menu_labels(self):
            """右键弹出前重刷一次菜单文案 —— 用户在设置里换了语言后不必重启。"""
            try:
                L = ball_labels()
                items = getattr(self, '_menu_items', ())
                for it, key in zip(items, ('search', 'detail', 'snip', 'translate',
                                         'settings', 'hide')):
                    it.Text = L[key]
            except Exception as e:
                _log('刷新菜单文案失败: %s: %s' % (type(e).__name__, e))

        # -- 回调派发（每条都带日志，异常不许静默） ---------------------
        def _spawn(self, name, fn, *args):
            if not fn:
                _log('%s 无回调' % name)
                return

            def _run():
                try:
                    _log('%s 开始' % name)
                    fn(*args)
                    _log('%s 结束' % name)
                except Exception as e:
                    _log('%s EXCEPTION: %s: %s' % (name, type(e).__name__, e))

            threading.Thread(target=_run, daemon=True).start()

        def _fire_click(self, src='mouse'):
            _log('单击触发（来源 %s）' % src)
            self._spawn('on_click', self._on_click)

        def _fire_snip(self):
            self._spawn('on_snip', self._on_snip)

        def _fire_detail(self):
            self._spawn('on_detail', self._on_detail)

        def _fire_translate(self):
            self._spawn('on_translate', self._on_translate)

        def _fire_settings(self):
            self._spawn('on_settings', self._on_settings)

        def _fire_disable(self):
            self._spawn('on_disable', self._on_disable)

        def _on_long_press_tick(self, sender, args):
            if self._is_down and not self._dragged:
                self._long_pressed = True
                self._long_timer.Stop()
                self.Invalidate()
                self._fire_snip()

        # -- 鼠标（订阅式） --------------------------------------------
        def _on_mouse_down(self, sender, e):
            if e.Button == MouseButtons.Left:
                self._is_down = True
                self._dragged = False
                self._long_pressed = False
                self._down_screen_pos = Cursor.Position
                self._form_start_pos = (self.Left, self.Top)
                try:
                    self.Capture = True       # 拖到球外松手也能收到 MouseUp
                except Exception:
                    pass
                self._long_timer.Start()
            elif e.Button == MouseButtons.Right:
                self._refresh_menu_labels()
                self._menu.Show(self, e.Location)

        def _on_mouse_move(self, sender, e):
            if not self._is_down:
                return
            cur_pos = Cursor.Position
            dx = cur_pos.X - self._down_screen_pos.X
            dy = cur_pos.Y - self._down_screen_pos.Y
            if not self._dragged and is_drag(dx, dy, self._dpi_x()):
                self._dragged = True
                self._long_timer.Stop()
                _log('开始拖拽 dx=%d dy=%d dpi=%.0f' % (dx, dy, self._dpi_x()))
            if self._dragged:
                self.Left = self._form_start_pos[0] + dx
                self.Top = self._form_start_pos[1] + dy

        def _on_mouse_up(self, sender, e):
            if e.Button != MouseButtons.Left:
                return
            was_down = self._is_down
            self._is_down = False
            self._long_timer.Stop()
            try:
                self.Capture = False
            except Exception:
                pass
            _log('松手 down=%s dragged=%s long=%s' % (was_down, self._dragged, self._long_pressed))
            if was_down and not self._dragged and not self._long_pressed:
                self._fire_click()
            elif self._dragged:
                try:
                    wa = Screen.FromControl(self).WorkingArea
                    x, y = self.Left, self.Top
                    if abs(x - wa.Left) < 25:
                        x = wa.Left + 8
                    elif abs(x + self.Width - wa.Right) < 25:
                        x = wa.Right - self.Width - 8
                    self.Left = int(x)
                    self.Top = int(y)
                except Exception:
                    pass
                if self._on_pos_changed:
                    self._spawn('on_pos_changed', self._on_pos_changed, self.Left, self.Top)

            self._long_pressed = False
            self.Invalidate()

        def _on_mouse_enter(self, sender, e):
            self._hover = True
            self.Cursor = Cursors.Hand
            self.Invalidate()

        def _on_mouse_leave(self, sender, e):
            self._hover = False
            self.Cursor = Cursors.Default
            self.Invalidate()


class FloatingBallManager:
    """悬浮球生命周期管理器。"""

    def __init__(self, on_click=None, on_snip=None, on_detail=None, on_translate=None,
                 on_settings=None, on_disable=None, on_pos_changed=None):
        self.on_click = on_click
        self.on_snip = on_snip
        self.on_detail = on_detail
        self.on_translate = on_translate
        self.on_settings = on_settings
        self.on_disable = on_disable
        self.on_pos_changed = on_pos_changed

        self._form = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()

    def is_running(self):
        with self._lock:
            return bool(self._running and self._form is not None)

    def start(self, x=-1, y=-1):
        if not HAS_WINFORMS:
            return False
        with self._lock:
            if self._running:
                return True
            self._running = True

        def _sta_run():
            try:
                form = FloatingBallForm(
                    on_click=self.on_click,
                    on_snip=self.on_snip,
                    on_detail=self.on_detail,
                    on_translate=self.on_translate,
                    on_settings=self.on_settings,
                    on_disable=self.on_disable,
                    on_pos_changed=self.on_pos_changed,
                    init_x=x, init_y=y
                )
                with self._lock:
                    self._form = form
                _log('窗体就绪 %dx%d @ (%d,%d)' % (form.Width, form.Height, form.Left, form.Top))
                Application.Run(form)
                _log('消息循环退出')
            except Exception as e:
                _log('STA 线程 EXCEPTION: %s: %s' % (type(e).__name__, e))
            finally:
                with self._lock:
                    self._form = None
                    self._running = False

        th = Thread(ThreadStart(_sta_run))
        th.SetApartmentState(ApartmentState.STA)
        th.IsBackground = True
        th.Start()
        self._thread = th
        return True

    def stop(self):
        """关掉悬浮球，**不长时间阻塞调用方**（最多 400ms，不再 1.5 秒）。"""
        with self._lock:
            form = self._form
            if not self._running or form is None:
                self._running = False
                return
            self._running = False

        try:
            form.BeginInvoke(System.Action(form.Close))
            _log('已派发 Close')
        except Exception as e:
            _log('BeginInvoke Close 失败: %s: %s' % (type(e).__name__, e))

        th = self._thread
        if th is not None:
            try:
                th.Join(400)
            except Exception:
                pass
        self._thread = None
        with self._lock:
            if self._form is form:
                self._form = None
            self._running = False


# 全局单例
_MGR = None

def get_manager(on_click=None, on_snip=None, on_detail=None, on_translate=None,
                on_settings=None, on_disable=None, on_pos_changed=None):
    global _MGR
    if _MGR is None:
        _MGR = FloatingBallManager(
            on_click=on_click,
            on_snip=on_snip,
            on_detail=on_detail,
            on_translate=on_translate,
            on_settings=on_settings,
            on_disable=on_disable,
            on_pos_changed=on_pos_changed
        )
    return _MGR
