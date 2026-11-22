import { useEffect, useMemo, useRef, useState, type ChangeEvent, type MouseEvent as ReactMouseEvent } from 'react'
import { createPortal } from 'react-dom'
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
  getVerify,
  runVerify,
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
  type VerifyState,
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
  pickFolder,
  saveConfig,
  updateStatus,
  type UpdateInfo,
} from '../api'
import { baseName, dayLabel, dirName, fmtSize, fmtTime, midName, shortPath } from '../lib/hits'
// 第 332 项：详情灯箱 ← → 先翻页、翻到头才换文件
import { resolveStep } from '../lib/paging'
import { IconCheck, IconClose, IconCoffee, IconDoc, IconFile, IconFolder, IconFolderSearch, IconGear, IconGlobe, IconHelp, IconImage, IconInfo, IconKeyboard, IconList, IconMini, IconPlug, IconTranslate, IconZip } from './icons'
import { Maintenance } from './maintenance'
import { AppearanceSection, Wallpaper } from './appearance'
import { useTitlebarDrag } from './titlebar'
import type { WallState } from '../api'
import { t, LANGS, setLang, getLang } from '../lib/i18n'
// 第 270 项：删掉的内置搜索引警要能一键补回（普通用户不会手填三栏加回来）
import { missingDefaultEngines, restoreDefaultEngines } from '../lib/websearch'
// 第 379 项：docx/xlsx 的真版面预览（原始文件字节 + 前端渲染）
import {
  canInline,
  canLayout,
  INLINE_MAX_BYTES,
  readPptxSlides,
  readSheets,
  renderDocxInline,
  type SheetTable,
  type SlideText,
} from './docview'
import { DocViewModal } from './docmodal'

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
  /** 第 364 项：设置页也吃壁纸时，抽屉底不能再是不透明实色，否则整窗被盖死 */
  wallOn?: boolean
  /** 维护任务跑完时通知外面刷新页脚数字 */
  onRefreshed?: () => void
  /** 「更多设置」里打开整本 OCR 面板（主工具栏不再放这个低频按钮） */
  onOpenOcr?: () => void
  ocrRunning?: boolean
  /** 点「已保存的搜索」时把关键词送回主界面 */
  onPickSearch?: (q: string) => void
  currentQ?: string
  /** 第 301 项：设置里的「关于」分区点「赞助」时打开独立赞助弹窗（二维码仍在那边） */
  onOpenSupport?: () => void
  /** 第 301 项：从托盘 / 侧栏 / 页脚版本号 / ?panel=about 进来时直接落在「关于」分区 */
  startSec?: SecId
  /** 第 358 项：「使用帮助」页里的两个按钮 —— 为什么搜不到 / 重看新手教程 */
  onOpenWhy?: () => void
  onReplayTour?: () => void
  /** 第 327 项：任何设置一落盘就回调一次，主界面据此立刻更新（左栏三块开关要用） */
  onSettings?: (p: Settings) => void
}

/** 第 299 项：网络代理。三种模式（跟随系统 / 不使用 / 自定义），外加"测试连接"。
 *
 *  为什么不只做一个下拉框：
 *  - 用户以前只有 settings.json 里的 trans_proxy，界面上根本没入口，想直连要手改文件；
 *  - 「跟随系统代理」和「不使用代理」看起来都是"不填东西"，但行为完全相反，
 *    所以每一种都要有一句人话说明，不能靠用户猜；
 *  - 自定义支持 host / host:port / 带账号密码的整串，前端不做格式猜测，
 *    拼不拼得起来由后端 netproxy.build_url 决定，拼不起来就当场说"不完整"。
 */
export function ProxySection({
  mode,
  host,
  port,
  onSave,
}: {
  mode: string
  host: string
  port: number
  onSave: (p: Settings) => void
}) {
  const [m, setM] = useState(mode || 'system')
  const [h, setH] = useState(host || '')
  const [p, setP] = useState(String(port || 7890))
  const [now, setNow] = useState<{ mode?: string; url?: string; system?: string } | null>(null)
  const [msg, setMsg] = useState('')
  const [bad, setBad] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = () => {
    fetch('/api/proxy', { headers: { 'Sec-Fetch-Site': 'same-origin' } })
      .then((r) => r.json())
      .then((d) => setNow(d as { mode?: string; url?: string; system?: string }))
      .catch(() => {})
  }

  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    setM(mode || 'system')
    setH(host || '')
    setP(String(port || 7890))
  }, [mode, host, port])

  const pick = (id: string) => {
    setM(id)
    setMsg('')
    onSave({ proxy_mode: id })
    window.setTimeout(load, 500)
  }

  const apply = () => {
    onSave({ proxy_host: h.trim(), proxy_port: Number(p) || 7890 })
    window.setTimeout(load, 500)
  }

  const test = async () => {
    if (busy) return
    setBusy(true)
    setMsg('')
    try {
      const r = await fetch('/api/proxy/test', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Sec-Fetch-Site': 'same-origin',
          Origin: window.location.origin,
        },
        body: JSON.stringify({ draft: { mode: m, host: h.trim(), port: Number(p) || 0 } }),
      })
      const d = (await r.json()) as {
        ok?: boolean; code?: string; ms?: number; via?: string; status?: number
      }
      if (d.ok) {
        setBad(false)
        setMsg(
          t('proxy_test_ok')
            .replace('{ms}', String(d.ms || 0))
            .replace('{v}', d.via || t('proxy_direct')),
        )
      } else {
        setBad(true)
        const code = d.code || 'network'
        const why =
          code === 'bad'
            ? t('proxy_test_bad')
            : code === 'http'
              ? t('proxy_why_http').replace('{status}', String(d.status || 0))
              : t('proxy_why_' + code) === 'proxy_why_' + code
                ? t('proxy_why_network')
                : t('proxy_why_' + code)
        setMsg(code === 'bad' ? why : t('proxy_test_fail').replace('{why}', why))
      }
      load()
    } catch {
      setBad(true)
      setMsg(t('proxy_test_fail').replace('{why}', t('proxy_why_network')))
    } finally {
      setBusy(false)
    }
  }

  const modes = [
    { id: 'system', name: t('proxy_system'), tip: t('proxy_system_tip') },
    { id: 'none', name: t('proxy_none'), tip: t('proxy_none_tip') },
    { id: 'custom', name: t('proxy_custom'), tip: t('proxy_custom_tip') },
  ]
  const nowUrl = now?.url || ''
  const nowText = nowUrl
    ? t('proxy_now').replace('{v}', nowUrl)
    : now?.mode === 'system'
      ? t('proxy_now_sysnone')
      : t('proxy_now_none')

  return (
    <>
      <div className="pt-1 text-[13px] uppercase tracking-wide text-sub">{t('nav_network')}</div>
      <div className="pt-2 text-[13px] leading-6 text-sub">{t('proxy_desc')}</div>

      <div className="mt-4 rounded-lg border border-line bg-card p-4">
        <div className="text-[14px] font-medium text-ink">{t('proxy_mode')}</div>
        <div className="mt-3 space-y-2">
          {modes.map((x) => {
            const on = m === x.id
            return (
              <button
                key={x.id}
                role="radio"
                aria-checked={on}
                onClick={() => pick(x.id)}
                className={`flex w-full items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors ${
                  on ? 'border-accent bg-accent/8' : 'border-line bg-bg/40 hover:border-sub/60'
                }`}
              >
                <span
                  className={`mt-[3px] inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
                    on ? 'border-accent' : 'border-sub/50'
                  }`}
                >
                  {on && <span className="h-2 w-2 rounded-full bg-accent" />}
                </span>
                <span className="min-w-0">
                  <span className={`block text-[13.5px] ${on ? 'font-medium text-ink' : 'text-ink/90'}`}>
                    {x.name}
                  </span>
                  <span className="mt-0.5 block text-[12px] leading-5 text-sub">{x.tip}</span>
                </span>
              </button>
            )
          })}
        </div>

        {/* 自定义地址：只在选了自定义时给输入框，避免用户以为"跟随系统"还要再填一遍 */}
        <div className={`mt-3 rounded-lg border border-line/70 bg-bg/30 p-3 ${m === 'custom' ? '' : 'opacity-50'}`}>
          <div className="flex flex-wrap items-end gap-2">
            <label className="min-w-0 flex-1">
              <span className="mb-1 block text-[12.5px] text-sub">{t('proxy_host')}</span>
              <input
                value={h}
                disabled={m !== 'custom'}
                onChange={(e) => setH(e.target.value)}
                placeholder={t('proxy_host_hint')}
                spellCheck={false}
                className="w-full rounded-md border border-line bg-bg/60 px-3 py-2 text-[13px] text-ink outline-none focus:border-accent disabled:cursor-not-allowed"
              />
            </label>
            <label className="w-[92px] shrink-0">
              <span className="mb-1 block text-[12.5px] text-sub">{t('proxy_port')}</span>
              <input
                value={p}
                disabled={m !== 'custom'}
                inputMode="numeric"
                onChange={(e) => setP(e.target.value.replace(/[^0-9]/g, '').slice(0, 5))}
                className="w-full rounded-md border border-line bg-bg/60 px-3 py-2 text-[13px] text-ink outline-none focus:border-accent disabled:cursor-not-allowed"
              />
            </label>
            <button
              onClick={apply}
              disabled={m !== 'custom'}
              className="rounded-md border border-line px-3 py-2 text-[13px] text-ink hover:border-accent hover:text-accent disabled:opacity-40"
            >
              {t('proxy_apply')}
            </button>
          </div>
          <div className="mt-1.5 text-[11.5px] leading-5 text-sub/70">{t('proxy_host_hint')}</div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            onClick={test}
            disabled={busy}
            className="rounded-md border border-line px-3 py-2 text-[13px] text-ink hover:border-accent hover:text-accent disabled:opacity-50"
          >
            {busy ? t('proxy_testing') : t('proxy_test')}
          </button>
          <span className="text-[12.5px] text-sub">{nowText}</span>
        </div>
        {msg && (
          <div
            role="status"
            className={`mt-2 rounded-md border px-3 py-2 text-[12.5px] leading-5 ${
              bad ? 'border-red-400/40 bg-red-500/5 text-red-500' : 'border-green-500/40 bg-green-500/5 text-green-600'
            }`}
          >
            {msg}
          </div>
        )}
      </div>
    </>
  )
}

/** 第 207 项：设置页的分类（对标 Listary 选项窗口的左侧导航） */
export type SecId =
  | 'general'
  | 'appearance'
  | 'hotkey'
  | 'files'
  | 'sidebar'
  | 'mini'
  | 'web'
  | 'network'
  | 'translate'
  | 'help'
  | 'about'

/** 第 281 项：语言名一律写成「该语言自己的写法」，前面不再挂「英语/日语…」中文前缀——
 *  阿拉伯用户看到「英语 (English)」只会更懵。auto 那项走 i18n（译成界面语言）。
 *  用函数而不是常量：切语言要跟着重渲染。 */
const transLangOptions = () => [
  { code: 'auto', label: t('trans_auto_opt') },
  { code: 'zh-CN', label: '中文 (简体)' },
  { code: 'en', label: 'English' },
  { code: 'ja', label: '日本語' },
  { code: 'ko', label: '한국어' },
  { code: 'fr', label: 'Français' },
  { code: 'de', label: 'Deutsch' },
  { code: 'es', label: 'Español' },
  { code: 'ru', label: 'Русский' },
  { code: 'pt', label: 'Português' },
  { code: 'it', label: 'Italiano' },
  { code: 'ar', label: 'العربية' },
  { code: 'th', label: 'ไทย' },
  { code: 'vi', label: 'Tiếng Việt' },
]

/** 第 268 项：聚合翻译的引擎清单。id 必须和 translator.ENGINES 里的 id 一字不差，
 *  用户勾选状态就按 id 存进 settings.json 的 trans_engines。
 *  第 278 项：名字和说明改存 i18n 键 —— 界面语言一变，这里跟着变（原来写死中文）。 */
const TRANS_ENGINES = [
  { id: 'google', nameKey: 'te_google', noteKey: 'te_google_note' },
  { id: 'mymemory', nameKey: 'te_mymemory', noteKey: 'te_mymemory_note' },
  // 第 295 项：Google 国内直连不通时，用户至少还有这家整句引擎（免密钥、直连可用）
  { id: 'tencent', nameKey: 'te_tencent', noteKey: 'te_tencent_note' },
  { id: 'youdao', nameKey: 'te_youdao', noteKey: 'te_youdao_note' },
  { id: 'baidu', nameKey: 'te_baidu', noteKey: 'te_baidu_note' },
  { id: 'iciba', nameKey: 'te_iciba', noteKey: 'te_iciba_note' },
  // 第 274 项：自带密钥的三家 —— id 与 translator.ENGINES 一致，密钥存在本机 settings.json
  { id: 'google_api', nameKey: 'te_google_api', noteKey: 'te_byok_note' },
  { id: 'deepseek', nameKey: 'te_deepseek', noteKey: 'te_byok_note' },
  { id: 'deepl', nameKey: 'te_deepl', noteKey: 'te_byok_note' },
  { id: 'openai', nameKey: 'te_openai', noteKey: 'te_byok_note' },
  { id: 'baidu_api', nameKey: 'te_baidu_api', noteKey: 'te_byok_note' },
]

/** 第 278 项：结果/被跳过里的引擎名按界面语言显示 —— 优先用后端给的 engine_id，
 *  拿不到 id 才退回后端原标签，永远不显示空白。 */
function engineLabel(r: { engine?: string; engine_id?: string }): string {
  const hit = TRANS_ENGINES.find((e) => e.id === (r.engine_id || ''))
  if (hit) return t(hit.nameKey)
  return r.engine || r.engine_id || ''
}

/** 第 295 项：被跳过的原因。后端给的是中文（含秒数），这里按 code 换成界面语言；
 *  拿不到 code 才退回后端原文。原来只显示引擎名，用户根本看不出"为什么没有它"。 */
function whyLabel(s: { code?: string; reason?: string; secs?: number }): string {
  const code = s.code || ''
  const key = code ? `trans_why_${code}` : ''
  const txt = key ? t(key) : ''
  const out = (txt && txt !== key ? txt : (s.reason || '')).replace('{s}', String(s.secs ?? ''))
  return out
}

/** 第 278 项：结果右上角小徽标（extra）按界面语言显示；不是固定文案的（模型名）原样透传。 */
function extraLabel(r: { extra?: string; extra_code?: string }): string {
  return r.extra_code ? t(`tx_${r.extra_code}`) : (r.extra || '')
}

/** 第 274 项：自带密钥卡片的字段定义（后端 /api/byok 的 specs 与此对应）。
 *  第 278 项：名称/提示/字段标签全部走 i18n。 */
const BYOK_FORMS = [
  { id: 'google_api', nameKey: 'te_google_api', hintKey: 'byok_hint_google', fields: [{ k: 'key', labelKey: 'te_field_api_key' }] },
  { id: 'deepseek', nameKey: 'te_deepseek', hintKey: 'te_deepseek_hint', fields: [{ k: 'key', labelKey: 'te_field_api_key' }] },
  { id: 'deepl', nameKey: 'te_deepl', hintKey: 'byok_hint_deepl', fields: [{ k: 'key', labelKey: 'te_field_api_key' }] },
  { id: 'openai', nameKey: 'te_openai', hintKey: 'byok_hint_openai', fields: [{ k: 'key', labelKey: 'te_field_api_key' }, { k: 'model', labelKey: 'te_field_model' }] },
  { id: 'baidu_api', nameKey: 'te_baidu_api', hintKey: 'byok_hint_baidu', fields: [{ k: 'appid', labelKey: 'te_field_appid' }, { k: 'secret', labelKey: 'byok_field_secret' }] },
]

export function TranslateSection({
  targetLang,
  onTargetLang,
  live,
  onLive,
  engines,
  onEngines,
  byokReady,
}: {
  targetLang: string
  onTargetLang: (v: string) => void
  live: boolean
  onLive: (v: boolean) => void
  engines: string[]
  onEngines: (v: string[]) => void
  /** 第 274 项：后端 /api/settings 下发的「哪几家自带密钥已配好」 */
  byokReady?: Record<string, boolean>
}) {
  const [testTxt, setTestTxt] = useState('')
  const [testing, setTesting] = useState(false)
  // 第 274 项：自带密钥的输入草稿与掩码（密钥本身后端不回显）
  const [byokDraft, setByokDraft] = useState<Record<string, Record<string, string>>>({})
  const [byokMasked, setByokMasked] = useState<Record<string, Record<string, string>>>({})
  const [saveBusy, setSaveBusy] = useState('')
  /** 第 346 项：自带密钥那五张卡默认折叠 —— 绝大多数人不会填，摆出来只是刷屏。
   *  有任意一家已经配好密钥时自动展开（他要改/要清空，得看得见）。 */
  const [byokOpen, setByokOpen] = useState(false)
  const byokReadyCount = Object.values(byokReady || {}).filter(Boolean).length
  // 已经配好任意一家密钥 → 自动展开（用户要改/要清空，不能藏在折叠里）
  useEffect(() => {
    if (byokReadyCount > 0) setByokOpen(true)
  }, [byokReadyCount])

  useEffect(() => {
    fetch('/api/byok')
      .then((r) => r.json())
      .then((d) => {
        if (d?.ok) setByokMasked(d.masked || {})
      })
      .catch(() => {})
  }, [])

  const saveByok = async (id: string, clear: boolean) => {
    setSaveBusy(id)
    try {
      const res = await fetch('/api/byok', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
        body: JSON.stringify({ provider: id, fields: byokDraft[id] || {}, clear }),
      })
      const d = await res.json()
      if (d?.ok) {
        setByokMasked(d.masked || {})
        setByokDraft((prev) => ({ ...prev, [id]: {} }))
        // 后端会把配好的这家自动加进 trans_engines；这里同步一下，省得等轮询
        if (!clear && d.ready?.[id] && !enabledIds.includes(id)) {
          onEngines([...enabledIds, id])
        }
      }
    } catch {
      /* 保存失败就保持原样，用户再点一次 */
    } finally {
      setSaveBusy('')
    }
  }
  const [results, setResults] = useState<{ engine: string; engine_id?: string; text: string; extra?: string; extra_code?: string }[]>([])
  // 第 271 项：后端会说明哪几家这次没参与（限流冷却 / 本机连不上 / 只用于词语），
  // 不然用户只看到一条结果，不知道另外四家是被自己省掉了还是坏了。
  const [skipped, setSkipped] = useState<
    { engine: string; reason: string; code?: string; secs?: number }[]
  >([])
  // 第 295 项：后端告诉界面当前走的是哪个代理（空 = 直连），出问题时一眼能对上
  const [proxy, setProxy] = useState('')
  // 第 341 项：后端判定「配的代理连不上、已自动直连」时带回代理地址
  const [bypass, setBypass] = useState('')

  // 第 346 项：缺省只开**前四个**（Google / MyMemory / 腾讯 / 有道），与后端
  // translator.DEFAULT_ENGINES 一致。原来缺省全开：十一引擎一起打，等的是最慢那家，
  // 后几个「自带密钥」的没密钥本来也打不通。顺序永远按 TRANS_ENGINES 排，
  // 用户点掉一个之后剩下那几个不会跳位置。
  const DEFAULT_ENGINE_IDS = TRANS_ENGINES.slice(0, 4).map((e) => e.id)
  const onSet = new Set(
    (Array.isArray(engines) && engines.length ? engines : DEFAULT_ENGINE_IDS).filter((id) =>
      TRANS_ENGINES.some((e) => e.id === id)
    )
  )
  const enabledIds = TRANS_ENGINES.filter((e) => onSet.has(e.id)).map((e) => e.id)
  const toggle = (id: string) => {
    const next = onSet.has(id) ? enabledIds.filter((x) => x !== id) : [...enabledIds, id]
    // 前端也守住"至少一个"：全关的后端会自动归回全开，那和用户点下去的结果相反，更迷惑。
    if (!next.length) return
    onEngines(next)
  }

  const runTest = async (txt = testTxt) => {
    const q = txt.trim()
    if (!q) return
    setTesting(true)
    try {
      const res = await fetch('/api/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
        body: JSON.stringify({ text: q, to: targetLang }),
      })
      const data = await res.json()
      if (data && data.ok) {
        setResults(data.results || [])
      } else {
        setResults([])
      }
      setSkipped(Array.isArray(data?.skipped) ? data.skipped : [])
      setProxy(typeof data?.proxy === 'string' ? data.proxy : '')
      setBypass(typeof data?.proxy_bypass === 'string' ? data.proxy_bypass : '')
    } catch {
      setResults([])
      setSkipped([])
      setProxy('')
      setBypass('')
    } finally {
      setTesting(false)
    }
  }

  return (
    <>
      <div className="pt-1 text-[13px] uppercase tracking-wide text-sub">{t('nav_translate')}</div>
      <div className="pt-2 text-[13px] leading-6 text-sub">{t('trans_desc')}</div>
      {/* 第 295 项：Google 那家要过代理才通，先说清楚"代理从哪来"，省得又以为只有一家能翻 */}
      <div className="pt-1 text-[12px] leading-5 text-sub/70">{t('trans_proxy_tip')}</div>

      <div className="mt-5 space-y-4">
        {/* 默认目标语言 */}
        <div className="rounded-lg border border-line bg-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[14px] font-medium text-ink">{t('trans_lang_target')}</div>
              <div className="text-[12.5px] text-sub mt-0.5">{t('trans_lang_target_tip')}</div>
            </div>
            <select
              value={targetLang || 'auto'}
              onChange={(e) => onTargetLang(e.target.value)}
              className="rounded-md border border-line bg-bg/50 px-3 py-1.5 text-[13px] text-ink outline-none focus:border-accent"
            >
              {transLangOptions().map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.code === 'auto' ? t('trans_lang_auto') : opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* 边打边译 */}
        <div className="rounded-lg border border-line bg-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[14px] font-medium text-ink">{t('trans_live')}</div>
              <div className="text-[12.5px] text-sub mt-0.5">{t('trans_live_tip')}</div>
            </div>
            <button
              type="button"
              onClick={() => onLive(!live)}
              className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                live ? 'bg-accent' : 'bg-line'
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                  live ? 'translate-x-5' : 'translate-x-0'
                }`}
              />
            </button>
          </div>
        </div>

        {/* 第 268 项：逐引擎开关。原来是五个"已启用"死卡片，一次翻译五个都打，
            慢的时候用户只能干等 —— 现在点一下就关，每次只请求勾上的那几个。 */}
        <div className="rounded-lg border border-line bg-card p-4">
          <div className="text-[14px] font-medium text-ink">{t('trans_engines')}</div>
          <div className="text-[12.5px] text-sub mt-0.5">{t('trans_engines_tip')}</div>
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-2.5 text-[12.5px]">
            {TRANS_ENGINES.map((e) => {
              const on = onSet.has(e.id)
              const isLast = on && enabledIds.length === 1
              const nm = t(e.nameKey)
              return (
                <button
                  key={e.id}
                  type="button"
                  onClick={() => toggle(e.id)}
                  disabled={isLast}
                  aria-pressed={on}
                  aria-label={`${nm} ${on ? 'on' : 'off'}`}
                  title={isLast ? t('trans_engines_tip') : nm}
                  className={`flex items-center gap-2 p-2 rounded border text-left transition-colors ${
                    on
                      ? 'border-line/60 bg-bg/30 hover:border-accent/40'
                      : 'border-line/40 bg-bg/10 hover:bg-bg/20'
                  } ${isLast ? 'cursor-default' : 'cursor-pointer'}`}
                >
                  <span className={`w-2 h-2 shrink-0 rounded-full ${on ? 'bg-emerald-500' : 'bg-line'}`} />
                  <span className={`font-medium ${on ? 'text-ink' : 'text-sub'}`}>{nm}</span>
                  <span className={`text-[11px] ml-auto ${on ? 'text-sub' : 'text-sub/60'}`}>{t(e.noteKey)}</span>
                  <span
                    className={`relative ml-1 inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors ${
                      on ? 'bg-accent' : 'bg-line'
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all ${
                        on ? 'left-[14px]' : 'left-0.5'
                      }`}
                    />
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        {/* 第 274 项：自带密钥。前五家是网页免密钥接口，额度按出口 IP 算（大家互相吃）；
            填自己的密钥之后，请求走用户自己的账号额度，密钥只存在本机 settings.json。
            第 346 项：这一整块默认折叠，标题行点一下展开（已有密钥时自动展开）。 */}
        <div className="rounded-lg border border-line bg-card p-4">
          <button
            type="button"
            onClick={() => setByokOpen((v) => !v)}
            aria-expanded={byokOpen}
            className="flex w-full items-start gap-2 text-left cursor-pointer"
          >
            <span className="mt-0.5 text-[12px] text-sub">{byokOpen ? '▾' : '▸'}</span>
            <span className="min-w-0 flex-1">
              <span className="block text-[14px] font-medium text-ink">{t('trans_byok_title')}</span>
              {!byokOpen && (
                <span className="mt-0.5 block text-[12.5px] text-sub leading-5">{t('trans_byok_collapsed')}</span>
              )}
            </span>
            {!byokOpen && byokReadyCount > 0 && (
              <span className="shrink-0 rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-700">
                {t('trans_byok_ready_n').replace('{n}', String(byokReadyCount))}
              </span>
            )}
          </button>
          {byokOpen && (
            <>
              <div className="text-[12.5px] text-sub mt-0.5 leading-5">{t('trans_byok_tip')}</div>
              <div className="mt-3 space-y-3">
            {BYOK_FORMS.map((f) => {
              const ready = !!byokReady?.[f.id]
              const draft = byokDraft[f.id] || {}
              const mask = byokMasked?.[f.id] || {}
              return (
                <div key={f.id} className="rounded border border-line/60 bg-bg/20 p-2.5">
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 shrink-0 rounded-full ${ready ? 'bg-emerald-500' : 'bg-line'}`} />
                    <span className="text-[13px] font-medium text-ink">{t(f.nameKey)}</span>
                    <span className={`text-[11px] ${ready ? 'text-emerald-600' : 'text-sub/70'}`}>
                      {ready ? t('trans_byok_ready') : t('trans_byok_empty')}
                    </span>
                    <span className="text-[11px] text-sub/60 ml-auto">{t(f.hintKey)}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {f.fields.map((fd) => (
                      <input
                        key={fd.k}
                        type="password"
                        value={draft[fd.k] ?? ''}
                        onChange={(e) =>
                          setByokDraft((prev) => ({ ...prev, [f.id]: { ...(prev[f.id] || {}), [fd.k]: e.target.value } }))
                        }
                        placeholder={
                          mask[fd.k]
                            ? `${t('trans_byok_placeholder')} ${mask[fd.k]}`
                            : `${t(fd.labelKey)}`
                        }
                        className="flex-1 min-w-[140px] rounded-md border border-line bg-bg/50 px-2.5 py-1.5 text-[12.5px] text-ink outline-none focus:border-accent"
                      />
                    ))}
                    <button
                      type="button"
                      onClick={() => saveByok(f.id, false)}
                      disabled={saveBusy === f.id}
                      className="px-3 py-1.5 text-[12.5px] font-medium bg-accent text-white rounded-md hover:opacity-90 disabled:opacity-50 transition-opacity cursor-pointer"
                    >
                      {saveBusy === f.id ? '…' : t('trans_byok_save')}
                    </button>
                    {ready && (
                      <button
                        type="button"
                        onClick={() => saveByok(f.id, true)}
                        className="px-2.5 py-1.5 text-[12px] text-sub hover:text-ink rounded-md border border-line/60 cursor-pointer"
                      >
                        {t('trans_byok_clear')}
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
              </div>
            </>
          )}
        </div>

        {/* 实时翻译测试卡片 */}
        <div className="rounded-lg border border-line bg-card p-4">
          <div className="text-[14px] font-medium text-ink mb-1">{t('trans_test_box')}</div>
          <div className="flex items-center gap-2 mt-2">
            <input
              type="text"
              value={testTxt}
              onChange={(e) => setTestTxt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  runTest()
                }
              }}
              placeholder={t('trans_test_placeholder')}
              className="flex-1 rounded-md border border-line bg-bg/50 px-3 py-1.5 text-[13px] text-ink outline-none focus:border-accent"
            />
            <button
              type="button"
              onClick={() => runTest()}
              disabled={testing || !testTxt.trim()}
              className="px-3.5 py-1.5 text-[12.5px] font-medium bg-accent text-white rounded-md hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1.5 cursor-pointer"
            >
              {testing && <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" />}
              <span>{t('trans_btn_run')}</span>
            </button>
          </div>

          {results.length > 0 && (
            <div className="mt-3 space-y-2 max-h-48 overflow-y-auto">
              {results.map((r, i) => (
                <div key={i} className="p-2.5 rounded border border-line/70 bg-bg/40 text-[12.5px]">
                  <div className="flex items-center justify-between text-[11px] text-accent font-semibold mb-1">
                    <span>{engineLabel(r)}</span>
                    {r.extra && <span className="text-sub font-normal">{extraLabel(r)}</span>}
                  </div>
                  <div className="text-ink">{r.text}</div>
                </div>
              ))}
            </div>
          )}
          {skipped.length > 0 && (
            <div className="mt-2 text-[11.5px] leading-5 text-sub/80">
              {t('trans_skipped')}{' '}
              {skipped
                .map((s) => {
                  const name = engineLabel({ engine: s.engine, engine_id: s.engine })
                  const why = whyLabel(s)
                  return why ? `${name}（${why}）` : name
                })
                .join(' · ')}
            </div>
          )}
          {/* 第 295 项：Google 那家要靠代理才通，当前代理写清楚，省得又去猜"为什么只有一家" */}
          {results.length > 0 && (
            <div className="mt-1 text-[11px] leading-5 text-sub/60">
              {proxy ? t('trans_proxy_on').replace('{v}', proxy) : t('trans_proxy_off')}
            </div>
          )}
          {/* 第 341 项：代理端口连不上、后端已自动改直连，明说一句 */}
          {bypass && (
            <div className="mt-1 text-[11px] leading-5 text-amber-600/80 dark:text-amber-400/80">
              {t('trans_bypass')}
            </div>
          )}
        </div>
      </div>
    </>
  )
}


/* 第 203 项：网络搜索。关键字 + 空格切换引擎，回车由后端用系统浏览器打开。
   引擎表只存在本机 settings.json，默认一条都不联网，只有用户主动回车才打开。 */
function WebSearchSection({
  engines, defaults, onChange,
}: {
  engines: { key: string; title: string; url: string; enabled?: boolean }[]
  /** 第 270 项：后端下发的出厂默认表，用来补回被删掉的内置引擎 */
  defaults?: { key: string; title: string; url: string; enabled?: boolean }[]
  onChange: (v: { key: string; title: string; url: string; enabled?: boolean }[]) => void
}) {
  const cls = 'rounded-md border border-line bg-card px-3 py-2.5 text-[13px]'
  // 第 304 项：鼠标移到演示图上才挂载 GIF（挂载即从第一帧开始，GIF 也不会在后台空转）
  const [demo, setDemo] = useState(false)
  const upd = (i: number, field: string, val: string | boolean) => {
    const next = engines.map((e, j) => (j === i ? { ...e, [field]: val } : e))
    onChange(next)
  }
  // 第 270 项：删掉的内置引擎能一键补回 —— 普通用户不会为了补一个 Bing 去手填
  // 关键字 / 标题 / 网址模板三栏。只补缺的，不动自定义条目与勾选状态。
  const missing = missingDefaultEngines(engines, defaults)
  return (
    <>
      {/* 第 304 项：用户「这些字应该要打一些，不然都看不清」——
          标题从 13px 灰字提到 16px 加粗正文色，说明从 text-sub 提到 text-ink/75，
          另在右侧放一张悬停才开演的演示图（GIF 体积 25KB，挂载即从第一帧播）。 */}
      <div className="flex items-start justify-between gap-5 pt-1">
        <div className="min-w-0">
          <div className="text-[16px] font-semibold text-ink">{t('web_search')}</div>
          <div className="mt-1.5 text-[13px] leading-6 text-ink/75">{t('web_search_hint')}</div>
          {/* 第 310 项：列表按「国内常用 → 国外常用」排好，这里把分组说清楚 */}
          <div className="mt-1 text-[12.5px] leading-5 text-sub/80">{t('web_search_order_tip')}</div>
        </div>
        <div
          className="relative w-[240px] shrink-0 cursor-default overflow-hidden rounded-lg border border-line bg-card/60"
          onMouseEnter={() => setDemo(true)}
          onMouseLeave={() => setDemo(false)}
          onFocus={() => setDemo(true)}
          onBlur={() => setDemo(false)}
          tabIndex={0}
          role="img"
          aria-label={t('web_demo_alt')}
          data-act="websearch-demo"
        >
          {demo ? (
            <img
              src="/web/websearch-demo.gif"
              alt={t('web_demo_alt')}
              width={640}
              height={300}
              className="block w-full"
            />
          ) : (
            <div className="relative">
              <img src="/web/websearch-demo.png" alt="" width={640} height={300} className="block w-full opacity-70" />
              <span className="absolute inset-0 grid place-items-center bg-black/10">
                <span className="flex items-center gap-1.5 rounded-full bg-black/55 px-3 py-1 text-[12px] text-white">
                  ▶ {t('web_demo_hint')}
                </span>
              </span>
            </div>
          )}
        </div>
      </div>
      {/* 第 216 项：列标题 —— 「g / b」这些关键字光看值猜不出含义，给每列表头
          （复用输入框的占位符键，不新增 i18n 键）。 */}
      <div className="flex items-center gap-1 text-[12px] text-sub/80">
        <span className="w-24 shrink-0 px-3">{t('web_search_key')}</span>
        <span className="w-40 shrink-0 px-3">{t('web_search_title')}</span>
        <span className="min-w-0 flex-1 px-3">{t('web_search_url')}</span>
        <span className="w-4 shrink-0" />
      </div>
      <div className="mt-1.5 space-y-3.5">
        {engines.map((e, i) => (
          <div key={i} className="flex items-center gap-1">
            <input className={`${cls} w-24`} value={e.key} placeholder={t('web_search_key')}
              onChange={(ev) => upd(i, 'key', ev.target.value)} />
            <input className={`${cls} w-40`} value={e.title} placeholder={t('web_search_title')}
              onChange={(ev) => upd(i, 'title', ev.target.value)} />
            <input className={`${cls} min-w-0 flex-1`} value={e.url} placeholder={t('web_search_url')}
              onChange={(ev) => upd(i, 'url', ev.target.value)} />
            <input type="checkbox" className="h-4 w-4 accent-accent" checked={e.enabled !== false}
              title={t('web_search_enable')}
              onChange={(ev) => upd(i, 'enabled', ev.target.checked)} />
            <button className="px-1.5 text-[16px] leading-none text-sub hover:text-ink"
              title={t('web_search_del')}
              onClick={() => onChange(engines.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button className="rounded-md border border-line px-3 py-2 text-[13px] text-sub hover:text-ink"
          onClick={() => onChange([...engines, { key: '', title: '', url: '', enabled: true }])}>
          + {t('web_search_add')}
        </button>
        <button
          className={`rounded-md border border-line px-3 py-2 text-[13px] ${
            missing.length ? 'text-sub hover:text-ink' : 'cursor-default text-sub/40'
          }`}
          title={missing.length ? t('web_search_restore_tip') : t('web_search_restore_none')}
          onClick={() => {
            const r = restoreDefaultEngines(engines, defaults)
            if (r.added) onChange(r.list)
          }}>
          {missing.length
            ? t('web_search_restore_n').replace('{n}', String(missing.length))
            : t('web_search_restore')}
        </button>
      </div>
      {/* 删过内置引擎时明说一句，否则用户不知道自己少了什么 */}
      {missing.length > 0 && (
        <div className="mt-1.5 text-[12.5px] text-sub/80">
          {t('web_search_restore_tip')}
        </div>
      )}
    </>
  )
}

/* 第 358 项：设置「使用帮助」页的分段渲染（原来 HelpModal 里的 Section 搬出来）。
   和热键页共用同一份 kbd 样式，视觉上两页看起来是一套东西。 */
const HelpSection = (title: string, rows: [string, string][]) => (
  <div className="min-w-0">
    <div className="pb-2 text-[13px] font-semibold text-accent">{title}</div>
    <div className="flex flex-col gap-1.5">
      {rows.map(([k, v]) => (
        <div key={k + v} className="flex items-start gap-2.5">
          <kbd className="mt-[1px] h-6 min-w-[74px] shrink-0 rounded border border-line bg-card px-2 text-center text-[11px] leading-[22px]">
            {k}
          </kbd>
          <span className="text-[12.5px] leading-6 text-ink/90">{v}</span>
        </div>
      ))}
    </div>
  </div>
)

export function SettingsDrawer({ open, onClose, palette, onPalette, wall, onWall, wallOn, onRefreshed, onPickSearch, currentQ, onOpenOcr, ocrRunning, onOpenSupport, startSec, onSettings, onOpenWhy, onReplayTour }: Props) {
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
  const [netIn, setNetIn] = useState('')
  // 第 294 项（C）：只读态的「添加网络位置」按钮要能把焦点直接落到那个输入框，
  // 不然用户点完还得自己在两排输入框里找哪个是网络用的。
  const netRef = useRef<HTMLInputElement | null>(null)
  const [exclIn, setExclIn] = useState('')
  const [saving, setSaving] = useState(false)
  const [scopeMsg, setScopeMsg] = useState<{ ok: boolean; text: string } | null>(null)
  // 第 294 项（E）：保存只写 scope.json，索引本身不会动；这里做「保存后一键按新范围更新」，
  // 免得用户看到「已保存」却不知道要再去维护区点一次，也免得以为加进去就能搜到。
  const [upd, setUpd] = useState<{ busy: boolean; done: number; total: number; finished: boolean } | null>(null)
  const updTimer = useRef<number | null>(null)
  // 第 207 项：当前所在的分类页；每次打开抽屉都回到「常规设置」
  const [sec, setSec] = useState<SecId>('general')
  // 第 216 项：抽屉头部条 = 第二个拖动把手（与标题栏同一套逻辑）
  const drag = useTitlebarDrag()

  const reloadCfg = () => getConfig().then(setCfg).catch(() => {})

  useEffect(
    () => () => {
      if (updTimer.current) window.clearInterval(updTimer.current)
      updTimer.current = null
    },
    [],
  )

  useEffect(() => {
    if (!open) return
    getSettings()
      .then((r) => setS({ ...r, theme: localStorage.getItem('sb_theme') || r.theme || 'auto' }))
      .catch(() => {})
    getAutostart().then((r) => setAuto(!!r.on)).catch(() => {})
    getStats().then(setStats).catch(() => {})
    // 索引范围：用户在设置里最想知道「到底扫了我哪些文件夹」，别只给一个总数
    getConfig().then(setCfg).catch(() => {})
    // 每次打开设置都退回只读 + 回到第一页，避免上次没保存的草稿/页面留在界面上
    setEditing(false)
    setScopeMsg(null)
    // 第 301 项：普通打开回「常规设置」；从「关于」入口进来直接落到关于分区。
    setSec(startSec || 'general')
  }, [open, startSec])



  if (!open) return null

  const patch = (p: Settings) => {
    setS((cur) => ({ ...cur, ...p }))
    saveSettings(p).catch(() => {})
    // 第 327 项：主界面（左栏三块）要跟着改，不能等用户重开窗口
    onSettings?.(p)
  }

  const beginEdit = (focusNet = false) => {
    setEDirs(cfg?.scope?.include_dirs ? [...cfg.scope.include_dirs] : [])
    setEExcl(cfg?.scope?.exclude_dirs ? [...cfg.scope.exclude_dirs] : [])
    setDirIn('')
    setNetIn('')
    setExclIn('')
    setScopeMsg(null)
    setEditing(true)
    if (focusNet) {
      window.setTimeout(() => netRef.current?.focus(), 120)
    }
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

  /* 第 294 项（B）：网络位置单列一块。
     用户原话「连我都不知道咋用，也不知道有这个功能」—— 所以这里不能只靠
     一个输入框碰运气，必须有独立标题、示例占位、和一段「怎么拿到这个地址」的说明。 */
  const isNetPath = (p: string) => p.startsWith('\\\\') || p.startsWith('//')

  const addNet = () => {
    const v = netIn.trim()
    if (!v) return
    if (!isNetPath(v)) {
      setScopeMsg({ ok: false, text: t('scope_net_bad') })
      return
    }
    setEDirs((cur) => (cur.includes(v) ? cur : [...cur, v]))
    setNetIn('')
    setScopeMsg(null)
  }

  const nDirs = eDirs.filter(isNetPath)
  const lDirs = eDirs.filter((p) => !isNetPath(p))
  const addExcl = () => {
    const v = exclIn.trim()
    if (!v) return
    setEExcl((cur) => (cur.includes(v) ? cur : [...cur, v]))
    setExclIn('')
  }

  const runUpd = async () => {
    if (upd?.busy) return
    setUpd({ busy: true, done: 0, total: 0, finished: false })
    setScopeMsg(null)
    try {
      const r = await fetch('/api/update', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Sec-Fetch-Site': 'same-origin',
          Origin: window.location.origin,
        },
        body: '{}',
      })
      const j = (await r.json().catch(() => ({}))) as { ok?: boolean; err?: string; k?: string }
      if (j && j.ok === false) {
        setUpd(null)
        setScopeMsg({ ok: false, text: (j.k && t(j.k)) || j.err || t('scope_upd_fail') })
        return
      }
    } catch {
      setUpd(null)
      setScopeMsg({ ok: false, text: t('scope_upd_fail') })
      return
    }
    if (updTimer.current) window.clearInterval(updTimer.current)
    updTimer.current = window.setInterval(async () => {
      try {
        const r = await fetch('/api/status', { headers: { 'Sec-Fetch-Site': 'same-origin' } })
        const p = (await r.json()) as { state?: string; done?: number; total?: number }
        if (p && p.state && p.state !== 'busy') {
          if (updTimer.current) window.clearInterval(updTimer.current)
          updTimer.current = null
          setUpd({ busy: false, done: p.done || 0, total: p.total || 0, finished: true })
          getStats().then(setStats).catch(() => {})
          reloadCfg()
        } else {
          setUpd({ busy: true, done: p.done || 0, total: p.total || 0, finished: false })
        }
      } catch {
        /* 后端忙时偶发失败，下一次再读 */
      }
    }, 700)
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
      // 第 294 项（E）：提示里不再让用户自己去找按钮，改成在下面直接给一个
      //「立即按新范围更新」的链接（drop 了不可用路径时才需要单独说明）。
      setScopeMsg({
        ok: true,
        text:
          t('scope_saved') +
          (dropped.length ? ' · ' + t('scope_dropped').replace('{p}', dropped.join('、')) : ''),
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
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? 'bg-accent' : 'bg-line'}`}
    >
      <span
        className={`absolute top-[3px] h-[18px] w-[18px] rounded-full bg-white transition-all ${on ? 'left-[23px]' : 'left-[3px]'}`}
      />
    </button>
  )

  const Row = ({ title, tip, children }: { title: React.ReactNode; tip?: string; children: React.ReactNode }) => (
    <div className="flex items-center justify-between gap-4 border-b border-line/70 py-4">
      <div className="flex items-center gap-1.5 text-[14px]">
        <span>{title}</span>
        {tip && (
          <span
            title={tip}
            className="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-sub/40 text-[11px] font-medium text-sub hover:border-accent hover:text-accent"
          >
            ?
          </span>
        )}
      </div>
      {children}
    </div>
  )

  // 第 207 项：左侧导航的六个分类。图标对齐 Listary 的习惯：
  // 常规=齿轮、外观=图片、热键=键盘、文件搜索=文件夹+放大镜、快搜窗=小窗、网络搜索=地球。
  // 第 210 项：对标 Listary 的饱满度 —— 图标 22、行高加大、侧栏加宽。
  const NAV: { id: SecId; label: string; icon: React.ReactNode }[] = [
    { id: 'general', label: t('nav_general'), icon: <IconGear size={22} /> },
    { id: 'appearance', label: t('nav_appearance'), icon: <IconImage size={22} /> },
    { id: 'hotkey', label: t('nav_hotkey'), icon: <IconKeyboard size={22} /> },
    { id: 'files', label: t('nav_files'), icon: <IconFolderSearch size={22} /> },
    /* 第 327 项：左栏（类型 / 文件夹 / 时间）也能自己配，所以单开一页 */
    { id: 'sidebar', label: t('nav_sidebar'), icon: <IconList size={22} /> },
    { id: 'mini', label: t('nav_mini'), icon: <IconMini size={22} /> },
    { id: 'web', label: t('web_search'), icon: <IconGlobe size={22} /> },
    { id: 'network', label: t('nav_network'), icon: <IconPlug size={22} /> },
    { id: 'translate', label: t('nav_translate'), icon: <IconTranslate size={22} /> },
    /* 第 358 项：原「使用帮助」弹窗并进设置 —— 弹窗里的语法/命令/其它操作全搬过来，
       只把和「热键」页重复的快捷键列删掉，一处维护一份。 */
    { id: 'help', label: t('help_title'), icon: <IconHelp size={22} /> },
  ]

  // 兼容模式 / 自绘标题栏 / 设置备份整块归入「常规设置」页（原来折叠在「更多设置」里）
  const careBlock = (
    <CarePanel
      compat={!!s.compat}
      onCompat={(v) => patch({ compat: v })}
      titlebar={s.titlebar !== false}
      onTitlebar={(v) => patch({ titlebar: v })}
      headless
    />
  )

  return (
    <aside
      className="fixed inset-x-0 bottom-0 z-20 flex flex-col overflow-hidden bg-bg"
      style={{ top: 'var(--sb-title, 0px)' }}
    >
      {/* 第 365 项：勾了「设置页」要真能看见壁纸，但**不能**把抽屉改成半透明——
          抽屉后面是主界面列表，一透就字叠字。做法是抽屉里自己铺一层壁纸（absolute），
          垫在实色 bg-bg 之上、内容之下；壁纸的位置/缩放/模糊与主窗那层完全同一套参数。 */}
      {wallOn && <Wallpaper wall={wall} absolute />}

      {/* 第 216 项：抽屉头部条共用标题栏的拖动 hook —— 铺满全窗后这条看起来
          就像标题栏，用户会去抓它；抓这里 = 拖窗口。✕ 按钮 data-nodrag。 */}
      <div
        className="relative z-10 flex shrink-0 cursor-default select-none items-center justify-between border-b border-line px-4 py-2.5"
        onPointerDown={drag.onDown}
        onPointerMove={drag.onMove}
        onPointerUp={drag.onUp}
        onPointerCancel={drag.onUp}
      >
        <span className="flex items-center gap-1.5 text-[14px] font-semibold">
          <IconGear size={15} /> {t('settings')}
        </span>
        <button
          onClick={onClose}
          data-nodrag
          className="text-sub hover:text-ink"
          aria-label={t('set_close')}
        >
          <IconClose size={16} />
        </button>
      </div>

      <div className="relative z-10 flex min-h-0 flex-1">
        {/* 第 207 项：Listary 式左侧分类导航 —— 图标 + 名称一列，「关于」钉在最底下。
            设置项本身没动（还是改了立即生效），只是不再全部堆在一竖条里。
            第 209 项：整个设置页从右侧 680px 抽屉改成铺满标题栏以下的整个窗口。
            第 210 项：对标 Listary 的饱满侧栏 —— 加宽到 228、图标 22、行距加大、
            侧栏铺浅色底、 mt-auto 把「关于」真正钉到窗口底。 */}
        <nav className="sb-veil flex w-[228px] shrink-0 flex-col gap-1 overflow-auto border-r border-line bg-line/30 px-3 py-3">
          {NAV.map((n) => (
            <button
              key={n.id}
              onClick={() => setSec(n.id)}
              aria-current={sec === n.id ? 'true' : undefined}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-left text-[14px] transition-colors ${
                sec === n.id ? 'bg-accent/12 font-medium text-accent' : 'text-sub hover:bg-line/50 hover:text-ink'
              }`}
            >
              <span className="shrink-0">{n.icon}</span>
              <span className="truncate">{n.label}</span>
            </button>
          ))}
          {/* 第 301 项：原来这条按钮会关掉设置、弹独立的「关于」窗口；现在只是把
              设置页切到最后一个分区 —— 独立的关于页已经取消。 */}
          <div className="mt-auto pt-2">
            <div className="mb-2 border-t border-line" />
            <button
              onClick={() => setSec('about')}
              aria-current={sec === 'about' ? 'true' : undefined}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-[14px] transition-colors ${
                sec === 'about' ? 'bg-accent/12 font-medium text-accent' : 'text-sub hover:bg-line/50 hover:text-ink'
              }`}
            >
              <span className="shrink-0"><IconInfo size={22} /></span>
              <span className="truncate">{t('about')}</span>
            </button>
          </div>
        </nav>

        <div className="sb-veil min-w-0 flex-1 overflow-auto px-6 py-4">
          <div className="max-w-[920px]">
          {sec === 'general' && (
            <>
              <Row title={t('lang_label')}>
                <select
                  value={getLang()}
                  onChange={(e) => {
                    setLang(e.target.value as (typeof LANGS)[number]['id'], true)
                    patch({ lang: e.target.value })
                  }}
                  className="rounded-md border border-line bg-card px-3 py-2 text-[14px]"
                >
                  {LANGS.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.label}
                    </option>
                  ))}
                </select>
              </Row>
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
              {/* 第 386 项：详情栏要不要默认展开。有人就喜欢一选中就摊开看大图，
                  默认仍按原来的窄栏走，别把所有人一起改掉。 */}
              <Row title={t('dtl_expand')} tip={t('dtl_expand_tip')}>
                <Toggle
                  label={t('dtl_expand_label')}
                  on={s.dtl_expand === true}
                  onClick={() => patch({ dtl_expand: s.dtl_expand !== true })}
                />
              </Row>
              {careBlock}
              </>
          )}
          {sec === 'hotkey' && (
            <>
              <div className="pt-1 text-[13px] font-medium uppercase tracking-wide text-accent">{t('summon_method')}</div>
              <Row title={t('hotkey_shortcut')}>
                <div className="flex flex-col items-center gap-2">
                  <div className="flex w-[260px] items-center justify-center rounded-md border border-line bg-card py-2 text-[15px] font-medium text-accent shadow-sm">
                    {s.hotkey_mode === 'alt_space' ? t('hotkey_opt_alt_space') : s.hotkey_mode === 'ctrl_space' ? t('hotkey_opt_ctrl_space') : t('hotkey_opt_double_ctrl')}
                  </div>
                  <div className="flex w-[260px] items-center justify-between px-1">
                    {[
                      { id: 'alt_space', label: t('hotkey_opt_alt_space') },
                      { id: 'ctrl_space', label: t('hotkey_opt_ctrl_space') },
                      { id: 'double_ctrl', label: t('hotkey_opt_double_ctrl') },
                    ].map((opt) => (
                      <button
                        key={opt.id}
                        type="button"
                        onClick={() => patch({ hotkey_mode: opt.id as any, hotkey: true })}
                        className={`text-xs transition-colors ${
                          (s.hotkey_mode || 'double_ctrl') === opt.id
                            ? 'font-semibold text-accent'
                            : 'text-sub hover:text-ink'
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
              </Row>

              <Row
                title={t('floating_ball')}
                tip={t('floating_ball_tip')}
              >
                <Toggle
                  label={t('floating_ball')}
                  on={!!s.floating_ball}
                  onClick={() => patch({ floating_ball: !s.floating_ball })}
                />
              </Row>

              {/* 第 300 项：用户「设置里加个快捷键分区把已经快捷键弄进去」。
                  数据源直接用帮助弹窗那份 KB()，一处维护两处显示 ——
                  以前帮助页列着的键和代码对不上（Ctrl+F / Ctrl+Enter 写了没实现），
                  就是因为列表是手写第二份。这里再拼一行"当前唤起方式"：
                  唤起键可在上面切换，写死 Ctrl Ctrl 会骗人。 */}
              <div className="mt-4 rounded-lg border border-line bg-card p-4">
                <div className="text-[14px] font-medium text-ink">{t('sc_title')}</div>
                <div className="mt-0.5 text-[12.5px] leading-5 text-sub">{t('sc_tip')}</div>
                <div className="mt-3 space-y-1.5">
                  {([
                    [
                      (s.hotkey_mode || 'double_ctrl') === 'alt_space'
                        ? t('hotkey_opt_alt_space')
                        : (s.hotkey_mode || 'double_ctrl') === 'ctrl_space'
                          ? t('hotkey_opt_ctrl_space')
                          : t('hotkey_opt_double_ctrl'),
                      t('kb8'),
                    ],
                    ...KB()
                      .filter(([k]) => k !== 'Ctrl Ctrl')
                      .map(([k, v]) => [k, k === 'Ctrl+G' ? t('sc_g') : v] as [string, string]),
                  ] as [string, string][]).map(([k, v]) => (
                    <div key={k + v} className="flex items-start gap-2.5">
                      <kbd className="mt-[1px] h-6 min-w-[74px] shrink-0 rounded border border-line bg-bg/60 px-2 text-center text-[11px] leading-[22px]">
                        {k}
                      </kbd>
                      <span className="text-[12.5px] leading-[22px] text-sub">{v}</span>
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex items-center gap-2 border-t border-line/60 pt-3 text-[11.5px] leading-5 text-sub">
                  <span className="flex-1">{t('sc_more')}</span>
                  <button
                    type="button"
                    onClick={() => setSec('help')}
                    className="shrink-0 rounded-md border border-line bg-bg/60 px-2.5 py-1 text-[12px] text-ink transition-colors hover:border-accent hover:text-accent"
                  >
                    {t('sc_more_go')}
                  </button>
                </div>
              </div>
            </>
          )}
          {/* 第 197 项：两个「自己冒出来」的开关。默认开（Listary 那种手感），
              嫌打扰的用户在这里关掉，不用去改 settings.json。 */}
          {sec === 'mini' && (
            <>
      <Row title={t('auto_popup')}>
        <Toggle
          label={t('auto_popup_label')}
          on={s.auto_popup !== false}
          onClick={() => patch({ auto_popup: s.auto_popup === false })}
        />
      </Row>
      <Row title={t('type_search')}>
        <Toggle
          label={t('type_search_label')}
          on={s.type_search !== false}
          onClick={() => patch({ type_search: s.type_search === false })}
        />
      </Row>
      {/* 第 233 项：正文搜索从快搜窗按钮挪到这里（按钮点亮是主题蓝底，和两侧图标
          不协调）。默认开 —— 这是与 Listary 的主要差异，关掉就退回纯文件名搜索。 */}
      <Row title={t('body_search')}>
        <Toggle
          label={t('body_search_label')}
          on={s.body_search !== false}
          onClick={() => patch({ body_search: s.body_search === false })}
        />
      </Row>
      {/* 第 275 项：「最近记录」三件事收成一个小集合 —— 显不显示、紧凑不紧凑、显示几条。
          用户原话：「弄成一个小集合……还要弄个最近记录显不显示的按钮」。 */}
      <div className="mt-4 rounded-lg border border-line bg-card px-4 pb-1 pt-3 [&>div:last-child]:border-0">
        <div className="text-[14px] font-medium text-ink">{t('qs_home_group')}</div>
        <div className="mt-0.5 text-[12.5px] text-sub">{t('qs_home_group_tip')}</div>
        <Row title={t('qs_home_show')}>
          <Toggle
            label={t('qs_home_show_label')}
            on={s.qs_home_show !== false}
            onClick={() => patch({ qs_home_show: s.qs_home_show === false })}
          />
        </Row>
        <Row title={t('qs_home_compact')}>
          <Toggle
            label={t('qs_home_compact_label')}
            on={!!s.qs_home_compact}
            onClick={() => patch({ qs_home_compact: !s.qs_home_compact })}
          />
        </Row>
        <Row title={t('qs_home_count')}>
          <select
            value={String(s.qs_home_count ?? 4)}
            disabled={s.qs_home_show === false}
            onChange={(e) => patch({ qs_home_count: Number(e.target.value) })}
            className="rounded-md border border-line bg-bg/50 px-3 py-1.5 text-[13px] text-ink outline-none focus:border-accent disabled:opacity-50"
          >
            {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
              <option key={n} value={n}>
                {t('qs_home_count_n').replace('{n}', String(n))}
              </option>
            ))}
          </select>
        </Row>
      </div>
      {/* 第 245 项：弹窗样式定稿为用户选中的方案1（带正文片段的卡片）。
          方案2（Listary 式紧凑表）下线 —— 只剩一种样式就不该留「一个选项的假下拉框」，
          改成不可交互的固定展示；后端/宿主/页面同时归一到 rich。 */}
      <Row title={t('qs_style')}>
        <span className="rounded-md border border-line bg-card px-3 py-2 text-[14px] text-sub">
          {t('qs_style_1')}
        </span>
      </Row>
            </>
          )}
          {/* 第 203 项：网络搜索引擎表 */}
          {sec === 'web' && (
            <WebSearchSection engines={s.web_search || []} defaults={s.web_search_defaults}
              onChange={(v) => patch({ web_search: v })} />
          )}
          {sec === 'network' && (
            <ProxySection
              mode={s.proxy_mode || 'system'}
              host={s.proxy_host || ''}
              port={Number(s.proxy_port) || 7890}
              onSave={(p) => patch(p)}
            />
          )}
          {sec === 'translate' && (
            <TranslateSection
              targetLang={s.trans_target_lang || 'auto'}
              onTargetLang={(lang) => patch({ trans_target_lang: lang })}
              live={s.trans_live !== false}
              onLive={(v) => patch({ trans_live: v })}
              engines={s.trans_engines || []}
              onEngines={(v) => patch({ trans_engines: v })}
              byokReady={s.trans_byok_ready}
            />
          )}
      {/* 外观（第 191 项）：主题配色 + 全局壁纸。
          这两块将来要整体切成 Pro，所以表现层收在 ui/appearance.tsx 一个组件里；
          现在按红线**全部免费**，这里不加任何门控。 */}
          {/* 第 358 项：「使用帮助」并进设置。这里只放「没法在设置项里表达」的东西：
              搜索语法、第一个词命令、其它操作入口；快捷键那张表留在「热键」页，
              两边不再各写一份（用户看截图后说「看起来有很多重复」）。 */}
          {sec === 'help' && (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3 pb-1">
                <div className="min-w-0">
                  <div className="text-[13px] font-medium uppercase tracking-wide text-accent">{t('help_title')}</div>
                  <div className="mt-1 text-[12.5px] leading-5 text-sub">{t('help_intro')}</div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenWhy?.()}
                    className="rounded-lg border border-line bg-card px-3 py-1.5 text-[12.5px] text-ink transition-colors hover:border-accent hover:text-accent"
                  >
                    {t('help_why')}
                  </button>
                  <button
                    type="button"
                    onClick={() => onReplayTour?.()}
                    className="rounded-lg border border-line bg-card px-3 py-1.5 text-[12.5px] text-ink transition-colors hover:border-accent hover:text-accent"
                  >
                    {t('help_tour')}
                  </button>
                </div>
              </div>

              <div className="mt-2 grid grid-cols-1 gap-y-5 lg:grid-cols-2 lg:gap-x-10">
                <div className="flex flex-col gap-5">
                  {HelpSection(t('help_qs'), QS())}
                  {HelpSection(t('help_cmd'), CMDS())}
                  <div className="-mt-2 text-[11.5px] leading-5 text-sub/70">{t('cd5')}</div>
                </div>
                <div className="flex flex-col gap-5">
                  {HelpSection(t('help_more'), MORE())}
                  <div className="rounded-lg border border-line bg-card p-3 text-[11.5px] leading-5 text-sub">
                    {t('help_kb_hint')}
                  </div>
                </div>
              </div>

              <div className="pt-4 text-[11px] leading-5 text-sub/70">{t('help_foot')}</div>
            </>
          )}
      {sec === 'about' && (
        <AboutSection onSupport={() => onOpenSupport?.()} />
      )}

      {sec === 'appearance' && (
        <AppearanceSection palette={palette} onPalette={onPalette} wall={wall} onWall={onWall} />
      )}

      {/* 第 327 项：主界面左栏那三块（类型分类 / 文件夹分组 / 时间筛选）开不开。
          用户原话：「如果是 0 为什么要放到这里来，可以把这里在设置里面设置」——
          模板是给所有人用的，不是每个人都有 4 万个文件夹、每个词都有计数，
          所以：默认全开，谁嫌空谁关掉，关了也不影响已经选中的筛选值。 */}
      {sec === 'sidebar' && (
        <>
          <div className="pt-1 text-[13px] uppercase tracking-wide text-sub">{t('sb_group')}</div>
          <div className="pt-2 text-[14px] leading-7 text-sub">{t('sb_group_tip')}</div>
          <div className="mt-4 rounded-lg border border-line/70 bg-card/50 px-4 pb-1 pt-1">
            <Row title={t('sb_types')}>
              <Toggle
                label={t('sb_types_label')}
                on={s.sb_types !== false}
                onClick={() => patch({ sb_types: s.sb_types === false })}
              />
            </Row>
            <Row title={t('sb_folders')}>
              <Toggle
                label={t('sb_folders_label')}
                /* 第 337 项：默认关（用户「这一堆感觉没啥用」）—— 打开才显示那 6 个顶层目录 */
                on={s.sb_folders === true}
                onClick={() => patch({ sb_folders: s.sb_folders !== true })}
              />
            </Row>
            <Row title={t('sb_time')}>
              <Toggle
                label={t('sb_time_label')}
                on={s.sb_time !== false}
                onClick={() => patch({ sb_time: s.sb_time === false })}
              />
            </Row>
            <Row title={t('sb_counts')}>
              <Toggle
                label={t('sb_counts_label')}
                /* 第 338 项：计数数字默认关，和 sbFromSettings 的 === true 对齐 */
                on={s.sb_counts === true}
                onClick={() => patch({ sb_counts: s.sb_counts !== true })}
              />
            </Row>
          </div>
        </>
      )}

      {sec === 'files' && (
        <>
      <div className="pt-1 text-[13px] uppercase tracking-wide text-sub">{t('section_index')}</div>
      <div className="pt-2 text-[14px] leading-7 text-sub">
        {t('stats_line').split('{n}')[0]}<b className="text-ink">{stats.files.toLocaleString()}</b>{t('stats_line').split('{n}')[1].split('{m}')[0]}
        <b className="text-ink">{stats.docs.toLocaleString()}</b>{t('stats_line').split('{m}')[1]}
      </div>

      {/* 第 307 项：在资源管理器里点一下文件夹，开着的另存为/选择对话框就跟过去。
          用户原话：「点击另一个已经打开了文件夹，选择文件夹这里就自动跳转选择那个文件夹的路径」 */}
      <div className="mt-4 rounded-lg border border-line/70 bg-card/50 p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-[14px] font-semibold">{t('blank_jump')}</div>
            <div className="mt-0.5 text-[12.5px] leading-5 text-sub">{t('blank_jump_tip')}</div>
          </div>
          <Toggle
            label={t('blank_jump')}
            on={s.blank_jump !== false}
            onClick={() => patch({ blank_jump: s.blank_jump === false })}
          />
        </div>
      </div>

      {/* 索引范围：文件名层扫了哪些盘、正文层收了哪些文件夹、哪些被排除。
          v0.9.46 起可以直接改 —— 之前只有「看」，用户发现范围不对根本改不了。
          保存只写 scope.json，不自动重扫：误点一下不该让几十万条索引重新跑一遍。 */}
      {cfg?.scope && (
        <div className="mt-4 rounded-lg border border-line/70 bg-card/50 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[14px] font-semibold">{t('scope_title')}</span>
            {!editing ? (
              <div className="flex items-center gap-2">
                {/* 第 241 项：默认一屏显示 12 条，所以阈值从 3 改成 12；
                    排除项也归这个开关管（两边一起展开/收起）。 */}
                {(cfg.scope.include_dirs.length > 12 || cfg.scope.exclude_dirs.length > 12) && (
                  <button
                    onClick={() => setScopeOpen((v) => !v)}
                    aria-expanded={scopeOpen}
                    className="text-[13px] text-accent hover:underline"
                  >
                    {scopeOpen ? t('scope_less') : t('scope_more')}
                  </button>
                )}
                <button onClick={() => beginEdit()} className="text-[13px] text-accent hover:underline">
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
                  className="text-[13px] text-sub hover:underline"
                >
                  {t('scope_cancel')}
                </button>
                <button
                  onClick={saveScope}
                  disabled={saving}
                  className="rounded bg-accent px-1.5 py-[1px] text-[13px] text-white disabled:opacity-60"
                >
                  {saving ? '…' : t('scope_save')}
                </button>
              </div>
            )}
          </div>

          {editing && (
            <div className="pt-2">
              <div className="text-[13px] text-sub">
                <span className="font-medium text-ink">{t('scope_docs')}</span>
                <span className="ml-1.5">{t('scope_folders').replace('{n}', String(eDirs.length))}</span>
              </div>
              {/* 第 241 项：编辑态同样改成网格 + 限高滚动，否则 264 行把「添加目录」输入框
                  顶到几屏之外。 */}
              <ul className="mt-1 grid max-h-[300px] gap-x-3 gap-y-1 overflow-y-auto pr-1 sm:grid-cols-2 xl:grid-cols-3">
                {lDirs.map((p) => (
                  <li key={p} className="flex min-w-0 items-center gap-1.5">
                    <span className="min-w-0 flex-1 truncate font-mono text-[12px] leading-4 text-sub" title={p}>
                      {shortPath(p, 40)}
                    </span>
                    <button
                      onClick={() => setEDirs((cur) => cur.filter((x) => x !== p))}
                      title={t('scope_remove')}
                      aria-label={t('scope_remove')}
                      className="shrink-0 rounded px-1 text-[13px] text-sub hover:bg-line/60 hover:text-red-500"
                    >
                      ✕
                    </button>
                  </li>
                ))}
                {!lDirs.length && <li className="text-[12px] text-sub/70">{t('scope_need_one')}</li>}
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
                  className="min-w-0 flex-1 rounded border border-line bg-bg px-1.5 py-[3px] font-mono text-[12px]"
                />
                <button
                  onClick={addDir}
                  className="shrink-0 rounded border border-line px-3 py-1.5 text-[13px] hover:border-accent"
                >
                  {t('scope_add_dir')}
                </button>
                {hasBridge && (
                  <button
                    onClick={pickFolder}
                    className="shrink-0 rounded border border-line px-3 py-1.5 text-[13px] hover:border-accent"
                  >
                    {t('scope_pick')}
                  </button>
                )}
              </div>

              {/* 第 294 项（B）：网络位置独立成块 —— 有标题、有示例、有「怎么拿到地址」的说明。
                  用户原话：「连我都不知道咋用，也不知道有这个功能」。 */}
              <div className="mt-3 rounded border border-line/70 bg-bg/40 p-2">
                <div className="flex items-center justify-between">
                  <span className="text-[13px]">
                    <span className="font-medium text-ink">{t('scope_net_title')}</span>
                    <span className="ml-1.5 text-sub">
                      {t('scope_net_count').replace('{n}', String(nDirs.length))}
                    </span>
                  </span>
                  <span className="rounded border border-line/70 px-1 py-[1px] text-[11px] text-sub">
                    Pro
                  </span>
                </div>
                <p className="mt-1 text-[12px] leading-4 text-sub/80">{t('scope_net_hint')}</p>
                <p className="mt-0.5 text-[12px] leading-4 text-sub/80">{t('scope_net_how')}</p>
                <ul className="mt-1 grid max-h-[140px] gap-x-3 gap-y-1 overflow-y-auto pr-1 sm:grid-cols-2">
                  {nDirs.map((p) => (
                    <li key={p} className="flex min-w-0 items-center gap-1.5">
                      <span className="shrink-0 rounded border border-line/60 bg-bg px-1 py-[1px] text-[11px] text-sub">
                        {t('scope_net_tag')}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[12px] leading-4 text-sub" title={p}>
                        {shortPath(p, 34)}
                      </span>
                      <button
                        onClick={() => setEDirs((cur) => cur.filter((x) => x !== p))}
                        title={t('scope_remove')}
                        aria-label={t('scope_remove')}
                        className="shrink-0 rounded px-1 text-[13px] text-sub hover:bg-line/60 hover:text-red-500"
                      >
                        ✕
                      </button>
                    </li>
                  ))}
                  {!nDirs.length && <li className="text-[12px] text-sub/70">{t('scope_net_none')}</li>}
                </ul>
                <div className="mt-1.5 flex gap-1">
                  <input
                    ref={netRef}
                    value={netIn}
                    onChange={(e) => setNetIn(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        addNet()
                      }
                    }}
                    placeholder={t('scope_net_ph')}
                    className="min-w-0 flex-1 rounded border border-line bg-bg px-1.5 py-[3px] font-mono text-[12px]"
                  />
                  <button
                    onClick={addNet}
                    className="shrink-0 rounded border border-line px-3 py-1.5 text-[13px] hover:border-accent"
                  >
                    {t('scope_net_add')}
                  </button>
                </div>
              </div>

              <div className="pt-2.5 text-[13px] text-sub">
                <span className="font-medium text-ink">{t('scope_excl').replace('{n}', String(eExcl.length))}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {eExcl.map((w) => (
                  <span
                    key={w}
                    className="flex items-center gap-1 rounded border border-line/60 bg-bg px-1.5 py-[1px] text-[12px] text-sub"
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
                {!eExcl.length && <span className="text-[12px] text-sub/70">—</span>}
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
                  className="min-w-0 flex-1 rounded border border-line bg-bg px-1.5 py-[3px] text-[12px]"
                />
                <button
                  onClick={addExcl}
                  className="shrink-0 rounded border border-line px-3 py-1.5 text-[13px] hover:border-accent"
                >
                  {t('scope_add_excl')}
                </button>
              </div>
              <div className="pt-1.5 text-[12px] leading-4 text-sub/80">{t('scope_edit_tip')}</div>
            </div>
          )}

          {!editing && (
            <>
              <div className="pt-2">
            <div className="text-[13px] text-sub">
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
                  className="rounded border border-line/60 bg-bg px-1.5 py-[1px] font-mono text-[12px] text-sub"
                >
                  {d} {n.toLocaleString()}
                </span>
              ))}
            </div>
            {cfg.scope.top.length > 0 && (
              /* 第 241 项：top 目录改成两列网格 —— 一条一行会把面板拉成横长条 */
              <ul className="mt-1 grid gap-x-5 gap-y-0.5 sm:grid-cols-2">
                {cfg.scope.top.slice(0, 6).map(([p, n]) => (
                  <li key={p} className="flex min-w-0 items-baseline gap-2 text-[12px] leading-4 text-sub" title={p}>
                    <span className="min-w-0 flex-1 truncate font-mono">{shortPath(p, 42)}</span>
                    <span className="shrink-0 tabular-nums text-sub/80">{n.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* 第 294 项（B/C/D）：只读态也要让人知道「网络位置」这回事。
              B：只加一行小字 → 用户「没看到」；C：做成 accent 边框卡片 + 实心按钮 →
              用户「太突兀了，跟周围完全格格不入」。周围这一页是浅灰卡片 + 13px 小标题 +
              `xx · 数字` 的灰字 + 右上角 `text-accent hover:underline` 的文字链接，
              所以这里回到同一套排版：一行标题（Pro 用和盘符一样的浅灰细边角标）、
              右侧一个文字链接「添加网络位置」。不抢眼，但和「修改」并排，一眼能找到。 */}
          {(() => {
            const nets = cfg.scope.include_dirs.filter((p) => p.startsWith('\\\\') || p.startsWith('//'))
            return (
              <div className="mt-3 flex items-baseline justify-between gap-3">
                <div className="min-w-0 text-[13px] leading-5 text-sub">
                  <span className="font-medium text-ink">{t('scope_net_title')}</span>
                  <span className="ml-1.5 rounded border border-line/60 bg-bg px-1 py-[1px] text-[11px]">
                    Pro
                  </span>
                  <span className="ml-1.5">
                    {nets.length
                      ? t('scope_net_count').replace('{n}', String(nets.length))
                      : t('scope_net_none_hint')}
                  </span>
                  {nets.length > 0 && (
                    <span className="ml-1.5 font-mono text-[12px] text-sub/80" title={nets.join('  ')}>
                      {nets.map((p) => shortPath(p, 26)).join(' · ')}
                    </span>
                  )}
                </div>
                <button
                  onClick={() => beginEdit(true)}
                  className="shrink-0 text-[13px] text-accent hover:underline"
                >
                  {t('scope_net_add')}
                </button>
              </div>
            )
          })()}

          <div className="pt-2.5">
            {(() => {
              const loc = cfg.scope.include_dirs.filter(
                (p) => !p.startsWith('\\\\') && !p.startsWith('//'),
              )
              return (
                <>
                  <div className="text-[13px] text-sub">
                    <span className="font-medium text-ink">{t('scope_docs')}</span>
                    <span className="ml-1.5">
                      {loc.length
                        ? `${t('scope_folders').replace('{n}', String(loc.length))} · ${cfg.scope.docs.toLocaleString()}`
                        : t('scope_empty')}
                    </span>
                  </div>
                  {/* 第 241 项：正文层有 264 个文件夹，原来一条一行排下来能把设置页撑到几千行。
                      改成多列网格；展开时给一个可滚动的固定高度区域，不再无限往下长。
                      第 294 项：网络位置已在上面的独立区块里显示，这里只列本地文件夹。 */}
                  <div
                    className={
                      scopeOpen
                        ? 'mt-1 max-h-[320px] overflow-y-auto rounded border border-line/50 bg-bg/40 p-1.5'
                        : 'mt-1'
                    }
                  >
                    <ul className="grid gap-x-4 gap-y-0.5 sm:grid-cols-2 xl:grid-cols-3">
                      {(scopeOpen ? loc : loc.slice(0, 12)).map((p) => (
                        <li key={p} className="min-w-0 truncate font-mono text-[12px] leading-4 text-sub" title={p}>
                          {shortPath(p, 44)}
                        </li>
                      ))}
                    </ul>
                    {!scopeOpen && loc.length > 12 && (
                      <div className="mt-1 text-[12px] text-sub/70">+{loc.length - 12}…</div>
                    )}
                  </div>
                </>
              )
            })()}
          </div>

          {cfg.scope.exclude_dirs.length > 0 && (
            <div className="pt-2 text-[12px] leading-4 text-sub">
              <span className="text-ink">{t('scope_excl').replace('{n}', String(cfg.scope.exclude_dirs.length))}</span>
              {/* 第 241 项：原来 6 条用「·」连成一行，很容易撑破卡片宽度 → 改成小标签网格 */}
              <ul className="mt-1 grid gap-1 sm:grid-cols-2 xl:grid-cols-3">
                {cfg.scope.exclude_dirs.slice(0, scopeOpen ? 999 : 12).map((w) => (
                  <li
                    key={w}
                    className="min-w-0 truncate rounded border border-line/50 bg-bg px-1.5 py-[1px] font-mono text-[12px] leading-4"
                    title={w}
                  >
                    {w}
                  </li>
                ))}
              </ul>
              {!scopeOpen && cfg.scope.exclude_dirs.length > 12 && (
                <div className="mt-1 text-sub/70">+{cfg.scope.exclude_dirs.length - 12}…</div>
              )}
            </div>
          )}
          <div className="pt-1 text-[12px] leading-4 text-sub/80">
            {/* 第 242 项：体积上限已取消（0 = 不限，Everything 的文件名索引同样不限）。
                这时不能显示「≤ 0 GB」那种误导文案，要换一句「不限体积」。 */}
            {cfg.scope.rules.size_limit > 0
              ? t('scope_rules')
                  .replace('{e}', String(cfg.scope.rules.ext))
                  .replace('{s}', String(Math.round(cfg.scope.rules.size_limit / 1073741824)))
              : t('scope_rules_nolimit').replace('{e}', String(cfg.scope.rules.ext))}
          </div>
          {/* 第 243 项：短音频（提示音/铃声/切片）、音乐平台曲库、已知软件目录
              里的素材都不入索引。这里把这三条说清楚，用户搜不到东西时能自己判断
              是不是被规则排掉了，而不是怀疑「索引坏了」。 */}
          {cfg.scope.rules.audio_min_seconds ? (
            <div className="pt-1 text-[12px] leading-4 text-sub/80">
              {t('scope_rules_media')
                .replace('{s}', String(cfg.scope.rules.audio_min_seconds))
                .replace('{n}', String(cfg.scope.rules.software_dirs ?? 0))}
            </div>
          ) : null}
            </>
          )}

          {(scopeMsg || upd) && (
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 pt-2 text-[12px] leading-4" role="status">
              {scopeMsg && (
                <span className={scopeMsg.ok ? 'text-accent' : 'text-red-500'}>{scopeMsg.text}</span>
              )}
              {scopeMsg?.ok && !editing && !upd && (
                <button onClick={runUpd} className="text-[12px] text-accent hover:underline">
                  {t('scope_upd_now')}
                </button>
              )}
              {upd?.busy && (
                <span className="text-sub">
                  {t('scope_updating')}
                  {upd.total ? ` ${upd.done}/${upd.total}` : ''}
                </span>
              )}
              {upd?.finished && <span className="text-accent">{t('scope_updated')}</span>}
            </div>
          )}
        </div>
      )}

      {/* 维护工具是常用操作，紧跟「索引范围」：看完扫了哪些盘，下一步就是重扫/体检 */}
      <Maintenance onDone={onRefreshed} />

      {/* 第 294 项（G）：整本 OCR 的入口从独立大按钮搬进「高级」折叠里
          （普通索引只啃扫描件前 6 页，整本识别是低频操作）。 */}
      <ExtrasPanel
        onPickSearch={onPickSearch}
        currentQ={currentQ}
        onRefreshed={onRefreshed}
        onOpenOcr={onOpenOcr}
        ocrRunning={ocrRunning}
      />
        </>
      )}
          </div>
        </div>
      </div>
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
  // 第 281 项：全库校验是慢动作，单独一个后台任务（体检里不再跑 quick_check）
  const [vf, setVf] = useState<VerifyState | null>(null)
  const [vfBusy, setVfBusy] = useState(false)

  const loadVf = () =>
    getVerify()
      .then((d) => setVf(d))
      .catch(() => setVf(null))

  const startVerify = () => {
    setVfBusy(true)
    runVerify()
      .then(() => loadVf())
      .catch(() => {})
      .finally(() => setVfBusy(false))
  }

  useEffect(() => {
    void loadVf()
  }, [])
  useEffect(() => {
    if (!vf?.running) return
    const id = window.setInterval(() => void loadVf(), 3000)
    return () => window.clearInterval(id)
  }, [vf?.running])

  const vfText = (): string => {
    if (vf?.running) return t('iv_running').replace('{n}', String(vf.started_s || 0))
    const last = vf?.last
    if (!last?.at) return t('iv_none')
    if (last.state === 'busy') return t('iv_busy')
    if (last.ok) {
      const s = Math.round(Number(last.elapsed || 0))
      return (last.stale ? t('iv_stale') : t('iv_ok'))
        .replace('{at}', last.at)
        .replace('{s}', String(s))
    }
    return t('iv_bad').replace('{st}', String(last.state || '?'))
  }

  const run = (force: boolean, deep = false) => {
    setBusy(true)
    setCopied(false)
    getHealth(force, getLang(), deep)
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
          disabled={busy}
          title={t('hc_deep_hint')}
          onClick={() => run(true, true)}
          className="rounded border border-line px-1.5 py-0.5 text-[11px] hover:bg-card disabled:opacity-60"
        >
          {busy ? t('hc_checking') : t('hc_deep')}
        </button>
        <button
          disabled={busy || !st?.ok}
          onClick={() => void copyAll()}
          className="rounded border border-line px-1.5 py-0.5 text-[11px] hover:bg-card disabled:opacity-60"
        >
          {copied ? t('hc_copied') : t('hc_copy')}
        </button>
        <button
          disabled={vfBusy || !!vf?.running}
          title={t('iv_run_hint')}
          onClick={startVerify}
          className="rounded border border-line px-1.5 py-0.5 text-[11px] hover:bg-card disabled:opacity-60"
        >
          {vf?.running ? t('iv_running_short') : t('iv_run')}
        </button>
      </div>
      <div className="mb-1.5 text-[10px] leading-4 text-sub/80">{t('hc_hint')}</div>
      <div className="mb-1.5 text-[10px] leading-4 text-sub">
        <span className="rounded border border-line/70 px-1 text-[9.5px]">{t('iv_label')}</span>{' '}
        {vfText()}
      </div>

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
    else if (op === 'send') setMsg(r.ok ? t('ex_ok') : t('err_send_local').replace('{e}', r.err || ''))
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
      {msg && <div className="pt-2 text-[12px] leading-5 text-accent">{msg}</div>}
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
  /** 第 207 项：在「常规设置」页里不显示「常规」标题与分隔线（导航分类名已经是标题） */
  headless = false,
}: {
  compat: boolean
  onCompat: (v: boolean) => void
  /** 自绘标题栏（第 180 项）：默认开，关掉退回系统标题栏 */
  titlebar: boolean
  onTitlebar: (v: boolean) => void
  headless?: boolean
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
    <section className={headless ? 'pt-2' : 'border-t border-line pt-3 mt-3'}>
      {!headless && <div className="mb-2 text-[14px] font-semibold">{t('care_title')}</div>}
      {!headless && <div className="mb-2 text-[10px] leading-4 text-sub">{t('care_hint')}</div>}

      <label className="flex items-start gap-2.5 py-2 text-[13px]">
        <input type="checkbox" checked={compat}
          onChange={(e) => onCompat(e.target.checked)} className="mt-0.5" />
        <span>
          {t('compat_on')}
          <span className="block text-[12px] leading-5 text-sub">{t('compat_hint')}</span>
        </span>
      </label>

      {/* 自绘标题栏（第 180 项）：窗口样式启动时定，所以这里标注「重启生效」。
          和 Python 读的是 settings.json 同一个键，改完下次启动两边一起变。 */}
      <label className="flex items-start gap-2.5 py-2 text-[13px]">
        <input type="checkbox" checked={titlebar}
          onChange={(e) => onTitlebar(e.target.checked)} className="mt-0.5" />
        <span>
          {t('care_titlebar')}
          <span className="block text-[12px] leading-5 text-sub">{t('care_titlebar_hint')}</span>
        </span>
      </label>

      <div className="mt-4 mb-1.5 text-[14px] font-semibold">{t('bm_title')}</div>
      <div className="mb-2 text-[12px] leading-5 text-sub">{t('bm_hint')}</div>
      <div className="flex flex-wrap gap-1.5">
        <button disabled={busy} onClick={() => void run(async () => {
          setMsg((await downloadSettingsBackup()) ? t('ex_ok') : t('ex_fail'))
        })} className="rounded-md border border-line px-3 py-2 text-[13px] hover:bg-card disabled:opacity-60">
          {t('bk_save')}
        </button>
        <label className="rounded-md border border-line px-3 py-2 text-[13px] hover:bg-card cursor-pointer">
          {t('bk_load')}
          <input type="file" accept=".zip" className="hidden" onChange={onFile} />
        </label>
      </div>

      {msg && <div className="pt-2 text-[12px] leading-5 text-accent">{msg}</div>}
    </section>
  )
}

/* --------- 索引集 / 保存搜索 / 索引诊断：三个免费新区块（0.8 开工令） --------- */

/** 第 294 项（G）：从维护区搬过来的两个诊断面板用到的类型（后端结构不变） */
type FailItem = { p: string; why: string; at: number; will_retry: boolean }
type ExcludeHit = { kind: string; rule: string }
type ExcludeResult = {
  ok: boolean
  p?: string
  excluded?: boolean
  hits?: ExcludeHit[]
  kept?: boolean
}

/** 第 294 项（H）：范围书签 = {名字, 目录}，存在本机 localStorage，不碰后端 */
type Bookmark = { name: string; dir: string }
const BOOK_KEY = 'sb_scopebooks'
const readBooks = (): Bookmark[] => {
  try {
    const v = JSON.parse(localStorage.getItem(BOOK_KEY) || '[]')
    if (!Array.isArray(v)) return []
    return v
      .filter((x) => x && typeof x.dir === 'string' && x.dir.trim())
      .map((x) => ({ name: String(x.name || '').trim(), dir: String(x.dir).trim() }))
  } catch {
    return []
  }
}
const writeBooks = (v: Bookmark[]) => {
  try {
    localStorage.setItem(BOOK_KEY, JSON.stringify(v))
  } catch {
    /* 隐私模式下写不进去就算了，本次会话内仍可用 */
  }
}

/** 只读 GET：诊断面板都是只读接口，不再往 api.ts 里加函数 */
async function jget<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { 'Sec-Fetch-Site': 'same-origin' } })
  return (await r.json()) as T
}

export function ExtrasPanel({
  onPickSearch,
  currentQ,
  onRefreshed,
  onOpenOcr,
  ocrRunning,
}: {
  onPickSearch?: (q: string) => void
  currentQ?: string
  onRefreshed?: () => void
  onOpenOcr?: () => void
  ocrRunning?: boolean
}) {
  const [libs, setLibs] = useState<LibSet[]>([])
  const [newName, setNewName] = useState('')
  const [allQ, setAllQ] = useState('')
  const [allHits, setAllHits] = useState<AllHit[] | null>(null)
  // 第 294 项（F）：「高级」折叠默认收起，见下方说明
  const [adv, setAdv] = useState(false)
  // 第 294 项（G）：诊断两件（解析失败清单 / 排除规则自检）从维护区搬进「高级」
  const [failOpen, setFailOpen] = useState(false)
  const [fails, setFails] = useState<FailItem[]>([])
  const [testPath, setTestPath] = useState('')
  const [exclude, setExclude] = useState<ExcludeResult | null>(null)
  // 第 294 项（H）：范围书签 —— 存「目录 + 名字」，点一下把 `D:\某目录\` 前缀填进搜索框。
  // 不新建索引，复用 qsyn 的「以斜杠结尾 = 限定文件夹」语法，本机 localStorage 保存。
  const [books, setBooks] = useState<Bookmark[]>(() => readBooks())
  const [bkName, setBkName] = useState('')
  const [bkDir, setBkDir] = useState('')
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

  /** 失败清单只在展开时拉一次：常驻轮询会跟后台任务抢连接。 */
  const toggleFailures = async () => {
    const next = !failOpen
    setFailOpen(next)
    if (!next) return
    try {
      const d = await jget<{ items?: FailItem[] }>('/api/index_failures')
      setFails(d.items || [])
    } catch {
      setFails([])
    }
  }

  const runExcludeTest = async () => {
    const p = testPath.trim()
    if (!p) return
    try {
      setExclude(await jget<ExcludeResult>(`/api/exclude_test?p=${encodeURIComponent(p)}`))
    } catch {
      setExclude({ ok: false })
    }
  }

  /** 范围书签：点一下 = 把「目录\\」当前缀塞进搜索框，关键词沿用当前输入的。
   *  没有当前关键词时只填目录，等于列该目录下的内容（qsyn 里斜杠结尾就是 folder 过滤）。 */
  const useBook = (b: Bookmark) => {
    const dir = b.dir.replace(/[\\/]+$/, '')
    const q = (currentQ || '').trim()
    // 路径里有空格时加双引号，否则 qsyn 会把它切成两个词、folder 过滤失效
    const pref = /\s/.test(dir) ? `"${dir}\\"` : `${dir}\\`
    onPickSearch?.(`${pref}${q ? ' ' + q : ' '}`)
  }

  const addBook = () => {
    const dir = bkDir.trim().replace(/[\\/]+$/, '')
    if (!dir) return
    const name = bkName.trim() || dir.split(/[\\/]/).pop() || dir
    const next = books.filter((b) => b.dir !== dir).concat([{ name, dir }])
    setBooks(next)
    writeBooks(next)
    setBkDir('')
    setBkName('')
  }

  const delBook = (dir: string) => {
    const next = books.filter((b) => b.dir !== dir)
    setBooks(next)
    writeBooks(next)
  }

  return (
    <div className="mt-4 space-y-3">
      {/* 第 294 项（H）：范围书签。它不新建索引、不重复扫盘 ——
          点一下只是把 `D:\某目录\` 当前缀填进搜索框（qsyn 里以斜杠结尾 = 限定文件夹），
          常用目录就不用每次手打路径了。 */}
      <div className="rounded-lg border border-line/70 bg-card/50 p-4">
        <div className="mb-1 text-[14px] font-semibold">{t('bm_title')}</div>
        <div className="text-[12px] leading-5 text-sub/80">{t('bm_hint')}</div>
        {books.length === 0 && <div className="mt-2 text-[13px] text-sub">{t('bm_none')}</div>}
        {books.map((b) => (
          <div key={b.dir} className="mt-1 flex items-baseline gap-2 text-[13px]">
            <button onClick={() => useBook(b)} className="min-w-0 max-w-[45%] truncate text-left text-accent hover:underline">
              {b.name}
            </button>
            <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-sub/80" title={b.dir}>
              {shortPath(b.dir, 46)}
            </span>
            <button onClick={() => delBook(b.dir)} className="shrink-0 text-sub hover:underline">
              {t('ex_lib_del')}
            </button>
          </div>
        ))}
        <div className="mt-2 flex gap-1">
          <input value={bkDir} onChange={(e) => setBkDir(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addBook() }}
            placeholder={t('bm_dir_ph')}
            className="min-w-0 flex-1 rounded border border-line bg-transparent px-2.5 py-1.5 font-mono text-[12px]" />
          <button onClick={async () => {
            const p = await pickFolder()
            if (p) setBkDir(p)
          }} className="shrink-0 rounded border border-line px-3 py-1.5 text-[13px] hover:border-accent">
            {t('scope_pick')}
          </button>
        </div>
        <div className="mt-1.5 flex gap-1">
          <input value={bkName} onChange={(e) => setBkName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addBook() }}
            placeholder={t('bm_name_ph')}
            className="min-w-0 flex-1 rounded border border-line bg-transparent px-2.5 py-1.5 text-[13px]" />
          <button onClick={addBook} disabled={!bkDir.trim()}
            className="shrink-0 rounded border border-line px-3 py-1.5 text-[13px] hover:border-accent disabled:opacity-60">
            {t('bm_add')}
          </button>
        </div>
      </div>

      {/* 第 294 项（F）：多库是「小众高级功能」，默认收起。
          用户原话：「我搜学习资料都整理好学习资料这个文件夹了，还需要他来搜干啥」——
          只搜某个文件夹本来就有 `D:\某目录\` 语法（qsyn 里以斜杠结尾 = 限定文件夹），
          不需要为此再建一份索引、重复扫一遍盘。 */}
      <div>
        <button
          onClick={() => setAdv((v) => !v)}
          aria-expanded={adv}
          className="flex w-full items-baseline justify-between text-[13px] text-sub hover:text-ink"
        >
          <span>{t('ex_adv')}</span>
          <span className="shrink-0 text-accent hover:underline">
            {adv ? t('ex_adv_close') : t('ex_adv_open')}
          </span>
        </button>
        {adv && <div className="mt-1 text-[12px] leading-5 text-sub/80">{t('ex_adv_tip')}</div>}
      </div>
      {adv && (
      <>
      <div className="rounded-lg border border-line/70 bg-card/50 p-4">
        <div className="mb-1 text-[14px] font-semibold">{t('ex_lib_title')}</div>
        {/* 第 296 项：用户「高级里面很多功能都没说明」—— 每块都写清「干什么用、什么时候用」 */}
        <div className="mb-2 text-[12px] leading-5 text-sub/80">{t('ex_lib_help')}</div>
        {libs.map((l) => (
          <div key={l.id} className="flex items-center gap-2 py-1 text-[13px]">
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
            className="min-w-0 flex-1 rounded border border-line bg-transparent px-2.5 py-1.5 text-[13px]" />
          <button disabled={busy || !newName.trim()}
            className="shrink-0 rounded bg-accent px-3 py-1.5 text-[13px] text-white disabled:opacity-60"
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
            className="min-w-0 flex-1 rounded border border-line bg-transparent px-2.5 py-1.5 text-[13px]" />
          <button onClick={runAll} className="shrink-0 rounded border border-line px-3 py-1.5 text-[13px]">
            {t('ex_lib_all')}
          </button>
        </div>
        {allHits !== null && (
          <div className="mt-2 max-h-40 overflow-auto text-[12px] leading-5 text-sub">
            {allHits.length === 0 ? t('ex_lib_no_hit') : allHits.map((h, i) => (
              <div key={i} className="truncate" title={h.p}>{h.n} · {h.p.split(/[\\/]/).pop()}</div>
            ))}
          </div>
        )}
      </div>

      {/* 第 294 项（G）：识别整本扫描件。普通索引本来就只啃扫描件前 6 页
          （brain.py: OCR_PAGES = 6），整本逐页很慢，属低频操作，所以收在「高级」里。 */}
      {onOpenOcr && (
        <div className="rounded-lg border border-line/70 bg-card/50 p-4">
          <div className="flex items-center gap-1.5 text-[14px] font-semibold">
            {t('ocr_btn')}
            {ocrRunning && <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent" />}
          </div>
          <div className="mt-1 text-[12px] leading-5 text-sub/80">{t('ex_adv_ocr_hint')}</div>
          <div className="mt-1 text-[12px] leading-5 text-sub/80">{t('ex_ocr_help')}</div>
          <button onClick={onOpenOcr}
            className="mt-2 rounded border border-line px-3 py-1.5 text-[13px] hover:border-accent">
            {t('ocr_title')}
          </button>
        </div>
      )}

      {/* 第 294 项（G）：解析失败清单 + 排除规则自检（原来在维护区常驻，现在归高级） */}
      <div className="rounded-lg border border-line/70 bg-card/50 p-4">
        <div className="mb-1.5 text-[12px] leading-5 text-sub/80">{t('ex_fail_help')}</div>
        <button onClick={() => void toggleFailures()}
          className="w-full text-left text-[13px] text-sub hover:text-ink">
          {t('maint_failures')}{fails.length ? ` (${fails.length})` : ''}
          <span className="ml-1 opacity-60">{failOpen ? '▾' : '▸'}</span>
        </button>
        {failOpen && (
          <div className="mt-2 max-h-44 overflow-auto rounded-lg bg-bg/40 p-2 text-[12px]">
            {fails.length === 0 ? (
              <div className="text-sub">{t('maint_failures_none')}</div>
            ) : fails.map((f) => (
              <div key={f.p} className="mb-1.5 last:mb-0">
                <div className="break-all text-ink">{f.p}</div>
                <div className="text-sub">
                  {f.why} · {t(f.will_retry ? 'maint_failures_retry' : 'maint_failures_skip')}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="mt-3 border-t border-line/70 pt-2.5">
          <div className="mb-1 text-[12px] text-sub">{t('maint_exclude_test')}</div>
          <div className="mb-1.5 text-[12px] leading-5 text-sub/80">{t('ex_excl_help')}</div>
          <div className="flex gap-1.5">
            <input
              value={testPath}
              onChange={(e) => setTestPath(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void runExcludeTest() }}
              placeholder={t('maint_exclude_ph')}
              className="min-w-0 flex-1 rounded border border-line bg-bg px-2.5 py-1.5 text-[12px]"
            />
            <button onClick={() => void runExcludeTest()}
              className="shrink-0 rounded border border-line px-2.5 py-1.5 text-[12px] hover:border-accent">
              {t('maint_exclude_btn')}
            </button>
          </div>
          {exclude && (
            <div className="mt-1.5 text-[12px]">
              {!exclude.ok ? (
                <span className="text-sub">{t('maint_net_fail')}</span>
              ) : (
                <>
                  <span className={exclude.excluded ? 'text-accent' : 'text-ink'}>
                    {t(exclude.excluded ? 'maint_exclude_yes' : 'maint_exclude_no')}
                  </span>
                  {!!exclude.hits?.length && (
                    <span className="ml-1.5 text-sub">
                      {t('maint_exclude_hint').replace('{n}', String(exclude.hits.length))}：
                      {exclude.hits.map((h) => h.rule).join('、')}
                    </span>
                  )}
                  {exclude.kept && (
                    <span className="ml-1.5 text-sub">· {t('maint_exclude_kept')}</span>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>
      </>
      )}

      <div className="rounded-lg border border-line/70 bg-card/50 p-4">
        <div className="mb-1 text-[14px] font-semibold">{t('ex_saved_title')}</div>
        <div className="mb-2 text-[12px] leading-5 text-sub/80">{t('ex_saved_help')}</div>
        {saved.length === 0 && <div className="text-[13px] text-sub">{t('ex_saved_empty')}</div>}
        {saved.map((x) => (
          <div key={x.q} className="flex items-center gap-2 py-1 text-[13px]">
            <button className="min-w-0 flex-1 truncate text-left text-accent hover:underline"
              onClick={() => onPickSearch?.(x.q)}>{x.name}</button>
            <button className="shrink-0 text-sub hover:underline"
              onClick={() => void run(() => savedOp('remove', { q: x.q }))}>{t('ex_lib_del')}</button>
          </div>
        ))}
        <button disabled={busy || !currentQ?.trim()}
          className="mt-2 rounded border border-line px-3 py-1.5 text-[13px] disabled:opacity-60"
          onClick={() => void run(() => savedOp('add', { q: currentQ?.trim() || '' }))}>
          {t('ex_saved_add')}
        </button>
      </div>

      {/* 「为什么搜不到」不在这里了（第 187 项）：使用帮助里有按钮，点了直接开
          WhyNotFoundModal，和「体检与报错」一样不跳设置。 */}
      {msg && <div className="text-[12px] text-sub" role="status">{msg}</div>}
    </div>
  )
}

/* ---------------------------------- 帮助 ---------------------------------- */

const KB = (): [string, string][] => [
  ['↑ ↓', t('kb1')],
  ['Enter', t('kb2')],
  ['Ctrl+Enter', t('kb3')],
  ['Ctrl+1~9', t('kb4')],
  // 第 311 项：第 308 项新加的标准键（Ctrl+C / Ctrl+Shift+C / 多选 / Delete）
  // 与第 310 项的网络搜索前缀，以前只在快搜窗里能用、设置页一个字都没写。
  ['Ctrl+C', t('kb10')],
  ['Ctrl+Shift+C', t('kb11')],
  ['Ctrl/Shift+点击', t('kb12')],
  ['Ctrl+G', t('kb9')],
  ['Ctrl+S', t('kb5')],
  ['Ctrl+F', t('kb6')],
  ['Esc', t('kb7')],
  ['Ctrl Ctrl', t('kb8')],
  ['dy / xhs / g + 空格', t('kb13')],
]

/** 搜索框语法速览（帮助弹窗与侧栏「可用的语法」共用同一份，别写两份） */
export const QS = (): [string, string][] => [
  ['ext:pdf', t('qs1')],
  ['size:>10m', t('qs2')],
  ['dm:7d', t('qs3')],
  ['-draft', t('qs4')],
  ['folder/', t('qs5')],
]

/* 第 356 项：快搜窗的「第一个词＝命令」。这些是敲下去就生效的动作，
   界面上没有开关，所以只在这里列出来 —— 和快捷键、语法放同一处，不另开页面。 */
const CMDS = (): [string, string][] => [
  ['shot', t('cd1')],
  ['verify', t('cd2')],
  ['cmd', t('cd3')],
  ['psh', t('cd4')],
]

const MORE = (): [string, string][] => [
  ['Empty', t('more1')],
  ['右键', t('more2')],
  ['drop', t('more3')],
  ['▦', t('more4')],
  ['托盘', t('more5')],
]

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

/** 第 301 项：原来是独立的 AboutModal（设置和侧栏各自弹一个窗）。
 *  现在整块内容搬进设置的「关于」分区，不再有单独的关于页 —— 但里面
 *  查更新 / 授权胶囊 / 官方链接 / 赞助入口的逻辑一行没动。 */
export function AboutSection({ onSupport }: {
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
  /** 第 380 项：开源组件弹窗（许可清单 + 模型工件指纹） */
  const [ossOpen, setOssOpen] = useState(false)
  /** 一键升级的下载进度（-1 = 还没拿到第一拍）；后端异步下载，前端轮询 */
  const [dlPct, setDlPct] = useState(-1)
  const dlTimer = useRef<number | null>(null)
  useEffect(() => {
    // 分区是按需挂载的（切到别的分区就卸载），所以这里不用再判 open
    getStats().then(setStats).catch(() => {})
    getPing().then(setPing).catch(() => {})
    setUp({ state: 'idle' })
    return () => {
      if (dlTimer.current) window.clearInterval(dlTimer.current)
      dlTimer.current = null
    }
  }, [])

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

  return (
    <>
      <div className="pb-2 text-[15px] font-semibold">{t('about_title')}</div>
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
        {/* 品牌名统一 Memex；副标语跟随界面语言 */}
        <div className="text-[17px] font-semibold leading-6">Memex</div>
        <div className="text-[13px] text-sub">{t('about_sub')}</div>
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
          <LicenseBox open pushedRight={!up.info?.newer} />
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

      {/* 第 380 项：开源许可 —— Apache-2.0 / MIT / BSD 都要求随分发附许可原文，
          这里给出组件清单和原文所在目录；正文从 _internal\THIRD-PARTY-NOTICES.md 读。 */}
      <div className="mt-3 border-t border-line pt-3">
        <div className="mb-1.5 text-[12px] text-sub">{t('oss_entry')}</div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setOssOpen(true)}
            data-act="open-licenses"
            className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-[12px] hover:bg-card"
          >
            <IconFile size={13} />
            {t('oss_open')}
          </button>
          <div className="min-w-0 flex-1 text-[11px] leading-5 text-sub">{t('oss_entry_hint')}</div>
        </div>
      </div>

      {ossOpen && <LicensesModal onClose={() => setOssOpen(false)} />}

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
    </>
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
  /* 第 381 项：遮罩必须铺满整个窗口。
     原来这里是 `absolute inset-x-0 bottom-0` + `top: var(--sb-title)`，依赖
     「Modal 挂在页面根节点下」这一前提。但这些弹窗（关于页开源组件、赞助、诊断、
     批量 OCR）都挂在设置抽屉内部，absolute 的参照系变成了抽屉的内容区 ——
     遮罩只从内容区往下铺，上方「设置 / 关于」标题带整条保持亮色，出现
     「上半白、下半灰」的断层（用户截图反馈「一半黑一半白」）。
     改为 portal 到 body + fixed inset-0：与 docview 的灯箱同一套做法，压满全窗。 */
  return createPortal(
    <div
      className="fixed inset-0 z-30 grid place-items-center bg-black/25"
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
    </div>,
    document.body,
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
  // 第 220 项：侧栏「全部」= 3650 天，说明行显示「全部」而不是「最近 3650 天」
  const all = days >= 3650
  if (L === 'zh') return all ? `全部 · 每天列出前 40 个（共 ${n} 条记录）` : `最近 ${days} 天 · 每天列出前 40 个（共 ${n} 条记录）`
  if (L === 'es') return all ? `Todo · primeros 40 por día (${n} en total)` : `${days} días · primeros 40 por día (${n} en total)`
  if (L === 'pt') return all ? `Tudo · primeiros 40 por dia (${n} no total)` : `${days} dias · primeiros 40 por dia (${n} no total)`
  if (L === 'hi') return all ? `सब · रोज़ पहले 40 (कुल ${n})` : `${days} दिन · रोज़ पहले 40 (कुल ${n})`
  return all ? `All · first 40 per day (${n} in total)` : `${days} days · first 40 per day (${n} in total)`
}

/* 第 315 项：这个组件 App 已经不再渲染了（首页改走搜索结果的网格/列表，一套布局）。
   留着是因为它自带「最近 N 天分组」的取数逻辑和 6 条测试，将来要做「按日期分组的首页」
   可以直接拿来；**但不要在 App 里再接第二份渲染路径** —— 两套 JSX 各管一套点击语义，
   才会出现「修好搜索页、首页还是坏的」。真要复活它，先把交互改造成和网格卡片同一套状态。 */
export function Timeline({
  onPick,
  onOpen,
  kind = 'all',
  sort = 'new',
  view = 'list',
  onHover,
  selected,
  onMenu,
  days = 14,
  onTotal,
}: {
  /** 第 314 项补：单击 = 钉住并在右侧详情里预览（不再直接打开） */
  onPick: (p: string) => void
  /** 双击才打开文件 */
  onOpen?: (p: string) => void
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

  // 列宽可拖拽（用户要自己拉宽「路径」列看全路径）：纯前端 localStorage 持久化，
  // 与 view 列表/网格模式同套路（sb_ 前缀），不动后端。表头与数据行共用同一份 grid 模板保证对齐。
  type ColW = { name: number; path: number; size: number; mtime: number }
  const DEFAULT_COLW: ColW = { name: 240, path: 240, size: 48, mtime: 112 }
  const MIN_COLW: ColW = { name: 80, path: 80, size: 44, mtime: 64 }
  const [colW, setColW] = useState<ColW>(() => {
    try {
      const raw = localStorage.getItem('sb_colw')
      if (raw) return { ...DEFAULT_COLW, ...JSON.parse(raw) }
    } catch {}
    return DEFAULT_COLW
  })
  useEffect(() => {
    try { localStorage.setItem('sb_colw', JSON.stringify(colW)) } catch {}
  }, [colW])
  const dragRef = useRef<{ key: keyof ColW; startX: number; startW: number } | null>(null)
  const onColResizeStart = (key: keyof ColW) => (e: ReactMouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragRef.current = { key, startX: e.clientX, startW: colW[key] }
    const move = (ev: MouseEvent) => {
      const r = dragRef.current
      if (!r) return
      const next = Math.max(MIN_COLW[key], r.startW + (ev.clientX - r.startX))
      setColW((w) => ({ ...w, [key]: next } as ColW))
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
  // 拖拽手柄：故意跨在「列右边界」上（right:-6px + 12px 宽），让用户在两列之间的缝隙处
  // 也能抓住——否则 6px 的细条贴在列内侧，点缝隙就抓空（实测只有最宽的「名称」列好抓，
  // 其余三列用户点不到）。中间画一条 1px 分隔线常驻可见，悬停高亮，提示这里能拖。
  const colHandle = (k: keyof ColW) => (
    <span
      onMouseDown={onColResizeStart(k)}
      className="group absolute right-[-6px] top-0 z-10 flex h-full w-3 cursor-col-resize select-none items-stretch justify-center hover:bg-accent/30"
      title={t('col_resize_hint')}
    >
      <span className="h-full w-px bg-line group-hover:bg-accent" />
    </span>
  )
  const gridCols = `16px minmax(${MIN_COLW.name}px, ${colW.name}fr) minmax(${MIN_COLW.path}px, ${colW.path}fr) minmax(${MIN_COLW.size}px, ${colW.size}fr) minmax(${MIN_COLW.mtime}px, ${colW.mtime}fr)`

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
        <div
          className="sticky top-0 z-[1] grid items-center gap-x-2 border-b border-line bg-bg px-2 pb-1 pt-1 text-[11px] text-sub"
          style={{ gridTemplateColumns: gridCols }}
        >
          <span className="w-4 shrink-0" />
          <span className="relative min-w-0 pr-2">
            <span className="block truncate">{t('col_name_only')}</span>
            {colHandle('name')}
          </span>
          <span className="relative min-w-0 pr-2">
            <span className="block truncate">{t('col_path')}</span>
            {colHandle('path')}
          </span>
          <span className="relative min-w-0 pr-2 text-left">
            <span className="block truncate">{t('col_size')}</span>
            {colHandle('size')}
          </span>
          <span className="relative min-w-0 pr-2 text-left">
            <span className="block truncate">{t('col_mtime')}</span>
            {colHandle('mtime')}
          </span>
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
                    onDoubleClick={() => onOpen?.(it.p)}
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
                onDoubleClick={() => onOpen?.(it.p)}
                onMouseEnter={() => onHover?.({ p: it.p, n: name, s: it.s, m: it.m })}
                onContextMenu={(e) => {
                  e.preventDefault()
                  onMenu?.(e, it.p)
                }}
                className={`grid w-full items-center gap-x-2 rounded-md px-2 py-[5px] text-left ${
                  selected === it.p ? 'bg-card' : 'hover:bg-card'
                }`}
                style={{ gridTemplateColumns: gridCols }}
              >
                <span className="w-4 shrink-0 text-sub">{iconForName(it.p)}</span>
                <span className="min-w-0 truncate">{midName(name, 80)}</span>
                <span className="min-w-0 truncate text-[11px] text-sub">{dirName(it.p)}</span>
                <span className="min-w-0 truncate text-left text-[11px] text-sub">{fmtSize(it.s)}</span>
                <span className="min-w-0 truncate text-left font-mono text-[11px] text-sub">{fmtTime(it.m)}</span>
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
/** 第 395 项：表格/幻灯片预览里的关键词高亮。
 *  以前只有下面那段「命中的正文」会标红，表格单元格是原样文字 —— 用户要的「深信服」
 *  全在表格里，却得自己一行行找。规则和列表标题高亮一致：按空格拆词、去掉
 *  ext:/size:/dm:/content: 这类语法项和 - 排除项，大小写不敏感。 */
export function qTokens(q: string): string[] {
  return q
    .split(/\s+/)
    .map((s) => s.trim())
    .filter((s) => {
      if (!s) return false
      if (/^[-－]/.test(s)) return false
      if (/^(ext|type|size|dm|content|内容|c):/i.test(s)) return false
      return true
    })
}

export function HiText({ text, q, strong = false }: { text: string; q: string; strong?: boolean }) {
  const raw = String(text ?? '')
  if (!raw || !q) return <>{raw}</>
  const toks = qTokens(q)
  if (!toks.length) return <>{raw}</>
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = raw.split(new RegExp(`(${toks.map(esc).join('|')})`, 'gi'))
  return (
    <>
      {parts.map((seg, i) =>
        i % 2 === 1 ? (
          <mark key={i} className={strong ? 'hl hl-strong' : 'hl'}>
            {seg}
          </mark>
        ) : (
          <span key={i}>{seg}</span>
        ),
      )}
    </>
  )
}

/** 第 401 项：多表 xlsx 里定位命中位置。
 *  以前详情只铺第 1 张表的前 25 行 × 14 列 —— 命中在第 2 张表、或第 81 行 / 第 25 列，
 *  用户就永远看不见（「这个表在详情页看不到命中的，他是多表结构」）。
 *  返回 {si: 表下标, ri: 行下标, ci: 列下标}；先按「命中的正文」片段整行比对，再按词。 */
export type SheetHit = { si: number; ri: number; ci: number }

export function findSheetHit(sheets: SheetTable[], q: string, ctx = ''): SheetHit {
  const none: SheetHit = { si: 0, ri: -1, ci: -1 }
  if (!sheets.length) return none
  const flat = (s: string) => String(s ?? '').replace(/\s+/g, '').toLowerCase()
  const c = flat(ctx)
  if (c.length >= 6) {
    for (let si = 0; si < sheets.length; si++) {
      const rs = sheets[si].rows
      for (let ri = 0; ri < rs.length; ri++) {
        if (flat(rs[ri].join(' ')).includes(c)) return { si, ri, ci: -1 }
      }
    }
  }
  const toks = qTokens(q).map((t) => t.toLowerCase())
  if (!toks.length) return none
  for (let si = 0; si < sheets.length; si++) {
    const rs = sheets[si].rows
    for (let ri = 0; ri < rs.length; ri++) {
      const row = rs[ri]
      for (let ci = 0; ci < row.length; ci++) {
        const cell = String(row[ci] ?? '').toLowerCase()
        if (cell && toks.some((t) => cell.includes(t))) return { si, ri, ci }
      }
    }
  }
  return none
}

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
  defaultExpand = false,
  onClose,
  onOpen,
  onReveal,
  onCopy,
  onStep,
}: {
  row: { p: string; n?: string; s?: number; m?: number; e?: string; b?: string; snip?: string; t?: string }
  /** 当前搜索词：取正文时用它定位命中位置 */
  q?: string
  /** 第 386 项：详情栏默认展开（设置里那个开关；缺省 = 原来那条窄栏） */
  defaultExpand?: boolean
  onClose: () => void
  onOpen: () => void
  onReveal: () => void
  onCopy: () => void
  /** 第 318 项：灯箱里左右翻上一张 / 下一张图（App 传进来，只在图片之间走） */
  onStep?: (d: number) => void
}) {
  const name = row.n || baseName(row.p)
  // 第 312 项：图片 / PDF 在详情里给一张预览图；Word / Excel / PPT 给正文首段。

  // 用户原话「在查看图片，pdf，word，excel 的时候，'详情'应该能够显示出来」——
  // 之前详情面板只有类型/大小/修改时间，图片还得跳出去用系统看图器。

  // Word/Excel 不做成图：本机 WPS 没注册 Shell 缩略图处理器（实测 docx 全是 None），

  // 硬撑一张图只会是空白破图；正文首段才是用户真正想确认「这是哪份文件」。

  // 预览取不到（204/解码失败）就安静隐藏，绝不留一块破图占位置。

  /* 第 314 项再补：时间轴的行只有 {p, n}，没有 e（扩展名）——不退回从路径里取后缀，
     从时间轴点出来的图片在详情里就永远不出图（用户看到的「点了还是没法预览」）。 */
  const thumbExt = String(row.e || row.p.split('.').pop() || '')
    .replace(/^\./, '')
    .toLowerCase()
  /* 第 336 项：视频也要在详情里出图 —— 后端 /api/thumb 早就有视频分支
     （VIDEO_EXTS 走 Windows 自带缩略图服务取一帧，8 秒超时 + 磁盘缓存），
     这里只是没把这个扩展名放进详情白名单。详情一次只选一个文件，
     最多一个请求，不存在网格那种几十个并发把 HTTP 线程占死的问题。 */
  const THUMB_IN_DTL = [
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tif', 'tiff', 'pdf',
    'mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'm4v', 'ts', 'webm', 'mpg', 'mpeg',
    /* 第 385 项：Word/PPT 也出页图 —— 后端先用本机 Office/WPS 转 PDF，
       再和 PDF 走同一个渲染分支（文本框、图表、SmartArt 都能画出来）。
       Excel 不在这里：宽表印成 PDF 只是左上角一小块（实测 10 页里 9 页空白），
       所以 xlsx 走下面「单元格表格」那套，字才是可读的。 */
    'doc', 'docx', 'wps', 'rtf', 'ppt', 'pptx', 'dps',
  ]
  const canThumb = row.t !== 'dir' && THUMB_IN_DTL.includes(thumbExt)
  const [thumbBad, setThumbBad] = useState(false)
  /* 第 314 项补充：详情里那张缩略图以前「点一下就打开文件」，用户原话
     「单击鼠标图片就是打开不是预览」。现在点图 = 原地放大（灯箱），只有「打开」按钮才启动外部程序。 */
  const [zoom, setZoom] = useState(false)
  /* 第 379 项：docx/xlsx 按原版式渲染（读原始文件字节 + 前端渲染器），
     其它格式仍旧只有纯文本 + 「打开」。 */
  const canLayoutView = row.t !== 'dir' && canLayout(row.e || row.p)
  const [docOpen, setDocOpen] = useState(false)
  /* 第 318 项：灯箱里滚轮放大 / 左右翻图。
     - 滚轮必须挂原生监听才能 preventDefault（React 的 onWheel 是 passive 的，拦不住页面滚动）；
     - 缩放用「宽度百分比」，因为 transform 的缩放不改变布局尺寸，滚不出被挡住的部分。 */
  const [scale, setScale] = useState(1)
  /* 第 325 项：PDF 翻页 + 「展开」。
     用户明确要求：默认宽度保持原样，只有点「展开」才把面板与页面图放大。
     页码从 1 开始（用户看到的就是「12 / 36」），后端按 0 基收。 */
  /* 第 386 项：初始是否展开由设置决定；用户点右上角「展开/收起」仍可随时改，
     只在设置里的值真的变了时才重新套一次（选别的文件不会被重置）。 */
  const [expand, setExpand] = useState(!!defaultExpand)
  useEffect(() => {
    setExpand(!!defaultExpand)
  }, [defaultExpand])
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(0)
  /* 第 390 项：关键词命中第几页（0 = 不知道/没命中，Word 文本框里的字 Find 搜不到就会是 0） */
  const [hitPg, setHitPg] = useState(0)
  /* 第 399 项：扫描件没有文本层，命中页由后台逐页 OCR 定位；这里显示 [已识别页, 总页数] */
  const [ocrProg, setOcrProg] = useState<[number, number] | null>(null)
  /* 第 406 项：整份 OCR 已经识别完（还没命中就是真的没有，别再让用户干等） */
  const [ocrDone, setOcrDone] = useState(false)
  /* 第 397 项：查询词变了要重新问「命中第几页」—— 但别每敲一个字就扫一遍 PDF 文本层，
     停手 400ms 再问。 */
  const [qNow, setQNow] = useState(q)
  useEffect(() => {
    const t = window.setTimeout(() => setQNow(q), 400)
    return () => window.clearTimeout(t)
  }, [q])
  /* 第 332 项：Word/Excel/PPT 没有分页图，正文按 900 字一块当「页」翻。
     textPage 1 基；textPages 由 /api/peek?pg= 返回。 */
  const [textPage, setTextPage] = useState(1)
  /* peek 提前声明：stepBy 里要读 peek.pages 决定箭头是翻页还是换文件，
     键盘监听 effect 的依赖数组也得在声明之后才能引用它。 */
  const [peek, setPeek] = useState<{ ok: boolean; text?: string; why?: string; matched?: boolean; page?: number; pages?: number } | null>(null)
  const [peeking, setPeeking] = useState(false)
  /* 第 385 项：PDF 与 Office 文档共用一套「页图 + 翻页」。名字仍叫 isPdf，
     是因为后面所有翻页/展开/箭头逻辑都挂在它身上，改名只会制造噪音。 */
  const PAGE_EXT = ['pdf', 'doc', 'docx', 'wps', 'rtf', 'ppt', 'pptx', 'dps']
  const isPdf = PAGE_EXT.includes(String(row.e || row.p.split('.').pop() || '').replace(/^\./, '').toLowerCase())
  const thumbSrc = (xl: boolean) =>
    `/api/thumb?p=${encodeURIComponent(row.p)}&big=1${xl ? '&xl=1' : ''}` +
    (isPdf ? `&pg=${page}` : '') +
    /* 第 390 项：Office 页图也带 q —— 后端用它在 Word 里定出命中页、只导那几页。
       第 387 项当初去掉 q 是怕每敲一个字就重排一次转换；现在 key 按文件去重、
       已经排队没开工的任务只会把 q 换新，不会重复转换，所以可以带回来。 */
    (isPdf ? `&q=${encodeURIComponent(qNow)}` : '') +
    /* 第 403 项：扫描件的高亮是后台 OCR 画出来的，第一张图往往在 OCR 跑完之前就发出去了
       （没框），之后浏览器还会按 max-age 缓存一整天 —— 用户就会觉得「扫描件一直不标」。
       把「已识别页数」当成版本号带上：OCR 一有进展地址就变，服务端缓存键也跟着换，
       才会重渲出带黄框的那一页。 */
    (isPdf && (ocrProg || hitPg)
      ? `&hv=${ocrProg ? 'o' + ocrProg[0] : 'h' + hitPg}`
      : '') +
    /* 重试用：只用来打破浏览器对 204 的缓存，让同一个地址能真的重发 */
    (pgTry ? `&t=${pgTry}` : '')

  /* 第 387 项：Office 首次预览要后台把文档转成 PDF，**几秒到几十秒都有可能**
     （实测 57MB 的 docx 25 秒都转不完，第 385 项那版的同步超时就是在这里把整机拖卡的）。
     所以详情栏不再「失败一次就判死」：隔一会儿自动重发，最多 30 次（约 70 秒）。
     PDF 与图片不依赖转换，仍旧一次失败就回落。 */
  const isOfficePage = isPdf && thumbExt !== 'pdf'
  const [pgTry, setPgTry] = useState(0)
  const [thmbOk, setThmbOk] = useState(false)
  const retryTimer = useRef<number | null>(null)
  /* 第 390 项：「已经自动跳到命中页」只跳一次 —— 用户自己翻走之后不许再拽回来 */
  const jumpedHit = useRef(false)
  useEffect(() => {
    setPgTry(0)
    setThmbOk(false)
    if (retryTimer.current) window.clearTimeout(retryTimer.current)
    retryTimer.current = null
  }, [row.p, page])
  useEffect(
    () => () => {
      if (retryTimer.current) window.clearTimeout(retryTimer.current)
    },
    [],
  )
  const zoomRef = useRef<HTMLDivElement | null>(null)
  /* 第 332 项：← → 的语义改成「先在当前文件里翻页，翻到头才换文件」。
     用户原话：「点击→他会跳转到下一个文件，而不是下一页……如果最后一页也看完了→
     才是下一份文件，这样的逻辑才对。」Word/Excel 在详情里只有正文首段、没有分页图，
     本来就没有「下一页」，所以它们仍然直接换文件。 */
  const stepBy = (d: number) => {
    /* 第 332 项：先在本文件里翻页，翻到头才换文件（规则见 lib/paging.ts，有单测）。 */
    const box = {
      isPdf,
      page,
      pages,
      textPage,
      textPages: peek?.ok ? Number(peek.pages || 0) : 0,
    }
    const r = resolveStep(box, d)
    if (r.to === 'page') {
      setScale(1)
      setPage(r.page)
      return
    }
    if (r.to === 'textPage') {
      setScale(1)
      setTextPage(r.textPage)
      void loadPeek(r.textPage)
      return
    }
    setScale(1)
    onStep?.(d)
  }
  /* 第 318 项：换到另一行时**不要关灯箱** —— 用户就是要在放大状态下左右翻图。
     只重置「这一张取不到图」的标记和缩放倍数。 */
  useEffect(() => { setThumbBad(false); setScale(1) }, [row.p])
  /* 第 325 项：换文件时页码归 1，并问一下这个 PDF 有多少页（不问就没有「/ 36」）。
     第 387 项：Office 是「先排队、几秒后才出图」，刚选中时 pdfmeta 还是 0（PDF 还没转出来）。
     第 388 项：页数要**轮询**到拿到为止，否则翻页器一直不出现（真机截图踩到）。
     第 390 项：后端还会回「关键词命中第几页」，这里拿到就直接停在那一页 ——
     180 页的 docx 用户要看的就那一页，不是第 1 页。用户自己翻过页之后就不再跳。 */
  useEffect(() => {
    setPage(1)
    setPages(0)
    setHitPg(0)
    setOcrProg(null)
    setOcrDone(false)
    jumpedHit.current = false
    if (!isPdf) return
    let alive = true
    let tries = 0
    let timer: number | null = null
    const ask = () => {
      /* 第 398 项：把「命中的正文」那一段一起带上 —— 后端用它把命中页对齐到
         片段所在的页，而不是「这份文档里第一个出现这个词的页」。 */
      fetch(`/api/pdfmeta?p=${encodeURIComponent(row.p)}&q=${encodeURIComponent(qNow)}` +
        `&s=${encodeURIComponent((row.b || row.snip || '').replace(/<\/?mark>/gi, ''))}`,
        { headers: { 'Sec-Fetch-Site': 'same-origin' } })
        .then((r) => r.json())
        .then((j: { pages?: number; hit?: number; done?: boolean; state?: string; ocr?: [number, number] | null; ocr_done?: boolean }) => {
          if (!alive || !j) return
          /* 第 391 项：后台明确说「这份转不出来」（卡死/失败冷却中），就别再转圈了 ——
             直接停掉重试、显示失败提示（还能用「打开」看原件）。 */
          if (j.state === 'failed') {
            setThumbBad(true)
            return
          }
          const op = j.ocr ? [Number(j.ocr[0]), Number(j.ocr[1])] as [number, number] : null
          setOcrProg(op)
          setOcrDone(!!j.ocr_done)
          const p = Number(j.pages || 0)
          if (p) setPages(p)
          const hit = Number(j.hit || 0)
          if (hit !== hitPg) setHitPg(hit)
          if (hit > 1 && !jumpedHit.current) {
            jumpedHit.current = true
            setPage(hit)
          }
          /* 页数到手、而且已按当前查询词算过（done）就不用再问了；
             Office 换了关键词时会重导那一段，done 变 true 之前继续等 → 才能跳到新的命中页 */
          if (p > 0 && j.done !== false) return
          /* 第 399 项：扫描件在 OCR，最多等 60 轮（约 90 秒），找到了就跳过去 */
          const limit = op ? 60 : 20
          if (++tries < limit) timer = window.setTimeout(ask, op ? 1500 : 1500)
        })
        .catch(() => {
          if (alive && ++tries < 20) timer = window.setTimeout(ask, 1500)
        })
    }
    ask()
    return () => {
      alive = false
      if (timer) window.clearTimeout(timer)
    }
  }, [row.p, isPdf, qNow, row.b, row.snip])
  useEffect(() => {
    if (!zoom) return
    const el = zoomRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      // 向上滚（deltaY < 0）= 放大
      setScale((s) => Math.min(4, Math.max(0.5, Math.round((s + (e.deltaY < 0 ? 0.15 : -0.15)) * 100) / 100)))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [zoom])
  useEffect(() => {
    if (!zoom) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setZoom(false)
        return
      }
      // 第 318 项：灯箱里 ← → 翻上一张 / 下一张（会同时把列表选中挪过去）
      // 第 332 项：PDF 先在本文件里翻页，翻到第一/最后一页才换文件。
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        if (!onStep) return
        e.preventDefault()
        e.stopPropagation()
        stepBy(e.key === 'ArrowLeft' ? -1 : 1)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoom, onStep, isPdf, page, pages, textPage, peek])
  // 命中片段是现成的（搜索时后端已带回），直接展示；
  // 没有片段（只是文件名命中）才让用户点一下按需读正文——docs 表按 path 查是全表扫，
  // 不能挂在 hover 上，否则鼠标一划就几百毫秒。
  useEffect(() => {
    setPeek(null)
    setPeeking(false)
    setTextPage(1)
  }, [row.p])

  /* 第 332 项：pg 传了就是「按块看正文」，箭头才能翻页而不是换文件。 */
  const loadPeek = async (pg = 1) => {
    setPeeking(true)
    try {
      const r = await fetch(
        `/api/peek?p=${encodeURIComponent(row.p)}&q=${encodeURIComponent(q)}&pg=${pg}`,
        { headers: { 'Sec-Fetch-Site': 'same-origin' } },
      )
      const j = (await r.json()) as { ok: boolean; text?: string; why?: string; matched?: boolean; page?: number; pages?: number }
      setPeek(j)
      setTextPage(Number(j.page || pg) || 1)
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
  /* 第 385 项：Word/Excel/PPT 选中就**自动**内嵌预览，不再要求先点「预览版面」。
     体积超标（>12MB 的扫描件）不自动铺版面 —— 硬铺会把 WebView 卡住，那种还是留给大窗。 */
  const inlineExt = String(row.e || row.p.split('.').pop() || '').replace(/^\./, '').toLowerCase()
  /* 第 385 项：Excel 系（xls/xlsx/et）用「单元格表格」当主预览 —— 可读、可滚，
     不像 Page 图那样被打印分页切成一小块。其余（docx/pptx）先出页图，
     页图取不到时才回落到纯前端渲染。 */
  /* 第 405 项：CSV/TSV 也走「单元格表格」（以前只有 xls/xlsx/et，csv 连预览块都不出现）。
     pptx 的幻灯片文字预览改成「主预览」：WPS 转页图要好几秒（60MB 的课件更久），
     这期间详情栏以前是空的；现在先立刻铺文字，页图转好了再叠在上面。 */
  const SHEET_EXT = ['xls', 'xlsx', 'et', 'csv', 'tsv']
  const INLINE_PRIMARY = [...SHEET_EXT, 'pptx']
  const inlinePrimary = INLINE_PRIMARY.includes(inlineExt)
  const inlineOversize = !!(row.s && row.s > INLINE_MAX_BYTES)
  const canInlineView = row.t !== 'dir' && canInline(inlineExt) && !inlineOversize
  type InlinePreview = {
    st: 'idle' | 'loading' | 'ok' | 'fail'
    pages?: number
    sheets?: SheetTable[]
    slides?: SlideText[]
    why?: string
  }
  const [inline, setInline] = useState<InlinePreview>({ st: 'idle' })
  /* 第 401 项：多表 xlsx 当前显示哪张表（null = 跟着命中走） */
  const [sheetPick, setSheetPick] = useState<number | null>(null)
  const inlineBox = useRef<HTMLDivElement | null>(null)
  const sheetBox = useRef<HTMLDivElement | null>(null)
  const sheetGrid = useRef<HTMLDivElement | null>(null)
  const hitRowRef = useRef<HTMLTableRowElement | null>(null)
  const hitCellRef = useRef<HTMLTableCellElement | null>(null)
  useEffect(() => {
    setInline({ st: 'idle' })
    setSheetPick(null)
    if (!canInlineView) return
    if (!inlinePrimary && !thumbBad) return   // 有页图就别再铺一份
    const el = inlineBox.current
    let alive = true
    setInline({ st: 'loading' })
    const pump = async () => {
      try {
        if (inlineExt === 'docx') {
          // 只有 docx 需要容器（docx-preview 直接把页面挂进去）
          if (!el) {
            if (alive) setInline({ st: 'idle' })
            return
          }
          const pages = await renderDocxInline(row.p, el, INLINE_MAX_BYTES)
          if (alive) setInline({ st: 'ok', pages })
        } else if (SHEET_EXT.includes(inlineExt)) {
          /* 第 401 项：多表文件命中可能在别的表 / 靠后的行与列 —— 行给到 400、列 26，
             全部工作表都读，前端再按命中位置切窗口显示。 */
          const sheets = await readSheets(row.p, 400, 26)
          if (alive) setInline({ st: 'ok', sheets })
        } else if (inlineExt === 'pptx') {
          const slides = await readPptxSlides(row.p, 8, INLINE_MAX_BYTES)
          if (alive) setInline(slides.length ? { st: 'ok', slides } : { st: 'fail', why: '幻灯片里没有可读文字' })
        } else {
          if (alive) setInline({ st: 'idle' })
        }
      } catch (e) {
        if (alive) setInline({ st: 'fail', why: (e as Error).message || String(e) })
      }
    }
    void pump()
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.p, canInlineView, inlineExt, thumbBad, inlinePrimary])
  /* 第 401 项：表格铺好后跳到命中单元格 —— 看起来就和「命中页」一样，不用自己翻。
     只滚表格这个盒子（不 scrollIntoView，免得把整个详情栏/页面一起滚跑）。 */
  useEffect(() => {
    if (inline.st !== 'ok' || !inline.sheets) return
    /* 铺表、字体、横向滚动都会改布局，一次可能算不准 —— 连做三次（60/250/600ms）。 */
    const jump = () => {
      const box = sheetGrid.current
      const cellEl = hitCellRef.current
      if (!box || !cellEl) return
      const left = cellEl.offsetLeft - box.clientWidth / 3
      box.scrollLeft = Math.max(0, Math.min(left, box.scrollWidth - box.clientWidth))
    }
    const t1 = window.setTimeout(jump, 60)
    const t2 = window.setTimeout(jump, 250)
    const t3 = window.setTimeout(jump, 600)
    return () => {
      window.clearTimeout(t1)
      window.clearTimeout(t2)
      window.clearTimeout(t3)
    }
  }, [inline, sheetPick])
  // 第 312 项：Word/Excel/PPT 不显示图，改成进面板就把正文首段拉出来。
  // 只在这三类上自动拉；txt/pdf 这类本来就长，还是让用户自己点「读取正文」，
  // 免得选一下就扫全表（/api/peek 是按 path 查 docs）。
  const AUTO_PEEK_EXT = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'wps', 'et', 'dps']
  useEffect(() => {
    if (row.t === 'dir' || snip) return
    if (!AUTO_PEEK_EXT.includes(e0)) return
    if (canThumb && !thumbBad) return   // 第 385 项：已经有页图了，别再拉纯文本
    if (canInlineView) return           // Excel 表格预览 / 页图失败时的回落，都不重复拉文本
    void loadPeek()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.p])
  return (
    <aside className={`flex h-full ${expand ? 'w-[560px]' : 'w-[340px]'} shrink-0 flex-col overflow-auto border-l border-line px-3 py-2 text-[12px]`}>
      <div className="flex items-center justify-between pb-2">
        <span className="text-[11px] uppercase tracking-wide text-sub">{t('dtl_title')}</span>
        <div className="flex items-center gap-2">
          {/* 第 325 项：展开 / 收起。展开后详情栏和 PDF 页面一起变大，正文小字才读得清。 */}
          {(canThumb || isPdf) && (
            <button
              onClick={() => setExpand((x) => !x)}
              title={expand ? t('collapse') : t('expand')}
              className="rounded border border-line px-1.5 py-0.5 text-[10px] text-sub hover:text-ink"
            >
              {expand ? t('collapse') : t('expand')}
            </button>
          )}
          <button onClick={onClose} className="text-sub hover:text-ink" aria-label={t('dtl_close')}>
            <IconClose size={14} />
          </button>
        </div>
      </div>
      {canThumb && !thumbBad && (
        /* 第 317 项：这块以前 max-h 200px + 320×400 的缩略图，PDF 首页的正文小字糊成一片
           （用户截图「看 pdf 完全看不清」）。现在取 900×1200 的大图、框也放到 420px；
           真正要读字就点一下进灯箱（灯箱按原始像素铺，可以滚）。
           第 325 项：展开后换 1600×2200 的超清图，并把框抬高到 70vh。 */
        <button
          onClick={() => setZoom(true)}
          title={t('preview')}
          className={`mb-1 grid ${expand ? 'max-h-[70vh]' : 'max-h-[420px]'} w-full cursor-zoom-in place-items-center overflow-hidden rounded-md bg-card`}
        >
          <img
            src={thumbSrc(expand)}
            alt=""
            className={`${expand ? 'max-h-[70vh]' : 'max-h-[420px]'} w-auto max-w-full object-contain`}
            onLoad={() => setThmbOk(true)}
            onError={() => {
              /* 第 387 项：Office 在后台排队转换，这里等它转好；其它格式一次失败就回落。 */
              const cap = isOfficePage ? 30 : 1
              if (pgTry + 1 < cap) {
                if (retryTimer.current) window.clearTimeout(retryTimer.current)
                retryTimer.current = window.setTimeout(
                  () => setPgTry((v) => v + 1),
                  pgTry < 4 ? 1200 : 2500,
                )
              } else {
                setThumbBad(true)
              }
            }}
          />
        </button>
      )}
      {canThumb && isOfficePage && !thmbOk && (
        <div className="-mt-0.5 mb-2 text-center text-[10px] text-sub">
          {thumbBad
            ? '这份文档没能生成页图预览（文件太大或排版引擎卡住了），点「打开」直接看原件即可'
            : '首次预览正在排版转换，大文件要十几秒（转好会缓存，下次秒开）'}
        </div>
      )}
      {isPdf && pages > 1 && (
        <div className="mb-2 flex items-center justify-center gap-2 text-[11px] text-sub">
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            aria-label={t('prev')}
            className="rounded border border-line px-2 py-0.5 hover:text-ink disabled:opacity-40"
          >
            ‹
          </button>
          <span className="tabular-nums">{page} / {pages}</span>
          {/* 第 390 项：命中页 —— 用户翻走了还能一键跳回去；停在命中页时直接标出来 */}
          {hitPg > 0 && page !== hitPg && (
            <button
              onClick={() => setPage(hitPg)}
              title={`关键词命中第 ${hitPg} 页`}
              className="rounded border border-line px-1.5 py-0.5 text-[10px] hover:text-ink"
            >
              命中 {hitPg}
            </button>
          )}
          {hitPg > 0 && page === hitPg && (
            <span className="text-[10px] opacity-70">命中页</span>
          )}
          <button
            disabled={page >= pages}
            onClick={() => setPage((p) => Math.min(pages, p + 1))}
            aria-label={t('next')}
            className="rounded border border-line px-2 py-0.5 hover:text-ink disabled:opacity-40"
          >
            ›
          </button>
        </div>
      )}
      {/* 第 397/399 项：扫描件的命中页靠后台 OCR 找，识别期间给进度。
          第 406 项：Word/PPT 里贴的扫描图也一样（这时 isOfficePage 为真，也要显示）；
          而且只有「OCR 真的跑过」才提示 —— 普通文字 PDF 搜不到词不该看见这句话。 */}
      {q && hitPg === 0 && pages > 0 && (ocrProg || ocrDone) && (
        <div className="mb-1 text-center text-[10px] text-sub">
          {ocrProg
            ? (ocrProg[1] > 0
              ? `图片型页面：正在识别第 ${ocrProg[0]} / ${ocrProg[1]} 页，找到命中词就跳过去…`
              : `图片型页面：正在识别第 ${ocrProg[0]} 页，找到命中词就跳过去…`)
            : '这份文件里是图片，OCR 已识别完，页面上没找到这个词；文字内容见下面「命中的正文」'}
        </div>
      )}
      {zoom && !thumbBad && (
        <div
          ref={zoomRef}
          className="fixed inset-0 z-[70] overflow-auto bg-black/80 p-6"
          onClick={() => setZoom(false)}
          role="dialog"
          aria-modal="true"
          aria-label={name}
        >
          <div className="mx-auto grid min-h-full w-fit place-items-center">
            {/* 第 318 项：宽度按百分比给（默认 92vw），滚轮在手改这个数 ——
                transform 缩放不占布局，放大后画面会被裁掉、滚不到边。 */}
            <img
              src={thumbSrc(true)}
              alt=""
              className="rounded-md object-contain shadow-2xl"
              style={{ width: `${92 * scale}vw`, maxWidth: 'none' }}
              draggable={false}
            />
          </div>
          {/* 左右翻图：点按钮不关灯箱（onClick 里停掉冒泡）
              第 332 项：PDF 同一份里先翻页，翻到头才换文件（见 stepBy）。 */}
          {onStep && (
            <>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  stepBy(-1)
                }}
                aria-label={isPdf && pages > 1 ? t('prev') : '上一张'}
                className="fixed left-2 top-1/2 z-[71] grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full bg-black/50 text-[18px] text-white hover:bg-black/70"
              >
                ‹
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  stepBy(1)
                }}
                aria-label={isPdf && pages > 1 ? t('next') : '下一张'}
                className="fixed right-2 top-1/2 z-[71] grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full bg-black/50 text-[18px] text-white hover:bg-black/70"
              >
                ›
              </button>
            </>
          )}
          <div className="pointer-events-none fixed bottom-4 left-0 right-0 text-center text-[11px] text-white/80">
            {name}
            {isPdf && pages > 1 ? ` · ${page} / ${pages}` : ''} · {Math.round(scale * 100)}% · 滚轮缩放 · ← →{' '}
            {isPdf && pages > 1 ? '翻页' : '翻图'} · {t('close')}（Esc / 点空白）
          </div>
        </div>
      )}
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
        {row.t !== 'dir' && (
          <div className="flex gap-2">
            <dt className="w-14 shrink-0 text-sub">{t('dtl_size')}</dt>
            <dd>{fmtSize(row.s)}</dd>
          </div>
        )}
        <div className="flex gap-2">
          <dt className="w-14 shrink-0 text-sub">{t('dtl_mtime')}</dt>
          <dd className="font-mono text-[11px]">{fmtTime(row.m)}</dd>
        </div>
      </dl>
      {row.t !== 'dir' && !(thumbBad && inline.st === 'fail')
        && ((canInlineView && (inlinePrimary || thumbBad)) || inlineOversize) && inlineExt && (
        <div className="mt-3">
          <div className="mb-1 flex items-center justify-between text-[10px] text-sub">
            <span>
              {inlineExt === 'docx' ? '版面预览' : SHEET_EXT.includes(inlineExt) ? '表格预览' : '幻灯片预览'}
              {inline.st === 'ok' && inline.pages ? ` · ${inline.pages} 页` : ''}
            </span>
            {inline.st === 'loading' && <span>{t('dtl_reading')}</span>}
            {inlineOversize && <span>大于 48MB，不自动渲染</span>}
          </div>
          {canInlineView && !inlinePrimary && (
            <div
              ref={inlineBox}
              className="max-h-[46vh] overflow-auto rounded-md border border-line bg-card p-1"
            />
          )}
          {inline.st === 'ok' && inline.sheets && inline.sheets.length > 0 && (() => {
            const sheets = inline.sheets
            const hit = findSheetHit(sheets, q, snip)
            const si = sheetPick != null && sheetPick >= 0 && sheetPick < sheets.length ? sheetPick : hit.si
            const sh = sheets[si]
            const focus = si === hit.si ? hit.ri : -1
            /* 命中行放在窗口第 4 行 —— 不再依赖 scrollTop（表格刚铺出来时高度还算不准，
               之前 scrollTop 会被后面的布局变化吃掉）。 */
            const start = focus >= 0 ? Math.max(0, focus - 3) : 0
            const end = focus >= 0 ? Math.min(sh.rows.length, focus + 30) : Math.min(sh.rows.length, 60)
            /* 第 401 项：窗口从命中行附近开始时，把第 1 行（表头）钉在顶部 ——
               不然滚过去只剩数据，看不出哪一列是什么。 */
            const shown: Array<{ ri: number; r: string[]; head: boolean }> = []
            if (start > 0 && sh.rows.length) shown.push({ ri: 0, r: sh.rows[0], head: true })
            for (let i = start; i < end; i++) shown.push({ ri: i, r: sh.rows[i], head: false })
            return (
              <div ref={sheetBox} className="rounded-md border border-line bg-card p-1">
                {sheets.length > 1 && (
                  <div className="mb-1 flex max-h-[52px] flex-wrap gap-1 overflow-auto px-0.5">
                    {sheets.map((s, i) => (
                      <button
                        key={s.name}
                        onClick={() => setSheetPick(i)}
                        title={s.name}
                        className={`max-w-[110px] truncate rounded border px-1.5 py-0.5 text-[10px] ${
                          i === si ? 'border-line bg-bg text-ink' : 'border-transparent text-sub hover:text-ink'
                        }`}
                      >
                        {s.name}
                      </button>
                    ))}
                  </div>
                )}
                <div className="mb-1 flex items-center justify-between gap-2 px-1 text-[10px] text-sub">
                  <span className="truncate">{sh.name}</span>
                  {hit.ri >= 0 && hit.si === si && (
                    <span className="shrink-0 tabular-nums">
                      {t('dtl_sheet_hit_row')} {hit.ri + 1}
                      {hit.ci >= 0 ? ` · ${hit.ci + 1}` : ''}
                    </span>
                  )}
                </div>
                {/* 第 401 项：命中行不一定在开头 —— 只铺命中附近的一段（命中行高亮，自动滚过去），
                    其余行靠点表名/打开原件看；整表 400 行全铺会把详情栏卡住。 */}
                <div ref={sheetGrid} className="max-h-[42vh] overflow-auto">
                  <table className="w-full border-collapse text-[10px] leading-4">
                    <tbody>
                      {shown.map(({ ri, r, head }) => {
                        const isHitRow = ri === focus
                        return (
                          <tr
                            key={`${head ? 'h' : 'r'}${ri}`}
                            ref={isHitRow ? hitRowRef : undefined}
                            className={
                              head
                                ? 'sticky top-0 z-10 bg-bg font-medium'
                                : isHitRow
                                  ? 'bg-card font-medium'
                                  : ''
                            }
                          >
                            {r.map((c, ci) => {
                              const cellHit = isHitRow && ci === hit.ci
                              return (
                                <td
                                  key={ci}
                                  ref={cellHit ? hitCellRef : undefined}
                                  className={`max-w-[120px] truncate border border-line px-1 py-0.5 align-top ${
                                    ci === 0 ? `sticky left-0 ${head ? 'z-20 bg-bg' : 'z-10 bg-card'}` : ''
                                  }`}
                                >
                                  <HiText text={c} q={q} strong />
                                </td>
                              )
                            })}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })()}
          {inline.st === 'ok' && inline.slides && inline.slides.length > 0 && (
            <div className="max-h-[46vh] space-y-1 overflow-auto">
              {inline.slides.map((sl) => (
                <div key={sl.n} className="rounded-md border border-line bg-card px-2 py-1.5">
                  <div className="mb-0.5 text-[10px] text-sub">第 {sl.n} 页</div>
                  <div className="text-[11px] font-medium leading-4">
                    <HiText text={String(sl.title || '')} q={q} strong />
                  </div>
                  {sl.lines.length > 0 && (
                    <ul className="mt-0.5 list-disc pl-4 text-[10px] leading-4 text-ink/80">
                      {sl.lines.slice(0, 6).map((ln, i) => (
                        <li key={i} className="truncate">
                          <HiText text={ln} q={q} />
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
          {inline.st === 'fail' && !thumbBad && (
            <div className="rounded-md bg-card px-2 py-1.5 text-[10px] leading-4 text-sub">
              这份文档的版面试不出来（可能是受保护或加密的文件），点「打开」看原件即可
            </div>
          )}
        </div>
      )}
      {snip ? (
        <div className="mt-3 rounded-md bg-card px-2 py-1.5 text-[11px] leading-5 text-ink/85">
          <div className="mb-1 text-[10px] text-sub">{t('dtl_hit_body')}</div>
          <Marked text={snip} />
        </div>
      ) : peek?.ok ? (
        <div className="mt-3 rounded-md bg-card px-2 py-1.5 text-[11px] leading-5 text-ink/85">
          <div className="mb-1 flex items-center justify-between text-[10px] text-sub">
            <span>{peek.matched ? t('dtl_peek_hit') : t('dtl_peek_head')}</span>
            {/* 第 332 项：正文超过一屏时给页码，箭头/按钮都能翻 */}
            {(peek.pages || 1) > 1 && (
              <span className="tabular-nums">{textPage} / {peek.pages}</span>
            )}
          </div>
          {/* 第 312 项：Word/Excel 自动读正文时只给开头 300 字 ——
              整篇 6000 字糊在 300px 宽的详情里，翻到底反而找不到「打开」按钮。
              第 332 项：能翻页（pages > 1）时给整块（900 字），不再砍成 300 字。 */}
          <Marked
            text={
              peek.matched || (peek.pages || 1) > 1
                ? (peek.text || '')
                : ((peek.text || '').length > 300
                    ? (peek.text || '').slice(0, 300) + '…'
                    : (peek.text || ''))
            }
          />
          {(peek.pages || 1) > 1 && (
            <div className="mt-1.5 flex items-center justify-center gap-2 text-[11px] text-sub">
              <button
                disabled={textPage <= 1}
                onClick={() => { setTextPage((p) => { const n = Math.max(1, p - 1); void loadPeek(n); return n }) }}
                className="rounded border border-line px-2 py-0.5 hover:text-ink disabled:opacity-40"
                aria-label={t('prev')}
              >
                ‹
              </button>
              <button
                disabled={textPage >= (peek.pages || 1)}
                onClick={() => { setTextPage((p) => { const n = Math.min(peek.pages || 1, p + 1); void loadPeek(n); return n }) }}
                className="rounded border border-line px-2 py-0.5 hover:text-ink disabled:opacity-40"
                aria-label={t('next')}
              >
                ›
              </button>
            </div>
          )}
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
          onClick={() => { void loadPeek(1) }}
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
        {canLayoutView && (
          <button
            onClick={() => setDocOpen(true)}
            className="rounded-md border border-accent px-2.5 py-1 text-[11px] text-accent hover:bg-card"
            title="按原版式渲染这份文档（不打开 Office）"
          >
            预览版面
          </button>
        )}
        <button onClick={onReveal} className="rounded-md border border-line px-2.5 py-1 text-[11px] hover:bg-card">
          {t('dtl_reveal')}
        </button>
        <button onClick={onCopy} className="rounded-md border border-line px-2.5 py-1 text-[11px] hover:bg-card">
          {t('ctx_copy')}
        </button>
      </div>
      <div className="mt-3 text-[10px] leading-4 text-sub">{t('dtl_hint')}</div>
      {docOpen && (
        <DocViewModal
          path={row.p}
          name={name}
          ext={String(row.e || row.p.split('.').pop() || '')}
          onClose={() => setDocOpen(false)}
        />
      )}
    </aside>
  )
}

export function Checkmark() {
  return <IconCheck size={13} />
}

/* ------------------------- 第 380 项：开源组件清单 ------------------------- */

type OssComp = { name: string; version: string; license: string; use: string; url: string }
type OssModel = { name: string; mb: number; sha256: string }

export function LicensesModal({ onClose }: { onClose: () => void }) {
  const [state, setState] = useState<{ loading: boolean; err?: string; comps: OssComp[]; models: OssModel[]; dir: string }>({
    loading: true,
    comps: [],
    models: [],
    dir: '',
  })
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let alive = true
    fetch('/api/oss/licenses')
      .then((r) => r.json())
      .then((d) => {
        if (!alive) return
        setState({
          loading: false,
          err: d?.ok ? undefined : t('oss_fail'),
          comps: Array.isArray(d?.components) ? d.components : [],
          models: Array.isArray(d?.models) ? d.models : [],
          dir: String(d?.dir || ''),
        })
      })
      .catch(() => alive && setState({ loading: false, err: t('oss_fail'), comps: [], models: [], dir: '' }))
    return () => {
      alive = false
    }
  }, [])

  const copyDir = async () => {
    try {
      await copyText(state.dir)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }

  return (
    <Modal title={t('oss_title')} onClose={onClose} width={720}>
      {state.loading && <div className="py-6 text-center text-[12px] text-sub">{t('clip_loading')}</div>}
      {!state.loading && state.err && <div className="py-4 text-[12px] text-sub">{state.err}</div>}
      {!state.loading && !state.err && (
        <>
          <div className="mb-2 text-[11px] leading-5 text-sub">{t('oss_intro')}</div>
          <div className="overflow-hidden rounded-lg border border-line">
            <div className="grid grid-cols-[1.05fr_0.6fr_0.9fr_1.5fr] gap-2 border-b border-line bg-card px-2.5 py-1.5 text-[11px] font-medium text-sub">
              <span>{t('oss_col_comp')}</span>
              <span>{t('oss_col_ver')}</span>
              <span>{t('oss_col_lic')}</span>
              <span>{t('oss_col_use')}</span>
            </div>
            <div className="max-h-[46vh] overflow-auto">
              {state.comps.map((c) => (
                <div
                  key={`${c.name}-${c.version}`}
                  className="grid grid-cols-[1.05fr_0.6fr_0.9fr_1.5fr] gap-2 border-b border-line/60 px-2.5 py-1.5 text-[11px] last:border-b-0"
                >
                  <span className="truncate" title={`${c.name} ${c.url}`}>
                    {c.url ? (
                      <a className="hover:text-accent hover:underline" href={c.url} onClick={(e) => { e.preventDefault(); openUrl(c.url) }}>
                        {c.name}
                      </a>
                    ) : (
                      c.name
                    )}
                  </span>
                  <span className="truncate whitespace-nowrap font-mono text-[10px] text-sub">{c.version}</span>
                  <span className="truncate" title={c.license}>{c.license}</span>
                  <span className="text-sub">{c.use}</span>
                </div>
              ))}
            </div>
          </div>

          {state.models.length > 0 && (
            <div className="mt-3">
              <div className="mb-1 text-[11px] font-medium">{t('oss_models')}</div>
              <div className="text-[10px] leading-4 text-sub">{t('oss_models_hint')}</div>
              <div className="mt-1 space-y-1">
                {state.models.map((m) => (
                  <div key={m.name} className="flex flex-wrap items-center gap-2 font-mono text-[10px] text-sub">
                    <span className="text-ink">{m.name}</span>
                    <span>{m.mb} MB</span>
                    <span>sha256:{m.sha256}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-3 border-t border-line pt-2">
            <div className="text-[11px] text-sub">{t('oss_dir')}</div>
            {state.dir ? (
              <div className="mt-1 flex items-center gap-2">
                <span className="min-w-0 flex-1 break-all rounded-md bg-card px-2 py-1 font-mono text-[10px] text-sub">{state.dir}</span>
                <button onClick={copyDir} className="shrink-0 rounded-md border border-line px-2 py-1 text-[11px] hover:bg-card">
                  {copied ? t('hc_copied') : t('oss_copy')}
                </button>
              </div>
            ) : (
              <div className="mt-1 text-[10px] text-sub">{t('oss_dir_dev')}</div>
            )}
          </div>
        </>
      )}
    </Modal>
  )
}