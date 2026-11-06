import { useCallback, useEffect, useRef, useState } from 'react'
import { IconRefresh, IconCheck, IconSearch, IconStop } from './icons'
import { t } from '../lib/i18n'

/**
 * 维护工具：扫描 / 更新 / 重建文件名索引 / 引擎体检。
 *
 * 这四个动作在旧界面是四段各自的轮询代码（`smartScan`/`upd`/`buildNames`/`selftest`），
 * 现在统一收在这里：点按钮 → POST 一次 → 每 700ms 读一次进度 → 结束时把 msg 显示出来。
 * 后端本来就只允许同时跑一个后台任务（`_job_busy`），所以这里不排队，忙的时候按钮全禁用。
 */

type Prog = {
  state?: string
  phase?: string
  done?: number
  total?: number
  ok?: number
  fail?: number
  msg?: string
  err?: string
  /** 后端给的文案键 + 参数：有 k 就按当前语言渲染，没有才退回 msg 原文 */
  k?: string
  a?: Record<string, string | number>
}

type Job = 'scan' | 'update' | 'names' | 'selftest'

/** 按钮名与「正在干什么」都走 i18n；以前这两张表是写死的中文，切成英文后整块不跟着变 */
const LABEL_KEY: Record<Job, string> = {
  scan: 'maint_scan',
  update: 'maint_update',
  names: 'maint_names',
  selftest: 'maint_selftest',
}

const BUSY_KEY: Record<Job, string> = {
  scan: 'maint_busy_scan',
  update: 'maint_busy_update',
  names: 'maint_busy_names',
  selftest: 'maint_busy_selftest',
}

/** 把 {n} 这类占位符填上：i18n 的 t() 只负责取串，参数在这里补 */
export function fill(s: string, a?: Record<string, string | number>): string {
  if (!a) return s
  return s.replace(/\{(\w+)\}/g, (m, k: string) =>
    a[k] === undefined || a[k] === null ? m : String(a[k]),
  )
}

/** 后端的 k/a 优先，其次是它给的中文原文（异常信息这类没法预翻译） */
export function say(p: { k?: string; a?: Record<string, string | number>; msg?: string; err?: string }): string {
  if (p.k) {
    const s = fill(t(p.k), p.a)
    if (s && s !== p.k) return s
  }
  return p.msg || p.err || ''
}

async function post<T>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
    body: JSON.stringify(body),
  })
  return (await r.json()) as T
}

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { 'Sec-Fetch-Site': 'same-origin' } })
  return (await r.json()) as T
}

const PROBE: Record<Job, string> = {
  scan: '/api/discover',
  update: '/api/status',
  names: '/api/names',
  selftest: '/api/selftest',
}

export function Maintenance({ onDone }: { onDone?: () => void }) {
  const [job, setJob] = useState<Job | null>(null)
  const [prog, setProg] = useState<Prog>({})
  const [note, setNote] = useState('')
  const timer = useRef<number | null>(null)

  const stop = useCallback(() => {
    if (timer.current) window.clearInterval(timer.current)
    timer.current = null
    setJob(null)
  }, [])

  useEffect(() => () => stop(), [stop])

  const start = useCallback(
    async (which: Job) => {
      if (job) return
      setJob(which)
      setProg({})
      setNote('')
      try {
        const r = await post<{ ok?: boolean; err?: string; k?: string }>(
          which === 'scan' ? '/api/discover' : `/api/${which === 'update' ? 'update' : which}`,
        )
        if (r && r.ok === false) {
          setNote(say(r) || t('maint_start_fail'))
          setJob(null)
          return
        }
      } catch {
        setNote(t('maint_net_fail'))
        setJob(null)
        return
      }
      if (timer.current) window.clearInterval(timer.current)
      timer.current = window.setInterval(async () => {
        try {
          const p = await getJson<Prog>(PROBE[which])
          setProg(p)
          if (p.state && p.state !== 'busy') {
            stop()
            setNote(say(p) || (p.state === 'done' ? t('maint_done') : p.state))
            onDone?.()
          }
        } catch {
          /* 后端忙的时候偶发失败，下一次再读 */
        }
      }, 700)
    },
    [job, onDone, stop],
  )

  const pct =
    prog.total && prog.total > 0 ? Math.min(100, Math.round(((prog.done || 0) / prog.total) * 100)) : job ? 5 : 0

  return (
    <div className="mt-4 border-t border-line pt-3">
      <div className="mb-2 text-[12px] text-sub">{t('maint_title')}</div>
      <div className="flex flex-wrap gap-1.5">
        <button
          disabled={!!job}
          onClick={() => start('scan')}
          className="flex items-center gap-1 rounded border border-line px-2 py-1 text-[12px] hover:border-accent disabled:opacity-50"
        >
          <IconSearch size={13} /> {t('maint_scan')}
        </button>
        <button
          disabled={!!job}
          onClick={() => start('update')}
          className="flex items-center gap-1 rounded border border-line px-2 py-1 text-[12px] hover:border-accent disabled:opacity-50"
        >
          <IconRefresh size={13} /> {t('maint_update')}
        </button>
        <button
          disabled={!!job}
          onClick={() => start('names')}
          className="flex items-center gap-1 rounded border border-line px-2 py-1 text-[12px] hover:border-accent disabled:opacity-50"
        >
          <IconRefresh size={13} /> {t('maint_names')}
        </button>
        <button
          disabled={!!job}
          onClick={() => start('selftest')}
          className="flex items-center gap-1 rounded border border-line px-2 py-1 text-[12px] hover:border-accent disabled:opacity-50"
        >
          <IconCheck size={13} /> {t('maint_selftest')}
        </button>
      </div>

      {job && (
        <div className="mt-2 text-[11px] text-sub">
          <div className="mb-1 flex items-center gap-1">
            <IconStop size={11} />
            {t(LABEL_KEY[job])} · {say(prog) || fill(t(BUSY_KEY[job]), prog.a)}
            {prog.done ? ` ${prog.done}${prog.total ? '/' + prog.total : ''}` : ''}
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-line">
            <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
          </div>
          <div className="mt-1 text-[11px] opacity-70">{t('maint_running_hint')}</div>
        </div>
      )}

      {!job && note && <div className="mt-2 text-[11px] text-accent">{note}</div>}
    </div>
  )
}
