# -*- coding: utf-8 -*-
import json
import unittest
from unittest import mock

import translator


class TestTranslator(unittest.TestCase):
    def test_language_detection(self):
        self.assertTrue(translator.is_chinese('你好'))
        self.assertTrue(translator.is_chinese('Hello 世界'))
        self.assertFalse(translator.is_chinese('Hello world!'))

    def test_detect_lang_multilingual(self):
        self.assertEqual(translator.detect_lang('测试文本'), 'zh-CN')
        self.assertEqual(translator.detect_lang('こんにちは'), 'ja')
        self.assertEqual(translator.detect_lang('안녕하세요'), 'ko')
        self.assertEqual(translator.detect_lang('Привет'), 'ru')
        self.assertEqual(translator.detect_lang('Hello world'), 'en')

    def test_supported_languages_list(self):
        self.assertTrue(len(translator.SUPPORTED_LANGUAGES) >= 10)
        codes = [item[0] for item in translator.SUPPORTED_LANGUAGES]
        self.assertIn('auto', codes)
        self.assertIn('zh-CN', codes)
        self.assertIn('en', codes)
        self.assertIn('ja', codes)
        self.assertIn('ko', codes)

    def test_translate_empty(self):
        res = translator.translate_text('')
        self.assertFalse(res['ok'])

    def test_translate_online_mock(self):
        # Even without network, translate_text should return a structured dict safely
        res = translator.translate_text('hello')
        self.assertIn('query', res)
        self.assertIn('results', res)
        self.assertIsInstance(res['results'], list)


class TestEngineSelection(unittest.TestCase):
    """第 268 项：聚合翻译要能逐引擎开关，不然一次请求要打满五个引擎。"""

    def test_registry_ids_are_stable(self):
        # 第 274 项只**往后追加**三家（自带密钥），老 id 一个都没动 ——
        # 动一个老 id，用户 settings.json 里勾选过的 trans_engines 就静默失效。
        # 第 279 项同样只**往后追加**（google_api / openai），老 id 一个都没动。
        # 第 295 项再加 tencent（国内可直连的免密钥整句引擎）；它的显示位置在
        # MyMemory 之后，但 id 列表里排第三，老用户的勾选不受影响。
        self.assertEqual(
            ['google', 'mymemory', 'tencent', 'youdao', 'baidu', 'iciba',
             'deepseek', 'deepl', 'baidu_api', 'google_api', 'openai'],
            translator.ENGINE_IDS,
        )
        # 每个 id 都必须真有一个取数函数，不然设置页勾了也白勾
        for eid in translator.ENGINE_IDS:
            self.assertTrue(callable(translator._ENGINE_FETCHERS[eid]))

    def test_normalize_keeps_only_known_in_order(self):
        # 大小写归一、未知 id 丢掉、重复去掉，顺序沿用调用方给的（iciba 在前就保持在前）
        self.assertEqual(
            ['iciba', 'google'],
            translator.normalize_engines(['ICIBA', 'google', 'nope', 'iciba', 'GOOGLE']),
        )

    def test_normalize_empty_falls_back_to_default_four(self):
        # 第 346 项：全关/空 → 回到默认前四个（不再全开）。
        # 仍然是「不把翻译功能整个关死」，只是兜底清单从 11 家收成 4 家。
        self.assertEqual(translator.DEFAULT_ENGINES, translator.normalize_engines([]))
        self.assertEqual(translator.DEFAULT_ENGINES, translator.normalize_engines(None))
        self.assertEqual(translator.DEFAULT_ENGINES, translator.normalize_engines(['nonsense']))

    def test_only_selected_engines_are_called(self):
        calls = []
        # 第 271 项：google 现在要先过「本机连得上吗」的探活闸门，这里假装连得上
        import time
        translator._REACH['google'] = (time.time(), True)

        def fake(text, src, tgt):
            calls.append('google')
            return {'engine': 'Google 翻译', 'text': 'x'}

        with mock.patch.dict(translator._ENGINE_FETCHERS, {'google': fake}):
            res = translator.translate_text('hello', engines=['google'])
        self.assertEqual(['google'], res['engines'])
        self.assertEqual(1, len(calls), '只勾了一个引擎，就只能打一次')
        self.assertTrue(res['ok'])

    def test_settings_drive_selection_when_arg_omitted(self):
        seen = []

        def fake_youdao(text, src, tgt):
            seen.append('youdao')
            return {'engine': '有道词典', 'text': 'y'}

        fake_brain = type('B', (), {'_settings_read': staticmethod(
            # 第 295 项：带上"保存时的引擎版本"，否则后来新增的引擎会被 active_engines
            # 自动补进清单（那是给老配置用的，不是这个用例要测的东西）。
            lambda: {'trans_engines': ['youdao'],
                     'trans_engines_ver': translator.ENGINE_VER})})
        with mock.patch.dict('sys.modules', {'brain': fake_brain}), \
                mock.patch.dict(translator._ENGINE_FETCHERS, {'youdao': fake_youdao}):
            res = translator.translate_text('hello')
        self.assertEqual(['youdao'], res['engines'])
        self.assertEqual(['youdao'], seen)


class TestRateLimitSelfDefense(unittest.TestCase):
    """第 271 项：五家都是匿名公开接口（没有 cookie，配额按用户自己的出口 IP 算），
    所以这里要防的是「把自己的配额烧光」：词语分层、缓存、冷却、到点即返。"""

    def setUp(self):
        translator.reset_limits()

    def test_looks_like_word(self):
        self.assertTrue(translator.looks_like_word('会议'))
        self.assertTrue(translator.looks_like_word('machine learning'))
        self.assertFalse(translator.looks_like_word('今天的会议改到下午三点'))
        self.assertFalse(translator.looks_like_word('the meeting is moved to three'))
        self.assertFalse(translator.looks_like_word('会议，改到下午三点'))
        self.assertFalse(translator.looks_like_word(''))

    def test_sentence_does_not_hit_dictionary_engines(self):
        called = []

        def fake(text, src, tgt):
            called.append(1)
            return {'engine': '词典', 'text': 'x'}

        with mock.patch.dict(translator._ENGINE_FETCHERS, {'youdao': fake}):
            res = translator.translate_text('今天的会议改到下午三点',
                                            engines=['youdao'], tgt_lang='en')
        self.assertEqual([], called, '整句不该打词典接口')
        self.assertFalse(res['ok'])
        self.assertIn('只用于词语', res['skipped'][0]['reason'])

    def test_word_does_hit_dictionary_engines(self):
        called = []

        def fake(text, src, tgt):
            called.append(1)
            return {'engine': '词典', 'text': 'meeting'}

        with mock.patch.dict(translator._ENGINE_FETCHERS, {'youdao': fake}):
            res = translator.translate_text('会议', engines=['youdao'], tgt_lang='en')
        self.assertEqual(1, len(called))
        self.assertTrue(res['ok'])

    def test_cache_hit_makes_no_second_request(self):
        calls = []

        def fake(text, src, tgt):
            calls.append(text)
            return {'engine': 'M', 'text': 'hello'}

        with mock.patch.dict(translator._ENGINE_FETCHERS, {'mymemory': fake}):
            r1 = translator.translate_text('你好世界', engines=['mymemory'], tgt_lang='en')
            r2 = translator.translate_text('你好世界', engines=['mymemory'], tgt_lang='en')
        self.assertEqual(1, len(calls), '同样文本同样目标语，第二次必须走缓存')
        self.assertTrue(r1['results'][0]['text'])
        self.assertTrue(r2['results'][0].get('cached'))

    def test_failure_puts_engine_in_cooldown(self):
        def boom(text, src, tgt):
            raise RuntimeError('429 too many requests')

        with mock.patch.dict(translator._ENGINE_FETCHERS, {'mymemory': boom}):
            r1 = translator.translate_text('hello', engines=['mymemory'], tgt_lang='zh-CN')
        self.assertGreater(translator.cooling('mymemory'), 0)
        self.assertIn('冷却', r1['skipped'][0]['reason'])
        calls = []

        def fake(text, src, tgt):
            calls.append(1)
            return {'engine': 'M', 'text': '你好'}

        with mock.patch.dict(translator._ENGINE_FETCHERS, {'mymemory': fake}):
            r2 = translator.translate_text('hello', engines=['mymemory'], tgt_lang='zh-CN')
        self.assertEqual([], calls, '冷却期内不该再发请求')
        self.assertIn('限流冷却', r2['skipped'][0]['reason'])

    def test_no_result_does_not_trigger_cooldown(self):
        def empty(text, src, tgt):
            return None

        with mock.patch.dict(translator._ENGINE_FETCHERS, {'baidu': empty}):
            translator.translate_text('会议', engines=['baidu'], tgt_lang='en')
        self.assertEqual(0, translator.cooling('baidu'), '查无此词不等于被限流')

    def test_slow_engine_does_not_block_the_rest(self):
        import time as _t

        def slow(text, src, tgt):
            _t.sleep(0.6)
            return {'engine': 'S', 'text': '慢'}

        def fast(text, src, tgt):
            return {'engine': 'F', 'text': '快'}

        with mock.patch.dict(translator._ENGINE_FETCHERS, {'mymemory': slow, 'iciba': fast}), \
                mock.patch.object(translator, '_OVERALL', 0.25):
            t0 = _t.time()
            res = translator.translate_text('会议', engines=['mymemory', 'iciba'],
                                            tgt_lang='en')
            dt = _t.time() - t0
        self.assertLess(dt, 0.55, '慢引擎不能拖住整体返回')
        self.assertIn('快', [r['text'] for r in res['results']])
        self.assertTrue(any('未返回' in s['reason'] for s in res['skipped']))

    def test_unreachable_engine_is_skipped_without_request(self):
        calls = []

        def fake(text, src, tgt):
            calls.append(1)
            return {'engine': 'G', 'text': 'x'}

        import time
        translator._REACH['google'] = (time.time(), False)
        with mock.patch.dict(translator._ENGINE_FETCHERS, {'google': fake}):
            res = translator.translate_text('今天的会议改到下午三点',
                                            engines=['google'], tgt_lang='en')
        self.assertEqual([], calls, '连不上的那家别再发请求')
        self.assertIn('连不上', res['skipped'][0]['reason'])

    def test_reset_limits_clears_state(self):
        translator._FAILS['google'] = (3, 1e9)
        translator._CACHE[('x', 'y', 'z')] = (0, {'text': 'old'})
        translator.reset_limits()
        self.assertEqual(0, translator.cooling('google'))
        self.assertEqual({}, translator._CACHE)


class TestAutoTargetsUiLanguage(unittest.TestCase):
    """第 273 项：「自动」= 译成界面语言，不是写死的"中/英互译"。

    用户原话：「我选的操作界面语言是中文，那么自动模式下，我输入德语、日语、
    英语都要翻译成中文」。以前后端是"源语言是中文→译成默认语言，否则→中文"，
    而且 API 层还把 tl='auto' 换成 settings.trans_target_lang —— 默认语言正好
    等于源语言时（英语→英语）接口原样吐回原文，用户看到的就是"翻译不了"。
    """

    def test_foreign_inputs_all_go_to_chinese_ui(self):
        for src in ('en', 'de', 'ja', 'ko', 'es', 'ru'):
            self.assertEqual(translator.auto_target(src, 'zh-CN'), 'zh-CN',
                             '%s 应该译成中文' % src)

    def test_chinese_input_flips_to_english(self):
        self.assertEqual(translator.auto_target('zh-CN', 'zh-CN'), 'en')

    def test_english_ui(self):
        self.assertEqual(translator.auto_target('de', 'en'), 'en')
        self.assertEqual(translator.auto_target('en', 'en'), 'zh-CN')

    def test_spanish_ui_same_language_flips(self):
        self.assertEqual(translator.auto_target('es', 'es'), 'en')
        self.assertEqual(translator.auto_target('ja', 'es'), 'es')

    def test_label_follows_ui_language(self):
        self.assertIn('中文', translator.auto_label('zh-CN'))
        self.assertIn('English', translator.auto_label('en'))
        self.assertIn('español', translator.auto_label('es'))
        self.assertIn('português', translator.auto_label('pt'))
        self.assertIn('हिंदी', translator.auto_label('hi'))

    def test_auto_mode_uses_ui_language_not_setting(self):
        """settings 里存着 de，也不能把「自动」变成"译成德语"。"""
        with mock.patch.object(translator, 'ui_lang', return_value='zh-CN'), \
                mock.patch.object(translator, 'reachable', return_value=False):
            res = translator.translate_text('Entschuldigung, dieses Konto ist nicht berechtigt.',
                                            'auto', 'auto')
        self.assertEqual('zh-CN', res['tgt_lang'])
        self.assertEqual('de', res['src_lang'], '德文要认出来，才能按 de|zh 去翻')

    def test_auto_chinese_input_targets_english(self):
        with mock.patch.object(translator, 'ui_lang', return_value='zh-CN'), \
                mock.patch.object(translator, 'reachable', return_value=False):
            res = translator.translate_text('今天的会议改到下午三点', 'auto', 'auto')
        self.assertEqual('zh-CN', res['src_lang'])
        self.assertEqual('en', res['tgt_lang'])


class TestEchoDetection(unittest.TestCase):
    """第 273 项：接口把原文吐回来（只吃掉换行/空格）时，不许当译文显示。"""

    def test_newline_and_space_variant_is_echo(self):
        a = 'Sorry, this account is ineligible.\nYour account is not linked.'
        b = 'Sorry, this account is ineligible. Your account is not linked.'
        self.assertTrue(translator.looks_echo(a, b))

    def test_punctuation_churn_is_echo(self):
        self.assertTrue(translator.looks_echo(
            'Antigravity is unavailable. Please contact support.',
            'Antigravity is unavailable, please contact support'))

    def test_real_translation_is_not_echo(self):
        self.assertFalse(translator.looks_echo('hello world', '你好世界'))
        self.assertFalse(translator.looks_echo('会议', 'n. 会议；开会'))

    def test_empty_is_not_echo(self):
        self.assertFalse(translator.looks_echo('', ''))
        self.assertFalse(translator.looks_echo('abc', ''))

    def test_echo_engine_is_reported_not_shown(self):
        def echo(text, src, tgt):
            return {'engine': 'MyMemory 翻译', 'echo': True}

        with mock.patch.dict(translator._ENGINE_FETCHERS, {'mymemory': echo}):
            res = translator.translate_text('Hello world', engines=['mymemory'],
                                            tgt_lang='zh-CN')
        self.assertEqual([], res['results'])
        self.assertFalse(res['ok'])
        self.assertTrue(any('只返回了原文' in s['reason'] for s in res['skipped']))
        self.assertEqual(0, translator.cooling('mymemory'), '吐回原文不是限流，别冷却')


class TestDetectLangDominance(unittest.TestCase):
    """第 273 项：以前"见到哪个字符集就返回哪个"，一句英文夹一个汉字就判成中文。"""

    def test_english_with_one_chinese_char(self):
        self.assertEqual(translator.detect_lang('Sorry, this account 的 status is unknown.'), 'en')

    def test_german(self):
        self.assertEqual(translator.detect_lang('Entschuldigung, dieses Konto ist nicht berechtigt.'),
                         'de')

    def test_spanish(self):
        self.assertEqual(translator.detect_lang('Lo siento, esta cuenta no es elegible.'), 'es')

    def test_portuguese(self):
        self.assertEqual(translator.detect_lang('Desculpe, esta conta não está qualificada.'), 'pt')

    def test_japanese_with_kanji(self):
        """日文里夹汉字不该被当成中文（以前第一条就 return ja，改按占比后仍要 ja）。"""
        self.assertEqual(translator.detect_lang('このアカウントは資格がありません'), 'ja')

    def test_chinese_stays_chinese(self):
        self.assertEqual(translator.detect_lang('今天的会议改到下午三点'), 'zh-CN')
        self.assertEqual(translator.detect_lang('Hello 世界'), 'en',
                         '一句英文带个"世界"也算英文')


class TestByokEngines(unittest.TestCase):
    """第 274 项：自带密钥（DeepSeek / DeepL / 百度开放平台）。

    用户原话：「用户自己填 API key 按自己账户计费可以，比如 deepseek 的 apikey 来翻译是吗？」
    —— 是。前五家是网页免密钥接口（额度按出口 IP 算），这三家走用户自己的账号。
    这里锁死三件事：没密钥不联网、密钥只进请求头不进日志、各家协议字段别写错。
    """

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def read(self):
            return json.dumps(self._p).encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def setUp(self):
        # 缓存与冷却都是模块级的：不清的话，前一个用例写入的缓存会让后一个用例
        # 根本不发请求（"看到 KeyError: url"就是这么来的），冷却也会挡住下一个用例。
        translator.reset_limits()

    def _cfg(self, **kw):
        base = {pid: {f: '' for f in translator.BYOK_FIELDS} for pid in translator.BYOK_IDS}
        for pid, val in kw.items():
            base[pid].update(val)
        return base

    def test_no_key_means_no_request(self):
        calls = []

        def boom(*a, **kw):
            calls.append(1)
            raise AssertionError('没填密钥就不该发请求')

        with mock.patch.object(translator, 'byok_cfg', return_value=self._cfg()), \
                mock.patch.object(translator, '_open', boom):
            res = translator.translate_text('hello world', engines=['deepseek'], tgt_lang='zh-CN')
        self.assertEqual([], calls)
        self.assertEqual([], res['results'])
        self.assertNotIn('deepseek', [s['engine'] for s in res['skipped']],
                         '没填密钥不算"失败"，别每次翻译都刷一行')

    def test_byok_ready_flags(self):
        cfg = self._cfg(deepseek={'key': 'sk-x'}, baidu_api={'appid': 'a'})
        ready = translator.byok_ready(cfg)
        self.assertTrue(ready['deepseek'])
        self.assertFalse(ready['deepl'])
        self.assertFalse(ready['baidu_api'], 'appid 有了但 secret 没填，仍算没配好')

    def test_mask_secret(self):
        self.assertEqual('', translator.mask_secret(''))
        self.assertNotIn('abcd1234', translator.mask_secret('sk-abcd1234'))
        self.assertEqual('•' * 4, translator.mask_secret('abcd'))

    def test_deepseek_posts_chat_completions(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen['url'] = req.full_url
            seen['auth'] = req.headers.get('Authorization')
            seen['body'] = json.loads(req.data.decode('utf-8'))
            return self._Resp({'model': 'deepseek-chat',
                               'choices': [{'message': {'content': '抱歉，此账号没有使用资格。'}}]})

        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(deepseek={'key': 'sk-test-123'})), \
                mock.patch.object(translator, '_open', fake_urlopen):
            res = translator.translate_text('Sorry, this account is ineligible.',
                                            engines=['deepseek'], tgt_lang='zh-CN')
        self.assertTrue(seen['url'].endswith('/chat/completions'), seen['url'])
        self.assertIn('sk-test-123', seen['auth'])
        self.assertEqual('deepseek-chat', seen['body']['model'])
        prompt = seen['body']['messages'][-1]['content']
        self.assertIn('简体中文', prompt, '提示词里要写明目标语言，别让模型自己猜')
        self.assertEqual(1, len(res['results']))
        self.assertIn('抱歉', res['results'][0]['text'])

    def test_deepseek_echo_is_flagged(self):
        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(deepseek={'key': 'k'})), \
                mock.patch.object(translator, '_open',
                                  lambda req, timeout=None: self._Resp(
                                      {'choices': [{'message': {'content': 'Hello world'}}]})):
            res = translator.translate_text('Hello world', engines=['deepseek'], tgt_lang='zh-CN')
        self.assertEqual([], res['results'])
        self.assertTrue(any('只返回了原文' in s['reason'] for s in res['skipped']))

    def test_deepl_free_key_uses_free_host(self):
        seen = {}
        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(deepl={'key': 'abc:fx'})), \
                mock.patch.object(translator, '_open',
                                  lambda req, timeout=None: (seen.update(url=req.full_url) or
                                                             self._Resp({'translations': [{'text': '你好'}]}))):
            res = translator.translate_text('Hello', engines=['deepl'], tgt_lang='zh-CN')
        self.assertIn('api-free.deepl.com', seen['url'], '以 :fx 结尾的是免费版 key')
        self.assertEqual('你好', res['results'][0]['text'])

    def test_deepl_paid_key_uses_pro_host(self):
        seen = {}
        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(deepl={'key': 'abc'})), \
                mock.patch.object(translator, '_open',
                                  lambda req, timeout=None: (seen.update(url=req.full_url) or
                                                             self._Resp({'translations': [{'text': '你好'}]}))):
            translator.translate_text('Hello', engines=['deepl'], tgt_lang='zh-CN')
        self.assertIn('api.deepl.com/v2/translate', seen['url'])
        self.assertNotIn('api-free', seen['url'])

    def test_baidu_api_signs_with_md5(self):
        import hashlib
        import urllib.parse as up
        seen = {}
        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(baidu_api={'appid': 'app1', 'secret': 'sec1'})), \
                mock.patch.object(translator, '_open',
                                  lambda req, timeout=None: (seen.update(form=dict(up.parse_qsl(
                                      req.data.decode('utf-8')))) or
                                                             self._Resp({'trans_result': [{'dst': '你好'}]}))):
            res = translator.translate_text('Hello', engines=['baidu_api'], tgt_lang='zh-CN')
        form = seen['form']
        self.assertEqual('app1', form['appid'])
        expect = hashlib.md5(('app1' + form['q'] + form['salt'] + 'sec1').encode('utf-8')).hexdigest()
        self.assertEqual(expect, form['sign'], '签名算法必须与百度开放平台一致')
        self.assertEqual('你好', res['results'][0]['text'])

    def test_baidu_api_error_code_raises(self):
        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(baidu_api={'appid': 'a', 'secret': 's'})), \
                mock.patch.object(translator, '_open',
                                  lambda req, timeout=None: self._Resp(
                                      {'error_code': '54003', 'error_msg': 'Invalid Access Limit'})):
            res = translator.translate_text('Hello', engines=['baidu_api'], tgt_lang='zh-CN')
        self.assertGreater(translator.cooling('baidu_api'), 0, '接口报错要冷却，别硬撞')
        self.assertTrue(any('54003' in s['reason'] for s in res['skipped']))

    def test_byok_engines_are_not_dictionary_only(self):
        for pid in translator.BYOK_IDS:
            self.assertNotIn(pid, translator._DICT_ONLY,
                             '自带密钥这几家是整句翻译，不该被"只用于词语"挡掉')

    def test_byok_budget_larger_than_web_engines(self):
        self.assertGreater(translator._BYOK_OVERALL, translator._OVERALL,
                           '大模型比网页接口慢，得给更长的等待预算')
        self.assertGreater(translator._BYOK_TIMEOUT, translator._OVERALL)

    def test_google_api_posts_key_in_body_and_unescapes(self):
        """Google 云翻译 v2：key 放请求体（别拼 URL，URL 会进日志），译文要 html 反转义。"""
        import urllib.parse as up
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen['url'] = req.full_url
            seen['form'] = dict(up.parse_qsl(req.data.decode('utf-8')))
            return self._Resp({'data': {'translations': [
                {'translatedText': '你好 &amp; 欢迎'}]}})

        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(google_api={'key': 'AIza-test'})), \
                mock.patch.object(translator, '_open', fake_urlopen):
            res = translator.translate_text('Hello & welcome', engines=['google_api'],
                                            tgt_lang='zh-CN')
        self.assertIn('translation.googleapis.com/language/translate/v2', seen['url'])
        self.assertNotIn('AIza-test', seen['url'], '密钥不许出现在 URL 里')
        self.assertEqual('AIza-test', seen['form']['key'])
        self.assertEqual('zh-CN', seen['form']['target'])
        self.assertEqual('text', seen['form']['format'])
        self.assertEqual('你好 & 欢迎', res['results'][0]['text'], 'HTML 实体要反转义')

    def test_google_api_bad_key_raises_with_google_message(self):
        """key 无效 → 把 Google 的原话带回来并进冷却，别静默当成功。"""
        import io as _io
        import urllib.error as _ue

        def boom(req, timeout=None):
            raise _ue.HTTPError(req.full_url, 400, 'Bad Request', {},
                                _io.BytesIO(b'{"error": {"message": "API key not valid."}}'))

        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(google_api={'key': 'bad'})), \
                mock.patch.object(translator, '_open', boom):
            res = translator.translate_text('Hello', engines=['google_api'], tgt_lang='zh-CN')
        self.assertGreater(translator.cooling('google_api'), 0)
        self.assertTrue(any('API key not valid' in s['reason'] for s in res['skipped']))

    def test_openai_compat_defaults_and_custom_base(self):
        """OpenAI 兼容：默认 api.openai.com/v1 + gpt-4o-mini；model 栏填 http 开头就当 base。"""
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen['url'] = req.full_url
            seen['auth'] = req.headers.get('Authorization')
            seen['body'] = json.loads(req.data.decode('utf-8'))
            return self._Resp({'model': seen['body']['model'],
                               'choices': [{'message': {'content': '你好'}}]})

        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(openai={'key': 'sk-openai'})), \
                mock.patch.object(translator, '_open', fake_urlopen):
            translator.translate_text('Hello', engines=['openai'], tgt_lang='zh-CN')
        self.assertEqual('https://api.openai.com/v1/chat/completions', seen['url'])
        self.assertEqual('gpt-4o-mini', seen['body']['model'])
        self.assertIn('sk-openai', seen['auth'])

        seen.clear()
        with mock.patch.object(translator, 'byok_cfg',
                               return_value=self._cfg(openai={'key': 'sk-x',
                                                              'model': 'https://api.siliconflow.cn/v1'})), \
                mock.patch.object(translator, '_open', fake_urlopen):
            translator.translate_text('Hello with my own base', engines=['openai'],
                                      tgt_lang='zh-CN')
        self.assertEqual('https://api.siliconflow.cn/v1/chat/completions', seen['url'])

    def test_byok_ready_covers_new_providers(self):
        ready = translator.byok_ready(self._cfg(google_api={'key': 'AIza'}, openai={'key': 'sk'}))
        self.assertTrue(ready['google_api'])
        self.assertTrue(ready['openai'])
        self.assertEqual(5, len(translator.BYOK_IDS), '自带密钥要覆盖五家')


if __name__ == '__main__':
    unittest.main()

