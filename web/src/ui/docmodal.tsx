/* 第 379 项：点「预览版面」后弹出来的那一层 —— docx 按页铺、xlsx 出真表格。
 * 版式和 PDF 灯箱一套（黑底、Esc 关、点空白关），只是里面装的是渲染出来的文档。 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { readSheets, renderDocx, type SheetTable } from './docview'

export function DocViewModal({
  path,
  name,
  ext,
  onClose,
}: {
  path: string
  name: string
  ext: string
  onClose: () => void
}) {
  const e = String(ext || '').replace(/^\./, '').toLowerCase()
  const host = useRef<HTMLDivElement | null>(null)
  const [mode, setMode] = useState<'' | 'docx' | 'xlsx'>('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [pages, setPages] = useState(0)
  const [sheets, setSheets] = useState<SheetTable[]>([])
  const [si, setSi] = useState(0)
  const [zv, setZv] = useState(1)

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== 'Escape') return
      // 全局的 Esc 是「清空搜索」，关预览时不能顺手把用户的查询也擦了。
      ev.stopPropagation()
      ev.preventDefault()
      onClose()
    }
    window.addEventListener('keydown', onKey, { capture: true })
    return () => window.removeEventListener('keydown', onKey, { capture: true } as EventListenerOptions)
  }, [onClose])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setErr('')
    setSheets([])
    setPages(0)
    setZv(1)
    void (async () => {
      try {
        if (e === 'docx') {
          const el = host.current
          if (!el) throw new Error('渲染容器没就绪')
          el.innerHTML = ''
          const n = await renderDocx(path, el)
          if (!alive) return
          setPages(n)
          setMode('docx')
        } else {
          const ss = await readSheets(path)
          if (!alive) return
          setSheets(ss)
          setSi(0)
          setMode('xlsx')
        }
      } catch (x) {
        if (alive) setErr(x instanceof Error ? x.message : String(x))
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [path, e])

  const sheet = sheets[si]

  return createPortal(
    <div
      className="fixed inset-0 z-[70] overflow-auto bg-black/80 p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={name}
    >
      <div className="mx-auto w-fit min-w-[320px]">
        {loading && (
          <div className="rounded-md bg-white px-6 py-10 text-center text-[13px] text-[#444]">
            正在按原版式渲染…
          </div>
        )}
        {!!err && (
          <div className="rounded-md bg-white px-6 py-8 text-center text-[13px] text-[#b32]">
            {err}
            <div className="mt-2 text-[11px] text-[#666]">用「打开」按钮在 Office 里看吧。</div>
          </div>
        )}
        {/* docx：docx-preview 会往这个容器里塞一串 <section>，一页一个 */}
        {e === 'docx' && !err && (
          <div
            ref={host}
            onClick={(ev) => ev.stopPropagation()}
            style={{ zoom: zv }}
            className="docview-docx rounded-md bg-white shadow-2xl"
          />
        )}
        {mode === 'xlsx' && sheet && (
          <div onClick={(ev) => ev.stopPropagation()} className="rounded-md bg-white shadow-2xl">
            {sheets.length > 1 && (
              <div className="flex flex-wrap gap-1 border-b border-[#ddd] px-3 py-2">
                {sheets.map((s, i) => (
                  <button
                    key={s.name}
                    onClick={() => setSi(i)}
                    className={`rounded px-2 py-0.5 text-[11px] ${
                      i === si ? 'bg-[#1a73e8] text-white' : 'bg-[#eee] text-[#333]'
                    }`}
                  >
                    {s.name}
                  </button>
                ))}
              </div>
            )}
            <div className="max-h-[82vh] overflow-auto">
              <table className="border-collapse text-[12px]">
                <tbody>
                  {sheet.rows.map((r, ri) => (
                    <tr key={ri} className={ri === 0 ? 'bg-[#f3f6fb] font-medium' : ''}>
                      {r.map((c, ci) => (
                        <td
                          key={ci}
                          className="max-w-[260px] overflow-hidden text-ellipsis whitespace-nowrap border border-[#e2e2e2] px-1.5 py-1 align-top"
                        >
                          {c}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {sheet.cut && (
              <div className="px-3 py-1 text-[10px] text-[#888]">只显示前 300 行 / 40 列</div>
            )}
          </div>
        )}
        {mode === 'xlsx' && !sheet && !loading && !err && (
          <div className="rounded-md bg-white px-6 py-8 text-center text-[13px] text-[#444]">
            这张表里没有可显示的数据。
          </div>
        )}
      </div>
      <div className="pointer-events-none fixed bottom-3 left-0 right-0 text-center text-[11px] text-white/80">
        {name}
        {mode === 'docx' && pages > 0 ? ` · 共 ${pages} 页` : ''}
        {mode === 'docx' ? ` · ${Math.round(zv * 100)}%` : ''}
        {' · 点空白或 Esc 关闭'}
      </div>
      {mode === 'docx' && (
        <div className="fixed bottom-10 left-0 right-0 flex justify-center gap-2">
          {[
            ['－', -0.1],
            ['＋', 0.1],
          ].map(([lab, d]) => (
            <button
              key={lab as string}
              onClick={(ev) => {
                ev.stopPropagation()
                setZv((z) => Math.min(2, Math.max(0.4, Math.round((z + (d as number)) * 10) / 10)))
              }}
              className="rounded-full bg-black/50 px-3 py-1 text-[13px] text-white hover:bg-black/70"
            >
              {lab as string}
            </button>
          ))}
        </div>
      )}
    </div>,
    document.body,
  )
}
