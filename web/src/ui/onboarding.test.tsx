/**
 * 欢迎页状态机的单元测试（交接文档第 105 项 P0/P1 的回归锁）。
 *
 * 完全 mock fetch：不连后端、不碰真实 settings.json，只验证「接口异常时
 * 界面怎么表现」——这正是以前每次重启都弹欢迎页、且界面零报错的根因。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { t } from '../lib/i18n'
import { Onboarding } from './onboarding'

function reply(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as unknown as Response
}

type FetchOpts = {
  firstrun?: unknown[]
  settings?: unknown
  postSettings?: (body: Record<string, unknown>) => Response
}

/** 按 URL / method 分发假响应，并记录所有写向 /api/settings 的请求体 */
function installFetch(opts: FetchOpts = {}) {
  const posts: Record<string, unknown>[] = []
  const queue = [...(opts.firstrun ?? [{ need: false }])]
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.includes('/api/settings') && method === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
      posts.push(body)
      return opts.postSettings ? opts.postSettings(body) : reply({ ok: true })
    }
    if (url.includes('/api/settings')) {
      return reply(opts.settings ?? { hotkey: true, theme: 'auto' })
    }
    if (url.includes('/api/firstrun') && method === 'GET') {
      const next = queue.length > 1 ? queue.shift() : queue[0]
      return reply(next ?? { need: false })
    }
    return reply({ ok: true })
  })
  vi.stubGlobal('fetch', fn)
  return { posts, fn }
}

const noop = () => {}
const renderOb = () => render(<Onboarding onOpenSettings={noop} onIndexed={noop} />)

describe('Onboarding', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('配置接口 403 时不弹欢迎页（宁可不显示，也不静默骚扰用户）', async () => {
    const fn = vi.fn(async () => reply({}, false, 403))
    vi.stubGlobal('fetch', fn)
    renderOb()
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(console.warn).toHaveBeenCalled())
    expect(screen.queryByText(t('ob_welcome'))).toBeNull()
  })

  it('markSeen 写失败时不关界面，并给出可见提示', async () => {
    const { posts } = installFetch({
      settings: { hotkey: true, theme: 'auto' },
      postSettings: () => reply({}, false, 500),
    })
    renderOb()
    fireEvent.click(await screen.findByText(t('ob_start_search')))
    await waitFor(() => expect(screen.getByText(t('ob_mark_err'))).toBeTruthy())
    // 第 208 项改名的副产物：以前 ob_tour_t1 英文与 ob_welcome 恰好同串，
    // 这条断言碰巧能匹配到教程页标题；现在分页标题是 tagline，改断言到真正的可见元素
    expect(screen.getByText(t('ob_tour_t1'))).toBeTruthy()
    expect(posts).toContainEqual({ welcome_seen: true })
  })

  it('建库失败显示独立错误卡，关闭时不写 welcome_seen', async () => {
    const { posts } = installFetch({
      firstrun: [{ need: true }, { state: 'error' }],
      settings: { hotkey: true, theme: 'auto' },
    })
    renderOb()
    expect(await screen.findByText(t('ob_err_title'))).toBeTruthy()
    fireEvent.click(screen.getByText(t('ob_start_search')))
    await waitFor(() => expect(screen.queryByText(t('ob_err_title'))).toBeNull())
    expect(posts).toEqual([])
  })

  it('建库小卡点 ✕ 只收起卡片，不把用户标成「看过欢迎页」', async () => {
    const { posts } = installFetch({
      firstrun: [{ need: true }],
      settings: { hotkey: true, theme: 'auto' },
    })
    renderOb()
    expect(await screen.findByText(t('ob_running_badge'))).toBeTruthy()
    fireEvent.click(screen.getByTitle(t('ob_skip')))
    await waitFor(() => expect(screen.queryByText(t('ob_running_badge'))).toBeNull())
    expect(posts).toEqual([])
  })

  it('服务端 welcome_seen=true 时不弹（跨重启的权威标记）', async () => {
    const { fn } = installFetch({ settings: { welcome_seen: true } })
    renderOb()
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(t('ob_welcome'))).toBeNull()
  })

  it('localStorage 兜底也能挡住重复弹窗', async () => {
    localStorage.setItem('sb_welcome_seen', '1')
    const { fn } = installFetch({ settings: { hotkey: true, theme: 'auto' } })
    renderOb()
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(t('ob_welcome'))).toBeNull()
  })

  it('老用户 firstrun_done 不因换了实现被再骚扰一次', async () => {
    const { fn } = installFetch({ settings: { firstrun_done: 'auto' } })
    renderOb()
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(t('ob_welcome'))).toBeNull()
  })

  it('点「直接开始」等标记落盘成功后才隐藏', async () => {
    const { posts } = installFetch({ settings: { hotkey: true, theme: 'auto' } })
    renderOb()
    fireEvent.click(await screen.findByText(t('ob_start_search')))
    await waitFor(() => expect(screen.queryByText(t('ob_welcome'))).toBeNull())
    expect(posts).toContainEqual({ welcome_seen: true })
  })

  it('建库小卡显示真实 ETA（不再写死 18%/65% 假进度）', async () => {
    installFetch({
      firstrun: [{ need: true, state: 'busy', stage: 'names', files: 1200, eta: 9, total: 0 }],
      settings: { hotkey: true, theme: 'auto' },
    })
    renderOb()
    expect(await screen.findByText(t('ob_running_badge'))).toBeTruthy()
    // ETA 文案按 i18n 渲染（eta_s：'预计还需约 {s} 秒'）
    expect(screen.getByText(t('eta_s').replace('{s}', '9'))).toBeTruthy()
  })

  it('有 total 时进度条按 n/total 走真百分比', async () => {
    installFetch({
      firstrun: [{ need: true, state: 'busy', stage: 'content', n: 50, files: 0, eta: 0, total: 200 }],
      settings: { hotkey: true, theme: 'auto' },
    })
    renderOb()
    expect(await screen.findByText(t('ob_running_badge'))).toBeTruthy()
    // 进度条容器内不该再有无定形动画条（状态点上的 animate-pulse 不算）
    const bar = document.querySelector('.h-1.overflow-hidden .animate-pulse')
    expect(bar).toBeNull()
  })

  /** 第 346 项回归锁：建库完成 ≠ 教程可以自己关掉。
   *  上一版在 done 阶段挂 2.4s 定时器 → 用户「还没点教程就没了」「刚点下一步就没了」。 */
  it('建库完成后教程不会自己消失，且空库时给出「选索引文件夹」入口', async () => {
    installFetch({
      firstrun: [
        { need: true },
        { need: false, state: 'busy', stage: 'names', files: 0 },
        { need: false, state: 'done', files: 0, docs: 0 },
      ],
      settings: { hotkey: true, theme: 'auto' },
    })
    renderOb()
    // 轮询命中 done 后应停在教程第 1 页
    expect(await screen.findByText(t('ob_tour_t1'), undefined, { timeout: 4000 })).toBeTruthy()
    // 老实现会在这之后 2.4s 内自动隐藏：等过一个轮询周期 + 余量，教程必须还在
    await new Promise((r) => setTimeout(r, 3600))
    expect(screen.queryByText(t('ob_tour_t1'))).not.toBeNull()
    expect(screen.getByText(t('ob_zero_hint'))).toBeTruthy()
    expect(screen.getByText(t('ob_pick_folder'))).toBeTruthy()
  })
})
