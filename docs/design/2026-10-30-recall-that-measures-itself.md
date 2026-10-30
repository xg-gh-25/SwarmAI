---
title: Recall：质量自己量，查询由推理者写
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: 待确认
follows: 2026-10-26-run-knowledge.md §3.6, 2026-10-28-knowledge-addressing.md, 2026-10-26-recall-precision-and-coverage.md
---

# Recall：质量自己量，查询由推理者写

状态：设计 v2，待 XG 确认。v1 经过一次零上下文评审，三处前提被推翻，见 §8；本版按根因重写。
数据来自 2026-10-30 的实测（样例集和脚本见 `Knowledge/Reports/2026-10-30-ddd-recall-eval/`、
`/tmp/recall-eval/scripts/`）。

## 0. 结论

recall 一直在改，但从来没有一个持续运行、确定性的质量测量，所以每次改动是好是坏都靠一次性实验，
而实验用的样例集因为没有消费方被删掉了。这是要修的第一件事，其余改动都建立在它上面。方案分四部分：

1. **测量环（地基）。** 每个 Brain 有一份 recall 样例集，由真实 run 里 agent 自己的搜索和引用自动补充；
   一个确定性的检查每天跑，退化逐条点名。以后任何 recall 改动都用它判断是否合入。
2. **查询由推理者写（实测最大的杠杆）。** pipeline 里凡是 agent 已经推理过的 recall
   （create、design、review），查询加上 agent 写下的"解决方案搜索词"。
   同一份 DDD 快照上，design 命中 10/49 → 18/49（评审复跑：8/49 → 17/49）。
3. **中文匹配：按事先写好的规则用数据决定。** Library 的 FTS 现在有 20% 的中文查询词是废词，
   但这个数字说明不了相关性。候选只有"不改"和"bigram 一致化"两个，在测量环上量，按 §4.3 的规则二选一。
   jieba 因为代价（常驻内存 +89MB、磁盘 38MB、初始化 0.7 秒）不作为候选。
4. **删掉一份死的使用记录。** `.ddd-usage.json`（`record_ddd_hit` / `record_cited`）只有写入方，
   没有读取方；DDD 的衰减只看时间。删掉它之后 `unit_usage` 是唯一的使用记录，
   在出现真实读取方之前不再新增写入方。

不做：缩减注入（少三分之一相关命中，只省约 600 token），向量检索，给 DDD 条目补双语别名，
合并 `unit_id` 和 CDF anchor 两套身份（理由见 §6）。

## 1. 实测（2026-10-30）

样例集：17 个最近的真实需求 × {requirement, design}，共 34 条样例、62 个预期条目，由零利害的 agent
只读 DDD 原文标注，不碰 recall 代码。每组对比在同一时刻的 DDD 上跑，只改一个变量。

| 变量 | design | requirement |
|---|---|---|
| 10-27 代码（`5876efc7f`） | 11/49 | 3/13 |
| HEAD | 11/49 | 7/13 |
| HEAD，注入从 7 条缩到 3 条 | 7/49（省约 2.3K 字符） | — |
| HEAD，中文用 jieba 分词（只算中文样例） | 9/43，现状 10/43 | 6/13，现状 6/13 |
| HEAD，查询 = 需求原文 + 解决方案搜索词 | **18/49**（复跑 17/49，现状复跑 8/49） | 7/13 |

需要说明的局限：

- **DDD 在几小时内就变了**，同一组对比复跑会差 1–3 条。所以 §5 规定每次实验都在冻结的 DDD 快照上跑。
- **解决方案搜索词**由一个看不到标注的 agent 只凭需求原文写出，属于单次采样。
  评审试了不同的词数上限和词序，结果在 16–17/49 之间，增益是稳定的。
- **它没有找回 agent 在真实 run 里按需搜到的那 3 条**（0/3）。所以它改善的是起步包，
  替代不了按需搜索，地图和 `knowledge find` 仍然是主通道。

真实 run（最近 5 个，`run_audit.json` 的 `details.knowledge`）：开局注入 61 条，引用 0 条；
agent 按需搜索 11 次，引用了其中 4 条。

## 2. 根因（本轮实读，v1 评审逐条核对过）

**RC1：没有持续的测量。**

- 样例集 `ddd_recall_cases.json` 没有消费方，10-29 整理 Brain 时被列为死数据删除（SwarmWS `0472468f36`）。
- 它的预期写的是条目子串，条目被改写后就失效了（例如 "Memory sovereignty" 现在是
  "Memory is self-owned plain text…"）。
- `scripts/ddd_recall_eval.py` 只能手动跑，只覆盖 DDD 的两个 purpose。
- 已有的测量各管一块，都不是持续的回归检查：
  - run 审计的 `_knowledge_summary`：每个 run 的 cited / cited_on_demand / searches；
  - `scripts/knowledge_usage_report.py`：从 transcript 统计；
  - `scripts/recall_replay.py`：聊天 recall 的回放，加上 LLM 判 useful/noise，只能手动跑，需要 owner 同意。

**RC2：pipeline 的查询取自错误的文本。**

- 每个入口都用 `extract_query_keywords` 对人写的文字抽词：
  `run_knowledge.query_for`（`run_knowledge.py:202`）、`expert_block`（`:372`）。
- design recall 在 EVALUATE 之后跑（`engine.py:2394`），这时 agent 已经读过代码，
  但查询只取需求原文和 REQUIREMENTS 的叙述字段（`_PROSE_KEYS`，`:165`）。
- 条目用的是解决方案的词，而且大多是英文。纯关键词 recall 跨不过这个落差；按需搜索能命中，是因为 agent 用了自己的词。

**RC3：中文查询词有两套匹配规则。**

- BM25 类入口（DDD、MEMORY、`knowledge find`）：两边都展开成两字片段，所有片段之间是 OR（`memory_index._bm25_tokenize`，`:258`）。
- FTS 类入口：每个词的两字片段之间是 AND（`cjk_index.expand_cjk_query`，`:191`）。
  入口是 `KnowledgeStore.fts5_search` / `_fts5_fallback_search`（`knowledge_store.py:731/774`，
  Library recall 经 `recall_engine.py:101`，界面的 `archive_browse.py:281` 也调它），
  以及 `SessionRecall.search` / `search_session_list`（`session_recall.py:135/295`）。
- `extract_query_keywords` 把中文硬切成 4 字片段（`_CJK_MAX_TERM`，`recall_filter.py:140`）。
  切出来的不是真实的词，在 FTS 的 AND 规则下成了废词。
- 聊天的相关性闸门也依赖这些词：按整词子串计覆盖（`_coverage` / `_required`，`recall_filter.py:244/240`），
  只有一个词时清空全部结果（`:417`）。所以只改 FTS、不改闸门，就会改变聊天的 recall。

**RC4：两份使用记录，其中一份是死的。**

- `.ddd-usage.json`：写入方是 `record_ddd_hit`（`session_router.py:1963`、`addressing.py:245`）
  和 `addressing.record_cited`（`engine.py:2481`、`hook_bridge.py:365`）。
  读取方 `load_ddd_usage` 没有任何生产调用。DDD 的衰减只看时间（`ddd_entry_lifecycle.py:18`），
  `assess_decay` 只有 `scripts/probe_memory.py` 在调。
- `unit_usage`（CDF）：有真实读取方，`memory_injection.py:326` 给 context file 排序，
  `ingestion/status.py:108` 也读它。`cited_as_evidence` 由 `ingestion/provenance.py:428` 写入。

## 3. 测量环

### 3.1 样例集

- **位置**：`<DDD>/.artifacts/recall-cases.jsonl`，路径由 `ddd_paths` 新增的 `recall_cases_path(project)` 给出。
  - 不放 `3-gates/`：那里的每个文件都进 Project Agent 的 `gates_digest`（`project_agent.py:244`），
    一追加就会让在跑的 session 被拒（`project_agent_session.py:240`）。而且 3-gates 会随 Brain 打包分发，
    样例里有用户的原话，不能跟着出去。
  - `.artifacts/` 只留在本机，Brain 标准里写明这个文件和它的消费方（§3.3），以后整理时不会再被当作死数据。
- **一条样例**：
  `{case_id, door, request, knowledge_query?, expect: [{unit_id, doc, head}], source, run_id?, added_at}`。
  - `door` 取值：`requirement` / `design` / `review`。
  - `head` 是条目的首行，也就是 `run_knowledge.entry_head`，REFLECT 用它找回条目，这里沿用同一个机制。

### 3.2 自动补充（写入方是 engine，不经过模型）

run 进入 completed 时：

- **DESIGN_SPEC 的 `knowledge_checked`**（`schemas.py:339`，`[{query, result_ids}]`）：
  agent 自己的查询加上它找到的条目。每个结果写成一条 design 样例，用 agent 的查询作为 `knowledge_query`。
  这是最好的来源：词和条目都来自真实使用。
- **按需引用**（`cited_on_demand`）：注入漏掉、agent 自己找到并引用的条目，写成样例。
- **注入后被引用**（`cited`）：写成正例。
- **入口归属按条目所在的文档判断**，不按引用出现在哪个文档里判断：
  - PRODUCT/PROJECT 的条目算 requirement；
  - TECH/IMPROVEMENT 的条目算 design，同时算 review；
  - archive 和 `knowledge/` 下的单元任何注入入口都不会返回，不写入。
- **去重**：按 `(door, request, unit_id)` 去重。
- **并发**：写入在 `utils.file_lock.flock_exclusive` 下进行（`eval_service` 用的是同一个原语），
  `--prune` 也一样。
- **种子**：本轮的 34 条样例把子串换成 `{unit_id, doc, head}`，作为 SwarmAI 的初始样例集。

### 3.3 检查：`scripts.recall_check`

- **怎么跑**：对每个 Brain 的样例，用真实的入口函数跑：
  - requirement：`run_knowledge.build` 的 DDD 腿；
  - design：同上，`purpose="design"`；
  - review：`expert_block`。
  只做关键词检索，不调模型。
- **找回条目**：先按 `unit_id` 找。找不到就在同一个 doc 里按 `head` 找：
  - 找到：条目是被改写了，跟随到新 id，并更新样例；
  - 找不到：条目已经没了（衰减或删除），记为"过时"。
  过时样例单独列出，不算作未命中；加 `--prune` 时删除。
- **输出**：
  - 每个入口的命中数；
  - 每个入口的"展示 → 引用"比例，读最近 N 个 run 的审计，不新加计数；
  - 和上一次结果（`<DDD>/.artifacts/recall-cases.last.json`）逐条对比：上次命中、这次未命中的样例点名列出。
- **谁来跑**：注册成一个私有 golden case（`scripts/eval_runner.py:648`：私有 case 能看到真实工作区），
  有退化就进 `eval_followups`。
- **产品侧**：带一个夹具 Brain（中英混合条目），加一个 `probe_recall` BVT，钉住 §4 的机制，在 CI 里跑。
- **取代**：`scripts/ddd_recall_eval.py` 删除，`tests/test_recall_ddd_purpose.py:233-241` 的用法一并迁移。

### 3.4 实验协议

- 每个设计决策（§4.2 的词序和词数、§4.3 的中文规则）都在冻结的 DDD 快照上量：
  `git archive` 出某个 SwarmWS commit 的 `Projects/<P>`，把 `SWARM_WORKSPACE` 指向它。
- 判定规则写在本文档里，量之前就定好，不看结果再改。
- 聊天入口用已有的 `scripts/recall_replay.py`（手动跑，owner 同意）。

## 4. 改动

### 4.1 查询由推理者写

| 入口 | 查询 | 改动 |
|---|---|---|
| 聊天首条消息 / follow-up / `[DDD]` 提示 | 用户原话 | 不变。这时还没有人推理过，它的职责是起步包加地图 |
| `knowledge find` | agent 自己的词 | 不变 |
| create（`requirement`；没有 EVALUATE 的 `direct` 在这里做 design） | 需求原文 + `--knowledge-query` | create 新增一个可选参数，由发起 run 的 agent 填 |
| design（EVALUATE 之后） | 需求原文 + `REQUIREMENTS.knowledge_query` + 叙述字段 | full 变体新增一个可选字段（lite 只给 `direct` 用，`direct` 没有 EVALUATE，所以用不上） |
| review 专家块 | 改动文件路径 + 需求原文 + `knowledge_query` | 从受保护的 `knowledge_recalled` 事件里读，不读 agent 能改的 `requirements.json` |
| REFLECT 查重 | 不变 | — |

- **字段**：`knowledge_query` 是一行文本，写"知识库里会怎么称呼这个解决方案"：机制、组件、标识符、失败模式，
  英文为主，可以夹中文术语。校验只看是不是字符串、长度不超过 300 个字符，不判断内容。
  进入 FTS 前沿用现有的清洗和加引号（`knowledge_store.py:709-724`）。
- **记录**：`run_knowledge.contract_record` 新增 `query` 字段，写入受保护的 contract 和
  `knowledge_recalled` 事件。这样每个 run 实际用了什么查询都有记录，可以审计。
  recall 读的是实时的 DDD，所以不承诺能完全重放，只承诺查询本身可查。
- **说明文字**：`evaluate.md:3` 加一句"写 `knowledge_query`：用你打算怎么解决它的词，不是复述需求"；
  pipeline 的 create 说明加上 `--knowledge-query`。

### 4.2 词序和词数（按 §3.4 的协议决定）

现在总数上限是 18：需求原文最多占 10 个，其余给叙述字段。加入 `knowledge_query` 之后，
叙述字段会被挤掉。所以在快照上量三种做法：

- (a) 需求 10 + 搜索词 + 叙述，总数 18；
- (b) 需求 8 + 搜索词 10 + 叙述，总数 30；
- (c) 需求 10 + 搜索词，不要叙述，总数 18。

判定规则：design 加 review 的命中数最高者胜；平局选总词数少的。review 的路径词（12 个）保持原样排在最前。

### 4.3 中文匹配（按 §3.4 的协议二选一）

- **候选 A：不改。**
- **候选 B：bigram 一致化。** recall 的所有环节都把一个中文片段当作一组两字片段：
  1. `extract_query_keywords` 不再硬切，保留按停用词断开后的完整片段；
  2. recall 的 FTS 查询里，这些两字片段之间是 OR。`mode` 参数从 `RecallEngine.search` 一直传到
     `fts5_search` 和 `SessionRecall.search`，没有默认值，调用方必须声明。
     recall 的调用方（Library 腿、会话腿、`knowledge find --in library`、`context_recall_cli`）传 `bag`；
     界面的调用方（`search_session_list`、`archive_browse`）传 `phrase`，行为不变；
  3. 相关性闸门里，一个中文片段在一段文字里"出现"的定义是：它的两字片段至少一半出现在这段文字里。
     英文词的规则不变。
- **判定规则**：同时满足以下两条才采用 B，否则保持 A：
  1. Library 样例的命中数严格上升（Library 样例由零利害的 agent 标注一次，放在 `<workspace>/Eval/recall-cases.jsonl`）；
  2. 聊天回放的 noise 比例上升不超过 2 个百分点。
- 代价已实测：15 条中文需求在 `knowledge_fts` 上，B 的查询 p50 是 35ms（现状 35ms），
  最大值 77ms（现状 94ms）。

### 4.4 删除死的使用记录

删除 `core/ddd_usage.py`，以及 `record_ddd_hit` 的两处调用（`session_router.py:1950-1966`、`addressing.py:245`）、
`addressing.record_cited` 和它的两处调用（`engine.py:2481`、`hook_bridge.py:365`），
还有 `recall_replay.py` 里对它的 no-op 替身。已有的 `.ddd-usage.json` 文件不再被写入，原样留在磁盘上，不删。
"展示 → 引用"的数据来自 run 审计（`_knowledge_summary`），这份记录本来就有。

## 5. 不变量（最终测试集，每条都用 mutation check 验证：撤回修复，测试变红）

- I1 没有 `knowledge_query` 时，create、design、review 的查询和今天逐字节相同。
- I2 有 `knowledge_query` 时，它的词出现在 create、design、review 的查询里，词序和词数符合 §4.2 选定的做法。
- I3 review 读到的 `knowledge_query` 来自受保护的事件。事后改 `requirements.json` 不会改变 review 的查询。
- I4 `recall_check`：
  - 条目原地改写、首行不变时，样例跟随到新 id，不记为过时；
  - 条目被删除时，样例记为过时，不计入未命中；
  - 上次命中、这次未命中的样例逐条点名。
- I5 run completed 之后，`knowledge_checked` 的每个结果、每个 cited 和 cited_on_demand 的 id，
  按 `(door, request, unit_id)` 在样例集里恰好出现一次。archive 和 `knowledge/` 下的单元不写入。
- I6 两个 run 同时完成时，两边的样例都不丢。
- I7（仅在采用 B 时）
  - 含一个废片段的中文词仍能在 Library recall 里命中；
  - `search_session_list` 和 `archive_browse` 的 MATCH 表达式和今天逐字节相同；
  - 只有一个中文片段的聊天消息，闸门行为符合 §4.3 第 3 条的定义。
- I8 生产代码里没有任何地方写 `.ddd-usage.json`。

## 6. 不做什么，为什么

- **缩减注入**：实测少三分之一相关命中，只省约 600 token。等"展示 → 引用"比例积累出数据再决定配额。
- **jieba**：常驻内存 +89MB、磁盘 38MB、初始化 0.7 秒。这个 daemon 按可用内存决定能开几个 tab；
  而它在 DDD 上的收益是 0（9/43 vs 10/43）。
- **向量 / embedding**：违反"keyword-only、记忆自主"的既定决策。
- **DDD 条目补双语别名**：能覆盖还没人推理过的聊天首条消息，但现有约 4000 个单元要回填。
  测量环跑出数据后，如果聊天入口的漏召回占主导，再做。
- **合并 `unit_id` 和 CDF anchor**：确实是两套身份，但本设计不需要合并，也不新增第三种身份。
- **DDD 落后一个 run**（5 个 run 里 4 个用上一版 DDD 开局）：属于新鲜度问题，不是检索问题。

## 7. 步骤和验证计划

| 步骤 | 内容 | 读取方 | 评审 |
|---|---|---|---|
| 1 | 测量环：`recall_check`、样例格式和位置、找回规则、种子转换、私有 golden case、夹具 BVT；删 `ddd_recall_eval.py` | 它自己就是后面所有步骤的读取方 | correctness |
| 2 | 自动补充（§3.2） | 步骤 1 的检查 | correctness、concurrency |
| 3 | 查询由推理者写（§4.1）。先让 schema 和事件能接受、能记录，再写说明文字；在快照上按 §4.2 定词序 | 步骤 1 的检查；review 读事件 | correctness、api-contract |
| 4 | 中文匹配：标注 Library 样例，在快照上量 A 和 B，跑聊天回放（需要你同意），按 §4.3 的规则定 | 步骤 1 的检查 + `recall_replay` | correctness |
| 5 | 删掉 `.ddd-usage.json` 的写入方（§4.4） | 无（被删的东西本来就没有读取方） | 不需要，测试证明 |

完成时逐行报告 I1–I8 是否成立，并报告每一步在冻结快照上前后的命中数。
每一步只跑它触及的测试，加 mutation check；最后整体跑一次 §5 的测试集。

## 8. v1 评审之后改了什么

v1 的三处前提被推翻：

- "CDF 衰减会读 pipeline 的使用记录"：DDD 衰减只看时间，所以 v1 的"补全使用记录"没有消费方。改为删掉死的那份记录（§4.4）。
- "去掉 4 字硬切不影响聊天"：聊天的相关性闸门依赖整词，所以中文改动从直接执行改为量过再定（§4.3）。
- "样例放 `3-gates/`"：会改变 Project Agent 的 gate digest，而且会随 Brain 外发，所以改放 `.artifacts/`（§3.1）。

其余修正：

- lite 变体的说法有误，已更正（§4.1）；
- review 改为读受保护的事件（§4.1）；
- "完全可重放"降级为"查询可审计"（§4.1）；
- 自动补充改为优先用 `knowledge_checked`，入口归属按条目所在文档判断（§3.2）；
- `mode` 参数改从 `fts5_search` 往下传，补上 `knowledge find --in library` 和 `context_recall_cli`（§4.3）；
- 找回规则在找不到 id 时按首行再找一次（§3.3）；
- 写入加锁（§3.2）；
- 不变量 I1–I3 重写，使它们能被违反、能钉住设计（§5）。

## 9. 待 XG 确认

- 这个方案是否通过。
- 执行方式：改动跨 recall、pipeline schema、eval 三块，还要删一个模块，属于跨模块、改持久化格式的变更，
  按 R1 请你选：直接改，还是走 pipeline。
- 步骤 4 的聊天回放会只读你的 `data.db`，跑之前我会再问一次。
