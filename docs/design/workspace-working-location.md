---
title: Workspace — SwarmWS always, plus the repos you worked in this month (a browsing place)
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: DRAFT v2 — 待审
related: terminal-locations.md（Terminal 的位置；本文不再与它共用选择）、left-nav-backend-and-visual-system.md（nav 原则：不影响聊天、只读现成记录、30 天窗口）
---

# Workspace：SwarmWS 永远在，加上这个月用过的仓库

## 0. 决定与原则

Gawan 2026-10-23（v2，替换 v1 的"只显示工作仓库、选择与 Terminal 共用"）：

- **SwarmWS 是默认位置，永远在列表第一位。**
- **其他位置是最近 30 天里你在 pipeline run 中改过的仓库**，当前这个 chat 的工作仓库排在最前。
- **Workspace 是用户浏览东西的地方。** 按"用户想找什么、看什么"来设计，不按"agent 在哪里干活"。
- **绝不影响聊天。**

由此带来的一处改动：**Workspace 的选择不再和 Terminal 共用。** Terminal 的默认位置是 repo（它是动手干活的地方），Workspace 的默认位置是 SwarmWS（它是浏览的地方），两边共用就违背了"SwarmWS 是默认"。两边共用的是位置列表和"在这里开 terminal"这个操作，不共用当前选中哪一个。

沿用 nav 的原则：

1. **不影响聊天。** 所有读取都跑在 `nav` 线程池；git 一律带 `GIT_OPTIONAL_LOCKS=0`；overlay 关着时什么都不取；聊天路径上不新增任何东西。
2. **只读现成记录。** 仓库列表来自 `run_index.recent()`，这是 30 天窗口、已经缓存的 run 数据，不跑 git，也不扫描磁盘去找仓库。
3. **前端不能传路径进来。** 前端只传后端列表里给出的位置 key。

## 1. 用户在这里想做什么

| 想做的事 | 现在 | 设计后 |
|---|---|---|
| 翻 SwarmWS 里的知识、笔记、日报 | 能 | 不变，打开就是它 |
| 看正在改的仓库里有什么文件 | 看不到 | 点一下仓库位置 |
| 看那个仓库现在有哪些没提交的改动 | 看不到 | 顶部的 Working files |
| 回头看上周在另一个仓库干了什么 | 看不到 | Recent 里有，带"10 天前" |
| 找一个文件 | SwarmWS 里有搜索框 | 每个位置都有，搜索的是已经加载出来的树 |
| 打开到 Canvas 看 | 能 | 每个位置都能 |
| 附加到 chat、复制路径 | 能 | 每个位置都能 |
| 在这个目录开 terminal | 能（右键） | 每个位置都能 |
| 改、删、重命名 | SwarmWS 能 | 只限 SwarmWS；仓库只读，改动走 pipeline run |

## 2. 现状（2026-10-23 实测）

- Workspace 只显示 SwarmWS，根目录来自数据库里的 `workspace_config`。
- 最近 30 天的 run.json 一共只涉及 3 个 main checkout：
  - `swarmai`：149 个 run，0.7 天前；
  - `agentcore-data-agent-handson`：10 个 run，10.7 天前；
  - SwarmWS 自己：7 个 run，在列表里会和默认位置合并。

  所以列表会很短。
- **文件树在后台一直刷新**：`ExplorerProvider` 挂在 `ThreeColumnLayout` 根部，app 一启动就取整棵 SwarmWS 树，之后每 30 秒做一次 `git status` 加文件系统扫描，overlay 关着也照样跑。今天 16:05 起日志里每 30 秒一条 500，就是这个轮询在报错。
- **加载失败的提示**只有一行文字加一个 Retry 链接，没有用统一的 `OverlayState`。

## 3. 界面

### 3.1 位置条

Workspace overlay 的顶部放一排位置条，替换现在的 "SWARMWS" 标题。宽度档位 `m` 不变。

```
[ SwarmWS ]  [ swarmai · this chat ]  [ agentcore-data-agent-handson · 10d ]   ⋯
```

- **第一个永远是 SwarmWS**，打开 Workspace 默认就在这里。
- **当前 chat 有工作仓库时**，它排第二，标 "this chat"。
- **然后是 Recent**：最近 30 天里在 run 中改过的其他仓库，按最近一次 run 的时间倒序排，每个标"N 天前"。鼠标悬停显示完整路径、run 数和所属项目。
- **最多显示 4 个，其余收进 `⋯`** 下拉。今天实际只有 3 个，`⋯` 不会出现。
- **样式**用视觉规范里的 `Segmented`（`components/overlay`）：选中的位置是浅主色底加主色字，没有新样式。
- **记住上次看的位置**：只在 app 这次运行期间记住，关掉 overlay 再打开还在原处。app 重启后回到 SwarmWS，因为 SwarmWS 是默认位置。不按 chat tab 分开记，Workspace 是浏览的地方，不跟着某个 chat 走。

### 3.2 每个位置的内容

| | SwarmWS | 仓库 |
|---|---|---|
| Working files | `Knowledge/`、`Projects/` 下的改动（不变） | 整个仓库的 `git status` 改动 |
| 树 | Knowledge、Projects 分区，系统文件夹保护（不变） | 普通目录树：首屏深度 2，子目录懒加载；跳过 `.git`、`node_modules`、`.venv`、`dist`、`build`、`__pycache__` 和 gitignore 掉的顶层目录 |
| 搜索框 | 有（不变） | 有，只搜已经加载出来的树，不去后端扫描 |
| 右键菜单 | 不变 | 打开到 Canvas、附加到 chat、复制路径、在这里开 terminal。没有新建、重命名、删除 |
| 拖动 | 不变 | 只能拖进 chat 作为附件，不能在树里移动 |

### 3.3 在这里开 terminal

在任何位置右键 "Open terminal here"，或者点仓库位置旁边的 terminal 图标：

- 打开 Terminal 面板，切到这个目录。
- 对 Terminal 来说，这是一个 custom 位置；如果这个目录正好就是这个 chat 的工作仓库，就是 repo 位置。
- 只改 Terminal 的位置，不改 Workspace 的位置。

### 3.4 加载、空状态、错误

- **首次加载失败**：用 `OverlayState` 的 error 显示，带 Retry，并说明是哪个位置，例如 "Couldn't load swarmai"。SwarmWS 那个标签照样能点。
- **仓库已经不在了**（被删除或移动）：这个位置不会出现在列表里，因为列表本身只收真实存在的 main checkout。如果它在这次打开期间消失，显示一行提示 "swarmai is no longer on disk"，然后回到 SwarmWS。
- **轮询失败但已经有数据**：保留现在的树，只在位置条右边显示一个小的错误标记。

## 4. 后端

### 4.1 位置列表：`GET /api/workspace/locations?session_id=`

返回：

```json
{"locations": [
  {"key": "swarmws", "label": "SwarmWS", "kind": "default"},
  {"key": "r_<12 hex>", "label": "swarmai", "kind": "this_chat",
   "path": "/Users/…/swarmai", "lastActive": "2026-10-22T…", "runs": 149, "projects": ["SwarmAI"]},
  {"key": "r_<12 hex>", "label": "agentcore-data-agent-handson", "kind": "recent", …}
]}
```

- **来源**：`run_index.recent()`，也就是最近 30 天、已经缓存的 run。每个 run 取它点名的仓库（`worktrees[].main`，以及 commits 里的 repo）。
- **过滤**：只收 main checkout，即真实存在的目录、`.git` 也是目录，判断只用 stat。去掉 SwarmWS 自己。
- **合并排序**：按 realpath 合并，记下最近一次 run 的时间、run 数和所属项目。
- **当前 chat 的工作仓库**：用 `working_repo(session_id)`。它在列表里就标成 `this_chat` 并排到最前；它不在 30 天窗口里也照样列出来，因为它就是这个 chat 现在用的仓库。
- **key**：`r_` 加 realpath 的 sha1 前 12 位。这是一个稳定、不透明的 id，前端只能拿它回传，不能自己拼路径。
- **运行位置**：跑在 `nav` 池。这里没有新的缓存，读的就是 run index 那份现成的缓存。

### 4.2 根目录解析：`_resolve_root(key, session_id)`

- `swarmws`（默认）：沿用 `_get_workspace_path()`。
- `r_…`：在 4.1 那份列表里查，查不到就返回 404 `unknown_location`。查到后在请求时再核对一次是不是 main checkout。

前端传什么路径都不会改变根目录。

### 4.3 现有接口加 `location` 参数

| 接口 | 改动 |
|---|---|
| `GET /workspace/tree` | 加 `location`、`session_id`。ETag 缓存的 key 带上根目录；仓库模式下用 4.2 的过滤规则 |
| `GET /workspace/tree/expand` | 同上；`path` 解析后必须仍在根目录之内（`_is_path_under`） |
| `GET /workspace/file`（打开到 Canvas） | `location=r_…` 时，`path` 按仓库根目录解析，并做同样的包含检查。只读 |
| 写接口 | 不接受 `location`，带了就返回 422 |

所有这些都跑在 `nav` 池，git 都带 `GIT_OPTIONAL_LOCKS=0`，由现有的静态检查 `test_nav_pool_and_git_locks.py` 守住。

## 5. 前端取数

- **改用 `overlayQueries`**，三条 key：
  - `['overlay','swarmws','locations',sessionId]`
  - `['overlay','swarmws','tree',locationKey]`
  - `['overlay','swarmws','expand',locationKey,path]`
- **只在 overlay 打开时取**：`ExplorerProvider` 的首屏请求和 30 秒轮询，都只在 overlay 打开、而且窗口可见时进行，只针对当前选中的那个位置。关掉 overlay 就停。`ThreeColumnLayout` 的 `RefreshTreeBridge` 换成 `invalidateOverlay(qc, 'swarmws')`。
- **预取**：鼠标悬停 nav 时，预取位置列表和上次看的那个位置的树。
- **切换位置**：先显示缓存，30 秒内切回来不重新请求。

## 6. 验收测试（先写，先红）

| # | 测试 | 证明什么 |
|---|---|---|
| T1 | 真实路由：`location=swarmws` 的 tree 和 expand 返回 200（沿用 `test_workspace_tree_loads.py`） | 不会再有今天那种 500 |
| T2 | 用真实 run.json 准备三类仓库：近期 run 指向的仓库、31 天前 run 指向的仓库、指向 linked worktree 的 run。`/workspace/locations` 只列第一类；SwarmWS 永远在第一位且只出现一次；按最近活动倒序；当前 chat 的工作仓库排在最前并标 `this_chat` | 列表只来自 run，30 天，不猜 |
| T3 | `location=r_…` 返回那个仓库的树；Working files 等于那个仓库的 `git status`；`.git`、`node_modules` 不出现 | 仓库浏览 |
| T4 | 不认识的 key 返回 404；`path=../..`、绝对路径、软链接逃逸都被拒；写接口带 `location` 返回 422 | 不能读白名单之外、仓库只读 |
| T5 | 位置列表接口在打桩下没有任何子进程，并且只读 run index | 不跑 git、不扫描磁盘 |
| T6 | 静态检查：这些接口都不用默认线程池，git 都带 `GIT_OPTIONAL_LOCKS=0` | 不影响聊天 |
| T7 | 前端：打开 Workspace 默认在 SwarmWS；切到仓库，关掉再打开仍在仓库；app 重启后（清掉 store）回到 SwarmWS；切换 Workspace 的位置不改 Terminal 的位置 | SwarmWS 默认，选择不共用 |
| T8 | 前端：overlay 关着时没有 `/workspace/*` 请求；打开后才取；关掉后轮询停止 | 不在后台刷新 |
| T9 | 前端：首次加载失败显示 `OverlayState` error 加 Retry；仓库消失时回到 SwarmWS；轮询失败不清空树 | 加载失败有提示 |
| T10 | `navOverlayContract`、workspace explorer、Terminal 位置相关测试原样通过 | 结构没被碰 |

每个修复都做反向验证。聊天回归另跑：`test_transcript_real_path.py` 和 liveReload 属性测试。

## 7. 实施顺序

| 阶段 | 内容 |
|---|---|
| W1 | 后端：`/workspace/locations`、`_resolve_root`，tree / expand / file 加 `location`，仓库模式的过滤规则；T1–T6 |
| W2 | 前端取数：只在 overlay 打开时取，改用 `overlayQueries`；T8 |
| W3 | 前端界面：位置条、仓库只读、"在这里开 terminal"、加载失败用 `OverlayState`；T7、T9 |
| W4 | 更新 `terminal-locations.md`（只在"在这里开 terminal"上和 Workspace 交互）和 navcard 标准文档 |

每个阶段在自己的 worktree 里做，ff-only 合进本地 main，不 push。W1 要重启 daemon 才生效，重启前会先提醒你：你正在用的长 session，重启后下一条消息的首字会慢几分钟。

## 8. 不做的

- bindings 里那些 30 天内没有 run 的仓库。
- Terminal 选过的 custom 文件夹（它们是任意路径，不进白名单）。
- 在仓库里改文件。
- 后端全文搜索仓库。

## 9. 待定

- 仓库首屏深度用 2 还是 1，W1 实测 swarmai 的响应大小后再定。
- 位置条超过 4 个时，用 `⋯` 下拉还是横向滚动。今天只有 3 个，暂按下拉写。
