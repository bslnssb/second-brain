"""第 241 项 · 原生「选文件夹 / 选文件」对话框 → 主窗白屏（回归测试）

症状
----
点「选择文件夹…」/「➕ 手动添加文件夹」，**有概率**主窗整块白屏
（外壳在、WebView2 渲染层没了 —— 和第 109/111 项那种白板一模一样）。

根因
----
pywebview 的 JS API 是**另起线程**执行的（``webview/util.py``：
``thread = Thread(target=_call); thread.start()``），所以 ``Api.pick_folder``
整个跑在 **MTA 后台线程**上。

而 winforms 后端的 ``create_file_dialog`` 是**唯一没有 Invoke 回 UI 线程**的
窗口操作 —— 同文件里 ``set_title`` / ``show`` / ``hide`` / ``destroy_window`` /
``create_confirmation_dialog`` 全都有 ``if self.InvokeRequired: self.Invoke(...)``，
只有它没有（实测 ``platforms/winforms.py`` 第 866 行）。

于是原生模态对话框在后台线程上弹，owner 却是 UI 线程的主窗：
模态框要跨线程 disable/enable 这个 WebView2 宿主，控制器把渲染层藏起来，
用户看到的就是白屏。概率性 = 取决于线程时序，所以是「有概率」。

另一处缺陷
----------
``MiniApi`` 根本没有 ``pick_folder`` / ``pick_file``：快搜窗设置页的
「➕ 手动添加文件夹」调 ``api.pick_folder()`` 得到 undefined → TypeError，
只能退回 ``prompt()`` 手输路径，桌面版明明能弹原生选择框。

为什么以前没抓到
-----------------
单测不弹原生对话框；preflight 只钉字符串不验线程；
隔离环境里复现不了（没有产品的自绘标题栏 + 守卫线程组合）。
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'app_window.py')


def _src():
    with open(SRC, encoding='utf-8') as f:
        return f.read()


def _tree():
    return ast.parse(_src())


def _class(tree, name):
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    return None


def _methods(cls):
    return {m.name for m in cls.body if isinstance(m, ast.FunctionDef)}


class TestNativeDialogThread(unittest.TestCase):
    """原生对话框必须调度到 UI 线程（否则跨线程 disable WebView2 宿主 → 白屏）。"""

    def setUp(self):
        self.tree = _tree()
        self.api = _class(self.tree, 'Api')
        self.mini = _class(self.tree, 'MiniApi')
        self.assertIsNotNone(self.api, 'Api 类没了')
        self.assertIsNotNone(self.mini, 'MiniApi 类没了')
        self.src = _src()

    def test_api_has_ui_dialog_helper(self):
        """Api 必须有 _ui_dialog：把原生对话框 Invoke 回 UI 线程再弹。"""
        self.assertIn('_ui_dialog', _methods(self.api))

    def test_pick_folder_does_not_call_win_create_file_dialog_directly(self):
        """pick_folder / pick_file 不许直接调 self._win.create_file_dialog。

        那个入口带 @_shown_call（主窗 hidden 时 events.shown 永不置位 →
        卡满 20 秒才抛 WebViewException），而且最终仍落到没有 Invoke 的
        winforms.create_file_dialog 上。
        """
        for name in ('pick_folder', 'pick_file'):
            fn = next(m for m in self.api.body
                      if isinstance(m, ast.FunctionDef) and m.name == name)
            seg = ast.get_source_segment(self.src, fn) or ''
            self.assertNotIn('create_file_dialog', seg,
                             '%s 又直接调 create_file_dialog 了（跨线程弹框 → 白屏）' % name)
            self.assertIn('_ui_dialog', seg,
                          '%s 必须走 _ui_dialog（UI 线程调度）' % name)

    def test_ui_dialog_invokes_on_ui_thread(self):
        """_ui_dialog 必须真的 Invoke 回 UI 线程。"""
        fn = next(m for m in self.api.body
                  if isinstance(m, ast.FunctionDef) and m.name == '_ui_dialog')
        seg = ast.get_source_segment(self.src, fn) or ''
        self.assertIn('InvokeRequired', seg)
        self.assertIn('Invoke(', seg)

    def test_after_native_dialog_self_heals(self):
        """对话框关掉后要检查渲染层并自愈（白屏兜底）。"""
        self.assertIn('_after_native_dialog', _methods(self.api))
        fn = next(m for m in self.api.body
                  if isinstance(m, ast.FunctionDef) and m.name == '_after_native_dialog')
        seg = ast.get_source_segment(self.src, fn) or ''
        self.assertIn('_render_children_visible', seg,
                      '自愈前必须先判断渲染层是否被藏')
        self.assertIn('revive_webview', seg)

    def test_mini_api_exposes_pick_folder_and_pick_file(self):
        """快搜窗也要能弹原生选择框（第 241 项：以前整个是死路）。"""
        ms = _methods(self.mini)
        self.assertIn('pick_folder', ms,
                      'MiniApi 缺 pick_folder → 快搜窗「手动添加文件夹」点不动')
        self.assertIn('pick_file', ms, 'MiniApi 缺 pick_file')

    def test_main_api_has_app_backref(self):
        """Api 要能拿到 App（自愈要调 App._force_webview_visible）。"""
        fn = next(m for m in self.api.body
                  if isinstance(m, ast.FunctionDef) and m.name == '__init__')
        seg = ast.get_source_segment(self.src, fn) or ''
        self.assertIn('self._app', seg, 'Api 缺 _app 指针，自愈拿不到 _force_webview_visible')

    def test_uses_new_filedialog_enum(self):
        """用新的 webview.FileDialog 枚举，别再用已废弃的 FOLDER_DIALOG/OPEN_DIALOG。

        旧常量会往 stderr 打 deprecation 警告，将来 pywebview 会直接删掉。
        """
        self.assertIn('def _dialog_kind(', self.src)
        for name in ('pick_folder', 'pick_file'):
            fn = next(m for m in self.api.body
                      if isinstance(m, ast.FunctionDef) and m.name == name)
            seg = ast.get_source_segment(self.src, fn) or ''
            self.assertNotIn('webview.FOLDER_DIALOG', seg)
            self.assertNotIn('webview.OPEN_DIALOG', seg)
            self.assertIn('_dialog_kind(', seg)


class TestPywebviewBackendGap(unittest.TestCase):
    """记录 pywebview 上游的这个缺口，防止我们自己又绕回去。"""

    def test_upstream_create_file_dialog_has_no_invoke(self):
        """pywebview 的 create_file_dialog 没有 Invoke（这就是白屏的根因）。

        这里的断言是**记录事实**：如果哪天 pywebview 修好了，这测试会失败，
        提示我们可以简化 _ui_dialog 的实现。
        """
        try:
            import webview.platforms.winforms as wf
        except Exception:
            self.skipTest('拿不到 pywebview winforms 后端')
        path = getattr(wf, '__file__', None)
        if not path or not os.path.exists(path):
            self.skipTest('winforms 后端不是源码形式')
        with open(path, encoding='utf-8') as f:
            src = f.read()
        i = src.find('def create_file_dialog(')
        self.assertGreater(i, 0, 'pywebview 后端结构变了，需要重新核对')
        seg = src[i:i + 2600]
        # 同文件里其它窗口操作都有 InvokeRequired 判断
        self.assertNotIn('InvokeRequired', seg,
                         'pywebview 已经修了 create_file_dialog 的线程问题，'
                         '我们的 _ui_dialog 可以简化')


if __name__ == '__main__':
    unittest.main()
