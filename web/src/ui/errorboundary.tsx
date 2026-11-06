import { Component, type ErrorInfo, type ReactNode } from 'react'
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
type State = { err: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null }

  static getDerivedStateFromError(err: Error): State {
    return { err }
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    // 打包后没有 devtools，至少让 pywebview 的控制台里留一条能对上的线索
    console.error('[sb] render error', err, info.componentStack)
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
          <button
            onClick={() => location.reload()}
            className="mt-4 rounded-lg bg-accent px-3 py-1.5 text-[12px] text-white"
          >
            {t('err_reload')}
          </button>
        </div>
      </div>
    )
  }
}
