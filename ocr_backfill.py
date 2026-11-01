# -*- coding: utf-8 -*-
"""补录：把之前提不出文本的 PDF / txt 重新处理（现在有 OCR 兜底和编码兜底了）

用法：双击没意义，维护会话里运行 `python ocr_backfill.py`
- 原理：失败文件只记在 meta 不在 docs，这里把它们从 meta 移除，再调 brain.index
  重新走一遍（其余已入库文件都在 meta 里，会被秒跳过）
- 可反复运行：已成功的不会再动；仍然失败的（加密、空文件）自动记回失败清单
"""
from pathlib import Path

import brain

con = brain.init_db()
docs = {r[0] for r in con.execute('SELECT path FROM docs')}
targets = [r[0] for r in con.execute('SELECT path FROM meta') if r[0] not in docs]
redo = [p for p in targets if p.lower().endswith(('.pdf', '.txt'))]
n_pdf = sum(1 for p in redo if p.lower().endswith('.pdf'))
print(f'待补录 {len(redo)} 个（PDF {n_pdf} / txt {len(redo) - n_pdf}）；'
      f'.doc 与企业加密文档救不了，不在此列')
for p in redo:
    con.execute('DELETE FROM meta WHERE path=?', (p,))
con.commit()
brain.index(str(Path(__file__).parent / 'scope.json'))
