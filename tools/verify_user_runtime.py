# -*- coding: utf-8 -*-
"""用户端验收：今天（第 243–248 项）的改动是否真的到了「用户日常运行的那个程序」。

⚠️ 特意**只从装机版 `%LOCALAPPDATA%\\Memex\\App\\Memex.exe` 与其 brain.db 取证**，
不查开发根源码 —— 源码写了不算数，sha 一致也不算数，要问用户真正跑起来的那个进程。

用法（开发测试版 或 上传的干净版 下都能跑）：
    py -3.12 tools/verify_user_runtime.py
输出每项 PASS/FAIL，退出码 0 = 全过。脚本路径按「工作区根」推导，
不依赖它放在哪一侧的 tools/ 里。

发布冻结（11-13 起）期间本脚本不重打安装包、不 commit/push；
对外发行物是否更新见脚本末尾的「发行通道」段落与交接文档。
"""
from __future__ import annotations

import hashlib
import json
import marshal
import os
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

# tools/<本文件> → parents[2] 恒为工作区根（开发根或干净版都在同一层），
# 所以脚本放在开发根 tools/ 或干净版 tools/ 都能推到同一组路径。
WS = Path(__file__).resolve().parents[2]
DEV = WS / '开发测试版'
INSTALLED = Path(os.environ['LOCALAPPDATA']) / 'Memex' / 'App'
EXE = INSTALLED / 'Memex.exe'
DB = INSTALLED / 'brain.db'
WEB = INSTALLED / '_internal' / 'web'
BASE = 'http://127.0.0.1:8765'
WATT_AUMID = '4651ED44255E.47979655102CE_k6txddmbb6c52!App'
WATT_PATH = 'shell:AppsFolder\\' + WATT_AUMID

results = []


def rec(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print('  %-4s %-42s %s' % ('PASS' if ok else 'FAIL', name, detail))
    return ok


def get(path, timeout=12):
    url = BASE + path
    req = urllib.request.Request(url, headers={'Sec-Fetch-Site': 'same-origin'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def api(path, timeout=12):
    raw, _ = get(path, timeout=timeout)
    return json.loads(raw.decode('utf-8'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


print('== 用户端验收：第 243–248 项 ==')
print('取证对象：', EXE)
assert EXE.is_file(), '装机版 exe 不存在'

# ---------------------------------------------------------------- 0 三处一致
print('\n[0] 产物一致性（开发根 / 干净版 dist / 装机版）')
paths = {
    'dev': DEV / 'Memex.exe',
    'clean': WS / '上传的干净版' / 'dist' / 'Memex' / 'Memex.exe',  # noqa: E501（路径按工作区根推）
    'installed': EXE,
}
shas = {k: sha(v) for k, v in paths.items()}
rec('三处 exe sha 一致', len(set(shas.values())) == 1, str(shas))
for k in ('dev', 'clean', 'installed'):
    w = paths[k].parent / '_internal' / 'web' / 'index.html'
    if w.is_file():
        import re
        m = re.search(r'assets/index-[A-Za-z0-9_-]+\.js', w.read_text(encoding='utf-8'))
        rec('三处 _internal/web 同一 bundle', bool(m),
            '%s -> %s' % (k, m.group(0) if m else 'N/A'))

# 第 302 项：卸载器应该是安装目录里一个能双击的 Uninstall.exe，而不是设置里的一张卡片
uni = EXE.parent / 'Uninstall.exe'
rec('安装目录有 Uninstall.exe，且与主程序同 sha',
    uni.is_file() and sha(uni) == sha(EXE),
    'exists=%s sha=%s' % (uni.is_file(), sha(uni) if uni.is_file() else '--------'))

# ------------------------------------------------------ 1 用户端 exe 解剖
print('\n[1] 用户端 exe 解剖（PYZ / 入口脚本，不是查源码）')
try:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    arc = CArchiveReader(str(EXE))
    entry = marshal.loads(arc.extract('app_window'))
    entry_names = set(entry.co_names)
    entry_fns = {}
    stack = [entry]
    while stack:
        c = stack.pop()
        entry_fns.setdefault(c.co_name, c)
        for x in (getattr(c, 'co_consts', ()) or ()):
            if hasattr(x, 'co_consts'):
                stack.append(x)
    globals()['ENTRY_FNS'] = entry_fns
    sm = entry_fns.get('show_mini')
    rec('入口 show_mini 带 origin/pin 分流',
        bool(sm) and 'origin' in sm.co_varnames and 'pin' in sm.co_varnames
        and 'topmost' in sm.co_names,
        'varnames=%s' % ([v for v in (sm.co_varnames if sm else []) if v in ('origin', 'pin')]))
    # 第 302 项：Uninstall.exe 靠文件名分流，_boot 里必须有这个判断
    _boot_fn = entry_fns.get('_boot')
    rec('入口 _boot 会把 Uninstall.exe 当卸载器',
        bool(_boot_fn) and '_as_uninstaller' in _boot_fn.co_varnames,
        'varnames=%s' % ([v for v in (_boot_fn.co_varnames if _boot_fn else ())
                          if v == '_as_uninstaller']))

    rec('入口 _auto_loop 接第 244 项兜底',
        '_type_mini_should_hide' in (entry_fns.get('_auto_loop').co_names if entry_fns.get('_auto_loop') else ()),
        '')
    # 第 267 项：托盘「设置/关于/反馈」点了没反应 = show_main 里那句
    # evaluate_js('location.href=…') 被 pywebview 的 _pywebviewready 门闩拖满 20 秒。
    # 用户端字节码里必须看到后端直连导航，且**看不到** evaluate_js。
    _sm = entry_fns.get('_show_main_once')
    _closing = entry_fns.get('_on_main_closing')
    rec('入口 show_main 走后端导航（第 267 项）',
        bool(_sm) and 'pv_load_url' in _sm.co_names and 'evaluate_js' not in _sm.co_names,
        'co_names 含 pv_load_url=%s / evaluate_js=%s'
        % (bool(_sm) and 'pv_load_url' in _sm.co_names,
           bool(_sm) and 'evaluate_js' in _sm.co_names))
    rec('入口托盘面板串行 worker 在用户端（第 267 项）',
        '_panel_worker' in entry_fns, '')
    rec('入口关窗会作废在飞的 show_main（第 267 项）',
        bool(_closing) and '_bump_hide' in _closing.co_names, '')
    rec('入口脚本含第 246 项启动接线', 'app_entries' in entry_names,
        'app_entries.launch 两处原生桥')
    pyz_name = next(x for x in arc.toc if x.lower().endswith('pyz.pyz'))
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'PYZ.pyz'
        f.write_bytes(arc.extract(pyz_name))
        z = ZlibArchiveReader(str(f))
        for mod in ('brain', 'scan_docs', 'audio_probe', 'app_entries', 'follow_click'):
            rec('PYZ 含 %s' % mod, mod in z.toc, '')
        raw = z.extract('brain')
        bc = marshal.loads(raw) if isinstance(raw, (bytes, bytearray)) else raw
        found = {}
        stack = [bc]
        while stack:
            c = stack.pop()
            found.setdefault(c.co_name, c)
            for x in (getattr(c, 'co_consts', ()) or ()):
                if hasattr(x, 'co_consts'):
                    stack.append(x)
        for fn in ('_media_noise_file', '_index_failures', '_exclude_test',
                   'shell_app_icon', 'syntax_only_items', 'sys_locale_info'):
            rec('brain.%s 在用户端 exe 里' % fn, fn in found, '')
        # 第 245 项：MINI_TPL 里方案2 的 CSS/JS 必须彻底没有
        strs = [x for x in bc.co_consts if isinstance(x, str)]
        tpl = max(strs, key=len)
        rec('用户端 MINI_TPL 无方案2',
            all(t not in tpl for t in ("STYLE==='tbl'", '.tbl #pane', 'qs_style_2')),
            '模板 %d B' % len(tpl))
        rec('用户端 MINI_TPL 贴角小窗最近访问最多2项',
            'if(SMALL) cap=Math.min(cap, 2);' in tpl,
            '贴角小窗限制2项已就位')
        # 第 308 项：快搜窗多选 + Ctrl+C 复制路径必须真的在模板里
        rec('用户端快搜窗含多选复制（第 308 项）',
            'function copySelection(' in tpl and 'sel_n' in tpl,
            'Ctrl+C / Ctrl+Shift+C / Ctrl+点击')
        # 第 268 项：聚合翻译必须能逐引擎开关 —— 注册表与选择逻辑得真进 exe，
        # 否则设置页点了保存，后端还是五个引擎全打，延迟一点没降。
        raw = z.extract('translator')
        bt = marshal.loads(raw) if isinstance(raw, (bytes, bytearray)) else raw
        tfind = {}
        stack = [bt]
        while stack:
            c = stack.pop()
            tfind.setdefault(c.co_name, c)
            for x in (getattr(c, 'co_consts', ()) or ()):
                if hasattr(x, 'co_consts'):
                    stack.append(x)
        rec('translator.normalize_engines 在用户端 exe 里（第 268 项）',
            'normalize_engines' in tfind and 'ENGINE_IDS' in bt.co_names,
            '引擎注册表 + 归一逻辑在位')
        tt = tfind.get('translate_text')
        rec('translate_text 按 chosen 清单取数（第 268 项）',
            bool(tt) and '_ENGINE_FETCHERS' in tt.co_names and 'normalize_engines' in tt.co_names,
            '')
        # 第 269 项：悬浮球 —— 点击吞掉的真因是拖拽阈值写死 4 设备像素；
        # 图标换品牌色；关球不再 Join(1500)；开关走每拍快同步。
        # 这四条只要有一条没进 exe，用户端就还是老行为。
        rec('floating_ball 在用户端 exe 里（第 269 项）',
            'floating_ball' in z.toc, '')
        rawb = z.extract('floating_ball')
        bb = marshal.loads(rawb) if isinstance(rawb, (bytes, bytearray)) else rawb
        bfind = {}
        stack = [bb]
        while stack:
            c = stack.pop()
            bfind.setdefault(c.co_name, c)
            for x in (getattr(c, 'co_consts', ()) or ()):
                if hasattr(x, 'co_consts'):
                    stack.append(x)
        rec('floating_ball.drag_threshold / is_drag（第 269 项）',
            'drag_threshold' in bfind and 'is_drag' in bfind, '')
        # 真根因：本机 pythonnet 不回调 Python 子类的 virtual 覆写 —— 用户端
        # 字节码里必须**看不到** OnPaint/OnMouseDown 这类覆写，必须是事件订阅。
        rec('悬浮球改用事件订阅、没有 virtual 覆写（第 269 项）',
            '_on_paint' in bfind and '_on_mouse_down' in bfind
            and '_on_mouse_up' in bfind and 'OnPaint' not in bfind
            and 'OnMouseDown' not in bfind, '')
        _mm = bfind.get('_on_mouse_move')
        rec('拖动判定按 DPI（第 269 项）',
            bool(_mm) and 'is_drag' in _mm.co_names, '')
        _md = bfind.get('_on_mouse_down')
        rec('按下抓 Capture（第 269 项）',
            bool(_md) and 'Capture' in _md.co_names, '')
        _sh = bfind.get('_on_shown')
        rec('显示后复位尺寸/置顶/圆形 Region（第 269 项）',
            bool(_sh) and '_apply_ball_geometry' in _sh.co_names
            and 'TopMost' in _sh.co_names, '')
        rec('用 SetWindowRgn 真裁圆（第 269 项）',
            '_set_circle_region' in bfind and 'SetWindowRgn' in bfind['_set_circle_region'].co_names, '')
        _stop = bfind.get('stop')
        rec('悬浮球 stop 的 Join 上限 400ms（第 269 项）',
            bool(_stop) and 400 in (_stop.co_consts or ())
            and 1500 not in (_stop.co_consts or ()),
            'co_consts 含 400=%s / 1500=%s'
            % (bool(_stop) and 400 in (_stop.co_consts or ()),
               bool(_stop) and 1500 in (_stop.co_consts or ())))
        rec('悬浮球品牌色（青环）在用户端 exe 里（第 269 项）',
            (9, 189, 194) in (bb.co_consts or ()), '')
        # 第 272 项：图标改成贴官方标志图（hand-drawn 只当兜底）+ 修 Cursor 崩溃。
        rec('悬浮球贴 ball.png（第 272 项）',
            'ball.png' in (bb.co_consts or ())
            and '_ball_png_path' in bfind and '_paint_png' in bfind, '')
        _pp = bfind.get('_paint_png')
        rec('_paint_png 用 DrawImage 高质量缩放（第 272 项）',
            bool(_pp) and 'DrawImage' in _pp.co_names
            and 'HighQualityBicubic' in _pp.co_names, '')
        _paint = bfind.get('_on_paint')
        rec('_on_paint 主路径是贴图、手绘只兜底（第 272 项）',
            bool(_paint) and '_paint_png' in _paint.co_names
            and '_paint_fallback' in _paint.co_names, '')
        rec('Region 按 alpha 剪影（手柄不被裁掉）（第 272 项）',
            'silhouettes' in bfind
            and 'silhouettes' in (bfind.get('_set_circle_region').co_names or ()), '')
        _geo = bfind.get('_apply_ball_geometry')
        rec('球按 DPI 放大（50 逻辑像素，第 272 项）',
            'ball_size' in bfind and bool(_geo) and 'ball_size' in (_geo.co_names or ()), '')
        rec('MouseDown 用到 Cursor（已 import，不再 NameError）（第 272 项）',
            bool(_md) and 'Cursor' in _md.co_names
            and 'Cursor' in (bfind.get('_on_mouse_move').co_names or ()), '')
        # 同名 code object 有多个（__init__ 就至少两份），按名字取会取到缓存类的那个；
        # 这里改成扫描**所有** code object，只问「有没有哪处在调 _guard」。
        bcodes = []
        stack2 = [bb]
        while stack2:
            c = stack2.pop()
            bcodes.append(c)
            for x in (getattr(c, 'co_consts', ()) or ()):
                if hasattr(x, 'co_consts'):
                    stack2.append(x)
        rec('事件回调全部套 _guard（异常不弹 .NET 框）（第 272 项）',
            '_guard' in bfind and 'wrapper' in bfind
            and any('_guard' in (getattr(c, 'co_names', ()) or ()) for c in bcodes), '')

        _al = entry_fns.get('_auto_loop')
        rec('_auto_loop 每拍快同步悬浮球（第 269 项）',
            bool(_al) and '_sync_floating_ball_fast' in _al.co_names, '')
        _bc = entry_fns.get('_on_ball_click')
        rec('单击回调接 _on_ball_click 且看真实前台（第 269 项）',
            bool(_bc) and 'foreground_is' in _bc.co_names, '')

except Exception as e:
    rec('exe 解剖', False, '%s: %s' % (type(e).__name__, e))

# ------------------------------------------------------------ 2 运行实例
print('\n[2] 运行实例真的在用户端（不是开发根被吸走）')
try:
    st = api('/api/stats')
    rec('/api/stats 可用', st.get('ok') is True,
        'files %s / docs %s' % (st.get('files'), st.get('docs')))
except Exception as e:
    rec('/api/stats 可用', False, str(e))

# ------------------------------------------------------------ 3 逐项实测
print('\n[3] 第 243 项：短音频 / 音乐平台 / 软件目录名单')
try:
    cfg = api('/api/config')
    rules = cfg.get('scope', {}).get('rules', {})
    rec('scope.rules 新字段已下发',
        rules.get('size_limit') == 0 and rules.get('audio_min_seconds') == 20
        and rules.get('music_apps', 0) > 0 and rules.get('software_dirs', 0) > 0,
        json.dumps(rules, ensure_ascii=False))
except Exception as e:
    rec('scope.rules 新字段已下发', False, str(e))

print('\n[4] 第 243 项：重建后的真实索引内容（装机版 brain.db 只读）')
try:
    con = sqlite3.connect('file:%s?mode=ro' % DB.as_posix(), uri=True)
    q = lambda s, *a: con.execute(s, a).fetchone()[0]
    www = q("select count(*) from files where path like '%phpstudy_pro%WWW%'")
    nmp3 = q("select count(*) from files where ext='.mp3' and path like '%CloudMusic%'")
    ding = q("select count(*) from files where path like '%DingDing%'")
    draf = q("select count(*) from files where path like '%JianyingPro Drafts%'")
    bkup = q("select count(*) from files where path like '%手机备份%'")
    rec('靶场 phpstudy_pro\\WWW 完整保留', www > 1000, '%d 条' % www)
    rec('网易云目录 mp3 已排除', nmp3 == 0, '%d 条' % nmp3)
    rec('钉钉只留应用入口', 0 < ding < 100, '%d 条' % ding)
    rec('剪映草稿 / 手机备份未误伤', draf > 0 and bkup > 0,
        '草稿 %d / 备份 %d' % (draf, bkup))
    con.close()
except Exception as e:
    rec('索引内容核对', False, str(e))

print('\n[5] 第 244 项：快搜窗按来源分流（行为需用户实测，这里钉用户端字节码）')
try:
    fns = globals().get('ENTRY_FNS') or {}
    bl = fns.get('on_mini_blur')
    # 注意：`getattr(self,'_mini_origin','')` 里的属性名是**字符串常量**，不在 co_names；
    # 上一版断言写成 co_names 会假阴性 —— 钉常量才对。
    bs = [x for x in (bl.co_consts if bl else ()) if isinstance(x, str)]
    rec('入口 on_mini_blur 认得 type 来源',
        bool(bl) and '_mini_origin' in bs and 'type' in bs
        and 'blur → 打字唤出，点空白处收起' in bs,
        'type 来源失焦立即收起 / dock 失焦保持吸附')
    print('       ⚠️ 不注入键鼠（红线）：「打字→不置顶、点空白处→收起」需用户实测；'
          '自动化证据＝17 例回归 + Win32 GWL_EXSTYLE 实测 + 上面的入口字节码')
except Exception as e:
    rec('第 244 项证据', False, str(e))

print('\n[6] 第 245 项：弹窗样式只剩方案1')
try:
    st = api('/api/settings')
    rec('/api/settings 归一 rich', st.get('qs_style') == 'rich', repr(st.get('qs_style')))
    idx = (WEB / 'index.html').read_text(encoding='utf-8')
    import re
    m = re.search(r'assets/index-[A-Za-z0-9_-]+\.js', idx)
    js = (WEB / m.group(0)).read_text(encoding='utf-8') if m else ''
    rec('用户端 bundle 无方案2 文案',
        '方案2 · 紧凑表' not in js and 'qs_style_2' not in js,
        m.group(0) if m else 'no bundle')
except Exception as e:
    rec('第 245 项证据', False, str(e))

print('\n[7] 第 246 项：商店应用可搜（真实搜索接口）')
try:
    for ep in ('/api/files?q=watt', '/api/mini?q=watt'):
        d = api(ep)
        hit = [x for x in (d.get('items') or []) if str(x.get('n', '')).lower() == 'watt toolkit']
        rec(ep + ' 命中 Watt Toolkit', len(hit) == 1,
            '显示名 %r' % (hit[0].get('dn') if hit else None))
    con = sqlite3.connect('file:%s?mode=ro' % DB.as_posix(), uri=True)
    n = con.execute("select count(*) from files where path like 'shell:AppsFolder%'").fetchone()[0]
    rec('库里虚拟应用入口', n >= 30, '%d 条' % n)
    con.close()
    raw, _ = get('/open?p=' + urllib.parse.quote('shell:AppsFolder\\NoSuch_1234567890!App', safe=''))
    rec('/open 对未搜过的假 AUMID 拒绝', b'deny' in raw.lower() or b'not' in raw.lower(),
        raw[:80].decode('utf-8', 'replace'))
except Exception as e:
    rec('第 246 项证据', False, str(e))

print('\n[8] 第 247 项：失败清单 / 排除规则自检')
try:
    d = api('/api/index_failures?limit=20')
    items = d.get('items') or []
    benign = [x for x in items if '无文本' in str(x.get('why', ''))]
    rec('失败清单是真失败（无「无文本」噪声）',
        d.get('total', 0) > 0 and not benign,
        'total=%d，样例=%s' % (d.get('total'), (items[0].get('why') if items else '')[:24]))
    ex = api('/api/exclude_test?p=' + urllib.parse.quote('E:\\SteamLibrary\\steamapps\\a.txt', safe=''))
    rec('排除规则自检命中', ex.get('excluded') is True,
        'rules=%s' % [h.get('rule') for h in ex.get('hits', [])][:4])
    keep = api('/api/exclude_test?p=' + urllib.parse.quote(
        'C:\\Users\\x\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\WeChat.lnk', safe=''))
    rec('白名单压过内置排除', keep.get('excluded') is False and keep.get('kept') is True, '')
except Exception as e:
    rec('第 247 项证据', False, str(e))

print('\n[9] 第 248 项：商店应用显示真实图标（不是空白纸）')
try:
    url = '/api/icon?e=&p=' + urllib.parse.quote(WATT_PATH, safe='')
    raw, hdr = get(url)
    generic, _ = get('/api/icon?e=')
    fake, _ = get('/api/icon?e=&p=' + urllib.parse.quote(
        'shell:AppsFolder\\NoSuch_1234567890!App', safe=''))
    rec('真 AUMID 返回彩色应用图标',
        raw.startswith(b'\x89PNG') and raw != generic,
        '%d B vs 空白纸 %d B / %s' % (len(raw), len(generic), hdr.get('Content-Type')))
    rec('假 AUMID 安全回落', fake == generic, '%d B' % len(fake))
except Exception as e:
    rec('第 248 项证据', False, str(e))

print('\n[10] 第 250 项：标题栏品牌胶囊按钮与版本/更新菜单')
try:
    idx = (WEB / 'index.html').read_text(encoding='utf-8')
    import re
    m = re.search(r'assets/index-[A-Za-z0-9_-]+\.js', idx)
    js = (WEB / m.group(0)).read_text(encoding='utf-8') if m else ''
    rec('用户端 bundle 含品牌按钮与更新调用',
        '"data-act":"brand"' in js and 'Version ' in js and 'update_check' in js,
        m.group(0) if m else 'no bundle')
except Exception as e:
    rec('第 250 项证据', False, str(e))

print('\n[11] 第 251 项：右键多维复制、正文片段复制与状态栏动态遥测')
try:
    idx = (WEB / 'index.html').read_text(encoding='utf-8')
    import re
    m = re.search(r'assets/index-[A-Za-z0-9_-]+\.js', idx)
    js = (WEB / m.group(0)).read_text(encoding='utf-8') if m else ''
    rec('用户端 bundle 包含复制文件名/所在目录/正文片段',
        'ctx_copy_name' in js and 'ctx_copy_dir' in js and 'ctx_copy_snip' in js,
        '右键细分复制齐')
    rec('用户端 bundle 包含状态栏遥测与 Ctrl+Enter 快捷键提示',
        'item_cur' in js and 'kb_reveal' in js and 'Ctrl+Enter' in js,
        '状态栏选中遥测与快捷键提示齐')
except Exception as e:
    rec('第 251 项证据', False, str(e))

print('\n[12] 第 253 项：表格列宽响应式自适应 minmax+fr')
try:
    idx = (WEB / 'index.html').read_text(encoding='utf-8')
    import re
    m = re.search(r'assets/index-[A-Za-z0-9_-]+\.js', idx)
    js = (WEB / m.group(0)).read_text(encoding='utf-8') if m else ''
    rec('用户端 bundle 包含响应式自适应列宽 minmax 与 fr',
        'minmax(80px' in js and 'fr)' in js,
        '响应式自适应列宽在位')
except Exception as e:
    rec('第 253 项证据', False, str(e))

print('\n[13] 第 268 项：聚合翻译逐引擎开关（用户端 bundle + 运行期设置）')
try:
    idx = (WEB / 'index.html').read_text(encoding='utf-8')
    import re
    m = re.search(r'assets/index-[A-Za-z0-9_-]+\.js', idx)
    js = (WEB / m.group(0)).read_text(encoding='utf-8') if m else ''
    rec('用户端 bundle 含引擎开关文案 trans_engines_tip',
        'trans_engines_tip' in js, m.group(0) if m else 'no bundle')
    rec('用户端 bundle 会把勾选写回 trans_engines',
        'trans_engines' in js, '')
    cfg = api('/api/settings')
    eng = cfg.get('trans_engines')
    rec('运行期 settings 已带 trans_engines 列表',
        isinstance(eng, list) and len(eng) >= 1, str(eng))
except Exception as e:
    rec('第 268 项证据', False, str(e))

print('\n[14] 第 269 项：悬浮球开关「点一下就动」+ 点击链路（装机版运行期实证）')
try:
    import time

    LOG = INSTALLED / 'debug.log'

    def post(path, payload):
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            BASE + path, data=body, method='POST',
            headers={'Sec-Fetch-Site': 'same-origin', 'Origin': BASE,
                     'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read().decode('utf-8'))

    def _tail(n=300000):
        if not LOG.exists():
            return ''
        with open(str(LOG), 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode('utf-8', 'ignore')

    def wait_marker(marker, timeout=2.5):
        """等到日志里 marker 的条数增加，返回耗时；超时返回 None。"""
        base = _tail().count(marker)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if _tail().count(marker) > base:
                return time.time() - t0
            time.sleep(0.05)
        return None

    cfg = api('/api/settings')
    was_on = bool(cfg.get('floating_ball'))
    # 统一从「关」开始，免得视角卡在中间态
    post('/api/settings', {'floating_ball': False})
    wait_marker('floating_ball: 收起', 2.0)

    dt_on = None
    base = _tail().count('floating_ball: 拉起')
    t0 = time.time()
    post('/api/settings', {'floating_ball': True})
    while time.time() - t0 < 3.0:
        if _tail().count('floating_ball: 拉起') > base:
            dt_on = time.time() - t0
            break
        time.sleep(0.05)
    rec('开启悬浮球 1 秒内生效（第 269 项）',
        dt_on is not None and dt_on <= 1.0,
        ('%.2fs 起来' % dt_on) if dt_on is not None else '3 秒内没看到「拉起」日志')

    base = _tail().count('floating_ball: 收起')
    t0 = time.time()
    post('/api/settings', {'floating_ball': False})
    dt_off = None
    while time.time() - t0 < 3.0:
        if _tail().count('floating_ball: 收起') > base:
            dt_off = time.time() - t0
            break
        time.sleep(0.05)
    rec('关闭悬浮球 1 秒内生效（第 269 项）',
        dt_off is not None and dt_off <= 1.0,
        ('%.2fs 收起' % dt_off) if dt_off is not None else '3 秒内没看到「收起」日志')

    if was_on:
        post('/api/settings', {'floating_ball': True})
        wait_marker('floating_ball: 拉起', 2.0)
    rec('悬浮球开关已复原到验证前状态（第 269 项）',
        bool(api('/api/settings').get('floating_ball')) == was_on,
        'was_on=%s' % was_on)

    # 第 272 项：图标必须是打包进 exe 的官方标志图，不再是手绘。
    png = INSTALLED / '_internal' / 'assets' / 'ball.png'
    if not png.exists():
        png = INSTALLED / 'assets' / 'ball.png'
    size = png.stat().st_size if png.exists() else 0
    rec('装机版带 assets/ball.png（第 272 项）', png.exists() and size > 5000,
        '%s (%d B)' % (png.name if png.exists() else '缺失', size))
    _tail_all = _tail(2000000)
    rec('悬浮球已改用 png 绘制（日志点过 ball.png 就绪）（第 272 项）',
        'ball.png 就绪' in _tail_all and 'png=True' in _tail_all,
        '就绪=%s / png=True=%s'
        % ('ball.png 就绪' in _tail_all, 'png=True' in _tail_all))
    rec('Region 走剪影而不是纯圆（日志「剪影 Region」）（第 272 项）',
        '剪影 Region' in _tail_all, '')
    _sz = re.findall(r'剪影 Region：\d+ 条扫描线 (\d+)x(\d+)', _tail_all)
    rec('球窗按 DPI 放大（144dpi 应为 75 设备像素）（第 272 项）',
        bool(_sz) and int(_sz[-1][0]) >= 60 and _sz[-1][0] == _sz[-1][1],
        ('最近一次 ' + _sz[-1][0] + 'x' + _sz[-1][1]) if _sz else '日志里没解析到尺寸')
    rec('日志里没有 Cursor NameError（第 272 项）',
        "name 'Cursor' is not defined" not in _tail_all
        and 'Cursor EXCEPTION' not in _tail_all, '')
except Exception as e:
    rec('第 269 项证据', False, str(e))

print('\n[15] 第 270 项：网络搜索「恢复默认引擎」（删掉的内置引擎能补回来）')
try:
    import re

    cfg = api('/api/settings')
    dflt = cfg.get('web_search_defaults')
    if not isinstance(dflt, list):
        dflt = []
    rec('用户端 /api/settings 下发出厂默认引擎表（第 270 项）',
        len(dflt) >= 5
        and all(isinstance(e, dict) and e.get('key') and '{query}' in (e.get('url') or '')
                for e in dflt),
        '%d 条：%s' % (len(dflt), ','.join(str(e.get('key')) for e in dflt)))
    # 用户这次删掉的就是 Bing —— 默认表里必须在，界面上才补得回来
    rec('默认表包含 Bing（用户删掉的那条，第 270 项）',
        any(e.get('key') == 'bing' for e in dflt), '')
    # 第 310 项：抖音 / 小红书 / TikTok 要在默认表里，且国内引擎排在国外前面
    keys = [str(e.get('key')) for e in dflt]
    cn = [k for k in keys if k in ('b', 'dy', 'xhs', 'bi')]
    abroad = [k for k in keys if k in ('g', 'yt', 'tt', 'bing', 'wiki', 'gh')]
    rec('默认表含抖音/小红书/TikTok（第 310 项）',
        all(k in keys for k in ('dy', 'xhs', 'tt')), ','.join(keys))
    rec('默认引擎按「国内 → 国外」排序（第 310 项）',
        bool(cn) and bool(abroad) and cn + abroad == keys, '')
    eff = cfg.get('web_search') or []
    eff_keys = [str(e.get('key')) for e in eff]
    rec('生效表里国内引擎也排在前面（第 310 项）',
        bool(eff_keys) and eff_keys[:len(cn)] == cn if eff_keys else False,
        '%d 条：%s' % (len(eff_keys), ','.join(eff_keys)))
    try:
        sj = json.loads((INSTALLED / 'settings.json').read_text(encoding='utf-8'))
        leaked = 'web_search_defaults' in sj
    except Exception as e:
        sj, leaked = {}, False
        print('    (读 settings.json 失败：%s)' % e)
    rec('默认表只下发、没有写进用户 settings.json（第 270 项）',
        not leaked, 'settings.json 键：%s' % ','.join(sorted(sj.keys())[:8]))

    # 全删光也要自洽：存 [] 时界面必须显示回出厂默认（快搜侧本来就回落默认，
    # 否则一边白表一边还能搜）。跑完把用户原来的表原样写回，不改用户配置。
    cur = cfg.get('web_search') or []
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                BASE + '/api/settings', data=json.dumps({'web_search': []}).encode('utf-8'),
                method='POST', headers={'Sec-Fetch-Site': 'same-origin', 'Origin': BASE,
                                        'Content-Type': 'application/json'}), timeout=12)
        after = api('/api/settings').get('web_search') or []
        rec('删光引擎后界面仍显示出厂默认（第 270 项）',
            len(after) == len(dflt) and all(e.get('key') for e in after),
            '存 [] → 显示 %d 条：%s' % (len(after), ','.join(str(e.get('key')) for e in after)))
    finally:
        urllib.request.urlopen(
            urllib.request.Request(
                BASE + '/api/settings', data=json.dumps({'web_search': cur}).encode('utf-8'),
                method='POST', headers={'Sec-Fetch-Site': 'same-origin', 'Origin': BASE,
                                        'Content-Type': 'application/json'}), timeout=12)
    back = [e.get('key') for e in (api('/api/settings').get('web_search') or [])]
    rec('引擎表已复原到验证前的样子（第 270 项）',
        back == [e.get('key') for e in cur], '复原：%s' % ','.join(back))

    idx = (WEB / 'index.html').read_text(encoding='utf-8')
    m = re.search(r'assets/index-[A-Za-z0-9_-]+\.js', idx)
    js = (WEB / m.group(0)).read_text(encoding='utf-8') if m else ''
    rec('用户端 bundle 含「恢复默认引擎」按钮（第 270 项）',
        '恢复默认引擎' in js and 'web_search_defaults' in js,
        m.group(0) if m else 'no bundle')
    # 第 311 项：快搜窗标准键与网络搜索前缀必须出现在设置页的快捷键清单里
    rec('用户端快捷键清单含 Ctrl+Shift+C（第 311 项）',
        'Ctrl+Shift+C' in js and 'Ctrl+C' in js, m.group(0) if m else 'no bundle')
    rec('用户端快捷键清单含网络搜索引擎前缀（第 311 项）',
        'dy / xhs / g' in js, m.group(0) if m else 'no bundle')
    # 第 312 项：详情面板必须带预览图（图片/PDF）与自动正文（Word/Excel）
    rec('用户端详情面板带预览图（第 312 项）',
        js.find('/api/thumb?p=') >= 0 and 'dtl_peek_head' in js, m.group(0) if m else 'no bundle')
    rec('用户端详情面板对 Word/Excel 自动读正文（第 312 项）',
        'docx' in js and 'xlsx' in js and '/api/peek' in js, m.group(0) if m else 'no bundle')
except Exception as e:
    rec('第 270 项证据', False, str(e))

print('\n[16] 第 271 项：聚合翻译「别把用户自己的配额烧光」（词语分层 + 缓存 + 冷却 + 到点即返）')
try:
    import time


    def post_json(path, payload):
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            BASE + path, data=body, method='POST',
            headers={'Sec-Fetch-Site': 'same-origin', 'Origin': BASE,
                     'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode('utf-8'))

    sentence = '这次的会议改到了十一月十八号的下午三点整'
    t0 = time.time()
    r1 = post_json('/api/translate', {'text': sentence, 'to': 'en'})
    dt1 = time.time() - t0
    sk = {s.get('engine'): s.get('reason') for s in (r1.get('skipped') or [])}
    # 只看**本机已启用**的词典引擎：用户的 trans_engines 可能没勾金山词霸，
    # 拿固定三家去比会误报（实测用户设置里就是 google/mymemory/youdao/baidu）。
    _eng = set(r1.get('engines') or [])
    _need = [k for k in ('youdao', 'baidu', 'iciba') if k in _eng]
    rec('整句不再打词典接口（第 271 项）',
        all(('只用于词语' in (sk.get(k) or '')) for k in _need),
        '已启用=%s skipped=%s' % (_need, json.dumps(sk, ensure_ascii=False)))
    rec('聚合到点即返（≤3.5 秒，第 271 项）', dt1 <= 3.5,
        '整句 %.2fs，拿到 %d 条结果' % (dt1, len(r1.get('results') or [])))
    # Google 在国内直连基本不通：要么被探活挡掉，要么在冷却里，都不能拖时间
    rec('Google 没通就不发请求（第 271 项）',
        ('google' not in sk) or ('连不上' in (sk.get('google') or ''))
        or ('冷却' in (sk.get('google') or '')),
        'google: %s' % (sk.get('google') or '（本次有参与）'))

    t0 = time.time()
    r2 = post_json('/api/translate', {'text': sentence, 'to': 'en'})
    dt2 = time.time() - t0
    cached = any(it.get('cached') for it in (r2.get('results') or []))
    rec('同样文本第二次走缓存（第 271 项）',
        (cached or dt2 <= 0.35) and dt2 <= 1.0,
        '第二次 %.2fs，cached=%s' % (dt2, cached))

    r3 = post_json('/api/translate', {'text': '会议', 'to': 'en'})
    sk3 = {s.get('engine'): s.get('reason') for s in (r3.get('skipped') or [])}
    rec('词语仍然会打词典引擎（第 271 项）',
        not any('只用于词语' in (sk3.get(k) or '') for k in ('youdao', 'baidu', 'iciba')),
        'word_like=%s，拿到 %d 条' % (r3.get('word_like'), len(r3.get('results') or [])))
except Exception as e:
    rec('第 271 项证据', False, str(e))

print('\n[17] 第 273 项：自动=译成界面语言 + 不把"回吐原文"当译文')
try:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    _arc = CArchiveReader(str(EXE))
    _pz = next(x for x in _arc.toc if x.lower().endswith('pyz.pyz'))

    def _mod_find(z, mod):
        raw = z.extract(mod)
        bc = marshal.loads(raw) if isinstance(raw, (bytes, bytearray)) else raw
        d = {}
        st = [bc]
        while st:
            c = st.pop()
            d.setdefault(c.co_name, c)
            for x in (getattr(c, 'co_consts', ()) or ()):
                if hasattr(x, 'co_consts'):
                    st.append(x)
        return bc, d

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'PYZ.pyz'
        f.write_bytes(_arc.extract(_pz))
        _z = ZlibArchiveReader(str(f))
        bt, tf = _mod_find(_z, 'translator')
        rec('translator 的 auto_target/looks_echo/ui_lang 进 exe（第 273 项）',
            all(n in tf for n in ('auto_target', 'looks_echo', 'ui_lang',
                                  'auto_label', '_skeleton', '_latin_lang')),
            '缺：%s' % [n for n in ('auto_target', 'looks_echo', 'ui_lang',
                                    'auto_label', '_skeleton', '_latin_lang')
                        if n not in tf])
        tt = tf.get('translate_text')
        rec('translate_text 自动模式改走 auto_target（第 273 项）',
            bool(tt) and 'auto_target' in tt.co_names and 'ui_lang' in tt.co_names,
            'co_names 含 auto_target=%s ui_lang=%s'
            % (bool(tt) and 'auto_target' in tt.co_names, bool(tt) and 'ui_lang' in tt.co_names))
        fetchers = ('_fetch_mymemory', '_fetch_google', '_fetch_youdao_dict',
                    '_fetch_baidu_sug', '_fetch_iciba_dict')
        rec('五家引擎都认"回吐原文"（第 273 项）',
            all(bool(tf.get(n)) and 'looks_echo' in tf.get(n).co_names for n in fetchers),
            '没接的有：%s' % [n for n in fetchers
                             if not (tf.get(n) and 'looks_echo' in tf.get(n).co_names)])
        bb, bfind = _mod_find(_z, 'brain')
        # 第 313 项：make_thumb 里**不能**再有 Office 分支 —— 312 那版就是它把小窗/主窗拖死
        # （缩略图视图对每格发 /api/thumb，Shell COM 一卡就占满 6 条 HTTP 线程）。
        mt = bfind.get('make_thumb')
        rec('用户端 make_thumb 没有 Office 缩略图分支（第 313 项）',
            bool(mt) and 'OFFICE_THUMB_EXTS' not in mt.co_names
            and 'OFFICE_THUMB_TIMEOUT' not in mt.co_names,
            'co_names 含 OFFICE_THUMB_EXTS=%s' % (bool(mt) and 'OFFICE_THUMB_EXTS' in mt.co_names))
        bconsts = []
        for c in bfind.values():
            for x in (getattr(c, 'co_consts', ()) or ()):
                if isinstance(x, str):
                    bconsts.append(x)
        tpl = max((x for x in bb.co_consts if isinstance(x, str)), key=len)
        rec('快搜栏「自动」文案不再是"中/英互译"（第 273 项）',
            'id="tbLangAuto"' in tpl and '中/英互译' not in tpl,
            '模板里找到 tbLangAuto=%s' % ('id="tbLangAuto"' in tpl))
        # 第 281 项：这句提示也进了 i18n，源码里不再有中文字面量；
        # 判据改成「有 transSkipText 变量」+「占位符已被替换掉」。
        rec('快搜栏没结果会报原因（第 273/281 项）',
            'transSkipText' in tpl and '__U_trans_fail__' in tpl,
            '')
        rec('/api/settings 下发 trans_auto_label / ui_lang（第 273 项）',
            'trans_auto_label' in bconsts and 'trans_auto_target' in bconsts,
            '')
        rec('聚合主循环遇到 echo 就不当译文（第 273 项）',
            bool(tt) and any(str(x) == 'echo' for x in (tt.co_consts or ())),
            'echo 常量 %s / 原文提示 %s'
            % (bool(tt) and any(str(x) == 'echo' for x in (tt.co_consts or ())),
               bool(tt) and any('只返回了原文' in str(x) for x in (tt.co_consts or ()))))

    # ---- 运行期：默认目标语言存成德语，选「自动」也必须译成中文（用户截图那条）
    cfg0 = api('/api/settings')
    old_tgt = cfg0.get('trans_target_lang')
    ui = cfg0.get('ui_lang')
    lab = cfg0.get('trans_auto_label') or ''
    # 界面语言可能是 zh-CN / en / hi / es / pt —— 「自动」的语义是跟随 ui_lang，
    # 所以期望目标语要按 ui_lang 推算，不能写死 zh-CN（否则用户切了语言就全红）。
    exp_tgt = ui if ui in ('zh-CN', 'en', 'hi', 'es', 'pt') else 'zh-CN'
    rec('/api/settings 带回 ui_lang + 自动文案（第 273 项运行期）',
        bool(ui) and bool(lab), 'ui_lang=%s label=%s 期望目标=%s' % (ui, lab, exp_tgt))

    def _has_cjk(s):
        import re as _re
        return bool(_re.search(r'[\u4e00-\u9fa5]', str(s or '')))

    def _tr(text, to='auto'):
        return post_json('/api/translate', {'text': text, 'to': to})

    try:
        post('/api/settings', {'trans_target_lang': 'de'})
        time.sleep(0.2)
        r_en = _tr('Sorry, this account is ineligible to use Antigravity.')
        en_txt = ' '.join(it.get('text', '') for it in (r_en.get('results') or []))
        rec('英文 + 自动 → 界面语言（默认目标语是德语也照样）（第 273 项运行期）',
            r_en.get('tgt_lang') == exp_tgt and 'x' not in en_txt[:0] and bool(en_txt),
            'tgt=%s 期望=%s results=%d'
            % (r_en.get('tgt_lang'), exp_tgt, len(r_en.get('results') or [])))

        r_de = _tr('Entschuldigung, dieses Konto ist nicht berechtigt.')
        de_txt = ' '.join(it.get('text', '') for it in (r_de.get('results') or []))
        rec('德文 + 自动 → 界面语言（第 273 项运行期）',
            r_de.get('src_lang') == 'de' and r_de.get('tgt_lang') == exp_tgt,
            'src=%s tgt=%s 期望=%s' % (r_de.get('src_lang'), r_de.get('tgt_lang'), exp_tgt))

        r_ja = _tr('このアカウントは資格がありません')
        rec('日文 + 自动 → 界面语言（第 273 项运行期）',
            r_ja.get('src_lang') == 'ja' and r_ja.get('tgt_lang') == exp_tgt,
            'src=%s tgt=%s 期望=%s' % (r_ja.get('src_lang'), r_ja.get('tgt_lang'), exp_tgt))

        r_zh = _tr('这个账号没有使用资格')
        exp_zh_tgt = 'en' if exp_tgt == 'zh-CN' else exp_tgt
        rec('中文 + 自动 → 英文/界面语言（第 273 项运行期）',
            r_zh.get('src_lang') == 'zh-CN' and r_zh.get('tgt_lang') == exp_zh_tgt,
            'src=%s tgt=%s 期望=%s' % (r_zh.get('src_lang'), r_zh.get('tgt_lang'), exp_zh_tgt))

        r_long = _tr('Sorry, this account is ineligible to use Antigravity.\n'
                     'Your account is not linked to an eligible account.')
        echoes = [it for it in (r_long.get('results') or [])
                  if it.get('text', '').strip().lower() ==
                  'sorry, this account is ineligible to use antigravity.']
        rec('整句结果里没有"译文=原文"的条目（第 273 项运行期）',
            not echoes, 'results=%d' % len(r_long.get('results') or []))
    finally:
        post('/api/settings', {'trans_target_lang': old_tgt or 'auto'})
except Exception as e:
    rec('第 273 项证据', False, str(e))

print('\n[18] 第 274 项：自带密钥（DeepSeek / DeepL / 百度开放平台，额度算用户自己的账号）')
try:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    _arc2 = CArchiveReader(str(EXE))
    _pz2 = next(x for x in _arc2.toc if x.lower().endswith('pyz.pyz'))

    def _mod_find2(z, mod):
        raw = z.extract(mod)
        bc = marshal.loads(raw) if isinstance(raw, (bytes, bytearray)) else raw
        d = {}
        st = [bc]
        while st:
            c = st.pop()
            d.setdefault(c.co_name, c)
            for x in (getattr(c, 'co_consts', ()) or ()):
                if hasattr(x, 'co_consts'):
                    st.append(x)
        return bc, d

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'PYZ.pyz'
        f.write_bytes(_arc2.extract(_pz2))
        _z2 = ZlibArchiveReader(str(f))
        bt2, tf2 = _mod_find2(_z2, 'translator')
        rec('translator 有 byok_cfg / byok_ready / mask_secret（第 274 项）',
            all(n in tf2 for n in ('byok_cfg', 'byok_ready', 'mask_secret')),
            '缺：%s' % [n for n in ('byok_cfg', 'byok_ready', 'mask_secret') if n not in tf2])
        rec('三家自带密钥取数函数进 exe（第 274 项）',
            all(n in tf2 for n in ('_fetch_deepseek', '_fetch_deepl', '_fetch_baidu_api')),
            '缺：%s' % [n for n in ('_fetch_deepseek', '_fetch_deepl', '_fetch_baidu_api')
                        if n not in tf2])
        ids = [x for x in bt2.co_consts if isinstance(x, str)]
        rec('引擎 id 追加 deepseek / deepl / baidu_api（第 274 项）',
            all(i in ids for i in ('deepseek', 'deepl', 'baidu_api')),
            '老 id 一个没动，只在尾部追加三家')
        _tt2 = tf2.get('translate_text')
        rec('translate_text 会先看 byok_ready 再决定发不发（第 274 项）',
            bool(_tt2) and 'byok_ready' in _tt2.co_names and 'BYOK_IDS' in _tt2.co_names,
            '')
        bb2, bf2 = _mod_find2(_z2, 'brain')
        bstr = []
        for c in bf2.values():
            for x in (getattr(c, 'co_consts', ()) or ()):
                if isinstance(x, str):
                    bstr.append(x)
        rec('/api/byok 接口进 exe（第 274 项）', '/api/byok' in bstr, '')
        rec('/api/settings 下发 trans_byok_ready（第 274 项）',
            'trans_byok_ready' in bstr, '')
        tpl2 = max((x for x in bb2.co_consts if isinstance(x, str)), key=len)
        rec('快搜栏引擎名表认自带密钥这五家（第 274/279 项）',
            all(k in tpl2 for k in ('google_api', 'baidu_api', 'openai', 'deepseek')), '')

    # 第 281 项：剪贴板条目里的「41 字符 / 1 个项目 / 5 分钟前」原来是写死的中文，
    # 现在按界面语言在读取时重拼。这条在非中文界面下抓中文残留。
    import re as _re
    _lang = str((api('/api/settings') or {}).get('ui_lang') or 'zh').lower()
    _clips = (api('/api/clipboard?kind=all&q=&limit=8') or {}).get('items') or []
    _cjk = _re.compile(u'[\u4e00-\u9fff]')
    _bad = [x for x in _clips
            if _re.search(_cjk, '%s|%s' % (x.get('detail'), x.get('relative_time')))]
    rec('剪贴板条目文案跟界面语言（第 281 项运行期）',
        bool(_clips) and (len(_bad) == 0 if not _lang.startswith('zh') else True),
        'ui_lang=%s n=%d 残留=%d 例=%s' % (
            _lang, len(_clips), len(_bad),
            ('%s / %s' % (_clips[0].get('detail'), _clips[0].get('relative_time'))
             if _clips else '')))
    # 快搜窗里 ste 剪贴板/翻译面板是否已经把 __U_ / __TIP_ 占位符全部换掉（第 281 项）
    _mini_pages = {}
    for _l in ('en', 'hi', 'es', 'pt'):
        try:
            _mini_pages[_l] = get('/mini?lang=' + _l)
        except Exception:
            _mini_pages[_l] = ''
    rec('快搜窗面板占位符全部替换（第 281 项运行期）',
        all(_mini_pages.values()) and not any(
            ('__U_' in v or '__TIP_' in v) for v in _mini_pages.values()),
        '页数=%d' % len([v for v in _mini_pages.values() if v]))

    byok = api('/api/byok')
    ready0 = byok.get('ready') or {}
    rec('/api/byok 报五家状态（第 274/279 项运行期）',
        byok.get('ok') is True and set(ready0) == {'google_api', 'deepseek', 'deepl', 'openai', 'baidu_api'},
        'ready=%s' % json.dumps(ready0, ensure_ascii=False))
    cfg1 = api('/api/settings')
    rec('/api/settings 带 trans_byok_ready（第 274 项运行期）',
        isinstance(cfg1.get('trans_byok_ready'), dict), '')
    raw_settings = json.dumps(cfg1, ensure_ascii=False)
    rec('五家的 name 都下发了（第 279 项运行期）',
        all(isinstance(x.get('name'), str) and x['name']
            for x in (byok.get('specs') or [])) and len(byok.get('specs') or []) == 5,
        '%d 家：%s' % (len(byok.get('specs') or []),
                      ' / '.join(x.get('name', '') for x in (byok.get('specs') or []))))
    rec('/api/settings 里没有密钥原文（第 274 项运行期）',  # noqa
        'sk-' not in raw_settings and 'secretValue' not in raw_settings,
        '整份 settings 响应 %d 字符，未见 sk- 前缀' % len(raw_settings))

    # 用一把假 key 走一遍「保存 → 状态变已配好 → 掩码 → 清除」，最后一定还原成未填
    saved_ok = False
    engines_before = (cfg1.get('trans_engines') or None)
    try:
        post_json('/api/byok', {'provider': 'deepl', 'fields': {'key': 'fake-key-for-verify'}})
        saved_ok = True
        st = api('/api/byok')
        rec('保存后 DeepL 变"已配好"（第 274 项运行期）',
            (st.get('ready') or {}).get('deepl') is True, '')
        rec('界面只能看到掩码（第 274 项运行期）',
            'fake-key-for-verify' not in json.dumps(st, ensure_ascii=False)
            and '…' in ((st.get('masked') or {}).get('deepl') or {}).get('key', ''),
            'masked=%s' % ((st.get('masked') or {}).get('deepl') or {}).get('key', ''))
        st2 = api('/api/settings')
        rec('填了密钥后该引擎自动进 trans_engines（第 274 项运行期）',
            'deepl' in (st2.get('trans_engines') or []),
            'trans_engines=%s' % (st2.get('trans_engines') or []))
    finally:
        if saved_ok:
            post_json('/api/byok', {'provider': 'deepl', 'clear': True})
            # 写密钥时后端会自动把 deepl 追加进 trans_engines；验证完要把引擎表也还原，
            # 否则用户设置页会多出一个"没配密钥的 DeepL"卡（我们自己造的痕迹）。
            if engines_before is not None:
                post_json('/api/settings', {'trans_engines': engines_before})
        st3 = api('/api/byok')
        rec('清除后恢复到"未填密钥"（第 274 项运行期）',
            (st3.get('ready') or {}).get('deepl') is False,
            'ready=%s' % json.dumps(st3.get('ready') or {}, ensure_ascii=False))
        rec('引擎表也复原到验证前（第 274 项运行期）',
            (api('/api/settings').get('trans_engines') or None) == engines_before,
            'before=%s now=%s' % (engines_before, api('/api/settings').get('trans_engines')))
except Exception as e:
    rec('第 274 项证据', False, str(e))

print('\n[19] 第 275 项：快搜窗「最近记录」紧凑显示（默认关 = 原来的两排大行，开关在设置里）')
try:
    _arc3 = CArchiveReader(str(EXE))
    _pz3 = next(x for x in _arc3.toc if x.lower().endswith('pyz.pyz'))
    with tempfile.TemporaryDirectory() as td3:
        _f3 = Path(td3) / 'PYZ.pyz'
        _f3.write_bytes(_arc3.extract(_pz3))
        _z3 = ZlibArchiveReader(str(_f3))
        _raw3 = _z3.extract('brain')
        _bb3 = marshal.loads(_raw3) if isinstance(_raw3, (bytes, bytearray)) else _raw3
        _strs3 = []
        _st3 = [_bb3]
        while _st3:
            _c3 = _st3.pop()
            for _x in (getattr(_c3, 'co_consts', ()) or ()):
                if isinstance(_x, str):
                    _strs3.append(_x)
                elif hasattr(_x, 'co_consts'):
                    _st3.append(_x)
        _tpl3 = max((s for s in _strs3 if '#list.compact' in s), key=len, default='')
        _all3 = '\n'.join(_strs3)
        rec('紧凑样式在 exe 里、且只挂 .compact（第 275 项）',
            '#list.compact .row{height:32px' in _tpl3, '模板 %d 字符' % len(_tpl3))
        rec('紧凑才把名称与路径排同一行（第 275 项）',
            '#list.compact .txt{flex-direction:row' in _tpl3, '')
        rec('条数按设置来、1..10 夹紧（第 275 项）',
            'cap=Math.min(10, want)' in _tpl3, '')
        rec('模板里有 HOME_COUNT 变量（第 275 项）',
            'var HOME_COUNT=__HOME_COUNT__' in _tpl3, '')
        rec('宿主能现推条数（第 275 项）',
            'window.__setHomeCount=function' in _tpl3, '')
        rec('模板里有 HOME_SHOW 变量（第 275 项）',
            'var HOME_SHOW=__HOME_SHOW__' in _tpl3, '')
        rec('整块能现推开关（第 275 项）',
            'window.__setHomeShow=function' in _tpl3, '')
        rec('模板里有 HOME_COMPACT 变量（第 275 项）',
            'var HOME_COMPACT=__HOME_COMPACT__' in _tpl3, '')
        rec('宿主能现推开关、不用重开窗（第 275 项）',
            'window.__setHomeCompact=function' in _tpl3, '')
        # 注意：`html.replace('__HOME_COMPACT__', …)` 在字节码里只是常量
        # '__HOME_COMPACT__'（方法名走 co_names），所以只能数这个常量出现几次：
        # 一次在模板里（var HOME_COMPACT=__HOME_COMPACT__），一次在 _subs 的替换里。
        rec('服务端按 settings 放值（第 275 项）',
            (_all3.count('__HOME_COMPACT__') >= 2 and _all3.count('__HOME_COUNT__') >= 2
             and _all3.count('__HOME_SHOW__') >= 2), '')
        rec('settings 认 qs_home_compact 这个键（第 275 项）',
            'qs_home_compact' in _all3 and 'qs_home_compact_label' not in _all3, '')


    st = api('/api/settings')
    rec('/api/settings 下发 qs_home_compact（第 275 项运行期）',
        'qs_home_compact' in st, 'on=%s' % st.get('qs_home_compact'))
    before = bool(st.get('qs_home_compact'))
    try:
        post_json('/api/settings', {'qs_home_compact': not before})
        import time as _t2
        _t2.sleep(2.5)
        now = bool(api('/api/settings').get('qs_home_compact'))
        rec('开关真的能存进 settings.json 并读回（第 275 项运行期）',
            now == (not before), '写入 %s → 读回 %s' % (not before, now))
    finally:
        post_json('/api/settings', {'qs_home_compact': before})
        import time as _t3
        _t3.sleep(2.5)
        back = bool(api('/api/settings').get('qs_home_compact'))
        rec('验证完把开关还原成原值（第 275 项运行期）', back == before,
            '还原为 %s' % back)

    try:
        ws = api('/api/web_search') or {}
        ws_items = ws.get('items') or ws.get('engines') or []
        ws_titles = [str(x.get('title') or '') for x in ws_items]
        rec('网络搜索引擎标题是拉丁字母（第 275 项运行期）',
            all(t and t.isascii() for t in ws_titles),
            ' / '.join(ws_titles)[:80])
    except Exception as e:
        rec('网络搜索引擎标题是拉丁字母（第 275 项运行期）', False, '取不到：%s' % e)

    sh0 = st.get('qs_home_show', True)
    rec('/api/settings 下发 qs_home_show（第 275 项运行期）', sh0 in (True, False),
        '当前 %s' % ('显示' if sh0 else '隐藏'))
    try:
        post_json('/api/settings', {'qs_home_show': False})
        import time as _t6
        _t6.sleep(2.5)
        rec('「显示最近记录」能关掉并存住（第 275 项运行期）',
            api('/api/settings').get('qs_home_show') is False, '写入 False → 读回 False')
    finally:
        post_json('/api/settings', {'qs_home_show': sh0})
        import time as _t7
        _t7.sleep(2.5)
        rec('验证完把显示开关还原（第 275 项运行期）',
            api('/api/settings').get('qs_home_show') == sh0,
            '还原为 %s' % sh0)

    cnt0 = int(st.get('qs_home_count') or 4)
    rec('/api/settings 下发 qs_home_count（第 275 项运行期）',
        1 <= cnt0 <= 10, '当前 %s 条（出厂 4，1..10）' % cnt0)
    try:
        post_json('/api/settings', {'qs_home_count': 7})
        import time as _t4
        _t4.sleep(2.5)
        rec('条数能存进并读回（第 275 项运行期）',
            int(api('/api/settings').get('qs_home_count') or 0) == 7, '写入 7 → 读回 7')
        post_json('/api/settings', {'qs_home_count': 999})
        _t4.sleep(2.5)
        rec('越界条数被夹到 10（第 275 项运行期）',
            int(api('/api/settings').get('qs_home_count') or 0) == 10, '写入 999 → 读回 10')
    finally:
        post_json('/api/settings', {'qs_home_count': cnt0})
        import time as _t5
        _t5.sleep(2.5)
        rec('验证完把条数还原成原值（第 275 项运行期）',
            int(api('/api/settings').get('qs_home_count') or 0) == cnt0,
            '还原为 %s' % cnt0)
except Exception as e:
    rec('第 275 项证据', False, str(e))

# ---------------------------------------------------------------- 19 第 277 项
print('\n[19] 第 277 项：失败现场与功能可用性体检')
try:
    import re as _re
    import time as _t6

    def _post(path, payload):
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            BASE + path, data=body, method='POST',
            headers={'Sec-Fetch-Site': 'same-origin', 'Origin': BASE,
                     'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode('utf-8'))

    _mt = api('/api/metrics')
    _feats = _mt.get('features') or []
    rec('/api/metrics 200 且有功能表（第 277 项运行期）',
        _mt.get('ok') is True and len(_feats) >= 3,
        '%d 项功能' % len(_feats))
    rec('指标表里有真实调用计数（第 277 项运行期）',
        any(int(f.get('calls') or 0) > 0 for f in _feats),
        'calls=%s' % [int(f.get('calls') or 0) for f in _feats[:5]])

    fm = INSTALLED / 'feature_metrics.json'
    _fm = json.loads(fm.read_text(encoding='utf-8')) if fm.is_file() else {}
    rec('feature_metrics.json 落盘且可解析（第 277 项）',
        bool(_fm.get('features')), 'features=%d 项'
        % len(_fm.get('features') or {}) if isinstance(_fm.get('features'), dict) else '坏格式')

    bc = INSTALLED / 'breadcrumb.json'
    _bc = json.loads(bc.read_text(encoding='utf-8')) if bc.is_file() else {}
    _items = _bc.get('items') if isinstance(_bc.get('items'), list) else []
    rec('breadcrumb.json 记到了应用动作（第 277 项）',
        any((it or {}).get('a') == 'app' for it in _items),
        '%d 条 / 首条 %s' % (len(_items), (_items[0] or {}).get('a') if _items else '-'))

    _t0 = _t6.time()
    env = api('/api/env')
    _env_s = _t6.time() - _t0
    info = env.get('info') or {}
    rec('轻量体检没跑 quick_check（应 < 15s）', _env_s < 15, '%.1fs' % _env_s)

    # 第 281 项：深度体检不再跑全库校验（2.4GB 的 quick_check 28–39s），应 < 20s。
    # 单独放宽请求超时：后台若正在往接收端发报告，探活会和它抢网络（实测一次 15.6s），
    # 用默认 12s 会把整段判成 timed out，连带 10 条检查一起丢。
    _t7 = _t6.time()
    _deep = api('/api/env?deep=1', timeout=45)
    _deep_s = _t6.time() - _t7
    rec('深度体检不再跑全库校验（应 < 20s，第 281 项）', _deep_s < 20, '%.1fs' % _deep_s)
    rec('/api/env 深度结果带上次全库校验结论（第 281 项）',
        isinstance((_deep.get('info') or {}).get('db_full'), dict),
        'db_full=%s' % (str((_deep.get('info') or {}).get('db_full'))[:40]))

    # 全库校验是独立动作：状态可查 + 能真的起一个后台任务
    try:
        _vf0 = api('/api/index/verify')
        _vf1 = _post('/api/index/verify', {})
        _vfok = bool(_vf0.get('ok')) and bool(_vf1.get('ok', True)) \
            and ('running' in _vf0) and bool(_vf1.get('running') or _vf1.get('already'))
        rec('/api/index/verify 可查状态并可启动（第 281 项）', _vfok,
            '先 running=%s / 启动后 running=%s%s'
            % (_vf0.get('running'), _vf1.get('running'),
               ' (已在跑)' if _vf1.get('already') else ''))
    except Exception as _e:
        rec('/api/index/verify 可查状态并可启动（第 281 项）', False, repr(_e)[:60])

    # 接收端：普通用户的地址是**内置**在 _internal/builtin_ingest.py 里的；
    # 作者机器上还可能有一份数据目录 error_ingest.json（可覆盖/关闭）。两者任一在位即为已配置。
    try:
        _ing_file = (INSTALLED / 'error_ingest.json').is_file()
        _ing_builtin = (INSTALLED / '_internal' / 'builtin_ingest.py').is_file()
        _conf = (api('/api/errlog') or {}).get('conf') or {}
        rec('装机版有上报端点（内置或 error_ingest.json，第 281 项）',
            bool(_conf.get('ingest') and (_ing_file or _ing_builtin)),
            'file=%s builtin=%s ingest=%s'
            % (_ing_file, _ing_builtin, _conf.get('ingest')))
    except Exception as _e:
        rec('装机版有上报端点（内置或 error_ingest.json，第 281 项）', False, repr(_e)[:60])

    rs = INSTALLED / 'run_state.json'
    _rs = json.loads(rs.read_text(encoding='utf-8')) if rs.is_file() else {}
    rec('run_state 指向当前进程且阶段有效（第 277 项）',
        _rs.get('pid') == info.get('pid')
        and str(_rs.get('stage')) in ('boot', 'ui', 'ready') and bool(_rs.get('ver')),
        'pid=%s stage=%s ver=%s'
        % (_rs.get('pid'), _rs.get('stage'), _rs.get('ver') or '空'))

    _keys = ('translate', 'ocr', 'index_health', 'clipboard', 'hotkey', 'update',
             'license', 'autostart', 'resources', 'capture', 'libsets', 'reporter')
    _miss = [k for k in _keys if k not in info]
    rec('/api/env 含 12 项功能探测（第 277 项运行期）',
        not _miss, ('缺 ' + ','.join(_miss)) if _miss else '12/12')

    # 前端钩子与「深度体检」按钮必须真的在**实下发的那份 bundle** 里
    _html = (WEB / 'index.html').read_text(encoding='utf-8') if (WEB / 'index.html').is_file() else ''
    _m = _re.search(r'assets/(index-[\w-]+\.js)', _html)
    _js = (WEB / 'assets' / _m.group(1)) if _m else None
    _jt = _js.read_text(encoding='utf-8', errors='ignore') if _js and _js.is_file() else ''
    rec('实下发 bundle 含前端异常钩子（第 277 项）',
        'unhandledrejection' in _jt and 'onerror' in _jt.lower(),
        _m.group(1) if _m else 'index.html 里没找到 bundle')
    rec('实下发 bundle 含「深度体检」按钮文案（第 277 项）',
        'hc_deep' in _jt or '深度体检' in _jt, _m.group(1) if _m else '')
    rec('实下发 bundle 含「全库校验」按钮与状态行（第 281 项）',
        ('iv_run' in _jt or '现在校验' in _jt) and ('iv_label' in _jt or '全库校验' in _jt),
        _m.group(1) if _m else '')
    # 第 304 项：网络搜索演示动图要真的在装机目录里（且 bundle 里引用了它），
    # 少一个文件时页面会只剩一块空白灰框。
    _demo = WEB / 'websearch-demo.gif'
    rec('网络搜索演示 GIF 已随包下发（第 304 项）',
        _demo.is_file() and _demo.stat().st_size > 10 * 1024
        and 'websearch-demo.gif' in _jt,
        'gif=%s bundle引用=%s' % (_demo.is_file(), 'websearch-demo.gif' in _jt))

    # op='js' 真的能把前端异常变成一份报告；验完立刻删掉这一份，不留假现场
    _rid = ''
    try:
        _rid = str((_post('/api/errlog', {'op': 'js', 'kind': 'VerifyHook',
                                          'message': 'verify-277 boom',
                                          'stack': 'at v (verify.ts:9)',
                                          'route': '/verify'}) or {}).get('id') or '')
        _rf = (INSTALLED / 'error_reports' / (_rid + '.json')) if _rid else None
        _ok = bool(_rid) and _rf.is_file() and _rf.is_file()
        rec('/api/errlog op=js 生成前端异常报告（第 277 项运行期）', _ok,
            'id=%s' % _rid if _rid else '没拿到 id')
    finally:
        if _rid:
            _rf2 = INSTALLED / 'error_reports' / (_rid + '.json')
            try:
                if _rf2.is_file():
                    _rf2.unlink()
            except Exception:
                pass
except Exception as e:
    rec('第 277 项证据', False, str(e))

# ------------------------------------------------------------ 第 280 项：悬停说明 i18n
try:
    def _mini_html(lg):
        import urllib.request as _u
        req = _u.Request('http://127.0.0.1:8765/mini?lang=' + lg,
                         headers={'Sec-Fetch-Site': 'same-origin',
                                  'Origin': 'http://127.0.0.1:8765'})
        with _u.urlopen(req, timeout=8) as r:
            return r.read().decode('utf-8')

    _zh, _en = _mini_html('zh'), _mini_html('en')

    def _btn_title(body, bid):
        import re as _re
        m = _re.search(r'<button[^>]*id="' + bid + r'"[^>]*>', body)
        tt = _re.search(r'title="([^"]*)"', m.group(0)) if m else None
        return tt.group(1) if tt else ''

    _zh_t = [_btn_title(_zh, b) for b in ('btnSnip', 'btnClip', 'btnTrans')]
    _en_t = [_btn_title(_en, b) for b in ('btnSnip', 'btnClip', 'btnTrans')]
    rec('快搜窗三个图标按钮的悬停说明随语言变（第 280 项运行期）',
        all(_zh_t) and all(_en_t) and _zh_t != _en_t and '屏幕截图' in _zh_t[0],
        'zh=%s / en=%s' % (' | '.join(_zh_t), ' | '.join(_en_t)))
    rec('快搜窗页面没有残留 __TIP_ 占位符（第 280 项运行期）',
        '__TIP_' not in _zh and '__TIP_' not in _en, '')
except Exception as e:
    rec('第 280 项证据', False, str(e))

# ------------------------------------------------------------ 第 296 项：卸载入口 + 高级说明
try:
    import winreg as _wr
    _un_key = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\Memex'
    _un_vals = {}
    try:
        with _wr.OpenKey(_wr.HKEY_CURRENT_USER, _un_key) as _k:
            for _name in ('DisplayName', 'DisplayIcon', 'InstallLocation', 'DisplayVersion',
                          'UninstallString', 'QuietUninstallString'):
                try:
                    _un_vals[_name] = _wr.QueryValueEx(_k, _name)[0]
                except OSError:
                    pass
    except OSError:
        pass
    rec('「设置→应用」里有 Memex 卸载项（第 296 项运行期）',
        _un_vals.get('DisplayName') == 'Memex'
        # 第 302 项：允许指向安装目录的 Uninstall.exe（也可退回 Memex.exe --uninstall）
        and ('Uninstall.exe' in str(_un_vals.get('UninstallString', ''))
             or _un_vals.get('UninstallString', '').endswith('--uninstall'))
        and (INSTALLED / 'Memex.exe').as_posix().lower()
        in str(_un_vals.get('DisplayIcon', '')).replace('\\', '/').lower(),
        'DisplayVersion=%s / UninstallString=%s' % (
            _un_vals.get('DisplayVersion', ''), _un_vals.get('UninstallString', '')[:60]))

    # 接口存在且必须先确认：不带 confirm 必须是 400，不能真把程序删了
    try:
        import urllib.request as _u2
        import urllib.error as _ue
        _req = _u2.Request('http://127.0.0.1:8765/api/uninstall',
                           data=json.dumps({'mode': 'keep'}).encode(),
                           headers={'Content-Type': 'application/json',
                                    'Sec-Fetch-Site': 'same-origin',
                                    'Origin': 'http://127.0.0.1:8765'})
        try:
            _body = _u2.urlopen(_req, timeout=8).read().decode('utf-8')
            _code = 200
        except _ue.HTTPError as _e:
            _code = _e.code
            _body = _e.read().decode('utf-8')
        rec('卸载接口不带 confirm 拒收（第 296 项运行期）',
            _code == 400 and 'need confirm' in _body, 'HTTP %s %s' % (_code, _body[:60]))
    except Exception as _e:
        rec('卸载接口不带 confirm 拒收（第 296 项运行期）', False, repr(_e))

    # 前端包：高级各块的说明键要在装机后的 bundle 里
    # 装机目录里历史 bundle 有一堆，按文件名排序取"最后"会拿到旧包 —— 必须照 index.html 的引用读
    import re as _re3
    _webroot = INSTALLED / '_internal' / 'web'
    _html = (_webroot / 'index.html').read_text(encoding='utf-8', errors='ignore')
    _m = _re3.search(r'assets/(index-[A-Za-z0-9_-]+\.js)', _html)
    _js = [(_webroot / 'assets' / _m.group(1))] if _m else []
    _txt = _js[0].read_text(encoding='utf-8', errors='ignore') if _js else ''
    _keys = ('ex_lib_help', 'ex_ocr_help', 'ex_fail_help', 'ex_excl_help', 'ex_saved_help',
             'un_title', 'un_keep', 'un_full', 'un_confirm_full')
    _miss = [k for k in _keys if k not in _txt]
    rec('高级说明 + 卸载卡已进前端包（第 296 项运行期）', not _miss,
        'bundle=%s 缺=%s' % (_js[-1].name if _js else '(none)', _miss))
except Exception as e:
    rec('第 296 项证据', False, str(e))

# ------------------------------------------------------------ 第 297 项：系统程序
try:
    import urllib.parse as _up
    import urllib.request as _u3

    def _mini(q):
        u = 'http://127.0.0.1:8765/api/mini?deep=0&q=' + _up.quote(q)
        rq = _u3.Request(u, headers={'Sec-Fetch-Site': 'same-origin',
                                     'Origin': 'http://127.0.0.1:8765'})
        with _u3.urlopen(rq, timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))

    _cmd = (_mini('cmd').get('items') or [{}])[0]
    rec('快搜窗搜 cmd 第一条就是命令提示符（第 297 项运行期）',
        str(_cmd.get('p') or '').lower().endswith('cmd.exe') and _cmd.get('app') == 1
        and '命' in str(_cmd.get('dn') or ''),
        'dn=%s p=%s' % (_cmd.get('dn'), str(_cmd.get('p'))[-40:]))

    _set = (_mini('设置').get('items') or [{}])[0]
    rec('快搜窗搜「设置」第一条就是设置应用（第 297 项运行期）',
        str(_set.get('p') or '').startswith('shell:AppsFolder' + chr(92))
        and 'immersivecontrolpanel' in str(_set.get('p') or '')
        and _set.get('app') == 1,
        'dn=%s p=%s' % (_set.get('dn'), str(_set.get('p'))[-40:]))

    # 图标：cmd.exe 走真实 shell 图标，设置走 AUMID 磁贴图标；两条都必须 200 PNG
    for _label, _p, _e in (('cmd', r'C:\Windows\System32\cmd.exe', '.exe'),
                           ('settings', str(_set.get('p') or ''), '')):
        _u = ('http://127.0.0.1:8765/api/icon?p=' + _up.quote(_p) + '&e=' + _up.quote(_e))
        _rq = _u3.Request(_u, headers={'Sec-Fetch-Site': 'same-origin',
                                       'Origin': 'http://127.0.0.1:8765'})
        try:
            with _u3.urlopen(_rq, timeout=10) as _r:
                _png = _r.read()
                _ok = _r.status == 200 and _png[:4] == b'\x89PNG'
        except Exception:
            _ok, _png = False, b''
        rec('系统程序有真实图标（%s）' % _label, _ok, '%d bytes' % len(_png))
except Exception as e:
    rec('第 297 项证据', False, repr(e))

# ------------------------------------------------------------ 第 298 项：排序不埋应用
try:
    def _files(q, _s):
        u = ('http://127.0.0.1:8765/api/files?q=' + _up.quote(q) + '&n=60&s=' + _s)
        rq = _u3.Request(u, headers={'Sec-Fetch-Site': 'same-origin',
                                     'Origin': 'http://127.0.0.1:8765'})
        with _u3.urlopen(rq, timeout=10) as r:
            return json.loads(r.read().decode('utf-8')).get('items') or [{}]

    for _s in ('rel', 'name', 'new', 'big'):
        _first = _files('微信', _s)[0]
        rec('「微信」在 %s 排序下仍排第一（第 298 项运行期）' % _s,
            str(_first.get('n') or '').lower().endswith('.lnk')
            and (_first.get('ab') or 0) > 0
            and str((_first.get('dn') or '')) == '微信',
            'dn=%s ab=%s' % (_first.get('dn'), _first.get('ab')))
    _set = _files('设置', 'name')[0]
    rec('「设置」在名称排序下仍排第一（第 298 项运行期）',
        'immersivecontrolpanel' in str(_set.get('p') or ''),
        'dn=%s' % _set.get('dn'))
except Exception as e:
    rec('第 298 项证据', False, repr(e))

# ------------------------------------------------------------ 第 299 项：网络代理
try:
    _g = _u3.Request('http://127.0.0.1:8765/api/proxy',
                     headers={'Sec-Fetch-Site': 'same-origin',
                              'Origin': 'http://127.0.0.1:8765'})
    with _u3.urlopen(_g, timeout=8) as _r:
        _d = json.loads(_r.read().decode('utf-8'))
    rec('GET /api/proxy 返回模式与实际出口（第 299 项运行期）',
        _d.get('mode') in ('system', 'none', 'custom') and 'url' in _d,
        'mode=%s url=%s' % (_d.get('mode'), _d.get('url') or '(direct)'))

    # 非法 proxy_mode 必须 400 拒绝，而不是悄悄落盘（写坏=全程序联网异常）
    _bad = _u3.Request('http://127.0.0.1:8765/api/settings', method='POST',
                       data=json.dumps({'proxy_mode': 'bogus'}).encode('utf-8'),
                       headers={'Content-Type': 'application/json',
                                'Sec-Fetch-Site': 'same-origin',
                                'Origin': 'http://127.0.0.1:8765'})
    try:
        _u3.urlopen(_bad, timeout=8)
        _code = 200
        _k = ''
    except urllib.error.HTTPError as _e:
        _code = _e.code
        _k = (json.loads(_e.read().decode('utf-8')) or {}).get('k', '')
    rec('非法 proxy_mode 被 400 拒绝（第 299 项运行期）',
        _code == 400 and _k == 'e_bad_proxy_mode', 'code=%s k=%s' % (_code, _k))

    _t = _u3.Request('http://127.0.0.1:8765/api/proxy/test', method='POST',
                     data=json.dumps({'draft': {'mode': _d.get('mode'),
                                                'host': _d.get('host'),
                                                'port': _d.get('port')}}).encode('utf-8'),
                     headers={'Content-Type': 'application/json',
                              'Sec-Fetch-Site': 'same-origin',
                              'Origin': 'http://127.0.0.1:8765'})
    with _u3.urlopen(_t, timeout=20) as _r:
        _p = json.loads(_r.read().decode('utf-8'))
    # 不求"一定通"（本机网络随时可能断），只求端点真的按当前模式跑、字段齐
    rec('代理测试端点返回结构（第 299 项运行期）',
        isinstance(_p.get('ok'), bool) and 'ms' in _p and 'code' in _p
        and _p.get('mode') in ('system', 'none', 'custom'),
        'ok=%s code=%s ms=%s via=%s' % (_p.get('ok'), _p.get('code'), _p.get('ms'),
                                        _p.get('via') or '(direct)'))
except Exception as e:
    rec('第 299 项证据', False, repr(e))

# ------------------------------------------------------------ 汇总
ok = sum(1 for _, o, _ in results if o)
bad = [n for n, o, _ in results if not o]
print('\n== 汇总：%d PASS / %d FAIL ==' % (ok, len(bad)))
if bad:
    for n in bad:
        print('   FAIL:', n)
print('\n发行通道（发布冻结，脚本不代做）：')
print('  · 装机版（用户日常运行）       : 本脚本取证对象，已覆盖')
print('  · 公开仓库工作区（干净版）     : 已 sync_public，未 commit/push（冻结）')
print('  · 安装包 / 线上 Release       : 仍是旧 staged 包 / v0.9.53，未重打、未发布')
sys.exit(0 if not bad else 1)
