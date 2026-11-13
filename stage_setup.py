# -*- coding: utf-8 -*-
"""把刚打出来的安装器落到项目的 构建产物/dist/（版本号进文件名，旧同名进 prev/）。

以前这一步是手工 `cp` 到 `%LOCALAPPDATA%\\SecondBrain\\dist\\`，结果当前版本的安装包
散落在系统用户目录里，既不在项目里也不在版本控制里（第 79 项）。现在固定收在这里：
`构建产物/dist/`，位于 `上传的干净版` 这个 git 仓库之外。
"""
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import setup_app  # noqa: E402

src = HERE / 'dist' / 'SecondBrain-Setup.exe'
out_dir = HERE.parent / '构建产物' / 'dist'
ver = setup_app.VERSION
dst = out_dir / ('SecondBrain-Setup-v%s.exe' % ver)

if not src.exists():
    print('ERROR: %s 不存在，先跑 PyInstaller 打安装器' % src, flush=True)
    sys.exit(2)

out_dir.mkdir(parents=True, exist_ok=True)
if dst.exists():
    prev = out_dir / 'prev'
    prev.mkdir(exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M')
    shutil.move(str(dst), str(prev / ('%s-prev-%s' % (dst.name, stamp))))
    print('旧版归档到 prev/', flush=True)

shutil.copy2(str(src), str(dst))
print('staged', dst, dst.stat().st_size, 'B  (v%s)' % ver, flush=True)
