#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA 套件 —— 上传前用来找 bug 和性能问题。

和 brain.py selftest 的区别：
  selftest 用合成语料验证「功能对不对」（检索质量、OCR、损坏文件），全程不碰真实库；
  qa_test 用真实库验证「会不会崩、会不会慢、会不会漏」——边界输入、并发、资源泄漏、
  越权访问、界面国际化完整性。

只读：不建索引、不写 brain.db（除了 init_db 本身的无害 DDL 幂等操作）。

用法：
    python qa_test.py                    # 用当前目录 brain.db
    python qa_test.py --db X.db --out 报告.md
    python qa_test.py --quick            # 跳过耗时项（并发/泄漏）
"""
import argparse
import gc
import http.client
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

RESULTS = []          # (分类, 名称, 状态, 细节, 耗时ms)
def rec(cat, name, st, detail='', ms=None):
    RESULTS.append((cat, name, st, detail, ms))
    mark = {'PASS': ' OK ', 'FAIL': 'FAIL', 'WARN': 'WARN', 'INFO': 'INFO'}[st]
    t = f' {ms:7.1f}ms' if ms is not None else ' ' * 10
    print(f'[{mark}]{t} {name}' + (f' —— {detail}' if detail else ''), flush=True)


# ---------------------------------------------------------------- 环境准备
def setup(db_path):
    import brain
    brain.DB = Path(db_path)
    return brain


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Client:
    """自带连接的 HTTP 客户端：绕过系统代理，避免接不到 127.0.0.1。"""
    def __init__(self, port):
        self.port = port
    def get(self, path, headers=None, timeout=30):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        try:
            h = {'Host': f'127.0.0.1:{self.port}'}
            h.update(headers or {})
            c.request('GET', path, headers=h)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()
    def post(self, path, obj, headers=None, timeout=30, raw=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        try:
            h = {'Host': f'127.0.0.1:{self.port}',
                 'Content-Type': 'application/json'}
            h.update(headers or {})
            body = raw if raw is not None else json.dumps(obj).encode()
            c.request('POST', path, body=body, headers=h)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()


# ---------------------------------------------------------------- A 检索边界
EDGE_CASES = [
    ('', '空查询'),
    ('   ', '纯空格'),
    ('a', '单个英文字符'),
    ('AI', '两个英文字符'),
    ('中', '单个汉字'),
    ('安全', '两个汉字'),
    ('!!!', '纯标点'),
    ('---', '纯连字符'),
    ('""', '一对引号'),
    ("'", '单引号'),
    ('%', '百分号'),
    ('%%', '双百分号'),
    ('_', '下划线'),
    ('a_', '字母+下划线'),
    ('C++', 'C++'),
    ('node.js', 'node.js'),
    ('a-b', '连字符词'),
    ('(括号)', '圆括号'),
    ('[方括号]', '方括号'),
    ('{花括号}', '花括号'),
    ('AND', 'FTS 关键字 AND'),
    ('OR NOT NEAR(...)', 'FTS 关键字组合'),
    ('*', '星号'),
    ("'; DROP TABLE docs;--", 'SQL 注入尝试'),
    ('" OR 1=1--', 'SQL 注入变体'),
    ('<script>alert(1)</script>', 'XSS 载荷'),
    ('😀', 'emoji'),
    ('ＡＩ', '全角字母'),
    ('AI 安全', '中英混合多词'),
    ('很长的查询词' * 3, '长中文词'),
    ('x' * 500, '500 字符'),
    ('y' * 5000, '5000 字符'),
    ('a\nb\tc', '换行与制表符'),
    (' 前后有空格 ', '首尾空格'),
]


def test_search_edges(brain, con):
    bad = []
    for q, label in EDGE_CASES:
        t0 = time.perf_counter()
        try:
            rows = brain.search_rows(con, q, 5)
            ms = (time.perf_counter() - t0) * 1000
            if not isinstance(rows, list):
                bad.append(f'{label}: 返回类型 {type(rows)}')
            elif rows and not (isinstance(rows[0], tuple) and len(rows[0]) == 2):
                bad.append(f'{label}: 行结构异常 {rows[0]!r}')
            elif any(r[0] is None for r in rows):
                bad.append(f'{label}: 出现 path=None')
            rec('A 检索边界', label, 'PASS', f'{len(rows)} 条', ms)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            bad.append(f'{label}: {type(e).__name__}: {e}')
            rec('A 检索边界', label, 'FAIL', f'{type(e).__name__}: {e}', ms)
    return bad


def test_wildcard_literal(brain, con):
    """LIKE 的通配符必须被转义。

    判据不能是「命中数应该很少」——`_` 在技术文档里真的很常见。正确的判据是
    「带 ESCAPE 的 LIKE」与「instr 字面匹配」在同一批数据上结果一致；没转义时
    两者会明显分叉（通配符会把整库都算进去）。"""
    esc = lambda t: t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    nul_docs = 0
    for t in ('_', '__', '%', '%%'):
        n_like = con.execute("SELECT count(*) FROM docs WHERE body LIKE ? ESCAPE '\\'",
                             (f'%{esc(t)}%',)).fetchone()[0]
        n_instr = con.execute('SELECT count(*) FROM docs WHERE instr(body, ?) > 0',
                              (t,)).fetchone()[0]
        diff_sets = []
        if n_like != n_instr:
            a = {p for (p,) in con.execute(
                "SELECT path FROM docs WHERE body LIKE ? ESCAPE '\\'", (f'%{esc(t)}%',))}
            b = {p for (p,) in con.execute(
                'SELECT path FROM docs WHERE instr(body, ?) > 0', (t,))}
            diff_sets = list(b - a)
            # 库里有 PDF 正文带 NUL 字节，SQLite 的 LIKE 遇到 NUL 会截断；
            # 这类文档是「历史数据」，不是转义失效。分别计数，别混成一个结论。
            nul_docs = max(nul_docs, sum(
                1 for p in diff_sets
                if con.execute('SELECT instr(body, char(0)) FROM docs WHERE path=?',
                               (p,)).fetchone()[0] > 0))
            real = [p for p in diff_sets
                    if con.execute('SELECT instr(body, char(0)) FROM docs WHERE path=?',
                                   (p,)).fetchone()[0] == 0]
        else:
            real = []
        rec('A 检索边界', f'通配符字面匹配 {t!r}',
            'PASS' if not real else 'FAIL',
            f'LIKE(ESCAPE) {n_like} 篇 / 字面 instr {n_instr} 篇'
            + (f'；差集 {len(diff_sets)} 篇全是含 NUL 的历史文档（LIKE 遇 NUL 截断）'
               if diff_sets and not real else '')
            + (f'；{len(real)} 篇是转义失效' if real else ''))
    n_nul = con.execute('SELECT count(*) FROM docs WHERE instr(body, char(0)) > 0').fetchone()[0]
    if n_nul:
        rec('A 检索边界', '库内含 NUL 字节的文档', 'WARN',
            f'{n_nul} 篇（历史数据，NUL 之后的内容对 1~2 字短词检索不可见；'
            f'提取器已加清洗，重新提取即可修复）')

    # 端到端：短词命中的结果里，必须真的含有这个词（而不是被通配符放进来）
    bad = 0
    for q in ('_', 'a_'):
        for p, _s in brain.search_rows(con, q, 10):
            body = con.execute('SELECT body FROM docs WHERE path=?', (p,)).fetchone()
            hit = (q.lower() in (body[0].lower() if body else '')) or (q.lower() in p.lower())
            if not hit:
                bad += 1
    rec('A 检索边界', '短词结果确实含该词', 'PASS' if bad == 0 else 'FAIL',
        f'{bad} 条结果不含查询词')


def test_clean_text(brain):
    """提取阶段必须清掉 NUL/控制字符：SQLite 的 LIKE 遇 NUL 就截断，后半篇会搜不到。"""
    dirty = '前\x00中\x01后\x1f尾'
    out = brain._clean_text(dirty) or ''
    ok = ('\x00' not in out and '\x01' not in out and '\x1f' not in out
          and '前' in out and '尾' in out)
    rec('A 检索边界', '提取时清洗 NUL/控制字符', 'PASS' if ok else 'FAIL', repr(out))
    keep = brain._clean_text('行1\n行2\t列\r\n')
    rec('A 检索边界', '保留换行与制表符', 'PASS' if keep == '行1\n行2\t列\r\n' else 'FAIL', repr(keep))
    sur = brain._clean_text('a\ud800b')
    try:
        sur.encode('utf-8')
        valid = True
    except UnicodeEncodeError:
        valid = False
    rec('A 检索边界', '孤立代理项被净化', 'PASS' if valid else 'FAIL',
        f'结果可 UTF-8 编码={valid}：{sur!r}')


def test_ext_and_path_filters(brain, con):
    cases = [('pdf', '正常扩展名'), ('.pdf', '带点扩展名'), ('PDF', '大写扩展名'),
             ('pdf ', '带空格'), ('', '空扩展名'), ("pdf' OR '1", '扩展名注入'),
             ('docx', 'docx')]
    for ext, label in cases:
        try:
            rows = brain.search_rows(con, '报表', 10, ext)
            rec('A 检索边界', f'类型过滤 {label}', 'PASS', f'{len(rows)} 条')
        except Exception as e:
            rec('A 检索边界', f'类型过滤 {label}', 'FAIL', f'{type(e).__name__}: {e}')

    for pf, label in [('Desktop', '正常路径'), ('ZZZ不存在', '不存在的路径'),
                      ('%', '百分号'), ('_', '下划线'), ('a' * 800, '超长路径')]:
        try:
            rows = brain.search_rows(con, '渗透', 10, None, pf)
            rec('A 检索边界', f'路径过滤 {label}', 'PASS', f'{len(rows)} 条')
        except Exception as e:
            rec('A 检索边界', f'路径过滤 {label}', 'FAIL', f'{type(e).__name__}: {e}')


# ---------------------------------------------------------------- B 性能
PERF_CASES = [
    ('渗透测试', '常见中文四字词（FTS）'),
    ('AI', '两字符（LIKE 全表降级）'),
    ('中', '单汉字（LIKE 全表降级）'),
    ('的', '高频单字（LIKE 最坏情况）'),
    ('网络安全等级保护', '长中文词（FTS）'),
    ('Windows', '常见英文词（FTS）'),
    ('Nmap 扫描', '中英混合（FTS 多词）'),
    ('zzzzqqqq不存在的词', '零命中（FTS）'),
    ('zzzz', '零命中（LIKE 全表扫描）'),
]


def test_perf(brain, con):
    rec('B 性能', '--- 检索延迟（真实库，各跑 3 次取中位）---', 'INFO')
    worst = 0
    for q, label in PERF_CASES:
        ts = []
        for _ in range(3):
            t0 = time.perf_counter()
            brain.search_rows(con, q, 30)
            ts.append((time.perf_counter() - t0) * 1000)
        med = sorted(ts)[1]
        worst = max(worst, med)
        st = 'PASS' if med < 1500 else ('WARN' if med < 4000 else 'FAIL')
        rec('B 性能', label, st, f'中位 {med:.0f}ms（范围 {min(ts):.0f}~{max(ts):.0f}）', med)
    rec('B 性能', '最慢查询', 'PASS' if worst < 2000 else 'WARN', f'{worst:.0f}ms')
    return worst


def test_perf_page(cli):
    t0 = time.perf_counter()
    st, body = cli.get('/')
    ms = (time.perf_counter() - t0) * 1000
    rec('B 性能', '首页（含库内篇数统计）', 'PASS' if ms < 300 else 'WARN',
        f'{len(body)/1024:.0f} KB / {ms:.1f}ms', ms)
    return body.decode('utf-8', 'replace')


# ---------------------------------------------------------------- C HTTP 层
def test_http_basic(cli):
    st, body = cli.get('/api/ping')
    ok = st == 200 and json.loads(body).get('app') == 'second-brain'
    rec('C HTTP', '/api/ping', 'PASS' if ok else 'FAIL', f'{st} {body[:60]!r}')

    t0 = time.perf_counter()
    st, body = cli.get('/')
    ms = (time.perf_counter() - t0) * 1000
    rec('C HTTP', '首页', 'PASS' if st == 200 and b'<html' in body.lower() else 'FAIL',
        f'{st}, {len(body)/1024:.0f} KB', ms)

    st, body = cli.get('/?lang=en')
    txt = body.decode('utf-8', 'replace')
    rec('C HTTP', '英文首页', 'PASS' if st == 200 and 'lang' in txt else 'FAIL',
        f'{st}, {len(body)/1024:.0f} KB')

    st, body = cli.get('/no/such/route')
    rec('C HTTP', '未知路由', 'PASS' if st == 200 else 'WARN',
        f'{st} —— 返回首页 HTML（单页应用可接受，但不是 404）')

    for path, label in [('/api?q=' + quote('渗透测试'), '正常检索'),
                        ('/api?q=', '空查询'),
                        ('/api?q=' + quote('中'), '中文 URL 编码'),
                        ('/api?q=' + quote('C++') + '&t=pdf&d=' + quote('Desktop'),
                         '三类参数同时使用')]:
        st, body = cli.get(path)
        try:
            n = len(json.loads(body).get('hits', []))
            rec('C HTTP', label, 'PASS' if st == 200 else 'FAIL', f'{st}, {n} 条')
        except Exception as e:
            rec('C HTTP', label, 'FAIL', f'{st}, 解析失败 {e}, body={body[:120]!r}')


def test_http_security(cli):
    st, body = cli.get('/api/ping', headers={'Host': 'evil.com'})
    rec('C HTTP', 'Host 伪造被拒', 'PASS' if st == 403 else 'FAIL', f'{st}')

    st, body = cli.get('/api/ping', headers={'Origin': 'http://evil.com'})
    rec('C HTTP', '跨站 Origin 被拒', 'PASS' if st == 403 else 'FAIL', f'{st}')

    st, body = cli.post('/api/config', {}, headers={'Content-Type': 'text/plain'})
    rec('C HTTP', 'POST 非 JSON 被拒', 'PASS' if st == 403 else 'FAIL', f'{st}')

    # CSRF 视角：<img src="..."> 这类跨站 GET 不带 Origin，只带 Sec-Fetch-Site: cross-site
    st, body = cli.get('/open?p=' + quote(str(HERE / 'qa_test.py')),
                       headers={'Sec-Fetch-Site': 'cross-site'})
    opened = json.loads(body).get('ok') if st == 200 else False
    rec('C HTTP', '跨站 GET /open 打开本地文件', 'FAIL' if opened else 'PASS',
        f'{st} ok={opened} —— 跨站请求不得触发 os.startfile')
    return bool(opened)


def test_open_endpoint(cli, indexed_path=None):
    """把 os.startfile 换成记录器，验证 /open 的授权范围（不真的启动程序）。"""
    calls = []
    orig = os.startfile
    os.startfile = lambda p: calls.append(p)
    try:
        if indexed_path:
            st, body = cli.get('/open?p=' + quote(str(indexed_path)))
            got = json.loads(body).get('ok')
            rec('C HTTP', '/open 打开索引内文件（正常用法）',
                'PASS' if got and calls else 'FAIL', f'ok={got}')

        n0 = len(calls)
        st, body = cli.get('/open?p=' + quote(str(HERE / 'qa_test.py')))
        rec('C HTTP', '/open 打开索引外的文件', 'PASS' if len(calls) == n0 else 'FAIL',
            f'{"仍可打开" if len(calls) > n0 else "已拒绝"}')

        st, body = cli.get('/open?p=' + quote('C:\\Windows\\System32\\calc.exe'))
        rec('C HTTP', '/open 指向任意 exe', 'PASS' if len(calls) == n0 else 'FAIL',
            f'{"仍可执行" if len(calls) > n0 else "已拒绝"} —— 否则就是个远程执行入口')

        st, body = cli.get('/open?p=' + quote(str(HERE / 'no-such-file.xyz')))
        rec('C HTTP', '/open 不存在的文件',
            'PASS' if json.loads(body).get('ok') is False else 'FAIL',
            f'ok={json.loads(body).get("ok")}')
    finally:
        os.startfile = orig
    return len(calls)


def test_concurrency(cli, port):
    """读操作在设计上共用一条连接（SQLite 一条连接也不能真并发），所以检索是串行的。
    真正要测的是：一个慢请求会不会把**不查库**的请求（状态轮询、心跳）也堵住。"""
    from concurrent.futures import ThreadPoolExecutor
    queries = ['渗透测试', '网络安全', 'Windows', 'Nmap 扫描', '应急预案']
    t0 = time.perf_counter()
    for q in queries:
        cli.get('/api?q=' + quote(q), timeout=60)
    seq = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(queries)) as ex:
        list(ex.map(lambda q: cli.get('/api?q=' + quote(q), timeout=60), queries))
    par = (time.perf_counter() - t0) * 1000
    rec('C HTTP', '5 并发 vs 5 串行', 'INFO',
        f'串行 {seq:.0f}ms / 并发 {par:.0f}ms = {par/max(seq,1):.2f}×（检索共用一条连接，按设计排队）',
        par)

    # 慢检索进行中，不查库的接口应该立刻回
    slow = '很长的查询词' * 400            # 约 400ms 的 LIKE 全表扫描
    th = threading.Thread(target=lambda: cli.get('/api?q=' + quote(slow), timeout=60))
    th.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    try:
        cli.get('/api/ping', timeout=5)
        ms = (time.perf_counter() - t0) * 1000
        rec('C HTTP', '慢检索时心跳仍响应', 'PASS' if ms < 200 else 'WARN',
            f'{ms:.0f}ms', ms)
    except Exception as e:
        rec('C HTTP', '慢检索时心跳仍响应', 'FAIL', f'{type(e).__name__}：整个服务被一个检索卡住')
    th.join(timeout=30)


def test_slow_client(port):
    """半开连接会不会把整个服务卡死（单线程 + 无 socket 超时）。"""
    s = socket.create_connection(('127.0.0.1', port), timeout=5)
    try:
        s.sendall(b'GET /api/ping HTTP/1.1\r\nHost: 127.0.0.1\r\n')   # 故意不发结尾空行
        time.sleep(0.6)
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        t0 = time.perf_counter()
        try:
            c.request('GET', '/api/ping', headers={'Host': f'127.0.0.1:{port}'})
            c.getresponse().read()
            ms = (time.perf_counter() - t0) * 1000
            rec('C HTTP', '半开连接时其他请求', 'PASS', f'仍可响应，{ms:.0f}ms', ms)
            blocked = False
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            rec('C HTTP', '半开连接时其他请求', 'FAIL',
                f'{type(e).__name__} 等了 {ms:.0f}ms —— 一个不发完请求的连接能把界面卡住', ms)
            blocked = True
        finally:
            c.close()
        return blocked
    finally:
        s.close()


def test_bad_post(cli):
    for raw, label, want in [(b'{not json', '垃圾 JSON', (200,)),
                             (b'', '空 body', (200,)),
                             (b'[1,2,3]', '数组而非对象', (400,)),
                             ('{"include_dirs": "不是数组"}'.encode(), '类型错误字段', (200,))]:
        try:
            st, body = cli.post('/api/config', None, raw=raw, timeout=15)
            rec('C HTTP', f'POST {label}', 'PASS' if st in want else 'FAIL',
                f'{st} {body[:60]!r}')
        except Exception as e:
            rec('C HTTP', f'POST {label}', 'FAIL',
                f'{type(e).__name__}: {e} —— 异常直接把连接掐断，前端只能看到 Failed to fetch')


# ---------------------------------------------------------------- D 资源泄漏
def count_connections():
    return sum(1 for o in gc.get_objects() if isinstance(o, sqlite3.Connection))


def test_leak(cli, n=200):
    import psutil
    p = psutil.Process()
    gc.collect()
    c0, h0, m0 = count_connections(), p.num_handles(), p.memory_info().rss
    for i in range(n):
        cli.get('/api?q=' + quote('渗透测试' if i % 2 else 'AI'), timeout=60)
    gc.collect()
    c1, h1, m1 = count_connections(), p.num_handles(), p.memory_info().rss
    dm = (m1 - m0) / 1048576
    rec('D 资源', f'{n} 次检索后 SQLite 连接数', 'PASS' if c1 - c0 <= 2 else 'FAIL',
        f'{c0} → {c1}（未关闭的连接会被 GC 兜底，但 fd 会先堆着）')
    rec('D 资源', f'{n} 次检索后句柄数', 'PASS' if h1 - h0 < 50 else 'WARN',
        f'{h0} → {h1}')
    rec('D 资源', f'{n} 次检索后内存', 'PASS' if dm < 200 else 'WARN', f'+{dm:.0f} MB')


# ---------------------------------------------------------------- E 界面 / i18n
def extract_dict(page):
    """从页面里按花括号深度抠出 DICT 字面量，返回 {lang: {k: v}}。"""
    i = page.find('const DICT')
    if i < 0:
        i = page.find('DICT=')
    j = page.find('{', i)
    depth, k = 0, j
    while k < len(page):
        ch = page[k]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                break
        k += 1
    src = page[j:k + 1]
    out = {}
    for lang in ('zh', 'en'):
        m = re.search(rf'{lang}\s*:\s*\{{', src)
        if not m:
            continue
        start = m.end() - 1
        depth, e = 0, start
        while e < len(src):
            if src[e] == '{':
                depth += 1
            elif src[e] == '}':
                depth -= 1
                if depth == 0:
                    break
            e += 1
        block = src[start:e + 1]
        keys = re.findall(r"(?:^|[{,])\s*([A-Za-z_][\w]*)\s*:", block)
        out[lang] = set(keys)
    return out


def test_i18n(page):
    d = extract_dict(page)
    zh, en = d.get('zh', set()), d.get('en', set())
    rec('E 界面', '字典键数', 'PASS' if zh and en else 'FAIL', f'zh {len(zh)} / en {len(en)}')
    missing = zh - en
    extra = en - zh
    rec('E 界面', '中英键对齐', 'PASS' if not missing and not extra else 'FAIL',
        f'英文缺 {sorted(missing)[:6]}；中文缺 {sorted(extra)[:6]}' if (missing or extra)
        else '完全一致')

    used = set(re.findall(r'data-i18n="([^"]+)"', page))
    by_js = set(re.findall(r"\bT\('([^']+)'\)", page))
    unknown = (used | by_js) - (zh | en)
    rec('E 界面', '页面引用的键都存在', 'PASS' if not unknown else 'FAIL',
        f'未定义：{sorted(unknown)[:8]}' if unknown else f'{len(used | by_js)} 个键全部有定义')

    # 英文模式下不应出现裸露中文。判据不是「这行有没有中文」（挂 data-i18n 的元素
    # 里本来就写着中文兜底文案，JS 一加载就替换掉），而是「这段中文有没有对应的字典值」。
    vals = set()
    for lang in ('zh', 'en'):
        m = re.search(rf'{lang}\s*:\s*\{{.*?\n\}};', page, re.S)
    zh_block = ''
    i = page.find('zh:{')
    if i >= 0:
        depth, e = 0, page.find('{', i)
        while e < len(page):
            if page[e] == '{':
                depth += 1
            elif page[e] == '}':
                depth -= 1
                if depth == 0:
                    break
            e += 1
        zh_block = page[i:e]
    vals = set(re.findall(r":'([^']*)'", zh_block))
    vals |= set(re.findall(r':"([^"]*)"', zh_block))

    leaks = []
    for ln in page.splitlines():
        s = ln.strip()
        if s.startswith(('//', '*', '/*')):
            continue
        code = re.sub(r'//.*$', '', ln)      # 行尾注释不会显示在界面上
        if not code.strip() or 'data-i18n' in code:
            continue
        for frag in re.findall(r'[\u4e00-\u9fff][^<>"\']{1,40}', code):
            if frag.strip() in ('中文', '切换语言'):   # 语言按钮与双语 tooltip，故意保留
                continue
            if any(frag.strip() in v for v in vals):
                continue
            leaks.append(frag.strip()[:40])
    rec('E 界面', '英文模式的裸露中文', 'PASS' if not leaks else 'FAIL',
        f'{len(leaks)} 处无字典覆盖：{leaks[:6]}' if leaks else '静态文案全部有字典覆盖')


# ---------------------------------------------------------------- F 库健康
def test_db_health(con):
    t0 = time.perf_counter()
    n = con.execute('SELECT count(*) FROM meta WHERE reason IS NULL').fetchone()[0]
    ms_fast = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    n2 = con.execute('SELECT count(*) FROM docs').fetchone()[0]
    ms_slow = (time.perf_counter() - t0) * 1000
    rec('F 库', '文档总数', 'INFO',
        f'{n:,} 篇；走 meta {ms_fast:.1f}ms，走 docs(全表) {ms_slow:.0f}ms '
        f'—— {ms_slow/max(ms_fast,0.01):.0f} 倍差距，页面每次加载都要这个数', ms_fast)
    if n != n2:
        rec('F 库', '两种计数一致', 'FAIL', f'meta {n} vs docs {n2}')
    else:
        rec('F 库', '两种计数一致', 'PASS', f'{n}')

    ok = con.execute('PRAGMA integrity_check').fetchone()[0]
    rec('F 库', 'integrity_check', 'PASS' if ok == 'ok' else 'FAIL', ok)

    con.execute("INSERT INTO docs(docs) VALUES('integrity-check')")
    con.rollback()
    rec('F 库', 'FTS5 integrity-check', 'PASS', '通过')

    # 注意：不能用「WHERE NOT EXISTS(SELECT 1 FROM docs WHERE docs.path=meta.path)」——
    # docs 是 FTS5 虚表且 path 未索引，这个相关子查询会退化成 8984 次全表扫描（卡死）。
    # 取两个集合在 Python 里比，O(n)。
    meta_rows = con.execute('SELECT path, reason FROM meta').fetchall()
    doc_paths = {r[0] for r in con.execute('SELECT path FROM docs')}
    from collections import Counter
    lost = Counter((r or '(成功)') for p, r in meta_rows if p not in doc_paths)
    rec('F 库', '未能入库的文件', 'INFO',
        '；'.join(f'{r} {c}' for r, c in lost.most_common()) or '无')

    orphan = sum(1 for p, r in meta_rows if r is None and p not in doc_paths)
    rec('F 库', '标记成功却没正文', 'PASS' if orphan == 0 else 'FAIL', f'{orphan} 条')

    # 正文体积按 200 篇抽样估算：对 FTS5 虚表做全表聚合要把 1.5 GB 正文全读一遍，
    # 那是分钟级的事，这个指标不值得。
    t0 = time.perf_counter()
    avg = con.execute('SELECT avg(length(body)), max(length(body)) FROM '
                      '(SELECT body FROM docs LIMIT 200)').fetchone()
    ms = (time.perf_counter() - t0) * 1000
    rec('F 库', '正文体积（200 篇抽样）', 'INFO',
        f'平均 {(avg[0] or 0)/1024:.0f} KB，最大样本 {(avg[1] or 0)/1024:.0f} KB', ms)


# ---------------------------------------------------------------- 报告
def write_report(path, meta):
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    groups = {}
    for cat, name, st, detail, ms in RESULTS:
        groups.setdefault(cat, []).append((name, st, detail, ms))
    n_pass = sum(1 for r in RESULTS if r[2] == 'PASS')
    n_fail = sum(1 for r in RESULTS if r[2] == 'FAIL')
    n_warn = sum(1 for r in RESULTS if r[2] == 'WARN')
    n_info = sum(1 for r in RESULTS if r[2] == 'INFO')

    lines = [f'# 第二大脑 QA 报告（{now}）', '']
    lines.append(f'**结论：{n_pass} 通过 / {n_fail} 失败 / {n_warn} 警告 / {n_info} 说明**')
    lines.append('')
    lines.append('> 由 `qa_test.py` 自动生成。用真实索引库跑，只读，不写库、不建索引。')
    lines.append('')
    lines.append('| 环境 | 值 |')
    lines.append('|---|---|')
    for k, v in meta.items():
        lines.append(f'| {k} | {v} |')
    lines.append('')

    for cat in sorted(groups):
        lines.append(f'## {cat}')
        lines.append('')
        lines.append('| 项目 | 结果 | 细节 | 耗时 |')
        lines.append('|---|---|---|---|')
        for name, st, detail, ms in groups[cat]:
            badge = {'PASS': '✅', 'FAIL': '❌', 'WARN': '⚠️', 'INFO': 'ℹ️'}[st]
            d = (detail or '').replace('|', '\\|').replace('\n', ' ')
            t = f'{ms:.0f}ms' if ms is not None else ''
            lines.append(f'| {name} | {badge} {st} | {d} | {t} |')
        lines.append('')

    problems = [r for r in RESULTS if r[2] in ('FAIL', 'WARN')]
    lines.append('## 待处理问题')
    lines.append('')
    if problems:
        for cat, name, st, detail, _ in problems:
            lines.append(f'- **[{st}] {cat} / {name}** —— {detail}')
    else:
        lines.append('无。')
    lines.append('')
    Path(path).write_text('\n'.join(lines), encoding='utf-8')
    print(f'\n报告已写入 {path}', flush=True)
    return n_fail, n_warn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(HERE.parent.parent / 'brain.db'))
    ap.add_argument('--out', default=str(HERE / 'QA_REPORT.md'))
    ap.add_argument('--quick', action='store_true')
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f'数据库不存在：{db}', flush=True)
        return 2

    brain = setup(db)
    con = brain.init_db()

    meta = {
        'Python': sys.version.split()[0],
        'SQLite': sqlite3.sqlite_version,
        '数据库': f'{db.name}，{db.stat().st_size / 1073741824:.2f} GB',
        'FTS5': 'trigram',
        '平台': sys.platform,
    }

    print('=== A 检索边界 ===', flush=True)
    bad = test_search_edges(brain, con)
    test_clean_text(brain)
    test_wildcard_literal(brain, con)
    test_ext_and_path_filters(brain, con)

    print('\n=== B 性能 ===', flush=True)
    test_perf(brain, con)

    print('\n=== F 库健康 ===', flush=True)
    test_db_health(con)

    port = free_port()
    srv = brain.make_server(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.6)
    cli = Client(port)
    print(f'\n=== C HTTP（端口 {port}）===', flush=True)
    try:
        test_http_basic(cli)
        page = test_perf_page(cli)
        print('\n=== E 界面 / i18n ===', flush=True)
        test_i18n(page)
        csrf = test_http_security(cli)
        try:
            sample = con.execute('SELECT path FROM meta WHERE reason IS NULL LIMIT 1').fetchone()
        except Exception:
            sample = None
        calls = test_open_endpoint(cli, sample[0] if sample else None)
        test_bad_post(cli)
        if not args.quick:
            test_concurrency(cli, port)
            print('\n=== D 资源泄漏 ===', flush=True)
            test_leak(cli)
        blocked = test_slow_client(port)
    finally:
        srv.shutdown()
        srv.server_close()
        con.close()

    n_fail, n_warn = write_report(args.out, meta)
    print(f'\n汇总：{n_fail} 失败 / {n_warn} 警告', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
