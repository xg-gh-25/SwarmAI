---
title: Left Nav 与 overlay：后端只读现成记录 + 视觉统一（字号、字重、颜色）
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: DRAFT — 待审
related: left-nav-overlay-single-source.md、left-nav-overlay-finish.md（前两份已 DONE，本文是之后的整体优化）
---

# Left Nav 与 overlay：后端只读现成记录 + 视觉统一

## 0. 原则（Gawan 定，2026-10-23）

1. **绝对不能影响聊天。** Nav 后面的任何工作都不能占用事件循环、默认线程池，也不能拿聊天要用的 git 锁。Nav 慢，最多是 overlay 自己慢。
2. **消费现成的，不现算。** 接口只读已经存在的记录：run.json / v1 投影、`run_index` 缓存、数据库里已有的表。不在请求里跑 git 历史、不全量解析文件。
3. **Pipeline run、changes、commits 都按 run 机制来，只看最近 30 天。** 30 天以前的没有意义，不打开、不统计。
4. **视觉：简约，只用主题的设计 token，零运行时成本。** 一套字号尺度、两种字重、一块和主体配色一致的颜色面板。

范围：Left Nav、OverlayHost 和 16 个 overlay（含 EvalDashboard）。聊天、Canvas 不在范围内，nav 的分组、层级、顺序、文案都不动。

## 1. 现状（实测，2026-10-23）

**后端**（只读 GET，各请求 4 次；脚本 `$KIROCREW_SCRATCH/bench_nav.py`）
- 热的时候，27 个接口里除 `/ddd/brains`（约 480 ms）外都在 150 ms 以内。
- 第一次请求慢：`/attention` 18.6 s、`/todos/history` 3.8 s、`/ddd/brains` 3.2 s（慢日志：last_commit 2276、pending 2798、dirty 2908 ms）。同一分钟 daemon 内存采样从 323 MB 掉到 111 MB，说明 macOS 在回收内存，和冷请求慢吻合，但不是证明。
- **13:44 事件循环卡了 38.8 s**，线程栈指向 `reflect_job._hook` -> `pipeline_human.owned_runs`：在事件循环上同步 glob 加 json 解析全部 run.json。
- run.json 共 751 个（7.6 MB），最近 30 天 191 个（其中 v1 1 个）；未结束的 run 只有 1 个（paused，在 30 天内）。
- Brain Hub 用 git 算"最近提交"：多数项目显示 2026-07-19（SwarmWS 自动提交，不反映项目工作）；3 个没提交过的项目每次要走完 14,503 个提交。
- 这几个 nav router 里有 60 处 `asyncio.to_thread`，全在默认线程池上，`core/executors.py` 写明这个池子饱和会拖慢聊天和 `/health`。
- 5 处 GET 里的 `git status` 没带 `GIT_OPTIONAL_LOCKS=0`（`workspace_api.py:289`，`ddd_brain.py` 169/196/354/405），会去拿 `.git/index.lock`，并设了 5 到 8 s 超时；超时结束进程可能留下 lock。

**视觉**（静态统计 nav + OverlayHost + 16 个 overlay，共 17 个文件）
- 字体族已经统一：`--font-family-sans`（SF Pro + PingFang SC）和 `--font-family-mono`，只定义在 `index.css`。
- 字号：手写像素 533 处，12 种值（`text-[11px]` 345 次，另有 8、8.5、10、11.5、12、12.5、13、14、15、20、22）；同时还混用 Tailwind 的 `text-xs` 84 次、`text-sm` 75 次。
- 字重：`font-medium` 111、`font-semibold` 104、`font-bold` 22，三种混用。
- 颜色：字面 hex/rgb 186 处（扣掉 `var(--x, #fallback)` 后多数是写死的），Tailwind 调色板 200 处（如表示错误同时用 `text-red-400` 和 `text-rose-500`）。BrainHub 用了 GitHub 暗色主题的颜色（`#58a6ff`、`#7ee787`、`#3fb950`、徽标底色 `#5a4a20`），浅色主题下不协调。
- nav 三个区的颜色写死在 `navTable.ts`（`#5fc99a`、`#4a8fb0`、`#7c8194`），不在 `index.css`，深浅主题用同一个值。
- 28 个按钮没用 `btn-*` / `icon-btn` 尺度。

## 2. 后端方案

### B1 聊天路径上不扫 run（最先做，唯一一处现在就在拖慢聊天）

两处在事件循环上同步解析全部 run.json：

| 位置 | 触发 | 改法 |
|---|---|---|
| `reflect_job.create_reflect_trigger._hook` -> `schedule()` -> `pipeline_human.owned_runs` | 每条含 `pipeline_v1` 的 Bash 命令之后（PostToolUse） | `_hook` 只把 `session_id` 交给 reflect 自己的单线程池就返回；查找 owned runs 移进池里的任务 |
| `security_hooks._count_required_approval` -> `record_required_approval` -> `_owned_engines` | 每次批准危险命令 / 对外发送之后（`:1787`、`:1926`） | 交给 `executors` 的专用池，fire-and-forget；批准结果立刻返回，不等计数 |

同时 `pipeline_human.owned_runs` 改读 `run_index.recent()`（B2），不再自己 glob 加 `json.loads`。

不变量：这两个钩子在事件循环上不做任何文件读、不解析 json。计数和 reflect 晚几百毫秒没关系，它们本来就是 fire-and-forget。

### B2 run 只有一个来源：`run_index.recent()`，只看 30 天

- `core/run_index.py` 新增常量 `RUN_WINDOW_DAYS = 30` 和 `recent(workspace)`。它用 `_run_files` 之后本来就要做的 `os.stat`，按 `st_mtime` 先过滤，**30 天以前的文件不打开、不解析、不进缓存**。run.json 每次状态变化都会重写，`st_mtime` 就是这个 run 最后一次动的时间。
- 所有读 run 的地方改用 `recent()`：

  | 读的地方 | 现在 |
  |---|---|
  | `routers/pipelines._load_pipeline_runs`（Pipeline overlay、`/pipelines`） | `run_index.runs()` 全量 |
  | `attention_authority._collect_paused_runs`（Need You） | 同上 |
  | `ddd_brain` 的 pending（经 attention collect） | 同上 |
  | `pipeline_human.owned_runs` | 自己 glob 全量 |

- 还在进行的状态只看一份定义 `run_index.LIVE_STATUSES`（已包含 legacy running/paused 和 v1 的 active/awaiting_human/reflecting）。
- 30 天内的旧机制 run（190 个）照常显示，只读，不做任何旧机制的处理（Gawan 决定）。
- **取舍（已接受）**：一个 run 超过 30 天没动，即使还停在 paused，也会从 Pipeline、Need You 里消失。今天这样的 run 为 0。
- 不改的：worktree 清理、gate 里判断"run 是否还活着"的地方仍读全量（`run_worktree._LIVE`、`security_hooks._ACTIVE_RUN_STATUSES`）。那是安全判断，不是统计，不能因为窗口漏掉。

效果：冷启动要解析的文件从 751 个降到约 191 个，之后由 `run_index` 缓存（文件没变就不重读）。

### B3 Brain Hub 的改动和提交：从 run 读，不跑 git

| 字段 | 现在 | 改为 |
|---|---|---|
| 最近提交 | 每个项目 `git log -1 -- Projects/<name>` | 这个项目 30 天内最后一个**已合入**的 run 的合入时间：旧 run 取 `commits` 非空时的 `completed_at`；v1 取投影 `landing in (landed, human_override)` 时的 `updated_at` |
| 有未提交改动 | 整仓 `git status`，失败时逐项目 `git status` | 这个项目有没有 `LIVE_STATUSES` 里的 run |
| pending | attention collect | 不变（它已改读 B2） |

- 删掉 `ddd_brain.py` 列表页用的 `_git_status_dirty`（169 行）、`_batch_dirty_project_dirs`（196）、`_parallel_last_commit_iso`（322），连同它们的测试一起删（按 lesson：换了数据来源，同一个改动里删掉旧来源的全部脚手架）。
- 数据全部来自 B2 已经在缓存里的 run，`/ddd/brains` 整个接口不起任何子进程。
- 详情页里逐文件的 git 标记（`_file_git_status` 354、`_batch_git_status` 405）和 Workspace 文件树的标记是同一类，按 B5 处理。
- **外观变化**：30 天内没有合入过 run 的项目，"最近提交"显示"--"；字段名不变。

### B4 nav 接口用自己的线程池

- `core/executors.py` 新增池 `nav`，2 个线程。Left Nav 各 overlay 调用的 GET（§1 的 27 个接口所在的 router）里的 `asyncio.to_thread` 全部改成 `executors.run_in("nav", ...)`。
- nav 慢的时候只是 `nav` 池排队，默认池和聊天用的 `gate`、`subprocess`、`spawn` 池都不受影响。
- 2 个线程也限制了同时在跑的 Python 计算，减少和事件循环抢 GIL。
- 守护测试：这些 router 模块里不允许出现 `asyncio.to_thread`。

### B5 Workspace 文件树的 git 标记：保留，不拿锁

- `workspace_api.py:289` 和 Brain Hub 详情页的 `_file_git_status`、`_batch_git_status` 这三处逐文件标记用的 `git status`，加 `GIT_OPTIONAL_LOCKS=0`，和 `repo_changes`、`run_worktree` 已有的做法一致；放进 `nav` 池（B4）。
- B3 删掉列表页的 git 之后，nav 里只剩这三处 git，而且都不写 index。聊天每个回合的 `git add -u` 快照不会再因为 nav 拿不到 index.lock。
- index.lock 反复残留是否就是这几处 `git status` 超时被结束造成的，实现时先写测试复现；复现不了就照实写"未证实"。

### 不做的

- 冷启动预热 `run_index`、按 HEAD 缓存 `git log`：B2、B3 之后不需要。
- 三个项目 `bindings.yaml` 缺 `bindings` 字段：是 SwarmWS 里的用户文件，不动，只在报告里提醒。

## 3. 视觉方案

### V0 约束

- 只改视觉表达（字号、字重、颜色、按钮尺寸）。nav 的分组、层级、顺序，overlay 的布局、信息结构、文案、testid 都不动。
- 零运行时成本：全部是 `index.css` 里的 CSS 变量和 Tailwind v4 `@theme` 生成的 utility，不加 JS、不加字体文件、不加图标库。
- **不重定义 Tailwind 自带的 `text-xs` / `text-sm`**：聊天和 Canvas 在用它们，改了就会影响范围外。新尺度用新名字。
- 不新增颜色，只用主题里已有的颜色，外加把 nav 三个区色收进主题。

### V1 字体与字号：一套尺度

字体族不变（已统一）。等宽字体只用于代码、id、sha、对齐的数字列。

字号尺度在 `index.css` 的 `@theme` 里定义一次（每个都带行高）：

| utility | 字号 / 行高 | 用途 |
|---|---|---|
| `text-meta` | 11px / 16px | 时间、计数、标签、徽标、列表的次要行 |
| `text-small` | 12px / 18px | 列表行的主要文字、表格、密集信息 |
| `text-body` | 13px / 20px | overlay 正文、说明文字（默认） |
| `text-heading` | 14px / 20px | 区块标题、卡片标题 |
| `text-title` | 16px / 22px | overlay 标题 |
| `text-display` | 20px / 26px | 指标大数字（只用于 Eval、Pipeline 的统计数） |

旧值怎么并：8 / 8.5 / 10 / 11 / 11.5 -> `meta`；12 / 12.5 和 `text-xs` -> `small`；13 -> `body`；14 / 15 和 `text-sm` -> `heading` 或 `body`（看用途）；`text-base` / `text-lg` -> `title`；20 / 22 / `text-xl` / `text-2xl` -> `display`。8 到 10px 这几处会变大一点。

**字重只留两种**：400（正文）和 600（标题、选中项、强调的数字）。`font-medium`（500）并到 400 或 600，`font-bold`（700）并到 600。SF Pro 下 500 和 600 区别很小，两档更简约。

### V2 颜色面板：和主体配色一致

overlay 里只允许用下面这些角色，全部是已有的主题变量，自带浅色、深色两套值：

| 角色 | 变量 |
|---|---|
| 背景层 | `--color-bg`（底）、`--color-card`（卡片、内容层）、`--color-hover` |
| 边框 | `--color-border` |
| 文字 | `--color-text`、`--color-text-secondary`、`--color-text-muted` |
| 强调 | `--color-primary`（跟随用户选的 accent） |
| 状态 | `--color-status-success`、`--color-status-warning`、`--color-status-error` |
| 区色 | `--color-zone-cognitive`、`--color-zone-work`、`--color-zone-system`（新收进主题，见下） |

- **浅色底一律用 `color-mix`**，例如 `bg-[color-mix(in_srgb,var(--color-status-error)_10%,transparent)]`，不再用 `bg-red-500/10`。这样深浅主题、accent 切换都自动正确。
- **区色收进主题**：`navTable.ts` 里写死的三个 hex 搬到 `index.css`，变成 `--color-zone-*`，深浅主题各一套（深色沿用现值；浅色加深到在白底上可读），`navTable` 改为引用变量。
- **映射**：

  | 现在 | 改为 |
  |---|---|
  | red / rose / `#ef4444` / `#ff9a94` | `--color-status-error` |
  | amber / yellow / orange / `#f0a500` / `#db8c3a` / `#5a4a20` | `--color-status-warning` |
  | emerald / green / `#3fb950` / `#7ee787` / `#5fc99a` | `--color-status-success`（表示"认知区"时用 `--color-zone-cognitive`） |
  | sky / blue / `#58a6ff` | `--color-primary` |
  | gray / slate / zinc / `#7c8194` / `#3b4552` | `--color-text-muted` 或 `--color-border` |
  | `text-white` / `bg-white` / `bg-black` | `--color-text` / `--color-card`（实色按钮上的白字保留为 `text-white`，但只限 `btn-primary` 这类实色底） |

- Eval 的趋势图如果确实需要第 4 种以上的系列色，实现时再列出来单独定，不先发明。

### V3 按钮

28 个手写按钮换成已有的 `btn-sm/md/lg` + `primary/secondary/ghost/soft/danger` 或 `icon-btn`。不新增按钮样式。

### V4 守住：一个只许变少的静态测试

`desktop/src/__tests__/navVisualSystem.test.ts`，扫描 nav、OverlayHost 和 16 个 overlay 的源文件，按文件统计：

- 手写像素字号（`text-[Npx]`、内联 `fontSize`）、Tailwind 自带字号（`text-xs/sm/base/lg/xl/2xl`）
- 字面颜色（hex、`rgb()`，`var(--x, #fallback)` 的兜底值不算）、Tailwind 调色板颜色
- `font-medium` / `font-bold`
- 没用 `btn-*` / `icon-btn` 的 `<button>`
- emoji 字符（V6：只用 Material Symbols）
- 手写的指标卡、区块标题、状态胶囊（以 V6 组件替换后，overlay 文件里不再出现这些组件的内部 class 组合；具体判据在写测试时按现有写法定）

每个文件的计数和一份基线 `KNOWN` **完全相等**：多了失败，少了但基线没改也失败。每迁移一个 overlay，它的那行降到 0；全部为 0 就是完成。做法和现有的 silent-except baseline、`iconScale.test.ts` 一样。

### V6 共用组件：光统一 token 还不够（依据：Gawan 2026-10-23 的 6 张截图）

截图里看起来"不像一个产品"，主要原因不是字号，而是同一种东西在每个 overlay 里长得不一样。外框已经统一（模式徽标、标题、副标题、ESC、关闭），里面没有统一。

| 同一种东西 | 现在各自的样子（截图） | 统一为 |
|---|---|---|
| 主色 | ToDo、Pipeline 用橙色 accent；Library 的 "+ Add Folder"、"Archive to Archives/" 是写死的蓝色；C&M、Library 的 tab 下划线和 "fresh" 是绿色；Brain Hub 的 SwarmAI 卡片顶边和 SELF·OS 是紫色 | 选中、主要操作、当前 tab 一律 `--color-primary`。区色只用在 nav 卡片、模式徽标、overlay 边框和尖角，不用在内容里 |
| 指标卡 | ToDo：图标 + 22px 大数字 + 大写标签；Pipeline：大写标签在上、数字和标签一样小；C&M：左栏 "86K"；Library：图标 + 数字 + 两行说明挤在一起 | 一个 `<Stat label value hint?>`：标签 `text-meta` 大写，数字 `text-display` 600，可选一行 `text-meta` 说明。不带图标 |
| 区块标题 | "CURRENT LOAD"、"HEALTH"（带扫帚 emoji）、"WEEKLY ACTIVITY"、"WORKING FILES"、"SWARMWS"，大小、字重、间距各不同 | 一个 `<SectionLabel>`：`text-meta` 600 大写，字距 0.06em，`--color-text-muted`，不带图标和 emoji |
| 切换视图 | C&M、Library：下划线 tab + 计数小胶囊 | 一个 `<Tabs>`：下划线 tab，下划线和选中文字用主色，计数用 `text-meta` muted |
| 筛选 | ToDo：实心橙色分段；Pipeline：浅色主色分段，放在右边 | 一个 `<Segmented>`：选中项浅色主色底（`color-mix` 15%）加主色文字，统一放在筛选行左侧 |
| 状态 | C&M：实心 "fresh/idle" 胶囊；ToDo："• Pending" 灰色胶囊；Pipeline："• stopped/unverified" 彩色文字不带底；Brain Hub："knowledge" 方块 chip；优先级 "• High" 红点 | 一个 `<StatusBadge tone>`：圆点 + 文字，`text-meta`，tone 只有 neutral / success / warning / error / primary，颜色来自状态 token；分类标签（knowledge、chat）用同一组件的 neutral |
| 表头 | C&M：小号大写灰色；ToDo：粗体白色大写，排序列是橙色；Pipeline、Library：没有表头 | 表格统一 `text-meta` 600 大写 muted 的表头，排序列只多一个箭头，不变色；纯列表（Library Recent、Pipeline 运行列表）不加表头 |
| 行高 | C&M 约 41px、Library 38、ToDo 47、Pipeline 51（日期折成两行）、Workspace 38 | 两档：单行 36px，两行 48px。日期列定宽不折行 |
| 说明文字 | ToDo：带边框的黄色提示框；C&M、Library：一段灰色小字 | 一个 `<Hint>`：一段 `text-small` muted 文字，不加框、不加颜色。ToDo 的提示框改成这样 |
| 图标 | Library 用 emoji（📗 🔗 🧹 🤖 ⬆），其余用 Material Symbols | 只用 Material Symbols，按 icon 尺度 |
| 按钮 | Library 的 "Delete" 是橙色实心，"Archive" 是蓝色实心，"+ Add Folder" 是整行蓝条 | 主要操作 `btn-primary`，删除 `btn-danger`，其他 `btn-secondary` / `btn-ghost`，按钮宽度跟内容走 |
| 空图表 | ToDo 的 "WEEKLY ACTIVITY"、Pipeline 的 "THROUGHPUT TREND" 有标题、没有图形 | 没数据时显示 `OverlayState` 的 empty，一行说明 |

组件放在 `desktop/src/components/overlay/`（新目录），每个都是几十行的纯展示组件，不带状态、不取数。overlay 的布局形态不变：C&M、Library 仍是左栏加 tab，Brain Hub 仍是卡片网格，ToDo、Pipeline 仍是筛选、指标、列表，Workspace 仍是文件树。只替换其中的零件。

**宽度**：navTable 现在有 s / m / l / xl 四档。截图里 ToDo（l）比 Pipeline（xl）窄一截，两个都是"指标 + 表格"，并排切换时外框跳动。建议：表格、看板类统一 `xl`（ToDo、Community、Artifacts、New Brain 从 l 改成 xl），文件树 Workspace 保留 `m`，Need You 保留 `m`。

**截图里顺带发现的问题**（不是样式，是显示错了，单独修，和视觉迁移分开提交）：
- Pipeline 的 "Needs you 0" 框里列着一个 stopped 的 run，数字和内容对不上；同一个 run 在下面的 SwarmAI 分组里又出现一次。
- Pipeline 的 token 列 "2727k/165k" 被右边的报告图标盖住了一截。
- Brain Hub 的 "96d ago"、"never" 就是 §1 说的 git 时间，B3 之后改为 run 的合入时间。
- Brain Hub 的 SwarmAI 卡片和其他卡片排版不同（数字竖排三行，其余是一行 "GROW · N sinking · 时间"）。统一成同一种卡片，SwarmAI 只多一个 SELF·OS 徽标。

### V5 推进方式

按 lesson：先迁一个，Gawan 看过再推开。

1. 定义 token（V1 字号、V2 区色变量）和 V6 的共用组件，加 V4 测试，基线 = 现状。这一步不改任何界面。
2. **试点：C&M、ToDo、Pipeline 三个一起迁。** 一个人看不出"像不像一个产品"，要并排比较才看得出；这三个正好覆盖三种形态（左栏加 tab、筛选加指标加表格、指标加列表），截图里差异也最大。迁完报告时写明哪几个画面最可能看起来不同。
3. Gawan 在深色、浅色主题下各看一眼，确认后再分批迁其余 overlay：Library、Brain Hub、New Brain -> Pollinate、Community、Jobs、Workspace -> Artifacts、Capabilities、Hive、History、Need You -> EvalDashboard -> nav 本身。

## 4. 验收测试（先写，先红）

| # | 测试 | 证明什么 |
|---|---|---|
| T1 | reflect 钩子和批准计数：把 `run_index` / 文件打开打桩成"调用就记一笔"，在事件循环线程上调用钩子，断言零次文件读、零次 `json.loads`，并且钩子返回前没有等计数完成 | B1：聊天路径上不扫 run |
| T2 | 真实临时目录里放 31 天前和 1 天前的 run.json：`recent()` 只返回后者，31 天前的文件**没被打开**（打桩 `open`） | B2：窗口在解析之前生效 |
| T3 | Pipeline、Need You、Brain Hub pending、`owned_runs` 四个读者，对同一组 run 得出一致的集合，30 天以前的都不出现 | B2：一个来源 |
| T4 | `/ddd/brains`：打桩 `subprocess.run` / `Popen`，断言整个请求零次子进程；最近提交、未提交改动和 run 里的事实一致；没有合入过 run 的项目返回空 | B3：从 run 读，不跑 git |
| T5 | 静态测试：nav router 模块里没有 `asyncio.to_thread` | B4 |
| T6 | 静态测试：`workspace_api` 和 `ddd_brain` 里每个 git 调用都带 `GIT_OPTIONAL_LOCKS=0` | B5 |
| T7 | `navVisualSystem.test.ts` 计数等于 `KNOWN` | V4 |
| T8 | `iconScale`、`buttonScale`、两份 nav 顺序测试、`navOverlayContract` 原样通过 | 结构没被碰 |

每个修复做反向验证：把修复改回去，对应测试必须变红。

聊天不受影响的回归：`test_transcript_real_path.py`、liveReload 属性测试、`test_turn_writer*`、权限审批相关测试原样通过。

后端改完后用 §1 的脚本重测，冷、热数字都写进 §7。

## 5. 实施顺序

| 阶段 | 内容 | 依赖 |
|---|---|---|
| S1 | B1 聊天路径不扫 run | 无，最先做 |
| S2 | B2 `run_index.recent()` 和四个读者 | 无 |
| S3 | B3 Brain Hub 从 run 读，删 git | S2 |
| S4 | B4 nav 线程池、B5 不拿锁 | 无 |
| S5 | V1/V2 token、V6 共用组件、V4 测试和基线 | 无，不改界面 |
| S5b | 截图里的显示错误：Pipeline "Needs you 0" 与内容矛盾、重复列出；token 列被遮挡 | 无，单独提交 |
| S6 | 试点 C&M、ToDo、Pipeline 视觉，ToDo 宽度改 xl | S5 |
| S7 | Gawan 看过后，分批迁其余 overlay（Brain Hub 在 S3 之后） | S6 确认 |

每个阶段单独提交，在自己的 worktree 里做，ff-only 合进本地 main，不 push。改后端的阶段需要重建、重启 daemon 才生效。

## 6. 已定的决定

- 30 天内的旧机制 run 照常统计和显示（Gawan，2026-10-23）。
- Workspace 文件树的 git 标记保留，改为不拿锁（Gawan，2026-10-23）；Brain Hub 详情页的逐文件标记同样处理。
- 超过 30 天没动的 paused run 不再出现（原则 3 的直接结果，今天为 0 个）。

**待 Gawan 定**：
- V6 宽度：ToDo、Community、Artifacts、New Brain 从 l 改成 xl（会改变这四个 overlay 的外框宽度）。
- V6 主色：区色退出内容区以后，C&M、Library 的 tab 下划线和 "fresh" 从绿色变成主色 / 成功色。

## 7. 结果

**S1**（`9570fb9c`）：reflect 钩子只把 session id 交给 reflect 的单线程池就返回；批准后的计数交给新池 `pipeline_signal`（1 线程）；`owned_runs` 改读 `run_index`。新测试 4 条，相关 143 个测试通过，两处反向验证都变红。

**S2**：`run_index.recent()`（30 天，按 stat 的修改时间先过滤，旧文件不打开）。Pipeline 列表、Pipeline 统计、Need You 的 paused run（以及经它算出的 Brain Hub pending）都改读它。和设计不同的两处：
- `owned_runs` 仍读全量 `runs()`：它驱动 Stop 暂停、提问卡片记录、reflect 这些 run 生命周期动作，属于安全判断，不是统计；S1 之后它已不在事件循环上，旧文件也已在缓存里，代价只是 stat。
- Pipeline 统计去掉了"Year to date"：数据源只有 30 天，YTD 和 30 天会是同一份数据，留着是误导。接口仍接受 `window=ytd`（旧客户端），一律按 30 天回答；前端去掉切换按钮。
- 删掉了 `test_aged_paused_decision_run_still_active`：它锁的正是原则 3 推翻的旧决定（超过 30 天的 paused run 必须显示）。新测试 `test_run_window.py` 锁相反的规则。

**S3**：`/ddd/brains` 不再起任何子进程。新函数 `_run_facts(ws)` 从 `run_index.recent()` 一次算出每个项目的最近合入时间和是否有进行中的 run；删掉了 `_git_status_dirty`、`_batch_dirty_project_dirs`、`_git_last_commit_iso`、`_batch_last_commit_iso`、`_parallel_last_commit_iso` 五个 git 函数和它们的测试。和设计不同的一处：字段 `health.uncommitted` 改名 `health.liveRun`，界面上的"uncommitted changes"改为"run in progress"/"a pipeline run in progress"。原因：值的含义变了，沿用旧名和旧文案就是错的说法。Review tab 的 `git diff` 是查看知识改动的功能本身，不在此列，S4 一并加 `GIT_OPTIONAL_LOCKS=0`。

**S4**：`executors` 新增 `nav` 池（2 线程）和 `run_nav(fn, *args, **kwargs)`（`to_thread` 的同形替代）。只服务 nav 的 10 个 router（attention、community_api、jobs、library_api、eval、ddd_brain、todos、pipelines、pollinate、hive）里的 `asyncio.to_thread` 全部改走 `nav` 池；`workspace_api` 只改两个文件树接口，文件读写接口 Canvas 也在用，保持原样。`ddd_brain`、`workspace_api` 里 14 处 git 调用都带 `GIT_OPTIONAL_LOCKS=0`（包括 Review tab 的 diff/apply）。守护测试是静态扫描，argv 是变量的 git 调用也算在内（`workspace_api` 的 `git status` 就是这种，字面量规则会漏掉）。`artifacts`、`agents`、`chat` 不在范围：前者已在专用池，后两者是聊天本身用的。
