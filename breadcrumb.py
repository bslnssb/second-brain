#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动作面包屑：最近 40 个动作，供异常报告还原「崩溃前在干什么」。

为什么需要它（第 277 项）：

    error_report 里现有的 `debug_tail` 只取 debug.log 最后 40 行，而 debug.log
    的常规记录全是窗口显隐/标题栏拖拽；真正的用户动作（点了哪个面板、在哪个
    索引集里搜、切了什么目标语言）一条都没有。结果就是报告只有堆栈，没有现场。

    这里维护一个内存环形缓冲：每次动作只记「动作名 + 枚举值 + 时间」，异常报告
    时整段附上；进程有自己的退出路径时清空，被原生崩溃/杀进程带走时上一次
    落盘的副本就是唯一证据。

隐私边界：

    · 只收动作名与枚举（面板名、库名序号、语言代码、结果码）；
    · 字符串里带路径分隔符或查询符（`/ \\ : ? < >`）一律丢弃，长度截断到 24；
    · 不记搜索词、不记文件路径、不记剪贴板内容 —— 调用方也只该传枚举。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

BUF_MAX = 40
PERSIST_NAME = 'breadcrumb.json'
PERSIST_SECS = 3.0
MAX_VALUE = 24

_CTX = {
    'dir': None,
    'buf': [],
    'lock': threading.RLock(),
    'dirty': False,
    'timer': None,
}


def configure(data_dir=None, keep=False):
    """注入数据目录。keep=True 时保留内存里已有动作（主程序里重复调用）。"""
    with _CTX['lock']:
        _CTX['dir'] = Path(data_dir) if data_dir else None
        if not keep:
            _CTX['buf'] = []
            _CTX['dirty'] = False


def _safe_value(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        try:
            f = float(v)
            if f != f or f in (float('inf'), float('-inf')):
                return None
            return round(f, 1) if f % 1 else int(f)
        except Exception:
            return None
    s = str(v or '').strip()
    if not s:
        return None
    if any(c in s for c in '/\\:?<>|"'):
        return None                      # 像路径 / URL / 带查询串的一律丢
    # 只收枚举形态（字母数字下划线点横线）。带空格或中文的一律丢 ——
    # 搜索词、文件名、剪贴板内容都属于这一侧，不能因为调用方手滑就落盘。
    if not re.fullmatch(r'[A-Za-z0-9_.\-]{1,24}', s):
        return None
    return s[:MAX_VALUE]


def add(action, **kv):
    """记一个动作。action 只该是短枚举（'search' / 'panel' / 'translate'…）。"""
    try:
        a = str(action or '').strip()[:24]
        if not a:
            return False
        item = {'t': time.strftime('%H:%M:%S') + ('.%03d' % (int(time.time() * 1000) % 1000)),
                'a': a}
        for k, v in list(kv.items())[:6]:
            key = str(k or '').strip()[:16]
            if not key:
                continue
            val = _safe_value(v)
            if val is not None:
                item[key] = val
        with _CTX['lock']:
            _CTX['buf'].append(item)
            if len(_CTX['buf']) > BUF_MAX:
                del _CTX['buf'][:len(_CTX['buf']) - BUF_MAX]
            _CTX['dirty'] = True
            need_timer = _CTX['timer'] is None and _CTX['dir'] is not None
            if need_timer:
                t = threading.Timer(PERSIST_SECS, _tick)
                t.daemon = True
                t.name = 'breadcrumb-persist'
                _CTX['timer'] = t
                t.start()
        return True
    except Exception:
        return False


def _tick():
    try:
        persist()
    finally:
        with _CTX['lock']:
            _CTX['timer'] = None
            again = _CTX['dirty'] and _CTX['dir'] is not None
            if again:
                t = threading.Timer(PERSIST_SECS, _tick)
                t.daemon = True
                t.name = 'breadcrumb-persist'
                _CTX['timer'] = t
                t.start()


def snapshot(limit=BUF_MAX):
    with _CTX['lock']:
        rows = list(_CTX['buf'])
    return rows[-limit:] if limit else rows


def _path():
    d = _CTX['dir']
    return (d / PERSIST_NAME) if d else None


def persist(extra=None):
    """原子写盘。extra 里的标量会并进同一个文件（崩溃标记用）。"""
    with _CTX['lock']:
        items = list(_CTX['buf'])
    p = _path()
    if p is None:
        return False
    obj = {'v': 1, 'saved': time.strftime('%Y-%m-%dT%H:%M:%S'), 'items': items}
    if isinstance(extra, dict):
        for k, v in list(extra.items())[:12]:
            if isinstance(v, (str, int, float, bool)) or v is None:
                obj[str(k)[:24]] = v
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + '.tmp')
        tmp.write_text(json.dumps(obj, ensure_ascii=False,
                                  separators=(',', ':')), encoding='utf-8')
        os.replace(str(tmp), str(p))
        with _CTX['lock']:
            _CTX['dirty'] = False
        return True
    except Exception:
        return False


def load(path=None):
    """读上一次落盘的面包屑（崩溃报告用）。"""
    p = Path(path) if path else _path()
    if p is None:
        return []
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return []
    items = raw.get('items') if isinstance(raw, dict) else None
    return items[-BUF_MAX:] if isinstance(items, list) else []


def clear():
    with _CTX['lock']:
        _CTX['buf'] = []
        _CTX['dirty'] = False
        t = _CTX['timer']
        _CTX['timer'] = None
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass
    p = _path()
    if p is not None:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
