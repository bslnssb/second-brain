# -*- coding: utf-8 -*-
"""把刚打好的应用部署到项目根目录（带自动备份 + 轮转），别再手搓 cp。

背景（2026-11-06）：
    以前每次部署都是手工 `cp SecondBrain.exe` + `mv _internal _internal-prev-<时间戳>`，
    一个下午就在项目根目录堆了 34 个 `_internal-prev-*`（每个 316 MB，合计 11 GB）
    和 36 个 `SecondBrain.exe-prev-*`，根目录彻底看不见正经文件。

现在只有一条命令：
    python -m PyInstaller --noconfirm --clean second-brain-app.spec
    python -B stage_app.py            # 默认保留最近 3 份回滚备份

规则：
    - 旧版本**移动**到 构建产物/build-backup/<YYYYMMDD-HHMMSS>/（同盘 rename，瞬时，不复制）
    - 新版本从 上传的干净版/dist/SecondBrain/ 复制到 开发测试版/
    - 超过 --keep 份的旧备份自动删除（只删本脚本自己产出的目录结构）
    - release/ 已被 brain._SELF_DIRS 排除，备份不会被索引进库

退出码：0 成功；2 源产物缺失；3 复制后校验失败（exe 不在）。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # 上传的干净版
ROOT = HERE.parent / '开发测试版'                # 开发测试版（brain.py 与运行数据所在）
SRC = HERE / 'dist' / 'SecondBrain'
BACKUP_ROOT = HERE.parent / '构建产物' / 'build-backup'

EXE = 'SecondBrain.exe'
INTERNAL = '_internal'


def human(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} GB'


def tree_size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(q.stat().st_size for q in p.rglob('*') if q.is_file())


def source_ok() -> bool:
    if not (SRC / EXE).is_file():
        print(f'[x] 没找到 {SRC / EXE}')
        print('    先跑：python -m PyInstaller --noconfirm --clean second-brain-app.spec')
        return False
    if not (SRC / INTERNAL).is_dir():
        print(f'[x] 没找到 {SRC / INTERNAL}')
        return False
    return True


def backup_current() -> Path | None:
    """只备份旧 exe（**复制**，不移动）；_internal 由 deploy() 增量覆盖。

    2026-11-10 03:5x 的教训：原来用 shutil.move 整体搬 _internal（500+ 文件），
    safe-delete hook 判定为批量删除并弹确认；用户一取消，exe 已经移走、
    _internal 还在、部署没执行 —— 程序目录变成空的。Path.rename 目录在这台
    机器上又 WinError 5。所以改成：绝不整体搬移，只复制那个 12MB 的 exe。
    """
    old_exe = ROOT / EXE
    if not old_exe.exists():
        print('[i] 项目根没有旧 exe，跳过备份')
        return None
    ts = time.strftime('%Y%m%d-%H%M%S')
    dest = BACKUP_ROOT / ts
    n = 1
    while dest.exists():                        # 同一秒内连打两次
        dest = BACKUP_ROOT / f'{ts}-{n}'
        n += 1
    dest.mkdir(parents=True)
    shutil.copy2(old_exe, dest / EXE)
    # 15:4x 物理分家后备份目录搬到了 构建产物/，已经不在 ROOT（开发测试版）下面，
    # 再用 dest.relative_to(ROOT) 会抛 ValueError，把整个部署链断在打印语句上。
    print(f'[1/3] 旧 exe 已备份 → {dest.relative_to(BACKUP_ROOT.parent)}')
    return dest


def _swap_locked_exe(src_exe: Path, dst_exe: Path) -> None:
    """旧 exe 正被运行中的实例锁着（WinError 32）时换上新 exe。

    Windows 允许「重命名」正在运行的 exe，但不允许覆盖或删除它。顺序必须是
    「先把新 exe 复制成同目录临时文件 → 旧 exe 改名让位 → 临时文件改名上位」，
    中途任何异常都把旧 exe 改回去，绝不留一个没有主程序的目录（踩过一次）。
    旧的 .old 通常删不掉（进程还锁着），留到下次部署清理即可，无害。
    """
    tmp = dst_exe.with_name(EXE + '.new')
    old = dst_exe.with_name(EXE + '.old')
    if tmp.is_file():
        tmp.unlink()
    shutil.copy2(src_exe, tmp)
    if old.is_file():
        try:
            old.unlink()
        except OSError:
            old = dst_exe.with_name(EXE + '.old-%d' % int(time.time()))
    dst_exe.rename(old)
    try:
        tmp.rename(dst_exe)
    except OSError:
        try:
            old.rename(dst_exe)
        except OSError:
            pass
        raise
    try:
        old.unlink()
    except OSError:
        pass


def prune_web_assets(root: Path) -> int:
    """删掉 web/assets 里没被 index.html 引用的旧构建产物（第 168 项）。

    增量部署只覆盖、不删除，每打一次包就多留一份 ~340KB 的旧 bundle：
    实测部署目录已经堆了 13 份（3.1MB），长期会一直涨。
    只动 assets 下形如 index-<hash>.{js,css} 的文件，且 index.html 里仍引用的保留；
    logo 之类的静态资源一律不碰。
    """
    web = root / 'web'
    idx = web / 'index.html'
    assets = web / 'assets'
    if not idx.is_file() or not assets.is_dir():
        return 0
    try:
        html = idx.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return 0
    keep = set(re.findall(r'index-[A-Za-z0-9_-]+\.(?:js|css)', html))
    n = 0
    for f in assets.iterdir():
        if not f.is_file():
            continue
        if not re.match(r'^index-[A-Za-z0-9_-]+\.(?:js|css)$', f.name):
            continue
        if f.name in keep:
            continue
        try:
            f.unlink()
            n += 1
        except OSError:
            pass        # 被占用就留着，清垃圾绝不能让部署失败
    return n


def deploy() -> bool:
    # 只覆盖内容不同的文件：用户常常一边开着程序一边打包，_internal 里的
    # DLL 被运行中的实例锁着（WinError 32），全量 copytree 会报几十条错并中断。
    # 相同文件一律跳过；不同的文件万一被占用也只警告，不把部署判失败。
    import hashlib
    # 主程序 exe 也会被运行中的实例锁住（WinError 32）。先按 sha 比：内容相同就
    # 整个跳过；不同才试着覆盖，被占用只警告 —— 前端改动常常只动 _internal/web，
    # exe 本来就没变，不该因为「覆盖一个没变的文件」把整条部署链断掉。
    src_exe, dst_exe = SRC / EXE, ROOT / EXE
    exe_note = ''
    try:
        need_exe = (not dst_exe.is_file()
                    or hashlib.sha256(src_exe.read_bytes()).digest()
                    != hashlib.sha256(dst_exe.read_bytes()).digest())
    except OSError:
        need_exe = True
    if need_exe:
        try:
            shutil.copy2(src_exe, dst_exe)
        except OSError as e:
            # 光「警告跳过」不够：PYZ.pyz 就嵌在 exe 里，exe 没换 = 新 Python
            # 代码根本没部署，验包只看 brain 常量会假绿（2026-11-12 实测）。
            try:
                _swap_locked_exe(src_exe, dst_exe)
                exe_note = '[i] 旧 exe 被占用：已改名让位为 .old，换上了新 exe'
            except OSError as e2:
                exe_note = ('[!] 主程序 exe 没更新（WinError %s；让位也失败 %s）。'
                            '退出程序后重跑 stage_app.py 即可补齐。'
                            % (getattr(e, 'winerror', '?'),
                               getattr(e2, 'winerror', '?')))
    else:
        exe_note = '[i] 主程序 exe 内容未变，跳过覆盖'
    root_int = SRC / INTERNAL
    n, skipped = 0, []
    for p in root_int.rglob('*'):
        if not p.is_file():
            continue
        q = ROOT / INTERNAL / p.relative_to(root_int)
        if q.is_file() and q.stat().st_size == p.stat().st_size:
            try:
                if (hashlib.sha256(q.read_bytes()).digest()
                        == hashlib.sha256(p.read_bytes()).digest()):
                    continue
            except OSError:
                pass
        q.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(p, q)
            n += 1
        except OSError as e:
            skipped.append('%s (%s)' % (q.name, getattr(e, 'winerror', '?')))
    if exe_note:
        print(exe_note)
    if skipped:
        print(f'[!] {len(skipped)} 个文件被占用没覆盖（多半是运行中的旧实例，重启后即可）：'
              + '、'.join(skipped[:4]))
    # PYZ.pyz 就嵌在 exe 里：exe 没换 = 新 Python 代码根本没部署。
    # 前面所有「被占用只警告」都可能掩盖这一点，所以最后显式比一次 sha 并打印。
    try:
        same_exe = (dst_exe.is_file()
                    and hashlib.sha256(dst_exe.read_bytes()).digest()
                    == hashlib.sha256(src_exe.read_bytes()).digest())
        print('[2/3] exe 与构建产物 sha ' +
              ('一致' if same_exe else '不一致（新代码没进去，退出程序后重跑 stage_app.py）'))
    except OSError:
        same_exe = False
    # 兜底反馈入口（第 164 项）：跟 exe 放一起，双击即用
    try:
        src_bat = SRC / '报告问题.bat'
        if not src_bat.is_file():
            src_bat = Path(__file__).resolve().parent / '报告问题.bat'
        if src_bat.is_file():
            shutil.copy2(src_bat, ROOT / '报告问题.bat')
            print('[2/3] 兜底入口 → 报告问题.bat')
        n = prune_web_assets(ROOT / '_internal')
        if n:
            print('[2/3] 清掉 %d 份过期的前端资源' % n)
    except Exception as e:
        print('[!] 报告问题.bat 没复制成功：%r' % (e,))
    ok = (ROOT / EXE).is_file() and (ROOT / INTERNAL).is_dir() and same_exe
    print(f'[2/3] 新版本已就位 → {ROOT.name}\\{EXE} '
          f'({human((ROOT / EXE).stat().st_size)})，_internal {human(tree_size(ROOT / INTERNAL))}')
    return ok


def rotate(keep: int) -> None:
    sets = sorted([q for q in BACKUP_ROOT.iterdir() if q.is_dir()], key=lambda q: q.name)
    drop = sets[:-keep] if keep > 0 else []
    if not drop:
        print(f'[3/3] 备份共 {len(sets)} 份，未超过 {keep} 份，不清理')
        return
    freed = 0
    stuck = []
    for q in drop:
        freed += tree_size(q)
        # 2026-11-09：safe-delete hook 连 Python 的 shutil.rmtree 也拦（>50 文件直接终止
        # 进程），清备份只是「顺手做」，绝不能因此让整个部署判失败。拦了就记下来继续；
        # 想彻底跳过清理用 `stage_app.py --keep 0`。
        try:
            shutil.rmtree(q, ignore_errors=True)
        except BaseException as e:
            print(f'[!] 清理被拦（跳过，不影响部署）：{q.name} {type(e).__name__}')
        # ignore_errors 会让「删不掉」彻底静默：2026-11-06 就撞过一次，
        # 备份攒到 5 份、1.6 GB，日志却一路说清理成功。删完必须复查。
        if q.exists():
            stuck.append(q.name)
    print(f'[3/3] 清理 {len(drop)} 份旧备份，释放 {human(freed)}；'
          f'保留 {len(sets) - len(drop)} 份')
    if stuck:
        print(f'[!] 有 {len(stuck)} 份没删掉（多半被资源管理器/杀软占用），'
              f'手动删：{BACKUP_ROOT} → ' + '、'.join(stuck))


def main() -> int:
    ap = argparse.ArgumentParser(description='部署应用并轮转回滚备份')
    ap.add_argument('--keep', type=int, default=3, help='保留几份回滚备份（默认 3，0=不清理）')
    args = ap.parse_args()

    print(f'源：{SRC}')
    print(f'目标：{ROOT}')
    if not source_ok():
        return 2
    backup_current()
    if not deploy():
        print('[x] 部署校验失败：缺 exe / _internal，或 exe 与构建产物 sha 不一致')
        print('    若提示文件被占用，先退出正在运行的 SecondBrain.exe，再重跑 stage_app.py')
        return 3
    rotate(max(args.keep, 0))
    print('完成。回滚＝把 构建产物/build-backup 里对应目录的 exe 和 _internal 搬回 开发测试版/。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
