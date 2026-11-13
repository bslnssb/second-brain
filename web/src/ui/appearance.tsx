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
import { clearWall, pickFile, setWallFile, tuneWall, type WallState } from '../api'

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
      className={`rounded-lg border p-1 text-left transition-colors ${
        active ? 'border-accent ring-1 ring-accent' : 'border-line/70 hover:border-sub'
      }`}
    >
      <div
        className="relative h-[34px] w-full overflow-hidden rounded-md"
        style={{ background: rgb(bg) }}
        aria-hidden="true"
      >
        <div
          className="absolute left-1.5 top-1.5 h-[3px] w-[62%] rounded-full"
          style={{ background: rgb(ink), opacity: 0.55 }}
        />
        <div
          className="absolute left-1.5 top-[13px] h-[3px] w-[42%] rounded-full"
          style={{ background: rgb(sub), opacity: 0.45 }}
        />
        <span
          className="absolute bottom-1.5 right-1.5 h-2 w-2 rounded-full"
          style={{ background: rgb(accent) }}
        />
      </div>
      <div className={`mt-1 truncate text-[10px] ${active ? 'text-ink' : 'text-sub'}`}>
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
      <span className="flex items-center justify-between text-[11px] text-sub">
        {label}
        <span className="tabular-nums text-ink/70">{value}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 h-1 w-full cursor-pointer appearance-none rounded bg-line accent-accent"
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
  const timer = useRef(0)

  useEffect(() => {
    setShade(wall.opacity)
    setBlur(wall.blur)
  }, [wall.opacity, wall.blur])

  const tune = (nextShade: number, nextBlur: number) => {
    setShade(nextShade)
    setBlur(nextBlur)
    onWall({ ...wall, opacity: nextShade, blur: nextBlur })
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      tuneWall(nextShade, nextBlur)
        .then((w) => onWall(w))
        .catch(() => {})
    }, 350)
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
      <div className="pt-4 text-[11px] uppercase tracking-wide text-sub">{t('ap_theme')}</div>
      <div className="mt-2 grid grid-cols-3 gap-1.5">
        {PALETTES.map((p) => (
          <Swatch
            key={p.id}
            p={p}
            active={p.id === palette}
            dark={p.id === palette ? d : paletteDark(p.id, false)}
            onClick={() => onPalette(p.id)}
          />
        ))}
      </div>

      <div className="pt-4 text-[11px] uppercase tracking-wide text-sub">{t('ap_wall')}</div>
      <div className="mt-2 rounded-lg border border-line/70 p-2">
        <div className="flex items-center justify-between gap-2">
          <span className="min-w-0 truncate text-[11px] text-sub" title={wall.name || ''}>
            {wall.on ? wall.name : t('ap_wall_none')}
          </span>
          <div className="flex shrink-0 items-center gap-2">
            <button
              onClick={() => void pick()}
              disabled={busy}
              className="text-[11px] text-accent hover:underline disabled:opacity-50"
            >
              {t('ap_wall_pick')}
            </button>
            {wall.on && (
              <button onClick={drop} className="text-[11px] text-sub hover:underline">
                {t('ap_wall_clear')}
              </button>
            )}
          </div>
        </div>

        {wall.on && (
          <>
            <Slider
              label={t('ap_wall_shade')}
              value={shade}
              min={5}
              max={100}
              onChange={(v) => tune(v, blur)}
            />
            <Slider
              label={t('ap_wall_blur')}
              value={blur}
              min={0}
              max={24}
              onChange={(v) => tune(shade, v)}
            />
          </>
        )}

        {err && <div className="pt-1 text-[10px] text-red-500">{err}</div>}
        <div className="pt-1 text-[10px] leading-4 text-sub/80">{t('ap_wall_hint')}</div>
      </div>
    </>
  )
}

/** 主界面底下的壁纸层：固定铺满、不吃鼠标事件、按可见度叠在主题底色上 */
export function Wallpaper({ wall }: { wall: WallState }) {
  if (!wall.on) return null
  const style: React.CSSProperties = {
    opacity: Math.min(100, Math.max(0, wall.opacity)) / 100,
  }
  if (wall.blur > 0) {
    style.filter = `blur(${wall.blur}px)`
    // 模糊会把边缘啃掉一圈，放大一点补回来（不然四边露底色）
    style.transform = 'scale(1.08)'
  }
  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden" aria-hidden="true">
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
