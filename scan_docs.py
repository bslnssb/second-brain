#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二大脑 · 全盘文档扫描器（重扫清单用，只记录路径/大小，不读内容）
排除规则与建库时一致：小说零接触、系统/游戏/缓存目录跳过。"""
import csv
import os
import sys
from pathlib import Path

NOVEL_WORDS = ('小说', '书屋', '书城', '书架', 'novel', 'fiction', '轻小说', '网文')
SKIP_DIRS = {'windows', 'program files', 'program files (x86)', 'appdata', '$recycle.bin',
             'node_modules', '.git', '__pycache__', 'site-packages', 'recovery',
             'system volume information', 'pylibs', '.vs', 'packages'}
DOCS = {'.pdf', '.docx', '.doc', '.pptx', '.xlsx', '.md', '.rtf', '.html', '.htm', '.txt', '.epub', '.mobi'}
BIG_TXT = 200 * 1024

def app_dir():
    """脚本所在目录；打包成 exe 后为 exe 所在目录"""
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

def load_includes():
    """读 scope.json 的 include_dirs（读不到/格式坏就返回空表，不影响全盘扫描）"""
    try:
        import json
        p = app_dir() / 'scope.json'
        data = json.loads(p.read_text(encoding='utf-8'))
        return [str(x) for x in data.get('include_dirs', [])]
    except Exception:
        return []

def _has_skip_segment(path):
    """路径中是否含 SKIP_DIRS 某一段：这类目录全盘扫描会被跳过，必须单独当根扫"""
    return any(part.lower() in SKIP_DIRS for part in Path(path).parts)

def drive_targets():
    import string
    out = []
    for d in string.ascii_uppercase:
        root = f'{d}:\\'
        if os.path.exists(root):
            out.append(os.path.expanduser('~')
                       if d + ':' == os.environ.get('SystemDrive', 'C:') else root)
    # 用户的资料目录可能被放在 "Program Files" 这类系统目录下（便携部署很常见），
    # 整条路径命中 SKIP_DIRS 就会被全盘扫描整片跳过，所以显式范围内的这类目录
    # 单独作为扫描根，重复的按规范化路径去重。
    seen = {os.path.normcase(os.path.abspath(x)) for x in out}
    for inc in load_includes():
        if not os.path.isdir(inc) or not _has_skip_segment(inc):
            continue
        key = os.path.normcase(os.path.abspath(inc))
        if key not in seen:
            seen.add(key)
            out.append(inc)
    return out

def is_novel_dir(path):
    low = path.lower()
    return any(w in low for w in NOVEL_WORDS)

def run_scan(targets=None):
    """targets=None 时全盘扫描（命令行老行为）；桌面版传入用户选的文件夹列表。"""
    import brain  # 调用时机在 brain 加载完成后，无循环依赖问题
    rows, novel_kw = [], 0
    roots = [str(t) for t in targets] if targets is not None else drive_targets()
    brain._prog(state='busy', phase='scan', done=0, total=len(roots), ok=0, fail=0, err='')
    for i, target in enumerate(roots):
        brain._prog(done=i, msg=f'正在扫描 {target}', k='m_scanning', a={'p': target})
        for cur, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith('.')]
            if is_novel_dir(cur):
                dirs[:] = []
                novel_kw += 1
                continue
            for f in files:
                if os.path.splitext(f)[1].lower() in DOCS:
                    p = os.path.join(cur, f)
                    try:
                        rows.append((p, os.path.getsize(p)))
                    except OSError:
                        pass
    brain._prog(phase='scan-end', msg=f'发现 {len(rows)} 个文档，准备入库',
                k='m_scan_end', a={'n': len(rows)})

    # 同一文件可能被"盘符根"和"显式范围根"各走到一次：按路径去重（保序）
    uniq = {}
    for p, sz in rows:
        uniq.setdefault(p, sz)
    rows = list(uniq.items())

    out = app_dir() / 'scan_raw.csv'
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['path', 'size'])
        w.writerows(rows)
    print(f'扫描完成：{len(rows)} 个文档（跳过小说类目录 {novel_kw} 个）-> {out.name}')
    return len(rows)

if __name__ == '__main__':
    run_scan()
