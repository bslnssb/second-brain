# web — 新的前端（React + TypeScript + Tailwind）

这是「第二大脑」的新界面，和原来的 Python 单文件界面**并存**：

- 旧界面：`http://127.0.0.1:8765/`（`brain.py` 里的 `PAGE_TPL`，功能齐全，默认使用）
- 新界面：`http://127.0.0.1:8765/web/`（本目录构建产物，实验阶段；旧界面页脚有「新界面」入口）

后端一点没动：还是 `brain.py` 起的 HTTP 服务，`/api`、`/api/files`、`/api/stats`、`/open` 全都复用。

## 开发

```bash
cd web
pnpm install              # 装不上就加 --registry=https://registry.npmmirror.com
pnpm dev                  # 5173，带 HMR；/api 自动代理到 8765
```

`pnpm dev` 之前先让后端跑起来（`python -B app_window.py`，或直接开 `SecondBrain.exe`）。

## 构建与打包

```bash
pnpm build                # 产出 web/dist
pnpm test                 # vitest：语法解析 / 排序 / 省略 / 时间格式
pnpm typecheck            # tsc --noEmit
```

`release/github/second-brain-app.spec` 会把 `web/dist` 打进 `_internal/web`，
运行期由 `brain.web_ui_dir()` 依次找：`_internal/web` → `<数据目录>/web/dist` → `<项目>/web/dist`。
**顺序很重要**：开发时 `<项目>/web/index.html` 是 Vite 源码（引 `/src/main.tsx`），
要是被当成成品发出去，页面就是一片白。

## 结构

```
src/
  lib/qsyn.ts      搜索语法解析（ext: size: dm: -排除 文件夹/ content:）
  lib/hits.ts      排序、中间省略（按半角宽度算）、大小/时间格式化
  lib/*.test.ts    vitest 单测（32 条）
  api.ts           后端接口 + pywebview 原生桥（open_path / reveal）
  ui/icons.tsx     全部图标是内联 SVG，颜色跟 currentColor，深色自动适配
  ui/panels.tsx    时间轴 / 设置抽屉 / 帮助 / 关于 / 详情面板
  App.tsx          顶栏 + 侧栏筛选 + 列表 + 页脚
  index.css        CSS 变量（浅色/深色两套）
```

## 已经搬过来的面板（2026-11-06）

| 面板 | 组件 | 数据来源 |
|---|---|---|
| 首页时间轴 | `Timeline` | `GET /api/timeline?days=14` |
| 设置抽屉 | `SettingsDrawer` | `GET/POST /api/settings`、`/api/autostart` |
| 使用帮助 | `HelpModal` | 纯静态文案（和旧界面同步维护） |
| 关于 | `AboutModal` | `GET /api/ping` + `/api/stats` |
| 详情面板 | `Preview` | 当前选中行 + 原生桥打开/定位 |

`reveal`（在资源管理器里定位）只有 pywebview 原生桥，浏览器里点「定位」不会动，这是正常的。

## 还没搬

右键菜单、拖入反查、缩略图视图、公告、维护工具（扫描/更新/体检仍在旧界面）。

## 三个坑（别重踩）

1. **`base: './'` 写在 `defineConfig` 顶层**。写进 `build` 里会被 Vite 静默忽略，产物就用
   `/assets/x.js` 绝对路径；页面在 `/web/` 下 → 请求打到后端 404 页 → **白屏但 `<title>` 正常**。
2. **后端静态目录 dist 优先**（`brain.py` 的 `web_ui_dir()`）。`web/index.html` 是 Vite 源码
   （引 `/src/main.tsx`），先命中它会得到「HTML 对了、JS 没跑」的白屏。
3. **截图别用 `PrintWindow` 看深色抽屉**：WebView2 的分层窗口会把 `position:absolute` 的抽屉
   渲染成白底，但 `getComputedStyle` 其实是 `rgb(24,27,33)`，数据没错。

## 现在首页就是它

`http://127.0.0.1:8765/` 已经 302 到 `/web/`（2026-11-06 起）。旧界面还在，三种入口：

- 页脚点「旧界面」→ `/?ui=legacy`
- 托盘「设置 / 帮助 / 关于」走 `/?panel=...`，仍然打开旧界面的对应抽屉
- 带查询串的老链接 `/?q=xxx` 也仍然走旧模板

## 还没搬的

缩略图视图、扫描/更新/体检三个维护按钮（设置抽屉里给了「去旧界面」链接）、`reveal` 的 HTTP 兜底
（目前只有 pywebview 原生桥，浏览器里点「定位」没反应）。
