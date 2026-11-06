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
    - 旧版本**移动**到 <项目根>/release/build-backup/<YYYYMMDD-HHMMSS>/（同盘 rename，瞬时，不复制）
    - 新版本从 release/github/dist/SecondBrain/ 复制到项目根
    - 超过 --keep 份的旧备份自动删除（只删本脚本自己产出的目录结构）
    - release/ 已被 brain._SELF_DIRS 排除，备份不会被索引进库

退出码：0 成功；2 源产物缺失；3 复制后校验失败（exe 不在）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # release/github
ROOT = HERE.parent.parent                        # 项目根（brain.py 所在）
SRC = HERE / 'dist' / 'SecondBrain'
BACKUP_ROOT = ROOT / 'release' / 'build-backup'

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
    """把项目根现有的 exe + _internal 挪进 build-backup/<ts>/。返回备份目录。"""
    old_exe, old_int = ROOT / EXE, ROOT / INTERNAL
    if not old_exe.exists() and not old_int.exists():
        print('[i] 项目根没有旧版本，跳过备份（首次部署）')
        return None
    ts = time.strftime('%Y%m%d-%H%M%S')
    dest = BACKUP_ROOT / ts
    n = 1
    while dest.exists():                        # 同一秒内连打两次
        dest = BACKUP_ROOT / f'{ts}-{n}'
        n += 1
    dest.mkdir(parents=True)
    if old_exe.exists():
        shutil.move(str(old_exe), str(dest / EXE))
    if old_int.is_dir():
        shutil.move(str(old_int), str(dest / INTERNAL))
    print(f'[1/3] 旧版本已备份 → {dest.relative_to(ROOT)}')
    return dest


def deploy() -> bool:
    shutil.copy2(SRC / EXE, ROOT / EXE)
    shutil.copytree(SRC / INTERNAL, ROOT / INTERNAL)
    ok = (ROOT / EXE).is_file() and (ROOT / INTERNAL).is_dir()
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
    for q in drop:
        freed += tree_size(q)
        shutil.rmtree(q, ignore_errors=True)
    print(f'[3/3] 清理 {len(drop)} 份旧备份，释放 {human(freed)}；'
          f'保留 {len(sets) - len(drop)} 份')


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
        print('[x] 部署后校验失败：根目录没有 SecondBrain.exe')
        return 3
    rotate(max(args.keep, 0))
    print('完成。回滚＝把 build-backup 里对应目录的 exe 和 _internal 搬回项目根。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
