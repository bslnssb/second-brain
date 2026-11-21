import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SettingsDrawer } from './panels'
import { t } from '../lib/i18n'

/**
 * 第 358 项：用户「使用帮助和热键重新整理下，看起来有很多重复……如果还打算保留，把使用帮助做进设置里面」。
 *
 * 断言两件事（真渲染，不 grep）：
 * 1. 设置里有一页「使用帮助」，落在 startSec='help' 时直接可见，且有语法/命令/其它三段；
 * 2. 这页里**没有**快捷键表 —— 快捷键只在「热键」页出现一次（去重的核心）。
 */
const props = {
  open: true,
  onClose: () => {},
  palette: 'auto',
  onPalette: () => {},
  wall: { on: false, opacity: 80, blur: 0, pos_x: 50, pos_y: 50, zoom: 100, scope_main: true, scope_settings: true, scope_mini: true },
  onWall: () => {},
}

vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
  const u = String(input)
  const body = u.includes('/api/stats') ? { files: 10, docs: 3 } : {}
  return { ok: true, json: async () => body } as unknown as Response
}))

describe('使用帮助并进设置', () => {
  it('帮助页有命令分区、四条命令，且不再重复快捷键表', async () => {
    render(<SettingsDrawer {...props} startSec="help" />)
    expect(await screen.findByText(t('help_cmd'))).toBeTruthy()
    for (const k of ['shot', 'verify', 'cmd', 'psh']) {
      expect(screen.getByText(k)).toBeTruthy()
    }
    // 搜索语法与「其它好用的」两段也在
    expect(screen.getByText(t('help_qs'))).toBeTruthy()
    expect(screen.getByText(t('help_more'))).toBeTruthy()
    // 快捷键不再出现在帮助页（只有热键页那一份）
    expect(screen.queryByText(t('kb3'))).toBeNull()
    // 反过来给一句指引：快捷键统一在「热键」页
    expect(screen.getByText(t('help_kb_hint'))).toBeTruthy()
  })
})
