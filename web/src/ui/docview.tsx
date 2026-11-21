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
export const LAYOUT_EXT = ['docx', 'xlsx']

export function canLayout(ext: string): boolean {
  return LAYOUT_EXT.includes(String(ext || '').replace(/^\./, '').toLowerCase())
}

async function fetchBytes(path: string, want = 48 * 1024 * 1024): Promise<ArrayBuffer> {
  const r = await fetch(docRawUrl(path), { headers: { 'Sec-Fetch-Site': 'same-origin' } })
  if (r.status === 204) throw new Error('这个文件不在索引里，或者超过 48MB，读不到原始内容')
  if (!r.ok) throw new Error(`读原始文件失败（HTTP ${r.status}）`)
  const buf = await r.arrayBuffer()
  if (!buf.byteLength) throw new Error('原始文件是空的')
  if (buf.byteLength > want) throw new Error('文件太大，版面渲染会卡住，用「打开」看吧')
  return buf
}

/** docx：按真实版面铺到容器里，返回页数。 */
export async function renderDocx(path: string, el: HTMLElement): Promise<number> {
  await ensureDocx()
  const buf = await fetchBytes(path)
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

/** xlsx：把每个工作表读成字符串矩阵（只给文本，前端自己拼表格）。 */
export async function readSheets(path: string, maxRows = 300, maxCols = 40): Promise<SheetTable[]> {
  await ensureXlsx()
  const buf = await fetchBytes(path)
  const w = window as AnyWin
  const wb = w.XLSX!.read(buf, { type: 'array' })
  const out: SheetTable[] = []
  for (const name of wb.SheetNames.slice(0, 12)) {
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
