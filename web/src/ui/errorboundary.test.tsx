import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ErrorBoundary } from './errorboundary'

function Boom(): never {
  throw new Error('boom-from-render')
}

describe('ErrorBoundary', () => {
  afterEach(() => {
    vi.restoreAllMocks()
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
    expect(screen.getByRole('button')).toBeTruthy()
  })
})
