"""屏幕截图与本地 OCR 提取模块（Phase 1）。

功能：
1. 触发原生屏幕截图（ShellExecute 打开 ms-screenclip:，零控制台黑框）；
2. 监视剪贴板序列号变化（GetClipboardSequenceNumber 极低开销轮询）；
3. 截图完成后自动抓取图像并调用内置 RapidOCR 进行纯离线文字识别；
4. 提供富文本提取、按行结构化解析、一键拷入剪贴板与另存为。
"""

import plat.win  # noqa: F401  ← 第 382 项：非 Windows 上给 ctypes 补 Win32 占位，
# 让模块「能 import」；功能本身仍要靠 P3~P5 的真实现，现在调用等于空转。
import ctypes
import io
import os
import threading
import time
from pathlib import Path
from PIL import Image, ImageGrab
import numpy as np

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

_LOCK = threading.Lock()
_OCR_ENGINE = None

# 当前最新一次截图的处理状态与数据
_STATE = {
    'status': 'idle',      # idle | snipping | processing | done | cancelled | error
    'error': '',
    'ts': 0,
    'has_image': False,
    'do_ocr': False,
    'img_size': (0, 0),
    'text': '',
    'lines': [],           # [{'text': str, 'score': float, 'box': [...]}, ...]
    'count': 0,
    'snip_id': 0,
    # 第 369 项：截图翻译（框选 → 本机 OCR → 出网翻译 → 原位卡片）
    'do_translate': False,
    'dst_text': '',
    'dst_engine': '',
    'rect': None,
}

_LATEST_IMG = None         # PIL.Image 实例引用
_LATEST_PNG_BYTES = None   # 缓存用于 HTTP 输出的 PNG 字节
_WATCHER_THREAD = None
_SNIP_COUNTER = 0


def get_ocr():
    """惰性加载 RapidOCR 离线模型，单例复用。"""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        # 第 380 项：统一走 ocr_engine（RapidOCR 3.x + PP-OCRv6，旧契约包装）
        from ocr_engine import RapidOCRCompat
        _OCR_ENGINE = RapidOCRCompat()
    return _OCR_ENGINE


# 第 277 项：OCR 埋点。空白页返回空文本不算异常，以前这类「识不出来」
# 完全没有记录；现在按 ok/empty/fail 分开计数，并记单张耗时。
try:
    import feature_metrics as _metrics
except Exception:                                     # pragma: no cover
    _metrics = None


def _ocr_metric(status, t0, err=''):
    if _metrics is None:
        return
    try:
        _metrics.add('ocr', status, ms=(time.time() - t0) * 1000.0, err=str(err)[:120])
    except Exception:
        pass


def ocr_image(pil_img):
    """对 PIL 图像执行离线文字识别（带耗时与结果计数）。"""
    t0 = time.time()
    try:
        out = _ocr_image(pil_img)
    except Exception as e:
        _ocr_metric('fail', t0, e)
        raise
    _ocr_metric('ok' if out.get('text') else 'empty', t0)
    return out


def _ocr_image(pil_img):
    """对 PIL 图像执行离线文字识别，返回按从上到下排布的结构化结果。"""
    if pil_img is None:
        return {'text': '', 'lines': [], 'count': 0}

    # 统一转为 RGB numpy 数组
    rgb = pil_img.convert('RGB')
    arr = np.array(rgb)

    engine = get_ocr()
    raw_res, _ = engine(arr)

    if not raw_res:
        return {'text': '', 'lines': [], 'count': 0}

    lines = []
    for item in raw_res:
        # RapidOCR 输出格式: [box_points, text, confidence]
        box, text, score = item[0], item[1], float(item[2])
        # 计算大致垂直中心位置，用于从上到下排序
        y_center = sum(pt[1] for pt in box) / len(box) if box else 0
        x_center = sum(pt[0] for pt in box) / len(box) if box else 0
        lines.append({
            'text': text.strip(),
            'score': round(score, 3),
            'box': box,
            '_y': y_center,
            '_x': x_center,
        })

    # 自然阅读顺序排序（垂直容差 12px 判定为同一行）
    lines.sort(key=lambda item: (round(item['_y'] / 14.0) * 14.0, item['_x']))

    clean_lines = [{'text': l['text'], 'score': l['score'], 'box': l['box']} for l in lines]
    full_text = '\n'.join(l['text'] for l in clean_lines if l['text'])

    return {
        'text': full_text,
        'lines': clean_lines,
        'count': len(clean_lines),
    }


def _process_image(img, on_done=None):
    """处理捕获到的图像并执行 OCR。"""
    global _STATE, _LATEST_IMG, _LATEST_PNG_BYTES, _SNIP_COUNTER
    try:
        with _LOCK:
            _SNIP_COUNTER += 1
            cur_id = _SNIP_COUNTER
            _STATE['status'] = 'processing'
            _STATE['has_image'] = True
            _STATE['do_ocr'] = True
            _STATE['img_size'] = img.size
            _STATE['ts'] = time.time()
            _STATE['snip_id'] = cur_id
            _LATEST_IMG = img

            # 缓存 PNG 字节
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            _LATEST_PNG_BYTES = buf.getvalue()

        # 执行 OCR（耗时约 0.1~0.3s）
        res = ocr_image(img)

        with _LOCK:
            _STATE['status'] = 'done'
            _STATE['text'] = res['text']
            _STATE['lines'] = res['lines']
            _STATE['count'] = res['count']
            if _LATEST_PNG_BYTES:
                import base64
                b64 = base64.b64encode(_LATEST_PNG_BYTES).decode('ascii')
                _STATE['thumb_data_url'] = f'data:image/png;base64,{b64}'

        if on_done:
            try:
                on_done(_STATE)
            except Exception:
                pass
    except Exception as e:
        with _LOCK:
            _STATE['status'] = 'error'
            _STATE['error'] = str(e)


def put_image_to_clipboard(pil_img):
    """把裁剪图像以全规格格式放入 Windows 剪贴板：
    1. CF_DIB：兼容常规 Win32 应用、Office、WPS、画图等；
    2. PNG (Registered Format)：兼容 Chrome、Edge、微信、QQ、钉钉、Discord、Telegram、Electron 输入框；
    3. CF_HDROP：兼容文件拖放粘贴（聊天框、文件管理器、桌面）。
    """
    if pil_img is None:
        return False
    try:
        import io
        import os
        import struct
        import tempfile
        import win32clipboard
        import win32con

        rgb_img = pil_img.convert('RGB')

        # 1. DIB 字节流（BMP 去掉 14 字节文件头）
        bmp_io = io.BytesIO()
        rgb_img.save(bmp_io, 'BMP')
        dib_data = bmp_io.getvalue()[14:]
        bmp_io.close()

        # 2. PNG 字节流（供现代浏览器与聊天应用输入框读取）
        png_io = io.BytesIO()
        pil_img.save(png_io, 'PNG')
        png_data = png_io.getvalue()
        png_io.close()

        # 3. 临时文件写入，供 CF_HDROP（聊天框拖放发送与文件复制）
        temp_dir = os.path.join(tempfile.gettempdir(), 'MemexSnip')
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, f'snip_{int(time.time()*1000)}.png')
        with open(temp_file, 'wb') as f:
            f.write(png_data)

        # DROPFILES 结构体: 20 字节 (pFiles=20, pt=(0,0), fNC=0, fWide=1)
        header = struct.pack('<IIIII', 20, 0, 0, 0, 1)
        dropfiles_data = header + (temp_file + '\0\0').encode('utf-16le')

        png_fmt = win32clipboard.RegisterClipboardFormat('PNG')

        for _ in range(15):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_DIB, dib_data)
                win32clipboard.SetClipboardData(png_fmt, png_data)
                try:
                    win32clipboard.SetClipboardData(win32con.CF_HDROP, dropfiles_data)
                except Exception:
                    pass
                win32clipboard.CloseClipboard()
                return True
            except Exception:
                time.sleep(0.03)
    except Exception:
        pass
    return False


def _same_text(a, b):
    """译文和原文是不是同一句（忽略空白/大小写）。用来识破引擎的「原样回显」。"""
    def norm(s):
        return ' '.join(str(s or '').split()).strip().lower()
    na = norm(a)
    return bool(na) and na == norm(b)


# 卡片顶栏「译成 ▾」的选项。auto = 跟随设置页里的目标语言。
TGT_CHOICES = [
    {'id': 'auto', 'name': '跟随设置'},
    {'id': 'zh-CN', 'name': '中文'},
    {'id': 'en', 'name': '英文'},
    {'id': 'ja', 'name': '日文'},
]


def _tgt_lang(tgt):
    """把卡片上选的「译成」翻成真实语言代码。

    卡片默认 auto，意思是**跟随设置页**里的 `trans_target_lang`；只有设置里也是 auto
    才真的交给引擎按界面语言猜。以前卡片死传 auto，用户在设置里选的目标语言白选了。
    """
    v = str(tgt or 'auto')
    if v and v != 'auto':
        return v
    try:
        import brain
        st = brain._settings_read() or {}
        return str(st.get('trans_target_lang') or 'auto')
    except Exception:
        return 'auto'


def _pick_translation(src_text, tgt='auto'):
    """跑一遍用户勾选的翻译引擎，取第一条非空译文。

    返回 (译文, 引擎名, 引擎 id, 失败原因)。失败原因别丢：卡片上一句「没返回译文」
    等于什么都没说，用户既不知道是网络还是这家不支持整句，也不知道该换哪家。
    """
    try:
        import translator
        res = translator.translate_text(src_text, src_lang='auto', tgt_lang=_tgt_lang(tgt))
    except Exception as e:
        return '', '', '', '翻译失败：%s' % (str(e)[:60],)
    for r in (res.get('results') or []):
        t = (r.get('text') or '').strip() if isinstance(r, dict) else ''
        # 只回原文的不算译文：谷歌/腾讯偶尔「echo」回一模一样的英文，
        # 卡片上就会显示一段「英文译文」，用户以为翻译坏了
        if t and not _same_text(t, src_text):
            return t, (r.get('engine') or ''), (r.get('engine_id') or ''), ''
    # 一条译文都没拿到：把各家的原因拼起来（最多两家，说清主因就够了）
    whys = []
    for s in (res.get('skipped') or []):
        if not isinstance(s, dict):
            continue
        nm = s.get('engine') or s.get('engine_id') or ''
        why = _SKIP_TEXT.get(str(s.get('code') or ''), '') or (s.get('reason') or '')
        if why:
            whys.append('%s %s' % (nm, why) if nm else why)
    why = '；'.join(whys[:2])
    return '', '', '', why or '这一次没翻出来，换一家引擎再试'


_SKIP_TEXT = {
    'dict_only': '这家只查词语，整句请换 Google / 腾讯 / MyMemory',
    'offline': '本机连不上（网络 / 代理）',
    'cooling': '刚被限流，过一会儿再试',
    'echo': '这家没翻出来（只回了原文）',
    'timeout': '超时没返回',
    'no_key': '没填密钥',
    'empty': '没返回译文',
}


def _translate_one(src_text, engine_id, tgt='auto'):
    """只用指定引擎翻一次，给卡片上的「引擎 ▾」下拉用。返回 (译文, 引擎名, 失败原因)。"""
    try:
        import translator
        res = translator.translate_text(src_text, src_lang='auto', tgt_lang=_tgt_lang(tgt),
                                        engines=[engine_id])
    except Exception as e:
        return '', '', '翻译失败：%s' % (str(e)[:60],)
    echo = False
    for r in (res.get('results') or []):
        if isinstance(r, dict) and (r.get('text') or '').strip():
            t = r['text'].strip()
            if _same_text(t, src_text):
                echo = True       # 只是把原文抄回来了，不算翻出来
                continue
            return t, (r.get('engine') or ''), ''
    nm = {e['id']: e['name'] for e in translator.ENGINES}.get(engine_id, engine_id)
    if echo:
        return '', '', '%s：%s' % (nm, _SKIP_TEXT['echo'])
    for s in (res.get('skipped') or []):
        if isinstance(s, dict) and (s.get('engine') or '') in (engine_id, ''):
            why = _SKIP_TEXT.get(str(s.get('code') or ''), '') or (s.get('reason') or '没返回译文')
            return '', '', '%s：%s' % (nm, why)
    # 词典类引擎（有道/百度/金山）遇到整句常常什么都不回，别把「只用于词语」说成「没返回译文」
    if engine_id in getattr(translator, '_DICT_ONLY', ()):
        return '', '', '%s：%s' % (nm, _SKIP_TEXT['dict_only'])
    return '', '', '%s：没返回译文' % (nm,)


def _engine_choices():
    """卡片下拉里能选的引擎：用户勾选的排前面，自带密钥没填的不列（选了也是白等）。"""
    try:
        import translator
        ready = translator.byok_ready()
        st = {}
        try:
            import brain
            st = brain._settings_read() or {}
        except Exception:
            st = {}
        try:
            act = list(translator.active_engines(st) or [])
        except Exception:
            act = list(translator.DEFAULT_ENGINES)
        out = []
        # 有道/百度/金山是词典接口，整句直接跳过 —— 列在卡片里只会让人白点一次
        dict_only = tuple(getattr(translator, '_DICT_ONLY', ()))
        for e in translator.ENGINES:
            eid = e['id']
            if eid in dict_only:
                continue
            if eid in translator.BYOK_IDS and not ready.get(eid):
                continue
            out.append({'id': eid, 'name': e['name'], 'note': e.get('note', ''), 'ready': True})
        order = list(act) + [e['id'] for e in translator.ENGINES if e['id'] not in act]
        out.sort(key=lambda x: order.index(x['id']) if x['id'] in order else 999)
        return out
    except Exception:
        return []


def _run_translate_snip(cropped, on_done=None):
    """框选完 → 立刻把「翻译中…」卡片贴到选区 → OCR → 翻译 → 回填。

    卡片是纯 WinForms 原生窗口（snip_card），不开 WebView，用完即销毁。
    """
    global _LATEST_IMG, _LATEST_PNG_BYTES, _SNIP_COUNTER
    rect = None
    try:
        import snip_overlay
        rect = snip_overlay.LAST_RECT
    except Exception:
        rect = None

    try:
        _SNIP_COUNTER += 1
        cur_id = _SNIP_COUNTER
        _LATEST_IMG = cropped.copy()
        buf = io.BytesIO()
        cropped.save(buf, format='PNG')
        _LATEST_PNG_BYTES = buf.getvalue()
    except Exception:
        cur_id = 0

    with _LOCK:
        _STATE['status'] = 'processing'
        _STATE['has_image'] = True
        _STATE['do_ocr'] = True
        _STATE['do_translate'] = True
        _STATE['img_size'] = cropped.size
        _STATE['rect'] = list(rect) if rect else None
        _STATE['ts'] = time.time()
        _STATE['snip_id'] = cur_id
        if _LATEST_PNG_BYTES:
            import base64
            b64 = base64.b64encode(_LATEST_PNG_BYTES).decode('ascii')
            _STATE['thumb_data_url'] = f'data:image/png;base64,{b64}'

    card = None
    try:
        # 第 370 项：先让 dev_override 顶一下（有 App\dev_override\snip_card.py 就优先它）
        try:
            import dev_override
            dev_override.install('snip_card')
        except Exception:
            pass
        import snip_card
    except Exception:
        snip_card = None

    state = {'src': '', 'tgt': 'auto'}

    def on_switch(engine_id, tgt='auto', text=None):
        """卡片上换了引擎 / 换了目标语言 / 改完原文按了「重翻」：只拿这一家重翻，回填同一张卡。

        text 非空表示用户把原文改过了（OCR 错字），这一条必须拿它当源头，
        否则改了半天还是按旧的错字重翻。
        """
        def run_switch():
            src = (text or '').strip() or state.get('src') or ''
            if not src:
                try:
                    card.set_error('还没识别出文字，稍等一下再换')
                except Exception:
                    pass
                return
            state['src'] = src
            state['tgt'] = tgt or 'auto'
            try:
                card.set_loading('翻译中…')
            except Exception:
                pass
            dst, eng, why = _translate_one(src, engine_id, tgt)
            try:
                if dst:
                    card.set_result(dst, eng, None, engine_id)
                else:
                    card.set_error(why or '这个引擎没返回译文')
            except Exception:
                pass
        threading.Thread(target=run_switch, daemon=True, name='memex-snip-engine').start()

    if snip_card is not None:
        try:
            # 先给「识别文字中…」：OCR 本地要几百毫秒，一上来就写「翻译中…」是骗人的
            card = snip_card.show_card(rect, loading_text='识别文字中…',
                                       engines=_engine_choices(), on_switch=on_switch,
                                       tgt_choices=TGT_CHOICES, tgt='auto')
        except Exception:
            card = None

    def work():
        src, dst, eng, err, eid, why = '', '', '', '', '', ''
        try:
            res = ocr_image(cropped)
            src = (res.get('text') or '').strip()
        except Exception as e:
            err = '识别失败：%s' % (str(e)[:80],)
        if src and not err:
            state['src'] = src
            try:
                if card is not None:
                    card.set_loading('翻译中…')      # OCR 完了，进入真正的翻译阶段
            except Exception:
                pass
            try:
                dst, eng, eid, why = _pick_translation(src, state.get('tgt') or 'auto')
            except Exception as e:
                err = '翻译失败：%s' % (str(e)[:80],)
        elif not src and not err:
            err = '没识别到文字'

        with _LOCK:
            _STATE['status'] = 'done'
            _STATE['text'] = src
            _STATE['dst_text'] = dst
            _STATE['dst_engine'] = eng
            _STATE['lines'] = []
            _STATE['count'] = len([l for l in src.splitlines() if l.strip()])
            _STATE['ts'] = time.time()

        try:
            if card is not None:
                # 原文先塞进去：翻译失败时「原文」按钮也得能看，不然出错就只能关掉重截
                if src:
                    card.set_source(src)
                if dst:
                    card.set_result(dst, eng, None, eid)
                else:
                    card.set_error(err or why or '这一次没翻出来，换一家引擎再试')
        except Exception:
            pass
        if on_done:
            try:
                on_done(dict(_STATE))
            except Exception:
                pass

    threading.Thread(target=work, daemon=True, name='memex-snip-translate').start()


def trigger_snip(on_done=None, do_ocr=False, do_translate=False, timeout=35.0):
    """触发截图。
    do_ocr=False: 纯截图（极速无延迟，松手直接将图片拷入剪贴板，不调用 OCR）；
    do_ocr=True: 截图并执行 OCR 文字提取；
    do_translate=True: 截图 → 本机 OCR → 出网翻译 → 在选区原位弹一张译文卡片
                       （**不写剪贴板**，免得翻译顺手把一张图塞进剪贴板历史）。
    """
    global _STATE, _WATCHER_THREAD, _SNIP_COUNTER
    if do_translate:
        do_ocr = True
    with _LOCK:
        _SNIP_COUNTER += 1
        _STATE['status'] = 'snipping'
        _STATE['error'] = ''
        _STATE['do_ocr'] = bool(do_ocr)
        _STATE['do_translate'] = bool(do_translate)
        _STATE['dst_text'] = ''
        _STATE['dst_engine'] = ''
        _STATE['rect'] = None
        _STATE['snip_id'] = _SNIP_COUNTER
        _STATE['thumb_data_url'] = ''
        _STATE['text'] = ''
        _STATE['lines'] = []
        _STATE['count'] = 0
        _STATE['ts'] = time.time()

    def do_capture():
        cropped = None
        # 极短等待让出屏幕并等待 DWM 刷新
        time.sleep(0.03)

        try:
            import snip_overlay
            if snip_overlay.HAS_WINFORMS:
                cropped = snip_overlay.capture_with_overlay()
        except Exception as e:
            cropped = None

        if cropped is not None:
            global _LATEST_IMG, _LATEST_PNG_BYTES, _SNIP_COUNTER

            if do_translate:
                # 第 369 项：翻译模式。位置取自遮罩窗记下的选区，卡片贴在原地；
                # 剪贴板一动不动 —— 用户要的是译文，不是又一张图。
                _run_translate_snip(cropped, on_done)
                return

            # 用户框选确认：先将图片存入系统剪贴板（供随时 Ctrl+V 粘贴）
            try:
                put_image_to_clipboard(cropped)
            except Exception:
                pass

            if do_ocr:
                # 仅在明确请求 OCR 时才启动模型识别，彻底消除日常截图的等待与卡顿
                _process_image(cropped, on_done=on_done)
                return

            # 纯截图模式：极速完成，直接触发回调
            global _LATEST_IMG, _LATEST_PNG_BYTES, _SNIP_COUNTER
            try:
                _SNIP_COUNTER += 1
                _LATEST_IMG = cropped.copy()
                buf = io.BytesIO()
                cropped.save(buf, format='PNG')
                _LATEST_PNG_BYTES = buf.getvalue()
            except Exception:
                pass
            with _LOCK:
                _STATE['status'] = 'done'
                _STATE['has_image'] = True
                _STATE['img_size'] = cropped.size
                _STATE['do_ocr'] = False
                _STATE['text'] = ''
                _STATE['lines'] = []
                _STATE['count'] = 0
                _STATE['snip_id'] = _SNIP_COUNTER
                _STATE['ts'] = time.time()
                if _LATEST_PNG_BYTES:
                    import base64
                    b64 = base64.b64encode(_LATEST_PNG_BYTES).decode('ascii')
                    _STATE['thumb_data_url'] = f'data:image/png;base64,{b64}'
                else:
                    _STATE['thumb_data_url'] = ''
            if on_done:
                try:
                    on_done(dict(_STATE))
                except Exception:
                    pass
            return

        # 若用户按 Esc / 右键取消，或未框选退出
        with _LOCK:
            _STATE['status'] = 'cancelled'
        if on_done:
            try:
                on_done(dict(_STATE))
            except Exception:
                pass

    _WATCHER_THREAD = threading.Thread(target=do_capture, daemon=True, name='memex-snip-thread')
    _WATCHER_THREAD.start()
    return _SNIP_COUNTER


def get_snip_result():
    """获取最新截图识别结果快照。"""
    with _LOCK:
        return dict(_STATE)


def get_snip_thumb_bytes():
    """获取最新截图的图像数据（PNG 格式）。"""
    with _LOCK:
        return _LATEST_PNG_BYTES


def copy_snip_text(custom_text=None):
    """把识别文本拷贝进 Windows 剪贴板。"""
    text_to_copy = custom_text if custom_text is not None else _STATE.get('text', '')
    if not text_to_copy:
        return False

    import win32clipboard
    import win32con

    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, str(text_to_copy))
            win32clipboard.CloseClipboard()
            return True
        except Exception:
            time.sleep(0.05)
    return False


def save_snip_image(target_path=None):
    """将最新截图保存到指定路径（若为空则保存到桌面）。"""
    with _LOCK:
        if _LATEST_IMG is None:
            return False, '无可用截图'
        img = _LATEST_IMG.copy()

    if not target_path:
        desktop = Path.home() / 'Desktop'
        t_str = time.strftime('%Y%m%d_%H%M%S')
        target_path = str(desktop / f'Memex_截图_{t_str}.png')

    try:
        p = Path(target_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(p), format='PNG')
        return True, str(p)
    except Exception as e:
        return False, str(e)


def test_screen_capture():
    """测试当前进程是否具备屏幕捕获权限。"""
    import ctypes
    from PIL import ImageGrab
    res = {}
    try:
        im = ImageGrab.grab(all_screens=True)
        res['imagegrab'] = {'ok': True, 'size': im.size}
    except Exception as e:
        res['imagegrab'] = {'ok': False, 'err': str(e)}

    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        hdc = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc)
        hbmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(hdc_mem, hbmp)
        ok = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)
        res['gdi'] = {'ok': bool(ok), 'w': w, 'h': h}
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteDC(hdc_mem)
        gdi32.DeleteObject(hbmp)
        user32.ReleaseDC(0, hdc)
    except Exception as e:
        res['gdi'] = {'ok': False, 'err': str(e)}
    return res


def read_clipboard_image():
    """读取当前系统剪贴板中的图像，若存在则返回 base64 数据 URL 与元数据。"""
    try:
        import base64
        cb = ImageGrab.grabclipboard()
        if cb is None:
            return {'has_image': False}
        if isinstance(cb, Image.Image):
            pil_img = cb
        elif isinstance(cb, list) and cb:
            first_path = cb[0]
            if os.path.exists(first_path) and any(str(first_path).lower().endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif')):
                pil_img = Image.open(first_path)
            else:
                return {'has_image': False}
        else:
            return {'has_image': False}

        buf = io.BytesIO()
        pil_img.save(buf, format='PNG')
        png_b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        data_url = f'data:image/png;base64,{png_b64}'
        return {
            'has_image': True,
            'data_url': data_url,
            'w': pil_img.width,
            'h': pil_img.height,
        }
    except Exception as e:
        return {'has_image': False, 'error': str(e)}


def ocr_base64_image(b64_or_data_url=None):
    """识别前端传入的 base64 / Data URL 图像文字。若未传入或传入空值/URL，优先回退到最新截图。"""
    try:
        global _LATEST_IMG
        # 1. 若未传入或传入空值，优先直接使用内存中原尺寸高清最新截图
        if not b64_or_data_url or not str(b64_or_data_url).strip():
            if _LATEST_IMG is not None:
                res = ocr_image(_LATEST_IMG)
                return {
                    'ok': True,
                    'text': res.get('text', ''),
                    'lines': res.get('lines', []),
                    'count': res.get('count', 0),
                }

        # 2. 若传入有效数据
        if b64_or_data_url and isinstance(b64_or_data_url, str):
            s = b64_or_data_url.strip()
            # 如果是 /api/snip/thumb 等本地 URL 引用，直接使用内存中原尺寸高清最新截图
            if s.startswith('/api/snip/') or s.startswith('http://127.0.0.1') or s.startswith('http://localhost'):
                if _LATEST_IMG is not None:
                    res = ocr_image(_LATEST_IMG)
                    return {
                        'ok': True,
                        'text': res.get('text', ''),
                        'lines': res.get('lines', []),
                        'count': res.get('count', 0),
                    }
            if ',' in s:
                s = s.split(',', 1)[1]
            try:
                import base64
                img_bytes = base64.b64decode(s)
                pil_img = Image.open(io.BytesIO(img_bytes))
                res = ocr_image(pil_img)
                return {
                    'ok': True,
                    'text': res.get('text', ''),
                    'lines': res.get('lines', []),
                    'count': res.get('count', 0),
                }
            except Exception:
                pass

        # 2. 如果未传入数据或 base64 解码失败，优先使用最新截图 _LATEST_IMG
        if _LATEST_IMG is not None:
            res = ocr_image(_LATEST_IMG)
            return {
                'ok': True,
                'text': res.get('text', ''),
                'lines': res.get('lines', []),
                'count': res.get('count', 0),
            }

        # 3. 兜底尝试读取系统剪贴板图像
        clip_img = read_clipboard_image()
        if clip_img.get('has_image') and clip_img.get('data_url'):
            return ocr_base64_image(clip_img.get('data_url'))

        return {'ok': False, 'text': '', 'error': 'empty image data'}
    except Exception as e:
        return {'ok': False, 'text': '', 'error': str(e)}


def prewarm():
    """后台预热 RapidOCR 模型与 WinForms 底层依赖，保证首次使用毫秒级响应。"""
    try:
        import snip_overlay
        snip_overlay.prewarm()
    except Exception:
        pass
    try:
        _ = get_ocr()
    except Exception:
        pass

