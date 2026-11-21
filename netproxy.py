# -*- coding: utf-8 -*-
"""第 299 项：网络代理的唯一出口。

以前每个模块各自读注册表 / 各自缓存（translator 有一套、error_report 又抄一份），
用户在设置里也改不了 —— 想直连只能去手改 settings.json 的 trans_proxy。
现在统一成三种模式，全程序共用一份：

    system  跟随 Windows 系统代理（Clash/V2Ray 打开「系统代理」就写注册表）
    none    永远直连（不查注册表、也不吃环境变量）
    custom  用自己填的 host:port（允许 http://user:pass@host:port 这种整串）

约定：
- 设置存在 settings.json：proxy_mode / proxy_host / proxy_port；
  老的 trans_proxy 仍然认（不能让升级把老用户的代理弄丢）。
- 模块要发外部请求就调 `netproxy.urlopen()` / `netproxy.opener()`；
  本机 127.0.0.1 的接口一律不要走这里（Everything、自己的 HTTP 服务都直连）。
- 改完设置调 `refresh()`：清缓存 + 同步环境变量，下一次请求立刻生效。
"""
import os
import socket
import threading
import time
import urllib.error
import urllib.request

MODES = ('system', 'none', 'custom')
DEFAULT_PORT = 7890
DEFAULT_HOST = '127.0.0.1'
# 探测用：这个端点回 204、不带正文，判断"通不通"最省流量
TEST_URL = 'https://www.gstatic.com/generate_204'
_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Memex/1'

_LOCK = threading.Lock()
_CACHE = {'at': 0.0, 'key': None, 'url': ''}
_TTL = 5.0
# 第 341 项：代理「端口都连不上」时自动退回直连。
# 用户常见两种环境：① 没梯子（system 模式但注册表里没有代理）；② Clash 关了但系统代理还留着 ——
# 后者以前会让每一次外网请求都砸在一个死端口上，翻译面板整片「无结果」。
# 这里只做一次 TCP 连接探测（毫秒级、不发请求），死了就当没有代理，返回直连。
_ALIVE = {'url': '', 'ok': True, 'at': 0.0}
_ALIVE_TTL = 45.0
_BYPASS = {'url': '', 'at': 0.0}


def _settings():
    try:
        import brain
        return brain._settings_read() or {}
    except Exception:
        return {}


def _norm_mode(raw):
    m = str(raw or '').strip().lower()
    return m if m in MODES else ''


def _legacy(d):
    """老配置（没写过 proxy_mode）：把 trans_proxy 当等价设置读。

    'none'/'direct'/'off' → 直连；'auto'/空 → 跟随系统；其余 → 自定义地址。
    """
    raw = str(d.get('trans_proxy') or '').strip()
    if not raw or raw.lower() == 'auto':
        return ('system', '', 0)
    if raw.lower() in ('none', 'direct', 'off', 'direct://'):
        return ('none', '', 0)
    return ('custom', raw, 0)


def mode_of(d=None):
    d = _settings() if d is None else d
    m = _norm_mode(d.get('proxy_mode'))
    return m or _legacy(d)[0]


def system_proxy():
    """Windows「Internet 选项」里的代理 → http://host:port；没开返回空串。"""
    if os.name != 'nt':
        return ''
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r'Software\Microsoft\Windows\CurrentVersion\Internet Settings')
        try:
            enabled, _ = winreg.QueryValueEx(key, 'ProxyEnable')
        except OSError:
            enabled = 0
        if not int(enabled or 0):
            return ''
        try:
            server, _ = winreg.QueryValueEx(key, 'ProxyServer')
        except OSError:
            return ''
        raw = str(server or '').strip()
        if not raw:
            return ''
        # 可能是 "127.0.0.1:7897"，也可能是 "http=127.0.0.1:7897;https=127.0.0.1:7897"
        if '=' in raw and '://' not in raw:
            parts = {}
            for seg in raw.split(';'):
                if '=' in seg:
                    k, v = seg.split('=', 1)
                    parts[k.strip().lower()] = v.strip()
            raw = parts.get('https') or parts.get('http') or ''
        if not raw:
            return ''
        return raw if '://' in raw else 'http://' + raw
    except Exception:
        return ''


def build_url(host, port=None):
    """用户填的 host/port → 规范代理地址；不合法返回空串（由调用方报错）。"""
    raw = str(host or '').strip()
    if not raw:
        raw = DEFAULT_HOST
    if any(ch in raw for ch in (' ', '\t', '\r', '\n', '"', "'")):
        return ''
    scheme = 'http'
    rest = raw
    if '://' in raw:
        scheme, rest = raw.split('://', 1)
        scheme = scheme.lower()
        if scheme not in ('http', 'https'):
            return ''
    rest = rest.strip().strip('/')
    if not rest:
        return ''
    # 端口：host 里已经写了 1234 就用它，否则用表单里的 port
    has_port = False
    tail = rest.rsplit('@', 1)[-1]
    if ':' in tail:
        after = tail.rsplit(':', 1)[1]
        has_port = after.isdigit()
    if not has_port:
        try:
            p = int(port)
        except Exception:
            p = 0
        if not p:
            p = DEFAULT_PORT
        if not (1 <= p <= 65535):
            return ''
        rest = rest + ':' + str(p)
    return scheme + '://' + rest


def _url_for(mode, host='', port=None, d=None):
    if mode == 'none':
        return ''
    if mode == 'custom':
        return build_url(host or (d or {}).get('proxy_host'),
                         port if port is not None else (d or {}).get('proxy_port'))
    return system_proxy()


def _proxy_alive(url, timeout=1.2):
    """代理的 host:port 能不能连上（第 341 项）。连不上 = 这条梯子根本没在跑。"""
    host, port = _host_port(url)
    if not host:
        return True
    now = time.time()
    with _LOCK:
        if _ALIVE['url'] == url and now - _ALIVE['at'] < _ALIVE_TTL:
            return _ALIVE['ok']
    ok = True
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except Exception:
        ok = False
    with _LOCK:
        _ALIVE.update({'url': url, 'ok': ok, 'at': now})
        if not ok:
            _BYPASS.update({'url': url, 'at': now})
    return ok


def _host_port(url):
    try:
        from urllib.parse import urlsplit
        u = urlsplit(url if '://' in str(url) else 'http://' + str(url))
        host = u.hostname
        port = u.port or 80
        return (host, int(port)) if host else (None, 0)
    except Exception:
        return None, 0


def last_bypass():
    """最近 2 分钟内被判定为「连不上、已改用直连」的代理地址（空 = 没发生）。"""
    with _LOCK:
        if _BYPASS['url'] and time.time() - _BYPASS['at'] < 120:
            return _BYPASS['url']
    return ''


def proxy_url(force=False, d=None):
    """当前该用的代理（'' = 直连）。5 秒缓存：切「系统代理」后能跟上。"""
    d = _settings() if d is None else d
    m = mode_of(d)
    if m == 'custom':
        key = (m, str(d.get('proxy_host') or ''), str(d.get('proxy_port') or ''))
    else:
        key = (m,)
    now = time.time()
    with _LOCK:
        if not force and _CACHE['key'] == key and _CACHE['at'] and now - _CACHE['at'] < _TTL:
            return _CACHE['url']
    url = _url_for(m, d=d)
    # 第 341 项：配了却没在跑（Clash 关了、端口写错、VM 里那台代理不在了）→ 直接走直连，
    # 否则所有外网请求都砸在死端口上，界面只会显示「连不上 / 无结果」。
    if url and not _proxy_alive(url):
        url = ''
    with _LOCK:
        _CACHE.update({'at': now, 'key': key, 'url': url})
    return url


def describe(d=None):
    """给界面的只读快照：现在用哪种模式、实际走哪个代理、系统代理探测到什么。"""
    d = _settings() if d is None else d
    m = mode_of(d)
    url = proxy_url(True, d=d)
    return {'mode': m, 'url': url, 'system': system_proxy(),
            'host': str(d.get('proxy_host') or ''), 'port': d.get('proxy_port')}


def _make_opener(url, context=None):
    handlers = []
    if url:
        handlers.append(urllib.request.ProxyHandler({'http': url, 'https': url}))
    else:
        # 明确直连：环境变量里残留的 HTTP_PROXY 不该影响「不使用代理」
        handlers.append(urllib.request.ProxyHandler({}))
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


_OPENER = {'url': None, 'op': None, 'at': 0.0}


def opener(force=False, context=None):
    url = proxy_url(force)
    if context is None and not force and _OPENER['op'] and _OPENER['url'] == url \
            and time.time() - _OPENER['at'] < _TTL:
        return _OPENER['op']
    op = _make_opener(url, context)
    if context is None:
        _OPENER.update({'url': url, 'op': op, 'at': time.time()})
    return op


def urlopen(req, timeout=8, context=None):
    """外部 GET/POST 的统一入口（所有走系统/自定义代理的请求都从这出去）。"""
    return opener(context=context).open(req, timeout=timeout)


def refresh():
    """设置变更后调用：清缓存 + 同步环境变量（第三方库也就跟着变了）。"""
    with _LOCK:
        _CACHE.update({'at': 0.0, 'key': None, 'url': ''})
    _OPENER.update({'url': None, 'op': None, 'at': 0.0})
    apply_env()


def apply_env():
    """把模式同步成环境变量。

    - custom：写入 HTTP(S)_PROXY / ALL_PROXY，本机地址进 NO_PROXY；
    - none：删掉所有代理环境变量（并显式 NO_PROXY 本机）；
    - system：删掉环境变量，让 urllib 回落到读 Windows 注册表。
    """
    url = proxy_url(True)
    for k in ('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy',
              'ALL_PROXY', 'all_proxy'):
        os.environ.pop(k, None)
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost,::1'
    if url:
        for k in ('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy',
                  'ALL_PROXY', 'all_proxy'):
            os.environ[k] = url


def test(draft=None, target=TEST_URL, timeout=6.0):
    """按当前（或草稿）设置打一次 204，回来告诉界面通不通、用了多久。

    draft: {'mode','host','port'}，用于"还没保存就先测一下"。
    返回 {'ok','ms','via','mode','status','code'}；code 给前端翻成界面语言。
    """
    d = _settings()
    if draft:
        m = _norm_mode(draft.get('mode')) or mode_of(d)
        host = draft.get('host', d.get('proxy_host'))
        port = draft.get('port', d.get('proxy_port'))
    else:
        m = mode_of(d)
        host = d.get('proxy_host')
        port = d.get('proxy_port')
    if m == 'custom':
        url = build_url(host, port)
        if not url:
            return {'ok': False, 'code': 'bad', 'mode': m, 'via': '',
                    'status': 0, 'ms': 0}
    elif m == 'system':
        url = system_proxy()
    else:
        url = ''
    t0 = time.time()
    req = urllib.request.Request(target, headers={'User-Agent': _UA,
                                                  'Cache-Control': 'no-cache'})
    try:
        with _make_opener(url).open(req, timeout=timeout) as r:
            code, _ = r.status, r.read(64)
        return {'ok': True, 'code': '', 'mode': m, 'via': url,
                'status': int(code), 'ms': int((time.time() - t0) * 1000)}
    except socket.timeout:
        return {'ok': False, 'code': 'timeout', 'mode': m, 'via': url,
                'status': 0, 'ms': int((time.time() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        # 代理通了、但目标站点给了 4xx/5xx：链路本身是通的，如实回报状态码
        return {'ok': int(e.code) < 500, 'code': 'http', 'mode': m, 'via': url,
                'status': int(e.code), 'ms': int((time.time() - t0) * 1000)}
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        code = 'timeout' if 'timed out' in low else (
            'refused' if 'refused' in low or '10061' in msg else 'network')
        return {'ok': False, 'code': code, 'mode': m, 'via': url, 'status': 0,
                'ms': int((time.time() - t0) * 1000), 'err': msg[:120]}
