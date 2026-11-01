---
title: Project Agent 只留 Hive Run 用得上的部分，不再确认
created: 2026-11-01
updated: 2026-11-01
project: SwarmAI
status: r1 已实现（2026-11-01）
tags: [project-agent, hive, brain-hub, deletion]
related:
  - docs/design/2026-10-28-project-agent-flow.md（原设计；本设计删掉它的确认、placements、delivery、每个 Brain 的保留动作、表单编辑）
  - docs/design/2026-11-01-project-agent-owned-by-agent.md（被本设计取代：底线、问题卡、widened 一并删掉）
---

# Project Agent 只留 Hive Run 用得上的部分

## 0. 为什么

XG 2026-11-01："每个 Brain 一个简单直接的 Project Agent，基本是标准模版，Setup DDD 的时候就出来了，能让 Hive Run 顺利用上就行。
有新版本就用新版本；不需要人来回 approve，你用的时候发现有问题就直接改，改完告知。对 Hive Run 没意义的就不需要存在。"

实测（2026-11-01）：10 个 Brain 的定义里，保留动作全是同样的四项，交付全是 bundle（`branch-pr` 准入直接拒绝），没有一个 mode 自己收紧过；
只有 2 个 Brain 允许上 Hive；一共派过 7 单。确认、底线、放宽、表单编辑这些机制保护的字段，在所有 Brain 上取值都一样。

## 1. 留下什么（Hive Run 真正读的）

Hive Run 从定义里读的只有这些（`core/task_spec.build` / `segment`、`core/project_agent_session.make_pin`）：

| 字段 | 用途 |
|---|---|
| `identity.summary` | 项目段第一行 |
| `modes[].id / when` | 选 mode |
| `modes[].repos` | Hive 上 clone 哪些仓库 |
| `modes[].setup / validations`（带 `source`） | 装依赖、结束前跑的检查；派单前在本机核对每条命令的来源在仓库里 |
| `modes[].skills` | 告诉 Hive 用哪些 skill（桌面专用的在 Hive 上不列） |
| `gates.enforce`（共享和 mode） | Hive session 里每条命令前跑的 Brain gate |
| `watches` | 项目段的"注意" |
| `default_mode`、`intake.repos` | 默认 mode；mode 的仓库必须在里面 |

revision 照旧：只用来记"这一单按哪一版派的"，派单时比对。

## 2. 删掉什么

- **确认：** `status` / `reviewed_at` / `reviewed_by`、`confirmation`、来源检查记录文件、确认路由和表单的确认、Brain Hub 的
  draft / changed / widened、P6 的 PENDING、今天加的底线（`project_agent_floor`）、问题卡（`project_agent_card`）、
  `scripts.project_agent`、gate 里的相关规则。派单时用最新的定义；定义有问题（校验不过、命令来源不在仓库），准入说明是哪一条，agent 改定义后再派，
  改完在对话里说一句。
- **每个 Brain 的保留动作：** push、开 PR、发布、对外发消息由产品固定，对每个 Brain、每个 mode 都一样；定义里写的不再读。
- **placements：** 每个 Brain 都能上 Hive；Hive 上不能用的桌面专用 skill 照旧不列。
- **delivery / pr_remote：** 交回什么在派单时定（`--returns`）；`branch-pr` 还没上线，不留字段。
- **表单编辑：** Brain Hub 的八段表单、`PATCH` 路由、`project_agent_form` / `project_agent_review` 删掉；Brain Hub 只读显示定义，
  "让 agent 修改"照旧发到对话里。人要改，说一句，agent 改。

起草时只写第 1 节的字段。`identity.in_scope / out_of_scope`、`intake.handles`、`confidence`、`proposed_aliases`、`sources`、
`profile` / `profile_rules` 对 Hive Run 没有用，起草不再写；旧文件里有的照旧忽略，不报错。

## 3. 验证计划

| # | 必须成立 |
|---|---|
| V1 | 一份从没"确认"过的定义（含 `status: draft`）能直接派到 Hive：spec 没有确认相关的拒绝，Brain 同步不因状态拦下 |
| V2 | 定义里删掉某个保留动作、写 `placements: [chat-tab]`、写 `delivery: branch-pr`，都不改变 Hive session 的保留动作（四项）、不拦派单 |
| V3 | 一条 setup / validation 的来源不在仓库：spec 拒绝，写明是哪一条 |
| V4 | 改了 skill 或定义：spec 给新 revision，派单带它就过，带旧的就拒（照旧） |
| V5 | Brain Hub：定义没问题时不出现任何"等你"；有问题时显示问题，不进 Need You |
| V6 | 删掉的路由、命令、模块没有残留的调用方（`core.code_intel.impact` + grep） |

测试：`test_task_spec`、`test_project_agent*`、`test_brain_sync*`、`test_pinned_execution`、P6 的测试、前端 brainMapModel / BrainMapView / BrainHub。
审查：correctness 一轮（删的东西有没有漏掉的读者）。

## 4. 实现

- 删掉：`core/project_agent_floor.py`、`core/project_agent_card.py`、`core/project_agent_review.py`、`core/project_agent_form.py`、
  `scripts/project_agent.py`、桌面 `ProjectAgentForm.tsx`，以及 Brain Hub 的确认 / 编辑路由、`run_owner_gate` 和 `ask_question_gate` 里的相关分支、
  同步白名单里的来源检查记录文件。
- `core/project_agent.py`：`validate` 不再看 status / placements / delivery / 保留动作；`_mode_view` 的保留动作是产品的四项；`source_problems`
  只做检查不留记录；Hub 状态词只有 ready / invalid。
- `core/task_spec.py`：准入不再查确认、placements、branch-pr；改为查命令来源（`source_problems`）。`spec["delivery"]` 固定 `bundle`
  （交回本机的 `hive_return` 读它）。
- `core/brain_sync_host.py`：定义有效就能同步。
- 起草（`project_agent_draft` / `project_agent_standard`）只写第 1 节的字段；`ordered` 写的时候丢掉 `RETIRED_KEYS`，旧文件下次写入时清干净。
- 桌面：`ProjectAgentView` 只读显示；Need You 里不再有 Project Agent 的条目；"让 agent 修改"发到对话。
- 附带修了一个昨天留下的测试：`test_hive_return` 的路由请求没写 `by: person`，被当成 agent 的命令。

## 5. 审查（correctness，一轮）

- 没有残留的读取方；同版本下四个留给人的动作对每一单都生效。
- **新 host + 旧 Hive（已修）：** 旧 Hive 从同步过去的定义里读留给人的动作，而定义里已经没有这个字段了。新 Hive 在 admission-state 里声明 `fixed_reserved: true`，host 派 Brain 的单之前要求有这一项，没有就拒绝，并提示先更新那台 Hive。缺 `placements` 的定义在旧 Hive 上报错，也由这一条挡住。
- **来源检查只看这次派单的 mode（已修）：** 一个 mode 里有过期的命令来源，不再挡住另一个 mode 的派单。
- **说明文字（已修）：** seed 模块、s_project-manager 和 `dispatch_spec` 里还在说"确认"的地方都改了。
- **不改：** 旧 host + 新 Hive 时，`.project-agent-check.json` 会让 Brain 同步被拒。Hive 的新版本由 host 打包推送，所以 host 总是先更新，这个组合不会出现。
