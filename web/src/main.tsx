import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './ui/errorboundary'
import { installErrorHooks } from './lib/errhook'
import './index.css'

// 第 277 项：必须在 render 之前装 —— React 初始化阶段的异常也要能报出去。
installErrorHooks()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* 包在最外层：任何一次 render 抛错都不该变成一片白窗口 */}
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
