#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二大脑 · 质量自检与检索评测（全程合成数据，绝不碰用户真实索引库）

四条互相独立的正确性证据链，全部可由任何人重复运行复核：
  A. 检索评测：合成语料 + 金标查询 → recall@1 / recall@3 / MRR，含相关度排序检验
  B. 双抽取器交叉验证：pypdf 与 pymupdf 两个独立实现对同一 PDF 的文本一致性
  C. OCR 字符错误率：程序自己渲染"标准答案"图片 → OCR → 与答案比对（CER）
  D. 健壮性 fuzz：畸形/截断/乱码文件喂给 extract_text，任何情况下不许崩溃
外加：SQLite integrity_check、FTS5 integrity-check、文件变更重索引检验、
加密 PDF 归因检验、prune 清理检验。报告写入 eval_report.txt。

用法：python brain.py selftest（或 eval）；桌面版设置面板点「🩺 质量自检」。
"""
import contextlib
import difflib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import brain

RESULTS = []          # (status, name, detail)

def _rec(status, name, detail=''):
    RESULTS.append((status, name, detail))
    print(f'[{status}] {name}' + (f' —— {detail}' if detail else ''))

def _has(name):
    try:
        return __import__(name)
    except Exception:
        return None

def _mk_pdf(mupdf, path, lines, fontsize=14):
    doc = mupdf.open()
    page = doc.new_page()
    y = 72
    for ln in lines:
        page.insert_text((72, y), ln, fontname='china-s', fontsize=fontsize)
        y += int(fontsize * 1.7)
    doc.save(str(path))
    doc.close()

def _mk_docx(path, text):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml',
                   f'<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')

def _norm(s):
    return ''.join(s.split())

def _search_quiet(q, limit=5):
    with contextlib.redirect_stdout(io.StringIO()):
        return brain.search(q, limit)

def run_all(verbose=True, write_report=True):
    """跑全部证据链；返回 True=全部通过（SKIP 不算失败）"""
    RESULTS.clear()
    mupdf = _has('pymupdf')
    has_ocr = _has('rapidocr_onnxruntime') is not None
    tmp = Path(tempfile.mkdtemp(prefix='sb-selftest-'))
    corpus = tmp / 'corpus'
    corpus.mkdir(parents=True)
    old_db = brain.DB
    brain.DB = tmp / 'brain.db'
    t0 = time.time()

    # ── 合成语料（内容即金标答案）
    (corpus / 'a.txt').write_text('第二大脑评测语料第一号：机房巡检记录模板，包含巡检时间、巡检人与整改期限。',
                                  encoding='utf-8')
    (corpus / 'b.txt').write_text('评测语料第二号：生鲜冷链运输的温控标准与应急预案。', encoding='utf-8')
    (corpus / 'f.txt').write_text('应急预案专项汇总。' + '应急预案。' * 6 +
                                  '本篇重复应急预案以检验相关度排序。', encoding='utf-8')
    # 正文刻意不含标题里的词：专供 H1「文件名参与检索」用
    (corpus / '蓝图评审会议纪要.txt').write_text(
        '本文件正文只写一句无关的话，标题里的关键词一个都不出现。', encoding='utf-8')
    _mk_docx(corpus / 'c.docx', '评测语料第三号：SQL注入漏洞的应急响应流程与修复建议。')
    enc_made = False
    if mupdf:
        _mk_pdf(mupdf, corpus / 'd.pdf',
                ['评测语料第四号：网络安全等级保护二级测评要点。', 'Zero Trust Architecture notes.'])
        enc = mupdf.open()
        pg = enc.new_page()
        pg.insert_text((72, 72), '加密文件语料：机房门禁配置清单。', fontname='china-s', fontsize=14)
        enc.save(str(corpus / 'e_encrypted.pdf'), encryption=mupdf.PDF_ENCRYPT_AES_256,
                 owner_pw='owner', user_pw='user')
        enc.close()
        enc_made = True

    # 扫描清单 + 索引（走真实 index 路径）
    import csv as _csv
    files = sorted(corpus.iterdir())
    csvp = tmp / 'scan.csv'
    with open(csvp, 'w', newline='', encoding='utf-8-sig') as f:
        w = _csv.writer(f)
        w.writerow(['path', 'size'])
        for p in files:
            w.writerow([str(p), p.stat().st_size])
    scope_path = tmp / 'scope.json'
    scope_path.write_text(json.dumps(
        {'scan_csv': str(csvp), 'include_dirs': [str(corpus).lower()], 'exclude_dirs': []}),
        encoding='utf-8')

    try:
        # ══ A. 检索评测（金标查询）
        try:
            brain.index(str(scope_path))
            golden = [('巡检记录', 'a.txt'), ('冷链运输', 'b.txt'),
                      ('SQL注入', 'c.docx'), ('应急预案', 'f.txt')]
            if mupdf:
                golden.append(('等级保护', 'd.pdf'))
            r1 = r3 = 0
            mrr = 0.0
            order_ok = None
            for q, expect in golden:
                hits = _search_quiet(q, 5)
                names = [Path(p).name for p, _s in hits]
                if names and names[0] == expect:
                    r1 += 1
                if expect in names[:3]:
                    r3 += 1
                if expect in names:
                    mrr += 1.0 / (names.index(expect) + 1)
                if q == '应急预案' and {'f.txt', 'b.txt'} <= set(names):
                    order_ok = names.index('f.txt') < names.index('b.txt')
            k = len(golden)
            _rec('PASS' if r3 == k else 'FAIL',
                 f'A1 检索评测 recall@3 = {r3}/{k}',
                 f'recall@1={r1}/{k}, MRR={mrr / k:.3f}')
            _rec('PASS' if order_ok else ('FAIL' if order_ok is None else 'FAIL'),
                 'A2 相关度排序（重复关键词多的文档排前）',
                 'bm25 排序生效' if order_ok else '排序异常')
            # 加密件必须搜不到
            hits = _search_quiet('门禁配置', 30)
            bad = [p for p, _s in hits if 'e_encrypted' in p]
            _rec('PASS' if not bad else 'FAIL',
                 'A3 加密 PDF 不入库不可检索', f'{len(bad)} 条泄漏' if bad else '确认零泄漏')
        except Exception as e:
            _rec('FAIL', 'A 检索评测', f'{type(e).__name__}: {e}')

        # ══ B. 双抽取器交叉验证（用标准字体 ASCII 基准页公平对比；
        #     中文抽取质量已由 A 金标检索与 C OCR 两条链覆盖）
        try:
            if not mupdf:
                _rec('SKIP', 'B 双抽取器交叉验证（缺 pymupdf）')
            else:
                from pypdf import PdfReader
                base = tmp / 'xcheck.pdf'
                doc = mupdf.open()
                pg = doc.new_page()
                pg.insert_text((72, 72), 'The quick brown fox jumps over the lazy dog 1234567890.',
                               fontname='helv', fontsize=14)
                pg.insert_text((72, 108), 'Local full-text search evaluation page for cross-check.',
                               fontname='helv', fontsize=14)
                doc.save(str(base))
                doc.close()
                t1 = _norm('\n'.join((p.extract_text() or '')
                                      for p in PdfReader(str(base)).pages))
                d2 = mupdf.open(str(base))
                t2 = _norm('\n'.join(p.get_text() for p in d2))
                d2.close()
                ratio = difflib.SequenceMatcher(None, t1, t2).ratio()
                _rec('PASS' if ratio >= 0.8 else 'FAIL',
                     f'B 双抽取器一致性 pypdf vs pymupdf = {ratio:.2f}',
                     '两个独立实现对同一基准页给出的文本一致' if ratio >= 0.8 else '一致性过低')
        except Exception as e:
            _rec('FAIL', 'B 双抽取器交叉验证', f'{type(e).__name__}: {e}')

        # ══ C. OCR 字符错误率（自渲染标准答案）
        try:
            if not (mupdf and has_ocr):
                _rec('SKIP', 'C OCR 字符错误率（缺 pymupdf 或 rapidocr）')
            else:
                truth = '第二大脑质量自检 OCR 二〇二六 ABC 123'
                tdir = tmp / 'ocr'
                tdir.mkdir()
                _mk_pdf(mupdf, tdir / 'ocr.pdf', [truth], fontsize=22)
                doc = mupdf.open(str(tdir / 'ocr.pdf'))
                pix = doc[0].get_pixmap(matrix=mupdf.Matrix(180 / 72, 180 / 72))
                from rapidocr_onnxruntime import RapidOCR
                ocr = brain._get_ocr()
                res, _ = ocr(pix.tobytes('png'))
                got = ''.join(line[1] for line in res) if res else ''
                doc.close()
                ratio = difflib.SequenceMatcher(None, _norm(truth), _norm(got)).ratio()
                cer = 1 - ratio
                _rec('PASS' if cer <= 0.2 else 'FAIL',
                     f'C OCR 字符错误率 CER = {cer:.1%}',
                     f'标准答案 {len(_norm(truth))} 字，识别 {len(_norm(got))} 字')
        except Exception as e:
            _rec('FAIL', 'C OCR 字符错误率', f'{type(e).__name__}: {e}')

        # ══ D. 健壮性 fuzz：畸形文件绝不许把 extract_text 搞崩
        try:
            base = (corpus / 'd.pdf').read_bytes() if mupdf else b'%PDF-1.4 fake'
            fz = tmp / 'fuzz'
            fz.mkdir()
            cases = {
                '截断PDF.pdf': base[:len(base) // 3],
                '头部PDF.pdf': base[:10],
                '随机字节.pdf': os.urandom(512),
                '空PDF.pdf': b'',
                '随机字节.docx': os.urandom(256),
                '空docx.docx': b'',
                '孤立代理.txt': '\ud800bad'.encode('utf-16', 'surrogatepass'),
                '二进制.html': os.urandom(128),
                '半截ZIP.epub': b'PK\x03\x04' + os.urandom(64),
            }
            bad = []
            for name, data in cases.items():
                p = fz / name
                p.write_bytes(data)
                try:
                    res = brain.extract_text(p)
                    if not isinstance(res, (str, type(None))):
                        bad.append(name)
                except Exception:
                    bad.append(name)
            _rec('PASS' if not bad else 'FAIL',
                 f'D 畸形文件 fuzz：{len(cases)} 例',
                 '全部安全返回，零崩溃' if not bad else f'崩溃: {bad}')
        except Exception as e:
            _rec('FAIL', 'D 健壮性 fuzz', f'{type(e).__name__}: {e}')

        # ══ E. 数据库完整性
        try:
            con = brain.init_db()
            assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            con.execute("INSERT INTO docs(docs) VALUES('integrity-check')")
            total = con.execute('SELECT count(*) FROM docs').fetchone()[0]
            expect = 6 if mupdf else 5   # 5(或4)+蓝图评审会议纪要.txt
            _rec('PASS' if total == expect else 'FAIL',
                 f'E SQLite/FTS5 完整性检查，库内 {total} 篇（预期 {expect}）',
                 'integrity_check + FTS5 integrity-check 双检通过')
            con.close()
        except Exception as e:
            _rec('FAIL', 'E 数据库完整性', f'{type(e).__name__}: {e}')

        # ══ F. 文件变更 → 自动重索引（内容过期的文件必须能被搜到新词）
        try:
            with open(corpus / 'b.txt', 'a', encoding='utf-8') as f:
                f.write('新增关键词：量子密钥分发试验。')
            time.sleep(1.1)                      # 保证 mtime 变化可观测
            brain.index(str(scope_path))
            hits = _search_quiet('量子密钥分发', 5)
            hit = [p for p, _s in hits if 'b.txt' in p]
            _rec('PASS' if hit else 'FAIL',
                 'F 文件变更自动重索引', '改过的文件新内容立即可搜' if hit else '新内容搜不到')
        except Exception as e:
            _rec('FAIL', 'F 文件变更自动重索引', f'{type(e).__name__}: {e}')

        # ══ G. 失败归因 + prune 清理
        try:
            con = brain.init_db()
            reason = con.execute("SELECT reason FROM meta WHERE path LIKE '%e_encrypted%'") \
                        .fetchone()
            _rec('PASS' if (reason and reason[0] == '加密') else 'FAIL',
                 'G1 失败归因（加密件记为「加密」）', f'reason={reason[0] if reason else None}')
            con.close()
            (corpus / 'c.docx').unlink()
            brain.prune()
            hits = _search_quiet('SQL注入', 5)
            left = [p for p, _s in hits if 'c.docx' in p]
            _rec('PASS' if not left else 'FAIL',
                 'G2 prune 清理已删除文件', '删除文件后索引同步清理' if not left else '仍残留')
        except Exception as e:
            _rec('FAIL', 'G 失败归因与清理', f'{type(e).__name__}: {e}')

        # ══ H. 文件名检索 + 路径过滤（两个曾经静默失效的点，留回归测试钉住）
        try:
            con = brain.init_db()
            hits = brain.search_rows(con, '蓝图评审会议纪要', 20)
            got = any(p.endswith('蓝图评审会议纪要.txt') for p, _s in hits)
            _rec('PASS' if got else 'FAIL', 'H1 文件名参与检索',
                 '正文里没有、文件名有的文档可以搜到' if got else '只能搜正文，文件名命不中')
            # 用 ≥3 字符的 ASCII 过滤条件：这正是过去被 FTS5 trigram 接管、悄悄返回 0 条的形态
            n_hit = len(brain.search_rows(con, '巡检', 20, None, tmp.name))
            n_miss = len(brain.search_rows(con, '巡检', 20, None, 'zz-no-such-dir-zz'))
            _rec('PASS' if (n_hit > 0 and n_miss == 0) else 'FAIL', 'H2 路径过滤生效',
                 f'命中 {n_hit} 条 / 不存在的路径 {n_miss} 条')
            con.close()

            # 孤立代理项（pymupdf 对字体映射损坏的 PDF 会吐出这种字符）曾经让整篇
            # 文档在写 JSON / 存库时抛 UnicodeEncodeError，表现为「解析失败」。
            bad = 'abc' + chr(0xD800) + 'def' + chr(0xDC80)
            fixed = brain._clean_text(bad)
            ok = True
            try:
                json.dumps(fixed).encode('utf-8')
            except UnicodeEncodeError:
                ok = False
            _rec('PASS' if ok else 'FAIL', 'H3 畸形字符净化',
                 '孤立代理项已替换为 U+FFFD，整篇不会因此报废' if ok
                 else f'仍含不可编码字符：{fixed!r}')
        except Exception as e:
            _rec('FAIL', 'H 文件名检索与路径过滤', f'{type(e).__name__}: {e}')
    finally:
        brain.DB = old_db
        brain._prog(state='idle')
        shutil.rmtree(tmp, ignore_errors=True)

    fails = [r for r in RESULTS if r[0] == 'FAIL']
    skips = [r for r in RESULTS if r[0] == 'SKIP']
    passed = len(RESULTS) - len(fails) - len(skips)
    ok = not fails
    summary = f'{passed} 项通过 / {len(fails)} 失败 / {len(skips)} 跳过'
    if write_report:
        lines = [
            f'第二大脑 {brain.APP_VERSION} 质量自检与评测报告',
            f'时间: {time.strftime("%Y-%m-%d %H:%M:%S")}',
            f'Python {sys.version.split()[0]} | SQLite {sqlite3.sqlite_version} | '
            f'pymupdf {"有" if mupdf else "无"} | rapidocr {"有" if has_ocr else "无"}',
            '-' * 60,
        ]
        lines += [f'[{s}] {n}' + (f' —— {d}' if d else '') for s, n, d in RESULTS]
        lines += ['-' * 60, summary, 'OVERALL: ' + ('PASS' if ok else 'FAIL')]
        try:
            (brain.app_dir() / 'eval_report.txt').write_text('\n'.join(lines), encoding='utf-8')
        except Exception:
            pass
    print(summary)
    print('OVERALL: ' + ('PASS' if ok else 'FAIL'))
    return ok

if __name__ == '__main__':
    run_all(verbose=True, write_report=True)
    sys.exit(0 if not any(r[0] == 'FAIL' for r in RESULTS) else 1)
