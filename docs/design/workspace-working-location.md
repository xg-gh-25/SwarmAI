---
title: Workspace — manually browse your working directories (SwarmWS + local repos), with light change stats
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: DRAFT v5 — 待审
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

**每个位置都是独立的一个 workspace，各看各的（Gawan 2026-10-23，v5）。** SwarmWS 和每个仓库各自有自己的树、统计、搜索词、展开状态、滚动位置、加载和错误状态，各自取数、各自缓存。看一个位置时，不计算、不显示其他位置的任何东西；一个位置加载慢或者失败，不影响其他位置。

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
[ SwarmWS ]  [ swarmai ]                                         ← 只用来切换，不带统计
────────────────────────────────────────────────────────────────
 swarmai · local main · 2 changed · last commit 3h ago      ↻   ← 当前这个位置自己的统计
 Filter loaded files…                                  ◌  ⇤
 ▸ backend                                         1 changed
 ▸ desktop                                         1 changed
 …
```

- **切换条**：用 `Segmented`，只写位置名，不带任何统计，因此切换条不需要等任何位置算完。打开时默认在 SwarmWS；关掉再打开，回到上次看的位置（只在这次 app 运行期间记住）。
- **位置头部**：只显示当前这个位置自己的信息：名字、`local main`、未提交文件数（0 就不显示）、最近一次提交（只给仓库显示），以及 ↻。↻ 只刷新这个位置。
- **每个位置的状态各存一份**：展开了哪些目录、搜索词、滚动位置、是否显示隐藏项，都按位置分开记。切回某个位置时，原样恢复。
- **目录树**：
  - **SwarmWS**：Knowledge、Projects 分区，系统文件夹保护，写操作，全部和现在一样。
  - **仓库**：普通目录树，首屏深度 2，子目录懒加载。跳过 `.git`、`node_modules`、`.venv`、`dist`、`build`、`__pycache__`。只读，不能新建、重命名、删除。
- **改动标记（简单统计）**：
  - 有未提交改动的文件，沿用现有的 git 标记（M / U / D）；
  - 目录行右侧显示它下面有几个改动文件的计数小字，只显示数字，不显示列表；
  - 去掉 SwarmWS 现在顶部那块 "Working files" 卡片。它列出的多半是定时任务产物，而且每次 session 结束自动提交后就空了。统计放进当前位置的头部和目录计数。
- **隐藏杂项**：默认隐藏以点开头的目录和根目录下的零散文件，例如 `.artifacts`、`.git-recovery-backup-*`、`.pytest_cache`、`config-backup`。工具栏有"显示隐藏项"开关。
- **搜索框**：只搜已经加载出来的树。
- **右键菜单**：打开到 Canvas、附加到 chat、复制路径。SwarmWS 另外保留原有的写操作和 "Open terminal here"。
- **加载失败**：只影响出错的那个位置。首次失败时，这个位置的区域用 `OverlayState` 显示错误和 Retry；切换条照样可以切到其他位置。刷新失败时保留这个位置现在的树，在它的头部加一个小的错误标记。

## 3. 后端

### 3.1 `GET /api/workspace/locations`：只给列表，不算统计

- **返回**：`[{key, label, kind: 'default'|'repo'}]`，按 1 节的规则排序。
- **计算**：只做 stat：目录是否存在、`.git` 是不是目录、`.git/logs/HEAD` 的修改时间（用来过滤 30 天、排序）。不跑 git，不读任何位置的内容。
- **key**：`swarmws`，或者 `r_` 加 realpath 的 sha1 前 12 位。前端只能回传 key，不能传路径。

### 3.1b `GET /api/workspace/summary?location=`：一个位置自己的统计

- **返回**：`{changed, lastCommitAt?}`，只针对这一个位置。
- **计算**：`changed` 是对这个位置跑一次 `git status --porcelain -unormal` 的行数，带 `GIT_OPTIONAL_LOCKS=0`；`lastCommitAt` 是 `.git/logs/HEAD` 的修改时间。
- **缓存**：按 `(根目录, .git/index 修改时间, .git/logs/HEAD 修改时间)`，每个位置一份。没变就不起进程。
- **实测**：一次 `git status`，swarmai 141 ms，SwarmWS 350 ms；缓存命中时只做两次 stat。

### 3.2 根目录解析与现有接口

- `_resolve_root(key)`：`swarmws` 沿用 `_get_workspace_path()`；`r_…` 在 3.1 的清单里查，查不到返回 404。
- `GET /workspace/tree`、`/tree/expand`、`/file` 加 `location` 参数。仓库模式下按 2 节的规则跳过目录，`path` 必须在根目录之内。
- 写接口不接受 `location`，带了就返回 422。
- 目录改动计数，用 3.1b 那份这个位置自己缓存的 `git status` 结果按目录累加，不额外跑命令。

所有这些都跑在 `nav` 池，git 都带 `GIT_OPTIONAL_LOCKS=0`，由现有的静态检查守住。

## 4. 前端取数：每个位置独立，不轮询

- 改用 `overlayQueries`。列表一条 key，其余每个位置各自一组：
  - `['overlay','swarmws','locations']`
  - `['overlay','swarmws',key,'summary']`
  - `['overlay','swarmws',key,'tree']`
  - `['overlay','swarmws',key,'expand',path]`
- **只取正在看的那个位置**。切到另一个位置时才取它，以前取过的就先显示缓存。
- **首屏深度 1**：SwarmWS 25 个节点，swarmai 51 个；现在默认深度 3 时分别约 3,500 和 4,900 个。点开一个目录时才取下一层。
- **统计和树分开请求**：头部的统计和第一层树并行取，谁先到谁先显示。
- **只在三种情况下刷新**：打开 overlay 时刷新正在看的位置；点 ↻ 时刷新这个位置；在 SwarmWS 里写操作之后刷新 SwarmWS。去掉现在的 30 秒轮询。`ExplorerProvider` 不再在 app 一启动就取数。
- **预取**：鼠标悬停 nav 时，预取位置列表，以及上次看的那个位置的统计和第一层。不预取其他位置。
- **每个位置的界面状态各存一份**（展开、搜索词、滚动、隐藏项开关），用位置 key 区分，存在内存里。
- 现有的 `RefreshTreeBridge` 换成 `invalidateOverlay(qc, 'swarmws')`，只作废 SwarmWS 那组 key。

## 5. 验收测试（先写，先红）

| # | 测试 | 证明什么 |
|---|---|---|
| T1 | 真实路由：`location=swarmws` 的 tree 和 expand 返回 200 | 不会再有今天那种 500 |
| T2 | 临时目录里准备 4 个 bindings：main checkout、linked worktree、不存在的目录、`logs/HEAD` 是 31 天前的。`/locations` 只列第一个；SwarmWS 永远在第一位；按活动时间倒序 | 位置只来自声明过的本地 main，30 天 |
| T3 | `/locations` 和 tree 的代码里不出现 `run_index`、`canvas_session`、`session_id`（静态检查） | 不绑定 run 和 session |
| T4 | `/summary?location=` 的 `changed` 等于这个位置 `git status` 的行数；目录计数正确；`.git/index` 和 `logs/HEAD` 都没变时，第二次请求不起子进程；`/locations` 不起任何子进程；请求 swarmai 的统计时不对 SwarmWS 跑 git，反过来也一样 | 统计轻量、各算各的 |
| T5 | 不认识的 key 返回 404；`../..`、绝对路径、软链接逃逸都被拒；写接口带 `location` 返回 422 | 不能读到外面、仓库只读 |
| T6 | 静态检查：这些接口不用默认线程池，git 都带 `GIT_OPTIONAL_LOCKS=0` | 不影响聊天 |
| T7 | 前端：打开前没有任何 `/workspace/*` 请求；打开时只请求正在看的那个位置；之后 60 秒内没有新请求（没有轮询）；点 ↻ 只刷新当前位置 | 不在后台跑，只取当前位置 |
| T8 | 前端：默认 SwarmWS，记住上次的位置；切换条上没有任何统计；在 swarmai 展开一个目录、输入搜索词，切到 SwarmWS 再切回来，原样恢复，SwarmWS 不受影响；默认隐藏点开头的目录；仓库没有写操作的菜单项 | 各看各的 |
| T9 | 前端：swarmai 首次失败时，只有它的区域显示 `OverlayState` 加 Retry，切到 SwarmWS 正常显示；刷新失败不清空树 | 一个位置出错不影响其他位置 |
| T10 | `navOverlayContract`、workspace explorer 现有测试原样通过 | 结构没被碰 |

每个修复做反向验证，并跑聊天回归。

## 6. 实施顺序

| 阶段 | 内容 |
|---|---|
| W1 | 后端：`/workspace/locations`（只 stat）、`/workspace/summary`（每个位置一份缓存）、`_resolve_root`、tree / expand / file 加 `location`、目录计数；T1–T6 |
| W2 | 前端取数：去掉轮询，首屏深度 1，按位置分开的 `overlayQueries`，只取当前位置；T7 |
| W3 | 前端界面：切换条、位置头部的统计、按位置分开的界面状态、目录计数、隐藏杂项、仓库只读、按位置的 `OverlayState`；去掉 Working files 卡片；T8、T9 |
| W4 | 更新 navcard 标准文档里 Workspace 的部分 |

每个阶段在自己的 worktree 里做，ff-only 合进本地 main，不 push。W1 要重启 daemon 才生效，重启前会先提醒你：你正在用的长 session，重启后下一条消息的首字会慢几分钟。

## 7. 不做的

- 任何和 run、session、Terminal 有关的东西。
- 没有在 bindings 里声明的仓库。
- diff、提交历史、活动记录。
- 在仓库里改文件。
- 后端全文搜索。
