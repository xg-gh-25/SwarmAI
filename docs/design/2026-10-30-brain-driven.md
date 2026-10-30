---
title: Brain-driven：认知文件只有给人看的内容；变化只有新增、覆盖、撤回；身份和记录在文件之外
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: 待确认
supersedes: 2026-10-30-recall-that-measures-itself.md
follows: 2026-10-29-cdf-session-review.md, 2026-10-30-cdf-gap-lifecycle.md, 2026-10-30-brain-change-feed.md
---

# Brain-driven：认知文件只有给人看的内容；变化只有新增、覆盖、撤回；身份和记录在文件之外

状态：设计 v4，待 XG 确认。

- **XG 定的方向**（2026-10-30）：
  - 进来的要有用，有用的不能丢；
  - 过期、没用、和新内容冲突的，要么覆盖，要么删除；
  - 历史条目上不打任何标记；
  - 认知文件是给人看的，只留有用的内容，不留操作标签；
  - 操作记录单独放进记录文档或存储。
- **评审情况**：v1、v2、v3 各经过一次零上下文评审，被推翻和修正的内容见 §14。

## 0. 结论

**DDD 管什么。** 判断取决于"这个产品是什么、做过什么决定、哪些不做"时，用 DDD。chat、native coding、pipeline 三种模式都一样。
- 写代码的机制坑：靠专家评审和仓库自己的评审模式接住，DDD 里留作可以查的参考；
- 代码实际怎么做：读代码；
- 我该怎么做事：靠治理规则。

**认知文件只放最新的正确全集，而且只有内容。** 涉及的文件：各 Brain 的 PRODUCT、PROJECT、TECH、IMPROVEMENT，以及 MEMORY、EVOLUTION、STEERING、USER、TOOLS。
- 文件里只有条目的标题、正文、类型、日期；
- 条目的身份、出处、taint、衰减状态、已关闭的历史，全部放在记录存储里；
- 对文件的改动只有三种：

| 操作 | 主文件里留下什么 | 记录存储里留下什么 |
|---|---|---|
| 新增 | 新条目 | 条目记录 + 一条写入日志 |
| 覆盖 | 只有新条目，在旧条目原来的位置 | 旧原文、去向、理由 |
| 撤回 | 什么都不留 | 原文、状态（撤回 / 关闭 / 完成）、理由，以及防止它再被写回的指纹 |

**基本统计**：按 Brain、按时间段列出新增、覆盖、撤回了什么，数据来自记录存储。

**DDD 有没有用**，看结果：月度错误倒查（§8）。

**分两期落地**，同一个设计：
- **A 期**：三种操作、撤回不留墓碑、防写回、统计、清理已有的重复和墓碑。不动条目身份，改动面小。
- **B 期**：操作标签全部移出文件，身份改由记录存储管理，库里的锚点一次性改键。
  涉及 40–50 个模块，单独作为一次大迁移（AGENT R26）。

## 1. 证据（2026-10-30）

**错误倒查**：38 个真实错误（10-16 到 10-30），用 git 历史证明条目早于错误。
明细见 `Knowledge/Reports/2026-10-30-ddd-recall-eval/mistake_audit.json`。

| 需要的知识 | 数 | 事先没有 | 有但没送到 | 送到仍犯 | DDD 内容过时 | 无法判断 |
|---|---|---|---|---|---|---|
| 写代码的机制坑 | 22 | 10 | 11 | 1 | 0 | 0 |
| 产品事实和决定 | 7 | 5 | 0 | 0 | 2 | 0 |
| 我自己的行为 | 9 | 7 | 0 | 0 | 0 | 2 |

产品真相出错，全部来自"事先没有"或"内容过时"，没有一例是"有但没送到"。所以这次不改送达方式。

**认知文件里今天不该有的东西**：41 个文件、1527K 字符，其中注释 247K（16%）。

| 内容 | 数量 | 现在承担的功能（读取方） |
|---|---|---|
| `<!-- ref:… \| last:… \| decay:… \| source:… \| taint:… -->` | 2339 | 衰减（`context_directory_loader._filter_dormant_entries`、`ddd_entry_lifecycle`）、出处和信任（`writer._unit_external` → baseline trust → `provenance.resolve`），约 20 个模块调 `parse_entries` |
| `<!-- maturity: … -->` | 728 | `ddd_maturity.py` |
| `<!-- cand:<id>.unit -->` | 196 | CDF 身份（`units._unit`），gap id，崩溃恢复（`writer.py:688`），redactions 和关系表的锚点 |
| `<!-- valid_from \| superseded_by -->`（MEMORY） | — | `memory_index` |
| `<!-- folded archived= -->` | — | `data_point_folding` |
| `<!-- channel -->`（STEERING） | — | 非 owner channel 的安全闸门 |
| 正文里的 run id、`source:manual` | 1322、1716 | 出处 |
| `- [x]`（已关闭的 gap、完成的事项） | 26 | gap 复发判断（`pipeline_v2.py:620`、`gaps.is_closed_gap`）、`ddd_drift_fix.py:10`、`ddd_self_audit.py:210` |
| 撤回墓碑 | 2 | 只有 `queries.py:529` 里的一个计数，而且这个计数的 join 写错了，永远是 0 |
| refine 留下的旧条目 | 4 对确认是同文件合并 | 无（`writer.py:544-546` 的注释误以为读取方会隐藏它） |

## 2. 三种操作的规则

### 2.1 新增

不变。

### 2.2 覆盖

覆盖只有一个写法：删掉旧条目的字节（先归档），把新条目写在原位置，同时在记录存储里记下旧原文、去向和理由。
允许覆盖的只有下面两种情况。

1. **合并**：判官对同一文件里的一条现存条目给出 `adds`（同一主题，新单元补充了新内容）。
   - 由 writer 把两者合成一条：旧条目的完整内容，包括标题、类型和日期，加上新内容；
   - 合成结果覆盖旧条目；
   - 类型取两者中衰减豁免的那个，所以 decision 不会降成 guideline；
   - 今天 `adds` 的做法是写一条新条目、挂一条 `derived_from` 关系，结果就是同一主题出现两条。改成合并后，什么都不丢，也不会重复。
2. **纠正**（旧内容是错的，有意丢弃），必须由有资格的来源声明：
   - **做决定的上下文**：S7 会话复盘、S3 run 报告，单元带 `overrides: [{id, head}]`。
     代码复核：
     - `id` 和首行（去掉注释、空白归一化后比较）恰好对上同一项目里的一条现存条目；
     - 新单元已经写入，判官给出的不是 `same`。

     这个字段对来源用白名单，只有 S7 和 S3 能带。其他来源的 `overrides` 和 `relation_hint` 在入队时一律剥掉
     （今天 `relation_hint` 能从任意来源透传，`extract_llm.py:192`）。
   - **用户**：在 Brain 变化流或 Brain Hub 里，对判官认定"可能冲突"的条目选择"用新的覆盖"。

**边界：**
- 人写的条目只能由用户覆盖，CDF 永远不能自动改动它。
- STEERING、USER、TOOLS 里，CDF 只能动"系统追加"部分（`writer.py:35`、`:147`），覆盖也不例外。

**判官单独给出的 "contradicts"**：不改动任何条目，也不再隐藏旧条目。今天 MEMORY 的注入会按判官的 supersedes 关系隐藏旧条目（`memory_injection.py:465-471`），这一条删掉。
这类条目进入变化流和月度报告，由人选择覆盖、撤回或保留。

### 2.3 撤回

撤回就是删掉条目字节（先归档），主文件里不留墓碑，记录存储里留下原文、状态和理由。触发方：

- 用户撤回；
- run 关闭了 gap（`close_declared`：从现在的标 `[x]` 改为撤回，状态记 `closed`）；
- 事项完成（状态记 `done`）。

**需要历史的读取方，改读记录存储**：
- gap 复发判断（`is_closed_gap`、`pipeline_v2.py:620-626`）：读状态为 `closed` 的记录；
- `ddd_drift_fix` 和 `ddd_self_audit` 引用"已完成"的地方：读状态为 `done` / `closed` 的记录。

这几个读取方要先迁移完，撤回才能启用（§11）。

**防写回**（今天并不存在，需要新建）：
- 撤回记录带上内容指纹，即去掉注释、空白归一化后正文的 sha256；
- CDF 在判官之前先比指纹：完全相同的单元直接拒收，原因记为 `withdrawn`；
- 不完全相同的，把相似的撤回原文作为"已撤回"命中交给判官；判官给出 `same` 就拒收。

实测已经发生过一次：node_modules 那条教训 10-29 从 EVOLUTION 撤回后，又被写进了 TECH.md。

## 3. 记录存储和身份（B 期）

- **存储**：放在 ingestion.db，不另建。新增一张表 `unit_records`，一行对应文件里的一条条目：
  - `record_id`：稳定 id，由 CDF 分配，永不复用；
  - `file`、`section`、`content_hash`、`head`；
  - `type`、`source`、`run_id` / `session_id`、`trust`（今天的 taint）、`status`（live / overridden / withdrawn / closed / done）、`body`（不再 live 的条目的原文）、`created_at`、`updated_at`。
  - `content_hash` 的计算口径：去掉所有注释、空白归一化之后的正文。这样标签洗掉前后，同一条目的哈希相同。
- **taint 的语义保持今天的样子**：只作为出处和信任的依据，不决定注入（今天只有 TIMELINE 的 "Not injected" 决定不注入）。
  没有记录的条目，按今天"没有标记的条目"对待，也就是人写的、可信的。这样数据库丢失或新装机器时，注入行为不会变。
- **身份怎么对上文件**：
  - CDF 写入时，记录和文件在同一个分步写入里更新，`ingestion_writes` 加一步；
  - 崩溃恢复不再看文件里有没有 `cand:`，改看文件里是否存在 `content_hash` 对应的条目。
- **文件被 CDF 以外的路径改动**：包括人、agent 的 Edit、`s_persist`、`data_point_folding` 等。
  在下一次 CDF 写这个文件之前，以及每日维护时，按 `content_hash` 对账：
  - 哈希没变的条目，记录不变；
  - 同一节里恰好消失一条、新出现一条的，按"用户覆盖"配对，记录（含出处、信任）跟过去；
  - 其他情况，消失的按"用户撤回"、新出现的按"用户新增"登记，原文都已归档，不丢。

  对账不放在读取的热路径上。
- **产品代码写认知文件只有一个入口**：`data_point_folding`、`context_health_hook`、`brain_bootstrap/apply`、`memory_health` 等改走 `governed_edit`，声明自己的来源，不再伪装成人的修改。
- **改键**（B 期的核心，一次完成，先备份、后校验）：
  - 今天约九成锚点是"整段原文（含注释）的 sha256"，例如 `unit_usage` 里 320 个锚点中有 299 个，还有关系表的 64 个对象、`claim_support` 19 条，以及大部分 `corpus_unit` 定位、`injection_selection`；
  - 对每一个现存条目，算出旧锚点 → `record_id`，在一个事务里改写所有引用它的表；
  - 改写前备份 ingestion.db，改写后逐表核对行数和引用完整性，有一项不对就整体回滚；
  - 改键完成之后，才允许洗掉文件里的标签。
- **EVOLUTION 的编号**（`C064` 等）：编号保留在文件里，因为人要用它互相引用。
  新编号从记录存储里"曾经用过的最大编号"往上加，不再取文件里现有的最大值（`writer.py:458`），被撤回或覆盖的编号不会被复用。
- **STEERING 的 `<!-- channel -->`**：这是安全闸门，B 期把它搬进记录存储时，闸门的读取方必须先迁移，并补一个"标签移走前后，闸门结论不变"的测试。
- **Brain 打包、同步到 Hive**：包里另带一个 `.records.json`（不是认知文件），装这个 Brain 的条目记录，不含使用记录。
  接收方用它恢复出处和信任；没有这个文件时，条目按"人写的"对待，和今天没有标记时一样。

## 4. 统计

- **查询**：新增 `brain_changes(project, since)`，从 `unit_records` 和 `ingestion_writes` 列出每一条新增、覆盖、撤回：
  条目首行、原文（覆盖和撤回的取自记录）、来源、理由、时间。
- **读取方**：
  - Brain 变化流（`2026-10-30-brain-change-feed.md`），它本来就要按 CDF 写入的条目列出变化，这里补上覆盖和撤回两类；
  - 月度报告（§8）。
- **A 期没有 `unit_records`**：统计先读 `ingestion_writes`、`redactions` 和归档。
  `queries.py:538` 里那个永远为 0 的撤回计数一并修掉。

## 5. 清理今天已有的问题（一次性，幂等，执行前先给 XG 看清单）

| 对象 | 处理 | 判定 | 期 |
|---|---|---|---|
| refine 留下的旧条目 | 撤回（归档） | 同一文件，且新条目包含旧条目的完整内容（标题、类型、日期、正文），旧正文不短于 40 字符。其余列出来给人看，不动 | A |
| 撤回墓碑 | 删除；原文已经在存档里 | 墓碑行的格式是确定的；同时给 `redactions` 补上指纹 | A |
| 已关闭的 gap、完成的事项 | 撤回，状态记 closed / done | `- [x]`，而且 §2.3 的读取方已经迁移完 | A |
| 操作标签 | 改键完成后洗掉 | 洗完后，每条条目的 `content_hash` 都能在记录里对上 | B |

## 6. 删除死代码

- `.ddd-usage.json` 的写入方：`core/ddd_usage.py`、`record_ddd_hit` 的两处调用、`addressing.record_cited` 和它的两处调用，
  以及 `recall_replay.py` 里对它的替身。它没有任何读取方。
- `usage.record_recalled_result` 里走不到的 DDD 分支（`usage.py:213-215`）。
- `refine` 动作：它的功能已经被 §2.2 的合并取代。
- B 期：只为文件内标签服务的解析和写回代码，包括 `_META_RE` 的往返、文件内 `cand:` 的解析、`render_ddd_unit` 的注释部分、衰减状态的写回。

## 7. 不变量（最终测试集，每条都用 mutation check；I11–I15 属于 B 期）

- I1 合并：判官给出 `adds`、同一文件时，主文件里只有一条合成后的条目，包含旧条目的完整内容；旧原文在归档和记录里。
- I2 纠正覆盖只在合格声明（S7/S3 白名单，id 和首行复核通过）或用户操作时发生。以下情况都不改动任何条目：
  判官的 `contradicts`、来源不在白名单、复核失败、判官给出 `same`。
- I3 CDF 永远不改人写的条目，也永远不改 STEERING、USER、TOOLS 的非系统部分。
- I4 MEMORY 的注入不再因为判官的 supersedes 关系隐藏任何条目。
- I5 撤回、关闭、完成之后，主文件里没有那一条，也没有墓碑；记录里有原文和状态。
- I6 gap 复发判断、`ddd_drift_fix`、自审在条目撤回之后，结论和撤回之前相同。
- I7 与撤回原文指纹相同的单元，在判官之前就被拒收。
- I8 `brain_changes` 给出的变化和写入日志逐条对得上，覆盖和撤回的原文都能拿到；撤回计数不再永远是 0。
- I9 清理幂等。
- I10 生产代码里没有任何地方写 `.ddd-usage.json`。
- I11 改键之后，所有表的引用都完整：每个锚点都能解析到一条记录，行数和改键前相同。中途失败就整体回滚。
- I12 洗掉标签之后，认知文件里没有 `<!-- … -->`、run id、`source:` 标签；每条条目都能在记录里查到身份、出处、信任。
- I13 注入、衰减、闸门（包括 STEERING channel）在标签洗掉前后结论相同。数据库清空之后，注入结论和今天"没有标记"时相同。
- I14 人或其他路径改了文件之后：没变的条目记录不变；一出一进的按覆盖配对；其余按撤回和新增登记；原文都不丢。
- I15 EVOLUTION 的新编号不会复用被撤回或覆盖的编号。

## 8. 量结果：月度错误倒查

- **任务**：job handler `brain_mistake_audit`，和 `ddd_self_audit` 同族，复用它的执行器原语、`_discover_projects`、`_write_job_result`，agent 只读。
- **收集**：run 的 REVIEW / FIXING_SPEC、EVOLUTION 的 correction、MEMORY 和 DailyActivity 里用户的纠正和 pitfall。
- **判定**：
  - 确定性的部分：事先有没有（`git log -S`）、有没有送到（run 的知识文件和引用）；
  - 由模型判断的部分："同一机制""是否过时"，报告里标为判断，只出报告，不做闸门。
- **报告**：`Knowledge/JobResults/<date>-brain-mistake-audit.md`，内容依次是：
  - §1 那张表和上月对比；
  - 本月的变化统计（§4）；
  - 判官认定"可能冲突"、等人选择的条目。
- **基线**：今天这 38 条。要盯的两个数：产品真相的"事先没有"（S7 是否起效）、"内容过时"（覆盖和撤回是否起效）。

## 9. 不做什么

| 不做 | 为什么 | 什么时候重新考虑 |
|---|---|---|
| 在旧条目上打取代标记 | XG 否决 | — |
| 送达时按条目类型过滤 | 类型和"产品真相 / 机制坑"对不上（审计里 4 条反例） | 倒查显示产品真相出现"有但没送到" |
| `knowledge_query` | 没有结果层面的证据 | 同上 |
| 写代码时按模式注入 | 每次 Edit 平均命中 27 行；`match:` 语法表达不了真实触发条件 | 倒查显示机制坑在评审之后才暴露 |
| 中文分词改动 | 对 DDD 没影响；对 Library 没有结果层面的证据 | Library 出现结果层面的漏召回 |
| 在读取时对账 | 读取路径是只读连接，在热路径上 | — |

## 10. 治理文字（SWARMAI.md §My Brain，A 期最后一步）

> - **What a DDD is for, and how it stays true.** A DDD holds what a project IS and what was
>   DECIDED — its decisions, models, principles and non-goals. I use it in chat, native
>   coding and pipeline alike whenever a judgment depends on that: proposing or choosing a
>   design, describing how a feature looks or works, judging whether a feature is complete.
>   Code-writing pitfalls are caught by the review experts and the repository's own review
>   patterns; the DDD keeps them as reference to look up. Its files are written for people
>   and hold the current, correct whole and nothing else: a change is an addition, an
>   override (the new entry replaces the old in place) or a withdrawal (the entry is
>   removed); what was replaced or removed, when and why, and every operational tag live in
>   the record store, never in the files. Its worth is measured by outcome: the mistakes a
>   recorded decision or lesson should have prevented.

## 11. 步骤和验证计划

**A 期**（读取方先于写入方）：

| 步骤 | 内容 | 读取方 | 评审 |
|---|---|---|---|
| A1 | 统计 `brain_changes`；修撤回计数 | 变化流、月度报告 | correctness |
| A2 | 需要历史的读取方改读撤回记录：gap 复发、`ddd_drift_fix`、自审 | 撤回记录 | correctness、state-machine |
| A3 | 防写回：redactions 加指纹；判官前先比指纹；给判官看已撤回的原文 | CDF 处理流程 | correctness、security |
| A4 | 撤回不留墓碑；gap 关闭、事项完成改为撤回 | A2、A3 已经落地 | correctness、state-machine |
| A5 | 覆盖：`adds` 改为合并；S7/S3 的 `overrides` 加白名单、复核；剥掉 `relation_hint`；删掉判官 supersedes 隐藏 MEMORY 条目的逻辑；人写条目和系统追加部分的边界 | — | correctness、security、concurrency |
| A6 | 清理（§5 的 A 期部分），先出清单 | A1 的统计 | correctness |
| A7 | 删死代码（§6 的 A 期部分） | 无 | 不需要，测试证明 |
| A8 | 月度倒查 job；治理文字 | XG 看报告 | correctness |

**B 期**（单独一次迁移，AGENT R26）：

| 步骤 | 内容 |
|---|---|
| B0 | 读取方和写入方清单：`parse_entries`、`parse_units`、`cand:`、`ref:`、`maturity:`、`valid_from`、`folded`、`channel` 的全部生产调用方（`code_intel.impact` 加 grep），先给 XG 看 |
| B1 | 建 `unit_records`，从现有文件回填；双写 |
| B2 | 产品代码写认知文件统一走 `governed_edit`，各自声明来源 |
| B3 | 读取方逐个改读记录，每个都要证明"带标签和不带标签结论相同"（I13） |
| B4 | 改键（I11），先备份、后校验、失败整体回滚 |
| B5 | 停写标签，洗掉所有认知文件里的标签（先备份） |
| B6 | 删掉只为标签服务的代码 |

每步只跑它触及的测试，加 mutation check。每期结束时整体跑一次该期的不变量（A：I1–I10；B：I11–I15），逐行报告。

## 12. 待 XG 确认

1. 文件里保留**类型标签、日期、EVOLUTION 编号**三样内容。默认保留，理由：人读得懂，也有用。要不要也移走？
2. A、B 两期是否都通过。B 期涉及 40–50 个模块和一次数据库改键，风险高于 A 期。
3. 执行方式：直接改，还是走 pipeline。B 期我建议走 pipeline。
4. A6 的清理清单、B0 的读取方清单、B4 的改键方案，执行前都会先给你看。

## 13. 风险

- **B 期改键涉及 ingestion.db，那是不可替代的用户数据**：先备份，校验备份能打开且完整，改键失败整体回滚。没有备份就不执行（AGENT § Safety、STEERING #20）。
- **对账配对规则**："同一节里恰好一出一进"才配对，其余按撤回和新增登记。这样永远不会丢原文，但配对失败时，那一条会丢掉出处和信任，改按人写的对待。

## 14. 评审记录

- **v1 评审**：推翻了"按类型过滤送达"和"写代码时按模式注入"。
- **v2 评审**：推翻了"writer 的 refine 钩子"和"回填取代标记"。
- **XG**：否决了在历史条目上打标记，定下"新增 / 覆盖 / 撤回"，以及认知文件只放内容。
- **v3 评审**（本版据此修正）：
  - taint 今天并不决定注入，"没有记录就不注入"会停掉 166 条正常注入的条目 → 改为保持今天的语义（§3）；
  - 身份移出文件必须给库里的锚点改键，崩溃恢复和 gap id 也依赖文件里的 cand → 加入改键步骤和恢复方式（§3）；
  - 防写回今天不存在 → 新建（§2.3）；
  - 撤回 `[x]` 会破坏 gap 复发判断等读取方 → 先迁移读取方（§2.3）；
  - 覆盖人写的条目会越过 STEERING、USER、TOOLS 的边界 → 加边界（§2.2）；
  - "包含旧正文"不足以证明不丢内容 → 合并改由 writer 合成（§2.2）；
  - 判官的 contradicts 今天会隐藏 MEMORY 条目 → 删掉（§2.2）；
  - EVOLUTION 编号会被复用 → 改从记录分配（§3）；
  - 标签清单不完整 → 补上 valid_from、folded、channel 和衰减过滤（§1）；
  - 读取方数量约为原估计的 3 倍 → 拆成 B 期（§11）。
