import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { dirName, fmtSize, fmtTime, midName, sortItems, SORT_KEYS, type Hit, type SortKey } from './lib/hits'
import { parseQSyn } from './lib/qsyn'
import { AboutModal, DiagnosticsModal, HelpModal, Preview, SettingsDrawer, SupportModal, Timeline, timelineCaption, WhyNotFoundModal } from './ui/panels'
import { TitleBar } from './ui/titlebar'
import { OcrBatchModal } from './ui/ocrbatch'
import { setLang, t } from './lib/i18n'
import { ContextMenu, ReverseModal, ZipToast, type MenuItem } from './ui/context'
import { Onboarding } from './ui/onboarding'
import { exportResults, getOcrBatch, getPage, getWall, openPage, savedOp, type OcrBatchStatus, type WallState } from './api'
import { applyPalette, hasOwnVars, paletteBgHex, readPalette } from './lib/palette'
import { Wallpaper } from './ui/appearance'
import {
  IconApp,
  IconClock,
  IconClose,
  IconDoc,
  IconFile,
  IconFolder,
  IconGrid,
  IconImage,
  IconList,
  IconZip,
  IconGear,
  IconHealth,
  IconCoffee,
  IconHelp,
  IconInfo,
  IconSearch,
  IconMini,
  IconVideo,
  IconAudio,
} from './ui/icons'

type Stats = { files: number; docs: number }
const THUMB_EXT = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'pdf', 'docx', 'xlsx', 'pptx']
const APP_EXTS = ['.exe', '.lnk', '.bat', '.cmd', '.msi', '.appref-ms', '.url', '.ps1']

type Row = Hit & { dn?: string; z?: number; d?: string; e?: string; b?: string; alts?: number; pin?: number }

const sortLabel = (k: SortKey) => t('sort_' + k)

/** 结果行图标：先问系统要真图标（.exe/.lnk 是应用自己的图标，文件夹是黄色文件夹），
 *  拿不到（204/网络错）再退回自绘 SVG —— Listary 那种效果靠的就是这一步。 */
function RowIcon({ row }: { row: Row }) {
  const [bad, setBad] = useState(false)
  const isDir = row.t === 'dir'
  const ext = String(row.e || '').replace(/^\./, '').toLowerCase()
  const src = `/api/icon?p=${encodeURIComponent(row.p)}&e=${encodeURIComponent(ext)}&d=${isDir ? 1 : 0}`
  if (!bad) {
    return (
      <img
        src={src}
        width={16}
        height={16}
        alt=""
        loading="lazy"
        className="h-4 w-4 shrink-0 rounded-[3px] object-contain"
        onError={() => setBad(true)}
      />
    )
  }
  if (isDir) return <IconFolder size={15} />
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'svg'].includes(ext)) return <IconImage size={15} />
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return <IconZip size={15} />
  if (['doc', 'docx', 'pdf', 'txt', 'md', 'xls', 'xlsx', 'ppt', 'pptx', 'csv'].includes(ext)) return <IconDoc size={15} />
  if (['exe', 'lnk', 'msi', 'bat', 'cmd'].includes(ext)) return <IconApp size={15} />
  return <IconFile size={15} />
}

/** 一行结果的「类型」：文件夹 / 文档·PDF / 图片·PNG / 应用·EXE…… */
const TYPE_GROUPS: Array<[string, string[]]> = [
  ['doc', ['doc', 'docx', 'pdf', 'txt', 'md', 'rtf', 'xls', 'xlsx', 'csv', 'ppt', 'pptx', 'epub', 'wps', 'et', 'dps']],
  ['img', ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'svg', 'tif', 'tiff', 'psd']],
  ['video', ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v']],
  ['audio', ['mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg', 'wma']],
  ['zip', ['zip', 'rar', '7z', 'tar', 'gz', 'iso']],
  ['app', ['exe', 'lnk', 'msi', 'bat', 'cmd', 'appref-ms', 'url', 'ps1']],
]

function typeLabel(row: Row) {
  if (row.t === 'dir') return t('kind_dir')
  const e = String(row.e || '').replace(/^\./, '').toLowerCase()
  if (!e) return t('kind_file')
  for (const [gk, exts] of TYPE_GROUPS) {
    if (exts.includes(e)) return `${t('kind_' + gk)} · ${e.toUpperCase()}`
  }
  return `${t('kind_file')} · ${e.toUpperCase()}`
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { 'Sec-Fetch-Site': 'same-origin', ...(init?.headers || {}) },
  })
  if (!r.ok) throw new Error(`${path} ${r.status}`)
  return (await r.json()) as T
}

/** 打开一条结果：桌面版走原生桥（.exe/.lnk 只有它放行），否则退回 HTTP /open */
async function openRow(p: string): Promise<boolean> {
  const bridge = (window as unknown as { pywebview?: { api?: { open_path?: (p: string) => Promise<boolean> } } })
    .pywebview?.api
  // 原生桥有自己的白名单（ev_recent），失败时不能静默吞掉，否则用户看到的是
  // 「双击/Enter/右键打开完全没反应」。桥失败就退回 HTTP /open 再试一次。
  try {
    if (bridge?.open_path) {
      const ok = await bridge.open_path(p)
      if (ok) return true
    }
  } catch {
    /* 桥不可用，继续走 HTTP */
  }
  try {
    await api(`/open?p=${encodeURIComponent(p)}`)
    return true
  } catch {
    return false
  }
}

/** 正文片段：后端用控制字符把命中的词夹起来，这里翻成「淡淡的」高亮。
 *
 * 为什么不高调：结果行是给人扫读的，满屏荧光黄会把「是哪份文件」这件事盖掉。
 * 这里只用同一色系的低透明底 + 稍重字重，扫一眼知道「这句里有」，但不刺眼。 */
function Snip({ text }: { text: string }) {
  if (!text) return null
  const parts = String(text).split(/[]/)
  return (
    <div className="flex min-w-0 items-baseline gap-1.5">
      <span className="shrink-0 rounded-[3px] bg-accent/10 px-1 text-[10px] leading-4 text-accent">{t('body_tag')}</span>
      <span className="truncate text-[11px] leading-4 text-sub">
        {parts.map((seg, i) =>
          i % 2 === 1 ? (
            <mark key={i} className="hl">
              {seg}
            </mark>
          ) : (
            <span key={i}>{seg}</span>
          ),
        )}
      </span>
    </div>
  )
}

export default function App() {
  // 初始关键词来自 URL：exe 的 `SecondBrain.exe 关键词`、托盘「打开详细搜索」、
  // 以及 README 里写的 ?q= 快捷方式都靠这条。早先只有旧模板读它，换成 React 后
  // 一路静默失效——命令行带了词，窗口却是空的。
  const [q, setQ] = useState(() => {
    try {
      return new URLSearchParams(location.search).get('q') || ''
    } catch {
      return ''
    }
  })
  const [rows, setRows] = useState<Row[]>([])
  const [sort, setSort] = useState<SortKey>(() => {
    const saved = localStorage.getItem('sb_sort')
    return (SORT_KEYS as string[]).includes(saved || '') ? (saved as SortKey) : 'rel'
  })
  const [stats, setStats] = useState<Stats>({ files: 0, docs: 0 })
  const [ver, setVer] = useState('v0.9.0')
  const [busy, setBusy] = useState(false)
  const [picked, setPicked] = useState(0)
  const [dark, setDark] = useState(false)
  /** 主题配色（第 191 项）：auto/light/dark + 12 套独立配色，全部免费 */
  const [palette, setPalette] = useState<string>(() => readPalette())
  /** 全局壁纸（第 191 项）：只记「开没开、多显、多糊」，图片本身由后端读本机文件 */
  const [wall, setWall] = useState<WallState>({ on: false, opacity: 15, blur: 0 })
  const [kind, setKind] = useState<'all' | 'dir' | 'doc' | 'img' | 'video' | 'audio' | 'zip' | 'app'>('all')
  const [days, setDays] = useState(0)
  // `?panel=about` 直接打开某个面板：截图、演示录屏、排查「关于面板打不开」时用，
  // 正常使用没有任何人会带这个参数（和 /mini 的 ?q= 预填同一类调试入口）。
  const [panel, setPanel] = useState<null | 'settings' | 'help' | 'about' | 'ocr'>(() => {
    const want = new URLSearchParams(location.search).get('panel')
    return ['about', 'settings', 'help', 'ocr'].includes(want || '')
      ? (want as 'settings' | 'help' | 'about' | 'ocr')
      : null
  })
  /** 「为什么搜不到」弹窗（第 187 项）：从设置里搬出来，帮助面板一个按钮直达 */
  const [whyOpen, setWhyOpen] = useState(false)
  /** 自绘标题栏（第 180 项）：和 Python 摘系统标题栏读的是同一个开关 */
  const [customTitle, setCustomTitle] = useState(false)
  /** 体检与报错弹窗（第 181 项）：独立显示，不再跳设置 */
  const [diagOpen, setDiagOpen] = useState(false)
  /** 赞助弹窗（第 185 项）：打赏从「关于」里独立出来，标题栏/顶栏一个键直达 */
  const [supportOpen, setSupportOpen] = useState(false)
  /** 时间轴天数 + 条数：说明行搬到顶栏后由 App 持有（第 179 项） */
  const [tlDays, setTlDays] = useState(14)
  const [tlTotal, setTlTotal] = useState(0)
  /** 非 0 即「从使用帮助重看教程」的时间戳（第 171 项） */
  const [tourReplay, setTourReplay] = useState(0)
  // 语言切换后整棵界面要跟着换字：监听 i18n 派发的事件，用 key 强制重挂子组件
  const [langTick, setLangTick] = useState(0)
  // 安装时选过语言（安装器写了 lang.txt，/api/ping 会带回来）：
  // 本机没存过选择时，以它为准——否则装完第一眼是中文，非中文用户直接卸载
  useEffect(() => {
    if (localStorage.getItem('sb_lang_user') === '1') return
    fetch('/api/ping')
      .then((r) => r.json())
      .then((v) => {
        if (v && v.lang) setLang(v.lang)   // 安装时选的：写 sb_lang，但不算「用户在本机选过」
      })
      .catch(() => {})
  }, [])
  // 自绘标题栏（第 180 项）：/api/ping 的 titlebar 来自 settings.json，和 Python
  // 启动时摘不摘系统标题栏是同一个值。这里必须**无条件**问一次，不能挂在语言那一支上
  // （用户在本机选过语言时那一支会提前 return）。
  useEffect(() => {
    fetch('/api/ping')
      .then((r) => r.json())
      .then((v) => setCustomTitle(!!(v && v.titlebar)))
      .catch(() => {})
  }, [])

  // 弹窗遮罩的下边界（第 189 项）：窗口最顶上那 8-9px 是 **DWM 画的 caption 带**，
  // 不在网页里，遮罩盖不到它。遮罩要是连网页里的标题栏一起压暗，就会出现
  // 「一条发白 + 一条发灰」的双层（用户截图指出）。所以把标题栏高度告诉 CSS，
  // 遮罩从标题栏底下开始画：上面保持窗口底色，下面照常变暗。
  // 写在 documentElement 上而不是根 div：导览用 createPortal 挂到 body，
  // 挂在根 div 上它继承不到。
  useEffect(() => {
    document.documentElement.style.setProperty('--sb-title', customTitle ? '36px' : '0px')
  }, [customTitle])

  // 托盘「关于/设置/帮助」会带 ?panel= 打开新界面：这里接住并清掉参数
  useEffect(() => {
    // cfg 是旧模板对「设置」的叫法，这里兼容一下（托盘以前传的就是 cfg）；
    // 老文档里还有 ?cfg=1 这种写法，一并认下来，别让链接悄悄失效
    const m = /[?&]panel=(help|about|settings|ocr|cfg)/.exec(location.search)
    const legacyCfg = /[?&]cfg=1(&|$)/.test(location.search)
    // focus=feedback/health 直接开「体检与报错」弹窗（第 181 项）；
    // focus=why 直接开「为什么搜不到」弹窗（第 187 项）——
    // 这两块都已经不在设置里了，还往设置跳等于绕远路。
    const fm = /[?&]focus=(feedback|why|health)/.exec(location.search)
    if (fm) {
      if (fm[1] === 'why') setWhyOpen(true)
      else setDiagOpen(true)
    }
    if ((m || legacyCfg) && fm?.[1] !== 'feedback' && fm?.[1] !== 'health') {
      setPanel(
        (m && m[1] !== 'cfg' ? m[1] : 'settings') as
          'help' | 'about' | 'settings' | 'ocr',
      )
    }
    // 参数一律清掉：留着的话刷新一次会再弹一遍弹窗（focus=feedback 就是这种）
    if (m || legacyCfg || fm) history.replaceState(null, '', location.pathname)
  }, [])
  useEffect(() => {
    const h = () => setLangTick((x) => x + 1)
    window.addEventListener('sb-lang', h)
    return () => window.removeEventListener('sb-lang', h)
  }, [])
  // 详情面板默认关闭（用户明确要求：别一进来就占掉三分之一宽度）；只有手动开过才记住
  const [pv, setPv] = useState(() => localStorage.getItem('sb_pv') === '1')
  const [view, setView] = useState<'list' | 'grid'>(() =>
    localStorage.getItem('sb_view') === 'grid' ? 'grid' : 'list',
  )
  const [menu, setMenu] = useState<{ x: number; y: number; row: { p: string } } | null>(null)
  const [reverse, setReverse] = useState<string | null>(null)
  const [toast, setToast] = useState('')
  const [exporting, setExporting] = useState(false)
  const [ocr, setOcr] = useState<OcrBatchStatus | null>(null)
  const [ocrPro, setOcrPro] = useState(false)
  const ocrOpen = panel === 'ocr'
  const seq = useRef(0)
  const qEl = useRef<HTMLInputElement>(null)

  const syn = useMemo(() => parseQSyn(q), [q])

  /** 导出当前结果（Pro，第 140 项）。后端自己写文件并定位，这里只提示。 */
  const doExport = async () => {
    if (!rows.length || exporting) return
    setExporting(true)
    try {
      const d = await exportResults(rows, q)
      if (d.ok) setToast(t('exp_ok').replace('{n}', String(d.n ?? rows.length)))
      else if (d.pro) setToast(t('exp_pro'))
      else setToast(t('exp_fail'))
    } catch {
      setToast(t('exp_fail'))
    } finally {
      setExporting(false)
    }
  }

  /** 整本 OCR 队列状态（第 148 项起免费）。面板开着 1 秒一刷，关着 5 秒一刷 ——
   *  后者只为让工具栏那个小圆点还准，关掉面板任务本身一直在后台跑。 */
  const refreshOcr = useCallback(() => {
    getOcrBatch()
      .then((d) => {
        // d.pro 只可能是「公开源码版没有 pro/ 私有包」，不是收费墙
        if (d.pro) { setOcrPro(true); setOcr(null) }
        else { setOcrPro(false); setOcr(d) }
      })
      .catch(() => {})
  }, [])
  useEffect(() => {
    if (document.visibilityState === 'hidden') return
    refreshOcr()
    const id = window.setInterval(refreshOcr, ocrOpen ? 1000 : 5000)
    return () => window.clearInterval(id)
  }, [ocrOpen, refreshOcr])

  // 主题配色（第 191 项）：auto = 跟随系统，其余是固定配色。
  // 只在这里写 DOM —— 别处一律调 applyTheme，否则两处各写一遍必然走味。
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      const { dark } = applyPalette(palette, mq.matches)
      setDark(dark)
    }
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [palette])

  // 壁纸（第 191 项）：图片本身由后端按 settings.json 里的路径读，
  // 前端只要「开没开 / 多显 / 多糊」这几项。
  useEffect(() => {
    getWall().then(setWall).catch(() => {})
  }, [])

  useEffect(() => {
    api<Stats>('/api/stats').then(setStats).catch(() => {})
    // 版本号别写死在页面里：后端 bump 之后这里自动跟上
    api<{ ver?: string }>('/api/ping')
      .then((d) => d.ver && setVer(d.ver))
      .catch(() => {})
  }, [])

  // 拖进来的文件：反查它在不在库里、同名版本有几个
  useEffect(() => {
    const over = (e: DragEvent) => {
      e.preventDefault()
    }
    /** 从拖拽事件里榨出真实路径（第 171 项）。
     *
     *  WebView2 不暴露 File.path（那是 Electron 独有的），所以以前只读 text/plain；
     *  而资源管理器拖文件时 text/plain 常常是空的，只有 text/uri-list 带 file:///。
     *  三条都试一遍，再不行走原生选择框兜底 —— 绝不给一句「不是本地路径」就完事。
     */
    const pathOf = (e: DragEvent): string => {
      try {
        const f = e.dataTransfer?.files?.[0] as (File & { path?: string }) | undefined
        if (f?.path) return f.path
      } catch {
        /* 没有 File.path 很正常，继续往下试 */
      }
      for (const kind of ['text/uri-list', 'text/plain']) {
        try {
          const all = e.dataTransfer?.getData(kind) || ''
          // uri-list 可能多行、第一行可能是 # 注释；逐行找带盘符/UNC 的那条
          for (const line of all.split(/\r?\n/)) {
            let v = line.trim()
            if (!v || v.startsWith('#')) continue
            if (v.toLowerCase().startsWith('file:')) {
              v = v.slice(5)
              while (v.startsWith('/')) v = v.slice(1)
            }
            try {
              v = decodeURIComponent(v)
            } catch {
              /* 已经是明文就跳过解码 */
            }
            // D:////a////b 或 D:/a/b，或 \\\\server\\share\\a
            if (/^[a-zA-Z]:[\\/]/.test(v) || v.startsWith('\\\\')) return v
          }
        } catch {
          /* 某些类型读不了，忽略 */
        }
      }
      return ''
    }

    const drop = (e: DragEvent) => {
      e.preventDefault()
      const p = pathOf(e)
      if (p) {
        setReverse(p)
        return
      }
      // 拿不到路径：让主进程弹原生文件选择框，用户选一次就好
      const api = (window as { pywebview?: { api?: { pick_file?: () => Promise<string> } } })
        .pywebview?.api
      if (api?.pick_file) {
        setToast(t('rev_pick'))
        api
          .pick_file()
          .then((q) => {
            if (q) setReverse(q)
            else setToast('')
          })
          .catch(() => setToast(''))
      } else {
        setToast(t('rev_pick'))
      }
    }
    window.addEventListener('dragover', over)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragover', over)
      window.removeEventListener('drop', drop)
    }
  }, [])

  // 设置里改主题：立刻生效，并把选择存起来（下次启动沿用）
  // 偏好改了（固定/隐藏）就让搜索重跑一次，不用用户再敲一遍
  const [reload, setReload] = useState(0)
  // 没搜索时点/划过时间轴某一行，右侧详情也要跟着变（否则「详情」按钮在首页像坏的）
  const [tlRow, setTlRow] = useState<Row | null>(null)
  // 搜索范围：全部 / 只搜文件名 / 只搜正文（正文就是文档内容，不是文件名）
  const [scope, setScope] = useState<'all' | 'name' | 'body'>(
    () => (localStorage.getItem('sb_scope') as 'all' | 'name' | 'body') || 'all',
  )

  // 换配色：立刻生效 + 记到 localStorage（applyPalette 里做）+ 报给后端存一份。
  // 后端那份只为了「下次启动时窗口外框颜色别闪」——真正的准星永远是 localStorage。
  const applyTheme = useCallback((id: string) => {
    setPalette(id)
    void api('/api/settings', { method: 'POST', body: JSON.stringify({ palette: id }) }).catch(() => {})
  }, [])

  // 深色变化同步系统标题栏（第 45 项）。React 主界面从没接过这个桥——旧模板时代
  // 标题栏能跟着变深，换 React 后一直靠后端启动时读 settings.json 兜底；
  // 桥不可用（浏览器模式）时静默跳过。
  //
  // ⚠️ 不能只在挂载时试一次：`window.pywebview.api` 是 pywebview 注入的，
  // 首屏 effect 跑的时候它往往还没到，`?.()` 静默跳过 → 窗口外框永远停在启动时
  // 的那个颜色（深色页套一圈浅色外框，第 184 项）。所以要等 `pywebviewready`
  // 再补一次，并最多轮询 5 秒兜底。
  useEffect(() => {
    type W = { pywebview?: { api?: { set_dark?: (d: boolean, bg?: string | null) => void } } }
    // 自定义配色不是标准深浅色：窗口外框必须拿到**这套配色的底色**，
    // 否则「暖纸」页面会套一圈冷灰边（第 184 项踩过的那三个白/灰来源之一）。
    const bg = hasOwnVars(palette) ? paletteBgHex(palette, window.matchMedia('(prefers-color-scheme: dark)').matches) : null
    let done = false
    const push = () => {
      try {
        const api = (window as unknown as W).pywebview?.api
        if (!api?.set_dark) return false
        api.set_dark(dark, bg)
        done = true
        return true
      } catch {
        return false
      }
    }
    if (push()) return
    const onReady = () => {
      push()
    }
    window.addEventListener('pywebviewready', onReady)
    const id = window.setInterval(() => {
      if (push() || done) window.clearInterval(id)
    }, 300)
    const stop = window.setTimeout(() => window.clearInterval(id), 5000)
    return () => {
      window.removeEventListener('pywebviewready', onReady)
      window.clearInterval(id)
      window.clearTimeout(stop)
    }
  }, [dark, palette])

  // 首启建完库：页脚数字和时间轴要立刻跟上，否则用户搜到了、下面还写着 0
  const onFirstIndexed = useCallback(() => {
    api<Stats>('/api/stats').then(setStats).catch(() => {})
    setReload((x) => x + 1)
  }, [])

  // 搜索：两种来源并行——文件名索引（/api/files，快、量大）与正文命中（/api，带相关度）
  useEffect(() => {
    const my = ++seq.current
    if (!q.trim()) {
      setRows([])
      setBusy(false)
      return
    }
    setBusy(true)
    const t = setTimeout(async () => {
      try {
        // 只搜正文时给后端加 content: 前缀（已有语法，复用同一套解析）；
        // 只搜文件名时干脆不请求正文那一路，省一次全表扫描。
        const bodyQ = scope === 'body' ? `${q} content:` : q
        const [f, b] = await Promise.all([
          scope === 'body'
            ? Promise.resolve({ items: [] as Row[], st: '' })
            : api<{ items: Row[]; st: string }>(`/api/files?q=${encodeURIComponent(q)}&n=60&s=${sort}`),
          scope === 'name'
            ? Promise.resolve({ hits: [] as Hit[] })
            : api<{ hits: Hit[] }>(`/api?q=${encodeURIComponent(bodyQ)}`),
        ])
        if (my !== seq.current) return
        const bodyRows: Row[] = (b.hits || []).map((h) => ({
          ...h,
          n: h.p.split(/[\\/]/).pop() || h.p,
          ty: 'hit',
          // 正文命中让位给文件名命中：搜「陈」时文件名里有陈的排前面
          r: 6,
          e: (h.p.match(/\.([A-Za-z0-9]{1,6})$/) || [, ''])[1],
        }))
        const seen = new Set<string>()
        const merged: Row[] = []
        for (const r of [...(f.items || []), ...bodyRows]) {
          // 分隔符要先统一：/api/files 给的是 C:\a\b，正文命中来自 docs 表、盘符那段
          // 是正斜杠（C:/a\b）。只 toLowerCase 的话同一篇会并列两行 ——
          // 一行带图标、一行带「正文」标签，用户以为库里有两份。
          const k = r.p.replace(/\//g, '\\').toLowerCase()
          if (seen.has(k)) continue
          seen.add(k)
          merged.push(r)
        }
        setRows(sortItems(merged, sort).slice(0, 60))
        setPicked(0)
      } catch {
        if (my === seq.current) setRows([])
      } finally {
        if (my === seq.current) setBusy(false)
      }
    }, 120)
    return () => clearTimeout(t)
  }, [q, sort, reload, scope])

  // 侧栏筛选在本地做：一屏只有几十行，比再打一次请求快，也不会打断输入节奏
  const shown = useMemo(() => {
    const now = Date.now() / 1000
    const kinds: Record<string, string[]> = {
      doc: ['doc', 'docx', 'pdf', 'txt', 'md', 'xls', 'xlsx', 'ppt', 'pptx', 'csv', 'wps', 'et', 'dps', 'rtf'],
      img: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'svg'],
      zip: ['zip', 'rar', '7z', 'tar', 'gz'],
      app: ['exe', 'lnk', 'msi', 'bat', 'cmd'],
    }
    return rows.filter((r) => {
      const e = String(r.e || r.p.split('.').pop() || '').replace(/^\./, '').toLowerCase()
      if (kind === 'dir' && r.t !== 'dir') return false
      if (kind === 'doc' && !kinds.doc.includes(e)) return false
      if (kind === 'img' && !kinds.img.includes(e)) return false
      if (kind === 'zip' && !kinds.zip.includes(e)) return false
      if (kind === 'app' && !kinds.app.includes(e)) return false
      if (days > 0 && (!r.m || r.m < now - days * 86400)) return false
      return true
    })
  }, [rows, kind, days])

  // 侧栏筛选把行数砍短后，picked 要跟着收回来：
  // 否则高亮停在一条已经不存在的行上，回车就是打开错的文件（或什么都不打开）。
  useEffect(() => {
    setPicked((i) => Math.min(i, Math.max(0, shown.length - 1)))
  }, [shown.length])

  const cycleSort = useCallback(() => {
    setSort((cur) => {
      const next = SORT_KEYS[(SORT_KEYS.indexOf(cur) + 1) % SORT_KEYS.length]
      localStorage.setItem('sb_sort', next)
      return next
    })
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName)
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        cycleSort()
        return
      }
      // 帮助面板里写着 Ctrl+F「回到搜索框」，但代码里从来没实现过 —— 用户照着按没反应。
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        qEl.current?.focus()
        qEl.current?.select()
        return
      }
      // 帮助面板还写着 Ctrl+Enter「在文件夹里定位」，同样没实现过。
      // 搜索框里按也算：这正是 Listary 用户的肌肉记忆，不该被 typing 守卫吃掉。
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        const r = shown[picked]
        if (!r) return
        const bridge = (
          window as unknown as { pywebview?: { api?: { reveal?: (p: string) => Promise<boolean> } } }
        ).pywebview?.api
        try {
          void bridge?.reveal?.(r.p)
        } catch {
          /* 浏览器里没有「定位」，桌面版才有 */
        }
        return
      }
      if (e.key === 'Escape') {
        // 输入法正在出候选词时按 Esc 是「取消这个词」，不是「清空搜索框」。
        // 全仓原本没有 isComposing 判断，中文用户每打错一次就被清一次搜索词。
        if (e.isComposing || e.keyCode === 229) return
        if (menu) {
          setMenu(null)
          return
        }
        if (reverse) {
          setReverse(null)
          return
        }
        if (panel) {
          setPanel(null)
          return
        }
        setQ('')
        qEl.current?.focus()
        return
      }
      if (typing) return
      if (!shown.length) return
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        setPicked((i) => Math.min(shown.length - 1, Math.max(0, i + (e.key === 'ArrowDown' ? 1 : -1))))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        // 侧栏筛选（图片/文档…）会在本地把行数砍短，picked 却还停在旧位置，
        // 直接 shown[picked].p 就是 TypeError —— 表现是「按回车什么都没发生」。
        const r = shown[picked]
        if (r) openRow(r.p)
      } else if ((e.ctrlKey || e.metaKey) && /^[1-9]$/.test(e.key)) {
        e.preventDefault()
        const r = shown[Number(e.key) - 1]
        if (r) openRow(r.p)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [shown, picked, cycleSort, panel, menu, reverse])

  const menuItems = (row: { p: string }): MenuItem[] => [
    { label: t('ctx_open'), onClick: () => void openRow(row.p) },
    {
      label: t('ctx_reveal'),
      onClick: async () => {
        const bridge = (window as unknown as { pywebview?: { api?: { reveal?: (p: string) => Promise<boolean> } } })
          .pywebview?.api
        try {
          await bridge?.reveal?.(row.p)
        } catch {
          setToast('浏览器里没有「定位」，桌面版可用')
        }
      },
    },
    {
      label: t('ctx_copy'),
      onClick: () => {
        try {
          void navigator.clipboard?.writeText(row.p)
          setToast('路径已复制')
        } catch {
          setToast('复制失败')
        }
      },
    },
    {
      label: t('ctx_pin'),
      onClick: async () => {
        try {
          const r = await fetch(`/api/app_pref?p=${encodeURIComponent(row.p)}&m=pin`)
          await r.json()
          setToast('记住了：以后先出这个')
          setReload((x) => x + 1)
        } catch {
          setToast('没记住，再试一次')
        }
      },
    },
    {
      label: t('ctx_hide'),
      onClick: async () => {
        try {
          const r = await fetch(`/api/app_pref?p=${encodeURIComponent(row.p)}&m=hide`)
          await r.json()
          setToast('已隐藏，可以从 apps.json 里恢复')
          setReload((x) => x + 1)
        } catch {
          setToast('没隐藏成功')
        }
      },
    },
    {
      label: t('ex_ctx_save'),
      onClick: () => {
        if (q.trim()) void savedOp('add', { q: q.trim() }).catch(() => {})
      },
    },
    {
      label: t('ctx_zip'),
      onClick: async () => {
        try {
          const r = await fetch('/api/zip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths: [row.p] }),
          })
          const d = (await r.json()) as { ok?: boolean; zip?: string; n?: number }
          setToast(d.ok ? t('zip_done').replace('{n}', String(d.n)) : t('zip_fail'))
        } catch {
          setToast('打包失败')
        }
      },
    },
  ]

  // 切到简要搜索：唤起快搜窗 → 收起详情窗。标题栏和顶栏两个入口共用。
  const openMini = () => {
    try {
      const api = (window as unknown as {
        pywebview?: { api?: { open_mini?: () => void; hide_main?: () => void } }
      }).pywebview?.api
      api?.open_mini?.()
      setTimeout(() => api?.hide_main?.(), 120)
    } catch {
      /* 浏览器里没有原生桥，安静失败 */
    }
  }

  const n = (v: number) => v.toLocaleString()

  return (
    <>
      {/* 全局壁纸（第 191 项）：铺在界面底下。开着时根容器底色去掉，
          侧栏/顶栏/列表（本来都是透明的）就能透出图片；卡片和弹窗是实色，文字不受影响。 */}
      <Wallpaper wall={wall} />
      <div
        key={langTick}
        className="relative z-10 flex h-full flex-col bg-bg text-ink"
        style={wall.on ? { background: 'transparent' } : undefined}
      >
      {/* 自绘标题栏（第 180 项）：只有 /api/ping.titlebar 为真才画 —— 那时 Python
          已经把系统标题栏摘掉了，画不出来就是没有标题栏的死窗口 */}
      {customTitle && (
        <TitleBar
          onMini={openMini}
          onDiag={() => setDiagOpen(true)}
          onSupport={() => setSupportOpen(true)}
        />
      )}
      {/* 顶栏 */}
      <header className="flex flex-col gap-2 px-4 pb-2 pt-3">
        <div className="relative">
          <IconSearch size={18} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-sub" />
          <input
            ref={qEl}
            value={q}
            autoFocus
            spellCheck={false}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t('placeholder')}
            className="h-12 w-full rounded-xl border border-line bg-card pl-11 pr-10 text-[15px] outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
          />
          {q && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-sub hover:text-ink"
              onClick={() => setQ('')}
              title={t('act_clear')}
            >
              <IconClose size={14} />
            </button>
          )}
        </div>
        {/* flex-wrap：顶栏右侧现在有 5 个图标按钮，窄窗口下必须能换行，
            否则会被裁掉（第 178 项加体检/报错时实测 640px 宽会溢出） */}
        <div className="flex flex-wrap items-center gap-2">
        <div className="flex h-8 shrink-0 items-center rounded-lg border border-line p-0.5 text-[12px]">
          {(['all', 'name', 'body'] as const).map((k) => (
            <button
              key={k}
              onClick={() => {
                localStorage.setItem('sb_scope', k)
                setScope(k)
              }}
              title={t('scope_' + k)}
              className={`rounded px-2 py-1 ${
                scope === k ? 'bg-accent text-white' : 'text-sub hover:text-ink'
              }`}
            >
              {t('scope_' + k)}
            </button>
          ))}
        </div>
        {/* 时间轴说明行搬到这里（第 179 项）：原来它单独占一行，
            「全部/文件名/正文」这一行中间空着一大片。窄窗口下可截断/换行。 */}
        {!q.trim() && (
          <>
            <span className="min-w-0 truncate text-[11px] text-sub" title={timelineCaption(tlDays, tlTotal)}>
              {timelineCaption(tlDays, tlTotal)}
            </span>
            <div className="flex shrink-0 items-center gap-1">
              {([7, 30] as const).map((d) => (
                <button
                  key={d}
                  onClick={() => setTlDays(d)}
                  className={`rounded-md border px-1.5 py-[2px] text-[11px] ${
                    tlDays === d
                      ? 'border-accent/60 text-accent'
                      : 'border-line text-sub hover:text-ink'
                  }`}
                >
                  {t(d === 7 ? 'd7' : 'd30')}
                </button>
              ))}
            </div>
          </>
        )}
        <div className="ml-auto" />
        {rows.length > 0 && (
          <button
            className="flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-[12px] text-sub hover:text-ink disabled:opacity-40"
            onClick={doExport}
            disabled={exporting}
            title={t('exp_title')}
            data-act="export"
          >
            <IconDoc size={14} />
            {exporting ? t('exp_doing') : t('exp_btn')}
          </button>
        )}
        <button
          className="flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-[12px] text-sub hover:text-ink"
          onClick={cycleSort}
          title={t('act_sort')}
        >
          <IconList size={14} />
          {t('sort')}：{sortLabel(sort)}
        </button>
        <button
          className={`grid h-8 w-8 place-items-center rounded-lg border border-line ${pv ? 'text-accent' : 'text-sub'} hover:text-ink`}
          onClick={() => {
            const next = !pv
            localStorage.setItem('sb_pv', next ? '1' : '0')
            setPv(next)
          }}
          title={t('preview')}
          aria-label={t('preview')} data-act="preview"
        >
          <IconDoc size={14} />
        </button>
        <button
          className={`grid h-8 w-8 place-items-center rounded-lg border border-line ${view === 'grid' ? 'text-accent' : 'text-sub'} hover:text-ink`}
          onClick={() => {
            const v = view === 'grid' ? 'list' : 'grid'
            localStorage.setItem('sb_view', v)
            setView(v)
          }}
          title={view === 'grid' ? t('list') : t('grid')}
          aria-label={t('act_view')} data-act="view"
        >
          {view === 'grid' ? <IconList size={14} /> : <IconGrid size={14} />}
        </button>
        <button
          className="grid h-9 w-9 place-items-center rounded-lg border border-line text-sub hover:text-ink"
          onClick={() => setPanel('help')}
          title={t('help')}
          aria-label={t('help')} data-act="help"
        >
          <IconHelp size={15} />
        </button>
        {/* 快搜：有自绘标题栏时它已经在标题栏里，这里不重复（第 180 项） */}
        {!customTitle && (
          <button
            data-act="mini"
            onClick={openMini}
            title={t('hdr_mini')}
            aria-label={t('hdr_mini')}
            className="grid h-9 w-9 place-items-center rounded-lg border border-line text-sub hover:text-ink"
          >
            <IconMini size={15} />
          </button>
        )}
        {/* 体检与报错（第 181 项）：合成**一个文字按钮**。原来两颗纯图标，
            用户原话「不然其他人不知道这个按钮是干嘛的」，所以带字。
            点了开独立弹窗，不再跳设置。 */}
        {!customTitle && (
          <button
            data-act="diag"
            onClick={() => setDiagOpen(true)}
            title={t('diag_title')}
            aria-label={t('diag_title')}
            className="grid h-9 w-9 place-items-center rounded-lg border border-line text-sub hover:text-ink"
          >
            <IconHealth size={15} />
          </button>
        )}
        {/* 赞助（第 185 项）：没有自绘标题栏时（或摘标题栏失败）顶栏必须有入口，
            否则打赏就彻底找不到了。第 188 项起也是纯图标，跟旁边一排同尺寸。 */}
        {!customTitle && (
          <button
            data-act="support"
            onClick={() => setSupportOpen(true)}
            title={t('sp_title')}
            aria-label={t('sp_title')}
            className="grid h-9 w-9 place-items-center rounded-lg border border-line text-sub hover:text-ink"
          >
            <IconCoffee size={15} />
          </button>
        )}
        <button
          className="grid h-9 w-9 place-items-center rounded-lg border border-line text-sub hover:text-ink"
          onClick={() => setPanel('settings')}
          title={t('settings')}
          aria-label={t('settings')} data-act="settings"
        >
          <IconGear size={15} />
        </button>
        </div>
      </header>

      {/* 语法提示：只在写了语法时出现，避免平时占地方 */}
      {(syn.ext || syn.days || syn.size[0] != null || syn.size[1] != null || syn.excl.length > 0 || syn.folder) && (
        <div className="px-3 pt-1.5 text-[11px] text-sub">
          {t('syn_cond')}
          {syn.ext && <code className="mr-2 rounded bg-card px-1">ext:{syn.ext}</code>}
          {syn.days && <code className="mr-2 rounded bg-card px-1">dm:{syn.days}d</code>}
          {(syn.size[0] != null || syn.size[1] != null) && (
            <code className="mr-2 rounded bg-card px-1">
              size:{syn.size[0] ?? ''}–{syn.size[1] ?? ''}
            </code>
          )}
          {syn.folder && <code className="mr-2 rounded bg-card px-1">{syn.folder}/</code>}
          {syn.excl.map((x) => (
            <code key={x} className="mr-2 rounded bg-card px-1">
              -{x}
            </code>
          ))}
          {syn.q && <span className="ml-1">{t('syn_q2')}{syn.q}</span>}
        </div>
      )}

      <div className="relative mt-2 flex min-h-0 flex-1">
      {/* 侧栏筛选 */}
      <aside className="flex w-[172px] shrink-0 flex-col overflow-auto border-r border-line px-2.5 pb-3 text-[13px]">
        <div className="px-1.5 pb-1.5 pt-2 text-[12px] text-sub">{t('type')}</div>
        {([
          ['all', t('kinds'), <IconGrid size={16} />],
          ['dir', t('kind_dir'), <IconFolder size={16} />],
          ['doc', t('kind_doc'), <IconDoc size={16} />],
          ['img', t('kind_img'), <IconImage size={16} />],
          ['video', t('kind_video'), <IconVideo size={16} />],
          ['audio', t('kind_audio'), <IconAudio size={16} />],
          ['zip', t('kind_zip'), <IconZip size={16} />],
          ['app', t('kind_app'), <IconApp size={16} />],
        ] as const).map(([k, label, ic]) => (
          <button
            key={k}
            onClick={() => setKind(k)}
            className={`mb-1 flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13.5px] ${
              kind === k ? 'bg-card text-ink' : 'text-sub hover:text-ink'
            }`}
          >
            <span className={kind === k ? 'text-accent' : ''}>{ic}</span>
            {label}
          </button>
        ))}
        <div className="mx-1.5 mt-2.5 border-t border-line pb-1.5 pt-2.5 text-[12px] text-sub">{t('time')}</div>
        {([[0, t('days_all')], [1, t('today')], [7, t('d7')], [30, t('d30')], [365, t('year')]] as const).map(([d, label]) => (
          <button
            key={d}
            onClick={() => setDays(d)}
            className={`mb-1 flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13.5px] ${
              days === d ? 'bg-card text-ink' : 'text-sub hover:text-ink'
            }`}
          >
            <span className={days === d ? 'text-accent' : ''}>
              <IconClock size={16} />
            </span>
            {label}
          </button>
        ))}
        {/* 语法速览已收进「使用帮助」：主界面重复一遍只是噪音（第 171 项）。
            三个入口原来只有文字：比上面「类型 / 时间」两组少一列图标、行高也更矮，
            看着像忘了画（第 190 项用户："弄个图标吧，不然感觉有点丑"）。
            现在图标、行高、左内边距和上面两组完全对齐，只保留「不是筛选、不带选中态」。 */}
        <div className="mx-1.5 mb-2 mt-3 border-t border-line" />
        {([
          ['help', t('help'), <IconHelp size={16} />],
          ['settings', t('settings'), <IconGear size={16} />],
          ['about', t('about'), <IconInfo size={16} />],
        ] as const).map(([k, label, ic]) => (
          <button
            key={k}
            onClick={() => setPanel(k)}
            className="mb-1 flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13.5px] text-sub hover:text-ink"
          >
            {ic}
            {label}
          </button>
        ))}
        {/* 文件和正文数量不在这里重复：右下角状态栏一直显示（第 173 项） */}
      </aside>

      {/* 列表 */}
      {/* 外层只管布局、不滚；真正的滚动交给 main。原因：时间轴的列头在 main 里
          `sticky top-0`，若滚动发生在 main 的**父级**，sticky 会跟着内容一起走
          （最近 14 天那种没有搜索词时列头一滚就没，第 179 项用户实测）。 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {q.trim() && (
          <div className="z-[1] flex shrink-0 items-center gap-2 border-b border-line bg-bg px-2 py-1 text-[11px] text-sub">
            <span className="w-4 shrink-0" />
            <span className="min-w-0 flex-1">{t('col_name')}</span>
            <span className="w-24 shrink-0">{t('col_type')}</span>
            <span className="w-20 shrink-0 text-right">{t('col_size')}</span>
            <span className="w-28 shrink-0 text-right">{t('col_mtime')}</span>
          </div>
        )}
      <main className="min-h-0 flex-1 overflow-auto px-3">
        {!q.trim() && (
          <Timeline
            key={reload}          /* 首启建库完成后 onIndexed 会 +1，这里强制重取时间轴 */
            days={tlDays}
            onTotal={setTlTotal}
            onPick={(p) => openRow(p)}
            kind={kind}
            sort={sort}
            view={view}
            selected={tlRow?.p}
            onHover={(r) => setTlRow({ ...r, ty: 'file' } as Row)}
            onMenu={(e, p) => setMenu({ x: e.clientX, y: e.clientY, row: { p } })}
          />
        )}

        {q.trim() && !busy && shown.length === 0 && (
          <div className="grid h-full place-items-center text-center text-sub">
            <div>
              <div className="text-[15px] text-ink">{t('empty_none').replace('{q}', syn.q || q)}</div>
              <div className="mt-1 text-[12px]">{t('empty_hint')}</div>
            </div>
          </div>
        )}

        {view === 'grid' && shown.length > 0 && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-2 py-2">
            {shown.map((r, i) => {
              const ext = String(r.e || '').replace(/^\./, '').toLowerCase()
              return (
                <div
                  key={r.p + i}
                  onClick={() => setPicked(i)}
                  onDoubleClick={() => openRow(r.p)}
                  onContextMenu={(e) => {
                    e.preventDefault()
                    setPicked(i)
                    setMenu({ x: e.clientX, y: e.clientY, row: r })
                  }}
                  className={`cursor-default rounded-lg border p-1.5 ${
                    i === picked ? 'border-accent bg-card' : 'border-transparent hover:bg-card/60'
                  }`}
                >
                  <div className="mb-1 grid h-[86px] place-items-center overflow-hidden rounded bg-card/60 text-sub">
                    {THUMB_EXT.includes(ext) ? (
                      <img
                        src={`/api/thumb?p=${encodeURIComponent(r.p)}&s=1`}
                        alt=""
                        className="max-h-full max-w-full object-contain"
                        onError={(e) => {
                          (e.currentTarget as HTMLImageElement).style.display = 'none'
                        }}
                      />
                    ) : (
                      <span className="scale-[1.8]"><RowIcon row={r} /></span>
                    )}
                  </div>
                  <div className="px-1.5 py-1">
                    <div className="truncate text-[11px]" title={r.dn || r.n}>
                      {midName(String(r.dn || r.n || r.p.split(/[\/]/).pop() || ''), 16)}
                    </div>
                    <div className="truncate text-[10px] text-sub">{fmtSize(r.s)}</div>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {view === 'list' &&
          shown.map((r, i) => {
            const prev = shown[i - 1]
            const startsHits = r.ty === 'hit' && (!prev || prev.ty !== 'hit')
            const hitCount = shown.filter((x) => x.ty === 'hit').length
            const isApp = APP_EXTS.includes(String(r.e || '').toLowerCase())
            return (
              <div key={r.p + i}>
                <div
                  onMouseEnter={() => setPicked(i)}
                  onContextMenu={(e) => {
                    e.preventDefault()
                    setPicked(i)
                    setMenu({ x: e.clientX, y: e.clientY, row: r })
                  }}
                  onDoubleClick={() => openRow(r.p)}
                  onClick={() => setPicked(i)}
                  className={`flex cursor-default items-start gap-2 rounded-md px-2 py-[5px] ${
                    i === picked ? 'bg-card' : 'hover:bg-card/60'
                  }`}
                >
                  <span className="w-4 shrink-0 text-sub">
                    {r.ty === 'hit' ? <IconDoc size={15} /> : <RowIcon row={r} />}
                  </span>
                  <span className="min-w-0 flex-1" title={r.dn || r.n}>
                    <span className="block truncate">
                      {midName(String(r.dn || r.n || r.p.split(/[\/]/).pop() || ''), 42)}
                    </span>
                    {r.ty === 'hit' && r.b ? (
                      <Snip text={String(r.b)} />
                    ) : isApp ? (
                      r.alts && r.alts > 1 ? (
                        <span className="block truncate text-[11px] leading-4 text-sub">
                          {t('alts_n').replace('{n}', String(r.alts))}
                        </span>
                      ) : null
                    ) : (
                      <span className="block truncate text-[11px] leading-4 text-sub">
                        {r.d || dirName(r.p)}
                      </span>
                    )}
                  </span>
                  {r.ty === 'hit' && /\.pdf$/i.test(r.p) ? (
                    <button
                      className="w-24 shrink-0 truncate text-left text-[11px] text-accent hover:underline"
                      onClick={async (e) => {
                        e.stopPropagation()
                        try {
                          const pr = await getPage(r.p, q)
                          if (!pr.page) { setToast(t('ex_page_none')); return }
                          await openPage(r.p, pr.page)
                          setToast(t('ex_page_open').replace('{n}', String(pr.page)))
                        } catch { setToast(t('ex_page_none')) }
                      }}
                    >{t('ex_page_btn')}</button>
                  ) : (
                    <span className="w-24 shrink-0 truncate text-[11px] text-sub">{typeLabel(r)}</span>
                  )}
                  <span className="w-20 shrink-0 text-right text-[11px] text-sub">{fmtSize(r.s)}</span>
                  <span className="w-28 shrink-0 text-right font-mono text-[11px] text-sub">{fmtTime(r.m)}</span>
                </div>
                {startsHits && i > 0 && (
                  <div className="mb-1 mt-1 border-t border-line pb-0.5 pl-6 pt-1.5 text-[11px] text-sub">
                    {t('sep_n').replace('{n}', String(hitCount))}
                  </div>
                )}
              </div>
            )
          })}
      </main>
      </div>

      {pv && !(shown[picked] || tlRow) && (
        <aside className="flex w-[300px] shrink-0 flex-col border-l border-line px-3 py-3 text-[12px] text-sub">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[13px] text-ink">{t('preview')}</span>
            <button onClick={() => setPv(false)} className="hover:text-ink" title={t('close')}>
              <IconClose size={14} />
            </button>
          </div>
          <div className="text-[11px] leading-5">{t('preview_hint')}</div>
          <div className="mt-2 text-[11px] leading-5">{t('preview_hint2')}</div>
        </aside>
      )}
      {pv && (shown[picked] || tlRow) && (
        <Preview
          row={(shown[picked] || tlRow)!}
          q={syn.q || q}
          onClose={() => setPv(false)}
          onOpen={() => openRow((shown[picked] || tlRow)!.p)}
          onReveal={async () => {
            const bridge = (window as unknown as { pywebview?: { api?: { reveal?: (p: string) => Promise<boolean> } } })
              .pywebview?.api
            try {
              await bridge?.reveal?.((shown[picked] || tlRow)!.p)
            } catch {
              /* 浏览器里没有原生桥，安静失败 */
            }
          }}
          onCopy={() => {
            try {
              void navigator.clipboard?.writeText((shown[picked] || tlRow)!.p)
            } catch {
              /* 剪贴板被拒就算了 */
            }
          }}
        />
      )}

      <SettingsDrawer
        open={panel === 'settings'}
        onClose={() => setPanel(null)}
        palette={palette}
        onPalette={applyTheme}
        wall={wall}
        onWall={setWall}
        onRefreshed={() => {
          api<Stats>('/api/stats').then(setStats).catch(() => {})
          setToast('维护任务跑完了')
        }}
        currentQ={q}
        onPickSearch={(x) => setQ(x)}
        onOpenOcr={() => setPanel('ocr')}
        ocrRunning={ocr?.run === 'running'}
      />
      </div>

      {menu && <ContextMenu x={menu.x} y={menu.y} items={menuItems(menu.row)} onClose={() => setMenu(null)} />}
      {reverse && <ReverseModal path={reverse} onClose={() => setReverse(null)} />}
      {toast && <ZipToast text={toast} onClose={() => setToast('')} />}

      <DiagnosticsModal open={diagOpen} onClose={() => setDiagOpen(false)} />
      {/* 为什么搜不到（第 187 项）：帮助里点按钮直接弹，不再跳设置 */}
      <WhyNotFoundModal open={whyOpen} onClose={() => setWhyOpen(false)} />
      {/* 赞助（第 185 项）：自带弹窗，和「关于」平级。关于里点按钮也会走这里 */}
      <SupportModal open={supportOpen} onClose={() => setSupportOpen(false)} />
      <HelpModal open={panel === 'help'} onClose={() => setPanel(null)}
        onWhyNotFound={() => { setPanel(null); setWhyOpen(true) }}
        onReplayTour={() => setTourReplay(Date.now())} />
      <AboutModal
        open={panel === 'about'}
        onClose={() => setPanel(null)}
        onSupport={() => {
          // 先关关于再开赞助：两个都是 z-30 的遮罩，叠在一起会互相压住
          setPanel(null)
          setSupportOpen(true)
        }}
      />
      <OcrBatchModal
        open={ocrOpen}
        onClose={() => setPanel(null)}
        status={ocr}
        pro={ocrPro}
        rows={shown}
        onRefresh={refreshOcr}
        onOpenAbout={() => setPanel('about')}
      />

      {/* 首启引导：装完第一次打开自动建库（老用户 need=false，完全无感） */}
      <Onboarding replay={tourReplay} onOpenSettings={() => setPanel('settings')}
        onIndexed={onFirstIndexed} />

      {/* 页脚 */}
      <footer className="flex items-center gap-3 border-t border-line px-3 py-1.5 text-[11px] text-sub">
        <span>
          {busy
            ? t('checking')
            : q.trim()
              ? `${shown.length} / ${rows.length} ${t('items')}`
              : t('timeline')}
        </span>
        <span className="text-line">|</span>
        <span>
          <kbd className="rounded border border-line px-1">↑↓</kbd> {t('kb_select')} ·{' '}
          <kbd className="rounded border border-line px-1">Enter</kbd> {t('kb_open')} ·{' '}
          <kbd className="rounded border border-line px-1">Ctrl+S</kbd> {t('kb_sort')} ·{' '}
          <kbd className="rounded border border-line px-1">Esc</kbd> {t('kb_clear')}
        </span>
        <span className="ml-auto">
          <button className="text-accent hover:underline" onClick={() => setPanel('about')}>{ver}</button> · {n(stats.files)} {t('files_indexed')} + {n(stats.docs)} {t('docs_indexed')}
        </span>
        <span className={dark ? 'opacity-70' : 'opacity-50'} title={dark ? t('theme_dark') : t('theme_light')}>
          <IconClock size={13} />
        </span>
        <span className="opacity-50" title={t('icon_svg')}>
          <IconGrid size={13} />
        </span>
      </footer>
      </div>
    </>
  )
}
