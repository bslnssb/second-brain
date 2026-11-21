import React, { useEffect, useState } from 'react'
import { t } from '../lib/i18n'
import { IconClipboard, IconSearch } from './icons'

export interface ClipboardItem {
  id: number
  kind: 'text' | 'image' | 'file'
  content: string
  preview: string
  detail: string
  created_at: number
  relative_time: string
  pinned: boolean
  /** 第 339 项：图片文件（Ctrl+C 复制文件）也会带回缩略图文件名，空串表示没有 */
  thumb?: string
}

interface ClipboardModalProps {
  isOpen: boolean
  onClose: () => void
  onToast: (msg: string) => void
}

export function ClipboardModal({ isOpen, onClose, onToast }: ClipboardModalProps) {
  const [items, setItems] = useState<ClipboardItem[]>([])
  const [loading, setLoading] = useState(false)
  const [filterKind, setFilterKind] = useState<'all' | 'fav' | 'text' | 'image' | 'file'>('all')
  const [query, setQuery] = useState('')

  const fetchItems = async (kind = filterKind, q = query) => {
    try {
      setLoading(true)
      const res = await fetch(`/api/clipboard?kind=${kind}&q=${encodeURIComponent(q)}&limit=100`, {
        headers: { 'Sec-Fetch-Site': 'same-origin' }
      })
      const data = await res.json()
      if (data && data.ok) {
        setItems(data.items || [])
      }
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (isOpen) {
      fetchItems(filterKind, query)
    }
  }, [isOpen, filterKind, query])

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const handleCopy = async (id: number) => {
    try {
      const res = await fetch('/api/clipboard/copy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
        body: JSON.stringify({ id })
      })
      const data = await res.json()
      if (data && data.ok) {
        onToast(data.msg || t('clip_copied'))
        onClose()
      } else {
        onToast(data?.msg || t('clip_copy_fail'))
      }
    } catch {
      onToast(t('clip_copy_fail'))
    }
  }

  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation()
    try {
      const res = await fetch('/api/clipboard/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
        body: JSON.stringify({ id })
      })
      const data = await res.json()
      if (data && data.ok) {
        setItems((prev) => prev.filter((it) => it.id !== id))
      }
    } catch {
      // ignore
    }
  }

  const handleTogglePin = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation()
    try {
      const res = await fetch('/api/clipboard/pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
        body: JSON.stringify({ id })
      })
      const data = await res.json()
      if (data && data.ok) {
        setItems((prev) =>
          prev.map((it) => (it.id === id ? { ...it, pinned: !!data.pinned } : it))
        )
        onToast(data.pinned ? t('clip_pin_toast_on') : t('clip_pin_toast_off'))
      }
    } catch {
      // ignore
    }
  }

  const handleClear = async () => {
    if (!window.confirm(t('clip_clear_confirm'))) return
    try {
      const res = await fetch('/api/clipboard/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' }
      })
      const data = await res.json()
      if (data && data.ok) {
        fetchItems(filterKind, query)
        onToast(t('clip_cleared'))
      }
    } catch {
      // ignore
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 backdrop-blur-[2px] p-4 animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="flex flex-col w-full max-w-[620px] max-h-[82vh] rounded-xl border border-line bg-card shadow-2xl overflow-hidden text-ink"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line px-4 py-3 bg-bg/40">
          <div className="flex items-center gap-2">
            <IconClipboard size={18} className="text-accent" />
            <span className="font-semibold text-[14px]">{t('clip_title')}</span>
          </div>

          <div className="flex items-center gap-2">
            {items.length > 0 && (
              <button
                type="button"
                onClick={handleClear}
                className="text-[12px] text-sub hover:text-red-500 px-2 py-1 rounded hover:bg-line/40 transition-colors"
                title={t('clip_clear')}
              >
                {t('clip_clear')}
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="text-sub hover:text-ink h-7 w-7 grid place-items-center rounded-lg hover:bg-line/50 transition-colors text-lg leading-none"
              title={t('clip_close_title')}
            >
              ×
            </button>
          </div>
        </div>

        {/* Toolbar: Search & Filters */}
        <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b border-line bg-card">
          <div className="relative flex-1 min-w-[180px]">
            <IconSearch size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-sub pointer-events-none" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('clip_search_ph')}
              className="w-full h-8 pl-8 pr-3 text-[13px] rounded-lg border border-line bg-bg/50 focus:border-accent focus:bg-card outline-none text-ink placeholder:text-sub transition-colors"
            />
          </div>

          <div className="flex items-center gap-1">
            {(
              [
                ['all', t('clip_all')],
                ['fav', t('clip_fav')],
                ['text', t('clip_text')],
                ['image', t('clip_image')],
                ['file', t('clip_file')]
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                type="button"
                onClick={() => setFilterKind(k)}
                className={`px-2.5 py-1 text-[12px] rounded-md transition-colors ${
                  filterKind === k ? 'bg-accent text-white font-medium shadow-sm' : 'text-sub hover:text-ink hover:bg-line/40'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Items List */}
        <div className="flex-1 overflow-y-auto divide-y divide-line/60 p-1.5 min-h-[220px]">
          {loading && items.length === 0 ? (
            <div className="py-16 text-center text-sub text-[13px]">{t('clip_loading')}</div>
          ) : items.length === 0 ? (
            <div className="py-16 text-center text-sub text-[13px]">{t('clip_empty')}</div>
          ) : (
            items.map((it) => (
              <div
                key={it.id}
                onClick={() => handleCopy(it.id)}
                className="group flex items-start gap-3 p-3 rounded-lg hover:bg-line/35 cursor-pointer transition-colors relative"
              >
                {/* Kind indicator / Thumbnail */}
                <div className="shrink-0 mt-0.5">
                  {it.kind === 'image' || it.thumb ? (
                    <div className="w-14 h-14 rounded-md overflow-hidden border border-line bg-bg flex items-center justify-center">
                      <img
                        src={`/api/clipboard/thumb/${it.kind === 'image' ? it.content : it.thumb}`}
                        alt="thumb"
                        className="max-w-full max-h-full object-contain"
                        onError={(e) => {
                          ;(e.currentTarget as HTMLImageElement).style.display = 'none'
                        }}
                      />
                    </div>
                  ) : it.kind === 'file' ? (
                    <div className="w-9 h-9 rounded-md bg-accent/10 text-accent grid place-items-center text-sm font-semibold">
                      📁
                    </div>
                  ) : (
                    <div className="w-9 h-9 rounded-md bg-line/40 text-sub grid place-items-center text-xs font-mono">
                      TXT
                    </div>
                  )}
                </div>

                {/* Content info */}
                <div className="flex-1 min-w-0 pr-12">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[11px] font-medium text-accent px-1.5 py-0.5 rounded bg-accent/10">
                      {it.detail}
                    </span>
                    <span className="text-[11px] text-sub">{it.relative_time}</span>
                  </div>

                  {it.kind === 'text' ? (
                    <div className="text-[13px] leading-relaxed line-clamp-3 text-ink select-none font-mono">
                      {it.preview}
                    </div>
                  ) : (
                    <div className="text-[13px] leading-normal truncate text-ink font-medium select-none">
                      {it.preview}
                    </div>
                  )}
                </div>

                {/* Actions */}
                <div className="absolute right-3 top-3 flex items-center gap-1">
                  <button
                    type="button"
                    onClick={(e) => handleTogglePin(e, it.id)}
                    className={`p-1 rounded hover:bg-line/60 transition-colors text-sm leading-none ${
                      it.pinned
                        ? 'text-amber-500 opacity-100'
                        : 'text-sub opacity-0 group-hover:opacity-100 hover:text-amber-500'
                    }`}
                    title={it.pinned ? t('clip_pin_del') : t('clip_pin_add')}
                  >
                    {it.pinned ? '★' : '☆'}
                  </button>
                  <button
                    type="button"
                    onClick={(e) => handleDelete(e, it.id)}
                    className="p-1 rounded hover:bg-line/60 text-sub hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                    title={t('clip_del_title')}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
