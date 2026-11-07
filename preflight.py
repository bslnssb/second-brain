# -*- coding: utf-8 -*-
"""打包前自检：把历次「打完才发现」的坑变成一条命令。

    python preflight.py                 # 静态检查 + 无 GUI 的桌面桥回归
    python preflight.py --live 8767     # 额外连一个已启动的服务跑接口矩阵/并发

不打包时也应该跑：发现问题改源码，比打完 180 MB 再重来便宜得多。
只检查，不改任何文件；FAIL 时退出码为 1。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT / 'release' / 'github'
results: list[tuple[str, str, str]] = []


def rec(level, name, detail=''):
    results.append((level, name, detail))
    print('  %-5s %-42s %s' % (level, name, detail))


def text(p: Path) -> str:
    return p.read_text(encoding='utf-8') if p.is_file() else ''


# ── 静态 ──────────────────────────────────────────────────────────────
def check_version():
    m = re.search(r"APP_VERSION\s*=\s*'v?([0-9.]+)'", text(ROOT / 'brain.py'))
    a = m.group(1) if m else ''
    m = re.search(r"^VERSION\s*=\s*'([0-9.]+)'", text(ROOT / 'setup_app.py'), re.M)
    b = m.group(1) if m else ''
    c = json.loads(text(ROOT / 'version.json') or '{}').get('version', '').lstrip('v')
    ok = a and a == b == c
    rec('PASS' if ok else 'FAIL', '版本号三处一致 (brain/setup/version.json)', '%s / %s / %s' % (a, b, c))


def check_i18n():
    s = text(ROOT / 'web/src/lib/i18n.ts')
    def keys(n):
        m = re.search(r'const %s\s*:\s*Dict\s*=\s*\{(.*?)\n\}' % n, s, re.S)
        return set(re.findall(r'^  ([A-Za-z0-9_]+)\s*:', m.group(1), re.M)) if m else set()
    zh = keys('ZH')
    bad = ['%s:%d' % (n, len(keys(n))) for n in ('EN', 'ES', 'PT', 'HI') if keys(n) != zh]
    rec('PASS' if zh and not bad else 'FAIL', '五语言字典键完全对齐', '%d 键 %s' % (len(zh), ' '.join(bad)))


def check_donate():
    s = text(ROOT / 'brain.py')
    m = re.search(r"DONATE_SHA256\s*=\s*'([0-9a-f]{64})'", s)
    want = m.group(1) if m else ''
    got = []
    for p in (ROOT / 'assets/donate.png', REPO / 'assets/donate.png'):
        got.append(hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'MISSING')
    ok = bool(want) and all(g == want for g in got)
    rec('PASS' if ok else 'FAIL', '赞赏码指纹 = 两张 donate.png', (want or '?')[:12])


def check_license():
    """离线授权（第 137 项）：公钥合法 + Ed25519 自检 + **私钥/签发工具没混进公开仓库**。

    最后一条是这个检查存在的全部理由：签发工具一旦进了 release/github，
    谁都能量产授权码，整套商业化就白做了 —— 它比任何功能都值得卡一道。
    顺带确认 license_core 的 Ed25519 没在某次重构里被改坏（拿 RFC 8032 向量验）。
    """
    bad = []
    m = re.search(r"LICENSE_PUBKEY_HEX\s*=\s*'([0-9a-f]{64})'", text(ROOT / 'brain.py'))
    if not m:
        bad.append('brain.py 缺合法 LICENSE_PUBKEY_HEX（要 64 位 hex）')
    else:
        try:
            if len(bytes.fromhex(m.group(1))) != 32:
                bad.append('公钥不是 32 字节')
        except ValueError:
            bad.append('公钥含非法 hex 字符')

    try:
        import license_core

        seed = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60')
        if license_core.public_key(seed).hex() != \
                'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a':
            bad.append('Ed25519 实现自检失败（RFC 8032 向量对不上）')
    except Exception as e:
        bad.append('license_core 导入/自检失败：%s' % e)

    # 公开仓库里绝对不能有的东西
    forbidden = {'make_license.py', 'seed.txt', 'pubkey.txt', 'license.json',
                 'licenses.tsv', 'private_key.txt'}
    for base in (REPO, REPO / 'tools', REPO / 'assets'):
        if not base.is_dir():
            continue
        for p in base.glob('*'):
            if p.is_file() and (p.name in forbidden or p.name.startswith('license-')
                                or p.suffix == '.pem'):
                bad.append('公开仓库里有 %s' % p.relative_to(REPO))

    # 只查真正的条目：清单的注释里正大光明写着「make_license.py 不进本仓库」，
    # 整段子串匹配会把这句提醒本身判成违规（和 CI 词表自指是同一类坑）。
    man = '\n'.join(l for l in text(REPO / 'tools/public_files.txt').splitlines()
                    if l.strip() and not l.lstrip().startswith('#'))
    for w in ('make_license', 'seed.txt', 'private', '.pem'):
        if w in man:
            bad.append('public_files.txt 的条目里出现了「%s」' % w)

    rec('PASS' if not bad else 'FAIL', '授权模块（公钥合法 / 私钥未入库）',
        '; '.join(bad) or ('公钥 %s…' % (m.group(1)[:12] if m else '?')))


def check_spec():
    need = ('win32file', 'pythoncom', 'win32com.client', 'pypinyin', 'hotkey', 'tray')
    bad = []
    for f in ('second-brain-app.spec', 'second-brain-core.spec'):
        s = text(REPO / f)
        miss = [x for x in need if "'%s'" % x not in s]
        if miss:
            bad.append('%s 缺 %s' % (f, ','.join(miss)))
    rec('PASS' if not bad else 'FAIL', 'spec 收齐函数内 import', '; '.join(bad))


def check_legacy():
    app = text(ROOT / 'app_window.py')
    body = app[app.index('def show_main('):app.index('def hide_main(')]
    # 说明文字里会引用旧写法当反面例子（docstring 里的 qEl.value=…;go()），
    # 不剥掉就会把注释误报成真代码——和 CI 词表自指是同一类坑。
    body = re.sub(r'"""[\s\S]*?"""', '', body)
    body = re.sub(r'#.*$', '', body, flags=re.M)
    bad = []
    if 'webbrowser.open' in body.replace('这里【绝不能】在出错时退回 webbrowser.open', ''):
        bad.append('show_main 仍有浏览器兜底')
    if 'qEl.value' in body or ';go()' in body:
        bad.append('show_main 仍注入旧模板 qEl/go()')
    if 'hidden=self.silent' in app:
        bad.append('主窗口仍 hidden 创建')
    if 'def _main_js(' in app:
        bad.append('_main_js 死代码复活')
    for f in (ROOT / 'web/src/App.tsx', ROOT / 'web/src/ui/context.tsx'):
        for w in ('NoticeBar', '/api/notices', 'sb_notice_seen'):
            if w in text(f):
                bad.append('%s 有公告残留 %s' % (f.name, w))
    rec('PASS' if not bad else 'FAIL', '旧模板/公告/浏览器兜底无残留', '; '.join(bad))


# 2026-11-06 全栈专家审查实证过的那条静默链：settings.json 并发写丢标记 →
# 半截 JSON 被 except: pass 吞掉 → 前端不检查 r.ok → cfg={} → 欢迎页每次弹且无报错。
# 下面这段子进程探针直接调 brain._settings_update，验证「原子 + 不丢键 + 损坏可恢复」，
# 不再只做字符串包含判断（以前这类检查对上面 5 个 bug 全 PASS）。
_SETTINGS_PROBE = r'''
import json, os, sys, threading
root, tmp = sys.argv[1], sys.argv[2]
os.environ['SB_DATA_DIR'] = tmp
os.environ['SB_NO_MAINT'] = '1'
os.environ['SB_NO_WATCH'] = '1'
sys.path.insert(0, root)
import brain
sp = brain._settings_path()
sp.write_text('{}', encoding='utf-8')
def w(i):
    ok, _ = brain._settings_update({'k%d' % i: i})
    if not ok: raise RuntimeError('write failed')
ts = [threading.Thread(target=w, args=(i,)) for i in range(40)]
for t in ts: t.start()
for t in ts: t.join()
d = json.loads(sp.read_text(encoding='utf-8'))
if not isinstance(d, dict) or any(d.get('k%d' % i) != i for i in range(40)):
    raise RuntimeError('concurrent writes lost keys')
sp.write_text('[]', encoding='utf-8')          # 模拟非原子写留下的非对象内容
ok, d2 = brain._settings_update({'welcome_seen': True})
if not ok or d2.get('welcome_seen') is not True:
    raise RuntimeError('broken settings.json not recovered')
if not isinstance(json.loads(sp.read_text(encoding='utf-8')), dict):
    raise RuntimeError('on-disk settings is not an object')
print('OK')
'''


def check_persistence():
    """欢迎页持久化：静态看关键实现点是否都在（防止有人改回去）。"""
    app = text(ROOT / 'app_window.py')
    brain = text(ROOT / 'brain.py')
    ob = text(ROOT / 'web/src/ui/onboarding.tsx')
    need = {
        'WebView2 持久化 profile': ('private_mode=False' in app),
        '读服务端 welcome_seen': ("jget<Settings>('/api/settings')" in ob),
        '写服务端 welcome_seen': ('welcome_seen: true' in ob),
        '请求检查 r.ok': ('if (!r.ok)' in ob),
        '关闭前 await 落盘': ('await markSeen()' in ob),
        '写失败有可见提示': ('markErr' in ob and 'ob_mark_err' in text(ROOT / 'web/src/lib/i18n.ts')),
        '建库失败独立错误态': ("phase === 'error'" in ob),
        'run 阶段不误写已读': ('close(false)' in ob),
        'settings 进程内锁': ('_SETTINGS_LOCK = threading.RLock()' in brain),
        'settings 原子替换': ('os.replace(tmp, sp)' in brain),
        'settings 读回校验': ('readback mismatch' in brain),
        'empty 不是首启终态': ("done not in ('error', 'empty')" in brain),
    }
    bad = [k for k, ok in need.items() if not ok]
    rec('PASS' if not bad else 'FAIL', '欢迎页标记 + 原子写静态检查',
        '缺失: ' + '、'.join(bad) if bad else '%d 项齐全' % len(need))


def check_settings_concurrent():
    """真起一个隔离子进程跑 40 线程并发写 + 损坏文件恢复，不碰真实 settings.json。"""
    tmp = ROOT / 'tmp' / 'preflight-settings'
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([sys.executable, '-c', _SETTINGS_PROBE, str(ROOT), str(tmp)],
                           capture_output=True, text=True, timeout=90, cwd=str(ROOT))
        ok = r.returncode == 0 and 'OK' in r.stdout
        detail = '' if ok else ((r.stdout + r.stderr).strip().splitlines() or ['?'])[-1][:150]
        rec('PASS' if ok else 'FAIL', 'settings.json 40 线程并发 + 损坏恢复', detail)
    except Exception as e:
        rec('FAIL', 'settings.json 40 线程并发 + 损坏恢复', repr(e)[:150])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_unknown_api():
    ok = "err': 'no such api'" in text(ROOT / 'brain.py')
    rec('PASS' if ok else 'FAIL', '未知 /api/ 返回 JSON 404 而非 HTML 首页', '')


def check_build_fresh():
    """改了 web/src 却忘了 npm run build —— 打包出来还是旧界面，最难查的一类。"""
    idx = ROOT / 'web/dist/index.html'
    if not idx.is_file():
        rec('FAIL', '前端已构建', 'web/dist/index.html 不存在')
        return
    src = ROOT / 'web/src'
    times = [p.stat().st_mtime for p in src.rglob('*')
             if p.is_file() and p.suffix in ('.ts', '.tsx', '.css') and 'test' not in p.name]
    times += [p.stat().st_mtime for p in (ROOT / 'web').iterdir()
              if p.is_file() and p.suffix in ('.ts', '.js', '.json', '.html', '.css')]
    newest = max(times + [0])
    built = idx.stat().st_mtime
    ok = built >= newest - 2
    rec('PASS' if ok else 'FAIL', 'web/dist 不比源码旧（防「改了没 build」）',
        '源码最后 %.0fs / 构建 %.0fs' % (newest % 100000, built % 100000) if not ok else '')


def check_sync():
    files = ('brain.py', 'app_window.py', 'web/src/App.tsx', 'web/src/ui/context.tsx',
             'web/src/ui/onboarding.tsx', 'web/src/lib/i18n.ts', '使用说明.md')
    diff = []
    for f in files:
        a, b = ROOT / f, REPO / f
        if not b.is_file():
            diff.append(f + ' (仓库缺失)')
        elif a.is_file() and hashlib.sha256(a.read_bytes()).digest() != hashlib.sha256(b.read_bytes()).digest():
            diff.append(f)
    rec('WARN' if diff else 'PASS', '开发根 ↔ 公开仓库源码',
        ('%d 个待同步，打包前跑 sync_public.py' % len(diff)) if diff else '已一致')


# ── 桌面桥（无 GUI）───────────────────────────────────────────────────
def check_bridge():
    sys.path.insert(0, str(ROOT))
    aw = importlib.import_module('app_window')
    log: list[str] = []
    aw._log = lambda m: None
    aw._log_always = lambda m: log.append('LOG ' + m.splitlines()[0])
    aw.webbrowser.open = lambda u, *a, **k: log.append('BROWSER ' + u)

    class W:
        def __init__(self, fail=False):
            self.fail, self.loaded, self.js = fail, [], []
        def show(self):
            if self.fail:
                raise RuntimeError('Main window failed to start')
        def load_url(self, u):
            self.loaded.append(u)
        def evaluate_js(self, s):
            self.js.append(s)

    def make(fail=False):
        a = aw.App.__new__(aw.App)
        a.url, a.hwnd_main, a.tray = 'http://127.0.0.1:8765', 1, None
        a.main = W(fail)
        a.show_mini = lambda: log.append('RECOVER_MINI')
        return a

    bad = []
    a = make()
    aw.App.show_main(a, '瑜伽')
    if not a.main.loaded or '/web/?q=' not in a.main.loaded[0]:
        bad.append('首次导航没带 q')
    aw.App.show_main(a, '周报')
    if not a.main.js or 'location.href' not in a.main.js[0]:
        bad.append('换词没走 location.href')
    n = len(a.main.js)
    aw.App.show_main(a, panel='settings')
    aw.App.show_main(a, panel='settings')
    if len(a.main.js) - n != 2:
        bad.append('同一 panel 连点没每次导航')
    log.clear()
    aw.App.show_main(make(fail=True), '瑜伽')
    if any(x.startswith('BROWSER') for x in log) or not any(x == 'RECOVER_MINI' for x in log):
        bad.append('显示失败时仍开浏览器/没恢复快搜窗')
    rec('PASS' if not bad else 'FAIL', 'show_main 四条回归断言', '; '.join(bad))


# ── 在线（可选）───────────────────────────────────────────────────────
def live(port):
    base = 'http://127.0.0.1:%d' % port
    h = {'Sec-Fetch-Site': 'same-origin', 'Origin': base}

    def req(path):
        r = urllib.request.Request(base + path, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=12) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            return -1, str(e).encode()

    gets = ['/api/ping', '/api/stats', '/api/config', '/api/donate', '/api/recent',
            '/api/timeline?days=7', '/api/settings', '/api/status', '/api/mini?q=report']
    bad = [p for p in gets if req(p)[0] != 200]
    rec('PASS' if not bad else 'FAIL', '只读接口矩阵', '异常: ' + ','.join(bad) if bad else '%d 个全 200' % len(gets))
    code, body = req('/api/notices')
    rec('PASS' if code == 404 and b'no such api' in body else 'FAIL', '已删接口返回 JSON 404', str(code))
    code, body = req('/api/open_url?u=https%3A%2F%2Fevil.example.com')
    rec('PASS' if b'not allowed' in body else 'FAIL', '打开链接白名单挡住外部站', str(code))
    # 缓存回归：同一个词 deep 三次，条数必须一致
    ns = []
    for _ in range(3):
        c, b = req('/api/mini?q=%E5%BD%92%E6%A1%A3&deep=1&body=1')
        try:
            ns.append(len(json.loads(b).get('items', [])))
        except Exception:
            ns.append(-1)
    rec('PASS' if len(set(ns)) == 1 and ns[0] >= 0 else 'FAIL', '缓存不吞正文命中（连搜 3 次）', str(ns))
    # 并发
    paths = ['/api/ping', '/api/stats', '/api/config', '/api/donate', '/api/mini?q=report']
    with ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(ex.map(lambda i: req(paths[i % len(paths)])[0], range(120)))
    nbad = sum(1 for c in codes if c != 200)
    rec('PASS' if nbad == 0 else 'FAIL', '8 线程 × 120 次混合请求', '非 200: %d' % nbad)
    rec('PASS' if req('/api/ping')[0] == 200 else 'FAIL', '压测后服务仍存活', '')


def check_tests():
    """打包门禁：后端 unittest + 前端 vitest 必须全绿，且不许「少跑文件还报绿」。

    2026-11-06 实测：沙箱里 vitest 多 worker 并发写缓存会 EPERM，
    "Test Files 1 passed" 却悄悄漏掉另外 3 个文件，退出码仍然是 0。
    所以除了 returncode，还要核对收集到的文件数 / 用例数下限。
    """
    ansi = re.compile(r'\x1b\[[0-9;]*m')
    jobs = (
        ('后端 unittest', [sys.executable, '-m', 'unittest', 'discover',
                           '-s', 'tests', '-p', 'test_*.py'], ROOT, False),
        ('前端 vitest', 'npm test', ROOT / 'web', True),
    )
    for name, cmd, cwd, shell in jobs:
        try:
            r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                               timeout=240, shell=shell)
            out = ansi.sub('', (r.stdout or '') + (r.stderr or ''))
            lines = [x.strip() for x in out.splitlines() if x.strip()]
            # 阈值 = 当前用例总数。以后每补一条测试都要把下限跟着往上调，
            # 否则「漏跑文件还报绿」又没人拦得住。unittest 有 skip 时末行是
            # "OK (skipped=1)"，不能用 'OK' in lines 精确判断。
            if name == '后端 unittest':
                m = re.search(r'Ran (\d+) tests', out)
                ok = (r.returncode == 0 and any(x.startswith('OK') for x in lines)
                      and m and int(m.group(1)) >= 92)
                want = '>= 92 项'
            else:
                mf = re.search(r'Test Files\s+(\d+) passed', out)
                mt = re.search(r'Tests\s+(\d+) passed', out)
                ok = (r.returncode == 0 and mf and mt and
                      int(mf.group(1)) >= 6 and int(mt.group(1)) >= 56)
                want = '>= 6 文件 / >= 56 用例'
            detail = '' if ok else '%s（要求 %s）' % (
                (lines[-1] if lines else '?')[:100], want)
            rec('PASS' if ok else 'FAIL', '测试全绿：' + name, detail)
        except Exception as e:
            rec('FAIL', '测试全绿：' + name, repr(e)[:140])


def check_white_screen():
    """2026-11-07：--silent 启动会 hide 主窗口，之后显示时 WebView2 渲染子窗口可能仍
    WS_VISIBLE=0 → 整块白窗口。根因是跨进程 ShowWindow 会被渲染层（msedgewebview2.exe）
    立刻重新藏回，所以必须在**第一个实例自己的线程**里 show() + revive。

    自检：revive 函数还在；show_main 调用它；第一个实例开了命名事件监听并收到信号后
    自己跑 show_main；第二个实例只 SetEvent 通知，不再跨进程 ShowWindow。"""
    s = text(ROOT / 'app_window.py')
    bad = []
    if 'def revive_webview(' not in s:
        bad.append('revive_webview 定义丢失')
    if 'EnumChildWindows' not in s or 'RedrawWindow' not in s:
        bad.append('枚举子窗口 / 强制重绘的实现丢失')
    i = s.find('def show_main(')
    j = s.find('def hide_main(', i + 1)
    if i < 0 or 'revive_webview(hwnd)' not in s[i:j if j > i else None]:
        bad.append('show_main 没有唤醒 WebView2 子窗口')
    if '_start_activate_listener' not in s or 'SecondBrain_Activate_v1' not in s:
        bad.append('第一个实例没有监听激活事件')
    else:
        m = s.find('def _start_activate_listener(')
        # 方法体内有嵌套的 def loop():，不能用 s.find('def ', m+1)（会停在 loop 上），
        # 要用「行首 4 空格 + def」定位下一个顶层方法，才能覆盖整个方法体。
        nxt = re.search(r'\n    def ', s[m + 1:])
        n = m + 1 + nxt.start() if nxt else len(s)
        if 'self.show_main()' not in s[m:n]:
            bad.append('激活监听器没调第一个实例自己的 show_main')
    k = s.find('def _activate_running(')
    if k < 0 or 'SecondBrain_Activate_v1' not in s[k:k + 700] \
            or 'OpenEventW' not in s[k:k + 700] \
            or 'SetEvent' not in s[k:k + 700]:
        bad.append('二次启动没用命名事件通知第一个实例')
    # 轮询式自愈（第 119 项）：只补一两拍不够——渲染层慢时固定拍数会刚好错开。
    # 这两个函数曾被文档写成"已做"、代码里却根本没有（show_main 里那行
    # `_late_revive_loop(self)` 直接 NameError，线程静默死掉），所以必须钉死。
    if 'def _late_revive_loop(' not in s:
        bad.append('_late_revive_loop 丢失（轮询自愈从未生效）')
    if 'def _render_children_visible(' not in s:
        bad.append('_render_children_visible 丢失（白屏判定失效）')
    i2 = s.find('def show_main(')
    j2 = s.find('def hide_main(', i2 + 1)
    if '_late_revive_loop(self)' not in s[i2:j2 if j2 > i2 else None]:
        bad.append('show_main 没起轮询自愈线程')
    # 2026-11-07 真根因：silent 启动时对"马上要 hide"的窗口调 resize()，
    # WinForms 的 Bounds 赋值会把刚 hide 的窗口顶回来，而渲染层不跟 → 白屏。
    # 同日第二轮改造（用户要求「别只修 150%，100% 的机器也得能跑」）：
    # 尺寸校正统一走 set_client_size（SetWindowPos，不改可见性），不再按 dpr 分叉，
    # 所以断言从「silent 别调」改成「谁都不许用 pywebview 的 resize 改尺寸」。
    a = s.find('def after_start(')
    b = s.find('def _start_activate_listener(', a + 1)
    body = s[a:b] if a >= 0 and b > a else ''
    if '_fit_main_size()' not in body:
        bad.append('after_start 没做尺寸校验（各档 DPI 会走偏）')
    if 'if not self.silent:' in body:
        bad.append('after_start 仍按 silent 分叉尺寸逻辑（100% 机器测不到另一条路）')
    fa = s.find('def _fit_main_size(')
    fb = s.find('def place_mini(', fa + 1)
    fbody = s[fa:fb] if fa >= 0 and fb > fa else ''
    if fa < 0 or 'set_client_size' not in fbody:
        bad.append('尺寸校正没用 set_client_size（应走 SetWindowPos，不动可见性）')
    if 'self.main.resize(' in fbody:
        bad.append('尺寸校正退回了 pywebview resize（会把隐藏窗口顶出来变白屏）')
    if 'needs_fit(' not in fbody:
        bad.append('尺寸校正缺少容差判定（100% 缩放下会被边框误差误触发）')
    nf = s.find('def needs_fit(')
    if nf < 0 or '24' not in s[nf:nf + 900] or '48' not in s[nf:nf + 900]:
        bad.append('needs_fit 的边框量级容差被改掉了（100% 机器会开始动窗口）')
    for fn in ('def set_client_size(', 'def calc_window_size(', 'def window_dpr('):
        if fn not in s:
            bad.append('%s 丢失（DPI 换算无法单测）' % fn.strip('def ('))
    if 'def _silent_hide_guard(' not in s:
        bad.append('静默启动缺少 hide 守卫')
    rec('PASS' if not bad else 'FAIL', '白屏自愈（第一个实例自己 show+revive）',
        '; '.join(bad))


def check_env_robustness():
    """跨环境健壮性（2026-11-07 第 131 项）：用户在别的机器上才会踩到的那几类坑。

    开发机只有一块 4K@150% 的屏、目录结构干净、库也没坏过，所以这几条只能靠
    静态断言 + 单测（tests/test_env_matrix.py / test_fs_safety.py / test_db_recovery.py）
    守住，一旦有人「简化」掉就又回到「用户那边打不开」。
    """
    s = text(ROOT / 'app_window.py')
    b = text(ROOT / 'brain.py')
    sc = text(ROOT / 'scan_docs.py')
    bad = []

    # ① 小屏 + 高缩放：窗口最小尺寸不能超过屏幕可用空间，否则标题栏出屏、关不掉。
    if 'def main_min_size(' not in s:
        bad.append('main_min_size 丢失（小屏上窗口会拖不动）')
    if 'min_size=main_min_size(' not in s:
        bad.append('create_window 没用自己的 min_size（又写死 520 了）')
    mf = s.find('def main_window_size(')
    mw = s[mf:mf + 2000] if mf >= 0 else ''
    if 'max(420' not in mw:
        bad.append('main_window_size 的高度下限回到 520（1366×768@150% 会超屏）')

    # ② 多显示器：快搜窗要跟着鼠标走，副屏在左/上时坐标是负数。
    for fn in ('def cursor_monitor_rect(', 'def mini_origin_in(', 'def _place_mini_on('):
        if fn not in s:
            bad.append('%s 丢失（副屏上快搜窗会弹在主屏）' % fn.strip('def ('))
    if 'if cur and prim and cur != prim:' not in s:
        bad.append('place_mini 没判断「鼠标在不在主屏」')

    # ③ 联接点：os.walk(followlinks=False) 挡不住 junction，会成环扫不完。
    if 'def _is_reparse_dir(' not in b:
        bad.append('_is_reparse_dir 丢失（junction 成环时索引永远跑不完）')
    if b.count('_is_reparse_dir(') < 3:
        bad.append('有 os.walk 没剪 junction（正文/首启/修复三条扫描路径要全覆盖）')
    if '_is_reparse_dir(' not in sc:
        bad.append('scan_docs 的正文扫描没剪 junction')

    # ④ 数据库损坏（断电/蓝屏）：要留档重建，而不是让所有接口 500。
    if 'def _open_db_guarded(' not in b or '_open_db_guarded()' not in b:
        bad.append('conn() 没走损坏保护（断电后程序会彻底打不开）')
    if "PRAGMA schema_version" not in b:
        bad.append('损坏探测没用「先探测再动文件」（Windows 上会 WinError 32）')

    # ⑤ 日志必须落在数据目录：从快捷方式/开机自启启动时 CWD 是 System32。
    lg = s.find('def _log(msg)')
    lb = s[lg:lg + 1200] if lg >= 0 else ''
    if 'open(\'debug.log\'' in lb or 'open("debug.log"' in lb:
        bad.append("_log 还在用相对路径 debug.log（开机自启时日志会丢）")

    # ⑥ 环境快照：用户报「窗口不对」时，日志里要有分辨率和缩放。
    if 'def env_snapshot(' not in s or '[env]' not in s:
        bad.append('启动没有环境快照（现场数据缺失，只能靠猜）')

    # ⑦ 同源校验的旧浏览器兜底：Sec-Fetch-Site 是 Chrome 76+ 才有的头，
    #    固定版本 WebView2 / IE 不发，严格判等会让所有接口 403（界面全空）。
    if 'def _same_origin_ok(' not in b:
        bad.append('同源校验没有 Referer 兜底（旧 WebView2 上接口会全 403）')
    if "Sec-Fetch-Site" not in b or 'Referer' not in b:
        bad.append('同源校验缺少头处理')

    rec('PASS' if not bad else 'FAIL', '跨环境健壮性（小屏/多屏/junction/坏库/日志）',
        '; '.join(bad))


def check_env_adapt():
    """环境自适应（第 132 项）：探测能力、自动降级，以及「DPI 感知真的设上了」。

    这一条专门给 2026-11-07 那个坑立的：SetProcessDpiAwarenessContext 的参数
    传 c_int(-4) 会**静默失败**（返回 False，不抛异常），进程退化成 DPI-unaware，
    整个窗口被窗口管理器当 96dpi 位图再拉伸一次 —— 界面发虚，而且 GetSystemMetrics
    返回虚拟化后的逻辑值，所有尺寸计算跟着错。老的 try/except 一点用都没有，
    因为没有异常。这种错只能靠静态断言参数写法来挡。
    """
    s = text(ROOT / 'app_window.py')
    su = text(ROOT / 'setup_app.py')
    bad = []

    # ① 环境探测模块在，且关键函数都在
    p = ROOT / 'env_check.py'
    if not p.is_file():
        bad.append('env_check.py 丢了（换环境只能靠猜）')
    else:
        e = text(p)
        for fn in ('def probe_all(', 'def judge(', 'def plan(', 'def webview2_version('):
            if fn not in e:
                bad.append('env_check 缺 %s' % fn.strip('def ('))
        if 'WEBVIEW2' not in e and 'EdgeWebView' not in e:
            bad.append('env_check 没查 WebView2（最致命的缺件）')

    # ② DPI 感知：参数必须用 64 位指针写法
    if 'SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))' not in s:
        bad.append('DPI 感知没用 c_void_p(-4)')
    if 'SetProcessDpiAwarenessContext(ctypes.c_int(' in s:
        bad.append('DPI 感知用了 c_int(-4)（返回 False，进程变 DPI-unaware）')

    # ③ 快搜窗按屏幕宽收窄
    if 'def calibrate_mini_width(' not in s:
        bad.append('没有 calibrate_mini_width（1920@250% 快搜窗会被切）')

    # ④ 渲染降级（远程桌面 / 无显卡驱动 → 黑块）
    if 'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS' not in s:
        bad.append('远程桌面/基础显示适配器时不会自动关 GPU（黑块）')

    # ⑤ 安装程序要查 WebView2
    if 'def wv2_version(' not in su:
        bad.append('安装程序没查 WebView2（LTSC/精简版装完就是白屏）')

    # ⑥ 启动要留环境快照
    if '[env-boot]' not in s:
        bad.append('启动没写环境快照（用户报问题时无从下手）')

    rec('PASS' if not bad else 'FAIL', '环境自适应（探测·降级·DPI 写法）', '; '.join(bad))


def check_autostart_default():
    """装完默认开机自启：GUI/CLI 两条安装路径都要写、默认勾选、验收可跳过、
    后端能识别指向已删目录的死链（否则设置里显示「已开启」其实开机啥也没起来）。"""
    s = text(ROOT / 'setup_app.py')
    b = text(ROOT / 'brain.py')
    sp = text(REPO / 'SecondBrain-Setup.spec')
    bad = []
    if 'def set_autostart(' not in s:
        bad.append('安装器没有写自启的函数')
    if "SB_NO_AUTOSTART" not in s:
        bad.append('静默安装验收无法跳过写注册表')
    if 'boot = tk.BooleanVar(value=True)' not in s:
        bad.append('GUI 复选框不是默认勾选')
    if 'auto = set_autostart(exe' not in s or 'auto = set_autostart(exe)' not in s:
        bad.append('GUI / CLI 有安装路径没接上')
    for k in ("'boot':", "'as_ok':", "'as_no':"):
        if s.count(k) != 5:
            bad.append('%s 五语言不齐（%d/5）' % (k, s.count(k)))
    if "'winreg'" not in sp:
        bad.append('安装器 spec 没收集函数内 import 的 winreg')
    if 'os.path.isfile(p)' not in b:
        bad.append('后端仍把注册表死链当成「已开启」')
    rec('PASS' if not bad else 'FAIL', '装完默认开机启动（含死链校验）',
        '; '.join(bad))


def main():
    print('== 打包前自检 ==')
    check_version()
    check_i18n()
    check_donate()
    check_license()
    check_spec()
    check_legacy()
    check_persistence()
    check_settings_concurrent()
    check_white_screen()
    check_env_robustness()
    check_env_adapt()
    check_autostart_default()
    check_tests()
    check_build_fresh()
    check_unknown_api()
    check_bridge()
    check_sync()
    if '--live' in sys.argv:
        i = sys.argv.index('--live')
        port = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 8765
        print('-- 在线检查 (端口 %d) --' % port)
        live(port)
    fails = [r for r in results if r[0] == 'FAIL']
    warns = [r for r in results if r[0] == 'WARN']
    print('\n结果：%d PASS / %d WARN / %d FAIL' %
          (len(results) - len(fails) - len(warns), len(warns), len(fails)))
    for _, n, d in fails:
        print('  FAIL: %s %s' % (n, d))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
