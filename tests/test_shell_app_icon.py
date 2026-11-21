# -*- coding: utf-8 -*-
"""第 248 项回归：商店应用结果显示空白纸图标。

症状（用户截图）：搜 `watt` 能出 "Watt Toolkit"，但左侧图标是一张空白白纸。
根因是两层各错一半、合起来才暴露：

  ① /api/mini 给虚拟应用返回的 `e=''`（AUMID 不是文件名，没有扩展名），
     `/api/icon` 走 `sys_icon('')` → 通用空白纸；
  ② 快搜窗 `ico()` 只在 `.exe/.lnk` 时才把 `p=` 传给后端，虚拟入口
     连「按路径取图标」的机会都没有；主窗口 RowIcon 虽然传了 p，
     后端却只认 `.exe/.lnk`，一样落到空白纸。

为什么以前没抓到：第 246 项只验了「搜得到 / 名字对 / 能启动」，
8 条测试里没有一条问过「这行的图标长什么样」。跨层字段契约
（e 为空 + p 是虚拟路径 + 图标接口只认扩展名）必须一起钉。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app_entries  # noqa: E402
import brain  # noqa: E402


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


WATT = r'shell:AppsFolder\4651ED44255E.47979655102CE_k6txddmbb6c52!App'


class ShellAppIconTest(unittest.TestCase):

    def test_01_path_and_aumid_shape(self):
        """虚拟入口必须同时满足前缀 + 合法 AUMID，普通路径/伪路径都要挡掉。"""
        self.assertTrue(app_entries.is_shell_app_path(WATT))
        self.assertTrue(app_entries.is_virtual_appid(WATT[len(app_entries.SHELL_PREFIX):]))
        self.assertFalse(app_entries.is_shell_app_path(r'C:\Windows\notepad.exe'))
        # 前缀函数只认开头；挡住路径穿越的是 AUMID 形状校验（第二道闸）。
        self.assertTrue(app_entries.is_shell_app_path(
            r'shell:AppsFolder\..\..\Windows\system32\cmd.exe'))
        self.assertFalse(app_entries.is_virtual_appid(
            r'..\..\Windows\system32\cmd.exe'))
        self.assertFalse(app_entries.is_virtual_appid('not-an-aumid'))

    def test_02_wiring_contract(self):
        """四层接线缺一层就是空白纸：后端函数 / 路由 / 快搜窗 / 主窗口。"""
        b = read('brain.py')
        a = read('web/src/App.tsx')
        for tok in ('def shell_app_icon(path, size=48):',
                    'BCC18B79-BA16-442F-80C4-8A59C30C463B',
                    'SHCreateItemFromParsingName',
                    'png = shell_app_icon(p)',
                    'app_entries.is_shell_app_path(p)',
                    "String(it.p).indexOf('shell:AppsFolder')===0",
                    "indexOf('shell:AppsFolder')===0)) q +=",
                    "'max-age=60' if (p and (ext in ('.exe', '.lnk') or app_entries.is_shell_app_path(p)))",
                    "fb='<svg class=\"fic\""):
            self.assertIn(tok, b, 'brain.py 缺接线：' + tok)
        self.assertIn('const isShellApp =', a)
        self.assertIn('if (isShellApp) return <IconApp size={15} />', a)
        # 反向：旧代码只给 exe/lnk 传 p，不能复活
        self.assertNotIn("if(!dir && (it.e==='.exe'||it.e==='.lnk') && it.p) q +=", b)
        # 顺序：虚拟图标分支必须排在通用 sys_icon 回落之前，否则永远吃空白纸
        i_shell = b.find('png = shell_app_icon(p)')
        i_fallback = b.find("png = sys_icon('' if isdir else ext, isdir)")
        self.assertTrue(0 <= i_shell < i_fallback, '虚拟图标分支被挪到通用回落之后')

    @unittest.skipUnless(os.name == 'nt', 'Win32 Shell 图标只在 Windows 上有')
    def test_03_real_icon_is_not_blank_paper(self):
        """真机：问 Shell 要图标，必须拿到彩色应用图，不是 sys_icon('') 那张空白纸。"""
        png = brain.shell_app_icon(WATT)
        if not png:
            self.skipTest('本机未安装该 AUMID 或 Shell 未就绪')
        generic = brain.sys_icon('', False)
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertNotEqual(png, generic, '取到的还是通用空白纸图标')
        self.assertNotEqual(hashlib.sha256(png).digest(),
                            hashlib.sha256(generic or b'').digest())
        from PIL import Image
        from io import BytesIO
        im = Image.open(BytesIO(png)).convert('RGBA')
        self.assertEqual((48, 48), im.size)
        self.assertGreater(len(im.getcolors(200000) or []), 30, '图标颜色太少，疑似占位图')

    @unittest.skipUnless(os.name == 'nt', 'Win32 Shell 图标只在 Windows 上有')
    def test_04_invalid_inputs_never_return_an_icon(self):
        """假 AUMID / 普通路径 / 带穿越的路径一律 None，不能把 /api/icon 变成任意解析器。"""
        self.assertIsNone(brain.shell_app_icon(r'C:\Windows\notepad.exe'))
        self.assertIsNone(brain.shell_app_icon(r'shell:AppsFolder\NoSuch_1234567890!App'))
        self.assertIsNone(brain.shell_app_icon(
            r'shell:AppsFolder\..\..\Windows\system32\cmd.exe'))

    @unittest.skipUnless(shutil.which('node'), 'node 不在 PATH')
    def test_05_mini_template_js_still_parses(self):
        """改 ico() 最容易把内联 JS 写坏；抽出来给 node 过一遍语法。"""
        tpl = max((v for v in vars(brain).values()
                   if isinstance(v, str) and '<script>' in v and 'function ico(' in v),
                  key=len)
        js = tpl[tpl.index('<script>') + len('<script>'):tpl.rindex('</script>')]
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'mini.js'
            f.write_text(js, encoding='utf-8')
            r = subprocess.run(['node', '--check', str(f)],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(0, r.returncode, r.stderr[:500])

    def test_06_api_route_keeps_safety_check(self):
        """虚拟分支必须保留「索引里存在」校验，不能裸调 Shell。"""
        b = read('brain.py')
        seg = b[b.find('if p and not isdir and app_entries.is_shell_app_path(p):'):]
        seg = seg[:seg.find("if p and not isdir and ext in ('.exe', '.lnk'):")]
        self.assertIn('ev_recent(p)', seg)
        self.assertIn('SELECT 1 FROM files WHERE path=? LIMIT 1', seg)
        self.assertIn('if allowed:', seg)


if __name__ == '__main__':
    unittest.main()
