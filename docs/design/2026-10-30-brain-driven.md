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
| 判官认定的 supersedes | DDD 5 条 | MEMORY 注入会因此隐藏旧条目（`memory_injection.py:465-471`），DDD 不隐藏。错得多的是已删除的 `refine` 路径（14 条错 8 条）；现在的判官判 `contradicts`，18 对里有 4 对其实不矛盾（`Knowledge/Reports/2026-10-30-cdf-override-experiment/`，单人标注） |

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
  - **先后按说出的时间，不按到达 CDF 的时间**：新单元的说出时间（它引用的证据在 `evidence_roots.observed_at` 里的最大值）必须晚于目标条目的说出时间（目标条目当初那个单元的同一个值），否则不覆盖、新单元也不写入，原因记 `older_than_existing`。
    依据：能确定先后的 14 对矛盾里有 4 对，新单元其实说得更早，只是在队列里排得久，到达时目标条目已经是更新的说法。按到达顺序会让旧话覆盖新话。
    目标条目查不到说出时间时（没有对应的单元），按条目自己的日期、以天为单位比较；同一天视为不晚于，不覆盖。
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

- I1 （合并开启后）判官给出 `adds`、同一文件时，主文件里只有一条合成后的条目，包含旧条目的完整内容；旧原文在归档里。
- I0 合并开关未打开时，判官给出 `adds` 的行为和今天一样：新增一条并挂 `derived_from`；判官回归集在提示词、模型或参数变化时必须重跑。
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
- I10 声明的覆盖，新单元说出得不晚于目标条目时，目标条目一字不变，新单元不写入，原因记 `older_than_existing`；撤掉这条检查，测试变红。

## 4. 步骤和验证计划

读取方先于写入方：

| 步骤 | 内容 | 评审 |
|---|---|---|
| 1 | 统计 `brain_changes`，修撤回计数 | correctness |
| 2 | 需要历史的读取方改读撤回记录；防写回指纹 | correctness、security |
| 3 | 撤回不留墓碑；gap 关闭、事项完成改为撤回 | correctness、state-machine |
| 0 | 判官准确度（§5.1）：抽样、盲标、混淆矩阵、错误归因、回归集。只读，不改代码 | 零利害标注 + XG 裁定 |
| 4 | 覆盖：S7/S3 的 `overrides`；白名单；按说出时间排先后（I10）；删掉判官 supersedes 隐藏 MEMORY 条目的逻辑；边界。**`adds` 改为合并，只在第 0 步达标后开启** | correctness、security、concurrency |
| 5 | 去掉"挂起"和衰减；删死字段（先迁移读取方） | correctness |
| 6 | 一次性清理（先出清单） | correctness |
| 7 | 删除每周自审、`.ddd-usage.json`、`refine` | 不需要，测试证明 |
| 8 | 月度倒查 job；治理文字 | correctness |

每步只跑它触及的测试，加 mutation check；最后整体跑一次 I0–I10，逐行报告。

## 5. 未决冲突（已知的取舍）

判官给出 `contradicts`、而做决定的上下文没有声明时，新旧两条都留在主文件里，在统计中列为"未决冲突"。
这和"主文件只放有效全集"有一点张力。之所以这样定，是因为判官的这类判断不可靠：18 对里有 4 对其实不矛盾；更关键的是，"更新的观察"不等于"纠正"，两两比较的判官分不清这两者。按它删条目会违反"有用的不能丢"。
按句删除错句、或让模型在只许用原句的约束下改写，都在同一批数据上试过：前者留下错误的标题，后者全部通过逐句核对，但约一半改错（`Knowledge/Reports/2026-10-30-cdf-override-experiment/`）。所以覆盖只由声明触发。

缓解办法：让 S7 更容易声明（它能看到会话里查过的 id），同时在月度报告里统计未决冲突的数量。
如果这个数量不小，再讨论是否要给判官更大的权限。

### 5.1 后续行动：判官准不准、错在哪、怎么改准（XG 2026-10-30）

**为什么必须先做**：判官的裁决在 v5 里直接决定文件内容，判错的后果各不相同。10-28 以来的裁决分布
（`ingestion_units` 的 `reason` 字段）如下：

| 判官说 | 次数（10-28 起） | v5 里的后果 | 判错的后果 |
|---|---|---|---|
| `same` | 106（reinforce 55 + already_exists 51） | 新单元被拒，或者只给旧条目加强 | **新知识被当成重复丢掉**，违反"有用的不能丢"（今天就存在的风险） |
| `adds` | 115 | §2.1 自动合并 | 不相关的两条被拼成一条，主文件变脏 |
| `contradicts` | 7 | 记为未决冲突 | 冲突清单里有噪声 |
| `unrelated` / `new` | 159 | 新增一条 | 应该判 `same` 的变成重复；应该判 `contradicts` 的变成无人知晓的冲突 |

所以**判官准确度达标，是 §2.1 合并能上线的前提**。达标之前，`adds` 保持今天的行为：新增一条，挂 `derived_from` 关系。

**数据**：现成的，不需要新埋点。起始样本：`Knowledge/Reports/2026-10-30-cdf-override-experiment/labels.json` 里 18 对 `contradicts` 已有单人标签，盲标时作为其中一部分重新标注。
- 每次裁决都存在 `ingestion_units.decision.version_vector.verify.hits`（h1…hN 各自的 relation），以及 `relation_hints`（被判 adds 或 contradicts 的那条现存条目的锚点）；
- 新单元的原文在 `ingestion_inbox.payload` 里；
- 现存条目的原文在文件或归档里。

**做法**：
1. **抽样**：从 CDF v2 切换以来的裁决里，`contradicts` 全取，`adds`、`same` 各取 60 条，判为 `unrelated` 的头部命中取 40 条。
2. **盲标**：两个零利害的 agent 各自独立标注，看不到判官的结论。两者不一致的条目由我裁定，拿不准的给 XG。
   结果是每种 relation 一张混淆矩阵。
3. **定位错误原因**：逐条读判错的案例，按原因归类。可能的原因：
   - relation 的定义含糊，比如"同一主题"的粒度没有定义；
   - 现存条目给判官看的形式有问题（是不是完整的、有没有截断）；
   - 候选命中本身就不相关：每次给 8 条，大多是 unrelated；
   - 一次调用同时判 grounded、durable、self_contained、significant、hits 五件事，负担过重；
   - 模型和参数本身。
4. **改法由数据决定，不预设。** 候选包括：
   - 拿判错的案例做反例，把定义写清楚；
   - 把 relation 判断拆成单独一次调用；
   - 判 `same`、`adds`、`contradicts` 时，必须摘出双方重叠的那句原文，由代码确定性地核对这句话确实同时出现在两边，核不上的降级为 `unrelated`。
     这一条能把"凭空认定的关系"挡在门外，我认为最值得先试。
5. **固化**：标好的案例变成判官的回归集，作为私有 golden case 加入 eval。以后每次改判官的提示词、模型或参数，都要重跑。

**合并上线的门槛**（事先定好，量完再改要说明理由）：
- `adds` 的精度 ≥ 90%；
- `same` 的误判（实际不是重复）≤ 5%。

达不到就不开合并，并把根因修复作为下一步。

### 5.2 第 0 步结果（2026-10-30）

数据和脚本在 `Knowledge/Reports/2026-10-30-judge-accuracy/`，回归集是 `judge_regression_set.json`（157 条）。

**样本**：
- CDF v2 以来共 221 个单元、1768 个配对；
- 抽样 169 对：`contradicts` 和 `same` 全部取，`adds` 取 60、`unrelated` 取 40；
- 两个零利害的标注者各自盲标，一致率 157/169（93%），在一致的 157 对上统计。

| 判官 | 准确率（判官说 X 时，实际也是 X） | 主要判错成 |
|---|---|---|
| 生产 `same` | 23/43 = **53%** | 17 条其实是 `adds` |
| 生产 `adds` | 23/54 = **43%** | 28 条其实是 `unrelated` |
| 生产 `contradicts` | 14/20 = 70% | — |
| 生产 `unrelated`（排第一的候选） | 39/40 = 98% | — |

**已经发生的损失**：被误判为 `same` 的 17 条新知识，10 条只给旧条目记了一次"加强"，7 条以 `already_exists` 被拒，新内容一条都没写进去。

**判错的原因**：
1. 判 `same` 只比了核心论点，没比新单元补上的细节（理由、被放弃的方案、具体原因）；
2. "同一主题"被放宽成同一个大领域：判对的 `adds`，对应候选的排名中位数是 1，判错的是 5；
3. 候选里混着非内容：撤回墓碑、`- [x]`、文档开头的 `---`、EVOLUTION 编号标题。

**改进实验（B 组）**，同一批数据重放：
- 提示词只判关系，"同一主题"按具体的决定、机制、事实来定；
- `same` 必须是新单元的每个具体说法都已经存在，同时列出 `new_only`；`new_only` 不为空，代码就把 `same` 降为 `adds`；
- 必须摘出两边的原文，由代码核对。

| | 生产 | A 组（现提示词重放） | B 组 |
|---|---|---|---|
| `same` 的准确率 | 53% | 64% | **100%（9/9）：不再丢知识** |
| `adds` 的准确率（是否同一主题，实际为 adds 或 same 都算对） | 54%（29/54） | — | 76%（45/59）；只算每个单元的第一个相关候选是 81%（35/43） |
| `contradicts` 的准确率和召回率 | 70% / 93% | 72% / 87% | 67% / 27% |

**这次实验暴露出两个问题**：
- **引文核对太严**：被它降级的 17 对已标注样本里，只有 4 对降对了，另外 13 对是真关系被误降。
  原因可能是模型没有逐字照抄，或者原文里的 markdown 标记影响了比对。所以 `contradicts` 的召回率才会掉到 27%。
- **判官本身不稳定**：同一提示词重放，准确率从 63% 变到 69%。重放时没有提供 grounding 数据，所以这一项还不能完全归因于判官。

**结论**：
- **`same` 的修法可以立刻上**（B 组的定义加上 `new_only` 降级）。它消除的是今天正在发生的丢知识，也就是违反"有用的不能丢"的那部分，优先级最高。
  `same` 被降为 `adds` 之后，按现在的行为是新增一条，可能产生重复，但不会丢东西。
- **合并的门槛（90%）没达到**（76%–81%），合并保持关闭。下一轮实验在回归集上做：
  - 修好引文核对：去掉 markdown 后再比对；
  - 合并目标只取第一个相关候选；
  - 给判官的候选从 8 条减到更少、更准的几条；
  - 对每个合并对单独再问一次"合成一条读起来是不是一件事"。

## 9. 早期版本里被推翻的部分

- 按条目类型过滤送达；
- 写代码时按模式注入；
- `knowledge_query`；
- 中文分词改动；
- 在旧条目上打取代标记；
- 把所有标签移出文件、改键数据库（v4 B 期，XG：标签保留）；
- 每周自审（XG：不做事后复判）。

推翻理由和证据见 git 历史中 v1–v4 的 §7、§9、§14。
