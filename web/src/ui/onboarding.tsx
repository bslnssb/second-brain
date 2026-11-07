/**
 * 首启引导（装完第一次打开）。
 *
 * 两个来源合并：
 *  1) 旧模板的 Listary 式欢迎页（两栏：搜索文件 / 提升效率，配示意图 + 两个大按钮）；
 *  2) 后来补的自动建库面板（不等用户点、进度、暂停/跳过）。
 *
 * 流程：
 *   打开界面 → GET /api/firstrun + GET /api/settings
 *     ├─ 任一接口失败 → 安静收起（宁可不显示，也不要每次重启都弹）
 *     ├─ 需要建库（或正在建）→ 右下角小卡，边扫边搜，不挡界面
 *     ├─ 建库失败 → 独立的错误卡，明确告诉用户去设置里手动选目录
 *     ├─ 建好了 / 不需要建库 + 没看过欢迎 → 显示一次两栏欢迎
 *     └─ 看过 → 什么都不显示
 *
 * 「看过没有」以服务端 settings.json 为准，localStorage 只作离线兜底。
 * 关闭时必须 **await** POST /api/settings 落盘成功才隐藏；写失败要给可见提示，
 * 否则用户会陷入「每次启动都弹、界面上还没有任何报错」的静默死循环。
 *
 * 所有文案走 i18n（中/英/西/葡/印地）。
 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { t } from '../lib/i18n'
import { fill } from './maintenance'
import { IconSearch, IconClock, IconDoc, IconFolder, IconImage, IconGrid } from './icons'

type FirstRun = {
  ok?: boolean
  need?: boolean
  state?: string
  stage?: string
  msg?: string
  n?: number
  files?: number
  docs?: number
  eta?: number
  total?: number
  bg?: string
}

type Settings = {
  welcome_seen?: boolean
  firstrun_done?: string
}

type Props = {
  onOpenSettings: () => void
  onIndexed: (files: number) => void
}

// Sec-Fetch-* 是 Fetch 规范的 forbidden header：JS 手动设置会被浏览器忽略。
// 同源请求能过 /api/ 的守卫，靠的是浏览器自动带上 Sec-Fetch-Site: same-origin，
// 不是这里的「显式声明」。保留这个对象只是让意图显式，别再把它当安全边界。
const SAME = { 'Sec-Fetch-Site': 'same-origin' }

/** 统一在这里检查 r.ok：403 空 body / 500 HTML 以前会被 .catch(() => ({})) 吞掉，
 *  前端拿到 cfg={} 就判定「没看过」，于是欢迎页每次都弹且没有任何报错。 */
async function jget<T>(p: string): Promise<T> {
  const r = await fetch(p, { headers: SAME })
  if (!r.ok) throw new Error(`${p} ${r.status}`)
  return (await r.json()) as T
}

async function jpost<T>(p: string, body: unknown): Promise<T> {
  const r = await fetch(p, {
    method: 'POST',
    headers: { ...SAME, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${p} ${r.status}`)
  return (await r.json()) as T
}

const stageKey = (s?: string) =>
  s === 'discover' ? 'ob_step2' : s === 'names_done' ? 'ob_step3' : 'ob_step1'

const SEEN_KEY = 'sb_welcome_seen'

/** 服务端是权威：换端口、清 WebView2 缓存、浏览器模式都不会再弹。
 *  必须 await 成功才允许关界面 —— fire-and-forget 时点完立刻关窗口，请求可能没落盘。 */
const markSeen = async (): Promise<boolean> => {
  try {
    await jpost<{ ok?: boolean }>('/api/settings', { welcome_seen: true })
    try {
      localStorage.setItem(SEEN_KEY, '1')
    } catch {
      /* 隐私模式下 localStorage 可能不可写；服务端标记才是权威 */
    }
    return true
  } catch (e) {
    console.warn('[onboarding] welcome_seen 写入失败', e)
    return false
  }
}

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
  const [markErr, setMarkErr] = useState(false)
  const finished = useRef(false)

  useEffect(() => {
    let alive = true
    const boot = async () => {
      try {
        const [d, cfg] = await Promise.all([
          jget<FirstRun>('/api/firstrun'),
          jget<Settings>('/api/settings'),
        ])
        if (!alive || finished.current) return
        setSt(d)
        if (d.need || d.state === 'busy') {
          setPhase('run')
          if (d.need) {
            jpost('/api/firstrun', {}).catch((e) => console.warn('[onboarding] 启动建库失败', e))
          }
          return
        }
        // firstrun_done 是给老用户升级用的：库早就建好了，别因为换了实现
        // 再骚扰一次；全新安装的 settings.json 里没有这个字段，照常显示。
        const seen = cfg?.welcome_seen === true ||
                     localStorage.getItem(SEEN_KEY) === '1' ||
                     !!cfg?.firstrun_done
        setPhase(seen ? 'hidden' : 'welcome')
      } catch (e) {
        // 读不到配置就别弹：以前会 fallback 成 cfg={} → 每次启动都弹且无报错
        console.warn('[onboarding] 初始化接口失败，先不打扰用户', e)
        if (alive) setPhase('hidden')
      }
    }
    void boot()
    return () => {
      alive = false
    }
  }, [])

  // 建库中轮询
  useEffect(() => {
    if (phase !== 'run') return
    let alive = true
    const tick = () => {
      jget<FirstRun>('/api/firstrun')
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
        .catch((e) => console.warn('[onboarding] 进度查询失败', e))
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
      void markSeen().then((ok) => {
        if (ok) {
          setMarkErr(false)
          setPhase('hidden')
        } else {
          setMarkErr(true)
        }
      })
    }, 2400)
    return () => clearTimeout(id)
  }, [phase])

  /** mark=false：只是把建库小卡收起来，不代表用户看过欢迎页。
   *  以前 run 阶段点 ✕ 也写 welcome_seen，用户从没见过教程就被标成看过。 */
  const close = async (mark = true): Promise<boolean> => {
    if (mark && !(await markSeen())) {
      setMarkErr(true)
      return false
    }
    finished.current = true
    setMarkErr(false)
    setPhase('hidden')
    return true
  }

  const togglePause = () => {
    const next = !paused
    setPaused(next)
    jpost('/api/firstrun', next ? { pause: 1 } : { resume: 1 })
      .catch((e) => console.warn('[onboarding] 暂停/继续失败', e))
  }

  const openSettings = async (mark = true) => {
    if (await close(mark)) onOpenSettings()
  }

  const skip = async () => {
    // cancel 失败也要让用户进设置手动选目录
    try {
      await jpost('/api/firstrun', { cancel: 1 })
    } catch (e) {
      console.warn('[onboarding] 取消建库失败', e)
    }
    await openSettings()
  }

  const openMini = async () => {
    // 原生桥：优先唤出快搜窗，没有桥就当作「开始使用」
    try {
      const api = (window as unknown as { pywebview?: { api?: { open_mini?: () => void } } }).pywebview?.api
      if (api?.open_mini) {
        api.open_mini()
        await close()
        return
      }
    } catch {
      /* ignore */
    }
    await close()
  }

  if (phase === 'boot' || phase === 'hidden') return null

  const files = st.files || 0
  const docs = st.docs || 0
  const busy = phase === 'run'
  const errBar = markErr ? (
    <div className="mt-2 rounded-lg border border-red-300 bg-red-50 px-2.5 py-1.5 text-[11px] leading-snug text-red-700">
      {t('ob_mark_err')}
    </div>
  ) : null

  // 建库中：**不挡界面**的小卡片（右下角），用户立刻就能在搜索框里打字，边扫边搜。
  if (busy) {
    return createPortal(
      <div className="fixed bottom-4 right-4 z-50 w-[340px] rounded-xl border border-line bg-bg p-3 shadow-2xl">
        <div className="flex items-center gap-2 text-[12.5px] font-medium">
          <span className={'inline-block h-2 w-2 rounded-full bg-accent ' + (paused ? '' : 'animate-pulse')} />
          <span className="min-w-0 flex-1 truncate">{t('ob_running_badge')}</span>
          <button className="text-[11px] text-sub hover:text-ink" onClick={() => void close(false)} title={t('ob_skip')}>
            ✕
          </button>
        </div>
        <div className="mt-2 flex items-center gap-2 text-[11.5px] text-sub">
          <span className="min-w-0 flex-1 truncate">{paused ? t('ob_paused') : t(stageKey(st.stage))}</span>
          <span className="shrink-0">
            <b className="text-ink">{files.toLocaleString()}</b> {t('ob_files')}
          </span>
        </div>
        {/* 进度条不再写死 18%/65%：有 total（如正文阶段）就是真百分比；文件名阶段
            总数未知的换成动画条，诚实一点——别让用户以为 65% 是算出来的 */}
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-line">
          {st.total && st.total > 0 && st.n ? (
            <div className="h-full rounded-full bg-accent transition-all"
                 style={{ width: `${Math.min(95, Math.round((st.n / st.total) * 100))}%` }} />
          ) : (
            <div className="h-full w-1/3 rounded-full bg-accent animate-pulse" />
          )}
        </div>
        {st.eta && st.eta > 0 && (
          <div className="mt-1.5 text-[11px] text-sub opacity-80">{fill(t('eta_s'), { s: st.eta })}</div>
        )}
        <div className="mt-2 text-[11px] leading-snug text-sub">{t('ob_can_search')}</div>
        {errBar}
        <div className="mt-2 flex justify-end gap-2">
          <button className="rounded-lg border border-line px-2.5 py-1 text-[11.5px] text-sub hover:text-ink" onClick={() => void skip()}>
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

  // 建库失败：以前这里照样渲染「欢迎使用」两栏页，标题固定、只有底部一行小字，
  // 按钮一点就 markSeen()，用户完全不知道自动建库挂了。现在独立成错误卡，
  // 且不写 welcome_seen —— 下次启动 first_run_need() 会把 error 当非终态再试一次。
  if (phase === 'error') {
    return createPortal(
      <div className="fixed bottom-4 right-4 z-50 w-[380px] rounded-xl border border-line bg-bg p-4 shadow-2xl">
        <div className="text-[13px] font-medium">{t('ob_err_title')}</div>
        <div className="mt-1.5 text-[11.5px] leading-relaxed text-sub">{t('ob_err_sub')}</div>
        {errBar}
        <div className="mt-3 flex justify-end gap-2">
          <button
            className="rounded-lg border border-line px-2.5 py-1 text-[11.5px] text-sub hover:text-ink"
            onClick={() => void close(false)}
          >
            {t('ob_start_search')}
          </button>
          <button
            className="rounded-lg border border-line px-2.5 py-1 text-[11.5px]"
            onClick={() => void openSettings(false)}
          >
            {t('ob_open_settings')}
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
          {phase === 'done' ? t('ob_done_title') : t('ob_sub')}
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
        {phase === 'done' && (
          <div className="mt-4 rounded-xl border border-line bg-card px-4 py-3">
            <div className="flex items-center gap-2 text-[12.5px]">
              <span className="inline-block h-2 w-2 rounded-full bg-accent" />
              <span className="min-w-0 flex-1 truncate">{t('ob_done_title')}</span>
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
              <div className="h-full rounded-full bg-accent transition-all" style={{ width: '100%' }} />
            </div>
          </div>
        )}

        {/* 按钮：建好/看过之后给 Listary 式两个大按钮 */}
        <div className="mt-5 flex items-center justify-between gap-3">
          <button className="text-[11.5px] text-sub hover:text-ink" onClick={() => void openSettings()}>
            {t('ob_open_settings')}
          </button>
          <div className="flex gap-2">
            <button className="rounded-lg border border-line px-4 py-2 text-[12.5px] text-sub hover:text-ink" onClick={() => void close()}>
              {t('ob_start_search')}
            </button>
            <button className="rounded-lg bg-accent px-4 py-2 text-[12.5px] font-medium text-white" onClick={() => void openMini()}>
              {t('ob_open_quick')}
            </button>
          </div>
        </div>

        {errBar}
        <div className="mt-2 text-right text-[11px] text-sub">{t('ob_ctrl_hint')}</div>
        {phase === 'done' && <div className="mt-3 text-[11.5px] leading-relaxed text-sub">{t('ob_done_bg')}</div>}
      </div>
    </div>,
    document.body,
  )
}
