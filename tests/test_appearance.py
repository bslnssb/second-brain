# -*- coding: utf-8 -*-
"""主题配色 / 全局壁纸（第 191 项）。

两块都要能在以后**整体切成 Pro**，所以先把「行为边界」钉住：
- 配色 id 的白名单必须和前端 `web/src/lib/palette.ts` 一字不差，
  不然用户在界面点一套、后端把它当非法值 400，保存就悄悄失败；
- 壁纸只认位图 + 只认配置里那条路径（前端拿不到「随便读文件」的能力）；
- 文件被删/格式不对时返回 `on: false`，**绝不抛异常**（异常会变成界面白屏）。

隔离方式沿用 test_settings：把 brain._DATA_DIR 指到临时目录。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain  # noqa: E402

PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a5a7'
    '0000000049454e44ae426082')


class PaletteTest(unittest.TestCase):
    def test_ids_are_unique_and_complete(self):
        self.assertEqual(15, len(brain.PALETTE_IDS))
        self.assertEqual(len(set(brain.PALETTE_IDS)), len(brain.PALETTE_IDS))
        for k in ('auto', 'light', 'dark', 'paper', 'contrast', 'cyber'):
            self.assertIn(k, brain.PALETTE_IDS)

    def test_ids_match_frontend(self):
        """前后端两份列表必须一模一样：前端多一个，用户点了就 400。"""
        ts = (ROOT / 'web/src/lib/palette.ts').read_text(encoding='utf-8')
        block = ts[ts.index('export const PALETTES'):ts.index('export const PALETTE_IDS')]
        ids = re.findall(r"id: '([a-z]+)'", block)
        self.assertEqual(list(brain.PALETTE_IDS), ids)


class WallStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sb-test-wall-')
        self.dir = Path(self.tmp.name)
        self._old = list(brain._DATA_DIR)
        self._old_maint = os.environ.get('SB_NO_MAINT')
        os.environ['SB_NO_MAINT'] = '1'
        brain._DATA_DIR[:] = [self.dir]

    def tearDown(self):
        brain._DATA_DIR[:] = self._old
        if self._old_maint is None:
            os.environ.pop('SB_NO_MAINT', None)
        else:
            os.environ['SB_NO_MAINT'] = self._old_maint
        self.tmp.cleanup()

    def _png(self, name='w.png'):
        p = self.dir / name
        p.write_bytes(PNG)
        return str(p)

    def test_default_state(self):
        st = brain._wall_state()
        self.assertFalse(st['on'])
        self.assertEqual('', st['name'])
        self.assertEqual(15, st['opacity'])      # 默认别太显眼
        self.assertEqual(0, st['blur'])

    def test_set_tune_clear(self):
        p = self._png()
        st = brain._wall_apply({'op': 'set', 'path': p})
        self.assertTrue(st['on'])
        self.assertEqual('w.png', st['name'])
        self.assertGreater(st['v'], 0)

        st = brain._wall_apply({'op': 'tune', 'opacity': 42, 'blur': 9})
        self.assertEqual(42, st['opacity'])
        self.assertEqual(9, st['blur'])

        st = brain._wall_apply({'op': 'clear'})
        self.assertFalse(st['on'])

    def test_tune_clamps(self):
        brain._wall_apply({'op': 'tune', 'opacity': 999, 'blur': -5})
        st = brain._wall_state()
        self.assertEqual(100, st['opacity'])
        self.assertEqual(0, st['blur'])

    def test_reject_non_image(self):
        b = self.dir / 'x.txt'
        b.write_text('not an image', encoding='utf-8')
        st = brain._wall_apply({'op': 'set', 'path': str(b)})
        self.assertFalse(st['on'])
        self.assertEqual('wall_bad_ext', st['err'])

    def test_missing_file_is_not_fatal(self):
        st = brain._wall_apply({'op': 'set', 'path': str(self.dir / 'nope.png')})
        self.assertFalse(st['on'])
        self.assertEqual('wall_missing', st['err'])
        # 配置里记着但文件被删：状态要自愈成 off，别一直报错
        brain._settings_update({'wall': str(self.dir / 'gone.png')})
        st = brain._wall_state()
        self.assertFalse(st['on'])
        self.assertEqual('wall_missing', st['err'])

    def test_image_bytes_and_mime(self):
        p = self._png()
        brain._wall_apply({'op': 'set', 'path': p})
        b, ct = brain._wall_image()
        self.assertEqual('image/png', ct)
        self.assertEqual(PNG, b)

    def test_image_missing_returns_json(self):
        brain._settings_update({'wall': str(self.dir / 'gone.png')})
        b, ct = brain._wall_image()
        self.assertIn('json', ct)
        self.assertIn(b'wall_missing', b)

    def test_bad_op_reports(self):
        st = brain._wall_apply({'op': 'nonsense'})
        self.assertEqual('wall_bad_op', st['err'])


if __name__ == '__main__':
    unittest.main()
