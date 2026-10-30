---
title: Brain-driven：DDD 管产品真相；先让它不说过时的话，再用犯过的错来量
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: 待确认（§3 待决，见 §10）
supersedes: 2026-10-30-recall-that-measures-itself.md
follows: 2026-10-29-cdf-session-review.md, 2026-10-30-cdf-gap-lifecycle.md, 2026-10-30-brain-change-feed.md
---

# Brain-driven：DDD 管产品真相；先让它不说过时的话，再用犯过的错来量

状态：设计 v2，待 XG 确认。结论已由 XG 认可（2026-10-30）。v1 经过一次零上下文评审，
其中"按类型过滤送达"和"写代码时按模式注入"两部分被推翻（见 §7）；本版只保留证据支持的部分。

## 0. 结论

**判断取决于"这个产品是什么、做过什么决定、哪些不做"时，用 DDD。chat、native coding、pipeline 三种模式都一样。**

- **怎么把代码写对**：靠专家评审和仓库自己的评审模式。DDD 只作为可以查的参考。
- **代码现在实际怎么做**：读代码。
- **我该怎么做事**：靠治理规则。

DDD 是否有用，看结果：已经记录过的决定和教训，有没有防住本该防住的错。

证据显示，产品真相出错，全是因为"事先没有"或"内容过时"，没有一例是"有但没送到"。所以这次不改送达方式，只做三件事：

1. **一个取代真相。** CDF 自己做的每一次取代（refine、replace），以及做决定的上下文声明并经过复核的取代，
   都在旧条目上写下 DDD 读取方本来就认的 `superseded_by:` 标记。今天这些旧条目在 DDD 的 recall 里依然可见。
2. **量结果。** 错误倒查做成定期任务，每次都按同一张表出报告。
3. **删一份死记录。** `.ddd-usage.json` 只有写入方，没有读取方。

"事先没有"由 10-29 上线的 S7 会话复盘负责写回，这次不另建机制，下个月的倒查会量它是否起效。

## 1. 证据（2026-10-30）

**错误倒查**：38 个真实错误（10-16 到 10-30），由零利害的 agent 逐条倒查，用 git 历史证明条目早于错误。
明细见 `Knowledge/Reports/2026-10-30-ddd-recall-eval/mistake_audit.json`。

| 需要的知识 | 数 | 事先没有 | 有但没送到 | 送到仍犯 | DDD 内容过时 | 无法判断 |
|---|---|---|---|---|---|---|
| 写代码的机制坑 | 22 | 10 | 11 | 1 | 0 | 0 |
| 产品事实和决定 | 7 | 5 | 0 | 0 | 2 | 0 |
| 我自己的行为 | 9 | 7 | 0 | 0 | 0 | 2 |

- **机制坑**：22 个里 16 个在合入前被专家评审抓到。11 条"有但没送到"的，事先写好的条目都没提到这次改动的文件，
  需要它们的时刻都在写某一类代码的时候。
- **产品真相**：出错全部来自"事先没有"或"内容过时"。
- **DDD 自己记录的风险**（IMPROVEMENT.md:1991）："DDD 质量就是天花板……没有机制把旧条目降级"。

样本只有 38 个，结论按方向来读，不按精确比例。

## 2. 根因：DDD 的读取方看不到取代（本轮实读）

- **writer 的假设不成立。** `ingestion/writer.py:544` 的注释说："refine keeps the old entry's bytes …
  it is superseded, and the injection selector never shows a superseded entry"。
  这只对 context file 成立：`memory_injection._superseded_anchors` 读的是关系表。
  DDD 的读取方，也就是 recall（`recall_multi.py:132/1026`）和 `knowledge find`（`addressing.py:44`），
  只认条目元数据里的 `superseded_by:`，而 ingestion 里没有任何代码写这个标记（四份文档里 0 处真实标记）。
- **后果**：CDF 在 SwarmAI DDD 上做过 12 次 refine，关系表里有 11 条以 DDD 条目为对象的 supersedes，其中 4 条有效。
  被取代的旧条目全部仍然可见。refine 的新条目又带着旧内容，所以 recall 会给出两条几乎相同、但新旧不一的条目。
- **取代的三个来源，可信度不同**：
  - **CDF 自己做的 refine/replace**：旧条目由 writer 按 anchor 找到（`_unit_for_anchor`，`writer.py:527`），身份是确定的。
    来源包括每周 DDD 自审（`jobs/handlers/ddd_self_audit.py`）产生的 S4 修正，它们的 relation_hint 是 supersedes（`:330`）。
  - **判官的 "contradicts"**（`pipeline_v2.py:641`）：大约一半是错的（`2026-10-30-cdf-gap-lifecycle.md` §5）。
  - **做决定的上下文**：现在无法声明取代。`relation_hint` 不由抽取器产出（`extract_llm.py:134` `_NOT_EXTRACTED`）。
- **可沿用的先例**是 gap 关闭（`ingestion/gaps.py:135` `close_gap`、`:189` `close_declared`）：
  - 走受管编辑路径 `governed_edit.apply_user_edit`：先归档，按文件版本乐观写入，有日志；
  - 只在新单元 `written` 之后执行；
  - 被并发修改挡住时，由每日维护重试；
  - 写入的说明里去掉 `cand:`，因为解析器把条目里第一个 `cand:` 当作 id（`gaps.py:156`）。

## 3. 一个取代真相

### 3.1 标记

- **格式**：在旧条目末尾单独加一行 `<!-- superseded | superseded_by:<新条目的 8 位 id> -->`。
  - 能被读取方的正则 `<!--[^>]*\|\s*superseded_by:(?!null\b)[^|]+?-->` 匹配；
  - 不含 `cand:`，不会改掉旧条目的身份；
  - `unit_id` 在计算前会去掉注释（`ddd_entry_lifecycle.py:817-825`），所以旧条目的 `unit_id` 不变。
  - 没有 cand 标记的旧条目，它的 CDF anchor（内容 sha256）会变。这是可以接受的：它已经被取代了。
- **写入**：用 `close_gap` 那条受管编辑路径，同样只在新单元 `written` 之后执行，同样每日重试。
  新增函数 `ingestion.supersede.mark(project, old, new_id, by, reason)`。

### 3.2 三个来源

| 来源 | 是否写标记 | 条件 |
|---|---|---|
| CDF 自己做的 refine/replace，目标在 DDD | 写 | writer 记下 supersedes 关系的同一次处理里，给旧条目写标记；旧条目原文保留，供审计 |
| S7 / S3 声明的 `supersedes` | 复核通过才写 | 见 §3.3 |
| 判官的 "contradicts"，没有声明 | 不写 | 只留在关系表里，列入每周自审和月度倒查的报告，由人用现有的撤回（redact）处理 |

### 3.3 声明和复核（S7、S3）

- **字段**：`CognitionUnit` 新增可选字段 `supersedes: [{id, head}]`，`id` 是 8 位 unit_id，`head` 是条目首行。
  只有 S7 的抽取 schema 和 S3 的报告 schema 暴露这个字段。其他来源（S1、S2、S6，包括外部内容）一律在入队时丢弃它，
  做法和 `closes` 只在 run 落地时保留一样。
- **S7 怎么知道 id**：S7 是会话的 fork，能看到 transcript 里的工具输出，`knowledge find/get` 会打印 id 和首行。
  会话里没出现过 id 的旧决定，S7 声明不了，这种情况交给 §3.2 的第三行（判官 + 人）。
- **复核**（全部确定性）：
  - `id` 能解析到同一项目 DDD 里恰好一条 live 条目，而且它的首行等于 `head`（`run_knowledge.entry_head`），防止拿错条目；
  - 旧条目的类型是 decision 或 model，或者它位于 PRODUCT/PROJECT；
  - 新单元已经 `written`，动作是 `add`；判官给出 `same`（也就是旧条目被 reinforce）时丢弃这个声明；
  - 不通过就不标，原因写进处理记录，新单元照常写入。

### 3.4 回填

把 SwarmAI 和其他 Brain 里 CDF 已经做过的 refine/replace 列成清单，确定性地逐条加上标记，幂等。
清单和结果写进报告。标记只是把旧条目从 recall 里隐藏，原文仍在、可以撤回，而且走受管编辑路径，会先归档。

## 4. 量结果：错误倒查

- **任务**：新增一个 job handler `brain_mistake_audit`，和 `ddd_self_audit` 同族，复用它的执行器原语、
  `_discover_projects` 和 `_write_job_result`。每月一次，也可以手动跑。agent 只读：`--tools Read,Grep,Glob,Bash(git log)`。
- **收集**：run 的 REVIEW/FIXING_SPEC 里确认并修复的 finding；EVOLUTION 的 correction；
  MEMORY 和 DailyActivity 里用户的纠正和 pitfall。
- **每个错误的判定**：
  - 知识类型：产品真相 / 机制坑 / 行为；
  - 事先有没有：`git log -S` 证明条目早于错误；
  - 有没有送到：pipeline 读 run 的知识文件和引用；
  - 是否过时。

  前两项和送达是确定性的；"同一机制""是否过时"由模型判断，标注为判断，只出报告，不做任何闸门。
- **报告**：放在 `Knowledge/JobResults/<date>-brain-mistake-audit.md`，开头就是 §1 那张表加上月对比，
  然后列出判官认定的"可能取代"，供人撤回。
- **基线**是今天这 38 条。要盯的是：
  - 产品真相里的"事先没有"（S7 是否起效）；
  - "内容过时"（§3 是否起效）。

## 5. 删除

- `.ddd-usage.json` 的写入方：`core/ddd_usage.py`、`record_ddd_hit` 的两处调用（`session_router.py:1966-1986`、`addressing.py:245`）、
  `addressing.record_cited` 和它的两处调用（`engine.py:2481`、`hook_bridge.py:365`），以及 `recall_replay.py` 里对它的替身。
  它没有任何读取方，DDD 衰减只看时间（`ddd_entry_lifecycle.py:18`）。已有的文件留在磁盘上，不删。
- `ingestion/usage.record_recalled_result` 里走不到的 DDD 分支（`usage.py:213-215`；聊天的统一 recall 不含 ddd 域，`session_router.py:1003`）。
- `2026-10-30-recall-that-measures-itself.md` 标为被本文取代。

## 6. 不变量（最终测试集，每条都用 mutation check）

- I1 CDF 对 DDD 条目做 refine/replace 之后：
  - 旧条目带上 `superseded_by:<新 id>`，recall 和 `knowledge find` 都不再返回它；
  - 原文保留，旧条目的 `unit_id` 不变；
  - 标记里不含 `cand:`；`ingestion.units` 解析出的旧条目身份不变。
- I2 一条合格的 S7/S3 声明会标记旧条目。以下任一情况都不标记任何条目，原因有记录，新单元照常写入：
  id 和首行对不上、解析到不止一条、类型不对、新单元没写入、判官给出 `same`。
- I3 S1、S2、S6 来源单元里的 `supersedes` 字段在入队时被丢弃。
- I4 判官给出 "contradicts" 而没有声明时，旧条目保持可见。
  这是防回退的守卫：注入"见到 contradicts 就标"这个变异，测试会变红。
- I5 用户正在改同一文件时，标记不会覆盖用户的修改，由每日维护重试，最终只标一次。
- I6 回填幂等：重复运行不会产生第二个标记。
- I7 生产代码里没有任何地方写 `.ddd-usage.json`。
- I8 倒查报告里的确定性字段（日期、送达、来源）在同一数据上重跑结果相同。

## 7. 不做什么，以及什么证据会让我们重新考虑

| 不做 | 为什么 | 什么时候重新考虑 |
|---|---|---|
| 送达时按条目类型过滤（只送"产品真相"） | 类型和"产品真相 / 机制坑"不对应：审计里 4 条机制坑被标成 model/principle/decision，PRODUCT 的 Non-Goals 全是 pitfall | 倒查显示产品真相出现"有但没送到" |
| `knowledge_query`（agent 写的解决方案搜索词） | 提升只体现在标注的相关性上，没有结果层面的证据；而产品真相的送达不是瓶颈 | 同上 |
| 写代码时按模式注入；产品自己的按仓库模式文件 | 实测每次 Edit 平均命中 27 行，会撑爆 hook 输出；`match:` 语法表达不了"锁里 await"；产品文件可以被受审的 run 自己改；`reflect_library.write` 没有生产调用方 | 倒查显示机制坑在评审之后才暴露（送到仍犯，或合入后才发现） |
| 按话题检索的回归样例集 | 今天的"有但没送到"全是机制坑，按话题检索的样例无从生成 | 和送达改动一起做 |
| 中文分词改动 | 对 DDD recall 没影响（9/43 vs 10/43）；对 Library 的影响没有结果层面的证据 | Library 出现结果层面的漏召回 |

## 8. 步骤和验证计划

| 步骤 | 内容 | 读取方 | 评审 |
|---|---|---|---|
| 1 | `supersede.mark` + writer 在 refine/replace 时调用它（§3.1、§3.2 第一行） | 已经存在（recall、find） | correctness、concurrency |
| 2 | 回填（§3.4）。先出清单给 XG 看，再执行 | 同上 | correctness |
| 3 | 声明和复核（§3.3）：字段、各来源的暴露范围、复核逻辑、S7 prompt 一句话 | 同上 | correctness、security（外部内容不能声明取代） |
| 4 | 删除（§5） | 无 | 不需要，测试证明 |
| 5 | `brain_mistake_audit` job，用今天的 38 条做基线 | XG 看报告 | correctness |
| 6 | SWARMAI.md §My Brain 加一段（§9 原文），改 `backend/context` 源文件，rebuild 后生效 | — | 无 |

每步只跑它触及的测试，加 mutation check；最后整体跑一次 I1–I8，逐行报告。
一个月后的倒查报告和今天的基线对比。

## 9. 治理文字（SWARMAI.md §My Brain，步骤 6）

> - **What a DDD is for, and when I use it.** A DDD holds what a project IS and what was
>   DECIDED — its decisions, models, principles and non-goals. I use it in chat, native
>   coding and pipeline alike whenever a judgment depends on that: proposing or choosing a
>   design, describing how a feature looks or works, judging whether a feature is complete.
>   Code-writing pitfalls are caught by the review experts and the repository's own review
>   patterns; the DDD keeps them as reference to look up. What the code does, I read in the
>   code; how I should behave is governance. A DDD that says something superseded is worse
>   than one that says nothing, so a replaced entry is marked and leaves recall. Its worth
>   is measured by outcome: the mistakes a recorded decision or lesson should have prevented.

## 10. 第二轮评审：§3 停在这里，交给 XG 决定

第二轮零上下文评审在 §3（取代）又找出 3 个 HIGH，均已对照源码核实：

- **H1**：CDF 现在已经没有 refine/replace 了。`run_unit_v2` 只产出 add、reinforce、reject、defer；
  最后一次 refine 发生在 10-29T17:52Z。取代现在以 `add` 加 `relation_hints` 的形式出现，
  而且 S4 的修正和判官的 "contradicts" 混在一起，分不出来（`_relation_hints` 丢掉了来源）。
  所以 §3.2 第一行要挂的钩子已经没有生产方。
- **H2**：历史上那 9 次 refine 的旧条目都是判官选的（`card3.py:141-193`），其中约一半是错的：
  - 新条目已被撤回；
  - 跨文件取代，旧内容没有带进新条目；
  - 判官当时自己就说两条"互不相交"。
  
  所以按 §3.4 回填，会把仍然有效的知识藏起来。
- **H3**：单独一行的标记，只有缩进时才算进条目。即使算进去，`parse_entries`、reclaim、Brain Hub 路由、
  `memory_injection` 也看不到它。标记必须写进条目已有的元数据注释（`_META_RE` / `to_comment`）。

还有一处关键：**§3.3 的"声明"机制对今天这 2 个"内容过时"的案例都无效。**

- E12：过时的是设计文档的一段文字，不是条目，而且新的决定当时根本没写进 DDD；
- E06：没有证据表明那个会话查到过旧条目的 id。

**按 AGENT §Iterative Refinement 的规则，§3 不再继续打补丁。** 三个选项：

- **A. 只保留"做决定的上下文声明取代"。** 字段对来源用白名单；标记写进元数据注释；首行做归一化后再比对；
  不挂 writer、不回填。代价中等，对已观察到的 2 个案例都无效。
- **B. 不做自动取代。** 过时内容由每周自审和月度倒查列出来，人用现有的撤回处理。没有新机制。
- **C. 本次先上 §4 倒查、§5 删除、§9 治理文字；取代机制等第一个月的倒查报告出来再设计。**
  先看清过时内容实际是从哪条路产生的：决定没写回、设计文档没更新、还是旧条目没降级。然后照着真实案例来设计。

**推荐 C。** 理由：

- 现有证据只有 2 个案例，而且两条显而易见的机制（writer 的 refine、判官的 contradicts）一条已经不存在、一条一半是错的；
- 现在建，等于对着没看清的问题打补丁。

C 的成本是：过时内容再存在一个月。月度倒查会把它点名列出来，人可以随时撤回。

## 11. 待 XG 确认

- 这个方案是否通过。
- 执行方式：改动跨 CDF 的写入和契约、DDD 文件回填、一个新 job，属于跨模块、改持久化数据的变更。
  按 R1 请你选：直接改，还是走 pipeline。
- 步骤 2 的回填清单，执行前会先给你看。
