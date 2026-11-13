"""索引集（多库）管理 —— 纯标准库，绝不 import brain。

一个「索引集」= 一个完整独立的 brain.db + 一份 scope.json。默认索引集直接复用
程序目录里原有的 brain.db / scope.json，不迁移、不复制、不重置任何已有数据；
新建的索引集放在 app_dir()/libs/<id>/ 下。

这个模块只负责「有哪些库、当前哪个、文件在哪」的元数据读写；
真正切换连接由 brain.switch_lib() 完成（它要关掉全局 conn、改 DB/SCOPE_FILE）。
"""
import json
import os
import re
import time
from pathlib import Path

DEFAULT_ID = 'default'
DEFAULT_NAME = '默认索引集'
FILE_NAME = 'libsets.json'
MAX_LIBS = 12


def _clean_name(name):
    s = re.sub(r'[\r\n\t]+', ' ', str(name or '')).strip()
    return s[:40]


def _norm_lib(x):
    if not isinstance(x, dict):
        return None
    lid = str(x.get('id') or '').strip()
    if not re.fullmatch(r'[a-z0-9_-]{1,48}', lid):
        return None
    return {
        'id': lid,
        'name': _clean_name(x.get('name')) or lid,
        'created': float(x.get('created') or 0),
    }


def path_for(app_dir):
    return Path(app_dir) / FILE_NAME


def load(app_dir):
    """读 libsets.json；缺文件/损坏/字段异常都退回「只有默认库」，绝不抛。"""
    d = {}
    try:
        d = json.loads(path_for(app_dir).read_text(encoding='utf-8')) or {}
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    raw = d.get('libs')
    libs = []
    if isinstance(raw, list):
        for x in raw:
            n = _norm_lib(x)
            if n and n['id'] != DEFAULT_ID:
                libs.append(n)
    libs.insert(0, {'id': DEFAULT_ID, 'name': _clean_name(d.get('default_name')) or DEFAULT_NAME,
                    'created': 0.0})
    ids = [x['id'] for x in libs]
    active = str(d.get('active') or DEFAULT_ID).strip()
    if active not in ids:
        active = DEFAULT_ID
    return {'active': active, 'libs': libs[:MAX_LIBS]}


def save(app_dir, data):
    """原子写：先 .tmp 再 os.replace，避免写一半留下坏 JSON。"""
    p = path_for(app_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    payload = {
        'active': data.get('active', DEFAULT_ID),
        'default_name': next((x['name'] for x in data.get('libs', [])
                              if x['id'] == DEFAULT_ID), DEFAULT_NAME),
        'libs': [x for x in data.get('libs', []) if x['id'] != DEFAULT_ID],
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(str(tmp), str(p))
    return True


def active_lib(data, lib_id=None):
    want = lib_id or data.get('active') or DEFAULT_ID
    return next((x for x in data['libs'] if x['id'] == want), None)


def lib_dir(app_dir, lib_id):
    return Path(app_dir) / 'libs' / lib_id


def db_path(app_dir, lib):
    if lib['id'] == DEFAULT_ID:
        return Path(app_dir) / 'brain.db'
    return lib_dir(app_dir, lib['id']) / 'brain.db'


def scope_path(app_dir, lib):
    if lib['id'] == DEFAULT_ID:
        return Path(app_dir) / 'scope.json'
    return lib_dir(app_dir, lib['id']) / 'scope.json'


def new_id(app_dir, name):
    """从名字取一段安全 slug，撞名就加时间后缀；只含 [a-z0-9_-]，不会路径穿越。"""
    base = re.sub(r'[^a-z0-9_-]+', '-', _clean_name(name).lower()).strip('-')[:24] or 'lib'
    d = load(app_dir)
    used = {x['id'] for x in d['libs']}
    if base not in used:
        return base
    return '%s-%d' % (base[:20], int(time.time()) % 1000000)


def create(app_dir, name):
    """新建一个索引集：建目录 + 写独立 scope.json，不切换当前库。"""
    d = load(app_dir)
    if len(d['libs']) >= MAX_LIBS:
        return None, '索引集最多 %d 个' % MAX_LIBS
    lid = new_id(app_dir, name)
    lib = {'id': lid, 'name': _clean_name(name) or lid, 'created': time.time()}
    sub = lib_dir(app_dir, lid)
    sub.mkdir(parents=True, exist_ok=True)
    sp = sub / 'scope.json'
    if not sp.exists():
        sp.write_text(json.dumps({
            'scan_csv': str(sub / 'scan_raw.csv'),
            'include_dirs': [],
            'exclude_dirs': [],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
    d['libs'].append(lib)
    save(app_dir, d)
    return lib, ''


def rename(app_dir, lib_id, name):
    d = load(app_dir)
    lib = active_lib(d, lib_id)
    if not lib:
        return False, '索引集不存在'
    if lib['id'] == DEFAULT_ID:
        d['default_name'] = _clean_name(name) or DEFAULT_NAME
        save(app_dir, d)
        return True, ''
    lib['name'] = _clean_name(name) or lib_id
    save(app_dir, d)
    return True, ''


def remove(app_dir, lib_id):
    """删索引集：先从清单移除并落盘，再逐个删库目录里的小文件（不用 rmtree）。"""
    if lib_id == DEFAULT_ID:
        return False, '默认索引集不能删'
    d = load(app_dir)
    lib = active_lib(d, lib_id)
    if not lib:
        return False, '索引集不存在'
    d['libs'] = [x for x in d['libs'] if x['id'] != lib_id]
    if d['active'] == lib_id:
        d['active'] = DEFAULT_ID
    save(app_dir, d)
    sub = lib_dir(app_dir, lib_id)
    try:
        for p in sorted(sub.glob('*'), key=lambda x: len(str(x)), reverse=True):
            try:
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    p.rmdir()
            except OSError:
                pass
        sub.rmdir()
    except OSError:
        pass
    return True, ''


def set_active(app_dir, lib_id):
    d = load(app_dir)
    if not active_lib(d, lib_id):
        return False
    d['active'] = lib_id
    save(app_dir, d)
    return True
