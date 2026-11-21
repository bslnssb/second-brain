# -*- coding: utf-8 -*-
"""第 362 项：全局壁纸 —— 画面位置/放大可调 + 生效范围可按界面开关。

用户原话：
  1. 「人像上传后在快搜窗头被截断了，只能看到身体了，给用户自己调节」
  2. 「有些人就不想在设置页面和快搜窗口生效全局壁纸，只希望在详情搜索里生效」

所以这里断言：状态里必须有 pos_x / pos_y / zoom 与三个 scope 开关；
tune 能落盘并夹取范围；主窗与快搜窗都按同一套语义用它们。
"""
import pathlib
import unittest
from unittest import mock

import brain

ROOT = pathlib.Path(brain.__file__).resolve().parent
TPL = brain.MINI_TPL
APP = (ROOT / 'web/src/App.tsx').read_text(encoding='utf-8')
APPX = (ROOT / 'web/src/ui/appearance.tsx').read_text(encoding='utf-8')


class WallStateShapeTest(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.object(brain, '_settings_read', return_value={}):
            st = brain._wall_state()
        self.assertEqual(st['pos_x'], 50)
        self.assertEqual(st['pos_y'], 50)
        self.assertEqual(st['zoom'], 100)
        # 默认全开 = 老行为不变，老用户升级不会突然看不见壁纸
        self.assertTrue(st['scope_main'])
        self.assertTrue(st['scope_settings'])
        self.assertTrue(st['scope_mini'])

    def test_reads_saved_values(self):
        d = {'wall_pos_x': 20, 'wall_pos_y': 8, 'wall_zoom': 140,
             'wall_scope_main': True, 'wall_scope_settings': False,
             'wall_scope_mini': False}
        with mock.patch.object(brain, '_settings_read', return_value=d):
            st = brain._wall_state()
        self.assertEqual((st['pos_x'], st['pos_y'], st['zoom']), (20, 8, 140))
        self.assertFalse(st['scope_settings'])
        self.assertFalse(st['scope_mini'])


class WallTuneTest(unittest.TestCase):
    def setUp(self):
        self.d = {}
        self.patches = [
            mock.patch.object(brain, '_settings_read', side_effect=lambda: dict(self.d)),
            mock.patch.object(brain, '_settings_update', side_effect=self.d.update),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_tune_position_zoom(self):
        st = brain._wall_apply({'op': 'tune', 'pos_x': 12, 'pos_y': 5, 'zoom': 160})
        self.assertEqual((st['pos_x'], st['pos_y'], st['zoom']), (12, 5, 160))
        self.assertEqual(self.d['wall_pos_x'], 12)
        self.assertEqual(self.d['wall_zoom'], 160)

    def test_tune_scope(self):
        st = brain._wall_apply({'op': 'tune', 'scope_main': True,
                                'scope_settings': False, 'scope_mini': False})
        self.assertFalse(st['scope_settings'])
        self.assertFalse(st['scope_mini'])
        self.assertIs(self.d['wall_scope_settings'], False)

    def test_clamp(self):
        st = brain._wall_apply({'op': 'tune', 'pos_x': 999, 'pos_y': -40, 'zoom': 20})
        self.assertEqual(st['pos_x'], 100)
        self.assertEqual(st['pos_y'], 0)
        self.assertEqual(st['zoom'], 100)


class WallSurfacesTest(unittest.TestCase):
    def test_main_window_uses_focus_and_zoom(self):
        self.assertIn('objectPosition', APPX)
        self.assertIn('transformOrigin', APPX)

    def test_main_window_gated_by_scope(self):
        self.assertIn('showWall', APP)
        self.assertIn('wall.scope_settings', APP)
        self.assertIn('wall.scope_main', APP)
        # 铺壁纸和「根容器透明」必须同一个条件，否则会露底色
        self.assertIn('{showWall && <Wallpaper wall={wall} />}', APP)

    def test_mini_window_uses_focus_and_scope(self):
        self.assertIn('!w.scope_mini', TPL)
        self.assertIn('backgroundPosition', TPL)
        self.assertIn("transformOrigin", TPL)

    def test_settings_panel_has_controls(self):
        for needle in ('ap_wall_focus', 'ap_wall_zoom', 'ap_wall_scope',
                       'ap_wall_scope_main', 'ap_wall_scope_settings',
                       'ap_wall_scope_mini'):
            self.assertIn('__U_' if False else needle, APPX, '缺 %s' % needle)

    def test_i18n_keys_all_langs(self):
        i18n = (ROOT / 'web/src/lib/i18n.ts').read_text(encoding='utf-8')
        for k in ('ap_wall_focus', 'ap_wall_focus_hint', 'ap_wall_zoom', 'ap_wall_scope',
                  'ap_wall_scope_main', 'ap_wall_scope_settings', 'ap_wall_scope_mini',
                  'ap_wall_scope_hint'):
            self.assertEqual(i18n.count(k + ':'), 5, '%s 不是五语言都有' % k)


if __name__ == '__main__':
    unittest.main()
