---
title: Project Agent 是替一个 Brain 干活的角色，定义是 Brain 给这个角色的约定，三个入口都生效
created: 2026-11-09
updated: 2026-11-09
project: SwarmAI
status: 草案 r2，待 XG 确认
supersedes: docs/design/2026-11-09-project-rules.md（规则表 r2，方案 D：规则放进 STEERING）
---

# Project Agent：角色、约定、三个入口

> r1 → r2：一次零上下文审查发现 r1 的规则机制会自己坏掉。核实依据的用户消息 90 天就被清掉，规则会跟着失效；核实失败会挡住派单；重新起草会删掉规则；聊天里的送达走 `repo_guidance` 会被 7,500 字的上限截掉。r1 对 3–4 条规则来说也太重。r2 收窄成：规则就是人的原话，写在定义里；只有一条送达路径；证据要求不做。

## 0. 定义（XG 2026-11-09 定）

- **Project Agent = 任何 agent 替一个 Brain 干活时扮演的角色。** 它不是常驻进程：谁接下这个 Brain 的活、对结果负责，谁就是它。
- **Project Agent 定义 = 这个 Brain 给这个角色的约定**，即 `project-agent.yaml`。它属于 Brain 的 ⑤ 交付段，跟着 Brain 同步、打包、记版本。
- **定义在三个入口都生效**：本机聊天进入这个 Brain、本地 pipeline（包括审查专家）、Hive 派单。

AgentSpaces 的 Project Agent 是每个项目一个常驻的编排者。我们不做常驻进程（已定的"单 agent 切换角色，优于多 agent"），借的是三样：what/how 分工、每个项目一份小规则表、"这个 Brain 的约定每个干活的人都拿到"。

## 1. 现在的样子（2026-11-09 读代码，经审查核实）

| 入口 | 读不读定义 | 依据 |
|---|---|---|
| Hive 派单 | 读。任务文本包含项目一句话、setup、validations、`Watch:`、skills、验收（Done when）、交回什么、保留动作和 gates。Hive 上的会话按 pin 执行：mode、revision、gates，保留动作直接拒绝。检查结果由 `task_checks` 按 id 记录，在 Swarm Out 卡片上显示 | `task_spec.py:270`、`:455-508`；`project_agent_session.py:101-139`；`swarm_out_results.py:208-222` |
| 本机聊天 | **不读**。会话识别出项目后注入一次 DDD 块：召回的条目，或者一句指路加仓库位置。如果是通过绑定仓库识别出的项目、又没有召回到条目，就什么都不注入 | `session_router.py:1154-1206`、`:1458-1474`、`:1987-1991` |
| 本地 pipeline | **不读**。`pipeline create` 只有 `--profile`；`knowledge.md` 是召回结果，开头写着"数据，不是指令"；只有 correctness 专家拿到 PROJECT KNOWLEDGE | `cli_commands.py:140-148`；`run_knowledge.py:296-300`；`engine.py:3023-3030` |

## 2. 约定分三层，只有中间一层写进定义

| 层 | 内容 | 放在哪 |
|---|---|---|
| **产品层**（每个 Brain 一样） | what/how 分工；先写出能推翻结论的观察（SOUL P1）；证据靠记录产生；保留给人的动作；审查专家；人只在需求和验收两头出现 | SOUL / AGENT / 产品 gate，定义里不抄 |
| **Brain 层** | `identity.summary`、`intake`、`modes`（仓库、setup、validations、skills、gates）、`gates.enforce`、`watches`（已有），加上 **`rules`**（新增，§4） | `project-agent.yaml` |
| **任务层** | Task Spec、run、这一次的检查结果和产出 | run 记录 |

**证据要求**这一版不做。"结束前跑哪些检查"已经由 validations 表达（Hive 上按 id 记录结果），"做到什么算完"已经由任务的验收表达。按 mode 规定要附的文件（比如截图）目前没有一个用户，等用到时再加。

## 3. 三个入口怎么用

渲染只用现有的读取函数：`load_project_agent` 和 `_mode_view`（`project_agent.py:471-525`），再加一个函数 `render_contract(view, for_=...)`，按入口把它渲染成文字。

### 3.1 本机聊天
- **只用一条送达路径：DDD 块。** 会话识别出项目后（这个块每条消息都会重新尝试识别，直到识别出来，`session_router.py:1159-1163`），把"Project Agent 约定"和 DDD 块一起送。绑定仓库那一支没有召回到条目时现在会直接返回（`:1987-1991`），改成照样送约定。`repo_guidance` 不参与，它有 7,500 字的上限（`repo_guidance.py:54-55`）。
- **内容**：
  - 一句开场："你现在替 X 这个 Brain 干活，下面是它的约定，排在产品规则之后。"
  - 项目一句话；
  - 每个 mode 的 `when`、validations、skills。agent 按 `when` 选一个 mode，并用一句话说明选了哪个；
  - watches；
  - rules，逐字列出人的原话；
  - gates 的名字。
  每条都整条显示，不截断。
- **不送的情况**：会话已经 pin 在某个 Project Agent 上，或者这台机器是 worker Hive（`is_worker()`）。这两种情况下任务文本是唯一的约定，再送一份会重复，而且会让 agent 在已经钉好的 mode 之外再选一次。
- **压缩之后重新送**：上下文压缩会把这个块压进摘要。压缩后的那一轮重新送一次，挂在现有的压缩后注入那一步上（`post_compact_injection`）。
- **gates 在本机不执行**，只列出名字（§8 第 2 条）。保留动作照旧走产品的审批 gate：会问人，不会直接拒绝。

### 3.2 本地 pipeline
- **create 时记下来**：`pipeline create` 加 `--agent-mode`，默认是定义里的 `default_mode`。不能叫 `mode`，因为 run 的 contract 里已经有 `mode: "isolated"`（`engine.py:543`）。create 时把渲染好的约定写成这个 run 的一份受保护文件，和 `knowledge.md` 同一类，并记下定义的 revision。之后各阶段都读这一份，不再读工作区里随时会变的定义。这和 `review.md` 要按 run 的基线读是同一个道理（`engine.py:3175`）。Hive 上的 pin 已经有 mode，`create_pinned_run` 把它带进来。
- **执行的 agent**：`knowledge.md` 在"数据，不是指令"那行标题**之前**加一段约定。约定里每条带 `item_id`，并写进 contract 的 knowledge items，REQUIREMENTS 和 DESIGN_SPEC 才能引用（RQ-5/DS-5，`engine.py:2726-2750`）。
- **审查专家**：每个专家的派单都加上 rules 和 watches，不只 correctness。措辞是"这个 Brain 的人定的要求和审查重点，检查改动有没有违反"。
- **TEST**：`next` 列出所选 mode 的 validations。REPORT 现有的"## Tests"里，凡是和某条 validation 命令相同的行就标上它的 id；没有跑的 validation 列成一行"未跑"。不新开一节。
- **不阻塞落地**。这遵守你定的"pipeline 的 DELIVER/REFLECT 要顺滑，不加新的阻塞检查"。

### 3.3 Hive 派单
- 在任务文本里加一段 `Rules:`，逐字列出人的原话，并注明"人对这个 Brain 的要求，排在产品规则之后"。
- 结尾那句"以上规则由 Project Agent 记录强制执行"（`task_spec.py:506-507`）改成分开说：保留动作和 gates 是强制执行的，rules 是要遵守的要求。
- 规则写在定义里，所以改规则会换 revision，派单时照常比对。

## 4. 规则

### 4.1 是什么
规则就是**人亲口说的、对这个 Brain 的每个任务都成立的要求**。

下面这些不算规则：
- 产品规则；
- 对所有项目都成立的要求，那是 STEERING 的；
- 仓库自己的规矩，那是仓库 AGENTS.md 的，随仓库走；
- 项目的决定和教训，那是 DDD 的，按需召回。

### 4.2 形状：原话就是规则
```yaml
rules:
- said: 发布说明要放仓库里 不要有内部信息
  added: 2026-11-09
```
- 送到三个入口的就是 `said` 这句原话，不另写一句改述。送出去的只有人说的话。
- 没有 id、没有 event、不做二次核实。消息会过期，换一台机器 event id 也对不上，二次核实反而会让规则自己失效。
- 至少 6 个字符。
- 规则永远不会成为定义的"问题"，也就不会进 `validate`/`dispatch_problems`，不会挡住派单。

### 4.3 怎么写进去、怎么拿掉
- **沿用定义现在的维护方式**（2026-11-01：人说一句，agent 改，改完告知）。你在对话里对某个 Brain 说出一条长期要求，agent 就把原话加进 `rules`，在对话里回一句"已加到 X 的规则：『…』"。你说"去掉这条"，agent 就删掉，同样回一句。
- **看得见就是保障**。agent 能不能乱写一条？靠两样防：每次改动都在对话里回显；Brain Hub 的 Project Agent 页在最上面列出全部规则（只读）。这和定义里其他字段的保障方式一样。
- **重新起草时保留**：`project_agent_draft._merge` 改成保留旧定义里起草不管的顶层键，`rules` 在内，并配一个测试（`project_agent_draft.py:582-650`）。现在每次绑定仓库都会重新起草（`artifact_cli.py:388`），不改的话规则会被删掉。
- **和 CDF 的边界**：CDF 抽取提示（`s7_review.py:355-365` 的 `_WHERE`）加一句："人只对一个 Brain 提的长期要求，agent 在会话里加到那个 Brain 的规则；已经加了的，不再作为 user 范围提交。" 会话里漏加的，CDF 照旧把它写进 STEERING，不会丢。
- **已有的那条**：STEERING 里"SwarmAI 的 pipeline DELIVER/REFLECT 要顺滑"挪进 SwarmAI 的 `rules`。STEERING 里那条要你在界面上撤回，撤回只能由人做。

## 5. 谁在扮演 Project Agent

| 场景 | 谁 | 拿到约定的方式 |
|---|---|---|
| 你在聊天里让我做某个 Brain 的事 | 聊天主 agent | §3.1 的块 |
| 本地 pipeline run | 执行 run 的会话；审查专家只审 | §3.2 记下的约定 |
| Swarm Run 把子任务派到 Hive | lead run 所在的会话 | §3.1 的块；派单时用 §3.3 把约定交给 worker |
| Hive 上执行一单 | worker，不是 Project Agent | 任务文本 |

## 6. 不做
- 常驻的编排进程；跨项目的 Today 视图（"任务成为一等对象"另做）。
- 证据要求（§2）；规则分层；从纠正里自动提议规则；审批；二次核实。
- 在本机执行 Brain gate；任何新的阻塞检查。

## 7. 分步落地

| 步 | 内容 | 主要改动 |
|---|---|---|
| 1 | `rules` 字段；`_merge` 保留；`render_contract`；本机聊天的约定（DDD 块、不送的两种情况、压缩后重送）；Hive 的 `Rules:` 和结尾句；Brain Hub 规则列表；CDF 抽取提示加一句；迁移那一条规则 | `project_agent.py`、`project_agent_draft.py`、`session_router.py`、压缩后注入、`task_spec.py`、`s7_review.py`、`ProjectAgentView.tsx` |
| 2 | pipeline 的 `--agent-mode`、约定文件、`knowledge.md` 的约定段和 ids、所有专家的约定段、TEST 的 validations 和 Tests 标注 | `cli_commands.py`、`engine.py`、`run_knowledge.py`、REPORT 渲染 |

第 1 步跨后端几个模块和前端，第 2 步改 pipeline 的 contract。两步各走一次 pipeline。

## 8. 验收（端到端）
1. 在聊天里说"在 SwarmAI 里，以后 X"：agent 回一句"已加到规则"；Brain Hub 能看到这条原话；会话结束后 STEERING 里没有这一条。
2. 新会话里第一条和 SwarmAI 相关的消息带着约定，里面有 X。会话只改了 swarmai 仓库里的文件、没有召回到任何条目时，也带着约定。
3. 在 Hive 上（worker），或者会话 pin 在某个 Project Agent 上时，不出现本机的约定块。
4. 压缩之后的那一轮，约定重新出现。
5. 绑定一个新仓库、触发重新起草后，`rules` 还在。
6. 一个 SwarmAI 的 pipeline run：`knowledge.md` 开头有约定，REQUIREMENTS 能引用它的 id；每个专家的派单都有 X；REPORT 的 Tests 标出 validation 的 id 和没跑的那些。
7. 只生成派单文本、不真派：有 `Rules:` 和 X；另一个 Brain 的派单里没有 X。

## 9. 验证计划
- **测试**（每条去掉实现后会变红）：
  - `rules` 的读取，以及长度不足的处理：跳过并给 Hub 一个警告，不报"问题"；
  - `_merge` 保留 `rules` 和其他起草不管的顶层键；
  - 聊天约定：三种识别方式都会送；pin 和 worker 不送；只送一次；压缩后重送；
  - `task_spec` 的 `Rules:` 和改写后的结尾句；
  - 第 2 步：`--agent-mode` 默认值、约定文件的 ids 写进 contract、每个专家派单都有约定、Tests 的标注。
- **审查**：两步都跑 correctness 和 security。约定会进每个会话、每个专家和每一单 Hive 任务，是注入面。
- **最终测试集**：上面新增的测试，加上 `test_project_agent*`、`test_task_spec*`、DDD 注入、压缩后注入、run_protocol 的 create、派单和 REPORT 的现有测试。

## 10. 要 XG 定
1. 规则 = 你的原话，agent 记、在对话里回显、Hub 上能看到；不做机器核实（核实依据的消息 90 天就会被清掉）。可以吗？
2. Brain gate 只在没人盯着的会话里执行（和今天一样），本机聊天里只列出名字：可以吗？在本机执行会挡住直接编码，违反产品规则"gate 不挡本机编码"。
3. 证据要求先不做，用到时再加：可以吗？
