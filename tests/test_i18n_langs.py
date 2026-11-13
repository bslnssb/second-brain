# -*- coding: utf-8 -*-
"""界面多语言完整性（第 176 项）：别再有「英文占位」和「少一个 {n}」。

踩过的坑：新面板的键只加进了中文和英文，西/葡/印地的值直接抄英文占位。
键集合是齐的，preflight 的键对齐查不出来；用户切成印地语看到整页英文，
反馈「为什么只有英语和中文」。所以这里按**值**盯，而不是按键。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / 'web/src/lib/i18n.ts'

Q = r"""('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")"""
# 西语里这几个词本来就和英文同形，允许相同
SAME_OK = {'kind_audio', 'care_title'}


def dicts():
    s = I18N.read_text(encoding='utf-8')
    out = {}
    for n in ('ZH', 'EN', 'ES', 'PT', 'HI'):
        m = re.search(r'const %s\s*:\s*Dict\s*=\s*\{(.*?)\n\}' % n, s, re.S)
        d = {}
        if m:
            for mm in re.finditer(r"^  ([A-Za-z0-9_]+)\s*:\s*((?:%s\s*\n?\s*)+)" % Q,
                                  m.group(1), re.M):
                d[mm.group(1)] = ''.join(p[1:-1] for p in re.findall(Q, mm.group(2)))
        out[n] = d
    return out


class TestI18n(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.D = dicts()

    def test_file_parsed(self):
        # 解析器失效会静默返回空字典，让后面几条全部「通过」—— 这条是自保
        self.assertEqual(489, len(self.D['ZH']))
        for n in ('EN', 'ES', 'PT', 'HI'):
            self.assertEqual(len(self.D['ZH']), len(self.D[n]), n)

    def test_key_sets_match(self):
        zh = set(self.D['ZH'])
        for n in ('EN', 'ES', 'PT', 'HI'):
            self.assertEqual(set(), set(self.D[n]) ^ zh, n)

    def test_no_english_placeholder(self):
        en = self.D['EN']
        left = []
        for n in ('ES', 'PT', 'HI'):
            for k, v in self.D[n].items():
                if en.get(k) and v == en[k] and k not in SAME_OK:
                    left.append('%s.%s' % (n, k))
        self.assertEqual([], left, '这些键还是英文占位（未翻译）：%s' % left[:10])

    def test_placeholders_match_english(self):
        en = self.D['EN']
        for n in ('ES', 'PT', 'HI'):
            for k, v in self.D[n].items():
                self.assertEqual(sorted(re.findall(r'\{\w+\}', en.get(k, ''))),
                                 sorted(re.findall(r'\{\w+\}', v)), '%s.%s' % (n, k))

    def test_india_uses_devanagari(self):
        """印地语至少得是梵文天城体，不能是拉丁字母 —— 防止整块被英文覆盖。"""
        dev = sum(1 for v in self.D['HI'].values() if re.search(r'[\u0900-\u097F]', v))
        self.assertGreater(dev, 380, '印地语里只有 %d 条含天城体' % dev)


if __name__ == '__main__':
    unittest.main()
