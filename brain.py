#!/usr/bin/env python3

# -*- coding: utf-8 -*-

"""brain.py — 本地Memex（轻量版）：全文索引 + 搜索，全部本地完成



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

import queue

import shutil

import re

import apprank

import license_core

import quick_switch

try:
    from pro import pro_api
except ModuleNotFoundError as e:
    # pro 包整体不存在 = 公开源码版，正常降级为免费。
    # 包内某个模块缺失是打包事故，不能静默吞掉，否则会变成「装了 exe 却没 Pro」。
    if e.name != 'pro':
        raise
    pro_api = None

import scan_docs

# 第 382 项：打开/定位这类「本机特有动作」不再直接写 os.startfile / explorer，
# 统一走平台层（Windows 行为不变，mac/linux 由 plat/shell.py 分派）。
import plat.shell as plat_shell
import plat.paths as plat_paths
# 第 385 项：Word/Excel/PPT 借本机 Office/WPS 转 PDF 出页图（详见 doc_render.py 顶部）
import doc_render
# 第 387 项：转换改成「后台排队 + 复用引擎」（WPS 实例闲置才 Quit）。
# 进程退出时把复用的实例收干净，别留后台 WPS。
import atexit as _atexit
_atexit.register(doc_render.shutdown_apps)

# 第 243 项：音频时长探测（纯标准库，不依赖 mutagen/ffmpeg）。
import audio_probe

# 第 246 项：商店/MSIX 应用的显示名在虚拟 shell:AppsFolder 里，磁盘遍历永远看不到。
import app_entries

# 第 297 项：系统程序（设置 / cmd / 记事本…）既不在 C:\Windows\System32（系统目录默认排除），
# 也不在开始菜单的 AppID 清单里（设置是 MSIX、cmd 只是个命令），所以查询时单独补一份。
import sys_apps

import sqlite3

import subprocess

import sys

_B = sys.modules[__name__]   # 传给私有 pro_api 的能力上下文

import threading

import time

import urllib.parse

import urllib.request

import netproxy          # 第 299 项：全程序唯一的代理出口

import zipfile

from html.parser import HTMLParser

from pathlib import Path



APP_VERSION = 'v0.9.56'          # 0.x = 预览 / 开发版；到达 1.0 即视为正式版

BUILD_DATE = '2026-11-09'

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

# 更新包签名用的 Ed25519 公钥（第 286 项）。私钥只在作者本机
# `开发测试版/keys/upd_ed25519.key`，**不进公开仓库、不进 exe**；
# 发布时用 tmp/sign_update.py 把 sha256+sig 写进 version.json，
# 客户端下载 zip 后先验签再解压 —— 堵住「镜像/version.json 被投毒 → 任意代码执行」。
# 换私钥 = 所有旧 version.json 上的签名作废，必须同时发一版新客户端。
UPD_PUBKEY_HEX = '487cd5daf1859553ff28f44ee192a1c2e853b925e7a321a1b8999e8b60c9b9a3'


def upd_msg(version, name, sha256):
    """签名/验签用的固定格式串。版本号、文件名、哈希任一被改都会验不过。"""
    return ('memex-update-v1|%s|%s|%s' % (version, name, sha256)).encode('utf-8')


def upd_sig_ok(version, name, sha256, sig_hex):
    """校验 version.json 里的签名。任何格式问题都返回 False，不抛异常。"""
    try:
        sig = bytes.fromhex(str(sig_hex or '').strip())
    except ValueError:
        return False
    if len(sig) != 64 or not sha256:
        return False
    try:
        pub = bytes.fromhex(UPD_PUBKEY_HEX)
    except ValueError:
        return False
    return license_core.verify(pub, upd_msg(version, name, sha256), sig)


def upd_check_file(info, asset, zpath):
    """下载完成后、解压前的那道门（第 286 项）。返回 (ok, k, err)。

    抽成独立函数是为了能单测：真的去跑 download_update 需要网络 + 90MB 文件。
    """
    asset = asset or {}
    sha_want = str(asset.get('sha256') or '').strip().lower()
    sig = str(asset.get('sig') or '').strip()
    try:
        sha_got = file_sha256(zpath, limit=10 ** 12)
    except Exception:
        sha_got = ''
    if sha_want and sha_got != sha_want:
        return False, 'upd_badsig', 'sha256 不一致（下载到的包不是发布的那一份）'
    if bool((info or {}).get('need_sig')):
        if not (sha_want and sig and upd_sig_ok((info or {}).get('latest'),
                                               str(asset.get('name') or ''),
                                               sha_want, sig)):
            return False, 'upd_badsig', 'version.json 缺签名或验签失败，已拒绝安装'
    return True, '', ''



# 授权码和「上次见到的时间」都存在数据目录里。存明文码而不是解析结果：

# 换公钥/改解析逻辑后老码还能重新解释，解析结果一旦落盘就固化了。

LICENSE_FILE = 'license.json'



# /api/open_url 只认本仓库下的地址：这条路由会真的调起系统浏览器，

# 放开任意 URL 等于给页面一个「随手打开钓鱼站」的按钮。

# 注意别写成 startswith(OFFICIAL_REPO) —— 那样 `.../second-brain-evil` 也能过。

def url_allowed(u):

    u = (u or '').strip()

    if u == OFFICIAL_REPO or u.startswith(OFFICIAL_REPO + '/'):

        return True

    # 备用下载页（国内网盘 / Gitee 页面）：只放行本机配置里那一个精确 URL，

    # 不按域名放行，避免「配了网盘就顺手能打开任意网址」。

    try:

        _, page = update_sources()

    except Exception:

        page = ''

    return bool(page) and u == page





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

            'plan': 'free',
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

                res = license_core.parse_code(code, LICENSE_PUBKEY_HEX,
                                     hwid=license_core.hwid())

                for k in ('valid', 'reason', 'edition', 'kind', 'name', 'ident',

                          'exp', 'issued', 'features', 'plan'):

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

    # 第 277 项：授权校验结果。过期/机器码不符/时钟回拨都记一笔 ——
    # 这类问题用户只会说「会员突然没了」，没有别的线索。
    try:

        if metrics is not None:

            _sub = str(st.get('reason') or ('valid' if st.get('valid') else 'free'))[:20]

            metrics.add('license', 'ok' if st.get('valid') else 'skip', sub=_sub)

            if st.get('clock_warn'):

                metrics.count('license', 'clock_warn')

    except Exception:

        pass

    _LIC['loaded'] = True

    _LIC['state'] = st

    return st




def license_feature(name):

    """Pro 功能开关（第 293 项定调）。

    真正挂在这儿的只有 **export**（`/api/export` 那一支）。
    `ocr_batch`（批量 OCR 队列）**故意不门控**：它是「补齐索引」的兜底，
    扫描件搜不到时免费用户必须能自救，锁了等于自毁口碑。
    索引 / 监听 / 搜索 / 预览 / 入库 OCR 一律不在这里出现，将来也别加。

    commercial（商用许可）不看签发时的 f 列表，直接由 plan 推导
    （pro / team 码自动带），避免签发漏字段导致「买了商用却显示免费」。
    """

    st = license_state()

    if not bool(st['valid']):

        return False

    if name == 'commercial':

        return str(st.get('plan') or 'free') in ('pro', 'team')

    return name in (st.get('features') or [])



def _license_online(code):

    """在线核销一次（第 170 项）。

    只把「码的 SHA-256 前 40 位 + 本机指纹」发给作者侧的核销端点：
    明文码永远不会离开本机。端点不可用、超时、返回怪东西一律降级为 offline ——
    本地 Ed25519 验签已经通过了，不能因为没网就把付费用户挡在门外。

    返回 (状态, 说明)，状态：ok / bound / revoked / offline。
    """

    try:

        conf = error_report.ingest_config()

        url = str(conf.get('url') or '')

    except Exception:

        url = ''

    if not url:

        return ('offline', 'no endpoint')

    try:

        parts = urllib.parse.urlsplit(url)

        base = '%s://%s%s' % (parts.scheme, parts.netloc, parts.path.rsplit('/', 1)[0])

        token = urllib.parse.parse_qs(parts.query).get('token', [''])[0]

        if not token:

            return ('offline', 'no token')

        digest = hashlib.sha256(code.encode('utf-8')).hexdigest()[:40]

        body = json.dumps({'code': digest, 'hwid': license_core.hwid()}).encode('utf-8')

        req = urllib.request.Request(

            base + '/activate?token=' + urllib.parse.quote(token),

            data=body,

            headers={'Content-Type': 'application/json',

                     'User-Agent': 'SecondBrain-License/1'})

        with netproxy.urlopen(req, timeout=4) as f:

            d = json.loads(f.read().decode('utf-8') or '{}')

        if d.get('ok'):

            return ('ok', str(d.get('bound') or ''))

        err = str(d.get('err') or '')

        if err in ('bound', 'revoked'):

            return (err, str(d.get('bound') or ''))

        return ('offline', err or 'unknown')

    except urllib.error.HTTPError as e:

        try:

            d = json.loads(e.read().decode('utf-8') or '{}')

        except Exception:

            d = {}

        err = str(d.get('err') or '')

        if err in ('bound', 'revoked'):

            return (err, '')

        return ('offline', 'http %s' % e.code)

    except Exception as e:

        return ('offline', '%s: %s' % (type(e).__name__, str(e)[:80]))



def license_activate(code):

    """保存并校验授权码。校验不过不落盘，避免把一个坏码留在机器上。"""

    code = (code or '').strip().replace('\n', '').replace('\r', '').replace(' ', '')

    res = license_core.parse_code(code, LICENSE_PUBKEY_HEX,
                                     hwid=license_core.hwid())

    if not res['valid']:

        return {'ok': False, 'reason': res['reason'], 'state': license_state()}

    # 本地验签 + 机器指纹都过了，再尽力在线核销一次（第 170 项）。
    # offline 不等于失败：没网、端点没配都照常激活，只是这台机器不会被记进台账。
    online, odetail = _license_online(code)

    if online in ('bound', 'revoked'):

        return {'ok': False, 'reason': online, 'detail': odetail, 'state': license_state()}

    p = license_path()

    payload = {'code': code, 'activated': time.strftime('%Y-%m-%d %H:%M'),

               'last_seen': time.strftime('%Y-%m-%d'),

               'hwid': license_core.hwid(),

               'online': online}

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

    # 第 299 项：走用户的代理设置（系统/自定义/直连），不再让 urllib 自己猜
    with netproxy.urlopen(req, timeout=timeout) as r:

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

    # 先试镜像里的 version.json（国内可达）；全都失败再回 GitHub API。
    # 三个源**并发**取，谁先回来用谁：串行时 jsDelivr 偶尔慢到 6s，用户点「检查更新」
    # 要等十几秒，体感像卡死（实测 12.6s）。
    _urls = [s % REPO_SLUG for s in _VER_SOURCES]
    _res = {}

    def _one(_u):
        try:
            _res[_u] = json.loads(_http_get(_u, timeout=min(timeout, 5),
                                            limit=256 * 1024).decode('utf-8', 'replace'))
        except Exception:
            pass

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=len(_urls), thread_name_prefix='updchk') as ex:
            _futs = [ex.submit(_one, u) for u in _urls]
            for _f in as_completed(_futs, timeout=min(timeout, 5) + 1):
                try:
                    _f.result()
                except Exception:
                    pass
    except Exception:
        for _u in _urls:          # 并发不可用就退回串行，功能不能因此断
            _one(_u)

    for _u in _urls:              # 按原顺序取第一个能用的（顺序即可信度）
        _info = _res.get(_u)
        if not _info:
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

                            'size': a.get('size') or 0, 'sha256': '', 'sig': ''}

            break

    # 这条分支直接走 api.github.com（TLS 直达官方），不经过镜像，所以不要求签名；
    # version.json 分支则是镜像/CDN，见 _ver_from_json 的 need_sig。
    out['need_sig'] = False

    return out





# 国内直连 GitHub 经常超时：版本信息先读仓库里的 version.json（jsDelivr 有国内节点），

# 下载安装包再按 ghfast / ghproxy 这类加速前缀依次重试。失败才回 GitHub 官方地址。

_VER_SOURCES = (

    'https://cdn.jsdelivr.net/gh/%s@main/version.json',

    'https://raw.gitmirror.com/%s/main/version.json',

    'https://raw.githubusercontent.com/%s/main/version.json',

)

_DL_MIRRORS = ('https://ghfast.top/', 'https://ghproxy.net/', 'https://gh-proxy.com/')





def update_sources():

    """国内下载源（本机可编辑，不进仓库、不进同步清单）。



    文件：app_dir()/update_sources.json

        {

          "urls": ["https://<国内直链>/SecondBrain-v0.9.xx-portable.zip"],

          "manual_page": "https://<网盘或 Gitee 的下载页>"

        }



    urls 必须是**能直接下载的 https 直链**（COS/OSS/Gitee 附件/自建服务器都行）；

    网盘分享页通常需要登录或跳转，程序没法直接下，放到 manual_page 里给用户手动点。

    返回 (直链列表, 手动下载页)。文件不存在/写坏都静默按空处理，绝不拖垮检查更新。

    """

    urls, page = [], ''

    try:

        d = json.loads((app_dir() / 'update_sources.json').read_text(encoding='utf-8'))

        if isinstance(d, dict):

            urls = [str(x).strip() for x in (d.get('urls') or [])

                    if str(x).strip().startswith('https://')][:8]

            page = str(d.get('manual_page') or '').strip()[:300]

            if not page.startswith('https://'):

                page = ''

    except Exception:

        pass

    return urls, page





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

           'newer': False, 'legacy': False, 'asset': None,

           'manual_page': str(info.get('manual_page') or '')[:300],

           'asset_urls': [],

           # version.json 走的是镜像/CDN，**必须带签名**（第 286 项）：
           # 少一个字段就等于把「投毒 → 覆盖程序」的口子留着。
           'need_sig': True}

    if latest[0] == 6:

        out['legacy'] = True

        return out

    out['newer'] = latest > cur

    for a in info.get('assets') or []:

        name = str(a.get('name') or '')

        u = str(a.get('url') or '').strip()

        if not name.lower().endswith('.zip') or not u:

            continue

        if not out['asset']:

            out['asset'] = {'name': name, 'url': u, 'size': a.get('size') or 0,

                            'sha256': str(a.get('sha256') or '').strip().lower(),

                            'sig': str(a.get('sig') or '').strip().lower()}

        out['asset_urls'].append(u)

    return out





_UPD_HOSTS = ('github.com', 'objects.githubusercontent.com',

              'github-releases.githubusercontent.com', 'codeload.github.com')



# 一键升级的下载/换装进度（/api/update_status 轮询用）。

# download_update 改成后台线程跑以后，POST /api/update_apply 立刻返回，

# 171MB 的下载要几十秒到几分钟——前端必须有真实进度可看，而不是干等 fetch。

_UPD = {'state': 'idle', 'pct': 0, 'err': '', 'k': '', 'bat': '', 'signed': None}





def _safe_asset_url(u, extra_hosts=None):

    """升级包只从 https 下，且域名必须是 GitHub 官方或配置里出现过的国内源。



    json / 配置文件都是外部数据，不能拿它当任意 URL 发射器；extra_hosts 由

    _update_candidates() 从配置里的直链域名算出，只放行「用户自己填过的那些」。

    """

    try:

        from urllib.parse import urlsplit

        sp = urlsplit(str(u or ''))

    except Exception:

        return ''

    if sp.scheme != 'https':

        return ''

    h = (sp.hostname or '').lower()

    if h not in _UPD_HOSTS and h not in (extra_hosts or ()):

        return ''

    return str(u)





def _github_asset():

    """version.json 没写 assets 时，回 GitHub Release API 要一个 zip 直链。



    以前 version.json 只有 version/notes，没有可下载地址 —— 走国内 jsDelivr 源

    查到新版后，一键升级会直接报 upd_noasset。这里补上兜底。

    """

    try:

        d = json.loads(_http_get(

            'https://api.github.com/repos/%s/releases/latest' % REPO_SLUG,

            timeout=8, limit=2 * 1024 * 1024).decode('utf-8', 'replace'))

        for a in d.get('assets') or []:

            n = str(a.get('name') or '')

            if n.lower().endswith('.zip') and 'core' not in n.lower():

                return str(a.get('browser_download_url') or '')

    except Exception:

        pass

    return ''





def _update_candidates(info, local_urls, github_url=''):

    """下载优先级：本机国内直链 → version.json 的多个直链 → GitHub 加速前缀 → GitHub 官方。



    原来把 GitHub 官方放第一位，国内常常直接超时；现在国内源优先，官方垫底。

    """

    from urllib.parse import urlsplit

    raw = list(local_urls) + list(info.get('asset_urls') or [])

    hosts = set()

    for u in raw:

        try:

            h = (urlsplit(str(u)).hostname or '').lower()

            if h:

                hosts.add(h)

        except Exception:

            pass

    out = []



    def add(u):

        su = _safe_asset_url(u, hosts)

        if su and su not in out:

            out.append(su)



    for u in raw:

        add(u)                       # 国内直链（配置 + version.json）

    gh = _safe_asset_url(github_url)

    if gh:

        for m in _DL_MIRRORS:        # 加速前缀先试，官方最后

            mu = m + gh

            if mu not in out:

                out.append(mu)

        if gh not in out:

            out.append(gh)

    return out





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

    _local_urls, _local_page = update_sources()

    # version.json 可能只写了版本号没写 assets —— 回 GitHub API 兜底要一个 zip

    url = _safe_asset_url(asset.get('url')) or _github_asset()

    _cands = _update_candidates(info, _local_urls, url)

    if not _cands:

        return {'ok': False, 'k': 'upd_noasset',

                'err': '没找到可下载的安装包；可在 update_sources.json 里填国内直链'}



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

        # 国内直链 → 加速前缀 → GitHub 官方，谁先通就用谁

        _last = None

        _UPD.update(state='download', pct=0, err='', k='')

        for _u in _cands:

            try:

                with netproxy.urlopen(urllib.request.Request(_u, headers=_UA),

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

        # ---- 先验签，再解压（第 286 项）------------------------------------

        # 镜像 / version.json 被换掉时，这里拦下「覆盖程序」的最后一步。

        # version.json 没声明任何 asset 时，走的是 GitHub 官方兜底（TLS 直达），

        # 这种情况不强制签名，否则「还不会签名」的旧发布直接升不上来。

        _info_sig = dict(info or {})

        if not _info_sig.get('asset_urls'):

            _info_sig['need_sig'] = False

        _ok, _k, _err = upd_check_file(_info_sig, asset, zpath)

        if not _ok:

            try:

                zpath.unlink()

            except Exception:

                pass

            return {'ok': False, 'k': _k, 'err': _err}

        _UPD['signed'] = bool(str(asset.get('sig') or '').strip())

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





# 窗口**实际**有没有摘掉系统标题栏（第 180 项）。启动时由 app_window 写入，
# None 表示「还不知道，按配置猜」。前端必须看这个值而不是配置值：
# 用户在设置里改了开关但没重启时，配置已经是新值、窗口样式还是旧值 ——
# 只看配置会出现「系统标题栏还在 + HTML 也画了一条」的双标题栏。
TITLEBAR_ACTIVE = None


def titlebar_on():
    """这条 HTML 标题栏该不该画：以窗口实际样式为准，配置只作兜底（第 180 项）。

    窗口样式只在启动时改，所以配置值只影响「下次开窗」；前端据它决定要不要画
    HTML 标题栏 —— 两边必须同一个来源，否则会出现「系统标题栏没了、HTML 也没画」
    的光秃秃窗口，或者两条标题栏叠着。
    """
    if TITLEBAR_ACTIVE is not None:
        return bool(TITLEBAR_ACTIVE)
    try:
        cfg = json.loads((app_dir() / 'settings.json').read_text(encoding='utf-8'))
        return bool(cfg.get('titlebar', True))
    except Exception:
        return True

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





def sys_locale_info():
    """Windows 区域格式（只读）：locale 名 + 短日期样式 + 短时间样式。

    第 308 项：Listary 被骂了一年多的「日期格式不跟系统」。
    这里直接问 Windows 要**格式串**，前端按串渲染，而不是自己猜 (1/2/2026)。
    失败一律给空串 —— 前端有 Intl / ISO 兜底，不该因为读不到格式把界面弄坏。
    """
    out = {'locale': '', 'date': '', 'time': ''}
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetUserDefaultLocaleName.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        k.GetUserDefaultLocaleName.restype = ctypes.c_int
        b = ctypes.create_unicode_buffer(128)
        if k.GetUserDefaultLocaleName(b, 128):
            out['locale'] = b.value
        k.GetLocaleInfoEx.argtypes = [ctypes.c_wchar_p, ctypes.c_uint,
                                      ctypes.c_wchar_p, ctypes.c_int]
        k.GetLocaleInfoEx.restype = ctypes.c_int
        loc = out['locale'] or None

        def _info(kind):
            buf = ctypes.create_unicode_buffer(64)
            if k.GetLocaleInfoEx(loc, kind, buf, 64):
                return buf.value
            return ''

        # LOCALE_SSHORTDATE=0x1F / LOCALE_STIMEFORMAT=0x1003 / LOCALE_SSHORTTIME=0x79
        out['date'] = _info(0x1F)
        out['time'] = _info(0x79) or _info(0x1003)
    except Exception:
        pass
    return out


def app_dir():
    """放数据的地方（数据库/settings/scope）——Windows 上默认还是程序所在目录。

    写不进去时退到 `%LOCALAPPDATA%\\SecondBrain\\data`（老注释里的原因：exe 放在只读位置、
    从压缩包直接双击、目录被安全软件锁住时，第一次查库就报
    "attempt to write a readonly database"，窗口里只剩「127.0.0.1 未发送任何数据」）。

    第 384 项：判定逻辑搬到 `plat.paths.pick_data_dir()`（纯函数、可单测），
    这里只负责**缓存结果 + 保留原来那句提示和 DATA_FALLBACK 语义**。
    Windows 上的两条路径（程序目录 → LOCALAPPDATA 兜底）逐字未改。

    可用环境变量 SB_DATA_DIR 强制指定（给了就照它办，不偷偷换地方）。
    """
    if _DATA_DIR:
        return _DATA_DIR[0]

    global DATA_FALLBACK

    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    picked, used_fallback = plat_paths.pick_data_dir(install=str(base), env=os.environ)

    if used_fallback and picked != str(base):
        print('[i] 程序目录写不进去，数据改放到：%s' % picked, flush=True)
        DATA_FALLBACK = True

    _DATA_DIR.append(Path(picked))
    return Path(picked)



DB = app_dir() / 'brain.db'

SCOPE_FILE = app_dir() / 'scope.json'



# 索引集：DB / SCOPE_FILE 都是运行时可重赋值的全局。所有 sqlite3.connect(DB)

# 与 scope 读取都在调用时取值，切库只要改这两个变量并关掉旧连接，

# 不必逐个改那 8 处引用，也不用迁移已有的 brain.db。

import libsets

import error_report


def _errlog_lang():
    """错误报告预览用哪种语言：跟界面语言（第 281 项：以前整块写死中文）。"""
    try:
        import translator as _tl
        return _tl.ui_lang() or 'zh-CN'
    except Exception:
        return 'zh-CN'


try:
    import feature_metrics as metrics
except Exception:                                     # pragma: no cover
    metrics = None

try:
    import breadcrumb as bc
except Exception:                                     # pragma: no cover
    bc = None

import settings_backup



def _apply_lib_paths(d, lib_id):

    """把全局 DB / SCOPE_FILE 指向某个索引集；调用方必须已持有 con_lock。"""

    global DB, SCOPE_FILE, _con

    lib = libsets.active_lib(d, lib_id)

    if not lib:

        return False

    if globals().get('_con') is not None:

        try:

            _con.close()

        except Exception:

            pass

        _con = None

    DB = libsets.db_path(app_dir(), lib)

    try:
        import clipboard_mgr
        clipboard_mgr.set_db_path(DB)
    except Exception:
        pass

    SCOPE_FILE = libsets.scope_path(app_dir(), lib)

    try:

        DB.parent.mkdir(parents=True, exist_ok=True)

    except OSError:

        pass

    return True



def _boot_lib_paths():

    """启动时恢复上次的活动索引集；默认库就是原有的 brain.db，零迁移。"""

    try:

        d = libsets.load(app_dir())

        _apply_lib_paths(d, d['active'])

    except Exception:

        pass



def switch_lib(lib_id):

    """切换索引集：全程持 con_lock，切完立刻建表，返回 (ok, 说明)。"""

    with con_lock:

        d = libsets.load(app_dir())

        if not _apply_lib_paths(d, lib_id):

            return False, '索引集不存在'

        libsets.set_active(app_dir(), lib_id)

        try:

            conn()

        except Exception:

            pass

        return True, lib_id



def lib_overview():

    """索引集列表 + 每个库的文件/正文条数（独立只读连接，不影响全局 conn）。"""

    d = libsets.load(app_dir())

    out = []

    for lib in d['libs']:

        fp = libsets.db_path(app_dir(), lib)

        files = docs = 0

        try:

            c = sqlite3.connect(fp.as_uri() + '?mode=ro', uri=True, timeout=1)

            try:

                files = c.execute('SELECT count(*) FROM files').fetchone()[0]

                docs = c.execute('SELECT count(*) FROM docs').fetchone()[0]

            finally:

                c.close()

        except Exception:

            pass

        out.append({'id': lib['id'], 'name': lib['name'], 'files': files,

                    'docs': docs, 'scope': str(libsets.scope_path(app_dir(), lib)),

                    'active': lib['id'] == d['active']})

    return {'ok': True, 'active': d['active'], 'libs': out}



def lib_search_all(q, limit=30):

    """跨索引集搜索：串行切换活动库复用 search_rows，finally 一定切回原库。"""

    d = libsets.load(app_dir())

    orig = d['active']

    out = []

    with con_lock:

        try:

            for lib in d['libs']:

                if not _apply_lib_paths(d, lib['id']):

                    continue

                try:

                    rows = search_rows(conn(), q, limit)

                except Exception:

                    rows = []

                for pp, snip in rows:

                    out.append({'p': pp, 'b': snip, 'lib': lib['id'], 'n': lib['name']})

        finally:

            _apply_lib_paths(d, orig)

    return out[:limit]



def saved_path():

    return app_dir() / 'saved_searches.json'



def saved_load():

    try:

        d = json.loads(saved_path().read_text(encoding='utf-8'))

        if isinstance(d, dict) and isinstance(d.get('items'), list):

            return [x for x in d['items'] if isinstance(x, dict) and str(x.get('q') or '').strip()][:100]

    except Exception:

        pass

    return []



def saved_save(items):

    pp = saved_path()

    tmp = pp.with_name(pp.name + '.tmp')

    tmp.write_text(json.dumps({'items': items[:100]}, ensure_ascii=False, indent=2), encoding='utf-8')

    os.replace(str(tmp), str(pp))

    return True



def saved_add(q, name=''):

    items = saved_load()

    q = str(q or '').strip()[:200]

    if not q:

        return False, '空的搜索词'

    if any(x.get('q') == q for x in items):

        return False, '已经保存过了'

    items.insert(0, {'q': q, 'name': str(name or '').strip()[:40] or q, 't': time.time()})

    saved_save(items)

    return True, ''



def saved_remove(q):

    items = saved_load()

    left = [x for x in items if x.get('q') != q]

    saved_save(left)

    return len(items) != len(left)



def diagnose(limit=60):

    """「为什么搜不到」：按 meta.reason 分组 + 列出前 N 条失败文件。"""

    try:

        with con_lock:

            c = conn()

            groups = c.execute("SELECT COALESCE(reason,'?'), COUNT(*) FROM meta WHERE reason IS NOT NULL GROUP BY 1 ORDER BY 2 DESC").fetchall()

            rows = c.execute('SELECT path, reason FROM meta WHERE reason IS NOT NULL ORDER BY path LIMIT ?', (max(1, min(limit, 300)),)).fetchall()

        return {'ok': True, 'groups': [{'why': r[0], 'n': r[1]} for r in groups],

                'items': [{'p': r[0], 'why': r[1]} for r in rows],

                'total': sum(r[1] for r in groups)}

    except Exception as e:

        return {'ok': False, 'err': str(e), 'groups': [], 'items': [], 'total': 0}



def diagnose_retry(paths):

    """把失败条目重新排进正文队列；_bodyq_offer 要 6 元组，第 6 位是 mtime。"""

    rows = []

    for pp in (paths or [])[:100]:

        pp = str(pp).strip()

        if not pp:

            continue

        try:

            mt = os.path.getmtime(pp)

        except OSError:

            mt = time.time()

        rows.append((pp, 0, 0, 0, 0, mt))

    _bodyq_offer(rows, [])

    return {'ok': True, 'n': len(rows)}



_PAGE_CACHE = {}



def pdf_page_for(path, kw, max_pages=30):

    """命中 PDF 时按需定位页码：读文本层前 N 页找关键词，带 mtime 缓存。

    不改动索引结构；扫描件（无文本层）返回 None，由界面提示。"""

    pp = str(path or '')

    k = str(kw or '').strip().lower()

    if not pp.lower().endswith('.pdf') or not k:

        return None

    try:

        mt = os.path.getmtime(pp)

    except OSError:

        return None

    key = (pp, int(mt), k[:40])

    if key in _PAGE_CACHE:

        return _PAGE_CACHE[key]

    page = None

    try:

        from pypdf import PdfReader

        r = PdfReader(pp)

        for i, pg in enumerate(r.pages[:max_pages]):

            if k in (pg.extract_text() or '').lower():

                page = i + 1

                break

    except Exception:

        page = None

    if len(_PAGE_CACHE) > 500:

        _PAGE_CACHE.clear()

    _PAGE_CACHE[key] = page

    return page



def pdf_page_url(path, n):

    """只给索引里的本地 PDF 生成 file://...#page=N；外部路径一律拒绝。"""

    pp = str(path or '').strip()

    if not pp.lower().endswith('.pdf') or not _local_only(pp):

        return None

    if not os.path.isfile(pp):

        return None

    with con_lock:

        try:

            ok = bool(conn().execute('SELECT 1 FROM meta WHERE path=? LIMIT 1', (pp,)).fetchone())

        except Exception:

            ok = False

    if not ok and not ev_recent(pp):

        return None

    try:

        nn = max(1, min(int(n), 9999))

        return Path(pp).as_uri() + '#page=%d' % nn

    except Exception:

        return None


# 第 326 项：正文上限 2MB → 512KB。
# 实测 9,668 篇正文合计 700MB，其中 337 篇 >200KB 就占了 565MB，
# 而且大头是「渗透测试工具/爆破字典」里几百个 6MB 的密码字典 txt ——
# 这些不是文档，全文进库只让库涨到 2.2GB、搜索一点没变快。
# 512KB 约等于 250 页纯文字，够判断「这文件里有没有我要的东西」；
# 再往前的部分用文件名/目录照样能找，点开详情也是直接读原文件。
MAX_BODY = 512 * 1024       # 单文档正文上限 512KB

# 第 326 项：「数据文件」不是文档。超过 8MB 的 txt/csv/log 基本都是密码字典、
# 爆破字典、聊天/日志导出 —— 整篇读进来只会把正文库撑到几百 MB。
TEXT_DUMP_EXTS = frozenset(('.txt', '.log', '.csv', '.tsv', '.json', '.xml', '.ini', '.yaml', '.yml'))

TEXT_DUMP_MAX = 8 * 1024 * 1024

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

# 第 366 项：整理索引库的进度对象（定义在这里，是因为 _job_busy 也要读它）
COMPACT = {'state': 'idle', 'phase': '', 'done': 0, 'total': 0, 'k': '', 'a': {},
           'msg': '', 'err': '', 'before': 0.0, 'after': 0.0, 'secs': 0.0,
           'at': 0.0, 'auto': False}
_COMPACT_LOCK = threading.Lock()





def _job_busy():

    return (PROG.get('state') in ('busy', 'running') or NAMES.get('state') == 'busy'

            or DISCOVER.get('state') == 'busy' or SELFTEST.get('state') == 'busy'

            # 第 366 项：整理索引库也算「有任务在跑」，别让扫描/更新挤进来抢锁
            or COMPACT.get('state') == 'busy')





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

        # 第 294 项：不可达的网络根这一轮跳过（带超时探测），否则整轮卡在智能扫描
        roots = [r for r in roots if _path_alive(r)]

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

        # 第 277 项：扫描完成记一笔。真的失败（读不了盘/权限）才记 fail，
        # 「范围外/重复跳过」不算失败，否则成功率永远难看。
        try:

            if metrics is not None:

                metrics.add('index', 'ok', sub='discover')

                metrics.note('index', '文档 %d 组 %d' % (docs_total, len(lst)))

        except Exception:

            pass

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

# 第 236 项：内置默认排除的目录名（steamapps / appcache / gpucache / node_modules /
# .venv / .next …）一起剪掉。这些名字本身就足以判定「里面没有用户会搜的文件」，
# 不再要求用户手工往 scope.json 里一条条加。

NAMES_SKIP_DIRS |= set(scan_docs.DEFAULT_SKIP_NAMES)

# 只索引用户真正会搜/会打开的文件类型，剔除游戏引擎原始资源（.dds/.uasset/.tga/.wem…）

# 与 scan_docs.ALL_FILES 保持一致，保证「文件名索引」和「正文索引」范围不打架。

NAMES_EXTS = scan_docs.ALL_FILES

# 名字索引只存路径，视频/压缩包存进来很便宜也很有用，给一个宽松上限挡住极少数超大文件。
#
# 第 242 项：**取消 2GB 上限，改成不设上限**（NAMES_SIZE_LIMIT = 0 表示不限）。
# 依据是 Everything 的做法：它的**文件名索引没有体积上限**，size 上限只作用于
# **内容索引**（voidtools.com/support/everything/options 的 "Maximum size"：
# "Exclude files with a size larger than specified"，那一项在 Content 分页）。
# 用户也指出：2GB 以上的常见就是压缩包和系统镜像，那正是「按名字找得到」的东西 ——
# 按体积排掉等于把最有价值的文件名索引堵死。
#
# 为什么原来有这条：怕虚拟磁盘/镜像把索引撑大。但名字索引一行只存 path/name/size/mtime，
# 一条记录几百字节；就算多收 10 万条，也只是几十 MB 的 sqlite，与搜索速度无关。
# 真要防的是「同名的几十 TB 稀疏文件」，那在 NTFS 上是异常，交给别处管。
#
# 正文那条上限仍然保留（scan_docs.SIZE_LIMIT = 80MB）：那是**要真读内容**的清单，
# 读一个 40GB 的 ISO 和读一个 5MB 的 txt 成本天差地别，限制是对的
# （Everything 的内容索引同样有 max_size）。
NAMES_SIZE_LIMIT = 0   # 0 = 不限（与 Everything 的文件名索引一致）

# 第 235 项：媒体文件不受上面那条上限约束。这条上限的初衷是挡住虚拟磁盘/镜像，
# 但 4K 影片动辄 20~60GB —— 用户搜片名时最想找的恰恰是它们，结果一条都进不来。
# 名字索引只存一行 path/name/size，多收几千条成本可以忽略。
#
# 第 242 项：NAMES_BIG_EXTS 保留但**不再用于放行**（上限已取消），改成给
# 「小体积图标类」判断用 —— 见 NAMES_ICON_EXTS。
NAMES_BIG_EXTS = scan_docs.MEDIA_EXTS

# 第 242 项：小体积图标不入索引（用户要求）。
# 理由：.ico 绝大多数是**应用自己的运行图标** —— 实测库里 201 条 .ico，
# 33 条在 Steam 游戏目录、18 条在 PotPlayer 扩展、12 条在 Subtitle Edit、
# 6 条在 VMware，全是程序资产；用户要找图标资源时，大的那些（>64KB）才可能是真素材。
# 阈值取 64KB：够小的图标（几 KB 的 favicon/应用图标）排掉，超过的保留。
NAMES_ICON_EXTS = {'.ico'}
NAMES_ICON_MAX_SIZE = 64 * 1024

# 第 243 项：**短音频不入索引**（用户提的「跳过 20 秒以内的 mp3」）。
#
# 出发点：文件层面分不出「用户存的 .mp3」和「软件自带的 .mp3」（第 242 项实测：
# 钉钉/腾讯会议/靶场的程序物占比差不到 3 个百分点，判据不成立）。但**时长**能分：
# 软件自带的绝大多数是提示音、铃声、切片音效（1~15 秒），用户存的是完整曲目
# （1~5 分钟）。所以用时长做粗筛。
#
# 阈值 20 秒：用户原话「歌曲通常都是一两分钟」，20 秒以下的完整歌曲基本不存在，
# 而音效/铃声/录音片段都在 20 秒以内。取 20 而不是 10，是为了不误伤短音效之外
# 的边缘情况（比如 15 秒的口播/语音备忘在 20 秒以下会被排掉 —— 这是已知取舍，
# 阈值改成 0 就完全不过滤）。
#
# 只对**能可靠解析时长**的格式生效（mp3/wav/flac/m4a，见 audio_probe.py）；
# 解析不出来（损坏文件、未知格式）一律保留 —— 宁可漏排不可错排。
NAMES_MIN_AUDIO_SECONDS = 20

# 音乐平台目录下的音频不入索引（网易云/QQ音乐/酷狗/酷我/Spotify/Apple Music…）。
# 只排**音频**，不排整个目录 —— 用户在音乐平台目录里也可能存着自己的歌单文件。
NAMES_MEDIA_APP_EXTS = {
    '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wma', '.ape', '.wav',
    '.aiff', '.dsf', '.dff', '.m4b', '.opus',
}

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

        sp = Path(scope_path) if scope_path else SCOPE_FILE

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

    # 第 236 项：内置默认排除（AppData / ProgramData / 回收站 / 旧系统目录 / WinSxS…）。
    # 和用户的 exclude_dirs 共用同一套「子串」语义，所以它们对文件名索引、正文索引、
    # 实时监听三条路径同时生效，不会出现「正文排了、文件名还在」的半挡状态。

    out.extend(os.path.normcase(s) for s in scan_docs.DEFAULT_SKIP_SEGS)

    return out





# 第 236 项：默认排除的「白名单反向词」。开始菜单物理上位于 AppData / ProgramData
# 里面，而这两棵树已被内置规则整棵排掉 —— 但「搜应用名出应用」全靠这里的 .lnk，
# 所以命中白名单的一律放行（见 _excluded）。

_KEEP_SEGS_NC = tuple(os.path.normcase(s) for s in scan_docs.DEFAULT_KEEP_SEGS)

# 第 236 项：目录名规则（steamapps / node_modules / gpucache / _internal …）预编译成一条正则。
# 必须按「路径段」精确匹配，不能用子串 —— 子串会让 `D:\我的cache资料` 一起消失；
# 也不能每条路径都 split 一遍 —— 30 万条时那点开销会变成十几秒。
# os.walk 里靠 dirs[:] 剪枝，而「对账」要判的是已经在库里的历史记录，只能走这条正则。

_SKIP_NAME_RE = re.compile(
    r'[\\/](?:' + '|'.join(re.escape(n) for n in sorted(scan_docs.DEFAULT_SKIP_NAMES))
    + r')(?=[\\/]|$)')

# 第 236 项：只对正文生效的排除段（文件名索引不受影响，仍搜得到）

_BODY_SKIP_SEGS_NC = tuple(os.path.normcase(s) for s in scan_docs.BODY_SKIP_SEGS)


def _excluded(path, exckw):

    """路径是否命中排除词。exckw 由 _scope_excludes() 预先算好，循环里别再读文件。"""

    lp = os.path.normcase(str(path))

    if any(k in lp for k in _KEEP_SEGS_NC):

        return False

    # 目录名规则：没这条的话，对账只能清掉「绝对路径段」命中的，
    # 而 steamapps / _internal / webcontent 这些靠名字判定的会一直留在库里。

    if _SKIP_NAME_RE.search(lp):

        return True

    if not exckw:

        return False

    return any(k in lp for k in exckw)





def _soft_dir_file(path, ext):
    """这个文件是不是「软件资产目录里的资源文件」（该排掉）。

    第 240 项：与全量扫描用同一套判据，否则「重建时干净了、用两天又被实时
    监听塞回来」。软件目录里的应用入口（exe/lnk…）照常保留 —— 搜「钉钉」
    还得能搜到 DingTalk.exe。

    第 242 项：判据换成「路径里有程序资产目录名 + 该段父目录里有 exe」
    （scan_docs.is_asset_path）。整目录的 looks_like_software_dir 只作为补充：
    目录本身是安装目录时，它下面的非应用入口文件一律排掉。

    第 243 项：再加一条「已知软件目录名单」（scan_docs.is_known_software_dir）。
    这条覆盖的是**非标准安装位置**（D:\\DingDing、D:\\tenxunhuiyi 这种），
    竞品（Everything / Recoll / Baloo / Tracker）都没有「自动猜软件目录」，
    全靠显式清单 —— 我们这份清单就是那份显式清单，且带用户数据豁免。
    """

    if not scan_docs.SOFTWARE_DIR_SNIFF:

        return False

    if (ext or '').lower() in scan_docs.APP_ONLY_EXTS:

        return False

    # 第 243 项：已知软件目录（具体软件名直接认定；厂商名还要目录里有 exe）。
    try:

        d0 = os.path.dirname(str(path))

        if d0 and scan_docs.is_known_software_dir(d0):

            return True

    except Exception:

        pass

    try:

        if scan_docs.is_asset_path(path):

            return True

    except Exception:

        pass

    d = os.path.dirname(str(path))

    if not d:

        return False

    try:

        return scan_docs.looks_like_software_dir(d)

    except Exception:

        return False


def _media_noise_file(path, ext):
    """这个音频文件是不是「软件/平台自带的音乐」（该排掉）。

    第 243 项，两条规则（用户提的）：

      ① 时长 < NAMES_MIN_AUDIO_SECONDS 秒 → 提示音/铃声/切片，排掉。
         用户存的歌是一两分钟，软件自带的音效多在 15 秒以内。
      ② 路径在音乐平台目录（网易云 / QQ音乐 / 酷狗 / 酷我 / Spotify /
         Apple Music…）下 → 曲库缓存，排掉。

    只对音频生效。时长解析不出来（损坏文件 / 未知格式）一律**保留** ——
    宁可漏排不可错排。
    """

    e = (ext or '').lower()

    if e not in NAMES_MEDIA_APP_EXTS:

        return False

    try:

        if scan_docs.is_media_app_path(path):

            return True

    except Exception:

        pass

    lim = NAMES_MIN_AUDIO_SECONDS

    if lim and e in audio_probe.PROBE_EXTS:

        try:

            sec = audio_probe.audio_seconds(path)

        except Exception:

            sec = None

        if sec is not None and sec < lim:

            return True

    return False


def _noindex_marked(path):
    """这个文件/目录是不是落在「不索引标记」下（.memex-noindex）。

    竞品同源设计：Recoll 的 .recoll-noindex、GNOME Tracker 的 .trackerignore、
    macOS 的 .metadata_never_index —— 在目录里放一个标记文件，整棵子树跳过。
    这是「内置软件名单」追不上新软件时的正解：用户自己一放就生效。
    """

    try:

        p = str(path)

        if scan_docs.has_noindex_marker(p):

            return True

        d = os.path.dirname(p)

        return bool(d) and scan_docs.has_noindex_marker(d)

    except Exception:

        return False


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

        # 第 236 项：AppData / ProgramData 这类树即使被写进 include 也不扫。
        # 用户当初把 AppData 勾进来多半是「全选」顺手加的，代价是 96,031 条缓存记录。

        if d and not scan_docs.never_index(d):

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

        # 第 294 项：同 docs —— 断线的 NAS 不能拖住文件名索引
        roots = [r for r in roots if _path_alive(r)]

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

                # 第 243 项：目录里有 .memex-noindex 标记 → 整棵子树不进索引。
                # 竞品同源机制（Recoll .recoll-noindex / Tracker .trackerignore /
                # macOS .metadata_never_index）：用户自己放一个标记文件就生效，
                # 比等我们更新内置软件名单快，也不存在误伤。
                if scan_docs.has_noindex_marker(cur):

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

                # 第 236 项：安装目录里只收「能启动的东西」。整盘扫描故意不跳过
                # Program Files（否则搜不到 chrome.exe / Weixin.exe），但把里面
                # 几千张 png、几千个 js 一起收进来就是纯噪声。每个目录算一次。

                cur_install = scan_docs.is_install_root(cur)

                # 第 240 项：软件安装目录内容嗅探。钉钉/腾讯会议/PotPlayer 这些
                # 装在 D:\ 下面的程序，几千张 png、几千个 js 是安装包自带素材，
                # 用户搜不到也不需要。命中后只保留应用入口（exe/lnk…），
                # 程序本身照样搜得到（「搜钉钉」要能搜到 DingTalk.exe）。
                #
                # 第 242 项：判据整个换了（按子树文件构成比例实测不可行，
                # 详见 scan_docs 里的注释）。现在是「**路径里有程序资产目录名
                # 且它的父目录里有 exe**」——在**文件层级**判断，不再整目录剪枝。
                # 这样剪掉的是具体的资源目录（剪映 Resources、有道云笔记 build、
                # GPT-SoVITS 的 venv runtime），而目录里用户的其它文件照收。
                cur_soft = False

                if scan_docs.SOFTWARE_DIR_SNIFF and any(f.lower().endswith('.exe')

                                                        for f in files):

                    cur_soft = scan_docs.looks_like_software_dir(cur)

                # 第 243 项：已知软件目录名单（chrome / pycharm / vlc / 钉钉 /
                # 腾讯会议…）。这些软件常常装在 D:\ 下面而不是 Program Files，
                # 靠 install_root 判不到；名单命中后这棵子树只保留应用入口
                # （exe/lnk…），几千张 png/几百个 js 不再进索引。
                # 用户数据目录（网盘同步、聊天文件、靶场…）优先豁免。
                if scan_docs.SOFTWARE_DIR_SNIFF and not cur_soft:

                    try:

                        cur_soft = scan_docs.is_known_software_dir(cur)

                    except Exception:

                        cur_soft = False

                for f in files:

                    ext = os.path.splitext(f)[1].lower()

                    # 只收用户会搜的文件类型，游戏引擎原始资源（.dds/.uasset/.tga…）直接跳过

                    if ext not in NAMES_EXTS:

                        continue

                    if cur_install and ext not in scan_docs.APP_ONLY_EXTS:

                        continue

                    full = os.path.join(cur, f)

                    # 第 242 项：这个文件是不是躺在「程序资产目录」里？
                    # 是的话只保留应用入口（exe/lnk…），素材不入索引。
                    if (cur_soft or scan_docs.is_asset_path(full)) and \
                            ext not in scan_docs.APP_ONLY_EXTS:

                        continue

                    # 第 243 项：短音频（<20 秒）与音乐平台目录下的音乐不入索引。
                    if _media_noise_file(full, ext):

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

                    # 第 242 项：名字索引**不再按体积排除**（Everything 的文件名索引同样
                    # 没有 size 上限，它的上限只管内容索引）。压缩包、系统镜像这些
                    # 2GB 以上的文件恰恰是最需要「按名字找得到」的。
                    # 上限为 0 表示不限，这个判断要保留成可关的开关。
                    if (NAMES_SIZE_LIMIT and sz and sz > NAMES_SIZE_LIMIT
                            and ext not in NAMES_BIG_EXTS):

                        continue

                    # 第 242 项：小体积图标不入索引。
                    # .ico 大多是应用自己的运行图标（Steam 游戏、VMware、PotPlayer
                    # 扩展……），几百条下来只有噪音；真正有用的图标资源通常 >64KB。
                    if ext in NAMES_ICON_EXTS and sz and sz <= NAMES_ICON_MAX_SIZE:

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

        # 第 246 项：商店/MSIX 应用的显示名只存在于 Windows 壳命名空间
        # （Get-StartApps / shell:AppsFolder），os.walk 再放宽排除规则也扫不到。
        # 单独补一批虚拟应用入口，path 记成 shell:AppsFolder\<AUMID>。
        # 采集失败只影响「商店应用搜不搜得到」，绝不能让整轮重建挂掉。
        try:
            app_batch = app_entries.app_rows(py_keys)
            if app_batch:
                con.executemany('INSERT OR REPLACE INTO files_new'
                                '(path,name,dir,ext,size,mtime,descr,py,ini) '
                                'VALUES(?,?,?,?,?,?,?,?,?)', app_batch)
                con.commit()
                n += len(app_batch)
                _log_file('shell app entries: %d' % len(app_batch))
        except Exception:
            pass

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

        try:

            if metrics is not None:

                metrics.add('index', 'ok', sub='names')

                metrics.note('index', '文件名索引 %d 个（跳过 %s）' % (n, skipped))

        except Exception:

            pass

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

            # 第 294 项：网络位置不做实时监听 —— SMB 上的 ReadDirectoryChangesW
            # 经常丢事件，断线时还会把监听线程阻塞住。网络位置靠手动更新。
            if is_net_path(d):

                return

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

        # 第 236 项：与 names_targets 同一条规则 —— 内置硬排除的树不当监听根

        if str(d).strip() and not scan_docs.never_index(d):

            push(d)

    # 第 239 项：这行在改 236 的排除规则时被误删，`_watch_roots()` 直接 NameError，
    # 启动就挂（表现是「窗口启动失败 + UnboundLocalError: traceback」，真因被遮住）。
    # 单独抽个函数保住它，别再让一次文本替换把启动路径上的变量吃掉。
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

        except sqlite3.Error as e:

            # 第 327 项：长任务（VACUUM / 瘦身 / 整盘重扫）会把写锁攥住几十秒，
            # 以前这一批就直接丢（debug.log 里成片的「实时索引写入失败：database is locked」），
            # 用户刚保存的文件在实时索引里就没有了。现在塞回队列，下一轮接着写。
            if 'locked' in str(e).lower() or 'busy' in str(e).lower():

                with _WATCH['lock']:

                    for k, v in batch:

                        _WATCH['q'].setdefault(k, v)

                time.sleep(0.5)

            else:

                print('[!] 实时索引写入失败：', e, flush=True)

        except Exception as e:

            print('[!] 实时索引写入失败：', e, flush=True)





# ---------------------------------------------------------------- 第 247 项
# 竞品源码调研后补的三块（详见交接文档第 247 项）：
#   A. Recoll fsindexer.cpp:680 的「失败文件签名 + 不重试」我们其实已有等价物
#      （meta.reason + _needs() 的 size/mtime 短路），但 DocFetcher 的 ErrorTable
#      把失败清单摆到用户面前，我们没有 —— 数据在库里躺着眼看不见。
#   B. DocFetcher FolderWatcher.java:378「只读访问也会报 MODIFIED」的 mtime 二次确认，
#      以及同文件 339 行的 Office `~$` 临时文件过滤，正文路径有、监听路径没有。
#   C. DocFetcher RegexTestPanel.java:132「输入一个路径立刻回显会不会被排除」，
#      我们的排除词是子串语义，用户改完只能靠「重建索引看结果」来验证。


def _watch_same_as_indexed(st, prev):
    """DocFetcher/Baloo 同款 mtime 二次确认：库里已有且大小/时间没变 → 伪变更。

    ReadDirectoryChangesW 连只读打开都会发 MODIFIED；不拦的话每次打开文档
    都会重写 files 行、顺手把正文重索引队列塞一遍。"""
    if not prev:
        return False
    sz, mt = prev
    if sz is None or mt is None:
        return False
    try:
        return abs(st.st_size - sz) < 1 and abs(st.st_mtime - mt) < 2
    except (TypeError, ValueError):
        return False


def _watch_is_office_temp(name):
    """Office 打开文档时生成的 ~$xxx.docx 锁文件，不是用户内容。"""
    return str(name or '').startswith('~$')


# 这三类不是「解析失败」：无文本（图片/二进制本来就没正文）、空文件、临时文件。
# 不滤掉的话失败清单会被几万条正常记录淹没（实测 meta 里 41,217 条「无文本」），
# 真正超时/加密/旧格式的坏文件反而被 LIMIT 挤出去 —— 清单就失去意义了。
BENIGN_DOC_REASONS = ('无文本', '空文件', '临时文件')


def _index_failures(limit=200, con=None):
    """把 meta.reason 里的失败记录整理给用户看（DocFetcher ErrorTable 同款）。

    will_retry 对应 Recoll fsindexer.cpp:680 的 `+` 签名语义：文件本身变了
    才会在下一轮重新解析；没变就一直跳过，避免坏 PDF 每轮都超时拖住队列。
    """
    limit = max(1, min(int(limit or 200), 500))
    sql = ("SELECT path, reason, size, mtime, indexed_at FROM meta "
           "WHERE reason IS NOT NULL AND reason<>'' "
           "AND reason NOT IN (?,?,?) "
           "ORDER BY indexed_at DESC LIMIT ?")
    try:
        if con is None:
            with con_lock:
                rows = conn().execute(sql, BENIGN_DOC_REASONS + (limit,)).fetchall()
        else:
            rows = con.execute(sql, BENIGN_DOC_REASONS + (limit,)).fetchall()
    except sqlite3.Error:
        rows = []
    out = []
    for p, why, sz, mt, at in rows:
        will_retry = False
        try:
            st = os.stat(p)
            will_retry = not (sz is not None and mt is not None
                              and abs(st.st_size - sz) < 1
                              and abs(st.st_mtime - mt) < 2)
        except OSError:
            will_retry = False
        out.append({'p': p, 'why': why or '', 'at': at or 0,
                    'will_retry': will_retry})
    return {'items': out, 'total': len(out)}


def _exclude_test(path):
    """给一个路径，立刻回显「会不会被排除 + 命中哪条规则」（DocFetcher 同款）。

    不只是布尔值：用户排除词 / 内置段 / 目录名规则 / 白名单放行分开列，
    否则「我明明加了排除词怎么还在」这种问题只能靠重建索引猜。
    """
    p = str(path or '').strip().strip('"')
    if not p:
        return {'ok': False, 'err': 'empty'}
    low = os.path.normcase(p)
    hits = []
    for k in (os.path.normcase(str(s).strip())
              for s in (_scope_conf().get('exclude_dirs') or [])
              if str(s).strip()):
        if k in low:
            hits.append({'kind': 'scope', 'rule': k})
    for k in (os.path.normcase(s) for s in scan_docs.DEFAULT_SKIP_SEGS):
        if k in low and not any(h['rule'] == k for h in hits):
            hits.append({'kind': 'builtin', 'rule': k})
    m = _SKIP_NAME_RE.search(low)
    if m:
        rule = m.group(0).strip('\\/')
        if rule and not any(h['rule'] == rule for h in hits):
            hits.append({'kind': 'dirname', 'rule': rule})
    for seg in re.split(r'[\\/]+', low):
        if seg in NAMES_SKIP_DIRS and not any(h['rule'] == seg for h in hits):
            hits.append({'kind': 'dirname', 'rule': seg})
    keep = [k for k in _KEEP_SEGS_NC if k in low]
    return {'ok': True, 'p': p,
            'excluded': bool(_excluded(p, _scope_excludes())),
            'hits': hits[:8], 'kept': bool(keep), 'keep_segs': keep[:4]}


def _watch_apply(batch):

    """把一批变更落到 files / dirs。返回实际写入条数。



    用独立连接：全局 conn() 要套 con_lock，而这里可能连着写几十条，

    占着锁会卡住搜索。busy_timeout 兜住和后台任务的短暂争用。"""

    exckw = _scope_excludes()

    # 第 247 项：MODIFIED 事件先查库里旧 size/mtime，一次 IN 查询，
    # 别在循环里每条路径开一次连接（Baloo PendingFileQueue 的去重思路）。
    mods = [f for f, a in batch if a == 3]
    old = {}
    if mods:
        try:
            c = sqlite3.connect(DB, check_same_thread=False)
            c.execute('PRAGMA busy_timeout=1000')
            old = {os.path.normcase(r[0]): (r[1], r[2]) for r in c.execute(
                'SELECT path, size, mtime FROM files WHERE path IN ('
                + ','.join('?' * len(mods)) + ')', mods)}
            c.close()
        except Exception:
            old = {}

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

        # 第 247 项：Office 的 ~$ 锁文件（正文路径早有同款，监听路径补齐）。
        if _watch_is_office_temp(os.path.basename(full)):
            gone.append(full)
            continue

        # 第 247 项：只读打开也会报 MODIFIED —— 库里 size/mtime 没变就别重写。
        if action == 3 and _watch_same_as_indexed(
                st, old.get(os.path.normcase(full))):
            continue

        if os.path.isdir(full):

            base = os.path.basename(full)

            if not base:

                continue

            py, ini = py_keys(base)

            add_d.append((full, base.lower(), os.path.dirname(full), py, ini, st.st_mtime))

            continue

        ext = os.path.splitext(full)[1].lower()

        # 第 242 项：体积上限默认关闭（0 = 不限，Everything 的文件名索引同样不限）；
        # 小体积图标按新规则排除。实时监听必须与全量扫描用同一套判据，
        # 否则「重建时干净了、用两天又被监听塞回来」。
        over_size = bool(NAMES_SIZE_LIMIT and st.st_size > NAMES_SIZE_LIMIT
                         and ext not in NAMES_BIG_EXTS)
        small_icon = (ext in NAMES_ICON_EXTS
                      and st.st_size <= NAMES_ICON_MAX_SIZE)

        if ext not in NAMES_EXTS or over_size or small_icon:

            gone.append(full)                # 不该入库的类型，顺手清掉同名旧记录

            continue

        # 第 236 项：安装目录下只留应用类，资源文件不进库（与全量扫描同一套判据，
        # 否则「重建后干净了、用两天又被实时监听塞回来」）

        if ((scan_docs.is_install_root(os.path.dirname(full))

                 and ext not in scan_docs.APP_ONLY_EXTS)

                or _soft_dir_file(full, ext)

                or _media_noise_file(full, ext)

                or _noindex_marked(full)):
            gone.append(full)

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

            # 第 236 项：与全量正文索引同一套排除，否则「重建时干净了、用两天又长回来」

            if scan_docs.is_install_root(k) or any(s in k for s in _BODY_SKIP_SEGS_NC):

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

                cur_rid = doc_insert(con, p, Path(p).suffix.lower(), text)

                rids[os.path.normcase(p)] = cur_rid

                _bigram_add(con, cur_rid, text)

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

        # 第 380 项：引擎升到 RapidOCR 3.x（PP-OCRv6 small，离线内置），
        # 旧 API 契约由 ocr_engine 适配，调用方不用动
        from ocr_engine import RapidOCRCompat

        _ocr = RapidOCRCompat()

    return _ocr



def ocr_pdf(path: Path, budget=OCR_BUDGET, pages=OCR_PAGES, on_page=None):

    """纯扫描版 PDF：逐页 OCR；失败返回 None。



    budget=单篇 OCR 的总时间预算（秒）。扫描版笔记/教材一本几十页，逐页 OCR 能吃掉

    好几分钟，一篇就能拖住整轮索引；给个预算，到点就把已识别的部分交出来——

    建搜索特征用不着整本。

    pages=None 表示**不限页数**（Pro 批量 OCR 队列用），默认仍是前 OCR_PAGES 页，
    免费索引的行为一个字都不变。on_page(i, total) 每页回调一次报进度。"""

    try:

        import pdf_engine as pymupdf

        doc = pymupdf.open(str(path))

        try:

            if doc.needs_pass and not doc.authenticate(''):

                return None

            ocr = _get_ocr()

            parts = []

            t0 = time.time()

            try:

                n_pages = doc.page_count

            except Exception:

                n_pages = 0

            for i, page in enumerate(doc):

                if (pages is not None and i >= pages) or (budget is not None

                                                          and time.time() - t0 > budget):

                    break

                if on_page:

                    try:

                        on_page(i + 1, n_pages or i + 1)

                    except Exception:

                        pass

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

    """对外统一入口：先按格式提取，再净化一次（见 _clean_text）。

    第 326 项：纯数据文件（密码字典 / 日志 / 导出表）超过 8MB 直接不读正文 ——
    它们不是文档，全文进库只会把库撑大，文件名照样可搜。
    """

    try:

        if path.suffix.lower() in TEXT_DUMP_EXTS and path.stat().st_size > TEXT_DUMP_MAX:

            return None

    except OSError:

        pass

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

        return _decode_text(raw)[:MAX_BODY]   # 第 326 项：以前没截，txt 会原样存 6MB

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



def doc_mode(con):
    """这份库是「老结构」还是「压缩结构」。

    v1：docs 带 content 表（docs_content 存明文正文），snippet() 可用；
    v2：docs contentless + doc_rec 存 zstd 正文（体积评估 #2/#3）。
    两种都必须在运行期支持 —— 用户库只有迁移过才变 v2。
    """
    try:
        hit = con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='doc_rec' LIMIT 1").fetchone()
        if not hit:
            return 'v1'
        # 迁移中可能两张表并存：docs_content 还在就仍按老结构读
        old = con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='docs_content' LIMIT 1").fetchone()
        return 'v1' if old else 'v2'
    except Exception:
        return 'v1'


_ZSTD = threading.local()


def _zc():
    """每线程一套 zstd 上下文（第 378 项，闪退根因）。

    以前这里是模块级 dict 缓存 **共享** 的 ZstdCompressor / ZstdDecompressor。
    python-zstandard 的对象不是线程安全的：同一个对象不能被多个线程同时
    compress / decompress —— 内部会复用同一块 C 侧缓冲和上下文，并发进同一个对象
    等于边读边写，出来的是「Data corruption detected / Unknown frame descriptor」，
    再往后就是 backend_c.cp312-win_amd64.pyd 里 0xc0000005 直接把进程干掉。

    而我们有 HTTP 工作线程（搜索、片段、详情预览）、索引线程、bigram 回填线程、
    对账线程同时在跑，全都会走 _z_pack / _z_unpack —— 2026-11-21 18:09 实测
    连续两次开窗即闪退（偏移 0x5b222 / 0x50aa0 两次还不一样，典型堆被写坏）。

    隔离进程里复现过（tmp\\_zstd_race.py）：
      共享 8 线程 → 6/8 线程报 Data corruption；
      每线程一套 → 0 报错。
    所以改成 threading.local：每个线程第一次用时自建，互不共享。
    """
    c = getattr(_ZSTD, 'c', None)
    if c is None:
        import zstandard
        c = zstandard.ZstdCompressor(level=3)
        _ZSTD.c = c
        _ZSTD.d = zstandard.ZstdDecompressor()
    return c


def _zd():
    d = getattr(_ZSTD, 'd', None)
    if d is None:
        _zc()
        d = _ZSTD.d
    return d


def _z_pack(text):
    return _zc().compress(str(text or '').encode('utf-8', 'replace'))


def _z_unpack(blob, n=None):
    if not blob:
        return ''
    try:
        if n is not None and len(blob) == n:      # 迁移期可能还是明文
            return blob.decode('utf-8', 'replace') if isinstance(blob, bytes) else str(blob)
        return _zd().decompress(bytes(blob)).decode('utf-8', 'replace')
    except Exception:
        # 不是 zstd（老明文 / 坏块）：当文本用，宁可搜不到也不要炸搜索
        try:
            return bytes(blob).decode('utf-8', 'replace')
        except Exception:
            return ''


def doc_body_get(con, rid, path=None):
    """按 rowid（或退回按路径）取回正文明文。v1 直接读 docs，v2 解压 doc_rec。"""
    if doc_mode(con) == 'v1':
        try:
            sql = ('SELECT body FROM docs WHERE rowid=?' if rid is not None
                   else 'SELECT body FROM docs WHERE path=? LIMIT 1')
            row = con.execute(sql, (rid if rid is not None else path,)).fetchone()
            return str((row or [''])[0] or '')
        except Exception:
            return ''
    try:
        sql = ('SELECT z, n FROM doc_rec WHERE id=?' if rid is not None
               else 'SELECT z, n FROM doc_rec WHERE path=? LIMIT 1')
        row = con.execute(sql, (rid if rid is not None else path,)).fetchone()
        return _z_unpack(*(row or (None, None)))
    except Exception:
        return ''


def doc_insert(con, path, ext, text):
    """写一篇正文，返回 rowid。v1 走 FTS 自带的行，v2 先定 id 再灌两张表。"""
    if doc_mode(con) == 'v1':
        cur = con.execute('INSERT INTO docs VALUES(?,?,?)', (path, ext, text))
        return cur.lastrowid
    blob = _z_pack(text)
    n = len(text or '')
    # 先拿 id：doc_rec 的 AUTOINCREMENT 是唯一 id 源，docs.rowid 必须与它一致
    rid = con.execute(
        'INSERT INTO doc_rec(path, ext, z, n) VALUES(?,?,?,?)',
        (path, ext, blob, n)).lastrowid
    con.execute('INSERT INTO docs(rowid, path, ext, body) VALUES(?,?,?,?)',
                (rid, path, ext, text))
    return rid


def doc_del(con, rid):
    """删一行正文（两张表 + bigram 一起清）。"""
    con.execute('DELETE FROM docs WHERE rowid=?', (rid,))
    con.execute('DELETE FROM bigram WHERE rid=?', (rid,))
    if doc_mode(con) == 'v2':
        con.execute('DELETE FROM doc_rec WHERE id=?', (rid,))


def doc_all_rows(con, limit=None, after=0):
    """(rid, path) 列表，替代老代码里的 SELECT rowid, path FROM docs。"""
    if doc_mode(con) == 'v2':
        sql = 'SELECT id, path FROM doc_rec WHERE id > ? ORDER BY id'
        args = [after]
        if limit:
            sql += ' LIMIT ?'
            args.append(int(limit))
        return con.execute(sql, args).fetchall()
    sql = 'SELECT rowid, path FROM docs'
    if after:
        sql += ' WHERE rowid > ? ORDER BY rowid'
        args = [after]
        if limit:
            sql += ' LIMIT ?'
            args.append(int(limit))
        return con.execute(sql, args).fetchall()
    if limit:
        sql += ' LIMIT ?'
        args.append(int(limit))
    return con.execute(sql, args).fetchall()


def doc_replace_body(con, rid, text, n=None, path=None, ext=None):
    """原地改正文（截断超长正文用）。v2 的 FTS 是 contentless，只能删了重灌同行号。"""
    if doc_mode(con) == 'v1':
        con.execute('UPDATE docs SET body=? WHERE rowid=?', (text, rid))
        return rid
    if path is None or ext is None:
        row = con.execute('SELECT path, ext FROM doc_rec WHERE id=?', (rid,)).fetchone() or (None, None)
        path, ext = row
    con.execute('DELETE FROM docs WHERE rowid=?', (rid,))
    con.execute('DELETE FROM bigram WHERE rid=?', (rid,))
    con.execute('UPDATE doc_rec SET z=?, n=? WHERE id=?', (_z_pack(text), len(text), rid))
    con.execute('INSERT INTO docs(rowid, path, ext, body) VALUES(?,?,?,?)',
                (rid, path, ext, text))
    _bigram_add(con, rid, text)
    return rid


def compact_legacy_db(con, log=None, prog=None):
    """把老结构库就地迁到压缩结构（体积评估 #2/#3）。

    只在「库里还有 docs_content」时动手；迁移前用 SQLite 备份 API 存一份
    brain.db.pre-compact-<时间戳>（WAL 下照抄文件不安全），迁完再 VACUUM。
    返回 True 表示这次真的迁了，False 表示无事可做 / 失败（失败保持老结构）。

    第 366 项：`prog(**kw)` 用来往界面上报进度（phase/done/total），
    手动「整理索引库」和后台空闲迁移共用同一条进度线，用户不会以为程序卡死。
    """
    log = log or (lambda *a: None)
    prog = prog or (lambda **kw: None)
    if doc_mode(con) != 'v1':
        return False
    try:
        n = con.execute('SELECT COUNT(*) FROM docs_content').fetchone()[0]
    except sqlite3.Error:
        return False
    if not n:
        return False

    # 1) 备份（backup API 会带上 WAL 里还没合并的页）
    prog(phase='backup', done=0, total=0, k='maint_compact_backup',
         a={'mb': '%.0f' % (os.path.getsize(DB) / 1048576.0)})
    try:
        import sqlite3 as _sq
        ts = time.strftime('%Y%m%d-%H%M%S')
        bak = Path(str(DB) + '.pre-compact-' + ts)
        src = con
        dst = _sq.connect(str(bak))
        try:
            src.backup(dst)
        finally:
            dst.close()
        log('已备份老库到 %s' % bak)
    except Exception as e:
        log('备份失败，放弃迁移: %r' % (e,))
        return False

    t0 = time.time()
    zc, zd = _zc(), _zd()
    try:
        # 2) 正文压进 doc_rec（id 与 docs.rowid 对齐）
        con.execute('CREATE TABLE IF NOT EXISTS doc_rec(id INTEGER PRIMARY KEY, '
                    'path TEXT, ext TEXT, z BLOB, n INTEGER)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_doc_rec_path ON doc_rec(path)')
        k = 0
        for rid, p, e, body in con.execute(
                'SELECT id, c0, c1, c2 FROM docs_content ORDER BY id'):
            body = body or ''
            con.execute('INSERT OR REPLACE INTO doc_rec(id, path, ext, z, n) '
                        'VALUES (?,?,?,?,?)',
                        (rid, p or '', e or '', zc.compress(body.encode('utf-8', 'replace')),
                         len(body)))
            k += 1
            if k % 2000 == 0:
                con.commit()
                _ACTIVITY['t'] = time.time()      # 别让别的维护任务以为闲置了
                prog(phase='compress', done=k, total=n, k='maint_compact_compress')
        con.commit()
        log('正文压缩完成 %d 篇 %.1fs' % (k, time.time() - t0))
        prog(phase='compress', done=k, total=k, k='maint_compact_compress')

        # 3) 重建 contentless + detail=full 的 docs（位置信息保留，短语/英文照常可搜）
        prog(phase='rebuild', done=0, total=k, k='maint_compact_rebuild')
        con.execute('DROP TABLE IF EXISTS docs_x')
        con.execute("""CREATE VIRTUAL TABLE docs_x USING fts5(
                       path UNINDEXED, ext UNINDEXED, body, tokenize='trigram',
                       detail=full, content='', contentless_delete=1)""")
        con.commit()
        for i, (rid, blob) in enumerate(con.execute(
                'SELECT id, z FROM doc_rec ORDER BY id').fetchall()):
            con.execute('INSERT INTO docs_x(rowid, path, ext, body) VALUES (?,?,?,?)',
                        (rid, '', '', zd.decompress(blob).decode('utf-8', 'replace')))
            if i % 2000 == 0:
                con.commit()
                _ACTIVITY['t'] = time.time()
                prog(phase='rebuild', done=i, total=k, k='maint_compact_rebuild')
        con.commit()
        log('全文索引重建完成 %.1fs' % (time.time() - t0))

        # 4) 换表（docs_content 一删，doc_mode 立刻翻成 v2）
        con.execute('DROP TABLE docs')
        for t in ('docs_data', 'docs_idx', 'docs_docsize', 'docs_config', 'docs_content'):
            con.execute('DROP TABLE IF EXISTS %s' % t)
        con.execute('ALTER TABLE docs_x RENAME TO docs')
        con.commit()

        # 5) 回收空间
        prog(phase='vacuum', done=0, total=0, k='maint_compact_vacuum')
        con.execute('VACUUM')
        _kv_set(con, 'compact_v2', '1')
        con.commit()
        log('迁移完成，全库 %.1fs' % (time.time() - t0))
        return True
    except Exception as e:
        log('迁移中断（保持老结构可用）: %r' % (e,))
        try:
            con.rollback()
        except Exception:
            pass
        # 换表的瞬间出错最坏：docs 已删、docs_x 还在 —— 这里补一次改名，别让库变砖
        try:
            has_docs = con.execute("SELECT 1 FROM sqlite_master WHERE name='docs' LIMIT 1").fetchone()
            has_new = con.execute("SELECT 1 FROM sqlite_master WHERE name='docs_x' LIMIT 1").fetchone()
            if (not has_docs) and has_new:
                con.execute('ALTER TABLE docs_x RENAME TO docs')
                con.commit()
                log('已把 docs_x 改名回 docs（老结构继续用）')
        except Exception:
            pass
        return False


def _doc_rids(con):

    """path → rowid 映射。



    docs 是 FTS5 虚表，path 又是 UNINDEXED 列，所以任何 `WHERE path=?` 的删除/更新

    都会退化成整表扫描：实测 3.4GB 的库上删一行要 1.4 秒，而按 rowid 删是 1 毫秒。

    重索引上千篇文件就白扔二十多分钟。先花一次（约 1.5 秒）把映射取到内存，

    之后全按 rowid 操作。只读 path 列，不用把正文也读出来。"""

    if doc_mode(con) == 'v2':
        # v2：路径在 doc_rec 里，docs 是 contentless（连 path 列都不存）
        return {p: r for r, p in con.execute('SELECT id, path FROM doc_rec')}

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

        if doc_mode(con) == 'v2':
            con.execute('DELETE FROM doc_rec WHERE id=?', (rid,))

    else:

        if doc_mode(con) == 'v2':
            rs = [r[0] for r in con.execute(
                'SELECT id FROM doc_rec WHERE lower(path)=lower(?)', (path,))]
            if not rs:
                return
            con.execute('DELETE FROM bigram WHERE rid IN (%s)'
                        % ','.join('?' * len(rs)), rs)
            con.execute('DELETE FROM doc_rec WHERE lower(path)=lower(?)', (path,))
            con.execute('DELETE FROM docs WHERE rowid IN (%s)'
                        % ','.join('?' * len(rs)), rs)
            return

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

    # 第 327 项：踩过的坑，必须写在这儿 ——
    # Python sqlite3 默认 isolation_level=''，一条 DML 就会隐式 BEGIN；忘 commit 的话
    # 这条连接（尤其是全局共享的 _con）会一直攥着写锁。表现是「读都正常，所有写库全是
    # database is locked」：实时监听丢事件、bigram 回填失败、gone reconcile 删不掉。
    # 实测 2026-11-19：`_kv_set(con, _SLIM_KV, '1')` 少了 commit，整个程序写库瘫了。
    # 所以凡是走共享连接的一律就地提交；回填线程传的是自己的连接，不受影响。
    try:

        if _con is not None and con is _con:

            con.commit()

    except Exception:

        pass





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

        total = con.execute('SELECT COUNT(*) FROM %s'
                            % ('doc_rec' if doc_mode(con) == 'v2' else 'docs')).fetchone()[0]

        if not total:

            _kv_set(con, 'bigram_done', '1')     # 空库：新正文都会经 _bigram_add 进来

            con.commit()

            return

        t0 = time.time()

        while True:

            if _job_busy():

                time.sleep(3)

                continue

            if doc_mode(con) == 'v2':
                rows = [(r, doc_body_get(con, r)) for (r,) in con.execute(
                    'SELECT id FROM doc_rec WHERE id > ? ORDER BY id LIMIT 200', (last,))]
            else:
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

    # 第 326 项：WAL 下 synchronous=FULL 每一笔都 fsync（索引器写 6.8 万条时这笔开销很实在）。
    # NORMAL 在 WAL 里仍然不会写坏库，最坏情况只丢「最后一笔已提交事务」——
    # 对「可重建的搜索索引」这个代价可以接受，换来的是写入快一档。
    con.execute('PRAGMA synchronous=NORMAL')

    con.execute('PRAGMA wal_autocheckpoint=2000')  # 长事务下 1000 页（4MB）太密

    # 体积评估 #1：autocheckpoint 只「合并」已提交的帧，**不会缩小 WAL 文件本身**；
    # 不设 journal_size_limit 的话，文件会一直停在历史最高水位（实测 60.9 MB）。
    # 8 MB 和上面的 2000 页（≈8 MB）对齐：收完就截到 8 MB 以内，文档承诺的
    # 「超过 8 MB 自动收」才算真的成立。
    con.execute('PRAGMA journal_size_limit=8388608')

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

                   path UNINDEXED, ext UNINDEXED, body, tokenize='trigram',

                   content='', contentless_delete=1)""")

    # 体积评估 #3：正文只在这里存一份，zstd 压缩（原文 245 MB → 实测 77 MB）。
    # docs 变成 contentless 之后不再有正文副本，路径/扩展名也一并从这里取。
    # id 必须和 docs.rowid 严格一致 —— 全文命中后按 id 解压出正文做片段。
    # **老库不建这张表**：有 docs_content 就说明还没迁移，继续走老路径（见 doc_mode）。
    _legacy = con.execute(
        "SELECT 1 FROM sqlite_master WHERE name='docs_content' LIMIT 1").fetchone()
    if not _legacy:
        con.execute("""CREATE TABLE IF NOT EXISTS doc_rec(
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       path TEXT, ext TEXT, z BLOB, n INTEGER)""")
        con.execute('CREATE INDEX IF NOT EXISTS idx_doc_rec_path ON doc_rec(path)')

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

    # 第 236 项：bigram 的主键是 (gram, rid)，按 rid 删除只能全表扫。
    # 49 万行时无所谓，490 万行时「删 1 万篇正文」变成 490 亿行扫描 ——
    # 实测一次对账跑 12 分钟没结束，prune 在大库上等于不可用。
    # 补一条 rid 索引：正文查询用 PK(gram, rid) 前缀，删除走 rid，两条路都 O(log n)。

    try:

        con.execute('CREATE INDEX IF NOT EXISTS idx_bigram_rid ON bigram(rid)')

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

    try:
        import clipboard_mgr
        clipboard_mgr.init_db(con)
    except Exception:
        pass

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

# 第 281 项：全库校验从体检里拆出来。整库 quick_check 在 2.4GB 库上 28–39s（冷启 2 分钟），
# 塞进体检按钮就是「按下去半分钟没反应」。这里做成后台单飞任务：立刻返回，状态可查。
_VERIFY_RUN = {'running': False, 'started': 0.0, 'res': {}}
_VERIFY_LOCK = threading.Lock()





def scope_report(con):

    """设置抽屉「索引范围」用：盘符分布 + 占用最多的目录 + 排除词 + 扫描规则。



    60 万行 GROUP BY dir 约 250ms，而用户每开一次设置都要这个数 —— 缓存 30 秒，

    并且用 scope.json 的 mtime 做键（改了范围立刻失效，不会被旧缓存骗）。

    调用方必须已持有 con_lock。"""

    sp = SCOPE_FILE

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

                      'size_limit': NAMES_SIZE_LIMIT,

                      # 第 243 项：短音频阈值 + 音乐平台目录数 + 已知软件目录数。
                      # 前端要显示「索引规则」说明，让用户知道哪些东西被排掉了、
                      # 以及为什么 —— 这比事后解释「为什么搜不到」省事得多。
                      'audio_min_seconds': NAMES_MIN_AUDIO_SECONDS,

                      'music_apps': len(scan_docs.MUSIC_APP_DIR_SEGS),

                      'software_dirs': len(scan_docs.SOFTWARE_DIR_NAMES)}}

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

            import pdf_engine as pymupdf

        except Exception:

            pymupdf = None

        if pymupdf is None:

            # pymupdf/pdf_engine 都拿不到时退回 pypdf：它同样能分辨「加密」和「损坏」，

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

        _pth_tbl = 'doc_rec' if doc_mode(con) == 'v2' else 'docs'
        fixed = con.execute('DELETE FROM meta WHERE reason IS NULL AND path NOT IN '
                            '(SELECT path FROM %s)' % _pth_tbl).rowcount

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

        # 第 236 项：程序安装目录 / 前端资源包里的 readme、license、界面文案不做正文。
        # 这些文件仍能被「文件名」搜到，只是不进正文池（Recoll noContentSuffixes 同款思路）。

        if scan_docs.is_install_root(pl) or any(s in pl for s in _BODY_SKIP_SEGS_NC):

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

            new_rid = doc_insert(con, p, Path(p).suffix.lower(), text)

            rids[os.path.normcase(p)] = new_rid

            _bigram_add(con, new_rid, text)   # 2 字中文查询的旁表倒排，成对维护

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

    # 第 277 项：正文索引结果。n_fail 才算失败；n_skip（范围外/重复）单独计数，
    # 否则「跳过 5 万」会把失败率算成 90%，看数据的人会以为索引坏了。
    try:

        if metrics is not None:

            metrics.add('index', 'empty' if n_fail else 'ok', sub='content')

            if n_skip:

                metrics.count('index', 'skipped')

            if n_fail:

                metrics.count('index', 'failed')

            metrics.note('index', '正文索引：新 %d / 空失败 %d / 跳过 %d / 库内 %d'

                         % (n_new, n_fail, n_skip, total))

    except Exception:

        pass

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

    if con is not None and doc_mode(con) == 'v2':
        # v2 的正文不在 FTS 里（contentless + doc_rec 压缩），真正的检索在
        # _search_v2() 里做。这里只保留等价的「路由描述」，供测试与调用方判断走哪条路。
        toks0 = _fts_tokens(q)
        if not _need_like(q):
            return ('SELECT rowid FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?',
                    [_fts_query(q)])
        if (toks0 and all(len(t) == 2 and _is_cjk_word(t) for t in toks0)
                and _bigram_ready(con)):
            return ('SELECT r.path FROM doc_rec r WHERE r.id IN '
                    '(SELECT rid FROM bigram WHERE gram=?) LIMIT ?', [toks0[0]])
        return ('SELECT path FROM doc_rec /* LIKE */ WHERE 1=1 LIMIT ?', [])

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



def _v2_snip(con, rid, toks, width=45):
    """v2 的片段：FTS 只给 rowid，正文要自己解压，再按第一个命中词取窗口。"""
    body = doc_body_get(con, rid)
    if not body:
        return ''
    low = body.lower()
    pos = -1
    for t in toks:
        p = low.find(str(t).lower())
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        frag = re.sub(r'\s+', ' ', body[:width * 2]).strip()
        return _mark_text(frag + ('…' if len(body) > width * 2 else ''), toks)
    start = max(0, pos - width)
    end = min(len(body), pos + width + 40)
    frag = re.sub(r'\s+', ' ', body[start:end]).strip()
    if start > 0:
        frag = '…' + frag
    if end < len(body):
        frag += '…'
    return _mark_text(frag, toks)


def _v2_ok(path, toks, ext=None, pf=None):
    """v2 的过滤都在 Python 侧：FTS 里既没有 path 也没有 ext。"""
    low = str(path or '').lower()
    e = str(ext or '').strip().lower().lstrip('.')
    if e:
        parts = [x.strip().lstrip('.') for x in e.split(',') if x.strip()]
        if parts and not any(low.endswith('.' + x) for x in parts):
            return False
    if pf and str(pf).lower() not in low:
        return False
    body_toks = [t for t in toks]
    return bool(body_toks)


def _v2_scan_rows(con, toks, limit):
    """短词（1 字 / AI / C 这类）在 v2 下只能解压后扫：9.6k 篇 ≈ 0.5s，够用。"""
    out = []
    for rid, path in con.execute('SELECT id, path FROM doc_rec ORDER BY id'):
        body = doc_body_get(con, rid)
        low = body.lower()
        if all(str(t).lower() in low for t in toks):
            out.append((rid, path))
            if len(out) >= limit * 2:
                break
    return out


def _search_v2(con, q, limit, ext=None, path_filter=None):
    """v2 检索：MATCH 只拿 rowid，正文、片段、过滤全部回到 Python 做。"""
    toks = [t.strip('"\u201c\u201d\u2018\u2019') for t in _fts_tokens(q)]
    toks = [t for t in toks if t]
    if not toks:
        return []
    want = max(int(limit or 20), 1)

    def build(cands):
        hits = []
        for rid, path in cands:
            if not _v2_ok(path, toks, ext, path_filter):
                continue
            body = doc_body_get(con, rid)
            low = body.lower()
            # trigram 索引只保证「三元组出现」，不保证词真的完整出现 —— 必须回扫确认
            if not all(str(t).lower() in low for t in toks):
                continue
            hits.append((path, _v2_snip(con, rid, toks)))
            if len(hits) >= want:
                break
        return hits

    if not _need_like(q):
        try:
            rids = [r for (r,) in con.execute(
                'SELECT rowid FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?',
                (_fts_query(q), want * 6))]
        except sqlite3.Error:
            rids = []
        if rids:
            paths = dict(con.execute(
                'SELECT id, path FROM doc_rec WHERE id IN (%s)'
                % ','.join('?' * len(rids)), rids).fetchall() or [])
            cands = [(r, paths[r]) for r in rids if r in paths]
            hits = build(cands)
            if hits:
                return hits

    if (all(len(t) == 2 and _is_cjk_word(t) for t in toks) and _bigram_ready(con)):
        # 每个 2 字词一张 gram 表，多词取交集（不能写成 gram=? AND gram=?，同一行不可能两值）
        sets = [set(r for (r,) in con.execute('SELECT rid FROM bigram WHERE gram=?', (t,)))
                for t in toks]
        rids = sorted(set.intersection(*sets))[:want * 6]
        if rids:
            rows = con.execute('SELECT id, path FROM doc_rec WHERE id IN (%s)'
                               % ','.join('?' * len(rids)), rids).fetchall()
            hits = build(rows)
            if hits:
                return hits

    return build(_v2_scan_rows(con, toks, want))


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

MINI_CACHE_MAX = 120               # 第 326 项：400 → 120。每条缓存是「结果列表 + 每项字典」，
                                   # 400 条能攒出几十 MB；用户真正回看的只有最近几十个词。

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

    """path(小写) -> 最近得分，范围 (0, 1]。整表读进内存：几十到几百行，比逐行查库便宜得多。"""

    try:

        rows = con.execute('SELECT path, hits, last FROM recent '

                           'ORDER BY last DESC LIMIT 800').fetchall()

    except sqlite3.Error:

        return {}

    n = max(1, len(rows))

    out = {}

    for i, (path, hits, _last) in enumerate(rows):

        # 得分必须封顶 1.0：调用方按 0.30 * 分 加成，注释承诺「最多 0.30」。

        # 老写法 hits - i*0.05 在 recent 表涨到几百行后，尾部行会变成大负数

        # （3 - 412*0.05 = -17.6），于是「开过但不算最近」的应用被压到最后

        # —— 2026-11-15 搜 ayu 时 AyuGram.lnk 掉到第 7 位就是这么来的。

        # fresh：第 0 名 1.0，最后一名趋近 0（绝不取负）。

        fresh = 1.0 - (i / float(n))

        # freq：开 1 次 = 0.2，5 次以上封顶 1.0。

        freq = min(1.0, (hits or 1) / 5.0)

        out[os.path.normcase(path)] = 0.5 * fresh + 0.5 * freq

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

    # 第 237 项：原来这里带 '.ts'，于是时间轴筛「视频」会把 TypeScript 源码当成影片。
    # MPEG-TS 影视少见、.ts 代码文件常见，两害取其轻：不放 .ts。
    'video': ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'),

}





def _kind_exts(kind):

    """把左侧栏的类型名换成扩展名元组；认不出或 'all' 返回 None（不过滤）。"""

    k = (kind or '').strip().lower()

    if not k or k == 'all':

        return None

    return _KIND_EXTS.get(k)




# 第 324 项：左栏「文档」拆子分类（PDF / Word / Excel / PowerPoint / Markdown / 文本）。
# 为什么只拆 doc：图片、视频、压缩包对用户来说看一眼缩略图就分得清，文档不打开
# 根本不知道是 PDF 还是 Word —— 竞品 gety 也是这么分的。子类型表必须和前端
# SUBTYPES 同源（前端只传子类型名，扩展名映射留在后端一处维护）。
DOC_SUBTYPES = (
    ('pdf', ('.pdf',)),
    ('word', ('.doc', '.docx', '.wps')),
    ('excel', ('.xls', '.xlsx', '.csv', '.et')),
    ('ppt', ('.ppt', '.pptx', '.dps')),
    ('md', ('.md', '.markdown')),
    ('txt', ('.txt', '.rtf')),
)




def _ext_kind_map():
    """扩展名 → 大类（'doc'/'img'…）。同一扩展名只归一类，先到先得。"""

    m = {}

    for _k, _exts in _KIND_EXTS.items():

        for _e in _exts:

            m.setdefault(_e, _k)

    return m




def _ext_sub_map():
    """扩展名 → 文档子类型（'pdf'/'word'/'excel'/'ppt'/'md'/'txt'）。"""

    m = {}

    for _k, _exts in DOC_SUBTYPES:

        for _e in _exts:

            m.setdefault(_e, _k)

    return m




_FROOTS = [None, None]      # [roots 签名, [(原样根, normcase 根)]]
_FBUCKET_MEMO = {}          # normcase(dir) -> (显示名, 路径)；换了索引范围就整表清空


def _roots_norm(roots):
    """索引范围根目录只算一次（调用方给的是 (原样, normcase) 二元组，也兼容纯路径）。

    第 328 项：`_folder_bucket` 每条目录都要跟自己比一遍 roots，而它原来在循环里
    对每个根**每次**都重算 `normpath` + 两次 `normcase`（上千个目录 × 几个根 = 秒级 CPU）。
    """
    pairs = []
    for item in (roots or []):
        if isinstance(item, (tuple, list)) and len(item) == 2:
            pairs.append((str(item[0]), str(item[1])))
        else:
            op = os.path.normpath(str(item))
            pairs.append((op, os.path.normcase(op)))
    sig = tuple(pairs)
    if _FROOTS[0] == sig and _FROOTS[1] is not None:
        return _FROOTS[1]
    _FROOTS[0], _FROOTS[1] = sig, pairs
    _FBUCKET_MEMO.clear()
    return pairs


def _folder_bucket(d, roots):
    """把一个目录归到「左栏文件夹」里的那一桶，返回 (显示名, 路径)。

    口径：索引范围（include_dirs）之下的**第一层子目录** —— 用户心里的文件夹是
    「钢铁小项目」「2025年度报告」这种，不是 D:\\ 或整个用户目录（那些条数上千、
    点进去还是同一片汪洋）。范围之外（比如挂载盘、OneDrive）退化成盘符 + 第一层。
    """
    d = str(d or '')

    if not d:

        return ('', '')

    p = os.path.normpath(d)

    nc_key = os.path.normcase(p)

    try:

        hit = _FBUCKET_MEMO.get(nc_key)

    except Exception:

        hit = None

    if hit is not None:

        return hit

    best = None                       # (normcase 根, 原始根)

    for op, norm in _roots_norm(roots):

        if nc_key == norm or nc_key.startswith(norm + os.sep):

            if best is None or len(norm) > len(best[0]):

                best = (norm, op)

    if best:

        op = best[1]

        rel = p[len(op):].lstrip(os.sep) if len(p) >= len(op) else ''

        seg = rel.split(os.sep)[0] if rel else ''

        if seg:

            out = (seg, os.path.join(op, seg))

        else:

            out = (os.path.basename(op) or op, op)

        _fbucket_memo_put(nc_key, out)

        return out

    parts = [x for x in p.split(os.sep) if x]

    if len(parts) >= 2:

        out = (parts[1], os.sep.join(parts[:2]) + os.sep)

    else:

        out = (p, p)

    _fbucket_memo_put(nc_key, out)

    return out


def _fbucket_memo_put(key, val):
    """目录归桶结果缓存；满了就整表清掉（不留内存黑洞）。"""
    try:

        if len(_FBUCKET_MEMO) > 50000:

            _FBUCKET_MEMO.clear()

        _FBUCKET_MEMO[key] = val

    except Exception:

        pass




# 第 326 项：facets 结果缓存（键 = 关键词元组 + 扩展名元组）
_FACETS_CACHE = {}

_FACETS_TTL = 30.0


def facets(con, syn=None, folder_limit=6, max_folders=400):

    """左栏计数：当前关键词下每个大类 / 文档子类型 / 顶层文件夹各有多少条。

    只数**文件名命中**。正文命中要全表扫且有 3 秒预算，让计数跟着它一起等，
    只会把「边打字边看结果」这件事拖垮 —— 宁可计数偏小，也不让搜索变慢。
    """
    syn = syn or {}

    toks = [t.lower() for t in _fts_tokens(syn.get('q') or '')]

    where, args = [], []

    if toks:

        where.append('(' + ' AND '.join('instr(lower(name),?)>0' for _ in toks) + ')')

        args += toks

    want = []

    if syn.get('ext'):

        want = ['.' + e.strip().lstrip('.').lower() for e in syn['ext'].split(',') if e.strip()]

        if want:

            where.append('ext IN (' + ','.join('?' * len(want)) + ')')

            args += want

    w = (' WHERE ' + ' AND '.join(where)) if where else ''

    out = {'ok': True, 'total': 0, 'kinds': [], 'subs': [], 'folders': []}

    # 第 326 项：30 秒缓存。空关键词那一路是一次 6.8 万行全表扫 + 8762 组 GROUP BY，
    # 冷缓存实测 2.2 秒（表页散在 2.2GB 文件里，每次都是随机读）。
    # 用户每打一个字都会请求一次 facets，没有缓存就是「字打完了数字才跟上」。
    ck = (tuple(toks), tuple(want))

    now = time.time()

    hit = _FACETS_CACHE.get(ck)

    if hit and now - hit[0] < _FACETS_TTL:

        return hit[1]

    try:

        # 一次扫描同时出「扩展名条数」和「目录条数」——
        # 以前分两条 SQL 各扫一遍全表，纯属扫两次同一份数据。
        rows = con.execute('SELECT lower(ext), dir, COUNT(*) FROM files' + w + ' GROUP BY 1, 2',

                           args).fetchall()

    except sqlite3.Error:

        return {'ok': False, 'total': 0, 'kinds': [], 'subs': [], 'folders': []}

    ext_n, dir_n = {}, {}

    for e, d, n in rows:

        n = int(n or 0)

        e = str(e or '').lower()

        ext_n[e] = ext_n.get(e, 0) + n

        dir_n[d] = dir_n.get(d, 0) + n

    rows = list(ext_n.items())

    drows = list(dir_n.items())

    em, sm = _ext_kind_map(), _ext_sub_map()

    kc, sc = {}, {}

    total = 0

    for e, n in rows:

        e = str(e or '').lower()

        n = int(n or 0)

        total += n

        k = em.get(e)

        if k:

            kc[k] = kc.get(k, 0) + n

        if k == 'doc':

            s = sm.get(e)

            if s:

                sc[s] = sc.get(s, 0) + n

    try:

        roots = _scope_dirs()

    except Exception:

        roots = []

    fc, fname, fpath = {}, {}, {}

    for d, n in drows:

        name, path = _folder_bucket(d, roots)

        if not path:

            continue

        key = os.path.normcase(path)

        fc[key] = fc.get(key, 0) + int(n or 0)

        fname[key] = name

        fpath[key] = path

    top = sorted(fc.items(), key=lambda kv: -kv[1])[:max(folder_limit, 1)]

    # 第 327 项：文件夹也要有计数。以前 kinds 只数 files 表，左栏「文件夹」那一行
    # 永远没有数字（用户看到就是 0），而「全部」其实也不含文件夹 ——
    # 点「全部」和点「文件夹」看到的东西对不上，数字更对不上。
    dn = 0

    if not want:

        try:

            if toks:

                dsql = ('SELECT COUNT(*) FROM dirs WHERE '

                        + ' AND '.join('instr(lower(name),?)>0' for _ in toks))

                dn = int(con.execute(dsql, toks).fetchone()[0] or 0)

            else:

                dn = int(con.execute('SELECT COUNT(*) FROM dirs').fetchone()[0] or 0)

        except sqlite3.Error:

            dn = 0

    total += dn

    if dn:

        kc['dir'] = dn

    out['total'] = total

    out['kinds'] = [{'k': k, 'n': kc[k]} for k in ('dir', 'doc', 'img', 'video', 'audio', 'zip', 'app')

                    if kc.get(k)]

    out['subs'] = [{'k': k, 'n': sc[k]} for k, _ in DOC_SUBTYPES if sc.get(k)]

    out['folders'] = [{'name': fname[k], 'p': fpath[k], 'n': n} for k, n in top]

    # 第 326 项：存缓存（超过 24 组就清旧的，别让缓存自己长成内存问题）。
    _FACETS_CACHE[ck] = (now, out)

    if len(_FACETS_CACHE) > 24:

        for k in sorted(_FACETS_CACHE, key=lambda k: _FACETS_CACHE[k][0])[:8]:

            _FACETS_CACHE.pop(k, None)

    return out





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

    # 第 321 项：os.stat 一律在锁外做。
    # 以前调用点在 `with con_lock:` 里，一圈 4000 个目录挨个 stat，碰上网络盘 /
    # 云盘 / 已拔掉的移动硬盘，一个 stat 就能卡几秒 —— 锁被攥住几分钟，整个
    # HTTP 面排队等锁（11-19 11:2x 实测：/api/files、/api、/api/stats 全 DB_LOCK_BUSY）。
    # 现在只把「读一页路径」「写回一批」各包一小段锁。
    with db_lock('backfill_dir:read', 10.0):

        rows = con.execute(sql, args).fetchall()

    n = 0

    pend = []

    for (path,) in rows:

        try:

            mt = os.stat(path).st_mtime

        except OSError:

            continue

        pend.append((mt, path))

        if len(pend) >= 50:

            with db_lock('backfill_dir:write', 10.0):

                con.executemany('UPDATE dirs SET mtime=? WHERE path=?', pend)

                con.commit()

            n += len(pend)

            pend = []

            if n % batch == 0:

                _prog(k='m_dir_mtime', a={'n': n})

    if pend:

        with db_lock('backfill_dir:write', 10.0):

            con.executemany('UPDATE dirs SET mtime=? WHERE path=?', pend)

            con.commit()

        n += len(pend)

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





# 第 379 项：详情面板「真版面预览」要原始文件字节。
# 索引里存的是抽出来的纯文本（doc_rec 的 zstd blob），docx/xlsx 的版式、表格、
# 分页早就在抽取那一步丢了 —— 前端要画版面只能回头读原文件。
# 只放行这三种「zip+xml」格式：老式 .doc/.xls 前端也能解析，但它们没有版式可画，
# 放进来只是多一条读盘通道，没必要。
# 第 401 项：老 Excel 也放行 —— 前端换成 xlsx.full 后能解析 .xls/.et 的 BIFF
_RAW_OK_EXT = ('docx', 'xlsx', 'pptx', 'xls', 'et')

_RAW_MIME = {
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'xls': 'application/vnd.ms-excel',
    'et': 'application/vnd.ms-excel',
}

# 48MB：正常合同/报表远小于这个数，再大前端渲染也顶不住，直接不给。
_RAW_MAX = 48 * 1024 * 1024


_PEEK_CACHE = {}





def peek_body(con, path, q='', span=360, page=0, chunk=900):

    """详情面板用：给某一份文档取一段正文。



    成本说明：docs 是 FTS5 虚表、path 是 UNINDEXED 列，`WHERE path=?` 会扫全表

    （5,700 篇 / 86MB，实测 ~100ms 冷、~20ms 热）。所以只在用户主动点「读一段正文」

    时调用，并且按 path 缓存 5 分钟——正常搜索根本不碰这条路径。

    第 332 项：`page` > 0 表示「按块分期看正文」（Word/Excel/PPT 这类没有分页图的文档，
    详情灯箱里 ← → 要能一页页往下读，读到底才换下一份文件）。块大小 `chunk` 字，
    返回里带上 `page` / `pages`，前端据此显示「3 / 12」并决定箭头是翻页还是换文件。
    """

    key = (path, (q or '').strip().lower(), int(page or 0), int(chunk or 0))

    hit = _PEEK_CACHE.get(key)

    now = time.time()

    if hit and now - hit[0] < 300:

        return hit[1]

    tok = (q or '').strip()

    try:

        body0 = doc_body_get(con, None, path)
        row = (body0,) if body0 else None

    except sqlite3.Error:

        row = None

    if not row or not row[0]:

        return {'ok': False, 'why': 'no_body'}

    body = str(row[0])

    body_len = len(body)

    if page and page > 0:

        csize = max(200, int(chunk or 900))

        pages = max(1, (body_len + csize - 1) // csize)

        pno = min(max(1, int(page)), pages)

        seg = body[(pno - 1) * csize: pno * csize]

        seg = re.sub(r'\s+', ' ', seg).strip()

        out = {'ok': True,

               'text': _mark_text(seg, _fts_tokens(tok)) if tok else seg,

               'offset': (pno - 1) * csize, 'length': body_len,

               'matched': bool(tok) and tok.lower() in seg.lower(),

               'page': pno, 'pages': pages}

        _PEEK_CACHE[key] = (now, out)

        if len(_PEEK_CACHE) > 200:

            for k in sorted(_PEEK_CACHE, key=lambda k: _PEEK_CACHE[k][0])[:50]:

                _PEEK_CACHE.pop(k, None)

        return out

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

           'offset': offset, 'length': len(body), 'matched': pos >= 0,
           'page': 1, 'pages': max(1, (body_len + max(200, int(chunk or 900)) - 1) // max(200, int(chunk or 900)))}

    _PEEK_CACHE[key] = (now, out)

    # 第 326 项：正文片段缓存原来没有上限 —— 一天点几百篇就在内存里攒几百段，
    # 每段 360 字。给个 200 条上限（用户真正回看的也就最近几十条）。
    if len(_PEEK_CACHE) > 200:

        for k in sorted(_PEEK_CACHE, key=lambda k: _PEEK_CACHE[k][0])[:50]:

            _PEEK_CACHE.pop(k, None)

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

    前端换成 <mark> —— 这两家长处都不在正文，这是Memex唯一守得住的差异。"""

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

    # 第 297 项：.msc（设备管理器/任务计划程序）与 .cpl 也是「程序」，
    # 显示名同样应该给中文，而不是让用户看到 taskschd.msc。
    if e not in ('.lnk', '.appref-ms', '.exe', '.msc', '.cpl'):

        return name

    stem = os.path.splitext(name)[0]

    for cand in (stem, (descr or '').strip()):

        if cand and any('\u4e00' <= ch <= '\u9fff' for ch in cand):

            return cand

    if e in ('.msc', '.cpl') and not (descr or '').strip():
        return name          # 第 297 项：管理工具没有描述时保留扩展名，别只给个 taskschd
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

    if len(_TGT_CACHE) > 8000:      # 全盘几十万文件，别让缓存无限长（第 326 项：2 万 → 8 千）

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





def _dedupe_nested(items):

    """同一个安装目录里叠着的同名 exe，只留最外层那条。



    真实案例（2026-11-13）：搜「豆包」给出三条同名结果 ——

    D:\\Doubao\\Doubao.exe、D:\\Doubao\\app\\Doubao.exe、D:\\Doubao\\app\\launcher\\Doubao.exe。

    用户点了中间那条（launcher 副本），它只会去**自己所在目录**找 app\\ 子目录，

    于是弹 "Can't find main exe file"（实测复现，窗口标题「错误」）。



    用户不需要知道一个程序内部有几层启动器。只合并「文件名相同 + 一条路径落在

    另一条的目录树里」的，所以 C:\\Python312\\python.exe 与别处的 python.exe

    这种同名无关的不会被误合。

    """

    appext = ('.exe', '.bat', '.cmd', '.msi')

    groups = {}

    for i, it in enumerate(items):

        if str(it.get('e') or '').lower() not in appext:

            continue

        stem = os.path.splitext(os.path.basename(str(it.get('p') or '')))[0].lower()

        groups.setdefault(stem, []).append(i)

    drop = set()

    for idxs in groups.values():

        if len(idxs) < 2:

            continue

        idxs.sort(key=lambda i: len(str(items[i].get('p') or '')))      # 层级浅的在前

        kept = []

        for i in idxs:

            d = os.path.dirname(os.path.normcase(str(items[i].get('p') or '')))

            host = None

            for j in kept:

                qd = os.path.dirname(os.path.normcase(str(items[j].get('p') or '')))

                if d == qd or d.startswith(qd + os.sep):

                    host = j

                    break

            if host is None:

                kept.append(i)

            else:

                drop.add(i)

                host_it = items[host]

                host_it['alts'] = (int(host_it.get('alts') or 0) + 1

                                   + int(items[i].get('alts') or 0))

    if not drop:

        return items

    return [it for k, it in enumerate(items) if k not in drop]


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

    out = _dedupe_nested(out)


    return out[:limit] if limit else out





_SCOPE_CACHE = {}
_SCOPE_TTL = 3.0
_SCOPE_MAX = 6000


def _scope_items(scope, q, limit=9):
    """在某个目录里找名字（第 197 项「只搜这个文件夹」）。

    两路并用：
    ① 实时扫目录 —— 用户刚新建 / 刚改名的文件还没进索引，这一路最准；但为了快，
       只往下 4 层、最多 6000 个条目，大目录 / 深路径靠它翻不完。
    ② files 表按路径前缀查 —— 不受层数和条目上限影响，深路径、超大目录翻得到。

    以前只有 ①，于是「某个文件夹第 5 层里的文件」怎么都搜不到，用户看到的是
    被全局结果补满的列表 —— 这就是「只在所在文件夹搜索失败」。
    """
    q = (q or '').strip().lower()
    try:
        root = os.path.normpath(str(scope or ''))
    except Exception:
        return []
    # 'I:' 这种只写到盘符的形式，normpath 会保留成 'I:'，一刀不减 —— 拿它去
    # scandir / join，Windows 会当成「该盘的当前目录」，拼出来的路径变成
    # 'I:文件夹'（少了那个反斜杠），既打不开又和真路径对不上，列表里就会
    # 出现同一条文件两个样子。盘符一律补成 'I:\'。
    if len(root) == 2 and root[1] == ':':
        root += '\\'
    if not q or not root or not any(ch.isalnum() for ch in q):
        return []
    try:
        pre = os.path.normcase(root).rstrip('\\') + '\\'
    except Exception:
        return []

    out, seen = [], set()

    def _push(it):
        k = os.path.normcase(it.get('p') or '')
        if not k or k in seen:
            return
        seen.add(k)
        out.append(it)

    # ① 实时扫目录（带 3 秒缓存：连打同一个词不再扫第二遍盘）
    key = (pre, q)
    now = time.time()
    with MINI_LOCK:
        got = _SCOPE_CACHE.get(key)
    live = None
    if got and now - got[0] < _SCOPE_TTL:
        live = got[1]
    elif os.path.isdir(root):
        hits, cnt, stack = [], 0, [(root, 0)]
        while stack and cnt < _SCOPE_MAX:
            cur, depth = stack.pop()
            try:
                it = os.scandir(cur)
            except OSError:
                continue
            with it:
                while True:
                    try:
                        e = next(it)
                    except (StopIteration, OSError):
                        break
                    cnt += 1
                    if cnt > _SCOPE_MAX:
                        break
                    try:
                        isdir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    nl = e.name.lower()
                    if q in nl:
                        hits.append((0 if nl.startswith(q) else 1, depth,
                                     len(e.name), nl, e.path, isdir))
                    if isdir and depth < 3 and not nl.startswith('.'):
                        stack.append((e.path, depth + 1))
        hits.sort()
        live = []
        for rank, _dep, _ln, _nl, path, isdir in hits:
            nm = os.path.basename(path)
            ext = '' if isdir else os.path.splitext(nm)[1].lower()
            try:
                st = os.stat(path)
                mtime, size = st.st_mtime, st.st_size
            except OSError:
                mtime, size = None, None
            live.append({'n': nm, 'p': path, 'd': os.path.dirname(path),
                         't': 'dir' if isdir else 'file', 'e': ext, 'app': 0,
                         'dn': _disp_name(nm, ext, None), 'm': mtime, 's': size,
                         'z': size, 'r': rank, 'in': 1})
        with MINI_LOCK:
            if len(_SCOPE_CACHE) > 64:
                _SCOPE_CACHE.clear()
            _SCOPE_CACHE[key] = (time.time(), [dict(x) for x in live])
    for x in (live or []):
        _push(dict(x))

    # ② 索引补：同一路径前缀下、名字含 q 的条目（深路径只有这一路翻得到）
    try:
        with con_lock:
            rows = conn().execute(
                'SELECT name, path, dir, ext, size, mtime FROM files '
                'WHERE instr(lower(path), ?) = 1 AND instr(lower(name), ?) > 0 '
                'ORDER BY (CASE WHEN substr(lower(name), 1, ?) = ? THEN 0 ELSE 1 END), '
                'mtime DESC LIMIT ?',
                (pre, q, len(q), q, max(limit, 9) * 2)).fetchall()
        for name, path, dirp, ext, size, mtime in rows:
            _push({'n': name, 'p': path, 'd': dirp or os.path.dirname(path),
                   't': 'file', 'e': (ext or '').lower(), 'app': 0,
                   'dn': _disp_name(name, ext, None), 'm': mtime, 's': size,
                   'z': size, 'r': 0 if (name or '').lower().startswith(q) else 1,
                   'in': 1})
    except Exception:
        pass

    out.sort(key=lambda x: (x.get('r', 1), x.get('t') != 'dir', -(x.get('m') or 0)))
    return out[:limit]

def mini_search(con, q, limit=9, deep=False, body=False, content_only=False, force_body=False, pf=None):

    """返回 {'items': [...], 'deep': bool, 'ms': int}。



    deep=False：中文前缀 + 拼音前缀，全部走索引，毫秒级；

    deep=True ：再加「名字任意位置包含」（全表扫）和拼音模糊 —— 用户停手后才发；

    body=True ：正文命中给固定配额，排在名字命中后面（见下方 body 分支的说明）；

    content_only=True：只搜正文（用户敲了 c: / content: 前缀），名字一个都不要。
    force_body=True：右侧「正文」按钮开启 —— 名字和正文都要，且不受 first_app 掐断。"""

    q = (q or '').strip().lower()

    if not q:

        return {'items': [], 'deep': deep, 'ms': 0, 'n': 0}

    if not any(ch.isalnum() for ch in q):

        # 只敲了「.」「%」「 」这类符号：结果必然是几百个带扩展名的文件，没有信息量，

        # 而且每敲一下就多跑一次全表扫。直接回空。

        return {'items': [], 'deep': deep, 'ms': 0, 'n': 0}

    # 第 324 项：空格分隔的多个关键词 = AND。正文那一路（_fts_query）早就是 AND，
    # 名字这一路却一直把整串当短语（instr(name,'子公司 2025 营收')），
    # 于是「子公司 2025 营收」名字命中一条都没有，用户看到的就是「搜不到」。
    _toks = _fts_tokens(q)

    _multi = len(_toks) > 1

    # 第 324 项：左栏「文件夹」子分类点进来时带目录前缀（pf），在 SQL 里就收口，
    # 不能等 mini_search 回来再过滤 —— 200 条候选里可能一条都不在那个目录，
    # 用户看到的就是「点了文件夹却是空的」。
    _pf_sql, _pf_args = '', []

    if pf:

        _pf_sql = ' AND instr(lower(path),?)>0'

        _pf_args = [str(pf).lower()]

    ck = (q, bool(body), bool(content_only), bool(force_body), _multi, str(pf or ''))

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

        # 第 246 项：虚拟应用入口的 path 是 AUMID，basename 是包名 ID；
        # 显示名由 app_entries 写进 files.descr，这里换回来，
        # 否则搜到 Watt Toolkit 却只显示一串 4651ED44255E...!App。
        _shell = app_entries.is_shell_app_path(path)
        if _shell and descr:
            name = str(descr)

        items.append({'n': name, 'p': path,

                      'd': os.path.dirname(path.rstrip('\\/')),

                      't': 'dir' if isdir else 'file',

                      'e': '' if isdir else ext,

                      'app': 1 if (not isdir and (_shell or apprank.is_app_file(name))) else 0,

                      'dn': (None if isdir else apprank.name_for_exe(name)) or _disp_name(name, ext, descr),

                      'm': mtime, 's': size, 'z': size,

                      'r': _mini_rank(name.lower(), q) if rank is None else rank})

        _rk[key] = items[-1]

    # 第 297 项：系统程序（设置 / cmd / 记事本…）不在文件索引里 —— 系统目录被排除、
    # 「设置」是 MSIX 应用没有 .lnk、cmd 不是「已安装程序」。它们只在查询时补，不写库：
    # 既不会被对账/清理规则当死路径删掉，也不占索引空间。ab 直接给高权重（排序第一键），
    # 用户敲 cmd / 设置 时第一行就是它。
    try:
        for _sa in sys_apps.match(q, lang=boot_lang()):
            add(_sa['path'], False, _sa['ext'], 0, _sa['name'])
            _k = os.path.normcase(_sa['path'])
            if _k in _rk:
                _rk[_k]['ab'] = max(int(_rk[_k].get('ab') or 0), int(_sa['ab']))
                _rk[_k]['app'] = 1
    except Exception:
        pass



    frows = drows = []

    # 两张表分开兜错：老库还没有 dirs 表（v6.3 之前建的索引），

    # 一个 OperationalError 不该把 files 的结果也一起吞掉。

    # 应用中文名匹配：搜「微信」能找到 wechat.exe —— VerInfo FileDescription 字段。

    try:

        if deep or _multi:

            # 深搜：名字任意位置 + descr 任意位置，两路 OR

            # 两阶段：先只用 rowid 扫名字索引（18MB，实测 40~47ms），再按 rowid 回表。

            # 老写法 `SELECT path,… WHERE instr(name,?)` 会读整张 216MB 的表（160~203ms）。

            # 第 324 项：多关键词改「每个词都出现在名字里」（AND），单关键词照旧整串。
            _nm_where = (' AND '.join('instr(name,?)>0' for _ in _toks)

                         if _multi else 'instr(name,?)>0')

            _nm_args = list(_toks) if _multi else [q]

            _ids = [r[0] for r in con.execute(

                'SELECT rowid FROM files WHERE ' + _nm_where + _pf_sql + ' LIMIT ?',

                (*_nm_args, *_pf_args, limit * 45)).fetchall()]

            if _ids:

                frows = con.execute(

                    'SELECT path, ext, descr, mtime, size FROM files WHERE rowid IN ('

                    + ','.join('?' * len(_ids)) + ')', _ids).fetchall()

            else:

                frows = []

            # 描述走 appdesc 小表：files 上 `instr(descr,?)` 是全表扫（607,731 行、174ms），

            # 而真正有描述的只有 3,749 行。分开查再拼，结果一样，深搜快 170ms。

            # 第 324 项：多关键词同样要 AND（「微软 浏览器」不该命中描述里只有「微软」的项）。
            _ds_where = (' AND '.join('instr(lower(descr),?)>0' for _ in _toks)

                         if _multi else 'instr(lower(descr),?)>0')

            _ds_args = list(_toks) if _multi else [q]

            try:

                frows = list(frows) + list(con.execute(

                    'SELECT path, ext, descr, mtime, size FROM appdesc '

                    'WHERE ' + _ds_where + _pf_sql + ' LIMIT ?', (*_ds_args, *_pf_args, limit * 10)).fetchall())

            except sqlite3.Error:

                pass

        else:

            hi = q + '\uffff'          # BINARY 序：q 之后、q+任何常规字符之前

            # 缩写命中：dsh → DeepSeek Harness（Listary 的招牌功能之一，只查程序/快捷方式）

            for _p, _e in acr_hits(q, limit=limit):

                add(_p, False, _e, 1)

            frows = con.execute('SELECT path, ext, descr, mtime, size FROM files WHERE name>=? AND name<?' + _pf_sql + ' LIMIT ?',

                                (q, hi, *_pf_args, limit * 8)).fetchall()

            # 前缀命中会被同名文件淹掉：搜「微信」全盘有 289 个「微信-2026*.aac」通话录音，

            # 而 SQLite 按字节序排，'-'(0x2D) < '.'(0x2E) → 录音全排在 微信.lnk 前面，

            # 72 个名额被吃光，应用压根进不了候选集（后面排得再好也没用）。

            # 所以应用类（exe/lnk/bat…）在这一步单独再捞一次，优先合入。

            try:

                arows = con.execute(

                    'SELECT path, ext, descr, mtime, size FROM files WHERE name>=? AND name<? AND ext IN '

                    "('.exe','.lnk','.bat','.cmd','.ps1','.appref-ms')" + _pf_sql + ' LIMIT ?',

                    (q, hi, *_pf_args, limit * 5)).fetchall()

                frows = list(arows) + list(frows)

            except sqlite3.Error:

                pass

            # 浅搜也补一条 descr 命中 —— 这是 exe 的中文名，优先级很高

            if q and not _is_letters(q):

                try:

                    extra = con.execute(

                        'SELECT path, ext, descr, mtime, size FROM appdesc '

                        'WHERE instr(lower(descr),?)>0' + _pf_sql + ' LIMIT ?', (q, *_pf_args, 30)).fetchall()

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

        # 第 246 项：虚拟应用入口没有磁盘路径，exists() 永远 False。
        # 不放行的话刚写进 files 表的商店应用会被当成死路径丢掉并 _mark_gone。
        if p and (os.path.exists(p) or app_entries.is_shell_app_path(p)):

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

        slack = _body_slots(len(out), first_app and not force_body, limit)

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



# 第 229 项五轮：用户嘴里说的「格式名」→ 真扩展名。ext:word 不是 .word（根本
# 不存在这个扩展名），用户想要的是 doc/docx。只收最常见的几个，别把「txt 该写
# txt」这种常识也包进去。
EXT_ALIASES = {'word': 'doc,docx', 'excel': 'xls,xlsx', 'ppt': 'ppt,pptx',
               '图片': 'jpg,jpeg,png,gif,webp,bmp'}

def parse_qsyn(q):

    """把「WinShun 式」语法从搜索词里剥出来：ext:pdf / size:>10m / dm:7d / -排除 / 某个文件夹/。



    用户在搜索框里直接写就行，不用去侧栏点。剥完剩下的词才是真正的关键词；

    看不懂的片段原样留在关键词里——宁可搜不到，也别悄悄吞掉用户打的字。"""

    raw = str(q or '')

    # 第 309 项：先把「前缀 + 冒号」两边的空白与全角冒号收拢：
    # `ext : pdf` / `ext： pdf` / `ext: pdf` → `ext:pdf`。
    # 不这么做的话，`ext:` 会以「空值语法前缀」身份落回关键词，
    # 变成一个永远匹配不到的字符串，用户看到的就是「关键词 ext:pdf 搜不到东西」。
    # 只动这几个已知前缀，文件名里正常的冒号（`报告：终稿.docx`）不受影响。
    raw = re.sub(r'(?i)(^|[\s\u3000])(ext|type|size|dm|content|内容)[\s\u3000]*[:：][\s\u3000]*',
                 lambda m: m.group(1) + m.group(2) + ':', raw)

    syn = {'q': raw.strip(), 'ext': None, 'size': (None, None),

           'days': None, 'excl': [], 'folder': None, 'content': False}

    rest = []

    # 第 294 项（H）：双引号内的空格不切分 —— 目录名带空格时（"D:\\我的 文件\\"），
    # 原来按空白切会把路径切成两个词，以斜杠结尾的 folder 规则直接失效。
    import re as _re_qs
    _toks = _re_qs.findall(r'"[^"]*"|[^\s\u3000]+', raw)

    # 第 309 项：`ext: pdf, docx` 这种逗号后带空格的写法，切词后 `pdf,` 与 `docx`
    # 分成两段，后半段会掉成关键词。以逗号结尾的 ext/type 前缀后面再拼回一个词。
    _merged = []
    for _t in _toks:
        _lt = _t.lower()
        if (_merged and _merged[-1].rstrip().endswith(',')
                and _merged[-1].lower().startswith(('ext:', 'type:'))):
            _merged[-1] = _merged[-1] + _t
        else:
            _merged.append(_t)
    _toks = _merged
    for tok in _toks:

        if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
            tok = tok[1:-1]

        low = tok.lower()

        # 第 229 项四轮：全角冒号也认（ext：pdf / size：>10m / dm：7d / 内容：微信）。
        # 中文输入法默认打「：」，以前只认半角，用户打出来就被当成普通关键词。
        # 只在「已知语法前缀 + 全角冒号」这个位置替换 —— 文件名里合法的 ： 不受影响
        # （报告：终稿.docx 照样按名字搜），c：\users 这类盘符路径照旧被 c: 分支放行。
        for _pfx in ('ext', 'type', 'size', 'dm', 'content', 'c', '内容'):

            if low.startswith(_pfx + '：'):

                tok = tok[:len(_pfx)] + ':' + tok[len(_pfx) + 1:]

                low = low[:len(_pfx)] + ':' + low[len(_pfx) + 1:]

                break

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

            # 第 229 项五轮：中文输入法的全角逗号/顿号也当分隔符；word/excel/ppt
            # 这类「类型名」按 EXT_ALIASES 展开成真扩展名（ext:word 以前永远 0 条）。
            exts = [x for e in re.split(r'[，、,\s]+', v) if e.strip()

                    for x in EXT_ALIASES.get(e.strip().lstrip('.'),
                                             e.strip().lstrip('.')).split(',')]

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

    # 「文件夹/」语法（第 238 项）：解析器一直有 syn['folder']，但这里从来没判过，
    # 于是 `工作\` 被当成「没有关键词」→ 落到「浏览最近 400 条」，筛选等于没写。
    folder = str(syn.get('folder') or '').strip().lower()

    if folder and folder not in str(path or '').lower():

        return False

    for x in (syn.get('excl') or []):

        if x and x in str(path or '').lower():

            return False

    return True



SORT_KEYS = ('rel', 'new', 'old', 'big', 'name')



def dir_rows(con, q, limit=200, pf=''):
    """第 327 项：左栏点「文件夹」要有东西。

    目录在 dirs 表，以前这条路走的是 /api/files（只查 files 表），所以左栏数字
    显示 41072、点进去一片空白 —— 用户原话「还是没东西啊」。
    关键词按目录名 LIKE，不带关键词就按修改时间倒序（和文件那路一致）。
    """
    kw = (q or '').strip().lower()
    where, args = [], []
    if pf:
        pre = os.path.normcase(pf.rstrip('\\/')) + os.sep.lower()
        where.append('lower(path) LIKE ?')
        args.append(pre + '%')
    if kw:
        where.append('lower(name) LIKE ?')
        args.append('%' + kw + '%')
    sql = 'SELECT path,name,mtime FROM dirs'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY mtime DESC LIMIT %d' % max(1, min(400, int(limit)))
    try:
        rows = con.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []
    return [{'p': p, 'n': nm, 't': 'dir', 'e': '', 'm': mt, 's': 0}
            for (p, nm, mt) in rows]


def syntax_only_items(con, syn, limit=400):

    """第 229 项五轮：纯语法筛选（无关键词）的统一 SQL 出口。

    ext:pdf / size:>2m / dm:7d 这类不带关键词的查询，以前全靠「最近 400 条再
    逐条筛」—— 最近 400 条里可能根本没有大文件，「size:>2m 搜不到」就是这么
    来的（用户 16:1x 实测）。现在把 ext/size/days 直接翻成 SQL WHERE 一次查表：
    - ext → lower(ext) IN (...)（ext 无索引，全表扫一次百 ms 级）
    - size → size >= / <=（bytes；边界与 syn_ok 的闭区间一致）
    - days → mtime >= now-days*86400（走 idx_files_mtime）
    排序仍 mtime 新的在前；-排除/文件夹/ 这类「名字级」语法留给 syn_ok 在
    结果上收口。返回 (items, total)，条目形状与 mini_search 一致
    （n/p/d/t/e/app/dn/m/z/r）。"""

    lo, hi = syn.get('size') or (None, None)

    days = syn.get('days')

    exts = []

    for e in str(syn.get('ext') or '').split(','):

        e = e.strip().lstrip('.').lower()

        if e:

            exts.append('.' + e)

    wheres, args = [], []

    if exts:

        wheres.append('lower(ext) IN (%s)' % ','.join('?' * len(exts)))

        args.extend(exts)

    if lo is not None:

        wheres.append('size >= ?')

        args.append(float(lo))

    if hi is not None:

        wheres.append('size <= ?')

        args.append(float(hi))

    if days:

        wheres.append('mtime >= ?')

        args.append(time.time() - int(days) * 86400)

    # 文件夹限定（`工作\` / `D:/资料/`）：以前只在解析器里存着、没人用。
    # 这里翻成 SQL，省得先取 400 条再逐条筛（换个目录就一条都不剩）。
    folder = str(syn.get('folder') or '').strip().lower()

    if folder:

        wheres.append('instr(lower(path), ?) > 0')

        args.append(folder)

    if not wheres:

        return [], 0

    rows = con.execute(
        'SELECT path,name,ext,mtime,size FROM files WHERE %s '
        'ORDER BY mtime DESC LIMIT %d' % (' AND '.join(wheres), int(limit)),
        args).fetchall()

    items = []

    for (p, nm, ex, mt, sz) in rows:

        base = os.path.basename(p.rstrip('/' + chr(92))) or nm

        items.append({'n': base, 'p': p, 'd': os.path.dirname(p.rstrip('/' + chr(92))),

                      't': 'file', 'e': (ex or '').lower(),

                      'app': 1 if apprank.is_app_file(base) else 0,

                      'dn': apprank.name_for_exe(base) or None,

                      'm': mt, 's': sz, 'z': sz, 'r': 0, 'src': 'disk'})

    return items, len(items)


def sort_items(items, sort='rel'):

    """排序只对已经拿到的一屏结果做，不重查数据库。"""

    s = str(sort or 'rel').lower()

    # 第 298 项：ab>0 = 这次查询命中了常用应用白名单（微信本体 / 设置 / cmd…）。
    # 手选的排序（名称/时间/大小）不该把「真正要打开的那个程序」埋到第十几行，
    # 所以应用行在任何模式下都排在最前，其余仍在各自模式内部有序。
    def _app_first(it):
        return 0 if (it.get('ab') or 0) > 0 else 1

    if s == 'new':

        items.sort(key=lambda it: (_app_first(it), -(it.get('m') or 0)))

    elif s == 'old':

        items.sort(key=lambda it: (_app_first(it), (it.get('m') or 0)))

    elif s == 'big':

        items.sort(key=lambda it: (_app_first(it), -(it.get('s') or 0)))

    elif s == 'name':

        items.sort(key=lambda it: (_app_first(it),
                                   str(it.get('n') or os.path.basename(str(it.get('p') or ''))).lower()))

    return items



def _v2_add_name_hits(con, q, hits, limit, ext=None, path_filter=None):
    """v2：正文命中不够时按文件名补（和 v1 一样扫 meta，不碰正文）。"""
    toks = _fts_tokens(q)
    if not toks:
        return hits
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
    a2.append(limit)
    seen = {p for p, _ in hits}
    out = list(hits)
    for (p,) in con.execute(sql2, a2):
        if p in seen:
            continue
        seen.add(p)
        out.append((p, _mark_name(p, toks)))
        if len(out) >= limit:
            break
    return out


def search_rows(con, q, limit=20, ext=None, path_filter=None, with_name=True):

    """正文检索 + 文件名补充检索。



    docs 里 path 是 UNINDEXED 列，FTS 根本看不到文件名，于是「文件名里有、正文里没有」

    的文档搜不出来（实际用起来这正是最常见的抱怨点）。正文命中不够时再扫一遍路径：

    万级路径的 LIKE 是毫秒级，不用为它加索引列——那会逼着所有人重建整个索引。"""

    if doc_mode(con) == 'v2':
        hits = _search_v2(con, q, limit, ext, path_filter)
        if len(hits) >= limit or not with_name:
            return hits
        return _v2_add_name_hits(con, q, hits, limit, ext, path_filter)

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

# Memex只能搜「索引里那几千篇文档」的正文和文件名；Everything 知道整台机器上每个文件名。

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





def shell_app_icon(path, size=48):
    """第 248 项：虚拟商店应用（shell:AppsFolder\\<AUMID>）的真实图标 → PNG。

    Windows 商店 / MSIX 应用的显示名和图标都不在文件系统里：
      * `SHGetFileInfoW` 直接传 'shell:AppsFolder\\...' 字符串会失败（实测 ok=0）；
      * 必须走 IShellItemImageFactory（SHCreateItemFromParsingName + GetImage）
        才能拿到开始菜单那张磁贴图标。
    实测 6 个不同 AUMID 的图标 hash 全不相同 —— 不是通用占位图。
    取不到一律返回 None，调用方回落自绘图标（绝不能让整行结果消失）。
    """
    if not app_entries.is_shell_app_path(path):
        return None
    aumid = str(path)[len(app_entries.SHELL_PREFIX):]
    if not app_entries.is_virtual_appid(aumid):
        return None
    key = 's:' + os.path.normcase(str(path))
    with ICON_LOCK:
        if key in ICON_CACHE:
            return ICON_CACHE[key]
    png = None
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort),
                        ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]

        class _SIZE(ctypes.Structure):
            _fields_ = [('cx', ctypes.c_long), ('cy', ctypes.c_long)]

        class _BIH(ctypes.Structure):
            _fields_ = [('biSize', ctypes.c_ulong), ('biWidth', ctypes.c_long),
                        ('biHeight', ctypes.c_long), ('biPlanes', ctypes.c_ushort),
                        ('biBitCount', ctypes.c_ushort), ('biCompression', ctypes.c_ulong),
                        ('biSizeImage', ctypes.c_ulong), ('biXPelsPerMeter', ctypes.c_long),
                        ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', ctypes.c_ulong),
                        ('biClrImportant', ctypes.c_ulong)]

        ole32, shell32, gdi32 = (ctypes.windll.ole32, ctypes.windll.shell32,
                                 ctypes.windll.gdi32)
        # HTTP 线程里 COM 未必初始化过；S_FALSE / RPC_E_CHANGED_MODE 都忽略。
        ole32.CoInitializeEx(None, 2)
        iid = _GUID()
        ole32.IIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_GUID)]
        ole32.IIDFromString.restype = ctypes.c_long
        if ole32.IIDFromString('{BCC18B79-BA16-442F-80C4-8A59C30C463B}',
                               ctypes.byref(iid)) != 0:
            return None
        shell32.SHCreateItemFromParsingName.argtypes = [
            wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
        obj = ctypes.c_void_p()
        if shell32.SHCreateItemFromParsingName(str(path), None, ctypes.byref(iid),
                                               ctypes.byref(obj)) != 0 or not obj:
            return None
        try:
            vt = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            # IShellItemImageFactory: IUnknown(3) + GetImage(SIZE, SIIGBF, HBITMAP*)
            get_image = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, _SIZE, ctypes.c_int,
                ctypes.POINTER(wintypes.HANDLE))(vt[3])
            hbmp = wintypes.HANDLE()
            if get_image(obj, _SIZE(size, size), 0, ctypes.byref(hbmp)) != 0 or not hbmp:
                return None
            try:
                gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
                gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
                gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            wintypes.UINT, wintypes.UINT,
                                            ctypes.c_void_p, ctypes.POINTER(_BIH),
                                            wintypes.UINT]
                gdi32.GetDIBits.restype = ctypes.c_int
                gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
                gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
                hdc = gdi32.CreateCompatibleDC(0)
                try:
                    bih = _BIH()
                    bih.biSize = ctypes.sizeof(_BIH)
                    bih.biWidth, bih.biHeight = size, -size
                    bih.biPlanes, bih.biBitCount, bih.biCompression = 1, 32, 0
                    buf = (ctypes.c_ubyte * (size * size * 4))()
                    if gdi32.GetDIBits(hdc, hbmp, 0, size,
                                       ctypes.cast(buf, ctypes.c_void_p),
                                       ctypes.byref(bih), 0) == size:
                        png = _rgba_to_png(bytes(buf), size)
                finally:
                    gdi32.DeleteDC(hdc)
            finally:
                gdi32.DeleteObject(hbmp)
        finally:
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(obj)
    except Exception:
        png = None
    with ICON_LOCK:
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

# 第 313 项：**不要**在这里给 Word/Excel/PPT 加 Shell 缩略图分支。

# 312 项试过一次，装机的下一个版本就把主窗拖到「未响应」：前端缩略图视图（App.tsx 的

# THUMB_EXT 里本来就有 docx/xlsx/pptx）会对每一格发 /api/thumb，Shell 缩略图处理器在

# 随机线程里调 COM 会卡住，几格就把 6 条 HTTP 线程全占死 —— 连 /api/ping 都不回。

# 本机 WPS 也没注册处理器（实测 docx 全是 None），所以这条路本来就没有收益。

# 详情面板看 Word/Excel 走正文首段（panels.tsx 的 AUTO_PEEK_EXT），不要图。

# 视频：不装 ffmpeg，直接用 Windows 自带的缩略图服务（资源管理器里能看到缩略图的格式它都能出）

VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.ts', '.webm', '.mpg', '.mpeg'}

VIDEO_THUMB_TIMEOUT = 8.0           # 秒；视频取帧超过这个时间就别要了


def _thumb_video_size(max_w):
    """第 336 项：视频缩略图请求尺寸。

    Shell 缩略图服务按调用方给的尺寸解码：网格格子（max_w≈320）给 256 就够，
    详情栏（big=1，max_w=900）给 640 才不至于糊成一团，展开（xl，1600）给 960。
    上限压到 960，再大就要多等几百毫秒，收益不值。
    """
    try:
        w = int(max_w)
    except Exception:
        w = 320
    if w >= 1200:
        return 960
    if w >= 600:
        return 640
    return 256

THUMB_LOCK = threading.Lock()

# 磁盘缓存：%LOCALAPPDATA%\SecondBrain\thumbs。库里 40k 视频 + 29 万图片，

# 光靠进程内 100 张的 THUMB_CACHE 每次重启都要重解码一遍，翻两页就明显卡。

THUMB_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),

                         'SecondBrain', 'thumbs')

# 第 400 项：画框方式变了（search_for 按行分组、扫描件走 OCR 框）。旧缓存图是按老算法
# 画的，key 里加个版本号，让它自然失效，不用用户手动清缓存。
THUMB_SCHEME = 'v2'





def _thumb_disk_path(path, max_w, max_h, page=0, hl=(), hv=''):

    """路径 + 修改时间 + 大小 + 尺寸（+ 第 326 项：页码、高亮词）→ 缓存文件名。



    带上 mtime/size，文件换了内容自然就换 key，不需要额外失效逻辑。

    第 403 项：`hv` 是「高亮数据版本」（前端按 OCR 已识别页数传进来）——
    扫描件第一次请求时 OCR 还没跑到这一页，画不出框；那张「没框」的图会被
    磁盘 + 浏览器（max-age=86400）一起记住，之后 OCR 认出命中页也不会更新。
    把 hv 并进缓存键，OCR 有进展就是一个新 key，自然重渲一张带框的。"""

    try:

        st = os.stat(path)

        stamp = '%d:%d' % (int(st.st_mtime), st.st_size)

    except OSError:

        stamp = 'na'

    raw = ('%s|%s|%dx%d|p%d|%s|%s|%s' % (os.path.normcase(path), stamp, max_w, max_h,
                                        int(page or 0), ','.join(hl or ()),
                                        THUMB_SCHEME, hv or '')).encode('utf-8', 'replace')

    import hashlib

    return os.path.join(THUMB_DIR, hashlib.sha1(raw).hexdigest() + '.png')





THUMB_MAX_BYTES = 500 * 1024 * 1024   # 缓存总量上限；超了按最旧删

THUMB_MAX_AGE = 30 * 86400            # 30 天没被读过就删

THUMB_FAIL = {}                       # 路径|尺寸 → 失败时间：解不开的文件别每次重试

_THUMB_ERR = {'n': 0}                 # 第 403 项：静默失败的排查计数（只记前 50 条）



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





def pdf_hit_page(path, kw, max_pages=300, snip=''):
    """PDF 命中页（1 基，0 = 没找到/扫描件）。第 397 项：多关键词 + 整份扫。

    不再用 pypdf 逐页 extract_text（还只扫前 30 页 —— 31 页的培训大纲命中在第 31 页
    就永远定位不到，详情栏停在首页，页面上自然一个高亮都没有）。改走 pdf_engine（PDFium）
    的 textpage：只读文本不解码，500 页也就一两秒；任一关键词命中即算这一页。
    """
    pp = str(path or '')
    toks = [t.lower() for t in doc_render._q_tokens(kw)]
    if not pp.lower().endswith('.pdf') or not toks:
        return 0
    try:
        mt = os.path.getmtime(pp)
    except OSError:
        return 0
    # 第 398 项：有「命中的正文」片段时，优先按片段上下文定页 —— 同一份 PDF 里
    # 同一个词可能出现在很多页，第一个命中未必是用户看到的那一段。
    _ctx = doc_render._context_window(snip)
    key = ('hitpg', pp, int(mt), '|'.join(toks), _ctx)
    if key in _PAGE_CACHE:
        return _PAGE_CACHE[key] or 0
    page = 0
    if _ctx:
        page = doc_render._pdf_page_with_ctx(pp, _ctx)
        if page:
            _PAGE_CACHE[key] = page
            return page
    try:
        import pdf_engine as pymupdf
        with pymupdf.open(pp) as doc:
            for i in range(min(len(doc), int(max_pages or 300))):
                try:
                    txt = (doc[i].get_text() or '').lower()
                except Exception:
                    continue
                if any(t in txt for t in toks):
                    page = i + 1
                    break
    except Exception:
        page = 0
    if len(_PAGE_CACHE) > 500:
        _PAGE_CACHE.clear()
    _PAGE_CACHE[key] = page
    return page


def pdf_pages(path, q=''):
    """PDF 总页数（拿不到就 0）。第 325 项：详情栏要显示「12 / 36」。

    第 385 项：Word/Excel/PPT 报的是「转出来的那份 PDF」的页数，
    这样详情栏的翻页器和 PDF 完全共用一套。"""
    try:
        _ext = os.path.splitext(str(path or ''))[1].lower()
        if _ext != '.pdf' and doc_render.is_office(path):
            # 第 388 项：页数在转换时就数好记进 meta.json（0.4s），不用等整份导完再开 PDF 数
            # 第 391 项之二：受保护文档会回 101560 这种鬼页数 —— 不可信就报 0（前端不显示翻页器），
            # 也**不要**拿手上那段 PDF 的页数顶（那段是命中页 ±1，报出去就是「1 / 8」假页数）
            n = doc_render.total_pages(path, app_dir(), q)
            if n and n <= doc_render.MAX_SANE_PAGES:
                return n
            return 0

        import pdf_engine as pymupdf

        with pymupdf.open(path) as doc:

            return len(doc)

    except Exception:

        return 0




# ------------------ 第 399 项：扫描件（无文本层）的页级 OCR ------------------
# PDF 全靠图片、没有文本层时，既定位不到命中页、也画不出高亮。索引阶段本来就 OCR 过，
# 但那是「整篇正文」，没留页号；这里给**详情栏**单独做一份按页缓存的 OCR：
# 只对用户真正点开的那一份跑、后台单线程、优先级 BELOW_NORMAL、每页落盘，
# 找命中页和画黄框都读这份缓存。
OCR_DPI = 150.0          # 定位/画框够用；180 会更准但每页多花 200~300ms
OCR_JOB_BUDGET = 75.0    # 单份最多烧 75 秒 OCR，超了就先把已识别的交出去
_OCR_MAP = {}            # key -> {'pages': {页号: {'lines': [...]}}, 'total': n, 'done': bool}
_OCR_JOBS = {}           # key -> Thread
_OCR_TARGET = {}         # key -> (tokens, ctx)
_OCR_ACTIVE = {'key': '', 'ts': 0.0}
_OCR_LOCK = threading.Lock()


def _ocr_side(path):
    try:
        st = os.stat(path)
    except OSError:
        return None, None
    k = hashlib.sha1(('%s|%d|%d' % (os.path.normcase(str(path)), int(st.st_mtime),
                                     st.st_size)).encode()).hexdigest()
    return k, os.path.join(app_dir(), 'preview_cache', k + '.ocr.json')


def _ocr_load(path):
    k, fp = _ocr_side(path)
    if not k:
        return None
    with _OCR_LOCK:
        m = _OCR_MAP.get(k)
    if m is not None:
        return m
    m = {'pages': {}, 'total': 0, 'done': False}
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            m.update(json.load(f) or {})
    except Exception:
        pass
    with _OCR_LOCK:
        _OCR_MAP[k] = m
    return m


def _ocr_save(k, m):
    try:
        fp = os.path.join(app_dir(), 'preview_cache', k + '.ocr.json')
        tmp = fp + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(m, f, ensure_ascii=False)
        os.replace(tmp, fp)
    except Exception:
        pass


def _ocr_norm(lines):
    return re.sub(r'\s+', '', ' '.join(str(l[4]) for l in lines)).lower()


def _ocr_map_search(m, tokens, ctx=''):
    """已识别页里找命中页（1 基）。有片段就先按片段找（哪怕后面的页也有词），
    片段找不到才退回按词。"""
    toks = [str(t).lower() for t in tokens or []]
    if not m or not m.get('pages'):
        return 0
    pages = sorted(m['pages'], key=lambda s: int(s))
    if ctx:
        for p in pages:
            norm = _ocr_norm((m['pages'][p] or {}).get('lines') or [])
            if norm and ctx.lower() in norm:
                return int(p)
    if toks:
        for p in pages:
            norm = _ocr_norm((m['pages'][p] or {}).get('lines') or [])
            if norm and any(t in norm for t in toks):
                return int(p)
    return 0


def _lower_current_thread():
    try:
        k32 = ctypes.windll.kernel32
        k32.SetThreadPriority(k32.GetCurrentThread(), -1)   # BELOW_NORMAL
    except Exception:
        pass


def _ocr_run(path, k):
    """后台逐页 OCR（只跑用户当前点开的那一份）。"""
    _lower_current_thread()
    m = _ocr_load(path) or {'pages': {}, 'total': 0, 'done': False}
    t0 = time.time()
    try:
        import pdf_engine as pymupdf
        with pymupdf.open(str(path)) as doc:
            n = len(doc)
            m['total'] = n
            ocr = _get_ocr()
            for i in range(n):
                with _OCR_LOCK:
                    if _OCR_ACTIVE.get('key') not in ('', k):
                        break                    # 用户已经去看别的文件了
                    toks, ctx = _OCR_TARGET.get(k, ([], ''))
                if pymupdf and _ocr_map_search(m, toks, ctx):
                    break                        # 已经找到，没必要继续烧 CPU
                if time.time() - t0 > OCR_JOB_BUDGET:
                    break
                p = i + 1
                if p in m['pages']:
                    continue
                try:
                    page = doc[i]
                    r = page.rect
                    z = min(OCR_DPI / 72.0, 2200.0 / max(r.width, r.height, 1))
                    if z < 0.05:
                        continue
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
                    res, _e = ocr(pix.tobytes('png'))
                    w = float(pix.width) or 1.0
                    h = float(pix.height) or 1.0
                    pix = None
                    lines = []
                    for box, txt, _score in (res or []):
                        txt = str(txt or '').strip()
                        if not box or not txt:
                            continue
                        xs = [pt[0] for pt in box]
                        ys = [pt[1] for pt in box]
                        lines.append([min(xs) / w, min(ys) / h,
                                      max(xs) / w, max(ys) / h, txt])
                    m['pages'][str(p)] = {'lines': lines[:200]}
                    _ocr_save(k, m)
                except Exception:
                    continue
            m['done'] = len(m['pages']) >= (m.get('total') or 0)
            _ocr_save(k, m)
    except Exception:
        try:
            m['done'] = True
            _ocr_save(k, m)
        except Exception:
            pass
    finally:
        with _OCR_LOCK:
            _OCR_JOBS.pop(k, None)


def ocr_hit_page(path, tokens, ctx=''):
    """扫描件的命中页：(页号, 已识别页数, 总页数, 是否还在跑)。没找到就先在后台排上。"""
    k, _fp = _ocr_side(path)
    if not k:
        return 0, 0, 0, False
    m = _ocr_load(path) or {'pages': {}, 'total': 0, 'done': False}
    hit = _ocr_map_search(m, tokens, ctx)
    done = len(m.get('pages') or {})
    total = int(m.get('total') or 0)
    if hit or m.get('done'):
        return hit, done, total, False
    with _OCR_LOCK:
        _OCR_ACTIVE['key'] = k
        _OCR_ACTIVE['ts'] = time.time()
        _OCR_TARGET[k] = ([str(t).lower() for t in tokens or []], str(ctx or ''))
        th = _OCR_JOBS.get(k)
        if th is None:
            th = threading.Thread(target=_ocr_run, args=(path, k), daemon=True,
                                  name='ocr-%s' % k[:6])
            _OCR_JOBS[k] = th
            th.start()
    return 0, done, total, True


def _ocr_line_rects(path, page, tokens):
    """某一页里命中词的位置（归一化 0~1），给页图画黄框用。

    按「行文本框 + 字序比例」切：OCR 给的是整行框，直接整行涂黄会把一行都糊掉。
    """
    k, _fp = _ocr_side(path)
    if not k or not tokens:
        return []
    with _OCR_LOCK:
        m = _OCR_MAP.get(k)
    if m is None:
        m = _ocr_load(path)
    pg = ((m or {}).get('pages') or {}).get(str(page))
    if not pg:
        return []
    out = []
    for x0, y0, x1, y1, txt in (pg.get('lines') or []):
        s = str(txt)
        low = s.lower()
        n = len(s) or 1
        for tok in tokens:
            tok = str(tok).lower()
            if not tok:
                continue
            i = low.find(tok)
            while i >= 0 and len(out) < 200:
                a = x0 + (x1 - x0) * (i / n)
                b = x0 + (x1 - x0) * ((i + len(tok)) / n)
                out.append((a, y0, b, y1))
                i = low.find(tok, i + len(tok))
    return out


def make_thumb(path, max_w=320, max_h=400, page=0, q='', hv=''):

    """图片/PDF → 缩略图 PNG 字节。失败返回 None，前端回落到系统图标。



    实现务实：

    - 图片：PIL 打开 → 等比缩放（最大 320×400）→ PNG 字节

    - PDF：pymupdf 取首页 → PIL 缩放

    - Word/Excel/PPT：**不出图**（第 313 项回滚：Shell 缩略图会卡死 HTTP 线程；详情走正文首段）

    - 视频等其它：交给前端用 /api/icon 显示系统大图标"""

    if not path or not _local_only(path) or not os.path.exists(path):

        return None

    try:

        st = os.stat(path)

        stamp = '%d:%d' % (int(st.st_mtime), st.st_size)

    except OSError:

        stamp = 'na'

    # 第 325 项：PDF 详情要能翻页、能把关键词在页面上标出来。
    # 缓存键要带上页码与高亮词：只按尺寸缓存的话，翻到第 12 页拿到的还是首页。
    # 第 397 项：多关键词 —— 以前直接按空格切、连同 `ext:pdf` 这种语法词一起丢给
    # `search_for`（白搜一遍还占名额）。改用 doc_render 的同一套分词（去语法、去 -排除），
    # 最多 8 个词；每个词各自在页面上找，前端一次查询里有几个词就标几处。
    _hl = doc_render._q_tokens(q)[:8]
    _hl = [t.lower() for t in _hl]
    k = os.path.normcase(path) + '|%s|%dx%d|p%d|%s|%s' % (
        stamp, max_w, max_h, int(page or 0), ','.join(_hl), hv or '')

    with THUMB_LOCK:

        if k in THUMB_CACHE:

            return THUMB_CACHE[k]

        fail_at = THUMB_FAIL.get(k)

        if fail_at and time.time() - fail_at < 300:

            return None

    # 第 326 项：磁盘缓存以前只存「首页、无高亮」—— 翻页 / 高亮图每次都要重渲染
    # （40 篇详情图压力测试里，最慢那张 2.5 秒，平均 100ms）。缓存键带上页码和高亮词后，
    # 每种变体都能落盘，翻回上一页就是读文件。
    use_disk = True

    disk = _thumb_disk_path(path, max_w, max_h, page, _hl, hv)

    hit = _thumb_from_disk(disk) if use_disk else None

    if hit:

        with THUMB_LOCK:

            if len(THUMB_CACHE) > 100:

                THUMB_CACHE.pop(next(iter(THUMB_CACHE)))

            THUMB_CACHE[k] = hit

        return hit

    ext = os.path.splitext(path)[1].lower()

    # 第 385 项：Word/Excel/PPT 借 Office/WPS 先转 PDF，再走下面同一个 PDF 分支出页图。
    # 转不出来（没装 Office/WPS、或者转换超时）就记一次失败冷却，前端会回落到纯文本。
    if doc_render.is_office(path):

        # 第 390 项：缓存里可能只有「命中页 ±1」那一段，必须同时拿到页下标，
        # 否则从另一个页码的 PDF 里取第 1 页，渲出来是别的页（用户就会觉得跳错页）。
        # 第 392 项：网格格子（max_w<600）只准读缓存 —— 一屏 20 个 docx 就是 20 次重排版，
        # 而网格只值一张 32px 小图；详情栏（big=1 → 900/1600）才允许排队转换。
        try:
            _mw = int(max_w)
        except Exception:
            _mw = 320
        if _mw < 600:
            _pdf, _idx = doc_render.pdf_page_cached(path, app_dir(), page, q)
        else:
            _pdf, _idx = doc_render.pdf_page_source(path, app_dir(), page, q)

        if not _pdf:

            # 第 387 项：Office 的「转不出来」统一由 doc_render 管冷却（60s，且每次
            # 都会换干净实例重试）。这里**不许**再写 THUMB_FAIL —— 那张表是 300s，
            # 会把排队 / 偶发 RPC 失败的文件按住 5 分钟，用户看到「图一直不出来」。
            return None

        path = _pdf
        page = _idx

        ext = '.pdf'

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

                    # 第 336 项：详情栏要「看得清」，别永远按 256 取缩略图 ——
                    # 网格格子走默认 256，详情 big=1 要 640，展开 xl 要 960。
                    box['png'] = _shell_thumb_png(path, size=_thumb_video_size(max_w))

                except Exception:

                    box['png'] = None



            th = threading.Thread(target=_run, daemon=True)

            th.start()

            th.join(VIDEO_THUMB_TIMEOUT)

            png = box.get('png')

        elif ext == '.pdf':

            import pdf_engine as pymupdf

            import io

            from PIL import Image

            with pymupdf.open(path) as doc:

                if len(doc):

                    # 第 325 项：页码按 0 基传进来，越界一律夹到最后一页 ——
                    # 前端翻到 999 页时宁可重复最后一页，也别回 204 让面板空掉。
                    pg = max(0, min(int(page or 0), len(doc) - 1))

                    page = doc[pg]

                    # 算缩放系数：原始页面宽 → max_w

                    zw = max_w / max(page.rect.width, 1)

                    # 窄长页（比如 320×320000 的整页截图型 PDF）按宽度缩放会把 pixmap

                    # 撑到几百 MB；再按「最长边 2200」压一次，和 ocr_pdf 同一个口径

                    zw = min(zw, 2200.0 / max(page.rect.width, page.rect.height, 1))

                    pix = page.get_pixmap(matrix=pymupdf.Matrix(zw, zw), alpha=False)

                    with Image.frombytes('RGB' if pix.n < 4 else 'RGBA',

                                         (pix.width, pix.height), pix.samples) as im0:

                        im = im0.convert('RGB')

                        # 第 326 项：pixmap 的原始缓冲区（1600×2200 ≈ 10MB）用完立刻放掉，
                        # 不然 40 张图连着请求时，内存峰值能到 576MB。
                        pix = None

                        # 第 325 项：把命中的词画成黄色高亮（gety 同款）。pymupdf 的
                        # search_for 给的是 PDF 点坐标，乘上同一个缩放系数才落得准。
                        # 只标前 5 个词、每词最多 60 处：一份 36 页的财报正文里
                        # 「报告」能出现上千次，全画完既慢又糊成一片。
                        if _hl:

                            try:

                                from PIL import ImageDraw

                                overlay = Image.new('RGBA', im.size, (0, 0, 0, 0))

                                dr = ImageDraw.Draw(overlay)

                                marked = False

                                for _t in _hl:

                                    try:

                                        rects = page.search_for(_t)
                                    except Exception:

                                        rects = []

                                    for _r in rects[:60]:

                                        dr.rectangle([_r.x0 * zw, _r.y0 * zw, _r.x1 * zw, _r.y1 * zw],

                                                     fill=(255, 214, 0, 120))

                                        marked = True

                                # 第 399 项：扫描件没有文本层，search_for 一个框都拿不到 ——
                                # 读后台刚 OCR 出来的行框（归一化坐标），按字序切出命中的那一截。
                                if not marked and _hl:
                                    try:
                                        W, Hh = im.size
                                        for _a, _b, _c, _d in _ocr_line_rects(
                                                str(path), int(pg) + 1, _hl)[:200]:
                                            dr.rectangle([_a * W, _b * Hh, _c * W, _d * Hh],
                                                         fill=(255, 214, 0, 120))
                                            marked = True
                                    except Exception:
                                        pass

                                if marked:

                                    im = Image.alpha_composite(im.convert('RGBA'), overlay).convert('RGB')

                            except Exception:

                                pass

                        im.thumbnail((max_w, max_h))

                        buf = io.BytesIO()

                        im.save(buf, format='PNG')

                        png = buf.getvalue()

    except Exception:

        # 第 403 项排查：缩略图失败以前是全静默的（前端只看到 204），
        # 结果「详情栏一片空白」只能靠猜。把前 50 条的 traceback 落进 debug.log。
        try:
            if _THUMB_ERR['n'] < 50:
                _THUMB_ERR['n'] += 1
                import traceback
                _log_file('thumb fail %s\n%s' % (path, traceback.format_exc()[:800]))
        except Exception:
            pass

        png = None

    if png:

        with THUMB_LOCK:

            if len(THUMB_CACHE) > 100:

                THUMB_CACHE.pop(next(iter(THUMB_CACHE)))

            THUMB_CACHE[k] = png

            THUMB_FAIL.pop(k, None)

        if use_disk:

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



def _qs_items():

    """第 194 项：快搜窗「当前打开的文件夹」的数据源。

    先列资源管理器正在看的目录（智能推荐），再用最近打开过的目录补位。

    不扫描磁盘、不读浏览器历史，拿不到就返回空列表。"""

    items, seen = [], set()

    try:

        folders = quick_switch.open_folders()

    except Exception:

        folders = []

    for p in folders:

        k = os.path.normcase(p)

        if k in seen:

            continue

        seen.add(k)

        items.append({'p': p, 'n': os.path.basename(p.rstrip('\\/')) or p, 'k': 'open'})

    try:

        with con_lock:

            rows = recent_rows(conn(), 8)

    except Exception:

        rows = []

    for r in rows:

        if r.get('t') != 'dir':

            continue

        p = str(r.get('p') or '')

        k = os.path.normcase(p)

        if not p or k in seen:

            continue

        seen.add(k)

        if not os.path.isdir(p):

            continue          # 目录都删了就别再推荐（第 199 项顺手补的）

        items.append({'p': p, 'n': r.get('n') or os.path.basename(p) or p, 'k': 'recent'})

        if len(items) >= 7:

            break

    return items





def _api_open_dirs():

    """GET /api/open_dirs：候选目录 + 当前有没有可用的文件对话框。"""

    items = _qs_items()

    try:

        allow_open([x['p'] for x in items])

    except Exception:

        pass

    pr = quick_switch.probe()

    return {'items': items, 'dialog': bool(pr.get('dialog')),

            'title': pr.get('title', ''), 'probe': pr.get('ok', False)}





def _api_dialog_jump(req):

    """POST /api/dialog_jump：把目录送进正在使用的打开/保存对话框。

    白名单：目录必须存在，且是「当前打开的文件夹 / 最近打开目录 / 刚搜出来的结果」。

    绝不接受页面随手拼的一个路径。"""

    p = str(req.get('p') or '')

    target = quick_switch.jump_target(p)

    if not target:

        return {'ok': False, 'reason': 'bad_path'}

    allow = set()

    try:

        allow = {os.path.normcase(x['p']) for x in _qs_items()}

    except Exception:

        allow = set()

    if (os.path.normcase(target) not in allow

            and not ev_recent(p) and not ev_recent(target)):

        return {'ok': False, 'reason': 'not_allowed'}

    r = quick_switch.jump(target)

    if r.get('ok'):

        # 第 196 项：跳成功就记一次「最近访问」，下次推荐自动把它排前面。

        try:

            with con_lock:

                record_open(conn(), target, True)

        except Exception:

            pass

    return r

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

# 第 236 项「范围对账」的保护阈值。和上面两个故意不同：改一次排除规则后，
# 一次清掉四成条目本来就是预期结果（实测 AppData 就占 31%），
# 用 50 条当上限会让这个功能永远不敢动。而且对账只删索引记录、不动文件，
# 最坏情况是重建一次索引，所以比例闸放在 60%，只挡「规则写反了」这种灾难。

RECONCILE_BULK_LIMIT = 180000

RECONCILE_RATIO = 0.6



def _scope_dirs(scope_path=None):

    """读索引范围，返回 [(原始写法, normcase 后的比较用写法)]。

    只给「范围体检 / prune 保护」用；读不到就返回空表，调用方退回盘根判断。"""

    try:

        sp = Path(scope_path) if scope_path else SCOPE_FILE

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





def snip_translate(text, tgt_lang='auto'):
    """第 361 项：截图翻译。把 OCR 文本送 translator 翻一遍，取首选结果。

    只在服务端串一次（取字 → 段落无意义、这里已经是整段文本 → 翻译），
    前端拿到 {'ok','src','text','tgt_lang'} 就能切原文/译文。
    翻译一定出网（走用户勾选的引擎），取字仍在本机 RapidOCR。
    """
    text = str(text or '').strip()
    if not text:
        try:
            import snip_ocr
            text = str(snip_ocr.get_snip_result().get('text') or '').strip()
        except Exception:
            text = ''
    if not text:
        return {'ok': False, 'src': '', 'text': '', 'why': 'empty', 'results': []}
    try:
        import translator
        res = translator.translate_text(text, src_lang='auto',
                                        tgt_lang=(tgt_lang or 'auto'))
    except Exception as e:
        return {'ok': False, 'src': text, 'text': '', 'why': str(e)[:120], 'results': []}

    best, eng = '', ''
    for r in (res.get('results') or []):
        t = (r.get('text') or '').strip() if isinstance(r, dict) else ''
        if t:
            best, eng = t, (r.get('engine') or '')
            break
    return {'ok': bool(best), 'src': text, 'text': best, 'engine': eng,
            'src_lang': res.get('src_lang'), 'tgt_lang': res.get('tgt_lang'),
            'why': '' if best else 'noresult',
            'results': (res.get('results') or [])[:4]}


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





# ── 主题配色 / 全局壁纸（第 191 项）──────────────────────────────────────

# 配色 id 必须和前端 web/src/lib/palette.ts 的 PALETTES 一一对应：

# 前端认 localStorage，后端这份只用来**校验**别写进奇怪的值，preflight 会逐项比对。

PALETTE_IDS = ('auto', 'light', 'dark', 'paper', 'amber', 'pure', 'mint', 'blue',

               'lilac', 'sakura', 'graphite', 'night', 'contrast', 'black', 'cyber')

# 网页把「整套配色」报上来（第 369 项）：原生窗口（截图翻译卡片等）直接用这份**具体值**，
# 不必再拿 id 去反查 —— 用户自定义变量、auto 跟随系统深浅，光有 id 都表达不出来。
_PALETTE_VAR_KEYS = ('bg', 'card', 'ink', 'sub', 'line', 'accent')

def _clean_palette_vars(raw):

    """只收 6 个键的 "r g b" 三元组；缺键、多值、越界一律整包拒掉（宁可不写，也别写脏）。"""

    if not isinstance(raw, dict):

        return None

    out = {}

    for k in _PALETTE_VAR_KEYS:

        v = raw.get(k)

        if not isinstance(v, str):

            return None

        parts = v.replace(',', ' ').split()

        if len(parts) != 3:

            return None

        try:

            nums = [int(p) for p in parts]

        except (TypeError, ValueError):

            return None

        if any(n < 0 or n > 255 for n in nums):

            return None

        out[k] = '%d %d %d' % (nums[0], nums[1], nums[2])

    return out

# 壁纸只认这几种位图：故意不收 svg（能带脚本）、不收 ico（多尺寸糊成一团）

WALL_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif')

WALL_MAX_BYTES = 40 * 1024 * 1024

WALL_MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',

             '.webp': 'image/webp', '.bmp': 'image/bmp', '.gif': 'image/gif'}

def _wall_path():

    """settings.json 里记的那张图。**只认这里** —— 接口不接受前端传路径来读文件。"""

    try:

        return str(_settings_read().get('wall') or '')

    except Exception:

        return ''

def _wall_ok(p=None):

    p = _wall_path() if p is None else p

    try:

        return (bool(p) and os.path.isfile(p)

                and os.path.splitext(p)[1].lower() in WALL_EXT

                and os.path.getsize(p) <= WALL_MAX_BYTES)

    except Exception:

        return False

def _wall_int(v, lo, hi, dflt):

    try:

        n = int(v)

    except Exception:

        return dflt

    return max(lo, min(hi, n))

def _wall_state():

    """给界面看的状态。图片读不出来时 on=False —— 界面照常可用，不会白屏。"""

    try:

        d = _settings_read()

    except Exception:

        d = {}

    p = str(d.get('wall') or '')

    ok = _wall_ok(p)

    st = {'on': ok,

          'name': os.path.basename(p) if ok else '',

          'opacity': _wall_int(d.get('wall_opacity', 15), 5, 100, 15),

          'blur': _wall_int(d.get('wall_blur', 0), 0, 30, 0),
          # 第 362 项：人像被 cover 裁掉脑袋 —— 焦点可拖、可放大。
          # 位置用百分比（50/50 = 居中），与 CSS object-position 同一套语义。
          'pos_x': _wall_int(d.get('wall_pos_x', 50), 0, 100, 50),
          'pos_y': _wall_int(d.get('wall_pos_y', 50), 0, 100, 50),
          'zoom': _wall_int(d.get('wall_zoom', 100), 100, 200, 100),
          # 生效范围：哪几个界面铺壁纸。默认全开 = 老行为不变。
          'scope_main': bool(d.get('wall_scope_main', True)),
          'scope_settings': bool(d.get('wall_scope_settings', True)),
          'scope_mini': bool(d.get('wall_scope_mini', True)),

          'v': 0}

    if ok:

        try:

            st['v'] = int(os.path.getmtime(p))

        except Exception:

            pass

    elif p:

        st['err'] = 'wall_missing'      # 文件被删/挪走：界面提示换一张

    return st

def _wall_image():

    """返回 (bytes, content-type)。路径只来自配置，前端拿不到「随便读文件」的能力。"""

    p = _wall_path()

    if not _wall_ok(p):

        return (json.dumps({'ok': False, 'err': 'wall_missing'},

                           ensure_ascii=False).encode(),

                'application/json; charset=utf-8')

    b = b''

    try:

        with open(p, 'rb') as f:

            b = f.read(WALL_MAX_BYTES + 1)

    except Exception:

        b = b''

    if not b or len(b) > WALL_MAX_BYTES:

        return (json.dumps({'ok': False, 'err': 'wall_unreadable'},

                           ensure_ascii=False).encode(),

                'application/json; charset=utf-8')

    return b, WALL_MIME.get(os.path.splitext(p)[1].lower(), 'application/octet-stream')

def _wall_apply(req):

    """壁纸的三个动作：set（选了新图）/ tune（两个滑杆）/ clear。"""

    op = str(req.get('op') or '')

    if op == 'clear':

        _settings_update({'wall': ''})

        return _wall_state()

    if op == 'set':

        p = str(req.get('path') or '')

        ext = os.path.splitext(p)[1].lower()

        st = _wall_state()

        if ext not in WALL_EXT:

            st['err'] = 'wall_bad_ext'

            return st

        try:

            size = os.path.getsize(p) if os.path.isfile(p) else -1

        except Exception:

            size = -1

        if size < 0:

            st['err'] = 'wall_missing'

            return st

        if size > WALL_MAX_BYTES:

            st['err'] = 'wall_too_big'

            return st

        _settings_update({'wall': p})

        return _wall_state()

    if op == 'tune':

        patch = {}

        if 'opacity' in req:

            patch['wall_opacity'] = _wall_int(req.get('opacity'), 5, 100, 15)

        if 'blur' in req:

            patch['wall_blur'] = _wall_int(req.get('blur'), 0, 30, 0)
        if 'pos_x' in req:
            patch['wall_pos_x'] = _wall_int(req.get('pos_x'), 0, 100, 50)
        if 'pos_y' in req:
            patch['wall_pos_y'] = _wall_int(req.get('pos_y'), 0, 100, 50)
        if 'zoom' in req:
            patch['wall_zoom'] = _wall_int(req.get('zoom'), 100, 200, 100)
        for _k, _key in (('scope_main', 'wall_scope_main'),
                         ('scope_settings', 'wall_scope_settings'),
                         ('scope_mini', 'wall_scope_mini')):
            if _k in req:
                patch[_key] = bool(req.get(_k))

        if patch:

            _settings_update(patch)

        return _wall_state()

    st = _wall_state()

    st['err'] = 'wall_bad_op'

    return st

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

            _refresh_names_from_csv(str(SCOPE_FILE))

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

                _refresh_names_from_csv(str(SCOPE_FILE))

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





def _safe_scope_dir(p, allow_unc=False):

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

        # 网络共享目录默认拒绝（SMB 协商会带出 NetNTLMv2）；只有用户在
        # 「索引范围」里主动添加时才放行，且必须真实可达、长度受限。
        if not allow_unc or len(q) > 260 or q.count('\\') < 3:

            return None

        try:

            if not os.path.isdir(q):

                return None

        except OSError:

            return None

        return q

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





def safe_scope_list(targets, allow_unc=False):

    """把界面传来的一串路径过滤成可信范围；全被滤掉时返回 None。"""

    out = []

    for t in (targets or []):

        q = _safe_scope_dir(t, allow_unc)

        if q and q not in out:

            out.append(q)

    return out or None





def is_net_path(p):

    """是不是网络位置（UNC `\\主机\共享\…` 或 `//主机/共享`）。

    第 294 项：网络位置单列一类 —— 可达性会抖、监听不可靠、SMB 协商还会带出凭据。
    """

    s = str(p or '').strip()

    # `\\?\` / `\\.\` 是 Windows 扩展长度/设备命名空间下的**本地**路径，不是共享
    if s.startswith('\\\\?\\') or s.startswith('\\\\.\\'):

        return False

    return s.startswith('\\\\') or s.startswith('//')



def scope_net_blocked(dirs, old_dirs=None):

    """这批待保存的目录里含网络位置、而本机没有 net_index 位 → 该拒绝。

    第 294 项：抽成函数只为可测 —— /api/config 只负责把 k=e_net_pro 交给界面。
    """

    def _norm(x):

        return os.path.normcase(os.path.normpath(str(x).strip()))

    old = {_norm(d) for d in (old_dirs or [])}

    new = [d for d in (dirs or []) if is_net_path(d) and _norm(d) not in old]

    if not new:

        return False

    return not license_feature('net_index')



def _path_alive(p, timeout=3.0):

    """目录可达性探测；网络路径放后台线程里等，超时即判不可达。

    为什么必须带超时：NAS 关机/断网时 os.path.isdir('\\\\nas\\share') 会走整套 SMB
    协商超时（几十秒到几分钟），一轮索引里只要有一条这样的路径，用户看到的就是
    「更新一直卡在智能扫描」。本地路径不额外开线程，直接查。
    """

    p = str(p or '').strip()

    if not p:

        return False

    if not is_net_path(p):

        try:

            return os.path.isdir(p)

        except OSError:

            return False

    box = {}

    def _probe():

        try:

            box['ok'] = os.path.isdir(p)

        except Exception:

            box['ok'] = False

    t = threading.Thread(target=_probe, daemon=True)

    t.start()

    t.join(timeout)

    return bool(box.get('ok'))



def save_scope(targets, scope_path=None):

    """把界面上的文件夹写进 scope.json，成功返回 True。

    原来这段写在 /api/update 里、外面只 print 一句：写不进去也照样起线程、照样回 ok，

    于是「新加的文件夹被静默忽略」换个入口又复发了一次。落盘失败必须让界面知道。"""

    try:

        sp = Path(scope_path) if scope_path else SCOPE_FILE

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

    sp = Path(scope_path) if scope_path else SCOPE_FILE

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

    sp = Path(scope_path) if scope_path else SCOPE_FILE

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


def path_alive(p):
    """单个路径是否还活着（虚拟应用入口按活算，挪到 _mark_gone 旁边统一用）。"""

    if not p:

        return False

    try:

        return bool(os.path.exists(p) or app_entries.is_shell_app_path(p))

    except Exception:

        return True


def filter_alive(items, budget=0.9, workers=8):
    """第 323 项：列表里的路径挨个验活，死行直接不返回。

    为什么要有：`mini_search` 只验「搜索命中」的那几十条，**浏览最近文件 / 纯语法筛选
    （ext:pdf 这类）** 两条路原来一条都不验 —— 删掉的图片还挂在列表里，用户点它
    打不开，看着就是「实时检查没做」。

    为什么限时：`os.stat` 碰上掉线的网络盘 / 云盘会卡好几秒，一次 200 条能卡几分钟。
    这里丢进程池里并发验，**最多等 budget 秒**；超时没回来的按「活着」处理（宁可多留
    一条，也不能把好文件误删）。"""
    if not items:

        return items

    from concurrent.futures import ThreadPoolExecutor, wait as _wait

    paths = [str(it.get('p') or '') for it in items]

    alive = [True] * len(paths)

    pending = [i for i, p in enumerate(paths) if p]

    if not pending:

        return items

    ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='alive')

    try:

        futs = {ex.submit(path_alive, paths[i]): i for i in pending}

        done, not_done = _wait(list(futs), timeout=budget)

        gone = []

        for f in done:

            i = futs[f]

            try:

                alive[i] = bool(f.result())

            except Exception:

                alive[i] = True

            if not alive[i]:

                gone.append(paths[i])

        for p in gone:

            _mark_gone(p)

        if not_done:

            _log_file('alive check: %d 条超时未验（按存在处理）' % len(not_done))

    except Exception as e:

        _log_file('alive check FAILED: %r' % (e,))

        return items

    finally:

        ex.shutdown(wait=False)

    return [it for i, it in enumerate(items) if alive[i]]


def _reconcile_gone(batch=400, workers=8, pause=0.15):
    """第 323 项：后台把库里已经不存在的路径清掉（一次一小批，跑完就歇）。

    这是「实时检查」的兜底：ReadDirectoryChangesW 漏事件、盘符变了、索引范围被挪走，
    都会让 files 表留下死路径。这里按批验活，死行从库里删掉，列表自然就干净了。

    三道保护（和 prune 一个口径）：
    ① 整盘不可达 → 那批整体跳过，不删（拔了移动硬盘不等于文件被删）；
    ② 一批里死得多（>20%）→ 隔 0.3 秒**二次确认**，两次都死的才删（不是整轮放弃）；
    ③ 全量任务在跑时整体让路。"""
    try:

        con = sqlite3.connect(DB, check_same_thread=False)

        con.execute('PRAGMA busy_timeout=3000')

    except Exception as e:

        _log_file('gone reconcile: open FAILED %r' % (e,))

        return 0

    checked = deleted = skipped = 0

    from concurrent.futures import ThreadPoolExecutor

    ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='reconcile')

    try:

        rows = [r[0] for r in con.execute('SELECT path FROM files')]

        for i in range(0, len(rows), batch):

            if _job_busy():

                break

            chunk = rows[i:i + batch]

            roots = {}

            for p in chunk:

                root = os.path.splitdrive(p)[0] + '\\'

                if root not in roots:

                    try:

                        roots[root] = os.path.exists(root)

                    except Exception:

                        roots[root] = False

            cand = [p for p in chunk if roots.get(os.path.splitdrive(p)[0] + '\\', True)]

            skipped += len(chunk) - len(cand)

            if not cand:

                continue

            futs = {ex.submit(path_alive, p): p for p in cand}

            dead = []

            for f, p in futs.items():

                try:

                    if not f.result():

                        dead.append(p)

                except Exception:

                    pass

            checked += len(cand)

            # 第 323 项 A：一批里死的太多不再「整体放弃」。
            # 原来的写法是一批死 >20% 就跳出循环 —— 12:12 实测第一轮就撞上
            # 「132/400」，整轮只清了 122 条就停了，用户看到的就是「死行还在」。
            # 死得多往往只是某个临时目录被整棵删掉（微信缓存、Temp），完全正常。
            # 现在改成**二次确认**：死得多的批次隔 0.3 秒再验一遍，两次都死的才删。
            # 真正「盘拔了」的情况由上面的整盘不可达判定挡住，不靠这个比例。
            if dead and len(dead) > 50 and len(dead) > len(cand) * 0.2:

                time.sleep(0.3)

                again = []

                for p in dead:

                    try:

                        if not path_alive(p):

                            again.append(p)

                    except Exception:

                        pass

                if len(again) != len(dead):

                    _log_file('gone reconcile: %d 条疑似失效，二次确认后剩 %d'
                              % (len(dead), len(again)))

                dead = again

            if dead:

                try:

                    with db_lock('gone:del', 10.0):

                        con.executemany('DELETE FROM files WHERE path=?', [(p,) for p in dead])

                        con.commit()

                    deleted += len(dead)

                except LockBusy:

                    break

                except sqlite3.Error as e:

                    _log_file('gone reconcile: delete FAILED %r' % (e,))

                    break

            time.sleep(pause)

    except Exception as e:

        _log_file('gone reconcile FAILED: %r' % (e,))

    finally:

        ex.shutdown(wait=False)

        try:

            con.close()

        except Exception:

            pass

    # 第 323 项：这行**无条件**记（哪怕一条没删）。不然「线程到底跑没跑」
    # 只能靠猜 —— 12:1x 那次就是 deleted=0 所以什么都没写。
    _log_file('gone reconcile: checked %d / deleted %d / root-unreachable %d%s'
              % (checked, deleted, skipped, '（有全量任务在跑，提前让路）' if _job_busy() else ''))

    return deleted


def _gone_reconcile_loop():
    """启动后先扫一遍（别和启动路径抢盘），之后每 30 分钟一次。

    如果启动后那几分钟正有全量任务在跑，就每 60 秒再看一眼，最多让路 3 次。"""

    try:

        time.sleep(40)

    except Exception:

        pass

    for _ in range(3):

        try:

            if not _job_busy():

                break

        except Exception:

            break

        try:

            time.sleep(60)

        except Exception:

            return

    while True:

        try:

            _reconcile_gone()

        except Exception as e:

            _log_file('gone reconcile loop FAILED: %r' % (e,))

        try:

            time.sleep(1800)

        except Exception:

            return





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

    # 注意：_doc_rids 返回的是「原始大小写 path → rowid」，而 _doc_del 是按
    # normcase(path) 去查的。不在这里换算就会永远错过快路径，掉进
    # `WHERE lower(path)=lower(?)` 的 FTS 全表扫 —— 实测 1,400 条从 7 分钟变成 20 分钟
    # 还没跑完（「第二大脑」那批旧正文一直留着，一半是这个原因）。

    rids = {os.path.normcase(k): v for k, v in _doc_rids(con).items()}

    # 先批量删正文、再批量删 meta。
    # 为什么不用 _doc_del 逐条删：它每篇一条 `DELETE FROM docs WHERE rowid=?`，
    # 而 FTS5 的 trigram 索引删除要重新读正文、重新分词，单独提交时实测 1.3 秒/条
    # （1,400 条 = 半小时），批量 IN 只要 0.29 秒/条。两者删的东西完全一样
    # （docs 行 + bigram 倒排），所以这里是 _doc_del 的批量版，语义不变。

    gone_rids = []

    for p in gone:

        con.execute('DELETE FROM meta WHERE path=?', (p,))

        r = rids.pop(os.path.normcase(p), None)

        if r is not None:

            gone_rids.append(r)

    for i in range(0, len(gone_rids), 200):

        chunk = gone_rids[i:i + 200]

        q = ','.join('?' * len(chunk))

        con.execute('DELETE FROM bigram WHERE rid IN (%s)' % q, chunk)

        con.execute('DELETE FROM docs WHERE rowid IN (%s)' % q, chunk)
        if doc_mode(con) == 'v2':
            con.execute('DELETE FROM doc_rec WHERE id IN (%s)' % q, chunk)

        con.commit()

        print(f'  ...清理失效条目 {min(i + 200, len(gone_rids))}/{len(gone_rids)}', flush=True)

    con.commit()

    tail = ''

    if skipped:

        tail += f'，另有 {skipped} 条所在磁盘当前不可达已跳过'

    if skipped_scope:

        tail += f'，另有 {skipped_scope} 条所在索引文件夹已失效已跳过'

    print(f'清理失效条目: {len(gone)} 条{tail}')

    return len(gone)



def reconcile_scope(dry=False, force=False, scope_path=None):

    """第 236 项：按「当前规则」对账，删掉库里不该再被索引的条目。

    为什么需要它：全量重建（build_files_index）是原子换表，天然干净；但用户平时点的是
    「更新」，走的是增量 —— 增量只在收到文件系统事件时才判断一条路径，而一个目录被
    排除、或被移出索引范围之后，它连事件都不会再产生，旧记录就永远留在库里。
    实测：正文清单里 220 条指向改名前的旧目录（磁盘上一条都不存在），
    文件名层还有一批落在 AppData / 安装目录里的资源文件。

    它和 prune() 是两件事，别混：
      · prune   = 「文件还在不在」（文件被删/被搬走）
      · 本函数  = 「按现在的规则还该不该索引」（规则变了、范围变了）

    返回 (文件名条目数, 目录条目数, 正文条目数)；dry=True 只统计不动库。"""

    con = init_db()

    exckw = _scope_excludes(scope_path)

    conf = _scope_conf(scope_path)

    inc = tuple(os.path.normcase(os.path.normpath(str(d)))

                for d in (conf.get('include_dirs') or []) if str(d).strip())

    # 程序自己的目录（数据库/清单/打包产物/备份）在循环外算一次

    self_base = os.path.normcase(str(app_dir())).rstrip(chr(92) + '/')

    def _drop_file(p, ext):

        # 第 246 项：虚拟应用入口不是磁盘路径，对账不能按文件系统规则清掉它。
        if app_entries.is_shell_app_path(p):
            return False

        # 注意：这里不调 _is_self_noise() —— 它内部每次都算一遍 app_dir()，
        # 30 万条路径就是 30 万次路径解析，实测把对账拖到 12 分钟还没跑完。
        # 下面把根目录在循环外算好，判定逻辑与 _is_self_noise 完全一致。

        q = os.path.normcase(str(p))

        if q.startswith(self_base):

            rel = q[len(self_base):].lstrip(chr(92) + '/')

            if rel and (rel.split(chr(92))[0].split('/')[0] in _SELF_DIRS

                        or rel.split(chr(92))[-1].split('/')[-1] in _SELF_NAMES):

                return True

        if _excluded(p, exckw):

            return True

        if scan_docs.is_install_root(p) and ext not in scan_docs.APP_ONLY_EXTS:

            return True

        if _soft_dir_file(p, ext):

            return True

        # 第 243 项：短音频 / 音乐平台目录 / 不索引标记文件。
        # 对账必须与全量扫描同一套判据，否则「重建时干净了、用两天又长回来」。
        if _media_noise_file(p, ext):

            return True

        if _noindex_marked(p):

            return True

        return False

    def _drop_doc(p):

        pl = os.path.normcase(p)

        if _drop_file(p, os.path.splitext(p)[1].lower()):

            return True

        if any(s in pl for s in _BODY_SKIP_SEGS_NC):

            return True

        # 正文是按 include 白名单索引的，不在任何一个范围里的条目 = 移出范围没清

        if inc and not any(pl.startswith(d) for d in inc):

            return True

        return False

    files_drop = [p for p, e in con.execute('SELECT path, ext FROM files')

                  if _drop_file(p, (e or '').lower())]

    dirs_drop = [p for (p,) in con.execute('SELECT path FROM dirs')

                 if _drop_file(p, '')]

    meta_drop = [p for (p,) in con.execute('SELECT path FROM meta') if _drop_doc(p)]

    n_files = con.execute('SELECT COUNT(*) FROM files').fetchone()[0]

    print(f'[对账] 文件名 {len(files_drop)}/{n_files} 条、目录 {len(dirs_drop)} 条、'
          f'正文 {len(meta_drop)} 条 按当前规则不该在库里', flush=True)

    if dry:

        return (len(files_drop), len(dirs_drop), len(meta_drop))

    # 保护：规则写错（比如把某个盘整个误判成排除）时，一次删太多 —— 停下，不删。
    # 注意阈值不能照抄 prune 的 50 条：改规则后一次清几万条本来就是预期的。

    if not force and len(files_drop) > max(RECONCILE_BULK_LIMIT,

                                           int(n_files * RECONCILE_RATIO)):

        # 注意：这里**不能**把 PROG 置成 error —— 对账是 update 流程的收尾步骤，
        # 置 error 会把整次「更新」标成失败，用户看到的是「更新出错」而不是
        # 「有一批旧条目没敢清」。只用进度文案提示，流程照常往下走。

        msg = (f'按当前规则这次要清掉 {len(files_drop)} 条文件名（库内共 {n_files} 条），'
               f'超过安全比例，这次没清。请确认索引范围与排除规则没写错，'
               f'再从命令行跑一次对账（加 --force）。')

        print('[!] ' + msg, flush=True)

        _prog(msg=msg)

        return 0

    # 正文要直接判 docs 表，不能只认 meta：meta 可能已经被上一轮清过（用户点了更新 /
    # 中途中断），那时 meta 里已经没有这条路径，再靠它去定位就永远删不掉正文行。
    # _doc_rids 返回的键是原始写法（不是 normcase），所以用 path 精确取。

    docs_drop = [p for (p,) in con.execute('SELECT path FROM docs') if _drop_doc(p)]

    rids = {os.path.normcase(k): v for k, v in _doc_rids(con).items()}

    drop_rids, seen_rid = [], set()

    for p in docs_drop + meta_drop:

        r = rids.get(os.path.normcase(p))

        if r is not None and r not in seen_rid:

            seen_rid.add(r)

            drop_rids.append(r)

    # 批量删，不逐条调 _doc_del：后者每篇都要 DELETE FROM bigram WHERE rid=?，
    # 而 bigram 的主键是 (gram, rid)，没有 rid 索引时每次都是 490 万行全表扫 ——
    # 实测一万篇直接跑过 12 分钟。这里按 rid 分批 IN 删除，几秒结束。

    for i in range(0, len(drop_rids), 200):

        chunk = drop_rids[i:i + 200]

        q = ','.join('?' * len(chunk))

        con.execute('DELETE FROM bigram WHERE rid IN (%s)' % q, chunk)

        con.execute('DELETE FROM docs WHERE rowid IN (%s)' % q, chunk)
        if doc_mode(con) == 'v2':
            con.execute('DELETE FROM doc_rec WHERE id IN (%s)' % q, chunk)

        con.commit()

        print('  ...正文 %d/%d' % (min(i + 200, len(drop_rids)), len(drop_rids)), flush=True)

    if meta_drop:

        con.executemany('DELETE FROM meta WHERE path=?', [(p,) for p in meta_drop])

    # 分批提交：一次性删 12 万条会把 WAL 顶到几百 MB 且中途无法 checkpoint，
    # 分批后每 2000 条落一次，进度可见、崩了也不至于全回滚。

    for i in range(0, len(files_drop), 2000):

        chunk = [(p,) for p in files_drop[i:i + 2000]]

        con.executemany('DELETE FROM files WHERE path=?', chunk)

        con.executemany('DELETE FROM appdesc WHERE path=?', chunk)

        con.commit()

        print('  ...文件名 %d/%d' % (min(i + 2000, len(files_drop)), len(files_drop)), flush=True)

    for i in range(0, len(dirs_drop), 2000):

        con.executemany('DELETE FROM dirs WHERE path=?',

                        [(p,) for p in dirs_drop[i:i + 2000]])

        con.commit()

    con.commit()

    print(f'[对账] 已清理：文件名 {len(files_drop)} 条、目录 {len(dirs_drop)} 条、'
          f'正文 {len(meta_drop)} 条', flush=True)

    return (len(files_drop), len(dirs_drop), len(meta_drop))


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

        sp = Path(scope_path) if scope_path else SCOPE_FILE

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

        scope = Path(scope_path) if scope_path else SCOPE_FILE

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

        # 第 236 项：范围对账。prune 只管「文件还在不在」，这里管「按现在的规则
        # 还该不该索引」—— 一个目录被排除、或被移出索引范围之后，它连文件系统
        # 事件都不会再产生，旧记录只靠 prune 永远清不掉。

        _prog(phase='prune', msg='按当前规则对账')

        try:

            reconcile_scope()

        except Exception as _e:

            print('[!] 范围对账没跑完（不影响本次更新的其他部分）：', _e, flush=True)

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

        try:

            if metrics is not None:

                metrics.add('index', 'ok', sub='update')

        except Exception:

            pass

        _prog(state='done', phase='', msg='更新完成', k='m_update_done', need_names=_need)

    except (Exception, SystemExit) as e:

        # index() 遇到坏配置是 sys.exit(1)；它跑在 /api/update 起的线程里，

        # 不接住就会带着 state='busy' 一起死，界面从此永久"正在更新"。

        _prog(state='error', err=f'{type(e).__name__}: {e}')

        traceback.print_exc()



_con = None

con_lock = threading.RLock()


class LockBusy(Exception):

    """等 con_lock 太久：有长任务（重扫 / 校验 / 清理）把库占住了。

    第 320 项：以前 HTTP 线程就在这儿一直等，一个卡住的长任务能把整个界面
    拖到 90 秒以上全无响应（11-19 10:5x 实测：所有 /api 超时，只有不走这把
    锁的 /api/ping 还是毫秒级）。现在等锁超过 timeout 就放弃，接口快点回
    「索引在忙」，同时把现场写一行 debug.log —— 先留证据，再谈根治。"""


class _DbLock(object):

    """带超时与慢日志的 con_lock。用法：with db_lock('/api/files', 20): ..."""

    __slots__ = ('where', 'timeout', 't0')

    def __init__(self, where='', timeout=20.0):

        self.where = where

        self.timeout = timeout

        self.t0 = 0.0

    def __enter__(self):

        self.t0 = time.time()

        if not con_lock.acquire(timeout=self.timeout):

            try:

                _log_file('DB_LOCK_BUSY %s 等锁超过 %.0fs（有长任务占着库）'
                          % (self.where, self.timeout))

            except Exception:

                pass

            raise LockBusy(self.where)

        waited = time.time() - self.t0

        if waited > 3:

            try:

                _log_file('DB_LOCK_SLOW %s 等锁 %.1fs' % (self.where, waited))

            except Exception:

                pass

        return self

    def __exit__(self, *a):

        con_lock.release()

        return False


def db_lock(where='', timeout=20.0):

    return _DbLock(where, timeout)


def _dump_thread_stacks(reason=''):

    """把当前所有线程的调用栈写进 debug.log。

    第 321 项：光知道「锁被占住」不够，要知道**被谁**占住。CPython 的 RLock
    不给看 owner，所以整份栈都打出来 —— 正在等锁的那几个线程栈里一定有
    `acquire`，剩下不等的那个就是攥着锁不放的人。"""

    try:

        import sys as _sys

        import traceback

        frames = _sys._current_frames()

        names = {t.ident: t.name for t in threading.enumerate()}

        out = ['=== THREAD DUMP %s (%d threads) ===' % (reason, len(frames))]

        for tid, fr in frames.items():

            out.append('--- %s tid=%s' % (names.get(tid, '?'), tid))

            out.append(''.join(traceback.format_stack(fr, limit=8)).rstrip())

        _log_file('\n'.join(out))

    except Exception:

        pass


def wal_tidy_once(min_bytes=8 * 1024 * 1024):
    """收一次 WAL。返回 (before, after)；出错返回 (-1, -1)。抽出来是为了能单测。"""
    import sqlite3

    p = Path(DB)
    wal = Path(str(p) + '-wal')
    if not wal.exists():
        return 0, 0
    before = wal.stat().st_size
    if before <= min_bytes:
        return before, before
    c = sqlite3.connect(DB, timeout=3)
    try:
        c.execute('PRAGMA busy_timeout=3000')
        busy, _log_pages, _done = c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    finally:
        c.close()
    if busy:
        return before, before
    after = wal.stat().st_size if wal.exists() else 0
    return before, after


def _wal_keeper_loop():
    """体积评估 #1：常驻期间每隔一分钟收一次 WAL。

    文档承诺「WAL 超过 8 MB 自动收」，但 autocheckpoint 只把已提交的帧并回主库，
    **不会缩小 wal 文件**；而 journal_size_limit 只在「真的发生一次 checkpoint」时
    才生效。长会话里索引/监听不断写，读者不断档，截断机会就一直没等到，
    文件停在最高水位（实测 60.9 MB）。这里定时主动 TRUNCATE：

    - busy_timeout 给 3 秒，抢不到就跳过这一轮（有长读事务时本来也不该硬截）；
    - 只在自己新开一条连接上做，不碰 HTTP 层那条共享连接，不会卡搜索；
    - 小于 8 MB 就不动，省掉无意义的 syscall。
    """
    while True:
        time.sleep(60)
        try:
            before, after = wal_tidy_once()
            if before - after > 1024 * 1024:
                _log_file('wal keeper: %.1f MB → %.1f MB'
                          % (before / 1048576, after / 1048576))
        except Exception as e:
            _log_file('wal keeper FAILED: %r' % (e,))
            time.sleep(240)   # 出错就退到 5 分钟一轮，别刷日志


def _lock_watchdog():

    """第 321 项：每 1 秒试一下 con_lock；连续 20 秒拿不到就打一份线程栈（4 分钟一次）。

    11-19 11:2x 实测：所有 /api 都 `DB_LOCK_BUSY`，可没人知道是谁攥着锁。
    这次加个看门狗，把现场抓下来。

    第 327 项：顺手看住「共享连接挂着没提交的事务」。这条比拿不到锁更阴：
    读接口全正常，只有写库全报 database is locked（实时监听丢事件、bigram 回填失败），
    用户能感觉到的就是「用着用着突然很卡」。挂了超过 30 秒就直接回滚自愈。
    """

    streak = 0.0

    last = 0.0

    tx_stuck = 0.0

    while True:

        try:

            time.sleep(1.0)

            # ---- 共享连接的事务泄漏（第 327 项）----
            # 只在「拿得到 con_lock」时才动手：拿得到说明没人在写批，这条事务是
            # 孤儿（忘 commit 留下的），回滚安全；拿不到就别碰别人的活。
            try:

                c = _con

                if c is not None and c.in_transaction:

                    tx_stuck += 1.0

                    if tx_stuck >= 30 and con_lock.acquire(timeout=0.05):

                        try:

                            _log_file('con 事务泄漏 %.0fs，回滚自愈' % tx_stuck)

                            try:

                                c.rollback()

                            except sqlite3.Error as e:

                                _log_file('rollback 失败：%r' % (e,))

                        finally:

                            con_lock.release()

                        tx_stuck = 0.0

                else:

                    tx_stuck = 0.0

            except Exception:

                tx_stuck = 0.0

            if con_lock.acquire(timeout=0.05):

                con_lock.release()

                streak = 0.0

                continue

            streak += 1.0

            now = time.time()

            if streak >= 20 and now - last >= 240:

                last = now

                # 第 366 项：整理索引库本来就独占写锁几十秒，别把它当卡死刷栈。
                if COMPACT.get('state') != 'busy':

                    _dump_thread_stacks('con_lock 被占 %.0fs' % streak)

        except Exception:

            time.sleep(1.0)


_boot_lib_paths()





def _boot_compat():

    """兼容模式（软件渲染）：用户显式打开时，强制给 WebView2 加 --disable-gpu。

    必须在 webview.start() 之前设置环境变量；自动降级逻辑在

    app_window.py 的 _apply_render_fallback()，两者叠加不冲突。

    """

    try:

        d = json.loads((app_dir() / 'settings.json').read_text(encoding='utf-8'))

        if isinstance(d, dict) and d.get('compat'):

            cur = os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS') or ''

            if '--disable-gpu' not in cur:

                os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = (cur + ' --disable-gpu').strip()

    except Exception:

        pass





def _boot_care():

    """错误日志：默认零副作用。只有用户打开开关时才会补发历史报告。"""

    try:

        error_report.bootstrap(app_dir(), APP_VERSION, context_fn=report_context)

    except Exception:

        pass

    # 第 277 项：本机功能计数与动作面包屑。这里故意不 autostart：
    # 测试 / CLI 导入 brain 时会走到这里，不能顺手起写盘线程；
    # 真正的 UI 进程在 app_window.main 里再 configure(..., autostart=True)。
    try:

        if metrics is not None:

            metrics.configure(app_dir())

        if bc is not None:

            bc.configure(app_dir(), keep=True)

    except Exception:

        pass


def report_context():

    """错误报告里的「复现必需」环境位：开关、索引规模、最近失败的功能。

    只收布尔与计数，不收任何用户内容；任何一项取不到就跳过，不能让报告本身失败。
    """

    out = {}

    try:

        st = _settings_read()

        out['settings'] = {

            'compat': bool(st.get('compat')),

            'titlebar': bool(st.get('titlebar')),

            'qs_style': str(st.get('qs_style') or '')[:12],

            'hotkey': bool(st.get('hotkey')),

            'hotkey_mode': str(st.get('hotkey_mode') or '')[:16],

            'floating_ball': bool(st.get('floating_ball')),

            'theme': str(st.get('theme') or '')[:12],

            'lang': str(st.get('lang') or '')[:8],

            'trans_engines': [str(x)[:12] for x in (st.get('trans_engines') or [])][:8],

            'byok': bool(st.get('trans_byok')),

        }

    except Exception:

        pass

    try:

        n_docs = _SUB_CACHE.get('docs')

        n_files = _SUB_CACHE.get('files')

        if n_docs is None or n_files is None:

            with con_lock:

                n_docs = _count_docs(conn())

                n_files = _count_files(conn())

        db = Path(DB)

        out['index'] = {

            'docs': int(n_docs or 0),

            'files': int(n_files or 0),

            'db_mb': int(db.stat().st_size / 1048576) if db.is_file() else 0,

            'wal_mb': int((db.with_name('brain.db-wal')).stat().st_size / 1048576)

            if db.with_name('brain.db-wal').is_file() else 0,

            'db_mtime': time.strftime('%Y-%m-%dT%H:%M:%S',

                                      time.localtime(db.stat().st_mtime)) if db.is_file() else '',

        }

    except Exception:

        pass

    try:

        import libsets

        data = libsets.load(app_dir())

        out['libsets'] = len((data or {}).get('libs') or [])

        out['libset'] = str((data or {}).get('active') or '')[:12]

    except Exception:

        pass

    try:

        if metrics is not None:

            out['metrics'] = metrics.failing(4)

    except Exception:

        pass

    return out





_boot_compat()

_boot_care()




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


# 第 327 项（性能二轮）：只读连接池。
#
# 为什么必须这么干：以前**读**也要抢 con_lock，于是「一个慢查询」就等于「整个界面卡住」——
# 13:37 的 debug.log 里 5.7s 的 `/api:body` 后面跟着 4 条 `/api/files` 等锁 5.3~5.4s，
# 界面那一屏就是空白（前端等不到结果，catch 完 setRows([])）。而 WAL 模式本来就允许
# 「一写多读」并发，读根本不需要那把锁。
#
# 现在读走自己的只读连接（mode=ro + query_only），写还是走 con_lock：
# 长任务（重扫 / 瘦身 / VACUUM）期间搜索照样是毫秒级，最坏也只是看到提交前的快照。
_RO_POOL = queue.Queue(maxsize=6)


def _open_ro():
    """开一条只读连接。busy_timeout 给足 8 秒：碰上 checkpoint 短暂排他也不会立刻报错。"""
    uri = 'file:' + str(DB).replace('\\', '/') + '?mode=ro'
    c = sqlite3.connect(uri, uri=True, timeout=8.0, check_same_thread=False)
    try:
        c.execute('PRAGMA busy_timeout=8000')       # 不写库，但别被瞬间排他打断
        c.execute('PRAGMA query_only=ON')            # 兜底：只读连接上绝不允许写
    except sqlite3.Error:
        pass
    return c


def _ro_acquire():
    try:
        return _RO_POOL.get_nowait()
    except queue.Empty:
        return _open_ro()


def _ro_release(c):
    if c is None:
        return
    try:
        _RO_POOL.put_nowait(c)
    except queue.Full:
        try:
            c.close()
        except Exception:
            pass


class _RoUse(object):
    """with ro_db('/api/files') as c: ... —— 借一条只读连接，**不碰 con_lock**。

    取不到连接（库文件不在 / schema 还没建）就退回老路径：拿 con_lock + 主连接，
    宁可慢一点，也不能因为改造本身把接口弄挂。
    """

    __slots__ = ('where', 'con', 'fb')

    def __init__(self, where=''):
        self.where = where
        self.con = None
        self.fb = None

    def __enter__(self):
        try:
            self.con = _ro_acquire()
            return self.con
        except sqlite3.Error:
            self.con = None
        except Exception:
            self.con = None
        self.fb = db_lock(self.where)
        self.fb.__enter__()
        return conn()

    def __exit__(self, *a):
        if self.con is not None:
            _ro_release(self.con)
            self.con = None
            return False
        if self.fb is not None:
            return self.fb.__exit__(*a)
        return False


def ro_db(where=''):
    return _RoUse(where)


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

# 第 203 项：网络搜索关键字。用户可在本机设置里增删改；这里只是出厂默认，
# 不删用户配置、不联网校验，只有用户主动回车才会让系统浏览器打开一个 URL。
DEFAULT_WEB_SEARCH = [
    # 第 310 项：按「国内常用 → 国外常用」分组排序（用户在快搜窗里从上往下念）：
    #   国内：Baidu / Douyin / Xiaohongshu / Bilibili
    #   国外：Google / YouTube / TikTok / Bing / Wikipedia / GitHub
    {'key': 'b', 'title': 'Baidu', 'url': 'https://www.baidu.com/s?wd={query}', 'enabled': True},
    {'key': 'dy', 'title': 'Douyin', 'url': 'https://www.douyin.com/search/{query}', 'enabled': True},
    {'key': 'xhs', 'title': 'Xiaohongshu',
     'url': 'https://www.xiaohongshu.com/search_result?keyword={query}&source=web_search_result_notes',
     'enabled': True},
    {'key': 'bi', 'title': 'Bilibili', 'url': 'https://search.bilibili.com/all?keyword={query}', 'enabled': True},
    {'key': 'g', 'title': 'Google', 'url': 'https://www.google.com/search?q={query}', 'enabled': True},
    {'key': 'yt', 'title': 'YouTube', 'url': 'https://www.youtube.com/results?search_query={query}', 'enabled': True},
    {'key': 'tt', 'title': 'TikTok', 'url': 'https://www.tiktok.com/search?q={query}', 'enabled': True},
    {'key': 'bing', 'title': 'Bing', 'url': 'https://www.bing.com/search?q={query}', 'enabled': True},
    {'key': 'wiki', 'title': 'Wikipedia', 'url': 'https://zh.wikipedia.org/w/index.php?search={query}', 'enabled': True},
    {'key': 'gh', 'title': 'GitHub', 'url': 'https://github.com/search?q={query}', 'enabled': True},
]

# 第 310 项：这批是「老配置里从来没有过」的新引擎。老用户的 settings.json 里
# 只存着他当年看到的那 7 条，不做合并就永远见不到抖音/小红书/TikTok。
NEW_WEB_KEYS = ('dy', 'xhs', 'tt')


def _normalize_web_search(v):
    """设置里存进来的 web_search 一律过这道门：只留合法的搜索引擎条目。

    - key 只能是 [a-z0-9_-] 且不带空格（「关键字 + 空格」是切换语法）
    - url 必须 http(s) 且含 {query} 占位符 —— 挡住 file:// / javascript: /
      本地回环地址，也挡住用户误填成不含查询词的固定网址
    - 条数上限 30，key 去重
    """
    out, seen = [], set()
    if not isinstance(v, list):
        return out
    for it in v[:30]:
        if not isinstance(it, dict):
            continue
        k = str(it.get('key') or '').strip().lower()
        title = str(it.get('title') or '').strip()[:40]
        url = str(it.get('url') or '').strip()[:500]
        if not re.fullmatch(r'[a-z0-9_-]{1,16}', k) or k in seen:
            continue
        if '{query}' not in url:
            continue
        try:
            pr = urllib.parse.urlparse(url.replace('{query}', 'x'))
            if pr.scheme not in ('http', 'https') or not pr.netloc:
                continue
        except Exception:
            continue
        # 第 275 项：出厂默认的中文标题改成英文 —— 阿拉伯语/印地语等界面下看不懂「百度」「哔哩哔哩」。
        # 只迁移出厂那两个字面值，用户自己起的名字（比如「百度一下」）保持原样。
        if title == '百度':
            title = 'Baidu'
        elif title == '哔哩哔哩':
            title = 'Bilibili'
        seen.add(k)
        out.append({'key': k, 'title': title or k,
                    'url': url, 'enabled': it.get('enabled') is not False})
    return out


def _web_search_effective(stored):
    """把用户存的表和新出厂默认合起来（第 310 项）。

    - 出厂形状（标题/网址都没改过，只是顺序不同）：按新的「国内 → 国外」顺序重排，
      并补上他从来没有的那三个新引擎（抖音 / 小红书 / TikTok）。
      用户删过的老引擎**不复活** —— 那是他的选择，要补让他点「恢复默认引擎」。
    - 用户自己加过引擎 / 改过标题网址：一条都不动，只把新引擎追加在后面，
      免得把他排好的顺序打乱。
    """
    norm = _normalize_web_search(stored)
    if not norm:
        return [dict(x) for x in DEFAULT_WEB_SEARCH]
    dmap = {x['key']: x for x in DEFAULT_WEB_SEARCH}
    have = {e['key']: e for e in norm}
    factory = all(
        e['key'] in dmap
        and e['title'] == dmap[e['key']]['title']
        and e['url'] == dmap[e['key']]['url']
        for e in norm
    )
    if factory:
        out = []
        for dflt in DEFAULT_WEB_SEARCH:
            cur = have.get(dflt['key'])
            if cur is not None:
                item = dict(dflt)
                item['enabled'] = cur['enabled']
                out.append(item)
            elif dflt['key'] in NEW_WEB_KEYS:
                out.append(dict(dflt))
        return out
    out = [dict(e) for e in norm]
    for dflt in DEFAULT_WEB_SEARCH:
        if dflt['key'] not in have:
            out.append(dict(dflt))
    return out


def web_search_items():
    """当前启用的引擎（读 settings.json，缺配置时用出厂默认）。"""
    try:
        raw = (_settings_read() or {}).get('web_search')
    except Exception:
        raw = None
    if not isinstance(raw, list) or not raw:
        raw = DEFAULT_WEB_SEARCH
    return [x for x in _web_search_effective(raw) if x['enabled']]


def web_search_url(key, query):
    """按 key + query 拼出最终 URL；非法/未启用/空查询一律返回 None。"""
    q = str(query or '').strip()
    if not q:
        return None
    k = str(key or '').strip().lower()
    for e in web_search_items():
        if e['key'] != k:
            continue
        url = e['url'].replace('{query}', urllib.parse.quote_plus(q))
        try:
            pr = urllib.parse.urlparse(url)
            if pr.scheme in ('http', 'https') and pr.netloc:
                return url
        except Exception:
            pass
        return None
    return None


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

for _lang, _ph in {
    'zh': '在 {t} 中搜索', 'en': 'Search {t}', 'es': 'Buscar en {t}',
    'pt': 'Pesquisar no {t}', 'hi': '{t} में खोजें',
}.items():
    MINI_L_EXTRA.setdefault(_lang, {})['web_ph'] = _ph


MINI_L = {

    'zh': {'ph': '搜索文件名、文件夹或正文内容', 'detail': '详细搜索（Ctrl+Enter）', 'search': '搜索',

           'recent': '最近打开', 'recent_hint': 'Enter 直接打开', 'fr': 'Ctrl+Enter 详细搜索',

           'sep': '以下 {n} 条命中在文档正文里', 'empty': '输入文件名或正文关键词，边打边出结果',

           'busy': '正在搜正文…', 'c_hint': 'c: 只搜正文',

           'body_btn': '正文', 'body_on': '正文搜索已开启 · 点此关闭',

           'body_off': '点此连文档正文一起搜', 'busy_all': '正在搜索…',

           'none': '没有找到 “{q}” 的文件名或正文',

           'empty_hint': '双击 Ctrl 唤出 · Esc 关闭', 'more': '展示更多 “{q}” 的搜索结果',

           'about_name': 'Memex · 本地搜索', 'about_ok': '知道了',

           'about_note': '当前为 <b>0.x 预览版</b>：功能还在打磨，未达正式标准。<br>版本号到达 <b>1.0</b> 即视为正式版。', 'ctx_open': '打开', 'ctx_folder': '打开所在文件夹', 'ctx_copy': '复制路径', 'ctx_props': '属性', 'dh': '当前打开的文件夹', 'dh_recent': '最近访问', 'dh_hint': 'Ctrl+G 跳到打开/保存对话框', 'dh_go': '跳过去', 'qs_ok': '已跳到对话框', 'qs_none': '没有正在使用的打开/保存对话框', 'qs_fail': '跳转失败', 'jm_hint': '正在另存为/打开文件 · 点结果直接选定（目录直接在对话框里打开）', 'ad_hint': '路径已填进地址栏，按回车跳过去', 'nav_hint': '已在对话框中打开该目录', 'scope': '只在「{p}」中搜索', 'scope_clear': '搜索全部位置', 'sel_n': '已选中 {n} 项', 'copied_one': '已复制路径', 'copied_n': '已复制 {n} 条路径', 'copied_dir': '已复制所在文件夹', 'copy_none': '没有可复制的路径', 'copy_fail': '复制失败', },

    'en': {'ph': 'Search file names, folders or document text', 'detail': 'Detailed search (Ctrl+Enter)', 'search': 'Search',

           'recent': 'Recently opened', 'recent_hint': 'Enter to open', 'fr': 'Ctrl+Enter detailed search',

           'sep': '{n} more matches inside document text', 'empty': 'Type a file name or a word from a document',

           'busy': 'Searching inside documents…', 'c_hint': 'c: text only',

           'body_btn': 'Text', 'body_on': 'Text search on · click to turn off',

           'body_off': 'Click to search inside documents', 'busy_all': 'Searching…',

           'none': 'Nothing found for “{q}” in names or text',

           'empty_hint': 'Double Ctrl to summon · Esc to close', 'more': 'Show more results for “{q}”',

           'about_name': 'Memex · local search', 'about_ok': 'Got it',

           'about_note': 'This is a <b>0.x preview</b>: still being polished.<br>It becomes official at <b>1.0</b>.', 'ctx_open': 'Open', 'ctx_folder': 'Open containing folder', 'ctx_copy': 'Copy path', 'ctx_props': 'Properties', 'dh': 'Open folders', 'dh_recent': 'Recent', 'dh_hint': 'Ctrl+G to jump into an Open/Save dialog', 'dh_go': 'Jump', 'qs_ok': 'Jumped into the dialog', 'qs_none': 'No Open/Save dialog is active', 'qs_fail': 'Jump failed', 'jm_hint': 'Save as / Open dialog · click a result to select it (folders open in the dialog)', 'ad_hint': 'Path is in the address bar — press Enter to jump', 'nav_hint': 'Opened that folder in the dialog', 'scope': 'Only in “{p}”', 'scope_clear': 'Search everywhere', 'sel_n': '{n} selected', 'copied_one': 'Path copied', 'copied_n': '{n} paths copied', 'copied_dir': 'Containing folder copied', 'copy_none': 'Nothing to copy', 'copy_fail': 'Copy failed', },

    'es': {'ph': 'Busca nombres de archivo, carpetas o texto', 'detail': 'Búsqueda detallada (Ctrl+Enter)', 'search': 'Buscar',

           'recent': 'Abiertos hace poco', 'recent_hint': 'Enter para abrir', 'fr': 'Ctrl+Enter búsqueda detallada',

           'sep': '{n} coincidencias más dentro del texto', 'empty': 'Escribe un nombre de archivo o una palabra del texto',

           'busy': 'Buscando dentro de los documentos…', 'c_hint': 'c: solo texto',

           'body_btn': 'Texto', 'body_on': 'Búsqueda en texto activada · clic para desactivar',

           'body_off': 'Clic para buscar dentro de los documentos', 'busy_all': 'Buscando…',

           'none': 'No se encontró “{q}” en nombres ni texto',

           'empty_hint': 'Doble Ctrl para abrir · Esc para cerrar', 'more': 'Ver más resultados de “{q}”',

           'about_name': 'Memex · búsqueda local', 'about_ok': 'Entendido',

           'about_note': 'Es una <b>vista previa 0.x</b>: aún en ajuste.<br>Pasa a oficial en la <b>1.0</b>.', 'ctx_open': 'Abrir', 'ctx_folder': 'Abrir carpeta contenedora', 'ctx_copy': 'Copiar ruta', 'ctx_props': 'Propiedades', 'dh': 'Carpetas abiertas', 'dh_recent': 'Recientes', 'dh_hint': 'Ctrl+G salta al diálogo Abrir/Guardar', 'dh_go': 'Saltar', 'qs_ok': 'Saltó al diálogo', 'qs_none': 'No hay ningún diálogo Abrir/Guardar activo', 'qs_fail': 'No se pudo saltar', 'jm_hint': 'Guardar como / Abrir diálogo · haz clic en un resultado para seleccionarlo (las carpetas se abren en el diálogo)', 'ad_hint': 'La ruta está en la barra de direcciones: pulsa Enter para saltar', 'nav_hint': 'Carpeta abierta en el diálogo', 'scope': 'Solo en «{p}»', 'scope_clear': 'Buscar en todas partes', 'sel_n': '{n} seleccionados', 'copied_one': 'Ruta copiada', 'copied_n': '{n} rutas copiadas', 'copied_dir': 'Carpeta contenedora copiada', 'copy_none': 'Nada que copiar', 'copy_fail': 'No se pudo copiar', },

    'pt': {'ph': 'Busque nomes de arquivo, pastas ou texto', 'detail': 'Busca detalhada (Ctrl+Enter)', 'search': 'Buscar',

           'recent': 'Abertos recentemente', 'recent_hint': 'Enter para abrir', 'fr': 'Ctrl+Enter busca detalhada',

           'sep': 'mais {n} resultados no texto do documento', 'empty': 'Digite um nome de arquivo ou uma palavra do texto',

           'busy': 'Buscando dentro dos documentos…', 'c_hint': 'c: só texto',

           'body_btn': 'Texto', 'body_on': 'Busca no texto ativada · clique para desativar',

           'body_off': 'Clique para buscar dentro dos documentos', 'busy_all': 'Buscando…',

           'none': 'Nada encontrado para “{q}” em nomes ou texto',

           'empty_hint': 'Ctrl duas vezes para abrir · Esc para fechar', 'more': 'Ver mais resultados de “{q}”',

           'about_name': 'Memex · busca local', 'about_ok': 'Entendi',

           'about_note': 'É uma <b>prévia 0.x</b>: ainda em ajuste.<br>Vira oficial na <b>1.0</b>.', 'ctx_open': 'Abrir', 'ctx_folder': 'Abrir pasta do arquivo', 'ctx_copy': 'Copiar caminho', 'ctx_props': 'Propriedades', 'dh': 'Pastas abertas', 'dh_recent': 'Recentes', 'dh_hint': 'Ctrl+G vai para a caixa Abrir/Salvar', 'dh_go': 'Ir', 'qs_ok': 'Foi para a caixa de diálogo', 'qs_none': 'Nenhuma caixa Abrir/Salvar ativa', 'qs_fail': 'Falha ao ir', 'jm_hint': 'Salvar como / Abrir diálogo · clique em um resultado para selecioná-lo (pastas abrem na caixa)', 'ad_hint': 'O caminho está na barra de endereço — pressione Enter para ir', 'nav_hint': 'Pasta aberta na caixa de diálogo', 'scope': 'Somente em “{p}”', 'scope_clear': 'Buscar em todos os lugares', 'sel_n': '{n} selecionados', 'copied_one': 'Caminho copiado', 'copied_n': '{n} caminhos copiados', 'copied_dir': 'Pasta contêiner copiada', 'copy_none': 'Nada para copiar', 'copy_fail': 'Falha ao copiar', },

    'hi': {'ph': 'फ़ाइल नाम, फ़ोल्डर या दस्तावेज़ टेक्स्ट खोजें', 'detail': 'विस्तृत खोज (Ctrl+Enter)', 'search': 'खोज',

           'recent': 'हाल में खोले गए', 'recent_hint': 'खोलने के लिए Enter', 'fr': 'Ctrl+Enter विस्तृत खोज',

           'sep': 'दस्तावेज़ टेक्स्ट में {n} और मिलान', 'empty': 'फ़ाइल नाम या दस्तावेज़ का शब्द लिखें',

           'busy': 'दस्तावेज़ों में खोजा जा रहा है…', 'c_hint': 'c: केवल टेक्स्ट',

           'body_btn': 'टेक्स्ट', 'body_on': 'टेक्स्ट खोज चालू · बंद करने के लिए क्लिक करें',

           'body_off': 'दस्तावेज़ों के अंदर खोजने के लिए क्लिक करें', 'busy_all': 'खोजा जा रहा है…',

           'none': '“{q}” नाम या टेक्स्ट में नहीं मिला',

           'empty_hint': 'Ctrl दो बार खोलें · Esc बंद करें', 'more': '“{q}” के और परिणाम देखें',

           'about_name': 'Memex · स्थानीय खोज', 'about_ok': 'समझ गया',

           'about_note': 'यह <b>0.x पूर्वावलोकन</b> है: अभी सुधार जारी है।<br><b>1.0</b> पर आधिकारिक होगा।', 'ctx_open': 'खोलें', 'ctx_folder': 'फ़ोल्डर खोलें', 'ctx_copy': 'पथ कॉपी करें', 'ctx_props': 'गुण', 'dh': 'खुले फ़ोल्डर', 'dh_recent': 'हाल के', 'dh_hint': 'Ctrl+G से खोलें/सहेजें संवाद में जाएँ', 'dh_go': 'जाएँ', 'qs_ok': 'संवाद में पहुँच गया', 'qs_none': 'कोई खोलें/सहेजें संवाद सक्रिय नहीं है', 'qs_fail': 'जाने में विफल', 'jm_hint': 'सहेजें / खोलें डायलॉग · परिणाम पर क्लिक करें (फ़ोल्डर डायलॉग में खुलेंगे)', 'ad_hint': 'पथ पता बार में भर दिया है — जाने के लिए Enter दबाएँ', 'nav_hint': 'डायलॉग में वह फ़ोल्डर खुल गया', 'scope': 'केवल “{p}” में', 'scope_clear': 'सभी जगह खोजें', 'sel_n': '{n} चुने गए', 'copied_one': 'पथ कॉपी हुआ', 'copied_n': '{n} पथ कॉपी हुए', 'copied_dir': 'फ़ोल्डर कॉपी हुआ', 'copy_none': 'कॉपी करने के लिए कुछ नहीं', 'copy_fail': 'कॉपी नहीं हो सका', },

}



# 第 355 项：命令关键字的名字（快搜窗里那一行）
MINI_L_CMD = {
    'zh': {'cmd_shot': '截图取字并复制', 'cmd_verify': '全库校验索引', 'cmd_cmd': '在此目录开 CMD',
           'cmd_psh': '在此目录开 PowerShell', 'cmd_here': '当前目录', 'cmd_fail': '命令没跑起来'},
    'en': {'cmd_shot': 'Capture screen text', 'cmd_verify': 'Verify the index', 'cmd_cmd': 'Open CMD here',
           'cmd_psh': 'Open PowerShell here', 'cmd_here': 'Current folder', 'cmd_fail': 'Command did not run'},
    'es': {'cmd_shot': 'Capturar texto de pantalla', 'cmd_verify': 'Verificar el índice', 'cmd_cmd': 'Abrir CMD aquí',
           'cmd_psh': 'Abrir PowerShell aquí', 'cmd_here': 'Carpeta actual', 'cmd_fail': 'El comando no se ejecutó'},
    'pt': {'cmd_shot': 'Capturar texto da tela', 'cmd_verify': 'Verificar o índice', 'cmd_cmd': 'Abrir CMD aqui',
           'cmd_psh': 'Abrir PowerShell aqui', 'cmd_here': 'Pasta atual', 'cmd_fail': 'O comando não rodou'},
    'hi': {'cmd_shot': 'स्क्रीन टेक्स्ट कैप्चर', 'cmd_verify': 'इंडेक्स जाँचें', 'cmd_cmd': 'यहाँ CMD खोलें',
           'cmd_psh': 'यहाँ PowerShell खोलें', 'cmd_here': 'मौजूदा फ़ोल्डर', 'cmd_fail': 'कमांड नहीं चला'},
}

for _k, _v in MINI_L_CMD.items():
    MINI_L.setdefault(_k, {}).update(_v)
for _k, _v in MINI_L_EXTRA.items():

    MINI_L.setdefault(_k, {}).update(_v)






# 第 280 项：快搜窗里那几个 title（悬停说明）原来写死中文 —— 界面切成英文/西语/印地语
# 照样弹「屏幕截图（松手即复制）」。这里按界面语言给一份，_subs() 里替换 __TIP_*__ 占位符。
MINI_TIPS = {
    'zh-CN': {
        'snip': '屏幕截图（松手即复制）', 'clip': '剪贴板历史', 'trans': '聚合翻译',
        'run': '点击立即翻译（或按 Enter）', 'copy_item': '点击复制到剪贴板',
        'copy_text': '点击复制译文',
        # 剪贴板面板本体的文案（面板里的条目 detail / 相对时间由后端按语言拼）
        'cb_title': '剪贴板历史', 'cb_all': '全部', 'cb_fav': '★ 收藏',
        'cb_text': '文本', 'cb_image': '图片', 'cb_file': '文件',
        'cb_clear': '清空', 'cb_clear_title': '清空全部', 'cb_close': '关闭 (Esc)',
        'cb_empty': '暂无剪贴板历史', 'cb_empty_fav': '暂无收藏的剪贴板内容',
        'cb_load_fail': '加载失败', 'cb_pin_add': '加入收藏', 'cb_pin_del': '取消收藏',
        'cb_del': '删除', 'cb_badge_file': '📁 文件', 'cb_badge_text': '文本',
        'cb_badge_image': '图片', 'cb_copied': '已复制到剪贴板 ✓', 'cb_copy_fail': '复制失败',
        'trans_target': '目标语言',
    },
    'en': {
        'snip': 'Screenshot (copied on release)', 'clip': 'Clipboard history',
        'trans': 'Aggregate translation', 'run': 'Translate now (or press Enter)',
        'copy_item': 'Click to copy to clipboard', 'copy_text': 'Click to copy the translation',
        'cb_title': 'Clipboard history', 'cb_all': 'All', 'cb_fav': '★ Favorites',
        'cb_text': 'Text', 'cb_image': 'Images', 'cb_file': 'Files',
        'cb_clear': 'Clear', 'cb_clear_title': 'Clear all', 'cb_close': 'Close (Esc)',
        'cb_empty': 'No clipboard history yet',
        'cb_empty_fav': 'No favorites in the clipboard yet',
        'cb_load_fail': 'Failed to load', 'cb_pin_add': 'Add to favorites',
        'cb_pin_del': 'Remove from favorites', 'cb_del': 'Delete',
        'cb_badge_file': '📁 File', 'cb_badge_text': 'Text', 'cb_badge_image': 'Image',
        'cb_copied': 'Copied to clipboard ✓', 'cb_copy_fail': 'Copy failed',
        'trans_target': 'Target language',
    },
    'es': {
        'snip': 'Captura de pantalla (se copia al soltar)', 'clip': 'Historial del portapapeles',
        'trans': 'Traducción agregada', 'run': 'Traducir ahora (o pulsa Enter)',
        'copy_item': 'Clic para copiar al portapapeles', 'copy_text': 'Clic para copiar la traducción',
        'cb_title': 'Historial del portapapeles', 'cb_all': 'Todo', 'cb_fav': '★ Favoritos',
        'cb_text': 'Texto', 'cb_image': 'Imágenes', 'cb_file': 'Archivos',
        'cb_clear': 'Borrar', 'cb_clear_title': 'Borrar todo', 'cb_close': 'Cerrar (Esc)',
        'cb_empty': 'Aún no hay historial del portapapeles',
        'cb_empty_fav': 'Aún no hay favoritos en el portapapeles',
        'cb_load_fail': 'No se pudo cargar', 'cb_pin_add': 'Añadir a favoritos',
        'cb_pin_del': 'Quitar de favoritos', 'cb_del': 'Eliminar',
        'cb_badge_file': '📁 Archivo', 'cb_badge_text': 'Texto', 'cb_badge_image': 'Imagen',
        'cb_copied': 'Copiado al portapapeles ✓', 'cb_copy_fail': 'No se pudo copiar',
        'trans_target': 'Idioma de destino',
    },
    'pt': {
        'snip': 'Captura de tela (copiada ao soltar)', 'clip': 'Histórico da área de transferência',
        'trans': 'Tradução agregada', 'run': 'Traduzir agora (ou pressione Enter)',
        'copy_item': 'Clique para copiar para a área de transferência',
        'copy_text': 'Clique para copiar a tradução',
        'cb_title': 'Histórico da área de transferência', 'cb_all': 'Tudo',
        'cb_fav': '★ Favoritos', 'cb_text': 'Texto', 'cb_image': 'Imagens',
        'cb_file': 'Arquivos', 'cb_clear': 'Limpar', 'cb_clear_title': 'Limpar tudo',
        'cb_close': 'Fechar (Esc)',
        'cb_empty': 'Ainda não há histórico da área de transferência',
        'cb_empty_fav': 'Ainda não há favoritos na área de transferência',
        'cb_load_fail': 'Falha ao carregar', 'cb_pin_add': 'Adicionar aos favoritos',
        'cb_pin_del': 'Remover dos favoritos', 'cb_del': 'Excluir',
        'cb_badge_file': '📁 Arquivo', 'cb_badge_text': 'Texto', 'cb_badge_image': 'Imagem',
        'cb_copied': 'Copiado para a área de transferência ✓', 'cb_copy_fail': 'Falha ao copiar',
        'trans_target': 'Idioma de destino',
    },
    'hi': {
        'snip': 'स्क्रीनशॉट (छोड़ते ही कॉपी)', 'clip': 'क्लिपबोर्ड इतिहास',
        'trans': 'एकत्रित अनुवाद', 'run': 'अभी अनुवाद करें (या Enter दबाएँ)',
        'copy_item': 'क्लिपबोर्ड में कॉपी करने के लिए क्लिक करें',
        'copy_text': 'अनुवाद कॉपी करने के लिए क्लिक करें',
        'cb_title': 'क्लिपबोर्ड इतिहास', 'cb_all': 'सभी', 'cb_fav': '★ पसंदीदा',
        'cb_text': 'टेक्स्ट', 'cb_image': 'चित्र', 'cb_file': 'फ़ाइलें',
        'cb_clear': 'साफ़ करें', 'cb_clear_title': 'सब साफ़ करें', 'cb_close': 'बंद करें (Esc)',
        'cb_empty': 'अभी कोई क्लिपबोर्ड इतिहास नहीं',
        'cb_empty_fav': 'क्लिपबोर्ड में अभी कोई पसंदीदा नहीं',
        'cb_load_fail': 'लोड नहीं हुआ', 'cb_pin_add': 'पसंदीदा में जोड़ें',
        'cb_pin_del': 'पसंदीदा से हटाएँ', 'cb_del': 'हटाएँ',
        'cb_badge_file': '📁 फ़ाइल', 'cb_badge_text': 'टेक्स्ट', 'cb_badge_image': 'चित्र',
        'cb_copied': 'क्लिपबोर्ड में कॉपी ✓', 'cb_copy_fail': 'कॉपी विफल',
        'trans_target': 'लक्ष्य भाषा',
    },
}


def _mini_tip_lang():
    """界面语言 → MINI_TIPS 的键。取不到或不认识就按中文（和别处一样的老实退路）。"""
    raw = ''
    try:
        raw = str(boot_lang() or '')
    except Exception:
        raw = ''
    if not raw:
        try:
            raw = str(_settings_read().get('lang') or '')
        except Exception:
            raw = ''
    code = str(raw).strip().lower()
    code = {'zh': 'zh-CN', 'zh-cn': 'zh-CN', 'zh_cn': 'zh-CN', 'cn': 'zh-CN'}.get(code, code)
    return code if code in MINI_TIPS else 'zh-CN'


def _mini_tips(lang=None):
    """lang 可以来自 /mini?lang=xx（页面请求），也可以不传（按 settings）。"""
    code = str(lang or '').strip().lower()
    code = {'zh': 'zh-CN', 'zh-cn': 'zh-CN', 'zh_cn': 'zh-CN', 'cn': 'zh-CN'}.get(code, code)
    if code not in MINI_TIPS:
        code = _mini_tip_lang()
    return MINI_TIPS.get(code, MINI_TIPS['zh-CN'])


MINI_UI = {
    'zh-CN': {
        'about': '关于', 'about_name': 'Memex · 本地搜索', 'about_ok': '知道了',
        'about_note': '当前为 <b>0.x 预览版</b>：功能还在打磨，未达正式标准。<br>版本号到达 <b>1.0</b> 即视为正式版。',
        'docs_line': '正文可搜 <b>__TOTAL__</b> 篇 · 文件名可搜 <b>__FILES__</b> 个',
        'build_date': '构建日期 <b>__BUILD__</b>',
        'win_title': 'Memex · 快搜', 'q_ph': '搜索文件名、文件夹或正文内容',
        'detail': '详细搜索（Ctrl+Enter）', 'detail_short': 'Ctrl+Enter 详细搜索',
        'search': '搜索', 'recent': '最近打开', 'recent_hint': 'Enter 直接打开',
'empty': '输入文件名或正文关键词，边打边出结果',
'empty_hint': '双击 Ctrl 唤出 · Esc 关闭',
        'snip_title': '截图识字', 'snip_ph': '未识别到文字',
        'snip_scan': '按 Enter 或点击搜索进行识字...',
        'snip_tr_hover': '截图翻译', 'snip_trans': '翻译', 'snip_src': '原文', 'snip_dst': '译文',
        'snip_trans_doing': '翻译中…', 'snip_trans_none': '翻译失败，稍后再试或换个引擎',
        'snip_trans_hint': '译文可复制，随时切回原文',
        'copy_text_btn': '复制文字 (Enter)', 'copy_img_btn': '复制图片',
        'search_this': '搜此文字', 'save_img': '保存图片',
        'snip_timeout': '识字超时', 'snip_none': '未识别到文字', 'snip_fail': '识字失败',
        'snip_ready': '截图已就位（按 Enter 识字搜索，Delete 删除）',
        'snip_lines': '{n} 行文字', 'snip_copied': '已复制！✓',
        'snip_img_copied': '图片已在剪贴板 ✓', 'snip_saved': '已存至桌面 ✓',
        'snip_save_fail': '保存失败',
        'preview_title': '截图预览', 'preview_hint': '按 Enter 识字搜索 · Esc / 点击关闭',
        'preview_close': '关闭预览 (Esc)',
        'img_pill': '点击放大查看图片（按 Enter 识字搜索，Delete 删除）',
        'img_pill_del': '删除图片 (Delete)',
        'trans_title': '翻译', 'trans_auto': '自动（译成中文）',
'trans_ph': '请在上方搜索框输入想要翻译的内容', 'trans_doing': '翻译中...',
'trans_timeout': '翻译超时',
'trans_fail': '未获取到翻译结果（可能引擎只返回了原文或这会儿连不上）',
                'trans_bypass': '代理端口连不上，已自动改走直连',
'trans_copied': '已复制译文 ✓',
        'cb_confirm': '确定清空未收藏的剪贴板历史吗？（收藏内容将保留）',
        'cb_loading': '加载中...', 'cb_pin_on': '已加入收藏 ★',
        'cb_pin_off': '已取消收藏', 'cb_deleted': '已删除', 'cb_cleared': '已清空未收藏历史',
    },
    'en': {
        'about': 'About', 'about_name': 'Memex · local search', 'about_ok': 'Got it',
        'about_note': 'This is a <b>0.x preview</b>: still being polished.<br>It becomes official at <b>1.0</b>.',
        'docs_line': 'Searchable text: <b>__TOTAL__</b> docs · file names: <b>__FILES__</b>',
        'build_date': 'Build date <b>__BUILD__</b>',
        'win_title': 'Memex · Quick search', 'q_ph': 'Search file names, folders or document text',
        'detail': 'Detailed search (Ctrl+Enter)', 'detail_short': 'Ctrl+Enter detailed search',
        'search': 'Search', 'recent': 'Recently opened', 'recent_hint': 'Enter to open',
        'empty': 'Type a file name or a word from a document',
        'empty_hint': 'Double Ctrl to summon · Esc to close',
        'snip_title': 'Snip & OCR', 'snip_ph': 'No text recognised',
        'snip_scan': 'Press Enter or click to OCR-search...',
        'snip_tr_hover': 'Snip translate', 'snip_trans': 'Translate', 'snip_src': 'Original', 'snip_dst': 'Translated',
        'snip_trans_doing': 'Translating…', 'snip_trans_none': 'Translation failed — try again later',
        'snip_trans_hint': 'Translation is copyable; switch back anytime',
        'copy_text_btn': 'Copy text (Enter)', 'copy_img_btn': 'Copy image',
        'search_this': 'Search this text', 'save_img': 'Save image',
        'snip_timeout': 'OCR timed out', 'snip_none': 'No text recognised',
        'snip_fail': 'OCR failed',
        'snip_ready': 'Screenshot ready (Enter to OCR-search, Delete to remove)',
        'snip_lines': '{n} text lines', 'snip_copied': 'Copied ✓',
        'snip_img_copied': 'Image copied to clipboard ✓', 'snip_saved': 'Saved to Desktop ✓',
        'snip_save_fail': 'Save failed',
        'preview_title': 'Screenshot preview',
        'preview_hint': 'Enter to OCR-search · Esc / click to close',
        'preview_close': 'Close preview (Esc)',
        'img_pill': 'Click to enlarge (Enter to OCR-search, Delete to remove)',
        'img_pill_del': 'Remove image (Delete)',
        'trans_title': 'Translate', 'trans_auto': 'Auto (into English)',
        'trans_ph': 'Type the text to translate in the search box above',
        'trans_doing': 'Translating...', 'trans_timeout': 'Translation timed out',
        'trans_fail': 'No translation returned (the engine may have echoed the source, '
                      'or the connection failed)',
        'trans_bypass': 'The proxy port is unreachable — switched to a direct connection',
        'trans_copied': 'Translation copied ✓',
        'cb_confirm': 'Clear clipboard history except favorites? (Favorites are kept)',
        'cb_loading': 'Loading...', 'cb_pin_on': 'Added to favorites ★',
        'cb_pin_off': 'Removed from favorites', 'cb_deleted': 'Deleted',
        'cb_cleared': 'Cleared (favorites kept)',
    },
    'es': {
        'about': 'Acerca de', 'about_name': 'Memex · búsqueda local', 'about_ok': 'Entendido',
        'about_note': 'Es una <b>vista previa 0.x</b>: aún se está puliendo.<br>Pasa a oficial en la <b>1.0</b>.',
        'docs_line': 'Texto buscable: <b>__TOTAL__</b> docs · nombres: <b>__FILES__</b>',
        'build_date': 'Fecha de compilación <b>__BUILD__</b>',
        'win_title': 'Memex · Búsqueda rápida',
        'q_ph': 'Busca nombres de archivo, carpetas o texto',
        'detail': 'Búsqueda detallada (Ctrl+Enter)',
        'detail_short': 'Ctrl+Enter búsqueda detallada',
        'search': 'Buscar', 'recent': 'Abiertos hace poco', 'recent_hint': 'Enter para abrir',
        'empty': 'Escribe un nombre de archivo o una palabra del documento',
        'empty_hint': 'Doble Ctrl para abrir · Esc para cerrar',
        'snip_title': 'Captura y OCR', 'snip_ph': 'No se reconoció texto',
        'snip_scan': 'Pulsa Enter o haz clic para buscar por OCR...',
        'snip_tr_hover': 'Traducir captura', 'snip_trans': 'Traducir', 'snip_src': 'Original', 'snip_dst': 'Traducido',
        'snip_trans_doing': 'Traduciendo…', 'snip_trans_none': 'No se pudo traducir, inténtalo luego',
        'snip_trans_hint': 'La traducción se puede copiar; vuelve al original cuando quieras',
        'copy_text_btn': 'Copiar texto (Enter)', 'copy_img_btn': 'Copiar imagen',
        'search_this': 'Buscar este texto', 'save_img': 'Guardar imagen',
        'snip_timeout': 'El OCR tardó demasiado', 'snip_none': 'No se reconoció texto',
        'snip_fail': 'Falló el OCR',
        'snip_ready': 'Captura lista (Enter para buscar por OCR, Delete para quitar)',
        'snip_lines': '{n} líneas de texto', 'snip_copied': 'Copiado ✓',
        'snip_img_copied': 'Imagen copiada al portapapeles ✓',
        'snip_saved': 'Guardado en el escritorio ✓', 'snip_save_fail': 'No se pudo guardar',
        'preview_title': 'Vista previa de la captura',
        'preview_hint': 'Enter para OCR · Esc o clic para cerrar',
        'preview_close': 'Cerrar vista previa (Esc)',
        'img_pill': 'Clic para ampliar (Enter para OCR, Delete para quitar)',
        'img_pill_del': 'Quitar imagen (Delete)',
        'trans_title': 'Traducir', 'trans_auto': 'Automático (a español)',
        'trans_ph': 'Escribe arriba, en el buscador, el texto que quieres traducir',
        'trans_doing': 'Traduciendo...', 'trans_timeout': 'La traducción tardó demasiado',
        'trans_fail': 'No se obtuvo traducción (puede que el motor solo devolviera el original '
                      'o que no haya conexión)',
        'trans_bypass': 'El puerto del proxy no responde — cambiado a conexión directa',
        'trans_copied': 'Traducción copiada ✓',
        'cb_confirm': '¿Borrar el historial salvo los favoritos? (Los favoritos se conservan)',
        'cb_loading': 'Cargando...', 'cb_pin_on': 'Añadido a favoritos ★',
        'cb_pin_off': 'Quitado de favoritos', 'cb_deleted': 'Eliminado',
        'cb_cleared': 'Historial borrado (favoritos conservados)',
    },
    'pt': {
        'about': 'Sobre', 'about_name': 'Memex · busca local', 'about_ok': 'Entendi',
        'about_note': 'É uma <b>prévia 0.x</b>: ainda em ajustes.<br>Vira oficial na <b>1.0</b>.',
        'docs_line': 'Texto pesquisável: <b>__TOTAL__</b> docs · nomes: <b>__FILES__</b>',
        'build_date': 'Data da compilação <b>__BUILD__</b>',
        'win_title': 'Memex · Busca rápida',
        'q_ph': 'Busque nomes de arquivo, pastas ou texto',
        'detail': 'Busca detalhada (Ctrl+Enter)',
        'detail_short': 'Ctrl+Enter busca detalhada',
        'search': 'Buscar', 'recent': 'Abertos recentemente', 'recent_hint': 'Enter para abrir',
        'empty': 'Digite um nome de arquivo ou uma palavra do documento',
        'empty_hint': 'Ctrl duas vezes para abrir · Esc para fechar',
        'snip_title': 'Captura e OCR', 'snip_ph': 'Nenhum texto reconhecido',
        'snip_scan': 'Pressione Enter ou clique para busca por OCR...',
        'snip_tr_hover': 'Traduzir captura', 'snip_trans': 'Traduzir', 'snip_src': 'Original', 'snip_dst': 'Traduzido',
        'snip_trans_doing': 'Traduzindo…', 'snip_trans_none': 'Falha ao traduzir, tente depois',
        'snip_trans_hint': 'A tradução pode ser copiada; volte ao original quando quiser',
        'copy_text_btn': 'Copiar texto (Enter)', 'copy_img_btn': 'Copiar imagem',
        'search_this': 'Buscar este texto', 'save_img': 'Salvar imagem',
        'snip_timeout': 'O OCR demorou demais', 'snip_none': 'Nenhum texto reconhecido',
        'snip_fail': 'Falha no OCR',
        'snip_ready': 'Captura pronta (Enter para OCR, Delete para remover)',
        'snip_lines': '{n} linhas de texto', 'snip_copied': 'Copiado ✓',
        'snip_img_copied': 'Imagem copiada para a área de transferência ✓',
        'snip_saved': 'Salvo na área de trabalho ✓', 'snip_save_fail': 'Falha ao salvar',
        'preview_title': 'Prévia da captura',
        'preview_hint': 'Enter para OCR · Esc ou clique para fechar',
        'preview_close': 'Fechar prévia (Esc)',
        'img_pill': 'Clique para ampliar (Enter para OCR, Delete para remover)',
        'img_pill_del': 'Remover imagem (Delete)',
        'trans_title': 'Traduzir', 'trans_auto': 'Automático (para português)',
        'trans_ph': 'Digite acima, na caixa de busca, o texto que quer traduzir',
        'trans_doing': 'Traduzindo...', 'trans_timeout': 'A tradução demorou demais',
        'trans_fail': 'Nenhuma tradução recebida (o motor pode ter devolvido o original '
                      'ou a conexão falhou)',
        'trans_bypass': 'A porta do proxy não responde — trocado para conexão direta',
        'trans_copied': 'Tradução copiada ✓',
        'cb_confirm': 'Limpar o histórico exceto favoritos? (Os favoritos são mantidos)',
        'cb_loading': 'Carregando...', 'cb_pin_on': 'Adicionado aos favoritos ★',
        'cb_pin_off': 'Removido dos favoritos', 'cb_deleted': 'Excluído',
        'cb_cleared': 'Histórico limpo (favoritos mantidos)',
    },
    'hi': {
        'about': 'परिचय', 'about_name': 'Memex · स्थानीय खोज', 'about_ok': 'समझ गया',
        'about_note': 'यह <b>0.x प्रीव्यू</b> है: अभी निखारा जा रहा है।<br><b>1.0</b> पर यह आधिकारिक होगा।',
        'docs_line': 'खोजने योग्य टेक्स्ट: <b>__TOTAL__</b> दस्तावेज़ · फ़ाइल नाम: <b>__FILES__</b>',
        'build_date': 'बिल्ड तिथि <b>__BUILD__</b>',
        'win_title': 'Memex · तेज़ खोज', 'q_ph': 'फ़ाइल नाम, फ़ोल्डर या दस्तावेज़ टेक्स्ट खोजें',
        'detail': 'विस्तृत खोज (Ctrl+Enter)', 'detail_short': 'Ctrl+Enter विस्तृत खोज',
        'search': 'खोजें', 'recent': 'हाल में खोले', 'recent_hint': 'Enter से खोलें',
        'empty': 'फ़ाइल का नाम या दस्तावेज़ का कोई शब्द लिखें',
        'empty_hint': 'दो बार Ctrl दबाएँ · Esc से बंद करें',
        'snip_title': 'स्क्रीनशॉट से टेक्स्ट', 'snip_ph': 'कोई टेक्स्ट नहीं पहचाना',
        'snip_scan': 'Enter दबाएँ या क्लिक करके टेक्स्ट पहचानें...',
        'snip_tr_hover': 'स्क्रीनशॉट अनुवाद', 'snip_trans': 'अनुवाद', 'snip_src': 'मूल', 'snip_dst': 'अनुवादित',
        'snip_trans_doing': 'अनुवाद हो रहा है…', 'snip_trans_none': 'अनुवाद नहीं हो सका, बाद में try करें',
        'snip_trans_hint': 'अनुवाद कॉपी किया जा सकता है, कभी भी मूल पर लौटें',
        'copy_text_btn': 'टेक्स्ट कॉपी करें (Enter)', 'copy_img_btn': 'चित्र कॉपी करें',
        'search_this': 'इस टेक्स्ट को खोजें', 'save_img': 'चित्र सहेजें',
        'snip_timeout': 'टेक्स्ट पहचान में समय लगा', 'snip_none': 'कोई टेक्स्ट नहीं पहचाना',
        'snip_fail': 'पहचान विफल',
        'snip_ready': 'स्क्रीनशॉट तैयार (Enter से खोज, Delete से हटाएँ)',
        'snip_lines': '{n} पंक्तियाँ', 'snip_copied': 'कॉपी हो गया ✓',
        'snip_img_copied': 'चित्र क्लिपबोर्ड में कॉपी ✓', 'snip_saved': 'डेस्कटॉप पर सहेजा ✓',
        'snip_save_fail': 'सहेजना विफल',
        'preview_title': 'स्क्रीनशॉट पूर्वावलोकन',
        'preview_hint': 'Enter से खोज · Esc या क्लिक से बंद करें',
        'preview_close': 'पूर्वावलोकन बंद करें (Esc)',
        'img_pill': 'बड़ा करने के लिए क्लिक करें (Enter से खोज, Delete से हटाएँ)',
        'img_pill_del': 'चित्र हटाएँ (Delete)',
        'trans_title': 'अनुवाद', 'trans_auto': 'स्वतः (हिन्दी में)',
        'trans_ph': 'ऊपर खोज बॉक्स में वह टेक्स्ट लिखें जिसका अनुवाद चाहिए',
        'trans_doing': 'अनुवाद हो रहा है...', 'trans_timeout': 'अनुवाद में समय लगा',
        'trans_fail': 'कोई अनुवाद नहीं मिला (शायद इंजन ने मूल टेक्स्ट लौटाया या कनेक्शन नहीं है)',
        'trans_bypass': 'प्रॉक्सी पोर्ट उपलब्ध नहीं — सीधे कनेक्शन पर स्विच किया',
        'trans_copied': 'अनुवाद कॉपी ✓',
        'cb_confirm': 'पसंदीदा छोड़कर क्लिपबोर्ड इतिहास साफ़ करें? (पसंदीदा सुरक्षित रहेंगे)',
        'cb_loading': 'लोड हो रहा है...', 'cb_pin_on': 'पसंदीदा में जोड़ा ★',
        'cb_pin_off': 'पसंदीदा से हटाया', 'cb_deleted': 'हटाया गया',
        'cb_cleared': 'इतिहास साफ़ (पसंदीदा सुरक्षित)',
    },
}


MINI_TPL = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">

<title>__U_win_title__</title>

<style>

*{box-sizing:border-box;margin:0;padding:0}

:root{--sb-bg:255 255 255;--sb-card:255 255 255;--sb-ink:26 34 51;--sb-sub:154 160 166;--sb-line:232 234 243;--sb-accent:79 110 247;--sb-side:128 137 160;--sb-glow:255 255 255}

html[data-theme=dark]{--sb-bg:24 27 33;--sb-card:34 38 46;--sb-ink:228 231 238;--sb-sub:154 163 178;--sb-line:46 51 61;--sb-accent:107 138 255;--sb-side:154 164 182;--sb-glow:0 0 0}

html,body{height:100%;background:rgb(var(--sb-bg));overflow:hidden;

  font-family:"Segoe UI Variable Text","Segoe UI","Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;

  color:rgb(var(--sb-ink));-webkit-font-smoothing:antialiased}

#pane{display:flex;flex-direction:column;height:100%}

.bar{display:flex;align-items:center;height:62px;padding:0 12px 0 22px;flex:0 0 auto}

.pasted-img-pill{display:inline-flex;align-items:center;gap:6px;padding:3px 6px 3px 4px;margin-right:8px;
  background:rgb(var(--sb-line) / .65);border:1px solid rgb(var(--sb-line));border-radius:7px;
  flex:0 0 auto;max-width:140px;position:relative;animation:pill-in .15s ease;cursor:pointer;user-select:none}
@keyframes pill-in{from{opacity:0;transform:scale(.92)}to{opacity:1;transform:scale(1)}}
.pasted-img-pill.hide{display:none}
.pasted-img-pill img{width:26px;height:26px;object-fit:cover;border-radius:4px;display:block}
.pasted-img-del{border:0;background:transparent;color:rgb(var(--sb-sub));cursor:pointer;
  width:16px;height:16px;display:flex;align-items:center;justify-content:center;
  border-radius:50%;font-size:13px;line-height:1;padding:0}
.pasted-img-del:hover{background:rgb(var(--sb-ink) / .15);color:rgb(var(--sb-ink))}
.pasted-img-spinner{width:12px;height:12px;border:2px solid rgb(var(--sb-accent) / .3);
  border-top-color:rgb(var(--sb-accent));border-radius:50%;animation:p-spin .6s linear infinite}
@keyframes p-spin{to{transform:rotate(360deg)}}
.pasted-img-spinner.hide{display:none}
.small .pasted-img-pill{margin-right:6px;padding:2px 5px 2px 3px}
.small .pasted-img-pill img{width:20px;height:20px}

#q{flex:1;min-width:0;border:0;outline:0;background:transparent;font-size:19px;

  font-family:inherit;color:rgb(var(--sb-ink));letter-spacing:.2px;line-height:1.4}

#q::placeholder{color:rgb(var(--sb-sub));font-weight:400}

.act{display:flex;align-items:center;gap:2px;flex:0 0 auto}

/* 第 369 项：截图翻译的「悬停浮层」——只有停在相机键上 0.6 秒才出现，平时零占位。
   绝对定位盖在结果列表上，出现时不挤动输入框和这排按钮。
   注意：锚点必须是**相机键自己**（.snipwrap），不是整排按钮 ——
   否则鼠标移到「详情 / 搜索」上也会冒出截图翻译（用户 2026-11-20 实测报的）。 */
.actwrap{position:relative;display:flex;align-items:center;flex:0 0 auto}
.snipwrap{position:relative;display:flex;align-items:center}
.act-pop{display:none;position:absolute;right:100%;top:50%;transform:translateY(-50%);margin-right:6px;z-index:60;padding:2px;
  border-radius:9px;background:rgb(var(--sb-card));border:1px solid rgb(var(--sb-line));
  box-shadow:0 8px 24px rgba(0,0,0,.22)}
.snipwrap.hover .act-pop{display:block;animation:actfade .12s ease}
@keyframes actfade{from{opacity:0}to{opacity:1}}
.act-pop-btn{display:inline-flex;align-items:center;gap:6px;border:0;cursor:pointer;font:inherit;
  font-size:12px;padding:6px 10px;border-radius:7px;background:transparent;color:rgb(var(--sb-ink));white-space:nowrap}
.act-pop-btn:hover{background:rgb(var(--sb-accent) / .12);color:rgb(var(--sb-accent))}
.act-pop-btn svg{width:15px;height:15px}

.ab{border:0;background:transparent;width:34px;height:34px;border-radius:9px;cursor:pointer;

  display:flex;align-items:center;justify-content:center;color:rgb(var(--sb-sub));transition:.13s}

.ab:hover{background:rgb(var(--sb-line) / .55);color:rgb(var(--sb-accent))}

.ab svg{width:17px;height:17px}

.ab.abtext{width:auto;padding:0 10px;font-size:12px;font-weight:600;letter-spacing:.3px}

.act .ab.abtext{min-width:46px}

.ab.on{background:rgb(var(--sb-accent) / .15);color:rgb(var(--sb-accent))}

.small .ab.abtext{width:auto;min-width:38px;padding:0 6px;font-size:11px}

#list{flex:0 0 auto;padding:0 8px;overflow:hidden}

.row{display:flex;align-items:center;gap:11px;height:52px;padding:0 14px 0 12px;

  border-radius:9px;cursor:default;user-select:none;position:relative}

#list.rec .row{height:48px}

.row.on{background:rgb(var(--sb-line) / .55)}

.row.mark{background:rgb(var(--sb-line) / .9);box-shadow:inset 3px 0 0 rgb(var(--sb-accent))}

.ico{flex:0 0 auto;display:flex;align-items:center;justify-content:center;width:26px;height:26px}

.ico .ic{display:block;width:26px;height:26px}

.ico .fic{display:none;width:17px;height:17px}

.ico.noic .ic{display:none}

.ico.noic .fic{display:block}

.txt{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}

.nm{font-size:16.5px;line-height:1.22;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;

  color:rgb(var(--sb-accent))}

.nm b{font-weight:600;color:rgb(var(--sb-accent));background:rgb(var(--sb-accent) / .16);border-radius:2px}

.tag{font-style:normal;font-size:10.5px;line-height:1;color:rgb(var(--sb-accent));background:rgb(var(--sb-accent) / .13);

  border-radius:4px;padding:2px 5px;margin-left:7px;vertical-align:1.5px;letter-spacing:.02em}

.sep{font-size:11px;color:rgb(var(--sb-sub));padding:7px 12px 3px 40px;border-top:1px solid rgb(var(--sb-line));margin-top:5px}

.sn{font-size:12px;line-height:1.25;color:rgb(var(--sb-sub));white-space:nowrap;overflow:hidden;

  text-overflow:ellipsis}

.sn mark{background:rgb(var(--sb-accent) / .16);border-radius:2px;padding:0 1px;color:rgb(var(--sb-ink));font-weight:500}

.pt,.pt b{font-weight:400;font-size:13px;line-height:1.2;color:rgb(var(--sb-sub));

  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}

/* 第 275 项：空状态那列「最近打开」的**可选紧凑样式**。原来一行两排（16.5px 名称 + 13px 灰路径）
   占 48px、铺 6 条 300 多像素；紧凑版把名称与路径排同一行、行高 32px、最多 4 条。
   用户说过"改小一点"，但看到实际观感又觉得不如原样 —— 所以 11-17 20:x 改成
   **默认还原原样，设置 → 快搜窗「最近记录紧凑显示」自己开**（qs_home_compact）。 */
#list.compact .row{height:32px;gap:8px;padding:0 11px 0 10px;border-radius:6px}

#list.compact .ico,#list.compact .ico .ic{width:17px;height:17px}

#list.compact .ico .fic{width:14px;height:14px}

#list.compact .txt{flex-direction:row;align-items:baseline;gap:9px;overflow:hidden}

#list.compact .nm{font-size:13.5px;flex:0 0 auto;max-width:58%}

#list.compact .pt{flex:1 1 0;min-width:0;font-size:11px;color:rgb(var(--sb-sub) / .82)}

#list.compact .sn{flex:0 1 auto;min-width:0;font-size:11px}


.rh{display:flex;align-items:center;height:26px;padding:0 14px 0 13px;font-size:11px;

  letter-spacing:.06em;color:rgb(var(--sb-sub) / .8);flex:0 0 auto}

.rh span{margin-left:auto;letter-spacing:0;color:rgb(var(--sb-sub) / .75)}

.kbd{flex:0 0 auto;font-size:12px;color:rgb(var(--sb-sub) / .8);font-variant-numeric:tabular-nums}

.dlist .row{height:44px;cursor:pointer;gap:10px}

.dlist .row:hover{background:rgb(var(--sb-line) / .55)}

.dlist .row .nm{font-weight:500}

.dlist .row .kbd{color:rgb(var(--sb-accent));font-size:11px}

.dh{color:rgb(var(--sb-accent))}

.dh span{color:rgb(var(--sb-sub))}

.jm{display:flex;align-items:center;height:26px;padding:0 14px 0 13px;font-size:11.5px;

  color:rgb(var(--sb-accent));background:rgb(var(--sb-line) / .55);flex:0 0 auto;

  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.jm.hide{display:none}

.row.on .kbd{color:rgb(var(--sb-sub))}

.foot{display:flex;align-items:center;justify-content:space-between;height:36px;

  margin:0 8px;padding:0 12px;border-top:1px solid rgb(var(--sb-line));font-size:12.5px;color:rgb(var(--sb-sub));

  gap:10px;flex:0 0 auto}

.foot.hide{display:none}

/* 「c: 只搜正文」的顺带提示：比正文更淡，不抢「展示更多」的主次 */

.chint{font-size:11.5px;color:rgb(var(--sb-sub) / .75);white-space:nowrap}

.foot.clickable{cursor:pointer}

.foot.clickable:hover .fl{color:rgb(var(--sb-ink))}

.foot.clickable:hover .fr{color:rgb(var(--sb-ink))}

.rh.hide{display:none}

#wallbg{position:fixed;inset:0;z-index:-1;background-position:center;background-size:cover;opacity:0;transition:opacity .18s;pointer-events:none}

body.has-wall{background:transparent}

body.has-wall #pane{background:rgb(var(--sb-bg) / .86);text-shadow:0 1px 2px rgb(var(--sb-glow) / .70)}

.foot .fl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center;gap:8px}

.foot .mic{flex:0 0 auto;width:20px;height:20px;border-radius:5px;background:#1a73e8;position:relative}

.foot .mic:after{content:"";position:absolute;left:5px;top:5px;width:8px;height:8px;

  border-top:2px solid #fff;border-right:2px solid #fff;transform:rotate(45deg)}

.foot.clickable .fl{color:rgb(var(--sb-accent))}

.foot .fr{flex:0 0 auto;color:rgb(var(--sb-sub));font-size:11.5px}

.foot b{font-weight:600;color:rgb(var(--sb-ink))}

.empty{height:64px;display:flex;align-items:center;padding:0 24px;font-size:13px;color:rgb(var(--sb-sub))}

.empty.hide{display:none}

.empty i{font-style:normal;margin-left:auto;color:rgb(var(--sb-sub) / .7);font-size:11.5px}

.fright{display:flex;align-items:center;gap:10px}

.mver{font-size:11.5px;color:rgb(var(--sb-sub));cursor:pointer;font-variant-numeric:tabular-nums}

.mver:hover{color:rgb(var(--sb-ink))}

.amask{position:fixed;inset:0;background:rgba(15,22,40,.4);z-index:50;display:none}
/* 右键菜单（第 123 项）：快搜窗结果行右键 → 打开/所在文件夹/复制路径/属性 */
#cmenu{position:fixed;z-index:60;display:none;min-width:150px;background:rgb(var(--sb-card) / .98);
  border:1px solid rgb(var(--sb-line));border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.28);
  padding:4px;overflow:hidden}
#cmenu.on{display:block}
#cmenu .ci{display:block;width:100%;text-align:left;padding:7px 12px;border:0;background:none;
  font-size:12.5px;color:rgb(var(--sb-ink));cursor:pointer;border-radius:7px}
#cmenu .ci:hover{background:rgb(var(--sb-accent) / .12);color:rgb(var(--sb-accent))}

.amask.on{display:block}

.about{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.95);z-index:51;

  width:300px;max-width:86vw;background:rgb(var(--sb-card));border:1px solid rgb(var(--sb-line));border-radius:16px;

  padding:26px 22px 20px;text-align:center;display:none;box-shadow:0 20px 60px rgba(20,30,60,.28)}

.about.on{display:block;animation:apop .14s ease}

@keyframes apop{from{opacity:0;transform:translate(-50%,-50%) scale(.92)}to{opacity:1;transform:translate(-50%,-50%) scale(1)}}

.about .alogo{font-size:34px;line-height:1}

.about .aname{font-size:15px;font-weight:700;margin-top:8px;color:rgb(var(--sb-ink))}

.about .aver{font-size:30px;font-weight:800;color:rgb(var(--sb-accent));margin:10px 0 14px;letter-spacing:.02em}

.about .ameta{font-size:12.5px;color:rgb(var(--sb-sub));line-height:1.95}

.about .ameta b{color:rgb(var(--sb-ink));font-weight:600;font-variant-numeric:tabular-nums}

.about .anote{font-size:11.5px;color:rgb(var(--sb-sub));margin:14px 0 18px;line-height:1.65;

  background:rgb(var(--sb-line) / .45);border-radius:9px;padding:10px 12px}

.pbtn{font-size:12px;padding:5px 12px;border-radius:6px;cursor:pointer;border:0;background:rgb(var(--sb-line));color:rgb(var(--sb-ink))}

.pbtn.blue{background:rgb(var(--sb-accent));color:#fff}

.pbtn:hover{opacity:.9}

.about .pbtn{width:100%}



.sc{display:flex;align-items:center;gap:6px;margin:2px 12px 6px;padding:3px 8px;border-radius:7px;

box-sizing:border-box;background:rgb(var(--sb-accent) / .13);font-size:12px;color:rgb(var(--sb-sub))}

.sc.hide{display:none}

.sc #sct{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.sc button{border:0;background:transparent;color:rgb(var(--sb-sub));cursor:pointer;font-size:15px;

line-height:1;padding:0 2px}

.sc button:hover{color:rgb(var(--sb-ink))}

/* 小窗模式（第 198 项）：贴在文件夹右下角 / 对话框下方时用，宽度 460。
   只缩尺寸不删信息，路径和快捷键都还在，只是行高紧一档。 */

.small #pane{width:100%;max-width:100%;overflow:hidden}

.small .txt{min-width:0;max-width:100%;overflow:hidden}

.small .bar{height:46px;padding:0 8px 0 14px}

.small #q{font-size:14px;letter-spacing:0}

.small .ab{width:26px;height:26px}

.small .ab svg{width:14px;height:14px}

.small #list{padding:0 5px}

.small .row{height:38px;gap:8px;padding:0 10px 0 8px;border-radius:7px}

.small #list.rec .row{height:36px}

.small .ico{width:20px;height:20px}

.small .ico .ic{width:20px;height:20px}

.small .ico .fic{width:14px;height:14px}

.small .nm{font-size:13.5px;min-width:0;max-width:100%}

.small .pt{font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;max-width:100%}

#dlist .grp{font-size:11px;color:rgb(var(--sb-sub));padding:5px 12px 0;opacity:.9}

/* 第 199 项：小窗只有 320~460 宽，标题右边那句提示会挤爆，直接不显示——
   第一条右边本来就有 Ctrl+G 键帽。 */

.small .dh span{display:none}

.small .dh,.small .rh{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}

.small #dlist .grp{font-size:10px;padding:3px 10px 0}

.small .row{overflow:hidden}

.small .rh{padding-right:8px;padding-left:10px}

.small .kbd{font-size:10.5px}

.small .sc{font-size:11px;margin:0 8px 4px;padding:2px 6px}

.small .foot{font-size:10.5px}


/* 第 203 项：网络搜索模式 —— 输入框左边显示引擎名 + 竖线，结果只有一行 */
.wtag{flex:0 0 auto;font-size:19px;font-weight:500;color:rgb(var(--sb-accent));
  padding-right:12px;margin-right:12px;border-right:1px solid rgb(var(--sb-line));
  white-space:nowrap;max-width:150px;overflow:hidden;text-overflow:ellipsis}
.wtag.hide{display:none}
.webrow .wico{display:flex;align-items:center;justify-content:center;
  width:26px;height:26px;border-radius:7px;background:rgb(var(--sb-accent) / .14);
  color:rgb(var(--sb-accent));font-size:14px;font-weight:600}
.small .wtag{font-size:13px;padding-right:8px;margin-right:8px}
.small .webrow .wico{width:18px;height:18px;font-size:11px;border-radius:5px}

/* 截图大图预览卡片 */
.img-preview-box{display:flex;flex-direction:column;background:rgb(var(--sb-card));border-bottom:1px solid rgb(var(--sb-line));animation:apop .15s ease}
.img-preview-box.hide{display:none}
.ip-head{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:rgb(var(--sb-bg) / .4);border-bottom:1px solid rgb(var(--sb-line) / .5)}
.ip-title{display:flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;color:rgb(var(--sb-ink))}
.ip-meta{font-size:11px;font-weight:normal;color:rgb(var(--sb-sub));margin-left:4px}
.ip-hint{font-size:11px;color:rgb(var(--sb-sub))}
.ip-close{border:0;background:transparent;cursor:pointer;font-size:16px;line-height:1;color:rgb(var(--sb-sub));padding:0 4px}
.ip-close:hover{color:rgb(var(--sb-ink))}
.ip-body{display:flex;align-items:center;justify-content:center;padding:10px 14px;background:rgb(var(--sb-bg) / .25);max-height:280px;overflow:hidden;cursor:zoom-out}
.ip-body img{max-width:100%;max-height:260px;object-fit:contain;border-radius:6px;box-shadow:0 2px 10px rgba(0,0,0,0.12)}

/* 截图识字卡片 */
.snip-box{display:flex;flex-direction:column;gap:8px;padding:12px;background:rgb(var(--sb-card));border-bottom:1px solid rgb(var(--sb-line));animation:apop .15s ease}
.snip-box.hide{display:none}
.sb-head{display:flex;align-items:center;justify-content:space-between}
.sb-title{display:flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;color:rgb(var(--sb-ink))}
.sb-meta{font-size:11px;font-weight:normal;color:rgb(var(--sb-sub));margin-left:4px}
.sb-close{border:0;background:transparent;cursor:pointer;font-size:16px;line-height:1;color:rgb(var(--sb-sub));padding:0 4px}
.sb-close:hover{color:rgb(var(--sb-ink))}
.sb-body{display:flex;gap:10px;max-height:160px}
.sb-thumb-wrap{flex:0 0 110px;height:120px;border-radius:8px;overflow:hidden;border:1px solid rgb(var(--sb-line));background:rgba(0,0,0,.03);display:flex;align-items:center;justify-content:center}
.sb-thumb-wrap img{max-width:100%;max-height:100%;object-fit:contain}
#sbText{flex:1;min-width:0;height:120px;border-radius:8px;border:1px solid rgb(var(--sb-line));padding:8px 10px;font-size:12.5px;line-height:1.5;color:rgb(var(--sb-ink));background:transparent;resize:none;outline:none;font-family:inherit}
#sbText:focus{border-color:rgb(var(--sb-accent))}
.sb-foot{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.sb-foot .pbtn{font-size:12px;padding:5px 12px;border-radius:6px;cursor:pointer;border:0}
.sb-foot .pbtn.blue{background:rgb(var(--sb-accent));color:#fff}
.sb-foot .pbtn.blue:hover{opacity:.9}
.sb-foot .pbtn.gray{background:rgb(var(--sb-line));color:rgb(var(--sb-ink))}
.sb-foot .pbtn.gray:hover{opacity:.8}
/* 第 361 项：截图翻译 —— 原文 / 译文 切换 */
.sb-views{display:none;gap:2px;margin-left:6px;padding:2px;border-radius:8px;background:rgb(var(--sb-line) / .38)}
.sb-views.on{display:inline-flex}
.sbv{border:0;cursor:pointer;font:inherit;font-size:11px;line-height:1;padding:4px 9px;border-radius:6px;background:transparent;color:rgb(var(--sb-sub))}
.sbv:hover{color:rgb(var(--sb-ink))}
.sbv.active{background:rgb(var(--sb-card));color:rgb(var(--sb-ink))}
.sb-foot .pbtn[disabled]{opacity:.6;cursor:default}

/* 剪贴板历史卡片 */
.clip-box{display:flex;flex-direction:column;gap:8px;padding:10px 12px;background:rgb(var(--sb-card));border-bottom:1px solid rgb(var(--sb-line));animation:apop .15s ease;max-height:260px}
.clip-box.hide{display:none}
.cb-head{display:flex;align-items:center;gap:8px}
.cb-title{display:flex;align-items:center;gap:5px;font-size:12.5px;font-weight:600;color:rgb(var(--sb-ink))}
.cb-tabs{display:flex;align-items:center;gap:4px;margin-left:6px}
.cbt{font-size:11.5px;padding:2px 8px;border-radius:4px;border:0;background:transparent;color:rgb(var(--sb-sub));cursor:pointer}
.cbt.active{background:rgb(var(--sb-accent));color:#fff;font-weight:500}
.cb-clear{font-size:11.5px;margin-left:auto;border:0;background:transparent;color:rgb(var(--sb-sub));cursor:pointer;padding:2px 6px;border-radius:4px}
.cb-clear:hover{color:#ef4444;background:rgba(239,68,68,0.08)}
.cb-list{display:flex;flex-direction:column;gap:4px;overflow-y:auto;max-height:200px;padding-right:2px}
.cb-item{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;cursor:pointer;transition:background .1s;position:relative}
.cb-item:hover{background:rgb(var(--sb-line) / .5)}
.cb-badge{font-size:10.5px;padding:1px 5px;border-radius:3px;background:rgb(var(--sb-line));color:rgb(var(--sb-ink));white-space:nowrap}
.cb-txt{flex:1;min-width:0;font-size:12px;color:rgb(var(--sb-ink));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cb-time{font-size:10.5px;color:rgb(var(--sb-sub));white-space:nowrap}
.cb-del{opacity:0;border:0;background:transparent;color:rgb(var(--sb-sub));cursor:pointer;padding:2px 4px;border-radius:3px;font-size:12px}
.cb-item:hover .cb-del{opacity:1}
.cb-del:hover{color:#ef4444}
.cb-pin{opacity:0;border:0;background:transparent;color:rgb(var(--sb-sub));cursor:pointer;padding:2px 4px;border-radius:3px;font-size:13px;line-height:1;transition:all .15s}
.cb-item:hover .cb-pin{opacity:1}
.cb-pin.pinned{opacity:1;color:#f59e0b}
.cb-pin:hover{color:#f59e0b;transform:scale(1.2)}
.cb-item.cb-copying{background:rgb(var(--sb-accent) / .15);transition:background .1s}
.cb-img-thumb{width:36px;height:24px;border-radius:3px;object-fit:cover;border:1px solid rgb(var(--sb-line))}
.mini-toast{position:fixed;top:8px;left:50%;transform:translateX(-50%) translateY(-6px);background:rgba(24,28,38,.94);color:#fff;backdrop-filter:blur(8px);padding:5px 14px;border-radius:6px;font-size:12px;font-weight:500;box-shadow:0 4px 14px rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.14);opacity:0;pointer-events:none;transition:opacity .15s,transform .15s;z-index:999;white-space:nowrap}
.mini-toast.on{opacity:1;transform:translateX(-50%) translateY(0)}

/* 聚合翻译卡片 */
.trans-box{display:flex;flex-direction:column;gap:8px;padding:10px 12px;background:rgb(var(--sb-card));border-bottom:1px solid rgb(var(--sb-line));animation:apop .15s ease;max-height:260px}
.trans-box.hide{display:none}
.tb-head{display:flex;align-items:center;gap:8px}
.tb-title{display:flex;align-items:center;gap:5px;font-size:12.5px;font-weight:600;color:rgb(var(--sb-ink))}
.tb-actions{display:flex;align-items:center;gap:6px;margin-left:auto}
.tb-lang-select{font-size:11px;color:rgb(var(--sb-ink));background:rgb(var(--sb-line) / .4);border:1px solid rgb(var(--sb-line));padding:2px 6px;border-radius:5px;outline:none;cursor:pointer}
.tb-lang-select:hover{border-color:rgb(var(--sb-accent))}
.tb-run-btn{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;color:#fff;background:rgb(var(--sb-accent));border:none;padding:3px 9px;border-radius:5px;cursor:pointer;transition:opacity .15s}
.tb-run-btn:hover{opacity:.9}
.tb-results{display:flex;flex-direction:column;gap:6px;overflow-y:auto;max-height:200px}
/* 第 359 项：这两块会滚动，之前用系统滚动条 —— 深色配色下就是一条白轨 */
.cb-list::-webkit-scrollbar,.tb-results::-webkit-scrollbar{width:8px;height:8px}
.cb-list::-webkit-scrollbar-thumb,.tb-results::-webkit-scrollbar-thumb{background:rgb(var(--sb-sub) / .45);border-radius:8px}
.cb-list::-webkit-scrollbar-track,.tb-results::-webkit-scrollbar-track{background:transparent}.cb-list::-webkit-scrollbar-thumb:hover,.tb-results::-webkit-scrollbar-thumb:hover{background:rgb(var(--sb-sub) / .7)}
.tb-card{display:flex;flex-direction:column;gap:3px;padding:8px 10px;border-radius:6px;border:1px solid rgb(var(--sb-line));background:rgb(var(--sb-bg) / .3);cursor:pointer;transition:border-color .15s}
.tb-card:hover{border-color:rgb(var(--sb-accent))}
.tb-card-head{display:flex;align-items:center;justify-content:space-between;font-size:11px}
.tb-engine{font-weight:600;color:rgb(var(--sb-accent))}
.tb-extra{color:rgb(var(--sb-sub))}
.tb-card-text{font-size:12.5px;line-height:1.4;color:rgb(var(--sb-ink))}

/* 第 245 项：方案2（.tbl 紧凑表）整段 CSS 已删除，弹窗样式只保留方案1。 */
</style></head><body>

<div id="wallbg" aria-hidden="true"></div>

<div id="pane">
  <div id="miniToast" class="mini-toast" aria-live="polite"></div>

  <div class="bar">

    <div class="pasted-img-pill hide" id="pastedImgPill" title="__U_img_pill__" onclick="toggleImgPreview(event)">
      <img id="pastedImgThumb" src="" alt="pasted" />
      <span class="pasted-img-spinner hide" id="pastedImgSpinner"></span>
      <button class="pasted-img-del" id="pastedImgDel" onclick="clearPastedImg(event)" title="__U_img_pill_del__">×</button>
    </div>

    <span class="wtag hide" id="wtag"></span>

    <input id="q" spellcheck="false" autocomplete="off" placeholder="__U_q_ph__">

    <div class="actwrap" id="actWrap">
    <div class="act">

      <!-- 第 369 项：相机键和它的悬停胶囊单独包一层，胶囊只跟这颗键走 -->
      <div class="snipwrap" id="snipWrap">
      <button class="ab" id="btnSnip" title="__TIP_SNIP__" onclick="doSnip(false, event)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round"><path d="M6 3v12a2 2 0 0 0 2 2h12"/><path d="M18 21V9a2 2 0 0 0-2-2H4"/></svg>
      </button>

      <div class="act-pop" id="actPop">
        <button class="act-pop-btn" id="btnSnipTr" title="__U_snip_trans_hint__" onclick="doSnip(true, event, true)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 8l6 6"/><path d="M4 14l6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="M22 22l-5-10-5 10"/><path d="M14 18h6"/></svg>
          <span>__U_snip_tr_hover__</span>
        </button>
      </div>
      </div>

      <button class="ab" id="btnClip" title="__TIP_CLIP__" onclick="toggleClipPanel(event)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>
      </button>

      <button class="ab" id="btnTrans" title="__TIP_TRANS__" onclick="toggleTransPanel(event)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round"><path d="M5 8l6 6"/><path d="M4 14l6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="M22 22l-5-10-5 10"/><path d="M14 18h6"/></svg>
      </button>

      <button class="ab" id="btnDetail" title="__U_detail__">

        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"

             stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/>

             <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>

      </button>

      <button class="ab" id="btnSearch" title="__U_search__">

        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"

             stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/></svg>

      </button>

    </div>

    </div>

  </div>

  <div class="sc hide" id="sc"><span id="sct"></span><button id="scx" onclick="clearScope()" title="">×</button></div>

  <div class="jm hide" id="jm"></div>

  <div class="snip-box hide" id="snipBox">
    <div class="sb-head">
      <div class="sb-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3v12a2 2 0 0 0 2 2h12"/><path d="M18 21V9a2 2 0 0 0-2-2H4"/></svg>
        <span>__U_snip_title__</span>
        <span class="sb-meta" id="sbMeta"></span>
        <span class="sb-views" id="sbViews">
          <button class="sbv active" data-v="src" onclick="snipShow('src', event)">__U_snip_src__</button>
          <button class="sbv" data-v="dst" onclick="snipShow('dst', event)">__U_snip_dst__</button>
        </span>
      </div>
      <button class="sb-close" onclick="closeSnipBox(event)" title="__TIP_CB_CLOSE__">×</button>
    </div>
    <div class="sb-body">
      <div class="sb-thumb-wrap" id="sbThumbWrap">
        <img id="sbThumb" src="" alt="" />
      </div>
      <textarea id="sbText" spellcheck="false" placeholder="__U_snip_ph__"></textarea>
    </div>
    <div class="sb-foot">
      <button class="pbtn blue" id="sbBtnTrans" onclick="translateSnip(event)" title="__U_snip_trans_hint__">__U_snip_trans__</button>
      <button class="pbtn gray" id="sbBtnCopy" onclick="copySnipText(event)">__U_copy_text_btn__</button>
      <button class="pbtn gray" id="sbBtnCopyImg" onclick="copySnipImg(event)">__U_copy_img_btn__</button>
      <button class="pbtn gray" onclick="fillSnipToSearch(event)">__U_search_this__</button>
      <button class="pbtn gray" id="sbBtnSave" onclick="saveSnipImg(event)">__U_save_img__</button>
    </div>
  </div>

  <div class="img-preview-box hide" id="imgPreviewBox">
    <div class="ip-head">
      <div class="ip-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
        <span>__U_preview_title__</span>
        <span class="ip-meta" id="ipMeta"></span>
      </div>
      <div class="ip-hint">__U_preview_hint__</div>
      <button class="ip-close" onclick="closeImgPreview(event)" title="__U_preview_close__">×</button>
    </div>
    <div class="ip-body" onclick="closeImgPreview(event)">
      <img id="ipImg" src="" alt="preview" />
    </div>
  </div>

  <div class="clip-box hide" id="clipBox">
    <div class="cb-head">
      <div class="cb-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>
        <span>__TIP_CB_TITLE__</span>
      </div>
      <div class="cb-tabs">
        <button class="cbt active" data-kind="all" onclick="setClipFilter('all', event)">__TIP_CB_ALL__</button>
        <button class="cbt" data-kind="fav" onclick="setClipFilter('fav', event)">__TIP_CB_FAV__</button>
        <button class="cbt" data-kind="text" onclick="setClipFilter('text', event)">__TIP_CB_TEXT__</button>
        <button class="cbt" data-kind="image" onclick="setClipFilter('image', event)">__TIP_CB_IMAGE__</button>
        <button class="cbt" data-kind="file" onclick="setClipFilter('file', event)">__TIP_CB_FILE__</button>
      </div>
      <button class="cb-clear" onclick="clearClipHistory(event)" title="__TIP_CB_CLEAR_TITLE__">__TIP_CB_CLEAR__</button>
      <button class="sb-close" onclick="closeClipPanel(event)" title="__TIP_CB_CLOSE__">×</button>
    </div>
    <div class="cb-list" id="cbList"></div>
  </div>

  <div class="trans-box hide" id="transBox">
    <div class="tb-head">
      <div class="tb-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 8l6 6"/><path d="M4 14l6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="M22 22l-5-10-5 10"/><path d="M14 18h6"/></svg>
        <span>__TIP_TRANS__</span>
      </div>
      <div class="tb-actions">
        <select class="tb-lang-select" id="tbLangSelect" onchange="onTransLangChange(event)" title="__TIP_TRANS_TARGET__">
          <option value="auto" id="tbLangAuto">__U_trans_auto__</option>
          <option value="zh-CN">中文 (简体)</option>
          <option value="en">English</option>
          <option value="ja">日本語</option>
          <option value="ko">한국어</option>
          <option value="fr">Français</option>
          <option value="de">Deutsch</option>
          <option value="es">Español</option>
          <option value="ru">Русский</option>
          <option value="pt">Português</option>
          <option value="it">Italiano</option>
          <option value="ar">العربية</option>
          <option value="th">ไทย</option>
          <option value="vi">Tiếng Việt</option>
        </select>
        <button class="tb-run-btn" id="tbBtnRun" onclick="doMiniTranslate(event)" title="__TIP_RUN__">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>
          <span>__U_trans_title__</span>
        </button>
      </div>
      <button class="sb-close" onclick="closeTransPanel(event)" title="__TIP_CB_CLOSE__">×</button>
    </div>
    <div class="tb-results" id="tbResults"></div>
  </div>

  <div class="rh hide" id="rh">__U_recent__<span>__U_recent_hint__</span></div>

  <div id="list"></div>

  <div class="foot hide" id="foot"><span class="fl" id="ft"></span>

    <span class="fright"><span class="mver" onclick="showAbout()" title="__U_about__">__VER__</span>

    <span class="fr" id="fr">__U_detail_short__</span></span></div>

  <div class="empty hide" id="empty">__U_empty__<i>__U_empty_hint__</i></div>

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

  <div class="aname">__U_about_name__</div>

  <div class="aver">__VER__</div>

  <div class="ameta" id="ab_meta_s" data-nf="__FILES__" data-nt="__TOTAL__">

    <div id="ab_docs">__U_docs_line__</div>

    <div>Python <b>__PY__</b> · SQLite <b>__SQL__</b></div>

    <div id="ab_build">__U_build_date__</div>

  </div>

  <div class="anote" id="ab_note">__U_about_note__</div>

  <button class="pbtn gray" onclick="hideAbout()">__U_about_ok__</button>

</div>

<script>

var MINI_L = __MINI_L__, MINI_LANG = '__MINI_LANG__';

var WEB_SEARCH = __WEB_SEARCH__, WEB_MODE = null, WEB_Q = '';

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

function _miniBgHex(){

  /* 把 --sb-bg（'24 27 33' 或 '#18211B'）转成 '#rrggbb'，给原生刷那圈非客户区用 */

  try{

    var v=getComputedStyle(document.documentElement).getPropertyValue('--sb-bg');

    v=(v||'').trim(); if(!v)return null;

    var r,g,b;

    if(v.charAt(0)==='#'){

      if(v.length===4){r=parseInt(v[1]+v[1],16);g=parseInt(v[2]+v[2],16);b=parseInt(v[3]+v[3],16)}

      else{r=parseInt(v.slice(1,3),16);g=parseInt(v.slice(3,5),16);b=parseInt(v.slice(5,7),16)}

    }else{

      var p=v.split(/[\s,\/]+/).map(Number);

      if(p.length<3||p.some(isNaN))return null;

      r=p[0];g=p[1];b=p[2];

    }

    var h=function(n){n=Math.max(0,Math.min(255,n|0));return (n<16?'0':'')+n.toString(16)};

    return '#'+h(r)+h(g)+h(b);

  }catch(e){return null}

}

var _MD_TRIES=0;

function syncMiniDark(dark){

  /* 第 363 项：窗口最外那圈（DPI 150% 实测 11px）不在网页里，是 DWM 的非客户区。

     以前没人告诉原生「这一页现在什么底色」，于是那圈一直停在建窗时的白色 ——

     深色界面上就是右侧一条白边（第 359/363 项）。这里把真实底色报回去。

     注意 pywebview 的 bridge 注入晚于脚本执行，所以带重试。 */

  var tick=function(){

    var api=window.pywebview&&window.pywebview.api;

    if(api&&api.set_dark){try{api.set_dark(!!dark,_miniBgHex())}catch(e){}return}

    if(_MD_TRIES++<20)setTimeout(tick,250);

  };

  tick();

}

function applyLook(){

  /* 第 195 项：快搜窗之前是一套写死的白底 CSS，用户在设置里换配色/壁纸它完全不动。

     localStorage 与主界面同源，主界面 applyPalette 会把 7 个变量写进 sb_vars，

     这里直接复用，不再让后端维护第二份调色板。 */

  try{

    var look=null;

    try{ look=JSON.parse(localStorage.getItem('sb_vars')||'null'); }catch(e2){}

    var dark=!!(look&&look.dark);

    document.documentElement.setAttribute('data-theme', dark?'dark':'light');

    var keys=['bg','card','ink','sub','line','accent','side'];

    if(look&&look.vars){

      for(var i=0;i<keys.length;i++){

        var v=look.vars[keys[i]];

        if(v)document.documentElement.style.setProperty('--sb-'+keys[i], v);

      }

    }else{

      for(var j=0;j<keys.length;j++) document.documentElement.style.removeProperty('--sb-'+keys[j]);

    }

    /* 第 363 项：窗口最外那圈非客户区不在网页里，颜色只能由原生刷。
       谁比这里更清楚「本窗现在的底色」？没有。所以把算出来的 --sb-bg 报回去。 */

    syncMiniDark(dark);

  }catch(e){}

  try{

    fetch('/api/wall').then(function(r){return r.json()}).then(function(w){

      var el=document.getElementById('wallbg');

      if(!el||!w||!w.on||!w.scope_mini)return;

      el.style.backgroundImage='url(/api/wall/image?v='+(w.v||0)+')';

      /* 第 362 项：焦点位置 + 放大，和主窗同一套百分比语义 */
      el.style.backgroundPosition=(w.pos_x||50)+'% '+(w.pos_y||50)+'%';

      var _z=(w.zoom||100)/100;

      if(_z>1){

        el.style.transform='scale('+_z+')';

        el.style.transformOrigin=(w.pos_x||50)+'% '+(w.pos_y||50)+'%';

      }

      el.style.opacity=(Math.min(100,Math.max(0,w.opacity||15))/100);

      if(w.blur>0)el.style.filter='blur('+w.blur+'px)';

      document.body.classList.add('has-wall');

    }).catch(function(){});

  }catch(e){}

}

function applyMiniLang(){

  try{

    var q = Q || document.getElementById('q');

    q.placeholder = MT('ph');

    /* 第 203 项：applyMiniLang() 会在 DOMContentLoaded / __onShow 时重设占位符，
       网络搜索模式下必须保留引擎提示，否则用户在 Google 模式里看到的是「搜文件名」。 */
    if(MODE==='web' && WEB_MODE)
      q.placeholder = MT('web_ph').replace('{t}', String(WEB_MODE.title||WEB_MODE.key));

    var bd = document.getElementById('btnDetail'), bs = document.getElementById('btnSearch');

    if(bd) bd.title = MT('detail');

    if(bs) bs.title = MT('search');

    renderHomeHeader();

    renderJumpHint();

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

  /* 图标接口万一挂了，也不能回落成一张空白纸：给商店应用一个专用应用图标 */
  if(!dir && String(it.p||'').indexOf('shell:AppsFolder')===0)
    fb='<svg class="fic" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="4.5" fill="#5b7cfa"/><path d="M8 8.5h8M8 12h8M8 15.5h5" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>';
  var q = dir ? 'd=1' : 'e='+encodeURIComponent(it.e||'');

  /* 应用类按真实路径取图标：shell 解析快捷方式到目标，微信显示绿气泡而不是通用 exe 图标 */

  /* 第 248 项：虚拟商店应用 e 是空的，也要把 p 带上，后端才能问 Shell 要图标 */
  if(!dir && it.p && (it.e==='.exe'||it.e==='.lnk'||String(it.p).indexOf('shell:AppsFolder')===0)) q += '&p='+encodeURIComponent(it.p);

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

function rowCap(){

  /* 第 199 项：小窗能放几行，由宿主按「触发窗口有多大」算出的可用高度 MAXH 决定。

     以前写死 6 行，在 900 宽的文件夹里就已经占掉一半高度了。 */

  if(!SMALL)return 9;


  var avail=(MAXH||360)-170;

  return Math.max(2,Math.min(6,Math.floor(avail/38)));

}

function height(){

  /* 不再用「62+n*52+36」这种硬编码账本：改字号/行高/提示条都会让它对不上，

     少算几十像素就把最后一行切掉。直接量已经渲染出来的各段高度。 */

  var acc=0, el;

  el=document.querySelector('.bar'); if(el) acc+=el.offsetHeight;

  el=document.getElementById('sc'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('rh'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('jm'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('snipBox'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('imgPreviewBox'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('clipBox'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('transBox'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('list'); if(el) acc+=el.offsetHeight;

  el=document.getElementById('empty'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  el=document.getElementById('foot'); if(el&&!el.classList.contains('hide')) acc+=el.offsetHeight;

  return Math.max(acc, 120);

}

function resize(){

  var h=height(); if(MAXH&&h>MAXH)h=MAXH;

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

    +(withKey?'<span class="kbd">'+(HAS_DIALOG?esc(MT('dh_go')):(i<9?'Ctrl+'+(i+1):''))+'</span>':'')+'</div>';

}

function render(items,q,keep){

  var old=cur;

  MODE='result';

  rows=items||[];

  /* 第 308 项：换一批结果就把多选清干净，否则下标会指向别的文件 */
  MARKS=[]; ANCHOR=-1;

  /* 小窗只留 6 行：贴在文件夹角上的小框，结果太多会把窗口撑得比文件夹还高 */

  if(SMALL) rows=rows.slice(0,6);

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

     只有 renderHome() 才该让它在场。 */

  RH.classList.add('hide');

  if(rows.length){

    /* 第 230 项：想连正文一起搜，点右侧「正文」按钮即可，不再要求用户敲 c: 前缀。
       正文命中由 render() 的 sep 分组标注，页脚只留「展示更多」入口。 */

    FT.innerHTML='<span class=mic></span><span>'+esc(MT('more', {q: q}))+'</span>';

    FR.textContent=MT('fr');

    FOOT.classList.add('clickable');

    FOOT.onclick=detail;

  }else{

    FOOT.classList.remove('clickable');

    FOOT.onclick=null;

  }

  bind(); resize();

}

/* 空状态：打开就先给一屏「刚动过的文件 / 正开着的文件夹」。Listary V7 起就是
   这个做法——很多时候要找的东西就在眼前，搜索这一步可以直接省掉。

   第 234 项：原来这里是两块（上面「最近访问」目录、下面「最近打开」文件），
   两块的记录同源，并列时几乎一模一样，用户看到的就是重复列表。现在合成一块：
   目录在前（在对话框里它们就是「跳过去」的目标）、最近文件在后，按路径去重。 */
var HOME_SIG='';

/* 标题跟着场景换：在打开/保存对话框里整块都是跳转目标，标题就叫「最近访问」；
   平时是「打开」列表，叫「最近打开」。两套文案都是现成的，不加新键。 */
function renderHomeHeader(){
  if(RH.classList.contains('hide'))return;
  RH.innerHTML = esc(HAS_DIALOG?MT('dh_recent'):MT('recent'))
    + '<span>' + esc(HAS_DIALOG?MT('dh_hint'):MT('recent_hint')) + '</span>';
}

function renderHome(){
  if(Q.value.trim())return;            /* 已经在搜了，交给 render()，别把结果顶掉 */
  MODE='empty';
  /* 第 275 项：设置里关掉「最近记录显示」就什么都不列，窗口只剩输入框和底部提示。 */
  if(!HOME_SHOW){
    if(HOME_SIG==='off')return;
    HOME_SIG='off'; rows=[]; cur=-1;
    LIST.innerHTML=''; LIST.className='rec';
    RH.classList.add('hide');
    EMPTY.classList.remove('hide');
    FOOT.classList.add('hide'); FOOT.classList.remove('clickable'); FOOT.onclick=null;
    bind(); resize(); return;
  }
  var merged=[], seen={}, i, it;
  for(i=0;i<OPEN_DIRS.length;i++){
    it=OPEN_DIRS[i]; if(!it||!it.p)continue;
    var k1=String(it.p).toLowerCase();
    if(seen[k1])continue; seen[k1]=1;
    merged.push({t:'dir', e:'', p:it.p, n:it.n||'', dn:it.n||'', d:it.p});
  }
  for(i=0;i<RECENT.length;i++){
    it=RECENT[i]; if(!it||!it.p)continue;
    var k2=String(it.p).toLowerCase();
    if(seen[k2])continue; seen[k2]=1;   /* 同一路径只留一份，目录行优先 */
    merged.push(it);
  }
  var cap=rowCap();
  /* 第 275 项：空状态列几条由设置决定（qs_home_count，默认 4）。
     夹紧到 1..10，读不到就用 4。 */
  var want=parseInt(HOME_COUNT,10);
  if(!(want>0)) want=4;
  cap=Math.min(10, want);          /* 空状态按设置来，不受 rowCap 的 9 行上限压着 */
  /* 第 255 项：在上传/选择文件等贴角小窗场景（SMALL 模式），「最近访问」最多只列 2 个，
     避免窗口过高遮挡宿主对话框列表；未贴角的大窗按设置来。 */
  if(SMALL) cap=Math.min(cap, 2);
  if(merged.length>cap) merged=merged.slice(0,cap);
  var sig=merged.length+':'+(HAS_DIALOG?1:0)+':';
  for(i=0;i<merged.length;i++) sig+=merged[i].p+'|';
  /* 宿主每 2 秒推一次目录状态：内容没变就别重建 DOM，
     否则用户正用方向键选行时高亮会被抹掉。 */
  var homeCls='rec'+(HOME_COMPACT?' compact':'');
  if(sig===HOME_SIG && LIST.className===homeCls){ renderHomeHeader(); resize(); return; }
  HOME_SIG=sig;
  rows=merged; cur=-1;
  MARKS=[]; ANCHOR=-1;   /* 第 308 项：首页换了列表，多选下标作废 */
  var withKey=HAS_DIALOG;
  var html='';
  for(i=0;i<rows.length;i++) html+=rowHtml(rows[i],i,'',withKey);
  LIST.className=homeCls;
  LIST.innerHTML=html;
  RH.classList.toggle('hide', !rows.length);
  renderHomeHeader();
  EMPTY.classList.toggle('hide', !!rows.length);
  FOOT.classList.toggle('hide', !rows.length);
  FOOT.classList.remove('clickable');
  FOOT.onclick=null;
  if(rows.length){ FT.textContent=MT('foot_typing'); FR.textContent=MT('foot_esc'); }
  bind();
  /* 兜底：高度算得再准也可能超高，真超了从尾部砍行，绝不让窗口裁掉内容 */
  var guard=0;
  while(SMALL && MAXH && rows.length>1 && height()>MAXH && guard++<6){
    rows.pop();
    var h2='';
    for(i=0;i<rows.length;i++) h2+=rowHtml(rows[i],i,'',withKey);
    LIST.innerHTML=h2; bind();
  }
  resize();
}

/* 第 245 项：弹窗样式定稿，STYLE 变量与 .tbl 分支一并删除（只剩方案1）。 */
var OPEN_DIRS=[], HAS_DIALOG=false, SCOPE='', SMALL=false, MAXH=0, BODY_ON=__BODYSEARCH__;

/* 第 275 项：空状态紧凑显示（设置 → 快搜窗「最近记录紧凑显示」）。
   模板里先按服务端 settings 落一次值，之后宿主改设置会调 __setHomeCompact 现推，
   不用关窗重开。 */
var HOME_COMPACT=__HOME_COMPACT__;

/* 第 275 项：空状态列几条，也由设置决定（默认 4，1..20 夹紧）。 */
var HOME_COUNT=__HOME_COUNT__;

/* 第 275 项：最近记录整块显不显示（qs_home_show，默认显示）。 */
var HOME_SHOW=__HOME_SHOW__;

window.__setHomeCompact=function(on){
  on=!!on;
  if(HOME_COMPACT===on)return;
  HOME_COMPACT=on; HOME_SIG='';
  if(!Q.value.trim())renderHome();
};

window.__setHomeCount=function(n){
  n=parseInt(n,10); if(!(n>0))return;
  n=Math.min(10,n);
  if(HOME_COUNT===n)return;
  HOME_COUNT=n; HOME_SIG='';
  if(!Q.value.trim())renderHome();
};

window.__setHomeShow=function(on){
  on=!!on;
  if(HOME_SHOW===on)return;
  HOME_SHOW=on; HOME_SIG='';
  if(!Q.value.trim())renderHome();
};

/* 第 194 项：当前打开的文件夹（智能推荐）。只在空查询、还没打字时出现，

   用户一开始搜就把这一组让位给搜索结果，别跟 9 行结果抢高度。 */

function renderJumpHint(){

  var el=document.getElementById('jm');

  if(!el)return;

  el.classList.toggle('hide',!HAS_DIALOG);

  if(HAS_DIALOG) el.textContent=MT('jm_hint');

}

/* 「只在 XX 中搜索」提示条：从资源管理器里直接打字唤起来的时候显示。

   去掉限定 = 回到全局搜索，不用重新打字。 */

function renderScope(){

  var el=document.getElementById('sc'), t=document.getElementById('sct');

  if(!el||!t)return;

  if(!SCOPE){ el.classList.add('hide'); t.textContent=''; resize(); return; }

  t.textContent=MT('scope').replace('{p}',SCOPE);

  el.classList.remove('hide');

  resize();

}

function clearScope(){ SCOPE=''; renderScope(); schedule(); Q.focus(); }

/* 宿主调用的两个入口：自动弹出（带限定目录 + 可选首字）与按键兜底填入。 */

window.__autoShow=function(o){

  o=o||{};

  SCOPE=o.scope||'';

  SMALL=!!o.small; MAXH=parseInt(o.maxh||0,10)||0;


  try{ document.body.classList.toggle('small',SMALL); }catch(e){}


  renderScope();

  var q=(typeof o.q==='string')?o.q:'';

  if(q){ Q.value=q; cur=-1; schedule(); }

  else{ Q.value=''; seq++; MODE='empty'; HOME_SIG=''; renderHome(); fetchOpenDirs(); }

  Q.focus();

};

window.__autoQuery=function(q){

  if(typeof q!=='string'||!q)return;

  Q.value=q; cur=-1; Q.focus(); schedule();

};

/* 对话框状态变了（用户点了保存/取消）：宿主推一下，别等下一次轮询。 */

window.__onDialog=function(){ fetchOpenDirs(); };

/* 第 199 项：显示期间宿主每 2 秒推一次，关掉的文件夹立刻从列表里消失。 */

window.__onDirs=function(){ fetchOpenDirs(); };

/* 第 229 项：宿主在锚点对话框变大变小时推过来 —— 只更新高度上限并重排，

   不清搜索词（__autoShow 会清，不能用）。 */

window.__rescale=function(o){

  o=o||{};

  if(o.maxh){ MAXH=parseInt(o.maxh,10)||MAXH; }

  resize();

};

function fetchOpenDirs(){
  fetch('/api/open_dirs').then(function(r){return r.json()}).then(function(d){
    OPEN_DIRS=d.items||[]; HAS_DIALOG=!!d.dialog;
    renderJumpHint();
    renderHome();
  }).catch(function(){ OPEN_DIRS=[]; HAS_DIALOG=false; renderJumpHint(); renderHome(); });
}

function flash(msg){

  try{ FR.textContent=msg; setTimeout(function(){ FR.textContent=MT('foot_typing'); },2600); }catch(e){}

}

/* 点了条目 / 按了 Ctrl+G：先尝试把当前文件对话框切过去；没有对话框就退回

   「在资源管理器里打开」，绝不让用户点了没反应。 */

function jumpDir(p){

  if(!p)return;

  fetch('/api/dialog_jump',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({p:p})})

    .then(function(r){return r.json()}).then(function(d){

      if(d&&d.ok){

        /* 第 229 项：目录结果已自动导航进该目录（对话框保持打开）。

           不 hide —— 用户可能还要在结果里继续挑。 */

        if(d.mode==='nav'){ flash(MT('nav_hint')); return; }

        /* 选择文件夹对话框没有文件名框：路径已填进地址栏，让用户按一次回车。

           这里不能 hide，否则用户看不到那个对话框。 */

        if(d.mode==='addr'){ flash(MT('ad_hint')); return; }

        if(API&&API.hide)API.hide(); return; }

      flash(MT(d&&d.reason==='no_dialog'?'qs_none':'qs_fail'));

      try{ if(API&&API.open_path)API.open_path(p); }catch(e){}

    }).catch(function(){ flash(MT('qs_fail')); });

}

/* 带搜索词时跳当前选中项所在目录；空查询时跳「当前打开的文件夹」第一条。 */

function jumpCur(){

  var p='';

  if(Q.value.trim() && rows.length){ var it=rows[cur<0?0:cur]; p=it?it.p:''; }

  if(!p && OPEN_DIRS.length) p=OPEN_DIRS[0].p;

  if(p)jumpDir(p); else flash(MT('qs_none'));

}

function fetchRecent(){

  fetch('/api/recent').then(function(r){return r.json()}).then(function(d){

    if(MODE!=='empty')return;         /* 用户已经开始打字了，别把结果顶掉 */

    RECENT=d.items||[];

    renderHome();

  }).catch(function(){});

}

function bind(){

  var els=LIST.querySelectorAll('.row');

  for(var i=0;i<els.length;i++){

    els[i].onmouseenter=function(){ sel(+this.dataset.i); };

    /* 第 308 项：Ctrl/⌘ 点击切换选中，Shift 点击选一段（和资源管理器一致）。
       普通单击仍然是「打开」，和多选互不干扰。 */
    els[i].onclick=function(ev){
      var i=+this.dataset.i;
      if(ev&&(ev.ctrlKey||ev.metaKey)){ toggleMark(i); return; }
      if(ev&&ev.shiftKey){ markRange(i); return; }
      clearMarks();
      open(i);
    };
    els[i].oncontextmenu=function(ev){ ev.preventDefault(); showCMenu(ev.clientX, ev.clientY, +this.dataset.i); };

  }

}

/* ---- 多选（第 308 项） ---- */
var MARKS=[], ANCHOR=-1;

function paintMarks(){
  var els=LIST.querySelectorAll('.row');
  for(var k=0;k<els.length;k++) els[k].classList.toggle('mark', MARKS.indexOf(+els[k].dataset.i)>=0);
}
function markInfo(){
  if(!MARKS.length) return '';
  return MT('sel_n').replace('{n}', String(MARKS.length));
}
function toggleMark(i){
  if(i<0||i>=rows.length)return;
  var at=MARKS.indexOf(i);
  if(at>=0) MARKS.splice(at,1); else { MARKS.push(i); ANCHOR=i; }
  sel(i); paintMarks();
  if(MARKS.length) toast(markInfo(), 1100);
}
function markRange(i){
  if(i<0||i>=rows.length)return;
  if(ANCHOR<0||ANCHOR>=rows.length) ANCHOR=i;
  var a=Math.min(ANCHOR,i), b=Math.max(ANCHOR,i);
  MARKS=[];
  for(var k=a;k<=b;k++) MARKS.push(k);
  sel(i); paintMarks();
  toast(markInfo(), 1100);
}
function clearMarks(){
  if(!MARKS.length) return;
  MARKS=[]; ANCHOR=-1; paintMarks();
}
/* 复制路径：先走服务端剪贴板（WebView2 里 navigator.clipboard 常常静默失败），
   失败再退回浏览器 API。第 305 项同一套路。 */
function copyText(t, okMsg){
  var done=false;
  function ok(){ if(done)return; done=true; toast(okMsg, 1500); }
  function fb(){
    try{
      if(navigator.clipboard&&navigator.clipboard.writeText){
        navigator.clipboard.writeText(t).then(ok).catch(function(){ toast(MT('copy_fail'),1800); });
        return;
      }
    }catch(e){}
    toast(MT('copy_fail'),1800);
  }
  fetch('/api/snip/copy_text',{method:'POST',
    headers:{'Content-Type':'application/json','Sec-Fetch-Site':'same-origin'},
    body:JSON.stringify({text:t})})
    .then(function(r){return r.json()}).then(function(d){ if(d&&d.ok) ok(); else fb(); })
    .catch(fb);
}
function dirOf(p){
  var s=String(p||''); var i=Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'));
  return i>0? s.slice(0,i): s;
}
/* Ctrl+C / Ctrl+Shift+C：多选时复制全部，单选时复制当前项 */
function copySelection(dirs){
  var picked = MARKS.length? MARKS.slice().sort(function(a,b){return a-b;}) : [cur];
  var vals=[];
  for(var k=0;k<picked.length;k++){
    var it=rows[picked[k]];
    if(!it||!it.p)continue;
    vals.push(dirs? dirOf(it.p): it.p);
  }
  if(!vals.length){ toast(MT('copy_none'),1500); return; }
  copyText(vals.join('\r\n'), dirs? MT('copied_dir'): (vals.length>1? MT('copied_n').replace('{n}',String(vals.length)): MT('copied_one')));
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

  if(MODE==='web'){ openWeb(); return; }

  var it=rows[i]; if(!it)return;

  /* 第 196 项：检测到「另存为/打开」对话框时，左键/回车＝把对话框切到该目录。

     用户在另存为时点一条结果，本意是「存到这里」，不是「再打开一个文件」。

     没有对话框时行为不变（打开文件）。 */

  if(HAS_DIALOG){ jumpDir(it.p); return; }

  if(API&&API.open_path)API.open_path(it.p);

}

function detail(){

  if(MODE==='web'){ openWeb(); return; }

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

  fetch('/api/mini?q='+encodeURIComponent(v)+'&scope='+encodeURIComponent(SCOPE)).then(function(r){return r.json()})

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

  LIST.innerHTML='<div class="empty">'+esc(BODY_ON?MT('busy'):MT('busy_all'))+'<i>'+esc(q)+'</i></div>';

  EMPTY.classList.add('hide');

  FOOT.classList.add('hide');

  resize();

}

function doDeep(v,needDeep){

  var my=++seq;

  busy(v);

  /* 第 230 项修订：按钮必须能真正管控正文 —— 关着就纯文件名、一条正文都不搜；

     点亮才带 body=1&fb=1（名字+正文一起，且绕过 first_app 归零）。 */

  var bdq = BODY_ON ? '&body=1&fb=1' : '';

  var u='/api/mini?q='+encodeURIComponent(v)+bdq+(needDeep?'&deep=1':'')+'&scope='+encodeURIComponent(SCOPE);

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

/* 第 203 项：网络搜索。用户原话：「输入 g 然后空格，一下就变成了 Google
   搜索，在简要搜索框里面就能谷歌搜索，然后输入你要搜索的东西，按 enter
   就会打开浏览器帮你去搜索」。关键字和 URL 全在本机设置里，默认不联网。 */
function fetchWeb(){
  fetch('/api/web_search').then(function(r){return r.json()}).then(function(d){
    if(d && d.items) WEB_SEARCH=d.items;
  }).catch(function(){});
}
function matchWeb(v){
  if(!WEB_SEARCH||!WEB_SEARCH.length)return null;
  var s=String(v||''), i, e, k;
  for(i=0;i<WEB_SEARCH.length;i++){
    e=WEB_SEARCH[i]; if(!e||e.enabled===false||!e.key)continue;
    k=String(e.key).toLowerCase();
    if(s.length>=k.length+1 && s.slice(0,k.length).toLowerCase()===k
       && s.charAt(k.length)===' ') return {eng:e,q:s.slice(k.length+1)};
  }
  return null;
}
function leaveWeb(){
  if(!WEB_MODE)return;
  WEB_MODE=null; WEB_Q='';
  var w=document.getElementById('wtag'); if(w)w.classList.add('hide');
  Q.placeholder=MT('ph');
  renderScope();
}
/* 第 355 项：命令关键字。输入 shot / verify / cmd / psh 直接做事，和 Listary 的
   「关键字 → 动作」同一套思路，但只做白名单内的本机动作，不执行任意命令。 */
var CMDS = [
  {k:'shot',   label:'cmd_shot'},
  {k:'verify', label:'cmd_verify'},
  {k:'cmd',    label:'cmd_cmd',  args:'path'},
  {k:'psh',    label:'cmd_psh',  args:'path'}
];
var CMD_MODE = null;

function matchCmd(v){
  var s = String(v||''), i, e;
  for(i=0;i<CMDS.length;i++){
    e = CMDS[i];
    if(s === e.k) return {cmd:e, arg:''};
    if(s.length > e.k.length && s.slice(0, e.k.length) === e.k
       && s.charAt(e.k.length) === ' ') return {cmd:e, arg:s.slice(e.k.length+1).trim()};
  }
  return null;
}

function cmdTarget(arg){
  if(arg) return arg;
  if(typeof SCOPE === 'string' && SCOPE) return SCOPE;
  return '';
}

function renderCmd(m){
  MODE = 'cmd'; CMD_MODE = m;
  seq++; clearTimeout(timer); clearTimeout(deeptimer);
  rows = []; cur = -1;
  var ids=['rh','empty','foot','sc','jm'], i, el;
  for(i=0;i<ids.length;i++){ el=document.getElementById(ids[i]); if(el) el.classList.add('hide'); }
  var lbl = MT(m.cmd.label);
  var sub = m.arg ? m.arg : (cmdTarget('') || MT('cmd_here'));
  LIST.className='';
  LIST.innerHTML = '<div class="row" id="cmdRow">'
    + '<span class="ico">' + esc('>') + '</span>'
    + '<span class="txt"><span class="nm">' + esc(lbl) + '</span></span>'
    + '<span class="kbd">Enter</span></div>'
    + '<div class="empty">' + esc(sub) + '</div>';
  var r = document.getElementById('cmdRow');
  if(r) r.onclick = function(){ runCmd(); };
  resize();
}

function leaveCmd(){
  if(!CMD_MODE) return;
  CMD_MODE = null;
  if(MODE === 'cmd') MODE = 'empty';
}

function runCmd(){
  var m = CMD_MODE; if(!m) return;
  var done = false;
  function bye(){ if(done) return; done = true; try{ if(API && API.hide) API.hide(); }catch(e){} }
  if(m.cmd.k === 'shot'){
    fetch('/api/snip/trigger', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ocr:true})}).then(bye).catch(bye);
    setTimeout(bye, 600);
    return;
  }
  if(m.cmd.k === 'verify'){
    fetch('/api/index/verify', {method:'POST', headers:{'Content-Type':'application/json'},
      body:'{}'}).then(bye).catch(bye);
    setTimeout(bye, 600);
    return;
  }
  var dir = cmdTarget(m.arg);
  fetch('/api/shell_open', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({kind:m.cmd.k, dir:dir})}).then(function(r){return r.json()})
    .then(function(d){ if(!d || !d.ok) toast(MT('cmd_fail')); bye(); })
    .catch(function(){ toast(MT('cmd_fail')); bye(); });
  setTimeout(bye, 600);
}

function renderWeb(m){
  WEB_MODE=m.eng; WEB_Q=m.q; MODE='web';
  seq++; clearTimeout(timer); clearTimeout(deeptimer);
  rows=m.q?[{web:true}]:[]; cur=rows.length?0:-1;
  var ids=['rh','empty','foot','sc','jm'], i, el;
  for(i=0;i<ids.length;i++){el=document.getElementById(ids[i]); if(el)el.classList.add('hide');}
  var name=String(m.eng.title||m.eng.key);
  var w=document.getElementById('wtag');
  if(w){ w.textContent=name; w.classList.remove('hide'); }
  Q.placeholder=MT('web_ph').replace('{t}',name);
  LIST.className='';
  LIST.innerHTML=m.q?('<div class="row webrow'+(cur===0?' on':'')+'" data-i="0">'
    +'<span class="ico wico">'+esc(name.charAt(0))+'</span>'
    +'<span class="txt"><span class="nm">'+esc(m.q)+'</span></span>'
    +'<span class="kbd">Ctrl+1</span></div>'):'';
  bind(); resize();
}
function openWeb(){
  if(!WEB_MODE)return;
  var q=String(WEB_Q||'').trim();
  if(!q){ Q.focus(); return; }
  /* URL 由后端按本机配置拼接并校验，前端只传 key + 查询词 */
  var done=false;
  function bye(){ if(done)return; done=true; try{ if(API&&API.hide)API.hide(); }catch(e){} }
  fetch('/api/web_search_open',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:WEB_MODE.key,query:q})})
    .then(function(r){return r.json()}).then(function(d){
      if(!d||!d.ok)console.warn('web search failed',d); bye();
    }).catch(function(e){console.warn('web search failed',e); bye();});
  /* 本地接口再慢也不会让快搜窗赖在屏幕上 */
  setTimeout(bye, 200);
}


function schedule(){

  /* 关键字 + 空格：切换成网络搜索，不发本地检索请求。
     命中后把「g 」这类前缀吃掉，输入框里只留查询词 —— 引擎名已经在
     左边的标签上（Listary 就是这个样子），不必让用户看着两遍。 */
  var cm = matchCmd(Q.value);
  if(cm){ renderCmd(cm); return; }
  leaveCmd();

  var wm=matchWeb(Q.value);
  if(wm){ WEB_MODE=wm.eng; Q.value=wm.q; renderWeb(wm); return; }
  /* 已经在引擎模式里：继续输入就沿用当前引擎；清空（退格到空）才退回本地搜索 */
  if(WEB_MODE){
    var wq=Q.value;
    if(!wq.trim()){ leaveWeb(); }
    else { renderWeb({eng:WEB_MODE,q:wq}); return; }
  }
  leaveWeb();

  var tb = document.getElementById('transBox');
  if(tb && !tb.classList.contains('hide')){
    LIST.innerHTML = '';
    FOOT.classList.add('hide');
    RH.classList.add('hide');
    var em = document.getElementById('empty');
    if(em) em.classList.add('hide');
    scheduleTrans();
    return;
  }

  renderHome();

  clearTimeout(timer); clearTimeout(deeptimer);

  scheduleTrans();

  var v=Q.value.trim();

  /* 第 306 项：清空到空必须把首页 DOM 重建一次。
     renderHome() 里有一层「HOME_SIG 和上次一样 + LIST.className 一样就直接返回」的缓存，
     而搜索结果的容器 class 也是 'rec' —— 沿用它就会命中这层缓存，
     于是输入框已经空了、列表里却还挂着上一条搜索的 5 条结果和「展示更多 “打”」的页脚。
     把 HOME_SIG 打成空串，逼它重算。 */
  if(!v){ seq++; MODE='empty'; HOME_SIG=''; renderHome(); return; }

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

    if(pastedImgData && !pastedImgOcred){
      ocrPastedImage();
      return;
    }

    var tb = document.getElementById('transBox');
    if(tb && !tb.classList.contains('hide')){
      doMiniTranslate();
      return;
    }

    if(e.ctrlKey||e.metaKey){ detail(); return; }

    if(MODE === 'cmd'){ runCmd(); return; }

    if(rows.length) open(cur<0?0:cur); else detail();

  }

  /* 第 308 项：标准键。Ctrl+C 复制选中项路径（多选时一次复制多条）；
     输入框里自己选中了文字就让浏览器照常复制，别抢。 */
  else if((k==='c'||k==='C') && (e.ctrlKey||e.metaKey)){
    try{
      if(typeof Q.selectionStart==='number' && Q.selectionStart!==Q.selectionEnd) return;
    }catch(err){}
    e.preventDefault();
    copySelection(!!e.shiftKey);
  }

  else if(k==='Delete'){
    if(pastedImgData){
      e.preventDefault();
      clearPastedImg();
      return;
    }
    if(MARKS.length){ e.preventDefault(); clearMarks(); }
  }

  else if(k==='Backspace'){
    if(!Q.value && pastedImgData){
      e.preventDefault();
      clearPastedImg();
      return;
    }
  }

  else if(k==='Escape'){
    var pb = document.getElementById('imgPreviewBox');
    if(pb && !pb.classList.contains('hide')){
      e.preventDefault();
      closeImgPreview();
      return;
    }
    if(pastedImgData){
      e.preventDefault();
      clearPastedImg();
      return;
    }
    e.preventDefault(); if(API&&API.hide)API.hide();
  }

  else if(e.ctrlKey&&k>='1'&&k<='9'){ e.preventDefault(); var i=+k-1; if(rows[i])open(i); }

  else if(e.ctrlKey&&(k==='g'||k==='G')){ e.preventDefault(); jumpCur(); }

  else if(k==='Tab'){ e.preventDefault(); detail(); }

});

document.getElementById('btnDetail').onclick=detail;
document.addEventListener('click', function(e){
  if(CMENU && !CMENU.contains(e.target)) hideCMenu();
});
document.addEventListener('contextmenu', function(e){
  if(CMENU && !CMENU.contains(e.target) && !(e.target.closest && e.target.closest('.row'))) hideCMenu();
});
document.addEventListener('keydown', function(e){
  if(e.key==='Escape'){
    hideCMenu();
    var pb = document.getElementById('imgPreviewBox');
    if(pb && !pb.classList.contains('hide')){
      closeImgPreview(e);
      return;
    }
    var sb = document.getElementById('snipBox');
    if(sb && !sb.classList.contains('hide')){
      closeSnipBox(e);
    }
  } else if(e.key==='Enter'){
    var sb = document.getElementById('snipBox');
    if(sb && !sb.classList.contains('hide') && document.activeElement !== document.getElementById('sbText') && document.activeElement !== Q){
      copySnipText(e);
    }
  }
});

var snipPollTimer = null;
var pastedImgData = null;
var pastedImgOcred = false;

function setPastedImg(dataUrl){
  if(!dataUrl) return;
  pastedImgData = dataUrl;
  pastedImgOcred = false;
  var pill = document.getElementById('pastedImgPill');
  var thumb = document.getElementById('pastedImgThumb');
  var spinner = document.getElementById('pastedImgSpinner');
  if(thumb) thumb.src = dataUrl;
  if(spinner) spinner.classList.add('hide');
  if(pill) pill.classList.remove('hide');
  Q.placeholder = '__U_snip_scan__';
  Q.focus();
}

function toggleImgPreview(e){
  if(e && e.stopPropagation) e.stopPropagation();
  if(!pastedImgData) return;
  var box = document.getElementById('imgPreviewBox');
  if(!box) return;
  if(box.classList.contains('hide')){
    showImgPreview();
  } else {
    closeImgPreview();
  }
}

function showImgPreview(){
  if(!pastedImgData) return;
  var box = document.getElementById('imgPreviewBox');
  var img = document.getElementById('ipImg');
  var meta = document.getElementById('ipMeta');
  if(img) img.src = pastedImgData;
  if(box) box.classList.remove('hide');
  if(img && meta){
    var naturalImg = new Image();
    naturalImg.onload = function(){
      meta.textContent = naturalImg.width + ' × ' + naturalImg.height;
    };
    naturalImg.src = pastedImgData;
  }
  resize();
}

function closeImgPreview(e){
  if(e && e.stopPropagation) e.stopPropagation();
  var box = document.getElementById('imgPreviewBox');
  if(box && !box.classList.contains('hide')){
    box.classList.add('hide');
    resize();
  }
}

function clearPastedImg(e){
  if(e && e.stopPropagation) e.stopPropagation();
  closeImgPreview();
  pastedImgData = null;
  pastedImgOcred = false;
  var pill = document.getElementById('pastedImgPill');
  var thumb = document.getElementById('pastedImgThumb');
  var spinner = document.getElementById('pastedImgSpinner');
  if(pill) pill.classList.add('hide');
  if(thumb) thumb.src = '';
  if(spinner) spinner.classList.add('hide');
  try {
    Q.placeholder = (typeof MT === 'function' ? MT('placeholder') : '') || '';
  } catch(err){}
}

function ocrPastedImage(){
  closeImgPreview();
  if(!pastedImgData) return;
  var spinner = document.getElementById('pastedImgSpinner');
  if(spinner) spinner.classList.remove('hide');
  var api = window.pywebview && window.pywebview.api;
  var promise = null;
  if(api && api.ocr_image){
    try {
      promise = api.ocr_image(pastedImgData);
    } catch(e) {
      promise = null;
    }
  }
  if(!promise){
    promise = fetch('/api/snip/ocr_image', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({image: pastedImgData})
    }).then(function(r){ return r.json(); });
  }
  var timer = setTimeout(function(){
    if(spinner) spinner.classList.add('hide');
    toast('__U_snip_timeout__');
  }, 8000);
  promise.then(function(res){
    clearTimeout(timer);
    if(spinner) spinner.classList.add('hide');
    pastedImgOcred = true;
    if(res && res.ok && res.text){
      var txt = res.text.trim();
      if(txt){
        Q.value = txt;
        clearPastedImg();
        schedule();
      } else {
        toast('__U_snip_none__');
      }
    } else {
      toast('__U_snip_none__');
    }
  }).catch(function(err){
    clearTimeout(timer);
    if(spinner) spinner.classList.add('hide');
    toast('__U_snip_fail__');
  });
}

window.addEventListener('paste', function(e){
  var cd = e.clipboardData || window.clipboardData;
  var items = cd && cd.items;
  if(items){
    for(var i=0; i<items.length; i++){
      if(items[i].type && items[i].type.indexOf('image') !== -1){
        e.preventDefault();
        var blob = items[i].getAsFile();
        if(blob){
          var reader = new FileReader();
          reader.onload = function(evt){
            setPastedImg(evt.target.result);
          };
          reader.readAsDataURL(blob);
          return;
        }
      }
    }
  }
  var api = window.pywebview && window.pywebview.api;
  if(api && api.read_clipboard_image){
    api.read_clipboard_image().then(function(res){
      if(res && res.has_image && res.data_url){
        setPastedImg(res.data_url);
      }
    }).catch(function(){});
  } else {
    fetch('/api/snip/clipboard_img').then(function(r){ return r.json(); }).then(function(res){
      if(res && res.has_image && res.data_url){
        setPastedImg(res.data_url);
      }
    }).catch(function(){});
  }
});

function doSnip(ocr, e, translate){
  if(e) e.stopPropagation();
  var withOcr = !!ocr;
  var withTr = !!translate;
  // 第 369 项：截图翻译。译文由原生卡片贴在选区原位显示，快搜窗让位、不抢焦点。
  try{ if(snipWrapEl) snipWrapEl.classList.remove('hover'); }catch(_e){}
  var startTs = Date.now() / 1000;
  var api = window.pywebview && window.pywebview.api;
  if(api && api.trigger_snip){
    try {
      Promise.resolve(api.trigger_snip(withOcr || withTr, withTr)).then(function(id){
        startSnipPoll(startTs, id);
      }).catch(function(){ startSnipPoll(startTs); });
    } catch(err){ startSnipPoll(startTs); }
  } else {
    fetch('/api/snip/trigger', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ocr: withOcr || withTr, translate: withTr})})
      .then(function(r){ return r.json(); })
      .then(function(d){ startSnipPoll(startTs, d && d.snip_id); })
      .catch(function(){ startSnipPoll(startTs); });
  }
}

/* 第 369 项：相机键自己的悬停浮层 —— 只认这颗键，停 600ms 冒出「截图翻译」，移开就收。
   锚点是 #snipWrap（相机键 + 胶囊那个盒子），**不是整排按钮**：
   挂在整排上会让鼠标划过「详情 / 搜索」也冒出截图翻译（用户 2026-11-20 实测报的）。
   胶囊是 snipWrap 的 DOM 子节点（绝对定位在它左侧），所以从按键挪到胶囊不算离开、
   不会被中途收走；再留 200ms 宽限防手抖/防抖掉帧。 */
var snipWrapEl = document.getElementById('snipWrap');
var actHoverTimer = null;
(function(){
  if(!snipWrapEl) return;
  var hideTimer = null;
  snipWrapEl.addEventListener('mouseenter', function(){
    if(hideTimer){ clearTimeout(hideTimer); hideTimer = null; }
    if(actHoverTimer) clearTimeout(actHoverTimer);
    actHoverTimer = setTimeout(function(){ snipWrapEl.classList.add('hover'); }, 600);
  });
  snipWrapEl.addEventListener('mouseleave', function(){
    if(actHoverTimer){ clearTimeout(actHoverTimer); actHoverTimer = null; }
    if(hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(function(){
      hideTimer = null;
      snipWrapEl.classList.remove('hover');
    }, 200);
  });
})();

function startSnipPoll(startTs, expectedId){
  if(snipPollTimer) clearInterval(snipPollTimer);
  var tries = 0;
  snipPollTimer = setInterval(function(){
    tries++;
    if(tries > 80){ clearInterval(snipPollTimer); snipPollTimer = null; return; }
    fetch('/api/snip/result?_t=' + Date.now()).then(function(r){ return r.json(); }).then(function(res){
      if(res && res.status === 'done'){
        if(expectedId && res.snip_id && res.snip_id < expectedId) return;
        if(res.ts && res.ts < startTs) return;
        clearInterval(snipPollTimer);
        snipPollTimer = null;
        if(window.__onSnipDone) window.__onSnipDone(res);
      }
    }).catch(function(){});
  }, 250);
}

window.__onSnipDone = function(st){
  if(st && st.status === 'done'){
    // 第 369 项：翻译模式的译文由原生卡片负责，快搜窗既不再挂缩略图、也不弹识字面板
    if(st.do_translate) return;
    if(snipPollTimer){ clearInterval(snipPollTimer); snipPollTimer = null; }
    var dataUrl = st.thumb_data_url;
    var attachThumb = function(url){
      setPastedImg(url);
      if(st.do_ocr){
        pastedImgOcred = true;
        if(st.text && st.text.trim()){
          Q.value = st.text.trim();
          schedule();
        } else {
          ocrPastedImage();
        }
      } else {
        toast('__U_snip_ready__');
      }
    };
    if(dataUrl){
      attachThumb(dataUrl);
    } else {
      fetch('/api/snip/thumb?_t=' + (st.ts || Date.now())).then(function(r){ return r.blob(); }).then(function(blob){
        var reader = new FileReader();
        reader.onload = function(evt){
          attachThumb(evt.target.result);
        };
        reader.readAsDataURL(blob);
      }).catch(function(){
        if(st.do_ocr) renderSnip(st);
      });
    }
    return;
  }
  fetch('/api/snip/result?_t=' + Date.now()).then(function(r){ return r.json(); }).then(function(res){
    if(res && res.status === 'done'){
      if(window.__onSnipDone) window.__onSnipDone(res);
    }
  }).catch(function(){});
};

var snipSrcText = '', snipDstText = '', snipView = 'src';

/* 第 361 项：截图翻译。原文/译文只换 textarea 里的内容，
   复制按钮直接拿 textarea 的值 —— 所以译文天然可复制，也能一键切回原文。 */
function snipSetView(v){
  snipView = (v === 'dst' && snipDstText) ? 'dst' : 'src';
  var el = document.getElementById('sbText');
  if(el) el.value = (snipView === 'dst') ? snipDstText : snipSrcText;
  var wrap = document.getElementById('sbViews');
  if(wrap){
    var bs = wrap.querySelectorAll('.sbv');
    for(var i=0;i<bs.length;i++){
      bs[i].className = 'sbv' + (bs[i].getAttribute('data-v') === snipView ? ' active' : '');
    }
    wrap.classList.toggle('on', !!(snipDstText));
  }
}

function snipShow(v, e){
  if(e) e.stopPropagation();
  snipSetView(v);
}

function translateSnip(e){
  if(e) e.stopPropagation();
  var el = document.getElementById('sbText');
  var txt = ((el && el.value) || snipSrcText || '').trim();
  if(!txt){ toast('__U_snip_none__'); return; }
  var btn = document.getElementById('sbBtnTrans');
  if(btn){ btn.disabled = true; btn.innerText = '__U_snip_trans_doing__'; }
  fetch('/api/snip/translate', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({text: txt})})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(btn){ btn.disabled = false; btn.innerText = '__U_snip_trans__'; }
      if(d && d.ok && d.text){
        snipSrcText = d.src || txt;
        snipDstText = d.text;
        snipSetView('dst');
      } else {
        toast('__U_snip_trans_none__');
      }
    }).catch(function(){
      if(btn){ btn.disabled = false; btn.innerText = '__U_snip_trans__'; }
      toast('__U_snip_trans_none__');
    });
}

function renderSnip(res){
  var box = document.getElementById('snipBox');
  if(!box) return;
  box.classList.remove('hide');
  var txtEl = document.getElementById('sbText');
  snipSrcText = res.text || ''; snipDstText = '';
  var vw = document.getElementById('sbViews');
  if(vw){ vw.classList.remove('on'); }
  var vt = document.getElementById('sbBtnTrans');
  if(vt){ vt.disabled = false; vt.innerText = '__U_snip_trans__'; }
  if(txtEl) txtEl.value = snipSrcText;
  var meta = document.getElementById('sbMeta');
  if(meta) meta.innerText = (res.lines ? '__U_snip_lines__'.replace('{n}', res.lines.length) : '') + (res.img_size ? ' · ' + res.img_size[0] + 'x' + res.img_size[1] : '');
  var img = document.getElementById('sbThumb');
  if(img) img.src = '/api/snip/thumb?_t=' + (res.ts || Date.now());
  var copyBtn = document.getElementById('sbBtnCopy');
  if(copyBtn){ copyBtn.innerText = '__U_copy_text_btn__'; copyBtn.focus(); }
  resize();
}

function closeSnipBox(e){
  if(e) e.stopPropagation();
  var box = document.getElementById('snipBox');
  if(box) box.classList.add('hide');
  resize();
}

function copySnipText(e){
  if(e) e.stopPropagation();
  var txt = document.getElementById('sbText').value;
  fetch('/api/snip/copy_text', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text: txt})})
  .then(function(r){ return r.json(); }).then(function(d){
    var btn = document.getElementById('sbBtnCopy');
    if(btn) btn.innerText = '__U_snip_copied__';
    setTimeout(function(){ if(btn) btn.innerText = '__U_copy_text_btn__'; }, 1500);
  }).catch(function(){
    if(navigator.clipboard) navigator.clipboard.writeText(txt);
  });
}

function copySnipImg(e){
  if(e) e.stopPropagation();
  var btn = document.getElementById('sbBtnCopyImg');
  if(btn){
    var orig = btn.innerText;
    btn.innerText = '__U_snip_img_copied__';
    setTimeout(function(){ btn.innerText = orig; }, 1500);
  }
}

function fillSnipToSearch(e){
  if(e) e.stopPropagation();
  var txt = (document.getElementById('sbText').value || '').replace(/\r?\n+/g, ' ').trim();
  closeSnipBox();
  if(Q){
    Q.value = txt;
    Q.focus();
    schedule();
  }
}

function saveSnipImg(e){
  if(e) e.stopPropagation();
  fetch('/api/snip/save', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})
  .then(function(r){ return r.json(); }).then(function(d){
    var btn = document.getElementById('sbBtnSave');
    if(btn){
      var orig = btn.innerText;
      btn.innerText = d.ok ? '__U_snip_saved__' : '__U_snip_save_fail__';
      setTimeout(function(){ btn.innerText = orig; }, 2000);
    }
  }).catch(function(){});
}

var curClipKind = 'all';

function toggleClipPanel(e){
  if(e && e.stopPropagation) e.stopPropagation();
  var box = document.getElementById('clipBox');
  if(!box) return;
  if(!box.classList.contains('hide')){
    closeClipPanel();
  } else {
    closeSnipBox();
    closeTransPanel();
    closeImgPreview();
    box.classList.remove('hide');
    fetchClipHistory(curClipKind);
    resize();
  }
}

function closeClipPanel(e){
  if(e && e.stopPropagation) e.stopPropagation();
  var box = document.getElementById('clipBox');
  if(box && !box.classList.contains('hide')){
    box.classList.add('hide');
    resize();
  }
}

function setClipFilter(kind, e){
  if(e && e.stopPropagation) e.stopPropagation();
  curClipKind = kind;
  var btns = document.querySelectorAll('.cb-tabs .cbt');
  for(var i=0; i<btns.length; i++){
    btns[i].classList.toggle('active', btns[i].getAttribute('data-kind') === kind);
  }
  fetchClipHistory(kind);
}

var _miniToastTimer = null;
function toast(m, dur){
  var el = document.getElementById('miniToast');
  if(!el) return;
  el.textContent = m;
  el.classList.add('on');
  clearTimeout(_miniToastTimer);
  _miniToastTimer = setTimeout(function(){
    el.classList.remove('on');
  }, dur || 1600);
}

function fetchClipHistory(kind){
  var list = document.getElementById('cbList');
  if(!list) return;
  list.innerHTML = '<div style="padding:16px;text-align:center;color:rgb(var(--sb-sub));font-size:12px">__U_cb_loading__</div>';
  fetch('/api/clipboard?kind=' + (kind || 'all') + '&limit=40', {headers: {'Sec-Fetch-Site': 'same-origin'}})
  .then(function(r){ return r.json(); })
  .then(function(d){
    if(d && d.ok && d.items && d.items.length){
      var html = '';
      for(var i=0; i<d.items.length; i++){
        var it = d.items[i];
        var iconHtml = '';
        if(it.kind === 'image'){
          iconHtml = '<img class="cb-img-thumb" src="/api/clipboard/thumb/' + it.content + '" />';
        } else if(it.kind === 'file' && it.thumb){
          /* 第 339 项：复制进来的图片文件也有缩略图，不再只显示一个 📁 */
          iconHtml = '<img class="cb-img-thumb" src="/api/clipboard/thumb/' + it.thumb + '" />';
        } else if(it.kind === 'file'){
          iconHtml = '<span class="cb-badge">__TIP_CB_BADGE_FILE__</span>';
        } else {
          iconHtml = '<span class="cb-badge">__TIP_CB_BADGE_TEXT__</span>';
        }
        html += '<div class="cb-item" onclick="copyClipItem(' + it.id + ', event)" title="__TIP_COPY_ITEM__">'
          + iconHtml
          + '<span class="cb-txt">' + esc(it.preview) + '</span>'
          + '<span class="cb-time">' + it.relative_time + '</span>'
          + '<button class="cb-pin' + (it.pinned ? ' pinned' : '') + '" onclick="togglePinClipItem(' + it.id + ', event)" title="' + (it.pinned ? '__TIP_CB_PIN_DEL__' : '__TIP_CB_PIN_ADD__') + '">' + (it.pinned ? '★' : '☆') + '</button>'
          + '<button class="cb-del" onclick="deleteClipItem(' + it.id + ', event)" title="__TIP_CB_DEL__">✕</button>'
          + '</div>';
      }
      list.innerHTML = html;
    } else {
      list.innerHTML = '<div style="padding:20px;text-align:center;color:rgb(var(--sb-sub));font-size:12px">' + (kind === 'fav' ? '__TIP_CB_EMPTY_FAV__' : '__TIP_CB_EMPTY__') + '</div>';
    }
    resize();
  }).catch(function(){
    list.innerHTML = '<div style="padding:20px;text-align:center;color:rgb(var(--sb-sub));font-size:12px">__TIP_CB_LOAD_FAIL__</div>';
    resize();
  });
}

function copyClipItem(id, e){
  if(e && e.stopPropagation) e.stopPropagation();
  var row = (e && (e.currentTarget || e.target)) ? (e.currentTarget || e.target) : null;
  if(row && row.closest){
    var itemEl = row.closest('.cb-item');
    if(itemEl) itemEl.classList.add('cb-copying');
  }
  fetch('/api/clipboard/copy', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin'},
    body: JSON.stringify({id: id})
  }).then(function(r){ return r.json(); }).then(function(d){
    if(d && d.ok){
      toast(d.msg || '__TIP_CB_COPIED__');
      setTimeout(function(){
        closeClipPanel();
        if(Q) Q.focus();
      }, 140);
    } else {
      toast(d && d.msg ? d.msg : '__TIP_CB_COPY_FAIL__');
    }
  }).catch(function(){
    toast('__TIP_CB_COPY_FAIL__');
  });
}

function togglePinClipItem(id, e){
  if(e && e.stopPropagation) e.stopPropagation();
  fetch('/api/clipboard/pin', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin'},
    body: JSON.stringify({id: id})
  }).then(function(r){ return r.json(); }).then(function(d){
    if(d && d.ok){
      toast(d.pinned ? '__U_cb_pin_on__' : '__U_cb_pin_off__');
      fetchClipHistory(curClipKind);
    }
  }).catch(function(){});
}

function deleteClipItem(id, e){
  if(e && e.stopPropagation) e.stopPropagation();
  fetch('/api/clipboard/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin'},
    body: JSON.stringify({id: id})
  }).then(function(){
    toast('__U_cb_deleted__');
    fetchClipHistory(curClipKind);
  }).catch(function(){});
}

function clearClipHistory(e){
  if(e && e.stopPropagation) e.stopPropagation();
  if(!confirm('__U_cb_confirm__')) return;
  fetch('/api/clipboard/clear', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin'},
    body: '{}'
  }).then(function(){
    fetchClipHistory(curClipKind);
    toast('__U_cb_cleared__');
  }).catch(function(){});
}

var transTimer = null;
var transLiveEnabled = true;

function scheduleTrans(){
  var box = document.getElementById('transBox');
  if(!box || box.classList.contains('hide')) return;
  if(!transLiveEnabled) return;
  clearTimeout(transTimer);
  transTimer = setTimeout(function(){
    doMiniTranslate();
  }, 350);
}

function fetchTransSettings(){
  fetch('/api/settings').then(function(r){ return r.json(); }).then(function(s){
    if(s){
      if(s.trans_target_lang){
        var sel = document.getElementById('tbLangSelect');
        if(sel) sel.value = s.trans_target_lang;
      }
      if(s.trans_live !== undefined){
        transLiveEnabled = !!s.trans_live;
      }
      // 第 273 项：「自动」不是"只在中英之间互转"，而是"译成界面语言"，文字由后端算好下发
      if(s.trans_auto_label){
        var av = document.getElementById('tbLangAuto');
        if(av) av.textContent = s.trans_auto_label;
      }
    }
  }).catch(function(){});
}

// 引擎 id → 界面名（没翻出来时告诉用户是哪家没给译文、为什么）
var TRANS_ENGINE_NAMES = {
  google: 'Google', mymemory: 'MyMemory', youdao: 'Youdao',
  baidu: 'Baidu', iciba: 'Iciba',
  deepseek: 'DeepSeek', deepl: 'DeepL', baidu_api: 'Baidu API',
  google_api: 'Google API', openai: 'OpenAI'
};

function transSkipText(d){
  try{
    if(!d || !d.skipped || !d.skipped.length) return '';
    var parts = [];
    for(var i=0;i<d.skipped.length;i++){
      var x = d.skipped[i] || {};
      parts.push((TRANS_ENGINE_NAMES[x.engine] || x.engine) + '：' + (x.reason || ''));
    }
    return parts.join('；');
  }catch(e){ return ''; }
}

function onTransLangChange(e){
  if(e && e.stopPropagation) e.stopPropagation();
  var langSel = document.getElementById('tbLangSelect');
  var val = langSel ? langSel.value : 'auto';
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({trans_target_lang: val})
  }).catch(function(){});
  doMiniTranslate();
}

function toggleTransPanel(e){
  if(e && e.stopPropagation) e.stopPropagation();
  var box = document.getElementById('transBox');
  if(!box) return;
  if(!box.classList.contains('hide')){
    closeTransPanel();
  } else {
    closeSnipBox();
    closeClipPanel();
    closeImgPreview();
    LIST.innerHTML = '';
    FOOT.classList.add('hide');
    RH.classList.add('hide');
    var em = document.getElementById('empty');
    if(em) em.classList.add('hide');
    box.classList.remove('hide');
    fetchTransSettings();
    doMiniTranslate();
    resize();
    if(Q) Q.focus();
  }
}

function closeTransPanel(e){
  if(e && e.stopPropagation) e.stopPropagation();
  var box = document.getElementById('transBox');
  if(box && !box.classList.contains('hide')){
    box.classList.add('hide');
    var resDiv = document.getElementById('tbResults');
    if(resDiv) resDiv.innerHTML = '';
    if(Q && Q.value.trim()){
      schedule();
    } else {
      renderHome();
    }
    resize();
  }
}

function doMiniTranslate(e){
  if(e && e.stopPropagation) e.stopPropagation();
  var resDiv = document.getElementById('tbResults');
  if(!resDiv) return;
  var txt = (Q ? Q.value : '').trim();
  if(!txt){
    resDiv.innerHTML = '<div style="padding:16px;text-align:center;color:rgb(var(--sb-sub));font-size:12px">__U_trans_ph__</div>';
    resize();
    return;
  }
  var langSel = document.getElementById('tbLangSelect');
  var tgt = langSel ? langSel.value : 'auto';
  resDiv.innerHTML = '<div style="padding:16px;text-align:center;color:rgb(var(--sb-sub));font-size:12px">__U_trans_doing__</div>';
  fetch('/api/translate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin'},
    body: JSON.stringify({text: txt, to: tgt, target_lang: tgt})
  }).then(function(r){ return r.json(); }).then(function(d){
    if(d && d.ok && d.results && d.results.length){
      var html = '';
      for(var i=0; i<d.results.length; i++){
        var r = d.results[i];
        html += '<div class="tb-card" onclick="copyTransText(' + JSON.stringify(r.text) + ', event)" title="__TIP_COPY_TEXT__">'
          + '<div class="tb-card-head"><span class="tb-engine">' + esc(r.engine) + '</span><span class="tb-extra">' + esc(r.extra || '') + '</span></div>'
          + '<div class="tb-card-text">' + esc(r.text) + '</div>'
          + '</div>';
      }
      resDiv.innerHTML = html;
    } else {
      var why = transSkipText(d);
      /* 第 341 项：代理端口连不上时后端已自动改直连，明说出来，别让人以为软件坏了 */
      var byp = (d && d.proxy_bypass) ? ('__U_trans_bypass__') : '';
      resDiv.innerHTML = '<div style="padding:16px;text-align:center;color:rgb(var(--sb-sub));font-size:12px">'
        + '__U_trans_fail__'
        + (byp ? '<div style="margin-top:6px;font-size:11px">' + byp + '</div>' : '')
        + (why ? '<div style="margin-top:6px;font-size:11px;opacity:.85">' + esc(why) + '</div>' : '')
        + '</div>';
    }
    resize();
  }).catch(function(){
    resDiv.innerHTML = '<div style="padding:16px;text-align:center;color:rgb(var(--sb-sub));font-size:12px">__U_trans_timeout__</div>';
    resize();
  });
}

function copyTransText(txt, e){
  if(e && e.stopPropagation) e.stopPropagation();
  if(navigator.clipboard){
    navigator.clipboard.writeText(txt);
  }
  toast('__U_trans_copied__');
}

document.getElementById('btnSearch').onclick=function(){

  if(pastedImgData && !pastedImgOcred){
    ocrPastedImage();
    return;
  }
  var tb = document.getElementById('transBox');
  if(tb && !tb.classList.contains('hide')){
    doMiniTranslate();
    return;
  }
  if(MODE==='web'){ openWeb(); return; }
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

  applyLook();

  fetchTransSettings();

  Q.focus();

  fetchRecent();

  fetchOpenDirs();

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

      if(v && v.lang && v.lang !== MINI_LANG){
        /* QA BUG-07：重载会把 SMALL/MAXH/STYLE/SCOPE 全清掉，而宿主不会
           重推 __autoShow，贴角小窗就变回大窗布局并被裁。先存下来，
           新页面一加载由 restoreMiniState() 恢复。 */
        try{ sessionStorage.setItem('sb_mini_state', JSON.stringify({
          small:SMALL, maxh:MAXH, scope:SCOPE, q:Q.value
        })); }catch(e){}
        location.search = '?lang=' + v.lang;
      }

    }).catch(function(){});

  }catch(e){}

}

/* QA BUG-07：语言切换导致的重载之后恢复贴角小窗状态。
   宿主只在 show 时推 __autoShow，重载后的新页面不会再收到一次。 */
function restoreMiniState(){
  try{
    var s=JSON.parse(sessionStorage.getItem('sb_mini_state')||'null');
    if(!s)return;
    sessionStorage.removeItem('sb_mini_state');
    SMALL=!!s.small; MAXH=parseInt(s.maxh||0,10)||0;
    SCOPE=s.scope||'';
    document.body.classList.toggle('small',SMALL);
    if(typeof s.q==='string' && s.q) Q.value=s.q;
    renderScope();
    shown=-1; resize();
  }catch(e){}
}
restoreMiniState();

setInterval(checkLang, 4000);

window.__onShow=function(opts){

  if(!opts || !opts.from_snip){
    clearPastedImg();
  }

  /* 每次唤出快搜窗，收起所有子面板（剪贴板/翻译/截图等），恢复默认清爽初始态 */
  closeClipPanel();
  closeTransPanel();
  closeSnipBox();
  closeImgPreview();

  applyMiniLang();

  applyLook();

  fetchTransSettings();

  /* 每次唤出刷新引擎配置，用户在设置里改完不用重启就能生效 */
  leaveWeb(); fetchWeb();

  /* 每次唤出先回到大窗：宿主紧接着会调 __autoShow({small:true}) 把它切小，

     顺序不能反（先大后小不会闪，反过来会先小一下再变大）。 */

  SMALL=false; MAXH=0;

  try{ document.body.classList.remove('small'); }catch(e){}

  renderScope();

  fetchOpenDirs();

  /* 语言在设置里改过之后，快搜窗要跟着换：问一下后端当前的界面语言，

     变了就带 ?lang= 重载一次（重载很快，窗口大小保持不变） */

  try{

    fetch('/api/ping').then(function(r){return r.json()}).then(function(v){

      if(v && v.lang && v.lang !== MINI_LANG){ location.search = '?lang=' + v.lang }

    }).catch(function(){});

  }catch(e){}

  Q.value=PRESET_Q; cur=-1; seq++;

  renderHome();        /* 先用上次那一屏，秒出；下面再后台刷新 */

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

    closeClipPanel();
    closeTransPanel();
    closeSnipBox();
    closeImgPreview();

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

        html = ('<!doctype html><meta charset="utf-8"><title>Memex · 出错了</title>'

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



def _licenses_base():

    """第 380 项：随包许可清单所在目录（打包后在 _internal，开发时在仓库根）。"""

    cands = []

    try:

        if getattr(sys, 'frozen', False):

            cands.append(Path(sys.executable).parent / '_internal')

        cands.append(Path(app_dir()))

    except Exception:

        pass

    cands.append(Path(__file__).resolve().parent)

    for base in cands:

        try:

            if (base / 'THIRD-PARTY-NOTICES.md').is_file():

                return base

        except Exception:

            continue

    return None



_LIC_CACHE = {}



def _load_licenses_manifest():

    """解析 THIRD-PARTY-NOTICES.md 的表格；解析不了就返回空（前端显示提示，不报错）。"""

    if 'rows' in _LIC_CACHE:

        return _LIC_CACHE['rows']

    base = _licenses_base()

    rows = []

    if base:

        try:

            txt = (base / 'THIRD-PARTY-NOTICES.md').read_text(encoding='utf-8')

            for line in txt.splitlines():

                line = line.strip()

                if not line.startswith('|') or '---' in line:

                    continue

                cells = [c.strip() for c in line.strip('|').split('|')]

                if len(cells) < 5 or cells[0] in ('组件', '组件名'):

                    continue

                rows.append({'name': cells[0], 'version': cells[1], 'license': cells[2],

                             'use': cells[3], 'url': cells[4]})

        except Exception:

            rows = []

    _LIC_CACHE['rows'] = rows

    return rows



def _model_digests():

    """OCR 模型的身份：文件名 + 大小 + SHA-256 前 12 位（首次算一次后缓存）。"""

    if 'models' in _LIC_CACHE:

        return _LIC_CACHE['models']

    out = []

    try:

        import ocr_engine

        info = ocr_engine.engine_info()

        digests = ocr_engine.model_digest()

        for m in info.get('models') or []:

            out.append({'name': m.get('name'), 'mb': m.get('mb'),

                        'sha256': (digests.get(m.get('name')) or '')[:12]})

        _LIC_CACHE['engine'] = {'version': info.get('version')}

    except Exception:

        pass

    _LIC_CACHE['models'] = out

    return out





# ---------------------------------------------------------------- 第 326 项：正文库瘦身 + 计数暖缓存
# 实测（2026-11-19）：库 2.2GB / 正文 700MB / FTS 影子表 1.9GB，
# 其中 337 篇 >200KB 的文档（大头是爆破字典里的 txt）就占 565MB。
# 做法：新正文按 MAX_BODY 截断、数据文件 >8MB 不读正文、老库一次性瘦身 + FTS 合并 + 增量回收页。

_SLIM_KV = 'slim_docs_v1'


def _slim_docs_once():

    """老库一次性瘦身（做完打 kv 标记，之后不再跑）。

    第 327 项改了两处，都是被实测教训出来的（13:04 那次跑了 **74 秒**，
    期间界面上搜什么都白屏）：
    1. **先把标记写掉再干活**。以前是「全部干完才写标记」，中途崩一次就下次开机重跑，
       于是同一台机器上看得到两次「截断 210 篇 / 回收 86581 页」。
    2. **等用户闲下来再做**（2 分钟没有任何 /api 请求）。开机就抢盘，用户一打开主窗
       正好撞上它 —— 这才是「用着用着突然很卡」的一大半。
    """

    con = conn()

    try:

        if _kv_get(con, _SLIM_KV) == '1':

            return

        _kv_set(con, _SLIM_KV, '1')      # 先占坑：崩了也不再重跑

        con.commit()                     # 第 327 项：同上，漏了就是全程序写库被锁

    except Exception:

        return

    try:

        time.sleep(15)              # 别和启动路径抢盘

    except Exception:

        pass

    # 等到用户真的停手（2 分钟没有请求）再动库；最多等 10 分钟，等到就干
    t_wait = time.time()

    while time.time() - _ACTIVITY['t'] < 120 and time.time() - t_wait < 600:

        time.sleep(5)

    t0 = time.time()

    trimmed = dropped = 0

    # 1) 超长正文截到 MAX_BODY
    while True:

        try:

            with db_lock('slim:trim', 8.0):

                if doc_mode(con) == 'v2':
                    rs = con.execute('SELECT id FROM doc_rec WHERE n > ? LIMIT 200',
                                     (MAX_BODY,)).fetchall()
                else:
                    rs = con.execute('SELECT rowid FROM docs WHERE LENGTH(body) > ? LIMIT 200',
                                     (MAX_BODY,)).fetchall()

                if not rs:

                    break

                for (rid,) in rs:

                    if doc_mode(con) == 'v2':
                        body = doc_body_get(con, rid)
                        doc_replace_body(con, rid, body[:MAX_BODY])
                    else:
                        con.execute('UPDATE docs SET body=substr(body,1,?) WHERE rowid=?',
                                    (MAX_BODY, rid))

                con.commit()

                trimmed += len(rs)

        except LockBusy:

            time.sleep(2)

        except sqlite3.Error as e:

            _log_file('slim trim FAILED: %r' % (e,))

            break

    # 2) 数据文件（>8MB 的 txt/log/csv…）不再正文索引，只留文件名
    try:

        with db_lock('slim:scan', 8.0):

            rows = doc_all_rows(con)

    except Exception:

        rows = []

    rids = {os.path.normcase(p): rid for rid, p in rows}

    victims = []

    for _rid, p in rows:

        if os.path.splitext(str(p))[1].lower() not in TEXT_DUMP_EXTS:

            continue

        try:

            if os.path.getsize(p) > TEXT_DUMP_MAX:

                victims.append(p)

        except OSError:

            continue

    for p in victims:

        try:

            with db_lock('slim:del', 8.0):

                _doc_del(con, rids, p)

                con.commit()

                dropped += 1

        except Exception:

            continue

    # 3) FTS5 合并段：trigram 索引段多了以后查询和体积都会变差。
    # 不用 'optimize' —— 那个命令会把整棵 b-tree 一次合并完，几 GB 的库能锁上好几分钟，
    # 期间所有搜索都在等锁。改成多次小 rank 的 'merge'，每次只合几十页。
    for _ in range(6):

        try:

            with db_lock('slim:merge', 20.0):

                con.execute("INSERT INTO docs(docs, rank) VALUES('merge', 32)")

                con.commit()

        except Exception as e:

            _log_file('slim merge skipped: %r' % (e,))

            break

    # 4) 增量回收空闲页（库是 auto_vacuum=INCREMENTAL）
    #    第 327 项：每次最多回收 60×500 页就收手。以前 300 轮能跑一分多钟，
    #    期间写锁基本被它一个人占着（实时索引只能不停重试）。剩下的交给月度 compact。
    reclaimed = 0

    try:

        vac = sqlite3.connect(DB, timeout=15)

        try:

            vac.execute('PRAGMA busy_timeout=8000')

        except sqlite3.Error:

            pass

        for _ in range(60):

            try:

                free = vac.execute('PRAGMA freelist_count').fetchone()[0]

                if not free:

                    break

                vac.execute('PRAGMA incremental_vacuum(500)').fetchall()

                reclaimed += min(free, 500)

            except sqlite3.Error:

                time.sleep(1)

        vac.close()

    except Exception:

        pass

    try:

        _kv_set(con, _SLIM_KV, '1')

        con.commit()          # 第 327 项：别忘了提交，否则写锁就一直攥在手里

    except Exception:

        pass

    # 瘦身写了几百 MB，WAL 会被撑大（实测 603MB）—— 这里截断一次再报结论。
    try:

        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    except Exception:

        pass

    size_mb = os.path.getsize(DB) / 1048576.0 if os.path.exists(DB) else 0

    _log_file('slim docs done: 截断 %d 篇 / 丢数据文件 %d 篇 / 回收 %d 页 / 库 %.0fMB / %.1fs'

              % (trimmed, dropped, reclaimed, size_mb, time.time() - t0))


def _facets_warm_loop():

    """启动 20 秒后先算一次空关键词计数，之后每 60 秒补一次。

    缓存 TTL 30 秒，所以用户随时点开主窗，左栏数字都是现成的。

    第 328 项：**数据没变就别重算**。之前是每 60 秒无条件重算一遍 ——
    py-spy 抓到的结论是这条线程每分钟把 CPU 打满 2~3 秒，而且它攥着 GIL，
    那两三秒里所有 HTTP 请求线程一起停摆（用户体感就是「隔一会儿卡一下」）。
    现在先比 files/dirs 的行数：没变就把缓存时间戳往后推，一秒都不用花。
    """

    try:

        time.sleep(20)

    except Exception:

        return

    state = [None, None]      # [files 行数, dirs 行数]

    while True:

        try:

            # 第 327 项：暖缓存也走只读连接 —— 它每分钟跑一次，没理由去抢写锁
            with ro_db('/api/facets:warm') as rc:

                nf = rc.execute('SELECT COUNT(*) FROM files').fetchone()[0]

                nd = rc.execute('SELECT COUNT(*) FROM dirs').fetchone()[0]

                if [nf, nd] != state or not _FACETS_CACHE:

                    facets(rc, {})

                    state[0], state[1] = nf, nd

                else:

                    # 数据没变：续期，不重算（12k 组归桶的活就省下来了）
                    now = time.time()

                    for k in list(_FACETS_CACHE.keys()):

                        v = _FACETS_CACHE.get(k)

                        if v:

                            _FACETS_CACHE[k] = (now, v[1])

        except Exception:

            pass

        try:

            time.sleep(60)

        except Exception:

            return


# 第 326 项：空闲压缩用得到的两件东西
_ACTIVITY = {'t': time.time()}

# 第 366 项：只读「心跳」路径 —— 这些接口是前端定时轮询的（面板关着也照打），
# 不应该算成「用户在使用」。不排除它们，主窗 WebView 活着活动戳就永远新鲜，
# 326 的空闲压缩与老库自动迁移会被 5 秒轮询永久饿死（线上库一直卡在 v1 就是这么来的）。
_HB_PATHS = (
    '/api/ocr_batch',        # App 每 5 秒一次（OCR 面板开着时 1 秒）
    '/api/index/verify',     # 校验跑起来时 3 秒一次
    '/api/update_status',    # 下载/解包进度
    '/api/snip/result',      # 截图翻译等结果
    '/api/ping',
    '/api/compact',          # 第 366 项：整理索引库状态（自己也不能把自己当活动）
)


def _is_heartbeat(path):

    """只读轮询不算用户活动。只认 GET，且必须整段匹配（/api/ping/x 不算）。"""

    try:

        p = path.split('?')[0].rstrip('/')

    except Exception:

        return False

    return p in _HB_PATHS


def _touch_activity():

    _ACTIVITY['t'] = time.time()


def _trim_working_set():

    """第 326 项：空闲时把工作集还给系统。

    Python 堆用过的页不会主动还 —— 实测压测峰值 584MB，收尾仍有 334MB，
    用户就记成「这软件越用越吃内存」。SetProcessWorkingSetSize(-1,-1) 等价于
    EmptyWorkingSet：把冷页交回系统，下次要用再读回来（磁盘上全是缓存，代价很小）。
    """

    try:

        import ctypes

        k32 = ctypes.windll.kernel32

        k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))

    except Exception:

        pass


def _compact_loop():

    """第 326 项：空闲时压缩 FTS 段 + VACUUM（30 天一次）。

    FTS5 每次 UPDATE/INSERT 都新写一段；改得多了段越叠越多 —— 实测瘦身后
    docs_data 反而从 1.2GB 涨到 1.4GB（旧段没合），一条 'optimize' 只用 8.6 秒
    就压到正文对应的量级，再 VACUUM 一次库从 1.9GB 缩到 0.9GB。
    代价：optimize / VACUUM 要独占写锁（这时搜索会短暂报忙），所以只在
    「最近 10 分钟没有任何 HTTP 请求」时才动手，而且 30 天内只做一次。
    """

    try:

        time.sleep(60)

    except Exception:

        return

    while True:

        try:

            time.sleep(300)

            if time.time() - _ACTIVITY['t'] < 600:

                # 用户刚用过：不 trim、不整理，隔 5 分钟再看一遍
                continue

            # 空闲 10 分钟以上：先把工作集还给系统（326 的初衷），再看要不要压缩库。
            # 第 366 项修正：原来 trim 写在「活动<600」的分支里，等于用户正用着才 trim、
             # 真空闲反而不 trim，跟 docstring 说的正好相反。
            _trim_working_set()

            con = conn()

            # 第 366 项：迁移与 30 天 optimize 合并成同一个 compact_now()，
            # 这样手动入口和后台走同一条路径、同一份进度、同一套备份与 VACUUM。
            try:

                if COMPACT.get('state') == 'busy':

                    continue

                need_mig = (doc_mode(con) == 'v1'
                            and _kv_get(con, 'compact_v2', '') != '1')

                try:

                    last = float(_kv_get(con, 'fts_opt_at', '0') or 0)

                except Exception:

                    last = 0

                if need_mig or time.time() - last >= 30 * 86400:

                    _log_file('compact: 空闲 %.0fs，开始整理（migrate=%s）'
                              % (time.time() - _ACTIVITY['t'], need_mig))

                    compact_now(auto=True)

            except Exception as e:

                _log_file('compact skipped: %r' % (e,))

        except Exception as e:

            _log_file('fts compact skipped: %r' % (e,))



# 第 366 项：整理索引库（老结构迁移 + FTS optimize + VACUUM + WAL 截断）
# 以前这三步只在「空闲 10 分钟」时后台偷偷做，而前端 5 秒一次的心跳让「空闲」永不成立，
# 线上库就一直停在 v1。现在：①心跳不算活动（见 _is_heartbeat）；②设置里给手动入口；
# ③无论手动还是后台，都往同一个进度对象里写，界面能显示「正在整理 / 已完成 X MB」。
COMPACT = {'state': 'idle', 'phase': '', 'done': 0, 'total': 0, 'k': '', 'a': {},
           'msg': '', 'err': '', 'before': 0.0, 'after': 0.0, 'secs': 0.0,
           'at': 0.0, 'auto': False}
_COMPACT_LOCK = threading.Lock()


COMPACT = {'state': 'idle', 'phase': '', 'done': 0, 'total': 0, 'k': '', 'a': {},
           'msg': '', 'err': '', 'before': 0.0, 'after': 0.0, 'secs': 0.0,
           'at': 0.0, 'auto': False}
_COMPACT_LOCK = threading.Lock()


def _compact_prog(**kw):

    try:

        COMPACT.update(kw)

    except Exception:

        pass


def _db_mb():

    try:

        return os.path.getsize(DB) / 1048576.0

    except Exception:

        return 0.0


def compact_now(auto=False, claimed=False):

    """同步跑一次整理（调用方负责开线程）。返回 COMPACT 快照。

    全程独占写锁，搜索会短暂报「忙」——所以必须能看到进度，不能像故障。
    claimed=True 表示调用方（POST /api/compact）已经把状态置成 busy，跳过重入检查。
    """

    before = _db_mb()

    with _COMPACT_LOCK:

        if COMPACT.get('state') == 'busy' and not claimed:

            return dict(COMPACT)

        if not claimed:

            COMPACT.update({'state': 'busy', 'phase': 'backup', 'done': 0, 'total': 0,
                            'k': 'maint_busy_compact', 'a': {}, 'msg': '', 'err': '',
                            'before': before, 'after': 0.0, 'secs': 0.0,
                            'at': time.time(), 'auto': bool(auto)})

    t0 = time.time()

    migrated = False

    con = None

    try:

        con = conn()

        if doc_mode(con) == 'v1' and _kv_get(con, 'compact_v2', '') != '1':

            with db_lock('compact:migrate', 600.0):

                migrated = compact_legacy_db(con, log=lambda m: _log_file('compact: ' + m),
                                             prog=_compact_prog)

        _compact_prog(phase='optimize', done=0, total=0, k='maint_compact_optimize', a={})

        with db_lock('compact:opt', 30.0):

            con.execute("INSERT INTO docs(docs) VALUES('optimize')")

            con.commit()

        _compact_prog(phase='vacuum', done=0, total=0, k='maint_compact_vacuum', a={})

        with db_lock('compact:vac', 180.0):

            con.execute('VACUUM')

            con.commit()

            con.execute('PRAGMA wal_checkpoint(TRUNCATE)')

        _kv_set(con, 'fts_opt_at', str(time.time()))

        con.commit()

        after = _db_mb()

        COMPACT.update({'state': 'done', 'phase': '', 'after': after,
                        'secs': time.time() - t0, 'at': time.time(),
                        'k': 'maint_compact_note',
                        'a': {'before': '%.1f' % before, 'after': '%.1f' % after,
                              'mb': '%.1f' % max(0.0, before - after),
                              's': '%.0f' % (time.time() - t0)}})

        _log_file('compact done migrated=%s %.0fMB -> %.0fMB in %.0fs'
                  % (migrated, before, after, time.time() - t0))

    except Exception as e:

        try:

            if con is not None:

                con.rollback()

        except Exception:

            pass

        COMPACT.update({'state': 'err', 'phase': '',
                        'err': '%r' % (e,), 'k': '', 'at': time.time()})

        _log_file('compact failed: %r' % (e,))

    finally:

        try:

            COMPACT.update({'after': _db_mb() if COMPACT.get('after') in (0.0, None) else COMPACT.get('after')})

        except Exception:

            pass

    return dict(COMPACT)


def _compact_thread(auto=False, claimed=False):

    try:

        compact_now(auto=auto, claimed=claimed)

    except Exception as e:

        _log_file('compact thread crashed: %r' % (e,))


def shell_open(kind, d=None):
    """第 355 项：命令关键字 cmd / psh 的落地。

    只开一个终端，工作目录用 Popen(cwd=...) 传进去，**不把用户给的路径拼进
    shell 命令行**——路径里有引号、空格、& 都不会变成命令。
    kind 白名单之外一律拒绝；目录不存在就退回用户主目录。
    """
    if kind not in ('cmd', 'psh'):
        return {'ok': False, 'err': 'kind'}
    d = str(d or '').strip()
    if not d or not os.path.isdir(d):
        d = os.path.expanduser('~')
    argv = ['cmd'] if kind == 'cmd' else ['powershell', '-NoExit']
    flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
    try:
        subprocess.Popen(argv, cwd=d, creationflags=flags, close_fds=True)
    except Exception as e:
        return {'ok': False, 'err': repr(e)[:120]}
    return {'ok': True, 'dir': d}


def make_server(port=8765):

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    # 第 374 项：旧界面（PAGE_TPL）整块下线。它当年撑起 / 首页和 ?ui=legacy 兜底，

    # 现在首页一律 302 到 /web/（React），这块 109KB 的 HTML 只剩「跳过去」一件事，

    # 留着它只是给 ?ui=legacy 和未知路径一个不至于白屏的落点。

    PAGE_TPL = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'

                '<title>Memex</title>'

                '<meta http-equiv="refresh" content="0;url=/web/">'

                '</head><body style="margin:0;background:#0a0b0d;color:#c9cdd6;'

                'font:14px/1.7 system-ui,sans-serif;padding:56px">'

                '界面已经换成新版，正在跳转…'

                '<p><a href="/web/" style="color:#17c6b6">没有自动跳转就点这里</a></p>'

                '</body></html>')


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



    def _subs(html, total=None, nfiles=None, lang=None):

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

        # 第 233 项：正文搜索是设置项（默认开），快搜窗的 BODY_ON 从这里取。
        # 直接读 settings.json 而不进 _SUB_CACHE：改完设置要立刻生效。
        _bs = _settings_read().get('body_search', True)
        # 第 275 项：空状态紧凑显示（默认关 = 原来的两排大行）。同理直接读 settings，
        # 改完下次开窗就生效。
        _hc = bool(_settings_read().get('qs_home_compact', False))
        try:
            _hn = int(_settings_read().get('qs_home_count', 4) or 4)
        except Exception:
            _hn = 4
        _hn = max(1, min(10, _hn))      # 第 275 项：条数 1..10，越界夹紧
        _hs = bool(_settings_read().get('qs_home_show', True))   # 第 275 项：整块显不显示
        # 第 280 项：悬停说明跟界面语言走（三个图标 + 立即翻译按钮 + 两条点击提示）。
        # lang 由调用方给：/mini?lang=en 必须跟着 URL 走，不能只看 settings。
        _tp = _mini_tips(lang)
        # 第 281 项：快搜窗面板正文（截图识字 / 翻译 / 剪贴板 / 首屏）的文案也跟语言走。
        # 用一张独立表 MINI_UI，占位符 __U_<key>__ 在这里一次性替换。
        _ml = str(lang or _mini_tip_lang()).strip().lower()
        _ml = {'zh': 'zh-CN', 'zh-cn': 'zh-CN', 'zh_cn': 'zh-CN', 'cn': 'zh-CN'}.get(_ml, _ml)
        if _ml not in MINI_UI:
            _ml = 'zh-CN'
        _ui = MINI_UI.get(_ml) or MINI_UI['zh-CN']
        for _k, _v in _ui.items():
            html = html.replace('__U_%s__' % _k, str(_v))
        return (html.replace('__TIP_SNIP__', _tp['snip'])
                    .replace('__TIP_CLIP__', _tp['clip'])
                    .replace('__TIP_TRANS__', _tp['trans'])
                    .replace('__TIP_RUN__', _tp['run'])
                    .replace('__TIP_COPY_ITEM__', _tp['copy_item'])
                    .replace('__TIP_COPY_TEXT__', _tp['copy_text'])
                    .replace('__TIP_CB_TITLE__', _tp['cb_title'])
                    .replace('__TIP_CB_ALL__', _tp['cb_all'])
                    .replace('__TIP_CB_FAV__', _tp['cb_fav'])
                    .replace('__TIP_CB_TEXT__', _tp['cb_text'])
                    .replace('__TIP_CB_IMAGE__', _tp['cb_image'])
                    .replace('__TIP_CB_FILE__', _tp['cb_file'])
                    .replace('__TIP_CB_CLEAR__', _tp['cb_clear'])
                    .replace('__TIP_CB_CLEAR_TITLE__', _tp['cb_clear_title'])
                    .replace('__TIP_CB_CLOSE__', _tp['cb_close'])
                    .replace('__TIP_CB_EMPTY__', _tp['cb_empty'])
                    .replace('__TIP_CB_EMPTY_FAV__', _tp['cb_empty_fav'])
                    .replace('__TIP_CB_LOAD_FAIL__', _tp['cb_load_fail'])
                    .replace('__TIP_CB_PIN_ADD__', _tp['cb_pin_add'])
                    .replace('__TIP_CB_PIN_DEL__', _tp['cb_pin_del'])
                    .replace('__TIP_CB_DEL__', _tp['cb_del'])
                    .replace('__TIP_CB_BADGE_FILE__', _tp['cb_badge_file'])
                    .replace('__TIP_CB_BADGE_TEXT__', _tp['cb_badge_text'])
                    .replace('__TIP_CB_COPIED__', _tp['cb_copied'])
                    .replace('__TIP_CB_COPY_FAIL__', _tp['cb_copy_fail'])
                    .replace('__TIP_TRANS_TARGET__', _tp['trans_target'])
                    .replace('__HOME_SHOW__', 'true' if _hs else 'false')
                    .replace('__HOME_COUNT__', str(_hn))
                    .replace('__HOME_COMPACT__', 'true' if _hc else 'false')
                    .replace('__BODYSEARCH__', '1' if _bs else '0')
                    .replace('__VER__', APP_VERSION)

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

            # 第 277 项：handle_error 里拿不到 handler 对象，这里留一条「最后处理的
            # 路径」给 QuietServer 兜底用（多线程下可能错位，只作线索、不当事实）。
            # 查询串必须砍掉：/api/translate 的 q 就是用户原文。
            try:
                self.server._last_path = self.path.split('?')[0][:80]
            except Exception:
                pass

            # 第 326 项：用户活动时间戳（空闲时后台才敢做 FTS 压缩 / VACUUM）。
            # 第 366 项：前端定时轮询的只读接口不算活动，否则空闲判断永远不会成立。
            if not _is_heartbeat(self.path):
                _touch_activity()

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

            elif self.path.startswith('/api/snip/result'):
                import snip_ocr
                res = snip_ocr.get_snip_result()
                body = json.dumps(res, ensure_ascii=False).encode('utf-8')
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/snip/thumb'):
                import snip_ocr
                tb = snip_ocr.get_snip_thumb_bytes()
                body = tb or b''
                ctype = 'image/png' if tb else 'text/plain'

            elif self.path.startswith('/api/snip/test_capture'):
                import snip_ocr
                data = snip_ocr.test_screen_capture()
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/snip/clipboard_img'):
                import snip_ocr
                info = snip_ocr.read_clipboard_image()
                body = json.dumps(info, ensure_ascii=False).encode('utf-8')
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/clipboard/thumb/'):
                from urllib.parse import unquote
                fn = unquote(self.path[len('/api/clipboard/thumb/'):].split('?')[0])
                import clipboard_mgr
                d = clipboard_mgr.get_thumb_dir()
                p = d / fn
                if p.is_file() and p.resolve().is_relative_to(d.resolve()):
                    body = p.read_bytes()
                    ctype = 'image/png'
                else:
                    self.send_response(404)
                    self.end_headers()
                    return

            elif self.path.startswith('/api/clipboard'):
                qs = parse_qs(urlparse(self.path).query)
                kind = qs.get('kind', ['all'])[0]
                q = qs.get('q', [''])[0]
                limit = int(qs.get('limit', ['60'])[0])
                offset = int(qs.get('offset', ['0'])[0])
                import clipboard_mgr
                # 第 281 项：条目里的「41 字符 / 3 个项目 / 5 分钟前」原来在库里写死中文，
                # 现在按界面语言在读的时候拼（老数据不用迁移）。
                rows = clipboard_mgr.list_history(kind=kind, q=q, limit=limit, offset=offset,
                                                  lang=_mini_tip_lang())
                body = json.dumps({'ok': True, 'items': rows}, ensure_ascii=False).encode('utf-8')
                ctype = 'application/json; charset=utf-8'

            elif self.path.split('?')[0] == '/api/byok':
                # 第 274 项：自带密钥状态。**只回掩码和"配好了没"** ——
                # 密钥本身永远不下发给前端、不写日志，用户填完就只有 settings.json 里那一份。
                import translator
                cfg = translator.byok_cfg()
                ready = translator.byok_ready(cfg)
                masked = {}
                for pid in translator.BYOK_IDS:
                    item = cfg.get(pid) or {}
                    masked[pid] = {
                        'key': translator.mask_secret(item.get('key')),
                        'model': item.get('model') or '',
                        'appid': item.get('appid') or '',
                        'secret': translator.mask_secret(item.get('secret')),
                    }
                body = json.dumps({
                    'ok': True,
                    'ready': ready,
                    'masked': masked,
                    'specs': [{'id': pid, 'name': translator.BYOK_SPECS[pid]['name'],
                               'note': translator.BYOK_SPECS[pid]['note'],
                               'required': list(translator.BYOK_REQUIRED[pid])}
                              for pid in translator.BYOK_IDS],
                }, ensure_ascii=False).encode('utf-8')
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/translate'):
                qs = parse_qs(urlparse(self.path).query)
                text = (qs.get('text', [''])[0] or qs.get('q', [''])[0] or '').strip()
                sl = (qs.get('from', ['auto'])[0] or qs.get('src_lang', ['auto'])[0] or 'auto').strip()
                tl = (qs.get('to', ['auto'])[0] or qs.get('target_lang', ['auto'])[0] or qs.get('tgt_lang', ['auto'])[0] or 'auto').strip()
                # 第 273 项：同上，tl='auto' 不再被 settings.trans_target_lang 顶替
                import translator
                res = translator.translate_text(text, src_lang=sl, tgt_lang=tl)
                body = json.dumps(res, ensure_ascii=False).encode('utf-8')
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/peek'):

                # 详情面板「读一段正文」：一次只读一篇，按 path 缓存 5 分钟

                qs = parse_qs(urlparse(self.path).query)

                pth = (qs.get('p', [''])[0] or '').strip()

                kw = (qs.get('q', [''])[0] or '').strip()

                # 第 332 项：pg>0 = 按块分期看正文（Word/Excel/PPT 没有分页图，
                # 详情灯箱里 ← → 要能一页页往下读，读到底才换文件）。
                try:

                    pg = int((qs.get('pg', ['0'])[0] or '0'))

                except ValueError:

                    pg = 0

                d = {'ok': False, 'why': 'bad_path'}

                if pth:

                    pth = os.path.normpath(pth)

                    if not ev_recent(pth):

                        try:

                            with ro_db('/api/peek:check') as rc:

                                okrow = bool(rc.execute(

                                    'SELECT 1 FROM meta WHERE path=? LIMIT 1', (pth,)).fetchone())

                        except sqlite3.Error:

                            okrow = False

                        if not okrow:

                            d = {'ok': False, 'why': 'not_indexed'}

                            pth = ''

                    if pth:

                        try:

                            with ro_db('/api/peek') as rc:

                                d = peek_body(rc, pth, kw, page=pg)

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

                                   'titlebar': titlebar_on(),

                                   'fallback': bool(DATA_FALLBACK),

                                   'repo': OFFICIAL_REPO,

                                   'releases': OFFICIAL_RELEASES,

                                   'issues': OFFICIAL_ISSUES,

                                   'manual_page': update_sources()[1]},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/perf'):
                # 第 404 项：标签栏那枚表盘 —— CPU / 内存（本程序 + WebView2 子进程）。
                # 只读、只在本机；服务端不采样线程，靠前端每 2 秒轮一次算出百分比。
                try:
                    body = json.dumps(perf_snapshot(), ensure_ascii=False).encode()
                except Exception as _e:
                    body = json.dumps({'ok': False, 'msg': str(_e)[:80]},
                                      ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/metrics'):

                # 第 277 项：本机功能计数（调用数 / 失败率 / p95 / 最近错误摘要）。
                # 只读、只在本机；不含文本、路径、搜索词。
                try:

                    body = json.dumps(metrics.snapshot() if metrics else {'ok': False, 'msg': '指标模块未加载'},

                                      ensure_ascii=False).encode()

                except Exception as _e:

                    body = json.dumps({'ok': False, 'msg': repr(_e)}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/locale'):

                # 第 308 项：日期 / 时间格式跟随 Windows 区域设置。
                # 只回格式串，不回任何用户数据；90 秒内重复请求也只是一次 ctypes 调用。
                body = json.dumps(sys_locale_info(), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/env'):

                # 环境自检：用户报「打不开 / 窗口不对」时，让他点一下这里，把结论发过来，
                # 等于把现场搬到开发机上。只读探测，不做任何修复动作。
                # 结果缓存 90 秒：quick_check 一个 1GB 的库要一两秒，面板刷新不该重复付这笔钱。

                global _ENV_CACHE

                _now = time.time()

                _force = 'force=1' in self.path

                # 第 277 项：深度体检（deep=1）会真的去连翻译引擎、加载 OCR 模型、
                # 对更新通道发 HEAD —— 只在用户点「深度体检」时跑，结果也不进缓存。
                _deep = 'deep=1' in self.path



                # 体检文案要跟界面语言走（第 174/176 项）：
                # 五种语言各有自己的表（env_check._EN + env_msgs），
                # 以前西/葡/印地全指向英文，用户切过去看到整页英文
                _qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)



                _lang = ((_qs.get('lang') or ['zh'])[0] or 'zh')[:8]

                if (not _force and not _deep and _ENV_CACHE.get('t')

                        and _ENV_CACHE.get('lang') == _lang

                        and _now - _ENV_CACHE['t'] < 90):

                    body = _ENV_CACHE['body']

                else:

                    try:

                        import env_check

                        _inf = env_check.probe_all(data_dir=str(app_dir()), db_path=str(DB),
                                                   light=not _deep, deep=_deep)

                        _iss = env_check.judge(_inf, _lang)

                        body = json.dumps({'ok': True, 'info': _inf, 'issues': _iss,

                                           'plan': env_check.plan(_inf),

                                           'metrics': metrics.failing(3) if metrics else [],

                                           'text': env_check.render(_inf, _iss, lang=_lang)},

                                          ensure_ascii=False).encode()

                    except Exception as _e:

                        import traceback

                        body = json.dumps({'ok': False, 'msg': repr(_e),

                                           'tb': traceback.format_exc()},

                                          ensure_ascii=False).encode()

                    _ENV_CACHE = {'t': _now, 'lang': _lang, 'body': body} if not _deep else _ENV_CACHE

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/index/verify'):

                # 第 281 项：查全库校验状态（GET）。体检只读结论，不跑校验。
                try:

                    import env_check as _ecv

                    out = {'ok': True,

                           'running': bool(_VERIFY_RUN.get('running')),

                           'started_s': int(time.time() - _VERIFY_RUN['started'])

                           if _VERIFY_RUN.get('running') else 0,

                           'last': _ecv.read_verify_state(str(app_dir()))}

                except Exception as _e:

                    out = {'ok': False, 'msg': repr(_e)}

                body = json.dumps(out, ensure_ascii=False).encode()

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

                sp = SCOPE_FILE

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

                _r = check_update()

                _lu, _lp = update_sources()

                _r['manual_page'] = _lp or _r.get('manual_page') or ''

                body = json.dumps(_r, ensure_ascii=False).encode()

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

            elif self.path.startswith('/api/ocr_batch'):
                # Pro 2：队列状态，面板每 1 秒轮询一次。实现在私有 pro/ 包，
                # 公开源码版没有该包时关闭入口；门控在这里，不在私有模块里。
                if pro_api is None:
                    body = json.dumps({'ok': False, 'pro': True},
                                      ensure_ascii=False).encode()
                else:
                    body = pro_api.ocr_batch_get(_B)
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

            elif self.path.startswith('/api/libsets'):

                # 索引集（多库）：不带 q 返回列表，带 q 做跨库合并搜（免费）。

                qs = parse_qs(urlparse(self.path).query)

                q = (qs.get('q', [''])[0] or '').strip()

                if q:

                    body = json.dumps({'ok': True, 'q': q, 'hits': lib_search_all(q, 30)},

                                      ensure_ascii=False).encode()

                else:

                    body = json.dumps(lib_overview(), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'



            elif self.path.startswith('/api/saved'):

                body = json.dumps({'ok': True, 'items': saved_load()},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'



            elif self.path.startswith('/api/diagnose'):

                qs = parse_qs(urlparse(self.path).query)

                try:

                    lim = max(1, min(int(qs.get('n', ['60'])[0]), 300))

                except (ValueError, TypeError):

                    lim = 60

                body = json.dumps(diagnose(lim), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'



            elif self.path.startswith('/api/page'):

                qs = parse_qs(urlparse(self.path).query)

                pp = (qs.get('p', [''])[0] or '').strip()

                kw = (qs.get('q', [''])[0] or '').strip()

                body = json.dumps({'ok': True, 'page': pdf_hit_page(pp, kw) or None},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'



            elif self.path.startswith('/api/open_page'):

                qs = parse_qs(urlparse(self.path).query)

                pp = (qs.get('p', [''])[0] or '').strip()

                nn = (qs.get('n', ['1'])[0] or '1').strip()

                u = pdf_page_url(pp, nn)

                if not u:

                    body = json.dumps({'ok': False, 'err': '不是索引里的本地 PDF'},

                                      ensure_ascii=False).encode()

                else:

                    try:

                        import webbrowser

                        webbrowser.open(u)

                        body = json.dumps({'ok': True, 'page': int(nn)},

                                          ensure_ascii=False).encode()

                    except Exception as e:

                        body = json.dumps({'ok': False, 'err': str(e)},

                                          ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/errlog'):

                # 错误日志：状态 + 本地报告列表 + 最近一份全文预览（发送前可审阅）

                body = json.dumps(error_report.status(), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/backup'):

                # 设置备份是二进制 zip，得自己完成响应并 return，不能落到末尾的 JSON 响应

                name, raw = settings_backup.make_backup(app_dir(), APP_VERSION)

                self.send_response(200)

                self.send_header('Content-Type', 'application/zip')

                self.send_header('Content-Disposition', 'attachment; filename="second-brain-settings.zip"')

                self.send_header('Content-Length', str(len(raw)))

                self.end_headers()

                self.wfile.write(raw)

                return

            elif self.path.startswith('/api/index/health'):

                # 第 308 项：索引「有没有东西」这件事要能被界面问一句。
                # 只读：文件数 / 正文数 / 索引范围条数 / 失联暂停的文件夹 /
                # 全盘文件名索引状态。前端用它决定要不要弹「索引是空的」横幅。
                try:

                    with con_lock:

                        nf = _count_files(conn())

                        nd = _count_docs(conn())

                except sqlite3.Error:

                    nf, nd = 0, 0

                sc = _scope_conf()

                inc = [str(x) for x in (sc.get('include_dirs') or []) if str(x).strip()]

                try:

                    paused = int((_index_failures(30).get('total') or 0))

                except Exception:

                    paused = 0

                body = json.dumps({'ok': True, 'files': nf, 'docs': nd,

                                   'scope': len(inc), 'includes': inc[:10],

                                   'paused': paused, 'names': NAMES.get('state'),

                                   'zero': nf == 0,

                                   'no_text': nd == 0},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/compact'):

                # 第 366 项：整理索引库的进度（手动入口与后台空闲迁移共用）。
                # 这个接口是只读心跳，不算用户活动（见 _is_heartbeat）。
                snap = dict(COMPACT)

                snap['db_mb'] = round(_db_mb(), 1)

                body = json.dumps(snap, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'



            elif self.path.startswith('/api/stats'):

                # 共享连接必须整段加锁：多线程同时 execute/fetchone 会互相踩游标，

                # 实测 8 并发 120 次请求里有 28 次 500（fetchone() 返回 None / 空元组）。

                # 同时复用页脚那套 30 秒缓存：count(*) 要扫 60 万行（单次 62ms），

                # 前端每挂一次就重算一遍纯属浪费。

                now = time.time()

                if now - _SUB_CACHE.get('t', 0.0) > 30 or 'files' not in _SUB_CACHE:

                    # 第 320 项：统计只是页脚数字，等锁等不到就先用旧值回话，
                    # 别为了两行计数把整个窗卡住。
                    try:

                        with ro_db('/api/stats') as rc:

                            _SUB_CACHE['docs'] = _count_docs(rc)

                            _SUB_CACHE['files'] = _count_files(rc)

                            _SUB_CACHE['t'] = now

                    except LockBusy:

                        pass

                body = json.dumps({'ok': True, 'files': _SUB_CACHE.get('files', 0),

                                   'docs': _SUB_CACHE.get('docs', 0)},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/settings'):

                # 损坏文件由 _settings_read 备份并返回 {}，这里再补默认值，

                # 绝不会因为 JSON 解析失败/res不是 dict 变成 500 HTML。

                d = _settings_read()

                d.setdefault('hotkey', True)
                d.setdefault('hotkey_mode', 'double_ctrl')
                d.setdefault('floating_ball', False)

                d.setdefault('theme', 'auto')

                d.setdefault('palette', 'auto')

                d.setdefault('wall', '')

                d.setdefault('wall_opacity', 30)

                d.setdefault('wall_blur', 0)

                d.setdefault('compat', False)

                # 第 197 项：两个「自动冒出来」的开关，默认都开（Listary 那种手感），
                # 嫌烦的用户可以在设置里关掉，不用改配置文件。
                d.setdefault('auto_popup', True)

                d.setdefault('type_search', True)

                # 第 245 项：弹窗样式定稿只剩方案1（rich，带正文片段的卡片）。
                # 老 settings.json 里可能还留着 'tbl'（方案2 紧凑表），这里强制归一：
                # 界面已经没有切换入口，不能让旧配置从后门把下线样式带回来。
                d['qs_style'] = 'rich'
                # 第 233 项：正文搜索默认开（这是与 Listary 的主要差异之一）。
                # 用户不想让正文占结果时在设置 → 快搜窗里关掉。
                d.setdefault('body_search', True)
                # 第 275 项：快搜窗空状态「最近打开」是否紧凑显示，默认关（原样两排）。
                d.setdefault('qs_home_compact', False)
                d.setdefault('qs_home_show', True)      # 第 275 项：最近记录整块，默认显示
                # 第 307 项：在资源管理器里点一下，开着的文件对话框跟过去。
                # 默认开 —— 用户提这个需求就是希望「不用再去找」；嫌它爱管闲事
                # 可以在设置 → 文件搜索里关掉。
                d.setdefault('blank_jump', True)
                # 第 327 项：左栏三块开关。第 337 项起「文件夹分组」默认**关** ——
                # 用户原话「这一堆我感觉没啥用」，那 6 个顶层目录对日常搜索没帮助，
                # 要的就到设置 → 左栏 里自己打开（true 才显示）。
                d.setdefault('sb_types', True)
                d.setdefault('sb_folders', False)
                d.setdefault('sb_time', True)
                # 第 338 项：左栏类型后面那列数字（全部 109763 / 图片 22842…）默认**关**。
                d.setdefault('sb_counts', False)
                # 第 386 项：详情栏「默认展开」默认**关** —— 保持原来那条窄栏，
                # 谁喜欢一选中就摊开看大图，在设置 → 常规里自己打开。
                d.setdefault('dtl_expand', False)
                # 第 275 项：空状态列几条（默认 4，1..10）。
                try:
                    d['qs_home_count'] = max(1, min(10, int(d.get('qs_home_count', 4) or 4)))
                except Exception:
                    d['qs_home_count'] = 4
                d.setdefault('web_search', DEFAULT_WEB_SEARCH)
                # 第 270 项：全删光的情况也要自洽 —— 用户把整表删空后 settings 里
                # 只剩一个 []，而快搜侧 web_search_items() 遇到空表本来就会回落到
                # 出厂默认（两边打架：界面一张白表、快搜仍能按 g/b 搜）。这里让
                # 界面也显示出厂默认，用户随手一存就把这套落盘了。
                if not d.get('web_search'):
                    d['web_search'] = [dict(x) for x in DEFAULT_WEB_SEARCH]
                # 第 310 项：老配置里只有那 7 条，且顺序还是老顺序。这里把存的表
                # 和新默认合一遍（老配置按「国内 → 国外」重排并补上抖音/小红书/TikTok，
                # 改过标题/网址的自定义配置只追加新引擎），界面拿到的就是最终生效的表。
                try:
                    d['web_search'] = _web_search_effective(d['web_search'])
                except Exception:
                    pass
                # 第 270 项：把出厂默认引擎一起下发。用户删掉一条（比如那个
                # 「Bing」）之后，之前只能自己点「添加引擎」再手打 key / 标题 /
                # 网址模板才能补回来 —— 普通用户根本不会这么干，删了就永久少一个。
                # 前端拿这份表做「恢复默认引擎」，只补被删掉的那些，用户的
                # 自定义条目和改过的标题一律不动。
                d['web_search_defaults'] = [dict(x) for x in DEFAULT_WEB_SEARCH]
                # 第 299 项：网络代理三种模式（默认跟随系统代理，和以前 trans_proxy=auto 一致）。
                # proxy_now 是"现在实际用的那个"，供设置页原样显示 —— 用户说"我明明开了代理"时，
                # 界面能直接告诉他程序到底认到了什么。
                d.setdefault('proxy_mode', 'system')
                d.setdefault('proxy_host', netproxy.DEFAULT_HOST)
                d.setdefault('proxy_port', netproxy.DEFAULT_PORT)
                try:
                    d['proxy_now'] = netproxy.describe()
                except Exception:
                    d['proxy_now'] = {'mode': d.get('proxy_mode'), 'url': '', 'system': ''}
                d.setdefault('trans_target_lang', 'auto')
                d.setdefault('trans_live', True)
                # 第 268 项：聚合翻译可逐引擎开关。默认全开（老行为不变），
                # 用户嫌慢就在设置页关掉几个 —— 每次翻译只打勾上的那几条。
                try:
                    import translator as _tr
                    # 第 346 项（B）：第一次读到老配置时收敛成默认前四个，并落盘一次；
                    # 打过标记之后再也不会动用户的勾选。
                    _mig, _changed = _tr.migrate_346(d)
                    if _changed:
                        _patch = {'trans_engines': _mig.get('trans_engines'),
                                  'trans_engines_346': True,
                                  'trans_engines_ver': _tr.ENGINE_VER}
                        try:
                            _settings_update(_patch)
                        except Exception:
                            pass
                        d.update(_patch)
                    # 第 295 项：把"后来新增、用户从没见过"的引擎补进清单，
                    # 否则老配置里只有 4 家，新引擎永远进不来（设置页列着也不生效）。
                    d['trans_engines'] = _tr.active_engines(d)
                    d['trans_engines_ver'] = _tr.ENGINE_VER
                except Exception:
                    d.setdefault('trans_engines', list(_tr.DEFAULT_ENGINES))
                # 第 273 项：「自动」= 译成界面语言。界面文字和界面语言都从后端算，
                # 免得快搜窗（Python 模板）和设置页（React）各写一套、说得不一样。
                try:
                    import translator as _tr2
                    d['ui_lang'] = _tr2.ui_lang()
                    d['trans_auto_label'] = _tr2.auto_label(d['ui_lang'])
                    d['trans_auto_target'] = _tr2.auto_target('en', d['ui_lang'])
                    # 第 274 项：哪几家自带密钥已经配好（只回布尔，不回密钥）
                    d['trans_byok_ready'] = _tr2.byok_ready()
                except Exception:
                    d.setdefault('ui_lang', 'zh-CN')
                    d.setdefault('trans_auto_label', '自动（译成中文）')

                body = json.dumps(d, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.split('?')[0] == '/api/proxy':
                # 第 299 项：设置页「网络代理」打开时读一次：模式、实际代理、检测到的系统代理
                try:
                    d = netproxy.describe()
                except Exception as e:
                    d = {'mode': 'system', 'url': '', 'system': '', 'err': repr(e)[:80]}
                body = json.dumps(d, ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/open_dirs'):

                body = json.dumps(_api_open_dirs(), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'



            elif self.path.startswith('/api/dialog_probe'):

                body = json.dumps(quick_switch.probe(), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/wall/image'):

                _b, ctype = _wall_image()

                body = _b

            elif self.path.startswith('/api/wall'):

                body = json.dumps(_wall_state(), ensure_ascii=False).encode()

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

            elif self.path.startswith('/api/index_failures'):

                # 第 247 项：meta.reason 里躺着的失败记录，摆到用户面前。
                lim = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('limit', ['200'])[0]
                try:
                    lim = int(lim)
                except ValueError:
                    lim = 200
                body = json.dumps(_index_failures(lim), ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/exclude_test'):

                _p = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('p', [''])[0]
                body = json.dumps(_exclude_test(_p), ensure_ascii=False).encode()

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

            elif self.path.startswith('/api/facets'):

                # 第 324 项：左栏计数（大类 / 文档子类型 / 顶层文件夹）。跟着当前
                # 关键词走 —— 计数不跟随，用户就会看到「PDF 25」点进去只有 2 条。
                qs = parse_qs(urlparse(self.path).query)

                qf = (qs.get('q', [''])[0] or '').strip()

                synf = parse_qsyn(qf)

                try:

                    # 第 327 项：只读连接，不抢 con_lock —— 后台在重扫/瘦身时左栏计数照样秒回
                    with ro_db('/api/facets') as rc:

                        out = facets(rc, synf)

                except LockBusy:

                    out = {'ok': False, 'busy': True, 'total': 0,
                           'kinds': [], 'subs': [], 'folders': []}

                except sqlite3.Error:

                    out = {'ok': False, 'total': 0, 'kinds': [], 'subs': [], 'folders': []}

                body = json.dumps(out, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/dirs'):

                # 第 327 项：左栏「文件夹」点进去要有结果（目录在 dirs 表）
                qs = parse_qs(urlparse(self.path).query)

                dq = (qs.get('q', [''])[0] or '').strip()

                dpf = (qs.get('pf', [''])[0] or '').strip()

                try:

                    dn = max(1, min(400, int(qs.get('n', ['200'])[0])))

                except (TypeError, ValueError):

                    dn = 200

                try:

                    with ro_db('/api/dirs') as rc:

                        ditems = dir_rows(rc, dq, dn, dpf)

                except sqlite3.Error:

                    ditems = []

                body = json.dumps({'ok': True, 'items': ditems, 'n': len(ditems)},
                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/files'):

                # 详情页第二路：文件名。用 mini_search 统一排序（应用优先 + 前缀优先），

                # 这样搜 chrome 能让 chrome.exe 排最前。

                qs = parse_qs(urlparse(self.path).query)

                q = (qs.get('q', [''])[0] or '').strip()

                syn = parse_qsyn(q)

                q = syn['q']

                # 第 324 项：左栏文件夹子分类 → 目录前缀。走 SQL 收口（mini_search 的 pf），
                # 而不是先取 200 条再本地过滤 —— 那个目录可能一条都没进候选。
                pf = (qs.get('pf', [''])[0] or '').strip()

                srt = (qs.get('s', ['rel'])[0] or 'rel').lower()

                items = []

                st = 'off'

                # 第 229 项二轮：空查询（q 为空）不走下面的 try，total 从未赋值
                # 就进 'more': total > len(items) —— debug.log 实测 UnboundLocalError
                total = 0

                if q:

                    try:

                        # 第 327 项：文件名搜索走只读连接。这条是主界面的主查询，
                        # 以前它一要排队，整屏就白了。
                        with ro_db('/api/files') as c:

                            # 先用前缀（走 idx_files_name，毫秒级）；只有前缀凑不满一屏

                            # 才补一次全表扫。以前每敲一个字都深扫 60 万行 ≈ 270ms。

                            if syn['ext'] or syn['size'] != (None, None) or syn['days'] or syn['folder']:

                                # 第 229 项五轮：ext/size/dm 语法都会把池子筛得所剩
                                # 无几，浅搜 20 条里可能一条都不剩（「微信 ext:pdf」
                                # 实测回空）—— 直接深扫大池子再交给下面的语法筛。
                                r = mini_search(c, q, limit=200, deep=True, body=False, pf=pf)

                                items = r.get('items', [])

                                total = r.get('n', 0)

                            else:

                                r = mini_search(c, q, limit=20, deep=False, body=False, pf=pf)

                                items = r.get('items', [])

                                total = r.get('n', 0)

                                if len(items) < 12:

                                    r2 = mini_search(c, q, limit=20, deep=True, body=False, pf=pf)

                                    if len(r2.get('items', [])) > len(items):

                                        items = r2.get('items', [])

                                        total = max(total, r2.get('n', 0))

                            st = 'ok' if total > 0 else 'off'

                    except LockBusy:

                        items = []

                        total = 0

                        st = 'busy'

                    except sqlite3.Error:

                        items = []

                        total = 0

                elif syn['ext'] or syn['size'] != (None, None) or syn['days'] or syn['folder']:

                    # 第 229 项五轮：纯语法筛选统一走 SQL WHERE（ext/size/days）——
                    # 「size:>2m 搜不到」就是最近 400 条里没有大文件，靠池子后筛
                    # 根本不可靠（用户 16:1x 实测）。-排除/文件夹/ 这类名字级语法
                    # 仍由下面的 syn_ok 在结果上收口。
                    try:

                        with ro_db('/api/files:syn') as rc:

                            items, total = syntax_only_items(rc, syn, 400)

                        st = 'ok' if total > 0 else 'off'

                    except LockBusy:

                        items = []

                        total = 0

                        st = 'busy'

                    except sqlite3.Error:

                        items = []

                        total = 0

                        st = 'off'

                else:

                    # 第 229 项四轮：空关键词（无任何语法）= 「浏览最近文件」——
                    # 主窗筛选条（全部/文件名）空着打字框点过来时给一屏最近文件
                    # （ORDER BY mtime 走 idx_files_mtime，毫秒级）；size>/dm: 这类
                    # 纯语法也落到这里，再由下面的 syn_ok 收口筛选。
                    try:

                        with ro_db('/api/files:recent') as rc:

                            rows = rc.execute(
                                'SELECT path,name,ext,mtime,size FROM files '
                                'ORDER BY mtime DESC LIMIT 400').fetchall()

                        items = [{'n': nm, 'p': p, 't': 'file',
                                  'e': (ex or '').lower(), 'm': mt,
                                  's': sz, 'z': sz}
                                 for (p, nm, ex, mt, sz) in rows]

                        total = len(items)

                        st = 'ok' if items else 'off'

                    except LockBusy:

                        items = []

                        total = 0

                        st = 'busy'

                    except sqlite3.Error:

                        items = []

                        total = 0

                        st = 'off'

                # 第 238 项：`c:xxx` / `内容:xxx` 是「只搜正文」。前端同时打 /api 那一路
                # 查正文，这里再回一批「文件名里有 xxx」的行，等于把用户明确排除掉的东西
                # 又塞了回来（更糟的是 q 被剥空时直接变成「浏览最近 400 条」）。
                if syn['content']:

                    items = []

                    total = 0

                    st = 'off'

                # 后处理对两条路都生效（原来长在 if q: 块里，ext-only 也要走）

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

                # 第 324 项：「浏览最近 / 纯语法筛选」这两路没走 mini_search，pf 在这里收口。
                if pf:

                    _pfl = pf.lower()

                    items = [it for it in items
                             if str(it.get('p') or '').lower().startswith(_pfl)]

                items = sort_items(items, srt)

                # 第 323 项：`q` 为空说明走的是「浏览最近文件」或「纯语法筛选（ext:pdf 这类）」，
                # 这两条路以前一条都不验活 —— 删掉的图片还挂在列表里，点开就是打不开。
                # 关键字那一路 `mini_search` 自己会验（top N 逐条 exists），这里不重复。
                if not q:

                    items = filter_alive(items)

                total = max(total, len(items))

                # 主窗口的结果也要登记进打开白名单，否则双击是「打开失败」

                # （/api/mini 与 /api/recent 都登记了，只有这里漏了）

                # 第 319 项：这里不能 replace=True。前端 /api/files（文件名）与 /api（正文）
                # 两路并发，谁后到谁执行；带 replace 的那一路会把另一路刚登记的正文命中
                # 整表清掉 —— 用户点正文命中那一行右键「定位」，白名单里已经没有它了。

                if items:

                    allow_open([x['p'] for x in items])

                body = json.dumps({'items': items, 'st': st, 'more': total > len(items)},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.split('?')[0] == '/api/web_search':
                body = json.dumps({'ok': True, 'items': web_search_items()},
                                  ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/mini'):

                # 双击 Ctrl 弹出的快搜窗。deep=0 只做前缀（走索引，毫秒级），

                # deep=1 才全表扫「名字任意位置包含」，由前端在结果不满一屏时补发；

                # body=1 连正文一起查（两家长处都不在正文，这是Memex的差异点）。

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

                force_body = qs.get('fb', ['0'])[0] in ('1', 'true')   # 第 230 项：右侧「正文」按钮

                if force_body: want_body = True

                # scope：快搜窗是「在某个文件夹里打字」唤起的时候只给这个目录的
                # 结果（第 197 项）。不传就是全局，行为跟以前一样。
                scope = (qs.get('scope', [''])[0] or '').strip()

                # c:xxx / content:xxx / 内容:xxx —— 只要正文，名字一条都不占。

                # 主窗口早就认这个语法，快搜窗以前把它当普通词剥掉就当没发生。

                c_only = bool(syn_m['content'])

                if c_only:

                    want_body = True

                try:

                    with con_lock:

                        # 第 229 项五轮：ext/size/dm 语法会把 9 条的池子筛光，
                        # 先捞大池子，语法筛完在下面截回一屏（有关键词才放大）。
                        d = mini_search(conn(), q,
                                        60 if (q and (syn_m['ext']
                                                      or syn_m['size'] != (None, None)
                                                      or syn_m['days']
                                                      or syn_m['folder'])) else 9,
                                        deep, want_body, c_only, force_body)

                except sqlite3.Error as e:

                    d = {'items': [], 'deep': deep, 'ms': 0, 'n': 0, 'err': str(e)}

                # 第 229 项五轮：纯语法筛选（ext:pdf / size:>2m / dm:7d）—— q 被
                # 语法剥空时 mini_search 会回空，这里直接按语法查 files 表
                # （mtime 新的在前），与主窗 /api/files 同一条规则，别一个软件两套行为。
                # 第 238 项：`folder/` 也算纯语法 —— 以前这里漏了它，q 被剥空后
                # 直接进 mini_search('')，快搜窗就回 0 条，用户以为文件夹里没东西。
                if not q and (syn_m['ext'] or syn_m['size'] != (None, None)
                              or syn_m['days'] or syn_m['folder']):

                    try:

                        with con_lock:

                            m_items, _m_total = syntax_only_items(conn(), syn_m, 9)

                        d = {'items': m_items, 'deep': False, 'ms': 0, 'n': len(m_items)}

                    except sqlite3.Error as e:

                        d = {'items': [], 'deep': deep, 'ms': 0, 'n': 0, 'err': str(e)}

                # 目录内的命中排前面，再用全局结果补满：用户就在这个文件夹里，
                # 但也可能记错了位置，别让他对着「没有结果」发呆。
                # 第 373 项：以前的 scope 是「目录内命中排前面，再用全局结果补满」，
                # 提示条却写「只在「X」中搜索」—— 用户看到列表里全是别的盘的文件，
                # 自然认为限定失效。现在改成真的只用这个目录里的结果。
                if scope and q:
                    try:
                        _pre_nc = os.path.normcase(os.path.normpath(scope)).rstrip('\\') + '\\'
                    except Exception:
                        _pre_nc = ''
                    if _pre_nc:
                        d['items'] = [x for x in d['items']
                                      if os.path.normcase(os.path.normpath(x.get('p') or '')
                                                          ).startswith(_pre_nc)]
                    try:
                        near = _scope_items(scope, q, 9)
                    except Exception:
                        near = []
                    _seen = set(os.path.normcase(x['p']) for x in d['items'])
                    for x in near:
                        _k = os.path.normcase(x['p'])
                        if _k in _seen:
                            continue
                        _seen.add(_k)
                        d['items'].append(x)
                    d['items'] = d['items'][:9]
                    d['scope'] = scope
                    d['scope_n'] = len(d['items'])
                    # 这个目录在文件名索引里一条都没有 → 前端可以说清「这里还没进索引」
                    try:
                        with con_lock:
                            d['scope_cold'] = 0 if conn().execute(
                                'SELECT 1 FROM files WHERE instr(lower(path), ?) = 1 LIMIT 1',
                                (_pre_nc,)).fetchone() else 1
                    except Exception:
                        d['scope_cold'] = 0


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

                # 第 248 项：虚拟商店应用入口（path = shell:AppsFolder\<AUMID>）。

                # 它的 ext 是空的，走 sys_icon('') 只会给一张空白纸；必须问 Shell 要

                # 开始菜单那张磁贴图标。AUMID 格式 + 索引存在性双重校验，

                # 免得 /api/icon 变成任意 shell: 路径的解析器。

                if p and not isdir and app_entries.is_shell_app_path(p):

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

                        png = shell_app_icon(p)

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

                                     'max-age=60' if (p and (ext in ('.exe', '.lnk') or app_entries.is_shell_app_path(p))) else 'max-age=86400')

                    self.send_header('Content-Length', str(len(png)))

                    self.end_headers()

                    self.wfile.write(png)

                    return

                self.send_response(204)

                self.end_headers()

                return

            elif self.path.startswith('/api/pdfmeta'):

                # 第 325 项：详情栏翻页要知道总页数。只回一个数字，不读页面内容。
                qs = parse_qs(urlparse(self.path).query)

                p = (qs.get('p', [''])[0] or '').strip()

                pgs = 0
                hpg = 0
                hdn = True
                stt = 'none'
                ocr_prog = None

                kw = (qs.get('q', [''])[0] or '').strip()
                # 第 398 项：「命中的正文」那一小段（带 \x01/\x02 标记）—— 命中页要和它对齐：
                # 同一份投标材料里品牌对比表每页都有「深信服」，按第一个命中会停在封面。
                snip = (qs.get('s', [''])[0] or '').strip()

                if p and _local_only(p) and os.path.exists(p):

                    allowed = ev_recent(p)

                    if not allowed:

                        try:

                            with con_lock:

                                allowed = bool(conn().execute(
                                    'SELECT 1 FROM files WHERE path=? LIMIT 1', (p,)).fetchone())

                        except sqlite3.Error:

                            pass

                    if allowed:

                        pgs = pdf_pages(p, kw)
                        # 第 390 项：详情栏要直接停在命中页（Word 转换时用 Find 定位好）
                        hpg, hdn = doc_render.hit_info(p, app_dir(), kw, snip)
                        # 第 397 项：PDF 以前没有命中页 —— pdfmeta 只问 Office，文档栏目
                        # 因此永远停在第 1 页，目录里的词自然一个高亮都没有。
                        # 这里只对 .pdf 走文本层定位（多关键词任一命中）；扫描件回来 0，
                        # 前端就知道「页面上没法标字」，给一句实话而不是空白。
                        if not hpg and p.lower().endswith('.pdf') and kw:
                            hpg = pdf_hit_page(p, kw, snip=snip)
                        elif hpg and p.lower().endswith('.pdf') and snip:
                            # 有片段就以片段为准（先按词定位到的可能是封面那页）
                            cp = pdf_hit_page(p, kw, snip=snip)
                            if cp:
                                hpg = cp
                        # 第 399 项：扫描件没有文本层 → 后台逐页 OCR 定命中页（单线程、
                        # BELOW_NORMAL、每页落盘）。没找完就让前端继续轮询、显示进度。
                        ocr_prog = None
                        if not hpg and p.lower().endswith('.pdf') and kw:
                            try:
                                oh, od, ot, obusy = ocr_hit_page(
                                    p, doc_render._q_tokens(kw),
                                    doc_render._context_window(snip))
                                hpg = oh
                                if not oh and obusy:
                                    ocr_prog = [od, ot]   # ot 先可能是 0（刚起跑）
                                    hdn = False           # 还没算完，前端别停
                            except Exception:
                                ocr_prog = None
                        if not hdn and kw:
                            # 手里的那段是旧关键词定位的（或者还是旧版本缓存）：
                            # 这里踢一脚，让它按当前词重导那一段；前端会继续轮询到 done
                            doc_render.pdf_page_source(p, app_dir(), 0, kw)
                        # 第 391 项：转换卡死/失败时告诉前端「别等了」，它会把重试停掉、
                        # 直接显示「这份预览不出来」——以前只能一直转圈到 70 秒上限
                        stt = doc_render.status_for(p, app_dir())

                body = json.dumps({'ok': bool(pgs), 'pages': pgs, 'hit': hpg, 'done': hdn,
                                   'state': stt,
                                   # 第 399 项：扫描件的 OCR 进度 [已识别页, 总页数]
                                   'ocr': ocr_prog},
                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/oss/licenses'):

                # 第 380 项：关于页「开源组件」的数据源。
                # 清单来自随包分发的 THIRD-PARTY-NOTICES.md（手写在前端会跟版本走散），
                # 模型工件额外给 SHA-256 前 12 位 —— 合规要求「不能只看文件名」。
                comps, models, base = [], [], ''

                try:

                    comps = _load_licenses_manifest()

                    base = _licenses_base() or ''

                    for m in _model_digests():

                        models.append(m)

                except Exception:

                    pass

                body = json.dumps({'ok': bool(comps), 'components': comps, 'models': models,

                                   'dir': (str(Path(base) / 'licenses') if base else ''),

                                   'notices': (str(Path(base) / 'THIRD-PARTY-NOTICES.md') if base else '')},

                                  ensure_ascii=False).encode()

                self.send_response(200)

                self.send_header('Content-Type', 'application/json; charset=utf-8')

                self.send_header('Cache-Control', 'max-age=600')

                self.send_header('Content-Length', str(len(body)))

                self.end_headers()

                self.wfile.write(body)

                return

            elif self.path.startswith('/api/raw'):

                # 第 379 项：Office 真版面预览的字节出口。
                # 安全口径和 /api/thumb 一模一样：路径必须在索引里（或本次搜索出现过）、
                # 必须 same-origin、扩展名白名单、48MB 上限 —— localhost 上也不能变成
                # 「任意读盘」的后门。
                qs = parse_qs(urlparse(self.path).query)

                p = (qs.get('p', [''])[0] or '').strip()

                site = (self.headers.get('Sec-Fetch-Site') or '').lower()

                ext = os.path.splitext(p)[1].lower().lstrip('.') if p else ''

                rb = b''

                mime = 'application/octet-stream'

                if p and ext in _RAW_OK_EXT and site not in ('cross-site', 'same-site', 'none'):

                    allowed = ev_recent(p)

                    if not allowed:

                        try:

                            with con_lock:

                                c = conn()

                                allowed = bool(c.execute(
                                    'SELECT 1 FROM files WHERE path=? LIMIT 1', (p,)).fetchone()) or bool(
                                    c.execute('SELECT 1 FROM meta WHERE path=? LIMIT 1', (p,)).fetchone())

                        except sqlite3.Error:

                            pass

                    if allowed and os.path.isfile(p):

                        try:

                            if os.path.getsize(p) <= _RAW_MAX:

                                with open(p, 'rb') as f:

                                    rb = f.read()

                                mime = _RAW_MIME.get(ext, mime)

                        except OSError:

                            rb = b''

                if rb:

                    self.send_response(200)

                    self.send_header('Content-Type', mime)

                    self.send_header('Cache-Control', 'max-age=600')

                    self.send_header('Content-Length', str(len(rb)))

                    self.end_headers()

                    self.wfile.write(rb)

                    return

                self.send_response(204)

                self.end_headers()

                return

            elif self.path.startswith('/api/thumb'):

                # 文件缩略图。图片直接缩放、PDF 取首页，其它类型回 204 让前端用系统图标。

                # 第 313 项：**不要**给 Office 加 Shell 缩略图分支 —— 前端缩略图视图会对每格

                # 发这个接口，COM 一卡就把 HTTP 线程全占死（312 项线上事故）。

                # 安全校验：必须是索引里的路径或本次搜索返回过的，防止任意文件被读

                qs = parse_qs(urlparse(self.path).query)

                p = (qs.get('p', [''])[0] or '').strip()

                site = (self.headers.get('Sec-Fetch-Site') or '').lower()

                small = (qs.get('s', [''])[0] or '') == '1'

                # 第 317 项：详情栏 / 灯箱要「看得清」的版本。
                # 默认 320×400 是给网格格子用的；PDF 首页按这个宽度渲染，正文小字完全糊成一团
                # （用户截图反馈「看 pdf 完全看不清」）。big=1 走 900×1200。
                big = (qs.get('big', [''])[0] or '') == '1'

                # 第 325 项：详情里翻页（pg 从 1 开始，用户看到的就是页码）、关键词高亮（q）、
                # 以及「展开」后的超清版（xl=1 → 1600×2200，PDF 正文小字这才真的读得清）。
                try:

                    pg = int((qs.get('pg', ['0'])[0] or '0').strip() or '0')

                except ValueError:

                    pg = 0

                hl = (qs.get('q', [''])[0] or '').strip()

                xl = (qs.get('xl', [''])[0] or '') == '1'

                # 第 403 项：高亮数据版本（扫描件按 OCR 已识别页数传进来，只用来换缓存键）
                hv = ''.join(ch for ch in (qs.get('hv', [''])[0] or '')
                             if ch.isalnum())[:16]

                png = b''

                pages = 0

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

                        # 第 317 项：big 优先（详情/灯箱），其次 small（网格小格），默认中等
                        # 第 325 项：页数只对 PDF 有意义，随手记下来给前端做「12 / 36」。
                        if p.lower().endswith('.pdf'):

                            try:

                                pages = pdf_pages(p)

                            except Exception:

                                pages = 0

                        pg0 = max(0, pg - 1)

                        if xl:

                            png = make_thumb(p, 1600, 2200, page=pg0, q=hl, hv=hv)

                        elif big:

                            # 第 390 项：Office 也要传页码 + 查询词 —— 缓存里可能只有
                            # 「命中页 ±1」那一段，靠 q 才能定位/高亮到对的那一页
                            png = make_thumb(p, 900, 1200, page=pg0, q=hl, hv=hv)

                        elif small:

                            png = make_thumb(p, 200, 200)

                        else:

                            png = make_thumb(p, page=pg0, q=hl, hv=hv)

                        png = png or b''

                if png:

                    self.send_response(200)

                    self.send_header('Content-Type', 'image/png')

                    self.send_header('Cache-Control', 'max-age=86400')

                    self.send_header('X-Pages', str(pages))

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

                    with ro_db('/api/timeline') as rc:

                        # 「文件夹」走 dirs 表（mtime 是后补的列，老库可能有 NULL 未回填）

                        groups = timeline_rows(rc, days, exts=exts, pf=pf,

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

                    with ro_db('/api:recent') as rc:

                        items = recent_rows(rc, 10)   # 第 275 项：条数最多 10 条，得先有 10 条可选

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

                busy = False

                try:

                    # 第 229 项四轮：空关键词 = 「浏览最近文档」，不是「没搜到」——
                    # 主窗筛选条空着打字框点「正文」时，给最近入索引的文档（mtime
                    # 新的在前）。以前故意回空（WHERE 1=1 LIMIT 30 像搜到了其实
                    # 没搜），但那样点了筛选没任何反应（用户 15:5x 反馈）。
                    # content: 前缀（scope=正文）同样落到这里。
                    if not q:

                        try:

                            with db_lock('/api:recent', 12.0):

                                rows = conn().execute(
                                    "SELECT path, '' FROM meta "
                                    'ORDER BY mtime DESC LIMIT 60').fetchall()

                        except LockBusy:

                            rows = []

                            busy = True

                        except sqlite3.Error:

                            rows = []

                    else:

                        try:

                            rows = self._search(q, ext, pf, wn)

                        except LockBusy:

                            # 库被长任务占着：这一路先空着，但要让前端知道是「忙」
                            # 而不是「真的没有」。以前这里会一起超时，界面只剩一半结果。
                            rows = []

                            busy = True

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

                    body = json.dumps({'hits': hits, 'dym': dym, 'q': q, 'busy': busy},

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

                # 第 246 项：虚拟应用入口（shell:AppsFolder\<AUMID>）不是磁盘路径，
                # 下面的 realpath 会把它拼到 cwd、os.path.exists 也永远为 False。
                # 闸门不放松：本机同源 + 本次搜索真的返回过，避免任意 AppID 启动口。
                if app_entries.is_shell_app_path(p):
                    if site not in ('cross-site', 'same-site', 'none') and ev_recent(p):
                        try:
                            app_entries.launch(p)
                            ok, reason = True, ''
                        except Exception:
                            ok, reason = False, 'fail'
                    else:
                        ok, reason = False, 'deny'
                    body = json.dumps({'ok': ok, 'reason': reason}, ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

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

                                # 第 382 项：走平台层；Windows 内部仍是 os.startfile，行为不变
                                plat_shell.open_path(p)

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

                wj = json.dumps(web_search_items(), ensure_ascii=False).replace('</', '<' + chr(92) + '/')
                body = _subs(MINI_TPL, lang=lang).replace(
                    '__MINI_L__', mj).replace('__MINI_LANG__', lang).replace(
                    '__WEB_SEARCH__', wj).encode()

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

                         # 第 204 项：教程动图。少了这行会回 application/octet-stream，
                         # 而全局 end_headers() 带 X-Content-Type-Options: nosniff，
                         # 浏览器会拒绝渲染 <img>，看起来像「动图没打包进去」。
                         '.gif': 'image/gif',

                         '.jpg': 'image/jpeg',

                         '.jpeg': 'image/jpeg',

                         '.webp': 'image/webp',

                         '.ico': 'image/x-icon',

                         '.woff2': 'font/woff2',

                         '.json': 'application/json; charset=utf-8'}.get(ext, 'application/octet-stream')

                body = hit.read_bytes()

            elif (self.path.split('?')[0] in ('/', '/index.html')

                  and web_ui_dir() is not None

                  and 'ui=legacy' not in self.path):

                # 第 374 项：旧界面早就没人用了，302 不再为 ?panel= / ?q= 开小门

                # ——那两条原本是给托盘抽屉和旧链接留的，可托盘走的是 app_window

                # 里直接拼好的 /web/?q=…&panel=…，这两条从来就没被触发过。

                # 查询串必须原样带过去，否则 /?q=归档 这类老书签会被跳成空页。

                _q = self.path.split('?', 1)

                self.send_response(302)

                self.send_header('Location', '/web/' + (('?' + _q[1]) if len(_q) > 1 else ''))

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
                self.server._last_path = self.path.split('?')[0][:80]
            except Exception:
                pass

            try:

                self._do_POST()

            except Exception:

                _sb_err(self)



        def _do_POST(self):
            if not self._guard() or 'application/json' not in (self.headers.get('Content-Type') or ''):
                self.close_connection = True
                self.send_response(403)
                self.end_headers()
                return

            # 写配置/改开机自启这类动作，同样只收页面自己发的同源请求
            if self.path.startswith('/api/') and not self._same_origin():
                self.close_connection = True
                self.send_response(403)
                self.end_headers()
                return

            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = 0                       # Content-Length 是垃圾时当空 body 处理

            max_body = 20 * 1024 * 1024 if self.path.startswith('/api/snip/') else 64 * 1024
            if not (0 <= n <= max_body):
                # 负数会让 rfile.read(-1) 一直读到对端关闭（线程被白占 15 秒）；
                # 超大值则是一次性读进内存。配置类请求没有这么大的合法 body；截图图片允许到 20MB。
                self.close_connection = True
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

            if self.path.split('?')[0] == '/api/web_search_open':
                q = str(req.get('query') or '').strip()
                k = str(req.get('key') or '').strip().lower()
                u = web_search_url(k, q)
                if not u:
                    body = json.dumps({'ok': False, 'err': 'no engine or empty query'},
                                      ensure_ascii=False).encode()
                else:
                    try:
                        import webbrowser
                        webbrowser.open(u)
                        body = json.dumps({'ok': True, 'url': u}, ensure_ascii=False).encode()
                    except Exception as e:
                        body = json.dumps({'ok': False, 'err': str(e)},
                                          ensure_ascii=False).encode()
                ctype = 'application/json; charset=utf-8'
                # 第 310 项：这里原来只赋值 body 就往下走，后面那条 if/elif/else
                # 大链会走到最后的 else，把 body 覆盖成 {'ok': False} —— 于是
                # 「网络搜索」按回车永远返回失败、浏览器也不会被打开。必须就地收口。
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] == '/api/snip/trigger':
                import snip_ocr
                qs = parse_qs(urlparse(self.path).query)
                do_ocr = req.get('ocr') if isinstance(req, dict) else False
                if not do_ocr and 'ocr' in qs:
                    do_ocr = qs['ocr'][0] in ('1', 'true', 'True')
                # 第 369 项：translate=1 走「框选 → OCR → 翻译 → 原位卡片」，不写剪贴板
                do_tr = req.get('translate') if isinstance(req, dict) else False
                if not do_tr and 'translate' in qs:
                    do_tr = qs['translate'][0] in ('1', 'true', 'True')
                ok = snip_ocr.trigger_snip(do_ocr=bool(do_ocr), do_translate=bool(do_tr))
                cur_id = int(ok) if isinstance(ok, (int, float)) and ok > 0 else snip_ocr.get_snip_result().get('snip_id', 0)
                body = json.dumps({'ok': bool(ok), 'snip_id': cur_id}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] == '/api/snip/copy_text':
                import snip_ocr
                txt = req.get('text')
                ok = snip_ocr.copy_snip_text(txt)
                body = json.dumps({'ok': ok}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] == '/api/snip/save':
                import snip_ocr
                p = req.get('path')
                ok, path_or_err = snip_ocr.save_snip_image(p)
                body = json.dumps({'ok': ok, 'path': path_or_err if ok else '', 'err': '' if ok else path_or_err}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] in ('/api/snip/ocr_image', '/api/snip/ocr_latest'):
                import snip_ocr
                img_data = req.get('image') if isinstance(req, dict) else ''
                res = snip_ocr.ocr_base64_image(img_data)
                body = json.dumps(res, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] == '/api/clipboard/copy':
                cid = int(req.get('id', 0)) if isinstance(req, dict) else 0
                import clipboard_mgr
                ok, msg = clipboard_mgr.copy_item(cid, _mini_tip_lang())
                body = json.dumps({'ok': ok, 'msg': msg}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] == '/api/clipboard/delete':
                cid = int(req.get('id', 0)) if isinstance(req, dict) else 0
                import clipboard_mgr
                ok = clipboard_mgr.delete_item(cid)
                body = json.dumps({'ok': ok}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] == '/api/clipboard/clear':
                import clipboard_mgr
                ok = clipboard_mgr.clear_history()
                body = json.dumps({'ok': ok}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] == '/api/clipboard/pin':
                cid = int(req.get('id', 0)) if isinstance(req, dict) else 0
                import clipboard_mgr
                ok, val = clipboard_mgr.toggle_pin(cid)
                body = json.dumps({'ok': ok, 'pinned': val}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] == '/api/byok':
                # 第 274 项：存/清自带密钥。只落本机 settings.json，写成功后把这家引擎
                # 自动加进 trans_engines —— 用户填了密钥就期待它生效，不该再去开关里找。
                import translator
                provider = (req.get('provider') or '').strip().lower() if isinstance(req, dict) else ''
                ok, why = False, 'unknown_provider'
                if provider in translator.BYOK_IDS:
                    cfg = translator.byok_cfg()
                    item = dict(cfg.get(provider) or {})
                    payload = req.get('fields') if isinstance(req.get('fields'), dict) else req
                    clear = bool(req.get('clear'))
                    for f in translator.BYOK_FIELDS:
                        if clear:
                            item[f] = ''
                            continue
                        v = payload.get(f) if isinstance(payload, dict) else None
                        if v is not None:
                            # 空串也照样写（就是"清掉这一项"），只有没传才保留原值
                            item[f] = str(v).strip()
                    cfg[provider] = item
                    payload_out = {'trans_byok': cfg}
                    engines = translator.normalize_engines(
                        _settings_read().get('trans_engines'))
                    filled = item.get('key') if provider != 'baidu_api' \
                        else (item.get('appid') and item.get('secret'))
                    if filled and provider not in engines:
                        engines.append(provider)
                    # 清空后仍留在开关里没问题（没密钥时后端自动跳过），这里不动清单
                    payload_out['trans_engines'] = engines
                    ok, back = _settings_update(payload_out)
                    why = '' if ok else 'write_failed'
                elif isinstance(req, dict) and req.get('provider') in (None, ''):
                    # 只读：POST 空 provider 当查询用（前端偶尔图省事）
                    ok, why = True, ''
                ready = translator.byok_ready()
                masked = {}
                for pid in translator.BYOK_IDS:
                    it = (translator.byok_cfg().get(pid) or {})
                    masked[pid] = {'key': translator.mask_secret(it.get('key')),
                                   'model': it.get('model') or '',
                                   'appid': it.get('appid') or '',
                                   'secret': translator.mask_secret(it.get('secret'))}
                body = json.dumps({'ok': ok, 'why': why, 'ready': ready, 'masked': masked},
                                  ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            # 第 361 项：截图翻译。body {text, to}；text 为空就用最新一次 OCR 的结果。
            if self.path.split('?')[0] == '/api/snip/translate':
                _txt = (req.get('text') or '') if isinstance(req, dict) else ''
                _tl = (req.get('to') or req.get('tgt_lang') or 'auto') if isinstance(req, dict) else 'auto'
                _res = snip_translate(_txt, _tl)
                body = json.dumps(_res, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.split('?')[0] == '/api/translate':
                text = (req.get('text') or req.get('q') or '').strip() if isinstance(req, dict) else ''
                sl = (req.get('from') or req.get('src_lang') or 'auto').strip() if isinstance(req, dict) else 'auto'
                tl = (req.get('to') or req.get('target_lang') or req.get('tgt_lang') or 'auto').strip() if isinstance(req, dict) else 'auto'
                # 第 273 项：**不再**把 tl='auto' 换成 settings.trans_target_lang。
                # 那一步会让人选了「自动」却实际按"默认目标语言"翻 —— 默认语言正好
                # 等于源语言时（英语→英语），接口原样吐回原文，用户看到的就是"翻译不了"。
                # 现在「自动」一律交给 translator.auto_target()：译成界面语言。
                import translator
                res = translator.translate_text(text, src_lang=sl, tgt_lang=tl)
                body = json.dumps(res, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(body)
                return

            # 第 346 项：把译文读出来。本机 SAPI 合成，离线，不出网。
            # 返回 audio/wav 原始字节（前端用 Audio 播）；合成不了就回 JSON，
            # 前端据此把喇叭按钮禁掉，而不是点了没反应。
            if self.path.split('?')[0] == '/api/tts':
                text = (req.get('text') or '') if isinstance(req, dict) else ''
                lang = (req.get('lang') or '') if isinstance(req, dict) else ''
                try:
                    import tts as _tts
                    data, voice = _tts.wav_bytes(text, lang)
                except Exception as e:
                    body = json.dumps({'ok': False, 'why': str(e)[:200]},
                                      ensure_ascii=False).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(body)
                    return
                try:
                    from urllib.parse import quote as _q
                    vh = _q((voice or '')[:80])
                except Exception:
                    vh = ''
                self.send_response(200)
                self.send_header('Content-Type', 'audio/wav')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                if vh:
                    self.send_header('X-TTS-Voice', vh)
                self.end_headers()
                self.wfile.write(data)
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

            if self.path.startswith('/api/dialog_jump'):

                body = json.dumps(_api_dialog_jump(req), ensure_ascii=False).encode()

                self.send_response(200)

                self.send_header('Content-Type', 'application/json; charset=utf-8')

                self.end_headers()

                self.wfile.write(body)

                return

            if self.path.startswith('/api/wall'):

                body = json.dumps(_wall_apply(req), ensure_ascii=False).encode()

                self.send_response(200)

                self.send_header('Content-Type', 'application/json; charset=utf-8')

                self.end_headers()

                self.wfile.write(body)

                return

            if self.path.split('?')[0] == '/api/uninstall':
                # 第 296 项：界面上的「卸载」按钮。必须显式 confirm，避免误点。
                # keep = 只删程序文件（索引库 *.db / settings.json / backups 留着）；
                # full = 连数据一起删。脚本会等本进程退出后再动手，所以这里先回包。
                mode = str((req or {}).get('mode') or 'keep')
                if not (req or {}).get('confirm'):
                    body = json.dumps({'ok': False, 'err': 'need confirm',
                                       'k': 'e_need_confirm'}, ensure_ascii=False).encode()
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(body)
                    return
                try:
                    import uninstall as _un
                    ok, script = _un.run_uninstall(app=app_dir(),
                                                   keep_data=(mode != 'full'))
                except Exception as e:
                    ok, script = False, ''
                    _log_file('uninstall failed: %r' % (e,))
                body = json.dumps({'ok': ok, 'script': script,
                                   'mode': 'full' if mode == 'full' else 'keep'},
                                  ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.startswith('/api/proxy/test'):
                # 第 299 项：设置页的「测试连接」。按"保存中的草稿"试，用户不用先保存再试。
                # 只打一个 204 端点，超时 6 秒；错误只回 code，由前端翻成界面语言。
                draft = {}
                if isinstance(req.get('draft'), dict):
                    draft = req['draft']
                try:
                    r = netproxy.test(draft=draft)
                except Exception as e:
                    r = {'ok': False, 'code': 'network', 'status': 0, 'ms': 0,
                         'via': '', 'err': repr(e)[:120]}
                body = json.dumps(r, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.startswith('/api/shell_open'):

                # 第 355 项：命令关键字 cmd / psh 的落地。只开一个终端，
                # 目录用 Popen(cwd=...) 传，不拼进 shell 命令，避免注入。
                _kind = str(req.get('kind') or 'cmd')

                _dir = str(req.get('dir') or '').strip()

                if _kind not in ('cmd', 'psh'):

                    body = json.dumps({'ok': False, 'err': 'kind'}, ensure_ascii=False).encode()

                    self.send_response(400)

                    self.send_header('Content-Type', 'application/json; charset=utf-8')

                    self.end_headers()

                    self.wfile.write(body)

                    return

                out = shell_open(_kind, _dir)

                body = json.dumps(out, ensure_ascii=False).encode()

                self.send_response(200)

                self.send_header('Content-Type', 'application/json; charset=utf-8')

                self.send_header('Cache-Control', 'no-store')

                self.end_headers()

                self.wfile.write(body)

                return

            if self.path.startswith('/api/settings'):

                # 先校验再写：非法值不该触发一次落盘。所有键合成一个 patch，

                # 由 _settings_update 加锁合并 + 原子替换，避免并发互相抹掉。

                patch = {}

                if 'compat' in req:

                    patch['compat'] = bool(req['compat'])

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

                if 'palette' in req:

                    # 配色 id 用白名单校验：前端写错了不至于把界面刷成透明/纯白

                    if req['palette'] not in PALETTE_IDS:

                        body = json.dumps({'ok': False, 'err': 'palette 不在白名单里',

                                           'k': 'e_bad_palette'}, ensure_ascii=False).encode()

                        self.send_response(400)

                        self.send_header('Content-Type', 'application/json; charset=utf-8')

                        self.end_headers()

                        self.wfile.write(body)

                        return

                    patch['palette'] = req['palette']

                if 'palette_vars' in req:

                    # 整套配色：卡片这类原生窗口的**唯一真源**。格式不对就整包拒，
                    # 免得半套颜色落盘后卡片更难解释。

                    clean = _clean_palette_vars(req['palette_vars'])

                    if clean is None:

                        body = json.dumps({'ok': False, 'err': 'palette_vars 要 6 个 "r g b"',

                                           'k': 'e_bad_palette_vars'}, ensure_ascii=False).encode()

                        self.send_response(400)

                        self.send_header('Content-Type', 'application/json; charset=utf-8')

                        self.end_headers()

                        self.wfile.write(body)

                        return

                    patch['palette_vars'] = clean

                if 'palette_dark' in req:

                    # 和 palette_vars 一起用：决定深色才挂 DarkMode_Explorer、accent 选黑字还是白字

                    patch['palette_dark'] = bool(req['palette_dark'])

                if 'titlebar' in req:

                    # 自绘标题栏（第 180 项）：只收布尔。窗口样式开机时改，

                    # 所以改完要重启才生效 —— 界面上的说明也这么写。

                    patch['titlebar'] = bool(req['titlebar'])

                if 'auto_popup' in req:

                    patch['auto_popup'] = bool(req['auto_popup'])

                if 'type_search' in req:

                    patch['type_search'] = bool(req['type_search'])

                if 'qs_style' in req:

                    # 第 245 项：方案2 已下线，任何写入都归一到 rich。
                    patch['qs_style'] = 'rich'

                if 'body_search' in req:

                    patch['body_search'] = bool(req['body_search'])

                if 'qs_home_compact' in req:

                    patch['qs_home_compact'] = bool(req['qs_home_compact'])

                if 'qs_home_show' in req:

                    patch['qs_home_show'] = bool(req['qs_home_show'])

                if 'blank_jump' in req:

                    patch['blank_jump'] = bool(req['blank_jump'])

                # 第 327 项：左栏三块（类型分类 / 文件夹分组 / 时间筛选）可以关掉，
                # 关掉只是不显示，筛选状态还在，重新打开照旧。
                for _sbk in ('sb_types', 'sb_folders', 'sb_time', 'sb_counts', 'dtl_expand'):

                    if _sbk in req:

                        patch[_sbk] = bool(req[_sbk])

                if 'qs_home_count' in req:

                    try:
                        patch['qs_home_count'] = max(1, min(10, int(req['qs_home_count'])))
                    except Exception:
                        pass

                if 'web_search' in req:

                    patch['web_search'] = _normalize_web_search(req['web_search'])

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

                if 'trans_target_lang' in req:
                    patch['trans_target_lang'] = str(req['trans_target_lang']).strip()

                if 'trans_live' in req:
                    patch['trans_live'] = bool(req['trans_live'])

                if 'trans_engines' in req:
                    # 第 268 项：只接受已知 id、去重、保序；空列表由 translator
                    # 归一回全开，别让用户把自己关到一个引擎都不剩。
                    try:
                        import translator as _tr
                        patch['trans_engines'] = _tr.normalize_engines(req['trans_engines'])
                        # 第 295 项：记下"保存这一刻的引擎版本"，以后新增的引擎
                        # 才会自动补进这份清单（用户从没见过，不该默认被关掉）。
                        patch['trans_engines_ver'] = _tr.ENGINE_VER
                    except Exception:
                        pass

                if 'proxy_mode' in req:
                    # 第 299 项：只收三个白名单值，写坏了会让全程序回落到直连（很难查）
                    m = str(req.get('proxy_mode') or '').strip().lower()
                    if m not in netproxy.MODES:
                        body = json.dumps({'ok': False, 'err': 'proxy_mode 只能是 system/none/custom',
                                           'k': 'e_bad_proxy_mode'}, ensure_ascii=False).encode()
                        self.send_response(400)
                        self.send_header('Content-Type', 'application/json; charset=utf-8')
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    patch['proxy_mode'] = m

                if 'proxy_host' in req:
                    host = str(req.get('proxy_host') or '').strip()
                    if len(host) > 200 or any(ch in host for ch in (' ', '\t', '\r', '\n', '"', "'")):
                        body = json.dumps({'ok': False, 'err': 'proxy_host 不合法',
                                           'k': 'e_bad_proxy_host'}, ensure_ascii=False).encode()
                        self.send_response(400)
                        self.send_header('Content-Type', 'application/json; charset=utf-8')
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    # 填了整串（http://user:pass@host:port）也校验一下，socks 这里不支持
                    if host and '://' in host and host.split('://', 1)[0].lower() not in ('http', 'https'):
                        body = json.dumps({'ok': False, 'err': '只支持 http/https 代理',
                                           'k': 'e_bad_proxy_scheme'}, ensure_ascii=False).encode()
                        self.send_response(400)
                        self.send_header('Content-Type', 'application/json; charset=utf-8')
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    patch['proxy_host'] = host or netproxy.DEFAULT_HOST

                if 'proxy_port' in req:
                    try:
                        port = int(req.get('proxy_port'))
                    except Exception:
                        port = 0
                    if not (1 <= port <= 65535):
                        body = json.dumps({'ok': False, 'err': 'proxy_port 只能是 1..65535',
                                           'k': 'e_bad_proxy_port'}, ensure_ascii=False).encode()
                        self.send_response(400)
                        self.send_header('Content-Type', 'application/json; charset=utf-8')
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    patch['proxy_port'] = port

                if 'hotkey_mode' in req:
                    mode = str(req['hotkey_mode']).strip()
                    if mode in ('double_ctrl', 'alt_space', 'ctrl_space'):
                        patch['hotkey_mode'] = mode

                if 'floating_ball' in req:
                    patch['floating_ball'] = bool(req['floating_ball'])

                if 'floating_ball_x' in req:
                    try:
                        patch['floating_ball_x'] = int(req['floating_ball_x'])
                    except Exception:
                        pass

                if 'floating_ball_y' in req:
                    try:
                        patch['floating_ball_y'] = int(req['floating_ball_y'])
                    except Exception:
                        pass

                ok, d = _settings_update(patch)

                if not ok:

                    body = json.dumps({'ok': False, 'err': 'settings.json 写入失败',

                                       'k': 'e_settings_write'}, ensure_ascii=False).encode()

                    self.send_response(500)

                    self.send_header('Content-Type', 'application/json; charset=utf-8')

                    self.end_headers()

                    self.wfile.write(body)

                    return

                # 第 299 项：改完代理立刻清缓存 + 同步环境变量，下一次联网请求就生效
                if any(k in patch for k in ('proxy_mode', 'proxy_host', 'proxy_port')):
                    try:
                        netproxy.refresh()
                    except Exception:
                        pass

                body = json.dumps({'ok': True, 'settings': d}, ensure_ascii=False).encode()

                self.send_response(200)

                self.send_header('Content-Type', 'application/json; charset=utf-8')

                self.end_headers()

                self.wfile.write(body)

                return

            if self.path.startswith('/api/config'):

                raw = [str(d).strip() for d in req.get('include_dirs', []) if str(d).strip()]

                exc = [str(d).strip() for d in req.get('exclude_dirs', []) if str(d).strip()]

                dirs = safe_scope_list(raw, True)  # 显式添加的共享盘也收；其余仍只收本机存在的目录

                # 第 294 项：网络位置（NAS / 共享盘）是 Pro 位 net_index。
                # 判定放在 _safe_scope_dir 校验之后：不可达的 UNC 早被滤掉了，
                # 不会因为用户手打错一个路径就弹付费提示。
                if scope_net_blocked(dirs, (_scope_conf().get('include_dirs') or [])):

                    body = json.dumps({'ok': False, 'err': '网络位置索引需要 Pro', 'k': 'e_net_pro'},

                                      ensure_ascii=False).encode()

                elif not dirs:

                    body = json.dumps({'ok': False, 'err': '至少要添加一个文件夹', 'k': 'e_need_dir'},

                                      ensure_ascii=False).encode()

                else:

                    sp = SCOPE_FILE

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

                        dropped = [d for d in raw if not _safe_scope_dir(d, True)]

                        body = json.dumps({'ok': True, 'include_dirs': dirs,

                                           'exclude_dirs': exc, 'dropped': dropped},

                                          ensure_ascii=False).encode()

                    except Exception as e:

                        print('[!] 保存索引范围失败：', e, flush=True)

                        body = json.dumps({'ok': False, 'err': '索引范围没保存（没有本机存在的文件夹，或写盘失败）',

                                           'k': 'e_scope_save'}, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/libsets'):

                op = str(req.get('op') or '').strip()

                if op == 'create':

                    lib, err = libsets.create(app_dir(), req.get('name') or '')

                    body = json.dumps({'ok': bool(lib), 'lib': lib, 'err': err},

                                      ensure_ascii=False).encode()

                elif op == 'switch':

                    ok, why = switch_lib(str(req.get('id') or ''))

                    body = json.dumps({'ok': ok, 'err': why}, ensure_ascii=False).encode()

                elif op == 'rename':

                    ok, why = libsets.rename(app_dir(), str(req.get('id') or ''),

                                             req.get('name') or '')

                    body = json.dumps({'ok': ok, 'err': why}, ensure_ascii=False).encode()

                elif op == 'delete':

                    lid = str(req.get('id') or '')

                    # 删当前库前先切回默认：否则连接还开着，Windows 上删不掉 db 文件。

                    d = libsets.load(app_dir())

                    if d['active'] == lid:

                        switch_lib('default')

                    ok, why = libsets.remove(app_dir(), lid)

                    body = json.dumps({'ok': ok, 'err': why}, ensure_ascii=False).encode()

                else:

                    body = json.dumps({'ok': False, 'err': '未知操作'},

                                      ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'



            elif self.path.startswith('/api/saved'):

                op = str(req.get('op') or 'add').strip()

                if op == 'add':

                    ok, why = saved_add(req.get('q'), req.get('name'))

                elif op == 'remove':

                    ok, why = saved_remove(req.get('q')), ''

                else:

                    ok, why = False, '未知操作'

                body = json.dumps({'ok': ok, 'err': why, 'items': saved_load()},

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'



            elif self.path.startswith('/api/diagnose'):

                op = str(req.get('op') or '').strip()

                if op == 'retry':

                    paths = req.get('paths')

                    if not isinstance(paths, list) or not paths:

                        paths = [x.get('p') for x in diagnose(300)['items']]

                    body = json.dumps(diagnose_retry(paths), ensure_ascii=False).encode()

                else:

                    body = json.dumps({'ok': False, 'err': '未知操作'},

                                      ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/index/verify'):

                # 第 281 项：启动全库校验（POST）。单飞：正在跑就返回 already，
                # 免得用户连点几次把磁盘读满。跑完把结论写 verify_state.json。
                try:

                    import env_check as _ecv2

                    if _VERIFY_RUN.get('running'):

                        out = {'ok': True, 'running': True, 'already': True,

                               'started_s': int(time.time() - _VERIFY_RUN['started'])}

                    else:

                        with _VERIFY_LOCK:

                            if _VERIFY_RUN.get('running'):

                                out = {'ok': True, 'running': True, 'already': True}

                            else:

                                _VERIFY_RUN['running'] = True

                                _VERIFY_RUN['started'] = time.time()

                                _dbp, _dd = str(DB), str(app_dir())

                                def _work():

                                    try:

                                        _r = _ecv2.full_verify(_dbp, data_dir=_dd)

                                        _VERIFY_RUN['res'] = _r

                                        if metrics:

                                            metrics.add('verify_full', status='ok' if _r.get('ok') else 'fail',

                                                        ms=int(float(_r.get('elapsed') or 0) * 1000),
                                                        err='' if _r.get('ok') else str(_r.get('state') or ''))

                                    except Exception as _ex:

                                        error_report.capture_exc(_ex, 'index-verify')

                                    finally:

                                        _VERIFY_RUN['running'] = False

                                threading.Thread(target=_work, name='index-verify', daemon=True).start()

                                out = {'ok': True, 'running': True}

                except Exception as _e:

                    out = {'ok': False, 'msg': repr(_e)}

                body = json.dumps(out, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/errlog'):

                op = str(req.get('op') or '').strip()

                cur = error_report.load_conf()

                if op == 'set':

                    # 接收端只从开发者侧 error_ingest.json 读；客户端给的地址一律拒绝，

                    # 免得有人通过 API 把报告引到自己服务器，或把地址又带回界面。

                    if str(req.get('url') or '').strip():

                        out = {'ok': False, 'err': '接收端由开发者配置，客户端不可指定'}

                    else:

                        # auto / include_paths 已恒开（第 162 项），界面也不再有开关；

                        # 这里只接受 collect，其余字段一律忽略。

                        error_report.save_conf({

                            'collect': bool(req['collect']) if 'collect' in req else cur['collect'],

                        })

                        out = error_report.status()

                elif op == 'test':

                    # 只生成预览，不落盘：让用户没遇到错误时也能看清「将要发送什么」

                    demo = error_report.build('manual-preview', RuntimeError,

                        RuntimeError('预览示例：这不是真实错误'), None, cur['include_paths'])

                    out = {'ok': True, 'text': error_report.markdown(demo, lang=_errlog_lang())}

                elif op == 'preview':

                    rid = str(req.get('id') or '')

                    r = error_report.get_report(rid) if rid else None

                    if r is None:

                        items = error_report.list_reports()

                        r = error_report.get_report(items[0]['id']) if items else None

                    out = {'ok': r is not None,
                           'text': error_report.markdown(r, lang=_errlog_lang()) if r else ''}

                elif op == 'send':

                    rid = str(req.get('id') or '')

                    if not rid:

                        items = error_report.list_reports()

                        rid = items[0]['id'] if items else ''

                    ok, err = error_report.send_one(rid) if rid else (False, '没有待发送的报告')

                    out = {'ok': ok, 'err': err, 'state': error_report.status()}

                elif op == 'crumb':

                    # 第 277 项：前端只报动作名与枚举（面板名 / 结果码），不报内容。
                    # 例：{'items': [{'a': 'panel', 'v': 'translate'}, {'a': 'search'}]}
                    items = req.get('items') if isinstance(req.get('items'), list) else []

                    n = 0

                    try:

                        import breadcrumb as _bc2

                        for it in items[:8]:

                            if isinstance(it, dict) and it.get('a'):

                                _bc2.add(it.get('a'), v=it.get('v'), panel=it.get('panel'))

                                n += 1

                    except Exception:

                        pass

                    out = {'ok': True, 'n': n}

                elif op == 'js':

                    # 第 277 项：前端异常（window.onerror / unhandledrejection /
                    # ErrorBoundary）。只收 message、stack 前若干帧与面板名，
                    # 不收任何输入框内容；与自动捕获一样受 collect 之外的脱敏规则约束。
                    rid = error_report.save_js(req)

                    out = {'ok': bool(rid), 'id': rid or '', 'err': '' if rid else '空报告',

                           'state': error_report.status()}

                elif op == 'feedback':

                    # 界面看着不对但没抛异常时，用户主动写一句描述生成报告。

                    # 不受 collect 开关影响（那是自动捕获的开关），同样二次脱敏。

                    txt = str(req.get('text') or '')[:2000]

                    rid = error_report.save_feedback(txt, cur['include_paths'])

                    out = {'ok': rid is not None, 'err': '' if rid else '请先写一句描述', 'state': error_report.status()}

                elif op == 'export':

                    fp = error_report.export_report(str(req.get('id') or ''))

                    out = {'ok': fp is not None, 'path': str(fp) if fp else ''}

                elif op == 'clear':

                    out = {'ok': True, 'n': error_report.clear_reports(), 'state': error_report.status()}

                else:

                    out = {'ok': False, 'err': '未知 op'}

                body = json.dumps(out, ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/backup'):

                import base64

                try:

                    raw = base64.b64decode(str(req.get('data') or ''), validate=True)

                except Exception:

                    raw = b''

                body = json.dumps(settings_backup.restore(app_dir(), raw),

                                  ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/compact'):

                # 第 366 项：手动「整理索引库」。迁移 + optimize + VACUUM 一次跑完，
                # 进度走 GET /api/compact；整个过程独占写锁，搜索会短暂报忙。
                if COMPACT.get('state') == 'busy':

                    body = json.dumps({'ok': False, 'err': '正在整理索引库', 'k': 'e_compact_busy'},

                                      ensure_ascii=False).encode()

                elif _job_busy():

                    body = json.dumps({'ok': False, 'err': '已有任务在跑', 'k': 'e_busy'},

                                      ensure_ascii=False).encode()

                else:

                    # 先把状态置成 busy 再开线程：否则前端 700ms 后第一次读进度
                    # 可能还读到上一次的 idle，直接判定「已结束」。
                    COMPACT.update({'state': 'busy', 'phase': 'backup', 'done': 0, 'total': 0,
                                    'k': 'maint_busy_compact', 'a': {}, 'msg': '', 'err': '',
                                    'before': _db_mb(), 'after': 0.0, 'secs': 0.0,
                                    'at': time.time(), 'auto': False})

                    threading.Thread(target=_compact_thread, kwargs={'auto': False, 'claimed': True},

                                     daemon=True, name='compact-now').start()

                    body = json.dumps({'ok': True}, ensure_ascii=False).encode()

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

                    # 第 382 项：Windows 走资源管理器打开；mac 走 open，linux 走 xdg-open
                    plat_shell.open_path(str(app_dir()))

                    body = b'{"ok": true}'

                except Exception as e:

                    body = json.dumps({'ok': False, 'err': str(e)}, ensure_ascii=False).encode()

                # 第 382 项：这个分支原来**漏了 ctype**，走到 send_header 时 ctype 未绑定，
                # 整个 /api/open_dir 直接 500（前端「打开数据目录」按钮其实一直是坏的）。
                ctype = 'application/json; charset=utf-8'

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

                        # 第 382 项：Windows 是 explorer /select 选中那个 zip，mac 是 open -R
                        plat_shell.reveal(str(zpath))

                        body = json.dumps({'ok': True, 'zip': str(zpath), 'n': len(good)},

                                          ensure_ascii=False).encode()

                    except Exception as e:

                        body = json.dumps({'ok': False, 'err': str(e)},

                                          ensure_ascii=False).encode()

                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/export'):
                # Pro（第 140 项）：门控只挂在新增 API 上，28 个已发 API 一个没锁。
                # 实现在私有 pro/ 包；公开源码版没有该包时关闭入口。
                if pro_api is None or not license_feature('export'):
                    body = json.dumps({'ok': False, 'pro': True},
                                      ensure_ascii=False).encode()
                else:
                    body = pro_api.export(req, _B)
                ctype = 'application/json; charset=utf-8'

            elif self.path.startswith('/api/ocr_batch'):
                # Pro 2（第 143 项）：所有动作走**同一个路径**、由 body 的 op 区分。
                # 刻意不拆 /api/ocr_batch_add 之类：POST 分发是 startswith 链，
                # 多一个前缀就多一次「顺序写反被上一个分支吃掉」的机会 ——
                # /api/license_clear 必须排在 /api/license 前，就是这么来的。
                if pro_api is None:
                    body = json.dumps({'ok': False, 'pro': True},
                                      ensure_ascii=False).encode()
                else:
                    body = pro_api.ocr_batch_post(req, _B)
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

            # 第 320 项：正文那一路以前是裸 with con_lock —— 一个长任务占住库，
            # 这里就跟文件名那一路一起挂死，界面看到的是「搜什么都转圈」。
            # 第 327 项：正文查询就是那个「5.6 秒的慢查询」（888MB 库 + 冷页），
            # 改成走只读连接后，它自己慢，别人不受影响 —— 界面不再整屏空白。
            with ro_db('/api:body') as rc:

                return search_rows(rc, q, 30, ext, pf, with_name)

    class QuietServer(ThreadingHTTPServer):

        """多线程 + 安静：单线程时一个不发完请求的连接会把界面卡住（见 H.timeout）。

        连接被对端中断（关窗口、刷新、探测端口）是常态，不该往控制台吐 traceback。"""

        daemon_threads = True



        def handle_error(self, request, client_address):

            et = sys.exc_info()[0]

            if et in (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):

                return

            # 第 277 项：这里接住的是 50+ 个 /api 处理器抛出的异常。
            # socketserver 只会 traceback.print_exc() 到 stderr，而打包版
            # pythonw 没有控制台 —— 等于整条链路都在黑洞里。
            try:

                error_report.capture_api(getattr(self, '_last_path', '') or '?')

            except Exception:

                pass

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

            with db_lock('boot_maint:count', 10.0):

                left = conn().execute('SELECT COUNT(*) FROM dirs WHERE mtime IS NULL').fetchone()[0]

            if left:

                # 第 321 项：不能再套一层 con_lock —— backfill_dir_mtime 内部只锁
                # 「读一页 + 写一批」，os.stat 全在锁外；套上外层锁就等于又回到
                # 「4000 个目录 stat 期间谁都进不来」的老毛病。
                got = backfill_dir_mtime(conn(), limit=4000)

                if got:

                    _log_file('dir mtime backfill: %d (left %d)' % (got, max(0, left - got)))

        except Exception as e:

            _log_file('dir mtime backfill FAILED: %r' % (e,))



    threading.Thread(target=_boot_maint, daemon=True).start()

    # 老库的 bigram 旁表回填（2 字中文查询提速）：分块后台跑，断点续跑。

    # 回填完才把 2 字查询路由过去（_bigram_ready），没完成前照旧走 LIKE，不回退。

    threading.Thread(target=_bigram_backfill, daemon=True).start()

    # 第 326 项：老库正文瘦身（一次性）+ 左栏计数暖缓存（每分钟一次）
    threading.Thread(target=_slim_docs_once, daemon=True, name='slim-docs').start()

    threading.Thread(target=_facets_warm_loop, daemon=True, name='facets-warm').start()

    # 第 326 项：空闲压缩（FTS 合并 + VACUUM），30 天一次
    threading.Thread(target=_compact_loop, daemon=True, name='db-compact').start()

    # 第 326 项：开机先把 WAL 截断一次。索引器写得多时 WAL 能涨到几百 MB
    # （实测 603MB，比库本身还占地方），启动这一刻还没有长读事务，截得掉。
    try:

        conn().execute('PRAGMA wal_checkpoint(TRUNCATE)')

    except Exception:

        pass

    # 第 321 项：锁看门狗。卡住时把「谁攥着 con_lock」的线程栈写进 debug.log。
    threading.Thread(target=_lock_watchdog, daemon=True, name='lock-watchdog').start()

    # 体积评估 #1：常驻期间定时收 WAL。autocheckpoint 只管合并帧，
    # 长会话里没有一次「干净的截断」机会，文件就停在最高水位（实测 60.9 MB）。
    # 这里每 60 秒试一次 TRUNCATE：有长读事务就 busy，下一轮再来，绝不等。
    threading.Thread(target=_wal_keeper_loop, daemon=True, name='wal-keeper').start()

    # 第 323 项：后台验活 —— 库里已经不存在的路径定时清掉（列表里就不会再出现死行）。
    threading.Thread(target=_gone_reconcile_loop, daemon=True, name='gone-reconcile').start()

    return QuietServer(('127.0.0.1', port), H)



# ------------------ 第 404 项：性能监控（标签栏那枚表盘） ------------------
# 只用 Win32 API，不引 psutil：打包体积和兼容性都不值得。
# WebView2 的渲染进程是 Memex.exe 的子进程，内存算「本程序」时必须一起算，
# 否则面板上永远只看到几十 MB，跟任务管理器对不上（用户第一反应就是「你这数是假的」）。

_PERF_PREV = {'ts': 0.0, 'proc': 0.0, 'sys': None}


def _perf_walk_pids():
    """返回 [(pid, 线程数)]：自己 + 所有后代。拿不到快照就只报自己。"""
    try:
        import ctypes.wintypes as wt

        class PE32(ctypes.Structure):
            _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD),
                        ('th32ProcessID', wt.DWORD),
                        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                        ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                        ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', ctypes.c_long),
                        ('dwFlags', wt.DWORD), ('szExeFile', ctypes.c_wchar * 260)]

        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
        if not snap or int(snap) == -1:
            return [(os.getpid(), 0)]
        info = {}
        try:
            k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            pe = PE32()
            pe.dwSize = ctypes.sizeof(PE32)
            ok = k32.Process32FirstW(snap, ctypes.byref(pe))
            while ok:
                info[int(pe.th32ProcessID)] = (int(pe.th32ParentProcessID),
                                               int(pe.cntThreads))
                ok = k32.Process32NextW(snap, ctypes.byref(pe))
        finally:
            k32.CloseHandle(snap)
        own = os.getpid()
        pids = [own]
        for _ in range(6):
            more = [p for p, (pp, _t) in info.items() if pp in pids and p not in pids]
            if not more:
                break
            pids += more
        return [(p, info.get(p, (0, 0))[1]) for p in pids]
    except Exception:
        return [(os.getpid(), 0)]


def _perf_proc_stats(pid):
    """单个进程：工作集 / 私有提交 / CPU 秒 / 句柄数 / 创建时间。"""
    try:
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [('cb', wt.DWORD), ('PageFaultCount', wt.DWORD),
                        ('PeakWorkingSetSize', ctypes.c_size_t),
                        ('WorkingSetSize', ctypes.c_size_t),
                        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                        ('PagefileUsage', ctypes.c_size_t),
                        ('PeakPagefileUsage', ctypes.c_size_t)]

        k32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        h = k32.OpenProcess(0x0410, False, int(pid))     # QUERY_INFORMATION | VM_READ
        if not h:
            h = k32.OpenProcess(0x1000, False, int(pid))  # LIMITED（至少能读内存以外）
        if not h:
            return None
        try:
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            mem_ok = psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), ctypes.sizeof(PMC))
            created, exited, kern, usr = (wt.FILETIME(), wt.FILETIME(),
                                          wt.FILETIME(), wt.FILETIME())
            k32.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exited),
                                ctypes.byref(kern), ctypes.byref(usr))

            def _ft(v):
                return (v.dwHighDateTime * 4294967296 + v.dwLowDateTime) / 1e7

            handles = wt.DWORD(0)
            k32.GetProcessHandleCount(h, ctypes.byref(handles))
            return {'ws': float(pmc.WorkingSetSize) if mem_ok else 0.0,
                    'private': float(pmc.PagefileUsage) if mem_ok else 0.0,
                    'peak': float(pmc.PeakWorkingSetSize) if mem_ok else 0.0,
                    'cpu': _ft(kern) + _ft(usr),
                    'created': _ft(created) - 11644473600.0,
                    'handles': int(handles.value)}
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def perf_snapshot():
    """首页性能面板的数据：一次调用算「自上次调用以来」的 CPU 占比。"""
    now = time.time()
    pids = _perf_walk_pids()
    ws = private = peak = cpu = 0.0
    threads = handles = 0
    created = 0.0
    for pid, th in pids:
        s = _perf_proc_stats(pid)
        if not s:
            continue
        ws += s['ws']
        private += s['private']
        peak = max(peak, s['peak'])
        cpu += s['cpu']
        threads += int(th or 0)
        handles += s['handles']
        if pid == os.getpid():
            created = s['created']

    sys_total = sys_avail = 0.0
    sys_pct = 0.0
    idle = kern = user = 0.0
    try:
        import ctypes.wintypes as wt

        class MSEX(ctypes.Structure):
            _fields_ = [('dwLength', wt.DWORD), ('dwMemoryLoad', wt.DWORD),
                        ('ullTotalPhys', ctypes.c_ulonglong),
                        ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong),
                        ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong),
                        ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

        k32 = ctypes.windll.kernel32
        m = MSEX()
        m.dwLength = ctypes.sizeof(MSEX)
        if k32.GlobalMemoryStatusEx(ctypes.byref(m)):
            sys_total = m.ullTotalPhys / 1048576.0
            sys_avail = m.ullAvailPhys / 1048576.0
            sys_pct = float(m.dwMemoryLoad)

        i2, k2, u2 = wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if k32.GetSystemTimes(ctypes.byref(i2), ctypes.byref(k2), ctypes.byref(u2)):
            def _ft(v):
                return (v.dwHighDateTime * 4294967296 + v.dwLowDateTime) / 1e7
            idle, kern, user = _ft(i2), _ft(k2), _ft(u2)
    except Exception:
        pass

    prev = _PERF_PREV
    proc_pct = 0.0
    sys_now_pct = 0.0
    if prev['ts'] and now - prev['ts'] > 0.2:
        wall = now - prev['ts']
        cores = os.cpu_count() or 1
        proc_pct = max(0.0, min(999.0, (cpu - prev['proc']) / wall * 100.0 / cores))
        if prev['sys']:
            d_idle = idle - prev['sys'][0]
            d_total = (kern - prev['sys'][1]) + (user - prev['sys'][2])
            if d_total > 0:
                sys_now_pct = max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))
    _PERF_PREV.update({'ts': now, 'proc': cpu, 'sys': (idle, kern, user)})

    return {
        'ok': True,
        'ts': round(now, 1),
        'cores': os.cpu_count() or 1,
        'cpu': {'proc': round(proc_pct, 1), 'sys': round(sys_now_pct, 1)},
        'mem': {'proc_mb': round(ws / 1048576.0, 1),
                'private_mb': round(private / 1048576.0, 1),
                'peak_mb': round(peak / 1048576.0, 1),
                'proc_pct': round(ws / 1048576.0 / max(sys_total, 1) * 100.0, 1),
                'sys_used_mb': round(max(0.0, sys_total - sys_avail), 1),
                'sys_total_mb': round(sys_total, 1),
                'sys_pct': round(sys_pct, 1)},
        'proc': {'threads': threads, 'handles': handles,
                 'uptime_s': int(max(0.0, now - created)) if created else 0,
                 'pids': len(pids)},
    }


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

    print(f'Memex已启动：http://127.0.0.1:{port}（关闭本窗口即停止服务）')

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



