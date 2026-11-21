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


class TestEnvMsgs(unittest.TestCase):
    """体检文案的西/葡/印地语表（第 176 项）：键与占位符必须与英文表一一对应。

    这些串走 % 格式化：少一个 %d 就是 TypeError，体检面板整块空白，
    而「键齐了」什么都说明不了。
    """

    def test_tables_cover_three_langs(self):
        import env_msgs
        self.assertEqual({'es', 'pt', 'hi'}, set(env_msgs.TABLES))

    def test_keys_match_english(self):
        import env_msgs
        for name, tb in env_msgs.TABLES.items():
            self.assertEqual(set(ec._EN), set(tb), name)

    def test_placeholders_match_english(self):
        import re
        import env_msgs
        def sig(v):
            return re.findall(r'%[sd]|%\.\d+f', v)
        for name, tb in env_msgs.TABLES.items():
            for k, v in tb.items():
                self.assertEqual(sig(ec._EN[k]), sig(v), '%s.%s' % (name, k))

    def test_table_picks_language(self):
        self.assertEqual({}, ec._table('zh'))          # 中文用调用处默认值
        self.assertEqual(ec._EN, ec._table('en'))
        self.assertEqual(ec._EN, ec._table('fr'))      # 没翻的语言退回英文
        self.assertEqual('Runtime de WebView2 %s', ec._table('es')['wv_ok_t'])
        self.assertEqual('Runtime do WebView2 %s', ec._table('pt')['wv_ok_t'])

    def test_judge_is_localized(self):
        import re
        info = mk(webview2='')
        hi = ec.judge(info, 'hi')
        self.assertTrue(re.search(r'[\u0900-\u097F]', hi[0]['title']), hi[0]['title'])
        es = ec.judge(info, 'es')
        self.assertIn('Falta', es[0]['title'])
        # 中文仍然是默认，不能因为加了表就把中文顶掉
        self.assertIn('WebView2', ec.judge(info)[0]['title'])
        self.assertIn('缺少', ec.judge(info)[0]['title'])


class TestProbeDepth(unittest.TestCase):
    """第 281 项：把 12 项功能体检里「只有壳」的几项钉死。

    踩过的坑：资源探测不声明 restype，64 位下句柄被压成 32 位 → GDI/User 恒为 0，
    这条判断等于关着；更新通道只看用户从没配过的 update_sources.json → urls 恒为 0，
    等于没有；免费版没 license 却天天报一条 WARN。这些都是「绿着但没用」。
    """

    def test_license_free_is_info_not_warn(self):
        l = lv(ec.judge(mk(license={'state': 'none', 'edition': 'free'})))
        self.assertEqual('INFO', l.get('license'))

    def test_license_real_failure_still_warns(self):
        l = lv(ec.judge(mk(license={'state': 'machine_mismatch'})))
        self.assertEqual('WARN', l.get('license'))

    def test_update_builtin_channel_decides(self):
        ok = lv(ec.judge(mk(update={'urls': 0, 'builtin': True, 'cur': 'v0.9.55', 'latest': 'v0.9.56'})))
        self.assertEqual('OK', ok.get('update'))
        bad = lv(ec.judge(mk(update={'urls': 0, 'builtin': False, 'errors': ['timeout']})))
        self.assertEqual('WARN', bad.get('update'))

    def test_resources_line_always_shown_with_memory(self):
        it = [i for i in ec.judge(mk(resources={'threads': 9, 'handles': 120,
                                                'gdi': 15, 'user': 3,
                                                'mem_mb': 118, 'peak_mb': 310}))
              if i['key'] == 'res']
        self.assertTrue(it, '资源这一行不能在没有 GDI 时整条消失')
        self.assertIn('118', it[0]['title'])
        self.assertIn('310', it[0]['title'])

    def test_index_body_mismatch_warns(self):
        l = lv(ec.judge(mk(index_health={'docs': 1000, 'files': 9000, 'fts': 300, 'ok': True,
                                        'wal_mb': 0.1, 'free_pct': 2})))
        self.assertEqual('WARN', l.get('idx_fts'))

    def test_libsets_problem_warns(self):
        l = lv(ec.judge(mk(libsets={'n': 1, 'active': 'default', 'writable': False,
                                    'sets': [{'id': 'default', 'db': True}]})))
        self.assertEqual('WARN', l.get('libsets'))

    def test_dir_writable_really_writes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(ec._dir_writable(d))
            self.assertFalse(ec._dir_writable(Path(d) / 'nope'))

    def test_probe_libsets_reports_writable_and_sync(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            got = ec._probe_libsets(d)
            self.assertIn('writable', got)
            self.assertIn('sync', got)
            self.assertTrue(got['writable'])

    def test_every_m_key_exists_in_english(self):
        """M('key') 用到的键必须真的在 _EN 里 —— 缺一个就静默回落中文，
        英语/西语界面里那一行突然出现中文（用户截图里就是这样被抓到的）。"""
        import re
        from pathlib import Path
        src = Path(ec.__file__).read_text(encoding='utf-8')
        used = set(re.findall(r"M\('([a-z0-9_]+)'", src))
        self.assertTrue(used)
        self.assertEqual(sorted(used - set(ec._EN)), [])

    def _make_db(self, d, n=50):
        import sqlite3
        p = Path(d) / 'brain.db'
        con = sqlite3.connect(str(p))
        con.execute('CREATE TABLE docs (rowid_ INTEGER PRIMARY KEY, body TEXT)')
        for i in range(n):
            con.execute('INSERT INTO docs (body) VALUES (?)', ('x' * 20,))
        con.commit()
        con.close()
        return p

    def test_header_check_is_cheap_and_correct(self):
        """深度体检只做 header 检查（quick_check 2.4GB 要 28–39s，塞不进按钮）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._make_db(d)
            st, size = ec._db_header_check(str(p))
            self.assertEqual('ok', st)
            self.assertEqual(p.stat().st_size, size)
            self.assertEqual('missing', ec._db_header_check(str(Path(d) / 'nope.db'))[0])

    def test_full_verify_writes_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._make_db(d)
            res = ec.full_verify(str(p), data_dir=d)
            self.assertEqual('ok', res['state'])
            self.assertTrue(res['ok'])
            got = ec.read_verify_state(d)
            self.assertEqual('ok', got.get('state'))
            self.assertFalse(got.get('stale'))
            self.assertTrue(ec.verify_state_path(d).is_file())


if __name__ == '__main__':
    unittest.main()
