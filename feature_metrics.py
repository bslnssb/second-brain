#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机功能指标：只回答「哪个功能、失败多少次、多慢」，不记录任何内容。

为什么单独有这个文件（第 277 项）：

    错误日志只能抓到「抛到栈顶」的异常。Memex 里绝大多数失败是被**吞掉**的：
    翻译五家全超时 → 返回空列表；OCR 遇到空白页 → 返回空文本；索引跳过加密文件；
    剪贴板被别的程序占着 → 只在 debug.log 留一行。这些都不是异常，所以
    error_reports/ 里一份都没有 —— 用户说「翻译坏了」，我们手里只有一张截图。

    这里给每个功能一张表：calls / ok / fail / empty / timeout / skip / echo、
    耗时直方图（p50/p95 由直方图算，内存 O(1)）、最近一次错误摘要、24 小时窗口。
    写入点是功能自己的失败分支，不需要改异常模型。

隐私边界（和错误日志同一条线，比它更严）：

    · 只计数，不落任何文本、搜索词、文件路径、剪贴板内容；
    · 子计数器（subs）键名由调用方给（引擎 id / 原因枚举），截断到 24 字符；
    · 只在本机，不联网；文件在 app_dir() 下，上限约 64 KB。

接口（全部幂等、异常不外抛）：

    configure(data_dir, log_fn=None, autostart=False)
    add(feature, status='ok', ms=None, sub=None, err='')   status: ok/fail/empty/timeout/skip/echo
    count(feature, key, n=1)                                纯计数（缓存命中、冷却次数…）
    note(feature, err)                                      只更新「最近一次错误」
    timer(feature, sub=None)                                上下文管理器，自动记耗时
    snapshot(top=None)                                     给 /api/metrics 与错误报告用
    flush(force=False) / reset()                            落盘 / 测试用清空
"""
from __future__ import annotations

import atexit
import json
import os
import threading
import time
from pathlib import Path

CONF_NAME = 'feature_metrics.json'
FLUSH_SECS = 5.0
MAX_FEATURES = 16
MAX_SUBS = 12
HOURS = 24
# 直方图边界（毫秒）：不到 10ms 一路到 5 分钟，足够分辨「快 / 慢 / 卡死」
EDGES = (10, 30, 60, 120, 250, 500, 1000, 2000, 4000, 8000,
         15000, 30000, 60000, 300000)
NBINS = len(EDGES) + 1
STATUSES = ('ok', 'fail', 'empty', 'timeout', 'skip', 'echo')

_CTX = {
    'dir': None,
    'log': None,
    'lock': threading.RLock(),
    'dirty': False,
    'timer': None,
    'auto': False,
    'since': 0.0,
}

# feature -> 累计表
_LIFE = {}
# feature -> [[hour, calls, ok, fail, empty, timeout, skip, bin...], ...]（≤24 槽）
_HOUR = {}


# --------------------------------------------------------------------------
# 配置 / 落盘
# --------------------------------------------------------------------------

def configure(data_dir, log_fn=None, autostart=False):
    """注入数据目录。可重复调用（测试里会指向临时目录）。"""
    with _CTX['lock']:
        _CTX['dir'] = Path(data_dir) if data_dir else None
        _CTX['log'] = log_fn
        _CTX['auto'] = bool(autostart)
    _load()
    if autostart and not _CTX['timer']:
        _schedule()


def _path():
    d = _CTX['dir']
    return (d / CONF_NAME) if d else None


def _log(msg):
    fn = _CTX['log']
    if fn:
        try:
            fn('[metrics] %s' % msg)
        except Exception:
            pass


def _blank():
    return {s: 0 for s in STATUSES} | {'calls': 0, 'bins': [0] * NBINS,
                                      'last_err': '', 'last_at': '', 'subs': {}}


def _load():
    p = _path()
    _LIFE.clear()
    _HOUR.clear()
    if p is None:
        return
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    try:
        _CTX['since'] = float(raw.get('since') or 0)
    except Exception:
        _CTX['since'] = 0.0
    feats = raw.get('features') or {}
    if isinstance(feats, dict):
        for name, d in list(feats.items())[:MAX_FEATURES]:
            if not isinstance(d, dict):
                continue
            f = _blank()
            for s in STATUSES:
                try:
                    f[s] = max(0, int(d.get(s) or 0))
                except Exception:
                    f[s] = 0
            try:
                f['calls'] = max(0, int(d.get('calls') or 0))
            except Exception:
                f['calls'] = 0
            bins = d.get('bins')
            if isinstance(bins, list):
                for i in range(min(NBINS, len(bins))):
                    try:
                        f['bins'][i] = max(0, int(bins[i] or 0))
                    except Exception:
                        pass
            f['last_err'] = str(d.get('last_err') or '')[:160]
            f['last_at'] = str(d.get('last_at') or '')[:24]
            subs = d.get('subs')
            if isinstance(subs, dict):
                f['subs'] = {str(k)[:24]: _safe_int(v)
                             for k, v in list(subs.items())[:MAX_SUBS]}
            _LIFE[str(name)[:32]] = f
    hours = raw.get('hours') or {}
    if isinstance(hours, dict):
        for name, slots in list(hours.items()):
            if name not in _LIFE or not isinstance(slots, list):
                continue
            rows = []
            for s in slots[-HOURS:]:
                if not isinstance(s, list) or len(s) < 8:
                    continue
                try:
                    rows.append([int(s[0])] + [_safe_int(x) for x in s[1:8]]
                                + [_safe_int(x) for x in (s[8:8 + NBINS] or [0] * NBINS)])
                except Exception:
                    continue
            _HOUR[name] = _fix_bins(rows)


def _safe_int(v):
    try:
        return max(0, int(v))
    except Exception:
        return 0


def _fix_bins(rows):
    out = []
    for r in rows:
        bins = list(r[8:])[:NBINS]
        bins += [0] * (NBINS - len(bins))
        r = r[:8] + bins
        out.append(r)
    return out


def flush(force=False):
    """原子落盘；没有改动且非强制时直接返回。"""
    with _CTX['lock']:
        if not _CTX['dirty'] and not force:
            return False
        p = _path()
        if p is None:
            return False
        if not _CTX['since']:
            _CTX['since'] = time.time()
        data = {
            'v': 2,
            'since': _CTX['since'],
            'updated': time.time(),
            'features': _LIFE,
            'hours': _HOUR,
        }
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + '.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False,
                                      separators=(',', ':')), encoding='utf-8')
            os.replace(str(tmp), str(p))
            _CTX['dirty'] = False
            return True
        except Exception as e:
            _log('flush failed: %r' % (e,))
            return False


def _tick():
    try:
        flush()
    except Exception:
        pass
    finally:
        with _CTX['lock']:
            _CTX['timer'] = None
            again = _CTX['auto']
        if again:
            _schedule()


def _schedule():
    t = threading.Timer(FLUSH_SECS, _tick)
    t.daemon = True
    t.name = 'metrics-flush'
    with _CTX['lock']:
        _CTX['timer'] = t
    t.start()


def reset():
    """测试用：清空内存与计时器，不删磁盘文件。"""
    with _CTX['lock']:
        _LIFE.clear()
        _HOUR.clear()
        _CTX['dirty'] = False
        _CTX['since'] = 0.0
        t = _CTX['timer']
        _CTX['timer'] = None
        _CTX['auto'] = False
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 记录
# --------------------------------------------------------------------------

def _bucket(ms):
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return 0
    if v < 0:
        v = 0
    for i, edge in enumerate(EDGES):
        if v < edge:
            return i
    return NBINS - 1


def _hour_now():
    return int(time.time() // 3600)


def _entry(feature):
    name = str(feature or '')[:32] or 'misc'
    f = _LIFE.get(name)
    if f is None:
        if len(_LIFE) >= MAX_FEATURES:
            # 淘汰调用量最少的那张表，防止未知 feature 把文件撑大
            victim = min(_LIFE, key=lambda k: (_LIFE[k].get('calls') or 0, k))
            _LIFE.pop(victim, None)
            _HOUR.pop(victim, None)
        f = _blank()
        _LIFE[name] = f
    return name, f


def _hour_slot(feature):
    h = _hour_now()
    rows = _HOUR.setdefault(feature, [])
    for r in rows:
        if r[0] == h:
            return r
    # 槽位：0 小时 / 1 调用 / 2 ok / 3 fail / 4 empty / 5 timeout / 6 skip / 7 echo
    # 之后是 NBINS 个耗时桶
    row = [h] + [0] * 7 + [0] * NBINS
    rows.append(row)
    if len(rows) > HOURS:
        del rows[:len(rows) - HOURS]
    return row


def add(feature, status='ok', ms=None, sub=None, err=''):
    """记一次调用。status 不在 STATUSES 里时按 'ok' 计。"""
    try:
        st = str(status or 'ok').lower()
        if st not in STATUSES:
            st = 'ok'
        with _CTX['lock']:
            name, f = _entry(feature)
            f['calls'] += 1
            f[st] += 1
            b = _bucket(ms) if ms is not None else None
            if b is not None:
                f['bins'][b] += 1
            if err:
                f['last_err'] = str(err)[:160]
            elif st in ('fail', 'timeout'):
                f['last_err'] = f['last_err'] or st
            f['last_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            row = _hour_slot(name)
            row[1] += 1
            row[2 + STATUSES.index(st)] += 1
            if b is not None:
                row[8 + b] += 1
            if sub:
                _bump_sub(f, sub)
            _CTX['dirty'] = True
        return True
    except Exception as e:
        _log('add failed: %r' % (e,))
        return False


def _bump_sub(f, sub):
    key = str(sub)[:24]
    if not key:
        return
    subs = f['subs']
    if key not in subs and len(subs) >= MAX_SUBS:
        victim = min(subs, key=lambda k: (subs[k], k))
        subs.pop(victim, None)
    subs[key] = _safe_int(subs.get(key)) + 1


def count(feature, key, n=1):
    """纯计数器：缓存命中、冷却次数、跳过次数都能用。"""
    try:
        with _CTX['lock']:
            name, f = _entry(feature)
            _bump_sub(f, key)
            if n and int(n) > 1:
                f['subs'][str(key)[:24]] = f['subs'].get(str(key)[:24], 1) + int(n) - 1
            _CTX['dirty'] = True
        return True
    except Exception:
        return False


def note(feature, err):
    """只更新最近一次错误摘要，不动计数（给「已知会失败但不算失败」的场景）。"""
    try:
        with _CTX['lock']:
            name, f = _entry(feature)
            f['last_err'] = str(err)[:160]
            f['last_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            _CTX['dirty'] = True
        return True
    except Exception:
        return False


class timer(object):
    """with timer('translate', sub='zh'): ... —— 退出时按是否抛异常记 ok/fail。"""

    def __init__(self, feature, sub=None):
        self.feature = feature
        self.sub = sub
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, et, ev, tb):
        ms = (time.time() - self.t0) * 1000.0
        add(self.feature, 'fail' if et else 'ok', ms=ms, sub=self.sub,
            err=('%s: %s' % (et.__name__, ev))[:160] if et else '')
        return False


# --------------------------------------------------------------------------
# 读出
# --------------------------------------------------------------------------

def _pct(bins, q):
    total = sum(bins)
    if not total:
        return 0
    acc = 0
    for i, n in enumerate(bins):
        acc += n
        if acc >= total * q:
            return EDGES[i] if i < len(EDGES) else EDGES[-1]
    return EDGES[-1]


def _rate(f, keys=('fail', 'timeout', 'empty')):
    calls = f.get('calls') or 0
    if not calls:
        return 0.0
    return round(sum(f.get(k) or 0 for k in keys) / float(calls), 4)


def feature_stats(feature):
    with _CTX['lock']:
        f = _LIFE.get(str(feature)[:32])
        if not f:
            return {}
        out = {k: f.get(k) for k in STATUSES}
        out['calls'] = f.get('calls', 0)
        out['p50'] = _pct(f.get('bins') or [], 0.50)
        out['p95'] = _pct(f.get('bins') or [], 0.95)
        out['bad_rate'] = _rate(f)
        out['last_err'] = f.get('last_err', '')
        out['last_at'] = f.get('last_at', '')
        out['subs'] = dict(f.get('subs') or {})
        rows = _HOUR.get(str(feature)[:32]) or []
        h_calls = sum(r[1] for r in rows)
        h_bad = sum(r[3] + r[4] + r[5] for r in rows)  # fail / empty / timeout
        out['h24'] = {'calls': h_calls,
                      'bad': h_bad,
                      'bad_rate': round(h_bad / h_calls, 4) if h_calls else 0.0}
        return out


def snapshot(top=None):
    """给 /api/metrics 与错误报告用。top=N 时只带最需要看的几张表。"""
    with _CTX['lock']:
        names = list(_LIFE)
    rows = []
    for n in names:
        st = feature_stats(n)
        if not st:
            continue
        rows.append({'name': n, **st})
    if top:
        rows.sort(key=lambda r: (r['h24']['bad'], r['h24']['calls'], r['calls']),
                  reverse=True)
        rows = rows[:top]
    return {
        'ok': True,
        'since': time.strftime('%Y-%m-%dT%H:%M:%S',
                               time.localtime(_CTX['since'])) if _CTX['since'] else '',
        'features': rows,
        'note': '只统计调用次数与耗时，不含文本、路径、搜索词；只在本机',
    }


def failing(top=4):
    """错误报告里附的那一小段：只带 24h 内有失败的功能，字段很少。"""
    out = []
    for r in snapshot()['features']:
        if not r['h24']['bad']:
            continue
        out.append({
            'name': r['name'],
            'h24_calls': r['h24']['calls'],
            'h24_bad': r['h24']['bad'],
            'bad_rate': r['h24']['bad_rate'],
            'p95': r['p95'],
            'last_err': r['last_err'][:80],
        })
    out.sort(key=lambda x: x['h24_bad'], reverse=True)
    return out[:top]


def health_line():
    """体检面板用的一句话：有失败的功能按 bad_rate 排序。"""
    feats = [r for r in snapshot()['features'] if r['h24']['calls']]
    if not feats:
        return ''
    bad = [r for r in feats if r['h24']['bad']]
    if not bad:
        return 'ok'
    bad.sort(key=lambda r: r['h24']['bad_rate'], reverse=True)
    return '; '.join('%s %.0f%%(%d/%d)' % (r['name'], r['h24']['bad_rate'] * 100,
                                          r['h24']['bad'], r['h24']['calls'])
                     for r in bad[:3])


try:
    atexit.register(flush, True)
except Exception:                                     # pragma: no cover
    pass
