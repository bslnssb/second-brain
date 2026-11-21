import { describe, it, expect, beforeEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { useColResize } from './useColResize'

const DEF = { name: 220, type: 96 }
const MIN = { name: 80, type: 48 }

function Harness({ defaults = DEF }: { defaults?: Record<string, number> }) {
  const { w, handle } = useColResize('sb_cols_test', defaults, MIN)
  return (
    <div>
      <div data-testid="name" style={{ width: w.name }}>name {w.name}</div>
      <div data-testid="type" style={{ width: w.type }}>type {w.type}</div>
      {/* 与真实用法一致：handle 直接返回带 onMouseDown 的 <span> */}
      {handle('name', 'hint')}
      {handle('type', 'hint')}
    </div>
  )
}

describe('useColResize 共享列宽拖拽逻辑', () => {
  beforeEach(() => {
    cleanup()            // 全量跑时 DOM 会跨文件累积，不清理会撞「找到多个元素」
    localStorage.clear()
  })

  it('拖「名称」手柄右移 60px → 存下 name 280', () => {
    const { container } = render(<Harness />)
    const handles = container.querySelectorAll('[class*="cursor-col-resize"]')
    const nameHandle = handles[0] as HTMLElement
    fireEvent.mouseDown(nameHandle, { clientX: 300 })
    fireEvent.mouseMove(document, { clientX: 360 })
    fireEvent.mouseUp(document)
    const saved = JSON.parse(localStorage.getItem('sb_cols_test') || '{}')
    expect(saved.w.name).toBe(280)
  })

  it('拖「类型」手柄右移 40px → 存下 type 136', () => {
    const { container } = render(<Harness />)
    const handles = container.querySelectorAll('[class*="cursor-col-resize"]')
    const typeHandle = handles[1] as HTMLElement
    fireEvent.mouseDown(typeHandle, { clientX: 200 })
    fireEvent.mouseMove(document, { clientX: 240 })
    fireEvent.mouseUp(document)
    const saved = JSON.parse(localStorage.getItem('sb_cols_test') || '{}')
    expect(saved.w.type).toBe(136)
  })

  it('默认值改过的那一列，丢弃旧宽度用新默认（2026-11-15 大小列 56→48 对老用户无效）', () => {
    // 老格式：只有宽度 map，没有默认值快照
    localStorage.setItem('sb_cols_test', JSON.stringify({ name: 300, type: 120 }))
    const { getByTestId } = render(<Harness />)
    // 没有 d 快照时无法判断，按「默认值已变」处理 → 全部回到新默认
    expect(getByTestId('name').textContent).toContain('220')
    expect(getByTestId('type').textContent).toContain('96')
  })

  it('默认值没变的列，继续沿用用户拖过的宽度', () => {
    localStorage.setItem(
      'sb_cols_test',
      JSON.stringify({ w: { name: 300, type: 120 }, d: { name: 220, type: 96 } }),
    )
    const { getByTestId } = render(<Harness />)
    expect(getByTestId('name').textContent).toContain('300')
    expect(getByTestId('type').textContent).toContain('120')
  })

  it('只有默认值变过的那列重置，其余保留', () => {
    // 用户拖过两列；之后 type 的默认值从 96 改成了 120
    localStorage.setItem(
      'sb_cols_test',
      JSON.stringify({ w: { name: 300, type: 150 }, d: { name: 220, type: 96 } }),
    )
    const { getByTestId } = render(<Harness defaults={{ name: 220, type: 120 }} />)
    expect(getByTestId('name').textContent).toContain('300') // 默认没变 → 保留
    expect(getByTestId('type').textContent).toContain('120') // 默认变了 → 用新默认
  })
})
