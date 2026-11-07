/**
 * 「关于」面板里的赞赏码防篡改（第 96 / 104 项）单元测试。
 *
 * 渲染导出的 AboutModal，不需要改产品代码：内部 DonateQR 会自己请求 /api/donate。
 * 模拟指纹不匹配 / 匹配 / 没有图片三种情况，验证二维码只在该显示时显示。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

const FREE_STATE = {
  valid: false, reason: 'none', edition: 'free', kind: '', name: '', ident: '',
  exp: '', issued: '', features: [], days_left: null, code_masked: '',
  clock_warn: false, activated: '',
}

const PRO_STATE = {
  valid: true, reason: 'ok', edition: 'pro', kind: 'pro', name: '张三', ident: '',
  exp: '2027-11-07', issued: '20261107T2039', features: ['export'],
  days_left: 365, code_masked: 'SB1.eNqrVipTsj…m0FYpPCw', clock_warn: false,
  activated: '2026-11-07 20:39',
}

/** 关于面板要拉 stats/ping/donate，这里一并兜住，只让 /api/license 按需可变 */
function mockAboutWithLicense(state: unknown, onActivate?: (code: string) => unknown) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const u = String(input)
    if (u.includes('/api/license')) {
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as { code?: string }
        return reply(onActivate ? onActivate(body.code || '') : { ok: true, state })
      }
      return reply({ ok: true, state })
    }
    if (u.includes('/api/stats')) return reply({ files: 10, docs: 3 })
    if (u.includes('/api/ping')) return reply({ ver: 'v0.9.53' })
    return reply({})
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('AboutModal / LicenseBox', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('未激活时只显示「免费版 + 输入框」，不打扰用户', async () => {
    mockAboutWithLicense(FREE_STATE)
    render(<AboutModal open onClose={() => {}} />)
    expect(await screen.findByText(t('lic_free'))).toBeTruthy()
    expect(screen.getByPlaceholderText(t('lic_ph'))).toBeTruthy()
    expect(screen.queryByText('Pro')).toBeNull()
  })

  it('已激活时显示 Pro、署名和剩余天数', async () => {
    mockAboutWithLicense(PRO_STATE)
    render(<AboutModal open onClose={() => {}} />)
    expect(await screen.findByText('Pro')).toBeTruthy()
    expect(document.body.textContent).toContain('张三')
    expect(document.body.textContent).toContain('2027-11-07')
    expect(document.body.textContent).toContain(t('lic_days').replace('{n}', '365'))
    // 激活后不该再摆着输入框
    expect(screen.queryByPlaceholderText(t('lic_ph'))).toBeNull()
  })

  it('永久授权显示「永久」而不是空白', async () => {
    mockAboutWithLicense({ ...PRO_STATE, exp: '', days_left: null })
    render(<AboutModal open onClose={() => {}} />)
    await screen.findByText('Pro')
    expect(document.body.textContent).toContain(t('lic_forever'))
  })

  it('激活失败必须说清原因，不能静默', async () => {
    mockAboutWithLicense(FREE_STATE, () => ({ ok: false, reason: 'signature', state: FREE_STATE }))
    render(<AboutModal open onClose={() => {}} />)
    const input = await screen.findByPlaceholderText(t('lic_ph'))
    fireEvent.change(input, { target: { value: 'SB1.abc.def' } })
    fireEvent.click(screen.getByText(t('lic_act')))
    expect(await screen.findByText(t('lic_err_sig'))).toBeTruthy()
  })

  it('没填内容就点激活，本地就拦下来，不发请求', async () => {
    const fn = mockAboutWithLicense(FREE_STATE)
    render(<AboutModal open onClose={() => {}} />)
    await screen.findByPlaceholderText(t('lic_ph'))
    const before = fn.mock.calls.length
    fireEvent.click(screen.getByText(t('lic_act')))
    expect(await screen.findByText(t('lic_err_empty'))).toBeTruthy()
    const posted = fn.mock.calls.filter(([u, i]) => String(u).includes('/api/license') && (i as RequestInit)?.method === 'POST')
    expect(posted.length).toBe(0)
    expect(before).toBeGreaterThan(0)
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
