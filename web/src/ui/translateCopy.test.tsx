/**
 * 第 305 项：聚合翻译结果复制。
 *
 * 用户原话：「聚合翻译的结果无法复制」。
 *
 * 钉死的三件事：
 * 1. 点结果卡先走服务端 `/api/snip/copy_text`（Win32 剪贴板），成功后提示已复制；
 * 2. 服务端说不行、浏览器 Clipboard API 又 reject 时，退到 execCommand('copy')；
 * 3. 三级全挂才提示失败 —— 不能像以前那样什么都没复制也弹「已复制」。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setLang, t } from '../lib/i18n'
import { TranslateModal } from './TranslateModal'

afterEach(cleanup)

const RESULTS = {
  ok: true,
  results: [{ engine: 'Google', engine_id: 'google', text: '你好，世界', extra: '' }],
}

function mockFetch(copyOk: boolean) {
  return vi.fn((url: string) => {
    if (url === '/api/settings') return Promise.resolve({ json: () => Promise.resolve({}) })
    if (url === '/api/translate') return Promise.resolve({ json: () => Promise.resolve(RESULTS) })
    if (url === '/api/snip/copy_text') return Promise.resolve({ json: () => Promise.resolve({ ok: copyOk }) })
    return Promise.resolve({ json: () => Promise.resolve({}) })
  })
}

async function openWithResult() {
  render(<TranslateModal isOpen onClose={vi.fn()} onToast={onToast} initialQuery="hello" />)
  await waitFor(() => expect(screen.getByText('你好，世界')).toBeTruthy())
}

let onToast: ReturnType<typeof vi.fn>

beforeEach(() => {
  setLang('zh')
  onToast = vi.fn()
  vi.stubGlobal('fetch', mockFetch(true))
})

describe('聚合翻译结果复制', () => {
  it('先走服务端剪贴板，成功即提示已复制', async () => {
    await openWithResult()
    fireEvent.click(screen.getByText('你好，世界'))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith(t('trans_copied')))
    const calls = (fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls
    const copy = calls.find((c) => c[0] === '/api/snip/copy_text')
    expect(copy).toBeTruthy()
    expect(String(copy?.[1]?.body)).toContain('你好，世界')
  })

  it('服务端失败且浏览器剪贴板不可用时，退到 execCommand', async () => {
    vi.stubGlobal('fetch', mockFetch(false))
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    const exec = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true })
    await openWithResult()
    fireEvent.click(screen.getByText('你好，世界'))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith(t('trans_copied')))
    expect(exec).toHaveBeenCalledWith('copy')
  })

  it('三级全失败时提示复制失败，不再谎报成功', async () => {
    vi.stubGlobal('fetch', mockFetch(false))
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    Object.defineProperty(document, 'execCommand', { value: vi.fn(() => false), configurable: true })
    await openWithResult()
    fireEvent.click(screen.getByText('你好，世界'))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith(t('trans_copy_fail')))
    expect(onToast).not.toHaveBeenCalledWith(t('trans_copied'))
  })
})
