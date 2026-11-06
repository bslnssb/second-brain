# -*- coding: utf-8 -*-
"""brain.py 纯函数与安全边界的单元测试（不碰数据库、不联网、不启动窗口）。

覆盖第 96 项的官方链接白名单 / 赞赏码指纹，以及 Listary 同款排序的几档优先级。
跑法：python -m unittest discover -s tests -p "test_*.py" -v
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402

# 测试用的默认 Python 可能没装 pypinyin（构建用的 3.12 才有），
# 拼音相关用例自动跳过；降级路径仍然要测，不能抛异常。
HAS_PYPINYIN = importlib.util.find_spec('pypinyin') is not None


class UrlWhitelistTest(unittest.TestCase):
    def test_official_links_allowed(self):
        for u in (brain.OFFICIAL_REPO, brain.OFFICIAL_RELEASES, brain.OFFICIAL_ISSUES,
                  brain.OFFICIAL_REPO + '/releases/tag/v0.9.48'):
            self.assertTrue(brain.url_allowed(u), u)

    def test_lookalike_domains_rejected(self):
        """startswith(repo) 写错就能被 second-brain-evil 这类域名骗过去。"""
        repo = brain.OFFICIAL_REPO
        for u in (repo + '-evil', repo + '.evil.com', repo + 'evil',
                  'https://evil.example.com' + repo,
                  'http://github.com/bslnssb/second-brain',
                  'javascript:alert(1)', '', None, '   '):
            self.assertFalse(brain.url_allowed(u), repr(u))


class FingerprintTest(unittest.TestCase):
    def test_file_sha256_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x.bin'
            data = os.urandom(4096)
            p.write_bytes(data)
            self.assertEqual(brain.file_sha256(p), hashlib.sha256(data).hexdigest())

    def test_missing_file_returns_empty_string(self):
        self.assertEqual(brain.file_sha256('/definitely/not/here'), '')

    def test_limit_stops_after_first_chunk(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x.bin'
            p.write_bytes(b'a' * 300000)
            partial = brain.file_sha256(p, limit=10)
            self.assertEqual(len(partial), 64)
            self.assertNotEqual(partial, brain.file_sha256(p))

    def test_shipped_donate_matches_constant(self):
        """换收款码忘了同步改 DONATE_SHA256，这条立刻红灯。"""
        p = ROOT / 'assets' / 'donate.png'
        self.assertTrue(p.is_file())
        self.assertEqual(brain.file_sha256(p), brain.DONATE_SHA256)


class RankTest(unittest.TestCase):
    def test_mini_rank_prefix_boundary_middle_miss(self):
        self.assertEqual(brain._mini_rank('report.docx', 'report'), 0)
        self.assertEqual(brain._mini_rank('2026_report.docx', 'report'), 1)
        self.assertEqual(brain._mini_rank('myreport.docx', 'report'), 2)
        self.assertEqual(brain._mini_rank('notes.txt', 'report'), 3)

    def test_py_rank_levels(self):
        self.assertEqual(brain._py_rank(1, 'x', 'q'), 2.4)
        self.assertEqual(brain._py_rank(2, 'x', 'q'), 3.0)
        self.assertEqual(brain._py_rank(3, 'x', 'q'), 3.2)

    def test_py_keys_never_raises(self):
        full, ini = brain.py_keys('随便什么名字')
        self.assertIsInstance(full, str)
        self.assertIsInstance(ini, str)

    @unittest.skipUnless(HAS_PYPINYIN, '测试环境没装 pypinyin')
    def test_py_keys_chinese(self):
        self.assertEqual(brain.py_keys('产品迭代周报'), ('chanpindiedaizhoubao', 'cpddzb'))
        self.assertEqual(brain.py_keys('2026 Q3')[0], '2026q3')


class LauncherPathTest(unittest.TestCase):
    def test_only_launcher_dir_and_its_subdirs(self):
        with tempfile.TemporaryDirectory() as d:
            desk = Path(d) / 'Desktop'
            old = brain._LAUNCHER_NC
            brain._LAUNCHER_NC = None
            try:
                with mock.patch.object(brain, 'launcher_dirs', return_value=[str(desk)]):
                    self.assertTrue(brain._is_launcher_path(str(desk / '微信.lnk')))
                    self.assertTrue(brain._is_launcher_path(str(desk / '子目录' / '应用.lnk')))
                    self.assertFalse(brain._is_launcher_path(str(Path(d) / 'Other' / 'x.exe')))
                    self.assertFalse(brain._is_launcher_path(str(Path(d) / 'DesktopBackup' / 'x.exe')))
            finally:
                brain._LAUNCHER_NC = old


if __name__ == '__main__':
    unittest.main()
