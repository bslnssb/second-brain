# -*- coding: utf-8 -*-
"""第 346 项回归锁：默认引擎只开前四个 + 朗读合成能出 wav。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import translator  # noqa: E402


class DefaultEngines(unittest.TestCase):
    def test_default_is_first_four(self):
        self.assertEqual(translator.DEFAULT_ENGINES, ['google', 'mymemory', 'tencent', 'youdao'])
        # 前四个必须是 ENGINES 里排在最前的那几个，否则设置页的高亮和实际请求会错位
        self.assertEqual(translator.ENGINE_IDS[:4], translator.DEFAULT_ENGINES)

    def test_normalize_empty_returns_default_four(self):
        self.assertEqual(translator.normalize_engines([]), translator.DEFAULT_ENGINES)
        self.assertEqual(translator.normalize_engines(None), translator.DEFAULT_ENGINES)

    def test_normalize_keeps_user_order(self):
        got = translator.normalize_engines(['youdao', 'google'])
        self.assertEqual(got, ['youdao', 'google'])

    def test_active_engines_without_settings_is_default_four(self):
        # 全新安装（settings.json 里没有 trans_engines）→ 只开前四个
        self.assertEqual(translator.active_engines({}), translator.DEFAULT_ENGINES)

    def test_active_engines_respects_saved_off(self):
        # 用户在第 346 项之后存过一次勾选（ver = ENGINE_VER）→ 原样照办，不再自动补
        saved = ['google', 'mymemory', 'tencent', 'youdao', 'baidu']
        got = translator.active_engines(
            {'trans_engines': saved, 'trans_engines_ver': translator.ENGINE_VER})
        self.assertEqual(got, saved)

    def test_old_config_still_gets_new_engines(self):
        # 第 295 项的承诺不能被第 346 项打掉：老配置（没有 ver）仍会补上后来新增的引擎
        got = translator.active_engines({'trans_engines': ['google', 'youdao']})
        self.assertEqual(got[:2], ['google', 'youdao'])
        self.assertIn('tencent', got)

    def test_migrate_turns_untouched_legacy_into_four(self):
        # 第 346 项（B）：五家免密钥都在 = 从没关过 → 一次性收敛成前四个，并写标记
        legacy = ["google", "youdao", "baidu", "mymemory", "tencent",
                  "deepseek", "deepl", "baidu_api", "google_api", "openai"]
        st, changed = translator.migrate_346({"trans_engines": legacy})
        self.assertTrue(changed)
        self.assertEqual(st["trans_engines"], translator.DEFAULT_ENGINES)
        self.assertTrue(st["trans_engines_346"])
        # 再读一次：标记在，就不再动（用户之后怎么勾都算数）
        st2, changed2 = translator.migrate_346(st)
        self.assertFalse(changed2)

    def test_migrate_keeps_a_deliberate_narrow_selection(self):
        # 只留了 3 家 = 用户明确表达过偏好，不许被升级改回去
        st, changed = translator.migrate_346(
            {"trans_engines": ["google", "mymemory", "tencent"]})
        self.assertTrue(changed)          # 只是打标记
        self.assertEqual(st["trans_engines"], ["google", "mymemory", "tencent"])


class Speech(unittest.TestCase):
    def test_wav_bytes_returns_riff(self):
        import tts
        try:
            data, voice = tts.wav_bytes('hello', 'en')
        except Exception as e:      # 没装 SAPI 语音的机器（CI / 精简系统）跳过
            self.skipTest('no speech voice: %r' % (e,))
        self.assertTrue(data.startswith(b'RIFF'), 'wav 头不对')
        self.assertGreater(len(data), 4000, '音频太短，八成是空合成')

    def test_long_text_is_truncated(self):
        import tts
        self.assertLessEqual(tts.MAX_CHARS, 500)
        # 缓存键用截断后的文本：两条超长文本不该各合成一遍
        import inspect
        src = inspect.getsource(tts.wav_bytes)
        self.assertIn('MAX_CHARS', src)


if __name__ == '__main__':
    unittest.main()
