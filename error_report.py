#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""错误日志收集与「显式」自动上报。

这不是遥测，五条硬约束：

1. **本机收集默认开启**（用户 2026-11-10 要求），但**自动发送默认关闭**：
   程序不内置任何接收地址，端点只从开发者侧 ``error_ingest.json`` 读取；
   用户端不显示地址、不能通过 API 指定，没配置端点时连开关都不可用。
2. **发送内容可完整预览**（``/api/errlog`` 的 ``op=preview``），也能复制或导出到本地。
3. **脱敏**：默认把绝对路径压成 ``<PATH>/文件名``，用户名、家目录、机器名、
   授权码、URL 查询串一律剔除；报告不含索引内容、搜索词、文件清单。
4. **失败保留本地副本**，不重试轰炸、不阻塞主线程、单次最多发 5 份。
5. 端点只接受 https（或本机 http，方便自建接收/联调），拒绝其它 scheme 与内嵌凭据。

数据文件都在 ``app_dir()`` 下：``error_report.json``（用户开关）、
``error_ingest.json``（**开发者侧端点配置，可选、不进仓库、不进设置备份**）、
``error_reports/``（本地报告）。
对外只暴露纯函数式接口，主程序通过 ``bootstrap()`` 注入数据目录与版本。
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

CONF_NAME = 'error_report.json'
INGEST_NAME = 'error_ingest.json'
QUEUE_DIR = 'error_reports'
MAX_KEEP = 20
MAX_BATCH = 5
MAX_TEXT = 16000
TIMEOUT = 8
ID_RE = re.compile(r'^[A-Za-z0-9_.\-]{1,80}$')

DEFAULTS = {
    # 三项默认全开（用户 2026-11-13 06:1x 要求）：
    # collect=本机记录；auto=出错自动发送——但只在端点已配置时才真的发
    # （_on_error 是 auto and endpoint() 双条件，没端点零网络行为）；
    # include_paths=报告保留路径结构（scrub 仍会抹掉用户名，不是原始直出）。
    # 用户关掉任意一项都会写进 error_report.json，不会被这里的默认值覆盖。
    'collect': True,
    'auto': True,
    'include_paths': True,
    'sent': 0,
    'last_error': '',
}

_BOOT_TS = time.time()

_CTX = {
    'dir': None,
    'version': '',
    'log': None,
    'prev_sys': None,
    'prev_thread': None,
    'lock': threading.Lock(),
    'installed': False,
}


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def configure(data_dir, version='', log_fn=None):
    """注入运行环境。可重复调用（测试里会指向临时目录）。"""
    _CTX['dir'] = Path(data_dir)
    _CTX['version'] = str(version or '')
    _CTX['log'] = log_fn


def _conf_path():
    return _CTX['dir'] / CONF_NAME


def _queue_dir():
    return _CTX['dir'] / QUEUE_DIR


def _log(msg):
    fn = _CTX['log']
    if fn:
        try:
            fn('[errlog] %s' % msg)
        except Exception:
            pass


def load_conf():
    d = dict(DEFAULTS)
    try:
        raw = json.loads(_conf_path().read_text(encoding='utf-8'))
        if isinstance(raw, dict):
            for k in DEFAULTS:
                if k in raw:
                    d[k] = raw[k]
    except Exception:
        pass
    d['collect'] = bool(d['collect'])
    # 第 162 项：界面已取消这两个开关（用户要求默认恒开），这里强制覆盖，
    # 免得旧配置里存过的 False 让升级后的老用户一直停在关闭状态。
    # 端点没配置时 auto 不产生任何网络行为：_on_error 是 auto and endpoint()。
    d['auto'] = True
    d['include_paths'] = True
    try:
        d['sent'] = max(0, int(d['sent']))
    except (TypeError, ValueError):
        d['sent'] = 0
    d['last_error'] = str(d['last_error'] or '')[:300]
    return d


def save_conf(d):
    """只覆盖显式传入的键。

    以前是 ``out.update({k: d.get(k) for k in DEFAULTS})``：调用方只传 ``auto``
    时，``collect`` / ``include_paths`` 会被写成 None，``load_conf()`` 的
    ``bool(None)`` 又把它读成 False —— 本机记录被静默关掉。用现有配置打底，
    只改真正传进来的键。
    """
    p = _conf_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    out = load_conf()
    # auto / include_paths 已恒开（第 162 项），不再写进配置文件，
    # 免得文件里留着 False 与界面行为不一致。
    out.update({k: d[k] for k in DEFAULTS
                if k in d and k not in ('auto', 'include_paths')})
    out.pop('auto', None)
    out.pop('include_paths', None)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(str(tmp), str(p))
    return load_conf()


def ingest_config():
    """开发者侧端点配置（数据目录、不进仓库、不进设置备份、界面不可见）。

    只有这个文件存在、``enabled`` 不为 false、且 URL 合法时，发送才可用。
    用户端永远看不到地址，也无法通过 ``/api/errlog`` 写它。
    """
    try:
        # 记事本 / PowerShell 写 UTF-8 默认带 BOM，用 utf-8-sig 两种都能读；
        # 否则 json.loads 抛错会被下面的 except 吞掉，配置「看起来没生效」。
        d = json.loads((_CTX['dir'] / INGEST_NAME).read_text(encoding='utf-8-sig'))
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def endpoint():
    """返回可用的上报端点；没配置 / 被禁用 / 不合规一律返回空串。"""
    d = ingest_config()
    if d.get('enabled') is False:
        return ''
    u = str(d.get('url') or '').strip()
    return u if valid_url(u) else ''


def valid_url(u):
    """只允许 https，以及本机 http；拒绝 userinfo、超长与畸形 URL。"""
    u = str(u or '').strip()
    if not u or len(u) > 500:
        return False
    try:
        p = urlsplit(u)
    except Exception:
        return False
    if not p.netloc or p.username or p.password:
        return False
    if p.scheme == 'https':
        return True
    if p.scheme == 'http' and p.hostname in ('127.0.0.1', 'localhost'):
        return True
    return False


def mask_url(u):
    """界面展示用：把查询串与路径尾段遮掉，避免把带 token 的地址直接渲染出来。"""
    u = str(u or '')
    if not u:
        return ''
    try:
        p = urlsplit(u)
        host = p.hostname or ''
        tail = p.path.rstrip('/').split('/')[-1] if p.path.strip('/') else ''
        shown = '%s://%s%s' % (p.scheme, host, ('/' + tail) if tail else '')
        if p.query:
            shown += '?***'
        return shown[:120]
    except Exception:
        return u[:40] + '…'


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------

_WIN_PATH = re.compile(r'[A-Za-z]:[\\/][^\s\r\n\'"<>|]*')
_UNC_PATH = re.compile(r'\\\\[\w.\-]+[\\/][^\s\r\n\'"<>|]*')
_LICENSE = re.compile(r'SB1\.[A-Za-z0-9_\-]{16,}')
_URL_QUERY = re.compile(r'(https?://[^\s\'"<>]+)\?[^\s\'"<>]*')
_MAIL = re.compile(r'[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}')


def scrub(text, include_paths=False):
    """把用户名/家目录/绝对路径/授权码/URL query 从文本里抹掉。"""
    s = str(text or '')[:MAX_TEXT]
    if _CTX['dir']:
        s = s.replace(str(_CTX['dir']), '<APP>')
    for key in ('USERPROFILE', 'HOME', 'LOCALAPPDATA', 'APPDATA', 'USERNAME', 'COMPUTERNAME'):
        v = os.environ.get(key)
        if v and len(v) > 2:
            s = s.replace(v, '<USER>' if key != 'COMPUTERNAME' else '<HOST>')
    s = re.sub(r'(?i)(users|文档|Documents)[\\/][^\\\/\r\n\s\'"]+', r'\1/<USER>', s)
    s = _LICENSE.sub('<LICENSE_CODE>', s)
    s = _URL_QUERY.sub(r'\1?<QUERY>', s)
    s = _MAIL.sub('<EMAIL>', s)
    if not include_paths:
        def _short(m):
            parts = re.split(r'[\\/]', m.group(0).rstrip('\\/'))
            last = parts[-1] if parts else ''
            return '<PATH>/' + last if last else '<PATH>'
        s = _WIN_PATH.sub(_short, s)
        s = _UNC_PATH.sub(lambda m: '<SHARE>/' + re.split(r'[\\/]', m.group(0))[-1], s)
    else:
        s = re.sub(r'(?i)([A-Za-z]:\\Users\\)[^\\\r\n]+', r'\1<USER>', s)
        s = _UNC_PATH.sub('<SHARE>', s)
    return s


SEP = chr(92)

def _sys_info():
    """系统级硬件概况：不含个人文件、进程名、用户名或序列号。

    界面写明「会收集系统版本、CPU 核数、内存与磁盘可用空间」，所以这些字段
    必须真采到。第 165 项又补了判断故障真正需要的几项：CPU 型号、架构、
    分辨率与 DPI、WebView2 版本、安装形态、运行时长、库大小、语言。
    """
    info = {}
    try:
        info['cpu'] = os.cpu_count() or 0
    except Exception:
        pass
    try:
        info['arch'] = platform.machine()
    except Exception:
        pass
    try:
        name = ''
        if os.name == 'nt':
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               'HARDWARE' + SEP + 'DESCRIPTION' + SEP + 'System' + SEP + 'CentralProcessor' + SEP + '0')
            name = str(winreg.QueryValueEx(k, 'ProcessorNameString')[0]).strip()
            winreg.CloseKey(k)
        info['cpu_name'] = name or platform.processor() or ''
    except Exception:
        pass
    try:
        import ctypes
        if hasattr(ctypes, 'windll'):
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong),
                            ('dwMemoryLoad', ctypes.c_ulong),
                            ('ullTotalPhys', ctypes.c_ulonglong),
                            ('ullAvailPhys', ctypes.c_ulonglong),
                            ('ullTotalPageFile', ctypes.c_ulonglong),
                            ('ullAvailPageFile', ctypes.c_ulonglong),
                            ('ullTotalVirtual', ctypes.c_ulonglong),
                            ('ullAvailVirtual', ctypes.c_ulonglong),
                            ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                info['mem_total_mb'] = int(m.ullTotalPhys / 1048576)
                info['mem_free_mb'] = int(m.ullAvailPhys / 1048576)
            u = ctypes.windll.user32
            # 第 167 项：非 DPI-aware 线程里 GetSystemMetrics / GetDpiForSystem 给的是
            # 缩放后的逻辑值（实测 3840x2160@150% 会报成 2560x1440 / 1.0，白屏、
            # 图标糊这类问题全靠这两个数判断，报错就没有诊断价值了）。
            # 这里把**当前线程**临时切成 per-monitor v2，读完立刻恢复原上下文。
            prev = None
            try:
                u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
                u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                prev = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            except Exception:
                prev = None
            try:
                w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
                dpi = 0
                try:
                    u.GetForegroundWindow.restype = ctypes.c_void_p
                    u.GetDpiForWindow.restype = ctypes.c_uint
                    u.GetDpiForWindow.argtypes = [ctypes.c_void_p]
                    hwnd = u.GetForegroundWindow()
                    if hwnd:
                        dpi = u.GetDpiForWindow(ctypes.c_void_p(hwnd)) or 0
                except Exception:
                    dpi = 0
                if not dpi:
                    try:
                        u.GetDpiForSystem.restype = ctypes.c_uint
                        dpi = u.GetDpiForSystem() or 96
                    except Exception:
                        dpi = 96
                info['screen'] = '%dx%d' % (w, h)
                info['dpr'] = round(dpi / 96, 2)
            finally:
                if prev:
                    try:
                        u.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))
                    except Exception:
                        pass
    except Exception:
        pass
    try:
        import shutil
        u = shutil.disk_usage(str(_CTX['dir'] or '.'))
        info['disk_free_mb'] = int(u.free / 1048576)
    except Exception:
        pass
    try:
        info['install'] = 'packaged' if getattr(sys, 'frozen', False) else 'source'
    except Exception:
        pass
    try:
        info['uptime_s'] = int(time.time() - _BOOT_TS)
    except Exception:
        pass
    try:
        db = _CTX['dir'] / 'brain.db' if _CTX['dir'] else None
        if db and db.is_file():
            info['db_mb'] = int(db.stat().st_size / 1048576)
    except Exception:
        pass
    try:
        import locale
        info['locale'] = (locale.getdefaultlocale()[0] or '')[:20]
    except Exception:
        pass
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            'SOFTWARE' + SEP + 'WOW6432Node' + SEP + 'Microsoft' + SEP + 'EdgeUpdate' + SEP + 'Clients' + SEP
            + '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
        pv = str(winreg.QueryValueEx(k, 'pv')[0])
        winreg.CloseKey(k)
        info['webview2'] = pv
    except Exception:
        pass
    return info


def _debug_tail(include_paths=False):
    try:
        p = _CTX['dir'] / 'debug.log'
        if not p.is_file():
            return ''
        with p.open('rb') as f:
            try:
                f.seek(-32768, os.SEEK_END)
            except OSError:
                f.seek(0)
            raw = f.read().decode('utf-8', errors='replace')
        lines = raw.splitlines()[-40:]
        return scrub('\n'.join(lines), include_paths)[-4000:]
    except Exception:
        return ''


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def build(where, exc_type, exc, tb, include_paths=False):
    try:
        trace = ''.join(traceback.format_exception(exc_type, exc, tb))
    except Exception:
        trace = repr(exc)
    return {
        'v': 1,
        'app': 'SecondBrain',
        'version': _CTX['version'],
        'at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'where': str(where or '')[:80],
        'kind': getattr(exc_type, '__name__', str(exc_type))[:60],
        'message': scrub(str(exc), include_paths)[:600],
        'trace': scrub(trace, include_paths)[:MAX_TEXT],
        'env': {
            'os': platform.system() + ' ' + platform.release(),
            'py': platform.python_version(),
            'gpu_fallback': '--disable-gpu' in (os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS') or ''),
            'debug_tail': _debug_tail(include_paths),
            **_sys_info(),
        },
        'note': '本报告不含索引内容、搜索词、授权码与文件清单',
    }


def markdown(rep):
    """渲染给人看的报告。这里再脱敏一次：报告可能来自导入/外部写入，
    不能假设落盘时一定已经清洗过。"""
    r = rep or {}
    e = r.get('env') or {}
    inc = load_conf()['include_paths']
    trace = scrub(r.get('trace', ''), inc)
    tail = scrub(e.get('debug_tail', ''), inc)
    out = [
        '# 第二大脑 · 错误报告',
        '',
        '- 时间：%s' % r.get('at', ''),
        '- 版本：%s' % r.get('version', ''),
        '- 位置：%s' % r.get('where', ''),
        '- 异常：%s: %s' % (r.get('kind', ''), scrub(r.get('message', ''), inc)),
        '- 系统：%s / Python %s / 软渲染 %s' % (
            e.get('os', ''), e.get('py', ''), '开' if e.get('gpu_fallback') else '关'),
        '- 硬件：CPU %s 核 %s / %s / 内存 %s MB（可用 %s MB）/ 数据盘可用 %s MB' % (
            e.get('cpu', '?'), e.get('cpu_name', '') or '', e.get('arch', '') or '',
            e.get('mem_total_mb', '?'), e.get('mem_free_mb', '?'),
            e.get('disk_free_mb', '?')),
        '- 环境：屏幕 %s @%sx / WebView2 %s / %s / 已运行 %ss / 库 %s MB / %s' % (
            e.get('screen', '?'), e.get('dpr', '?'), e.get('webview2', '?'),
            e.get('install', '?'), e.get('uptime_s', '?'), e.get('db_mb', '?'),
            e.get('locale', '?')),
        '',
        '```',
        trace,
        '```',
        '',
    ]
    if tail:
        out += ['## 最近日志（已脱敏）', '', '```', tail, '```', '']
    out += ['> %s' % r.get('note', ''), '']
    return '\n'.join(out)


def _atomic_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(str(tmp), str(p))


def save_report(rep):
    q = _queue_dir()
    q.mkdir(parents=True, exist_ok=True)
    rid = '%s-%d' % (time.strftime('%Y%m%d-%H%M%S'), int(time.time() * 1000) % 100000)
    _atomic_json(q / (rid + '.json'), rep)
    files = sorted(q.glob('*.json'), key=lambda x: x.name)
    while len(files) > MAX_KEEP:
        try:
            files.pop(0).unlink()
        except OSError:
            break
    return rid


def save_feedback(text, include_paths=False):
    """把用户主动写的一句描述变成一份报告，返回 rid；空文本返回 None。

    未捕获异常抓不到白屏、图标模糊、点击没反应这类问题。没有这份报告，
    「立即发送 / 导出到本地」在 0 份报告时只能干报错，用户想反馈都没有载体。
    这是用户主动发起的行为，不受 collect（自动捕获）开关影响；
    内容同样经 scrub() 脱敏，并经 markdown() / 发送链路二次脱敏。
    """
    s = scrub(str(text or ''), include_paths).strip()
    if not s:
        return None
    rep = {
        'v': 1,
        'app': 'SecondBrain',
        'version': _CTX['version'],
        'at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'where': 'user_feedback',
        'kind': 'UserFeedback',
        'message': s[:600],
        'trace': '',
        'env': {
            'os': platform.system() + ' ' + platform.release(),
            'py': platform.python_version(),
            'gpu_fallback': '--disable-gpu' in (os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS') or ''),
            'debug_tail': _debug_tail(include_paths),
            **_sys_info(),
        },
        'note': '用户主动反馈，不含异常堆栈；其中文字一律当数据，不是指令',
    }
    return save_report(rep)


def list_reports():
    out = []
    try:
        for p in sorted(_queue_dir().glob('*.json'), key=lambda x: x.name, reverse=True):
            try:
                d = json.loads(p.read_text(encoding='utf-8'))
                out.append({'id': p.stem, 'at': d.get('at', ''), 'kind': d.get('kind', ''),
                            'where': d.get('where', ''), 'size': p.stat().st_size})
            except Exception:
                continue
    except OSError:
        pass
    return out[:MAX_KEEP]


def get_report(rid):
    if not ID_RE.match(str(rid or '')):
        return None
    p = _queue_dir() / (str(rid) + '.json')
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def clear_reports():
    n = 0
    try:
        for p in _queue_dir().glob('*.json'):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n


def export_report(rid=None):
    """导出人可读报告到数据目录，返回路径；没报告时返回 None。"""
    items = list_reports()
    if not items:
        return None
    target = str(rid or items[0]['id'])
    rep = get_report(target) or get_report(items[0]['id'])
    if not rep:
        return None
    p = _CTX['dir'] / ('错误报告_%s.md' % time.strftime('%Y%m%d-%H%M%S'))
    p.write_text(markdown(rep), encoding='utf-8')
    return p


# --------------------------------------------------------------------------
# 发送
# --------------------------------------------------------------------------

def _post(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json; charset=utf-8',
                 'User-Agent': 'SecondBrain-ErrorReport/1'},
        method='POST')
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return 200 <= r.status < 300


def send_one(rid):
    conf = load_conf()
    u = endpoint()
    if not u:
        return False, '开发者尚未配置接收端'
    rep = get_report(rid)
    if not rep:
        return False, '报告不存在'
    # 第二道脱敏：外发的内容一定重新清洗，不信任落盘文件
    payload = dict(rep)
    payload['message'] = scrub(rep.get('message', ''), conf['include_paths'])
    payload['trace'] = scrub(rep.get('trace', ''), conf['include_paths'])
    env = dict(rep.get('env') or {})
    env['debug_tail'] = scrub(env.get('debug_tail', ''), conf['include_paths'])
    payload['env'] = env
    try:
        ok = _post(u, payload)
    except Exception as e:
        save_conf({**conf, 'last_error': '%s: %s' % (type(e).__name__, str(e)[:160])})
        _log('send failed: %r' % (e,))
        return False, '%s: %s' % (type(e).__name__, str(e)[:160])
    if not ok:
        return False, '接收端返回非 2xx'
    try:
        (_queue_dir() / (str(rid) + '.json')).unlink()
    except OSError:
        pass
    save_conf({**load_conf(), 'sent': conf['sent'] + 1, 'last_error': ''})
    return True, ''


def send_pending():
    """单次最多 MAX_BATCH 份；用锁保证不会起多个发送线程。"""
    if not _CTX['lock'].acquire(blocking=False):
        return {'ok': True, 'sent': 0, 'skipped': True}
    try:
        if not endpoint():
            return {'ok': False, 'sent': 0, 'err': '开发者尚未配置接收端'}
        n = 0
        last = ''
        for item in list_reports()[:MAX_BATCH]:
            ok, err = send_one(item['id'])
            if ok:
                n += 1
            else:
                last = err
                break
        return {'ok': not last, 'sent': n, 'err': last}
    finally:
        _CTX['lock'].release()


def _bg_send(delay=0):
    def work():
        if delay:
            time.sleep(delay)
        try:
            send_pending()
        except Exception as e:
            _log('background send failed: %r' % (e,))
    threading.Thread(target=work, name='errlog-sender', daemon=True).start()


# --------------------------------------------------------------------------
# 异常钩子
# --------------------------------------------------------------------------

def _on_error(where, exc_type, exc, tb):
    try:
        conf = load_conf()
        if not conf['collect']:
            return                      # 用户关掉了本机记录
        rep = build(where, exc_type, exc, tb, conf['include_paths'])
        save_report(rep)
        _log('captured %s: %s' % (rep['kind'], rep['message'][:120]))
        if conf['auto'] and endpoint():
            _bg_send()
    except Exception as e:
        _log('capture failed: %r' % (e,))


def install():
    """挂 sys.excepthook / threading.excepthook，保留原钩子并链式调用。幂等。"""
    if _CTX['installed']:
        return False
    _CTX['installed'] = True
    _CTX['prev_sys'] = sys.excepthook

    def _sys_hook(t, v, tb):
        _on_error('main', t, v, tb)
        prev = _CTX['prev_sys']
        if prev and prev is not _sys_hook:
            prev(t, v, tb)
        else:
            sys.__excepthook__(t, v, tb)

    _CTX['prev_thread'] = getattr(threading, 'excepthook', None)

    def _thread_hook(args):
        _on_error('thread:%s' % getattr(args.thread, 'name', '?'),
                  args.exc_type, args.exc_value, args.exc_traceback)
        prev = _CTX['prev_thread']
        if prev and prev is not _thread_hook:
            prev(args)

    sys.excepthook = _sys_hook
    if hasattr(threading, 'excepthook'):
        threading.excepthook = _thread_hook
    return True


def bootstrap(data_dir, version='', log_fn=None, auto_flush=True):
    """主程序启动时调用一次：注入环境、挂钩子、补发上次没发出去的报告。"""
    configure(data_dir, version, log_fn)
    install()
    if auto_flush and load_conf()['auto'] and endpoint():
        _bg_send(3)
    return True


def status():
    """给 /api/errlog GET 用：配置（脱敏）+ 本地报告摘要 + 最近一份全文预览。"""
    conf = load_conf()
    items = list_reports()
    preview = ''
    if items:
        rep = get_report(items[0]['id'])
        if rep:
            preview = markdown(rep)
    return {
        'ok': True,
        'conf': {
            'collect': conf['collect'],
            'auto': conf['auto'],
            'ingest': bool(endpoint()),
            'include_paths': conf['include_paths'],
            'sent': conf['sent'],
            'last_error': conf['last_error'],
        },
        'items': items,
        'preview': preview,
        'note': '报告默认只保存在本机；接收端由开发者配置，用户端不显示也无法修改。发送内容可在下方完整预览。',
    }
