# -*- coding: utf-8 -*-
"""把 dist/SecondBrain 打成免安装便携版 zip（发 GitHub Release 附件用）。

和 make_payload.py 同一套纪律：**先自检再打包，打完再验一遍**——缺主程序/打完清单
不对就直接非零退出，不产出半成品。zip 里不带任何用户数据（brain.db / settings.json /
scope.json / tmp / thumbs / backups / webview-profile），解压到哪个目录都是全新开始；
程序目录只读时会自动退到 %LOCALAPPDATA%\\SecondBrain\\data，便携盘也能跑。

用法（在对外仓库根目录下，即 上传的干净版/）：
    python tools/make_portable.py
产物：../构建产物/dist/SecondBrain-v<版本>-portable.zip（不进 git）。
"""
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent          # 上传的干净版/tools
REPO = HERE.parent                              # 上传的干净版
root = REPO / 'dist' / 'SecondBrain'
out_dir = REPO.parent / '构建产物' / 'dist'    # 和工作区里安装包放一起
sep = chr(92)  # 反斜杠，避免转义麻烦

# 这些是「这一台机器的状态」，绝不能打进便携版
EXCLUDE_FILES = {'brain.db', 'brain.db-wal', 'brain.db-shm', 'settings.json',
                 'scope.json', 'debug.log', 'eval_report.txt', 'scan_raw.csv',
                 'update_failed.txt', 'notices_cache.json', 'payload.zip'}
EXCLUDE_DIRS = {'tmp', 'thumbs', 'backups', 'webview-profile', '__pycache__',
                '.workbuddy', '.git'}

exe = root / 'SecondBrain.exe'
if not exe.exists():
    print('ERROR: %s missing — 先把主程序打包到 dist/SecondBrain 再做便携版' % exe,
          flush=True)
    sys.exit(2)

ver = '?'
try:
    ver = str(json.loads(Path('version.json').read_text(encoding='utf-8')).get('version') or '?')
    if ver.startswith('v'):
        ver = ver[1:]          # version.json 存的是 v0.9.49，文件名里别出现双 v
except Exception:
    pass
out = out_dir / ('SecondBrain-v%s-portable.zip' % ver)

n = 0
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file():
            rel = str(p.relative_to(root))
            parts = rel.split(sep)
            if any(seg in EXCLUDE_DIRS for seg in parts[:-1]) or parts[-1] in EXCLUDE_FILES:
                continue
            z.write(p, 'SecondBrain/' + rel.replace(sep, '/'))
            n += 1
    for d in (Path('使用说明.md'), Path('README.md')):
        if d.exists():
            z.write(d, 'SecondBrain/' + d.name)
            n += 1

# 打完再验：主程序必须在、文件数不能少得离谱（正常 ~500）、用户数据一个都不许混进去
with zipfile.ZipFile(out) as z:
    names = z.namelist()
if 'SecondBrain/SecondBrain.exe' not in names or n < 100:
    print('ERROR: 便携版不完整（%d 个文件，主程序%s）'
          % (n, '在' if 'SecondBrain/SecondBrain.exe' in names else '缺失'), flush=True)
    out.unlink(missing_ok=True)
    sys.exit(3)
leak = [x for x in names if any(seg in EXCLUDE_DIRS for seg in x.split('/')[1:-1])
        or x.rsplit('/', 1)[-1] in EXCLUDE_FILES]
if leak:
    print('ERROR: 便携版混进了用户数据：%s' % leak[:5], flush=True)
    out.unlink(missing_ok=True)
    sys.exit(4)

print('portable', n, 'files', round(out.stat().st_size / 1e6, 1), 'MB →', out)
