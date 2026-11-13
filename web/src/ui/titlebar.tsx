import { useEffect, useRef, useState } from 'react'
import { t } from '../lib/i18n'
import { IconClose, IconCoffee, IconHealth, IconMini } from './icons'

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
}

function bridge(): Bridge | undefined {
  return (window as unknown as { pywebview?: { api?: Bridge } }).pywebview?.api
}

/** 标题栏里所有键（功能键 + 窗口键）共用同一个格子：满高、定宽 46、无圆角、无间隙。
 *  第 188 项之前功能键是「文字 + 左右内边距」，宽度被文案撑开，跟 − □ ✕ 对不齐；
 *  现在一律这个类，图标居中，整条栏的分格才是等距的。 */
const CAP = 'grid h-full w-[46px] shrink-0 place-items-center whitespace-nowrap text-sub transition-colors'

export function TitleBar({
  onMini,
  onDiag,
  onSupport,
}: {
  onMini: () => void
  onDiag: () => void
  onSupport: () => void
}) {
  const [max, setMax] = useState(false)

  useEffect(() => {
    bridge()
      ?.titlebar_state?.()
      .then((s) => setMax(!!s?.max))
      .catch(() => {})
  }, [])

  const cmd = (c: 'min' | 'max' | 'close') => {
    void bridge()?.titlebar_cmd?.(c)
    if (c === 'max') {
      // 状态要等窗口真的换了再问；给系统一帧时间
      window.setTimeout(() => {
        bridge()
          ?.titlebar_state?.()
          .then((s) => setMax(!!s?.max))
          .catch(() => {})
      }, 120)
    }
  }

  // ── 拖动（第 182 项）────────────────────────────────────────────────
  // 不用 WM_NCLBUTTONDOWN(HTCAPTION)：那条路要求系统在消息里进入 modal 拖动循环，
  // 从 pywebview 的 JS 桥线程 SendMessage 过去实测**完全不动**。改成：
  // pointerdown 记起点 + 窗口原点 → pointermove 按帧 SetWindowPos → 松手吸附。
  const startPt = useRef<{ x0: number; y0: number } | null>(null)
  const originPt = useRef<{ wx: number; wy: number } | null>(null)
  const wantPt = useRef<{ x: number; y: number } | null>(null)
  const rafId = useRef(0)
  const movedRef = useRef(false)

  // 一帧只发一次：pointermove 频率远高于窗口能跟上的速度，不节流会堆一堆 IPC
  const flush = () => {
    rafId.current = 0
    const p = wantPt.current
    if (!p) return
    wantPt.current = null
    void bridge()?.titlebar_move?.(p.x, p.y)
  }

  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    // 按钮区不拖；文本也不拖（用户可以选字）
    if ((e.target as HTMLElement).closest('[data-nodrag]')) return
    const api = bridge()
    if (!api?.titlebar_rect) {
      void api?.titlebar_drag?.()      // 老桥没有新方法：退回系统拖动
      return
    }
    startPt.current = { x0: e.screenX, y0: e.screenY }
    originPt.current = null
    movedRef.current = false
    try {
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    } catch {
      /* 个别环境不支持指针捕获，靠 window 监听也能拖 */
    }
    const mouseX = e.screenX
    void api.titlebar_rect().then(async (r) => {
      if (!r) {
        void api.titlebar_drag?.()
        return
      }
      // 最大化状态下移动无效 —— 必须先还原，再把窗口摆到鼠标下方
      if (r.max && api.titlebar_restore) {
        const rr = await api.titlebar_restore(mouseX)
        if (rr) {
          originPt.current = { wx: rr.x, wy: rr.y }
          return
        }
      }
      originPt.current = { wx: r.x, wy: r.y }
    })
  }

  const onMove = (e: React.PointerEvent) => {
    const s0 = startPt.current
    const o0 = originPt.current
    if (!s0 || !o0) return
    const dx = e.screenX - s0.x0
    const dy = e.screenY - s0.y0
    if (!movedRef.current && Math.abs(dx) + Math.abs(dy) < 3) return
    movedRef.current = true
    wantPt.current = { x: o0.wx + dx, y: o0.wy + dy }
    if (!rafId.current) rafId.current = window.requestAnimationFrame(flush)
  }

  const onUp = (e: React.PointerEvent) => {
    const moved = movedRef.current
    startPt.current = null
    originPt.current = null
    wantPt.current = null
    movedRef.current = false
    if (rafId.current) {
      window.cancelAnimationFrame(rafId.current)
      rafId.current = 0
    }
    try {
      ;(e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId)
    } catch {
      /* ignore */
    }
    // 贴着屏幕边缘松手才吸附（顶上最大化 / 左右半屏）
    if (moved) void bridge()?.titlebar_snap?.()
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
      className="flex h-[36px] shrink-0 select-none items-center border-b border-line bg-bg pl-3"
    >
      <img
        src="/logo.png"
        alt=""
        className="h-4 w-4 rounded"
        onError={(e) => {
          ;(e.currentTarget as HTMLImageElement).style.display = 'none'
        }}
      />
      <span className="ml-1.5 whitespace-nowrap text-[12px] font-semibold leading-none">Second Brain</span>
      <span className="ml-1.5 whitespace-nowrap text-[11px] leading-none text-sub">第二大脑</span>

      <div className="ml-auto flex h-full shrink-0 items-stretch" data-nodrag>
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
