/**
 * 第 312 项：详情面板「看图 / 看正文」两套预览。
 *
 * 用户原话「在查看图片，pdf，word，excel 的时候，'详情'应该能够显示出来」。
 * 图片/PDF → 预览图；Word/Excel/PPT → 进面板自动拉正文首段（本机 WPS
 * 没注册 Shell 缩略图处理器，出图只会是空白破图）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { Preview } from './panels'

const noop = () => {}

/** 第 327 项补测修：详情面板里第一个带 title 的按钮已经不是缩略图了（第 325 项把
 *  「展开」也做成了带 title 的按钮，排在前面），所以按「包着 img 的那个按钮」取。 */
function thumbButton(container: HTMLElement) {
  return (container.querySelector('img')?.closest('button') || null) as HTMLButtonElement | null
}

function draw(p: string, e: string, t = 'file') {
  return render(
    <Preview row={{ p, e, t }} q="" onClose={noop} onOpen={noop} onReveal={noop} onCopy={noop} />,
  )
}

describe('Preview 详情面板的预览（第 312 项）', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('图片行渲染 /api/thumb 预览图', () => {
    const { container } = draw('D:\\pics\\a.JPG', 'JPG')
    const img = container.querySelector('img')
    expect(img).toBeTruthy()
    expect(img!.getAttribute('src')).toContain('/api/thumb?p=')
  })

  it('第 317 项：详情栏与灯箱都取大图（big=1），否则 PDF 首页看不清', () => {
    const { container } = draw('D:\\doc\\合同.PDF', 'PDF')
    const img = container.querySelector('img') as HTMLImageElement
    expect(img.getAttribute('src')).toContain('&big=1')
    fireEvent.click(thumbButton(container) as HTMLButtonElement)
    const dlg = container.querySelector('[role="dialog"]') as HTMLElement
    expect(dlg).toBeTruthy()
    const big = dlg.querySelector('img') as HTMLImageElement
    expect(big.getAttribute('src')).toContain('&big=1')
    // 灯箱不再压高度（压了就又缩成邮票）
    expect(big.className).not.toContain('max-h-')
  })

  it('PDF 行渲染预览图（取首页）', () => {
    const { container } = draw('D:\\doc\\合同.PDF', 'PDF')
    expect(container.querySelector('img')).toBeTruthy()
  })

  it('Word / Excel / PPT 不画图，改成自动读正文首段', async () => {
    const fn = vi.fn(async (_input: RequestInfo | URL) => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, text: '第一段正文……', matched: false }),
    }))
    vi.stubGlobal('fetch', fn)
    const { container } = draw('D:\\doc\\方案.docx', 'docx')
    expect(container.querySelector('img')).toBeNull()
    await waitFor(() => expect(container.textContent).toContain('第一段正文'))
    expect(fn.mock.calls[0][0]).toContain('/api/peek')
  })

  it('exe / 音频 / 文件夹：既不画图也不自动读正文', () => {
    for (const [p, e, t] of [
      ['D:\\a.exe', 'exe', 'file'],
      ['D:\\a.mp3', 'mp3', 'file'],
      ['D:\\dir', '', 'dir'],
    ] as const) {
      cleanup()
      const { container } = draw(p, e, t)
      expect(container.querySelector('img'), p).toBeNull()
    }
  })

  it('图片取不到（onError）后整块隐藏，不留空框', async () => {
    const { container } = draw('D:\\pics\\a.png', 'png')
    const img = container.querySelector('img') as HTMLImageElement
    expect(img).toBeTruthy()
    img.dispatchEvent(new Event('error'))
    await waitFor(() => expect(container.querySelector('button[title] img')).toBeNull())
  })

  it('点缩略图只放大（灯箱），不启动外部程序（第 314 项）', async () => {
    const onOpen = vi.fn()
    const { container } = render(
      <Preview
        row={{ p: 'D:\\pics\\a.png', e: 'png', t: 'file' }}
        q=""
        onClose={noop}
        onOpen={onOpen}
        onReveal={noop}
        onCopy={noop}
      />,
    )
    const thumbBtn = thumbButton(container) as HTMLButtonElement
    expect(thumbBtn).toBeTruthy()
    fireEvent.click(thumbBtn)
    await waitFor(() => expect(container.querySelector('[role="dialog"]')).toBeTruthy())
    expect(onOpen).not.toHaveBeenCalled()
    // 点灯箱空白关掉
    fireEvent.click(container.querySelector('[role="dialog"]')!)
    await waitFor(() => expect(container.querySelector('[role="dialog"]')).toBeNull())
  })

  it('第 332 项：PDF 灯箱里 → 先翻页，翻到最后一页才换下一个文件', async () => {
    const onStep = vi.fn()
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ pages: 3 }),
    })))
    const { container } = render(
      <Preview
        row={{ p: 'D:\\doc\\手册.pdf', e: 'pdf', t: 'file' }}
        q=""
        onClose={noop}
        onOpen={noop}
        onReveal={noop}
        onCopy={noop}
        onStep={onStep}
      />,
    )
    fireEvent.click(thumbButton(container) as HTMLButtonElement)
    const dlg = () => container.querySelector('[role="dialog"]') as HTMLElement
    await waitFor(() => expect(dlg()).toBeTruthy())
    // 等 /api/pdfmeta 回来，才有「3 页」可翻
    await waitFor(() => expect(dlg().querySelector('img')!.getAttribute('src')).toContain('pg=1'))

    const img = () => dlg().querySelector('img') as HTMLImageElement
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    await waitFor(() => expect(img().getAttribute('src')).toContain('pg=2'))
    expect(onStep).not.toHaveBeenCalled()

    fireEvent.keyDown(window, { key: 'ArrowRight' })
    await waitFor(() => expect(img().getAttribute('src')).toContain('pg=3'))
    expect(onStep).not.toHaveBeenCalled()

    // 已经是最后一页 → 这一次才换文件
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    await waitFor(() => expect(onStep).toHaveBeenCalledWith(1))
  })

  it('第 332 项：PDF 在第一页按 ← 才换上一个文件', async () => {
    const onStep = vi.fn()
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ pages: 4 }),
    })))
    const { container } = render(
      <Preview
        row={{ p: 'D:\\doc\\手册.pdf', e: 'pdf', t: 'file' }}
        q=""
        onClose={noop}
        onOpen={noop}
        onReveal={noop}
        onCopy={noop}
        onStep={onStep}
      />,
    )
    fireEvent.click(thumbButton(container) as HTMLButtonElement)
    await waitFor(() => expect(container.querySelector('[role="dialog"]')).toBeTruthy())
    await waitFor(() => expect(
      (container.querySelector('[role="dialog"] img') as HTMLImageElement).getAttribute('src'),
    ).toContain('pg=1'))

    fireEvent.keyDown(window, { key: 'ArrowRight' })
    await waitFor(() => expect(
      (container.querySelector('[role="dialog"] img') as HTMLImageElement).getAttribute('src'),
    ).toContain('pg=2'))

    // 退回第 1 页
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    await waitFor(() => expect(
      (container.querySelector('[role="dialog"] img') as HTMLImageElement).getAttribute('src'),
    ).toContain('pg=1'))
    expect(onStep).not.toHaveBeenCalled()

    // 第 1 页再按 ← → 换上一个文件
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    await waitFor(() => expect(onStep).toHaveBeenCalledWith(-1))
  })
})
