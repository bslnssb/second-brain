/**
 * api.ts 的请求契约单元测试。
 *
 * 重点是第 105 项那条静默链：所有请求都必须检查 r.ok，
 * 500/403 不许被当成「空配置」悄悄吞掉。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { checkUpdate, dialogJump, getOpenDirs, getSettings, openUrl, saveConfig, saveSettings } from './api'

function reply(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as unknown as Response
}

describe('api.ts', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('非 2xx 时抛错，不把 500 当成空配置', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({}, false, 500)))
    await expect(getSettings()).rejects.toThrow('/api/settings 500')
  })

  it('正常响应按 JSON 解析', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ hotkey: false, theme: 'dark' })))
    await expect(getSettings()).resolves.toEqual({ hotkey: false, theme: 'dark' })
  })

  it('saveSettings 发 POST 且 body 与 Content-Type 正确', async () => {
    const fn = vi.fn(async () => reply({ ok: true }))
    vi.stubGlobal('fetch', fn)
    await saveSettings({ theme: 'dark' })
    expect(fn).toHaveBeenCalledTimes(1)
    const [, init] = fn.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ theme: 'dark' })
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('saveConfig 把索引范围写进 body', async () => {
    const fn = vi.fn(async () => reply({ ok: true, dropped: [] }))
    vi.stubGlobal('fetch', fn)
    await saveConfig(['D:/docs'], ['D:/tmp'])
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/api/config')
    expect(JSON.parse(String(init.body))).toEqual({
      include_dirs: ['D:/docs'],
      exclude_dirs: ['D:/tmp'],
    })
  })

  it('openUrl 只在后端明确 ok 时返回 true', async () => {
    const fn = vi.fn(async () => reply({ ok: false }))
    vi.stubGlobal('fetch', fn)
    await expect(openUrl('https://evil.example.com')).resolves.toBe(false)
    vi.stubGlobal('fetch', vi.fn(async () => reply({ ok: true })))
    await expect(openUrl('https://github.com/bslnssb/second-brain')).resolves.toBe(true)
  })

  it('dialogJump 发 POST 且把路径放进 body（第 194 项）', async () => {
    const fn = vi.fn(async () => reply({ ok: true, path: "F:/x" }))
    vi.stubGlobal('fetch', fn)
    await expect(dialogJump('F:/x/a.docx')).resolves.toEqual({ ok: true, path: 'F:/x' })
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/api/dialog_jump')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ p: 'F:/x/a.docx' })
  })

  it('getOpenDirs 解析候选目录与对话框状态', async () => {
    const d = { items: [{ p: 'F:/x', n: 'x', k: 'open' }], dialog: true, title: '另存为' }
    vi.stubGlobal('fetch', vi.fn(async () => reply(d)))
    await expect(getOpenDirs()).resolves.toEqual(d)
  })

  it('检查更新断网时降级成 {ok:false}，不抛异常', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
    const r = await checkUpdate()
    expect(r.ok).toBe(false)
    expect(r.err).toContain('offline')
  })
})
