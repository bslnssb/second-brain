# -*- coding: utf-8 -*-
"""离线授权码（Ed25519 + 授权码编解码）的单元测试。

背景：商业化需要一个**不依赖服务器**的授权方案 —— 用户买完收到一串码，
粘进「关于 → 授权」就激活，断网也能用。选纯标准库实现 Ed25519 是为了
不引入 cryptography / PyNaCl（打包体积 + 两个 spec 都要加 hiddenimports）。

这个文件守三件事：
1. Ed25519 实现是对的（RFC 8032 官方测试向量，不是自己造的数据）；
2. 授权码改一个字节就验不过；
3. **任何畸形输入都不许抛异常** —— 授权码是用户从微信/邮件里复制来的，
   会带上换行、空格、被截断，解析器一崩就等于软件打不开。

跑法：python -m unittest tests.test_license -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import license_core as L  # noqa: E402


# RFC 8032 §7.1 的三组官方向量：(私钥种子, 公钥, 消息, 签名)
RFC8032 = [
    ('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60',
     'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a',
     b'',
     'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f'
     'b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
    ('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb',
     '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c',
     b'\x72',
     '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da'
     '085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
    ('c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7',
     'fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025',
     b'\xaf\x82',
     '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac'
     '18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a'),
]

# 测试专用私钥：固定值，和作者本机的签发私钥无关
SEED = bytes.fromhex(RFC8032[0][0])
PUB = RFC8032[0][1]


class TestEd25519(unittest.TestCase):
    """实现本身对不对，只认官方向量。"""

    def test_rfc8032_vectors(self):
        for i, (sk, pk, msg, sig) in enumerate(RFC8032, 1):
            with self.subTest(vector=i):
                seed = bytes.fromhex(sk)
                self.assertEqual(L.public_key(seed).hex(), pk, '公钥推导错了')
                self.assertEqual(L.sign(seed, msg).hex(), sig, '签名和标准不一致')
                self.assertTrue(L.verify(bytes.fromhex(pk), msg, bytes.fromhex(sig)),
                                '自己的签名验不过')

    def test_verify_rejects_tampered(self):
        seed = bytes.fromhex(RFC8032[1][0])
        sig = L.sign(seed, b'hello')
        pub = bytes.fromhex(RFC8032[1][1])
        self.assertTrue(L.verify(pub, b'hello', sig))
        self.assertFalse(L.verify(pub, b'hellp', sig), '改了消息还验过了')
        bad = bytearray(sig)
        bad[0] ^= 1
        self.assertFalse(L.verify(pub, b'hello', bytes(bad)), '改了签名还验过了')
        self.assertFalse(L.verify(pub, b'hello', sig[:-1]), '长度不对没被拒')

    def test_verify_garbage_never_raises(self):
        for pub, sig in [(b'', b''), (b'x' * 32, b'y' * 64), (b'\xff' * 32, b'\xff' * 64)]:
            with self.subTest(pub=len(pub)):
                self.assertFalse(L.verify(pub, b'm', sig))

    def test_sign_rejects_bad_seed(self):
        with self.assertRaises(ValueError):
            L.sign(b'short', b'x')


class TestCode(unittest.TestCase):
    """授权码编解码 + 校验。"""

    def test_roundtrip(self):
        code = L.make_code(SEED, name='张三', exp='2027-11-07')
        res = L.parse_code(code, PUB, today='2026-11-07')
        self.assertTrue(res['valid'], res['reason'])
        self.assertEqual(res['name'], '张三')
        self.assertEqual(res['edition'], 'pro')
        self.assertEqual(res['exp'], '2027-11-07')

    def test_all_features(self):
        code = L.make_code(SEED, features='*')
        res = L.parse_code(code, PUB)
        self.assertEqual(set(res['features']), set(L.ALL_FEATURES))

    def test_partial_features(self):
        code = L.make_code(SEED, features='export,ocr_batch')
        res = L.parse_code(code, PUB)
        self.assertEqual(res['features'], ['export', 'ocr_batch'])

    def test_unknown_feature_dropped(self):
        """功能位只认白名单：签发端写错一个名字，不该让程序做未知的事。"""
        code = L.make_code(SEED, features='export,no_such_thing')
        res = L.parse_code(code, PUB)
        self.assertEqual(res['features'], ['export'])

    def test_perpetual(self):
        res = L.parse_code(L.make_code(SEED, exp=''), PUB)
        self.assertTrue(res['valid'])
        self.assertEqual(res['exp'], '')
        self.assertIsNone(L.days_left(''))

    def test_expired(self):
        code = L.make_code(SEED, exp='2020-01-01')
        res = L.parse_code(code, PUB, today='2026-11-07')
        self.assertFalse(res['valid'])
        self.assertEqual(res['reason'], 'expired')
        self.assertEqual(res['edition'], 'free', '过期后必须降级成免费版')

    def test_expires_today_still_valid(self):
        """到期当天还能用 —— 否则会出现「明明写的今天到期，今天就不让用」。"""
        res = L.parse_code(L.make_code(SEED, exp='2026-11-07'), PUB, today='2026-11-07')
        self.assertTrue(res['valid'])

    def test_tampered_payload(self):
        """改 payload：签名必然对不上。"""
        code = L.make_code(SEED, name='张三', exp='2027-11-07')
        head, payload, sig = code.split('.')
        flipped = payload[:-2] + ('AA' if payload[-2:] != 'AA' else 'BB')
        res = L.parse_code('.'.join([head, flipped, sig]), PUB)
        self.assertFalse(res['valid'])
        self.assertIn(res['reason'], ('signature', 'format'))

    def test_wrong_pubkey(self):
        code = L.make_code(SEED, name='张三')
        other = bytes.fromhex(RFC8032[1][1]).hex()
        self.assertFalse(L.parse_code(code, other)['valid'])

    def test_malformed_never_raises(self):
        """这些输入都是从聊天窗口复制粘贴来的真实形状。"""
        cases = [
            '', '   ', 'SB1', 'SB1.', 'SB1..', 'SB1.a.b', 'SB1..' * 3,
            'SB1.@@@.###', 'not-a-code', 'SB1.' + 'A' * 500 + '.BB',
            'SB1.' + L._b64e(b'not zlib') + '.',
        ]
        for c in cases:
            with self.subTest(code=c[:20]):
                res = L.parse_code(c, PUB)
                self.assertFalse(res['valid'])
                self.assertEqual(res['edition'], 'free')

    def test_whitespace_tolerated(self):
        """复制时带上换行/空格是常态，必须能认。"""
        code = L.make_code(SEED, name='张三')
        messy = '  ' + code[:40] + '\n' + code[40:] + '  \n'
        self.assertTrue(L.parse_code(messy, PUB)['valid'])

    def test_truncated_code(self):
        code = L.make_code(SEED, name='张三')
        self.assertFalse(L.parse_code(code[:-20], PUB)['valid'])

    def test_bad_pubkey_hex(self):
        """公钥配错是程序 bug，不能表现成「用户的码无效」。"""
        res = L.parse_code(L.make_code(SEED), 'zzzz')
        self.assertEqual(res['reason'], 'server')

    def test_version_guard(self):
        """未来格式版本必须被拒，而不是被当成 v1 硬解。"""
        import json
        import zlib

        raw = json.dumps({'v': 9, 't': 'pro', 'n': '', 'u': '', 'e': '', 'i': '',
                          'f': ['*']}, separators=(',', ':')).encode()
        code = 'SB1.%s.%s' % (L._b64e(zlib.compress(raw)), L._b64e(L.sign(SEED, raw)))
        self.assertEqual(L.parse_code(code, PUB)['reason'], 'version')


class TestHelpers(unittest.TestCase):
    def test_days_left(self):
        self.assertEqual(L.days_left('2026-11-08', today='2026-11-07'), 1)
        self.assertEqual(L.days_left('2026-11-07', today='2026-11-07'), 0)
        self.assertLess(L.days_left('2026-11-01', today='2026-11-07'), 0)
        self.assertIsNone(L.days_left(''))
        self.assertIsNone(L.days_left('乱写'))

    def test_mask_code(self):
        code = L.make_code(SEED, name='张三')
        masked = L.mask_code(code)
        self.assertLess(len(masked), len(code))
        self.assertNotEqual(masked, code)
        self.assertEqual(L.mask_code('短码'), '短码')


class TestBrainIntegration(unittest.TestCase):
    """brain 层的读/存/清 —— 用临时数据目录，绝不碰真实 brain.db。"""

    @classmethod
    def setUpClass(cls):
        import brain

        cls.brain = brain

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sb_lic_')
        self._old_dir = self.brain._DATA_DIR
        self.brain._DATA_DIR = [Path(self.tmp.name)]
        self.brain._LIC = {'loaded': False, 'state': None}
        # 测试用自己这组公私钥，**不依赖作者本机的签发私钥**（那个绝不进仓库）
        self._old_pub = self.brain.LICENSE_PUBKEY_HEX
        self.brain.LICENSE_PUBKEY_HEX = PUB

    def tearDown(self):
        self.brain._DATA_DIR = self._old_dir
        self.brain.LICENSE_PUBKEY_HEX = self._old_pub
        self.brain._LIC = {'loaded': False, 'state': None}
        self.tmp.cleanup()

    def test_free_by_default(self):
        st = self.brain.license_state(recheck=True)
        self.assertEqual(st['edition'], 'free')
        self.assertFalse(st['valid'])
        self.assertFalse(self.brain.license_feature('export'))

    def test_activate_and_persist(self):
        code = L.make_code(SEED, name='张三', exp='2027-11-07')
        r = self.brain.license_activate(code)
        self.assertTrue(r['ok'], r.get('reason'))
        self.assertEqual(r['state']['edition'], 'pro')
        self.assertTrue((Path(self.tmp.name) / 'license.json').is_file())

        # 重新"启动"：清缓存后从磁盘读回来
        self.brain._LIC = {'loaded': False, 'state': None}
        st = self.brain.license_state()
        self.assertEqual(st['edition'], 'pro')
        self.assertEqual(st['name'], '张三')

    def test_bad_code_not_persisted(self):
        """坏码不落盘：试错十次不该把原来的好码覆盖掉。"""
        good = L.make_code(SEED, name='张三')
        self.assertTrue(self.brain.license_activate(good)['ok'])
        bad = good[:-6] + ('A' if good[-6] != 'A' else 'B') + good[-5:]
        r = self.brain.license_activate(bad)
        self.assertFalse(r['ok'])
        self.assertEqual(r['reason'], 'signature')
        self.assertEqual(self.brain.license_state()['edition'], 'pro')

    def test_clear(self):
        self.brain.license_activate(L.make_code(SEED, name='张三'))
        st = self.brain.license_clear()
        self.assertEqual(st['edition'], 'free')
        self.assertFalse((Path(self.tmp.name) / 'license.json').exists())
        # 重复清除不该抛
        self.assertEqual(self.brain.license_clear()['edition'], 'free')

    def test_corrupt_license_file_degrades_to_free(self):
        """license.json 被写坏不能连带软件不能用。"""
        (Path(self.tmp.name) / 'license.json').write_text('{这不是 json', encoding='utf-8')
        st = self.brain.license_state(recheck=True)
        self.assertEqual(st['edition'], 'free')
        self.assertFalse(st['valid'])

    def test_clock_rollback_warns_only(self):
        """时钟回拨只提示，不改判 —— 双系统/虚拟机时区经常造成「今天比上次早」。"""
        import json

        code = L.make_code(SEED, name='张三')
        self.assertTrue(self.brain.license_activate(code)['ok'])
        p = Path(self.tmp.name) / 'license.json'
        data = json.loads(p.read_text(encoding='utf-8'))
        data['last_seen'] = '2099-01-01'
        p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

        self.brain._LIC = {'loaded': False, 'state': None}
        st = self.brain.license_state()
        self.assertTrue(st['valid'], '回拨不该把授权判死')
        self.assertTrue(st['clock_warn'])


class 机器绑定(unittest.TestCase):
    """第 170 项：u 以 hw: 开头的码绑定机器，换机必须拒签。"""

    SEED = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc4'
                         '4449c5697b326919703bac031cae7f60')

    def setUp(self):
        self.pub = L.public_key(self.SEED).hex()

    def test_hwid_稳定且是16位hex(self):
        a, b = L.hwid(), L.hwid()
        self.assertEqual(a, b, '同一台机器两次结果必须一致')
        self.assertEqual(len(a), 16)
        self.assertTrue(all(c in '0123456789abcdef' for c in a), a)

    def test_bind_ident_前缀(self):
        self.assertTrue(L.bind_ident('abcdef0123456789').startswith('hw:'))

    def test_绑定码本机通过(self):
        h = L.hwid()
        code = L.make_code(self.SEED, name='张三', ident=L.bind_ident(h), exp='2030-01-01')
        self.assertTrue(L.parse_code(code, self.pub, hwid=h)['valid'])

    def test_绑定码换机拒签(self):
        code = L.make_code(self.SEED, ident=L.bind_ident('0123456789abcdef'))
        res = L.parse_code(code, self.pub, hwid='fedcba9876543210')
        self.assertFalse(res['valid'])
        self.assertEqual(res['reason'], 'hwid')

    def test_绑定码不传hwid也拒(self):
        """调用方漏传 hwid 时必须拒绝，不能静默放行。"""
        code = L.make_code(self.SEED, ident=L.bind_ident('0123456789abcdef'))
        self.assertEqual(L.parse_code(code, self.pub)['reason'], 'hwid')

    def test_老码不受影响(self):
        """已经发出去的码 u 是自由文本，加了绑定校验后必须仍然可用。"""
        code = L.make_code(self.SEED, ident='a@b.com', exp='2030-01-01')
        self.assertTrue(L.parse_code(code, self.pub, hwid=L.hwid())['valid'])

    def test_绑定码改ident会验签失败(self):
        code = L.make_code(self.SEED, ident=L.bind_ident('0123456789abcdef'))
        parts = code.split('.')
        raw = __import__('zlib').decompress(L._b64d(parts[1])).replace(
            b'0123456789abcdef', b'ffffffffffffffff')
        forged = 'SB1.' + L._b64e(__import__('zlib').compress(raw, 9)) + '.' + parts[2]
        self.assertEqual(L.parse_code(forged, self.pub, hwid='f' * 16)['reason'],
                         'signature')


class 在线核销降级(unittest.TestCase):
    """第 170 项：没端点 / 网络挂了都不能挡住已通过本地验签的用户。"""

    def setUp(self):
        import brain
        self.brain = brain

    def test_无端点直接offline(self):
        from unittest import mock
        with mock.patch.object(self.brain.error_report, 'ingest_config',
                               return_value={'enabled': False, 'url': ''}):
            st, why = self.brain._license_online('SB1.x.y')
        self.assertEqual(st, 'offline')
        self.assertIn('endpoint', why)

    def test_网络异常也降级(self):
        from unittest import mock
        with mock.patch.object(self.brain.error_report, 'ingest_config',
                               return_value={'url': 'https://example.invalid/ingest?token=abc'}), \
             mock.patch.object(self.brain.urllib.request, 'urlopen',
                               side_effect=OSError('boom')):
            st, _ = self.brain._license_online('SB1.x.y')
        self.assertEqual(st, 'offline')


if __name__ == '__main__':
    unittest.main()
