import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './ui/errorboundary'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* 包在最外层：任何一次 render 抛错都不该变成一片白窗口 */}
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
