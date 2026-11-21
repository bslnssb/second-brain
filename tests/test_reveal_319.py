# -*- coding: utf-8 -*-
"""第 319 项：右键「定位」为什么报「浏览器里没有定位」。

用户 11-19 10:5x 实测截图：搜索 ext:pdf 后右键第一条 → 提示
「浏览器里没有『定位』，桌面版可用」，可他用的是桌面版。

两个根因都在这里钉住：
1. `/api/files` 登记打开白名单时 `replace=True`，会把并发打进来的
   `/api`（正文命中）那一路刚登记的路径整表清掉 —— 点了正文命中的行，
   白名单里已经没有它，原生桥 `reveal` 直接回 False。
2. `Api.reveal` 拿 `ev_recent` 当唯一的准入门。定位只是「在资源管理器里选中」，
   不执行任何东西，却和「打开 exe」共用一张 5 分钟就过期的白名单；
   过期/被清掉时前端只能报一句指错方向的话。
"""
import io
import io as _io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with io.open(os.path.join(ROOT, name), 'r', encoding='utf-8') as f:
        return f.read()


class RevealWhitelistTest(unittest.TestCase):
    def test_api_files_does_not_replace_whitelist(self):
        """`/api/files` 必须 merge 登记，不能 replace —— 否则正文命中那条点不动。"""
        b = read('brain.py')
        seg = b[b.find("elif self.path.startswith('/api/files'):"):]
        seg = seg[:seg.find("elif self.path.split('?')[0] == '/api/web_search':")]
        self.assertNotIn('allow_open([x[\'p\'] for x in items], replace=True)', seg)
        self.assertIn("allow_open([x['p'] for x in items])", seg)

    def test_reveal_accepts_existing_path_without_ev_whitelist(self):
        """定位不再要求 ev_recent：路径存在就放行（定位不执行文件，风险等级不同）。"""
        w = read('app_window.py')
        i = w.find('    def reveal(self, p):')
        self.assertGreater(i, 0, '没找到 Api.reveal')
        seg = w[i:i + 1600]
        self.assertIn('os.path.exists(np)', seg)
        # 老写法：先判白名单就直接返回 False
        self.assertNotIn('if not p or not brain.ev_recent(p):', seg)

    def test_missing_bridge_and_failure_use_different_toasts(self):
        """桥不在 ≠ 定位失败：两句话分开，别把用户往错方向带。"""
        app = read(os.path.join('web', 'src', 'App.tsx'))
        self.assertIn("ctx_reveal_web", app)
        self.assertIn("ctx_reveal_fail", app)
        i18n = read(os.path.join('web', 'src', 'lib', 'i18n.ts'))
        self.assertEqual(i18n.count('ctx_reveal_fail:'), 5,
                         'ctx_reveal_fail 要在 5 种语言里都给文案（前端有对齐测试）')
        self.assertEqual(i18n.count('ctx_reveal_web:'), 5)

    def test_detail_panel_reveal_is_not_silent(self):
        """详情栏「定位」以前可选链 + 空 catch，点了像没反应。"""
        app = read(os.path.join('web', 'src', 'App.tsx'))
        seg = app[app.find('onReveal={async () => {'):]
        seg = seg[:seg.find('onCopy={() => {')]
        self.assertIn("if (!bridge?.reveal)", seg)
        self.assertIn("setToast(t('ctx_reveal_fail'))", seg)


if __name__ == '__main__':
    unittest.main()
