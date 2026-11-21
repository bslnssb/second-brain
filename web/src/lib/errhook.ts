import { errOp } from '../api'

/**
 * 第 277 项：前端异常钩子。
 *
 * 之前只有 ErrorBoundary：只接 render 崩溃，而且必须用户手点「报告」。
 * 事件回调里抛的异常（Enter 取越界行就是典型）、promise rejection、fetch 失败
 * 全都没有去处 —— 打包版是 WebView2，没有控制台，这些错误对开发者等于不存在。
 *
 * 这里补两条系统钩子，把「界面坏了」变成后端 error_reports 里的一份报告。
 * 隐私：只报 message、堆栈前 6 帧、当前面板名；不报输入内容、不报搜索结果。
 */

let lastSig = ''
let lastAt = 0
let sentInWindow = 0
let windowStart = 0

const clean = (s: unknown, n = 400) => String(s ?? '').slice(0, n)

function panelName(): string {
  // 只保留路径里安全的字符（面板名是固定枚举，不含用户数据）
  return location.pathname.replace(/[^A-Za-z0-9_\-/.#]/g, '').slice(0, 40) || '/'
}

function head(stack: string): string {
  return String(stack || '').split('\n').slice(0, 6).join('\n').slice(0, 1500)
}

/** 上报一条前端异常。同消息 60 秒内只发一次；每 10 分钟最多 5 条。 */
export function reportJs(kind: string, message: string, stack = '') {
  try {
    const sig = `${kind}|${clean(message, 120)}`
    const now = Date.now()
    if (sig === lastSig && now - lastAt < 60_000) return
    if (now - windowStart > 10 * 60_000) {
      windowStart = now
      sentInWindow = 0
    }
    if (sentInWindow >= 5) return
    lastSig = sig
    lastAt = now
    sentInWindow += 1
    void errOp('js', {
      kind,
      message: clean(message, 500),
      stack: head(stack),
      route: panelName(),
    }).catch(() => {
      /* 上报失败不能反过来抛错 */
    })
  } catch {
    /* 同上：这个函数永远不该抛 */
  }
}

/** 装钩子。main.tsx 在 render 之前调用一次。 */
export function installErrorHooks() {
  window.addEventListener('error', (ev) => {
    const e = (ev as ErrorEvent).error as Error | undefined
    reportJs(
      (e && e.name) || 'Error',
      (e && e.message) || String((ev as ErrorEvent).message || 'unknown error'),
      (e && e.stack) || '',
    )
  })
  window.addEventListener('unhandledrejection', (ev) => {
    const r = (ev as PromiseRejectionEvent).reason as
      | { message?: string; stack?: string }
      | string
      | undefined
    const msg = typeof r === 'string' ? r : (r && r.message) || 'unhandled rejection'
    const stack = typeof r === 'string' ? '' : (r && r.stack) || ''
    reportJs('UnhandledRejection', msg, stack)
  })
}

// ── 面包屑：动作名 + 枚举，不报内容 ─────────────────────────────────────────
const crumbQ: Array<{ a: string; v?: string }> = []
let crumbTimer: number | null = null

/**
 * 记一个动作（'panel' / 'search' / 'translate' / 'ocr'…）。
 * 只该传枚举值，不要传用户输入。后端还有一层过滤：带路径分隔符的一律丢。
 */
export function crumb(a: string, v?: string) {
  try {
    crumbQ.push(v ? { a, v } : { a })
    if (crumbQ.length > 8) crumbQ.shift()
    if (crumbTimer !== null) return
    crumbTimer = window.setTimeout(() => {
      const items = crumbQ.splice(0, 8)
      crumbTimer = null
      if (!items.length) return
      void errOp('crumb', { items }).catch(() => {
        /* 面包屑发不出去无所谓 */
      })
    }, 1500)
  } catch {
    /* 不能因为记面包屑把界面搞崩 */
  }
}
