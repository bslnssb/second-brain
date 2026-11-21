# -*- coding: utf-8 -*-
"""第 361 项：截图翻译（原文/译文可切换、译文可复制）。

需求：截屏取字之后能直接翻，译文能复制，也能切回没翻译之前的原文。
实现要点：
  * 只换 #sbText 里的字符串 —— `复制文字` 读的就是 textarea，所以译文天然可复制；
  * 译文留在 snipDstText、原文留在 snipSrcText，两个 tab 互相切；
  * 后端只加一个 `snip_translate()` + `POST /api/snip/translate`，翻译仍走 translator
    （用户勾选的引擎），取字仍在本机 RapidOCR。
"""
import unittest
from unittest import mock

import brain
import translator


class SnipTranslateI18nTest(unittest.TestCase):
    KEYS = ('snip_trans', 'snip_src', 'snip_dst', 'snip_trans_doing',
            'snip_trans_none', 'snip_trans_hint')

    def test_all_five_langs_have_keys(self):
        for lang in ('zh-CN', 'en', 'es', 'pt', 'hi'):
            ui = brain.MINI_UI[lang]
            for k in self.KEYS:
                self.assertIn(k, ui, '%s 缺 %s' % (lang, k))
                self.assertTrue(str(ui[k]).strip(), '%s 的 %s 是空的' % (lang, k))

    def test_placeholders_in_template(self):
        for k in self.KEYS:
            self.assertIn('__U_%s__' % k, brain.MINI_TPL, '模板里没有 __U_%s__' % k)


class SnipTranslateTemplateTest(unittest.TestCase):
    def test_tabs_and_button(self):
        self.assertIn('id="sbViews"', brain.MINI_TPL)
        self.assertIn('id="sbBtnTrans"', brain.MINI_TPL)
        self.assertIn("onclick=\"translateSnip(event)\"", brain.MINI_TPL)
        self.assertIn("snipShow('src', event)", brain.MINI_TPL)
        self.assertIn("snipShow('dst', event)", brain.MINI_TPL)

    def test_copy_reads_textarea(self):
        """复制按钮必须拿 textarea 当前值，才能既复制原文也复制译文。"""
        i = brain.MINI_TPL.find('function copySnipText(')
        seg = brain.MINI_TPL[i:i + 300]
        self.assertIn("getElementById('sbText').value", seg)

    def test_translate_calls_new_endpoint(self):
        i = brain.MINI_TPL.find('function translateSnip(')
        seg = brain.MINI_TPL[i:i + 900]
        self.assertIn("/api/snip/translate", seg)
        self.assertIn('__U_snip_trans_doing__', seg)

    def test_view_switch_keeps_both_texts(self):
        i = brain.MINI_TPL.find('function snipSetView(')
        seg = brain.MINI_TPL[i:i + 600]
        self.assertIn('snipDstText', seg)
        self.assertIn('snipSrcText', seg)

    def test_shape_reset_on_new_snip(self):
        """每次重新截图必须回到原文态，别把上一张的译文留着。"""
        i = brain.MINI_TPL.find('function renderSnip(')
        seg = brain.MINI_TPL[i:i + 900]
        self.assertIn("snipDstText = ''", seg)
        self.assertIn("classList.remove('on')", seg)


class SnipTranslateBackendTest(unittest.TestCase):
    def test_picks_first_result(self):
        fake = {'ok': True, 'src_lang': 'en', 'tgt_lang': 'zh-CN',
                'results': [{'engine': 'google', 'text': '季度营收增长 18%'},
                            {'engine': 'mymemory', 'text': '季度营收增长18%'}]}
        with mock.patch.object(translator, 'translate_text', return_value=fake):
            r = brain.snip_translate('Quarterly revenue grew 18%')
        self.assertTrue(r['ok'])
        self.assertEqual(r['text'], '季度营收增长 18%')
        self.assertEqual(r['engine'], 'google')
        self.assertEqual(r['src'], 'Quarterly revenue grew 18%')
        self.assertEqual(r['tgt_lang'], 'zh-CN')

    def test_no_result_is_not_ok(self):
        fake = {'ok': False, 'src_lang': 'en', 'tgt_lang': 'zh-CN', 'results': []}
        with mock.patch.object(translator, 'translate_text', return_value=fake):
            r = brain.snip_translate('hello')
        self.assertFalse(r['ok'])
        self.assertEqual(r['why'], 'noresult')

    def test_engine_error_degrades(self):
        with mock.patch.object(translator, 'translate_text', side_effect=RuntimeError('boom')):
            r = brain.snip_translate('hello')
        self.assertFalse(r['ok'])
        self.assertIn('boom', r['why'])

    def test_empty_text(self):
        with mock.patch.object(brain, 'snip_ocr', create=True) as ocr:
            ocr.get_snip_result.return_value = {'text': '  '}
            r = brain.snip_translate('')
        self.assertFalse(r['ok'])
        self.assertEqual(r['why'], 'empty')

    def test_route_registered(self):
        import pathlib
        src = pathlib.Path(brain.__file__).read_text(encoding='utf-8')
        self.assertIn("== '/api/snip/translate'", src)
        self.assertIn('snip_translate(_txt, _tl)', src)


if __name__ == '__main__':
    unittest.main()
