---
title: Brain-driven：主文件就是最新的有效全集
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: 待确认
supersedes: 2026-10-30-recall-that-measures-itself.md
follows: 2026-10-29-cdf-session-review.md, 2026-10-30-cdf-gap-lifecycle.md, 2026-10-30-brain-change-feed.md
---

# Brain-driven：主文件就是最新的有效全集

状态：设计 v5，待 XG 确认执行方式。本版是 XG 和我讨论后（2026-10-30）收敛的结果，取代 v1–v4。
早期版本中被推翻的部分见 §9。

## 0. 原则

1. **DDD 管产品真相。** 判断取决于"这个产品是什么、做过什么决定、哪些不做"时，用 DDD。chat、native coding、pipeline 三种模式都一样。
   - 写代码的机制坑：靠专家评审和仓库自己的评审模式接住，DDD 里留作可以查的参考；
   - 代码实际怎么做：读代码；
   - 我该怎么做事：靠治理规则。
2. **主文件就是最新的有效全集。** 在主文件里的就是有效的，无效的不在主文件里。
   - 没有"挂起""已取代""已完成"这类中间态；
   - 任何读取方都不需要再过滤；
   - 聊天、pipeline、Brain Hub 看到的是同一份认知。
3. **改动只有三种：新增、覆盖、撤回。** 被覆盖、被撤回的原文，以及何时、为何，都记在主文件之外
   （写入日志 `ingestion_writes`、`.archive/`、撤回表 `redactions`）。
4. **标签：对 recall 和判断有用的就保留。** 保留分类、引用、条目身份（`cand:`）、出处和 taint、日期。
   删掉没有读取方、或者会误导判断的字段（§2.5）。
5. **"过期"只在两处判定，而且都发生在新内容进入的那一刻：**
   - **机械可判的**：残留、墓碑、已完成，自动处理；
   - **做决定的那个上下文自己声明的**：会话复盘 S7 根据用户原话，run 报告 S3 根据 run 自己的修改，经过确定性复核后自动执行。

   不做任何事后的周期性复判。不变的判断就一直留着；会变的判断，在新判断进来时就已经处理掉了。
6. **按结果衡量。** 月度错误倒查只量"完整"（该有的有没有）和"最新"（有没有过时的），不改动任何文件。

## 1. 证据（2026-10-30）

**错误倒查**：38 个真实错误，明细见 `Knowledge/Reports/2026-10-30-ddd-recall-eval/mistake_audit.json`。

| 需要的知识 | 数 | 事先没有 | 有但没送到 | 送到仍犯 | DDD 内容过时 | 无法判断 |
|---|---|---|---|---|---|---|
| 写代码的机制坑 | 22 | 10 | 11 | 1 | 0 | 0 |
| 产品事实和决定 | 7 | 5 | 0 | 0 | 2 | 0 |
| 我自己的行为 | 9 | 7 | 0 | 0 | 0 | 2 |

**主文件里今天不是"最新有效"的内容：**

| 内容 | 数量 | 问题 |
|---|---|---|
| refine 后残留的旧条目 | 4 对已确认 | 新旧两条都会被 recall 出来（`writer.py:544-546` 不删旧条目） |
| 撤回墓碑 | 2 | `governed_edit.redact` 原地写墓碑 |
| `- [x]`（已关闭的 gap、完成的事项） | 26 | 留在主文件 |
| `decay:dormant` | 主文件约 190 条 | 只按时间判定，旧不等于过期；而且读取方行为不一致：MEMORY 注入会过滤掉（`memory_injection.py:250/435`），DDD 的 recall 和 find 不过滤 |
| 判官认定的 supersedes | DDD 5 条 | MEMORY 注入会因此隐藏旧条目（`memory_injection.py:465-471`），DDD 不隐藏；判官的判断约一半是错的 |

**死字段（没有生产读取方，或者会误导）：**

- `<!-- maturity: … -->` 728 处：`verified: false` 554 处、`used: false` 567 处，没有任何生产代码读取；
- `ref:0` 计数 2330 处：自 2026-06 起没有生产方。

**衰减任务**：`assess_decay` 没有生产调用方，现有的 dormant 状态是早先冻结下来的。

## 2. 改动

### 2.1 覆盖

- **合并**：判官对同一文件里的现存条目给出 `adds` 时，writer 把旧条目的完整内容和新内容合成一条，原位覆盖。
  - 类型取两者中衰减豁免的那个；
  - 不会丢内容，也不会重复。
  
  今天的做法是新写一条，保留旧的。
- **纠正**：只由 S7 或 S3 的 `overrides: [{id, head}]` 声明触发（来源白名单），或由用户操作触发。
  - 代码复核：`id` 和首行（去掉注释、空白归一化后比较）恰好对上一条现存条目；新单元已写入；判官给出的不是 `same`。
  - S7 的提示词加一句：会话里改变了一条已有决定时，用会话里 `knowledge find/get` 给出的 id 声明它替换了哪条。
- **边界**：CDF 永远不改人写的条目；STEERING、USER、TOOLS 里只动"系统追加"部分。
- **判官单独给出的 "contradicts"**：不改动任何条目，也不再隐藏 MEMORY 里的旧条目，删掉 `memory_injection.py:465-471` 的这段逻辑。
  这种情况下新条目照常写入，列进统计里的"未决冲突"。
- 其他来源带的 `overrides` 和 `relation_hint` 在入队时剥掉。

### 2.2 撤回

- 撤回就是删掉条目字节（先归档），不留墓碑，原文、状态（withdrawn / closed / done）和理由写进 `redactions`。
- gap 被 run 关闭、事项完成，都改为撤回。
  需要这段历史的读取方，先改成读撤回记录：gap 复发判断（`gaps.is_closed_gap`、`pipeline_v2.py:620-626`）、`ddd_drift_fix`。
- **防写回**：
  - 撤回记录带内容指纹，即去掉注释、空白归一化后正文的 sha256；
  - 指纹相同的单元，在判官之前就拒收；
  - 相似的撤回原文作为命中交给判官，判官给出 `same` 就拒收。
  
  今天这道拦截不存在，已经发生过一次写回。

### 2.3 去掉"挂起"

- DDD 不再按时间衰减，条目只会因为覆盖或撤回离开主文件。
- 删掉 `assess_decay` 及其状态转换、`context_directory_loader._filter_dormant_entries`，以及 `memory_injection` 里对 dormant 的过滤。
- Brain Hub 的计数（`brain_graph`、`context_brain`）改为只统计"有效"。
- **现有约 190 条 dormant**：dormant 只是按时间打上的，恢复成有效是它的机械逆操作，所以全部恢复为有效，去掉这个标签。
  其中如果有过时的，按原则 5 在新判断进入时处理。
- MEMORY 在预算内按分数挑选整条注入（AGENT R22），这是排序，不是删除，不变。

### 2.4 统计

- 新增 `brain_changes(project, since)`：从写入日志、撤回表和归档，列出每一条新增、覆盖、撤回，以及未决冲突。
  每条带首行、原文、来源、理由、时间。
- 读取方：Brain 变化流（`2026-10-30-brain-change-feed.md`），以及月度倒查报告。
- 顺带修掉 `queries.py:538` 里那个永远为 0 的撤回计数。

### 2.5 删掉死字段

- 删掉 `maturity` 注释，以及 `ref:` 计数、`decay:`、`last:` 这几个字段（衰减取消后，后两个也没有读取方了）。
- 元数据注释里保留 `source:` 和 `taint:`。
- 删之前逐个确认读取方：`ddd_maturity.py`、`memory_decay.py` 的 `ref_count` 参数来源、`ddd_format.py`、`archive_browse.py` 等。
  有生产读取方的字段先迁移读取方，再删。

### 2.6 一次性清理（先出清单给 XG 看）

- 残留的旧条目：撤回。只撤回"同一文件，且新条目包含旧条目完整内容"的那些，其余列出来，不动。
- 墓碑：删除，并给 `redactions` 补上指纹。
- `- [x]`：撤回，状态记 closed / done。
- dormant：恢复为有效。
- 死字段：删除。

### 2.7 删除

- 每周 DDD 自审：`jobs/handlers/ddd_self_audit.py`，连同它的 S4 修正路径、`core/ddd_drift_signal.py` 和 Eval 面板上对它的读取。
  依据是原则 5：不做事后复判。删之前确认它的调度注册和所有调用方。
- `.ddd-usage.json` 的写入方：`core/ddd_usage.py`、`record_ddd_hit`、`addressing.record_cited`。它们没有任何读取方。
- `refine` 动作：已被合并取代。
- `usage.record_recalled_result` 里走不到的 DDD 分支。

### 2.8 月度错误倒查

- **任务**：job handler `brain_mistake_audit`，agent 只读。
- **判定**：
  - 确定性的部分：事先有没有（`git log -S`）、有没有送到；
  - 由模型判断的部分：标为判断，只出报告。
- **报告**：`Knowledge/JobResults/<date>-brain-mistake-audit.md`，内容依次是：
  - 上面那张表和上月对比；
  - 本月的变化统计；
  - 未决冲突。
- 以今天的 38 条为基线。

### 2.9 治理文字（SWARMAI.md §My Brain，最后一步）

> - **What a DDD is for, and how it stays true.** A DDD holds what a project IS and what was
>   DECIDED — its decisions, models, principles and non-goals. I use it in chat, native
>   coding and pipeline alike whenever a judgment depends on that: proposing or choosing a
>   design, describing how a feature looks or works, judging whether a feature is complete.
>   Code-writing pitfalls are caught by the review experts and the repository's own review
>   patterns; the DDD keeps them as reference to look up. Its main files are the current,
>   valid whole: what is there is in force, and what is not in force is not there. A change
>   is an addition, an override or a withdrawal, decided when the new knowledge comes in —
>   mechanically, or by the context that made the new decision — never by a later re-judging
>   pass. What was replaced or removed, when and why, is recorded outside the main files.
>   Its worth is measured by outcome: whether it holds what it should, and nothing stale.

## 3. 不变量（最终测试集，每条都用 mutation check）

- I1 判官给出 `adds`、同一文件时，主文件里只有一条合成后的条目，包含旧条目的完整内容；旧原文在归档里。
- I2 纠正覆盖只在 S7/S3 的合格声明，或用户操作时发生。以下情况都不改动任何条目：
  判官的 `contradicts`、来源不在白名单、复核失败、判官给出 `same`。
- I3 CDF 不改人写的条目，也不改 STEERING、USER、TOOLS 的非系统部分。
- I4 没有任何读取方因为 dormant、superseded 或 `[x]` 而过滤条目：主文件里的条目全部都是有效的。
- I5 撤回、关闭、完成之后，主文件里没有那一条，也没有墓碑；`redactions` 里有原文和状态。
  gap 复发判断的结论和撤回之前相同。
- I6 指纹与撤回原文相同的单元，在判官之前就被拒收。
- I7 `brain_changes` 和写入日志逐条对得上；撤回计数不再永远为 0。
- I8 清理幂等；清理之后，SwarmAI 主文件里 `decay:dormant`、`maturity:`、`ref:0`、`[redacted]`、`- [x]` 的数量都是 0。
- I9 生产代码里没有 `ddd_self_audit`、`ddd_drift_signal`、`.ddd-usage.json` 的写入，也没有 `assess_decay` 的调用。

## 4. 步骤和验证计划

读取方先于写入方：

| 步骤 | 内容 | 评审 |
|---|---|---|
| 1 | 统计 `brain_changes`，修撤回计数 | correctness |
| 2 | 需要历史的读取方改读撤回记录；防写回指纹 | correctness、security |
| 3 | 撤回不留墓碑；gap 关闭、事项完成改为撤回 | correctness、state-machine |
| 4 | 覆盖：`adds` 改为合并；S7/S3 的 `overrides`；白名单；删掉判官 supersedes 隐藏 MEMORY 条目的逻辑；边界 | correctness、security、concurrency |
| 5 | 去掉"挂起"和衰减；删死字段（先迁移读取方） | correctness |
| 6 | 一次性清理（先出清单） | correctness |
| 7 | 删除每周自审、`.ddd-usage.json`、`refine` | 不需要，测试证明 |
| 8 | 月度倒查 job；治理文字 | correctness |

每步只跑它触及的测试，加 mutation check；最后整体跑一次 I1–I9，逐行报告。

## 5. 未决冲突（已知的取舍）

判官给出 `contradicts`、而做决定的上下文没有声明时，新旧两条都留在主文件里，在统计中列为"未决冲突"。
这和"主文件只放有效全集"有一点张力。之所以这样定，是因为判官的这类判断约一半是错的，按它删条目会违反"有用的不能丢"。

缓解办法：让 S7 更容易声明（它能看到会话里查过的 id），同时在月度报告里统计未决冲突的数量。
如果这个数量不小，再讨论是否要给判官更大的权限。

## 9. 早期版本里被推翻的部分

- 按条目类型过滤送达；
- 写代码时按模式注入；
- `knowledge_query`；
- 中文分词改动；
- 在旧条目上打取代标记；
- 把所有标签移出文件、改键数据库（v4 B 期，XG：标签保留）；
- 每周自审（XG：不做事后复判）。

推翻理由和证据见 git 历史中 v1–v4 的 §7、§9、§14。
