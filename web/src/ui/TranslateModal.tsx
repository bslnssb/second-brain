import { useEffect, useRef, useState } from 'react'
import { t } from '../lib/i18n'
import { IconTranslate } from './icons'

interface TranslationResult {
  engine: string
  /** 第 278 项：语言无关的引擎 id，界面按它翻成当前界面语言 */
  engine_id?: string
  text: string
  extra: string
  /** 第 278 项：固定徽标（「词条解析」「官方 API」这类）的语言无关 code */
  extra_code?: string
}

interface TranslateModalProps {
  isOpen: boolean
  onClose: () => void
  onToast: (msg: string) => void
  initialQuery?: string
}

/** 第 278 项：语言名一律用母语写法（中英西葡印界面都一样），不再缀中文注释 */
const MODAL_LANGUAGES = [
  { code: 'auto', label: '' },
  { code: 'zh-CN', label: '简体中文' },
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

/** 第 278 项：引擎标签按界面语言显示（te_<id>），没有 id 才退回后端原标签。 */
function engineName(r: TranslationResult): string {
  const id = r.engine_id || ''
  return id ? t(`te_${id}`) : r.engine
}

/** 第 278 项：右上角小徽标也按界面语言显示；模型名这类非固定文案原样透传。 */
function extraName(r: TranslationResult): string {
  return r.extra_code ? t(`tx_${r.extra_code}`) : (r.extra || '')
}

export function TranslateModal({ isOpen, onClose, onToast, initialQuery = '' }: TranslateModalProps) {
  const [text, setText] = useState('')
  const [targetLang, setTargetLang] = useState('auto')
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<TranslationResult[]>([])
  const [hasSearched, setHasSearched] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  /** 第 346 项：朗读。**只给前两条结果**生成语音**（用户要求：一次要发一堆语音会慢，
   *  前两个有、后面的不做）。点喇叭时若还没合成好，就现取。 */
  const TTS_MAX = 2
  const audioCache = useRef<Map<string, string>>(new Map())
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [speaking, setSpeaking] = useState<number | null>(null)
  const [ttsBusy, setTtsBusy] = useState<number | null>(null)

  const stopAudio = () => {
    const a = audioRef.current
    if (a) {
      a.pause()
      audioRef.current = null
    }
    setSpeaking(null)
  }

  /** 取（或合成）一条语音，返回对象 URL；合成失败返回 null。 */
  const ensureAudio = async (txt: string, lang: string): Promise<string | null> => {
    const key = `${lang}|${txt}`
    const hit = audioCache.current.get(key)
    if (hit) return hit
    try {
      const r = await fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
        body: JSON.stringify({ text: txt, lang }),
      })
      const ct = (r.headers.get('Content-Type') || '').toLowerCase()
      if (!ct.startsWith('audio/')) return null
      const blob = await r.blob()
      if (!blob.size) return null
      const url = URL.createObjectURL(blob)
      audioCache.current.set(key, url)
      return url
    } catch {
      return null
    }
  }

  const speak = async (i: number, txt: string) => {
    stopAudio()
    setTtsBusy(i)
    // 'auto' 交给后端按系统默认音色读（多半就是界面语言那个）
    const lang = targetLang === 'auto' ? '' : targetLang
    const url = await ensureAudio(txt, lang)
    setTtsBusy(null)
    if (!url) {
      onToast(t('tm_tts_fail'))
      return
    }
    try {
      const a = new Audio(url)
      audioRef.current = a
      setSpeaking(i)
      a.onended = () => setSpeaking((cur) => (cur === i ? null : cur))
      await a.play()
    } catch {
      onToast(t('tm_tts_fail'))
      setSpeaking(null)
    }
  }

  // 关窗/换结果时停掉上一段并释放 objectURL
  useEffect(() => {
    if (!isOpen) {
      stopAudio()
      const m = audioCache.current
      audioCache.current = new Map()
      m.forEach((u) => URL.revokeObjectURL(u))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen])

  useEffect(() => {
    if (isOpen) {
      fetch('/api/settings').then((r) => r.json()).then((s) => {
        if (s && s.trans_target_lang) setTargetLang(s.trans_target_lang)
      }).catch(() => {})
      const q = initialQuery.trim()
      setText(q)
      setResults([])
      setHasSearched(false)
      setTimeout(() => textareaRef.current?.focus(), 80)
      if (q) {
        doTranslate(q, targetLang)
      }
    }
  }, [isOpen, initialQuery])

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [isOpen, onClose])

  const doTranslate = async (q = text, tl = targetLang) => {
    const s = q.trim()
    if (!s) return
    try {
      setLoading(true)
      setHasSearched(true)
      const res = await fetch('/api/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' },
        body: JSON.stringify({ text: s, to: tl })
      })
      const data = await res.json()
      if (data && data.ok) {
        const list: TranslationResult[] = data.results || []
        setResults(list)
        // 第 346 项：只预热前两条（用户明确要求：一次合成一大堆语音会慢，后面的不做）
        const lang = tl === 'auto' ? '' : tl
        list.slice(0, TTS_MAX).forEach((r) => {
          if (r?.text) void ensureAudio(r.text, lang)
        })
      } else {
        setResults([])
      }
    } catch {
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  /* 第 305 项：用户「聚合翻译的结果无法复制」。
     原来只用 navigator.clipboard.writeText：桌面包里（WebView2）这条 API 经常因为
     窗口焦点/权限直接 reject，而且没 await、没 catch —— 创口贴式地先弹「已复制」，
     实际剪贴板没变。现在按可靠性递减三级回退：
     ① 服务端 /api/snip/copy_text（Win32 剪贴板，跟截图识字那条路同一套，最稳）；
     ② 浏览器 Clipboard API；③ 老式 execCommand('copy')。三级都失败才提示失败。 */
  const handleCopy = async (tText: string) => {
    if (!tText) return
    const headers = { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' }
    try {
      const r = await fetch('/api/snip/copy_text', {
        method: 'POST',
        headers,
        body: JSON.stringify({ text: tText }),
      })
      const d = (await r.json().catch(() => null)) as { ok?: boolean } | null
      if (d?.ok) {
        onToast(t('trans_copied'))
        return
      }
    } catch {
      /* 服务端不在（比如纯浏览器预览）就往下走 */
    }
    try {
      await navigator.clipboard.writeText(tText)
      onToast(t('trans_copied'))
      return
    } catch {
      /* 继续下一级 */
    }
    try {
      const ta = document.createElement('textarea')
      ta.value = tText
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      onToast(ok ? t('trans_copied') : t('trans_copy_fail'))
    } catch {
      onToast(t('trans_copy_fail'))
    }
  }

  const handleLangChange = (newLang: string) => {
    setTargetLang(newLang)
    if (text.trim()) {
      doTranslate(text, newLang)
    }
  }

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 backdrop-blur-[2px] p-4 animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="flex flex-col w-full max-w-[580px] max-h-[82vh] rounded-xl border border-line bg-card shadow-2xl overflow-hidden text-ink"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line px-4 py-3 bg-bg/40">
          <div className="flex items-center gap-2">
            <IconTranslate size={18} className="text-accent" />
            <span className="font-semibold text-[14px]">{t('trans_title')}</span>
            <select
              value={targetLang}
              onChange={(e) => handleLangChange(e.target.value)}
              className="text-[11px] text-ink bg-line/40 hover:bg-line/60 px-2 py-0.5 rounded border border-line/60 outline-none cursor-pointer transition-colors"
            >
              {MODAL_LANGUAGES.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.code === 'auto' ? t('trans_lang_auto') : opt.label}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="text-sub hover:text-ink h-7 w-7 grid place-items-center rounded-lg hover:bg-line/50 transition-colors text-lg leading-none"
            title={t('tm_close')}
          >
            ×
          </button>
        </div>

        {/* Input Area */}
        <div className="p-3 border-b border-line bg-card flex flex-col gap-2">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                doTranslate()
              }
            }}
            placeholder={t('tm_placeholder')}
            rows={3}
            className="w-full resize-none rounded-lg border border-line bg-bg/50 p-2.5 text-[13.5px] leading-relaxed outline-none focus:border-accent focus:bg-card text-ink placeholder:text-sub transition-colors font-sans"
          />

          <div className="flex items-center justify-between">
            <span className="text-[11px] text-sub">{t('tm_hint')}</span>
            <div className="flex items-center gap-2">
              {text && (
                <button
                  type="button"
                  onClick={() => {
                    setText('')
                    setResults([])
                    setHasSearched(false)
                  }}
                  className="px-2 py-1 text-[12px] text-sub hover:text-ink rounded transition-colors"
                >
                  {t('tm_clear')}
                </button>
              )}
              <button
                type="button"
                onClick={() => doTranslate()}
                disabled={loading || !text.trim()}
                className="px-4 py-1.5 text-[12.5px] font-medium bg-accent text-white rounded-md hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1.5 cursor-pointer"
              >
                {loading && <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" />}
                <span>{t('tm_go')}</span>
              </button>
            </div>
          </div>
        </div>

        {/* Results List */}
        <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2.5 min-h-[180px] bg-bg/25">
          {loading ? (
            <div className="py-12 text-center text-sub text-[13px] flex flex-col items-center gap-2">
              <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-sub/30 border-t-accent" />
              <span>{t('tm_loading')}</span>
            </div>
          ) : results.length > 0 ? (
            results.map((res, i) => (
              <div
                key={i}
                onClick={() => void handleCopy(res.text)}
                className="group p-3 rounded-lg border border-line bg-card shadow-sm hover:border-accent hover:shadow cursor-pointer transition-all relative"
                title={t('tm_copy_tip')}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-semibold text-accent">{engineName(res)}</span>
                    {res.extra && (
                      <span className="text-[11px] text-sub bg-line/40 px-1.5 py-0.2 rounded font-normal">
                        {extraName(res)}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    {i < TTS_MAX && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          if (speaking === i) stopAudio()
                          else void speak(i, res.text)
                        }}
                        title={t('tm_tts_tip')}
                        aria-label={t('tm_tts')}
                        className={`shrink-0 h-6 w-6 grid place-items-center rounded-full border text-[11px] transition-colors ${
                          speaking === i
                            ? 'border-accent text-accent bg-accent/10'
                            : 'border-line/70 text-sub hover:border-accent hover:text-accent'
                        }`}
                      >
                        {ttsBusy === i ? (
                          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-sub/30 border-t-accent" />
                        ) : (
                          <span aria-hidden>{speaking === i ? '■' : '▶'}</span>
                        )}
                      </button>
                    )}
                    <span className="shrink-0 rounded border border-line/70 px-1.5 py-0.5 text-[11px] text-sub group-hover:border-accent group-hover:text-accent transition-colors">
                      {t('tm_copy_hint')}
                    </span>
                  </div>
                </div>

                <div className="text-[13.5px] leading-relaxed text-ink select-text font-sans">
                  {res.text}
                </div>
              </div>
            ))
          ) : hasSearched ? (
            <div className="py-12 text-center text-sub text-[13px]">
              {t('tm_empty_fail')}
            </div>
          ) : (
            <div className="py-12 text-center text-sub text-[13px]">
              {t('tm_empty_start')}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
