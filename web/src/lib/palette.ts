/**
 * 主题配色（第 191 项）。
 *
 * 为什么要单独一个模块，而不是在 App 里塞几个 if：
 * 「主题配色」和「全局壁纸」将来是要**整体切成 Pro** 的两块（用户原话：
 * 「以后好分割成pro的功能」）。所以这两块的表现层收在
 * `lib/palette.ts` + `ui/appearance.tsx` 两个文件里，
 * 以后加门控只需要在这两个文件的入口加一个 `if`，不用去翻 App。
 * **现在一律免费、不写任何门禁**（本项目的红线：不提前锁功能）。
 *
 * 数据结构：每套配色只声明 7 个 RGB 三元组，直接覆盖 `--sb-*`。
 * 用三元组而不是 hex，是因为 Tailwind 那套 `bg-card/60`、`text-ink/70`
 * 都靠 `rgb(var(--sb-xxx) / <alpha>)` 拼透明度 —— 换成 hex 这些写法全失效。
 *
 * 三档基础色（跟随系统 / 浅色 / 深色）故意**不带 vars**：它们就用 index.css 里的
 * 默认值，这样「跟随系统」才能真的跟着系统走，不会被我在这里写死。
 */

export type PaletteVars = {
  bg: string
  card: string
  ink: string
  sub: string
  line: string
  accent: string
  side: string
}

export type Palette = {
  id: string
  /** 底色族：决定 data-theme（滚动条、阴影、原生窗口外框都按它走） */
  dark: boolean
  /** 分组只影响设置里的排列顺序 */
  group: 'basic' | 'paper' | 'soft' | 'deep'
  /** 第 202 项：和别的配色肉眼几乎同色，设置里不再展示；**id 仍保留**，
      避免删 id 牵动前后端列表 / i18n / 用户已保存的 sb_palette。 */
  legacy?: boolean
  /** 不填 = 用 index.css 的默认值 */
  vars?: PaletteVars
}

/** 覆盖顺序固定：清空时按这个表清，漏一个就会残留上一套配色的颜色 */
export const VAR_KEYS: (keyof PaletteVars)[] = ['bg', 'card', 'ink', 'sub', 'line', 'accent', 'side']

/** 浅色/深色默认底（与 index.css 的 --sb-bg 一致，preflight 会逐值比对） */
export const BASE_BG = { light: '244 246 250', dark: '24 27 33' }

export const PALETTES: Palette[] = [
  // ── 系统与基础：不加 vars，跟随 index.css / data-theme ──
  { id: 'auto', dark: false, group: 'basic' },
  { id: 'light', dark: false, group: 'basic', legacy: true },
  { id: 'dark', dark: true, group: 'basic' },

  // ── 纸墨质感：暖色底，长时间看文档比纯白舒服 ──
  {
    id: 'paper',
    dark: false,
    group: 'paper',
    vars: {
      bg: '246 240 229',
      card: '255 252 245',
      ink: '58 48 36',
      sub: '118 103 82',
      line: '232 221 203',
      accent: '176 118 60',
      side: '138 122 100',
    },
  },
  {
    id: 'amber',
    legacy: true,
    dark: false,
    group: 'paper',
    vars: {
      bg: '248 237 220',
      card: '255 250 240',
      ink: '70 50 30',
      sub: '132 102 70',
      line: '238 220 196',
      accent: '190 110 40',
      side: '150 120 90',
    },
  },
  {
    id: 'pure',
    legacy: true,
    dark: false,
    group: 'paper',
    vars: {
      bg: '250 250 251',
      card: '255 255 255',
      ink: '20 22 26',
      sub: '105 112 124',
      line: '232 234 238',
      accent: '30 90 220',
      side: '110 116 128',
    },
  },

  // ── 雅致色调：低饱和，适合一直开着的窗口 ──
  {
    id: 'mint',
    dark: false,
    group: 'soft',
    vars: {
      bg: '240 248 243',
      card: '250 255 252',
      ink: '28 52 44',
      sub: '92 118 106',
      line: '214 234 222',
      accent: '22 140 96',
      side: '110 134 122',
    },
  },
  {
    id: 'blue',
    legacy: true,
    dark: false,
    group: 'soft',
    vars: {
      bg: '240 245 252',
      card: '250 253 255',
      ink: '26 40 64',
      sub: '92 108 134',
      line: '214 226 242',
      accent: '44 104 200',
      side: '108 124 150',
    },
  },
  {
    id: 'lilac',
    legacy: true,
    dark: false,
    group: 'soft',
    vars: {
      bg: '245 242 252',
      card: '252 250 255',
      ink: '44 34 66',
      sub: '108 96 132',
      line: '228 220 244',
      accent: '116 84 210',
      side: '126 112 150',
    },
  },
  {
    id: 'sakura',
    dark: false,
    group: 'soft',
    vars: {
      bg: '253 243 246',
      card: '255 250 252',
      ink: '62 36 48',
      sub: '130 96 108',
      line: '244 219 228',
      accent: '214 90 130',
      side: '148 112 124',
    },
  },

  // ── 深邃暗色：夜里用，对比度别调太高（纯黑白反而刺眼） ──
  {
    id: 'graphite',
    legacy: true,
    dark: true,
    group: 'deep',
    vars: {
      bg: '30 31 34',
      card: '40 41 45',
      ink: '226 228 232',
      sub: '150 154 162',
      line: '58 60 66',
      accent: '150 160 180',
      side: '150 154 162',
    },
  },
  {
    id: 'night',
    legacy: true,
    dark: true,
    group: 'deep',
    vars: {
      bg: '18 24 38',
      card: '26 34 52',
      ink: '226 234 246',
      sub: '146 162 190',
      line: '44 56 82',
      accent: '84 140 246',
      side: '146 162 190',
    },
  },
  {
    id: 'contrast',
    dark: true,
    group: 'deep',
    vars: {
      bg: '0 0 0',
      card: '14 14 14',
      ink: '255 255 255',
      sub: '200 200 200',
      line: '70 70 70',
      accent: '255 214 10',
      side: '200 200 200',
    },
  },
  {
    id: 'black',
    legacy: true,
    dark: true,
    group: 'deep',
    vars: {
      bg: '8 8 9',
      card: '18 18 20',
      ink: '236 236 238',
      sub: '150 150 155',
      line: '44 44 48',
      accent: '120 130 250',
      side: '150 150 155',
    },
  },
  {
    id: 'cyber',
    dark: true,
    group: 'deep',
    vars: {
      /* 第 202 项：原来和 dark(24 27 33) 都是近黑，改成明显偏青的深蓝绿 */
      bg: '4 40 56',
      card: '10 52 70',
      ink: '220 246 250',
      sub: '120 170 180',
      line: '32 58 66',
      accent: '34 200 220',
      side: '120 170 180',
    },
  },
]

export const PALETTE_IDS = PALETTES.map((p) => p.id)
export const DEFAULT_PALETTE = 'auto'
const LS_KEY = 'sb_palette'

export function getPalette(id: string): Palette {
  return PALETTES.find((p) => p.id === id) || PALETTES[0]
}

/** 这套配色在「系统当前是深/浅」下实际呈现的深色与否 */
export function paletteDark(id: string, systemDark: boolean): boolean {
  const p = getPalette(id)
  if (p.id === 'auto') return systemDark
  return p.dark
}

/** 底色（RGB 三元组）：壁纸的遮罩、原生窗口外框都要用它 */
export function paletteBg(id: string, systemDark: boolean): string {
  const p = getPalette(id)
  if (p.vars) return p.vars.bg
  return paletteDark(id, systemDark) ? BASE_BG.dark : BASE_BG.light
}

/** '#F4F6FA'：给原生窗口（PowerShell/DWM）用的那种格式 */
export function paletteBgHex(id: string, systemDark: boolean): string {
  const rgb = paletteBg(id, systemDark).split(/\s+/).map((n) => Number(n) || 0)
  return '#' + rgb.map((n) => Math.max(0, Math.min(255, n)).toString(16).padStart(2, '0')).join('')
}

/**
 * 三档基础色在 index.css 里实际生效的值（与 index.css 逐字一致）。
 * 卡片这类原生窗口要的是「屏幕上真正显示的颜色」，光知道 id 还不够 ——
 * auto 得先按系统深浅落成 light/dark 才有意义。
 */
export const BASE_VARS: Record<'light' | 'dark', PaletteVars> = {
  light: { bg: '244 246 250', card: '255 255 255', ink: '26 34 51', sub: '92 104 128', line: '226 231 240', accent: '37 99 235', side: '110 122 148' },
  dark: { bg: '24 27 33', card: '31 35 43', ink: '232 236 244', sub: '154 164 182', line: '52 58 70', accent: '110 141 248', side: '150 162 182' },
}

/** 这套配色此刻真正落地的 7 个颜色（三档基础色也解析成具体值，不给 null） */
export function paletteVarsOf(id: string, systemDark: boolean): PaletteVars {
  const p = getPalette(id)
  if (p.vars) return p.vars
  return paletteDark(id, systemDark) ? BASE_VARS.dark : BASE_VARS.light
}

/** 基础三档不带 vars —— App 用它决定「要不要把底色报给原生窗口」 */
export function hasOwnVars(id: string): boolean {
  return !!getPalette(id).vars
}

export function readPalette(): string {
  try {
    const v = localStorage.getItem(LS_KEY)
    if (v && PALETTE_IDS.includes(v)) return v
    // 老版本只有 sb_theme（auto/light/dark）：第一次升级上来按它落位，
    // 不然「之前选了深色」的人会被悄悄送回浅色。
    const old = localStorage.getItem('sb_theme')
    if (old === 'dark' || old === 'light') return old
    return DEFAULT_PALETTE
  } catch {
    return DEFAULT_PALETTE
  }
}

/**
 * 把配色写到 <html> 上：data-theme 走基础深浅，7 个变量按需覆盖。
 * 返回实际生效的 dark，调用处拿去同步原生窗口外框。
 *
 * 注意**不要**顺手 clear 别的行内变量：`--sb-title` 也是挂在同一个
 * `documentElement.style` 上的（第 189 项），这里只清自己那 7 个。
 */
export function applyPalette(id: string, systemDark: boolean): { dark: boolean; palette: Palette } {
  const p = getPalette(id)
  const dark = paletteDark(id, systemDark)
  const root = document.documentElement
  root.dataset.theme = dark ? 'dark' : 'light'
  for (const k of VAR_KEYS) root.style.removeProperty('--sb-' + k)
  if (p.vars) {
    for (const k of VAR_KEYS) root.style.setProperty('--sb-' + k, p.vars[k])
  }
  try {
    localStorage.setItem(LS_KEY, p.id)
    // 第 195 项：快搜窗（/mini）是独立的内嵌页面，拿不到这里的模块；
    // 同源 localStorage 是两边唯一零成本的数据通道。
    localStorage.setItem('sb_vars', JSON.stringify({ dark, vars: p.vars || null }))
  } catch {
    /* 隐私模式下 localStorage 会抛，不影响当前这次生效 */
  }
  return { dark, palette: p }
}
