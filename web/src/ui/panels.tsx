import { useEffect, useMemo, useRef, useState, type ChangeEvent, type MouseEvent as ReactMouseEvent } from 'react'
import {
  getAutostart,
  getErrlog,
  errOp,
  downloadSettingsBackup,
  restoreSettingsBackup,
  type ErrState,
  getConfig,
  getLicense,
  getPing,
  getSettings,
  getStats,
  getTimeline,
  saveSettings,
  setAutostart,
  getDiagnose,
  getHealth,
  getLibsets,
  getSaved,
  libsetOp,
  retryDiagnose,
  savedOp,
  searchAllLibs,
  type AllHit,
  type Diagnose,
  type Health,
  type HealthItem,
  type LibSet,
  type SavedItem,
  type ConfigInfo,
  type LicenseState,
  type Ping,
  type Settings,
  type Stats,
  type TimelineGroup,
  activateLicense,
  applyUpdate,
  checkUpdate,
  clearLicense,
  copyText,
  openUrl,
  saveConfig,
  updateStatus,
  type UpdateInfo,
} from '../api'
import { baseName, dayLabel, dirName, fmtSize, fmtTime, midName } from '../lib/hits'
import { IconCheck, IconClose, IconCoffee, IconDoc, IconFile, IconFolder, IconGear, IconImage, IconZip } from './icons'
import { Maintenance } from './maintenance'
import { AppearanceSection } from './appearance'
import type { WallState } from '../api'
import { t, LANGS, setLang, getLang } from '../lib/i18n'

/* ---------------------------------- 设置 ---------------------------------- */

type Props = {
  open: boolean
  onClose: () => void
  /** 主题配色 id（第 191 项）：auto / light / dark + 12 套独立配色 */
  palette: string
  onPalette: (id: string) => void
  /** 全局壁纸状态（第 191 项）：图片本身在后端，这里只有开关与两个滑杆 */
  wall: WallState
  onWall: (w: WallState) => void
  /** 维护任务跑完时通知外面刷新页脚数字 */
  onRefreshed?: () => void
  /** 「更多设置」里打开整本 OCR 面板（主工具栏不再放这个低频按钮） */
  onOpenOcr?: () => void
  ocrRunning?: boolean
  /** 点「已保存的搜索」时把关键词送回主界面 */
  onPickSearch?: (q: string) => void
  currentQ?: string
}

export function SettingsDrawer({ open, onClose, palette, onPalette, wall, onWall, onRefreshed, onPickSearch, currentQ, onOpenOcr, ocrRunning }: Props) {
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

      {/* 外观（第 191 项）：主题配色 + 全局壁纸。
          这两块将来要整体切成 Pro，所以表现层收在 ui/appearance.tsx 一个组件里；
          现在按红线**全部免费**，这里不加任何门控。 */}
      <AppearanceSection palette={palette} onPalette={onPalette} wall={wall} onWall={onWall} />

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

      {/* 维护工具是常用操作，紧跟「索引范围」：看完扫了哪些盘，下一步就是重扫/体检 */}
      <Maintenance onDone={onRefreshed} />

      <details className="group mt-3 border-t border-line pt-3">
        <summary className="flex cursor-pointer list-none items-center justify-between">
          <span className="text-[12px] font-semibold">{t('more_title')}</span>
          <span className="text-[10px] text-sub">{t('more_hint')} ▾</span>
        </summary>
        <div className="mt-2">
          {/* 环境体检与错误日志已整体搬到标题栏的「体检与报错」弹窗（第 187 项）：
              同一个东西在两处出现，用户会以为设置里那份才是"完整版"。 */}
          <CarePanel
            compat={!!s.compat}
            onCompat={(v) => patch({ compat: v })}
            titlebar={s.titlebar !== false}
            onTitlebar={(v) => patch({ titlebar: v })}
          />

          <ExtrasPanel onPickSearch={onPickSearch} currentQ={currentQ} onRefreshed={onRefreshed} />

          {onOpenOcr && (
            <button onClick={onOpenOcr} title={t('ocr_title')}
              className="mt-3 flex w-full items-center justify-between rounded-md border border-line px-2 py-1.5 text-[11px] text-sub hover:bg-card hover:text-ink">
              <span>{t('ocr_btn')}</span>
              {ocrRunning && <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent" />}
            </button>
          )}
        </div>
      </details>
    </aside>
  )
}

/* --------- 常规：兼容模式 / 设置备份 / 错误日志（截图对标，全部免费） --------- */
const LEVEL_STYLE: Record<HealthItem['level'], { dot: string; tag: string; text: string }> = {
  FAIL: { dot: 'bg-red-500', tag: 'border-red-500/50 text-red-500', text: 'text-red-500' },
  WARN: { dot: 'bg-amber-500', tag: 'border-amber-500/50 text-amber-600', text: 'text-amber-600' },
  OK: { dot: 'bg-emerald-500', tag: 'border-emerald-500/40 text-emerald-600', text: 'text-sub' },
  INFO: { dot: 'bg-slate-400', tag: 'border-line text-sub', text: 'text-sub' },
}
const LEVEL_ORDER: HealthItem['level'][] = ['FAIL', 'WARN', 'OK', 'INFO']

/** 环境体检（第 172 项）。
 *
 *  后端 env_check.probe_all/judge 早就有完整探测（WebView2 / 数据目录 / 屏幕缩放 /
 *  数据库 quick_check / 端口…），但前端一直没显示，用户只能翻 debug.log。
 *  这里把 `issues` 直接铺开：每条带一句人话和一条修复建议，出问题能自己先查一遍。
 */
export function HealthPanel() {
  const [st, setSt] = useState<Health | null>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const run = (force: boolean) => {
    setBusy(true)
    setCopied(false)
    getHealth(force, getLang())
      .then((d) => setSt(d))
      .catch(() => setSt({ ok: false, msg: 'request failed' }))
      .finally(() => setBusy(false))
  }
  useEffect(() => {
    run(false)
  }, [])

  const items = useMemo(() => {
    const list = st?.issues || []
    return [...list].sort(
      (a, b) => LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level),
    )
  }, [st])

  const bad = items.filter((x) => x.level === 'FAIL').length
  const warn = items.filter((x) => x.level === 'WARN').length

  const copyAll = async () => {
    try {
      await copyText(st?.text || '')
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="mt-1 rounded-lg border border-line/70 bg-card/50 p-2.5">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="text-[12px] font-semibold">{t('hc_title')}</span>
        {st?.ok && (
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] ${
              bad ? 'bg-red-500 text-white' : warn ? 'bg-amber-500 text-white' : 'border border-line text-sub'
            }`}
          >
            {bad || warn
              ? t('hc_need').replace('{n}', String(bad + warn))
              : t('hc_all_ok')}
          </span>
        )}
        <button
          disabled={busy}
          onClick={() => run(true)}
          className="ml-auto rounded border border-line px-1.5 py-0.5 text-[11px] hover:bg-card disabled:opacity-60"
        >
          {busy ? t('hc_checking') : t('hc_run')}
        </button>
        <button
          disabled={busy || !st?.ok}
          onClick={() => void copyAll()}
          className="rounded border border-line px-1.5 py-0.5 text-[11px] hover:bg-card disabled:opacity-60"
        >
          {copied ? t('hc_copied') : t('hc_copy')}
        </button>
      </div>
      <div className="mb-1.5 text-[10px] leading-4 text-sub/80">{t('hc_hint')}</div>

      {!st && <div className="text-[11px] text-sub">{t('hc_checking')}</div>}
      {st && !st.ok && (
        <div className="text-[11px] text-red-500">
          {t('hc_fail')}：{st.msg || 'unknown'}
        </div>
      )}
      {st?.ok && (
        <div className="space-y-1">
          {items.map((x) => {
            const c = LEVEL_STYLE[x.level] || LEVEL_STYLE.INFO
            return (
              <div key={x.key} className="flex items-start gap-1.5">
                <span className={`mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full ${c.dot}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-1.5">
                    <span className={`text-[11.5px] ${c.text}`}>{x.title}</span>
                    <span className={`rounded border px-1 text-[9.5px] ${c.tag}`}>{x.level}</span>
                  </div>
                  {x.detail && (
                    <div className="text-[10px] leading-4 text-sub/80">{x.detail}</div>
                  )}
                  {x.fix && (
                    <div className="text-[10px] leading-4 text-accent/90">
                      {t('hc_fix')}：{x.fix}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** 后端 diagnose 的 reason 是中文枚举（库里就这么存的），展示时按语言映射（第 174 项）。
 *  未收录的新 reason 原样显示，不会变成空白。 */
const whyText = (why: string): string => {
  switch (why) {
    case '无文本':
      return t('why_notext')
    case '空文件':
      return t('why_empty')
    case '临时文件':
      return t('why_temp')
    case 'doc 旧版格式，需另存为 docx/xlsx/pptx':
      return t('why_doc_old')
    case 'ppt 旧版格式，需另存为 docx/xlsx/pptx':
      return t('why_ppt_old')
    case '加密':
      return t('why_enc')
    default:
      return why
  }
}

/** 错误日志（第 181 项）：从设置里抽出来，成为能独立渲染的组件。
 *
 * 为什么抽：它原来长在 CarePanel 里，所以「体检」「报错」只能点进设置才看得见。
 * 现在顶栏那个「体检与报错」按钮直接把两块都拿出来，自己开弹窗 ——
 * 组件自包含 state（不再依赖 CarePanel 的 st/busy/msg），两处渲染互不干扰。
 */
export function ErrorLogPanel() {
  const [st, setSt] = useState<ErrState | null>(null)
  const [collect, setCollect] = useState(true)
  const [ingest, setIngest] = useState(false)
  const [preview, setPreview] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [fbOpen, setFbOpen] = useState(false)
  const [fbText, setFbText] = useState('')

  const reload = () => {
    getErrlog()
      .then((r) => {
        setSt(r)
        setCollect(r.conf.collect)
        // 只取「开发者是否配置好接收端」这个布尔值，地址永远不回传前端
        setIngest(r.conf.ingest)
        setPreview(r.preview || '')
      })
      .catch(() => {})
  }
  useEffect(reload, [])

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMsg('')
    try {
      await fn()
    } catch {
      setMsg(t('ex_fail'))
    }
    setBusy(false)
    reload()
  }

  const saveConf = (v?: boolean) => run(async () => {
    const r = await errOp('set', { collect: typeof v === 'boolean' ? v : collect })
    setMsg(r.ok ? t('ex_ok') : t('bk_fail').replace('{e}', r.err || ''))
  })

  const doOp = (op: string) => run(async () => {
    if ((op === 'export' || op === 'send' || op === 'preview') && !st?.items.length) {
      setMsg(t('err_no_report'))
      return
    }
    const r = await errOp(op)
    if (r.text) setPreview(r.text)
    if (op === 'export') setMsg(r.ok ? r.path || t('ex_ok') : t('ex_fail'))
    else if (op === 'send') setMsg(r.ok ? t('ex_ok') : t('bk_fail').replace('{e}', r.err || ''))
    else if (op === 'clear') setMsg(t('ex_ok'))
  })

  return (
    <div>
      {/* 这个 id 是「反馈 / 报错」的落点（第 175 / 178 项）：必须挂在错误报告块本身，
          挂在 CarePanel 外框上会滚到「常规」顶部，错误日志仍在下面看不见 */}
      <div id="feedback" className="mt-3 mb-1 text-[11px] font-semibold">{t('er_title')}</div>
      <div className="mb-2 text-[10px] leading-4 text-sub">{t('err_hint')}</div>
      <label className="flex items-center gap-2 py-1 text-[11px]">
        <input type="checkbox" checked={collect}
          onChange={(e) => { const v = e.target.checked; setCollect(v); void saveConf(v) }} />
        {t('err_collect')}
      </label>
      <div className="mb-2 rounded-md bg-card/60 px-2 py-1.5 text-[10px] leading-4 text-sub">
        {t('err_collect_hint')}
      </div>
      {!ingest && (
        <div className="mb-1 text-[10px] leading-4 text-sub">{t('err_no_ingest')}</div>
      )}
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        <button disabled={busy} onClick={() => { setFbOpen(v => !v); setMsg('') }}
          className="rounded-md bg-accent px-2 py-1 text-[11px] text-white hover:opacity-90">
          {t('err_fb_open')}
        </button>
        <button disabled={busy || !ingest} onClick={() => void doOp('send')}
          className="rounded-md border border-line px-2 py-1 text-[11px] hover:bg-card">{t('err_send')}</button>
        <button disabled={busy} onClick={() => void doOp('export')}
          className="rounded-md border border-line px-2 py-1 text-[11px] hover:bg-card">{t('err_export')}</button>
      </div>
      {fbOpen && (
        <div className="mt-1.5 rounded-md border border-line bg-card/40 p-2">
          <div className="mb-1 text-[10px] leading-4 text-sub">{t('err_fb_hint')}</div>
          <textarea value={fbText} onChange={(e) => setFbText(e.target.value)}
            placeholder={t('err_fb_ph')} maxLength={2000} rows={3}
            className="w-full rounded-md border border-line bg-card px-2 py-1 text-[11px]" />
          <div className="mt-1.5 flex gap-1.5">
            <button disabled={busy || !fbText.trim()}
              onClick={() => void run(async () => {
                const r = await errOp('feedback', { text: fbText })
                if (r.ok) { setFbText(''); setFbOpen(false); setMsg(t('ex_ok')) }
                else setMsg(t('bk_fail').replace('{e}', r.err || ''))
              })}
              className="rounded-md bg-accent px-2 py-1 text-[11px] text-white disabled:opacity-60">{t('err_fb_save')}</button>
            <button disabled={busy} onClick={() => { setFbOpen(false); setFbText('') }}
              className="rounded-md border border-line px-2 py-1 text-[11px] hover:bg-card">{t('err_fb_cancel')}</button>
          </div>
        </div>
      )}
      <div className="mt-1.5 text-[10px] text-sub">
        {t('err_count').replace('{n}', String(st?.items.length || 0))}
        {' · '}{t('err_last').replace('{n}', String(st?.conf.sent || 0))}
      </div>
      {!fbOpen && !st?.items.length && (
        <div className="mt-1 text-[10px] leading-4 text-sub">{t('err_no_report')}</div>
      )}
      <pre className="mt-1.5 max-h-36 overflow-auto rounded-md border border-line bg-card/40 p-2 text-[10px] leading-4 whitespace-pre-wrap">
        {preview || t('err_empty')}
      </pre>
      {msg && <div className="pt-1.5 text-[10px] leading-4 text-accent">{msg}</div>}
    </div>
  )
}

/** 体检与报错（第 181 项）：一个独立弹窗，不再往设置里跳。
 *
 * 用户的原话是「点击后不要跑到设置里面去，他自己要能显示出来」——
 * 原来这两块都长在设置抽屉的「更多设置」折叠里，出问题时要从顶栏点进设置、
 * 再展开更多、再往下翻，等于藏起来。现在点一下直接开这个弹窗。
 *
 * 两块内容都是现成组件（HealthPanel / ErrorLogPanel），这里只负责排版：
 * 体检在上（先看有没有硬故障），错误日志在下（再看要不要上报）。
 */
export function DiagnosticsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  // GitHub 议题入口从「关于」搬到这里（第 184 项）：报错的人不会先去关于页翻链接。
  // 地址仍来自 /api/ping（后端常量），点开走 /api/open_url 白名单；打不开就复制。
  const [issues, setIssues] = useState('')
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!open) return
    getPing()
      .then((p) => setIssues(p.issues || ''))
      .catch(() => {})
  }, [open])
  if (!open) return null
  return (
    <Modal title={t('diag_title')} onClose={onClose} width={620}>
      <div className="text-[11px] leading-4 text-sub">{t('diag_hint')}</div>
      <div className="mt-3">
        <HealthPanel />
      </div>
      <div className="mt-4 border-t border-line pt-3">
        <ErrorLogPanel />
      </div>
      {issues && (
        <div className="mt-3 flex items-center gap-2 rounded-md border border-line/70 px-2.5 py-1.5">
          <span className="min-w-0 text-[11px] leading-4 text-sub">{t('diag_issue_hint')}</span>
          <button
            onClick={async () => {
              const ok = await openUrl(issues)
              if (!ok) {
                copyText(issues)
                setCopied(true)
              }
            }}
            title={issues}
            className="ml-auto shrink-0 rounded-md border border-line px-2 py-0.5 text-[11px] hover:border-accent hover:text-ink"
          >
            {copied ? t('hc_copied') : t('about_issues')}
          </button>
        </div>
      )}
    </Modal>
  )
}

/** 「为什么搜不到」弹窗（第 187 项）。
 *
 * 这块原来是设置 → 更多设置 → 索引诊断，帮助里的按钮只负责「跳到设置再滚过去」——
 * 那个 scrollIntoView 还踩过坑（诊断块在折叠的 <details> 里，滚了等于没滚）。
 * 用户现在的意思很明确：帮助里已经有按钮了，就别往设置里绕。点了直接弹这个。
 *
 * 自包含 state：自己拉 /api/diagnose、自己重试，不依赖设置那边的任何状态。
 */
export function WhyNotFoundModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [diag, setDiag] = useState<Diagnose | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    if (!open) return
    setMsg('')
    getDiagnose(60).then(setDiag).catch(() => {})
  }, [open])

  const retry = async () => {
    setBusy(true)
    setMsg('')
    try {
      await retryDiagnose()
      setDiag(await getDiagnose(60))
    } catch {
      setMsg(t('ex_fail'))
    }
    setBusy(false)
  }

  if (!open) return null
  return (
    <Modal onClose={onClose} title={t('ex_diag_title')} width={560}>
      <div className="mb-2 flex items-start justify-between gap-3">
        <p className="text-[12px] leading-5 text-sub">{t('why_modal_hint')}</p>
        <button
          disabled={busy}
          onClick={() => void retry()}
          className="shrink-0 rounded-md border border-line px-2.5 py-1 text-[12px] hover:bg-card disabled:opacity-60"
        >
          {t('ex_diag_retry')}
        </button>
      </div>

      {diag === null ? (
        <div className="py-8 text-center text-[12px] text-sub">{t('checking')}</div>
      ) : diag.total === 0 ? (
        <div className="rounded-lg border border-dashed border-line px-3 py-8 text-center text-[12px] leading-5 text-sub">
          {t('ex_diag_empty')}
        </div>
      ) : (
        <>
          <div className="mb-1.5 text-[12px] text-sub">
            {t('ex_diag_total').replace('{n}', String(diag.total))}
          </div>
          <div className="max-h-40 overflow-auto rounded-lg bg-card/60 p-2 text-[11px] leading-5 text-sub">
            {diag.groups.map((g) => (
              <div key={g.why} className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate">{whyText(g.why)}</span>
                <span className="shrink-0 tabular-nums">{g.n}</span>
              </div>
            ))}
          </div>
          <div className="mt-2 max-h-44 overflow-auto rounded-lg border border-line/70 p-2 text-[11px] leading-5 text-sub">
            {diag.items.slice(0, 30).map((x) => (
              <div key={x.p} className="truncate" title={x.p}>
                {x.p.split(/[\\/]/).pop()} · {whyText(x.why)}
              </div>
            ))}
          </div>
        </>
      )}

      {msg && <div className="pt-2 text-[11px] text-red-500">{msg}</div>}
    </Modal>
  )
}

export function CarePanel({
  compat,
  onCompat,
  titlebar,
  onTitlebar,
}: {
  compat: boolean
  onCompat: (v: boolean) => void
  /** 自绘标题栏（第 180 项）：默认开，关掉退回系统标题栏 */
  titlebar: boolean
  onTitlebar: (v: boolean) => void
}) {
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMsg('')
    try {
      await fn()
    } catch {
      setMsg(t('ex_fail'))
    }
    setBusy(false)
  }

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    void run(async () => {
      if (f.size > 350 * 1024) {
        setMsg(t('bk_fail').replace('{e}', 'file too large'))
        return
      }
      const bytes = new Uint8Array(await f.arrayBuffer())
      let bin = ''
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
      const r = await restoreSettingsBackup(btoa(bin))
      setMsg(r.ok
        ? t('bk_ok').replace('{n}', String(r.restored?.length || 0))
        : t('bk_fail').replace('{e}', r.err || ''))
    })
    e.target.value = ''
  }

  return (
    <section className="border-t border-line pt-3 mt-3">
      <div className="mb-1.5 text-[12px] font-semibold">{t('care_title')}</div>
      <div className="mb-2 text-[10px] leading-4 text-sub">{t('care_hint')}</div>

      <label className="flex items-start gap-2 py-1 text-[11px]">
        <input type="checkbox" checked={compat}
          onChange={(e) => onCompat(e.target.checked)} className="mt-0.5" />
        <span>
          {t('compat_on')}
          <span className="block text-[10px] leading-4 text-sub">{t('compat_hint')}</span>
        </span>
      </label>

      {/* 自绘标题栏（第 180 项）：窗口样式启动时定，所以这里标注「重启生效」。
          和 Python 读的是 settings.json 同一个键，改完下次启动两边一起变。 */}
      <label className="flex items-start gap-2 py-1 text-[11px]">
        <input type="checkbox" checked={titlebar}
          onChange={(e) => onTitlebar(e.target.checked)} className="mt-0.5" />
        <span>
          {t('care_titlebar')}
          <span className="block text-[10px] leading-4 text-sub">{t('care_titlebar_hint')}</span>
        </span>
      </label>

      <div className="mt-3 mb-1 text-[11px] font-semibold">{t('bk_title')}</div>
      <div className="mb-1.5 text-[10px] leading-4 text-sub">{t('bk_hint')}</div>
      <div className="flex flex-wrap gap-1.5">
        <button disabled={busy} onClick={() => void run(async () => {
          setMsg((await downloadSettingsBackup()) ? t('ex_ok') : t('ex_fail'))
        })} className="rounded-md border border-line px-2 py-1 text-[11px] hover:bg-card disabled:opacity-60">
          {t('bk_save')}
        </button>
        <label className="rounded-md border border-line px-2 py-1 text-[11px] hover:bg-card cursor-pointer">
          {t('bk_load')}
          <input type="file" accept=".zip" className="hidden" onChange={onFile} />
        </label>
      </div>

      {msg && <div className="pt-1.5 text-[10px] leading-4 text-accent">{msg}</div>}
    </section>
  )
}

/* --------- 索引集 / 保存搜索 / 索引诊断：三个免费新区块（0.8 开工令） --------- */
function ExtrasPanel({
  onPickSearch,
  currentQ,
  onRefreshed,
}: {
  onPickSearch?: (q: string) => void
  currentQ?: string
  onRefreshed?: () => void
}) {
  const [libs, setLibs] = useState<LibSet[]>([])
  const [newName, setNewName] = useState('')
  const [allQ, setAllQ] = useState('')
  const [allHits, setAllHits] = useState<AllHit[] | null>(null)
  const [saved, setSaved] = useState<SavedItem[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const reload = () => {
    getLibsets().then((r) => setLibs(r.libs || [])).catch(() => {})
    getSaved().then((r) => setSaved(r.items || [])).catch(() => {})
  }
  useEffect(() => { reload() }, [])

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      setMsg(t('ex_ok'))
      reload()
      onRefreshed?.()
    } catch {
      setMsg(t('ex_fail'))
    } finally {
      setBusy(false)
    }
  }
  const runAll = () => {
    const v = allQ.trim()
    if (!v) return
    searchAllLibs(v).then((r) => setAllHits(r.hits || [])).catch(() => setAllHits([]))
  }

  return (
    <div className="mt-3 space-y-2">
      <div className="rounded-lg border border-line/70 bg-card/50 p-2.5">
        <div className="mb-1.5 text-[12px] font-semibold">{t('ex_lib_title')}</div>
        {libs.map((l) => (
          <div key={l.id} className="flex items-center gap-1.5 py-0.5 text-[11px]">
            <span className={`min-w-0 flex-1 truncate ${l.active ? 'text-accent' : 'text-ink'}`}>
              {l.id === 'default' ? t('lib_default') : l.name}
              {l.active ? ' · ' + t('ex_lib_active') : ''}
            </span>
            <span className="shrink-0 text-sub">{l.files.toLocaleString()}</span>
            {!l.active && (
              <button disabled={busy} className="shrink-0 text-accent hover:underline"
                onClick={() => void run(() => libsetOp('switch', { id: l.id }))}>
                {t('ex_lib_switch')}
              </button>
            )}
            {l.id !== 'default' && (
              <button disabled={busy} className="shrink-0 text-sub hover:underline"
                onClick={() => void run(() => libsetOp('delete', { id: l.id }))}>
                {t('ex_lib_del')}
              </button>
            )}
          </div>
        ))}
        <div className="mt-1.5 flex gap-1">
          <input value={newName} onChange={(e) => setNewName(e.target.value)}
            placeholder={t('ex_lib_ph')}
            className="min-w-0 flex-1 rounded border border-line bg-transparent px-1.5 py-0.5 text-[11px]" />
          <button disabled={busy || !newName.trim()}
            className="shrink-0 rounded bg-accent px-1.5 text-[11px] text-white disabled:opacity-60"
            onClick={() => void run(async () => {
              const r = await libsetOp('create', { name: newName.trim() })
              setNewName('')
              if (r.lib) await libsetOp('switch', { id: r.lib.id })
            })}>
            {t('ex_lib_new')}
          </button>
        </div>
        <div className="mt-2 flex gap-1">
          <input value={allQ} onChange={(e) => setAllQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') runAll() }}
            placeholder={t('ex_lib_all_ph')}
            className="min-w-0 flex-1 rounded border border-line bg-transparent px-1.5 py-0.5 text-[11px]" />
          <button onClick={runAll} className="shrink-0 rounded border border-line px-1.5 text-[11px]">
            {t('ex_lib_all')}
          </button>
        </div>
        {allHits !== null && (
          <div className="mt-1 max-h-28 overflow-auto text-[10px] leading-4 text-sub">
            {allHits.length === 0 ? t('ex_lib_no_hit') : allHits.map((h, i) => (
              <div key={i} className="truncate" title={h.p}>{h.n} · {h.p.split(/[\\/]/).pop()}</div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-lg border border-line/70 bg-card/50 p-2.5">
        <div className="mb-1.5 text-[12px] font-semibold">{t('ex_saved_title')}</div>
        {saved.length === 0 && <div className="text-[11px] text-sub">{t('ex_saved_empty')}</div>}
        {saved.map((x) => (
          <div key={x.q} className="flex items-center gap-1.5 py-0.5 text-[11px]">
            <button className="min-w-0 flex-1 truncate text-left text-accent hover:underline"
              onClick={() => onPickSearch?.(x.q)}>{x.name}</button>
            <button className="shrink-0 text-sub hover:underline"
              onClick={() => void run(() => savedOp('remove', { q: x.q }))}>{t('ex_lib_del')}</button>
          </div>
        ))}
        <button disabled={busy || !currentQ?.trim()}
          className="mt-1 rounded border border-line px-1.5 py-0.5 text-[11px] disabled:opacity-60"
          onClick={() => void run(() => savedOp('add', { q: currentQ?.trim() || '' }))}>
          {t('ex_saved_add')}
        </button>
      </div>

      {/* 「为什么搜不到」不在这里了（第 187 项）：使用帮助里有按钮，点了直接开
          WhyNotFoundModal，和「体检与报错」一样不跳设置。 */}
      {msg && <div className="text-[10px] text-sub" role="status">{msg}</div>}
    </div>
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
]

const MORE = (): [string, string][] => [
  ['Empty', t('more1')],
  ['Enter', t('more6')],
  ['右键', t('more2')],
  ['drop', t('more3')],
  ['▦', t('more4')],
  ['托盘', t('more5')],
]

export function HelpModal({ open, onClose, onWhyNotFound, onReplayTour }: { open: boolean; onClose: () => void; onWhyNotFound?: () => void; onReplayTour?: () => void }) {
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
      <div className="flex flex-wrap justify-center gap-2 pt-4">
        <button
          onClick={() => { onClose(); onWhyNotFound?.() }}
          className="rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-white transition hover:opacity-90"
        >
          {t('help_why')}
        </button>
        <button
          onClick={() => { onClose(); onReplayTour?.() }}
          className="rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-white transition hover:opacity-90"
        >
          {t('help_tour')}
        </button>
      </div>
      <div className="pt-3 text-center text-[11px] text-sub">
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

/** 「赞助」弹窗（第 185 项）。
 *
 * 之前赞赏码是「关于」最底下的一个小方块：得把关于滚到底才看得见，
 * 还和版本号、授权、更新检查挤在一起。用户要求**做成独立按钮**，
 * 点开是独立弹窗（参考形态：左边档位列表 + 右边大二维码的卡片）。
 *
 * 三条纪律：
 * 1. 按钮在**标题栏 / 顶栏**上，红心 + 文字，一眼知道是赞赏；
 * 2. 点了开**自己的弹窗**，不跳「关于」；
 * 3. 防篡改校验照旧：指纹对不上就整块变红警告，绝不显示别人的收款码。
 *
 * 左栏只在**有多张收款码**时出现（assets/donate-6.png 这种文件名带数字的就是档位）；
 * 只有一张时就单栏居中 —— 空着一个只有一行的列表更难看。
 */
export function SupportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [items, setItems] = useState<DonateItem[] | null>(null)
  const [pick, setPick] = useState(0)
  const [zoom, setZoom] = useState(false)

  useEffect(() => {
    if (!open) return
    fetch('/api/donate', { headers: { 'Sec-Fetch-Site': 'same-origin' } })
      .then((r) => r.json())
      .then((d: { items?: DonateItem[] }) =>
        setItems((d.items || []).slice().sort((a, b) => a.amount - b.amount)),
      )
      .catch(() => setItems([]))
  }, [open])

  if (!open) return null

  const list = items || []
  const multi = list.length > 1
  const cur: DonateItem | undefined = list[Math.min(pick, Math.max(list.length - 1, 0))]
  const src = cur ? `/donate.png?f=${encodeURIComponent(cur.file)}` : ''
  // official 缺失（后端没配指纹）时不拦；明确 false 才拦
  const trusted = !!cur && cur.official !== false

  const tierBtn = (it: DonateItem, i: number) => (
    <button
      key={it.file}
      onClick={() => setPick(i)}
      title={donateNote(it.amount)}
      className={`flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left transition-colors ${
        i === pick ? 'bg-accent/10 ring-1 ring-accent/40' : 'hover:bg-card'
      }`}
    >
      <span
        className={`grid h-7 w-7 shrink-0 place-items-center rounded-md text-[10px] font-semibold ${
          i === pick ? 'bg-accent/15 text-accent' : 'bg-card text-sub'
        }`}
      >
        {it.amount > 0 ? `¥${it.amount}` : '¥'}
      </span>
      <span className="min-w-0 flex-1 truncate text-[12px] leading-4">{donateNote(it.amount)}</span>
    </button>
  )

  return (
    <Modal onClose={onClose} title={t('sp_title')} width={multi ? 636 : 476}>
      <div className={multi ? 'grid grid-cols-[204px_minmax(0,1fr)] gap-4' : 'flex flex-col'}>
        {multi && (
          <aside className="flex max-h-[404px] min-h-0 flex-col overflow-hidden rounded-xl border border-line">
            <div className="flex shrink-0 items-center gap-1.5 border-b border-line px-3 py-2">
              <span className="text-accent">
                <IconCoffee size={12} />
              </span>
              <span className="text-[12px] font-medium leading-none">{t('sp_tiers')}</span>
              <span className="ml-auto rounded-full bg-card px-1.5 py-0.5 text-[10px] leading-none text-sub">
                {list.length}
              </span>
            </div>
            <div className="min-h-0 flex-1 space-y-0.5 overflow-auto p-1.5">
              {list.map((it, i) => tierBtn(it, i))}
            </div>
            <div className="shrink-0 border-t border-line px-3 py-1.5 text-[10px] leading-4 text-sub">
              {t('sp_pick_hint')}
            </div>
          </aside>
        )}

        <div className="flex min-w-0 flex-col">
          <div className="flex items-center gap-1.5">
            {/* 第 188 项：红心 → 咖啡杯、红 → 主题色。用户原话「红色感觉不咋搭，
                现在的主题色是蓝白黑这样的」；心形也容易和"收藏"混。 */}
            <span className="text-accent">
              <IconCoffee size={16} />
            </span>
            <span className="text-[14px] font-semibold leading-5">{t('sp_head')}</span>
          </div>
          <p className="mt-1.5 text-[12px] leading-5 text-sub">{t('sp_body')}</p>

          <div className="mt-3">
            {items === null ? (
              <div className="grid h-[232px] place-items-center rounded-xl border border-line bg-card text-[11px] text-sub">
                {t('checking')}
              </div>
            ) : !list.length ? (
              <div className="grid h-[232px] place-items-center rounded-xl border border-dashed border-line px-4 text-center text-[11px] leading-5 text-sub">
                <div>
                  <div className="text-[12px] text-ink">{t('sp_none')}</div>
                  <div>{t('sp_none_hint')}</div>
                </div>
              </div>
            ) : !trusted ? (
              <div className="rounded-xl border border-red-300 bg-red-50 p-3 text-[11px] leading-5 text-red-700">
                <div className="font-semibold">{t('about_fp_bad')}</div>
                <div>{t('about_fp_bad_hint')}</div>
                {cur?.sha && <div className="font-mono opacity-70">{cur.sha.slice(0, 16)}</div>}
              </div>
            ) : (
              <button onClick={() => setZoom(true)} title={t('donate_zoom')} className="block w-full">
                <span className="grid place-items-center rounded-xl border border-line bg-gradient-to-br from-[#fff1f2] via-[#fff7ed] to-[#fef9c3] px-4 py-4 dark:from-[#2b1f24] dark:via-[#2a2320] dark:to-[#2b2718]">
                  <img
                    src={src}
                    alt={t('donate_alt')}
                    className="h-[210px] w-[210px] rounded-lg bg-white object-contain p-1 shadow-sm"
                  />
                </span>
              </button>
            )}
          </div>

          {list.length > 0 && trusted && (
            <p className="mt-2 text-center text-[11px] leading-4 text-sub">{t('sp_scan')}</p>
          )}
          {list.length > 0 && (
            <p className="mt-0.5 text-center text-[10px] leading-4 text-sub/80">{t('sp_note')}</p>
          )}

          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2 text-[10px] leading-4 text-sub">
            <span>{t('sp_footer')}</span>
            {cur?.sha && trusted && (
              <span className="font-mono text-[9px] text-sub/70" title={`SHA-256 ${cur.sha}`}>
                {t('about_fp')} {cur.sha.slice(0, 12)} · {t('about_fp_ok')}
              </span>
            )}
          </div>
        </div>
      </div>

      {zoom && cur && (
        <div
          className="fixed inset-x-0 bottom-0 z-[60] grid place-items-center bg-black/60 p-6"
          style={{ top: 'var(--sb-title, 0px)' }}
          onClick={() => setZoom(false)}
          title={t('donate_close')}
        >
          <img
            src={src}
            alt={t('donate_alt')}
            className="max-h-[86vh] max-w-[86vw] rounded-lg bg-white p-3 shadow-2xl"
          />
        </div>
      )}
    </Modal>
  )
}

/** 官方地址区：地址来自 /api/ping（后端常量），点击走 /api/open_url 白名单 */
function OfficialLinks({ ping }: { ping: Ping }) {
  const [copied, setCopied] = useState(false)
  if (!ping.repo) return null
  const rows: [string, string][] = [
    [t('about_repo'), ping.repo],
    [t('about_releases'), ping.releases || ''],
    [t('about_mirror'), ping.manual_page || ''],
    // 「GitHub 议题」已搬到「体检与报错」弹窗（第 184 项）：报错的人第一步不会来关于页
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
      <div className="grid grid-cols-[auto_1fr] gap-y-1">
        {rows
          .filter(([, u]) => !!u)
          .map(([label, u]) => (
            /* 两格拼成一行（第 189 项）：名称和地址分别落在同一张网格的两列上，
               **所有行的地址起点才是同一条竖线**。原来是一个 flex 行 + ml-auto，
               短的那条（源码仓库）被推到右边，两行的地址一头一尾，看着参差。
               display:contents 的壳只为 group-hover 存在，它自己不产生盒子。 */
            <div key={label} className="group contents">
              <button
                onClick={() => go(u)}
                title={u}
                className="flex items-center rounded-l-md border border-r-0 border-line/70 px-2 py-1 text-left text-[12px] group-hover:border-accent"
              >
                <span className="whitespace-nowrap">{label}</span>
              </button>
              <button
                onClick={() => go(u)}
                title={u}
                className="flex min-w-0 items-center rounded-r-md border border-l-0 border-line/70 px-2 py-1 text-left group-hover:border-accent"
              >
                <span className="truncate font-mono text-[10px] text-sub">{u}</span>
              </button>
            </div>
          ))}
      </div>
      {copied && <div className="pt-1 text-[10px] leading-4 text-sub">{t('about_open_fail')}</div>}
      <div className="pt-1.5 text-[10px] leading-4 text-sub/80">{t('about_only_official')}</div>
    </div>
  )
}

/** 授权区：免费版给输入框，Pro 显示署名和到期。**纯本地校验，不联网**。
 *
 *  设计上有意做成「低调的一小块」：免费用户不该为它分心，Pro 用户找得到就行。
 *  另外错误原因必须说清楚（格式错 / 签名错 / 过期），否则用户只会觉得「我买的东西坏了」。
 */
function LicenseBox({ open, pushedRight }: { open: boolean; pushedRight?: boolean }) {
  const [st, setSt] = useState<LicenseState | null>(null)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!open) return
    setMsg(null)
    setEditing(false)
    setExpanded(false)
    setCode('')
    getLicense()
      .then((r) => setSt(r.state))
      .catch(() => {})
  }, [open])

  const reasonText = (r: string) => {
    switch (r) {
      case 'empty':
        return t('lic_err_empty')
      case 'format':
        return t('lic_err_fmt')
      case 'signature':
        return t('lic_err_sig')
      case 'expired':
        return t('lic_err_exp')
      case 'hwid':
      case 'bound':
        return t('lic_err_hw')
      case 'revoked':
        return t('lic_err_revoked')
      case 'readonly':
        return t('lic_err_io')
      case 'version':
        return t('lic_err_ver')
      default:
        return t('lic_err_other')
    }
  }

  const doActivate = async () => {
    if (!code.trim()) {
      setMsg({ ok: false, text: t('lic_err_empty') })
      return
    }
    setBusy(true)
    try {
      const r = await activateLicense(code)
      setSt(r.state)
      if (r.ok) {
        setMsg({ ok: true, text: t('lic_ok') })
        setCode('')
        setEditing(false)
      } else {
        setMsg({ ok: false, text: reasonText(r.reason) })
      }
    } catch {
      setMsg({ ok: false, text: t('lic_err_other') })
    } finally {
      setBusy(false)
    }
  }

  const doClear = async () => {
    if (!window.confirm(t('lic_clear_ask'))) return
    try {
      const r = await clearLicense()
      setSt(r.state)
      setMsg(null)
      setCode('')
      setEditing(true)
    } catch {
      setMsg({ ok: false, text: t('lic_err_other') })
    }
  }

  if (!st) return null
  const active = st.valid && st.edition === 'pro'
  const showInput = expanded && (!active || editing)

  // 第 168 项：授权不再单独占一整块，改成「版本」那一行右侧的小胶囊；
  // 点胶囊才展开输入框 —— 免费用户不必天天看它，Pro 用户点一下就能换码。
  return (
    <>
      <button
        onClick={() => setExpanded((v) => !v)}
        title={t('lic_title')}
        className={`${
          pushedRight ? 'ml-auto' : ''
        } flex shrink-0 items-center gap-1.5 rounded-md border border-line px-2 py-1 text-[11px] hover:bg-card`}
      >
        <span className="text-sub">{t('lic_title')}</span>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] ${
            active ? 'bg-accent text-white' : 'border border-line text-sub'
          }`}
        >
          {active ? 'Pro' : t('lic_free')}
        </span>
        {active && <span className="text-sub">{st.exp || t('lic_forever')}</span>}
      </button>

      {expanded && (
        <div className="w-full space-y-0.5 pt-0.5">
          {active && st.name && (
            <div className="truncate text-[11px] text-ink">{st.name}</div>
          )}

          {active && !editing && (
            <div className="space-y-0.5 text-[11px] leading-5 text-sub">
              <div>
                {st.exp ? `${t('lic_exp')} ${st.exp}` : t('lic_forever')}
                {st.days_left !== null && st.days_left >= 0
                  ? ` · ${t('lic_days').replace('{n}', String(st.days_left))}`
                  : ''}
              </div>
              <div
                className="truncate font-mono text-[10px] opacity-70"
                title={t('lic_masked_hint')}
              >
                {st.code_masked}
              </div>
              {st.clock_warn && <div className="text-amber-600">{t('lic_clock')}</div>}
            </div>
          )}

          {showInput && (
            <div className="space-y-1.5 pt-1">
              <input
                value={code}
                onChange={(e) => {
                  setCode(e.target.value)
                  setMsg(null)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') doActivate()
                }}
                placeholder={t('lic_ph')}
                spellCheck={false}
                className="w-full rounded-md border border-line bg-card px-2 py-1 font-mono text-[11px] outline-none focus:border-accent"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={doActivate}
                  disabled={busy}
                  className="rounded-md bg-accent px-2.5 py-1 text-[12px] text-white hover:opacity-90 disabled:opacity-60"
                >
                  {busy ? t('lic_busy') : t('lic_act')}
                </button>
                {active && editing && (
                  <button
                    onClick={() => {
                      setEditing(false)
                      setCode('')
                      setMsg(null)
                    }}
                    className="rounded-md border border-line px-2.5 py-1 text-[12px] hover:bg-card"
                  >
                    {t('lic_cancel')}
                  </button>
                )}
                <span className="ml-auto text-[10px] text-sub/70">{t('lic_offline')}</span>
              </div>
              <div className="text-[10px] leading-4 text-sub/80">{t('lic_hint')}</div>
            </div>
          )}

          {msg && (
            <div className={`pt-0.5 text-[11px] ${msg.ok ? 'text-accent' : 'text-red-500'}`}>
              {msg.text}
            </div>
          )}

          {active && !editing && (
            <div className="flex gap-3 pt-1">
              <button
                onClick={() => setEditing(true)}
                className="text-[10px] text-sub underline-offset-2 hover:underline"
              >
                {t('lic_change')}
              </button>
              <button
                onClick={doClear}
                className="text-[10px] text-sub underline-offset-2 hover:underline"
              >
                {t('lic_clear')}
              </button>
            </div>
          )}
        </div>
      )}
    </>
  )
}

export function AboutModal({
  open,
  onClose,
  onSupport,
}: {
  open: boolean
  onClose: () => void
  /** 打开独立的「赞助」弹窗（第 185 项）；关于里只留入口，不再内嵌二维码 */
  onSupport: () => void
}) {
  const [stats, setStats] = useState<Stats>({ files: 0, docs: 0 })
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

      {/* 检查更新 + 授权：同一行，授权用胶囊占住右侧空位（第 168 项） */}
      <div className="border-t border-line pt-3">
        <div className="mb-2 text-[12px] text-sub">{t('about_ver2')}</div>
        <div className="flex flex-wrap items-center gap-2">
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
          <LicenseBox open={open} pushedRight={!up.info?.newer} />
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

      {/* 支持一下：第 185 项起**只留一个入口按钮**，二维码搬进独立弹窗（SupportModal）。
          原来当小方块埋在关于最底下，得滚到底才看得见 —— 用户实测反馈。 */}
      <div className="mt-3 border-t border-line pt-3">
        <div className="mb-1.5 text-[12px] text-sub">{t('about_support')}</div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={onSupport}
            data-act="support-about"
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-[12px] hover:bg-card"
          >
            <span className="text-accent">
              <IconCoffee size={13} />
            </span>
            {t('sponsor')}
          </button>
          <div className="min-w-0 flex-1 text-[11px] leading-5 text-sub">
            {t('about_free')}
            <br />
            {t('about_thanks')}
          </div>
        </div>
      </div>
    </Modal>
  )
}

export function Modal({
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
    /* 遮罩从标题栏底下开始（第 189 项）：窗口最顶上 8-9px 是 DWM 的 caption 带，
       盖不到它；遮罩要是连标题栏一起压暗，上面就会剩一条发白、下面发灰 —— 看着像双层。
       顶部留出 --sb-title（自绘标题栏高度），整条标题栏保持窗口底色。 */
    <div
      className="absolute inset-x-0 bottom-0 z-30 grid place-items-center bg-black/25"
      style={{ top: 'var(--sb-title, 0px)' }}
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        style={{ width }}
        className="max-h-[calc(100%-32px)] max-w-[94vw] overflow-auto rounded-2xl border border-line bg-bg shadow-2xl"
      >
        {/* 标题栏常驻（第 177 项）：关于面板往下翻到底部二维码时标题会滚走，
            和右侧设置栏不一致。这里把标题粘在滚动容器顶部，正文单独一层 p-5。
            bg-bg 必须和弹窗同色 —— 不然滚动内容会从标题底下透出来。 */}
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-t-2xl border-b border-line bg-bg px-5 py-3">
          <span className="text-[15px] font-semibold">{title}</span>
          <button onClick={onClose} className="text-sub hover:text-ink" aria-label={t('close')}>
            <IconClose size={16} />
          </button>
        </div>
        <div className="p-5">{children}</div>
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

export function timelineCaption(days: number, total: number): string {
  const L = getLang()
  const n = total.toLocaleString()
  if (L === 'zh') return `最近 ${days} 天 · 每天列出前 40 个（共 ${n} 条记录）`
  if (L === 'es') return `${days} días · primeros 40 por día (${n} en total)`
  if (L === 'pt') return `${days} dias · primeiros 40 por dia (${n} no total)`
  if (L === 'hi') return `${days} दिन · रोज़ पहले 40 (कुल ${n})`
  return `${days} days · first 40 per day (${n} in total)`
}

export function Timeline({
  onPick,
  kind = 'all',
  sort = 'new',
  view = 'list',
  onHover,
  selected,
  onMenu,
  days = 14,
  onTotal,
}: {
  onPick: (p: string) => void
  /** 时间轴天数由顶栏控制（第 179 项）：说明行与「7/30 天」搬到顶栏那一行，
      state 跟着上提，否则两处各存一份会互相打架。这里只读。 */
  days?: number
  /** 把「共 N 条记录」报给顶栏，说明行在那边渲染 */
  onTotal?: (n: number) => void
  kind?: string
  /** 排序：时间轴默认按时间倒序，用户换排序时这里也跟着变，否则按钮像坏的 */
  sort?: 'rel' | 'new' | 'old' | 'big' | 'name'
  view?: 'list' | 'grid'
  /** 鼠标划到某一行 → 右侧详情面板跟着显示（没搜索结果时也能用详情） */
  onHover?: (row: { p: string; n?: string; s?: number; m?: number }) => void
  selected?: string
  /** 右键 → 交给 App 渲染那一套「打开 / 在文件夹里定位 / 复制路径 / …」菜单 */
  onMenu?: (e: ReactMouseEvent, p: string) => void
}) {
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

  // 说明行搬到顶栏了：这里只负责把条数交出去（第 179 项）
  useEffect(() => {
    onTotal?.(total)
  }, [total, onTotal])

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
      {/* 列头：搜索结果的列表有「名称/路径/大小/时间」表头，时间轴以前没有——
          用户不知道右边两列是什么。列宽与下面的行严格对齐；缩略图（grid）模式没有列，不渲染。 */}
      {view === 'list' && (
        <div className="sticky top-0 z-[1] flex items-center gap-2 border-b border-line bg-bg px-2 pb-1 pt-1 text-[11px] text-sub">
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
                    onContextMenu={(e) => {
                      e.preventDefault()
                      onMenu?.(e, it.p)
                    }}
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
                onContextMenu={(e) => {
                  e.preventDefault()
                  onMenu?.(e, it.p)
                }}
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