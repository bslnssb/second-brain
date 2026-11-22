/** 第 405 项：CSV/TSV 详情预览（以前整块不出来）+ pptx 幻灯片文字当主预览。 */
import { describe, expect, it } from 'vitest'
import { canInline, parseDelimited } from './docview'

describe('parseDelimited', () => {
  it('普通逗号行', () => {
    expect(parseDelimited('a,b\n1,2\n', ',')).toEqual([
      ['a', 'b'],
      ['1', '2'],
    ])
  })

  it('引号里的逗号与换行不切', () => {
    expect(parseDelimited('a,"b,c"\n"x\ny",z', ',')).toEqual([
      ['a', 'b,c'],
      ['x\ny', 'z'],
    ])
  })

  it('双引号转义', () => {
    expect(parseDelimited('"她说""好""",b', ',')).toEqual([['她说"好"', 'b']])
  })

  it('制表符分隔', () => {
    expect(parseDelimited('a\tb\n1\t2', '\t')).toEqual([
      ['a', 'b'],
      ['1', '2'],
    ])
  })
})

describe('canInline', () => {
  it('csv / tsv 也算可内嵌预览', () => {
    expect(canInline('csv')).toBe(true)
    expect(canInline('.tsv')).toBe(true)
    expect(canInline('pptx')).toBe(true)
  })
})
