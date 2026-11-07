import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getAutostart,
  getConfig,
  getPing,
  getSettings,
  getStats,
  getTimeline,
  saveSettings,
  setAutostart,
  type ConfigInfo,
  type Ping,
  type Settings,
  type Stats,
  type TimelineGroup,
  applyUpdate,
  checkUpdate,
  copyText,
  openUrl,
  saveConfig,
  updateStatus,
  type UpdateInfo,
} from '../api'
import { baseName, dayLabel, dirName, fmtSize, fmtTime, midName } from '../lib/hits'
import { IconCheck, IconClose, IconDoc, IconFile, IconFolder, IconGear, IconImage, IconZip } from './icons'
import { Maintenance } from './maintenance'
import { t, LANGS, setLang, getLang } from '../lib/i18n'

/* ---------------------------------- 设置 ---------------------------------- */

type Props = {
  open: boolean
  onClose: () => void
  onTheme: (t: string) => void
  /** 维护任务跑完时通知外面刷新页脚数字 */
  onRefreshed?: () => void
}

const THEMES: [string, string][] = [
  [ 'auto', t('theme_auto') ],
  [ 'light', t('theme_light2') ],
  [ 'dark', t('theme_dark2') ],
]

export function SettingsDrawer({ open, onClose, onTheme, onRefreshed }: Props) {
  const [s, setS] = useState<Settings>({ hotkey: true, theme: 'auto', lang: 'zh' })
  const [auto, setAuto] = useState(false)
  const [stats, setStats] = useState<Stats>({ files: 0, docs: 0 })
  const [cfg, setCfg] = useState<ConfigInfo | null>(null)
  const [scopeOpen, setScopeOpen] = useState(false)
  /** 范围编辑态：以前这块只读，用户发现范围不对只能去旧界面改 */
  const [editing, setEditing] = useState(false)
  const [eDirs, setEDirs] = useState<string[]>([])
  const [eExcl, setEExcl] = useState<string[]>([])
  const [dirIn, setDirIn] = useState('')
  const [exclIn, setExclIn] = useState('')
  const [saving, setSaving] = useState(false)
  const [scopeMsg, setScopeMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const reloadCfg = () => getConfig().then(setCfg).catch(() => {})

  useEffect(() => {
    if (!open) return
    getSettings()
      .then((r) => setS({ ...r, theme: localStorage.getItem('sb_theme') || r.theme || 'auto' }))
      .catch(() => {})
    getAutostart().then((r) => setAuto(!!r.on)).catch(() => {})
    getStats().then(setStats).catch(() => {})
    // 索引范围：用户在设置里最想知道「到底扫了我哪些文件夹」，别只给一个总数
    getConfig().then(setCfg).catch(() => {})
    // 每次打开设置都退回只读，避免上次没保存的草稿留在界面上
    setEditing(false)
    setScopeMsg(null)
  }, [open])

  if (!open) return null

  const patch = (p: Settings) => {
    setS((cur) => ({ ...cur, ...p }))
    saveSettings(p).catch(() => {})
  }

  const beginEdit = () => {
    setEDirs(cfg?.scope?.include_dirs ? [...cfg.scope.include_dirs] : [])
    setEExcl(cfg?.scope?.exclude_dirs ? [...cfg.scope.exclude_dirs] : [])
    setDirIn('')
    setExclIn('')
    setScopeMsg(null)
    setEditing(true)
  }

  /** 桌面端有原生选目录框；浏览器里没有 pywebview，就只能手输 */
  const pickFolder = async () => {
    const w = window as unknown as {
      pywebview?: { api?: { pick_folder?: () => Promise<string> } }
    }
    try {
      const p = (await w.pywebview?.api?.pick_folder?.()) || ''
      if (p) setEDirs((cur) => (cur.includes(p) ? cur : [...cur, p]))
    } catch {
      /* 浏览器里没有这个桥，忽略 */
    }
  }

  const hasBridge = !!(window as unknown as { pywebview?: { api?: unknown } }).pywebview?.api

  const addDir = () => {
    const v = dirIn.trim()
    if (!v) return
    setEDirs((cur) => (cur.includes(v) ? cur : [...cur, v]))
    setDirIn('')
  }
  const addExcl = () => {
    const v = exclIn.trim()
    if (!v) return
    setEExcl((cur) => (cur.includes(v) ? cur : [...cur, v]))
    setExclIn('')
  }

  const saveScope = async () => {
    if (!eDirs.length) {
      setScopeMsg({ ok: false, text: t('scope_need_one') })
      return
    }
    setSaving(true)
    try {
      const r = await saveConfig(eDirs, eExcl)
      if (!r.ok) {
        setScopeMsg({ ok: false, text: r.k ? t(r.k) : r.err || t('e_scope_save') })
        return
      }
      const dropped = r.dropped || []
      setScopeMsg({
        ok: true,
        text:
          t('scope_saved') +
          ' · ' +
          (dropped.length
            ? t('scope_dropped').replace('{p}', dropped.join('、'))
            : t('scope_saved_tip')),
      })
      setEditing(false)
      reloadCfg()
      getStats().then(setStats).catch(() => {})
    } catch {
      setScopeMsg({ ok: false, text: t('e_scope_save') })
    } finally {
      setSaving(false)
    }
  }

  const Toggle = ({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) => (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onClick}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${on ? 'bg-accent' : 'bg-line'}`}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${on ? 'left-[18px]' : 'left-0.5'}`}
      />
    </button>
  )

  const Row = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div className="flex items-center justify-between gap-3 border-b border-line/70 py-3">
      <span className="text-[13px]">{title}</span>
      {children}
    </div>
  )

  return (
    <aside className="absolute right-0 top-0 z-20 flex h-full w-[330px] flex-col overflow-auto border-l border-line bg-bg px-4 pb-4 shadow-2xl">
      <div className="sticky top-0 flex items-center justify-between bg-bg py-3">
        <span className="flex items-center gap-1.5 text-[14px] font-semibold">
          <IconGear size={15} /> {t('settings')}
        </span>
        <button onClick={onClose} className="text-sub hover:text-ink" aria-label={t('set_close')}>
          <IconClose size={16} />
        </button>
      </div>

      <Row title={t('autostart')}>
        <Toggle
          label={t('boot_start')}
          on={auto}
          onClick={() => {
            const next = !auto
            setAuto(next)
            setAutostart(next).catch(() => setAuto(!next))
          }}
        />
      </Row>
      <Row title={t('hotkey')}>
        <Toggle label={t('hotkey_label')} on={!!s.hotkey} onClick={() => patch({ hotkey: !s.hotkey })} />
      </Row>
      <Row title={t('lang_label')}>
        <select
          value={getLang()}
          onChange={(e) => {
            setLang(e.target.value as (typeof LANGS)[number]['id'], true)
            patch({ lang: e.target.value })
          }}
          className="rounded-md border border-line bg-card px-2 py-1 text-[12px]"
        >
          {LANGS.map((l) => (
            <option key={l.id} value={l.id}>
              {l.label}
            </option>
          ))}
        </select>
      </Row>
      <Row title={t('theme_label')}>
        <select
          value={s.theme || 'auto'}
          onChange={(e) => {
            patch({ theme: e.target.value })
            onTheme(e.target.value)
          }}
          className="rounded-md border border-line bg-card px-2 py-1 text-[12px]"
        >
          {THEMES.map(([v]) => (
            <option key={v} value={v}>
              {t('theme_' + v)}
            </option>
          ))}
        </select>
      </Row>

      <div className="pt-4 text-[11px] uppercase tracking-wide text-sub">{t('section_index')}</div>
      <div className="pt-2 text-[12px] leading-6 text-sub">
        {t('stats_line').split('{n}')[0]}<b className="text-ink">{stats.files.toLocaleString()}</b>{t('stats_line').split('{n}')[1].split('{m}')[0]}
        <b className="text-ink">{stats.docs.toLocaleString()}</b>{t('stats_line').split('{m}')[1]}
      </div>

      {/* 索引范围：文件名层扫了哪些盘、正文层收了哪些文件夹、哪些被排除。
          v0.9.46 起可以直接改 —— 之前只有「看」，用户发现范围不对根本改不了。
          保存只写 scope.json，不自动重扫：误点一下不该让几十万条索引重新跑一遍。 */}
      {cfg?.scope && (
        <div className="mt-3 rounded-lg border border-line/70 bg-card/50 p-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12px] font-semibold">{t('scope_title')}</span>
            {!editing ? (
              <div className="flex items-center gap-2">
                {cfg.scope.include_dirs.length > 3 && (
                  <button
                    onClick={() => setScopeOpen((v) => !v)}
                    aria-expanded={scopeOpen}
                    className="text-[11px] text-accent hover:underline"
                  >
                    {scopeOpen ? t('scope_less') : t('scope_more')}
                  </button>
                )}
                <button onClick={beginEdit} className="text-[11px] text-accent hover:underline">
                  {t('scope_edit')}
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    setEditing(false)
                    setScopeMsg(null)
                  }}
                  className="text-[11px] text-sub hover:underline"
                >
                  {t('scope_cancel')}
                </button>
                <button
                  onClick={saveScope}
                  disabled={saving}
                  className="rounded bg-accent px-1.5 py-[1px] text-[11px] text-white disabled:opacity-60"
                >
                  {saving ? '…' : t('scope_save')}
                </button>
              </div>
            )}
          </div>

          {editing && (
            <div className="pt-2">
              <div className="text-[11px] text-sub">
                <span className="font-medium text-ink">{t('scope_docs')}</span>
                <span className="ml-1.5">{t('scope_folders').replace('{n}', String(eDirs.length))}</span>
              </div>
              <ul className="mt-1 space-y-1">
                {eDirs.map((p) => (
                  <li key={p} className="flex items-center gap-1.5">
                    <span className="truncate font-mono text-[10px] leading-4 text-sub" title={p}>
                      {p}
                    </span>
                    <button
                      onClick={() => setEDirs((cur) => cur.filter((x) => x !== p))}
                      title={t('scope_remove')}
                      aria-label={t('scope_remove')}
                      className="ml-auto shrink-0 rounded px-1 text-[11px] text-sub hover:bg-line/60 hover:text-red-500"
                    >
                      ✕
                    </button>
                  </li>
                ))}
                {!eDirs.length && <li className="text-[10px] text-sub/70">{t('scope_need_one')}</li>}
              </ul>
              <div className="mt-1.5 flex gap-1">
                <input
                  value={dirIn}
                  onChange={(e) => setDirIn(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      addDir()
                    }
                  }}
                  placeholder={t('scope_ph_dir')}
                  className="min-w-0 flex-1 rounded border border-line bg-bg px-1.5 py-[3px] font-mono text-[10px]"
                />
                <button
                  onClick={addDir}
                  className="shrink-0 rounded border border-line px-1.5 text-[11px] hover:border-accent"
                >
                  {t('scope_add_dir')}
                </button>
                {hasBridge && (
                  <button
                    onClick={pickFolder}
                    className="shrink-0 rounded border border-line px-1.5 text-[11px] hover:border-accent"
                  >
                    {t('scope_pick')}
                  </button>
                )}
              </div>

              <div className="pt-2.5 text-[11px] text-sub">
                <span className="font-medium text-ink">{t('scope_excl').replace('{n}', String(eExcl.length))}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {eExcl.map((w) => (
                  <span
                    key={w}
                    className="flex items-center gap-1 rounded border border-line/60 bg-bg px-1.5 py-[1px] text-[10px] text-sub"
                  >
                    {w}
                    <button
                      onClick={() => setEExcl((cur) => cur.filter((x) => x !== w))}
                      title={t('scope_remove')}
                      aria-label={t('scope_remove')}
                      className="text-sub hover:text-red-500"
                    >
                      ✕
                    </button>
                  </span>
                ))}
                {!eExcl.length && <span className="text-[10px] text-sub/70">—</span>}
              </div>
              <div className="mt-1.5 flex gap-1">
                <input
                  value={exclIn}
                  onChange={(e) => setExclIn(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      addExcl()
                    }
                  }}
                  placeholder={t('scope_ph_excl')}
                  className="min-w-0 flex-1 rounded border border-line bg-bg px-1.5 py-[3px] text-[10px]"
                />
                <button
                  onClick={addExcl}
                  className="shrink-0 rounded border border-line px-1.5 text-[11px] hover:border-accent"
                >
                  {t('scope_add_excl')}
                </button>
              </div>
              <div className="pt-1.5 text-[10px] leading-4 text-sub/80">{t('scope_edit_tip')}</div>
            </div>
          )}

          {!editing && (
            <>
              <div className="pt-2">
            <div className="text-[11px] text-sub">
              <span className="font-medium text-ink">{t('scope_names')}</span>
              <span className="ml-1.5">
                {t('scope_drives').replace('{n}', String(cfg.scope.drives.length))} ·{' '}
                {cfg.scope.files.toLocaleString()}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {cfg.scope.drives.map(([d, n]) => (
                <span
                  key={d}
                  className="rounded border border-line/60 bg-bg px-1.5 py-[1px] font-mono text-[10px] text-sub"
                >
                  {d} {n.toLocaleString()}
                </span>
              ))}
            </div>
            {cfg.scope.top.length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {cfg.scope.top.slice(0, 3).map(([p, n]) => (
                  <li key={p} className="flex gap-2 text-[10px] leading-4 text-sub" title={p}>
                    <span className="truncate font-mono">{p}</span>
                    <span className="ml-auto shrink-0 text-sub/80">{n.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="pt-2.5">
            <div className="text-[11px] text-sub">
              <span className="font-medium text-ink">{t('scope_docs')}</span>
              <span className="ml-1.5">
                {cfg.scope.include_dirs.length
                  ? `${t('scope_folders').replace('{n}', String(cfg.scope.include_dirs.length))} · ${cfg.scope.docs.toLocaleString()}`
                  : t('scope_empty')}
              </span>
            </div>
            <ul className="mt-1 space-y-0.5">
              {(scopeOpen ? cfg.scope.include_dirs : cfg.scope.include_dirs.slice(0, 3)).map((p) => (
                <li key={p} className="truncate font-mono text-[10px] leading-4 text-sub" title={p}>
                  {p}
                </li>
              ))}
              {!scopeOpen && cfg.scope.include_dirs.length > 3 && (
                <li className="text-[10px] text-sub/70">+{cfg.scope.include_dirs.length - 3}…</li>
              )}
            </ul>
          </div>

          {cfg.scope.exclude_dirs.length > 0 && (
            <div className="pt-2 text-[10px] leading-4 text-sub">
              <span className="text-ink">{t('scope_excl').replace('{n}', String(cfg.scope.exclude_dirs.length))}</span>
              <span className="ml-1">
                {cfg.scope.exclude_dirs.slice(0, 6).join(' · ')}
                {cfg.scope.exclude_dirs.length > 6 ? ' …' : ''}
              </span>
            </div>
          )}
          <div className="pt-1 text-[10px] leading-4 text-sub/80">
            {t('scope_rules')
              .replace('{e}', String(cfg.scope.rules.ext))
              .replace('{s}', String(Math.round(cfg.scope.rules.size_limit / 1073741824)))}
          </div>
            </>
          )}

          {scopeMsg && (
            <div
              className={`pt-2 text-[10px] leading-4 ${scopeMsg.ok ? 'text-accent' : 'text-red-500'}`}
              role="status"
            >
              {scopeMsg.text}
            </div>
          )}
        </div>
      )}

      <Maintenance onDone={onRefreshed} />
    </aside>
  )
}

/* ---------------------------------- 帮助 ---------------------------------- */

const KB = (): [string, string][] => [
  ['↑ ↓', t('kb1')],
  ['Enter', t('kb2')],
  ['Ctrl+Enter', t('kb3')],
  ['Ctrl+1~9', t('kb4')],
  ['Ctrl+S', t('kb5')],
  ['Ctrl+F', t('kb6')],
  ['Esc', t('kb7')],
  ['Ctrl Ctrl', t('kb8')],
]

/** 搜索框语法速览（帮助弹窗与侧栏「可用的语法」共用同一份，别写两份） */
export const QS = (): [string, string][] => [
  ['ext:pdf', t('qs1')],
  ['size:>10m', t('qs2')],
  ['dm:7d', t('qs3')],
  ['-draft', t('qs4')],
  ['folder/', t('qs5')],
  ['content:', t('qs6')],
]

const MORE = (): [string, string][] => [
  ['0', t('more1')],
  ['→', t('more2')],
  ['drop', t('more3')],
  ['▦', t('more4')],
  ['tray', t('more5')],
]

export function HelpModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null
  const Section = ({ title, rows }: { title: string; rows: [string, string][] }) => (
    <div className="min-w-0">
      <div className="pb-2 text-[13px] font-semibold text-accent">{title}</div>
      <div className="flex flex-col gap-1.5">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-start gap-2.5">
            <kbd className="mt-[1px] h-6 min-w-[74px] shrink-0 rounded border border-line bg-card px-2 text-center text-[11px] leading-[22px]">
              {k}
            </kbd>
            <span className="text-[12px] leading-6 text-ink/90">{v}</span>
          </div>
        ))}
      </div>
    </div>
  )
  return (
    <Modal onClose={onClose} title={t('help_title')} width={760}>
      <div className="grid grid-cols-2 gap-x-8 gap-y-5">
        <Section title={t('help_kb')} rows={KB()} />
        <div className="flex flex-col gap-5">
          <Section title={t('help_qs')} rows={QS()} />
          <Section title={t('help_more')} rows={MORE()} />
        </div>
      </div>
      <div className="pt-5 text-center text-[11px] text-sub">
        {t('help_foot')}
      </div>
    </Modal>
  )
}

/* ---------------------------------- 关于 ---------------------------------- */

/** 档位话术：金额只是「心意的大小」，说法要轻松，不能像催付款 */
const DONATE_NOTE: Record<number, string> = {
  1: '随喜，一分也是心意',
  2: '一瓶水，谢谢',
  5: '支持一下',
  6: '支持一下，谢谢你的时间',
  10: '请我喝杯奶茶',
  16: '请我喝杯奶茶，加个珍珠',
  20: '一顿午饭，够我接着写了',
  30: '一箱方便面，今晚不睡',
  50: '请我吃顿饭',
  66: '一顿好的，谢谢老板',
  100: '你是真的觉得它好用',
}
function donateNote(amount: number): string {
  if (!amount) return '请我喝杯奶茶，祝你天天开心'
  return DONATE_NOTE[amount] || '谢谢你的支持'
}

/** 收款码：图片放在 assets/donate*.png，一张或多张（文件名里的数字就是档位）。
 *  每张都由后端算 SHA-256 跟编译进 exe 的常量比：
 *  对不上就**不显示**——有人把安装包拆开换成自己的收款码再发出去时，这里会当场露馅。 */
type DonateItem = { file: string; amount: number; sha?: string; official?: boolean }

function DonateQR() {
  const [zoom, setZoom] = useState(false)
  const [items, setItems] = useState<DonateItem[] | null>(null)
  const [pick, setPick] = useState(0)
  useEffect(() => {
    fetch('/api/donate', { headers: { 'Sec-Fetch-Site': 'same-origin' } })
      .then((r) => r.json())
      .then((d: { items?: DonateItem[] }) => setItems((d.items || []).slice().sort((a, b) => a.amount - b.amount)))
      .catch(() => setItems([]))
  }, [])
  if (!items) return <div className="h-[104px] w-[104px] shrink-0 rounded-md bg-card" />
  if (!items.length)
    return (
      <div className="grid h-[104px] w-[104px] shrink-0 place-items-center rounded-md border border-dashed border-line text-center text-[10px] leading-4 text-sub">
        {t('donate_drop1')}
        <br />
        {t('donate_put')}
      </div>
    )
  const cur = items[Math.min(pick, items.length - 1)]
  const src = `/donate.png?f=${encodeURIComponent(cur.file)}`
  // official 缺失（后端没配指纹）时不拦；明确 false 才拦
  const trusted = cur.official !== false

  if (!trusted) {
    return (
      <div className="flex w-[180px] shrink-0 flex-col gap-1.5 rounded-md border border-red-300 bg-red-50 p-2 text-[10px] leading-4 text-red-700">
        <div className="font-semibold">{t('about_fp_bad')}</div>
        <div>{t('about_fp_bad_hint')}</div>
        {cur.sha && <div className="font-mono opacity-70">{cur.sha.slice(0, 16)}</div>}
      </div>
    )
  }

  return (
    <div className="flex shrink-0 flex-col items-center gap-1.5">
      <button onClick={() => setZoom(true)} title={t('donate_zoom')} className="shrink-0">
        <img
          src={src}
          alt={t('donate_alt')}
          className="h-[180px] w-[180px] rounded-md border border-line object-contain hover:border-accent"
        />
      </button>
      <div className="text-center text-[10px] leading-4 text-sub">{donateNote(cur.amount)}</div>
      {cur.sha && (
        <div
          className="text-center font-mono text-[9px] leading-3 text-sub/70"
          title={`SHA-256 ${cur.sha}`}
        >
          {t('about_fp')} {cur.sha.slice(0, 12)} · {t('about_fp_ok')}
        </div>
      )}
      {zoom && (
        <div
          className="fixed inset-0 z-[60] grid place-items-center bg-black/55 p-6"
          onClick={() => setZoom(false)}
          title={t('donate_close')}
        >
          <img src={src} alt={t('donate_alt')} className="max-h-[86vh] max-w-[86vw] rounded-lg bg-white p-3 shadow-2xl" />
        </div>
      )}
      {items.length > 1 && (
        <div className="flex flex-wrap justify-center gap-1">
          {items.map((it, i) => (
            <button
              key={it.file}
              onClick={() => setPick(i)}
              title={donateNote(it.amount)}
              className={`rounded px-1.5 py-0.5 text-[11px] ${
                i === pick ? 'bg-accent text-white' : 'border border-line text-sub hover:text-ink'
              }`}
            >
              {it.amount > 0 ? `¥${it.amount}` : it.file}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** 官方地址区：地址来自 /api/ping（后端常量），点击走 /api/open_url 白名单 */
function OfficialLinks({ ping }: { ping: Ping }) {
  const [copied, setCopied] = useState(false)
  if (!ping.repo) return null
  const rows: [string, string][] = [
    [t('about_repo'), ping.repo],
    [t('about_releases'), ping.releases || ''],
    [t('about_issues'), ping.issues || ''],
  ]
  const go = async (u: string) => {
    if (!u) return
    const ok = await openUrl(u)
    if (!ok) {
      copyText(u)
      setCopied(true)
    }
  }
  return (
    <div className="mt-3 border-t border-line pt-3">
      <div className="mb-1.5 text-[12px] text-sub">{t('about_official')}</div>
      <div className="space-y-1">
        {rows
          .filter(([, u]) => !!u)
          .map(([label, u]) => (
            <button
              key={label}
              onClick={() => go(u)}
              title={u}
              className="flex w-full items-center gap-2 rounded-md border border-line/70 px-2 py-1 text-left hover:border-accent"
            >
              <span className="shrink-0 text-[12px]">{label}</span>
              <span className="ml-auto max-w-[60%] truncate font-mono text-[10px] text-sub">{u}</span>
            </button>
          ))}
      </div>
      {copied && <div className="pt-1 text-[10px] leading-4 text-sub">{t('about_open_fail')}</div>}
      <div className="pt-1.5 text-[10px] leading-4 text-sub/80">{t('about_only_official')}</div>
    </div>
  )
}

export function AboutModal({ open, onClose }: { open: boolean; onClose: () => void }) {  const [stats, setStats] = useState<Stats>({ files: 0, docs: 0 })
  const [ping, setPing] = useState<Ping>({ ver: '…' })
  // 检查更新：只有点按钮才发请求（要连 GitHub，不能跟着弹窗自动跑）
  const [up, setUp] = useState<{ state: 'idle' | 'busy' | 'done' | 'err'; text?: string; info?: UpdateInfo }>({
    state: 'idle',
  })
  const [installing, setInstalling] = useState(false)
  /** 一键升级的下载进度（-1 = 还没拿到第一拍）；后端异步下载，前端轮询 */
  const [dlPct, setDlPct] = useState(-1)
  const dlTimer = useRef<number | null>(null)
  useEffect(() => {
    if (!open) return
    getStats().then(setStats).catch(() => {})
    getPing().then(setPing).catch(() => {})
    setUp({ state: 'idle' })
    return () => {
      if (dlTimer.current) window.clearInterval(dlTimer.current)
      dlTimer.current = null
    }
  }, [open])

  const doCheck = async () => {
    setUp({ state: 'busy', text: t('about_checking') })
    const r = await checkUpdate()
    if (!r.ok) {
      // 国内直连 GitHub 经常超时，这里说人话，并把「手动下载」入口给出来
      setUp({
        state: 'err',
        text: t('up_offline'),
      })
      return
    }
    if (r.newer) setUp({ state: 'done', text: t('up_new').replace('{v}', String(r.latest)), info: r })
    else if (r.norelease) setUp({ state: 'done', text: t('up_uptodate').replace('{v}', String(r.latest || ping.ver)), info: r })
    else setUp({ state: 'done', text: t('up_uptodate').replace('{v}', String(r.latest || ping.ver)), info: r })
  }

  const doInstall = async () => {
    setInstalling(true)
    setDlPct(-1)
    let r: { ok: boolean; err?: string; started?: boolean }
    try {
      r = await applyUpdate()
    } catch (e) {
      r = { ok: false, err: String(e) }
    }
    if (!r.ok) {
      setInstalling(false)
      setUp({ state: 'err', text: t('about_dl_fail').replace('{e}', String(r.err || t('about_unknown'))) })
      return
    }
    // 后端已改异步：立即返回 started，下载进度用 /api/update_status 轮询
    const id = window.setInterval(async () => {
      try {
        const s = await updateStatus()
        if (s.state === 'download' || s.state === 'unpack') {
          setDlPct(s.pct ?? 0)
        } else if (s.state === 'done') {
          window.clearInterval(id)
          setDlPct(100)                       // 到这步程序马上自己重启，保持「完成」显示
        } else if (s.state === 'error') {
          window.clearInterval(id)
          setInstalling(false)
          setDlPct(-1)
          setUp({
            state: 'err',
            text: t('about_dl_fail').replace('{e}', String(s.err || t('about_unknown'))),
          })
        }
      } catch {
        /* 后端正在退出重启时读不到，下一轮再说 */
      }
    }, 700)
    dlTimer.current = id
  }

  if (!open) return null
  return (
    <Modal onClose={onClose} title={t('about_title')} width={520}>
      <div className="flex flex-col items-center gap-1 pb-4 text-center">
        <img
          src="/logo.png"
          alt=""
          width={56}
          height={56}
          className="mb-1 h-14 w-14 rounded-xl"
          onError={(e) => {
            ;(e.currentTarget as HTMLImageElement).style.display = 'none'
          }}
        />
        {/* 品牌名固定双语，不跟界面语言变：中文用户看得懂，外国用户也认得出 */}
        <div className="text-[17px] font-semibold leading-6">Second Brain</div>
        <div className="text-[13px] text-sub">第二大脑 · {t('about_sub')}</div>
        <div className="text-[22px] font-bold text-accent">{ping.ver}</div>
        <div className="pt-1 text-[12px] text-sub">
          {t('about_stats')
            .replace('{n}', stats.files.toLocaleString())
            .replace('{m}', stats.docs.toLocaleString())}
        </div>
        <div className="text-[11px] text-sub">{t('about_local')}</div>
        {ping.dir && (
          <div className="mt-2 max-w-full break-all rounded-md bg-card px-2 py-1 font-mono text-[10px] text-sub">
            {t('about_datadir')}: {ping.dir}
            {ping.fallback ? t('about_fallback') : ''}
          </div>
        )}
      </div>

      {/* 检查更新 */}
      <div className="border-t border-line pt-3">
        <div className="mb-2 text-[12px] text-sub">{t('about_ver2')}</div>
        <div className="flex items-center gap-2">
          <button
            onClick={doCheck}
            disabled={up.state === 'busy' || installing}
            className="rounded-md border border-line px-2.5 py-1 text-[12px] hover:bg-card disabled:opacity-60"
          >
            {up.state === 'busy' ? t('about_checking') : t('about_check')}
          </button>
          <span
            className={`text-[12px] ${
              up.state === 'err' ? 'text-red-500' : up.info?.newer ? 'text-accent' : 'text-sub'
            }`}
          >
            {up.text || ''}
          </span>
          {up.info?.newer && (
            <button
              onClick={doInstall}
              disabled={installing}
              className="ml-auto rounded-md bg-accent px-2.5 py-1 text-[12px] text-white hover:opacity-90 disabled:opacity-60"
            >
              {installing
                ? dlPct >= 0
                  ? `${t('about_downloading')} ${dlPct}%`
                  : t('about_downloading')
                : t('about_download')}
            </button>
          )}
        </div>
        {installing && (
          <div className="pt-2">
            <div className="h-1.5 overflow-hidden rounded-full bg-line">
              {dlPct >= 0 && (
                <div className="h-full bg-accent transition-all" style={{ width: `${dlPct}%` }} />
              )}
            </div>
            <div className="pt-1 text-[11px] leading-4 text-sub">
              {dlPct >= 100 ? t('about_dl_hint') : t('about_downloading')}
            </div>
          </div>
        )}
      </div>

      {/* 官方地址：链接本身写在 brain.py 里（跟着 exe 编译），前端只显示。
          别人改一行 JS 就能换掉写死在前端的链接，但要换这里的得重新打包。 */}
      <OfficialLinks ping={ping} />

      {/* 支持一下：二维码是可选的，assets/donate.png 存在才显示；指纹对不上就藏起来 */}
      <div className="mt-3 border-t border-line pt-3">
        <div className="mb-1 text-[12px] text-sub">{t('about_support')}</div>
        <div className="flex items-center gap-3">
          <DonateQR />
          <div className="text-[11px] leading-5 text-sub">
            {t('about_free')}
            <br />
            {t('about_thanks')}
            <br />
            <span className="text-[10px] opacity-80">{t('about_scan')}</span>
          </div>
        </div>
      </div>
    </Modal>
  )
}

function Modal({
  title,
  onClose,
  width,
  children,
}: {
  title: string
  onClose: () => void
  width: number
  children: React.ReactNode
}) {
  return (
    <div className="absolute inset-0 z-30 grid place-items-center bg-black/25" onClick={onClose}>
      <div
        role="dialog"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        style={{ width }}
        className="max-h-[86vh] max-w-[94vw] overflow-auto rounded-2xl border border-line bg-bg p-5 shadow-2xl"
      >
        <div className="flex items-center justify-between pb-3">
          <span className="text-[15px] font-semibold">{title}</span>
          <button onClick={onClose} className="text-sub hover:text-ink" aria-label={t('close')}>
            <IconClose size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

/* --------------------------------- 时间轴 --------------------------------- */

function iconForName(p: string) {
  const e = (p.match(/\.([A-Za-z0-9]{1,6})$/) || [, ''])[1].toLowerCase()
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'svg'].includes(e)) return <IconImage size={15} />
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(e)) return <IconZip size={15} />
  if (['doc', 'docx', 'pdf', 'txt', 'md', 'xls', 'xlsx', 'ppt', 'pptx', 'csv'].includes(e)) return <IconDoc size={15} />
  if (e) return <IconFile size={15} />
  return <IconFolder size={15} />
}

/* 时间轴卡片也要系统真图标：这里复刻一份 App 里那套（同一个 /api/icon） */
function RowIcon({ row }: { row: { p: string; e?: string } }) {
  const [bad, setBad] = useState(false)
  const ext = String(row.e || '').replace(/^\./, '').toLowerCase()
  if (bad) return <span className="text-[11px] text-sub">{ext ? ext.toUpperCase() : t('kind_file')}</span>
  return (
    <img
      src={`/api/icon?p=${encodeURIComponent(row.p)}&e=${encodeURIComponent(ext)}`}
      width={28}
      height={28}
      alt=""
      loading="lazy"
      className="h-7 w-7 object-contain"
      onError={() => setBad(true)}
    />
  )
}

export function Timeline({
  onPick,
  kind = 'all',
  sort = 'new',
  view = 'list',
  onHover,
  selected,
}: {
  onPick: (p: string) => void
  kind?: string
  /** 排序：时间轴默认按时间倒序，用户换排序时这里也跟着变，否则按钮像坏的 */
  sort?: 'rel' | 'new' | 'old' | 'big' | 'name'
  view?: 'list' | 'grid'
  /** 鼠标划到某一行 → 右侧详情面板跟着显示（没搜索结果时也能用详情） */
  onHover?: (row: { p: string; n?: string; s?: number; m?: number }) => void
  selected?: string
}) {
  const [days, setDays] = useState(14)
  const [groups, setGroups] = useState<TimelineGroup[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    getTimeline(days, kind)
      .then((r) => alive && setGroups(r.groups || []))
      .catch(() => alive && setGroups([]))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [days, kind])

  const total = useMemo(() => groups.reduce((a, g) => a + g.n, 0), [groups])

  // 每组内部按当前排序重排：相关度在这里退化成时间倒序（时间轴本来就没有相关度）
  const sorted = useMemo(
    () =>
      groups.map((g) => ({
        ...g,
        items: [...g.items].sort((a, b) => {
          if (sort === 'name') return String(a.n || a.p).localeCompare(String(b.n || b.p), 'zh')
          if (sort === 'big') return (b.s || 0) - (a.s || 0)
          if (sort === 'old') return (a.m || 0) - (b.m || 0)
          return (b.m || 0) - (a.m || 0)
        }),
      })),
    [groups, sort],
  )

  if (loading) return <div className="grid h-full place-items-center text-[12px] text-sub">{t('tl_reading')}</div>
  if (!groups.length)
    return (
      <div className="grid h-full place-items-center text-center text-sub">
        <div>
          <div className="text-[14px] text-ink">
            {t(kind === 'dir' ? 'tl_empty_dir' : kind === 'all' ? 'tl_empty_all' : 'tl_empty_kind')
              .replace('{n}', String(days))}
          </div>
          <div className="mt-1 text-[12px]">{t('type_to_search')}</div>
        </div>
      </div>
    )

  return (
    <div className="pb-6">
      <div className="flex items-center gap-2 px-2 py-1 text-[11px] text-sub">
        <span>
          {(() => {
            const L = getLang()
            const n = total.toLocaleString()
            if (L === 'zh') return `最近 ${days} 天 · 每天列出前 40 个（共 ${n} 条记录）`
            if (L === 'es') return `${days} días · primeros 40 por día (${n} en total)`
            if (L === 'pt') return `${days} dias · primeiros 40 por dia (${n} no total)`
            if (L === 'hi') return `${days} दिन · रोज़ पहले 40 (कुल ${n})`
            return `${days} days · first 40 per day (${n} in total)`
          })()}
        </span>
        <span className="text-line">|</span>
        <button className="hover:text-ink" onClick={() => setDays(7)}>{getLang() === 'zh' ? '7 天' : getLang() === 'es' ? '7 días' : getLang() === 'pt' ? '7 dias' : getLang() === 'hi' ? '7 दिन' : '7 days'}</button>
        <button className="hover:text-ink" onClick={() => setDays(30)}>{getLang() === 'zh' ? '30 天' : getLang() === 'es' ? '30 días' : getLang() === 'pt' ? '30 dias' : getLang() === 'hi' ? '30 दिन' : '30 days'}</button>
      </div>
      {/* 列头：搜索结果的列表有「名称/路径/大小/时间」表头，时间轴以前没有——
          用户不知道右边两列是什么。列宽与下面的行严格对齐；缩略图（grid）模式没有列，不渲染。 */}
      {view === 'list' && (
        <div className="flex items-center gap-2 border-b border-line px-2 pb-1 text-[11px] text-sub">
          <span className="w-4 shrink-0" />
          <span className="w-[46%] shrink-0">{t('col_name_only')}</span>
          <span className="min-w-0 flex-1">{t('col_path')}</span>
          <span className="w-20 shrink-0 text-right">{t('col_size')}</span>
          <span className="w-28 shrink-0 text-right">{t('col_mtime')}</span>
        </div>
      )}
      {sorted.map((g) => (
        <section key={g.d}>
          <div className="flex items-baseline gap-2 px-2 pb-1 pt-3">
            <span className="text-[12px] font-semibold text-ink">{dayLabel(g.items[0]?.m ?? 0, undefined, getLang()) || g.label}</span>
            <span className="text-[11px] text-sub">{g.n.toLocaleString()}</span>
            {g.more && (
              <span className="text-[11px] text-sub">
                {getLang() === 'zh' ? `· 只列前 ${g.items.length} 个`
                  : getLang() === 'es' ? `· primeros ${g.items.length}`
                  : getLang() === 'pt' ? `· primeiros ${g.items.length}`
                  : getLang() === 'hi' ? `· पहले ${g.items.length}`
                  : `· first ${g.items.length}`}
              </span>
            )}
          </div>
          {view === 'grid' ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-2 px-2 pb-2">
              {g.items.map((it) => {
                const name = it.n || baseName(it.p)
                return (
                  <button
                    key={it.p}
                    onClick={() => onPick(it.p)}
                    onMouseEnter={() => onHover?.({ p: it.p, n: name, s: it.s, m: it.m })}
                    className={`overflow-hidden rounded-lg border bg-card text-left ${
                      selected === it.p ? 'border-accent' : 'border-line hover:border-accent/60'
                    }`}
                  >
                    <span className="relative grid h-[86px] place-items-center overflow-hidden bg-bg">
                      {/* 底下永远先放类型图标（系统真图标），缩略图能拿到就盖在上面，
                          拿不到（204）就自然露出来，不会是一块空白 */}
                      <span className="scale-[1.6]">
                        <RowIcon row={{ p: it.p, e: (it.p.match(/\.([A-Za-z0-9]{1,6})$/) || [, ''])[1] }} />
                      </span>
                      <img
                        src={`/api/thumb?p=${encodeURIComponent(it.p)}`}
                        alt=""
                        loading="lazy"
                        className="absolute inset-0 m-auto max-h-[86px] max-w-full object-contain"
                        onError={(e) => {
                          ;(e.currentTarget as HTMLImageElement).style.display = 'none'
                        }}
                      />
                    </span>
                    <span className="block truncate px-1.5 py-1 text-[12px]">{midName(name, 24)}</span>
                    <span className="block px-1.5 pb-1 text-[10px] text-sub">{fmtSize(it.s)}</span>
                  </button>
                )
              })}
            </div>
          ) : (
          g.items.map((it) => {
            const name = it.n || baseName(it.p)
            return (
              <button
                key={it.p}
                onClick={() => onPick(it.p)}
                onMouseEnter={() => onHover?.({ p: it.p, n: name, s: it.s, m: it.m })}
                className={`flex w-full items-center gap-2 rounded-md px-2 py-[5px] text-left ${
                  selected === it.p ? 'bg-card' : 'hover:bg-card'
                }`}
              >
                <span className="w-4 shrink-0 text-sub">{iconForName(it.p)}</span>
                <span className="w-[46%] shrink-0 truncate">{midName(name, 42)}</span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-sub">{dirName(it.p)}</span>
                <span className="w-20 shrink-0 text-right text-[11px] text-sub">{fmtSize(it.s)}</span>
                <span className="w-28 shrink-0 text-right font-mono text-[11px] text-sub">{fmtTime(it.m)}</span>
              </button>
            )
          })
          )}
        </section>
      ))}
    </div>
  )
}

/** 把后端用 … 标的命中片段渲染成高亮 */
function Marked({ text }: { text: string }) {
  const parts = String(text || '').split(/[]/)
  return (
    <span className="break-words">
      {parts.map((seg, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="hl">
            {seg}
          </mark>
        ) : (
          <span key={i}>{seg}</span>
        ),
      )}
    </span>
  )
}

/* --------------------------------- 详情面板 -------------------------------- */

export function Preview({
  row,
  q = '',
  onClose,
  onOpen,
  onReveal,
  onCopy,
}: {
  row: { p: string; n?: string; s?: number; m?: number; e?: string; b?: string; snip?: string; t?: string }
  /** 当前搜索词：取正文时用它定位命中位置 */
  q?: string
  onClose: () => void
  onOpen: () => void
  onReveal: () => void
  onCopy: () => void
}) {
  const name = row.n || baseName(row.p)
  // 命中片段是现成的（搜索时后端已带回），直接展示；
  // 没有片段（只是文件名命中）才让用户点一下按需读正文——docs 表按 path 查是全表扫，
  // 不能挂在 hover 上，否则鼠标一划就几百毫秒。
  const [peek, setPeek] = useState<{ ok: boolean; text?: string; why?: string; matched?: boolean } | null>(null)
  const [peeking, setPeeking] = useState(false)
  useEffect(() => {
    setPeek(null)
    setPeeking(false)
  }, [row.p])

  const loadPeek = async () => {
    setPeeking(true)
    try {
      const r = await fetch(
        `/api/peek?p=${encodeURIComponent(row.p)}&q=${encodeURIComponent(q)}`,
        { headers: { 'Sec-Fetch-Site': 'same-origin' } },
      )
      setPeek((await r.json()) as { ok: boolean; text?: string; why?: string; matched?: boolean })
    } catch {
      setPeek({ ok: false, why: 'error' })
    } finally {
      setPeeking(false)
    }
  }
  const snip = row.b || row.snip || ''
  // 只有「文档类」才值得读正文：exe/音频/视频/文件夹既没正文，点了也是空
  const DOC_EXT = ['doc', 'docx', 'pdf', 'txt', 'md', 'rtf', 'xls', 'xlsx', 'csv', 'ppt', 'pptx', 'wps', 'et', 'dps', 'epub']
  const e0 = String(row.e || '').replace(/^\./, '').toLowerCase()
  const canPeek = row.t !== 'dir' && (DOC_EXT.includes(e0) || (!e0 && row.t === 'doc'))
  return (
    <aside className="flex h-full w-[300px] shrink-0 flex-col overflow-auto border-l border-line px-3 py-2 text-[12px]">
      <div className="flex items-center justify-between pb-2">
        <span className="text-[11px] uppercase tracking-wide text-sub">{t('dtl_title')}</span>
        <button onClick={onClose} className="text-sub hover:text-ink" aria-label={t('dtl_close')}>
          <IconClose size={14} />
        </button>
      </div>
      <div className="flex items-start gap-2 pb-2">
        <span className="mt-[2px] text-sub">{iconForName(row.p)}</span>
        <span className="break-all text-[13px] font-medium leading-5">{name}</span>
      </div>
      <div className="break-all rounded-md bg-card px-2 py-1.5 font-mono text-[10px] leading-4 text-sub">{row.p}</div>
      <dl className="mt-3 space-y-1.5">
        <div className="flex gap-2">
          <dt className="w-14 shrink-0 text-sub">{t('dtl_type')}</dt>
          <dd>{row.t === 'dir' ? t('kind_dir') : (row.e || t('kind_file')).toString().replace(/^\./, '').toUpperCase()}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-14 shrink-0 text-sub">{t('dtl_size')}</dt>
          <dd>{fmtSize(row.s)}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-14 shrink-0 text-sub">{t('dtl_mtime')}</dt>
          <dd className="font-mono text-[11px]">{fmtTime(row.m)}</dd>
        </div>
      </dl>
      {snip ? (
        <div className="mt-3 rounded-md bg-card px-2 py-1.5 text-[11px] leading-5 text-ink/85">
          <div className="mb-1 text-[10px] text-sub">{t('dtl_hit_body')}</div>
          <Marked text={snip} />
        </div>
      ) : peek?.ok ? (
        <div className="mt-3 rounded-md bg-card px-2 py-1.5 text-[11px] leading-5 text-ink/85">
          <div className="mb-1 text-[10px] text-sub">
            {peek.matched ? t('dtl_peek_hit') : t('dtl_peek_head')}
          </div>
          <Marked text={peek.text || ''} />
        </div>
      ) : peek && !peek.ok ? (
        <div className="mt-3 rounded-md bg-card px-2 py-1.5 text-[11px] leading-5 text-sub">
          {peek.why === 'not_indexed'
            ? t('dtl_no_body')
            : peek.why === 'no_body'
              ? t('dtl_no_text')
              : t('dtl_peek_fail')}
        </div>
      ) : !canPeek ? null : (
        <button
          onClick={loadPeek}
          disabled={peeking}
          className="mt-3 w-full rounded-md border border-line px-2 py-1.5 text-[11px] text-sub hover:text-ink disabled:opacity-60"
        >
          {peeking ? t('dtl_reading') : t('dtl_read_body')}
        </button>
      )}
      <div className="mt-4 flex flex-wrap gap-1.5">
        <button onClick={onOpen} className="rounded-md bg-accent px-2.5 py-1 text-[11px] text-white hover:opacity-90">
          {t('ctx_open')}
        </button>
        <button onClick={onReveal} className="rounded-md border border-line px-2.5 py-1 text-[11px] hover:bg-card">
          {t('dtl_reveal')}
        </button>
        <button onClick={onCopy} className="rounded-md border border-line px-2.5 py-1 text-[11px] hover:bg-card">
          {t('ctx_copy')}
        </button>
      </div>
      <div className="mt-3 text-[10px] leading-4 text-sub">{t('dtl_hint')}</div>
    </aside>
  )
}

export function Checkmark() {
  return <IconCheck size={13} />
}
