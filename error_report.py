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
6. **失败也要有现场**（第 277 项）：只挂 excepthook 只能抓到「抛到栈顶」的异常，
   而 HTTP 处理器的异常被 socketserver 吞成 stderr、前端事件里的异常根本到不了
   后端。现在补了三处：``capture_api()``（QuietServer.handle_error 调用）、
   ``save_js()``（前端 window.onerror / unhandledrejection）、以及
   ``arm_run_guard()`` 的残留标记（原生崩溃 / 杀进程 / 断电后仍能出一份报告）。
7. **同一签名合并计数**：同一个 bug 反复报只留首份 + 4 个时间点，其余记 count，
   不再用 20 份上限把别的证据挤掉（MAX_KEEP=60 / MAX_PER_SIG=5 / SIG_CAP=60）。
8. 出错前在干什么：报告附 ``breadcrumb``（最近 30 个动作，只有动作名与枚举）。
   它由 ``breadcrumb.py`` 维护，不含搜索词、路径、剪贴板内容。

数据文件都在 ``app_dir()`` 下：``error_report.json``（用户开关）、
``error_ingest.json``（**开发者侧端点配置，可选、不进仓库、不进设置备份**）、
``error_reports/``（本地报告）。
对外只暴露纯函数式接口，主程序通过 ``bootstrap()`` 注入数据目录与版本。
"""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import urllib.request

import netproxy            # 第 299 项：代理只留一份
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import urlsplit

CONF_NAME = 'error_report.json'
INGEST_NAME = 'error_ingest.json'
QUEUE_DIR = 'error_reports'
RUN_NAME = 'run_state.json'
MAX_KEEP = 60          # 第 277 项：20 份太小，一个反复报的 bug 能把别的证据挤光
MAX_PER_SIG = 5        # 同一签名最多留 5 份（首份 + 4 个时间点），其余合并成 count
SIG_CAP = 60           # 不同签名最多 60 个（= MAX_KEEP）；同签名多份也受总份数约束
MAX_BC = 30            # 报告里附带的面包屑条数
MAX_BATCH = 5
MAX_TEXT = 16000
TIMEOUT = 8
# 直连先试：普通用户没代理，不能一上来就卡 8 秒；短超时失败后再看有没有代理。
# 2026-11-18（第 283 项）：主端点换成国内可直连的自定义域名，实测 TLS 0.5s +
# TTFB 0.9s，4 秒在弱网/回程抖动时会误判失败；抬到 6 秒，仍小于代理分支的 8 秒。
DIRECT_TIMEOUT = 6
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
    # 第 277 项：发送退避。端点偶发超时（实测 URLError: timed out）时，
    # 以前每次启动都重撞一遍；现在记 streak / next_try，2 的幂退避到 10 分钟。
    'fail_streak': 0,
    'next_try': 0.0,
    'send_fail': 0,
}

_BOOT_TS = time.time()

_CTX = {
    'dir': None,
    'version': '',
    'log': None,
    'prev_sys': None,
    'prev_thread': None,
    'context_fn': None,
    'guard': False,
    'prev_checked': False,
    'lock': threading.Lock(),
    'installed': False,
}


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def configure(data_dir, version='', log_fn=None, context_fn=None):
    """注入运行环境。可重复调用（测试里会指向临时目录）。

    ``context_fn``：主程序提供的「复现必需」环境位（设置开关 / 索引规模），
    只在生成报告时调用，异常一律吞掉 —— 环境探测失败不能拖垮报告本身。
    """
    _CTX['dir'] = Path(data_dir) if data_dir else None
    _CTX['version'] = str(version or '')
    _CTX['log'] = log_fn
    _CTX['context_fn'] = context_fn


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
    try:
        d['fail_streak'] = max(0, int(d.get('fail_streak') or 0))
    except (TypeError, ValueError):
        d['fail_streak'] = 0
    try:
        d['next_try'] = max(0.0, float(d.get('next_try') or 0.0))
    except (TypeError, ValueError):
        d['next_try'] = 0.0
    try:
        d['send_fail'] = max(0, int(d.get('send_fail') or 0))
    except (TypeError, ValueError):
        d['send_fail'] = 0
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


def _builtin_endpoints():
    """打包时写进 `_internal/builtin_ingest.py` 的端点（安装包用，用户零配置）。

    只认构建产物里的那个模块：开发目录里没有它，就等于没内置。
    blob 是 XOR+base64 混淆过的字符串 —— 提高门槛，不承诺防得住决心解包的人；
    真正的防线在服务端（限流 + 管理口分离 + token 可轮换）。
    """
    try:
        import builtin_ingest as _bi          # 只在打包产物里存在
    except Exception:
        return []
    try:
        raw = _bi.BLOB
        key = int(getattr(_bi, 'KEY', 0))
        data = base64.b64decode(raw.encode('ascii'))
        txt = bytes(b ^ (key or 0x5A) for b in data).decode('utf-8')
        d = json.loads(txt)
    except Exception:
        return []
    if not isinstance(d, dict) or d.get('enabled') is False:
        return []
    out = []
    for u in [d.get('url')] + list(d.get('urls') or []):
        u = str(u or '').strip()
        if valid_url(u) and u not in out:
            out.append(u)
    return out


def endpoints():
    """返回所有可用的上报端点（按顺序试）。

    顺序 = 作者侧 `error_ingest.json`（本机可改、可关）→ 打包内置（给普通用户）。
    `url` 与 `urls` 合并去重，逐个尝试；任何一个成功就算送达。
    """
    d = ingest_config()
    if d.get('enabled') is False:
        return []              # 显式关掉就是全关，连内置也不发
    cands = []
    one = str(d.get('url') or '').strip()
    if one:
        cands.append(one)
    many = d.get('urls')
    if isinstance(many, (list, tuple)):
        cands.extend(str(x).strip() for x in many)
    cands.extend(_builtin_endpoints())
    out = []
    for u in cands:
        if valid_url(u) and u not in out:
            out.append(u)
    return out


def endpoint():
    """返回第一个可用端点（兼容旧调用点）；没配置 / 被禁用 / 不合规一律返回空串。"""
    eps = endpoints()
    return eps[0] if eps else ''


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

# API 密钥形态（第 286 项）：用户把带 key 的报错/日志贴进来时，绝不能原样上传。
# 只认「够长且形态明确」的串，避免误伤普通英文单词。
_SECRETS = (
    re.compile(r'sk-[A-Za-z0-9_\-]{16,}'),                    # OpenAI / DeepSeek / Moonshot
    re.compile(r'AIza[0-9A-Za-z_\-]{30,}'),                   # Google API key
    re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}'),                # GitHub token
    re.compile(r'github_pat_[A-Za-z0-9_]{20,}'),              # GitHub fine-grained PAT
    re.compile(r'xox[baprs]-[A-Za-z0-9\-]{10,}'),             # Slack
    re.compile(r'AKIA[0-9A-Z]{16}'),                          # AWS access key id
    re.compile(r'(?i)bearer\s+[A-Za-z0-9._\-]{16,}'),         # Authorization: Bearer
    re.compile(r'(?i)(api[_-]?key|token|secret)["\']?\s*[:=]\s*["\']?[A-Za-z0-9._\-]{16,}'),  # key=xxx
)


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
    for _re in _SECRETS:
        s = _re.sub('<KEY>', s)
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

def _app_env():
    """主程序注入的「复现必需」环境位（设置开关 / 索引规模 / 指标摘要）。

    由 bootstrap(context_fn=...) 提供；没有就返回空 dict。这里只收布尔与计数，
    不收任何用户内容 —— 打开哪个开关、库多大、哪个功能最近失败了多少次。
    """
    fn = _CTX.get('context_fn')
    if not fn:
        return {}
    try:
        d = fn()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


_FRAME = re.compile(r'File "([^"]+)", line (\d+)')


def _env_check_block():
    """第 288 项：把环境体检的 20 条判定附进报告。

    只用 `env_check.snapshot()` 读快照（启动时的 light 探测、或用户点过的体检），
    **不在这里重跑探测**：light 一次 3.1s，崩溃现场 / 点「立即发送」都等不起。
    快照太旧（>30 分钟）也照用，但记下 `age_s` —— 报告的用途是定位问题，不是追责。
    """
    try:
        import env_check
        # 界面语言（第 288 项）：报告预览是给用户看的，判定标题必须跟着语言走，
        # 不能又是「英文界面里冒出一段中文」。拿不到就退回中文。
        lang = 'zh'
        try:
            import brain
            lang = (brain.boot_lang() or 'zh')[:8]
        except Exception:
            pass
        info, age = env_check.snapshot(max_age=None)
        if not info:
            # 没快照：后台补一次，本份报告先不带（绝不为它卡住现场）
            _check_worker()
            return {}
        if age is not None and age > 600:
            # 快照超过 10 分钟：照样用，同时后台刷一份给下次
            _check_worker()
        items = []
        for it in env_check.judge(info, lang):
            if not isinstance(it, dict):
                continue
            items.append({'level': it.get('level'), 'key': it.get('key'),
                          'title': (it.get('title') or '')[:160],
                          'detail': (it.get('detail') or '')[:160]})
        bad = [it for it in items if it['level'] in ('FAIL', 'WARN')]
        return {'age_s': int(age or 0), 'lang': lang,
                'ok': len([i for i in items if i['level'] == 'OK']),
                'warn': len([i for i in items if i['level'] == 'WARN']),
                'fail': len([i for i in items if i['level'] == 'FAIL']),
                'items': items[:40],
                'bad': [i['key'] for i in bad][:12]}
    except Exception:
        return {}


def _check_worker():
    """第 288 项：没有快照时，后台补一次轻量体检（3 秒级），本份报告不等它。

    崩溃/反馈现场最忌讳卡住主线程，所以探测放 daemon 线程里跑；跑完的快照会留在
    env_check 里，下一份报告（以及用户再点一次预览）就带上。同一时刻只跑一个。
    """
    global _CHECK_THREAD
    try:
        if _CHECK_THREAD is not None and _CHECK_THREAD.is_alive():
            return
        import threading
        import env_check

        def run():
            try:
                env_check.probe_all(light=True)
            except Exception:
                pass

        _CHECK_THREAD = threading.Thread(target=run, daemon=True,
                                         name='env-check-refresh')
        _CHECK_THREAD.start()
    except Exception:
        pass


_CHECK_THREAD = None


def _signature(kind, where, trace):
    """同一 bug 的指纹：异常类型 + 位置 + 最后三帧的文件名:行号。

    只用文件名和行号，不用完整路径 —— 换台机器路径不同，但签名应当一样。
    """
    frames = []
    for m in _FRAME.finditer(trace or ''):
        base = re.split(r'[\\/]', m.group(1))[-1]
        frames.append('%s:%s' % (base, m.group(2)))
    return '|'.join([str(kind or '')[:40], str(where or '')[:24]]
                    + frames[-3:])[:160]


def _breadcrumb():
    try:
        import breadcrumb
        return breadcrumb.snapshot(MAX_BC)
    except Exception:
        return []


def build(where, exc_type, exc, tb, include_paths=False):
    try:
        trace = ''.join(traceback.format_exception(exc_type, exc, tb))
    except Exception:
        trace = repr(exc)
    clean_trace = scrub(trace, include_paths)[:MAX_TEXT]
    kind = getattr(exc_type, '__name__', str(exc_type))[:60]
    where = str(where or '')[:80]
    return {
        'v': 1,
        'app': 'SecondBrain',
        'version': _CTX['version'],
        'at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'where': where,
        'kind': kind,
        'message': scrub(str(exc), include_paths)[:600],
        'trace': clean_trace,
        'sig': _signature(kind, where, clean_trace),
        'count': 1,
        'breadcrumb': _breadcrumb(),
        # 第 288 项：体检结论（20 条判定）跟着报告一起走，只读快照、不重测
        'check': _env_check_block(),
        'env': {
            'os': platform.system() + ' ' + platform.release(),
            'py': platform.python_version(),
            'gpu_fallback': '--disable-gpu' in (os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS') or ''),
            'debug_tail': _debug_tail(include_paths),
            **_sys_info(),
            **_app_env(),
        },
        'note': '本报告不含索引内容、搜索词、授权码与文件清单',
    }


_MD_L = {
    'zh-CN': {
        'title': '# Memex · 错误报告',
        'time': '- 时间：%s',
        'ver': '- 版本：%s',
        'where': '- 位置：%s',
        'kind': '- 异常：%s: %s',
        'dup': '- 重复：同一签名累计 %d 次（本份为首份现场）',
        'sys': '- 系统：%s / Python %s / 软渲染 %s',
        'hw': '- 硬件：CPU %s 核 %s / %s / 内存 %s MB（可用 %s MB）/ 数据盘可用 %s MB',
        'env': '- 环境：屏幕 %s @%sx / WebView2 %s / %s / 已运行 %ss / 库 %s MB / %s',
        'gpu_on': '开', 'gpu_off': '关',
        'tail': '## 最近日志（已脱敏）',
        'bc': '## 出错前动作（最近 %d 条）',
        'mx': '## 各功能最近 24 小时（本机计数）',
        'check': '## 环境体检（快照 %s 前：%d OK / %d WARN / %d FAIL）',
    },
    'en': {
        'title': '# Memex · Error report',
        'time': '- Time: %s',
        'ver': '- Version: %s',
        'where': '- Where: %s',
        'kind': '- Error: %s: %s',
        'dup': '- Repeat: %d times for this signature (this copy is the first scene)',
        'sys': '- System: %s / Python %s / software rendering %s',
        'hw': '- Hardware: CPU %s cores %s / %s / RAM %s MB (free %s MB) / data disk free %s MB',
        'env': '- Environment: screen %s @%sx / WebView2 %s / %s / uptime %ss / index %s MB / %s',
        'gpu_on': 'on', 'gpu_off': 'off',
        'tail': '## Recent log (redacted)',
        'bc': '## Actions before the error (last %d)',
        'mx': '## Features in the last 24 h (local counters)',
        'check': '## Environment check (snapshot %s ago: %d OK / %d WARN / %d FAIL)',
    },
    'es': {
        'title': '# Memex · Informe de error',
        'time': '- Hora: %s',
        'ver': '- Versión: %s',
        'where': '- Dónde: %s',
        'kind': '- Error: %s: %s',
        'dup': '- Repetición: %d veces con la misma firma (esta copia es el primer caso)',
        'sys': '- Sistema: %s / Python %s / renderizado por software %s',
        'hw': '- Hardware: CPU %s núcleos %s / %s / RAM %s MB (libre %s MB) / disco de datos libre %s MB',
        'env': '- Entorno: pantalla %s @%sx / WebView2 %s / %s / activo %ss / índice %s MB / %s',
        'gpu_on': 'sí', 'gpu_off': 'no',
        'tail': '## Registro reciente (censurado)',
        'bc': '## Acciones antes del error (últimas %d)',
        'mx': '## Funciones en las últimas 24 h (contadores locales)',
        'check': '## Chequeo del entorno (instantánea de hace %s: %d OK / %d WARN / %d FAIL)',
    },
    'pt': {
        'title': '# Memex · Relatório de erro',
        'time': '- Hora: %s',
        'ver': '- Versão: %s',
        'where': '- Onde: %s',
        'kind': '- Erro: %s: %s',
        'dup': '- Repetição: %d vezes para a mesma assinatura (esta cópia é o primeiro caso)',
        'sys': '- Sistema: %s / Python %s / renderização por software %s',
        'hw': '- Hardware: CPU %s núcleos %s / %s / RAM %s MB (livre %s MB) / disco de dados livre %s MB',
        'env': '- Ambiente: tela %s @%sx / WebView2 %s / %s / ativo %ss / índice %s MB / %s',
        'gpu_on': 'sim', 'gpu_off': 'não',
        'tail': '## Log recente (censurado)',
        'bc': '## Ações antes do erro (últimas %d)',
        'mx': '## Funções nas últimas 24 h (contadores locais)',
        'check': '## Verificação do ambiente (instantâneo de %s atrás: %d OK / %d WARN / %d FAIL)',
    },
    'hi': {
        'title': '# Memex · त्रुटि रिपोर्ट',
        'time': '- समय: %s',
        'ver': '- संस्करण: %s',
        'where': '- स्थान: %s',
        'kind': '- त्रुटि: %s: %s',
        'dup': '- दोहराव: इसी हस्ताक्षर से %d बार (यह प्रति पहला मामला है)',
        'sys': '- सिस्टम: %s / Python %s / सॉफ़्टवेयर रेंडरिंग %s',
        'hw': '- हार्डवेयर: CPU %s कोर %s / %s / रैम %s MB (उपलब्ध %s MB) / डेटा डिस्क उपलब्ध %s MB',
        'env': '- वातावरण: स्क्रीन %s @%sx / WebView2 %s / %s / चालू %ss / इंडेक्स %s MB / %s',
        'gpu_on': 'चालू', 'gpu_off': 'बंद',
        'tail': '## हाल का लॉग (साफ़ किया गया)',
        'bc': '## त्रुटि से पहले की क्रियाएँ (अंतिम %d)',
        'mx': '## पिछले 24 घंटे में सुविधाएँ (स्थानीय गिनती)',
        'check': '## पर्यावरण जाँच (%s पहले का स्नैपशॉट: %d OK / %d WARN / %d FAIL)',
    },
}


def _md_lang(lang):
    s = str(lang or '').lower()
    if s.startswith('zh'):
        return 'zh-CN'
    for k in ('en', 'es', 'pt', 'hi'):
        if s.startswith(k):
            return k
    return 'zh-CN'


def markdown(rep, lang='zh-CN'):
    """渲染给人看的报告。这里再脱敏一次：报告可能来自导入/外部写入，
    不能假设落盘时一定已经清洗过。

    第 281 项：整块以前是写死中文，西/英/印地语界面里那一段全是中文。
    文案按界面语言取（``_MD_L``），中文仍是默认。
    """
    L = _MD_L[_md_lang(lang)]
    r = rep or {}
    e = r.get('env') or {}
    inc = load_conf()['include_paths']
    trace = scrub(r.get('trace', ''), inc)
    tail_txt = scrub(e.get('debug_tail', ''), inc)
    out = [
        L['title'],
        '',
        L['time'] % r.get('at', ''),
        L['ver'] % r.get('version', ''),
        L['where'] % r.get('where', ''),
        L['kind'] % (r.get('kind', ''), scrub(r.get('message', ''), inc)),
    ]
    try:
        n = int(r.get('count') or 1)
    except Exception:
        n = 1
    if n > 1:
        out.append(L['dup'] % n)
    out += [
        L['sys'] % (e.get('os', ''), e.get('py', ''),
                    L['gpu_on'] if e.get('gpu_fallback') else L['gpu_off']),
        L['hw'] % (e.get('cpu', '?'), e.get('cpu_name', '') or '', e.get('arch', '') or '',
                   e.get('mem_total_mb', '?'), e.get('mem_free_mb', '?'),
                   e.get('disk_free_mb', '?')),
        L['env'] % (e.get('screen', '?'), e.get('dpr', '?'), e.get('webview2', '?'),
                    e.get('install', '?'), e.get('uptime_s', '?'), e.get('db_mb', '?'),
                    e.get('locale', '?')),
        '',
        '```',
        trace,
        '```',
        '',
    ]
    if tail_txt:
        out += [L['tail'], '', '```', tail_txt, '```', '']
    # 第 288 项：环境体检的 20 条判定（用户反馈「体检那么多项，报告里几乎看不到」）
    ck = r.get('check') or {}
    if isinstance(ck, dict) and ck.get('items'):
        age = int(ck.get('age_s') or 0)
        age_txt = ('%d s' % age) if age < 60 else ('%d min' % (age // 60))
        rows = []
        for it in ck['items']:
            if not isinstance(it, dict):
                continue
            rows.append('%s  %-10s  %s' % (it.get('level', '?'), it.get('key', ''),
                                           it.get('title', '')))
        out += [L['check'] % (age_txt, int(ck.get('ok') or 0),
                              int(ck.get('warn') or 0), int(ck.get('fail') or 0)),
                '', '```', '\n'.join(rows), '```', '']
    bc = r.get('breadcrumb') or []
    if isinstance(bc, list) and bc:
        rows = []
        for it in bc[-MAX_BC:]:
            if not isinstance(it, dict):
                continue
            extra = ' '.join('%s=%s' % (k, v) for k, v in it.items()
                             if k not in ('t', 'a'))
            rows.append('%s  %s%s' % (it.get('t', ''), it.get('a', ''),
                                      ('  ' + extra) if extra else ''))
        if rows:
            out += [L['bc'] % len(rows), '', '```', '\n'.join(rows), '```', '']
    mx = (r.get('env') or {}).get('metrics')
    if isinstance(mx, list) and mx:
        rows = ['%s  %d/%d 失败  p95 %sms  %s' % (
            it.get('name', ''), it.get('h24_bad', 0), it.get('h24_calls', 0),
            it.get('p95', 0), it.get('last_err', '')) for it in mx if isinstance(it, dict)]
        if rows:
            out += [L['mx'], '', '```', '\n'.join(rows), '```', '']
    out += ['> %s' % r.get('note', ''), '']
    return '\n'.join(out)


def _atomic_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(str(tmp), str(p))


def _find_sig(q, sig):
    """找同签名的已有报告（从新到旧）。sig 为空时不合并。"""
    if not sig:
        return None
    try:
        files = sorted(q.glob('*.json'), key=lambda x: x.name, reverse=True)
    except OSError:
        return None
    for p in files[:MAX_KEEP]:
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        if isinstance(d, dict) and d.get('sig') == sig:
            return p
    return None


def _prune(q):
    """按签名分桶保留：同签名最多 MAX_PER_SIG 份、不同签名最多 SIG_CAP 个。

    以前只有「总份数 ≤ 20」一条，于是一个每秒报一次的 bug 能把 20 份配额全吃掉，
    真正需要的另一类报告连影子都留不下。
    """
    try:
        files = sorted(q.glob('*.json'), key=lambda x: x.name)
    except OSError:
        return
    info = {}
    for p in files:
        sig = ''
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
            if isinstance(d, dict):
                sig = str(d.get('sig') or '')
        except Exception:
            pass
        info[p] = sig
    # ① 同一签名只留最新的 MAX_PER_SIG 份
    for sig in {s for s in info.values() if s}:
        group = [p for p in files if info.get(p) == sig and p.exists()]
        for p in group[:-MAX_PER_SIG] if len(group) > MAX_PER_SIG else []:
            try:
                p.unlink()
            except OSError:
                pass
    # ② 不同签名超过 SIG_CAP：整个最旧的一组丢掉
    live = [p for p in files if p.exists()]
    while len({info[p] for p in live if info.get(p)}) > SIG_CAP:
        oldest_sig = info.get(live[0]) or ''
        victims = [p for p in live if (info.get(p) or '') == oldest_sig]
        if not victims:
            victims = [live[0]]
        for p in victims:
            try:
                p.unlink()
            except OSError:
                pass
        live = [p for p in live if p.exists()]
    # ③ 总份数兜底
    while len(live) > MAX_KEEP:
        try:
            live.pop(0).unlink()
        except OSError:
            break
        live = [p for p in live if p.exists()]


def save_report(rep):
    if not isinstance(rep, dict):
        return ''
    q = _queue_dir()
    q.mkdir(parents=True, exist_ok=True)
    sig = str(rep.get('sig') or _signature(rep.get('kind'), rep.get('where'),
                                           rep.get('trace')))
    rep['sig'] = sig
    rep.setdefault('count', 1)
    dup = _find_sig(q, sig)
    if dup is not None:
        # 同签名：不新增文件，累加次数、刷新时间；首份的现场（面包屑/堆栈）保留
        try:
            d = json.loads(dup.read_text(encoding='utf-8'))
            if isinstance(d, dict):
                d['count'] = max(1, int(d.get('count') or 1)) + 1
                d['last_at'] = rep.get('at') or d.get('last_at', '')
                if not d.get('breadcrumb') and rep.get('breadcrumb'):
                    d['breadcrumb'] = rep['breadcrumb']
                _atomic_json(dup, d)
                return dup.stem
        except Exception:
            pass
    # rid = 秒 + 毫秒尾数。Windows 的 time.time() 精度有限（~2ms），连续快存会同毫秒
    # 撞名，os.replace 会把上一份覆盖掉 —— 队列悄悄少几份（test_care 的 cap 测试
    # 因此偶发 17 != 20，probe 实测 26 存只剩 16~20 份）。撞名就往后挪一毫秒，
    # 每份都真实落盘；跨过 99999 时重新取秒，保证文件名排序仍是新旧序。
    sec = time.strftime('%Y%m%d-%H%M%S')
    ms = int(time.time() * 1000) % 100000
    rid = '%s-%d' % (sec, ms)
    while (q / (rid + '.json')).exists():
        ms += 1
        if ms >= 100000:
            ms = 0
            sec = time.strftime('%Y%m%d-%H%M%S')
        rid = '%s-%d' % (sec, ms)
    _atomic_json(q / (rid + '.json'), rep)
    _prune(q)
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
# 抓取入口：HTTP 处理器 / 前端 JS / 原生崩溃残留
# --------------------------------------------------------------------------

def capture_exc(where, include_paths=None):
    """在 except 块里调用：把当前异常记成一份报告（不受线程归属限制）。

    QuietServer.handle_error 就是靠它接住 50+ 个 /api 处理器的异常 ——
    那条路径上异常被 socketserver 吞掉，sys.excepthook 根本不会触发。
    """
    t, v, tb = sys.exc_info()
    if t is None:
        return ''
    try:
        if include_paths is None:
            include_paths = load_conf()['include_paths']
        rid = _on_error(str(where or 'except')[:80], t, v, tb)
        return rid or ''
    except Exception as e:
        _log('capture failed: %r' % (e,))
        return ''


def capture_api(path):
    """HTTP 处理器异常。查询串一律砍掉 —— /api/translate 的 q 里就是用户原文。"""
    p = str(path or '').split('?')[0].strip()
    return capture_exc('api:' + (p[:60] or '?'))


def save_js(payload):
    """前端异常（window.onerror / unhandledrejection / ErrorBoundary）。

    只收 message、stack 前若干帧、当前面板与版本；不收任何输入框内容。
    """
    d = payload if isinstance(payload, dict) else {}
    conf = load_conf()
    inc = conf['include_paths']
    msg = scrub(str(d.get('message') or '')[:600], inc)
    stack = scrub(str(d.get('stack') or '')[:3000], inc)
    route = re.sub(r'[^A-Za-z0-9_\-/.#]', '', str(d.get('route') or ''))[:40]
    kind = re.sub(r'[^A-Za-z0-9_.]', '', str(d.get('kind') or 'JsError'))[:40] or 'JsError'
    if not msg and not stack:
        return ''
    rep = {
        'v': 1,
        'app': 'SecondBrain',
        'version': _CTX['version'],
        'at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'where': ('web:' + route) if route else 'web',
        'kind': kind,
        'message': msg,
        'trace': stack,
        'sig': _signature(kind, 'web:' + route, stack),
        'count': 1,
        'breadcrumb': _breadcrumb(),
        'env': {
            'os': platform.system() + ' ' + platform.release(),
            'py': platform.python_version(),
            'ui': 'webview',
            **_sys_info(),
            **_app_env(),
        },
        'note': '前端异常：不含输入内容；route 只是当前面板名',
    }
    rid = save_report(rep)
    try:
        if conf['auto'] and endpoint():
            _bg_send()
    except Exception:
        pass
    return rid


# --------------------------------------------------------------------------
# 运行标记：原生崩溃 / 杀进程 / 断电也要留一份现场
# --------------------------------------------------------------------------

def _run_path():
    d = _CTX['dir']
    return (d / RUN_NAME) if d else None


def _run_write(stage, clean=False, extra=None):
    p = _run_path()
    if p is None:
        return False
    now = time.time()
    start = now
    try:
        if p.is_file():
            old = json.loads(p.read_text(encoding='utf-8'))
            # 只有同一个进程（同一 pid）才继承 start；上一次是别的 pid（崩溃残留）
            # 时不能继承，否则重启后的 uptime 会把「上一轮跑了多久」算进来。
            if isinstance(old, dict) and old.get('start') and old.get('pid') == os.getpid():
                start = float(old['start'])
    except Exception:
        pass
    obj = {'v': 1, 'pid': os.getpid(), 'start': start, 'at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'stage': str(stage)[:20], 'clean': bool(clean), 'ver': _CTX['version'],
           'uptime_s': int(now - start)}
    if isinstance(extra, dict):
        obj.update({k: v for k, v in list(extra.items())[:6]
                    if isinstance(v, (str, int, float, bool))})
    try:
        _atomic_json(p, obj)
        return True
    except Exception:
        return False


def arm_run_guard():
    """真正的 UI 进程启动时调用一次：先检查上次是否异常退出，再写本次标记。

    只有 app_window.main 会调它 —— 测试 / CLI / env_check 导入 brain 时不该
    往数据目录写运行标记，否则每次跑测试都会制造一条「上次异常退出」。
    """
    _CTX['guard'] = True
    try:
        rid = check_prev_run()
        mark_run_start()
        return rid
    except Exception as e:
        _log('run guard failed: %r' % (e,))
        return ''


def mark_run_start(stage='boot'):
    if not _CTX['guard']:
        return False
    return _run_write(stage, clean=False)


def mark_stage(stage):
    """启动阶段推进：boot → ui → ready。崩溃报告里能看出死在哪一步。"""
    if not _CTX['guard']:
        return False
    return _run_write(stage, clean=False)


def mark_clean():
    """正常退出（托盘「退出」/ 关窗）时调用。"""
    if not _CTX['guard']:
        return False
    return _run_write('ready', clean=True)


def check_prev_run():
    """上次没写 clean 标记就没了 → 生成一份 AbnormalExit 报告。

    覆盖 sys.excepthook 抓不到的：ctypes 段错误、WebView2 渲染进程崩、
    任务管理器结束进程、断电。报告里带上次落盘的面包屑。
    """
    if not _CTX['guard'] or _CTX['prev_checked']:
        return ''
    _CTX['prev_checked'] = True
    p = _run_path()
    if p is None or not p.is_file():
        return ''
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return ''
    if not isinstance(d, dict) or d.get('clean'):
        return ''
    try:
        up = max(0, int(time.time() - float(d.get('start') or time.time())))
    except Exception:
        up = 0
    stage = str(d.get('stage') or '?')[:20]
    msg = ('上次运行未正常退出：阶段 %s，pid %s，已运行 %ss'
           % (stage, d.get('pid', '?'), up))
    conf = load_conf()
    rep = {
        'v': 1,
        'app': 'SecondBrain',
        'version': _CTX['version'],
        'at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'where': 'crash-exit',
        'kind': 'AbnormalExit',
        'message': msg,
        'trace': '',
        'sig': _signature('AbnormalExit', 'crash-exit', ''),
        'count': 1,
        'breadcrumb': _prev_breadcrumb(),
        'env': {
            'os': platform.system() + ' ' + platform.release(),
            'py': platform.python_version(),
            'last_stage': stage,
            'last_pid': d.get('pid'),
            'last_uptime_s': up,
            'last_at': d.get('at', ''),
            'debug_tail': _debug_tail(conf['include_paths']),
            **_sys_info(),
            **_app_env(),
        },
        'note': '异常退出标记：进程没有走到正常退出路径（崩溃/被杀/断电），不含索引内容',
    }
    rid = save_report(rep)
    try:
        if conf['auto'] and endpoint():
            _bg_send()
    except Exception:
        pass
    return rid


def _prev_breadcrumb():
    """读上一次落盘的面包屑（当前进程内存里的是这次的，不能用）。"""
    try:
        import breadcrumb
        p = _run_path()
        if p is None:
            return []
        return breadcrumb.load(p.with_name(breadcrumb.PERSIST_NAME))[-MAX_BC:]
    except Exception:
        return []


# --------------------------------------------------------------------------
# 发送
# --------------------------------------------------------------------------

def _sys_proxy():
    """本机现在该走的代理（第 299 项：统一由 netproxy 决定）。

    none 模式下这里是空串 —— 用户明确选了「不使用代理」，上报也不该偷偷走代理。
    """
    try:
        return netproxy.proxy_url(True)
    except Exception:
        return ''


_OPENERS = {}


def _opener_for(url):
    """按目标决定走不走代理：本机地址永不代理。（保留给测试与手动排查用）"""
    host = ''
    try:
        host = urlparse(url).hostname or ''
    except Exception:
        pass
    local = host in ('127.0.0.1', 'localhost', '::1') or host.endswith('.local')
    proxy = '' if local else _sys_proxy()
    key = proxy or '-'
    op = _OPENERS.get(key)
    if op is None:
        if proxy:
            op = urllib.request.build_opener(
                urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
        else:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        _OPENERS[key] = op
    return op


def _post_once(url, payload, proxy='', timeout=None):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json; charset=utf-8',
                 # Cloudflare 对 Python 默认 UA 直接回 1010 Forbidden，这里的 UA 是通行证
                 'User-Agent': 'Memex/0.9 (Windows NT 10.0; Win64; x64)',
                 'Accept': 'application/json'},
        method='POST')
    if proxy:
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    else:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout or TIMEOUT) as r:
        return 200 <= r.status < 300


def _post(url, payload):
    """先直连，再（若有）走系统代理。

    普通用户没有代理，不能把「能发出去」建立在代理上：直连用短超时先试，
    不通再看本机有没有配代理（有才试，没有就当场失败，绝不拖时间）。
    本机地址（自建接收端）永不代理。
    """
    host = ''
    try:
        host = urlparse(url).hostname or ''
    except Exception:
        pass
    local = host in ('127.0.0.1', 'localhost', '::1') or host.endswith('.local')
    try:
        return _post_once(url, payload, '', timeout=DIRECT_TIMEOUT)
    except Exception as e:
        direct_err = e
    proxy = '' if local else _sys_proxy()
    if not proxy:
        raise direct_err
    return _post_once(url, payload, proxy, timeout=TIMEOUT)


def send_one(rid):
    conf = load_conf()
    eps = endpoints()
    if not eps:
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
    ok, err = False, ''
    for u in eps:          # 多个端点按顺序试，任一成功即算送达
        try:
            if _post(u, payload):
                ok, err = True, ''
                break
            err = '接收端返回非 2xx'
        except Exception as e:
            err = '%s: %s' % (type(e).__name__, str(e)[:160])
    if not ok:
        conf2 = load_conf()
        streak = conf2.get('fail_streak', 0) + 1
        wait = min(600.0, 30.0 * (2 ** (min(streak, 5) - 1)))
        save_conf({'last_error': err, 'fail_streak': streak,
                   'next_try': time.time() + wait,
                   'send_fail': conf2.get('send_fail', 0) + 1})
        _log('send failed: %s' % err)
        return False, err
    try:
        (_queue_dir() / (str(rid) + '.json')).unlink()
    except OSError:
        pass
    save_conf({**load_conf(), 'sent': conf['sent'] + 1, 'last_error': '',
               'fail_streak': 0, 'next_try': 0.0})
    return True, ''


def send_pending():
    """单次最多 MAX_BATCH 份；用锁保证不会起多个发送线程。

    第 277 项：失败退避。端点超时一次就退 30s，连续失败翻倍到 10 分钟，
    避免「每启动一次就重撞一遍」把配额和时间都耗在失败请求上。
    """
    if not _CTX['lock'].acquire(blocking=False):
        return {'ok': True, 'sent': 0, 'skipped': True}
    try:
        if not endpoint():
            return {'ok': False, 'sent': 0, 'err': '开发者尚未配置接收端'}
        conf = load_conf()
        left = conf.get('next_try', 0.0) - time.time()
        if left > 0:
            return {'ok': True, 'sent': 0, 'skipped': True,
                    'err': '退避中 %ds' % int(left)}
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
    threading.Thread(target=work, name='errlog-sender', daemon=True).start()# --------------------------------------------------------------------------
# 异常钩子
# --------------------------------------------------------------------------

def _on_error(where, exc_type, exc, tb):
    try:
        conf = load_conf()
        if not conf['collect']:
            return ''                    # 用户关掉了本机记录
        rep = build(where, exc_type, exc, tb, conf['include_paths'])
        rid = save_report(rep)
        _log('captured %s: %s' % (rep['kind'], rep['message'][:120]))
        if conf['auto'] and endpoint():
            _bg_send()
        return rid
    except Exception as e:
        _log('capture failed: %r' % (e,))
        return ''


class _StderrTee(object):
    """打包版没有控制台时把 stderr/stdout 接到 debug.log。

    PyInstaller --windowed 下 sys.stderr 是 None，第三方库（pywebview / sqlite /
    socketserver）的 traceback.print_exc() 会静默丢弃。接一条管道至少让它们
    进 debug.log，报告的 debug_tail 也能看到。
    """

    def __init__(self, path, tag='err'):
        self.path = path
        self.tag = tag

    def write(self, s):
        s = str(s or '')
        if not s.strip():
            return len(s)
        try:
            with open(self.path, 'a', encoding='utf-8', errors='replace') as f:
                f.write('[%s] %s%s' % (self.tag, s[:4000],
                                       '' if s.endswith('\n') else '\n'))
        except Exception:
            pass
        return len(s)

    def flush(self):
        return None

    def isatty(self):
        return False


def install_streams():
    """只在「没有控制台」时接管 stdout/stderr；有控制台时不抢。"""
    try:
        d = _CTX['dir']
        if not d:
            return False
        p = Path(d) / 'debug.log'
        changed = False
        if sys.stderr is None:
            sys.stderr = _StderrTee(p, 'err')
            changed = True
        if sys.stdout is None:
            sys.stdout = _StderrTee(p, 'out')
            changed = True
        return changed
    except Exception:
        return False


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


def bootstrap(data_dir, version='', log_fn=None, auto_flush=True, context_fn=None,
              guard=False):
    """主程序启动时调用一次：注入环境、挂钩子、补发上次没发出去的报告。

    guard=True 时另外做两件事（只有真正的 UI 进程该开，测试/CLI 不要开）：
    · 检查上次残留的运行标记 → 出一份 AbnormalExit 报告；
    · 写本次运行标记，正常退出时由 mark_clean() 收尾。
    """
    configure(data_dir, version, log_fn, context_fn=context_fn)
    install_streams()
    install()
    if guard:
        _CTX['guard'] = True
        try:
            rid = check_prev_run()
            mark_run_start()
            if rid:
                _log('prev run abnormal, report=%s' % rid)
        except Exception as e:
            _log('run guard failed: %r' % (e,))
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
    sigs = set()
    for it in items:
        try:
            d = get_report(it['id']) or {}
            s = str(d.get('sig') or '')
            if s:
                sigs.add(s)
        except Exception:
            pass
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
        'spool': {
            'count': len(items),
            'sigs': len(sigs),
            'send_fail': conf.get('send_fail', 0),
            'backoff_s': max(0, int(conf.get('next_try', 0.0) - time.time())),
            'run_guard': bool(_CTX.get('guard')),
        },
        'preview': preview,
        'note': '报告默认只保存在本机；接收端由开发者配置，用户端不显示也无法修改。发送内容可在下方完整预览。',
    }
