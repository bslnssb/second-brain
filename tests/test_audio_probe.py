# -*- coding: utf-8 -*-
"""音频时长探测 + 短音频/音乐平台排除（第 243 项）的回归测试。

用户原话：
  「户存的 .mp3」和「软件自带的 .mp3」这个判断可以直接跳过 20 秒以内的 mp3，
   歌曲通常都是一两分钟的。也可以看是不是网易云和 qq 音乐酷狗音乐，
   和国外的音乐平台下的目录。

为什么以前没抓到：
  第 242 项把「用户存的 .mp3」与「软件自带的 .mp3」判为**文件层面不可区分**
  （钉钉 69.0% / 腾讯会议 66.9% / 靶场 66.5%，区分度不到 3 个百分点），
  于是那轮只做了「按目录形态排」—— 同一首软件自带的短音效在别的目录里
  照样进索引。这次换了个维度（**时长**）才真正分开。

覆盖：
  ① audio_probe 对 mp3 / wav / flac / m4a 的时长解析（用合成文件，不依赖外部素材）
  ② 短音频（<20 秒）不入索引、完整曲目照收
  ③ 音乐平台目录下的音频不入索引
  ④ 解析不出来的文件一律保留（宁可漏排不可错排）

跑法：py -3.12 -m unittest tests.test_audio_probe -v
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import audio_probe  # noqa: E402
import brain  # noqa: E402
import scan_docs  # noqa: E402


# ---------------------------------------------------------------------------
# 合成音频文件（不依赖任何外部素材）
# ---------------------------------------------------------------------------

def make_mp3(path: Path, seconds: float, bitrate: int = 128, rate: int = 44100) -> Path:
    """合成一个 CBR 的 MPEG1 Layer3 文件。

    帧头 0xFF 0xFB = MPEG1 / Layer3 / 无 CRC；
    0x90 = bitrate_index(9→128kbps) + samplerate_index(0→44100) + 无 padding。
    """
    br_index = {32: 1, 40: 2, 48: 3, 56: 4, 64: 5, 80: 6, 96: 7, 112: 8,
                128: 9, 160: 10, 192: 11, 224: 12, 256: 13, 320: 14}.get(bitrate)
    sr_index = {44100: 0, 48000: 1, 32000: 2}[rate]
    assert br_index is not None, bitrate
    hdr = bytes([0xFF, 0xFB, (br_index << 4) | (sr_index << 2), 0x00])
    flen = 144 * bitrate * 1000 // rate
    frames = max(2, int(seconds * rate / 1152))
    with open(path, 'wb') as f:
        for _ in range(frames):
            f.write(hdr + b'\x00' * (flen - 4))
    return path


def make_wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b'\x00\x00' * int(rate * seconds))
    return path


def make_flac(path: Path, seconds: float, rate: int = 44100) -> Path:
    total = int(rate * seconds)
    si = bytearray(34)
    packed = ((rate & 0xFFFFF) << 44) | (1 << 41) | (15 << 36) | total
    si[10:18] = packed.to_bytes(8, 'big')
    with open(path, 'wb') as f:
        f.write(b'fLaC')
        f.write(bytes([0x80]) + (34).to_bytes(3, 'big'))   # 最后一个块，类型 0
        f.write(bytes(si))
    return path


def make_m4a(path: Path, seconds: float, rate: int = 44100) -> Path:
    dur = int(rate * seconds)
    payload = bytes([0]) + b'\x00\x00\x00' + struct.pack('>II', 0, 0) \
        + struct.pack('>I', rate) + struct.pack('>I', dur)
    mvhd = struct.pack('>I', len(payload) + 8) + b'mvhd' + payload
    moov = struct.pack('>I', len(mvhd) + 8) + b'moov' + mvhd
    with open(path, 'wb') as f:
        f.write(struct.pack('>I', 20) + b'ftyp' + b'M4A ' + b'\x00' * 8)
        f.write(moov)
    return path


class TestAudioProbe(unittest.TestCase):
    """audio_probe 的时长解析本身要对。"""

    def test_mp3_duration(self):
        with tempfile.TemporaryDirectory() as td:
            p = make_mp3(Path(td) / 'a.mp3', 185)
            sec = audio_probe.audio_seconds(p)
            self.assertIsNotNone(sec)
            self.assertAlmostEqual(sec, 185, delta=1.5)

    def test_mp3_short_duration(self):
        with tempfile.TemporaryDirectory() as td:
            p = make_mp3(Path(td) / 'b.mp3', 5)
            sec = audio_probe.audio_seconds(p)
            self.assertIsNotNone(sec)
            self.assertLess(sec, 20)

    def test_wav_duration(self):
        with tempfile.TemporaryDirectory() as td:
            p = make_wav(Path(td) / 'c.wav', 3)
            self.assertAlmostEqual(audio_probe.audio_seconds(p), 3.0, delta=0.05)

    def test_flac_duration(self):
        with tempfile.TemporaryDirectory() as td:
            p = make_flac(Path(td) / 'd.flac', 42)
            self.assertAlmostEqual(audio_probe.audio_seconds(p), 42.0, delta=0.05)

    def test_m4a_duration(self):
        with tempfile.TemporaryDirectory() as td:
            p = make_m4a(Path(td) / 'e.m4a', 200)
            self.assertAlmostEqual(audio_probe.audio_seconds(p), 200.0, delta=0.05)

    def test_unknown_format_returns_none(self):
        """解不出来的格式必须返回 None（调用方据此「保留」）。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'f.ogg'
            p.write_bytes(b'OggS' + b'\x00' * 100)
            self.assertIsNone(audio_probe.audio_seconds(p))

    def test_broken_file_returns_none(self):
        """损坏的 mp3 不能抛异常，也不能算出离谱的时长。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'g.mp3'
            p.write_bytes(b'\xff\xfb\x90\x00' + b'\x00' * 10)
            sec = audio_probe.audio_seconds(p)
            self.assertTrue(sec is None or sec >= 0)


class TestShortAudioRule(unittest.TestCase):
    """brain._media_noise_file：短音频与音乐平台目录。"""

    def test_short_mp3_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            p = make_mp3(Path(td) / 'ding.mp3', 6)
            self.assertTrue(brain._media_noise_file(str(p), '.mp3'))

    def test_full_song_kept(self):
        """完整曲目（两分钟）必须留着 —— 用户自己的歌不能被误排。"""
        with tempfile.TemporaryDirectory() as td:
            p = make_mp3(Path(td) / 'song.mp3', 120)
            self.assertFalse(brain._media_noise_file(str(p), '.mp3'))

    def test_boundary_around_20s(self):
        """19 秒排掉、21 秒留下（阈值 20 的两侧都要钉住）。"""
        with tempfile.TemporaryDirectory() as td:
            short = make_mp3(Path(td) / 's.mp3', 19)
            long = make_mp3(Path(td) / 'l.mp3', 21)
            self.assertTrue(brain._media_noise_file(str(short), '.mp3'))
            self.assertFalse(brain._media_noise_file(str(long), '.mp3'))

    def test_non_audio_never_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'a.txt'
            p.write_text('x', encoding='utf-8')
            self.assertFalse(brain._media_noise_file(str(p), '.txt'))

    def test_media_app_dir_audio_excluded(self):
        """网易云/QQ音乐/酷狗目录下的音频不入索引。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            for d in ('CloudMusic', 'QQMusic', 'Kugou', 'Spotify', 'Apple Music'):
                p = base / d / 'Music' / 'x.mp3'
                p.parent.mkdir(parents=True, exist_ok=True)
                make_mp3(p, 180)
                self.assertTrue(brain._media_noise_file(str(p), '.mp3'),
                                '%s 下的音频应排掉' % d)

    def test_media_app_dir_non_audio_kept(self):
        """音乐平台目录下的其它文件（用户自己的歌单 txt/图片）不能被排。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'CloudMusic' / 'Music' / '我的歌单.txt'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('x', encoding='utf-8')
            self.assertFalse(brain._media_noise_file(str(p), '.txt'))

    def test_unparsable_audio_kept(self):
        """解不出时长的音频必须保留（宁可漏排不可错排）。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'x.mp3'
            p.write_bytes(b'not an mp3 at all')
            self.assertFalse(brain._media_noise_file(str(p), '.mp3'))


class TestRuleWiring(unittest.TestCase):
    """接线不能只写在文件里 —— 三条路径都要用同一套判据。"""

    def test_scan_docs_has_rules(self):
        self.assertTrue(hasattr(scan_docs, 'MUSIC_APP_DIR_SEGS'))
        self.assertTrue(hasattr(scan_docs, 'SOFTWARE_DIR_NAMES'))
        self.assertTrue(hasattr(scan_docs, 'USER_DATA_DIR_NAMES'))

    def test_brain_threshold_is_20(self):
        self.assertEqual(brain.NAMES_MIN_AUDIO_SECONDS, 20)

    def test_probe_module_is_pure_stdlib(self):
        """audio_probe 不得引入第三方依赖（打包体积 + 离线要求）。"""
        src = (ROOT / 'audio_probe.py').read_text(encoding='utf-8')
        for bad in ('import mutagen', 'import pydub', 'import librosa', 'import soundfile'):
            self.assertNotIn(bad, src)

    def test_wiring_in_all_three_paths(self):
        """全量扫描 / 实时监听 / 范围对账 —— 三处都要接。"""
        src = (ROOT / 'brain.py').read_text(encoding='utf-8')
        self.assertIn('_media_noise_file(full, ext)', src)      # 全量 + 实时
        self.assertIn('if _media_noise_file(p, ext):', src)      # 对账
        self.assertIn('scan_docs.has_noindex_marker(cur)', src)  # 标记文件剪枝

    def test_scope_rules_contract(self):
        """跨层契约：后端 /api/scope 的 rules 新字段与前端类型定义必须一致。

        为什么单列一条：这类问题两边各自都绿 —— 后端多返回三个字段、前端类型
        少写三个，TypeScript 不会报错（读的是别的字段），运行时才显示
        「undefined」。所以要按**字段名**比对两边。
        """
        b = (ROOT / 'brain.py').read_text(encoding='utf-8')
        api = (ROOT / 'web/src/api.ts').read_text(encoding='utf-8')
        panel = (ROOT / 'web/src/ui/panels.tsx').read_text(encoding='utf-8')
        for f in ('audio_min_seconds', 'music_apps', 'software_dirs'):
            self.assertIn(f, b, '后端 rules 缺 %s' % f)
            self.assertIn(f, api, '前端类型定义缺 %s' % f)
        self.assertIn('scope_rules_media', panel)
        # 五语言都要有这个键
        i18n = (ROOT / 'web/src/lib/i18n.ts').read_text(encoding='utf-8')
        self.assertEqual(5, i18n.count('scope_rules_media:'))


if __name__ == '__main__':
    unittest.main()
