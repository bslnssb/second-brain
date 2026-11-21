/**
 * 详情灯箱里 ← → 的落点规则（第 332 项）。
 *
 * 用户原话：「点击→他会跳转到下一个文件，而不是下一页。因为很多 pdf 或者 word 是有
 * 多页的，所以需要点击→的时候下一页。如果最后一页也看完了→才是下一份文件。」
 *
 * 两种「页」：
 *  · PDF —— 真页面（/api/pdfmeta 给总页数，后端按页渲染图）；
 *  · Word / Excel / PPT / txt / md —— 没有分页图，按正文切块当作「页」（/api/peek?pg=N）。
 *
 * 抽成纯函数是为了能打表测：翻到头才换文件、往回翻同理、单页文件直接换文件。
 */
export type PageBox = {
  /** 当前文件是 PDF（有真页码） */
  isPdf: boolean
  /** PDF 当前页 / 总页数（1 基） */
  page: number
  pages: number
  /** 文本类文档的正文分块（1 基） */
  textPage: number
  textPages: number
}

export type StepResult =
  | { to: 'page'; page: number }
  | { to: 'textPage'; textPage: number }
  | { to: 'file' }

/** d = +1 下一张 / -1 上一张 */
export function resolveStep(box: PageBox, d: number): StepResult {
  if (box.isPdf && box.pages > 1) {
    const nxt = box.page + d
    if (nxt >= 1 && nxt <= box.pages) return { to: 'page', page: nxt }
    return { to: 'file' }
  }
  if (!box.isPdf && box.textPages > 1) {
    const nxt = box.textPage + d
    if (nxt >= 1 && nxt <= box.textPages) return { to: 'textPage', textPage: nxt }
    return { to: 'file' }
  }
  return { to: 'file' }
}
