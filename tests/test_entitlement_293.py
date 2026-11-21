# -*- coding: utf-8 -*-
"""第 293 项回归：Free / Pro 功能位的切法必须和定调一致。

要守住的三条：
  1. 免费状态下 export 锁、commercial 锁；
  2. pro / team 码：export 开、commercial 开（**不用签发时手写 f 列表**）；
  3. 批量 OCR 队列**不在门控里**（源码里不能出现 license_feature('ocr_batch')），
     索引 / 搜索 / 入库 OCR 也不许上锁。
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import license_core  # noqa: E402


class Features293(unittest.TestCase):
    def test_free_plan(self):
        self.assertEqual(license_core.plan_of(''), 'free')
        self.assertEqual(license_core.plan_of('unknown'), 'free')
        self.assertEqual(license_core._features_of({}, False), [])

    def test_pro_code_features_keep_signed_only(self):
        # features 只反映签发内容；商用位由 plan 推导，不偷偷加进来
        feats = license_core._features_of({'t': 'pro', 'f': ['export']}, True)
        self.assertEqual(feats, ['export'])
        self.assertEqual(license_core.plan_of('pro'), 'pro')

    def test_team_star_expands_except_commercial(self):
        feats = license_core._features_of({'t': 'team', 'f': ['*']}, True)
        self.assertIn('export', feats)
        self.assertIn('net_index', feats)
        self.assertNotIn('commercial', feats)
        self.assertEqual(license_core.plan_of('team'), 'team')

    def test_trial_is_pro_plan_but_not_commercial(self):
        self.assertEqual(license_core.plan_of('trial'), 'pro')
        self.assertNotIn('commercial',
                         license_core._features_of({'t': 'trial', 'f': ['*']}, True))

    def test_all_features_cover_new_bits(self):
        for name in ('export', 'multi_lib', 'net_index', 'commercial'):
            self.assertIn(name, license_core.ALL_FEATURES)

    def test_ocr_batch_not_gated_in_source(self):
        """批量 OCR 队列必须免费：源码里不许出现 license_feature('ocr_batch')。"""
        src = io.open(os.path.join(ROOT, 'brain.py'), encoding='utf-8').read()
        self.assertNotIn("license_feature('ocr_batch')", src)
        self.assertNotIn('license_feature("ocr_batch")', src)
        # 索引 / 搜索 / 入库 OCR 也不许挂门控
        for bad in ("license_feature('index')", "license_feature('search')",
                    "license_feature('ocr')"):
            self.assertNotIn(bad, src)

    def test_commercial_gate_uses_plan(self):
        src = io.open(os.path.join(ROOT, 'brain.py'), encoding='utf-8').read()
        self.assertIn("if name == 'commercial':", src)
        self.assertIn("return str(st.get('plan') or 'free') in ('pro', 'team')", src)


class FeatureGate293(unittest.TestCase):
    """真调 brain.license_feature，用假 state 覆盖三种授权状态。"""

    def _gate(self, state):
        import brain
        brain._LIC['loaded'] = True
        brain._LIC['state'] = state
        return brain.license_feature

    def _state(self, **kw):
        base = {'valid': False, 'reason': 'none', 'edition': 'free', 'kind': '',
                'name': '', 'ident': '', 'exp': '', 'issued': '', 'features': [],
                'plan': 'free', 'days_left': None, 'code_masked': '',
                'clock_warn': False, 'activated': ''}
        base.update(kw)
        return base

    def test_free_blocks_export_and_commercial(self):
        f = self._gate(self._state())
        self.assertFalse(f('export'))
        self.assertFalse(f('commercial'))
        self.assertFalse(f('net_index'))

    def test_pro_allows_export_and_commercial(self):
        f = self._gate(self._state(valid=True, edition='pro', kind='pro',
                                   plan='pro', features=['export', 'commercial']))
        self.assertTrue(f('export'))
        self.assertTrue(f('commercial'))
        self.assertFalse(f('net_index'), '没这条位就不该开')

    def test_team_allows_net_index(self):
        f = self._gate(self._state(valid=True, edition='pro', kind='team',
                                   plan='team',
                                   features=['export', 'commercial', 'net_index']))
        self.assertTrue(f('net_index'))
        self.assertTrue(f('commercial'))

    def test_expired_pro_falls_back_to_free(self):
        f = self._gate(self._state(valid=False, reason='expired'))
        self.assertFalse(f('export'))
        self.assertFalse(f('commercial'))


if __name__ == '__main__':
    unittest.main()
