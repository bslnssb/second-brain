#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memex · 音频时长探测（纯标准库，不依赖 mutagen / ffmpeg / ffprobe）

为什么需要它（第 243 项）
------------------------
用户要区分「用户自己存的 .mp3」和「软件自带的 .mp3」。文件层面区分不了，
但**时长**能区分：软件自带的绝大多数是提示音、铃声、切片音效（1~15 秒），
而用户存的歌是完整曲目（1~5 分钟）。所以规则是：

    时长 < 20 秒的音频不入文件名索引。

为什么自己写而不用现成库
------------------------
· mutagen 是第三方依赖，打进 exe 会增加体积与供应链面，且很多发行版没装；
· ffprobe/ffmpeg 不是 Windows 自带，用户机器上大概率没有（实测本机有，
  但不能要求每个用户都装）；
· 这里只做「粗判时长」，精确到秒就够用，纯标准库足够。

支持格式：MP3（帧头 + Xing/VBRI）、WAV（wave 模块）、FLAC（STREAMINFO）、
M4A/MP4（moov/mvhd）。其它格式（OGG / WMA / APE）返回 None —— 调用方
遇到 None 一律**保留**（宁可漏排不可错排）。

调用方约定：`audio_seconds(path)` 返回 float 秒数；解析不出来返回 None。
"""

import os
import struct
import wave

__all__ = ['audio_seconds', 'probe_exts', 'PROBE_EXTS']


# ---------------------------------------------------------------------------
# MPEG 音频常量
# ---------------------------------------------------------------------------

# bitrate 表（kbps），索引 = bitrate_index。0 表示无效。
_BITRATE = {
    ('v1', 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    ('v1', 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    ('v1', 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    ('v2', 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    ('v2', 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    ('v2', 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}

_SR = {
    3: (44100, 48000, 32000),   # MPEG1
    2: (22050, 24000, 16000),   # MPEG2
    0: (11025, 12000, 8000),    # MPEG2.5
}

# Xing/VBRI 头相对帧首的偏移（不含帧头 4 字节）
_XING_OFF = {
    (3, 1): 21, (3, 2): 36,     # MPEG1: mono / stereo
    (2, 1): 13, (2, 2): 21,     # MPEG2 / 2.5
    (0, 1): 13, (0, 2): 21,
}

MAX_PROBE_FRAMES = 12     # VBR 估算时最多看几帧（每帧只读 4 字节头）
MAX_SCAN_BYTES = 4 * 1024 * 1024   # MP4 找 moov 时最多回扫多少字节


# ---------------------------------------------------------------------------
# MP3
# ---------------------------------------------------------------------------

def _id3v2_size(f):
    """跳过 ID3v2 标签，返回 (标签总字节数, 音频起始偏移)。

    ID3v2 头 10 字节：'ID3' + ver(2) + flags(1) + size(4，syncsafe)。
    flags 第 5 位是「带扩展头」，再第 6 位是 footer —— 都要算进偏移。
    """
    head = f.read(10)
    if len(head) < 10 or head[:3] != b'ID3':
        return 0, 0
    flags = head[5]
    size = 0
    for b in head[6:10]:
        size = (size << 7) | (b & 0x7F)
    total = 10 + size
    if flags & 0x10:      # 扩展头（syncsafe 长度）
        ext = f.read(4)
        if len(ext) == 4:
            ext_size = 0
            for b in ext:
                ext_size = (ext_size << 7) | (b & 0x7F)
            total += ext_size
    if flags & 0x20:      # footer
        total += 10
    return total, total


def _parse_frame_header(h):
    """解析一个 MPEG 音频帧头。

    返回 dict 或 None：
      {'ver': 1|2, 'layer': 1..3, 'bitrate': kbps, 'sr': Hz,
       'samples': 每帧样本数, 'len': 帧长字节, 'pad': 0|1}
    """
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return None
    ver_bits = (h[1] >> 3) & 0x03      # 3=MPEG1, 2=MPEG2, 0=MPEG2.5, 1=保留
    layer_bits = (h[1] >> 1) & 0x03    # 1=Layer3, 2=Layer2, 3=Layer1, 0=保留
    if ver_bits == 1 or layer_bits == 0:
        return None
    ver = 1 if ver_bits == 3 else 2
    layer = 4 - layer_bits              # layer_bits 1→3, 2→2, 3→1
    br_index = (h[2] >> 4) & 0x0F
    sr_index = (h[2] >> 2) & 0x03
    pad = (h[2] >> 1) & 0x01
    if br_index in (0, 15) or sr_index == 3:
        return None
    bitrate = _BITRATE[('v1' if ver == 1 else 'v2', layer)][br_index]
    if not bitrate:
        return None
    sr = _SR[ver_bits][sr_index]
    if layer == 1:
        samples = 384
        flen = (12 * bitrate * 1000 // sr + pad) * 4
    else:
        samples = 1152 if (layer == 3 and ver == 1) else (1152 if layer == 2 else 576)
        coef = 144 if ver == 1 else 72
        flen = coef * bitrate * 1000 // sr + pad
    if flen <= 4:
        return None
    return {'ver': ver, 'layer': layer, 'bitrate': bitrate, 'sr': sr,
            'samples': samples, 'len': flen, 'pad': pad,
            'ver_bits': ver_bits, 'mono': ((h[3] >> 6) & 0x03) == 3}


def _xing_frames(f, frame_start, fr):
    """读 Xing/Info/VBRI 头，返回总帧数（None 表示没有）。"""
    off = _XING_OFF.get((fr['ver_bits'], 1 if fr['mono'] else 2))
    if off is None:
        return None
    f.seek(frame_start + 4 + off)
    tag = f.read(4)
    if tag in (b'Xing', b'Info'):
        flags = struct.unpack('>I', f.read(4))[0]
        if flags & 0x01:
            return struct.unpack('>I', f.read(4))[0]
        return None
    # VBRI 通常在帧首 + 32
    f.seek(frame_start + 32)
    if f.read(4) == b'VBRI':
        f.read(6)
        return struct.unpack('>I', f.read(4))[0]
    return None


def _mp3_seconds(path):
    try:
        fsize = os.path.getsize(path)
    except OSError:
        return None
    if fsize < 128:
        return None
    try:
        with open(path, 'rb') as f:
            tag, offset = _id3v2_size(f)
            if offset > fsize:
                return None
            # 找第一帧：允许前面有少量垃圾字节
            f.seek(offset)
            buf = f.read(65536)
            fr = None
            frame_start = None
            limit = min(len(buf) - 4, 8192)
            i = 0
            while i <= limit:
                if buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0:
                    cand = _parse_frame_header(buf[i:i + 4])
                    if cand:
                        # 校验下一帧，避免把假同步当成帧头
                        nxt = i + cand['len']
                        if nxt + 4 <= len(buf):
                            if _parse_frame_header(buf[nxt:nxt + 4]):
                                fr = cand
                                frame_start = offset + i
                                break
                        elif nxt < fsize:
                            fr = cand
                            frame_start = offset + i
                            break
                i += 1
            if not fr or frame_start is None:
                return None

            # ① Xing/Info/VBRI：直接给总帧数，最准
            try:
                frames = _xing_frames(f, frame_start, fr)
            except Exception:
                frames = None
            if frames:
                return frames * fr['samples'] / float(fr['sr'])

            # ② CBR：用文件大小算
            audio_bytes = fsize - frame_start
            if fr['pad'] == 0 and fr['layer'] != 1:
                dur = audio_bytes * 8.0 / (fr['bitrate'] * 1000.0)
                if 0 < dur < 24 * 3600:
                    return dur

            # ③ VBR 无 Xing：抽样几帧取平均码率
            f.seek(frame_start)
            total = 0
            n = 0
            for _ in range(MAX_PROBE_FRAMES):
                h = f.read(4)
                p = _parse_frame_header(h)
                if not p:
                    break
                f.seek(p['len'] - 4, os.SEEK_CUR)
                total += p['bitrate']
                n += 1
            if n >= 2:
                avg = total / float(n)
                dur = (fsize - frame_start) * 8.0 / (avg * 1000.0)
                if 0 < dur < 24 * 3600:
                    return dur
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# WAV / FLAC / MP4(M4A)
# ---------------------------------------------------------------------------

def _wav_seconds(path):
    try:
        with wave.open(path, 'rb') as w:
            rate = w.getframerate()
            frames = w.getnframes()
            if rate > 0 and frames > 0:
                return frames / float(rate)
    except Exception:
        return None
    return None


def _flac_seconds(path):
    try:
        with open(path, 'rb') as f:
            if f.read(4) != b'fLaC':
                return None
            while True:
                hdr = f.read(4)
                if len(hdr) < 4:
                    return None
                last = hdr[0] & 0x80
                btype = hdr[0] & 0x7F
                blen = int.from_bytes(hdr[1:4], 'big')
                if btype == 0:      # STREAMINFO
                    data = f.read(blen)
                    if len(data) < 18:
                        return None
                    v = int.from_bytes(data[10:18], 'big')
                    rate = (v >> 44) & 0xFFFFF
                    total = v & ((1 << 36) - 1)
                    if rate > 0 and total > 0:
                        return total / float(rate)
                    return None
                if last:
                    return None
                f.seek(blen, os.SEEK_CUR)
    except Exception:
        return None
    return None


def _find_box(f, size, want):
    """在文件里找顶层 box `want`，返回它的**box 起始偏移**（找不到 None）。

    注意返回的是 box 头位置（不是数据区起点）—— 调用方要靠它再读一遍
    box 头拿总长度。早先版本误返回 data_at，导致 moov/mvhd 定位整体错位、
    M4A 时长永远解不出来。
    """
    pos = 0
    while pos + 8 <= size:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return None
        bsize = int.from_bytes(hdr[0:4], 'big')
        btype = hdr[4:8]
        if bsize == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return None
            bsize = int.from_bytes(ext, 'big')
        elif bsize == 0:
            bsize = size - pos
        if bsize < 8:
            return None
        if btype == want:
            return pos
        pos += bsize
    return None


def _mp4_seconds(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    try:
        with open(path, 'rb') as f:
            moov = _find_box(f, size, b'moov')
            if moov is None:
                # moov 常在文件尾（非流式 mp4）：从尾部回扫
                start = max(0, size - MAX_SCAN_BYTES)
                f.seek(start)
                tail = f.read()
                idx = tail.rfind(b'moov')
                if idx < 0:
                    return None
                moov = start + idx - 4
                if moov < 0:
                    return None
            f.seek(moov)
            hdr = f.read(8)
            if len(hdr) < 8:
                return None
            bsize = int.from_bytes(hdr[0:4], 'big')
            end = moov + (bsize if bsize > 8 else size - moov)
            # moov 内找 mvhd
            pos = moov + 8
            while pos + 8 <= end:
                f.seek(pos)
                h = f.read(8)
                if len(h) < 8:
                    break
                bs = int.from_bytes(h[0:4], 'big')
                bt = h[4:8]
                if bs == 1:
                    ext = f.read(8)
                    bs = int.from_bytes(ext, 'big')
                    data_at = pos + 16
                else:
                    data_at = pos + 8
                if bs < 8:
                    break
                if bt == b'mvhd':
                    ver = f.read(1)[0]
                    f.read(3)
                    if ver == 1:
                        f.read(16)
                        timescale = struct.unpack('>I', f.read(4))[0]
                        duration = struct.unpack('>Q', f.read(8))[0]
                    else:
                        f.read(8)
                        timescale = struct.unpack('>I', f.read(4))[0]
                        duration = struct.unpack('>I', f.read(4))[0]
                    if timescale > 0 and duration > 0:
                        return duration / float(timescale)
                    return None
                pos += bs
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

# 支持探测的扩展名（小写，含点）。只列**能可靠解析**的格式。
probe_exts = ('.mp3', '.wav', '.flac', '.m4a')
PROBE_EXTS = frozenset(probe_exts)

# 每种格式的探测函数
_FUNCS = {
    '.mp3': _mp3_seconds,
    '.wav': _wav_seconds,
    '.flac': _flac_seconds,
    '.m4a': _mp4_seconds,
}


def audio_seconds(path):
    """返回音频时长（秒，float）；解析不出来返回 None。

    调用方一律把 None 当「保留」处理 —— 宁可漏排不可错排。
    """
    try:
        ext = os.path.splitext(str(path))[1].lower()
    except Exception:
        return None
    fn = _FUNCS.get(ext)
    if fn is None:
        return None
    try:
        return fn(str(path))
    except Exception:
        return None


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        print(p, audio_seconds(p))
