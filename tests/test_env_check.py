# -*- coding: utf-8 -*-
"""环境自检的判定与适配（不依赖本机硬件）。

这里测的不是「本机有什么」，而是「给定一份环境数据，结论对不对」——
所以任何一台机器上跑，结果都该一样。环境矩阵（分辨率 × 缩放）用纯算术构造，
不需要真的插一块 4K 屏。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import env_check as ec


def mk(**kw):
    """一份「一切正常」的最小环境，按需覆盖单项。"""
    base = {
        'webview2': '156.0.4314.58',
        'data_dir': 'D:\\SecondBrain', 'data_writable': True,
        'data_ascii': True, 'data_unc': False, 'data_sync': '',
        'free_mb': 5000,
        'primary': {'name': 'DISPLAY1', 'primary': True,
                    'phys_w': 1920, 'phys_h': 1080, 'scale': 100, 'dpr': 1.0,
                    'work_w': 1920, 'work_h': 1040, 'left': 0, 'top': 0,
                    'logical_w': 1920, 'logical_h': 1040, 'logical_full_w': 1920},
        'screens': [],
        'remote_session': False, 'adapter': 'NVIDIA GeForce RTX 3060',
        'db_state': 'ok', 'db_mb': 12.0,
        'free_port': 8765, 'svr_running': False, 'ports': {8765: 'free'},
        'windows': '10.0.22631', 'arch': '64-bit', 'admin': False, 'mem_gb': 16.0,
        'font_yahei': True, 'long_paths': False, 'dark': False,
        'high_contrast': False, 'ts': '2026-11-07 18:00:00',
    }
    base.update(kw)
    return base


def lv(issues):
    return {i['key']: i['level'] for i in issues}


class TestJudgeWebView2(unittest.TestCase):
    def test_missing_is_fatal(self):
        m = lv(ec.judge(mk(webview2='')))
        self.assertEqual(m['webview2'], 'FAIL')

    def test_very_old_warns(self):
        m = lv(ec.judge(mk(webview2='92.0.900.0')))
        self.assertEqual(m['webview2'], 'WARN')

    def test_recent_is_ok(self):
        m = lv(ec.judge(mk(webview2='120.0.1.0')))
        self.assertEqual(m['webview2'], 'OK')

    def test_missing_webview2_gives_download_link(self):
        it = [i for i in ec.judge(mk(webview2='')) if i['key'] == 'webview2'][0]
        self.assertIn('http', it['fix'])


class TestJudgeStorage(unittest.TestCase):
    def test_low_disk_fails(self):
        m = lv(ec.judge(mk(free_mb=120)))
        self.assertEqual(m['disk'], 'FAIL')

    def test_sync_folder_warns(self):
        m = lv(ec.judge(mk(data_dir='C:\\Users\\a\\OneDrive\\SB', data_sync='onedrive')))
        self.assertEqual(m['data_sync'], 'WARN')

    def test_unc_path_warns(self):
        m = lv(ec.judge(mk(data_dir='\\\\nas\\share\\SB', data_unc=True)))
        self.assertEqual(m['data_unc'], 'WARN')

    def test_unwritable_data_dir_warns_when_fallback_works(self):
        m = lv(ec.judge(mk(data_writable=False, fallback_writable=True)))
        self.assertEqual(m['data_dir'], 'WARN')

    def test_unwritable_and_no_fallback_is_fatal(self):
        m = lv(ec.judge(mk(data_writable=False, fallback_writable=False)))
        self.assertEqual(m['data_dir'], 'FAIL')


class TestJudgeDbPort(unittest.TestCase):
    def test_bad_db_fails(self):
        self.assertEqual(lv(ec.judge(mk(db_state='bad')))['db'], 'FAIL')

    def test_missing_db_is_only_info(self):
        self.assertEqual(lv(ec.judge(mk(db_state='missing')))['db'], 'INFO')

    def test_all_ports_busy_fails(self):
        m = lv(ec.judge(mk(free_port=0, ports={p: 'busy' for p in range(8765, 8777)})))
        self.assertEqual(m['port'], 'FAIL')

    def test_running_instance_keeps_port_ok(self):
        m = lv(ec.judge(mk(ports={8765: 'ours'}, svr_running=True)))
        self.assertEqual(m['port'], 'OK')

    def test_other_data_dir_instance_is_only_info(self):
        """多用户会话：另一个用户的实例占着 8765，不该被判成「端口被占」也不能复用。"""
        m = lv(ec.judge(mk(free_port=8766,
                           ports={8765: 'other', 8766: 'free'},
                           other_user_port=8765)))
        self.assertEqual(m['port'], 'OK')
        self.assertEqual(m['port_other'], 'INFO')


class TestJudgeGpu(unittest.TestCase):
    def test_basic_display_warns(self):
        m = lv(ec.judge(mk(adapter='Microsoft Basic Display Adapter')))
        self.assertEqual(m['gpu'], 'WARN')

    def test_remote_session_warns(self):
        m = lv(ec.judge(mk(remote_session=True)))
        self.assertEqual(m['remote'], 'WARN')


class TestPlanIsPureArithmetic(unittest.TestCase):
    """窗口尺寸只由「可用区域」推出来 —— 所以能在任何机器上验证所有屏幕。"""

    def _screen(self, w, h, scale, work_h=None):
        dpr = scale / 100.0
        wh = work_h if work_h is not None else h
        return {'name': 'D', 'primary': True, 'phys_w': w, 'phys_h': h,
                'scale': scale, 'dpr': dpr, 'work_w': w, 'work_h': wh,
                'left': 0, 'top': 0,
                'logical_w': round(w / dpr), 'logical_h': round(wh / dpr),
                'logical_full_w': round(w / dpr)}

    def test_common_screens_fit(self):
        cases = [
            (1366, 768, 100, 728), (1366, 768, 125, 688), (1366, 768, 150, 688),
            (1600, 900, 100, 860), (1920, 1080, 100, 1040), (1920, 1080, 125, 1000),
            (1920, 1080, 150, 1000), (1920, 1080, 200, 1000),
            (2560, 1440, 100, 1400), (2560, 1440, 150, 1400),
            (3840, 2160, 100, 2120), (3840, 2160, 150, 2088), (3840, 2160, 200, 2088),
            (3440, 1440, 100, 1400), (1080, 1920, 100, 1880),
        ]
        for w, h, sc, wh in cases:
            with self.subTest(screen='%dx%d@%d%%' % (w, h, sc)):
                p = ec.plan(mk(primary=self._screen(w, h, sc, wh)))
                lw = round(w / (sc / 100.0))
                lh = round(wh / (sc / 100.0))
                self.assertLessEqual(p['main_w'], lw, '主窗宽超出可用区')
                self.assertLessEqual(p['main_h'], lh, '主窗高超出可用区')
                self.assertGreaterEqual(p['main_w'], 640)
                self.assertGreaterEqual(p['main_h'], 420)
                self.assertLessEqual(p['mini_w'], lw, '快搜窗比屏幕还宽')
                self.assertGreaterEqual(p['mini_x'], 0, '快搜窗跑到屏幕左边')

    def test_mini_keeps_design_width_on_normal_screens(self):
        for w, h, sc in ((1920, 1080, 100), (2560, 1440, 100), (3840, 2160, 150),
                         (1920, 1080, 150), (1920, 1080, 200)):
            p = ec.plan(mk(primary=self._screen(w, h, sc)))
            self.assertEqual(p['mini_w'], 760, '%dx%d@%d%% 不该动设计宽' % (w, h, sc))

    def test_mini_shrinks_on_very_high_scaling(self):
        # 1920×1080 @250% → 逻辑 768；@300% → 逻辑 640。760 会超出屏幕。
        p250 = ec.plan(mk(primary=self._screen(1920, 1080, 250)))
        self.assertLess(p250['mini_w'], 760)
        self.assertLessEqual(p250['mini_w'], 768)
        p300 = ec.plan(mk(primary=self._screen(1920, 1080, 300)))
        self.assertLessEqual(p300['mini_w'], 640)

    def test_disable_gpu_only_when_needed(self):
        self.assertFalse(ec.plan(mk())['disable_gpu'])
        self.assertTrue(ec.plan(mk(adapter='Microsoft Basic Display Adapter'))['disable_gpu'])
        self.assertTrue(ec.plan(mk(remote_session=True))['disable_gpu'])

    def test_plan_survives_garbage(self):
        for bad in ({}, {'primary': None}, {'primary': {'logical_w': 0, 'logical_h': 0}},
                    {'primary': {'logical_w': -5, 'logical_h': -5}}):
            p = ec.plan(bad)
            self.assertGreater(p['main_w'], 0)
            self.assertGreater(p['mini_w'], 0)


class TestRender(unittest.TestCase):
    def test_render_lists_failures(self):
        info = mk(webview2='', free_mb=10)
        text = ec.render(info)
        self.assertIn('WebView2', text)
        self.assertIn('FAIL', text)
        self.assertIn('适配结果', text)

    def test_render_ok_path(self):
        text = ec.render(mk())
        self.assertNotIn('[FAIL]', text)
        self.assertIn('本机环境正常', text)


class TestVersionParse(unittest.TestCase):
    def test_compare_by_number_not_string(self):
        # '99.0' 与 '156.0' 按字符串比会判错（'9' > '1'）
        self.assertGreater(ec._ver_tuple('156.0.4314.58'), ec._ver_tuple('99.0.1.0'))
        self.assertEqual(ec._ver_tuple('garbage'), (0,))


if __name__ == '__main__':
    unittest.main()
