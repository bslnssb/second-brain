/**
 * 赞赏码防篡改（第 96 / 104 项）+ 独立赞助弹窗（第 185 项）。
 *
 * 渲染导出的 SupportModal，不需要改产品代码：它会自己请求 /api/donate。
 * 模拟指纹不匹配 / 匹配 / 没有图片 / 多档位四种情况。
 * 另加一条：二维码**不能再回到「关于」里**（用户要求打赏做成独立按钮）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { t } from '../lib/i18n'
import { AboutModal, Modal, SupportModal, Timeline, ErrorLogPanel, HealthPanel, WhyNotFoundModal } from './panels'

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

describe('SupportModal / 赞赏码防篡改', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('指纹不匹配时隐藏二维码并给出警告', async () => {
    mockAbout([{ file: 'donate.png', amount: 0, sha: 'f'.repeat(64), official: false }])
    render(<SupportModal open onClose={() => {}} />)
    expect(await screen.findByText(t('about_fp_bad'))).toBeTruthy()
    expect(screen.queryByAltText(t('donate_alt'))).toBeNull()
  })

  it('指纹匹配时显示二维码和指纹前 12 位', async () => {
    mockAbout([{ file: 'donate.png', amount: 0, sha: SHA, official: true }])
    render(<SupportModal open onClose={() => {}} />)
    const img = await screen.findByAltText(t('donate_alt'))
    expect(img.getAttribute('src')).toContain('donate.png')
    expect(document.body.textContent).toContain(SHA.slice(0, 12))
  })

  it('没有放二维码时说明放哪，而不是破图', async () => {
    mockAbout([])
    render(<SupportModal open onClose={() => {}} />)
    expect(await screen.findByText(t('sp_none'))).toBeTruthy()
    expect(document.body.textContent).toContain(t('sp_none_hint'))
    expect(screen.queryByAltText(t('donate_alt'))).toBeNull()
  })

  it('多档位时左栏列出来，点一下换对应的码', async () => {
    mockAbout([
      { file: 'donate-6.png', amount: 6, sha: SHA, official: true },
      { file: 'donate-20.png', amount: 20, sha: SHA, official: true },
    ])
    render(<SupportModal open onClose={() => {}} />)
    // 左栏标题 + 两档徽标都在
    expect(await screen.findByText(t('sp_tiers'))).toBeTruthy()
    expect(screen.getByText('¥20')).toBeTruthy()
    fireEvent.click(screen.getByText('¥20'))
    await waitFor(() =>
      expect(screen.getByAltText(t('donate_alt')).getAttribute('src')).toContain('donate-20.png'),
    )
  })

  it('「关于」里只留入口按钮，二维码已经搬走', async () => {
    mockAbout([{ file: 'donate.png', amount: 0, sha: SHA, official: true }])
    const onSupport = vi.fn()
    render(<AboutModal open onClose={() => {}} onSupport={onSupport} />)
    expect(screen.queryByAltText(t('donate_alt'))).toBeNull()
    fireEvent.click(await screen.findByText(t('sponsor')))
    expect(onSupport).toHaveBeenCalledTimes(1)
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
  // 第 168 项：授权搬到「版本」行做成胶囊，默认收起 —— 所有需要输入框的用例先点开
  const expand = async () => {
    fireEvent.click(await screen.findByTitle(t('lic_title')))
  }

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('未激活时胶囊显示「免费版」，点开才出现输入框', async () => {
    mockAboutWithLicense(FREE_STATE)
    render(<AboutModal open onClose={() => {}} onSupport={() => {}} />)
    expect(await screen.findByText(t('lic_free'))).toBeTruthy()
    // 收起状态下输入框不该占地方（用户嫌「版本这里太占」）
    expect(screen.queryByPlaceholderText(t('lic_ph'))).toBeNull()
    await expand()
    expect(screen.getByPlaceholderText(t('lic_ph'))).toBeTruthy()
    expect(screen.queryByText('Pro')).toBeNull()
  })

  it('已激活时胶囊直接显示 Pro + 到期日，点开看署名和剩余天数', async () => {
    mockAboutWithLicense(PRO_STATE)
    render(<AboutModal open onClose={() => {}} onSupport={() => {}} />)
    expect(await screen.findByText('Pro')).toBeTruthy()
    // 胶囊本身就带到期日，不用点开也能看到关键信息
    expect(document.body.textContent).toContain('2027-11-07')
    await expand()
    expect(document.body.textContent).toContain('张三')
    expect(document.body.textContent).toContain('2027-11-07')
    expect(document.body.textContent).toContain(t('lic_days').replace('{n}', '365'))
    // 激活后不该再摆着输入框
    expect(screen.queryByPlaceholderText(t('lic_ph'))).toBeNull()
  })

  it('永久授权显示「永久」而不是空白', async () => {
    mockAboutWithLicense({ ...PRO_STATE, exp: '', days_left: null })
    render(<AboutModal open onClose={() => {}} onSupport={() => {}} />)
    await screen.findByText('Pro')
    await expand()
    expect(document.body.textContent).toContain(t('lic_forever'))
  })

  it('激活失败必须说清原因，不能静默', async () => {
    mockAboutWithLicense(FREE_STATE, () => ({ ok: false, reason: 'signature', state: FREE_STATE }))
    render(<AboutModal open onClose={() => {}} onSupport={() => {}} />)
    await expand()
    const input = await screen.findByPlaceholderText(t('lic_ph'))
    fireEvent.change(input, { target: { value: 'SB1.abc.def' } })
    fireEvent.click(screen.getByText(t('lic_act')))
    expect(await screen.findByText(t('lic_err_sig'))).toBeTruthy()
  })

  it('没填内容就点激活，本地就拦下来，不发请求', async () => {
    const fn = mockAboutWithLicense(FREE_STATE)
    render(<AboutModal open onClose={() => {}} onSupport={() => {}} />)
    await expand()
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

  it('grid 缩略图卡片右键会回调统一菜单（带路径与坐标）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(oneGroup)))
    const onMenu = vi.fn()
    render(<Timeline onPick={() => {}} view="grid" onMenu={onMenu} />)
    const btn = (await screen.findByText('a.txt')).closest('button')!
    fireEvent.contextMenu(btn, { clientX: 120, clientY: 80 })
    expect(onMenu).toHaveBeenCalledTimes(1)
    expect(onMenu.mock.calls[0][1]).toBe(oneGroup.groups[0].items[0].p)
    expect(onMenu.mock.calls[0][0].clientX).toBe(120)
  })

  it('list 时间轴行右键也会回调同一套菜单', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(oneGroup)))
    const onMenu = vi.fn()
    render(<Timeline onPick={() => {}} onMenu={onMenu} />)
    const btn = (await screen.findByText('a.txt')).closest('button')!
    fireEvent.contextMenu(btn, { clientX: 60, clientY: 40 })
    expect(onMenu).toHaveBeenCalledTimes(1)
    expect(onMenu.mock.calls[0][1]).toBe(oneGroup.groups[0].items[0].p)
  })
})

describe('ErrorLogPanel / 手动反馈（第 161 项）', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  const state = (items: unknown[] = []) => ({
    ok: true,
    conf: { collect: true, auto: false, ingest: true, include_paths: false, sent: 0, last_error: '' },
    items,
    preview: '',
    note: '',
  })

  function mockErrlog() {
    const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const u = String(input)
      if (u.includes('/api/errlog')) {
        if (init?.method === 'POST') {
          const body = JSON.parse(String(init.body || '{}'))
          if (body.op === 'feedback') {
            return reply({ ok: true, state: state([{
              id: 'r:1', at: '2026-11-13T04:00:00+08:00',
              kind: 'UserFeedback', where: 'user_feedback', size: 100,
            }]) })
          }
          return reply({ ok: true, state: state() })
        }
        return reply(state())
      }
      return reply({})
    })
    vi.stubGlobal('fetch', fn)
    return fn
  }

  it('0 份报告时给出明确引导，并能展开反馈输入框', async () => {
    mockErrlog()
    render(<ErrorLogPanel />)
    expect(await screen.findByText(t('err_no_report'))).toBeTruthy()
    fireEvent.click(screen.getByText(t('err_fb_open')))
    expect(await screen.findByPlaceholderText(t('err_fb_ph'))).toBeTruthy()
  })

  it('写一句描述会 POST op=feedback 并把文字带上', async () => {
    const fn = mockErrlog()
    render(<ErrorLogPanel />)
    fireEvent.click(await screen.findByText(t('err_fb_open')))
    fireEvent.change(await screen.findByPlaceholderText(t('err_fb_ph')),
      { target: { value: '窗口一片白' } })
    fireEvent.click(screen.getByText(t('err_fb_save')))
    await waitFor(() => {
      const posts = fn.mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === 'POST')
      expect(posts.length).toBeGreaterThan(0)
      const body = JSON.parse(String((posts[0][1] as RequestInit).body))
      expect(body.op).toBe('feedback')
      expect(body.text).toBe('窗口一片白')
    })
  })
})


describe('WhyNotFoundModal（第 187 项：从设置搬到弹窗）', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  const diag = {
    total: 3,
    // 后端给的是中文原因原文，前端 whyText() 再翻成界面语言
    groups: [
      { why: '加密', n: 2 },
      { why: '无文本', n: 1 },
    ],
    items: [
      { p: 'D:\\docs\\a.doc', why: '加密' },
      { p: 'D:\\docs\\b.pdf', why: '无文本' },
    ],
  }

  function mockDiag() {
    const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/api/diagnose')) {
        if (init?.method === 'POST') return reply({ ok: true, n: 2 })
        return reply(diag)
      }
      return reply({})
    })
    vi.stubGlobal('fetch', fn)
    return fn
  }

  it('打开就拉诊断，按原因分组并给出总数', async () => {
    mockDiag()
    render(<WhyNotFoundModal open onClose={() => {}} />)
    expect(await screen.findByText(t('why_enc'))).toBeTruthy()
    expect(document.body.textContent).toContain(t('ex_diag_total').replace('{n}', '3'))
    // 文件名只显示 basename，不铺整条路径
    expect(document.body.textContent).toContain('a.doc')
  })

  it('点「重试」会再 POST 一次，然后重新拉结果', async () => {
    const fn = mockDiag()
    render(<WhyNotFoundModal open onClose={() => {}} />)
    fireEvent.click(await screen.findByText(t('ex_diag_retry')))
    await waitFor(() => {
      const posts = fn.mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === 'POST')
      expect(posts.length).toBeGreaterThan(0)
    })
  })
})

describe('HealthPanel（环境体检）', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('把 FAIL 项和修复建议都显示出来，OK 项也在', async () => {
    const fn = vi.fn(async () =>
      reply({
        ok: true,
        issues: [
          { level: 'OK', key: 'disk', title: '磁盘剩余 720 GB' },
          {
            level: 'FAIL',
            key: 'webview2',
            title: '缺少 WebView2 运行时',
            detail: '窗口起不来会白屏',
            fix: '装一次即可（约 2MB）',
          },
        ],
        text: '诊断文本',
      }),
    )
    vi.stubGlobal('fetch', fn)
    render(<HealthPanel />)
    expect(await screen.findByText('缺少 WebView2 运行时')).toBeTruthy()
    expect(screen.getByText(/装一次即可/)).toBeTruthy()
    expect(screen.getByText('磁盘剩余 720 GB')).toBeTruthy()
    // 摘要徽章不能把 FAIL 说成「全部正常」
    expect(document.body.textContent).toContain(t('hc_need').replace('{n}', '1'))
  })

  it('后端报错时给出明确失败提示，不空着', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => reply({ ok: false, msg: 'probe boom' })),
    )
    render(<HealthPanel />)
    expect(await screen.findByText(new RegExp(t('hc_fail')))).toBeTruthy()
    expect(document.body.textContent).toContain('probe boom')
  })
})

/**
 * 弹窗遮罩必须让开标题栏（第 189 项）。
 *
 * 窗口最顶上那 8-9px 是 DWM 画的 caption 带，网页遮罩盖不到它；
 * 遮罩要是连网页里的标题栏一起压暗，用户看到的就是「上面一条白、下面一条灰」。
 * 所以遮罩的 top 必须是 --sb-title（标题栏高度），且不许再用 inset-0 铺满整窗。
 */
describe('Modal 遮罩（第 189 项）', () => {
  it('遮罩从标题栏底下开始，不再盖住标题栏', () => {
    const { container } = render(
      <Modal title="t" onClose={() => {}} width={400}>
        <div>x</div>
      </Modal>,
    )
    const ov = container.firstElementChild as HTMLElement
    expect(ov.getAttribute('style') || '').toContain('var(--sb-title')
    expect(ov.className).not.toContain('inset-0')
    expect(ov.className).toContain('bottom-0')
  })
})
