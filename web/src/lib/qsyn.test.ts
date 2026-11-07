import { describe, expect, it } from 'vitest'
import { parseQSyn, qsize, synOk } from './qsyn'

describe('qsize', () => {
  it('把 10m 换成字节', () => {
    expect(qsize('10', 'm')).toBe(10 * 1024 * 1024)
    expect(qsize('1', 'k')).toBe(1024)
    expect(qsize('2', 'g')).toBe(2 * 1024 ** 3)
  })
  it('没有单位就当字节', () => {
    expect(qsize('500')).toBe(500)
  })
  it('乱写返回 null 而不是 NaN', () => {
    expect(qsize('abc')).toBeNull()
  })
})

describe('parseQSyn', () => {
  it('纯关键词原样保留', () => {
    const s = parseQSyn('项目计划')
    expect(s.q).toBe('项目计划')
    expect(s.ext).toBeNull()
    expect(s.days).toBeNull()
  })

  it('ext: 单个和多个都认', () => {
    expect(parseQSyn('合同 ext:pdf').ext).toBe('pdf')
    expect(parseQSyn('合同 ext:pdf,docx').ext).toBe('pdf,docx')
    expect(parseQSyn('合同 type:PDF').ext).toBe('pdf')
    expect(parseQSyn('合同 ext:.xlsx').ext).toBe('xlsx')
  })

  it('ext: 后面没东西时不吞掉这个词', () => {
    const s = parseQSyn('ext:')
    expect(s.ext).toBeNull()
    expect(s.q).toBe('ext:')
  })

  it('size: 三种写法', () => {
    expect(parseQSyn('a size:>10m').size).toEqual([10 * 1024 * 1024, null])
    expect(parseQSyn('a size:<500k').size).toEqual([null, 500 * 1024])
    expect(parseQSyn('a size:1k-2m').size).toEqual([1024, 2 * 1024 * 1024])
  })

  // 2026-11-07 用户实测：写 `微信 size>10m`（不带冒号）一条都搜不到 ——
  // 那段认不出来，按设计原样当关键词去匹配文件名了。现在两种写法等价。
  it('size 不带冒号也认', () => {
    for (const q of ['a size>10m', 'a size>=10m', 'a size10m']) {
      expect(parseQSyn(q).size).toEqual([10 * 1024 * 1024, null])
    }
    expect(parseQSyn('微信 size>10m').q).toBe('微信')
    expect(parseQSyn('a size<1g').size).toEqual([null, 1024 ** 3])
    expect(parseQSyn('a size5m-10m').size).toEqual([5 * 1024 * 1024, 10 * 1024 * 1024])
  })

  it('size 出现在普通单词里不被吞', () => {
    for (const w of ['filesize', 'sizechart', 'resize2', 'size']) {
      const s = parseQSyn(w)
      expect(s.q).toBe(w)
      expect(s.size).toEqual([null, null])
    }
  })

  it('dm 不带冒号也认', () => {
    expect(parseQSyn('a dm<30d').days).toBe(30)
    expect(parseQSyn('a dm7d').days).toBe(7)
    expect(parseQSyn('dmg').q).toBe('dmg')
  })

  it('size: 写坏时当普通词', () => {
    const s = parseQSyn('size:abc')
    expect(s.size).toEqual([null, null])
    expect(s.q).toBe('size:abc')
  })

  it('dm: 支持天/周/月/年', () => {
    expect(parseQSyn('x dm:7d').days).toBe(7)
    expect(parseQSyn('x dm:2w').days).toBe(14)
    expect(parseQSyn('x dm:1m').days).toBe(30)
    expect(parseQSyn('x dm:1y').days).toBe(365)
  })

  it('-排除 支持半角与全角减号', () => {
    expect(parseQSyn('报告 -草稿').excl).toEqual(['草稿'])
    expect(parseQSyn('报告 －草稿').excl).toEqual(['草稿'])
    // 单个减号不算
    expect(parseQSyn('-').q).toBe('-')
  })

  it('文件夹用尾斜杠，两种斜杠都认', () => {
    expect(parseQSyn('资料/ 笔记').folder).toBe('资料')
    expect(parseQSyn('资料\\ 笔记').folder).toBe('资料')
  })

  it('content: 只搜正文', () => {
    expect(parseQSyn('content: 合同').content).toBe(true)
  })

  it('多个语法叠加后剩下的才是关键词', () => {
    const s = parseQSyn('ext:pdf size:>1m dm:30d -草稿 合同 2024/')
    expect(s.q).toBe('合同')
    expect(s.ext).toBe('pdf')
    expect(s.days).toBe(30)
    expect(s.excl).toEqual(['草稿'])
    expect(s.folder).toBe('2024')
  })

  it('全角空格也能分词（中文输入法常见）', () => {
    const s = parseQSyn('合同\u3000ext:pdf')
    expect(s.q).toBe('合同')
    expect(s.ext).toBe('pdf')
  })
})

describe('synOk', () => {
  const now = 1_700_000_000
  it('大小区间', () => {
    const s = parseQSyn('x size:1k-2m')
    expect(synOk('C:\\a.pdf', 2000, now, s, now)).toBe(true)
    expect(synOk('C:\\a.pdf', 500, now, s, now)).toBe(false)
    expect(synOk('C:\\a.pdf', 3 * 1024 * 1024, now, s, now)).toBe(false)
  })
  it('最近 N 天', () => {
    const s = parseQSyn('x dm:7d')
    expect(synOk('C:\\a.pdf', 1, now - 86400, s, now)).toBe(true)
    expect(synOk('C:\\a.pdf', 1, now - 8 * 86400, s, now)).toBe(false)
  })
  it('排除词大小写不敏感', () => {
    const s = parseQSyn('x -Draft')
    expect(synOk('C:\\a\\DRAFT.pdf', 1, now, s, now)).toBe(false)
    expect(synOk('C:\\a\\final.pdf', 1, now, s, now)).toBe(true)
  })
  it('信息缺失时不拦（宁可多给一条）', () => {
    const s = parseQSyn('x size:>10m dm:7d')
    expect(synOk('C:\\a.pdf', null, null, s, now)).toBe(true)
  })
})
