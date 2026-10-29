---
title: 两个自带 Brain（SwarmAI、General）和 Project Agent 标准
date: 2026-10-29
created: 2026-10-29
updated: 2026-10-29
status: r3，已实现；代码评审（correctness、ux）后按原因改完
related:
  - docs/design/2026-10-28-project-agent-flow.md（schema v2 §4、草稿生成 §5、决策 D2/D4）
  - docs/design/2026-10-29-hive-run-record-and-worker-role.md（第 5 步的端到端被 General 卡住）
---

# 两个自带 Brain（SwarmAI、General）和 Project Agent 标准

**r3（实现后）：** 代码评审（correctness、ux）之后，按原因又改了这几处：
- **打开 Library：** Library 没有 show 事件，原来发的 `swarm:show-library` 没有人听。改成由挂 Brain Hub 的那一层把 `openOverlay('library')` 传下来。
- **深链：** 不再用 `general: true` 这个标记，因为没有任何地方会发它。名字恰好是 `General` 的目标就打开 General 页面，事件和挂载前记下的目标都这样处理。agent 的 `show-brain-hub` 带 `brain: "General"` 时也一样，不会再落到 404 的详情页。
- **页面状态：** 加载中和读取失败分开显示，失败时有"重试"按钮。
- **同名提示的位置：** 提示放在同名项目自己的卡片上，那里才是人改名的地方。
- **另一种形状的标准 mode：** 生成器不收另一种形状 Brain 的标准 mode。有仓库的 Brain 提议 write、没仓库的 Brain 提议 code-change，都会丢掉。
- **几处小改：**
  - Need You 里 General 那一行写成"General (built-in)"。
  - BRAINS 的计数算上 General。
  - 没有 Runner Agent 时，卡片用警示色。

**r2：** 前提审查找出 6 个 MED、5 个 LOW，大多是设计对代码说错的地方。这一版按原因一次改完：
- **注册路径：** 还有 `ensure_project_metadata` 这条路，Hive 同步也会用大小写变体建出 Brain。
- **mode 的选择：** 是靠 `when` 选的，所以 research 和 write 的措辞不能重叠。
- **General 页面：** 不能靠名字 `General` 打开，否则会和同名的项目撞。
- **Library 计数：** 要按同步白名单算。
- **deviations：** 不能报生成器自己允许的改动。

## 0. 一句话

每个安装都自带两个删不掉的 Brain：SwarmAI（产品对自己的认知）和 General（不属于任何项目的活，知识就是 `Knowledge/Library`）。两个 Brain 都排在 Brain Hub 卡片墙的最前面。

它们的 Project Agent 是**标准范本**。自建 Brain 生成的草稿、两份自带定义、审查卡片上"和标准不同"的提示，读的是同一份标准（`core/project_agent_standard.py`），说明文档是 `templates/project-agents/STANDARD.md`。

## 1. 现在的样子（2026-10-29 读代码核实）

| | SwarmAI | General |
|---|---|---|
| 怎么来的 | 每次初始化工作区都会建 `Projects/SwarmAI`，带 4 份种子文档（`swarm_workspace_manager.py` 中 `_provision` 一段，`DEFAULT_PROJECT_NAME` 定义在 `project_registry.py:42`） | 不是一个项目。只有一份 Runner Agent 定义：`Knowledge/Library/project-agent.yaml`（`project_agent.py:135` `general_brain_dir`，`project_agent_seed.py:36`） |
| 删除和改名 | 改名被拒（`swarm_workspace_manager.py:2764`），删除被拒（`:2914-2926`），列表里固定排第一（`project_registry.py:166`） | 没有删除的路径。**但可以生出一个叫 `General` 的项目**，有三条路：<br>① 建项目和改名的检查只挡 Windows 保留名（`swarm_workspace_manager.py:728`、`:2889-2893`）。<br>② 注册函数 `ensure_project_metadata`（`:189`）根本不检查名字，pipeline CLI 的 `release-gate` 这类命令会经过它（`artifact_cli.py:165-192`）。<br>③ Hive 上的 `brain_sync.brain_root` 只把大小写完全一样的 `General` 当成通用 Brain（`brain_sync.py:356-366`），`general` 会被装成 `Projects/general`；本机推送前的排除也区分大小写（`brain_sync_host.py:95`）。派单（`task_spec.py:80`、`:204`）、同步（`brain_sync.py:358`、`brain_sync_host.py:90-95`）和教训范围（`hive_lessons.py:147-172`），都把名字叫 `General` 的当成通用 Brain。撞名时，这个项目会被当成 Library |
| 在 Brain Hub 里 | 卡片墙第一张 | 不在卡片墙上。卡片墙只列 `Projects/` 下带 `.project.json` 的目录（`ddd_brain.py:1024`、`:130-145`）。Runner Agent 的审查卡片只在首页最底下一行的 "Runner Agent · draft to confirm" 里（`BrainHubHome.tsx:227-243`），点开才看得到 |
| Need You | 草稿会进 Need You（`brainMapModel.ts:168`、`:203-206`） | 不进。Need You 只从卡片墙的 Brain 推，Runner Agent 是草稿的时候，没有任何地方提醒人 |
| 接口 | `/brains/{name}`、`/brains/{name}/project-agent/confirm` | `/ddd/runner-agent`、`/ddd/runner-agent/confirm`（`ddd_brain.py:1900-1919`），两个接口都已经有了 |

**Project Agent 的"标准"散在三处，已经不一致：**

| 标准 mode | 生成器 `DEFAULT_WHEN`（`project_agent_draft.py:45-48`） | `swarmai.yaml` | `runner.yaml` |
|---|---|---|---|
| research | "a question, an investigation or a design, with no code change" | "a question, an investigation or a design about SwarmAI, with no code change" | "a question or an investigation that belongs to no project" |
| code-change | "a change to the code of a bound repo" | — | — |
| write | — | — | "writing a document or a report that belongs to no project"（profile direct） |

- **共享部分不一样：** 两份自带定义都没有 `intake.handles`、`in_scope`。
- **字段顺序不一样：** 生成器写出来的顺序（`build`，`project_agent_draft.py:390` 起）和自带定义不同。
- **文件头注释不一样：** 三处各写各的。

一个用户照着 SwarmAI 去审自己的 Brain，看到的是三种写法。

## 2. 目标和不做的

**目标：**
1. **General 是一等 Brain：** 卡片墙上第二张（SwarmAI 之后），能打开，Runner Agent 的草稿进 Need You，不能删，不能被同名项目顶替。
2. **一份标准：** 共享部分的字段集合和顺序、标准 mode 的目录（id、when、profile、什么样的 Brain 有它）、文件头，代码只有一处。生成器、两份自带定义、审查卡片都从这一处读。
3. **两份自带定义是范本：** 按标准写全，包括 `intake.handles`、`in_scope`、`out_of_scope`。各代表一种 Brain 形状：General 是"纯知识，没有资产"，SwarmAI 是"产品自带、种子知识"。

**不做：**
- **不搬 Library：** General 的知识就是 `Knowledge/Library`，跨项目共享、无条件可 recall。不建 `Projects/General`（方案 A 已否）。
- **不把自带定义发成 reviewed：** 设计 D4 不变，每个用户自己审。
- **不改 schema v2 的字段和校验规则：** 标准只规定"草稿默认怎么写"，不新增"什么算错"。和标准不同只是提示，人可以有意改。
- **不改派单、路由和准入的规则。**

## 3. 设计 A：General 是一等 Brain

### 3.1 后端

- **列表：** `GET /api/ddd/brains` 的响应加一个字段 `general`，不放进 `brains` 数组：
  ```json
  {"name": "General", "description": "<Runner Agent 的 identity.summary>",
   "library": {"files": 52, "bytes": 812345},
   "projectAgentStatus": "draft|reviewed|invalid|null", "shadowed": false}
  ```
  - **为什么单独一个字段：** `brains` 数组的每一个消费方都把元素当成 `Projects/` 下的 DDD：画六段的花（`Flower`）、算 `hubSummary` 的计数、给"加知识"挑 Brain、六段的 `sectionsPresent`。General 没有六段，混进数组，每个消费方都要加一个判断，漏一个就是 bug。单独一个字段，只有要显示它的三处去读。
  - **`library` 怎么算：** 数的是同步到 Hive 时，General 真正带过去的知识：`brain_sync._walk(general_brain_dir(ws), True)` 走出来的文件里的 `.md`。白名单在 `brain_sync.whitelisted` 的 `general` 分支（`brain_sync.py:88-89`）：`.md` 去掉 `*-archive*`，外加定义文件本身。`/api/library/native` 数的是 Library 里的所有文件（`library_api.py:66-84`），口径不同，两边都不改。页面上写明"Library 里会带到 Hive 的 Markdown"。
- **保留名：** `General` 不管大小写都不能成为 `Projects/` 下的 Brain，三条路径都挡住。名字一律用 `project_agent.GENERAL_BRAIN`，比较时不分大小写，用一个函数 `project_agent.is_general_name(name)`。
  - **建项目和改名：** 在 `_validate_project_name`（`swarm_workspace_manager.py:2648`、`:2767`、`:2860`）里拒绝，错误信息说明它是自带的通用 Brain。
  - **注册：** `ensure_project_metadata`（`:189`）对这个名字抛 `ValueError`，不写 `.project.json`。调用它的 CLI 命令就会报错，而不是悄悄建出一个影子 Brain。已经有 `.project.json` 的目录照旧什么都不做（这个函数本来就只在没有 `.project.json` 时写）。
  - **Hive 同步：** `brain_root` 只认大小写完全一样的 `General`，其余大小写变体都拒绝（`SyncError`）；本机推送前的排除（`brain_sync_host.py:95`）改成不分大小写。
  - **SwarmAI 不用另加：** 它总是存在，建项目和改名时不分大小写的撞名检查已经会挡住。
- **已经存在的 `Projects/General`：** 不动它（不删不改名，属于用户数据）。列表照常列出它，`general.shadowed` 为 true。Brain Hub 在它自己的卡片上提示"Uses the built-in name General: rename it (ask in chat)"：Brain Hub 里没有改名的按钮，改名由 agent 在聊天里调现有的 `update_project` 完成（改成别的名字是允许的）。

### 3.2 前端（Brain Hub）

按 design-judgment 的规矩：一张卡回答一个决定。General 这张卡要回答的是："不属于任何项目的任务现在能不能派出去？"

- **卡片墙：** SwarmAI 第一张，General 第二张，其余照旧。
  - **卡片布局：** 和其他卡片同一套（名字、一句描述、一行状态），不画六段的花，换成 Library 图标。
  - **唯一突出的状态：** Runner Agent 的状态。草稿时显示"Runner Agent 待确认"，用 error 色。已审时显示"Runner Agent 已确认"。
  - **次要信息：** "Library 52 篇"，用 muted 色。
- **General 页面：** 有自己的打开方式，不借名字 `General`。`BrainHub` 的选中状态现在只是一个名字字符串（`BrainHub.tsx:147-152`）：
  - **问题：** 借用名字的话，一个已经存在的 `Projects/General` 就再也打不开了（D3 偏偏要人去打开它改名），而且所有 `/brains/General/*` 的 tab 都会 404。
  - **做法：** 选中状态加一种 `general`，由 `openGeneral()` 打开，渲染 `GeneralBrainView`，完全不走 `BrainDetailShell`。名字恰好是 `General` 的深链目标（`swarm:show-brain-hub` 的 `brain`，或挂载前记下的目标）也打开它。名字的其他大小写写法，打开的是同名项目自己的详情页。
  - **页面内容：** 三块，从上到下。
  1. **它是什么：** Runner Agent 的 `identity.summary` 和 `out_of_scope`，加一句"自带，不能删除"。
  2. **知识：** "Knowledge/Library · 52 篇 Markdown"，一个按钮"打开 Library"（`swarm:show-library`）。
  3. **Project Agent：** 现有的 `ProjectAgentCard brain={null}`，确认、改、提示和其他 Brain 一样。
  - **没有的东西：** 没有六段、Runs、Reflect、Eval 这些 tab，因为 General 没有这些数据；也没有删除和改名。
- **Need You：** `hubNeeds` 和 `hubSummary`（`brainMapModel.ts:203-219`）都多读 `general`，列表和计数保持一致。Runner Agent 是 draft 或 invalid 时加一条，`id` 是 `builtin-general-agent`，不和同名项目的 `General-agent` 撞，点"Open"调 `openGeneral()`。
- **数据进前端：** `services/ddd.ts` 的列表 mapper（`:310`、`:318`）目前只返回 `brains` 和 `pinned`，要把 `general` 带出来。New Brain 对话框的 `existingNames`（`overlaySurfaces.tsx:118`）要加上 `General`，提交前就能提示这个名字不能用。
- **"加知识到哪个 Brain"：** 列表里加 General，选它打开 Library 的加知识入口。
- **删掉：** 首页最底下那一行 "Runner Agent · …" 和它展开的卡片（`BrainHubHome.tsx:227-243`）。入口只留卡片墙这一个。

## 4. 设计 B：Project Agent 标准

### 4.1 一份标准，代码只有一处

新增 `core/project_agent_standard.py`，内容全是数据，外加两个小函数：

```python
SHARED_ORDER = ("schema", "status", "reviewed_at", "reviewed_by", "drafted_at",
                "identity", "intake", "confidence", "watches", "gates",
                "reserved_for_human", "placements", "landing",
                "modes", "default_mode", "sources")

MODES = {   # 标准 mode：什么样的 Brain 有它、when（按 Brain 的形状）、profile
  "research":    {"for": "every", "profile": "research",
                  "when": {"repo":    "a question, an investigation or a design, with no change to code",
                           "no-repo": "a question or an investigation, answered in the conversation"}},
  "code-change": {"for": "repo",    "profile": "bugfix", "delivery": "bundle",
                  "when": "a change to the code of a bound repo"},
  "write":       {"for": "no-repo", "profile": "direct",
                  "when": "a document or a report as the deliverable"},
}
DEFAULTS = {"reserved_for_human": [4 项], "placements": ["chat-tab"],
            "landing": {"approval": "human"}, "gates": {"enforce": []}}

def standard_modes(has_code_repo: bool) -> list[dict]   # 草稿的骨架
def deviations(doc) -> list[str]                        # 和标准不同的地方（提示，不是问题）
```

- **"有仓库"的定义：** `bindings.yaml` 里绑了仓库（`project_agent._bindings`，生成器也是看这个：`project_agent_draft.py:400`、`:466`），不看 DDD 资产的 kind。生成器和 `deviations` 用同一个判断。
- **`for` 的三种取值：**
  - `every`：每个 Brain 都有 research。
  - `repo`：有仓库才有 code-change。
  - `no-repo`：没有仓库才有 write。没有仓库的 Brain，交付的就是文档和报告；有仓库的 Brain，设计文档归 research。这条规则是新定的，见 §7 D2。
- **`when` 不能重叠：** 一个 Brain 里用哪个 mode，是 agent 对照各个 mode 的 `when` 选的（`task_spec.py:11`，`mode_when` 记在 spec 里，`:138`），`intake.handles` 只用来选 Brain（`:58-61`）。所以：
  - **没有仓库的 Brain：** research 说"在对话里回答"，write 说"交付物是一份文档或报告"，两者不重叠。
  - **有仓库的 Brain：** 没有 write，research 照旧包括设计。
  - **措辞通用：** 不带 Brain 的名字，mode 是哪个 Brain 的由它所在的定义决定。
- **`deviations` 报什么**（只报生成器自己不会写出来的东西，生成器的输出永远不提示）：
  - 共享部分缺字段：缺 `identity.summary`，或缺 `intake.handles`。
  - `reserved_for_human` 比默认少：人删掉保留动作是允许的，但值得在卡片上看到。
  - 有仓库，却没有 code-change。
  - **不报：** 标准 mode 的 profile 被改了。生成器允许有出处的 proposal 改 code-change 的 profile（`project_agent_draft.py:509-516`），报了就是自己提示自己。
- **`deviations` 怎么用：** 结果并进 `project_agent.notes()`（`project_agent.py:410`），它的 docstring 一起改。
  - **唯一的调用方：** `load_project_agent`（`:577`）。它喂给 `/brains/{name}`、`/runner-agent` 和审查卡片的提示行（`ProjectAgentCard.tsx:217-219`）。
  - **不读 notes 的地方：** Hive 的投影和派单都不读。
  - **所以：** 提示会自然出现在卡片上，不新加显示的地方。它们不算 problem，不挡确认，也不挡派单。

### 4.2 三个读取方改成读它

| 读取方 | 现在 | 改成 |
|---|---|---|
| 生成器 `project_agent_draft.build` | 自己的 `DEFAULT_WHEN`，固定的 research + code-change，自己的字段顺序 | 用 `standard_modes(has_code_repo)` 当骨架，按 `SHARED_ORDER` 写，文件头用标准的那一段。`DEFAULT_WHEN` 删掉。没有 code-repo 的 Brain 从此多一个 write mode |
| 两份自带定义 `templates/project-agents/{swarmai,runner}.yaml` | 手写，各说各的 | 按标准重写：标准 mode 的 `when` 和 profile 与目录一致，共享部分写全（`identity.in_scope` / `out_of_scope`，`intake.handles` 3-5 条），顺序和文件头与生成器相同。SwarmAI（种子知识，没有仓库）：research + write。General：research + write，`placements: [chat-tab, hive]`（D2：没有 Brain 的任务能派到 Hive） |
| 审查卡片 | 只显示 problems 和 notes | 不改代码。`deviations` 并进了 notes，会自动出现 |
| 路由 `task_spec.route` | General 的 `summary` 被换成一句固定的话（`task_spec.py:81`），只有 `handles` 来自定义 | 不改。所以范本里 General 的 `intake.handles` 要写好，路由真正读的是它 |

**`STANDARD.md`（`templates/project-agents/`，随产品发布）：** 写给人看。每个字段一行：做什么、谁写（代码/模型/人）、默认值、从哪推出来。再附上标准 mode 目录、Brain 的两种形状（纯知识、有代码仓库）、各自对应哪份范本。生成器给模型的提议说明（`survey` 里的 `format`）链到它，审查卡片的"这是什么"也链到它。按 R30#7，里面不放 run id、用户名或本机路径。

### 4.3 已经在用户机器上的定义

- **没改过的自带定义：** 本机文件的 sha256 等于上次发布时记下的 sha256，`seed_shipped` 会换成新发布的那份（`project_agent_seed.py:60-84`）。XG 机器上 General 那份就是这种情况（`.project-agent-shipped.json` 的 hash 和文件一致），会自动换新。
- **生成器写的或人改过的定义：** 不动，只在审查卡片上多出 `deviations` 的提示。XG 的 SwarmAI 就是生成器写的。想对齐，可以重跑 `artifact_cli project-agent-draft`，它不会覆盖已审的定义（`project_agent_draft.py` 模块说明）。

## 5. 已知限制

- **General 的知识只有 Library 的 Markdown。** 别的类别（Notes、Designs、Reports）不算 General 的知识，和同步到 Hive 的白名单一致（`brain_sync.whitelisted`，`brain_sync.py:88-89`）。页面上写清楚只数 Library。
- **write 的规则（§4.1）只影响新草稿。** 已有 Brain 的定义里没有 write，`deviations` 不会因此报，因为它报的是"缺字段"和"标准 mode 被改"，不报"少了一个可选的标准 mode"。

## 6. 验证计划（测试在写代码前写好）

**最后必须成立的不变量：**

| # | 不变量 | 测试 |
|---|---|---|
| S1 | 两份自带定义 `validate` 无 problem，`deviations` 为空（在没有绑定仓库的临时工作区里；绑了仓库却还用自带 SwarmAI 定义的机器上，会提示"有仓库却没有 code-change"，这是对的），字段顺序等于 `SHARED_ORDER` | `test_project_agent_standard.py` |
| S2 | 生成器给一个没有资产的 Brain 写出的骨架，mode 的 id、when、profile 和 General 范本相同；给一个有 code-repo 的 Brain 写出 research + code-change，与目录一致 | 同上，真生成器，`apply` 一个最小 proposal |
| S3 | `deviations` 对缺 `identity.summary`、缺 `intake.handles`、少了保留动作、有仓库没有 code-change 各报一条，并出现在 `notes()` 里；生成器允许的 profile 改动不报；另一种形状的标准 mode 不进草稿 | 同上 |
| S4 | 建项目、改名、`ensure_project_metadata`、pipeline CLI、Hive 上的 `brain_root`、本机推送列表都拒绝 `General` 的大小写变体（`brain_root` 只接受大小写完全一样的 `General`，当成 Library）；改成别的名字照常；已有 `.project.json` 的目录不受影响 | `test_project_agent_standard.py`（S4 一节，走真的 `create_project` / `update_project`） |
| S5 | `/brains` 有 `general` 字段，`library.files` 等于同步白名单里的 Markdown 数，状态和 `/runner-agent` 一致；已有 `Projects/General` 时 `general.shadowed` 为 true，那个项目照样在 `brains` 里 | 同上（S5 一节） |
| S6 | 没改过的 runner.yaml 被换成新版本，改过的不动 | 同上（S6 一节）；改过的不动由 `test_project_agent_draft.py` 已有的测试覆盖 |
| F1 | 卡片墙上 SwarmAI 第一张、General 第二张，状态行显示 Runner Agent 的状态（没有定义时用警示色）；BRAINS 计数算上 General；首页底部不再有 Runner Agent 那一行；同名项目的卡片提示改名 | `BrainHub.test.tsx`（"the built-in General Brain" 一组） |
| F2 | Runner Agent 是草稿时，Need You 有一条 `builtin-general-agent`（写成"General (built-in)"），计数也加一，点开是 General 页面；已审时没有；有同名项目时两条都在，id 不同 | `brainMapModel.test.ts`、`BrainHub.test.tsx` |
| F3 | General 页面：<br>• 有三块，没有删除和六段的 tab，不请求 `/brains/General`。<br>• 加载中、读取失败（带重试）、没有定义，三种情况分开显示。<br>• 两处"打开 Library"都真的切到 Library 这个 overlay。<br>• 名字恰好是 `General` 的深链能打开它；同名项目的卡片仍打开它自己的页面。 | `BrainHub.test.tsx`、`brainHubSurface.test.tsx` |

**每个修复的测试都做变异检查**（`scripts.mutation_check`）。

**评审：**
- 设计定稿前：correctness 做一次前提审查。
- 代码写完后：correctness（后端 + 前端）。UI 部分加 ux。

**最后的真机检查：** rebuild 之后，在 Brain Hub 卡片墙上看到 General，从卡片打开、确认 Runner Agent，然后派一个 General 的任务到 Hive，跑通 run 记录第 5 步的端到端。

## 7. 要定的（按建议做，任何一条都可以推翻）

| # | 问题 | 建议 |
|---|---|---|
| D1 | General 放卡片墙第几张 | 第二张，紧跟 SwarmAI。两张都是自带的，放在一起 |
| D2 | write 这个标准 mode 给谁 | 给没有绑定仓库的 Brain。它们的交付就是文档和报告。有仓库的 Brain，设计文档归 research |
| D3 | 已有的 `Projects/General` 怎么办 | 不动，卡片上提示改名。它是用户数据，自动改名或挡住都不对 |
| D4 | 两份范本要不要发成 reviewed | 不要，沿用原设计 D4，每个用户自己审 |
