/**
 * 第 268 项：聚合翻译的逐引擎开关。
 *
 * 用户原话：「聚合翻译这里应该要能支持关闭某些翻译，可以自由开启和关闭的，
 * 不然一次请求就要向那么多去请求，延迟会变大。」
 *
 * 钉死的三件事：
 * 1. 缺省（trans_engines 为空 / 老配置没有这个键）= 五个全开，行为和以前一致；
 * 2. 点一下就真的从列表里去掉，交给 patch({trans_engines}) 落盘；
 * 3. 只剩最后一个时点不动 —— 否则前端交个空数组给后端，后端会归回全开，
 *    用户会看到「我全关了它又全开了」。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setLang, t } from '../lib/i18n'
import { TranslateSection } from './panels'

// 本仓库没有开 vitest 的 globals，@testing-library 的自动 cleanup 不会挂上；
// 不手动清，上一个用例的 DOM 会留在 body 里，getByRole 直接报 "Found multiple elements"。
afterEach(cleanup)

const baseProps = {
  targetLang: 'auto',
  onTargetLang: vi.fn(),
  live: true,
  onLive: vi.fn(),
}

function engineButton(name: string, state: 'on' | 'off') {
  return screen.getByRole('button', { name: `${name} ${state}` })
}

describe('TranslateSection 引擎开关', () => {
  it('缺省时只开前四个引擎（第 346 项）', () => {
    render(<TranslateSection {...baseProps} engines={[]} onEngines={vi.fn()} />)
    // 默认开：Google / MyMemory / 腾讯 / 有道
    for (const key of ['te_google', 'te_mymemory', 'te_tencent', 'te_youdao']) {
      expect(engineButton(t(key), 'on')).toBeTruthy()
    }
    // 默认关：后面这几个（没填密钥的自带密钥引擎 + 两家词典）
    for (const key of ['te_baidu', 'te_iciba', 'te_deepseek', 'te_deepl', 'te_openai']) {
      expect(engineButton(t(key), 'off')).toBeTruthy()
    }
  })

  it('引擎名跟着界面语言变（第 278 项）', () => {
    // 第 278 项之前这里是写死的中文，切到印地/英文界面还是中文
    setLang('hi')
    try {
      render(<TranslateSection {...baseProps} engines={['google']} onEngines={vi.fn()} />)
      expect(engineButton('Google अनुवाद', 'on')).toBeTruthy()
      expect(screen.queryByText('Google 翻译')).toBeNull()
      expect(screen.queryByText('自带密钥')).toBeNull()   // 自带密钥的说明也本地化了
    } finally {
      setLang('zh')
    }
  })

  it('点掉 Google 就只交剩下四个', () => {
    const onEngines = vi.fn()
    render(
      <TranslateSection {...baseProps} engines={['google', 'mymemory', 'youdao', 'baidu', 'iciba']} onEngines={onEngines} />
    )
    fireEvent.click(engineButton(t('te_google'), 'on'))
    expect(onEngines).toHaveBeenCalledTimes(1)
    expect(onEngines.mock.calls[0][0]).toEqual(['mymemory', 'youdao', 'baidu', 'iciba'])
  })

  it('关掉的引擎会显示成 off，再点能开回来', () => {
    const onEngines = vi.fn()
    render(<TranslateSection {...baseProps} engines={['youdao']} onEngines={onEngines} />)
    expect(engineButton(t('te_google'), 'off')).toBeTruthy()
    fireEvent.click(engineButton(t('te_google'), 'off'))
    // 开关是「保序追加」：已有 youdao，再开 google 就排到后面
    expect(onEngines.mock.calls[0][0]).toEqual(['youdao', 'google'])
  })

  it('只剩一个时点不动，避免后端把空列表归回全开', () => {
    const onEngines = vi.fn()
    render(<TranslateSection {...baseProps} engines={['iciba']} onEngines={onEngines} />)
    const last = engineButton(t('te_iciba'), 'on')
    expect((last as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(last)
    expect(onEngines).not.toHaveBeenCalled()
  })

  it('老配置里出现不认识的引擎 id 不会渲染出来', () => {
    render(<TranslateSection {...baseProps} engines={['google', 'legacy-engine']} onEngines={vi.fn()} />)
    expect(engineButton(t('te_google'), 'on')).toBeTruthy()
    expect(engineButton(t('te_mymemory'), 'off')).toBeTruthy()
    expect(screen.queryByText(/legacy-engine/)).toBeNull()
  })
})
