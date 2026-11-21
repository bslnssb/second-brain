# -*- coding: utf-8 -*-
"""第 246 项：把 Windows「壳命名空间」里的应用入口补进文件名索引。

问题（用户反馈）：Watt Toolkit 这类 Microsoft Store 应用搜不到。
根因不是「漏扫了某个目录」，而是**这类应用的显示名根本不在文件系统里**：

  * `Get-StartApps` 里 Watt Toolkit 的 AppID 是
    `4651ED44255E.47979655102CE_k6txddmbb6c52!App`（AUMID，虚拟壳 ID），
    而 Steam 的 AppID 是 `H:\\STEAM\\steam.exe`（真实磁盘路径）。
  * 商店应用的实体躺在 `C:\\Program Files\\WindowsApps\\<PackageFullName>`，
    该目录 ACL 对普通进程直接拒绝枚举（实测 `PermissionError [WinError 5]`），
    而且目录名是包全名，不叫 "Watt Toolkit"。
  * 开始菜单里也没有对应的 `.lnk` —— 它在虚拟的 `shell:AppsFolder` 里，
    `os.walk` / `QDirIterator` / `opendir` 这类文件系统遍历永远看不见。

所以「调排除规则」是治不好的：Recoll 遇到 EACCES 直接 `goto out`、
Baloo 用 `QDirIterator`、DocFetcher 用 `File.listFiles()`，三家都是用户态遍历，
同样读不到；Everything 靠 NTFS MFT + USN Journal（服务/管理员）绕过 ACL
能列出包里的 `WattToolkit.exe`，**但依然搜不到显示名 "Watt Toolkit"**。

正解：向 Windows Shell 要应用清单，把显示名作为索引项写进 files 表，
path 记成 `shell:AppsFolder\\<AUMID>`；点击时交给 ShellExecute 启动。

数据来源只用一个 Windows 自带的只读命令 `Get-StartApps`（PowerShell 5.1 起有，
无需管理员、无需 COM、无需联网、无第三方依赖）。失败一律返回空列表 ——
采集不到最多是「商店应用搜不到」，绝不能让整轮索引重建挂掉。
"""
from __future__ import annotations

import json
import os
import subprocess

SHELL_PREFIX = 'shell:AppsFolder\\'

# PowerShell 5.1 的 ConvertTo-Json 会按当前控制台编码输出；显式切 UTF-8，
# 否则中文应用名（「画图」「照片」）在 cp936 下会变成问号。
_PS_CMD = (
    '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; '
    'Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress'
)


def is_virtual_appid(appid):
    """是不是「磁盘上没有同名 .lnk」的虚拟应用 ID（AUMID / 现代应用 ID）。

    商店与 MSIX 应用的 AUMID 形如 `PackageFamilyName!App`：
    带 `!`、不含盘符、不含反斜杠。传统 Win32 应用的 AppID 是磁盘绝对路径
    （`H:\\STEAM\\steam.exe`）或注册表 ProgID，那些已经有 .lnk / .exe 可搜，
    再收一遍只会制造重复结果。
    """
    a = str(appid or '').strip()
    if not a or '!' not in a:
        return False
    return '\\' not in a and ':/' not in a and ':\\' not in a


def is_shell_app_path(path):
    """这条索引记录是不是虚拟应用入口（对账 / 打开路径都要特判）。"""
    return str(path).lower().startswith(SHELL_PREFIX.lower())


def launch(path):
    """启动结果：虚拟应用走 `explorer.exe shell:AppsFolder\\<AUMID>`，普通路径照旧。

    为什么不用 `os.startfile(shell:...)`：ShellExecute 对 `shell:` 前缀的解析在
    不同 Windows 版本上不一致，而 `explorer.exe shell:AppsFolder\\...` 是微软
    用来按 AUMID 启动商店应用的标准做法（开始菜单自己就这么干）。
    """
    p = str(path or '')
    if is_shell_app_path(p):
        kw = {'close_fds': True}
        if os.name == 'nt':
            kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
        subprocess.Popen(['explorer.exe', p], **kw)
        return
    os.startfile(p)


def _run_ps(timeout=15.0):
    """跑一次 Get-StartApps，返回原始行列表；任何异常交给调用方降级。"""
    kw = dict(capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    if os.name == 'nt':
        # 后台重建索引时别弹一个黑框吓用户
        kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    r = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', _PS_CMD], **kw)
    if r.returncode != 0:
        return []
    out = (r.stdout or '').strip().lstrip('\ufeff')
    if not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        return [data]
    return data if isinstance(data, list) else []


def collect_start_apps(runner=None):
    """返回 [(显示名, 'shell:AppsFolder\\<AUMID>')]，按显示名排序、去重。

    runner 是测试注入点：传入一个返回 dict 列表的可调用对象即可离线验证，
    不让单元测试依赖本机 PowerShell / 真实开始菜单。
    """
    try:
        rows = (runner or _run_ps)()
    except Exception:
        return []           # 采集失败绝不影响索引重建
    out, seen = [], set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get('Name') or '').strip()
        appid = str(row.get('AppID') or '').strip()
        if not name or not is_virtual_appid(appid):
            continue
        path = SHELL_PREFIX + appid
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, path))
    out.sort(key=lambda x: (x[0].lower(), x[1]))
    return out


def app_rows(py_keys=None):
    """生成可直接 INSERT 进 files_new 的九列元组。

    py_keys 由 brain 传进来（避免这里重复 import pypinyin）；中文显示名
    也要能靠拼音/首字母搜到，否则「微软商店」「画图」这类名字只能打中文。
    """
    rows = []
    for name, path in collect_start_apps():
        fpy, fini = (None, None)
        if py_keys and any(ord(ch) > 127 for ch in name):
            try:
                fpy, fini = py_keys(name)
            except Exception:
                fpy, fini = (None, None)
        # descr 存显示名：files 表的 name 列统一小写，而搜索结果的显示名是从
        # path 取 basename（这里会是 AUMID 那串包名）。用 descr 把「Watt Toolkit」
        # 带过去，add() 里再覆盖，否则用户搜到了也只看到一串看不懂的 ID。
        rows.append((path, name.lower(), SHELL_PREFIX.rstrip('\\'), '',
                     0, 0, name, fpy, fini))
    return rows
