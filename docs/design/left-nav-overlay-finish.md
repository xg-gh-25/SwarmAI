---
title: Left Nav 与 overlay — 收尾四项（加载状态统一、ToDo 拆出首包、首包检查接入构建、真机计时）
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: DONE — A–D 全部落地（2026-10-23），结果见 §7
related: left-nav-overlay-single-source.md（主设计，已 DONE；本文是它 §11、§12 列出的未做项）
---

# Left Nav 与 overlay — 收尾四项

## 0. 范围

主设计 `left-nav-overlay-single-source.md` 完成后剩四项：

| # | 项 | 主设计出处 |
|---|---|---|
| A | 加载 / 空 / 出错三态统一成一个组件（OverlayState） | §S3、§11 |
| B | ToDo 的代码拆出首包 | §12 |
| C | 首包大小检查接入构建 | §12 |
| D | 真机计时 | §5.5、§12 |

另外补一项相关的：Need You overlay 还在用 `useEffect` 自己取数（它不是 nav 项，从告警药丸打开，所以
主设计的契约测试没覆盖它的取数），一并改到 `overlayQueries`（A 的一部分）。

不变的：nav 的分组、层级、顺序、样式（主设计 §3）；各 overlay 的内容和交互。

## 1. A — 加载状态统一

### 1.1 现状（2026-10-23 读代码）

overlay **首次没有数据时**的三种状态，各写各的：

| overlay | 加载中 | 出错 | 空 |
|---|---|---|---|
| ToDo | 工具栏 "Loading…" | 红框 + Retry（`todo-load-error`） | 文案（`todo-empty-hint`） |
| Pipeline | 工具栏 | 红色虚线框 + 文案 + Retry（`pipeline-load-error`） | 文案 |
| Pollinate | 工具栏 | 同 Pipeline（`pollinate-load-error`） | 文案 |
| Community | 居中 "Loading…" | 红字 "Couldn't load. Retry shortly."，**无按钮** | 居中文案 |
| Need You | 居中 "Loading…" | 红字，**无按钮** | 图标 + 文案 |
| Jobs | 工具栏 | 无（失败时列表为空，显示"No jobs yet"） | 文案（`jobs-empty`） |
| Capabilities | 工具栏 | 灰字（`cap-error`），无按钮 | 文案（`cap-empty`） |
| Hive | 工具栏 | 无 | 文案（`hive-accounts-empty`） |
| C&M | — | 红色虚线框 + Retry（`cm-needs-error`） | — |
| Library | — | `FetchError` 组件 + Retry | 文案 |
| Artifacts | 骨架 | 图标 + 文案，**无按钮**（`artifacts-error`） | 图标 + 文案 |
| Brain Hub | "Loading brains…" | 文案 + Retry（`brainhub-error`） | — |

问题：有的出错没有重试按钮；Jobs 和 Hive 失败时看起来像"什么都没有"（主设计 B 系列修过的同类问题）；
样式五六种。

### 1.2 方案

`desktop/src/components/layout/OverlayState.tsx`，三个组件，只管**首次没有数据**这一刻：

- `OverlayLoading({ label? })`：居中一行 "Loading…"（或 `label`）。
- `OverlayError({ message, onRetry, testid, retryTestid })`：Pipeline/Pollinate 现有的样式（红色虚线框 +
  文案 + Retry 按钮）——这是做得最完整的一种，`classifyLoadError` 给出文案。
- `OverlayEmpty({ icon?, children, testid })`：居中、灰色、可带图标；文案仍是各 overlay 自己的。

规则：
- 已有数据时刷新，不显示这三个（工具栏的小 "Loading…" 照旧）。
- 出错一定有 Retry，Retry 调该 overlay 查询的 `refetch`。
- **testid 原样保留**（测试锁着它们），文案原样保留（它说的是"这里是什么"），只统一外框和按钮。
- 不在范围：抽屉、详情、单项操作里的加载和报错（如 Pipeline 详情抽屉、ToDo 操作失败条），它们是局部的。

逐个 overlay 改，表里每一格对应一处替换。Jobs、Hive 补上出错态（现在失败看起来像空）。

### 1.3 Need You 改到 overlayQueries

`overlayQuery.needsYou()`（`attentionService.fetchAttention`，null 视为出错），overlay 用 `useQuery`，
加进 `NAV_PREFETCH['needs-you']`（告警药丸悬停时预取，药丸在 `ChatPage`）。`useAttentionQueue`（药丸自己
的轮询）不动：它有自己的轮询节奏，合并是另一件事。

### 1.4 验收

- `OverlayState.test.tsx`：三个组件的结构、Retry 调回调、testid 透传。
- 每个改到的 overlay：原有测试照过；新增"首次加载失败 → 有 Retry → 点 Retry 再请求一次"（Community、
  Need You、Artifacts、Jobs、Hive、Capabilities 这几个原来没有按钮的）。
- 反向验证：去掉某个 overlay 的 Retry，对应测试变红。

## 2. B — ToDo 拆出首包

现状：`ChatPage.tsx` 静态 `import { parseWorkPacket } from '../components/layout/ToDoOverlay'`，整个
ToDoOverlay（1251 行）因此留在首包，vite 构建有提示。

方案：`parseWorkPacket` 和 `WorkPacket` 类型移到 `components/layout/todoWorkPacket.ts`（纯函数，无 React），
ChatPage 和 ToDoOverlay 都从这里引；ToDoOverlay 保留 re-export，旧的测试 import 不用改。

验收：`vite build` 后 ToDoOverlay 是独立 chunk，构建输出不再有那条提示；首包变小（记数字）；
`parseWorkPacket.test.ts` 照过。

## 3. C — 首包检查接入构建

方案：`package.json` 的 `build` 改为 `tsc -b && vite build && node scripts/check-bundle-size.mjs`。
`build:all` 经 tauri `beforeBuildCommand` → `scripts/pre-build.sh` → `npm run build`，所以生产构建也会检查。
上限在 B 做完后按实测值 +5% 重设。

超了怎么办：构建失败并打印"首包 X kB 超过上限 Y kB"。真需要变大时，在同一个改动里调高上限并写明原因
（脚本注释里写了）。

验收：故意把上限调到当前值以下，`npm run build` 失败；恢复后通过。不跑 `./prod.sh`（XG 的流程）。

## 4. D — 真机计时

不能由我在本机代跑：浏览器工具进不了 Tauri webview；另起一个 dev 前端连正在运行的 daemon 也不行——前端
启动时会恢复和保存标签页（`open_tabs.json`），会覆盖 XG 正在用的标签页状态。

方案：把计时做成 XG 点一遍就能拿到一张表。
- OverlayHost 现在已在 dev 构建里记"打开 → 内容挂载"；再加"打开 → 首次有数据"（OverlayState 是首次
  无数据时唯一的渲染点，它消失的那一刻就是数据到了）。
- 结果存进 `window.__overlayTimings`（每个 id 最近一次：代码加载 ms、数据到达 ms、是否命中缓存），
  `console.table(window.__overlayTimings)` 一行出表。只在 dev 构建（`import.meta.env.DEV`，测试除外）。
- 设计文档写清操作步骤：`./dev.sh` → 每个 nav 项悬停 1 秒再点开、关掉 → 再点一次 → 控制台
  `console.table(window.__overlayTimings)` → 贴回来。

验收：单元测试驱动 OverlayHost + 一个假 overlay，断言表里记下了这个 id 的两项耗时；生产/测试模式不记。

## 5. 实施顺序

| 步 | 内容 | 依赖 |
|---|---|---|
| 1 | B：拆 `todoWorkPacket.ts`，量首包 | 无 |
| 2 | C：`build` 接入检查，按 B 后的实测值定上限 | 1 |
| 3 | A：`OverlayState.tsx` + 测试；Need You 改 useQuery | 无 |
| 4 | A：逐个 overlay 替换三态，每次一个提交或两三个一组 | 3 |
| 5 | D：数据到达计时 + `window.__overlayTimings` + 测试 + 操作步骤 | 3 |
| 6 | 主设计和 navcard 标准文档补上 OverlayState 和计时的说明 | 全部 |

每步：自己的 worktree、先测试、反向验证、受影响的测试（layout 目录、契约测试、两份 nav 顺序测试、
overlayHostE2E、改到的 overlay 的测试）、tsc、eslint、ff-only 合入、不 push、不跑 `./prod.sh`。

## 6. 会改变外观的地方（XG 已授权"全做"，列出来便于验收）

- Community、Need You、Artifacts、Capabilities 的出错态：从一行红字/灰字变成红色虚线框 + Retry。
- Jobs、Hive：失败时从"空列表"变成出错框 + Retry。
- Community、Need You 的加载中：样式统一（位置不变）。
- 各空状态的文案和图标不变，只统一居中和字号。

## 7. 结果（2026-10-23）

| 项 | 提交 | 结果 |
|---|---|---|
| B | `9142a01a` | `todoWorkPacket.ts`；ToDoOverlay 成为独立 chunk（33 kB），首包 3,771,284 → 3,737,362 B；新增静态测试：overlay 之外的模块不得静态引用 overlay 文件 |
| C | `db59328d` | `build` = `tsc -b && vite build && node scripts/check-bundle-size.mjs`；上限 3,832 kB（3,650 kB +5%）；上限调低时构建失败并说明怎么改 |
| A | `4ddb6fa5` | `OverlayState.tsx`；Need You 改 `overlayQuery.needsYou` + AlertsPill 悬停预取 |
| A | `670a494d` | Community、Artifacts、Capabilities 出错有 Retry |
| A | `f623516c` | Jobs、Hive 失败时显示出错 + Retry，不再像空列表 |
| A | `281bd525` | 其余首次加载出错框全部换成 `OverlayError`（ToDo、Pipeline、Pollinate、Library、C&M、Brain Hub）；新增静态测试：OverlayState 之外不得手写出错框 |
| D | `9d502862` | `overlayTiming.ts`，`window.__overlayTimings` |

和设计不同的地方：
- Artifacts 的加载骨架和带 emoji 的空状态保留（骨架比一行字更贴合列表；空状态的图标是它自己的）。
- Library 的出错框原来贴边，现在和其他 overlay 一样居中、最宽 `max-w-sm`。
- C&M 的检索出错原来没有 Retry，补上了。
- 计时的"数据到达"按 overlay 的 `NAV_PREFETCH` 查询何时全部有数据算（读 TanStack Query 缓存），
  没有用界面上加载态的消失：有几个 overlay 首次加载时界面上不显示加载态，按界面算会不准。

### 真机计时：XG 的操作步骤

1. `./dev.sh`，等窗口出来。
2. 打开 DevTools（Cmd+Option+I）→ Console。
3. 对每个 nav 项（和 Need You）：先**不悬停直接点开**，等内容出来，关掉；再**悬停 1 秒后点开**，关掉。
   每个 id 只保留最近一次，所以想看冷打开就看第一轮后的表，想看预取效果就看第二轮后的表。
4. 控制台执行 `console.table(window.__overlayTimings)`，把表贴回来。

列的意思：`code_ms` 打开 → 内容代码加载完并挂载；`data_ms` 打开 → 首屏数据全部到达（缓存命中为 0）；
`cache` hit / miss / n/a（n/a = 该 overlay 没有预取项，如 Settings、Workspace）。
