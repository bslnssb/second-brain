/**
 * 「关于」面板里的赞赏码防篡改（第 96 / 104 项）单元测试。
 *
 * 渲染导出的 AboutModal，不需要改产品代码：内部 DonateQR 会自己请求 /api/donate。
 * 模拟指纹不匹配 / 匹配 / 没有图片三种情况，验证二维码只在该显示时显示。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { t } from '../lib/i18n'
import { AboutModal, Timeline } from './panels'

function reply(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

function mockAbout(items: unknown[]) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const u = String(input)
    if (u.includes('/api/donate')) return reply({ ok: true, items })
    if (u.includes('/api/stats')) return reply({ files: 10, docs: 3 })
    if (u.includes('/api/ping')) {
      return reply({
        ver: 'v0.9.48',
        repo: 'https://github.com/bslnssb/second-brain',
        releases: 'https://github.com/bslnssb/second-brain/releases',
        issues: 'https://github.com/bslnssb/second-brain/issues',
      })
    }
    return reply({})
  })
  vi.stubGlobal('fetch', fn)
}

const SHA = '956fec5ba8786652cae62cbe828450771301320c8c3e40f5e4aad4ed544be38d'

describe('AboutModal / DonateQR', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('指纹不匹配时隐藏二维码并给出警告', async () => {
    mockAbout([{ file: 'donate.png', amount: 0, sha: 'f'.repeat(64), official: false }])
    render(<AboutModal open onClose={() => {}} />)
    expect(await screen.findByText(t('about_fp_bad'))).toBeTruthy()
    expect(screen.queryByAltText(t('donate_alt'))).toBeNull()
  })

  it('指纹匹配时显示二维码和指纹前 12 位', async () => {
    mockAbout([{ file: 'donate.png', amount: 0, sha: SHA, official: true }])
    render(<AboutModal open onClose={() => {}} />)
    const img = await screen.findByAltText(t('donate_alt'))
    expect(img.getAttribute('src')).toContain('donate.png')
    expect(document.body.textContent).toContain(SHA.slice(0, 12))
  })

  it('没有放二维码时显示占位文字而不是破图', async () => {
    mockAbout([])
    render(<AboutModal open onClose={() => {}} />)
    // 占位文案被 <br> 拆成两个文本节点，getByText 精确匹配不到，改查整块文本
    await waitFor(() => expect(document.body.textContent).toContain(t('donate_put')))
    expect(screen.queryByAltText(t('donate_alt'))).toBeNull()
  })
})

describe('Timeline 列头（第 115 项）', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  const oneGroup = {
    days: 14,
    groups: [{
      d: '2026-11-07', label: '今天', n: 1,
      items: [{ p: 'D:\\tmp\\a.txt', n: 'a.txt', s: 123, m: 1793991187 }],
    }],
  }

  it('list 视图渲染「名称/路径/大小/时间」列头', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(oneGroup)))
    render(<Timeline onPick={() => {}} />)
    expect(await screen.findByText('a.txt')).toBeTruthy()
    expect(screen.getByText(t('col_name_only'))).toBeTruthy()
    expect(screen.getByText(t('col_path'))).toBeTruthy()
    expect(screen.getByText(t('col_size'))).toBeTruthy()
    expect(screen.getByText(t('col_mtime'))).toBeTruthy()
  })

  it('grid（缩略图）视图不渲染列头', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(oneGroup)))
    render(<Timeline onPick={() => {}} view="grid" />)
    expect(await screen.findByText('a.txt')).toBeTruthy()
    expect(screen.queryByText(t('col_name_only'))).toBeNull()
  })
})
