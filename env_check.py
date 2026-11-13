#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二大脑 · 运行环境自检与自适应（把「用户那边是什么样」变成可测量、可自愈的数据）

为什么单独有这个文件 —— 而不是继续「一个环境打一个补丁」：

    用户的机器有多少种，我们就有多少种没见过。靠人在自己电脑上试，只能撞见
    自己那一台。所以这里换个做法：**不猜环境，只测量环境**，然后把结论变成
    三样东西：

      1. 明确判定   —— 这一项在这台机器上到底行不行（OK / INFO / WARN / FAIL）
      2. 自动动作   —— 能自己修的当场修（换数据目录、换端口、降级渲染）
      3. 可复制报告 —— 用户点一下「环境自检」，把报告发过来，等于把现场搬过来

    判定全部基于「实测 + 阈值」，不基于版本号猜测。新增一项检查的成本是写一个
    probe 函数，而不是在 app_window 里加一个 if。

三层防线（这个文件是第 2 层）：

    ① 代码不假设环境   —— 永远测量、带容差、可降级（app_window 的 _fit_main_size）
    ② 启动探测 + 自愈   —— 本文件：探不出能力就换路子，而不是白屏/闪退
    ③ 自动化验证       —— tests/：把环境做成参数（DPI-unaware 线程＝100% 缩放、
                          负坐标＝副屏、假 db＝损坏库、假注册表＝没装 WebView2）

用法：
    python env_check.py              # 人类可读报告（发现 FAIL 时退出码 1）
    python env_check.py --json       # 机器可读（给支持人员用）
    SecondBrain.exe --diagnose       # 打包版同样能跑

注意：这里**只读**环境，除 `apply_fixes()` 里明确列出的小动作（建目录、探测写权限）
之外不动任何系统设置。所有 ctypes 调用都单独包 try —— 一项炸了不能拖垮整份报告，
否则用户拿到的报告本身就是残缺的，比没有更糟。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

__all__ = ['probe_all', 'judge', 'plan', 'render', 'main',
           'webview2_version', 'logical_screens', 'free_port']

# ── WebView2 运行时（Edge 的独立组件，Win10 不是天生就有）───────────────────
# 注册表里这条 GUID 就是「WebView2 Runtime」本身；'pv' = 版本，'0.0.0.0' = 没装。
# 三处都要看：64 位系统上 32 位安装写 WOW6432Node，用户级安装写 HKCU。
_WV2_GUID = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
_WV2_KEYS = [
    ('HKLM', r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\\' + _WV2_GUID),
    ('HKLM', r'SOFTWARE\Microsoft\EdgeUpdate\Clients\\' + _WV2_GUID),
    ('HKCU', r'Software\Microsoft\EdgeUpdate\Clients\\' + _WV2_GUID),
]
_WV2_URL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'

# 索引是「文件名 + 正文」两套表，写放大不小；低于这个值就别让用户白跑一遍扫描。
MIN_FREE_MB = 300
# 快搜窗设计宽度（逻辑像素）。屏幕比这还窄时必须收窄，否则右边被切掉。
MINI_DESIGN_W = 760
MINI_MIN_W = 420


# ══════════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════════

def _user32():
    try:
        u = ctypes.WinDLL('user32', use_last_error=True)
        # Per-Monitor v2：不加这句，本进程看到的分辨率会被系统按 96dpi 虚拟化，
        # 150% 屏上 GetSystemMetrics 返回的是「逻辑」值，算出来的窗口尺寸全错。
        try:
            u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        return u
    except Exception:
        return None


def _reg_read(hive_name, path, value):
    try:
        import winreg
        hive = {'HKLM': winreg.HKEY_LOCAL_MACHINE,
                'HKCU': winreg.HKEY_CURRENT_USER}[hive_name]
        with winreg.OpenKey(hive, path) as k:
            v, _t = winreg.QueryValueEx(k, value)
            return v
    except Exception:
        return None


def _ver_tuple(s):
    """'156.0.4314.58' → (156, 0, 4314, 58)；认不出就是 (0,)"""
    try:
        return tuple(int(x) for x in re.findall(r'\d+', str(s))[:4]) or (0,)
    except Exception:
        return (0,)


# ══════════════════════════════════════════════════════════════════
# ① WebView2 运行时 —— 没它就是「窗口一闪/白屏/直接退出」，用户完全没法用
# ══════════════════════════════════════════════════════════════════

def webview2_version():
    """返回 WebView2 Runtime 版本字符串；没装返回 ''。

    注册表和文件系统**都查**：注册表项可能被清理工具删掉而文件还在（能跑），
    也可能注册表还在但目录被搬走（跑不了）。以文件为准、注册表兜底。
    """
    cands = []
    for hive, path in _WV2_KEYS:
        v = _reg_read(hive, path, 'pv')
        if v and not str(v).startswith('0.0.0'):
            cands.append(str(v))
    roots = [os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'),
             os.path.join(os.environ.get('LOCALAPPDATA') or '', 'Microsoft')]
    for r in roots:
        if not r:
            continue
        base = Path(r) / 'Microsoft' / 'EdgeWebView' / 'Application'
        # 用户级安装的实际路径是 %LOCALAPPDATA%\Microsoft\EdgeWebView\Application
        for base in (base, Path(r) / 'EdgeWebView' / 'Application'):
            try:
                if not base.is_dir():
                    continue
                for d in base.iterdir():
                    if (d / 'msedgewebview2.exe').is_file():
                        cands.append(d.name)
            except Exception:
                pass
    if not cands:
        return ''
    # 取最高版本；同一个 Runtime 可能被多处登记（64 位 + WOW6432）
    return max(cands, key=_ver_tuple)


# ══════════════════════════════════════════════════════════════════
# ② 显示器 / 缩放 —— 决定主窗会不会超出屏幕、快搜窗会不会被切
# ══════════════════════════════════════════════════════════════════

class _MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT),
                ('rcWork', wt.RECT), ('dwFlags', wt.DWORD),
                ('szDevice', wt.WCHAR * 32)]


def logical_screens():
    """每块屏幕的信息，尺寸换算成**逻辑像素**（CSS px）。

    为什么不用 GetDpiForMonitor：它对 Per-Monitor v2 进程返回的是「系统 DPI」，
    实测在 150% 的屏上也给 96，用了等于没量。GetScaleFactorForMonitor 是显示器
    级别接口，PMv2 下给出正确值（实测 150），所以缩放以它为准、失败再退回窗口 DPI。
    """
    u = _user32()
    if not u:
        return []
    shcore = None
    try:
        shcore = ctypes.WinDLL('shcore', use_last_error=True)
    except Exception:
        pass
    out = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(wt.RECT), ctypes.c_double)

    def _cb(hmon, _hdc, _rc, _data):
        try:
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(mi)
            if not u.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
                return 1
            scale = 100
            if shcore is not None:
                try:
                    sf = ctypes.c_int(0)
                    if shcore.GetScaleFactorForMonitor(
                            ctypes.c_void_p(hmon), ctypes.byref(sf)) == 0 and sf.value:
                        scale = int(sf.value)
                except Exception:
                    pass
            dpr = scale / 100.0
            mon = mi.rcMonitor
            work = mi.rcWork
            out.append({
                'name': mi.szDevice,
                'primary': bool(mi.dwFlags & 1),
                'phys_w': mon.right - mon.left, 'phys_h': mon.bottom - mon.top,
                'work_w': work.right - work.left, 'work_h': work.bottom - work.top,
                'left': work.left, 'top': work.top,
                'scale': scale, 'dpr': dpr,
                'logical_w': round((work.right - work.left) / dpr),
                'logical_h': round((work.bottom - work.top) / dpr),
                'logical_full_w': round((mon.right - mon.left) / dpr),
            })
        except Exception:
            pass
        return 1

    try:
        u.EnumDisplayMonitors(None, None, PROC(_cb), None)
    except Exception:
        pass
    if not out:
        # 任何一步失败都要给个能用的兜底，不能让调用方拿到空列表
        try:
            dpr = (u.GetDpiForSystem() or 96) / 96.0
            w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
            out.append({'name': 'fallback', 'primary': True, 'phys_w': w, 'phys_h': h,
                        'work_w': w, 'work_h': h, 'left': 0, 'top': 0,
                        'scale': int(dpr * 100), 'dpr': dpr,
                        'logical_w': round(w / dpr), 'logical_h': round(h / dpr),
                        'logical_full_w': round(w / dpr)})
        except Exception:
            pass
    return out


def _display_adapter():
    """显卡描述。虚拟机 / 远程桌面 / 无驱动时是「Microsoft Basic Display Adapter」，
    WebView2 在这种环境里最常见的表现是黑块、白块或整块不刷新。"""
    try:
        u = _user32()
        class DD(ctypes.Structure):
            _fields_ = [('cb', wt.DWORD), ('DeviceName', wt.WCHAR * 32),
                        ('DeviceString', wt.WCHAR * 128), ('StateFlags', wt.DWORD),
                        ('DeviceID', wt.WCHAR * 128), ('DeviceKey', wt.WCHAR * 128)]
        dd = DD()
        dd.cb = ctypes.sizeof(dd)
        if u.EnumDisplayDevicesW(None, 0, ctypes.byref(dd), 0):
            return dd.DeviceString.strip()
    except Exception:
        pass
    return ''


# ══════════════════════════════════════════════════════════════════
# ③ 数据目录 / 磁盘 / 长路径
# ══════════════════════════════════════════════════════════════════

def _dir_writable(p):
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / '.sb_env_probe'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        return True
    except Exception:
        return False


def _fallback_dir():
    return (Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData/Local'))
            / 'SecondBrain' / 'data')


def long_paths_enabled():
    v = _reg_read('HKLM', r'SYSTEM\CurrentControlSet\Control\FileSystem',
                  'LongPathsEnabled')
    return bool(v)


# ══════════════════════════════════════════════════════════════════
# ④ 端口 —— 8765 被别的程序占了就起不来
# ══════════════════════════════════════════════════════════════════

def port_state(port, data_dir=None):
    """'free' 空闲 / 'ours' 本用户的本应用 / 'other' 别的数据目录的实例 / 'busy' 别人占了

    区分 'other' 是有意义的：多用户会话（快速用户切换、远程桌面服务器）下另一个
    用户的实例也监听 8765，它同样是 second-brain，但里面是别人的索引。
    """
    import socket
    try:
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return 'free'
    except Exception:
        return 'free'
    try:
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:%d/api/ping' % port,
                                    timeout=0.8) as r:
            v = json.loads(r.read().decode('utf-8', 'replace'))
        if v.get('app') != 'second-brain':
            return 'busy'
        if data_dir:
            try:
                a = os.path.normcase(os.path.abspath(str(v.get('dir') or '')))
                b = os.path.normcase(os.path.abspath(str(data_dir)))
                if a != b:
                    return 'other'
            except Exception:
                pass
        return 'ours'
    except Exception:
        return 'busy'


def free_port(start=8765, tries=12):
    """第一个能用的端口；'ours' 也算能用（直接复用在跑的那个实例）。0 = 全占。"""
    for p in range(start, start + tries):
        if port_state(p) in ('free', 'ours'):
            return p
    return 0


# ══════════════════════════════════════════════════════════════════
# ⑤ 系统 / 权限 / 个性化
# ══════════════════════════════════════════════════════════════════

def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _mem_gb():
    try:
        class MS(ctypes.Structure):
            _fields_ = [('dwLength', wt.DWORD), ('dwMemoryLoad', wt.DWORD),
                        ('ullTotalPhys', ctypes.c_ulonglong),
                        ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong),
                        ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong),
                        ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        ms = MS()
        ms.dwLength = ctypes.sizeof(ms)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return round(ms.ullTotalPhys / (1024 ** 3), 1)
    except Exception:
        pass
    return 0.0


def _remote_session():
    try:
        return bool(_user32().GetSystemMetrics(0x1000))     # SM_REMOTESESSION
    except Exception:
        return False


def _high_contrast():
    try:
        u = _user32()

        class HC(ctypes.Structure):
            _fields_ = [('cbSize', wt.UINT), ('dwFlags', wt.DWORD),
                        ('lpszDefaultScheme', wt.LPWSTR)]
        hc = HC()
        hc.cbSize = ctypes.sizeof(hc)
        # SPI_GETHIGHCONTRAST = 0x0042，HIGHCONTRASTW 的第一个字段是 cbSize
        if u.SystemParametersInfoW(0x0042, ctypes.sizeof(hc), ctypes.byref(hc), 0):
            return bool(hc.dwFlags & 1)                     # HCF_HIGHCONTRASTON
    except Exception:
        pass
    return False


def _dark_theme():
    v = _reg_read('HKCU', r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
                  'AppsUseLightTheme')
    return v == 0


def _font_exists(name):
    """拿 EnumFontFamiliesExW 精确找一次字体族。找不到 → 界面会回落到别的字体，
    中文可能变成方块（常见于精简版系统删掉了「微软雅黑」）。"""
    try:
        u = _user32()
        gdi = ctypes.WinDLL('gdi32', use_last_error=True)

        class LF(ctypes.Structure):
            _fields_ = [('lfHeight', wt.LONG), ('lfWidth', wt.LONG),
                        ('lfEscapement', wt.LONG), ('lfOrientation', wt.LONG),
                        ('lfWeight', wt.LONG), ('lfItalic', wt.BYTE),
                        ('lfUnderline', wt.BYTE), ('lfStrikeOut', wt.BYTE),
                        ('lfCharSet', wt.BYTE), ('lfOutPrecision', wt.BYTE),
                        ('lfClipPrecision', wt.BYTE), ('lfQuality', wt.BYTE),
                        ('lfPitchAndFamily', wt.BYTE), ('lfFaceName', wt.WCHAR * 32)]
        hdc = u.GetDC(None)
        lf = LF()
        lf.lfCharSet = 1                                    # DEFAULT_CHARSET
        lf.lfFaceName = name
        found = [0]
        PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                  wt.DWORD, ctypes.c_void_p)

        def _cb(_h, _l, _t, _p):
            found[0] = 1
            return 0                                        # 返回 0 = 停止枚举
        gdi.EnumFontFamiliesExW(ctypes.c_void_p(hdc), ctypes.byref(lf), PROC(_cb), 0, 0)
        u.ReleaseDC(None, ctypes.c_void_p(hdc))
        return bool(found[0])
    except Exception:
        return False


def _db_quick_check(db_path):
    """只读打开做 quick_check —— 不建表、不写 WAL，绝不碰用户数据。

    timeout 给 0.6 秒而不是几秒：首次启动正在全盘扫描时库被写锁占着，
    「环境自检」不该跟着卡三秒 —— 读不到就老实说 busy，用户还能看别的项。
    """
    try:
        p = Path(db_path)
        if not p.is_file():
            return ('missing', 0)
        size = p.stat().st_size
        uri = 'file:%s?mode=ro' % p.as_posix().replace('?', '%3f').replace('#', '%23')
        con = sqlite3.connect(uri, uri=True, timeout=0.6)
        try:
            row = con.execute('PRAGMA quick_check(1)').fetchone()
        finally:
            con.close()
        return ('ok' if (row and row[0] == 'ok') else 'bad', size)
    except sqlite3.OperationalError as e:
        if 'locked' in str(e).lower() or 'busy' in str(e).lower():
            return ('busy', 0)
        return ('error:%s' % e.__class__.__name__, 0)
    except Exception as e:
        return ('error:%s' % e.__class__.__name__, 0)


# ══════════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════════

def probe_all(data_dir=None, db_path=None, port=8765, port_tries=12, light=False):
    """把能测的都测一遍，返回一个大 dict。任何一项失败都只影响它自己。

    light=True 时跳过 quick_check（1 GB 的库要一两秒）—— 启动流程用它，
    用户在界面里点「环境自检」才跑完整版。
    """
    info = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'pid': os.getpid(),
            'frozen': bool(getattr(sys, 'frozen', False))}

    info['webview2'] = webview2_version()

    try:
        info['python'] = sys.version.split()[0]
        info['arch'] = '%d-bit' % (ctypes.sizeof(ctypes.c_void_p) * 8)
        info['windows'] = '%d.%d.%d' % platform_release()
    except Exception:
        info['python'] = info['arch'] = info['windows'] = ''

    info['admin'] = _is_admin()
    info['mem_gb'] = _mem_gb()
    info['remote_session'] = _remote_session()
    info['adapter'] = _display_adapter()
    info['long_paths'] = long_paths_enabled()
    info['dark'] = _dark_theme()
    info['high_contrast'] = _high_contrast()
    info['font_yahei'] = _font_exists('Microsoft YaHei UI')
    try:
        # 不走 locale.getdefaultlocale（3.15 起移除）：直接问 Win32 要界面语言和代码页
        lang = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        info['locale'] = '%04X / cp%d' % (lang, ctypes.windll.kernel32.GetACP())
    except Exception:
        info['locale'] = ''

    info['screens'] = logical_screens()
    prim = next((s for s in info['screens'] if s['primary']),
                info['screens'][0] if info['screens'] else None)
    info['primary'] = prim

    # 数据目录：优先用调用方给的（app_window 里就是 brain.app_dir()），
    # 没给就按 brain 的规则猜一个 —— 这里不 import brain，避免循环依赖和重导入。
    if data_dir:
        d = Path(data_dir)
    else:
        base = (Path(sys.executable).parent if info['frozen'] else Path(__file__).parent)
        d = Path(os.environ.get('SB_DATA_DIR') or base)
    info['data_dir'] = str(d)
    info['data_writable'] = _dir_writable(d)
    info['data_ascii'] = all(ord(c) < 128 for c in str(d))
    info['data_unc'] = str(d).startswith('\\\\')
    # 同步盘：索引库是会一直被写的 sqlite 文件，放进同步目录会被反复上传，
    # 同步工具还会短暂锁住文件 —— 用户看到的是「database is locked」或索引莫名回退。
    _low = str(d).lower()
    info['data_sync'] = next((n for n in ('onedrive', 'dropbox', 'nutstore', '坚果云',
                                          'baidunetdisk', '百度网盘', 'googledrive',
                                          'icloud', 'seafile', '同步空间')
                              if n in _low), '')
    if not info['data_writable']:
        fb = _fallback_dir()
        info['fallback_dir'] = str(fb)
        info['fallback_writable'] = _dir_writable(fb)
    try:
        du = shutil.disk_usage(str(d if info['data_writable'] else Path(str(d).split(':')[0] + ':\\')))
        info['free_mb'] = int(du.free / (1024 * 1024))
        info['total_gb'] = round(du.total / (1024 ** 3), 1)
    except Exception:
        info['free_mb'] = -1
        info['total_gb'] = 0.0

    db = db_path or (Path(str(d)) / 'brain.db')
    if light:
        try:
            _p = Path(db)
            info['db_state'] = 'ok' if _p.is_file() and _p.stat().st_size > 0 else 'missing'
            info['db_mb'] = round(_p.stat().st_size / (1024 * 1024), 1) if _p.is_file() else 0.0
        except Exception:
            info['db_state'], info['db_mb'] = 'missing', 0.0
    else:
        st, size = _db_quick_check(db)
        info['db_state'] = st
        info['db_mb'] = round(size / (1024 * 1024), 1)
    info['db'] = str(db)

    states = {}
    for p in range(port, port + port_tries):
        states[p] = port_state(p, info['data_dir'])
    info['ports'] = states
    info['free_port'] = free_port(port, port_tries)
    info['svr_running'] = any(v == 'ours' for v in states.values())
    info['other_user_port'] = next((p for p, v in states.items() if v == 'other'), 0)
    return info


def platform_release():
    """os.sys.getwindowsversion() 在部分精简系统上不可用，单独兜一层。"""
    try:
        v = sys.getwindowsversion()
        return (v.major, v.minor, v.build)
    except Exception:
        return (0, 0, 0)


# ══════════════════════════════════════════════════════════════════
# 判定：把测量结果翻译成「行 / 要注意 / 不行」
# ══════════════════════════════════════════════════════════════════

# ── 体检文案（第 174 项）─────────────────────────────────────────
# 以前 judge() 把中文写死在 30 个 add() 里，界面切到英文后上半截英文、
# 下半截中文。这里把文案抽成表：中文留作默认（调用处传），英文走 _EN。
# 西/葡/印地先用英文兜底 —— WebView2 / SMB / quick_check 这些术语用英文反而更准。
_EN = {
    'wv_missing_t': 'WebView2 runtime is missing',
    'wv_missing_d': 'The window cannot open: you get a blank screen or it exits at once, '
                    'even though the service is already running',
    'wv_missing_f': 'Install it once (~2MB): %s',
    'wv_old_t': 'WebView2 runtime is old (%s)',
    'wv_old_d': 'Some newer APIs are missing; individual pages may render wrong',
    'wv_old_f': 'Update to the latest version: %s',
    'wv_ok_t': 'WebView2 runtime %s',

    'data_bad_t': 'Program folder is not writable',
    'data_bad_d': 'Data folder %s %s',
    'data_moved': 'was switched to %s',
    'data_nofb': 'and the fallback folder is not writable either',
    'data_bad_f': 'Move the program to a folder you can write to (e.g. D:/SecondBrain) '
                  'and start it again',
    'data_ok_t': 'Data folder is writable: %s',
    'unc_t': 'Data folder is on a network path (%s)',
    'unc_d': 'SQLite file locking over SMB is unreliable: the index may get corrupted '
             'or report "database is locked"',
    'unc_f': 'Put the program on a local disk (e.g. D:/SecondBrain) and start it again',
    'sync_t': 'Data folder is inside a sync folder (%s)',
    'sync_d': 'The index is over 1 GB and written constantly; sync tools re-upload it '
              'and briefly lock the files',
    'sync_f': 'Move the program out of the sync folder; back up settings.json separately',

    'disk_low_t': 'Not enough free disk space (%d MB)',
    'disk_low_d': 'Indexing needs to write; running out mid-way leaves a partial index',
    'disk_low_f': 'Free up at least 1 GB before indexing',
    'disk_some_t': 'Free disk space is low (%d MB)',
    'disk_ok_t': '%.1f GB free',

    'screen_t': 'Primary display %d×%d @%d%% (logical %d×%d)',
    'screen_small_t': 'Usable screen area is small',
    'screen_small_d': 'Logical %d×%d; the quick window and the main window shrink to fit',
    'screen_small_f': 'Nothing breaks, but windows will feel cramped',
    'multi_t': '%d displays detected',

    'remote_t': 'Running in a Remote Desktop session',
    'remote_d': 'WebView2 may show black blocks or stop refreshing over RDP',
    'remote_f': 'If it glitches, start with --disable-gpu or use a local display',
    'gpu_t': 'Graphics is using the basic display adapter (%s)',
    'gpu_d': 'No hardware acceleration; WebView2 may flicker or show black blocks',
    'gpu_f': 'Install the GPU driver, or start with --disable-gpu',

    'db_missing_t': 'No index yet (created on first launch)',
    'db_ok_t': 'Index is healthy (%.1f MB)',
    'db_busy_t': 'Index is being written; integrity check skipped',
    'db_busy_d': 'Normal during the first scan or a rebuild — run the check again later',
    'db_bad_t': 'Index is broken (%s)',
    'db_bad_d': 'On startup the bad file is archived and an empty index is created; '
                'you need to index once more',
    'db_bad_f': 'If this repeats, check disk health (smartctl / chkdsk)',

    'port_running_t': 'Another instance is running (port %d)',
    'port_free_t': 'Port %d is available',
    'port_full_t': 'Ports 8765–8776 are all in use',
    'port_full_d': 'The service cannot start and the UI cannot connect',
    'port_full_f': 'Close whatever holds these ports (netstat -ano | findstr 8765)',
    'port_other_t': 'Port %d belongs to an instance with a different data folder',
    'port_other_d': 'Left by another Windows session or another install; it is not reused '
                    '(this one takes the next free port)',

    'sys_t': '%s · %s · %s · %.1f GB RAM',
    'sys_admin': 'Administrator',
    'sys_user': 'standard user',
    'font_ok_t': 'Microsoft YaHei is available',
    'font_bad_t': 'Microsoft YaHei is missing',
    'font_bad_d': 'Chinese text may show as boxes',
    'font_bad_f': 'Install the "Microsoft YaHei" font (bundled with Windows; '
                  'your image had it stripped)',
    'longpath_t': 'Long path support is off',
    'longpath_d': 'Paths over 260 characters are skipped (the rest still index fine)',
    'longpath_f': 'If files are missing from the index, enable LongPathsEnabled and re-scan',
    'theme_t': 'System is in dark mode',
    'contrast_t': 'High contrast is on',
    'nonascii_t': 'Data folder contains non-ASCII characters',

    'rpt_title': 'Second Brain · environment check    %s',
    'rpt_plan': 'Layout: main %d×%d · quick %d wide (screen %d×%d)%s',
    'rpt_gpu': ' · hardware acceleration recommended off',
    'rpt_concl': 'Result: %s',
    'rpt_fail': '%d unavailable — must fix',
    'rpt_warn': '%d to look at, still usable',
    'rpt_ok': 'environment looks fine',
}


# 西/葡/印地语的文案在 env_msgs.py（第 176 项）。以前这里只有英文表、把三种语言
# 全指到英文，用户切到那三种语言时体检面板与「复制诊断」正文仍是英文 —— 看起来
# 就像「只有英语和中文」。模块缺失时退回英文，绝不因为翻译文件坏了让体检崩掉。
try:
    from env_msgs import TABLES as _TABLES
except Exception:                                   # pragma: no cover
    _TABLES = {}


def _use_en(lang):
    """非中文语言是否走英文表 —— 只用于 `render` 的旧调用兼容。"""
    return bool(lang) and not str(lang).lower().startswith('zh')


def _table(lang):
    """按语言取文案表：中文返回 {}（用调用处写的中文默认值）。"""
    l = str(lang or '').lower()
    if not l or l.startswith('zh'):
        return {}
    if l.startswith('es'):
        return _TABLES.get('es') or _EN
    if l.startswith('pt'):
        return _TABLES.get('pt') or _EN
    if l.startswith('hi'):
        return _TABLES.get('hi') or _EN
    return _EN


def judge(info, lang='zh'):
    """返回 [{level, key, title, detail, fix}]，level ∈ OK/INFO/WARN/FAIL。

    判定只依赖实测值。FAIL = 「这台机器上功能确实用不了」，WARN = 「能用但吃亏」，
    INFO = 「记录一下，出问题时有用」。
    """
    r = []
    T = _table(lang)

    def M(k, zh):
        return T.get(k) or zh

    def add(level, key, title, detail='', fix=''):
        r.append({'level': level, 'key': key, 'title': title,
                  'detail': detail, 'fix': fix})

    # ① WebView2 —— 没有它就没有界面
    wv = info.get('webview2') or ''
    if not wv:
        add('FAIL', 'webview2', M('wv_missing_t', '缺少 WebView2 运行时'),
            M('wv_missing_d', '窗口起不来会白屏或直接退出，服务其实已经跑起来了'),
            M('wv_missing_f', '装一次即可（约 2MB）：%s') % _WV2_URL)
    elif _ver_tuple(wv) < (100,):
        add('WARN', 'webview2', M('wv_old_t', 'WebView2 运行时偏旧（%s）') % wv,
            M('wv_old_d', '新版 API 缺失，个别页面可能显示异常'),
            M('wv_old_f', '建议升级到最新版：%s') % _WV2_URL)
    else:
        add('OK', 'webview2', M('wv_ok_t', 'WebView2 运行时 %s') % wv)

    # ② 数据目录可写 + 空间
    if not info.get('data_writable'):
        fb_ok = info.get('fallback_writable')
        add('WARN' if fb_ok else 'FAIL', 'data_dir',
            M('data_bad_t', '程序目录写不进去'),
            M('data_bad_d', '数据目录 %s %s')
            % (info.get('data_dir'),
               (M('data_moved', '已自动改到 %s') % info.get('fallback_dir')) if fb_ok
               else M('data_nofb', '回落目录也写不进去')),
            '' if fb_ok else M('data_bad_f',
                               '把程序换到有写权限的目录（如 D:/SecondBrain）再启动'))
    else:
        add('OK', 'data_dir',
            M('data_ok_t', '数据目录可写：%s') % info.get('data_dir'))
    if info.get('data_unc'):
        add('WARN', 'data_unc',
            M('unc_t', '数据目录在网络路径上（%s）') % info.get('data_dir'),
            M('unc_d', 'sqlite 在 SMB 共享上的文件锁不可靠，索引可能损坏或报 database is locked'),
            M('unc_f', '把程序放到本地磁盘（如 D:/SecondBrain）再启动'))
    if info.get('data_sync'):
        add('WARN', 'data_sync',
            M('sync_t', '数据目录在同步盘里（%s）') % info.get('data_sync'),
            M('sync_d', '索引库有一千多 MB 且持续写入，同步工具会反复上传并短暂锁文件'),
            M('sync_f', '把程序移出同步目录；要备份的话单独备份 settings.json 就够了'))
    fm = info.get('free_mb', -1)
    if fm >= 0:
        if fm < MIN_FREE_MB:
            add('FAIL', 'disk', M('disk_low_t', '磁盘剩余空间不足（%d MB）') % fm,
                M('disk_low_d', '建索引需要写入，空间不够会中途失败、索引残缺'),
                M('disk_low_f', '清理出至少 1 GB 再建索引'))
        elif fm < 1024:
            add('WARN', 'disk', M('disk_some_t', '磁盘剩余空间偏少（%d MB）') % fm)
        else:
            add('OK', 'disk', M('disk_ok_t', '磁盘剩余 %.1f GB') % (fm / 1024))

    # ③ 屏幕与缩放 —— 小屏 + 高缩放会把窗口顶出屏幕
    prim = info.get('primary')
    if prim:
        lw, lh = prim['logical_w'], prim['logical_h']
        add('OK' if lw >= 1000 else 'INFO', 'screen',
            M('screen_t', '主屏 %d×%d @%d%%（逻辑 %d×%d）')
            % (prim['phys_w'], prim['phys_h'], prim['scale'], lw, lh))
        if lw < MINI_DESIGN_W + 40 or lh < 560:
            add('WARN', 'screen_small', M('screen_small_t', '屏幕可用区域偏小'),
                M('screen_small_d', '逻辑 %d×%d，快搜窗与主窗口已按可用区收缩') % (lw, lh),
                M('screen_small_f', '功能不受影响，但窗口会比较紧凑'))
        if len(info.get('screens') or []) > 1:
            add('INFO', 'multi_monitor',
                M('multi_t', '检测到 %d 块屏幕') % len(info['screens']),
                '; '.join('%s %d×%d@%d%%' % (s['name'].strip(chr(92) + '.' + chr(92)), s['phys_w'],
                                             s['phys_h'], s['scale'])
                          for s in info['screens']))

    # ④ 显卡 / 远程桌面 —— WebView2 的黑块白块大半出在这里
    ad = (info.get('adapter') or '').lower()
    if info.get('remote_session'):
        add('WARN', 'remote', M('remote_t', '当前是远程桌面会话'),
            M('remote_d', 'WebView2 在远程桌面里可能黑块或不刷新'),
            M('remote_f', '如遇花屏，启动时加 --disable-gpu 或改用本机屏幕'))
    if any(k in ad for k in ('basic display', 'basic render', '标准 vga')):
        add('WARN', 'gpu',
            M('gpu_t', '显卡驱动是系统基础适配器（%s）') % info.get('adapter'),
            M('gpu_d', '没有硬件加速，WebView2 可能闪烁或黑块'),
            M('gpu_f', '装显卡驱动；或启动时加 --disable-gpu'))

    # ⑤ 数据库
    st = info.get('db_state')
    if st == 'missing':
        add('INFO', 'db', M('db_missing_t', '还没有索引库（首次启动会建）'))
    elif st == 'ok':
        add('OK', 'db', M('db_ok_t', '索引库完好（%.1f MB）') % info.get('db_mb', 0))
    elif st == 'busy':
        add('INFO', 'db', M('db_busy_t', '索引库正在被写入，跳过完整性检查'),
            M('db_busy_d', '首次扫描或重建索引时正常现象，稍后再自检一次即可'))
    else:
        add('FAIL', 'db', M('db_bad_t', '索引库异常（%s）') % st,
            M('db_bad_d', '启动时会自动留档坏库并重建空库，需要重新建一次索引'),
            M('db_bad_f', '若反复出现，检查磁盘健康（smartctl / chkdsk）'))

    # ⑥ 端口
    fp = info.get('free_port')
    if info.get('svr_running'):
        add('OK', 'port', M('port_running_t', '已有实例在运行（端口 %d）') % _ours_port(info))
    elif fp:
        add('OK', 'port', M('port_free_t', '可用端口 %d') % fp)
    else:
        add('FAIL', 'port', M('port_full_t', '8765–8776 全部被占用'),
            M('port_full_d', '服务起不来，界面连不上'),
            M('port_full_f', '关掉占用这些端口的程序（netstat -ano | findstr 8765）'))
    if info.get('other_user_port'):
        add('INFO', 'port_other',
            M('port_other_t', '端口 %d 上是**另一个数据目录**的实例') % info['other_user_port'],
            M('port_other_d', '多用户会话 / 另一份安装留下的，不会复用（会自己换下一个端口）'))

    # ⑦ 系统能力（只记录，不影响判定）
    role = M('sys_admin', '管理员') if info.get('admin') else M('sys_user', '普通用户')
    add('INFO', 'sys', M('sys_t', '%s · %s · %s · 内存 %.1f GB')
        % (info.get('windows', '?'), info.get('arch', '?'), role, info.get('mem_gb', 0)))
    if info.get('font_yahei'):
        add('OK', 'font', M('font_ok_t', '微软雅黑可用'))
    else:
        add('WARN', 'font', M('font_bad_t', '微软雅黑缺失'),
            M('font_bad_d', '中文可能显示成方块'),
            M('font_bad_f', '装一下「微软雅黑」字体（Windows 自带，被精简掉了）'))
    if not info.get('long_paths'):
        add('INFO', 'longpath', M('longpath_t', '系统未启用长路径支持'),
            M('longpath_d', '超过 260 字符的路径会被跳过（不影响其余文件）'),
            M('longpath_f', '如索引有遗漏，可开启 LongPathsEnabled 后重扫'))
    if info.get('dark'):
        add('INFO', 'theme', M('theme_t', '系统为深色模式'))
    if info.get('high_contrast'):
        add('INFO', 'contrast', M('contrast_t', '系统开启了高对比度'))
    if not info.get('data_ascii'):
        add('INFO', 'nonascii', M('nonascii_t', '数据目录含非 ASCII 字符'),
            info.get('data_dir', ''))
    return r

def _ours_port(info):
    for p, v in (info.get('ports') or {}).items():
        if v == 'ours':
            return p
    return 0


# ══════════════════════════════════════════════════════════════════
# 适配方案：把环境转成具体参数（窗口尺寸、渲染开关）
# ══════════════════════════════════════════════════════════════════

def plan(info):
    """返回给界面用的适配参数。**纯函数**，好测。

    这里的原则：窗口尺寸只由「可用区域」推，不按缩放档写 if/else —— 因为
    「哪一档缩放会出问题」这件事本身就在变，写分档等于把未来的机型排除在外。
    """
    prim = info.get('primary') or {}
    lw = prim.get('logical_w') or 1280
    lh = prim.get('logical_h') or 800
    if lw <= 0:
        lw = 1280
    if lh <= 0:
        lh = 800

    # 主窗：屏宽的 50%（1180–1400），高度 62%（720–900），但都必须塞进可用区
    mw = int(min(max(1180, lw * 0.50), 1400))
    mh = int(min(max(720, lh * 0.62), 900))
    mw = max(640, min(mw, int(lw * 0.94)))
    mh = max(420, min(mh, int(lh * 0.94)))

    # 快搜窗：设计宽 760，屏幕更窄就跟着窄（否则右边被切掉、文字被裁）
    mini_w = MINI_DESIGN_W if lw >= MINI_DESIGN_W + 40 else max(MINI_MIN_W, lw - 32)
    mini_w = min(mini_w, lw - 16) if lw > MINI_MIN_W + 16 else MINI_MIN_W

    # 渲染降级建议
    gpu_ok = not any(k in (info.get('adapter') or '').lower()
                     for k in ('basic display', 'basic render', '标准 vga'))
    return {
        'main_w': mw, 'main_h': mh,
        'mini_w': int(mini_w),
        'mini_x': max(0, int((lw - mini_w) // 2)),
        'mini_y': max(0, int(lh * 0.17)),
        'logical_w': lw, 'logical_h': lh,
        'disable_gpu': bool(info.get('remote_session')) or not gpu_ok,
        'port': info.get('free_port') or 0,
    }


# ══════════════════════════════════════════════════════════════════
# 输出
# ══════════════════════════════════════════════════════════════════

_ICON = {'OK': '[ OK ]', 'INFO': '[info]', 'WARN': '[ !! ]', 'FAIL': '[FAIL]'}


def render(info, issues=None, color=False, lang='zh'):
    """人类可读报告。用户能直接截图发过来 —— 这就是「把现场搬过来」。"""
    T = _table(lang)

    def M(k, zh):
        return T.get(k) or zh

    issues = issues if issues is not None else judge(info, lang)
    lines = []
    lines.append(M('rpt_title', '第二大脑 · 环境自检    %s') % info.get('ts', ''))
    lines.append('=' * 64)
    for it in issues:
        lines.append('%s %s' % (_ICON.get(it['level'], it['level']), it['title']))
        if it.get('detail'):
            lines.append('       %s' % it['detail'])
        if it.get('fix'):
            lines.append('       → %s' % it['fix'])
    p = plan(info)
    lines.append('-' * 64)
    lines.append(M('rpt_plan', '适配结果：主窗 %d×%d · 快搜窗 %d 宽（屏 %d×%d）%s')
                 % (p['main_w'], p['main_h'], p['mini_w'],
                    p['logical_w'], p['logical_h'],
                    M('rpt_gpu', ' · 建议关硬件加速') if p['disable_gpu'] else ''))
    n_fail = sum(1 for i in issues if i['level'] == 'FAIL')
    n_warn = sum(1 for i in issues if i['level'] == 'WARN')
    lines.append(M('rpt_concl', '结论：%s') % (
        M('rpt_fail', '%d 项不可用，必须先处理') % n_fail if n_fail
        else (M('rpt_warn', '%d 项需注意，功能可用') % n_warn if n_warn
              else M('rpt_ok', '本机环境正常'))))
    return '\n'.join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = '--json' in argv
    info = probe_all()
    issues = judge(info)
    if as_json:
        print(json.dumps({'info': info, 'issues': issues, 'plan': plan(info)},
                         ensure_ascii=False, indent=2))
    else:
        print(render(info, issues))
        try:
            out = Path(info['data_dir'] if info.get('data_writable') else _fallback_dir())
            out.mkdir(parents=True, exist_ok=True)
            (out / 'env_report.json').write_text(
                json.dumps({'info': info, 'issues': issues, 'plan': plan(info)},
                           ensure_ascii=False, indent=2), encoding='utf-8')
            print('\n报告已存：%s' % (out / 'env_report.json'))
        except Exception:
            pass
    return 1 if any(i['level'] == 'FAIL' for i in issues) else 0


if __name__ == '__main__':
    sys.exit(main())
