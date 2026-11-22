/* 第 379 项：详情面板的「真版面预览」。
 *
 * 为什么要有这个文件：
 *   索引里只存了从 docx/xlsx 抽出来的纯文本（doc_rec 的 zstd blob），版式、表格、
 *   分页在抽取那一步就丢了。所以详情栏里只能显示一段文字流，用户的原话是
 *   「为什么预览无法像 gety 一样，能够看到文档内容」。
 *   要画版面，只能回头读原始文件字节（/api/raw），再靠渲染器画：
 *     · docx → docx-preview（纯前端，JSZip 解 zip+xml，按页出 DOM）
 *     · xlsx → SheetJS 解析，自己拼 <table>（只取单元格文本，绝不 innerHTML，
 *              免得一个恶意 xlsx 里的字符串变成标签注入进来）
 *     · pptx → 暂不做（没有成熟的前端版式渲染器，仍是纯文本 + 打开原文件）
 *
 * 三个库都不进主包：放在 public/vendor 下，用到的那一刻才 script 注入。
 * 主 bundle 一行没变，平时搜索根本不会加载它们。
 */

type AnyWin = Window & {
  docx?: { renderAsync: (b: Blob, el: HTMLElement, s?: unknown, o?: unknown) => Promise<unknown> }
  XLSX?: {
    read: (b: ArrayBuffer, o?: unknown) => { SheetNames: string[]; Sheets: Record<string, unknown> }
    utils: { sheet_to_json: (ws: unknown, o?: unknown) => unknown[][] }
  }
}

const _loads: Record<string, Promise<void>> = {}

/** 按需加载 vendor 里的 UMD 脚本；同一个文件只加载一次。 */
function loadScript(file: string): Promise<void> {
  const hit: Promise<void> | undefined = _loads[file]
  if (hit) return hit
  _loads[file] = new Promise<void>((resolve, reject) => {
    const s = document.createElement('script')
    // base 是 './'，页面可能挂在 /web/ 下 —— 用 baseURI 拼，别写死 /vendor
    s.src = new URL(`vendor/${file}`, document.baseURI).href
    s.async = false
    s.onload = () => resolve()
    s.onerror = () => reject(new Error(`vendor 加载失败：${file}`))
    document.head.appendChild(s)
  })
  return _loads[file]
}

/** docx-preview 的 UMD 依赖全局 JSZip，必须先装它，再装本体。 */
async function ensureDocx(): Promise<void> {
  await loadScript('jszip.min.js')
  await loadScript('docx-preview.min.js')
}

async function ensureXlsx(): Promise<void> {
  await loadScript('xlsx.mini.min.js')
}

export function docRawUrl(path: string): string {
  return `/api/raw?p=${encodeURIComponent(path)}`
}

/** 支持「真版面」的扩展名。加进这个名单 = 详情栏里多一颗「预览版面」。 */
export const LAYOUT_EXT = ['docx', 'xlsx', 'pptx']

export function canLayout(ext: string): boolean {
  return LAYOUT_EXT.includes(String(ext || '').replace(/^\./, '').toLowerCase())
}

/* 第 385 项：详情栏**自动**内嵌预览的格式。
   跟 canLayout 分开是因为两件事不一样：
   · canLayout  = 能开「预览版面」大窗（版面渲染器真能画）；
   · canInline  = 选中即出小预览（有的格式只能出「内容」，出不了版面，比如 pptx）。 */
export const INLINE_EXT = ['docx', 'xlsx', 'pptx']

export function canInline(ext: string): boolean {
  return INLINE_EXT.includes(String(ext || '').replace(/^\./, '').toLowerCase())
}

/** 详情栏自动预览的体积上限：跟 /api/raw 的 48MB 上限对齐。
 *  用户的扫描件 docx 动辄 20~55MB，卡在 12MB 等于「又要手动点」，所以放到接口上限；
 *  再大的连原始字节都读不到，只能走「打开」。 */
export const INLINE_MAX_BYTES = 48 * 1024 * 1024

async function fetchBytes(path: string, want = 48 * 1024 * 1024): Promise<ArrayBuffer> {
  const r = await fetch(docRawUrl(path), { headers: { 'Sec-Fetch-Site': 'same-origin' } })
  if (r.status === 204) throw new Error(`这个文件不在索引里，或者超过 ${Math.round(want / 1024 / 1024)}MB，读不到原始内容`)
  if (!r.ok) throw new Error(`读原始文件失败（HTTP ${r.status}）`)
  const buf = await r.arrayBuffer()
  if (!buf.byteLength) throw new Error('原始文件是空的')
  if (buf.byteLength > want) throw new Error('文件太大，版面渲染会卡住，用「打开」看吧')
  return buf
}

/** docx：按真实版面铺到容器里，返回页数。 */
export async function renderDocx(path: string, el: HTMLElement, maxBytes = 48 * 1024 * 1024): Promise<number> {
  await ensureDocx()
  const buf = await fetchBytes(path, maxBytes)
  const w = window as AnyWin
  const blob = new Blob([buf], {
    type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  })
  // ignoreHeight=false：按 A4 真实页高排版，才能像 Word 那样一页一页断开
  await w.docx!.renderAsync(blob, el, null, {
    inWrapper: true,
    breakPages: true,
    ignoreWidth: false,
    ignoreHeight: false,
    renderHeaders: true,
    renderFooters: true,
    useBase64URL: true,
    experimental: true,
  })
  return el.querySelectorAll('.docx-wrapper > section').length || el.querySelectorAll('section').length || 1
}

export type SheetTable = { name: string; rows: string[][]; cols: number; cut: boolean }
/** xlsx：把每个工作表读成字符串矩阵（只给文本，前端自己拼表格）。
 *  第 401 项：以前只读前 12 张表 —— 网安行业互助表这种 40+ 张表的文件，
 *  命中在别的表里就永远看不到。改成全部读（上限 40 张，防病态文件）。 */
export async function readSheets(path: string, maxRows = 300, maxCols = 40): Promise<SheetTable[]> {
  await ensureXlsx()
  const buf = await fetchBytes(path)
  const w = window as AnyWin
  const wb = w.XLSX!.read(buf, { type: 'array' })
  const out: SheetTable[] = []
  for (const name of wb.SheetNames.slice(0, 40)) {
    const ws = wb.Sheets[name]
    const raw = (w.XLSX!.utils.sheet_to_json(ws, { header: 1, raw: false, defval: '' }) || []) as unknown[][]
    const rows = raw.slice(0, maxRows).map((r) => {
      const cells = (Array.isArray(r) ? r : []).slice(0, maxCols)
      const s: string[] = []
      for (let i = 0; i < cells.length; i++) s.push(cells[i] == null ? '' : String(cells[i]))
      return s
    })
    while (rows.length && rows[rows.length - 1].every((c) => !c)) rows.pop()
    if (!rows.length) continue
    const cols = rows.reduce((m, r) => Math.max(m, r.length), 0)
    for (const r of rows) while (r.length < cols) r.push('')
    out.push({ name, rows, cols, cut: raw.length > maxRows })
  }
  return out
}

/* ───────────────── 第 385 项：详情栏内嵌预览 ───────────────── */

/** 读原始文件字节（带体积上限），给内嵌预览用。 */
export async function fetchRaw(path: string, maxBytes = INLINE_MAX_BYTES): Promise<ArrayBuffer | null> {
  try {
    const buf = await fetchBytes(path, maxBytes)
    return buf
  } catch {
    return null
  }
}

/** docx：渲染成真实版面后按容器宽度等比缩放（详情栏只有 ~280px，铺满会横向滚动）。 */
export async function renderDocxInline(path: string, el: HTMLElement, maxBytes = INLINE_MAX_BYTES): Promise<number> {
  const pages = await renderDocx(path, el, maxBytes)
  const wrap = el.querySelector('.docx-wrapper') as HTMLElement | null
  if (wrap) {
    // A4 = 794 CSS px；zoom 会改变布局尺寸（不像 transform 那样只改显示），所以能一次铺满
    const w = el.clientWidth || 280
    const scale = Math.min(1, Math.max(0.2, (w - 8) / 794))
    wrap.style.zoom = String(scale)
  }
  return pages
}

export type SlideText = { n: number; title: string; lines: string[] }

type ZipEntry = { async: (t: 'string') => Promise<string> }
type AnyWinZip = Window & {
  JSZip?: { loadAsync: (b: Blob | ArrayBuffer) => Promise<{ files: Record<string, ZipEntry> }> }
}

/**
 * pptx：没有成熟的前端版式渲染器（LibreOffice 打包已否决），所以这里不做「画版式」，
 * 而是**按幻灯片逐页抽文字**：第 1 页是标题页也一眼能认出来是哪份 PPT。
 * 只读 zip 里的 ppt/slides/slideN.xml，取 <a:t> 文本，按 <a:p> 分段落。
 */
export async function readPptxSlides(path: string, maxSlides = 6, maxBytes = INLINE_MAX_BYTES): Promise<SlideText[]> {
  await loadScript('jszip.min.js')
  const buf = await fetchBytes(path, maxBytes)
  const w = window as AnyWinZip
  const zip = await w.JSZip!.loadAsync(buf)
  const names = Object.keys(zip.files)
  const slides = names
    .filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))
    .sort((a, b) => parseInt(a.match(/slide(\d+)\.xml/)![1], 10) - parseInt(b.match(/slide(\d+)\.xml/)![1], 10))
  const out: SlideText[] = []
  for (let i = 0; i < slides.length && i < maxSlides; i++) {
    const xml = await zip.files[slides[i]].async('string')
    const doc = new DOMParser().parseFromString(xml, 'application/xml')
    const paras = Array.from(doc.getElementsByTagName('a:p'))
    const lines: string[] = []
    for (const p of paras) {
      const runs = Array.from(p.getElementsByTagName('a:t')).map((t) => t.textContent || '')
      const line = runs.join('').replace(/\s+/g, ' ').trim()
      if (line) lines.push(line)
    }
    if (!lines.length) continue
    out.push({ n: i + 1, title: lines[0], lines: lines.slice(1) })
  }
  return out
}
