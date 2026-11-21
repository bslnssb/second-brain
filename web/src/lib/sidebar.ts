/**
 * 左栏类型按钮的「点第二次收回」规则（第 331 项）。
 *
 * 用户原话：「左侧栏点击『文档』后应该要再次点击『文档』就能收回，而不是点击左侧栏
 * 其他的才能收回。」—— 以前只做了「点另一个类型」才换，同一个类型再点是空操作，
 * 子分类（PDF/Word/Excel…）就一直摊在那儿，只能靠点别的类型把它挤掉。
 *
 * 为什么抽成纯函数：这块逻辑将来还会加「点空白处收回」「Esc 收回」，
 * 单独一条规则 + 打表测试比在 JSX 里写 if 稳。
 */
export type Kind = 'all' | 'dir' | 'doc' | 'img' | 'video' | 'audio' | 'zip' | 'app'

/** 点当前已选中的类型 → 收回成「全部」；点别的类型 → 换成它。 */
export function nextKind(cur: Kind, clicked: Kind): Kind {
  if (clicked === 'all') return 'all'
  return cur === clicked ? 'all' : clicked
}
