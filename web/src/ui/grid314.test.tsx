/**
 * 第 314 项：单击 = 预览、双击 = 打开、悬停也预览；详情面板按「路径」跟随选中行。
 *
 * 用户原话「我想预览哪一个只能用键盘上下，一点击某一条直接打开了，不会预览，
 * 鼠标移动到某一条也不会预览」+「改成单击鼠标是预览，双击鼠标才是打开这样好不好」。
 *
 * App 组件本身依赖一堆后端接口，整套挂起来太重；这里按源码契约钉死交互接线，
 * 谁把 onClick 接回 openRow、或把详情退回按下标取行，这条就会红。
 */
import { describe, expect, it } from 'vitest'
import SRC from '../App.tsx?raw'
import PANELS from './panels.tsx?raw'

/** 取 App.tsx 里网格视图那段（从 `view === 'grid' && shown.length` 到列表视图开始）。 */
function gridBlock() {
  const start = SRC.indexOf("view === 'grid' && shown.length > 0")
  expect(start).toBeGreaterThan(-1)
  const end = SRC.indexOf("{view === 'list' &&", start)
  expect(end).toBeGreaterThan(start)
  return SRC.slice(start, end)
}

describe('第 314 项：网格视图的点击语义', () => {
  it('单击只钉住并预览，不打开文件', () => {
    const g = gridBlock()
    expect(g).toContain('onClick={() => { setPicked(i); pinRow(r.p) }}')
    expect(g).not.toContain('onClick={() => openRow(')
    expect(g).not.toContain('onClick={() => void openRow(')
  })

  it('双击才打开', () => {
    expect(gridBlock()).toContain('onDoubleClick={() => openRow(r.p)}')
  })

  it('第 318 项：悬停不再预览（只认单击，鼠标划过详情不许跳）', () => {
    // 用户原话「改成要单击鼠标左键才能预览，不然我鼠标一动，预览一直跳」
    expect(gridBlock()).not.toContain('onMouseEnter')
    expect(SRC).not.toContain('hoverPin')
    expect(SRC).not.toContain('hoverTimer')
  })

  it('高亮跟「现算出来的下标」走，不再拿旧下标比', () => {
    expect(gridBlock()).toContain('i === curIdx')
    expect(gridBlock()).not.toContain('i === picked')
  })

  it('时间轴的行没有扩展名字段时，详情也能从路径推出后缀出图', () => {
    expect(PANELS).toContain("String(row.e || row.p.split('.').pop() || '')")
  })

  it('详情面板按路径取行（列表时序刷新时不会指到别的文件）', () => {
    expect(SRC).toContain('const pinIdx = pickP ? shown.findIndex((r) => r.p === pickP) : -1')
    expect(SRC).toContain('const curRow = shown[curIdx]')
    expect(SRC).toContain('const activeRow = curRow')
    expect(SRC).toContain('row={activeRow!}')
  })

  it('键盘上下也把路径钉住，和鼠标选中是同一套状态', () => {
    expect(SRC).toContain("setPickP(shown[next]?.p || '')")
  })

  it('第 315 项：首页和搜索页只留一套布局（App 不再渲染 Timeline）', () => {
    // 两套 JSX 各管一套点击语义，才会出现「修好一个、另一个还是坏的」。
    // 现在首页也走 shown（空关键词 = 最近文件），网格/列表是唯一渲染路径。
    expect(SRC).not.toContain('<Timeline')
    expect(SRC).not.toContain('tlRow')
    expect(SRC).not.toContain('tlDays')
    // 卡片那两块不能再被搜索词门控，否则首页就没东西可点
    expect(gridBlock()).toContain('onDoubleClick={() => openRow(r.p)}')
    // 列头按视图显示（列表才有列），不再按「有没有搜索词」
    expect(SRC).toContain("{view === 'list' && (")
  })

  it('第 318 项：灯箱滚轮缩放 + 左右翻图，App 按「图片之间」走', () => {
    expect(SRC).toContain('onStep={stepImage}')
    expect(SRC).toContain('const stepImage = (d: number)')
    expect(PANELS).toContain("el.addEventListener('wheel', onWheel, { passive: false })")
    expect(PANELS).toContain("e.key === 'ArrowLeft'")
    // 第 332 项：箭头语义改成「先翻页、翻到头才换文件」，纯规则在 lib/paging.ts（有单测），
    // 这里只盯面板确实走了这条规则、两个方向都接上了。
    expect(PANELS).toContain('resolveStep(')
    expect(PANELS).toContain('stepBy(-1)')
    expect(PANELS).toContain('stepBy(1)')
    // 翻图时灯箱必须留着，只重置缩放
    expect(PANELS).toContain('useEffect(() => { setThumbBad(false); setScale(1) }, [row.p])')
  })

  it('第 316 项：首页放到 200 条，侧栏筛选作用在同一份 shown，缩略图懒加载', () => {
    // 请求参数与截断长度必须一致，否则「后端给了 400、界面只显示 60」
    expect(SRC).toContain('&n=200&s=')
    expect(SRC).toContain('.slice(0, 200)')
    // 200 条一进页面就并发 200 个缩略图会把 HTTP 线程占死（313 事故），必须 lazy
    expect(SRC).toContain('loading="lazy"')
    // 类型 / 时间两个筛选都过 shown，网格和列表读的是同一份
    const shownBlock = SRC.slice(SRC.indexOf('const shown = useMemo'), SRC.indexOf('}, [rows, kind, days])'))
    expect(shownBlock).toContain("KIND_EXTS[kind]")
    // 第 316 项再调：时间筛选按「日历天」算，不再是滚动 24 小时
    expect(shownBlock).toContain('midnight.setHours(0, 0, 0, 0)')
    expect(shownBlock).toContain('(days - 1) * 86400')
    expect(SRC).toContain('{shown.map((r, i) =>')
  })
})
