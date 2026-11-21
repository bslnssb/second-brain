# -*- coding: utf-8 -*-
"""第 384 项 · 数据目录挑选（plat.paths.pick_data_dir）。

这一层的价值全在「Windows 行为必须逐字不变」：程序目录优先，写不进去才退
`%LOCALAPPDATA%\\SecondBrain\\data`；给了 SB_DATA_DIR 就照它办、不偷偷换地方。
mac / linux 走另一条兜底链（`~/Memex-data` / `~/.memex`）。

`probe` 参数只为单测注入（真实路径永远真写文件再删），避免用 chmod 之类的
平台差异手段去伪造「写不进去」。
"""

import os
import tempfile
import unittest
from unittest import mock

import plat.paths as paths


class TestPickDataDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='memex384-')
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_windows_install_writable_stays_in_install_dir(self):
        """Windows 基线：程序目录能写就**永远**用它（现状 = 装哪存哪）。"""
        d, fb = paths.pick_data_dir(plat='win32', env={}, install=self.tmp)
        self.assertEqual(d, self.tmp)
        self.assertFalse(fb)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, '.sb_write_test')))

    def test_windows_falls_back_to_localappdata_secondbrain_data(self):
        """写不进去时退到 `%LOCALAPPDATA%\\SecondBrain\\data`，并且要报 fallback=True。"""
        local = os.path.join(self.tmp, 'Local')
        want = os.path.join(local, 'SecondBrain', 'data')
        d, fb = paths.pick_data_dir(plat='win32', env={'LOCALAPPDATA': local},
                                    install=os.path.join(self.tmp, 'install'),
                                    probe=lambda p: p == want)
        self.assertTrue(fb)
        self.assertEqual(d, want)

    def test_windows_localappdata_fallback_when_env_missing(self):
        """没有 LOCALAPPDATA 的老兜底：`~/AppData/Local/SecondBrain/data`（与老代码一致）。"""
        home = os.path.join(self.tmp, 'home')
        d, fb = paths.pick_data_dir(plat='win32', env={}, install=os.path.join(self.tmp, 'i'),
                                    home=home, probe=lambda p: p.startswith(home))
        self.assertTrue(fb)
        self.assertEqual(d, os.path.join(home, 'AppData', 'Local', 'SecondBrain', 'data'))

    def test_sb_data_dir_wins_over_install(self):
        """SB_DATA_DIR 优先级高于程序目录（老代码同样的口径）。"""
        forced = os.path.join(self.tmp, 'forced')
        d, fb = paths.pick_data_dir(plat='win32', env={'SB_DATA_DIR': forced},
                                    install=os.path.join(self.tmp, 'install'))
        self.assertEqual(d, forced)
        self.assertFalse(fb)
        self.assertTrue(os.path.isdir(forced))     # 给了就尽力建出来

    def test_both_unwritable_keeps_first_candidate(self):
        """两条都写不进时不要假装成功：保留首选目录、fallback 报 False。"""
        install = os.path.join(self.tmp, 'i')
        d, fb = paths.pick_data_dir(plat='win32', env={'LOCALAPPDATA': os.path.join(self.tmp, 'L')},
                                    install=install, probe=lambda p: False)
        self.assertEqual(d, install)
        self.assertFalse(fb)

    def test_mac_uses_library_application_support(self):
        """mac 数据目录：`~/Library/Application Support/Memex`，且**不**用程序目录。"""
        home = os.path.join(self.tmp, 'home')
        d, fb = paths.pick_data_dir(plat='darwin', env={}, install='/Applications/Memex.app', home=home)
        self.assertEqual(d, os.path.join(home, 'Library', 'Application Support', 'Memex'))
        self.assertFalse(fb)
        self.assertTrue(os.path.isdir(d))

    def test_mac_fallback_when_library_unwritable(self):
        home = os.path.join(self.tmp, 'home')
        d, fb = paths.pick_data_dir(plat='darwin', env={}, home=home,
                                    probe=lambda p: p == os.path.join(home, 'Memex-data'))
        self.assertTrue(fb)
        self.assertEqual(d, os.path.join(home, 'Memex-data'))

    def test_linux_uses_xdg_data_home(self):
        home = os.path.join(self.tmp, 'home')
        xdg = os.path.join(self.tmp, 'xdg')
        d, fb = paths.pick_data_dir(plat='linux', env={'XDG_DATA_HOME': xdg}, home=home)
        self.assertEqual(d, os.path.join(xdg, 'memex'))
        self.assertFalse(fb)

    def test_linux_fallback_is_dot_memex(self):
        home = os.path.join(self.tmp, 'home')
        d, fb = paths.pick_data_dir(plat='linux', env={}, home=home,
                                    probe=lambda p: p == os.path.join(home, '.memex'))
        self.assertTrue(fb)
        self.assertEqual(d, os.path.join(home, '.memex'))


class TestBrainUsesPathsLayer(unittest.TestCase):
    """brain.app_dir() 现在只是这层的薄壳：必须真的调到这层，不能又自己算一遍。"""

    def test_app_dir_delegates_to_pick_data_dir(self):
        import brain
        with mock.patch.object(paths, 'pick_data_dir', return_value=('/fake/memex384', False)) as m:
            brain._DATA_DIR.clear()
            try:
                got = brain.app_dir()
                self.assertEqual(str(got).replace('\\', '/'), '/fake/memex384')
                self.assertTrue(m.called, 'brain.app_dir() 没有走 plat.paths.pick_data_dir')
            finally:
                brain._DATA_DIR.clear()
                brain.app_dir()                    # 复位缓存，别污染后面的测试


if __name__ == '__main__':
    unittest.main()
