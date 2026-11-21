# -*- coding: utf-8 -*-
"""第 297 项回归：系统程序（设置 / cmd / 记事本…）必须能在快搜窗直接搜到。

用户原话：「搜索『设置』和『cmd』都应该要能找到系统的程序打开，现在都没实现」。
之前搜不到不是扫描漏了：System32 是系统目录（默认排除）、「设置」是 MSIX 应用没有 .lnk、
cmd 也不是「已安装程序」。这些现在由 sys_apps 在查询时补，不写库。
"""
import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import brain  # noqa: E402
import sys_apps  # noqa: E402


class MatchTest(unittest.TestCase):
    def test_01_cmd(self):
        r = sys_apps.match('cmd', 'zh')
        self.assertEqual(1, len(r), 'cmd 只该给命令提示符一条，不该把截图/磁盘管理带出来')
        self.assertEqual('命令提示符', r[0]['name'])
        self.assertTrue(r[0]['path'].lower().endswith('system32\\cmd.exe'))
        self.assertEqual('.exe', r[0]['ext'])
        self.assertEqual(100, r[0]['ab'], '完全相等的命中必须给最高权重（排序第一键）')

    def test_02_settings_chinese_and_english(self):
        for q in ('设置', 'settings', 'shezhi'):
            r = sys_apps.match(q, 'zh')
            self.assertEqual(1, len(r), q)
            self.assertEqual('设置', r[0]['name'])
            self.assertTrue(r[0]['path'].startswith('shell:AppsFolder\\'))
            self.assertIn('immersivecontrolpanel', r[0]['path'])
            self.assertEqual('', r[0]['ext'], '虚拟应用入口没有扩展名（图标走 AUMID 那条路）')

    def test_03_english_ui_never_shows_chinese(self):
        r = sys_apps.match('cmd', 'en')
        self.assertEqual('Command Prompt', r[0]['name'])
        r = sys_apps.match('设置', 'en')
        self.assertEqual('Settings', r[0]['name'])
        for lg in ('es', 'pt', 'hi'):
            nm = sys_apps.match('cmd', lg)[0]['name']
            self.assertNotIn('命', nm, '英文/西语/葡语/印地语界面不能露中文（第 280 项的老毛病）')

    def test_04_no_single_letter_noise(self):
        for q in ('c', 'p', '设'):
            self.assertEqual([], sys_apps.match(q, 'zh'), '单字不去猜程序，否则满屏噪音')

    def test_05_prefix_still_ranks_below_exact(self):
        r = sys_apps.match('notepad', 'zh')
        self.assertEqual('记事本', r[0]['name'])
        self.assertEqual(100, r[0]['ab'])

    def test_06_missing_tool_is_skipped(self):
        """精简版 Windows 没有某个 exe 时，不许给一条点了没反应的结果。"""
        real = sys_apps._target
        sys_apps._target = lambda e: '' if 'notepad' in (e.get('path') or '') else real(e)
        try:
            r = sys_apps.match('notepad', 'zh')
            self.assertEqual([], r)
        finally:
            sys_apps._target = real

    def test_07_task_list(self):
        r = sys_apps.match('任务', 'zh')
        names = [x['name'] for x in r]
        self.assertIn('任务管理器', names)
        self.assertLessEqual(len(r), 5)


class MiniSearchTest(unittest.TestCase):
    """空库也要能出结果：证明它不依赖索引，而是查询时补的。"""

    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.execute('CREATE TABLE files (path TEXT PRIMARY KEY, name TEXT, ext TEXT,'
                         ' descr TEXT, mtime REAL, size INTEGER)')
        self.con.execute('CREATE TABLE appdesc (path TEXT PRIMARY KEY, descr TEXT)')

    def tearDown(self):
        self.con.close()

    def _q(self, q, lang='zh'):
        old = brain.boot_lang
        brain.boot_lang = lambda: lang
        try:
            return brain.mini_search(self.con, q, limit=9)
        finally:
            brain.boot_lang = old

    def test_08_cmd_in_empty_index(self):
        r = self._q('cmd')
        self.assertTrue(r['items'], '空库也要有 cmd')
        top = r['items'][0]
        self.assertEqual('命令提示符', top['dn'])
        self.assertEqual(1, top['app'], '得标记成应用，点击才会走 open_path 而不是当文件')
        self.assertTrue(top['p'].lower().endswith('cmd.exe'))

    def test_09_settings_in_empty_index(self):
        r = self._q('设置')
        self.assertTrue(r['items'])
        top = r['items'][0]
        self.assertEqual('设置', top['dn'])
        self.assertTrue(top['p'].startswith('shell:AppsFolder\\'))
        self.assertEqual('', top['e'], '虚拟应用 ext 为空，前端才走 AUMID 图标')

    def test_10_lang_switches_display_name(self):
        r = self._q('cmd', 'en')
        self.assertEqual('Command Prompt', r['items'][0]['dn'])

    def test_11_still_found_in_deep_mode(self):
        r = self._q('cmd')
        old = brain.boot_lang
        try:
            brain.boot_lang = lambda: 'zh'
            deep = brain.mini_search(self.con, 'cmd', limit=9, deep=True)
        finally:
            brain.boot_lang = old
        self.assertTrue(deep['items'])
        self.assertIn('命令提示符', [x['dn'] for x in deep['items']] + [r['items'][0]['dn']])


class WiringTest(unittest.TestCase):
    def test_12_brain_wires_sys_apps(self):
        with open(os.path.join(ROOT, 'brain.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('import sys_apps', src)
        self.assertIn('sys_apps.match(q, lang=boot_lang())', src,
                      '必须按当前界面语言取显示名，不能写死中文')

    def test_13_msc_display_name(self):
        self.assertEqual('任务计划程序', brain._disp_name('taskschd.msc', '.msc', '任务计划程序'))
        self.assertEqual('taskschd.msc', brain._disp_name('taskschd.msc', '.msc', ''),
                         '没有描述时还是显示文件名，不能凭空造名字')


if __name__ == '__main__':
    unittest.main()
