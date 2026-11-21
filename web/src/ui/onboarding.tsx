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
  /** 非 0 即「从使用帮助重看教程」：绕过 welcome_seen，直接放欢迎页（第 171 项） */
  replay?: number
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

/** 第 204 项：教程改成 Listary 式「分页 + 动态 GIF」。
 *  原来的两栏静态 CSS 示意图（MockSearch / MockRecent）已删除：
 *  GIF 放在 web/public/assets/tour-1..4.gif，由 Vite 原样拷进 dist/assets/。
 *  动图里**不放任何自然语言文字**，标题/说明/按钮全部走 i18n，
 *  这样中/英/西/葡/印地五种语言看到的文案都是自己的语言。
 */

export function Onboarding({ onOpenSettings, onIndexed, replay = 0 }: Props) {
  const [phase, setPhase] = useState<'boot' | 'hidden' | 'welcome' | 'run' | 'done' | 'error'>('boot')
  const [st, setSt] = useState<FirstRun>({})
  const [paused, setPaused] = useState(false)
  const [markErr, setMarkErr] = useState(false)
  /** 教程页码 0..3；replay 重看时从第一页开始 */
  const [tour, setTour] = useState(0)
  const finished = useRef(false)

  useEffect(() => {
    let alive = true
    // 重看教程：不看 welcome_seen，也不去碰建库流程，直接放欢迎页
    if (replay) {
      finished.current = false
      setMarkErr(false)
      setTour(0)
      setPhase('welcome')
      return () => {
        alive = false
      }
    }
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
  }, [replay])

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

  // 第 346 项：**不再自动收起**。
  // 上一版在这里挂了 2.4s 定时器把 phase 从 done 打到 hidden —— 那是「只剩一张小进度卡」
  // 时代为了防止用户以为卡死的补丁。后来 done 阶段渲染的是整页四步教程，定时器照旧把
  // 教程在两秒半后自己关掉：用户「还没点就没了」，或者刚点「下一步」就没了。
  // 现在收起只有一个来源：用户点「开始使用」/「跳过」/「直接开始」（都走 close()）。

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
    <div
      className="fixed inset-x-0 bottom-0 z-50 flex items-center justify-center overflow-auto bg-black/30 p-4"
      style={{ top: 'var(--sb-title, 0px)' }}
    >
      <div className="max-h-[94vh] w-[760px] max-w-[96vw] overflow-auto rounded-2xl border border-line bg-bg p-6 shadow-2xl">
        <div className="text-[12px] tracking-wide text-sub">0{tour + 1} / 04</div>
        <div className="mt-2 text-[26px] font-bold leading-tight">
          {t(`ob_tour_t${tour + 1}`)}
        </div>
        <div className="mt-2 text-[13px] leading-relaxed text-sub">
          {t(`ob_tour_d${tour + 1}`)}
        </div>

        {/* Listary 式分页动图：每页一张自制 GIF，循环播放 */}
        <div className="mt-4 overflow-hidden rounded-xl border border-line bg-card">
          <img src={`./assets/tour-${tour + 1}.gif`} alt="" className="block w-full" />
        </div>
        <div className="mt-3 flex items-center justify-center gap-1.5">
          {[1, 2, 3, 4].map((n) => (
            <button
              key={n}
              aria-label={`step ${n}`}
              onClick={() => setTour(n - 1)}
              className={'h-1.5 rounded-full transition-all ' +
                (n - 1 === tour ? 'w-6 bg-accent' : 'w-1.5 bg-line hover:bg-sub')}
            />
          ))}
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
            {/* 第 346 项：索引完了但一个文件都没有（新机器 / 虚拟机里常见）——
                以前只留一句「建好了，可以搜了」，用户回到界面就看到「索引里还没有文件」，
                不知道是哪儿的问题。这里直接把下一步给成按钮。 */}
            {files === 0 && (
              <div className="mt-2.5 flex items-start gap-2">
                <span className="flex-1 text-[11.5px] leading-relaxed text-sub">{t('ob_zero_hint')}</span>
                <button
                  className="shrink-0 rounded-lg bg-accent px-3 py-1.5 text-[11.5px] font-medium text-white"
                  onClick={() => void openSettings()}
                >
                  {t('ob_pick_folder')}
                </button>
              </div>
            )}
          </div>
        )}

        {/* 按钮：建好/看过之后给 Listary 式两个大按钮 */}
        <div className="mt-5 flex items-center justify-between gap-3">
          <button className="text-[11.5px] text-sub hover:text-ink" onClick={() => void close()}>
            {t('ob_tour_skip')}
          </button>
          <div className="flex gap-2">
            <button className="rounded-lg border border-line px-4 py-2 text-[12.5px] text-sub hover:text-ink"
              onClick={() => void close()}>
              {t('ob_start_search')}
            </button>
            {tour < 3 ? (
              <button className="rounded-lg bg-accent px-5 py-2 text-[12.5px] font-medium text-white"
                onClick={() => setTour(tour + 1)}>
                {t('ob_tour_next')}
              </button>
            ) : (
              <button className="rounded-lg bg-accent px-5 py-2 text-[12.5px] font-medium text-white"
                onClick={() => void openMini()}>
                {t('ob_tour_start')}
              </button>
            )}
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
