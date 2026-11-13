# -*- coding: utf-8 -*-
"""把 dist/SecondBrain 打成安装器用的 payload.zip。

**先自检再打包**：上一版出过「payload 是空的但安装器照样打包成功」的事故
（第 77 项之后发现的 v0.9.32 安装器），用户装到一半只得到一句「解压后没找到主程序」。
所以这里缺主程序就直接非零退出，不生成 payload。
"""
import sys
import zipfile
from pathlib import Path

root = Path('dist/SecondBrain')
docs = [Path('使用说明.md'), Path('README.md')]
out = Path('payload.zip')
sep = chr(92)  # 反斜杠，避免转义麻烦

exe = root / 'SecondBrain.exe'
if not exe.exists():
    print('ERROR: %s missing — 先把主程序打包到 dist/SecondBrain 再生成 payload' % exe,
          flush=True)
    sys.exit(2)

# 界面打不开时的兜底入口（第 164 项）：随包分发，双击即可上报
bat = root / '报告问题.bat'
if not bat.exists():
    bat.write_bytes(Path('报告问题.bat').read_bytes())

n = 0
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in root.rglob('*'):
        if p.is_file():
            rel = str(p.relative_to(root)).replace(sep, '/')
            z.write(p, 'SecondBrain/' + rel)
            n += 1
    for d in docs:
        if d.exists():
            z.write(d, 'SecondBrain/' + d.name)
            n += 1

# 打完再验一遍：清单里必须有主程序，且文件数不能少得离谱（正常 500+）
with zipfile.ZipFile(out) as z:
    names = z.namelist()
bad = [x for x in names if x == 'SecondBrain/']
if 'SecondBrain/SecondBrain.exe' not in names or n < 100:
    print('ERROR: payload 不完整（%d 个文件，主程序%s）' %
          (n, '在' if 'SecondBrain/SecondBrain.exe' in names else '缺失'), flush=True)
    out.unlink(missing_ok=True)
    sys.exit(3)

print('payload', n, 'files', round(out.stat().st_size / 1e6, 1), 'MB',
      'exe', exe.stat().st_size, 'B')
