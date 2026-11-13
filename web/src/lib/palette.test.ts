/**
 * 主题配色（第 191 项）的单测。
 *
 * 盯的是三件容易悄悄坏掉的事：
 * 1. 换配色时**必须把上一套的行内变量清干净** —— 否则「暖纸 → 跟随系统」会
 *    留着暖色底，看着像没生效；
 * 2. 清变量时**不能顺手把 `--sb-title` 也清了**（第 189 项弹窗遮罩靠它让开标题栏）；
 * 3. 老版本只有 sb_theme，升级上来要跟着它落位，不能把深色用户送回浅色。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import {
  applyPalette,
  DEFAULT_PALETTE,
  PALETTES,
  paletteBgHex,
  paletteDark,
  readPalette,
} from './palette'

const VARS = ['bg', 'card', 'ink', 'sub', 'line', 'accent', 'side']

beforeEach(() => {
  const root = document.documentElement
  root.removeAttribute('data-theme')
  for (const k of VARS) root.style.removeProperty('--sb-' + k)
  root.style.removeProperty('--sb-title')
  localStorage.clear()
})

describe('PALETTES', () => {
  it('15 套、id 不重复、auto 排第一', () => {
    expect(PALETTES).toHaveLength(15)
    const ids = PALETTES.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids[0]).toBe('auto')
  })

  it('基础三档不带变量（跟随系统才真能跟系统）', () => {
    for (const id of ['auto', 'light', 'dark']) {
      expect(PALETTES.find((p) => p.id === id)?.vars).toBeUndefined()
    }
  })
})

describe('applyPalette', () => {
  it('自定义配色写进 7 个变量，data-theme 跟着底色族走', () => {
    const { dark } = applyPalette('paper', false)
    expect(dark).toBe(false)
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(document.documentElement.style.getPropertyValue('--sb-bg')).toBe('246 240 229')
    expect(document.documentElement.style.getPropertyValue('--sb-accent')).toBe('176 118 60')
  })

  it('切回「跟随系统」时把上一套变量清干净', () => {
    applyPalette('night', true)
    expect(document.documentElement.style.getPropertyValue('--sb-bg')).toBe('18 24 38')
    const { dark } = applyPalette('auto', true)
    expect(dark).toBe(true)
    expect(document.documentElement.dataset.theme).toBe('dark')
    for (const k of VARS) {
      expect(document.documentElement.style.getPropertyValue('--sb-' + k)).toBe('')
    }
  })

  it('不动 --sb-title（第 189 项的标题栏高度）', () => {
    document.documentElement.style.setProperty('--sb-title', '36px')
    applyPalette('lilac', false)
    expect(document.documentElement.style.getPropertyValue('--sb-title')).toBe('36px')
  })

  it('深色系配色的 dark 为真，浅色系为假', () => {
    expect(applyPalette('contrast', false).dark).toBe(true)
    expect(applyPalette('mint', true).dark).toBe(false)
    expect(paletteDark('cyber', false)).toBe(true)
    expect(paletteDark('auto', true)).toBe(true)
  })
})

describe('paletteBgHex', () => {
  it('自定义配色取自己的底色', () => {
    expect(paletteBgHex('paper', false)).toBe('#f6f0e5')
    expect(paletteBgHex('night', false)).toBe('#121826')
  })

  it('基础三档用 index.css 的默认值', () => {
    expect(paletteBgHex('light', true)).toBe('#f4f6fa')
    expect(paletteBgHex('auto', true)).toBe('#181b21')
    expect(paletteBgHex('dark', false)).toBe('#181b21')
  })
})

describe('readPalette', () => {
  it('没存过就是跟随系统', () => {
    expect(readPalette()).toBe(DEFAULT_PALETTE)
  })

  it('老版本只有 sb_theme 时按它落位', () => {
    localStorage.setItem('sb_theme', 'dark')
    expect(readPalette()).toBe('dark')
  })

  it('存了不认识的值就回默认，不崩', () => {
    localStorage.setItem('sb_palette', 'hotdog')
    expect(readPalette()).toBe(DEFAULT_PALETTE)
  })
})
