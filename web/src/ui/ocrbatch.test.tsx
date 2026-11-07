/** 批量 OCR 面板（Pro 2）的前端契约测试。
 *
 * 不测 OCR 本身，测三件容易悄悄坏掉的事：
 * 未授权时别把队列控件露出来、运行中进度与当前文件名真的显示、
 * 「加入当前结果」只把扫描件路径发给后端（别把 .exe / 文件夹塞进队列）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { t } from '../lib/i18n'
import { OcrBatchModal } from './ocrbatch'
import type { OcrBatchStatus } from '../api'

function reply(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

const RUNNING: OcrBatchStatus = {
  ok: true, run: 'running', total: 3, done: 1, failed: 0, skipped: 0,
  pending: 2, pct: 33, current: '合同第7页.pdf', current_path: 'D:/case/合同第7页.pdf',
  page_done: 4, page_total: 12,
  items: [
    { p: 'D:/case/a.pdf', st: 'done', chars: 120, pages: 10, err: '' },
    { p: 'D:/case/b.pdf', st: 'running', chars: 0, pages: 0, err: '' },
  ],
}

function setup(status: OcrBatchStatus, posted: unknown[], rows: { p: string }[] = []) {
  const fn = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'POST') {
      posted.push(JSON.parse(String(init.body)))
      return reply({ ...status, ok: true, added: 2 })
    }
    return reply(status)
  })
  vi.stubGlobal('fetch', fn)
  return render(
    <OcrBatchModal
      open
      onClose={() => {}}
      status={status}
      pro={false}
      rows={rows}
      onRefresh={() => {}}
      onOpenAbout={() => {}}
    />,
  )
}

describe('OcrBatchModal', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('未授权时只显示 Pro 说明和去激活按钮', () => {
    render(
      <OcrBatchModal open onClose={() => {}} status={null} pro rows={[]}
        onRefresh={() => {}} onOpenAbout={() => {}} />,
    )
    expect(screen.getByText(t('ocr_pro'))).toBeTruthy()
    expect(screen.getByText(t('ocr_go_license'))).toBeTruthy()
    expect(screen.queryByText(t('ocr_pick'))).toBeNull()
  })

  it('关着的时候不渲染', () => {
    const { container } = render(
      <OcrBatchModal open={false} onClose={() => {}} status={RUNNING} pro={false}
        rows={[]} onRefresh={() => {}} onOpenAbout={() => {}} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('运行中显示进度、当前文件和页码', () => {
    setup(RUNNING, [])
    expect(screen.getByText(t('ocr_state_run'))).toBeTruthy()
    expect(document.body.textContent).toContain('1 / 3')
    expect(document.body.textContent).toContain(t('ocr_current').replace('{p}', '合同第7页.pdf'))
    expect(document.body.textContent).toContain('4 / 12')
  })

  it('空队列给出引导文案，不画空列表', () => {
    setup({ ok: true, run: 'idle', total: 0, done: 0, failed: 0, skipped: 0,
      pending: 0, pct: 0, items: [] }, [])
    // 引导文案被 <br> 拆成两个文本节点，getByText 精确匹配不到（panels 测试踩过）
    expect(document.body.textContent).toContain(t('ocr_empty'))
    expect(document.body.textContent).toContain(t('ocr_ready'))
  })

  it('「加入当前结果」只把扫描件路径交给后端', async () => {
    const posted: unknown[] = []
    const { container } = setup(RUNNING, posted, [
      { p: 'D:/case/a.pdf' },
      { p: 'D:/case/photo.JPG' },
      { p: 'D:/case/note.docx' },
      { p: 'D:/case/app.exe' },
    ])
    expect(container.textContent).toContain('(2)')
    const btn = container.querySelector('[data-act="ocr-add-results"]') as HTMLElement
    fireEvent.click(btn)
    await vi.waitFor(() => expect(posted.length).toBe(1))
    expect(posted[0]).toEqual({
      op: 'add',
      paths: ['D:/case/a.pdf', 'D:/case/photo.JPG'],
    })
  })

  it('暂停按钮在运行时才出现，点它发 pause', async () => {
    const posted: unknown[] = []
    const { container } = setup(RUNNING, posted)
    const btn = container.querySelector('[data-act="ocr-pause"]') as HTMLElement
    expect(btn).toBeTruthy()
    fireEvent.click(btn)
    await vi.waitFor(() => expect(posted.length).toBe(1))
    expect(posted[0]).toEqual({ op: 'pause' })
  })

  it('暂停状态显示「继续」而不是「暂停」', () => {
    const { container } = setup({ ...RUNNING, run: 'paused' }, [])
    expect(container.querySelector('[data-act="ocr-pause"]')).toBeNull()
    expect(container.querySelector('[data-act="ocr-resume"]')).toBeTruthy()
  })

  it('失败项显示原因，成功项显示页数', () => {
    setup({
      ...RUNNING, run: 'done',
      items: [
        { p: 'D:/case/ok.pdf', st: 'done', chars: 99, pages: 8, err: '' },
        { p: 'D:/case/bad.pdf', st: 'failed', chars: 0, pages: 0, err: 'pymupdf 炸了' },
      ],
    }, [])
    expect(document.body.textContent).toContain('ok.pdf')
    expect(document.body.textContent).toContain('8p')
    expect(document.body.textContent).toContain('pymupdf 炸了')
  })
})
