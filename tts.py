# -*- coding: utf-8 -*-
"""第 346 项：把译文「读出来」——本机 SAPI5 合成，离线、不出网、不花钱。

设计取舍：
- **不用在线 TTS**：翻译本身已经要联网了，再让语音走网络（Edge TTS 之类）意味着
  没网就听不了、还要多等一次 RTT；本机 SAPI 立刻出声，隐私上也不外发文本。
- **不常驻进程**：一次性 COM 调用，返回 wav 字节，不做后台队列 —— 用户对多一个
  常驻进程是零容忍（第 342/343 项的教训）。
- 音色按语言挑：中文/英文/日文…能对上就用对上的；对不上退回系统默认音色
  （口音别扭也胜过点了没声），并在返回里带 voice 名，方便排查。
- 文本截断：一条 400 字的中文 ≈ 90 秒音频，再长就不像「听一下怎么读」了。
"""
import os
import tempfile
import threading

MAX_CHARS = 400          # 超出截断（含省略号），避免一条音频过长
_CACHE = {}
_CACHE_MAX = 12
_LOCK = threading.Lock()

# SAPI 语音 Language 属性是 LCID：高 10 位主语言 id（0x04=中文 0x09=英 0x11=日 …）
_LCID_PRIM = {
    'zh': 0x04, 'en': 0x09, 'ja': 0x11, 'ko': 0x12, 'fr': 0x0C, 'de': 0x07,
    'es': 0x0A, 'ru': 0x19, 'pt': 0x16, 'it': 0x10, 'ar': 0x01, 'th': 0x1E,
    'vi': 0x2A, 'hi': 0x39,
}


def _prim(lang):
    """'zh-CN' / 'en' / '' → 主语言 id；认不出返回 None（= 用默认音色）。"""
    code = (lang or '').strip().lower().replace('_', '-')
    if not code or code == 'auto':
        return None
    head = code.split('-')[0]
    return _LCID_PRIM.get(head)


def _pick_voice(sp, prim):
    """在系统已装语音里挑一个主语言对得上的；挑不到返回 None（调用方用默认）。"""
    try:
        voices = sp.GetVoices()
    except Exception:
        return None
    if prim is None:
        return None
    for i in range(voices.Count):
        try:
            v = voices.Item(i)
            raw = str(v.GetAttribute('Language') or '')
            lcid = int(raw, 16) if raw.lower().startswith('0x') else int(raw or '0')
            if (lcid >> 10) == prim:
                return v
        except Exception:
            continue
    return None


def _synth(text, lang):
    """合成一次，返回 (wav_bytes, voice_desc)；失败抛异常。"""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    tmp = None
    try:
        sp = win32com.client.Dispatch('SAPI.SpVoice')
        voice = _pick_voice(sp, _prim(lang))
        if voice is not None:
            sp.Voice = voice
        desc = ''
        try:
            desc = str(sp.Voice.GetDescription() or '')
        except Exception:
            desc = ''
        fd, tmp = tempfile.mkstemp(suffix='.wav', prefix='memex-tts-')
        os.close(fd)
        fs = win32com.client.Dispatch('SAPI.SpFileStream')
        fs.Open(tmp, 3)            # 3 = SSFMCreateForWrite
        sp.AudioOutputStream = fs
        sp.Speak(text)
        fs.Close()
        with open(tmp, 'rb') as f:
            data = f.read()
        return data, desc
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def wav_bytes(text, lang=''):
    """给一段文字，返回 (wav 字节, 音色描述)。失败时抛异常，端点自己转成 JSON 错误。

    结果按 (文本, 语言) 缓存：同一批翻译里两条结果如果文字一样（很常见），
    不会再合成第二遍。
    """
    s = (text or '').strip()
    if not s:
        raise ValueError('empty text')
    if len(s) > MAX_CHARS:
        s = s[:MAX_CHARS].rstrip() + '…'
    key = (s, (lang or '').lower())
    with _LOCK:
        hit = _CACHE.get(key)
    if hit:
        return hit
    data, desc = _synth(s, lang)
    if not data or not data.startswith(b'RIFF'):
        raise RuntimeError('sapi returned empty audio')
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (data, desc)
    return data, desc
