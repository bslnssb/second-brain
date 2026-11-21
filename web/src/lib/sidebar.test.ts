import { describe, expect, it } from 'vitest'
import { nextKind } from './sidebar'

describe('nextKind（第 331 项：点第二次收回）', () => {
  it('点已选中的类型 → 收回成「全部」', () => {
    expect(nextKind('doc', 'doc')).toBe('all')
    expect(nextKind('img', 'img')).toBe('all')
    expect(nextKind('dir', 'dir')).toBe('all')
  })

  it('点别的类型 → 换成它', () => {
    expect(nextKind('all', 'doc')).toBe('doc')
    expect(nextKind('doc', 'img')).toBe('img')
    expect(nextKind('img', 'dir')).toBe('dir')
  })

  it('「全部」自己再点还是「全部」，不会变成空筛选', () => {
    expect(nextKind('all', 'all')).toBe('all')
    expect(nextKind('doc', 'all')).toBe('all')
  })
})
