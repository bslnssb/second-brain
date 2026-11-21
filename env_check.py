#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memex · 运行环境自检与自适应（把「用户那边是什么样」变成可测量、可自愈的数据）

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
import socket
import sqlite3
import sys
import time
from pathlib import Path

__all__ = ['probe_all', 'judge', 'plan', 'render', 'main', 'snapshot',
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


def _db_header_check(db_path):
    """便宜的库状态检查：只确认文件头、页数、表清单和抽样行能读。

    为什么不在这里跑 quick_check：2.4GB 库实测 27.7–31s（冷启动 2 分钟），
    体检按钮按下去等半分钟是不能接受的。全库校验拆成独立动作
    （`full_verify()` + 界面上的「现在校验」），结果缓存在 verify_state.json。
    """
    try:
        p = Path(db_path)
        if not p.is_file():
            return ('missing', 0)
        size = p.stat().st_size
        uri = 'file:%s?mode=ro' % p.as_posix().replace('?', '%3f').replace('#', '%23')
        con = sqlite3.connect(uri, uri=True, timeout=0.6)
        try:
            pages = int(con.execute('PRAGMA page_count').fetchone()[0] or 0)
            tables = int(con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
                         .fetchone()[0] or 0)
            if pages <= 0 or tables <= 0:
                return ('bad', size)
            try:
                con.execute('SELECT rowid FROM docs ORDER BY rowid DESC LIMIT 3').fetchall()
            except sqlite3.OperationalError:
                pass          # 表名对不上不是错：老库/空库都可能没有 docs
        finally:
            con.close()
        return ('ok', size)
    except sqlite3.OperationalError as e:
        if 'locked' in str(e).lower() or 'busy' in str(e).lower():
            return ('busy', 0)
        return ('error:%s' % e.__class__.__name__, 0)
    except Exception as e:
        return ('error:%s' % e.__class__.__name__, 0)


VERIFY_STATE = 'verify_state.json'


def verify_state_path(data_dir):
    return Path(data_dir) / VERIFY_STATE


def read_verify_state(data_dir):
    """上次全库校验的结论。库文件在结论之后被写过，就标 stale。"""
    try:
        p = verify_state_path(data_dir)
        d = json.loads(p.read_text(encoding='utf-8'))
        if not isinstance(d, dict):
            return {}
        d['stale'] = False
        try:
            dbm = (Path(data_dir) / 'brain.db').stat().st_mtime
            d['stale'] = bool(dbm > float(d.get('ts') or 0))
        except Exception:
            pass
        return d
    except Exception:
        return {}


def full_verify(db_path, data_dir=None):
    """全库校验：整库 quick_check。慢（2.4GB 约 28–39s），只由用户显式触发。"""
    t0 = time.time()
    st, size = _db_quick_check(db_path)
    res = {'state': st, 'ok': st == 'ok',
           'elapsed': round(time.time() - t0, 1),
           'mb': round(size / (1024 * 1024), 1) if size else 0.0,
           'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'ts': time.time()}
    if data_dir:
        try:
            p = verify_state_path(data_dir)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + '.tmp')
            tmp.write_text(json.dumps(res, ensure_ascii=False), encoding='utf-8')
            os.replace(str(tmp), str(p))
        except Exception:
            pass
    return res


# ══════════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# 第 277 项：功能可用性 —— 前面 30 项回答「能不能启动」，这里回答「功能能不能用」
#
# 体检的价值不在「WebView2 装了没」，而在用户说「翻译坏了 / OCR 没反应 /
# 更新一直失败」时，能当场指出是哪一环断了。这里只测量，不修复，不发内容。
# ---------------------------------------------------------------------------

# 免费引擎的真实域名（与 translator.py 的 fetcher 对齐）。
_TRANS_HOSTS = {
    'google': ('clients5.google.com', 443),
    'mymemory': ('api.mymemory.translated.net', 443),
    'youdao': ('dict.youdao.com', 443),
    'baidu': ('fanyi.baidu.com', 443),
    'iciba': ('dict-mobile.iciba.com', 443),
}


def _tcp(host, port=443, timeout=1.2):
    """只做 TCP 握手：能连上不代表接口一定通，但连不上就一定用不了。"""
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True, int((time.time() - t0) * 1000)
    except Exception as e:
        return False, '%s' % str(e)[:60]


def _probe_translate(deep=False):
    """聚合翻译：逐引擎探活 + 内存里的冷却/连续失败状态。

    deep=False 时不发任何网络请求（启动时跑这个不该占带宽）；
    但冷却状态是内存里的，读它不花钱，所以常规体检也能报「Google 正在冷却」。
    """
    out = {'engines': [], 'ok_n': 0, 'n': 0, 'usable': True, 'byok': []}
    health = {}
    try:
        import translator
        health = {x['id']: x for x in translator.engine_health()}
        out['byok'] = [x['id'] for x in translator.engine_health() if x.get('byok')]
    except Exception:
        pass
    rows = []
    if deep:
        # 五个引擎**并发**握手：串行时每个 1.2s 超时，全都不通就要 6s，
        # 体感像体检卡住（实测深度体检最慢一次 27.5s）。并发后最差 ~1.5s。
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=max(1, len(_TRANS_HOSTS)),
                                    thread_name_prefix='envtr') as _ex:
                _futs = {eid: _ex.submit(_tcp, host, port)
                         for eid, (host, port) in _TRANS_HOSTS.items()}
                _probes = {}
                for eid, f in _futs.items():
                    try:
                        _probes[eid] = f.result(timeout=3)
                    except Exception as e:
                        _probes[eid] = (False, e)
        except Exception:
            _probes = {eid: _tcp(host, port) for eid, (host, port) in _TRANS_HOSTS.items()}
    else:
        _probes = {}
    for eid, (host, port) in _TRANS_HOSTS.items():
        row = {'id': eid, 'ok': None, 'ms': 0, 'cool_s': 0, 'fail_n': 0}
        if eid in health:
            row['cool_s'] = int(health[eid].get('cool_s') or 0)
            row['fail_n'] = int(health[eid].get('fail_n') or 0)
        if deep:
            ok, info = _probes.get(eid, (False, 'no probe'))
            row['ok'] = bool(ok)
            row['ms'] = info if ok else 0
            if not ok:
                row['err'] = str(info)[:60]
        rows.append(row)
    out['engines'] = rows
    out['n'] = len(out['engines'])
    if deep:
        out['ok_n'] = sum(1 for x in out['engines'] if x['ok'])
        out['usable'] = out['ok_n'] > 0
    # 自带密钥的引擎不探活（没配时探它没有意义），只回报配置状态
    for eid in ('deepseek', 'deepl', 'baidu_api'):
        h = health.get(eid) or {}
        out['engines'].append({'id': eid, 'ok': None, 'byok': True,
                               'ready': bool(h.get('ready')),
                               'cool_s': int(h.get('cool_s') or 0)})
    # 最近 24 小时成功率（本机计数，没有就空）
    try:
        import feature_metrics
        st = feature_metrics.feature_stats('translate_engine')
        out['h24'] = st.get('h24') if st else {}
    except Exception:
        pass
    return out


def _probe_ocr(deep=False):
    """离线 OCR：onnxruntime / rapidocr 能否 import、模型文件在不在、真跑一张耗时。"""
    out = {'import': False, 'rapid': False, 'model_n': 0, 'ok': False, 'run_ms': 0}
    try:
        import onnxruntime  # noqa: F401
        out['import'] = True
    except Exception as e:
        out['err'] = 'onnxruntime: %s' % str(e)[:70]
        return out
    try:
        import rapidocr  # 第 380 项：引擎换成 RapidOCR 3.x（PP-OCRv6 小模型）
        out['rapid'] = True
        pkg = Path(rapidocr.__file__).parent
        out['model_n'] = sum(1 for _ in pkg.rglob('*.onnx'))
        try:
            out['ver'] = __import__('ocr_engine').engine_info().get('version')
        except Exception:
            pass
    except Exception as e:
        out['err'] = 'rapidocr: %s' % str(e)[:70]
        return out
    if not deep:
        out['ok'] = out['model_n'] > 0
        return out
    try:
        from PIL import Image
        t0 = time.time()
        img = Image.new('RGB', (320, 120), 'white')
        import snip_ocr
        res = snip_ocr.ocr_image(img)
        out['run_ms'] = int((time.time() - t0) * 1000)
        out['ok'] = True
        out['lines'] = int(res.get('count') or 0)
    except Exception as e:
        out['ok'] = False
        out['err'] = 'run: %s' % str(e)[:70]
    return out


def _probe_index_health(db_path, deep=False):
    """索引库的真实规模与「有没有悄悄变形」。

    quick_check 只回答「文件坏了没」，不回答「FTS 和 meta 对不对得上」。
    这里多取几个数：文档数 / 文件名数 / WAL 大小 / 空闲页占比。
    deep=True 才扫 body 列（2.5 GB 的库要好几秒，轻量体检不该付这笔钱）。
    """
    out = {'ok': False}
    p = Path(str(db_path or ''))
    if not p.is_file():
        out['state'] = 'missing'
        return out
    try:
        out['db_mb'] = round(p.stat().st_size / (1024 * 1024), 1)
        wal = p.with_name(p.name + '-wal')
        out['wal_mb'] = round(wal.stat().st_size / (1024 * 1024), 1) if wal.is_file() else 0.0
        con = sqlite3.connect('file:%s?mode=ro' % p.as_posix(), uri=True, timeout=2)
        try:
            out['docs'] = int(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0])
        except Exception:
            out['docs'] = -1
        try:
            out['files'] = int(con.execute('SELECT COUNT(*) FROM files').fetchone()[0])
        except Exception:
            out['files'] = -1
        try:
            pc = con.execute('PRAGMA page_count').fetchone()[0]
            fl = con.execute('PRAGMA freelist_count').fetchone()[0]
            out['free_pct'] = int(fl * 100 / pc) if pc else 0
        except Exception:
            out['free_pct'] = -1
        try:
            rows = con.execute(
                "SELECT COALESCE(reason,'?'), COUNT(*) FROM meta "
                "WHERE reason IS NOT NULL AND reason != '' GROUP BY reason "
                "ORDER BY 2 DESC LIMIT 3").fetchall()
            out['why'] = [{'reason': str(r[0])[:24], 'n': int(r[1])} for r in rows]
        except Exception:
            out['why'] = []
        try:
            if deep:
                out['fts'] = int(con.execute(
                    "SELECT COUNT(*) FROM docs WHERE body IS NOT NULL AND body != ''").fetchone()[0])
                out['meta_n'] = int(con.execute('SELECT COUNT(*) FROM meta').fetchone()[0])
                ts = con.execute('SELECT MAX(indexed_at) FROM meta').fetchone()[0]
                out['last_indexed'] = (time.strftime('%m-%d %H:%M', time.localtime(float(ts)))
                                       if ts else '')
        except Exception:
            out['fts'] = -1
        try:
            out['db_mtime'] = time.strftime('%m-%d %H:%M', time.localtime(p.stat().st_mtime))
        except Exception:
            out['db_mtime'] = ''
        con.close()
        out['ok'] = out['docs'] >= 0
    except Exception as e:
        out['state'] = 'error'
        out['err'] = '%s: %s' % (type(e).__name__, str(e)[:70])
    return out


def _probe_clipboard(deep=False):
    """剪贴板监听：能不能拿到剪贴板（被别的程序长期占用时复制不进历史）。"""
    out = {'open': None, 'history': -1}
    try:
        import clipboard_mgr
        try:
            out['history'] = int(len(clipboard_mgr.list_history(limit=1).get('items') or []))
        except Exception:
            out['history'] = -1
    except Exception:
        return out
    if not deep:
        return out
    try:
        import win32clipboard as clip
    except Exception as e:
        out['err'] = 'win32clipboard: %s' % str(e)[:60]
        return out
    opened = False
    for _ in range(10):
        try:
            clip.OpenClipboard()
            opened = True
            break
        except Exception:
            time.sleep(0.03)
    if opened:
        try:
            n = 0
            f = clip.EnumClipboardFormats(0)
            while f and n < 64:
                n += 1
                f = clip.EnumClipboardFormats(f)
            out['formats'] = n
        except Exception as e:
            out['enum_err'] = str(e)[:60]
        finally:
            try:
                clip.CloseClipboard()
            except Exception:
                pass
    out['open'] = opened
    if not opened:
        # 打不开就查出是谁占着（1418 那次就是被常驻的剪贴板工具长期占着）
        try:
            u32 = ctypes.windll.user32
            u32.GetOpenClipboardWindow.restype = ctypes.c_void_p
            u32.GetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
            hwnd = u32.GetOpenClipboardWindow()
            if hwnd:
                out['owner_hwnd'] = int(hwnd)
                pid = ctypes.c_ulong(0)
                u32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
                out['owner_pid'] = int(pid.value)
                buf = ctypes.create_unicode_buffer(64)
                u32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 64)
                out['owner'] = (buf.value or '')[:32]
        except Exception:
            pass
    return out


def _probe_hotkey(deep=False):
    """全局热键：低级键盘钩子能否装上（被杀软拦、被别的程序抢先都会静默失败）。"""
    out = {'ok': None, 'mode': ''}
    try:
        import hotkey as _hkmod
    except Exception as e:
        out['err'] = str(e)[:60]
        return out
    out['mode'] = 'double_ctrl'
    try:
        out['dblclick_ms'] = int(ctypes.windll.user32.GetDoubleClickTime())
    except Exception:
        out['dblclick_ms'] = 0
    if not deep:
        return out
    t0 = time.time()
    try:
        hk = _hkmod.GlobalHotkey(lambda: None)
        ok = hk.start() is not None
        if ok:
            hk.stop()
        out['ok'] = bool(ok)
    except Exception as e:
        out['ok'] = False
        out['err'] = '%s: %s' % (type(e).__name__, str(e)[:60])
    out['ms'] = int((time.time() - t0) * 1000)
    return out


def _probe_update(deep=False):
    """更新通道：直链与 GitHub API 的可达性（国内用户最常见的「更不了」）。

    第 278 项补：用户从没配过 update_sources.json 时 urls 恒为 0 —— 那是「没配」，
    不是「不可达」，光看它这条体检等于不存在。所以 deep 时真去打一次**内置通道**
    （镜像里的 version.json + GitHub releases API），那才是用户点「检查更新」走的路。
    """
    out = {'ok': None, 'urls': 0, 'reachable': 0}
    try:
        import brain
        urls, page = brain.update_sources()
    except Exception as e:
        out['err'] = str(e)[:60]
        return out
    out['urls'] = len(urls or [])
    out['page'] = str(page or '')[:80]
    if not deep:
        return out
    import urllib.request
    for u in (urls or [])[:2]:
        try:
            req = urllib.request.Request(u, method='HEAD')
            import netproxy   # 第 299 项：体检的联网探测也要吃代理设置
            with netproxy.urlopen(req, timeout=4) as resp:
                out['reachable'] += 1 if 200 <= resp.status < 400 else 0
        except Exception as e:
            out.setdefault('errors', []).append(str(e)[:50])
    out['ok'] = (out['reachable'] > 0) if out['urls'] else None
    try:
        import brain as _b
        _i = _b.check_update(timeout=4) or {}
        out['builtin'] = bool(_i.get('ok'))
        out['cur'] = str(_i.get('cur') or '')
        out['latest'] = str(_i.get('latest') or '')
        if not _i.get('ok'):
            out.setdefault('errors', []).append(str(_i.get('err') or '')[:50])
    except Exception as e:
        out['builtin'] = False
        out.setdefault('errors', []).append('%s: %s' % (type(e).__name__, str(e)[:40]))
    return out


def _probe_license():
    """授权：解析结果、剩余天数、机器码是否变、时钟回拨提示。"""
    out = {'state': 'unknown'}
    try:
        import brain
        st = brain.license_state()
        out.update({'state': 'ok' if st.get('valid') else (st.get('reason') or 'free'),
                    'edition': st.get('edition') or 'free',
                    'days_left': st.get('days_left'),
                    'clock_warn': bool(st.get('clock_warn')),
                    'exp': st.get('exp') or ''})
    except Exception as e:
        out['err'] = str(e)[:60]
    return out


def _probe_autostart():
    """开机自启：注册表 Run 项是否指向一个还存在的 exe（换盘/移动目录后常见）。"""
    out = {'cmd': '', 'exists': None}
    for name in ('SecondBrain', 'Memex'):
        v = _reg_read('HKCU', r'Software\Microsoft\Windows\CurrentVersion\Run', name)
        if v:
            out['cmd'] = str(v)[:160]
            m = re.search(r'"([^"]+\.exe)"|^([^\s]+\.exe)', str(v), re.I)
            path = (m.group(1) or m.group(2)) if m else ''
            out['exists'] = bool(path) and Path(path).is_file()
            out['name'] = name
            break
    return out


def _probe_resources(deep=False):
    """进程资源：线程数、句柄数、GDI / User 对象、内存（标题栏拖拽 + 悬浮球最容易泄漏）。

    第 278 项修的真 bug：上一版不声明 restype，64 位下句柄被压成 32 位 int，
    GetGuiResources / GetProcessHandleCount 拿不到有效句柄就回 0 —— 于是体检里
    GDI/User 恒为 0、这条判断等于关着。这里显式声明签名，并用进程内存计数器补上
    「内存涨没涨」（泄漏最先体现在工作集/峰值差）。
    """
    out = {'threads': 0, 'handles': 0, 'gdi': 0, 'user': 0, 'mem_mb': 0, 'peak_mb': 0}
    try:
        import threading
        out['threads'] = threading.active_count()
    except Exception:
        pass
    try:
        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        u32.GetGuiResources.restype = ctypes.c_ulong
        u32.GetGuiResources.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        k32.GetProcessHandleCount.restype = ctypes.c_int
        k32.GetProcessHandleCount.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        k32.CloseHandle.restype = ctypes.c_int
        k32.CloseHandle.argtypes = (ctypes.c_void_p,)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x0400, False, os.getpid())   # PROCESS_QUERY_INFORMATION
        pseudo = False
        if not h:
            h = k32.GetCurrentProcess()
            pseudo = True
        try:
            n = ctypes.c_ulong(0)
            if k32.GetProcessHandleCount(h, ctypes.byref(n)):
                out['handles'] = int(n.value)
            out['gdi'] = int(u32.GetGuiResources(h, 0))
            out['user'] = int(u32.GetGuiResources(h, 1))

            class _PMC(ctypes.Structure):
                _fields_ = [('cb', ctypes.c_ulong), ('page_faults', ctypes.c_ulong),
                            ('peak_ws', ctypes.c_size_t), ('ws', ctypes.c_size_t),
                            ('quota_peak_paged', ctypes.c_size_t), ('quota_paged', ctypes.c_size_t),
                            ('quota_peak_nonpaged', ctypes.c_size_t), ('quota_nonpaged', ctypes.c_size_t),
                            ('pagefile', ctypes.c_size_t), ('peak_pagefile', ctypes.c_size_t)]
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                out['mem_mb'] = int(pmc.ws / 1048576)
                out['peak_mb'] = int(pmc.peak_ws / 1048576)
        finally:
            if not pseudo:
                try:
                    k32.CloseHandle(h)
                except Exception:
                    pass
    except Exception as e:
        out['err'] = '%s: %s' % (type(e).__name__, str(e)[:50])
    return out


def _probe_capture(deep=False):
    """截图 OCR 的前置条件：屏幕捕获能不能拿到非黑图（远程桌面/受保护内容常见）。"""
    out = {'ok': None}
    if not deep:
        return out
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        small = img.resize((16, 16)).convert('L')
        px = list(small.getdata())
        out['mean'] = int(sum(px) / len(px))
        out['ok'] = out['mean'] > 4
    except Exception as e:
        out['ok'] = False
        out['err'] = '%s: %s' % (type(e).__name__, str(e)[:60])
    return out


_SYNC_NAMES = ('onedrive', 'dropbox', 'nutstore', '坚果云', 'baidunetdisk', '百度网盘',
               'googledrive', 'icloud', 'pikpak', 'quark', 'aliyundrive', '阿里云盘')


def _dir_writable(p):
    """真写一次再删 —— os.access 在 Windows 上经常骗人（只读目录也回 True）。"""
    try:
        p = Path(str(p))
        if not p.is_dir():
            return False
        probe = p / ('.sb_env_%d.tmp' % int(time.time() * 1000 % 1e9))
        probe.write_text('x', encoding='utf-8')
        probe.unlink()
        return True
    except Exception:
        return False


def _probe_libsets(data_dir, deep=False):
    """多索引集：每个库的 db 是否完好、目录是否真可写、是否落在同步盘、小库顺手 quick_check。

    quick_check 只在库小于 300 MB 时跑：2.5 GB 的库实测要 45 秒，
    之前整块「轻量体检」被它拖垮过，这里绝不重犯（大库的完整性交给深检的 db 那一项）。
    """
    out = {'n': 0, 'sets': []}
    try:
        import libsets
        d = libsets.load(Path(data_dir))
        libs = d.get('libs') or []
        out['n'] = len(libs)
        out['active'] = d.get('active')
        data_dir = Path(str(data_dir))
        out['writable'] = _dir_writable(data_dir)
        out['sync'] = next((n for n in _SYNC_NAMES if n in str(data_dir).lower()), '')
        for lb in libs[:6]:
            p = data_dir / ('brain-%s.db' % lb['id']) if lb['id'] != 'default' \
                else data_dir / 'brain.db'
            item = {'id': lb['id'], 'name': str(lb.get('name'))[:24],
                    'db': bool(p.is_file()),
                    'mb': round(p.stat().st_size / (1024 * 1024), 1) if p.is_file() else 0.0}
            if item['db'] and deep and item['mb'] <= 300:
                try:
                    con = sqlite3.connect('file:%s?mode=ro' % p.as_posix(), uri=True, timeout=2)
                    try:
                        r = con.execute('PRAGMA quick_check').fetchone()
                        item['quick'] = str(r[0])[:20] if r else '?'
                    finally:
                        con.close()
                except Exception as e:
                    item['quick'] = '%s: %s' % (type(e).__name__, str(e)[:30])
            out['sets'].append(item)
    except Exception as e:
        out['err'] = str(e)[:60]
    return out


def _probe_reporter(data_dir):
    """报错组件自身健康：待发积压、报告数、日志体积、接收端是否配置（只报布尔）。"""
    out = {'reports': 0, 'debug_mb': 0, 'profile_mb': 0, 'ingest': False, 'backoff_s': 0}
    try:
        d = Path(str(data_dir))
        q = d / 'error_reports'
        out['reports'] = len(list(q.glob('*.json'))) if q.is_dir() else 0
        lg = d / 'debug.log'
        out['debug_mb'] = round(lg.stat().st_size / (1024 * 1024), 1) if lg.is_file() else 0.0
        prof = d / 'webview-profile'
        if prof.is_dir():
            total = 0
            for f in prof.rglob('*'):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    pass
            out['profile_mb'] = round(total / (1024 * 1024), 1)
        import error_report
        conf = error_report.load_conf()
        out['ingest'] = bool(error_report.endpoint())
        out['alert'] = bool(conf['auto'])
        out['send_fail'] = conf.get('send_fail', 0)
        out['backoff_s'] = max(0, int(conf.get('next_try', 0.0) - time.time()))
    except Exception as e:
        out['err'] = str(e)[:60]
    return out


def probe_all(data_dir=None, db_path=None, port=8765, port_tries=12, light=False,
              deep=False):
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
    elif deep:
        # 深度体检不再跑 quick_check（2.4GB 实测 28–39s，冷启 2 分钟）：
        # 只做便宜的 header 检查，全库校验单独触发，结论从 verify_state.json 读。
        st, size = _db_header_check(db)
        info['db_state'] = st
        info['db_mb'] = round(size / (1024 * 1024), 1) if size else 0.0
    else:
        st, size = _db_quick_check(db)
        info['db_state'] = st
        info['db_mb'] = round(size / (1024 * 1024), 1)
    info['db'] = str(db)
    try:
        info['db_full'] = read_verify_state(str(d))
    except Exception:
        info['db_full'] = {}

    states = {}
    for p in range(port, port + port_tries):
        states[p] = port_state(p, info['data_dir'])
    info['ports'] = states
    info['free_port'] = free_port(port, port_tries)
    info['svr_running'] = any(v == 'ours' for v in states.values())
    info['other_user_port'] = next((p for p, v in states.items() if v == 'other'), 0)

    # 第 277 项：功能级探测。常规体检只读本机状态（不联网、不加载模型）；
    # deep=True 才真的握手 / 加载 OCR / 跑截图 —— 界面上的「深度体检」按钮。
    #
    # 第 281 项（追加）：引擎探活 7.6s、更新通道 5s、OCR 首次加载 1.5–11s 是三条
    # 互不相干的活，串着跑等于把三者的等待相加。深检时这三项并发，其余照旧。
    _probes = [
        ('translate', lambda: _probe_translate(deep)),
        ('ocr', lambda: _probe_ocr(deep)),
        ('index_health', lambda: _probe_index_health(db, deep)),
        ('clipboard', lambda: _probe_clipboard(deep)),
        ('hotkey', lambda: _probe_hotkey(deep)),
        ('update', lambda: _probe_update(deep)),
        ('license', _probe_license),
        ('autostart', _probe_autostart),
        ('resources', _probe_resources),
        ('capture', lambda: _probe_capture(deep)),
        ('libsets', lambda: _probe_libsets(info['data_dir'], deep)),
        ('reporter', lambda: _probe_reporter(info['data_dir'])),
    ]
    _slow = ('translate', 'ocr', 'update')

    def _run(key, fn):
        try:
            return fn()
        except Exception as e:                       # 一项炸了不能拖垮整份报告
            return {'err': '%s: %s' % (type(e).__name__, str(e)[:60])}

    if deep:
        _pool = {}
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix='envprobe') as ex:
                for key, fn in _probes:
                    if key in _slow:
                        _pool[key] = ex.submit(_run, key, fn)
                for key, fn in _probes:
                    if key not in _slow:
                        info[key] = _run(key, fn)
            for key in _slow:
                info[key] = _pool[key].result()
        except Exception:
            for key in _slow:
                info[key] = _pool[key].result() if key in _pool else _run(key, dict(_probes)[key])
    else:
        for key, fn in _probes:
            info[key] = _run(key, fn)
    # 第 288 项：留一份最近快照 —— 错误报告要用它，但不能让崩溃现场等 3 秒重测一遍。
    _remember(info, bool(deep))
    return info


_SNAP = {'t': 0.0, 'info': None, 'deep': False}
_SNAP_LOCK = None


def _remember(info, deep):
    global _SNAP_LOCK
    try:
        if _SNAP_LOCK is None:
            import threading
            _SNAP_LOCK = threading.Lock()
        with _SNAP_LOCK:
            _SNAP['t'] = time.time()
            _SNAP['info'] = info
            _SNAP['deep'] = bool(deep)
    except Exception:
        pass


def snapshot(max_age=None):
    """最近一次 probe_all 的结论：(info, age_seconds)。

    没有快照就返回 (None, None)；给了 max_age 且快照太旧也返回 (None, age)，
    让调用方自己决定是「用旧的」还是「先跳过」。**本函数永远不跑探测**。
    """
    try:
        if not _SNAP.get('info'):
            return None, None
        age = max(0.0, time.time() - float(_SNAP['t']))
        if max_age is not None and age > max_age:
            return None, age
        return _SNAP['info'], age
    except Exception:
        return None, None


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

    'vf_none_t': 'Full verification: not run yet',
    'vf_none_d': 'Press "Verify now" below; it takes about 30 s to 2 min on a large library',
    'vf_ok_t': 'Full verification: passed %s (%.0f s, %.0f MB)',
    'vf_ok_d': 'Every page checked',
    'vf_stale': ' (library changed since; treat it as a reference)',
    'vf_bad_t': 'Full verification: problems found (%s)',
    'vf_bad_d': 'The index has bad pages: search may miss results or error out',
    'vf_bad_f': 'Rebuild the index in settings; if it repeats, check disk health',
    'vf_busy_t': 'Full verification did not finish: the library was being written',

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

    'rpt_title': 'Memex · environment check    %s',
    'rpt_plan': 'Layout: main %d×%d · quick %d wide (screen %d×%d)%s',
    'rpt_gpu': ' · hardware acceleration recommended off',
    'rpt_concl': 'Result: %s',
    'rpt_fail': '%d unavailable — must fix',
    'rpt_warn': '%d to look at, still usable',
    'rpt_ok': 'environment looks fine',

    # 第 277 项：功能可用性（只补标题/说明，其余语言缺键会回落到这里）
    'tr_none_t': 'Translation engines unreachable (0/%d)',
    'tr_none_d': 'All free engines failed the TCP handshake — usually a local proxy / TLS filter',
    'tr_none_f': 'Allow translate.googleapis.com and friends to connect directly, then re-run deep check',
    'tr_some_t': 'Translation %d/%d engines reachable',
    'tr_some_d': 'Unreachable: %s',
    'tr_some_f': 'The other engines still translate; if only one keeps working, switch proxy node',
    'tr_ok_t': 'Translation %d/%d engines reachable',
    'tr_cool_t': 'Rate-limit cooldown: %s',
    'tr_cool_d': 'These engines just returned a rate-limit error and are skipped until cooldown ends',
    'tr_h24_t': 'Translation 24h failure rate %.0f%% (%d/%d)',
    'tr_h24_d': 'Includes timeouts, empty results and echoed source text; counted locally',
    'ocr_noimp_t': 'OCR engine failed to load',
    'ocr_noimp_f': 'Install is incomplete or blocked by security software; reinstall once',
    'ocr_norapid_t': 'OCR model package missing',
    'ocr_nomodel_t': 'No OCR model files found',
    'ocr_nomodel_d': 'First recognition will try to download models; offline machines will fail',
    'ocr_run_t': 'OCR test run failed',
    'ocr_ok_t': 'Offline OCR ready (%d models, test run %d ms)',
    'idx_err_t': 'Index database unreadable',
    'idx_t': 'Index: %s docs / %s files / WAL %.1f MB / free pages %d%%',
    'idx_wal_t': 'WAL has grown to %.0f MB',
    'idx_wal_d': 'Normal during indexing; staying this large means it never checkpointed',
    'idx_wal_f': 'Restart once to force a checkpoint; if it recurs, check for a process writing continuously',
    'idx_free_t': 'Index has %d%% free pages',
    'idx_free_d': 'Holes left by deletes/rebuilds; a rebuild reclaims them',
    'idx_last_t': 'Last indexed %s (index file written %s)',
    'idx_last_d': 'Not updating for long means live indexing stopped (moved folders / permissions)',
    'idx_fts_t': 'Body text does not match the doc count (docs %s / with body %s)',
    'idx_fts_d': 'Some docs only got title/metadata and no body; search cannot reach them',
    'idx_fts_f': 'Rebuild the index once in settings to fill them in',
    'cb_busy_t': 'Clipboard is held by another program',
    'cb_busy_d': 'Ten attempts failed — copies may not reach history',
    'cb_owner_d': '%s is holding the clipboard (pid %s)',
    'cb_busy_f': 'Close clipboard tools / remote-control apps and retry',
    'cb_ok_t': 'Clipboard readable (%d formats)',
    'hk_fail_t': 'Global hotkey could not install',
    'hk_fail_f': 'Usually blocked by security software; switch trigger in settings',
    'hk_ok_t': 'Global hotkey works (installed in %d ms)',
    'up_fail_t': 'Update URLs unreachable (0/%d)',
    'up_fail_f': 'Check network/proxy, or put a direct link in update_sources.json',
    'up_ok_t': 'Update URLs reachable (%d/%d)',
    'up_live_t': 'Update channel reachable (current %s, online %s)',
    'up_live_f_t': 'Update channel unreachable (mirrors and GitHub both fail)',
    'up_urls_d': '%d direct links configured, %d reachable',
    'lic_ok_t': 'License valid (%s, %s days left)',
    'lic_free_t': 'Not licensed (free edition)',
    'lic_free_d': 'Import a license in settings for Pro; nothing else is affected',
    'lic_bad_t': 'License unusable (%s)',
    'lic_bad_d': 'Hardware change or clock rollback both cause this',
    'clock_t': 'System clock is earlier than last run',
    'clock_d': 'Dual boot / timezone / VM; informational only',
    'auto_bad_t': 'Autostart points to a missing exe',
    'auto_bad_f': 'Toggle autostart off and on in settings to rewrite the path',
    'auto_ok_t': 'Autostart configured',
    'res_t': 'Threads %s / handles %s / GDI %s / User %s / memory %s MB (peak %s MB)',
    'res_d': 'Steadily growing GDI/User counts indicate a drawing-object leak (titlebar drag, '
             'floating ball); a peak far above the usual also points at a leak',
    'cap_black_t': 'Screen capture returns a black frame',
    'cap_black_f': 'Remote desktop / protected content; OCR cannot capture here',
    'lib_t': 'Index sets: %d (active %s)',
    'lib_missing': 'Missing DB: %s',
    'lib_bad_d': 'Problems: %s',
    'lib_nowrite': 'folder not writable',
    'lib_syncd': 'inside a sync folder (%s)',
    'rep_t': 'Reporter: %d local reports / %.1f MB log / %.1f MB cache',
    'rep_backoff': 'Send backoff %ds (%d failures so far)',
    'rep_nosend': 'No endpoint configured; reports stay local',
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

    # ⑤-b 全库校验的最近一次结论（第 281 项追加：全库校验已从体检里拆出去）
    _vf = info.get('db_full') or {}
    if isinstance(_vf, dict) and _vf.get('at'):
        _stale = M('vf_stale', '（之后库有写入，结论仅供参考）') if _vf.get('stale') else ''
        if _vf.get('state') == 'busy':
            add('INFO', 'verify', M('vf_busy_t', '全库校验没跑完：库正被写入'))
        elif _vf.get('ok'):
            add('OK', 'verify', M('vf_ok_t', '全库校验：%s 通过（%.0f 秒，%.0f MB）')
                % (_vf.get('at', ''), float(_vf.get('elapsed') or 0), float(_vf.get('mb') or 0)),
                M('vf_ok_d', '整库逐页校验通过') + _stale)
        else:
            add('WARN', 'verify', M('vf_bad_t', '全库校验：发现问题（%s）') % _vf.get('state'),
                M('vf_bad_d', '索引库里有坏页，搜索可能漏结果或直接报错') + _stale,
                M('vf_bad_f', '在设置里重建索引；反复出现就查磁盘健康'))
    else:
        add('INFO', 'verify', M('vf_none_t', '全库校验：还没做过'),
            M('vf_none_d', '点下面的「现在校验」跑一次；库大时约半分钟到两分钟'))

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

    # ⑧ 聚合翻译 —— 逐引擎可用性（用户报「翻译坏了」时最关键的一节）
    tr = info.get('translate') or {}
    if tr and not tr.get('err'):
        eng = [x for x in tr.get('engines') or [] if not x.get('byok')]
        if tr.get('ok_n') is not None:
            ok_n = int(tr.get('ok_n') or 0)
            n = int(tr.get('n') or 0)
            bad = [x['id'] for x in eng if x.get('ok') is False]
            if n and ok_n == 0:
                add('FAIL', 'translate', M('tr_none_t', '聚合翻译全部不可达（0/%d）') % n,
                    M('tr_none_d', '五家免费引擎都连不上：多半是本机网络 / 代理拦了 TLS'),
                    M('tr_none_f', '关掉代理或把 translate.googleapis.com 等域名加入直连，再点深度体检'))
            elif bad:
                add('WARN', 'translate', M('tr_some_t', '聚合翻译 %d/%d 家可用') % (ok_n, n),
                    M('tr_some_d', '不可达：%s') % ', '.join(bad[:4]),
                    M('tr_some_f', '其余引擎仍可翻译；经常只剩一家时可换代理节点'))
            else:
                add('OK', 'translate', M('tr_ok_t', '聚合翻译 %d/%d 家可达') % (ok_n, n))
        cool = [x['id'] for x in eng if int(x.get('cool_s') or 0) > 0]
        if cool:
            add('INFO', 'tr_cool', M('tr_cool_t', '正在限流冷却：%s') % ', '.join(cool[:4]),
                M('tr_cool_d', '这几家刚返回过限流/错误，冷却期内会被自动跳过'))
        h24 = tr.get('h24') or {}
        if h24.get('calls'):
            add('INFO' if h24.get('bad_rate', 0) < 0.2 else 'WARN', 'tr_h24',
                M('tr_h24_t', '翻译 24 小时失败率 %.0f%%（%d/%d）')
                % ((h24.get('bad_rate') or 0) * 100, h24.get('bad') or 0, h24.get('calls') or 0),
                M('tr_h24_d', '含超时 / 空结果 / 回吐原文；这个数来自本机计数'))

    # ⑨ 离线 OCR
    oc = info.get('ocr') or {}
    if oc:
        if not oc.get('import'):
            add('FAIL', 'ocr', M('ocr_noimp_t', 'OCR 引擎加载失败'),
                str(oc.get('err') or '')[:120],
                M('ocr_noimp_f', '安装包不完整或被安全软件拦截；重新安装一次即可'))
        elif not oc.get('rapid'):
            add('FAIL', 'ocr', M('ocr_norapid_t', 'OCR 模型包缺失'),
                str(oc.get('err') or '')[:120])
        elif not oc.get('model_n'):
            add('WARN', 'ocr', M('ocr_nomodel_t', 'OCR 模型文件为 0 个'),
                M('ocr_nomodel_d', '首次识别会尝试下载模型，离线环境下会失败'))
        elif oc.get('ok') is False:
            add('WARN', 'ocr', M('ocr_run_t', 'OCR 试跑失败'),
                str(oc.get('err') or '')[:120])
        elif oc.get('ok'):
            add('OK', 'ocr', M('ocr_ok_t', '离线 OCR 就绪（%d 个模型，试跑 %d ms）')
                % (oc.get('model_n') or 0, oc.get('run_ms') or 0))

    # ⑩ 索引库规模与形状
    ih = info.get('index_health') or {}
    if ih and ih.get('state') != 'missing':
        if ih.get('err'):
            add('WARN', 'index_health', M('idx_err_t', '索引库读取失败'),
                str(ih.get('err'))[:120])
        else:
            add('INFO', 'index_health',
                M('idx_t', '索引库：%s 篇正文 / %s 个文件 / WAL %.1f MB / 空闲页 %d%%')
                % (ih.get('docs'), ih.get('files'), ih.get('wal_mb') or 0,
                   ih.get('free_pct') if ih.get('free_pct') is not None else -1))
            if (ih.get('wal_mb') or 0) > 100:
                add('WARN', 'idx_wal', M('idx_wal_t', 'WAL 已到 %.0f MB') % ih['wal_mb'],
                    M('idx_wal_d', '索引期间会涨，但长期这么大说明没正常收尾'),
                    M('idx_wal_f', '重启一次会强制 checkpoint；反复出现请检查是否有进程常驻写库'))
            if (ih.get('free_pct') or 0) > 30:
                add('INFO', 'idx_free', M('idx_free_t', '索引库空闲页 %d%%') % ih['free_pct'],
                    M('idx_free_d', '删除/重建索引留下的空洞，重建一次可回收'))
            if ih.get('last_indexed'):
                add('INFO', 'idx_last', M('idx_last_t', '最近入库 %s（库文件写于 %s）')
                    % (ih['last_indexed'], ih.get('db_mtime') or '?'),
                    M('idx_last_d', '长时间不更新说明实时索引没在跟（目录被移走/权限变更）'))
            dn, ft = ih.get('docs'), ih.get('fts')
            # 比的必须是同一层的两个数：docs 是「入库文档数」，fts 是 body 非空的文档数。
            # 别拿 meta 去比 files —— meta 只记扫过的路径、files 连没入库的也算，
            # 这两个天然不平，比出来天天一条假告警（本机实测 38190 vs 198417）。
            if isinstance(dn, int) and dn > 0 and isinstance(ft, int) and ft >= 0 \
                    and (dn - ft) > max(20, dn // 10):
                add('WARN', 'idx_fts', M('idx_fts_t', '正文与文档数对不上（docs %s / 有正文 %s）')
                    % (dn, ft), M('idx_fts_d', '部分文档只进了标题/元数据没进正文，搜索会搜不到它们'),
                    M('idx_fts_f', '在设置里重建一次索引即可补齐'))

    # ⑪ 剪贴板 / 热键
    cb = info.get('clipboard') or {}
    if cb.get('open') is False:
        owner = (cb.get('owner') or '').strip()
        add('WARN', 'clipboard', M('cb_busy_t', '剪贴板被别的程序占用'),
            (M('cb_owner_d', '%s 正占着剪贴板（pid %s）') % (owner or '?', cb.get('owner_pid') or '?'))
            if cb.get('owner_hwnd') else M('cb_busy_d', '连续 10 次都打不开，复制的内容可能不会进历史'),
            M('cb_busy_f', '关掉常驻的剪贴板工具/远程控制软件后重试'))
    elif cb.get('open') is True:
        add('OK', 'clipboard', M('cb_ok_t', '剪贴板可读写（%d 种格式）') % (cb.get('formats') or 0))
    hk = info.get('hotkey') or {}
    if hk.get('ok') is False:
        add('WARN', 'hotkey', M('hk_fail_t', '全局热键装不上'),
            str(hk.get('err') or '低级键盘钩子安装失败')[:120],
            M('hk_fail_f', '多为安全软件拦截；可在设置里改用其他触发方式'))
    elif hk.get('ok') is True:
        add('OK', 'hotkey', M('hk_ok_t', '全局热键可用（%d ms 装上）') % (hk.get('ms') or 0))

    # ⑫ 更新 / 授权 / 自启
    up = info.get('update') or {}
    if up.get('builtin') is True:
        add('OK', 'update', M('up_live_t', '更新通道可达（当前 %s，线上 %s）')
            % (up.get('cur') or '?', up.get('latest') or '?'),
            (M('up_urls_d', '另配了 %d 条国内直链，%d 条可达') % (up['urls'], up['reachable']))
            if up.get('urls') else '')
    elif up.get('builtin') is False:
        add('WARN', 'update', M('up_live_f_t', '更新通道不可达（镜像与 GitHub 都连不上）'),
            '; '.join((up.get('errors') or [])[:2])[:160],
            M('up_fail_f', '检查网络/代理；也可在 update_sources.json 填国内直链'))
    elif up and up.get('urls'):
        if up.get('ok') is False:
            add('WARN', 'update', M('up_fail_t', '更新直链不可达（0/%d）') % up['urls'],
                '; '.join((up.get('errors') or [])[:2])[:160],
                M('up_fail_f', '检查网络/代理；也可在 update_sources.json 填国内直链'))
        elif up.get('ok') is True:
            add('OK', 'update', M('up_ok_t', '更新直链可达（%d/%d）') % (up['reachable'], up['urls']))
    li = info.get('license') or {}
    if li.get('state') == 'ok':
        add('OK', 'license', M('lic_ok_t', '授权有效（%s，剩余 %s 天）')
            % (li.get('edition'), li.get('days_left')))
    elif li.get('state') in ('none', 'free'):
        # 免费版没有 license.json 是常态，不该报「异常」——之前这里天天一条 WARN
        add('INFO', 'license', M('lic_free_t', '未授权（免费版）'),
            M('lic_free_d', '要 Pro 就在设置里导入授权码；不影响其它功能'))
    elif li.get('state') and li.get('state') not in ('unknown', 'free'):
        add('WARN', 'license', M('lic_bad_t', '授权不可用（%s）') % li.get('state'),
            M('lic_bad_d', '机器码变化（换硬件/重装）或系统时间回拨都会这样'))
    if li.get('clock_warn'):
        add('WARN', 'clock', M('clock_t', '系统时间早于上次记录'),
            M('clock_d', '双系统 / 时区 / 虚拟机常见；只提示不改判'))
    au = info.get('autostart') or {}
    if au.get('cmd') and au.get('exists') is False:
        add('WARN', 'autostart', M('auto_bad_t', '开机自启指向的 exe 已不存在'),
            str(au.get('cmd'))[:120],
            M('auto_bad_f', '在设置里关闭再打开一次「开机自启」，让它重写路径'))
    elif au.get('cmd'):
        add('INFO', 'autostart', M('auto_ok_t', '开机自启已配置'))

    # ⑬ 进程资源 / 截图前置 / 多库 / 报错组件
    rs = info.get('resources') or {}
    if rs.get('gdi') or rs.get('threads'):
        lvl = 'WARN' if int(rs.get('gdi') or 0) > 8000 or int(rs.get('user') or 0) > 8000 else 'INFO'
        add(lvl, 'res', M('res_t', '线程 %s / 句柄 %s / GDI %s / User %s / 内存 %s MB（峰值 %s MB）')
            % (rs.get('threads'), rs.get('handles'), rs.get('gdi'), rs.get('user'),
               rs.get('mem_mb'), rs.get('peak_mb')),
            M('res_d', 'GDI/User 持续上涨说明有绘图对象泄漏（标题栏拖拽、悬浮球常见）；'
                       '内存峰值远高于常态也可能是泄漏'))
    cp = info.get('capture') or {}
    if cp.get('ok') is False:
        add('WARN', 'capture', M('cap_black_t', '屏幕捕获拿到的是黑图'),
            str(cp.get('err') or '')[:100],
            M('cap_black_f', '远程桌面 / 受保护内容会这样；截图 OCR 在这种会话里没法用'))
    ls = info.get('libsets') or {}
    if ls.get('n'):
        missing = [s['id'] for s in ls.get('sets') or [] if not s.get('db')]
        bad = []
        if ls.get('writable') is False:
            bad.append(M('lib_nowrite', '目录不可写'))
        if ls.get('sync'):
            bad.append(M('lib_syncd', '落在同步盘（%s）') % ls['sync'])
        for s in (ls.get('sets') or []):
            q = s.get('quick')
            if q and q != 'ok':
                bad.append('%s: %s' % (s.get('id'), str(q)[:20]))
        lvl = 'WARN' if bad else 'INFO'
        add(lvl, 'libsets', M('lib_t', '索引集 %d 个（当前 %s）') % (ls['n'], ls.get('active')),
            (M('lib_bad_d', '问题：%s') % ('; '.join(bad[:4])) if bad
             else ((M('lib_missing', '缺库：%s') % ', '.join(missing[:4])) if missing else '')))
    rp = info.get('reporter') or {}
    if rp:
        add('INFO', 'reporter', M('rep_t', '报错组件：本地 %d 份 / 日志 %.1f MB / 缓存 %.1f MB')
            % (rp.get('reports') or 0, rp.get('debug_mb') or 0, rp.get('profile_mb') or 0),
            (M('rep_backoff', '发送退避中 %ds（累计失败 %d 次）')
             % (rp.get('backoff_s'), rp.get('send_fail') or 0)) if rp.get('backoff_s') else
            (M('rep_nosend', '接收端未配置，报告只留在本机') if not rp.get('ingest') else ''))
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
    lines.append(M('rpt_title', 'Memex · 环境自检    %s') % info.get('ts', ''))
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
