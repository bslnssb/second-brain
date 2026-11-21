import { useEffect, useRef, useState } from 'react'
import { t } from '../lib/i18n'
import { IconClose, IconDoc, IconImage, IconSearch, IconSnip } from './icons'

export type SnipResultData = {
  status: string
  text: string
  lines: Array<{ text: string; score: number }>
  img_size?: [number, number]
  ts?: number
}

export function SnipModal({
  open,
  onClose,
  result,
  onSearch,
}: {
  open: boolean
  onClose: () => void
  result: SnipResultData | null
  onSearch: (txt: string) => void
}) {
  const [copied, setCopied] = useState(false)
  const [copyImgTip, setCopyImgTip] = useState(false)
  const [savedPath, setSavedPath] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [editedText, setEditedText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (result) {
      setEditedText(result.text || '')
    }
  }, [result])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      } else if (e.key === 'Enter' && (e.ctrlKey || document.activeElement !== textareaRef.current)) {
        e.preventDefault()
        doCopy()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, editedText, onClose])

  if (!open || !result) return null

  const doCopy = async () => {
    const textToCopy = editedText
    try {
      await fetch('/api/snip/copy_text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: textToCopy }),
      })
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(textToCopy)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }
    }
  }

  const doCopyImage = () => {
    setCopyImgTip(true)
    setTimeout(() => setCopyImgTip(false), 2000)
  }

  const doSaveImage = async () => {
    if (saving) return
    setSaving(true)
    try {
      const res = await fetch('/api/snip/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      }).then((r) => r.json())
      if (res && res.ok) {
        setSavedPath(res.path)
        setTimeout(() => setSavedPath(null), 3000)
      }
    } catch {}
    setSaving(false)
  }

  const lineCount = result.lines ? result.lines.length : 0
  const sizeInfo = result.img_size ? `${result.img_size[0]}×${result.img_size[1]}` : ''

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-[2px] animate-fadeIn"
      onClick={onClose}
    >
      <div
        className="relative flex w-full max-w-[620px] flex-col rounded-2xl border border-line bg-card p-5 shadow-2xl text-ink"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏 */}
        <div className="flex items-center justify-between border-b border-line pb-3">
          <div className="flex items-center gap-2">
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-accent/10 text-accent">
              <IconSnip size={16} />
            </div>
            <h3 className="text-[15px] font-semibold">{t('snip_title')}</h3>
            <span className="text-[12px] text-sub">
              {lineCount > 0 ? t('snip_lines').replace('{n}', String(lineCount)) : t('snip_empty')} {sizeInfo && `· ${sizeInfo}`}
            </span>
          </div>
          <button
            onClick={onClose}
            className="grid h-7 w-7 place-items-center rounded-lg text-sub hover:bg-line/40 hover:text-ink transition-colors"
            title={t('clip_close_title')}
          >
            <IconClose size={14} />
          </button>
        </div>

        {/* 内容区：左缩略图 + 右文本框 */}
        <div className="my-4 flex gap-4">
          <div className="flex h-44 w-44 shrink-0 flex-col items-center justify-center rounded-xl border border-line bg-bg/50 p-1.5 overflow-hidden">
            <img
              src={`/api/snip/thumb?_t=${result.ts || Date.now()}`}
              alt={t('app_preview_heading')}
              className="max-h-full max-w-full rounded object-contain select-none"
            />
          </div>
          <div className="flex flex-1 flex-col">
            <textarea
              ref={textareaRef}
              value={editedText}
              onChange={(e) => setEditedText(e.target.value)}
              placeholder={t('snip_empty')}
              spellCheck={false}
              className="h-44 w-full resize-none rounded-xl border border-line bg-bg p-3 text-[13px] leading-relaxed outline-none focus:border-accent focus:ring-1 focus:ring-accent/20"
            />
          </div>
        </div>

        {/* 状态提示 */}
        {savedPath && (
          <div className="mb-3 rounded-lg bg-emerald-500/10 px-3 py-1.5 text-[12px] text-emerald-600 dark:text-emerald-400">
            {t('snip_save_ok').replace('{p}', savedPath)}
          </div>
        )}

        {/* 底部操作区 */}
        <div className="flex items-center justify-between pt-1">
          <div className="flex items-center gap-2">
            <button
              onClick={doCopy}
              className="flex h-8 items-center gap-1.5 rounded-lg bg-accent px-4 text-[13px] font-medium text-white shadow-sm hover:bg-accent/90 transition-colors"
            >
              <IconDoc size={14} />
              {copied ? t('snip_copied') : `${t('snip_copy')} (Enter)`}
            </button>
            <button
              onClick={doCopyImage}
              className="flex h-8 items-center gap-1.5 rounded-lg border border-line px-3 text-[13px] text-sub hover:bg-line/30 hover:text-ink transition-colors"
            >
              <IconImage size={14} />
              {copyImgTip ? t('snip_img_copied') : t('snip_copy_img')}
            </button>
            <button
              onClick={() => onSearch(editedText.replace(/\r?\n+/g, ' ').trim())}
              className="flex h-8 items-center gap-1.5 rounded-lg border border-line px-3 text-[13px] text-sub hover:bg-line/30 hover:text-ink transition-colors"
            >
              <IconSearch size={14} />
              {t('snip_search')}
            </button>
            <button
              onClick={doSaveImage}
              disabled={saving}
              className="flex h-8 items-center gap-1.5 rounded-lg border border-line px-3 text-[13px] text-sub hover:bg-line/30 hover:text-ink transition-colors disabled:opacity-50"
            >
              {t('snip_save_img')}
            </button>
          </div>
          <button
            onClick={onClose}
            className="flex h-8 items-center rounded-lg px-3 text-[13px] text-sub hover:text-ink transition-colors"
          >
            {t('lic_cancel')}
          </button>
        </div>
      </div>
    </div>
  )
}
