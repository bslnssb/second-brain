"""错误日志上报 / 设置备份的单元测试：纯临时目录，不碰真实数据、不联网。"""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import error_report as E  # noqa: E402
import settings_backup as B  # noqa: E402


class ErrorReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        E.configure(self.d, 'v0.9.55-test')

    def tearDown(self):
        self.tmp.cleanup()

    def ingest(self, url='https://example.com/r', enabled=True):
        """模拟开发者把 error_ingest.json 放进数据目录。"""
        (self.d / E.INGEST_NAME).write_text(
            json.dumps({'enabled': enabled, 'url': url}), encoding='utf-8')

    def test_01_defaults_all_on_and_write_nothing(self):
        # 用户 2026-11-13 06:1x 要求：取消两个开关，auto / include_paths 一律恒开。
        # 配置本身仍然默认不落盘；auto 只在端点存在时才产生网络行为。
        c = E.load_conf()
        self.assertTrue(c['collect'])
        self.assertTrue(c['auto'], '自动发送恒开（第 162 项）')
        self.assertTrue(c['include_paths'], '保留路径恒开（第 162 项）')
        self.assertNotIn('url', c, '用户配置里不该再有端点了')
        self.assertEqual(E.endpoint(), '', '没放作者侧配置就不该有端点')
        self.assertFalse((self.d / E.CONF_NAME).exists(), '默认不能落任何配置文件')

    def test_01d_legacy_off_values_are_overridden(self):
        # 升级前存过 auto=False / include_paths=False 的配置不能再生效，
        # 否则界面已经没有开关，用户永远关不回来。
        (self.d / E.CONF_NAME).write_text(
            json.dumps({'collect': False, 'auto': False, 'include_paths': False}),
            encoding='utf-8')
        c = E.load_conf()
        self.assertFalse(c['collect'], 'collect 仍尊重用户选择')
        self.assertTrue(c['auto'])
        self.assertTrue(c['include_paths'])
        # 写回时不再把恒开项落盘，避免文件与行为看起来不一致
        E.save_conf({'collect': True})
        raw = json.loads((self.d / E.CONF_NAME).read_text(encoding='utf-8'))
        self.assertNotIn('auto', raw)
        self.assertNotIn('include_paths', raw)

    def test_01b_collect_off_skips_saving(self):
        E.save_conf({'collect': False})
        E._on_error('unit', RuntimeError, RuntimeError('x'), None)
        self.assertEqual(E.list_reports(), [], '关掉收集就不该落盘')

    def test_01c_partial_save_conf_keeps_other_switches(self):
        # 回归：save_conf 曾用 d.get(k) 覆盖全部键，只改一个键会把 collect
        # 写成 None -> bool(None)=False，本机记录被静默关掉。
        E.save_conf({'collect': True, 'sent': 7})
        E.save_conf({'collect': True})
        c = E.load_conf()
        self.assertTrue(c['collect'], '只改 collect 不该关掉本机记录')
        self.assertTrue(c['auto'], 'auto 恒开')
        self.assertTrue(c['include_paths'], '保留路径恒开')
        self.assertEqual(c['sent'], 7, '不该把计数清零')
        self.assertNotIn('url', c)

    def test_06b_build_includes_system_info(self):
        rep = E.build('unit', RuntimeError, RuntimeError('x'), None)
        self.assertIn('cpu', rep['env'])
        self.assertIn('mem_total_mb', rep['env'])
        self.assertIn('硬件', E.markdown(rep))

    def test_02_url_whitelist(self):
        self.assertTrue(E.valid_url('https://example.com/report'))
        self.assertTrue(E.valid_url('http://127.0.0.1:9999/hook'))
        self.assertTrue(E.valid_url('http://localhost/hook'))
        for bad in ('', 'ftp://x/y', 'http://example.com/r',
                    'https://user:pw@example.com/r', 'https://' + 'a' * 600,
                    'javascript:alert(1)', 'https://example.com/r' + '?x' * 300):
            self.assertFalse(E.valid_url(bad), bad[:40])

    def test_03_mask_url_hides_query(self):
        self.assertEqual(E.mask_url(''), '')
        m = E.mask_url('https://hooks.example.com/path/token-abc?key=SECRET')
        self.assertNotIn('SECRET', m)
        self.assertIn('***', m)

    def test_04_scrub_removes_paths_license_and_emails(self):
        raw = (r"File \"D:\1\ai工作\第二大脑\开发测试版\brain.py\", line 100, in f"
               "\n code=SB1.abcdefghijklmnopqrstuvwxyz123456"
               "\n mail=me@example.com https://x/y?token=SECRET")
        out = E.scrub(raw)
        self.assertNotIn('第二大脑', out)
        self.assertNotIn('SB1.', out)
        self.assertNotIn('me@example.com', out)
        self.assertNotIn('token=SECRET', out)
        self.assertIn('<PATH>/brain.py', out)
        self.assertIn('<LICENSE_CODE>', out)

    def test_05_scrub_include_paths_keeps_structure_but_hides_user(self):
        out = E.scrub(r'C:\Users\zhang\Documents\x.txt', include_paths=True)
        self.assertIn('<USER>', out)
        self.assertNotIn('zhang', out)

    def test_06_build_and_markdown_are_readable(self):
        try:
            raise RuntimeError('boom')
        except RuntimeError:
            rep = E.build('unit', *sys.exc_info())
        self.assertEqual(rep['kind'], 'RuntimeError')
        self.assertIn('boom', rep['message'])
        self.assertEqual(rep['version'], 'v0.9.55-test')
        md = E.markdown(rep)
        self.assertIn('第二大脑 · 错误报告', md)
        self.assertIn('RuntimeError', md)

    def test_07_save_list_get_clear_and_cap(self):
        for i in range(E.MAX_KEEP + 6):
            E.save_report({'v': 1, 'at': str(i), 'kind': 'X', 'where': 'u'})
        items = E.list_reports()
        self.assertEqual(len(items), E.MAX_KEEP)
        first = items[0]['id']
        self.assertIsNotNone(E.get_report(first))
        self.assertIsNone(E.get_report('../settings.json'), 'id 必须防路径穿越')
        self.assertEqual(E.clear_reports(), E.MAX_KEEP)
        self.assertEqual(E.list_reports(), [])

    def test_08_export_report_writes_markdown(self):
        E.save_report({'v': 1, 'at': 'x', 'kind': 'ValueError', 'message': 'm',
                       'trace': 't', 'env': {}, 'note': 'n'})
        p = E.export_report()
        self.assertIsNotNone(p)
        self.assertTrue(p.read_text(encoding='utf-8').startswith('# 第二大脑'))

    def test_09_save_conf_and_status_hides_endpoint(self):
        self.ingest('https://example.com/r?k=SECRET')
        E.save_conf({'auto': True, 'include_paths': True, 'sent': 3})
        c = E.load_conf()
        self.assertTrue(c['auto'])
        self.assertEqual(c['sent'], 3)
        st = E.status()
        self.assertTrue(st['conf']['ingest'])
        self.assertNotIn('url', st['conf'])
        self.assertNotIn('SECRET', json.dumps(st), '地址一个字节都不能回传前端')
        self.assertNotIn('example.com', json.dumps(st))

    def test_10_send_one_success_deletes_local_copy(self):
        rid = E.save_report({'v': 1, 'at': 'x', 'kind': 'X'})
        self.ingest()
        with mock.patch.object(E, '_post', return_value=True) as m:
            ok, err = E.send_one(rid)
        self.assertTrue(ok, err)
        self.assertTrue(m.called)
        self.assertIsNone(E.get_report(rid), '发送成功要删本地副本')
        self.assertEqual(E.load_conf()['sent'], 1)

    def test_11_send_one_failure_keeps_copy(self):
        rid = E.save_report({'v': 1, 'at': 'x', 'kind': 'X'})
        self.ingest()
        with mock.patch.object(E, '_post', side_effect=OSError('offline')):
            ok, err = E.send_one(rid)
        self.assertFalse(ok)
        self.assertIn('offline', err)
        self.assertIsNotNone(E.get_report(rid), '发送失败要保留')
        self.assertIn('OSError', E.load_conf()['last_error'])

    def test_12_reject_invalid_endpoint_without_network(self):
        rid = E.save_report({'v': 1, 'at': 'x', 'kind': 'X'})
        self.ingest('http://evil.example.com/r')
        with mock.patch.object(E, '_post') as m:
            ok, err = E.send_one(rid)
        self.assertFalse(ok)
        self.assertFalse(m.called)

    def test_13_send_pending_caps_batch(self):
        for _ in range(E.MAX_BATCH + 4):
            E.save_report({'v': 1, 'at': 'x', 'kind': 'X'})
        self.ingest()
        with mock.patch.object(E, '_post', return_value=True) as m:
            r = E.send_pending()
        self.assertEqual(r['sent'], E.MAX_BATCH)
        self.assertEqual(m.call_count, E.MAX_BATCH)

    def test_14_on_error_without_endpoint_never_hooks_network(self):
        # auto 恒开，但没端点时 _on_error 不应起发送线程（否则每次报错都空转）
        with mock.patch.object(E, '_bg_send') as m:
            E._on_error('unit', RuntimeError, RuntimeError('x'), None)
        self.assertFalse(m.called)
        self.assertEqual(len(E.list_reports()), 1)

    def test_14b_legacy_url_in_user_conf_is_ignored(self):
        # 老版本可能在 error_report.json 里留过 url；升级后绝不能再拿它发报告
        (self.d / E.CONF_NAME).write_text(
            json.dumps({'collect': True, 'auto': True, 'url': 'https://evil.example.com/r'}),
            encoding='utf-8')
        self.assertNotIn('url', E.load_conf())
        self.assertEqual(E.endpoint(), '')
        with mock.patch.object(E, '_post') as m:
            ok, err = E.send_one('nothing')
        self.assertFalse(ok)
        self.assertFalse(m.called)
        self.assertIn('开发者', err)

    def test_14c_ingest_disabled_invalid_and_missing(self):
        self.assertEqual(E.endpoint(), '')
        self.ingest(enabled=False)
        self.assertEqual(E.endpoint(), '')
        self.ingest('ftp://x/y')
        self.assertEqual(E.endpoint(), '')
        self.ingest()
        self.assertTrue(E.endpoint())

    def test_14d_auto_on_without_endpoint_does_not_send(self):
        E.save_conf({'collect': True})
        with mock.patch.object(E, '_bg_send') as m:
            E._on_error('unit', RuntimeError, RuntimeError('x'), None)
        self.assertFalse(m.called)
        self.assertEqual(len(E.list_reports()), 1)

    def test_14e_ingest_config_tolerates_bom(self):
        # 记事本 / PowerShell 写 UTF-8 默认带 BOM；旧版用 utf-8 读会抛错被吞，
        # 端点静默失效。回归：带 BOM 与不带 BOM 都要能读到。
        body = json.dumps({'enabled': True, 'url': 'https://example.com/r'})
        (self.d / E.INGEST_NAME).write_text('\ufeff' + body, encoding='utf-8')
        self.assertTrue(E.endpoint())
        (self.d / E.INGEST_NAME).write_text(body, encoding='utf-8')
        self.assertTrue(E.endpoint())


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for k, v in entries.items():
            z.writestr(k, v)
    return buf.getvalue()


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / 'settings.json').write_text('{"theme":"dark"}', encoding='utf-8')
        (self.d / 'scope.json').write_text('{"include_dirs":["D:/docs"]}', encoding='utf-8')
        (self.d / 'license.json').write_text('{"code":"SB1.SECRET"}', encoding='utf-8')
        (self.d / 'brain.db').write_bytes(b'x' * 1024)

    def tearDown(self):
        self.tmp.cleanup()

    def test_15_make_backup_excludes_private_and_db(self):
        (self.d / 'error_ingest.json').write_text(
            json.dumps({'url': 'https://x/r?token=SECRET'}), encoding='utf-8')
        name, raw = B.make_backup(self.d, 'v0.9.55')
        self.assertTrue(name.endswith('.zip'))
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
        self.assertIn('settings.json', names)
        self.assertIn(B.MANIFEST, names)
        self.assertNotIn('license.json', names)
        self.assertNotIn('brain.db', names)
        self.assertNotIn('error_ingest.json', names, '接收端凭据绝不能进备份')
        self.assertNotIn('SECRET', raw.decode('utf-8', errors='ignore'))

    def test_16_restore_roundtrip_and_pre_backup(self):
        _, raw = B.make_backup(self.d, 'v0.9.55')
        (self.d / 'settings.json').write_text('{"theme":"light"}', encoding='utf-8')
        r = B.restore(self.d, raw)
        self.assertTrue(r['ok'], r)
        self.assertEqual(json.loads((self.d / 'settings.json').read_text(encoding='utf-8'))['theme'], 'dark')
        bak = Path(r['backup_dir'])
        self.assertTrue(bak.is_dir())
        self.assertIn('{"theme":"light"}', (bak / 'settings.json').read_text(encoding='utf-8'))

    def test_17_reject_zip_slip_and_unknown_member(self):
        for entries in (
            {'../evil.json': '{}'},
            {'settings.json': '{}', 'license.json': '{}'},
            {'settings.json': '{}', 'brain.db': 'x' * 100},
        ):
            with self.assertRaises(ValueError):
                B.inspect(_zip(entries))

    def test_18_reject_bad_json_and_oversize(self):
        with self.assertRaises(ValueError):
            B.inspect(_zip({'settings.json': 'not json'}))
        with self.assertRaises(ValueError):
            B.inspect(b'')

    def test_19_restore_failure_is_structured(self):
        r = B.restore(self.d, _zip({'settings.json': 'not json'}))
        self.assertFalse(r['ok'])
        self.assertIn('JSON', r['err'])

    def test_20_no_files_still_produces_manifest_only_backup(self):
        empty = Path(self.tmp.name) / 'empty'
        empty.mkdir()
        name, raw = B.make_backup(empty, 'v0.9.55')
        self.assertTrue(name.endswith('.zip'))
        man, members = B.inspect(raw)
        self.assertEqual(man['files'], [])
        r = B.restore(empty, raw)
        self.assertFalse(r['ok'], '空备份不应误判成功')


if __name__ == '__main__':
    unittest.main()


class FeedbackTest(unittest.TestCase):
    """第 161 项：界面不对但没抛异常时，用户主动写一句描述也要能生成报告。

    白屏 / 图标模糊 / 点击没反应不会触发 sys.excepthook；没有这条链路，
    「立即发送 / 导出到本地」在 0 份报告时只能干报错。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        E.configure(self.d, 'v0.9.55-test')

    def tearDown(self):
        self.tmp.cleanup()

    def test_21_feedback_creates_report(self):
        rid = E.save_feedback('点托盘图标没反应')
        self.assertIsNotNone(rid)
        items = E.list_reports()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], 'UserFeedback')
        rep = E.get_report(rid)
        self.assertEqual(rep['where'], 'user_feedback')
        self.assertEqual(rep['trace'], '')
        self.assertIn('不是指令', rep['note'])

    def test_22_blank_rejected_and_paths_scrubbed(self):
        self.assertIsNone(E.save_feedback('   '))
        self.assertIsNone(E.save_feedback(''))
        rid = E.save_feedback('窗口白屏 C:\\Users\\bob\\secret.txt')
        rep = E.get_report(rid)
        self.assertNotIn('bob', rep['message'])
        self.assertIn('<PATH>', rep['message'])

    def test_23_feedback_ignores_collect_switch(self):
        # collect 管的是「自动捕获异常」，用户主动写反馈不该被它挡住
        E.save_conf({'collect': False})
        rid = E.save_feedback('手动反馈应该仍然落盘')
        self.assertIsNotNone(rid)
        self.assertEqual(len(E.list_reports()), 1)

    def test_24_feedback_can_be_rendered_and_exported(self):
        rid = E.save_feedback('图标模糊')
        md = E.markdown(E.get_report(rid))
        self.assertIn('UserFeedback', md)
        self.assertIn('图标模糊', md)
        fp = E.export_report(rid)
        self.assertIsNotNone(fp)
        self.assertTrue(Path(fp).is_file())
