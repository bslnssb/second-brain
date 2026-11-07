# -*- coding: utf-8 -*-
"""批量 OCR 队列（Pro 第 2 个功能）。

为什么单独一个文件：brain.py 已经两万行，而队列是个**有状态的状态机**
（暂停 / 取消 / 断点续跑 / 给大任务让路），值得能脱离窗口、脱离数据库、
脱离 OCR 引擎单独测试。所以这里只管队列：不 import brain、不碰 SQLite、
不调 RapidOCR，也不写任何本机绝对路径。

所有外部动作在构造时注入：

    runner(path, on_page) -> {'text':..., 'err':..., 'skip':...}
        做 OCR。on_page(i, total) 用来报「第几页 / 共几页」。
    sink(path, text)      -> None 或错误字符串：把识别结果写进搜索索引。
    expand(dir)           -> 该目录下待处理的文件路径列表。
    allowed(path)         -> 是否是可处理的文件（扩展名 / 体积 / 存在性）。
    may_run()             -> False 时让路**等待**（比如全量更新正在跑），不丢任务。
    authorized()          -> False 时**暂停**并提示（授权到期或被清除）。
    begin()               -> 工作线程开工前调一次（brain 侧缓存 path→rowid）。

持久化：注入的 state_path，每完成一篇原子写一次。程序重启后**恢复成 paused
而不是自动续跑** —— 用户没点「继续」就别偷偷把 CPU 吃满。
"""
from __future__ import annotations

import json
import os
import threading
import time

BLANK = {
    'v': 1,
    'run': 'idle',            # idle | running | paused | done
    'items': [],
    'current': '',
    'page_done': 0,
    'page_total': 0,
    'note': '',
    'started': 0,
    'finished': 0,
    'gen': 0,                 # 每次 clear +1：让还没退出的工作线程放弃写回
}

# 单次队列上限。选 2000 是想「误选了整个 D 盘」时界面还能停下来，
# 而不是排队排到明天；真有更多文件，分几批加即可。
DEFAULT_MAX = 2000
# status() 只回最近这么多条，避免一个几千项的队列把 GET 响应撑到几 MB
STATUS_TAIL = 120

_ST_DONE = ('done', 'failed', 'skipped')


def norm(p):
    return os.path.normcase(os.path.abspath(str(p or '')))


class Queue:
    def __init__(self, state_path, runner=None, sink=None, expand=None,
                 allowed=None, may_run=None, authorized=None, begin=None,
                 max_items=DEFAULT_MAX, sleep=None, clock=None, auto_start=True):
        self.path = os.fspath(state_path)
        self.runner = runner or (lambda p, cb: {'err': 'no runner'})
        self.sink = sink
        self.expand = expand or (lambda d: [])
        self.allowed = allowed or (lambda p: True)
        self.may_run = may_run
        self.authorized = authorized
        self.begin = begin
        self.max_items = int(max_items or DEFAULT_MAX)
        self.auto_start = bool(auto_start)
        self._sleep = sleep or time.sleep
        self._clock = clock or time.time
        self.lock = threading.RLock()
        self._thread = None
        self.st = self._load()
        with self.lock:
            self._save()

    # ---------------------------------------------------------------- 持久化
    def _blank(self):
        return dict(BLANK, items=[])

    def _load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            return self._blank()
        if not isinstance(d, dict) or d.get('v') != 1:
            return self._blank()
        out = self._blank()
        out['items'] = [
            {'p': str(x.get('p') or ''), 'st': str(x.get('st') or 'pending'),
             'chars': int(x.get('chars') or 0), 'pages': int(x.get('pages') or 0),
             'err': str(x.get('err') or '')}
            for x in (d.get('items') or [])
            if isinstance(x, dict) and x.get('p')
        ]
        out['started'] = float(d.get('started') or 0)
        out['finished'] = float(d.get('finished') or 0)
        out['gen'] = int(d.get('gen') or 0)
        # 崩溃/退出时线程没了，状态文件却可能停在 running。恢复为暂停，
        # 让用户明确点「继续」；正在处理那篇退回 pending，不会漏掉。
        if d.get('run') == 'running':
            out['run'] = 'paused'
            out['note'] = '上次运行中程序退出了，点「继续」从断点接着跑'
        elif d.get('run') in ('idle', 'paused', 'done'):
            out['run'] = d['run']
        for it in out['items']:
            if it['st'] == 'running':
                it['st'] = 'pending'
        return out

    def _save(self):
        """调用方必须已持 lock。写失败不能让整个队列崩掉（磁盘满 / 只读目录）。"""
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.st, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ---------------------------------------------------------------- 队列操作
    def add(self, paths=(), dirs=()):
        """返回加入的条数。目录先经 expand() 展开（由 brain 侧剪 junction / 限体积）。"""
        cand = []
        for d in (dirs or ()):
            try:
                cand.extend(self.expand(d) or [])
            except Exception:
                continue
        cand.extend(paths or ())
        with self.lock:
            seen = {norm(x['p']) for x in self.st['items']}
            room = max(0, self.max_items - len(self.st['items']))
            n = 0
            for raw in cand:
                if n >= room:
                    break
                p = str(raw or '').strip()
                if not p:
                    continue
                k = norm(p)
                if k in seen:
                    continue
                try:
                    if not self.allowed(p):
                        continue
                except Exception:
                    continue
                seen.add(k)
                self.st['items'].append(
                    {'p': p, 'st': 'pending', 'chars': 0, 'pages': 0, 'err': ''})
                n += 1
            if n:
                if self.st['run'] == 'done':
                    self.st['run'] = 'paused'
                self.st['note'] = ''
                self._save()
        # 首次加入就自动开跑；用户手动暂停过则尊重暂停，等他点「继续」。
        # auto_start=False 留给「先加好几个目录，最后再统一开始」和测试。
        if n and self.auto_start and self.st['run'] == 'idle':
            self.resume()
        return n

    def pause(self):
        with self.lock:
            if self.st['run'] == 'running':
                self.st['run'] = 'paused'
                self.st['note'] = '已暂停，当前这篇跑完就停'
                self._save()
            return True

    def resume(self):
        with self.lock:
            if self._alive():
                return True
            pending = [x for x in self.st['items'] if x['st'] == 'pending']
            if not pending:
                self.st['run'] = 'done'
                self.st['finished'] = self._clock()
                self.st['note'] = ''
                self._save()
                return True
            self.st['run'] = 'running'
            self.st['note'] = ''
            if not self.st['started']:
                self.st['started'] = self._clock()
            self.st['finished'] = 0
            self._save()
            gen = self.st['gen']
        t = threading.Thread(target=self._work, args=(gen,), daemon=True,
                             name='sb-ocr-batch')
        # 连点两次「继续」不能起两条线程：赋值和 start 都得在锁内，
        # 否则两边各自看到 _alive()==False，队列会被跑两遍。
        with self.lock:
            if self._alive():
                return True
            self._thread = t
            t.start()
        return True

    def cancel(self):
        """停止 + 把还没跑的标记成「已取消」；跑完的保留，可以再 retry。"""
        with self.lock:
            self.st['run'] = 'paused'
            self.st['note'] = '已取消，剩下的没有处理'
            self.st['current'] = ''
            for it in self.st['items']:
                if it['st'] == 'pending':
                    it['st'] = 'skipped'
                    it['err'] = 'cancelled'
            self._save()
        return True

    def retry_failed(self):
        with self.lock:
            n = 0
            for it in self.st['items']:
                if it['st'] in ('failed', 'skipped'):
                    it['st'] = 'pending'
                    it['err'] = ''
                    n += 1
            if n:
                self.st['run'] = 'paused'
                self.st['note'] = ''
                self._save()
        if n:
            self.resume()
        return n

    def clear(self):
        """清空并回到 idle。gen +1 是给还没退出的工作线程看的：它一发现
        代数变了就放弃写回，不会把刚清掉的条目又「复活」。"""
        with self.lock:
            gen = int(self.st['gen']) + 1
            fresh = self._blank()
            fresh['gen'] = gen
            self.st = fresh
            self._save()
        return True

    def status(self):
        with self.lock:
            items = self.st['items']
            n = len(items)
            done = sum(1 for x in items if x['st'] == 'done')
            failed = sum(1 for x in items if x['st'] == 'failed')
            skipped = sum(1 for x in items if x['st'] == 'skipped')
            processed = done + failed + skipped
            pct = round(processed * 100 / n) if n else 0
            cur = self.st.get('current') or ''
            return {
                'ok': True,
                'run': self.st['run'],
                'total': n,
                'done': done,
                'failed': failed,
                'skipped': skipped,
                'pending': n - processed,
                'pct': pct,
                'current': os.path.basename(cur),
                'current_path': cur,
                'page_done': self.st.get('page_done', 0),
                'page_total': self.st.get('page_total', 0),
                'note': self.st.get('note', ''),
                'started': self.st.get('started', 0),
                'finished': self.st.get('finished', 0),
                'items': [dict(x) for x in items[-STATUS_TAIL:]],
            }

    # ---------------------------------------------------------------- 工作线程
    def _alive(self):
        return bool(self._thread and self._thread.is_alive())

    def _on_page(self, i, total):
        with self.lock:
            self.st['page_done'] = max(0, int(i))
            self.st['page_total'] = max(0, int(total))

    def _work(self, gen):
        if self.begin:
            try:
                self.begin()
            except Exception:
                pass                     # 缓存拿不到就走慢路径，不该拖垮整个队列
        while True:
            with self.lock:
                if self.st['run'] != 'running' or self.st['gen'] != gen:
                    return
                it = next((x for x in self.st['items'] if x['st'] == 'pending'), None)
                if it is None:
                    self.st['run'] = 'done'
                    self.st['finished'] = self._clock()
                    self.st['current'] = ''
                    self.st['note'] = ''
                    self._save()
                    return
                it['st'] = 'running'
                it['err'] = ''
                self.st['current'] = it['p']
                self.st['page_done'] = 0
                self.st['page_total'] = 0
                self._save()

            path = it['p']
            if self.authorized and not self.authorized():
                with self.lock:
                    if self.st['gen'] == gen:
                        it['st'] = 'pending'
                        self.st['run'] = 'paused'
                        self.st['note'] = '授权已失效，任务已暂停'
                        self.st['current'] = ''
                        self._save()
                return
            if self.may_run and not self.may_run():
                with self.lock:
                    if self.st['gen'] == gen and self.st['run'] == 'running':
                        it['st'] = 'pending'
                        self.st['current'] = ''
                        # 让路可能持续几十分钟，别每 2 秒就把状态文件重写一遍
                        changed = not self.st['note'].startswith('有索引任务')
                        self.st['note'] = '有索引任务在跑，稍后自动继续'
                        if changed:
                            self._save()
                self._sleep(2)
                continue

            try:
                res = self.runner(path, self._on_page) or {}
            except Exception as e:
                res = {'err': str(e)}
            text = str(res.get('text') or '').strip()
            err = str(res.get('err') or '')
            skip = str(res.get('skip') or '')
            if text and not err and self.sink:
                try:
                    err = self.sink(path, text) or ''
                except Exception as e:
                    err = str(e)

            with self.lock:
                if self.st['gen'] != gen:
                    return                    # clear() 过了，状态已被整份替换
                if skip and not text and not err:
                    it['st'], it['err'] = 'skipped', skip
                elif err or not text:
                    it['st'], it['err'] = 'failed', err or '没识别出文字'
                else:
                    it['st'], it['err'] = 'done', ''
                it['chars'] = len(text)
                it['pages'] = int(self.st.get('page_done') or 0)
                self.st['current'] = ''
                self.st['page_done'] = 0
                self.st['page_total'] = 0
                if self.st['run'] == 'running' and self.st['note'].startswith('有索引任务'):
                    self.st['note'] = ''
                self._save()
