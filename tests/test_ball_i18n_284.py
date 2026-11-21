# -*- coding: utf-8 -*-
"""第 284 项：悬浮球右键菜单文案跟随界面语言。

用户反馈：右键四项是硬编码中文，切到英文/西语/葡语/印地语界面还是中文。
"""
import ast
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SRC = os.path.join(ROOT, 'floating_ball.py')


def _src():
    with io.open(SRC, encoding='utf-8') as f:
        return f.read()


class BallMenuI18n284(unittest.TestCase):
    def test_labels_all_langs(self):
        import floating_ball as fb
        keys = ('search', 'snip', 'settings', 'hide')
        for lang in ('zh', 'en', 'es', 'pt', 'hi'):
            self.assertIn(lang, fb.BALL_L, '缺语言 ' + lang)
            d = fb.BALL_L[lang]
            for k in keys:
                self.assertTrue(d.get(k), '%s 缺 %s' % (lang, k))

    def test_non_zh_has_no_chinese(self):
        import floating_ball as fb
        for lang in ('en', 'es', 'pt', 'hi'):
            for k, v in fb.BALL_L[lang].items():
                for ch in v:
                    self.assertFalse('\u4e00' <= ch <= '\u9fff',
                                     '%s.%s 里还有中文: %s' % (lang, k, v))

    def test_ball_labels_fallback(self):
        import floating_ball as fb
        got = fb.ball_labels()
        self.assertIn(got['search'], [fb.BALL_L[lg]['search'] for lg in fb.BALL_L])

    def test_menu_uses_labels_not_literals(self):
        s = _src()
        build = s[s.index('def _build_context_menu'):]
        build = build[:build.index('def _refresh_menu_labels')]
        self.assertIn("L['search']", build)
        self.assertIn("L['hide']", build)
        self.assertNotIn('唤出快搜栏', build)
        self.assertNotIn('隐藏悬浮球', build)

    def test_refresh_before_show(self):
        s = _src()
        i = s.index('self._refresh_menu_labels()')
        j = s.index('self._menu.Show(self, e.Location)')
        self.assertLess(i, j, '必须先刷新文案再 Show')
        self.assertIn('def _refresh_menu_labels', s)

    def test_syntax(self):
        ast.parse(_src())


if __name__ == '__main__':
    unittest.main()
