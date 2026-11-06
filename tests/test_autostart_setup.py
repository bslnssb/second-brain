"""安装器「装完默认开机启动」的单元测试。

全程 mock winreg，绝不碰真实注册表；同时覆盖五语言文案齐全（漏一种语言，
安装界面切换语言时那个复选框就会显示成键名 'boot'）。
"""
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('sb_setup', ROOT / 'setup_app.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def fake_winreg():
    m = types.ModuleType('winreg')
    m.HKEY_CURRENT_USER = object()
    m.REG_SZ = 1
    key = MagicMock()
    key.__enter__.return_value = key
    m.CreateKey = MagicMock(return_value=key)
    m.SetValueEx = MagicMock()
    return m, key


class AutostartSetupTest(unittest.TestCase):

    def setUp(self):
        self.env = {'SB_NO_AUTOSTART': ''}
        self.exe = ROOT / 'SecondBrain.exe'

    def call(self, **env):
        m, key = fake_winreg()
        on = env.pop('on', True)          # on 是函数参数，不能混进环境变量（bool 会炸）
        e = dict(os.environ)
        e.pop('SB_NO_AUTOSTART', None)
        e.update(env)
        with patch.dict(sys.modules, {'winreg': m}), patch.dict(os.environ, e, clear=True):
            ok = setup.set_autostart(self.exe, on=on)
        return ok, m, key

    def test_默认写入当前exe的silent命令(self):
        ok, m, key = self.call()
        self.assertTrue(ok)
        self.assertEqual(m.CreateKey.call_args[0],
                         (m.HKEY_CURRENT_USER,
                          r'Software\Microsoft\Windows\CurrentVersion\Run'))
        args = m.SetValueEx.call_args[0]
        self.assertEqual(args[1], 'SecondBrain')       # SetValueEx(k, name, 0, type, cmd)
        self.assertEqual(args[2], 0)
        self.assertEqual(args[3], m.REG_SZ)
        self.assertEqual(args[4], '"%s" --silent' % self.exe)

    def test_验收环境不写注册表(self):
        ok, m, _key = self.call(SB_NO_AUTOSTART='1')
        self.assertFalse(ok)
        m.SetValueEx.assert_not_called()

    def test_用户取消勾选时不写也不删(self):
        ok, m, _key = self.call(on=False)
        self.assertFalse(ok)
        m.SetValueEx.assert_not_called()
        m.CreateKey.assert_not_called()

    def test_写注册表失败安静返回False(self):
        m, _key = fake_winreg()
        m.CreateKey.side_effect = OSError('denied')
        with patch.dict(sys.modules, {'winreg': m}):
            self.assertFalse(setup.set_autostart(self.exe))

    def test_五种语言都有开机启动文案(self):
        for lang, d in setup.L.items():
            for k in ('boot', 'as_ok', 'as_no'):
                self.assertIn(k, d, '%s 缺 %s' % (lang, k))

    def test_后端会校验注册表路径是否还存在(self):
        # 指向已删目录的死链不该在设置里显示「已开启」
        s = (ROOT / 'brain.py').read_text(encoding='utf-8')
        self.assertIn('os.path.isfile(p)', s)


if __name__ == '__main__':
    unittest.main()
