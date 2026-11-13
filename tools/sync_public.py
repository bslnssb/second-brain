# -*- coding: utf-8 -*-
"""把开发根里「该公开的东西」按清单同步到 上传的干净版，并把不该出现的东西报出来。

为什么要有这个脚本（2026-11-06）：
    以前发版前要手工 `cp brain.py setup_app.py 上传的干净版/`、
    `cp -r web/src/. 上传的干净版/web/src/`、`cp -r web/dist ...`，
    每次都得在脑子里过一遍「哪些该传、哪些不能传」——漏一次就把本机路径或私人笔记
    传上去了（v0.9.44 那次真发生了，`使用说明.md` 是我自己的笔记本）。
    现在清单写在 tools/public_files.txt，一条命令搞定，判断成本为零。

用法：
    python tools/sync_public.py            # 按清单同步（只动清单里 sync 的文件）
    python tools/sync_public.py --dry-run  # 只报告，不落盘
    python tools/sync_public.py -v         # 连「内容相同」的也列出来

退出码：0 正常；2 清单里的源文件缺失；3 仓库里有多余/缺失的跟踪文件。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # 上传的干净版/tools
REPO = HERE.parent                              # 上传的干净版  ← 对外仓库根
DEV = REPO.parent / '开发测试版'                # 开发测试版    ← 源码与运行数据
MANIFEST = HERE / 'public_files.txt'

# 仓库里允许存在、但清单不必逐条列出的东西（git 自己 + 构建产物 + 缓存）
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', 'build', 'dist', 'prev',
             'release', '.github_ignore'}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def parse_manifest():
    """返回 {'sync','local','build','private'}，值为清单里的原始模式。"""
    out = {'sync': [], 'local': [], 'build': [], 'private': []}
    for i, line in enumerate(MANIFEST.read_text(encoding='utf-8').splitlines(), 1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        parts = s.split(None, 1)
        if len(parts) != 2 or parts[0] not in out:
            raise SystemExit('[!] 清单第 %d 行格式不对：%r（应为 "sync|local|build 路径"）' % (i, line))
        out[parts[0]].append(parts[1].strip())
    return out


def expand(root: Path, pattern: str):
    """把清单条目展开成实际文件列表。目录条目返回其下所有文件（相对路径）。"""
    if pattern.endswith('/'):
        base = root / pattern
        if not base.is_dir():
            return []
        return [p.relative_to(root).as_posix() for p in sorted(base.rglob('*')) if p.is_file()]
    p = root / pattern
    return [pattern] if p.is_file() else []


def tracked():
    """git 已跟踪的文件（相对路径）。不是 git 仓库就返回空集。"""
    try:
        # core.quotepath=false：不加这个，中文文件名会被转义成 "\344\275\277…"，
        # 跟清单里的 UTF-8 原文永远匹配不上，全被当成「多余文件」。
        r = subprocess.run(['git', '-c', 'core.quotepath=false', 'ls-files'],
                           cwd=str(REPO), capture_output=True, text=True, encoding='utf-8')
        if r.returncode != 0:
            return set()
        return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    except Exception:
        return set()


def covered(path: str, patterns) -> bool:
    for pat in patterns:
        if pat.endswith('/'):
            if path.startswith(pat):
                return True
        elif path == pat:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description='按清单把开发根同步到公开仓库')
    ap.add_argument('--dry-run', action='store_true', help='只报告，不写文件')
    ap.add_argument('-v', '--verbose', action='store_true', help='连内容相同的也列出来')
    args = ap.parse_args()

    if not DEV.joinpath('brain.py').is_file():
        raise SystemExit('[!] 找不到开发根：%s（这个脚本只能在项目里跑）' % DEV)

    mf = parse_manifest()
    added, updated, same, missing, stale = [], [], [], [], []

    for pattern in mf['sync']:
        files = expand(DEV, pattern)
        if not files:
            missing.append(pattern)
            continue
        for rel in files:
            src, dst = DEV / rel, REPO / rel
            if not dst.is_file():
                added.append(rel)
                if not args.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(src.read_bytes())
            elif sha(src) != sha(dst):
                updated.append(rel)
                if not args.dry_run:
                    dst.write_bytes(src.read_bytes())
            else:
                same.append(rel)

    for pattern in mf['build']:
        files = expand(DEV, pattern)
        if not files:
            missing.append(pattern + '  （先 `cd web && npm run build`）')
            continue
        # 构建产物必须按「镜像」同步，不能只覆盖：Vite 每次生成的 hash 文件名都不同，
        # 上一版的 index-XXXX.js 会永远留在仓库里，最后跟着 payload 被塞进 exe。
        # 目标目录里凡是源目录没有的文件，一律删掉（只删文件，不整目录 rmtree，
        # 免得被 safe-delete 拦成批量删除）。
        if pattern.endswith('/'):
            dst_root = REPO / pattern.rstrip('/')
            src_set = set(files)
            if dst_root.is_dir():
                for old in sorted(dst_root.rglob('*')):
                    if not old.is_file():
                        continue
                    rel = old.relative_to(REPO).as_posix()
                    if rel not in src_set:
                        stale.append(rel)
                        if not args.dry_run:
                            old.unlink()
        for rel in files:
            src, dst = DEV / rel, REPO / rel
            if not dst.is_file() or sha(src) != sha(dst):
                updated.append(rel)
                if not args.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(src.read_bytes())
            else:
                same.append(rel)

    # ── 专业版私有模块：镜像到打包工作区，但绝不进 git ──
    # 公开源码版没有 pro/ 包，brain.py 会把 Pro 入口关掉；这里的副本只给
    # PyInstaller 打 exe 用。stale 文件按镜像清掉，避免旧实现被打进包。
    for pattern in mf['private']:
        # 别把 __pycache__ 一起镜像过去：那是本机编译产物，不是源码，
        # 混进打包工作区还会让 stale 清理每次都报一堆无关项。
        files = [f for f in expand(DEV, pattern)
                 if '__pycache__' not in f and not f.endswith(('.pyc', '.pyo'))]
        if not files:
            missing.append(pattern + '  （开发根 pro/ 不存在，打包会缺 Pro）')
            continue
        if pattern.endswith('/'):
            dst_root = REPO / pattern.rstrip('/')
            src_set = set(files)
            if dst_root.is_dir():
                for stale_file in sorted(dst_root.rglob('*')):
                    if not stale_file.is_file():
                        continue
                    rel = stale_file.relative_to(REPO).as_posix()
                    if rel not in src_set:
                        stale.append(rel)
                        if not args.dry_run:
                            stale_file.unlink()
        for rel in files:
            src, dst = DEV / rel, REPO / rel
            if not dst.is_file() or sha(src) != sha(dst):
                updated.append(rel)
                if not args.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(src.read_bytes())
            else:
                same.append(rel)

    # ── 仓库侧：跟踪文件必须全部被清单覆盖 ──
    all_pat = mf['sync'] + mf['local'] + mf['build']
    tr = tracked()
    leaked = sorted(p for p in tr if p.startswith('pro/') or p in
                    ('pro_api.py', 'ocr_batch.py', 'xlsx_writer.py'))
    if leaked:
        print('\n[!] 私有模块被 git 跟踪（先 git rm --cached 再同步）：')
        for q in leaked:
            print('   ', q)
        return 4
    orphan = sorted(p for p in tr if not covered(p, all_pat))
    absent = [p for p in mf['local'] if not p.endswith('/') and not (REPO / p).is_file()]
    absent += [p for p in mf['local'] if p.endswith('/') and not (REPO / p).is_dir()]

    print('清单：%d 条 sync / %d 条 local / %d 条 build / %d 条 private' %
          (len(mf['sync']), len(mf['local']), len(mf['build']),
           len(mf['private'])))
    print('开发根：%s\n公开仓库：%s\n' % (DEV, REPO))
    for label, items in (('新增', added), ('更新', updated), ('清掉的旧构建文件', stale),
                         ('缺失', missing),
                         ('仓库里缺清单要求的', absent), ('仓库里多余的跟踪文件', orphan)):
        if not items:
            continue
        print('%s（%d）：' % (label, len(items)))
        for it in items[:40]:
            print('   ', it)
        if len(items) > 40:
            print('    … 还有 %d 条' % (len(items) - 40))
    if args.verbose and same:
        print('内容相同（%d）：%s' % (len(same), '、'.join(same[:8]) + (' …' if len(same) > 8 else '')))
    if args.dry_run:
        print('\n[dry-run] 什么都没写。')
    else:
        print('\n同步完成：新增 %d、更新 %d、内容相同 %d。' % (len(added), len(updated), len(same)))

    if missing or orphan or absent:
        print('\n[!] 上面有需要人工确认的项：')
        if missing:
            print('    · 清单里的源文件不存在 —— 是改名/删除了？改清单，别假装同步成功。')
        if absent:
            print('    · 清单声明「本地维护」但仓库里没有 —— 补文件，或从清单删掉这条。')
        if orphan:
            print('    · 仓库里有清单没写的文件 —— 要么加进清单，要么 `git rm` 掉。'
                  ' 公开仓库的内容必须**恰好等于**清单。')
        return 2 if missing else 3
    print('\n[✓] 仓库内容与清单完全一致。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
