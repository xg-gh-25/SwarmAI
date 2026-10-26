---
title: Recall 语料的写入与准入 — Memory、DDD、Knowledge 三块统一的准入、身份、使用信号与退出
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r10 — 定稿（r3 答题卡；r5 两轮评审；r6 全自动+审计；r7 不动已沉淀内容；r8 ontology；r9 卡的维度、顺序、路由表；r10 部分冲突，见 §10）
---

# Recall 语料的写入与准入

## 0. 一句话

读取侧已经按排名、日期、来源做了过滤（`2026-10-26-recall-precision-and-coverage.md`）。这一版看写入侧：
Memory、DDD、Knowledge 三块的每个写入者都按"宁缺毋滥"审了一遍。结论是三块犯的是同一组错误：

1. **准入不问"该不该放在这里"。** 共用的 LLM 判官只检查一条内容是否具体、能否执行，不检查它属不属于这个存储。
   所以每轮都会注入的 MEMORY 里，有一半以上是只和某个项目有关的条目。
2. **写入者能绕过准入。** DDD 最大的写入路径 REFLECT（标为 auto-cultivated 的 592 条）直接写入，跳过了准入；还有好几个 skill 和
   编辑器接口也能直接写。
3. **"用过没有"这个信号是死的。** `ref` 字段从来没有生产代码去加。MEMORY 87 条全是 `ref:0`，
   DDD 也基本如此。所有退出决策（衰减、归档）都只能按时间和类型来判断。
4. **大部分条目没有退出路径。** 按类型免疫、按 section 常青，TECH 甚至完全没有自动退出。有几条退出路径是
   直接删除，不先归档。
5. **注入形态和内容大小不匹配。** DDD 注入的是 section 指针，指向的 section 中位约 16K 字符，最大 234K。

本设计给三块定一份统一的**存储契约**（§3），所有语义写入只能经过一个受控入口（§4.1），
入口里每个候选都要过质量、去向、准入三张答题卡（§4.2）。

**和 r1 的关系**：r1 只覆盖 Knowledge，其中 S3 要重切全部 chunk，太重，而且给 chunk 加标题行会让读侧的覆盖率判断变松。
本版改成在读取和渲染时处理（§4.7），不重切、不重建索引。

## 1. 怎么评估的

三个只读子 agent 并行审计，不改任何文件、不读会话数据。只有 Knowledge 那一路以只读方式查了派生的
`knowledge_chunks` 表。审计报告留在 scratch，不进仓库，因为里面引用了私人内容。这里只放汇总数字。

| 块 | 看了什么 |
|---|---|
| Memory | 所有写入和退出路径（带代码行号）；live MEMORY.md 每一条逐条分类；EVOLUTION 按 section 分类；`ref`/`decay` 元数据的维护 |
| DDD | 所有写入者及各自写入的条数；使用信号；把 48 次真实 DDD 注入指向的 section 打开，逐个判断有没有用、项目对不对；SwarmAI 的 TECH 和 IMPROVEMENT 随机抽 40 条做分类 |
| Knowledge | 各目录由谁写、按什么条件写；目录统计；backflow 写的 Note 抽 15 篇判断价值；10 月的中途 checkpoint |

同时用到前面读取侧回放和盲评的数据：库里 21062 个 chunk、约 3000 个打过标签的命中。

## 2. 发现

### 2.1 Memory（每轮整份注入）

| 实测 | |
|---|---|
| live MEMORY.md 的 97 条语义条目（87 条 parser 可见 + 10 条 Open Threads） | 该留 31（34% 字符）· **只关某个项目、该进 DDD 51（57%）** · 该归档 3 · 和 steering/SOUL/AGENT 重复 12 |
| `ref` 字段 | 87 条全是 `ref:0`。`bump_references()` 没有生产调用者；会话结束时的生命周期代码明确写着 `bumped = 0`（`context_health_hook.py:1536-1549`） |
| 能按时间退出的 | 只有 `guideline` / `process` 两类；`decision` / `model` / `principle` / `correction` / `pitfall`、COE、Open Threads、Standing Preferences、EVOLUTION 全部 section 都不受时间影响 |
| 每周 Memory Health | LLM 只看 MEMORY 前 8K 字符；被判过时的条目**直接删除**，不先归档 |

根因：
- 共用判官（`ingestion_gate`）的评分标准只问"具体吗、能执行吗、自洽吗"，判断不了的项目背景默认放行。
  它不问"这条内容是不是跨项目、不依赖查询、大多数会话都用得上"，而这是整份注入的存储唯一该问的问题。
- 自动蒸馏从 DailyActivity 提升到全局 MEMORY 时，**不先判断它属于哪个项目**。所以 57% 的内容是项目专属的。
- 绕过准入的写入口：`s_memory-distill` skill 的说明已经过时（段名、阈值、锁、"不删除"的规则都和代码不一致）；
  `s_persist` / `s_self-evolution` 用 Edit 直接写；两个工作区编辑接口可以不加锁地整文件覆盖。

### 2.2 DDD（按项目注入 section 指针）

| 实测 | |
|---|---|
| 写入路径及写入条数（SwarmAI TECH + IMPROVEMENT） | REFLECT 直写（标为 auto-cultivated）**592** · session 结束 writeback 196（大多是旧格式；现在的置信度 0.5 低于 0.7 的阈值，**实际已写不进去**） · DailyActivity 决策/纠正 20 · signal 桥 0 · 来源无法识别 333 |
| 随机抽 40 条 | 该留 23（57.5%）· 过时 4 · 重复 5 · 不是持久内容（会话流水、会变的数字）8 |
| 48 次真实 DDD 注入的 101 个指针 | 有用 28.7% · 沾边 32.7% · **噪音 38.6%**；43.8% 的消息至少拿到一个有用指针；项目识别正确 93.8% |
| 指针指向的 section | 中位 16K 字符，P90 234K；9% 指向已经不存在的 section 名 |
| 使用信号 | `ref` 没人加；`record_ddd_hit` 只记"最后命中日期"。而且渲染时只输出 `doc § section`，丢掉了 entry 正文，使用记录照样写，所以"命中过"不等于"内容被看到过" |

根因：
- **REFLECT 新增领域经验时直接调用 `apply_to_ddd`，绕过了 `admission_band`**（`reflect_job.py:231-284`）。
  它是 DDD 最大的写入路径，却是唯一不过判官的。
- 人工批准 cultivation 提案也直接写入（`routers/cultivation.py:130-236`）。人工批准可以跳过判官，
  但现在连去重和路由也一起跳过了。
- DailyActivity 里的决策和纠正一律归到 SwarmAI，代码注释写着"future: infer"（`context_health_hook.py:961-969`）。
- 去重只比完全相同的整串文字；TECH 没有自动退出路径；`pitfall` 永远不衰减；
  矛盾替换时直接删除旧条目（`ddd_cultivation.py:978-1007`）。

### 2.3 Knowledge Library（召回时排名过滤）

| 实测 | |
|---|---|
| backflow Note（418 篇，抽 15 篇） | 可复用 6 · **只在原会话里有意义 8** · 和人写的文档重复 1 |
| backflow 的同内容多份 | 515 组重复 chunk（按日期重复存） |
| 中途 checkpoint（10 月 20 块，覆盖 15 个会话） | 抽的 10 个会话，**都有完整的会话结束条目**，checkpoint 没有任何独立价值 |
| Pollinate 的 700 个 chunk | **429 个（61%）来自 `node_modules`** |
| 版本族（`-v2`、`final`、`r3` 并存） | 至少 24 组，没有"当前版本"这个标记 |
| DailyActivity | 冒烟探针 23 条（会话已被删除）；一个不属于任何条目的 `## Session Resume` 块 |
| frontmatter 开头的 chunk | 897 个，约 40% 没有正文；盲评 有用 28%、噪音 38% |

根因：backflow 的准入只看"长度 + 表面标记"，不问内容离开原会话还有没有意义、有没有更权威的文档已经覆盖；
索引只按目录排除，`node_modules` 这种依赖树会被扫进来；checkpoint 在会话结束条目写入之后没有被替换掉。

### 2.4 三块的共同问题

| 问题 | Memory | DDD | Knowledge |
|---|---|---|---|
| 准入不问"该不该在这里" | 判官不问跨项目 | 判官默认放行 | backflow 只看长度 |
| 写入者绕过准入 | 3 个 skill、2 个编辑接口 | REFLECT、人工批准 | 通用文件接口（人工，可接受） |
| 路由（放到哪） | 不先判断项目 | DailyActivity 一律 SwarmAI | 自动内容直接进可搜索目录 |
| 去重 | 只在会话结束时做同 section 的完全相同去重；不和 steering/DDD 比 | 只做完全相同去重 | 按日期重复存 |
| 使用信号 | `ref` 死了 | `ref` 死了，命中记录多报 | 无 |
| 退出 | 大部分类型不受时间影响；有直接删除 | TECH 无退出；有直接删除 | 精选目录永不老化；版本族没有当前版标记 |

## 3. 存储契约

一份带类型的契约（`backend/core/store_contracts.py`），覆盖所有会被写入的存储。r1 里的来源策略也并进来
（替换 `_SKIP_*`、`NO_INJECT_PREFIXES`、`_DAILY_RE`、`ARCHIVE_PREFIX`）。所有写入者、准入入口、读取侧、复审 job
都只读这一份。

| 存储 | 属于召回语料吗 | 准入问题 | 允许的动作 | 退出 |
|---|---|---|---|---|
| MEMORY | 是（每轮整份注入） | 跨项目、不依赖查询、多数会话都会影响判断 | 新加 / 补充 / 替换 | 复审（§4.8） |
| EVOLUTION | 是（每轮整份注入） | 关于 agent 自己的能力或纠正，不针对某个项目 | 新加 / 补充 / 替换 | 复审 |
| 项目 DDD（PRODUCT / TECH / IMPROVEMENT / PROJECT） | 是（按项目按需注入） | 离开这次会话或 run，以后还会影响这个项目的判断 | 新加 / 补充 / 替换 | 复审（TECH 也包括在内） |
| Knowledge Library（Notes / Library / Learned） | 是（召回时排名） | 离开原会话也能独立读懂、可复用的参考材料 | 新加（一个文件） | 复审（版本族旧版移进 Archives） |
| 用户 context 文件（USER / STEERING / TOOLS） | 是（每轮整份注入） | 改的是这个用户"怎么判断" | 新加 / 补充 / 替换，写到用户的 `.context/`（§4.9） | 复审 |
| DailyActivity | 是（日志，按日期规则召回） | 不过答题卡，它是日志 | 按会话 id 覆盖写自己的块 | 不退出 |
| 候选表 `ingestion_candidates`（§4.3） | **否**（状态和审计） | — | 插入 / 更新结论 | 保留 180 天 |
| 不写 | — | — | — | — |

**四种身份，各管各的**（r4 把它们混在一起了）：

| 身份 | 用途 | 例子 |
|---|---|---|
| `candidate_id` | 同一个候选重放时不重复处理（幂等） | `writer:来源id:序号` 的哈希，如 backflow 用消息 id |
| 来源 | 记录出处，校准每个写入者 | `source:reflect run=… lesson=…` |
| 内容指纹 | 完全相同内容去重 | 规范化正文的哈希（复用 `content_signature`） |
| 条目锚点 | 卡 3 的"补充"和"替换"指向哪条 | DDD / MEMORY 条目的稳定锚点 |

## 4. 设计

### 4.1 受控写入：只有一个能改语料的口子

r4 只是"改走 `admit()`"的一份名单，名单漏一个就会绕过。r5 改成结构上只有一个口子：

- **`governed_store` 模块**是唯一能改 MEMORY、EVOLUTION、DDD、Library、用户 context 文件的模块。
  现在公开的底层写函数（`apply_to_ddd`、`annotate_entry`、`reflect_library.write`、`locked_write` 的 MEMORY 写入、
  context 文件写入）改成这个模块的私有函数。
- 对外只有一个函数：`submit(candidate) -> Decision`。它跑三张答题卡（§4.2），按结果执行（§4.4）。
  `ingestion_gate()` 和 `admission_band()` 并进卡 1，不再作为公开的决策入口，这样不会出现第三套准入。
- **架构测试**：扫描 `backend/`，凡是写这几类路径、或者导入私有写函数的，必须在一份白名单里，并标明属于哪一类。
  新加的写入者如果没进白名单，测试就会失败。

写入者分类（第一位 reviewer 补齐了 r4 漏掉的写入者）：

| 类别 | 写入者 | 规则 |
|---|---|---|
| 语义写入（必须走 `submit`） | REFLECT 的新加 / 强化 / 细化（`reflect_job.py:248-282`）、REFLECT 写 pipeline-lessons（`reflect_library.py:161-207`）、自动蒸馏、即时纠正、`memory_extractor.py:351`、`evolution_optimizer.py:899`、DailyActivity 决策/纠正 → DDD、backflow、cultivation 提案、`s_persist` / `s_self-evolution` / `s_ddd-persist` | 三张卡 |
| 结构刷新和元数据 | `ddd_orchestrator.py:566-590` 的机械替换、`ddd_maturity.py:521-539` 的成熟度元数据、生命周期状态 | 不改语义；只走受控模块里的结构写函数，不跑答题卡 |
| 日志和过程产出 | DailyActivity、checkpoint、JobResults（`jobs/executor.py:2007-2056`）、Signals 摘要、Reports、Pollinate 包 | 不是知识存储。在契约里标明召回规则（Signals 和 DailyBriefs 现在已经不自动注入） |
| 初始化 | `ensure_directory` 复制模板、工作区骨架 | 不变 |
| 用户直接编辑 | 两个工作区编辑接口 | 改受控路径时走 `write_context_cas`（§4.9）或受控的整文件写入；用户自己的编辑不跑判官，但记进审计（`writer=user_edit`） |
| 删除或改造 | session 结束 writeback（置信度 0.5 永远过不了 0.7）：先看它过去 30 天丢弃的候选，确认没有价值再删；`s_memory-distill`：改造成手动触发入口（§4.12） | — |

### 4.2 答题卡：卡 0 规则 → 卡 1 内容本身 → 卡 2 路由表 → 卡 3 和已有内容的关系

**输入和输出都是固定结构**，每次决定都记下契约版本、prompt 版本和模型 id。每道题的结果是 `PASS` / `REJECT` / `DEFER` 之一，外加一个原因码。

- `REJECT`：判断过了，不该写，不重试。判官拿不准时也给 `REJECT`，原因码 `uncertain`。这就是"宁缺毋滥"。
- `DEFER`：没法判断，比如判官预算用完、LLM 出错、输出解析失败、检索不可用或索引过期、拿不到锁、项目有歧义。候选留在候选表里，稍后重试（§4.3）。

#### 顺序为什么这样排

| 依赖 | 说明 |
|---|---|
| 卡 2 依赖卡 1 | 去向由类型、主体、适用范围决定，这三样卡 1 才给出 |
| 卡 3 依赖卡 2 | 检索范围由去向决定 |
| 卡 0 放最前 | 规则能判掉的内容不花 LLM 调用 |

r8 有两处调整：

1. **"值得记吗"和"重复吗"从卡 1 挪到卡 3。** 这两个问题是相对的，不看已有内容就答不准。卡 1 只问内容本身的问题。
2. **卡 2 改成确定性的路由表。** 判官只在卡 1 输出类型、主体、范围、形式这几个字段，卡 2 用一张表直接算出去向。这样卡 2 不需要 LLM，结果可以重放，写测试也简单。

**为什么不先检索、再定去向。** 这样做，去向就跟着"相似内容现在放在哪"走。可审计发现 MEMORY 里有 57% 的内容放错了地方，按现状路由只会继续放错。所以去向由内容本身决定，卡 3 只能在发现明显放错时改投一次，最多一次，避免在两个去向之间来回打转。

**LLM 调用次数**：卡 1 一次。卡 3 只在检索到相关条目时调用一次。卡 0 和卡 2 不调 LLM。

#### 卡 0：规则（不调 LLM）

| 检查 | 不通过时 |
|---|---|
| `MemoryGuard` 扫描：注入、角色劫持、外传、隐藏字符、密钥 | `REJECT unsafe` |
| 来源可信度：来源含不可信外部内容（网页、MCP 返回、别人发的频道消息），且去向可能是 MEMORY、EVOLUTION 或 context 文件 | 只允许去 Library；其余去向 `REJECT untrusted_source`，在卡 2 生效 |
| 噪音规则：会话 id、scratch 路径、PID、提交哈希、"N 个测试通过"之类 | `REJECT noisy` |
| 长度：MEMORY、EVOLUTION、context 候选超过 600 字 | `REJECT not_atomic`。说明提取阶段没有拆好 |
| 内容指纹：和任何受控存储里的条目完全相同 | 动作"不变"，`REJECT already_exists`，不进卡 1 |
| 会话已删除（冒烟探针等） | `REJECT session_gone` |

#### 卡 1：内容本身（一次 LLM 调用）

**质量**：任何一题答"否"即 `REJECT`。

| # | 维度 | 问题 | 原因码 | 答"否"的例子 |
|---|---|---|---|---|
| 1 | 原子 | 只讲一件事吗？两件强相关的事（比如决定和它的配套措施）算一件 | `not_atomic` | 一段里混了三个决定 |
| 2 | 判断价值 | 下次遇到同类情况，会改变做法吗？要有触发条件和对应做法 | `no_judgment_value` | "已完成 X"、"测试全绿" |
| 3 | 自洽 | 离开这次会话能读懂吗？没有"上面、刚才、这个方案"这类指代 | `not_self_contained` | "按上面的方案改" |
| 4 | 持久 | 这次会话结束后还成立吗？不是进度、状态或临时结论 | `not_durable` | "现在卡在 Gate 1" |
| 5 | 有依据 | 来自用户明说、实测、失败、评审结论，而不是推测 | `speculative` | "可能是因为…，以后注意" |
| 6 | 内部一致 | 前后不矛盾 | `self_contradictory` | 前面说必须，后面说可选 |
| 7 | 精炼 | 去掉了复述、过程和寒暄，长度合适 | `not_concise` | 把对话原样贴进来 |

**分类字段**：这几项不判对错，只给卡 2 用。

| 字段 | 取值 | 说明 |
|---|---|---|
| 类型 | `principle` 原则 · `correction` 纠错 · `decision` 决定 · `model` 心智模型 · `guideline` 经验 · `pitfall` 坑 · `process` 流程 | 只能从 `VALID_TYPES` 里选。选不出就 `REJECT untyped` |
| 主体 | `user`：和这个用户协作的方式（语言、沟通、权限、工作习惯）· `agent`：agent 自身的能力或错误 · `project`：某个项目的产品、技术、做法 · `world`：领域知识 | 决定去 context 文件、EVOLUTION、DDD 还是 MEMORY |
| 范围 | `single_project`（指明是哪个）· `cross_project` | 项目只能从项目快照里选。多个项目分不清时 `DEFER ambiguous_project` |
| 形式 | `rule`（会改变做法的判断）· `reference`（可以查阅的材料） | `reference` 去 Library |
| 证据强度 | `user_explicit` 用户明说 > `measured` 实测或测试 > `review` 评审结论 > `run_lesson` REFLECT > `inferred` 推断 | 卡 3 用它判断谁替换谁 |

七类的判别标准（判官 prompt 和黄金用例都用这一份）：

| 类型 | 层 | 判别句式 | 例子 |
|---|---|---|---|
| 原则 | 元认知 | 任何领域都成立的思考规则 | "结构性的修法优于聪明的检测" |
| 纠错 | 元认知 | 我曾经这样做错 → 正确做法 | "把评审意见当结论 → 应该当线索去验证" |
| 决定 | 认知 | 在几个方案里选了 X，理由是… | "ingestion 不设人工审批，靠事后审计" |
| 心智模型 | 认知 | X 是这样运作的（机制） | "context 文件有模板和 live 两份" |
| 经验 | 操作 | 做 X 时这样做效果好 | "squash 只从 merge-base 做" |
| 坑 | 操作 | 现象 → 原因 → 怎么避开 | "`git checkout <file>` 会冲掉未提交的改动" |
| 流程 | 操作 | 有顺序的步骤 | "回放 → 盲评 → 不降才合并" |

层由最终写入的段决定（`MEMORY_SECTIONS` 里每个段都带 `layer`），不由类型单独决定。比如 `COE Registry` 的类型是 pitfall，层却是元认知。

#### 卡 2：路由表（确定性，不调 LLM）

按顺序匹配，命中第一条就停：

| # | 条件（来自卡 1） | 去向 | 文件和段 |
|---|---|---|---|
| 1 | 主体 = `user`，形式 = `rule` | 用户 context 文件 | 偏好和工作方式 → `STEERING.md`；用户本人的事实（角色、背景）→ `USER.md`；用户的工具和环境 → `TOOLS.md` |
| 2 | 主体 = `agent` | `EVOLUTION.md` | 纠错 → Corrections；能力 → Competence |
| 3 | 主体 = `project` 或 `world`，范围 = `single_project` | 该项目 DDD（`Projects/<名字>/2-understanding/`） | 决定 → `PROJECT.md` § Recent Decisions；产品定位、目标、非目标 → `PRODUCT.md`；架构、机制、约定 → `TECH.md`；经验、坑、做对和做错的事 → `IMPROVEMENT.md` |
| 4 | 范围 = `cross_project`，形式 = `rule` | `MEMORY.md` | 按类型选段：Principles / Corrections / Decisions / Models / Guidelines / Pitfalls / Processes |
| 5 | 形式 = `reference` | Knowledge Library | 外部内容的学习卡 → `Learned/`；研究和分析 → `Notes/`；规格和参考 → `Library/` |
| 6 | 以上都不是 | 不写 | `REJECT no_destination` |

以下几处不在路由表里：
- SOUL、AGENT、IDENTITY、SWARMAI 是产品文件（§4.9），主体为 `user` 的规则一律进 STEERING。
- DailyActivity 是日志，由自己的 hook 写入。候选只在这次会话里有用的话，卡 1 第 4 题就会拒绝它，它本来就已经记在 DailyActivity 里了。
- 卡 0 标记为不可信来源的候选，命中 1、2、4 条时 `REJECT untrusted_source`。

#### 卡 3：和已有内容的关系（先检索，有相关条目才调 LLM）

**检索范围**：卡 2 选定的去向，**加上每轮都会注入的那几份**，即 MEMORY、EVOLUTION、STEERING、USER、TOOLS、SOUL、AGENT。原因是这几份每轮都在 prompt 里，新内容和它们一旦矛盾，模型每轮都会同时看到两条相反的规则。去向是 DDD 时，还要检索这个项目的另外三份 DDD 文档。

| # | 维度 | 问题 | 结果 |
|---|---|---|---|
| 1 | 检索完整 | 检索成功了吗？ | `OK_EMPTY`：直接新加，不调 LLM · `OK_HITS`：继续 · `UNAVAILABLE` / `STALE`：`DEFER` |
| 2 | 重复度 | 和最接近的条目比，是相同、被包含、包含它，还是不相干？ | 相同或被包含 → 不变，`REJECT already_exists` |
| 3 | 一致性 | 有没有冲突？冲突的是整条，还是其中一部分（比如只在某个子领域里冲突）？ | 一致 → 看第 5 题；冲突 → 看第 4 题 |
| 4 | 证据和时间 | 冲突时，谁的证据更强、谁更新？ | 旧的更强 → `REJECT conflicts_stronger`。新的更强，或同样强但更新：**整条冲突** → 替换；**部分冲突** → 新加，并标注取代范围（见下文） |
| 5 | 粒度 | 主题和触发条件都和某条相同吗？ | 相同 → 补充那一条；不同 → 新加 |
| 6 | 能否改动 | 要补充或替换的旧条目，是新能力写入的（带 `cand:`）吗？ | 是 → 直接改。否（已沉淀的内容）→ 旧条目一个字节都不改：补充改成"新加一条 + `extends`"，整条替换改成"新加一条 + `supersedes`" |
| 7 | 去向复核 | 检索到的内容说明它明显属于别处（比如同一件事已经在另一个项目的 DDD 里）？ | 改投，回到卡 2，只允许一次 |
| 8 | 关系 | 输出要写的边 | `supersedes`（只用于整条冲突）· `conflicts_with` + 取代范围（部分冲突，新条目已写入）· `extends` · `applies_to`（路径必须真实存在）· `produced_by`。被拒的冲突只写进审计，不进关系图 |

**整条冲突和部分冲突分开处理**

| | 判定标准 | 写入 | 读取侧 |
|---|---|---|---|
| 整条冲突 | 旧条目的核心主张整个被新内容否定，旧条目里没有别的还成立的内容 | 新条目 + `新 —supersedes→ 旧` | 注入时跳过旧条目，方式和现在剥掉 dormant、archived 条目一样（`context_directory_loader`、DDD 注入） |
| 部分冲突 | 旧条目只在某个子领域或某个方面和新内容冲突，其余内容仍然成立 | 新条目 + `新 —conflicts_with→ 旧`，边上带 `scope`（一句话说明取代的是哪方面，例如"ingestion 的审批方式"）。新条目正文末尾加一行：`取代：<旧条目标题>（<scope>）` | 旧条目照常注入，不跳过。新条目照常注入，靠这行标注告诉模型在这个方面以新条目为准。新旧两条在同一个注入块里时，新条目排在旧条目后面 |

- 判断是整条还是部分冲突，属于卡 3 第 3 题。判官拿不准时按部分冲突处理，因为部分冲突不会让任何旧内容消失。
- 被取代的那一面不靠删除来消失，而是靠标注让模型知道以哪条为准。这样旧条目不用改，其中仍然成立的内容也不会丢。
- 整条冲突时，跳过旧条目只影响注入，不改旧条目的文件。审计里能看到是哪条新条目让它被跳过的。

**用户说"记住"**：同样是一个候选，同样过全部卡，没有特权，只是卡 1 的证据强度是 `user_explicit`。被拒的话，agent 当场把原因码告诉用户。

**已知局限**：系统不用向量库，换了说法或跨中英文的重复，BM25 找不到，这类情况交给复审兜底（§4.8）。验收时单独统计卡 3 找回了多少。

### 4.2a Ontology 标签：三层七类 + 关系，同在这条流程里打

现在的 ontology 有两层规则，但打标签的方式和准入是脱开的：
- **分类**：七类（原则、纠错、决定、心智模型、经验、坑、流程），分属三层（元认知、认知、操作）。
  唯一来源是 `ddd_entry_lifecycle.VALID_TYPES` 和 `MEMORY_SECTIONS`（`ddd_entry_lifecycle.py:42-59`）。
  写入者没有声明类型时，由 `classify_entry_type()` 按关键词猜（`:509`、`:1598`）。层级决定生命周期，
  元认知层永不衰减，所以猜错一次影响很大。
- **关系**：10 种谓词（`knowledge_graph.VALID_PREDICATES`），存在 `.context/.knowledge-graph.yaml`，
  读取侧会用到它（`session_router.py:1126`）。但生产代码里唯一写关系的地方挂在 `bump_references()` 下面
  （`ddd_entry_lifecycle.py:955-970`），而这个函数没有生产调用者，所以关系图实际上已经不再增长了。

r8 把这两层都放进三张卡里，标签和准入同一次决定、同一次写入、同一行审计：

**卡 1 增加一题：这条是哪一类。**
- 判官从 `VALID_TYPES` 里选一个，并给一句理由（判别标准见 §4.2 卡 1）。层不由判官选，按最终写入的段从 `MEMORY_SECTIONS` 查出来，不另写一份对照表。
- 选不出来，或者给的类型不在 `VALID_TYPES` 里，结果就是 `REJECT`，原因码 `untyped`。一条知识归不进七类里的任何一类，
  说明它不是可复用的判断，"宁缺毋滥"。
- `classify_entry_type()` 的关键词结果作为一个对照信号写进审计。它和判官的结论不一致时不阻断写入，但会在审计统计里单独计数，
  用来发现 prompt 或关键词哪一边需要改。不再用它决定类型。
- 类型会影响卡 2：MEMORY 和 EVOLUTION 的目标段由类型决定（`MEMORY_SECTIONS` 已经是"类型 → 段"的映射）；
  DDD 条目同样带类型标签，决定它在 TECH / IMPROVEMENT 里的衰减规则。
- **Library 文件**不是一条条规则，不强行归类。frontmatter 里写 `ontology_types:`，列出文中主要讲到的类型，
  允许为空；它的关系照样按卡 3 写。

**卡 3 增加输出：关系。** 动作和关系是同一个判断的两种表达，一次给出：

| 卡 3 的结论 | 写入的关系 |
|---|---|
| 替换（新条目取代旧条目） | `新 —supersedes→ 旧` |
| 补充（作用于新能力写入的条目），或给已沉淀的旧条目写一条新条目（§6） | `新 —extends→ 旧` |
| 整条冲突，新的证据更强，或同样强但更新 | 旧条目是新能力写入的 → 替换并归档；是已沉淀的 → 新加一条，加 `新 —supersedes→ 旧`，旧文件不动，注入时跳过旧条目 |
| 部分冲突，新的证据更强，或同样强但更新 | 新加一条，加 `新 —conflicts_with→ 旧` 和 `scope`，正文标注"取代：…"；旧条目照常注入 |
| 冲突，旧的证据更强 | 不写，`REJECT conflicts_stronger`，审计里列出两条 |
| 条目里提到的文件、模块（从正文里抽取，再用项目快照里的仓库路径校验它确实存在） | `新 —applies_to→ 路径` |
| 来源 | `新 —produced_by→ 写入者:来源 id` |

- r7 里写在条目元数据里的 `relates_to:` 取消，改成关系图里的边。这样旧条目一个字节都不用改，
  关系只存在于 `.knowledge-graph.yaml` 这个旁路文件里。
- 关系在 §4.4 的同一个事务里写入，用现有的 `batch_add_relations()`（一次加锁、一次保存）。
  关系写入失败的话，整个候选回滚成 `DEFER`，不会出现"条目写进去了但关系没写"的情况。
- 关系的主语和宾语用条目锚点（和读取侧 `query_related_entries` 用的标识一致）。
- 关系图路径来自契约：全局一份，在用户的 `.context/` 下，和读取侧读的是同一个文件。不按项目另建一份，
  DDD 条目的锚点本身就带着项目和文档名。
- `bump_references()` 下面那段只能在死路径上运行的自动抽取删掉，关系只从准入流程产生。

**审计里看得到**：每一行候选都显示类型、层、关键词分类器的对照结果，以及写了哪些关系（可以点开对端的条目）。
统计里加上按类型、层的写入计数，以及判官和关键词分类器不一致的次数。

**复审**也用同一套标签：对新能力写入的条目，判断类型还对不对（错了就改类型标签，并记进审计），
有没有新的 `supersedes` 或 `conflicts_with`。和其他复审动作一样，只作用于带 `cand:` 标记的条目。

### 4.3 候选表：重试状态和审计记录是同一份

r4 的"拿不准就不写"把两种情况混在了一起："判断过，不该写"和"没能判断"。后一种情况下，只出现一次的候选
（人工纠正、REFLECT、backflow）会直接丢失。r6 用一张表同时解决重试和审计：

- **`ingestion_candidates` 表**放在每个用户自己的 `data.db` 里（`database/sqlite.py` 加一个迁移）。
  `submit` 在跑任何一张答题卡之前，先插入一行。一行就是一个候选，从进来到结束的全过程都在这一行：

| 字段 | 内容 |
|---|---|
| `candidate_id`、`created_at`、`decided_at` | 幂等键；什么时候进来；什么时候有了结论 |
| `writer`、`source` | 谁产出的（REFLECT、蒸馏、backflow、skill…）；出处（会话 id、run id、消息 id） |
| `content`、`content_hash` | 候选全文和它的哈希 |
| `project_snapshot` | §4.5 的项目快照 |
| `card1` / `card2` / `card3` | 每张卡的结构化答案：每题 PASS/REJECT/DEFER、原因码、判官给的一句话理由 |
| `ontology` | 类型、层、关键词分类器的对照结果、写入的关系列表（§4.2a） |
| `outcome` | `written` / `rejected` / `deferred` / `expired` |
| `target` | 写到了哪：存储、文件路径、条目锚点、动作（新加 / 补充 / 替换） |
| `replaced` | 被替换或归档的旧条目：锚点和归档位置 |
| `versions` | 契约、prompt、模型的版本 |
| `retry_count`、`next_attempt_at`、`error_class` | 只给 `deferred` 用 |

- 这张表**不进召回、不进索引**，它是状态和审计记录，不是知识。
- 一个后台 worker 按 `candidate_id` 幂等地重试 `deferred`，退避重试，最多 5 次，之后标成 `expired`。`rejected` 不重试。
- 保留 180 天（可配置），由会话结束时的清理顺带删除过期行。
- 这张表取代 r5 里的 WAL 文件和 `admission_decisions.jsonl`，一个事实只有一个来源。

**没有人工审批这一层。** 三张卡就是决定本身：通过就写入，拒绝就不写，所有写入者都一样，包括 agent 调用 skill、
用户说"记住"、cultivation 提案。人的角色是事后看审计（§4.11），不是事前批准。cultivation 那个没有认证的
批准接口不再直接写入，提案改成作为普通候选进入 `submit`。

**一条安全底线**（`security-coding-baseline` B5）：来源里含有不可信外部内容的候选（网页抓取、MCP 返回、
别人发的频道消息），卡 1 一律拒绝写进 MEMORY、EVOLUTION 和 context 文件，原因码 `untrusted_source`；
写进 Library 可以。同时每个候选都必须过 `MemoryGuard` 的注入扫描。全自动之后，这是唯一防止外部文本变成
长期指令的关口。

### 4.4 执行：校验版本，再归档，再写入

在卡 3 检索和真正写入之间，别的写入者或复审 job 可能已经改过目标文件。执行分这几步：

1. 拿到目标存储的锁（MEMORY、EVOLUTION、context 文件用各自的 `.md.lock`，DDD 用文档锁）。
2. 在锁里重新读取，比较卡 3 记下的条目版本（条目内容哈希；新加时用整文件哈希）。不一致就 `DEFER`，重新跑卡 3。
3. 需要归档的（替换），先归档，再从原文件移除。用 `archive_and_strip_lines`（`ddd_entry_lifecycle.py:1256`）；
   EVOLUTION 的 `###` 块用 `archive_raw_lines`（`:1284`），整份 context 文件用整文件快照。r4 写的 `archive_entries`
   只追加，不移除，用错了。这三种归档统一包成一个 `archive_then_strip()` 入口。
4. 写入新内容，原子替换文件。新条目的元数据里带 `cand:<candidate_id>`，Library 文件在 frontmatter 里带
   `ingestion_candidate`。这是"由新能力写入"的唯一标记，复审和卡 3 都靠它来区分新旧内容。
5. 每一步都写进一个按 `candidate_id` 记录的事务日志。重启后先读日志：归档做完、写入没做的，补做写入；
   每个归档都带幂等 id，重做不会产生重复。

测试要覆盖：两个写入者同时写、复审和写入者同时写，以及在每一步之后人为让进程崩溃，验证都能恢复。

### 4.5 Knowledge 写入者和项目快照

- **项目快照**：`session_bound_project` 是进程内的状态，会话释放时就清空了，而 hook 是之后才在后台跑的。
  r5 改成在 hook 入队之前（`lifecycle_manager.enqueue_hooks`），把会话碰过的仓库根目录、对应的绑定、
  以及"是否有歧义"一起快照进 `HookContext`。同样的快照也写进 DailyActivity 每条 JSONL 记录。
  延迟运行的扫描只读这份快照。`context_health_hook.py:961-969` 写死 SwarmAI 的那段代码删掉。
- **backflow**：`candidate_id` 用消息 id，同一条消息重放也不会产出第二份；候选过三张卡。
  按审计的样本，15 篇里大约能留下 6 篇。
- **checkpoint**：**新的** checkpoint 不再写进 DailyActivity，改写到不进索引的恢复文件
  `<app data>/state/checkpoints/<session>.json`。会话正常结束时删掉这个文件，异常结束时它还在，用来恢复。
  这样既不用去改历史上的 DailyActivity 文件，也不存在跨午夜的问题。已有的 checkpoint 块留在原地不动，
  在读取侧不再召回（§4.7）。
- **已删除的会话**：DailyActivity 和 backflow 两个 hook 开头先查会话是否还在，不在就不写。
- **DailyActivity 块格式**：`upsert_session_block` 加断言：块以条目头开头，块内没有 `## `。
  同时追查 `## Session Resume` 这个独立块是谁写出来的。

### 4.6 使用信号：只记录真正送达模型的条目

- 注入块渲染完之后，收集最终块里**实际出现**的条目锚点，调用一次批量的 `record_ddd_deliveries()`：
  一把锁、一次原子写入。现在是每条命中单独加锁、读文件、写回，这一步在用户等待回复的路径上。
- 只输出了指针、没有输出正文的条目不记。`record_ddd_hit` 删除。
- MEMORY 每轮都整份注入，"送达"对它没有区分度，所以不做使用统计。不再根据 `ref:N` 做任何判断；
  这个字段保留，不批量改写已有条目。
- 这一步计入 recall 延迟，和现在一样测 p50 / p90，要求比现在快（N 次文件事务变成 1 次）。

### 4.7 注入形态和读侧联动（不改语料）

- **DDD 注入条目正文。** 现在 `_recall_ddd`（`recall_multi.py:800-967`）只返回 section 指针和最多一条 entry；
  注入函数之后又把 DDD 文档重新加载、解析一遍（`session_router.py:1888-1920`）。r5 改成一次检索直接返回一个结果对象，
  里面有选中的条目正文、校验过的 section、排名。渲染、长度上限、来源标注、使用信号都用这同一个对象，
  不再做第二次检索（不调用 `recall_ddd_entries`）。长度上限用 `DOMAIN_CHAR_CAPS["ddd"]`，经 `_deliver_block` 注入，
  计入会话预算。section 已经不存在的直接丢掉。
- **Library 先多取，再过滤，再补足名额。** 现在 FTS 只取前 8 条（`recall_engine.py:97`），过滤掉的不会由第 9 条之后的补上。
  改成先取 24 条，再依次：丢掉只有 frontmatter 的、丢掉正文不到 200 字的、丢掉旧 checkpoint 块、
  按内容哈希去重（用 `RecallEngine.search()` 现有的实现，`recall_engine.py:108-137`，不另写一份），最后补足到 8 条，
  再交给 `LIBRARY_TOP` 和长度预算。测试：前 7 条都无效或重复，第 9 条有用，它必须进入结果。
- **标题在渲染时显示在来源行**，取 frontmatter 的 `title` 或文件路径。不写进 chunk 正文，不参与覆盖率计算。
- **索引排除依赖目录**：来源策略里，路径含 `node_modules`、`.venv`、`dist`、`build` 的文件不进索引。
  测试：先放一个已经被索引的 `node_modules` 文件，同步一次后，它的 chunk 从派生索引里删掉了，源文件还在。
- 不重切、不重建索引，所以 prompt 内去重、覆盖率门槛、排名名额这三条读侧规则的输入都不变。

### 4.8 退出：复审只管新能力写入的条目

- **范围**：复审只处理 `governed_store` 写入的条目。识别方式是条目元数据里的 `cand:<candidate_id>`
  （Library 文件用 frontmatter 的 `ingestion_candidate`），见 §4.4。没有这个标记的条目，都是上线前已经沉淀的内容，
  复审不读、不判、不动。
- 对范围内的条目，按存储契约逐条判断：保留 / 改投 / 替换 / 归档 / 重复。判断结果直接执行，不出提案。
  执行同样走 §4.4：锁里校验版本，先归档再移除，记进事务日志。
- 每次复审也是一批候选，写进 `ingestion_candidates`（`writer=review`）。审计里能看到每一次退出的理由，
  以及旧内容归档到了哪里。
- 读**完整**文件，分段送判官，不再只看前 8K 字符。TECH 也包括在内。
- 每次处理的条数有上限。复审用单独的维护预算（§4.10），不挤占写入的预算。
- **所有运行模式下都能跑**：复审挂在会话结束的 hook 上，节流为每 7 天最多一次，上次运行时间记在 `<app data>/state/`。
  daemon / hive 下的定时 job 只负责触发同一个函数，不另写一份。
- 版本族（`-v2` 与 `final` 并存）：只处理新能力写入的文件。

### 4.9 context 文件：自动写进用户的 `.context/`，不写源码仓库

context 文件有两份，位置不同，很容易混：

| | 位置 | 归属 |
|---|---|---|
| 模板 | `backend/context/*.md`，代码里叫 `templates_dir`，由 `Path(__file__)` 推出（`prompt_builder.py:1244`、`initialization_manager.py:438`） | 产品。dev 模式和 worktree 里运行时，它**就在源码仓库里**；打包后的 app 里它在安装包内 |
| 用户的 live 文件 | `<workspace>/.context/*.md`，workspace 来自 `get_workspace_path()` | 当前用户。prompt 读的是这一份 |

1. **能写的文件用一个显式字段标出来**：`ContextFileSpec` 新增 `auto_write_eligible`，只有 USER、STEERING、TOOLS 为真。
   不能从 `user_customized` 推出来，因为 SELF、MEMORY、EVOLUTION、KNOWLEDGE 的这个标记也是真。
   SWARMAI、IDENTITY、SOUL、AGENT 是产品文件，每次会话开始都会从模板覆盖回去，所以针对这个用户的判断规则一律改投 STEERING。
2. **目标路径在卡 2 做决定时就定下来**：`get_workspace_path()/.context/<文件名>` 解析成绝对路径，和当时的文件哈希一起存进候选那一行。
   执行时只用这个路径，不重新推算。
3. **执行前在锁里校验**：路径必须正好落在 live `.context/` 目录下，不能落在 `templates_dir` 下，文件必须没被 git 跟踪
   或已被忽略（SwarmWS 本身是 git 仓库，`.context/` 在 `.gitignore` 里）。哈希必须和决定时一致，不一致就 `DEFER`，按当前内容重跑卡 3。
4. **写入原语** `write_context_cas()`：加锁 → 锁里比较哈希 → 整份旧文件归档 → `MemoryGuard` 安全扫描 → 按文件名做整份文档的
   格式校验 → 原子替换 → 权限保持 0o644。自动写入和两个工作区编辑接口都用这一个原语。
5. **源码仓库里的模板**只在开发时、由人明确指定要改时，走普通的代码修改流程（worktree、commit）。受控写入模块没有写
   `templates_dir` 的能力。

测试：在 worktree 里运行时（`templates_dir` 在仓库里）执行被拒；目标是 SOUL 时被改投到 STEERING；决定之后用户改过 STEERING
时变成 `DEFER` 并重跑；每种情况下 `backend/context/` 都没被写过。

### 4.10 判官预算和成本

- 判官调用都在 backend 进程里（daemon、subprocess、hive 都是单进程）。现在的预算（60 次 / 300 秒）只在进程内有效，而且不是线程安全的。改成加锁，并分成几个优先级通道：
  用户纠正 > 会话结束 > REFLECT > 维护（复审）。复审单独一份预算，用完就等下一轮，不会挤占写入。
- 预算用完返回 `DEFER`，候选留在候选表里，不会丢。
- 每天有多少候选、每个候选调几次判官、用多少 token、`deferred` 最长积压多久，都从候选表里统计，显示在审计顶部。
  上线第一周实测之后，再定上限和告警阈值，这里不先定数字。

### 4.11 Ingestion 审计（产品功能）

人不做事前审批，所以审计必须是一个产品功能：每个用户都能看到系统自己往记忆里写了什么、为什么写、写到哪、为什么拒绝。

- **数据**：只来自 `ingestion_candidates`（§4.3），不新建存储。
- **接口**：`GET /api/ingestion/candidates`，可以按时间范围、写入者、存储、结果（written / rejected / deferred / expired）、
  原因码、关键词筛选，分页返回；`GET /api/ingestion/candidates/{id}` 返回一行的全部内容。这两个接口只读，
  跑在 `nav` 线程池上（`executors.run_nav`），和其他 Left Nav 后端一样不影响 chat。字段在 Pydantic 响应模型里声明，
  前端走 `toCamelCase` 映射。
- **界面**：Left Nav 里放一个 overlay（遵守 Nav visual system，用共享组件，中英文 i18n）：
  - 列表：时间、写入者、内容开头、去向、结果、原因码。
  - 点开一行：完整内容；三张卡逐题的答案和判官给的理由；写到了哪个文件、哪个条目（可以点开）；
    如果替换了旧条目，显示旧内容和它的归档位置。
  - 顶部统计：最近 7 天按结果、去向、原因码的计数，以及 `deferred` 的积压数。
- **不提供审批按钮。** 审计里如果发现某一类判断持续出错，应该去改契约、prompt 或规则，而不是逐条推翻。
  用户可以直接编辑目标文件，编辑同样走 `write_context_cas()` 或受控写入，并且会作为 `writer=user_edit` 记进审计。
- **通知**：判官或检索连续失败超过 1 小时，或者架构测试之外的运行时检查拦下一次绕过，就发一条通知。

### 4.12 对每个用户都可用，不依赖当前这台机器

这一节里的每一条，都是验收项（§7）：

- **路径**：所有路径都来自 `config` / `get_workspace_path()` / app 数据目录，不写死 `~/.swarm-ai`、某个仓库，或某个项目名。
  `context_health_hook.py:961-969` 里写死的 `SwarmAI` 删掉。项目归属只看用户自己的 DDD 和 `bindings.yaml`。
- **平台**：文件锁一律用 `utils.file_lock`（Windows 用 msvcrt），不直接用 `fcntl`；原子写入用现有的跨平台工具。
  backend-windows 的 CI 要覆盖受控写入、候选表和 context 写入。
- **模式**：daemon、subprocess、hive、dev 四种模式下都能写入、重试、复审、审计。重试 worker 和复审都挂在
  会话结束的 hook 执行器上，不只依赖只在 daemon / hive 下运行的定时 job。
- **打包**：打包后 `templates_dir` 在安装包里，§4.9 的校验照样成立。不调用 `sys.executable -m`。
- **不依赖开发者自己的东西**：运行时不依赖 `Projects/SwarmAI` 里的 skill、`backend/scripts/recall_replay.py`、
  或者盲评脚本。这些只是开发时的验收工具。
- **skill**：`s_memory-distill` 改造成一个手动触发入口，而不是删除。用户说"整理一下记忆"时，它调用 `governed_store` 现成的两个动作：
  马上跑一次蒸馏（同一套三张卡），马上跑一次复审，然后把这次的审计结果汇总给用户。skill 里不再写任何阈值、段名、锁的细节
  （这些都由契约给出），所以它不会再和代码对不上。

## 5. 每个事实在哪、谁写谁读

| 事实 | 唯一来源 | 写 | 读 |
|---|---|---|---|
| 每个存储的准入问题、动作、检索范围、退出；来源策略 | `store_contracts` | 人改 | `governed_store`、读侧、复审 |
| 哪些 context 文件能自动写 | `ContextFileSpec.auto_write_eligible` | 人改 | `governed_store` |
| 一个候选的状态、三张卡、去向、理由 | `ingestion_candidates` | `submit`、重试 worker、复审 | 重试 worker、审计接口 |
| 会话属于哪个项目 | `HookContext` 里的项目快照，以及 DailyActivity 记录里的同一份快照 | 入队前的快照 | 卡 2 |
| 条目的类型和层 | 条目上的类型标签；层从 `MEMORY_SECTIONS` 查 | 卡 1 | 读取侧、生命周期、复审 |
| 条目之间、条目和文件的关系 | `.context/.knowledge-graph.yaml` | §4.4 的事务（`batch_add_relations`） | 读取侧 `query_related_entries`、审计 |
| 一个条目有没有送达过模型 | `.ddd-usage.json` | 注入后的批量记录 | 复审 |
| context 写入的目标 | 候选行里的绝对路径和文件哈希 | 卡 2 | 执行前校验 |

热路径：答题卡、候选表、复审都只在后台 hook、REFLECT、skill 和定时任务里跑，不碰 chat 发送路径。
读侧的改动（§4.6、§4.7）在发送路径上，要求延迟实测不比现在差：多取 24 条只多一次 FTS 查询，
DDD 少了一次重复加载，使用信号从 N 次文件事务降到 1 次。

## 6. 已经沉淀的内容：一律不动

这次只做功能。上线之后的沉淀按新能力走；上线前已经沉淀的内容，新功能一律不碰：

- **不改写**：已有的 MEMORY、EVOLUTION、DDD 条目，用户 context 文件里已有的内容，Note、Library、Learned、
  DailyActivity（包括其中已有的 checkpoint 块）、会话、Reports、Signals。不改名，不移动，不删除，不改写，不加元数据。
- **新内容怎么和旧内容相处**：卡 3 检索时会看到旧条目，这是为了判断"是否已经有了"，旧条目本身不会被改：
  - 和旧条目重复 → "不变"，不写；
  - 补充或修正旧条目 → 作为**新条目**写入，在关系图里加一条 `新 —extends→ 旧`，旧条目原样保留；
  - 和旧条目整条冲突，且新内容证据更强或更新 → 新加一条，加 `supersedes` 边，旧条目不动，注入时跳过；
  - 部分冲突 → 新加一条，加 `conflicts_with` 和取代范围，旧条目照常注入；新内容证据更弱 → 不写；
  - 只有 `cand:` 标记的条目（也就是新能力写入的）才能被"补充"或"替换"。
- **复审**只处理新能力写入的条目（§4.8）。
- **已有的维护机制**（size valve、decay、每周 Memory Health、EVOLUTION maintenance）：这次不扩大它们的范围，也不改它们的规则。
  它们今天就会处理已有条目，这一点保持原样。唯一的改动是，其中三处"直接删除"改成"先归档再移除"（§4.4）。
  这只会让删掉的东西少，不会多动任何内容。
- **读取侧**（§4.7）只改变"召回什么、怎么渲染"，不改任何文件。旧 checkpoint 块、frontmatter-only 段、重复段
  都是在召回时被过滤掉的。
- **派生索引**：`node_modules` 之类的 chunk 在下次同步时从索引里移除。索引本身就是从文件派生出来的，源文件不动。
- 验收：
  - 测试里构造一个只有"已沉淀内容"的工作区（没有 `cand:` 标记），对它跑写入、卡 3 的补充和替换、复审，
    结束后所有文件的 SHA-256 必须和开始时一致。只允许出现新文件和新追加的条目。
  - 上线后，在第一次复审前后各对真实工作区做一份清单。除了新能力写入的条目，以及已有维护机制本来就会做的变化，其余必须一致。

## 7. 实施和验收

每一步一个 worktree、ff-merge 一个 commit，不 push、不 rebuild。

**第 1 步：读取侧和索引**（不改语料）
1. `store_contracts` 的来源策略部分，加上依赖目录排除。
2. Library：多取、过滤、补足名额；标题在渲染时加；旧 checkpoint 块不召回。
3. DDD：一次检索返回条目正文，校验 section，批量记录使用信号。
- 验收：固定回放清单（第 101–200 个会话，清单哈希写进文档），A/B 随机、盲评，判官的评分标准和模型版本固定。
  通过条件：噪音比例下降至少 3 个百分点；有用比例和"需要且拿到"比例各自下降不超过 1 个百分点；
  注入总字符数和 recall p90 都不超过基线。

**第 2 步：受控写入、三张卡、候选表、审计**（一次切换）
4. `store_contracts` 补全契约和四种身份；`ingestion_candidates` 迁移；`governed_store.submit` 加三张卡和 §4.4 的执行。
5. 所有语义写入者一次全部切过来；底层写函数改成私有；启用架构测试；删除旧路径。
   writeback 删除前，先看它过去 30 天丢弃的候选；`s_memory-distill` 按 §4.12 改造。
6. 项目快照、backflow 身份、checkpoint 改存到恢复文件、已删除会话不写、DailyActivity 块格式断言。
7. 审计的接口和 overlay（§4.11）。
- 验收：
  - 夹具用今天审计里已经分好类的样本（MEMORY 97 条、DDD 40 条、backflow 15 篇），阈值先写定：
    这些样本只作为候选输入（复制出来喂给 `submit`），不改原文件。
    卡 1 对"该留"的放行率 ≥ 85%，对"不是持久内容"的拦截率 ≥ 80%；卡 2 把 51 条项目专属 MEMORY 条目路由到正确 DDD 的比例 ≥ 80%；
    卡 3 对审计确认的重复对全部给出"不变"，对 4 条过时条目至少 3 条给出"替换"；卡 3 检索的召回率单独报告。
  - 黄金用例：去向的每条边界、多项目歧义、检索 `UNAVAILABLE` / `STALE`、空检索、`untrusted_source`。
  - 真实用例（2026-10-26 15:16 用户原话"答题卡过了就直接进……审计就行"）：卡 2 路由到 SwarmAI `PROJECT.md` § Recent Decisions
    （不是 STEERING）；卡 3 对 PRODUCT § Autonomy Phases 和 MEMORY "confident-only" 判为部分冲突，写 `conflicts_with` 并带取代范围，
    对 PROJECT"Autonomy 正典"判为 `extends`；三条旧条目文件不变，下一轮注入时三条都还在，新条目带"取代：…"标注。
  - 整条冲突用例：注入时跳过旧条目，旧条目文件的 SHA-256 不变。
  - Ontology：手工标注 49 条（七类各 7 条，跨三层，包含容易混淆的成对样本，例如"原则"和"经验"、"坑"和"纠错"）。
    判官类型一致率 ≥ 85%，层一致率 ≥ 95%（层错了会改变生命周期，所以要求更高）。
    卡 3 的每种结论都要写出表里对应的关系，`applies_to` 不能指向不存在的路径。
    关系写入失败时，候选回滚为 `DEFER`，条目文件不变。
  - 验收矩阵：写入者 × 卡 × 动作 × 失败类型。故障注入覆盖：预算用完、LLM 出错、输出格式错、检索不可用、
    候选表写失败、拿不到锁。
  - 并发和崩溃：两个写入者同时写、复审和写入同时进行、§4.4 每一步之后崩溃，都能恢复。
  - 架构测试：故意加一处直接写 MEMORY，测试必须失败。
  - 每条规则做 mutation 检查：撤掉修复，对应测试必须变红。
  - 审计：每个 `outcome` 都能在接口和 overlay 里看到完整的三张卡和理由；前端测试覆盖列表、详情、筛选，中英文都有。
  - §4.12 的每一条：Windows CI 绿；subprocess 模式下重试和复审能跑；打包路径下 §4.9 的校验成立；
    代码里搜不到写死的 `SwarmAI` 和 `~/.swarm-ai`。
  - 已沉淀内容的 SHA-256 清单前后一致（§6）。

**第 3 步：退出**
8. `archive_then_strip()` 替换三处直接删除。
9. 复审挂到会话结束 hook，7 天节流；daemon / hive 下的定时 job 只负责触发。
- 验收：复审对审计样本（复制一份，加上 `cand:` 标记当夹具）的判断与审计结论一致率 ≥ 80%；
  有一个测试证明没有 `cand:` 标记的条目，复审一条都不碰；每条退出路径都有"先归档后移除、崩溃可恢复"的测试；
  每次复审的每个动作都能在审计里看到。

上线后的第一周，每天看一次审计统计：`rejected` 的原因码分布、`deferred` 积压、每个写入者写入的条数。
某一类判断持续出错，就改契约或 prompt，改完把版本号加一。

## 8. 不做

- **不做人工审批，不做提案。** 判断交给三张卡，人看审计。
- **不做候选收件箱和"提升"流程。** 候选表只记录状态和审计，不进召回。
- **不做自动的语义合并。** 近似内容由卡 3 在写入前判断，卡 3 没找到的由复审处理。
- **不引入向量库。** 换了说法或跨语言的重复，卡 3 的 BM25 找不到，这是已知局限，由复审兜底，验收时单独统计。
- **不做影子模式、切换日、新旧写入并存。**
- **不整理已经沉淀的内容**：MEMORY 里那 51 条项目专属条目和 12 条重复保持原样。
- **不整类排除** Pollinate 或 `Knowledge/Archives/`，只排除依赖目录。
- **不改 DailyActivity 的 LLM 提炼门槛。**
- **不重切、不重建索引。**

## 9. 已确认

1. 已有的维护机制（size valve、decay、每周 Memory Health、EVOLUTION maintenance）范围不扩大，规则不变。
   其中三处"直接删除"改成"先归档再移除"（§4.4），这样只会删得更少，不会多动任何内容。
2. 部分冲突只做标注，不跳过旧条目；整条冲突时才 `supersedes`，并在注入时跳过旧条目（§4.2 卡 3）。
3. 先从第 1 步（读取侧和索引）开始实施，这一步不改任何文件。

## 10. 评审记录

r4 之后，两位独立 reviewer 各审了一遍。两人都没有本会话的上下文，只读代码和这份文档。
- **代码事实和接入**：3 个 BLOCKER、9 个 MAJOR。已全部吸收：可提案文件的判定、git 仓库检查、项目快照、
  入口合并、写入时检索的语料、REFLECT 的另外两条直写路径、使用信号批量化、DDD 一次检索、Library 补足名额、
  checkpoint 跨午夜、归档函数用错、`MemoryGuard` 不是格式校验、写入者清单不全。
- **设计本身**：5 个 BLOCKER、8 个 MAJOR。已全部吸收：结构上只有一个写入口、`DEFER` 和 WAL、人工权限不能由调用方声明、
  四种身份、确定性的卡、带类型的检索结果、历史数据的定义、写入前校验版本、复审的权限、预算通道、验收矩阵、
  有阈值的盲评、影子模式和逐类切换、可观测性。
- 有一条没有照搬："跨进程的配额服务"。判官调用都在 backend 这一个进程里，所以只加锁、分优先级通道，不另建服务。

r6 按 Gawan 的决定改了以下几处：
- **全自动**：去掉人工审批和提案，context 文件也自动写入；三张卡就是最终决定。r5 的人工权限（批准记录、nonce）删除。
- **没有切换期**：去掉影子模式、切换日和逐个写入者切换，一次上线、直接生效。live 知识文件里的已有条目由复审全自动处理。
- **审计是产品功能**（§4.11）：候选表同时记录重试状态和审计，取代 WAL 文件和决定日志；另做接口和 Left Nav overlay。
- **对每个用户都可用**（§4.12）：不写死路径和项目名、跨平台锁、四种运行模式都能跑（复审挂在会话结束 hook 上）、打包后照样成立。
- **`s_memory-distill` 改造而不删除**：变成手动触发入口，不再自己写阈值和规则。
- **保留一条安全底线**：来源含不可信外部内容的候选，不写进 MEMORY、EVOLUTION 和 context 文件。

r7 按 Gawan 的决定：已经沉淀的内容一律不动，只做功能。复审和卡 3 的"补充 / 替换"只作用于新能力写入的条目（`cand:` 标记）；
对旧条目的补充或修正写成新条目，带 `relates_to:`。不再处理 MEMORY 里的积压。

r8 把 ontology 的三层七类和关系标签放进同一条流程（§4.2a）：类型由卡 1 判定，层从类型查出来，关系由卡 3 的结论产生，跟条目在同一个事务里写入。`relates_to:` 改为关系图里的 `extends` 边。`bump_references` 下面那段已经不会运行的关系抽取删掉。

r9：列清每张卡的维度；新增卡 0（规则，不调 LLM）；"值得记 / 重复"从卡 1 挪到卡 3；卡 2 改为确定性路由表，覆盖全部去向和段；卡 3 的检索范围总是包含每轮都注入的那几份文件，新增证据强度维度；已沉淀的旧条目被更新、更强的内容否定时，写新条目并加 `supersedes`，旧文件不动、注入时跳过（r8 在这种情况下会直接拒绝，用户最新的明确决定反而写不进去）。

r10：部分冲突改成写新条目，加 `conflicts_with`、取代范围和正文标注，旧条目照常注入；只有整条冲突才 `supersedes` 并在注入时跳过旧条目；拿不准时按部分冲突处理。三处直接删除改为先归档再移除。

## 11. 验收结果

（实施后填写。）
