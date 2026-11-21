"""打开 / 定位（第 382 项）。

命令拼装是纯函数，单测只验证「在哪个平台发哪条命令」；
真跑的那层只负责 subprocess + 失败抛错（调用方本来就 try/except 成 fail 状态）。

Windows：保持原样——文件/文件夹都用 os.startfile，定位用 explorer `/select,`。
mac    ：`open <p>` 打开，`open -R <p>` 在 Finder 里选中。
linux  ：`xdg-open`；定位没有统一接口，退化成「打开所在目录」
         （能识别的桌面环境会额外试 nautilus --select，失败就算了，绝不能因此报错）。
"""

import os
import subprocess

from .paths import LINUX, MAC, WIN, platform_name


def open_argv(path: str, plat: str | None = None) -> list[str] | None:
    """返回打开用的 argv；返回 None 表示「这个平台用 os.startfile」（Windows 原样）。"""
    name = platform_name(plat)
    if name == WIN:
        return None
    if name == MAC:
        return ['open', path]
    return ['xdg-open', path]


def reveal_argv(path: str, plat: str | None = None) -> list[str] | None:
    """返回「在文件管理器里定位」用的 argv；None = Windows 的 explorer /select。"""
    name = platform_name(plat)
    if name == WIN:
        return None
    if name == MAC:
        return ['open', '-R', path]
    return ['xdg-open', os.path.dirname(path) or '.']


def _run(argv: list[str]) -> None:
    """跑命令并按退出码判成败。带 start_new_session，别让子进程跟着主进程一起被收走。"""
    try:
        r = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as e:   # 命令不存在 / 超时
        raise RuntimeError(str(e)) from e
    if r.returncode != 0:
        raise RuntimeError('%s 失败（退出码 %d）' % (argv[0], r.returncode))


def open_path(path: str) -> None:
    """用系统默认程序打开文件或目录。失败抛 RuntimeError（调用方转成 ok:false）。"""
    if not path:
        raise RuntimeError('空路径')
    argv = open_argv(path)
    if argv is None:
        os.startfile(path)      # Windows：与改造前完全一致，异常也照旧往上抛
        return
    _run(argv)


def reveal(path: str) -> None:
    """在文件管理器里定位到某个文件（Windows：资源管理器选中它）。"""
    if not path:
        raise RuntimeError('空路径')
    argv = reveal_argv(path)
    if argv is None:
        subprocess.Popen(['explorer', '/select,', path])
        return
    try:
        _run(argv)
    except RuntimeError:
        # linux 上没有统一的「选中」接口，退化成打开目录，别让用户觉得「定位失败」
        if platform_name() == LINUX:
            _run(['xdg-open', os.path.dirname(path) or '.'])
            return
        raise
