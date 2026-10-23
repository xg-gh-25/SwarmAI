---
title: Workspace — manually browse your working directories (SwarmWS + local repos), with light change stats
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: DRAFT v4 — 待审
related: left-nav-backend-and-visual-system.md（nav 原则：不影响聊天、轻量、30 天窗口）
---

# Workspace：手动浏览工作目录

## 0. 定位（Gawan 2026-10-23，替换 v1–v3）

Workspace 是给用户**手动浏览整个工作目录**的地方：看目录里有什么，打开、附加到 chat、复制路径。

- **反映本地 main 的状态。** 显示的是用户磁盘上的主 checkout。不看 pipeline run 的临时 worktree。
- **不绑定 session，也不绑定 pipeline run。** 同时可能有 N 个 run 在跑，绑定哪一个都没有意义。不显示"agent 正在干什么"。
- **改动只给简单统计**：哪些地方有改动、改了几个文件、最近一次提交是什么时候。不做 diff 流，不做活动记录。
- **轻量，绝不影响聊天。**

v1–v3 里的这些全部去掉：this chat、`working_repo()`、按 run 列出合入了什么、Terminal 联动。

## 1. 位置

- **SwarmWS**：默认位置，永远在第一位。
- **本地仓库**：DDD 在 `bindings.yaml` 里声明了 `worktree` 的仓库，同时满足：
  - 目录真实存在；
  - `.git` 是目录，也就是 main checkout，不是 linked worktree；
  - 30 天内有活动，以 `.git/logs/HEAD` 的修改时间为准，任何提交、checkout、merge 都会更新它，只需一次 stat。

  按最近活动倒序排列。来源就是 `core.repo_changes.allowed_roots()` 已经算出的那份绑定清单，不另建登记表。

**今天实测**：bindings 里有 `worktree` 的有 2 个，`swarmai`（今天有活动）和 `AI-Ready-Repo`（91 天前，过滤掉）。所以位置条上是 `SwarmWS | swarmai`。

**已知取舍**：`agentcore-data-agent-handson` 这个月在 run 里用过，但没有写进任何 `bindings.yaml`，所以不会出现。要让它出现，就在对应 DDD 的 `bindings.yaml` 里声明它。这符合"不绑定 run"：位置列表只来自用户声明过的仓库。

## 2. 界面

```
[ SwarmWS · 0 changed ]  [ swarmai · 2 changed · 3h ago ]        ↻
```

- **位置条**用视觉规范里的 `Segmented`。每个位置显示两项统计：
  - 未提交的文件数：本地 main 的 `git status` 计数，0 就不显示；
  - 最近一次提交：一个相对时间，只给仓库显示。

  打开时默认在 SwarmWS；关掉再打开，回到上次看的位置（只在这次 app 运行期间记住）。
- **目录树**：
  - **SwarmWS**：Knowledge、Projects 分区，系统文件夹保护，写操作，全部和现在一样。
  - **仓库**：普通目录树，首屏深度 2，子目录懒加载。跳过 `.git`、`node_modules`、`.venv`、`dist`、`build`、`__pycache__`。只读，不能新建、重命名、删除。
- **改动标记（简单统计）**：
  - 有未提交改动的文件，沿用现有的 git 标记（M / U / D）；
  - 目录行右侧显示它下面有几个改动文件的计数小字，只显示数字，不显示列表；
  - 去掉 SwarmWS 现在顶部那块 "Working files" 卡片。它列出的多半是定时任务产物，而且每次 session 结束自动提交后就空了。统计并入位置条和目录计数。
- **隐藏杂项**：默认隐藏以点开头的目录和根目录下的零散文件，例如 `.artifacts`、`.git-recovery-backup-*`、`.pytest_cache`、`config-backup`。工具栏有"显示隐藏项"开关。
- **搜索框**：只搜已经加载出来的树。
- **右键菜单**：打开到 Canvas、附加到 chat、复制路径。SwarmWS 另外保留原有的写操作和 "Open terminal here"。
- **加载失败**：首次失败用 `OverlayState` 显示错误和 Retry，并写明是哪个位置。刷新失败时保留现在的树，位置条右边加一个小的错误标记。

## 3. 后端

### 3.1 `GET /api/workspace/locations`

- **返回**：`[{key, label, kind: 'default'|'repo', path?, changed, lastCommitAt?}]`。
- **计算**：
  - 位置：SwarmWS 加上 1 节筛出的仓库；
  - `changed`：对每个位置跑一次 `git status --porcelain -unormal`，只取行数，带 `GIT_OPTIONAL_LOCKS=0`；
  - `lastCommitAt`：`.git/logs/HEAD` 的修改时间。
- **缓存**：按 `(根目录, .git/index 的修改时间, .git/logs/HEAD 的修改时间)` 缓存。这两个文件都没变，就不重新跑 `git status`。
- **实测**：swarmai 的 `git status` 141 ms，SwarmWS 350 ms；缓存命中时只做两次 stat。
- **key**：`swarmws`，或者 `r_` 加 realpath 的 sha1 前 12 位。前端只能回传 key，不能传路径。

### 3.2 根目录解析与现有接口

- `_resolve_root(key)`：`swarmws` 沿用 `_get_workspace_path()`；`r_…` 在 3.1 的清单里查，查不到返回 404。
- `GET /workspace/tree`、`/tree/expand`、`/file` 加 `location` 参数。仓库模式下按 2 节的规则跳过目录，`path` 必须在根目录之内。
- 写接口不接受 `location`，带了就返回 422。
- 目录改动计数，在构建树的时候用同一次 `git status` 的结果按目录累加，不额外跑命令。

所有这些都跑在 `nav` 池，git 都带 `GIT_OPTIONAL_LOCKS=0`，由现有的静态检查守住。

## 4. 前端取数：不轮询

- 改用 `overlayQueries`，三条 key：`['overlay','swarmws','locations']`、`['overlay','swarmws','tree',key]`、`['overlay','swarmws','expand',key,path]`。
- **只在三种情况下刷新**：打开 overlay 时；点 ↻ 时；在 SwarmWS 里写操作（新建、重命名、删除）之后。去掉现在的 30 秒轮询。`ExplorerProvider` 不再在 app 一启动就取数。
- **预取**：鼠标悬停 nav 时预取位置列表和上次看的那个位置的树。
- 现有的 `RefreshTreeBridge` 换成 `invalidateOverlay(qc, 'swarmws')`。

## 5. 验收测试（先写，先红）

| # | 测试 | 证明什么 |
|---|---|---|
| T1 | 真实路由：`location=swarmws` 的 tree 和 expand 返回 200 | 不会再有今天那种 500 |
| T2 | 临时目录里准备 4 个 bindings：main checkout、linked worktree、不存在的目录、`logs/HEAD` 是 31 天前的。`/locations` 只列第一个；SwarmWS 永远在第一位；按活动时间倒序 | 位置只来自声明过的本地 main，30 天 |
| T3 | `/locations` 和 tree 的代码里不出现 `run_index`、`canvas_session`、`session_id`（静态检查） | 不绑定 run 和 session |
| T4 | `changed` 等于 `git status` 的行数；目录计数正确；`.git/index` 和 `logs/HEAD` 都没变时，第二次请求不起子进程 | 统计轻量 |
| T5 | 不认识的 key 返回 404；`../..`、绝对路径、软链接逃逸都被拒；写接口带 `location` 返回 422 | 不能读到外面、仓库只读 |
| T6 | 静态检查：这些接口不用默认线程池，git 都带 `GIT_OPTIONAL_LOCKS=0` | 不影响聊天 |
| T7 | 前端：打开前没有任何 `/workspace/*` 请求；打开时请求一次；之后 60 秒内没有新请求（没有轮询）；点 ↻ 才再请求 | 不在后台跑 |
| T8 | 前端：默认 SwarmWS，记住上次的位置；默认隐藏点开头的目录，开关能显示出来；仓库没有写操作的菜单项 | 用户视角的行为 |
| T9 | 前端：首次失败显示 `OverlayState` 加 Retry；刷新失败不清空树 | 加载失败有提示 |
| T10 | `navOverlayContract`、workspace explorer 现有测试原样通过 | 结构没被碰 |

每个修复做反向验证，并跑聊天回归。

## 6. 实施顺序

| 阶段 | 内容 |
|---|---|
| W1 | 后端：`/workspace/locations`（带缓存）、`_resolve_root`、tree / expand / file 加 `location`、目录计数；T1–T6 |
| W2 | 前端取数：去掉轮询，只在打开时取，改用 `overlayQueries`；T7 |
| W3 | 前端界面：位置条和统计、目录计数、隐藏杂项、仓库只读、`OverlayState`；去掉 Working files 卡片；T8、T9 |
| W4 | 更新 navcard 标准文档里 Workspace 的部分 |

每个阶段在自己的 worktree 里做，ff-only 合进本地 main，不 push。W1 要重启 daemon 才生效，重启前会先提醒你：你正在用的长 session，重启后下一条消息的首字会慢几分钟。

## 7. 不做的

- 任何和 run、session、Terminal 有关的东西。
- 没有在 bindings 里声明的仓库。
- diff、提交历史、活动记录。
- 在仓库里改文件。
- 后端全文搜索。
