#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置备份 / 导入还原（纯标准库 zipfile）。

只打包**配置类小文件**，绝不打包：``brain.db``（用户索引，可能上 G）、
``license.json``（授权码，换机无效且属隐私）、``debug.log``（可能含路径）、
``ocr_batch.json``（队列状态，跨机无意义）、``error_report.json``（本机报告开关）、
``error_ingest.json``（开发者侧接收端地址与凭据，绝不能随备份流转）。

导入时逐条校验：文件名白名单、无路径分隔符 / ``..``、单文件 ≤ 1MB、
总条目 ≤ 12、JSON 必须能解析；还原前把现有文件整体复制到
``backup_before_restore_<时间>/``，再用临时文件 + ``os.replace`` 原子替换。
不使用 ``extractall``，天然避开 zip slip。
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

# 能被还原的文件白名单。新增配置文件（比如索引集）要显式加到这里。
ALLOW = (
    'settings.json',
    'scope.json',
    'saved_searches.json',
    'libsets.json',
    'apps.json',
    'lang.txt',
)
MANIFEST = 'manifest.json'
MAX_ZIP = 512 * 1024
MAX_MEMBER = 1024 * 1024
MAX_MEMBERS = 12
SAFE_NAME = re.compile(r'^[A-Za-z0-9_.\-]{1,60}$')


def file_list(data_dir):
    """返回实际存在的、允许备份的文件名（稳定排序）。"""
    root = Path(data_dir)
    return [n for n in ALLOW if (root / n).is_file()]


def make_backup(data_dir, version=''):
    """返回 (建议文件名, zip 字节)。没有任何配置文件时仍产出只含 manifest 的备份。"""
    root = Path(data_dir)
    names = file_list(root)
    buf = io.BytesIO()
    manifest = {
        'app': 'SecondBrain',
        'kind': 'settings-backup',
        'v': 1,
        'version': str(version or ''),
        'at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'files': names,
        'excludes': ['brain.db', 'license.json', 'debug.log', 'ocr_batch.json',
                     'error_report.json', 'error_ingest.json', 'libs/'],
    }
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        for n in names:
            try:
                data = (root / n).read_bytes()
            except OSError:
                continue
            z.writestr(n, data[:MAX_MEMBER])
    raw = buf.getvalue()
    return '第二大脑-设置备份_%s.zip' % time.strftime('%Y%m%d-%H%M%S'), raw


def inspect(raw):
    """只读检查一份备份，返回 (manifest, {文件名: bytes}) 或抛 ValueError。"""
    if not raw or len(raw) > MAX_ZIP:
        raise ValueError('备份文件为空或超过 512KB')
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except Exception:
        raise ValueError('不是有效的 zip 备份文件')
    with z:
        names = z.namelist()
        if not names or len(names) > MAX_MEMBERS:
            raise ValueError('备份条目数异常')
        out = {}
        for n in names:
            if n != MANIFEST and n not in ALLOW:
                raise ValueError('备份里有不允许的文件：%s' % n[:40])
            if not SAFE_NAME.match(n) or '..' in n or '/' in n or '\\' in n:
                raise ValueError('备份里有非法文件名')
            info = z.getinfo(n)
            if info.file_size > MAX_MEMBER:
                raise ValueError('备份条目过大：%s' % n[:40])
            data = z.read(n)
            if len(data) > MAX_MEMBER:
                raise ValueError('备份条目解压后过大')
            out[n] = data
        man = {}
        if MANIFEST in out:
            try:
                man = json.loads(out[MANIFEST].decode('utf-8'))
            except Exception:
                man = {}
            if not isinstance(man, dict):
                man = {}
        for n, data in out.items():
            if n == MANIFEST or n == 'lang.txt':
                continue
            try:
                v = json.loads(data.decode('utf-8'))
            except Exception:
                raise ValueError('%s 不是合法 JSON' % n)
            if not isinstance(v, (dict, list)):
                raise ValueError('%s 结构不是对象或数组' % n)
        return man, out


def restore(data_dir, raw):
    """校验 → 备份现有 → 原子替换。返回结构化结果，不抛异常给 HTTP 层。"""
    try:
        man, members = inspect(raw)
    except ValueError as e:
        return {'ok': False, 'err': str(e)}
    except Exception as e:
        return {'ok': False, 'err': '读取备份失败：%s' % type(e).__name__}
    root = Path(data_dir)
    targets = [n for n in ALLOW if n in members]
    if not targets:
        return {'ok': False, 'err': '备份里没有可还原的设置文件'}
    stamp = time.strftime('%Y%m%d-%H%M%S')
    bak = root / ('backup_before_restore_' + stamp)
    try:
        bak.mkdir(parents=True, exist_ok=True)
        for n in targets:
            src = root / n
            if src.is_file():
                (bak / n).write_bytes(src.read_bytes())
    except OSError as e:
        return {'ok': False, 'err': '现有设置备份失败，已中止：%s' % type(e).__name__}
    done = []
    for n in targets:
        tmp = root / (n + '.tmp')
        try:
            tmp.write_bytes(members[n])
            os.replace(str(tmp), str(root / n))
            done.append(n)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
    return {'ok': bool(done), 'restored': done, 'backup_dir': str(bak),
            'from_version': (man or {}).get('version', ''),
            'at': (man or {}).get('at', '')}
