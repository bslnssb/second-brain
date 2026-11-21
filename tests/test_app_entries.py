# -*- coding: utf-8 -*-
"""第 246 项：Microsoft Store / MSIX 应用能被搜到、能被启动 —— 跨层契约回归测试。

用户原话：「watttokite这一类在微软商店的应用都是搜索不到为什么呢？为什么会犯这种错误？」

症状：搜 "Watt Toolkit" 0 条；`Get-StartApps` 里明明有它。

为什么以前没抓到（这才是重点）：
  ① 之前调研竞品只看**规则清单**（Recoll 的 4 个 skipped* 清单、Baloo 的 2 个默认数组、
     标记文件），**没看遍历层** —— 所以误以为「排除规则放宽就能扫到商店应用」。
  ② 真因是**模型层级错了**：商店应用的显示名在 Windows 壳命名空间，
     `Get-StartApps` 里 Watt Toolkit 的 AppID 是
     `4651ED44255E.47979655102CE_k6txddmbb6c52!App`（虚拟 AUMID），
     而 Steam 是 `H:\\STEAM\\steam.exe`（磁盘路径）。磁盘上既没有
     "Watt Toolkit.lnk"，`C:\\Program Files\\WindowsApps` 又对普通进程
     `PermissionError [WinError 5]`（`os.walk`/`QDirIterator`/`opendir` 都读不到）。
  ③ 现有 499 条测试里没有一条问过「搜应用名能不能出商店应用」，
     排除规则的测试全绿，合起来照样是 0 条。

覆盖：
  ① AUMID 判定 / 去重 / 排序 / 采集失败降级
  ② 虚拟入口写进 files 表后，关键词查询真能命中（不是只看源码字符串）
  ③ 中文显示名生成拼音字段
  ④ 启动走 explorer.exe shell:AppsFolder\\...，普通路径仍走 os.startfile
  ⑤ 跨层接线：索引重建 / 对账豁免 / HTTP /open / 原生桥 / 打包清单
  ⑥ 真实 Windows 壳解析（SHParseDisplayName）只在本机能跑时验，不启动应用

跑法：py -3.12 -m unittest tests.test_app_entries -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app_entries  # noqa: E402


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


# 用户机器上 Get-StartApps 的真实片段（2026-11-16 采集，脱敏只保留这三条）
REAL_ROWS = [
    {'Name': 'Watt Toolkit',
     'AppID': '4651ED44255E.47979655102CE_k6txddmbb6c52!App'},
    {'Name': 'Steam', 'AppID': r'H:\STEAM\steam.exe'},          # 传统 Win32，已有 lnk
    {'Name': 'Microsoft Clipchamp',
     'AppID': 'Clipchamp.Clipchamp_yxz26nhyzhsrt!App'},
    {'Name': '画图', 'AppID': 'Microsoft.Paint_8wekyb3d8bbwe!App'},
    {'Name': '', 'AppID': 'Bad.Empty_8wekyb3d8bbwe!App'},         # 脏数据
    {'Name': 'NoBang', 'AppID': 'electron.app.Antigravity'},       # 无 !，不是 AUMID
    'not-a-dict',
]


class AppEntriesTest(unittest.TestCase):

    def test_01_virtual_appid(self):
        self.assertTrue(app_entries.is_virtual_appid(
            '4651ED44255E.47979655102CE_k6txddmbb6c52!App'))
        self.assertTrue(app_entries.is_virtual_appid(
            'Microsoft.Paint_8wekyb3d8bbwe!App'))
        for bad in ('', r'H:\STEAM\steam.exe', 'electron.app.Antigravity',
                    r'C:\foo.exe!App', 'shell:AppsFolder\\x!App', None):
            self.assertFalse(app_entries.is_virtual_appid(bad), repr(bad))

    def test_02_collect_filters_and_sorts(self):
        got = app_entries.collect_start_apps(runner=lambda: REAL_ROWS)
        names = [n for n, _ in got]
        self.assertEqual(['Microsoft Clipchamp', 'Watt Toolkit', '画图'],
                         sorted(names, key=lambda x: x.lower()))
        self.assertNotIn('Steam', names)          # 磁盘上已有 steam.exe，别造重复
        self.assertNotIn('NoBang', names)
        self.assertTrue(all(p.startswith(app_entries.SHELL_PREFIX) for _, p in got))
        watt = dict(got)['Watt Toolkit']
        self.assertTrue(app_entries.is_shell_app_path(watt))
        self.assertFalse(app_entries.is_shell_app_path(r'H:\STEAM\steam.exe'))

    def test_03_collect_failure_is_silent(self):
        def boom():
            raise OSError('powershell 不在 PATH')
        self.assertEqual([], app_entries.collect_start_apps(runner=boom))
        self.assertEqual([], app_entries.collect_start_apps(runner=lambda: None))
        self.assertEqual([], app_entries.collect_start_apps(runner=lambda: {'Name': 'x'}))

    def test_04_rows_shape_and_pinyin(self):
        with mock.patch.object(app_entries, 'collect_start_apps',
                               return_value=[('Watt Toolkit', 'shell:AppsFolder\\A!App'),
                                             ('画图', 'shell:AppsFolder\\B!App')]):
            rows = app_entries.app_rows(py_keys=lambda s: ('huatu', 'ht'))
        self.assertEqual(2, len(rows))
        for r in rows:
            self.assertEqual(9, len(r))       # files_new 的九列，少一列重建就炸
            self.assertEqual('', r[3])        # ext 必须空：否则被类型筛选/图标逻辑误判
            self.assertEqual(0, r[4])
            self.assertTrue(r[0].startswith('shell:AppsFolder\\'))
        self.assertEqual('watt toolkit', rows[0][1])
        self.assertEqual('Watt Toolkit', rows[0][6])   # descr 存原始大小写显示名
        self.assertEqual('画图', rows[1][6])
        self.assertIsNone(rows[0][7])         # 纯英文名不必算拼音
        self.assertEqual(('huatu', 'ht'), (rows[1][7], rows[1][8]))

    def test_05_search_really_finds_store_app(self):
        """行为级：把虚拟入口插进真实的 files 表结构，用关键词 SQL 查一次。

        这是本次症状的核心 —— 只看「源码里写了 app_rows」会假绿，
        必须证明索引表 + 查询这条链路真能搜到 Watt Toolkit。
        """
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'brain.db'
            con = sqlite3.connect(db)
            con.execute('CREATE TABLE files(path TEXT PRIMARY KEY, name TEXT NOT NULL, '
                        'dir TEXT, ext TEXT, size REAL, mtime REAL, descr TEXT, '
                        'py TEXT, ini TEXT)')
            with mock.patch.object(app_entries, 'collect_start_apps',
                                   return_value=[('Watt Toolkit',
                                                  'shell:AppsFolder\\A!App')]):
                con.executemany('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)',
                                app_entries.app_rows())
            con.commit()
            hits = con.execute(
                "SELECT path, name FROM files WHERE name LIKE ?", ('%watt%',)).fetchall()
            self.assertEqual(1, len(hits))
            self.assertTrue(hits[0][0].startswith('shell:AppsFolder\\'))
            con.close()

    def test_06_launch_uses_explorer_for_shell_paths(self):
        with mock.patch.object(app_entries.subprocess, 'Popen') as popen, \
                mock.patch.object(app_entries.os, 'startfile') as start:
            app_entries.launch('shell:AppsFolder\\A!App')
            self.assertEqual(1, popen.call_count)
            args = popen.call_args[0][0]
            self.assertEqual('explorer.exe', args[0])
            self.assertEqual('shell:AppsFolder\\A!App', args[1])
            self.assertEqual(0, start.call_count)   # 不能退化成 startfile(shell:)
            app_entries.launch(r'D:\资料\a.txt')
            self.assertEqual(1, start.call_count)

    def test_07_cross_layer_wiring(self):
        """跨层契约：少接任何一层，商店应用都会「能扫到但用不起来」。"""
        b = read('brain.py')
        for tok in ('import app_entries', 'app_entries.app_rows(py_keys)',
                    'if app_entries.is_shell_app_path(p):',
                    'app_entries.launch(p)',
                    # 主搜索结果里那句 exists() 会把虚拟入口当死路径丢掉并 _mark_gone，
                    # 这是「写进库了却还是搜不到」的最后一道暗门（差点漏掉）。
                    'os.path.exists(p) or app_entries.is_shell_app_path(p)',
                    # 搜到了却只显示 AUMID 包名也算没修完：显示名走 files.descr。
                    'if _shell and descr:',
                    '_shell or apprank.is_app_file(name)',
                    "INSERT OR REPLACE INTO files_new"):
            self.assertIn(tok, b, 'brain.py 缺接线：' + tok)
        w = read('app_window.py')
        self.assertIn('import app_entries', w)
        self.assertEqual(2, w.count('app_entries.launch(p)'),
                         '主窗 / 快搜窗两个原生桥都要改')
        self.assertNotIn('os.startfile(p)', w)
        # 对账豁免必须排在文件系统规则之前（否则虚拟条目重建完又被清掉）
        i_guard = b.index('if app_entries.is_shell_app_path(p):')
        i_soft = b.index('if _soft_dir_file(p, ext):')
        self.assertLess(i_guard, i_soft)

    def test_08_shell_id_really_resolves(self):
        """Windows 本机：用 SHParseDisplayName 只解析 PIDL，不启动应用。

        能解析 = `explorer.exe shell:AppsFolder\\...` 这条启动路径是通的；
        顺手用一个不存在的 AUMID 证明这个检查不是永远返回成功。
        """
        if os.name != 'nt':
            self.skipTest('非 Windows')
        import ctypes
        from ctypes import wintypes
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 2)
            sh = ctypes.windll.shell32
            sh.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_void_p),
                                              wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD)]
            sh.SHParseDisplayName.restype = ctypes.c_long
        except Exception as e:
            self.skipTest('Shell32 不可用: %s' % e)

        def resolves(p):
            pidl, attrs = ctypes.c_void_p(), wintypes.DWORD(0)
            hr = sh.SHParseDisplayName(p, None, ctypes.byref(pidl), 0,
                                       ctypes.byref(attrs))
            return hr == 0

        self.assertTrue(resolves(r'shell:AppsFolder\Microsoft.WindowsStore_8wekyb3d8bbwe!App'))
        self.assertFalse(resolves(r'shell:AppsFolder\NoSuchPackage_1234567890!App'))


if __name__ == '__main__':
    unittest.main()
