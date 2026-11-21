# -*- coding: utf-8 -*-
"""软件资产目录排除（第 240 → 242 项）的回归测试。

**第 242 项把判据整个换掉了** —— 起因是用户三条实测反馈：
  ① 2GB 体积上限不该有：2GB 以上的常见就是压缩包和系统镜像，那正是
     「按名字找得到」的东西（Everything 的文件名索引同样没有 size 上限，
     它的 size 上限只管**内容索引**）。
  ② 该排的是几 KB 的应用图标（.ico），不是大文件。
  ③ **D:\\phpstudy_pro\\WWW 是渗透测试靶场**，绝不能被当成软件目录排掉。

旧判据（按子树文件构成比例）实测不可行，用真实数据验证过：
  · 「程序物占比」：钉钉 current 69.0%、腾讯会议 66.9%、靶场 66.5%
    —— 区分度不到 3 个百分点；课件包 95.7% 反而会被误判。
  · 「源码占比」：格式工厂 40.7%（软件自带 .py）、PotPlayer 20%（.pyd 绑定）
    被当成用户源码而放过；靶场 32% 又显得像用户项目。
  · 「项目标志文件」（社区通用的找项目根目录法）：靶场里的靶机是**散装拷贝**的，
    没有 package.json 也没有 .git —— 靠标志文件反而会误伤。
文件层面根本区分不了「用户存的 .mp3」和「软件自带的 .mp3」。

新判据（Everything 的模型：**不猜，只排明确的**）：
  ① 路径里有**程序资产目录名**（resources / locales / runtime / webcontent…）；
  ② 该段的**父目录里有 .exe** —— 这是「这是安装目录」的确证，也是安全阀：
     用户的项目目录就算有 resources/runtime，父目录没有 exe 就不排；
  ③ 靶场目录名无条件保护。

跑法：py -3.12 -m unittest tests.test_software_dir_sniff -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scan_docs  # noqa: E402


def _mk(base: Path, sub: str, files: dict) -> Path:
    d = base / sub
    d.mkdir(parents=True, exist_ok=True)
    for name, n in files.items():
        for i in range(n):
            (d / ('%s_%d%s' % (name.strip('.'), i, name[name.rfind('.'):]))).write_text('x', encoding='utf-8')
    return d


class TestAssetDirRule(unittest.TestCase):
    """新判据：资产目录名 + 父目录有 exe。"""

    def setUp(self):
        scan_docs._softdir_cache.clear()

    def test_asset_dir_under_install_root_is_excluded(self):
        """<App>\\Resources\\ 里的一堆素材要排掉（父目录有 exe = 安装目录）。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _mk(base, 'App', {'.exe': 1})
            res = base / 'App' / 'Resources'
            _mk(res, '.', {'.png': 100, '.js': 40})
            self.assertTrue(scan_docs.is_asset_path(str(res)),
                            '安装目录下的 Resources 是程序资产，应排掉')

    def test_asset_dir_without_exe_parent_is_kept(self):
        """没有 exe 的父目录 → 不是安装目录 → 不排（用户的项目里也可能有 resources）。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            proj = base / 'myproj'
            res = proj / 'resources'
            _mk(res, '.', {'.png': 30, '.json': 20})
            self.assertFalse(scan_docs.is_asset_path(str(res)),
                             '没有 exe 的父目录不是安装目录，不能排')

    def test_pentest_lab_is_always_kept(self):
        """用户的渗透靶场（D:\\phpstudy_pro\\WWW）无条件保护 —— 用户明说过的。"""
        for seg in ('www', 'wwwroot', 'htdocs', 'dvwa', 'sqli-labs', 'pikachu'):
            with tempfile.TemporaryDirectory() as td:
                base = Path(td)
                lab = base / seg
                _mk(lab, '.', {'.php': 40, '.html': 10})
                self.assertFalse(scan_docs.is_asset_path(str(lab)),
                                 '靶场目录名 %r 必须无条件保护' % seg)

    def test_asset_dir_inside_lab_is_kept(self):
        """靶场里就算有 resources/runtime 也不能排 —— 保护是整条路径生效的。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            lab = base / 'www'
            (lab).mkdir(parents=True, exist_ok=True)
            _mk(lab, '.', {'.exe': 1})
            inner = lab / 'resources'
            _mk(inner, '.', {'.png': 20})
            self.assertFalse(scan_docs.is_asset_path(str(inner)),
                             '靶场内部的 assets 目录也不能排')

    def test_www_not_in_asset_seg_list(self):
        """'www' 绝不能进资产目录名表 —— 会误伤用户的靶场。"""
        self.assertNotIn('www', scan_docs.SOFTDIR_ASSET_SEGS)
        for risky in ('www', 'lib', 'bin', 'assets', 'plugins', 'modules', 'lib64'):
            self.assertNotIn(risky, scan_docs.SOFTDIR_ASSET_SEGS,
                             '%r 在用户项目/靶场里太常见，不能当程序资产' % risky)


class TestSizeLimitRemoved(unittest.TestCase):
    """第 242 项：文件名索引不再有体积上限（Everything 的文件名索引同样不限）。"""

    def test_names_size_limit_is_disabled(self):
        import brain
        self.assertEqual(0, brain.NAMES_SIZE_LIMIT,
                         '文件名索引不该有体积上限：2GB 以上的压缩包/镜像正是要按名字找的')

    def test_body_size_limit_still_exists(self):
        """正文那条上限要留着 —— 那是真读内容的清单，读 40GB 的 ISO 代价太大。"""
        self.assertGreater(scan_docs.SIZE_LIMIT, 0)
        self.assertLessEqual(scan_docs.SIZE_LIMIT, 200 * 1024 * 1024)


class TestSmallIconRule(unittest.TestCase):
    """第 242 项：几 KB 的应用图标不入索引（大图标保留）。"""

    def test_icon_exts_and_threshold(self):
        import brain
        self.assertIn('.ico', brain.NAMES_ICON_EXTS)
        self.assertGreater(brain.NAMES_ICON_MAX_SIZE, 0)
        self.assertLessEqual(brain.NAMES_ICON_MAX_SIZE, 256 * 1024,
                             '阈值太大会把用户的图标素材也排掉')


class TestSwitchCanDisable(unittest.TestCase):
    def test_switch_can_disable(self):
        old = scan_docs.SOFTWARE_DIR_SNIFF
        scan_docs.SOFTWARE_DIR_SNIFF = False
        try:
            with tempfile.TemporaryDirectory() as td:
                base = Path(td)
                _mk(base, 'App', {'.exe': 2, '.png': 120, '.js': 40})
                self.assertFalse(scan_docs.looks_like_software_dir(str(base / 'App')),
                                 '开关关掉时必须完全不生效')
        finally:
            scan_docs.SOFTWARE_DIR_SNIFF = old


if __name__ == '__main__':
    unittest.main()
