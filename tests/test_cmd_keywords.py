# -*- coding: utf-8 -*-
"""第 355 项：命令关键字 —— shell_open 的行为约束。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain


class ShellOpenTest(unittest.TestCase):
    def setUp(self):
        self._old = brain.subprocess.Popen
        self.calls = []

        def fake(args, **kw):
            self.calls.append({'args': list(args), 'cwd': kw.get('cwd'),
                               'flags': kw.get('creationflags')})
            return None

        brain.subprocess.Popen = fake

    def tearDown(self):
        brain.subprocess.Popen = self._old

    def test_rejects_unknown_kind(self):
        out = brain.shell_open('bogus', 'C:\\')
        self.assertFalse(out['ok'])
        self.assertEqual(out['err'], 'kind')
        self.assertEqual(self.calls, [])

    def test_cmd_uses_cwd_not_shell_string(self):
        tmp = os.path.dirname(os.path.abspath(__file__))
        out = brain.shell_open('cmd', tmp)
        self.assertTrue(out['ok'])
        self.assertEqual(self.calls[0]['args'], ['cmd'])
        self.assertEqual(self.calls[0]['cwd'], tmp)

    def test_psh_argv(self):
        out = brain.shell_open('psh', os.path.dirname(os.path.abspath(__file__)))
        self.assertTrue(out['ok'])
        self.assertEqual(self.calls[0]['args'], ['powershell', '-NoExit'])

    def test_bad_dir_falls_back_to_home(self):
        out = brain.shell_open('cmd', r'Z:\definitely\not\here')
        self.assertTrue(out['ok'])
        self.assertEqual(out['dir'], os.path.expanduser('~'))

    def test_no_args_falls_back_to_home(self):
        out = brain.shell_open('psh', '')
        self.assertEqual(out['dir'], os.path.expanduser('~'))

    def test_path_with_quotes_is_only_a_cwd(self):
        """带引号和 & 的目录名不会变成命令的一部分。"""
        tmp = os.path.dirname(os.path.abspath(__file__))
        weird = os.path.join(tmp, 'a&b "c"')
        try:
            os.makedirs(weird, exist_ok=True)
        except OSError:
            self.skipTest('目录名建不出来')
        out = brain.shell_open('cmd', weird)
        self.assertTrue(out['ok'])
        self.assertEqual(self.calls[0]['args'], ['cmd'])
        self.assertEqual(self.calls[0]['cwd'], weird)


if __name__ == '__main__':
    unittest.main()
