import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发时 `pnpm dev` 起在 5173，/api 全部转发给本地 Python 后端（8765），
// 这样改一行 JSX 立刻热更新，不用重启 SecondBrain.exe。
const BACKEND = process.env.SB_BACKEND || 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [react()],
  // 关键：base 是顶层选项，写进 build 里会被忽略，产物就会用绝对路径 /assets/…，
  // 而页面挂在 /web/ 下，请求 /assets/x.js 会打到后端 404 页 → 白屏。
  base: './',
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: false },
      '/open': { target: BACKEND, changeOrigin: false },
      '/reveal': { target: BACKEND, changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
