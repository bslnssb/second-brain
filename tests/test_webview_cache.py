# -*- coding: utf-8 -*-
"""体积评估 #5：启动清 WebView2 缓存，但不碰用户态（Cookies / Local Storage）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_window


class WebviewCacheTest(unittest.TestCase):
    def _touch(self, p: Path, data=b'x' * 1024):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def test_caches_removed_and_state_kept(self):
        with tempfile.TemporaryDirectory() as td:
            prof = Path(td)
            base = prof / 'EBWebView'
            self._touch(base / 'Default' / 'Cache' / 'data_0', b'c' * 4096)
            self._touch(base / 'Default' / 'Code Cache' / 'js' / 'index', b'c' * 2048)
            self._touch(base / 'GPUCache' / 'blob', b'c' * 1024)
            self._touch(base / 'GrShaderCache' / 'shader', b'c' * 1024)
            # 用户态：一个都不能删
            self._touch(base / 'Default' / 'Cookies', b'cookie')
            self._touch(base / 'Default' / 'Local Storage' / 'leveldb' / '000003.log',
                        b'lang=zh')

            freed = app_window._clean_webview_cache(prof)

            self.assertGreater(freed, 8000)
            for gone in ('Default/Cache', 'Default/Code Cache', 'GPUCache', 'GrShaderCache'):
                self.assertFalse((base / gone).exists(), gone)
            self.assertTrue((base / 'Default' / 'Cookies').exists())
            self.assertTrue((base / 'Default' / 'Local Storage' / 'leveldb' / '000003.log').exists())

    def test_webview2_component_dirs_removed(self):
        """5′：WebView2 自下载的组件（Widevine / crx / 广告规则）也算可再生。

        这条和上面那条的区别：名字带空格（'Subresource Filter'）必须整段匹配，
        否则会误删别的目录；省的是 ≈55 MB，是本轮体积收尾的主要来源。"""
        with tempfile.TemporaryDirectory() as td:
            prof = Path(td)
            base = prof / 'EBWebView'
            self._touch(base / 'component_crx_cache' / 'x.crx', b'c' * 1024)
            self._touch(base / 'WidevineCdm' / '2.0' / 'widevinecdm.dll', b'w' * 1024)
            self._touch(base / 'Subresource Filter' / 'Indexed Rules' / '9.0' / 'rules', b'r' * 1024)
            self._touch(base / 'Default' / 'Cache' / 'data_0', b'c' * 1024)

            freed = app_window._clean_webview_cache(prof)

            self.assertGreater(freed, 3000)
            for gone in ('component_crx_cache', 'WidevineCdm', 'Subresource Filter',
                         'Default/Cache'):
                self.assertFalse((base / gone).exists(), gone)

    def test_component_update_off_sets_env_once(self):
        saved = os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS')
        try:
            os.environ.pop('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', None)
            app_window._component_update_off()
            v1 = os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']
            self.assertIn('--disable-component-update', v1)
            app_window._component_update_off()          # 第二次不能重复追加
            v2 = os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']
            self.assertEqual(v1.count('--disable-component-update'), 1)
            self.assertEqual(v1, v2)
        finally:
            if saved is None:
                os.environ.pop('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', None)
            else:
                os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = saved

    def test_missing_profile_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(app_window._clean_webview_cache(Path(td) / 'nope'), 0)

    def test_undeletable_cache_does_not_raise(self):
        """Windows 上被占用的文件删不掉时只能放弃，不能抛出去把启动搞挂。"""
        with tempfile.TemporaryDirectory() as td:
            prof = Path(td)
            f = prof / 'EBWebView' / 'Default' / 'Cache' / 'locked'
            self._touch(f, b'z' * 10)
            try:
                os.chmod(f, 0o444)
            except OSError:
                pass
            app_window._clean_webview_cache(prof)  # 不抛就是过


if __name__ == '__main__':
    unittest.main()
