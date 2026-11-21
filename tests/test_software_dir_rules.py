# -*- coding: utf-8 -*-
"""已知软件目录名单 / 音乐平台目录 / 标记文件（第 243 项）的回归测试。

用户原话：
  「你是不是应该内置一个世界常用软件 top500 的目录来判断这个软件的目录
   该不该被索引呢？？」

调研竞品源码后的结论（这是本项的设计依据）：
  · **没有任何一个索引器靠「猜」来排除软件目录**。
    Recoll 的 recoll.conf 只有 skippedNames / noContentSuffixes / skippedPaths /
    nowalkfn 四个显式清单；Baloo 的 fileexcludefilters.cpp 是两个默认数组
    （文件通配 + 目录名通配）；GNOME Tracker 是 ignored-directories /
    ignored-files；Everything 的 size 上限**只作用于内容索引**。
  · 竞品里「用户自己标记某个目录别索引」的通行做法是**在目录里放标记文件**：
    Recoll `.recoll-noindex`、Tracker `.trackerignore`、macOS `.metadata_never_index`。
    Memex 取同一机制：`.memex-noindex`。

所以这里的名单**不是黑名单式地把目录整棵删掉**，而是「认出这是软件目录，
于是这棵子树只保留应用入口（exe/lnk…）」。三档设计：
  ① 具体软件名（chrome / pycharm / vlc …）→ 直接认定；
  ② 厂商名（tencent / adobe …）→ 还要该目录里确实有 exe 才认定；
  ③ 用户数据目录（网盘同步 / 聊天文件 / 我的目录 / 靶场）→ 优先豁免。

跑法：py -3.12 -m unittest tests.test_software_dir_rules -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402
import scan_docs  # noqa: E402


def _touch(base: Path, rel: str, content: bytes = b'x') -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


class TestSoftwareDirList(unittest.TestCase):
    """名单本身的行为。"""

    def test_list_size_is_substantial(self):
        """名单要有规模（用户要的是「世界常用软件 top500」）。"""
        self.assertGreaterEqual(len(scan_docs.SOFTWARE_DIR_NAMES), 500)
        self.assertGreaterEqual(len(scan_docs.SOFTWARE_VENDOR_DIRS), 20)
        self.assertGreaterEqual(len(scan_docs.MUSIC_APP_DIR_SEGS), 20)

    def test_known_software_name_matched(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _touch(base, 'pycharm64/bin/pycharm64.exe')
            _touch(base, 'vlc/video.avi')
            _touch(base, 'chrome/Application/chrome.exe')
            for rel in ('pycharm64/bin', 'vlc', 'chrome/Application'):
                self.assertTrue(scan_docs.is_known_software_dir(str(base / rel)),
                                '%s 应识别为软件目录' % rel)

    def test_versioned_dir_matched(self):
        """带版本号的目录名（pycharm64 / pycharm2023）也要认出来。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            self.assertTrue(scan_docs.is_known_software_dir(str(base / 'pycharm64')))
            self.assertTrue(scan_docs.is_known_software_dir(str(base / 'pycharm2023.1')))

    def test_vendor_needs_exe_evidence(self):
        """厂商名要「目录里有 exe」才认定 —— 用户的 D:\\Tencent\\资料 不能被当成软件。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            plain = base / 'Tencent'
            plain.mkdir()
            self.assertFalse(scan_docs.is_known_software_dir(str(plain)))
            _touch(base, 'Tencent/QQ/QQ.exe')
            self.assertTrue(scan_docs.is_known_software_dir(str(base / 'Tencent' / 'QQ')))

    def test_user_data_always_exempt(self):
        """用户数据目录优先豁免：网盘同步、聊天文件、我的目录。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            for rel in ('Dropbox', 'OneDrive', 'Tencent Files', 'WeChat Files',
                        'Documents', 'Downloads', '桌面', '下载', '百度网盘'):
                p = base / rel
                p.mkdir(parents=True, exist_ok=True)
                self.assertFalse(scan_docs.is_known_software_dir(str(p)),
                                 '%s 是用户数据，不该当软件目录' % rel)
                self.assertTrue(scan_docs.is_user_data_path(str(p)))

    def test_pentest_lab_exempt(self):
        """D:\\phpstudy_pro\\WWW 是渗透靶场 —— 一条都不能丢。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _touch(base, 'phpstudy_pro/phpstudy.exe')
            _touch(base, 'phpstudy_pro/WWW/dvwa/index.php')
            for rel in ('phpstudy_pro/WWW', 'phpstudy_pro/WWW/dvwa'):
                self.assertFalse(scan_docs.is_known_software_dir(str(base / rel)),
                                 '靶场 %s 必须豁免' % rel)
                self.assertTrue(scan_docs.is_user_data_path(str(base / rel)))
            # phpstudy 的运行时目录反而该被认成软件目录
            self.assertTrue(scan_docs.is_known_software_dir(
                str(base / 'phpstudy_pro' / 'Extensions')))

    def test_media_app_dirs(self):
        """音乐平台目录：网易云 / QQ音乐 / 酷狗 / 酷我 / Spotify / Apple Music。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            for rel in ('CloudMusic', 'QQMusic', 'Kugou', 'Kuwo', 'Spotify',
                        'Apple Music', '网易云音乐'):
                p = base / rel / 'Music'
                p.mkdir(parents=True, exist_ok=True)
                self.assertTrue(scan_docs.is_media_app_path(str(p)),
                                '%s 应是音乐平台目录' % rel)

    def test_media_app_needs_whole_segment(self):
        """整段相等才算命中：MyCloudMusic 这种撞名不能误伤。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            p = base / 'MyCloudMusic'
            p.mkdir()
            self.assertFalse(scan_docs.is_media_app_path(str(p)))


class TestNoIndexMarker(unittest.TestCase):
    """标记文件机制（竞品同源：.recoll-noindex / .trackerignore）。"""

    def test_marker_detected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            d = base / 'MyNoise'
            d.mkdir()
            self.assertFalse(scan_docs.has_noindex_marker(str(d)))
            _touch(base, 'MyNoise/.memex-noindex')
            self.assertTrue(scan_docs.has_noindex_marker(str(d)))

    def test_marker_makes_files_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _touch(base, 'MyNoise/.memex-noindex')
            f = _touch(base, 'MyNoise/a.png')
            self.assertTrue(brain._noindex_marked(str(f)))

    def test_marker_is_not_a_noindex_dir_itself(self):
        """标记文件本身不该被当成目录标记（它在目录里，标记的是那棵树）。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _touch(base, 'MyNoise/.memex-noindex')
            self.assertFalse(scan_docs.has_noindex_marker(str(base / 'MyNoise/.memex-noindex')))


class TestWiring(unittest.TestCase):
    """接线形态：代码在 ≠ 真的被调用。"""

    def test_soft_dir_file_uses_known_software_dir(self):
        src = (ROOT / 'brain.py').read_text(encoding='utf-8')
        self.assertIn('scan_docs.is_known_software_dir(d0)', src)

    def test_full_scan_uses_known_software_dir(self):
        src = (ROOT / 'brain.py').read_text(encoding='utf-8')
        self.assertIn('scan_docs.is_known_software_dir(cur)', src)

    def test_scope_reconcile_uses_noindex(self):
        src = (ROOT / 'brain.py').read_text(encoding='utf-8')
        self.assertIn('if _noindex_marked(p):', src)

    def test_asset_rule_still_present(self):
        """第 242 项的资产目录规则不能被这一项挤掉。"""
        src = (ROOT / 'brain.py').read_text(encoding='utf-8')
        self.assertIn('scan_docs.is_asset_path(full)', src)
        self.assertIn('scan_docs.is_asset_path(path)', src)


if __name__ == '__main__':
    unittest.main()
