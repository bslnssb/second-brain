# -*- coding: utf-8 -*-
"""补录：把之前提不出文本、或提取结果不可靠的文件重新处理一遍。

原理：失败文件只记在 meta 不在 docs；这里把它们从 meta 移除，再调 brain.index
重新走一遍（其余已入库文件都在 meta 里，会被秒跳过）。可反复运行。

用法：
  python ocr_backfill.py                   # 补历史失败项（PDF / TXT / MD / EPUB）
  python ocr_backfill.py --all             # 强制重跑 txt/md/epub 全部（解析规则修过之后用）
  python ocr_backfill.py --all txt,epub    # 强制重跑指定扩展名

什么时候需要 --all：解析器本身改了（比如修好了 UTF-16 编码识别、修好了 epub 解包），
那些文件当初"成功"入库但正文是乱码或空的，不会出现在失败清单里，得强制重跑。
"""
import sys
from pathlib import Path

import brain

# 默认补录范围：解析器可改进的格式（.doc / 企业加密文档救不了，不在此列）
RETRY_EXTS = ('.pdf', '.txt', '.md', '.epub')
# --all 的默认范围：纯文本与电子书，重跑快且最可能受编码/解包修复影响
ALL_EXTS = '.txt,.md,.epub'

con = brain.init_db()
docs = {r[0] for r in con.execute('SELECT path FROM docs')}
failed = [r[0] for r in con.execute('SELECT path FROM meta') if r[0] not in docs]

argv = sys.argv[1:]
if argv and argv[0] == '--all':
    spec = argv[1] if len(argv) > 1 else ALL_EXTS
    exts = tuple('.' + e.strip().lstrip('.').lower() for e in spec.split(',') if e.strip())
    redo = [r[0] for r in con.execute('SELECT path FROM meta')
            if r[0].lower().endswith(exts)]
    print(f'强制重跑 {len(redo)} 个文件（{", ".join(exts)}）')
else:
    redo = [p for p in failed if p.lower().endswith(RETRY_EXTS)]
    n_pdf = sum(1 for p in redo if p.lower().endswith('.pdf'))
    print(f'待补录 {len(redo)} 个（PDF {n_pdf} / 其他 {len(redo) - n_pdf}）；'
          f'.doc 与企业加密文档救不了，不在此列')

if not redo:
    print('没有需要补录的文件。')
    sys.exit(0)

for p in redo:
    con.execute('DELETE FROM meta WHERE path=?', (p,))
con.commit()
brain.index(str(Path(__file__).parent / 'scope.json'))
