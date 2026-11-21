# -*- coding: utf-8 -*-
"""第 388 项：顺手捞出来的两个前端白渲染 + 一个 O(n²)。

- 整本 OCR 状态 5 秒一刷、整理索引库 10 秒一刷：原来每次都 `setState` 一个新对象，
  整棵界面（含 200 行结果）跟着重渲染。现在先比签名，没变就 return。
- 列表里「往后还剩几条正文命中」原来用 `shown.slice(i).filter(...)`，200 行 = 两万次
  切片；现在 `hitLeft` 倒着数一遍（O(n)）。
"""

import io
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, '..', 'web', 'src', 'App.tsx')


def _read(p):
    with io.open(p, encoding='utf-8') as f:
        return f.read()


class TestNoUselessRerender(unittest.TestCase):
    def setUp(self):
        self.src = _read(APP)

    def test_ocr_poll_skips_when_unchanged(self):
        self.assertIn('lastOcrSig', self.src)
        self.assertRegex(self.src, r'sig === lastOcrSig\.current\) return')

    def test_compact_poll_skips_when_unchanged(self):
        self.assertRegex(self.src, r'if \(`\$\{s\.state\}\|\$\{pct\}` === last\) return')

    def test_hit_count_is_linear(self):
        self.assertIn('const hitLeft = useMemo', self.src)
        self.assertNotRegex(self.src, r"shown\.slice\(i\)\.filter")
        self.assertIn('const hitCount = hitLeft[i]', self.src)

    def test_highlight_regex_compiled_once_per_query(self):
        # 200 行 × 每行编一次正则 → 现在按查询词缓存
        self.assertIn('const HI_RE', self.src)
        self.assertIn('function hiRegex', self.src)
        self.assertIn('const parts = raw.split(re)', self.src)


if __name__ == '__main__':
    unittest.main()
