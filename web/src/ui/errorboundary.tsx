import { Component, type ErrorInfo, type ReactNode } from 'react'
import { errOp } from '../api'
import { t } from '../lib/i18n'

/**
 * 渲染兜底。
 *
 * 为什么需要：React 18 里，任何一次 render 抛异常都会把整棵树卸载掉 —— 桌面版看到的
 * 就是一片纯白窗口，没有地址栏也没有刷新按钮，用户只能去任务管理器结束进程。
 * 之前的键盘 bug（Enter 取越界行）是事件里抛的，React 不接管；但只要哪天错误从
 * render 里冒出来，这个缺口就会直接暴露。这里给一条「重载」的自救路径。
 */
type Props = { children: ReactNode }
type State = { err: Error | null; reported: boolean }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null, reported: false }

  static getDerivedStateFromError(err: Error): State {
    return { err, reported: false }
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    // 打包后没有 devtools，至少让 pywebview 的控制台里留一条能对上的线索
    console.error('[sb] render error', err, info.componentStack)
  }

  // 第 162 项：界面崩了，设置页里的「报告我看到的问题」根本进不去。
  // 这里直接把错误写进本机报告（后端 op=feedback），用户一键即可留下证据。
  async report() {
    const e = this.state.err
    if (!e) return
    const text = ['界面渲染崩溃', String(e.message || e), String(e.stack || '')]
      .filter(Boolean).join('\n').slice(0, 1800)
    try {
      await errOp('feedback', { text })
      this.setState({ reported: true })
    } catch {
      this.setState({ reported: false })
    }
  }

  render() {
    if (!this.state.err) return this.props.children
    return (
      <div className="grid min-h-screen place-items-center bg-bg p-8 text-center text-ink">
        <div className="max-w-[460px]">
          <div className="text-[15px] font-medium">{t('err_title')}</div>
          <div className="mt-2 text-[12px] leading-relaxed text-sub">{t('err_body')}</div>
          <pre className="mt-3 max-h-[120px] overflow-auto rounded-lg border border-line bg-card p-2 text-left text-[11px] whitespace-pre-wrap text-sub">
            {String(this.state.err.message || this.state.err)}
          </pre>
          <div className="mt-4 flex justify-center gap-2">
            <button
              onClick={() => location.reload()}
              className="rounded-lg bg-accent px-3 py-1.5 text-[12px] text-white"
            >
              {t('err_reload')}
            </button>
            <button
              onClick={() => { void this.report() }}
              className="rounded-lg border border-line px-3 py-1.5 text-[12px]"
            >
              {this.state.reported ? t('eb_reported') : t('eb_report')}
            </button>
          </div>
        </div>
      </div>
    )
  }
}
