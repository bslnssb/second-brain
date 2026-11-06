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

/** 排序只动已经拿到的一屏结果，不重查数据库。 */
export function sortItems<T extends Hit>(items: T[], sort: SortKey | string = 'rel'): T[] {
  const s = String(sort || 'rel').toLowerCase()
  const copy = items.slice()
  if (s === 'rel' || s === '' || s === 'rel ') {
    // 常用应用先上：搜「微信」时先给微信本体，其余仍按后端相关度排
    copy.sort(
      (a, b) =>
        (b.ab ?? 0) - (a.ab ?? 0) ||
        (a.r ?? 3) - (b.r ?? 3) ||
        (b.m ?? 0) - (a.m ?? 0),
    )
  } else if (s === 'new') copy.sort((a, b) => (b.m ?? 0) - (a.m ?? 0))
  else if (s === 'old') copy.sort((a, b) => (a.m ?? 0) - (b.m ?? 0))
  else if (s === 'big') copy.sort((a, b) => (b.s ?? 0) - (a.s ?? 0))
  else if (s === 'name')
    copy.sort((a, b) => baseName(a.p || a.n).toLowerCase().localeCompare(baseName(b.p || b.n).toLowerCase()))
  return copy
}

export function baseName(pathOrName: string): string {
  const s = String(pathOrName ?? '')
  const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'))
  return i >= 0 ? s.slice(i + 1) : s
}

/** 目录部分（用于「路径」那一列） */
export function dirName(path: string): string {
  const s = String(path ?? '')
  const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'))
  return i > 0 ? s.slice(0, i) : ''
}

/** 2026-11-06 00:28 —— 列表里的修改时间 */
export function fmtTime(sec?: number | null): string {
  if (!sec) return ''
  const d = new Date(sec * 1000)
  if (Number.isNaN(d.getTime())) return ''
  const p2 = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`
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
