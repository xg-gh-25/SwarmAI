---
title: Project Agent 是替一个 Brain 干活的角色，定义是 Brain 给这个角色的约定，三个入口都生效
created: 2026-11-09
updated: 2026-11-09
project: SwarmAI
status: r3 已确认（2026-11-09）；已全部实现：规则写入、三个入口的送达、STEERING 去重（第 1 步），pipeline 记 mode、TEST 列检查、REPORT 标出每条检查（第 2 步，2026-11-10 收窄）
supersedes: docs/design/2026-11-09-project-rules.md（规则表 r2，方案 D：规则放进 STEERING）
---

# Project Agent：角色、约定、三个入口

> **r1 → r2**：一次零上下文审查发现，r1 的规则机制会自己坏掉。核实所依据的用户消息 90 天后会被清掉，规则跟着失效；核实失败会挡住派单；重新起草会删掉规则；聊天里走 `repo_guidance` 送达，会被 7,500 字的上限截掉。r2 收窄成三点：规则就是人的原话；只留一条送达路径；不做证据要求。
>
> **r2 → r3**：补上风险对策（§7）。规则在写入时核实一次，核实记录保存在 agent 写不到的地方。本机的约定块不列 setup，检查写成"按改动挑"。`_merge` 只保留 `rules`。CDF 用确定性比对去重。打包时默认不带规则。Hive 上的规则只从主机核实过的那份来。改动前已先盘点并清理了 12 份定义（§8 第 0 步）。

## 0. 定义（XG 2026-11-09 定）

- **Project Agent = 任何 agent 替一个 Brain 干活时扮演的角色。** 它不是常驻进程：谁接下这个 Brain 的活、对结果负责，谁就是它。
- **Project Agent 定义 = 这个 Brain 给这个角色的约定**，就是 `project-agent.yaml`，属于 Brain 的 ⑤ 交付段。它跟着 Brain 同步、记版本。打包分享给别人时，默认不带人的规则（§7 第 7 条）。
- **定义在三个入口都生效**：本机聊天进入这个 Brain、本地 pipeline（包括审查专家）、Hive 派单。

AgentSpaces 的 Project Agent 是每个项目一个常驻的编排者。我们不做常驻进程（已定"单 agent 切换角色，优于多 agent"），从它那里借三样东西：what/how 分工；每个项目一份小规则表；这个 Brain 的约定每个干活的 agent 都拿得到。

## 1. 现在的样子（2026-11-09 读代码，经审查核实）

| 入口 | 读不读定义 | 依据 |
|---|---|---|
| Hive 派单 | 读。任务文本包含：项目一句话、setup、validations、`Watch:`、skills、验收（Done when）、交回什么、保留动作、gates。Hive 上的会话按 pin 执行：mode、revision、gates 照 pin 来，保留动作直接拒绝。检查结果由 `task_checks` 按 id 记录，并显示在 Swarm Out 卡片上 | `task_spec.py:270`、`:455-508`；`project_agent_session.py:101-139`；`swarm_out_results.py:208-222` |
| 本机聊天 | **不读**。会话识别出项目后，注入一次 DDD 块：召回到的条目，或者一句指路加仓库位置。如果是通过绑定仓库识别的、又没召回到条目，什么都不注入 | `session_router.py:1154-1206`、`:1458-1474`、`:1987-1991` |
| 本地 pipeline | **不读**。`pipeline create` 只有 `--profile`；`knowledge.md` 是召回结果，开头标着"数据，不是指令"；只有 correctness 专家拿得到 PROJECT KNOWLEDGE | `cli_commands.py:140-148`；`run_knowledge.py:296-300`；`engine.py:3023-3030` |

## 2. 约定分三层，只有中间一层写进定义

| 层 | 内容 | 放在哪 |
|---|---|---|
| **产品层**（每个 Brain 一样） | what/how 分工；先写出能推翻结论的观察（SOUL P1）；证据靠记录产生；保留给人的动作；审查专家；人只在需求和验收两头出现 | SOUL / AGENT / 产品 gate，定义里不抄 |
| **Brain 层** | `identity.summary`、`intake`、`modes`（仓库、setup、validations、skills、gates）、`gates.enforce`、`watches`，这些已有；新增 **`rules`**（§4） | `project-agent.yaml` |
| **任务层** | Task Spec、run、这一次的检查结果和产出 | run 记录 |

`watches` 是这个 Brain 的工作长期要守、审查时要看的东西。它不是状态、悬而未决的问题、待办、市场判断，也不是产品已经给了每个会话的规则。2026-11-09 已经把这句写进起草说明和标准模板（`s_project-manager` SKILL.md、`templates/project-agents/STANDARD.md`）。

**这一版不做证据要求。** "结束前跑哪些检查"已经由 validations 表达，"做到什么算完"已经由任务的验收表达。按 mode 规定要附哪些文件（比如截图），目前没有一个用户，等用到时再加。

## 3. 三个入口怎么用

渲染只用现有的读取函数 `load_project_agent` / `_mode_view`（`project_agent.py:471-525`），新增一个函数 `render_contract(view, for_=...)`，按入口渲染成各自的文字。规则只取核实过的（§4.3）。

### 3.1 本机聊天
- **只用一条送达路径：DDD 块。** 会话识别出项目时（这个块每条消息都会重试，直到识别出来，见 `session_router.py:1159-1163`），把"Project Agent 约定"和 DDD 块一起送。绑定仓库那一支没召回到条目时，现在会直接返回（`:1987-1991`），改成照样送约定。不走 `repo_guidance`，它有 7,500 字的上限（`repo_guidance.py:54-55`）。
- **送的内容**：
  - 开场一句："你现在替 X 这个 Brain 干活，下面是它的约定，排在产品规则之后。"
  - 项目一句话。
  - 每个 mode 的 `when`、skills 和检查清单。检查清单的措辞是"这个 Brain 的检查，按这次改动挑相关的跑"，不写"结束前全部跑一遍"（§7 第 3 条）。agent 按 `when` 选一个 mode，用一句话说明选了哪个。
  - watches。
  - 核实过的规则，逐字列出原话。
  - gates 的名字。
  
  **不列 setup**。setup 是给一台全新机器装环境的命令，里面会用到 `$GITHUB_PATH` 这类只有 Hive 上的 `task_checks` 才模拟的变量（`task_checks.py:15-21`），在本机照着跑会出错。每条都整条显示，不截断。
- **不送的情况**：会话已经 pin 在一个 Project Agent 上，或者这台机器是 worker Hive（`is_worker()`）。这两种情况下任务文本就是约定。
- **压缩之后重送一次**，挂在现有的压缩后注入那一步（`post_compact_injection`）上。
- **gates 在本机不执行**，只列名字（§9 第 2 条）。保留动作照旧走产品的审批 gate，会问人，不会直接拒绝。

### 3.2 本地 pipeline
- **create 时记下来**：`pipeline create` 新增 `--agent-mode`，默认取定义里的 `default_mode`。不能叫 `mode`，因为 run 的 contract 里已经有 `mode: "isolated"`（`engine.py:543`）。create 时把规则、watches、所选 mode 和这个 mode 的检查（id 和命令）记进 run 受保护的 contract（`project_agent` 这一项）；之后各阶段只读这一份，和 `review.md` 按 run 的基线读是同一个道理。定义里没有这个 mode，或者 Brain 没有定义却给了 `--agent-mode`，create 直接拒绝，不建 run。旧 run 没有这一项，所有读的地方都把"没有"当作"没有约定"处理，不报错（§7 第 8 条）。Hive 上，规则和 mode 从 pin 里来（§3.3）。
- **执行的 agent**：不另写约定段。本地 pipeline 跑在聊天会话里，这个会话已经从聊天入口拿到了完整约定（§3.1）；`knowledge.md` 里再写一份是重复（2026-11-10 和 XG 一起收窄）。
- **审查专家**：每个专家的派单开头都带上规则和 watches，首轮和增量审查都有，不只 correctness。措辞是"这个 Brain 的人定的要求和审查重点，检查改动有没有违反"。（第 1 步已做）
- **TEST**：`next` 列出所选 mode 的检查，写成"按改动挑相关的跑"。报告里哪条命令跑的是哪条检查，由 agent 在这一行写 `"validation": "<id>"` 说明；不按命令文字比对，因为命令都包着时间上限、带着解释器路径，文字几乎不会相同。写的 id 不是这个 mode 的检查时，报告照收，stage-complete 的返回里给一条 warning。REPORT 现有的"## Tests"里，每条检查列一行：TEST 报告给它的结果，没写到的标"not run"。不另开一节；没有 TEST 阶段的 profile（research）不列。
- **都不阻塞落地**（按你定的"pipeline 的 DELIVER/REFLECT 要顺滑"）。

### 3.3 Hive 派单
- 任务文本加 `Rules:`，逐字列出**主机上核实过的**原话，注明"人对这个 Brain 的要求，排在产品规则之后"。
- 派单请求里的 `project_agent` 一并带上这份规则，Hive 的 pin 记下来；Hive 上的 pipeline run 由 `create_pinned_run` 创建时从 pin 取规则，不读同步过去的 YAML（Hive 上没有核实记录，§7 第 6 条）。
- 结尾那句"以上规则由 Project Agent 记录强制执行"（`task_spec.py:506-507`）改成分开说：保留动作和 gates 是强制执行的；rules 是要遵守的要求。

## 4. 规则

### 4.1 是什么
规则是**人亲口说的、对这个 Brain 的每个任务都成立的要求**。

以下都不算规则：
- 产品规则；
- 对所有项目都成立的要求，那是 STEERING 的；
- 仓库自己的规矩，那是仓库 AGENTS.md 的；
- 项目的决定和教训，那是 DDD 的。

### 4.2 形状
```yaml
rules:
- said: 发布说明要放仓库里 不要有内部信息
  added: 2026-11-09
```
送出去的就是原话 `said`，不另写改述。规则永远不会成为定义的"问题"，不进 `validate` / `dispatch_problems`，也就不会挡住派单。

### 4.3 写入：核实一次，记在 agent 写不到的地方
- **只通过一个命令写**：`"$SWARM_BACKEND_BIN" py -m scripts.project_agent_rule add --brain <B> --said "<原话>"`（实现时定的名字）。命令做这几件事：
  1. 取这一轮人的消息：和 `ingest submit --evidence last-user` 一样，用 `session_inputs.latest_user_event_id`（`desktop_main.py:342-344`）。
  2. 核实原话是这条消息里完整的一句或连续几句（只在句号、问号、叹号、换行处断句；用 CDF 的归一化），归一化后至少 6 个字。截一句里的一部分会丢掉"不要""never"，意思反过来，所以不接受。
  3. 写进 YAML。
  4. 往核实记录里追加一行：Brain、原话的哈希、event id、时间。
  
  对话里回一句"已加到 X 的规则：『…』"。
- **核实记录**放在 `state/project_agent_rules/`。像 `state/pipeline_agent_audit` 一样，由 audit 写入 gate 拒绝 agent 写入、移动、删除（`hook_builder.py:392-402`，把这个目录加进它保护的路径）。这和 pipeline 审计日志是同一个防护等级：防的是 agent 出错或被诱导，不是防同一用户的其他进程。
- **只核实一次，读的时候不再核实**。读的地方只送"YAML 里有、核实记录里也有"的规则。用户消息 90 天后被清掉不影响，换机器也不影响，Hive 用的是主机发过去的那份。
- **核实不了就不写，并说明原因**：这一轮没有人的消息（频道消息让 `latest_user_event_id` 返回 None，`session_inputs.py:234-250`）；原话在消息里找不到；不到 6 个字。预热后被领用的会话没有 `SWARM_APP_SESSION_ID`：这个命令和 pipeline 命令一样，由 `run_owner_gate` 在调用前补上会话 id（`run_owner_gate.py:15-20`，加进它的命令名单）。
- **拿掉**：`… scripts.project_agent_rule remove --brain <B> --said "<原话>"`，也要在人自己开启的这一轮里，而且这一轮的消息里要有整条规则：这一轮读到的网页拿不掉人的规则。
- **防的是什么**：防 agent 出错（记了人没说的话，或者只截了一部分）。和 agent 执行的其他命令（pipeline、`ingest submit`）一样，这些检查在 agent 自己的进程里做，不防一个专门想绕过的 agent：用变量拼出会话 id 的名字、按路径跑脚本、`py -c` 直接调用，都看不见。这是全产品的已知限制（S6-3），在 REPORT 里列为剩余风险。XG 2026-11-09 选定这个方向（方案 A），不做人工确认。
- **手改 YAML 加的规则**不会送出去，Brain Hub 上显示成"未核实"的警告，不算问题。
- **重新起草时保留规则**：`project_agent_draft._merge` 只额外保留 `rules` 这一个键（`project_agent_draft.py:582-650`；每次绑定仓库都会重新起草，`artifact_cli.py:388`），不保留其他不认识的键，以免 11-01 退役的 `status`、`placements` 这类字段又回来。配一个测试。
- **和 CDF 的边界**（送达那次 run 一起做：规则送达之前，STEERING 是唯一能送到每个会话的路，提前去重会让规则两头落空）：
  - 抽取提示（`s7_review.py:355-365` 的 `_WHERE`）加一句："人只对一个 Brain 提的长期要求，在会话里用 `project-agent rule add` 记；记过的不再作为 user 范围提交。"
  - 再加一道确定性的去重：CDF 写 STEERING 之前，如果这个单元的 `user_quote` 和某条核实过的 Brain 规则原话相同，就不写，原因记为"已经是 Brain 规则"。
  - 会话里漏记的，CDF 照旧写进 STEERING，不会丢。
- **已有的那条**："SwarmAI 的 pipeline DELIVER/REFLECT 要顺滑"留在 STEERING。它当初是你在会话里说的，那条消息已经找不回来，按 §4.3 核实不了。等你哪天再说一次，再用命令记进 SwarmAI 的规则；STEERING 里那条由你在界面上撤回。

### 4.4 展示
Brain Hub 的 Project Agent 页最上面列出规则：原话、日期、是否已核实。只读；要拿掉，在对话里说。

## 5. 谁在扮演 Project Agent

| 场景 | 谁 | 怎么拿到约定 |
|---|---|---|
| 你在聊天里让我做某个 Brain 的事 | 聊天主 agent | §3.1 的块 |
| 本地 pipeline run | 执行 run 的会话（审查专家只审） | §3.2 记下的约定 |
| Swarm Run 把子任务派到 Hive | lead run 所在的会话 | §3.1 的块；派单时按 §3.3 把约定交给 worker |
| Hive 上执行一单 | worker，不是 Project Agent | 任务文本和 pin |

## 6. 不做
- 常驻的编排进程；跨项目的 Today 视图（"任务成为一等对象"另做）。
- 证据要求（§2）；规则分层；从纠正里自动提议规则；审批流程。
- 在本机执行 Brain gate；任何新的阻塞检查。

## 7. 风险和对策

| # | 风险 | 对策 | 落在哪 |
|---|---|---|---|
| 1 | **规则成了长期注入口**：agent 读到外部内容，被诱导写下"用户说以后都要 X"，假规则就会进入每个会话、每个专家、每一单 | 写入时核实一次：原话必须是这一轮人的消息里完整的一句或几句；核实记录放在 agent 写不到的地方；只送核实过的规则；送出去的只有原话 | §4.3 |
| 2 | **定义里的错被放大到每个本机会话**：今天只有 Hive 读定义，起草出来的错一直没人发现 | 第 0 步已完成：12 份定义盘点清理，watches 从 58 条减到 41 条，起草说明和标准模板写明 watches 不是什么。第 1 步上线前再盘点一次 | §8 第 0 步 |
| 3 | **本机被诱导跑大量检查**：SwarmAI code-change 有 11 条检查，KiroCrew 有 10 条 | 本机写成"按改动挑相关的跑"，不列 setup；pipeline 的 TEST 和 Hive 才是"结束前要跑" | §3.1 |
| 4 | **token 和首 token 时间增加** | 不截断、不设上限（R23）。第 1 步前后各测一次：每个 Brain 的约定字数，SwarmAI 会话的提示长度和首 token 时间；明显变大时精简定义本身 | §9 验收 7 |
| 5 | **重新起草带回退役字段，或删掉规则** | `_merge` 只额外保留 `rules` | §4.3 |
| 6 | **Hive 上规则全部"未核实"**：Hive 没有主机的核实记录 | Hive 的规则只从派单带过去的那份来，记进 pin；不读同步过去的 YAML | §3.3 |
| 7 | **人的规则随 Brain 分享出去**，可能带内部信息 | `ddd_packager` 打包时默认不带 `rules` | §0、§8 第 1 步 |
| 8 | **改 pipeline contract 弄坏已有的 run** | 约定是新加的文件，所有读的地方把"没有"当"没有约定"；为旧 run 写测试 | §3.2 |
| 9 | **同一条规则同时存在两个地方** | 抽取提示加一句，CDF 写 STEERING 前做确定性去重 | §4.3 |
| 10 | **改规则会换 revision**，已派出、Hive 还没开始执行的那一单启动失败 | 接受。很少见，失败信息会说明，重派即可 | — |
| 11 | **本机不执行 Brain gate**，写 gate 的人以为它到处生效 | 本机的约定块写明"gates 只在派出去的会话里执行"；目前唯一的例子 `no_git_push` 在本机已有产品的 push 审批 | §3.1 |
| 12 | **agent 写的定义文字进入每个会话**（第 1 步安全审查 CF-2f35086b）：summary、mode 的 when、检查命令、watches 都是 agent 写的，被诱导写进去的一行指令或一条命令会被后来的会话当成 Brain 的约定 | 已接受。定义的标准部分由 host agent 根据 Brain 的知识生成和维护，和 DDD 文档一样不经人审（XG 2026-11-01 删掉确认步骤，2026-11-10 再次确认：只有自定义部分里的规则要人的原话，以后如果做自定义 gate，再给它加人审）。已做的：所有定义文字送进提示前压成一行，伪造不了提示里的段落 | §3.1、§4 |

## 8. 分步落地

| 步 | 内容 | 主要改动 |
|---|---|---|
| 0（已完成 11-09） | 盘点并清理 12 份定义，补强 watches 的起草说明 | 各 Brain 的 `project-agent.yaml`；`s_project-manager` SKILL.md；`STANDARD.md` |
| 1 | `rules` 字段；`rule add/remove` 命令、核实记录和 gate 保护；`_merge` 保留；`render_contract`；本机聊天的约定（DDD 块、两种不送、压缩后重送）；Hive 的 `Rules:`、pin 带规则、结尾句；Brain Hub 规则列表；CDF 抽取提示和去重；打包不带规则 | `project_agent.py`、新命令、`run_owner_gate.py`、`hook_builder.py`（gate 路径）、`project_agent_draft.py`、`session_router.py`、压缩后注入、`task_spec.py`、`swarm_out.py`、`project_agent_session.py`、`s7_review.py`、`pipeline_v2.py`、`ddd_packager.py`、`ProjectAgentView.tsx` |
| 2（已完成 11-10） | pipeline：`--agent-mode` 记进 contract；TEST 列出这个 mode 的检查；报告行用 `validation` 写明跑的是哪条检查；REPORT 的 Tests 列出每条检查的结果或 not run。所有专家的约定段已在第 1 步做了；`knowledge.md` 约定段不做（§3.2） | `project_agent.py`、`cli_commands.py`、`engine.py`、`validation_report.py`、`events.py`、`next_step.py`、`audit.py`、`project_agent_session.py` |

两步各走一次 pipeline。第 1 步改动面大：命令、gate、CDF、注入、派单、前端都在内。如果太大，可以再拆成"规则写入和核实"和"三个入口送达"两次 run。

## 9. 验收（端到端）
1. 在聊天里说"在 SwarmAI 里，以后 X"：agent 回"已加到规则"；Brain Hub 显示这条原话、已核实；会话结束后 STEERING 里没有这一条。
2. 让 agent 根据一段网页内容加一条规则，而原话不在这一轮你的消息里：命令拒绝并说明原因。手改 YAML 加的规则，三个入口都看不到，Hub 显示"未核实"。
3. 新会话里第一条和 SwarmAI 相关的消息带着约定，里面有 X，没有 setup，检查清单写着"按改动挑"。只改 swarmai 仓库文件、没有召回到任何条目的会话，也带着约定。
4. Hive worker 上，以及 pin 在 Project Agent 上的会话里，不出现本机的约定块；Hive 上的 pipeline run 里有 X（来自 pin）。
5. 压缩后的那一轮，约定重新出现。重新起草后，`rules` 还在，退役字段没有回来。
6. 一个 SwarmAI pipeline run：contract 里记着 mode 和它的检查；每个专家的派单里都有 X；TEST 的 `next` 列出检查；REPORT 的 Tests 列出每条检查的结果和 not run 的项，不挡落地。旧 run 照常能走完。
7. 测量：每个 Brain 的约定字数，SwarmAI 会话的提示长度和首 token 时间，上线前后各一次，写进 REPORT。
8. 只生成派单文本、不真派：有 `Rules:` 和 X；另一个 Brain 的派单里没有。打包 SwarmAI Brain，包里没有 `rules`。

## 10. 验证计划
- **测试**（每条去掉实现都会变红）：
  - `rule add`：通过；原话不在消息里；没有人的消息；不到 6 个字；没有会话 id 时由 gate 补上。
  - 核实记录目录受 gate 保护：写入、移动、删除都被拒绝。
  - 只送核实过的规则。
  - `_merge` 只保留 `rules`。
  - 聊天约定：三种识别方式都送；pin 和 worker 不送；只送一次；压缩后重送；不含 setup。
  - `task_spec` 的 `Rules:`、pin 带规则、改写后的结尾句。
  - CDF 去重。
  - 打包不带 `rules`。
  - 第 2 步：`--agent-mode` 默认值、拒绝不存在的 mode；mode 和检查写进 contract；Hive 的 pin 带 mode；TEST 列出检查；报告行的 `validation` 记进事件，不认识的 id 只是 warning；REPORT 的检查行和 not run；旧 run 和 research run 不列。
- **审查**：两步都跑 correctness 和 security。约定会进每个会话、每个专家、每一单，是注入面；第 1 步还动了 gate 和 CDF 的准入。
- **最终测试集**：上面新增的测试，加上这些现有测试：`test_project_agent*`、`test_task_spec*`、`test_run_owner_gate*`、audit 写入 gate、DDD 注入、压缩后注入、CDF `pipeline_v2`、`ddd_packager`，以及 run_protocol 的 create、派单、REPORT。

## 11. 要 XG 定
1. **规则在写入时核实一次**：原话必须出现在这一轮你的消息里；核实记录放在受 gate 保护的目录；只送核实过的规则。可以吗？
2. **Brain gate 只在派出去的会话里执行**，本机只列名字。可以吗？在本机执行会挡住直接编码，和产品规则"gate 不挡本机编码"冲突。
3. **证据要求先不做**，用到时再加。可以吗？
4. **STEERING 里那条"DELIVER/REFLECT 要顺滑"先留在 STEERING**，等你再说一次时记进 SwarmAI 的规则。可以吗？
