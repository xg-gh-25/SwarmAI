---
title: Recall 语料的写入与准入 — Memory、DDD、Knowledge 三块统一的准入、身份、使用信号与退出
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r2 — 设计，待评审（r1 只覆盖 Knowledge，已被本版替换）
---

# Recall 语料的写入与准入

## 0. 一句话

读取侧已经按排名、日期、来源做了过滤（`2026-10-26-recall-precision-and-coverage.md`）。这一版看写入侧：
Memory、DDD、Knowledge 三块的每个写入者都按"宁缺毋滥"审了一遍。结论是三块犯的是同一组错误：

1. **准入不问"该不该放在这里"。** 共用的 LLM 判官只检查一条内容是否具体、能否执行，不检查它属不属于这个存储。
   所以每轮都会注入的 MEMORY 里，有一半以上是只和某个项目有关的条目。
2. **写入者能绕过准入。** DDD 最大的写入者 REFLECT（592 条）直接写入，跳过了准入；还有好几个 skill 和
   编辑器接口也能直接写。
3. **"用过没有"这个信号是死的。** `ref` 字段从来没有生产代码去加。MEMORY 87 条全是 `ref:0`，
   DDD 也基本如此。所有退出决策（衰减、归档）都只能按时间和类型来判断。
4. **大部分条目没有退出路径。** 按类型免疫、按 section 常青，TECH 甚至完全没有自动退出。有几条退出路径是
   直接删除，不先归档。
5. **注入形态和内容大小不匹配。** DDD 注入的是 section 指针，指向的 section 中位约 16K 字符，最大 234K。

本设计给三块定一份统一的**存储契约**（§3），再把每个问题落到契约上（§4）。

**和 r1 的关系**：r1 只覆盖 Knowledge，其中 S3 要重切全部 chunk，太重，而且给 chunk 加标题行会让读侧的覆盖率判断变松。
本版改成在读取和渲染时处理（§4.4），不重切、不重建索引。

## 1. 怎么评估的

三个只读子 agent 并行审计，不改任何文件、不读会话数据。只有 Knowledge 那一路以只读方式查了派生的
`knowledge_chunks` 表。审计报告留在 scratch，不进仓库，因为里面引用了私人内容。这里只放汇总数字。

| 块 | 看了什么 |
|---|---|
| Memory | 所有写入和退出路径（带代码行号）；live MEMORY.md 每一条逐条分类；EVOLUTION 按 section 分类；`ref`/`decay` 元数据的维护 |
| DDD | 所有写入者及各自写入的条数；使用信号；把 48 次真实 DDD 注入指向的 section 打开，逐个判断有没有用、项目对不对；SwarmAI 的 TECH 和 IMPROVEMENT 随机抽 40 条做分类 |
| Knowledge | 各目录由谁写、按什么条件写；目录统计；backflow 写的 Note 抽 15 篇判断价值；10 月的中途 checkpoint |

同时用到前面读取侧回放和盲评的数据：库里 21062 个 chunk、约 3000 个打过标签的命中。

## 3. 存储契约

三块共用一份契约。每个存储回答四个问题，答案写在一个模块里（`backend/core/store_contracts.py`，
吸收 r1 里的 `recall_sources` 来源策略），所有写入者和读取侧都读它：

| | MEMORY（每轮整份注入） | 项目 DDD（按项目按需注入） | Knowledge Library（召回时排名） |
|---|---|---|---|
| **准入问题** | 跨项目、不依赖查询、在多数会话里都会影响判断吗？ | 离开这次会话或 run，以后还会影响这个项目的判断吗？（不是流水，不是会变的数字） | 离开原会话也能独立读懂、可复用吗？已有更权威的文档覆盖了吗？ |
| **答"否"时去哪** | 只关一个项目 → 那个项目的 DDD；只是这一次的事 → 不写 | 不写（它本来就在会话和 DailyActivity 里） | 不写（它本来就在会话里，过去会话那一路能召回） |
| **身份（去重的键）** | 条目标题签名 | 条目内容签名 + 来源（run id / 会话 id） | 生产者 id（消息 id、会话 id） |
| **去重范围** | live MEMORY + steering/SOUL/AGENT + 目标项目的 DDD | 整份 DDD 文档 | 同一生产者 id 的已有文件 |
| **退出** | 契约复审（§4.5）；归档优先 | 契约复审；归档优先 | 版本族只留当前版；归档优先 |

"宁缺毋滥"落在这张表的意思是：**判官拿不准就不写**（fail-closed）。现在的判官为避免漏掉内容，
默认放行；这对按需召回的库问题不大，但对每轮注入的 MEMORY 正好相反。

## 4. 设计

### 4.1 一个准入入口

`ingestion_gate.admit(store, candidate, *, writer, authority)` 是三个存储的唯一写入入口：

1. **形状**：符合规范格式（类型标签、标题、元数据行）。
2. **路由**：按契约判断该放哪。MEMORY 的候选先确定项目：用会话碰过的绑定仓库
   （`repo_guidance.session_bound_project`），或 run 所在的仓库。能确定项目且内容只和这个项目有关的，
   转到这个项目的 DDD。
3. **去重**：按契约里的身份和范围检查。
4. **判官**：用契约里这个存储的准入问题；拿不准就不写。
5. **写入**：加锁写入，带 `source:<writer>` 元数据。

`authority="human"`（人工批准的提案、用户明确说"记住"）**只跳过判官**，形状、路由、去重照常执行。

改走这个入口的写入者：

| 写入者 | 现在 | 改为 |
|---|---|---|
| REFLECT（`reflect_job.py:231-284`） | 直接 `apply_to_ddd` | `admit(store="ddd")` |
| 人工批准 cultivation 提案 | 直接 `apply_to_ddd` | `admit(store="ddd", authority="human")` |
| 自动蒸馏 → MEMORY | 共用判官，没有路由 | `admit(store="memory")`，先路由 |
| 即时纠正 → MEMORY（`runtime_hooks.py:300-447`） | 共用判官 | `admit(store="memory")` |
| DailyActivity 决策/纠正 → DDD | 一律写到 SwarmAI | 按会话的绑定仓库确定项目；确定不了就不写 |
| session 结束 writeback | 置信度 0.5 永远过不了 0.7，实际已经不写 | 删掉这条路径（它的内容由 REFLECT 和 DailyActivity 覆盖），或者给它一个真实的置信度；实施时先看过去 30 天它丢弃的候选再定 |
| `s_persist` / `s_self-evolution` / `s_ddd-persist` | Edit 或 `locked_write` 直写 | 用同一个 `admit` 的 CLI，`authority="human"` |
| `s_memory-distill` | 说明和代码多处矛盾 | 删除这个 skill（同样的工作由会话结束的蒸馏 hook 完成） |
| 工作区编辑接口写 MEMORY/EVOLUTION | 不加锁，整文件覆盖 | 加锁 + `MemoryGuard` 形状检查；人工编辑内容不跑判官 |

### 4.2 写入者按生产者身份去重（Knowledge）

- **backflow**：同一条 assistant 消息只产出一个 Note，文件名带消息 id，存在就跳过（r1 的 S2）。
  同时按契约过判官：内容离开原会话读不懂（进度汇报、review 结论、"暂停等你决定"）就不写；
  已有人写的同主题文档就不写。按审计的样本，15 篇里能留下 6 篇。
- **checkpoint**：会话结束条目写入时，在同一个锁里删掉这个会话的 checkpoint 块（`upsert_session_block`
  已经按会话和类型定位块）。checkpoint 只在会话没有正常结束时保留，用于崩溃恢复。
- **已删除的会话**：DailyActivity 和 backflow 两个 hook 开头先查会话是否还在，不在就不写（r1 的 S2）。
- **DailyActivity 块格式**：`upsert_session_block` 断言块以条目头开头、块内没有 `## `；
  追查 `## Session Resume` 独立块的写入者（r1 的 S2）。

### 4.3 真实的使用信号

现在的 `ref` 没人加，`record_ddd_hit` 又会把没送到模型的内容也记成"用过"。改成只有一种信号：

- **"送达"事件**：recall 或 DDD 那一路把一个条目的正文真正写进注入块时，记一条
  `(存储, 条目锚点, 日期)`，写进现有的 `.ddd-usage.json` 边车文件（同一个写入者、同一把锁，不新建存储）。
  只输出指针、没有正文时不记。
- MEMORY 每轮整份注入，"送达"对它没有意义，所以 MEMORY 不做使用统计，它的退出靠 §4.5 的复审。
- 不再依赖条目上的 `ref:N` 字段做任何判断。字段保留，不批量改写已有条目。

### 4.4 注入形态和读侧联动（不改语料）

- **DDD 注入条目正文，不注入 section 指针。** 每个命中的 section 只取和查询最相关的几条 entry
  （`_recall_ddd` 内部已经有这些 entry），按 `recall_filter.DOMAIN_CHAR_CAPS["ddd"]` 截断，
  经 `_deliver_block` 注入并计入会话预算。指针目标在渲染时先校验是否存在，不存在的丢掉。
- **Library 命中在读侧处理 r1 的 S3 想解决的问题**：
  - 只有 frontmatter、没有正文的命中直接丢掉；
  - 正文少于 200 字的命中丢掉；
  - 文档标题在渲染时从 frontmatter 的 `title` 或来源路径取出，显示在来源行上，**不写进 chunk 正文，
    不参与覆盖率计算**；
  - 内容完全相同的命中先折叠，再取排名名额（r1 的 S4，顺序固定并写测试）。
- **索引排除依赖目录**：来源策略里加一条规则，路径中含 `node_modules`、`.venv`、`dist`、`build`
  的文件不进索引。下一次索引同步会把这些派生 chunk 删掉（文件本身不动）。
- 这一节不重切、不重建索引，所以 prompt 内去重（最近两天的 DailyActivity）、覆盖率门槛、排名名额
  这三条读侧规则的输入都不变。r1 里那几处联动风险不再存在。

### 4.5 退出：归档优先，契约复审

- **所有删除改成先归档。** 每周 Memory Health 删 MEMORY/EVOLUTION 条目、DDD 矛盾替换删旧条目、
  EVOLUTION 的 prune，统一改为调用已有的 `archive_entries`，再从原文件去掉。
- **契约复审替代"按类型免疫"。** 现在大部分类型不受时间影响，是为了保护来之不易的经验；
  问题是没有任何别的出口。新增一个离线复审（并进现有的每周 Memory Health job，不新建 job）：
  - 读**完整**文件，分段送判官，而不是只看前 8K 字符；
  - 按存储契约逐条判断：该留、该移去 DDD、该归档、和别处重复；
  - 输出一份提案列表，每条带理由。执行方式：都是归档优先（可以从归档恢复），所以每周自动执行，
    每次最多执行固定条数（沿用 Memory Health 现在的上限），执行结果写进 `EVOLUTION_CHANGELOG.jsonl`
    和每周报告。不额外加人工确认这一步；**第一次**复审例外（见 §6），因为积压量大，先给你看。
  - DDD 的 TECH 也进入复审范围（现在它没有任何退出路径）。
- **版本族**：Designs、Library 里 `-v2`/`final` 并存的文件，复审时提议把旧版移进 `Archives/`。不自动移动。

## 5. 每个事实在哪、谁写谁读

| 事实 | 唯一来源 | 写 | 读 |
|---|---|---|---|
| 一个存储的准入问题、路由、身份、去重范围、退出 | `store_contracts` | 人改表 | `ingestion_gate.admit`、复审 job |
| 一类来源进不进索引、能不能自动注入、走不走时间规则 | `store_contracts`（来源策略部分） | 人改表 | `knowledge_store` 同步、`recall_filter` |
| 会话属于哪个项目 | `repo_guidance` 的会话状态 + `bindings.yaml` | repo_guidance hook | `admit` 路由、DDD 注入 |
| 一个条目有没有送达过模型 | `.ddd-usage.json` | recall / DDD 注入（只在正文送达时） | 复审 job |
| 一个 Note 来自哪条消息 | 文件名里的消息 id + frontmatter `source_message` | backflow | backflow |

热路径：`admit` 只在后台 hook、REFLECT 后台任务、skill 调用里运行，不在 chat 发送路径上；判官调用沿用现有的
预算（60 次 / 300 秒）。读侧改动（§4.4）是对已经取回的十几个命中做内存里的过滤和折叠，DDD 正文注入
只读已经加载的文档。都不碰事件循环，也不碰 chat 等待的线程池。

## 6. 历史数据

- 不改名、不删除、不改写任何已有的 Note、DailyActivity、会话。
- 已有的重复、frontmatter chunk 在读侧处理（§4.4）。
- `node_modules` 的 chunk 会在下一次索引同步时从**派生索引**里消失，源文件不动。
- **MEMORY 里的 51 条项目专属条目和 12 条重复**属于现有内容的整理，不是 bug 修复，所以不自动搬。
  第一次契约复审只生成提案列表（每条：目标位置、理由），由你看过之后再按归档优先执行；之后的每周复审
  按 §4.5 自动执行。

## 7. 实施顺序和验收

每一步一个 worktree、一个 ff-merge 的 commit，不 push、不 rebuild。

**阶段 1：读侧和索引（不改语料内容）**
1. `store_contracts` 的来源策略部分：替换 `_SKIP_*`、`NO_INJECT_PREFIXES`、`_DAILY_RE`、`ARCHIVE_PREFIX`，
   加依赖目录排除。行为不变（排除规则除外），现有测试全绿即证明。
2. Library 读侧：frontmatter-only / 过短命中丢掉、渲染时加标题、相同内容先折叠再取名额。
3. DDD 注入条目正文 + 指针校验 + 只在送达时记使用。
- 验收：回放第 101–200 个会话，改前改后成对盲评（同今天的做法）。DDD 部分单独盲评注入的正文。
  通过条件：噪音比例下降，"需要 recall 且拿到有用内容"不下降，注入总量不超过现在。

**阶段 2：写入准入**
4. `ingestion_gate.admit` + 三个存储的契约问题；fail-closed。
5. 改走 `admit`：REFLECT、人工批准、自动蒸馏（含项目路由）、即时纠正、DailyActivity → DDD 的项目归属、
   三个 skill；删除 `s_memory-distill`；编辑接口加锁和形状检查；writeback 先看丢弃日志再定去留。
6. backflow 身份去重 + 契约判官；checkpoint 随会话结束条目一起替换；已删除会话不写；DailyActivity 块断言。
- 验收：
  - 用今天审计里已经分好类的样本当夹具：MEMORY 的 97 条、DDD 抽的 40 条、backflow 抽的 15 篇。
    `admit` 对"该留"的放行率和对其余类的拦截率分别报出来；对"只关一个项目"的 MEMORY 候选，
    路由到对应 DDD 的比例要报出来。
  - 每条规则一个单测并做 mutation 检查（撤掉修复，测试必须变红）。
  - 上线后一周的只读观察：新写入 MEMORY 的条目里项目专属的占比、REFLECT 被拦下的条数、
    同一消息产出两份 Note 的次数（应为 0）。

**阶段 3：退出**
7. 所有删除改为归档优先。
8. 契约复审并进每周 Memory Health：读完整文件，按契约提案；第一次只出提案（§6）。
- 验收：复审对今天审计样本的判断和审计结论的一致率；所有退出路径的单测证明"先归档后去掉"。

## 8. 不做

- **不做候选收件箱**（审计建议自动内容先进一个 candidate 状态再提升）。fail-closed 的判官已经做到"拿不准不写"，
  再加一层状态和提升流程会多出一套要维护的东西；先看阶段 2 上线后的拦截效果再说。
- **不做近似去重和语义合并。** 只做同一身份或完全相同内容的去重；近似的交给契约复审逐条判断。
- **不排除 Pollinate、`Knowledge/Archives/` 整类来源**，只排除其中的依赖目录。
- **不改 DailyActivity 的 LLM 提炼门槛**，要单独评估成本。
- **不重切、不重建索引**（r1 的 S3 已撤回，原因见 §0）。

## 9. 待确认

1. 第一次契约复审只出提案、你看过再执行；之后每周自动执行（归档优先、每次有上限、有日志）。这样可以吗？
2. 删除 `s_memory-distill` 这个 skill（它和生产代码矛盾，做的事由蒸馏 hook 完成）。可以吗？
3. 阶段 1 不改任何语料，可以先做；阶段 2、3 依次做，每个阶段验收通过再进下一个。

## 10. 验收结果

（实施后填写。）
