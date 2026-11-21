import { describe, expect, it } from 'vitest'
import { baseName, dayLabel, nextSortCol, sortByCol, type SortColState, dispLen, dirName, fmtSize, fmtTime, mergeHits, midName, setTimeFormat, shortPath, sortItems, type Hit } from './hits'

describe('fmtTime（第 308 项：跟随 Windows 区域格式）', () => {
  const sec = new Date(2026, 10, 6, 0, 28).getTime() / 1000
  it('没拿到格式串时退回 ISO 式', () => {
    setTimeFormat('', '')
    expect(fmtTime(sec)).toBe('2026-11-06 00:28')
  })
  it('zh-CN：yyyy/M/d + H:mm', () => {
    setTimeFormat('yyyy/M/d', 'H:mm')
    expect(fmtTime(sec)).toBe('2026/11/6 0:28')
  })
  it('en-US：M/d/yyyy + h:mm tt', () => {
    setTimeFormat('M/d/yyyy', 'h:mm tt')
    expect(fmtTime(sec)).toBe('11/6/2026 12:28 AM')
  })
  it('de-DE：dd.MM.yyyy + HH:mm', () => {
    setTimeFormat('dd.MM.yyyy', 'HH:mm')
    expect(fmtTime(sec)).toBe('06.11.2026 00:28')
  })
  it('带引号的字面量', () => {
    setTimeFormat("yyyy'年'M'月'd'日'", 'H:mm')
    expect(fmtTime(sec)).toBe('2026年11月6日 0:28')
  })
})

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
    expect(baseName('C:\\work\\a\\b.txt')).toBe('b.txt')
    expect(baseName('C:/work/a/b.txt')).toBe('b.txt')
    expect(baseName('b.txt')).toBe('b.txt')
  })
  it('dirName 取目录', () => {
    expect(dirName('C:\\work\\a\\b.txt')).toBe('C:\\work\\a')
  })
})

/* 第 241 项：索引范围里一条路径占一整行，横向拉满、纵向堆成几百行。
   shortPath 负责「中间省略」——头留盘符、尾留真正的文件夹名。
   为什么以前没抓到：以前靠纯 CSS truncate（切尾巴），把最有用的文件夹名切掉了；
   那属于「显示不好看」，没有单测覆盖，纯函数也没有。 */
describe('shortPath（索引范围的路径中间省略）', () => {
  it('短路径不动', () => {
    expect(shortPath('X:\\work\\notes', 34)).toBe('X:\\work\\notes')
  })
  it('超长时保留盘符和末尾文件夹名（不是切掉尾巴）', () => {
    const p = 'E:\\html工具和课件【更多平价it资源shipin_vip_hc】\\html工具和课件'
    const out = shortPath(p, 34)
    expect(out.startsWith('E:')).toBe(true)
    expect(out).toContain('…')
    expect(out.endsWith('html工具和课件')).toBe(true)
    expect(dispLen(out)).toBeLessThanOrEqual(34)
  })
  it('用户真实路径：Start Menu 那条不能被切成看不出是什么', () => {
    const p = 'C:\\Users\\tester\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs'
    const out = shortPath(p, 44)
    expect(out).toContain('Start Menu')
    expect(out).toContain('Programs')
  })
  it('没有盘符的相对路径也能省略', () => {
    const out = shortPath('Users\\tester\\Documents\\项目\\需求\\说明', 24)
    expect(out).toContain('…')
    expect(out).toContain('说明')
  })
  it('正斜杠也认', () => {
    const out = shortPath('C:/Users/tester/AppData/Roaming/Microsoft/Windows/Start Menu/Programs', 40)
    expect(out).toContain('…')
    expect(out).toContain('Programs')
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
  it('name 有中文显示名时按中文名排（不按 exe 文件名）', () => {
    // 屏幕上写「微信 / 钉钉 / 飞书」，按名称排序就该是 钉钉→飞书→微信，
    // 而不是按 WeChat.exe / DingTalk.exe / Feishu.exe 的英文顺序。
    const apps = [
      { p: 'C:\\Menu\\WeChat.exe', n: 'WeChat.exe', dn: '微信', m: 1 },
      { p: 'C:\\Menu\\DingTalk.exe', n: 'DingTalk.exe', dn: '钉钉', m: 1 },
      { p: 'C:\\Menu\\Feishu.exe', n: 'Feishu.exe', dn: '飞书', m: 1 },
    ] as Hit[]
    expect(sortItems(apps, 'name').map((x) => x.dn)).toEqual(['钉钉', '飞书', '微信'])
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

  /* 第 298 项：用户截图「搜索微信，微信怎么又被顶到那么下面」——
     排序按钮停在「名称」，微信.lnk（ab=92）被 localeCompare 排到第 17 行。
     名称排序本身要生效，但这次查询命中的应用必须在最上面。 */
  it('name 排序：命中的应用钉第一，其余仍按中文名排', () => {
    const rows: Hit[] = [
      { p: 'D:\\a\\网安行业互助表.xlsx', n: '网安行业互助表.xlsx', dn: '网安行业互助表.xlsx' },
      { p: 'C:\\Menu\\微信.lnk', n: '微信.lnk', dn: '微信', ab: 92 },
      { p: 'D:\\a\\保密安全重点注意事项.pdf', n: '保密安全重点注意事项.pdf' },
    ]
    expect(sortItems(rows, 'name').map((x) => x.dn || x.n)).toEqual([
      '微信',
      '保密安全重点注意事项.pdf',
      '网安行业互助表.xlsx',
    ])
  })
  it('new / big 排序同样让应用先上', () => {
    const rows: Hit[] = [
      { p: 'D:\\新文件.txt', n: '新文件.txt', m: 999, s: 900 },
      { p: 'C:\\Menu\\微信.lnk', n: '微信.lnk', dn: '微信', m: 1, s: 1, ab: 92 },
    ]
    expect(sortItems(rows, 'new')[0].dn).toBe('微信')
    expect(sortItems(rows, 'big')[0].dn).toBe('微信')
  })
  it('没有 ab 的普通文件不会被乱插队（ab=0 不算应用）', () => {
    const rows: Hit[] = [
      { p: 'C:\\x\\a.txt', n: 'a.txt' },
      { p: 'C:\\x\\b.txt', n: 'b.txt', ab: 0 },
    ]
    expect(sortItems(rows, 'name').map((x) => x.n)).toEqual(['a.txt', 'b.txt'])
  })

  it('rel 让「常用应用」插队，哪怕 r 更差', () => {
    const rows: Hit[] = [
      { p: 'D:\微信聊天记录.txt', n: '微信聊天记录.txt', r: 0, m: 999 },
      { p: 'C:\Program Files\Tencent\WeChat\WeChat.exe', n: 'WeChat.exe', r: 1, m: 1, ab: 55 },
    ]
    expect(sortItems(rows, 'rel').map((x) => x.n)).toEqual(['WeChat.exe', '微信聊天记录.txt'])
  })
})

  /* 第 303 项：用户截图「搜索结果的排序是乱的，名称和正文乱的」。
     以前两路结果直接拼起来再整体排序：切到「名称」后，正文命中的文档会按名字
     插进文件名结果中间，屏幕上那条分隔线也被切在随机位置。
     现在固定两段，每段内部按当前排序键排好。 */
  describe('mergeHits（第 303 项）', () => {
    const nameHits: Hit[] = [
      { p: 'C:\\a\\b.txt', n: 'b.txt', m: 10 },
      { p: 'C:\\a\\a.txt', n: 'a.txt', m: 20 },
    ]
    const bodyHits: Hit[] = [
      { p: 'D:\\doc\\A报告.docx', n: 'A报告.docx', ty: 'hit', m: 1 },
      { p: 'C:\\a\\b.txt', n: 'b.txt', ty: 'hit', m: 2 },   // 与文件名路重复（同一路径）
    ]

    it('名称排序时正文命中仍排在后半段，不插进文件名结果里', () => {
      const out = mergeHits(nameHits, bodyHits, 'name')
      expect(out.map((x) => x.n)).toEqual(['a.txt', 'b.txt', 'A报告.docx'])
      expect(out.slice(0, 2).every((x) => x.ty !== 'hit')).toBe(true)
    })

    it('正文路里与文件名路重复的同一篇只留一条（带图标的在前）', () => {
      const out = mergeHits(nameHits, bodyHits, 'new')
      expect(out.length).toBe(3)
      expect(out.filter((x) => baseName(x.p) === 'b.txt').length).toBe(1)
    })

    it('两段内部各自按排序键有序：old 时文件名段升序、正文段也升序', () => {
      const out = mergeHits(nameHits, bodyHits, 'old')
      expect(out.map((x) => x.m)).toEqual([10, 20, 1])
    })
  })

  describe('名称排序的可预期性（第 303 项）', () => {
    it('标点不参与排序：书名号/省略号不会把同一批文件打散', () => {
      const rows: Hit[] = [
        { p: 'C:\\x\\《数据要素》.pdf', n: '《数据要素》.pdf' },
        { p: 'C:\\x\\安服一体化.xlsx', n: '安服一体化.xlsx' },
        { p: 'C:\\x\\保密安全.pdf', n: '保密安全.pdf' },
      ]
      expect(sortItems(rows, 'name').map((x) => x.n)).toEqual([
        '安服一体化.xlsx',
        '保密安全.pdf',
        '《数据要素》.pdf',
      ])
    })

    it('数字按数值比，2 不会排在 10 后面', () => {
      const rows: Hit[] = [
        { p: 'C:\\x\\第10章.pdf', n: '第10章.pdf' },
        { p: 'C:\\x\\第2章.pdf', n: '第2章.pdf' },
      ]
      expect(sortItems(rows, 'name').map((x) => x.n)).toEqual(['第2章.pdf', '第10章.pdf'])
    })
  })

describe('格式化', () => {
  it('fmtTime', () => {
    setTimeFormat('', '')   // 第 308 项：模块级格式串是共享状态，这里显式回落 ISO
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


describe('sortByCol（第 334 项：点列头排序）', () => {
  const rows: Hit[] = [
    { p: 'D:\\a\\b.txt', n: 'b.txt', e: 'txt', m: 100, s: 300 },
    { p: 'D:\\a\\A.docx', n: 'A.docx', e: 'docx', m: 300, s: 100 },
    { p: 'D:\\a\\文件夹', n: '文件夹', t: 'dir', ty: 'dir', m: 200, s: 0 },
  ]
  it('不点列头就原样返回（不动相关度那套）', () => {
    expect(sortByCol(rows, null)).toEqual(rows)
  })
  it('名称：中文/数字用 locale 序，可反向', () => {
    const asc = sortByCol(rows, { key: 'name', dir: 1 }).map((x) => x.n)
    const desc = sortByCol(rows, { key: 'name', dir: -1 }).map((x) => x.n)
    expect(asc).not.toEqual(desc)
    expect(asc).toHaveLength(3)
  })
  it('类型：文件夹排最前，其余按扩展名，同扩展名再按名称', () => {
    const out = sortByCol(rows, { key: 'type', dir: 1 }).map((x) => x.e || x.n)
    expect(out[0]).toBe('文件夹')
    expect(out[1]).toBe('docx')
    expect(out[2]).toBe('txt')
  })
  it('大小 / 修改时间：默认方向 + 反向都要对', () => {
    expect(sortByCol(rows, { key: 'size', dir: -1 }).map((x) => x.n)).toEqual(['b.txt', 'A.docx', '文件夹'])
    expect(sortByCol(rows, { key: 'size', dir: 1 }).map((x) => x.n)).toEqual(['文件夹', 'A.docx', 'b.txt'])
    expect(sortByCol(rows, { key: 'mtime', dir: -1 }).map((x) => x.n)).toEqual(['A.docx', '文件夹', 'b.txt'])
    expect(sortByCol(rows, { key: 'mtime', dir: 1 }).map((x) => x.n)).toEqual(['b.txt', '文件夹', 'A.docx'])
  })
  it('常用应用（ab>0）在任何列排序下都被钉在最上面', () => {
    const withApp: Hit[] = [...rows, { p: 'C:\\微信.lnk', n: '微信.lnk', e: 'lnk', m: 1, s: 1, ab: 92 }]
    expect(sortByCol(withApp, { key: 'mtime', dir: 1 })[0].n).toBe('微信.lnk')
    expect(sortByCol(withApp, { key: 'size', dir: 1 })[0].n).toBe('微信.lnk')
  })
  it('nextSortCol：同一列翻方向，换列用默认方向', () => {
    expect(nextSortCol(null, 'name')).toEqual({ key: 'name', dir: 1 })
    expect(nextSortCol({ key: 'name', dir: 1 } as SortColState, 'name')).toEqual({ key: 'name', dir: -1 })
    expect(nextSortCol({ key: 'name', dir: -1 } as SortColState, 'name')).toEqual({ key: 'name', dir: 1 })
    expect(nextSortCol({ key: 'size', dir: -1 } as SortColState, 'mtime')).toEqual({ key: 'mtime', dir: -1 })
    expect(nextSortCol({ key: 'size', dir: 1 } as SortColState, 'type')).toEqual({ key: 'type', dir: 1 })
  })
})

describe('mergeHits + 列头排序（第 335 项：分段排，不串段）', () => {
  const nameHits: Hit[] = [
    { p: 'D:\\a\\big.txt', n: 'big.txt', e: 'txt', s: 9000, m: 100 },
    { p: 'D:\\a\\small.txt', n: 'small.txt', e: 'txt', s: 10, m: 200 },
  ]
  const bodyHits: Hit[] = [
    { p: 'D:\\a\\mid.md', n: 'mid.md', e: 'md', s: 500, m: 300, ty: 'hit' },
    { p: 'D:\\a\\tiny.py', n: 'tiny.py', e: 'py', s: 1, m: 50, ty: 'hit' },
  ]
  it('按大小升序时，正文命中也不会插到文件名命中前面', () => {
    const out = mergeHits(nameHits, bodyHits, 'rel', { key: 'size', dir: 1 })
    expect(out.map((x) => x.n)).toEqual(['small.txt', 'big.txt', 'tiny.py', 'mid.md'])
    // 前两段全是文件名命中，后两段全是正文命中 —— 分隔线只可能出现一次
    const firstHit = out.findIndex((x) => x.ty === 'hit')
    expect(out.slice(firstHit).every((x) => x.ty === 'hit')).toBe(true)
  })
  it('不传 sortCol 时行为与以前一致（各自按 sort 排）', () => {
    const out = mergeHits(nameHits, bodyHits, 'big')
    expect(out[0].n).toBe('big.txt')
    expect(out[2].n).toBe('mid.md')
  })
})
