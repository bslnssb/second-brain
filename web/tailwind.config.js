export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // 深色走 <html data-theme="dark">，和 Python 版保持同一套开关，迁移期两边一致
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        ink: 'rgb(var(--sb-ink) / <alpha-value>)',
        sub: 'rgb(var(--sb-sub) / <alpha-value>)',
        line: 'rgb(var(--sb-line) / <alpha-value>)',
        card: 'rgb(var(--sb-card) / <alpha-value>)',
        bg: 'rgb(var(--sb-bg) / <alpha-value>)',
        accent: 'rgb(var(--sb-accent) / <alpha-value>)',
      },
      fontFamily: {
        ui: ['"Microsoft YaHei UI"', '"Segoe UI"', 'system-ui', 'sans-serif'],
        mono: ['"Cascadia Mono"', 'Consolas', 'monospace'],
      },
      keyframes: {
        pop: {
          '0%': { transform: 'translate(-50%, -50%) scale(.94)', opacity: '0' },
          '100%': { transform: 'translate(-50%, -50%) scale(1)', opacity: '1' },
        },
      },
      animation: { pop: 'pop .16s ease-out' },
    },
  },
  plugins: [],
}
