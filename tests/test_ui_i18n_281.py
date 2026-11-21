# -*- coding: utf-8 -*-
"""第 281 项回归：剪贴板面板 / 标题栏副标题 / 截图预览不许再出现硬编码中文。

用户原话：「剪切板里面还是一堆中文啊，你好好排查下最近加的几个插件哪些露了翻译」
       「图标旁边的这个也是硬编码」（标题栏 Memex 右边「文字与正文搜索」）

这里只钉「用户点得到、看得见」的那几条，不搞通篇 CJK 扫描——注释、日志、语言名
（简体中文 / 日本語）都是合理的，扫了就全是假阳性。
"""
import io
import os
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


class NoHardcodedUICn(unittest.TestCase):
    def test_clipboard_modal_has_no_bare_chinese(self):
        src = _read(os.path.join('web', 'src', 'ui', 'ClipboardModal.tsx'))
        banned = [
            "'复制失败'", "'已加入收藏 ★'", "'已取消收藏'",
            "'确定清空未收藏的剪贴板历史记录吗？（收藏内容将保留）'",
            "'已清空未收藏历史'", 'title="关闭 (Esc)"',
            'placeholder="搜索剪贴板历史..."', "'★ 收藏'", "'文本'",
            '>加载中...<', '>暂无匹配的剪贴板历史<',
            "'取消收藏' : '加入收藏'", 'title="删除此项"',
        ]
        for b in banned:
            assert b not in src, 'ClipboardModal 仍是硬编码中文: ' + b
        for key in ('clip_search_ph', 'clip_all', 'clip_empty', 'clip_pin_add',
                    'clip_del_title', 'clip_close_title', 'clip_clear_confirm'):
            assert key in src, 'ClipboardModal 没接上 ' + key

    def test_titlebar_subtitle_uses_i18n(self):
        src = _read(os.path.join('web', 'src', 'ui', 'titlebar.tsx'))
        assert '文字与正文搜索' not in src.replace('// ', '// '), '标题栏副标题还是写死的中文'
        assert "t('titlebar_sub')" in src

    def test_snipmodal_uses_i18n(self):
        src = _read(os.path.join('web', 'src', 'ui', 'snipmodal.tsx'))
        assert 'title="关闭 (Esc)"' not in src
        assert 'alt="截图预览"' not in src

    def test_app_snip_preview_uses_i18n(self):
        src = _read(os.path.join('web', 'src', 'App.tsx'))
        for b in ("setToast('识字完成')", "setToast('识字失败')", '>截图预览<',
                  "title=\"删除图片 (Delete)\""):
            assert b not in src, 'App.tsx 仍是硬编码中文: ' + b

    def test_mini_template_clip_panel_uses_i18n(self):
        src = _read('brain.py')
        # 只看模板本体（MINI_TIPS 词典里当然有中文，那是译文表）
        tpl_start = src.index('MINI_TPL = ')
        tpl_end = src.index('PAGE_TPL', tpl_start)
        tpl = src[tpl_start:tpl_end]
        # 面板里的中文标签不许再写死
        for b in ('<span>剪贴板历史</span>', '>★ 收藏</button>', "'暂无收藏的剪贴板内容'",
                  "'暂无剪贴板历史'", "'已复制到剪贴板 ✓'", 'title="清空全部"',
                  "'📁 文件'", '>文本</button>'):
            assert b not in tpl, '快搜窗剪贴板面板仍是硬编码中文: ' + b
        # 占位符必须真的被 _subs 替换掉，否则用户会看到 __TIP_CB_TITLE__
        for key in ('cb_title', 'cb_all', 'cb_fav', 'cb_text', 'cb_image', 'cb_file',
                    'cb_clear', 'cb_empty', 'cb_load_fail', 'cb_pin_add', 'cb_pin_del',
                    'cb_badge_file', 'cb_badge_text', 'cb_copied', 'cb_copy_fail'):
            assert ("'%s':" % key) in src, 'MINI_TIPS 缺 ' + key
            assert ('__TIP_%s__' % key.upper()) in src, '模板里没用到 __TIP_%s__' % key.upper()

    def test_mini_template_placeholders_all_substitutable(self):
        """模板里出现的每个 __TIP_*__ / __U_*__ 都必须在 _subs 里有对应规则，否则会漏到界面上。"""
        import re
        src = _read('brain.py')
        tpl_start = src.index('MINI_TPL = ')
        tpl_end = src.index('PAGE_TPL', tpl_start) if 'PAGE_TPL' in src[tpl_start:] else len(src)
        tpl = src[tpl_start:tpl_end]
        used = set(re.findall(r'__TIP_[A-Z0-9_]+__', tpl))
        subs = src[src.index('def _subs'):]
        missing = [u for u in sorted(used) if (".replace('%s'" % u) not in subs]
        assert not missing, '这些占位符没有替换规则，会原样显示: %s' % missing

        # __U_<key>__ 走 MINI_UI 的循环替换：键必须在表里，否则页面会留占位符
        import brain
        used_u = set(re.findall(r'__U_([a-z0-9_]+)__', tpl))
        for lang, d in brain.MINI_UI.items():
            missing = sorted(k for k in used_u if k not in d)
            self.assertEqual([], missing, 'MINI_UI[%s] 缺键: %s' % (lang, missing))
            assert '__U_%s__' % '__DUMMY__' not in tpl

    def test_mini_ui_values_are_real_text(self):
        """MINI_UI 的值必须是真文案：混进 __U_ 占位符就说明被模板批量替换误伤过。"""
        import brain
        for lang, d in brain.MINI_UI.items():
            for k, v in d.items():
                self.assertNotIn('__U_', str(v), '%s / %s' % (lang, k))
                self.assertNotIn('__TIP_', str(v), '%s / %s' % (lang, k))
                self.assertNotEqual('__U_%s__' % k, str(v), '%s / %s 自引用' % (lang, k))

    def test_mini_template_fully_renders_each_language(self):
        """按 _subs 的同一套规则在内存里渲一遍：五种语言都不许剩 __U_ 占位符。"""
        import brain, re
        for lang in ('zh', 'en', 'es', 'pt', 'hi'):
            key = {'zh': 'zh-CN'}.get(lang, lang)
            html = brain.MINI_TPL
            for k, v in brain.MINI_UI[key].items():
                html = html.replace('__U_%s__' % k, str(v))
            left = sorted(set(re.findall(r'__U_[a-z0-9_]+__', html)))
            self.assertEqual([], left, lang)

    def test_mini_l_not_polluted_by_placeholders(self):
        """MINI_L 是 json.dumps 注入 JS 的，不经过 _subs —— 里面混进 __U_/__TIP_ 占位符会原样显示。"""
        import brain
        for lang, d in brain.MINI_L.items():
            for k, v in d.items():
                self.assertNotIn('__U_', str(v), '%s / %s' % (lang, k))
                self.assertNotIn('__TIP_', str(v), '%s / %s' % (lang, k))

    def test_mini_ui_has_five_languages(self):
        import brain
        self.assertEqual({'zh-CN', 'en', 'es', 'pt', 'hi'}, set(brain.MINI_UI))
        zh = brain.MINI_UI['zh-CN']
        for lang in ('en', 'es', 'pt', 'hi'):
            d = brain.MINI_UI[lang]
            self.assertEqual(set(zh), set(d), lang)
            # 「关闭 / 复制图片」这类短句必须真翻，不能照抄中文
            for k in ('copy_img_btn', 'trans_doing', 'cb_confirm', 'about_ok'):
                self.assertNotEqual(zh[k], d[k], '%s 的 %s 还是中文' % (lang, k))
            for k, v in d.items():
                self.assertTrue(str(v).strip(), '%s / %s 是空的' % (lang, k))


class ClipboardLangTest(unittest.TestCase):
    def test_relative_time_follows_lang(self):
        import clipboard_mgr
        now = time.time() - 300
        assert '分钟前' in clipboard_mgr.format_relative_time(now, 'zh-CN')
        assert clipboard_mgr.format_relative_time(now, 'en').endswith('min ago')
        assert 'मिनट' in clipboard_mgr.format_relative_time(now, 'hi')
        assert 'Hace' in clipboard_mgr.format_relative_time(now, 'es')
        assert 'Há' in clipboard_mgr.format_relative_time(now, 'pt')
        # 未知语言回落中文，不能让界面出现英文
        assert '分钟前' in clipboard_mgr.format_relative_time(now, 'fr')

    def test_detail_follows_lang(self):
        import clipboard_mgr
        assert clipboard_mgr.format_detail('text', 'abcd', '4 字符', 'en') == '4 chars'
        assert clipboard_mgr.format_detail('text', 'abcd', '4 字符', 'zh-CN') == '4 字符'
        assert clipboard_mgr.format_detail('file', '["a","b","c"]', '3 个项目', 'en') == '3 items'
        assert clipboard_mgr.format_detail('file', '["a"]', '1 个项目', 'hi').startswith('1 ')
        # 图片尺寸是语言无关的，别翻
        assert clipboard_mgr.format_detail('image', 'h', '1920×1080', 'hi') == '1920×1080'

    def test_clip_copy_image_uses_existing_api(self):
        """用户点图片条目报「module 'snip_ocr' has no attribute 'write_clipboard_image'」——
        那个函数从来不存在，必须调 put_image_to_clipboard。"""
        import io as _io
        import snip_ocr
        import clipboard_mgr
        self.assertTrue(hasattr(snip_ocr, 'put_image_to_clipboard'))
        self.assertFalse(hasattr(snip_ocr, 'write_clipboard_image'))
        src = _io.open(clipboard_mgr.__file__, encoding='utf-8').read()
        self.assertNotIn('write_clipboard_image', src)
        self.assertIn('put_image_to_clipboard', src)

    def test_clip_copy_messages_follow_lang(self):
        """复制成功/失败提示也是后端拼的，必须跟界面语言。"""
        import clipboard_mgr as c
        for lang in ('zh-CN', 'en', 'es', 'pt', 'hi'):
            msg = c._clip_msg('copied_img', lang)
            self.assertTrue(msg and msg != 'copied_img', lang)
        self.assertEqual('已复制图片到剪贴板', c._clip_msg('copied_img', 'zh-CN'))
        self.assertEqual('Copied image to clipboard', c._clip_msg('copied_img', 'en'))
        self.assertIn('boom', c._clip_msg('copy_fail', 'es', e='boom'))
        self.assertIn('3', c._clip_msg('copied_files', 'pt', n=3))

    def test_image_row_title_follows_lang(self):
        """用户截图：「图片 [1668×655]」在印地语界面照样是中文。preview 也要跟语言。"""
        import clipboard_mgr
        # 老数据：preview 里带中文前缀，靠 detail 里的尺寸重拼
        assert clipboard_mgr.format_preview('image', '图片 [1668×655]', '1668×655', 'hi') \
            .startswith('चित्र [1668×655')
        assert clipboard_mgr.format_preview('image', '[1668×655]', '1668×655', 'en') == 'Image [1668×655]'
        assert clipboard_mgr.format_preview('image', '图片 [1668×655]', '1668×655', 'zh-CN') == '图片 [1668×655]'
        # 文本条目原样返回，别把正文改了
        assert clipboard_mgr.format_preview('text', 'hello world', '11 字符', 'hi') == 'hello world'


if __name__ == '__main__':
    unittest.main()
