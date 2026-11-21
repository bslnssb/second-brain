# -*- coding: utf-8 -*-
"""第 341 项：代理端口连不上时自动改走直连。

朋友那种「没梯子」或者「Clash 关了但系统代理还留着」的机器，以前所有外网请求都
会砸在一个死端口上，翻译面板整片「连不上 / 无结果」。现在 netproxy 用一次 TCP
探测判断代理是否真在跑，不跑就当没有代理。
"""
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netproxy
import translator


class HostPortTest(unittest.TestCase):
    def test_host_port_http(self):
        self.assertEqual(('127.0.0.1', 7897), netproxy._host_port('http://127.0.0.1:7897'))

    def test_host_port_bare(self):
        self.assertEqual(('127.0.0.1', 7890), netproxy._host_port('127.0.0.1:7890'))

    def test_host_port_default_port(self):
        self.assertEqual(('proxy.example.com', 80), netproxy._host_port('http://proxy.example.com'))


class AliveTest(unittest.TestCase):
    def setUp(self):
        with netproxy._LOCK:
            netproxy._ALIVE.update({'url': '', 'ok': True, 'at': 0.0})
            netproxy._BYPASS.update({'url': '', 'at': 0.0})

    def test_dead_port_is_not_alive(self):
        # 9 是 discard 端口，正常机器上没人监听
        self.assertFalse(netproxy._proxy_alive('http://127.0.0.1:9', timeout=0.4))

    def test_open_port_is_alive(self):
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(4)
        port = srv.getsockname()[1]
        try:
            self.assertTrue(netproxy._proxy_alive('http://127.0.0.1:%d' % port, timeout=0.6))
        finally:
            srv.close()

    def test_bypass_recorded(self):
        netproxy._proxy_alive('http://127.0.0.1:9', timeout=0.4)
        self.assertEqual('http://127.0.0.1:9', netproxy.last_bypass())

    def test_alive_result_cached(self):
        with netproxy._LOCK:
            netproxy._ALIVE.update({'url': 'http://127.0.0.1:9', 'ok': False, 'at': 0.0})
        # 缓存里已是「不通」，不该再去连一次（这里只验证返回值一致）
        self.assertFalse(netproxy._proxy_alive('http://127.0.0.1:9', timeout=0.4))


class ProxyUrlFallbackTest(unittest.TestCase):
    def setUp(self):
        self._old = netproxy._settings
        with netproxy._LOCK:
            netproxy._ALIVE.update({'url': '', 'ok': True, 'at': 0.0})
            netproxy._BYPASS.update({'url': '', 'at': 0.0})
        netproxy.refresh()

    def tearDown(self):
        netproxy._settings = self._old
        netproxy.refresh()

    def test_dead_custom_proxy_falls_back_to_direct(self):
        netproxy._settings = lambda: {'proxy_mode': 'custom',
                                      'proxy_host': '127.0.0.1', 'proxy_port': 9}
        self.assertEqual('', netproxy.proxy_url(True))
        self.assertEqual('http://127.0.0.1:9', netproxy.last_bypass())

    def test_live_custom_proxy_kept(self):
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(4)
        port = srv.getsockname()[1]
        try:
            netproxy._settings = lambda: {'proxy_mode': 'custom',
                                         'proxy_host': '127.0.0.1', 'proxy_port': port}
            self.assertEqual('http://127.0.0.1:%d' % port, netproxy.proxy_url(True))
        finally:
            srv.close()


class TranslateFieldTest(unittest.TestCase):
    def setUp(self):
        # mode_of 是全局函数，改完必须还原，否则后面的 test_netproxy_299 全被带跑
        self._old_mode = netproxy.mode_of

    def tearDown(self):
        netproxy.mode_of = self._old_mode
        netproxy.refresh()

    def test_response_has_proxy_bypass(self):
        netproxy.mode_of = lambda d=None: 'none'
        netproxy.refresh()
        r = translator.translate_text('hello', 'auto', 'zh-CN', engines=['mymemory'])
        self.assertIn('proxy_bypass', r)
        self.assertIsInstance(r['proxy_bypass'], str)


class MiniUiTest(unittest.TestCase):
    def test_mini_ui_has_trans_bypass_in_all_langs(self):
        import brain
        for lang, d in brain.MINI_UI.items():
            self.assertIn('trans_bypass', d, 'lang=%s 缺 trans_bypass' % lang)
            self.assertTrue(str(d['trans_bypass']).strip())

    def test_html_placeholder_replaced(self):
        import brain
        html = brain._mini_html if hasattr(brain, '_mini_html') else ''
        # 占位符必须能在替换表里找到名字，否则界面上会露出 __U_trans_bypass__
        self.assertNotIn('__U_trans_bypass__', brain.MINI_UI['zh-CN'])
        self.assertIn('trans_bypass', brain.MINI_UI['zh-CN'])


if __name__ == '__main__':
    unittest.main()
