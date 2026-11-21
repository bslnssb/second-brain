# -*- coding: utf-8 -*-
"""clipboard_mgr.py — 跨开机长效剪贴板历史管理器（uTools 风格）

功能特性：
1. 监听系统剪贴板更新（AddClipboardFormatListener / WM_CLIPBOARDUPDATE，0 CPU 轮询占用）
2. 自动捕获：
   - 文本（纯文本、代码、链接，记录字符数、摘要预览、完整内容）
   - 图片（截图、位图，生成轻量缩略图持久化在本地，记录分辨率宽高与时间）
   - 文件（从资源管理器复制的文件/文件夹路径列表，记录主文件名、项目总数）
3. 去重与更新：
   - 内容哈希去重；若重复复制相同内容，仅更新最近时间戳并将其置顶
   - 忽略密码管理器（Clipboard Viewer Ignore 等）标记的敏感内容
   - 自复制防重入（_IGNORE_NEXT_CLIP 保护）
4. 查询、写回、删除与清空：
   - list_history: 支持分类过滤（all / text / image / file）与实时关键词搜索
   - copy_item: 将指定历史项写回 Windows 剪贴板（文本/文件/图片多格式支持）
   - delete_item: 删除单条历史与关联缩略图
   - clear_history: 清空所有未置顶历史
   - toggle_pin: 置顶/取消置顶
"""
import ctypes
import hashlib
import json
import re
import os
import sqlite3
import struct
import sys
import threading
import time
from pathlib import Path
from PIL import Image, ImageGrab

try:
    import win32clipboard as clip
    import win32con
    import win32gui
except ImportError:
    clip = None
    win32con = None
    win32gui = None

# 第 277 项：剪贴板埋点。这个功能最典型的故障形态是「复制了但没进历史」，
# 不是异常 —— 打开剪贴板失败、枚举格式失败、写入失败都只吞掉，用户毫无线索。
try:
    import feature_metrics as _metrics
except Exception:                                     # pragma: no cover
    _metrics = None


def _cm(status='ok', sub=None, err=''):
    if _metrics is None:
        return
    try:
        _metrics.add('clipboard', status, sub=sub, err=str(err)[:120])
    except Exception:
        pass

u32 = ctypes.windll.user32 if hasattr(ctypes, 'windll') else None
WM_CLIPBOARDUPDATE = 0x031D

_DB_PATH = None
_THUMB_DIR = None
_LISTENER_THREAD = None
_WORKER_THREAD = None
_UPDATE_EVENT = threading.Event()
_LISTENER_HWND = None
_RUNNING = False
_LOCK = threading.Lock()
_IGNORE_NEXT_CLIP = False
_LAST_HASH = None


def _log(msg):
    try:
        import brain
        brain._log(f"[clipboard] {msg}")
    except Exception:
        pass


def get_thumb_dir():
    global _THUMB_DIR
    if _THUMB_DIR:
        return _THUMB_DIR
    try:
        import brain
        d = brain.app_dir() / 'clip_thumbs'
    except Exception:
        d = Path(os.environ.get('LOCALAPPDATA', '')) / 'Memex' / 'clip_thumbs'
    d.mkdir(parents=True, exist_ok=True)
    _THUMB_DIR = d
    return d


def get_db_path():
    global _DB_PATH
    if _DB_PATH:
        return _DB_PATH
    try:
        import brain
        _DB_PATH = Path(brain.DB)
    except Exception:
        _DB_PATH = Path(os.environ.get('LOCALAPPDATA', '')) / 'Memex' / 'brain.db'
    return _DB_PATH


def set_db_path(p):
    global _DB_PATH
    _DB_PATH = Path(p)


# 第 339 项：老库缺 thumb 列时自动补上（每个库文件只查一次 PRAGMA，不拖慢每次取连接）。
_MIGRATED = set()


def get_con():
    p = get_db_path()
    con = sqlite3.connect(str(p), timeout=5, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        key = str(p)
        if key not in _MIGRATED:
            cols = {r[1] for r in con.execute("PRAGMA table_info(clipboard_history)")}
            if cols:
                if 'thumb' not in cols:
                    con.execute("ALTER TABLE clipboard_history ADD COLUMN thumb TEXT")
                    con.commit()
                _MIGRATED.add(key)
    except Exception:
        pass
    return con


def init_db(con=None):
    close = False
    if con is None:
        con = get_con()
        close = True
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS clipboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,         -- 'text' | 'image' | 'file'
                content TEXT NOT NULL,      -- text, thumbnail filename, or JSON list of paths
                preview TEXT,               -- search/display text preview
                detail TEXT,                -- '11 字符' / '1920×1080' / '3 个文件'
                hash TEXT,                  -- sha1 for deduplication
                created_at REAL NOT NULL,   -- time.time()
                pinned INTEGER DEFAULT 0    -- 1 if pinned, 0 otherwise
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_clip_created ON clipboard_history(created_at DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_clip_kind ON clipboard_history(kind)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_clip_hash ON clipboard_history(hash)")
        # 第 339 项：Ctrl+C 复制的**图片文件**（CF_HDROP 里只有路径、没有像素）原来只能显示成
        # 「1 个项目 + 📁」，用户看着以为是文件夹。现在给它补一张缩略图，列在这里。
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(clipboard_history)")}
            if 'thumb' not in cols:
                con.execute("ALTER TABLE clipboard_history ADD COLUMN thumb TEXT")
        except Exception:
            pass
        con.commit()
    finally:
        if close:
            con.close()


# 第 281 项：用户「剪切板里面还是一堆中文」——「41 字符 / 3 个项目 / 5 分钟前」都是中文。
# 这些字符串原来在**写入时就拼成中文存进库**，界面一切语言就露馅。现在写库那套保留（兼容老数据），
# 读的时候按界面语言重拼一遍。
_CLIP_L = {
    'zh-CN': {'now': '刚刚', 'sec': '{n}秒前', 'min': '{n}分钟前', 'hour': '{n}小时前',
              'yest': '昨天', 'day': '{n}天前', 'chars': '{n} 字符',
              'item1': '1 个项目', 'items': '{n} 个项目', 'img': '图片'},
    'en': {'now': 'Just now', 'sec': '{n}s ago', 'min': '{n} min ago', 'hour': '{n} h ago',
           'yest': 'Yesterday', 'day': '{n} d ago', 'chars': '{n} chars',
           'item1': '1 item', 'items': '{n} items', 'img': 'Image'},
    'es': {'now': 'Ahora mismo', 'sec': 'Hace {n} s', 'min': 'Hace {n} min',
           'hour': 'Hace {n} h', 'yest': 'Ayer', 'day': 'Hace {n} d',
           'chars': '{n} caracteres', 'item1': '1 elemento', 'items': '{n} elementos',
           'img': 'Imagen'},
    'pt': {'now': 'Agora mesmo', 'sec': 'Há {n} s', 'min': 'Há {n} min', 'hour': 'Há {n} h',
           'yest': 'Ontem', 'day': 'Há {n} d', 'chars': '{n} caracteres',
           'item1': '1 item', 'items': '{n} itens', 'img': 'Imagem'},
    'hi': {'now': 'अभी', 'sec': '{n} सेकंड पहले', 'min': '{n} मिनट पहले',
           'hour': '{n} घंटे पहले', 'yest': 'कल', 'day': '{n} दिन पहले',
           'chars': '{n} अक्षर', 'item1': '1 आइटम', 'items': '{n} आइटम', 'img': 'चित्र'},
}
# 复制/失败提示（copy_item 用；原来一律返回中文，非中文界面会冒出中文 toast）
_CLIP_EXTRA = {
    'zh-CN': {
        'copied_text': '已复制文本到剪贴板',
        'copied_files': '已复制 {n} 个文件到剪贴板',
        'copied_img': '已复制图片到剪贴板',
        'copy_fail': '复制失败: {e}',
        'thumb_missing': '缩略图文件已丢失',
        'img_write_fail': '写剪贴板失败',
        'unsupported': '不支持的格式', 'not_found': '未找到该记录',
    },
    'en': {
        'copied_text': 'Copied text to clipboard',
        'copied_files': 'Copied {n} file(s) to clipboard',
        'copied_img': 'Copied image to clipboard',
        'copy_fail': 'Copy failed: {e}',
        'thumb_missing': 'Thumbnail file is missing',
        'img_write_fail': 'Failed to write to the clipboard',
        'unsupported': 'Unsupported format', 'not_found': 'Item not found',
    },
    'es': {
        'copied_text': 'Texto copiado al portapapeles',
        'copied_files': 'Se copiaron {n} archivo(s) al portapapeles',
        'copied_img': 'Imagen copiada al portapapeles',
        'copy_fail': 'Error al copiar: {e}',
        'thumb_missing': 'Falta el archivo de miniatura',
        'img_write_fail': 'No se pudo escribir en el portapapeles',
        'unsupported': 'Formato no compatible', 'not_found': 'No se encontró el elemento',
    },
    'pt': {
        'copied_text': 'Texto copiado para a área de transferência',
        'copied_files': '{n} arquivo(s) copiado(s) para a área de transferência',
        'copied_img': 'Imagem copiada para a área de transferência',
        'copy_fail': 'Falha ao copiar: {e}',
        'thumb_missing': 'Arquivo de miniatura ausente',
        'img_write_fail': 'Falha ao gravar na área de transferência',
        'unsupported': 'Formato não suportado', 'not_found': 'Item não encontrado',
    },
    'hi': {
        'copied_text': 'टेक्स्ट क्लिपबोर्ड पर कॉपी हो गया',
        'copied_files': '{n} फ़ाइलें क्लिपबोर्ड पर कॉपी हुईं',
        'copied_img': 'चित्र क्लिपबोर्ड पर कॉपी हो गया',
        'copy_fail': 'कॉपी नहीं हो सका: {e}',
        'thumb_missing': 'थंबनेल फ़ाइल गायब है',
        'img_write_fail': 'क्लिपबोर्ड पर लिखा नहीं जा सका',
        'unsupported': 'यह फ़ॉर्मैट समर्थित नहीं है', 'not_found': 'यह आइटम नहीं मिला',
    },
}
for _k, _v in _CLIP_EXTRA.items():
    _CLIP_L.setdefault(_k, {}).update(_v)



def _clip_lang(lang):
    code = str(lang or '').strip()
    if code in _CLIP_L:
        return code
    return {'zh': 'zh-CN', 'zh-cn': 'zh-CN', 'zh_cn': 'zh-CN', 'cn': 'zh-CN'}.get(
        code.lower(), 'zh-CN')


def format_relative_time(ts, lang='zh-CN'):
    L = _CLIP_L[_clip_lang(lang)]
    now = time.time()
    diff = max(0, int(now - ts))
    if diff < 5:
        return L['now']
    if diff < 60:
        return L['sec'].replace('{n}', str(diff))
    if diff < 3600:
        return L['min'].replace('{n}', str(diff // 60))
    if diff < 86400:
        return L['hour'].replace('{n}', str(diff // 3600))
    if diff < 86400 * 2:
        return L['yest']
    if diff < 86400 * 7:
        return L['day'].replace('{n}', str(diff // 86400))
    return time.strftime('%m-%d %H:%M', time.localtime(ts))


def _clip_msg(key, lang='zh-CN', **kw):
    """剪贴板操作提示（复制成功 / 失败原因）按界面语言出。"""
    L = _CLIP_L[_clip_lang(lang)]
    s = L.get(key) or _CLIP_L['zh-CN'].get(key, key)
    for k, v in kw.items():
        s = s.replace('{%s}' % k, str(v))
    return s


def format_preview(kind, preview, stored, lang='zh-CN'):
    """图片条目的标题原来写死「图片 [1668×655]」——中文界的用户在印地语界面照样看到「图片」。
    读的时候按语言重拼；尺寸从存的 preview/detail 里正则取，老数据不用迁移。"""
    if kind != 'image':
        return preview or ''
    L = _CLIP_L[_clip_lang(lang)]
    m = re.search(r'(\d+)\s*[×x]\s*(\d+)', '%s %s' % (preview or '', stored or ''))
    if m:
        return '%s [%s×%s]' % (L['img'], m.group(1), m.group(2))
    return L['img']


def format_detail(kind, content, stored, lang='zh-CN'):
    """文本给字符数、文件给项目数、图片保留 '1920×1080'（这个是语言无关的）。"""
    L = _CLIP_L[_clip_lang(lang)]
    try:
        if kind == 'text':
            return L['chars'].replace('{n}', str(len(str(content or ''))))
        if kind == 'file':
            # 第 339 项：单个图片文件存的是纯尺寸（'248×503'）——当图片显示，不再说「1 个项目」。
            if re.fullmatch(r'\d+\s*[×x]\s*\d+', str(stored or '').strip()):
                return '%s %s' % (L['img'], str(stored).strip())
            try:
                n = len(json.loads(content or '[]'))
            except Exception:
                n = 1
            tpl = L['item1'] if n == 1 else L['items']
            return tpl.replace('{n}', str(n))
        if kind == 'image':
            return str(stored or '') or L['img']
    except Exception:
        pass
    return str(stored or '')


def _save_image_thumb(img, h):
    d = get_thumb_dir()
    fn = f"{h[:16]}.png"
    p = d / fn
    if not p.is_file():
        thumb = img.copy()
        thumb.thumbnail((360, 240), Image.Resampling.LANCZOS)
        thumb.save(str(p), format='PNG', optimize=True)
    return fn


# 第 339 项：能被当作「图片」显示的剪贴板文件扩展名。
_CLIP_IMG_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff')


def file_thumb(path_str):
    """单个图片文件 → (缩略图文件名, '宽×高')；不是图片/读不了就 (None, '')。

    剪贴板里「复制一个图片文件」走的是 CF_HDROP（只有路径），所以历史里只剩文件名 +
    「1 个项目」。用户看到的就是一张图片却标着项目——这里补一张缩略图，
    盘上已经有了的老条目由 list_history 顺手回填，不用迁移。
    """
    try:
        p = Path(str(path_str))
        if p.suffix.lower() not in _CLIP_IMG_EXT or not p.is_file():
            return None, ''
        if p.stat().st_size > 80 * 1024 * 1024:
            return None, ''
        with Image.open(str(p)) as im:
            w, h = im.size
            fn = _save_image_thumb(im.convert('RGB'),
                                   hashlib.sha1(str(p.resolve()).encode('utf-8')).hexdigest())
        return fn, f"{w}×{h}"
    except Exception:
        return None, ''


def record_clip(kind, content, preview, detail, h, thumb=None):
    global _LAST_HASH
    with _LOCK:
        if h and h == _LAST_HASH:
            # 同样的内容重复复制，只更新时间戳置顶
            try:
                con = get_con()
                cur = con.execute("UPDATE clipboard_history SET created_at = ? WHERE hash = ?", (time.time(), h))
                con.commit()
                if cur.rowcount > 0:
                    con.close()
                    _cm('ok', 'dedupe')
                    return
                con.close()
            except Exception:
                pass

        con = get_con()
        try:
            # 检查数据库中是否已存在该 hash
            cur = con.execute("SELECT id FROM clipboard_history WHERE hash = ? LIMIT 1", (h,))
            row = cur.fetchone()
            now = time.time()
            if row:
                if thumb:
                    con.execute("UPDATE clipboard_history SET created_at = ?, thumb = ? WHERE id = ?",
                                (now, thumb, row['id']))
                else:
                    con.execute("UPDATE clipboard_history SET created_at = ? WHERE id = ?", (now, row['id']))
            else:
                con.execute("""
                    INSERT INTO clipboard_history (kind, content, preview, detail, hash, created_at, pinned, thumb)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """, (kind, content, preview, detail, h, now, thumb))
            con.commit()
            _LAST_HASH = h
            _cm('ok', kind)
            # 自动维护容量上限：最多保留 1000 条未置顶记录
            _prune_history(con, keep=1000)
        finally:
            con.close()


def _prune_history(con, keep=1000):
    try:
        cur = con.execute("""
            SELECT id, kind, content FROM clipboard_history
            WHERE pinned = 0
            ORDER BY created_at DESC
            LIMIT -1 OFFSET ?
        """, (keep,))
        old_rows = cur.fetchall()
        if old_rows:
            ids = [r['id'] for r in old_rows]
            con.execute(f"DELETE FROM clipboard_history WHERE id IN ({','.join('?' for _ in ids)})", ids)
            con.commit()
            # 清理失效缩略图
            d = get_thumb_dir()
            for r in old_rows:
                if r['kind'] == 'image' and r['content']:
                    tp = d / r['content']
                    if tp.is_file():
                        try:
                            tp.unlink()
                        except OSError:
                            pass
    except Exception:
        pass


def _process_clipboard():
    global _IGNORE_NEXT_CLIP, _LISTENER_HWND
    if _IGNORE_NEXT_CLIP:
        _IGNORE_NEXT_CLIP = False
        return

    if not clip:
        return

    # 尝试打开剪贴板读取格式（带重试与 HWND 归属绑定）
    opened = False
    for _ in range(10):
        try:
            if _LISTENER_HWND:
                clip.OpenClipboard(_LISTENER_HWND)
            else:
                clip.OpenClipboard()
            opened = True
            break
        except Exception:
            time.sleep(0.03)

    if not opened:
        # 第 277 项：剪贴板被别的程序长期占着（实测出过 WinError 1418）。
        # 以前只悄悄 return，用户只会觉得「复制没进历史」，没有任何线索。
        _cm('fail', 'open')
        return

    try:
        formats = []
        try:
            f = clip.EnumClipboardFormats(0)
            while f:
                formats.append(f)
                f = clip.EnumClipboardFormats(f)
        except Exception:
            _cm('fail', 'enum')

        # 检查是否包含密码管理器敏感标记
        for fmt in formats:
            try:
                name = clip.GetClipboardFormatName(fmt)
                if name in ('Clipboard Viewer Ignore', 'ExcludeClipboardContentFromMonitor'):
                    return
            except Exception:
                pass

        # 1. 优先判定文件类型 (CF_HDROP = 15)
        if win32con.CF_HDROP in formats:
            try:
                paths = clip.GetClipboardData(win32con.CF_HDROP)
                if paths and len(paths) > 0:
                    content_json = json.dumps(list(paths), ensure_ascii=False)
                    first_name = Path(paths[0]).name
                    # 第 339 项：复制的是**一张图片文件**时顺手做缩略图，显示成图片而不是「项目」。
                    tfn, dim = (None, '')
                    if len(paths) == 1:
                        tfn, dim = file_thumb(str(paths[0]))
                    if len(paths) == 1:
                        preview = first_name
                        detail = dim or '1 个项目'
                    else:
                        preview = f"{first_name} 等 {len(paths)} 个项目"
                        detail = f"{len(paths)} 个项目"
                    h = hashlib.sha1(content_json.encode('utf-8')).hexdigest()
                    clip.CloseClipboard()
                    opened = False
                    record_clip('file', content_json, preview, detail, h, thumb=tfn)
                    return
            except Exception:
                pass

        # 2. 判定图片类型 (CF_DIB / CF_BITMAP)
        is_image = bool(win32con.CF_DIB in formats or getattr(win32con, 'CF_DIBV5', 17) in formats or win32con.CF_BITMAP in formats)
        is_text = bool(win32con.CF_UNICODETEXT in formats or win32con.CF_TEXT in formats)

        # 如果只有图片，或图片优先级明确高于文本
        if is_image and not is_text:
            clip.CloseClipboard()
            opened = False
            try:
                im = ImageGrab.grabclipboard()
                if isinstance(im, Image.Image):
                    w, h_px = im.size
                    # 计算图像数据指纹
                    raw_hash = hashlib.sha1(im.tobytes()[:65536]).hexdigest()
                    fn = _save_image_thumb(im, raw_hash)
                    record_clip('image', fn, f"[{w}×{h_px}]", f"{w}×{h_px}", raw_hash)
                    return
            except Exception:
                pass

        # 3. 判定文本类型 (CF_UNICODETEXT = 13)
        if is_text:
            try:
                text = clip.GetClipboardData(win32con.CF_UNICODETEXT)
                if text and text.strip():
                    # 避免极端超大文件挤爆内存（上限 500KB 文本）
                    text_store = text if len(text) <= 500000 else text[:500000] + '\n...(文本过长已截断)'
                    preview = text.strip().replace('\r', ' ').replace('\n', ' ')
                    if len(preview) > 160:
                        preview = preview[:160] + '...'
                    detail = f"{len(text)} 字符"
                    h = hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()
                    clip.CloseClipboard()
                    opened = False
                    record_clip('text', text_store, preview, detail, h)
                    return
            except Exception:
                pass

        # 4. 混合格式：如果含有图片，再兜底尝试 grabclipboard
        if is_image and opened:
            clip.CloseClipboard()
            opened = False
            try:
                im = ImageGrab.grabclipboard()
                if isinstance(im, Image.Image):
                    w, h_px = im.size
                    raw_hash = hashlib.sha1(im.tobytes()[:65536]).hexdigest()
                    fn = _save_image_thumb(im, raw_hash)
                    record_clip('image', fn, f"[{w}×{h_px}]", f"{w}×{h_px}", raw_hash)
                    return
            except Exception:
                pass

    finally:
        if opened:
            try:
                clip.CloseClipboard()
            except Exception:
                pass


def list_history(kind='all', q='', limit=60, offset=0, lang='zh-CN'):
    con = get_con()
    try:
        where = []
        args = []
        if kind in ('fav', 'pinned'):
            where.append("pinned = 1")
        elif kind and kind != 'all':
            if kind == 'image':
                # 第 339 项：「图片」这一栏也要带上「复制的是图片文件」那种条目
                where.append("(kind = 'image' OR (kind = 'file' AND thumb IS NOT NULL))")
            else:
                where.append("kind = ?")
                args.append(kind)
        if q and q.strip():
            where.append("(preview LIKE ? OR content LIKE ?)")
            pat = f"%{q.strip()}%"
            args.extend([pat, pat])

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"""
            SELECT id, kind, content, preview, detail, hash, created_at, pinned, thumb
            FROM clipboard_history
            {where_sql}
            ORDER BY pinned DESC, created_at DESC
            LIMIT ? OFFSET ?
        """
        args.extend([int(limit), int(offset)])
        cur = con.execute(sql, args)
        rows = []
        backfilled = 0
        for r in cur.fetchall():
            thumb = r['thumb']
            detail_src = r['detail']
            # 第 339 项：老条目（改造前复制进来的图片文件）这里顺手补缩略图，
            # 只对「单个路径 + 图片扩展名」动手，一次列表最多补 12 张，别把滚动拖慢。
            if r['kind'] == 'file' and not thumb and backfilled < 12:
                try:
                    ps = json.loads(r['content'] or '[]')
                except Exception:
                    ps = []
                if isinstance(ps, list) and len(ps) == 1:
                    backfilled += 1
                    tfn, dim = file_thumb(str(ps[0]))
                    if tfn:
                        thumb = tfn
                        detail_src = dim or detail_src
                        try:
                            con.execute("UPDATE clipboard_history SET thumb = ?, detail = ? WHERE id = ?",
                                        (tfn, detail_src, r['id']))
                            con.commit()
                        except Exception:
                            pass
            rows.append({
                'id': r['id'],
                'kind': r['kind'],
                'content': r['content'],
                'preview': format_preview(r['kind'], r['preview'], detail_src, lang),
                'detail': format_detail(r['kind'], r['content'], detail_src, lang),
                'created_at': r['created_at'],
                'relative_time': format_relative_time(r['created_at'], lang),
                'pinned': bool(r['pinned']),
                'thumb': thumb or '',
            })
        return rows
    finally:
        con.close()


def copy_item(item_id, lang='zh-CN'):
    """将指定历史项写回剪贴板，并设置防重入标记"""
    global _IGNORE_NEXT_CLIP
    con = get_con()
    try:
        cur = con.execute("SELECT kind, content FROM clipboard_history WHERE id = ? LIMIT 1", (item_id,))
        row = cur.fetchone()
        if not row:
            return False, _clip_msg('not_found', lang)

        kind = row['kind']
        content = row['content']
        _IGNORE_NEXT_CLIP = True

        if kind == 'text':
            if clip:
                clip.OpenClipboard()
                try:
                    clip.EmptyClipboard()
                    clip.SetClipboardData(win32con.CF_UNICODETEXT, content)
                finally:
                    clip.CloseClipboard()
                return True, _clip_msg('copied_text', lang)

        elif kind == 'file':
            try:
                paths = json.loads(content)
                if clip and isinstance(paths, list):
                    offset = 20  # sizeof(DROPFILES)
                    dropfiles = struct.pack('IIIIi', offset, 0, 0, 0, 1)
                    file_bytes = '\0'.join(paths).encode('utf-16le') + b'\0\0\0\0'
                    data = dropfiles + file_bytes
                    clip.OpenClipboard()
                    try:
                        clip.EmptyClipboard()
                        clip.SetClipboardData(win32con.CF_HDROP, data)
                    finally:
                        clip.CloseClipboard()
                    return True, _clip_msg('copied_files', lang, n=len(paths))
            except Exception as e:
                return False, _clip_msg('copy_fail', lang, e=e)

        elif kind == 'image':
            try:
                d = get_thumb_dir()
                p = d / content
                if p.is_file():
                    im = Image.open(str(p))
                    import snip_ocr
                    # 真正写剪贴板的是 snip_ocr.put_image_to_clipboard（CF_DIB + PNG + CF_HDROP 三格式）。
                    # 早先这里调的是 snip_ocr 里一个**根本不存在**的函数名，用户点图片条目就弹
                    # 「复制图片失败: module 'snip_ocr' has no attribute ...」（11-18 用户截图）。
                    ok = snip_ocr.put_image_to_clipboard(im)
                    if not ok:
                        return False, _clip_msg('img_write_fail', lang)
                    return True, _clip_msg('copied_img', lang)
                return False, _clip_msg('thumb_missing', lang)
            except Exception as e:
                return False, _clip_msg('copy_fail', lang, e=e)

        return False, _clip_msg('unsupported', lang)
    finally:
        con.close()


def delete_item(item_id):
    con = get_con()
    try:
        cur = con.execute("SELECT kind, content FROM clipboard_history WHERE id = ? LIMIT 1", (item_id,))
        row = cur.fetchone()
        if row and row['kind'] == 'image' and row['content']:
            d = get_thumb_dir()
            tp = d / row['content']
            if tp.is_file():
                try:
                    tp.unlink()
                except OSError:
                    pass
        con.execute("DELETE FROM clipboard_history WHERE id = ?", (item_id,))
        con.commit()
        return True
    finally:
        con.close()


def clear_history():
    con = get_con()
    try:
        cur = con.execute("SELECT content FROM clipboard_history WHERE pinned = 0 AND kind = 'image'")
        d = get_thumb_dir()
        for r in cur.fetchall():
            if r['content']:
                tp = d / r['content']
                if tp.is_file():
                    try:
                        tp.unlink()
                    except OSError:
                        pass
        con.execute("DELETE FROM clipboard_history WHERE pinned = 0")
        con.commit()
        return True
    finally:
        con.close()


def toggle_pin(item_id):
    con = get_con()
    try:
        cur = con.execute("SELECT pinned FROM clipboard_history WHERE id = ? LIMIT 1", (item_id,))
        row = cur.fetchone()
        if not row:
            return False, 0
        new_val = 0 if row['pinned'] else 1
        con.execute("UPDATE clipboard_history SET pinned = ? WHERE id = ?", (new_val, item_id))
        con.commit()
        return True, new_val
    finally:
        con.close()


# ------------------------------------------------------------------ 监听器服务
def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_CLIPBOARDUPDATE:
        if _UPDATE_EVENT:
            _UPDATE_EVENT.set()
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def _worker_loop():
    while _RUNNING:
        try:
            if _UPDATE_EVENT.wait(timeout=0.5):
                _UPDATE_EVENT.clear()
                # 防抖等待写入端完全提交并关闭剪贴板
                time.sleep(0.08)
                _UPDATE_EVENT.clear()
                _process_clipboard()
        except Exception as e:
            _log(f"worker loop error: {e}")


def _listener_loop():
    global _LISTENER_HWND, _RUNNING
    try:
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = _wndproc
        wc.lpszClassName = 'MemexClipWatchWnd'
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass

        hwnd = win32gui.CreateWindow(
            'MemexClipWatchWnd',
            'MemexClipWatch',
            0, 0, 0, 0, 0, 0, 0, 0, None
        )
        _LISTENER_HWND = hwnd
        _log(f"created listener window hwnd={hwnd}")

        if u32 and hwnd:
            ok = u32.AddClipboardFormatListener(hwnd)
            _log(f"AddClipboardFormatListener returned {ok}")

        _RUNNING = True
        win32gui.PumpMessages()

        if u32 and hwnd:
            u32.RemoveClipboardFormatListener(hwnd)
    except Exception as e:
        _log(f"listener loop exception: {e}")


def start_listener():
    global _LISTENER_THREAD, _WORKER_THREAD, _RUNNING
    if _RUNNING and _LISTENER_THREAD and _LISTENER_THREAD.is_alive():
        return
    if not win32gui or not u32:
        _log("win32gui or user32 unavailable, clipboard listener skipped")
        return
    init_db()
    _RUNNING = True
    _UPDATE_EVENT.clear()
    _WORKER_THREAD = threading.Thread(target=_worker_loop, name='MemexClipWorker', daemon=True)
    _WORKER_THREAD.start()
    _LISTENER_THREAD = threading.Thread(target=_listener_loop, name='MemexClipListener', daemon=True)
    _LISTENER_THREAD.start()
    _log("start_listener launched worker and listener threads")


def stop_listener():
    global _RUNNING, _LISTENER_HWND
    _RUNNING = False
    if _UPDATE_EVENT:
        _UPDATE_EVENT.set()
    if _LISTENER_HWND and win32gui:
        try:
            win32gui.PostMessage(_LISTENER_HWND, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass