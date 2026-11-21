/**
 * 第 295 项：聚合翻译「本次未参与」要把**原因**说出来。
 *
 * 用户原话：「为什么翻译功能，我使用默认都只有 mymemory 在翻译出来啊，
 * 其他的都没看到。但我明明看了好多个」——
 * 后端一直有 reason 字段，界面却只印引擎名，所以看不出是被省掉了还是坏了。
 * 这里钉死：①原因按 code 翻成界面语言 ②当前代理写出来 ③切英文界面不露中文。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setLang, t } from '../lib/i18n'
import { TranslateSection } from './panels'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const baseProps = {
  targetLang: 'auto',
  onTargetLang: vi.fn(),
  live: true,
  onLive: vi.fn(),
  engines: [] as string[],
  onEngines: vi.fn(),
}

function stubTranslate(payload: unknown) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/byok') return { json: async () => ({ ok: true, masked: {} }) }
    return { json: async () => payload }
  }))
}

async function runOnce(text: string, expectText: RegExp) {
  render(<TranslateSection {...baseProps} />)
  fireEvent.change(screen.getByPlaceholderText(t('trans_test_placeholder')), {
    target: { value: text },
  })
  fireEvent.click(screen.getByRole('button', { name: new RegExp(t('trans_btn_run')) }))
  await waitFor(() => expect(screen.getByText(expectText)).toBeTruthy())
}

describe('TranslateSection 未参与原因', () => {
  it('中文界面：引擎名后面跟原因，并写出当前代理', async () => {
    stubTranslate({
      ok: true,
      proxy: 'http://127.0.0.1:7897',
      results: [{ engine: 'MyMemory 翻译', engine_id: 'mymemory', text: '会议改到明天下午三点。' }],
      skipped: [
        { engine: 'youdao', reason: '只用于词语', code: 'dict_only' },
        { engine: 'google', reason: '本机连不上（网络/代理）', code: 'offline' },
      ],
    })
    setLang('zh')
    try {
      await runOnce('The meeting has been moved to three tomorrow.', /会议改到明天下午三点。/)
      expect(screen.getByText(/只用于词语/)).toBeTruthy()
      expect(screen.getByText(/本机连不上/)).toBeTruthy()
      expect(screen.getByText(/127\.0\.0\.1:7897/)).toBeTruthy()
    } finally {
      setLang('zh')
    }
  })

  it('英文界面不露中文：原因走 code 翻成英文', async () => {
    stubTranslate({
      ok: true,
      proxy: '',
      results: [{ engine: 'MyMemory 翻译', engine_id: 'mymemory', text: 'Meeting moved.' }],
      skipped: [{ engine: 'youdao', reason: '只用于词语', code: 'dict_only' }],
    })
    setLang('en')
    try {
      await runOnce('The meeting has been moved.', /Meeting moved\./)
      expect(screen.queryByText(/只用于词语/)).toBeNull()
      expect(screen.getByText(/words only/i)).toBeTruthy()
      expect(screen.getByText(t('trans_proxy_off'))).toBeTruthy()
    } finally {
      setLang('zh')
    }
  })
})
