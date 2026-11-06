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
}

type Job = 'scan' | 'update' | 'names' | 'selftest'

const LABEL: Record<Job, string> = {
  scan: '智能扫描全盘',
  update: '按现有范围更新',
  names: '重建文件名索引',
  selftest: '引擎体检',
}

const BUSY_TEXT: Record<string, string> = {
  scan: '正在找文件夹…',
  update: '正在读正文…',
  names: '正在扫文件名…',
  selftest: '正在跑 20 项自检…',
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
        const r = await post<{ ok?: boolean; err?: string }>(
          which === 'scan' ? '/api/discover' : `/api/${which === 'update' ? 'update' : which}`,
        )
        if (r && r.ok === false) {
          setNote(r.err || '没启动起来')
          setJob(null)
          return
        }
      } catch {
        setNote('请求失败，看看程序还在不在')
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
            setNote(p.msg || p.err || (p.state === 'done' ? '完成' : p.state))
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
            {LABEL[job]} · {BUSY_TEXT[job]}
            {prog.done ? ` ${prog.done}${prog.total ? '/' + prog.total : ''}` : ''}
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-line">
            <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
          </div>
          <div className="mt-1 text-[11px] opacity-70">关掉这个抽屉也会继续跑，跑完在界面底部提示。</div>
        </div>
      )}

      {!job && note && <div className="mt-2 text-[11px] text-accent">{note}</div>}
    </div>
  )
}
