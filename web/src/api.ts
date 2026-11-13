/** 后端接口的薄封装：只在浏览器里跑（开发时 Vite 代理到 8765）。 */

export type Stats = { files: number; docs: number }
/** 官方地址由后端下发（写在 brain.py 里、跟着 exe 编译），前端只负责显示 */
export type Ping = {
  ver: string
  dir?: string
  fallback?: boolean
  repo?: string
  releases?: string
  issues?: string
  /** 国内备用下载页（本机 update_sources.json 配置，空则不显示） */
  manual_page?: string
}

/** 设置抽屉「索引范围」用：后端 scope_report() 的输出 */
export type ScopeInfo = {
  include_dirs: string[]
  exclude_dirs: string[]
  drives: [string, number][]
  top: [string, number][]
  files: number
  docs: number
  names_state?: string
  rules: { ext: number; skip: number; size_limit: number }
}
export type ConfigInfo = {
  exists: boolean
  include_dirs: string[]
  exclude_dirs: string[]
  app_dir?: string
  docs: number
  scope?: ScopeInfo
  broken?: boolean
}

export type TimelineItem = { p: string; n?: string; m?: number; s?: number; e?: string; t?: string }
export type TimelineGroup = { d: string; label: string; n: number; more?: boolean; items: TimelineItem[] }

export type Settings = {
  hotkey?: boolean
  /** 旧的深浅色开关，已被 palette 取代；留着只为兼容老配置文件 */
  theme?: string
  /** 主题配色 id（第 191 项）：真值以 localStorage.sb_palette 为准 */
  palette?: string
  lang?: string
  compat?: boolean
  /** 自绘标题栏（第 180 项）：窗口样式开机时改，要重启才生效 */
  titlebar?: boolean
}

type Bridge = {
  open_path?: (p: string) => Promise<boolean>
  reveal?: (p: string) => Promise<boolean>
  /** 顶栏/标题栏「快搜」按钮：收起详情窗（快搜窗由 open_mini 唤起） */
  hide_main?: () => Promise<boolean>
  open_mini?: () => Promise<boolean>
  /** 原生目录选择框。浏览器里拿不到绝对路径，整本 OCR 入队必须走这个桥 */
  pick_folder?: () => Promise<string>
  /** 自绘标题栏（第 180 项）：拖动交给系统（含 Aero Snap） */
  titlebar_drag?: () => Promise<boolean>
  /** 自绘标题栏三键：min / max / close */
  titlebar_cmd?: (cmd: 'min' | 'max' | 'close') => Promise<boolean>
  /** 当前是否最大化，用来决定画 □ 还是 ❐ */
  titlebar_state?: () => Promise<{ max: boolean }>
  /** 原生文件选择框（壁纸用；也能给别的「选一个文件」场景用） */
  pick_file?: () => Promise<string>
}

function bridge(): Bridge | undefined {
  return (window as unknown as { pywebview?: { api?: Bridge } }).pywebview?.api
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!r.ok) throw new Error(`${path} ${r.status}`)
  return (await r.json()) as T
}

export const getStats = () => api<Stats>('/api/stats')
export const getConfig = () => api<ConfigInfo>('/api/config')
export const getPing = () => api<Ping>('/api/ping')

/**
 * Pro：把当前结果导出成 Excel（第 140 项）。
 * 后端自己写文件到「文档/第二大脑导出」并定位，前端只负责提示 ——
 * 和「右键打包」同一套做法，免得在 WebView2 里跟 blob 下载打交道。
 * 未授权时返回 `{ ok:false, pro:true }`，不是异常。
 */
export type ExportResult = {
  ok?: boolean
  pro?: boolean
  path?: string
  n?: number
  err?: string
  k?: string
}

export const exportResults = (rows: unknown[], q: string) =>
  api<ExportResult>('/api/export', { method: 'POST', body: JSON.stringify({ rows, q }) })

/* --------- 批量 OCR 队列（第 143 项实现，第 148 项起免费） --------- */
/** 免费索引只识别扫描件前几页；这个队列逐页跑完整本，写回同一个正文索引。 */
export type OcrBatchItem = {
  p: string
  st: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  chars: number
  pages: number
  err: string
}
export type OcrBatchStatus = {
  ok?: boolean
  pro?: boolean
  run?: 'idle' | 'running' | 'paused' | 'done'
  total?: number
  done?: number
  failed?: number
  skipped?: number
  pending?: number
  pct?: number
  current?: string
  current_path?: string
  page_done?: number
  page_total?: number
  note?: string
  items?: OcrBatchItem[]
  added?: number
  retried?: number
}
export type OcrOp = 'add' | 'pause' | 'resume' | 'cancel' | 'retry' | 'clear'

/** 面板每 1 秒轮询；关掉面板后 5 秒一次，只为让工具栏那个小圆点还准 */
export const getOcrBatch = () => api<OcrBatchStatus>('/api/ocr_batch')

export const ocrBatchOp = (op: OcrOp, extra?: { paths?: string[]; dirs?: string[] }) =>
  api<OcrBatchStatus>('/api/ocr_batch', {
    method: 'POST',
    headers: { 'Sec-Fetch-Site': 'same-origin' },
    body: JSON.stringify({ op, ...(extra || {}) }),
  })

/** 选文件夹拿完整路径；浏览器里没有原生桥时返回空串 */
export async function pickFolder(): Promise<string> {
  try {
    return (await bridge()?.pick_folder?.()) || ''
  } catch {
    return ''
  }
}

/** 选一个文件拿完整路径（壁纸用）。取消或浏览器模式返回空串 */
export async function pickFile(): Promise<string> {
  try {
    return (await bridge()?.pick_file?.()) || ''
  } catch {
    return ''
  }
}

/* --------- 全局壁纸（第 191 项） ---------
 * 图片只从本机读、只留在本机：后端只肯返回 `settings.json` 里记的那一张，
 * 前端拿不到「随便读一个路径」的能力。 */
export type WallState = {
  on: boolean
  name?: string
  opacity: number
  blur: number
  /** 文件修改时间：换图 / 重选同一张时用它让浏览器重新取图（缓存键） */
  v?: number
  err?: string
}

export const getWall = () => api<WallState>('/api/wall')

const wallPost = (body: unknown) =>
  api<WallState>('/api/wall', {
    method: 'POST',
    headers: { 'Sec-Fetch-Site': 'same-origin' },
    body: JSON.stringify(body),
  })

export const setWallFile = (path: string) => wallPost({ op: 'set', path })
export const tuneWall = (opacity: number, blur: number) => wallPost({ op: 'tune', opacity, blur })
export const clearWall = () => wallPost({ op: 'clear' })

/** 授权状态。字段与后端 license_core.parse_code 的返回一致，未激活时 edition='free'。 */
export type LicenseState = {
  valid: boolean
  reason: string
  edition: 'free' | 'pro'
  kind: string
  name: string
  ident: string
  exp: string
  issued: string
  features: string[]
  days_left: number | null
  code_masked: string
  clock_warn: boolean
  activated: string
}

export const getLicense = () => api<{ ok: boolean; state: LicenseState }>('/api/license')

/** 激活：校验不过后端不会落盘，所以这里失败是「安全」的，原授权保持可用。 */
export const activateLicense = (code: string) =>
  api<{ ok: boolean; reason: string; state: LicenseState }>('/api/license', {
    method: 'POST',
    body: JSON.stringify({ code }),
  })

export const clearLicense = () =>
  api<{ ok: boolean; state: LicenseState }>('/api/license_clear', {
    method: 'POST',
    body: JSON.stringify({}),
  })

/** 保存索引范围：后端只收本机真实存在的目录（不存在/网络路径会被忽略并在 dropped 里回传） */
export type SaveScopeResult = {
  ok: boolean
  err?: string
  k?: string
  include_dirs?: string[]
  exclude_dirs?: string[]
  dropped?: string[]
}
export const saveConfig = (include_dirs: string[], exclude_dirs: string[]) =>
  api<SaveScopeResult>('/api/config', {
    method: 'POST',
    headers: { 'Sec-Fetch-Site': 'same-origin' },
    body: JSON.stringify({ include_dirs, exclude_dirs }),
  })

/** 打开官方地址：后端有白名单（只认本仓库），桌面端会用系统浏览器打开 */
export async function openUrl(u: string): Promise<boolean> {
  try {
    const r = await fetch(`/api/open_url?u=${encodeURIComponent(u)}`, {
      headers: { 'Sec-Fetch-Site': 'same-origin' },
    })
    const d = (await r.json()) as { ok?: boolean }
    return !!d.ok
  } catch {
    return false
  }
}
export const getTimeline = (days = 14, kind = 'all') => {
  const qs = new URLSearchParams({ days: String(days) })
  if (kind && kind !== 'all') qs.set('t', kind)
  return api<{ days: number; groups: TimelineGroup[] }>(`/api/timeline?${qs.toString()}`)
}
export const getSettings = () => api<Settings>('/api/settings')
export const saveSettings = (patch: Settings) =>
  api<{ ok: boolean }>('/api/settings', { method: 'POST', body: JSON.stringify(patch) })
export const getAutostart = () => api<{ on: boolean }>('/api/autostart')
export const setAutostart = (on: boolean) =>
  api<{ ok: boolean; on: boolean }>('/api/autostart', { method: 'POST', body: JSON.stringify({ on }) })

/** 打开：桌面版走原生桥（.exe/.lnk 只有它放行），浏览器里退回 HTTP /open */
export async function openPath(p: string): Promise<boolean> {
  try {
    if (bridge()?.open_path) return await bridge()!.open_path!(p)
    await api(`/open?p=${encodeURIComponent(p)}`)
    return true
  } catch {
    return false
  }
}

/** 在资源管理器里定位：后端只暴露了 pywebview 桥，没有 HTTP 路由 */
export async function revealPath(p: string): Promise<boolean> {
  try {
    if (bridge()?.reveal) return await bridge()!.reveal!(p)
    return false
  } catch {
    return false
  }
}

export function copyText(t: string): boolean {
  try {
    void navigator.clipboard?.writeText(t)
    return true
  } catch {
    return false
  }
}

/* ------------------------------ 更新与公告 ------------------------------ */

export type UpdateInfo = {
  ok: boolean
  latest?: string
  newer?: boolean
  page?: string
  asset?: string
  size?: number
  note?: string
  norelease?: boolean
  err?: string
  manual_page?: string
}

/** 检查更新：要连 GitHub，前端要显示「检查中…」，不要放在页面加载时调 */
export async function checkUpdate(): Promise<UpdateInfo> {
  try {
    const r = await fetch('/api/update_check', { headers: { 'Sec-Fetch-Site': 'same-origin' } })
    return (await r.json()) as UpdateInfo
  } catch (e) {
    return { ok: false, err: String(e) }
  }
}

/** 下载并安装：后端立即返回 started，下载在后台线程跑（171MB 要几十秒到几分钟），
 *  进度用 updateStatus() 轮询；完成后后端起 bat、自己退出，界面会断开——这是正常的 */
export async function applyUpdate(): Promise<{ ok: boolean; err?: string; started?: boolean }> {
  try {
    const r = await fetch('/api/update_apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
      body: '{}',
    })
    return (await r.json()) as { ok: boolean; err?: string; started?: boolean }
  } catch (e) {
    return { ok: false, err: String(e) }
  }
}

export type UpdateStatus = {
  state?: 'idle' | 'download' | 'unpack' | 'done' | 'error'
  pct?: number
  err?: string
  k?: string
}

/** 一键升级的下载进度（每 700ms 轮询一次） */
export async function updateStatus(): Promise<UpdateStatus> {
  try {
    const r = await fetch('/api/update_status', { headers: { 'Sec-Fetch-Site': 'same-origin' } })
    return (await r.json()) as UpdateStatus
  } catch {
    /* 后端正要退出重启时读不到，下一轮再试 */
    return {}
  }
}


/* -------- 索引集 / 保存搜索 / 索引诊断 / 页码（新增功能，全部免费） -------- */
export type LibSet = {
  id: string
  name: string
  files: number
  docs: number
  scope: string
  active: boolean
}
export type LibOverview = { ok: boolean; active: string; libs: LibSet[] }
export type LibOpResult = { ok: boolean; err?: string; lib?: { id: string; name: string } }
export const getLibsets = () => api<LibOverview>('/api/libsets')
export const libsetOp = (op: string, extra: Record<string, unknown> = {}) =>
  api<LibOpResult>('/api/libsets', {
    method: 'POST',
    headers: { 'Sec-Fetch-Site': 'same-origin' },
    body: JSON.stringify({ op, ...extra }),
  })

export type AllHit = { p: string; b?: string; lib: string; n: string }
export const searchAllLibs = (q: string) =>
  api<{ ok: boolean; q: string; hits: AllHit[] }>(`/api/libsets?q=${encodeURIComponent(q)}`)

export type SavedItem = { q: string; name: string; t?: number }
export const getSaved = () => api<{ ok: boolean; items: SavedItem[] }>('/api/saved')
export const savedOp = (op: string, extra: Record<string, unknown> = {}) =>
  api<{ ok: boolean; err?: string; items: SavedItem[] }>('/api/saved', {
    method: 'POST',
    headers: { 'Sec-Fetch-Site': 'same-origin' },
    body: JSON.stringify({ op, ...extra }),
  })

export type Diagnose = {
  ok: boolean
  groups: { why: string; n: number }[]
  items: { p: string; why: string }[]
  total: number
}
export const getDiagnose = (n = 60) => api<Diagnose>(`/api/diagnose?n=${n}`)
export const retryDiagnose = (paths: string[] = []) =>
  api<{ ok: boolean; n: number }>('/api/diagnose', {
    method: 'POST',
    headers: { 'Sec-Fetch-Site': 'same-origin' },
    body: JSON.stringify({ op: 'retry', paths }),
  })

export const getPage = (p: string, q: string) =>
  api<{ ok: boolean; page: number | null }>(`/api/page?p=${encodeURIComponent(p)}&q=${encodeURIComponent(q)}`)
export const openPage = (p: string, n: number) =>
  api<{ ok: boolean; page?: number; err?: string }>(`/api/open_page?p=${encodeURIComponent(p)}&n=${n}`)


/* -------- 常规设置：错误日志上报 / 设置备份 / 兼容模式（全部免费） -------- */
export type ErrConf = {
  /** 本机记录错误报告，默认开启；与「自动发送」是两件事 */
  collect: boolean
  auto: boolean
  /** 开发者侧是否已配置接收端；地址本身永远不回传前端 */
  ingest: boolean
  include_paths: boolean
  sent: number
  last_error: string
}
export type ErrItem = { id: string; at: string; kind: string; where: string; size: number }
export type ErrState = {
  ok: boolean
  conf: ErrConf
  items: ErrItem[]
  preview: string
  note: string
}
export type ErrOpResult = ErrState & {
  err?: string
  text?: string
  n?: number
  path?: string
  state?: ErrState
}
export const getErrlog = () => api<ErrState>('/api/errlog')
export const errOp = (op: string, extra: Record<string, unknown> = {}) =>
  api<ErrOpResult>('/api/errlog', {
    method: 'POST',
    headers: { 'Sec-Fetch-Site': 'same-origin' },
    body: JSON.stringify({ op, ...extra }),
  })

/** 设置备份是二进制 zip，得走 fetch + blob，不能套 api<T>() 的 JSON 解析 */
export async function downloadSettingsBackup(): Promise<boolean> {
  const r = await fetch('/api/backup', { headers: { 'Sec-Fetch-Site': 'same-origin' } })
  if (!r.ok) return false
  const u = URL.createObjectURL(await r.blob())
  const a = document.createElement('a')
  a.href = u
  a.download = 'second-brain-settings.zip'
  a.click()
  setTimeout(() => URL.revokeObjectURL(u), 3000)
  return true
}
export const restoreSettingsBackup = (data: string) =>
  api<{ ok: boolean; err?: string; restored?: string[]; backup_dir?: string }>('/api/backup', {
    method: 'POST',
    headers: { 'Sec-Fetch-Site': 'same-origin' },
    body: JSON.stringify({ data }),
  })

/** 环境体检（第 172 项）：后端 /api/env 早就把「问题 + 修复建议」算好了，
 *  这里只是把它接到界面上。level ∈ OK / INFO / WARN / FAIL。 */
export type HealthItem = {
  level: 'OK' | 'INFO' | 'WARN' | 'FAIL'
  key: string
  title: string
  detail?: string
  fix?: string
}
export type Health = {
  ok: boolean
  msg?: string
  issues?: HealthItem[]
  plan?: string[]
  text?: string
}
export const getHealth = (force = false, lang = 'zh') => {
  const q = new URLSearchParams()
  if (force) q.set('force', '1')
  q.set('lang', lang)
  return api<Health>(`/api/env?${q.toString()}`)
}
