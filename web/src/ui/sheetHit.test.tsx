/**
 * 第 401 项：多表 xlsx 的命中定位。
 *
 * 用户截图：搜「公安」命中 `网安行业互助表.xlsx`（14MB、43 张表），
 * 详情里只铺第 1 张表的前 25 行 × 14 列 —— 命中实际在第 16 行第 25 列，
 * 页面上一片空白，用户问「这个表在详情页看不到命中的，他是多表结构」。
 */
import { describe, expect, it } from 'vitest'
import { findSheetHit } from './panels'
import type { SheetTable } from './docview'

const sheet = (name: string, rows: string[][]): SheetTable => {
  const cols = rows.reduce((m, r) => Math.max(m, r.length), 0)
  return { name, rows, cols, cut: false }
}

describe('findSheetHit（第 401 项）', () => {
  it('命中在第 2 张表的靠后行/列也要找到', () => {
    const sheets = [
      sheet('封面', [['标题', ''], ['说明', '']]),
      sheet('互助表', [['姓名', '公司'], ['张三', '某公司'], ['李四', '可曾听闻大专二年级在校远程办公安恒正式员工']]),
    ]
    const h = findSheetHit(sheets, '公安', '')
    expect(h.si).toBe(1)
    expect(h.ri).toBe(2)
    expect(h.ci).toBe(1)
  })

  it('有「命中的正文」片段时优先按片段整行对齐（哪怕别处也有这个词）', () => {
    const sheets = [
      sheet('A', [['公安三所', 'x']]),
      sheet('B', [['前言'], ['abc 公安 def']]),
    ]
    const h = findSheetHit(sheets, '公安', 'abc公安def')
    expect(h.si).toBe(1)
    expect(h.ri).toBe(1)
  })

  it('多关键词任一命中即可', () => {
    const sheets = [sheet('A', [['无关'], ['网络安全 保险方案']])]
    expect(findSheetHit(sheets, '网络安全 保险 找不到的词', '')).toEqual({ si: 0, ri: 1, ci: 0 })
  })

  it('一个都没有时回第 1 张表、ri=-1（前端照常从头铺）', () => {
    expect(findSheetHit([sheet('A', [['abc']])], '不存在的词', '')).toEqual({ si: 0, ri: -1, ci: -1 })
    expect(findSheetHit([], '公安', '')).toEqual({ si: 0, ri: -1, ci: -1 })
  })

  it('大小写与空白不敏感', () => {
    const sheets = [sheet('A', [['Sangfor Technologies']])]
    expect(findSheetHit(sheets, 'sangFOR', '').ri).toBe(0)
  })
})
