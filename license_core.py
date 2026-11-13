"""离线授权码 —— 纯标准库实现，零第三方依赖。

Ed25519 按 RFC 8032 实现，只用 hashlib。签名/验证一次约 5~20ms，
只在启动和激活时跑，不进热路径。

为什么不用 cryptography / PyNaCl：
  1. 打包体积：两个 spec 都要加 hiddenimports，还会带进 OpenSSL 相关二进制；
  2. 本功能只需要 sign / verify 两个函数，120 行纯 Python 就够；
  3. 纯 Python 没有平台相关的二进制，跨机器不会因为 DLL 缺失而崩。

授权码格式（一行字符串，可直接粘贴）：
    SB1.<payload>.<sig>
      payload = base64url(zlib(JSON 原文))
      sig     = base64url(64 字节 Ed25519 签名)
      签名对象是 **JSON 原文**（解压后、解析前），所以改一个字节就验不过

JSON 字段：
    v  格式版本（当前 1）
    t  类型：pro / trial / team
    n  署名（显示用，可空）
    u  标识（可空）。**以 hw: 开头时表示绑定机器**：`hw:<16 位 hex>`
       由 license_core.hwid() 算出，激活时本机指纹必须一致，否则拒签。
    e  到期日 YYYY-MM-DD，空字符串 = 永久
    i  签发时间 YYYYMMDDTHHMM
    f  功能位列表，["*"] 表示全部

安全边界（写在明处，免得以后误以为它防得住谁）：
  * 防的是「随便发个码就能用」，不是「有人逆向后 patch 掉校验」。
    离线授权的本质是提高门槛，不是绝对防护 —— 真正想破解的人直接改二进制，
    任何客户端授权方案都拦不住，所以这里不做过度的反调试/混淆。
  * 时钟回拨：记录见过的最大时间，回拨超过 24 小时会标记警告（不阻断，
    避免误伤系统时间同步、双系统、虚拟机时区）。
"""

import base64
import binascii
import json
import time
import zlib

# ---------------------------------------------------------------- Ed25519

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512(b):
    import hashlib

    return hashlib.sha512(b).digest()


def _x_recover(y):
    """由 y 求 x（取「偶数」那个根）；不在曲线上返回 None。"""
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - xx) % _P:
        return None
    if x & 1:
        x = _P - x
    return x


_BY = 4 * pow(5, _P - 2, _P) % _P
_BX = _x_recover(_BY)
_B = (_BX, _BY, 1, _BX * _BY % _P)


def _add(P, Q):
    """扭曲爱德华兹曲线点加（扩展坐标，a = -1）。"""
    x1, y1, z1, t1 = P
    x2, y2, z2, t2 = Q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _encode(P):
    x, y, z, _t = P
    zi = pow(z, _P - 2, _P)
    x = x * zi % _P
    y = y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, 'little')


def _decode(raw):
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        return None
    x = _x_recover(y)
    if x is None:
        return None
    if (x & 1) != sign:
        x = _P - x
    return (x, y, 1, x * y % _P)


def _clamp(h32):
    a = int.from_bytes(h32, 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a


def public_key(seed):
    """32 字节私钥种子 -> 32 字节公钥。"""
    if len(seed) != 32:
        raise ValueError('seed must be 32 bytes')
    return _encode(_mul(_clamp(_sha512(seed)[:32]), _B))


def sign(seed, msg):
    """32 字节私钥种子 + 消息 -> 64 字节签名。"""
    if len(seed) != 32:
        raise ValueError('seed must be 32 bytes')
    h = _sha512(seed)
    a = _clamp(h[:32])
    A = _encode(_mul(a, _B))
    r = int.from_bytes(_sha512(h[32:] + msg), 'little') % _L
    R = _encode(_mul(r, _B))
    k = int.from_bytes(_sha512(R + A + msg), 'little') % _L
    return R + ((r + k * a) % _L).to_bytes(32, 'little')


def verify(pub, msg, sig):
    """验签。任何格式错误一律返回 False，不抛异常。"""
    if len(pub) != 32 or len(sig) != 64:
        return False
    A = _decode(pub)
    R = _decode(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], 'little')
    if s >= _L:
        return False
    k = int.from_bytes(_sha512(sig[:32] + pub + msg), 'little') % _L
    return _encode(_mul(s, _B)) == _encode(_add(R, _mul(k, A)))


# ---------------------------------------------------------------- 授权码

PREFIX = 'SB1'
EDITION_FREE = 'free'
EDITION_PRO = 'pro'

FEATURE_EXPORT = 'export'
FEATURE_OCR_BATCH = 'ocr_batch'
FEATURE_MULTI_LIB = 'multi_lib'
FEATURE_TEMPLATES = 'templates'
ALL_FEATURES = (FEATURE_EXPORT, FEATURE_OCR_BATCH, FEATURE_MULTI_LIB, FEATURE_TEMPLATES)


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _b64d(text):
    pad = '=' * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def make_code(seed, kind='pro', name='', ident='', exp='', features='*'):
    """签发授权码（只有持有私钥的人能调，私钥不进仓库也不进 exe）。"""
    if isinstance(features, str):
        features = ['*'] if features == '*' else [f for f in features.split(',') if f]
    payload = {
        'v': 1,
        't': kind,
        'n': name,
        'u': ident,
        'e': exp,
        'i': time.strftime('%Y%m%dT%H%M'),
        'f': list(features or []),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    sig = sign(seed, raw)
    return '%s.%s.%s' % (PREFIX, _b64e(zlib.compress(raw, 9)), _b64e(sig))


def _result(valid, reason, payload=None, edition=EDITION_FREE):
    payload = payload or {}
    feats = payload.get('f') or []
    if '*' in feats:
        feats = list(ALL_FEATURES)
    return {
        'valid': bool(valid),
        'reason': reason,
        'edition': edition if valid else EDITION_FREE,
        'kind': payload.get('t', '') if valid else '',
        'name': payload.get('n', '') if valid else '',
        'ident': payload.get('u', '') if valid else '',
        'exp': payload.get('e', '') if valid else '',
        'issued': payload.get('i', '') if valid else '',
        'features': [f for f in feats if f in ALL_FEATURES] if valid else [],
    }


def parse_code(code, pubkey_hex, today=None, hwid=None):
    """校验授权码，返回结构化结果（永不抛异常）。

    today: 'YYYY-MM-DD'，测试用；默认取本机日期。
    """
    text = (code or '').strip().replace('\n', '').replace('\r', '').replace(' ', '')
    if not text:
        return _result(False, 'empty')
    parts = text.split('.')
    if len(parts) != 3 or parts[0] != PREFIX:
        return _result(False, 'format')
    try:
        raw = zlib.decompress(_b64d(parts[1]))
        sig = _b64d(parts[2])
    except (binascii.Error, zlib.error, ValueError):
        return _result(False, 'format')

    try:
        pub = bytes.fromhex(pubkey_hex)
    except ValueError:
        return _result(False, 'server')  # 公钥配错，属于程序 bug 不是用户问题

    if not verify(pub, raw, sig):
        return _result(False, 'signature')

    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _result(False, 'format')
    if not isinstance(payload, dict) or payload.get('v') != 1:
        return _result(False, 'version')

    exp = str(payload.get('e') or '')
    if exp:
        day = today or time.strftime('%Y-%m-%d')
        if day > exp:
            return _result(False, 'expired', payload)

    # 机器绑定（第 170 项）：u 以 hw: 开头就必须和本机指纹一致。
    # 老码的 u 是自由文本（邮箱/后四位），不受影响 —— 向后兼容，不会把已发的码废掉。
    ident = str(payload.get('u') or '')
    if ident.startswith('hw:'):
        want = ident[3:].strip().lower()
        got = str(hwid or '').strip().lower()
        if not got or got != want:
            return _result(False, 'hwid', payload)

    kind = payload.get('t') or 'pro'
    if kind not in ('pro', 'trial', 'team'):
        return _result(False, 'format')
    return _result(True, 'ok', payload, edition=EDITION_PRO)


def hwid():
    """本机指纹：CPU 型号 + 系统盘卷序列号 + 主板标识，SHA-256 前 16 位（第 170 项）。

    只求「同一台机器稳定、换机就变」，不求防伪造 —— 本地校验本来就挡不住改代码，
    这一步的目的是让「一个码发给十个人」失效，而不是防逆向。

    任何一项取不到就跳过；全取不到时返回空串，此时 `hw:` 绑定一律拒绝
    （宁可让作者手动补签，也不静默放过）。
    """
    import hashlib
    parts = []
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           'HARDWARE' + chr(92) + 'DESCRIPTION' + chr(92) + 'System'
                           + chr(92) + 'CentralProcessor' + chr(92) + '0')
        parts.append(str(winreg.QueryValueEx(k, 'ProcessorNameString')[0]).strip())
        winreg.CloseKey(k)
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes
        vol = wintypes.DWORD(0)
        ctypes.windll.kernel32.GetVolumeInformationW.restype = wintypes.BOOL
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p('C:' + chr(92)), None, 0, ctypes.byref(vol),
            None, None, None, 0)
        if ok:
            parts.append('%08X' % vol.value)
    except Exception:
        pass
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           'SYSTEM' + chr(92) + 'CurrentControlSet' + chr(92)
                           + 'Control' + chr(92) + 'SystemInformation')
        parts.append(str(winreg.QueryValueEx(k, 'ComputerHardwareId')[0]))
        winreg.CloseKey(k)
    except Exception:
        pass
    if not parts:
        return ''
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:16]


def bind_ident(hwid_hex=None):
    """把本机指纹包成 payload 的 u 字段值，签码时传给 ident。"""
    return 'hw:' + (hwid_hex or hwid())


def days_left(exp, today=None):
    """到期日还剩几天；永久返回 None；已过期返回负数。"""
    if not exp:
        return None
    try:
        y, m, d = (int(x) for x in exp.split('-'))
        end = time.mktime((y, m, d, 23, 59, 59, 0, 0, -1))
    except (ValueError, TypeError):
        return None
    now = time.mktime(time.strptime(today, '%Y-%m-%d')) if today else time.time()
    return int((end - now) // 86400)


def mask_code(code):
    """日志/界面里显示时只留头尾，避免整串被截图抄走。"""
    text = (code or '').strip()
    if len(text) <= 24:
        return text
    return text[:14] + '…' + text[-8:]
