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


def _probe_writable(d: str) -> bool:
    """真写一个文件再删掉。只 stat 不够：只读目录 / 被安全软件锁住的目录也 stat 得过。"""
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, '.sb_write_test')
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write('ok')
        os.unlink(p)
        return True
    except Exception:                        # noqa: BLE001
        return False


def _win_fallback(env: dict, home: str) -> str:
    """Windows 的兜底目录：老代码里写死 `%LOCALAPPDATA%\\SecondBrain\\data`，一个字都不能改。"""
    base = env.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
    return os.path.join(base, 'SecondBrain', 'data')


def pick_data_dir(plat: str | None = None, env: dict | None = None, install: str | None = None,
                  home: str | None = None, probe=None) -> tuple[str, bool]:
    """挑一个真能写的数据目录，返回 `(目录, 是否用了兜底)`。

    这是 `brain.app_dir()` 的纯逻辑版（第 384 项）。Windows 上的行为必须与老代码逐字一致：

    1. `SB_DATA_DIR` 给了就**照它办**（只 mkdir，不偷偷改地方）；
    2. 否则用程序目录；
    3. 写不进去才退到 `%LOCALAPPDATA%\\SecondBrain\\data`，并把 `fallback=True` 报出去。

    mac / linux 换一条兜底链（`~/Memex-data` / `~/.memex`），因为 .app 里的目录本来就不该写。

    `probe` 只为单测注入，生产路径永远用真写测试。
    """
    name = platform_name(plat)
    env = os.environ if env is None else env
    h = home or _home()
    check = probe or _probe_writable

    forced = (env.get('SB_DATA_DIR') or '').strip()
    if name == WIN:
        first = forced or install or os.path.dirname(os.path.abspath(sys.argv[0]))
        fallback = _win_fallback(env, h)
    elif name == MAC:
        first = forced or os.path.join(h, 'Library', 'Application Support', 'Memex')
        fallback = os.path.join(h, 'Memex-data')
    elif name == LINUX:
        base = env.get('XDG_DATA_HOME') or os.path.join(h, '.local', 'share')
        first = forced or os.path.join(base, 'memex')
        fallback = os.path.join(h, '.memex')
    else:
        first = forced or install or os.getcwd()
        fallback = os.path.join(h, 'Memex-data')

    if forced:                               # 环境变量指定时先尽力把目录建出来（老代码同款）
        try:
            os.makedirs(first, exist_ok=True)
        except Exception:                    # noqa: BLE001
            pass

    if check(first):
        return first, False
    if check(fallback):
        return fallback, True
    return first, False                      # 两个都写不进：保留原判定，别假装成功
