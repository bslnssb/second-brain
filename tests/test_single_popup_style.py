# -*- coding: utf-8 -*-
"""第 245 项：弹窗样式定稿只剩方案1（rich 卡片）——跨层契约回归测试。

用户原话（设置页截图）：「弹窗样式改成只有方案1的」。

症状：设置 → 快搜窗里「弹窗样式」仍是可切换的下拉框，展开有
  「方案2 · 紧凑表（像 Listary）」和「方案1 · 卡片（带正文片段）」两项。

为什么以前没抓到：
  第 201 项当年是「加两种样式」，`check_popup_style()` 门禁钉的正是
  「**两种样式都在位**」（`.tbl #pane` / `STYLE==='tbl'` / `qs_style_2`…），
  和用户现在的定稿方向相反；而单元测试里根本没有一条覆盖 qs_style。
  更关键的是这是**跨层契约**：设置页只删一个 <option>、后端仍默认 'tbl'、
  宿主 `_qs_style()` 仍读 settings.json、内联 JS 仍认 `o.style==='tbl'` ——
  四层各自看都「没坏」，合起来就会让老配置里的 'tbl' 把下线样式从后门带回来
  （而且用户根本无法在界面上改回去）。所以这里一次钉死四层 + 五语言文案。

覆盖：
  ① 设置页没有切换控件、没有方案2 文案，只剩固定展示的方案1
  ② 前端 Settings 类型不再有 'tbl' 取值
  ③ 五语言字典 qs_style_2 清零，qs_style / qs_style_1 仍各 5 份
  ④ 后端 GET 强制归一 rich、POST 写入强制 rich（老配置不会复活）
  ⑤ 宿主 _qs_style() 行为级验证：即使 settings.json 写着 tbl 也返回 rich
  ⑥ brain.py 内联 mini 页 JS/CSS 无 tbl 分支，且 rowCap/height/空状态仍是方案1 的算法
  ⑦ 抽出内联 JS 用 node --check 验语法（删分支最容易漏括号 / 留下悬空变量）

跑法：py -3.12 -m unittest tests.test_single_popup_style -v
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class SinglePopupStyleTest(unittest.TestCase):

    def test_01_settings_ui_has_no_switcher(self):
        """设置页：只剩一个不可交互的固定展示，不能有下拉框 / 方案2 选项。"""
        p = read('web/src/ui/panels.tsx')
        self.assertIn("<Row title={t('qs_style')}>", p)
        self.assertIn("{t('qs_style_1')}", p)
        self.assertNotIn('qs_style_2', p)
        self.assertNotIn('<option value="tbl">', p)
        self.assertNotIn('qs_style:', p)          # 不再写回这个字段
        # 这一行原本是 <select value={s.qs_style === 'rich' ? ...}>
        self.assertNotIn('s.qs_style', p)
        row = p[p.index("<Row title={t('qs_style')}>"):]
        row = row[:row.index('</Row>')]
        self.assertNotIn('<select', row)
        self.assertIn('<span', row)

    def test_02_api_type_drops_tbl(self):
        api = read('web/src/api.ts')
        self.assertNotIn("'tbl'", api)
        self.assertNotIn('qs_style?:', api)

    def test_03_i18n_five_languages_no_option2(self):
        i18 = read('web/src/lib/i18n.ts')
        self.assertEqual(5, i18.count('qs_style:'))
        self.assertEqual(5, i18.count('qs_style_1:'))
        self.assertEqual(0, i18.count('qs_style_2:'))

    def test_04_backend_normalizes_to_rich(self):
        """GET 归一 + POST 强制 rich：老 settings.json 的 tbl 不能复活方案2。"""
        b = read('brain.py')
        self.assertIn("d['qs_style'] = 'rich'", b)
        self.assertIn("patch['qs_style'] = 'rich'", b)
        self.assertNotIn("d.setdefault('qs_style', 'tbl')", b)
        self.assertNotIn("patch['qs_style'] = 'rich' if", b)

    def test_05_host_always_rich_even_with_old_settings(self):
        """行为级：伪造一份写着 tbl 的 settings.json，宿主仍必须返回 rich。"""
        import app_window
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / 'settings.json'
            fake.write_text(json.dumps({'qs_style': 'tbl'}), encoding='utf-8')
            with mock.patch.object(app_window.App, '_settings_path', return_value=fake):
                self.assertEqual('rich', app_window.App._qs_style())
        self.assertEqual('rich', app_window.App._qs_style())

    def test_06_inline_js_has_no_tbl_branch(self):
        b = read('brain.py')
        for token in ("STYLE==='tbl'", "STYLE=(o.style==='tbl')",
                      "STYLE=(s.style==='tbl')", "var tbl=(STYLE==='tbl')",
                      ".tbl #pane", ".tbl .row{", "classList.toggle('tbl'",
                      "style:STYLE", "STYLE='rich'", "classList.remove('tbl')"):
            self.assertNotIn(token, b, '内联页面仍残留方案2 分支：' + token)
        # 方案1 的算法仍在：rowCap 用 170/38/6，height 下限 120，空状态有行就收
        self.assertIn('var avail=(MAXH||360)-170;', b)
        self.assertIn('Math.min(6,Math.floor(avail/38))', b)
        self.assertIn('return Math.max(acc, 120);', b)
        self.assertIn("EMPTY.classList.toggle('hide', !!rows.length)", b)
        self.assertIn('var withKey=HAS_DIALOG;', b)

    def test_07_mini_inline_js_is_valid_javascript(self):
        """把模板里 <script> 抽出来，占位符替换成常量后交给 node --check。"""
        if not shutil.which('node'):
            self.skipTest('node 不在 PATH，跳过语法校验')
        import brain
        m = re.search(r'<script>(.*?)</script>', brain.MINI_TPL, re.S)
        self.assertIsNotNone(m, 'MINI_TPL 里没有 <script> 块')
        js = re.sub(r'__[A-Z0-9_]+__', '1', m.group(1))
        self.assertNotIn("STYLE==='tbl'", js)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'mini.js'
            f.write_text(js, encoding='utf-8')
            r = subprocess.run(['node', '--check', str(f)],
                               capture_output=True, text=True)
        self.assertEqual(0, r.returncode, '内联 JS 语法错误：' + r.stderr[:400])

    def test_08_preflight_guard_is_reversed(self):
        """门禁本身也要反向：不能再钉「两种样式都在位」。"""
        pf = read('preflight.py')
        self.assertIn('弹窗样式只剩方案1（方案2 全链路下线）', pf)
        self.assertIn("'qs_style_2' in i18 or 'qs_style_2' in front", pf)
        self.assertNotIn("need = ('qs_style', 'qs_style_1', 'qs_style_2')", pf)

    def test_09_rowcap_and_height_run_as_option1(self):
        """运行期：把 rowCap/height 抽出来用 node 真跑一次，确认走方案1 的账本。

        node --check 只验语法；删掉 tbl 分支后容易留下「变量还在用但没定义」的
        运行期 ReferenceError（这个项目踩过 SWP_ASYNCWINDOWPOS 那次）。
        """
        if not shutil.which('node'):
            self.skipTest('node 不在 PATH，跳过运行期校验')
        import brain
        html = brain.MINI_TPL
        cap = re.search(r'function rowCap\(\)\{.*?\n\}', html, re.S)
        hi = re.search(r'function height\(\)\{.*?\n\}', html, re.S)
        self.assertIsNotNone(cap)
        self.assertIsNotNone(hi)
        js = (
            "var SMALL=true, MAXH=360;\n"
            "var document={querySelector:function(){return {offsetHeight:0}},"
            "getElementById:function(){return null}};\n"
            + cap.group(0) + "\n" + hi.group(0) + "\n"
            + "console.log(JSON.stringify({cap:rowCap(), h:height()}));\n"
        )
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'probe.js'
            f.write_text(js, encoding='utf-8')
            r = subprocess.run(['node', str(f)], capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stderr[:400])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(5, out['cap'])     # (360-170)/38 = 5 行，方案2 会是 8 行
        self.assertEqual(120, out['h'])     # 方案1 下限 120，方案2 是 56


if __name__ == '__main__':
    unittest.main()
