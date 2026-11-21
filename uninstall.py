# -*- coding: utf-8 -*-
"""uninstall.py — 卸载 Memex（第 296 项）。

为什么要有这个：装的时候只写了快捷方式和开机自启，**没有任何卸载入口** ——
用户想删只能自己去「设置 → 应用」找不到、也不知道 C:\\Users\\...\\App 在哪。
这里做三件事：

1. `ensure_uninstall_entry()`：在「设置 → 应用 / 控制面板」里登记一条卸载项
   （HKCU\\...\\Uninstall\\Memex），卸载命令是 `Memex.exe --uninstall`；
2. `build_script()`：生成一个**独立 .cmd**，等主进程退出后清快捷方式、注册表、程序目录。
   自己删不掉自己（exe 正在运行、目录里全是自己的文件），必须借外部脚本来做；
3. `run_uninstall()`：写脚本 → 分离启动 → 调用方随即退出程序。

两种模式：
- `keep_data=True`  程序文件清掉，`*.db`（索引库）、`settings.json`、`backups/`、`lang.txt` 留着；
- `keep_data=False` 整个安装目录删掉（不可恢复，界面里要二次确认）。
"""
import os
import subprocess
import sys
import tempfile
import time

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
UN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\Memex'
RUN_VALUE = 'Memex'
APP_NAME = 'Memex'


def _shortcuts():
    """安装器建的两条快捷方式：桌面 + 开始菜单。名字变了也按通配删一遍。"""
    home = os.path.expanduser('~')
    out = [os.path.join(home, 'Desktop', APP_NAME + '.lnk'),
           os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows',
                        'Start Menu', 'Programs', APP_NAME + '.lnk')]
    return [p for p in out if p]


def _exe_path():
    try:
        if getattr(sys, 'frozen', False):
            return os.path.abspath(sys.executable)
    except Exception:
        pass
    return ''


def _trace(msg):
    """把卸载登记的失败原因落到安装目录里的 uninstall_entry.log。

    为什么要这个：app_window 的 _log 只在 SB_DEBUG=1 时写，装机版正常启动是关的，
    第 296 项第一轮就是这样「静默失败」——注册表没写进去，日志里一个字都没有。
    """
    try:
        p = os.path.join(os.path.dirname(_exe_path()) or tempfile.gettempdir(),
                         'uninstall_entry.log')
        with open(p, 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


def ensure_uninstall_entry(version='', app=None):
    """登记「设置 → 应用」里的卸载项。重复调用安全（每次都覆盖成当前值）。"""
    if os.name != 'nt':
        return False
    exe = _exe_path()
    app = app or (os.path.dirname(exe) if exe else '')
    if not exe:
        _trace('skip: not frozen (source run)')
        return False
    if not os.path.isfile(exe):
        _trace('skip: exe missing %r' % exe)
        return False
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UN_KEY) as k:
            winreg.SetValueEx(k, 'DisplayName', 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(k, 'DisplayIcon', 0, winreg.REG_SZ, exe)
            winreg.SetValueEx(k, 'InstallLocation', 0, winreg.REG_SZ, app)
            winreg.SetValueEx(k, 'Publisher', 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(k, 'DisplayVersion', 0, winreg.REG_SZ, str(version or ''))
            # NoModify/NoRepair：这不是 MSI，改/修复按钮没有意义
            winreg.SetValueEx(k, 'NoModify', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, 'NoRepair', 0, winreg.REG_DWORD, 1)
            # 第 302 项：安装目录里有 Uninstall.exe 就优先用它（用户双击的那个），
            # 没有才退回 Memex.exe --uninstall。
            uni = os.path.join(app, 'Uninstall.exe')
            if os.path.isfile(uni):
                cmd = '"%s"' % uni
                quiet = '"%s" --quiet' % uni
            else:
                cmd = '"%s" --uninstall' % exe
                quiet = '"%s" --uninstall --quiet' % exe
            winreg.SetValueEx(k, 'UninstallString', 0, winreg.REG_SZ, cmd)
            winreg.SetValueEx(k, 'QuietUninstallString', 0, winreg.REG_SZ, quiet)
            try:
                for root, _dirs, files in os.walk(app):
                    pass
                total = 0
                for root, _dirs, files in os.walk(app):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                winreg.SetValueEx(k, 'EstimatedSize', 0, winreg.REG_DWORD,
                                  max(1, int(total / 1024)))
            except Exception:
                pass
        return True
    except Exception as _e:
        _trace('fail: %r' % (_e,))
        return False


def build_script(app, exe, keep_data=True, wait=4):
    """生成卸载 .cmd。只用 ASCII，免得 cmd 的代码页把中文吃成乱码。"""
    app = os.path.abspath(app)
    lines = [
        '@echo off',
        'chcp 65001 >nul 2>nul',
        'timeout /t %d /nobreak >nul 2>nul' % max(1, int(wait)),
        'taskkill /F /IM %s.exe >nul 2>nul' % APP_NAME,
        'timeout /t 2 /nobreak >nul 2>nul',
        'reg delete "HKCU\\%s" /v %s /f >nul 2>nul' % (RUN_KEY, RUN_VALUE),
        'reg delete "HKCU\\%s" /f >nul 2>nul' % UN_KEY,
    ]
    for p in _shortcuts():
        lines.append('del /q "%s" >nul 2>nul' % p)
    # 开始菜单里可能还有整个文件夹（安装器按语言建过）
    sm = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows',
                      'Start Menu', 'Programs')
    lines.append('if exist "%s\\%s" rd /s /q "%s\\%s" >nul 2>nul' % (sm, APP_NAME, sm, APP_NAME))
    if keep_data:
        # 留下用户数据：索引库(*.db)、settings.json、backups、lang.txt
        lines += [
            'for %%F in ("%s\\*") do (' % app,
            '  if /I not "%%~xF"==".db" if /I not "%%~nxF"=="settings.json" '
            'if /I not "%%~nxF"=="backups" if /I not "%%~nxF"=="lang.txt" '
            'if /I not "%%~nxF"=="settings.json.bak" rd /s /q "%%~F"',
            ')',
            'del /q "%s\\Memex.exe" >nul 2>nul' % app,
            'del /q "%s\\Uninstall.exe" >nul 2>nul' % app,
        ]
    else:
        lines.append('rd /s /q "%s" >nul 2>nul' % app)
    lines.append('del /q "%~f0" >nul 2>nul')
    return '\r\n'.join(lines) + '\r\n'


def run_uninstall(app=None, keep_data=True, wait=4):
    """写脚本、分离启动、立刻返回。调用方应当随后退出程序（脚本会等 4 秒）。"""
    if os.name != 'nt':
        return False, ''
    exe = _exe_path()
    app = app or (os.path.dirname(exe) if exe else '')
    if not app or not os.path.isdir(app):
        return False, ''
    script = os.path.join(tempfile.gettempdir(),
                          'memex-uninstall-%d.cmd' % int(time.time()))
    try:
        with open(script, 'w', encoding='utf-8', newline='') as f:
            f.write(build_script(app, exe, keep_data=keep_data, wait=wait))
        flags = 0
        if hasattr(subprocess, 'DETACHED_PROCESS'):
            flags |= subprocess.DETACHED_PROCESS
        if hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP'):
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(['cmd', '/c', script], creationflags=flags,
                         close_fds=True, cwd=tempfile.gettempdir())
        return True, script
    except Exception:
        try:
            os.unlink(script)
        except Exception:
            pass
        return False, ''
