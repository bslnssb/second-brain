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
            if not mupdf:
                # 加密样本本身要用 pymupdf 造，core 版没有它 → 没有可验证的对象。
                # 判 PASS 会让人误以为「查过了」，所以这里如实跳过。
                _rec('SKIP', 'A3 加密 PDF 不入库不可检索（缺 pymupdf，无加密样本）')
            else:
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
            if not mupdf:
                # 同上：加密样本由 pymupdf 生成，core 版里没有这个文件，
                # 查不到记录不代表归因失败。
                _rec('SKIP', 'G1 失败归因（缺 pymupdf，无加密样本）')
            else:
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

        # ══ I. 「更新」防清空护栏（2026-11-04 搬家事故的回归测试）
        # 事故形态：索引范围里的文件夹被搬走 → 扫描安静地扫出 0 个 → prune 把
        # 整库当成"用户把文件删了"。这里把三道护栏逐个钉住。
        guard = None
        try:
            guard = Path(tempfile.mkdtemp(prefix='sb-guard-'))
            groot = guard / 'root'
            gscope = guard / 'scope.json'

            def _gfill(dead):
                gscope.write_text(json.dumps({'scan_csv': 'x.csv',
                                              'include_dirs': [str(groot)],
                                              'exclude_dirs': []}, ensure_ascii=False),
                                  encoding='utf-8')
                c = brain.init_db()
                c.execute('DELETE FROM meta')
                for i in range(100):
                    p = str(groot / (f'dead{i}.txt' if i < dead else f'live{i}.txt'))
                    c.execute('INSERT INTO meta(path, indexed_at) VALUES(?,?)', (p, 0))
                c.commit()
                c.close()

            def _gcount():
                c = brain.init_db()
                n = c.execute('SELECT COUNT(*) FROM meta').fetchone()[0]
                c.close()
                return n

            def _gfiles(dead):
                groot.mkdir(parents=True, exist_ok=True)
                for i in range(dead, 100):
                    (groot / f'live{i}.txt').write_text('x')

            # I1：范围目录本身不在了 —— 它名下一条都不许删
            _gfill(0)
            _gfiles(0)
            shutil.rmtree(groot)
            n1 = brain.prune(scope_path=str(gscope))
            left1 = _gcount()
            _rec('PASS' if (n1 == 0 and left1 == 100) else 'FAIL',
                 'I1 索引范围被搬走 → 一条不删',
                 f'删了 {n1} 条、剩 {left1} 条' if (n1 or left1 != 100) else
                 '范围目录不存在时整体跳过（这正是搬家事故的形态）')

            # I2：一次消失过半 —— 停下问人，不删
            _gfill(60)
            _gfiles(60)
            brain._prog(state='busy')
            n2 = brain.prune(scope_path=str(gscope))
            left2 = _gcount()
            blocked = brain.PROG.get('k') == 'e_prune_bulk'
            _rec('PASS' if (n2 == 0 and left2 == 100 and blocked) else 'FAIL',
                 'I2 一次要清掉过半 → 暂停并报错',
                 f'删了 {n2} 条、剩 {left2} 条、k={brain.PROG.get("k")}')

            # I3：人已确认（--force）—— 照清
            n3 = brain.prune(force=True, scope_path=str(gscope))
            _rec('PASS' if n3 == 60 and _gcount() == 40 else 'FAIL',
                 'I3 人工确认后（force）照清',
                 f'删了 {n3} 条、剩 {_gcount()} 条')

            # I4：少量真删除 —— 照常清理，不许误刹车
            _gfill(3)
            _gfiles(3)
            n4 = brain.prune(scope_path=str(gscope))
            _rec('PASS' if (n4 == 3 and _gcount() == 97) else 'FAIL',
                 'I4 少量真删除照常清理',
                 f'删了 {n4} 条、剩 {_gcount()} 条')
        except Exception as e:
            _rec('FAIL', 'I 更新防清空护栏', f'{type(e).__name__}: {e}')
        finally:
            if guard is not None:
                shutil.rmtree(str(guard), ignore_errors=True)

        # ══ J. 范围搬家后「自动认亲」（第 2 层，2026-11-04 事故的正解）
        # 用户把文件夹整体挪走 → 库里全是旧地址 → 先在本机找同名文件夹，再拿库里的
        # 旧路径做抽检；对上才平移，对不上一条都不许改。
        jroot = None
        real_jidx = brain._dir_index
        try:
            jroot = Path(tempfile.mkdtemp(prefix='sb-repair-'))
            jold = jroot / 'oldplace' / '1' / 'library'
            (jold / 'a').mkdir(parents=True)
            for n in ('doc1.docx', 'doc2.pdf', 'a/x.md'):
                (jold / n).write_text('测试内容 ' + n, encoding='utf-8')
            (jroot / '1').mkdir()
            jnew = jroot / '1' / 'library'
            shutil.move(str(jold), str(jnew))        # 搬家：旧目录消失
            jscope = jroot / 'scope.json'

            def _jfill():
                jscope.write_text(json.dumps({'scan_csv': 'x.csv',
                                              'include_dirs': [str(jold)],
                                              'exclude_dirs': []}, ensure_ascii=False),
                                  encoding='utf-8')
                c = brain.init_db()
                c.execute('CREATE TABLE IF NOT EXISTS dirs(path TEXT PRIMARY KEY, '
                          'name TEXT NOT NULL, parent TEXT, py TEXT, ini TEXT)')
                c.execute('DELETE FROM meta')
                c.execute('DELETE FROM files')
                for n in ('doc1.docx', 'doc2.pdf', 'a/x.md'):
                    p = str(jold / n)
                    c.execute('INSERT INTO meta(path, indexed_at) VALUES(?,?)', (p, 0))
                    c.execute('INSERT OR REPLACE INTO files(path, name, dir, size, mtime) '
                              'VALUES(?,?,?,?,?)',
                              (p, os.path.basename(p), os.path.dirname(p), 1.0, 1.0))
                c.commit()
                c.close()

            def _jcount(prefix):
                c = brain.init_db()
                n = c.execute('SELECT COUNT(*) FROM meta WHERE lower(path) LIKE ?',
                              (prefix.lower() + os.sep + '%',)).fetchone()[0]
                c.close()
                return n

            # J1：唯一同名目录 + 抽检全中 → 自动平移，且范围配置跟着改、留了备份
            _jfill()
            brain._dir_index = lambda root: {'library': [str(jnew)]}
            left = brain._repair_then_recheck([str(jold)], str(jscope))
            moved = _jcount(str(jnew))
            data = json.loads(jscope.read_text(encoding='utf-8'))
            baks = list(jroot.glob('scope.json.bak-*'))
            ok = (left == [] and moved == 3 and _jcount(str(jold)) == 0
                  and data['include_dirs'][0] == str(jnew) and bool(baks))
            _rec('PASS' if ok else 'FAIL', 'J1 搬家后自动认亲并平移路径',
                 '剩余缺目录、平移条数、范围配置、备份都对' if ok else
                 f'left={left} moved={moved} old={_jcount(str(jold))} '
                 f'scope={data["include_dirs"]} bak={len(baks)}')

            # J2：同名但里面根本不是那批文件 → 一条不改（宁可不动，也不猜）
            _jfill()
            decoy = jroot / 'decoy' / 'library'
            decoy.mkdir(parents=True)
            brain._dir_index = lambda root: {'library': [str(decoy)]}
            fixed2 = brain.repair_scope([str(jold)], str(jscope))
            data2 = json.loads(jscope.read_text(encoding='utf-8'))
            ok2 = (fixed2 == [] and _jcount(str(jold)) == 3
                   and data2['include_dirs'][0] == str(jold))
            _rec('PASS' if ok2 else 'FAIL', 'J2 同名但抽检对不上 → 一条不改',
                 '抽检不够就退回第 1 层的"停下"' if ok2 else
                 f'fixed={fixed2} old={_jcount(str(jold))} scope={data2["include_dirs"]}')

            # J3：两个都像 → 不在黑窗里猜，把候选交给界面点选（点完才动库）
            _jfill()
            d1 = jroot / 'p1' / 'library'
            d2 = jroot / 'p2' / 'library'
            for d in (d1, d2):
                (d / 'a').mkdir(parents=True)
                for n in ('doc1.docx', 'doc2.pdf'):
                    (d / n).write_text('测试内容', encoding='utf-8')
                (d / 'a' / 'x.md').write_text('测试内容', encoding='utf-8')
            brain._dir_index = lambda root: {'library': [str(d1), str(d2)]}
            brain.REPAIR_ASK.update(state='idle', old='', items=[], scope='')
            fixed3 = brain.repair_scope([str(jold)], str(jscope))
            ask = dict(brain.REPAIR_ASK)
            ok3a = (fixed3 == [] and ask.get('state') == 'wait'
                    and len(ask.get('items') or []) == 2)
            ok3b, _rep = brain.repair_choose(0)
            data3 = json.loads(jscope.read_text(encoding='utf-8'))
            ok3 = (ok3a and ok3b and brain.REPAIR_ASK.get('state') == 'idle'
                   and _jcount(str(d1)) == 3 and _jcount(str(jold)) == 0
                   and data3['include_dirs'][0] == str(d1))
            _rec('PASS' if ok3 else 'FAIL', 'J3 两个候选都像 → 交界面点选，点完才动',
                 '候选记下、按选中的那条平移、待确认清掉' if ok3 else
                 f'ask={ask.get("state")}/{len(ask.get("items") or [])} '
                 f'ok3b={ok3b} d1={_jcount(str(d1))} old={_jcount(str(jold))} '
                 f'scope={data3["include_dirs"]}')
        except Exception as e:
            _rec('FAIL', 'J 范围搬家自动认亲', f'{type(e).__name__}: {e}')
        finally:
            brain._dir_index = real_jidx
            if jroot is not None:
                shutil.rmtree(str(jroot), ignore_errors=True)
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
