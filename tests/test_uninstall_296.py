# -*- coding: utf-8 -*-
"""第 296 项：卸载 —— 脚本内容与两种模式的回归测试。"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location('uninstall296', os.path.join(ROOT, 'uninstall.py'))
un = importlib.util.module_from_spec(spec)
spec.loader.exec_module(un)

APP = r'C:\Users\Public\AppData\Local\Memex\App'


class ScriptTest(unittest.TestCase):
    def test_01_common_lines(self):
        s = un.build_script(APP, APP + r'\Memex.exe', keep_data=True, wait=3)
        self.assertIn('taskkill /F /IM Memex.exe', s, '主进程不退，后面的删除全会失败')
        self.assertIn('timeout /t 3', s, '要给主进程留退出时间')
        self.assertIn(r'HKCU\Software\Microsoft\Windows\CurrentVersion\Run', s, '开机自启项要删掉')
        self.assertIn(r'HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Memex', s,
                      '应用列表里的卸载项也要删掉，不然留个点不动的条目')
        self.assertIn('Memex.lnk', s, '桌面快捷方式要删')
        self.assertNotIn('%%~f0', s, 'batch 里自删必须写 %~f0')

    def test_02_keep_data_keeps_db_and_settings(self):
        s = un.build_script(APP, APP + r'\Memex.exe', keep_data=True)
        self.assertIn('"%s\\*"' % APP, s)
        self.assertIn('"%%~xF"==".db"', s, '索引库要留下')
        self.assertIn('"%%~nxF"=="settings.json"', s, '设置要留下')
        self.assertIn('"%%~nxF"=="backups"', s, '备份目录要留下')
        self.assertNotIn('rd /s /q "%s"' % APP, s, '保留数据时不该删整个目录')

    def test_03_full_removes_whole_dir(self):
        s = un.build_script(APP, APP + r'\Memex.exe', keep_data=False)
        self.assertIn('rd /s /q "%s"' % APP, s)
        self.assertNotIn('"%%~nxF"==".db"', s, '全删模式不需要逐个保留')

    def test_04_ascii_only(self):
        s = un.build_script(APP, APP + r'\Memex.exe', keep_data=True)
        bad = [ch for ch in s if ord(ch) > 127]
        self.assertEqual([], bad, 'cmd 里不能有中文/全角字符，代码页会把它们吃成乱码')

    def test_05_run_uninstall_refuses_missing_dir(self):
        ok, script = un.run_uninstall(app=r'Z:\definitely\not\here', keep_data=True)
        self.assertFalse(ok)
        self.assertEqual('', script)


class EntryTest(unittest.TestCase):
    def test_06_ensure_entry_requires_frozen_exe(self):
        # 源码跑（没有 sys.frozen / exe 不存在）时不该往注册表里写东西
        self.assertFalse(un.ensure_uninstall_entry('1.0'))

    def test_07_brain_has_uninstall_api(self):
        b = io_open(os.path.join(ROOT, 'brain.py'))
        self.assertIn("== '/api/uninstall'", b)
        self.assertIn("'need confirm'", b, '卸载必须先确认，不能误点')
        a = io_open(os.path.join(ROOT, 'app_window.py'))
        self.assertIn("'--uninstall'", a)
        self.assertIn('ensure_uninstall_entry', a, '启动时要登记「设置→应用」里的卸载项')


def io_open(p):
    import io
    with io.open(p, encoding='utf-8') as f:
        return f.read()


if __name__ == '__main__':
    unittest.main()
