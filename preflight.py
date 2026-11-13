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
REPO = ROOT.parent / '上传的干净版'
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


_Q = r"""('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")"""
# 允许与英文完全相同的键：西语里 General/Audio 本来就写成一样。
# 新加键如果确实同形，加到这里并写一行理由 —— 别为了过门禁瞎改词。
_I18N_SAME_OK = {'kind_audio', 'care_title'}


def _i18n_dicts(s):
    """把 i18n.ts 五个字典块解析成 {键: 值}（值可能用单引号或双引号、可能跨行拼接）。"""
    out = {}
    for n in ('ZH', 'EN', 'ES', 'PT', 'HI'):
        m = re.search(r'const %s\s*:\s*Dict\s*=\s*\{(.*?)\n\}' % n, s, re.S)
        d = {}
        if m:
            for mm in re.finditer(r"^  ([A-Za-z0-9_]+)\s*:\s*((?:%s\s*\n?\s*)+)" % _Q,
                                  m.group(1), re.M):
                d[mm.group(1)] = ''.join(p[1:-1] for p in re.findall(_Q, mm.group(2)))
        out[n] = d
    return out


def check_i18n():
    """五语言：键集合一致 + 没有「英文占位没翻」+ 占位符 {n} 不能少。

    第三条是第 176 项踩的坑：新面板的键只加进了中文和英文，西/葡/印地的值
    是英文占位，用户切到印地语看到整页英文，反馈「怎么只有英语和中文」。
    键对齐查不出来（键都齐），必须比值。
    """
    s = text(ROOT / 'web/src/lib/i18n.ts')
    D = _i18n_dicts(s)
    zh, en = D['ZH'], D['EN']
    bad = ['%s:%d' % (n, len(D[n])) for n in ('EN', 'ES', 'PT', 'HI') if set(D[n]) != set(zh)]
    rec('PASS' if zh and not bad else 'FAIL', '五语言字典键完全对齐',
        '%d 键 %s' % (len(zh), ' '.join(bad)))

    left = []
    ph = []
    for n in ('ES', 'PT', 'HI'):
        for k, v in D[n].items():
            if en.get(k) and v == en[k] and k not in _I18N_SAME_OK:
                left.append('%s.%s' % (n, k))
            if sorted(re.findall(r'\{\w+\}', en.get(k, ''))) != sorted(re.findall(r'\{\w+\}', v)):
                ph.append('%s.%s' % (n, k))
    rec('PASS' if not left else 'FAIL', '西/葡/印地没有英文占位',
        ('%d 条未翻：%s' % (len(left), ' '.join(left[:6]))) if left else '逐条与英文比对，0 条占位')
    rec('PASS' if not ph else 'FAIL', '五语言占位符 {x} 一致',
        ('%d 条不一致：%s' % (len(ph), ' '.join(ph[:6]))) if ph else '占位符集合逐键一致')


def check_env_i18n():
    """体检文案五语言（第 176 项）：env_msgs 的键 / %占位符必须与 env_check._EN 一致。

    这两张表是用 % 格式化的：少一个 %s 就是运行期 TypeError（体检面板整块空），
    所以键对齐不够，占位符顺序与类型也必须一模一样。
    """
    import ast

    def grab(path, names):
        got = {}
        p = ROOT / path
        if not p.is_file():
            return got
        for node in ast.parse(text(p)).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names:
                try:
                    got[node.targets[0].id] = ast.literal_eval(node.value)
                except Exception:
                    pass
        return got

    en = grab('env_check.py', {'_EN'}).get('_EN') or {}
    tabs = grab('env_msgs.py', {'ES', 'PT', 'HI'})
    if not en or len(tabs) != 3:
        rec('FAIL', '体检文案五语言键对齐', 'env_check._EN=%d 表=%d' % (len(en), len(tabs)))
        return
    bad = []
    for n in ('ES', 'PT', 'HI'):
        t = tabs[n]
        if set(t) != set(en):
            bad.append('%s 键差 %s' % (n, sorted(set(t) ^ set(en))[:4]))
            continue
        for k, v in t.items():
            a = re.findall(r'%[sd]|%\.\d+f', en[k])
            if a != re.findall(r'%[sd]|%\.\d+f', v):
                bad.append('%s.%s 占位符 %s≠%s' % (n, k, a, re.findall(r'%[sd]|%\.\d+f', v)))
    rec('PASS' if not bad else 'FAIL', '体检文案五语言键对齐',
        '; '.join(bad[:4]) if bad else '%d 键 × 西/葡/印地，占位符一致' % len(en))



def check_header_actions():
    """「体检与报错」入口（第 178 项建、第 181 项按用户三次要求改）。

    用户叠加后的形态，每一条都是他明确说过的：
    ① 体检 + 报错**合成一个按钮**（原来两颗纯图标）；
    ② 按钮形态**改过两次**：第 181 项要求带文字（「不然其他人不知道这个按钮是干嘛的」），
       第 188 项又拍板改回**纯图标**，并要求跟 − □ ✕ 同格、同间距；
       所以现在断言的是「有图标 + 有 aria-label + 不许再出现文字」；
    ③ 点了**开自己的弹窗**，不许跳设置；
    ④ **设置按钮留在顶栏**，别放标题栏（他没让放）。

    任何一条被后面的重构改掉，用户会立刻看见，所以逐条断言。
    第 187 项起：设置里那两块已经删掉，锚点 `id="health"` 不许再回来
    （`id="feedback"` 还在错误日志组件内部当标题锚点，无害）。
    """
    app = text(ROOT / 'web/src/App.tsx')
    pan = text(ROOT / 'web/src/ui/panels.tsx')
    tbr = text(ROOT / 'web/src/ui/titlebar.tsx')
    bad = []
    if 'data-act="diag"' not in app and 'data-act="diag"' not in tbr:
        bad.append('没有 data-act="diag"（体检与报错按钮）')
    if 'data-act="health"' in app or 'data-act="errlog"' in app:
        bad.append('旧的 health/errlog 两颗按钮还在（应合成一个）')
    # 第 188 项：用户改主意了 —— 标题栏改回**纯图标**，但 title/aria-label 必须有，
    # 否则这颗键对读屏和没鼠标的人是哑的。同时不许再出现按文案撑宽的文字按钮。
    if "t('diag_title')" not in tbr or "aria-label={t('diag_title')}" not in tbr:
        bad.append('标题栏「体检与报错」缺 aria-label（纯图标不能变成哑巴键）')
    if "t('diag_btn')" in tbr:
        bad.append('标题栏又变回文字按钮了（第 188 项已改纯图标）')
    if 'onClick={() => setDiagOpen(true)}' not in app:
        bad.append('顶栏点击没开独立弹窗')
    if 'onDiag={() => setDiagOpen(true)}' not in app:
        bad.append('标题栏点击没开独立弹窗')
    if 'DiagnosticsModal' not in app or 'export function DiagnosticsModal' not in pan:
        bad.append('缺 DiagnosticsModal 弹窗')
    if 'id="health"' in pan:
        bad.append('设置里的环境体检锚点又回来了（第 187 项已从设置删除）')
    if 'ErrorLogPanel' not in pan:
        bad.append('错误日志没抽成可独立渲染的组件')
    if 'data-act="settings"' not in app:
        bad.append('顶栏缺设置按钮')
    if 'onSettings' in tbr:
        bad.append('设置按钮又跑进标题栏了（用户没让放）')
    if 'focus=(feedback|why|health)' not in app or 'setDiagOpen(true)' not in app:
        bad.append('?focus=feedback/health 没接到弹窗')
    if 'flex flex-wrap items-center gap-2' not in app:
        bad.append('顶栏按钮行没有 flex-wrap（窄窗口会裁掉按钮）')
    # 第 188 项：和 − □ ✕ 同格同间距 —— 46 宽、图标居中、谁都不许按文案撑宽
    if 'w-[46px]' not in tbr:
        bad.append('标题栏键不是 46 宽格子（和 − □ ✕ 对不齐）')
    if 'px-3' in tbr:
        bad.append('标题栏还有按文案撑宽的按钮（px-3）')
    if '<IconHealth' not in tbr:
        bad.append('「体检与报错」没有图标')
    # 第 184 项：GitHub 议题入口从「关于」搬到这里，且不许两处都在
    if "t('diag_issue_hint')" not in pan:
        bad.append('体检弹窗缺「开个议题」入口')
    if "[t('about_issues'), ping.issues" in pan:
        bad.append('「关于」里还留着 GitHub 议题（应只在这里）')
    rec('PASS' if not bad else 'FAIL', '体检与报错入口（合并/文字/独立弹窗/设置留顶栏）',
        '; '.join(bad[:4]) if bad else '一颗图标键开独立弹窗（46px 同格），设置仍在顶栏，议题入口齐全')


def check_support_entry():
    """赞助独立入口（第 185 项）。

    用户原话：「关于」里的「支持一下」打赏功能做成独立的按钮（参考形态是
    左边名单 + 右边大二维码的卡片）。三条要求：
    ① 按钮在标题栏上（红心 + 文字，跟体检那颗同尺寸同间距）；
    ② 点了开**自己的弹窗**，不跳「关于」；
    ③ 二维码要**真的搬走**——关于里只留入口按钮，不是复制一份摆两处。
    另外防篡改判定（official !== false）不许因为搬位置丢掉：那是第 96 项的红线。
    """
    app = text(ROOT / 'web/src/App.tsx')
    pan = text(ROOT / 'web/src/ui/panels.tsx')
    tbr = text(ROOT / 'web/src/ui/titlebar.tsx')
    i18n = text(ROOT / 'web/src/lib/i18n.ts')
    bad = []
    if 'data-act="support"' not in tbr:
        bad.append('标题栏缺赞助按钮')
    if "t('sp_title')" not in tbr or "aria-label={t('sp_title')}" not in tbr:
        bad.append('标题栏赞助键缺 aria-label（纯图标不能变成哑巴键）')
    if "t('sponsor')" in tbr:
        bad.append('标题栏赞助键又变回文字了（第 188 项改纯图标）')
    if 'IconCoffee' not in tbr:
        bad.append('赞助键没有换成咖啡杯图标')
    if 'data-act="support"' not in app:
        bad.append('顶栏缺兜底入口（摘不到系统标题栏时就没地方点了）')
    if 'SupportModal' not in app or 'export function SupportModal' not in pan:
        bad.append('缺独立的赞助弹窗')
    if 'onSupport={() => setSupportOpen(true)}' not in app:
        bad.append('标题栏点不动（没接 onSupport）')
    # 二维码必须搬走：关于面板体内不许再出现二维码渲染
    i = pan.index('export function AboutModal')
    j = pan.find('export function Modal', i)
    body = pan[i:j if j > 0 else len(pan)]
    if '<DonateQR' in body or "t('donate_alt')" in body:
        bad.append('二维码还留在「关于」里（应只留入口按钮）')
    if 'official !== false' not in pan:
        bad.append('指纹校验被丢了（第 96 项红线）')
    for k in ('sponsor', 'sp_title', 'sp_head', 'sp_tiers', 'sp_scan', 'sp_none'):
        if (k + ':') not in i18n:
            bad.append('缺 i18n 键 %s' % k)
    rec('PASS' if not bad else 'FAIL', '赞助独立入口（标题栏按钮 / 独立弹窗 / 二维码搬走）',
        '; '.join(bad[:4]) if bad else '标题栏咖啡杯图标键、独立 SupportModal、关于只留入口、指纹校验仍在')


def check_settings_slim():
    """设置瘦身（第 187 项）。

    用户两句原话：
    ①「设置里的『错误日志』和『环境体检』可以删除了，因为标题栏那边已经增加了按钮了」；
    ②「『为什么搜不到』也在『使用帮助』里面有按钮了，改成点击按钮后弹出来，
       不要留在设置里面了」。

    这三块都是「两处各有一份」的典型：看着方便，实际两边状态容易不同步，
    用户还会以为设置里那份才是完整版。现在统一成：
    体检/报错 → 标题栏按钮的 DiagnosticsModal；为什么搜不到 → 帮助按钮的 WhyNotFoundModal。
    """
    pan = text(ROOT / 'web/src/ui/panels.tsx')
    app = text(ROOT / 'web/src/App.tsx')
    bad = []

    drawer = pan[pan.index('export function SettingsDrawer'):pan.index('export function HealthPanel')]
    if 'HealthPanel' in drawer:
        bad.append('设置里还渲染着环境体检')
    care = pan[pan.index('export function CarePanel('):pan.index('function ExtrasPanel(')]
    if '<ErrorLogPanel' in care:
        bad.append('设置里还渲染着错误日志')
    if 'id="why-not-found"' in pan:
        bad.append('设置里还留着「为什么搜不到」那块')

    if 'export function WhyNotFoundModal' not in pan:
        bad.append('缺独立的「为什么搜不到」弹窗')
    if '<WhyNotFoundModal' not in app:
        bad.append('App 没渲染 WhyNotFoundModal')
    # 帮助里的按钮必须直连弹窗，不许再回到 setPanel('settings')
    if "onWhyNotFound={() => { setPanel(null); setWhyOpen(true) }}" not in app:
        bad.append('帮助按钮没接弹窗（又往设置跳了）')
    if "if (fm[1] === 'why') setWhyOpen(true)" not in app:
        bad.append('?focus=why 没接到弹窗')
    if "t('why_modal_hint')" not in pan:
        bad.append('弹窗缺一句说明（why_modal_hint）')
    rec('PASS' if not bad else 'FAIL', '设置瘦身（体检 / 错误日志 / 为什么搜不到都搬出去了）',
        '; '.join(bad[:4]) if bad else '设置里三块已删，帮助按钮与 ?focus=why 都直连 WhyNotFoundModal')


def check_side_actions():
    """左栏「使用帮助 / 设置 / 关于」必须带图标（第 190 项）。

    用户原话：「左边栏里，『使用帮助』『设置』『关于』也弄个图标吧，不然感觉有点丑」。
    这三个原来只有文字：比上面「类型 / 时间」两组少一列图标，行高也更矮（py-1 对 py-1.5），
    看着像画漏了。现在三颗图标 + 同样的 gap-2.5 / py-1.5，只是不带选中态（它们不是筛选）。

    断言两件事：三颗图标都在；**旧的纯文字数组不许回来**（那就是退回原样）。
    """
    app = text(ROOT / 'web/src/App.tsx')
    i = app.index('<aside className="flex w-[172px]')
    side = app[i:app.index('</aside>', i)]
    bad = []
    for ic in ('<IconHelp size={16} />', '<IconGear size={16} />', '<IconInfo size={16} />'):
        if ic not in side:
            bad.append('左栏缺图标 %s' % ic)
    if "[['help', t('help')], ['settings', t('settings')], ['about', t('about')]]" in side:
        bad.append('左栏又退回纯文字（三个入口没图标）')
    if 'gap-2.5' not in side:
        bad.append('左栏入口的图标间距与上面两组不一致')
    rec('PASS' if not bad else 'FAIL', '左栏三个入口带图标（帮助 / 设置 / 关于）',
        '; '.join(bad[:4]) if bad else '圆圈问号 / 滑块 / 圆圈 i，间距行高与类型·时间两组一致')


def check_overlay_top():
    """弹窗遮罩让开标题栏（第 189 项）。

    用户原话：「点击『关于』与『使用帮助』后标题栏那里又出现了双层，
    一层白一层灰蓝」。

    根因不在前端配色：窗口最顶上那 8-9px 是 **DWM 画的 caption 带**（见
    `paint_frame` 的注释），它不在网页里，遮罩永远盖不到它。遮罩要是连网页
    里的标题栏一起压暗，上面一条保持窗口底色、下面一条变暗 —— 就是「双层」。
    所以遮罩的 top 走 `--sb-title`（自绘标题栏高度，App 按 /api/ping.titlebar
    设到 documentElement 上，导览是用 createPortal 挂 body 也能继承到）。

    另一条：官方地址原来一行一个 flex + ml-auto，地址短的（源码仓库）被推到
    最右边，两行的地址一头一尾 —— 所以改成同一张两列网格，名称和地址各占一列。
    """
    css = text(ROOT / 'web/src/index.css')
    app = text(ROOT / 'web/src/App.tsx')
    pan = text(ROOT / 'web/src/ui/panels.tsx')
    ctx = text(ROOT / 'web/src/ui/context.tsx')
    ob = text(ROOT / 'web/src/ui/onboarding.tsx')
    bad = []

    if '--sb-title: 0px' not in css:
        bad.append('index.css 没有 --sb-title 默认值')
    if "setProperty('--sb-title'" not in app:
        bad.append('App 没按 titlebar 把 --sb-title 设到 documentElement')
    # 四个遮罩都要让开：通用弹窗 / 右键预览 / 打赏二维码放大 / 导览
    want = ((pan, 2, 'panels.tsx（弹窗 + 二维码放大）'),
            (ctx, 1, 'context.tsx（预览弹层）'),
            (ob, 1, 'onboarding.tsx（导览）'))
    for src, n, label in want:
        got = src.count('var(--sb-title')
        if got < n:
            bad.append('%s 的遮罩没让开标题栏（%d/%d 处）' % (label, got, n))
    if 'grid-cols-[auto_1fr]' not in pan:
        bad.append('官方地址两行没用同一张网格（长短不同就会参差）')
    if 'max-w-[60%]' in pan:
        bad.append('官方地址又退回 ml-auto + max-w（两行对不齐的老写法）')

    rec('PASS' if not bad else 'FAIL', '弹窗遮罩让开标题栏 + 官方地址两列对齐',
        '; '.join(bad[:4]) if bad else '--sb-title 四处遮罩都用了，地址落在同一张两列网格')


def check_appearance():
    """主题配色 + 全局壁纸（第 191 项）。

    用户原话：「把『全局壁纸』『主题配色』这个也做出来，以后好分割成pro的功能」。
    所以两块都做全、**现在不加门控**，但表现层必须收在 palette.ts / appearance.tsx，
    以后加一道条件就能整体切走；这里把这条边界钉住。

    四个必踩的点：
    ① 配色 id 前后端两份列表必须一致（不一致 → 用户点了 400，保存悄悄失败）；
    ② `/api/wall/image` 必须排在 `/api/wall` **前面**（startswith 先到先得）；
    ③ 壁纸接口只认配置里的路径，不接受前端传路径（那是任意文件读取）；
    ④ 遮罩/根容器：壁纸开着时根底色要透明，否则图片永远看不见。
    """
    import re
    ts = text(ROOT / 'web/src/lib/palette.ts')
    appr = text(ROOT / 'web/src/ui/appearance.tsx')
    app = text(ROOT / 'web/src/App.tsx')
    pan = text(ROOT / 'web/src/ui/panels.tsx')
    brain = text(ROOT / 'brain.py')
    bad = []

    block = ts[ts.index('export const PALETTES'):ts.index('export const PALETTE_IDS')]
    ids = re.findall(r"id: '([a-z]+)'", block)
    m = re.search(r'PALETTE_IDS = \((.*?)\)', brain, re.S)
    py_ids = re.findall(r"'([a-z]+)'", m.group(1)) if m else []
    if len(ids) < 15 or ids != py_ids:
        bad.append('配色 id 前后端不一致（前端 %d 个 / 后端 %d 个）' % (len(ids), len(py_ids)))

    if 'export function AppearanceSection' not in appr or 'export function Wallpaper' not in appr:
        bad.append('appearance.tsx 缺 AppearanceSection / Wallpaper')
    if '<AppearanceSection' not in pan:
        bad.append('设置里没接外观区')
    if 'theme_label' in pan and 'THEMES' in pan:
        bad.append('旧的主题下拉还在（会和配色网格两处各一份）')
    if 'applyPalette(' not in app or '<Wallpaper' not in app:
        bad.append('App 没用 applyPalette / 没渲染壁纸层')
    if "background: 'transparent'" not in app:
        bad.append('壁纸开着时根容器底色没放开（图片透不出来）')

    if "/api/wall/image" not in brain or "/api/wall" not in brain:
        bad.append('后端缺 /api/wall 接口')
    else:
        if brain.index("/api/wall/image") > brain.index("elif self.path.startswith('/api/wall')"):
            bad.append('/api/wall/image 排在 /api/wall 后面（会被吃掉）')
    if 'def _wall_path' not in brain or 'WALL_EXT' not in brain:
        bad.append('壁纸路径校验缺失')
    if "if 'palette' in req" not in brain or 'PALETTE_IDS' not in brain:
        bad.append('设置接口没收 palette / 没白名单校验')

    rec('PASS' if not bad else 'FAIL', '主题配色 + 全局壁纸（可整体切 Pro 的边界）',
        '; '.join(bad[:4]) if bad else
        '15 套配色前后端一致；壁纸只读配置里的图；App 有应用与透明底')


def check_timeline_head():
    """时间轴列头常驻 + 说明行在顶栏（第 179 项）。

    两个坑各一条断言：
    ① 列头 `sticky` 只有在「自己所在的那层才是滚动容器」时才有效。列表区的外层
       曾经是 `overflow-auto`，滚动发生在 main 的**父级** → 时间轴列头跟着内容一起走。
    ② 说明行（最近 N 天 · 共 N 条）原来单独占一行，现在搬进顶栏那一行，
       文案函数在 panels，调用在 App —— 两边少一个就退回「两条空行」的老样子。
    """
    app = text(ROOT / 'web/src/App.tsx')
    pan = text(ROOT / 'web/src/ui/panels.tsx')
    bad = []
    if 'sticky top-0 z-[1] flex items-center gap-2 border-b border-line bg-bg' not in pan:
        bad.append('时间轴列头没有 sticky + 实底色')
    if 'flex min-w-0 flex-1 flex-col overflow-hidden' not in app:
        bad.append('列表外层又变成滚动容器了（sticky 会失效）')
    if 'timelineCaption(tlDays, tlTotal)' not in app:
        bad.append('顶栏没渲染时间轴说明行')
    if 'export function timelineCaption(' not in pan:
        bad.append('panels 缺 timelineCaption')
    if 'setDays(7)' in pan or 'setDays(30)' in pan:
        bad.append('时间轴里还留着 7/30 天按钮（会和顶栏两份）')
    rec('PASS' if not bad else 'FAIL', '时间轴列头常驻 + 说明行在顶栏',
        '; '.join(bad[:4]) if bad else '列头 sticky、外层不滚、说明行只在顶栏')


def check_custom_titlebar():
    """自绘标题栏（第 180 项）：三个必踩的坑各一条断言。

    ① **只能摘 WS_CAPTION**。一旦顺手把 WS_THICKFRAME / WS_MAXIMIZEBOX 一起清了，
       缩放边框、最大化、DWM 阴影全没；换成 WS_POPUP 则最大化会盖住任务栏
       —— 这正是第 115/121 项翻车的两条路。
    ② **/api/ping 必须报窗口实际状态**，不是配置值。用户改了开关没重启时，
       配置已是新值、窗口还是旧样式，前端照着配置画就会出现两条标题栏。
    ③ **按钮区必须 data-nodrag**，否则点按钮被当成拖标题栏，点击事件直接丢。
    """
    app = text(ROOT / 'app_window.py')
    br = text(ROOT / 'brain.py')
    tsx = text(ROOT / 'web/src/ui/titlebar.tsx')
    appx = text(ROOT / 'web/src/App.tsx')
    bad = []
    if 'style & ~_WS_CAPTION' not in app:
        bad.append('caption_off 没按「只清 WS_CAPTION」写')
    if '_WS_POPUP' in app or '0x80000000' in app:
        bad.append('出现 WS_POPUP（最大化会盖任务栏，第 115 项翻车路）')
    if 'caption_off(self.hwnd_main, True)' not in app:
        bad.append('启动时没调用 caption_off')
    # 只摘一次不够：WinForms 会在 resize / 布局后把 WS_CAPTION 写回来（实测）
    if 'after = get(h, _GWL_STYLE)' not in app:
        bad.append('caption_off 没读回真实状态（发过命令不等于摘掉了）')
    if 'recheck_caption' not in app:
        bad.append('缺延迟确认（布局稳定后样式会被写回）')
    # 顺序：fit 之后还要再 ensure 一次（fit 里的 resize 会把样式写回）
    if 'fit 后样式被写回' not in app:
        bad.append('_fit_main_size 之后没有再次确认标题栏')
    if 'TITLEBAR_ACTIVE = bool(self.custom_titlebar)' not in app:
        bad.append('没把实际状态告诉 brain（会退回读配置）')
    if 'if TITLEBAR_ACTIVE is not None' not in br:
        bad.append('titlebar_on 没优先读实际状态')
    if "'titlebar': titlebar_on()" not in br:
        bad.append('/api/ping 没带 titlebar')
    if 'data-nodrag' not in tsx:
        bad.append('标题栏按钮区缺 data-nodrag（点击会被当拖动）')
    # 拖动（第 182 项）：系统 SendMessage 那条路在 pywebview 里实测不动，
    # 必须是「前端按帧报坐标 + Python SetWindowPos」，且最大化要先还原。
    for fn in ('def titlebar_rect(', 'def titlebar_move(', 'def titlebar_restore(',
               'def titlebar_snap('):
        if fn not in app:
            bad.append('缺 %s' % fn.strip('def ('))
    for pat in ('onPointerDown={onDown}', 'titlebar_move?.(', 'titlebar_snap?.(',
                'titlebar_restore'):
        if pat not in tsx:
            bad.append('前端拖动链路缺 %s' % pat)
    if 'setPointerCapture' not in tsx:
        bad.append('拖动没做指针捕获（指针移出窗口会断）')
    if 'titlebar_drag' not in tsx or 'titlebar_cmd' not in tsx:
        bad.append('标题栏没接拖动/窗口命令桥')
    if 'customTitle && (' not in appx:
        bad.append('App 没按开关渲染标题栏')
    if appx.count('{!customTitle && (') < 2:
        bad.append('顶栏按钮没去重（会和标题栏重复两行）')
    # 第 183 项：漏定义 _SWP_* 会让 SetWindowPos 抛 NameError，被 except 吞成「拖不动」
    for c in ('_SWP_NOSIZE = ', '_SWP_NOZORDER = ', '_SWP_NOACTIVATE = ', '_SWP_SHOWWINDOW = '):
        if c not in app:
            bad.append('缺常量 %s' % c.split()[0])
    rec('PASS' if not bad else 'FAIL', '自绘标题栏（摘 CAPTION / 状态同源 / 按钮不吞点击）',
        '; '.join(bad[:4]) if bad else '只摘 CAPTION、ping 报实际状态、按钮 data-nodrag、顶栏不重复')

def check_window_bg():
    """窗口外框那圈 7px 必须是界面底色，不能是白（第 184 项）。

    用户的描述是「标题栏这边的颜色，我感觉变成了两层，一个是白色，一个是灰色」。
    真因：摘掉 WS_CAPTION 后窗口仍保留 WS_THICKFRAME，那圈缩放边框由 **Form 的
    BackColor** 填，而 pywebview 的默认值是 `#FFFFFF` —— 于是浅灰标题栏外面套一圈白。

    三条断言，缺一条都会退回白边：
    ① create_window 必须显式给 `background_color`；
    ② Python 里的 UI_BG_* 必须和 `web/src/index.css` 的 `--sb-bg` 完全一致
       （两边各写一份颜色，最容易悄悄漂移，所以这里逐值比对）；
    ③ 摘完标题栏要顺手刷 DWM 描边（DWMWA_BORDER_COLOR=34）。
    """
    app = text(ROOT / 'app_window.py')
    appx = text(ROOT / 'web/src/App.tsx')
    css = text(ROOT / 'web/src/index.css')
    bad = []
    if 'background_color=ui_bg_hex()' not in app:
        bad.append('主窗口没给 background_color（会退回 #FFFFFF 白边）')
    m = re.search(r'--sb-bg:\s*(\d+)\s+(\d+)\s+(\d+)', css)
    m2 = re.search(r"\[data-theme='dark'\]\s*\{([^}]*)\}", css, re.S)
    if not m:
        bad.append('index.css 里找不到 --sb-bg')
    else:
        want_l = tuple(int(x) for x in m.groups())
        got_l = re.search(r'UI_BG_LIGHT = \((\d+), (\d+), (\d+)\)', app)
        if not got_l or tuple(int(x) for x in got_l.groups()) != want_l:
            bad.append('UI_BG_LIGHT 与 --sb-bg 不一致')
    if not m2:
        bad.append('index.css 里找不到 dark 主题块')
    else:
        dm = re.search(r'--sb-bg:\s*(\d+)\s+(\d+)\s+(\d+)', m2.group(1))
        got_d = re.search(r'UI_BG_DARK = \((\d+), (\d+), (\d+)\)', app)
        if not dm or not got_d or tuple(int(x) for x in got_d.groups()) != tuple(int(x) for x in dm.groups()):
            bad.append('UI_BG_DARK 与 dark --sb-bg 不一致')
    # 三个来源的白各要一条：外框填充(background_color) / 1px 描边(34) / 顶部 caption 带(35)。
    # 顶部那条最阴：颜色**不受 Form.BackColor 影响**，只设 34 的话抹不掉（实测）
    if 'paint_frame' not in app:
        bad.append('缺 paint_frame（外框底色没人刷）')
    if '), 34, ctypes.byref(v), 4)' not in app:
        bad.append('没设 DWMWA_BORDER_COLOR(34)')
    if '), 35, ctypes.byref(v), 4)' not in app:
        bad.append('没设 DWMWA_CAPTION_COLOR(35)：顶部会留一条白带')
    if 'paint_frame(hwnd)' not in app and 'paint_frame(hw)' not in app:
        bad.append('paint_frame 定义了但没调用')
    # 主题切换靠前端把「页面实际用的深色」报给后端；桥是异步注入的，
    # 只在挂载时试一次会静默跳过 → 深色页套一圈浅色外框（实测踩过）
    if 'pywebviewready' not in appx:
        bad.append('set_dark 没等 pywebviewready（首屏桥还没到就静默跳过）')
    # 第 191 项起还要带上「这套配色的底色」：暖纸/夜航蓝既不是浅也不是深
    if 'paint_frame(_hw, bool(dark), _hex_rgb(bg))' not in app:
        bad.append('set_dark 没用前端报来的深色值/底色刷外框')
    rec('PASS' if not bad else 'FAIL', '窗口外框底色跟随主题（没有白边）',
        '; '.join(bad[:4]) if bad else 'background_color + 34/35 三条齐全，颜色与 CSS 逐值一致')


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

    最后一条是这个检查存在的全部理由：签发工具一旦进了 上传的干净版，
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
    # `private` 本身现在是清单的第四种前缀类型名（private pro/），不能当禁词；
    # 只拦真正指向私钥/签发工具的条目。自指误判在 138 项词表上已经踩过一次。
    for w in ('make_license', 'seed.txt', 'private_key', 'private.pem', '.pem'):
        if w in man:
            bad.append('public_files.txt 的条目里出现了「%s」' % w)

    rec('PASS' if not bad else 'FAIL', '授权模块（公钥合法 / 私钥未入库）',
        '; '.join(bad) or ('公钥 %s…' % (m.group(1)[:12] if m else '?')))


# 公开仓库 assets/ 里允许出现的素材，全部是自制：截图、演示 GIF、赞赏码、图标。
# 新增图片不在这个集合里就出 WARN —— 不是不让加，是提醒确认来源。
# 从网上下载的图标/插画是这一行代码唯一能拦住的那类侵权。
KNOWN_ASSETS = {
    'donate.png', 'icon.ico', 'icon_preview.png',
    'demo-contract.gif', 'demo-invoice.gif', 'demo-note.gif',
    'screenshot-main.png', 'screenshot-main-dark.png', 'screenshot-main-en.png',
    'screenshot-quick.png', 'screenshot-quick-empty.png', 'screenshot-quick-en.png',
    'screenshot-settings.png', 'screenshot-settings-en.png',
    # 落地页 docs/img/ 里用到的（logo 从仓库根复制，og.png 由 tmp/make_og.py 生成）
    'logo40.png', 'logo64.png', 'logo256.png', 'og.png',
}


def check_ip():
    """知识产权边界（第 138 项）：只学功能，不碰标识。

    功能与交互从来不受著作权保护 —— 《计算机软件保护条例》第六条写明
    「不延及开发软件所用的思想、处理过程、操作方法」；界面布局也有判例支持
    （北京知产法院九宫格案：3×3 摆放方式属通用表达、应保留于公有领域）。
    所以「参考 Listary 做快搜窗」本身没有法律问题，这也是这个项目能继续做的前提。

    真正会出事的是另外三件事，而且全是「以后顺手一干」的类型，靠记性记不住，
    所以写成门禁。词表刻意不含单独的「替代」二字：README 里「X 无法替代」这类
    正常对比不该被拦，只拦「平替 / 汉化版 / 破解 / 替代品」这种自认搭便车的说法。

      1. 宣传里自称「某软件的平替 / 汉化版 / 破解」—— 反不正当竞争法第 6 条的入口
      2. 打包商业字体 —— 中国字体侵权索赔比界面纠纷常见得多，一套字库动辄数万
      3. 来源不明的图片素材 —— 顺手抓个图标进 assets，是最容易犯的一次

    注意：本文件通过 `public_files.txt` 的 `sync preflight.py` 同步进 上传的干净版，
    所以扫描时必须跳过自己，否则下面的词表会把自己判成违规（CI 词表自指的经典坑，
    和 `public_files.txt` 注释自指是同一类）。以后加词只加正则，别在别的行里
    让竞品名和攀附词同现 —— 那样即使跳过了自己也会误伤。
    """
    bad, warn = [], []
    rivals = r'Listary|Everything|TextSeek|AnyTXT|Gety'
    hitch = r'平替|汉化版|汉化|破解|克隆|山寨|仿制|仿冒|替代品|免费替代|完美替代'
    pat = re.compile(r'(?:%s)[^\n]{0,12}(?:%s)|(?:%s)[^\n]{0,12}(?:%s)'
                     % (rivals, hitch, hitch, rivals), re.I)

    for p in REPO.rglob('*'):
        if not p.is_file() or p.suffix.lower() not in (
                '.md', '.py', '.ts', '.tsx', '.txt', '.html', '.htm', '.css'):
            continue
        if '.git' in p.parts or 'node_modules' in p.parts or 'dist' in p.parts:
            continue
        if p.name == 'preflight.py':
            continue
        for i, line in enumerate(text(p).splitlines(), 1):
            m = pat.search(line)
            if m:
                bad.append('%s:%d「%s」' % (p.relative_to(REPO), i, m.group(0)))

    # 字体只认系统字体栈（随 OS 授权，不随安装包分发）。一旦出现 @font-face
    # 或字体文件，就等于在分发别人的字库，得先有授权。
    for p in (ROOT / 'web/src').rglob('*.css'):
        if '@font-face' in text(p):
            bad.append('%s 用了 @font-face（先确认字体授权）' % p.relative_to(ROOT))
    for base in (ROOT, ROOT / 'assets', REPO, REPO / 'assets'):
        if not base.is_dir():
            continue
        for p in base.glob('*'):
            if p.suffix.lower() in ('.ttf', '.otf', '.woff', '.woff2'):
                bad.append('%s 打包了字体文件' % p.relative_to(ROOT))

    # 素材白名单：assets/（README 用）和 docs/img/（落地页用）都要过一遍。
    # 落地页的图是从 assets 复制过去的，将来单独加图也得走同一个白名单。
    for adir in (REPO / 'assets', REPO / 'docs' / 'img'):
        if not adir.is_dir():
            continue
        for p in adir.glob('*'):
            if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico'):
                if p.name not in KNOWN_ASSETS:
                    warn.append('新增素材 %s（确认自制或已获授权）'
                                % p.relative_to(REPO).as_posix())

    rec('FAIL' if bad else ('WARN' if warn else 'PASS'),
        '知识产权边界（不攀附 / 不打包他人字体）',
        '; '.join(bad + warn) or '无攀附措辞、仅系统字体、素材全自制')


def check_terms():
    """许可证表述一致（第 139 项）：换了协议，整套文案要跟着换。

    MIT 不可撤销，但那只对**已发布的版本**有效。LICENSE 换成 PolyForm
    Noncommercial 之后，「开源」这个词在 README 和界面里就变成虚假陈述 ——
    开源有明确定义（OSI），不符合还写，被人指出一次就够难看了。

    这条盯的是**将来手滑写回去**，尤其是界面里那句面向用户的话
    （`about_free`，五语言）。第 138 项管「别用别人的东西」，这条管
    「别把自己的协议说错」，两件事分开才查得准。
    """
    bad = []
    lic = text(REPO / 'LICENSE')
    if 'PolyForm Noncommercial License 1.0.0' not in lic:
        bad.append('LICENSE 不是 PolyForm Noncommercial 1.0.0')
    if 'Required Notice: Copyright' not in lic:
        bad.append('LICENSE 缺 Required Notice 行（协议要求携带）')

    for f, tag in (('README.md', '## 许可证'), ('README.en.md', '## License')):
        body = text(REPO / f)
        if tag not in body:
            bad.append('%s 缺「%s」段' % (f, tag))
            continue
        head = body.split(tag, 1)[1][:120]
        if 'PolyForm' not in head:
            bad.append('%s 的许可证段没指向 PolyForm' % f)

    gv = text(REPO / 'gen_version.py')
    if 'MIT License' in gv:
        bad.append('gen_version.py 的 LegalCopyright 仍写 MIT License（exe 文件属性会对外显示）')
    if 'PolyForm' not in gv:
        bad.append('gen_version.py 的 LegalCopyright 没指向 PolyForm')

    s = text(ROOT / 'web/src/lib/i18n.ts')
    for m in re.finditer(r"^\s*about_free:\s*'([^']*)'", s, re.M):
        if re.search(r'开源|open source|código abierto|código aberto|ओपन सोर्स', m.group(1), re.I):
            bad.append('about_free 仍自称开源：%s' % m.group(1)[:20])
    if len(re.findall(r'^\s*about_free:', s, re.M)) != 5:
        bad.append('about_free 不是五语言齐全')

    rec('PASS' if not bad else 'FAIL', '许可证表述一致（PolyForm / 不自称开源）',
        '; '.join(bad) or 'LICENSE、两份 README、五语言界面均已同步')


def check_landing():
    """落地页完整性（第 141 项）：页面能打开 ≠ 页面是对的。

    线上最不容易发现的一类错误是**图片路径写错**：HTML 照常渲染，
    只是图的位置是空的，而且本地用 file:// 打开往往还是好的（相对路径不同），
    只有真到 Pages 上才 404。发版前把每个 src 核一遍，比上线后盯着看强。

    另外两条守住的是「以后顺手一干」：
      - 引外部资源（CDN 字体 / 图标库）：断网或域名被墙时整页变白板，
        而且很可能把别人的素材打包进了自己的宣传页 —— 和第 138 项同一个坑
      - 下载链接写死版本号：下次发版忘了改，用户点到的是旧安装包
    """
    bad, warn = [], []
    doc = REPO / 'docs' / 'index.html'
    if not doc.is_file():
        rec('FAIL', '落地页（docs/index.html）', '文件不存在')
        return
    s = text(doc)

    for m in re.finditer(r'\ssrc="((?:https?:)?//[^"]+)"', s):
        bad.append('外部资源 %s' % m.group(1))
    if '@font-face' in s:
        bad.append('用了 @font-face')

    # 本地图片要真的在（这是本条检查的主要目的）
    for m in re.finditer(r'(?:src|content)="(img/[^"]+)"', s):
        rel = m.group(1)
        if not (doc.parent / rel).is_file():
            bad.append('缺文件 %s' % rel)

    # 下载入口指向 latest，别写死 vX.Y.Z
    if 'releases/latest' not in s:
        warn.append('下载链接没有指向 releases/latest')

    if not re.search(r'<title>[^<]{4,}</title>', s):
        bad.append('缺 <title>')
    if 'og:image' not in s:
        warn.append('缺 og:image（转发到群里没有预览图）')

    rec('FAIL' if bad else ('WARN' if warn else 'PASS'),
        '落地页（资源齐全 / 无外部依赖）',
        '; '.join(bad + warn) or '图片齐全、无外部资源、下载指向 latest')


def check_pro_features():
    """Pro 的每个入口都必须同时满足：私有实现存在 + 授权门控 + 降级安全。

    分离后最容易出的事故不是「忘了写门控」，而是重构时把
    `pro_api is None or not license_feature(...)` 简化成只看其中一个：
    公开源码版有 pro_api=None 却没门控、或打包版有门控却没私有模块，
    都会变成「谁都能用」或「装了 exe 却没 Pro」。所以逐个分支查两个条件。
    """
    bad = []
    s = text(ROOT / 'brain.py')
    if 'from pro import pro_api' not in s or "if e.name != 'pro':" not in s:
        bad.append('brain.py 薄挂载点被改（只应放行「整个 pro 包不存在」）')
    if '_B = sys.modules[__name__]' not in s:
        bad.append('能力上下文 _B 没了，私有模块拿不到 brain 的能力')
    for route in ('/api/export', '/api/ocr_batch'):
        blocks = s.split("elif self.path.startswith('%s'):" % route)[1:]
        if not blocks:
            bad.append('%s 分支找不到' % route)
            continue
        for i, b in enumerate(blocks):
            # 只看这个分支自身，别把下一个 elif 的门控算进来
            seg = b.split('\n            elif ', 1)[0]
            if route == '/api/ocr_batch':
                # 交接第 0 节 0.8：批量 OCR 转免费，只保留「无私有包则降级」，
                # 绝不再按授权码拦人；这两条断言也防止有人把收费门控加回去。
                if 'if pro_api is None:' not in seg:
                    bad.append('%s 第 %d 个分支缺「私有模块降级」' % (route, i + 1))
                if 'license_feature(' in seg:
                    bad.append('%s 第 %d 个分支不该再查授权（已免费）' % (route, i + 1))
            elif 'pro_api is None or not license_feature(' not in seg:
                bad.append('%s 第 %d 个分支缺「私有模块 + 授权」双门控' % (route, i + 1))
    for call in ('pro_api.export(', 'pro_api.ocr_batch_get(', 'pro_api.ocr_batch_post('):
        if s.count(call) != 1:
            bad.append('%s 调用次数不是 1（%d）' % (call, s.count(call)))

    # 私有模块仍然是纯状态机 / 不反向 import brain
    mod = text(ROOT / 'pro' / 'ocr_batch.py')
    heavy = re.findall(r'^\s*(?:from|import)\s+'
                       r'(brain|rapidocr_onnxruntime|pymupdf|sqlite3)\b', mod, re.M)
    if not mod or heavy:
        bad.append('pro/ocr_batch.py 不再是纯状态机（别把 DB / OCR 引擎塞进去）')
    api = text(ROOT / 'pro' / 'pro_api.py')
    if not api or re.findall(r'^\s*(?:from\s+brain|import\s+brain)\b', api, re.M):
        bad.append('pro/pro_api.py 不该 import brain（能力应由 B 注入）')

    man = '\n'.join(l for l in text(REPO / 'tools/public_files.txt').splitlines()
                    if l.strip() and not l.lstrip().startswith('#'))
    if 'private pro/' not in man:
        bad.append('public_files.txt 缺 private pro/')
    for f in ('ocr_batch.py', 'xlsx_writer.py',
              'tests/test_ocr_batch.py', 'tests/test_xlsx.py'):
        if ('sync  %s' % f) in man:
            bad.append('public_files.txt 仍把 %s 当公开源码同步' % f)
    rec('PASS' if not bad else 'FAIL', 'Pro 门控（双条件 / 私有实现）',
        '; '.join(bad) or 'export 双门控 / ocr_batch 免费且降级安全，适配层不 import brain')


def check_private_modules():
    """私有模块绝不进公开仓库 —— 「核心免费 + Pro 私有」结构的唯一硬边界。

    只查一个地方不够：文件可能这次没被跟踪，却因为 .gitignore 漏写而在下次
    `git add -A` 时混进去；spec 也可能忘了 hiddenimports，导致 exe 里没 Pro。
    """
    bad = []
    r = subprocess.run(['git', '-c', 'core.quotepath=false', 'ls-files'],
                       cwd=str(REPO), capture_output=True, text=True, encoding='utf-8')
    tracked = [x.strip() for x in (r.stdout or '').splitlines() if x.strip()]
    leaked = [f for f in tracked
              if f.startswith('pro/') or f in
              ('pro_api.py', 'ocr_batch.py', 'xlsx_writer.py')]
    if leaked:
        bad.append('公开仓库仍跟踪私有模块：%s' % ', '.join(leaked[:5]))
    if 'pro/' not in text(REPO / '.gitignore'):
        bad.append('.gitignore 没排除 pro/')
    for f in ('second-brain-app.spec', 'second-brain-core.spec'):
        if 'pro.pro_api' not in text(REPO / f):
            bad.append('%s 的 hiddenimports 缺 pro.pro_api' % f)
    for name in ('__init__.py', 'pro_api.py', 'ocr_batch.py', 'xlsx_writer.py'):
        if not (ROOT / 'pro' / name).is_file():
            bad.append('开发根 pro/ 缺 %s' % name)
    if "'private'" not in text(REPO / 'tools/sync_public.py'):
        bad.append('sync_public.py 不支持 private 段')
    if '私有模块泄漏' not in text(REPO / 'tools/check_public.py'):
        bad.append('check_public.py 没断言私有模块泄漏')
    rec('PASS' if not bad else 'FAIL', '私有模块边界（不进公开仓库）',
        '; '.join(bad) or 'pro/ 未被 git 跟踪、.gitignore / spec / 两个脚本都已就位')


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
    """开发根 ↔ 公开仓库的一致性。

    2026-11-13 补的漏洞：原来是**硬编码 7 个文件**，`web/src/ui/*.tsx` 一个都不在名单里，
    于是 `icons.tsx` 改完没同步、preflight 照样报「已一致」，打包就会用旧界面
    （第 188 项改图标时实测踩到）。现在把整个 `web/src` 递归比一遍。
    """
    files = ('brain.py', 'app_window.py', 'web/src/App.tsx', 'web/src/ui/context.tsx',
             'web/src/ui/onboarding.tsx', 'web/src/lib/i18n.ts', '使用说明.md')
    diff = []
    for f in files:
        a, b = ROOT / f, REPO / f
        if not b.is_file():
            diff.append(f + ' (仓库缺失)')
        elif a.is_file() and hashlib.sha256(a.read_bytes()).digest() != hashlib.sha256(b.read_bytes()).digest():
            diff.append(f)
    # 前端源码整目录比：sync_public.py 是按目录镜像的，硬编码名单永远会漏
    src = ROOT / 'web/src'
    if src.is_dir():
        for p in sorted(src.rglob('*')):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if rel in files:
                continue
            q = REPO / rel
            if not q.is_file():
                diff.append(rel + ' (仓库缺失)')
            elif hashlib.sha256(p.read_bytes()).digest() != hashlib.sha256(q.read_bytes()).digest():
                diff.append(rel)
    # 私有 pro/ 镜像：spec 的 pathex=[] 让 PyInstaller 只能从 REPO/pro/ 取源码，
    # 漏跑 sync_public.py 会把上一次镜像的旧实现打进 exe（安全审查 U8-F1）。
    # 这个必须硬失败；开发根压根没有 pro/ 时是纯免费源码版，跳过即可。
    pro_bad = []
    # 公开仓库也有一份 preflight.py 副本；在那里 REPO 指向的子目录并不存在，
    # 跳过即可，别让「在仓库目录里误跑」变成一条假的 FAIL。
    if (ROOT / 'pro').is_dir() and REPO.is_dir():
        for name in ('__init__.py', 'pro_api.py', 'ocr_batch.py', 'xlsx_writer.py'):
            a, b = ROOT / 'pro' / name, REPO / 'pro' / name
            if not b.is_file():
                pro_bad.append('pro/%s 镜像缺失' % name)
            elif hashlib.sha256(a.read_bytes()).digest() != hashlib.sha256(b.read_bytes()).digest():
                pro_bad.append('pro/%s 与镜像不一致' % name)

    if pro_bad:
        rec('FAIL', '开发根 ↔ 公开仓库源码',
            'pro/ 镜像不一致：%s（先跑 sync_public.py，否则旧实现会进 exe）'
            % '、'.join(pro_bad))
    else:
        rec('WARN' if diff else 'PASS', '开发根 ↔ 公开仓库源码',
            ('%d 个待同步，打包前跑 sync_public.py' % len(diff)) if diff
            else '已一致（含 pro/ 镜像 4 文件）')


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
                      and m and int(m.group(1)) >= 329)
                want = '>= 329 项'
            else:
                mf = re.search(r'Test Files\s+(\d+) passed', out)
                mt = re.search(r'Tests\s+(\d+) passed', out)
                ok = (r.returncode == 0 and mf and mt and
                      int(mf.group(1)) >= 7 and int(mt.group(1)) >= 97)
                want = '>= 7 文件 / >= 97 用例'
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
    # 2026-11-12：最小化窗口 IsWindowVisible() 仍是 True，Form.Show() 不还原它
    # （窗口被挪到 -32000,-32000）。当时点图标日志「成功、visible_now=True、
    # fg 是自己」全绿，屏幕却毫无变化。show_main 必须先 IsIconic + SW_RESTORE。
    sm = s[i2:j2 if j2 > i2 else None]
    if 'IsIconic(hwnd)' not in sm or 'SW_RESTORE' not in sm:
        bad.append('show_main 没处理最小化窗口（点图标会没反应）')
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
    # 第 163 项：退出不能串行等关库（实测用户要等 1~2 秒）
    q = s.find('    def quit(self):')
    qe = s.find('\n    def ', q + 10)
    qbody = s[q:qe] if q >= 0 else ''
    if 'quit timing' not in qbody:
        bad.append('退出流程没有分段计时')
    if 'done.wait(0.3)' not in qbody:
        bad.append('关库没有超时上限（退出会等 WAL checkpoint）')
    if 'quit-close-db' not in qbody:
        bad.append('关库没有放到后台线程')
    # 第 164 项：界面打不开时的兜底入口与自动更新
    if 'def _feedback_cli(' not in s or '--feedback' not in s:
        bad.append('缺 --feedback 无界面反馈入口')
    if 'def _start_auto_update(' not in s or 'check_update()' not in s:
        bad.append('缺后台自动检查更新')
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


def check_icon():
    """图标门禁：多尺寸齐全 + 按 DPI 选档，别再把 16x16 放大成任务栏大图标。

    2026-11-12 实测 LoadImageW(..., 0, 0) 对多尺寸 ico 拿的是第一张 16x16，
    而代码注释一直写着「让系统按 DPI 自己挑 256」——想当然的假设没验证过。
    """
    import struct
    bad = []
    n = 0
    f = ROOT / 'assets' / 'icon.ico'
    if not f.is_file():
        bad.append('icon.ico 不存在')
    else:
        try:
            b = f.read_bytes()
            n = struct.unpack_from('<H', b, 4)[0]
            dims = {b[6 + i * 16] or 256 for i in range(n)}
            miss = sorted({16, 20, 24, 32, 40, 48, 64, 128, 256} - dims)
            if miss:
                bad.append('缺尺寸档 %s' % miss)
        except Exception as e:
            bad.append('ico 解析失败 %r' % (e,))
    win = text(ROOT / 'app_window.py')
    tray = text(ROOT / 'tray.py')
    if 'GetDpiForWindow' not in win or 'small_sz' not in win:
        bad.append('apply_window_icon 没按 DPI 选小图标档')
    if re.search(r'LoadImageW\(0,\s*ico,\s*IMAGE_ICON,\s*0,\s*0', win):
        bad.append('仍在用 LoadImage(0,0)，实测只会拿到 16x16')
    if 'LR_DEFAULTSIZE' in tray:
        bad.append('托盘图标仍用默认尺寸标志')
    if 'GetDpiForSystem' not in tray:
        bad.append('托盘图标没按系统 DPI 选档')
    rec('PASS' if not bad else 'FAIL', '图标多尺寸 / DPI 选档',
        '; '.join(bad) if bad else '%d 档齐全，按 DPI 选档' % n)


def check_err_ingest():
    """端点外置（第 158 项）：用户端看不到地址，客户端也不能指定端点。

    接收地址是开发者配置，摆到用户端既难看又危险（带 token 的地址一旦内置
    进 exe 就会被反编译 / 抓包）。这条门禁挡的是「地址又被加回界面 / API」。
    """
    bad = []
    er = text(ROOT / 'error_report.py')
    br = text(ROOT / 'brain.py')
    panel = text(ROOT / 'web/src/ui/panels.tsx')
    api = text(ROOT / 'web/src/api.ts')
    i18 = text(ROOT / 'web/src/lib/i18n.ts')
    bk = text(ROOT / 'settings_backup.py')
    lc = text(ROOT / 'license_core.py')

    if "INGEST_NAME = 'error_ingest.json'" not in er:
        bad.append('没定义作者侧配置文件名')
    for fn in ('def ingest_config(', 'def endpoint('):
        if fn not in er:
            bad.append('缺 %s' % fn.strip())
    defaults = er.split('DEFAULTS = {')[1].split('}')[0] if 'DEFAULTS = {' in er else ''
    if "'url':" in defaults:
        bad.append('用户配置 DEFAULTS 里仍存端点')
    if "'url': mask_url(" in er or "'url_set':" in er:
        bad.append('status() 又把地址返回给前端了')
    if 'def send_one(rid):' not in er:
        bad.append('send_one 仍允许外部传 url')

    # 只检查 /api/errlog POST 的 op=set 段，别误伤别处的同名分支
    k = br.rfind('/api/errlog')
    i = br.find("if op == 'set':", k)
    j = br.find("elif op == 'test':", i + 1)
    seg = br[i:j] if i > 0 and j > i else ''
    if '客户端不可指定' not in seg:
        bad.append('op=set 没拒绝客户端指定的地址')
    if "'url': u," in seg:
        bad.append('op=set 仍在写端点')

    if 'err_url' in panel or 'setUrl' in panel or 'url.trim()' in panel:
        bad.append('设置面板仍有接收地址输入框')
    if '!ingest' not in panel or 'err_no_ingest' not in panel:
        bad.append('面板没按端点就绪状态禁用发送')
    if 'url_set' in api or 'ingest: boolean' not in api:
        bad.append('前端类型没换成 ingest 布尔值')
    if 'err_url' in i18 or i18.count('err_no_ingest') != 5:
        bad.append('五语言文案没同步（或残留接收地址）')

    if 'error_ingest.json' not in bk:
        bad.append('设置备份没显式排除端点配置')
    allow = bk.split('ALLOW = (')[1].split(')')[0] if 'ALLOW = (' in bk else ''
    if 'error_ingest.json' in allow:
        bad.append('端点配置进了备份白名单')

    if (REPO / 'error_ingest.json').exists():
        bad.append('真实端点配置混进了公开仓库')
    manifest = text(REPO / 'tools/public_files.txt')
    if re.search(r'^\s*(sync|local|build)\s+error_ingest\.json\s*$', manifest, re.M):
        bad.append('端点配置被写进公开仓库清单')

    # 第 161 项：界面不对但没抛异常时也要能主动反馈，否则 0 份报告时
    # 「立即发送 / 导出到本地」永远空转，用户想反馈都没有载体。
    if 'def save_feedback(' not in er:
        bad.append('缺手动反馈 save_feedback()')
    post = br[br.rfind('/api/errlog'):]
    if "elif op == 'feedback':" not in post:
        bad.append('/api/errlog 缺 op=feedback')
    if "t('err_fb_open')" not in panel or "t('err_no_report')" not in panel:
        bad.append('界面缺手动反馈入口或空态引导')

    # 第 162 项：auto / include_paths 恒开，界面不再有这两个开关。
    defaults = er.split('DEFAULTS = {')[1].split('}')[0] if 'DEFAULTS = {' in er else ''
    if "'auto': True" not in defaults or "'include_paths': True" not in defaults:
        bad.append('默认值不是 auto/include_paths 恒开')
    if "err_auto" in panel or "err_paths" in panel:
        bad.append('界面仍残留已取消的开关')
    if "auto, include_paths: paths" in panel:
        bad.append('前端还在提交已取消的开关')
    if "t('care_save')" in panel or "t('err_preview')" in panel:
        bad.append('面板仍残留已删除的按钮（会显示原始键名）')
    if "onWhyNotFound" not in panel:
        bad.append('使用帮助缺少「为什么搜不到」入口')
    # 第 168 项：授权必须并进「版本」行（胶囊），不能再单独占一整块
    if 'pushedRight' not in panel:
        bad.append('授权没有并到版本行（第 168 项）')
    if '<LicenseBox open={open} />' in panel:
        bad.append('授权仍是独立大块，没有挪进版本行')
    if 'const expand = async ()' not in text(ROOT / 'web/src/ui/panels.test.tsx'):
        bad.append('授权用例没有适配「默认收起」')
    if "def save_feedback(" not in er:
        bad.append('缺手动反馈 save_feedback()')
    if "if conf['auto'] and endpoint():" not in er:
        bad.append('自动发送缺少端点双条件（无端点必须零网络行为）')
    # 第 167 项：诊断字段必须真采到，且 DPI 不能取到缩放后的逻辑值
    for f in ("'cpu_name'", "'arch'", "'screen'", "'dpr'", "'webview2'",
              "'install'", "'uptime_s'", "'db_mb'", "'locale'"):
        if f not in er:
            bad.append('诊断字段缺 %s' % f)
    if 'SetThreadDpiAwarenessContext' not in er:
        bad.append('未临时切 DPI 上下文（分辨率会报成逻辑值）')
    # 第 170 项：授权收紧三件套（HWID 绑定 / 在线核销 / 台账）
    if 'def hwid(' not in lc or "startswith('hw:')" not in lc:
        bad.append('授权缺机器指纹绑定')
    if br.count('hwid=license_core.hwid()') < 2:
        bad.append('激活或读状态漏了 hwid 校验（只查一处等于没查）')
    if 'def _license_online(' not in br:
        bad.append('缺在线核销')
    wk = text(ROOT / 'tools/errlog_worker.js')
    if "p === '/activate'" not in wk or "p === '/revoke'" not in wk:
        bad.append('Worker 缺 /activate 或 /revoke')
    if not (ROOT / 'tools/license_ledger.py').is_file():
        bad.append('缺授权台账工具')
    if 'lic_err_hw' not in panel or 'lic_err_revoked' not in panel:
        bad.append('界面缺绑定/吊销的错误提示')
    # 第 171 项：主界面别重复语法表 / 托盘单击开快搜 / 拖拽要能拿路径
    app = text(ROOT / 'web/src/App.tsx')
    if 'QS().map' in app:
        bad.append('主界面又出现了语法速览（帮助里有）')
    if "args=(IDI_MINI,)" not in text(ROOT / 'tray.py'):
        bad.append('托盘单击没有打开简要搜索')
    if 'text/uri-list' not in app or 'pick_file' not in app:
        bad.append('拖拽没走多路取路径/原生兜底')
    if 'onReplayTour' not in panel:
        bad.append('使用帮助缺「重看教程」入口')
    # 第 172 项：环境体检面板（后端 /api/env 早有数据，前端必须接上）
    if 'HealthPanel' not in panel or 'hc_title' not in panel:
        bad.append('缺环境体检面板')
    if '/api/env' not in text(ROOT / 'web/src/api.ts'):
        bad.append('前端没有调用 /api/env')
    if 'hc_need' not in i18 or 'hc_fix' not in i18:
        bad.append('体检面板缺文案键')
    # 第 173 项：侧栏不再重复统计 / 帮助按钮蓝底 / 键位别写成箭头
    if "{n(stats.files)}</b>" in app or "mt-auto pt-3 text-[10px]" in app:
        bad.append('侧栏底部又出现文件/正文统计（右下角已有）')
    if "['→'" in panel:
        bad.append('帮助里的键位还是箭头，容易被当成方向键')
    if "['content:'" in panel:
        bad.append('帮助里仍有 content: 条目（正文按钮已覆盖）')
    if panel.count('bg-accent px-4 py-2') < 2:
        bad.append('帮助里的两个按钮不是蓝底放大')
    # 第 174 项：后端文案必须跟界面语言走，不许英文界面里冒中文
    ev = text(ROOT / 'env_check.py')
    if 'def judge(info, lang=' not in ev or '_EN = {' not in ev:
        bad.append('体检判定没做多语言')
    if 'judge(_inf, _lang)' not in br or "_qs.get('lang')" not in br:
        bad.append('/api/env 没把 lang 传下去')
    if 'whyText' not in panel or 'lib_default' not in panel:
        bad.append('诊断原因/默认库名没有按语言映射')
    for k in ('why_notext', 'why_enc', 'lib_default'):
        if k not in i18:
            bad.append('缺文案键 %s' % k)
    # 第 175 项：帮助键位要含 Enter / 按钮要有间距 / 托盘反馈要直达错误日志
    if "['Enter'" not in panel:
        bad.append('帮助里没有 Enter 打开文件的说明')
    if 'justify-center gap-2' not in panel:
        bad.append('帮助底部两个按钮没有间距（会黏在一起）')
    if 'focus=feedback' not in app and "'feedback'" not in app:
        bad.append('托盘反馈没有定位到错误日志区')
    if 'ErrorLogPanel' not in panel or 'id="feedback"' not in panel:
        bad.append('错误日志没抽成组件 / 锚点丢了')
    if "qs.append('focus=' + focus)" not in text(ROOT / 'app_window.py'):
        bad.append('open_panel 没把 focus 传给前端')
    if 'text/uri-list' not in app or "startsWith('#')" not in app:
        bad.append('拖拽没逐行解析 uri-list（首行常是注释）')

    rec('PASS' if not bad else 'FAIL', '错误日志端点只在作者侧',
        '; '.join(bad) if bad else '界面无地址 / API 拒写 / 备份排除 / 仓库无配置')


def check_no_ingest_host_in_public():
    """第 160 项：上报端点主机名不得出现在公开仓库 / 前端源码与产物里。

    端点只从作者侧 error_ingest.json 热读，客户端与公开代码不该有任何
    接收主机名或带令牌的路径片段；否则等于把地址（以及账户子域里可能
    携带的个人信息）随源码、exe、前端产物一起分发出去。
    """
    bad = []
    # 拼接避免本文件自己命中（门禁扫公开仓库时会读到自己那份副本）
    needles = ['workers' + '.dev', '/report?to' + 'ken=']
    exts = {'.py', '.ts', '.tsx', '.js', '.jsx', '.json', '.md',
            '.html', '.htm', '.txt', '.spec', '.bat', '.css',
            '.toml', '.yml', '.yaml'}
    bases = [REPO, ROOT / 'web' / 'src', ROOT / 'web' / 'dist']
    for base in bases:
        if not base.is_dir():
            continue
        for f in base.rglob('*'):
            if not f.is_file() or '.git' in f.parts or f.suffix.lower() not in exts:
                continue
            try:
                t = f.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            for n in needles:
                if n in t:
                    bad.append('%s/%s 含 %s' % (base.name, f.name, n))
                    break
    rec('PASS' if not bad else 'FAIL', '公开代码里没有上报端点主机名',
        '; '.join(bad[:4]) if bad else '公开仓库 + 前端源码/产物 0 处端点主机名')


def main():
    print('== 打包前自检 ==')
    check_version()
    check_icon()
    check_err_ingest()
    check_no_ingest_host_in_public()
    check_i18n()
    check_env_i18n()
    check_header_actions()
    check_support_entry()
    check_settings_slim()
    check_side_actions()
    check_appearance()
    check_overlay_top()
    check_timeline_head()
    check_custom_titlebar()
    check_window_bg()
    check_donate()
    check_license()
    check_ip()
    check_terms()
    check_landing()
    check_pro_features()
    check_private_modules()
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
