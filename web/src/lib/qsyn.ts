/**
 * 搜索框语法的解析与过滤 —— 从 brain.py 的 parse_qsyn / syn_ok 原样搬过来。
 *
 * 为什么单独成文件：这是全项目最值得写单测的一段逻辑（边角情况多、出错了用户只会
 * 觉得"搜不到"），而且和界面无关、和 Python 也无关，纯函数最好测。
 */

export type QSyn = {
  /** 剥掉语法之后真正的关键词 */
  q: string
  /** 逗号分隔的扩展名，如 "pdf,docx"；没写就是 null */
  ext: string | null
  /** 字节大小区间，[lo, hi)，null 表示不限 */
  size: [number | null, number | null]
  /** 只要最近 N 天改过的 */
  days: number | null
  /** -排除 的词（小写） */
  excl: string[]
  /** 以 / 结尾的文件夹名 */
  folder: string | null
  /** content: 只搜正文，不要文件名凑数 */
  content: boolean
}

const SU: Record<string, number> = { k: 1024, m: 1024 ** 2, g: 1024 ** 3, t: 1024 ** 4 }

export function qsize(num: string, unit = ''): number | null {
  const v = Number(num)
  if (!Number.isFinite(v)) return null
  return Math.trunc(v * (SU[unit.toLowerCase()] ?? 1))
}

export function parseQSyn(raw: string): QSyn {
  const syn: QSyn = {
    q: String(raw ?? '').trim(),
    ext: null,
    size: [null, null],
    days: null,
    excl: [],
    folder: null,
    content: false,
  }
  const rest: string[] = []
  // 用 [ \t\u3000] 切：中文输入法常带全角空格，JS 的 split(' ') 会把它留在词里
  for (let tok of String(raw ?? '').split(/[ \t\u3000]+/)) {
    if (!tok) continue
    let low = tok.toLowerCase()

    // 不带冒号的写法也认：size>10m / size>=1g / size10m / dm<30d。
    // 用户第一反应就是写 `size>10m`，不认的话这段会掉进关键词、结果一条都没有。
    // 判据收紧到「前缀后面紧跟运算符或数字」，免得把 resize2、dmg 这类词吞成语法。
    // 必须和 brain.parse_qsyn 保持一致，否则提示条和后端行为会打架。
    if (/^size[<>=0-9.]/.test(low)) {
      tok = 'size:' + tok.slice(4)
      low = tok.toLowerCase()
    } else if (/^dm[<>=0-9]/.test(low)) {
      tok = 'dm:' + tok.slice(2)
      low = tok.toLowerCase()
    }

    // -排除（全角减号也认）
    if (tok.length > 1 && (low[0] === '-' || low[0] === '－')) {
      syn.excl.push(low.slice(1))
      continue
    }
    if (low === 'content:') {
      syn.content = true
      continue
    }
    if (low.startsWith('ext:') || low.startsWith('type:')) {
      const v = low.split(':').slice(1).join(':')
      const exts = v
        .split(/[,\s]+/)
        .map((e) => e.trim().replace(/^\.+/, ''))
        .filter(Boolean)
      if (exts.length) {
        syn.ext = exts.join(',')
        continue
      }
    }
    if (low.startsWith('size:')) {
      const m = /^size:(>=|<=|>|<)?([0-9.]+)([kmgt]?)b?(?:-([0-9.]+)([kmgt]?)b?)?$/.exec(low)
      if (m) {
        const [, op, n1, u1, n2, u2] = m
        const a = qsize(n1, u1)
        const b = n2 ? qsize(n2, u2) : null
        if (op === '>' || op === '>=') syn.size = [a, null]
        else if (op === '<' || op === '<=') syn.size = [null, a]
        else if (b != null) syn.size = [a, b]
        else syn.size = [a, null]
        continue
      }
    }
    if (low.startsWith('dm:')) {
      const m = /^dm:(>=|<=|>|<)?([0-9]+)([dmwy])$/.exec(low)
      if (m) {
        const n = Number(m[2])
        const mult = { d: 1, w: 7, m: 30, y: 365 }[m[3] as 'd' | 'w' | 'm' | 'y']
        syn.days = n * mult
        continue
      }
    }
    // 以 \ 或 / 结尾 = 限定文件夹
    if (tok.length > 1 && (tok.endsWith('/') || tok.endsWith('\\'))) {
      syn.folder = syn.folder ?? tok.slice(0, -1)
      continue
    }
    rest.push(tok)
  }
  syn.q = rest.join(' ').trim()
  return syn
}

/** 按 size:/dm:/-排除 过滤一条结果。信息不全就不拦（宁可多给一条）。 */
export function synOk(
  path: string,
  size: number | null | undefined,
  mtime: number | null | undefined,
  syn: QSyn,
  nowSec = Date.now() / 1000,
): boolean {
  const [lo, hi] = syn.size
  if ((lo != null || hi != null) && size != null) {
    if (lo != null && size < lo) return false
    if (hi != null && size > hi) return false
  }
  if (syn.days && mtime) {
    if (mtime < nowSec - syn.days * 86400) return false
  }
  const lowPath = String(path ?? '').toLowerCase()
  for (const x of syn.excl) {
    if (x && lowPath.includes(x)) return false
  }
  if (syn.folder) {
    const f = syn.folder.toLowerCase()
    if (!lowPath.includes(f)) return false
  }
  return true
}
