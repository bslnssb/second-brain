/** 第 270 项：网络搜索「恢复默认引擎」的纯逻辑。 */
import { describe, expect, it } from 'vitest'
import { missingDefaultEngines, restoreDefaultEngines, type SearchEngine } from './websearch'

const G: SearchEngine = { key: 'g', title: 'Google', url: 'https://www.google.com/search?q={query}' }
const B: SearchEngine = { key: 'b', title: '百度', url: 'https://www.baidu.com/s?wd={query}' }
const BING: SearchEngine = { key: 'bing', title: 'Bing', url: 'https://www.bing.com/search?q={query}' }
const DEF = [G, B, BING]

describe('missingDefaultEngines', () => {
  it('用户删掉 bing 后，只报 bing 一个缺失', () => {
    expect(missingDefaultEngines([G, B], DEF).map((e) => e.key)).toEqual(['bing'])
  })

  it('都在就不报缺失', () => {
    expect(missingDefaultEngines(DEF, DEF)).toEqual([])
  })

  it('判重忽略大小写和首尾空格（老配置里有 "G " 这种脏数据）', () => {
    const dirty = [{ ...G, key: ' G ' }, { ...B, key: 'B' }]
    expect(missingDefaultEngines(dirty, DEF).map((e) => e.key)).toEqual(['bing'])
  })

  it('空行（用户点过「添加引擎」还没填）不影响判重', () => {
    const blank: SearchEngine = { key: '', title: '', url: '' }
    expect(missingDefaultEngines([blank, G, B], DEF).map((e) => e.key)).toEqual(['bing'])
  })

  it('后端没下发默认表时安静返回空，不报错', () => {
    expect(missingDefaultEngines([G], undefined)).toEqual([])
  })
})

describe('restoreDefaultEngines', () => {
  it('补回到末尾，且补回来的默认是启用状态', () => {
    const r = restoreDefaultEngines([G, B], DEF)
    expect(r.added).toBe(1)
    expect(r.list.map((e) => e.key)).toEqual(['g', 'b', 'bing'])
    expect(r.list[2].enabled).toBe(true)
    expect(r.list[2].title).toBe('Bing')
  })

  it('用户自己加的引擎原样保留，改过的标题不覆盖', () => {
    const mine: SearchEngine = { key: 'ddg', title: 'DuckDuckGo', url: 'https://duckduckgo.com/?q={query}' }
    const renamed: SearchEngine = { ...G, title: '谷歌（我的叫法）', enabled: false }
    const r = restoreDefaultEngines([renamed, mine], DEF)
    expect(r.list.map((e) => e.key)).toEqual(['g', 'ddg', 'b', 'bing'])
    expect(r.list[0].title).toBe('谷歌（我的叫法）')
    expect(r.list[0].enabled).toBe(false)
    expect(r.list[1].key).toBe('ddg')
  })

  it('没缺的就原样返回（added=0，调用方不必保存）', () => {
    const list = [G, B, BING]
    const r = restoreDefaultEngines(list, DEF)
    expect(r.added).toBe(0)
    expect(r.list).toBe(list)
  })

  it('不改动传入的默认表本身（后端下发的是共享对象）', () => {
    restoreDefaultEngines([G], DEF)
    expect(DEF.map((e) => e.key)).toEqual(['g', 'b', 'bing'])
  })
})
