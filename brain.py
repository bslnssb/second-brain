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

import ctypes

import hashlib

import io

import json

import os

import shutil

import re

import apprank

import license_core

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



APP_VERSION = 'v0.9.53'          # 0.x = 预览 / 开发版；到达 1.0 即视为正式版

BUILD_DATE = '2026-11-07'

PY_VER = sys.version.split()[0]

SQL_VER = sqlite3.sqlite_version



# ---------------------------------------------------------------- 在线更新

# 走 GitHub 的公开接口，不需要任何 token；断网/被墙时一律「安静地失败」，

# 绝不弹错误框——检查更新失败不该影响搜索。

REPO_SLUG = 'bslnssb/second-brain'

_UA = {'User-Agent': 'SecondBrain/%s' % APP_VERSION}



# ---------------------------------------------------------------- 官方地址 / 赞赏码防篡改

# 关于面板里的仓库地址**不写在前端**：写在这里，跟着 exe 一起编译。

# 别人拿走仓库改一行 JS 就能换掉链接，但要换掉这里的地址得重新打包 exe。

OFFICIAL_REPO = 'https://github.com/%s' % REPO_SLUG

OFFICIAL_RELEASES = OFFICIAL_REPO + '/releases'

OFFICIAL_ISSUES = OFFICIAL_REPO + '/issues'



# 发行版赞赏码的 SHA-256（assets/donate.png）。用途只有一个：

# 有人把安装包拆开、换掉二维码再发出去时，界面能当场认出「这不是我发出的那张」。

# 换自己的收款码要同时改这一行 —— 这是故意的，改图比改这行容易得多。

DONATE_SHA256 = '956fec5ba8786652cae62cbe828450771301320c8c3e40f5e4aad4ed544be38d'



# 授权码校验用的 Ed25519 公钥。私钥只在作者本机（~/.second-brain-license/seed.txt），

# **不进仓库、不进 exe**。这里只有公钥，只能验签、签不出码。

# 换私钥 = 所有已发出的授权码一起失效，换之前想清楚。

LICENSE_PUBKEY_HEX = 'b4f5b91a3530e1b670fee5c92f50ca0b1ea43209ded00c265f9958979e3ed2ad'



# 授权码和「上次见到的时间」都存在数据目录里。存明文码而不是解析结果：

# 换公钥/改解析逻辑后老码还能重新解释，解析结果一旦落盘就固化了。

LICENSE_FILE = 'license.json'



# /api/open_url 只认本仓库下的地址：这条路由会真的调起系统浏览器，

# 放开任意 URL 等于给页面一个「随手打开钓鱼站」的按钮。

# 注意别写成 startswith(OFFICIAL_REPO) —— 那样 `.../second-brain-evil` 也能过。

def url_allowed(u):

    u = (u or '').strip()

    return u == OFFICIAL_REPO or u.startswith(OFFICIAL_REPO + '/')





def file_sha256(p, limit=8 * 1024 * 1024):

    """算文件指纹，读不出来就返回空串（不抛——关于面板不该因为图标缺失打不开）。"""

    try:

        h = hashlib.sha256()

        with open(p, 'rb') as f:

            while True:

                b = f.read(262144)

                if not b:

                    break

                h.update(b)

                limit -= len(b)

                if limit <= 0:

                    break

        return h.hexdigest()

    except Exception:

        return ''




def _license_blank(reason='none'):

    """没有授权时的默认状态。字段和 license_core.parse_code 的返回保持一致。"""

    return {'valid': False, 'reason': reason, 'edition': 'free', 'kind': '',

            'name': '', 'ident': '', 'exp': '', 'issued': '', 'features': [],

            'days_left': None, 'code_masked': '', 'clock_warn': False,
            'activated': ''}




_LIC = {'loaded': False, 'state': None}




def license_path():

    return app_dir() / LICENSE_FILE




def license_state(recheck=False):

    """读 + 校验本机授权码。结果缓存，`recheck=True` 强制重算。

    验一次约 4ms，但 /api/license 会被前端反复拉，缓存是必要的。
    任何 IO/解析异常都降级成 free —— **授权读不出来不能连带软件不能用**。
    """

    if _LIC['loaded'] and not recheck:

        return _LIC['state']

    st = _license_blank()

    try:

        p = license_path()

        if p.is_file():

            data = json.loads(p.read_text(encoding='utf-8') or '{}')

            code = (data.get('code') or '').strip()

            if code:

                res = license_core.parse_code(code, LICENSE_PUBKEY_HEX)

                for k in ('valid', 'reason', 'edition', 'kind', 'name', 'ident',

                          'exp', 'issued', 'features'):

                    st[k] = res[k]

                st['code_masked'] = license_core.mask_code(code)

                st['activated'] = data.get('activated') or ''

                st['days_left'] = license_core.days_left(res['exp']) if res['valid'] else None

                # 时钟回拨：只提示不改判。系统时间同步、双系统、虚拟机时区都会造成

                # 「今天比上次早」，一律拦下会误伤；真过期了 parse_code 已经判过了。

                last = str(data.get('last_seen') or '')

                today = time.strftime('%Y-%m-%d')

                if last and today < last:

                    st['clock_warn'] = True

                elif today != last:

                    data['last_seen'] = today

                    try:

                        p.write_text(json.dumps(data, ensure_ascii=False, indent=2),

                                     encoding='utf-8')

                    except OSError:

                        pass

    except Exception:

        st = _license_blank('io')

    _LIC['loaded'] = True

    _LIC['state'] = st

    return st




def license_feature(name):

    """Pro 功能开关。**当前没有任何功能接在这里** —— 先把地基铺好。"""

    st = license_state()

    return bool(st['valid']) and name in (st.get('features') or [])




def license_activate(code):

    """保存并校验授权码。校验不过不落盘，避免把一个坏码留在机器上。"""

    code = (code or '').strip().replace('\n', '').replace('\r', '').replace(' ', '')

    res = license_core.parse_code(code, LICENSE_PUBKEY_HEX)

    if not res['valid']:

        return {'ok': False, 'reason': res['reason'], 'state': license_state()}

    p = license_path()

    payload = {'code': code, 'activated': time.strftime('%Y-%m-%d %H:%M'),

               'last_seen': time.strftime('%Y-%m-%d')}

    try:

        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    except OSError as e:

        return {'ok': False, 'reason': 'readonly', 'detail': str(e), 'state': license_state()}

    return {'ok': True, 'reason': 'ok', 'state': license_state(recheck=True)}




def license_clear():

    """清除本机授权（换码、换机器、售后排查用）。"""

    try:

        license_path().unlink()

    except OSError:

        pass

    return license_state(recheck=True)





def _http_get(url, timeout=8, limit=16 * 1024 * 1024):

    """只读一次 GET。超限直接放弃，避免被一个坏响应拖住内存。"""

    req = urllib.request.Request(url, headers=_UA)

    with urllib.request.urlopen(req, timeout=timeout) as r:

        data = r.read(limit + 1)

    if len(data) > limit:

        raise IOError('响应太大，已放弃')

    return data





def ver_tuple(s):

    """'v0.10.2' → (0, 10, 2)。不按字符串比，否则 0.10 会被判成比 0.9 旧。"""

    parts = str(s or '').strip().lstrip('vV').split('.')[:3]

    out = []

    for part in parts:

        num = ''

        for ch in part:

            if ch.isdigit():

                num += ch

            else:

                break

        out.append(int(num or 0))

    while len(out) < 3:

        out.append(0)

    return tuple(out)





def check_update(timeout=8):

    """查最新 Release。



    只认 0.x 序列：仓库里还挂着 v6.5 那些旧版本线的 Release（那时版本号是 6.x），

    如果不挡掉，0.7 会被判成「比 6.5 旧」，天天提示更新到一个更老的程序。

    """

    # 先试镜像里的 version.json（国内可达）；全都失败再回 GitHub API

    for _src in _VER_SOURCES:

        _u = _src % REPO_SLUG

        try:

            _info = json.loads(_http_get(_u, timeout=min(timeout, 6), limit=256 * 1024)

                               .decode('utf-8', 'replace'))

        except Exception:

            continue

        _r = _ver_from_json(_info)

        if _r:

            _r['src'] = _u

            return _r

    url = 'https://api.github.com/repos/%s/releases/latest' % REPO_SLUG

    try:

        info = json.loads(_http_get(url, timeout=timeout, limit=2 * 1024 * 1024)

                          .decode('utf-8', 'replace'))

    except Exception as e:

        # 仓库里一个 Release 都没发、或者这个 tag 被删了，GitHub 回 404。

        # 对用户来说这不是"网络出错"，而是"还没有新版"——照常告诉他已是最新，

        # 否则刚装完就点「检查更新」会红一片，像坏了。

        if '404' in str(e):

            return {'ok': True, 'cur': APP_VERSION, 'latest': APP_VERSION,

                    'notes': '', 'published': '',

                    'page': 'https://github.com/%s/releases' % REPO_SLUG,

                    'newer': False, 'legacy': False, 'asset': None, 'norelease': True}

        return {'ok': False, 'err': str(e)[:200], 'cur': APP_VERSION}

    tag = str(info.get('tag_name') or '').strip()

    cur, latest = ver_tuple(APP_VERSION), ver_tuple(tag)

    out = {'ok': True, 'cur': APP_VERSION, 'latest': tag or '?',

           'notes': (info.get('body') or '')[:4000],

           'page': info.get('html_url') or ('https://github.com/%s/releases' % REPO_SLUG),

           'published': (info.get('published_at') or '')[:10],

           'newer': False, 'legacy': False, 'asset': None}

    if latest[0] == 6:

        out['legacy'] = True          # 线上最新还是旧版本线 v6.x

        return out

    out['newer'] = latest > cur

    for a in info.get('assets') or []:

        name = str(a.get('name') or '')

        if name.lower().endswith('.zip') and 'core' not in name.lower():

            out['asset'] = {'name': name, 'url': a.get('browser_download_url'),

                            'size': a.get('size') or 0}

            break

    return out





# 国内直连 GitHub 经常超时：版本信息先读仓库里的 version.json（jsDelivr 有国内节点），

# 下载安装包再按 ghfast / ghproxy 这类加速前缀依次重试。失败才回 GitHub 官方地址。

_VER_SOURCES = (

    'https://cdn.jsdelivr.net/gh/%s@main/version.json',

    'https://raw.gitmirror.com/%s/main/version.json',

    'https://raw.githubusercontent.com/%s/main/version.json',

)

_DL_MIRRORS = ('https://ghfast.top/', 'https://ghproxy.net/', 'https://gh-proxy.com/')





def _ver_from_json(info):

    """把 version.json 的内容整理成和 GitHub Release 一样的返回结构。"""

    tag = str(info.get('version') or info.get('tag') or '').strip()

    if not tag:

        return None

    if not tag.startswith('v'):

        tag = 'v' + tag

    cur, latest = ver_tuple(APP_VERSION), ver_tuple(tag)

    out = {'ok': True, 'cur': APP_VERSION, 'latest': tag,

           'notes': str(info.get('notes') or '')[:4000],

           'page': str(info.get('page') or ('https://github.com/%s/releases' % REPO_SLUG)),

           'published': str(info.get('published') or '')[:10],

           'newer': False, 'legacy': False, 'asset': None}

    if latest[0] == 6:

        out['legacy'] = True

        return out

    out['newer'] = latest > cur

    for a in info.get('assets') or []:

        name = str(a.get('name') or '')

        if name.lower().endswith('.zip'):

            out['asset'] = {'name': name, 'url': a.get('url'), 'size': a.get('size') or 0}

            break

    return out





_UPD_HOSTS = ('github.com', 'objects.githubusercontent.com',

              'github-releases.githubusercontent.com', 'codeload.github.com')



# 一键升级的下载/换装进度（/api/update_status 轮询用）。

# download_update 改成后台线程跑以后，POST /api/update_apply 立刻返回，

# 171MB 的下载要几十秒到几分钟——前端必须有真实进度可看，而不是干等 fetch。

_UPD = {'state': 'idle', 'pct': 0, 'err': '', 'k': '', 'bat': ''}





def _safe_asset_url(u):

    """升级包只从 GitHub 的 https 地址下（json 里的 url 是外部数据）。"""

    try:

        from urllib.parse import urlsplit

        sp = urlsplit(str(u or ''))

    except Exception:

        return ''

    if sp.scheme != 'https':

        return ''

    if (sp.hostname or '').lower() not in _UPD_HOSTS:

        return ''

    return str(u)





def download_update(info=None):

    """下新版 zip 并解到临时目录，写一个「等本进程退出后替换再重启」的 bat。



    只替换程序文件：brain.db / scope.json / settings.json 这些用户数据一个都不碰

    （robocopy 的 /XF 里点名列了）。替换失败最多是这次更新没生效，原程序还在。

    """

    info = info or check_update()

    if not info.get('ok'):

        return {'ok': False, 'k': 'upd_net'}

    if not info.get('newer'):

        return {'ok': False, 'k': 'upd_none'}

    asset = info.get('asset') or {}

    url = _safe_asset_url(asset.get('url'))

    if not url:

        return {'ok': False, 'k': 'upd_noasset'}



    base = app_dir() / 'tmp' / 'upd'

    stage = base / 'stage'

    try:

        if stage.exists():

            shutil.rmtree(stage, ignore_errors=True)

        base.mkdir(parents=True, exist_ok=True)

        stage.mkdir(parents=True, exist_ok=True)

        _nm = os.path.basename(str(asset.get('name') or 'update.zip'))

        if not _nm.lower().endswith('.zip'):

            _nm = 'update.zip'

        zpath = base / _nm

        # 直连 + 三个加速前缀，谁先通就用谁（每次都不通才报错）

        _cands = [url] + [m + url for m in _DL_MIRRORS]

        _last = None

        _UPD.update(state='download', pct=0, err='', k='')

        for _u in _cands:

            try:

                with urllib.request.urlopen(urllib.request.Request(_u, headers=_UA),

                                            timeout=30) as r, open(zpath, 'wb') as f:

                    clen = int(r.headers.get('Content-Length') or 0)

                    total = 0

                    while True:

                        chunk = r.read(256 * 1024)

                        if not chunk:

                            break

                        total += len(chunk)

                        if total > 400 * 1024 * 1024:

                            raise IOError('安装包超过 400MB，已放弃')

                        f.write(chunk)

                        if clen:

                            _UPD['pct'] = min(99, int(total * 100 / clen))   # 界面的下载进度条

                _last = None

                break

            except Exception as _e:

                _last = _e

                continue

        if _last is not None:

            raise _last

        _UPD.update(state='unpack', pct=100)

        with zipfile.ZipFile(zpath) as z:

            infos = z.infolist()

            if len(infos) > 5000:

                raise IOError('安装包条目过多（%d），已放弃' % len(infos))

            if sum(i.file_size for i in infos) > 1500 * 1024 * 1024:

                raise IOError('安装包解压后超过 1.5GB，已放弃')

            for i in infos:                      # 只允许相对路径，挡住 .. 与盘符

                nm = i.filename.replace('\\', '/')

                if nm.startswith('/') or ':' in nm or '..' in nm.split('/'):

                    raise IOError('安装包里有可疑路径：%s' % i.filename)

            z.extractall(stage)

        # 有的包多一层目录，找到真正含 exe/py 的那一层

        root = stage

        for _ in range(3):

            subs = [p for p in root.iterdir() if p.is_dir()]

            if (root / 'SecondBrain.exe').exists() or (root / 'brain.py').exists():

                break

            if len(subs) == 1:

                root = subs[0]

            else:

                break

        if not ((root / 'SecondBrain.exe').exists() or (root / 'brain.py').exists()):

            return {'ok': False, 'k': 'upd_badzip'}

    except Exception as e:

        return {'ok': False, 'k': 'upd_net', 'err': str(e)[:200]}



    bat = base / 'install_update.bat'

    relaunch = ('start "" "%DST%\\SecondBrain.exe"' if getattr(sys, 'frozen', False)

                else 'start "" /D "' + str(app_dir()) + '" "' + str(sys.executable)

                     + '" -B app_window.py')

    lines = [

        '@echo off',

        'setlocal',

        'set "SRC=%s"' % root,

        'set "DST=%s"' % app_dir(),

        'set "PIDWAIT=%d"' % os.getpid(),

        'set "MARK=%DST%\\update_failed.txt"',

        ':wait',

        'tasklist /FI "PID eq %PIDWAIT%" 2>nul | find "%PIDWAIT%" >nul',

        # 等旧进程退出：用 ping 代替 timeout——没有控制台时 timeout 会立刻失败（实测 0.57s）

        'if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto wait )',

        'robocopy "%SRC%" "%DST%" /E /XF brain.db brain.db-wal brain.db-shm '

        'scope.json settings.json notices_cache.json /XD tmp thumbs backups .workbuddy '

        '/R:1 /W:1 /NFL /NDL /NJH /NJS',

        'if errorlevel 8 (',

        '  echo robocopy failed with %errorlevel% > "%MARK%"',

        '  echo %DATE% %TIME% >> "%MARK%"',

        ')',

        relaunch,

        'del "%~f0"',

    ]

    try:

        bat.write_bytes(('\r\n'.join(lines) + '\r\n').encode('gbk', 'replace'))

    except Exception as e:

        return {'ok': False, 'k': 'upd_bat', 'err': str(e)[:200]}

    return {'ok': True, 'bat': str(bat), 'version': info.get('latest')}





_DATA_DIR = []





DATA_FALLBACK = False





def boot_lang():

    """界面语言从哪来：安装器写的 lang.txt → settings.json 的 lang → 空串（前端自己猜）。



    这样用户装的时候选「English」，第一次打开就是英文，不会看到一屏中文直接卸载。

    """

    # 顺序很重要：用户在本机设置里手选过（lang_user=true）的优先，

    # 其次才是安装器写下的 lang.txt，最后按系统语言。

    # 反过来写的话，「装的时候选了英文、装完在设置里改成中文」会被 lang.txt 永久压回英文 ——

    # 2026-11-06 就是这么出的 bug（界面中文、托盘菜单英文）。

    try:

        cfg = json.loads((app_dir() / 'settings.json').read_text(encoding='utf-8'))

        v = str(cfg.get('lang') or '').lower()

        if cfg.get('lang_user') and v in ('zh', 'en', 'es', 'pt', 'hi'):

            return v

    except Exception:

        pass

    try:

        f = app_dir() / 'lang.txt'

        if f.exists():

            v = f.read_text(encoding='utf-8').strip().lower()

            if v in ('zh', 'en', 'es', 'pt', 'hi'):

                return v

    except Exception:

        pass

    try:

        import ctypes

        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()

        prim = langid & 0x3FF

        sysv = {0x04: 'zh', 0x0A: 'es', 0x16: 'pt', 0x39: 'hi'}.get(prim)

        if sysv:

            return sysv

        return 'en'

    except Exception:

        pass

    return ''





def export_dir():
    """导出文件的落地目录：`我的文档/第二大脑导出`。

    不写 app_dir()：程序可能装在 Program Files 或某个深层目录，用户点完
    「导出」根本找不到文件在哪；也不直接扔桌面，那会把桌面弄乱。
    Documents 在 OneDrive 重定向时可能不是默认名，所以加一层 `文档` 兜底。
    """
    base = Path.home() / 'Documents'
    if not base.is_dir():
        base = Path.home() / '文档'
    d = base / '第二大脑导出'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _export_slug(s):
    """把搜索词变成能当文件名的片段。Windows 文件名不许 \\ / : * ? " < > |。"""
    s = re.sub(r'[\\/:*?"<>|\s]+', '_', str(s or '')).strip('._')
    return (s or '结果')[:40]


def app_dir():

    """放数据的地方（数据库/settings/scope）——默认是程序所在目录。



    写不进去时自动退到 `%LOCALAPPDATA%\\SecondBrain\\data`：

    用户把 exe 放在只读位置、直接从压缩包里双击、或者目录被安全软件锁住时，

    原来会在第一次查库时报 "attempt to write a readonly database"，

    窗口里只显示「127.0.0.1 未发送任何数据」，看上去就是「打不开」。

    （可用环境变量 SB_DATA_DIR 强制指定。）

    """

    if _DATA_DIR:

        return _DATA_DIR[0]

    global DATA_FALLBACK

    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

    env_dir = (os.environ.get('SB_DATA_DIR') or '').strip()

    if env_dir:

        # 环境变量给了就**照它办**，别偷偷换地方：曾经因为 SB_DATA_DIR 指向的目录

        # 还没建出来，探针失败就默默退到 %LOCALAPPDATA%，结果测试读的是 A、程序写的是 B。

        try:

            Path(env_dir).mkdir(parents=True, exist_ok=True)

        except Exception:

            pass

    pick = Path(env_dir) if env_dir else base

    try:

        probe = pick / '.sb_write_test'

        probe.write_text('ok', encoding='utf-8')

        probe.unlink()

    except Exception:

        try:

            fb = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData/Local'))                 / 'SecondBrain' / 'data'

            fb.mkdir(parents=True, exist_ok=True)

            probe = fb / '.sb_write_test'

            probe.write_text('ok', encoding='utf-8')

            probe.unlink()

            print('[i] 程序目录写不进去，数据改放到：%s' % fb, flush=True)

            DATA_FALLBACK = True

            pick = fb

        except Exception:

            pass

    _DATA_DIR.append(pick)

    return pick



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

    # 刹车（e_prune_*）不是"出错"，是"我故意停下保护你"——界面上一律显示黄色"已暂停"，

    # 别用红叉吓人，否则用户会去点那个会把记录真删掉的强制清理。

    if str(kw.get('k') or '').startswith('e_prune'):

        kw['paused'] = True

    PROG.update(kw)





# ---------------------------------------------------------------- 一次只跑一个后台任务

# /api/update、/api/names、/api/discover、/api/selftest 原来各查各的 state，检查和置位不是原子的：

# 连点三下「更新」会起三个线程同时改库、同时抢连接（实测 3 个并发 POST 全回 ok）。

# 这里用一把锁 + 一个「谁在干活」的标记，四个入口共用。

_JOB_LOCK = threading.Lock()

_JOB = {'who': '', 'since': 0.0}

_JOB_STALE = 1800.0     # 标记超过 30 分钟、四个状态又都空闲 → 认为线程偷偷死了，允许重新认领





def _job_busy():

    return (PROG.get('state') in ('busy', 'running') or NAMES.get('state') == 'busy'

            or DISCOVER.get('state') == 'busy' or SELFTEST.get('state') == 'busy')





def _job_claim(who):

    """抢到返回 True；已经有人在跑就返回 False（调用方回 e_busy）。"""

    with _JOB_LOCK:

        if _JOB['who']:

            if time.time() - _JOB['since'] < _JOB_STALE or _job_busy():

                return False

            _JOB['who'] = ''          # 线程偷偷死了，标记留下：清掉重来

        if _job_busy():

            return False

        _JOB['who'], _JOB['since'] = who, time.time()

        return True





def _job_release(who):

    with _JOB_LOCK:

        if _JOB['who'] == who:

            _JOB['who'] = ''





def _run_job(who, fn, *a, **kw):

    """后台线程的统一外壳：跑完一定释放标记（否则一次异常就让「更新」永久点不动）。"""

    try:

        fn(*a, **kw)

    finally:

        _job_release(who)





# 智能扫描：噪音预判（默认不勾，用户可手动勾上）。按候选目录整条路径匹配。

NOISE_WORDS = ('steam', 'steamapps', 'epic games', '游戏', '驱动', 'driver', '备份',

               'backup', '缓存', 'cache', 'vmware', 'virtualbox', '虚拟机', 'recycle',

               'recovery', '恢复', '安装包', 'ghost', 'windows.old')



# 智能扫描发现结果（供 /api/discover 轮询）

DISCOVER = {'state': 'idle', 'groups': [], 'err': ''}



# ---------------------------------------------------------------- 拼音（azb → 安装包）

# Listary 抓人的功能之一：打首字母就能找到中文名。做法是给文件夹名和已入库文档名

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

        # 抢不到锁时等 5 秒再报错，而不是立刻抛 database is locked

        con.execute('PRAGMA busy_timeout=5000')

        for tbl in ('dirs', 'meta'):     # 这个函数可能先于 init_db 被调用，列自己补

            for col in ('py', 'ini'):

                try:

                    con.execute(f'ALTER TABLE {tbl} ADD COLUMN {col} TEXT')

                except sqlite3.Error:

                    pass

        con.commit()

        # files 60 万行：只补中文名（约 4.5 万），纯英文名留 NULL —— 它们靠 name 前缀

        # 就能搜到，补拼音只会多占空间、多花十几分钟。

        CJK = "'*[^ -~]*'"

        jobs = []

        for tbl, col, where in (('dirs', 'name', '1=1'), ('meta', 'path', '1=1'),

                                ('files', 'name', "name GLOB " + CJK)):

            try:

                left = con.execute(f'SELECT count(*) FROM {tbl} '

                                   f'WHERE ini IS NULL AND {where}').fetchone()[0]

            except sqlite3.Error:

                left = 0

            if left:

                jobs.append((tbl, col, left, where))

        PY_READY['total'] = sum(j[2] for j in jobs)

        for tbl, col, _left, where in jobs:

            while True:

                try:

                    rows = con.execute(f'SELECT rowid, {col} FROM {tbl} '

                                       f'WHERE ini IS NULL AND {where} LIMIT 500').fetchall()

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

        n_files = 0

        for i, root in enumerate(roots):

            _prog(state='busy', phase='discover', done=n_files, total=0, ok=docs_total,

                  msg=f'智能扫描 {root}', k='m_discover_target', a={'p': root})

            for cur, dirs, files in os.walk(root):

                dirs[:] = [d for d in dirs if not _is_reparse_dir(os.path.join(cur, d))]

                dirs[:] = [d for d in dirs if d.lower() not in scan_docs.SKIP_DIRS

                           and not d.startswith('.')]

                if scan_docs.is_novel_dir(cur):

                    dirs[:] = []

                    novel_dirs += 1

                    continue

                n_files += len(files)

                # 每 2000 个文件报一次：「已看过 N 个，找到 M 个文档」。以前进度条按盘数走

                # （一共 3 个盘就跳 0/33/66/100），看起来像卡死。

                if n_files % 2000 < len(files):

                    _prog(done=n_files, ok=docs_total,

                          msg=f'已看过 {n_files:,} 个文件，找到 {docs_total:,} 个文档')

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



def _acr(name: str) -> str:

    """首字母缩写：DeepSeek Harness → dsh（Listary 的招牌功能之一）。



    规则：按空格 / 连字符 / 下划线 / 点切词，每词取首字母；词内的大写字母也算

    （DeepSeek → D、S）；全大写或全小写的词只取首字母，避免 harness → H,A,R,N…。

    """

    if not name:

        return ''

    out = []

    for tok in re.split(r'[^0-9A-Za-z\u4e00-\u9fff]+', name):

        if not tok:

            continue

        caps = [c for c in tok if c.isupper()]

        if len(caps) >= 2 and not tok.isupper():

            out.extend(caps)

        else:

            out.append(tok[0])

    acr = ''.join(out).lower()

    return acr if 2 <= len(acr) <= 24 else ''





_ACR = {'built': False, 'rows': []}





def build_acr_index(con=None):

    """只为「程序 / 快捷方式」建缩写索引（几千行，毫秒级），随文件名重建而失效。"""

    try:

        c = con or init_db()

        rows = c.execute(

            "SELECT path, name, ext FROM files WHERE ext IN ('.lnk','.exe','.url','.appref-ms','.bat','.cmd')"

        ).fetchall()

        out = []

        seen = set()

        for path, name, ext in rows:

            real = os.path.basename(path) or (name or '')

            stem = os.path.splitext(real)[0]          # 去掉扩展名：否则 .lnk 会多出个 l

            a = _acr(stem)

            key = os.path.normcase(path)

            if a and key not in seen:

                seen.add(key)

                out.append((a, path, ext or ''))

        _ACR['rows'] = out

        _ACR['built'] = True

    except Exception:

        _ACR['rows'] = []

        _ACR['built'] = True





def acr_hits(q, limit=9):

    """按缩写查：完全等于 / 以前缀开头 / 包含。返回 [(path, ext)]。"""

    if not _ACR['built']:

        build_acr_index()

    q = (q or '').lower()

    if len(q) < 2 or not q.isascii() or not q.isalpha():

        return []

    exact, pref, mid, sub = [], [], [], []



    def is_subseq(needle, hay):

        it = iter(hay)

        return all(ch in it for ch in needle)



    for a, path, ext in _ACR['rows']:

        if a == q:

            exact.append((path, ext))

        elif a.startswith(q):

            pref.append((path, ext))

        elif q in a:

            mid.append((path, ext))

        elif len(q) <= 6 and is_subseq(q, a):

            sub.append((path, ext))

        if len(exact) + len(pref) + len(mid) + len(sub) > limit * 4:

            break

    return (exact + pref + mid + sub)[:limit]





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





def _verinfo_descr(path):

    """exe 的 FileDescription 字段。微信.exe 的 VerInfo 里有「微信」字样，

    Listary 也是靠这个让搜中文找到英文名的应用。



    用 win32api 取最稳：GetFileVersionInfo + VerQueryValue。

    pywin32 没装时回 None，不阻塞扫描。"""

    try:

        import win32api

        import win32con

        info = win32api.GetFileVersionInfo(path, '\\VarFileInfo\\Translation')

        if not info:

            return None

        lang, codepage = info[0]

        sub = f'\\StringFileInfo\\{lang:04X}{codepage:04X}\\FileDescription'

        d = win32api.GetFileVersionInfo(path, sub)

        if isinstance(d, str):

            d = d.strip()

            return d[:80] if d else None

    except Exception:

        return None

    return None





# WScript.Shell 必须是「每线程一个」，不能全局共享 —— 原因见 _lnk_target 的注释。

# 名字带下划线：pywebview 会把 js_api 的公开属性递归展开，模块级变量别用裸名字。

_LNK_TLS = threading.local()

# .lnk → 目标 的解析缓存。一次搜索里同一路径会被问两遍（隐藏过滤 + 合并去重），

# 重复走 COM 又慢又容易出事。

_TGT_CACHE = {}





def _local_only(p):

    """只放行「本机固定盘/移动盘上的真实路径」。



    为什么必须有这一道：解析 .lnk 目标、取图标、生成缩略图都会顺着路径去访问，

    而 UNC（\\\\主机\\共享\\x.exe）一访问就会发起 SMB 协商并自动带上当前账户的

    NetNTLMv2 哈希——对方连点击都不用骗，只要往被索引的目录里放一个快捷方式，

    等下一次更新索引即可。\\\\.\\ 与 \\\\?\\ 这类设备路径同理，一律拒。

    """

    if not p:

        return False

    s = str(p).strip()

    if s.startswith('\\\\') or s.startswith('//'):

        return False

    drv = os.path.splitdrive(s)[0]

    if len(drv) != 2 or drv[1] != ':':

        return False

    try:

        import ctypes

        kind = ctypes.windll.kernel32.GetDriveTypeW(drv + os.sep)

    except Exception:

        return True      # 拿不到盘类型时不因为这一道就拒绝本地文件

    return kind in (2, 3)   # 2=可移动盘 3=固定盘（网络盘 4 / 光驱 5 一律拒）







def _lnk_target(path):

    """解析 .lnk 快捷方式的目标路径。桌面上用户亲手放的都是常用应用，

    拿到目标才能取它的中文名（VerInfo）和真实图标。



    **WScript.Shell 不能存成全局单例**：HTTP 服务是多线程的（每个请求一个线程），

    COM 对象只能在创建它的公寓里用，跨线程调用直接 access violation，整个进程段错误。

    2026-11-06 实测：`/api/mini` 连着搜第 3、4 个词就把服务打崩，faulthandler 栈顶是

    `CreateShortcut` → `_lnk_target` → `_target_key` → `merge_same_target`。

    所以改成「每线程各自 CoInitialize + 各自一个 shell」。

    任何失败都回 None，不阻塞扫描。"""

    try:

        sh = getattr(_LNK_TLS, 'shell', None)

        if sh is None:

            import pythoncom

            pythoncom.CoInitialize()    # 同一线程重复调用只是加引用计数，安全

            import win32com.client

            sh = win32com.client.Dispatch('WScript.Shell')

            _LNK_TLS.shell = sh

        t = sh.CreateShortcut(path).TargetPath

        return t if t and _local_only(t) and os.path.exists(t) else None

    except Exception:

        return None





LNK_CACHE = {}





def _lnk_target_for_icon(path):

    """给 /api/icon 用：在 HTTP 线程里解一次 .lnk 的目标。

    每个请求一个线程，必须先 CoInitialize，否则 WScript.Shell 直接抛异常。

    结果按路径缓存 —— 快捷方式很少改，一次搜索只解几十个。"""

    key = os.path.normcase(path)

    with ICON_LOCK:

        if key in LNK_CACHE:

            return LNK_CACHE[key]

    tgt = None

    try:

        import pythoncom

        import win32com.client

        pythoncom.CoInitialize()

        try:

            sh = win32com.client.Dispatch('WScript.Shell')

            t = sh.CreateShortcut(path).TargetPath

            tgt = t if t and _local_only(t) and os.path.exists(t) else None

        finally:

            pythoncom.CoUninitialize()

    except Exception:

        tgt = None

    with ICON_LOCK:

        LNK_CACHE[key] = tgt

    return tgt





def _file_descr(path, ext):

    """索引时给应用类文件取「中文描述名」：

    .exe 直接读 VerInfo；.lnk 先解析到目标 exe 再读它的 VerInfo。

    搜「微信」命中 微信.lnk / Weixin.exe 就靠这个字段。"""

    if ext == '.exe':

        return _verinfo_descr(path)

    if ext == '.lnk':

        t = _lnk_target(path)

        if t and t.lower().endswith('.exe'):

            return _verinfo_descr(t)

    return None





# 文件名索引用的「跳过目录」：比 scan_docs.SKIP_DIRS 少了 program files 两类——

# 程序主 exe（chrome.exe / WeChat.exe）都在那里，全文件检索必须要它们。

# appdata 仍跳过：里面绝大多数是浏览器缓存/配置，对「启动器式」搜索是噪声；

# 应用快捷方式还能从 ProgramData 的 Start Menu、桌面等处拿到。

NAMES_SKIP_DIRS = {d for d in scan_docs.SKIP_DIRS

                   if d not in ('program files', 'program files (x86)')}

# 只索引用户真正会搜/会打开的文件类型，剔除游戏引擎原始资源（.dds/.uasset/.tga/.wem…）

# 与 scan_docs.ALL_FILES 保持一致，保证「文件名索引」和「正文索引」范围不打架。

NAMES_EXTS = scan_docs.ALL_FILES

# 名字索引只存路径，视频/压缩包存进来很便宜也很有用，给一个宽松上限挡住极少数超大文件。

NAMES_SIZE_LIMIT = 2 * 1024 * 1024 * 1024   # 2 GB

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse_dir(path):
    """这个目录是 junction / 符号链接 / 挂载点吗。

    为什么必须单独判：`os.walk(followlinks=False)` 是拿 `os.path.islink` 来判断的，
    而 Windows 上 **junction 的 islink 返回 False** —— 于是 os.walk 会跟着 junction 走。
    指向上级目录的 junction 就形成环（`C:\\Users\\<user>\\AppData\\Local\\Application Data`
    就是指向自己的那种），扫描会一直往下走到路径超长为止：用户看到的是
    「索引进度卡住不动、风扇一直转」。
    多一次 lstat 的代价很小（NTFS 元数据在缓存里），换来的是扫描一定能结束。
    """
    try:
        st = os.lstat(path)
        return bool(getattr(st, 'st_file_attributes', 0) & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False

# 「能抽出正文」的类型，与 _extract_text_impl 的支持集一一对应；正文近实时（_body_worker）

# 只收这些类型，别把视频/图片也丢去解析。ALL_FILES 是全类型（文件名层用），两者故意不同。

BODY_EXTS = frozenset(('.pdf', '.docx', '.doc', '.pptx', '.ppt',

                       '.xls', '.xlsx', '.xlsm', '.csv',

                       '.txt', '.md', '.rtf', '.html', '.htm', '.epub',

                       '.wps', '.et', '.dps'))





def _scope_conf(scope_path=None):

    """读 scope.json，读不到返回空 dict（不影响全盘扫描）。"""

    try:

        sp = Path(scope_path) if scope_path else app_dir() / 'scope.json'

        return json.loads(sp.read_text(encoding='utf-8')) or {}

    except Exception:

        return {}





def _scope_excludes(scope_path=None):

    """scope.json 的 exclude_dirs，normcase 后的一串「子串」。

    语义：拿它去 in 整条路径 —— 写 `steam` 能命中 E:\\SteamLibrary，写绝对路径

    只命中那棵子树。文件名索引和正文索引共用这一份，别只挡一半。"""

    out = []

    for s in (_scope_conf(scope_path).get('exclude_dirs') or []):

        s = str(s).strip()

        if s:

            out.append(os.path.normcase(s))

    return out





def _excluded(path, exckw):

    """路径是否命中排除词。exckw 由 _scope_excludes() 预先算好，循环里别再读文件。"""

    if not exckw:

        return False

    lp = os.path.normcase(str(path))

    return any(k in lp for k in exckw)





def launcher_dirs():

    """「启动器目录」：桌面（个人+公共）、开始菜单（个人+公共）。

    用户亲手放在桌面上的快捷方式 = 最常用的应用，扫描和排序都要给最高优先级。"""

    home = os.path.expanduser('~')

    out = [

        os.path.join(home, 'Desktop'),

        os.path.join(os.path.dirname(home), 'Public', 'Desktop'),

        os.path.join(os.environ.get('APPDATA', ''),

                     'Microsoft', 'Windows', 'Start Menu', 'Programs'),

        os.path.join(os.environ.get('PROGRAMDATA', 'C:\\ProgramData'),

                     'Microsoft', 'Windows', 'Start Menu', 'Programs'),

    ]

    return [d for d in out if d and os.path.isdir(d)]





def names_targets():

    """文件名索引的扫描根，按「用户真正在用的东西优先」排序：



    0) scope.json 的 include_dirs —— 用户亲手勾进正文范围的目录，就是他自己的工作目录。

       放最前面：重建索引时这几棵树先能搜到，不用等整盘扫完；

    1) 启动器目录（桌面/开始菜单）——最常用应用，最先扫、最先可用；

    2) 各盘根目录整盘扫。系统盘也扫 C:\\ 整盘而不是只扫用户目录 ——

       Program Files（chrome.exe / Weixin.exe）和 Public Desktop（公共桌面

       快捷方式）都在用户目录之外，只扫 ~ 会漏掉机器上安装的应用本体。



    scope.json 的 exclude_dirs 在这三个来源上**一律生效**（见 build_files_index），

    所以「正文里排掉的游戏库/素材库」不会又把文件名索引撑满。

    """

    out, seen = [], set()



    def push(d):

        k = os.path.normcase(os.path.abspath(d))

        if k not in seen and os.path.isdir(d):

            seen.add(k)

            out.append(d)



    for d in (_scope_conf().get('include_dirs') or []):

        d = str(d).strip()

        if d:

            push(d)

    for d in launcher_dirs():

        push(d)

    import string

    for letter in string.ascii_uppercase:

        root = f'{letter}:\\'

        if os.path.exists(root):

            push(root)

    return out





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

                 else [_real_case(t) for t in names_targets()])

        _prog(state='busy', phase='names', done=0, total=len(roots) + 1, ok=0, fail=0,

              msg='建立全盘文件名索引', k='m_names')

        con = sqlite3.connect(DB, check_same_thread=False)

        con.execute('PRAGMA journal_mode=WAL')

        # 全盘建索引要跑几分钟，期间搜索也在读；抢不到写锁就等，别直接失败

        con.execute('PRAGMA busy_timeout=5000')

        # synchronous=OFF 只影响这条一次性的批量写：崩了最多重扫一遍，换来成倍的写入速度

        con.execute('PRAGMA synchronous=OFF')

        con.execute('DROP TABLE IF EXISTS files_new')

        con.execute('CREATE TABLE files_new(path TEXT PRIMARY KEY, name TEXT NOT NULL, '

                    'dir TEXT, ext TEXT, size REAL, mtime REAL, descr TEXT, '

                    'py TEXT, ini TEXT)')

        # 目录也单独存一份：Listary 那类工具的体验里「搜文件夹」和「搜文件」一样常用，

        # 光靠文件路径猜不出空文件夹。整体占比不到文件数的 1/4，代价可以接受。

        con.execute('DROP TABLE IF EXISTS dirs_new')

        con.execute('CREATE TABLE dirs_new(path TEXT PRIMARY KEY, name TEXT NOT NULL, '

                    'parent TEXT, py TEXT, ini TEXT, mtime REAL)')

        con.commit()

        batch, dbatch, n = [], [], 0

        # 排除词只读一次：全盘要过十几万个目录，循环里再读 scope.json 会把 IO 打满

        exckw = _scope_excludes()

        skipped = 0

        for i, root in enumerate(roots):

            _prog(done=i, msg=f'扫描文件名 {root}', k='m_names_target', a={'p': root})

            for cur, dirs, files in os.walk(root):

                # 剪掉 junction / 符号链接目录。os.walk(followlinks=False) 挡不住 junction：
                # Windows 上 os.path.islink 对 junction 返回 False，而 os.walk 正是用它判断的。
                # 指向上层的 junction（典型：C:\Users\x\AppData\Local\Application Data 指向自己）
                # 会让扫描无限往下走，用户看到的是「索引永远做不完、风扇狂转」。
                dirs[:] = [d for d in dirs if not _is_reparse_dir(os.path.join(cur, d))]

                dirs[:] = [d for d in dirs if d.lower() not in NAMES_SKIP_DIRS

                           and not d.startswith('.')]

                # 排除词在「往下走之前」剪掉整棵子树：命中 E:\SteamLibrary 就不用再进

                # steamapps 翻 30 万个贴图，重建速度直接受益（旧版排除只挡正文，文件名照样全扫）

                if exckw:

                    kept = []

                    for d in dirs:

                        if _excluded(os.path.join(cur, d), exckw):

                            skipped += 1

                        else:

                            kept.append(d)

                    dirs[:] = kept

                if scan_docs.is_novel_dir(cur) or _excluded(cur, exckw):

                    dirs[:] = []

                    continue

                # 自己的程序目录不建目录索引：用户点「文件夹」不该看到 _internal / webview / yaml

                try:

                    if os.path.normcase(cur).startswith(_self_root_nc()):

                        dirs[:] = []

                        continue

                except NameError:

                    pass

                base = os.path.basename(cur)

                if base:

                    py, ini = py_keys(base)     # 建的时候就填好，重建完拼音立刻可用

                    try:

                        dmt = os.stat(cur).st_mtime

                    except OSError:

                        dmt = None

                    dbatch.append((cur, base.lower(), os.path.dirname(cur), py, ini, dmt))

                for f in files:

                    ext = os.path.splitext(f)[1].lower()

                    # 只收用户会搜的文件类型，游戏引擎原始资源（.dds/.uasset/.tga…）直接跳过

                    if ext not in NAMES_EXTS:

                        continue

                    full = os.path.join(cur, f)

                    # exe 读自身 VerInfo；lnk 解析到目标 exe 再读 ——

                    # 搜「微信」命中 微信.lnk / Weixin.exe 靠这个

                    descr = _file_descr(full, ext) if ext in ('.exe', '.lnk') else None

                    try:

                        st = os.stat(full)

                        sz = st.st_size

                        mt = st.st_mtime

                    except OSError:

                        sz, mt = None, None

                    # 极少数超大文件（>2GB，多是镜像/虚拟盘）不入索引，避免索引虚胖

                    if sz and sz > NAMES_SIZE_LIMIT:

                        continue

                    # 中文名顺手把拼音算好：重建完拼音立刻可用，不用再等 backfill

                    fpy, fini = py_keys(f) if any(ord(ch) > 127 for ch in f) else (None, None)

                    batch.append((full, f.lower(), cur, ext, sz, mt, descr, fpy, fini))

                    if len(batch) >= 20000:

                        con.executemany('INSERT OR REPLACE INTO files_new'

                                        '(path,name,dir,ext,size,mtime,descr,py,ini) '

                                        'VALUES(?,?,?,?,?,?,?,?,?)', batch)

                        if dbatch:

                            con.executemany('INSERT OR REPLACE INTO dirs_new'

                                            '(path,name,parent,py,ini,mtime) VALUES(?,?,?,?,?,?)', dbatch)

                            dbatch = []

                        con.commit()

                        n += len(batch); batch = []

                        _prog(done=i, msg=f'扫描文件名 {root}（已 {n}）',

                              k='m_names_progress', a={'n': n})

            if batch:

                con.executemany('INSERT OR REPLACE INTO files_new'

                                '(path,name,dir,ext,size,mtime,descr,py,ini) '

                                'VALUES(?,?,?,?,?,?,?,?,?)', batch)

                con.commit()

                n += len(batch); batch = []

        if dbatch:

            con.executemany('INSERT OR REPLACE INTO dirs_new'

                            '(path,name,parent,py,ini,mtime) VALUES(?,?,?,?,?,?)', dbatch)

            con.commit()

        _prog(done=len(roots), msg='整理文件名索引…', k='m_names_finish')

        # 换表这一下很快，但要挡住 HTTP 线程正在进行的查询，避免它们撞上「表刚被删」

        with con_lock:

            con.execute('DROP TABLE IF EXISTS files')

            con.execute('ALTER TABLE files_new RENAME TO files')

            con.execute('CREATE INDEX IF NOT EXISTS idx_files_name ON files(name)')

            con.execute('CREATE INDEX IF NOT EXISTS idx_files_py ON files(py)')

            con.execute('CREATE INDEX IF NOT EXISTS idx_files_ini ON files(ini)')

            # 首页时间轴按 mtime 倒序、反查按 size 找同大小——没有索引就是 60 万行全表扫

            con.execute('CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime)')

            # idx_files_dir 从来没有任何 dir=? 查询用它（EQP 证实），白占 53.8MB、

            # 每次重建多花 0.84 秒。删掉。

            con.execute('DROP INDEX IF EXISTS idx_files_dir')

            # 反查面板按 lower(path)=lower(?) 查 meta：BINARY 主键失效只能全扫，

            # 建成 NOCASE 索引后 0.07ms（146 倍）。

            con.execute('CREATE INDEX IF NOT EXISTS idx_meta_path_nc ON meta(path COLLATE NOCASE)')

            # 描述小表跟着重建：全盘索引一换，旧的 appdesc 就过期了

            con.execute('DROP TABLE IF EXISTS appdesc')

            con.execute('CREATE TABLE appdesc(path TEXT PRIMARY KEY, descr TEXT, '

                        'ext TEXT, mtime REAL, size REAL)')

            con.execute('INSERT OR REPLACE INTO appdesc SELECT path, descr, ext, mtime, size '

                        "FROM files WHERE descr IS NOT NULL AND descr<>''")

            con.execute('DROP TABLE IF EXISTS dirs')

            con.execute('ALTER TABLE dirs_new RENAME TO dirs')

            con.execute('CREATE INDEX IF NOT EXISTS idx_dirs_name ON dirs(name)')

            con.execute('CREATE INDEX IF NOT EXISTS idx_dirs_ini ON dirs(ini)')

            con.execute('CREATE INDEX IF NOT EXISTS idx_dirs_py ON dirs(py)')

            con.execute('CREATE INDEX IF NOT EXISTS idx_dirs_mtime ON dirs(mtime)')

            con.commit()

        con.close()

        _ACR['built'] = False

        NAMES = {'state': 'done', 'err': '', 'n': n, 'skipped': skipped}

        db_upkeep()          # 换表 + 灌 60 万行会把 WAL 撑到几百 MB，收尾时誊回去

        db_reclaim()         # 换表会删掉整张 files，旧页留着库就虚胖了

        _prog(state='done', phase='', msg=f'文件名索引完成：{n} 个文件',

              k='m_names_done', a={'n': n})

        print(f'全盘文件名索引完成：{n} 个文件'

              + (f'，排除词剪掉 {skipped} 个目录' if skipped else ''))

    except Exception as e:

        import traceback

        traceback.print_exc()

        NAMES = {'state': 'error', 'err': f'{type(e).__name__}: {e}', 'n': 0}

        _prog(state='error', phase='', msg=f'文件名索引失败：{e}', err=str(e),

              k='m_names_fail', a={'e': str(e)})



# ─────────────────────── 文件系统实时监听（v0.9.47）───────────────────────

# 为什么要有：以前新建/改名/删掉一个文件，必须点「按现有范围更新」才进库。

# 用户刚存完文档、回头搜不到，第一反应是「这软件是不是坏了」（第 76 项待办第 2、3 条）。

#

# 为什么用 ReadDirectoryChangesW 而不是 USN Journal：后者要 DeviceIoControl 读整卷日志、

# 处理日志回绕和卷内偏移换算，代码量是前者的十倍；而这里只需要知道「用户工作目录里

# 发生了什么」，一个递归句柄就够。代价是进程不在时不记录 —— 那本来就得靠常规扫描补齐。

#

# 只做**文件名层**的增量：正文索引要解 PDF/OCR，一路自动跑起来会把磁盘吃满，

# 而且用户感知不到「刚存的 docx 已经能搜正文」这种收益。正文仍走手动更新。

_WATCH = {'on': False, 'stop': None, 'q': {}, 'lock': threading.Lock(),

          'threads': [], 'dropped': 0, 'wrote': 0}

_WATCH_FLAGS = 0x0001 | 0x0002 | 0x0008 | 0x0010   # 文件名/目录名/大小/修改时间

_WATCH_SHARE_ALL = 0x0001 | 0x0002 | 0x0004





def _watch_roots():

    """要监听的目录：索引范围里的工作目录 + 桌面 / 下载 / 文档。



    刻意**不监听整盘根**：解压一个游戏、跑一次编译就能压出十几万条事件，

    把监听线程和数据库一起拖死。用户真正在意的是「我自己刚存的那份文件」，

    而那份文件基本都落在这几个地方。上限 8 个根，每个根一个线程一个句柄。"""

    out, seen = [], set()



    def push(d):

        try:

            d = str(d or '').strip()

            if not d or not os.path.isdir(d):

                return

            k = os.path.normcase(os.path.abspath(d))

            if k not in seen:

                seen.add(k)

                # 用磁盘上的真实大小写：scope.json 里是人手写的 `c:\users\…`，

                # 原样监听会让每条事件都写成小写路径，和全量索引（真实大小写）分家，

                # 同一个文件在库里出现两条。

                out.append(_real_case(d))

        except OSError:

            pass



    for d in (_scope_conf().get('include_dirs') or []):

        push(d)

    home = os.path.expanduser('~')

    for sub in ('Desktop', 'Downloads', 'Documents', '桌面', '下载', '文档'):

        push(os.path.join(home, sub))

    return out[:8]





def _watch_worker(root):

    """一个目录一棵子树，阻塞读变更。线程是 daemon，进程退出即结束。"""

    h = None

    try:

        import pythoncom

        import win32file

        pythoncom.CoInitialize()

        h = win32file.CreateFileW(

            root, 0x0001, _WATCH_SHARE_ALL, None, 3, 0x02000000, None)

        while not _WATCH['stop'].is_set():

            acts = win32file.ReadDirectoryChangesW(h, 64 * 1024, True, _WATCH_FLAGS, None, None)

            with _WATCH['lock']:

                for action, rel in acts:

                    full = os.path.join(root, rel)

                    if len(_WATCH['q']) >= 4000:      # 洪水时丢新事件，别把内存吃掉

                        _WATCH['dropped'] += 1

                        continue

                    _WATCH['q'][full] = action

    except Exception:

        pass                      # 目录被删/网盘断开/权限不足：这个根就不监听了

    finally:

        if h is not None:

            try:

                h.Close()

            except Exception:

                pass





def _watch_flusher():

    """攒 1.5 秒写一次库，把「一次保存产生十几个事件」压成一条 SQL。"""

    while not _WATCH['stop'].wait(1.5):

        with _WATCH['lock']:

            q = _WATCH['q']

            _WATCH['q'] = {}

            batch = list(q.items())

        if not batch:

            continue

        # 全量任务在跑时会 DROP/RENAME files 表，这时写进去也是白写，等它跑完

        if PROG.get('state') == 'busy' or NAMES.get('state') == 'busy':

            with _WATCH['lock']:

                for k, v in batch:

                    _WATCH['q'].setdefault(k, v)

            continue

        try:

            _WATCH['wrote'] += _watch_apply(batch)

        except Exception as e:

            print('[!] 实时索引写入失败：', e, flush=True)





def _watch_apply(batch):

    """把一批变更落到 files / dirs。返回实际写入条数。



    用独立连接：全局 conn() 要套 con_lock，而这里可能连着写几十条，

    占着锁会卡住搜索。busy_timeout 兜住和后台任务的短暂争用。"""

    exckw = _scope_excludes()

    add_f, add_d, gone = [], [], []

    for full, action in batch:

        if action in (2, 4):                 # REMOVED / RENAMED_OLD_NAME

            gone.append(full)

            continue

        try:

            st = os.stat(full)               # 1 ADDED / 3 MODIFIED / 5 RENAMED_NEW

        except OSError:

            gone.append(full)                # 事件到得比文件晚（临时文件已删）

            continue

        if os.path.isdir(full):

            base = os.path.basename(full)

            if not base:

                continue

            py, ini = py_keys(base)

            add_d.append((full, base.lower(), os.path.dirname(full), py, ini, st.st_mtime))

            continue

        ext = os.path.splitext(full)[1].lower()

        if ext not in NAMES_EXTS or st.st_size > NAMES_SIZE_LIMIT:

            gone.append(full)                # 不该入库的类型，顺手清掉同名旧记录

            continue

        if exckw and _excluded(full, exckw):

            gone.append(full)

            continue

        try:

            if _is_self_noise(full):     # 程序自己的日志/备份/构建产物不入索引

                gone.append(full)

                continue

        except NameError:

            pass

        base = os.path.basename(full)

        # .lnk 的 descr 要解 COM，实时线程里只给 .exe 取（VerInfo 是纯读文件，快）

        descr = _file_descr(full, ext) if ext == '.exe' else None

        py, ini = py_keys(base) if any(ord(c) > 127 for c in base) else (None, None)

        add_f.append((full, base.lower(), os.path.dirname(full), ext,

                      st.st_size, st.st_mtime, descr, py, ini))

    if not (add_f or add_d or gone):

        return 0

    con = sqlite3.connect(DB, check_same_thread=False)

    try:

        con.execute('PRAGMA busy_timeout=3000')

        if gone:

            con.executemany('DELETE FROM files WHERE path=?', [(g,) for g in gone])

            con.executemany('DELETE FROM dirs WHERE path=?', [(g,) for g in gone])

            con.executemany('DELETE FROM appdesc WHERE path=?', [(g,) for g in gone])

        if add_f:

            con.executemany('INSERT OR REPLACE INTO files'

                            '(path,name,dir,ext,size,mtime,descr,py,ini) '

                            'VALUES(?,?,?,?,?,?,?,?,?)', add_f)

            con.executemany('INSERT OR REPLACE INTO appdesc(path,descr,ext,mtime,size) '

                            "SELECT path,descr,ext,mtime,size FROM files "

                            "WHERE path=? AND descr IS NOT NULL AND descr<>''",

                            [(a[0],) for a in add_f])

        if add_d:

            con.executemany('INSERT OR REPLACE INTO dirs(path,name,parent,py,ini,mtime) '

                            'VALUES(?,?,?,?,?,?)', add_d)

        con.commit()

        _bodyq_offer(add_f, gone)     # 其中的文档顺手排进正文重索引队列（近实时）

        return len(add_f) + len(add_d)

    finally:

        con.close()





# ---------------------------------------------------------------- 正文近实时

# 文件名层（_watch_apply）2 秒进库，但正文仍要手动「按现有范围更新」——改完文档

# 搜出来的还是旧正文，这是实时化落地后最大的体感缺口。这里把 watcher 看到的文档

# 变更排进队列，由一个后台线程空闲时段小批重抽正文（子进程、可超时），写库纪律

# 与 _watch_apply 相同：独立连接、busy_timeout、全量任务在跑时让路。

_BODYQ_LOCK = threading.Lock()

_BODYQ = {}                 # normcase(path) -> (原始 path, mtime)；dict 天然去重、保序

_BODYQ_DEL = set()          # normcase(path)：已删除/改名旧名，等 worker 清 docs+meta

_BODYQ_BATCH = 8            # 每轮最多重抽几篇（OCR/PDF 子进程单篇可达几十秒）

_BODYQ_INTERVAL = 30.0      # 取批间隔（秒）；「保存 → 约 1 分钟内可搜」就是它决定的

_BODYQ_CAP = 4000           # 洪水上限，与 _WATCH['q'] 同款：丢新事件，别把内存吃掉

_BODYSTAT = {'pending': 0, 'done': 0, 'last': '', 'dropped': 0, 'merge': 0}





def _bodyq_offer(add_f, gone):

    """文件名层落库后，把其中的文档挑出来排进正文重索引队列（_watch_apply 专用）。"""

    with _BODYQ_LOCK:

        for row in add_f:

            p, mt = row[0], row[5]

            k = os.path.normcase(p)

            if os.path.splitext(p)[1].lower() not in BODY_EXTS:

                continue

            if k in _BODYQ_DEL:            # 删了又建回来：撤掉删除，按变更处理

                _BODYQ_DEL.discard(k)

            if len(_BODYQ) >= _BODYQ_CAP and k not in _BODYQ:

                _BODYSTAT['dropped'] += 1

                continue

            _BODYQ[k] = (p, mt)

        for g in gone:

            if os.path.splitext(g)[1].lower() not in BODY_EXTS:

                continue

            k = os.path.normcase(g)

            _BODYQ_DEL.add(k)

            _BODYQ.pop(k, None)            # 先删后建竞态：以删除事件收尾

        _BODYSTAT['pending'] = len(_BODYQ) + len(_BODYQ_DEL)





def _body_need(con, p, st):

    """单文件版的「要不要重抽正文」，判定逻辑与 index() 的 _needs 一致

    （size±1 / mtime±2 容差；旧库条目补登记不重建）。"""

    r = con.execute('SELECT indexed_at, size, mtime FROM meta '

                    'WHERE path=? OR lower(path)=lower(?)', (p, p)).fetchone()

    if not r:

        return True

    at, sz, mt = r

    if sz is not None and mt is not None:

        return not (abs(st.st_size - sz) < 1 and abs(st.st_mtime - mt) < 2)

    if st.st_mtime <= (at or 0) + 60:

        con.execute('UPDATE meta SET size=?, mtime=? WHERE path=? OR lower(path)=lower(?)',

                    (st.st_size, st.st_mtime, p, p))

        return False

    return True





def _body_apply(items, dels):

    """处理一批正文变更（items=[(path, mtime)]，dels=[normcase path]）。

    写库纪律与 _watch_apply 相同：独立连接，不占 con_lock。"""

    n_ok = n_fail = 0

    con = sqlite3.connect(DB, check_same_thread=False)

    try:

        con.execute('PRAGMA busy_timeout=3000')

        # _doc_rids 返回原始大小写键，这里照 index() 的先例归一化，pop(normcase) 才找得到

        rids = {os.path.normcase(k): v for k, v in _doc_rids(con).items()}

        delset = set(dels)

        for p in dels:

            _doc_del(con, rids, p)

            con.execute('DELETE FROM meta WHERE path=? OR lower(path)=lower(?)', (p, p))

        for p, _mt in items:

            if os.path.normcase(p) in delset:      # 同批里先改后删：以删除为准

                continue

            try:

                st = os.stat(p)

            except OSError:

                continue                            # 事件到得比文件晚，留给删除分支

            if Path(p).name.startswith('~$'):      # Office 锁文件，不是真文档

                continue

            if not _body_need(con, p, st):

                continue

            text, why = extract_text_safe(Path(p))

            if text:

                text = text.encode('utf-8', 'ignore').decode('utf-8')

            if text and text.strip():

                _doc_del(con, rids, p)             # 同步清 bigram 旁表（见 _doc_del）

                cur = con.execute('INSERT INTO docs VALUES(?,?,?)',

                                  (p, Path(p).suffix.lower(), text))

                rids[os.path.normcase(p)] = cur.lastrowid

                _bigram_add(con, cur.lastrowid, text)

                con.execute('INSERT OR REPLACE INTO meta(path, indexed_at, size, mtime, reason) '

                            'VALUES(?,?,?,?,?)',

                            (p, time.time(), st.st_size, st.st_mtime, None))

                n_ok += 1

                _BODYSTAT['last'] = p

            else:

                con.execute('INSERT OR REPLACE INTO meta(path, indexed_at, size, mtime, reason) '

                            'VALUES(?,?,?,?,?)',

                            (p, time.time(), st.st_size, st.st_mtime,

                             why or _fail_reason(Path(p))))

                n_fail += 1

        con.commit()

        _BODYSTAT['merge'] += n_ok

        if _BODYSTAT['merge'] >= 20:

            # 小批重抽攒够了照 index() 的收尾惯例合并 FTS 段（'optimize' 会写几百 MB WAL）

            try:

                con.execute("INSERT INTO docs(docs, rank) VALUES('merge', 8)")

                con.commit()

                _BODYSTAT['merge'] = 0

            except Exception:

                try:

                    con.rollback()

                except Exception:

                    pass

        _BODYSTAT['done'] += n_ok + n_fail

    finally:

        with _BODYQ_LOCK:

            _BODYSTAT['pending'] = len(_BODYQ) + len(_BODYQ_DEL)

        con.close()





def _body_worker():

    """正文近实时的工作循环：空闲时段每 30 秒小批重抽。全量任务（更新/重建文件名/

    智能扫描/体检）在跑时整批让路，绝不跟用户正等着的大任务抢磁盘。"""

    while True:

        stop = _WATCH['stop']

        if stop is not None and stop.is_set():

            return

        time.sleep(_BODYQ_INTERVAL)

        if _job_busy():

            continue

        with _BODYQ_LOCK:

            keys = list(_BODYQ)[:_BODYQ_BATCH]

            items = [_BODYQ.pop(k) for k in keys]

            dels = list(_BODYQ_DEL)[:_BODYQ_BATCH]

            for k in dels:

                _BODYQ_DEL.discard(k)

            _BODYSTAT['pending'] = len(_BODYQ) + len(_BODYQ_DEL)

        if not items and not dels:

            continue

        try:

            _body_apply(items, dels)

        except Exception as e:

            # 单批失败不能带崩线程：路径塞回队列等下一轮，留下的痕迹只进日志

            _log_file('body reindex batch FAILED: %r' % (e,))

            with _BODYQ_LOCK:

                for p, mt in items:

                    _BODYQ.setdefault(os.path.normcase(p), (p, mt))

                _BODYQ_DEL.update(dels)

                _BODYSTAT['pending'] = len(_BODYQ) + len(_BODYQ_DEL)





def start_watcher():

    """启动实时监听。settings.json 里 "watch": false 可关掉（排查问题时有用）；

    "watch_body": false 只关正文重索引，文件名层照常。"""

    if _WATCH['on'] or os.environ.get('SB_NO_WATCH'):

        return

    body_on = True

    try:

        cfg = _settings_read()

        if cfg.get('watch') is False:

            return

        body_on = cfg.get('watch_body') is not False

    except Exception:

        pass

    roots = _watch_roots()

    if not roots:

        return

    _WATCH['on'] = True

    _WATCH['stop'] = threading.Event()

    for r in roots:

        t = threading.Thread(target=_watch_worker, args=(r,), daemon=True,

                             name='sb-watch')

        t.start()

        _WATCH['threads'].append(t)

    threading.Thread(target=_watch_flusher, daemon=True, name='sb-watch-flush').start()

    if body_on:

        threading.Thread(target=_body_worker, daemon=True, name='sb-body').start()

    print('实时索引：监听 %d 个目录%s'

          % (len(roots), '' if body_on else '（正文重索引已关）'), flush=True)





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

                               encoding='utf-8', errors='replace', env=env,

                               stdin=subprocess.DEVNULL)

            # stdin 必须掐断：从控制台启动的程序点「质量自检」，子进程会继承控制台

            # stdin，selftest 的 repair_scope 看到 isatty 就走 input() 问号，卡满

            # 1200 秒超时才报错（selftest 内部已自固定 stdin，这里是第二道保险）。

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

        finally:

            _job_release('selftest')

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





_NS_X = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

_NS_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'





def _xlsx_text(path: Path):

    """把 .xlsx/.xlsm 读成「工作表 + 行」的可读文本。



    只读三样东西，全在 zip 里，不装 openpyxl：

      xl/workbook.xml        → 工作表名字与顺序

      xl/sharedStrings.xml   → 单元格里重复出现的字符串（Excel 把同一段文字存一次、多处引用）

      xl/worksheets/sheetN.xml → 每个单元格的类型与取值

    输出形如：

      工作表「报销」

      第 1 行：姓名 | 部门 | 金额

      第 2 行：张三 | 市场部 | 1200

    行的形态比拍平成一行更容易让命中片段读懂是哪一行。

    """

    import xml.etree.ElementTree as ET

    try:

        with zipfile.ZipFile(path) as z:

            names = set(z.namelist())



            # 1) 工作表名字（找不到就用 sheet1、sheet2 兜底）

            titles = []

            if 'xl/workbook.xml' in names:

                try:

                    wb = ET.fromstring(z.read('xl/workbook.xml'))

                    for sh in wb.iter(_NS_X + 'sheet'):

                        titles.append((sh.get('name') or '').strip() or 'Sheet')

                except ET.ParseError:

                    titles = []



            # 2) 共享字符串

            shared = []

            if 'xl/sharedStrings.xml' in names:

                try:

                    ss = ET.fromstring(z.read('xl/sharedStrings.xml'))

                    for si in ss.iter(_NS_X + 'si'):

                        shared.append(''.join(t.text or '' for t in si.iter(_NS_X + 't')))

                except ET.ParseError:

                    shared = []



            # 3) 逐个 sheet 取行；文件里 sheet 的顺序就是下标顺序

            sheets = sorted(

                (n for n in names if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')),

                key=lambda n: int(re.sub(r'[^0-9]', '', n.rsplit('/', 1)[-1]) or 0))

            parts = []

            total = 0

            for i, sn in enumerate(sheets):

                title = titles[i] if i < len(titles) else ('Sheet%d' % (i + 1))

                parts.append('工作表「%s」' % title)

                try:

                    ws = ET.fromstring(z.read(sn))

                except ET.ParseError:

                    continue

                for row in ws.iter(_NS_X + 'row'):

                    cells = []

                    for c in row.iter(_NS_X + 'c'):

                        t = c.get('t') or ''

                        v = None

                        if t == 'inlineStr':

                            v = ''.join(x.text or '' for x in c.iter(_NS_X + 't'))

                        else:

                            vn = c.find(_NS_X + 'v')

                            if vn is not None and vn.text is not None:

                                if t == 's':          # 共享字符串的索引

                                    try:

                                        v = shared[int(vn.text)]

                                    except (ValueError, IndexError):

                                        v = ''

                                elif t == 'b':

                                    v = '是' if vn.text.strip() == '1' else '否'

                                else:

                                    v = vn.text

                        v = (v or '').strip()

                        if v:

                            cells.append(v)

                    if not cells:

                        continue

                    line = '第 %s 行：%s' % (row.get('r') or (len(parts)), ' | '.join(cells))

                    total += len(line)

                    if total > MAX_BODY:

                        break

                    parts.append(line)

                if total > MAX_BODY:

                    break

            return '\n'.join(parts)[:MAX_BODY] or None

    except (zipfile.BadZipFile, OSError, KeyError):

        return None





def _csv_text(path: Path):

    """CSV：按行读，保留「第 N 行」前缀，GBK/UTF-8 都认（Excel 导出的 CSV 常是 GBK）。"""

    try:

        with open(path, 'rb') as f:

            raw = f.read(MAX_BODY * 3)

        text = _decode_text(raw)

    except OSError:

        return None

    if not text:

        return None

    out = []

    total = 0

    for i, line in enumerate(text.splitlines(), 1):

        line = line.strip()

        if not line:

            continue

        cells = [c.strip().strip('"') for c in line.split(',')]

        line = '第 %d 行：%s' % (i, ' | '.join(c for c in cells if c))

        total += len(line)

        if total > MAX_BODY:

            break

        out.append(line)

    return '\n'.join(out)[:MAX_BODY] or None





_COM_STATE = {}          # {'doc': True/False, 'xls': ..., 'ppt': ...}：探过一次就记住，别对 238 个文件重复撞墙



_COM_PROGS = {'doc': ('Word.Application', 'Kwps.Application'),

              'xls': ('Excel.Application', 'KET.Application'),

              'ppt': ('PowerPoint.Application', 'KWPP.Application')}





def _com_kind(ext: str):

    """旧格式 → 需要哪种 COM 应用；不是旧格式返回 None。"""

    if ext in ('.xls', '.et'):

        return 'xls'

    if ext in ('.ppt', '.dps'):

        return 'ppt'

    if ext in ('.doc', '.wps', '.rtf'):

        return 'doc'

    return None





def _com_probe(kind: str) -> bool:

    """母进程里试一次「这台机器有没有 Word/Excel/WPS」，结果记住。



    抽正文是**每篇一个子进程**，子进程里探不到共享缓存；如果不在这里先探，

    没装 Office 的机器会为每个旧格式文件白起一个子进程、白等一次 COM 失败。

    探一次约 0.3 秒，之后 238 个 .doc 直接判「读不了」。"""

    if kind in _COM_STATE:

        return _COM_STATE[kind]

    ok = False

    app = None

    try:

        import win32com.client as _wc

        for prog in _COM_PROGS.get(kind, ()):

            try:

                app = _wc.DispatchEx(prog)

                ok = True

                break

            except Exception:

                continue

    except Exception:

        ok = False

    finally:

        try:

            if app is not None:

                app.Quit()

        except Exception:

            pass

    _COM_STATE[kind] = ok

    return ok





def _ole_text(path: Path, ext: str):

    """旧版 Office / WPS 二进制格式：借本机装的 Word / Excel / PowerPoint（或 WPS）走 COM 读文本。



    这是唯一不引入第三方转换器的办法。三条纪律：

      1) DispatchEx 起独立实例，不劫持用户已经开着的 Word；

      2) Visible=False、DisplayAlerts=0、AutomationSecurity=3（禁宏）、ReadOnly，绝不弹窗、绝不改文件；

      3) 任何失败都返回 None，且把「这台机器没有这个 COM」记进 _COM_STATE，后续文件直接跳过。

    """

    kind, progs = (

        ('xls', ('Excel.Application', 'KET.Application')) if ext in ('.xls', '.et')

        else ('ppt', ('PowerPoint.Application', 'KWPP.Application')) if ext in ('.ppt', '.dps')

        else ('doc', ('Word.Application', 'Kwps.Application'))

    )

    if _COM_STATE.get(kind) is False:

        return None

    try:

        import win32com.client as _wc

    except Exception:

        _COM_STATE[kind] = False

        return None



    app = None

    try:

        app = None

        for prog in progs:

            try:

                app = _wc.DispatchEx(prog)

                break

            except Exception:

                continue

        if app is None:

            _COM_STATE[kind] = False

            return None

        _COM_STATE[kind] = True

        try:

            app.Visible = False

        except Exception:

            pass

        for attr, val in (('DisplayAlerts', 0), ('AutomationSecurity', 3),

                          ('AskToUpdateLinks', False), ('ShowWindows', 0)):

            try:

                setattr(app, attr, val)

            except Exception:

                pass



        text = None

        if kind == 'doc':

            # 只用位置参数：不同版本的 Word / WPS 关键字名不一样（AddToRecentFiles / AddToMru 之类）

            doc = app.Documents.Open(str(path), False, True)

            try:

                text = doc.Range().Text

            finally:

                try:

                    doc.Close(False)

                except Exception:

                    pass

        elif kind == 'xls':

            wb = app.Workbooks.Open(str(path), 0, True)   # 文件名, 不更新外链, 只读

            try:

                lines = []

                for ws in wb.Worksheets:

                    lines.append('工作表「%s」' % (ws.Name or ''))

                    vals = ws.UsedRange.Value      # 一次取整块，比逐格读快几十倍

                    if vals is None:

                        continue

                    if not isinstance(vals, tuple):

                        vals = ((vals,),)

                    for row in vals:

                        if not isinstance(row, tuple):

                            row = (row,)

                        cells = [str(v).strip() for v in row if v is not None and str(v).strip()]

                        if cells:

                            lines.append(' | '.join(cells))

                        if sum(len(x) for x in lines) > MAX_BODY:

                            break

                text = '\n'.join(lines)

            finally:

                try:

                    wb.Close(False)

                except Exception:

                    pass

        else:  # ppt

            pres = app.Presentations.Open(str(path), -1, 0, 0)   # 只读、不新建、无窗口

            try:

                lines = []

                for i, slide in enumerate(pres.Slides, 1):

                    parts = []

                    for shape in slide.Shapes:

                        try:

                            if shape.HasTextFrame and shape.TextFrame.HasText:

                                parts.append(str(shape.TextFrame.TextRange.Text))

                        except Exception:

                            continue

                    if parts:

                        lines.append('第 %d 页：%s' % (i, ' '.join(parts)))

                text = '\n'.join(lines)

            finally:

                try:

                    pres.Close()

                except Exception:

                    pass

        try:

            app.Quit()

        except Exception:

            pass

        app = None

        if not text:

            return None

        text = text.replace('\r', '\n').replace('\x07', '  ')

        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()[:MAX_BODY] or None

    except Exception:

        return None

    finally:

        try:

            if app is not None:

                app.Quit()

        except Exception:

            pass





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

    if ext in ('.xlsx', '.xlsm'):

        # 表格单独走一条路：按「工作表 → 行 → 单元格」拼成可读的行，

        # 这样命中片段能显示「第 12 行：姓名 | 电话 | 地址」，而不是把 XML 拍平成一行乱码

        return _xlsx_text(path)

    if ext == '.csv':

        return _csv_text(path)

    if ext in ('.docx', '.pptx', '.epub'):

        try:

            texts = []

            with zipfile.ZipFile(path) as z:

                for n in z.namelist():

                    low = n.lower()

                    if ext == '.docx':

                        keep = low == 'word/document.xml'

                    elif ext == '.pptx':

                        keep = low.startswith(('ppt/slides/slide', 'ppt/notesslides/'))

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

    # 旧版二进制 Office（.doc/.xls/.ppt）与 WPS 自有格式（.wps/.et/.dps）：

    # 没有纯 Python 的可靠解法，借本机装着的 Word / Excel / WPS 走一次 COM。

    if ext in ('.doc', '.xls', '.ppt', '.wps', '.et', '.dps', '.rtf'):

        return _ole_text(path, ext)

    return None



EXTRACT_TIMEOUT = 30         # 单个 PDF 的解析上限（秒）；要给 OCR 预算留出余量

OLE_EXTS = ('.doc', '.xls', '.ppt', '.wps', '.et', '.dps', '.rtf')   # 需要借 Office/WPS 的旧格式

OLE_TIMEOUT = 40             # 旧格式上限更宽：Word 冷启动就要 1 秒多



def _extract_cmd(path_str, out_path):

    """构造「提取子进程」的命令行；源码运行和打包成 exe 都适用"""

    if getattr(sys, 'frozen', False):

        return [sys.executable, '_extract', path_str, out_path]

    return [sys.executable, str(Path(__file__).resolve()), '_extract', path_str, out_path]



_EXTRACT_TMP = None



def _extract_tmp() -> Path:

    """子进程回传结果的临时文件，全进程复用一个；退出时删掉。



    **不能放在程序目录**：里面是一篇文档的完整正文（明文 JSON）。程序目录会被打包、

    会被 git add -A、在多用户机器上别人还读得到；进程被强杀或断电时 atexit 不会执行，

    这份正文就永久留在盘上。放 %LOCALAPPDATA%/SecondBrain/tmp，并顺手清掉上次崩溃的遗留。"""

    global _EXTRACT_TMP

    if _EXTRACT_TMP is None:

        base = Path(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')) / 'SecondBrain' / 'tmp'

        try:

            base.mkdir(parents=True, exist_ok=True)

            for old in base.glob('.extract-*.json'):

                try:

                    if time.time() - old.stat().st_mtime > 3600:

                        old.unlink()

                except OSError:

                    pass

        except OSError:

            base = app_dir()

        _EXTRACT_TMP = base / f'.extract-{os.getpid()}.json'

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

    ext = path.suffix.lower()

    if ext == '.pdf':

        return _extract_subprocess(path, EXTRACT_TIMEOUT)

    if ext in OLE_EXTS:

        kind = _com_kind(ext)

        if kind and not _com_probe(kind):

            return None, '这种旧格式要本机装 Office 或 WPS 才能读'

        # 旧版 Office / WPS 走 COM：可能弹窗、可能卡在"正在修复"对话框，

        # 所以同样放到子进程里，超时能真 kill 掉（Word 实例在子进程退出时一起走）。

        return _extract_subprocess(path, OLE_TIMEOUT)

    try:

        return extract_text(path), None

    except Exception as e:

        return None, f'解析异常（{type(e).__name__}）'



def _extract_subprocess(path: Path, timeout: float):

    """把单个文件的解析丢进子进程，超时就 kill。PDF 与旧版二进制 Office 共用。"""

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

        proc.wait(timeout=timeout)

    except subprocess.TimeoutExpired:

        proc.kill()

        try:

            proc.wait(timeout=5)

        except Exception:

            pass

        return None, f'解析超时（>{int(timeout)}s）'

    try:

        data = json.loads(tmp.read_text(encoding='utf-8'))

    except Exception:

        return None, '解析失败'

    if str(data.get('p') or '') != str(path):     # 复用的临时文件，残留不能当真

        return None, '解析失败'

    return data.get('t'), data.get('w')



def _doc_rids(con):

    """path → rowid 映射。



    docs 是 FTS5 虚表，path 又是 UNINDEXED 列，所以任何 `WHERE path=?` 的删除/更新

    都会退化成整表扫描：实测 3.4GB 的库上删一行要 1.4 秒，而按 rowid 删是 1 毫秒。

    重索引上千篇文件就白扔二十多分钟。先花一次（约 1.5 秒）把映射取到内存，

    之后全按 rowid 操作。只读 path 列，不用把正文也读出来。"""

    return {p: r for r, p in con.execute('SELECT rowid, path FROM docs')}



def _doc_del(con, rids, path):

    """按路径删索引行（走 rowid，必要时才回退到慢路径），连带清 bigram 旁表。



    bigram（2 字中文查询的倒排旁表，见 _cjk_grams）按 rid 挂在 docs 行上，

    rowid 一动它就是死键——所以删除必须成对做，这函数是唯一的删除咽喉点。

    慢路径必须按 lower() 匹配：正文近实时送进来的路径可能已经 normcase 过

    （_BODYQ_DEL 存的就是 normcase 键），而 docs.path 存的是扫描时的原始大小写。"""

    rid = rids.pop(os.path.normcase(path), None)

    if rid is not None:

        con.execute('DELETE FROM docs WHERE rowid=?', (rid,))

        con.execute('DELETE FROM bigram WHERE rid=?', (rid,))

    else:

        rs = [r[0] for r in con.execute('SELECT rowid FROM docs WHERE lower(path)=lower(?)',

                                        (path,))]

        if not rs:

            return

        con.execute('DELETE FROM bigram WHERE rid IN (%s)'

                    % ','.join('?' * len(rs)), rs)

        con.execute('DELETE FROM docs WHERE lower(path)=lower(?)', (path,))





# ---------------------------------------------------------------- 2 字中文查询：bigram 旁表

# trigram tokenizer 只索引 ≥3 字符的子串，「微信」「简历」这类 2 字中文词永远 MATCH 不中，

# 只能降级 body LIKE 全表扫（5,700 篇实测 0.3~0.9s）。FTS5 没有内置 bigram tokenizer、

# Python 也不暴露自定义 tokenizer 的 C 接口，所以按 appdesc/recent 的「小表分流」先例

# 建旁表倒排：每篇正文里出现过的 CJK 二元组（去重），查询时 rowid IN 直取。

_CJK_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+')





def _is_cjk_word(t):

    """整个词都是 CJK 表意文字（2 字查询路由的判据，ASCII/标点不进旁表）"""

    return bool(t) and all('\u3400' <= c <= '\u4dbf' or '\u4e00' <= c <= '\u9fff'

                           or '\uf900' <= c <= '\ufaff' for c in t)





def _cjk_grams(text):

    """正文里的 CJK 二元组，每篇内去重保序。纯函数：写入（index/_body_apply）与

    回填共用，单测锁行为。"""

    out, seen = [], set()

    for seg in _CJK_RE.findall(text or ''):

        for i in range(len(seg) - 1):

            g = seg[i:i + 2]

            if g not in seen:

                seen.add(g)

                out.append(g)

    return out





def _bigram_add(con, rid, text):

    """把一篇正文的大码写进旁表（与 INSERT INTO docs 成对调用）。

    注意：不置就绪标记——就绪只能由回填完成（_bigram_backfill）置位，

    否则回填中途路由过去，2 字查询只看得到已回填的子集。"""

    gs = _cjk_grams(text)

    if gs:

        con.executemany('INSERT OR IGNORE INTO bigram(gram,rid) VALUES(?,?)',

                        [(g, rid) for g in gs])





def _kv_get(con, key, default=''):

    r = con.execute('SELECT val FROM kv WHERE key=?', (key,)).fetchone()

    return r[0] if r else default





def _kv_set(con, key, val):

    con.execute('INSERT OR REPLACE INTO kv(key, val) VALUES(?,?)', (key, str(val)))





_BIGRAM_READY = [False, 0.0]     # [就绪?, 上次检查时间] —— 10s TTL，别每个查询都查一次表





def _bigram_ready(con):

    """旁表就绪（kv 标记，由回填线程完成时写入）才路由过去；没就绪自动落回 LIKE，

    老库迁移前后检索行为都不回退。"""

    now = time.time()

    if now - _BIGRAM_READY[1] < 10:

        return _BIGRAM_READY[0]

    try:

        ok = _kv_get(con, 'bigram_done') == '1'

    except Exception:

        ok = False

    _BIGRAM_READY[0], _BIGRAM_READY[1] = ok, now

    return ok





def _bigram_backfill():

    """老库回填：把已有 docs 正文的大码写进 bigram 旁表。不重抽任何源文件——

    正文就在 docs 里，读出来重切即可。分块 200 篇、断点记 kv('bigram_until')、

    全量任务在跑时让路（同 _body_worker 的纪律）。完成后置 kv('bigram_done')。"""

    time.sleep(4)                       # 让启动路径先走完（db_upkeep 等）

    try:

        con = init_db()

        last = int(_kv_get(con, 'bigram_until', '0') or 0)

        total = con.execute('SELECT COUNT(*) FROM docs').fetchone()[0]

        if not total:

            _kv_set(con, 'bigram_done', '1')     # 空库：新正文都会经 _bigram_add 进来

            con.commit()

            return

        t0 = time.time()

        while True:

            if _job_busy():

                time.sleep(3)

                continue

            rows = con.execute('SELECT rowid, body FROM docs WHERE rowid > ? '

                               'ORDER BY rowid LIMIT 200', (last,)).fetchall()

            if not rows:

                break

            for rid, body in rows:

                _bigram_add(con, rid, body)

            last = rows[-1][0]

            _kv_set(con, 'bigram_until', last)

            con.commit()

        _kv_set(con, 'bigram_done', '1')

        con.commit()

        n = con.execute('SELECT COUNT(*) FROM bigram').fetchone()[0]

        _log_file('bigram backfill done: %d docs → %d grams in %.0fs'

                  % (total, n, time.time() - t0))

    except Exception as e:

        _log_file('bigram backfill FAILED (断点已存，下次启动续跑): %r' % (e,))



def init_db():

    # check_same_thread=False：HTTP 层共用一条连接，由 con_lock 保证不并发使用

    con = sqlite3.connect(DB, check_same_thread=False)

    con.execute('PRAGMA journal_mode=WAL')   # 读写并发：建索引时搜索不再被锁死

    con.execute('PRAGMA busy_timeout=5000')  # 写冲突时等 5 秒，而不是立刻抛 locked

    con.execute('PRAGMA wal_autocheckpoint=2000')  # 长事务下 1000 页（4MB）太密

    # reverse_rows 用 lower(path)=lower(?) 查 meta，BINARY 主键失效只能全扫（21ms）；

    # 建成 NOCASE 索引后 0.07ms。idx_files_dir 没人用，顺手删掉。

    # 注意：不要在这里建/删索引。init_db() 每条连接都跑一次，

    # 真做 DDL 会去抢写锁，和常驻窗口的读快照撞成 "database is locked"。

    # 索引只在 names_index()（整表重建）里维护，见 idx_meta_path_nc。

    con.execute('PRAGMA auto_vacuum=INCREMENTAL')  # 新库：删东西时顺手把页还回去

    con.execute("CREATE TABLE IF NOT EXISTS meta(path TEXT PRIMARY KEY, indexed_at REAL)")

    for col, decl in (('size', 'REAL'), ('mtime', 'REAL'), ('reason', 'TEXT')):

        try:

            con.execute(f'ALTER TABLE meta ADD COLUMN {col} {decl}')

        except Exception:

            pass  # 列已存在

    con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(

                   path UNINDEXED, ext UNINDEXED, body, tokenize='trigram')""")

    # 2 字中文查询的旁表倒排（见 _cjk_grams）：每篇正文的 CJK 二元组 → docs.rowid。

    # kv 存后台回填的断点（'bigram_until'），断了下次接着跑。

    con.execute("""CREATE TABLE IF NOT EXISTS bigram(

                   gram TEXT NOT NULL, rid INTEGER NOT NULL,

                   PRIMARY KEY(gram, rid)) WITHOUT ROWID""")

    con.execute('CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, val TEXT)')

    # 全盘文件名索引：只存路径/名字/大小/时间，不读内容。让「文件名命中」这栏

    # 覆盖整个磁盘，而不是只覆盖已入库的几千篇——这正是原先要去问 Everything 的那件事。

    # name 存小写，查询时把关键词也转小写，这样 instr 只用一次 lower()。

    # dirs 原来只在 names_index() 里建：首启的快速文件名阶段先写 files 再写 dirs，

    # 新库上 dirs 不存在会让整批（含 files）回滚 → 首屏一条都搜不到。这里补一次。

    con.execute('CREATE TABLE IF NOT EXISTS dirs(path TEXT PRIMARY KEY, name TEXT NOT NULL, '

                'parent TEXT, py TEXT, ini TEXT)')

    # 老库的 dirs 没有 mtime（当时觉得「文件夹不需要时间」），导致首页点「文件夹」筛不出东西。

    # 补一列；历史行先留 NULL，由 backfill_dir_mtime() 慢慢填。

    try:

        cols = {r[1] for r in con.execute('PRAGMA table_info(dirs)')}

        if 'mtime' not in cols:

            con.execute('ALTER TABLE dirs ADD COLUMN mtime REAL')

            con.commit()

    except sqlite3.Error:

        pass

    try:

        con.execute('CREATE INDEX IF NOT EXISTS idx_dirs_mtime ON dirs(mtime)')

    except sqlite3.Error:

        pass

    con.execute("""CREATE TABLE IF NOT EXISTS files(

                   path TEXT PRIMARY KEY, name TEXT NOT NULL, dir TEXT,

                   ext TEXT, size REAL, mtime REAL)""")

    # 应用中文名：exe/lNK 的 FileDescription 字段。搜「微信」能匹配上 wechat.exe。

    # Listary 也是靠 VerInfo 做这个，比纯文件名匹配准多了。

    try:

        con.execute('ALTER TABLE files ADD COLUMN descr TEXT')

    except Exception:

        pass  # 列已存在

    # 应用中文名的独立小表：files 有 60 万行，`instr(descr,?)` 是全表扫（实测 174ms，

    # 而只有 3~4 千个 exe/lnk 有描述）。单独存一份，搜中文应用名的代价从 174ms 降到 ~1ms。

    con.execute('CREATE TABLE IF NOT EXISTS appdesc(path TEXT PRIMARY KEY, descr TEXT, '

                'ext TEXT, mtime REAL, size REAL)')

    try:

        if con.execute('SELECT 1 FROM appdesc LIMIT 1').fetchone() is None:

            con.execute('INSERT OR REPLACE INTO appdesc SELECT path, descr, ext, mtime, size '

                        "FROM files WHERE descr IS NOT NULL AND descr<>''")

            # 必须提交：不提交的话别的连接（搜索那条）看不到这张表，等于白补

            con.commit()

    except sqlite3.Error:

        # 老库还没有 descr 列/没建过 files：等下次全盘索引时再补。

        # 必须回滚：失败的 INSERT 会留一个隐式事务在开着，后面所有写都撞 database is locked。

        try:

            con.rollback()

        except sqlite3.Error:

            pass

    # 拼音：让纯字母输入也能命中中文名的普通文件（wx → 微信.lnk / 微信截图.png）。

    # 只给中文名存值，纯英文名留 NULL —— 英文靠 name 前缀就够，不为它多占空间。

    for col in ('py', 'ini'):

        try:

            con.execute(f'ALTER TABLE files ADD COLUMN {col} TEXT')

        except Exception:

            pass  # 列已存在

    for _ix, _col in (('idx_files_py', 'py'), ('idx_files_ini', 'ini')):

        try:

            con.execute(f'CREATE INDEX IF NOT EXISTS {_ix} ON files({_col})')

        except sqlite3.Error:

            pass

    try:

        con.execute('CREATE INDEX IF NOT EXISTS idx_files_name ON files(name)')

    except sqlite3.Error:

        pass

    for _ix, _col in (('idx_files_mtime', 'mtime'),):

        try:

            con.execute(f'CREATE INDEX IF NOT EXISTS {_ix} ON files({_col})')

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

    # 建表和补表都是写操作，走默认事务；统一落地一次，免得「表建了、数据没提交」

    try:

        con.commit()

    except sqlite3.Error:

        pass

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



def _count_files(con):

    """库内文件名条数（`files` 表）。



    和 `_count_docs` 是两个不同的口径，界面必须分开说：files 是「搜名字能搜到的

    文件数」（60 万量级），docs/meta 是「正文抽出来能搜内容的篇数」（5 千量级）。

    混成一个数字，用户会以为自己只有 5 千个文件可搜。"""

    try:

        return con.execute('SELECT count(*) FROM files').fetchone()[0]

    except sqlite3.Error:

        return 0





_REPORT = {'mt': None, 't': 0.0, 'body': None}

# /api/env 的结果缓存（环境自检要查注册表、枚举显示器、quick_check 数据库，跑一次不便宜）
_ENV_CACHE = {}





def scope_report(con):

    """设置抽屉「索引范围」用：盘符分布 + 占用最多的目录 + 排除词 + 扫描规则。



    60 万行 GROUP BY dir 约 250ms，而用户每开一次设置都要这个数 —— 缓存 30 秒，

    并且用 scope.json 的 mtime 做键（改了范围立刻失效，不会被旧缓存骗）。

    调用方必须已持有 con_lock。"""

    sp = app_dir() / 'scope.json'

    try:

        mt = sp.stat().st_mtime

    except OSError:

        mt = 0

    now = time.time()

    if (_REPORT['body'] is not None and _REPORT['mt'] == mt

            and now - _REPORT['t'] < 30):

        return _REPORT['body']

    conf = _scope_conf()

    include = [str(x) for x in (conf.get('include_dirs') or []) if str(x).strip()]

    exclude = [str(x) for x in (conf.get('exclude_dirs') or []) if str(x).strip()]

    drives, top = [], []

    try:

        # 一次全表扫描同时算出「按盘」和「按二级目录」：分开查就是扫两遍 60 万行

        agg = {}

        for d, c in con.execute('SELECT dir, count(*) FROM files GROUP BY dir'):

            agg[d] = c

        dv, tp = {}, {}

        for d, c in agg.items():

            dv[d[:2].upper()] = dv.get(d[:2].upper(), 0) + c

            # 二级前缀：'E:\\SteamLibrary\\steamapps\\x' → 'E:\\SteamLibrary'

            seg = d.split('\\')

            key = '\\'.join(seg[:3]) if len(seg) > 3 else d

            tp[key] = tp.get(key, 0) + c

        drives = sorted(dv.items(), key=lambda kv: -kv[1])

        top = sorted(tp.items(), key=lambda kv: -kv[1])[:6]

    except sqlite3.Error:

        pass

    body = {'include_dirs': include, 'exclude_dirs': exclude,

            'drives': drives, 'top': top,

            'files': sum(c for _, c in drives),

            'docs': _count_docs(con),

            'names_state': NAMES.get('state'),

            'rules': {'ext': len(NAMES_EXTS), 'skip': len(NAMES_SKIP_DIRS),

                      'size_limit': NAMES_SIZE_LIMIT}}

    _REPORT.update({'mt': mt, 't': now, 'body': body})

    return body



_SUB_CACHE = {}          # _subs 的 30 秒缓存：{'docs': int, 'files': int, 't': float}





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

    # 键一律用 normcase(path)：Windows 不区分大小写，若按原始字符串比对，

    # 「d:\work\a.md」和「D:\work\a.md」会被当成两个文件——库里的重复行就是这么来的

    # （2026-11-04 实测 docs 8,931 行里 3,671 行是重复、meta 39,890 里 3,937 行）。

    meta_info = {os.path.normcase(r[0]): r for r in

                 con.execute('SELECT path, indexed_at, size, mtime FROM meta')}

    rids = {os.path.normcase(p_): r for p_, r in (_doc_rids(con) or {}).items()}



    def _needs(p, pl, dry=False):

        """ True=需要(重新)索引：范围外 False；新文件 True；

        已索引但大小+修改时间都没变 False；旧库条目补登记后 False；索引后改过的 True

        dry=True 时只判断不写库（用于预算 todo，避免预计算把待办偷偷改掉、进度永远到不了 100%）"""

        pl = os.path.normcase(os.path.normpath(pl))

        if not any(pl.startswith(d) for d in include) or any(x in pl for x in exclude):

            return False

        if os.path.normcase(p) not in meta_info:

            return True

        try:

            st = os.stat(p)

        except OSError:

            return False

        key = os.path.normcase(p)

        at, sz, mt = meta_info[key][1:]

        if sz is not None and mt is not None:

            return not (abs(st.st_size - sz) < 1 and abs(st.st_mtime - mt) < 2)

        if st.st_mtime <= (at or 0) + 60:      # 旧库条目：补记 size/mtime，不重建

            if not dry:

                con.execute('UPDATE meta SET size=?, mtime=? WHERE path=?',

                            (st.st_size, st.st_mtime, p))

                meta_info[key] = (p, at, st.st_size, st.st_mtime)

            return False

        return True                             # 索引之后文件改过 → 重建该条



    todo = sum(1 for p, _sz in rows if _needs(p, p.lower(), dry=True))

    # 预算阶段不写库，正式跑第一遍时这些旧条目会被补登记（返回 False、不算失败），

    # 所以进度用「已处理条数」而不是「成功+失败」来推进。

    n_new = n_skip = n_fail = n_done = 0

    _prog(state='busy', phase='index', done=0, total=todo, ok=0, fail=0, err='',

          msg='建立索引中', k='m_indexing', a={'n': todo})

    t0 = time.time()

    # 单个扫描版 PDF 可能要解析几十秒，主循环那时一条输出都没有，看起来就是卡死。

    # 一只看门狗线程：10 秒没换文件就报一句"正在解析 X"，让人知道它还活着。

    _watch = {'doc': '', 'ts': time.time(), 'stop': False}



    def _watchdog():

        while not _watch['stop']:

            time.sleep(5)

            if _watch['doc'] and time.time() - _watch['ts'] > 10:

                print(f'  ...正在解析：{os.path.basename(_watch["doc"])}'

                      f'（大文件，可能要几十秒）', flush=True)

                _watch['ts'] = time.time()



    threading.Thread(target=_watchdog, daemon=True).start()

    for p, _sz in rows:

        if _is_self_noise(p):            # 程序自己的库/清单/打包产物/临时文件，别收进正文索引

            n_skip += 1

            continue

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

        _watch['doc'] = p

        _watch['ts'] = time.time()

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

            rids[os.path.normcase(p)] = cur.lastrowid

            _bigram_add(con, cur.lastrowid, text)   # 2 字中文查询的旁表倒排，成对维护

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

            _prog(done=n_done, ok=n_new, fail=n_fail, eta=int(eta))   # 界面的「预计还需」

            print(f'  ...{n_done}/{todo} 已处理（成功 {n_new}、失败 {n_fail}）, {el:.0f}s, '

                  f'{rate:.1f}篇/s, 预计还需 {eta:.0f}s', flush=True)

    _watch['stop'] = True

    con.commit()

    try:

        # 'optimize' 会把整份 FTS 索引重写一遍：真库实测 3.5 秒、写出 264.8MB WAL，

        # 随后 checkpoint 又被读者挡住 → WAL 长期几百 MB。改成 merge（只合并小段），

        # 每次几毫秒，效果足够。

        con.execute("INSERT INTO docs(docs, rank) VALUES('merge', 8)")

        con.commit()

    except Exception:

        # 失败必须回滚：这里曾经写成 'merge=8,2000'（SQL logic error），

        # 异常被吞掉后隐式事务还挂着 → 之后别的连接一写就 "database is locked"

        try:

            con.rollback()

        except Exception:

            pass

    try:

        # 索引期间 WAL 会一路长到接近 1GB（实测 895MB），结束时收回去

        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    except Exception:

        pass

    total = _count_docs(con)

    _prog(state='done', phase='', done=n_done, total=todo, ok=n_new, fail=n_fail,

          eta=0, msg=f'完成，库内共 {total} 篇', k='m_index_done', a={'n': f'{total:,}'})

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

    # 用户从别处粘贴来的短语常自带引号：再包一层会变成三个引号，FTS5 当语法错、MATCH 0 条。

    toks = [t.strip('"\u201c\u201d\u2018\u2019') for t in toks]

    toks = [t for t in toks if t]

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

    if not e:

        return ('', [])

    if ',' in e:

        parts = [x.strip().lstrip('.') for x in e.split(',') if x.strip()]

        if not parts:

            return ('', [])

        return (' AND ext IN (%s)' % ','.join('?' * len(parts)), ['.' + x for x in parts])

    return (' AND ext = ?', [f'.{e}'])



def _like_esc(t):

    """把 LIKE 的通配符转义掉。



    `_` 和 `%` 在 LIKE 里是通配符：搜「_」会匹配任何一个字符、搜「a_」会匹配所有

    含 a 的文档——等于把单字查询变成全库 dump。转义后才是字面匹配。"""

    return t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')





def _search_sql(q, ext=None, pf=None, con=None):

    """返回 (sql, args)，网页与命令行共用同一套检索逻辑。



    三条分支：≥3 字符走 trigram MATCH（有 bm25 排序）；全是 2 字中文且旁表有数据走

    bigram 倒排（_cjk_grams，rowid IN 直取）；其余短词（1 字、AI/C 这类）降级 LIKE。"""

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

    # 2 字中文（微信/简历/报告这类最高频查询）走 bigram 旁表：每个 token 一个

    # rowid IN 子查询，多 token 天然取交集；预览片段照抄 LIKE 分支的 substr+instr。

    if (con is not None and toks

            and all(len(t) == 2 and _is_cjk_word(t) for t in toks)

            and _bigram_ready(con)):

        ins = ' AND '.join(['rowid IN (SELECT rid FROM bigram WHERE gram=?)'] * len(toks))

        sql = ("SELECT path, substr(body, max(1, instr(lower(body), lower(?)) - 30), 90) "

               "FROM docs WHERE " + ins + extra + " LIMIT ?")

        return sql, [toks[0], *toks, *args]

    # 短词（含 1 字中文、AI、C 这类）：trigram 命中不了，降级 LIKE。

    # 预览定位到第一个词的命中位置，而不是正文开头。

    like = ' AND '.join(["body LIKE ? ESCAPE '\\'"] * len(toks)) or '1=1'

    # instr 区分大小写、LIKE 不区分：搜 ai/AI 时 instr 找不到 → 片段退回正文开头。

    sql = ("SELECT path, substr(body, max(1, instr(lower(body), lower(?)) - 30), 90) "

           "FROM docs "

           "WHERE " + like + extra + " LIMIT ?")

    return sql, [toks[0] if toks else '', *[f'%{_like_esc(t)}%' for t in toks], *args]



def _mark_text(text, toks):

    """给一段纯文本里的命中词套上 SNIP_OPEN/SNIP_CLOSE，供前端高亮。

    FTS 分支由 snippet() 直接给标记；LIKE 降级（两字中文、AI 这类短词）没有标记，

    这里补上，否则「正文命中」只有文字、看不出为什么命中。"""

    s = str(text or '')

    low = s.lower()

    spans = []

    for t in toks:

        tl = str(t or '').lower()

        if not tl:

            continue

        start = 0

        while True:

            i = low.find(tl, start)

            if i < 0:

                break

            spans.append((i, i + len(tl)))

            start = i + len(tl)

    if not spans:

        return s

    spans.sort()

    out, pos = [], 0

    for a, b in spans:

        if b <= pos:

            continue

        a = max(a, pos)

        out.append(s[pos:a])

        out.append(SNIP_OPEN + s[a:b] + SNIP_CLOSE)

        pos = b

    out.append(s[pos:])

    return ''.join(out)





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

LAST_Q = ''                       # 快搜窗最后在搜的词，托盘「打开详细搜索」带着它开主窗口

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



    Listary 的「越用越准」就是靠这张表 —— 同一档匹配里，你常开的排前面。"""

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

    Listary V7 起打开启动器就是这一屏，一个字都不用打。"""

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





# 左侧栏「类型」→ 扩展名。跟前端 KIND 一一对应（前端只传名字，扩展名映射放后端一处维护）。

_KIND_EXTS = {

    'doc': ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.md', '.txt',

            '.rtf', '.csv', '.wps', '.et', '.dps', '.epub', '.xmind'),

    'img': ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.ico', '.heic',

            '.tif', '.tiff', '.psd', '.raw', '.cr2', '.nef'),

    'zip': ('.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso'),

    'app': ('.exe', '.lnk', '.bat', '.cmd', '.msi', '.appref-ms', '.url', '.ps1'),

    'audio': ('.mp3', '.flac', '.wav', '.m4a', '.aac', '.ogg', '.wma', '.ape'),

    'video': ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.ts', '.m4v'),

}





def _kind_exts(kind):

    """把左侧栏的类型名换成扩展名元组；认不出或 'all' 返回 None（不过滤）。"""

    k = (kind or '').strip().lower()

    if not k or k == 'all':

        return None

    return _KIND_EXTS.get(k)





def backfill_dir_mtime(con, limit=0, batch=2000):

    """给老库的 dirs 补 mtime（一列是后加的，历史行全是 NULL）。



    为什么要补：首页左侧点「文件夹」要按天分组，没有 mtime 就只能显示空。

    limit=0 表示全补；给个数字就每次启动补一小批，别把启动拖长。

    返回补了多少条。目录已经不存在的不硬塞时间（保持 NULL），留给 prune。

    """

    sql = 'SELECT path FROM dirs WHERE mtime IS NULL'

    args = []

    if limit:

        sql += ' LIMIT ?'

        args.append(int(limit))

    rows = con.execute(sql, args).fetchall()

    n = 0

    for (path,) in rows:

        try:

            mt = os.stat(path).st_mtime

        except OSError:

            continue

        con.execute('UPDATE dirs SET mtime=? WHERE path=?', (mt, path))

        n += 1

        if n % batch == 0:

            con.commit()

            _prog(k='m_dir_mtime', a={'n': n})

    con.commit()

    return n





def _dir_rows(con, days, per_day, pf, fallback=False):

    """文件夹时间轴：dirs 表按 mtime 分天。



    老库刚补上 mtime 时可能有 NULL（还没回填），那些行先不显示——

    显示成「1970 年改过」比不显示更让人困惑。

    """

    now = time.time()

    since = now - days * 86400

    a_win, a_day = [], []

    if not fallback:

        a_win.append('mtime>=?')

        a_win.append('mtime<?')

        a_day.extend([since, now + 3600])

    w_win = (' AND ' + ' AND '.join(a_win)) if a_win else ''

    # 老库可能已经把自己的目录写进去了，查询时挡掉（不用等下一次重建）

    w_win += " AND path NOT LIKE ?"

    a_day.append(os.path.normcase(str(app_dir())) + os.sep + '%')

    if pf:

        w_win += ' AND path LIKE ?'

        a_day.append('%' + pf + '%')

    rows = con.execute(

        'SELECT strftime("%Y-%m-%d", mtime, "unixepoch", "localtime") day, COUNT(*) '

        'FROM dirs WHERE mtime IS NOT NULL' + w_win +

        ' GROUP BY day ORDER BY day DESC LIMIT ?', a_day + [days]).fetchall()

    out, today = [], time.strftime('%Y-%m-%d')

    yday = time.strftime('%Y-%m-%d', time.localtime(now - 86400))

    for day, total in rows:

        if fallback:

            sel = con.execute(

                'SELECT path, name, mtime, parent FROM dirs WHERE mtime IS NOT NULL' + w_win +

                ' ORDER BY mtime DESC LIMIT ?', a_day + [per_day]).fetchall()

        else:

            d0 = time.mktime(time.strptime(day, '%Y-%m-%d'))

            sel = con.execute(

                'SELECT path, name, mtime, parent FROM dirs'

                ' WHERE mtime>=? AND mtime<?' + (' AND path LIKE ?' if pf else '') +

                ' ORDER BY mtime DESC LIMIT ?',

                [d0, d0 + 86400] + ([('%' + pf + '%')] if pf else []) + [per_day]).fetchall()

        items = [{'n': n, 'p': p, 'd': par or os.path.dirname(p), 't': 'dir',

                  'e': '', 'm': mt, 's': None} for p, n, mt, par in sel]

        label = '今天' if day == today else ('昨天' if day == yday else str(day))

        out.append({'d': str(day), 'label': label, 'n': int(total),

                    'more': int(total) > len(items), 'items': items})

    return out





def timeline_rows(con, days=14, per_day=40, exts=None, pf=None, dirs_only=False):

    """首页时间轴：最近几天动过的文件，按天分组，新的在前。



    用户原话是「我的东西去哪了」。要回答这句话，先得让他在**不打字**的情况下

    看到「今天 / 昨天 / 前天」各自有什么，而不是先想文件名。



    exts：只要这几类扩展名（左侧栏点「文档/图片/应用」时会传）；

    pf  ：路径必须包含的片段（「路径包含」框）。

    两个都为空时就是原来的「全部」。

    """

    if dirs_only:

        rows = _dir_rows(con, days, per_day, pf)

        if rows:

            return rows

        return _dir_rows(con, days, per_day, pf, fallback=True)

    now = time.time()

    since = now - days * 86400

    # 先拿每天的真实条数：以前 n 是「截断后拿到几条」，于是表头写「今天 40」，

    # 而今天其实动过 300 个文件 —— 用户一眼就能看出在骗人。

    def _where(extra=''):

        """把「类型 / 路径包含」拼成 WHERE 尾巴，参数单独返回（不拼字符串值）。"""

        sql, arg = '', []

        if exts:

            sql += ' AND (' + ' OR '.join(['ext=?'] * len(exts)) + ')'

            arg.extend([e if e.startswith('.') else '.' + e for e in exts])

        if pf:

            sql += ' AND instr(lower(path), ?)>0'

            arg.append(pf.lower())

        return sql + extra, arg



    w_win, a_win = _where()

    try:

        cnt = con.execute(

            "SELECT strftime('%Y-%m-%d', mtime, 'unixepoch', 'localtime') d, count(*) "

            "FROM files WHERE mtime>=? AND mtime<=? AND name NOT LIKE '%~$'" + w_win +

            ' GROUP BY d ORDER BY d DESC LIMIT ?', [since, now + 3600] + a_win + [days]).fetchall()

    except sqlite3.Error:

        return []

    today = time.strftime('%Y-%m-%d')

    yday = time.strftime('%Y-%m-%d', time.localtime(now - 86400))

    out = []

    for day, total in cnt:

        if not day or not total:

            continue

        # 按天各取一次，而不是一条全局 LIMIT：今天有 500 个文件时，

        # 全局截断会把昨天、前天整段挤掉。

        d0 = time.mktime(time.strptime(str(day), '%Y-%m-%d'))

        try:

            w_day, a_day = _where()

            rows = con.execute(

                'SELECT path,name,ext,size,mtime,dir FROM files '

                "WHERE mtime>=? AND mtime<? AND name NOT LIKE '%~$'" + w_day +

                ' ORDER BY mtime DESC LIMIT ?', [d0, d0 + 86400] + a_day + [per_day]).fetchall()

        except sqlite3.Error:

            continue

        items = [{'n': name, 'p': path, 'd': d or os.path.dirname(path),

                  'e': (ext or '').lower(), 'm': mtime, 's': size}

                 for path, name, ext, size, mtime, d in rows if mtime]

        label = '今天' if day == today else ('昨天' if day == yday else str(day))

        out.append({'d': str(day), 'label': label, 'n': int(total),

                    'more': int(total) > len(items), 'items': items})

    if not out:

        # 新装的机器常常最近两周什么都没动过 —— 那就退一步，按修改时间

        # 倒序列出「最新的这些文件」，总比空着一屏让人以为没扫到强。

        w_fb, a_fb = _where()

        try:

            rows = con.execute(

                'SELECT path,name,ext,size,mtime,dir FROM files '

                "WHERE name NOT LIKE '%~$' AND mtime<=?" + w_fb +

                ' ORDER BY mtime DESC LIMIT ?',

                [now + 3600] + a_fb + [per_day * 3]).fetchall()

        except sqlite3.Error:

            return out

        byday = {}

        for path, name, ext, size, mtime, d in rows:

            if not mtime:

                continue

            day = time.strftime('%Y-%m-%d', time.localtime(mtime))

            byday.setdefault(day, []).append(

                {'n': name, 'p': path, 'd': d or os.path.dirname(path),

                 'e': (ext or '').lower(), 'm': mtime, 's': size})

        for day in sorted(byday, reverse=True):

            items = byday[day][:per_day]

            label = '今天' if day == today else ('昨天' if day == yday else str(day))

            out.append({'d': day, 'label': label, 'n': len(byday[day]),

                        'more': len(byday[day]) > len(items), 'items': items})

    return out





def same_name_rows(con, path, limit=12):

    """同名/相似文件（版本链）：把「简历.docx / 简历(1).docx / 简历-最终版.docx」

    归成一条线，用户一眼看到哪个是最新的、哪个更大。"""

    base = os.path.basename(path)

    stem = os.path.splitext(base)[0]

    key = _lineage_key(stem)

    if not key:

        return []

    try:

        rows = con.execute(

            'SELECT path,name,ext,size,mtime FROM files '

            'WHERE name>=? AND name<? ORDER BY mtime DESC LIMIT 400',

            (key[:1], chr(ord(key[:1]) + 1) if key else '\uffff')).fetchall()

    except sqlite3.Error:

        return []

    out = []

    for p, name, ext, size, mtime in rows:

        if os.path.normcase(p) == os.path.normcase(path):

            continue

        if _lineage_key(os.path.splitext(name or '')[0]) != key:

            continue

        out.append({'n': name, 'p': p, 'd': os.path.dirname(p), 'e': (ext or '').lower(),

                    'm': mtime, 's': size})

        if len(out) >= limit:

            break

    return out





_VER_RE = re.compile(r'(?:^|[\s\-_()（）\[\]])(?:v|ver|version|第)?\s*'

                     r'(?:副本|复制|copy|最终版?|定稿|改|修|新|旧|new|old|final|'

                     r'\d{1,4}|[一二三四五六七八九十]{1,3})(?:$|[\s\-_()（）\[\]])',

                     re.I)





def _lineage_key(stem):

    """把「简历(1)」「简历 - 副本」「简历-最终版」「简历 v2」折叠成同一个 key。"""

    s = (stem or '').strip()

    if not s:

        return ''

    prev = None

    while prev != s:

        prev = s

        s = re.sub(r'\s*[(（]\s*\d{1,3}\s*[)）]\s*$', '', s)

        s = re.sub(r'\s*[-_—]?\s*(副本|复制|copy|final|最终版?|定稿|new|old)\s*$', '', s, flags=re.I)

        s = re.sub(r'\s*[-_—]?\s*v(?:er)?\.?\s*\d{1,3}\s*$', '', s, flags=re.I)

        s = re.sub(r'\s*[-_—]\s*\d{1,3}\s*$', '', s)

    return s.strip().lower()





def _dir_range(d):

    """一个目录下所有文件的 path 区间：files 主键是 path，范围扫能直接用上索引。"""

    d = (d or '').rstrip('\\/')

    return d + '\\', d + '\\' + chr(0xFFFF)





def reverse_rows(con, path):

    """把用户拖进来的那个文件「反查」一遍：库里有几条、同名版本在哪、

    同目录还有谁、什么时候入的库。"""

    path = os.path.normpath(path)

    out = {'p': path, 'n': os.path.basename(path), 'in_index': False}

    try:

        row = con.execute('SELECT indexed_at,size,mtime FROM meta WHERE lower(path)=lower(?)',

                          (path,)).fetchone()

        if row:

            out['in_index'] = True

            out['idx'] = row[0]

            out['size'] = row[1]

            out['mtime'] = row[2]

        else:

            st = os.stat(path)

            out['size'] = st.st_size

            out['mtime'] = st.st_mtime

    except (sqlite3.Error, OSError):

        pass

    out['same'] = same_name_rows(con, path, limit=8)

    try:

        d = os.path.dirname(path)

        # 走 files 主键(path) 的范围扫描：只要这个目录下的行，不用给 dir 单建索引

        lo, hi = _dir_range(d)

        out['siblings'] = con.execute(

            'SELECT count(*) FROM files WHERE path>=? AND path<?', (lo, hi)).fetchone()[0]

    except sqlite3.Error:

        out['siblings'] = 0

    # 同样大小、同样扩展名的其他文件（重复文件的第一层筛，不用算哈希）

    try:

        ext = os.path.splitext(path)[1].lower()

        sz = out.get('size')

        d2 = os.path.dirname(path)

        if sz and ext:

            # 只在同目录里比：一样大小的同名文件几乎总是放在一起，跨盘找既慢又没意义

            lo, hi = _dir_range(d2)

            out['same_size'] = [{'n': n, 'p': p} for n, p in con.execute(

                'SELECT name,path FROM files WHERE path>=? AND path<? AND size=? AND ext=? '

                'AND lower(path)<>? LIMIT 8',

                (lo, hi, sz, ext, path.lower())).fetchall()]

        else:

            out['same_size'] = []

    except sqlite3.Error:

        out['same_size'] = []

    return out





def dym_items(con, q, limit=5):

    """零命中时的「你是不是要找」：中文词先转拼音，再找同名不同字的文件；

    字母词则找中文名、拼音对得上的那些。只在本机库内做，不联网。"""

    q = (q or '').strip()

    if not q or len(q) > 24:

        return []

    out, seen = [], set()

    try:

        py, ini = py_keys(q)

    except Exception:

        return []

    if any(ord(ch) > 127 for ch in q):

        # 威信 → weixin → 库里拼音以 weixin 开头的中文名（微信.lnk / 微信-2026…wav）。

        # 用前缀而不是等号：py 是拿「整个文件名含扩展名」算的，微信.lnk 的 py 是 weixinlnk。

        sql = ("SELECT name,py,ini FROM {t} WHERE (py LIKE ? OR ini LIKE ?) "

               "AND name NOT LIKE ? AND name GLOB '*[^ -~]*' "

               "ORDER BY length(name) LIMIT ?")

        args = (py + '%', ini + '%', '%' + q + '%', limit * 6)

    else:

        sql = ("SELECT name,py,ini FROM {t} WHERE (py=? OR ini=? OR py LIKE ?) "

               "AND name GLOB '*[^ -~]*' ORDER BY length(name) LIMIT ?")

        args = (q, q, q + '%', limit * 6)

    for tbl in ('files', 'dirs'):

        try:

            rows = con.execute(sql.format(t=tbl), args).fetchall()

        except sqlite3.Error:

            continue

        for name, _py, _ini in rows:

            # 同名的不同扩展名只留一条（微信.lnk / 微信.wav 建议一次就够）

            k = os.path.splitext((name or '').lower())[0]

            if not name or k in seen:

                continue

            seen.add(k)

            out.append({'n': name, 't': 'dir' if tbl == 'dirs' else 'file'})

            if len(out) >= limit:

                return out

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



    # 纯英文名不进拼音这一档：它们的 py/ini 就是名字本身，名字前缀匹配已经覆盖了，

    # 再走一遍拼音只会把「WXWork.exe」「wxid_xxx」这类噪音塞满前 9 格。

    # dirs/files 有 name 列可直接比；meta 只有 path，少这一层过滤也不影响大局。

    # 只认「名字里真有中文」的：dirs 的 py 对英文名也补过（py == 去掉分隔符的小写名），

    # 光靠 py<>name 滤不干净（weixinpay-feedback 这种照样漏进来），直接用 GLOB 判中文最稳。

    for tbl, isdir, safe in (('dirs', True, "name GLOB '*[^ -~]*'"),

                             ('meta', False, '1=1'),

                             ('files', False, "name GLOB '*[^ -~]*'")):

        # 按拼音/首字母长度排序：越短越可能是用户要的那个（wxlnk「微信.lnk」排在

        # wx20260319120512aac「微信-2026…aac」前面）。不加这一句，40 条名额会被

        # 一堆长名字录音占满，微信图标永远进不了候选集。

        take(tbl, isdir, f'SELECT path FROM {tbl} WHERE ini>=? AND ini<? AND {safe} '

                         f'ORDER BY length(ini) LIMIT ?', (q, hi, limit), 0)

        take(tbl, isdir, f'SELECT path FROM {tbl} WHERE py>=? AND py<? AND {safe} '

                         f'ORDER BY length(py) LIMIT ?', (q, hi, limit), 1)

        if deep:

            take(tbl, isdir, f'SELECT path FROM {tbl} WHERE instr(ini,?)>0 AND {safe} LIMIT ?',

                 (q, limit), 2)

            take(tbl, isdir, f'SELECT path FROM {tbl} WHERE instr(py,?)>0 AND {safe} LIMIT ?',

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

        ini = py_keys(name)[1]

        if ini == q:

            return -0.15

        # 首字母以关键词开头（wx → 微信.lnk / jl → 简历.docx）：这是中文用户最常见的

        # 用法，得排在「WXWork.exe」这类恰好同前缀的英文名前面，否则 9 个格子全被

        # 腾讯那堆 WX* 程序占满，搜「wx」永远看不到微信图标。0.55 比应用加权(0.75~0.75)更靠前，

        # 又比名字直接前缀(1.0)更靠后——名字本身像比拼音像更值得信。

        return 0.55

    return (2.4, 3.0, 3.2)[level - 1]





_PEEK_CACHE = {}





def peek_body(con, path, q='', span=360):

    """详情面板用：给某一份文档取一段正文。



    成本说明：docs 是 FTS5 虚表、path 是 UNINDEXED 列，`WHERE path=?` 会扫全表

    （5,700 篇 / 86MB，实测 ~100ms 冷、~20ms 热）。所以只在用户主动点「读一段正文」

    时调用，并且按 path 缓存 5 分钟——正常搜索根本不碰这条路径。

    """

    key = (path, (q or '').strip().lower())

    hit = _PEEK_CACHE.get(key)

    now = time.time()

    if hit and now - hit[0] < 300:

        return hit[1]

    tok = (q or '').strip()

    try:

        row = con.execute('SELECT body FROM docs WHERE path=? LIMIT 1', (path,)).fetchone()

    except sqlite3.Error:

        row = None

    if not row or not row[0]:

        return {'ok': False, 'why': 'no_body'}

    body = str(row[0])

    pos = -1

    if tok:

        pos = body.lower().find(tok.lower())

    if pos < 0:

        seg, offset = body[:span], 0

    else:

        start = max(0, pos - span // 3)

        seg, offset = body[start:start + span], start

    seg = re.sub(r'\s+', ' ', seg).strip()

    out = {'ok': True, 'text': _mark_text(seg, _fts_tokens(tok)) if tok else seg,

           'offset': offset, 'length': len(body), 'matched': pos >= 0}

    _PEEK_CACHE[key] = (now, out)

    return out





def _body_slots(n_name, first_app, limit=9):

    """正文命中该占几格。抽成纯函数，好单测（真实库跑不出来这些边界）。

    0 格只有一个理由：第一名是程序。搜「微信」想打开的是微信本体，
    不是提到微信的 20 篇文档 —— 这时候正文占位纯属添乱。

    其余情况至少 3 格。名字满屏时挤掉 3 条名字，因为「名字满屏」恰恰是
    最常见的情况，正文要是只在名字不够时才出现，等于这个功能从没生效。
    名字本来就少时多补几行，最多补满一屏（名字 0 条时就是 9 条正文）。"""

    if first_app:

        return 0

    return min(limit, max(3, limit - n_name))


def _body_hits(con, q, limit=3):

    """正文命中，排在名字命中之后垫底。片段里 char(1)/char(2) 是高亮标记，

    前端换成 <mark> —— 这两家长处都不在正文，这是第二大脑唯一守得住的差异。"""

    try:

        # 短词（中文 1~2 字，trigram 索引够不着）只能退化成 body LIKE 全表扫。

        # 给它一个时间预算：超时放弃正文命中，只留文件名命中（宁快勿卡）。

        # 单字最容易全表扫到底，预算压到 0.3s；两字实测 0.3~0.9s，给 1.5s 通常能扫完。

        nq = len((q or '').strip())

        budget = 0.3 if nq < 2 else (1.5 if nq < 3 else 3.0)

        deadline = time.perf_counter() + budget



        def _abort():

            return 1 if time.perf_counter() > deadline else 0



        try:

            con.set_progress_handler(_abort, 20000)

        except Exception:

            pass

        try:

            rows = search_rows(con, q, limit=limit, with_name=False)

        finally:

            try:

                con.set_progress_handler(None, 0)

            except Exception:

                pass

    except Exception:

        return []

    toks_for_marks = _fts_tokens(q)

    out = []

    for path, snip in rows:

        s = re.sub(r'\s+', ' ', str(snip or '')).strip()

        if SNIP_OPEN not in s:

            s = _mark_text(s, toks_for_marks)

        out.append({'n': os.path.basename(path) or path, 'p': path,

                    'd': os.path.dirname(path), 't': 'doc',

                    'e': os.path.splitext(path)[1].lower(), 'b': s[:110]})

    return out





_LAUNCHER_NC = None





def _is_launcher_path(p):

    """路径是否位于「启动器目录」（桌面/开始菜单）。结果缓存成 normcase 元组，

    每次排序比较只是几个 startswith，61 万文件的 top-N 排序无感。"""

    global _LAUNCHER_NC

    if _LAUNCHER_NC is None:

        _LAUNCHER_NC = tuple(os.path.normcase(d) for d in launcher_dirs())

    np = os.path.normcase(os.path.dirname(p.rstrip('\\/')))

    for d in _LAUNCHER_NC:

        if np == d or np.startswith(d + os.sep):

            return True

    return False





def _stat_dirs(items, cap=30):

    """补目录的修改时间。只碰本机盘（跳过 UNC 和手机 MTP 路径），失败就算了。"""

    n = 0

    for it in items:

        if n >= cap:

            break

        if it.get('t') != 'dir' or it.get('m'):

            continue

        p = it.get('p') or ''

        if p.startswith('\\\\') or '\\CrossDevice\\' in p:

            continue

        n += 1

        try:

            st = os.stat(p)

            it['m'] = st.st_mtime

        except OSError:

            pass





def _disp_name(name, ext, descr=None):

    """列表里给人看的名字（Listary 同款）：快捷方式/程序显示中文名、不带 .lnk/.exe 后缀。

    搜「微信」第一行就该是「微信」，而不是让用户去辨认「微信.lnk」是什么。

    优先级：文件名里的中文（微信 / 卸载微信）> 文件描述（可能是 Weixin 这种英文）> 去后缀名。"""

    e = (ext or '').lower()

    if e not in ('.lnk', '.appref-ms', '.exe'):

        return name

    stem = os.path.splitext(name)[0]

    for cand in (stem, (descr or '').strip()):

        if cand and any('\u4e00' <= ch <= '\u9fff' for ch in cand):

            return cand

    return (descr or '').strip() or stem





_APP_SELF = {}





def app_self_rows(con, q, limit=3):

    """查询命中「常用应用」白名单时，把这个应用的 exe/lnk 直接捞出来。



    为什么单独查一次：中文名（微信）和 exe 名（WeChat.exe）对不上 ——

    搜「微信」时名字索引只找得到「微信.lnk / 微信文件夹」，

    真正的程序在 Program Files 里叫 WeChat.exe，靠名字永远匹配不到。

    """

    apps = apprank.match(q or '')

    if not apps:

        return []

    ck = (q, limit)

    hit = _APP_SELF.get(ck)

    if hit and time.time() - hit[0] < 120:

        return [dict(x) for x in hit[1]]

    names = []

    for app, tier in apps[:2]:

        if tier > 2:

            continue

        names.extend(sorted(app.exes))

    if not names:

        return []

    # 必须写成 name IN (...) 走 idx_files_name：写成 lower(name) IN (...) 会退化成

    # 60 万行的全表扫（实测把浅搜从 2ms 拖到 55ms）。所以这里补几种常见大小写写法。

    variants = set()

    for n in names:

        base = n[:-4] if n.endswith('.exe') else n

        variants.update({n, base + '.exe', base.capitalize() + '.exe',

                         base.upper() + '.exe', base + '.EXE'})

    want = {v.lower() for v in variants}

    marks = ','.join('?' * len(variants))

    try:

        rows = con.execute(

            'SELECT path, name, ext, mtime, size FROM files WHERE name IN (%s) LIMIT ?' % marks,

            sorted(variants) + [limit * 4]).fetchall()

    except sqlite3.Error:

        return []

    rows = [r for r in rows if (r[1] or '').lower() in want][:limit]

    out = []

    for path, name, ext, mtime, size in rows:

        out.append({'n': name, 'p': path, 'd': os.path.dirname(path), 't': 'file',

                    'e': (ext or '').lower(), 'm': mtime, 's': size, 'r': 0, 'ab': 100,

                    'app': 1, 'dn': apprank.name_for_exe(name) or name})

    _APP_SELF[ck] = (time.time(), [dict(x) for x in out])

    if len(_APP_SELF) > 200:

        _APP_SELF.clear()

    return out





APPS_PREF_FILE = 'apps.json'

_APPS_CACHE = {'ts': 0.0, 'd': None}





def app_prefs():

    """用户对应用的个人意见：{"pinned": [...], "hidden": [...]}。



    为什么不用数据库：这是「人的决定」，几条到几十条，手改也方便；

    库是索引的派生数据，重建索引不该把用户的偏好一起冲掉。

    """

    now = time.time()

    if _APPS_CACHE['d'] is not None and now - _APPS_CACHE['ts'] < 5:

        return _APPS_CACHE['d']

    d = {'pinned': [], 'hidden': []}

    try:

        p = app_dir() / APPS_PREF_FILE

        if p.exists():

            raw = json.loads(p.read_text(encoding='utf-8'))

            for k in ('pinned', 'hidden'):

                v = raw.get(k)

                if isinstance(v, list):

                    d[k] = [str(x) for x in v if str(x).strip()]

    except Exception:

        pass

    _APPS_CACHE.update(ts=now, d=d)

    return d





def set_app_pref(path, mode):

    """mode: pin / unpin / hide / unhide。返回新的偏好。"""

    path = str(path or '')

    if not path:

        return app_prefs()

    d = app_prefs()

    key = os.path.normcase(path)

    for k in ('pinned', 'hidden'):

        d[k] = [x for x in d[k] if os.path.normcase(x) != key]

    if mode == 'pin':

        d['pinned'].insert(0, path)

    elif mode == 'hide':

        d['hidden'].append(path)

    try:

        (app_dir() / APPS_PREF_FILE).write_text(

            json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')

    except Exception:

        pass

    _APPS_CACHE.update(ts=time.time(), d=d)

    return d





def _target_key(path):

    """同一个程序的多个入口要合成一条：桌面「微信.lnk」与 Program Files 的 Weixin.exe

    指向的是同一个东西，用户在结果里看到三行只会犹豫。



    结果按路径缓存：`mini_search` 一次调用里这批路径会被问两遍（隐藏过滤 + 合并去重），

    而解析 .lnk 要走 COM，重复做既慢又增加踩线程问题的机会。"""

    p = str(path or '')

    ck = os.path.normcase(p)

    hit = _TGT_CACHE.get(ck)

    if hit is not None:

        return hit

    key = ck

    if ck.endswith('.lnk'):

        t = _lnk_target(p)

        if t:

            key = os.path.normcase(os.path.realpath(t))

    if len(_TGT_CACHE) > 20000:      # 全盘几十万文件，别让缓存无限长

        _TGT_CACHE.clear()

    _TGT_CACHE[ck] = key

    return key





def _place_boost(path):

    """位置本身就是人的选择：桌面上放着的图标，是他自己摆的。



    Listary 那种「最近打开」其实也是这个思路，只是它更信系统记录；

    这里先用最直白的信号：桌面 > 开始菜单 > 其它。

    """

    low = str(path or '').lower()

    if '\\desktop\\' in low:

        return 0.34

    if '\\start menu\\programs\\' in low:

        return 0.20

    if '\\start menu\\' in low:

        return 0.18

    return 0.0





def merge_same_target(items, limit=None):

    """把指向同一个程序的多个入口合并成一条，保留分最高的那条，记下 alts 数量。



    桌面的「微信.lnk」与 Program Files 的 Weixin.exe 会被当成同一目标；

    用户看到的是「微信」这一条，点它还是能打开；右键里能看到其它入口。

    """

    out, idx = [], {}

    for it in items:

        if it.get('t') == 'dir' or str(it.get('e') or '').lower() not in (

                '.exe', '.lnk', '.bat', '.cmd', '.msi', '.appref-ms', '.url'):

            out.append(it)

            continue

        k = _target_key(it.get('p'))

        j = idx.get(k)

        if j is None:

            idx[k] = len(out)

            it['alts'] = 0

            out.append(it)

        else:

            keep = out[j]

            keep['alts'] = int(keep.get('alts') or 0) + 1

            # 合并后保留「更像用户自己摆的那个」：桌面快捷方式 > exe

            if _place_boost(it.get('p')) > _place_boost(keep.get('p')):

                it['alts'] = keep['alts']

                out[j] = it

    return out[:limit] if limit else out





def mini_search(con, q, limit=9, deep=False, body=False, content_only=False):

    """返回 {'items': [...], 'deep': bool, 'ms': int}。



    deep=False：中文前缀 + 拼音前缀，全部走索引，毫秒级；

    deep=True ：再加「名字任意位置包含」（全表扫）和拼音模糊 —— 用户停手后才发；

    body=True ：正文命中给固定配额，排在名字命中后面（见下方 body 分支的说明）；

    content_only=True：只搜正文（用户敲了 c: / content: 前缀），名字一个都不要。"""

    q = (q or '').strip().lower()

    if not q:

        return {'items': [], 'deep': deep, 'ms': 0, 'n': 0}

    if not any(ch.isalnum() for ch in q):

        # 只敲了「.」「%」「 」这类符号：结果必然是几百个带扩展名的文件，没有信息量，

        # 而且每敲一下就多跑一次全表扫。直接回空。

        return {'items': [], 'deep': deep, 'ms': 0, 'n': 0}

    ck = (q, bool(body), bool(content_only))

    if deep:

        with MINI_LOCK:

            got = MINI_CACHE.get(ck)

        if got and time.time() - got[0] < MINI_TTL:

            return {'items': [dict(x) for x in got[1][:limit]], 'deep': True, 'ms': 0,

                    'n': len(got[1]), 'cache': True}

    t0 = time.perf_counter()

    # 只搜正文：用户敲了 c:xxx / content:xxx。名字、目录、拼音全跳过 ——
    # 主窗口早就支持这个语法，快搜窗一直没有，等于同一个软件两套行为。
    if content_only:

        hits = []

        for it in _body_hits(con, q, limit):

            hits.append(it)

        ms = int((time.perf_counter() - t0) * 1000)

        if hits:

            with MINI_LOCK:

                MINI_CACHE[ck] = (time.time(), [dict(x) for x in hits])

        return {'items': hits, 'deep': deep, 'ms': ms,

                'n': len(hits), 'content': True}

    items, seen, _rk = [], set(), {}



    def add(path, isdir, ext='', rank=None, descr=None, mtime=None, size=None):

        # 卸载快捷方式不是「用户想打开的东西」：搜「微信」时它排在应用后面很碍眼

        # （Listary 也不列）。只在名字确实以「卸载/uninstall」开头时丢掉。

        _bn = os.path.basename(path.rstrip('/' + chr(92))).lower()

        if ext in ('.lnk', '.url') and (_bn.startswith('卸载') or 'uninstall' in _bn):

            return

        key = os.path.normcase(path)

        if key in seen:

            # 同一路径可能先被别的档捞进来（「微信.lnk」先作为 .lnk 应用被应用档捞到，

            # 位次差 3），后又被拼音档认出来（位次最好 0）——留更好的那个，

            # 否则搜 wx 永远看不见微信图标。

            it0 = _rk.get(key)

            if it0 is not None:

                o0 = it0.get('r')

                o = 3 if o0 is None else (-1 if o0 < 0 else o0)

                n0 = rank

                nw = 3 if n0 is None else (-1 if n0 < 0 else n0)

                if nw < o:

                    it0['r'] = n0

            return

        seen.add(key)

        name = os.path.basename(path.rstrip('\\/')) or path

        items.append({'n': name, 'p': path,

                      'd': os.path.dirname(path.rstrip('\\/')),

                      't': 'dir' if isdir else 'file',

                      'e': '' if isdir else ext,

                      'app': 1 if (not isdir and apprank.is_app_file(name)) else 0,

                      'dn': (None if isdir else apprank.name_for_exe(name)) or _disp_name(name, ext, descr),

                      'm': mtime, 'z': size,

                      'r': _mini_rank(name.lower(), q) if rank is None else rank})

        _rk[key] = items[-1]



    frows = drows = []

    # 两张表分开兜错：老库还没有 dirs 表（v6.3 之前建的索引），

    # 一个 OperationalError 不该把 files 的结果也一起吞掉。

    # 应用中文名匹配：搜「微信」能找到 wechat.exe —— VerInfo FileDescription 字段。

    try:

        if deep:

            # 深搜：名字任意位置 + descr 任意位置，两路 OR

            # 两阶段：先只用 rowid 扫名字索引（18MB，实测 40~47ms），再按 rowid 回表。

            # 老写法 `SELECT path,… WHERE instr(name,?)` 会读整张 216MB 的表（160~203ms）。

            _ids = [r[0] for r in con.execute(

                'SELECT rowid FROM files WHERE instr(name,?)>0 LIMIT ?',

                (q, limit * 45)).fetchall()]

            if _ids:

                frows = con.execute(

                    'SELECT path, ext, descr, mtime, size FROM files WHERE rowid IN ('

                    + ','.join('?' * len(_ids)) + ')', _ids).fetchall()

            else:

                frows = []

            # 描述走 appdesc 小表：files 上 `instr(descr,?)` 是全表扫（607,731 行、174ms），

            # 而真正有描述的只有 3,749 行。分开查再拼，结果一样，深搜快 170ms。

            try:

                frows = list(frows) + list(con.execute(

                    'SELECT path, ext, descr, mtime, size FROM appdesc '

                    'WHERE instr(lower(descr),?)>0 LIMIT ?', (q, limit * 10)).fetchall())

            except sqlite3.Error:

                pass

        else:

            hi = q + '\uffff'          # BINARY 序：q 之后、q+任何常规字符之前

            # 缩写命中：dsh → DeepSeek Harness（Listary 的招牌功能之一，只查程序/快捷方式）

            for _p, _e in acr_hits(q, limit=limit):

                add(_p, False, _e, 1)

            frows = con.execute('SELECT path, ext, descr, mtime, size FROM files WHERE name>=? AND name<? LIMIT ?',

                                (q, hi, limit * 8)).fetchall()

            # 前缀命中会被同名文件淹掉：搜「微信」全盘有 289 个「微信-2026*.aac」通话录音，

            # 而 SQLite 按字节序排，'-'(0x2D) < '.'(0x2E) → 录音全排在 微信.lnk 前面，

            # 72 个名额被吃光，应用压根进不了候选集（后面排得再好也没用）。

            # 所以应用类（exe/lnk/bat…）在这一步单独再捞一次，优先合入。

            try:

                arows = con.execute(

                    'SELECT path, ext, descr, mtime, size FROM files WHERE name>=? AND name<? AND ext IN '

                    "('.exe','.lnk','.bat','.cmd','.ps1','.appref-ms') LIMIT ?",

                    (q, hi, limit * 5)).fetchall()

                frows = list(arows) + list(frows)

            except sqlite3.Error:

                pass

            # 浅搜也补一条 descr 命中 —— 这是 exe 的中文名，优先级很高

            if q and not _is_letters(q):

                try:

                    extra = con.execute(

                        'SELECT path, ext, descr, mtime, size FROM appdesc '

                        'WHERE instr(lower(descr),?)>0 LIMIT ?', (q, 30)).fetchall()

                    frows = list(frows) + list(extra)

                except sqlite3.Error:

                    pass

    except sqlite3.Error:

        frows = []

    try:

        if deep:

            drows = con.execute('SELECT path, name FROM dirs WHERE instr(name,?)>0 LIMIT ?',

                                (q, limit * 30)).fetchall()

        else:

            hi = q + '\uffff'

            drows = con.execute('SELECT path, name FROM dirs WHERE name>=? AND name<? LIMIT ?',

                                (q, hi, limit * 5)).fetchall()

    except sqlite3.Error:

        drows = []

    for path, ext, dsc, mt, sz in frows:

        add(path, False, ext or '', descr=dsc, mtime=mt, size=sz)

    for path, dname in drows:

        add(path, True, '', descr=dname)

    # 拼音：azb → 安装包。和中文命中并存，同一个路径只留更准的那个位次。

    for _k, (path, level, isdir) in _py_hits(con, q, deep).items():

        _nm = os.path.basename(path.rstrip(chr(92) + '/')) or path

        _r = _py_rank(level, _nm, q)

        # 中文名靠拼音命中时往前挪一档：中文用户敲 wx / weixin / jl，想找的是「微信」

        # 「简历」，而不是恰好同前缀的 WXWork.exe、wx_camera_1628...jpg。只在名字真含中文时生效。

        if _r > 0 and any(ord(ch) > 127 for ch in _nm):

            # 以前所有「靠拼音命中的中文名」统一 -0.30，等于同分：打 wx / xx 时

            # 「休闲区」和「学习典型安全心得体会」谁赢全看最近打开，用户觉得「认不出我要的」。

            # 现在按「拼音序列比关键词长出多少」分档：越短的名字越可能是整名匹配。

            try:

                _ini = py_keys(_nm)[1] or ''

            except Exception:

                _ini = ''

            if level <= 1:

                if _ini == q:

                    _r = -0.60

                else:

                    # -0.55 起步，名字越长扣回越多（最多回到 -0.30）：

                    # 「微信.lnk」(wxlnk) 该在「无限…旅行.rar」前面。

                    _r = -0.55 + min(0.25, 0.02 * max(0, len(_ini) - len(q)))

            else:

                _r = _r - 0.6

        add(path, isdir, os.path.splitext(path)[1].lower(), _r)

    # 目录在库里没存 mtime（dirs 表只有 path/name/parent），表格里的「修改时间」就空着。

    # 只对这次真返回的少量目录 stat，且只 stat 本机盘 —— 网络盘/手机存储一次 stat 能卡好几秒。

    _stat_dirs(items)

    if not items and not _has_files(con):

        # 还没建过全盘索引时，至少让已入库的文档文件名可用

        for p in name_rows(con, q, limit * 3):

            add(p, False, os.path.splitext(p)[1].lower())

    # 排序用一个总分，而不是先按「是不是应用」切档：

    # 切档的老写法会让 9 个搜索位被「简历.exe」这类同名程序占满，而用户要的往往是

    # 简历.docx / 简历.pdf —— 应用只是**加权**，不是**通行证**。

    #   名字像不像（前缀 1.0 / 词边界 0.7 / 中间 0.45 / 不像 0.2）

    # + 应用加权（桌面/开始菜单的直达 -  0.45，其他应用 0.25）

    # + 你平时开不开它（最多 0.30，压不过「名字像」的差距）

    # - 名字太长扣一点（「2023-01-25 备份-最终版-真的最终版.docx」不该排第一）

    rec = _recent_map(con)

    APP_EXTS = {'.exe', '.lnk', '.bat', '.cmd', '.ps1', '.appref-ms'}

    _ab = bool(apprank.match(q))

    _BASE = {0: 1.0, 1: 0.7, 2: 0.45, 3: 0.2}



    def _rel(it):

        # r 一般是 0~3（名字像不像）。拼音档用负数表示「比名字前缀更该排前」，

        # 这里换成 1.0 - r：-0.05 → 1.05，刚好压过名字前缀的 1.0，又不至于把

        # 真正一字不差的命中挤下去。

        _r0 = it.get('r', 3)

        if isinstance(_r0, (int, float)) and _r0 < 0:

            s = 1.0 - float(_r0)

        else:

            s = _BASE.get(_r0, 0.1)

        if it.get('e', '').lower() in APP_EXTS:

            s += 0.45 if _is_launcher_path(it.get('p', '')) else 0.25

            # 位置就是人的选择：桌面上的图标是他自己摆的，比 Program Files 里那份更该靠前

            s += _place_boost(it.get('p'))

        # 常用应用加成（清单在 apprank.py；上面已经算好 it['ab']）

        if it.get('ab'):

            s += it['ab'] / 100.0

        s += 0.30 * rec.get(os.path.normcase(it['p']), 0.0)

        if len(it.get('n', '')) > 24:

            s -= 0.10

        return -s

    # 应用本体直接插到候选里（中文名与 exe 名对不上的那种，靠名字永远匹配不到）

    if _ab:

        have = {os.path.normcase(it['p']) for it in items}

        for it in app_self_rows(con, q):

            k = os.path.normcase(it['p'])

            if k not in have:

                have.add(k)

                items.append(it)

                seen.add(k)

    # 用户明确表态的两类：隐藏的踢掉，固定的插到最前（ab 给 1000，压过所有算法分）

    _pref = app_prefs()

    if _pref['hidden']:

        _hid = {os.path.normcase(x) for x in _pref['hidden']}

        items = [it for it in items if os.path.normcase(it.get('p', '')) not in _hid

                 and _target_key(it.get('p')) not in _hid]

    if _pref['pinned']:

        _pin = {os.path.normcase(x) for x in _pref['pinned']}

        for it in items:

            if os.path.normcase(it.get('p', '')) in _pin:

                it['ab'] = 1000

                it['pin'] = 1

    # 同一程序的多个入口合成一条（桌面快捷方式优先），否则一屏里三行「微信」

    items = merge_same_target(items)



    # 再按「显示名」收一次：同一程序可能以 exe 与 .lnk 两种形态分别命中（target 大小写/解析

    # 不一致时 merge_same_target 合不掉），用户看到的就是两行没路径的「腾讯电脑管家」。

    # 规则：同名只留 ab/位置分最高的那条；它的 alts 累加其余入口数；同名的文件夹行直接丢掉。

    _appish = [it for it in items if it.get('app')]

    if _appish:

        _best = {}

        for it in _appish:

            k = (it.get('dn') or it.get('n') or '').strip().lower()

            if not k:

                continue

            cur = _best.get(k)

            if cur is None:

                _best[k] = it

                continue

            def _score(x):

                pp = os.path.normcase(x.get('p', ''))

                desk = 1 if (os.sep + 'desktop' + os.sep) in pp else 0

                return (x.get('ab') or 0, desk, len(pp))

            if _score(it) > _score(cur):

                it['alts'] = int(it.get('alts') or 0) + int(cur.get('alts') or 0)

                _best[k] = it

            else:

                cur['alts'] = int(cur.get('alts') or 0) + int(it.get('alts') or 0)

        _keep = {id(v) for v in _best.values()}

        _names = set(_best.keys())

        # 开始菜单里那些「应用同名文件夹」也别留：用户点开它只是又跳一层目录，

        # 而真正的启动入口（桌面/开始菜单快捷方式）已经在上面那条里了。

        items = [it for it in items

                 if id(it) in _keep

                 or not ((it.get('app')) or (it.get('t') == 'dir' and (it.get('n') or '').strip().lower() in _names))]

        # 同名文件夹（同一个开始菜单目录在 AppData 和 ProgramData 各有一份）也只留一条

        _seen_dir = set()

        _out = []

        for it in items:

            if it.get('t') == 'dir':

                kk = (it.get('n') or '').strip().lower()

                if kk in _seen_dir:

                    continue

                _seen_dir.add(kk)

            _out.append(it)

        items = _out

    # 先把「常用应用」标记打上，再排序 —— 不能放进排序键里懒算：

    # 排序键是按元素依次求值的，轮到第一个元素时它自己的 ab 还没赋上。

    if _ab:

        for it in items:

            b = apprank.boost(q, it.get('n', ''), it.get('p', ''))

            if b:

                it['ab'] = int(round(b * 100))

    # 常用应用永远先出：搜「微信」时微信本体在最前，其余按原规则排。

    # 只在查询命中白名单时才有 ab（0~100），普通查询所有行都是 0，顺序不受影响。

    items.sort(key=lambda it: (-(it.get('ab') or 0), _rel(it),

                               -(it.get('m') or 0), len(it['n']), it['n'].lower()))

    # 同分时先看谁最近改过：打 pmjt 想找的是刚截的那张图，而不是去年的同名截图。

    # 搜索结果实时验活：和 Listary / Everything 一致 —— 结果一定可点。

    # 不会扫全表（8626 条全扫约 1 秒太慢），只对 top N 做 os.path.exists 校验，

    # 失效的踢掉，让后面的候选补上。后台把失效路径登记给 prune，最终从索引清掉。

    N_CHECK = max(80, limit * 8)

    checked, gone, name_cnt = [], [], {}

    for it in items[:N_CHECK]:

        p = it.get('p', '')

        if p and os.path.exists(p):

            # 同名折叠：同一个名字最多两条（不同文件夹里的两份「简历.pdf」可以留，

            # 但 9 格里有 6 个「简历.exe」对用户毫无用处，把后面的位置让给别的名字）

            k = it['n'].lower()

            if name_cnt.get(k, 0) >= 2:

                continue

            name_cnt[k] = name_cnt.get(k, 0) + 1

            checked.append(it)

            if len(checked) >= limit + 3:

                break

        else:

            gone.append(p)

    out = checked[:limit]

    if gone:

        for p in gone:

            _mark_gone(p)

    if body:

        # 正文配额不该由「名字够不够一屏」决定。

        # 老逻辑是 room = limit - len(out)：名字命中满 9 条时 room=0，于是搜「报告」

        # 这种常见词，正文命中**永远不会出现**——用户以为快搜窗根本不能搜正文

        # （2026-11-07 反馈）。而名字满一屏恰恰是最常见的情况，等于这个功能从没生效。

        # 现在给正文固定配额：名字让出 3 格；名字本来就少时会补更多。

        # 唯一例外是「用户在启动程序」：搜「微信」时第一名就是 微信.lnk（app + 前缀命中），
        # 想打开的是微信本体，不是提到微信的 20 篇文档。
        # 但只看 app 标记不够 —— 搜「会议」第一名是「腾讯会议.lnk」（app=1，中间命中 r=2），
        # 这时候用户要的多半是会议纪要，正文必须出来。所以要 app 且 r<=1（前缀/词边界）。
        _top = out[0] if out else None

        _tr = _top.get('r') if _top else None

        first_app = bool(_top and _top.get('app')

                         and isinstance(_tr, (int, float)) and _tr <= 1)

        slack = _body_slots(len(out), first_app, limit)

        if slack:

            bhits = []

            for it in _body_hits(con, q, slack + 3):

                _np = os.path.normcase(it['p'])

                if _np in seen:

                    # 同一个文件既名字命中又正文命中：名字那条排得更靠前，够了

                    continue

                if not os.path.exists(it['p']):

                    _mark_gone(it['p'])

                    continue

                seen.add(_np)

                bhits.append(it)

                if len(bhits) >= slack:

                    break

            if bhits:

                # 按实际拿到的条数挤名字，而不是按 slack 挤：正文只有 2 条时

                # 名字该补回到 7 条，否则底部会空出两格，用户以为结果没加载完。

                out = out[:max(0, limit - len(bhits))] + bhits

    # 只缓存「有结果」的查询：首启建库时表还很小，这时把空结果缓存下来，

    # 会让用户在接下来的一整段建库时间里反复搜到「没有找到」（实测：png 在 24 秒内一直是 0 条）。

    # 必须缓存 out 而不是 items：正文命中是后补进 out 的，items 里没有它们。

    # 早先缓存的是 items，于是同一个词**第一次**搜有 5 条（含 3 条正文），

    # 第二次只剩 2 条 —— 用户看到的是「正文结果凭空消失」，还以为自己记错了。

    if deep and out:

        with MINI_LOCK:

            if len(MINI_CACHE) > MINI_CACHE_MAX:

                for k in sorted(MINI_CACHE, key=lambda k: MINI_CACHE[k][0])[:100]:

                    MINI_CACHE.pop(k, None)

            MINI_CACHE[ck] = (time.time(), [dict(x) for x in out])

    return {'items': out, 'deep': deep,

            'ms': int((time.perf_counter() - t0) * 1000), 'n': len(items)}



_SU = {'k': 1024, 'm': 1024 ** 2, 'g': 1024 ** 3, 't': 1024 ** 4}



def _qsize(num, unit):

    try:

        v = float(num)

    except (TypeError, ValueError):

        return None

    return int(v * _SU.get((unit or '').lower(), 1))



def parse_qsyn(q):

    """把「WinShun 式」语法从搜索词里剥出来：ext:pdf / size:>10m / dm:7d / -排除 / 某个文件夹/。



    用户在搜索框里直接写就行，不用去侧栏点。剥完剩下的词才是真正的关键词；

    看不懂的片段原样留在关键词里——宁可搜不到，也别悄悄吞掉用户打的字。"""

    raw = str(q or '')

    syn = {'q': raw.strip(), 'ext': None, 'size': (None, None),

           'days': None, 'excl': [], 'folder': None, 'content': False}

    rest = []

    for tok in raw.split():

        low = tok.lower()

        # 不带冒号的写法也认：size>10m / size>=10m / size<1g / size10m、dm<30d。
        # 用户的第一反应就是写 `size>10m`（不是 `size:>10m`），而原来只认后者 ——
        # 认不出来的片段会原样当关键词，于是「微信 size>10m」一条都搜不到，
        # 用户只会得出「搜索坏了」的结论（2026-11-07 用户实测反馈）。
        # 判据收紧到「前缀后面紧跟运算符或数字」，免得把 sizechart、filesize、
        # dmg 这类正常词吞成语法。
        if low.startswith('size') and len(tok) > 4 and low[4] in '<>=0123456789.':

            tok = 'size:' + tok[4:]

            low = 'size:' + low[4:]

        elif low.startswith('dm') and len(tok) > 2 and low[2] in '<>=0123456789':

            tok = 'dm:' + tok[2:]

            low = 'dm:' + low[2:]

        if len(tok) > 1 and low[0] in '-－':

            syn['excl'].append(low[1:])

            continue

        if low == 'content:' or low == '内容:':

            syn['content'] = True

            continue

        # 也接受「content:微信」这种连写：拆成开关 + 关键词，用户少打一个空格也认

        if low.startswith('content:') and len(tok) > 8:

            syn['content'] = True

            rest.append(tok[8:])

            continue

        # 快搜窗里少打几个字很值钱：c:微信 / 内容:微信 也当正文开关。

        # 但必须放过「c:\users\...」这种盘符路径 —— 那是搜文件，不是搜正文。

        if low.startswith('内容:') and len(tok) > 3:

            syn['content'] = True

            rest.append(tok[3:])

            continue

        if (low.startswith('c:') and len(tok) > 2

                and tok[2] not in '\\/' and not tok.startswith('c:\\')):

            syn['content'] = True

            rest.append(tok[2:])

            continue

        if low.startswith('ext:') or low.startswith('type:'):

            v = low.split(':', 1)[1]

            exts = [e.strip().lstrip('.') for e in re.split(r'[,\s]+', v) if e.strip()]

            if exts:

                syn['ext'] = ','.join(exts)

                continue

        if low.startswith('size:'):

            m = re.fullmatch(r'size:(>=|<=|>|<)?([0-9.]+)([kmgt]?)b?(?:-([0-9.]+)([kmgt]?)b?)?', low)

            if m:

                op, n1, u1, n2, u2 = m.groups()

                a, b = _qsize(n1, u1), (_qsize(n2, u2) if n2 else None)

                if op in ('>', '>='):

                    syn['size'] = (a, None)

                elif op in ('<', '<='):

                    syn['size'] = (None, a)

                elif b is not None:

                    syn['size'] = (a, b)

                else:

                    syn['size'] = (a, None)

                continue

        if low.startswith('dm:'):

            m = re.fullmatch(r'dm:(>=|<=|>|<)?([0-9]+)([dmwy])', low)

            if m:

                _op, n, u = m.groups()

                n = int(n)

                syn['days'] = n * {'d': 1, 'w': 7, 'm': 30, 'y': 365}[u]

                continue

        if len(tok) > 1 and (tok[-1] == '/' or tok[-1] == chr(92)):

            syn['folder'] = syn['folder'] or tok[:-1]

            continue

        rest.append(tok)

    syn['q'] = ' '.join(rest).strip()

    return syn



def _sz_of(it):
    """取一条结果的字节大小。

    **结果字典里这个字段叫 z，不叫 s**（`files_rows()`、`mini_search()`、
    `/api/recent` 三处产出都是 `'m': mtime, 'z': size`）。而 `syn_ok()` 的形参叫
    size，调用方历史上写的是 `it.get('s')` —— 恒为 None，于是 syn_ok 里
    「信息不全就不拦」那条兜底把条件整个放行，`size:` 过滤从来没真正生效过。
    症状：`微信 size:>10m` 会把 958 字节的「微信.lnk」也列出来（2026-11-07 用户实测）。

    这里统一成一个取值口，新增结果来源时只要字段是 z 就自动生效。
    """
    v = it.get('s')
    if v is None:
        v = it.get('z')
    return v



def syn_ok(path, size, mtime, syn):

    """按语法里的 size:/dm:/-排除 过滤一条结果。信息不全就不拦（宁可多给一条）。"""

    lo, hi = syn.get('size') or (None, None)

    if (lo is not None or hi is not None) and size is not None:

        if lo is not None and size < lo:

            return False

        if hi is not None and size > hi:

            return False

    days = syn.get('days')

    if days and mtime:

        if mtime < time.time() - days * 86400.0:

            return False

    for x in (syn.get('excl') or []):

        if x and x in str(path or '').lower():

            return False

    return True



SORT_KEYS = ('rel', 'new', 'old', 'big', 'name')



def sort_items(items, sort='rel'):

    """排序只对已经拿到的一屏结果做，不重查数据库。"""

    s = str(sort or 'rel').lower()

    if s == 'new':

        items.sort(key=lambda it: -(it.get('m') or 0))

    elif s == 'old':

        items.sort(key=lambda it: (it.get('m') or 0))

    elif s == 'big':

        items.sort(key=lambda it: -(it.get('s') or 0))

    elif s == 'name':

        items.sort(key=lambda it: str(it.get('n') or os.path.basename(str(it.get('p') or ''))).lower())

    return items



def search_rows(con, q, limit=20, ext=None, path_filter=None, with_name=True):

    """正文检索 + 文件名补充检索。



    docs 里 path 是 UNINDEXED 列，FTS 根本看不到文件名，于是「文件名里有、正文里没有」

    的文档搜不出来（实际用起来这正是最常见的抱怨点）。正文命中不够时再扫一遍路径：

    万级路径的 LIKE 是毫秒级，不用为它加索引列——那会逼着所有人重建整个索引。"""

    sql, args = _search_sql(q, ext, path_filter, con=con)

    hits = con.execute(sql, (*args, limit)).fetchall()

    # LIKE 降级分支（两字中文、AI/C 这类短词）只返回 substr，没有高亮标记；

    # 这里统一补上，前端才能把「为什么命中」标出来。

    _toks0 = _fts_tokens(q)

    if _toks0 and any(SNIP_OPEN not in str(s or '') for _, s in hits):

        hits = [(p, s if SNIP_OPEN in str(s or '')

                 else _mark_text(re.sub(r'\s+', ' ', str(s or '')).strip(), _toks0))

                for p, s in hits]

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

class _IconDone(Exception):

    """内部哨兵：jumbo 图标已经拿到，别再走 32px 的兜底路径。"""





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





def _draw_icon_png(hicon, draw, out):

    """把 HICON 画到 draw×draw 的 32 位 DIB，再用 PIL 缩到 out×out 出 PNG。

    列表里 24~26px 的图标在 150% 屏上要 36~39 物理像素，32px 的原图放大就糊；

    256px 的 jumbo 图标缩下来边缘是干净的。"""

    try:

        import ctypes

        from ctypes import wintypes

        from PIL import Image



        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

        user32.GetDC.restype = ctypes.c_void_p

        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p

        gdi32.CreateDIBSection.restype = ctypes.c_void_p

        gdi32.SelectObject.restype = ctypes.c_void_p

        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]

        gdi32.DeleteObject.restype = wintypes.BOOL

        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

        user32.DestroyIcon.argtypes = [ctypes.c_void_p]



        class BITMAPINFOHEADER(ctypes.Structure):

            _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),

                        ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),

                        ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),

                        ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),

                        ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),

                        ('biClrImportant', ctypes.c_uint32)]



        class BITMAPINFO(ctypes.Structure):

            _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', ctypes.c_uint32 * 3)]



        bi = BITMAPINFO()

        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)

        bi.bmiHeader.biWidth = draw

        bi.bmiHeader.biHeight = -draw        # 负数 = 从上往下

        bi.bmiHeader.biPlanes = 1

        bi.bmiHeader.biBitCount = 32



        hdc = user32.GetDC(None)

        mdc = gdi32.CreateCompatibleDC(ctypes.c_void_p(hdc))

        bits = ctypes.c_void_p()

        hbmp = gdi32.CreateDIBSection(ctypes.c_void_p(mdc), ctypes.byref(bi), 0,

                                      ctypes.byref(bits), None, 0)

        if not hbmp or not bits:

            gdi32.DeleteDC(ctypes.c_void_p(mdc))

            user32.ReleaseDC(None, ctypes.c_void_p(hdc))

            return None

        old = gdi32.SelectObject(ctypes.c_void_p(mdc), ctypes.c_void_p(hbmp))

        user32.DrawIconEx(ctypes.c_void_p(mdc), 0, 0, ctypes.c_void_p(hicon),

                          draw, draw, 0, None, 3)     # DI_NORMAL

        gdi32.SelectObject(ctypes.c_void_p(mdc), ctypes.c_void_p(old))

        buf = ctypes.string_at(bits, draw * draw * 4)

        gdi32.DeleteObject(ctypes.c_void_p(hbmp))

        gdi32.DeleteDC(ctypes.c_void_p(mdc))

        user32.ReleaseDC(None, ctypes.c_void_p(hdc))

        if not buf:

            return None

        img = Image.frombuffer('RGBA', (draw, draw), buf, 'raw', 'BGRA', 0, 1)

        # 有些 exe 只有小尺寸图标（比如各种 Uninstall.exe），DrawIconEx 不会把它放大，

        # 画在 256 的画布上就缩在左上角一小块。量一下不透明区域，太小就放大居中。

        try:

            bb = img.split()[3].getbbox()

            if bb:

                cw, ch = bb[2] - bb[0], bb[3] - bb[1]

                if cw < draw * 0.75 or ch < draw * 0.75:

                    glyph = img.crop(bb)

                    k = min(draw / float(cw), draw / float(ch))

                    glyph = glyph.resize((max(1, int(round(cw * k))),

                                          max(1, int(round(ch * k)))), Image.LANCZOS)

                    canvas = Image.new('RGBA', (draw, draw), (0, 0, 0, 0))

                    canvas.paste(glyph, ((draw - glyph.width) // 2,

                                         (draw - glyph.height) // 2), glyph)

                    img = canvas

        except Exception:

            pass

        if out != draw:

            img = img.resize((out, out), Image.LANCZOS)

        o = io.BytesIO()

        img.save(o, 'PNG')

        return o.getvalue()

    except Exception:

        return None





def _jumbo_png(target, out=64, fake=False):

    """SHIL_JUMBO（256px）取一张图标再缩到 out。

    fake=True 时按扩展名问（USEFILEATTRIBUTES，不碰磁盘）。失败回 None。"""

    hicon = 0

    piml = None

    release = None

    try:

        import ctypes

        from ctypes import wintypes



        shell32 = ctypes.windll.shell32

        ole32 = ctypes.windll.ole32



        class SHFILEINFOW(ctypes.Structure):

            _fields_ = [('hIcon', wintypes.HANDLE), ('iIcon', ctypes.c_int),

                        ('dwAttributes', wintypes.DWORD),

                        ('szDisplayName', wintypes.WCHAR * 260),

                        ('szTypeName', wintypes.WCHAR * 80)]



        class GUID(ctypes.Structure):

            _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort),

                        ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]



        SHGFI_SYSICONINDEX, SHGFI_USEFILEATTRIBUTES = 0x4000, 0x10

        flags = SHGFI_SYSICONINDEX | (SHGFI_USEFILEATTRIBUTES if fake else 0)

        info = SHFILEINFOW()

        shell32.SHGetFileInfoW.restype = ctypes.c_size_t

        shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,

                                           ctypes.POINTER(SHFILEINFOW),

                                           wintypes.UINT, wintypes.UINT]

        if not shell32.SHGetFileInfoW(target, 0x80, ctypes.byref(info),

                                      ctypes.sizeof(info), flags):

            return None



        iid = GUID()

        ole32.CLSIDFromString.restype = ctypes.c_long

        if ole32.CLSIDFromString(ctypes.c_wchar_p('{46EB5926-582E-4017-9FDF-E8998DAA0950}'),

                                 ctypes.byref(iid)) != 0:

            return None

        vptr = ctypes.c_void_p()

        shell32.SHGetImageList.restype = ctypes.c_long

        shell32.SHGetImageList.argtypes = [ctypes.c_int, ctypes.POINTER(GUID),

                                           ctypes.POINTER(ctypes.c_void_p)]

        if shell32.SHGetImageList(0x4, ctypes.byref(iid), ctypes.byref(vptr)) != 0 or not vptr:

            return None

        piml = vptr

        vtbl = ctypes.cast(piml, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        GetIcon = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,

                                     ctypes.c_uint, ctypes.POINTER(wintypes.HANDLE))(vtbl[10])

        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])

        h = wintypes.HANDLE()

        if GetIcon(piml, info.iIcon, 0x1, ctypes.byref(h)) != 0 or not h.value:

            return None

        hicon = h.value

        return _draw_icon_png(hicon, 256, out)

    except Exception:

        return None

    finally:

        try:

            import ctypes

            if hicon:

                ctypes.windll.user32.DestroyIcon(ctypes.c_void_p(hicon))

            if release and piml:

                release(piml)

        except Exception:

            pass





_BADGE_COLOR = {

    'audio': (124, 58, 237), 'video': (8, 145, 178), 'zip': (161, 98, 7),

    'text': (100, 116, 139), 'img': (37, 99, 235), 'code': (37, 99, 235),

    'pdf': (224, 82, 82), 'other': (148, 163, 184),

}

_BADGE_FAMILY = {}

for _e in ('.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg', '.wma', '.ape', '.amr'):

    _BADGE_FAMILY[_e] = 'audio'

for _e in ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.ts', '.m4v', '.webm'):

    _BADGE_FAMILY[_e] = 'video'

for _e in ('.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso'):

    _BADGE_FAMILY[_e] = 'zip'

for _e in ('.txt', '.md', '.log', '.csv', '.ini', '.cfg', '.json', '.xml', '.yml', '.yaml'):

    _BADGE_FAMILY[_e] = 'text'

for _e in ('.py', '.js', '.ts', '.c', '.h', '.cpp', '.java', '.go', '.rs', '.sh', '.bat', '.ps1'):

    _BADGE_FAMILY[_e] = 'code'

for _e in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.psd', '.ai'):

    _BADGE_FAMILY[_e] = 'img'

_BADGE_FAMILY['.pdf'] = 'pdf'





def _ext_badge(ext, out=48):

    """画一块「彩色圆角方块 + 白色图形」的图标，给系统只会回白纸的那些格式用。



    图形比字母更小就能认出来：26px 的列表行里，三个字母挤成一团根本看不清，

    一个音符/播放键/箱子反而一眼分得清。先按 4 倍尺寸画再缩，边缘才不毛刺。"""

    try:

        from PIL import Image, ImageDraw, ImageFont



        e = (ext or '').lower()

        if e and not e.startswith('.'):

            e = '.' + e

        fam = _BADGE_FAMILY.get(e, 'other')

        color = _BADGE_COLOR.get(fam, _BADGE_COLOR['other'])

        k = 4

        n = out * k

        img = Image.new('RGBA', (n, n), (0, 0, 0, 0))

        d = ImageDraw.Draw(img)

        pad = int(n * 0.06)

        d.rounded_rectangle([pad, pad, n - pad, n - pad], radius=int(n * 0.22), fill=color + (255,))

        W = (255, 255, 255, 255)



        def line(x1, y1, x2, y2, w=0.06):

            d.line([x1 * n, y1 * n, x2 * n, y2 * n], fill=W, width=int(n * w), joint='curve')



        if fam == 'audio':

            d.ellipse([0.28 * n, 0.56 * n, 0.46 * n, 0.74 * n], fill=W)

            line(0.44, 0.64, 0.44, 0.32, 0.055)

            d.polygon([(0.44 * n, 0.32 * n), (0.68 * n, 0.38 * n),

                       (0.68 * n, 0.50 * n), (0.44 * n, 0.44 * n)], fill=W)

        elif fam == 'video':

            d.polygon([(0.38 * n, 0.29 * n), (0.73 * n, 0.50 * n),

                       (0.38 * n, 0.71 * n)], fill=W)

        elif fam == 'zip':

            d.rounded_rectangle([0.32 * n, 0.30 * n, 0.68 * n, 0.70 * n],

                                radius=int(n * 0.06), outline=W, width=int(n * 0.055))

            d.line([0.50 * n, 0.30 * n, 0.50 * n, 0.70 * n], fill=W, width=int(n * 0.05))

            for i in range(3):

                y = 0.36 + 0.12 * i

                x1 = 0.40 if i % 2 == 0 else 0.50

                d.line([x1 * n, y * n, (x1 + 0.10) * n, y * n], fill=W, width=int(n * 0.045))

        elif fam == 'code':

            line(0.44, 0.34, 0.30, 0.50)

            line(0.30, 0.50, 0.44, 0.66)

            line(0.56, 0.34, 0.70, 0.50)

            line(0.70, 0.50, 0.56, 0.66)

        elif fam == 'img':

            d.polygon([(0.28 * n, 0.70 * n), (0.47 * n, 0.42 * n), (0.63 * n, 0.70 * n)], fill=W)

            d.ellipse([0.56 * n, 0.30 * n, 0.68 * n, 0.42 * n], fill=W)

        elif fam == 'text':

            for x2, y in ((0.70, 0.34), (0.62, 0.48), (0.72, 0.62)):

                d.rounded_rectangle([0.28 * n, y * n, x2 * n, (y + 0.075) * n],

                                    radius=int(n * 0.037), fill=W)

        else:

            label = (e.lstrip('.')[:4].upper() or '?')

            size = int(n * (0.44 if len(label) <= 2 else 0.34 if len(label) == 3 else 0.28))

            font = None

            for name in ('segoeuib.ttf', 'arialbd.ttf', 'seguisb.ttf'):

                try:

                    font = ImageFont.truetype(name, size)

                    break

                except Exception:

                    font = None

            if font is None:

                font = ImageFont.load_default()

            box = d.textbbox((0, 0), label, font=font)

            tw, th = box[2] - box[0], box[3] - box[1]

            d.text(((n - tw) / 2 - box[0], (n - th) / 2 - box[1]), label, font=font, fill=W)



        img = img.resize((out, out), Image.LANCZOS)

        o = io.BytesIO()

        img.save(o, 'PNG')

        return o.getvalue()

    except Exception:

        return None





def _generic_icon_png():

    """系统对「谁都不认识的扩展名」回的那张白纸图标，用来判断某个扩展名有没有真图标。"""

    with ICON_LOCK:

        if 'generic' in ICON_CACHE:

            return ICON_CACHE.get('generic')

    png = _jumbo_png('.zzzzq', 48, fake=True)

    with ICON_LOCK:

        ICON_CACHE['generic'] = png

    return png





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

        # 先要 256px 的那张（缩到 48），拿不到再退回 32px 的 SHGFI_ICON

        if isdir:

            png = _jumbo_png(os.environ.get('WINDIR') or 'C:\\', 48)

        else:

            png = _jumbo_png(key or '.txt', 48, fake=True)

    except Exception:

        png = None

    try:

        import ctypes

        from ctypes import wintypes



        if png:

            raise _IconDone()



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

    except _IconDone:

        pass

    except Exception:

        png = None

    finally:

        if hicon:

            try:

                import ctypes

                ctypes.windll.user32.DestroyIcon(ctypes.c_void_p(hicon))

            except Exception:

                pass

    # 系统没给这个格式注册图标时，SHGetFileInfoW 回的是同一张白纸：

    # 换成自绘的彩色扩展名块，列表里一眼能分出音频/压缩包/文本

    if png and not isdir:

        try:

            if png == _generic_icon_png():

                png = _ext_badge(key) or png

        except Exception:

            pass

    with ICON_LOCK:

        # 只缓存成功路径；失败不缓存，让下次 HTTP 请求还能重试 ——

        # webview 进程首次启动时 shell32 可能晚一点就绪，第二次试就成

        if png:

            ICON_CACHE[key] = png

    return png





def path_icon(path):

    """按真实文件路径取图标 —— .lnk 由 shell 自动解析到目标应用，

    .exe 取自己内嵌的图标。和资源管理器里看到的完全一致。



    与 sys_icon 的差别：不用 USEFILEATTRIBUTES，要碰真实文件；

    只给应用类文件用（列表里一行一个），按路径缓存，代价可控。

    失败回 None，调用方回落到扩展名图标。"""

    if not path or not _local_only(path) or not os.path.exists(path):

        return None

    key = 'p:' + os.path.normcase(path)

    with ICON_LOCK:

        if key in ICON_CACHE:

            return ICON_CACHE[key]

    png = _jumbo_png(os.path.normpath(path), 64)

    if png:

        with ICON_LOCK:

            ICON_CACHE[key] = png

        return png

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

        SHGFI_ICON, SHGFI_LARGEICON = 0x100, 0x0

        info = SHFILEINFOW()

        ok = shell32.SHGetFileInfoW(os.path.normpath(path), 0, ctypes.byref(info),

                                    ctypes.sizeof(info), SHGFI_ICON | SHGFI_LARGEICON)

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

    # 注意：这里的 png 来自 SHGetFileInfoW 的 32px 图标（jumbo 那条已经提前 return），

    # 它可能是 shell 未热时的「通用未知文件」图，所以**不写缓存**，下次请求重算。

    return png





# 图片缩略图所需的扩展名 —— 不在此名单里的文件，预览面板退回系统图标

THUMB_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff'}

# 视频：不装 ffmpeg，直接用 Windows 自带的缩略图服务（资源管理器里能看到缩略图的格式它都能出）

VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.ts', '.webm', '.mpg', '.mpeg'}

VIDEO_THUMB_TIMEOUT = 8.0           # 秒；视频取帧超过这个时间就别要了

THUMB_LOCK = threading.Lock()

# 磁盘缓存：%LOCALAPPDATA%\SecondBrain\thumbs。库里 40k 视频 + 29 万图片，

# 光靠进程内 100 张的 THUMB_CACHE 每次重启都要重解码一遍，翻两页就明显卡。

THUMB_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),

                         'SecondBrain', 'thumbs')





def _thumb_disk_path(path, max_w, max_h):

    """路径 + 修改时间 + 大小 + 尺寸 → 缓存文件名。



    带上 mtime/size，文件换了内容自然就换 key，不需要额外失效逻辑。"""

    try:

        st = os.stat(path)

        stamp = '%d:%d' % (int(st.st_mtime), st.st_size)

    except OSError:

        stamp = 'na'

    raw = ('%s|%s|%dx%d' % (os.path.normcase(path), stamp, max_w, max_h)).encode('utf-8', 'replace')

    import hashlib

    return os.path.join(THUMB_DIR, hashlib.sha1(raw).hexdigest() + '.png')





THUMB_MAX_BYTES = 500 * 1024 * 1024   # 缓存总量上限；超了按最旧删

THUMB_MAX_AGE = 30 * 86400            # 30 天没被读过就删

THUMB_FAIL = {}                       # 路径|尺寸 → 失败时间：解不开的文件别每次重试



def _thumb_sweep():

    """清缩略图缓存：先删过期的，再超总量删最旧的。



    没有这一步，把全库图片翻一遍就是十几个 GB 落在系统盘，而且源文件删了、盘拔了，

    副本还留着（那是用户的私人照片和 PDF 首页，不该无条件长期留存）。"""

    try:

        files = []

        total = 0

        now = time.time()

        with os.scandir(THUMB_DIR) as it:

            for f in it:

                try:

                    if not f.is_file() or not f.name.endswith('.png'):

                        continue

                    st = f.stat()

                except OSError:

                    continue

                if now - st.st_atime > THUMB_MAX_AGE:

                    try:

                        os.unlink(f.path)

                    except OSError:

                        pass

                    continue

                files.append((st.st_mtime, st.st_size, f.path))

                total += st.st_size

        if total > THUMB_MAX_BYTES:

            files.sort()

            for _, size, fp in files:

                if total <= THUMB_MAX_BYTES:

                    break

                try:

                    os.unlink(fp)

                    total -= size

                except OSError:

                    pass

    except OSError:

        pass





def _thumb_from_disk(target):

    try:

        with open(target, 'rb') as f:

            data = f.read()

        # 并发写坏过的半截文件不能被当成命中（否则那张图永久空白）

        if len(data) > 64 and data[:8] == b'\x89PNG\r\n\x1a\n':

            return data

        try:

            os.unlink(target)

        except OSError:

            pass

        return None

    except OSError:

        return None





def _thumb_to_disk(target, png):

    try:

        os.makedirs(THUMB_DIR, exist_ok=True)

        # 缩略图在多个 HTTP 线程里并发生成，tmp 名只带 pid 会撞（同进程同 pid），

        # 两个线程写同一个文件 → 半截 PNG 被 os.replace 提升成正式缓存。

        tmp = target + '.%d_%d.tmp' % (os.getpid(), threading.get_ident())

        with open(tmp, 'wb') as f:

            f.write(png)

        os.replace(tmp, target)

    except OSError:

        pass

THUMB_CACHE = {}                    # (normcase(path)) → png bytes，最多 100 张





class _ThumbGUID(ctypes.Structure):

    _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort),

                ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]





class _ThumbSIZE(ctypes.Structure):

    _fields_ = [('cx', ctypes.c_long), ('cy', ctypes.c_long)]





class _ThumbBITMAP(ctypes.Structure):

    _fields_ = [('bmType', ctypes.c_long), ('bmWidth', ctypes.c_long), ('bmHeight', ctypes.c_long),

                ('bmWidthBytes', ctypes.c_long), ('bmPlanes', ctypes.c_ushort),

                ('bmBitsPixel', ctypes.c_ushort), ('bmBits', ctypes.c_void_p)]





class _ThumbBMIH(ctypes.Structure):

    _fields_ = [('biSize', ctypes.c_ulong), ('biWidth', ctypes.c_long), ('biHeight', ctypes.c_long),

                ('biPlanes', ctypes.c_ushort), ('biBitCount', ctypes.c_ushort),

                ('biCompression', ctypes.c_ulong), ('biSizeImage', ctypes.c_ulong),

                ('biXPelsPerMeter', ctypes.c_long), ('biYPelsPerMeter', ctypes.c_long),

                ('biClrUsed', ctypes.c_ulong), ('biClrImportant', ctypes.c_ulong)]





class _ThumbBMI(ctypes.Structure):

    _fields_ = [('bmiHeader', _ThumbBMIH), ('bmiColors', ctypes.c_ulong * 3)]





def _shell_thumb_png(path, size=256):

    """视频/其它靠系统缩略图服务出图（等价于资源管理器里的大缩略图）。



    走 SHCreateItemFromParsingName + IShellItemImageFactory::GetImage：

    解码由系统 codec 做，不需要 ffmpeg。**必须在本线程 CoInitialize**，

    所以调用方要么在 HTTP 线程里直接调，要么丢进线程池（本函数自带初始化）。

    失败（没装解码器 / 文件损坏）返回 None，前端退回系统图标。"""

    import io as _io

    import platform

    if platform.system() != 'Windows' or not _local_only(path):

        return None

    try:

        ole32 = ctypes.WinDLL('ole32')

        shell32 = ctypes.WinDLL('shell32')

        gdi32 = ctypes.WinDLL('gdi32')

        user32 = ctypes.WinDLL('user32')

    except Exception:

        return None

    iid = _ThumbGUID()

    try:

        ole32.CLSIDFromString(ctypes.c_wchar_p('{bcc18b79-ba16-442f-80c4-8a59c30c463b}'),

                              ctypes.byref(iid))

    except Exception:

        return None

    S_ok = 0

    ole32.CoInitialize(None)

    pxi = ctypes.c_void_p()

    hbm = ctypes.c_void_p()

    try:

        hr = shell32.SHCreateItemFromParsingName(ctypes.c_wchar_p(path), None,

                                                 ctypes.byref(iid), ctypes.byref(pxi))

        if hr != S_ok or not pxi:

            return None

        vt = ctypes.cast(pxi, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, _ThumbSIZE, ctypes.c_int,

                                   ctypes.POINTER(ctypes.c_void_p))

        hr = proto(vt[3])(pxi, _ThumbSIZE(size, size), 0x8 | 0x1, ctypes.byref(hbm))

        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(pxi)

        if hr != S_ok or not hbm:

            return None

        bm = _ThumbBITMAP()

        gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

        gdi32.GetObjectW(ctypes.c_void_p(hbm.value), ctypes.sizeof(_ThumbBITMAP), ctypes.byref(bm))

        w, h = bm.bmWidth, bm.bmHeight

        if w <= 0 or h <= 0:

            return None

        bi = _ThumbBMI()

        bi.bmiHeader.biSize = ctypes.sizeof(_ThumbBMIH)

        bi.bmiHeader.biWidth = w

        bi.bmiHeader.biHeight = -h

        bi.bmiHeader.biPlanes = 1

        bi.bmiHeader.biBitCount = 32

        buf = ctypes.create_string_buffer(w * h * 4)

        gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,

                                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]

        # 这里必须自己配对释放：GetDC(None) 每调一次占一个 DC，进程的 DC 配额一万左右，

        # 之前内联写在参数里、一次没放，翻几百个视频缩略图后 GetDC 就开始返回 NULL，

        # 连图标提取（同样走 GetDC）一起坏掉，症状是「用久了满屏没图标」。

        hdc = user32.GetDC(None)

        try:

            gdi32.GetDIBits(ctypes.c_void_p(hdc), ctypes.c_void_p(hbm.value),

                            0, h, buf, ctypes.byref(bi), 0)

        finally:

            if hdc:

                user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

                user32.ReleaseDC(None, ctypes.c_void_p(hdc))

        from PIL import Image, ImageChops

        im = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')

        # 系统会按 16:9 → 正方形做信箱填充（上下或左右留白），把白边裁掉

        bg = Image.new('RGB', im.size, im.getpixel((0, 0)))

        box = ImageChops.difference(im, bg).getbbox()

        if box and (box[2] - box[0]) > 16 and (box[3] - box[1]) > 16:

            im = im.crop(box)

        out = _io.BytesIO()

        im.save(out, format='PNG')

        return out.getvalue()

    except Exception:

        return None

    finally:

        try:

            if hbm:

                gdi32.DeleteObject.argtypes = [ctypes.c_void_p]

                gdi32.DeleteObject(ctypes.c_void_p(hbm.value))

        except Exception:

            pass

        try:

            ole32.CoUninitialize()

        except Exception:

            pass





def make_thumb(path, max_w=320, max_h=400):

    """图片/PDF → 缩略图 PNG 字节。失败返回 None，前端回落到系统图标。



    实现务实：

    - 图片：PIL 打开 → 等比缩放（最大 320×400）→ PNG 字节

    - PDF：pymupdf 取首页 → PIL 缩放

    - 视频等其它：交给前端用 /api/icon 显示系统大图标"""

    if not path or not _local_only(path) or not os.path.exists(path):

        return None

    try:

        st = os.stat(path)

        stamp = '%d:%d' % (int(st.st_mtime), st.st_size)

    except OSError:

        stamp = 'na'

    k = os.path.normcase(path) + '|%s|%dx%d' % (stamp, max_w, max_h)

    with THUMB_LOCK:

        if k in THUMB_CACHE:

            return THUMB_CACHE[k]

        fail_at = THUMB_FAIL.get(k)

        if fail_at and time.time() - fail_at < 300:

            return None

    disk = _thumb_disk_path(path, max_w, max_h)

    hit = _thumb_from_disk(disk)

    if hit:

        with THUMB_LOCK:

            if len(THUMB_CACHE) > 100:

                THUMB_CACHE.pop(next(iter(THUMB_CACHE)))

            THUMB_CACHE[k] = hit

        return hit

    ext = os.path.splitext(path)[1].lower()

    png = None

    try:

        if ext in THUMB_EXTS:

            from PIL import Image

            with Image.open(path) as im:

                im.thumbnail((max_w, max_h))

                if im.mode not in ('RGB', 'RGBA'):

                    im = im.convert('RGB')

                import io

                buf = io.BytesIO()

                im.save(buf, format='PNG')

                png = buf.getvalue()

        elif ext in VIDEO_EXTS:

            # 先在子线程里等，超时就放弃（解码器卡住时不能把 HTTP 线程拖死）

            box = {}



            def _run():

                try:

                    box['png'] = _shell_thumb_png(path)

                except Exception:

                    box['png'] = None



            th = threading.Thread(target=_run, daemon=True)

            th.start()

            th.join(VIDEO_THUMB_TIMEOUT)

            png = box.get('png')

        elif ext == '.pdf':

            import pymupdf

            import io

            from PIL import Image

            with pymupdf.open(path) as doc:

                if len(doc):

                    page = doc[0]

                    # 算缩放系数：原始页面宽 → max_w

                    zw = max_w / max(page.rect.width, 1)

                    # 窄长页（比如 320×320000 的整页截图型 PDF）按宽度缩放会把 pixmap

                    # 撑到几百 MB；再按「最长边 2200」压一次，和 ocr_pdf 同一个口径

                    zw = min(zw, 2200.0 / max(page.rect.width, page.rect.height, 1))

                    pix = page.get_pixmap(matrix=pymupdf.Matrix(zw, zw), alpha=False)

                    with Image.frombytes('RGB' if pix.n < 4 else 'RGBA',

                                         (pix.width, pix.height), pix.samples) as im:

                        im.thumbnail((max_w, max_h))

                        buf = io.BytesIO()

                        im.save(buf, format='PNG')

                        png = buf.getvalue()

    except Exception:

        png = None

    if png:

        with THUMB_LOCK:

            if len(THUMB_CACHE) > 100:

                THUMB_CACHE.pop(next(iter(THUMB_CACHE)))

            THUMB_CACHE[k] = png

            THUMB_FAIL.pop(k, None)

        _thumb_to_disk(disk, png)

    else:

        # 解不开就记一笔，5 分钟内别再试。视频尤其要紧：一个坏文件本来每次重渲染

        # 都要白等 8 秒超时，网格里几个坏文件就能把同端口的 6 个连接全占满。

        with THUMB_LOCK:

            if len(THUMB_FAIL) > 500:

                THUMB_FAIL.clear()

            THUMB_FAIL[k] = time.time()

    return png





_OPEN_DENY_EXT = ('.exe', '.com', '.scr', '.pif', '.bat', '.cmd', '.ps1', '.psm1', '.vbs', '.vbe',

                  '.js', '.jse', '.wsf', '.wsh', '.msi', '.msp', '.lnk', '.url', '.appref-ms',

                  '.reg', '.hta', '.msc', '.cpl', '.jar', '.gadget', '.crt',

                  # 2026-11-04 安全审查补：本机 .py 关联是 py.exe，漏了它等于黑名单白写

                  '.py', '.pyw', '.pyz', '.chm', '.scf', '.settingcontent-ms', '.application')





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



# 「更新」是一条链：扫清单 → 入库 → 清理。清理这一步是唯一会删数据的，

# 而它原先只判断「这个文件还在不在」，压根不知道「我索引的那个文件夹本身没了」

# 意味着地址变了、不是文件被删了。2026-11-04 整块工作目录换了一次位置，

# 就是靠这一点一次抹掉了 3967 条文档记录（重扫要一两个小时 + 重跑 OCR）。

# 下面三个常量/函数是给它补的护栏：

#   ① 索引范围里的目录不存在 → 它名下整体跳过（对应 prune 里的 ②）

#   ② 一次要清掉太多 → 先停下问人（对应 prune 里的 ③）

#   ③ 开扫之前先体检范围（对应 update 开头的预检）

PRUNE_BULK_LIMIT = 50          # 单次清理的绝对上限：正常删文件一周也就几十条

PRUNE_BULK_RATIO = 0.5

# 单个索引范围里的失踪率阈值（防止「范围根还在、子目录整块搬走」被静默清掉）

PRUNE_SCOPE_MIN = 30         # 相对上限：一次消失超过库内一半，不可能是人删的



def _scope_dirs(scope_path=None):

    """读索引范围，返回 [(原始写法, normcase 后的比较用写法)]。

    只给「范围体检 / prune 保护」用；读不到就返回空表，调用方退回盘根判断。"""

    try:

        sp = Path(scope_path) if scope_path else app_dir() / 'scope.json'

        data = json.loads(sp.read_text(encoding='utf-8'))

        out = []

        for s in data.get('include_dirs') or []:

            s = str(s).strip()

            if s:

                out.append((s, os.path.normcase(os.path.normpath(s))))

        return out

    except Exception:

        return []



def _missing_scope_dirs(scope_path=None):

    """范围里哪些文件夹已经不在了（原始写法，按首次出现去重、保持顺序）。"""

    seen, miss = set(), []

    for orig, norm in _scope_dirs(scope_path):

        if norm in seen:

            continue

        seen.add(norm)

        try:

            ok = os.path.isdir(orig)

        except OSError:

            ok = False

        if not ok:

            miss.append(orig)

    return miss





# ── 首次运行：没人点按钮，也把库建起来 ────────────────────────────────────

# 2026-11-05 用户提的：🚀 智能扫描藏在设置抽屉的「维护工具」里，新人打开一看

# 搜不到东西，很可能就卸载了。这里在「库里一个文件都没有」时自动走一遍：

# 扫盘 → 收下推荐的文件夹 → 建库（更新会顺带补文件名索引）。

# 只允许界面在页面加载时触发（POST /api/firstrun），绝不在 make_server() 里自己跑

# —— 不然每一个 test_* 的空库测试都会被它拖去扫全盘。

# 首启分两段跑：先把「桌面 / 文档 / 下载」的文件名建起来（几十秒就能搜），

# 再后台补全盘与正文。1.0 前专家给的最狠一条就是「等你建完库，用户已经卸载了」。

FIRST_RUN = {'state': 'idle', 'stage': '', 'msg': '', 'n': 0, 'files': 0,

             'docs': 0, 'eta': 0, 'total': 0, 'paused': False, 'cancel': False, 't0': 0.0}

FIRST_RUN_BUDGET = 12.0      # 第一段最多占这么久（用户承诺：30 秒内能搜；12 秒留足余量），剩下的留给后台

FIRST_RUN_MAX_FILES = 80000  # 第一段最多收这么多条，避免超大桌面拖住首屏

_KF_GUIDS = {

    'desktop': 'B4BFCC3A-DB2C-424C-B029-7FE99A87C641',

    'documents': 'FDD39AD0-238F-46AF-ADB4-6C85480369C7',

    'downloads': '374DE290-123F-4565-9164-39C4925E467B',

}





def _known_folder(name):

    """Windows 的「桌面/文档/下载」真实位置（可能被 OneDrive 重定向、可能不是英文名）。"""

    fid = _KF_GUIDS.get(name)

    if not fid:

        return None

    try:

        import ctypes

        from uuid import UUID

        raw = ctypes.create_string_buffer(UUID(fid).bytes_le, 16)

        out = ctypes.c_wchar_p()

        hr = ctypes.windll.shell32.SHGetKnownFolderPath(raw, 0, None, ctypes.byref(out))

        if hr != 0 or not out.value:

            return None

        p = out.value

        try:

            ctypes.windll.ole32.CoTaskMemFree(out)

        except Exception:

            pass

        return p if os.path.isdir(p) else None

    except Exception:

        return None





def _user_dirs():

    """第一段要抢的目录，**顺序很重要**：先扫几百个快捷方式，再扫桌面/文档/下载。



    为什么：用户装完第一件事是搜「微信 / 豆包 / 有道云笔记」，这些程序本体在

    Program Files 或 %LOCALAPPDATA%Programs 下，第一段扫不到；但开始菜单和公共桌面里

    有它们的 .lnk，加起来只有几百个文件，stat 一遍不到 1 秒。

    反过来如果把桌面/文档/下载放前面，一个装满素材的下载目录就能吃掉 12 秒预算，

    快捷方式排到后面根本轮不到 —— 用户看到的就是「明明装了却搜不到」。

    """

    out = []



    appdata = os.environ.get('APPDATA') or ''

    progdata = os.environ.get('PROGRAMDATA') or ''

    public = os.environ.get('PUBLIC') or ''

    quick = []

    if appdata:

        quick.append(os.path.join(appdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs'))

    if progdata:

        quick.append(os.path.join(progdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs'))

    if public:

        quick.append(os.path.join(public, 'Desktop'))

    for cand in quick:

        try:

            if cand and os.path.isdir(cand) and not _noise_dir(cand) and not _is_self_noise(cand):

                q = os.path.normpath(cand)

                if q not in out:

                    out.append(q)

        except OSError:

            continue



    for k in ('desktop', 'documents', 'downloads'):

        p = _known_folder(k)

        if p and os.path.isdir(p) and not _noise_dir(p) and not _is_self_noise(p):

            q = os.path.normpath(p)

            if q not in out:

                out.append(q)

    return out





def _quick_names(dirs, budget=None, max_files=None):

    """把 dirs 下的文件名直接写进 files/dirs（不读正文、不等扫描清单）。



    为什么要另开一条快速路：原来的首启是「全盘 os.walk → 生成清单 → 按范围过滤 → 抽正文」，

    本机实测 100 分钟。用户在第一分钟就搜不到东西，然后卸载。这里只 stat 一遍文件名，

    十万级文件大约十几秒，先让搜索能用。

    返回写进去的条数。"""

    budget = FIRST_RUN_BUDGET if budget is None else budget

    max_files = FIRST_RUN_MAX_FILES if max_files is None else max_files

    t0 = time.time()

    n = 0

    con = init_db()

    for root in dirs or []:

        if not os.path.isdir(root):

            continue

        for cur, subs, files in os.walk(root):

            if FIRST_RUN.get('cancel'):

                break

            while FIRST_RUN.get('paused') and not FIRST_RUN.get('cancel'):

                time.sleep(0.3)

            if _noise_dir(cur) or _is_self_noise(cur):

                subs[:] = []

                continue

            subs[:] = [d for d in subs

                       if not d.startswith('.') and not d.startswith('$')

                       and not _is_reparse_dir(os.path.join(cur, d))

                       and not _noise_dir(os.path.join(cur, d))]

            rows = []

            for fn in files:

                p = os.path.join(cur, fn)

                try:

                    st = os.stat(p)

                except OSError:

                    continue

                rows.append((p, fn.lower(), cur, os.path.splitext(fn)[1].lower(),

                             st.st_size, st.st_mtime))

            if rows:

                try:

                    con.executemany(

                        'INSERT OR IGNORE INTO files(path,name,dir,ext,size,mtime) '

                        'VALUES(?,?,?,?,?,?)', rows)

                    for d in _parent_dirs(cur):

                        con.execute('INSERT OR IGNORE INTO dirs(path,name,parent) VALUES(?,?,?)',

                                    (d, os.path.basename(d).lower(), os.path.dirname(d)))

                    con.commit()

                except sqlite3.Error:

                    try:

                        con.rollback()

                    except Exception:

                        pass

                n += len(rows)

                FIRST_RUN['files'] = n

                FIRST_RUN['eta'] = max(0, int(budget - (time.time() - t0)))   # 这段受预算封顶

                FIRST_RUN['msg'] = '先建文件名索引…已收 %s 个文件' % format(n, ',')

            if n >= max_files or (time.time() - t0) > budget:

                return n

    return n





def _first_run_eta(docs_left, names_left):

    """粗估还要多久：正文按 1.2 篇/秒，文件名按 1500 条/秒（都是本机实测的保守值）。"""

    return int(max(0, docs_left) / 1.2 + max(0, names_left) / 1500.0 + 5)







# settings.json 存的是全局偏好（主题 / 语言 / 热键 / 欢迎页 / 首启标记）。

# 写它的有四方：设置抽屉、欢迎页、旧模板、后端首启线程。以前是

# 「读整个文件 → 改一个键 → write_text 整文件覆盖」，没有锁也不是原子写：

# 两个并发 POST 实测有 45% 概率互相抹掉 welcome_seen，写一半还会留下半截 JSON，

# GET 再被 except: pass 吞掉，偏好静默清零、欢迎页每次都弹（2026-11-06 专家审查实证）。

# 现在统一收口到 _settings_read / _settings_update：进程内锁 + 临时文件 os.replace。

_SETTINGS_LOCK = threading.RLock()





def _settings_path():

    return app_dir() / 'settings.json'





def _settings_read():

    """带锁读配置。文件损坏 / 不是对象时先备份成 .bad-* 再返回空 dict，

    绝不把半截 JSON 静默当成「用户没设过偏好」。"""

    with _SETTINGS_LOCK:

        sp = _settings_path()

        try:

            raw = sp.read_text(encoding='utf-8') if sp.exists() else ''

        except Exception as e:

            _log_file('settings.json read failed: %r' % (e,))

            return {}

        if not raw.strip():

            return {}

        try:

            d = json.loads(raw)

        except Exception as e:

            _log_file('settings.json broken JSON: %r' % (e,))

            d = None

        if not isinstance(d, dict):

            if d is not None:

                _log_file('settings.json is not an object: %s' % type(d).__name__)

            try:

                sp.replace(sp.with_name('settings.json.bad-%d' % int(time.time())))

            except Exception:

                pass

            return {}

        return d





def _settings_update(patch):

    """合并 patch 并原子落盘，返回 (ok, data)。所有写配置的地方都必须走这里。



    原子性靠「临时文件写完整 → os.replace 一次换名」，读者永远只会看到

    旧文件或完整的新文件；锁保证同进程内 read-modify-write 不被插队。

    """

    with _SETTINGS_LOCK:

        sp = _settings_path()

        d = _settings_read()      # RLock 可重入

        d.update(patch)

        tmp = sp.with_name('settings.json.tmp-%d' % os.getpid())

        try:

            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')

            os.replace(tmp, sp)

        except Exception as e:

            _log_file('settings.json write failed: %r' % (e,))

            try:

                if tmp.exists():

                    tmp.unlink()

            except Exception:

                pass

            return False, d

        # 读回校验：落盘的必须是可解析的 dict，且本次 patch 真的写进去了

        try:

            back = json.loads(sp.read_text(encoding='utf-8'))

            if not isinstance(back, dict) or any(back.get(k) != v for k, v in patch.items()):

                raise ValueError('settings.json readback mismatch')

        except Exception as e:

            _log_file('settings.json readback failed: %r' % (e,))

            return False, d

        return True, back





def _settings_mark(key, val):

    """往 settings.json 里写一个标记（保留其它键）。"""

    ok, _ = _settings_update({key: val})

    if not ok:

        print('[!] 写 settings.json 失败：%s=%r' % (key, val), flush=True)

    return ok





def first_run_need():

    """还没建过库、也没被用户主动拒绝过 → True。"""

    done = _settings_read().get('firstrun_done')

    # 'error'（写不进 scope / 抛异常）和 'empty'（当时没找到任何可索引目录）都不是

    # 终态：换了目录、插了盘、目录不再只读，下次启动就该再试一次。

    # 'skip' 是用户亲手点的「我自己选文件夹」，尊重它，别每次重启都自动开扫；

    # 想反悔可以在设置里手动点智能扫描（/api/firstrun 不受这个标记限制）。

    if done == 'skip' or (done and done not in ('error', 'empty')):

        return False

    try:

        con = init_db()

        nf = (con.execute('SELECT count(*) FROM files').fetchone() or [0])[0]

        nm = (con.execute('SELECT count(*) FROM meta').fetchone() or [0])[0]

    except Exception:

        return False

    return int(nf or 0) == 0 and int(nm or 0) == 0





def _bg_content_then_full():

    """首启后台：先把正文读完，再补齐全盘文件名；界面已经可用，不阻塞。"""

    try:

        if FIRST_RUN.get('cancel'):

            return

        try:

            # 只扫首启选中的那几个目录：传 None 会先全盘重扫一遍（几十秒），

            # 而用户此刻已经在等正文了，没理由再为“全盘”付一次扫描钱。

            dirs = list(FIRST_RUN.get('dirs') or [])

            update(dirs or None)

        except Exception as e:

            _log_file('bg content FAILED: %r' % (e,))

        try:

            _refresh_names_from_csv(str(app_dir() / 'scope.json'))

        except Exception:

            pass

    finally:

        try:

            if FIRST_RUN.get('cancel'):

                FIRST_RUN['bg'] = 'done'

                return

            _bg_full_disk()

        finally:

            FIRST_RUN['bg'] = 'done'

            try:

                con = init_db()

                nd = (con.execute('SELECT count(*) FROM docs_content').fetchone() or [0])[0]

                nf = (con.execute('SELECT count(*) FROM files').fetchone() or [0])[0]

                FIRST_RUN.update(files=int(nf or 0), docs=int(nd or 0), n=int(nd or 0))

            except Exception:

                pass





def _bg_full_disk():

    """首启「已经能用」之后，再在后台逛全盘，把没覆盖到的文件夹补进范围。



    用户看到的是：十几秒能搜 → 界面已经能用 → 过一会儿右下角说「又补进来 N 个」。

    而不是对着一个「正在扫描」的条干等到怀疑软件坏了。

    """

    try:

        if FIRST_RUN.get('cancel'):

            return

        FIRST_RUN['bg'] = 'busy'

        roots = scan_docs.drive_targets()

        discover_scan()

        if FIRST_RUN.get('cancel'):

            FIRST_RUN['bg'] = 'done'

            return

        cur = [o for o, _n in _scope_dirs()]

        cur_norm = {os.path.normcase(os.path.normpath(c)) for c in cur}

        groups = [g for g in (DISCOVER.get('groups') or []) if not g.get('noise')]

        extra = []

        for g in groups:

            d = _safe_scope_dir(str(g.get('dir') or ''))

            if d and os.path.normcase(os.path.normpath(d)) not in cur_norm:

                cur_norm.add(os.path.normcase(os.path.normpath(d)))

                extra.append(d)

        if not extra:

            FIRST_RUN['bg'] = 'done'

            return

        if save_scope(cur + extra):

            _log_file('bg full disk: +%d dirs' % len(extra))

            update(None)

            try:

                _refresh_names_from_csv(str(app_dir() / 'scope.json'))

            except Exception:

                pass

        FIRST_RUN['bg'] = 'done'

    except Exception as e:

        FIRST_RUN['bg'] = 'done'

        _log_file('bg full disk FAILED: %r' % (e,))





def first_run_auto():

    """首次运行自动建库：**先 60 秒内可搜，再后台补全**。



    阶段（写进 FIRST_RUN['stage']，界面按它显示人话）：

      names      只 stat 桌面/文档/下载的文件名 → 立刻能搜

      discover   全盘逛一遍，挑出候选文件夹（只读文件名）

      content    按范围抽正文（可以边搜边等）

      names_full 用新清单补齐全盘文件名

      done / error

    """

    global FIRST_RUN

    if FIRST_RUN.get('state') == 'busy':

        return

    FIRST_RUN = {'state': 'busy', 'stage': 'names', 'msg': '先建你桌面、文档、下载的文件名…',

                 'n': 0, 'files': 0, 'docs': 0, 'eta': 0, 'total': 0, 'paused': False,

                 'cancel': False, 't0': time.time()}

    try:

        _settings_mark('firstrun_state', 'running')

        dirs = _user_dirs()

        n1 = _quick_names(dirs) if dirs else 0

        FIRST_RUN.update(stage='names_done', files=n1,

                         msg=('已经能搜 %s 个文件了；全盘还在后台补，可以边搜边等' % format(n1, ','))

                         if n1 else '没找到桌面/文档/下载里的文件，直接开始全盘')

        _settings_mark('firstrun_stage', 'names')



        # 以前这里先「全盘逛一遍」再开始读正文：一个装满系统文件的 C 盘能逛十几分钟，

        # 用户看到的就是「装完一直在扫描、什么也没有」。现在先拿桌面/文档/下载直接开工，

        # 全盘发现挪到「已经能用了」之后在后台做。

        scope_dirs = []

        for d in dirs:

            d = _safe_scope_dir(d)

            if d and d not in scope_dirs:

                scope_dirs.append(d)

        if not scope_dirs:

            FIRST_RUN.update(stage='discover', msg='没找到桌面/文档/下载，先看一眼有哪些盘…')

            roots = scan_docs.drive_targets()

            FIRST_RUN['msg'] = '正在看你 %d 个盘上的文件（只读文件名，不读内容）' % len(roots)

            discover_scan()

            groups = [g for g in (DISCOVER.get('groups') or []) if not g.get('noise')]

            for d in (str(g.get('dir')) for g in groups if g.get('dir')):

                d = _safe_scope_dir(d)

                if d and d not in scope_dirs:

                    scope_dirs.append(d)

        if not scope_dirs:

            FIRST_RUN.update(state='error', stage='error',

                             msg='没找到可以自动索引的文件夹，请手动选一下')

            _settings_mark('firstrun_done', 'empty')

            return

        docs_total = int(DISCOVER.get('docs_total') or 0)

        if not save_scope(scope_dirs):

            FIRST_RUN.update(state='error', stage='error',

                             msg='索引范围写不进 scope.json（磁盘只读或被占用）')

            _settings_mark('firstrun_done', 'error')

            return

        # 关键改动：正文抽取可能要十几分钟（本机 3 个文件夹 1.6 万条候选，

        # 扫描版 PDF 单篇就要几秒）。用户此刻已经能按文件名搜了，没理由让他

        # 对着「正在读正文」的条干等。所以先宣布可用，正文+全盘都放后台。

        try:

            con = init_db()

            nf = (con.execute('SELECT count(*) FROM files').fetchone() or [0])[0]

        except Exception:

            nf = n1

        FIRST_RUN.update(state='done', stage='names_done', files=int(nf or 0),

                         docs=0, n=0, eta=0, bg='busy',

                         msg='已经能搜 %s 个文件名了；正文在后台继续读，读一篇多一篇'

                             % format(int(nf or 0), ','))

        _settings_mark('firstrun_done', 'auto')

        print('[i] 首启：文件名已就绪（%s 个），正文转后台' % format(int(nf or 0), ','),

              flush=True)

        FIRST_RUN['dirs'] = list(scope_dirs)

        threading.Thread(target=_bg_content_then_full, daemon=True).start()

        return

    except Exception as e:

        import traceback

        traceback.print_exc()

        FIRST_RUN.update(state='error', stage='error', msg='%s: %s' % (type(e).__name__, e))

        _settings_mark('firstrun_done', 'error')





def _safe_scope_dir(p):

    """索引范围只接受「本机已存在的目录」。



    为什么不能直接信界面：scope.json 里的路径会进 os.walk、会进 exists() 判断，

    写进一台远程共享目录就等于让程序去连那台机器（SMB 协商会带出 NetNTLMv2）。

    这里统一收口：必须本地盘符相对路径、必须 _local_only() 认可、必须真的存在。"""

    try:

        q = os.path.normpath(str(p).strip().strip('"'))

    except Exception:

        return None

    if not q or q in ('.', os.sep):

        return None

    if q.startswith('\\') or q.startswith('//'):

        return None

    if not (len(q) > 2 and q[1] == ':'):

        return None

    if not _local_only(q):

        return None

    try:

        if not os.path.isdir(q):

            return None

    except OSError:

        return None

    return q





def safe_scope_list(targets):

    """把界面传来的一串路径过滤成可信范围；全被滤掉时返回 None。"""

    out = []

    for t in (targets or []):

        q = _safe_scope_dir(t)

        if q and q not in out:

            out.append(q)

    return out or None





def save_scope(targets, scope_path=None):

    """把界面上的文件夹写进 scope.json，成功返回 True。

    原来这段写在 /api/update 里、外面只 print 一句：写不进去也照样起线程、照样回 ok，

    于是「新加的文件夹被静默忽略」换个入口又复发了一次。落盘失败必须让界面知道。"""

    try:

        sp = Path(scope_path) if scope_path else app_dir() / 'scope.json'

        try:

            sc = json.loads(sp.read_text(encoding='utf-8'))

        except Exception:

            sc = {}

        if not isinstance(sc, dict):

            sc = {}

        kept = safe_scope_list(targets)

        if not kept:

            print('[!] 索引范围里没有一个是本机存在的文件夹，未保存', flush=True)

            return False

        sc['include_dirs'] = kept

        sc.setdefault('scan_csv', 'scan_raw.csv')

        sp.write_text(json.dumps(sc, ensure_ascii=False, indent=2), encoding='utf-8')

        return True

    except Exception as e:

        print('[!] 保存索引范围失败：', e, flush=True)

        return False



# ── 第 2 层：范围搬家了，自己认亲并平移 ──────────────────────────────────

# 第 1 层只会"停下不动"。可真搬一次家，用户看到的就是"更新用不了、搜索点开全没了"

# ——2026-11-04 那次整目录搬迁就是这个形态。这一层做的是：

#   ① 发现范围目录不在了 → 在本机找同名文件夹当候选；

#   ② 拿库里那批"旧地址"下的文件做抽检，新位置对上八成以上才算认亲；

#   ③ 一次事务把 6 个路径列 + 正文库的 path 平移过去，并把 scope.json 里那行改写。

# 安全边界：抽检不够、候选不唯一（且控制台问不了人）→ 一条不改，退回第 1 层的"停下"。

# 宁可不动，也不猜。

REPAIR_SAMPLE = 60            # 抽检多少个文件来认亲

REPAIR_MIN_SAMPLE = 3         # 少于这个数不敢认

REPAIR_MATCH_RATIO = 0.8      # 抽检命中率达标才算同一个文件夹

REPAIR_WALK_SECONDS = 60      # 逛盘找同名目录的时间上限（D: 5.5 万目录约 10 秒）

REPAIR_SKIP_DIRS = {'windows', 'system volume information', '$recycle.bin', '$winreagent',

                    'config.msi', 'node_modules', '.git', '__pycache__', 'appdata',

                    'recovery', 'perflogs'}

_DIR_INDEX_CACHE = {}

# 认亲时「同名文件夹不止一个、又没法在控制台问人」的候选，暂存给界面点选

REPAIR_ASK = {'state': 'idle', 'old': '', 'items': [], 'scope': ''}



def _dir_index(root):

    """root 下所有子目录按「小写目录名 → [完整路径]」建索引（每块盘只建一次，进程内缓存）。"""

    key = os.path.normcase(root)

    if key in _DIR_INDEX_CACHE:

        return _DIR_INDEX_CACHE[key]

    idx, n, t0 = {}, 0, time.time()

    try:

        for cur, dirs, _files in os.walk(root):

            dirs[:] = [d for d in dirs if d.lower() not in REPAIR_SKIP_DIRS

                       and not _is_reparse_dir(os.path.join(cur, d))]

            for d in dirs:

                idx.setdefault(d.lower(), []).append(os.path.join(cur, d))

                n += 1

            if n > 400000 or time.time() - t0 > REPAIR_WALK_SECONDS:

                break

    except OSError:

        pass

    _DIR_INDEX_CACHE[key] = idx

    return idx



def _sample_paths(old, con):

    """从库里取若干条「旧地址下」的文件路径，用来验证新位置是不是同一个文件夹。"""

    pref = (os.path.normcase(os.path.normpath(old)) + os.sep).lower()

    for table in ('files', 'meta'):

        try:

            rows = con.execute(f'SELECT path FROM {table} WHERE lower(path) LIKE ? LIMIT ?',

                               (pref + '%', REPAIR_SAMPLE)).fetchall()

        except sqlite3.Error:

            rows = []

        if rows:

            return [p for (p,) in rows if p]

    return []



def _verify_candidate(cand, old, con):

    """抽检：旧地址下的样本文件平移到 cand 之后还在不在。返回 (命中数, 样本数)。"""

    samples = _sample_paths(old, con)

    if not samples:

        return (0, 0)

    tail = os.path.normpath(old).lower()

    hits = 0

    for p in samples:

        np_ = os.path.normpath(p)

        if not np_.lower().startswith(tail):

            continue

        rel = np_[len(tail):].lstrip('\\/')

        if rel and os.path.isfile(os.path.join(cand, rel)):

            hits += 1

    return (hits, len(samples))



def _candidates_by_suffix(old, con):

    """便宜的一招，先试它：库里如果已经有"别的地址 + 同样的相对后缀"的路径

    （搬到了另一个已索引的目录、或跨盘搬），直接反推新根，不用逛盘。"""

    samples = _sample_paths(old, con)

    if not samples:

        return []

    tail = os.path.normpath(old).lower()

    leaf = os.path.basename(tail)

    out, seen = [], set()

    for p in samples[:6]:

        np_ = os.path.normpath(p)

        if not np_.lower().startswith(tail):

            continue

        rel = np_[len(tail):].lstrip('\\/')

        if not rel:

            continue

        suf = ('\\' + leaf + '\\' + rel).lower()

        try:

            rows = con.execute('SELECT path FROM files WHERE lower(path) LIKE ? LIMIT 8',

                               ('%' + suf,)).fetchall()

        except sqlite3.Error:

            rows = []

        for (q,) in rows:

            ql = (q or '').lower()

            if ql.startswith(tail):

                continue

            i = ql.rfind(suf)

            if i <= 0:

                continue

            cand = os.path.normpath(q[:i + 1 + len(leaf)])   # 带上"资料"这一级本身

            k = os.path.normcase(cand)

            if k in seen or k == os.path.normcase(old):

                continue

            seen.add(k)

            try:

                if os.path.isdir(cand):

                    out.append(cand)

            except OSError:

                pass

    return out



def _candidates_for(old, con):

    """在本机找同名文件夹当候选，按抽检命中率排序。返回 [(路径, 命中, 样本)]。"""

    oldn = os.path.normpath(old)

    quick = []

    for c in _candidates_by_suffix(oldn, con):        # 先试便宜的：从库里反推

        hits, n = _verify_candidate(c, oldn, con)

        quick.append((c, hits, n))

    if any(h >= REPAIR_MIN_SAMPLE for _c, h, _n in quick):

        quick.sort(key=lambda x: (-(x[1] / x[2] if x[2] else 0.0), -x[1], len(x[0])))

        return quick

    leaf = os.path.basename(oldn).lower()

    if not leaf:

        return []

    roots = []

    r = _drive_root(oldn)

    if r:

        roots.append(r)

    for orig, _norm in _scope_dirs():       # 整块盘改了名时，再试别的范围所在的盘

        rr = _drive_root(orig)

        if rr and rr not in roots:

            roots.append(rr)

    seen, out = set(), []

    for root in roots:

        try:

            idx = _dir_index(root)

        except Exception:

            continue

        for c in idx.get(leaf, []):

            k = os.path.normcase(c)

            if k in seen or k == os.path.normcase(oldn):

                continue

            seen.add(k)

            hits, n = _verify_candidate(c, oldn, con)

            out.append((c, hits, n))

        if any(h >= REPAIR_MIN_SAMPLE for _c, h, _n in out):

            break                            # 这块盘已经认出亲了，不用再翻别的盘

    out.sort(key=lambda x: (-(x[1] / x[2] if x[2] else 0.0), -x[1], len(x[0])))

    return out



def _rewrite_prefix(old, new):

    """把库里所有以 old 开头的路径平移成 new（一次事务）。返回 {表.列: 条数}。"""

    con = init_db()

    old_n = os.path.normpath(old).rstrip('\\/')

    new_n = os.path.normpath(new).rstrip('\\/')

    lo = os.path.normcase(old_n) + os.sep

    pairs = (('meta', 'path'), ('files', 'path'), ('files', 'dir'),

             ('dirs', 'path'), ('dirs', 'parent'), ('recent', 'path'))

    counts = {}

    con.execute('BEGIN IMMEDIATE')

    try:

        for t, c in pairs:

            try:

                cur = con.execute(

                    f'UPDATE {t} SET {c} = ? || substr({c}, ?) WHERE lower({c}) LIKE ?',

                    (new_n, len(lo), lo + '%'))

                if cur.rowcount:

                    counts[f'{t}.{c}'] = cur.rowcount

                cur = con.execute(f'UPDATE {t} SET {c} = ? WHERE lower({c}) = ?',

                                  (new_n, os.path.normcase(old_n)))

                if cur.rowcount:

                    counts[f'{t}.{c}'] = counts.get(f'{t}.{c}', 0) + cur.rowcount

            except sqlite3.Error:

                continue          # 老库可能没有这张表/这一列

        # 被搬走的那个文件夹自己那一行：它的 parent 指向旧父目录（在旧前缀之外，

        # 上面的前缀更新扫不到），搬完要跟着换，否则「搜文件夹」里它的上级是条死路。

        try:

            con.execute('UPDATE dirs SET parent = ? WHERE lower(path) = ?',

                        (os.path.dirname(new_n), os.path.normcase(new_n)))

        except sqlite3.Error:

            pass

        try:      # 正文库：path 是 UNINDEXED，改内容表即可，不动 trigram 索引、rowid 不变

            cur = con.execute('UPDATE docs_content SET c0 = ? || substr(c0, ?) '

                              'WHERE lower(c0) LIKE ?', (new_n, len(lo), lo + '%'))

            if cur.rowcount:

                counts['docs'] = cur.rowcount

        except sqlite3.Error:

            pass

        con.commit()

    except Exception:

        con.rollback()

        raise

    return counts



def _update_scope_path(old, new, scope_path=None):

    """把 scope.json 里指向 old 的那行改成 new（改前留一份 .bak）。"""

    sp = Path(scope_path) if scope_path else app_dir() / 'scope.json'

    try:

        raw = sp.read_text(encoding='utf-8')

        data = json.loads(raw)

    except Exception as e:

        print(f'[!] 读不了索引范围配置：{e}')

        return False

    old_n = os.path.normcase(os.path.normpath(old))

    new_n = os.path.normpath(new)

    hit = 0

    for key in ('include_dirs', 'exclude_dirs'):

        arr = data.get(key) or []

        for i, s in enumerate(arr):

            try:

                same = os.path.normcase(os.path.normpath(str(s))) == old_n

            except Exception:

                same = False

            if same:

                arr[i] = new_n

                hit += 1

        if arr:

            data[key] = arr

    if not hit:

        return False

    try:

        sp.with_name(sp.name + time.strftime('.bak-%Y%m%d-%H%M%S')).write_text(

            raw, encoding='utf-8')

    except Exception:

        pass

    sp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    return True



def repair_scope(miss=None, scope_path=None, ask=True):

    """第 2 层：范围目录搬走了 → 认亲 → 改库 + 改配置。返回修好的 [(旧, 新)]。



    认不出来的仍然留在返回的缺目录里，交给 update() 那道护栏去停下报错。

    ask=True 且控制台可交互时，候选不唯一会让人选号；否则只打印候选。"""

    sp = Path(scope_path) if scope_path else app_dir() / 'scope.json'

    if miss is None:

        miss = _missing_scope_dirs(str(sp))

    if not miss:

        return []

    con = init_db()

    fixed = []

    for old in miss:

        print(f'[*] 索引范围里的文件夹不在了：{old}')

        cands = _candidates_for(old, con)

        good = [(c, h, n) for (c, h, n) in cands

                if n >= REPAIR_MIN_SAMPLE and h / n >= REPAIR_MATCH_RATIO]

        if not good:

            print('    —— 本机没找到能对上的同名文件夹（也可能只是改了名）。先不动。')

            continue

        pick = None

        if len(good) == 1:

            pick = good[0]

        elif ask and sys.stdin and sys.stdin.isatty():

            print('    找到多个可能的文件夹：')

            for i, (c, h, n) in enumerate(good[:6], 1):

                print(f'      [{i}] {c}    （抽检 {n} 个，{h} 个对上）')

            print('      [0] 都不是，先别改')

            try:

                sel = int((input('    搬到了哪一个？输入编号回车：') or '0').strip() or 0)

            except Exception:

                sel = 0

            if 1 <= sel <= min(6, len(good)):

                pick = good[sel - 1]

        else:

            print('    找到多个可能的文件夹，需要人来确认：')

            for c, h, n in good[:6]:

                print(f'      {c}    （抽检 {n} 个，{h} 个对上）')

            # 控制台问不了人时（桌面版就是这么跑的），把候选交给界面点选，

            # 否则用户只能看着黑窗里几行路径干瞪眼——文档还写着「什么都不用改」。

            REPAIR_ASK.update(state='wait', old=old,

                              items=[{'path': c, 'hits': h, 'n': n} for c, h, n in good[:6]],

                              scope=str(sp))

        if not pick:

            print('    —— 一条都没改。请在设置里把旧文件夹换成新位置，再点更新。')

            continue

        cpath, hits, n = pick

        print(f'    → 认出来了：{cpath}（抽检 {n} 个文件，{hits} 个还在）')

        counts = _rewrite_prefix(old, cpath)

        ok_scope = _update_scope_path(old, cpath, str(sp))

        total = sum(v for k, v in counts.items() if k != 'docs')

        detail = '、'.join(f'{k} {v}' for k, v in counts.items() if v)

        print(f'    → 已平移库里 {total} 条路径记录（{detail or "无"}）'

              + ('，索引范围配置已更新' if ok_scope else ''))

        fixed.append((old, cpath))

    if fixed:

        REPAIR_ASK.update(state='idle', old='', items=[], scope='')

    return fixed



def repair_choose(idx):

    """界面点了某个候选之后：改库 + 改范围配置，然后把待确认状态清掉。

    返回 (ok, payload)。真正重跑更新由调用方（HTTP 层）起线程。"""

    if REPAIR_ASK.get('state') != 'wait':

        return False, {'err': '现在没有等待确认的候选'}

    items = REPAIR_ASK.get('items') or []

    try:

        it = items[int(idx)]

    except (TypeError, ValueError, IndexError):

        return False, {'err': '候选编号不对'}

    old = REPAIR_ASK.get('old') or ''

    newp = it.get('path') or ''

    sp = REPAIR_ASK.get('scope') or ''

    if not old or not newp:

        return False, {'err': '候选记不全，请在设置里手动改'}

    counts = _rewrite_prefix(old, newp)

    ok_scope = _update_scope_path(old, newp, sp)

    REPAIR_ASK.update(state='idle', old='', items=[], scope='')

    total = sum(v for k, v in counts.items() if k not in ('docs', 'dirs_parent'))

    print(f'[+] 按界面选择把 {old} 改成了 {newp}：{total} 条路径记录，范围配置'

          + ('已更新' if ok_scope else '没改成（请检查 scope.json 权限）'))

    return True, {'new': newp, 'counts': counts, 'scope': ok_scope}





def repair_dismiss():

    """用户点「都不是」：清掉待确认，别一直弹。"""

    REPAIR_ASK.update(state='idle', old='', items=[], scope='')

    return True, {}



def _repair_then_recheck(miss, scope_path):

    """update() 用：先试自动认亲，再把"还没修好"的缺目录交回去。"""

    try:

        fixed = repair_scope(miss, scope_path)

    except Exception as e:

        print(f'[!] 自动修复出错（数据没动）：{type(e).__name__}: {e}')

        return miss

    if not fixed:

        return miss

    _prog(msg='索引范围已自动修复：' + ' → '.join(f'{a} ⇒ {b}' for a, b in fixed),

          k='m_scope_fixed', a={'p': ' → '.join(f'{a} ⇒ {b}' for a, b in fixed)})

    return _missing_scope_dirs(scope_path)



# 失效路径登记表：搜索时遇到不存在的路径记下来，启动后台 prune 时优先清，

# 而不是每次搜索都同步阻塞写库。线程安全。

GONE_SEEN = set()

GONE_LOCK = threading.Lock()



def _mark_gone(path):

    """记录搜索时发现的失效路径。不阻塞搜索，下次 prune 时一起清掉。"""

    if not path:

        return

    k = os.path.normcase(path)

    with GONE_LOCK:

        if len(GONE_SEEN) < 5000:        # 防止异常情况爆内存

            GONE_SEEN.add(k)



def prune(force=False, scope_path=None):

    """清理已删除文件的索引条目。



    三道保护，每一道都是被真实事故逼出来的：

    ① 整块盘不可达（移动硬盘没插、网络盘没挂载）→ 那盘下的条目整体跳过；

    ② 索引范围里的文件夹本身不在了（搬家、改盘符）→ 它名下整体跳过。

       这时 exists()=False 不代表"文件被删了"，只代表"地址变了"；

    ③ 一次要清掉的条目太多 → 停下问人，不删（force=True 表示人已经确认过）。

       记录留着最多搜出一条死路径（点开提示不存在），清错了要重扫一两个小时。

    """

    con = init_db()

    paths = [p for (p,) in con.execute('SELECT path FROM meta')]

    scopes = _scope_dirs(scope_path)

    if not scopes:

        # scope.json 读不出来时，②那道「范围还在吗」的保护等于不存在（每条都走盘根判断），

        # 一旦再配上 force，就会把整库当"用户删了"清掉。宁可什么都不做。

        msg = ('索引范围配置读不出来，为了不误删，这次一条都没清。'

               '请先在设置里重新选一次文件夹并保存，再清理。')

        print('[!] ' + msg)

        _prog(state='error', err=msg, k='e_no_scope')

        return 0

    alive = {}

    def _reachable(p):

        root = _drive_root(p)

        if not root:

            return True

        if root not in alive:

            alive[root] = os.path.exists(root)

        return alive[root]

    scope_ok = {}          # 每个范围只测一次 isdir，别对着几万条路径反复碰盘

    def _scope_of(p):

        """p 落在哪个索引范围里（取最长前缀）？不属于任何范围返回 None。"""

        pl = os.path.normcase(p)

        best = None

        for orig, norm in scopes:

            if pl == norm or pl.startswith(norm + os.sep):

                if best is None or len(norm) > len(best[1]):

                    best = (orig, norm)

        return best

    def _in_live_scope(p):

        """p 落在哪个索引范围里？那个范围还在吗？不属于任何范围时返回 True（交给盘根判断）。"""

        best = _scope_of(p)

        if best is None:

            return True

        orig, norm = best

        if norm not in scope_ok:

            try:

                scope_ok[norm] = os.path.isdir(orig)

            except OSError:

                scope_ok[norm] = False

        return scope_ok[norm]

    gone, skipped, skipped_scope = [], 0, 0

    for p in paths:

        if not _reachable(p):

            skipped += 1

            continue

        if not _in_live_scope(p):

            skipped_scope += 1

            continue

        if not os.path.exists(p):

            gone.append(p)

    total = len(paths)

    if not force and len(gone) > PRUNE_BULK_LIMIT and len(gone) > total * PRUNE_BULK_RATIO:

        dead = _missing_scope_dirs(scope_path)

        tip = ('索引范围里的这些文件夹已经不在了：' + '、'.join(dead[:3]) + '。'

               if dead else '多半是索引范围失效、盘符变了或权限出了异常。')

        msg = (f'这次要清掉 {len(gone)} 条（库内共 {total} 条），已暂停，一条都没删。'

               f'{tip}请先确认要索引的文件夹还在原处；如果确认这些文件是你真的删掉了，'

               f'双击【强制清理.bat】再清一次。')

        print('[!] ' + msg)

        _prog(state='error', err=msg, k='e_prune_bulk',

              a={'n': len(gone), 'total': total, 'p': dead[0] if dead else ''})

        return 0

    # ④-补：全局比例看不见「某个子目录整块被搬走」——实测单个子目录只丢 16%、

    # 另一个 12.8%，都低于 50% 的全局阈值，可那正是一次搬家。

    # 但「清桌面、删下载」也会让一批文件消失，只是它们是散着删的，文件夹还在。

    # 所以这里不看比例，看「有没有一整层文件夹连人带文件一起没了」：

    # 某个目录自己已经不存在，且它名下有 >= PRUNE_SCOPE_MIN 条记录同时消失。

    # 这类情况 exists()=False 说的不是「删了」，而是「搬走了/改名了」。

    if not force:

        dir_gone, dir_alive = {}, {}

        for p in gone:

            d = os.path.dirname(p)

            ok = dir_alive.get(d)

            if ok is None:

                try:

                    ok = os.path.isdir(d)

                except OSError:

                    ok = True        # 判断不了就当它还在，宁可少删、不要误停

                dir_alive[d] = ok

            if not ok:

                dir_gone[d] = dir_gone.get(d, 0) + 1

        worst = max(dir_gone.items(), key=lambda kv: kv[1], default=None)

        if worst and worst[1] >= PRUNE_SCOPE_MIN:

            d, lost = worst

            msg = (f'文件夹「{d}」整个不见了，它名下有 {lost} 条记录同时找不到了，'

                   f'已暂停，一条都没删。多半是它被搬走或改名了——'

                   f'点一次更新让它自动认亲；确认是删掉了，再双击【强制清理.bat】。')

            print('[!] ' + msg)

            _prog(state='error', err=msg, k='e_prune_scope_loss',

                  a={'n': lost, 'p': d})

            return 0

    rids = _doc_rids(con)

    for p in gone:

        con.execute('DELETE FROM meta WHERE path=?', (p,))

        _doc_del(con, rids, p)

    con.commit()

    tail = ''

    if skipped:

        tail += f'，另有 {skipped} 条所在磁盘当前不可达已跳过'

    if skipped_scope:

        tail += f'，另有 {skipped_scope} 条所在索引文件夹已失效已跳过'

    print(f'清理失效条目: {len(gone)} 条{tail}')

    return len(gone)



def _refresh_names_from_csv(scope_path=None):

    """把这次「更新」扫出来的文件清单，增量补进 files / dirs（不重扫全盘）。



    背景：文档索引（update）和全盘文件名索引（build_files_index）原本是两个按钮。

    用户刚存的文件，点完「更新」名字仍然搜不到，得再去点一次「重建文件名索引」——

    而且他根本不知道还有这一步。扫描阶段本来就把范围内的 path,size 写进了

    scan_raw.csv，这里直接拿它补，几千到几万行进一个事务，通常一两秒。



    只做增补：

      * 新路径 → 插一行（exe/lnk 顺手读一下描述，界面上才认得出「微信」）；

      * 老路径 → 只更正 size，不动 mtime（mtime 是「最近打开」排序的依据之一）和 descr；

      * 已有记录的盘符大小写（`d:/work/…` 与 `D:/work/…`）按不区分大小写去重，避免搜出两份。



    返回 (新增文件数, 新增文件夹数)；读不到清单就返回 (0, 0)，绝不抛。"""

    try:

        sp = Path(scope_path) if scope_path else app_dir() / 'scope.json'

        cfg = json.loads(sp.read_text(encoding='utf-8'))

        csvp = Path(cfg.get('scan_csv') or 'scan_raw.csv')

        if not csvp.is_absolute():

            csvp = sp.parent / csvp

        if not csvp.exists():

            return 0, 0

        rows = []

        with open(csvp, 'r', encoding='utf-8-sig', newline='') as f:

            rd = csv.reader(f)

            next(rd, None)                      # 表头 path,size

            for r in rd:

                if len(r) >= 2 and r[0]:

                    rows.append((r[0], r[1]))

        if not rows:

            return 0, 0

        con = sqlite3.connect(DB, check_same_thread=False)

        con.execute('PRAGMA busy_timeout=5000')

        # 只把「小写盘符开头」的老记录读出来（迁移遗留的 d:\old\…），一次全表扫 0.4 秒，

        # 换来几千行比对，比逐行 WHERE lower(path)=? 快得多。

        case_map = {}

        for (old,) in con.execute(

                "SELECT path FROM files WHERE substr(path,1,1) BETWEEN 'a' AND 'z' "

                "AND substr(path,2,1)=':'"):

            case_map[old.lower()] = old

        filedirs = {}

        fnew, dnew, exes = [], {}, []

        for full, sz in rows:

            key = full.lower()

            if key in case_map:

                # 老记录（迁移遗留的小写盘符）：顺手把 size/mtime 对齐磁盘。

                # 时间轴首页靠 files.mtime 说话，只补 size 的话「今天改过的文件」

                # 会一直显示成上次全盘建索引那天的日期。

                real = case_map[key]

                try:

                    st = os.stat(real)

                    filedirs[key] = (real, st.st_size, st.st_mtime)

                except OSError:

                    filedirs[key] = (real, sz, None)

                continue

            cur = os.path.dirname(full)

            base = os.path.basename(full)

            ext = os.path.splitext(base)[1].lower()

            if _is_self_noise(full):        # 程序自己的库与打包产物，别进文件名索引

                continue

            try:

                st = os.stat(full)

                mt = st.st_mtime

            except OSError:

                continue

            if ext in ('.exe', '.lnk') and len(exes) < 400:

                exes.append((full, ext))

            fnew.append((full, base.lower(), cur, ext, st.st_size, mt))

            filedirs[key] = (full, st.st_size, mt)

            dnew[cur] = None

            for d in _parent_dirs(cur):

                dnew[d] = None

        nfile = len(fnew)

        ndir = 0

        if filedirs:

            # 已经在 fnew 里的新文件不用再 UPDATE；只更新老记录

            old_rows = [(sz, mt, p_) for (p_, sz, mt) in filedirs.values()

                        if mt is not None]

            if old_rows:

                con.executemany(

                    'UPDATE files SET size=?, mtime=? WHERE path=? AND (size IS NOT ? OR mtime IS NOT ?)',

                    [(sz, mt, p_, sz, mt) for sz, mt, p_ in old_rows])

        if fnew:

            con.executemany('INSERT OR IGNORE INTO files'

                            '(path,name,dir,ext,size,mtime) VALUES(?,?,?,?,?,?)', fnew)

        if dnew:

            # files 里出现过的目录链，缺的补进 dirs（空文件夹另说，这里只管有文件的）。

            # dirs 表是老库/刚建的库都可能没有（它由 build_files_index 建），缺就跳过。

            have = set()

            try:

                for (d,) in con.execute('SELECT path FROM dirs'):

                    have.add(d.lower())

            except sqlite3.Error:

                have = None

            if have is None:

                dnew = {}

            add = []

            for d in dnew:

                if d.lower() in have or not d:

                    continue

                base = os.path.basename(d)

                if not base:

                    continue

                try:

                    py, ini = py_keys(base)

                except Exception:

                    py, ini = None, None

                add.append((d, base.lower(), os.path.dirname(d), py, ini))

            if add:

                con.executemany('INSERT OR IGNORE INTO dirs'

                                '(path,name,parent,py,ini) VALUES(?,?,?,?,?)', add)

                ndir = len(add)

        # 新冒出来的 exe/lnk 补描述：不补的话「微信」搜不到刚下载的应用

        for full, ext in exes:

            try:

                des = _file_descr(full, ext)

            except Exception:

                des = None

            if not des:

                continue

            con.execute('UPDATE files SET descr=? WHERE path=? AND descr IS NULL', (des, full))

            con.execute('INSERT OR REPLACE INTO appdesc(path,descr,ext,mtime,size) '

                        'SELECT path,descr,ext,mtime,size FROM files WHERE path=?', (full,))

        con.commit()

        con.close()

        return nfile, ndir

    except Exception as e:

        print(f'[!] 文件名索引增量更新没做完：{type(e).__name__}: {e}')

        return 0, 0





_SELF_DIRS = ('tmp', 'backups', '.workbuddy', '_internal', 'release', '__pycache__', '.git')

_SELF_NAMES = ('scan_raw.csv', 'brain.db', 'brain.db-wal', 'brain.db-shm',

               'debug.log', 'eval_report.txt', 'settings.json', 'notices_cache.json',

               'path_undo_20261104-194448.json')





def _self_root_nc():

    """程序自己的目录（小写、带尾分隔符），用来把整棵子树排除出索引。"""

    try:

        return os.path.normcase(str(app_dir())) + os.sep

    except Exception:

        return '\x00'





def _is_self_noise(p) -> bool:

    """程序自己目录里的东西不进索引：数据库本体、扫描清单、打包产物、临时文件、备份。



    以前 scope 里带 d:i工作，而程序就在它下面，结果 scan_raw.csv（62 MB）

    和 release/build 里的上千个 .txt 都被当文档收了，搜「就业协议书」第二屏就冒出

    一条 scan_raw.csv —— 用户会以为程序在自娱自乐。

    """

    try:

        base = str(app_dir()).rstrip(chr(92) + '/').lower()

        q = str(p).lower()

        if not q.startswith(base):

            return False

        rel = q[len(base):].lstrip(chr(92) + '/')

        if not rel:

            return False

        head = rel.split(chr(92))[0].split('/')[0]

        if head in _SELF_DIRS:

            return True

        name = rel.split(chr(92))[-1].split('/')[-1]

        return name in _SELF_NAMES

    except Exception:

        return False





def _parent_dirs(p):

    """把 p 逐级往上收集（不含盘符根本身），用于补 dirs 表。"""

    out = []

    cur = p

    while True:

        parent = os.path.dirname(cur)

        if not parent or parent == cur or len(parent) <= 3:

            break

        out.append(parent)

        cur = parent

    return out





def update(targets=None, scope_path=None):

    """增量维护一条龙：重扫清单 → 索引新文件 → 清理已删除条目。

    targets=None 时全盘扫描（命令行老行为）；桌面版传入用户选的文件夹列表。"""

    import traceback

    try:

        if PROG['state'] == 'busy':

            print('已有更新在进行，忽略本次请求')

            return

        _prog(state='busy', phase='scan', err='')

        scope = Path(scope_path) if scope_path else app_dir() / 'scope.json'

        if not scope.exists():

            _prog(state='error', err='找不到 scope.json —— 请先在设置里选择要索引的文件夹',

                  k='e_no_scope')

            print('[!] 找不到 scope.json')

            return

        # scope.json 语法坏了比"目录搬走"更隐蔽：_missing_scope_dirs 读不出来会返回空，

        # 于是护栏以为"没有约束"，扫描照跑、prune 照清。这里必须先把它摁住。

        try:

            json.loads(scope.read_text(encoding='utf-8'))

        except Exception as e:

            msg = (f'索引范围文件（scope.json）读不了：{e}。'

                   f'索引一条没动 —— 请在设置里重新选一次文件夹并保存。')

            print('[!] ' + msg)

            _prog(state='error', err=msg, k='e_no_scope')

            return

        # 开扫之前先体检范围。搬家/改盘符之后这里会整批命中，而继续往下跑的后果是

        # 扫描扫出 0 个文件、prune 把整库当"用户删了"——一次更新就能毁掉索引。

        # 命中时先走第 2 层自动认亲（repair_scope）；认不出来的才停下不动。

        miss = _missing_scope_dirs(str(scope))

        if miss:

            miss = _repair_then_recheck(miss, str(scope))

        if miss:

            msg = (f'要索引的文件夹找不到了：{"、".join(miss[:3])}。'

                   f'已试过自动找新位置，没认出来（或需要你确认），索引一条没动。'

                   f'请确认它还在原处（是不是搬走了？），'

                   f'或在设置里把它换成新位置，再点更新。')

            print('[!] ' + msg)

            _prog(state='error', err=msg, k='e_scope_gone', a={'p': '、'.join(miss[:3])})

            return

        scan_docs.run_scan(targets)

        # 清单不在数据目录时，index() 会 sys.exit(1)（外层接住后变成「什么都没发生」）。

        # 与其让用户看到「建好了：N 个文件、0 篇正文」，不如当场说清楚。

        try:

            _sp = json.loads(scope.read_text(encoding='utf-8'))

            _csv = Path(_sp.get('scan_csv') or 'scan_raw.csv')

            if not _csv.is_absolute():

                _csv = app_dir() / _csv

        except Exception:

            _csv = None

        if _csv is not None and not _csv.exists():

            msg = ('扫描清单没生成（%s 不存在），这次只更新了文件名、正文一篇都没读。'

                   '请确认程序目录可写，然后重新点一次更新。' % _csv)

            print('[!] ' + msg)

            _prog(state='error', err=msg, k='e_no_scan')

            return

        index(str(scope))

        _prog(phase='prune', msg='清理失效条目', k='m_pruning')

        prune()

        # prune 踩到"一次删太多"的刹车时会把状态置成 error 并把原因写进 err，

        # 这里不能再盖一句"更新完成"——否则用户只看到成功，不知道一条都没清。

        if PROG.get('state') == 'error':

            return

        # 顺手把这次扫到的文件名补进 files：刚存的文件不点「重建文件名索引」也能搜到。

        # 花一两秒，换来「更新」一件事就够用 —— 用户不需要知道有两个索引。

        _prog(phase='names', msg='同步文件名索引', k='m_names_sync')

        nf, nd = _refresh_names_from_csv(str(scope))

        if nf:

            print(f'[i] 文件名索引补进 {nf} 个新文件（不用再点「重建文件名索引」）')

        elif PROG.get('state') == 'busy':

            print('[i] 文件名索引已经是最新的')

        # 第一次用的人到这儿只完成了「文档正文」这一半：60 万个文件名的索引还要再点一次

        # 🗂「重建文件名索引」，而且那个按钮藏在另一个折叠区里——首启的断点就在这。

        # 干脆在这里标一下，界面看到 need_names 就自动接着建，不用人再点。

        try:

            _c = init_db()

            _need = (_c.execute('SELECT count(*) FROM files').fetchone() or [0])[0] == 0

        except sqlite3.Error:

            _need = False

        if _need:

            print('[i] 还没建文件名索引，接着自动建（第一次会多花几十秒）')

        db_upkeep()          # 更新跑完顺手收草稿本（几百 MB 的 -wal 就是这么攒出来的）

        db_reclaim()         # 清理掉一批文件后把空洞还回磁盘

        _prog(state='done', phase='', msg='更新完成', k='m_update_done', need_names=_need)

    except (Exception, SystemExit) as e:

        # index() 遇到坏配置是 sys.exit(1)；它跑在 /api/update 起的线程里，

        # 不接住就会带着 state='busy' 一起死，界面从此永久"正在更新"。

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

                _con = _open_db_guarded()

    return _con


def _open_db_guarded():
    """打开数据库；文件损坏就备份坏库、起一个空库，让程序还能用。

    断电、蓝屏、磁盘写错误都会留下 `database disk image is malformed`。
    以前这条异常会从 conn() 一路冒到 HTTP 层：每个接口都 500，界面一直转圈，
    用户唯一的出路是找到并删掉 brain.db —— 而普通用户根本不知道有这个文件。
    现在变成「坏库改名留档 → 起空库 → 用户重新扫一遍」：索引是可再生的，
    用户的文件一个都没动，程序也不至于打不开。

    **必须先探测、再动文件**：如果直接 init_db() 抛异常，异常的 traceback 会
    一直引用那条 sqlite 连接（局部变量 con 没被回收），Windows 上这时
    `os.replace` 会报 WinError 32「另一个程序正在使用此文件」——
    2026-11-07 实测踩到。所以用一条「只读 schema 头」的探针连接，
    它在 finally 里立刻 close，之后才轮得到改名。
    """
    try:
        probe = sqlite3.connect(DB, check_same_thread=False)
        try:
            # 只读文件头：损坏的库在这一步就会抛 DatabaseError，比 quick_check 便宜得多
            probe.execute('PRAGMA schema_version').fetchone()
        finally:
            probe.close()
    except sqlite3.DatabaseError as e:
        msg = str(e).lower()
        if not any(k in msg for k in ('malformed', 'not a database', 'corrupt')):
            raise
        stamp = time.strftime('%Y%m%d-%H%M%S')
        bad = DB.with_name(DB.name + '.bad-' + stamp)
        try:
            if DB.exists():
                DB.replace(bad)
            for suf in ('-wal', '-shm'):
                p = DB.with_name(DB.name + suf)
                if p.exists():
                    p.replace(bad.with_name(bad.name + suf))
        except Exception as ex:
            # 换不动（被占用/没权限）就别硬来，把原异常抛出去，至少日志里看得见
            _log_file('数据库损坏且备份失败：%r / %r' % (e, ex))
            raise
        _log_file('数据库损坏（%s）→ 已留档 %s，重建空库，请重新建索引' % (e, bad))
    return init_db()





def db_upkeep(min_mb=8.0):

    """把 WAL 草稿本誊回主库并截断（wal_checkpoint TRUNCATE）。



    为什么需要它：界面服务常驻，会一直持有读快照；SQLite 的默认自动 checkpoint（1000 页≈4MB）

    在有读者时收不干净，于是 -wal 越攒越大——2026-11-04 实测攒到 285 MB，而主库还同时躺着

    3.1 GB 的空闲页，库一度 3.85 GB。每次「更新 / 重建文件名索引」结束、以及服务启动时各收一次，

    没人在读就是毫秒级；有人在读就静默跳过，绝不打扰用户。

    返回 (改前字节, 改后字节)；低于 min_mb 或失败返回 None。"""

    try:

        p = Path(DB)

        if not p.exists():

            return None

        wal = Path(str(DB) + '-wal')

        before = wal.stat().st_size if wal.exists() else 0

        if before < min_mb * 1024 * 1024:

            return None

        con = sqlite3.connect(str(DB), timeout=10)

        try:

            con.execute('PRAGMA busy_timeout=8000')

            # 先 PASSIVE（有读者也能推进一部分），再试 TRUNCATE；以前只有一次 TRUNCATE，

            # 有读者就整条失败、WAL 一直停在几百 MB。

            con.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()

            row = None

            for _retry in range(3):

                row = con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()

                if row and not row[0]:

                    break

                time.sleep(0.2)

        finally:

            con.close()

        after = wal.stat().st_size if wal.exists() else 0

        if row and row[0]:          # 第一列 1 = 有读者挡着，没截断成

            print(f'[i] WAL 还有 {after / 1048576:.0f} MB：有窗口在读库，下次更新再收', flush=True)

            return None

        if after < before:

            print(f'[i] 数据库草稿本已回收：{before / 1048576:.1f} MB → '

                  f'{after / 1048576:.1f} MB', flush=True)

        return (before, after)

    except Exception:

        return None





AUTO_RECLAIM_MB = 200.0     # 空闲页攒到这么多才值得整理一次（VACUUM 要几秒）





def db_reclaim(min_free_mb=AUTO_RECLAIM_MB):

    """把删除留下的空洞收回去，并让库以后自己收。



    为什么需要：删文件、去重、重建索引都会在库里留「空洞」——行没了，页还占着文件大小。

    2026-11-04 实测空洞一度到 3.1 GB（占库 81%），用户看到的就是「这软件怎么越用越大」，

    和微信一样烦。做法分两种：

      - 老库还是 auto_vacuum=0：一次性 `PRAGMA auto_vacuum=INCREMENTAL; VACUUM`，

        VACUUM 顺手把空洞收掉，同时打开「以后每次删除自动回收」；

      - 已经是 INCREMENTAL：只跑 `PRAGMA incremental_vacuum`，毫秒级，不需要 VACUUM。

    有窗口在读库时 VACUUM 会等不到锁，捕获异常静默跳过，下次更新再来。

    返回 (整理前字节, 整理后字节)；没到阈值或失败返回 None。"""

    try:

        p = Path(DB)

        if not p.exists():

            return None

        con = sqlite3.connect(str(DB), timeout=10)

        try:

            con.execute('PRAGMA busy_timeout=8000')

            ps = (con.execute('PRAGMA page_size').fetchone() or [4096])[0] or 4096

            free = ((con.execute('PRAGMA freelist_count').fetchone() or [0])[0]) * ps

            mode = (con.execute('PRAGMA auto_vacuum').fetchone() or [0])[0]

            before = p.stat().st_size

            if mode == 0:

                if free < min_free_mb * 1048576:

                    return None

                con.execute('PRAGMA auto_vacuum=INCREMENTAL')

                con.execute('VACUUM')

                con.commit()

            else:

                # 已经是增量模式：删除会把页挂到空闲表，这里只还「确实有」的那部分；

                # 没有空洞就直接返回，免得服务一启动就写库、把只读测试也惊动。

                if free < ps:

                    return None

                con.execute('PRAGMA incremental_vacuum(%d)' % max(1, free // ps))

                con.commit()

        finally:

            con.close()

        after = p.stat().st_size

        if after < before:

            print(f'[i] 数据库整理完成：{before / 1048576:.0f} MB → {after / 1048576:.0f} MB'

                  f'（收回 {(before - after) / 1048576:.0f} MB，以后删除会自动回收）', flush=True)

        return (before, after)

    except Exception:

        return None





# 快搜窗底部两行（结果列表下方）也要跟着语言走，单独补两个键，

# 免得为了两句话去改上面五个字典块的结构。

MINI_L_EXTRA = {

    'zh': {'foot_typing': '也可以直接打字，正文一样能搜', 'foot_esc': 'Esc 关闭', 'tag_body': '正文'},

    'en': {'foot_typing': 'Type directly — document text is searched too', 'foot_esc': 'Esc to close',

           'tag_body': 'Text'},

    'es': {'foot_typing': 'Escribe directamente: también busca en el texto', 'foot_esc': 'Esc para cerrar',

           'tag_body': 'Texto'},

    'pt': {'foot_typing': 'Digite direto: também busca no texto', 'foot_esc': 'Esc para fechar',

           'tag_body': 'Texto'},

    'hi': {'foot_typing': 'सीधे टाइप करें — दस्तावेज़ पाठ भी खोजा जाता है', 'foot_esc': 'बंद करने के लिए Esc',

           'tag_body': 'पाठ'},

}

MINI_L = {

    'zh': {'ph': '搜索文件名、文件夹或正文内容', 'detail': '详细搜索（Ctrl+Enter）', 'search': '搜索',

           'recent': '最近打开', 'recent_hint': 'Enter 直接打开', 'fr': 'Ctrl+Enter 详细搜索',

           'sep': '以下 {n} 条命中在文档正文里', 'empty': '输入文件名或正文关键词，边打边出结果',

           'busy': '正在搜正文…', 'c_hint': 'c: 只搜正文',

           'none': '没有找到 “{q}” 的文件名或正文',

           'empty_hint': '双击 Ctrl 唤出 · Esc 关闭', 'more': '展示更多 “{q}” 的搜索结果',

           'about_name': '第二大脑 · 本地搜索', 'about_ok': '知道了',

           'about_note': '当前为 <b>0.x 预览版</b>：功能还在打磨，未达正式标准。<br>版本号到达 <b>1.0</b> 即视为正式版。', 'ctx_open': '打开', 'ctx_folder': '打开所在文件夹', 'ctx_copy': '复制路径', 'ctx_props': '属性', },

    'en': {'ph': 'Search file names, folders or document text', 'detail': 'Detailed search (Ctrl+Enter)', 'search': 'Search',

           'recent': 'Recently opened', 'recent_hint': 'Enter to open', 'fr': 'Ctrl+Enter detailed search',

           'sep': '{n} more matches inside document text', 'empty': 'Type a file name or a word from a document',

           'busy': 'Searching inside documents…', 'c_hint': 'c: text only',

           'none': 'Nothing found for “{q}” in names or text',

           'empty_hint': 'Double Ctrl to summon · Esc to close', 'more': 'Show more results for “{q}”',

           'about_name': 'Second Brain · local search', 'about_ok': 'Got it',

           'about_note': 'This is a <b>0.x preview</b>: still being polished.<br>It becomes official at <b>1.0</b>.', 'ctx_open': 'Open', 'ctx_folder': 'Open containing folder', 'ctx_copy': 'Copy path', 'ctx_props': 'Properties', },

    'es': {'ph': 'Busca nombres de archivo, carpetas o texto', 'detail': 'Búsqueda detallada (Ctrl+Enter)', 'search': 'Buscar',

           'recent': 'Abiertos hace poco', 'recent_hint': 'Enter para abrir', 'fr': 'Ctrl+Enter búsqueda detallada',

           'sep': '{n} coincidencias más dentro del texto', 'empty': 'Escribe un nombre de archivo o una palabra del texto',

           'busy': 'Buscando dentro de los documentos…', 'c_hint': 'c: solo texto',

           'none': 'No se encontró “{q}” en nombres ni texto',

           'empty_hint': 'Doble Ctrl para abrir · Esc para cerrar', 'more': 'Ver más resultados de “{q}”',

           'about_name': 'Second Brain · búsqueda local', 'about_ok': 'Entendido',

           'about_note': 'Es una <b>vista previa 0.x</b>: aún en ajuste.<br>Pasa a oficial en la <b>1.0</b>.', 'ctx_open': 'Abrir', 'ctx_folder': 'Abrir carpeta contenedora', 'ctx_copy': 'Copiar ruta', 'ctx_props': 'Propiedades', },

    'pt': {'ph': 'Busque nomes de arquivo, pastas ou texto', 'detail': 'Busca detalhada (Ctrl+Enter)', 'search': 'Buscar',

           'recent': 'Abertos recentemente', 'recent_hint': 'Enter para abrir', 'fr': 'Ctrl+Enter busca detalhada',

           'sep': 'mais {n} resultados no texto do documento', 'empty': 'Digite um nome de arquivo ou uma palavra do texto',

           'busy': 'Buscando dentro dos documentos…', 'c_hint': 'c: só texto',

           'none': 'Nada encontrado para “{q}” em nomes ou texto',

           'empty_hint': 'Ctrl duas vezes para abrir · Esc para fechar', 'more': 'Ver mais resultados de “{q}”',

           'about_name': 'Second Brain · busca local', 'about_ok': 'Entendi',

           'about_note': 'É uma <b>prévia 0.x</b>: ainda em ajuste.<br>Vira oficial na <b>1.0</b>.', 'ctx_open': 'Abrir', 'ctx_folder': 'Abrir pasta do arquivo', 'ctx_copy': 'Copiar caminho', 'ctx_props': 'Propriedades', },

    'hi': {'ph': 'फ़ाइल नाम, फ़ोल्डर या दस्तावेज़ टेक्स्ट खोजें', 'detail': 'विस्तृत खोज (Ctrl+Enter)', 'search': 'खोज',

           'recent': 'हाल में खोले गए', 'recent_hint': 'खोलने के लिए Enter', 'fr': 'Ctrl+Enter विस्तृत खोज',

           'sep': 'दस्तावेज़ टेक्स्ट में {n} और मिलान', 'empty': 'फ़ाइल नाम या दस्तावेज़ का शब्द लिखें',

           'busy': 'दस्तावेज़ों में खोजा जा रहा है…', 'c_hint': 'c: केवल टेक्स्ट',

           'none': '“{q}” नाम या टेक्स्ट में नहीं मिला',

           'empty_hint': 'Ctrl दो बार खोलें · Esc बंद करें', 'more': '“{q}” के और परिणाम देखें',

           'about_name': 'Second Brain · स्थानीय खोज', 'about_ok': 'समझ गया',

           'about_note': 'यह <b>0.x पूर्वावलोकन</b> है: अभी सुधार जारी है।<br><b>1.0</b> पर आधिकारिक होगा।', 'ctx_open': 'खोलें', 'ctx_folder': 'फ़ोल्डर खोलें', 'ctx_copy': 'पथ कॉपी करें', 'ctx_props': 'गुण', },

}

for _k, _v in MINI_L_EXTRA.items():

    MINI_L.setdefault(_k, {}).update(_v)





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

.row{display:flex;align-items:center;gap:11px;height:52px;padding:0 14px 0 12px;

  border-radius:9px;cursor:default;user-select:none;position:relative}

#list.rec .row{height:48px}

.row.on{background:#f1f3f4}

.ico{flex:0 0 auto;display:flex;align-items:center;justify-content:center;width:26px;height:26px}

.ico .ic{display:block;width:26px;height:26px}

.ico .fic{display:none;width:17px;height:17px}

.ico.noic .ic{display:none}

.ico.noic .fic{display:block}

.txt{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}

.nm{font-size:16.5px;line-height:1.22;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;

  color:#1a73e8}

.nm b{font-weight:600;color:#1a73e8;background:#fff3b8;border-radius:2px}

.tag{font-style:normal;font-size:10.5px;line-height:1;color:#8b7ad6;background:#f1eefc;

  border-radius:4px;padding:2px 5px;margin-left:7px;vertical-align:1.5px;letter-spacing:.02em}

.sep{font-size:11px;color:#8b93a6;padding:7px 12px 3px 40px;border-top:1px solid #eef1f7;margin-top:5px}

.sn{font-size:12px;line-height:1.25;color:#9aa3b2;white-space:nowrap;overflow:hidden;

  text-overflow:ellipsis}

.sn mark{background:#fff2b8;border-radius:2px;padding:0 1px;color:#5a4a12;font-weight:500}

.pt,.pt b{font-weight:400;font-size:13px;line-height:1.2;color:#9aa0a6;

  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}

.rh{display:flex;align-items:center;height:26px;padding:0 14px 0 13px;font-size:11px;

  letter-spacing:.06em;color:#b3bac7;flex:0 0 auto}

.rh span{margin-left:auto;letter-spacing:0;color:#c6ccd7}

.kbd{flex:0 0 auto;font-size:12px;color:#c0c6d1;font-variant-numeric:tabular-nums}

.row.on .kbd{color:#8b93a5}

.foot{display:flex;align-items:center;justify-content:space-between;height:36px;

  margin:0 8px;padding:0 12px;border-top:1px solid #f1f2f6;font-size:12.5px;color:#7b8398;

  gap:10px;flex:0 0 auto}

.foot.hide{display:none}

/* 「c: 只搜正文」的顺带提示：比正文更淡，不抢「展示更多」的主次 */

.chint{font-size:11.5px;color:#bcc3d0;white-space:nowrap}

.foot.clickable{cursor:pointer}

.foot.clickable:hover .fl{color:#0d1424}

.foot.clickable:hover .fr{color:#3b465c}

.rh.hide{display:none}

.foot .fl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center;gap:8px}

.foot .mic{flex:0 0 auto;width:20px;height:20px;border-radius:5px;background:#1a73e8;position:relative}

.foot .mic:after{content:"";position:absolute;left:5px;top:5px;width:8px;height:8px;

  border-top:2px solid #fff;border-right:2px solid #fff;transform:rotate(45deg)}

.foot.clickable .fl{color:#1a73e8}

.foot .fr{flex:0 0 auto;color:#a7aebc;font-size:11.5px}

.foot b{font-weight:600;color:#3b465c}

.empty{height:64px;display:flex;align-items:center;padding:0 24px;font-size:13px;color:#9aa3b2}

.empty.hide{display:none}

.empty i{font-style:normal;margin-left:auto;color:#c2c8d4;font-size:11.5px}

.fright{display:flex;align-items:center;gap:10px}

.mver{font-size:11.5px;color:#a7aebc;cursor:pointer;font-variant-numeric:tabular-nums}

.mver:hover{color:#3b465c}

.amask{position:fixed;inset:0;background:rgba(15,22,40,.4);z-index:50;display:none}
/* 右键菜单（第 123 项）：快搜窗结果行右键 → 打开/所在文件夹/复制路径/属性 */
#cmenu{position:fixed;z-index:60;display:none;min-width:150px;background:#fff;
  border:1px solid #e8eaf3;border-radius:10px;box-shadow:0 8px 24px rgba(15,22,40,.16);
  padding:4px;overflow:hidden}
#cmenu.on{display:block}
#cmenu .ci{display:block;width:100%;text-align:left;padding:7px 12px;border:0;background:none;
  font-size:12.5px;color:#2b2f3a;cursor:pointer;border-radius:7px}
#cmenu .ci:hover{background:#eef1fb;color:#3b5bfd}
.dark #cmenu{background:#23262f;border-color:#3a3f4d}
.dark #cmenu .ci{color:#dfe3ea}
.dark #cmenu .ci:hover{background:#31353f;color:#aebdfc}

.amask.on{display:block}

.about{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.95);z-index:51;

  width:300px;max-width:86vw;background:#fff;border:1px solid #e8eaf3;border-radius:16px;

  padding:26px 22px 20px;text-align:center;display:none;box-shadow:0 20px 60px rgba(20,30,60,.28)}

.about.on{display:block;animation:apop .14s ease}

@keyframes apop{from{opacity:0;transform:translate(-50%,-50%) scale(.92)}to{opacity:1;transform:translate(-50%,-50%) scale(1)}}

.about .alogo{font-size:34px;line-height:1}

.about .aname{font-size:15px;font-weight:700;margin-top:8px;color:#1a2233}

.about .aver{font-size:30px;font-weight:800;color:#4f6ef7;margin:10px 0 14px;letter-spacing:.02em}

.about .ameta{font-size:12.5px;color:#6b7280;line-height:1.95}

.about .ameta b{color:#1a2233;font-weight:600;font-variant-numeric:tabular-nums}

.about .anote{font-size:11.5px;color:#8089a0;margin:14px 0 18px;line-height:1.65;

  background:#f6f7fb;border-radius:9px;padding:10px 12px}

.about .pbtn{width:100%}



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

    <span class="fright"><span class="mver" onclick="showAbout()" title="关于 / About">__VER__</span>

    <span class="fr" id="fr">Ctrl+Enter 详细搜索</span></span></div>

  <div class="empty hide" id="empty">输入文件名或正文关键词，边打边出结果<i>双击 Ctrl 唤出 · Esc 关闭</i></div>

</div>

<div class="amask" id="amask" onclick="hideAbout()"></div>
<div id="cmenu">
  <button class="ci" id="cmOpen" data-i18n="ctx_open" onclick="ctxAct('open')"></button>
  <button class="ci" id="cmFolder" data-i18n="ctx_folder" onclick="ctxAct('folder')"></button>
  <button class="ci" id="cmCopy" data-i18n="ctx_copy" onclick="ctxAct('copy')"></button>
  <button class="ci" id="cmProps" data-i18n="ctx_props" onclick="ctxAct('props')"></button>
</div>

<div class="about" id="about">

  <div class="alogo">🧠</div>

  <div class="aname" data-i18n="about_name">第二大脑 · 本地搜索</div>

  <div class="aver">__VER__</div>

  <div class="ameta" id="ab_meta_s" data-nf="__FILES__" data-nt="__TOTAL__">

    <div id="ab_docs">正文可搜 <b>__TOTAL__</b> 篇 · 文件名可搜 <b>__FILES__</b> 个</div>

    <div>Python <b>__PY__</b> · SQLite <b>__SQL__</b></div>

    <div id="ab_build">构建日期 <b>__BUILD__</b></div>

  </div>

  <div class="anote" id="ab_note" data-i18n="about_note">当前为 <b>0.x 预览版</b>：功能还在打磨，未达正式标准。<br>版本号到达 <b>1.0</b> 即视为正式版。</div>

  <button class="pbtn gray" onclick="hideAbout()" data-i18n="about_ok">知道了</button>

</div>

<script>

var MINI_L = __MINI_L__, MINI_LANG = '__MINI_LANG__';

function MT(k, a){

  var d = MINI_L[MINI_LANG] || MINI_L.zh || {}, v = d[k];

  if (v == null) v = (MINI_L.en && MINI_L.en[k] != null) ? MINI_L.en[k] : ((MINI_L.zh || {})[k] || '');

  if (a) v = String(v).replace(/\{(\w+)\}/g, function(_, key){ return a[key] == null ? '' : a[key] });

  return v;

}

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

function applyMiniLang(){

  try{

    var q = Q || document.getElementById('q');

    q.placeholder = MT('ph');

    var bd = document.getElementById('btnDetail'), bs = document.getElementById('btnSearch');

    if(bd) bd.title = MT('detail');

    if(bs) bs.title = MT('search');

    var rh = document.getElementById('rh');

    if(rh) rh.innerHTML = esc(MT('recent')) + '<span>' + esc(MT('recent_hint')) + '</span>';

    var fr = document.getElementById('fr');

    if(fr) fr.textContent = MT('fr');

    var em = document.getElementById('empty');

    if(em) em.innerHTML = esc(MT('empty')) + '<i>' + esc(MT('empty_hint')) + '</i>';

    var an = document.querySelector('.about .aname'); if(an) an.textContent = MT('about_name');

    var note = document.getElementById('ab_note'); if(note) note.innerHTML = MT('about_note');

    var ok = document.querySelector('.about .pbtn'); if(ok) ok.textContent = MT('about_ok');

    var mt = document.querySelector('#empty2'); if(mt) mt.textContent = MT('empty');

  }catch(e){}

}

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

  /* 应用类按真实路径取图标：shell 解析快捷方式到目标，微信显示绿气泡而不是通用 exe 图标 */

  if(!dir && (it.e==='.exe'||it.e==='.lnk') && it.p) q += '&p='+encodeURIComponent(it.p);

  return '<img class="ic" src="/api/icon?'+q+'" alt="" '

    +'onerror="this.parentNode.classList.add(&quot;noic&quot;)">'+fb;

}

/* 路径分级：Listary 7.1 起把文件夹路径拆成一级一级，每级独立好读，

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

  /* 不再用「62+n*52+36」这种硬编码账本：改字号/行高/提示条都会让它对不上，

     少算几十像素就把最后一行切掉。直接量已经渲染出来的各段高度。 */

  var acc=0, el;

  el=document.querySelector('.bar'); if(el) acc+=el.offsetHeight;

  el=document.getElementById('rh'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('list'); if(el) acc+=el.offsetHeight;

  el=document.getElementById('empty'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('foot'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  return Math.max(acc, 120);

}

function resize(){

  var h=height();

  if(h===shown)return;

  if(!API||!API.resize){ shown=h; return; }

  /* 宿主在窗口藏着的时候会拒绝改尺寸（改了就弹出来），返回值 false 时不能记住 h，

     否则「打开时算过一次、窗口还没显示」就把错误高度钉死，下次打开最后一行被切掉。 */

  try{

    API.resize(h).then(function(ok){ if(ok!==false) shown=h; }).catch(function(){});

  }catch(e){ shown=h; }

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

  /* 程序行（微信/QQ/设置…）不显示路径：

     普通人看到 C:\Program Files\Tencent\Weixin 只会愣一下——他要的是「打开微信」。

     路径留在右键/主窗口详情里，需要的时候能看到。 */

  var sub = it.b

    ? '<span class="sn">'+snipHtml(it.b)+'</span>'

    : (it.app ? '' : '<span class="pt">'+esc(it.d||'')+'</span>');

  return '<div class="row'+(i===cur?' on':'')+'" data-i="'+i+'">'

    +'<span class="ico">'+ico(it)+'</span>'

    +'<span class="txt"><span class="nm">'+(q?hl(it.dn||it.n,q):esc(it.dn||it.n))

      +(it.b?'<i class="tag">'+esc(MT('tag_body'))+'</i>':'')+'</span>'+sub+'</span>'

    +(withKey?'<span class="kbd">'+(i<9?'Ctrl+'+(i+1):'')+'</span>':'')+'</div>';

}

function render(items,q,keep){

  var old=cur;

  MODE='result';

  rows=items||[];

  cur=rows.length?(keep&&old>=0?Math.min(old,rows.length-1):0):-1;

  var html='', sepDone=false;

  for(var i=0;i<rows.length;i++){

    /* 正文命中排在最后（后端配额固定 3 格）。

       在交界处放一行小字，而不是一条粗线——用户要的是「知道下面这批为什么在」，

       不是再画一道分割。

       整屏都是正文时（用户敲了 c:xxx）不画：从头到尾都在正文里，这条线没有意义，

       而且它的文案「以下 N 条」会漏掉第一条，看着像少了一条。 */

    if(!sepDone && rows[i].b && i>0 && !rows[0].b){

      var rest=0; for(var j=i;j<rows.length;j++) if(rows[j].b) rest++;

      html+='<div class="sep">'+esc(MT('sep', {n: rest}))+'</div>';

      sepDone=true;

    }

    html+=rowHtml(rows[i],i,q,true);

  }

  LIST.className='';

  LIST.innerHTML=html;

  EMPTY.classList.add('hide');

  FOOT.classList.toggle('hide', !rows.length);

  if(!rows.length){

    /* 以前这里把表头、空态、页脚全藏了，窗口只剩一个 120px 的空框：

       用户看到的是「它坏了」，而不是「没找到」。留一行字。 */

    RH.classList.add('hide');

    /* MINI_TPL 里没有 T()/F()（那是主窗口的词典函数），以前这里调 F(T(...))

       必然抛 ReferenceError，又被外层 .catch 吞掉 —— 表现是「搜不到就一片白」。 */

    LIST.innerHTML='<div class="empty">'+esc(MT('none', {q: q}))

      +'<i>'+esc(MT('fr'))+'</i></div>';

    bind(); resize(); return;

  }

  /* 结果态必须把它藏起来：这个表头写的是「最近打开 / Enter 直接打开」，

     挂在搜索结果上面等于告诉用户「下面这些是你最近开的」——纯误导。

     只有 renderRecent() 才该让它在场。 */

  RH.classList.add('hide');

  if(rows.length){

    /* 一条正文都没出、而第一名又是程序时，用户多半会怀疑「正文是不是没搜」。

       这不是错觉：搜「微信」正文确实一条都不占位（他要的是打开微信本体）。

       顺手把 c: 语法写在这儿——不占额外空间，也不用去翻文档。 */

    var nb=0;

    for(var j2=0;j2<rows.length;j2++) if(rows[j2].b) nb++;

    var hint=(!nb && rows[0] && rows[0].app) ? '  ·  '+MT('c_hint') : '';

    FT.innerHTML='<span class=mic></span><span>'+esc(MT('more', {q: q}))+'</span>'
      +(hint?'<span class="chint">'+esc(hint)+'</span>':'');

    FR.textContent=MT('fr');

    FOOT.classList.add('clickable');

    FOOT.onclick=detail;

  }else{

    FOOT.classList.remove('clickable');

    FOOT.onclick=null;

  }

  bind(); resize();

}

/* 空状态：打开就先给一屏「刚动过的文件」。Listary V7 起就是这个做法——

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

  if(rows.length){ FT.textContent=MT('foot_typing'); FR.textContent=MT('foot_esc'); }

  FOOT.classList.remove('clickable');

  FOOT.onclick=null;

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
    els[i].oncontextmenu=function(ev){ ev.preventDefault(); showCMenu(ev.clientX, ev.clientY, +this.dataset.i); };

  }

}

function sel(i){

  if(i<0||i>=rows.length)return;

  cur=i;

  var els=LIST.querySelectorAll('.row');

  for(var k=0;k<els.length;k++) els[k].classList.toggle('on', k===i);

}

var CMENU=null, CMI=-1;
function showCMenu(x,y,i){
  CMI=i;
  CMENU=document.getElementById('cmenu');
  /* 文案用 MT() 手动填（mini 模板没有 data-i18n 自动填充机制）；
     每次弹出都填，语言参数变化也跟随 */
  var items=[['cmOpen','ctx_open'],['cmFolder','ctx_folder'],['cmCopy','ctx_copy'],['cmProps','ctx_props']];
  for(var k=0;k<items.length;k++){
    var el=document.getElementById(items[k][0]);
    if(el) el.textContent=MT(items[k][1]);
  }
  CMENU.classList.add('on');
  var mw=CMENU.offsetWidth||160, mh=CMENU.offsetHeight||130;
  x=Math.min(x, window.innerWidth-mw-6); y=Math.min(y, window.innerHeight-mh-6);
  CMENU.style.left=x+'px'; CMENU.style.top=y+'px';
}
function hideCMenu(){ if(CMENU){CMENU.classList.remove('on');} CMI=-1; }
function ctxAct(act){
  var it=rows[CMI]; hideCMenu(); if(!it||!it.p)return;
  try{
    if(act==='open'){ if(API&&API.open_path)API.open_path(it.p); }
    else if(act==='folder'){ if(API&&API.reveal)API.reveal(it.p); }
    else if(act==='copy'){ if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(it.p); }
    else if(act==='props'){ if(API&&API.properties)API.properties(it.p); }
    else { console.warn('ctxAct unknown act:', act); return; }
  }catch(e){ console.warn('ctxAct error:', act, e); }
}

function open(i){

  var it=rows[i]; if(!it)return;

  if(API&&API.open_path)API.open_path(it.p);

}

function detail(){

  var v=Q.value.trim();

  if(API&&API.detail){

    /* 桌面窗口：交给原生桥去 show 自带的主窗口。这里必须 catch：

       pywebview 的 js_api 返回 Promise，宿主一出异常就会 unhandled rejection，

       页面上看不到任何反馈，用户会以为「点了没反应」。 */

    try{

      var r=API.detail(v);

      if(r&&r.catch)r.catch(function(e){ console.warn('detail failed',e); });

    }catch(e){ console.warn('detail failed',e); }

    return;

  }

  /* 没有原生桥 = 这个 /mini 是被人在浏览器里直接打开的（调试/截图场景）。

     只在【当前标签页】跳到完整界面，别新开浏览器窗口。 */

  if(v)location.href='/web/?q='+encodeURIComponent(v);

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

      render(d.items||[], d.q||v);

      /* 停手 300ms 后再补一趟。名字不满一屏时连名字一起全表扫（deep=1），

         满一屏时只补正文（deep=0）—— deep 会重排名字，结果在用户眼皮底下

         换一批，比「少了 3 条正文」难受得多。

         以前这里带 length<9 的前提，于是名字满一屏时连请求都不发：

         搜「报告」这类常见词，正文命中**永远不会出现**。 */

      var n=(d.items||[]).length;

      if(v.length>=2){

        clearTimeout(deeptimer);

        deeptimer=setTimeout(function(){

          if(my!==seq)return;

          doDeep(v, n<9);

        },300);

      }

    }).catch(function(){});

}

/* 已经等了一会儿还没结果：告诉用户「在搜正文」，别让他以为搜不到。

   正文检索要走 docs 的 FTS5 索引，短词还会降级成全表扫（0.3~1.5s），

   这段时间界面什么都不变的话，用户按 Esc 就走了。 */

function busy(q){

  if(rows.length)return;              /* 已经有名字结果就别动它，否则会闪一下 */

  LIST.className='';

  LIST.innerHTML='<div class="empty">'+esc(MT('busy'))+'<i>'+esc(q)+'</i></div>';

  EMPTY.classList.add('hide');

  FOOT.classList.add('hide');

  resize();

}

function doDeep(v,needDeep){

  var my=++seq;

  busy(v);

  var u='/api/mini?q='+encodeURIComponent(v)+'&body=1'+(needDeep?'&deep=1':'');

  fetch(u).then(function(r){return r.json()})

    .then(function(d){

      if(my!==seq)return;

      if(!(d.items||[]).length){

        /* 深搜也没东西：把名字结果留下来（可能刚才 busy() 清掉了空态），

           但这次请求为空说明确实没有，走 render 的空态分支。 */

        if(!rows.length) render([], d.q||v);

        return;

      }

      render(d.items, d.q||v, true);

    }).catch(function(){

      /* 正文检索超时/断连时别把「正在搜正文…」留在屏幕上：

         那个状态没有超时机制，会一直挂着，比没结果更让人困惑。 */

      if(!rows.length) render([], v);

    });

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

  /* 输入法拼字中的按键全部放行：否则用拼音打「wx」按回车选词，会直接打开第一个结果 */

  if(e.isComposing||e.keyCode===229)return;

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
document.addEventListener('click', function(e){
  if(CMENU && !CMENU.contains(e.target)) hideCMenu();
});
document.addEventListener('contextmenu', function(e){
  if(CMENU && !CMENU.contains(e.target) && !(e.target.closest && e.target.closest('.row'))) hideCMenu();
});
document.addEventListener('keydown', function(e){ if(e.key==='Escape') hideCMenu(); });

document.getElementById('btnSearch').onclick=function(){

  var v=Q.value.trim(); if(v) doPrefix(v); else Q.focus();

};

window.addEventListener('pywebviewready',function(){ API=window.pywebview.api; });

/* ?q=xxx 预填：截图、调试、演示都要用，正常打开没有这个参数。

   放在脚本顶层读一次，DOMContentLoaded 和 __onShow 都用它 —— __onShow 里

   本来会把输入框清空（重新唤出该是干净的），带 q 的参数说明调用方想预填。 */

var PRESET_Q='';

try{ PRESET_Q=(new URLSearchParams(location.search)).get('q')||''; }catch(e){}

document.addEventListener('DOMContentLoaded',function(){

  if(window.pywebview&&window.pywebview.api)API=window.pywebview.api;

  applyMiniLang();

  Q.focus();

  fetchRecent();

  if(PRESET_Q){ Q.value=PRESET_Q; schedule(); }

});

window.addEventListener('focus',function(){

  setTimeout(function(){Q.focus();},20);

});

/* 设置里改语言时，快搜窗可能正开着：每 4 秒问一次后端，变了就带 ?lang= 重载 */

function checkLang(){

  try{

    if(document.hidden)return;

    /* 地址栏里已经明确带了 ?lang= 的时候，说明是外面（托盘/测试）指定的语言，

       别再拿服务端的语言去覆盖它——否则 ?lang=es 一进来就被改成 en。 */

    if(/[?&]lang=/.test(location.search))return;

    fetch('/api/ping').then(function(r){return r.json()}).then(function(v){

      if(v && v.lang && v.lang !== MINI_LANG){ location.search = '?lang=' + v.lang }

    }).catch(function(){});

  }catch(e){}

}

setInterval(checkLang, 4000);

window.__onShow=function(){

  applyMiniLang();

  /* 语言在设置里改过之后，快搜窗要跟着换：问一下后端当前的界面语言，

     变了就带 ?lang= 重载一次（重载很快，窗口大小保持不变） */

  try{

    fetch('/api/ping').then(function(r){return r.json()}).then(function(v){

      if(v && v.lang && v.lang !== MINI_LANG){ location.search = '?lang=' + v.lang }

    }).catch(function(){});

  }catch(e){}

  Q.value=PRESET_Q; cur=-1; seq++;

  renderRecent(RECENT);        /* 先用上次那一屏，秒出；下面再后台刷新 */

  if(PRESET_Q){ setTimeout(function(){ schedule(); },0); }

  /* 窗口刚 Show 出来那一下，宿主还没把 mini_visible 置真，这时的 resize 会被拒。

     过一小会儿补两次，等窗口真的在前台了再把高度对上。 */

  shown=-1;

  setTimeout(function(){ shown=-1; resize(); },80);

  setTimeout(function(){ shown=-1; resize(); },260);

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

function showAbout(){document.getElementById('about').classList.add('on');document.getElementById('amask').classList.add('on')}

function hideAbout(){document.getElementById('about').classList.remove('on');document.getElementById('amask').classList.remove('on')}

</script></body></html>'''





_LOG_MAX = 512 * 1024





def _log_file(msg):

    """把异常写进程序目录的 debug.log（截断到 512KB，滚动一个 .1）。"""

    try:

        p = app_dir() / 'debug.log'

        try:

            if p.exists() and p.stat().st_size > _LOG_MAX:

                p.replace(p.with_suffix('.log.1'))

        except Exception:

            pass

        with open(p, 'a', encoding='utf-8') as f:

            f.write('[%s] %s%s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg, chr(10)))

    except Exception:

        pass





def _sb_err(handler):

    """请求处理时抛异常：别让连接直接断掉（浏览器只会显示「未发送任何数据」），

    给一个能看懂的错误页，并把 traceback 写进 debug.log。"""

    import traceback

    tb = traceback.format_exc()

    _log_file('REQUEST FAILED %s%s%s' % (getattr(handler, 'path', '?'), chr(10), tb))

    try:

        tail = tb.strip().splitlines()[-1]

        html = ('<!doctype html><meta charset="utf-8"><title>第二大脑 · 出错了</title>'

                '<body style="font:14px/1.7 system-ui,Segoe UI,Microsoft YaHei;padding:40px;'

                'max-width:760px;margin:0 auto;color:#1a2233">'

                '<h2 style="color:#c0392b">这一页没能显示出来</h2>'

                '<p>本地服务在处理请求时出错了。窗口还能用，但这一页是坏的。</p>'

                '<pre style="background:#f6f7fb;padding:12px;border-radius:10px;'

                'white-space:pre-wrap">%s</pre>'

                '<p>完整日志：<code>%s</code></p>'

                '<p>常见原因：程序目录没有写权限（比如解压到了只读位置、或者被安全软件锁了）。'

                '把整个文件夹挪到桌面或文档下面再试一次。</p>'

                '</body>') % (tail.replace('<', '&lt;'), app_dir() / 'debug.log')

        body = html.encode('utf-8')

        handler.send_response(500)

        handler.send_header('Content-Type', 'text/html; charset=utf-8')

        handler.send_header('Content-Length', str(len(body)))

        handler.end_headers()

        handler.wfile.write(body)

    except Exception:

        pass





def web_ui_dir():

    """React 前端的产物目录。开发时在 <项目>/web/dist，打包后在 _internal/web。



    找不到就返回 None —— 此时 /web/ 走 404，旧页面照常工作，不会因为前端没构建就打不开。"""

    cands = []

    # 顺序要紧：开发时 web/ 里放的是 Vite 源码（index.html 引 /src/main.tsx），

    # 真正能跑的是 web/dist。先找 dist，否则会把源码目录当成品发出去，页面一片空白。

    try:

        cands.append(Path(app_dir()) / 'web' / 'dist')

        cands.append(Path(app_dir()) / 'web')

    except Exception:

        pass

    if getattr(sys, 'frozen', False):

        # 打包后由 PyInstaller 放进 _internal/web（build 时把 web/dist 整个拷进去）

        cands.insert(0, Path(sys.executable).parent / '_internal' / 'web')

    cands.append(Path(__file__).resolve().parent / 'web' / 'dist')

    for d in cands:

        try:

            if (d / 'index.html').is_file():

                return d

        except Exception:

            continue

    return None





def make_server(port=8765):

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    PAGE_TPL = r'''<!doctype html><html lang="zh-CN"><head><meta charset=utf-8>

<meta name=viewport content="width=device-width,initial-scale=1">

<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='22' fill='%236b53e8'/%3E%3Ctext y='.92em' x='8' font-size='82'%3E%F0%9F%A7%A0%3C/text%3E%3C/svg%3E">

<title>第二大脑 · 本地文档搜索</title>

<style>

:root{--bg:#f6f7fb;--card:#fff;--ink:#1a2233;--sub:#6b7280;--acc:#4f6ef7;--mark:#fde68a;--line:#e8eaf3;--sel:#eef1fd;--side:#8089a0}

[data-theme="dark"]{--bg:#1a1d24;--card:#22262e;--ink:#e4e7ee;--sub:#9aa3b2;--acc:#6b8aff;--mark:#5a4a1a;--line:#2e333d;--sel:#2a3050;--side:#9aa4b6}

*{box-sizing:border-box;margin:0}

body{font-family:"Segoe UI",system-ui,-apple-system,"Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);min-height:100vh;display:flex;flex-direction:column;height:100vh;overflow:hidden}

.topbar{display:flex;gap:8px;align-items:center;padding:10px 16px;background:var(--card);border-bottom:1px solid var(--line);flex-shrink:0}

.topbar .logo{font-size:0;display:flex;align-items:center;margin-right:4px;flex-shrink:0}

.topbar input{border:1px solid var(--line);background:var(--bg);border-radius:8px;padding:8px 12px;font-size:14px;outline:none;transition:.15s;font-family:inherit}

.topbar input:focus{border-color:var(--acc);box-shadow:0 0 0 2px rgba(79,110,247,.12)}

#q{flex:1;min-width:120px}#t{width:90px}#d{width:120px}

.topbar .sbtn{border:none;border-radius:8px;padding:8px 18px;font-size:14px;font-weight:500;color:#fff;background:var(--acc);cursor:pointer;font-family:inherit;white-space:nowrap}

.topbar .sbtn:hover{background:#3d5bd9}

.topbar .gear{border:1px solid var(--line);background:var(--card);border-radius:8px;width:34px;height:34px;font-size:15px;color:var(--sub);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s;flex-shrink:0}

.topbar .gear:hover{color:var(--acc);border-color:var(--acc)}

.filterbar{display:flex;gap:6px;padding:6px 16px;background:var(--card);border-bottom:1px solid var(--line);flex-shrink:0;overflow-x:auto}

.filterbar:empty{display:none}

.fchip{font-size:12px;padding:3px 10px;border-radius:6px;background:var(--bg);color:var(--sub);cursor:pointer;white-space:nowrap;transition:.12s;border:1px solid transparent}

.fchip:hover{color:var(--ink)}

.fchip.on{background:var(--sel);color:var(--acc);border-color:rgba(79,110,247,.2)}

.fchip b{font-weight:600;margin-left:2px}

.main{flex:1;display:flex;overflow:hidden;min-height:0}

.frbar{display:flex;align-items:center;gap:10px;padding:8px 14px;font-size:12.5px;

  color:var(--tx2);background:linear-gradient(90deg,#eef2ff,#f7f9ff);border-bottom:1px solid var(--ln)}

.frbar.hide{display:none}

.frbar b{color:var(--tx);font-variant-numeric:tabular-nums}

.frbar #frmsg{flex:1 1 auto;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}

.frbar .frbtn{flex:0 0 auto;white-space:nowrap}

.frbar .frp{flex:1;height:3px;border-radius:2px;background:#dfe5f5;overflow:hidden;max-width:260px}

.frbar .frp i{display:block;height:100%;width:0;background:var(--acc);transition:width .4s}

.frbar .frbtn{border:1px solid var(--ln);background:#fff;color:var(--tx2);border-radius:6px;

  padding:2px 9px;font-size:12px;cursor:pointer}

.frbar .frbtn:hover{border-color:var(--acc);color:var(--acc)}

.frbar.ok{background:linear-gradient(90deg,#eafaf1,#f6fdf9);border-bottom-color:#bfe8d2;color:#1a7a4a}

.side{flex:0 0 158px;border-right:1px solid var(--line);background:var(--card);overflow-y:auto;padding:6px 6px 10px;display:flex;flex-direction:column;gap:0}

.side .sh{font-size:11.5px;color:var(--side);padding:7px 8px 4px;display:flex;align-items:center;justify-content:space-between;font-weight:500}

.sitem{display:flex;align-items:center;gap:7px;padding:4.5px 9px;border-radius:6px;font-size:12.5px;color:var(--sub);cursor:pointer;transition:.1s;border:1px solid transparent}

.sitem:hover{background:var(--bg);color:var(--ink)}

.sitem.on{background:var(--sel);color:var(--acc);border-color:rgba(79,110,247,.18);font-weight:500}

.sitem .si{width:16px;text-align:center;font-size:13px;opacity:.9}

.spath{display:flex;align-items:center;gap:4px;padding:1px 4px 4px 6px}

.spath input{flex:1;min-width:0;height:26px;border:1px solid var(--line);border-radius:7px;

  background:var(--bg);color:var(--ink);font-size:12px;padding:0 8px;outline:0;font-family:inherit}

.spath input:focus{border-color:#b9c6ff}

.dpclr{font-weight:400;font-size:11px;color:#b6bcc9;cursor:pointer;padding:0 2px;visibility:hidden}

.dpclr:hover{color:#4f6ef7}

.sitem b{margin-left:auto;font-weight:500;font-size:11.5px;color:var(--side)}

.sitem.on b{color:var(--acc)}

.tablewrap{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;overflow-x:auto}

.thead{display:grid;grid-template-columns:26px minmax(150px,1.05fr) minmax(170px,1.55fr) 116px;gap:10px;align-items:center;padding:7px 12px;font-size:11.5px;color:var(--side);border-bottom:1px solid var(--line);background:var(--card);flex-shrink:0;font-weight:500}

.rlist{flex:1;overflow-y:auto;padding:0}

.ritem{display:grid;grid-template-columns:26px minmax(150px,1.05fr) minmax(170px,1.55fr) 116px;gap:10px;align-items:center;padding:6px 12px;border-radius:6px;cursor:pointer;transition:background .08s;border:1px solid transparent}

.ritem:hover{background:var(--bg)}

.ritem.on{background:var(--sel);border-color:rgba(79,110,247,.15)}

.ritem .ico{width:24px;height:24px;flex-shrink:0;border-radius:5px;object-fit:contain}

.ritem .ico .ic{display:block;width:22px;height:22px}

.ritem .ico .fic{display:none;width:22px;height:22px}

.ritem .ico.noic .ic{display:none}

.ritem .ico.noic .fic{display:block}

.ritem .nm{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}

.ritem .pp{font-size:12px;color:var(--side);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}

.ritem .mt{font-size:11.5px;color:var(--side);white-space:nowrap;text-align:right}

.ritem .mt.none{color:transparent}

.ritem .rmain{display:none}

.ritem .evtag{font-size:10px;color:#7c3aed;border:1px solid #dfd3fb;border-radius:4px;padding:1px 4px;flex-shrink:0}

.rsnip{font-size:12px;color:var(--sub);line-height:1.5;padding-left:34px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}

.ritem .sub{grid-column:2/4;font-size:11.5px;color:var(--sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:-2px}

.rsnip mark{background:var(--mark);border-radius:2px;padding:0 1px}

[data-theme="dark"] .ritem .badge{background:#2a3050;color:var(--acc)}

.ritem .evtag{font-size:10px;color:#7c3aed;border:1px solid #dfd3fb;border-radius:4px;padding:1px 4px;flex-shrink:0}

.evhead{font-size:11.5px;font-weight:600;color:var(--acc);padding:9px 12px 5px;display:flex;align-items:baseline;gap:6px;background:var(--bg);border-bottom:1px solid var(--line)}

.evhead em{font-weight:400;color:var(--side);font-size:11px}

.evmore{font-size:11.5px;color:var(--side);padding:6px 10px 4px}

.preview{flex:0 0 190px;min-width:0;border-left:1px solid var(--line);background:var(--card);overflow-y:auto;padding:12px 10px;display:none}

.preview.on{display:block}

@media(max-width:900px){.preview{display:none!important}}

@media(max-width:1020px){.preview.on{flex-basis:170px}}

.pv-title{font-size:13px;font-weight:600;margin-bottom:6px;word-break:break-word}

.pv-thumb{margin-bottom:10px;text-align:center;background:var(--bg);border-radius:8px;padding:6px;overflow:hidden}

.pv-thumb img{max-width:100%;max-height:190px;border-radius:4px;object-fit:contain}

.pv-meta{font-size:11px;color:var(--side);margin-bottom:10px;line-height:1.6;word-break:break-word}

.pv-meta b{color:var(--ink);font-weight:500}

.pv-snip{font-size:12px;line-height:1.7;color:#3a4356;background:var(--bg);border-radius:8px;padding:10px 10px;word-break:break-word;border:1px solid var(--line)}

.pv-actions{display:flex;flex-direction:column;gap:6px;margin-top:10px}

.pv-actions .btn{text-align:center}

.pv-empty{color:var(--side);font-size:12px;text-align:center;padding:24px 0;line-height:1.7}

.btn{border:1px solid var(--line);background:var(--card);color:var(--sub);border-radius:7px;padding:6px 12px;font-size:12px;cursor:pointer;transition:.15s;white-space:nowrap;font-family:inherit}

.btn:hover{color:var(--acc);border-color:var(--acc)}

.btn.primary{background:var(--acc);color:#fff;border-color:var(--acc)}

.btn.primary:hover{background:#3d5bd9}

.footer{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:6px 16px;background:var(--card);border-top:1px solid var(--line);font-size:11.5px;color:var(--side);flex-shrink:0;white-space:nowrap;overflow:hidden}

.footer>span{white-space:nowrap;min-width:0}

.footer .newui{margin-right:6px;color:var(--sub,#8089a0);text-decoration:none;border-bottom:1px dotted currentColor}

.footer .newui:hover{color:var(--acc,#4f6ef7)}

.footer .verbox{overflow:hidden;text-overflow:ellipsis}

@media (max-width:1280px){.footer .hkhint{display:none}}

.footer .hkhint{min-width:0;overflow:hidden;text-overflow:ellipsis}

.footer kbd{border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:11px;font-family:inherit;background:var(--bg)}

.footer .hkhint{color:var(--accent,#3b82f6);cursor:pointer}

.empty{text-align:center;color:var(--side);padding:80px 0;font-size:14px;line-height:2}

.empty b{color:var(--ink)}

/* 首页时间轴 */

.tlwrap{padding:6px 16px 24px}

.tlmore{text-align:center;color:#4f6ef7;font-size:12.5px;padding:16px 0 6px;cursor:pointer}

.tlmore:hover{text-decoration:underline}

.tlhead{display:flex;align-items:center;gap:8px;margin:16px 4px 6px;font-size:12px;font-weight:600;color:var(--side);letter-spacing:.5px}

.tln{font-weight:400;color:var(--side);opacity:.7}

.tlrow{display:grid;grid-template-columns:44px minmax(0,2fr) minmax(0,3fr) 96px;gap:10px;align-items:center;padding:7px 8px;border-radius:8px;cursor:default}

.tlrow:hover{background:var(--hov)}

.tlrow.on{background:var(--sel)}

.tlic{font-size:10px;font-weight:700;color:var(--acc);background:var(--accbg);border-radius:4px;text-align:center;padding:2px 0;overflow:hidden}

.tlnm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}

.tldir{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--side)}

.tlt{font-size:11px;color:var(--side);text-align:right}

/* 右键菜单 */

.ctxm{position:fixed;z-index:9999;background:var(--card);border:1px solid var(--line);border-radius:9px;box-shadow:0 8px 26px rgba(0,0,0,.16);padding:5px;min-width:170px}

.ctxi{padding:7px 10px;font-size:13px;border-radius:6px;cursor:pointer;color:var(--ink)}

.ctxi:hover{background:var(--hov)}

.vmenu{display:none;min-width:172px}

.qsyn{margin-top:10px;font-size:12px;color:#8a91a3;line-height:1.7}

.about.help{width:min(760px,94vw);max-width:94vw;max-height:86vh;overflow:auto;text-align:left;padding:20px 22px 16px}

.help .hhd{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}

.help .hht{font-size:16px;font-weight:700;color:#1a2233}

.help .wx{border:0;background:none;font-size:16px;color:#8a91a3;cursor:pointer;line-height:1}

.help .hcols{display:grid;grid-template-columns:1fr 1fr;gap:8px 26px}

.help .hsec{min-width:0}

.help .hsec:first-child{grid-row:span 2}

.help .hst{font-size:12.5px;font-weight:700;color:#4f6ef7;margin:6px 0 8px;letter-spacing:.02em}

.help .hrow{display:flex;gap:10px;align-items:baseline;padding:3px 0;font-size:12.5px;color:#4a5162;line-height:1.5}

.help .hk{flex:none;min-width:74px;font-family:ui-monospace,Consolas,monospace;font-size:11.5px;

  color:#1f2a44;background:#f1f3f9;border:1px solid #e3e7f2;border-radius:5px;padding:1px 6px;text-align:center}

.help .hfoot{margin-top:14px;font-size:11.5px;color:#8a91a3;text-align:center}

@media (max-width:640px){.help .hcols{grid-template-columns:1fr}}

.vmenu.on{display:block}

.vmenu .ctxi{display:flex;align-items:center;gap:8px}

.vmenu .vic{width:14px;color:#4f6ef7;font-weight:700;display:inline-block;text-align:center}

.vsep{height:1px;background:var(--line);margin:5px 6px}

/* 零命中建议 */

.dym{padding:0 24px 24px}

.dymh{font-size:12px;color:var(--side);margin-bottom:8px}

.dymc{display:inline-block;margin:0 6px 6px 0;padding:5px 11px;border-radius:14px;background:var(--accbg);color:var(--acc);font-size:12.5px;cursor:pointer}

/* 反查 / 同名版本 */

.revsec{margin-top:10px;font-size:12px;color:var(--side);font-weight:600}

.revrow{display:flex;gap:6px;flex-direction:column;padding:5px 6px;border-radius:6px;cursor:pointer;font-size:12px}

.revrow:hover{background:var(--hov)}

.revdir{color:var(--side);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.revt{color:var(--side);font-size:10.5px}

.pvsame{margin-top:12px;border-top:1px dashed var(--line);padding-top:8px}

.pvsame-h{font-size:11.5px;color:var(--side);margin-bottom:4px}

.pvsame-row{display:flex;justify-content:space-between;gap:6px;padding:3px 4px;border-radius:5px;cursor:pointer;font-size:12px}

.pvsame-row:hover{background:var(--hov)}

.pvsame-n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.pvsame-t{color:var(--side);font-size:10.5px;white-space:nowrap}

mark{background:var(--mark);border-radius:3px;padding:0 2px;font-weight:600}

.drawer{position:fixed;top:0;right:-360px;width:340px;height:100vh;background:var(--card);box-shadow:-4px 0 24px rgba(0,0,0,.08);z-index:20;transition:right .2s;overflow-y:auto;padding:20px 22px}

.drawer.on{right:0}

.drawer h3{font-size:15px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}

.drawer h3 b{cursor:pointer;color:var(--side);font-weight:400;font-size:18px}

.drawer h3 b:hover{color:var(--ink)}

.dsec{margin-bottom:20px}

.dsec-t{font-size:12px;font-weight:600;color:var(--side);margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}

.drow{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line)}

.drow:last-child{border-bottom:none}

.drow .dl{font-size:13px}

.drow .dl small{display:block;font-size:11px;color:var(--side);margin-top:2px}

.toggle{width:40px;height:22px;border-radius:11px;background:#d3d5e0;position:relative;cursor:pointer;transition:.2s;flex-shrink:0}

.toggle.on{background:var(--acc)}

.toggle i{position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;background:#fff;transition:.2s;box-shadow:0 1px 3px rgba(0,0,0,.2)}

.toggle.on i{left:20px}

.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}

.chip{display:flex;align-items:center;gap:6px;background:var(--sel);color:var(--acc);border-radius:7px;padding:5px 9px;font-size:12px;word-break:break-all;max-width:100%}

.chip b{cursor:pointer;font-weight:600;color:var(--side)}

.chip b:hover{color:#e05555}

.pbtn{border:none;border-radius:8px;padding:9px 18px;font-size:13px;font-weight:500;color:#fff;background:var(--acc);cursor:pointer;font-family:inherit}

.pbtn.hot{animation:hotpulse 1.7s ease-in-out infinite}

@keyframes hotpulse{0%,100%{box-shadow:0 0 0 0 rgba(63,125,255,.0)}50%{box-shadow:0 0 0 6px rgba(63,125,255,.18)}}

#pi.indet{width:100%!important;background:repeating-linear-gradient(90deg,var(--acc) 0 14px,rgba(63,125,255,.35) 14px 28px);background-size:28px 100%;animation:indetmove .9s linear infinite}

@keyframes indetmove{from{background-position:0 0}to{background-position:28px 0}}

.pbtn.gray{background:var(--bg);color:var(--ink)}

.pbtn:disabled{opacity:.5;cursor:default}

.prow{display:flex;gap:8px;flex-wrap:wrap}

.pmsg{font-size:13px;color:var(--side);margin-top:10px;line-height:1.7;min-height:18px}

.nstat{font-size:12px;color:var(--side);margin-top:8px;line-height:1.7}

.pbar{height:8px;background:var(--line);border-radius:6px;overflow:hidden;margin-top:10px}

.pbar i{display:block;height:100%;width:0;background:var(--acc);transition:width .3s}

.help{margin-top:12px;font-size:12.5px;color:var(--side)}

.help summary{cursor:pointer;color:var(--acc);font-weight:500;user-select:none}

.help summary:hover{text-decoration:underline}

.help li{margin:5px 0;line-height:1.75}

details.dsec{margin-bottom:20px}

details.dsec>summary::-webkit-details-marker{display:none}

details.dsec[open]>summary span:last-child::before{content:'▾ '}

details.dsec:not([open])>summary span:last-child::before{content:'▸ '}

.crow{display:flex;align-items:center;gap:8px;background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:6px 9px;margin-bottom:6px;font-size:12.5px;cursor:pointer}

.crow input{accent-color:var(--acc)}

.crow .n{color:var(--side);margin-left:auto;white-space:nowrap}

nz{display:inline-block;color:#b45309;font-size:11px;border:1px solid #fde68a;background:#fffbeb;border-radius:4px;padding:1px 5px;white-space:nowrap}

#toast{position:fixed;left:50%;bottom:48px;transform:translateX(-50%) translateY(20px);background:#1a2233;color:#fff;padding:10px 18px;border-radius:8px;font-size:13px;opacity:0;pointer-events:none;transition:.2s;z-index:30;max-width:80vw;text-align:center}

#toast.on{opacity:1;transform:translateX(-50%) translateY(0)}

.mask{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.06);z-index:15;display:none}

.mask.on{display:block}

.ver{cursor:pointer;color:var(--acc,#4f6ef7);font-weight:700}

.ver:hover{text-decoration:underline}

.verbox{display:inline-flex;align-items:center;gap:4px}

.amask{position:fixed;inset:0;background:rgba(15,22,40,.4);z-index:50;display:none}
/* 右键菜单（第 123 项）：快搜窗结果行右键 → 打开/所在文件夹/复制路径/属性 */
#cmenu{position:fixed;z-index:60;display:none;min-width:150px;background:#fff;
  border:1px solid #e8eaf3;border-radius:10px;box-shadow:0 8px 24px rgba(15,22,40,.16);
  padding:4px;overflow:hidden}
#cmenu.on{display:block}
#cmenu .ci{display:block;width:100%;text-align:left;padding:7px 12px;border:0;background:none;
  font-size:12.5px;color:#2b2f3a;cursor:pointer;border-radius:7px}
#cmenu .ci:hover{background:#eef1fb;color:#3b5bfd}
.dark #cmenu{background:#23262f;border-color:#3a3f4d}
.dark #cmenu .ci{color:#dfe3ea}
.dark #cmenu .ci:hover{background:#31353f;color:#aebdfc}

.amask.on{display:block}

.about{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.95);z-index:51;

  width:320px;max-width:86vw;background:var(--card);border:1px solid var(--line);border-radius:16px;

  padding:28px 24px 22px;text-align:center;display:none;box-shadow:0 20px 60px rgba(20,30,60,.28)}

.about.on{display:block;animation:apop .14s ease}

@keyframes apop{from{opacity:0;transform:translate(-50%,-50%) scale(.92)}to{opacity:1;transform:translate(-50%,-50%) scale(1)}}

.about .alogo{font-size:36px;line-height:1}

.about .aname{font-size:15px;font-weight:700;margin-top:8px;color:var(--ink)}

.about .aver{font-size:32px;font-weight:800;color:var(--acc,#4f6ef7);margin:10px 0 14px;letter-spacing:.02em}

.about .ameta{font-size:12.5px;color:var(--sub);line-height:1.95}

.about .ameta b{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}

.about .anote{font-size:11.5px;color:var(--side);margin:14px 0 18px;line-height:1.65;

  background:var(--bg);border-radius:9px;padding:10px 12px}

.about.welcome{width:min(880px,92vw);max-width:92vw;max-height:88vh;padding:22px 24px;text-align:left}

.wtop{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}

.wttl{font-size:19px;font-weight:700}

.wx{border:0;background:transparent;font-size:20px;line-height:1;cursor:pointer;color:var(--tx2);padding:4px 6px}

.wcards{display:grid;grid-template-columns:1fr 1fr;gap:14px}

.about.welcome .wcard .pbtn{font-size:13px;padding:10px 12px}

.wcard{border:1px solid var(--ln);border-radius:14px;padding:16px;display:flex;flex-direction:column}

.wname{font-size:15px;font-weight:600;margin-bottom:6px}

.wpic{font-size:34px;text-align:center;margin:8px 0 10px;opacity:.9}

.whow{font-size:13px;color:var(--tx2);line-height:1.75;flex:1}

.wcard .pbtn{width:100%;margin-top:12px}

@media (max-width:760px){.wcards{grid-template-columns:1fr}}

.aurow{display:flex;gap:8px;margin-top:2px}

.about .aurow .pbtn{flex:1;width:auto;min-width:0}

.about .amsg{margin-top:10px;font-size:12px;line-height:1.7;color:var(--sub);

  max-height:168px;overflow:auto;white-space:pre-wrap;word-break:break-word}

.about .amsg .upd{margin-top:8px;padding:9px 11px;border-radius:9px;background:var(--bg);

  color:var(--ink);font-size:12px;line-height:1.7;white-space:pre-wrap;text-align:left}

.about .amsg .pbtn{margin-top:9px}

.rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,182px));gap:10px;padding:12px 12px 18px;justify-content:start}

body.gridmode .thead{display:none}

.gitem{border:1px solid var(--line);border-radius:10px;padding:9px;cursor:pointer;display:flex;flex-direction:column;gap:7px;min-width:0;background:var(--card)}

.gitem:hover{background:var(--bg)}

.gitem.on{border-color:rgba(79,110,247,.5);background:var(--sel);box-shadow:0 0 0 1px rgba(79,110,247,.2) inset}

.gthumb{position:relative;height:94px;background:var(--bg);border-radius:7px;overflow:hidden}

.gthumb img{position:absolute;inset:0;margin:auto;max-width:100%;max-height:100%;object-fit:contain}

.gthumb img.gic{width:42px;height:42px}

.gthumb svg{width:40px;height:40px}

.gname{font-size:12.5px;line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;word-break:break-all;color:var(--ink)}

.gpath{font-size:11px;color:var(--side);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.gextag{font-size:10px;color:var(--sub);letter-spacing:.02em}

.pv-thumb{position:relative;display:flex;align-items:center;justify-content:center}

.pv-loading{position:absolute;font-size:11.5px;color:var(--side)}

/* ---- 深色主题补丁：这些地方原先写死了浅色主题的颜色 ---- */

[data-theme="dark"] body{background:var(--bg);color:var(--ink)}

[data-theme="dark"] #q{color:var(--ink)}

[data-theme="dark"] select{background:var(--card);color:var(--ink);border-color:var(--line)}

[data-theme="dark"] .foot b{color:var(--ink)}

[data-theme="dark"] .foot .fr,

[data-theme="dark"] .footer{color:var(--sub)}

[data-theme="dark"] .footer kbd{background:var(--card);color:var(--ink);border-color:var(--line)}

[data-theme="dark"] .about .aname,

[data-theme="dark"] .about .ameta b,

[data-theme="dark"] .help .hht,

[data-theme="dark"] .mver:hover{color:var(--ink)}

[data-theme="dark"] .about,

[data-theme="dark"] .help{background:var(--card);border-color:var(--line)}

[data-theme="dark"] .about .ameta,

[data-theme="dark"] .about .anote,

[data-theme="dark"] .help .hrow span,

[data-theme="dark"] .help .hfoot{color:var(--sub)}

[data-theme="dark"] .help .hk{background:var(--bg);border-color:var(--line);color:var(--ink)}

[data-theme="dark"] .help .hsec h4{color:var(--acc)}

[data-theme="dark"] .welcome .wcard{background:var(--bg);border-color:var(--line)}

[data-theme="dark"] .welcome .wcard h3,

[data-theme="dark"] .welcome h2{color:var(--ink)}

[data-theme="dark"] .welcome .wcard p{color:var(--sub)}

[data-theme="dark"] .chip{background:var(--bg);border-color:var(--line);color:var(--ink)}

[data-theme="dark"] .pmsg,[data-theme="dark"] .small,[data-theme="dark"] .tip{color:var(--sub)}

[data-theme="dark"] .drow small{color:var(--sub)}

[data-theme="dark"] .pbtn{background:var(--card);border-color:var(--line);color:var(--ink)}

[data-theme="dark"] .pbtn:hover{border-color:var(--acc);color:var(--acc)}

[data-theme="dark"] .pbtn.hot{box-shadow:0 0 0 2px rgba(107,138,255,.25)}

[data-theme="dark"] input::placeholder,[data-theme="dark"] textarea::placeholder{color:#7b8496}

[data-theme="dark"] .tlhead .tln,[data-theme="dark"] .tlt{color:var(--sub)}

[data-theme="dark"] .pv-snip{color:#c3cad8}

[data-theme="dark"] .pv-k{color:var(--sub)}

[data-theme="dark"] .revrow,[data-theme="dark"] .revdir{color:var(--sub)}

[data-theme="dark"] .ctxm{background:var(--card);border-color:var(--line)}

[data-theme="dark"] .ctxm .ctxi{color:var(--ink)}

[data-theme="dark"] .ctxm .ctxi:hover{background:var(--sel)}

[data-theme="dark"] .toast{background:#2b3140;color:#eef1f6}

</style></head><body>

<div class=topbar>

<span class=logo><svg width=20 height=20 viewBox="0 0 24 24" fill="none"><rect width=24 height=24 rx=6 fill="#4f6ef7"/><circle cx=10 cy=10 r=4.5 stroke="#fff" stroke-width=1.6"/><path d="M13.2 13.2L17 17" stroke="#fff" stroke-width=1.8" stroke-linecap="round"/><path d="M7.5 10h5M10 7.5v5" stroke="#fff" stroke-width=1.2 stroke-linecap="round"/></svg></span>

<input id=q data-i18n-ph="ph_q" placeholder="输入关键词，比如：项目方案 / 课程笔记 / 报销标准" autofocus>

<button class="gear" id=viewbtn onclick="viewMenu(event)" title="视图">▦</button>

<button class=sbtn onclick=go() data-i18n="search">搜 索</button>

<button class="gear" id=homebtn onclick=goHome() title="最近动过的文件">🕘</button>

<button class="gear" id=revbtn onclick=pickReverse() title="反查一个文件">🔎</button>

<button class=gear id=gear onclick=showCfg() title="设置">⚙</button>

</div>

<div class=filterbar id=fbar></div>

<div class="ctxm vmenu" id=vmenu>

<div class=ctxi onclick="vPick('list')"><span class=vic id=vm_list></span><span data-i18n="view_list">详情视图</span></div>

<div class=ctxi onclick="vPick('grid')"><span class=vic id=vm_grid></span><span data-i18n="view_grid">缩略图视图</span></div>

<div class=vsep></div>

<div class=ctxi onclick="vPick('pv')"><span class=vic id=vm_pv></span><span data-i18n="view_panel">右侧详情面板</span></div>

<div class=vsep></div>

<div class=ctxi onclick="vPick('sort')"><span class=vic>⇅</span><span id=vm_sortt>排序</span></div>

</div>

<div class=main>

<div class=side id=side>

<div class=sh><span data-i18n="side_filter">筛选</span></div>

<div class="sitem on" data-kind=all onclick="pickKind('all',this)"><span class=si>▦</span><span data-i18n="k_all">全部</span><b id=ck_all></b></div>

<div class=sitem data-kind=dir onclick="pickKind('dir',this)"><span class=si>📁</span><span data-i18n="k_dir">文件夹</span><b id=ck_dir></b></div>

<div class=sitem data-kind=file onclick="pickKind('file',this)"><span class=si>📄</span><span data-i18n="k_file">文件</span><b id=ck_file></b></div>

<div class=sitem data-kind=doc onclick="pickKind('doc',this)"><span class=si>📑</span><span data-i18n="k_doc">文档</span><b id=ck_doc></b></div>

<div class=sitem data-kind=img onclick="pickKind('img',this)"><span class=si>🖼</span><span data-i18n="k_img">图片</span><b id=ck_img></b></div>

<div class=sitem data-kind=video onclick="pickKind('video',this)"><span class=si>🎬</span><span data-i18n="k_video">视频</span><b id=ck_video></b></div>

<div class=sitem data-kind=audio onclick="pickKind('audio',this)"><span class=si>🎵</span><span data-i18n="k_audio">音频</span><b id=ck_audio></b></div>

<div class=sitem data-kind=zip onclick="pickKind('zip',this)"><span class=si>🗜</span><span data-i18n="k_zip">压缩包</span><b id=ck_zip></b></div>

<div class=sitem data-kind=app onclick="pickKind('app',this)"><span class=si>🚀</span><span data-i18n="k_app">应用</span><b id=ck_app></b></div>

<div class=sh><span data-i18n="recent_h">最近修改</span></div>

<div class="sitem on" data-days=0 onclick="pickDays(0,this)"><span class=si>🕒</span><span data-i18n="r_all">全部</span></div>

<div class=sitem data-days=1 onclick="pickDays(1,this)"><span class=si>🕐</span><span data-i18n="r_1d">最近 1 天</span></div>

<div class=sitem data-days=7 onclick="pickDays(7,this)"><span class=si>🕐</span><span data-i18n="r_7d">最近 7 天</span></div>

<div class=sitem data-days=30 onclick="pickDays(30,this)"><span class=si>🕐</span><span data-i18n="r_30d">最近 30 天</span></div>

<div class=sitem data-days=365 onclick="pickDays(365,this)"><span class=si>🕐</span><span data-i18n="r_365d">最近 365 天</span></div>

<div class=sh><span data-i18n="path_h">路径过滤</span></div>

<div class=spath><input id=dp oninput="onPathInput()" data-i18n-ph="ph_d" placeholder="路径包含…"><b id=dpclr class=dpclr onclick="clearPath()" title="清空">✕</b></div>

</div>

<div class=tablewrap>

<div class="frbar hide" id=frbar><b id=frn>0</b><span id=frmsg></span>

<span class=frp><i id=frpi></i></span><button class=frbtn id=frpb onclick="frToggle()">暂停</button>

<button class=frbtn onclick="frHide()" title="收起来">✕</button></div>

<div class=thead><div></div><div data-i18n="col_name">名称</div><div data-i18n="col_path">路径</div><div style="text-align:right" data-i18n="col_mtime">修改时间</div></div>

<div class=rlist id=r><div class=empty>输入关键词开始搜索</div></div>

</div>

<div class=preview id=pv></div>

</div>

<div class=footer>

<span class=nitems id=nitems>0 <span data-i18n="n_items">个项目</span></span>

<span><kbd>↑↓</kbd> <span data-i18n="hk_sel">选择</span> · <kbd>Enter</kbd> <span data-i18n="hk_open">打开</span> · <kbd>Ctrl+Enter</kbd> <span data-i18n="hk_loc">定位</span> · <kbd>Ctrl+1~9</kbd> <span data-i18n="hk_direct">直达</span> · <kbd>Ctrl+S</kbd> <span data-i18n="hk_sort">排序</span> · <kbd>Esc</kbd> <span data-i18n="hk_clr">清除</span></span>

<span class=verbox><span class="hkhint" data-i18n="hk" onclick="showAbout()">双击 Ctrl 呼出快搜窗 —— 关掉本窗口不会退出，程序在托盘里等着</span> <a class=newui href="/web/" title="试用新的 React 界面（实验）">新界面</a> <b class=ver onclick="showAbout()" title="关于 / About">__VER__</b> <span id=fmid data-i18n="footer_mid">· __FILES__ 个文件名 + __TOTAL__ 篇正文可搜 · 本地搜索</span></span>

</div>

<div class=mask id=mask onclick=hideCfg()></div>

<div class=drawer id=cfg>

<h3><span data-i18n="settings">⚙ 设置</span> <b onclick=hideCfg()>✕</b></h3>

<div class=dsec>

<div class=dsec-t data-i18n="d_general">通用</div>

<div class=drow><div class=dl data-i18n="d_autostart">开机启动<small data-i18n="d_autostart_tip">开机时自动后台启动</small></div><div class=toggle id=tg_auto onclick=toggleAuto()><i></i></div></div>

<div class=drow><div class=dl data-i18n="d_hotkey">双击 Ctrl 唤出快搜<small data-i18n="d_hotkey_tip">全局热键，随时唤出</small></div><div class=toggle on id=tg_hotkey onclick=toggleHotkey()><i></i></div></div>

<div class=drow><div class=dl data-i18n="d_lang">界面语言</div><select id=sel_lang onchange=changeLang(this.value) style="border:1px solid var(--line);border-radius:6px;padding:4px 8px;font-size:12px;background:var(--card)"><option value=zh>中文 / Chinese</option><option value=en>English / 英文</option></select></div>

<div class=drow><div class=dl data-i18n="d_theme">主题<small data-i18n="d_theme_tip">跟随系统 / 浅色 / 深色</small></div><select id=sel_theme onchange=changeTheme(this.value) style="border:1px solid var(--line);border-radius:6px;padding:4px 8px;font-size:12px;background:var(--card)"><option value=auto data-i18n="th_auto">跟随系统</option><option value=light data-i18n="th_light">浅色</option><option value=dark data-i18n="th_dark">深色</option></select></div>

</div>

<details class=dsec id=scope_details><summary class=dsec-t style="cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between"><span data-i18n="scope_h">索引范围</span><span style="font-size:11px;color:var(--side)" data-i18n="scope_expand">点击展开</span></summary>

<p id=cfgtip data-i18n="scope_tip" style="font-size:12.5px;color:var(--sub);margin-bottom:10px;line-height:1.7">下面这些文件夹里的文档会被收入索引。</p>

<div class=chips id=dirs></div>

<div id=cand></div>

<button class="pbtn gray" id=addbtn data-i18n="btn_add" data-i18n-title="tip_add" onclick=addDir() style="margin-top:6px">➕ 手动添加文件夹</button>

<div class=nstat id=nstat></div>

</details>

<details class=dsec id=maint_details><summary class=dsec-t style="cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between"><span data-i18n="maint_h">维护工具</span><span style="font-size:11px;color:var(--side)" data-i18n="scope_expand">点击展开</span></summary>

<div class=prow style="margin-top:8px">

<button class="pbtn" id=smartbtn data-i18n="btn_scan" data-i18n-title="tip_scan" onclick=smartScan(this)>🚀 智能扫描全盘（推荐）</button>

<button class="pbtn gray" id=savebtn data-i18n="btn_save" data-i18n-title="tip_save" onclick=saveUpd(this)>💾 保存并更新索引</button>

<button class="pbtn gray" id=updonly data-i18n="btn_update" data-i18n-title="tip_update" onclick=upd(this)>🔄 按现有范围更新</button>

<button class="pbtn gray" id=stbtn data-i18n="btn_test" data-i18n-title="tip_test" onclick=selfTest(this)>🩺 引擎体检</button>

<button class="pbtn gray" id=nbtn data-i18n="btn_names" data-i18n-title="tip_names" onclick=buildNames(this)>🗂 重建文件名索引</button></div>

</details>

<div class=pbar id=pw style=display:none><i id=pi></i></div>

<div class=pmsg id=pm></div>

<details class=help><summary data-i18n="help_q">❓ 这五个按钮分别是干嘛的？</summary><ul>

<li data-i18n="help_1"></li><li data-i18n="help_2"></li><li data-i18n="help_3"></li><li data-i18n="help_4"></li><li data-i18n="help_5"></li>

</ul></details>

</div>

<div id=toast></div>

<div class=amask id=amask onclick=hideAbout()></div>

<div class=about id=about>

  <div class=alogo>🧠</div>

  <div class=aname data-i18n="about_name">第二大脑 · 本地搜索</div>

  <div class=aver>__VER__</div>

  <div class=ameta id="ab_meta" data-nf="__FILES__" data-nt="__TOTAL__" data-build="__BUILD__">

    <div id="ab_docs">正文可搜 <b>__TOTAL__</b> 篇 · 文件名可搜 <b>__FILES__</b> 个</div>

    <div>Python <b>__PY__</b> · SQLite <b>__SQL__</b></div>

    <div id="ab_build">构建日期 <b>__BUILD__</b></div>

    <div id="ab_dir" style="margin-top:6px">数据目录 <b id="ab_dirp">…</b> <a href="#" onclick="openDataDir();return false" style="color:var(--acc)">打开</a></div>

  </div>

  <div class=anote id="ab_note" data-i18n="about_note">当前为 <b>0.x 预览版</b>：功能还在打磨，未达正式标准。<br>版本号到达 <b>1.0</b> 即视为正式版。</div>

  <div class=aurow>

    <button class="pbtn gray" id=ab_chk onclick=checkUpdate() data-i18n="about_check">检查更新</button>

    <button class="pbtn gray" onclick=hideAbout() data-i18n="about_ok">知道了</button>

  </div>

  <div class=amsg id=ab_msg></div>

</div>

<div class=amask id=hmask onclick=hideHelp()></div>

<div class="about help" id=help>

 <div class=hhd><span class=hht data-i18n="hp_title">使用帮助</span>

  <button class=wx onclick=hideHelp()>✕</button></div>

 <div class=hcols>

<div class=hsec><div class=hst data-i18n="hp_s_kb">快捷键</div>

<div class=hrow><span class=hk>↑ ↓</span><span class=ht data-i18n="hp_kb1">上下选择</span></div>

<div class=hrow><span class=hk>Enter</span><span class=ht data-i18n="hp_kb2">打开选中的</span></div>

<div class=hrow><span class=hk>Ctrl+Enter</span><span class=ht data-i18n="hp_kb3">在文件夹里定位</span></div>

<div class=hrow><span class=hk>Ctrl+1~9</span><span class=ht data-i18n="hp_kb4">直接打开第 1~9 条</span></div>

<div class=hrow><span class=hk>Ctrl+S</span><span class=ht data-i18n="hp_kb5">换排序：相关度 / 最新 / 最早 / 最大 / 名称</span></div>

<div class=hrow><span class=hk>Ctrl+F</span><span class=ht data-i18n="hp_kb6">回到搜索框</span></div>

<div class=hrow><span class=hk>Esc</span><span class=ht data-i18n="hp_kb7">清空搜索 / 关掉面板</span></div>

<div class=hrow><span class=hk>双击 Ctrl</span><span class=ht data-i18n="hp_kb8">唤出快搜窗（关掉窗口程序还在托盘）</span></div>

</div>

<div class=hsec><div class=hst data-i18n="hp_s_q">搜索框里能直接写</div>

<div class=hrow><span class=hk>ext:pdf</span><span class=ht data-i18n="hp_q1">只看 PDF；写 ext:pdf,docx 看两种</span></div>

<div class=hrow><span class=hk>size:>10m</span><span class=ht data-i18n="hp_q2">按大小；也支持 &lt; 和区间 1k-2m</span></div>

<div class=hrow><span class=hk>dm:7d</span><span class=ht data-i18n="hp_q3">最近 7 天改过的；2w 两周、1m 一个月</span></div>

<div class=hrow><span class=hk>-草稿</span><span class=ht data-i18n="hp_q4">排除路径里带这个词的</span></div>

<div class=hrow><span class=hk>资料/</span><span class=ht data-i18n="hp_q5">只看这个文件夹（末尾加斜杠）</span></div>

<div class=hrow><span class=hk>content:</span><span class=ht data-i18n="hp_q6">只搜正文，不要文件名凑数</span></div>

</div>

<div class=hsec><div class=hst data-i18n="hp_s_more">其它好用的</div>

<div class=hrow><span class=hk>🕘</span><span class=ht data-i18n="hp_m1">首页时间轴：不输入时列出最近改过的文件</span></div>

<div class=hrow><span class=hk>右键</span><span class=ht data-i18n="hp_m2">打开 / 定位 / 复制路径 / 打包成一个 zip</span></div>

<div class=hrow><span class=hk>拖进来</span><span class=ht data-i18n="hp_m3">把文件拖进窗口，反查有没有被索引、同名版本有几个</span></div>

<div class=hrow><span class=hk>▦</span><span class=ht data-i18n="hp_m4">列表 / 缩略图 / 右侧详情面板</span></div>

<div class=hrow><span class=hk>🔎</span><span class=ht data-i18n="hp_m5">选一个文件反查（不会上传任何东西）</span></div>

<div class=hrow><span class=hk>托盘</span><span class=ht data-i18n="hp_m6">设置、关于、检查更新都在这里</span></div>

</div>

 </div>

 <div class=hfoot data-i18n="hp_foot">这些都不需要记，用久了自然会。忘了就右键托盘图标 →「使用帮助」。</div>

</div>

<div class=amask id=wmask onclick=hideWelcome(1)></div>

<div class="about welcome" id=welcome>

 <div class=wtop><span class=wttl data-i18n="wel_title">欢迎使用第二大脑</span>

  <button class=wx onclick=hideWelcome(1)>✕</button></div>

 <div class=wcards>

  <div class=wcard><div class=wname data-i18n="wel_c1">搜索文件</div>

   <div class=wpic>⌨️</div>

   <div class=whow data-i18n="wel_c1h">双击 Ctrl 立刻弹出快搜窗，边打边出结果；也可以点托盘图标打开详细搜索。</div>

   <button class=pbtn onclick=welcomeMini() data-i18n="wel_b1">打开快搜窗口（双击 Ctrl）</button></div>

  <div class=wcard><div class=wname data-i18n="wel_c2">不打字也能用</div>

   <div class=wpic>🕘</div>

   <div class=whow data-i18n="wel_c2h">首页列出最近改过的文件；右键有打开 / 定位 / 复制路径 / 打包；把文件拖进窗口可以反查它有没有被索引。</div>

   <button class="pbtn gray" onclick=hideWelcome(1) data-i18n="wel_b2">开始搜索</button></div>

 </div>

</div>

<script>

const DICT={

zh:{

title:'第二大脑 · 本地文档搜索',

brand:'第二大脑',

sub:'搜文档内容和文件名 —— __FILES__ 个文件名 + __TOTAL__ 篇正文可搜 · 全程本地',

hk:'双击 <kbd>Ctrl</kbd> 呼出快搜窗 —— 关掉本窗口不会退出，程序在托盘里等着',

settings:'⚙ 设置',

d_general:'通用',d_autostart:'开机启动',d_autostart_tip:'开机时自动后台启动',

d_hotkey:'双击 Ctrl 唤出快搜',d_hotkey_tip:'全局热键，随时唤出',

d_autostart_on:'已设置开机启动',d_autostart_off:'已取消开机启动',d_hotkey_change:'热键状态已切换',

d_hotkey_on:'双击 Ctrl 已开启',d_hotkey_off:'双击 Ctrl 已关闭',

d_lang:'界面语言',d_theme:'主题',d_theme_tip:'跟随系统 / 浅色 / 深色',

th_auto:'跟随系统',th_light:'浅色',th_dark:'深色',

hk_sel:'选择',hk_open:'打开',hk_loc:'定位',hp_title:'使用帮助',hp_foot:'这些都不需要记，用久了自然会。忘了就右键托盘图标 →「使用帮助」。',hp_s_kb:'快捷键',hp_s_q:'搜索框里能直接写',hp_s_more:'其它好用的',hp_kb1:'上下选择',hp_kb2:'打开选中的',hp_kb3:'在文件夹里定位',hp_kb4:'直接打开第 1~9 条',hp_kb5:'换排序：相关度 / 最新 / 最早 / 最大 / 名称',hp_kb6:'回到搜索框',hp_kb7:'清空搜索 / 关掉面板',hp_kb8:'唤出快搜窗（关掉窗口程序还在托盘）',hp_q1:'只看 PDF；写 ext:pdf,docx 看两种',hp_q2:'按大小；也支持 < 和区间 1k-2m',hp_q3:'最近 7 天改过的；2w 两周、1m 一个月',hp_q4:'排除路径里带这个词的',hp_q5:'只看这个文件夹（末尾加斜杠）',hp_q6:'只搜正文，不要文件名凑数',hp_m1:'首页时间轴：不输入时列出最近改过的文件',hp_m2:'打开 / 定位 / 复制路径 / 打包成一个 zip',hp_m3:'把文件拖进窗口，反查有没有被索引、同名版本有几个',hp_m4:'列表 / 缩略图 / 右侧详情面板',hp_m5:'选一个文件反查（不会上传任何东西）',hp_m6:'设置、关于、检查更新都在这里',hk_direct:'直达',hk_sort:'换排序',hk_clr:'清除',

side_filter:'筛选',k_all:'全部',k_dir:'文件夹',k_file:'文件',k_doc:'文档',k_img:'图片',k_video:'视频',k_app:'应用',k_audio:'音频',k_zip:'压缩包',path_h:'路径过滤',recent_h:'最近修改',r_all:'全部',r_1d:'最近 1 天',r_7d:'最近 7 天',r_30d:'最近 30 天',r_365d:'最近 365 天',

col_name:'名称',col_path:'路径',col_mtime:'修改时间',pv_loading:'缩略图生成中…',view_grid:'缩略图视图',view_list:'详情视图',view_menu:'视图',view_panel:'右侧详情面板',vmenu_sort:'排序',sort_rel:'相关度',sort_new:'最新',sort_old:'最早',sort_big:'最大',sort_name:'按名称',sort_now:'排序：',qsyn_tip:'也可以在搜索框里直接写：ext:pdf · size:>10m · dm:7d · -排除词 · 某文件夹/',n_items:'个项目',filter_empty:'这个筛选下没有结果 —— 点左边的「全部」看所有命中',

f_doc:'文档',f_file:'文件名',pv_empty:'选中文件查看预览',pv_nosnip:'该文件无正文预览（仅命中文件名）',

scope_expand:'点击展开',

ph_q:'输入关键词，比如：项目方案 / 课程笔记 / 报销标准',

ph_t:'类型：pdf / docx…',

ph_d:'路径包含…',

search:'搜 索',

scope_h:'索引范围',maint_h:'维护工具',

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

help_q:'❓ 这五个按钮分别是干嘛的？',

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

m_names_auto:'🗂 还差最后一步，正在自动建立文件名索引（第一次要几十秒）',

cand_go:'✅ 就这些，开始建库',

c_open:'打开',c_reveal:'在文件夹中显示',c_copy:'复制路径',c_zip:'打包这个文件',

zip_ok:'已打包 __N__ 个文件，正在打开所在文件夹',zip_fail:'打包没成功',

dym_prefix:'你是不是要找：',

    'tl_cap':'只列前 __N__ 个',

tl_more:'看更早的（30 天）',

same_n:'同名版本 __N__ 个',same_none:'没有其他同名版本',

rev_title:'反查这个文件',rev_in:'✓ 这个文件在索引里，入库时间',rev_out:'不在库内（还没被索引到）',

rev_size:'一样大小、同类型',rev_sib:'同目录还有 __N__ 个文件',

rev_none:'库里没有这个文件的相关记录',rev_pick:'粘贴文件完整路径（例如 D 盘某个文件夹里的 xxx.docx）',

rev_need:'拖进来的文件只拿到名字、拿不到路径，帮你打开选择框：',

e_names_busy:'已有任务在跑，等它跑完再点',

empty_hint:'输入关键词开始搜索',

empty_tip:'<b>小提示</b>：两个过滤框可以留空；点结果里的「打开」直接打开原文件',

footer_mid:'· __FILES__ 个文件名 + __TOTAL__ 篇正文可搜',about_ok:'知道了',

about_name:'第二大脑 · 本地搜索',

about_docs:'正文可搜 <b>{t}</b> 篇 · 文件名可搜 <b>{n}</b> 个',

about_build:'构建日期 <b>{b}</b>',

about_note:'当前为 <b>0.x 预览版</b>：功能还在打磨，未达正式标准。<br>版本号到达 <b>1.0</b> 即视为正式版。',

about_check:'检查更新',

wel_title:'欢迎使用第二大脑',

wel_c1:'搜索文件',wel_c1h:'双击 Ctrl 立刻弹出快搜窗，边打边出结果；也可以点托盘图标打开详细搜索。',

wel_b1:'打开快搜窗口（双击 Ctrl）',

wel_c2:'不打字也能用',wel_c2h:'首页列出最近改过的文件；右键有打开 / 定位 / 复制路径 / 打包；把文件拖进窗口可以反查它有没有被索引。',

wel_b2:'开始搜索',

fr_files:'__N__ 个文件',

fr_bar_pause:'暂停',

  fr_bar_resume:'继续',

  fr_bar_eta:'预计还需 __M__ 分钟',

  fr_bar_done:'✅ 已经能搜 __N__ 个文件了，全盘也在后台补完了',

  fr_hint_ctrl:'随时双击 Ctrl 呼出快搜窗；右键托盘图标可以打开设置或禁用这个热键',

  data_fallback:'程序目录写不进去，数据库改放到：__P__',

  bg_done:'全盘也补完了，现在可搜 __N__ 个文件',

bg_reading:'正文读取中 __N__ 篇',

  fr_stage_names:'先建桌面 / 文档 / 下载的文件名…',

  fr_start:'第一次打开，正在自动帮你把电脑上的文件建成索引（只读文件名和文档内容，不上传任何东西）。',

fr_busy:'正在自动建库，你可以先去干别的，窗口最小化也没关系。',

fr_skip:'先不要，我自己选文件夹',

fr_done:'建好了，正在刷新…',

fr_fail:'自动建库没成功：',

fr_skipped:'好，那你自己选文件夹吧——点「＋ 添加文件夹」，选完点 🔄 更新。',

about_checking:'正在连 GitHub 看看有没有新版本…',

about_latest:'已经是最新的（线上 {v}）。',

about_new:'有新版本 {v} 可以装上：',

about_page:'打不开自动更新？去发布页手动下载',

about_apply:'下载并安装（装完自动重开）',

about_applying:'正在下载…下载完会自动关掉重开，大概十几秒。',

about_fail:'连不上 GitHub（网络或代理的问题）。可以稍后再试，或去发布页手动下载。',

upd_net:'下载没成功（网络断了或文件不对）。可以稍后再试，或在浏览器里手动下载。',

upd_done:'✅ 已下载 {v}，正在关掉重开完成安装。',

open:'打开',copy:'复制路径',copied:'已复制',

found:'找到 <b>{n}</b> 条 · {ms} ms',

searching:'搜索中…',

need_kw:'请先输入关键词',

no_hit:'没有找到「{q}」',

ev_h:'📁 文件名命中',ev_sub:'全盘任意文件 · 本机文件名索引',ev_sub_off:'索引内已收录的文件',ev_tag:'全盘',

ev_more:'还有更多 —— 再加一个关键词缩小范围',

ev_only:'索引的内容里没有命中，但文件名有 <b>{n}</b> 条匹配：',

no_hit_tip:'试试<b>换个词</b>、<b>少个过滤条件</b>，或先点「🔄 按现有范围更新」把新文件收进来',

opened:'已打开',open_fail:'打开失败',

file_gone:'这个文件搬走或删了。点一下「🔄 按现有范围更新」，几秒钟就能清掉这条记录（不会重扫整台电脑）',

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

err_prefix:'❌ 出错了：',revealed:'已在文件夹里选中',no_hit_mini:'没有找到「{q}」。换个说法试试，或者按 Ctrl+Enter 去主窗口搜正文。',net_fail:'后台没回应（可能刚重启）。把窗口关掉重开，或双击 更新第二大脑.bat 再试。',

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

e_no_scan:'扫描清单没生成，正文一篇都没读进来 —— 请确认程序目录可写，再点一次更新',

e_bad_theme:'主题值不合法（只能是 跟随系统 / 浅色 / 深色）',

e_bad_lang:'界面语言值不合法（只能是中文或英文）',

cfg_unsaved:'设置里改的文件夹还没保存——点 🔄 更新会先保存再更新（也可以点 💾 只保存）',

paused_prefix:'⏸ 已暂停（不是出错）：',

repair_ask:'「__P__」找不到了，下面几个文件夹看起来像是它搬去的地方——是哪一个？',

repair_ask_tail:'点一个就开始更新；都不是就点最下面那行，先别改。',

repair_done:'好了，正在按新位置更新，等一小会儿…',

repair_none:'都不是，先别改',

e_prune_scope_loss:'文件夹「__P__」整个不见了，它名下有 __N__ 条记录同时找不到了，已暂停，一条都没删。多半是它被搬走或改名了——点一次更新让它自动认亲；确认是删掉了，再双击【强制清理.bat】。',

e_scope_gone:'要索引的文件夹找不到了：{p}。已停止更新，索引一条没动。请确认它还在原处（是不是搬走了？），或在设置里把它从索引范围里去掉。',

e_prune_bulk:'这次要清掉 {n} 条（库内共 {total} 条），已暂停，一条都没删。多半是索引范围失效、盘符变了或权限异常。请先确认索引范围里的文件夹都还在原处；如果确认这些文件是你真的删掉了，双击【强制清理.bat】再清一次。',

e_need_dir:'至少要添加一个文件夹',

e_discover_busy:'智能扫描正在进行',

e_selftest_busy:'自检已在进行',

e_update_busy:'已有更新在进行',

e_scope_save:'索引范围没存进 scope.json（磁盘只读/被占用/路径写错），这次更新没有开始',

e_busy:'已经有一个任务在跑了（更新/建索引/扫描/自检），等它跑完再点',

e_bad_body:'请求格式不正确',

e_query:'查询出错（关键词至少 3 个字符效果最佳）: {e}'

},

en:{

title:'Second Brain · Local Document Search',

brand:'Second Brain',

sub:'Search document contents and file names — __FILES__ file names + __TOTAL__ documents · 100% local & offline · best with 3+ characters',

hk:'Double-tap <kbd>Ctrl</kbd> for quick search — closing this window keeps it running in the tray',

settings:'⚙ Settings',

d_general:'General',d_autostart:'Start at boot',d_autostart_tip:'Auto-start in background on boot',

d_hotkey:'Double Ctrl to summon',d_hotkey_tip:'Global hotkey, always available',

d_autostart_on:'Will start at boot',d_autostart_off:'Removed from startup',d_hotkey_change:'Hotkey toggled',

d_hotkey_on:'Double-Ctrl enabled',d_hotkey_off:'Double-Ctrl disabled',

d_lang:'Language',d_theme:'Theme',d_theme_tip:'System / Light / Dark',

th_auto:'System',th_light:'Light',th_dark:'Dark',

hk_sel:'Select',hk_open:'Open',hk_loc:'Locate',hp_title:'Help',hp_foot:'Nothing here needs memorizing. Forgot one? Right-click the tray icon and pick Help.',hp_s_kb:'Shortcuts',hp_s_q:'Type filters in the search box',hp_s_more:'Other things that help',hp_kb1:'Move up / down',hp_kb2:'Open',hp_kb3:'Show in folder',hp_kb4:'Open item 1~9',hp_kb5:'Cycle sort: relevance / newest / oldest / largest / name',hp_kb6:'Back to the search box',hp_kb7:'Clear the search / close this panel',hp_kb8:'Summon the quick window (the tray keeps running)',hp_q1:'PDFs only; ext:pdf,docx for two kinds',hp_q2:'By size; also ranges like 1k-2m',hp_q3:'Changed in the last 7 days; 2w and 1m work too',hp_q4:'Exclude paths containing this word',hp_q5:'Only this folder (trailing slash)',hp_q6:'Body text only, no file-name padding',hp_m1:'Home timeline: recently changed files while the box is empty',hp_m2:'Open / reveal / copy path / zip them together',hp_m3:'Drop a file here to see whether it is indexed and how many versions exist',hp_m4:'List / thumbnails / details panel',hp_m5:'Pick a file to look up (nothing is uploaded)',hp_m6:'Settings, about and update checks live here',hk_direct:'open 1~9',hk_sort:'sort',hk_clr:'Clear',

side_filter:'Filter',k_all:'All',k_dir:'Folders',k_file:'Files',k_doc:'Documents',k_img:'Images',k_video:'Videos',k_app:'Apps',k_audio:'Audio',k_zip:'Archives',path_h:'Path filter',recent_h:'Modified',r_all:'Any time',r_1d:'Last 1 day',r_7d:'Last 7 days',r_30d:'Last 30 days',r_365d:'Last 365 days',

col_name:'Name',col_path:'Path',col_mtime:'Modified',pv_loading:'Rendering thumbnail…',view_grid:'Thumbnail view',view_list:'Detail view',view_menu:'View',view_panel:'Details panel',vmenu_sort:'Sort',sort_rel:'Relevance',sort_new:'Newest',sort_old:'Oldest',sort_big:'Largest',sort_name:'Name',sort_now:'Sort: ',qsyn_tip:'You can also type filters: ext:pdf · size:>10m · dm:7d · -exclude · somefolder/',n_items:'items',filter_empty:'Nothing matches this filter — click “All” to see every hit',

f_doc:'Docs',f_file:'Files',pv_empty:'Select a file to preview',pv_nosnip:'No content preview (filename match only)',

scope_expand:'Click to expand',

ph_q:'Keywords, e.g. project plan / lecture notes / expense policy',

ph_t:'Type: pdf / docx…',

ph_d:'Path contains…',

search:'Search',

scope_h:'Index scope',maint_h:'Maintenance',

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

help_q:'❓ What do these five buttons do?',

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

m_names_auto:'🗂 One last step: building the file-name index automatically (takes a while the first time)',

cand_go:'✅ That’s it — start indexing',

c_open:'Open',c_reveal:'Show in folder',c_copy:'Copy path',c_zip:'Zip this file',

zip_ok:'Zipped __N__ file(s) — opening the folder',zip_fail:'Could not create the zip',

dym_prefix:'Did you mean:',

    tl_cap:'first __N__ shown',

tl_more:'Show earlier (30 days)',

same_n:'__N__ other version(s) of this name',same_none:'No other versions with this name',

rev_title:'Look up this file',rev_in:'✓ This file is indexed, added on',rev_out:'Not in the index (not indexed yet)',

rev_size:'Same size, same type',rev_sib:'__N__ more files in the same folder',

rev_none:'Nothing about this file in the index',rev_pick:'Paste the full path (for example, xxx.docx inside a folder on drive D)',

rev_need:'A dropped file gives us only its name, not its path — opening a picker:',

e_names_busy:'Another task is running — wait for it to finish',

empty_hint:'Type a keyword to start searching',

empty_tip:'<b>Tip</b>: the two filter boxes are optional; hit “Open” on a result to launch the original file',

footer_mid:'· __FILES__ file names + __TOTAL__ documents searchable',about_ok:'Got it',

about_name:'Second Brain · local search',

about_docs:'<b>{t}</b> documents · <b>{n}</b> file names searchable',

about_build:'Built <b>{b}</b>',

about_note:'This is a <b>0.x preview</b>: still being polished.<br>Version <b>1.0</b> will be the stable release.',

about_check:'Check for updates',

wel_title:'Welcome to Second Brain',

wel_c1:'Find files',wel_c1h:'Press Ctrl twice to pop up the quick search — results appear as you type. You can also click the tray icon.',

wel_b1:'Open quick search (double Ctrl)',

wel_c2:'Works without typing',wel_c2h:'The home screen lists recently changed files; right-click for open / reveal / copy path / zip; drop a file on the window to look it up.',

wel_b2:'Start searching',

fr_files:'__N__ files',

fr_bar_pause:'Pause',

  fr_bar_resume:'Resume',

  fr_bar_eta:'about __M__ min left',

  fr_bar_done:'✅ __N__ file names are searchable now, and the rest is done too',

  fr_hint_ctrl:'Double-tap Ctrl any time for quick search; right-click the tray icon for settings or to turn it off',

  data_fallback:'Install folder is not writable — database moved to: __P__',

  bg_done:'Full-disk pass finished — __N__ file names searchable now',

bg_reading:'reading text — __N__ so far',

  fr_stage_names:'Indexing Desktop / Documents / Downloads first…',

  fr_start:'First run: automatically indexing the files on this PC (file names and document text stay on your machine).',

fr_busy:'Building the index automatically. You can keep working; minimising the window is fine.',

fr_skip:'Not now, I will pick folders myself',

fr_done:'Done. Refreshing…',

fr_fail:'Automatic setup failed: ',

fr_skipped:'Fine — add folders with “+ Add folder”, then press 🔄 Update.',

about_checking:'Asking GitHub whether there is a newer version…',

about_latest:'Already up to date (online: {v}).',

about_new:'Version {v} is available:',

about_page:'Auto-update failed? Download from the release page',

about_apply:'Download and install (restarts itself)',

about_applying:'Downloading… the app will close and reopen by itself, about ten seconds.',

about_fail:'Cannot reach GitHub (network or proxy). Try again later, or download from the release page.',

upd_net:'Download failed (network error or bad file). Try again, or download in your browser.',

upd_done:'✅ Downloaded {v}. Restarting to finish the install.',

open:'Open',copy:'Copy path',copied:'Copied',

found:'<b>{n}</b> results · {ms} ms',

searching:'Searching…',

need_kw:'Please type a keyword first',

no_hit:'Nothing found for “{q}”',

ev_h:'📁 Filename matches',ev_sub:'any file on disk · built-in name index',ev_sub_off:'files already in the index',ev_tag:'disk',

ev_more:'more matches — add a keyword to narrow it down',

ev_only:'Nothing in the index matched, but <b>{n}</b> filenames do:',

no_hit_tip:'Try <b>another keyword</b>, <b>fewer filters</b>, or run the updater to index new files first.',

opened:'Opened',open_fail:'Failed to open',

file_gone:'Moved or deleted — click “🔄 Update current scope” to clear this row in seconds (no full rescan)',

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

err_prefix:'❌ Error: ',revealed:'Selected in Explorer',no_hit_mini:'Nothing found for “{q}”. Try different words, or press Ctrl+Enter to search document contents.',net_fail:'The background service did not answer (it may have just restarted). Reopen the window, or run the update script and try again.',

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

e_no_scan:'The scan manifest was not written, so no document text was indexed — make sure the app folder is writable and update again',

e_bad_theme:'Invalid theme value (auto / light / dark only)',

e_bad_lang:'Invalid language value (zh / en only)',

cfg_unsaved:'Folders changed in settings are not saved yet — the 🔄 update button saves them first (or 💾 to only save)',

paused_prefix:'⏸ Paused (not an error): ',

repair_ask:'"__P__" is gone. These folders look like where it moved — which one?',

repair_ask_tail:'Click one to update; if none of them, use the line below to skip.',

repair_done:'Done — updating from the new location, one moment…',

repair_none:'None of these — leave it for now',

e_prune_scope_loss:'The whole folder “__P__” is gone and __N__ indexed files under it vanished too — paused, nothing deleted. It was probably moved or renamed: run an update to let it find the new location; if you really deleted it, run 【强制清理.bat】.',

e_scope_gone:'The folders to index are missing: {p}. Update stopped, nothing in the index was touched. Check that the folder is still where it was (moved it?), or remove it from the index scope in settings.',

e_prune_bulk:'About to remove {n} of {total} entries — paused, nothing deleted. This usually means the index scope is stale, the drive letter changed, or permissions failed. Check the folders in your scope first; if you really deleted these files, run 【强制清理.bat】 to clean anyway.',

e_need_dir:'At least one folder is required',

e_discover_busy:'A scan is already running',

e_selftest_busy:'A health check is already running',

e_update_busy:'An update is already running',

e_scope_save:'Could not save scope.json (read-only / locked / bad path) — update did not start',

e_busy:'Another task is already running (update / names / scan / selftest) — wait for it',

e_bad_body:'Malformed request body',

e_query:'Query error (3+ characters works best): {e}'

}};

const $=id=>document.getElementById(id);

const qEl=$('q'),dEl=$('dp'),rEl=$('r'),pvEl=$('pv'),fEl=$('fbar');

/* 顶部原来那两个「类型 / 路径」输入框并进了左侧栏：类型 = 左边的分类（带计数），

   路径 = 侧栏底部这一个输入框。这里保留 tEl=null，go() 里 tv 恒为空。 */

const tEl=null;

let PATH_T=null;

function onPathInput(){

  const c=$('dpclr'); if(c) c.style.visibility=dEl.value.trim()?'visible':'hidden';

  clearTimeout(PATH_T); PATH_T=setTimeout(()=>{ if(qEl.value.trim()) go(); },350);

}

function clearPath(){ dEl.value=''; onPathInput(); }

let RLIST=[],RCUR=-1,MASTER=[],KIND='all',DAYS=0,PV_ON=false;

let LANG=(function(){

const q=(new URLSearchParams(location.search)).get('lang');   // ?lang=en 可强制指定语言

if(q==='zh'||q==='en')return q;

try{const v=localStorage.getItem('sb_lang');if(v==='zh'||v==='en')return v}catch(e){}

return (navigator.language||'zh').toLowerCase().indexOf('zh')===0?'zh':'en'})();

const T=k=>{const d=DICT[LANG];return d[k]!==undefined?d[k]:(DICT.zh[k]!==undefined?DICT.zh[k]:k)};

const F=(s,a)=>String(s).replace(/\{(\w+)\}|__(\w+)__/g,(m,k,k2)=>{

  const kk=(k||k2); if(!a)return m;

  if(a[kk]!==undefined)return a[kk];

  const lo=kk.toLowerCase(); return a[lo]!==undefined?a[lo]:m});

const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

let HAS_RESULT=0;

let AT_HOME=0;

let SEQ=0;   // 输入序号：晚到的旧结果直接丢掉

let SORT='rel';   // 排序：rel/new/old/big/name —— Ctrl+S 循环，▦ 菜单里也能点

const SORT_SEQ=['rel','new','old','big','name'];

   // 首页时间轴模式：applyLang 重跑时不要把它冲掉

function applyLang(){

document.documentElement.lang=(LANG==='zh'?'zh-CN':'en');

document.title=T('title');

document.querySelectorAll('[data-i18n]').forEach(el=>{el.innerHTML=T(el.dataset.i18n)});

document.querySelectorAll('[data-i18n-ph]').forEach(el=>{el.placeholder=T(el.dataset.i18nPh)});

document.querySelectorAll('[data-i18n-title]').forEach(el=>{el.title=T(el.dataset.i18nTitle)});

const sl=$('sel_lang');if(sl)sl.value=LANG;

draw();

if(HAS_RESULT&&qEl.value.trim())go(); else if(!HAS_RESULT&&!AT_HOME)showEmpty();

if($('pm').textContent&&DIRS.length)pm.textContent='';

fillAbout();

}

function toggleLang(){LANG=(LANG==='zh'?'en':'zh');try{localStorage.setItem('sb_lang',LANG)}catch(e){}

applyLang();try{if(PROG_CACHE)renderProg(PROG_CACHE)}catch(e){}loadNames()}

function showEmpty(){rEl.innerHTML='<div class=empty>'+esc(T('empty_hint'))+'<br>'+T('empty_tip')+'</div>';pvEmpty();updateCount(0)}

// —— 首页时间轴：不打字也能看见「今天/昨天动过什么」。用户真正的问题是「我的东西去哪了」。

let TLINE=[];

async function goHome(days){

AT_HOME=1;HAS_RESULT=0;RLIST=[];RCUR=-1;fEl.innerHTML='';updateCount(0);

const my=++SEQ;

rEl.innerHTML='<div class=empty>'+esc(T('searching'))+'</div>';

let d;

try{d=await(await fetch('/api/timeline?days='+(days||7))).json()}catch(e){if(my===SEQ&&AT_HOME)showEmpty();return}

// 晚到的时间轴不许盖掉已经打出来的搜索结果：以前启动那一刻打字，半秒后结果被时间轴冲掉。

if(my!==SEQ||!AT_HOME)return;

const gs=(d&&d.groups)||[];

if(!gs.length){showEmpty();return}

TLINE=[];

let h='<div class=tlwrap>';

gs.forEach(g=>{

h+='<div class=tlhead>'+esc(g.label)+'<span class=tln>'+g.n+(g.more?' · '+esc(T('tl_cap').replace('__N__',String(g.items.length))):'')+'</span></div>';

g.items.forEach(it=>{

const i=TLINE.length;

TLINE.push({ty:'file',name:it.n,dn:it.n,path:it.p,dir:it.d||'',e:it.e||'',

ext:(it.e||'').replace('.','').toUpperCase().slice(0,4),snip:'',

k:kindOf(it.p,it.e||'',false),mt:it.m||0,s:it.s});

h+='<div class=tlrow data-i="'+i+'" onclick="tlPick('+i+')" ondblclick="openF(\''+enc(it.p)+'\')">'+

'<div class=tlic>'+esc((it.e||'').replace('.','').toUpperCase().slice(0,4)||'FILE')+'</div>'+

'<div class=tlnm title="'+esc(it.n)+'">'+esc(it.n)+'</div>'+

'<div class=tldir title="'+esc(it.d)+'">'+esc(it.d)+'</div>'+

'<div class=tlt>'+(it.m?esc(fmtT(it.m)):'')+'</div></div>'});

});

h+='<div class=tlmore onclick="goHome(30)">'+esc(T('tl_more'))+'</div>';

h+='</div>';

rEl.innerHTML=h}

function tlPick(i){const r=TLINE[i];if(!r)return;

rEl.querySelectorAll('.tlrow').forEach(el=>el.classList.toggle('on',+el.dataset.i===i));

pvRender(r)}

// —— 右键菜单：打开 / 定位 / 复制路径 / 打包

let CTX_I=-1;

function ctxHide(){const m=document.getElementById('ctxm');if(m)m.remove()}

function ctxShow(i,x,y){

ctxHide();CTX_I=i;const r=RLIST[i];if(!r)return;

const m=document.createElement('div');m.id='ctxm';m.className='ctxm';

const it=(t,fn)=>'<div class=ctxi onclick="'+fn+'">'+esc(t)+'</div>';

m.innerHTML=it(T('c_open'),'ctxAct(0)')+it(T('c_reveal'),'ctxAct(1)')+

it(T('c_copy'),'ctxAct(2)')+it(T('c_zip'),'ctxAct(3)');

m.style.left=Math.max(4,Math.min(x,innerWidth-190))+'px';

m.style.top=Math.max(4,Math.min(y,innerHeight-170))+'px';

document.body.appendChild(m);

setTimeout(()=>document.addEventListener('click',ctxHide,{once:true}),0)}

function ctxAct(k){

const r=RLIST[CTX_I];ctxHide();if(!r)return;

if(k===0){openF(enc(r.path))}

else if(k===1){revealPath(r.path)}

else if(k===2){copyPath(r.path)}

else if(k===3){zipPaths([r.path])}}

function revealPath(p){

try{if(window.pywebview&&window.pywebview.api&&window.pywebview.api.reveal){

window.pywebview.api.reveal(p).then(ok=>toast(ok?T('revealed'):T('open_fail')));return}}catch(e){}

toast(T('open_fail'))}

function copyPath(p){

try{navigator.clipboard.writeText(p).then(()=>toast(T('copied')));return}catch(e){}

toast(p)}

async function zipPaths(ps){

try{const r=await(await fetch('/api/zip',{method:'POST',headers:{'Content-Type':'application/json'},

body:JSON.stringify({paths:ps})})).json();

toast(r&&r.ok?F(T('zip_ok'),{n:r.n}):T('zip_fail'))}catch(e){toast(T('zip_fail'))}}

rEl.addEventListener('contextmenu',e=>{

const el=e.target.closest&&e.target.closest('[data-i]');if(!el)return;

e.preventDefault();const i=+el.dataset.i;

if(i>=0&&i<RLIST.length){selectRow(i,false);ctxShow(i,e.clientX,e.clientY)}});

// —— 反查：拖进来的文件在库里是什么情况（几份、同名版本在哪、同目录还有谁）

async function doReverse(p){

if(!p)return;

let d;

try{d=await(await fetch('/api/reverse',{method:'POST',headers:{'Content-Type':'application/json'},

body:JSON.stringify({p:p})})).json()}catch(e){d={ok:false}}

if(!d||!d.ok){toast(T('rev_none'));return}

PV_ON=1;const b=$('pvbtn');if(b)b.classList.add('on');

pvEl.className='preview on';

let h='<div class=pv-title>'+esc(T('rev_title'))+'</div>'; 

h+='<div class=pv-meta><b>'+esc(d.n||'')+'</b><br>'+esc(d.p||'')+'</div>';

h+='<div class=pv-snip>'+(d.in_index

?esc(T('rev_in'))+'<br>'+esc(String(d.idx||'').replace('T',' '))

:T('rev_out'))+'</div>';

if(d.same&&d.same.length){

h+='<div class=revsec>'+F(T('same_n'),{n:d.same.length})+'</div>';

d.same.forEach(x=>{h+='<div class=revrow onclick="openF(\''+enc(x.p)+'\')"><b>'+esc(x.n)+

'</b><span class=revdir>'+esc(x.d)+'</span><span class=revt>'+(x.m?esc(fmtT(x.m)):'')+'</span></div>'})}

if(d.same_size&&d.same_size.length){

h+='<div class=revsec>'+esc(T('rev_size'))+'</div>';

d.same_size.forEach(x=>{h+='<div class=revrow onclick="openF(\''+enc(x.p)+'\')"><b>'+esc(x.n)+

'</b><span class=revdir>'+esc(x.p)+'</span></div>'})}

if(d.siblings)h+='<div class=revsec>'+F(T('rev_sib'),{n:d.siblings})+'</div>';

h+='<div class=pv-actions><button class="btn primary" onclick="revealPath(\''+enc(d.p||'')+'\')">'+

esc(T('c_reveal'))+'</button><button class=btn onclick="copyPath(\''+enc(d.p||'')+'\')">'+

esc(T('copy'))+'</button></div>';

pvEl.innerHTML=h}

async function pickReverse(){

try{if(window.pywebview&&window.pywebview.api&&window.pywebview.api.pick_file){

const p=await window.pywebview.api.pick_file();if(p){doReverse(p)}return}}catch(e){}

const p=prompt(T('rev_pick'));if(p&&p.trim()){doReverse(p.trim())}}

window.addEventListener('dragover',e=>{e.preventDefault()});

window.addEventListener('drop',e=>{

e.preventDefault();let p='';

try{const f=e.dataTransfer.files&&e.dataTransfer.files[0];if(f){p=f.path||f.fullPath||''}}catch(err){}

if(!p){try{p=e.dataTransfer.getData('text/plain')||''}catch(err){}}

p=p.replace(/^file:\/\/\//,'');

if(p&&/^[a-zA-Z]:[\\/]/.test(p)){doReverse(p)}

else{toast(T('rev_need'));pickReverse()}});

// —— 预览里的「同名版本」链（简历 / 简历(1) / 简历-最终版）

let SAME_C={};

function loadSame(p){

const box=document.getElementById('pvsame');if(!box)return;

const hit=SAME_C[p];

if(hit){box.innerHTML=hit;return}

fetch('/api/same?p='+enc(p)).then(r=>r.json()).then(d=>{

const its=(d&&d.items)||[],box2=document.getElementById('pvsame');if(!box2)return;

if(!its.length){box2.innerHTML='<div class=pvsame-h>'+esc(T('same_none'))+'</div>';return}

let h='<div class=pvsame-h>'+F(T('same_n'),{n:its.length})+'</div>';

its.forEach(x=>{h+='<div class=pvsame-row onclick="openF(\''+enc(x.p)+'\')"><span class=pvsame-n>'+

esc(x.n)+'</span><span class=pvsame-t>'+esc(fmtT(x.m))+'</span></div>'});

if(Object.keys(SAME_C).length>300)SAME_C={};

SAME_C[p]=h;box2.innerHTML=h}).catch(()=>{})}

function dymPick(n){qEl.value=String(n||'').replace(/\.[^.]+$/,'');go()}

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

AT_HOME=0;

const my=++SEQ;

const qv=qEl.value.trim(),tv='',dv=(dEl&&dEl.value.trim())||'';

if(!qv){HAS_RESULT=0;RLIST=[];RCUR=-1;goHome();pvEmpty();fEl.innerHTML='';return}

rEl.innerHTML='<div class=empty>'+esc(T('searching'))+'</div>';

const t0=performance.now();

// 两段式：文件名索引几毫秒就回来，先把这部分画出来；正文检索慢（几百毫秒），

// 回来了再补上。以前两件事 Promise.all 一起等，用户敲完字要盯着空白等半秒，

// 就会觉得「主窗口不会边打边搜」——快搜窗因为只查文件名，反而显得很灵。

const pFiles=fetch('/api/files?q='+encodeURIComponent(qv)+'&s='+SORT).then(r=>r.json()).catch(()=>({items:[],st:'off'}));

const pBody=fetch('/api?q='+encodeURIComponent(qv)+'&t='+encodeURIComponent(tv)+'&d='+encodeURIComponent(dv)+'&name=0&s='+SORT).then(r=>r.json());

let fx,filesMs=0,x=null,err=null;

try{fx=await pFiles}catch(e){fx={items:[],st:'off'}}

if(my!==SEQ)return;                      // 已经有更新的输入了，这份结果作废

filesMs=performance.now()-t0;

paintRes(null,fx,filesMs);

try{x=await pBody}catch(e){err=e}

if(my!==SEQ)return;

if(err){HAS_RESULT=0;rEl.innerHTML='<div class=empty>'+esc(String(err))+'</div>';return}

paintRes(x,fx,performance.now()-t0);

}

// 把「文件名结果」与「正文结果」画到同一张表上。x 为 null 表示正文还没回来，

// 这时只画文件名部分；正文到了再整体重画一次（纯内存操作，不会闪）。

function paintRes(x,fx,ms){

if(x&&x.err){HAS_RESULT=0;rEl.innerHTML='<div class=empty>'+esc(x.k?F(T(x.k),x.a||{e:x.err}):x.err)+'</div>';return}

HAS_RESULT=1;

const hits=(x&&x.hits)||[];

const hp=new Set(hits.map(h=>String(h.p).toLowerCase()));

const files=(fx&&fx.items)?fx.items.filter(f=>!hp.has(String(f.p).toLowerCase())):[];

MASTER=[];

const fxSt=fx&&fx.st;

if(files.length){MASTER.push({ty:'head',text:hits.length

 ? esc(T('ev_h'))+'<em>'+esc(T(fxSt==='ok'?'ev_sub':'ev_sub_off'))+'</em>'

 : F(T('ev_only'),{n:files.length})});

files.forEach(f=>{const isDir=f.t==='dir'||f.t==='folder';

const e=isDir?'':(f.e||('.'+((f.n.split('.').pop()||'').toLowerCase()))||'');

MASTER.push({ty:'file',name:f.n,dn:f.dn||f.n,path:f.p,dir:f.d||'',e:e,t:isDir?'dir':'',

ext:isDir?'DIR':e.slice(1).toUpperCase().slice(0,4),snip:'',src:f.src==='ev'?'ev':'',

k:kindOf(f.p,e,isDir),mt:f.m||0})});

if(fx.more)MASTER.push({ty:'more',text:T('ev_more')})}

if(files.length&&hits.length)MASTER.push({ty:'head',text:'📄 '+esc(T('f_doc'))+' <em>'+hits.length+'</em>'});

hits.forEach(h=>{const i=h.p.lastIndexOf('\\'),nm=i<0?h.p:h.p.slice(i+1),pt=i<0?'':h.p.slice(0,i),

e='.'+(h.p.split('.').pop()||'').toLowerCase();

MASTER.push({ty:'hit',name:nm,dn:nm,path:h.p,dir:pt,e:e,t:'',ext:e.slice(1).toUpperCase().slice(0,4),

snip:h.s,src:'',k:'doc',mt:h.m||0})});

const rows=MASTER.filter(r=>r.ty==='file'||r.ty==='hit').length;

if(!rows){

if(x){                                   // 正文也回来了，才算「真的没有」

const dym=(x&&x.dym)||[];

rEl.innerHTML='<div class=empty>'+F(T('no_hit'),{q:esc((x&&x.q)||qEl.value.trim())})+'<br>'+T('no_hit_tip')+'<div class=qsyn>'+esc(T('qsyn_tip'))+'</div></div>'+

(dym.length?'<div class=dym><div class=dymh>'+esc(T('dym_prefix'))+'</div>'+

dym.map(d=>'<span class=dymc onclick="dymPick(\''+enc(d.n)+'\')">'+esc(d.n)+'</span>').join('')+'</div>':'');

RCUR=-1;pvEmpty();updateCount(0);return}

return                                // 文件名没命中、正文还在路上：先留着「正在搜索」，别急着报空

}

renderFilter(hits.length,files.length,ms?ms.toFixed(0):'—');rebuild()}

// 服务端拿系统图标失败时回 204（不缓存失败，等下次请求再试），可 <img> 自己不会重试，

// 于是首屏刚起来那一下会满屏兜底图标。这里补两次带时间戳的重试，还取不到就留着兜底。

let ICON_RETRY_T=0;

function iconRetry(root,delays){

clearTimeout(ICON_RETRY_T);

let i=0;

const tick=function(){

const bad=[].slice.call(root.querySelectorAll('img.ic')).filter(function(im){

return im.parentNode&&im.parentNode.classList.contains('noic')});

bad.forEach(function(im){

const s=im.getAttribute('src');

if(!s)return;

im.onload=function(){im.parentNode.classList.remove('noic')};

im.src=s+(s.indexOf('?')>=0?'&':'?')+'r='+(Date.now()%1e6)});

if(++i<delays.length)ICON_RETRY_T=setTimeout(tick,delays[i])};

ICON_RETRY_T=setTimeout(tick,delays[0])}

let VIEW=(function(){try{return localStorage.getItem('sb_view')||'list'}catch(e){return 'list'}})();

function viewMenu(ev){

  const m=$('vmenu'),b=$('viewbtn');if(!m||!b)return;

  if(m.classList.contains('on')){m.classList.remove('on');return}

  const r=b.getBoundingClientRect();vSync();

  m.style.left=Math.max(8,Math.min(window.innerWidth-196,r.right-172))+'px';

  m.style.top=(r.bottom+6)+'px';m.classList.add('on');

  setTimeout(()=>document.addEventListener('click',vCloseOnce,{once:true}),0);

}

function vCloseOnce(e){const m=$('vmenu');if(m&&!m.contains(e.target))m.classList.remove('on')}

function vSync(){

  const a=$('vm_list'),b=$('vm_grid'),c=$('vm_pv');

  if(a)a.textContent=VIEW==='list'?'✓':'';

  if(b)b.textContent=VIEW==='grid'?'✓':'';

  if(c)c.textContent=PV_ON?'✓':'';

  const t=$('vm_sortt');

  if(t)t.textContent=T('vmenu_sort')+'：'+T('sort_'+SORT);

}

function cycleSort(){

  SORT=SORT_SEQ[(SORT_SEQ.indexOf(SORT)+1)%SORT_SEQ.length];

  vSync();

  try{localStorage.setItem('sb_sort',SORT)}catch(e){}

  if(HAS_RESULT)go();

  else toast(T('sort_now')+T('sort_'+SORT));

}

function vPick(k){

  const m=$('vmenu');if(m)m.classList.remove('on');

  if(k==='pv'){togglePv();return}

  if(k==='sort'){cycleSort();return}

  setView(k);

}

// 长文件名中间省略、保留扩展名：「…项目周报-第二版.xlsx」比「…周报-第二版.xl…」有用得多

function midName(str,max){

  str=String(str||'');max=max||44;

  if(str.length<=max)return str;

  const dot=str.lastIndexOf('.');

  const ext=(dot>0&&str.length-dot<=6)?str.slice(dot):'';

  const base=ext?str.slice(0,dot):str;

  const keep=Math.max(10,max-ext.length-1);

  const head=Math.max(6,Math.ceil(keep*0.62)), tail=Math.max(3,keep-head);

  return base.slice(0,head)+'…'+base.slice(base.length-tail)+ext;

}

// Ctrl+1..9：直接打开当前列表第 N 项（WinShun / Listary 都这么做）

function openRowN(n){

  const rows=RLIST.filter(r=>r.ty==='file'||r.ty==='hit');

  const r=rows[n-1];

  if(r&&r.path)openF(r.path);

}

function setView(v){

VIEW=v==='grid'?'grid':'list';

document.body.classList.toggle('gridmode',VIEW==='grid');

try{localStorage.setItem('sb_view',VIEW)}catch(e){}

const b=$('viewbtn');

vSync();

rebuild()}

function renderGrid(){

const qv=qEl.value.trim();

let html='<div class=rgrid>';

RLIST.forEach((r,i)=>{

const on=i===RCUR?' on':'';

const isDir=r.t==='dir';

const e=(r.e||'').toLowerCase();

const thumbable=!isDir&&/^\.(jpg|jpeg|png|gif|webp|bmp|tif|tiff|pdf|mp4|mkv|avi|mov|wmv|flv|m4v|ts|webm|mpg|mpeg)$/.test(e);

let media;

const icq=isDir?'d=1':'e='+encodeURIComponent(r.e||'')+((r.e==='.exe'||r.e==='.lnk')&&r.path?'&p='+encodeURIComponent(r.path):'');

const iconImg='<img loading=lazy class=gic src="/api/icon?'+icq+'" alt="" onerror="this.style.display=\'none\'">';

if(thumbable){

// 缩略图叠在类型图标上：取到了就把下面的图标藏起来；取不到（手机存储没挂、缺 codec）

// 至少显示类型图标，不至于留一个空白框

media=iconImg+'<img loading=lazy class=gthumbimg src="/api/thumb?s=1&p='+enc(r.path)+'" alt=""'

+' onload="this.previousElementSibling&&(this.previousElementSibling.style.display=\'none\')"'

+' onerror="this.style.display=\'none\'">';

}else{

media=iconImg;

}

html+='<div class="gitem'+on+'" data-i="'+i+'" onclick="clickRow('+i+')" ondblclick="openF(\''+enc(r.path)+'\')" title="'+esc(r.path)+'">'+

'<div class=gthumb>'+media+'</div>'+

'<div class=gname>'+(()=>{const g=String(r.dn||r.name);return g.length>24?esc(midName(g,24)):hl(g,qv)})()+'</div>'+

'<div class=gpath>'+esc(r.dir||'')+'</div></div>'});

rEl.innerHTML=html+'</div>'}

function renderList(){

let html='';

RLIST.forEach((r,i)=>{

if(r.ty==='head'){html+='<div class=evhead>'+r.text+'</div>';return}

if(r.ty==='more'){html+='<div class=evmore>'+esc(r.text)+'</div>';return}

const on=i===RCUR?' on':'';

const qv=qEl.value.trim();

const rn=String(r.dn||r.name);const nm=rn.length>36?esc(midName(rn,36)):hl(rn,qv);

const isDir=r.t==='dir';

const icq=isDir?'d=1':'e='+encodeURIComponent(r.e||'')

+((r.e==='.exe'||r.e==='.lnk')&&r.path?'&p='+encodeURIComponent(r.path):'');

const snipHtml=r.snip?'<div class=sub>'+hl(r.snip,qv)+'</div>':'';

// ico HTML：真图标 + SVG 兜底（学快搜窗的做法，避免单纯 <img> 失败留空白）

const fbSvg=isDir

?'<svg class=fic viewBox="0 0 24 24"><path d="M2.6 7.1c0-1 .8-1.8 1.8-1.8h4c.5 0 1 .2 1.3.6l1.3 1.5h8.7c1 0 1.8.8 1.8 1.8v9.4c0 1-.8 1.8-1.8 1.8H4.4c-1 0-1.8-.8-1.8-1.8z" fill="#f4bd4e"/></svg>'

:(function(){var c='#a3a9b6';var tc={'.pdf':'#e9573f','.docx':'#2b579a','.doc':'#2b579a','.xlsx':'#107c41','.xls':'#107c41','.pptx':'#d24726','.zip':'#f0a020','.txt':'#7a8294'};if(tc[r.e])c=tc[r.e];return '<svg class=fic viewBox="0 0 24 24"><path d="M4.8 2.6h8.4L18.8 8.3v13.1H4.8z" fill="#fff" stroke="#c9cfdb" stroke-width="1.15" stroke-linejoin="round"/><path d="M13.2 2.6v5.7h5.6" fill="none" stroke="#c9cfdb" stroke-width="1.15" stroke-linejoin="round"/><rect x="7.2" y="11.7" width="9" height="2.6" rx="1.3" fill="'+c+'" opacity=".92"/><rect x="7.2" y="16" width="5.8" height="2.6" rx="1.3" fill="'+c+'" opacity=".45"/></svg>';})();

// 注意：外层这层 .ritem 是行的「容器」，CSS 全是按 .ritem 后代写的，

// 少这一层整行会塌成裸的图标+文字（0.7 开发期踩过：列表变成一张巨型文件图标）

const mt=r.mt?fmtT(r.mt):'';

html+='<div class="ritem'+on+'" data-i="'+i+'" onclick="clickRow('+i+')"'+' ondblclick="openF(\''+enc(r.path)+'\')">'+

'<div class=ico><img class=ic src="/api/icon?'+icq+'" alt="" onerror="this.parentNode.classList.add(\'noic\')">'+fbSvg+'</div>'+

'<div class=nm title="'+esc(r.name)+'">'+nm+(r.src==='ev'?' <span class=evtag>'+esc(T('ev_tag'))+'</span>':'')+'</div>'+

'<div class=pp title="'+esc(r.path)+'">'+esc(r.dir)+'</div>'+

'<div class="mt'+(mt?'':' none')+'">'+esc(mt||'—')+'</div>'+

snipHtml+'</div>'});

rEl.innerHTML=html;

iconRetry(rEl,[260,700,1500,2800,4600])}

const KIND_TBL={

dir:['.dir'],

doc:['.pdf','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.txt','.md','.csv','.rtf','.epub','.wps','.et','.dps','.ofd'],

img:['.jpg','.jpeg','.png','.gif','.webp','.bmp','.tif','.tiff','.heic','.svg'],

audio:['.mp3','.wav','.flac','.aac','.m4a','.ogg','.wma','.ape','.amr'],

zip:['.zip','.rar','.7z','.tar','.gz','.bz2','.iso','.cab'],

video:['.mp4','.mkv','.avi','.mov','.wmv','.flv','.m4v','.ts','.webm','.rmvb','.mpg','.mpeg'],

app:['.exe','.lnk','.bat','.cmd','.ps1','.appref-ms','.msi','.com']};

function kindOf(p,e,isDir){

if(isDir)return 'dir';e=(e||'').toLowerCase();

for(const k in KIND_TBL){if(KIND_TBL[k].indexOf(e)>=0)return k}

return 'file'}

function fmtT(ts){if(!ts)return '';const d=new Date(ts*1000),z=n=>String(n).padStart(2,'0');

return d.getFullYear()+'/'+(d.getMonth()+1)+'/'+d.getDate()+' '+z(d.getHours())+':'+z(d.getMinutes())}

function pickKind(k,el){KIND=k;document.querySelectorAll('[data-kind]').forEach(x=>x.classList.toggle('on',x===el));rebuild()}

function pickDays(n,el){DAYS=n;document.querySelectorAll('[data-days]').forEach(x=>x.classList.toggle('on',x===el));rebuild()}

function togglePv(){PV_ON=!PV_ON;const b=$('pvbtn');if(b)b.classList.toggle('on',PV_ON);

  vSync();

if(PV_ON&&RCUR>=0)showPreview(RCUR);else pvEmpty()}

function updateCount(n){const el=$('nitems');if(el&&el.firstChild)el.firstChild.nodeValue=String(n)+' '}

function rebuild(){

const all=MASTER.filter(r=>r.ty==='file'||r.ty==='hit');

document.querySelectorAll('[data-kind]').forEach(el=>{const k=el.dataset.kind,b=$('ck_'+k);

if(!b)return;const n=k==='all'?all.length:all.filter(r=>r.k===k).length;b.textContent=n||''});

const now=Date.now()/1000;

let rows=all;

if(KIND!=='all')rows=rows.filter(r=>r.k===KIND);

if(DAYS>0)rows=rows.filter(r=>r.mt&&(now-r.mt)<=DAYS*86400);

// 没加筛选时保留「文件名命中 / 文档」两段分区头；一旦筛选就只列结果

RLIST=(KIND==='all'&&DAYS===0&&VIEW!=='grid')?MASTER.slice():rows;

RCUR=RLIST.findIndex(r=>r.ty==='file'||r.ty==='hit');

updateCount(rows.length);

if(!rows.length){rEl.innerHTML='<div class=empty>'+esc(T('filter_empty'))+'</div>';RCUR=-1;pvEmpty();return}

if(VIEW==='grid')renderGrid();else renderList();

if(RCUR>=0&&PV_ON)showPreview(RCUR)}

(function(){const b=$('viewbtn');if(b){b.title=T('view_menu')}

document.body.classList.toggle('gridmode',VIEW==='grid')})();

function renderFilter(hits,files,ms){

const chips=[];

if(hits)chips.push(esc(T('f_doc'))+' <b>'+hits+'</b>');

if(files)chips.push(esc(T('f_file'))+' <b>'+files+'</b>');

fEl.innerHTML=chips.length?'<span class=fchip>'+chips.join('</span><span class=fchip>')+'</span>'+

'<span class=fchip style="cursor:default">'+esc(ms)+'ms</span>':''}

function clickRow(i){selectRow(i,true)}

function selectRow(i,scroll){

if(i<0||i>=RLIST.length)return;RCUR=i;

rEl.querySelectorAll('[data-i]').forEach(el=>el.classList.toggle('on',+el.dataset.i===i));

const el=rEl.querySelector('[data-i="'+i+'"]');

if(el&&scroll)el.scrollIntoView({block:'nearest'});showPreview(i)}

function showPreview(i){

const r=RLIST[i];if(!r||r.ty==='head'||r.ty==='more'){pvEmpty();return}

pvRender(r)}

function pvRender(r){

pvEl.className='preview'+(PV_ON?' on':'');const qv=qEl.value.trim();

const isThumb=/^\.(jpg|jpeg|png|gif|webp|bmp|tif|tiff|pdf|mp4|mkv|avi|mov|wmv|flv|m4v|ts|webm|mpg|mpeg)$/i.test(r.e||'');

const thumbHtml=isThumb?'<div class=pv-thumb><span class=pv-loading>'+esc(T('pv_loading'))+'</span>'

+'<img src="/api/thumb?p='+enc(r.path)+'" onload="var s=this.previousElementSibling;if(s)s.style.display=\'none\'"'

+' onerror="this.parentNode.style.display=\'none\'"></div>':'';

pvEl.innerHTML=thumbHtml+

'<div class=pv-title>'+hl(r.dn||r.name,qv)+'</div>'+

'<div class=pv-meta><b>'+esc(r.ext)+'</b> · '+esc(r.dir)+'</div>'+

(r.snip?'<div class=pv-snip>'+hl(r.snip,qv)+'</div>':'<div class=pv-empty>'+esc(T('pv_nosnip'))+'</div>')+

'<div class=pv-actions><button class="btn primary" onclick="openF(\''+enc(r.path)+'\')">'+esc(T('open'))+'</button>'+

'<button class=btn onclick="copyP(this,\''+enc(r.path)+'\')">'+esc(T('copy'))+'</button>'+

'<button class=btn onclick="zipPaths([\''+enc(r.path)+'\'])">'+esc(T('c_zip'))+'</button></div>'+

'<div id=pvsame class=pvsame></div>';

if(r.ty==='file'||r.ty==='hit'){loadSame(r.path)}}

function pvEmpty(){pvEl.className='preview';pvEl.innerHTML='<div class=pv-empty>'+esc(T('pv_empty'))+'</div>'}

async function openF(p){

// 桌面版优先走原生桥：HTTP 的 /open 只放行文档/媒体类（对 .exe/.lnk 一律 deny，

// 因为 Host/Sec-Fetch-Site 这两道门本机进程都能伪造）。启动应用、打开快捷方式必须

// 走这里 —— window.pywebview.api.open_path 内部同样要过 ev_recent 白名单。

try{

if(window.pywebview&&window.pywebview.api&&window.pywebview.api.open_path){

const ok=await window.pywebview.api.open_path(decodeURIComponent(p));

toast(ok?T('opened'):T('open_fail'));return}}catch(e){}

try{const x=await(await fetch('/open?p='+p)).json();

if(x.ok){toast(T('opened'));return}

if(x.reason==='gone'){toast(T('file_gone'));return}

toast(T('open_fail'))}

catch(e){toast(T('open_fail'))}}

async function copyP(b,p){try{await navigator.clipboard.writeText(decodeURIComponent(p));b.textContent=T('copied');setTimeout(()=>{b.textContent=T('copy')},1200)}

catch(e){toast(decodeURIComponent(p))}}

function moveCur(d){

if(!RLIST.length)return;let i=RCUR;

do{i+=d}while(i>=0&&i<RLIST.length&&(RLIST[i].ty==='head'||RLIST[i].ty==='more'));

if(i>=0&&i<RLIST.length)selectRow(i,true)}

function openCur(){const r=RLIST[RCUR];if(r&&(r.ty==='file'||r.ty==='hit'))openF(enc(r.path))}

async function revealCur(){const r=RLIST[RCUR];if(!r||(r.ty!=='file'&&r.ty!=='hit'))return;

try{if(window.pywebview&&window.pywebview.api&&window.pywebview.api.reveal){

const ok=await window.pywebview.api.reveal(r.path);toast(ok?T('revealed'):T('open_fail'));return}}catch(e){}

toast(T('open_fail'))}

qEl.addEventListener('keydown',e=>{

// 输入法正在拼字时的 Enter 是「上屏」，不是「搜索」——不挡会直接执行第一个结果

if(e.isComposing||e.keyCode===229)return;

if((e.ctrlKey||e.metaKey)&&(e.key==='s'||e.key==='S')){e.preventDefault();cycleSort();return}

if((e.ctrlKey||e.metaKey)&&/^[1-9]$/.test(e.key)){e.preventDefault();openRowN(+e.key);return}

if(e.key==='Enter'){e.preventDefault();if(e.ctrlKey||e.metaKey){revealCur()}else if(HAS_RESULT&&RCUR>=0){openCur()}else{go()}return}

if(e.key==='ArrowDown'){e.preventDefault();moveCur(1);return}

if(e.key==='ArrowUp'){e.preventDefault();moveCur(-1);return}});

// 边打边搜：以前主窗口只在 Enter 时查，快搜窗却实时出结果，两个窗口像两套软件。

// 这里补一个 180ms 防抖；中文输入法拼字期间（composition）不触发，避免拿拼音去搜。

let QDT=0,QCOMP=0;

qEl.addEventListener('compositionstart',()=>{QCOMP=1});

qEl.addEventListener('compositionend',()=>{QCOMP=0;clearTimeout(QDT);QDT=setTimeout(liveSearch,120)});

qEl.addEventListener('input',()=>{

if(QCOMP)return;

clearTimeout(QDT);

QDT=setTimeout(liveSearch,180)});

function liveSearch(){

const qv=qEl.value.trim();

if(!qv){SEQ++;HAS_RESULT=0;RLIST=[];RCUR=-1;goHome();pvEmpty();fEl.innerHTML='';return}

if(!HAS_RESULT&&!qEl.value)return;

go()}

document.addEventListener('keydown',e=>{

if(e.key==='Escape'){if($('help').classList.contains('on')){hideHelp()}}if(e.key==='Escape'){if($('cfg').classList.contains('on')){hideCfg()}else if(document.activeElement===qEl){qEl.value='';HAS_RESULT=0;RLIST=[];RCUR=-1;goHome();pvEmpty();fEl.innerHTML=''}}

if((e.ctrlKey||e.metaKey)&&e.key==='f'){e.preventDefault();qEl.focus();qEl.select()}

if((e.ctrlKey||e.metaKey)&&(e.key==='s'||e.key==='S')){e.preventDefault();cycleSort()}

// 键盘契约：页脚写着「↑↓ 选择 · Enter 打开」，可这两个键以前只绑在搜索框上——

// 鼠标点过一行、焦点离开输入框之后，键位就全失效了。这里补一条全局兜底。

const _inp=(document.activeElement&&/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName));

if(!_inp&&HAS_RESULT&&RLIST.length){

 if(e.key==='ArrowDown'){e.preventDefault();moveCur(1);return}

 if(e.key==='ArrowUp'){e.preventDefault();moveCur(-1);return}

 if(e.key==='Enter'){e.preventDefault();if(e.ctrlKey||e.metaKey){revealCur()}else{openCur()}return}

 if((e.ctrlKey||e.metaKey)&&/^[1-9]$/.test(e.key)){e.preventDefault();openRowN(+e.key);return}}});

let DIRS=[],EXC=[],tid=0,PROG_CACHE=null,CFG_DIRTY=false;

async function cfgLoad(){try{const c=await(await fetch('/api/config')).json();DIRS=c.include_dirs||[];EXC=c.exclude_dirs||[];

if(!c.exists){showCfg(1);pm.textContent=T('first_run');

// 首启的两个坑：①🚀 智能扫描藏在「维护工具」折叠区里，新人根本看不到；

// ②「索引范围」也收着，看到一片空白不知道下一步做什么。首启把两个都展开，并给 🚀 加呼吸高亮。

try{$('scope_details').open=true;$('maint_details').open=true;const _sb=$('smartbtn');if(_sb)_sb.classList.add('hot')}catch(e){}

draw()}}catch(e){}loadNames();loadAutostart();loadSettings();setTimeout(maybeFirstRun,600);setTimeout(maybeWelcome,1100);

setTimeout(()=>{try{fetch('/api/ping').then(r=>r.json()).then(v=>{

  if(v&&v.fallback&&v.dir)toast(F(T('data_fallback'),{p:v.dir}));

}).catch(function(){})}catch(e){}},1800)}

async function loadAutostart(){try{const r=await(await fetch('/api/autostart')).json();

$('tg_auto').classList.toggle('on',!!r.on)}catch(e){}}

async function loadSettings(){try{const r=await(await fetch('/api/settings')).json();

$('tg_hotkey').classList.toggle('on',r.hotkey!==false);

const st=$('sel_theme');if(st)st.value=r.theme||'auto';

THEME=r.theme||'auto';

applyTheme(r.theme||'auto');

if(r.lang){LANG=r.lang;try{localStorage.setItem('sb_lang',LANG)}catch(e){}applyLang()}

else{applyLang()}}catch(e){applyLang()}}

function showCfg(wizard){$('cfg').classList.add('on');$('mask').classList.add('on');if(wizard)$('gear').style.display='none'}

function hideCfg(){$('cfg').classList.remove('on');$('mask').classList.remove('on');$('gear').style.display='';

// 关抽屉时把没保存的改动说出来（以前这行写在函数体外面，等于永远不提醒）

if(CFG_DIRTY){CFG_DIRTY=false;toast(T('cfg_unsaved'))}}

function showAbout(){$('help').classList.remove('on');$('hmask').classList.remove('on');try{hideWelcome(0)}catch(e){};$('about').classList.add('on');$('amask').classList.add('on')}

function hideAbout(){$('about').classList.remove('on');$('amask').classList.remove('on')}

function showHelp(){$('about').classList.remove('on');$('amask').classList.remove('on');try{hideWelcome(0)}catch(e){};$('help').classList.add('on');$('hmask').classList.add('on')}

function hideHelp(){$('help').classList.remove('on');$('hmask').classList.remove('on')}

function showPanel(n){try{if(n==='help')showHelp();else if(n==='about')showAbout();else if(n==='cfg'){loadSettings();showCfg()}}catch(e){}}

// —— 关于弹窗里的三件事：数字跟着语言走、检查更新

function tOf(o,k){const en=(LANG==='en');const v=(en&&o[k+'_en'])?o[k+'_en']:o[k];return v||''}

function openDataDir(){try{fetch('/api/open_dir',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})}catch(e){}}

function fillAbout(){

  const m=$('ab_meta')||$('ab_meta_s');if(!m)return;

  const nf=m.dataset.nf||'',nt=m.dataset.nt||'',bd=m.dataset.build||'';

  const d=$('ab_docs');if(d)d.innerHTML=F(T('about_docs'),{t:nt,n:nf});

  const b=$('ab_build');if(b)b.innerHTML=F(T('about_build'),{b:bd});

  const dp=$('ab_dirp');

  if(dp&&!dp.textContent)try{fetch('/api/ping').then(r=>r.json()).then(v=>{

    if(v&&v.dir){dp.textContent=v.dir;dp.style.fontWeight='400';}

  }).catch(function(){})}catch(e){}

}

let UPD=null;

async function checkUpdate(){

  const msg=$('ab_msg');if(!msg)return;

  msg.innerHTML=esc(T('about_checking'));

  let d=null;

  try{d=await(await fetch('/api/update_check',{cache:'no-store'})).json()}catch(e){d=null}

  if(!d||!d.ok){msg.innerHTML=esc(T('about_fail'));return}

  UPD=d;

  if(!d.newer||d.legacy){

    msg.innerHTML=esc(F(T('about_latest'),{v:(d.latest||'?')}));

    return;

  }

  let h=esc(F(T('about_new'),{v:(d.latest||'?')}));

  if(d.notes)h+='<div class=upd>'+esc(String(d.notes).slice(0,1200))+'</div>';

  h+='<button class="pbtn" id=ab_do onclick=applyUpdate()>'+esc(T('about_apply'))+'</button>';

  var _pg=String(d.page||'');var _okp=/^https:\/\/[A-Za-z0-9._~:\/?#\[\]@!$&'()*+,;=%-]+$/.test(_pg);

  if(_okp)h+='<div style="margin-top:7px"><a href="'+esc(_pg)+'" target="_blank" rel="noreferrer" style="color:var(--acc)">'+esc(T('about_page'))+'</a></div>';

  msg.innerHTML=h;

}

async function applyUpdate(){

  const msg=$('ab_msg');if(!msg)return;

  msg.innerHTML=esc(T('about_applying'));

  let d=null;

  try{

    d=await(await fetch('/api/update_apply',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();

  }catch(e){d=null}

  if(!d){msg.innerHTML=esc(T('upd_net'));return}

  if(!d.ok){msg.innerHTML=esc(T(d.k||'upd_net'));return}

  msg.innerHTML=esc(F(T('upd_done'),{v:(d.version||'')}));

}

async function toggleAuto(){const el=$('tg_auto');const on=!el.classList.contains('on');

try{const r=await(await fetch('/api/autostart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on:on})})).json();

if(r.ok){el.classList.toggle('on',on);toast(on?T('d_autostart_on'):T('d_autostart_off'))}else{toast(r.err||'failed')}}catch(e){toast('failed')}}

async function toggleHotkey(){const el=$('tg_hotkey');const on=!el.classList.contains('on');

try{const r=await(await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hotkey:on})})).json();

if(r.ok){el.classList.toggle('on',on);toast(on?T('d_hotkey_on'):T('d_hotkey_off')+' (重启生效)')}}catch(e){toast('failed')}}

async function changeLang(l){LANG=l;try{localStorage.setItem('sb_lang',l)}catch(e){}

try{await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lang:l})})}catch(e){}

applyLang()}

function changeTheme(t){THEME=t;applyTheme(t);

try{fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({theme:t})})}catch(e){}}

let THEME='auto';

let _TB_DARK=null,_TB_TRIES=0;

function syncTitlebar(dark){

  _TB_DARK=dark;_TB_TRIES=0;

  const tick=()=>{

    const api=window.pywebview&&window.pywebview.api;

    if(api&&api.set_dark){try{api.set_dark(dark)}catch(e){}return}

    if(_TB_TRIES++<20)setTimeout(tick,250);

  };

  tick();

}

function applyTheme(t){

const dark=(t==='dark')||(t==='auto'&&matchMedia('(prefers-color-scheme:dark)').matches);

document.documentElement.setAttribute('data-theme',dark?'dark':'light');

syncTitlebar(dark);

// 系统主题变化（auto 模式）也要跟着变，不然标题栏一天都不会自己刷新

try{_darkMQ=_darkMQ||matchMedia('(prefers-color-scheme:dark)');_darkMQ.onchange=()=>{if((THEME||'auto')==='auto')applyTheme('auto')}}catch(e){}

}

let _darkMQ=null;

function draw(){const el=$('dirs');if(!el)return;

el.innerHTML=DIRS.map((d,i)=>'<span class=chip>'+esc(d)+' <b onclick=delD('+i+')>✕</b></span>').join('')

||'<span class=pmsg>'+esc(T('no_dir'))+'</span>'}

async function addDir(){let p=null;

// 桌面窗口模式下才有原生文件夹选择器。注意不能用一个全局常量来判断：

// pywebview 注入 window.pywebview 的时机晚于本脚本执行，那时常量还是 false。

const api=window.pywebview&&window.pywebview.api;

if(api&&api.pick_folder){try{p=await api.pick_folder()}catch(e){p=null}}

if(!p)p=prompt(T('add_prompt'));

if(p){p=String(p).replace(/[\\/]+$/,'');if(!DIRS.includes(p))DIRS.push(p);CFG_DIRTY=true;draw()}}

function delD(i){DIRS.splice(i,1);CFG_DIRTY=true;draw()}

let CAND=[];

// —— 欢迎页（只出现一次，学 Listary 那种"刚装好先告诉你两句"） ——

async function maybeWelcome(force){

  if(FR_ACTIVE&&!force)return;   // 首启正在建库：先让人能搜，别用欢迎页盖住

  let r;try{r=await(await fetch('/api/settings')).json()}catch(e){return}

  if(!r||r.welcome_seen)return;

  // 装好那一刻，帮助/关于/设置可能正开着：先收掉，别两层叠在一起

  try{hideHelp();hideAbout();$('cfg').classList.remove('on');$('mask').classList.remove('on')}catch(e){}

  $('welcome').classList.add('on');$('wmask').classList.add('on');

}

function hideWelcome(mark){

  $('welcome').classList.remove('on');$('wmask').classList.remove('on');

  if(mark){try{fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},

    body:JSON.stringify({welcome_seen:true})})}catch(e){}}

}

function welcomeMini(){

  hideWelcome(1);

  try{if(window.pywebview&&pywebview.api&&pywebview.api.open_mini)pywebview.api.open_mini()}catch(e){}

}

// —— 首次运行：不用找按钮，打开就开始建库 ——

// 以前要用户自己钻进设置 → 维护工具 → 🚀 智能扫描，还要在候选目录里勾选。

// 新人十有八九在这一步就以为"这软件搜不到东西"。

let FR_ACTIVE=0,FR_PAUSED=0,FR_TID=0,FR_SHOWN=0;

function frShow(){const b=$('frbar');if(b)b.classList.remove('hide')}

function frHide(){const b=$('frbar');if(b)b.classList.add('hide')}

function frSet(msg,n,pct,ok){

  const b=$('frbar');if(!b)return;

  b.classList.toggle('ok',!!ok);

  $('frmsg').textContent=msg||'';

  if(n!==null&&n!==undefined)$('frn').textContent=Number(n)>0?F(T('fr_files'),{n:Number(n).toLocaleString()}):'';

  $('frpi').style.width=Math.max(0,Math.min(100,pct||0))+'%';

  const pb=$('frpb'); if(pb)pb.style.display=ok?'none':'';

}

async function frToggle(){

  const want=FR_PAUSED?0:1;

  try{const r=await(await fetch('/api/firstrun',{method:'POST',headers:{'Content-Type':'application/json'},

    body:JSON.stringify(want?{pause:1}:{resume:1})})).json();

    if(r&&r.ok!==false){FR_PAUSED=want;$('frpb').textContent=T(want?'fr_bar_resume':'fr_bar_pause')}}

  catch(e){toast(T('net_fail'))}

}

async function maybeFirstRun(){

  try{hideCfg()}catch(e){}

  try{$('mask').classList.remove('on')}catch(e){}

  let r;try{r=await(await fetch('/api/firstrun')).json()}catch(e){return}

  if(!r||!r.need)return;

  FR_ACTIVE=1;frShow();frSet(T('fr_stage_names'),0,2,0);

  $('frpb').textContent=T('fr_bar_pause');

  try{const s0=await(await fetch('/api/firstrun',{method:'POST',headers:{'Content-Type':'application/json'},

    body:'{}'})).json();

    if(s0&&s0.ok===false){frSet(smsg(s0),0,0,0);return}}

  catch(e){frSet(T('net_fail'),0,0,0);return}

  clearInterval(FR_TID);

  FR_TID=setInterval(async()=>{

    let d;try{d=await(await fetch('/api/firstrun')).json()}catch(e){return}

    let st={};try{st=await(await fetch('/api/status')).json()}catch(e){}

    const pct=st&&st.total>0?Math.round(st.done/st.total*100):(d.stage==='names'?8:30);

    const mins=d.eta>0?Math.ceil(d.eta/60):0;

    let line=d.msg||T('fr_busy');

    if(mins>0)line+=' · '+F(T('fr_bar_eta'),{m:mins});

    frSet(line,d.files||0,pct,d.state==='done');

    // 文件名一进库就先把「最近的文件」摆出来：用户在等的时候能看见东西，

    // 也知道这个软件确实在工作，而不是一片空白。

    if(!FR_SHOWN&&(d.files||0)>0&&!qEl.value.trim()){

      FR_SHOWN=1;try{goHome()}catch(e){}

      // 欢迎页不要等到「全盘 + 正文都补完」才弹：那时可能已经几分钟过去了。

      // 文件名一入库（通常十几秒）就让人知道怎么用。

      setTimeout(()=>{try{maybeWelcome(1)}catch(e){}},900);

      setTimeout(()=>{try{toast(T('fr_hint_ctrl'))}catch(e){}},2000);

    }

    if(d.state==='done'){clearInterval(FR_TID);FR_ACTIVE=0;

      frSet(F(T('fr_bar_done'),{n:Number(d.files||0).toLocaleString()}),d.files,pct,1);

      try{fetch('/api/stats').then(r=>r.json()).then(s=>{const el=$('fmid');if(!el||!s)return;el.innerHTML=T('footer_mid').replace('__FILES__',Number(s.files||0).toLocaleString()).replace('__TOTAL__',Number(s.docs||0).toLocaleString())}).catch(function(){})}catch(e){}

      // 扫完了要让人看见「扫到了什么」，不是停在一片空白上

      setTimeout(()=>{try{goHome()}catch(e){}},300);

      // 第一面欢迎页应该在「装好、扫好」之后自动出现，而不是藏在帮助里

      if(!FR_SHOWN){setTimeout(()=>{try{maybeWelcome(1)}catch(e){}},1100);

        setTimeout(()=>{try{toast(T('fr_hint_ctrl'))}catch(e){}},2200);}

      setTimeout(()=>frHide(),8000);

      setTimeout(()=>watchBg(),12000);}

    else if(d.state==='error'){clearInterval(FR_TID);FR_ACTIVE=0;

      frSet(T('fr_fail')+(d.msg||''),d.files,0,0);}

  },1200);

}

async function watchBg(tries){

  tries=tries||0;

  if(tries>40)return;

  let d;try{d=await(await fetch('/api/firstrun')).json()}catch(e){return}

  if(d&&d.bg==='busy'){

    try{const s=await(await fetch('/api/stats')).json();

      if(s)$('fmid')&&($('fmid').textContent=T('footer_mid').replace('__FILES__',Number(s.files||0).toLocaleString())

        .replace('__TOTAL__',Number(s.docs||0).toLocaleString())+' · '+F(T('bg_reading'),{n:Number(s.docs||0).toLocaleString()}));

    }catch(e){}

    setTimeout(()=>watchBg(tries+1),15000);return}

  if(d&&d.bg==='done'){

    try{const s=await(await fetch('/api/stats')).json();

      const el=$('fmid');

      if(el&&s)el.innerHTML=T('footer_mid').replace('__FILES__',Number(s.files||0).toLocaleString())

        .replace('__TOTAL__',Number(s.docs||0).toLocaleString());

      if(s)toast(F(T('bg_done'),{n:Number(s.files||0).toLocaleString()}));

    }catch(e){}

    try{goHome()}catch(e){}

  }

}

async function firstRunSkip(){

  try{await fetch('/api/firstrun',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cancel:1})})}catch(e){}

  clearInterval(tid);$('pw').style.display='none';

  const t=$('frtip');if(t)t.textContent=T('fr_skipped');

}

async function smartScan(b){b.disabled=1;$('pw').style.display='';$('pi').style.width='3%';

pm.textContent=T('scan_hint');

try{const r=await(await fetch('/api/discover',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();

if(r&&r.ok===false){b.disabled=0;$('pw').style.display='none';pm.textContent=smsg(r);return}}

catch(e){b.disabled=0;$('pw').style.display='none';pm.textContent=T('err_prefix')+T('net_fail');return}

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

+'<div class=pmsg>'+esc(T('cand_tail'))+'</div>'

+'<div class=prow style="margin-top:8px"><button class="pbtn" onclick=candGo(this)>'+esc(T('cand_go'))+'</button></div>';

DIRS=gs.filter(g=>!g.noise).map(g=>g.dir);draw();

pm.textContent=T('cand_done')}

function renderRepairAsk(a){cfg.classList.add('on');

$('cand').innerHTML='<div class=pmsg>'+esc(F(T('repair_ask'),{p:a.old}))+'</div>'+

a.items.map((it,i)=>'<label class=crow onclick=repairPick('+i+')><span>'+esc(it.path)+'</span><span class=n>'+it.hits+'/'+it.n+'</span></label>').join('')+

'<div class=pmsg><a href="javascript:repairNone()">'+esc(T('repair_none'))+'</a> · '+esc(T('repair_ask_tail'))+'</div>'}

async function repairPick(i){try{const r=await(await fetch('/api/repair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({i:i})})).json();

if(!r.ok){toast(r.err||T('save_fail'));return}

$('cand').innerHTML='';pm.textContent=T('repair_done');

fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(e=>{});

}catch(e){toast(T('save_fail'))}}

async function repairNone(){try{await fetch('/api/repair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dismiss:true})})}catch(e){}

$('cand').innerHTML='';pm.textContent=T('err_prefix')+T('repair_ask_tail')}

function candGo(b){CFG_DIRTY=false;hideCfg();upd(b)}

function candPick(i,on){const d=CAND[i].dir;

if(on){if(!DIRS.includes(d))DIRS.push(d)}else{DIRS=DIRS.filter(x=>x!==d)}CFG_DIRTY=true;draw()}

async function loadNames(){try{const d=await(await fetch('/api/names')).json();

const el=$('nstat');if(!el)return;

el.innerHTML=d.count>0?F(T('nstat_on'),{n:d.count.toLocaleString()}):esc(T('nstat_off'));

}catch(e){}}

async function buildNames(b){if(b)b.disabled=1;$('pw').style.display='';$('pi').style.width='3%';

pm.textContent=T('m_names');

try{const r=await(await fetch('/api/names',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();

if(!r.ok){pm.textContent=T(r.k)||r.err||'';if(b)b.disabled=0;$('pw').style.display='none';return}}

catch(e){pm.textContent=String(e);if(b)b.disabled=0;$('pw').style.display='none';return}

clearInterval(tid);tid=setInterval(async()=>{let d;

try{d=await(await fetch('/api/names')).json()}catch(e){return}

if(d.state==='busy'){try{const s=await(await fetch('/api/status')).json();

const pc=s.total>0?Math.min(100,Math.round(s.done/s.total*100)):10;$('pi').style.width=pc+'%';

pm.textContent='🗂 '+(smsg(s)||T('m_names'));}catch(e){}}

else{clearInterval(tid);if(b)b.disabled=0;$('pi').style.width='100%';

pm.textContent=d.state==='done'?F(T('m_names_done'),{n:d.n}):((T(d.k)||'')+' '+(d.err||''));

loadNames();setTimeout(()=>location.reload(),1500)}},1000)}

async function selfTest(b){b.disabled=1;$('pw').style.display='';$('pi').style.width='5%';

pm.textContent=T('test_hint');

try{const r=await(await fetch('/api/selftest',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();

if(r&&r.ok===false){b.disabled=0;$('pw').style.display='none';pm.textContent=smsg(r);return}}catch(e){b.disabled=0;$('pw').style.display='none';pm.textContent=T('err_prefix')+T('net_fail');return}

clearInterval(tid);tid=setInterval(async()=>{let s;

try{s=await(await fetch('/api/selftest')).json()}catch(e){return}

if(s.state==='busy'){$('pi').style.width='60%';pm.textContent=smsg(s)||T('test_busy')}

else{clearInterval(tid);b.disabled=0;$('pw').style.display='none';

pm.textContent=(s.ok?T('test_ok'):T('test_bad'))+(smsg(s)||'')+T('test_report')}},1500)}

async function saveUpd(b){if(!DIRS.length){pm.textContent=T('save_need');return}

b.disabled=1;pm.textContent=T('saving');

let r;

try{r=await(await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},

body:JSON.stringify({include_dirs:DIRS,exclude_dirs:EXC})})).json();

}catch(e){b.disabled=0;pm.textContent=T('err_prefix')+T('net_fail');return}

if(!r.ok){pm.textContent=r.k?T(r.k):(r.err||T('save_fail'));b.disabled=0;return}

CFG_DIRTY=false;upd(null)}

async function upd(b){if(!DIRS.length){pm.textContent=T('upd_need');return}

if(b)b.disabled=1;$('savebtn').disabled=1;$('updonly').disabled=1;$('smartbtn').disabled=1;

$('pw').style.display='';$('pi').style.width='2%';pm.textContent=T('started');

/* 以前这里 await fetch 没兜底：请求一失败（服务刚重启/端口变了），按钮就永远灰着，

   恢复代码只写在 poll 里，而 poll 根本没被启动。失败时当场把按钮还回去。 */

try{const r=await(await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({targets:DIRS})})).json();

  if(r&&r.ok===false){btnBack();$('pw').style.display='none';$('pi').style.width='0';pm.textContent=smsg(r);return}

}catch(e){btnBack();$('pw').style.display='none';pm.textContent=T('err_prefix')+T('net_fail');return}

poll()}

function smsg(o){if(!o)return'';

if(o.k&&DICT[LANG][o.k]!==undefined)return F(T(o.k),o.a||{});

return o.msg||o.err||''}

function renderProg(s){

if(s.state!=='busy')return;

if(s.phase==='discover'&&!s.total){$('pi').classList.add('indet');pm.textContent='🧠 '+smsg(s);return}

$('pi').classList.remove('indet');

const pc=s.total>0?Math.min(100,Math.round(s.done/s.total*100)):8;

$('pi').style.width=pc+'%';

const ph=T('ph_'+s.phase)||T('ph_working');

pm.textContent=F(T('prog'),{ph:ph,d:s.done,t:(s.total||'?'),ok:s.ok,f:s.fail})+(smsg(s)?' · '+smsg(s):'')}

let POLLF=0;

function btnBack(){$('savebtn').disabled=0;$('updonly').disabled=0;$('smartbtn').disabled=0;$('gear').style.display=''}

function poll(){clearInterval(tid);POLLF=0;tid=setInterval(async()=>{let s;

try{s=await(await fetch('/api/status')).json();POLLF=0}

catch(e){/* 服务没了就别刷了：连挂 8 次把按钮还回去，告诉用户联系不上后台 */

if(++POLLF>=8){clearInterval(tid);btnBack();$('pw').style.display='none';pm.textContent=T('err_prefix')+T('net_fail')}

return}

PROG_CACHE=s;

if(s.state==='busy'){s.phase&&DICT[LANG]['ph_'+s.phase]?renderProg(s):(()=>{const pc=s.total>0?Math.min(100,Math.round(s.done/s.total*100)):8;

$('pi').style.width=pc+'%';pm.textContent=T('ph_working')+(smsg(s)?' · '+smsg(s):'')})()}

else{clearInterval(tid);btnBack();

if(s.state==='done'){$('pi').style.width='100%';pm.textContent=F(T('done_reload'),{m:smsg(s)||T('m_update_done')});

// 第一次用：更新只建了「文档正文」，60 万个文件名的索引还得再点一次 🗂（藏在折叠区）。

// 与其指望新人自己找到，不如直接接着建。

if(s.need_names&&!window.__namesAuto){window.__namesAuto=1;pm.textContent=T('m_names_auto');setTimeout(()=>buildNames(null),400);return}

setTimeout(()=>location.reload(),2500)}

else if(s.state==='error'){$('pw').style.display='none';if(s.ask&&s.ask.state==='wait'){renderRepairAsk(s.ask)}pm.textContent=(s.paused?T('paused_prefix'):T('err_prefix'))+(smsg(s)||'')}}},800)}

cfgLoad();applyLang();setTimeout(()=>{try{applyTheme(THEME||'auto')}catch(e){}},300);

/* 托盘里点「设置/关于/使用帮助」时，主窗口可能是刚加载的，面板名走 URL 参数传进来 */

(function(){try{var m=/[?&]panel=([a-z]+)/.exec(location.search);if(!m)return;var n=m[1];setTimeout(function(){showPanel(n)},400);history.replaceState(null,'',location.pathname+(location.hash||''))}catch(e){}})();

// ?q=关键词 直接出结果：方便收藏常用搜索，或从命令行直接打开结果页

const Q0=(new URLSearchParams(location.search)).get('q');

if(Q0){qEl.value=Q0;go()}

else{goHome()}

// ?cfg=1 打开即展开设置面板：供 --settings 启动参数、书签直达、截图

try{if((new URLSearchParams(location.search)).get('cfg'))showCfg()}catch(e){}

</script></body></html>'''

    import os

    from urllib.parse import urlparse, parse_qs, urlsplit



    def _autostart_info():

        """检查/设置开机启动（写 HKCU\\...\\Run）。

        返回 (当前是否启用, 设置时用的命令行)。"""

        try:

            import winreg

            sub = r'Software\Microsoft\Windows\CurrentVersion\Run'

            name = 'SecondBrain'

            if getattr(sys, 'frozen', False):

                cmd = f'"{sys.executable}" --silent'

            else:

                cmd = f'"{sys.executable}" "{app_dir() / "app_window.py"}" --silent'

            try:

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as k:

                    val, _ = winreg.QueryValueEx(k, name)

                    # 只看「键存在」会骗过设置面板：静默安装验收留下的旧路径

                    # （测试目录早删了）也会显示「已开启」，实际开机什么都没起来。

                    # 拆出命令里的 exe，文件还在才算真的开着；用户点一下「开」

                    # 就会用当前 exe 的路径覆盖掉这条死链。

                    v = (val or '').strip()

                    if v.startswith('"') and '"' in v[1:]:

                        p = v[1:v.find('"', 1)]

                    else:

                        p = v.split(' ')[0]

                    # 文件还在但指向**别的目录**（用户挪过文件夹、或机器上同时装了两份）
                    # 也算失效：开机起来的是旧那份，用户在新窗口里改的设置、建的索引全对不上。
                    # 只有「路径就是当前这个 exe」才认作真的开着。

                    try:

                        same = bool(p) and (os.path.normcase(os.path.abspath(p))

                                            == os.path.normcase(os.path.abspath(sys.executable)))

                    except Exception:

                        same = True

                    alive = bool(p) and os.path.isfile(p) and same

                    return alive, cmd, val

            except FileNotFoundError:

                return False, cmd, ''

        except Exception:

            return False, '', ''



    def _set_autostart(on):

        """开机启动开关。on=True 写注册表，on=False 删值。"""

        try:

            import winreg

            sub = r'Software\Microsoft\Windows\CurrentVersion\Run'

            name = 'SecondBrain'

            if on:

                _, cmd, _ = _autostart_info()

                if not cmd:

                    return False

                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, sub) as k:

                    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, cmd)

            else:

                try:

                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0,

                                        winreg.KEY_SET_VALUE) as k:

                        winreg.DeleteValue(k, name)

                except FileNotFoundError:

                    pass

            return True

        except Exception:

            return False



    def _subs(html, total=None, nfiles=None):

        """统一替换模板里的版本 / 索引量 / 运行环境等占位符。



        __TOTAL__ = 正文篇数（meta 里成功入库的文档），__FILES__ = 文件名条数。

        两个数分开说：用户问「能搜多少」时，页脚那个数得是 60 万而不是 5000。

        30 秒缓存：count(*) 要扫 60 万行，每刷新一页都算一遍没必要。"""

        now = time.time()

        if total is None or nfiles is None:

            with con_lock:

                if now - _SUB_CACHE.get('t', 0.0) > 30 or 'docs' not in _SUB_CACHE:

                    _SUB_CACHE['docs'] = _count_docs(conn())

                    _SUB_CACHE['files'] = _count_files(conn())

                    _SUB_CACHE['t'] = now

                if total is None:

                    total = _SUB_CACHE['docs']

                if nfiles is None:

                    nfiles = _SUB_CACHE['files']

        return (html.replace('__VER__', APP_VERSION)

                    .replace('__FILES__', f'{nfiles:,}')

                    .replace('__TOTAL__', f'{total:,}')

                    .replace('__PY__', PY_VER)

                    .replace('__SQL__', SQL_VER)

                    .replace('__BUILD__', BUILD_DATE))



    _LOOPBACK = ('127.0.0.1', 'localhost', '::1')



    def _host_ok(h):

        """Host 头必须**解析后**是回环地址。



        不能写成 startswith('127.0.0.1')：`127.0.0.1.evil.com` 也满足，

        而 DNS rebinding 正是让攻击者域名解析到 127.0.0.1 —— 浏览器随后认为

        同源、连 Sec-Fetch-Site: same-origin 都会老实发过来，于是本机接口全裸。"""

        h = (h or '').strip().lower()

        if not h:

            return False

        if any(c in h for c in ('@', '#', '/', '%', chr(92))):

            return False

        try:

            host = urlsplit('//' + h).hostname or ''

        except ValueError:

            return False

        return host in _LOOPBACK



    def _origin_ok(o):

        """Origin 头（有的话）也必须指向回环。"""

        o = (o or '').strip().lower()

        if not o:

            return True

        try:

            host = urlsplit(o).hostname or ''

        except ValueError:

            return False

        return host in _LOOPBACK



    def _same_origin_ok(sfs, referer):

        """浏览器自己发的同源请求才放行（抽成纯函数，方便单测）。

        现代浏览器会给 `Sec-Fetch-Site: same-origin`，判等即可；
        但那是 Chrome 76+ 才有的头 —— **固定版本部署的旧 WebView2、降级到
        IE/旧 Edge 的用户根本不会发**，严格判等会让所有接口 403，
        用户看到的是「界面能打开但一片空白、一直转圈」（2026-11-07 补）。

        只对「没有这个头」放宽到看 Referer：攻击页面发起的跨站请求一定带
        `cross-site`（照样拦），<img>/<a ping> 带的是攻击者域名的 Referer（也拦），
        而且它们读不到响应内容，所以放宽不降低安全性。"""

        sfs = (sfs or '').strip().lower()

        if sfs:

            return sfs == 'same-origin'

        ref = (referer or '').strip()

        if not ref:

            return False

        try:

            return (urlsplit(ref).hostname or '').lower() in _LOOPBACK

        except ValueError:

            return False



    def build_page():

        return _subs(PAGE_TPL)



    class H(BaseHTTPRequestHandler):

        # 单线程 HTTPServer + 默认无超时：一个连上来却不把请求发完的客户端（页面刷新时

        # 被中断的请求、被占用的端口探测）会让后续请求全部排在这一条连接后面，

        # 界面表现就是「点了没反应」。给它一个读超时，并在下面换成多线程服务器。

        timeout = 15



        def log_message(self, *a):

            pass

        def _guard(self):

            """只信本机：Host 解析后必须是回环地址，Origin（如有）也必须回环。

            用 urlsplit 取 hostname 严格比对，而不是字符串前缀 —— 前缀判断挡不住

            `127.0.0.1.evil.com` 这类 DNS rebinding 域名。"""

            if not _host_ok(self.headers.get('Host')):

                return False

            return _origin_ok(self.headers.get('Origin'))



        def _same_origin(self):

            return _same_origin_ok(self.headers.get('Sec-Fetch-Site'),
                                   self.headers.get('Referer'))

        def do_GET(self):

            try:

                self._do_GET()

            except Exception:

                _sb_err(self)



        def _do_GET(self):

            if not self._guard():

                self.send_response(403)

                self.end_headers()

                return

            # 会读全盘清单、或产生副作用的接口：只接受页面自己发的同源请求。

            # 页面本身的加载（导航）不带 Sec-Fetch-Site: same-origin，所以只拦接口路径。

            # /api/ping 留给启动器探端口、/api/selftest 留给命令行，都不带 header。

            p0 = self.path

            if (p0.startswith('/api/') or p0.startswith('/api?') or p0.startswith('/open')):

                _bare = p0.split('?')[0]

                if _bare not in ('/api/ping', '/api/selftest'):

                    if not self._same_origin():

                        self.send_response(403)

                        self.end_headers()

                        return

            if self.path.startswith('/api/selftest'):

                body = json.dumps(dict(SELFTEST), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/peek'):

                # 详情面板「读一段正文」：一次只读一篇，按 path 缓存 5 分钟

                qs = parse_qs(urlparse(self.path).query)

                pth = (qs.get('p', [''])[0] or '').strip()

                kw = (qs.get('q', [''])[0] or '').strip()

                d = {'ok': False, 'why': 'bad_path'}

                if pth:

                    pth = os.path.normpath(pth)

                    if not ev_recent(pth):

                        try:

                            with con_lock:

                                okrow = bool(conn().execute(

                                    'SELECT 1 FROM meta WHERE path=? LIMIT 1', (pth,)).fetchone())

                        except sqlite3.Error:

                            okrow = False

                        if not okrow:

                            d = {'ok': False, 'why': 'not_indexed'}

                            pth = ''

                    if pth:

                        try:

                            with con_lock:

                                d = peek_body(conn(), pth, kw)

                        except sqlite3.Error as e:

                            d = {'ok': False, 'why': str(e)}

                body = json.dumps(d, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/ping'):

                # 供桌面启动器确认「这个端口上跑的是不是本应用」，防止端口被别的程序占用时显示错页面。

                # 官方地址也从这里发：前端只负责显示，改地址得动这个文件（跟着 exe 编译）。

                body = json.dumps({'app': 'second-brain', 'ver': APP_VERSION,

                                   'dir': str(app_dir()),

                                   'lang': boot_lang(),

                                   'fallback': bool(DATA_FALLBACK),

                                   'repo': OFFICIAL_REPO,

                                   'releases': OFFICIAL_RELEASES,

                                   'issues': OFFICIAL_ISSUES},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/env'):

                # 环境自检：用户报「打不开 / 窗口不对」时，让他点一下这里，把结论发过来，
                # 等于把现场搬到开发机上。只读探测，不做任何修复动作。
                # 结果缓存 90 秒：quick_check 一个 1GB 的库要一两秒，面板刷新不该重复付这笔钱。

                global _ENV_CACHE

                _now = time.time()

                _force = 'force=1' in self.path

                if (not _force and _ENV_CACHE.get('t')

                        and _now - _ENV_CACHE['t'] < 90):

                    body = _ENV_CACHE['body']

                else:

                    try:

                        import env_check

                        _inf = env_check.probe_all(data_dir=str(app_dir()), db_path=str(DB))

                        _iss = env_check.judge(_inf)

                        body = json.dumps({'ok': True, 'info': _inf, 'issues': _iss,

                                           'plan': env_check.plan(_inf),

                                           'text': env_check.render(_inf, _iss)},

                                          ensure_ascii=False).encode()

                    except Exception as _e:

                        import traceback

                        body = json.dumps({'ok': False, 'msg': repr(_e),

                                           'tb': traceback.format_exc()},

                                          ensure_ascii=False).encode()

                    _ENV_CACHE = {'t': _now, 'body': body}

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/open_url'):

                # 关于面板里的「官方仓库 / 发布页 / 反馈」：桌面端用系统浏览器打开，

                # 浏览器端就等于普通跳转。白名单只认本仓库，见 url_allowed()。

                qs = parse_qs(urlparse(self.path).query)

                u = (qs.get('u', [''])[0] or '').strip()

                if not url_allowed(u):

                    body = json.dumps({'ok': False, 'err': 'url not allowed', 'k': 'e_url'},

                                      ensure_ascii=False).encode()

                else:

                    try:

                        import webbrowser

                        webbrowser.open(u)

                        body = json.dumps({'ok': True}, ensure_ascii=False).encode()

                    except Exception as e:

                        body = json.dumps({'ok': False, 'err': str(e)}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/config'):

                sp = app_dir() / 'scope.json'

                with con_lock:

                    rep = scope_report(conn())

                # 老界面的三个字段必须留着（?ui=legacy 还在读），新字段给 React 设置抽屉用

                cfg = {'exists': sp.exists(),

                       'include_dirs': rep['include_dirs'],

                       'exclude_dirs': rep['exclude_dirs'],

                       'app_dir': str(app_dir()),

                       'docs': rep['docs'],

                       'scope': rep}

                if sp.exists():

                    try:

                        json.loads(sp.read_text(encoding='utf-8'))

                    except Exception:

                        cfg['broken'] = True

                body = json.dumps(cfg, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/autostart'):

                on, cmd, val = _autostart_info()

                body = json.dumps({'on': on, 'cmd': cmd}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/update_check'):

                # 检查更新：慢（要连 GitHub），放线程里等，前端显示「检查中…」

                body = json.dumps(check_update(), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/update_status'):

                # 一键升级的下载进度：POST /api/update_apply 立刻返回，前端每 700ms 读这个

                body = json.dumps(dict(_UPD), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/license'):

                # 授权状态。**只读**：激活/清除都走 POST。

                # 关于面板一打开就拉这个，所以必须走缓存（见 license_state）。

                body = json.dumps({'ok': True, 'state': license_state()},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/firstrun'):

                # 首次运行：库里一个文件都没有、也没被标记过 → 界面自动开始建库

                try:

                    need = first_run_need()

                except Exception:

                    need = False

                body = json.dumps({'ok': True, 'need': bool(need),

                                   'state': FIRST_RUN.get('state'),

                                   'stage': FIRST_RUN.get('stage', ''),

                                   'msg': FIRST_RUN.get('msg', ''),

                                   'n': FIRST_RUN.get('n', 0),

                                   'files': FIRST_RUN.get('files', 0),

                                   'docs': FIRST_RUN.get('docs', 0),

                                   'eta': int(FIRST_RUN.get('eta') or 0),

                                   'total': int(FIRST_RUN.get('total') or 0),

                                   'bg': FIRST_RUN.get('bg', '')},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/stats'):

                # 共享连接必须整段加锁：多线程同时 execute/fetchone 会互相踩游标，

                # 实测 8 并发 120 次请求里有 28 次 500（fetchone() 返回 None / 空元组）。

                # 同时复用页脚那套 30 秒缓存：count(*) 要扫 60 万行（单次 62ms），

                # 前端每挂一次就重算一遍纯属浪费。

                now = time.time()

                if now - _SUB_CACHE.get('t', 0.0) > 30 or 'files' not in _SUB_CACHE:

                    with con_lock:

                        _SUB_CACHE['docs'] = _count_docs(conn())

                        _SUB_CACHE['files'] = _count_files(conn())

                        _SUB_CACHE['t'] = now

                body = json.dumps({'ok': True, 'files': _SUB_CACHE.get('files', 0),

                                   'docs': _SUB_CACHE.get('docs', 0)},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/settings'):

                # 损坏文件由 _settings_read 备份并返回 {}，这里再补默认值，

                # 绝不会因为 JSON 解析失败/res不是 dict 变成 500 HTML。

                d = _settings_read()

                d.setdefault('hotkey', True)

                d.setdefault('theme', 'auto')

                body = json.dumps(d, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/status'):

                st = dict(PROG)

                if REPAIR_ASK.get('state') == 'wait':

                    st['ask'] = dict(REPAIR_ASK)   # 认亲候选，交给界面点选

                if _BODYSTAT['pending'] or _BODYSTAT['done']:

                    st['body'] = {'pending': _BODYSTAT['pending'],

                                  'done': _BODYSTAT['done'],

                                  'dropped': _BODYSTAT['dropped']}

                body = json.dumps(st, ensure_ascii=False).encode()

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

                # 详情页第二路：文件名。用 mini_search 统一排序（应用优先 + 前缀优先），

                # 这样搜 chrome 能让 chrome.exe 排最前。

                qs = parse_qs(urlparse(self.path).query)

                q = (qs.get('q', [''])[0] or '').strip()

                syn = parse_qsyn(q)

                q = syn['q']

                srt = (qs.get('s', ['rel'])[0] or 'rel').lower()

                items = []

                st = 'off'

                if q:

                    try:

                        with con_lock:

                            c = conn()

                            # 先用前缀（走 idx_files_name，毫秒级）；只有前缀凑不满一屏

                            # 才补一次全表扫。以前每敲一个字都深扫 60 万行 ≈ 270ms。

                            r = mini_search(c, q, limit=20, deep=False, body=False)

                            items = r.get('items', [])

                            total = r.get('n', 0)

                            if len(items) < 12:

                                r2 = mini_search(c, q, limit=20, deep=True, body=False)

                                if len(r2.get('items', [])) > len(items):

                                    items = r2.get('items', [])

                                    total = max(total, r2.get('n', 0))

                            st = 'ok' if total > 0 else 'off'

                    except sqlite3.Error:

                        items = []

                        total = 0

                    for it in items:

                        it.setdefault('d', os.path.dirname(it.get('p', '')))

                        it.setdefault('src', 'disk')

                        # 程序行：中文名（Weixin.exe → 微信）+ app 标记（前端据此不显示路径）

                        nm = it.get('n') or os.path.basename(it.get('p', ''))

                        zh = None if it.get('t') == 'dir' else apprank.name_for_exe(nm)

                        if zh:

                            it.setdefault('dn', zh)

                        if not it.get('app') and apprank.is_app_file(nm):

                            it['app'] = 1

                    if syn['ext']:

                        want = ['.' + e.strip().lstrip('.') for e in syn['ext'].split(',') if e.strip()]

                        items = [it for it in items if str(it.get('e') or '').lower() in want]

                    items = [it for it in items

                             if syn_ok(it.get('p'), _sz_of(it), it.get('m'), syn)]

                    items = sort_items(items, srt)

                    total = max(total, len(items))

                    # 主窗口的结果也要登记进打开白名单，否则双击是「打开失败」

                    # （/api/mini 与 /api/recent 都登记了，只有这里漏了）

                    if items:

                        allow_open([x['p'] for x in items], replace=True)

                body = json.dumps({'items': items, 'st': st, 'more': total > len(items)},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/mini'):

                # 双击 Ctrl 弹出的快搜窗。deep=0 只做前缀（走索引，毫秒级），

                # deep=1 才全表扫「名字任意位置包含」，由前端在结果不满一屏时补发；

                # body=1 连正文一起查（两家长处都不在正文，这是第二大脑的差异点）。

                qs = parse_qs(urlparse(self.path).query)

                q = (qs.get('q', [''])[0] or '').strip()

                syn_m = parse_qsyn(q)

                if q:

                    # 记下快搜窗最后在搜什么：托盘「打开详细搜索」带着它打开主窗口，

                    # 用户看到的就是「接着刚才的继续查」，而不是一个空窗口

                    global LAST_Q

                    LAST_Q = q

                    q = syn_m['q']

                deep = qs.get('deep', ['0'])[0] in ('1', 'true')

                want_body = qs.get('body', ['0'])[0] in ('1', 'true')

                # c:xxx / content:xxx / 内容:xxx —— 只要正文，名字一条都不占。

                # 主窗口早就认这个语法，快搜窗以前把它当普通词剥掉就当没发生。

                c_only = bool(syn_m['content'])

                if c_only:

                    want_body = True

                try:

                    with con_lock:

                        d = mini_search(conn(), q, 9, deep, want_body, c_only)

                except sqlite3.Error as e:

                    d = {'items': [], 'deep': deep, 'ms': 0, 'n': 0, 'err': str(e)}

                if d['items']:

                    if syn_m['ext']:

                        want = ['.' + e.strip().lstrip('.') for e in syn_m['ext'].split(',') if e.strip()]

                        d['items'] = [x for x in d['items'] if str(x.get('e') or '').lower() in want]

                    d['items'] = [x for x in d['items']

                                  if syn_ok(x.get('p'), _sz_of(x), x.get('m'), syn_m)]

                    allow_open([x['p'] for x in d['items']])

                # 回传剥掉语法之后的词：前端页脚/空态显示「展示更多 “c:会议”」
                # 像在提示用户再敲一遍 c:，而剥过的 q 才是他真正在找的东西。

                d['q'] = q

                body = json.dumps(d, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/icon'):

                # 系统图标。按扩展名取，和资源管理器里看到的是同一张图；

                # 应用类（.exe/.lnk）还可以传 p= 按真实路径取 —— shell 自动解析

                # 快捷方式到目标应用，每个应用拿到自己的图标（Listary 同款）。

                # 拿不到就回 204，前端换回自绘图标。

                qs = parse_qs(urlparse(self.path).query)

                ext = (qs.get('e', [''])[0] or '').strip().lower()[:12]

                isdir = qs.get('d', ['0'])[0] in ('1', 'true') or ext == 'dir'

                # 前端传的 e 有两种写法（"lnk" / ".lnk"），统一成带点的，

                # 否则下面的 `ext in ('.exe', '.lnk')` 判不中，只给一张通用 LNK 图标

                if ext and not isdir and not ext.startswith('.'):

                    ext = '.' + ext

                p = (qs.get('p', [''])[0] or '').strip()

                if p:

                    # 容忍正斜杠：索引里统一存反斜杠，先归一化再校验/取图标

                    p = os.path.normpath(p)

                png = None

                if p and not isdir and ext in ('.exe', '.lnk'):

                    # 安全校验：只给「索引里有的」或「最近搜出来过的」路径取图标

                    allowed = ev_recent(p)

                    if not allowed:

                        try:

                            with con_lock:

                                allowed = bool(conn().execute(

                                    'SELECT 1 FROM files WHERE path=? LIMIT 1',

                                    (p,)).fetchone())

                        except sqlite3.Error:

                            pass

                    if allowed:

                        if ext == '.lnk':

                            tgt = _lnk_target_for_icon(p)

                            if tgt:

                                png = path_icon(tgt)

                        png = png or path_icon(p)

                if not png:

                    png = sys_icon('' if isdir else ext, isdir)

                if png:

                    self.send_response(200)

                    self.send_header('Content-Type', 'image/png')

                    # 按路径取的图标可能是一次失败（shell 还没热起来时给的是通用图标），

                    # 缓存一整天会把错的那张钉死；扩展名图标是稳定的，照旧缓存。

                    self.send_header('Cache-Control',

                                     'max-age=60' if (p and ext in ('.exe', '.lnk')) else 'max-age=86400')

                    self.send_header('Content-Length', str(len(png)))

                    self.end_headers()

                    self.wfile.write(png)

                    return

                self.send_response(204)

                self.end_headers()

                return

            elif self.path.startswith('/api/thumb'):

                # 文件缩略图。图片直接缩放、PDF 取首页、其它类型回 204 让前端用系统图标。

                # 安全校验：必须是索引里的路径或本次搜索返回过的，防止任意文件被读

                qs = parse_qs(urlparse(self.path).query)

                p = (qs.get('p', [''])[0] or '').strip()

                site = (self.headers.get('Sec-Fetch-Site') or '').lower()

                small = (qs.get('s', [''])[0] or '') == '1'

                png = b''

                if p and site not in ('cross-site', 'same-site', 'none'):

                    # 路径必须在索引里或最近搜索出现过

                    allowed = False

                    try:

                        with con_lock:

                            c = conn()

                            # 图片/视频大多没进 meta（meta 只收正文抽取过的文档），

                            # 光查 meta 会让绝大多数缩略图回 204 —— 再查 files 全量表

                            allowed = bool(c.execute(

                                'SELECT 1 FROM files WHERE path=? LIMIT 1', (p,)).fetchone()) or bool(

                                c.execute('SELECT 1 FROM meta WHERE path=? LIMIT 1', (p,)).fetchone())

                    except sqlite3.Error:

                        pass

                    allowed = allowed or ev_recent(p)

                    if allowed:

                        png = make_thumb(p, 200, 200) if small else make_thumb(p)

                        png = png or b''

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

            elif self.path.startswith('/api/app_pref'):

                # 用户对某个入口的表态：固定到第一位 / 不再显示

                qs = parse_qs(urlparse(self.path).query)

                p = (qs.get('p', [''])[0] or '').strip()

                mode = (qs.get('m', [''])[0] or '').strip().lower()

                if p and mode in ('pin', 'unpin', 'hide', 'unhide'):

                    set_app_pref(p, mode)

                body = json.dumps({'ok': True, 'prefs': app_prefs()},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/timeline'):

                # 首页时间轴：不打字也能看见「今天/昨天/前几天」动过什么。

                qs = parse_qs(urlparse(self.path).query)

                try:

                    days = max(1, min(60, int(qs.get('days', ['7'])[0])))

                except (TypeError, ValueError):

                    days = 7

                kind = (qs.get('t', [''])[0] or '').strip().lower()

                exts = _kind_exts(kind)

                pf = (qs.get('pf', [''])[0] or '').strip()

                try:

                    with con_lock:

                        # 「文件夹」走 dirs 表（mtime 是后补的列，老库可能有 NULL 未回填）

                        groups = timeline_rows(conn(), days, exts=exts, pf=pf,

                                               dirs_only=(kind == 'dir'))

                except sqlite3.Error:

                    groups = []

                flat = [x['p'] for g in groups for x in g['items']]

                if flat:

                    allow_open(flat)

                body = json.dumps({'days': days, 'groups': groups},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/same'):

                # 版本链：同一个文件名的其他版本（简历 / 简历(1) / 简历-最终版）

                qs = parse_qs(urlparse(self.path).query)

                p = (qs.get('p', [''])[0] or '').strip()

                try:

                    with con_lock:

                        items = same_name_rows(conn(), p) if p else []

                except sqlite3.Error:

                    items = []

                if items:

                    allow_open([x['p'] for x in items] + [p])

                body = json.dumps({'items': items}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/recent'):

                # 快搜窗的空状态：打开就先给一屏「你刚动过的文件」，不用先打字。

                try:

                    with con_lock:

                        items = recent_rows(conn(), 5)

                except sqlite3.Error:

                    items = []

                if items:

                    allow_open([x['p'] for x in items])

                    # 和主列表一样给个中文显示名：桌面快捷方式显示「微信」而不是「微信.lnk」

                    for x in items:

                        e = os.path.splitext(x.get('p', ''))[1].lower()

                        try:

                            x['dn'] = _disp_name(x.get('n') or os.path.basename(x.get('p', '')), e)

                        except Exception:

                            x['dn'] = x.get('n')

                body = json.dumps({'items': items}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api?'):

                qs = parse_qs(urlparse(self.path).query)

                q = (qs.get('q', [''])[0] or '').strip()

                ext = qs.get('t', [''])[0] or None

                pf = qs.get('d', [''])[0] or None

                # 界面上文件名另有独立一栏，这里传 name=0 关掉垫底补位，免得同一条出现两次

                wn = qs.get('name', ['1'])[0] != '0'

                # 搜索框里可以直接写 ext:pdf / size:>10m / dm:7d / -排除 / 某文件夹/

                syn = parse_qsyn(q)

                q = syn['q']

                if syn['ext']:

                    ext = syn['ext']

                if syn['folder']:

                    pf = syn['folder']

                if syn['content']:

                    wn = False          # content: 只看正文，别拿文件名垫底

                srt = (qs.get('s', ['rel'])[0] or 'rel').lower()

                try:

                    # 空关键词返回空列表：以前会 WHERE 1=1 LIMIT 30 随便给 30 篇，

                    # 页面上看起来像「搜到了」，其实什么都没搜

                    rows = [] if not q else self._search(q, ext, pf, wn)

                    # 第二个字段是正文片段（snippet 里用 / 标高亮），

                    # 以前这里写成 {'s': snip}，随后又被 meta 的 size 覆盖 → 界面上看不到「为什么命中」。

                    hits = [{'p': p, 'b': snip, 's': None} for p, snip in rows]

                    # 零命中时给一句「你是不是要找」：中文查不到 → 按拼音找同音的；

                    # 字母查不到 → 找中文名里拼音对得上的（wx → 微信.lnk）。

                    dym = []

                    if q and not hits:

                        try:

                            with con_lock:

                                dym = dym_items(conn(), q)

                        except sqlite3.Error:

                            dym = []

                    # 详情页要显示「修改时间」这一列：doc 的 mtime 在 meta 表里，

                    # 一次 IN 查询取回来，比每条 os.stat 便宜（也避开网络盘卡死）。

                    if hits:

                        try:

                            with con_lock:

                                mm = {r[0]: (r[1], r[2]) for r in conn().execute(

                                    'SELECT path, mtime, size FROM meta WHERE path IN (%s)'

                                    % ','.join('?' * len(hits)),

                                    [h['p'] for h in hits])}

                        except sqlite3.Error:

                            mm = {}

                        for h in hits:

                            m_sz = mm.get(h['p'])

                            if m_sz:

                                h['m'], h['s'] = m_sz[0], m_sz[1]

                        # size:/dm:/-排除 在这里收口：结果集只有一屏，过滤比改 SQL 便宜

                        hits = [h for h in hits if syn_ok(h['p'], _sz_of(h), h.get('m'), syn)]

                        hits = sort_items(hits, srt)

                        # 正文命中也要进打开白名单，否则双击/Enter 走原生桥会被 ev_recent 挡下，

                        # 表现就是「点了完全没反应」（v0.9.0 用户实测）。

                        allow_open([h['p'] for h in hits])

                    body = json.dumps({'hits': hits, 'dym': dym, 'q': q},

                                      ensure_ascii=False).encode()

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

                reason = ''

                # 先规范化再判：Windows 会忽略结尾的空格和点（`notepad.exe.` 仍是 exe），

                # `a.txt:stream` 是备用数据流；realpath 再解开符号链接/目录联接，

                # 否则把 notes.txt 指到 cmd.exe 就绕过了下面所有后缀判断。

                if p:

                    p = p.rstrip(' .')

                    if ':' in p[2:]:

                        p = p[:2] + p[2:].split(':', 1)[0]     # 去掉 NTFS 备用数据流

                    p = os.path.realpath(p)

                # 可执行/脚本类一律不在这里启动：这几道门（Host / Sec-Fetch-Site）对

                # 本机进程来说都能伪造，一旦放行就等于「任何本地程序可以让我执行任意 exe」。

                # 桌面版的「打开应用」走原生桥 MiniApi.open_path，不经过 HTTP。

                if not p or not _local_only(p) or p.lower().endswith(tuple(_OPEN_DENY_EXT)):

                    body = json.dumps({'ok': False, 'reason': 'deny'}, ensure_ascii=False).encode()

                    self.send_response(200)

                    self.send_header('Content-Type', 'application/json; charset=utf-8')

                    self.send_header('Content-Length', str(len(body)))

                    self.end_headers()

                    self.wfile.write(body)

                    return

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

                        # 先校验存在，区分 gone/fail —— 否则 os.startfile 抛错被吞，

                        # 用户只看到"打开失败"，不知道是文件没了。

                        if not os.path.exists(p):

                            ok = False

                            reason = 'gone'

                            _mark_gone(p)

                        else:

                            try:

                                os.startfile(p)

                                with con_lock:

                                    record_open(conn(), p, os.path.isdir(p))

                            except Exception:

                                ok = False

                                reason = 'fail'

                body = json.dumps({'ok': ok, 'reason': reason}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/mini'):

                lang = ''

                try:

                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

                    lang = (qs.get('lang') or [''])[0].lower()

                except Exception:

                    lang = ''

                if lang not in MINI_L:

                    lang = boot_lang() or 'zh'

                mj = json.dumps(MINI_L, ensure_ascii=False).replace('</', '<' + chr(92) + '/')

                body = _subs(MINI_TPL).replace(

                    '__MINI_L__', mj).replace('__MINI_LANG__', lang).encode()

                ctype = 'text/html; charset=utf-8'

            elif self.path.split('?')[0] in ('/web', '/react'):

                # 新版前端（React）在 /web/ 下，/react 只是个好记的别名

                self.send_response(302)

                self.send_header('Location', '/web/')

                self.end_headers()

                return

            elif self.path.startswith('/api/donate'):

                # 赞赏码清单：assets 里 donate*.png/jpg，文件名带数字就是档位（donate-6.png → ¥6）。

                # 每张都带上 SHA-256：前端拿它和编译进 exe 的 DONATE_SHA256 比，

                # 对不上就**不显示二维码**——被换掉收款码的发行版在这里露馅。

                items = []

                for base in (app_dir() / 'assets', Path(__file__).resolve().parent / 'assets'):

                    if not base.is_dir():

                        continue

                    for f in sorted(base.glob('donate*')):

                        if f.suffix.lower() not in ('.png', '.jpg', '.jpeg') or not f.is_file():

                            continue

                        digits = ''.join(ch for ch in f.stem if ch.isdigit())

                        sha = file_sha256(f)

                        items.append({'file': f.name,

                                      'amount': int(digits) if digits else 0,

                                      'sha': sha,

                                      'official': bool(DONATE_SHA256) and sha == DONATE_SHA256})

                    if items:

                        break

                body = json.dumps({'ok': True, 'items': items,

                                   'official_sha': DONATE_SHA256},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.split('?')[0] in ('/logo.png', '/appicon.png'):

                # 程序自己的图标（关于弹窗、欢迎页用）。和捐赠码一样运行时读文件，

                # 换图标不需要重新打包。

                cand = None

                for base in (app_dir() / 'assets', Path(__file__).resolve().parent / 'assets'):

                    f = base / 'icon_preview.png'

                    if f.is_file():

                        cand = f

                        break

                if cand is None:

                    self.send_response(404)

                    self.end_headers()

                    return

                body = cand.read_bytes()

                ctype = 'image/png'

            elif self.path.split('?')[0] in ('/donate.png', '/donate.jpg'):

                # 「支持一下」的收款二维码：用户把图片放到 assets/donate.png 就会出现，

                # 没有就 404，前端显示一句「还没放二维码」而不是破图。

                qs = parse_qs(urlparse(self.path).query)

                want = (qs.get('f', [''])[0] or '').strip()

                cand = None

                names = (want,) if want else ('donate.png', 'donate.jpg')

                for name in names:

                    for base in (app_dir() / 'assets', Path(__file__).resolve().parent / 'assets'):

                        f = base / name

                        if f.is_file():

                            cand = f

                            break

                    if cand:

                        break

                if cand is None:

                    self.send_response(404)

                    self.send_header('Content-Type', 'text/plain; charset=utf-8')

                    self.end_headers()

                    self.wfile.write(b'no donate image')

                    return

                body = cand.read_bytes()

                ctype = 'image/png' if cand.suffix.lower() == '.png' else 'image/jpeg'

            elif self.path.startswith('/web/'):

                ui = web_ui_dir()

                rel = urllib.parse.unquote(self.path.split('?')[0])[len('/web/'):] or 'index.html'

                hit = None

                if ui is not None and rel and '..' not in rel:

                    try:

                        cand = (ui / rel).resolve()

                        if str(cand).startswith(str(ui.resolve())) and cand.is_file():

                            hit = cand

                    except Exception:

                        hit = None

                if hit is None:

                    self.send_response(404)

                    self.send_header('Content-Type', 'text/plain; charset=utf-8')

                    self.end_headers()

                    self.wfile.write('前端还没构建：在 web/ 里跑 pnpm build'.encode('utf-8'))

                    return

                ext = hit.suffix.lower()

                ctype = {'.html': 'text/html; charset=utf-8',

                         '.js': 'text/javascript; charset=utf-8',

                         '.css': 'text/css; charset=utf-8',

                         '.svg': 'image/svg+xml',

                         '.png': 'image/png',

                         '.ico': 'image/x-icon',

                         '.woff2': 'font/woff2',

                         '.json': 'application/json; charset=utf-8'}.get(ext, 'application/octet-stream')

                body = hit.read_bytes()

            elif (self.path.split('?')[0] in ('/', '/index.html')

                  and web_ui_dir() is not None

                  and 'ui=legacy' not in self.path

                  and 'panel=' not in self.path

                  and 'q=' not in self.path):

                # 默认首页改成新界面（React）。三种情况仍回旧界面：

                # ?ui=legacy（手动兜底）、?panel=…（托盘要用旧界面的抽屉）、?q=…（旧链接）

                self.send_response(302)

                self.send_header('Location', '/web/')

                self.end_headers()

                return

            elif self.path.startswith('/api/'):

                # 未知接口绝不能落回旧模板首页（200 + text/html）。前端 r.json()

                # 解析失败又被 catch 吞掉，于是「拼错路径」「接口已删」全都伪装成

                # 「请求成功但什么也没发生」——公告下线后残留的 /api/notices

                # 就是这么静默跑了好几个版本的。给个明确的 JSON 404。

                body = json.dumps({'ok': False, 'err': 'no such api',

                                   'path': self.path.split('?')[0][:120]},

                                  ensure_ascii=False).encode()

                self.send_response(404)

                self.send_header('Content-Type', 'application/json; charset=utf-8')

                self.end_headers()

                self.wfile.write(body)

                return

            else:

                body = build_page().encode()

                ctype = 'text/html; charset=utf-8'

            self.send_response(200)

            self.send_header('Content-Type', ctype)

            self.end_headers()

            self.wfile.write(body)



        # 不回 Server: BaseHTTP/0.6 Python/3.12.10 —— 版本号只会帮人踩点

        server_version = 'SecondBrain'

        sys_version = ''



        def end_headers(self):

            # 任意网页都能 iframe 本应用（同源闸门在 iframe 里是放行的），

            # 于是能做点击劫持：把「打开」「更新索引」对齐到用户鼠标下。

            try:

                self.send_header('X-Frame-Options', 'DENY')

                self.send_header('Content-Security-Policy', "frame-ancestors 'none'")

                self.send_header('X-Content-Type-Options', 'nosniff')

                # 搜索词在 URL 里（/?q=…），别让它顺着 Referer 流到别处，也别落进磁盘缓存

                self.send_header('Referrer-Policy', 'no-referrer')

                self.send_header('Cache-Control', 'no-store')

            except Exception:

                pass

            super().end_headers()



        def do_POST(self):

            try:

                self._do_POST()

            except Exception:

                _sb_err(self)



        def _do_POST(self):

            if not self._guard() or 'application/json' not in (self.headers.get('Content-Type') or ''):

                self.send_response(403)

                self.end_headers()

                return

            # 写配置/改开机自启这类动作，同样只收页面自己发的同源请求

            if self.path.startswith('/api/') and not self._same_origin():

                self.send_response(403)

                self.end_headers()

                return

            try:

                n = int(self.headers.get('Content-Length') or 0)

            except ValueError:

                n = 0                       # Content-Length 是垃圾时当空 body 处理

            if not (0 <= n <= 64 * 1024):

                # 负数会让 rfile.read(-1) 一直读到对端关闭（线程被白占 15 秒）；

                # 超大值则是一次性读进内存。配置类请求没有这么大的合法 body。

                self.send_response(413)

                self.end_headers()

                return

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

            if self.path.startswith('/api/autostart'):

                on = bool(req.get('on', False))

                ok = _set_autostart(on)

                body = json.dumps({'ok': ok, 'on': on}, ensure_ascii=False).encode()

                self.send_response(200)

                self.send_header('Content-Type', 'application/json; charset=utf-8')

                self.end_headers()

                self.wfile.write(body)

                return

            if self.path.startswith('/api/settings'):

                # 先校验再写：非法值不该触发一次落盘。所有键合成一个 patch，

                # 由 _settings_update 加锁合并 + 原子替换，避免并发互相抹掉。

                patch = {}

                if 'hotkey' in req:

                    patch['hotkey'] = bool(req['hotkey'])

                if 'theme' in req:

                    if req['theme'] not in ('auto', 'dark', 'light'):

                        body = json.dumps({'ok': False, 'err': 'theme 只能是 auto/dark/light',

                                           'k': 'e_bad_theme'}, ensure_ascii=False).encode()

                        self.send_response(400)

                        self.send_header('Content-Type', 'application/json; charset=utf-8')

                        self.end_headers()

                        self.wfile.write(body)

                        return

                    patch['theme'] = req['theme']

                if 'welcome_seen' in req:

                    # 欢迎页只该出现一次。存进 settings.json，跟主题/语言一个地方。

                    patch['welcome_seen'] = bool(req['welcome_seen'])

                if 'lang' in req:

                    if req['lang'] not in ('zh', 'en', 'es', 'pt', 'hi'):

                        body = json.dumps({'ok': False, 'err': 'lang 只能是 zh/en/es/pt/hi',

                                           'k': 'e_bad_lang'}, ensure_ascii=False).encode()

                        self.send_response(400)

                        self.send_header('Content-Type', 'application/json; charset=utf-8')

                        self.end_headers()

                        self.wfile.write(body)

                        return

                    patch['lang'] = req['lang']

                    # 用户在设置里亲手选的：以后 boot_lang() 才认这一项

                    patch['lang_user'] = True

                ok, d = _settings_update(patch)

                if not ok:

                    body = json.dumps({'ok': False, 'err': 'settings.json 写入失败',

                                       'k': 'e_settings_write'}, ensure_ascii=False).encode()

                    self.send_response(500)

                    self.send_header('Content-Type', 'application/json; charset=utf-8')

                    self.end_headers()

                    self.wfile.write(body)

                    return

                body = json.dumps({'ok': True, 'settings': d}, ensure_ascii=False).encode()

                self.send_response(200)

                self.send_header('Content-Type', 'application/json; charset=utf-8')

                self.end_headers()

                self.wfile.write(body)

                return

            if self.path.startswith('/api/config'):

                raw = [str(d).strip() for d in req.get('include_dirs', []) if str(d).strip()]

                exc = [str(d).strip() for d in req.get('exclude_dirs', []) if str(d).strip()]

                dirs = safe_scope_list(raw)     # 只收本机存在的目录，挡掉远程共享/手打错路径

                if not dirs:

                    body = json.dumps({'ok': False, 'err': '至少要添加一个文件夹', 'k': 'e_need_dir'},

                                      ensure_ascii=False).encode()

                else:

                    sp = app_dir() / 'scope.json'

                    try:

                        # 合并进原文件而不是整份重写：scope.json 里可能还有别的键

                        # （早期版本写进去的 scan_csv、以后可能加的字段），别一保存就丢。

                        try:

                            sc_old = json.loads(sp.read_text(encoding='utf-8'))

                            if not isinstance(sc_old, dict):

                                sc_old = {}

                        except Exception:

                            sc_old = {}

                        sc_old.setdefault('scan_csv', 'scan_raw.csv')

                        sc_old['include_dirs'] = dirs

                        sc_old['exclude_dirs'] = exc

                        sp.write_text(json.dumps(sc_old, ensure_ascii=False, indent=2), encoding='utf-8')

                        # 被 safe_scope_list 筛掉的（路径不存在 / 网络路径 / 手打错）要告诉界面，

                        # 否则用户加了 5 个只生效 3 个，还以为是保存没生效。

                        # 注意别拿 raw 和 dirs 直接比：dirs 是规范化过的大小写/斜杠，

                        # `c://users//x` 和 `c:\users\x` 是同一个目录，直接比会全被当成「跳过」。

                        dropped = [d for d in raw if not _safe_scope_dir(d)]

                        body = json.dumps({'ok': True, 'include_dirs': dirs,

                                           'exclude_dirs': exc, 'dropped': dropped},

                                          ensure_ascii=False).encode()

                    except Exception as e:

                        print('[!] 保存索引范围失败：', e, flush=True)

                        body = json.dumps({'ok': False, 'err': '索引范围没保存（没有本机存在的文件夹，或写盘失败）',

                                           'k': 'e_scope_save'}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/names'):

                if not _job_claim('names'):

                    body = json.dumps({'ok': False, 'err': '已有任务在跑', 'k': 'e_busy'},

                                      ensure_ascii=False).encode()

                else:

                    threading.Thread(target=_run_job, args=('names', build_files_index),

                                     daemon=True).start()

                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/discover'):

                if not _job_claim('discover'):

                    body = json.dumps({'ok': False, 'err': '已有任务在跑', 'k': 'e_busy'},

                                      ensure_ascii=False).encode()

                else:

                    targets = req.get('targets')

                    if targets is not None:

                        targets = safe_scope_list([str(t) for t in targets])

                        if not targets:

                            body = json.dumps({'ok': False, 'err': '没有本机存在的文件夹',

                                               'k': 'e_no_dir'}, ensure_ascii=False).encode()

                            ctype = 'application/json; charset=utf-8'

                            self._send(200, body, ctype)

                            return

                    threading.Thread(target=_run_job, args=('discover', discover_scan, targets),

                                     daemon=True).start()

                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/selftest'):

                if not _job_claim('selftest'):

                    body = json.dumps({'ok': False, 'err': '已有任务在跑', 'k': 'e_busy'},

                                      ensure_ascii=False).encode()

                else:

                    run_selftest_async()      # 它自己起线程，跑完在 work() 里释放标记

                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/repair'):

                # 认亲候选点选：{i: 0..5} 或 {dismiss: true}

                if req.get('dismiss'):

                    ok_, rep_ = repair_dismiss()

                else:

                    ok_, rep_ = repair_choose(req.get('i', -1))

                body = json.dumps(dict(rep_, ok=ok_), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/open_dir'):

                # 只允许打开「我们自己的数据目录」，客户端传什么路径都不认

                try:

                    subprocess.Popen(['explorer', str(app_dir())])

                    body = b'{"ok": true}'

                except Exception as e:

                    body = json.dumps({'ok': False, 'err': str(e)}, ensure_ascii=False).encode()

            elif self.path.startswith('/api/reverse'):

                # 拖进来的文件反查：在库里有几条、同名版本在哪、同目录还有谁

                p = str(req.get('p') or '').strip()

                if not p or not os.path.exists(p):

                    body = json.dumps({'ok': False, 'k': 'e_gone'},

                                      ensure_ascii=False).encode()

                else:

                    try:

                        with con_lock:

                            d = reverse_rows(conn(), p)

                        allow_open([d.get('p', '')] + [x['p'] for x in d.get('same', [])])

                        body = json.dumps(dict(d, ok=True), ensure_ascii=False).encode()

                    except sqlite3.Error as e:

                        body = json.dumps({'ok': False, 'err': str(e)},

                                          ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/zip'):

                # 右键「打包选中」：把它们压成一个 zip 放到临时目录并定位出来

                paths = req.get('paths') or []

                paths = [str(x) for x in paths if str(x).strip()][:200]

                good = [x for x in paths if os.path.isfile(x)]

                if not good:

                    body = json.dumps({'ok': False, 'k': 'e_nothing'},

                                      ensure_ascii=False).encode()

                else:

                    try:

                        tdir = app_dir() / 'tmp'

                        tdir.mkdir(parents=True, exist_ok=True)

                        zname = time.strftime('打包_%Y%m%d-%H%M%S.zip')

                        zpath = tdir / zname

                        used = set()

                        with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as zf:

                            for f in good:

                                arc = os.path.basename(f)

                                if arc in used:   # 不同目录同名文件：加目录前缀，别互相覆盖

                                    arc = os.path.basename(os.path.dirname(f)) + '_' + arc

                                base, n = arc, 1

                                while arc in used:

                                    stem, ext = os.path.splitext(base)

                                    arc = f'{stem}_{n}{ext}'

                                    n += 1

                                used.add(arc)

                                zf.write(f, arc)

                        subprocess.Popen(['explorer', '/select,', str(zpath)])

                        body = json.dumps({'ok': True, 'zip': str(zpath), 'n': len(good)},

                                          ensure_ascii=False).encode()

                    except Exception as e:

                        body = json.dumps({'ok': False, 'err': str(e)},

                                          ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/export'):
                # Pro（第 140 项）：把当前搜索结果导出成 Excel。
                # 只挂**新增**功能 —— 现有 28 个 API 一个都没上锁，反锁等于口碑自杀。
                if not license_feature('export'):
                    body = json.dumps({'ok': False, 'pro': True},
                                      ensure_ascii=False).encode()
                else:
                    rows = req.get('rows') or []
                    if not isinstance(rows, list) or not rows:
                        body = json.dumps({'ok': False, 'k': 'e_nothing'},
                                          ensure_ascii=False).encode()
                    else:
                        try:
                            import xlsx_writer
                            rows = rows[:5000]
                            q = str(req.get('q') or '').strip()[:40] or '搜索结果'
                            p = export_dir() / ('导出_%s_%s.xlsx' % (
                                _export_slug(q), time.strftime('%Y%m%d-%H%M%S')))
                            p.write_bytes(xlsx_writer.build_xlsx(
                                xlsx_writer.result_rows(rows), head=xlsx_writer.HEAD,
                                widths=xlsx_writer.WIDTHS, sheet=q))
                            subprocess.Popen(['explorer', '/select,', str(p)])
                            body = json.dumps({'ok': True, 'path': str(p), 'n': len(rows)},
                                              ensure_ascii=False).encode()
                        except Exception as e:
                            body = json.dumps({'ok': False, 'err': str(e)},
                                              ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/update_apply'):

                # 下载 171MB 要几十秒到几分钟，同步跑前端只能干等（fetch 连进度都没有，

                # 还可能被浏览器掐超时）。改成：立即返回 started，下载在后台线程，

                # 进度走 GET /api/update_status（_UPD）。旧版前端不认识 started 也没关系——

                # 它把 {'ok': True} 当成功显示，下载照样在后台完成并重启。

                if not _job_claim('upd_apply'):

                    body = json.dumps({'ok': False, 'err': '已有更新在进行',

                                       'k': 'e_busy'}, ensure_ascii=False).encode()

                else:

                    _UPD.update(state='download', pct=0, err='', k='', bat='')



                    def _apply():

                        try:

                            r = download_update()

                        except Exception as e:

                            r = {'ok': False, 'k': 'upd_net', 'err': str(e)[:200]}

                        if not r.get('ok'):

                            _UPD.update(state='error', err=str(r.get('err') or '下载失败')[:200],

                                        k=str(r.get('k') or 'upd_net'))

                            _job_release('upd_apply')

                            return

                        _UPD.update(state='done', pct=100, bat=str(r.get('bat') or ''))

                        # bat 在等这个 PID 消失才敢动文件，所以先睡 1.2s 再换装

                        time.sleep(1.2)

                        try:

                            subprocess.Popen(['cmd', '/c', r['bat']], close_fds=True,

                                             creationflags=0x00000008 | 0x00000200)

                        except Exception as e:

                            # 起不来就别退出：至少让用户现在这份还能用

                            _UPD.update(state='error', err=str(e)[:120], k='upd_start')

                            _job_release('upd_apply')

                            print('[!] 更新脚本起不来：%s' % e)

                            return

                        time.sleep(0.4)

                        os._exit(0)



                    threading.Thread(target=_apply, daemon=True).start()

                    body = json.dumps({'ok': True, 'started': True},

                                      ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/license_clear'):

                # 换码 / 换机器 / 售后排查用。

                # 必须排在 /api/license 前面 —— startswith 会让 '/api/license'

                # 把 '/api/license_clear' 也吃掉。

                license_clear()

                body = json.dumps({'ok': True, 'state': license_state()},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/license'):

                r = license_activate(req.get('code') or '')

                if not r.get('ok'):

                    print('[license] 激活失败：%s' % r.get('reason'))

                body = json.dumps(r, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/firstrun'):

                if req.get('pause') is not None or req.get('resume') is not None:

                    # 首启可以暂停：用户要马上用电脑，不想让磁盘一直响。

                    FIRST_RUN['paused'] = bool(req.get('pause'))

                    body = json.dumps({'ok': True, 'paused': FIRST_RUN['paused'],

                                       'state': FIRST_RUN.get('state')},

                                      ensure_ascii=False).encode()

                elif req.get('cancel'):

                    # 「先不要，我自己选文件夹」：只记下来别再自动跑，正在扫描的这次不打断

                    _settings_mark('firstrun_done', 'skip')

                    FIRST_RUN['cancel'] = True

                    FIRST_RUN['paused'] = False

                    if FIRST_RUN.get('state') == 'idle':

                        FIRST_RUN['state'] = 'idle'

                    body = json.dumps({'ok': True, 'skipped': True},

                                      ensure_ascii=False).encode()

                elif not _job_claim('firstrun'):

                    body = json.dumps({'ok': False, 'err': '已有任务在进行',

                                       'k': 'e_busy'}, ensure_ascii=False).encode()

                else:

                    threading.Thread(target=_run_job, args=('firstrun', first_run_auto),

                                     daemon=True).start()

                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/update'):

                if not _job_claim('update'):

                    body = json.dumps({'ok': False, 'err': '已有更新在进行',

                                       'k': 'e_busy'}, ensure_ascii=False).encode()

                else:

                    targets = req.get('targets')

                    bad_save = False

                    if targets is not None and len(targets) > 0:

                        targets = safe_scope_list([str(t).strip() for t in targets if str(t).strip()])

                        if not targets:

                            bad_save = True

                        # 界面上的文件夹就是用户的意思：先落盘再开跑。

                        # 原来只把 targets 传给 update()，scope.json 还是旧的，于是 index() 按旧范围过滤，

                        # 新加的文件夹一条都进不去，界面却报「更新完成」——用户只能靠猜。

                        # 落盘失败必须当场回错并**不起线程**，否则就是同一个坑换个入口复发。

                        if targets:

                            if save_scope(targets):

                                print('[i] 索引范围已按界面保存（%d 个文件夹）' % len(targets), flush=True)

                            else:

                                bad_save = True

                    elif targets is not None:

                        targets = None

                    if bad_save:

                        _job_release('update')

                        body = json.dumps({'ok': False, 'err': '索引范围没存进去',

                                           'k': 'e_scope_save'}, ensure_ascii=False).encode()

                    else:

                        threading.Thread(target=_run_job, args=('update', update, targets),

                                         daemon=True).start()

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



    def _boot_maint():

        # 启动维护放后台：把 VACUUM 这类重活放在 bind 之前，双开时两个进程会同时抠库

        # 回归测试里一律关掉（SB_NO_MAINT=1）：否则测试起的临时服务会顺手整理真库，

        # 表现为「跑一遍测试，brain.db 小了几 KB」——指纹守卫报红，却说不清是谁改的。

        if os.environ.get('SB_NO_MAINT'):

            return

        time.sleep(1.5)

        try:

            db_upkeep()      # 先收上次遗留的草稿本（断电/崩溃后 -wal 可能很大）

            db_reclaim()     # 不到阈值瞬间返回

        except Exception as e:

            _log_file('boot maintenance FAILED: %r' % (e,))

        # 老库的 dirs 没有 mtime（列是后补的）。每次启动补一小批，

        # 十几万目录全补要十几秒，不能放在启动路径上。

        try:

            with con_lock:

                left = conn().execute('SELECT COUNT(*) FROM dirs WHERE mtime IS NULL').fetchone()[0]

            if left:

                with con_lock:

                    got = backfill_dir_mtime(conn(), limit=4000)

                if got:

                    _log_file('dir mtime backfill: %d (left %d)' % (got, max(0, left - got)))

        except Exception as e:

            _log_file('dir mtime backfill FAILED: %r' % (e,))



    threading.Thread(target=_boot_maint, daemon=True).start()

    # 老库的 bigram 旁表回填（2 字中文查询提速）：分块后台跑，断点续跑。

    # 回填完才把 2 字查询路由过去（_bigram_ready），没完成前照旧走 LIKE，不回退。

    threading.Thread(target=_bigram_backfill, daemon=True).start()

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

    start_watcher()              # 新建/改名/删除的文件实时进库，不用再点「更新」

    threading.Thread(target=_thumb_sweep, daemon=True).start()

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

        # 退出码是给 bat 看的：失败还返回 0 的话，黑窗最后一行照样打印"全部完成"，

        # 用户根本不知道这次没更新成功（2026-11-04 靠"没更新成功却说完成"踩过）。

        sys.exit(0 if PROG.get('state') == 'done' else 2)

    elif cmd == 'search':

        if len(argv) < 2:

            print('用法：python brain.py search 关键词 [条数] [扩展名] [路径过滤]')

            sys.exit(1)

        search(argv[1], int(argv[2]) if len(argv) > 2 else 20,

               argv[3] if len(argv) > 3 else None, argv[4] if len(argv) > 4 else None)

    elif cmd == 'repair':

        # 手动跑一次「范围认亲」：更新时会自动调，这里留着给排障/手工补跑

        fixed = repair_scope()

        print('修好了 %d 个：%s' % (len(fixed), '；'.join(f'{a} ⇒ {b}' for a, b in fixed))

              if fixed else '没有需要修复的索引范围（或者没认出来，一条没动）。')

    elif cmd == 'prune':

        # --force：人已经确认过「这些文件确实删了」，跳过"一次删太多"的刹车。

        # 只有【强制清理.bat】会用到；范围失效（目录不存在）的那道保护不受影响。

        prune(force=('--force' in argv))

        sys.exit(2 if PROG.get('state') == 'error' else 0)

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
