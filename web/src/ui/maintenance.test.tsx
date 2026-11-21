/**
 * 第 247 项：竞品调研后补的两个「可见性」面板，真渲染一遍。
 * 第 294 项（G）：这两块从维护区搬进设置页「高级」折叠（ExtrasPanel），
 * 所以这里改成「先展开高级，再验两个面板」。
 *
 * 为什么必须真渲染：后端 /api/index_failures 与 /api/exclude_test 都写了、
 * i18n 键也齐了，只要界面上没接上，用户照样看不见 —— 典型的
 * 「两边各自绿、合起来错」。这里用 jsdom 断言点了之后真的去请求接口，
 * 并把结果（含 will_retry / 命中规则）显示出来。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ExtrasPanel } from './panels'
import { t } from '../lib/i18n'

function mockFetch() {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const u = String(input)
    if (u.includes('/api/index_failures')) {
      return {
        ok: true,
        json: async () => ({
          items: [{ p: 'D:\\坏文件.pdf', why: '解析超时（>60s）', at: 1, will_retry: true }],
          total: 1,
        }),
      }
    }
    if (u.includes('/api/exclude_test')) {
      return {
        ok: true,
        json: async () => ({ ok: true, excluded: true, hits: [{ kind: 'scope', rule: 'steam' }] }),
      }
    }
    return { ok: true, json: async () => ({}) }
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('设置 · 高级里的失败清单与排除自检（第 247 / 294G 项）', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('展开高级 → 展开失败清单：拉接口、显示路径/原因/重试提示', async () => {
    const fn = mockFetch()
    render(<ExtrasPanel />)
    fireEvent.click(screen.getByText(t('ex_adv')))
    const toggle = await screen.findByText(new RegExp(t('maint_failures')))
    fireEvent.click(toggle)
    expect(await screen.findByText('D:\\坏文件.pdf')).toBeTruthy()
    expect(screen.getByText(/解析超时/)).toBeTruthy()
    expect(screen.getByText(new RegExp(t('maint_failures_retry')))).toBeTruthy()
    expect(fn.mock.calls.some((c) => String(c[0]).includes('/api/index_failures'))).toBe(true)
  })

  it('展开高级 → 排除规则自检：输入路径回车 → 显示「会被排除」与命中规则', async () => {
    const fn = mockFetch()
    render(<ExtrasPanel />)
    fireEvent.click(screen.getByText(t('ex_adv')))
    const input = await screen.findByPlaceholderText(t('maint_exclude_ph'))
    fireEvent.change(input, { target: { value: 'E:\\SteamLibrary\\a.txt' } })
    fireEvent.click(screen.getByText(t('maint_exclude_btn')))
    await waitFor(() => expect(screen.getByText(t('maint_exclude_yes'))).toBeTruthy())
    expect(screen.getByText(/steam/)).toBeTruthy()
    expect(fn.mock.calls.some((c) => String(c[0]).includes('/api/exclude_test?p='))).toBe(true)
  })
})
