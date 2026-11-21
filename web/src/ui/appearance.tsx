/**
 * 外观：主题配色 + 全局壁纸（第 191 项）。
 *
 * ⚠️ **这是以后切 Pro 的分割点**：用户原话「以后好分割成pro的功能」。
 * 所以这两块的表现层全在这一个文件里，入口只有 `AppearanceSection` 一个组件；
 * 将来加门控 = 在 `App` 里给这个组件加一个 `disabled`/提示，**不用动别处**。
 * 现在按项目红线**一律免费、不写任何门禁**。
 *
 * 壁纸刻意做成「垫在界面底下 + 可见度 + 模糊」而不是铺满整窗：
 * 侧栏/顶栏/列表本身是透明的（只有卡片是实色），所以图片能透出来；
 * 但弹窗、设置抽屉这些 `bg-bg` 的面板保持不透明 —— 文字永远压得住。
 */
import { useEffect, useRef, useState } from 'react'
import { t } from '../lib/i18n'
import { PALETTES, paletteDark, type Palette } from '../lib/palette'
import {
  clearWall,
  pickFile,
  setWallFile,
  tuneWall,
  type WallState,
  type WallTune,
} from '../api'

/** 缩略图用 rgb() 拼：数据里是三元组，Tailwind 的 alpha 语法在这儿用不上 */
const rgb = (v: string) => `rgb(${v.split(/\s+/).join(', ')})`

function Swatch({
  p,
  active,
  dark,
  onClick,
}: {
  p: Palette
  active: boolean
  dark: boolean
  onClick: () => void
}) {
  // 基础三档（跟随系统/浅色/深色）自己没有 vars，缩略图就按当前实际底色画
  const bg = p.vars?.bg || (dark ? '24 27 33' : '244 246 250')
  const ink = p.vars?.ink || (dark ? '232 236 244' : '26 34 51')
  const sub = p.vars?.sub || (dark ? '154 164 182' : '92 104 128')
  const accent = p.vars?.accent || (dark ? '110 141 248' : '37 99 235')
  return (
    <button
      onClick={onClick}
      title={t('pal_' + p.id)}
      aria-pressed={active}
      className={`rounded-lg border p-2 text-left transition-colors ${
        active ? 'border-accent ring-1 ring-accent' : 'border-line/70 hover:border-sub'
      }`}
    >
      <div
        className="relative h-[60px] w-full overflow-hidden rounded-md"
        style={{ background: rgb(bg) }}
        aria-hidden="true"
      >
        <div
          className="absolute left-2.5 top-3 h-[4px] w-[62%] rounded-full"
          style={{ background: rgb(ink), opacity: 0.55 }}
        />
        <div
          className="absolute left-2.5 top-5 h-[4px] w-[42%] rounded-full"
          style={{ background: rgb(sub), opacity: 0.45 }}
        />
        <span
          className="absolute bottom-2.5 right-2.5 h-2.5 w-2.5 rounded-full"
          style={{ background: rgb(accent) }}
        />
      </div>
      <div className={`mt-1.5 truncate text-[12px] ${active ? 'text-ink' : 'text-sub'}`}>
        {t('pal_' + p.id)}
      </div>
    </button>
  )
}

function Slider({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  onChange: (v: number) => void
}) {
  return (
    <label className="mt-2 block">
      <span className="flex items-center justify-between text-[13px] text-sub">
        {label}
        <span className="tabular-nums text-ink/70">{value}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1.5 h-1.5 w-full cursor-pointer appearance-none rounded bg-line accent-accent"
      />
    </label>
  )
}

export function AppearanceSection({
  palette,
  onPalette,
  wall,
  onWall,
}: {
  palette: string
  onPalette: (id: string) => void
  wall: WallState
  onWall: (w: WallState) => void
}) {
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  // 滑杆先本地动、再延迟落盘：拖动时每次 change 都写 settings.json 太浪费
  const [shade, setShade] = useState(wall.opacity)
  const [blur, setBlur] = useState(wall.blur)
  const [zoom, setZoom] = useState(wall.zoom ?? 100)
  // 第 364 项：整块壁纸设置默认收起 —— 一进来就是预览+三条滑杆+三个勾太长，
  // 把「主题配色」挤得只剩一小条。展开态记在 localStorage，重开设置页还保持。
  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem('sb_wall_open') === '1'
    } catch {
      return false
    }
  })
  const toggle = () =>
    setOpen((v) => {
      const next = !v
      try {
        localStorage.setItem('sb_wall_open', next ? '1' : '0')
      } catch {
        /* 隐私模式写入失败就算了，下次还是默认收起 */
      }
      return next
    })
  const timer = useRef(0)

  useEffect(() => {
    setShade(wall.opacity)
    setBlur(wall.blur)
    setZoom(wall.zoom ?? 100)
  }, [wall.opacity, wall.blur, wall.zoom])

  /* 第 362 项：拖焦点、拉滑杆都要连续刷界面，但只在停手后落盘 —— 沿用原来
     两个滑杆的做法（每次 change 都写 settings.json 太浪费）。 */
  const tune = (patch: WallTune) => {
    if (patch.opacity !== undefined) setShade(patch.opacity)
    if (patch.blur !== undefined) setBlur(patch.blur)
    if (patch.zoom !== undefined) setZoom(patch.zoom)
    onWall({ ...wall, ...patch })
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      tuneWall(patch)
        .then((w) => onWall(w))
        .catch(() => {})
    }, 350)
  }

  /* 范围开关是点一下就要生效的，不排队 */
  const tuneNow = (patch: WallTune) => {
    onWall({ ...wall, ...patch })
    tuneWall(patch)
      .then((w) => onWall(w))
      .catch(() => {})
  }

  /* 预览框里拖十字：换算成百分比焦点（与 object-position 同一套语义） */
  const boxRef = useRef<HTMLDivElement | null>(null)
  const [dragging, setDragging] = useState(false)
  const putFocus = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = boxRef.current?.getBoundingClientRect()
    if (!r || r.width < 1 || r.height < 1) return
    const cx = Math.min(100, Math.max(0, Math.round(((e.clientX - r.left) / r.width) * 100)))
    const cy = Math.min(100, Math.max(0, Math.round(((e.clientY - r.top) / r.height) * 100)))
    tune({ pos_x: cx, pos_y: cy })
  }

  const pick = async () => {
    setBusy(true)
    setErr('')
    try {
      const p = await pickFile()
      if (!p) return                            // 用户取消 / 浏览器里没有原生桥
      const w = await setWallFile(p)
      if (w.on) onWall(w)
      else setErr(t(w.err === 'wall_bad_ext' ? 'ap_wall_bad' : 'ap_wall_fail'))
    } catch {
      setErr(t('ap_wall_fail'))
    } finally {
      setBusy(false)
    }
  }

  const drop = () => {
    clearWall()
      .then((w) => onWall(w))
      .catch(() => {})
  }

  const d = paletteDark(palette, false)
  return (
    <>
      <div className="pt-5 text-[13px] uppercase tracking-wide text-sub">{t('ap_theme')}</div>
      <div className="mt-3 grid grid-cols-4 gap-3">
        {/* 第 202 项：重复色折叠。legacy 项不渲染，但 id / 数据 / 旧配置都还在 */}
        {PALETTES.filter((p) => !p.legacy).map((p) => (
          <Swatch
            key={p.id}
            p={p}
            active={p.id === palette}
            dark={p.id === palette ? d : paletteDark(p.id, false)}
            onClick={() => onPalette(p.id)}
          />
        ))}
      </div>

      <div className="pt-5 text-[13px] uppercase tracking-wide text-sub">{t('ap_wall')}</div>
      {/* 第 364 项：可收放 —— 收起时只留一行（文件名 + 选择/清除），
          展开才给预览框、三条滑杆和生效范围；展开态记在 localStorage。 */}
      <div className="mt-2 overflow-hidden rounded-lg border border-line/70">
        <div className="flex items-center gap-2 px-4 py-2.5">
          <button
            onClick={toggle}
            aria-expanded={open}
            aria-label={open ? t('ap_wall_collapse') : t('ap_wall_expand')}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-sub transition-colors hover:text-ink"
          >
            <svg
              viewBox="0 0 16 16"
              width="12"
              height="12"
              aria-hidden="true"
              style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}
            >
              <path
                d="M5 3 L10 8 L5 13"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          <button
            onClick={toggle}
            title={wall.name || ''}
            className="min-w-0 flex-1 truncate text-left text-[13px] text-ink/85 hover:text-ink"
          >
            {wall.on ? wall.name : t('ap_wall_none')}
          </button>
          <div className="flex shrink-0 items-center gap-2">
            <button
              onClick={() => void pick()}
              disabled={busy}
              className="text-[13px] text-accent hover:underline disabled:opacity-50"
            >
              {t('ap_wall_pick')}
            </button>
            {wall.on && (
              <button onClick={drop} className="text-[13px] text-sub hover:underline">
                {t('ap_wall_clear')}
              </button>
            )}
          </div>
        </div>

        {/* 收起态：一行摘要把关键设置说清楚，不用展开也能知道现在什么样 */}
        {!open && err && (
          <div className="border-t border-line/50 px-4 py-2 text-[12px] text-red-500">{err}</div>
        )}
        {!open && wall.on && (
          <div className="border-t border-line/50 px-4 py-2 text-[11.5px] leading-4 text-sub/80">
            {`${t('ap_wall_shade')} ${shade}%`}
            {' · '}
            {[
              wall.scope_main ? t('ap_wall_scope_main') : '',
              wall.scope_settings ? t('ap_wall_scope_settings') : '',
              wall.scope_mini ? t('ap_wall_scope_mini') : '',
            ]
              .filter(Boolean)
              .join(' / ') || t('ap_wall_scope')}
          </div>
        )}

        {open && (
          <div className="border-t border-line/50 px-4 pb-4 pt-3.5">
            {wall.on ? (
              <>
                {/* 第 362 项：人像被裁到脸 —— 直接拖预览框里的焦点，别让用户猜百分比 */}
                <label className="block">
                  <span className="flex items-center justify-between text-[13px] text-sub">
                    {t('ap_wall_focus')}
                    <span className="tabular-nums text-ink/70">
                      {wall.pos_x ?? 50}% · {wall.pos_y ?? 50}%
                    </span>
                  </span>
                  <div
                    ref={boxRef}
                    onPointerDown={(e) => {
                      setDragging(true)
                      e.currentTarget.setPointerCapture(e.pointerId)
                      putFocus(e)
                    }}
                    onPointerMove={(e) => {
                      if (dragging) putFocus(e)
                    }}
                    onPointerUp={() => setDragging(false)}
                    onPointerCancel={() => setDragging(false)}
                    className="relative mt-1.5 h-[96px] w-full cursor-crosshair select-none overflow-hidden rounded-md border border-line/70 bg-bg"
                    style={{ touchAction: 'none' }}
                  >
                    <img
                      src={`/api/wall/image?v=${wall.v || 0}`}
                      alt=""
                      draggable={false}
                      className="pointer-events-none absolute inset-0 h-full w-full select-none object-cover"
                      style={{
                        objectPosition: `${wall.pos_x ?? 50}% ${wall.pos_y ?? 50}%`,
                        transform: `scale(${(wall.zoom ?? 100) / 100})`,
                        transformOrigin: `${wall.pos_x ?? 50}% ${wall.pos_y ?? 50}%`,
                      }}
                    />
                    <div
                      className="pointer-events-none absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow"
                      style={{ left: `${wall.pos_x ?? 50}%`, top: `${wall.pos_y ?? 50}%` }}
                    />
                  </div>
                  <div className="pt-1 text-[11.5px] leading-4 text-sub/80">
                    {t('ap_wall_focus_hint')}
                  </div>
                </label>
                <Slider
                  label={t('ap_wall_zoom')}
                  value={zoom}
                  min={100}
                  max={200}
                  onChange={(v) => tune({ zoom: v })}
                />
                <Slider
                  label={t('ap_wall_shade')}
                  value={shade}
                  min={5}
                  max={100}
                  onChange={(v) => tune({ opacity: v })}
                />
                <Slider
                  label={t('ap_wall_blur')}
                  value={blur}
                  min={0}
                  max={24}
                  onChange={(v) => tune({ blur: v })}
                />
                {/* 第 361→362 项：生效范围 —— 有人不想在设置页/快搜窗看见壁纸 */}
                <div className="mt-4 border-t border-line/50 pt-3 text-[13px] text-sub">
                  {t('ap_wall_scope')}
                </div>
                {(
                  [
                    ['scope_main', t('ap_wall_scope_main')],
                    ['scope_settings', t('ap_wall_scope_settings')],
                    ['scope_mini', t('ap_wall_scope_mini')],
                  ] as ['scope_main' | 'scope_settings' | 'scope_mini', string][]
                ).map(([k, label]) => (
                  <label
                    key={k}
                    className="mt-1.5 flex cursor-pointer items-center gap-2 text-[13px] text-ink/80"
                  >
                    <input
                      type="checkbox"
                      checked={Boolean(wall[k])}
                      onChange={(e) => {
                        const v = e.target.checked
                        if (k === 'scope_main') tuneNow({ scope_main: v })
                        else if (k === 'scope_settings') tuneNow({ scope_settings: v })
                        else tuneNow({ scope_mini: v })
                      }}
                      className="h-3.5 w-3.5 accent-accent"
                    />
                    {label}
                  </label>
                ))}
                <div className="pt-1 text-[11.5px] leading-4 text-sub/80">
                  {t('ap_wall_scope_hint')}
                </div>
              </>
            ) : (
              <div className="pt-0.5 text-[12px] leading-5 text-sub/80">{t('ap_wall_empty_hint')}</div>
            )}

            {err && <div className="pt-2 text-[12px] text-red-500">{err}</div>}
          </div>
        )}

        {!open && err && <div className="px-4 pb-2 text-[12px] text-red-500">{err}</div>}
        {!open && (
          <div className="border-t border-line/50 px-4 py-2 text-[11.5px] leading-4 text-sub/80">
            {t('ap_wall_hint')}
          </div>
        )}
      </div>
    </>
  )
}

/** 主界面底下的壁纸层：固定铺满、不吃鼠标事件、按可见度叠在主题底色上。
 *  第 365 项：absolute=true 时改成铺满最近的定位祖先——设置抽屉要自己铺一层，
 *  不然抽屉半透明会把后面的主界面列表透出来（字叠字，比模糊更难看）。 */
export function Wallpaper({ wall, absolute = false }: { wall: WallState; absolute?: boolean }) {
  if (!wall.on) return null
  const zx = Math.min(100, Math.max(0, wall.pos_x ?? 50))
  const zy = Math.min(100, Math.max(0, wall.pos_y ?? 50))
  const zoom = Math.max(1, (wall.zoom ?? 100) / 100)
  const style: React.CSSProperties = {
    opacity: Math.min(100, Math.max(0, wall.opacity)) / 100,
    // 第 362 项：焦点可拖 —— 人像往上摆，脑袋就不会被 cover 裁掉
    objectPosition: `${zx}% ${zy}%`,
  }
  if (zoom > 1 || wall.blur > 0) {
    // 模糊会把边缘啃掉一圈，放大一点补回来（不然四边露底色）
    const s = (zoom > 1 ? zoom : 1) * (wall.blur > 0 ? 1.08 : 1)
    style.transform = `scale(${s})`
    style.transformOrigin = `${zx}% ${zy}%`
  }
  if (wall.blur > 0) style.filter = `blur(${wall.blur}px)`
  return (
    <div
      className={`pointer-events-none ${absolute ? 'absolute' : 'fixed'} inset-0 z-0 overflow-hidden`}
      aria-hidden="true"
    >
      <img
        src={`/api/wall/image?v=${wall.v || 0}`}
        alt=""
        className="h-full w-full object-cover"
        style={style}
        onError={() => {
          /* 文件被删/格式坏了：这一层就是空白，界面照常用 */
        }}
      />
    </div>
  )
}
