# -*- coding: utf-8 -*-
"""第 336 项：视频缩略图的请求尺寸（详情栏/展开要更大，网格保持 256）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brain  # noqa: E402


class TestVideoThumbSize(unittest.TestCase):
    def test_grid_folder_is_256(self):
        self.assertEqual(brain._thumb_video_size(320), 256)
        self.assertEqual(brain._thumb_video_size(0), 256)

    def test_detail_big_is_640(self):
        # big=1 → 900×1200，壳里给 640
        self.assertEqual(brain._thumb_video_size(900), 640)

    def test_expand_xl_is_960(self):
        self.assertEqual(brain._thumb_video_size(1600), 960)
        self.assertEqual(brain._thumb_video_size(9999), 960)

    def test_bad_input_falls_back(self):
        self.assertEqual(brain._thumb_video_size(None), 256)
        self.assertEqual(brain._thumb_video_size('abc'), 256)


if __name__ == '__main__':
    unittest.main()
