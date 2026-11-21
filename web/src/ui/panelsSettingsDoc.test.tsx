import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SettingsDrawer } from './panels'

function reply(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

/**
 * 第 356 项：用户「这些用法要在设置里面写出来，不然用户不知道我也不知道」。
 * 快捷命令和搜索语法是没有开关、只能靠记的东西，所以要有能翻看的地方；
 * 这里锁住「卡片真的渲染出来、四条命令都在」—— 只断言代码存在会假绿（201/202 的教训）。
 * 测试环境的界面语言是英文（无 localStorage），所以断言英文文案。
 */
describe('第 356 项：设置里的命令与语法说明', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('落在快搜窗分区时两张卡都在', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const u = String(input)
      if (u.includes('/api/config')) {
        return reply({ exists: true, include_dirs: [], exclude_dirs: [], docs: 0, scope: null })
      }
      if (u.includes('/api/stats')) return reply({ files: 10, docs: 3 })
      return reply({})
    }))
    render(
      <SettingsDrawer
        open
        startSec={'mini'}
        onClose={() => {}}
        palette='auto'
        onPalette={() => {}}
        wall={{ on: false, opacity: 80, blur: 0 }}
        onWall={() => {}}
      />,
    )
    expect(await screen.findByText('Typed commands')).toBeTruthy()
    expect(screen.getByText('Search syntax')).toBeTruthy()
    expect(screen.getByText('psh')).toBeTruthy()
    expect(screen.getByText('shot')).toBeTruthy()
    expect(screen.getByText(/trailing backslash/)).toBeTruthy()
  })
})
