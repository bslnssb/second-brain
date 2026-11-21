# -*- coding: utf-8 -*-
"""第 287 项：悬浮球右键加「详细搜索框」「聚合翻译」。

用户定下的顺序：唤出快搜栏 / 详细搜索框 / 屏幕截图(长点) / ─ / 聚合翻译 / ─ / 设置 / 隐藏悬浮球。
聚合翻译走主窗口 `?panel=translate`（TranslateModal 弹层），不是独立窗口。
"""
import ast
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

KEYS = ('search', 'detail', 'snip', 'translate', 'settings', 'hide')


def _src(rel):
    with io.open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


class BallMenu285(unittest.TestCase):
    def test_labels_six_keys_all_langs(self):
        import floating_ball as fb
        for lang in ('zh', 'en', 'es', 'pt', 'hi'):
            d = fb.BALL_L[lang]
            for k in KEYS:
                self.assertTrue(d.get(k), '%s 缺 %s' % (lang, k))
            self.assertEqual(len(d), len(KEYS), '%s 键数不对' % lang)

    def test_non_zh_no_chinese(self):
        import floating_ball as fb
        for lang in ('en', 'es', 'pt', 'hi'):
            for k, v in fb.BALL_L[lang].items():
                for ch in v:
                    self.assertFalse('\u4e00' <= ch <= '\u9fff',
                                     '%s.%s 还有中文: %s' % (lang, k, v))

    def test_menu_order(self):
        s = _src('floating_ball.py')
        i_search = s.index("L['search']")
        i_detail = s.index("L['detail']")
        i_snip = s.index("L['snip']")
        i_tr = s.index("L['translate']")
        i_cfg = s.index("L['settings']")
        i_hide = s.index("L['hide']")
        self.assertLess(i_search, i_detail)
        self.assertLess(i_detail, i_snip)
        self.assertLess(i_snip, i_tr)      # 中间隔着一条分隔线
        self.assertLess(i_tr, i_cfg)
        self.assertLess(i_cfg, i_hide)
        # 聚合翻译前面要有分隔线
        sep = s.rindex('menu.Items.Add("-")', 0, i_tr)
        self.assertGreater(sep, i_snip)

    def test_refresh_covers_new_items(self):
        s = _src('floating_ball.py')
        self.assertIn("('search', 'detail', 'snip', 'translate',", s)

    def test_manager_and_get_manager_pass_through(self):
        import floating_ball as fb
        import inspect
        for fn in (fb.get_manager, fb.FloatingBallManager.__init__):
            sig = inspect.signature(fn)
            self.assertIn('on_detail', sig.parameters)
            self.assertIn('on_translate', sig.parameters)

    def test_app_window_wires_callbacks(self):
        s = _src('app_window.py')
        self.assertIn('on_detail=self._on_ball_detail', s)
        self.assertIn('on_translate=self._on_ball_translate', s)
        self.assertIn('def _on_ball_detail', s)
        self.assertIn('def _on_ball_translate', s)
        i = s.index('def _on_ball_translate')
        self.assertIn("open_panel('translate')", s[i:i + 900])
        ast.parse(s)

    def test_frontend_accepts_panel_translate(self):
        s = _src(os.path.join('web', 'src', 'App.tsx'))
        self.assertIn('|translate)', s, '?panel= 正则没认 translate')
        i = s.index("m[1] === 'translate'")
        self.assertIn('setTransOpen(true)', s[i:i + 300])


if __name__ == '__main__':
    unittest.main()
