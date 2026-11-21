"""基于 WinForms 的自主全屏遮罩截图器（uTools / 微信截图交互风格）。

特性：
1. 毫秒级冻结当前全屏画面，置顶半透明黑色遮罩，绝不调用外部系统截图；
2. 十字准星光标，鼠标左键拖拽高亮选区，实时显示选区尺寸；
3. 双击选区或按 Enter 确认裁剪（无选区双击则截取全屏），按 Esc 或右键随时退出；
4. 纯本地原生渲染，绑定 .NET Public Events 确保 100% 触发重绘与按键事件；
5. 针对高 DPI 与多显示器进行像素级校准，杜绝位移与缩放模糊。
"""

import io
import os
import tempfile
import threading
import time
from PIL import Image, ImageGrab

try:
    import clr
    clr.AddReference('System.Windows.Forms')
    clr.AddReference('System.Drawing')
    import System
    from System.Windows.Forms import (
        Form, FormBorderStyle, FormStartPosition, Cursors, Screen,
        MouseButtons, Keys, DialogResult, SystemInformation, ControlStyles
    )
    from System.Drawing import (
        Color, SolidBrush, Pen, Rectangle, Bitmap, Graphics, Point, Size, Font, FontStyle, Brushes
    )
    HAS_WINFORMS = True
except Exception:
    HAS_WINFORMS = False

# 第 369 项：最近一次框选的屏幕矩形 (x, y, w, h)，WinForms 屏幕坐标（= DIP）。
# 截图翻译要把译文卡片贴在选区原位，必须知道选区在哪 —— 以前只回图，位置信息随窗销毁丢了。
LAST_RECT = None


if HAS_WINFORMS:
    class SnipOverlayForm(Form):
        def __init__(self, pil_bg):
            super().__init__()
            self.pil_bg = pil_bg
            self.cropped_pil = None

            # 转换为 .NET Bitmap（通过 BMP 临时文件内存映射，耗时仅 20ms，彻底根治 MemoryStream 的 3.7 秒 COM 阻塞）
            tmp_dir = os.path.join(tempfile.gettempdir(), 'MemexSnip')
            os.makedirs(tmp_dir, exist_ok=True)
            self._tmp_bmp = os.path.join(tmp_dir, f'snip_bg_{os.getpid()}_{int(time.time()*1000)}.bmp')
            pil_bg.save(self._tmp_bmp, format='BMP')
            self.bg_bmp = Bitmap(self._tmp_bmp)

            # 开启双缓冲防闪烁 + 自定义绘制
            self.SetStyle(
                ControlStyles.AllPaintingInWmPaint |
                ControlStyles.UserPaint |
                ControlStyles.OptimizedDoubleBuffer,
                True
            )
            self.UpdateStyles()

            # 窗体属性：无边框、全屏置顶、黑色底色（防白闪）、无任务栏图标、十字光标
            self.FormBorderStyle = getattr(FormBorderStyle, 'None')
            self.StartPosition = FormStartPosition.Manual
            self.BackColor = Color.Black
            vs = SystemInformation.VirtualScreen
            self.Location = Point(vs.X, vs.Y)
            self.Size = Size(vs.Width, vs.Height)
            self.TopMost = True
            self.ShowInTaskbar = False
            self.DoubleBuffered = True
            self.Cursor = Cursors.Cross

            # 校正位图 DPI，与屏幕实际渲染 DPI 保持 1:1，杜绝 GDI+ 自动缩放模糊/位移
            try:
                g_tmp = self.CreateGraphics()
                self.bg_bmp.SetResolution(g_tmp.DpiX, g_tmp.DpiY)
                g_tmp.Dispose()
            except Exception:
                pass

            self.start_p = None
            self.cur_p = None
            self.is_dragging = False
            self.has_rect = False
            self.sel_x = 0
            self.sel_y = 0
            self.sel_w = 0
            self.sel_h = 0

            # 样式笔刷
            self.mask_brush = SolidBrush(Color.FromArgb(95, 0, 0, 0))
            self.border_pen = Pen(Color.FromArgb(255, 79, 110, 247), 2)
            self.tip_brush = SolidBrush(Color.FromArgb(220, 24, 27, 33))
            self.font = Font("Segoe UI", 9.0, FontStyle.Regular)

            # 必须绑定 .NET public events（Python.NET 中无法直接重写 C# protected virtual 方法）
            self.Paint += self._handle_paint
            self.MouseDown += self._handle_mouse_down
            self.MouseMove += self._handle_mouse_move
            self.MouseUp += self._handle_mouse_up
            self.MouseDoubleClick += self._handle_double_click
            self.KeyDown += self._handle_key_down

        def _calc_rect(self):
            if not self.start_p or not self.cur_p:
                return 0, 0, 0, 0
            x = min(self.start_p.X, self.cur_p.X)
            y = min(self.start_p.Y, self.cur_p.Y)
            w = abs(self.cur_p.X - self.start_p.X)
            h = abs(self.cur_p.Y - self.start_p.Y)
            return x, y, w, h

        def _handle_paint(self, sender, e):
            try:
                g = e.Graphics
                cw = self.ClientSize.Width
                ch = self.ClientSize.Height

                # 1. 直接贴 1:1 原图（将整张底图映射到全屏客户区，自适应任何 DPI 与多显示器）
                rect_screen = Rectangle(0, 0, cw, ch)
                g.DrawImage(self.bg_bmp, rect_screen, 0, 0, self.bg_bmp.Width, self.bg_bmp.Height, System.Drawing.GraphicsUnit.Pixel)

                # 2. 几何 4 块半透明遮罩围绕选区（上、下、左、右），中间选区镂空高亮
                x, y, w, h = self._calc_rect()
                if w > 2 and h > 2:
                    cx = max(0, min(cw, x))
                    cy = max(0, min(ch, y))
                    sel_w = min(w, cw - cx)
                    sel_h = min(h, ch - cy)

                    # 上方遮罩
                    if cy > 0:
                        g.FillRectangle(self.mask_brush, 0, 0, cw, cy)
                    # 下方遮罩
                    bot_y = cy + sel_h
                    if bot_y < ch:
                        g.FillRectangle(self.mask_brush, 0, bot_y, cw, ch - bot_y)
                    # 左侧遮罩
                    if cx > 0 and sel_h > 0:
                        g.FillRectangle(self.mask_brush, 0, cy, cx, sel_h)
                    # 右侧遮罩
                    right_x = cx + sel_w
                    if right_x < cw and sel_h > 0:
                        g.FillRectangle(self.mask_brush, right_x, cy, cw - right_x, sel_h)

                    # 选区高亮矩形边框
                    sel_rect = Rectangle(cx, cy, sel_w, sel_h)
                    g.DrawRectangle(self.border_pen, sel_rect)

                    # 3. 绘制尺寸标签与操作提示（换算为物理像素真实尺寸展示）
                    sx = float(self.pil_bg.width) / float(max(1, cw))
                    sy = float(self.pil_bg.height) / float(max(1, ch))
                    phys_w = int(round(w * sx))
                    phys_h = int(round(h * sy))
                    tip = f" {phys_w} × {phys_h}   松开鼠标完成截图 · Esc / 右键取消 "
                    sz = g.MeasureString(tip, self.font)
                    tip_y = y - int(sz.Height) - 6
                    if tip_y < 5:
                        tip_y = y + 6
                    tip_x = max(5, min(x, cw - int(sz.Width) - 10))
                    g.FillRectangle(self.tip_brush, tip_x, tip_y, int(sz.Width) + 6, int(sz.Height) + 4)
                    g.DrawString(tip, self.font, Brushes.White, tip_x + 3, tip_y + 2)
                else:
                    # 未拖拽选区时全屏半透明遮罩
                    g.FillRectangle(self.mask_brush, rect_screen)
            except Exception:
                pass

        def _handle_mouse_down(self, sender, e):
            if e.Button == MouseButtons.Right:
                self.DialogResult = DialogResult.Cancel
                self.Close()
                return
            if e.Button == MouseButtons.Left:
                self.start_p = e.Location
                self.cur_p = e.Location
                self.is_dragging = True
                self.has_rect = False
                self.Capture = True
                self.Invalidate()

        def _handle_mouse_move(self, sender, e):
            if self.is_dragging:
                self.cur_p = e.Location
                self.Invalidate()

        def _handle_mouse_up(self, sender, e):
            if e.Button == MouseButtons.Left and self.is_dragging:
                self.is_dragging = False
                self.Capture = False
                self.cur_p = e.Location
                x, y, w, h = self._calc_rect()
                if w > 8 and h > 8:
                    # 框选松开鼠标即刻确认截图，彻底免除二次按 Enter 或双击的麻烦！
                    self._confirm(x, y, w, h)
                else:
                    self.Invalidate()

        def _handle_double_click(self, sender, e):
            if e.Button == MouseButtons.Left:
                x, y, w, h = self._calc_rect()
                if w > 8 and h > 8:
                    self._confirm(x, y, w, h)
                else:
                    # 未拖拽选区直接双击：截取全屏
                    self.cropped_pil = self.pil_bg.copy()
                    self.DialogResult = DialogResult.OK
                    self.Close()

        def _handle_key_down(self, sender, e):
            if e.KeyCode == Keys.Escape:
                self.DialogResult = DialogResult.Cancel
                self.Close()
            elif e.KeyCode == Keys.Enter:
                x, y, w, h = self._calc_rect()
                if w > 8 and h > 8:
                    self._confirm(x, y, w, h)
                else:
                    # 未拖拽选区直接按 Enter：截取全屏
                    self.cropped_pil = self.pil_bg.copy()
                    self.DialogResult = DialogResult.OK
                    self.Close()

        def _confirm(self, x, y, w, h):
            global LAST_RECT
            try:
                cw = max(1, self.ClientSize.Width)
                ch = max(1, self.ClientSize.Height)
                sx = float(self.pil_bg.width) / float(cw)
                sy = float(self.pil_bg.height) / float(ch)

                real_x = int(round(x * sx))
                real_y = int(round(y * sy))
                real_w = int(round(w * sx))
                real_h = int(round(h * sy))

                # 边界保护
                real_x = max(0, min(self.pil_bg.width - 1, real_x))
                real_y = max(0, min(self.pil_bg.height - 1, real_y))
                real_w = max(1, min(self.pil_bg.width - real_x, real_w))
                real_h = max(1, min(self.pil_bg.height - real_y, real_h))
                self.cropped_pil = self.pil_bg.crop((real_x, real_y, real_x + real_w, real_y + real_h))
                # 记下选区在屏幕上的位置（无边框窗体，客户区原点即窗体原点）
                LAST_RECT = (int(self.Left + x), int(self.Top + y), int(w), int(h))
            except Exception:
                self.cropped_pil = None
            self.DialogResult = DialogResult.OK
            self.Close()

        def Dispose(self, disposing=True):
            try:
                if self.mask_brush: self.mask_brush.Dispose()
                if self.border_pen: self.border_pen.Dispose()
                if self.tip_brush: self.tip_brush.Dispose()
                if self.font: self.font.Dispose()
                if self.bg_bmp: self.bg_bmp.Dispose()
            except Exception:
                pass
            try:
                if hasattr(self, '_tmp_bmp') and os.path.exists(self._tmp_bmp):
                    os.unlink(self._tmp_bmp)
            except Exception:
                pass
            try:
                super().Dispose(disposing)
            except Exception:
                pass


def prewarm():
    """在后台线程预热 WinForms / GDI+，消除首次截图时的加载延迟。"""
    try:
        if HAS_WINFORMS:
            _ = SystemInformation.VirtualScreen
    except Exception:
        pass


def capture_with_overlay():
    """抓取全屏并启动全屏置顶选区遮罩窗。返回裁剪后的 PIL Image（若取消则返回 None）。"""
    if not HAS_WINFORMS:
        return None

    # 1. 抓取当前全屏
    try:
        full_img = ImageGrab.grab(all_screens=True)
    except Exception as e:
        return None

    # 2. 必须在拥有 Windows STA 消息循环的线程中运行 Form.ShowDialog()
    res = {'img': None}

    try:
        import System.Threading as ST

        def worker():
            try:
                form = SnipOverlayForm(full_img)
                ret = form.ShowDialog()
                if ret == DialogResult.OK:
                    res['img'] = form.cropped_pil
                form.Dispose()
            except Exception:
                pass

        th = ST.Thread(ST.ThreadStart(worker))
        th.SetApartmentState(ST.ApartmentState.STA)
        th.Start()
        th.Join()
    except Exception:
        def worker2():
            try:
                form = SnipOverlayForm(full_img)
                ret = form.ShowDialog()
                if ret == DialogResult.OK:
                    res['img'] = form.cropped_pil
                form.Dispose()
            except Exception:
                pass
        t = threading.Thread(target=worker2)
        t.start()
        t.join()

    return res['img']
