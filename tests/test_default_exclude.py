# -*- coding: utf-8 -*-
"""第 236 项「内置默认排除」的回归测试。

背景：库里 303,808 条文件名、12,754 条正文，实测 57% 是用户永远不会搜的东西：
  · AppData 96,031 条（微软 Windows Search 官方默认就排除整棵 AppData）
  · Program Files 下的资源文件 25,474 条（png/js/dll，用户只会搜 exe）
  · 正文池里 9,979 条是插件文档 / 前端 chunk（飞书 webcontent 3,009、豆包 2,535…）

这些规则一旦被改坏，症状是「搜索里又冒出几千张图标」或「搜微信搜不到应用了」，
都得跑一次全盘重建才发现。所以在纯函数层钉死。

跑法：py -3.12 -m unittest tests.test_default_exclude -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402
import scan_docs  # noqa: E402


class DefaultExcludeTest(unittest.TestCase):

    def setUp(self):
        self.exckw = brain._scope_excludes()

    def test_appdata_is_excluded(self):
        for p in (r'C:\Users\a\AppData\Local\Google\Chrome\User Data\Default\Cache\data_1',
                  r'C:\Users\a\AppData\Roaming\kingsoft\office6\backup\x.docx',
                  r'C:\Users\a\AppData\LocalLow\Sun\Java\deployment\log'):
            self.assertTrue(brain._excluded(p, self.exckw), p)

    def test_programdata_and_recycle_bin(self):
        for p in (r'C:\ProgramData\Intel\Package Cache\x.log',
                  r'D:\$Recycle.Bin\S-1-5-21\$R123.txt'):
            self.assertTrue(brain._excluded(p, self.exckw), p)

    def test_start_menu_survives(self):
        """开始菜单必须在 AppData 里活下来 —— 「搜微信出应用」全靠它。"""
        for p in (r'C:\Users\a\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\微信.lnk',
                  r'C:\ProgramData\Microsoft\Windows\Start Menu\Programs\腾讯会议.lnk'):
            self.assertFalse(brain._excluded(p, self.exckw), p)

    def test_user_docs_not_excluded(self):
        for p in (r'D:\资料\项目\实施工具\云主机安装手册.pdf',
                  r'C:\Users\a\Documents\劳动合同.docx',
                  r'J:\阿凡达：水之道 (2022) 4K HDR\Avatar.mkv'):
            self.assertFalse(brain._excluded(p, self.exckw), p)

    def test_lookalike_dir_not_excluded(self):
        """子串匹配必须带目录段，别把「我的appdata备份」这种资料目录误杀。"""
        self.assertFalse(brain._excluded(r'D:\我的appdata备份\笔记.txt', self.exckw))

    def test_install_root_only_keeps_apps(self):
        exe = r'C:\Program Files\Tencent\WeMeet\wemeetapp.exe'
        png = r'C:\Program Files\Tencent\WeMeet\res\logo.png'
        js = r'C:\Users\a\AppData\Local\Programs\Microsoft VS Code\resources\app\out\main.js'
        for p in (exe, png, js):
            self.assertTrue(scan_docs.is_install_root(p), p)
        self.assertIn('.exe', scan_docs.APP_ONLY_EXTS)
        self.assertNotIn('.png', scan_docs.APP_ONLY_EXTS)
        self.assertNotIn('.js', scan_docs.APP_ONLY_EXTS)

    def test_normal_dir_is_not_install_root(self):
        for p in (r'D:\资料\项目\实施工具\a.pdf', r'J:\电影\a.mkv'):
            self.assertFalse(scan_docs.is_install_root(p), p)

    def test_body_skip_hits_webcontent(self):
        """正文池的污染源：飞书 / 豆包 / 剪映 / Git 文档。"""
        kws = brain._BODY_SKIP_SEGS_NC
        for p in (r'D:\Feishu\app\webcontent\bridge.js',
                  r'D:\Doubao\app\local_webcontents\main.js',
                  r'D:\Git\mingw64\share\doc\git\README.md',
                  r'D:\App\locales\zh-CN.pak'):
            lp = p.lower()
            self.assertTrue(any(s in lp for s in kws), p)
        self.assertFalse(any(s in r'd:\资料\项目\实施工具\说明.txt'.lower() for s in kws))

    def test_never_index_includes(self):
        """include_dirs 里写着 AppData 也不能当扫描根 —— 那 96,031 条就是这么来的。"""
        for p in (r'C:\Users\a\AppData', r'C:\Users\a\AppData\Local\Temp',
                  r'C:\ProgramData', r'C:\$Recycle.Bin'):
            self.assertTrue(scan_docs.never_index(p), p)
        for p in (r'D:\资料\项目', r'C:\Users\a\Desktop', r'C:\Program Files\Tencent',
                  r'J:\阿凡达 (2022)'):
            self.assertFalse(scan_docs.never_index(p), p)

    def test_skip_names_are_high_confidence(self):
        """通用名（build/dist/temp/cache/target）故意不收 —— 用户可能拿来存资料。"""
        for name in ('build', 'dist', 'temp', 'cache', 'target', 'out', 'logs'):
            self.assertNotIn(name, scan_docs.DEFAULT_SKIP_NAMES, name)
        for name in ('node_modules', '__pycache__', '.git', 'steamapps', 'gpucache'):
            self.assertIn(name, scan_docs.DEFAULT_SKIP_NAMES, name)


if __name__ == '__main__':
    unittest.main()
