import { useEffect, useRef, useState } from 'react'
import { checkUpdate } from '../api'
import { t } from '../lib/i18n'
import { IconClose, IconCoffee, IconHealth, IconMini, IconSnip, IconClipboard, IconTranslate } from './icons'

/**
 * 自绘标题栏（第 180 项）。
 *
 * 为什么要有这个文件：Windows 的 − □ ✕ 在**非客户区**，应用往里插不了按钮。
 * WorkBuddy / VS Code 那类 Electron 应用能用 `titleBarStyle: hidden` 一行搞定，
 * pywebview + WinForms 没有这个开关，只能在**启动时把系统标题栏摘掉**
 * （`app_window.caption_off()` 只去掉 WS_CAPTION，保留缩放边框 / 最大化 / 系统菜单），
 * 然后由这个组件把整条标题栏画出来。
 *
 * 三条实现纪律：
 * 1. **拖动交给系统**：mousedown 调 `titlebar_drag()` → Python 发
 *    WM_NCLBUTTONDOWN(HTCAPTION)。这样 Aero Snap（拖到屏幕边缘半屏/最大化）、
 *    右键系统菜单、双击最大化全都不用自己实现。
 * 2. **按钮区要 `data-nodrag`**：否则点按钮会被当成拖标题栏，点击事件丢掉。
 * 3. **只有 `/api/ping.titlebar` 为真才渲染**：那个值来自窗口实际样式，
 *    跟 Python 摘标题栏用的是同一个来源 —— 两边不一致会出现「没标题栏也没按钮」的死窗口。
 *
 * 尺寸（第 181 项 → 第 188 项定型）：**所有键共用 46px 一格**。最初功能键 28x28、
 * 窗口键 46x36，挤在一起明显不齐；后来改成「文字 + 左右内边距」，宽度又被文案撑开。
 * 现在功能键和 − □ ✕ 一样是 46 宽的格子、图标居中、彼此无间隙，
 * 只在功能键组与窗口键组之间留一条 1px 分隔线（两侧各 6px）—— 和原生标题栏一致。
 *
 * 按钮取舍：
 * - 「环境体检 + 错误日志」**合成一颗**（第 181 项），点了直接开弹窗，不跳设置；
 * - 「设置」**不放在这里**（用户没让放），在顶栏右侧；
 * - 「赞助」第 185 项加进来，第 188 项从「红心 + 文字」改成**咖啡杯纯图标**，
 *   颜色也收回主题色 —— 用户原话「红色感觉不咋搭，主题色是蓝白黑这样的」。
 *
 * 纯图标之后 `title` / `aria-label` 就是唯一出口：`diag_title`、`sp_title` 必须给，
 * 否则这颗键对读屏和没鼠标的人是哑的（preflight 有断言，别删）。
 */
type Bridge = {
  /** 系统拖动（WM_NCLBUTTONDOWN 那条路）；实测在 pywebview 里不动，只作兜底 */
  titlebar_drag?: () => Promise<boolean>
  titlebar_cmd?: (cmd: 'min' | 'max' | 'close') => Promise<boolean>
  titlebar_state?: () => Promise<{ max: boolean }>
  /** 第 182 项：窗口当前屏幕坐标 / 移动到 / 松手吸附 */
  titlebar_rect?: () => Promise<{ x: number; y: number; max?: boolean } | null>
  titlebar_move?: (x: number, y: number) => Promise<boolean>
  /** 最大化时先还原（最大化窗口的 SetWindowPos 会被系统忽略） */
  titlebar_restore?: (mouseX?: number) => Promise<{ x: number; y: number } | null>
  titlebar_snap?: () => Promise<boolean>
  /** 第 216 项拖动诊断：onMove 首次触发时报 OS 光标位置（排查 screenX/rAF 冻结） */
  titlebar_dbg?: () => Promise<boolean>
  /** 第 217 项：拖动开始（Python 记住光标↔窗口关系并处理最大化还原）/ 拖动中 */
  titlebar_drag_start?: () => Promise<{ cx: number; cy: number; wx: number; wy: number } | null>
  titlebar_drag_move?: () => Promise<boolean>
  /** 第 375 项：标题栏最上面 6px 是缩放热区。它落在客户区里 → 按下去被 WebView2 接走，
      系统缩放循环轮不到。前端自己起手/拖动/收手，Python 侧按光标真值改 top/height。 */
  titlebar_resize_start?: (edge?: 'top' | 'bottom') => Promise<unknown>
  titlebar_resize_move?: () => Promise<boolean>
  titlebar_resize_end?: () => Promise<boolean>
}

function bridge(): Bridge | undefined {
  return (window as unknown as { pywebview?: { api?: Bridge } }).pywebview?.api
}

/**
 * 第 216/217/218 项：标题栏拖动逻辑抽成 hook —— 设置页铺满全窗（第 209 项）之后，
 * 抽屉的头部条看起来也像标题栏，用户会去抓它；两处共用同一套 pointer 逻辑。
 * 返回的三个 handler 铺到可拖动的条上；条里的按钮/输入框标 `data-nodrag`。
 *
 * 第 218 项重大简化：**JS 不再传任何坐标**。WebView2 无边框窗口里 pointer
 * 事件的 screenX/screenY 在拖动期间会冻结（216 项 rect 到、move 帧零的实锤），
 * 改成 JS 只报「开始 / 拖动中 / 松手」三个事件，坐标全部由 Python 侧
 * GetCursorPos（OS 真值）读取 —— 窗口跟着真光标走。
 *
 * ⚠️ `dragging` 必须**同步乐观置位**（218 项实测教训）：若等 drag_start 的
 * Promise 回调再置位，回调慢一点，整个拖动的 move 事件就全被
 * `if (!dragging) return` 吞掉（56 构建实测：drag_start 到了 Python、
 * move 帧一个没来）。Python 侧失败才在回调里收回。
 */
export function useTitlebarDrag() {
  const dragging = useRef(false)
  const movedRef = useRef(false)
  const timerId = useRef(0)
  const dbgDone = useRef(false)

  // 16ms 一调：拖动中的频率上限，不节流会堆一堆 IPC
  const flush = () => {
    timerId.current = 0
    if (!dragging.current) return
    void bridge()?.titlebar_drag_move?.()
  }

  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    // 按钮区不拖；文本也不拖（用户可以选字）
    if ((e.target as HTMLElement).closest('[data-nodrag]')) return
    const api = bridge()
    if (api?.titlebar_drag_start) {
      try {
        ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
      } catch {
        /* 个别环境不支持指针捕获，靠 window 监听也能拖 */
      }
      dbgDone.current = false
      movedRef.current = false
      dragging.current = true
      void api.titlebar_drag_start().then((r) => {
        if (!r) dragging.current = false   // Python 侧明确失败才收手
      })
      return
    }
    void api?.titlebar_drag?.()      // 老桥没有新方法：退回系统拖动
  }

  const onMove = () => {
    if (!dragging.current) return
    if (!movedRef.current) {
      movedRef.current = true
      // 第 216 项诊断（每拖一次报一次）：OS 光标是否真的在动
      if (!dbgDone.current) {
        dbgDone.current = true
        void bridge()?.titlebar_dbg?.()
      }
    }
    if (!timerId.current) timerId.current = window.setTimeout(flush, 16)
  }

  const onUp = (e: React.PointerEvent) => {
    const moved = movedRef.current
    dragging.current = false
    movedRef.current = false
    if (timerId.current) {
      window.clearTimeout(timerId.current)
      timerId.current = 0
    }
    try {
      ;(e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId)
    } catch {
      /* ignore */
    }
    // 拖动过且贴着屏幕边缘松手才吸附（顶上最大化 / 左右半屏）；纯点击不吸附
    if (moved) void bridge()?.titlebar_snap?.()
  }

  return { onDown, onMove, onUp }
}

/** 标题栏里所有键（功能键 + 窗口键）共用同一个格子：满高、定宽 46、无圆角、无间隙。
 *  第 188 项之前功能键是「文字 + 左右内边距」，宽度被文案撑开，跟 − □ ✕ 对不齐；
 *  现在一律这个类，图标居中，整条栏的分格才是等距的。 */
const CAP = 'grid h-full w-[46px] shrink-0 place-items-center whitespace-nowrap text-sub transition-colors'

export function TitleBar({
  onMini,
  onDiag,
  onSupport,
  onSnip,
  onSnipTranslate,
  onClipboard,
  onTranslate,
  onOpenAbout,
  ver,
}: {
  onMini: () => void
  onDiag: () => void
  onSupport: () => void
  onSnip?: () => void
  onSnipTranslate?: () => void
  onClipboard?: () => void
  onTranslate?: () => void
  onOpenAbout?: () => void
  ver?: string
}) {
  const [max, setMax] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [snipMenu, setSnipMenu] = useState(false)
  const snipHoldRef = useRef<number | null>(null)
  const snipHeldRef = useRef(false)
  const snipMenuRef = useRef<HTMLDivElement>(null)
  const [upState, setUpState] = useState<'idle' | 'busy' | 'done' | 'err'>('idle')
  const [upText, setUpText] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const resizing = useRef(false)
  const rzTimer = useRef(0)
  const topDownAt = useRef(0)
  const resetTimer = useRef<number | null>(null)

  const cleanVer = (ver || '0.9.55').replace(/^v/, '')

  useEffect(() => {
    if (!menuOpen) return
    const onDocDown = (e: MouseEvent | PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('pointerdown', onDocDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onDocDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [menuOpen])

  /* 相机键的「截图翻译」小菜单：右键 / 长按 300ms 才弹，普通点击行为一点不变 */
  useEffect(() => {
    if (!snipMenu) return
    const onDocDown = (e: MouseEvent | PointerEvent) => {
      if (snipMenuRef.current && !snipMenuRef.current.contains(e.target as Node)) {
        setSnipMenu(false)
      }
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSnipMenu(false)
    }
    document.addEventListener('pointerdown', onDocDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onDocDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [snipMenu])

  const snipMenuOpen = () => {
    if (snipHoldRef.current) {
      window.clearTimeout(snipHoldRef.current)
      snipHoldRef.current = null
    }
    setSnipMenu(true)
  }

  const onSnipPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    snipHeldRef.current = false
    if (snipHoldRef.current) window.clearTimeout(snipHoldRef.current)
    snipHoldRef.current = window.setTimeout(() => {
      snipHeldRef.current = true
      setSnipMenu(true)
    }, 300) as unknown as number
  }

  const onSnipPointerUp = () => {
    if (snipHoldRef.current) {
      window.clearTimeout(snipHoldRef.current)
      snipHoldRef.current = null
    }
  }

  const onSnipClick = () => {
    if (snipHeldRef.current) {
      // 长按已经弹出菜单了，松开这一下不要再当成普通截图
      snipHeldRef.current = false
      return
    }
    // 第 369 项修正：Ctrl+点击这个入口已砍掉（修饰键在快搜窗里读不稳，
    // 用户按了半天不出译文）。翻译只走两处看得见的路：本键右键/长按菜单、
    // 快搜窗按钮排悬停出来的胶囊。
    onSnip?.()
  }

  useEffect(() => {
    return () => {
      if (resetTimer.current) window.clearTimeout(resetTimer.current)
    }
  }, [])

  const handleCheckUpdate = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (upState === 'busy') return
    if (resetTimer.current) {
      window.clearTimeout(resetTimer.current)
      resetTimer.current = null
    }
    setUpState('busy')
    setUpText(t('about_checking'))
    try {
      const r = await checkUpdate()
      if (!r.ok) {
        setUpState('err')
        setUpText('检查失败')
        resetTimer.current = window.setTimeout(() => {
          setUpState('idle')
          setUpText(null)
        }, 3000)
      } else if (r.newer) {
        setUpState('done')
        setUpText(t('up_new').replace('{v}', String(r.latest || '')))
        resetTimer.current = window.setTimeout(() => {
          onOpenAbout?.()
          setMenuOpen(false)
        }, 1200)
      } else {
        setUpState('done')
        setUpText(t('up_uptodate').replace('{v}', String(r.latest || cleanVer)))
        resetTimer.current = window.setTimeout(() => {
          setUpState('idle')
          setUpText(null)
        }, 3000)
      }
    } catch {
      setUpState('err')
      setUpText('检查失败')
      resetTimer.current = window.setTimeout(() => {
        setUpState('idle')
        setUpText(null)
      }, 3000)
    }
  }

  // 第 219 项：拖动会触发「自动还原 / 贴边最大化」（217/218 项），□ 图标必须
  // 跟着窗口真值走 —— 只在启动和点 □ 时问一次不够了，拖动前后都要刷新。
  const refreshMax = () => {
    bridge()
      ?.titlebar_state?.()
      .then((s) => setMax(!!s?.max))
      .catch(() => {})
  }

  useEffect(() => {
    refreshMax()
  }, [])

  const cmd = (c: 'min' | 'max' | 'close') => {
    void bridge()?.titlebar_cmd?.(c)
    if (c === 'max') {
      // 状态要等窗口真的换了再问；给系统一帧时间
      window.setTimeout(refreshMax, 120)
    }
  }

  const { onDown: dragDown, onMove, onUp: dragUp } = useTitlebarDrag()
  const onDown = (e: React.PointerEvent) => {
    dragDown(e)
    refreshMax()   // drag_start 可能把最大化窗口自动还原了
  }
  const onUp = (e: React.PointerEvent) => {
    dragUp(e)
    window.setTimeout(refreshMax, 150)   // 贴边吸附 / 双击最大化后再问一次
  }

  return (
    <header
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
      onDoubleClick={(e) => {
        if ((e.target as HTMLElement).closest('[data-nodrag]')) return
        cmd('max')
      }}
      className="relative flex h-[36px] shrink-0 select-none items-center border-b border-line bg-bg pl-3"
    >
      {/* 第 375 项：窗口顶边那 6px 是缩放热区。它落在客户区里 → 按下去先被 WebView2
          接走，系统缩放循环轮不到（用户描述：标题栏最上面拖不动、拉不了高度）。
          这里铺一条 6px 的透明条，光标 ns-resize，按下起手、拖动按帧报、松手收手。
          最大化时不做：那会儿顶边不是缩放区，双击应该还原窗口。 */}
      {!max && (
        <div
          aria-hidden="true"
          className="absolute inset-x-0 top-0 z-10"
          style={{ height: 6, cursor: 'ns-resize' }}
          onPointerDown={(e) => {
            if (e.button !== 0) return
            e.preventDefault()
            e.stopPropagation()
            // 系统在标题栏顶边双击是「最大化 / 还原」，热区把这一下接走之后
            // dblclick 就传不到 header 了 —— 自己认 350ms 内的第二下。
            const now = Date.now()
            if (now - topDownAt.current < 350) {
              topDownAt.current = 0
              void bridge()?.titlebar_cmd?.('max')
              window.setTimeout(refreshMax, 150)
              return
            }
            topDownAt.current = now
            try {
              ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
            } catch {
              /* 不支持指针捕获就靠 window 监听 */
            }
            resizing.current = true
            void bridge()?.titlebar_resize_start?.('top')
          }}
          onPointerMove={() => {
            if (!resizing.current || rzTimer.current) return
            rzTimer.current = window.setTimeout(() => {
              rzTimer.current = 0
              if (resizing.current) void bridge()?.titlebar_resize_move?.()
            }, 16)
          }}
          onPointerUp={() => {
            if (!resizing.current) return
            resizing.current = false
            if (rzTimer.current) {
              window.clearTimeout(rzTimer.current)
              rzTimer.current = 0
            }
            void bridge()?.titlebar_resize_end?.()
          }}
          onPointerCancel={() => {
            if (!resizing.current) return
            resizing.current = false
            if (rzTimer.current) {
              window.clearTimeout(rzTimer.current)
              rzTimer.current = 0
            }
            void bridge()?.titlebar_resize_end?.()
          }}
        />
      )}
      <div className="relative flex items-center" data-nodrag ref={menuRef}>
        <button
          type="button"
          data-act="brand"
          onClick={() => setMenuOpen((v) => !v)}
          className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-ink transition-colors cursor-pointer select-none ${
            menuOpen ? 'bg-line/80' : 'bg-line/40 hover:bg-line/70'
          }`}
          title={t('about')}
          aria-expanded={menuOpen}
          aria-haspopup="true"
        >
          <img
            src="/logo.png"
            alt=""
            className="h-4 w-4 rounded"
            onError={(e) => {
              ;(e.currentTarget as HTMLImageElement).style.display = 'none'
            }}
          />
          <span className="whitespace-nowrap text-[12px] font-semibold leading-none">Memex</span>
        </button>

        {menuOpen && (
          <div
            className="absolute left-0 top-full mt-1.5 z-50 min-w-[150px] rounded-lg border border-line bg-card p-1 shadow-lg text-ink"
            data-nodrag
          >
            <div
              className="px-2.5 py-1.5 text-[13px] text-sub select-none cursor-pointer hover:bg-line/30 rounded transition-colors"
              onClick={() => {
                onOpenAbout?.()
                setMenuOpen(false)
              }}
              title={t('about')}
            >
              Version {cleanVer}
            </div>
            <button
              type="button"
              onClick={handleCheckUpdate}
              disabled={upState === 'busy'}
              className="w-full text-left px-2.5 py-1.5 text-[13px] rounded transition-colors hover:bg-line/40 cursor-pointer flex items-center justify-between text-ink disabled:opacity-60"
            >
              <span>{upText || t('about_check')}</span>
              {upState === 'busy' && (
                <span className="ml-2 inline-block h-3 w-3 animate-spin rounded-full border-2 border-sub border-t-accent" />
              )}
            </button>
          </div>
        )}
      </div>

      <span className="ml-2 hidden whitespace-nowrap text-[11px] leading-none text-sub sm:inline">{t('titlebar_sub')}</span>

      <div className="ml-auto flex h-full shrink-0 items-stretch" data-nodrag>
        {/* 屏幕截图功能键（松手即复制到剪贴板，轻量极速） */}
        <div className="relative flex h-full">
          <button
            className={`${CAP} hover:bg-card hover:text-ink`}
            onClick={onSnipClick}
            onPointerDown={onSnipPointerDown}
            onPointerUp={onSnipPointerUp}
            onPointerLeave={onSnipPointerUp}
            onContextMenu={(e) => {
              e.preventDefault()
              snipMenuOpen()
            }}
            title={`${t('snip_btn')} · ${t('snip_trans_btn')}（右键 / 长按）`}
            aria-label={`${t('snip_btn')} / ${t('snip_trans_btn')}`}
            data-act="snip"
          >
            <IconSnip size={15} />
          </button>
          {/* 第 369 项：右键 / 长按 300ms 出的小菜单。普通点击仍是「松手进剪贴板」，一个字没改。 */}
          {snipMenu && (
            <div
              ref={snipMenuRef}
              className="absolute right-0 top-full z-50 mt-1 w-[186px] rounded-lg border border-line bg-card py-1 text-[13px] shadow-xl"
              data-nodrag
            >
              <button
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-ink hover:bg-line/40"
                onClick={() => {
                  setSnipMenu(false)
                  onSnip?.()
                }}
              >
                <IconSnip size={14} />
                {t('snip_menu_pure')}
              </button>
              <button
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-ink hover:bg-line/40"
                onClick={() => {
                  setSnipMenu(false)
                  onSnipTranslate?.()
                }}
              >
                <IconTranslate size={14} />
                {t('snip_menu_trans')}
              </button>
              <div className="px-3 pb-1 pt-1 text-[11px] leading-snug text-sub">{t('snip_menu_hint')}</div>
            </div>
          )}
        </div>

        {/* 剪贴板历史 */}
        <button
          className={`${CAP} hover:bg-card hover:text-ink`}
          onClick={onClipboard}
          title={t('clip_title')}
          aria-label={t('clip_title')}
          data-act="clipboard"
        >
          <IconClipboard size={15} />
        </button>

        {/* 聚合翻译 */}
        <button
          className={`${CAP} hover:bg-card hover:text-ink`}
          onClick={onTranslate}
          title={t('trans_title')}
          aria-label={t('trans_title')}
          data-act="translate"
        >
          <IconTranslate size={15} />
        </button>
        {/* 三个功能键与 − □ ✕ **完全同格**：都在 46px 宽的格子里、图标居中、彼此无间隙。
            只要有一个是「文字按钮」，宽度就会被文案撑开，整条标题栏的分格立刻不齐
            （第 181 项为这个来回改过三次，第 188 项用户拍板：全部纯图标）。 */}
        <button className={`${CAP} hover:bg-card hover:text-ink`} onClick={onMini} title={t('hdr_mini')} aria-label={t('hdr_mini')}>
          <IconMini size={15} />
        </button>
        {/* 体检 + 报错合成一颗：图标是监测屏 + 心跳线，点了开独立弹窗。
            title/aria-label 走 diag_title（「体检与报错」），纯图标也不能变成哑巴键。 */}
        <button
          className={`${CAP} hover:bg-card hover:text-ink`}
          onClick={onDiag}
          title={t('diag_title')}
          aria-label={t('diag_title')}
          data-act="diag"
        >
          <IconHealth size={15} />
        </button>
        {/* 赞助：咖啡杯（打赏），颜色跟其它功能键一样走 text-sub，不再用红色 */}
        <button
          className={`${CAP} hover:bg-card hover:text-ink`}
          onClick={onSupport}
          title={t('sp_title')}
          aria-label={t('sp_title')}
          data-act="support"
        >
          <IconCoffee size={15} />
        </button>

        <span className="mx-1.5 h-4 w-px self-center bg-line" />

        <button className={`${CAP} hover:bg-card hover:text-ink`} onClick={() => cmd('min')} title={t('win_min')} aria-label={t('win_min')}>
          <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
            <path d="M2 5h6" stroke="currentColor" strokeWidth="1.1" />
          </svg>
        </button>
        <button
          className={`${CAP} hover:bg-card hover:text-ink`}
          onClick={() => cmd('max')}
          title={max ? t('win_restore') : t('win_max')}
          aria-label={max ? t('win_restore') : t('win_max')}
        >
          {max ? (
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.1">
              <rect x="2.4" y="3.4" width="4.6" height="4.6" />
              <path d="M3.4 3.4V2.4h4.2v4.2H6.9" />
            </svg>
          ) : (
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.1">
              <rect x="2.2" y="2.2" width="5.6" height="5.6" />
            </svg>
          )}
        </button>
        <button className={`${CAP} hover:bg-red-500 hover:text-white`} onClick={() => cmd('close')} title={t('win_close')} aria-label={t('win_close')}>
          <IconClose size={13} />
        </button>
      </div>
    </header>
  )
}
