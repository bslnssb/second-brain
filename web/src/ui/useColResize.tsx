import { useEffect, useRef, useState } from 'react'

/**
 * 列宽可拖拽：纯前端 localStorage 持久化，与 view 列表/网格模式同套路（sb_ 前缀），不动后端。
 * 表头与数据行共用同一份 grid 模板保证对齐。每列右缘跨在列边界上的拖拽手柄（详见调用处）。
 *
 * storageKey：两张表用不同键（如时间轴 sb_colw、搜索结果 sb_cols），互不覆盖。
 * defaults / mins：各列默认宽与最小宽（px）。
 *
 * 持久化格式：{ w: 用户宽度, d: 当时的默认值快照 }。
 * 为什么要存 d：老格式只有 { 列: 宽 }，读取时 `{...defaults, ...旧值}` 会让旧宽度
 * 永远压住新默认 —— 2026-11-15 把「大小」列默认 56→48，凡是以前拖过这一列的用户
 * 都看不到变化（用户反馈「还是太大」）。存了 d 之后：某列的默认值改过，就丢掉那一列
 * 的旧宽度改用新默认；默认值没变的列，继续沿用用户自己拖的宽度，不白费他的调整。
 */
type Persisted = { w?: Record<string, number>; d?: Record<string, number> }

export function useColResize(
  storageKey: string,
  defaults: Record<string, number>,
  mins: Record<string, number>,
) {
  const [w, setW] = useState<Record<string, number>>(() => {
    try {
      const raw = localStorage.getItem(storageKey)
      if (!raw) return { ...defaults }
      const parsed = JSON.parse(raw) as Persisted | Record<string, number>
      // 兼容老格式（直接存 map，没有 d 快照）：按「默认值已变则重置该列」处理
      const oldW = (parsed as Persisted).w ?? (parsed as Record<string, number>)
      const oldD = (parsed as Persisted).d ?? {}
      const next: Record<string, number> = { ...defaults }
      for (const k of Object.keys(defaults)) {
        if (typeof oldW?.[k] !== 'number') continue
        // 老格式没有 d 快照 → 无法判断默认值是否变过，按「变过」处理（重置一次，
        // 让新默认真正生效；之后写入新格式就能精确区分了）。
        const hasSnap = typeof oldD?.[k] === 'number'
        const defChanged = hasSnap ? oldD[k] !== defaults[k] : true
        if (!defChanged) next[k] = oldW[k]
      }
      return next
    } catch {
      return { ...defaults }
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify({ w, d: defaults }))
    } catch {}
    // defaults 每次渲染都是新对象，不能进依赖数组（会无限写）；它是常量配置，只在初始化用
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey, w])

  const dragRef = useRef<{ key: string; startX: number; startW: number } | null>(null)
  const onResizeStart = (key: string) => (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragRef.current = { key, startX: e.clientX, startW: w[key] }
    const move = (ev: MouseEvent) => {
      const r = dragRef.current
      if (!r) return
      const next = Math.max(mins[key] ?? 40, r.startW + (ev.clientX - r.startX))
      setW((cur) => ({ ...cur, [key]: next }))
    }
    const up = () => {
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      dragRef.current = null
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  /** 拖拽手柄：跨在「列右边界」上（right:-6px + 12px 宽），两列缝隙处也能抓住；
   *  中间 1px 分隔线常驻可见、悬停高亮，提示这里能拖。 */
  const handle = (key: string, hint: string) => (
    <span
      onMouseDown={onResizeStart(key)}
      className="group absolute right-[-6px] top-0 z-10 flex h-full w-3 cursor-col-resize select-none items-stretch justify-center hover:bg-accent/30"
      title={hint}
    >
      <span className="h-full w-px bg-line group-hover:bg-accent" />
    </span>
  )

  return { w, handle }
}
