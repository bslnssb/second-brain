/** 第 404 项：标签栏「性能监控」小面板。
 *
 * 只在面板打开时轮询 `/api/perf`（2 秒一次），关掉就停 —— 平时不占一点后台。
 * 数字口径：CPU 是「占整机几核的百分比」（12 核跑满 1 核 = 8.3%），内存含
 * WebView2 的渲染进程（它们是 Memex.exe 的子进程），和任务管理器里「这一组」对得上。
 */
import { useEffect, useRef, useState } from 'react'
import { t } from '../lib/i18n'
import { IconClose } from './icons'

type Snap = {
  ok: boolean
  cores: number
  cpu: { proc: number; sys: number }
  mem: {
    proc_mb: number
    private_mb: number
    peak_mb: number
    proc_pct: number
    sys_used_mb: number
    sys_total_mb: number
    sys_pct: number
  }
  proc: { threads: number; handles: number; uptime_s: number; pids: number }
}

function size(mb: number): string {
  if (!mb || mb < 0) return '0 MB'
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${Math.round(mb)} MB`
}

function uptime(s: number): string {
  if (!s || s <= 0) return '—'
  if (s < 60) return `${s}s`
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m`
}

function Bar({ pct, strong }: { pct: number; strong?: boolean }) {
  const w = Math.max(0, Math.min(100, pct || 0))
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line">
      <div
        className={`h-full rounded-full ${strong ? 'bg-accent' : 'bg-sub'}`}
        style={{ width: `${w}%` }}
      />
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[11px] text-sub">{label}</div>
      <div className="truncate text-[12px] font-medium text-ink">{value}</div>
    </div>
  )
}

export function PerfPopover({ onClose }: { onClose: () => void }) {
  const [d, setD] = useState<Snap | null>(null)
  const [bad, setBad] = useState(false)
  const box = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const r = await fetch('/api/perf', { cache: 'no-store' })
        const j = (await r.json()) as Snap
        if (!alive) return
        setBad(!j || !j.ok)
        if (j && j.ok) setD(j)
      } catch {
        if (alive) setBad(true)
      }
    }
    tick()
    const timer = window.setInterval(tick, 2000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  const cpu = d?.cpu.proc ?? 0
  const mem = d?.mem
  const sysCpu = d?.cpu.sys ?? 0

  return (
    <div
      ref={box}
      data-perf-panel="1"
      className="absolute right-0 top-10 z-40 w-[272px] rounded-xl border border-line bg-card p-3 text-ink shadow-xl"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[12px] font-medium">{t('perf_title')}</span>
        <button
          className="grid h-5 w-5 place-items-center rounded text-sub hover:text-ink"
          onClick={onClose}
          aria-label={t('perf_title')}
          title={t('perf_title')}
        >
          <IconClose size={12} />
        </button>
      </div>

      {bad && !d ? (
        <div className="py-3 text-[12px] text-sub">—</div>
      ) : (
        <>
          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-[11px] text-sub">{t('perf_cpu')}</span>
            <span className="text-[13px] font-semibold tabular-nums">
              {cpu.toFixed(1)}%
            </span>
          </div>
          <Bar pct={cpu} strong />
          <div className="mb-3 mt-1 text-[11px] text-sub">
            {t('perf_proc')} · {d?.cores ?? 0} {t('perf_cores')} · {t('perf_sys')}{' '}
            {sysCpu.toFixed(0)}%
          </div>

          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-[11px] text-sub">{t('perf_mem')}</span>
            <span className="text-[13px] font-semibold tabular-nums">
              {size(mem?.proc_mb ?? 0)}
              <span className="ml-1 text-[11px] font-normal text-sub">
                {(mem?.proc_pct ?? 0).toFixed(1)}%
              </span>
            </span>
          </div>
          <Bar pct={mem?.proc_pct ?? 0} strong />
          <div className="mb-3 mt-1 text-[11px] text-sub">
            {t('perf_sys')} · {t('perf_used')} {size(mem?.sys_used_mb ?? 0)} / {t('perf_total')}{' '}
            {size(mem?.sys_total_mb ?? 0)} · {(mem?.sys_pct ?? 0).toFixed(0)}%
          </div>

          <div className="mt-2 grid grid-cols-3 gap-2 border-t border-line pt-2">
            <Stat label={t('perf_threads')} value={String(d?.proc.threads ?? 0)} />
            <Stat label={t('perf_handles')} value={String(d?.proc.handles ?? 0)} />
            <Stat label={t('perf_uptime')} value={uptime(d?.proc.uptime_s ?? 0)} />
          </div>
          <div className="mt-1 text-[10px] text-sub">{t('perf_hint')}</div>
        </>
      )}
    </div>
  )
}
