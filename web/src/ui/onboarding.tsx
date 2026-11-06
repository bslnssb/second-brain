/**
 * 首启引导（装完第一次打开）。
 *
 * 两个来源合并：
 *  1) 旧模板的 Listary 式欢迎页（两栏：搜索文件 / 提升效率，配示意图 + 两个大按钮）；
 *  2) 后来补的自动建库面板（不等用户点、进度、暂停/跳过）。
 *
 * 流程：
 *   打开界面 → GET /api/firstrun
 *     ├─ 需要建库（或正在建）→ 直接 POST 开跑，两栏照常显示，进度条代替按钮
 *     ├─ 不需要建库 + 没看过欢迎 → 只显示一次两栏欢迎（localStorage 记 sb_welcome_seen）
 *     └─ 都不需要 → 什么都不显示
 *
 * 所有文案走 i18n（中/英/西/葡/印地）。
 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { t } from '../lib/i18n'
import { IconSearch, IconClock, IconDoc, IconFolder, IconImage, IconGrid } from './icons'

type FirstRun = {
  ok?: boolean
  need?: boolean
  state?: string
  stage?: string
  files?: number
  docs?: number
  bg?: string
}

type Props = {
  onOpenSettings: () => void
  onIndexed: (files: number) => void
}

const jget = (p: string) => fetch(p).then((r) => r.json())
const jpost = (p: string, body: unknown) =>
  fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => r.json())

const stageKey = (s?: string) =>
  s === 'discover' ? 'ob_step2' : s === 'names_done' ? 'ob_step3' : 'ob_step1'

const SEEN_KEY = 'sb_welcome_seen'

/** 左边那栏的示意图：一个搜索框 + 三行结果（纯 CSS/SVG，不依赖截图） */
function MockSearch() {
  const rows: [JSX.Element, string, string][] = [
    [<IconDoc key="a" size={12} />, t('ob_mock_f1'), 'D:\\Work\\Reports'],
    [<IconFolder key="b" size={12} />, t('ob_mock_f2'), 'D:\\Work\\Archive'],
    [<IconImage key="c" size={12} />, t('ob_mock_f3'), 'C:\\Users\\me\\Pictures'],
  ]
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-card">
      <div className="flex items-center gap-2 border-b border-line px-2.5 py-2">
        <IconSearch size={13} />
        <span className="text-[12px] text-sub">{t('ob_mock_q')}</span>
      </div>
      {rows.map(([icon, name, dir], i) => (
        <div key={i} className={'flex items-center gap-2 px-2.5 py-1.5 text-[11.5px] ' + (i === 0 ? 'bg-accent/10' : '')}>
          <span className="text-sub">{icon}</span>
          <span className="truncate text-ink">{name}</span>
          <span className="ml-auto shrink-0 text-[10px] text-sub">{dir}</span>
        </div>
      ))}
    </div>
  )
}

/** 右边那栏的示意图：不输入时列最近改过的文件 + 分类入口 */
function MockRecent() {
  const rows: [JSX.Element, string, string][] = [
    [<IconClock key="a" size={12} />, t('ob_mock_file'), t('ob_mock_now')],
    [<IconFolder key="b" size={12} />, t('ob_mock_folder'), t('ob_mock_today')],
    [<IconGrid key="c" size={12} />, t('ob_mock_images'), t('ob_mock_week')],
  ]
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-card">
      <div className="flex items-center gap-2 border-b border-line px-2.5 py-2 text-[12px] text-sub">
        {t('ob_mock_recent')}
      </div>
      {rows.map(([icon, name, when], i) => (
        <div key={i} className="flex items-center gap-2 px-2.5 py-1.5 text-[11.5px]">
          <span className="text-sub">{icon}</span>
          <span className="truncate text-ink">{name}</span>
          <span className="ml-auto shrink-0 text-[10px] text-sub">{when}</span>
        </div>
      ))}
    </div>
  )
}

export function Onboarding({ onOpenSettings, onIndexed }: Props) {
  const [phase, setPhase] = useState<'boot' | 'hidden' | 'welcome' | 'run' | 'done' | 'error'>('boot')
  const [st, setSt] = useState<FirstRun>({})
  const [paused, setPaused] = useState(false)
  const finished = useRef(false)

  useEffect(() => {
    let alive = true
    jget('/api/firstrun')
      .then((d: FirstRun) => {
        if (!alive || finished.current) return
        setSt(d)
        if (d.need || d.state === 'busy') {
          setPhase('run')
          if (d.need) jpost('/api/firstrun', {}).catch(() => {})
          return
        }
        // 老用户：只在没看过欢迎页时显示一次
        if (localStorage.getItem(SEEN_KEY) !== '1') setPhase('welcome')
        else setPhase('hidden')
      })
      .catch(() => {
        if (alive) setPhase('hidden')
      })
    return () => {
      alive = false
    }
  }, [])

  // 建库中轮询
  useEffect(() => {
    if (phase !== 'run') return
    let alive = true
    const tick = () => {
      jget('/api/firstrun')
        .then((d: FirstRun) => {
          if (!alive || finished.current) return
          setSt(d)
          if (d.state === 'done') {
            finished.current = true
            setPhase('done')
            onIndexed(d.files || 0)
          } else if (d.state === 'error') {
            finished.current = true
            setPhase('error')
          } else if (d.state !== 'busy' && d.need === false) {
            finished.current = true
            setPhase('done')
            onIndexed(d.files || 0)
          }
        })
        .catch(() => {})
    }
    tick()
    const id = setInterval(tick, 900)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [phase, onIndexed])

  // 建好了就自动收起：别让用户一直停在这张卡上（实测虚拟机里会以为卡死）
  useEffect(() => {
    if (phase !== 'done') return
    const id = setTimeout(() => {
      finished.current = true
      localStorage.setItem(SEEN_KEY, '1')
      setPhase('hidden')
    }, 2400)
    return () => clearTimeout(id)
  }, [phase])

  const close = (markSeen = true) => {
    finished.current = true
    if (markSeen) localStorage.setItem(SEEN_KEY, '1')
    setPhase('hidden')
  }

  const togglePause = () => {
    const next = !paused
    setPaused(next)
    jpost('/api/firstrun', next ? { pause: 1 } : { resume: 1 }).catch(() => {})
  }

  const skip = () => {
    jpost('/api/firstrun', { cancel: 1 }).catch(() => {})
    close()
    onOpenSettings()
  }

  const openMini = () => {
    // 原生桥：优先唤出快搜窗，没有桥就当作「开始使用」
    try {
      const api = (window as unknown as { pywebview?: { api?: { open_mini?: () => void } } }).pywebview?.api
      if (api?.open_mini) {
        api.open_mini()
        close()
        return
      }
    } catch {
      /* ignore */
    }
    close()
  }

  if (phase === 'boot' || phase === 'hidden') return null

  const files = st.files || 0
  const docs = st.docs || 0
  const busy = phase === 'run'

  // 建库中：**不挡界面**的小卡片（右下角），用户立刻就能在搜索框里打字，边扫边搜。
  if (busy) {
    return createPortal(
      <div className="fixed bottom-4 right-4 z-50 w-[340px] rounded-xl border border-line bg-bg p-3 shadow-2xl">
        <div className="flex items-center gap-2 text-[12.5px] font-medium">
          <span className={'inline-block h-2 w-2 rounded-full bg-accent ' + (paused ? '' : 'animate-pulse')} />
          <span className="min-w-0 flex-1 truncate">{t('ob_running_badge')}</span>
          <button className="text-[11px] text-sub hover:text-ink" onClick={() => close()} title={t('ob_skip')}>
            ✕
          </button>
        </div>
        <div className="mt-2 flex items-center gap-2 text-[11.5px] text-sub">
          <span className="min-w-0 flex-1 truncate">{paused ? t('ob_paused') : t(stageKey(st.stage))}</span>
          <span className="shrink-0">
            <b className="text-ink">{files.toLocaleString()}</b> {t('ob_files')}
          </span>
        </div>
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-line">
          <div className="h-full rounded-full bg-accent transition-all" style={{ width: files > 0 ? '65%' : '18%' }} />
        </div>
        <div className="mt-2 text-[11px] leading-snug text-sub">{t('ob_can_search')}</div>
        <div className="mt-2 flex justify-end gap-2">
          <button className="rounded-lg border border-line px-2.5 py-1 text-[11.5px] text-sub hover:text-ink" onClick={skip}>
            {t('ob_skip')}
          </button>
          <button className="rounded-lg border border-line px-2.5 py-1 text-[11.5px]" onClick={togglePause}>
            {paused ? t('ob_resume') : t('ob_pause')}
          </button>
        </div>
      </div>,
      document.body,
    )
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-auto bg-black/30 p-4">
      <div className="max-h-[94vh] w-[760px] max-w-[96vw] overflow-auto rounded-2xl border border-line bg-bg p-6 shadow-2xl">
        <div className="text-[19px] font-semibold">{t('ob_welcome')}</div>
        <div className="mt-1 text-[12.5px] leading-relaxed text-sub">
          {busy ? t('ob_sub_running') : phase === 'done' ? t('ob_done_title') : t('ob_sub')}
        </div>

        {/* 两栏：左边搜索、右边不用打字（照 Listary 的样子，但用我们自己的界面画） */}
        <div className="mt-4 grid grid-cols-2 gap-4">
          <div className="rounded-xl border border-line p-3">
            <div className="mb-2 text-[13px] font-medium">{t('ob_col1')}</div>
            <MockSearch />
            <div className="mt-2 text-[11.5px] leading-snug text-sub">{t('ob_col1_cap')}</div>
          </div>
          <div className="rounded-xl border border-line p-3">
            <div className="mb-2 text-[13px] font-medium">{t('ob_col2')}</div>
            <MockRecent />
            <div className="mt-2 text-[11.5px] leading-snug text-sub">{t('ob_col2_cap')}</div>
          </div>
        </div>

        {/* 建库进度（只在第一次建库时出现） */}
        {(busy || phase === 'done') && (
          <div className="mt-4 rounded-xl border border-line bg-card px-4 py-3">
            <div className="flex items-center gap-2 text-[12.5px]">
              <span className={'inline-block h-2 w-2 rounded-full bg-accent ' + (busy && !paused ? 'animate-pulse' : '')} />
              <span className="min-w-0 flex-1 truncate">
                {phase === 'done' ? t('ob_done_title') : paused ? t('ob_paused') : t(stageKey(st.stage))}
              </span>
              <span className="shrink-0 text-[11.5px] text-sub">
                <b className="text-ink">{files.toLocaleString()}</b> {t('ob_files')}
                {docs > 0 && (
                  <>
                    {' · '}
                    <b className="text-ink">{docs.toLocaleString()}</b> {t('ob_docs')}
                  </>
                )}
              </span>
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-line">
              <div
                className="h-full rounded-full bg-accent transition-all"
                style={{ width: phase === 'done' ? '100%' : files > 0 ? '65%' : '18%' }}
              />
            </div>
          </div>
        )}

        {/* 按钮：建库中给「先跳过 / 暂停」，否则给 Listary 式两个大按钮 */}
        <div className="mt-5 flex items-center justify-between gap-3">
          <button className="text-[11.5px] text-sub hover:text-ink" onClick={() => { close(); onOpenSettings() }}>
            {t('ob_open_settings')}
          </button>
          {busy ? (
            <div className="flex gap-2">
              <button className="rounded-lg border border-line px-3 py-1.5 text-[12px] text-sub hover:text-ink" onClick={skip}>
                {t('ob_skip')}
              </button>
              <button className="rounded-lg border border-line px-3 py-1.5 text-[12px]" onClick={togglePause}>
                {paused ? t('ob_resume') : t('ob_pause')}
              </button>
            </div>
          ) : (
            <div className="flex gap-2">
              <button className="rounded-lg border border-line px-4 py-2 text-[12.5px] text-sub hover:text-ink" onClick={() => close()}>
                {t('ob_start_search')}
              </button>
              <button className="rounded-lg bg-accent px-4 py-2 text-[12.5px] font-medium text-white" onClick={openMini}>
                {t('ob_open_quick')}
              </button>
            </div>
          )}
        </div>

        {!busy && <div className="mt-2 text-right text-[11px] text-sub">{t('ob_ctrl_hint')}</div>}
        {phase === 'done' && <div className="mt-3 text-[11.5px] leading-relaxed text-sub">{t('ob_done_bg')}</div>}
        {phase === 'error' && <div className="mt-3 text-[11.5px] leading-relaxed text-sub">{t('ob_err_sub')}</div>}
      </div>
    </div>,
    document.body,
  )
}
