# -*- coding: utf-8 -*-
"""第 404 项：标签栏性能监控（/api/perf）。"""

import io
import os
import time
import unittest

import brain


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPerfSnapshot(unittest.TestCase):
    def test_snapshot_shape(self):
        a = brain.perf_snapshot()
        self.assertTrue(a['ok'])
        self.assertGreaterEqual(a['cores'], 1)
        self.assertGreater(a['mem']['proc_mb'], 0)
        self.assertGreater(a['mem']['sys_total_mb'], 0)
        self.assertGreaterEqual(a['mem']['proc_pct'], 0)
        self.assertLessEqual(a['mem']['proc_pct'], 100.0)
        self.assertGreaterEqual(a['proc']['handles'], 0)
        for k in ('proc', 'sys'):
            self.assertIn(k, a['cpu'])

    def test_cpu_is_delta_based(self):
        brain.perf_snapshot()
        time.sleep(1.0)
        b = brain.perf_snapshot()
        self.assertGreaterEqual(b['cpu']['proc'], 0.0)
        self.assertGreaterEqual(b['cpu']['sys'], 0.0)
        self.assertLessEqual(b['cpu']['sys'], 100.0)

    def test_endpoint_registered(self):
        src = io.open(os.path.join(ROOT, 'brain.py'), encoding='utf-8').read()
        self.assertIn("startswith('/api/perf')", src)

    def test_frontend_wired(self):
        app = io.open(os.path.join(ROOT, 'web/src/App.tsx'), encoding='utf-8').read()
        self.assertIn('data-act="perf"', app)
        panel = io.open(os.path.join(ROOT, 'web/src/ui/perf.tsx'), encoding='utf-8').read()
        self.assertIn('/api/perf', panel)
        self.assertIn("t('perf_title')", panel)


if __name__ == '__main__':
    unittest.main()
