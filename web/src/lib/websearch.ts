/** 第 270 项：网络搜索引擎的「恢复默认」逻辑。
 *
 *  用户在设置里点 × 删掉一条内置引擎（比如 bing）之后，以前没有任何路能补回来：
 *  只能自己点「添加引擎」，再手打 key / 标题 / 网址模板三栏 —— 普通用户不会这么干。
 *  这里只做一件纯函数的事：把**出厂默认里有、当前列表里没有**的条目按 key 补回末尾，
 *  用户自己加的条目、改过的标题/模板、勾选状态一律原样保留（不覆盖、不重排）。
 */

export type SearchEngine = { key: string; title: string; url: string; enabled?: boolean }

/** 当前列表里还缺哪几个出厂引擎（按 key 判重，忽略大小写与首尾空格） */
export function missingDefaultEngines(list: SearchEngine[], defaults?: SearchEngine[]): SearchEngine[] {
  const have = new Set((list || []).map((e) => (e?.key || '').trim().toLowerCase()).filter(Boolean))
  return (defaults || []).filter((d) => d?.key && !have.has(d.key.trim().toLowerCase()))
}

/** 把缺的出厂引擎补回列表末尾；added 是补了几个（0 表示啥也没改，调用方不必保存） */
export function restoreDefaultEngines(
  list: SearchEngine[],
  defaults?: SearchEngine[],
): { list: SearchEngine[]; added: number } {
  const miss = missingDefaultEngines(list, defaults)
  if (!miss.length) return { list, added: 0 }
  return {
    list: [...(list || []), ...miss.map((d) => ({ key: d.key, title: d.title, url: d.url, enabled: true }))],
    added: miss.length,
  }
}
