#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brain.py — 本地第二大脑（轻量版）：全文索引 + 搜索，全部本地完成

原则：内容不出本机；不想索引的目录写在 scope.json 的 exclude_dirs，小说类目录由 scan_docs.py 关键词双保险零接触。

命令：
  index   <scope.json>          按范围建索引（SQLite FTS5 trigram，断点续建）
  update                        增量维护：全盘重扫 → 索引新文件 → 清死条目
  search  <关键词> [条数]        命令行搜索（中文≥3字走FTS，短词自动降级LIKE）
  serve   [端口]                 本机网页界面 http://127.0.0.1:8765（仅本机可访问）
  selftest                      质量自检（合成数据，不碰真实库）
  无参数                         等价于 serve（桌面窗口版走 app_window.py）

支持格式：txt/md（utf-8/gbk）、pdf（pypdf，纯扫描件自动 OCR 兜底）、docx/pptx/xlsx/epub（zip+XML剥皮）、html
不支持：.doc 旧二进制格式（跳过并记录）、企业加密文档（假 docx，魔数 sisf…，跳过并记录）
"""
import csv
import json
import os
import re
import scan_docs
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

APP_VERSION = 'v6.5'

def app_dir():
    """脚本所在目录；打包成 exe 后为 exe 所在目录（数据库/配置放在 exe 旁边）"""
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

DB = app_dir() / 'brain.db'
MAX_BODY = 2 * 1024 * 1024  # 单文档正文上限 2MB
OCR_PAGES = 6               # OCR 最多识别前 N 页：建搜索特征用不着整本，页数越多单篇越慢
OCR_DPI = 180               # 渲染清晰度，实测中文识别够用
OCR_BUDGET = 20             # 单篇 OCR 的时间预算（秒），到点就用已识别的部分
PDF_MAX_SIZE = 80 * 1024 * 1024   # 超过这个体积的 PDF 直接跳过（整本扫描图集）

# 更新进度（网页/桌面窗口轮询展示用）
# k + a 是给前端的语言无关消息（k 为文案键，a 为插值参数）；msg 保留中文原文作兜底，
# 命令行直接打印时仍可读，网页按当前语言渲染。
PROG = {'state': 'idle', 'phase': '', 'done': 0, 'total': 0, 'ok': 0, 'fail': 0,
        'msg': '', 'err': '', 'k': '', 'a': {}}

def _prog(**kw):
    """更新进度。传 k='消息键', a={参数} 让前端按当前语言渲染；
    只传 msg 时前端原样显示（用于异常信息这类无法预翻译的文本）。"""
    if 'k' in kw:
        kw.setdefault('a', {})
    else:
        kw['k'], kw['a'] = '', {}
    PROG.update(kw)

# 智能扫描：噪音预判（默认不勾，用户可手动勾上）。按候选目录整条路径匹配。
NOISE_WORDS = ('steam', 'steamapps', 'epic games', '游戏', '驱动', 'driver', '备份',
               'backup', '缓存', 'cache', 'vmware', 'virtualbox', '虚拟机', 'recycle',
               'recovery', '恢复', '安装包', 'ghost', 'windows.old')

# 智能扫描发现结果（供 /api/discover 轮询）
DISCOVER = {'state': 'idle', 'groups': [], 'err': ''}

# ---------------------------------------------------------------- 拼音（azb → 安装包）
# 同类文件搜索器普遍会做的一件事：打首字母就能找到中文名。做法是给文件夹名和已入库文档名
# 各存一份「全拼」和「首字母」，查询时打字母就同时查这两列。
# 全盘 157 万个文件名不参与：既没必要（平时要找的是文件夹和文档），也扛不住
# （11 万条要十几秒，157 万就是几分钟，还要多占一倍存储）。
_PY_MEMO = {}
_PY_LOCK = threading.Lock()
PY_READY = {'state': 'idle', 'n': 0, 'total': 0}   # 老库回填进度（首次启动时在后台补）


def py_keys(name):
    """名字 → (全拼, 首字母)，只含小写字母和数字。算不出来返回 ('', '')。

    '产品迭代周报' → ('chanpindiedaizhoubao', 'cpddzb')
    '2026 Q3' → ('2026q3', '2026q3')

    中间的空格和分隔符全部去掉：用户打首字母不会去打空格，
    '产品 迭代' 的 'cpdd' 必须能匹配上。"""
    key = str(name or '')
    with _PY_LOCK:
        got = _PY_MEMO.get(key)
    if got is not None:
        return got
    try:
        from pypinyin import lazy_pinyin, Style
        src = key.lower()
        # errors 决定非汉字怎么走：英文数字原样留着（再小写），
        # 于是 'Q3-周报' 不会因为夹了个英文就整串算不出来。
        full = ''.join(lazy_pinyin(src, errors=lambda x: list(x)))
        ini = ''.join(lazy_pinyin(src, style=Style.FIRST_LETTER, errors=lambda x: list(x)))
        out = (re.sub(r'[^a-z0-9]', '', full)[:180], re.sub(r'[^a-z0-9]', '', ini)[:90])
    except Exception:
        out = ('', '')          # 没装 pypinyin 时静默降级，拼音功能不参与
    with _PY_LOCK:
        if len(_PY_MEMO) > 20000:      # 同名目录很多，缓存够用就行，别无限长大
            _PY_MEMO.clear()
        _PY_MEMO[key] = out
    return out


def _is_letters(q):
    """纯 ascii 字母/数字才走拼音：中文查询自己就能匹配名字，不需要再翻一遍。"""
    return bool(q) and all(('a' <= c <= 'z') or ('0' <= c <= '9') for c in q)


def backfill_pinyin():
    """给老库补拼音。11 万文件夹 + 8 千文档，十几秒。

    v6.4 之前建的索引没有 py/ini 列，init_db 只会把列补上、值是 NULL。
    这里用独立连接分批 UPDATE（每批 500 条、立刻 commit）：WAL 下读不会被挡，
    搜索照常出结果，只是补到哪一行哪一行才参与拼音匹配。
    单独开连接、不碰全局 conn()，免得和 HTTP 线程抢同一把 con_lock。"""
    global PY_READY
    if PY_READY['state'] == 'busy':
        return 0
    if not py_keys('测试')[1]:
        PY_READY = {'state': 'nopinyin', 'n': 0, 'total': 0}   # 没装 pypinyin，直接放弃
        return 0
    PY_READY = {'state': 'busy', 'n': 0, 'total': 0}
    done = 0
    try:
        con = sqlite3.connect(DB, check_same_thread=False)
        con.execute('PRAGMA journal_mode=WAL')
        for tbl in ('dirs', 'meta'):     # 这个函数可能先于 init_db 被调用，列自己补
            for col in ('py', 'ini'):
                try:
                    con.execute(f'ALTER TABLE {tbl} ADD COLUMN {col} TEXT')
                except sqlite3.Error:
                    pass
        con.commit()
        jobs = []
        for tbl, col in (('dirs', 'name'), ('meta', 'path')):
            try:
                left = con.execute(f'SELECT count(*) FROM {tbl} WHERE ini IS NULL').fetchone()[0]
            except sqlite3.Error:
                left = 0
            if left:
                jobs.append((tbl, col, left))
        PY_READY['total'] = sum(j[2] for j in jobs)
        for tbl, col, _left in jobs:
            while True:
                try:
                    rows = con.execute(f'SELECT rowid, {col} FROM {tbl} '
                                       'WHERE ini IS NULL LIMIT 500').fetchall()
                except sqlite3.Error:
                    break
                if not rows:
                    break
                upd = []
                for rid, val in rows:
                    nm = val if tbl == 'dirs' else os.path.basename(str(val).rstrip('\\/'))
                    py, ini = py_keys(nm or '')
                    upd.append((py, ini, rid))
                try:
                    con.executemany(f'UPDATE {tbl} SET py=?, ini=? WHERE rowid=?', upd)
                    con.commit()
                except sqlite3.Error:
                    break
                done += len(upd)
                PY_READY['n'] = done
            try:    # 补完一表建索引；建了才走得了索引查询
                con.execute(f'CREATE INDEX IF NOT EXISTS idx_{tbl}_ini ON {tbl}(ini)')
                con.execute(f'CREATE INDEX IF NOT EXISTS idx_{tbl}_py ON {tbl}(py)')
                con.commit()
            except sqlite3.Error:
                pass
        con.close()
        PY_READY = {'state': 'done', 'n': done, 'total': done}
        if done:
            print(f'拼音回填完成：{done} 条')
    except Exception as e:
        PY_READY = {'state': 'error', 'n': done, 'total': PY_READY.get('total', 0)}
        print('拼音回填失败:', e)
    return done


def start_pinyin_backfill():
    """后台补拼音，启动时不挡路。已经补过就直接返回。"""
    t = threading.Thread(target=backfill_pinyin, daemon=True)
    t.start()
    return t

def discover_scan(targets=None, home=None):
    """发现模式：走一遍全盘（或指定根），只记文件名，按顶层候选目录归组统计文档数，
    并做噪音预判。小说目录零接触（连候选清单里都不出现）。不读任何文件内容。"""
    global DISCOVER
    try:
        if DISCOVER['state'] == 'busy':
            return
        DISCOVER = {'state': 'busy', 'groups': [], 'err': ''}
        home = str(Path.home() if home is None else home)
        roots = [str(t) for t in targets] if targets is not None else scan_docs.drive_targets()
        _prog(state='busy', phase='discover', done=0, total=len(roots), ok=0, fail=0,
              msg='智能扫描中（只记文件名，不读内容）', k='m_discovering', err='')
        groups, docs_total, novel_dirs = {}, 0, 0
        for i, root in enumerate(roots):
            _prog(done=i, msg=f'智能扫描 {root}', k='m_discover_target', a={'p': root})
            for cur, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d.lower() not in scan_docs.SKIP_DIRS
                           and not d.startswith('.')]
                if scan_docs.is_novel_dir(cur):
                    dirs[:] = []
                    novel_dirs += 1
                    continue
                for f in files:
                    if os.path.splitext(f)[1].lower() in scan_docs.DOCS:
                        key, noise = _group_key(os.path.join(cur, f), home)
                        g = groups.setdefault(key.lower(),
                                              {'dir': key, 'docs': 0, 'noise': noise})
                        g['docs'] += 1
                        docs_total += 1
        lst = sorted(groups.values(), key=lambda g: -g['docs'])
        DISCOVER = {'state': 'ready', 'groups': lst, 'err': '', 'novel_dirs': novel_dirs,
                    'docs_total': docs_total}
        _prog(state='done', phase='', msg=f'智能扫描完成：{docs_total} 个文档',
              k='m_discover_done', a={'n': docs_total})
        print(f'智能扫描完成：{docs_total} 个文档，{len(lst)} 个候选目录（小说目录跳过 {novel_dirs} 个）')
    except Exception as e:
        import traceback
        traceback.print_exc()
        DISCOVER = {'state': 'error', 'groups': [], 'err': f'{type(e).__name__}: {e}'}

def _noise_dir(path: str):
    low = path.lower()
    return any(w in low for w in NOISE_WORDS)

def _group_key(path: str, home: str):
    """把文档路径归到顶层候选目录：主目录下取二级目录（桌面/文档/下载等），
    其他盘取根下一级目录。返回 (候选目录, 是否噪音)"""
    if path.lower().startswith(home.lower()):
        rest = path[len(home):].lstrip('\\/')
        seg = rest.split('\\')[0] if rest else ''
        d = os.path.join(home, seg) if seg else home
    else:
        drive = path[:3]                       # 'D:\\'
        rest = path[3:]
        seg = rest.split('\\')[0] if rest else ''
        d = drive + seg if seg else drive.rstrip('\\/')
    return d, _noise_dir(d)

# 全盘文件名索引的构建状态（/api/names 轮询用）
NAMES = {'state': 'idle', 'err': '', 'n': 0}

def _real_case(path):
    """把路径各级还原成磁盘上的真实大小写。

    scope.json 里的 include_dirs 是人手写进去的（`d:\\my docs\\reports\\...`），
    Windows 不区分大小写所以扫描照常工作，但这串小写会被原样存进文件名索引，
    快搜窗里显示出来就像是拼错了。逐级 os.listdir 对上原名即可 —— 一个根路径
    也就几层目录，比起扫一遍全盘的开销可以忽略。"""
    if not path:
        return path
    p = os.path.normpath(str(path))
    drive, rest = os.path.splitdrive(p)
    if not drive:
        return p
    drive = drive[0].upper() + drive[1:]     # 盘符统一大写（'d:' → 'D:'）
    cur = drive + os.sep
    for seg in [s for s in rest.split(os.sep) if s]:
        try:
            real = next((n for n in os.listdir(cur) if n.lower() == seg.lower()), None)
        except OSError:
            return p                  # 盘没插 / 没权限：保持原样，别让整个索引挂在这
        cur = os.path.join(cur, real or seg)
    return cur


def build_files_index(targets=None):
    """走一遍全盘，把文件名写进 files 表。只记路径与文件名，不打开、不读内容。

    先建 files_new 再原子换名：中途失败或被关掉，正在用的那份索引保持完整可用，
    不会出现「搜到一半的半个磁盘」这种状态。"""
    global NAMES
    try:
        if NAMES['state'] == 'busy' or PROG.get('state') == 'busy':
            NAMES = {'state': 'error', 'err': '另一项任务正在进行', 'n': 0,
                     'k': 'e_names_busy'}
            return
        NAMES = {'state': 'busy', 'err': '', 'n': 0}
        roots = ([_real_case(t) for t in targets] if targets is not None
                 else [_real_case(t) for t in scan_docs.drive_targets()])
        _prog(state='busy', phase='names', done=0, total=len(roots) + 1, ok=0, fail=0,
              msg='建立全盘文件名索引', k='m_names')
        con = sqlite3.connect(DB, check_same_thread=False)
        con.execute('PRAGMA journal_mode=WAL')
        # synchronous=OFF 只影响这条一次性的批量写：崩了最多重扫一遍，换来成倍的写入速度
        con.execute('PRAGMA synchronous=OFF')
        con.execute('DROP TABLE IF EXISTS files_new')
        con.execute('CREATE TABLE files_new(path TEXT PRIMARY KEY, name TEXT NOT NULL, '
                    'dir TEXT, ext TEXT, size REAL, mtime REAL)')
        # 目录也单独存一份：这类工具的体验里「搜文件夹」和「搜文件」一样常用，
        # 光靠文件路径猜不出空文件夹。整体占比不到文件数的 1/4，代价可以接受。
        con.execute('DROP TABLE IF EXISTS dirs_new')
        con.execute('CREATE TABLE dirs_new(path TEXT PRIMARY KEY, name TEXT NOT NULL, '
                    'parent TEXT, py TEXT, ini TEXT)')
        con.commit()
        batch, dbatch, n = [], [], 0
        for i, root in enumerate(roots):
            _prog(done=i, msg=f'扫描文件名 {root}', k='m_names_target', a={'p': root})
            for cur, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d.lower() not in scan_docs.SKIP_DIRS
                           and not d.startswith('.')]
                if scan_docs.is_novel_dir(cur):
                    dirs[:] = []
                    continue
                base = os.path.basename(cur)
                if base:
                    py, ini = py_keys(base)     # 建的时候就填好，重建完拼音立刻可用
                    dbatch.append((cur, base.lower(), os.path.dirname(cur), py, ini))
                for f in files:
                    batch.append((os.path.join(cur, f), f.lower(), cur,
                                  os.path.splitext(f)[1].lower(), None, None))
                    if len(batch) >= 20000:
                        con.executemany('INSERT OR REPLACE INTO files_new'
                                        '(path,name,dir,ext,size,mtime) VALUES(?,?,?,?,?,?)', batch)
                        if dbatch:
                            con.executemany('INSERT OR REPLACE INTO dirs_new'
                                            '(path,name,parent,py,ini) VALUES(?,?,?,?,?)', dbatch)
                            dbatch = []
                        con.commit()
                        n += len(batch); batch = []
                        _prog(done=i, msg=f'扫描文件名 {root}（已 {n}）',
                              k='m_names_progress', a={'n': n})
            if batch:
                con.executemany('INSERT OR REPLACE INTO files_new'
                                '(path,name,dir,ext,size,mtime) VALUES(?,?,?,?,?,?)', batch)
                con.commit()
                n += len(batch); batch = []
        if dbatch:
            con.executemany('INSERT OR REPLACE INTO dirs_new'
                            '(path,name,parent,py,ini) VALUES(?,?,?,?,?)', dbatch)
            con.commit()
        _prog(done=len(roots), msg='整理文件名索引…', k='m_names_finish')
        # 换表这一下很快，但要挡住 HTTP 线程正在进行的查询，避免它们撞上「表刚被删」
        with con_lock:
            con.execute('DROP TABLE IF EXISTS files')
            con.execute('ALTER TABLE files_new RENAME TO files')
            con.execute('CREATE INDEX IF NOT EXISTS idx_files_name ON files(name)')
            con.execute('DROP TABLE IF EXISTS dirs')
            con.execute('ALTER TABLE dirs_new RENAME TO dirs')
            con.execute('CREATE INDEX IF NOT EXISTS idx_dirs_name ON dirs(name)')
            con.execute('CREATE INDEX IF NOT EXISTS idx_dirs_ini ON dirs(ini)')
            con.execute('CREATE INDEX IF NOT EXISTS idx_dirs_py ON dirs(py)')
            con.commit()
        con.close()
        NAMES = {'state': 'done', 'err': '', 'n': n}
        _prog(state='done', phase='', msg=f'文件名索引完成：{n} 个文件',
              k='m_names_done', a={'n': n})
        print(f'全盘文件名索引完成：{n} 个文件')
    except Exception as e:
        import traceback
        traceback.print_exc()
        NAMES = {'state': 'error', 'err': f'{type(e).__name__}: {e}', 'n': 0}
        _prog(state='error', phase='', msg=f'文件名索引失败：{e}', err=str(e))

# 质量自检（GUI/CLI 共用）；在独立子进程里跑，绝不碰正在服务的真实索引库
SELFTEST = {'state': 'idle', 'msg': '', 'ok': None, 'k': ''}

def run_selftest_async():
    def work():
        SELFTEST.update(state='busy', msg='质量自检进行中（检索评测/OCR 准确率/损坏文件 fuzz…）',
                        k='m_selftest_busy', ok=None)
        try:
            flags = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW
            env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
            # 打包成 exe 时 selftest 是子命令；源码运行必须显式带上 brain.py
            test_cmd = ([sys.executable, 'selftest'] if getattr(sys, 'frozen', False)
                        else [sys.executable, 'brain.py', 'selftest'])
            r = subprocess.run(test_cmd, capture_output=True, timeout=1200,
                               creationflags=flags, cwd=str(app_dir()),
                               encoding='utf-8', errors='replace', env=env)
            out = (r.stdout or '') + '\n' + (r.stderr or '')
            # 摘要优先从报告文件读（UTF-8 落盘，免疫子进程管道编码问题）
            msg, ok = '', None
            try:
                txt = (app_dir() / 'eval_report.txt').read_text(encoding='utf-8')
                ok = 'OVERALL: PASS' in txt
                summ = next((ln.strip() for ln in txt.splitlines() if '项通过' in ln), '')
                over = next((ln.strip() for ln in txt.splitlines() if 'OVERALL' in ln), '')
                msg = (summ or over)
            except Exception:
                pass
            if not msg:
                lines = out.splitlines()
                msg = next((ln.strip() for ln in lines if 'OVERALL' in ln), out[-300:])
                if ok is None:
                    ok = 'OVERALL: PASS' in out
            SELFTEST.update(state='done' if ok else 'error', msg=msg[:300], k='', ok=ok)
        except Exception as e:
            SELFTEST.update(state='error', msg=f'{type(e).__name__}: {e}', k='', ok=False)
    threading.Thread(target=work, daemon=True).start()

_ocr = None

def _get_ocr():
    """惰性加载 OCR 引擎（首次调用约 1~2 秒）"""
    global _ocr
    if _ocr is None:
        try:
            # 个别 PDF 有超长页（上亿像素），PIL 默认上限会导致该页 OCR 失败；
            # 图片全部由本机 PDF 渲染产生，非不可信来源，放心解除限制
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = None
        except Exception:
            pass
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr

def ocr_pdf(path: Path, budget=OCR_BUDGET):
    """纯扫描版 PDF：渲染前 OCR_PAGES 页逐页 OCR；失败返回 None。

    budget=单篇 OCR 的总时间预算（秒）。扫描版笔记/教材一本几十页，逐页 OCR 能吃掉
    好几分钟，一篇就能拖住整轮索引；给个预算，到点就把已识别的部分交出来——
    建搜索特征用不着整本。"""
    try:
        import pymupdf
        doc = pymupdf.open(str(path))
        try:
            if doc.needs_pass and not doc.authenticate(''):
                return None
            ocr = _get_ocr()
            parts = []
            t0 = time.time()
            for i, page in enumerate(doc):
                if i >= OCR_PAGES or time.time() - t0 > budget:
                    break
                r = page.rect
                if r.width < 1 or r.height < 1:
                    continue
                # 限制输出像素：有的扫描件是超长拼接图（几十万像素高），按固定 DPI 渲染
                # 会生成上亿像素的位图，pymupdf 在 C 层算好几分钟且**不释放 GIL**——
                # 此时连上一层的 threading.join(timeout) 都返回不了，整轮索引直接卡死
                # （实测卡 13 分钟不止）。这里把长边压到 2200px，足够 OCR 认字。
                z = min(OCR_DPI / 72, 2200 / max(r.width, r.height))
                if z < 0.05:
                    continue
                pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
                res, _ = ocr(pix.tobytes('png'))
                if res:
                    parts.append('\n'.join(line[1] for line in res))
            return '\n'.join(parts).strip() or None
        finally:
            doc.close()
    except Exception:
        return None

class _HTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, d):
        if d.strip():
            self.parts.append(d.strip())

def _decode_text(raw: bytes):
    """猜文本编码。顺序很重要：GBK 几乎能"成功"解码任意字节序列，
    所以必须先认 BOM、再严格试 UTF-8，最后才轮到 GBK/GB18030。
    原实现按 utf-8 → gbk → utf-16 排，UTF-16 文件会被 GBK 抢先解成乱码，
    utf-16 那一档永远走不到（Windows 记事本存"Unicode"就是 UTF-16，很常见）。"""
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw.decode('utf-8-sig', errors='ignore')
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        try:
            return raw.decode('utf-16', errors='ignore')
        except Exception:
            pass
    for cut in range(4):                    # 截断可能切在多字节字符中间，回退几个字节再试
        try:
            return raw[:len(raw) - cut].decode('utf-8') if cut else raw.decode('utf-8')
        except UnicodeDecodeError:
            continue
    try:
        return raw.decode('gbk')
    except UnicodeDecodeError:
        return raw.decode('gb18030', errors='replace')

_RTF_ENC = {936: 'gbk', 950: 'big5', 932: 'shift_jis', 1252: 'cp1252', 65001: 'utf-8'}
# RTF 的「目标组」：内容不是正文（字体表/颜色表/图片/文档属性…），整组丢弃。
# 不丢的话字体名、十六进制图片数据会混进正文，搜「Arial」能命中几百个不相干的文件。
_RTF_DEST = {b'fonttbl', b'colortbl', b'stylesheet', b'info', b'generator', b'pict',
             b'shppict', b'nonshppict', b'object', b'header', b'headerl', b'headerr',
             b'headerf', b'footer', b'footerl', b'footerr', b'footerf', b'footnote',
             b'annotation', b'listtable', b'listoverridetable', b'rsidtbl', b'filetbl',
             b'revtbl', b'pgptbl', b'latentstyles', b'themebegin', b'datastore',
             b'xmlnstbl', b'fldinst', b'thematicbreak', b'pn'}

def _rtf_no_groups(s: bytes) -> bytes:
    """剔除目标组（{\\*...} 和 {\\fonttbl...} 这类）。它们在 RTF 里可以嵌套，
    正则的非贪婪匹配会在第一个 } 就收手、留下一地垃圾，所以按花括号深度配平来跳。"""
    out, i, n, skip = bytearray(), 0, len(s), 0
    while i < n:
        c = s[i:i + 1]
        if c == b'\\' and i + 1 < n:            # \{ \} \\ 是转义，别当结构符号
            if not skip:
                out += s[i:i + 2]
            i += 2
            continue
        if c == b'{':
            if skip:
                skip += 1
            else:
                m = re.match(rb'\\\*|\\([a-zA-Z]+)', s[i + 1:i + 32])
                if m and (m.group(1) is None or m.group(1) in _RTF_DEST):
                    skip = 1
                else:
                    out += c
        elif c == b'}':
            if skip:
                skip -= 1
            else:
                out += c
        elif not skip:
            out += c
        i += 1
    return bytes(out)

def _rtf_text(raw: bytes) -> str:
    """RTF → 纯文本。中文在 RTF 里多半是 \\'b1\\'a3 这样按代码页转义的单字节，
    直接丢给 _decode_text 会解成乱码，所以先在字节层面还原再按 \\ansicpg 解码。"""
    m = re.search(rb'\\ansicpg(\d+)', raw[:4096])
    enc = _RTF_ENC.get(int(m.group(1)) if m else 936, 'gb18030')
    s = _rtf_no_groups(raw)
    # \uN 是 Unicode 转义（Windows 版 Word 存中文常走这条），先换占位符免得被控制字清理吃掉
    s = re.sub(rb'\\u(-?\d+)\s?\??', lambda m: b'\x01U%d\x01' % (int(m.group(1)) % 65536), s)
    s = re.sub(rb"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), s)
    s = re.sub(rb'\\par\b', b'\n', s)
    s = re.sub(rb'\\[a-zA-Z]+-?\d*\s?', b'', s)          # 其余控制字
    s = re.sub(rb'\\([\\{}])', rb'\1', s)                # 还原转义的花括号/反斜杠
    s = re.sub(rb'[{}]', b'', s)
    out = []
    for i, part in enumerate(re.split(rb'\x01U(\d+)\x01', s)):
        if i % 2:
            out.append(chr(int(part)))
        elif part:
            out.append(part.decode(enc, errors='replace'))
    return re.sub(r'[ \t]{2,}', ' ', ''.join(out))[:MAX_BODY]

_CTRL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

def _clean_text(t):
    """清掉会让整篇文档报废、或者让检索失明的字符。

    1) 孤立代理项：pymupdf 遇到字体映射损坏的 PDF 会吐出 U+D800~U+DFFF 区间里落单的
       码位，它们不是合法 Unicode，一写 JSON（子进程回传）或存进 SQLite 就抛
       UnicodeEncodeError —— 表现为「解析失败」，整篇正文白读了。替换成 U+FFFD。
    2) NUL 等控制字符：SQLite 的 LIKE 是按 C 字符串实现的，**遇到 \\x00 就截断**，
       于是短词检索（1~2 个字走 LIKE）看不见 NUL 之后的所有内容。实测库里 24 篇 PDF
       正文带 NUL，那些文档的后半截等于搜不到。换成空格，位置信息也还保留着。
       （保留 \\t \\n \\r）
    """
    if not t:
        return t
    try:
        t.encode('utf-8')
    except UnicodeEncodeError:
        t = t.encode('utf-8', 'replace').decode('utf-8')
    if '\x00' in t or _CTRL.search(t):
        t = _CTRL.sub(' ', t)
    return t


def extract_text(path: Path):
    """对外统一入口：先按格式提取，再净化一次（见 _clean_text）。"""
    return _clean_text(_extract_text_impl(path))


def _extract_text_impl(path: Path):
    ext = path.suffix.lower()
    if ext == '.rtf':
        try:
            with open(path, 'rb') as f:
                raw = f.read(MAX_BODY * 6)   # RTF 的控制字比正文还多，多留点余量
        except OSError:
            return None
        try:
            return _rtf_text(raw) or None
        except Exception:
            return None
    if ext in ('.txt', '.md'):
        # 只读前几 MB：read_bytes() 会把整个文件读进内存，遇上几百 MB 的密码字典 /
        # 游戏数据 / 日志会把内存打满并卡住整轮索引（实测 244MB 的 txt 直接让进度停滞）
        with open(path, 'rb') as f:
            raw = f.read(MAX_BODY * 3)
        return _decode_text(raw)
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
            r = PdfReader(str(path))
            # 只读前 20 页：扫描版教材/笔记每页都要解码大图，60 页能跑上好几分钟，
            # 而判断「有没有文本层」20 页足够了
            text = '\n'.join((p.extract_text() or '') for p in r.pages[:20])[:MAX_BODY]
        except Exception:
            text = None
        if not (text and len(text.strip()) >= 100):
            # 提不出字或几乎没字 → 纯扫描件，OCR 兜底；OCR 成功则与已有文字合并
            ocr_t = ocr_pdf(path)
            if ocr_t:
                return ((text or '') + '\n' + ocr_t).strip()[:MAX_BODY]
        return text
    if ext in ('.docx', '.pptx', '.xlsx', '.epub'):
        try:
            texts = []
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    low = n.lower()
                    if ext == '.docx':
                        keep = low == 'word/document.xml'
                    elif ext == '.pptx':
                        keep = low.startswith(('ppt/slides/slide', 'ppt/notesslides/'))
                    elif ext == '.xlsx':
                        # 单元格文本多数进 sharedStrings；用内联字符串(inlineStr)存的则只在 sheet 里
                        keep = low == 'xl/sharedstrings.xml' or low.startswith('xl/worksheets/sheet')
                    else:  # .epub：正文是 .xhtml，之前只认 .xml 导致一个文件都读不到
                        keep = low.endswith(('.xhtml', '.html', '.htm', '.xml'))
                    if not keep:
                        continue
                    xml = z.read(n).decode('utf-8', errors='replace')[:MAX_BODY]
                    texts.append(re.sub(r'<[^>]+>', ' ', xml))
            return ' '.join(texts)[:MAX_BODY] or None
        except Exception:
            return None
    if ext in ('.html', '.htm'):
        try:
            with open(path, 'rb') as f:          # 同上：只读前几 MB，别把整份文件读进内存
                raw = f.read(MAX_BODY * 3)
            hp = _HTML()
            hp.feed(_decode_text(raw)[:MAX_BODY])
            return ' '.join(hp.parts)[:MAX_BODY] or None
        except Exception:
            return None
    return None

EXTRACT_TIMEOUT = 30         # 单个 PDF 的解析上限（秒）；要给 OCR 预算留出余量

def _extract_cmd(path_str, out_path):
    """构造「提取子进程」的命令行；源码运行和打包成 exe 都适用"""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '_extract', path_str, out_path]
    return [sys.executable, str(Path(__file__).resolve()), '_extract', path_str, out_path]

_EXTRACT_TMP = None

def _extract_tmp() -> Path:
    """子进程回传结果的临时文件，全进程复用一个；退出时删掉。
    一进一出，不在用户目录里留一地 .extract-*.json。"""
    global _EXTRACT_TMP
    if _EXTRACT_TMP is None:
        _EXTRACT_TMP = app_dir() / f'.extract-{os.getpid()}.json'
        import atexit
        atexit.register(lambda: _EXTRACT_TMP.unlink(missing_ok=True))
    return _EXTRACT_TMP

def extract_text_safe(path: Path):
    """提取文本，返回 (text, reason)。

    **PDF 一律放到子进程里解析**。pypdf / pymupdf / onnxruntime 这些 C 扩展在某些
    文件上会长时间持着 GIL（实测：超长扫描图渲染、onnxruntime 文本检测），此时连
    threading.join(timeout) 都返回不了，线程超时方案形同虚设——整轮索引会卡死十几
    分钟不动。子进程可以被真正 kill，是目前唯一可靠的中断手段。

    结果用**临时文件**回传，不走 stdout 管道：管道一旦被子进程留着不关，
    communicate() 收尾时 join 读取线程会永久阻塞，连超时都救不回来（实测卡 5 分钟）。
    这里母进程只做纯 p.wait(timeout)，kill 之后立刻返回。

    临时文件**全进程复用同一个**：一个文件建一次、退出删一次。原来每篇一个文件，
    几百篇下来会攒一堆（还会被同步盘/安全软件当成异常批量删除反复拦），既脏又慢。
    复用就得防「读到上一篇的残留」——子进程把请求路径一起写进 JSON，父进程核对，
    对不上就当作失败，绝不把别人的正文算到这篇头上。

    其余格式（txt/md/rtf/docx/pptx/xlsx/epub/html）都在毫秒级，直接在进程内做，
    省掉每次启动进程的开销。"""
    if path.suffix.lower() == '.pdf':
        tmp = _extract_tmp()
        flags = 0x08000000 if os.name == 'nt' else 0    # CREATE_NO_WINDOW
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        try:
            proc = subprocess.Popen(_extract_cmd(str(path), str(tmp)),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=flags, env=env)
        except Exception as e:
            return None, f'解析异常（{type(e).__name__}）'
        try:
            proc.wait(timeout=EXTRACT_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            return None, f'解析超时（>{EXTRACT_TIMEOUT}s）'
        try:
            data = json.loads(tmp.read_text(encoding='utf-8'))
        except Exception:
            return None, '解析失败'
        if str(data.get('p') or '') != str(path):     # 复用的临时文件，残留不能当真
            return None, '解析失败'
        return data.get('t'), data.get('w')

    try:
        return extract_text(path), None
    except Exception as e:
        return None, f'解析异常（{type(e).__name__}）'

def _doc_rids(con):
    """path → rowid 映射。

    docs 是 FTS5 虚表，path 又是 UNINDEXED 列，所以任何 `WHERE path=?` 的删除/更新
    都会退化成整表扫描：实测 3.4GB 的库上删一行要 1.4 秒，而按 rowid 删是 1 毫秒。
    重索引上千篇文件就白扔二十多分钟。先花一次（约 1.5 秒）把映射取到内存，
    之后全按 rowid 操作。只读 path 列，不用把正文也读出来。"""
    return {p: r for r, p in con.execute('SELECT rowid, path FROM docs')}

def _doc_del(con, rids, path):
    """按路径删索引行（走 rowid，必要时才回退到慢路径）"""
    rid = rids.pop(path, None)
    if rid is not None:
        con.execute('DELETE FROM docs WHERE rowid=?', (rid,))
    else:
        con.execute('DELETE FROM docs WHERE path=?', (path,))

def init_db():
    # check_same_thread=False：HTTP 层共用一条连接，由 con_lock 保证不并发使用
    con = sqlite3.connect(DB, check_same_thread=False)
    con.execute('PRAGMA journal_mode=WAL')   # 读写并发：建索引时搜索不再被锁死
    con.execute("CREATE TABLE IF NOT EXISTS meta(path TEXT PRIMARY KEY, indexed_at REAL)")
    for col, decl in (('size', 'REAL'), ('mtime', 'REAL'), ('reason', 'TEXT')):
        try:
            con.execute(f'ALTER TABLE meta ADD COLUMN {col} {decl}')
        except Exception:
            pass  # 列已存在
    con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
                   path UNINDEXED, ext UNINDEXED, body, tokenize='trigram')""")
    # 全盘文件名索引：只存路径/名字/大小/时间，不读内容。让「文件名命中」这栏
    # 覆盖整个磁盘，而不是只覆盖已入库的几千篇——这正是原先要去问 Everything 的那件事。
    # name 存小写，查询时把关键词也转小写，这样 instr 只用一次 lower()。
    con.execute("""CREATE TABLE IF NOT EXISTS files(
                   path TEXT PRIMARY KEY, name TEXT NOT NULL, dir TEXT,
                   ext TEXT, size REAL, mtime REAL)""")
    try:
        con.execute('CREATE INDEX IF NOT EXISTS idx_files_name ON files(name)')
    except sqlite3.Error:
        pass
    # 打开记录。单独一张小表，不往 files 里加列：files 有 157 万行，
    # 每打开一个文件就 UPDATE 它一次，会把索引页写脏，还会让全盘重建变慢。
    # 这里只有几百行，读写都是微秒级。
    con.execute("""CREATE TABLE IF NOT EXISTS recent(
                   path TEXT PRIMARY KEY, name TEXT, isdir INTEGER DEFAULT 0,
                   hits INTEGER DEFAULT 1, last REAL)""")
    try:
        con.execute('CREATE INDEX IF NOT EXISTS idx_recent_last ON recent(last DESC)')
    except sqlite3.Error:
        pass
    # 拼音列（v6.5）：dirs 和 meta 各加两份。老库在这里补列，回填交给
    # backfill_pinyin() 在后台线程慢慢走 —— 11 万条要十几秒，不能挡住启动。
    for tbl in ('dirs', 'meta'):
        for col in ('py', 'ini'):
            try:
                con.execute(f'ALTER TABLE {tbl} ADD COLUMN {col} TEXT')
            except sqlite3.Error:
                pass                    # 列已存在，或者这张表还没建
    return con

def _count_docs(con):
    """库内文档数。

    **别用 `count(*) FROM docs`**：docs 是 FTS5 虚表，count 会把每一行（含整段正文）
    都物化出来，2.4 GB 的库实测 288ms —— 而页面每次加载都要这个数字。meta 的
    `reason IS NULL` 恰好就是成功入库的集合（索引和 prune 同步维护），同样的事 0.6ms。"""
    try:
        n = con.execute('SELECT count(*) FROM meta WHERE reason IS NULL').fetchone()[0]
        if n:
            return n
    except sqlite3.Error:
        pass
    return con.execute('SELECT count(*) FROM docs').fetchone()[0]


def _fail_reason(path: Path):
    """给无文本文件归因：空文件 / 加密 / 损坏 / 无文本 / 格式不支持"""
    try:
        sz = path.stat().st_size
        if sz == 0:
            return '空文件'
    except OSError:
        return '文件消失'
    ext = path.suffix.lower()
    # 这些是 2007 之前的二进制 Office / 电子书格式，纯 Python 读不动。归因写清楚，
    # 免得和「扫描件提不出字」混在一起看不出问题（改存 docx/xlsx 就能进索引）。
    if ext in ('.doc', '.xls', '.ppt', '.wps', '.et', '.dps', '.mobi', '.azw3'):
        return f'{ext[1:]} 旧版格式，需另存为 docx/xlsx/pptx'
    if ext == '.pdf':
        # 只对较小的 PDF 做归因。pymupdf.open() 打开病态大文件同样会卡死，而这里是
        # 在主线程里同步执行的，没有任何超时手段——为了一个归因标签不值得再冒一次险。
        if sz > 3 * 1024 * 1024:
            return '无文本'
        try:
            import pymupdf
        except Exception:
            pymupdf = None
        if pymupdf is None:
            # core 版没打包 pymupdf。退回 pypdf：它同样能分辨「加密」和「损坏」，
            # 只是不能像 pymupdf 那样先试空密码。少了这一步，加密件会被记成
            # reason=None，既不算成功也不算失败，统计里凭空消失。
            try:
                from pypdf import PdfReader
                r = PdfReader(str(path))
            except Exception:
                return '损坏'
            return '加密' if r.is_encrypted else '无文本'
        try:
            d = pymupdf.open(str(path))
        except Exception:
            return '损坏'
        try:
            if d.needs_pass and not d.authenticate(''):
                return '加密'
            return '无文本'
        finally:
            d.close()
    return '无文本'

def index(scope_path):
    try:
        scope = json.loads(Path(scope_path).read_text(encoding='utf-8'))
    except Exception as e:
        print(f'[!] scope 配置文件读不了：{e}\n'
              f'    请检查格式（对照 scope.example.json；注意路径要写成双反斜杠）')
        sys.exit(1)
    if 'scan_csv' not in scope or 'include_dirs' not in scope:
        print('[!] scope.json 缺少 scan_csv 或 include_dirs 字段（对照 scope.example.json）')
        sys.exit(1)
    csvp = Path(scope['scan_csv'])
    if not csvp.is_absolute():
        csvp = app_dir() / csvp  # exe 场景：相对路径一律相对 exe 所在目录
    if not csvp.exists():
        print(f'[!] 找不到扫描清单 {csvp} —— 先运行全盘扫描（update 命令会自动做）')
        sys.exit(1)
    # 路径比较前必须统一分隔符和大小写：scan_docs 写出的清单长这样
    # C:/Users/Public/Documents/Library\Q2.md（盘符用 /、后面用 \），
    # 而 include_dirs 是全反斜杠。直接 startswith 的话整批都会被判成「范围外」，
    # 一篇都索引不进去，界面上还只显示「跳过 N 篇」，极难看出来。
    include = tuple(os.path.normcase(os.path.normpath(s)) for s in scope['include_dirs'])
    exclude = tuple(os.path.normcase(os.path.normpath(s)) for s in scope.get('exclude_dirs', []))
    # 统一成 Windows 标准写法再入库：清单里是 C:/a\b 这种混排，不归一化就会和库里
    # 已有的 C:\a\b 被当成两个文件，同一篇索引两遍（meta 的 path 是主键，重复了也不会报错）
    rows = [(os.path.normpath(r[0]), *r[1:])
            for r in list(csv.reader(open(csvp, encoding='utf-8-sig')))[1:] if r]
    con = init_db()
    # 自愈：历史上有些 meta 条目 reason 为 NULL（记成「已索引成功」）却根本没有正文行——
    # 旧版不支持的格式、库表迁移、或旧代码在插入正文前就写 meta 都会留下这种残迹。
    # 它们永远命中 _needs() 的「不必重建」分支，于是永久隐身：文件明明在，就是搜不到。
    # 删掉这些 meta 行，让本轮按正常流程重解一遍并写上真实原因。
    try:
        fixed = con.execute('DELETE FROM meta WHERE reason IS NULL AND path NOT IN '
                            '(SELECT path FROM docs)').rowcount
        if fixed:
            con.commit()
            print(f'  自愈 {fixed} 条「标为成功但无正文」的历史记录，本轮重新解析', flush=True)
    except Exception:
        pass
    meta_info = {r[0]: (r[1], r[2], r[3]) for r in
                 con.execute('SELECT path, indexed_at, size, mtime FROM meta')}
    rids = _doc_rids(con)

    def _needs(p, pl, dry=False):
        """ True=需要(重新)索引：范围外 False；新文件 True；
        已索引但大小+修改时间都没变 False；旧库条目补登记后 False；索引后改过的 True
        dry=True 时只判断不写库（用于预算 todo，避免预计算把待办偷偷改掉、进度永远到不了 100%）"""
        pl = os.path.normcase(os.path.normpath(pl))
        if not any(pl.startswith(d) for d in include) or any(x in pl for x in exclude):
            return False
        if p not in meta_info:
            return True
        try:
            st = os.stat(p)
        except OSError:
            return False
        at, sz, mt = meta_info[p]
        if sz is not None and mt is not None:
            return not (abs(st.st_size - sz) < 1 and abs(st.st_mtime - mt) < 2)
        if st.st_mtime <= (at or 0) + 60:      # 旧库条目：补记 size/mtime，不重建
            if not dry:
                con.execute('UPDATE meta SET size=?, mtime=? WHERE path=?',
                            (st.st_size, st.st_mtime, p))
                meta_info[p] = (at, st.st_size, st.st_mtime)
            return False
        return True                             # 索引之后文件改过 → 重建该条

    todo = sum(1 for p, _sz in rows if _needs(p, p.lower(), dry=True))
    # 预算阶段不写库，正式跑第一遍时这些旧条目会被补登记（返回 False、不算失败），
    # 所以进度用「已处理条数」而不是「成功+失败」来推进。
    n_new = n_skip = n_fail = n_done = 0
    _prog(state='busy', phase='index', done=0, total=todo, ok=0, fail=0, err='',
          msg='建立索引中', k='m_indexing', a={'n': todo})
    t0 = time.time()
    for p, _sz in rows:
        if not _needs(p, p.lower()):
            n_skip += 1
            continue
        if Path(p).name.startswith('~$'):  # Office 打开文档时的临时锁文件，不是真文档
            con.execute('INSERT OR REPLACE INTO meta(path, indexed_at, reason) VALUES(?,?,?)',
                        (p, time.time(), '临时文件'))
            n_skip += 1
            continue
        try:
            st = os.stat(p)
        except OSError:
            n_skip += 1
            continue
        if p.lower().endswith('.pdf') and st.st_size > PDF_MAX_SIZE:
            # 几百 MB 的 PDF 基本是整本书的图像扫描/图集，解析要几分钟且几乎提不出文本
            n_fail += 1
            n_done += 1
            con.execute('INSERT OR REPLACE INTO meta(path, indexed_at, size, mtime, reason) '
                        'VALUES(?,?,?,?,?)',
                        (p, time.time(), st.st_size, st.st_mtime, '文件过大（PDF）'))
            continue
        text, why = extract_text_safe(Path(p))
        if text and not why:
            # 清洗孤立代理字符（个别破损 PDF 会抽出 \ud800-\udfff，SQLite/FTS 拒收）
            text = text.encode('utf-8', 'ignore').decode('utf-8')
        n_done += 1
        if not text or not text.strip():
            n_fail += 1
            _prog(done=n_done, ok=n_new, fail=n_fail)
            # 超时/异常的别再调 _fail_reason 二次打开文件（那本身也可能是卡点）
            con.execute('INSERT OR REPLACE INTO meta(path, indexed_at, size, mtime, reason) '
                        'VALUES(?,?,?,?,?)',
                        (p, time.time(), st.st_size, st.st_mtime, why or _fail_reason(Path(p))))
        else:
            _doc_del(con, rids, p)      # 变更重建时先清旧行（按 rowid，别用慢的 path 扫描）
            cur = con.execute('INSERT INTO docs VALUES(?,?,?)',
                              (p, Path(p).suffix.lower(), text))
            rids[p] = cur.lastrowid
            con.execute('INSERT OR REPLACE INTO meta(path, indexed_at, size, mtime, reason) '
                        'VALUES(?,?,?,?,?)',
                        (p, time.time(), st.st_size, st.st_mtime, None))
            n_new += 1
            _prog(done=n_done, ok=n_new, fail=n_fail)
        # 失败项也要定期落盘。原来只有「成功满 200 篇」才 commit，而扫描件是成片失败的，
        # 于是事务一直悬着：外部只读连接看不到任何写入，看上去像卡死，中途被杀还全白跑。
        if n_done % 20 == 0 or n_done == todo:
            con.commit()
        if n_done % 50 == 0 or n_done == todo:
            el = time.time() - t0
            rate = n_done / el if el > 0 else 0
            eta = (todo - n_done) / rate if rate > 0 else 0
            print(f'  ...{n_done}/{todo} 已处理（成功 {n_new}、失败 {n_fail}）, {el:.0f}s, '
                  f'{rate:.1f}篇/s, 预计还需 {eta:.0f}s', flush=True)
    con.commit()
    try:
        con.execute("INSERT INTO docs(docs) VALUES('optimize')")  # 收紧 FTS 索引，查询更快
        con.commit()
    except Exception:
        pass
    try:
        # 索引期间 WAL 会一路长到接近 1GB（实测 895MB），结束时收回去
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except Exception:
        pass
    total = _count_docs(con)
    _prog(state='done', phase='', done=n_done, total=todo, ok=n_new, fail=n_fail,
          msg=f'完成，库内共 {total} 篇', k='m_index_done', a={'n': f'{total:,}'})
    print(f'完成：新索引 {n_new}，空/失败 {n_fail}，范围外/重复跳过 {n_skip}；库内共 {total} 篇')
    # 归因汇总：一眼看出失败是「该补 OCR」还是「该转格式」，不用去翻数据库
    try:
        why = con.execute("SELECT COALESCE(reason,'?'), COUNT(*) FROM meta "
                          "WHERE reason IS NOT NULL GROUP BY 1 ORDER BY 2 DESC").fetchall()
        if why:
            print('未入库归因：' + '、'.join(f'{r} {n}' for r, n in why))
    except Exception:
        pass

SNIP_OPEN, SNIP_CLOSE = '\x01', '\x02'   # snippet 高亮标记用不可见控制字符

def _fts_tokens(q):
    return [t for t in re.split(r'\s+', str(q).strip()) if t]

def _fts_query(q):
    """把用户输入转成安全的 FTS5 查询：每个词整体加双引号，词间 AND。
    否则 C++ / node.js / a-b / 带引号括号的输入会被当成 FTS 语法直接报错。"""
    toks = _fts_tokens(q)
    if not toks:
        return ''
    return ' AND '.join('"' + t.replace('"', '""') + '"' for t in toks)

def _need_like(q):
    """trigram 索引至少要 3 个字符才命中：任一词短于 3 就走 LIKE 降级"""
    toks = _fts_tokens(q)
    return (not toks) or any(len(t) < 3 for t in toks)

def _ext_clause(ext):
    """按扩展名精确过滤（表里 ext 存的就是带点小写形式）。
    原来用 path LIKE '%.pdf%' 会把 report.pdf.bak、以及路径里含 .pdf 的文件夹也算进来。"""
    e = str(ext or '').strip().lower().lstrip('.')
    return (' AND ext = ?', [f'.{e}']) if e else ('', [])

def _like_esc(t):
    """把 LIKE 的通配符转义掉。

    `_` 和 `%` 在 LIKE 里是通配符：搜「_」会匹配任何一个字符、搜「a_」会匹配所有
    含 a 的文档——等于把单字查询变成全库 dump。转义后才是字面匹配。"""
    return t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _search_sql(q, ext=None, pf=None):
    """返回 (sql, args)，网页与命令行共用同一套检索逻辑"""
    extra, args = '', []
    clause, eargs = _ext_clause(ext)
    extra += clause
    args += eargs
    if pf:
        # 只能用 instr，不能用 path LIKE：docs 是 trigram 分词的 FTS5 虚表，LIKE 会被
        # FTS5 的 trigram 优化接管，而 path 是 UNINDEXED 列、没有三元组可查，
        # 于是「3 个字符以上的路径过滤」会静默返回 0 条（2 个字符走不到优化，反而正常）。
        extra += ' AND instr(lower(path), lower(?)) > 0'
        args.append(pf)
    toks = _fts_tokens(q)
    if not _need_like(q):
        sql = ("SELECT path, snippet(docs,2,char(1),char(2),'…',10) FROM docs "
               "WHERE docs MATCH ?" + extra + " ORDER BY rank LIMIT ?")
        return sql, [_fts_query(q), *args]
    # 短词（含 1~2 字中文、AI、C 这类）：trigram 命中不了，降级 LIKE。
    # 预览定位到第一个词的命中位置，而不是正文开头。
    like = ' AND '.join(["body LIKE ? ESCAPE '\\'"] * len(toks)) or '1=1'
    sql = ("SELECT path, substr(body, max(1, instr(body, ?) - 30), 90) FROM docs "
           "WHERE " + like + extra + " LIMIT ?")
    return sql, [toks[0] if toks else '', *[f'%{_like_esc(t)}%' for t in toks], *args]

def _mark_name(p, toks):
    """给「文件名命中」的结果做片段：取文件名，把命中的词打上高亮标记。
    标记用控制字符而不是 <b>，前端统一交给 hl() 处理，避免双重转义。"""
    name = os.path.basename(p)
    low, spans = name.lower(), []
    for t in toks:
        tl, start = t.lower(), 0
        while tl:
            i = low.find(tl, start)
            if i < 0:
                break
            spans.append((i, i + len(tl)))
            start = i + len(tl)
    if not spans:
        return name                      # 命中的是路径里的文件夹名，文件名本身没有，原样返回
    spans.sort()
    out, pos = [], 0
    for a, b in spans:
        if b <= pos:
            continue
        a = max(a, pos)
        out.append(name[pos:a])
        out.append(SNIP_OPEN + name[a:b] + SNIP_CLOSE)
        pos = b
    out.append(name[pos:])
    return ''.join(out)

def name_rows(con, q, limit=15):
    """只按「文件名 / 目录名」找，不碰正文。
    和 search_rows 里那段补充检索是同一套 SQL，但它的用途相反：
    那边是「正文命中不够时垫底」，这里是给界面单独开一栏，让文件名命中不被正文结果挤掉。"""
    toks = _fts_tokens(q)
    if not toks:
        return []
    sql = ('SELECT path FROM meta WHERE reason IS NULL AND '
           + ' AND '.join(['instr(lower(path), lower(?)) > 0'] * len(toks))
           + ' LIMIT ?')
    return [r[0] for r in con.execute(sql, (*toks, limit))]

def file_rows(con, q, limit=12):
    """全盘文件名检索（自建索引，不依赖 Everything）。

    走 instr 全表扫：157 万行、命中够 6 条就提前收工，常见的词 20~40ms。
    不按路径长度排序——那要先把所有命中都捞出来再排，稀见词会从 20ms 变成 600ms，
    而 6 个名额本来就只是「先告诉你有没有」。"""
    q = (q or '').strip().lower()
    if not q:
        return []
    try:
        return [r[0] for r in con.execute(
            'SELECT path FROM files WHERE instr(name, ?) > 0 LIMIT ?', (q, limit))]
    except sqlite3.Error:
        return []

def file_count(con):
    """全盘文件名索引里的条目数；0 表示还没建过"""
    try:
        return con.execute('SELECT count(*) FROM files').fetchone()[0]
    except sqlite3.Error:
        return 0


# ---------------------------------------------------------------- 快搜（双击 Ctrl 弹出的迷你窗）
# 目标是把「敲两个字就出结果」做到无感：前缀查询走 name 索引（157 万行 0.2ms），
# 「包含」查询只能全表扫（最坏 500ms），所以拆成两趟，第二趟由前端判断要不要发。
MINI_CACHE = {}                   # 关键词 -> (时间, 结果)：深搜一次 500ms，退格重搜不该再等
MINI_LOCK = threading.Lock()
MINI_TTL = 180.0
MINI_CACHE_MAX = 400
_SEP = ' .-_—~()[]{}【】（）、，'


def _has_files(con):
    """文件名索引里有没有东西。不能问 count(*)：157 万行的全表统计要 58ms，
    而这里只想知道「表空不空」—— 取一行就够，微秒级。"""
    try:
        return con.execute('SELECT 1 FROM files LIMIT 1').fetchone() is not None
    except sqlite3.Error:
        return False


def _mini_rank(name, q):
    """0 前缀 / 1 词边界 / 2 名字中间 / 3 完全不像。越小的越像用户想要的那个。"""
    i = name.find(q)
    if i < 0:
        return 3
    if i == 0:
        return 0
    return 1 if name[i - 1] in _SEP else 2


def record_open(con, path, isdir=False):
    """记一次打开。写失败不能影响「打开文件」本身，所以整体吞异常。

    「越用越准」就是靠这张表 —— 同一档匹配里，你常开的排前面。"""
    path = str(path or '')
    if not path:
        return
    name = os.path.basename(path.rstrip('\\/')) or path
    try:
        con.execute(
            'INSERT INTO recent(path,name,isdir,hits,last) VALUES(?,?,?,1,?) '
            'ON CONFLICT(path) DO UPDATE SET hits=hits+1, last=excluded.last',
            (path, name, 1 if isdir else 0, time.time()))
        con.commit()
    except sqlite3.Error:
        pass


def _recent_map(con):
    """path(小写) -> 最近得分。整表读进内存：几十到几百行，比逐行查库便宜得多。"""
    try:
        rows = con.execute('SELECT path, hits, last FROM recent '
                           'ORDER BY last DESC LIMIT 800').fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for i, (path, hits, _last) in enumerate(rows):
        # 最近的第 0 名权重最高；开过的次数再叠一点，免得只开过一次的旧文件挤掉常驻文件。
        out[os.path.normcase(path)] = (hits or 1) - i * 0.05
    return out


def recent_rows(con, limit=6):
    """最近打开的文件/文件夹，最近的在最前 —— 快搜窗的空状态直接铺这一列。
    如今的启动器打开就是这一屏，一个字都不用打。"""
    try:
        rows = con.execute('SELECT path,name,isdir FROM recent '
                           'ORDER BY last DESC LIMIT ?', (limit,)).fetchall()
    except sqlite3.Error:
        return []
    out, seen = [], set()
    for path, name, isdir in rows:
        key = os.path.normcase(path)
        if key in seen or not os.path.exists(path):   # 删掉的、挪走的就别占位置了
            continue
        seen.add(key)
        out.append({'n': name or os.path.basename(path.rstrip('\\/')) or path,
                    'p': path, 'd': os.path.dirname(path.rstrip('\\/')),
                    't': 'dir' if isdir else 'file',
                    'e': '' if isdir else os.path.splitext(path)[1].lower()})
    return out


def _py_hits(con, q, deep, limit=40):
    """纯字母输入时补一档拼音匹配。返回 {小写路径: (原路径, 等级, 是否目录)}，
    等级越小越准：0 首字母前缀 / 1 全拼前缀 / 2 首字母包含 / 3 全拼包含。

    前缀两档走 idx_*_ini / idx_*_py，11 万行也是毫秒级；"包含"只能全表 instr，
    所以只在 deep（用户停手 300ms）时才做。"""
    if not _is_letters(q):
        return {}
    hi = q + '\uffff'
    found = {}

    def take(tbl, isdir, sql, args, level):
        try:
            rows = con.execute(sql, args).fetchall()
        except sqlite3.Error:
            return                       # 老库还没建这张表 / 还没补拼音列
        for (p,) in rows:
            k = os.path.normcase(p)
            if k not in found or level < found[k][1]:
                found[k] = (p, level, isdir)

    for tbl, isdir in (('dirs', True), ('meta', False)):
        take(tbl, isdir, f'SELECT path FROM {tbl} WHERE ini>=? AND ini<? LIMIT ?',
             (q, hi, limit), 0)
        take(tbl, isdir, f'SELECT path FROM {tbl} WHERE py>=? AND py<? LIMIT ?',
             (q, hi, limit), 1)
        if deep:
            take(tbl, isdir, f'SELECT path FROM {tbl} WHERE instr(ini,?)>0 LIMIT ?',
                 (q, limit), 2)
            take(tbl, isdir, f'SELECT path FROM {tbl} WHERE instr(py,?)>0 LIMIT ?',
                 (q, limit), 3)
    return found


# 拼音的排序位。第一档给「整个名字的首字母正好等于关键词」——打 azb 出现「安装包」，
# 这是最准的一种命中，比一堆恰好以 AZB 开头的英文文件更该排在前面。
# 其余三档排在「名字里直接含关键词」之后，但仍然进得来。
def _py_rank(level, name, q):
    if level == 0:
        # 整个名字的首字母正好等于关键词：打 azb 得到「安装包」，这是最该排第一的命中。
        # 给负分是因为「名字以 azb 开头」的英文文件可能有几十个（游戏资源里全是 AZB_xxx），
        # 排在 0 后面就永远看不见了。
        return -0.15 if py_keys(name)[1] == q else 1.2
    return (2.4, 3.0, 3.2)[level - 1]


def _body_hits(con, q, limit=3):
    """正文命中，排在名字命中之后垫底。片段里 char(1)/char(2) 是高亮标记，
    前端换成 <mark> —— 这两家长处都不在正文，这是第二大脑唯一守得住的差异。"""
    try:
        rows = search_rows(con, q, limit=limit, with_name=False)
    except Exception:
        return []
    out = []
    for path, snip in rows:
        s = re.sub(r'\s+', ' ', str(snip or '')).strip()
        out.append({'n': os.path.basename(path) or path, 'p': path,
                    'd': os.path.dirname(path), 't': 'doc',
                    'e': os.path.splitext(path)[1].lower(), 'b': s[:110]})
    return out


def mini_search(con, q, limit=9, deep=False, body=False):
    """返回 {'items': [...], 'deep': bool, 'ms': int}。

    deep=False：中文前缀 + 拼音前缀，全部走索引，毫秒级；
    deep=True ：再加「名字任意位置包含」（全表扫）和拼音模糊 —— 用户停手后才发；
    body=True ：最后补几条正文命中（带片段），排在名字命中后面。"""
    q = (q or '').strip().lower()
    if not q:
        return {'items': [], 'deep': deep, 'ms': 0, 'n': 0}
    ck = (q, bool(body))
    if deep:
        with MINI_LOCK:
            got = MINI_CACHE.get(ck)
        if got and time.time() - got[0] < MINI_TTL:
            return {'items': [dict(x) for x in got[1][:limit]], 'deep': True, 'ms': 0,
                    'n': len(got[1]), 'cache': True}
    t0 = time.perf_counter()
    items, seen = [], set()

    def add(path, isdir, ext='', rank=None):
        key = os.path.normcase(path)
        if key in seen:
            return
        seen.add(key)
        name = os.path.basename(path.rstrip('\\/')) or path
        items.append({'n': name, 'p': path,
                      'd': os.path.dirname(path.rstrip('\\/')),
                      't': 'dir' if isdir else 'file',
                      'e': '' if isdir else ext,
                      'r': _mini_rank(name.lower(), q) if rank is None else rank})

    frows = drows = []
    # 两张表分开兜错：老库还没有 dirs 表（v6.3 之前建的索引），
    # 一个 OperationalError 不该把 files 的结果也一起吞掉。
    try:
        if deep:
            frows = con.execute('SELECT path, ext FROM files WHERE instr(name,?)>0 LIMIT ?',
                                (q, limit * 45)).fetchall()
        else:
            hi = q + '\uffff'          # BINARY 序：q 之后、q+任何常规字符之前
            frows = con.execute('SELECT path, ext FROM files WHERE name>=? AND name<? LIMIT ?',
                                (q, hi, limit * 8)).fetchall()
    except sqlite3.Error:
        frows = []
    try:
        if deep:
            drows = con.execute('SELECT path FROM dirs WHERE instr(name,?)>0 LIMIT ?',
                                (q, limit * 30)).fetchall()
        else:
            hi = q + '\uffff'
            drows = con.execute('SELECT path FROM dirs WHERE name>=? AND name<? LIMIT ?',
                                (q, hi, limit * 5)).fetchall()
    except sqlite3.Error:
        drows = []
    for path, ext in frows:
        add(path, False, ext or '')
    for (path,) in drows:
        add(path, True)
    # 拼音：azb → 安装包。和中文命中并存，同一个路径只留更准的那个位次。
    for _k, (path, level, isdir) in _py_hits(con, q, deep).items():
        add(path, isdir, os.path.splitext(path)[1].lower(),
            _py_rank(level, os.path.basename(path.rstrip('\\/')) or path, q))
    if not items and not _has_files(con):
        # 还没建过全盘索引时，至少让已入库的文档文件名可用
        for p in name_rows(con, q, limit * 3):
            add(p, False, os.path.splitext(p)[1].lower())
    # 排序分三档，和近年主流搜索器的思路一致：先看名字像不像（前缀 > 词边界 > 中间），
    # 同一档里再看你平时开不开它 —— 常开的排前面，但你打的前缀永远压过习惯。
    rec = _recent_map(con)
    items.sort(key=lambda it: (it['r'],
                               -rec.get(os.path.normcase(it['p']), 0.0),
                               len(it['n']), it['n'].lower()))
    out = items[:limit]
    if body:
        # 正文命中垫在名字命中之后。名字已经给出了答案时就不占位置了，
        # 只留出 3 行给「文件名里没有、正文里提过」的文档。
        room = max(0, min(3, limit - len(out)))
        if room:
            for it in _body_hits(con, q, room):
                if os.path.normcase(it['p']) in seen:
                    continue
                seen.add(os.path.normcase(it['p']))
                out.append(it)
    if deep:
        with MINI_LOCK:
            if len(MINI_CACHE) > MINI_CACHE_MAX:
                for k in sorted(MINI_CACHE, key=lambda k: MINI_CACHE[k][0])[:100]:
                    MINI_CACHE.pop(k, None)
            MINI_CACHE[ck] = (time.time(), [dict(x) for x in items])
    return {'items': out, 'deep': deep,
            'ms': int((time.perf_counter() - t0) * 1000), 'n': len(items)}

def search_rows(con, q, limit=20, ext=None, path_filter=None, with_name=True):
    """正文检索 + 文件名补充检索。

    docs 里 path 是 UNINDEXED 列，FTS 根本看不到文件名，于是「文件名里有、正文里没有」
    的文档搜不出来（实际用起来这正是最常见的抱怨点）。正文命中不够时再扫一遍路径：
    万级路径的 LIKE 是毫秒级，不用为它加索引列——那会逼着所有人重建整个索引。"""
    sql, args = _search_sql(q, ext, path_filter)
    hits = con.execute(sql, (*args, limit)).fetchall()
    if len(hits) >= limit or not with_name:
        return hits
    toks = _fts_tokens(q)
    if not toks:
        return hits
    # 扫「meta」而不是「docs」：docs 是 FTS5 虚表，取 path 也要把整行（含正文）读出来，
    # 2.4 GB 的库实测 280ms；meta 是普通表且有 path 主键，同一件事只要 4ms（65 倍）。
    # meta 里 reason IS NULL 就是成功入库的集合，索引与 prune 都同步维护它。
    sql2 = ('SELECT path FROM meta WHERE reason IS NULL AND '
            + ' AND '.join(['instr(lower(path), lower(?)) > 0'] * len(toks)))
    a2 = list(toks)
    e = str(ext or '').strip().lower().lstrip('.')
    if e:
        sql2 += ' AND lower(path) LIKE ?'
        a2.append(f'%.{_like_esc(e)}')
    if path_filter:
        sql2 += ' AND instr(lower(path), lower(?)) > 0'
        a2.append(path_filter)
    sql2 += ' LIMIT ?'
    a2.append(limit + len(hits))
    seen = {p for p, _ in hits}
    for (p,) in con.execute(sql2, a2):
        if p in seen:
            continue
        seen.add(p)
        hits.append((p, _mark_name(p, toks)))
        if len(hits) >= limit:
            break
    return hits

def search(q, limit=20, ext=None, path_filter=None):
    con = init_db()
    con_row = _count_docs(con)
    if con_row == 0:
        print('索引为空，先运行 index 建库')
        return
    hits = search_rows(con, q, limit, ext, path_filter)
    for p, snip in hits:
        print(f'{p}\n    {snip}'.replace('\n', ' '))
    print(f'-- {len(hits)} 条')
    return hits

# ---------------------------------------------------------------- Everything 联动（可选）
# 第二大脑只能搜「索引里那几千篇文档」的正文和文件名；Everything 知道整台机器上每个文件名。
# 两者合起来才完整：一个答「哪份文件里提过这句」，一个答「这个文件到底在哪」。
# 通道用 Everything 自带的 HTTP 服务（不需要 SDK、不需要插件，跨进程最稳）。
EV_LOCK = threading.Lock()
EV_LAST = {}                      # 路径 -> 时间戳：只允许打开本次搜索真的返回过的文件
EV_CACHE = {'port': -1, 'tried': 0.0}
EV_TTL = 300                      # 一次搜索的结果在 5 分钟内可点「打开」

def _ev_ini():
    """Everything 的配置文件（勾了 app_data 时在 %APPDATA%，否则在安装目录）"""
    out = []
    a = os.environ.get('APPDATA')
    if a:
        out.append(os.path.join(a, 'Everything', 'Everything.ini'))
    for base in (r'C:\Program Files\Everything', r'C:\Program Files (x86)\Everything'):
        out.append(os.path.join(base, 'Everything.ini'))
    return [p for p in out if os.path.exists(p)]

def _ev_get(port, qs, timeout=1.5):
    # 必须绕开系统代理：走代理时 127.0.0.1 也会被转发出去，直接连不上
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(f'http://127.0.0.1:{port}/?{qs}', timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))

def _ev_alive(port):
    """这根端口上跑的到底是不是「有货的」Everything。
    光能返回 JSON 不够——服务开着但索引是空的时候照样返回 {"totalResults":0}，
    那样界面会写着「含 Everything」却永远补不出东西。用 * 通配探一次，有结果才认。"""
    try:
        d = _ev_get(port, 'search=%2A&json=1&count=1', 0.8)
        return (isinstance(d, dict) and 'results' in d
                and int(d.get('totalResults') or 0) > 0)
    except Exception:
        return False

def ev_port():
    """探测 Everything HTTP 端口；0 = 没开或没装。结果缓存一分钟，避免每次搜索都探。"""
    now = time.time()
    with EV_LOCK:
        if now - EV_CACHE['tried'] < 60:
            return max(EV_CACHE['port'], 0)
    cand = []
    for p in _ev_ini():
        try:
            for line in open(p, encoding='utf-8', errors='replace'):
                s = line.strip()
                if s.startswith('http_server_enabled=') and s.split('=', 1)[1].strip() == '0':
                    cand = None                      # 明确关着，不用探了
                    break
                if s.startswith('http_server_port='):
                    v = s.split('=', 1)[1].strip()
                    if v.isdigit():
                        cand.append(int(v))
        except Exception:
            pass
        if cand is None:
            break
    if cand:
        cand += [p for p in (8199, 80, 8080) if p not in cand]
        port = next((c for c in cand if _ev_alive(c)), 0)
    else:
        port = 0
    with EV_LOCK:
        EV_CACHE['port'] = port
        EV_CACHE['tried'] = now
    return port

def ev_search(q, limit=20):
    """查全盘文件名。返回 (items, status)，status 为 ok / off / err。"""
    port = ev_port()
    if not port:
        return [], 'off'
    try:
        d = _ev_get(port, 'search=%s&json=1&path_column=1&size_column=1'
                          '&date_modified_column=1&count=%d&sort=name&ascending=1'
                    % (urllib.parse.quote(q), limit))
    except Exception:
        with EV_LOCK:
            EV_CACHE['port'], EV_CACHE['tried'] = -1, 0.0   # 服务可能刚关，下次重探
        return [], 'err'
    items = []
    for r in (d.get('results') or [])[:limit]:
        name = str(r.get('name') or '')
        parent = str(r.get('path') or '')
        # path 列给的是父目录；但个别版本给完整路径，靠 basename 是否等于 name 判断
        if parent and os.path.basename(parent) == name:
            full, show = parent, os.path.dirname(parent)
        else:
            full = os.path.join(parent, name) if parent else name
            show = parent
        items.append({'n': name, 'p': full, 'd': show,
                      't': str(r.get('type') or 'file')})
    with EV_LOCK:
        now = time.time()
        EV_LAST.clear()
        for it in items:
            EV_LAST[it['p']] = now
    return items, 'ok'


# ---------------------------------------------------------------- 系统图标
# 快搜窗里的图标原先是自绘的「文档 + 彩色条」，一看就是画的。系统图标好看在哪：
# 它和你在资源管理器里看到的是同一张图，认得出来。做法是拿扩展名问 shell
# （SHGFI_USEFILEATTRIBUTES 允许传 ".pdf" 这种不存在的路径），再画到 32 位 DIB 上。
# 只依赖 ctypes + zlib，不引进 PIL —— 打包体积一点没涨。
ICON_CACHE = {}
ICON_LOCK = threading.Lock()
ICON_SIZE = 32                     # 界面显示 17px，2 倍屏正好吃满


def _png_chunk(tag, data):
    import struct
    import zlib as _z
    c = tag + data
    return (struct.pack('>I', len(data)) + c
            + struct.pack('>I', _z.crc32(c) & 0xFFFFFFFF))


def _rgba_to_png(buf, size=ICON_SIZE):
    """BGRA（DIB 从上到下的顺序）→ PNG。手写编码比引依赖划算：
    IHDR + 一行一个 0 过滤字节的 IDAT + IEND，就这三块。"""
    import struct
    import zlib as _z
    alphas = buf[3::4]
    opaque = not any(alphas)       # 老式图标只给了 AND 掩码，alpha 全是 0
    rows = []
    for y in range(size):
        base = y * size * 4
        row = bytearray(size * 4)
        for x in range(size):
            i = base + x * 4
            row[x * 4] = buf[i + 2]        # R
            row[x * 4 + 1] = buf[i + 1]    # G
            row[x * 4 + 2] = buf[i]        # B
            row[x * 4 + 3] = 255 if opaque else buf[i + 3]
        rows.append(b'\x00' + bytes(row))
    raw = b''.join(rows)
    return (b'\x89PNG\r\n\x1a\n'
            + _png_chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + _png_chunk(b'IDAT', _z.compress(raw, 9))
            + _png_chunk(b'IEND', b''))


def _hicon_png(hicon, size=ICON_SIZE):
    """HICON → PNG。画进一块自带 alpha 的 DIB，AlphaBlend 由 DrawIconEx 处理。

    每个 GDI 函数都要显式写 argtypes：不写的话 ctypes 按 int 传参，
    64 位下句柄被截成 32 位，画出来是一片空白（和 SetWindowsHookEx 那个坑同源）。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class BIH(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    class BI(ctypes.Structure):
        _fields_ = [('bmiHeader', BIH), ('bmiColors', wintypes.DWORD * 3)]

    gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.c_void_p, ctypes.c_uint]
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    user32.DrawIconEx.restype = ctypes.c_int

    bi = BI()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = size
    bi.bmiHeader.biHeight = -size         # 负数 = 从上往下写，省一次翻转
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0         # BI_RGB

    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        return None
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bi), 0,
                                  ctypes.byref(bits), None, 0)
    if not hbmp or not bits:
        user32.ReleaseDC(None, hdc_screen)
        return None
    mdc = gdi32.CreateCompatibleDC(hdc_screen)
    gdi32.SelectObject(mdc, hbmp)
    user32.DrawIconEx(mdc, 0, 0, ctypes.c_void_p(hicon), size, size, 0, None, 3)  # DI_NORMAL
    buf = ctypes.string_at(bits, size * size * 4)
    gdi32.DeleteDC(mdc)
    gdi32.DeleteObject(hbmp)
    user32.ReleaseDC(None, hdc_screen)
    return _rgba_to_png(buf, size) if buf else None


def sys_icon(ext, isdir=False):
    """扩展名 → PNG 字节（按扩展名缓存，首次提取一次就够）。
    拿不到时返回 None，前端回落到自绘图标，不影响可用性。

    用 ctypes 直接调 shell32：pywin32 的 win32gui 并没有封装 SHGetFileInfo。"""
    key = 'dir' if isdir else (ext or '').lower()
    with ICON_LOCK:
        if key in ICON_CACHE:
            return ICON_CACHE[key]
    png = None
    hicon = 0
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEINFOW(ctypes.Structure):
            _fields_ = [('hIcon', wintypes.HANDLE), ('iIcon', ctypes.c_int),
                        ('dwAttributes', wintypes.DWORD),
                        ('szDisplayName', wintypes.WCHAR * 260),
                        ('szTypeName', wintypes.WCHAR * 80)]

        shell32 = ctypes.windll.shell32
        shell32.SHGetFileInfoW.restype = ctypes.c_size_t
        shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.POINTER(SHFILEINFOW),
                                          wintypes.UINT, wintypes.UINT]
        SHGFI_ICON, SHGFI_LARGEICON, SHGFI_USEFILEATTRIBUTES = 0x100, 0x0, 0x10
        FILE_ATTRIBUTE_NORMAL, FILE_ATTRIBUTE_DIRECTORY = 0x80, 0x10
        if isdir:
            # 文件夹图标问不出来：shell 不给「目录类型」的通用图标，只认真实存在的目录。
            # 拿 Windows 目录当样本问一次就行，所有文件夹共用同一张。
            name = os.environ.get('WINDIR') or 'C:\\'
            args_flags = SHGFI_ICON | SHGFI_LARGEICON
        else:
            name = key or '.txt'
            # USEFILEATTRIBUTES：只按扩展名查类型对应的图标，不去碰磁盘
            args_flags = SHGFI_ICON | SHGFI_LARGEICON | SHGFI_USEFILEATTRIBUTES
        attr = FILE_ATTRIBUTE_DIRECTORY if isdir else FILE_ATTRIBUTE_NORMAL
        info = SHFILEINFOW()
        ok = shell32.SHGetFileInfoW(name, attr, ctypes.byref(info),
                                    ctypes.sizeof(info), args_flags)
        hicon = int(getattr(info, 'hIcon', 0) or 0)
        if ok and hicon:
            png = _hicon_png(hicon)
    except Exception:
        png = None
    finally:
        if hicon:
            try:
                import ctypes
                ctypes.windll.user32.DestroyIcon(ctypes.c_void_p(hicon))
            except Exception:
                pass
    with ICON_LOCK:                    # 失败也缓存（None），免得每行都重试一次
        ICON_CACHE[key] = png
    return png


def allow_open(paths, replace=False):
    """登记「刚出现在结果里的路径」。/open 与快搜窗的「打开」都只认这份白名单 ——
    os.startfile 对 .exe 就是「运行」，没有闸门等于给了一个任意路径执行入口。
    replace=True 用于整页搜索（换一批结果就该作废旧白名单），
    快搜窗用 replace=False：前缀结果先出、深度结果后补，两批都得能点。"""
    now = time.time()
    with EV_LOCK:
        if replace:
            EV_LAST.clear()
        elif len(EV_LAST) > 4000:
            for k in [k for k, v in EV_LAST.items() if now - v > EV_TTL]:
                EV_LAST.pop(k, None)
            if len(EV_LAST) > 4000:
                EV_LAST.clear()
        for p in paths:
            EV_LAST[p] = now

def ev_recent(p):
    """这个路径是不是刚搜出来的（决定能不能点「打开」）"""
    with EV_LOCK:
        t = EV_LAST.get(p)
    return bool(t) and time.time() - t < EV_TTL

def _drive_root(p):
    """取路径所在盘的根（'D:\\' / '\\\\server\\share\\'；相对路径返回 ''）"""
    d = os.path.splitdrive(p)[0]
    return d + os.sep if d else ''

def prune():
    """清理已删除文件的索引条目。

    关键保护：整块盘不可达时（移动硬盘没插、网盘/网络盘没挂载），那盘下所有文件
    都会 exists()=False。按原逻辑会把这盘的全部索引一次性当"已删除"清光。
    所以先判断盘根可达性，不可达的盘整体跳过，只清理真正消失的文件。"""
    con = init_db()
    paths = [p for (p,) in con.execute('SELECT path FROM meta')]
    alive = {}
    def _reachable(p):
        root = _drive_root(p)
        if not root:
            return True
        if root not in alive:
            alive[root] = os.path.exists(root)
        return alive[root]
    gone, skipped = [], 0
    for p in paths:
        if not _reachable(p):
            skipped += 1
            continue
        if not os.path.exists(p):
            gone.append(p)
    rids = _doc_rids(con)
    for p in gone:
        con.execute('DELETE FROM meta WHERE path=?', (p,))
        _doc_del(con, rids, p)
    con.commit()
    if skipped:
        print(f'清理失效条目: {len(gone)} 条（另有 {skipped} 条所在磁盘当前不可达，已跳过）')
    else:
        print(f'清理失效条目: {len(gone)} 条')
    return len(gone)

def update(targets=None, scope_path=None):
    """增量维护一条龙：重扫清单 → 索引新文件 → 清理已删除条目。
    targets=None 时全盘扫描（命令行老行为）；桌面版传入用户选的文件夹列表。"""
    import traceback
    try:
        if PROG['state'] == 'busy':
            print('已有更新在进行，忽略本次请求')
            return
        _prog(state='busy', phase='scan', err='')
        scan_docs.run_scan(targets)
        scope = Path(scope_path) if scope_path else app_dir() / 'scope.json'
        if not scope.exists():
            _prog(state='error', err='找不到 scope.json —— 请先在设置里选择要索引的文件夹',
                  k='e_no_scope')
            print('[!] 找不到 scope.json')
            return
        index(str(scope))
        _prog(phase='prune', msg='清理失效条目', k='m_pruning')
        prune()
        _prog(state='done', phase='', msg='更新完成', k='m_update_done')
    except Exception as e:
        _prog(state='error', err=f'{type(e).__name__}: {e}')
        traceback.print_exc()

_con = None
con_lock = threading.RLock()

def conn():
    """HTTP 层共用的只读连接（配合 con_lock 使用）。

    原来是「每个请求 init_db() 新建一个、从不关闭」：连接靠 GC 兜底，文件句柄和 WAL
    读者先堆着；而且每次都要重跑一遍 PRAGMA 和 3 个必然失败的 ALTER TABLE。改成一条
    共享连接后，页面和接口的数据库开销从毫秒级降到微秒级。
    多线程下 sqlite3 连接不能裸用，所以所有访问都套 con_lock（RLock，可重入）。"""
    global _con
    if _con is None:
        with con_lock:
            if _con is None:
                _con = init_db()
    return _con


MINI_TPL = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>第二大脑 · 快搜</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#fff;overflow:hidden;
  font-family:"Segoe UI Variable Text","Segoe UI","Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;
  color:#1a2233;-webkit-font-smoothing:antialiased}
#pane{display:flex;flex-direction:column;height:100%}
.bar{display:flex;align-items:center;height:62px;padding:0 12px 0 22px;flex:0 0 auto}
#q{flex:1;min-width:0;border:0;outline:0;background:transparent;font-size:19px;
  font-family:inherit;color:#101828;letter-spacing:.2px;line-height:1.4}
#q::placeholder{color:#a9b0be;font-weight:400}
.act{display:flex;align-items:center;gap:2px;flex:0 0 auto}
.ab{border:0;background:transparent;width:34px;height:34px;border-radius:9px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;color:#9aa3b2;transition:.13s}
.ab:hover{background:#f1f3f8;color:#4f6ef7}
.ab svg{width:17px;height:17px}
#list{flex:0 0 auto;padding:0 8px;overflow:hidden}
.row{display:flex;align-items:center;gap:11px;height:50px;padding:0 14px 0 12px;
  border-radius:9px;cursor:default;user-select:none;position:relative}
#list.rec .row{height:46px}
.row.on{background:#f2f5fc}
.row.on:before{content:"";position:absolute;left:0;top:12px;bottom:12px;width:3px;
  border-radius:2px;background:#4f6ef7}
.ico{flex:0 0 auto;display:flex;align-items:center;justify-content:center;width:19px;height:19px}
.ico .ic{display:block;width:19px;height:19px}
.ico .fic{display:none;width:17px;height:17px}
.ico.noic .ic{display:none}
.ico.noic .fic{display:block}
.txt{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
.nm{font-size:14.5px;line-height:1.22;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  color:#1b2334}
.nm b{font-weight:700;color:#0d1424}
.tag{font-style:normal;font-size:10.5px;line-height:1;color:#8b7ad6;background:#f1eefc;
  border-radius:4px;padding:2px 5px;margin-left:7px;vertical-align:1.5px;letter-spacing:.02em}
.sn{font-size:12px;line-height:1.25;color:#9aa3b2;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.sn mark{background:#fff2b8;border-radius:2px;padding:0 1px;color:#5a4a12;font-weight:500}
.pt{display:flex;align-items:baseline;font-size:11.5px;line-height:1.2;color:#aab2c0;
  white-space:nowrap;overflow:hidden}
.pt b{font-weight:400;overflow:hidden;text-overflow:ellipsis;flex:0 1 auto}
.pt b.last{color:#7c8698}
.pt u{text-decoration:none;color:#d4d9e2;margin:0 5px;flex:0 0 auto;font-size:11px}
.rh{display:flex;align-items:center;height:26px;padding:0 14px 0 13px;font-size:11px;
  letter-spacing:.06em;color:#b3bac7;flex:0 0 auto}
.rh span{margin-left:auto;letter-spacing:0;color:#c6ccd7}
.kbd{flex:0 0 auto;font-size:12px;color:#c0c6d1;font-variant-numeric:tabular-nums}
.row.on .kbd{color:#8b93a5}
.foot{display:flex;align-items:center;justify-content:space-between;height:36px;
  margin:0 8px;padding:0 12px;border-top:1px solid #f1f2f6;font-size:12.5px;color:#7b8398;
  gap:10px;flex:0 0 auto}
.foot.hide{display:none}
.rh.hide{display:none}
.foot .fl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.foot .fr{flex:0 0 auto;color:#a7aebc;font-size:11.5px}
.foot b{font-weight:600;color:#3b465c}
.empty{height:64px;display:flex;align-items:center;padding:0 24px;font-size:13px;color:#9aa3b2}
.empty i{font-style:normal;margin-left:auto;color:#c2c8d4;font-size:11.5px}
</style></head><body>
<div id="pane">
  <div class="bar">
    <input id="q" spellcheck="false" autocomplete="off" placeholder="搜索文件名、文件夹或正文内容">
    <div class="act">
      <button class="ab" id="btnDetail" title="详细搜索（Ctrl+Enter）">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/>
             <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>
      </button>
      <button class="ab" id="btnSearch" title="搜索">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/></svg>
      </button>
    </div>
  </div>
  <div class="rh hide" id="rh">最近打开<span>Enter 直接打开</span></div>
  <div id="list"></div>
  <div class="foot hide" id="foot"><span class="fl" id="ft"></span>
    <span class="fr" id="fr">Ctrl+Enter 详细搜索</span></div>
  <div class="empty hide" id="empty">输入文件名或正文关键词，边打边出结果<i>双击 Ctrl 唤出 · Esc 关闭</i></div>
</div>
<script>
var Q = document.getElementById('q'), LIST = document.getElementById('list'),
    FOOT = document.getElementById('foot'), FT = document.getElementById('ft'),
    FR = document.getElementById('fr'), RH = document.getElementById('rh'),
    EMPTY = document.getElementById('empty');
var API = null, rows = [], cur = -1, seq = 0, timer = 0, deeptimer = 0, shown = 60;
var MODE = 'empty', RECENT = [];
var TYPE = {'.pdf':'#e05252','.doc':'#2b6cb0','.docx':'#2b6cb0','.xls':'#2f855a',
  '.xlsx':'#2f855a','.csv':'#2f855a','.ppt':'#dd6b20','.pptx':'#dd6b20',
  '.md':'#64748b','.txt':'#64748b','.jpg':'#7c3aed','.png':'#7c3aed','.jpeg':'#7c3aed',
  '.gif':'#7c3aed','.webp':'#7c3aed','.mp4':'#0891b2','.mkv':'#0891b2','.avi':'#0891b2',
  '.mp3':'#0d9488','.wav':'#0d9488','.zip':'#a16207','.rar':'#a16207','.7z':'#a16207',
  '.exe':'#475569','.lnk':'#475569','.psd':'#2563eb','.ai':'#d97706'};
function esc(s){return String(s).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function ico(it){
  /* 真图标：向 shell 要该扩展名的图标（/api/icon），和资源管理器里看到的是同一张。
     拿不到就把这批 SVG 露出来（onerror 给父元素加 noic），不影响可用性。 */
  var dir = it.t==='dir';
  var fb = dir
    ? '<svg class="fic" viewBox="0 0 24 24"><path d="M2.6 7.1c0-1 .8-1.8 1.8-1.8h4c.5 0 1 .2 1.3.6l1.3 1.5h8.7c1 0 1.8.8 1.8 1.8v9.4c0 1-.8 1.8-1.8 1.8H4.4c-1 0-1.8-.8-1.8-1.8z" fill="#f4bd4e"/></svg>'
    : (function(){ var c = TYPE[it.e] || '#a3a9b6';
        return '<svg class="fic" viewBox="0 0 24 24"><path d="M4.8 2.6h8.4L18.8 8.3v13.1H4.8z" fill="#fff" stroke="#c9cfdb" stroke-width="1.15" stroke-linejoin="round"/><path d="M13.2 2.6v5.7h5.6" fill="none" stroke="#c9cfdb" stroke-width="1.15" stroke-linejoin="round"/><rect x="7.2" y="11.7" width="9" height="2.6" rx="1.3" fill="'+c+'" opacity=".92"/><rect x="7.2" y="16" width="5.8" height="2.6" rx="1.3" fill="'+c+'" opacity=".45"/></svg>'; })();
  var q = dir ? 'd=1' : 'e='+encodeURIComponent(it.e||'');
  return '<img class="ic" src="/api/icon?'+q+'" alt="" '
    +'onerror="this.parentNode.classList.add(&quot;noic&quot;)">'+fb;
}
/* 路径分级：近年主流搜索器把文件夹路径拆成一级一级，每级独立好读，
   比一整行灰字好认得多。层级超过三级就保留盘符 + 最后两级——
   最后一级就是文件所在的文件夹，这才是你要的定位信息。 */
function segs(p){
  if(!p)return '';
  var parts=String(p).split(/[\\\/]+/), out=[];
  for(var k=0;k<parts.length;k++) if(parts[k]) out.push(parts[k]);
  if(!out.length)return '';
  var show=out;
  if(out.length>3) show=[out[0],'…',out[out.length-2],out[out.length-1]];
  var h='';
  for(var i=0;i<show.length;i++){
    if(i)h+='<u>›</u>';
    h+='<b'+(i===show.length-1?' class="last"':'')+'>'+esc(show[i])+'</b>';
  }
  return h;
}
function hl(name,q){
  if(!q)return esc(name);
  var low=name.toLowerCase(), ql=q.toLowerCase(), out='', i=0, j;
  while((j=low.indexOf(ql,i))>=0){
    out+=esc(name.slice(i,j))+'<b>'+esc(name.slice(j,j+ql.length))+'</b>';
    i=j+ql.length;
  }
  return out+esc(name.slice(i));
}
function height(){
  var n=rows.length;
  if(!n) return 62+64;                      /* 空且没记录：一条提示，最克制 */
  if(MODE==='recent') return 62+26+n*46+36; /* 空状态推荐：标题行 + 紧凑行 + 提示条 */
  return 62+n*50+36;
}
function resize(){
  var h=height();
  if(h===shown)return;
  shown=h;
  if(API&&API.resize)API.resize(h);
}
/* 正文片段的 char(1)/char(2) 是检索层给的命中标记，换成 <mark>；
   其余字符一律转义，片段来自本地文件的任意内容，不能直接塞进 innerHTML。 */
function snipHtml(s){
  var out='', open=false;
  s=String(s||'');
  for(var i=0;i<s.length;i++){
    var c=s.charAt(i);
    if(c==='\u0001'){ if(!open){out+='<mark>';open=true;} }
    else if(c==='\u0002'){ if(open){out+='</mark>';open=false;} }
    else out+=esc(c);
  }
  return out+(open?'</mark>':'');
}
function rowHtml(it,i,q,withKey){
  var sub = it.b
    ? '<span class="sn">'+snipHtml(it.b)+'</span>'
    : '<span class="pt">'+segs(it.d||'')+'</span>';
  return '<div class="row'+(i===cur?' on':'')+'" data-i="'+i+'">'
    +'<span class="ico">'+ico(it)+'</span>'
    +'<span class="txt"><span class="nm">'+(q?hl(it.n,q):esc(it.n))
      +(it.b?'<i class="tag">正文</i>':'')+'</span>'+sub+'</span>'
    +(withKey?'<span class="kbd">'+(i<9?'Ctrl+'+(i+1):'')+'</span>':'')+'</div>';
}
function render(items,q,keep){
  var old=cur;
  MODE='result';
  rows=items||[];
  cur=rows.length?(keep&&old>=0?Math.min(old,rows.length-1):0):-1;
  var html='';
  for(var i=0;i<rows.length;i++) html+=rowHtml(rows[i],i,q,true);
  LIST.className='';
  LIST.innerHTML=html;
  RH.classList.add('hide');
  EMPTY.classList.add('hide');
  FOOT.classList.toggle('hide', !rows.length);
  if(rows.length){
    FT.innerHTML='展示更多 “'+esc(q)+'” 的搜索结果';
    FR.innerHTML='Ctrl+Enter 详细搜索';
  }
  bind(); resize();
}
/* 空状态：打开就先给一屏「刚动过的文件」。近年主流搜索器就是这个做法——
   很多时候要找的东西就在眼前，搜索这一步可以直接省掉。 */
function renderRecent(items){
  MODE='recent';
  rows=items||[]; cur=-1;
  var html='';
  for(var i=0;i<rows.length;i++) html+=rowHtml(rows[i],i,'',false);
  LIST.className='rec';
  LIST.innerHTML=html;
  RH.classList.toggle('hide', !rows.length);
  EMPTY.classList.toggle('hide', !!rows.length);
  FOOT.classList.toggle('hide', !rows.length);
  if(rows.length){ FT.innerHTML='也可以直接打字，正文一样能搜'; FR.innerHTML='Esc 关闭'; }
  bind(); resize();
}
function fetchRecent(){
  fetch('/api/recent').then(function(r){return r.json()}).then(function(d){
    if(MODE!=='empty')return;         /* 用户已经开始打字了，别把结果顶掉 */
    RECENT=d.items||[];
    renderRecent(RECENT);
  }).catch(function(){});
}
function bind(){
  var els=LIST.querySelectorAll('.row');
  for(var i=0;i<els.length;i++){
    els[i].onmouseenter=function(){ sel(+this.dataset.i); };
    els[i].onclick=function(){ open(+this.dataset.i); };
  }
}
function sel(i){
  if(i<0||i>=rows.length)return;
  cur=i;
  var els=LIST.querySelectorAll('.row');
  for(var k=0;k<els.length;k++) els[k].classList.toggle('on', k===i);
}
function open(i){
  var it=rows[i]; if(!it)return;
  if(API&&API.open_path)API.open_path(it.p);
}
function detail(){
  var v=Q.value.trim();
  if(API&&API.detail)API.detail(v);
}
/* 两趟查：
   prefix 走名字索引，157 万行 0.2ms，每次输入都立刻发；
   deep 要全表扫（一个都没命中时 500ms），所以等用户停手 300ms 再发，
   期间继续打字就取消——打字快的人根本等不到它，看到的永远是毫秒级的前缀结果。 */
function doPrefix(v){
  var my=++seq;
  fetch('/api/mini?q='+encodeURIComponent(v)).then(function(r){return r.json()})
    .then(function(d){
      if(my!==seq)return;
      render(d.items||[],v);
      if((d.items||[]).length<9 && v.length>=2){
        clearTimeout(deeptimer);
        deeptimer=setTimeout(function(){
          if(my!==seq)return;
          doDeep(v);
        },300);
      }
    }).catch(function(){});
}
function doDeep(v){
  var my=++seq;
  /* body=1：连同正文一起查。名字命中已经排满一屏时这一步本来就不会发，
     发出来的时候说明名字不够，正好拿正文命中补满最后几行。 */
  fetch('/api/mini?q='+encodeURIComponent(v)+'&deep=1&body=1').then(function(r){return r.json()})
    .then(function(d){
      if(my!==seq)return;
      if(!(d.items||[]).length)return;
      render(d.items,v,true);
    }).catch(function(){});
}
function schedule(){
  clearTimeout(timer); clearTimeout(deeptimer);
  var v=Q.value.trim();
  if(!v){ seq++; MODE='empty'; renderRecent(RECENT); return; }
  timer=setTimeout(function(){ doPrefix(v); },80);
}
Q.addEventListener('input',schedule);
Q.addEventListener('focus',function(){ setTimeout(function(){Q.focus()},10); });
Q.addEventListener('keydown',function(e){
  var k=e.key;
  if(k==='ArrowDown'){e.preventDefault();sel(Math.min(cur+1,rows.length-1));}
  else if(k==='ArrowUp'){e.preventDefault();sel(Math.max(cur-1,0));}
  else if(k==='Enter'){
    e.preventDefault();
    if(e.ctrlKey||e.metaKey){ detail(); return; }
    if(rows.length) open(cur<0?0:cur); else detail();
  }
  else if(k==='Escape'){ e.preventDefault(); if(API&&API.hide)API.hide(); }
  else if(e.ctrlKey&&k>='1'&&k<='9'){ e.preventDefault(); var i=+k-1; if(rows[i])open(i); }
  else if(k==='Tab'){ e.preventDefault(); detail(); }
});
document.getElementById('btnDetail').onclick=detail;
document.getElementById('btnSearch').onclick=function(){
  var v=Q.value.trim(); if(v) doPrefix(v); else Q.focus();
};
window.addEventListener('pywebviewready',function(){ API=window.pywebview.api; });
document.addEventListener('DOMContentLoaded',function(){
  if(window.pywebview&&window.pywebview.api)API=window.pywebview.api;
  Q.focus();
  fetchRecent();
});
window.addEventListener('focus',function(){
  setTimeout(function(){Q.focus();},20);
});
window.__onShow=function(){
  Q.value=''; cur=-1; seq++;
  renderRecent(RECENT);        /* 先用上次那一屏，秒出；下面再后台刷新 */
  Q.focus();
  fetchRecent();
};
window.addEventListener('blur',function(){
  /* 失焦交给 Python 判断（MiniApi.blurred）：搜索结果出来时窗口会变高，
     WebView2 重新布局会短暂丢焦点，无脑照着 blur 关窗就成了「刚打完字窗口没了」。
     那边会看鼠标在不在窗口里，再决定是收起还是把焦点抢回来。 */
  setTimeout(function(){
    if(document.hasFocus())return;
    if(API&&API.blurred)API.blurred();
    else if(API&&API.hide)API.hide();
  },180);
});
</script></body></html>'''


def make_server(port=8765):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    PAGE_TPL = r'''<!doctype html><html lang="zh-CN"><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='22' fill='%236b53e8'/%3E%3Ctext y='.92em' x='8' font-size='82'%3E%F0%9F%A7%A0%3C/text%3E%3C/svg%3E">
<title>第二大脑 · 本地文档搜索</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--ink:#1a2233;--sub:#6b7280;--acc:#4f6ef7;--mark:#fde68a}
*{box-sizing:border-box;margin:0}
body{font-family:"Segoe UI",system-ui,-apple-system,"Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);min-height:100vh}
.hero{max-width:1360px;margin:0 auto;padding:48px 20px 12px;text-align:center;position:relative}
.hero h1{font-size:30px;font-weight:700;letter-spacing:.5px}
.hero h1 em{font-style:normal;background:linear-gradient(120deg,#4f6ef7,#9333ea);-webkit-background-clip:text;background-clip:text;color:transparent}
.hero p{color:var(--sub);margin-top:8px;font-size:14px}
.hero .hk{margin-top:10px;font-size:12.5px;color:#9aa3b2}
.hero .hk kbd{border:1px solid #e3e6ef;border-bottom-width:2px;border-radius:5px;padding:1px 7px;
  font-size:11.5px;font-family:inherit;background:#fff;color:#5b6478;margin:0 1px}
.tools{position:absolute;top:14px;right:18px;display:flex;gap:8px;align-items:center}
.gear,.lang{border:1px solid #e3e6ef;background:#fff;border-radius:10px;padding:8px 14px;font-size:13px;color:var(--sub);cursor:pointer;transition:.15s;font-family:inherit}
.gear:hover,.lang:hover{color:var(--acc);border-color:var(--acc)}
.lang{font-weight:700;min-width:52px}
.bar{max-width:1360px;margin:22px auto 0;padding:0 20px;display:flex;gap:10px;flex-wrap:wrap}
.bar input{border:1px solid #e3e6ef;background:var(--card);border-radius:12px;padding:13px 16px;font-size:15px;outline:none;transition:.15s;font-family:inherit}
.bar input:focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(79,110,247,.15)}
#q{flex:3;min-width:220px}#t{flex:1;min-width:110px}#d{flex:1.4;min-width:140px}
.bar button{border:none;border-radius:12px;padding:13px 26px;font-size:15px;font-weight:600;color:#fff;background:linear-gradient(120deg,#4f6ef7,#7c3aed);cursor:pointer;transition:.15s;font-family:inherit}
.bar button:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(79,110,247,.35)}
.wrap{max-width:1360px;margin:18px auto 60px;padding:0 20px}
.meta{color:var(--sub);font-size:13px;margin:4px 2px 10px}
.card{background:var(--card);border:1px solid #eceef4;border-radius:14px;padding:16px 18px;margin-bottom:12px;transition:.15s}
.card:hover{box-shadow:0 8px 22px rgba(26,34,51,.08);border-color:#dfe3ee}
.top{display:flex;align-items:center;gap:10px}
.badge{font-size:11px;font-weight:700;letter-spacing:.5px;padding:3px 8px;border-radius:6px;background:#eef1fd;color:#4f6ef7}
.name{font-weight:600;font-size:15px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn{border:1px solid #e3e6ef;background:#fff;color:var(--sub);border-radius:8px;padding:5px 12px;font-size:12px;cursor:pointer;transition:.15s;white-space:nowrap;font-family:inherit}
.btn:hover{color:var(--acc);border-color:var(--acc)}
.path{color:var(--sub);font-size:12px;margin-top:6px;word-break:break-all}
.snip{margin-top:10px;font-size:13.5px;line-height:1.75;color:#3a4356;background:#f8f9fc;border-radius:8px;padding:10px 12px;word-break:break-word}
.evblock{background:linear-gradient(180deg,#fbfcff,#f6f8ff);border:1px solid #e1e7fa;border-radius:14px;padding:12px 16px 8px;margin-bottom:16px}
.evhead{font-size:12.5px;font-weight:700;color:#3a4fd7;display:flex;align-items:baseline;gap:8px;margin-bottom:6px}
.evhead em{font-weight:400;font-style:normal;color:var(--sub);font-size:11.5px}
.evitem{display:flex;align-items:center;gap:8px;padding:6px 0;border-top:1px solid #ebeffb}
.evitem:first-of-type{border-top:none}
.evitem .badge{background:#e6ebff}
.evname{font-size:13px;font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.evpath{font-size:11.5px;color:var(--sub);flex:0 1 auto;max-width:42%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left}
.evtag{font-size:10.5px;color:#7c3aed;border:1px solid #dfd3fb;border-radius:5px;padding:1px 5px;white-space:nowrap;flex:0 0 auto}
.evmore{font-size:11.5px;color:var(--sub);padding:7px 0 3px;border-top:1px solid #ebeffb}
mark{background:var(--mark);border-radius:3px;padding:0 2px;font-weight:600}
.empty{text-align:center;color:var(--sub);padding:60px 0;font-size:14px;line-height:2}
.empty b{color:var(--ink)}
.panel{max-width:1360px;margin:0 auto;padding:0 20px 8px;display:none}
.panel.on{display:block}
.pbox{background:var(--card);border:1px solid #eceef4;border-radius:14px;padding:18px 20px;text-align:left}
.pbox h3{font-size:15px;margin-bottom:6px}
.pbox p{font-size:12.5px;color:var(--sub);margin-bottom:10px;line-height:1.7}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.chip{display:flex;align-items:center;gap:6px;background:#eef1fd;color:#3a4fd7;border-radius:8px;padding:6px 10px;font-size:12.5px;word-break:break-all;max-width:100%}
.chip b{cursor:pointer;font-weight:700;color:#9aa3c0}
.chip b:hover{color:#e05555}
.pbtn{border:none;border-radius:10px;padding:10px 22px;font-size:14px;font-weight:600;color:#fff;background:linear-gradient(120deg,#4f6ef7,#7c3aed);cursor:pointer;font-family:inherit}
.pbtn.gray{background:#eef0f7;color:var(--ink)}
.pbtn:disabled{opacity:.5;cursor:default}
.prow{display:flex;gap:10px;flex-wrap:wrap}
.pmsg{font-size:13px;color:var(--sub);margin-top:10px;line-height:1.7;min-height:18px}
.nstat{font-size:12px;color:var(--sub);margin-top:8px;line-height:1.7}
.pbar{height:8px;background:#e8eaf3;border-radius:6px;overflow:hidden;margin-top:10px}
.pbar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#4f6ef7,#7c3aed);transition:width .3s}
.help{margin-top:12px;font-size:12.5px;color:var(--sub)}
.help summary{cursor:pointer;color:var(--acc);font-weight:600;user-select:none}
.help summary:hover{text-decoration:underline}
.help li{margin:5px 0;line-height:1.75}
.crow{display:flex;align-items:center;gap:8px;background:#f8f9fc;border:1px solid #eceef4;border-radius:8px;padding:7px 10px;margin-bottom:6px;font-size:12.5px;cursor:pointer}
.crow input{accent-color:#4f6ef7}
.crow .n{color:var(--sub);margin-left:auto;white-space:nowrap}
nz{display:inline-block;color:#b45309;font-size:11px;border:1px solid #fde68a;background:#fffbeb;border-radius:5px;padding:1px 6px;white-space:nowrap}
#toast{position:fixed;left:50%;bottom:36px;transform:translateX(-50%) translateY(20px);background:#1a2233;color:#fff;padding:10px 18px;border-radius:10px;font-size:13px;opacity:0;pointer-events:none;transition:.2s;z-index:9;max-width:80vw;text-align:center}
#toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
@media(max-width:680px){.tools{position:static;justify-content:flex-end;margin-bottom:12px}.hero{padding-top:18px}.hero h1{font-size:24px}}
</style></head><body>
<div class=hero>
<div class=tools>
<button class=lang id=langbtn onclick=toggleLang() title="Switch language / 切换语言">EN</button>
<button class=gear id=gear onclick=showCfg() data-i18n="settings">⚙ 设置 / 更新</button>
</div>
<h1>🧠 <em data-i18n="brand">第二大脑</em></h1>
<p data-i18n="sub">搜文档内容和文件名 —— __TOTAL__ 篇已入库 · 全程本地 · 中文 3 字以上效果最佳</p>
<p class=hk data-i18n="hk">双击 <kbd>Ctrl</kbd> 呼出快搜窗 —— 关掉本窗口不会退出，程序在托盘里等着</p>
</div>
<div class=bar>
<input id=q data-i18n-ph="ph_q" placeholder="输入关键词，比如：项目方案 / 课程笔记 / 报销标准" autofocus>
<input id=t data-i18n-ph="ph_t" placeholder="类型：pdf / docx…">
<input id=d data-i18n-ph="ph_d" placeholder="路径包含…">
<button onclick=go() data-i18n="search">搜 索</button></div>
<div class=panel id=cfg><div class=pbox>
<h3 data-i18n="scope_h">索引范围与更新</h3>
<p id=cfgtip data-i18n="scope_tip">下面这些文件夹里的文档会被收入索引。只扫描选定的文件夹，小说类目录永远自动跳过。</p>
<div class=chips id=dirs></div>
<div id=cand></div>
<div class=prow>
<button class="pbtn" id=smartbtn data-i18n="btn_scan" data-i18n-title="tip_scan" onclick=smartScan(this)>🚀 智能扫描全盘（推荐）</button>
<button class="pbtn gray" id=addbtn data-i18n="btn_add" data-i18n-title="tip_add" onclick=addDir()>➕ 手动添加文件夹</button>
<button class="pbtn" id=savebtn data-i18n="btn_save" data-i18n-title="tip_save" onclick=saveUpd(this)>💾 保存并更新索引</button>
<button class="pbtn gray" id=updonly data-i18n="btn_update" data-i18n-title="tip_update" onclick=upd(this)>🔄 按现有范围更新</button>
<button class="pbtn gray" id=stbtn data-i18n="btn_test" data-i18n-title="tip_test" onclick=selfTest(this)>🩺 引擎体检</button>
<button class="pbtn gray" id=nbtn data-i18n="btn_names" data-i18n-title="tip_names" onclick=buildNames(this)>🗂 重建文件名索引</button></div>
<div class=nstat id=nstat></div>
</div>
<div class=pbar id=pw style=display:none><i id=pi></i></div>
<div class=pmsg id=pm></div>
<details class=help><summary data-i18n="help_q">❓ 这五个按钮分别是干嘛的？</summary><ul>
<li data-i18n="help_1"></li><li data-i18n="help_2"></li><li data-i18n="help_3"></li><li data-i18n="help_4"></li><li data-i18n="help_5"></li>
</ul></details>
</div></div>
<div class=wrap><div id=r class=empty>输入关键词开始搜索</div></div>
<div class=meta style="text-align:center;padding:0 20px 28px" data-i18n="footer">第二大脑 __VER__</div>
<div id=toast></div>
<script>
const DICT={
zh:{
title:'第二大脑 · 本地文档搜索',
brand:'第二大脑',
sub:'搜文档内容和文件名 —— __TOTAL__ 篇已入库 · 全程本地 · 中文 3 字以上效果最佳',
hk:'双击 <kbd>Ctrl</kbd> 呼出快搜窗 —— 关掉本窗口不会退出，程序在托盘里等着',
settings:'⚙ 设置 / 更新',
ph_q:'输入关键词，比如：项目方案 / 课程笔记 / 报销标准',
ph_t:'类型：pdf / docx…',
ph_d:'路径包含…',
search:'搜 索',
scope_h:'索引范围与更新',
scope_tip:'下面这些文件夹里的文档会被收入索引。只扫描选定的文件夹，小说类目录永远自动跳过。',
btn_scan:'🚀 智能扫描全盘（推荐）',
btn_add:'➕ 手动添加文件夹',
btn_save:'💾 保存并更新索引',
btn_update:'🔄 按现有范围更新',
btn_test:'🩺 引擎体检',
btn_names:'🗂 重建文件名索引',
tip_scan:'第一次用点这个：扫一遍电脑（只记文件名，不读内容），列出哪里有多少文档给您勾选，噪音目录自动不勾',
tip_add:'自己挑要搜索的文件夹，精确控制范围',
tip_save:'把勾选/添加的文件夹存为索引范围并开始收录（第一次全量较慢，之后增量很快）',
tip_update:'日常维护：来了新文件点一下，只收新文件，约 1~3 分钟',
tip_test:'给搜索引擎考试：用内置假图书馆测搜得准不准、OCR 错字率、坏文件崩不崩溃。约 1~2 分钟，不碰真实文档库',
tip_names:'扫一遍全盘、只记文件名（不打开、不读内容），让「文件名命中」覆盖整个磁盘——安装包、图片、视频、压缩包都能按名字搜到。157 万个文件实测约 3 分钟',
help_q:'❓ 这六个按钮分别是干嘛的？',
help_1:'🚀 <b>智能扫描全盘</b>——第一次用点这个：扫一遍电脑（只记文件名、不读内容），列出「哪里有多少文档」给您勾选，疑似噪音自动不勾、小说目录直接排除',
help_2:'➕ <b>手动添加文件夹</b>——想精确控制搜索范围时用，点一次弹一个目录选择框',
help_3:'💾 <b>保存并更新索引</b>——把上面勾选/添加的范围生效并开始收录：第一次全量较慢，之后每次只收新文件',
help_4:'🔄 <b>按现有范围更新</b>——日常维护按钮：电脑里来了新文档点一下，约 1~3 分钟',
help_5:'🩺 <b>引擎体检</b>——给搜索引擎「考试」：用一份内置的假图书馆测它搜得准不准、排序对不对、OCR 错字率多少、坏文件崩不崩溃，约 1~2 分钟。<b>不碰您的真实文档库</b>，报告在 eval_report.txt',
help_6:'🗂 <b>重建文件名索引</b>——扫一遍全盘但只记文件名，不打开任何文件，约 3 分钟（157 万个文件）。之后搜「文件名」能覆盖整个磁盘（安装包、视频、压缩包都算），不只是入库的那几千份文档。加了移动硬盘、换了大批文件后点一次',
nstat_off:'全盘文件名索引：还没建立 · 现在只能搜到已入库的文档，点「🗂 重建文件名索引」扫一遍（约 3 分钟）',
nstat_on:'全盘文件名索引：{n} 个文件（只记名字和路径，不读内容）',
m_names:'建立全盘文件名索引中…',
m_names_target:'扫描 {p}',
m_names_progress:'扫描 {p}（已 {n} 个文件）',
m_names_finish:'整理文件名索引…',
m_names_done:'文件名索引完成：{n} 个文件',
e_names_busy:'已有任务在跑，等它跑完再点',
empty_hint:'输入关键词开始搜索',
empty_tip:'<b>小提示</b>：两个过滤框可以留空；点结果里的「打开」直接打开原文件',
footer:'第二大脑 __VER__ · 结果按相关度(bm25)排序 · 引擎体检：设置里点「🩺 引擎体检」或命令行 selftest，报告在 eval_report.txt',
open:'打开',copy:'复制路径',copied:'已复制',
found:'找到 <b>{n}</b> 条 · {ms} ms',
searching:'搜索中…',
need_kw:'请先输入关键词',
no_hit:'没有找到「{q}」',
ev_h:'📁 文件名命中',ev_sub:'全盘任意文件 · 本机文件名索引',ev_sub_off:'索引内已收录的文件',ev_tag:'全盘',
ev_more:'还有更多 —— 再加一个关键词缩小范围',
ev_only:'索引的内容里没有命中，但文件名有 <b>{n}</b> 条匹配：',
no_hit_tip:'试试<b>换个词</b>、<b>少个过滤条件</b>，或先点「🔄 按现有范围更新」把新文件收进来',
opened:'已打开',open_fail:'文件不存在或已被移动',
first_run:'首次使用：推荐点「🚀 智能扫描全盘」，它会列出电脑里哪里有文档给您勾选；也可以手动添加文件夹。',
no_dir:'（还没有文件夹，点「添加文件夹」）',
add_prompt:'输入要索引的文件夹完整路径：',
scan_hint:'🧠 智能扫描全盘中（只记文件名不读内容，约 2~4 分钟）…',
scan_fail:'❌ 智能扫描失败：',
cand_msg:'共扫到 <b>{n}</b> 个文档，分布在 {g} 个顶层目录。<b>橙色「疑似噪音」默认不勾</b>；确认后点「💾 保存并更新索引」。',
noise:'疑似噪音',
cand_tail:'（小说类目录已自动排除，不会出现在这里）',
cand_done:'勾选完成后点「💾 保存并更新索引」。',
test_hint:'🩺 引擎体检进行中（独立子进程，不碰真实索引库）…',
test_busy:'体检进行中…',
test_ok:'✅ 体检通过：',test_bad:'❌ 体检未通过：',
test_report:'（完整报告 eval_report.txt）',
save_need:'请先至少添加一个文件夹',
saving:'保存配置…',save_fail:'保存失败',
upd_need:'请先添加或选择索引文件夹',
started:'已开始，后台处理中…',
ph_scan:'📂 扫描文件中',ph_scan_end:'📄 扫描完成，准备入库',ph_index:'🔎 建立索引中',ph_discover:'🧠 智能扫描中',ph_prune:'🧹 清理失效条目',ph_working:'⏳ 处理中',
prog:'{ph} · 已处理 {d}/{t}，成功 {ok}，失败 {f}',
done_reload:'✅ {m}，2 秒后自动刷新…',
err_prefix:'❌ 出错了：',
m_scanning:'正在扫描 {p}',
m_scan_end:'发现 {n} 个文档，准备入库',
m_indexing:'正在建立索引（共 {n} 篇）',
m_index_done:'完成，库内共 {n} 篇',
m_pruning:'清理失效条目',
m_pruned:'清理失效条目 {n} 条',
m_update_done:'更新完成',
m_discovering:'智能扫描中（只记文件名，不读内容）',
m_discover_target:'智能扫描 {p}',
m_discover_done:'智能扫描完成：{n} 个文档',
m_selftest_busy:'质量自检进行中（检索评测/OCR 准确率/损坏文件 fuzz…）',
m_selftest_done:'自检结束',
e_no_scope:'找不到 scope.json —— 请先在设置里选择要索引的文件夹',
e_need_dir:'至少要添加一个文件夹',
e_discover_busy:'智能扫描正在进行',
e_selftest_busy:'自检已在进行',
e_update_busy:'已有更新在进行',
e_bad_body:'请求格式不正确',
e_query:'查询出错（关键词至少 3 个字符效果最佳）: {e}'
},
en:{
title:'Second Brain · Local Document Search',
brand:'Second Brain',
sub:'Search document contents and file names — __TOTAL__ files indexed · 100% local & offline · best with 3+ characters',
hk:'Double-tap <kbd>Ctrl</kbd> for quick search — closing this window keeps it running in the tray',
settings:'⚙ Settings',
ph_q:'Keywords, e.g. project plan / lecture notes / expense policy',
ph_t:'Type: pdf / docx…',
ph_d:'Path contains…',
search:'Search',
scope_h:'Index scope & maintenance',
scope_tip:'Documents inside these folders are indexed. Only the folders you pick are scanned; fiction/novel folders are always skipped.',
btn_scan:'🚀 Smart scan (recommended)',
btn_add:'➕ Add folder',
btn_save:'💾 Save & build index',
btn_update:'🔄 Update index',
btn_test:'🩺 Health check',
btn_names:'🗂 Rebuild name index',
tip_scan:'First run: scans the whole computer (file names only, never contents), then lists candidate folders with document counts. Noisy folders stay unchecked.',
tip_add:'Pick the folders you want to search, one by one',
tip_save:'Save the selected folders as the index scope and start indexing (the first full build is slow, later runs are incremental)',
tip_update:'Routine maintenance: pick up newly added documents, usually 1-3 minutes',
tip_test:'Exams for the search engine: measures ranking quality, OCR error rate and crash-resistance on corrupted files. About 1-2 minutes, never touches your real library.',
tip_names:'Walks every disk but records file names only — nothing is opened or read. Lets the filename column cover the whole machine (installers, images, videos, archives), not just indexed documents. About 3 minutes for 1.57M files.',
help_q:'❓ What do these six buttons do?',
help_1:'🚀 <b>Smart scan</b> — first run: walks your disks (file names only), lists folder candidates with counts. Likely-noise folders are unchecked, fiction folders are excluded entirely.',
help_2:'➕ <b>Add folder</b> — use when you want precise control over the search scope; opens a folder picker.',
help_3:'💾 <b>Save & build index</b> — applies the selected scope and starts indexing. Slow the first time, incremental afterwards.',
help_4:'🔄 <b>Update index</b> — everyday maintenance: click when new documents arrive, about 1-3 minutes.',
help_5:'🩺 <b>Health check</b> — runs an exam on a synthetic library: ranking quality, OCR error rate, robustness against corrupted files. Takes 1-2 minutes and <b>never touches your real library</b>. Report saved to eval_report.txt',
help_6:'🗂 <b>Rebuild name index</b> — walks every disk but records file names only, nothing is opened. Afterwards filename search covers the whole machine (installers, videos, archives), not just the few thousand indexed documents. Run it after adding a drive or moving a lot of files.',
nstat_off:'Disk-wide name index: not built yet · only indexed documents can be matched — click 🗂 Rebuild name index (about 3 minutes)',
nstat_on:'Disk-wide name index: {n} files (names and paths only, contents never read)',
m_names:'Building the disk-wide name index…',
m_names_target:'Scanning {p}',
m_names_progress:'Scanning {p} ({n} files so far)',
m_names_finish:'Sorting the name index…',
m_names_done:'Name index complete: {n} files',
e_names_busy:'Another task is running — wait for it to finish',
empty_hint:'Type a keyword to start searching',
empty_tip:'<b>Tip</b>: the two filter boxes are optional; hit “Open” on a result to launch the original file',
footer:'Second Brain __VER__ · results ranked by relevance (bm25) · health check: “🩺 Health check” in settings, or `selftest` on the command line — report in eval_report.txt',
open:'Open',copy:'Copy path',copied:'Copied',
found:'<b>{n}</b> results · {ms} ms',
searching:'Searching…',
need_kw:'Please type a keyword first',
no_hit:'Nothing found for “{q}”',
ev_h:'📁 Filename matches',ev_sub:'any file on disk · built-in name index',ev_sub_off:'files already in the index',ev_tag:'disk',
ev_more:'more matches — add a keyword to narrow it down',
ev_only:'Nothing in the index matched, but <b>{n}</b> filenames do:',
no_hit_tip:'Try <b>another keyword</b>, <b>fewer filters</b>, or run the updater to index new files first.',
opened:'Opened',open_fail:'File no longer exists or has been moved',
first_run:'First run: click “🚀 Smart scan” to see where your documents live, then tick the folders you want. You can also add folders manually.',
no_dir:'(no folders yet — click “Add folder”)',
add_prompt:'Full path of the folder to index:',
scan_hint:'🧠 Smart scan running (file names only, no contents; about 2-4 minutes)…',
scan_fail:'❌ Smart scan failed: ',
cand_msg:'Found <b>{n}</b> documents across {g} top-level folders. Orange “likely noise” ones are unchecked by default; tick what you want and click “💾 Save & build index”.',
noise:'likely noise',
cand_tail:'(fiction/novel folders are excluded automatically and never listed here)',
cand_done:'Tick the folders you want, then click “💾 Save & build index”.',
test_hint:'🩺 Health check running (separate process, your real library is untouched)…',
test_busy:'Health check running…',
test_ok:'✅ Health check passed: ',test_bad:'❌ Health check failed: ',
test_report:' (full report in eval_report.txt)',
save_need:'Add at least one folder first',
saving:'Saving…',save_fail:'Save failed',
upd_need:'Add or select a folder to index first',
started:'Started, working in the background…',
ph_scan:'📂 Scanning files',ph_scan_end:'📄 Scan complete',ph_index:'🔎 Building index',ph_discover:'🧠 Discovering',ph_prune:'🧹 Removing stale entries',ph_working:'⏳ Working',
prog:'{ph} · {d}/{t} processed, {ok} ok, {f} failed',
done_reload:'✅ {m} — refreshing in 2 s…',
err_prefix:'❌ Error: ',
m_scanning:'scanning {p}',
m_scan_end:'found {n} documents, indexing…',
m_indexing:'building index ({n} documents)',
m_index_done:'done — {n} documents in library',
m_pruning:'removing stale entries',
m_pruned:'removed {n} stale entries',
m_update_done:'update complete',
m_discovering:'discovering (file names only, no contents)',
m_discover_target:'discovering {p}',
m_discover_done:'discovery complete: {n} documents',
m_selftest_busy:'health check running (retrieval / OCR accuracy / corrupted-file fuzz)…',
m_selftest_done:'health check finished',
e_no_scope:'scope.json not found — pick the folders to index in settings first',
e_need_dir:'At least one folder is required',
e_discover_busy:'A scan is already running',
e_selftest_busy:'A health check is already running',
e_update_busy:'An update is already running',
e_bad_body:'Malformed request body',
e_query:'Query error (3+ characters works best): {e}'
}};
const $=id=>document.getElementById(id);
const qEl=$('q'),tEl=$('t'),dEl=$('d'),rEl=$('r');
let LANG=(function(){
const q=(new URLSearchParams(location.search)).get('lang');   // ?lang=en 可强制指定语言
if(q==='zh'||q==='en')return q;
try{const v=localStorage.getItem('sb_lang');if(v==='zh'||v==='en')return v}catch(e){}
return (navigator.language||'zh').toLowerCase().indexOf('zh')===0?'zh':'en'})();
const T=k=>{const d=DICT[LANG];return d[k]!==undefined?d[k]:(DICT.zh[k]!==undefined?DICT.zh[k]:k)};
const F=(s,a)=>String(s).replace(/\{(\w+)\}/g,(m,k)=>a&&a[k]!==undefined?a[k]:m);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let HAS_RESULT=0;
function applyLang(){
document.documentElement.lang=(LANG==='zh'?'zh-CN':'en');
document.title=T('title');
document.querySelectorAll('[data-i18n]').forEach(el=>{el.innerHTML=T(el.dataset.i18n)});
document.querySelectorAll('[data-i18n-ph]').forEach(el=>{el.placeholder=T(el.dataset.i18nPh)});
document.querySelectorAll('[data-i18n-title]').forEach(el=>{el.title=T(el.dataset.i18nTitle)});
$('langbtn').textContent=(LANG==='zh'?'EN':'中文');
draw();
if(HAS_RESULT&&qEl.value.trim())go(); else if(!HAS_RESULT)showEmpty();
if($('pm').textContent&&DIRS.length)pm.textContent='';
}
function toggleLang(){LANG=(LANG==='zh'?'en':'zh');try{localStorage.setItem('sb_lang',LANG)}catch(e){}
applyLang();try{if(PROG_CACHE)renderProg(PROG_CACHE)}catch(e){}loadNames()}
function showEmpty(){rEl.className='empty';rEl.innerHTML=esc(T('empty_hint'))+'<br>'+T('empty_tip')}
let TOAST_T=0;
function toast(m){const el=$('toast');el.textContent=m;el.classList.add('on');clearTimeout(TOAST_T);TOAST_T=setTimeout(()=>el.classList.remove('on'),1800)}
function hl(s,qv){
const parts=String(s).split(/(\u0001[^\u0002]*\u0002)/);
const hasMark=parts.length>1;
return parts.map(p=>{
if(p.charAt(0)==='\u0001'&&p.charAt(p.length-1)==='\u0002')return '<mark>'+esc(p.slice(1,-1))+'</mark>';
let t=esc(p);
if(!hasMark&&qv){const ws=qv.trim().split(/\s+/).filter(x=>x.length>0);
for(const w of ws){try{t=t.replace(new RegExp(w.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi'),m=>'<mark>'+m+'</mark>')}catch(e){}}}
return t}).join('')}
function enc(p){return encodeURIComponent(p).replace(/'/g,'%27')}
async function go(){
const qv=qEl.value.trim(),tv=tEl.value.trim(),dv=dEl.value.trim();
if(!qv){HAS_RESULT=0;showEmpty();return}
rEl.className='empty';rEl.innerHTML=T('searching');
const t0=performance.now();let x,fx;
try{[x,fx]=await Promise.all([
fetch('/api?q='+encodeURIComponent(qv)+'&t='+encodeURIComponent(tv)+'&d='+encodeURIComponent(dv)+'&name=0').then(r=>r.json()),
fetch('/api/files?q='+encodeURIComponent(qv)).then(r=>r.json()).catch(()=>({items:[],st:'off'}))
])}catch(e){rEl.innerHTML=esc(String(e));return}
const ms=(performance.now()-t0).toFixed(0);
if(x.err){HAS_RESULT=0;rEl.innerHTML=esc(x.k?F(T(x.k),x.a||{e:x.err}):x.err);return}
HAS_RESULT=1;
// 正文里已经列出的文件不再重复出现在文件名区：两栏互补，不是两份清单
const hp=new Set(x.hits.map(h=>String(h.p).toLowerCase()));
const files=(fx&&fx.items)?fx.items.filter(f=>!hp.has(String(f.p).toLowerCase())):[];
if(!x.hits.length){rEl.className='';
if(!files.length){rEl.className='empty';rEl.innerHTML=F(T('no_hit'),{q:esc(qv)})+'<br>'+T('no_hit_tip');return}
rEl.innerHTML='<div class=meta style="text-align:center">'+F(T('ev_only'),{n:files.length})+'</div>'+evBlock(files,fx.st,fx.more);return}
rEl.className='';
rEl.innerHTML='<div class=meta>'+F(T('found'),{n:x.hits.length,ms:ms})+'</div>'+
(files.length?evBlock(files,fx.st,fx.more):'')+
x.hits.map(h=>{const i=h.p.lastIndexOf('\\'),nm=i<0?h.p:h.p.slice(i+1),pt=i<0?'':h.p.slice(0,i),
e=(h.p.split('.').pop()||'').toUpperCase().slice(0,4);
return '<div class=card><div class=top><span class=badge>'+esc(e)+'</span><span class=name title="'+esc(h.p)+'">'+esc(nm)+'</span>'+
'<button class=btn onclick="openF(\''+enc(h.p)+'\')">'+esc(T('open'))+'</button>'+
'<button class=btn onclick="copyP(this,\''+enc(h.p)+'\')">'+esc(T('copy'))+'</button></div>'+
'<div class=path>'+esc(pt)+'</div><div class=snip>'+hl(h.s,qv)+'</div></div>'}).join('')}
function evBlock(items,st,more){
return '<div class=evblock><div class=evhead>'+esc(T('ev_h'))+'<em>'+esc(T(st==='ok'?'ev_sub':'ev_sub_off'))+'</em></div>'+
items.map(f=>{const e=f.t==='folder'?'DIR':((f.n.split('.').pop()||'').toUpperCase().slice(0,4)||'FILE');
return '<div class=evitem><span class=badge>'+esc(e)+'</span>'+
'<span class=evname title="'+esc(f.p)+'">'+esc(f.n)+'</span>'+
(f.src==='ev'?'<span class=evtag>'+esc(T('ev_tag'))+'</span>':'')+
'<span class=evpath title="'+esc(f.d)+'">'+esc(f.d)+'</span>'+
'<button class=btn onclick="openF(\''+enc(f.p)+'\')">'+esc(T('open'))+'</button>'+
'<button class=btn onclick="copyP(this,\''+enc(f.p)+'\')">'+esc(T('copy'))+'</button></div>'}).join('')+
(more?'<div class=evmore>'+esc(T('ev_more'))+'</div>':'')+
'</div>'}
async function openF(p){try{const x=await(await fetch('/open?p='+p)).json();toast(x.ok?T('opened'):T('open_fail'))}
catch(e){toast(T('open_fail'))}}
async function copyP(b,p){try{await navigator.clipboard.writeText(decodeURIComponent(p));b.textContent=T('copied');setTimeout(()=>{b.textContent=T('copy')},1200)}
catch(e){toast(decodeURIComponent(p))}}
qEl.addEventListener('keydown',e=>{if(e.key==='Enter')go()});
document.addEventListener('keydown',e=>{
if(e.key==='Escape'){if(document.activeElement===qEl){qEl.value='';HAS_RESULT=0;showEmpty()}}
if((e.ctrlKey||e.metaKey)&&e.key==='f'){e.preventDefault();qEl.focus();qEl.select()}});
let DIRS=[],EXC=[],tid=0,PROG_CACHE=null;
async function cfgLoad(){try{const c=await(await fetch('/api/config')).json();DIRS=c.include_dirs||[];EXC=c.exclude_dirs||[];
if(!c.exists){showCfg(1);pm.textContent=T('first_run')}draw()}catch(e){}loadNames()}
function showCfg(wizard){cfg.classList.add('on');if(wizard)$('gear').style.display='none'}
function draw(){const el=$('dirs');if(!el)return;
el.innerHTML=DIRS.map((d,i)=>'<span class=chip>'+esc(d)+' <b onclick=delD('+i+')>✕</b></span>').join('')
||'<span class=pmsg>'+esc(T('no_dir'))+'</span>'}
async function addDir(){let p=null;
// 桌面窗口模式下才有原生文件夹选择器。注意不能用一个全局常量来判断：
// pywebview 注入 window.pywebview 的时机晚于本脚本执行，那时常量还是 false。
const api=window.pywebview&&window.pywebview.api;
if(api&&api.pick_folder){try{p=await api.pick_folder()}catch(e){p=null}}
if(!p)p=prompt(T('add_prompt'));
if(p){p=String(p).replace(/[\\/]+$/,'');if(!DIRS.includes(p))DIRS.push(p);draw()}}
function delD(i){DIRS.splice(i,1);draw()}
let CAND=[];
async function smartScan(b){b.disabled=1;$('pw').style.display='';$('pi').style.width='3%';
pm.textContent=T('scan_hint');
await fetch('/api/discover',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
clearInterval(tid);tid=setInterval(async()=>{let d;
try{d=await(await fetch('/api/discover')).json()}catch(e){return}
if(d.state==='busy'){try{const s=await(await fetch('/api/status')).json();
const pc=s.total>0?Math.min(100,Math.round(s.done/s.total*100)):10;$('pi').style.width=pc+'%';
pm.textContent='📂 '+(smsg(s)||T('ph_discover'));}catch(e){}}
else{clearInterval(tid);b.disabled=0;$('pw').style.display='none';
if(d.state==='ready')renderCand(d.groups);
else pm.textContent=T('scan_fail')+d.err}},1000)}
function renderCand(gs){CAND=gs;cfg.classList.add('on');
const total=gs.reduce((a,g)=>a+g.docs,0);
$('cand').innerHTML='<div class=pmsg>'+F(T('cand_msg'),{n:total,g:gs.length})+'</div>'+
gs.map((g,i)=>'<label class=crow><input type=checkbox '+(g.noise?'':'checked')+' onchange=candPick('+i+',this.checked)><span>'+esc(g.dir)+'</span>'+(g.noise?'<nz>'+esc(T('noise'))+'</nz>':'')+'<span class=n>'+g.docs+'</span></label>').join('')
+'<div class=pmsg>'+esc(T('cand_tail'))+'</div>';
DIRS=gs.filter(g=>!g.noise).map(g=>g.dir);draw();
pm.textContent=T('cand_done')}
function candPick(i,on){const d=CAND[i].dir;
if(on){if(!DIRS.includes(d))DIRS.push(d)}else{DIRS=DIRS.filter(x=>x!==d)}draw()}
async function loadNames(){try{const d=await(await fetch('/api/names')).json();
const el=$('nstat');if(!el)return;
el.innerHTML=d.count>0?F(T('nstat_on'),{n:d.count.toLocaleString()}):esc(T('nstat_off'));
}catch(e){}}
async function buildNames(b){b.disabled=1;$('pw').style.display='';$('pi').style.width='3%';
pm.textContent=T('m_names');
try{const r=await(await fetch('/api/names',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
if(!r.ok){pm.textContent=T(r.k)||r.err||'';b.disabled=0;$('pw').style.display='none';return}}
catch(e){pm.textContent=String(e);b.disabled=0;$('pw').style.display='none';return}
clearInterval(tid);tid=setInterval(async()=>{let d;
try{d=await(await fetch('/api/names')).json()}catch(e){return}
if(d.state==='busy'){try{const s=await(await fetch('/api/status')).json();
const pc=s.total>0?Math.min(100,Math.round(s.done/s.total*100)):10;$('pi').style.width=pc+'%';
pm.textContent='🗂 '+(smsg(s)||T('m_names'));}catch(e){}}
else{clearInterval(tid);b.disabled=0;$('pw').style.display='none';
pm.textContent=d.state==='done'?F(T('m_names_done'),{n:d.n}):((T(d.k)||'')+' '+(d.err||''));
loadNames()}},1000)}
async function selfTest(b){b.disabled=1;$('pw').style.display='';$('pi').style.width='5%';
pm.textContent=T('test_hint');
await fetch('/api/selftest',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
clearInterval(tid);tid=setInterval(async()=>{let s;
try{s=await(await fetch('/api/selftest')).json()}catch(e){return}
if(s.state==='busy'){$('pi').style.width='60%';pm.textContent=smsg(s)||T('test_busy')}
else{clearInterval(tid);b.disabled=0;$('pw').style.display='none';
pm.textContent=(s.ok?T('test_ok'):T('test_bad'))+(smsg(s)||'')+T('test_report')}},1500)}
async function saveUpd(b){if(!DIRS.length){pm.textContent=T('save_need');return}
b.disabled=1;pm.textContent=T('saving');
const r=await(await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({include_dirs:DIRS,exclude_dirs:EXC})})).json();
if(!r.ok){pm.textContent=r.k?T(r.k):(r.err||T('save_fail'));b.disabled=0;return}
upd(null)}
async function upd(b){if(!DIRS.length){pm.textContent=T('upd_need');return}
if(b)b.disabled=1;$('savebtn').disabled=1;$('updonly').disabled=1;$('smartbtn').disabled=1;
$('pw').style.display='';$('pi').style.width='2%';pm.textContent=T('started');
await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({targets:DIRS})});
poll()}
function smsg(o){if(!o)return'';
if(o.k&&DICT[LANG][o.k]!==undefined)return F(T(o.k),o.a||{});
return o.msg||o.err||''}
function renderProg(s){
if(s.state!=='busy')return;
const pc=s.total>0?Math.min(100,Math.round(s.done/s.total*100)):8;
$('pi').style.width=pc+'%';
const ph=T('ph_'+s.phase)||T('ph_working');
pm.textContent=F(T('prog'),{ph:ph,d:s.done,t:(s.total||'?'),ok:s.ok,f:s.fail})+(smsg(s)?' · '+smsg(s):'')}
function poll(){clearInterval(tid);tid=setInterval(async()=>{let s;
try{s=await(await fetch('/api/status')).json()}catch(e){return}
PROG_CACHE=s;
if(s.state==='busy'){s.phase&&DICT[LANG]['ph_'+s.phase]?renderProg(s):(()=>{const pc=s.total>0?Math.min(100,Math.round(s.done/s.total*100)):8;
$('pi').style.width=pc+'%';pm.textContent=T('ph_working')+(smsg(s)?' · '+smsg(s):'')})()}
else{clearInterval(tid);$('gear').style.display='';
$('savebtn').disabled=0;$('updonly').disabled=0;$('smartbtn').disabled=0;
if(s.state==='done'){$('pi').style.width='100%';pm.textContent=F(T('done_reload'),{m:smsg(s)||T('m_update_done')});
setTimeout(()=>location.reload(),2500)}
else if(s.state==='error'){$('pw').style.display='none';pm.textContent=T('err_prefix')+(smsg(s)||'')}}},800)}
cfgLoad();applyLang();
// ?q=关键词 直接出结果：方便收藏常用搜索，或从命令行直接打开结果页
const Q0=(new URLSearchParams(location.search)).get('q');
if(Q0){qEl.value=Q0;go()}
// ?cfg=1 打开即展开设置面板：供 --settings 启动参数、书签直达、截图
try{if((new URLSearchParams(location.search)).get('cfg'))showCfg()}catch(e){}
</script></body></html>'''
    import os
    from urllib.parse import urlparse, parse_qs

    def build_page():
        with con_lock:
            total = _count_docs(conn())
        return (PAGE_TPL.replace('__TOTAL__', f'{total:,}')
                        .replace('__VER__', APP_VERSION))

    class H(BaseHTTPRequestHandler):
        # 单线程 HTTPServer + 默认无超时：一个连上来却不把请求发完的客户端（页面刷新时
        # 被中断的请求、被占用的端口探测）会让后续请求全部排在这一条连接后面，
        # 界面表现就是「点了没反应」。给它一个读超时，并在下面换成多线程服务器。
        timeout = 15

        def log_message(self, *a):
            pass
        def _guard(self):
            """只信本机：Host 必须是 127.0.0.1/localhost，跨站 Origin 拒绝（防恶意网页
            借浏览器偷打本机接口），POST 只收 application/json"""
            host = (self.headers.get('Host') or '').lower()
            if not (host.startswith('127.0.0.1') or host.startswith('localhost')):
                return False
            org = self.headers.get('Origin')
            if org and '127.0.0.1' not in org and 'localhost' not in org:
                return False
            return True
        def do_GET(self):
            if not self._guard():
                self.send_response(403)
                self.end_headers()
                return
            if self.path.startswith('/api/selftest'):
                body = json.dumps(dict(SELFTEST), ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/ping'):
                # 供桌面启动器确认「这个端口上跑的是不是本应用」，防止端口被别的程序占用时显示错页面
                body = json.dumps({'app': 'second-brain', 'ver': APP_VERSION},
                                  ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/config'):
                sp = app_dir() / 'scope.json'
                with con_lock:
                    n_docs = _count_docs(conn())
                cfg = {'exists': sp.exists(), 'include_dirs': [], 'exclude_dirs': [],
                       'app_dir': str(app_dir()),
                       'docs': n_docs}
                if sp.exists():
                    try:
                        c = json.loads(sp.read_text(encoding='utf-8'))
                        cfg['include_dirs'] = c.get('include_dirs', [])
                        cfg['exclude_dirs'] = c.get('exclude_dirs', [])
                    except Exception:
                        cfg['broken'] = True
                body = json.dumps(cfg, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/status'):
                body = json.dumps(dict(PROG), ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/discover'):
                body = json.dumps(dict(DISCOVER), ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/names'):
                # 全盘文件名索引的构建进度 + 当前规模
                try:
                    with con_lock:
                        cnt = file_count(conn())
                except sqlite3.Error:
                    cnt = 0
                body = json.dumps({'state': NAMES.get('state'), 'n': NAMES.get('n', 0),
                                   'count': cnt, 'err': NAMES.get('err', ''),
                                   'k': NAMES.get('k', '')}, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/files'):
                # 第二路结果：文件名。已入库的排前面（用户真正当资料看的那些），
                # 再用自建的全盘文件名索引补齐。两者都没有时才退回 Everything ——
                # 那条路径只为了照顾「装了 Everything 但还没建过文件名索引」的老用户。
                qs = parse_qs(urlparse(self.path).query)
                q = (qs.get('q', [''])[0] or '').strip()
                items, st, more = [], 'off', False
                if q:
                    try:
                        with con_lock:
                            c = conn()
                            local = name_rows(c, q, 4)
                            has_disk = file_count(c) > 0
                            disk = file_rows(c, q, 14) if has_disk else []
                    except sqlite3.Error:
                        local, disk, has_disk = [], [], False
                    for p in local:
                        items.append({'n': os.path.basename(p), 'p': p,
                                      'd': os.path.dirname(p), 't': 'file', 'src': 'idx'})
                    seen = {os.path.normcase(x['p']) for x in items}
                    for p in disk:
                        key = os.path.normcase(p)
                        if key in seen:
                            continue
                        items.append({'n': os.path.basename(p), 'p': p,
                                      'd': os.path.dirname(p), 't': 'file', 'src': 'disk'})
                        seen.add(key)
                    if has_disk:
                        st = 'ok'
                    else:
                        ev, st = ev_search(q, 7)
                        for x in ev:
                            if os.path.normcase(x['p']) not in seen:
                                x['src'] = 'ev'
                                items.append(x)
                                seen.add(os.path.normcase(x['p']))
                    # 这一栏是「先告诉你有没有」，不是列表页：多了会把正文结果挤到屏幕外
                    more = len(items) > 6
                    items = items[:6]
                body = json.dumps({'items': items, 'st': st, 'more': more},
                                  ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/mini'):
                # 双击 Ctrl 弹出的快搜窗。deep=0 只做前缀（走索引，毫秒级），
                # deep=1 才全表扫「名字任意位置包含」，由前端在结果不满一屏时补发；
                # body=1 连正文一起查（两家长处都不在正文，这是第二大脑的差异点）。
                qs = parse_qs(urlparse(self.path).query)
                q = (qs.get('q', [''])[0] or '').strip()
                deep = qs.get('deep', ['0'])[0] in ('1', 'true')
                want_body = qs.get('body', ['0'])[0] in ('1', 'true')
                try:
                    with con_lock:
                        d = mini_search(conn(), q, 9, deep, want_body)
                except sqlite3.Error as e:
                    d = {'items': [], 'deep': deep, 'ms': 0, 'n': 0, 'err': str(e)}
                if d['items']:
                    allow_open([x['p'] for x in d['items']])
                body = json.dumps(d, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/icon'):
                # 系统图标。按扩展名取，和资源管理器里看到的是同一张图；
                # 拿不到就回 204，前端换回自绘图标。
                qs = parse_qs(urlparse(self.path).query)
                ext = (qs.get('e', [''])[0] or '').strip().lower()[:12]
                isdir = qs.get('d', ['0'])[0] in ('1', 'true') or ext == 'dir'
                png = sys_icon('' if isdir else ext, isdir)
                if png:
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('Cache-Control', 'max-age=86400')
                    self.send_header('Content-Length', str(len(png)))
                    self.end_headers()
                    self.wfile.write(png)
                    return
                self.send_response(204)
                self.end_headers()
                return
            elif self.path.startswith('/api/recent'):
                # 快搜窗的空状态：打开就先给一屏「你刚动过的文件」，不用先打字。
                try:
                    with con_lock:
                        items = recent_rows(conn(), 5)
                except sqlite3.Error:
                    items = []
                if items:
                    allow_open([x['p'] for x in items])
                body = json.dumps({'items': items}, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api?'):
                qs = parse_qs(urlparse(self.path).query)
                q = qs.get('q', [''])[0]
                ext = qs.get('t', [''])[0] or None
                pf = qs.get('d', [''])[0] or None
                # 界面上文件名另有独立一栏，这里传 name=0 关掉垫底补位，免得同一条出现两次
                wn = qs.get('name', ['1'])[0] != '0'
                try:
                    hits = [{'p': p, 's': s} for p, s in self._search(q, ext, pf, wn)]
                    body = json.dumps({'hits': hits}, ensure_ascii=False).encode()
                except Exception as e:
                    body = json.dumps({'err': f'查询出错: {e}', 'k': 'e_query', 'a': {'e': str(e)}},
                                      ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/open?'):
                # 只允许打开**索引里的文件**，并且拒绝跨站发起。
                # 光靠 Host/Origin 校验不够：浏览器用 <img src> / <a ping> 这类简单 GET
                # 打本机接口时并不发 Origin，于是任意网页都能让这里执行 os.startfile，
                # 而 startfile 对 .exe 就是「运行」——等于给了一个远程执行入口。
                p = parse_qs(urlparse(self.path).query).get('p', [''])[0]
                site = (self.headers.get('Sec-Fetch-Site') or '').lower()
                ok = False
                if p and site not in ('cross-site', 'same-site', 'none'):
                    try:
                        with con_lock:
                            ok = bool(conn().execute(
                                'SELECT 1 FROM meta WHERE path=? LIMIT 1', (p,)).fetchone())
                    except sqlite3.Error:
                        ok = False
                    # Everything 那一列多半是索引外的文件；放行条件收紧成「本次搜索真的返回过」，
                    # 否则这里就成了任意路径的 startfile 入口（对 .exe 等于运行）
                    ok = ok or ev_recent(p)
                    if ok:
                        try:
                            os.startfile(p)
                            with con_lock:
                                record_open(conn(), p, os.path.isdir(p))
                        except Exception:
                            ok = False
                body = json.dumps({'ok': ok}, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/mini'):
                body = MINI_TPL.encode()
                ctype = 'text/html; charset=utf-8'
            else:
                body = build_page().encode()
                ctype = 'text/html; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            if not self._guard() or 'application/json' not in (self.headers.get('Content-Type') or ''):
                self.send_response(403)
                self.end_headers()
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = 0                       # Content-Length 是垃圾时当空 body 处理
            try:
                req = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
            except Exception:
                req = {}
            if not isinstance(req, dict):
                # 请求体是数组/字符串时 req.get() 会抛 AttributeError，连接被直接掐断，
                # 前端只看到「Failed to fetch」。统一回一个 400。
                body = json.dumps({'ok': False, 'err': '请求体必须是 JSON 对象',
                                   'k': 'e_bad_body'}, ensure_ascii=False).encode()
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith('/api/config'):
                dirs = [str(d).strip() for d in req.get('include_dirs', []) if str(d).strip()]
                exc = [str(d).strip() for d in req.get('exclude_dirs', []) if str(d).strip()]
                if not dirs:
                    body = json.dumps({'ok': False, 'err': '至少要添加一个文件夹', 'k': 'e_need_dir'},
                                      ensure_ascii=False).encode()
                else:
                    sp = app_dir() / 'scope.json'
                    sp.write_text(json.dumps(
                        {'scan_csv': 'scan_raw.csv', 'include_dirs': dirs, 'exclude_dirs': exc},
                        ensure_ascii=False, indent=2), encoding='utf-8')
                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/names'):
                if NAMES.get('state') == 'busy' or PROG.get('state') == 'busy':
                    body = json.dumps({'ok': False, 'err': '已有任务在跑', 'k': 'e_names_busy'},
                                      ensure_ascii=False).encode()
                else:
                    threading.Thread(target=build_files_index, daemon=True).start()
                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/discover'):
                if DISCOVER['state'] == 'busy':
                    body = json.dumps({'ok': False, 'err': '智能扫描正在进行', 'k': 'e_discover_busy'}, ensure_ascii=False).encode()
                else:
                    targets = req.get('targets')
                    if targets is not None:
                        targets = [str(t) for t in targets]
                    threading.Thread(target=discover_scan, args=(targets,), daemon=True).start()
                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/selftest'):
                if SELFTEST['state'] == 'busy':
                    body = json.dumps({'ok': False, 'err': '自检已在进行', 'k': 'e_selftest_busy'}, ensure_ascii=False).encode()
                else:
                    run_selftest_async()
                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            elif self.path.startswith('/api/update'):
                if PROG['state'] == 'busy':
                    body = json.dumps({'ok': False, 'err': '已有更新在进行', 'k': 'e_update_busy'}, ensure_ascii=False).encode()
                else:
                    targets = req.get('targets')
                    if targets is not None:
                        targets = [str(t) for t in targets]
                    threading.Thread(target=update, args=(targets,), daemon=True).start()
                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            else:
                body = json.dumps({'ok': False}, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.end_headers()
            self.wfile.write(body)
        def _search(self, q, ext=None, pf=None, with_name=True):
            with con_lock:
                return search_rows(conn(), q, 30, ext, pf, with_name)
    class QuietServer(ThreadingHTTPServer):
        """多线程 + 安静：单线程时一个不发完请求的连接会把界面卡住（见 H.timeout）。
        连接被对端中断（关窗口、刷新、探测端口）是常态，不该往控制台吐 traceback。"""
        daemon_threads = True

        def handle_error(self, request, client_address):
            et = sys.exc_info()[0]
            if et in (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                return
            super().handle_error(request, client_address)

    return QuietServer(('127.0.0.1', port), H)

def serve(port=8765):
    """浏览器模式：直接跑网页界面；想要独立桌面窗口用 app_window.py"""
    import webbrowser
    try:
        srv = make_server(port)
    except OSError:
        # 端口被占（常见：上次的窗口没关干净）。往上找几个空位，别直接甩一段报错出来
        for p in range(port + 1, port + 12):
            try:
                srv, port = make_server(p), p
                break
            except OSError:
                continue
        else:
            print(f'[!] {port}-{port + 11} 都被占用，请关掉占用端口的程序后重试')
            sys.exit(1)
    threading.Timer(1.2, webbrowser.open, args=(f'http://127.0.0.1:{port}',)).start()
    print(f'第二大脑已启动：http://127.0.0.1:{port}（关闭本窗口即停止服务）')
    start_pinyin_backfill()      # 老库补拼音，后台跑，不挡界面
    srv.serve_forever()

def cli(argv=None):
    """命令行入口（python brain.py …；打包后 exe 的同名子命令走同一套逻辑）"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        serve()
        return
    cmd = argv[0]
    if cmd == 'index':
        if len(argv) < 2:
            print('用法：python brain.py index scope.json\n'
                  '      （没有 scope.json 就用 python brain.py 打开界面，点「智能扫描」自动生成）')
            sys.exit(1)
        index(argv[1])
    elif cmd == 'update':
        update()
    elif cmd == 'search':
        if len(argv) < 2:
            print('用法：python brain.py search 关键词 [条数] [扩展名] [路径过滤]')
            sys.exit(1)
        search(argv[1], int(argv[2]) if len(argv) > 2 else 20,
               argv[3] if len(argv) > 3 else None, argv[4] if len(argv) > 4 else None)
    elif cmd == 'prune':
        prune()
    elif cmd == '_extract':
        # 内部子命令：由 extract_text_safe() 以子进程方式调用，结果写到临时文件。
        # 不走 stdout 管道——管道会让母进程在超时收尾时永久阻塞。
        try:
            # 'p' 是请求的路径：回传文件是全进程复用的，父进程靠它确认这份结果没串篇
            payload = {'p': argv[1], 't': extract_text(Path(argv[1])), 'w': None}
        except Exception as e:
            payload = {'p': argv[1], 't': None, 'w': f'解析异常（{type(e).__name__}）'}
        Path(argv[2]).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    elif cmd == 'serve':
        serve(int(argv[1]) if len(argv) > 1 else 8765)
    elif cmd in ('selftest', 'eval'):
        import selftest
        ok = selftest.run_all(verbose=True, write_report=True)
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)

if __name__ == '__main__':
    cli()
