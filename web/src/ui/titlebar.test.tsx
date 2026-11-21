/**
 * 详细搜索栏标题栏品牌按钮与版本/检查更新测试（第 250 项）
 *
 * 对标 Antigravity 体验：图标与名字做成胶囊按钮，
 * 点击后下拉显示当前版本和检查更新。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { TitleBar } from './titlebar'
import { t } from '../lib/i18n'

function mockBridge() {
  vi.stubGlobal('bridge', () => ({
    titlebar_state: async () => ({ max: false }),
    titlebar_cmd: async () => true,
  }))
}

describe('TitleBar · 品牌胶囊按钮与版本/更新菜单', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('初始状态：渲染胶囊按钮与 Memex 名字，下拉菜单默认不展开', () => {
    mockBridge()
    const { container } = render(
      <TitleBar
        onMini={() => {}}
        onDiag={() => {}}
        onSupport={() => {}}
        ver="v0.9.55"
      />
    )
    const btn = container.querySelector('button[data-act="brand"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(screen.getByText('Memex')).toBeTruthy()
    // 第 281 项：副标题改走 i18n，别再按中文原文断言（切到印地语就该变）
    expect(screen.getByText(t('titlebar_sub'))).toBeTruthy()
    expect(screen.queryByText(/Version 0\.9\.55/)).toBeNull()
  })

  it('点击胶囊按钮：展开菜单并显示当前版本与「检查更新」', () => {
    mockBridge()
    const { container } = render(
      <TitleBar
        onMini={() => {}}
        onDiag={() => {}}
        onSupport={() => {}}
        ver="v0.9.55"
      />
    )
    const btn = container.querySelector('button[data-act="brand"]') as HTMLButtonElement
    fireEvent.click(btn)

    expect(screen.getByText('Version 0.9.55')).toBeTruthy()
    expect(screen.getByText(t('about_check'))).toBeTruthy()
  })

  it('点击版本条目：触发 onOpenAbout 且关闭菜单', () => {
    mockBridge()
    const onAbout = vi.fn()
    const { container } = render(
      <TitleBar
        onMini={() => {}}
        onDiag={() => {}}
        onSupport={() => {}}
        onOpenAbout={onAbout}
        ver="v0.9.55"
      />
    )
    const btn = container.querySelector('button[data-act="brand"]') as HTMLButtonElement
    fireEvent.click(btn)
    fireEvent.click(screen.getByText('Version 0.9.55'))

    expect(onAbout).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Version 0.9.55')).toBeNull()
  })

  it('点击「检查更新」：调用接口，已是最新时显示相应反馈', async () => {
    mockBridge()
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ ok: true, newer: false, latest: '0.9.55' }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(
      <TitleBar
        onMini={() => {}}
        onDiag={() => {}}
        onSupport={() => {}}
        ver="v0.9.55"
      />
    )
    const btn = container.querySelector('button[data-act="brand"]') as HTMLButtonElement
    fireEvent.click(btn)
    const checkBtn = screen.getByText(t('about_check'))
    fireEvent.click(checkBtn)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
      expect(screen.getByText(new RegExp(t('up_uptodate').replace('{v}', '.*')))).toBeTruthy()
    })
  })

  it('按 Escape 键或点击外部：关闭下拉菜单', () => {
    mockBridge()
    const { container } = render(
      <div>
        <div data-testid="outside">外面</div>
        <TitleBar
          onMini={() => {}}
          onDiag={() => {}}
          onSupport={() => {}}
          ver="v0.9.55"
        />
      </div>
    )
    const btn = container.querySelector('button[data-act="brand"]') as HTMLButtonElement
    fireEvent.click(btn)
    expect(screen.getByText('Version 0.9.55')).toBeTruthy()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByText('Version 0.9.55')).toBeNull()

    // 重新打开并点击外部
    fireEvent.click(btn)
    expect(screen.getByText('Version 0.9.55')).toBeTruthy()

    fireEvent.pointerDown(screen.getByTestId('outside'))
    expect(screen.queryByText('Version 0.9.55')).toBeNull()
  })
})
