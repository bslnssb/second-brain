"""平台名与目录约定（第 382 项）。

Windows 现状（不能动）：程序和数据都在同一个目录里
    C:\\Users\\<u>\\AppData\\Local\\Memex\\App    ← Memex.exe / _internal / brain.db / settings.json
这个「装哪就在哪存」的习惯在 mac 上不合适（.app 是只读签名包，
往里写文件会破坏签名、也会被 Gatekeeper 当成篡改），所以显式把两个概念分开：

    install_dir()  程序所在目录（Windows 才有意义；mac 是 .app 内部）
    data_dir()     数据目录（brain.db / settings.json / webview-profile / error_reports）

Windows 上两者**同值**，所以现有代码把 app_dir() 当数据目录用不会变；
mac 上 data_dir() 落到 ~/Library/Application Support/Memex，linux 落到 XDG 目录。
"""

import os
import sys

WIN = 'win'
MAC = 'mac'
LINUX = 'linux'
OTHER = 'other'


def platform_name(plat: str | None = None) -> str:
    """把 sys.platform 归一成四个名字。传参是为了单测能直接喂 'darwin' / 'linux2'。"""
    p = sys.platform if plat is None else plat
    if p.startswith('win') or p in ('cygwin', 'msys'):
        return WIN
    if p == 'darwin':
        return MAC
    if p.startswith('linux'):
        return LINUX
    return OTHER


def _home() -> str:
    return os.path.expanduser('~')


def data_dir(plat: str | None = None, env: dict | None = None, install: str | None = None) -> str:
    """数据目录。Windows 保持现状（= 程序目录），只有 mac/linux 走系统约定。"""
    name = platform_name(plat)
    env = os.environ if env is None else env
    if name == WIN:
        return install or os.environ.get('LOCALAPPDATA', '') or _home()
    if name == MAC:
        return os.path.join(_home(), 'Library', 'Application Support', 'Memex')
    if name == LINUX:
        base = env.get('XDG_DATA_HOME') or os.path.join(_home(), '.local', 'share')
        return os.path.join(base, 'memex')
    return install or os.getcwd()


def install_dir(plat: str | None = None) -> str:
    """程序所在目录。Windows 用当前解释器/打包目录；mac 是 .app 内的 MacOS 目录。"""
    name = platform_name(plat)
    if name == WIN:
        return os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False)
                                                else sys.argv[0]))
    if name == MAC:
        # 打包后 sys.executable 在 Foo.app/Contents/MacOS/ 下
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(sys.argv[0]))
