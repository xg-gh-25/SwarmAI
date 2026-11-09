---
title: 项目规则表：你对一个项目的长期要求送到这个项目的每个任务
created: 2026-11-09
updated: 2026-11-09
project: SwarmAI
status: 已被 docs/design/2026-11-09-project-agent-contract.md 取代（2026-11-09）
---

# 项目规则表（Project Rules）

（r1 提出把规则放进 DDD 的一个新段落；一次零上下文审查发现它要在 CDF 里重造 STEERING 已有的整套门槛，而且门槛按 store 判断、不按段落，原设计根本挡不住 agent 自己的总结。r2 改为复用 STEERING。r1 的方案作为 §3 的 B 保留，供比较。）

## 1. 要解决什么

你对一个项目说过的长期要求（"在 SwarmAI 里，发布说明放仓库里，不带内部信息"），要在这个项目的**每个任务**里送到干活的 agent 手里：本机聊天、pipeline 的审查专家、派到 Hive 的任务。

**先看现在已经做到了多少**（读代码确认）：
- CDF 抽取时，"用户自己的规则"声明为 `scope: user`（`s7_review.py:361` 的 `_WHERE`），写进 STEERING 的系统附录。准入要你的原话：`user_quote` 在你的消息里逐字找到，并且 `user_check.says` 判定这句话确实说了这条规则（`pipeline_v2.py:750-766`）。覆盖一条以你原话为依据的条目，也要你的话确认（`overrides.py:261-272` 的 `weaker_than_target` 和 `ask_user`）。
- STEERING 每轮整份注入，所以**本机聊天已经做到**。现在 STEERING 里就有一条只对 SwarmAI 成立的规则（"Pipeline DELIVER and REFLECT should stay smooth… Applies when: … in SwarmAI"），它在每个会话里都在。
- **缺的只有两处**：pipeline 的审查专家是独立的子 agent，拿不到 STEERING；Hive 上的任务也拿不到你的 STEERING，它只看派单时的任务文本（`task_spec.py`，目前只有 `Watch:`）。另外，STEERING 里的条目没有结构化地写明属于哪个项目，所以没法只挑出某个项目的规则。

**数据**（2026-10-01 以来 CDF 的全部记录）：项目范围、带你原话的长期要求 5 周 8 条，其中真正"每个任务都该照做"的 3–4 条；项目范围的重复纠正 0 条。所以要做小：补上缺的两处，不造新的知识通道，也不做"纠正两次就提议规则"。

## 2. 规则是什么

规则 = **你亲口说的、对某个项目的每个任务都成立的要求**。它就是 STEERING 里的一条，只是多标了它属于哪个项目。

| 东西 | 家 | 和规则的关系 |
|---|---|---|
| 产品规则（对所有用户成立） | SOUL / AGENT / SECURITY-BASELINE | 规则排在它们后面 |
| 你在所有项目里的要求 | STEERING（不标项目） | 同一个文件，不标项目 |
| **你对某个项目的要求** | **STEERING，标项目** | **就是规则表** |
| 仓库自己的规矩 | 仓库的 AGENTS.md / `.swarmai/review.md` | 不放，`repo_guidance` 已经注入 |
| 项目的决定、架构、教训 | DDD，按需召回 | 不放 |
| 审查重点（watches） | `project-agent.yaml`，建 Brain 时起草 | 不放，来源不同，不合并 |

## 3. 方案比较

| | **D. STEERING 条目标项目（推荐）** | B. DDD 新段落（r1） | A. `project-agent.yaml` 加 `rules:` |
|---|---|---|---|
| 准入（必须是你的原话） | 已有 | 要新加按段落的判断（`_admitted` 现在只按 store，`pipeline_v2.py:139`） | CDF 外的第二个入口，或新写一个 YAML 写入器 |
| 不合格时退到哪 | 已有：退回项目 DDD（`_home`） | `_home` 会把单元送回同一段（`topic` 还是 rule），要改 | — |
| 覆盖要你确认 | 已有（全局目标的检查） | 本地 DDD 目标没有这道检查（`overrides.py:275-300`），要加 | — |
| 段落从哪来 | 已有（系统附录由写入器建） | 写入器不给 DDD 建段落（`writer.py:250-257`），老 Brain 要迁移 | — |
| 本机聊天 | 已有（每轮注入） | 要新加注入，且 DDD 块每会话只发一次，绑定仓库的分支不发（`session_router.py:1987-1991`） | 要新加 |
| CDF 改动 | 抽取提示加一句、单元加一个可选字段、写入器把项目写进标记 | `Topic` 两处、提示、`_DDD_MATRIX`、骨架模板、`ddd_skeleton`、`ddd_format`、写入器、准入、`_home`、覆盖，约 10 处 | 新写入器 |
| 跟着 Brain 走（打包分享给别人） | **不跟**：规则在你的 STEERING 里 | 跟 | 跟 |
| 在别的项目里也注入 | 是（每条带"仅适用于 X"，量很小） | 否 | 否 |

推荐 **D**：缺的两处补上就闭环，CDF 里的门槛一处都不用重造。**放弃的是"规则跟着 Brain 走"**：你把一个 Brain 分享给团队时，你的规则不会跟过去；团队都该遵守的规矩，应写进仓库的 AGENTS.md（它随仓库走）。三个读的地方都只调一个函数 `project_rules(project)`，以后真要让规则跟 Brain 走，换存放处不用改读的地方。

## 4. 设计（D）

### 4.1 写入：CDF 一个可选字段
- `scope: user` 的单元可以多声明一个 `project: <项目名>`（必须是已有项目，用现有的 `_resolve_project` 解析；找不到就当没写）。
- 抽取提示（`s7_review.py` 的 `_WHERE`，以及 s_persist SKILL.md 说 scope 的那段）加一句：用户只对一个项目提的长期要求，scope 仍是 user，加上 `project`。
- 写入器把它写进条目行末已有的标记里：`<!-- cand:<id> | project:<名> -->`。这个标记格式已经用在 common-DDD 的 `project_hint` 上（`writer.py` 的 `_render_ddd`），元数据注释本身不动（它的字段顺序是固定的）。
- 准入、`user_check`、覆盖确认、撤回全部照旧，因为它就是一条 STEERING 条目。

### 4.2 读：一个函数
`project_rules(project) -> list[Rule]`：读 STEERING 系统附录里标了这个项目的条目，每条整条返回（id、标题、正文），不截断、不设条数上限。只读 CDF 写入、带 `cand:` 标记的条目，不读你手写的部分（手写部分没有项目标记）。

### 4.3 补上的两处
1. **审查专家**：每个专家的派单里加一段"你对这个项目的规则"（用 `project_rules`），**所有专家都有**，不只是 correctness（现在 `engine.py:3023` 只给 correctness 项目知识）。措辞是"用户对这个项目的要求，审查时检查改动是否违反"，不放进标着"数据，不是指令"的 PROJECT KNOWLEDGE 段（`run_knowledge.py:296`、`:389`）。
2. **Hive 派单**：`task_spec` 在主机上生成任务文本时（`task_spec.py:485` 附近）加 `Rules:` 一段。任务文本派单时就固定了，所以规则也固定，不进 revision（`project_agent.py:409`）。任务文本结尾那句"以上规则由 Project Agent 记录强制执行"（`task_spec.py:506-507`）对这一段不成立，这一段单独说明："你对这个项目的要求，优先级低于产品规则"。

本机聊天不改：STEERING 已经每轮整份注入。

### 4.4 展示和删除
- Brain Hub 的 Project Agent 页面（`ProjectAgentView.tsx:92-96`）加一个"项目规则"列表，`load_project_agent`（`project_agent.py:485`）返回里加 `rules`。
- 每条旁边"移除"，调现有的用户专属撤回（`routers/ingestion_redact.py:35`）。在聊天里说"改成 Y"走 CDF 覆盖，现有检查会确认是你的话。
- 不新增看板。

### 4.5 已有的条目
STEERING 里现在那条"SwarmAI pipeline 的 DELIVER/REFLECT 要顺滑"没有项目标记，所以 `project_rules` 读不到它（本机聊天照样有）。实现时把它补上 `project:SwarmAI`：只改这一条的标记，先备份、改完对比差异。

## 5. 不做
- 规则跟着 Brain 走（见 §3 放弃的部分）。
- 分层、按 mode 区分、导入导出、agent 提议加审批。
- 把规则写进 pipeline 的 `knowledge.md`：执行的 agent 本机有 STEERING，Hive 上有任务文本，再写一份是重复。
- watches 清理：SwarmAI 的 6 条 watches 有 4 条是在抄产品规则（来源是 `backend/context/*`）。这是 SwarmAI 这一个 Brain 的数据问题，"来源是产品文件"在别的仓库里没有通用的定义（P7），所以不改起草代码，实现时顺手删掉 SwarmAI 这 4 条。

## 6. 验收（端到端）
1. 在聊天里说"在 SwarmAI 里，以后 X"，会话结束后 STEERING 里有这一条，带 `project:SwarmAI`；Project Agent 页面能看到。
2. 下一个会话第一条消息，STEERING 里就有它（现在已经是这样，回归检查）。
3. 一个 SwarmAI 的 pipeline run 走到 REVIEW：每个专家的派单里都有这条规则。
4. 只生成派单文本、不真派：任务文本里有 `Rules:` 和这条；另一个项目的派单里没有。
5. agent 自己总结的一条（没有你的原话）标了 project：没进 STEERING，进了项目 DDD。
6. 在页面上移除：专家派单和任务文本里都没有了；撤回记录可查。

## 7. 验证计划
- **测试**（每条去掉实现后会变红）：
  - CDF：带原话 + project → STEERING 条目带项目标记；project 不存在 → 不带标记；没原话 → 项目 DDD。
  - `project_rules`：只返回该项目、带 `cand:` 的条目，整条；STEERING 不存在或坏了返回空，不抛。
  - 专家派单（每个专家都有）、`task_spec` 的 `Rules:`（只本项目）。
- **审查**：CDF 写入器和抽取提示的改动跑 correctness + security（这段内容会进每个专家和每个 Hive 任务）。
- **最终测试集**：上面新增的 + `test_task_spec*`、`test_project_agent*`、CDF `pipeline_v2` / `writer` 的现有测试、run_protocol 的 review 派单测试。

## 8. 需要 XG 决定
1. 用 D（规则就是 STEERING 里标了项目的条目），接受"规则不跟着 Brain 走"，可以吗？要是你希望规则属于 Brain、能随 Brain 分享，就选 B，代价是 CDF 里约 10 处改动。
