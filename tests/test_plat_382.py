"""平台抽象层（第 382 项）的契约测试。

只测「派发」，不真开窗口：命令拼装是纯函数，所以能在 Windows 上直接把 darwin/linux 喂进去。
"""

import os
import unittest
from unittest import mock

from plat import paths, shell


class TestPlatformName(unittest.TestCase):
    def test_win(self):
        self.assertEqual(paths.platform_name('win32'), paths.WIN)
        self.assertEqual(paths.platform_name('win64'), paths.WIN)

    def test_mac(self):
        self.assertEqual(paths.platform_name('darwin'), paths.MAC)

    def test_linux(self):
        self.assertEqual(paths.platform_name('linux'), paths.LINUX)
        self.assertEqual(paths.platform_name('linux2'), paths.LINUX)

    def test_other(self):
        self.assertEqual(paths.platform_name('freebsd'), paths.OTHER)


class TestDataDir(unittest.TestCase):
    @mock.patch.dict(os.environ, {'LOCALAPPDATA': r'C:\Users\u\AppData\Local'})
    def test_win_keeps_install_dir(self):
        self.assertEqual(paths.data_dir('win32', install=r'C:\Users\u\AppData\Local\Memex\App'),
                         r'C:\Users\u\AppData\Local\Memex\App')

    def test_mac_uses_app_support(self):
        with mock.patch.object(paths, '_home', return_value='/Users/u'):
            self.assertEqual(paths.data_dir('darwin'),
                             os.path.join('/Users/u', 'Library', 'Application Support', 'Memex'))

    def test_linux_respects_xdg(self):
        env = {'XDG_DATA_HOME': '/home/u/.xdg'}
        with mock.patch.object(paths, '_home', return_value='/home/u'):
            self.assertEqual(paths.data_dir('linux', env=env), os.path.join('/home/u/.xdg', 'memex'))

    def test_linux_default(self):
        with mock.patch.object(paths, '_home', return_value='/home/u'):
            self.assertEqual(paths.data_dir('linux', env={}),
                             os.path.join('/home/u', '.local', 'share', 'memex'))


class TestShellDispatch(unittest.TestCase):
    def test_open_win_is_startfile(self):
        self.assertIsNone(shell.open_argv(r'C:\a.txt', 'win32'))

    def test_open_mac_linux(self):
        self.assertEqual(shell.open_argv('/a.txt', 'darwin'), ['open', '/a.txt'])
        self.assertEqual(shell.open_argv('/a.txt', 'linux'), ['xdg-open', '/a.txt'])

    def test_reveal_win_is_explorer_select(self):
        self.assertIsNone(shell.reveal_argv(r'C:\a.txt', 'win32'))

    def test_reveal_mac_linux(self):
        self.assertEqual(shell.reveal_argv('/a/b.txt', 'darwin'), ['open', '-R', '/a/b.txt'])
        self.assertEqual(shell.reveal_argv('/a/b.txt', 'linux'), ['xdg-open', '/a'])

    def test_open_path_windows_uses_startfile(self):
        with mock.patch.object(shell, 'platform_name', return_value=paths.WIN), \
                mock.patch.object(shell.os, 'startfile', create=True) as sf:
            shell.open_path(r'C:\a.txt')
        sf.assert_called_once_with(r'C:\a.txt')

    def test_open_path_mac_runs_open(self):
        with mock.patch.object(shell, 'platform_name', return_value=paths.MAC), \
                mock.patch.object(shell, '_run') as run:
            shell.open_path('/a.txt')
        run.assert_called_once_with(['open', '/a.txt'])

    def test_reveal_windows_keeps_popen(self):
        with mock.patch.object(shell, 'platform_name', return_value=paths.WIN), \
                mock.patch.object(shell.subprocess, 'Popen') as pop:
            shell.reveal(r'C:\a.txt')
        pop.assert_called_once_with(['explorer', '/select,', r'C:\a.txt'])

    def test_linux_reveal_falls_back_to_dir(self):
        """linux 上「选中」失败要退化成打开目录，不能直接报错。"""
        with mock.patch.object(shell, 'platform_name', return_value=paths.LINUX):
            calls = []

            def fake_run(argv):
                calls.append(argv)
                if len(calls) == 1:
                    raise RuntimeError('nautilus 不在')

            with mock.patch.object(shell, '_run', side_effect=fake_run):
                shell.reveal('/a/b.txt')
        self.assertEqual(calls[-1], ['xdg-open', '/a'])

    def test_empty_path_raises(self):
        with self.assertRaises(RuntimeError):
            shell.open_path('')
        with self.assertRaises(RuntimeError):
            shell.reveal('')


if __name__ == '__main__':
    unittest.main()

