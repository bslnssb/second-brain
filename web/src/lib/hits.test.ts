import { describe, expect, it } from 'vitest'
import { baseName, dayLabel, dispLen, dirName, fmtSize, fmtTime, midName, sortItems, type Hit } from './hits'

describe('midName', () => {
  it('短名字不动', () => {
    expect(midName('简历.docx', 36)).toBe('简历.docx')
  })
  it('长名字中间省略并保留扩展名', () => {
    const name = '2026年度第二大脑项目周报-第三版-最终定稿-含附件说明.xlsx'
    const out = midName(name, 36)
    expect(dispLen(out)).toBeLessThanOrEqual(36)
    expect(out).toContain('…')
    expect(out.endsWith('.xlsx')).toBe(true)
    expect(out.startsWith('2026年度')).toBe(true)
  })
  it('UUID 式长名：头尾保留，中间不同就会被省略（完整名字靠 tooltip）', () => {
    const a = 'ab5c6ce7e8178c6b8ac88c9c8d55862f_54934233c965076e0e1e54bee738022c_8.pdf'
    const out = midName(a, 36)
    expect(dispLen(out)).toBeLessThanOrEqual(36)
    expect(out.startsWith('ab5c6ce7')).toBe(true)
    expect(out.endsWith('_8.pdf')).toBe(true)
  })
  it('中文按两倍宽算：32 个汉字会省略，16 个汉字不会', () => {
    const long = '第二大脑项目周报第三版最终定稿含附件说明补充材料附录甲乙丙丁戊己庚'
    expect(dispLen(long)).toBe(long.length * 2)
    expect(midName(long, 36)).toContain('…')
    expect(midName('第二大脑项目周报', 36)).toBe('第二大脑项目周报')
  })
  it('没有扩展名时直接截断', () => {
    const out = midName('a'.repeat(50), 12)
    expect(out.length).toBeLessThanOrEqual(12)
    expect(out).toContain('…')
  })
})

describe('路径工具', () => {
  it('baseName 支持两种斜杠', () => {
    expect(baseName('D:\\1\\a\\b.txt')).toBe('b.txt')
    expect(baseName('D:/1/a/b.txt')).toBe('b.txt')
    expect(baseName('b.txt')).toBe('b.txt')
  })
  it('dirName 取目录', () => {
    expect(dirName('D:\\1\\a\\b.txt')).toBe('D:\\1\\a')
  })
})

describe('sortItems', () => {
  const items: Hit[] = [
    { p: 'C:\\a\\b.txt', n: 'b.txt', m: 100, s: 5 },
    { p: 'C:\\a\\a.txt', n: 'a.txt', m: 300, s: 1 },
    { p: 'C:\\a\\c.txt', n: 'c.txt', m: 200, s: 9 },
  ]
  it('new / old 按时间', () => {
    expect(sortItems(items, 'new').map((x) => x.n)).toEqual(['a.txt', 'c.txt', 'b.txt'])
    expect(sortItems(items, 'old').map((x) => x.n)).toEqual(['b.txt', 'c.txt', 'a.txt'])
  })
  it('big 按大小', () => {
    expect(sortItems(items, 'big').map((x) => x.n)).toEqual(['c.txt', 'b.txt', 'a.txt'])
  })
  it('name 按文件名', () => {
    expect(sortItems(items, 'name').map((x) => x.n)).toEqual(['a.txt', 'b.txt', 'c.txt'])
  })
  it('rel 按后端相关度 r 排（同分才看时间），不改原数组', () => {
    const before = items.map((x) => x.n)
    const ranked: Hit[] = [
      { p: 'C:\a\b.txt', n: 'b.txt', m: 100, r: 2 },
      { p: 'C:\a\a.txt', n: 'a.txt', m: 300, r: 0 },
      { p: 'C:\a\c.txt', n: 'c.txt', m: 200, r: 0 },
    ]
    expect(sortItems(ranked, 'rel').map((x) => x.n)).toEqual(['a.txt', 'c.txt', 'b.txt'])
    expect(items.map((x) => x.n)).toEqual(before)
  })

  it('rel 让「常用应用」插队，哪怕 r 更差', () => {
    const rows: Hit[] = [
      { p: 'D:\微信聊天记录.txt', n: '微信聊天记录.txt', r: 0, m: 999 },
      { p: 'C:\Program Files\Tencent\WeChat\WeChat.exe', n: 'WeChat.exe', r: 1, m: 1, ab: 55 },
    ]
    expect(sortItems(rows, 'rel').map((x) => x.n)).toEqual(['WeChat.exe', '微信聊天记录.txt'])
  })
})

describe('格式化', () => {
  it('fmtTime', () => {
    const sec = new Date(2026, 10, 6, 9, 5).getTime() / 1000
    expect(fmtTime(sec)).toBe('2026-11-06 09:05')
    expect(fmtTime(0)).toBe('')
  })
  it('dayLabel 今天/昨天/月日/年月', () => {
    const now = new Date(2026, 10, 6, 12, 0).getTime() / 1000
    expect(dayLabel(new Date(2026, 10, 6, 1, 0).getTime() / 1000, now)).toBe('今天')
    expect(dayLabel(new Date(2026, 10, 5, 23, 0).getTime() / 1000, now)).toBe('昨天')
    expect(dayLabel(new Date(2026, 8, 3, 10, 0).getTime() / 1000, now)).toBe('9月3日')
    expect(dayLabel(new Date(2025, 5, 1).getTime() / 1000, now)).toBe('2025年6月')
  })
  it('fmtSize 用 1024 进制', () => {
    expect(fmtSize(0)).toBe('')
    expect(fmtSize(512)).toBe('512 B')
    expect(fmtSize(2048)).toBe('2.0 KB')
    expect(fmtSize(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})
