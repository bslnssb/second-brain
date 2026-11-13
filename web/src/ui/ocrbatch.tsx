/** 整本扫描件 OCR 队列面板（第 143 项实现，第 148 项起免费）。
 *
 * 轮询放在 App 里：面板打开时 1 秒一次，关掉后 5 秒一次（只为工具栏那个小圆点）。
 * 这里只负责发命令和把状态画出来 —— 关掉面板任务照样在后台跑，是刻意设计。
 */
import { useState } from 'react'
import { t } from '../lib/i18n'
import {
  ocrBatchOp,
  pickFolder,
  type OcrBatchStatus,
  type OcrOp,
} from '../api'
import { Modal } from './panels'

type Props = {
  open: boolean
  onClose: () => void
  status: OcrBatchStatus | null
  pro: boolean
  rows: { p: string }[]
  onRefresh: () => void
  onOpenAbout: () => void
}

const SCAN_EXT = /\.(pdf|png|jpe?g|bmp|webp|tiff?)$/i

function stateLabel(run?: string): string {
  if (run === 'running') return t('ocr_state_run')
  if (run === 'paused') return t('ocr_state_paused')
  if (run === 'done') return t('ocr_state_done')
  return t('ocr_state_idle')
}

function badge(st: string): { text: string; color: string } {
  if (st === 'done') return { text: '✓', color: '#16a34a' }
  if (st === 'failed') return { text: '!', color: '#dc2626' }
  if (st === 'skipped') return { text: '–', color: '#94a3b8' }
  if (st === 'running') return { text: '·', color: '#2563eb' }
  return { text: '·', color: '#94a3b8' }
}

export function OcrBatchModal({
  open, onClose, status, pro, rows, onRefresh, onOpenAbout,
}: Props) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  if (!open) return null

  const run = async (op: OcrOp, extra?: { paths?: string[]; dirs?: string[] }) => {
    if (busy) return
    setBusy(true)
    try {
      const r = await ocrBatchOp(op, extra)
      if (r.pro) setMsg(t('ocr_pro'))
      else if (r.ok === false) setMsg(t('ocr_err'))
      else {
        if (op === 'add') setMsg(t('ocr_added').replace('{n}', String(r.added ?? 0)))
        else setMsg('')
        onRefresh()
      }
    } catch {
      setMsg(t('ocr_err'))
    } finally {
      setBusy(false)
    }
  }

  const pick = async () => {
    const d = await pickFolder()
    if (d) void run('add', { dirs: [d] })
  }

  const addResults = () => {
    const paths = rows.map((r) => r.p).filter((p) => SCAN_EXT.test(p))
    if (paths.length) void run('add', { paths })
  }

  const s = status
  const total = s?.total ?? 0
  const pending = s?.pending ?? 0
  const running = s?.run === 'running'
  const pct = s?.pct ?? 0
  const canRetry = (s?.failed ?? 0) + (s?.skipped ?? 0) > 0
  const scanCount = rows.filter((r) => SCAN_EXT.test(r.p)).length

  return (
    <Modal title={t('ocr_title')} onClose={onClose} width={640}>
      {pro ? (
        <div className="space-y-4">
          <p className="text-[13px] leading-6 text-sub">{t('ocr_hint')}</p>
          <div className="rounded-xl border border-line bg-card p-4 text-[13px] leading-6">
            {t('ocr_pro')}
          </div>
          <button
            className="h-9 rounded-lg bg-accent px-4 text-[13px] text-white hover:opacity-90"
            onClick={() => { onClose(); onOpenAbout() }}
          >
            {t('ocr_go_license')}
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-[12px] leading-5 text-sub">{t('ocr_hint')}</p>

          {/* 命令区 */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              className="flex h-8 items-center rounded-lg bg-accent px-3 text-[12px] text-white hover:opacity-90 disabled:opacity-40"
              onClick={pick}
              disabled={busy}
              data-act="ocr-pick"
            >
              {t('ocr_pick')}
            </button>
            <button
              className="flex h-8 items-center rounded-lg border border-line px-3 text-[12px] text-sub hover:text-ink disabled:opacity-40"
              onClick={addResults}
              disabled={busy || scanCount === 0}
              title={t('ocr_from_results')}
              data-act="ocr-add-results"
            >
              {t('ocr_from_results')} ({scanCount})
            </button>
            <div className="ml-auto" />
            {running ? (
              <button
                className="h-8 rounded-lg border border-line px-3 text-[12px] text-sub hover:text-ink disabled:opacity-40"
                onClick={() => void run('pause')}
                disabled={busy}
                data-act="ocr-pause"
              >
                {t('ocr_pause')}
              </button>
            ) : (
              <button
                className="h-8 rounded-lg border border-line px-3 text-[12px] text-sub hover:text-ink disabled:opacity-40"
                onClick={() => void run('resume')}
                disabled={busy || pending === 0}
                data-act="ocr-resume"
              >
                {t('ocr_resume')}
              </button>
            )}
            <button
              className="h-8 rounded-lg border border-line px-3 text-[12px] text-sub hover:text-ink disabled:opacity-40"
              onClick={() => void run('cancel')}
              disabled={busy || pending === 0}
            >
              {t('ocr_cancel')}
            </button>
            <button
              className="h-8 rounded-lg border border-line px-3 text-[12px] text-sub hover:text-ink disabled:opacity-40"
              onClick={() => void run('retry')}
              disabled={busy || !canRetry}
            >
              {t('ocr_retry')}
            </button>
            <button
              className="h-8 rounded-lg border border-line px-3 text-[12px] text-sub hover:text-ink disabled:opacity-40"
              onClick={() => void run('clear')}
              disabled={busy || total === 0}
            >
              {t('ocr_clear')}
            </button>
          </div>

          {/* 进度 */}
          <div className="rounded-xl border border-line bg-card p-3">
            <div className="flex items-center justify-between text-[12px]">
              <span className="font-semibold">{stateLabel(s?.run)}</span>
              <span className="text-sub">
                {t('ocr_stat')
                  .replace('{done}', String(s?.done ?? 0))
                  .replace('{total}', String(total))
                  .replace('{failed}', String(s?.failed ?? 0))
                  .replace('{skipped}', String(s?.skipped ?? 0))}
              </span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded bg-line">
              <div
                className="h-full rounded bg-accent transition-[width] duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
            {running && s?.current ? (
              <p className="mt-2 truncate text-[11px] text-sub" title={s.current_path}>
                {t('ocr_current').replace('{p}', s.current)}
                {(s.page_total ?? 0) > 0
                  ? ` · ${t('ocr_page').replace('{i}', String(s.page_done ?? 0)).replace('{n}', String(s.page_total ?? 0))}`
                  : ''}
              </p>
            ) : null}
            {s?.note ? <p className="mt-2 text-[11px] text-sub">{s.note}</p> : null}
          </div>

          {msg ? <p className="text-[12px] text-accent">{msg}</p> : null}

          {/* 最近条目（新的在前） */}
          {total === 0 ? (
            <p className="rounded-xl border border-dashed border-line p-4 text-center text-[12px] leading-6 text-sub">
              {t('ocr_empty')}
              <br />
              {t('ocr_ready')}
            </p>
          ) : (
            <div className="max-h-[34vh] overflow-auto rounded-xl border border-line">
              {(s?.items || []).slice(-40).reverse().map((it, i) => {
                const b = badge(it.st)
                const name = it.p.split(/[\\/]/).pop() || it.p
                return (
                  <div
                    key={`${it.p}-${i}`}
                    className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-[12px] last:border-b-0"
                    title={it.p}
                  >
                    <span className="w-3 shrink-0 text-center font-bold" style={{ color: b.color }}>
                      {b.text}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{name}</span>
                    {it.pages > 0 ? (
                      <span className="shrink-0 text-[11px] text-sub">{it.pages}p</span>
                    ) : null}
                    {it.err ? (
                      <span className="max-w-[40%] shrink-0 truncate text-[11px] text-sub" title={it.err}>
                        {it.err}
                      </span>
                    ) : null}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
