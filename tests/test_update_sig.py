# -*- coding: utf-8 -*-
"""第 286 项：更新包签名 / 错误报告密钥脱敏 的回归测试。"""
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brain            # noqa: E402
import license_core     # noqa: E402
import error_report as er  # noqa: E402


class TestUpdSignature(unittest.TestCase):
    def test_msg_is_canonical(self):
        a = brain.upd_msg('v0.9.55', 'Memex-Core.zip', 'ab' * 32)
        self.assertEqual(a.decode(), 'memex-update-v1|v0.9.55|Memex-Core.zip|%s' % ('ab' * 32))
        # 任一处变化都必须换出不同的串，否则签名可被挪用到别的包上
        for other in (('v0.9.56', 'Memex-Core.zip', 'ab' * 32),
                      ('v0.9.55', 'Memex-Core2.zip', 'ab' * 32),
                      ('v0.9.55', 'Memex-Core.zip', 'ac' * 32)):
            self.assertNotEqual(brain.upd_msg(*other), a)

    def test_ed25519_roundtrip_and_tamper(self):
        seed = secrets.token_bytes(32)
        pub = license_core.public_key(seed)
        m = brain.upd_msg('v0.9.55', 'a.zip', 'cd' * 32)
        sig = license_core.sign(seed, m)
        self.assertTrue(license_core.verify(pub, m, sig))
        self.assertFalse(license_core.verify(pub, m[:-1] + b'X', sig))
        self.assertFalse(license_core.verify(pub, m, sig[:-1] + b'\x01'))

    def test_upd_sig_ok_rejects_garbage(self):
        self.assertFalse(brain.upd_sig_ok('v1', 'a.zip', 'ab' * 32, ''))
        self.assertFalse(brain.upd_sig_ok('v1', 'a.zip', 'ab' * 32, 'zz'))
        self.assertFalse(brain.upd_sig_ok('v1', 'a.zip', '', 'ab' * 64))

    def test_unsigned_version_json_is_refused(self):
        """need_sig 的源（镜像 version.json）没有签名 → 必须拒绝。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.zip'
            p.write_bytes(b'hello')
            ok, k, err = brain.upd_check_file(
                {'need_sig': True, 'latest': 'v9.9.9'}, {'name': 'a.zip', 'sha256': '', 'sig': ''}, p)
            self.assertFalse(ok)
            self.assertEqual(k, 'upd_badsig')

    def test_hash_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.zip'
            p.write_bytes(b'hello')
            ok, k, _ = brain.upd_check_file(
                {'need_sig': False}, {'name': 'a.zip', 'sha256': 'de' * 32, 'sig': ''}, p)
            self.assertFalse(ok)
            self.assertEqual(k, 'upd_badsig')

    def test_github_branch_without_signature_still_passes(self):
        """GitHub API 直连分支不要求签名（TLS 直达官方），否则老版本升不上来。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.zip'
            p.write_bytes(b'hello')
            ok, k, _ = brain.upd_check_file({'need_sig': False}, {'name': 'a.zip'}, p)
            self.assertTrue(ok, k)

    def test_version_json_marks_need_sig_and_carries_fields(self):
        info = {'version': '0.9.99',
                'assets': [{'name': 'A.zip', 'url': 'https://x/A.zip', 'size': 1,
                            'sha256': 'AB' * 32, 'sig': 'cd' * 64}]}
        out = brain._ver_from_json(info)
        self.assertTrue(out['need_sig'])
        self.assertEqual(out['asset']['sha256'], 'ab' * 32)
        self.assertEqual(out['asset']['sig'], 'cd' * 64)

    def test_pubkey_constant_is_32_bytes(self):
        pub = bytes.fromhex(brain.UPD_PUBKEY_HEX)
        self.assertEqual(len(pub), 32)


class TestScrubSecrets(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        er.configure(self._tmp.name, version='scrub-test')

    def tearDown(self):
        er.configure(None)
        self._tmp.cleanup()

    def _scrub(self, s):
        return er.scrub(s, include_paths=False)

    def test_masks_common_key_shapes(self):
        cases = [
            'sk-abcdefghijklmnopqrstuvwxyz012345',
            'AIza' + 'b' * 35,
            'ghp_' + 'c' * 30,
            'github_pat_' + 'd' * 22,
            'xoxb-1234567890-abcdefghij',
            'AKIA' + 'E' * 16,
            'Authorization: Bearer abcdefghijklmnopqrstuvwxyz',
            'api_key=abcdefghijklmnop1234',
        ]
        for s in cases:
            out = self._scrub('出错了 ' + s + ' 结束')
            self.assertNotIn(s, out, s)
            self.assertIn('<KEY>', out, s)

    def test_keeps_ordinary_words(self):
        s = 'task-1234 磁盘 disk-usage 都正常'
        self.assertEqual(self._scrub(s), s)

    def test_license_and_query_still_masked(self):
        out = self._scrub('SB1.' + 'A' * 30 + ' https://x.test/a?token=secretvalue')
        self.assertIn('<LICENSE_CODE>', out)
        self.assertIn('<QUERY>', out)


if __name__ == '__main__':
    unittest.main()
