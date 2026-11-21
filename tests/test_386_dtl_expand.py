# -*- coding: utf-8 -*-
"""第 386 项：详情栏「默认展开」做成设置项（默认关 = 原来那条窄栏）。

用户原话：「'详情'是否默认展开，这个添加到设置里，有的人就喜欢默认展开的」——
所以要点是**默认值必须是 false**（不能顺手把所有现有用户的面板都改宽），
打开后选中文件就摊开成大栏，并且抽屉里一改主界面立刻跟着变。
"""

import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    for enc in ('utf-8', 'utf-8-sig'):
        try:
            with open(os.path.join(HERE, *p), encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise AssertionError('decode failed: %r' % (p,))


class TestDetailExpandSetting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = read('brain.py')
        cls.app = read(os.path.join('web', 'src', 'App.tsx'))
        cls.panels = read(os.path.join('web', 'src', 'ui', 'panels.tsx'))
        cls.api = read(os.path.join('web', 'src', 'api.ts'))
        cls.i18n = read(os.path.join('web', 'src', 'lib', 'i18n.ts'))

    def test_backend_default_false(self):
        self.assertIn("d.setdefault('dtl_expand', False)", self.b)
        # PATCH 白名单必须收这个键，否则打开开关不落盘
        self.assertIn("'sb_counts', 'dtl_expand'", self.b)

    def test_api_type(self):
        self.assertIn('dtl_expand?: boolean', self.api)

    def test_toggle_in_settings_general(self):
        self.assertIn('on={s.dtl_expand === true}', self.panels)
        self.assertIn('patch({ dtl_expand: s.dtl_expand !== true })', self.panels)

    def test_preview_defaults_narrow(self):
        # Preview 缺省必须是 false，老用户界面一点不变
        self.assertIn('defaultExpand = false', self.panels)
        self.assertIn('useState(!!defaultExpand)', self.panels)

    def test_app_wires_setting_into_preview(self):
        self.assertIn('setDtlExpand(r.dtl_expand === true)', self.app)
        self.assertIn('defaultExpand={dtlExpand}', self.app)
        # 抽屉里改完立刻生效（不用重开详情栏）
        self.assertIn('p.dtl_expand !== undefined ? p.dtl_expand : cur', self.app)

    def test_i18n_keys_in_all_five(self):
        for k in ('dtl_expand', 'dtl_expand_label', 'dtl_expand_tip'):
            self.assertEqual(5, self.i18n.count(k + ':'), k)


if __name__ == '__main__':
    unittest.main()
