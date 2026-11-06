/** 后端接口的薄封装：只在浏览器里跑（开发时 Vite 代理到 8765）。 */

export type Stats = { files: number; docs: number }
export type Ping = { ver: string; dir?: string; fallback?: boolean }

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

export type Settings = { hotkey?: boolean; theme?: string; lang?: string }

type Bridge = {
  open_path?: (p: string) => Promise<boolean>
  reveal?: (p: string) => Promise<boolean>
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
export const getTimeline = (days = 14, kind = 'all', pf = '') => {
  const qs = new URLSearchParams({ days: String(days) })
  if (kind && kind !== 'all') qs.set('t', kind)
  if (pf.trim()) qs.set('pf', pf.trim())
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

/** 下载并安装：后端会起一个 bat、然后自己退出，界面会断开——这是正常的 */
export async function applyUpdate(): Promise<{ ok: boolean; err?: string }> {
  try {
    const r = await fetch('/api/update_apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
      body: '{}',
    })
    return (await r.json()) as { ok: boolean; err?: string }
  } catch (e) {
    return { ok: false, err: String(e) }
  }
}
