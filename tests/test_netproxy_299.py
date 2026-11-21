# -*- coding: utf-8 -*-
"""第 299 项：netproxy 单测（三种模式 / 地址拼装 / 老配置兼容 / 探测）。

为什么值得单独测：代理配置写错是"整程序联网都坏"的级别，而且三种模式
（system / none / custom）在界面上长得像、行为完全相反，必须一条条钉住。
"""
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import netproxy


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, *a):
        pass


class BuildUrlTest(unittest.TestCase):
    def test_01_host_and_port(self):
        self.assertEqual('http://127.0.0.1:7890', netproxy.build_url('127.0.0.1', 7890))

    def test_02_host_with_port_wins(self):
        # 地址里已经写了端口，就不该再拼一次
        self.assertEqual('http://127.0.0.1:7897',
                         netproxy.build_url('127.0.0.1:7897', 7890))

    def test_03_full_url_with_auth(self):
        self.assertEqual('http://u:p@10.0.0.2:8080',
                         netproxy.build_url('http://u:p@10.0.0.2:8080', 7890))

    def test_04_https_scheme(self):
        self.assertEqual('https://proxy.local:443',
                         netproxy.build_url('https://proxy.local', 443))

    def test_05_socks_rejected(self):
        # urllib 不认 socks：直接拒绝，别让它变成一个更难懂的 DNS 错误
        self.assertEqual('', netproxy.build_url('socks5://127.0.0.1:1080', 0))

    def test_06_empty_host_falls_back_to_default(self):
        self.assertEqual('http://127.0.0.1:7890', netproxy.build_url('', 7890))

    def test_07_blank_port_uses_default(self):
        self.assertEqual('http://127.0.0.1:7890', netproxy.build_url('127.0.0.1', 0))


class ModeTest(unittest.TestCase):
    def setUp(self):
        self._orig = (netproxy._settings, netproxy.system_proxy, netproxy._proxy_alive)
        # 第 341 项：proxy_url 现在会先探代理端口活不活。这一组只验"选哪个代理"，
        # 把存活探测钉成 True（探测本身在 test_341 里单独测）。
        netproxy._proxy_alive = lambda url, timeout=1.2: True
        with netproxy._LOCK:
            netproxy._ALIVE.update({'url': '', 'ok': True, 'at': 0.0})
            netproxy._BYPASS.update({'url': '', 'at': 0.0})

    def tearDown(self):
        netproxy._settings, netproxy.system_proxy, netproxy._proxy_alive = self._orig
        netproxy.refresh()

    def test_10_explicit_modes(self):
        for m in ('system', 'none', 'custom'):
            netproxy._settings = lambda m=m: {'proxy_mode': m}
            self.assertEqual(m, netproxy.mode_of())

    def test_11_legacy_trans_proxy(self):
        netproxy._settings = lambda: {'trans_proxy': 'none'}
        self.assertEqual('none', netproxy.mode_of())
        netproxy._settings = lambda: {'trans_proxy': 'auto'}
        self.assertEqual('system', netproxy.mode_of())
        netproxy._settings = lambda: {'trans_proxy': 'http://127.0.0.1:7897'}
        self.assertEqual('custom', netproxy.mode_of())

    def test_12_none_always_direct(self):
        netproxy._settings = lambda: {'proxy_mode': 'none'}
        netproxy.system_proxy = lambda: 'http://127.0.0.1:7897'
        self.assertEqual('', netproxy.proxy_url(True))

    def test_13_system_uses_registry(self):
        netproxy._settings = lambda: {'proxy_mode': 'system'}
        netproxy.system_proxy = lambda: 'http://127.0.0.1:7897'
        self.assertEqual('http://127.0.0.1:7897', netproxy.proxy_url(True))

    def test_14_custom_ignores_registry(self):
        netproxy._settings = lambda: {'proxy_mode': 'custom',
                                      'proxy_host': '10.0.0.2', 'proxy_port': 1080}
        netproxy.system_proxy = lambda: 'http://127.0.0.1:7897'
        self.assertEqual('http://10.0.0.2:1080', netproxy.proxy_url(True))

    def test_15_refresh_syncs_env(self):
        netproxy._settings = lambda: {'proxy_mode': 'custom',
                                      'proxy_host': '10.0.0.2', 'proxy_port': 1080}
        netproxy.refresh()
        self.assertEqual('http://10.0.0.2:1080', os.environ.get('HTTP_PROXY'))
        netproxy._settings = lambda: {'proxy_mode': 'none'}
        netproxy.refresh()
        self.assertNotIn('HTTP_PROXY', os.environ)
        self.assertIn('127.0.0.1', os.environ.get('NO_PROXY', ''))

    def test_16_describe_shape(self):
        netproxy._settings = lambda: {'proxy_mode': 'system'}
        netproxy.system_proxy = lambda: 'http://127.0.0.1:7897'
        d = netproxy.describe()
        self.assertEqual('system', d['mode'])
        self.assertEqual('http://127.0.0.1:7897', d['url'])
        self.assertEqual('http://127.0.0.1:7897', d['system'])


class TestProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(('127.0.0.1', 0), _H)
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_20_none_mode_reaches_local(self):
        netproxy._settings = lambda: {'proxy_mode': 'none'}
        r = netproxy.test(draft={'mode': 'none'},
                          target='http://127.0.0.1:%d/x' % self.port, timeout=3)
        self.assertTrue(r['ok'], r)
        self.assertGreaterEqual(r['ms'], 0)

    def test_21_bad_custom_url(self):
        netproxy._settings = lambda: {'proxy_mode': 'custom'}
        r = netproxy.test(draft={'mode': 'custom', 'host': 'socks5://x', 'port': 1},
                          target='http://127.0.0.1:%d/x' % self.port, timeout=3)
        self.assertFalse(r['ok'])
        self.assertEqual('bad', r['code'])

    def test_22_refused_code(self):
        util = __import__('socket')
        s = util.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()          # 拿到一个没人听的端口
        netproxy._settings = lambda: {'proxy_mode': 'none'}
        r = netproxy.test(draft={'mode': 'none'},
                          target='http://127.0.0.1:%d/x' % port, timeout=2)
        self.assertFalse(r['ok'])
        # 本机 Windows 上连"没人听的端口"有时不是立刻 refused，而是等到超时
        #（受防火墙/本地栈影响），两种都算"没连上"，别为了好看硬编成一种。
        self.assertIn(r['code'], ('refused', 'network', 'timeout'))


if __name__ == '__main__':
    unittest.main()
