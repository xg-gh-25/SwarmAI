---
title: Brain-driven：主文件只放最新的正确全集；变化只有新增、覆盖、撤回三种；记录在主文件之外
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: 待确认
supersedes: 2026-10-30-recall-that-measures-itself.md
follows: 2026-10-29-cdf-session-review.md, 2026-10-30-cdf-gap-lifecycle.md, 2026-10-30-brain-change-feed.md
---

# Brain-driven：主文件只放最新的正确全集；变化只有新增、覆盖、撤回三种；记录在主文件之外

状态：设计 v3，待 XG 确认。v1、v2 各经过一次零上下文评审。v2 的"在旧条目上打取代标记"被 XG 否决（2026-10-30），
原话：不在历史条目上打标记；过期、没用或者和新内容冲突的，要么覆盖，要么直接删除；记录留着就行，
但不放在主认知文件里；主文件就是最新的、正确内容的全集，里面不要有垃圾。

## 0. 结论

**DDD 管什么。** 判断取决于"这个产品是什么、做过什么决定、哪些不做"时，用 DDD。chat、native coding、pipeline 三种模式都一样。
- 写代码的机制坑：靠专家评审和仓库自己的评审模式接住，DDD 里留作可以查的参考；
- 代码实际怎么做：读代码；
- 我该怎么做事：靠治理规则。

**主文件的规则只有一条：只放最新的正确全集。** 进来的必须有用，有用的不能丢。所以对主文件的改动只有三种：

| 操作 | 含义 | 主文件里留下什么 |
|---|---|---|
| **新增** | 写入一条新条目 | 新条目 |
| **覆盖** | 新条目原地替换旧条目 | 只有新条目 |
| **撤回** | 删掉过期、没用或已完成的条目 | 什么都不留 |

被替换、被删掉的旧文本，以及谁在什么时候、为什么改的，全部记在主文件之外：
写入日志 `ingestion_writes`、`.archive/`、撤回表 `redactions` 及其存档。

**基本统计**按 Brain、按时间段给出"新增了什么、覆盖了什么、撤回了什么"，读的是上面这份日志。
每一条都能看到原文、去向和理由。

**DDD 有没有用**，看结果：已经记录过的决定和教训，有没有防住本该防住的错（§5）。

## 1. 证据（2026-10-30）

**错误倒查**：38 个真实错误（10-16 到 10-30），用 git 历史证明条目早于错误。
明细见 `Knowledge/Reports/2026-10-30-ddd-recall-eval/mistake_audit.json`。

| 需要的知识 | 数 | 事先没有 | 有但没送到 | 送到仍犯 | DDD 内容过时 | 无法判断 |
|---|---|---|---|---|---|---|
| 写代码的机制坑 | 22 | 10 | 11 | 1 | 0 | 0 |
| 产品事实和决定 | 7 | 5 | 0 | 0 | 2 | 0 |
| 我自己的行为 | 9 | 7 | 0 | 0 | 0 | 2 |

产品真相出错，全部来自"事先没有"或"内容过时"，没有一例是"有但没送到"。所以这次不改送达方式，只解决两件事：
主文件不说过时的话，以及主文件里不留垃圾。

**主文件里今天的垃圾**（2026-10-30 实数）：

- **refine 的旧条目**：CDF 做 refine 时保留旧条目，新条目里带着旧内容（`writer.py:544-546` 的注释）。
  DDD 的读取方看不到取代关系，所以新旧两条都被检索出来。SwarmAI 上有 9 次 refine 碰到 DDD，其中 4 次确实是同文件合并。
- **撤回的墓碑**：撤回会原地写一行 `- [redacted] **Withdrawn** [已撤回…]`（`governed_edit.py:491` `redact`）。
  SwarmAI TECH.md 里有 1 条，EVOLUTION.md 里也有。
- **已完成的事项**：已关闭的 gap 和完成的事项改成 `- [x]` 后还留在原地（`gaps.close_gap`）。
  SwarmAI PROJECT.md 有 16 条，IMPROVEMENT.md 有 8 条。
- **记账注释**：SwarmAI TECH.md 有 546 行 `<!-- ref:… | last:… | decay:… -->`、228 行 `<!-- maturity: … -->`，
  PROJECT.md 分别有 162 行和 81 行。其中引用次数从 2026-06 起就没有代码在写（`ddd_entry_lifecycle.py:16-18`）。

## 2. 三种操作怎么做（落在现有机制上）

### 2.1 新增

不变：CDF 的 add。

### 2.2 覆盖

**现状：**
- `replace` 会删掉旧条目的字节并归档（`writer.py:547`），但只对带 `cand:` 标记的旧条目生效，人写的条目替换不了。
- `refine` 把旧内容并进新条目，却留下旧条目（`writer.py:530-546`）。
- CDF v2 的主流程现在只产出 add、reinforce、reject、defer（第二轮评审已核实），覆盖完全靠单元在写入时声明。

**改为：** 覆盖只有一个写法，就是 `replace`。旧条目整条删除并归档，新条目写在它原来的位置。以下两种情况允许覆盖：

1. **无损合并**（可以自动执行）：
   - 新旧条目在同一个文件；
   - 新条目的正文包含旧条目的完整正文（`_plain_unit_body(old)`，确定性的字符串包含判断）；
   - 新条目没有被撤回。

   这样旧条目里的内容不会丢，删掉它不损失任何东西。
   今天 `refine` 走的就是这条路，只是没有删旧条目。现在改成真的删，人写的条目也一样。
2. **被纠正**（旧内容是错的，有意丢弃），必须由一个有资格的来源声明：
   - **做决定的上下文**：S7 会话复盘、S3 run 报告，单元带上 `overrides: [{id, head}]`。
     代码复核：`id` 和首行（归一化后）恰好对上同一项目里的一条现存条目，新单元已写入，判官给出的不是 `same`。
     这个字段对来源用白名单，只有 S7 和 S3 能带；其他来源的会被剥掉，`relation_hint` 也一起收紧。
   - **用户**：在 Brain 变化流或 Brain Hub 里，对一条判官认定"可能冲突"的条目选择"用新的覆盖"。

判官单独给出的 "contradicts" 不会自动覆盖，因为它大约一半是错的。它只会出现在变化流和月度报告里，等人来选：覆盖、撤回或保留。

### 2.3 撤回

撤回就是删除条目字节并归档，主文件里不留墓碑。三个触发方：

- 用户（现有的撤回操作）；
- run 关闭了一个 gap（`close_declared`：从现在的标 `[x]` 改为撤回）；
- 一个事项完成了（PROJECT 的 `- [x]`）。

撤回的原文和理由仍然写进 `redactions` 和它的存档。
"同样的内容以后不能再写回来"这类检查，改为读 `redactions` 表，不再依赖文件里的墓碑。
这些依赖方在实现时逐个 grep 确认（AGENT R27）。

### 2.4 记录与统计

- **记录**：每一次新增、覆盖、撤回，都是 `ingestion_writes` 里的一行，带改前改后的哈希和归档路径。
  撤回另外记进 `redactions`。主文件里不写任何记录。
- **统计**：新增查询 `brain_changes(project, since)`，返回每条变化的操作、条目首行、原文（覆盖和撤回的取自归档）、
  来源和理由。读取方有两个：
  - Brain 变化流（`2026-10-30-brain-change-feed.md`），它本来就要按 CDF 写入的条目列出变化，这里给它补上覆盖和撤回两类；
  - 月度报告（§5）。

### 2.5 主文件里的记账注释

`<!-- ref:… | last:… | decay:… -->` 和 `<!-- maturity: … -->` 也是写在主文件里的记录。
处理分两步，不能一刀切，要先确认谁还在读：

- **先列读取方**：已知会读它们的有 `ddd_entry_lifecycle`（`last:`、`decay:`）、`ddd_maturity.py`、`memory_decay.py`、
  `ddd_format.py`、`recall_multi.py`。逐个确认是不是生产路径、读了做什么。
- **再定去留**：
  - 没有生产读取方的，直接删掉对应的写入和现有的注释行；
  - 仍然需要的状态，比如衰减要用的最后使用时间，搬到主文件之外（`unit_usage` 已经按单元记录了使用），主文件里不再写。

这一部分单独成一步（§7 第 5 步），先出读取方清单给 XG 看。

## 3. 清理今天的垃圾（一次性，幂等）

| 对象 | 处理 | 判定 |
|---|---|---|
| refine 留下的旧条目 | 删除（归档） | 只删同文件、而且新条目正文完整包含旧条目正文的那些；其余列出来给人看，不动 |
| 撤回墓碑 | 删除；原文已在存档 | 墓碑行的格式是确定的 |
| 已关闭的 gap、完成的事项 | 撤回 | `- [x]` 前缀 |
| 记账注释 | 按 §2.5 的结论处理 | — |

执行前先出清单（每个 Brain、每类多少条、逐条首行），XG 看过再执行。执行结果出现在统计里。

## 4. 删除死代码

- `.ddd-usage.json` 的写入方：`core/ddd_usage.py`、`record_ddd_hit` 的两处调用（`session_router.py:1966-1986`、`addressing.py:245`）、
  `addressing.record_cited` 和它的两处调用（`engine.py:2481`、`hook_bridge.py:365`），以及 `recall_replay.py` 里对它的替身。
  它没有任何读取方。
- `ingestion/usage.record_recalled_result` 里走不到的 DDD 分支（`usage.py:213-215`）。
- `refine` 这个动作本身：它的功能并进 §2.2 的无损合并覆盖。实现时确认它没有别的调用方。

## 5. 量结果：月度错误倒查

- **任务**：job handler `brain_mistake_audit`，和 `ddd_self_audit` 同族，复用它的执行器原语、`_discover_projects`、`_write_job_result`。
  agent 只读。
- **收集**：run 的 REVIEW/FIXING_SPEC 里确认并修复的 finding；EVOLUTION 的 correction；
  MEMORY 和 DailyActivity 里用户的纠正和 pitfall。
- **判定**：
  - 确定性的部分：知识类型的来源、事先有没有（`git log -S`）、有没有送到（run 的知识文件和引用）；
  - 由模型判断的部分："同一机制""是否过时"，报告里标为判断，只出报告，不做任何闸门。
- **报告**：`Knowledge/JobResults/<date>-brain-mistake-audit.md`，内容依次是：
  - §1 那张表和上月对比；
  - 本月的变化统计（§2.4）；
  - 判官认定"可能冲突"、等人选择的条目。
- **基线**：今天这 38 条。要盯的两个数：产品真相的"事先没有"（S7 是否起效）、"内容过时"（覆盖和撤回是否起效）。

## 6. 不变量（最终测试集，每条都用 mutation check：撤回修复，测试变红）

- I1 无损合并覆盖：
  - 旧条目的字节从主文件消失，新条目在原位置；
  - 归档里有旧原文，`ingestion_writes` 有记录；
  - 人写的条目同样适用。
- I2 新条目正文不完整包含旧条目正文时，不会自动覆盖。
- I3 声明覆盖：S7/S3 的 `overrides` 通过复核才执行；以下任一情况都不覆盖、记录原因，新单元照常写入：
  id 和首行对不上、对上不止一条、新单元没写入、判官给出 `same`。
- I4 S1、S2、S6（以及任何不在白名单里的来源）带的 `overrides` 和 `relation_hint` 在入队时被剥掉。
- I5 判官的 "contradicts" 不会改动任何现存条目。这是防回退的守卫：注入"见到 contradicts 就覆盖"这个变异，测试会变红。
- I6 撤回之后主文件里没有墓碑，`redactions` 有记录。同一内容再次进入时，仍然按 `redactions` 拦下。
- I7 run 关闭 gap、事项完成之后，主文件里不再有那一条。
- I8 `brain_changes` 给出的新增、覆盖、撤回，和 `ingestion_writes` 逐条对得上；覆盖和撤回能拿到原文。
- I9 清理幂等：重复运行不再改动任何文件。
- I10 生产代码里没有任何地方写 `.ddd-usage.json`。

## 7. 步骤和验证计划

| 步骤 | 内容 | 读取方 | 评审 |
|---|---|---|---|
| 1 | 统计：`brain_changes` 查询，加上变化流的数据源 | 变化流、月度报告 | correctness |
| 2 | 撤回不留墓碑；gap 关闭和事项完成改为撤回（§2.3）。先 grep 所有依赖墓碑和 `[x]` 的地方 | `redactions` 的读取方 | correctness、state-machine |
| 3 | 覆盖（§2.2）：无损合并改为 replace，人写的条目也能覆盖；S7/S3 的 `overrides`；来源白名单 | recall、find 不用改（旧条目已经不在了） | correctness、security、concurrency |
| 4 | 一次性清理（§3），先出清单 | 统计（第 1 步） | correctness |
| 5 | 记账注释（§2.5）：先出读取方清单给 XG，再定去留 | — | correctness |
| 6 | 删死代码（§4） | 无 | 不需要，测试证明 |
| 7 | 月度倒查 job（§5） | XG 看报告 | correctness |
| 8 | SWARMAI.md §My Brain 加一段（§8 原文），改源文件，rebuild 后生效 | — | 无 |

每步只跑它触及的测试，加 mutation check；最后整体跑一次 I1–I10，逐行报告。

## 8. 治理文字（SWARMAI.md §My Brain，步骤 8）

> - **What a DDD is for, and how it stays true.** A DDD holds what a project IS and what was
>   DECIDED — its decisions, models, principles and non-goals. I use it in chat, native
>   coding and pipeline alike whenever a judgment depends on that: proposing or choosing a
>   design, describing how a feature looks or works, judging whether a feature is complete.
>   Code-writing pitfalls are caught by the review experts and the repository's own review
>   patterns; the DDD keeps them as reference to look up. Its main files are the current,
>   correct whole and nothing else: a change is an addition, an override (the new entry
>   replaces the old in place) or a withdrawal (the entry is removed). What was replaced or
>   removed, when and why, is recorded outside the main files. Its worth is measured by
>   outcome: the mistakes a recorded decision or lesson should have prevented.

## 9. 不做什么

| 不做 | 为什么 | 什么时候重新考虑 |
|---|---|---|
| 在旧条目上打取代标记 | XG 否决：主文件里不留历史 | — |
| 送达时按条目类型过滤 | 类型和"产品真相 / 机制坑"对不上（审计里 4 条反例） | 倒查显示产品真相出现"有但没送到" |
| `knowledge_query` | 没有结果层面的证据 | 同上 |
| 写代码时按模式注入 | 每次 Edit 平均命中 27 行；`match:` 语法表达不了真实触发条件 | 倒查显示机制坑在评审之后才暴露 |
| 中文分词改动 | 对 DDD 没影响；对 Library 的影响没有结果层面的证据 | Library 出现结果层面的漏召回 |

## 10. 待 XG 确认

- 这个方案是否通过。
- 执行方式：改动跨 CDF 的写入和契约、撤回和 gap 关闭、DDD 文件清理、一个新 job，属于跨模块、改持久化数据的变更。
  按 R1 请你选：直接改，还是走 pipeline。
- 第 4 步的清理清单和第 5 步的读取方清单，执行前都会先给你看。
