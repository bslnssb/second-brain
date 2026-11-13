import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { t } from '../lib/i18n'
import { ErrorBoundary } from './errorboundary'

function Boom(): never {
  throw new Error('boom-from-render')
}

describe('ErrorBoundary', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('正常时原样渲染子树', () => {
    render(
      <ErrorBoundary>
        <div>ok-content</div>
      </ErrorBoundary>,
    )
    expect(screen.getByText('ok-content')).toBeTruthy()
  })

  it('render 抛错时给兜底页，并把错误信息露出来', () => {
    // React 会往 console.error 打整段组件栈，测试里静音，否则刷屏
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    )
    expect(screen.getByText('boom-from-render')).toBeTruthy()
    // 第 162 项起兜底页有两个按钮：重新载入 + 报告此问题
    expect(screen.getAllByRole('button')).toHaveLength(2)
    expect(screen.getByText(t('eb_report'))).toBeTruthy()
  })

  it('点「报告此问题」会把错误写进本机报告（op=feedback）', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const fn = vi.fn(async () => ({
      ok: true, status: 200,
      json: async () => ({ ok: true, conf: {}, items: [], preview: '', note: '' }),
    }))
    vi.stubGlobal('fetch', fn)
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    )
    fireEvent.click(screen.getByText(t('eb_report')))
    await waitFor(() => {
      expect(fn).toHaveBeenCalled()
      const [, init] = fn.mock.calls[0] as unknown as [string, RequestInit]
      const body = JSON.parse(String(init.body))
      expect(body.op).toBe('feedback')
      expect(body.text).toContain('boom-from-render')
    })
    expect(await screen.findByText(t('eb_reported'))).toBeTruthy()
    vi.unstubAllGlobals()
  })
})
