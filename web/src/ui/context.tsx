import { useEffect, useRef, useState } from 'react'
import { baseName, dirName, fmtSize, fmtTime, midName } from '../lib/hits'
import { IconCheck, IconClose, IconZip } from './icons'
import { t } from '../lib/i18n'

/* ------------------------------ 右键菜单 ------------------------------ */

export type MenuItem = { label: string; onClick: () => void; danger?: boolean }

export function ContextMenu({
  x,
  y,
  items,
  onClose,
}: {
  x: number
  y: number
  items: MenuItem[]
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    // 捕获阶段监听，避免被行的 onClick 抢走
    window.addEventListener('mousedown', away, true)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('mousedown', away, true)
      window.removeEventListener('keydown', esc)
    }
  }, [onClose])

  // 贴边时往回收，别跑到屏幕外
  const left = Math.min(x, window.innerWidth - 190)
  const top = Math.min(y, window.innerHeight - items.length * 30 - 16)

  return (
    <div
      ref={ref}
      role="menu"
      style={{ left, top }}
      className="fixed z-40 min-w-[176px] overflow-hidden rounded-lg border border-line bg-bg py-1 text-[12px] shadow-xl"
    >
      {items.map((it) => (
        <button
          key={it.label}
          role="menuitem"
          onClick={() => {
            it.onClick()
            onClose()
          }}
          className={`flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-card ${
            it.danger ? 'text-red-500' : ''
          }`}
        >
          {it.label}
        </button>
      ))}
    </div>
  )
}

/* ------------------------------ 拖入反查 ------------------------------ */

type ReverseSame = { p: string; n?: string; m?: number; s?: number }
type Reverse = {
  ok?: boolean
  p?: string
  in_index?: boolean
  size?: number
  mtime?: number
  same?: ReverseSame[]
  siblings?: number
  dup?: number
}

export function ReverseModal({ path, onClose }: { path: string; onClose: () => void }) {
  const [d, setD] = useState<Reverse | null>(null)
  const [failed, setFailed] = useState('')

  useEffect(() => {
    let alive = true
    fetch('/api/reverse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ p: path }),
    })
      .then((r) => r.json())
      .then((r: Reverse) => alive && (r.ok === false ? setFailed(t('rev_cant')) : setD(r)))
      .catch(() => alive && setFailed(t('rev_noresp')))
    return () => {
      alive = false
    }
  }, [path])

  const name = baseName(path)
  return (
    <div
      className="absolute inset-x-0 bottom-0 z-30 grid place-items-center bg-black/20"
      style={{ top: 'var(--sb-title, 0px)' }}
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label={t('rev_title')}
        onClick={(e) => e.stopPropagation()}
        className="w-[560px] max-w-[92vw] rounded-2xl border border-line bg-bg p-5 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 pb-2">
          <div className="min-w-0">
            <div className="truncate text-[14px] font-medium" title={name}>
              {midName(name, 52)}
            </div>
            <div className="truncate text-[11px] text-sub" title={dirName(path)}>
              {dirName(path)}
            </div>
          </div>
          <button onClick={onClose} className="text-sub hover:text-ink" aria-label={t('close')}>
            <IconClose size={16} />
          </button>
        </div>

        {failed && <div className="pt-2 text-[12px] text-red-500">{failed}</div>}

        {d && (
          <div className="space-y-2 pt-2 text-[12px]">
            <div className="flex items-center gap-2">
              <span className={d.in_index ? 'text-green-600' : 'text-amber-600'}>
                {d.in_index ? <IconCheck size={13} /> : null}
              </span>
              <span>{d.in_index ? t('rev_in') : t('rev_out')}</span>
            </div>
            <div className="flex gap-4 text-sub">
              <span>{t('rev_size').replace('{v}', fmtSize(d.size))}</span>
              <span>{t('rev_mtime').replace('{v}', fmtTime(d.mtime))}</span>
              <span>{t('rev_sib').replace('{n}', String(d.siblings ?? 0))}</span>
            </div>

            <div className="pt-1">
              <div className="pb-1 text-[11px] text-sub">
                {t('rev_same').replace('{n}', String((d.same || []).length))}
                {d.same && d.same.length > 1 ? t('rev_same_hint') : ''}
              </div>
              {(d.same || []).slice(0, 8).map((s) => (
                <div key={s.p} className="flex items-center gap-2 border-b border-line/60 py-1 last:border-0">
                  <span className="min-w-0 flex-1 truncate" title={s.p}>
                    {midName(baseName(s.p), 46)}
                  </span>
                  <span className="shrink-0 text-[11px] text-sub">{fmtTime(s.m)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/* -------------------------------- 打包提示 ------------------------------ */

export function ZipToast({ text, onClose }: { text: string; onClose: () => void }) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000)
    return () => clearTimeout(t)
  }, [onClose])
  return (
    <div className="fixed bottom-10 left-1/2 z-40 -translate-x-1/2 rounded-lg bg-ink px-3 py-1.5 text-[12px] text-bg shadow-lg">
      <span className="mr-1 inline-block align-[-2px]">
        <IconZip size={13} />
      </span>
      {text}
    </div>
  )
}
