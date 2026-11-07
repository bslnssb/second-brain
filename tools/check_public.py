# -*- coding: utf-8 -*-
"""公开仓库体检：该有的有没有、不该有的有没有、版本号对不对、有没有本机痕迹。

只读脚本，本地和 CI 都能跑（不依赖开发根）：
    python tools/check_public.py                    # 查工作区 + 已跟踪文件
    python tools/check_public.py --history          # 额外扫最近一次提交的新增行
    python tools/check_public.py --history A..B     # 扫指定范围

它查六件事：
    1. 跟踪文件必须**恰好**被 tools/public_files.txt 覆盖（多一个少一个都报）
    2. 黑名单文件名（索引库、扫描清单、私人笔记、日志、运行期配置）
    3. 文本文件里的本机痕迹（词表见 tools/private_words.txt）
    4. 版本号三处是否一致：brain.APP_VERSION / setup_app.VERSION / version.json
    5. 体积：单文件 ≤ 3 MB（截图最大 0.6 MB），防止有人把 exe 或 db 塞进来
    6. 重复内容（同 sha 的两份文件）—— 以前的 icon.ico 就在根层和 assets/ 各存了一份

为什么要有这个脚本：v0.9.44 把作者的私人笔记当成用户手册提交了，还打进了安装包。
靠记性不可靠，靠一条命令可靠。

退出码：0 全部通过；1 有问题。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MANIFEST = HERE / 'public_files.txt'
WORDS = HERE / 'private_words.txt'

# 扫描时跳过的文件：规则文件自己当然含有这些词，扫自己没有意义
SELF_SKIP = {
    'tools/check_public.py',
    'tools/private_words.txt',
    'tools/sync_public.py',
    '.github/workflows/privacy.yml',
}

# 索引库 / 扫描清单 / 私人笔记 / 日志 / 运行期配置：这些出现在仓库里就是事故
BAD_NAMES = [
    '*.db', '*.db-wal', '*.db-shm', '*.sqlite', '*.sqlite3',
    'scan_raw.csv', 'scope.json', 'settings.json', 'apps.json',
    '交接文档.md', 'HANDOFF.md', '代码审查*.md', '界面审查*.md',
    '架构评审*.md', '前端架构审查*.md',
    'debug.log', '*.log', 'eval_report.txt', 'QA_REPORT.md',
    'payload.zip', 'version_info.txt', 'nul', '*.bak', '*.bak-*',
    'setup_app_v*_backup.py', '发公告.bat', '发布公告.py', '诊断.bat',
]

# 通用的「不像示例的本机路径」兜底：C:\Users\<某个名字>\ 。
# 排除 Public / Default / All Users 这类示例路径，否则文档里的合法写法会被当成泄露。
BAD_CONTENT = [
    (re.compile(r'[A-Za-z]:\\Users\\(?!Public\\|Default\\|All Users\\)[A-Za-z0-9_.\-]{2,}\\'),
     '疑似本机用户目录'),
    (re.compile(r'\.workbuddy[\\/]memory'), '本机 AI 记忆目录'),
]

# 内容确实一样但属于「故意两份」的，报出来只会让人每次去核对一遍
KNOWN_DUP = {
    ('app.ico', 'assets/icon.ico'),        # 安装器窗口图标 = 程序图标，同一张图

    # 落地页 docs/ 必须自包含：GitHub Pages 只发布 /docs 目录，
    # 页面里写 ../assets/x.png 在线上会变成站点根的 /assets/x.png → 404。
    # 所以这些图是「有意复制的副本」，不是忘了删。
    ('assets/demo-contract.gif', 'docs/img/demo-contract.gif'),
    ('assets/demo-invoice.gif', 'docs/img/demo-invoice.gif'),
    ('assets/screenshot-quick.png', 'docs/img/screenshot-quick.png'),
    ('logo64.png', 'docs/img/logo64.png'),
}

MAX_FILE = 3 * 1024 * 1024          # 单文件 3 MB
REQUIRED = [                        # 少了任何一个都算发不出去
    'README.md', 'README.en.md', 'LICENSE', '.gitignore',
    'brain.py', 'setup_app.py', 'selftest.py',
    'web/src/App.tsx', 'web/package.json',
    'assets/icon.ico', 'assets/donate.png',
    '使用说明.md', 'version.json',
]


def run_git(*args):
    r = subprocess.run(['git', '-c', 'core.quotepath=false', *args],
                       cwd=str(REPO), capture_output=True, text=True, encoding='utf-8')
    return r.stdout if r.returncode == 0 else ''


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_words():
    if not WORDS.is_file():
        return []
    return [w.strip() for w in WORDS.read_text(encoding='utf-8').splitlines()
            if w.strip() and not w.strip().startswith('#')]


def parse_manifest():
    out = {'sync': [], 'local': [], 'build': []}
    for i, line in enumerate(MANIFEST.read_text(encoding='utf-8').splitlines(), 1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        parts = s.split(None, 1)
        if len(parts) != 2 or parts[0] not in out:
            raise SystemExit('[!] 清单第 %d 行格式不对：%r' % (i, line))
        out[parts[0]].append(parts[1].strip())
    return out


def covered(path: str, patterns) -> bool:
    for pat in patterns:
        if pat.endswith('/'):
            if path.startswith(pat):
                return True
        elif path == pat:
            return True
    return False


def scan_history(rng, words):
    """扫提交历史里**新增的行**。老内容早就人工过了一遍，这里只防「这次带进来的」。

    `--format=%H` 是必须的：`git log -p` 默认会把 commit message 一起打出来，
    而 commit message 里出现路径是正常的（说明问题时总要举个例子），
    不加这个会把「我在说明里提了一句 D:\\1\\」也判成泄露 —— 第一次跑 CI 就是这么红的。
    """
    out = run_git('log', '-p', '--unified=0', '--no-color', '--format=%H', rng)
    hits = []
    cur = ''
    for line in out.splitlines():
        if line.startswith('+++ b/'):
            cur = line[6:].strip()
            continue
        if line.startswith('+++') or not line.startswith('+'):
            continue
        if cur in SELF_SKIP:           # 规则文件本身当然含有这些词
            continue
        for w in words:
            if w in line:
                hits.append('%s ← %s' % (w, line[1:120].strip()))
    return hits


def main():
    args = sys.argv[1:]
    problems, warnings = [], []
    mf = parse_manifest()
    words = load_words()
    all_pat = mf['sync'] + mf['local'] + mf['build']
    files = [p for p in run_git('ls-files').splitlines() if p.strip()]

    if not files:
        print('[!] 读不到 git 跟踪文件（不在仓库里跑？）')
        return 1

    # 1) 清单覆盖 + 必需文件
    for f in files:
        if not covered(f, all_pat):
            problems.append('清单里没有：%s —— 公开仓库的内容必须恰好等于 tools/public_files.txt' % f)
    for pat in mf['local']:
        if pat.endswith('/'):
            if not any(f.startswith(pat) for f in files):
                warnings.append('清单声明 local 但没有文件：%s' % pat)
        elif pat not in files:
            warnings.append('清单声明 local 但文件不存在：%s' % pat)
    for need in REQUIRED:
        if need not in files:
            problems.append('缺必需文件：%s' % need)

    # 2) 黑名单文件名
    for f in files:
        name = f.rsplit('/', 1)[-1]
        if f in SELF_SKIP:
            continue
        for pat in BAD_NAMES:
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(f, pat):
                problems.append('不该提交的文件：%s（匹配 %s）' % (f, pat))
                break

    # 3+5+6) 内容 / 体积 / 重复
    seen = {}
    for f in files:
        p = REPO / f
        if not p.is_file():
            problems.append('跟踪了但文件不存在：%s' % f)
            continue
        raw = p.read_bytes()
        if len(raw) > MAX_FILE:
            problems.append('文件太大（%.1f MB）：%s' % (len(raw) / 1048576, f))
        h = sha(raw)
        if h in seen:
            pair = (seen[h], f)
            if pair not in KNOWN_DUP and pair[::-1] not in KNOWN_DUP:
                warnings.append('内容完全相同：%s 与 %s（多半是忘了删副本）' % (f, seen[h]))
        else:
            seen[h] = f
        if f in SELF_SKIP or b'\x00' in raw[:8192]:
            continue                       # 规则文件 / 二进制（图标、截图）不扫正文
        try:
            txt = raw.decode('utf-8')
        except UnicodeDecodeError:
            try:
                txt = raw.decode('gbk')
            except UnicodeDecodeError:
                warnings.append('既不是 UTF-8 也不是 GBK，没法扫内容：%s' % f)
                continue
        for w in words:
            i = txt.find(w)
            if i >= 0:
                line = txt[:i].count('\n') + 1
                problems.append('第 %d 行出现本机痕迹 %r（%s）' % (line, w, f))
        for rx, why in BAD_CONTENT:
            m = rx.search(txt)
            if m:
                line = txt[:m.start()].count('\n') + 1
                problems.append('第 %d 行出现%s：%r（%s）' % (line, why, m.group(0), f))

    # 4) 版本号三处一致
    def grab(path, pat):
        p = REPO / path
        if not p.is_file():
            return None
        m = re.search(pat, p.read_text(encoding='utf-8', errors='replace'))
        return m.group(1) if m else None

    v_brain = grab('brain.py', r"APP_VERSION\s*=\s*'v?([\d.]+)'")
    v_setup = grab('setup_app.py', r"VERSION\s*=\s*'v?([\d.]+)'")
    v_json = None
    try:
        v_json = json.loads((REPO / 'version.json').read_text(encoding='utf-8')).get('version', '').lstrip('v')
    except Exception:
        problems.append('version.json 读不出来（JSON 坏了？）')
    trio = {'brain.py': v_brain, 'setup_app.py': v_setup, 'version.json': v_json}
    if len({v for v in trio.values() if v}) > 1:
        problems.append('版本号不一致：' + '、'.join('%s=%s' % kv for kv in trio.items()))

    # 历史扫描（可选）
    if '--history' in args:
        idx = args.index('--history')
        rng = args[idx + 1] if len(args) > idx + 1 and not args[idx + 1].startswith('-') else 'HEAD~1..HEAD'
        hits = scan_history(rng, words)
        for h in hits[:20]:
            problems.append('历史 %s 里出现本机痕迹：%s' % (rng, h))
        print('（历史扫描 %s：%d 处）' % (rng, len(hits)))

    # ── 输出 ──
    print('检查了 %d 个跟踪文件；清单 %d 条；黑名单 %d 个词。' % (len(files), len(all_pat), len(words)))
    for w in warnings:
        print('  [提醒] ' + w)
    for p in problems:
        print('  [问题] ' + p)
    if problems:
        print('\nFAIL：%d 个问题，先修完再提交/发版。' % len(problems))
        return 1
    print('\nPASS：该有的都在，不该有的一样没有；版本号 %s。' % (v_brain or '?'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
