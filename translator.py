# -*- coding: utf-8 -*-
"""translator.py — 聚合翻译引擎（uTools 风格，零配置多语种极速聚合）

支持：
1. 智能语言探测：根据输入文本自动探测源语种（中文/日语/韩语/俄语/阿拉伯语/泰语/英文等）；
2. 自由配置多语种目标语言（支持中、英、日、韩、法、德、西、俄、葡、意、阿、泰、越等14种主流语言）；
3. 多引擎并行并发请求（3.5 秒极速超时熔断）：
   - Google 翻译（全球主流语种即时机器翻译）
   - MyMemory 神经机器翻译（全句与长文本开放聚合）
   - 有道词典公共接口（中英日韩词组、网络释义）
   - 百度词条解析接口（常用词汇与短语）
   - 金山词霸接口（双向词条与词义解析）
4. 结果聚合、去重与错误自愈。
5. 第 271 项：这三件事都不是"cookie 问题"，是"谁来扛限流"的问题 ——
   - 五家全是**匿名公开接口**（只发 User-Agent，一个 cookie / 密钥都不带），
     配额按**出口 IP** 计，也就是用户自己家宽的那一份。所以不存在"用谁的
     cookie"这回事，也**没必要去抓浏览器 cookie**（网页端要登录态 + 签名，
     接口一改就废，还违反人家条款）。
   - 真正的病根是 Google 那个 clients5 接口在国内直连不通（实测一次 21 秒、
     永远空结果），它把整个聚合拖死；再叠上"边打边译 350ms 一触发 × 五家全打"，
     用户自己的 IP 匿名配额很快就被自己烧光。
   - 现在的对策（都在本文件里，零配置）：逐引擎**冷却熔断** + **本机结果缓存**
     + **词典类只打词语**（句子不再发给三个词典接口）+ **总时限到点就返回**
     （慢的那家留在后台自己超时，不再拖住界面）。
"""
import concurrent.futures
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import netproxy            # 第 299 项：代理只留一份

# 第 277 项：可选依赖。没有这个模块（单测/独立脚本）时只是不计数，功能照常。
try:
    import feature_metrics as _metrics
except Exception:                                     # pragma: no cover
    _metrics = None


def _m(feature, status='ok', ms=None, sub=None, err=''):
    """埋点入口，异常一律吞掉 —— 统计失败绝不能影响翻译本身。"""
    if _metrics is None:
        return
    try:
        _metrics.add(feature, status, ms=ms, sub=sub, err=err)
    except Exception:
        pass

_TIMEOUT = 3.5
_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'

# ------------------------------------------------ 第 295 项：代理
# 病根：Google 的免密钥接口在国内直连不通，而 urllib 默认**不读 Windows 系统代理**
# （只认 http_proxy 环境变量）—— 于是用户明明开着 Clash「系统代理」，聚合里却永远
# 只剩 MyMemory 一家出结果。
# 规则（按优先级）：
#   1. settings.json 的 `trans_proxy`：填 'none'/'direct' = 强制直连；填 http://host:port = 用它；
#      不填或 'auto' = 跟随系统代理；
#   2. 系统代理（注册表 ProxyEnable/ProxyServer，Clash/V2Ray 开「系统代理」就写在这）；
#   3. 都没有 = 直连。
_PROXY_CACHE = {'at': 0.0, 'url': '', 'raw': None}
_PROXY_LOCK = threading.Lock()


def _sys_proxy():
    """Windows 系统代理 → 'http://host:port'；没开/不是 Windows 返回空串。

    第 299 项：实现搬到 netproxy，这里只为兼容老调用方留个名字。"""
    return netproxy.system_proxy()


def _sys_proxy_old():
    """（保留原实现备查；不再被调用）"""
    if os.name != 'nt':
        return ''
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r'Software\Microsoft\Windows\CurrentVersion\Internet Settings')
        try:
            enabled, _ = winreg.QueryValueEx(key, 'ProxyEnable')
        except OSError:
            enabled = 0
        if not int(enabled or 0):
            return ''
        try:
            server, _ = winreg.QueryValueEx(key, 'ProxyServer')
        except OSError:
            return ''
        raw = str(server or '').strip()
        if not raw:
            return ''
        # 可能是 "127.0.0.1:7897"，也可能是 "http=127.0.0.1:7897;https=127.0.0.1:7897"
        if '=' in raw:
            parts = {}
            for seg in raw.split(';'):
                if '=' in seg:
                    k, v = seg.split('=', 1)
                    parts[k.strip().lower()] = v.strip()
            raw = parts.get('https') or parts.get('http') or ''
        if not raw:
            return ''
        if '://' not in raw:
            raw = 'http://' + raw
        return raw
    except Exception:
        return ''


def proxy_url(force=False):
    """当前该用的代理（''=直连）。第 299 项起由 netproxy 统一决定：
    settings.json 的 proxy_mode（system/none/custom），老 trans_proxy 仍兼容。"""
    url = netproxy.proxy_url(force)
    # 兼容老代码/老测试读 translator._PROXY_CACHE
    try:
        with _PROXY_LOCK:
            _PROXY_CACHE.update({'at': time.time(), 'url': url, 'raw': None})
    except Exception:
        pass
    return url


def _opener(force=False):
    """按当前代理设置建一个 opener（netproxy 里统一缓存）。"""
    return netproxy.opener(force)


def _open(req, timeout=_TIMEOUT):
    """统一出口：所有引擎请求都从这里出去，才吃得到代理设置。"""
    return netproxy.urlopen(req, timeout=timeout)

# ------------------------------------------------ 第 271 项：限流自救
# 一次聚合最多等这么久（到点就返回已完成的，慢的留在后台自己超时）。
_OVERALL = 3.0
# 第 274 项：自带密钥的引擎（大模型/官方 API）本来就慢，给它们单独的预算。
# 只有这一批真的在本次请求里，才把总等待放大到 6.5s；否则一切照旧 3.0s。
_BYOK_TIMEOUT = 12.0
_BYOK_OVERALL = 6.5
# 词典类（有道/百度/金山）只适合词语，句子发了也是空结果还白烧配额。
_DICT_ONLY = ('youdao', 'baidu', 'iciba')
_WORD_MAX_WORDS = 3      # 英文等拉丁文本：≤3 个词算短语
_WORD_MAX_CJK = 8        # 中日韩没有空格，按字数卡（"今天的会议改到下午三点"=11 字，算句子）
# 本机结果缓存：同文本 + 同目标语言命中就直接复用，边打边译前缀重复率很高。
_CACHE_TTL = 600.0
_CACHE_MAX = 128
# 冷却：某家失败/超时后先歇一会儿，别拿用户自己的 IP 配额去硬撞限流。
_COOL_BASE = 60.0
_COOL_MAX = 300.0
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_FAILS = {}
_FAILS_LOCK = threading.Lock()

SUPPORTED_LANGUAGES = [
    ('auto', '智能识别'),
    ('en', '英语 (English)'),
    ('zh-CN', '中文 (简体中文)'),
    ('ja', '日语 (日本語)'),
    ('ko', '韩语 (한국어)'),
    ('fr', '法语 (Français)'),
    ('de', '德语 (Deutsch)'),
    ('es', '西班牙语 (Español)'),
    ('ru', '俄语 (Русский)'),
    ('pt', '葡萄牙语 (Português)'),
    ('it', '意大利语 (Italiano)'),
    ('ar', '阿拉伯语 (العربية)'),
    ('th', '泰语 (ภาษาไทย)'),
    ('vi', '越南语 (Tiếng Việt)'),
]


def is_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fa5]', str(text or '')))


# 第 273 项：各语种的字符集。以前 detect_lang 是"见到哪个字符集就返回哪个"，
# 于是「一句英文里夹一个汉字」就被判成中文 —— 自动模式随即按"中→外"翻，
# 英文原文原样返回，用户看到的就是"没翻"。改成按出现次数取占比最大的那个。
_SCRIPT_RE = {
    'ja': re.compile(r'[\u3040-\u309f\u30a0-\u30ff]'),      # 平假名 / 片假名
    'ko': re.compile(r'[\uac00-\ud7af\u1100-\u11ff]'),
    'zh-CN': re.compile(r'[\u4e00-\u9fa5]'),
    'ru': re.compile(r'[\u0400-\u04ff]'),
    'ar': re.compile(r'[\u0600-\u06ff]'),
    'th': re.compile(r'[\u0e00-\u0e7f]'),
}

# 拉丁语系的轻量判据：变音符 + 高频虚词。只用于「没有中日韩/西里尔/阿拉伯/泰文」
# 的纯拉丁文本，判不准就退回 en（退回是对的：真判成 en 也不会把德语翻坏）。
_LATIN_HINTS = {
    'de': (re.compile(r'[äöüßÄÖÜ]'),
           ('und', 'ist', 'nicht', 'der', 'die', 'das', 'sie', 'wir', 'bitte', 'konto',
            'werden', 'haben', 'diese', 'dieser',
            # 第 295 项：被动式/介词这些德语高频词，英语里没有同形，误判率低
            'wurde', 'wird', 'wurden', 'zum', 'zur', 'einem', 'einer', 'eines',
            'über', 'beim', 'durch', 'gegen', 'ohne', 'unter', 'zwischen')),
    'es': (re.compile(r'[áéíóúüñ¿¡]'),
           ('que', 'los', 'las', 'para', 'con', 'pero', 'esta', 'este', 'cuenta', 'porque')),
    'pt': (re.compile(r'[ãõçáâêôà]'),
           ('que', 'nao', 'não', 'para', 'com', 'uma', 'voce', 'você', 'conta', 'porque')),
    'fr': (re.compile(r'[àâçéèêëîïôùûœ]'),
           ('les', 'des', 'une', 'pas', 'pour', 'avec', 'vous', 'compte', 'parce')),
    'it': (re.compile(r'[àèéìòóù]'),
           ('che', 'non', 'per', 'una', 'sono', 'questo', 'conto', 'perché')),
}


def detect_lang(text):
    """推断文本主要语种：非拉丁字符按**占比**取胜，纯拉丁文本再按变音符/虚词细分。"""
    s = str(text or '')
    if not s.strip():
        return 'en'

    # 1) 非拉丁文字：谁的字数最多算谁（日语常夹汉字，所以假名优先级调高）
    counts = {}
    for code, rx in _SCRIPT_RE.items():
        n = len(rx.findall(s))
        if n:
            counts[code] = n
    cjk = sum(counts.get(k, 0) for k in ('ja', 'ko', 'zh-CN'))
    latin = len(re.findall(r'[A-Za-z\u00c0-\u024f]', s))

    # 西里尔 / 阿拉伯 / 泰文：有就直接算（它们不会和拉丁字母混着判）
    others = {k: v for k, v in counts.items() if k in ('ru', 'ar', 'th')}
    if others:
        return max(others.items(), key=lambda kv: kv[1])[0]

    # 一个非拉丁字符都没有 → 拉丁文本
    if not counts:
        return _latin_lang(s)

    # 拉丁字母明显多于中日韩字数 → 这是拉丁文本，混进来的几个汉字是名字/术语
    if latin >= cjk * 2:
        return _latin_lang(s)

    if counts.get('ja'):
        # 有假名就是日文：日文里的汉字不该把它判成中文
        if counts['ja'] >= counts.get('zh-CN', 0) or counts.get('zh-CN', 0) <= counts['ja'] * 3:
            return 'ja'
    if counts:
        best = max(counts.items(), key=lambda kv: kv[1])
        if best[0] == 'zh-CN' and counts.get('ja'):
            return 'ja'
        return best[0]
    return _latin_lang(s)


def _latin_lang(s):
    """纯拉丁文本细分：变音符 + 高频虚词，判不准就退 en。"""
    low = s.lower()
    words = set(re.findall(r"[a-zà-ÿœ]+", low))
    scores = {}
    for code, (dia, stops) in _LATIN_HINTS.items():
        hit = len(dia.findall(s)) * 2 + sum(1 for w in stops if w in words)
        if hit:
            scores[code] = hit
    if scores:
        best = max(scores.items(), key=lambda kv: kv[1])
        if best[1] >= 2:            # 至少 2 分才敢下结论，宁可变 en
            return best[0]
    return 'en'



def _skeleton(s):
    """去掉空白与标点后的"骨架"，只用来判断两条文本是不是同一句。

    MyMemory 之类的接口在翻不出来时会**原样吐回原文**，但顺手把句中的换行、
    多余空格、某些标点吃掉 —— `.strip().lower()` 比不出来，用户就看到
    "译文 = 原文"，也就是他说的"翻译不了"。
    """
    s = str(s or '').lower()
    s = re.sub(r'[\s\u3000]+', '', s)
    s = re.sub(r'[^\w\u4e00-\u9fa5]+', '', s, flags=re.UNICODE)
    return s


def looks_echo(src, out):
    """译文是不是"其实没翻"（与原文去掉空白标点后完全一致）。"""
    a = _skeleton(src)
    b = _skeleton(out)
    return bool(a) and a == b


# ---------------------------------------------------------------- 第 273 项：自动模式
# 用户原话：「"自动翻译"这个功能不应该局限于"中英翻译"，应该根据用户选择的界面语言，
# 我选的操作界面语言是中文，那么自动模式下，我输入德语、日语、英语都要翻译成中文」。
UI_LANG_CODE = {'zh': 'zh-CN', 'en': 'en', 'es': 'es', 'pt': 'pt', 'hi': 'hi'}
UI_LANG_NAME = {'zh-CN': '中文', 'en': 'English', 'es': 'Español', 'pt': 'Português', 'hi': 'हिंदी'}

# 「自动」这个选项在界面上的文字：跟着界面语言走，别写死"中/英互译"
AUTO_LABELS = {
    'zh-CN': '自动（译成中文）',
    'en': 'Auto (into English)',
    'es': 'Automático (al español)',
    'pt': 'Automático (para português)',
    'hi': 'स्वतः (हिंदी में)',
}


def ui_lang():
    """当前界面语言（zh/en/es/pt/hi）→ 语言代码。取不到就按 zh-CN。"""
    raw = ''
    try:
        import brain
        raw = str(brain.boot_lang() or '')
    except Exception:
        raw = ''
    if not raw:
        try:
            import brain
            raw = str(brain._settings_read().get('lang') or '')
        except Exception:
            raw = ''
    return UI_LANG_CODE.get(raw.strip().lower(), 'zh-CN')


def auto_target(src_lang, ui=None):
    """自动模式的目标语言：**译成界面语言**；输入本来就是界面语言时中英互转。

    * 界面中文 + 输入英/德/日/西/俄… → 中文（用户明确要求的那条）；
    * 界面中文 + 输入中文 → 英文（不然等于把中文再"翻"一遍，会原样吐回）；
    * 界面英文 + 输入英文 → 中文；界面西语 + 输入西语 → 英文。
    """
    ui = ui or ui_lang()
    same = (src_lang == ui) or (src_lang == 'zh-CN' and ui == 'zh-CN')
    if same:
        return 'en' if ui != 'en' else 'zh-CN'
    return ui


def auto_label(ui=None):
    ui = ui or ui_lang()
    return AUTO_LABELS.get(ui, AUTO_LABELS['zh-CN'])


def _fetch_google(text, src_lang, tgt_lang):
    """Google 翻译（Chrome 扩展免密钥多语种接口）。"""
    try:
        # 第 295 项：源语种一律 auto，让 Google 自己判。
        # 我们那套轻量 detect_lang 会把德语/荷兰语之类判成 en，带着 sl=en 去请求，
        # Google 就把德语当英语翻，原样吐回原文 —— 用户看到的就是"这段没翻"。
        sl = 'auto'
        tl = 'zh-CN' if tgt_lang.startswith('zh') else tgt_lang
        q = urllib.parse.quote(text.strip())
        url = f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl={sl}&tl={tl}&q={q}"
        req = urllib.request.Request(url, headers={'User-Agent': _UA})
        with _open(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            # 第 295 项：这个接口有两种返回形状 ——
            #   sl=auto 时是 [["译文","en"]]（嵌套），带上 sl 时是 ["译文"]（扁平）。
            # 以前只认嵌套那种，于是"明确指定源语言"的请求全部解析成空 → 看着像没翻。
            t = ''
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, list) and first:
                    t = ' '.join([str(x) for x in first[:1] if x]).strip()
                elif isinstance(first, str):
                    t = ' '.join([str(x) for x in data if isinstance(x, str)]).strip()
            if t:
                if looks_echo(text, t):
                    return {'engine': 'Google 翻译', 'echo': True}
                return {
                    'engine': 'Google 翻译',
                    'text': t,
                    'extra': '全球多语种'
                }
    except Exception:
        pass
    return None


def _fetch_mymemory(text, src_lang, tgt_lang):
    """MyMemory 神经机器翻译（全句与长文本聚合）。"""
    try:
        sl = detect_lang(text) if src_lang == 'auto' else src_lang
        tl = 'zh-CN' if tgt_lang.startswith('zh') else tgt_lang
        sl = 'zh-CN' if sl.startswith('zh') else sl
        pair = f"{sl}|{tl}"
        q = urllib.parse.quote(text.strip())
        url = f"https://api.mymemory.translated.net/get?q={q}&langpair={pair}"
        req = urllib.request.Request(url, headers={'User-Agent': _UA})
        with _open(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            trans = (data.get('responseData') or {}).get('translatedText')
            if trans and trans.strip():
                trans = str(trans).strip()
                if 'INVALID' in trans or 'EXAMPLE: LANGPAIR' in trans \
                        or 'PLEASE SELECT TWO DISTINCT' in trans:
                    return None
                # 第 273 项：原样吐回原文（连空白/标点都没动）也算没翻出来。
                # 以前只比 `.strip().lower()`，接口把换行和点后的空格吃掉就漏过去了，
                # 用户看到的就是"译文 = 原文"。
                if looks_echo(text, trans):
                    return {'engine': 'MyMemory 翻译', 'echo': True}
                return {
                    'engine': 'MyMemory 翻译',
                    'text': trans,
                    'extra': '整句聚合'
                }
    except Exception:
        pass
    return None


_TENCENT_LANG = {
    'zh-CN': 'zh', 'zh': 'zh', 'en': 'en', 'ja': 'ja', 'ko': 'ko', 'de': 'de',
    'fr': 'fr', 'es': 'es', 'pt': 'pt', 'ru': 'ru', 'ar': 'ar', 'th': 'th',
    'it': 'it', 'vi': 'vi',
}


def _fetch_tencent(text, src_lang, tgt_lang):
    """腾讯交互翻译（transmart，网页免密钥接口，国内可直连）。

    第 295 项补的第三家整句引擎：Google 不通、MyMemory 偶尔限流时，
    国内用户至少还有这家的整句译文。请求体要求 `source.text_list`（数组）。
    """
    try:
        body = json.dumps({
            'header': {'fn': 'auto_translation',
                       'client_key': 'browser-chrome-110.0.0.0-20250506-0-1-1-0-0'},
            'type': 'plain', 'model_category': 'normal',
            # 源语种同样交给接口自己判（判错了它会翻出完全不相干的句子）
            'source': {'lang': 'auto', 'text_list': [text.strip()]},
            'target': {'lang': _TENCENT_LANG.get(tgt_lang, 'zh')},
        }).encode('utf-8')
        req = urllib.request.Request('https://transmart.qq.com/api/imt', data=body,
                                     headers={'Content-Type': 'application/json',
                                              'User-Agent': _UA})
        with _open(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        arr = data.get('auto_translation') or []
        out = str((arr[0] if isinstance(arr, list) and arr else '') or '').strip()
        if out:
            if looks_echo(text, out):
                return {'engine': '腾讯交互翻译', 'echo': True}
            return {'engine': '腾讯交互翻译', 'text': out, 'extra': '国内直连'}
    except Exception:
        pass
    return None


def _fetch_youdao_dict(text, src_lang, tgt_lang):
    """有道词典公共建议接口（词汇释义）。"""
    try:
        q = urllib.parse.quote(text.strip())
        le = 'en'
        if tgt_lang in ('ja', 'ko'):
            le = 'jap' if tgt_lang == 'ja' else 'ko'
        elif not is_chinese(text):
            le = 'zh'
        url = f"https://dict.youdao.com/suggest?num=5&ver=3.0&doctype=json&cache=false&le={le}&q={q}"
        req = urllib.request.Request(url, headers={'User-Agent': _UA})
        with _open(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            entries = (data.get('data') or {}).get('entries') or []
            if entries:
                first = entries[0]
                exp = first.get('explain', '')
                if exp and exp.strip():
                    if looks_echo(text, exp):
                        return {'engine': '有道词典', 'echo': True}
                    return {
                        'engine': '有道词典',
                        'text': exp.strip(),
                        'extra': '词典释义'
                    }
    except Exception:
        pass
    return None


def _fetch_baidu_sug(text, src_lang, tgt_lang):
    """百度词典接口（中英词条解析）。"""
    try:
        data = urllib.parse.urlencode({'kw': text.strip()}).encode('utf-8')
        req = urllib.request.Request('https://fanyi.baidu.com/sug', data=data, headers={'User-Agent': _UA})
        with _open(req, timeout=_TIMEOUT) as resp:
            res = json.loads(resp.read().decode('utf-8'))
            items = res.get('data') or []
            if items:
                v = items[0].get('v', '')
                if v and v.strip():
                    if looks_echo(text, v):
                        return {'engine': '百度词典', 'echo': True}
                    return {
                        'engine': '百度词典',
                        'text': v.strip(),
                        'extra': '词条解析'
                    }
    except Exception:
        pass
    return None


def _fetch_iciba_dict(text, src_lang, tgt_lang):
    """金山词霸接口（词汇解析）。"""
    try:
        q = urllib.parse.quote(text.strip())
        url = f"https://dict-mobile.iciba.com/interface/index.php?c=word&m=getsuggest&nums=5&is_need_mean=1&word={q}"
        req = urllib.request.Request(url, headers={'User-Agent': _UA})
        with _open(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            msgs = data.get('message') or []
            if msgs and isinstance(msgs, list):
                para = msgs[0].get('paraphrase')
                if para and para.strip():
                    if looks_echo(text, para):
                        return {'engine': '金山词霸', 'echo': True}
                    return {
                        'engine': '金山词霸',
                        'text': para.strip(),
                        'extra': '词汇解析'
                    }
    except Exception:
        pass
    return None


# ------------------------------------------------ 第 274 项：自带密钥（BYOK）
# 上面五家都是"网页免密钥接口"，额度按**出口 IP** 算（第 271 项已取证）：
# 大家共用同一份限额，用户一多就互相吃。下面这三家走**用户自己的账号**：
#   1. DeepSeek —— OpenAI 兼容的 chat 接口，按 token 计费，译文质量最好、最慢；
#   2. DeepL    —— 官方 API，免费额度 50 万字符/月（key 以 `:fx` 结尾走 api-free）；
#   3. 百度开放平台 —— 官方通用翻译 API，appid+secret 签名，按账号免费额度。
# 密钥只存在本机 settings.json，请求从本机直发，**永远不回显、不写日志**。
BYOK_SPECS = {
    # 第 279 项：用户「API 接入太少了，谷歌这种居然没有」——补 Google 云翻译与 OpenAI 兼容
    'google_api': {'name': 'Google 云翻译', 'note': '自带密钥'},
    'deepseek': {'name': 'DeepSeek', 'note': '自带密钥'},
    'deepl': {'name': 'DeepL', 'note': '自带密钥'},
    'openai': {'name': 'OpenAI 兼容', 'note': '自带密钥'},
    'baidu_api': {'name': '百度开放平台', 'note': '自带密钥'},
}
BYOK_IDS = tuple(BYOK_SPECS)
# 每家要填哪几项才算"配好"（模型名有默认值，不算必填）
BYOK_REQUIRED = {
    'google_api': ('key',),
    'deepseek': ('key',),
    'deepl': ('key',),
    'openai': ('key',),
    'baidu_api': ('appid', 'secret'),
}
BYOK_FIELDS = ('key', 'model', 'appid', 'secret')

# 译文语言给 DeepSeek 看的自然语言名字（提示词里用中文名最稳）
_LANG_CN = {
    'zh-CN': '简体中文', 'zh': '简体中文', 'en': '英文', 'ja': '日文', 'ko': '韩文',
    'de': '德文', 'fr': '法文', 'es': '西班牙文', 'pt': '葡萄牙文', 'ru': '俄文',
    'ar': '阿拉伯文', 'th': '泰文', 'it': '意大利文',
}
_DEEPL_CODE = {
    'zh-CN': 'ZH', 'zh': 'ZH', 'en': 'EN', 'de': 'DE', 'ja': 'JA', 'ko': 'KO',
    'fr': 'FR', 'es': 'ES', 'pt': 'PT', 'ru': 'RU', 'ar': 'AR', 'th': 'TH', 'it': 'IT',
}
_GOOGLE_CODE = {
    'zh-CN': 'zh-CN', 'zh': 'zh-CN', 'en': 'en', 'ja': 'ja', 'ko': 'ko', 'de': 'de',
    'fr': 'fr', 'es': 'es', 'pt': 'pt', 'ru': 'ru', 'ar': 'ar', 'th': 'th', 'it': 'it',
}
_BAIDU_CODE = {
    'zh-CN': 'zh', 'zh': 'zh', 'en': 'en', 'ja': 'jp', 'ko': 'kor', 'de': 'de',
    'fr': 'fra', 'es': 'spa', 'pt': 'pt', 'ru': 'ru', 'ar': 'ara', 'th': 'th', 'it': 'it',
}


def byok_cfg():
    """读 settings.json 的 `trans_byok`；读不到/坏掉一律当没填。"""
    try:
        import brain
        raw = brain._settings_read().get('trans_byok') or {}
    except Exception:
        raw = {}
    out = {}
    for pid in BYOK_IDS:
        item = raw.get(pid) if isinstance(raw, dict) else None
        out[pid] = {k: str((item or {}).get(k) or '').strip() for k in BYOK_FIELDS} \
            if isinstance(item, dict) else {k: '' for k in BYOK_FIELDS}
    return out


def byok_ready(cfg=None):
    """哪几家已经配全（只返回 True/False，不返回密钥本身）。"""
    cfg = cfg or byok_cfg()
    return {pid: all(bool((cfg.get(pid) or {}).get(f)) for f in BYOK_REQUIRED[pid])
            for pid in BYOK_IDS}


def mask_secret(value, keep=4):
    """给界面看的掩码：`sk-1234abcd` → `sk-…cd`。短到没意义的直接打星。"""
    s = str(value or '').strip()
    if not s:
        return ''
    if len(s) <= keep + 2:
        return '•' * len(s)
    return s[:3] + '…' + s[-keep:]


def _byok_skip(pid):
    """没填密钥时的返回：不是失败，别进冷却，也别在每次翻译里刷存在感。"""
    return {'engine': BYOK_SPECS[pid]['name'], 'skip': '没填密钥（设置 → 聚合翻译 → 自带密钥）'}


def _fetch_deepseek(text, src_lang, tgt_lang):
    """DeepSeek（OpenAI 兼容 chat/completions）。译文质量最好，但也是这三家里最慢的。"""
    cfg = (byok_cfg().get('deepseek') or {})
    if not cfg.get('key'):
        return _byok_skip('deepseek')
    base = (cfg.get('model') and '') or 'https://api.deepseek.com'
    model = cfg.get('model') or 'deepseek-chat'
    if cfg.get('model', '').startswith('http'):
        # 兼容自定义 base：把 model 栏当 base 用，模型退回默认（省得再加一个输入框）
        base = cfg.get('model').rstrip('/')
        model = 'deepseek-chat'
    tgt = _LANG_CN.get(tgt_lang, tgt_lang)
    src = '原文' if (not src_lang or src_lang == 'auto') else _LANG_CN.get(src_lang, src_lang)
    body = {
        'model': model,
        'messages': [
            {'role': 'system',
             'content': '你是翻译引擎。只输出译文本身：不要解释、不要加引号、不要重复原文，'
                        '保留原有的换行与缩进。'},
            {'role': 'user',
             'content': '把下面这段%s翻译成%s：\n\n%s' % (src, tgt, text)},
        ],
        'temperature': 0.2,
        'max_tokens': 1024,
        'stream': False,
    }
    req = urllib.request.Request(
        base + '/chat/completions', data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cfg['key'],
                 'User-Agent': _UA})
    with _open(req, timeout=_BYOK_TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    choices = data.get('choices') or []
    text_out = ''
    if choices:
        msg = choices[0].get('message') or {}
        text_out = str(msg.get('content') or '').strip()
    if not text_out:
        return None
    if looks_echo(text, text_out):
        return {'engine': 'DeepSeek', 'echo': True}
    model_used = str(data.get('model') or model)
    return {'engine': 'DeepSeek', 'text': text_out, 'extra': model_used}


def _fetch_deepl(text, src_lang, tgt_lang):
    """DeepL 官方 API。key 以 `:fx` 结尾的是免费版，必须走 api-free 域名。"""
    cfg = (byok_cfg().get('deepl') or {})
    key = cfg.get('key') or ''
    if not key:
        return _byok_skip('deepl')
    host = 'https://api-free.deepl.com' if key.endswith(':fx') else 'https://api.deepl.com'
    form = {'text': text, 'target_lang': _DEEPL_CODE.get(tgt_lang, 'EN')}
    if src_lang and src_lang != 'auto' and src_lang in _DEEPL_CODE:
        form['source_lang'] = _DEEPL_CODE[src_lang]
    req = urllib.request.Request(
        host + '/v2/translate', data=urllib.parse.urlencode(form).encode('utf-8'),
        headers={'Authorization': 'DeepL-Auth-Key ' + key, 'User-Agent': _UA,
                 'Content-Type': 'application/x-www-form-urlencoded'})
    with _open(req, timeout=_BYOK_TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    items = data.get('translations') or []
    if not items:
        return None
    out = str(items[0].get('text') or '').strip()
    if not out:
        return None
    if looks_echo(text, out):
        return {'engine': 'DeepL', 'echo': True}
    return {'engine': 'DeepL', 'text': out, 'extra': '官方 API'}


def _fetch_google_api(text, src_lang, tgt_lang):
    """Google Cloud Translation v2（只要 API Key）。

    额度算用户自己 GCP 项目的：免费每月 50 万字符（未绑卡）/ 付费按项目结算。
    key 放**请求体**而不是 URL —— URL 会进日志和报错信息。
    """
    cfg = (byok_cfg().get('google_api') or {})
    key = cfg.get('key') or ''
    if not key:
        return _byok_skip('google_api')
    form = {'q': text, 'target': _GOOGLE_CODE.get(tgt_lang, 'en'),
            'format': 'text', 'key': key}
    sl = _GOOGLE_CODE.get(src_lang or '')
    if src_lang and src_lang != 'auto' and sl:
        form['source'] = sl
    req = urllib.request.Request(
        'https://translation.googleapis.com/language/translate/v2',
        data=urllib.parse.urlencode(form).encode('utf-8'),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': _UA})
    try:
        with _open(req, timeout=_BYOK_TIMEOUT) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        # 400 = key 不对 / 项目没开 Cloud Translation API —— 把 Google 的原话带回来
        try:
            err = json.loads(e.read().decode('utf-8')).get('error') or {}
        except Exception:
            err = {}
        raise RuntimeError('Google 接口 %s：%s' % (e.code, err.get('message') or e.reason))
    tr = ((body.get('data') or {}).get('translations') or [])
    if not tr:
        return None
    import html as _html
    out = _html.unescape(str(tr[0].get('translatedText') or '')).strip()
    if not out:
        return None
    if looks_echo(text, out):
        return {'engine': 'Google 云翻译', 'echo': True}
    return {'engine': 'Google 云翻译', 'text': out, 'extra': '官方 API'}


def _fetch_openai(text, src_lang, tgt_lang):
    """任意 OpenAI 兼容的 chat/completions（OpenAI / 硅基流动 / Kimi / 火山方舟…）。

    model 栏的用法和 DeepSeek 一样：填模型名就用官方域名，填 http 开头的就把它当 base。
    """
    cfg = (byok_cfg().get('openai') or {})
    if not cfg.get('key'):
        return _byok_skip('openai')
    base = 'https://api.openai.com/v1'
    model = 'gpt-4o-mini'
    raw = (cfg.get('model') or '').strip()
    if raw.startswith('http'):
        base = raw.rstrip('/')
    elif raw:
        model = raw
    tgt = _LANG_CN.get(tgt_lang, tgt_lang)
    src = '原文' if (not src_lang or src_lang == 'auto') else _LANG_CN.get(src_lang, src_lang)
    body = {
        'model': model,
        'messages': [
            {'role': 'system',
             'content': '你是翻译引擎。只输出译文本身：不要解释、不要加引号、不要重复原文，'
                        '保留原有的换行与缩进。'},
            {'role': 'user',
             'content': '把下面这段%s翻译成%s：\n\n%s' % (src, tgt, text)},
        ],
        'temperature': 0.2,
        'max_tokens': 1024,
        'stream': False,
    }
    req = urllib.request.Request(
        base + '/chat/completions', data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cfg['key'],
                 'User-Agent': _UA})
    with _open(req, timeout=_BYOK_TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    choices = data.get('choices') or []
    out = ''
    if choices:
        out = str((choices[0].get('message') or {}).get('content') or '').strip()
    if not out:
        return None
    if looks_echo(text, out):
        return {'engine': 'OpenAI 兼容', 'echo': True}
    return {'engine': 'OpenAI 兼容', 'text': out,
            'extra': str(data.get('model') or model)}


def _fetch_baidu_api(text, src_lang, tgt_lang):
    """百度翻译开放平台（appid + secret，MD5 签名）。"""
    cfg = (byok_cfg().get('baidu_api') or {})
    appid = cfg.get('appid') or ''
    secret = cfg.get('secret') or ''
    if not (appid and secret):
        return _byok_skip('baidu_api')
    import hashlib
    salt = str(int(time.time() * 1000))
    sign = hashlib.md5((appid + text + salt + secret).encode('utf-8')).hexdigest()
    form = {
        'q': text,
        'from': _BAIDU_CODE.get(src_lang, 'auto') if src_lang != 'auto' else 'auto',
        'to': _BAIDU_CODE.get(tgt_lang, 'zh'),
        'appid': appid, 'salt': salt, 'sign': sign,
    }
    req = urllib.request.Request(
        'https://fanyi-api.baidu.com/api/trans/vip/translate',
        data=urllib.parse.urlencode(form).encode('utf-8'),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': _UA})
    with _open(req, timeout=_BYOK_TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if data.get('error_code'):
        # 54003 = 访问频率受限，54001 = 签名错误 …… 都当失败进冷却，别硬撞
        raise RuntimeError('百度接口 %s：%s' % (data.get('error_code'), data.get('error_msg') or ''))
    parts = [str(x.get('dst') or '') for x in (data.get('trans_result') or [])]
    out = '\n'.join([p for p in parts if p]).strip()
    if not out:
        return None
    if looks_echo(text, out):
        return {'engine': '百度开放平台', 'echo': True}
    return {'engine': '百度开放平台', 'text': out, 'extra': '官方 API'}


# ------------------------------------------------ 引擎注册表（第 268 项）
# id 是写进 settings.json 的稳定字符串：用户勾选状态就存它，
# 一旦改名/改序，老用户的配置就会静默失效 —— 所以 id 只增不改（第 274 项只增三家）。
# name/note 只用于设置页展示，前端也从 /api/translate 的 engines 字段里认得出来。
ENGINES = [
    {'id': 'google',    'name': 'Google 翻译',   'note': '全球 14 语',  'since': 268},
    {'id': 'mymemory',  'name': 'MyMemory',      'note': '全球记忆库',   'since': 268},
    {'id': 'tencent',   'name': '腾讯交互翻译',   'note': '国内直连',    'since': 295},
    {'id': 'youdao',    'name': '有道词典',       'note': '词库释义',    'since': 268},
    {'id': 'baidu',     'name': '百度词典',       'note': '权威词释',    'since': 268},
    {'id': 'iciba',     'name': '金山词霸',       'note': '权威词典',    'since': 268},
    {'id': 'deepseek',  'name': 'DeepSeek',      'note': '自带密钥',    'since': 274},
    {'id': 'deepl',     'name': 'DeepL',         'note': '自带密钥',    'since': 274},
    {'id': 'baidu_api', 'name': '百度开放平台',    'note': '自带密钥',    'since': 274},
    {'id': 'google_api', 'name': 'Google 云翻译',  'note': '自带密钥',    'since': 279},
    {'id': 'openai',    'name': 'OpenAI 兼容',   'note': '自带密钥',    'since': 279},
]
# 当前"版本号"= 最近一次新增引擎的项号。用户保存勾选时会把它写进 trans_engines_ver。
ENGINE_VER = 295
_SINCE = {e['id']: int(e.get('since') or 0) for e in ENGINES}
ENGINE_IDS = [e['id'] for e in ENGINES]

# 第 346 项：**默认只开前四个**（Google / MyMemory / 腾讯 / 有道）。
# 一上来十一引擎全开，用户看到的是一屏结果、等的是最慢那家；后五个「自带密钥」
# 的更没意义 —— 没填密钥本来就打不通。用户要更多，去设置里一个个点亮。
DEFAULT_ENGINES = ['google', 'mymemory', 'tencent', 'youdao']


def migrate_346(st):
    """第 346 项（B）：把「从没动过」的老配置一次性收敛到默认前四个。

    背景：268 项的默认是「全部开着」，所以老用户的 settings.json 里躺着一串
    十来个引擎的清单 —— 打开设置页看到满屏绿灯，翻译时等最慢那家。
    这里只动**一次**（写 `trans_engines_346` 标记），之后用户的任何调整都按原样尊重。

    判定「从没动过」：五家常用免密钥引擎（google/mymemory/tencent/youdao/baidu）
    全在清单里 —— 真关过一家的人会缺其中至少一个，那种保持原样。
    配好密钥的 BYOK 引擎保留（他已经花过成本）；没密钥的 BYOK 一律收起。

    返回 (新的 settings 副本, 是否改动过)。
    """
    st = dict(st or {})
    if st.get('trans_engines_346'):
        return st, False
    saved = normalize_engines(st.get('trans_engines')) if st.get('trans_engines') else []
    keep = list(saved) if saved else []
    looks_untouched = all(k in saved for k in
                          ('google', 'mymemory', 'tencent', 'youdao', 'baidu'))
    if looks_untouched or not saved:
        out = list(DEFAULT_ENGINES)
        try:
            ready = byok_ready()
        except Exception:
            ready = {}
        for eid in ('deepseek', 'deepl', 'baidu_api', 'google_api', 'openai'):
            if (ready or {}).get(eid) and eid not in out:
                out.append(eid)
        keep = out
    st['trans_engines'] = keep
    st['trans_engines_346'] = True
    st['trans_engines_ver'] = ENGINE_VER
    return st, True


# 第 278 项：结果右上角那个小徽标（extra）原来也是写死中文 —— 英文/印地界面里
# 会露出「词条解析」「官方 API」。这里给固定徽标配一个语言无关的 code，前端按界面语言翻；
# 不是固定文案的（比如 DeepSeek 的模型名）原样透传。
EXTRA_CODES = {
    '全球多语种': 'multi_lang',
    '整句聚合': 'sentence',
    '词典释义': 'dict_meaning',
    '词条解析': 'entry_parse',
    '词汇解析': 'lexical',
    '官方 API': 'official_api',
    '国内直连': 'cn_direct',
}


def _engine_id_of(name):
    """引擎标签 → id（第 278 项）。

    界面要按「界面语言」显示引擎名，所以结果里必须带一个语言无关的 id；
    这里把后端原有那句中文标签反查回 id，查不到就给空串（前端会退回原标签）。
    """
    n = str(name or '').strip()
    if not n:
        return ''
    if n in ENGINE_IDS or n in BYOK_IDS:
        return n
    for e in ENGINES:
        if e['name'] and e['name'] in n:
            return e['id']
    for pid, spec in BYOK_SPECS.items():
        if spec.get('name') and spec['name'] in n:
            return pid
    return ''


_ENGINE_FETCHERS = {
    'google': _fetch_google,
    'mymemory': _fetch_mymemory,
    'tencent': _fetch_tencent,
    'youdao': _fetch_youdao_dict,
    'baidu': _fetch_baidu_sug,
    'iciba': _fetch_iciba_dict,
    'deepseek': _fetch_deepseek,
    'deepl': _fetch_deepl,
    'baidu_api': _fetch_baidu_api,
    'google_api': _fetch_google_api,
    'openai': _fetch_openai,
}


def active_engines(st=None):
    """用户实际要打的引擎清单（settings.json 的勾选 + 后来新增的引擎）。

    第 295 项的由来：用户在第 268 项存过一次勾选（只有 google/youdao/baidu/mymemory），
    之后新增的引擎（自带密钥那 5 家、腾讯）一个都不在清单里 —— 设置页列着 10 家，
    请求时却只打 4 家，看着就像"只有 MyMemory 会翻"。
    规则：`since` 晚于用户保存时的 `trans_engines_ver` 的引擎自动补进来（他从没见过，
    默认开）；他保存时就已存在、但没勾的（比如 iciba）保持关。老配置没有 ver，
    按 268 算。
    """
    st = st if isinstance(st, dict) else {}
    saved = st.get('trans_engines')
    # 第 346 项：**从没保存过**（全新安装 / 从没用过翻译）→ 直接给默认前四个，
    # 不要走下面「后来新增的引擎自动补进来」那段：那段会把五家自带密钥的也补上，
    # 而没填密钥的引擎根本打不通，只会把首屏拖慢。
    if not saved:
        return list(DEFAULT_ENGINES)
    chosen = normalize_engines(saved)
    try:
        ver = int(st.get('trans_engines_ver') or 0)
    except Exception:
        ver = 0
    if not ver:
        ver = 268
    for eid in ENGINE_IDS:
        if eid not in chosen and _SINCE.get(eid, 0) > ver:
            chosen.append(eid)
    return chosen


def normalize_engines(value):
    """把设置里/请求里的引擎列表收敛成合法的 id 列表。

    - 不认识的 id 丢掉：删过引擎的旧配置也不会炸；
    - 保留调用方给的顺序：关掉 Google 之后，剩下的引擎顺序不该跳；
    - 全空/全非法 → 回全部：宁可多打几次请求，也不能让"关到一个不剩"把
      翻译功能整个关死（前端也同样禁止关掉最后一个）。
    """
    ids = []
    for x in (value or []):
        k = str(x or '').strip().lower()
        if k in _ENGINE_FETCHERS and k not in ids:
            ids.append(k)
    return ids or list(DEFAULT_ENGINES)


# ------------------------------------------------ 第 271 项：小工具
def looks_like_word(text):
    """是不是「词语/短语」（词典类引擎只该在这个长度区间被调用）。

    日韩和中文没有空格，没法按词数算，按字数：≤8 字算词（"会议"、"人工智能"），
    「今天的会议改到下午三点」这种 11 字就是句子。拉丁文本按词数 ≤3（"machine learning"）。
    """
    s = str(text or '').strip()
    if not s or len(s) > 40:
        return False
    if re.search(r'[\u4e00-\u9fa5\u3040-\u30ff\uac00-\ud7af]', s):
        return len(re.sub(r'\s+', '', s)) <= _WORD_MAX_CJK and not re.search(r'[，。！？；：,.!?;:]', s)
    return len(s.split()) <= _WORD_MAX_WORDS


def _cache_key(engine, text, tgt):
    return (engine, str(text).strip(), str(tgt))


def _cache_get(key):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        ts, val = item
        if time.time() - ts > _CACHE_TTL:
            _CACHE.pop(key, None)
            return None
        return val


def _cache_put(key, val):
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), val)
        if len(_CACHE) > _CACHE_MAX:
            # 先扔最老的：dict 在 3.7+ 保持插入顺序，取前几个删掉就行
            for k in list(_CACHE)[:len(_CACHE) - _CACHE_MAX]:
                _CACHE.pop(k, None)


def cooling(engine):
    """这家是否还在冷却期。返回剩余秒数（0 = 可以打）。"""
    with _FAILS_LOCK:
        st = _FAILS.get(engine) or (0, 0.0)
        left = st[1] - time.time()
        return left if left > 0 else 0.0


def _mark_fail(engine):
    """失败一次就歇 _COOL_BASE 秒，连着失败一路翻倍到 _COOL_MAX。"""
    with _FAILS_LOCK:
        n, _until = _FAILS.get(engine) or (0, 0.0)
        n += 1
        wait = min(_COOL_MAX, _COOL_BASE * (2 ** (n - 1)))
        _FAILS[engine] = (n, time.time() + wait)
        return wait


def _mark_ok(engine):
    with _FAILS_LOCK:
        _FAILS.pop(engine, None)


# Google 那家在墙内直连通常是不通的，跟踪一次 21 秒才放弃（六个地址轮着超时）。
# 所以发请求前先用 TCP 探一下，1.2 秒连不上就别提交 —— 免得白挂线程、白等配额。
_PREFLIGHT = {'google': ('clients5.google.com', 443)}
_REACH = {}
_REACH_LOCK = threading.Lock()


def reachable(engine):
    """要不要为这家先探活；探不通就跳过。非预检引擎一律返回 True。

    注意：create_connection 对「被墙」的域名会把六个地址轮流超时（实测能拖到 7 秒），
    所以探活必须整体封顶 —— 扔进线程里 join(1.0)，超时直接当连不上。
    """
    host = _PREFLIGHT.get(engine)
    if not host:
        return True
    px = proxy_url()
    if px and engine == 'google':
        # 走代理时真正要连的是代理端口，google 域名由代理去解析
        target = px.split('://', 1)[-1].split('/', 1)[0]
        if ':' in target:
            host, port = target.rsplit(':', 1)
            try:
                host = (host, int(port))
            except ValueError:
                host = (host, 7897)
    with _REACH_LOCK:
        cached = _REACH.get(engine)
        # 老写法只存了 (时间, 结果) 两元；没有第三项时按"当前代理"处理，别把它当失效
        cached_px = tuple(cached[2:]) if cached and len(cached) > 2 else None
        if cached and time.time() - cached[0] < 600 and (cached_px in (None, (px,))):
            return cached[1]

    result = {'ok': False}

    def probe():
        import socket
        try:
            s = socket.create_connection(host, timeout=0.9)
            s.close()
            result['ok'] = True
        except Exception:
            result['ok'] = False

    th = threading.Thread(target=probe, daemon=True)
    th.start()
    th.join(1.0)
    ok = result['ok'] and not th.is_alive()
    with _REACH_LOCK:
        _REACH[engine] = (time.time(), ok, px)
    return ok


def reset_limits():
    """清空缓存与冷却（单测/排查用，也给以后设置页的「重置」留口子）。"""
    with _CACHE_LOCK:
        _CACHE.clear()
    with _FAILS_LOCK:
        _FAILS.clear()


def translate_text(text, src_lang='auto', tgt_lang='auto', engines=None):
    """多引擎并发聚合翻译。
    
    参数：
    - text: 待翻译文本
    - src_lang: 源语言代码，默认 'auto' 智能识别
    - tgt_lang: 目标语言代码，默认 'auto' 依配置与语种智能分配
    - engines: 本次要打的引擎 id 列表（第 268 项）。None = 读 settings.json 的
      `trans_engines`（用户设置页里勾的那几个）；用户全关等于全开，见 normalize_engines()
    """
    if not text or not str(text).strip():
        _m('translate', 'empty', sub='blank')
        return {'ok': False, 'msg': '文本为空', 'results': []}

    chosen = list(ENGINE_IDS)
    if engines is not None:
        chosen = normalize_engines(engines)
    else:
        try:
            import brain
            st = brain._settings_read()
            chosen = active_engines(st)
        except Exception:
            pass

    s = str(text).strip()
    detected_src = detect_lang(s)

    actual_src = detected_src if (not src_lang or src_lang == 'auto') else src_lang

    # 第 273 项：「自动」= 译成**当前界面语言**，跟 settings 里的"默认目标语言"无关。
    # 以前这里是"源语言是中文就译成默认语言、否则译成中文"，而且 API 层还把
    # tl='auto' 悄悄换成 settings.trans_target_lang —— 于是用户选了"自动"、
    # 默认语言又正好等于源语言时，就变成"英语→英语"，接口原样吐回，看着就像翻译不了。
    ui_tgt = ui_lang()
    actual_tgt = tgt_lang
    if not actual_tgt or actual_tgt == 'auto':
        actual_tgt = auto_target(actual_src, ui_tgt)

    if actual_src == actual_tgt:
        actual_tgt = 'zh-CN' if actual_src != 'zh-CN' else 'en'

    results = []
    seen = set()
    skipped = []
    word_like = looks_like_word(s)
    _t0 = time.time()

    # 第 271 项：先按「缓存命中 / 冷却中 / 这一家根本不适合这段话」把活分好，
    # 真正发出去的请求越少，用户自己的 IP 配额越经得住边打边译。
    # 第 274 项：自带密钥的三家，没填密钥就当这项功能不存在（不写进 skipped，
    # 否则每翻一次都刷一行"没填密钥"，用户还以为坏了）。
    ready = byok_ready()
    todo = []
    for k in chosen:
        if k in BYOK_IDS and not ready.get(k):
            _m('translate_engine', 'skip', sub=k)
            continue
        if k in _DICT_ONLY and not word_like:
            skipped.append({'engine': k, 'reason': '只用于词语', 'code': 'dict_only'})
            _m('translate_engine', 'skip', sub=k)
            continue
        left = cooling(k)
        if left:
            skipped.append({'engine': k, 'reason': '限流冷却 %.0fs' % left,
                            'code': 'cooling', 'secs': int(left)})
            _m('translate_engine', 'skip', sub=k)
            continue
        if not reachable(k):
            skipped.append({'engine': k, 'reason': '本机连不上（网络/代理）', 'code': 'offline'})
            _m('translate_engine', 'skip', sub=k)
            continue
        hit = _cache_get(_cache_key(k, s, actual_tgt))
        if hit:
            if _metrics is not None:
                try:
                    _metrics.count('translate', 'cache_hit')
                except Exception:
                    pass
            if hit['text'] not in seen:
                seen.add(hit['text'])
                results.append(dict(hit, cached=True))
            continue
        todo.append(k)

    if todo:
        # 自带密钥那几家（DeepSeek/DeepL/百度开放平台）是走用户的账号，本来就慢，
        # 总等待给它 6.5s；纯免密钥的组合保持 3.0s，别被它们拖回 21 秒那会儿。
        overall = _BYOK_OVERALL if any(k in BYOK_IDS for k in todo) else _OVERALL
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(todo))
        futs = {}
        try:
            for k in todo:
                futs[executor.submit(_ENGINE_FETCHERS[k], s, actual_src, actual_tgt)] = k
            # 到点就收：慢的那家留在后台自己超时，界面不再被它拖住
            done, pending = concurrent.futures.wait(
                list(futs), timeout=overall, return_when=concurrent.futures.ALL_COMPLETED)
            for f in done:
                k = futs[f]
                try:
                    res = f.result()
                except Exception as e:
                    # 异常（多半是限流/断网）才冷却；「查无此词」不算失败，
                    # 否则用户查一个生僻词就把这家歇一分钟，太冤。
                    wait = _mark_fail(k)
                    _m('translate_engine', 'fail', sub=k, err=str(e)[:120])
                    skipped.append({'engine': k, 'code': 'error',
                                    'reason': '%s，冷却 %.0fs' % (str(e) or e.__class__.__name__, wait)})
                    continue
                if res and res.get('skip'):
                    # 「没填密钥」这类明确说明：照实告诉用户，不进冷却
                    _m('translate_engine', 'skip', sub=k)
                    skipped.append({'engine': k, 'reason': res['skip'], 'code': 'no_key'})
                    continue
                if res and res.get('echo'):
                    # 接口把原文吐回来了 —— 不当作译文，也不冷却（不是限流）
                    _mark_ok(k)
                    _m('translate_engine', 'echo', sub=k)
                    skipped.append({'engine': k, 'reason': '只返回了原文（这段它没翻出来）',
                                    'code': 'echo'})
                    continue
                if res and res.get('text'):
                    _mark_ok(k)
                    _m('translate_engine', 'ok', sub=k)
                    _cache_put(_cache_key(k, s, actual_tgt), res)
                    if res['text'] not in seen:
                        seen.add(res['text'])
                        results.append(res)
                else:
                    _m('translate_engine', 'empty', sub=k)
                    skipped.append({'engine': k, 'reason': '无结果', 'code': 'empty'})
            for f in pending:
                k = futs[f]
                _mark_fail(k)
                _m('translate_engine', 'timeout', sub=k)
                skipped.append({'engine': k, 'reason': '超过 %.1fs 未返回' % overall,
                                'code': 'timeout', 'secs': round(overall, 1)})
        finally:
            # 不等慢线程：wait=False，别让 shutdown 把整体时间又拖回 20 秒
            executor.shutdown(wait=False)

    # 第 278 项：给每条结果补一个稳定的引擎 id。界面按「界面语言」翻引擎名，
    # 不再直接显示后端这几句中文标签（英文/西语/印地界面里原来是中文）。
    for _r in results:
        if isinstance(_r, dict) and not _r.get('engine_id'):
            _r['engine_id'] = _engine_id_of(_r.get('engine'))
        if isinstance(_r, dict) and not _r.get('extra_code'):
            _r['extra_code'] = EXTRA_CODES.get(str(_r.get('extra') or ''), '')

    _m('translate', 'ok' if results else 'empty', ms=(time.time() - _t0) * 1000.0)
    # 第 341 项：代理端口连不上时 netproxy 已经自动改直连了，这里把原因带回界面，
    # 用户看到「全都无结果」时至少知道是梯子没跑，而不是以为软件坏了。
    try:
        bypass = netproxy.last_bypass()
    except Exception:
        bypass = ''
    return {
        'ok': len(results) > 0,
        'query': s,
        'src_lang': actual_src,
        'tgt_lang': actual_tgt,
        'engines': chosen,
        'proxy': proxy_url(),
        'proxy_bypass': bypass,
        'word_like': word_like,
        'skipped': skipped,
        'results': results,
    }


def engine_health():
    """给体检用：每家引擎当前冷却多久、连续失败几次、是否配了自带密钥。

    只读内存里的 _FAILS，不发请求、不读用户文本。
    """
    out = []
    for e in ENGINES:
        left = cooling(e['id'])
        with _FAILS_LOCK:
            n = int((_FAILS.get(e['id']) or (0, 0.0))[0])
        out.append({'id': e['id'], 'name': e['name'],
                    'cool_s': int(left), 'fail_n': n,
                    'byok': e['id'] in BYOK_IDS,
                    'ready': bool(byok_ready().get(e['id'])) if e['id'] in BYOK_IDS else True})
    return out
