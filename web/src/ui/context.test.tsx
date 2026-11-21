import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ContextMenu, type MenuItem } from './context'

describe('ContextMenu', () => {
  it('renders menu items and displays shortcut hint', () => {
    const onAction = vi.fn()
    const onClose = vi.fn()
    const items: MenuItem[] = [
      { label: '打开', onClick: onAction },
      { label: '在文件夹里定位', hint: 'Ctrl+Enter', onClick: onAction },
      { label: '删除', danger: true, onClick: onAction },
    ]

    render(<ContextMenu x={100} y={100} items={items} onClose={onClose} />)

    expect(screen.getByText('打开')).toBeDefined()
    expect(screen.getByText('在文件夹里定位')).toBeDefined()
    expect(screen.getByText('Ctrl+Enter')).toBeDefined()

    const dangerBtn = screen.getByText('删除').closest('button')
    expect(dangerBtn?.className).toContain('text-red-500')
  })

  it('clicking an item calls its onClick and closes the menu', () => {
    const onAction = vi.fn()
    const onClose = vi.fn()
    const items: MenuItem[] = [{ label: '复制文件名', onClick: onAction }]

    render(<ContextMenu x={50} y={50} items={items} onClose={onClose} />)

    fireEvent.click(screen.getByText('复制文件名'))
    expect(onAction).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('pressing Escape closes the menu', () => {
    const onClose = vi.fn()
    const items: MenuItem[] = [{ label: '测试', onClick: vi.fn() }]

    render(<ContextMenu x={50} y={50} items={items} onClose={onClose} />)

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
