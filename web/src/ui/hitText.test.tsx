/**
 * 第 395 项：表格/幻灯片预览里的关键词高亮。
 *
 * 用户截图：搜「深信服」命中一份 xlsx，右侧「表格预览」里全是要找的词，
 * 但只有下面那段「命中的正文」标红了 —— 用户问「为什么不能直接在表格中标注出来」。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { HiText } from './panels'

describe('HiText 单元格高亮（第 395 项）', () => {
  afterEach(cleanup)

  it('把命中的词标成 mark', () => {
    const { container } = render(<HiText text="深信服产品" q="深信服" />)
    const marks = container.querySelectorAll('mark')
    expect(marks.length).toBe(1)
    expect(marks[0].textContent).toBe('深信服')
    // 前后文要保留
    expect(container.textContent).toBe('深信服产品')
  })

  it('大小写不敏感（AD / admin 这类）', () => {
    const { container } = render(<HiText text="Admin" q="admin" />)
    expect(container.querySelectorAll('mark').length).toBe(1)
    expect(container.querySelector('mark')!.textContent).toBe('Admin')
  })

  it('多个词分别标、且不把标点当成正则', () => {
    const { container } = render(<HiText text="默认账号 / sangfor@2018" q="账号 sangfor@2018" />)
    expect(container.querySelectorAll('mark').length).toBe(2)
    expect(container.textContent).toBe('默认账号 / sangfor@2018')
  })

  it('ext:/size: 这类语法项不参与高亮', () => {
    const { container } = render(<HiText text="等工作表 Sheet1" q="ext:xlsx 工作表" />)
    expect(container.querySelectorAll('mark').length).toBe(1)
    expect(container.querySelector('mark')!.textContent).toBe('工作表')
  })

  it('没查询词就原样输出', () => {
    const { container } = render(<HiText text="第三方" q="" />)
    expect(container.querySelectorAll('mark').length).toBe(0)
    expect(container.textContent).toBe('第三方')
  })

  it('表格里用加强档（小字号才看得见）', () => {
    const { container } = render(<HiText text="深信服VPN" q="深信服" strong />)
    const m = container.querySelector('mark')!
    expect(m.className).toContain('hl-strong')
  })
})
