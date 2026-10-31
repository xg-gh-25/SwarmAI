---
title: Memory 链路审计与修复 —— 从提取源头到 MEMORY.md
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: M1–M3、A1–A3、E1–E2 已落地（2026-10-31）；39 条重新复盘等 E2；晨报去留待 XG 定
scope: 只管 Memory 链路。提取时机（S7 什么时候跑）、事件/gap、DailyActivity 归
  docs/design/2026-10-31-extraction-at-task-end.md（另一个 session），本文不改那些。
---

# Memory 链路审计与修复

## 0. 每条链路都用的审计套路

一条链路 = 一个存储从"源头"到"被读"的全过程。每条链路按同样五步做，结论都要有实测数字：

1. **源头覆盖**：该读的输入是否都被读到（每个 session 是否都复盘、复盘是否读到末尾、
   用户消息是否都能被引用）。数漏掉的，不数做到的。
2. **源头产出**：源头交出来的东西有多少被丢、为什么丢（格式、校验、门槛）；丢掉的
   是否还能找回。
3. **判定与去向**：每类单元按什么规则去哪个存储；规则用的判据是否就是问题本身
   （适用面 vs 可信度 vs 类型）。
4. **终点内容**：存储里现在每一条逐条过：该不该在这里、是否仍然成立、有没有别处
   的重复。按"它被谁、在什么时候读"来判断归属。
5. **读者**：谁读这个存储、怎么读；挪走内容会不会让某个读者失效。

修复按原因分组，一组一个解法；规则问题修在唯一的定义处（所有来源共用），存量数据
用一次性脚本按标题搬迁（先写目标、再删源，archive 先行，可重跑），每处修复配一个
撤掉修复就变红的测试。

## 1. 实测（2026-10-31，live workspace，v2 切换后 10-29 起）

**源头覆盖**
- 18 个有用户消息的 session 全部有复盘任务（`ingestion_s2_jobs`）。
- 4 个 session 的末尾各有 1 条用户消息没被复盘：其中 2 个（f1271f4b、9d2d074e）是旧
  代码"无 sdk id → unresumable，0 条"，2 个（1479d634、13927d14）是旧的
  `below_threshold` 门槛。两者现行代码都已没有（`review_session`：无法 resume 就按记录
  复盘；有用户消息就复盘）。这 4 条都有旧 S2 的产出覆盖，不补。
- 用户消息的 input 事件登记从 10-29 07:48 UTC 开始；之后 0 条缺失。之前 156 条缺失，
  其中带明确要求的 10 条已核对，长期有效的那几句已在 SwarmAI DDD 里。

**源头产出**
- S7 共交出 897 条，丢 66 条（7%），全部在 10-29 ~ 10-30 11:25 之前：49 条是 evidence
  引用少了 `event:` 前缀，8 条 `evidence_not_in_index`，3 条 gap 多了 `type` 字段。之后 0。
- 丢掉的 45 条 cognition 原文都在 `extraction_attempts.invalid_units` 里，有价值的未找回。

**判定与去向**
- 3 天里写进 MEMORY 的 6 条：npm ENOTEMPTY、token 求和少算缓存、macOS/Linux 路径大小写、
  pydantic 报错带出原值（工具/库的窄知识），以及两条 CNTech tenets 文档的内容（一条后来
  已被 XG 推翻并撤回）。**6/6 都不是"每个 session 都该读的判断"**。
- 原因：`route_unit` 里 `scope="world"` → MEMORY，`scope="agent"` → EVOLUTION，能不能进
  只看可信度（用户原话或实测）。prompt（`s7_review._WHERE`）只说 agent/world"会在以后每个
  session 被读"，没给标准；`SCOPE_RULE` 只是枚举。于是"可靠"等于"每轮注入"，适用面不在
  判据里。EVOLUTION 的 Competence Learned 同样中招（K004 调度器孤儿状态、K006 弹窗位置）。

**终点内容**（MEMORY.md 81KB，每轮整份注入）
- 85 条，81 条是 CDF 之前手写/旧蒸馏留下的，4 条 CDF。
- 约四分之一只在 SwarmAI 这个项目里有用（具体模块、工具、管线的坑和决定），或是窄的
  工具知识；它们应在 SwarmAI DDD（做 SwarmAI 时召回）或 General Brain（用到该工具时召回）。
- Open Threads 一节是状态快照（"prod-verify pending"、"P0 未解决"），CDF 解析不出条目，
  也没有任何写入方再更新它；晨报（`proactive_intelligence._parse_open_threads`）读它。

## 2. 修复（按原因分组）

**M1 —— 适用面写进 scope 的定义（规则，唯一定义处）**
`contracts_v2.SCOPE_RULE` 和 `s7_review._WHERE` 写明：`agent` / `world` 是每个 session 都读的
认知，只留"会改变你在大多数任务里怎么判断"的原则、决定、模型、纠错和普遍的教训；
只在用到某个工具、库、API、领域时才有用的知识，无论多可靠，写 `project:<那个主题>`
（不是项目的名字就进 General Brain 的那个主题，按需召回）。S1（`s_persist`）的示例不再
默认 `world`。所有来源经 `SCOPE_RULE` 共享这句（S7 prompt、schema 说明、S1 报错）。
不加代码判据：适用面是含义判断，由有全部上下文的 agent 声明（cdf-agent-decides 原则）。

**M2 —— MEMORY 存量按归属搬走（一次性，`scripts/memory_relocate.py`）**
逐条按 §3 的表：只在 SwarmAI 有用的 → SwarmAI DDD 对应文档/节；窄的工具/领域知识 →
`Knowledge/Library/<subject>.md`；目标里已有同一条的只从 MEMORY 删。按标题匹配，先写
目标、再删源，经 `governed_edit.apply_user_edit`（archive 先行、baseline 修订），可重跑。

**M3 —— 找回因格式丢掉的单元（一次性，`scripts/cdf_replay_invalid.py`）**
证据是裸 id 的补上 `event:input:` 前缀（仅当它是同一 session 登记过的输入）；`project:SwarmWS`
改成 `project:SwarmAI`；读集给该 session 在那次复盘结束前的全部输入（只会比原读集大，
外部内容的标记不会变弱）；去掉已不成立的；按原 session 以 S7 重新入队，走正常判定。

实际结果：45 条里 **39 条没有可引用的证据**（引用的是一句话、提交说明或消息 uuid，复盘的
证据索引只收用户消息和实测结果），CDF 只留能指出证据的，补回去也会再被拒，不替它们编
证据；2 条已不成立；**4 条补回，全部写入**（SwarmAI 的 PROJECT / TECH / IMPROVEMENT）。
39 条的原文仍在 `extraction_attempts.invalid_units`，其中不少还有价值（几条安全审查结论、
Hive 隔离决定、"把 Hive worker 当作直接延伸"）。找回它们的正确办法是让这些 session 从当时
的水位重新复盘一次；那要求一次复盘能看到本 session 之前写过的单元以免重复，正是
extraction-at-task-end E2 在做的事，所以等 E2 落地后做。

**不在本次（记为下一条链路）**
- Open Threads 换成 SwarmAI IMPROVEMENT 的 gap，晨报改读 gap：属于"开放问题/晨报"链路。
- EVOLUTION 存量（Competence Learned 里的窄知识）：EVOLUTION 链路，同一套路。

## 3. MEMORY 搬迁结果

26 条搬出（`scripts/memory_relocate.py` 的 `PLAN`，逐条写了原因），MEMORY.md 81,460 → 67,269
字节：20 条只在 SwarmAI 有用的进 SwarmAI DDD（CDF 自己按类型和主题会选的文档和节），5 条
工具/库的窄知识进 General Brain `general-knowledge.md`，1 条 tenets 文档的模型进
`cntech-ivt-autonomous-tenets.md`。目标里没有重复；5 条 CDF 条目的去向记录和关系已更新。

保留的判断：原则、跨项目的工程教训、"价值优先"、COE Registry（STEERING #20 引用它，
事故教训塑造每次判断）。

## 4. 不再找回的 12 条（已不成立）

墓碑保留 cand 身份；General Brain 覆盖 Learned 和 Library；DDD 衰减只看年龄；
`.ddd-usage.json` 写入方是死代码；superseded_by 标记位置；DDD 覆盖机制推迟；撤回最高
EVOLUTION 编号会被复用（已修）；judge 的 same / adds 精度两条和修法一条（judge 已删）；
逐字核对挡掉 contradicts（该关系已不存在）。

## 5. 验证

| 必须成立 | 测试 |
|---|---|
| S7 prompt、schema 说明、S1 报错里的 scope 规则都带适用面标准，且来自同一个定义 | test_cdf_scope_rule（新） |
| 搬迁：目标里有、MEMORY 里没有；目标已有同一条时不重复；重跑不重复 | test_memory_relocate（新） |
| 找回：只补登记里存在的事件；过时的不入队；入队后走正常判定 | test_cdf_replay_invalid（新） |

上线后实测：之后 7 天 MEMORY / EVOLUTION 新写入的每一条是否都是"每个 session 都该读"的。


## 6. 链路 A：Open Threads 与晨报（同一套路）

**实测**
- 写入方：没有。CDF 只写条目，Open Threads 不是条目（解析不出单元）；`coe_threads` 明确"不再写
  进 Open Threads"。最后一次手工更新是 10-27。内容是状态快照（"P0""prod-verify pending"
  "PARTIALLY RESOLVED (2026-06-18)"），每轮整份注入 MEMORY。
- 逐条对照现状：streaming freeze 那一半已不成立（事件循环卡顿 10-21 132 次/天 → 10-30、10-31 为 0）；
  TTFT p50 10.1s → 7.4s，耗时在模型一侧；Interrupted 仍有发生、根因未查；其余是 06 至 08 月的
  技术债描述，MCP Gateway 的前提已经失效。
- 读者：晨报（`proactive_intelligence`）把它当作 Suggested focus 的来源；`loops_health` 的 M4 探针；
  `s_radar-todo` 的"Open Threads 里有的不要再建 ToDo"。晨报自己的记录：**54 次建议，0 次被采纳**，
  每天推同样三条（4 天、77 天、COE）。
- 产品模板 `backend/context/MEMORY.md`：规则 2-7 是 CDF 之前的说法（从 DailyActivity 蒸馏、
  压缩旧条目、超预算挑条目注入），与 R22（整份注入、不截断）矛盾；节名是 Recent Context /
  Key Decisions / Lessons Learned，而 CDF 写 Principles / Decisions / Pitfalls 等
  （`MEMORY_TYPE_TO_SECTION`）。**新装的工作区（含 Hive）每条进 MEMORY 的单元都会因
  `missing_section` 挂起，并在 Need You 里请用户手动加标题。**

**修复**
- **A1 —— Open Threads 不再是 MEMORY 的一节。** 持久的知识（freeze 的机制、streaming 卡住时被源码推翻
  的几种说法、reconcile race 的两条已修与仍需观察）原文搬进 SwarmAI IMPROVEMENT「What to Watch For」；
  还开着的工作变成 ToDo（自包含的工作包）；过时的删掉（archive 留原文）。
- **A2 —— 晨报不读 Open Threads。** 来源只剩 COE 状态和 DailyActivity 的后续项（后者归
  extraction-at-task-end E5）。`loops_health` 删掉 M4，`s_radar-todo` 和 CONTEXT.md 的说法改正。
  晨报本身是否保留（0/54）交给 XG。
- **A3 —— MEMORY 的节由写入方保证。** MEMORY 的节由类型决定（产品定义，不是用户的结构），所以写入
  MEMORY 时节不存在就补上，不挂起；DDD 文档的结构归人管，仍然挂起。模板改成 CDF 的节和现行规则。

## 7. 链路 B：EVOLUTION（同一套路）

**实测**
- S7 交出的纠错约 50 条，只有 4 条带了 `user_quote`；其余即使就是 XG 的纠正，也因为"无用户原话"
  进不了 EVOLUTION，转去 General Brain 的 agent-self-knowledge（31 条）。切换时的积压
  （`discarded_backlog`，19 条纠错在内）按 cdf-agent-decides 的决定不重跑。
- EVOLUTION.md 56.8KB：CLASS B 一块 29.5KB（纠错、识别信号和历次数据点），按 C046 不压缩；
  Competence Learned 里 K005（`.secrets.baseline` 的工具细节）是窄知识；C067/C070/C071 只关乎
  SwarmAI 的记忆管线、pipeline 阶段和 pipeline 结果卡。
- 读者：每轮注入；`governance_miner` 读 C 条目；编号读撤回记录（已修）。

**修复**
- **E1 —— 用户的纠正带上用户的话。** 复盘 prompt 第 5 步：用户纠正你的每一次，把用户纠正你时的原话
  逐字放进 `user_quote`；只关乎一份文档、一个工具的纠正按 M1 的适用面声明 scope。
- **E2 —— 存量。** K005 → General Brain；C067、C070、C071 → SwarmAI DDD（`memory_relocate.py`
  扩展到 EVOLUTION）。CLASS B 不动。
