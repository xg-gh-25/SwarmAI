---
title: Workspace — one working location shared with Terminal (SwarmWS or the chat's working repo)
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: DRAFT — 待审
related: terminal-locations.md（Terminal 的位置模型，本文复用）、left-nav-backend-and-visual-system.md（nav 原则：不影响聊天、只读现成记录）
---

# Workspace：和 Terminal 共用一个"工作位置"

## 0. 决定与原则

Gawan 2026-10-23 定：

- **只显示两个位置**：默认的 SwarmWS，以及这个 chat 的工作仓库（和 Terminal 的 `repo` 是同一个）。不列 bindings 里的其他仓库。
- **选择共用**：一个 chat tab 只有一个"当前位置"，Terminal 和 Workspace 读写的是同一个值。

沿用 nav 的原则：

1. **绝不影响聊天。** 文件树的取数跑在 `nav` 线程池；git 调用一律带 `GIT_OPTIONAL_LOCKS=0`，不拿 index.lock；聊天路径上不新增任何东西。
2. **只读现成记录。** 工作仓库只有一个来源：`canvas_session.working_repo()`（读 run index，不跑 git）。
3. **前端不能传路径进来。** 后端只认 `swarmws`，以及这个 session 的工作仓库。

## 1. 现状（2026-10-23）

| | Terminal | Workspace overlay |
|---|---|---|
| 位置 | `swarmws` / `repo` / `custom`，存在 `stores/terminalLocation.ts`，每个 chat tab 记一份（localStorage `terminalLocationByChatTab`） | 只有 SwarmWS。根目录来自数据库里的 `workspace_config` |
| 工作仓库 | `GET /api/canvas/session/{id}/working-repo` -> `working_repo()` | 看不到 |
| 改动 | -- | "Working files"只列 `Knowledge/`、`Projects/` 下的 git 改动 |

问题：

- **P1 两边说的不是同一件事。** Terminal 在 swarmai 仓库里，Workspace 打开却是 SwarmWS。正在改的仓库里有哪些文件、哪些改了，在 Workspace 里看不到。
- **P2 文件树在后台一直刷新。** `ExplorerProvider` 挂在 `ThreeColumnLayout` 根部，app 一启动就取整棵 SwarmWS 树，然后每 30 秒轮询一次，每次都要做 `git status` 加文件系统扫描。Workspace overlay 关着也照样轮询。今天 16:05 起日志里每 30 秒一条 500，就是这个轮询在报错。
- **P3 加载失败时提示不够。** 现在只有一行 "Failed to load file tree" 加一个文字链接 Retry，没用统一的 `OverlayState`；而且看不出到底是哪个位置没加载出来。
- **P4 今天的 500**（`3d349113` 漏了 import，已由 `5b5c2b17` 修复）：以前的测试从来没有真正调用过文件树接口。

## 2. 模型

**位置**：`swarmws` 或 `repo`，另外 Terminal 还有 `custom`。

- **共用的选择**：沿用 `terminalLocation` 这个 store，不新建。Workspace 读它的 `snapshot()`，切换时调 `choose()`。这个 store 从 Terminal 专用升格为"chat tab 的工作位置"，模块路径和导出名不变，只更新注释和设计文档。
- **Workspace 里显示的位置**：
  - 当前位置是 `swarmws` 或 `repo`：照原样显示。
  - 当前位置是 `custom`（Terminal 里选的文件夹）：Workspace 显示 SwarmWS，选择本身不改。原因是 custom 文件夹不在后端白名单里，Workspace 不开放任意路径。
- **工作仓库**：沿用 `working_repo()`。chat 没有工作仓库时，Workspace 只显示 SwarmWS，切换器里不出现 repo 这一项。
- 在 Workspace 里切换位置会改 Terminal 的选择，反过来也一样。这正是"选择共用"的意思。

## 3. 后端

### 3.1 一个解析函数决定根目录

`routers/workspace_api.py` 新增 `_resolve_root(root: str, session_id: str | None) -> Path`：

- `root="swarmws"`（默认）：沿用现在的 `_get_workspace_path()`。
- `root="repo"`：调用 `working_repo(session_id, swarmws)`。拿到路径后，再确认它是真实目录，并且 `.git` 也是目录（main checkout）。条件不满足就返回 404 `no_working_repo`。
- 其他取值返回 422。请求里任何形式的路径参数都不能改变根目录。

`working_repo()` 本身已经保证不会是 run worktree，也不会取到更旧的 run。这里只是在请求时再核对一遍，因为 run.json 可能在两次请求之间变了。

### 3.2 接口：在现有接口上加参数，不新开接口

| 接口 | 改动 |
|---|---|
| `GET /workspace/tree` | 加 `root`、`session_id`；ETag 缓存的 key 带上解析后的根目录 |
| `GET /workspace/tree/expand` | 同上；`path` 解析后必须仍在根目录之内（现有的 `_is_path_under`） |
| `GET /workspace/file`（打开到 Canvas） | 加 `root=repo` 时，`path` 按仓库根目录解析，并做同样的包含检查。只读 |
| 写接口（PUT/POST/DELETE、trash、rename、folders） | **不加 `root`**。仓库里的改动走 pipeline run，不在文件树里改 |

仓库模式下的树：

- **跳过的目录**：`.git`、`node_modules`、`.venv`、`dist`、`build`、`__pycache__`，以及 `git status --ignored` 报告为忽略的顶层目录。
- **深度**：首屏深度 2，子目录一律懒加载。
- **Working files**：对仓库根目录跑现有的 `_get_git_status()`（已经带 `GIT_OPTIONAL_LOCKS=0`），列出所有改动的文件，不再只限 `Knowledge/`、`Projects/`。这和 Canvas Changes 是两件事：Changes 看的是 run worktree 里的改动和 run 合入的提交；这里看的是 main checkout 当前的工作区。

全部跑在 `nav` 池，git 调用都带 `GIT_OPTIONAL_LOCKS=0`，由 `test_nav_pool_and_git_locks.py` 的静态检查守住。

### 3.3 不变的

SwarmWS 模式下的行为一律不变：Knowledge、Projects 分区，系统文件夹保护，写操作，ETag。

## 4. 前端

### 4.1 头部：位置切换器

Workspace overlay 的工具栏左边放一个分段切换器：`SwarmWS | <repo 名>`，样式和 Terminal 头部的切换器一致，用的是视觉规范里的 `Segmented`（`components/overlay`）。

- chat 没有工作仓库时，切换器不显示，界面和现在一样。
- 现有的工具栏按钮（排序、折叠、展开、新建文件夹、刷新）在仓库模式下只保留折叠、展开和刷新。

### 4.2 取数：只在 overlay 打开时取

`ExplorerProvider` 改为只给 Workspace overlay（以及确实读它的 LibraryTree）提供数据：

- **首屏**：overlay 打开时，按 `(位置, session)` 取一次。
- **轮询**：只在 overlay 打开、而且窗口可见时才轮询 30 秒一次。关掉 overlay 就停。
- `ThreeColumnLayout` 里的 `RefreshTreeBridge` 换成 `invalidateOverlay(qc, 'swarmws')`，和其他 overlay 的刷新方式一样（`overlayQueries`）。
- **缓存**：取数改走 `overlayQueries`，key 是 `['overlay','swarmws',位置key,sessionId]`。30 秒内重新打开直接显示缓存；鼠标悬停 nav 时预取当前位置。

这一条解决 P2：overlay 关着的时候，不再每 30 秒做一次 `git status` 加文件系统扫描。

### 4.3 加载、空状态、错误

- **首次加载失败**：用 `OverlayState` 的 error 显示，带 Retry，文案写明是哪个位置，例如 "Couldn't load swarmai"。
- **仓库已经不存在**（404 `no_working_repo`）：显示一行提示 "This chat has no working repo now"，然后自动切回 SwarmWS 显示，但不改共用的选择。
- **轮询失败但已经有数据**：保留现在的树，只在工具栏显示一个小的错误标记。不清空树，也不弹窗。

### 4.4 仓库模式下的操作

| 操作 | 仓库模式 |
|---|---|
| 浏览、展开 | 有 |
| 双击打开到 Canvas | 有（走 3.2 的 `root=repo` 读取） |
| 附加到 chat | 有（绝对路径） |
| 在这里开 terminal | 有，并把共用的位置切到 `repo` |
| 新建、重命名、删除、拖动 | 没有：右键菜单里不出现，拖动不生效 |

## 5. 验收测试（先写，先红）

| # | 测试 | 证明什么 |
|---|---|---|
| T1 | 真实路由、临时目录：`root=swarmws` 的 tree 和 expand 返回 200，并包含磁盘上的目录（沿用 `test_workspace_tree_loads.py`） | 不会再有今天那种 500 |
| T2 | 用真实 run.json 让一个 session 拥有一个指向临时 git 仓库的 run：`root=repo&session_id=` 返回这个仓库的树；Working files 等于这个仓库的 `git status`；`.git`、`node_modules` 不出现 | 仓库根目录只从 run 推出来 |
| T3 | session 没有 run，或者 run 指向的是 linked worktree：`root=repo` 返回 404 `no_working_repo` | 不猜 |
| T4 | `root=repo` 时 `path=../../etc`、绝对路径、软链接逃逸，全部被拒；写接口传 `root` 返回 422 | 前端不能传路径进来；仓库只读 |
| T5 | 静态检查：文件树相关接口不用默认线程池；git 调用都带 `GIT_OPTIONAL_LOCKS=0`（扩展现有测试） | 不影响聊天 |
| T6 | 前端：Terminal 切到 repo 后，打开 Workspace 就是 repo；Workspace 切回 SwarmWS 后，Terminal 也是 SwarmWS；当前位置是 custom 时，Workspace 显示 SwarmWS，选择不变 | 选择共用 |
| T7 | 前端：overlay 关着时没有任何 `/workspace/tree` 请求；打开后才请求；关掉后轮询停止 | 不在后台刷新 |
| T8 | 前端：首次加载失败显示 `OverlayState` error 加 Retry；404 `no_working_repo` 回落到 SwarmWS；轮询失败不清空树 | 加载失败有提示 |
| T9 | 现有的 `navOverlayContract`、Workspace explorer、Terminal 位置相关测试原样通过 | 结构没被碰 |

每个修复都做反向验证。另外跑聊天相关的回归：`test_transcript_real_path.py`、liveReload 属性测试。

## 6. 实施顺序

| 阶段 | 内容 |
|---|---|
| W1 | 后端：`_resolve_root`，tree / expand / file 加 `root`，仓库模式的过滤规则；T1–T5 |
| W2 | 前端取数：`ExplorerProvider` 只在 overlay 打开时取数和轮询，改走 `overlayQueries`；T7 |
| W3 | 前端：位置切换器接共用的 `terminalLocation`，仓库模式下只读，加载失败用 `OverlayState`；T6、T8 |
| W4 | 更新 `terminal-locations.md`（这个 store 现在也是 Workspace 的位置）和 navcard 标准文档 |

每个阶段在自己的 worktree 里做，ff-only 合进本地 main，不 push。W1 要重启 daemon 才生效。按今天的教训，重启前要提醒你：你正在用的长 session，重启后下一条消息的首字会慢几分钟。

## 7. 不做的

- bindings 里其他仓库的下拉（Gawan 定：只显示工作仓库）。
- Workspace 打开 Terminal 的 custom 文件夹（不开放任意路径）。
- 在仓库里改文件。
- 把 Terminal 的 custom 文件夹也做成共用：custom 仍然只在 Terminal 里有。

## 8. 待定

- 仓库模式下首屏深度用 2 还是 1。swarmai 仓库顶层有 20 个左右目录，深度 2 的响应大小要在 W1 实测后再定。
