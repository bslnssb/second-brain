# -*- coding: utf-8 -*-
"""settings.json 写入侧的正式单元测试（交接文档第 105 项 P0 的回归锁）。

以前这些断言只活在 tmp/verify_settings.py、tmp/bugcheck/probe2.py 这类一次性脚本里，
跑完就没人再跑。现在每改一次代码、每次打包前都会被 preflight.py 自动重跑。

隔离：直接把 brain._DATA_DIR 指到 tempfile 目录，绝不碰真实的 brain.db /
settings.json；HTTP 用例起在随机端口，不碰用户正在运行的 8765 实例。

跑法：python -m unittest discover -s tests -p "test_*.py" -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402


class _EmptyDb:
    """first_run_need() 只查两个 count(*)，用假连接挡住真实 sqlite。"""

    def execute(self, sql):
        return self

    def fetchone(self):
        return (0,)


class SettingsWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sb-test-settings-')
        self.dir = Path(self.tmp.name)
        self._old_dir = list(brain._DATA_DIR)
        self._old_maint = os.environ.get('SB_NO_MAINT')
        self._old_watch = os.environ.get('SB_NO_WATCH')
        os.environ['SB_NO_MAINT'] = '1'
        os.environ['SB_NO_WATCH'] = '1'
        # 绕过 app_dir() 的目录探测：测试只写这个临时目录
        brain._DATA_DIR[:] = [self.dir]

    def tearDown(self):
        brain._DATA_DIR[:] = self._old_dir
        for key, old in (('SB_NO_MAINT', self._old_maint), ('SB_NO_WATCH', self._old_watch)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self.tmp.cleanup()

    @property
    def sp(self) -> Path:
        return self.dir / 'settings.json'

    def test_concurrent_updates_keep_every_key(self):
        """专家 probe2 复现过的竞态：两个写入互相抹掉 welcome_seen。"""
        def write(i):
            ok, _ = brain._settings_update({'k%d' % i: i})
            self.assertTrue(ok)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        d = json.loads(self.sp.read_text(encoding='utf-8'))
        self.assertIsInstance(d, dict)
        for i in range(40):
            self.assertEqual(d.get('k%d' % i), i)

    def test_non_object_config_is_backed_up_and_recovered(self):
        """非原子写留下的 [] 以前会让 GET/POST 变成 500 HTML。"""
        self.sp.write_text('[]', encoding='utf-8')
        self.assertEqual(brain._settings_read(), {})
        self.assertEqual(len(list(self.dir.glob('settings.json.bad-*'))), 1)
        ok, d = brain._settings_update({'welcome_seen': True})
        self.assertTrue(ok)
        self.assertIs(d.get('welcome_seen'), True)
        self.assertIsInstance(json.loads(self.sp.read_text(encoding='utf-8')), dict)

    def test_truncated_json_reads_as_empty(self):
        """半截 JSON 不许被静默当成「用户没设过偏好」之后还崩掉。"""
        self.sp.write_text('{"welcome_seen": true, "theme": "da', encoding='utf-8')
        self.assertEqual(brain._settings_read(), {})

    def test_first_run_terminal_states(self):
        """error / empty 要重试，用户主动 skip 或已建好 auto 才是终态。"""
        with mock.patch.object(brain, 'init_db', return_value=_EmptyDb()):
            for val in ('error', 'empty'):
                brain._settings_update({'firstrun_done': val})
                self.assertTrue(brain.first_run_need(), val)
            for val in ('skip', 'auto'):
                brain._settings_update({'firstrun_done': val})
                self.assertFalse(brain.first_run_need(), val)

    def test_http_settings_endpoints(self):
        """真服务：并发不丢键、坏配置不再 500、缺同源头仍然 403。"""
        srv = brain.make_server(0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d' % port
        hdr = {'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/json'}

        def req(path, data=None, headers=hdr):
            r = urllib.request.Request(
                base + path,
                data=json.dumps(data).encode() if data is not None else None,
                headers=headers,
                method='POST' if data is not None else 'GET')
            try:
                with urllib.request.urlopen(r, timeout=8) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()

        try:
            lost = 0
            for _ in range(10):
                self.sp.write_text('{}', encoding='utf-8')
                with ThreadPoolExecutor(max_workers=2) as ex:
                    a = ex.submit(req, '/api/settings', {'welcome_seen': True})
                    b = ex.submit(req, '/api/settings', {'theme': 'dark'})
                    self.assertEqual((a.result()[0], b.result()[0]), (200, 200))
                d = json.loads(self.sp.read_text(encoding='utf-8'))
                if d.get('welcome_seen') is not True or d.get('theme') != 'dark':
                    lost += 1
            self.assertEqual(lost, 0)

            self.sp.write_text('[]', encoding='utf-8')
            code, body = req('/api/settings')
            self.assertEqual(code, 200)
            self.assertIsInstance(json.loads(body), dict)
            code, body = req('/api/settings', {'lang': 'en'})
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(body).get('ok'))
            self.assertEqual(json.loads(self.sp.read_text(encoding='utf-8')).get('lang'), 'en')

            self.assertEqual(req('/api/settings', headers={})[0], 403)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == '__main__':
    unittest.main()
