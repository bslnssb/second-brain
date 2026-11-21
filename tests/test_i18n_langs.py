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
# 第 278 项：品牌名/字段名本来就不翻译（MyMemory、DeepSeek、DeepL、API Key、APP ID…）
SAME_OK = {'kind_audio', 'care_title',
           'sub_pdf', 'sub_word', 'sub_excel', 'sub_ppt', 'sub_md',
           'te_mymemory', 'te_youdao', 'te_baidu', 'te_iciba', 'te_deepseek', 'te_deepl',
           'te_deepseek_hint', 'te_field_api_key', 'te_field_appid'}


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
        # 610 = 第 275 项「最近记录显示几条」之后；第 277 项又加「深度体检」2 键
        # 616 → 652：第 278 项聚合翻译引擎名/自带密钥卡/翻译弹窗共 +36 键
        # 652 → 657：第 279 项 BYOK 补 Google 云翻译 / OpenAI 兼容，共 +5 键
        # 657 → 687：第 281 项剪贴板面板 / 标题栏副标题 / 截图预览 / 设置页语言下拉共 +30 键
        # 687 → 697：第 281 项（B）体检卡「全库校验」那一行 + 按钮，共 +10 键
        # 697 → 698：第 281 项（三次）错误上报失败时给普通用户的「已存本机，稍后重试」
        # 698 → 700：第 294 项「网络位置需 Pro」提示 + 拒绝文案，共 +2 键
        # 700 → 709：第 294 项（B）网络位置独立区块（标题/计数/空态/标签/占位/
        #              按钮/怎么拿到地址/格式提醒），共 +9 键
        # 717 → 724：第 294 项（G/H）范围书签 6 键 + 识别整本说明 1 键
# 724 → 737：第 295 项聚合翻译 —— 腾讯引擎名/说明 2 键 + 跳过原因与代理状态 10 键 + 徽标 1 键
# 738：第 295 项补一句代理说明（trans_proxy_tip）
# 753：第 296 项高级五块说明 5 键 + 卸载卡 10 键
        # 709 → 713：第 294 项（E）保存后「立即按新范围更新」4 个键
        #              （按钮/更新中/完成/起不来）
        # 800：第 299 项代理 27 键 + 第 300 项快捷键一览 4 键 + 第 307 项 blank_jump 2 键
        # + 第 308 项索引横幅 6 键，共 800 键 ×5 语言
        # 801：第 319 项「定位失败」与「浏览器里没有定位」分开，+1 键（ctx_reveal_fail）
        # 802：第 320 项「索引在忙」提示（db_busy），+1 键
        # 803：第 322 项「ext: 与侧栏类型没有交集」提示（ext_no_match），+1 键
        # 811：第 324 项左栏类型子分类 + 文件夹分组，共 +7 键（folders / sub_* 6 个）
        # 815：第 325 项预览展开 / 收起 / 上下页，共 +4 键
        # 824：第 327 项左栏设置页（nav_sidebar + sb_group/tip + 三块标题与说明），共 +9 键
        # 827：338 的计数开关 +2 键、341 的代理直连提示 +1 键
        # 第 346 项：+2 引导页空库提示 + 3 朗读 + 2 自带密钥折叠 = 834
        # 837：第 356 项帮助弹窗「命令（第一个词）」分区 +3 键（help_cmd / cd1 说明 / cd5 小字）
        # 839：第 358 项帮助并进设置 —— 删 help_kb（快捷键并到热键页），
        #      +help_intro / help_kb_hint / sc_more_go 三键
        # 846：第 362 项壁纸「画面位置/放大/生效范围」+8 键（ap_wall_*）
        # 849：第 364 项壁纸块可收放 +3 键（expand / collapse / empty_hint）
        # 859：第 366 项整理索引库 +10 键（maint_compact* / compact_banner / e_compact_busy）
        # 863：第 369 项截图翻译三入口 +4 键（snip_trans_btn / snip_menu_pure / snip_menu_trans / snip_menu_hint）
        # 878：第 380 项关于页「开源组件」+15 键（oss_* 清单 / 列头 / 模型指纹 / 目录 / 复制）
        # 881：第 386 项详情栏「默认展开」+3 键（dtl_expand / _label / _tip）
        self.assertEqual(881, len(self.D['ZH']))
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
