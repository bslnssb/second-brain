# -*- coding: utf-8 -*-
"""scan_docs.py 的单元测试（全盘扫描的排除规则 / 小说判定 / 扫描根构造）。

只测纯逻辑，不真去扫盘。drive_targets 用 mock 控盘符存在性与 include，
避免依赖真实机器。

跑法：python -m unittest discover -s tests -p "test_*.py" -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scan_docs  # noqa: E402


class SkipSegmentTest(unittest.TestCase):
    def test_system_dirs_detected(self):
        for p in ('C:\\Windows\\System32', 'D:\\Program Files\\app',
                  'E:\\node_modules\\x', 'F:\\.git\\config'):
            self.assertTrue(scan_docs._has_skip_segment(p), p)

    def test_crossdevice_virtual_folder_flagged(self):
        # 第 122 项：Windows「跨设备」虚拟文件夹（手机映射）不索引——枚举它会
        # 弹「连接至 Windows」对话框，在用户机器上像木马行为
        for p in ('C:\\Users\\me\\CrossDevice\\OnePlus Ace 2 Pro',
                  'C:\\Users\\me\\CrossDevice\\OnePlus Ace 2 Pro\\storage\\Download'):
            self.assertTrue(scan_docs._has_skip_segment(p), p)

    def test_normal_dirs_not_flagged(self):
        for p in ('D:\\MyDocs', 'C:\\Users\\me\\Photos', 'E:\\Work\\报告'):
            self.assertFalse(scan_docs._has_skip_segment(p), p)


class NovelDirTest(unittest.TestCase):
    def test_novel_keywords(self):
        for p in ('D:\\小说写作素材', 'E:\\我的书屋', 'F:\\net novel', 'G:\\light_novel'):
            self.assertTrue(scan_docs.is_novel_dir(p), p)

    def test_non_novel(self):
        self.assertFalse(scan_docs.is_novel_dir('D:\\Work\\reports'))


class LoadIncludesTest(unittest.TestCase):
    def test_missing_scope_json_returns_empty(self):
        with mock.patch.object(scan_docs, 'app_dir', return_value=Path(tempfile.mkdtemp())):
            self.assertEqual(scan_docs.load_includes(), [])

    def test_reads_include_dirs(self):
        d = Path(tempfile.mkdtemp())
        (d / 'scope.json').write_text(
            json.dumps({'include_dirs': ['D:\\a', 'E:\\b']}), encoding='utf-8')
        with mock.patch.object(scan_docs, 'app_dir', return_value=d):
            self.assertEqual(scan_docs.load_includes(), ['D:\\a', 'E:\\b'])

    def test_bad_json_returns_empty(self):
        d = Path(tempfile.mkdtemp())
        (d / 'scope.json').write_text('not json{', encoding='utf-8')
        with mock.patch.object(scan_docs, 'app_dir', return_value=d):
            self.assertEqual(scan_docs.load_includes(), [])


class DriveTargetsTest(unittest.TestCase):
    def _mock(self, existing, system_drive, includes):
        exists = {'%s:\\' % c: (c in existing) for c in 'CDEFG'}
        return mock.patch.object(scan_docs.os.path, 'exists',
                                 side_effect=lambda p: exists.get(p, False)), \
            mock.patch.dict(os.environ, {'SystemDrive': system_drive}, clear=False), \
            mock.patch.object(scan_docs, 'load_includes', return_value=includes), \
            mock.patch.object(scan_docs.os.path, 'expanduser',
                              return_value='C:\\Users\\test'), \
            mock.patch.object(scan_docs.os.path, 'isdir', return_value=True)

    def test_system_drive_maps_to_home_others_to_root(self):
        m_exists, m_env, m_inc, m_exp, m_isdir = self._mock(
            existing='CD', system_drive='C:', includes=[])
        with m_exists, m_env, m_inc, m_exp, m_isdir:
            t = scan_docs.drive_targets()
        self.assertIn('C:\\Users\\test', t)   # 系统盘 -> 用户目录
        self.assertIn('D:\\', t)              # 其它盘 -> 盘根

    def test_includes_with_skip_segment_appended_once(self):
        # include 命中 SKIP_DIRS 子串才单独当根；重复的去重
        m_exists, m_env, m_inc, m_exp, m_isdir = self._mock(
            existing='C', system_drive='C:',
            includes=['C:\\Program Files\\Steam', 'C:\\Program Files\\Steam'])
        with m_exists, m_env, m_inc, m_exp, m_isdir:
            t = scan_docs.drive_targets()
        # 同一个 include 不应出现两次
        self.assertEqual(t.count('C:\\Program Files\\Steam'), 1)

    def test_includes_without_skip_segment_ignored(self):
        m_exists, m_env, m_inc, m_exp, m_isdir = self._mock(
            existing='C', system_drive='C:',
            includes=['D:\\MyWork'])   # D 盘不存在，且不含 SKIP_DIRS
        with m_exists, m_env, m_inc, m_exp, m_isdir:
            t = scan_docs.drive_targets()
        self.assertNotIn('D:\\MyWork', t)


if __name__ == '__main__':
    unittest.main()
