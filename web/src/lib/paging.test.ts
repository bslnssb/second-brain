import { describe, expect, it } from 'vitest'
import { resolveStep, type PageBox } from './paging'

const box = (x: Partial<PageBox>): PageBox => ({
  isPdf: false,
  page: 1,
  pages: 0,
  textPage: 1,
  textPages: 0,
  ...x,
})

describe('resolveStep（第 332 项：先翻页，翻到头才换文件）', () => {
  it('PDF 第 1 页按 → 去第 2 页，不换文件', () => {
    expect(resolveStep(box({ isPdf: true, page: 1, pages: 36 }), 1)).toEqual({ to: 'page', page: 2 })
  })

  it('PDF 最后一页按 → 才换文件', () => {
    expect(resolveStep(box({ isPdf: true, page: 36, pages: 36 }), 1)).toEqual({ to: 'file' })
  })

  it('PDF 第 1 页按 ← 换文件（上一份），第 2 页按 ← 回第 1 页', () => {
    expect(resolveStep(box({ isPdf: true, page: 1, pages: 36 }), -1)).toEqual({ to: 'file' })
    expect(resolveStep(box({ isPdf: true, page: 2, pages: 36 }), -1)).toEqual({ to: 'page', page: 1 })
  })

  it('只有一页的 PDF 直接换文件', () => {
    expect(resolveStep(box({ isPdf: true, page: 1, pages: 1 }), 1)).toEqual({ to: 'file' })
  })

  it('Word 这类正文分块的文档同样先翻块', () => {
    expect(resolveStep(box({ textPage: 1, textPages: 5 }), 1)).toEqual({ to: 'textPage', textPage: 2 })
    expect(resolveStep(box({ textPage: 5, textPages: 5 }), 1)).toEqual({ to: 'file' })
    expect(resolveStep(box({ textPage: 1, textPages: 5 }), -1)).toEqual({ to: 'file' })
  })

  it('图片（没有页概念）直接换', () => {
    expect(resolveStep(box({}), 1)).toEqual({ to: 'file' })
  })
})
