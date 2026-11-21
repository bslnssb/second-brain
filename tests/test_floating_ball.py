# -*- coding: utf-8 -*-
"""第 269 项：桌面悬浮球 —— 点击没反应 / 图标不是 Memex / 开关卡几秒。

真机证据：debug.log 里 17:10~17:22 三次 `show_mini begin` 全是热键和托盘触发的，
用户点悬浮球那几次一条都没有 —— 说明 click 根本没发出去，不是「打开了没看见」。

三条不变量钉在这里：
1. 拖拽阈值必须按 DPI 缩放。旧代码写死 4 设备像素，3840x2160@150% 下只有
   2.7 逻辑像素，正常点一下的手抖就被判成拖拽 → OnMouseUp 只走拖拽分支，
   `_fire_click` 永远轮不到。这是「点了没反应」的真因。
2. 开关必须走 mtime 闸门的每拍快同步（`_sync_floating_ball_fast`），
   不能埋回 `tick % 16`(≈2.4s) 那个慢块里 —— 那就是用户说的「卡几秒」。
3. 单击回调要看**真实前台**，并且异常一定写进 debug.log，不许再被静默吞掉。
"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import floating_ball as fb

try:
    import app_window as aw
except Exception:                       # 无 pywebview 环境时跳过
    aw = None


class TestDragThreshold(unittest.TestCase):
    """拖拽阈值：逻辑 6px，按 DPI 换算成设备像素。"""

    def test_default_is_six(self):
        self.assertEqual(fb.drag_threshold(), 6.0)
        self.assertEqual(fb.drag_threshold(96), 6.0)

    def test_scales_with_dpi(self):
        self.assertEqual(fb.drag_threshold(144), 9.0)
        self.assertEqual(fb.drag_threshold(192), 12.0)

    def test_bad_dpi_falls_back(self):
        for bad in (0, -5, None, 'x', '', 96.0):
            self.assertGreaterEqual(fb.drag_threshold(bad), 6.0)

    def test_small_jitter_is_not_drag(self):
        """回归钉：旧的 4px 硬阈值会把 (4,4) 判成拖拽，单击就此被吞。"""
        self.assertFalse(fb.is_drag(4, 4, 96))
        self.assertFalse(fb.is_drag(-4, 0, 96))
        self.assertFalse(fb.is_drag(3, 5, 144))
        self.assertFalse(fb.is_drag(0, 0, 144))

    def test_real_move_is_drag(self):
        self.assertTrue(fb.is_drag(7, 0, 96))
        self.assertTrue(fb.is_drag(0, -8, 96))
        self.assertTrue(fb.is_drag(10, 10, 144))


class TestLogger(unittest.TestCase):
    def test_log_forwards_with_prefix(self):
        got = []
        fb.set_logger(lambda m: got.append(m))
        try:
            fb._log('测试一行')
            self.assertEqual(got, ['ball: 测试一行'])
        finally:
            fb.set_logger(None)

    def test_log_never_raises(self):
        def boom(msg):
            raise RuntimeError('log 坏了')
        fb.set_logger(boom)
        try:
            fb._log('不该炸')          # 不抛就算过
        finally:
            fb.set_logger(None)


class _FakeThread:
    def __init__(self):
        self.joined = []

    def Join(self, ms):
        self.joined.append(ms)


class _FakeForm:
    def __init__(self):
        self.closed = []
        self.Visible = True

    def BeginInvoke(self, action):
        self.closed.append(action)


class TestStopNotBlocking(unittest.TestCase):
    """关悬浮球最多等 400ms，不许再 1500ms 同步阻塞调用方。"""

    def test_join_is_capped(self):
        mgr = fb.FloatingBallManager()
        mgr._running = True
        mgr._form = _FakeForm()
        th = _FakeThread()
        mgr._thread = th
        t0 = time.time()
        mgr.stop()
        elapsed = time.time() - t0
        # _FakeThread.Join 只记参数不真等；断言上限从 1500 改成了 400
        self.assertEqual(th.joined, [400])
        self.assertLess(elapsed, 0.5)
        self.assertFalse(mgr.is_running())

    def test_stop_when_not_running_is_noop(self):
        mgr = fb.FloatingBallManager()
        mgr.stop()
        self.assertFalse(mgr.is_running())


class TestNoVirtualOverrides(unittest.TestCase):
    """第 269 项的真根因：本机 pythonnet **不回调 Python 子类的 virtual 覆写**。

    旧文件的 OnPaint / OnMouseDown / OnMouseUp / OnMouseMove / OnShown /
    OnHandleCreated / CreateParams 全是一次都没被调用（探针实锤），
    所以绘制、单击、拖拽、右键菜单整层都是死的 —— 用户点球没反应就是这个。
    这里钉死：不许再靠覆写接交互，必须用事件订阅。
    """

    SRC = (ROOT / 'floating_ball.py').read_text(encoding='utf-8')

    def test_no_override_handlers(self):
        for bad in ('def OnPaint(', 'def OnMouseDown(', 'def OnMouseUp(',
                    'def OnMouseMove(', 'def OnShown(', 'def OnHandleCreated(',
                    'def OnResize(', 'def WndProc(', 'def CreateParams(',
                    'def OnMouseEnter(', 'def OnMouseLeave('):
            self.assertNotIn(bad, self.SRC, '又用 virtual 覆写接交互了：%s' % bad)

    def test_uses_event_subscription(self):
        for good in ('self.Paint +=', 'self.MouseDown +=', 'self.MouseUp +=',
                     'self.MouseMove +=', 'self.MouseEnter +=', 'self.Shown +='):
            self.assertIn(good, self.SRC, '缺事件订阅：%s' % good)

    def test_geometry_and_region_are_forced(self):
        """窗口会被系统夹到 136/202px，Region 属性赋值也不落到 HWND 上。"""
        self.assertIn('Size(50, 50)', self.SRC)
        self.assertIn('SetWindowRgn', self.SRC)

    def test_topmost_after_show(self):
        """Show 之前设 TopMost 实测没落到 exstyle，必须在 Shown 里再设一次。"""
        self.assertIn('def _on_shown', self.SRC)
        self.assertIn('self.TopMost = True', self.SRC)


@unittest.skipIf(aw is None, '没有 pywebview/app_window')
class TestBallClick(unittest.TestCase):
    """单击回调以真实前台为准，异常必须留痕。"""

    class _Stub:
        def __init__(self, visible=True, hwnd=1234):
            self.mini_visible = visible
            self.hwnd_mini = hwnd
            self.calls = []

        def show_mini(self, *a, **k):
            self.calls.append('show')

        def hide_mini(self):
            self.calls.append('hide')

    def _run(self, stub, fg):
        logs = []
        with mock.patch.object(aw, 'foreground_is', return_value=fg), \
             mock.patch.object(aw, '_log_always', side_effect=lambda m: logs.append(m)):
            aw.App._on_ball_click(stub)
        return stub.calls, logs

    def test_visible_and_foreground_hides(self):
        calls, _ = self._run(self._Stub(visible=True), fg=True)
        self.assertEqual(calls, ['hide'])

    def test_visible_but_not_foreground_shows(self):
        calls, _ = self._run(self._Stub(visible=True), fg=False)
        self.assertEqual(calls, ['show'])

    def test_hidden_shows(self):
        calls, _ = self._run(self._Stub(visible=False), fg=False)
        self.assertEqual(calls, ['show'])

    def test_exception_is_logged(self):
        stub = self._Stub(visible=False)
        logs = []

        def boom(*a, **k):
            raise RuntimeError('show 炸了')

        stub.show_mini = boom
        with mock.patch.object(aw, 'foreground_is', return_value=False), \
             mock.patch.object(aw, '_log_always', side_effect=lambda m: logs.append(m)):
            with self.assertRaises(RuntimeError):
                aw.App._on_ball_click(stub)
        self.assertTrue(any('EXCEPTION' in m for m in logs), logs)


@unittest.skipIf(aw is None, '没有 pywebview/app_window')
class TestFastSync(unittest.TestCase):
    """settings.json 没变 → 不重复同步；变了 → 立刻同步一次。"""

    class _Stub:
        floating_ball_mgr = None
        _ball_last_want = False

        def __init__(self):
            self.synced = []

        def _sync_floating_ball(self, force=False):
            self.synced.append(force)

    def test_only_on_change(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'settings.json'
            p.write_text('{"floating_ball": false}', encoding='utf-8')
            stub = self._Stub()
            with mock.patch.object(aw.App, '_settings_path', return_value=p):
                aw.App._sync_floating_ball_fast(stub)      # 第一次：必同步
                self.assertEqual(len(stub.synced), 1)
                aw.App._sync_floating_ball_fast(stub)      # 文件没变：不重复
                self.assertEqual(len(stub.synced), 1)
                time.sleep(0.02)
                p.write_text('{"floating_ball": true}', encoding='utf-8')
                aw.App._sync_floating_ball_fast(stub)      # 变了：再同步
                self.assertEqual(len(stub.synced), 2)


class TestOfficialLogo(unittest.TestCase):
    """第 272 项：悬浮球改用官方标志图 + 修 Cursor 崩溃。

    用户两张图钉死两条：
    1. `标志.png`（青环 + 白 M + 橙手柄）当悬浮图标，原话「你那个太模糊了」
       —— 手绘那套（18px Font M + 5.2px Pen 环）50px 下必然糊，改成贴
       assets/ball.png；
    2. 「不要把手柄去掉」—— 手柄在圆外，纯 48px 圆 Region 会把它切掉，
       所以 Region 必须按图片 alpha 剪影生成。
    3. 另修 .NET 弹窗 `name 'Cursor' is not defined`：import 里必须有 Cursor，
       而且所有事件回调都要套 _guard，异常只进日志不弹框。
    """

    SRC = (ROOT / 'floating_ball.py').read_text(encoding='utf-8')

    def test_cursor_is_imported(self):
        """回归钉：只 import Cursors 不 import Cursor → 每次按下 NameError。"""
        self.assertIn('Cursor', self.SRC)
        head = self.SRC.split('HAS_WINFORMS = ')[0]
        self.assertIn('Cursor.Position', self.SRC)
        # 真正的判据：from System.Windows.Forms import (...) 那段里有 Cursor 这个名字
        self.assertRegex(head, r'from System\.Windows\.Forms import \([^)]*\bCursor\b')

    def test_handlers_are_guarded(self):
        """事件回调不许把异常冒到 .NET（冒上去就是「未处理的异常」对话框）。"""
        for ev in ('self.Paint += _guard', 'self.MouseDown += _guard',
                   'self.MouseUp += _guard', 'self.MouseMove += _guard',
                   'self.Shown += _guard'):
            self.assertIn(ev, self.SRC, '缺 _guard 包裹：%s' % ev)
        self.assertIn('def _guard(', self.SRC)

    def test_paints_png_not_hand_drawn(self):
        self.assertIn('def _paint_png(', self.SRC)
        self.assertIn('DrawImage', self.SRC)
        # 手绘版只能当兜底，不能还是主路径
        self.assertIn('def _paint_fallback(', self.SRC)
        main = self.SRC.split('def _on_paint(')[1].split('def _paint_png(')[0]
        self.assertIn('_paint_png', main)
        self.assertIn('_paint_fallback', main)

    def test_asset_exists_and_is_transparent(self):
        png = ROOT / 'assets' / 'ball.png'
        self.assertTrue(png.is_file(), '缺 assets/ball.png')
        head = png.read_bytes()[:8]
        self.assertEqual(head, b'\x89PNG\r\n\x1a\n')

    def test_asset_path_lookup(self):
        """打包后 cwd 不可靠：必须多路候选（源码目录 / _MEIPASS / exe 同级）。"""
        self.assertIn('_MEIPASS', self.SRC)
        found = fb._ball_png_path()
        self.assertTrue(found and found.endswith('ball.png'), found)


class _FakePixel:
    def __init__(self, a):
        self.A = a


class _FakeBitmap:
    """只实现 GetPixel，给 silhouettes 的纯函数测试用（不起窗体、不装 .NET）。"""

    def __init__(self, rows):
        self._rows = rows

    def GetPixel(self, x, y):
        return _FakePixel(self._rows[y][x])


class TestBallSize(unittest.TestCase):
    """球窗按 DPI 放大：50 **逻辑**像素，不是 50 设备像素。

    本机 3840x2160 @144dpi（150%），50 设备像素只有 33 逻辑像素 —— 标志里的
    M 和环被压成一坨，这是「太模糊」的一半原因（另一半是手绘本来就糙）。
    """

    def test_96_dpi_is_fifty(self):
        self.assertEqual(fb.ball_size(96), 50)
        self.assertEqual(fb.ball_size(), 50)

    def test_scales_with_dpi(self):
        self.assertEqual(fb.ball_size(144), 75)
        self.assertEqual(fb.ball_size(192), 100)
        # 50*120/96 = 62.5 → Python round 取偶数 62（不是 63），别按四舍五入想当然
        self.assertEqual(fb.ball_size(120), 62)

    def test_bad_dpi_falls_back(self):
        for bad in (0, -1, None, 'x', ''):
            self.assertEqual(fb.ball_size(bad), 50)

    def test_never_smaller_than_40(self):
        self.assertEqual(fb.ball_size(48), 40)

    def test_geometry_uses_ball_size(self):
        src = (ROOT / 'floating_ball.py').read_text(encoding='utf-8')
        geo = src.split('def _apply_ball_geometry(')[1].split('def _clamp_to_workarea')[0]
        self.assertIn('ball_size(', geo)


class TestSilhouette(unittest.TestCase):
    """Region 剪影：按 alpha 扫描线拼形状，手柄必须留在窗口里。"""

    def test_full_square(self):
        bmp = _FakeBitmap([[255] * 4 for _ in range(3)])
        runs = fb.silhouettes(bmp, 4, 3)
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0], (0, 0, 4, 1))

    def test_splits_into_runs(self):
        bmp = _FakeBitmap([[255, 0, 0, 255, 255]])
        runs = fb.silhouettes(bmp, 5, 1)
        self.assertEqual(runs, [(0, 0, 1, 1), (3, 0, 2, 1)])

    def test_threshold_ignores_faint_edges(self):
        bmp = _FakeBitmap([[10, 40, 41, 200]])
        runs = fb.silhouettes(bmp, 4, 1)
        self.assertEqual(runs, [(1, 0, 3, 1)])

    def test_handle_corner_is_kept(self):
        """右下角那撮像素就是手柄：剪影必须把它包含进去（用户要求别切掉手柄）。"""
        rows = [[([255, 0, 0, 255][x]) for x in range(4)] for y in range(4)]
        rows[3] = [0, 0, 0, 255]
        bmp = _FakeBitmap(rows)
        runs = fb.silhouettes(bmp, 4, 4)
        self.assertIn((3, 3, 1, 1), runs)

    def test_empty_bitmap_is_safe(self):
        self.assertEqual(fb.silhouettes(None, 3, 3), [])


if __name__ == '__main__':
    unittest.main()

