import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { baseName, dirName, fmtSize, fmtTime, mergeHits, midName, nextSortCol, setTimeFormat, SORT_KEYS, type Hit, type SortCol, type SortColState, type SortKey } from './lib/hits'
import { parseQSyn } from './lib/qsyn'
import { DiagnosticsModal, Preview, SettingsDrawer, SupportModal, WhyNotFoundModal } from './ui/panels'
import { useColResize } from './ui/useColResize'
import { TitleBar } from './ui/titlebar'
import { OcrBatchModal } from './ui/ocrbatch'
import { SnipModal, type SnipResultData } from './ui/snipmodal'
import { ClipboardModal } from './ui/ClipboardModal'
import { TranslateModal } from './ui/TranslateModal'
import { setLang, t } from './lib/i18n'
import { crumb } from './lib/errhook'
import { nextKind } from './lib/sidebar'
import { ContextMenu, ReverseModal, ZipToast, type MenuItem } from './ui/context'
import { Onboarding } from './ui/onboarding'
import { dialogJump, exportResults, getCompact, getIndexHealth, getLocaleInfo, getOcrBatch, getPage, getSettings, getWall, openPage, savedOp, startIndexUpdate, type IndexHealth, type OcrBatchStatus, type WallState } from './api'
import { applyPalette, hasOwnVars, paletteBgHex, paletteDark, paletteVarsOf, readPalette } from './lib/palette'
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
  IconSearch,
  IconMini,
  IconSnip,
  IconClipboard,
  IconTranslate,
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
  // 第 248 项：虚拟商店应用没有扩展名，图标挂了也不能回落成空白纸
  const isShellApp = String(row.p || '').indexOf('shell:AppsFolder') === 0
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
  if (isShellApp) return <IconApp size={15} />
  if (isDir) return <IconFolder size={15} />
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'svg'].includes(ext)) return <IconImage size={15} />
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return <IconZip size={15} />
  if (['doc', 'docx', 'pdf', 'txt', 'md', 'xls', 'xlsx', 'ppt', 'pptx', 'csv'].includes(ext)) return <IconDoc size={15} />
  if (['exe', 'lnk', 'msi', 'bat', 'cmd'].includes(ext)) return <IconApp size={15} />
  return <IconFile size={15} />
}

/** 一行结果的「类型」：文件夹 / 文档·PDF / 图片·PNG / 应用·EXE……
 *  这张表必须和 brain.py 的 `_KIND_EXTS` 一模一样 —— 少一个扩展名，用户选了那一类
 *  就搜不到（iPhone 的 .heic 曾经整个漏在「图片」外面）。preflight 逐项比对两边。 */
const TYPE_GROUPS: Array<[string, string[]]> = [
  ['doc', ['doc', 'docx', 'pdf', 'txt', 'md', 'rtf', 'xls', 'xlsx', 'csv', 'ppt', 'pptx', 'epub', 'wps', 'et', 'dps', 'xmind']],
  ['img', ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'svg', 'tif', 'tiff', 'psd', 'heic', 'raw', 'cr2', 'nef']],
  ['video', ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v']],
  ['audio', ['mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg', 'wma', 'ape']],
  ['zip', ['zip', 'rar', '7z', 'tar', 'gz', 'iso', 'bz2', 'xz']],
  ['app', ['exe', 'lnk', 'msi', 'bat', 'cmd', 'appref-ms', 'url', 'ps1']],
]

/** 侧栏 kind → 扩展名（不带点）。搜索时翻成 ext: 语法交给后端，和 typeLabel 同源。
 *  第 235 项：以前这张表只在 typeLabel 里用，侧栏筛选另写了一份、还漏了 video/audio，
 *  于是「筛选视频」等于没筛。 */
const KIND_EXTS: Record<string, string[]> = Object.fromEntries(TYPE_GROUPS)

/** 第 324 项：左栏「文档」子分类。表必须和 brain.py 的 `DOC_SUBTYPES` 一模一样 ——
 *  两边错一个扩展名，就会出现「点了 PDF 却冒出 docx」这种自相矛盾的结果。 */
const SUBTYPES: Array<[string, string[]]> = [
  ['pdf', ['pdf']],
  ['word', ['doc', 'docx', 'wps']],
  ['excel', ['xls', 'xlsx', 'csv', 'et']],
  ['ppt', ['ppt', 'pptx', 'dps']],
  ['md', ['md', 'markdown']],
  ['txt', ['txt', 'rtf']],
]
const SUB_EXTS: Record<string, string[]> = Object.fromEntries(SUBTYPES)

type Facet = { k?: string; name?: string; p?: string; n: number }
type Facets = {
  ok?: boolean
  total?: number
  kinds?: Array<{ k: string; n: number }>
  subs?: Array<{ k: string; n: number }>
  folders?: Facet[]
}

/** 第 324 项：把命中的关键词在文件名里标出来。
 *  只标普通词；`ext:pdf`、`size:>10m`、`-排除`、`xxx/` 这些语法片段不参与高亮 ——
 *  高亮它们等于告诉用户「这几个字在文件名里」，纯属误导。 */
function Hi({ text, q }: { text: string; q: string }) {
  const raw = String(text || '')
  if (!raw || !q) return <>{raw}</>
  const toks = q
    .split(/\s+/)
    .map((s) => s.trim())
    .filter((s) => {
      const lo = s.toLowerCase()
      if (!s) return false
      if (/^[-－]/.test(s)) return false
      if (/^(ext|type|size|dm|content|内容|c):/.test(lo)) return false
      return !/[/\\]$/.test(s)
    })
  if (!toks.length) return <>{raw}</>
  const esc = toks.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const parts = raw.split(new RegExp(`(${esc.join('|')})`, 'gi'))
  return (
    <>
      {parts.map((seg, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="hl">
            {seg}
          </mark>
        ) : (
          <span key={i}>{seg}</span>
        ),
      )}
    </>
  )
}

function typeLabel(row: Row) {
  if (row.t === 'dir') return t('kind_dir')
  const e = String(row.e || '').replace(/^\./, '').toLowerCase()
  if (!e) return t('kind_file')
  for (const [gk, exts] of TYPE_GROUPS) {
    if (exts.includes(e)) return `${t('kind_' + gk)} · ${e.toUpperCase()}`
  }
  return `${t('kind_file')} · ${e.toUpperCase()}`
}

/** 第 327 项：settings.json → 左栏开关。类型/时间缺省显示（老配置里没有这几个键）。
    第 337 项：**文件夹分组默认关** —— 用户原话「这一堆我感觉没啥用」，那 6 个顶层目录
    （按顶层目录名分出来的那几组）对日常搜索没什么帮助，改成默认不显示、设置里再开。
    第 338 项：**计数数字默认关** —— 类型后面那列 109763 / 41075…（用户要求做成开关）。 */
const sbFromSettings = (s: { sb_types?: boolean; sb_folders?: boolean; sb_time?: boolean; sb_counts?: boolean }) => ({
  types: s.sb_types !== false,
  folders: s.sb_folders === true,
  time: s.sb_time !== false,
  counts: s.sb_counts === true,
})

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    // ⚠️ `Content-Type: application/json` 不能省：后端 POST 校验它，缺了直接 403，
    // 而错误又被调用处的 .catch 吞掉 —— 表现就是「设置页点什么都没发生」（第 370 项查出来）。
    // `Sec-Fetch-Site` 是浏览器自己发的（JS 设这个头会被静默丢弃），留在这里只是表意。
    headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin', ...(init?.headers || {}) },
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
  /* 第 334 项：点列头排序。null = 没点过，仍按上面的 sort 走。 */
  const [sortCol, setSortCol] = useState<SortColState | null>(() => {
    try {
      const raw = localStorage.getItem('sb_sortcol')
      if (!raw) return null
      const j = JSON.parse(raw) as SortColState
      return j && j.key ? j : null
    } catch {
      return null
    }
  })
  const toggleSortCol = (key: SortCol) => {
    setSortCol((cur) => {
      const next = nextSortCol(cur, key)
      localStorage.setItem('sb_sortcol', JSON.stringify(next))
      return next
    })
  }
  const [stats, setStats] = useState<Stats>({ files: 0, docs: 0 })
  /** 第 308 项：索引健康快照（空索引 / 失联文件夹的横幅用） */
  const [health, setHealth] = useState<IndexHealth | null>(null)
  const [scanning, setScanning] = useState(false)
  const [ver, setVer] = useState('v0.9.0')
  const [busy, setBusy] = useState(false)
  /** 第 366 项：整理索引库的进度（后台空闲迁移与手动入口共用）——busy 时挂一条横幅，
   *  别让「搜索短暂报忙」看起来像故障。 */
  const [compact, setCompact] = useState<{ state: string; pct: number }>({ state: 'idle', pct: 0 })
  const [picked, setPicked] = useState(0)
  // 第 314 项：选中项改成「按路径钉住」。以前只记下标，而结果列表会随时序刷新
  // （「今天」这段每写一个文件就重排一次），下标就会指到别的行上 —— 表现是
  // 「点了 A，右边详情还停在 B」。钉住路径，下标每次现算。
  const [pickP, setPickP] = useState('')
  const [dark, setDark] = useState(false)
  /** 主题配色（第 191 项）：auto/light/dark + 12 套独立配色，全部免费 */
  const [palette, setPalette] = useState<string>(() => readPalette())
  /** 全局壁纸（第 191 项）：只记「开没开、多显、多糊」，图片本身由后端读本机文件 */
  const [wall, setWall] = useState<WallState>({
    on: false,
    opacity: 15,
    blur: 0,
    pos_x: 50,
    pos_y: 50,
    zoom: 100,
    scope_main: true,
    scope_settings: true,
    scope_mini: true,
  })
  const [kind, setKind] = useState<'all' | 'dir' | 'doc' | 'img' | 'video' | 'audio' | 'zip' | 'app'>('all')
  /* 第 324 项：文档子分类（点进「文档」后再分 PDF/Word/Excel/PowerPoint/Markdown/文本）。
     空串 = 全部文档。文件夹子分类（folder）是目录前缀，直接交给后端 pf。 */
  const [subType, setSubType] = useState('')
  const [folder, setFolder] = useState('')
  const [facets, setFacets] = useState<Facets>({})
  const [days, setDays] = useState(0)
  /* 第 327 项：左栏三块（类型 / 文件夹 / 时间）能不能显示，做成设置项。
     默认全开；关掉只是「不画出来」，筛选状态本身保留，重新打开还是原来那样。 */
  const [sb, setSb] = useState({ types: true, folders: false, time: true, counts: false })
  // `?panel=about` 直接打开某个面板：截图、演示录屏、排查「关于面板打不开」时用，
  // 正常使用没有任何人会带这个参数（和 /mini 的 ?q= 预填同一类调试入口）。
  const [panel, setPanel] = useState<null | 'settings' | 'about' | 'ocr'>(() => {
    const want = new URLSearchParams(location.search).get('panel')
    // 第 358 项：?panel=help 仍然兼容（托盘/老链接都还在用），落到设置的「使用帮助」分区
    const m = want === 'help' ? 'settings' : want
    return ['about', 'settings', 'ocr'].includes(m || '') ? (m as 'settings' | 'about' | 'ocr') : null
  })
  /** 第 358 项：打开设置时先落在哪一页（「使用帮助」并进设置后由入口决定） */
  const [settingsSec, setSettingsSec] = useState<'help' | undefined>(() =>
    new URLSearchParams(location.search).get('panel') === 'help' ? 'help' : undefined,
  )
  /** 任何入口要「看使用帮助」都走这里：开设置 + 直接切到 help 分区 */
  const openHelp = () => {
    setSettingsSec('help')
    setPanel('settings')
  }
  /** 「为什么搜不到」弹窗（第 187 项）：从设置里搬出来，帮助面板一个按钮直达 */
  const [whyOpen, setWhyOpen] = useState(false)
  // 第 277 项：面板切换记一条面包屑（只有面板名，不含任何搜索词）。
  // 崩溃报告里能看出「出事前用户停在哪个面板」，比 debug.log 的窗口事件有用。
  useEffect(() => {
    crumb('panel', panel || 'main')
  }, [panel])
  /** 自绘标题栏（第 180 项）：和 Python 摘系统标题栏读的是同一个开关 */
  const [customTitle, setCustomTitle] = useState(false)
  /** 体检与报错弹窗（第 181 项）：独立显示，不再跳设置 */
  const [diagOpen, setDiagOpen] = useState(false)
  /** 赞助弹窗（第 185 项）：打赏从「关于」里独立出来，标题栏/顶栏一个键直达 */
  const [supportOpen, setSupportOpen] = useState(false)
  /** 屏幕截图识字结果弹层与数据 */
  const [snipOpen, setSnipOpen] = useState(false)
  const [snipResult, setSnipResult] = useState<SnipResultData | null>(null)
  /** 剪贴板历史与聚合翻译弹层 */
  const [clipOpen, setClipOpen] = useState(false)
  const [transOpen, setTransOpen] = useState(false)
  /** 粘贴到搜索框的图片 base64 数据与识字状态 */
  const [pastedImg, setPastedImg] = useState<string | null>(null)
  const [pastedImgLoading, setPastedImgLoading] = useState(false)
  /** 图片放大查看弹层 */
  const [previewImgOpen, setPreviewImgOpen] = useState(false)

  const doOcrPastedImg = useCallback(
    async (targetImg?: string) => {
      const img = targetImg || pastedImg
      if (!img) return
      setPastedImgLoading(true)
      try {
        const fetchOcr = async (): Promise<{ ok: boolean; text?: string }> => {
          // 优先通过标准 HTTP POST 接口调用（轻量异步，不阻塞 pywebview IPC 消息循环）
          try {
            const resp = await fetch('/api/snip/ocr_image', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ image: img }),
            })
            if (resp.ok) {
              const r = await resp.json()
              if (r && typeof r === 'object') return r
            }
          } catch {}

          // 备用：pywebview API（若字符串过长则传空，触发后端复用最新截图原图）
          const api = (window as unknown as {
            pywebview?: { api?: { ocr_image?: (b64?: string) => Promise<{ ok: boolean; text?: string }> } }
          }).pywebview?.api
          if (api?.ocr_image) {
            try {
              const r = await api.ocr_image(img.length > 500 ? '' : img)
              if (r && typeof r === 'object') return r
            } catch {}
          }
          return { ok: false, text: '' }
        }

        const res = await Promise.race([
          fetchOcr(),
          new Promise<{ ok: boolean; text?: string }>((_, reject) =>
            setTimeout(() => reject(new Error('OCR timeout')), 12000),
          ),
        ])

        if (res?.ok && res.text && res.text.trim()) {
          const txt = res.text.trim()
          setQ(txt)
          setPastedImg(null)
          setPreviewImgOpen(false)
          setToast(t('app_snip_ocr_done'))
          qEl.current?.focus()
        } else {
          setToast(t('app_snip_ocr_none'))
        }
      } catch {
        setToast(t('app_snip_ocr_fail'))
      } finally {
        setPastedImgLoading(false)
      }
    },
    [pastedImg],
  )

  useEffect(() => {
    // 第 308 项：列表里的时间按 Windows 区域格式显示（读不到就留在 ISO 式）
    getLocaleInfo()
      .then((l) => {
        if (l && (l.date || l.time)) setTimeFormat(l.date || '', l.time || '')
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    const handlePaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items
      if (items) {
        for (let i = 0; i < items.length; i++) {
          if (items[i].type.indexOf('image') !== -1) {
            e.preventDefault()
            const blob = items[i].getAsFile()
            if (blob) {
              const reader = new FileReader()
              reader.onload = (evt) => {
                setPastedImg(evt.target?.result as string)
              }
              reader.readAsDataURL(blob)
              return
            }
          }
        }
      }
      const api = (window as unknown as { pywebview?: { api?: { read_clipboard_image?: () => Promise<{ has_image: boolean; data_url?: string }> } } }).pywebview?.api
      if (api?.read_clipboard_image) {
        api.read_clipboard_image().then((res) => {
          if (res?.has_image && res.data_url) {
            setPastedImg(res.data_url)
          }
        }).catch(() => {})
      }
    }
    window.addEventListener('paste', handlePaste)
    return () => window.removeEventListener('paste', handlePaste)
  }, [])

  const handleSnipDone = useCallback(
    (st?: { status?: string; do_ocr?: boolean; do_translate?: boolean; thumb_data_url?: string; text?: string }) => {
      if (!st || st.status !== 'done') return
      // 翻译模式：结果由原生原位卡片显示，前端不弹 SnipModal、也不把图挂到搜索框
      if (st.do_translate) return
      const thumbUrl = `/api/snip/thumb?_t=${Date.now()}`
      const applyThumb = (url: string) => {
        setPastedImg(url)
        if (st.do_ocr) {
          setSnipResult(st as unknown as SnipResultData)
          if (st.text && st.text.trim()) {
            setQ(st.text.trim())
            qEl.current?.focus()
          } else {
            void doOcrPastedImg(url)
          }
        } else {
          setToast(t('app_snip_ready'))
          qEl.current?.focus()
        }
      }

      applyThumb(thumbUrl)
    },
    [doOcrPastedImg],
  )

  const checkSnipResult = useCallback(async () => {
    try {
      const res = await fetch('/api/snip/result?_t=' + Date.now()).then((r) => r.json())
      if (res && res.status === 'done') {
        handleSnipDone(res)
      }
    } catch {}
  }, [handleSnipDone])

  const triggerSnip = useCallback(
    (withOcr: boolean = false, withTranslate: boolean = false) => {
      const startTs = Date.now() / 1000
      const api = (window as unknown as { pywebview?: { api?: { trigger_snip?: (ocr: boolean, translate?: boolean) => Promise<number | boolean> } } }).pywebview?.api

      const startPoll = (expId?: number) => {
        let tries = 0
        const timer = window.setInterval(async () => {
          tries++
          if (tries > 80) {
            window.clearInterval(timer)
            return
          }
          try {
            const res = await fetch('/api/snip/result?_t=' + Date.now()).then((r) => r.json())
            if (res && res.status === 'done') {
              if (expId && res.snip_id && res.snip_id < expId) return
              if (res.ts && res.ts < startTs) return
              window.clearInterval(timer)
              handleSnipDone(res)
            }
          } catch {}
        }, 250)
      }

      if (api?.trigger_snip) {
        try {
          Promise.resolve(api.trigger_snip(withOcr || withTranslate, withTranslate))
            .then((ret) => {
              const id = typeof ret === 'number' ? ret : 0
              startPoll(id)
            })
            .catch(() => startPoll())
        } catch {
          startPoll()
        }
      } else {
        fetch('/api/snip/trigger', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ocr: withOcr || withTranslate, translate: withTranslate }),
        })
          .then((r) => r.json())
          .then((d) => startPoll(d?.snip_id))
          .catch(() => startPoll())
      }
    },
    [handleSnipDone],
  )

  const triggerSnipPure = useCallback(() => triggerSnip(false), [triggerSnip])
  /* 第 369 项：截图翻译 —— 框选后本机 OCR、出网翻译，译文由原生卡片贴在选区原位显示 */
  const triggerSnipTranslate = useCallback(() => triggerSnip(false, true), [triggerSnip])

  useEffect(() => {
    ;(window as unknown as { __onSnipDone?: (st?: { status?: string; do_ocr?: boolean; thumb_data_url?: string; text?: string }) => void }).__onSnipDone = (st) => {
      if (st && st.status === 'done') {
        handleSnipDone(st)
        return
      }
      void checkSnipResult()
    }
  }, [handleSnipDone, checkSnipResult])
  /* 第 315 项：删掉「时间轴」那一整套状态（天数、条数、钉住的那一行）。
     没搜索词时不再渲染 Timeline，直接复用搜索结果的网格/列表 —— 一套布局，
     以后改交互只需要改一处。 */
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

  // 第 327 项：左栏那三块显不显示，是设置里存的（默认都显示）。
  // 一次性取回来即可 —— 设置里改完会同时回调 sbFromSettings，不用再拉一遍。
  useEffect(() => {
    getSettings()
      .then((r) => setSb(sbFromSettings(r)))
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
    // 老文档里还有 ?cfg=1 这种写法，一并认下来，别让链接悄悄失效。
    // translate（第 285 项，悬浮球右键「聚合翻译」）：它不是独立面板，
    // 而是主窗口里的弹层 TranslateModal，所以下面单独走 setTransOpen(true)。
    const m = /[?&]panel=(help|about|settings|ocr|cfg|translate)/.exec(location.search)
    const legacyCfg = /[?&]cfg=1(&|$)/.test(location.search)
    // focus=feedback/health 直接开「体检与报错」弹窗（第 181 项）；
    // focus=why 直接开「为什么搜不到」弹窗（第 187 项）——
    // 这两块都已经不在设置里了，还往设置跳等于绕远路。
    const fm = /[?&]focus=(feedback|why|health)/.exec(location.search)
    if (fm) {
      if (fm[1] === 'why') setWhyOpen(true)
      else setDiagOpen(true)
    }
    if (m && m[1] === 'translate') {
      setTransOpen(true)
    } else if ((m || legacyCfg) && fm?.[1] !== 'feedback' && fm?.[1] !== 'health') {
      setPanel(
        (m && m[1] !== 'cfg' ? m[1] : 'settings') as
          'about' | 'settings' | 'ocr',
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
  // 搜索结果列表的列宽可拖拽（与 Timeline 共用同一套逻辑，但用独立键 sb_cols，
  // 避免两张表的列宽互相覆盖）。列：名称 / 类型 / 大小 / 修改时间（路径是名称下的副行，
  // 拖宽「名称」列即让路径副行显示更全）。
  const { w: colWs, handle: colHandle } = useColResize(
    'sb_cols',
    { name: 220, type: 96, size: 48, mtime: 112 },
    { name: 80, type: 48, size: 44, mtime: 64 },
  )
  const gridCols2 = `16px minmax(80px, ${colWs.name}fr) minmax(48px, ${colWs.type}fr) minmax(44px, ${colWs.size}fr) minmax(64px, ${colWs.mtime}fr)`
  const [menu, setMenu] = useState<{
    x: number
    y: number
    row: Row | { p: string; b?: string; snip?: string }
  } | null>(null)
  const [reverse, setReverse] = useState<string | null>(null)
  const [toast, setToast] = useState('')
  /* 第 322 项：框里的 ext: 和侧栏类型没交集时，空列表那句「没有找到「ext:pdf」」会误导
     （并不是没结果，是两个条件互相排斥）。这里单独记一个标记，空态换成说人话的解释。 */
  const [extClash, setExtClash] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [ocr, setOcr] = useState<OcrBatchStatus | null>(null)
  const [ocrPro, setOcrPro] = useState(false)
  const ocrOpen = panel === 'ocr'
  const seq = useRef(0)
  /* 第 320 项：后端回「索引在忙」时只自动重试一次，别变成死循环 */
  const busyRetry = useRef(false)
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
  // 第 366 项：每 10 秒读一次「整理索引库」的进度。后端把这条路径当只读心跳，
  // 不让它把「用户还在活动」的假象续上（否则空闲迁移永远不会触发）。
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const s = await getCompact()
        if (!alive) return
        const pct =
          s.state === 'busy'
            ? s.total && s.total > 0
              ? Math.min(99, Math.round(((s.done || 0) / s.total) * 100))
              : 0
            : 100
        setCompact({ state: s.state, pct })
      } catch {
        /* 后端忙的时候偶发失败，下次再读 */
      }
    }
    void tick()
    const id = window.setInterval(tick, 10000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
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
    // 第 308 项：索引为空 / 有失联文件夹时要主动说一句话，别让用户对着空白猜
    getIndexHealth().then(setHealth).catch(() => {})
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
  // 第 315 项：这里原有的「时间轴钉住行」状态已删除 —— 没搜索词时也走 shown/curRow，详情只有一个来源
  // 搜索范围：全部 / 只搜文件名 / 只搜正文（正文就是文档内容，不是文件名）
  const [scope, setScope] = useState<'all' | 'name' | 'body'>(
    () => (localStorage.getItem('sb_scope') as 'all' | 'name' | 'body') || 'all',
  )

  // 换配色：立刻生效 + 记到 localStorage（applyPalette 里做）；后端那份由下面的
  // effect 统一报，这里只管切 id —— 别让同一个契约有两个出口。
  const applyTheme = useCallback((id: string) => {
    setPalette(id)
  }, [])

  // 把**整套配色**（7 个具体颜色 + 深浅）报给后端，让原生窗口只认这一份真源。
  // 只报 id 是不够的：用户自定义变量、auto 跟随系统深浅，id 都表达不出来。
  // 截图翻译卡片（snip_card）历史上就吃过这个亏 —— 选了绿主题卡片还是白的。
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const push = () => {
      const systemDark = mq.matches
      void api('/api/settings', {
        method: 'POST',
        body: JSON.stringify({
          palette,
          palette_dark: paletteDark(palette, systemDark),
          palette_vars: paletteVarsOf(palette, systemDark),
        }),
      }).catch(() => {})
    }
    push()
    mq.addEventListener('change', push)
    return () => mq.removeEventListener('change', push)
  }, [palette])

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
    ;(window as unknown as { __syncTheme?: () => void }).__syncTheme = () => { push() }
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
    // 第 229 项四轮：空着打字框点「全部/文件名/正文」也要有反应 —— 走同一管道：
    // /api/files 空关键词=最近文件、/api 空=最近文档（后端配合），scope 照旧
    // 决定请求哪几路。以前直接清空返回，点了筛选没任何反应（用户实测反馈）。
    setBusy(true)
    const timer = setTimeout(async () => {
      try {
        // 只搜正文时给后端加 content: 前缀（已有语法，复用同一套解析）；
        // 只搜文件名时干脆不请求正文那一路，省一次全表扫描。
        //
        // 第 235 项：侧栏选了具体类型时，把类型也翻译成 ext: 语法交给后端。
        // 不这么做的话，60 条名额会被别的类型占满（搜「阿凡达」全是 txt 正文命中，
        // 视频那几条根本挤不进前 60，用户看到的就是「筛选了视频还是文档」）。
        // 第 324 项：文档子分类（PDF/Word/Excel…）换算成扩展名；其余大类照旧。
        const subExts = kind === 'doc' && subType ? (SUB_EXTS[subType] || []) : []
        const kindExts = kind === 'dir' || kind === 'doc' ? subExts : (KIND_EXTS[kind] || [])
        // 图片/视频/音频里没有可检索的正文 —— 这三类压根不该请求正文那一路，
        // 否则「正文」命中的 txt 会照样混进来。
        const noBodyKind = kind === 'img' || kind === 'video' || kind === 'audio'
        /* 第 322 项：框里自己写了 ext:xxx，侧栏又点了类型 —— 以前两段 ext: 一起丢给后端，
           后端只认最后一个，于是「ext:pdf 再点图片」搜出一屏图片（用户实测截图）。
           现在两处取交集：ext:pdf ∩ 图片 = 空，就明确说「没有交集」，而不是给错结果。 */
        const typedExts = (q.match(/(^|\s)ext:[^\s]+/gi) || [])
          .flatMap((s) => s.replace(/^\s*ext:/i, '').split(','))
          .map((s) => s.trim().replace(/^\./, '').toLowerCase())
          .filter(Boolean)
        const noExtQ = q.replace(/(^|\s)ext:[^\s]+/gi, ' ').replace(/\s+/g, ' ').trim()
        let effExts = kindExts
        let extConflict = false
        if (kindExts.length && typedExts.length) {
          effExts = kindExts.filter((e) => typedExts.includes(e))
          extConflict = !effExts.length
        }
        const filesQ = extConflict ? '' : effExts.length ? `${noExtQ} ext:${effExts.join(',')}` : q
        const bodyQ = scope === 'body' ? `${q} content:` : q
        /* 第 327 项：左栏点「文件夹」走 /api/dirs —— 目录在 dirs 表，
           以前这条路照样查 files 表，于是数字 41072、点进去一片空白。 */
        const dirMode = kind === 'dir'
        const [f, b] = extConflict
          ? [{ items: [] as Row[], st: '', busy: false }, { hits: [] as Hit[], busy: false }]
          : await Promise.all([
          scope === 'body' || dirMode
            ? (dirMode
                ? api<{ items: Row[]; st: string; busy?: boolean }>(
                    `/api/dirs?q=${encodeURIComponent(q)}&n=200` +
                      (folder ? `&pf=${encodeURIComponent(folder)}` : ''),
                  )
                : Promise.resolve({ items: [] as Row[], st: '', busy: false }))
            : api<{ items: Row[]; st: string; busy?: boolean }>(
                `/api/files?q=${encodeURIComponent(filesQ)}&n=200&s=${sort}` +
                  (folder ? `&pf=${encodeURIComponent(folder)}` : ''),
              ),
          // 第 335 项：**空关键词不搜正文**。后端 /api?q= 会当「最近文档」回 60 条正文命中，
          // 于是首页凭空多出一句「以下 60 条命中在文档正文里」，用户压根没搜索。
          scope === 'name' || noBodyKind || !q.trim()
            ? Promise.resolve({ hits: [] as Hit[], busy: false })
            : api<{ hits: Hit[]; busy?: boolean }>(`/api?q=${encodeURIComponent(bodyQ)}`),
        ])
        if (my !== seq.current) return
        if (extConflict) {
          // 第 322 项：框里的 ext: 和侧栏类型没有交集 —— 空列表 + 一句人话，别默默给错结果
          setExtClash(true)
          setToast(t('ext_no_match'))
          setRows([])
          setPicked(0)
          setPickP('')
          return
        }
        setExtClash(false)
        /* 第 320 项：后端等锁超时会回 st='busy' / busy=true（原来是一直挂着，
           界面转圈 90 秒以上）。这时不能把空列表当成「没有结果」——
           提示一句「索引在忙」，并自动重试一次（只一次，避免死循环）。 */
        if (f.busy || f.st === 'busy' || b.busy) {
          setToast(t('db_busy'))
          if (!busyRetry.current) {
            busyRetry.current = true
            window.setTimeout(() => setReload((n) => n + 1), 2500)
          }
          setRows([])
          return
        }
        busyRetry.current = false
        const bodyRows: Row[] = (b.hits || []).map((h) => ({
          ...h,
          n: h.p.split(/[\\/]/).pop() || h.p,
          ty: 'hit',
          // 正文命中让位给文件名命中：搜「陈」时文件名里有陈的排前面
          r: 6,
          e: (h.p.match(/\.([A-Za-z0-9]{1,6})$/) || [, ''])[1],
        }))
        // 第 303 项：两段各自排序（文件名命中 → 正文命中），分隔线固定落在中间。
        // 以前整串一起排，「名称」排序时正文结果会插进文件名结果里，看着像乱序。
        // 第 316 项：首页要「够看」，60 条太短（时间轴时代是每天前 40、共 467）。
        // 后端空关键词本来就回最近 400 条，这里截到 200 条：既比原来长得多，
        // 又不会让网格一次点开两百多张缩略图（缩略图另有 lazy 加载兜底）。
        // 第 334 项：点列头排序（名称/类型/大小/修改时间）叠加在原来的 sort 之上；
        // 没点过列头（sortCol=null）就还是「相关度/最新/最大…」那一套。
        // 第 335 项：列头排序必须**分段**做（mergeHits 里），不能对拼好的整串再排 ——
        // 否则正文命中会被「按大小排」打散进文件名结果里，分隔线一条接一条地重复。
        setRows(mergeHits((f.items || []) as Row[], bodyRows, sort, sortCol).slice(0, 200))
        setPicked(0)
        setPickP('')
      } catch {
        if (my === seq.current) setRows([])
      } finally {
        if (my === seq.current) setBusy(false)
      }
    }, 120)
    return () => clearTimeout(timer)
  }, [q, sort, reload, scope, kind, subType, folder, sortCol])

  /* 第 324 项：左栏计数。跟着关键词走（用户改词后 PDF 25 变成 3，点进去才对得上），
     但要防抖 + 只在停手后才发 —— 每敲一个字都跑一次全库 GROUP BY 是浪费。
     q 原样交给后端：语法（ext:/dm:/-排除）在 parse_qsyn 里统一剥，前端不重复解析。 */
  useEffect(() => {
    const tm = window.setTimeout(() => {
      api<Facets>(`/api/facets?q=${encodeURIComponent(q)}`)
        .then((f) => setFacets(f && f.ok !== false ? f : {}))
        .catch(() => {})
    }, 600)
    return () => window.clearTimeout(tm)
  }, [q, reload])

  // 侧栏筛选：一屏只有几十行，本地先过一遍最省事。但**后端也收到了同一份 ext 约束**
  // （见搜索 effect 里的 kindExts）——本地这层只负责兜底与目录行，别指望它救场：
  // 以前只有这一层，而且缺 video/audio，选「视频」等于没筛，正文命中的 txt 全在。
  const shown = useMemo(() => {
    // 第 316 项：时间筛选用「日历天」，不用滚动 24 小时。
    // 以前 `days=1` 算的是「最近 24 小时」，而侧栏写的是「今天」——
    // 半夜改的文件第二天早上还算「今天」，用户会觉得筛选没生效。
    const midnight = new Date()
    midnight.setHours(0, 0, 0, 0)
    // 1 → 今天 00:00；7 → 含今天在内的 7 个日历天
    const cutoff = days > 0 ? midnight.getTime() / 1000 - (days - 1) * 86400 : 0
    const exts = kind === 'doc' && subType ? (SUB_EXTS[subType] || []) : (KIND_EXTS[kind] || [])
    return rows.filter((r) => {
      const e = String(r.e || r.p.split('.').pop() || '').replace(/^\./, '').toLowerCase()
      if (folder && !String(r.p || '').toLowerCase().startsWith(folder.toLowerCase())) return false
      if (kind === 'dir' && r.t !== 'dir') return false
      if (kind !== 'all' && kind !== 'dir') {
        if (r.t === 'dir') return false
        if (!exts.includes(e)) return false
      }
      if (days > 0 && (!r.m || r.m < cutoff)) return false
      return true
    })
  }, [rows, kind, days, subType, folder])

  // 侧栏筛选把行数砍短后，picked 要跟着收回来：
  // 否则高亮停在一条已经不存在的行上，回车就是打开错的文件（或什么都不打开）。
  useEffect(() => {
    setPicked((i) => Math.min(i, Math.max(0, shown.length - 1)))
  }, [shown.length])

  /* 第 318 项：悬停不再预览（用户原话「鼠标一动，预览一直跳」），只认单击。
     314 时期这里有个 160ms 去抖的悬停钉选，扫过一排卡片详情会跟着乱跳。 */
  const pinIdx = pickP ? shown.findIndex((r) => r.p === pickP) : -1
  const curIdx = pinIdx >= 0 ? pinIdx : Math.min(picked, Math.max(0, shown.length - 1))
  const curRow = shown[curIdx]
  /* 第 315 项：详情只有一个来源 —— 现算出来的那一行。
     314 时期这里还要在「时间轴行」和「结果行」之间二选一，因为首页有第二套布局；
     现在首页也走 shown，这层分叉整个去掉了（少一个能各自跑偏的地方）。 */
  const activeRow = curRow
  const pinRow = (p?: string) => {
    if (!p) return
    setPickP(p)
  }
  /* 第 318 项：灯箱里翻上一张 / 下一张「图片」（pdf 也算），
     跳过文档、文件夹、exe，否则点一下箭头变成看 txt 的行。 */
  const stepImage = (d: number) => {
    const n = shown.length
    if (!n) return
    const isPic = (r?: Row) => {
      if (!r || r.t === 'dir') return false
      const e = String(r.e || r.p.split('.').pop() || '').replace(/^\./, '').toLowerCase()
      return THUMB_EXT.includes(e)
    }
    let i = curIdx
    for (let k = 0; k < n; k++) {
      i = (i + d + n) % n
      if (isPic(shown[i])) {
        setPicked(i)
        pinRow(shown[i].p)
        return
      }
    }
  }

  const cycleSort = useCallback(() => {
    // Ctrl+S 换排序时把「点列头」的选择清掉，否则用户按了快捷键却看不出变化。
    setSortCol(null)
    localStorage.removeItem('sb_sortcol')
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
        const r = curRow
        if (!r) return
        const bridge = (
          window as unknown as { pywebview?: { api?: { reveal?: (p: string) => Promise<boolean> } } }
        ).pywebview?.api
        // 帮助面板写着「Ctrl+Enter 定位」，浏览器里没有这个能力时必须说一声，
        // 否则用户只会觉得快捷键坏了（可选链 + catch 什么都捕获不到）。
        if (!bridge?.reveal) {
          setToast(t('ctx_reveal_web'))
          return
        }
        // 第 319 项：桥在、但没定位成功 → 不能再说「浏览器里没有定位」
        void bridge.reveal(r.p).then((ok) => { if (!ok) setToast(t('ctx_reveal_fail')) }, () => setToast(t('ctx_reveal_fail')))
        return
      }
      // 第 194 项：把当前选中项所在目录送进正在使用的打开/保存对话框。
      // 和 Ctrl+Enter 一样，搜索框里按也算，别被 typing 守卫吃掉。
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'g') {
        e.preventDefault()
        const r = curRow || shown[0]
        if (!r) return
        void (async () => {
          try {
            const d = await dialogJump(r.p)
            setToast(d.ok ? t('qs_jump_ok') : d.reason === 'no_dialog' ? t('qs_no_dialog') : t('qs_jump_fail'))
          } catch {
            setToast(t('qs_jump_fail'))
          }
        })()
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
        if (previewImgOpen) {
          setPreviewImgOpen(false)
          return
        }
        if (pastedImg) {
          setPastedImg(null)
          return
        }
        setQ('')
        qEl.current?.focus()
        return
      }
      if (pastedImg) {
        if (e.key === 'Enter') {
          e.preventDefault()
          setPreviewImgOpen(false)
          void doOcrPastedImg()
          return
        }
        if (e.key === 'Delete') {
          e.preventDefault()
          setPastedImg(null)
          setPreviewImgOpen(false)
          return
        }
      }
      if (typing) return
      if (!shown.length) return
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const next = Math.min(shown.length - 1, Math.max(0, curIdx + (e.key === 'ArrowDown' ? 1 : -1)))
        setPicked(next)
        setPickP(shown[next]?.p || '')
      } else if (e.key === 'Enter') {
        e.preventDefault()
        // 侧栏筛选（图片/文档…）会在本地把行数砍短，选中行要现算，别拿旧下标硬套 ——
        // 以前这里直接 shown[picked].p 会 TypeError，表现是「按回车什么都没发生」。
        const r = curRow
        if (r) openRow(r.p)
      } else if ((e.ctrlKey || e.metaKey) && /^[1-9]$/.test(e.key)) {
        e.preventDefault()
        const r = shown[Number(e.key) - 1]
        if (r) openRow(r.p)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [shown, picked, pickP, cycleSort, panel, menu, reverse, pastedImg, previewImgOpen])

  const menuItems = (row: Row | { p: string; b?: string; snip?: string }): MenuItem[] => {
    const p = row.p
    const fn = baseName(p)
    const dir = dirName(p)
    const rawSnip = (row as Row).b || (row as Hit).snip || ''
    const snip = String(rawSnip).replace(/[\x01\x02]/g, '').replace(/<\/?mark>/gi, '').trim()

    const items: MenuItem[] = [
      { label: t('ctx_open'), onClick: () => void openRow(p) },
      {
        label: t('ctx_dialog'),
        onClick: async () => {
          try {
            const d = await dialogJump(p)
            setToast(d.ok ? t('qs_jump_ok') : d.reason === 'no_dialog' ? t('qs_no_dialog') : t('qs_jump_fail'))
          } catch {
            setToast(t('qs_jump_fail'))
          }
        },
      },
      {
        label: t('ctx_reveal'),
        hint: 'Ctrl+Enter',
        onClick: async () => {
          const bridge = (window as unknown as { pywebview?: { api?: { reveal?: (p: string) => Promise<boolean> } } })
            .pywebview?.api
          // 没有桥 = 浏览器里跑，必须明确告诉用户「这里没有定位」。
          // 以前直接 `bridge?.reveal?.(...)`：可选链什么都不断言，UI 上就是点了完全没反应。
          if (!bridge?.reveal) {
            setToast(t('ctx_reveal_web'))
            return
          }
          try {
            const ok = await bridge.reveal(p)
            if (!ok) setToast(t('ctx_reveal_fail'))
          } catch {
            setToast(t('ctx_reveal_fail'))
          }
        },
      },
      {
        label: t('ctx_copy'),
        onClick: async () => {
          try {
            await navigator.clipboard.writeText(p)
            setToast(t('ctx_copy_ok'))
          } catch {
            // 剪贴板被拒（没焦点/没权限）时不能继续说「已复制」——用户会去粘贴一个空
            setToast(t('ctx_copy_fail'))
          }
        },
      },
      {
        label: t('ctx_copy_name'),
        onClick: async () => {
          try {
            await navigator.clipboard.writeText(fn)
            setToast(t('ctx_copy_name_ok'))
          } catch {
            setToast(t('ctx_copy_fail'))
          }
        },
      },
      ...(dir
        ? [
            {
              label: t('ctx_copy_dir'),
              onClick: async () => {
                try {
                  await navigator.clipboard.writeText(dir)
                  setToast(t('ctx_copy_dir_ok'))
                } catch {
                  setToast(t('ctx_copy_fail'))
                }
              },
            },
          ]
        : []),
      ...(snip
        ? [
            {
              label: t('ctx_copy_snip'),
              onClick: async () => {
                try {
                  await navigator.clipboard.writeText(snip)
                  setToast(t('ctx_copy_snip_ok'))
                } catch {
                  setToast(t('ctx_copy_fail'))
                }
              },
            },
          ]
        : []),
      {
        label: t('ctx_pin'),
        onClick: async () => {
          try {
            const r = await fetch(`/api/app_pref?p=${encodeURIComponent(p)}&m=pin`)
            const d = (await r.json()) as { ok?: boolean }
            setToast(d.ok ? t('ctx_pin_ok') : t('ctx_pin_fail'))
            setReload((x) => x + 1)
          } catch {
            setToast(t('ctx_pin_fail'))
          }
        },
      },
      {
        label: t('ctx_hide'),
        onClick: async () => {
          try {
            const r = await fetch(`/api/app_pref?p=${encodeURIComponent(p)}&m=hide`)
            const d = (await r.json()) as { ok?: boolean }
            setToast(d.ok ? t('ctx_hide_ok') : t('ctx_hide_fail'))
            setReload((x) => x + 1)
          } catch {
            setToast(t('ctx_hide_fail'))
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
              body: JSON.stringify({ paths: [p] }),
            })
            const d = (await r.json()) as { ok?: boolean; zip?: string; n?: number }
            setToast(d.ok ? t('zip_done').replace('{n}', String(d.n)) : t('ctx_zip_fail'))
          } catch {
            setToast(t('ctx_zip_fail'))
          }
        },
      },
    ]
    return items
  }

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

  // 第 362 项：壁纸按界面生效范围铺 —— 设置抽屉里不想见就关掉，不影响详情搜索。
  const inDrawer = panel === 'settings' || panel === 'about'
  const showWall = wall.on && (inDrawer ? wall.scope_settings : wall.scope_main)

  // 第 195 项：壁纸遮罩随可见度反比——5% 时 0.77（字优先），100% 时 0.22（图优先）。
  // 文字可读性交给 .sb-readable 的 text-shadow，不再靠一层厚底把图压没。
  useEffect(() => {
    const s = showWall ? (0.80 - 0.48 * Math.min(100, Math.max(0, wall.opacity)) / 100) : 0.72
    document.documentElement.style.setProperty('--sb-scrim', s.toFixed(3))
  }, [showWall, wall])

  return (
    <>
      {/* 全局壁纸（第 191 项）：铺在界面底下。开着时根容器底色去掉，
          侧栏/顶栏/列表（本来都是透明的）就能透出图片；卡片和弹窗是实色，文字不受影响。 */}
      {showWall && <Wallpaper wall={wall} />}
      <div
        key={langTick}
        className={`relative z-10 flex h-full flex-col bg-bg text-ink${showWall ? ' wall-on' : ''}`}
        style={showWall ? { background: 'transparent' } : undefined}
      >
      {/* 自绘标题栏（第 180 项）：只有 /api/ping.titlebar 为真才画 —— 那时 Python
          已经把系统标题栏摘掉了，画不出来就是没有标题栏的死窗口 */}
      {customTitle && (
        <TitleBar
          onMini={openMini}
          onDiag={() => setDiagOpen(true)}
          onSupport={() => setSupportOpen(true)}
          onSnip={triggerSnipPure}
          onSnipTranslate={triggerSnipTranslate}
          onClipboard={() => setClipOpen(true)}
          onTranslate={() => setTransOpen(true)}
          onOpenAbout={() => setPanel('about')}
          ver={ver}
        />
      )}
      {/* 顶栏 */}
      <header className="sb-readable flex flex-col gap-2 px-4 pb-2 pt-3">
        <div className="relative flex items-center">
          {pastedImg ? (
            <div
              className="absolute left-2.5 z-10 flex cursor-pointer items-center gap-1.5 rounded-lg border border-line bg-card/90 px-1.5 py-1 shadow-sm transition-colors hover:border-accent"
              onClick={() => setPreviewImgOpen(true)}
              title={t('img_zoom_title')}
            >
              <img src={pastedImg} alt="pasted" className="h-6 w-6 rounded object-cover" />
              {pastedImgLoading && <div className="h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  setPastedImg(null)
                  setPreviewImgOpen(false)
                }}
                className="text-sub hover:text-ink text-xs font-bold leading-none px-0.5"
                title={t('img_del_title')}
              >
                ×
              </button>
            </div>
          ) : (
            <IconSearch size={18} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-sub" />
          )}
          <input
            ref={qEl}
            value={q}
            autoFocus
            spellCheck={false}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && pastedImg) {
                e.preventDefault()
                setPreviewImgOpen(false)
                void doOcrPastedImg()
              } else if (e.key === 'Delete' && pastedImg) {
                e.preventDefault()
                setPastedImg(null)
                setPreviewImgOpen(false)
              } else if (e.key === 'Backspace' && !q && pastedImg) {
                e.preventDefault()
                setPastedImg(null)
                setPreviewImgOpen(false)
              } else if (e.key === 'Escape' && pastedImg) {
                e.preventDefault()
                if (previewImgOpen) {
                  setPreviewImgOpen(false)
                } else {
                  setPastedImg(null)
                }
              }
            }}
            placeholder={pastedImg ? t('app_paste_ocr_ph') : t('placeholder')}
            className={`h-12 w-full rounded-xl border border-line bg-card pr-10 text-[15px] outline-none focus:border-accent focus:ring-2 focus:ring-accent/15 ${
              pastedImg ? 'pl-20' : 'pl-11'
            }`}
          />
          {q && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-sub hover:text-ink"
              onClick={() => {
                setQ('')
                if (pastedImg) setPastedImg(null)
              }}
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
        {/* 第 315 项：原来这里是一段「时间轴：最近 14 天 · N 个」+ 7/30 天快捷按钮，
            只有没搜索词时才出现 —— 于是首页和搜索页的顶栏也不一样。现在删掉：
            时间范围统一由左侧栏那五个选项（全部/今天/7 天/30 天/一年）控制。 */}
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
          onClick={openHelp}
          title={t('help')}
          aria-label={t('help')} data-act="help"
        >
          <IconHelp size={15} />
        </button>
        {/* 快搜：有自绘标题栏时它已经在标题栏里，这里不重复（第 180 项） */}
        {!customTitle && (
          <button
            data-act="snip"
            onClick={triggerSnipPure}
            title={t('snip_btn')}
            aria-label={t('snip_btn')}
            className="grid h-9 w-9 place-items-center rounded-lg border border-line text-sub hover:text-ink"
          >
            <IconSnip size={15} />
          </button>
        )}
        {!customTitle && (
          <button
            data-act="clipboard"
            onClick={() => setClipOpen(true)}
            title={t('clip_title')}
            aria-label={t('clip_title')}
            className="grid h-9 w-9 place-items-center rounded-lg border border-line text-sub hover:text-ink"
          >
            <IconClipboard size={15} />
          </button>
        )}
        {!customTitle && (
          <button
            data-act="translate"
            onClick={() => setTransOpen(true)}
            title={t('trans_title')}
            aria-label={t('trans_title')}
            className="grid h-9 w-9 place-items-center rounded-lg border border-line text-sub hover:text-ink"
          >
            <IconTranslate size={15} />
          </button>
        )}
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
      <aside className="sb-readable flex w-[172px] shrink-0 flex-col border-r border-line px-2.5 pb-3 text-[13px]">
        {/* 第 376 项：滚动只给「类型 / 时间」这两组长筛选，底下三颗入口（帮助 / 设置 / 关于）
            钉在侧栏底部。以前整条 aside 一起 overflow-auto，窗口一矮 —— 1366×768 笔记本、
            1080p 开 150%（逻辑屏只有 720 高）—— 「关于」就掉到折叠线以下，得先滚一下才看得见，
            用户装完第一件事想找「关于」反而找不到。 */}
        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        {sb.types && <div className="px-1.5 pb-1.5 pt-2 text-[12px] text-sub">{t('type')}</div>}
        {/* 第 324 项：每一类后面跟当前关键词下的条数（gety 同款）。计数只在停手
            600ms 后刷新一次，打字过程中不会一抖一抖地跳。
            第 327 项：整块可以在设置里关掉（sb_types）。 */}
        {sb.types && (() => {
          const cnt = (k: string) => (facets.kinds || []).find((x) => x.k === k)?.n
          return ([
            ['all', t('kinds'), <IconGrid size={16} />],
            ['dir', t('kind_dir'), <IconFolder size={16} />],
            ['doc', t('kind_doc'), <IconDoc size={16} />],
            ['img', t('kind_img'), <IconImage size={16} />],
            ['video', t('kind_video'), <IconVideo size={16} />],
            ['audio', t('kind_audio'), <IconAudio size={16} />],
            ['zip', t('kind_zip'), <IconZip size={16} />],
            ['app', t('kind_app'), <IconApp size={16} />],
          ] as const).map(([k, label, ic]) => {
            const n = k === 'all' ? facets.total : cnt(k)
            return (
              <Fragment key={k}>
                <button
                  onClick={() => {
                    // 第 331 项：再点一次已选中的类型 = 收回（回到「全部」）。
                    // 以前同一类型再点是空操作，文档子分类只能靠点别的类型挤掉。
                    const nk = nextKind(kind, k)
                    setKind(nk)
                    if (nk !== 'doc') setSubType('')
                  }}
                  className={`mb-1 flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13.5px] ${
                    kind === k ? 'bg-card text-ink' : 'text-sub hover:text-ink'
                  }`}
                >
                  <span className={kind === k ? 'text-accent' : ''}>{ic}</span>
                  <span className="min-w-0 flex-1 truncate">{label}</span>
                  {sb.counts && n != null && n > 0 && (
                    <span className="shrink-0 tabular-nums text-[11px] text-sub">{n}</span>
                  )}
                </button>
                {/* 文档子分类：点了「文档」才展开，平时不占地方 */}
                {k === 'doc' && kind === 'doc' && (
                  <div className="mb-1 ml-1.5 border-l border-line pl-1.5">
                    {SUBTYPES.map(([sk]) => {
                      const sn = (facets.subs || []).find((x) => x.k === sk)?.n
                      const on = subType === sk
                      return (
                        <button
                          key={sk}
                          onClick={() => setSubType(on ? '' : sk)}
                          className={`mb-0.5 flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[12.5px] ${
                            on ? 'bg-card text-ink' : 'text-sub hover:text-ink'
                          }`}
                        >
                          <span className="min-w-0 flex-1 truncate">{t('sub_' + sk)}</span>
                          {sb.counts && sn != null && (
                            <span className="shrink-0 tabular-nums text-[11px] text-sub">{sn}</span>
                          )}
                        </button>
                      )
                    })}
                  </div>
                )}
              </Fragment>
            )
          })
        })()}
        {/* 文件夹子分类：按索引范围下的第一层目录聚类（几十条以上的才露头，
            最多 6 个）。点一个就等于把搜索收进这个目录，再点一次取消。 */}
        {sb.folders && (facets.folders || []).length > 0 && (
          <>
            <div className="mx-1.5 mt-2.5 border-t border-line pb-1.5 pt-2.5 text-[12px] text-sub">
              {t('folders')}
            </div>
            {(facets.folders || []).slice(0, 6).map((f) => {
              const on = folder === f.p
              return (
                <button
                  key={f.p}
                  title={f.p}
                  onClick={() => setFolder(on ? '' : (f.p || ''))}
                  className={`mb-1 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] ${
                    on ? 'bg-card text-ink' : 'text-sub hover:text-ink'
                  }`}
                >
                  <span className={on ? 'text-accent' : 'text-sub'}>
                    <IconFolder size={14} />
                  </span>
                  <span className="min-w-0 flex-1 truncate">{f.name}</span>
                  {sb.counts && (
                    <span className="shrink-0 tabular-nums text-[11px] text-sub">{f.n}</span>
                  )}
                </button>
              )
            })}
          </>
        )}
        {sb.time && (
          <>
            <div className="mx-1.5 mt-2.5 border-t border-line pb-1.5 pt-2.5 text-[12px] text-sub">{t('time')}</div>
            {([[0, t('days_all')], [1, t('today')], [7, t('d7')], [30, t('d30')], [365, t('year')]] as const).map(([d, label]) => {
              /* 第 315 项：首页与搜索页共用一套布局之后，时间筛选也只剩一套状态（days）。
                 以前无搜索时要另外映射成「时间轴天数」，「全部」还得当成 3650 天，两套状态各管一半。 */
              const active = days === d
              return (
                <button
                  key={d}
                  onClick={() => setDays(d)}
                  className={`mb-1 flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13.5px] ${
                    active ? 'bg-card text-ink' : 'text-sub hover:text-ink'
                  }`}
                >
                  <span className={active ? 'text-accent' : ''}>
                    <IconClock size={16} />
                  </span>
                  {label}
                </button>
              )
            })}
          </>
        )}
        </div>
        {/* 第 376 项：底部这一颗不参与滚动，钉在侧栏底部 —— 任何窗口高度下都点得到「设置」。
            2026-11-21 用户反馈：「使用帮助」「关于」都并进设置抽屉了（设置里左侧导航
            有独立的 help / about 两页），侧栏再各放一颗等于同一个东西开两个门，
            所以这里只留「设置」一颗，其余从这条侧栏删掉。 */}
        <div className="shrink-0">
        <div className="mx-1.5 mb-2 mt-3 border-t border-line" />
        <button
          onClick={() => setPanel('settings')}
          className="mb-1 flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13.5px] text-sub hover:text-ink"
        >
          <IconGear size={16} />
          {t('settings')}
        </button>
        </div>
        {/* 文件和正文数量不在这里重复：右下角状态栏一直显示（第 173 项） */}
      </aside>

      {/* 列表 */}
      {/* 外层只管布局、不滚；真正的滚动交给 main。原因：时间轴的列头在 main 里
          `sticky top-0`，若滚动发生在 main 的**父级**，sticky 会跟着内容一起走
          （最近 14 天那种没有搜索词时列头一滚就没，第 179 项用户实测）。 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* 第 308 项：索引为空 / 文件夹失联时主动解释，并给两个能立刻点的动作 */}
        {health?.zero && (
          <div className="flex shrink-0 items-start gap-3 border-b border-line bg-card/60 px-3 py-2 text-[12px] leading-5">
            <div className="min-w-0 flex-1">
              {health.zero && (
                <>
                  <b className="text-ink">{t('idx_zero_title')}</b>
                  <div className="text-sub">{t('idx_zero_tip')}</div>
                </>
              )}
              {health.paused > 0 && (
                <div className="text-sub">{t('idx_paused').replace('{n}', String(health.paused))}</div>
              )}
              {scanning && <div className="text-accent">{t('idx_scanning')}</div>}
            </div>
            <div className="flex shrink-0 gap-1.5">
              <button
                onClick={() => setPanel('settings')}
                className="rounded-md border border-line px-2 py-1 hover:bg-card"
              >
                {t('idx_scope_btn')}
              </button>
              <button
                disabled={scanning}
                onClick={() => {
                  setScanning(true)
                  startIndexUpdate()
                    .then(() => {
                      // 扫描是后台线程：给它几秒，再把快照和计数刷回来
                      window.setTimeout(() => {
                        getIndexHealth().then(setHealth).catch(() => {})
                        api<Stats>('/api/stats').then(setStats).catch(() => {})
                        setScanning(false)
                      }, 4000)
                    })
                    .catch(() => setScanning(false))
                }}
                className="rounded-md border border-line px-2 py-1 hover:bg-card disabled:opacity-60"
              >
                {t('idx_scan')}
              </button>
            </div>
          </div>
        )}

        {/* 第 315 项：列头不再只在「有搜索词」时出现 —— 首页和搜索页必须是同一张表。
            列表视图才需要列头，网格视图没有列，所以只在 view==='list' 时画。 */}
        {view === 'list' && (
          <div
            className="z-[1] grid w-full shrink-0 items-center gap-x-2 border-b border-line bg-bg px-2 py-1 text-[11px] text-sub"
            style={{ gridTemplateColumns: gridCols2 }}
          >
            <span className="w-4 shrink-0" />
            <span className="relative min-w-0 pr-2">
              <button
                type="button"
                onClick={() => toggleSortCol('name')}
                className="flex w-full min-w-0 items-center gap-1 truncate text-left hover:text-fg"
              >
                <span className="min-w-0 truncate">{t('col_name')}</span>
                {sortCol?.key === 'name' && <span className="shrink-0 text-[9px]">{sortCol.dir === 1 ? '▲' : '▼'}</span>}
              </button>
              {colHandle('name', t('col_resize_hint'))}
            </span>
            <span className="relative min-w-0 pr-2">
              <button
                type="button"
                onClick={() => toggleSortCol('type')}
                className="flex w-full min-w-0 items-center gap-1 truncate text-left hover:text-fg"
              >
                <span className="min-w-0 truncate">{t('col_type')}</span>
                {sortCol?.key === 'type' && <span className="shrink-0 text-[9px]">{sortCol.dir === 1 ? '▲' : '▼'}</span>}
              </button>
              {colHandle('type', t('col_resize_hint'))}
            </span>
            <span className="relative min-w-0 pr-2 text-left">
              <button
                type="button"
                onClick={() => toggleSortCol('size')}
                className="flex w-full min-w-0 items-center gap-1 truncate text-left hover:text-fg"
              >
                <span className="min-w-0 truncate">{t('col_size')}</span>
                {sortCol?.key === 'size' && <span className="shrink-0 text-[9px]">{sortCol.dir === 1 ? '▲' : '▼'}</span>}
              </button>
              {colHandle('size', t('col_resize_hint'))}
            </span>
            <span className="relative min-w-0 pr-2 text-left">
              <button
                type="button"
                onClick={() => toggleSortCol('mtime')}
                className="flex w-full min-w-0 items-center gap-1 truncate text-left hover:text-fg"
              >
                <span className="min-w-0 truncate">{t('col_mtime')}</span>
                {sortCol?.key === 'mtime' && <span className="shrink-0 text-[9px]">{sortCol.dir === 1 ? '▲' : '▼'}</span>}
              </button>
              {colHandle('mtime', t('col_resize_hint'))}
            </span>
          </div>
        )}
      <main className="sb-readable min-h-0 flex-1 overflow-auto px-3">
        {/* 第 366 项：正在整理索引库时挂一条横幅。以前这一步在后台独占写锁、搜索报忙，
            用户只看到「索引在忙」的 toast，很像故障；现在能看见「在做什么、到几成」。 */}
        {compact.state === 'busy' && (
          <div className="mx-2 mt-2 flex items-center gap-2 rounded-md border border-accent/40 bg-accent/10 px-3 py-1.5 text-[12px] text-accent">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
            <span>{t('compact_banner').replace('{pct}', String(compact.pct))}</span>
          </div>
        )}
        {/* 第 315 项：删掉时间轴组件那一块。首页（无搜索词）原来走它，
            搜索后走下面的网格/列表，两套 JSX 各自管点击语义 —— 所以每次都只修好一半。
            现在首页也走 shown（空关键词 = 最近文件）这一套，只剩一处要维护。 */}

        {/* 没搜索词时给一句说明，免得「最近改过的文件」被当成搜索结果；
            第 316 项：选了类型/时间就跟着说明，让人知道这一屏已经被筛过。 */}
        {!q.trim() && shown.length > 0 && (
          <div className="shrink-0 px-2 pt-2 text-[11px] text-sub">
            {kind === 'all' ? t('ob_mock_recent') : t(kind === 'dir' ? 'kind_dir' : 'kind_' + kind)}
            {days > 0 ? ` · ${t(days === 1 ? 'today' : days === 7 ? 'd7' : days === 30 ? 'd30' : 'year')}` : ''}
            {' · '}
            {shown.length}
          </div>
        )}

        {q.trim() && !busy && shown.length === 0 && (
          <div className="grid h-full place-items-center text-center text-sub">
            <div>
              <div className="text-[15px] text-ink">
                {extClash ? t('ext_no_match') : t('empty_none').replace('{q}', syn.q || q)}
              </div>
              <div className="mt-1 text-[12px]">
                {extClash ? t('ext_no_match_hint') : t('empty_hint')}
              </div>
              {/* 第 308 项：搜不到时给个直接入口，别再让人去帮助页翻 */}
              <button
                onClick={() => setWhyOpen(true)}
                className="mt-2 rounded-md border border-line px-2.5 py-1 text-[12px] hover:bg-card"
              >
                {t('help_why')}
              </button>
              {health && health.paused > 0 && (
                <div className="mt-1.5 text-[11px]">
                  {t('idx_paused').replace('{n}', String(health.paused))}
                </div>
              )}
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
                  onClick={() => { setPicked(i); pinRow(r.p) }}
                  onDoubleClick={() => openRow(r.p)}
                  onContextMenu={(e) => {
                    e.preventDefault()
                    setPicked(i)
                    pinRow(r.p)
                    setMenu({ x: e.clientX, y: e.clientY, row: r })
                  }}
                  className={`cursor-default rounded-lg border p-1.5 ${
                    i === curIdx ? 'border-accent bg-card' : 'border-transparent hover:bg-card/60'
                  }`}
                >
                  <div className="mb-1 grid h-[86px] place-items-center overflow-hidden rounded bg-card/60 text-sub">
                    {THUMB_EXT.includes(ext) ? (
                      <img
                        src={`/api/thumb?p=${encodeURIComponent(r.p)}&s=1`}
                        alt=""
                        /* 第 316 项：首页放到 200 条，缩略图必须跟着滚才取，
                           否则一进页面就是 200 个 /api/thumb 并发（313 那次「未响应」的教训）。 */
                        loading="lazy"
                        decoding="async"
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
                      <Hi text={midName(String(r.dn || r.n || r.p.split(/[\\/]/).pop() || ''), 16)} q={q} />
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
            // 第 335 项：数「这一屏里剩下的」正文命中，别用全量 —— 截到 200 条时数字会对不上
            const hitCount = shown.slice(i).filter((x) => x.ty === 'hit').length
            const isApp = APP_EXTS.includes(String(r.e || '').toLowerCase())
            return (
              <div key={r.p + i}>
                <div
                  onContextMenu={(e) => {
                    e.preventDefault()
                    setPicked(i)
                    pinRow(r.p)
                    setMenu({ x: e.clientX, y: e.clientY, row: r })
                  }}
                  onDoubleClick={() => openRow(r.p)}
                  onClick={() => { setPicked(i); pinRow(r.p) }}
                  className={`grid w-full cursor-default items-center gap-x-2 rounded-md px-2 py-[5px] ${
                    i === curIdx ? 'bg-card' : 'hover:bg-card/60'
                  }`}
                  style={{ gridTemplateColumns: gridCols2 }}
                >
                  <span className="w-4 shrink-0 text-sub">
                    {r.ty === 'hit' ? <IconDoc size={15} /> : <RowIcon row={r} />}
                  </span>
                  <span className="min-w-0" title={r.dn || r.n}>
                    <span className="block truncate">
                      <Hi text={midName(String(r.dn || r.n || r.p.split(/[\\/]/).pop() || ''), 80)} q={q} />
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
                      className="min-w-0 truncate text-left text-[11px] text-accent hover:underline"
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
                    <span className="min-w-0 truncate text-[11px] text-sub">{typeLabel(r)}</span>
                  )}
                  {/* 文件夹没有「文件大小」这回事：os.stat 给的是目录元数据（4 KB 之类），
                      显示出来只会让人以为索引记错了。第 236 项用户截图追出来的。 */}
                  <span className="min-w-0 text-left text-[11px] text-sub">
                    {r.t === 'dir' ? '' : fmtSize(r.s)}
                  </span>
                  <span className="min-w-0 text-left font-mono text-[11px] text-sub">{fmtTime(r.m)}</span>
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

      {pv && !activeRow && (
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
      {pv && activeRow && (
        <Preview
          row={activeRow!}
          q={syn.q || q}
          onClose={() => setPv(false)}
          onOpen={() => openRow(activeRow!.p)}
          onReveal={async () => {
            const bridge = (window as unknown as { pywebview?: { api?: { reveal?: (p: string) => Promise<boolean> } } })
              .pywebview?.api
            /* 第 319 项：详情栏的「定位」以前既不说没有桥，也不说失败，点了像没反应 */
            if (!bridge?.reveal) {
              setToast(t('ctx_reveal_web'))
              return
            }
            try {
              const ok = await bridge.reveal(activeRow!.p)
              if (!ok) setToast(t('ctx_reveal_fail'))
            } catch {
              setToast(t('ctx_reveal_fail'))
            }
          }}
          onCopy={() => {
            try {
              void navigator.clipboard?.writeText(activeRow!.p)
            } catch {
              /* 剪贴板被拒就算了 */
            }
          }}
          /* 第 318 项：灯箱里 ← → / 左右按钮在「图片之间」走，跳过文档与文件夹 */
          onStep={stepImage}
        />
      )}

      <SettingsDrawer
        /* 第 301 项：about 不再是一个独立面板，而是设置里的一个分区。
           托盘 / 侧栏 / 页脚版本号 / ?panel=about 都走这里，统一落到「关于」分区。 */
        open={panel === 'settings' || panel === 'about'}
        startSec={panel === 'about' ? 'about' : settingsSec}
        onClose={() => {
          setPanel(null)
          setSettingsSec(undefined)
        }}
        onOpenWhy={() => setWhyOpen(true)}
        onReplayTour={() => setTourReplay(Date.now())}
        palette={palette}
        onPalette={applyTheme}
        wall={wall}
        onWall={setWall}
        /* 第 364 项：设置页要不要吃壁纸，跟主窗同一个判定（勾了「设置页」+ 壁纸开着） */
        wallOn={showWall}
        onRefreshed={() => {
          api<Stats>('/api/stats').then(setStats).catch(() => {})
          setToast(t('maint_finished'))
        }}
        currentQ={q}
        onPickSearch={(x) => setQ(x)}
        onOpenOcr={() => setPanel('ocr')}
        ocrRunning={ocr?.run === 'running'}
        onOpenSupport={() => setSupportOpen(true)}
        /* 第 327 项：设置里关掉左栏某一块，抽屉不用关也不用重开，主界面立刻跟着变 */
        onSettings={(p) =>
          setSb((cur) => ({
            types: p.sb_types !== undefined ? p.sb_types : cur.types,
            folders: p.sb_folders !== undefined ? p.sb_folders : cur.folders,
            time: p.sb_time !== undefined ? p.sb_time : cur.time,
            counts: p.sb_counts !== undefined ? p.sb_counts : cur.counts,
          }))
        }
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
      <OcrBatchModal
        open={ocrOpen}
        onClose={() => setPanel(null)}
        status={ocr}
        pro={ocrPro}
        rows={shown}
        onRefresh={refreshOcr}
        onOpenAbout={() => setPanel('about')}
      />
      <SnipModal
        open={snipOpen}
        onClose={() => setSnipOpen(false)}
        result={snipResult}
        onSearch={(txt) => {
          setSnipOpen(false)
          setQ(txt)
          qEl.current?.focus()
        }}
      />
      <ClipboardModal
        isOpen={clipOpen}
        onClose={() => setClipOpen(false)}
        onToast={(msg) => setToast(msg)}
      />
      <TranslateModal
        isOpen={transOpen}
        onClose={() => setTransOpen(false)}
        onToast={(msg) => setToast(msg)}
        initialQuery={q}
      />
      {previewImgOpen && pastedImg && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in"
          onClick={() => setPreviewImgOpen(false)}
        >
          <div
            className="relative max-h-[85vh] max-w-[90vw] overflow-hidden rounded-2xl border border-line bg-card shadow-2xl flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <span className="text-sm font-semibold text-ink">{t('app_preview_heading')}</span>
              <span className="text-xs text-sub">{t('app_preview_sub')}</span>
              <button
                type="button"
                onClick={() => setPreviewImgOpen(false)}
                className="text-sub hover:text-ink text-base font-bold leading-none px-1"
                title={t('clip_close_title')}
              >
                ✕
              </button>
            </div>
            <div className="p-3 bg-bg/50 overflow-auto flex items-center justify-center max-h-[75vh]">
              <img
                src={pastedImg}
                alt="preview"
                className="max-h-[70vh] max-w-full rounded-lg object-contain shadow-sm cursor-zoom-out"
                onClick={() => setPreviewImgOpen(false)}
              />
            </div>
          </div>
        </div>
      )}

      {/* 首启引导：装完第一次打开自动建库（老用户 need=false，完全无感） */}
      <Onboarding replay={tourReplay} onOpenSettings={() => setPanel('settings')}
        onIndexed={onFirstIndexed} />

      {/* 页脚 */}
      <footer className="sb-readable flex items-center gap-3 border-t border-line px-3 py-1.5 text-[11px] text-sub">
        <span>
          {busy
            ? t('checking')
            : q.trim()
              ? curRow
                ? `${t('item_cur').replace('{i}', String(curIdx + 1)).replace('{n}', String(shown.length))}${
                    curRow.s && curRow.t !== 'dir' ? ` · ${fmtSize(curRow.s)}` : ''
                  }${curRow.m ? ` · ${fmtTime(curRow.m)}` : ''}`
                : `${shown.length} / ${rows.length} ${t('items')}`
              : t('timeline')}
        </span>
        <span className="text-line">|</span>
        <span>
          <kbd className="rounded border border-line px-1">↑↓</kbd> {t('kb_select')} ·{' '}
          <kbd className="rounded border border-line px-1">Enter</kbd> {t('kb_open')} ·{' '}
          <kbd className="rounded border border-line px-1">Ctrl+Enter</kbd> {t('kb_reveal')} ·{' '}
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
