#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二大脑 · 全盘文档扫描器（重扫清单用，只记录路径/大小，不读内容）
排除规则与建库时一致：小说零接触、系统/游戏/缓存目录跳过。"""
import csv
import os
import sys
import time
from pathlib import Path

NOVEL_WORDS = ('小说', '书屋', '书城', '书架', 'novel', 'fiction', '轻小说', '网文')
SKIP_DIRS = {'windows', 'program files', 'program files (x86)', 'appdata', '$recycle.bin',
             'node_modules', '.git', '__pycache__', 'site-packages', 'recovery',
             'system volume information', 'pylibs', '.vs', 'packages',
             # Windows「跨设备」虚拟文件夹（手机经「连接至 Windows」映射进来的
             # C:\Users\<user>\CrossDevice\<手机名>）：枚举它会尝试连接手机，手机不在
             # 就弹「连接至 Windows」对话框（第 122 项），且内容随手机在线状态变化，
             # 索引它没有意义。
             'crossdevice'}
# 第二大脑从「文档检索」扩为「全文件检索」（对标 Listary）：
# 视频/图片/应用/音频/压缩包/源码/文档全部入索引，按 size 跳过超 50MB 的大文件。
# 系统临时文件、缓存、备份类型主动排除，避免索引冗余。
ALL_FILES = {
    # 文档（原 DOCS）
    '.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls',
    '.md', '.markdown', '.rtf', '.html', '.htm', '.txt', '.epub', '.mobi',
    # 视频
    '.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.m4v', '.mpg', '.mpeg',
    # 图片
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.ico', '.heic', '.psd',
    # 应用程序
    '.exe', '.lnk', '.bat', '.cmd', '.ps1', '.appref-ms', '.msi', '.scr',
    # 音频
    '.mp3', '.aac', '.flac', '.wav', '.ogg', '.m4a', '.wma',
    # 压缩包
    '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso',
    # 源码 / 配置
    '.py', '.js', '.ts', '.tsx', '.jsx', '.cpp', '.c', '.h', '.hpp', '.cs',
    '.java', '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.json', '.xml',
    '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.csv', '.log',
    # 其它常见格式
    '.torrent', '.apk', '.dmg', '.deb', '.rpm',
}
# 索引时按 size 上限跳过：视频/压缩包常 >100MB，全收会很慢且检索意义不大
SIZE_LIMIT = 80 * 1024 * 1024   # 80 MB
DOCS = ALL_FILES                # 兼容旧引用
BIG_TXT = 200 * 1024

def app_dir():
    """放扫描清单的地方 —— 必须和 brain.app_dir() 一致。

    否则用户在只读目录安装、或设置了 SB_DATA_DIR 时，清单会写到程序目录，
    而正文索引去数据目录找，结果是「扫描跑完了、一篇正文都没有」。
    老办法（按脚本位置推断）保留为兜底。"""
    try:
        import brain
        return brain.app_dir()
    except Exception:
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
        # 范围里的文件夹被搬走/盘符变了：os.walk 会安静地返回空，看起来和"这盘确实
        # 没文档"一模一样。这里必须留痕，否则后面 prune 会把整库当"用户删了"清掉。
        if not os.path.isdir(target):
            print(f'[!] 跳过：{target} 不存在（搬走了？盘符变了？）', flush=True)
            brain._prog(done=i, msg=f'{target} 不存在，已跳过', k='')
            continue
        brain._prog(done=i, msg=f'正在扫描 {target}', k='m_scanning', a={'p': target})
        # 扫描是"静默"的：一分钟一条输出都没有，用户会以为死机，去连点两次、
        # 或者干脆关掉黑窗。每 3 秒报一次心跳（含当前目录），看着心里有数。
        n_seen = 0
        t_hb = time.time()
        errs = [0]

        def _onerr(e):
            errs[0] += 1

        for cur, dirs, files in os.walk(target, onerror=_onerr):
            # 剪掉 junction / 符号链接：os.walk(followlinks=False) 用 os.path.islink 判断，
            # 而 Windows 上 junction 的 islink 是 False，指向上层的 junction 会成环，
            # 让正文扫描永远跑不完。判据见 brain._is_reparse_dir。
            dirs[:] = [d for d in dirs if not brain._is_reparse_dir(os.path.join(cur, d))]
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith('.')]
            if is_novel_dir(cur):
                dirs[:] = []
                novel_kw += 1
                continue
            n_seen += len(files)
            if time.time() - t_hb >= 3:
                t_hb = time.time()
                print(f'  ...已看过 {n_seen:,} 个文件（当前：{cur}）', flush=True)
                brain._prog(msg=f'正在扫描 {target}（已看 {n_seen:,} 个文件）', k='')
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in DOCS:
                    continue
                p = os.path.join(cur, f)
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    continue
                # 大文件不入索引（视频/压缩包常 >80MB，意义不大还拖慢检索）
                if sz > SIZE_LIMIT:
                    continue
                rows.append((p, sz))
        if errs[0]:
            print(f'  [!] 有 {errs[0]} 个文件夹读不进去（权限/占用），已跳过', flush=True)
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
    print(f'扫描完成：{len(rows)} 个文件（文档/视频/图片/应用/音频/压缩包等全文件类型，跳过小说类目录 {novel_kw} 个，超 {SIZE_LIMIT//1024//1024}MB 跳过）-> {out.name}')
    return len(rows)

if __name__ == '__main__':
    run_scan()
