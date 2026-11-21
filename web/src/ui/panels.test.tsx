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
import { AboutSection, Modal, SupportModal, Timeline, ErrorLogPanel, HealthPanel, WhyNotFoundModal, SettingsDrawer } from './panels'

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
    render(<AboutSection onSupport={onSupport} />)
    expect(screen.queryByAltText(t('donate_alt'))).toBeNull()
    fireEvent.click(await screen.findByText(t('sponsor')))
    expect(onSupport).toHaveBeenCalledTimes(1)
  })
})

const FREE_STATE = {
  valid: false, reason: 'none', edition: 'free', kind: '', name: '', ident: '',
  exp: '', issued: '', features: [], plan: 'free', days_left: null, code_masked: '',
  clock_warn: false, activated: '',
}

const PRO_STATE = {
  valid: true, reason: 'ok', edition: 'pro', kind: 'pro', name: '张三', ident: '',
  exp: '2027-11-07', issued: '20261107T2039', features: ['export'], plan: 'pro',
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

describe('AboutSection / LicenseBox', () => {
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
    render(<AboutSection onSupport={() => {}} />)
    expect(await screen.findByText(t('lic_free'))).toBeTruthy()
    // 收起状态下输入框不该占地方（用户嫌「版本这里太占」）
    expect(screen.queryByPlaceholderText(t('lic_ph'))).toBeNull()
    await expand()
    expect(screen.getByPlaceholderText(t('lic_ph'))).toBeTruthy()
    expect(screen.queryByText('Pro')).toBeNull()
  })

  it('已激活时胶囊直接显示 Pro + 到期日，点开看署名和剩余天数', async () => {
    mockAboutWithLicense(PRO_STATE)
    render(<AboutSection onSupport={() => {}} />)
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
    render(<AboutSection onSupport={() => {}} />)
    await screen.findByText('Pro')
    await expand()
    expect(document.body.textContent).toContain(t('lic_forever'))
  })

  it('激活失败必须说清原因，不能静默', async () => {
    mockAboutWithLicense(FREE_STATE, () => ({ ok: false, reason: 'signature', state: FREE_STATE }))
    render(<AboutSection onSupport={() => {}} />)
    await expand()
    const input = await screen.findByPlaceholderText(t('lic_ph'))
    fireEvent.change(input, { target: { value: 'SB1.abc.def' } })
    fireEvent.click(screen.getByText(t('lic_act')))
    expect(await screen.findByText(t('lic_err_sig'))).toBeTruthy()
  })

  it('没填内容就点激活，本地就拦下来，不发请求', async () => {
    const fn = mockAboutWithLicense(FREE_STATE)
    render(<AboutSection onSupport={() => {}} />)
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

  it('list 视图四列手柄均可拖拽并各自更新对应键', async () => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn(async () => reply(oneGroup)))
    render(<Timeline onPick={vi.fn()} view="list" />)
    await screen.findByText('a.txt')
    const handles = Array.from(
      document.querySelectorAll('[class*="cursor-col-resize"]'),
    ) as HTMLElement[]
    expect(handles.length).toBe(4)
    const keys = ['name', 'path', 'size', 'mtime'] as const
    const before = JSON.parse(localStorage.getItem('sb_colw') || '{}')
    expect([before.name, before.path, before.size, before.mtime]).toEqual([240, 240, 48, 112])
    for (let i = 0; i < 4; i++) {
      const h = handles[i]
      const start = (JSON.parse(localStorage.getItem('sb_colw') || '{}'))[keys[i]]
      fireEvent.mouseDown(h, { clientX: 100 })
      fireEvent.mouseMove(document, { clientX: 150 })
      fireEvent.mouseUp(document)
      const after = JSON.parse(localStorage.getItem('sb_colw') || '{}')
      expect(after[keys[i]]).toBe(start + 50)
    }
  })

  it('list 视图表头与行采用 minmax+fr 响应式自适应列宽（防止窗口缩小时大小时间消失、放大时右侧大面积留白）', async () => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn(async () => reply(oneGroup)))
    render(<Timeline onPick={vi.fn()} view="list" />)
    await screen.findByText('a.txt')
    const header = document.querySelector('.sticky.top-0') as HTMLElement
    expect(header).toBeTruthy()
    expect(header.style.gridTemplateColumns).toContain('minmax(80px, 240fr)')
    expect(header.style.gridTemplateColumns).toContain('minmax(44px, 48fr)')
    expect(header.style.gridTemplateColumns).toContain('minmax(64px, 112fr)')
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
 * 弹窗遮罩必须铺满整个窗口（第 381 项修正第 189 项）。
 *
 * 第 189 项当时把遮罩写成「absolute + top: var(--sb-title) + bottom-0」，
 * 前提是弹窗挂在页面根节点下。但关于页开源组件、赞助、诊断、批量 OCR 这些
 * 弹窗都挂在设置抽屉内部，absolute 的参照系变成了抽屉内容区 ——
 * 遮罩只从内容区往下铺，上方「设置 / 关于」标题带整条保持亮色，
 * 用户在真机上看到的就是「一半黑一半白」。
 *
 * 现在的契约：portal 到 body + fixed inset-0，四边都压满，不依赖挂载位置。
 */
describe('Modal 遮罩（第 381 项）', () => {
  it('遮罩固定铺满整窗，且不再依赖 --sb-title 偏移', () => {
    render(
      <Modal title="t" onClose={() => {}} width={400}>
        <div>x</div>
      </Modal>,
    )
    // portal 到 body 之后，container 里什么都没有
    const ov = document.body.querySelector('[role="dialog"]')?.parentElement as HTMLElement
    expect(ov).toBeTruthy()
    expect(ov.className).toContain('fixed')
    expect(ov.className).toContain('inset-0')
    expect(ov.getAttribute('style') || '').not.toContain('var(--sb-title')
  })
})

/**
 * SettingsDrawer 的 Listary 式分页（第 207 项）。
 *
 * 之前 21 个用例没有一个直接渲染过设置抽屉 —— 201/202 项的「运行期假绿」
 * 教训就是静态断言会漏，所以这里用 jsdom 真渲染一遍：
 * 六个分类齐、默认落在常规设置、点导航才切页。
 */
describe('SettingsDrawer Listary 式分页（第 207 项）', () => {
  const drawerProps = {
    open: true,
    onClose: () => {},
    palette: 'auto',
    onPalette: () => {},
    wall: { on: false, opacity: 80, blur: 0, pos_x: 50, pos_y: 50, zoom: 100, scope_main: true, scope_settings: true, scope_mini: true },
    onWall: () => {},
    onOpenAbout: () => {},
  }

  /** 设置抽屉打开时拉 settings/autostart/stats/config 四个接口；config 给出可渲染的 scope */
  function mockSettingsFetch() {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const u = String(input)
      if (u.includes('/api/config')) {
        return reply({
          exists: true, include_dirs: [], exclude_dirs: [], docs: 0,
          scope: {
            include_dirs: ['C:\\Users\\Public\\Documents'], exclude_dirs: [],
            drives: [['C:', 12345]], top: [], files: 12345, docs: 0,
            rules: { ext: 12, skip: 0, size_limit: 2147483648 },
          },
        })
      }
      if (u.includes('/api/stats')) return reply({ files: 10, docs: 3 })
      return reply({})
    }))
  }

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('左侧六个分类 + 底部「关于」入口都在，默认落在常规设置', async () => {
    mockSettingsFetch()
    render(<SettingsDrawer {...drawerProps} />)
    expect(await screen.findByText(t('nav_general'))).toBeTruthy()
    for (const k of ['nav_appearance', 'nav_hotkey', 'nav_files', 'nav_mini', 'web_search', 'about']) {
      expect(screen.getByText(t(k)), k).toBeTruthy()
    }
    // 默认页是常规设置：界面语言行在；文件搜索页的「索引范围」卡片不在
    expect(screen.getByText(t('lang_label'))).toBeTruthy()
    expect(screen.queryByText(t('scope_title'))).toBeNull()
  })

  it('点导航切到「文件搜索」：索引范围出现，索引集默认收在「高级」里', async () => {
    mockSettingsFetch()
    render(<SettingsDrawer {...drawerProps} />)
    await screen.findByText(t('nav_general'))
    fireEvent.click(screen.getByText(t('nav_files')))
    expect(await screen.findByText(t('scope_title'))).toBeTruthy()
    // 第 294 项（F）：多库默认折叠 —— 只有「高级」这一行的标题，卡片本身不渲染
    expect(screen.getByText(t('ex_adv'))).toBeTruthy()
    expect(screen.queryByText(t('ex_lib_title'))).toBeNull()
    // 展开后才出现
    fireEvent.click(screen.getByText(t('ex_adv')))
    expect(await screen.findByText(t('ex_lib_title'))).toBeTruthy()
    // 第 296 项：用户「高级里面很多功能都没说明」—— 每块都要有使用说明，不能只有标题
    // （「识别整本」那块要传 onOpenOcr 才渲染，由设置抽屉真实调用时提供，这里不渲染它）
    for (const k of ['ex_lib_help', 'ex_fail_help', 'ex_excl_help', 'ex_saved_help']) {
      expect(screen.getByText(t(k)), k).toBeTruthy()
    }
    expect(screen.queryByText(t('lang_label'))).toBeNull()
  })

  /* 第 302 项：用户「卸载程序是这样放到安装目录里面去的，不是放到设置里面去的」——
     设置里不该再出现卸载卡，卸载交给安装目录的 Uninstall.exe。 */
  it('设置里不再出现卸载卡（第 302 项）', async () => {
    mockSettingsFetch()
    render(<SettingsDrawer {...drawerProps} />)
    await screen.findByText(t('nav_general'))
    expect(screen.queryByText(t('un_title'))).toBeNull()
    expect(screen.queryByText(t('un_keep'))).toBeNull()
    expect(screen.queryByText(t('un_full'))).toBeNull()
  })

  /** 第 245 项：用户定稿弹窗样式只剩方案1。
   * 以前这里是个 <select>（方案1 / 方案2），只做静态断言容易漏「下拉框又回来了」，
   * 所以真渲染一次「快搜窗」分类，确认没有一个「方案切换」下拉、方案2 文案不在。
   * 第 275 项追加「最近记录显示几条」后，这里确实有且只剩那一个下拉，
   * 所以判据改成「下拉数量 = 1，且它是条数选择（选项是 1/2/3…条）」。 */
  /* 第 300 项：用户「设置里加个快捷键分区把已经快捷键弄进去」。
     这里真渲染快捷键页：标题在、键位逐条在、说明文字不落空；
     并且「当前唤起方式」那一行跟着上面选的模式变（写死 Ctrl Ctrl 会骗人）。 */
  it('快捷键页列出所有生效按键，唤起键跟着模式变（第 300 项）', async () => {
    mockSettingsFetch()
    render(<SettingsDrawer {...drawerProps} />)
    await screen.findByText(t('nav_general'))
    fireEvent.click(screen.getByText(t('nav_hotkey')))

    expect(await screen.findByText(t('sc_title'))).toBeTruthy()
    expect(screen.getByText(t('sc_tip'))).toBeTruthy()
    expect(screen.getByText(t('sc_more'))).toBeTruthy()

    // 键位逐个在：上下、Enter、Ctrl+Enter、Ctrl+1~9、Ctrl+S、Ctrl+F、Esc、Ctrl+G
    for (const k of ['↑ ↓', 'Enter', 'Ctrl+Enter', 'Ctrl+1~9', 'Ctrl+S', 'Ctrl+F', 'Esc', 'Ctrl+G']) {
      expect(screen.getByText(k), k).toBeTruthy()
    }
    // 说明不是空壳：kb2（Enter 打开）与快捷键页的 Ctrl+G 新文案都在
    expect(screen.getByText(t('kb2'))).toBeTruthy()
    expect(screen.getByText(t('sc_g'))).toBeTruthy()

    // 默认双击 Ctrl：唤起那一行的 kbd 就是它
    expect(screen.getAllByText(t('hotkey_opt_double_ctrl')).length).toBeGreaterThanOrEqual(2)

    // 切到 Alt+Space：键盘提示（第一条 kbd）应该跟着变，而不是仍显示 Ctrl Ctrl
    fireEvent.click(screen.getByText(t('hotkey_opt_alt_space')))
    expect(screen.getAllByText(t('hotkey_opt_alt_space')).length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText('Ctrl Ctrl')).toBeNull()
  })

  /* 第 301 项：用户「取消单独的关于页面，把关于弄进设置里面」。
     两件事要一起成立：① 设置里点「关于」能切到这块内容（版本/授权胶囊/赞助入口都在）；
     ② 页面上没有独立 dialog 弹窗了（以前是 520 宽的 Modal）。 */
  it('关于并入设置：切到关于分区能看到内容，且没有独立弹窗（第 301 项）', async () => {
    mockAboutWithLicense(FREE_STATE)
    render(<SettingsDrawer {...drawerProps} />)
    await screen.findByText(t('nav_general'))

    // 侧栏底部「关于」是切换分区，不是关掉设置去弹窗
    fireEvent.click(screen.getByRole('button', { name: t('about') }))
    expect((await screen.findAllByText(t('about_title'))).length).toBeGreaterThan(0)
    expect(await screen.findByText(t('lic_free'))).toBeTruthy()
    expect(screen.getByText(t('sponsor'))).toBeTruthy()
    // 仍在设置页里（左侧导航还在），而不是另起一个窗口
    expect(screen.getByText(t('nav_general'))).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('从托盘/页脚进来时直接落在关于分区（startSec，第 301 项）', async () => {
    mockAboutWithLicense(PRO_STATE)
    render(<SettingsDrawer {...drawerProps} startSec="about" />)
    expect((await screen.findAllByText(t('about_title'))).length).toBeGreaterThan(0)
    expect(await screen.findByText('Pro')).toBeTruthy()
  })

  /* 第 304 项：用户「这些字应该要打一些，不然都看不清」+「鼠标移到按钮上自动演示」。
     断言两件事：① 说明文字在（内容不再只是灰灰一小行靠 CSS 撑场面）；
     ② 演示图平时是静态封面，鼠标移上去才换成 GIF，移开又回到封面（不在后台空转）。 */
  it('网络搜索页有可读说明 + 悬停才播放演示 GIF（第 304 项）', async () => {
    mockSettingsFetch()
    render(<SettingsDrawer {...drawerProps} />)
    await screen.findByText(t('nav_general'))
    fireEvent.click(screen.getByRole('button', { name: t('web_search') }))

    expect(await screen.findByText(t('web_search_hint'))).toBeTruthy()
    const demo = screen.getByRole('img', { name: t('web_demo_alt') })
    expect(demo.getAttribute('data-act')).toBe('websearch-demo')
    // 默认是静态封面
    expect(document.querySelector('img[src="/web/websearch-demo.png"]')).toBeTruthy()
    expect(document.querySelector('img[src="/web/websearch-demo.gif"]')).toBeNull()
    // 移上去 → GIF（从第一帧开始播）
    fireEvent.mouseEnter(demo)
    await waitFor(() => expect(document.querySelector('img[src="/web/websearch-demo.gif"]')).toBeTruthy())
    // 移开 → 回到封面
    fireEvent.mouseLeave(demo)
    await waitFor(() => expect(document.querySelector('img[src="/web/websearch-demo.gif"]')).toBeNull())
  })

  it('弹窗样式只剩方案1：固定展示，没有可切换的下拉框', async () => {
    mockSettingsFetch()
    render(<SettingsDrawer {...drawerProps} />)
    await screen.findByText(t('nav_general'))
    fireEvent.click(screen.getByText(t('nav_mini')))
    expect(await screen.findByText(t('qs_style'))).toBeTruthy()
    expect(screen.getByText(t('qs_style_1'))).toBeTruthy()
    expect(screen.queryByText('方案2 · 紧凑表（像 Listary）')).toBeNull()
    const sels = document.querySelectorAll('select')
    expect(sels.length).toBe(1)
    expect(sels[0].closest('div')?.textContent || '').toContain(
      t('qs_home_count').replace('{n}', '').slice(0, 4),
    )
    expect(sels[0].querySelectorAll('option').length).toBe(10)
  })
})
