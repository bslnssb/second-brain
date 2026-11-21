/** 结果行、排序、时间格式化 —— 都是纯函数，配了单测。 */

export type SortKey = 'rel' | 'new' | 'old' | 'big' | 'name'
export const SORT_KEYS: SortKey[] = ['rel', 'new', 'old', 'big', 'name']

export type Hit = {
  /** 完整路径 */
  p: string
  /** 文件名 */
  n: string
  /** 类型：file / dir / hit（正文命中）——后端在 /api/files 里叫 t，在 /api 里叫 ty */
  ty?: string
  t?: string
  /** 扩展名（小写，不带点） */
  e?: string
  /** 修改时间，秒 */
  m?: number
  /** 大小，字节 */
  s?: number
  /** 正文片段（已带 <mark>？——这里一律当纯文本，渲染时再高亮） */
  snip?: string
  /** 相关度分数（后端给，越小越靠前） */
  r?: number
  /** 命中「常用应用」白名单的强度（0~100，微信→WeChat.exe 是 55），越大越靠前 */
  ab?: number
  /** 显示名：应用的中文名（WeChat.exe → 微信）。后端给，没有就退回文件名 */
  dn?: string
}

/** 半角宽度：中文/全角算 2，其它算 1。列宽是按像素算的，按字数是第二次踩坑。 */
export function dispLen(s: string): number {
  let n = 0
  for (const ch of s) {
    const c = ch.codePointAt(0) ?? 0
    n += c > 0x2e80 && c < 0xffef ? 2 : 1
  }
  return n
}

/** 中间省略：按「半角宽度」截断并保留扩展名。36 ≈ 列表里那一列能放下的像素宽。 */
export function midName(name: string, max = 36): string {
  const s = String(name ?? '')
  if (dispLen(s) <= max) return s
  const dot = s.lastIndexOf('.')
  const ext = dot > 0 && s.length - dot <= 6 ? s.slice(dot) : ''
  const keep = max - dispLen(ext) - 1
  if (keep < 8) return s.slice(0, Math.max(1, max - 1)) + '…'
  const headBudget = Math.ceil(keep * 0.62)
  const tailBudget = keep - headBudget
  let head = ''
  for (const ch of s) {
    if (dispLen(head + ch) > headBudget) break
    head += ch
  }
  let tail = ''
  for (let i = s.length - ext.length - 1; i >= 0; i--) {
    const ch = s[i]
    if (dispLen(ch + tail) > tailBudget) break
    tail = ch + tail
  }
  return head + '…' + tail + ext
}

/** 路径太长时中间省略：保留盘符 + 末尾的目录名。
 *  第 241 项：索引范围里一条路径占一整行，横向被拉满、纵向堆成几百行。
 *  直接 truncate 会把最有用的那一段（真正的文件夹名）切掉 ——
 *  「E:\html工具和课件【…】\html工具和课件」被切成「E:\html工具和课件【…」，反而看不出是哪个。
 *  所以中间省略：头留盘符、尾留末两段。完整路径靠 title 提示。 */
export function shortPath(path: string, max = 34): string {
  const s = String(path ?? '')
  if (dispLen(s) <= max) return s
  const m = /^([A-Za-z]:)?[\\/]?/.exec(s)
  const drive = m && m[1] ? m[1] : ''
  const sep = s.includes('\\') ? '\\' : '/'
  const headLen = m ? m[0].length : 0
  const parts = s.slice(headLen).split(/[\\/]+/).filter(Boolean)
  if (!parts.length) return midName(s, max)
  const head = drive ? drive + sep : ''
  const budget = max - dispLen(head) - 1 // 1 = 中间的省略号
  if (budget < 4) return midName(s, max)
  // 从后往前塞段，末段（真正的文件夹名）优先保留
  const tail: string[] = []
  let used = 0
  for (let i = parts.length - 1; i >= 0; i--) {
    const w = dispLen(parts[i]) + (tail.length ? 1 : 0)
    if (used + w > budget) break
    tail.unshift(parts[i])
    used += w
  }
  if (!tail.length) return midName(s, max)
  return head + '…' + sep + tail.join(sep)
}

/** 排序只动已经拿到的一屏结果，不重查数据库。 */
export function sortItems<T extends Hit>(items: T[], sort: SortKey | string = 'rel'): T[] {
  const s = String(sort || 'rel').toLowerCase()
  const copy = items.slice()
  // 第 298 项：手选的排序（名称/时间/大小）不能把「这次搜索真正要的那个程序」埋掉。
  // 以前只有 rel 分支看 ab：搜「微信」切到「按名称」，微信.lnk 会被 locoCompare 排到
  // 第 17 行（ab=92 白算了）—— ab>0 表示后端按查询命中了常用应用白名单，
  // 这类行在任何排序模式下都先钉在最上面，其余仍在各自模式内部有序。
  const appFirst = (a: T, b: T) => (Number((b.ab ?? 0) > 0) - Number((a.ab ?? 0) > 0))
  if (s === 'rel' || s === '' || s === 'rel ') {
    // 常用应用先上：搜「微信」时先给微信本体，其余仍按后端相关度排
    copy.sort(
      (a, b) =>
        (b.ab ?? 0) - (a.ab ?? 0) ||
        (a.r ?? 3) - (b.r ?? 3) ||
        (b.m ?? 0) - (a.m ?? 0),
    )
  } else if (s === 'new') copy.sort((a, b) => appFirst(a, b) || (b.m ?? 0) - (a.m ?? 0))
  else if (s === 'old') copy.sort((a, b) => appFirst(a, b) || (a.m ?? 0) - (b.m ?? 0))
  else if (s === 'big') copy.sort((a, b) => appFirst(a, b) || (b.s ?? 0) - (a.s ?? 0))
  else if (s === 'name')
    // 屏幕上写的是中文名（微信），排序就不能按英文文件名（Weixin.exe）——
    // 否则「按名称」看起来是乱序。dn 是后端给的中文显示名，没有才退回文件名。
    //
    // 第 303 项（二）：直接 localeCompare(name, 'zh') 有两个坑：
    // ① 书名号《》、省略号 …、下划线这些标点参与了权重，同一批文件名排出来
    //    前后不连贯（实测「安服…」被排到 8f7c… 后面）；
    // ② 纯数字段按字符比，2 排在 10 后面。
    // 所以先把标点全部去掉再比，并打开 numeric —— ICU 的 zh 拼音序这时才稳定。
    copy.sort((a, b) => appFirst(a, b) || NAME_COLLATOR.compare(nameKey(a), nameKey(b)))
  return copy
}

const NAME_COLLATOR = new Intl.Collator('zh-Hans', { numeric: true, sensitivity: 'base' })

function nameKey(x: Hit): string {
  // 只留字母、数字、汉字：标点（《》…_-【】等）一律不参与排序
  return String(x.dn || baseName(x.n || x.p || ''))
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '')
}

/** 第 334 项：点列头排序（名称 / 类型 / 大小 / 修改时间）。 */
export type SortCol = 'name' | 'type' | 'size' | 'mtime'
export type SortDir = 1 | -1
export type SortColState = { key: SortCol; dir: SortDir }

/** 各列第一次点下去的方向：名称/类型顺着排，大小/时间从大到近排（用户预期）。 */
export const SORT_COL_DEFAULT: Record<SortCol, SortDir> = { name: 1, type: 1, size: -1, mtime: -1 }

/** 点同一列 = 反向；点另一列 = 换成它并按默认方向。 */
export function nextSortCol(cur: SortColState | null, key: SortCol): SortColState {
  if (cur && cur.key === key) return { key, dir: (cur.dir === 1 ? -1 : 1) as SortDir }
  return { key, dir: SORT_COL_DEFAULT[key] }
}

/** 类型的排序键：文件夹排在一起，其余按扩展名（同扩展名再按名称）。 */
function typeKey(x: Hit): string {
  if (x.t === 'dir' || x.ty === 'dir') return '0folder'
  return '1' + String(x.e || '').replace(/^\./, '').toLowerCase()
}

/** 按列排序（纯客户端，动的是已经拿到的一屏结果）。 */
export function sortByCol<T extends Hit>(items: T[], st: SortColState | null): T[] {
  if (!st) return items
  const copy = items.slice()
  const d = st.dir
  const appFirst = (a: T, b: T) => (Number((b.ab ?? 0) > 0) - Number((a.ab ?? 0) > 0))
  if (st.key === 'name') {
    copy.sort((a, b) => appFirst(a, b) || d * NAME_COLLATOR.compare(nameKey(a), nameKey(b)))
  } else if (st.key === 'type') {
    copy.sort(
      (a, b) =>
        appFirst(a, b) ||
        d * typeKey(a).localeCompare(typeKey(b)) ||
        NAME_COLLATOR.compare(nameKey(a), nameKey(b)),
    )
  } else if (st.key === 'size') {
    copy.sort((a, b) => appFirst(a, b) || d * ((a.s ?? 0) - (b.s ?? 0)))
  } else {
    copy.sort((a, b) => appFirst(a, b) || d * ((a.m ?? 0) - (b.m ?? 0)))
  }
  return copy
}

export function baseName(pathOrName: string): string {

  const s = String(pathOrName ?? '')
  const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'))
  return i >= 0 ? s.slice(i + 1) : s
}

/**
 * 第 303 项：合并「文件名命中」与「正文命中」两路结果。
 *
 * 以前是把两路直接拼起来、再对**整串**调 sortItems：切到「名称」后正文命中的文档
 * 会按名字插进文件名结果中间，屏幕上那条「以下 N 条命中在文档正文里」的分隔线
 * 也被切在随机位置 —— 用户看到的就是「名称和正文乱的」。
 * 现在固定两段：先文件名命中（按当前排序键排好），再正文命中（同样排好），
 * 分隔线永远落在两段之间。正文路里如果某篇已经在文件名路出现（同一个文件），
 * 只保留前面那条，避免一行带图标一行带「正文」标签。
 */
export function mergeHits<T extends Hit>(nameHits: T[], bodyHits: T[], sort: SortKey | string = 'rel', sortCol: SortColState | null = null): T[] {
  const seen = new Set<string>()
  const head: T[] = []
  const tail: T[] = []
  for (const r of nameHits) {
    const k = String(r.p ?? '').replace(/\//g, '\\').toLowerCase()
    if (seen.has(k)) continue
    seen.add(k)
    head.push(r)
  }
  for (const r of bodyHits) {
    const k = String(r.p ?? '').replace(/\//g, '\\').toLowerCase()
    if (seen.has(k)) continue
    seen.add(k)
    tail.push(r)
  }
  return [...sortByCol(sortItems(head, sort), sortCol), ...sortByCol(sortItems(tail, sort), sortCol)]
}

/** 目录部分（用于「路径」那一列） */
export function dirName(path: string): string {
  const s = String(path ?? '')
  const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'))
  return i > 0 ? s.slice(0, i) : ''
}

/** Windows 区域格式（第 308 项）：后端 /api/locale 下发，空串表示没拿到 */
let DATE_FMT = ''
let TIME_FMT = ''

/** 设置系统日期/时间格式串（Windows 的 LOCALE_SSHORTDATE / LOCALE_SSHORTTIME） */
export function setTimeFormat(date: string, time: string): void {
  DATE_FMT = date || ''
  TIME_FMT = time || ''
}

/** 按 Windows 格式串渲染：yyyy/M/d、dd.MM.yyyy、h:mm tt 这些 */
function fmtPattern(d: Date, pat: string): string {
  if (!pat) return ''
  const p2 = (n: number) => String(n).padStart(2, '0')
  const h24 = d.getHours()
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12
  let out = ''
  let i = 0
  let literal = false
  while (i < pat.length) {
    const ch = pat[i]
    if (ch === "'") {
      literal = !literal
      i++
      continue
    }
    // 同一个字母连写算一个 token（yyyy / MM / dd / tt）
    let j = i + 1
    while (!literal && j < pat.length && pat[j] === ch) j++
    const tok = pat.slice(i, j)
    if (literal) {
      out += tok
    } else if (ch === 'y') {
      out += tok.length >= 4 ? String(d.getFullYear()) : tok.length === 2 ? p2(d.getFullYear() % 100) : String(d.getFullYear())
    } else if (ch === 'M') {
      out += tok.length >= 2 ? p2(d.getMonth() + 1) : String(d.getMonth() + 1)
    } else if (ch === 'd') {
      out += tok.length >= 2 ? p2(d.getDate()) : String(d.getDate())
    } else if (ch === 'H') {
      out += tok.length >= 2 ? p2(h24) : String(h24)
    } else if (ch === 'h') {
      out += tok.length >= 2 ? p2(h12) : String(h12)
    } else if (ch === 'm') {
      out += tok.length >= 2 ? p2(d.getMinutes()) : String(d.getMinutes())
    } else if (ch === 's') {
      out += tok.length >= 2 ? p2(d.getSeconds()) : String(d.getSeconds())
    } else if (ch === 't') {
      out += h24 < 12 ? 'AM' : 'PM'
    } else {
      out += tok
    }
    i = j
  }
  return out
}

/** 2026-11-06 00:28 —— 列表里的修改时间（跟随 Windows 区域格式；没拿到就用 ISO 式） */
export function fmtTime(sec?: number | null): string {
  if (!sec) return ''
  const d = new Date(sec * 1000)
  if (Number.isNaN(d.getTime())) return ''
  const p2 = (n: number) => String(n).padStart(2, '0')
  const iso = `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`
  if (!DATE_FMT && !TIME_FMT) return iso
  const date = fmtPattern(d, DATE_FMT) || `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
  const time = fmtPattern(d, TIME_FMT) || `${p2(d.getHours())}:${p2(d.getMinutes())}`
  return `${date} ${time}`
}

/** 时间轴分组标题：今天 / 昨天 / 11月3日 / 2025年6月 */
export function dayLabel(sec: number, nowSec = Date.now() / 1000, lang = 'zh'): string {
  const d = new Date(sec * 1000)
  const now = new Date(nowSec * 1000)
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diffDays = Math.round((midnight(now) - midnight(d)) / 86400000)
  if (lang === 'zh') {
    if (diffDays === 0) return '今天'
    if (diffDays === 1) return '昨天'
    if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`
    return `${d.getFullYear()}年${d.getMonth() + 1}月`
  }
  // 其它语言用系统日期格式：今天/昨天是特殊词，其余交给 Intl
  if (diffDays === 0) return lang === 'es' ? 'Hoy' : lang === 'pt' ? 'Hoje' : lang === 'hi' ? 'आज' : 'Today'
  if (diffDays === 1)
    return lang === 'es' ? 'Ayer' : lang === 'pt' ? 'Ontem' : lang === 'hi' ? 'कल' : 'Yesterday'
  const sameYear = d.getFullYear() === now.getFullYear()
  return d.toLocaleDateString(lang === 'hi' ? 'hi-IN' : lang === 'pt' ? 'pt-BR' : lang === 'es' ? 'es-ES' : 'en-US', {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
}

/** 1024 → 1.0 KB（和资源管理器口径一致，用 1024） */
export function fmtSize(bytes?: number | null): string {
  if (!bytes || bytes < 0) return ''
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return `${i === 0 ? v : v.toFixed(v >= 10 ? 0 : 1)} ${u[i]}`
}
