---
title: Project Agent 标准模板 —— 一张表单，Brain 一建好就有
date: 2026-10-29
created: 2026-10-29
updated: 2026-10-29
status: r2（r1 经一轮零上下文前提审查：3 HIGH、5 MED、1 LOW，按原因一次改完，见 §13），等 XG 看
related:
  - docs/design/2026-10-28-project-agent-flow.md（schema v2 §4、草稿生成 §5）
  - docs/design/2026-10-29-default-brains-and-project-agent-standard.md（标准模式、STANDARD.md）
  - Knowledge/Mockups/2026-10-29-project-agent-form.html（页面 mock）
---

# Project Agent 标准模板 —— 一张表单，Brain 一建好就有

## 0. 一句话

Project Agent 是一个产品能力：**每个 Brain 同一张表单**，8 个区，每个字段说清它是什么、干什么、谁能改。
这张表的定义在后端只有一份（`TEMPLATE`），页面、STANDARD.md、生成器、确认/编辑接口都从它读。
存储格式不变（schema 2），表单只是把现有字段按区组织好、把 Brain 里已有的能力如实呈现出来。
Brain 一创建，代码就写出一份骨架；agent 补判断字段；人在表单里改、确认。

## 1. 现在的样子（2026-10-29 读代码核实）

- **所有 Brain 都已经有定义**：10 个 Brain 里 9 个是草稿，SwarmAI 已确认。缺的不是定义，是**没人确认的草稿**，
  以及看不懂、改不了的页面。
- **建 Brain 时没有代码写定义**：`create_project`（`swarm_workspace_manager.py:2618`）不写，要靠 agent 跑
  s_project-manager P3b。agent 没跑，P6 因为文件缺失而 FAIL（`verify_ddd_complete.py:432`），这是逼 P3b 执行的唯一信号。
- **profile 是有用的**：run 的 profile 在创建时定死（`run_protocol/cli_commands.py:125` 默认 `direct`，
  `lifecycle.py:205-214` 按它选阶段），之后不会再改。无人值守的 Hive run 只能从定义拿 profile
  （`task_spec.py:118,216-217`），本地从 Brain 页提示词拿（`BrainMapView.tsx:385`）。所以表单不能删掉 profile。
  `profile_rules` 在代码里是允许名单（`task_spec.py:89-95`），条件文字由模型匹配。
- **`local-worker` 是死值**：只有枚举、P6 回退和一个写死的 mock 单选（`BrainMapView.tsx:360`）。
- **定义的守卫只在 Hive 上执行**：桌面端没有任何地方发 `ChatRequest.project_agent`，本地会话不 pin 定义，
  `reserved_for_human` 和 Brain gates 只在 Hive 生效（`make_pin` → `security_hooks.py:1896-1904`、`run_gates`）。
  本地仍有产品守卫（危险命令、对外动作）。
- **`landing` 无人执行、无人显示**；`drafted_at` 没有读者。
- **有字段没显示**：`identity.in_scope`、`confidence`、`intake.repos`、`sources`。
- **卡片只能删行、接受别名、开关 Hive**（`project_agent_review.py:40-46,139-149,184-223`），改文字只能去 chat。
- **Brain 的能力在页面上看不到**：skills 只是模式下的名字；`aim.json` 的 tools/MCP/jobs 不出现；
  能不能上 Hive 要读 `brain_sync` 才知道。
- **字段名单散在多处**：`SHARED_ORDER`/`MODE_ORDER`（`project_agent_standard.py:30-39`）、`PROPOSAL_CONTRACT`
  （`project_agent_draft.py:60-97`）、确认接口的路径正则（`project_agent_review.py:40-46`）、DTO（`project_agent.py:515-593`）、
  卡片 TSX。

## 2. 目标和不做的

**目标**
1. 一张标准表单，8 个区，顺序固定；每个字段带：名字、一行用途、是否必填、值、出处、**谁能改**。
2. 字段名单只有一份（`TEMPLATE`）；上面列的五处都从它生成，测试保证不分叉。
3. Brain 创建时就有骨架；骨架不冒充"已起草"，P6 仍逼 P3b 补完。
4. Brain 的能力（skills、tools、jobs、gates、产品守卫）只读呈现，写明哪些能到 Hive。
5. 表单只写真话：守卫写明在哪里执行；不显示做不到的选项。
6. Editable 字段能在表单里直接改。

**不做（各自另起设计）**
- **不改存储格式**：仍是 schema 2。r1 打算改名归组成 v3，审查发现 Hive 跑旧代码、确认接口会把规范化后的形状写回磁盘，
  而 Hive 没有能报"认哪个 schema"的信号 —— 改名换不来任何能力，删掉这一步（§13 #3、#4）。
- **不做本地 pin**（从 Brain 页发起的本地会话也执行定义）：发送是往输入框注入文字，带不上 pin；已有会话的 tab 会悄悄丢掉 pin；
  名字带空格的 Brain pin 不了；本地编辑自己 `3-gates/` 会被 gate 锁住（§13 #6）。表单如实写"定义的守卫在 Hive 上执行"。
- **不扩 Hive 同步白名单**：`aim.json domain_tools` 不全（CMHK 的 client、queries 没声明），依赖内网密钥，
  且会让一个可编辑文件决定什么出机器（§13 #7）。表单如实写"不会到 Hive"。
- **不做 Slack 通道**：Runs in 区显示"Slack · 暂不可用"，定义里不能选。
- **不做 EVALUATE 阶段重新选 profile**。

## 3. 表单：8 个区，先说它是什么，再说边界，再说怎么接活和何时找你

| 区 | 字段 | 存在哪 | 谁能改 |
|---|---|---|---|
| **1 Identity** | Name（= Brain 名）· Brain 目录 · 仓库（名 / 本机目录 / 远程 URL，去掉凭据） | 读时算（bindings） | 来自 Brain |
| | Summary（必填）· Takes on · Refuses | `identity.*` | 可改 |
| **2 Capabilities** | Skills 表：名字、做什么、哪个模式用、能否上 Hive | 读时算（4-capabilities + 模式的 `skills`） | 来自 Brain；分给哪个模式在 3 区改 |
| | Tools 和 MCP（`aim.json`）· Jobs | 读时算 | 来自 Brain |
| **3 Modes** | 模式表：id、Used when、开始时的 profile、检查数；选中一行编辑：Used when（必填）、开始时的 profile、仓库（Identity 的子集时才显示）、Setup、Checks、Skills（勾选）、Delivers | `modes[]` | Used when / profile / Delivers / skill 勾选可改；Setup、Checks 来自仓库声明，只能勾掉；标准模式不能删 |
| **4 Guardrails** | 总是先问我（push、开 PR、发版、对外发消息） | `reserved_for_human`（含模式级） | 你定 |
| | Brain gates（`3-gates/`，可以取消某个 gate 的执行） | `gates.enforce` | 来自 Brain，你可以取消 |
| | 产品守卫（危险命令、对外动作、后台命令、语法、`ln`） | 产品 | 固定 |
| | 一行事实："定义的守卫在 Hive 上执行；chat tab 里只有产品守卫" | — | 固定 |
| **5 Runs in** | Chat tab · 本地（总开）· Hive · 云（开关；列出不上云的模式和原因）· Slack · 云（暂不可用） | `placements`（含模式级收窄） | Hive 开关你定；模式收窄是定义的一部分 |
| **6 Intake** | 典型请求（必填，3-5 条）· 别名（勾选保留，确认时写进 `aim.json`） | `intake.handles` / `proposed_aliases` | 可改 |
| **7 Judgment** | 拿不准时 · 什么情况走更重的流程（条件 + 用哪个 profile）· Review focus | `confidence` / `modes[].profile_rules` / `watches` | 可改 |
| **8 Pipeline profiles** | full · bugfix · trivial · direct · research：适合什么、跑哪些阶段、哪些模式从它开始 | 产品 | 固定，只读 |

**去重：每个事实只出现一次**
- 仓库只在 Identity；模式里只在它是子集时显示。
- skill 的说明、platform、能否上 Hive 只在 Capabilities；模式里只有勾选。
- gates 和产品守卫只在 Guardrails（它们是限制）；Capabilities 只放能做的事（skills、tools、jobs）。
- 运行位置只在 Runs in；模式收窄写在 Hive 那一行。
- profile 的含义只在参考区；Modes 里只有"开始时用哪个"，Judgment 里只有"什么条件升级到哪个"。

**四种"谁能改"**（后端按 TEMPLATE 给每个字段算好，前端照着画）

| 标记 | 意思 |
|---|---|
| Editable | 从 Brain 文档起草，在这里改 |
| From the Brain | 从 Brain 文件读出，这里只读，链接到去哪里改 |
| Your choice | 只有人能定 |
| Fixed | 产品规定，每个 Brain 一样 |

## 4. 存储：仍是 schema 2，只删死字段

- **不再写**：`landing`（无执行、无显示）、`drafted_at`（无读者）。validate 本来就不要求它们，旧 Hive 读得了。
  已有文件里的这两个键照读不误，不改写文件。
- **枚举去掉 `local-worker`**：没有任何文件用它；连同 `BrainMapView.tsx:360` 写死的 mock 单选、
  卡片 label、P6 回退、`test_project_agent_v2.py:91,181` 一起删。
- **删死代码**：`desktop/src/components/swarm-out/TraceTags.tsx` 没有任何引用，删掉。
- 其余字段名、含义、validate 规则不变，所以 revision、Hive pin、确认的 409、seed 的"未改动才替换"都不受影响。

## 5. 一份 TEMPLATE，替换五处字段名单

`core/project_agent_standard.py` 增加 `TEMPLATE`：按区排好的字段表，每个字段
`{key, section, label, help, kind(edit|brain|you|fixed), required, store(v2 路径或 derived), example}`。从它生成：

1. `SHARED_ORDER` / `MODE_ORDER`（写文件的键顺序）；
2. `PROPOSAL_CONTRACT`（agent 起草时看到的格式）；
3. 确认/编辑接口接受的路径；
4. DTO：`sections: [{id, title, purpose, fields: [{key, label, help, kind, required, value, source, path}]}]`；
5. STANDARD.md 的字段表（生成后比对，不一致即红）。

前端按 DTO 通用渲染，TSX 里不写字段名单。

`required` **不进 validate**：validate 管"能不能执行"，必填管"起草完没有"（§6）。

Derived 字段扩展现有函数，不另写：
- `project_agent_draft.survey` 已返回仓库、skills（含 platform）、gates、命令 → 加上仓库的本机目录和远程 URL
  （URL 去掉凭据）、skill 描述、`aim.json` 的 tools / MCP / jobs。
- 能否上 Hive：skill 用 `task_spec` 已有的 platform 过滤（`skills_not_here`）；文件用 `brain_sync.allowlisted`；
  Brain 级原因用 `brain_sync_host._row` 已有的判断。
- `project_agent.notes` 已有的提示（例如 Hive 模式里有只能本机跑的 skill）原样放进对应字段旁边。

## 6. 建 Brain 就有骨架，P6 仍逼着补完

1. 新函数 `ensure_project_agent_skeleton(project_dir)`：文件不存在时，用 `build(proposal=None)` 写一份
   （标准模式、仓库对应的 setup/checks、skills 分到默认模式、全部保留动作、placements = chat-tab）。
   出错只记日志，绝不让建 Brain 失败。`create_project` 和 `ensure_project_metadata` 都调它。
2. **骨架可识别**：没有 `identity.summary`、也没有 `intake.handles` 的草稿就是骨架（今天 10 个定义都有这两项）。
   P6 遇到骨架 FAIL："Project Agent 还没起草（P3b）"，所以骨架不会让一个没跑完 P3b 的 Brain 被记为 ready。
   表单上骨架的必填字段显示示例占位和"起草"按钮。
3. **起草不再覆盖人的修改**：`apply` 加合并模式（默认开启）—— proposal 没给的判断字段，保留现有文件里的值。
   "从 Brain 重新起草"和 P3b 都走它；"完全重来"保留为显式选项。旧草稿照旧备份到 `.artifacts/project-agent/previous-*`。
4. **绑定新仓库后**：bind 调一次合并模式的 `apply`，补上 code-change 模式（没有时）和新仓库的 setup/checks，
   不动已有的判断字段；定义回到草稿，需要重新确认。

## 7. 在表单里改

新接口 `PATCH /ddd/brains/{name}/project-agent`：`{revision, set: [{path, value}], remove: [path]}`。
- 只接受 TEMPLATE 里 kind = edit / you 的路径（由 TEMPLATE 生成，和确认接口共用一份）；值按字段类型校验
  （单行文字长度、列表、枚举、profile 名）；整份过 validate；写之前不做 normalize 之外的改写。
- 写入后 status = draft，revision 变；带旧 revision 的请求得 409（沿用确认接口的做法）。
- 底部操作栏：N 处改动 · 放弃 · 确认（确认 = 现有 confirm 接口，接受别名、Hive 开关不变）。
- 页面在 Hive 上只读（沿用确认接口在 Hive 上 403 的规则）。

## 8. 页面文案就是事实

- Guardrails 区底部固定一行："定义的守卫在 Hive 上执行；chat tab 里只有产品守卫。"
- Capabilities 的 Tools 行写"到 Hive：不会（只同步 data-contract 文档）"，由 `brain_sync.allowlisted` 算出。
- Runs in 的 Hive 行列出这个 Brain 到 Hive 时缺什么（只能本机跑的 skill、MCP），由 §5 的现有函数算出。
- Slack 行显示"暂不可用"，开关禁用；页面不出现 local-worker。

## 9. 分步落地（每步单独提交、单独验证）

| 步 | 内容 | 主要改动 |
|---|---|---|
| 1 | TEMPLATE；五处字段名单从它生成；STANDARD.md 生成比对 | project_agent_standard、project_agent_draft、project_agent_review、project_agent、STANDARD.md |
| 2 | 删死字段和死代码：landing / drafted_at 不再写，local-worker，TraceTags | draft、standard、project_agent、BrainMapView、BrainMap、ProjectAgentCard、P6、模板 yaml |
| 3 | survey 扩展 + DTO 按区返回 + PATCH 接口 | project_agent_draft、project_agent、routers/ddd_brain、project_agent_review |
| 4 | 骨架 + 合并模式 apply + bind 时补模式 + P6 识别骨架 + P3b 文档 | swarm_workspace_manager、project_agent_draft、bind 路径、verify_ddd_complete、s_project-manager |
| 5 | 表单页面替换 ProjectAgentCard；General 页同一组件 | desktop components/layout |

## 10. 验证计划（写代码前先写测试，测试集在这里定）

| 不变量 | 测试 |
|---|---|
| 字段名单只有一份：键顺序、PROPOSAL_CONTRACT、接口路径、DTO 区、STANDARD.md 都等于 TEMPLATE | 生成比对 + 前端不含字段名单的检查 |
| 已有定义读写不变：SwarmAI、General 和 9 个草稿的 revision 在步 1-3 前后不变 | 用真实文件做 fixture |
| 新建 Brain 立刻有骨架；骨架下 P6 FAIL；P3b 之后 PENDING；确认后 PASS | create_project + P6 测试 |
| 合并模式不丢人的修改：先 PATCH 改 summary，再重新起草（proposal 不含 summary），summary 还在 | draft 测试，revert 合并即红 |
| bind 新仓库后有 code-change 模式，判断字段不变 | bind 测试 |
| PATCH 只接受 edit/you 路径；改已确认的定义后变草稿；旧 revision 409；Hive 上 403 | review 测试 |
| 去掉 local-worker 后，validate 拒绝它，现有文件全部通过 | v2 测试 |
| 一次真实 Hive 派单在步 1-4 之后仍跑通（profile 照旧从定义来） | 手动端到端，一次 |

Review：步 1、3、4 做 correctness 审；步 3 的 PATCH 加 security 审（新写入口）；步 5 做 ux 审。

## 11. 要你定的

1. **profile 不能只做说明**：run 的 profile 在创建时定死，后面不会再选，Hive 上无人值守时只能从定义拿。
   所以我保留"每个模式开始时用哪个 profile"，参考区解释每个 profile 是什么。若你要它只做说明，
   得先做"EVALUATE 阶段重新选 profile"，那是 pipeline 的改动，另起设计。
2. **gates 放 Guardrails，不放 Capabilities**（它们是限制）。
3. **本地 pin、Hive 同步工具、Slack** 三件事这次不做，表单如实写现状。
4. 做法：跨前后端、改派单相关代码，按 §9 拆 5 步。用 pipeline 跑，还是我直接做？

## 12. 已知限制

- 骨架判断依赖"没有 summary 和 handles"这一结构特征；有人手写一个只有 summary 的定义也会被当成已起草。
- 表单只读呈现 MCP，不判断它在 Hive 上是否已配置（Hive 不报这个信息）。

## 13. r1 → r2：审查发现和处理

| # | 级别 | 发现 | 处理 |
|---|---|---|---|
| 1 | HIGH | "run 在 EVALUATE 选 profile"不成立：profile 在 create 时定死 | 保留每个模式的 profile；§11.1 交给你 |
| 2 | HIGH | 必填进 validate 会让骨架写不进去；不进则骨架让 P6 误判 ready | 必填不进 validate；P6 识别骨架为 FAIL（§6.2） |
| 3 | HIGH | 确认接口会把规范化后的形状写回磁盘；Hive 没有 schema 信号 | 不改存储格式（§2、§4） |
| 4 | MED | 多处把 schema ≠ 2 当 v1 处理 | 随 #3 消失 |
| 5 | MED | profile / local-worker / landing / drafted_at 的读者没列全；TraceTags 是死代码 | profile 保留；其余读者全部列进步 2；TraceTags 删除 |
| 6 | MED | 本地 pin 带不上、会悄悄丢、会锁住编辑 gates 的会话 | 本次不做，页面如实写（§2、§8） |
| 7 | MED | 同步 domain_tools：旧 Hive 拒收整包、声明不全、可编辑文件决定出机器 | 本次不做，页面如实写 |
| 8 | MED | `apply` 整份重建，会抹掉人的修改；建 Brain 时还没有仓库；有别的建 Brain 路径 | 合并模式 apply；bind 时补模式；两条建 Brain 路径都调骨架函数（§6） |
| 9 | LOW | brain_facts 重复现有函数；TEMPLATE 会变成又一份名单；§1 几处事实错 | 扩展 survey / notes；TEMPLATE 生成五处名单；§1 改正 |
