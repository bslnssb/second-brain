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
    rec('PASS' if not bad else 'FAIL', '白屏自愈（第一个实例自己 show+revive）',
        '; '.join(bad))


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
    check_spec()
    check_legacy()
    check_persistence()
    check_settings_concurrent()
    check_white_screen()
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
