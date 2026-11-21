/**
 * 第 299 项：设置页「网络代理」。
 *
 * 钉三件事：
 * ① 三种模式都在界面上（跟随系统 / 不使用 / 自定义），文案走 i18n，不写死中文；
 * ② 选了「不使用代理」时自定义输入框禁用 —— 不能让人以为还要再填一遍；
 * ③ 「测试连接」把后端结果拼成人话（成功带毫秒与出口，失败给原因），失败不假装成功。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ProxySection } from './panels'
import { t } from '../lib/i18n'

function reply(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

function mockFetch(testBody: unknown) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const u = String(input)
    if (u.includes('/api/proxy/test')) return reply(testBody)
    if (u.includes('/api/proxy')) return reply({ mode: 'system', url: '', system: 'http://127.0.0.1:7897' })
    return reply({})
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('ProxySection（网络代理）', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('三种模式都列出，且文案不是中文写死（走 i18n）', async () => {
    mockFetch({ ok: true, ms: 128, via: 'http://127.0.0.1:7897' })
    render(<ProxySection mode="system" host="127.0.0.1" port={7897} onSave={() => {}} />)
    expect(await screen.findByText(t('proxy_system'))).toBeTruthy()
    expect(screen.getByText(t('proxy_none'))).toBeTruthy()
    expect(screen.getByText(t('proxy_custom'))).toBeTruthy()
  })

  it('点「不使用代理」立刻保存 proxy_mode=none', async () => {
    mockFetch({ ok: true })
    const onSave = vi.fn()
    render(<ProxySection mode="system" host="" port={7890} onSave={onSave} />)
    fireEvent.click(await screen.findByText(t('proxy_none')))
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ proxy_mode: 'none' }))
    // 非自定义模式下输入框禁用
    expect((screen.getByDisplayValue('') as HTMLInputElement).disabled).toBe(true)
  })

  it('测试连接成功：显示毫秒和实际出口', async () => {
    mockFetch({ ok: true, ms: 128, via: 'http://127.0.0.1:7897' })
    render(<ProxySection mode="custom" host="127.0.0.1" port={7897} onSave={() => {}} />)
    fireEvent.click(await screen.findByText(t('proxy_test')))
    const want = t('proxy_test_ok').replace('{ms}', '128').replace('{v}', 'http://127.0.0.1:7897')
    expect(await screen.findByText(want)).toBeTruthy()
  })

  it('测试连接失败：给原因，不显示成功', async () => {
    mockFetch({ ok: false, code: 'timeout', ms: 6000, via: '' })
    render(<ProxySection mode="system" host="" port={7890} onSave={() => {}} />)
    fireEvent.click(await screen.findByText(t('proxy_test')))
    const want = t('proxy_test_fail').replace('{why}', t('proxy_why_timeout'))
    expect(await screen.findByText(want)).toBeTruthy()
    expect(screen.queryByText(want.replace(t('proxy_why_timeout'), 'x'))).toBeNull()
  })

  it('端口非法（空）也照样能测：草稿里 port=0 交给后端判', async () => {
    const fn = mockFetch({ ok: false, code: 'bad' })
    const { container } = render(<ProxySection mode="custom" host="127.0.0.1" port={7897} onSave={() => {}} />)
    const inputs = container.querySelectorAll('input')
    expect(inputs.length).toBe(2)
    fireEvent.change(inputs[1], { target: { value: '' } })
    fireEvent.click(screen.getByText(t('proxy_test')))
    await waitFor(() => {
      const call = fn.mock.calls.map((c) => String(c[0])).find((u) => u.includes('/api/proxy/test'))
      expect(call).toBeTruthy()
    })
    expect(await screen.findByText(t('proxy_test_bad'))).toBeTruthy()
  })
})
