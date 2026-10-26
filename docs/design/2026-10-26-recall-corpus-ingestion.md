---
title: Recall 语料的写入与准入 — Memory、DDD、Knowledge 三块统一的准入、身份、使用信号与退出
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r12 — 定稿，已吸收三轮独立评审（修订记录见 §10）
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
本版改成在读取和渲染时处理（§4.9），不重切、不重建索引。

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

所有会被写入的存储共用一份带类型的契约（`backend/core/store_contracts.py`）。r1 的来源策略也并进来，替换掉 `_SKIP_*`、`NO_INJECT_PREFIXES`、`_DAILY_RE`、`ARCHIVE_PREFIX`。所有写入者、准入入口、读取侧、复审都只读这一份契约。

| 存储 | 属于召回语料吗 | 准入问题 | 允许的动作 | 退出 |
|---|---|---|---|---|
| MEMORY | 是（每轮注入，有确定性的预算，§4.9） | 跨项目、不依赖查询、多数会话都会影响判断 | 新加 / 补充 / 替换 | 复审（§4.10） |
| EVOLUTION | 是（每轮注入，有确定性的预算） | 关于 agent 自己的能力或纠正，不针对某个项目 | 新加 / 补充 / 替换 | 复审 |
| 项目 DDD（PRODUCT / TECH / IMPROVEMENT / PROJECT） | 是（按项目按需注入） | 离开这次会话或 run 以后，还会影响这个项目的判断 | 新加 / 补充 / 替换 | 复审 |
| Knowledge Library（Notes / Library / Learned） | 是（召回时排名） | 离开原会话也能独立读懂、可以复用的参考材料 | 新加（一个文件） | 复审 |
| 用户 context 文件（USER / STEERING / TOOLS） | 是（每轮整份注入） | 改变的是这个用户"怎么判断" | 新加 / 补充 / 替换，写到用户的 `.context/`（§4.11） | 复审 |
| DailyActivity | 是（日志，按日期规则召回） | 不过答题卡，它是日志 | 按会话 id 覆盖写自己的块 | 不退出 |
| 候选表 `ingestion_candidates` | **否**（状态和审计） | — | 状态机迁移（§4.5） | 保留 180 天 |

**身份**：r4 把这几样混在了一起，r11 分开定义。

| 身份 | 用途 | 定义 |
|---|---|---|
| `candidate_id` | 同一个候选重放时不重复处理（幂等） | `writer:来源 id:序号` 的哈希，例如 backflow 用消息 id |
| 来源信封 | 依据的输入事件、证据强度、是否被污染（§4.4） | 由输入账本和写入者给出的 `source_event_ids` 算出，候选自己不能声明 |
| 内容指纹 | 完全相同的内容去重 | 规范化后正文的哈希（复用 `content_signature`） |
| 单元锚点 | 卡 3 的比较对象、关系图的端点、链头转发 | `工作区相对路径 # 段标题 # identity`，由 `parse_units()` 统一给出（§4.3）。光靠标题不够唯一 |
| 基线 | 哪些内容属于"已沉淀" | 上线时生成的清单，用户编辑时修订（§6） |

## 4. 设计

### 4.1 写入口：拦截 + 检测，写入者清单从调用点扫描生成

**做不到的事要先讲清楚**：在 PreToolUse 这一层，挡不住所有写法。Bash 写入检测（`file_change_classifier.py:99-106`）只识别有把握的模式，有意宁可漏报。`python patch.py`、`perl -pi`、带变量的路径、可执行程序在内部写文件，它都看不出来。要彻底挡住，必须在操作系统层把受控文件对 agent 子进程设为只读，写入只能走 daemon 的 RPC。这个改动太大，而且各平台做法不同（r12 决定不做）。r12 的承诺是：**常见写法当场拦下，其余所有绕过都能被看见。**

1. **适配器**：`governed_store` 里的写入适配器，是 backend 代码写受控路径的唯一函数。受控路径包括 MEMORY、EVOLUTION、USER、STEERING、TOOLS、各项目 `2-understanding/` 下的四份 DDD、`Knowledge/{Notes,Library,Learned}`、`.knowledge-graph.yaml`。这些路径由契约给出，按当前工作区的真实根目录解析。临时工作区、测试工作区不算受控路径。
2. **拦截** `governed_corpus_gate`（PreToolUse，匹配 Write、Edit、MultiEdit、NotebookEdit、Bash）：agent 直接写受控路径时拒绝，并提示改用 `"$SWARM_BACKEND_BIN" ingest submit --stdin`。Bash 部分复用 `governance_file_gate` 的写入检测。它只是**提前提示**，不是安全边界。它不碰代码、测试和提交；登记在 `hook_builder.py`，AGENTS.md 同步更新。
3. **检测**：每次 drain 时（§4.10），对每个受控文件做一次比对，比较当前文件的单元集合，和"基线 + 适配器写过的所有版本"。凡是对不上的改动，都在审计里记一行 `writer=unknown`，内容是这次改动的 diff，同时发通知。被检测出的改动不回滚，人决定怎么处理；但这些新内容没有 `cand:` 标记，不会被复审，也不会被当作已沉淀内容保护。
4. **静态测试**：作为补充，扫描 `backend/` 里对受控路径的文件写入点（`open(...,"w")`、`write_text`、`replace`、`rename`、`locked_write`、`apply_to_ddd`、`annotate_entry` 等），每一处都必须在白名单里，并标明类别。

**`ingest submit` 命令**：在 `desktop_main.py` 的分发里，和 `py`、`pipeline-v1`、`hook`、`daemon` 并列，加一个 `ingest`，放在 daemon import 之前，只初始化数据库和工作区服务。内容从 stdin 或权限为 0600 的 JSON 文件读入，不放在 argv 里，避免进程列表和长度限制的问题。命令输出结构化 JSON，退出码固定。这个命令只负责入队，不在进程里跑答题卡，答题卡由 backend 的 drain 跑。

**写入者清单**：r11 是按机制名逐个列的，漏了好几个。r12 改成从生产代码的调用点扫描生成，静态测试保证清单不会再漂。

| 类别 | 写入者（调用点） | 规则 |
|---|---|---|
| 语义写入（走 `submit`） | REFLECT 新加（`reflect_job.py:267-282`）、REFLECT 强化/细化（`annotate_entry`，`:157-213`）、REFLECT 写 pipeline-lessons（`reflect_library.py:145-228`）；会话结束蒸馏；即时纠正；`memory_extractor.py:351`；`evolution_optimizer.py:899`；DailyActivity 决策/纠正 → DDD（`context_health_hook.py:902-1041`）；backflow；cultivation 的三路候选生成；agent 调用的 skill（`s_persist`、`s_self-evolution`、`s_ddd-persist`、改造后的 `s_memory-distill`）；`ingest submit` | 全部答题卡 |
| 退役：会改已沉淀内容 | 每周 Memory Health 的修改阶段（`memory_health.py:500-1057`）；ContextHealth 对 MEMORY 的生命周期元数据改写、归档剥离、去重、旧索引清理（`context_health_hook.py:1482-1660`），以及对 KNOWLEDGE、EVOLUTION 的去重和改写（`:1666-1870`）；ContextHealth 的生产验证改成熟度（`:1190-1272`）；`ddd_maturity.promote_section()`（`:348-385`）；EVOLUTION prune 和 size cap（`evolution_maintenance_hook.py:674-961`）；MEMORY size valve 和 section cap（`distillation_hook.py:2382-2915`）；`locked_write` 每次写 MEMORY 时清理旧索引（`locked_write.py:570-613`）；DDD 矛盾剥离（`ddd_cultivation.py:978-1007`、`:1096-1125`）；session 结束 writeback | 修改阶段删除，只保留只读的统计和报告。确实需要的功能（size 控制、成熟度）改成只作用于 `cand:` 单元，或者改到读取侧做（§4.9） |
| 结构元数据 | `ddd_maturity.py:521-539` 刷新证据注释（只改段头的注释行） | 只能改段头注释，不能改单元；改动走适配器 |
| 退出 | 复审（§4.10） | 只作用于 `cand:` 单元 |
| 用户编辑 | 两个工作区编辑接口（`workspace_api.py:1759-1813`、`workspace.py:715-790`）、上传接口 | 走适配器的 CAS 整文件写，记为 `writer=user_edit`，不跑答题卡；同时触发一次基线修订（§6） |
| 日志和过程产出 | DailyActivity、checkpoint、JobResults、Signals 摘要、Reports、Pollinate 包 | 不是知识存储；召回规则写在契约里 |
| 初始化 | `ensure_directory` 复制模板、工作区骨架、`swarm_workspace_manager` 新建项目 | 不变；DDD 模板只保留一个来源（§4.2 卡 2） |

### 4.2 答题卡：卡 0 规则 → 卡 1 内容本身 → 卡 2 路由表 → 卡 3 和已有内容的关系

每次决定都记下契约、prompt、模型三个版本。每道题的结果是 `PASS` / `REJECT` / `DEFER` 之一，外加一个原因码。
- `REJECT`：判断过了，不该写，不重试。判官拿不准时也给 `REJECT`，原因码 `uncertain`。
- `DEFER`：没法判断，比如判官预算用完、LLM 出错、输出解析失败、检索不可用或过期、拿不到锁、版本冲突、项目有歧义。候选留在候选表里，稍后重试（§4.5）。

**顺序**：卡 0（不调 LLM）→ 卡 1（1 次 LLM）→ 卡 2（不调 LLM）→ 卡 3（检索到命中才调 LLM；替换时再加 1 次，用来生成有效单元）→ 必要时改投回卡 2，最多 1 次 → 提交。这个顺序由依赖关系决定：卡 2 要用卡 1 的字段，卡 3 的检索范围要用卡 2 的去向。"值得记吗"和"重复吗"要和已有内容比较才答得出来，所以放在卡 3。

#### 卡 0：规则（不调 LLM）

| 检查 | 不通过 |
|---|---|
| 来源信封（§4.4）里是"被污染"，并且候选是 rule | `REJECT tainted_rule`（被污染的规则一律不写，不会改投到别处） |
| `MemoryGuard` 扫描（附加检测，不是安全边界） | `REJECT unsafe` |
| 噪音规则：会话 id、scratch 路径、PID、提交哈希、"N 个测试通过"之类 | `REJECT noisy` |
| 长度：rule 超过 600 字；reference 超过契约上限 | `REJECT not_atomic` / `too_large` |
| 内容指纹和某个受控单元完全相同 | `REJECT already_exists` |
| 会话已被删除 | `REJECT session_gone` |
| 配额（§4.6） | `REJECT quota` |
| `candidate_id` 已存在，但内容哈希、来源信封或项目快照有任何一项不同 | `REJECT id_collision`，同时告警，审计里记下两边的哈希。完全一致时直接返回已有的状态 |

#### 卡 1：内容本身（1 次 LLM）

**输出是一个判别联合类型**，只有下面 7 种合法形状。判官的输出不属于其中任何一种，就判 `REJECT untyped`。

| 形状 | 字段 |
|---|---|
| `Reference` | `reference_kind` ∈ {learned, analysis, spec}；可选 `project`（取自项目快照） |
| `UserCross` | `type`；`user_kind` ∈ {preference, profile, environment} |
| `UserSingle` | `type`；`project`；`topic` ∈ {decision, product, architecture, lesson}（例如"在 X 仓库里用中文写 commit"属于 architecture） |
| `Agent` | `type`；`agent_kind` ∈ {correction, competence} |
| `ProjectSingle` | `type`；`project`；`topic` ∈ {decision, product, architecture, lesson} |
| `WorldSingle` | `type`；`project`；`topic`（同上）。领域知识，但目前只和这一个项目有关 |
| `WorldCrossGlobal` | `type`；**`global_relevance` 必须为 true**，含义是：不依赖具体查询，在多数会话里都会影响判断 |

- `type` 只能取 `VALID_TYPES` 里的值。`project` 只能从项目快照里选；有多个候选项目分不清时，`DEFER ambiguous_project`。
- 不存在 `ProjectCross`：项目的内容不进 MEMORY。不存在 `status` 这个 topic：进度和状态是 DailyActivity 的事。
- `WorldCross` 但 `global_relevance=false`：`REJECT not_global`。这种内容应该在 Library 里作为参考，或者等它和某个项目有关时再写。
- rule 类形状都要附带两样东西：`applicability`（一句话写清适用条件）和 `claims`（1 到 5 条原子主张）。

**质量题**：按 reference 或 rule 分支，任何一题答"否"就 `REJECT`。

| 维度 | rule | reference | 原因码 |
|---|---|---|---|
| 原子 | 只讲一件事（强相关的两件算一件） | 只讲一个主题 | `not_atomic` |
| 价值 | 下次遇到同类情况会改变做法，并且写明了触发条件和做法 | 以后会被检索到、被读，并且读懂不需要原会话 | `no_judgment_value` / `no_reference_value` |
| 自洽 | 不依赖"上面""刚才"这类指代 | 同左 | `not_self_contained` |
| 持久 | 会话结束后仍然成立，不是进度或状态 | 不是某次 review 或某次 run 的流水 | `not_durable` |
| 有依据 | 来自用户明说、实测、失败或评审，不是推测 | 有出处 | `speculative` |
| 内部一致 | 前后不矛盾 | 同左 | `self_contradictory` |
| 精炼 | 去掉复述、过程和寒暄 | 同左 | `not_concise` |

七类的判别句式（判官 prompt 和黄金用例共用）：原则 = 任何领域都成立的思考规则；纠错 = 我曾经这样做错 → 正确做法；决定 = 在几个选项里选了 X，理由是…；心智模型 = X 是这样运作的；经验 = 做 X 时这样做效果好；坑 = 现象 → 原因 → 怎么避开；流程 = 有先后顺序的步骤。

#### 卡 2：路由表（确定性，不调 LLM）

一种形状只对应一行，所以不存在一个候选同时命中两行的情况。

| 形状 | 去向：文件 § 段 |
|---|---|
| `Reference` | `learned` → `Knowledge/Learned/`；`analysis` → `Knowledge/Notes/`；`spec` → `Knowledge/Library/` |
| `UserCross` | `preference` → `STEERING.md`；`profile` → `USER.md`；`environment` → `TOOLS.md`。段取文件里已有的标题，取不到就用契约给的默认段 |
| `Agent` | `EVOLUTION.md`：`correction` → `## Corrections Captured`；`competence` → `## Competence Learned` |
| `UserSingle` / `ProjectSingle` / `WorldSingle` | 该项目的 DDD，按下面的 topic × type 矩阵定位 |
| `WorldCrossGlobal` | `MEMORY.md` § `MEMORY_TYPE_TO_SECTION[type]` |

**DDD 段矩阵**：段名取自 `backend/templates/ddd/*.md`。

| topic \ type | principle / model | decision | guideline / process | pitfall / correction |
|---|---|---|---|---|
| decision | `PROJECT.md` § Recent Decisions | `PROJECT.md` § Recent Decisions | `PROJECT.md` § Recent Decisions | `PROJECT.md` § Recent Decisions |
| product | `PRODUCT.md` § Vision（principle）/ § What Makes … Different（model） | `PRODUCT.md` § Strategic Priorities | `PRODUCT.md` § Success Criteria | `PRODUCT.md` § Non-Goals |
| architecture | `TECH.md` § Architecture（principle）/ § Key Subsystems（model） | `TECH.md` § Architecture | `TECH.md` § Conventions | `TECH.md` § Runtime Traps |
| lesson | `IMPROVEMENT.md` § What Worked | `IMPROVEMENT.md` § What Worked | `IMPROVEMENT.md` § What Worked | `IMPROVEMENT.md` § What Failed |

- 这张矩阵的唯一真实来源是契约，文档里的版本由契约生成。DDD 模板也只保留一个来源 `backend/templates/ddd/*.md`：`swarm_workspace_manager.py:379-467` 里内嵌的那份 `DDD_TEMPLATES` 改成从这些文件读取。测试检查矩阵里的每个段在模板里都存在。
- 写入前解析目标文件里实际存在的标题。找不到目标段就 `DEFER missing_section`，不会自己新建段。用户的项目如果删过段，这条候选会留在审计里，看得见。
- 层由 `layer_for(target_section, type)` 唯一决定（§4.3）。

#### 卡 3：和已有内容的关系

**检索语料**：一个专用的单元语料，范围是卡 2 选定的去向，加上每轮都会注入的 MEMORY、EVOLUTION、STEERING、USER、TOOLS、SOUL、AGENT；去向是 DDD 时，再加上该项目的另外三份文档。语料由**统一的单元解析器**生成（§4.3），用 `memory_index._bm25_scores` 打分。召回取三路结果的并集：内容指纹、标题或锚点、BM25 前 8 个。被取代的单元**不剔除**，它们的命中按 §4.3 转交给当前生效的那一条。

**检索结果带类型**：`OK_EMPTY` 直接新加，不调 LLM；`OK_HITS` 继续往下走；`UNAVAILABLE` 和 `STALE` 一律 `DEFER`。

**判官对每个命中输出**（不是只对最接近的一个）：

| 字段 | 取值 |
|---|---|
| `overlap` | `same` / `contained`（候选被旧单元包含）/ `contains`（候选包含旧单元）/ `related` / `unrelated` |
| `same_topic` | true / false：主题和适用条件都相同 |
| `claim_verdicts` | 候选的每条 claim 一个结论：`agrees` / `refines`（更细或更准）/ `refutes` / `independent`，并指出旧单元里对应的那一句 |
| `scope_relation` | `equal` / `narrower`（候选更窄）/ `wider` / `disjoint` |

**证据强弱**由来源信封决定（§4.4），是全序：`user_explicit` > `measured` > `review` > `run_lesson` > `inferred`。

**动作由两个纯函数算出**，判官只负责给出上面那张表：

`relation_for_hit(hit) →` 按顺序，命中第一条就停：
1. 有任何 `refutes`，并且 `scope_relation=narrower` → `exception`
2. 有任何 `refutes`，并且 `scope_relation ∈ {equal, wider}` → 候选证据更强，或者一样强但更新 → `replace`；否则 → `blocked`
3. 有任何 `refutes`，并且 `scope_relation=disjoint` → `independent`
4. `overlap ∈ {same, contained}`，并且没有 `refutes` → `duplicate`
5. `same_topic=true`，并且没有 `refutes`（包含 `refines`、`agrees`、`contains`）→ `refine`
6. 其余 → `independent`

`aggregate_hits(relations) →` 按顺序，命中第一条就停：
1. 任何一个命中是 `blocked` → `REJECT conflicts_stronger`，审计里把两条并排列出
2. 有 `replace` 或 `exception`，同时又有 `duplicate` → `REJECT incompatible_hits`（同一批里既重复又冲突，说明已有内容自相矛盾，这时不再往里写）
3. 有两个或以上 `replace` → `REJECT incompatible_hits`
4. 恰好一个 `replace` → 替换那一条
5. 有 `duplicate` → `REJECT already_exists`
6. 有 `exception` → 新加，作为例外，加 `extends` 边
7. 有一个或多个 `refine` → 选分数最高的那一条，分数相同时取锚点字典序最小的。它是 `cand:` 单元就补充它；是基线单元就新加一条，加 `extends`
8. 其余 → 新加
9. 命中明显说明候选属于别的去向 → 改投，回到卡 2，只允许一次（判官单独给出一个 `reroute_to` 字段，跟上面这些规则分开）

两个函数都要做全枚举的属性测试，包括命中顺序打乱后结果不变。

**替换**：
- 旧单元是 `cand:`：先归档再移除，写入新单元，加 `supersedes` 边。
- 旧单元是基线单元，分三步：
  1. **列出旧单元的全部主张**：第二次判官调用，为旧单元生成一份主张清单，每条带原文句子的位置。条数不设上限。
  2. **生成有效单元**：同一次调用里，对清单上每一条旧主张标明 `preserve` / `refute` / `refine`，然后写出新的有效单元。
  3. **校验**：每条 `preserve` 和 `refine` 的旧主张，在有效单元里都要能找到对应的句子；每条 `refute` 都要有候选里的一条 claim 支持。有效单元必须重新通过卡 1。任何一条旧主张没有对上，就 `REJECT conflict_unresolved`。
- 通过之后新加有效单元，加 `新 —supersedes→ 旧`。旧单元的文件不改。

**用户说"记住"**：同样过全部的卡，没有特权，只是证据强弱按来源信封走（§4.4）。被拒时，agent 当场把原因码告诉用户。

**已知局限**：没有向量库，换个说法、跨中英文的重复 BM25 可能找不到，由复审兜底。卡 3 端到端的重复召回率有门槛（§7）。

### 4.3 单元、层、关系，以及读取侧怎么用

- **统一的单元解析器** `parse_units(path) -> [Unit]`，每个 Unit 有这几个字段：`path`（工作区相对路径）、`section`、`byte_start`、`byte_end`、`identity`、`byte_hash`。
  - bullet 形式的单元（MEMORY、DDD）和 `###` 块形式的单元（EVOLUTION）都由它解析。
  - 基线生成、卡 3 语料、MEMORY 组装、DDD 条目召回、DDD 段语料重建、渲染，这六处都只用这一个解析器。现有的 `context_directory_loader.py:256-302` 和 `recall_multi.py:100-220` 改成调用它。
  - `byte_hash` 是原始字节的哈希，用于"已沉淀内容不变"的验收。
  - `identity` 用于查找：`cand:` 单元取它的 `cand:<id>`；基线单元取基线清单里记下的 `byte_hash`。在单元后面追加新单元，不会改变已有单元的字节，所以已有单元的 identity 保持稳定。
- **单元锚点**：`<path>#<section>#<identity>`。
- **层**：`layer_for(target_section, type)`，所有合法组合列成一张表，非法组合直接拒绝。审计、文件元数据、复审只读这个函数的结果。
- **关系图 schema v2**（`knowledge_graph.py`）：
  - 边的两端是 `subject_anchor` 和 `object_anchor`，另外有 `predicate`、`scope`、`attrs`、`created`、`expired`。
  - 去重身份是 `(subject_anchor, predicate, object_anchor, scope)`。YAML 读写带版本号。旧边读进来时，title 原样当 anchor 用，并标 `legacy=true`。
  - `batch_add_relations()` 改为接收带类型的输入。
  - 锁改用 `utils.file_lock`（现在的写法是模块顶层 `import fcntl`，Windows 上会失败）。
- **supersedes 的图约束**（写入时在锁里校验，不满足就 `REJECT incompatible_hits`）：
  - 不能有环。
  - 每个锚点最多有一个生效中的取代者。第二次想取代同一个旧单元时，必须改为取代当前的链头，也就是"新 → 当前头"。
  - 边的两端都必须是能解析出来的单元。
  - 只有提交协议（§4.5）能写入 supersedes 边。
- **链头**：`active_head(anchor)` 沿着生效中的 supersedes 边一直走到终点。复审要归档某个链头时，必须在同一个事务里把这条边设为失效，让它的前驱重新成为链头。
- **读取侧**（MEMORY 组装、DDD 条目召回、DDD 段打分、渲染）：
  - 检索命中某个被取代的单元时，**不丢掉这次命中**，把它的分数和命中理由转给 `active_head`。同一个链头被多次命中时，取最高分。
  - 被取代的单元本身不进入输出。
  - 这样处理之后，查询只匹配旧措辞时，模型看到的是当前有效的那条；任何情况下都不会同时看到新旧两种说法。
- **Library 文件**：frontmatter 写 `ontology_types:`，可以为空。
- **类型对照**：`classify_entry_type()` 只作为对照信号写进审计；它和判官给的类型不一致时单独计数，不阻断写入。

### 4.4 来源信封和污染：按输入事件记录

**信任从哪里来**：后端没法知道一段文字是用户亲手打的，还是粘贴进来的。所以 r12 不再声称能区分这两种情况。所有信任判断都基于"这段内容来自哪个输入事件"。

- **输入事件**：每次有内容进入会话，都记一条事件，带不可变的 `event_id` 和 `trust`。会话里所有事件 id 都记进会话的**输入账本**（`session_inputs` 表）。账本随会话持久化，压缩和恢复会话之后仍然保留，`HookContext` 和 run 记录都会带上它。

| 输入事件 | trust |
|---|---|
| 用户在 dashboard 或已绑定频道里**本人发出**的消息（已认证的发送者） | `user`。如果消息里有引用块、转发块、附件，或者有 fenced 代码块，这些部分单独记为 `external` 事件 |
| 白名单里的结构化测量器（pytest 的通过/失败计数、`/health` 字段、exit code），只取结构化字段 | `measured` |
| 其他所有工具或命令的输出、网页抓取、MCP 返回、读入的文件、他人发的频道消息 | `external`（工具和命令输出默认都算这一档，因为它们可能把外部文本原样打出来） |
| agent 自己产生的文本 | 不是输入事件，信任等级继承它所依据的输入 |

- **候选必须声明依据**：`submit` 要求写入者给出这条候选所依据的 `source_event_ids`，并且只能引用这个会话账本里已有的 id。
  - **证据**：所依据的事件全部是 `user`，证据为 `user_explicit`；全部是 `user` 或 `measured`，证据为 `measured`；其余情况，证据按写入者类型取 `review` / `run_lesson` / `inferred`。
  - **污染**：所依据的事件里只要有一个 `external`，就是 `tainted`。
- **写入者不声明依据时**，按最坏情况处理：会话账本里有任何 `external` 事件，就算 `tainted`。蒸馏、backflow、REFLECT 这类自动写入者，如果拿不出逐条依据，就走这条兜底规则。会话里先读过网页、后来用户又亲口纠正过的情况：只要纠正那条候选的依据只指向用户那条消息，它就不算被污染。
- **转述不能降级**：agent 转述、REFLECT 总结、review expert 的结论，都继承原始输入的污染。review expert 和 pipeline run 也各自维护输入账本。
- **规则**：被污染的 rule 一律 `REJECT tainted_rule`（卡 0）；被污染的 reference 只能写进 Library，frontmatter 标 `taint: external`。从被污染的 Library 文件里再提炼出来的内容，依据会指向那个文件，所以仍然是被污染的。
- **召回**：被污染的 Library 内容放在不可信数据边界里渲染。
- `MemoryGuard` 只是附加检测，不是安全边界。

### 4.5 候选表：一个状态机管重试、执行和审计

- **`ingestion_candidates` 和 `session_inputs`** 两张表都加进 `SQLiteDatabase.SCHEMA`，新库直接创建。另外加一个幂等的 v16 迁移，`CURRENT_SCHEMA_VERSION` 改成 16（`database/sqlite.py:2103`）。两张表都有专门的 repository API，测试覆盖 `initialize(skip_init=True)` 这条路径。

| 字段 | 内容 |
|---|---|
| `candidate_id`、`created_at`、`decided_at` | 幂等键和时间 |
| `writer`、`envelope` | 写入者；来源信封：`source_event_ids`、证据强度、是否被污染 |
| `content`、`content_hash`、`content_retained` | 全文、哈希、全文是否还在（§4.6） |
| `project_snapshot` | §4.8 的快照 |
| `card0`–`card3`、`ontology` | 每张卡的结构化答案；类型、层、关系 |
| `state` | `received → judged → prepared → committed`，或者结束于 `rejected` / `deferred` / `expired` |
| `targets` | 每个目标一项：存储、工作区相对路径、单元锚点、动作，以及 `original_hash` / `staged_hash` / `original_snapshot` / `staged_path` / `install_state` |
| `replaced` | 被替换或被取代的旧单元锚点，以及它的归档位置 |
| `versions` | 契约、prompt、模型的版本 |
| `lease_owner`、`lease_until` | 防止多个触发点同时处理同一行（§4.10） |
| `retry_count`、`next_attempt_at`、`error_class` | `deferred` 状态用 |

- **提交协议**：一次写入涉及语料文件、关系图、数据库三处，没有跨这三处的原子操作，所以先准备、再提交。
  1. **准备**：拿到所有目标文件和关系图的锁。在锁里读出当前内容，计算 `original_hash`，并在同一文件系统上保存一份**旧版本快照**。生成新版本，写入暂存文件，计算 `staged_hash`。快照和暂存文件都 fsync，所在目录也 fsync。然后在一个 SQLite 事务里把状态改成 `prepared`，同时写入每个目标的四项信息（`original_hash`、`staged_hash`、`original_snapshot`、`staged_path`）。
  2. **归档**（替换 `cand:` 单元时才有）：写入归档文件。归档带幂等 id，重复执行不会写出两份。
  3. **逐个安装**：对每个目标做一次原子替换，然后把它的 `install_state` 改成 `installed`。
  4. **提交**：状态改成 `committed`，再清理快照和暂存文件。
- **恢复**：启动时，以及每次 drain 之前，处理所有处于 `prepared` 的行。对每个目标读出当前文件的哈希，分三种情况：
  - 等于 `staged_hash`：已经装好了。
  - 等于 `original_hash`：还没装。
  - **两者都不等**：崩溃之后有别人写过这个文件。这时**不覆盖**，整行改成 `deferred`，`error_class=concurrent_change`，保留现场并发出通知。

  每个目标都是"已装好"或"还没装"时：暂存文件都在、校验和也对，就把剩下的装完；否则把"已装好"的目标用 `original_snapshot` 还原，前提是还原前再确认一次它的当前哈希仍等于 `staged_hash`。还原完把行改成 `deferred`。
- **崩溃点测试**：每个崩溃点的预期状态列成一张表，逐项写测试。另外两个场景必须覆盖：崩溃之后用户编辑了文件；崩溃之后另一个候选先提交了。
- **重试**：`deferred` 按指数退避重试，最多 5 次，之后改成 `expired`。`rejected` 不重试。
- 这两张表不进召回，也不进索引。

### 4.6 配额、熔断和预算

上线时就带硬上限，都写在契约里，可以配置。

| 上限 | 默认 |
|---|---|
| 每个写入者每小时提交的候选数 | 60 |
| 每个来源（同一会话或 run）的候选数 | 30 |
| 所有未结束状态（`received` / `judged` / `prepared` / `deferred`）合计 | 1000 行 / 20 MB |
| 候选表总大小 | 50 MB。超过后，最旧的已结束行只留哈希和决定，`content_retained=false`；每次清理后执行 `incremental_vacuum` |
| 每个每轮注入文件（MEMORY、EVOLUTION、STEERING、USER、TOOLS）的 `cand:` 字节数 | 每天新增 4 KB，总量 40 KB |
| 每个项目 DDD 每天新增 | 20 KB |
| Library 每天新增 | 50 个文件 / 5 MB |
| 每次提交新增的关系边 | 10 |

- 超出上限：`REJECT quota`，只记哈希和原因。
- **熔断只看系统故障**：同一个写入者在 1 小时内至少处理过 20 个候选，并且其中 `DEFER` 里的基础设施、解析、检索三类错误占比超过 50%，就暂停这个写入者 1 小时并发通知。内容被拒绝（`REJECT`）不计入熔断，宁缺毋滥是正常结果，只在审计里看。
- 计数、预算、熔断状态都存在数据库里，用原子计数器，跨进程共享，因为 `ingest submit` 的 CLI 和 backend 是两个进程。
- **判官预算**：一个全局计数器，分优先级通道：用户纠正 > 会话结束 > REFLECT > 复审。复审另有一份维护预算。预算用完时返回 `DEFER`。

### 4.7 追加式插入原语

提交时用适配器里的**字节级插入**：
- 先用统一解析器找到目标段，只在段末插入新单元的字节，其余字节原样保留。
- 找不到目标段时拒绝写入，不会新建段。
- 写入方式：在锁内写临时文件，fsync，再原子替换。
- 新单元的格式：bullet 形式写成 `- [type] … <!-- cand:<id> | source:<writer> -->`；`###` 块形式在标题下第一行写 `<!-- cand:<id> -->`。
- 语义写入不再直接调用 `locked_write` 或 `apply_to_ddd`。这两个函数里会改动已沉淀内容的分支（旧索引清理、矛盾剥离、新建段）随 §4.1 的退役一起删除。

### 4.8 Knowledge 写入者和项目快照

- **项目快照**：在 hook 入队之前（`lifecycle_manager.enqueue_hooks`），把会话碰过的仓库根目录、对应的绑定、是否有歧义，连同会话的输入账本 id 一起写进 `HookContext`。DailyActivity 的每条 JSONL 记录也带上同一份快照。延迟执行的扫描只读快照。`context_health_hook.py:961-969` 里写死 SwarmAI 的那段删掉。
- **backflow**：`candidate_id` 用消息 id，`source_event_ids` 指向那条 assistant 消息所依据的输入。backflow 拿不出依据时，按会话账本兜底（§4.4）。
- **checkpoint**：新的 checkpoint 写到不进索引的恢复文件 `<app data>/state/checkpoints/<session>.json`，会话正常结束时删除。已有的 checkpoint 块留在原处，读取侧不再召回。
- **已删除的会话**：DailyActivity 和 backflow 两个 hook 开头先查会话还在不在，不在就直接返回。
- **DailyActivity 块格式**：`upsert_session_block` 加断言。另外要查清 `## Session Resume` 那个独立块是谁写的。

### 4.9 读取侧：注入预算、使用信号、召回

- **每轮注入文件的确定性预算**（本次改变 AGENTS.md 里"读取侧永不截断"的规则）：
  - MEMORY、EVOLUTION 各有一个注入上限，默认 MEMORY 24K tokens、EVOLUTION 8K tokens，可配置。源文件一个字节都不改。
  - 文件超过上限时，按单元选取，规则固定：
    1. 先剔除被取代的单元。
    2. 元认知层整层保留。
    3. 认知层和操作层各自按本轮查询的 BM25 分数排序，再按单元在文件里的顺序排序，依次装入，装满为止。
    4. 输出时按单元原来的顺序拼接。
  - 没装进去的单元仍然能被检索到：recall 的 `context_files` 那一路（读取侧设计 `2026-10-26-recall-precision-and-coverage.md` D4：只检索 MEMORY 里不在 prompt 中的条目）照常覆盖它们。
  - STEERING、USER、TOOLS 由用户自己管，不设上限，只在超过 8K tokens 时在审计页面提示。
  - `over_budget` 警告保留，额外记录被省略的单元数，显示在审计页顶部。
  - 实施时同步改 AGENTS.md 的 Context and Memory System 一节和 `context_directory_loader` 的文档，L1 缓存的预算头也跟着改。
- **使用信号**：渲染完成后，把最终块里实际出现的单元锚点交给一次批量调用 `record_ddd_deliveries()`，一把锁、一次原子写入。删掉 `record_ddd_hit`。
- **DDD 注入条目正文**：一次检索直接返回条目正文、校验过的 section 和排名。被取代的命中转交给当前生效的那一条（§4.3）。
- **Library 多取后补足**：先取 24 条，依次丢掉只有 frontmatter 的、正文少于 200 字的、旧 checkpoint 块，再用 `RecallEngine.search()` 现有的哈希去重，最后补足到 8 条。被污染的内容放进不可信数据边界里渲染。标题在渲染时加到来源行上。
- **索引排除依赖目录**：`node_modules`、`.venv`、`dist`、`build`。
- 不重切 chunk，不重建索引。

### 4.10 复审和后台任务

- 复审只处理带 `cand:` 的单元和带 `ingestion_candidate` 的 Library 文件。每个动作走 §4.5 的提交协议，并且都是审计里的一行（`writer=review`）。读完整文件，分段处理，每次的条数和预算都有上限。被取代链的链头要归档时，按 §4.3 重新连接前驱。
- **drain**（恢复 `prepared`、重试 `deferred`、检测带外修改、7 天一次的复审）只有一个入口函数，三处会触发它：
  1. backend 启动时，四种模式都会。放在 lifespan 里数据库和工作区初始化之后。
  2. 会话空闲 120 秒后（`lifecycle_manager.py:968-1000`）。注册的是一个单独的轻量 hook，不复用整条 hook 链。
  3. daemon / hive 下的定时 job，只是为了更及时。
- 同一行候选在同一时间只能被一个触发点处理，靠候选行上的租约（`lease_owner`、`lease_until`，跨进程有效）。
- `ingest submit` 只负责入队，不在 CLI 进程里跑 drain。
- 复审改了某个正在运行的会话已经加载的文件时，那个会话下一次组装 prompt 才读到新内容，沿用 L1 缓存失效机制。

### 4.11 context 文件：自动写进用户的 `.context/`，不写源码仓库

| | 位置 | 归属 |
|---|---|---|
| 模板 | `backend/context/*.md`，即 `templates_dir`。dev 模式和 worktree 里它在源码仓库内，打包后在安装包内 | 产品 |
| 用户的 live 文件 | `<workspace>/.context/*.md` | 当前用户，prompt 读的是这一份 |

1. `ContextFileSpec` 新增 `auto_write_eligible`，只有 USER、STEERING、TOOLS 为真；不能从 `user_customized` 推出来。SWARMAI、IDENTITY、SOUL、AGENT 是产品文件，用户的规则一律写进 STEERING。
2. 候选里存工作区 id 和相对路径，执行时重新解析。绝对路径只作为审计里的快照。
3. 执行前在锁里校验：路径解析后必须正好在 live `.context/` 目录下，不在 `templates_dir` 下；工作区是 git 仓库时，目标文件必须是未跟踪或已忽略的；文件版本必须没变，变了就 `DEFER`。
4. 用户编辑走 `write_context_cas()`：加锁 → 在锁里比较版本 → 归档整份旧文件 → 安全扫描 → 按文件名做格式校验 → 原子替换 → 保持 0o644 权限 → 触发基线修订（§6）。系统写入走 §4.7 的插入原语，再经过同样的校验。
5. 受控模块没有写 `templates_dir` 的能力。

### 4.12 Ingestion 审计（产品功能）

- **数据**：读 `ingestion_candidates` 和 `session_inputs` 两张表，不新增存储。
- **接口**
  - `GET /api/ingestion/candidates`：可以按时间、写入者、存储、状态、原因码、关键词筛选；分页，每页最多 100 条；正文截断到 200 字。
  - `GET /api/ingestion/candidates/{id}`：返回完整的一行。全文已被清理时返回 `content_retained=false`。
  - 两个接口都只读。实现方式是同步 SQLite 只读查询，用 `executors.run_nav` 包起来，固定连当前实例自己的 `data.db`，查询全部参数化。调用方不能传路径或用户参数。
  - 沿用 dashboard 现有的本机认证，CORS 仍然只放行列出的来源。
  - 正文、来源、路径都按纯文本渲染；疑似密钥打码；访问记日志。
  - 后端要接的地方：新增 router，在 `main.py` 里 import 并 include；新增 service 或 query 模块。
- **overlay**
  - 前端要改的地方：`overlayIds.ts`、`navTable.ts`、`overlayChunks.ts`、`overlayRegistry.tsx`、`overlayQueries.ts`（预取）；`ThreeColumnLayout.tsx` 的 System 区，在 Eval 和 Settings 旁边加一张卡；加图标；加中英文 i18n；补相等性测试和 nav visual 测试。
  - 遵守 Nav visual system，用共享组件。
  - **列表**：时间、写入者、内容开头、去向、状态、原因码。`writer=unknown` 的行（带外修改，§4.1）高亮显示。
  - **详情**
    - 完整内容。
    - 来源信封：依据了哪些输入事件，每个事件的 trust，是否被污染。
    - 卡 0 到卡 3 每一题的答案和理由。卡 3 里每个命中的关系、聚合后的动作；替换的话，还有旧单元的主张清单和逐条映射。
    - 类型和层。
    - 写了哪些边。写入的单元可以点开；被替换或被取代的旧单元可以点开；归档位置。
  - **顶部统计**
    - 最近 7 天的计数，按状态、去向、原因码分组。
    - `deferred` 积压量。
    - 每天的 LLM 调用次数和 token 用量。
    - 判官和关键词分类器的结论不一致次数。
    - 熔断记录。
    - 带外修改次数。
    - MEMORY 和 EVOLUTION 的大小，以及每轮注入时被省略的单元数（§4.9）。
- **不提供审批按钮。** 发现判断有错，就改契约或 prompt，再把版本号加一。
- **通知**：以下情况发通知：判官或检索连续失败超过 1 小时；触发熔断；检测到带外修改；恢复时遇到 `concurrent_change`；`id_collision`。

### 4.13 对每个用户都可用，不依赖这台机器

- **路径**：只从 `config`、`get_workspace_path()`、app 数据目录取，不写死 `~/.swarm-ai`、仓库路径或项目名。候选里存的是工作区相对路径。项目归属只看用户自己的 DDD 和 `bindings.yaml`。
- **平台**：文件锁一律用 `utils.file_lock`，`knowledge_graph.py` 也一样。原子写和 fsync 用现有的跨平台工具。
- **运行模式**：daemon、subprocess、hive、dev 四种模式都能完成写入、重试、恢复、检测、复审和审计（§4.10）。
- **打包**：打包后 `templates_dir` 在安装包里，§4.11 的校验照样成立。不调用 `sys.executable -m`；`ingest` 走 `desktop_main.py` 的分发。
- **不依赖开发者自己的东西**：运行时不用 `Projects/SwarmAI` 里的 skill，不用回放或盲评脚本。
- **工作区不是 git 仓库**：跳过 §4.11 第 3 条里的 git 检查，只检查目录归属。
- **`s_memory-distill`**：改造成手动触发入口，调用 `governed_store` 的立即蒸馏和立即复审，再汇总这一次的审计结果。skill 本身不写阈值，也不写段名。

## 5. 每个事实在哪、谁写谁读

| 事实 | 唯一来源 | 写 | 读 |
|---|---|---|---|
| 准入问题、卡 1 形状、路由表、DDD 段矩阵、配额、注入预算、来源策略 | `store_contracts` | 人改 | `governed_store`、读取侧、复审 |
| DDD 模板 | `backend/templates/ddd/*.md` | 人改 | 新建项目、卡 2 校验 |
| 哪些 context 文件能自动写 | `ContextFileSpec.auto_write_eligible` | 人改 | `governed_store` |
| 单元是什么 | `parse_units()` | — | 所有读写方 |
| 基线 | `<app data>/state/baseline.json` | 上线时生成，用户编辑时修订 | 适配器、检测、复审、验收 |
| 输入事件和它的 trust | `session_inputs` | 消息入口、工具结果入口 | `submit`、审计 |
| 候选的状态、卡、去向、理由、暂存 | `ingestion_candidates` | `submit`、drain、复审 | drain、审计接口 |
| 会话属于哪个项目 | `HookContext` 快照（DailyActivity 记录里有同一份） | 入队前 | 卡 1、卡 2 |
| 类型和层 | 单元上的类型标签；`layer_for()` | 卡 1、卡 2 | 读取侧、复审 |
| 关系和链头 | `.knowledge-graph.yaml` v2；`active_head()` | 提交协议 | 读取侧、审计 |
| 单元有没有送达过模型 | `.ddd-usage.json` | 注入后批量写 | 复审 |

**热路径**：答题卡、候选表、复审、检测都在后台跑。要在发送路径上实测的有：

- `session_inputs` 写入，每条消息和每个工具结果各写一行；
- 注入预算的选取，在 MEMORY 超过上限时才发生；
- 链头转发，`active_head` 在加载关系图时缓存一次；
- Library 多一次 FTS 查询；
- DDD 少一次重复加载；
- 使用信号合并成一次批量写。

要求和现在持平或更快。`session_inputs` 写入要放在 chat 池之外的 executor 上。

## 6. 已经沉淀的内容：系统不动，用户可以改

- **基线**：上线后 backend 第一次启动时，对每个受控文件跑一遍 `parse_units()`，把每个单元的锚点和 `byte_hash` 记进 `baseline.json`。这一步只读不写。
- **系统不动**：基线单元不会被任何系统路径修改、移动、删除或打标记。这包括新功能、复审、所有维护机制（§4.1 的退役清单），以及插入原语，后者只往段末追加字节。
- **用户可以改**：用户通过编辑器或上传修改受控文件时，适配器在同一个事务里做三件事：
  1. 对修改后的文件重跑 `parse_units()`；
  2. 更新 `baseline.json` 里这个文件的单元表。被删的单元移出基线；被改过的单元换成新的 `byte_hash`；新出现、但没有 `cand:` 的单元也算作基线，因为它们是用户亲手写的；
  3. 关系图里指向已消失单元的边设为失效。

  这次修订在审计里记一行 `writer=user_edit`，附 diff。
- **带外修改**：没经过适配器的改动由 drain 检测出来，记作 `writer=unknown`（§4.1）。基线**不会**自动修订，等用户在编辑器里保存一次（就算一次确认），或者下次启动时再修订。
- **新旧共存**：卡 3 检索到的基线单元只用于判断。补充时新加一条，加 `extends` 边；替换时新加一个完整的有效单元，加 `supersedes` 边，读取侧把命中转发给链头（§4.2、§4.3）。
- **不在不变范围内的**：DailyActivity、会话、Reports、Signals、checkpoint、派生索引。它们是持续追加或会被 upsert 的日志和派生数据，不属于基线。验收时只检查切换前已有的记录仍然存在、内容不变，粒度到记录或块。
- **预算**：MEMORY 不再按大小驱逐基线单元，文件会变大，但每轮注入有确定性的上限（§4.9），所以 prompt 大小有界。`cand:` 单元的增长另有配额（§4.6）。

## 7. 实施和验收

每一步一个 worktree，ff-merge 一个 commit，不 push，不 rebuild。

**第 1 步：读取侧和索引**（不改语料，也不涉及注入预算）
1. `store_contracts` 加来源策略，排除依赖目录。
2. Library：多取、过滤、补足名额；渲染时加标题；旧 checkpoint 块不召回。
3. DDD：一次检索拿到条目正文；校验 section；使用信号批量写。
- **验收：成对回放**
  - 用固定清单（第 101–200 个会话，清单哈希写进文档）。每条查询同时生成基线版和新版的注入块，盲化、随机左右顺序后，由判官成对打分。判官的评分标准、模型、温度固定。
  - 通过条件：
    - 噪音比例下降的 95% 置信区间下界 ≥ 1 个百分点；
    - "有用"和"需要且拿到"两项，下降幅度的 95% 置信区间上界 ≤ 1 个百分点；
    - 注入总字符数 ≤ 基线；
    - recall p90 ≤ 基线。

**第 2 步：受控写入、答题卡、候选表、审计**（一次切换）
4. 契约、`parse_units()`、基线生成、v16 迁移（两张表）、输入账本的入口（消息、工具结果）、来源信封、配额和熔断。
5. `governed_store`：适配器、插入原语、`submit` 和四张卡（两个纯函数）、提交协议和恢复、drain 和三个触发点、租约、带外检测。
6. 关系图 v2、图约束、`active_head` 接入四个读取点、`knowledge_graph` 改用 `utils.file_lock`。
7. 注入预算（§4.9），并同步更新 AGENTS.md。
8. 语义写入者全部切过来；退役清单里的写入者删除或缩到 `cand:` 范围；`governed_corpus_gate` 登记进 `hook_builder.py`，并同步 AGENTS.md；静态测试。
9. 项目快照、backflow、checkpoint、已删除会话、DailyActivity 断言；DDD 模板只保留一个来源。
10. `ingest submit` 命令；审计接口和 overlay；改造 `s_memory-distill`。

**验收**

- **夹具**：放在 `backend/tests/fixtures/ingestion/`，合成数据，可以提交进仓库，没有私人内容。
  - 按卡 1 的 7 种形状分层生成。
  - 分成 train、dev、test 三份：test 冻结，调 prompt 只用 train 和 dev。
  - 每层的样本量事先按目标置信区间算好：test 至少 400 条，冲突对至少 120 对，换说法的重复至少 100 对。中英文各占一半。
  - 私人的真实样本只在本地补充使用，不是 CI 必需的。
- **门槛**：用 test 集 95% 置信区间的下界判定，事先写定。
  - 写入精确率 ≥ 93%；
  - 卡 1 对"该写"的召回率 ≥ 75%；
  - 写进 MEMORY 的内容里，非全局的比例，其上界 ≤ 5%；
  - 卡 3 换说法重复的端到端检出率 ≥ 60%；
  - 冲突对的动作正确率 ≥ 85%；
  - 形状正确率 ≥ 85%；
  - 层正确率 ≥ 95%。

  结果按写入者、语言、目标存储分别报告。
- **纯函数**：`relation_for_hit` 和 `aggregate_hits` 对全部输入做穷举的属性测试，打乱命中顺序结果不变。路由表的 7 种形状各有且只有一行；矩阵里每个段在模板里都存在。
- **supersedes**：
  - 查询只匹配旧措辞时，输出里是当前有效的那一条，并且包含所需的 claim。不能只检查"旧的没出现"。
  - 同一个旧单元被取代两次，链头唯一。
  - 链头被归档后，前驱恢复成链头。
  - 有环或有悬空边时拒绝。
- **有效单元**：覆盖以下夹具：限定条件漂移、否定词、数值边界、多段单元、超过 5 条主张。只要有一条旧主张没被映射，就 `REJECT`。
- **模型行为**：从真实的检索入口开始，固定模型和温度，每个用例重复 10 次。用"有效单元"作答，按新说法行动的比例 ≥ 90%。同时量化对照组"同时注入新旧两条"的结果。
- **安全**（四种模式都要做端到端）：
  - 读外部内容 → 改写 → REFLECT 或 review → 提交：rule 被拒，reference 带污染标记写进 Library。
  - 被污染的 Library 再被提炼：仍然被拒。
  - 命令输出里回显攻击文本：算 `external`。
  - 用户本人发的消息，附带引用块：引用部分算 `external`。
  - 注入攻击样本覆盖中文、改写、分多步诱导三类。
  - 恶意内容在审计页面渲染时不会被执行。
  - 跨工作区读不到别人的候选。
- **配额和熔断**：跑 1 小时 soak，三种写入者：恶意换 `candidate_id` 的、持续出故障的、拒绝率高但本身健康的。期望：候选表和每个存储的增长不超过上限；故障写入者被熔断；健康写入者不被熔断。
- **提交协议**：每个崩溃点都杀一次进程，恢复后的状态和预期表一致。还要覆盖：崩溃后用户编辑同一文件、崩溃后另一个候选提交，这两种情况都不能覆盖掉别人的写入。另外测两个写入者并发、复审和写入并发。
- **写入口**：
  - agent 用 Write、Edit、MultiEdit、NotebookEdit、Bash 直接写受控文件，都被 gate 拒绝，并提示用 submit 命令。
  - 用 `python -c`、`perl -pi`、带变量的路径绕过 gate 写入：下一次 drain 时记为 `writer=unknown` 并发通知。
  - 静态测试：故意新增一处直接写入，测试变红。
  - 临时工作区里的 `.context` 夹具不受影响。
- **已沉淀内容**：
  - 每个基线单元的 `byte_hash` 在上线前后一致。
  - 每个受控文件里，基线单元按原来的顺序构成新文件的一个子序列。
  - 用户编辑之后基线修订正确，被删单元的边失效。
  - 切换前已有的日志类记录仍然存在，内容不变。
- **注入预算**：MEMORY 超过上限时，元认知层完整保留；选取结果是确定的，同样的输入得到同样的输出；被省略的单元能被 recall 的 `context_files` 路检索到；prompt 大小不超过上限。
- **全平台矩阵**：{daemon, subprocess, hive, dev} × {macOS, Windows, Linux} × {源码, 打包} × {git 工作区, 非 git 工作区, 迁移过的工作区}。每个组合都要跑通写入、重试、恢复、检测、复审、审计。CI 跑能自动化的部分（backend 和 backend-windows 两个 job），其余列进发布检查表。
- **mutation 检查**：每条规则都做一次，撤掉修复后对应的测试必须变红。
- **现有测试**：`test_section_caps.py`、`test_memory_promotion.py`、`test_context_health_hook_memory_lifecycle.py`、`test_evolution_maintenance_hook.py`、`test_context_budget.py` 按新的不变量重写，不变量是"基线不动、`cand:` 有配额、注入有上限"，不再是"总能降到低水位"。

**第 3 步：复审**
11. 复审只处理 `cand:` 单元，由三个触发点驱动，链头归档时重新连接前驱。
- 验收：在合成 test 集上，结论和标注一致率的置信区间下界 ≥ 75%；只含基线的夹具跑完后字节完全不变；每个动作都出现在审计里。

上线后第一周，每天看一次审计统计。某一类判断持续出错，就改契约或 prompt，再把版本号加一。

## 8. 不做

- **不做人工审批或提案。** 答题卡就是决定，人看审计。cultivation 的提案审批状态机删除，它的三路输入改成产出普通候选。
- **不做 OS 层的写入隔离**（受控文件对 agent 子进程只读、写入走 RPC）。只做拦截加检测（§4.1）。
- **不做候选收件箱，也不做"提升"流程。**
- **不做批量的语义合并。**
- **不引入向量库。**
- **不做影子模式、切换日，也不让新旧写入路径并存。**
- **系统不整理已经沉淀的内容。**
- **不整类排除** Pollinate 或 `Knowledge/Archives/`，只排除依赖目录。
- **不改 DailyActivity 的 LLM 提炼门槛。**
- **不重切 chunk，也不重建索引。**

## 9. 已确认

1. 全自动：答题卡就是决定，人看审计（r6）。
2. 已经沉淀的内容系统不动，新内容走新流程（r7）。用户自己编辑算一次基线修订（r12）。
3. 每轮注入的文件有确定性的上限，源文件不变。这一条改变了 AGENTS.md 里"读取侧永不截断"的规定（r12）。
4. 写入口做拦截加检测，不做 OS 层隔离（r12）。
5. 先实施第 1 步。

## 10. 修订记录（非规范，仅供追溯，不按这里的旧说法实现）

- r1：只覆盖 Knowledge，含重切 chunk 的 S3（已撤回）。
- r2：Memory、DDD、Knowledge 三块统一成一份存储契约。
- r3：引入三张答题卡（质量、去向、准入）。
- r4：规定 context 写入的位置（用户的 `.context/`，不写源码仓库）。
- r5：吸收两轮独立评审（代码事实 3 BLOCKER + 9 MAJOR，设计 5 BLOCKER + 8 MAJOR）。当时引入的人工批准、影子模式、WAL 文件，在 r6 中已删除。
- r6：Gawan 决定改为全自动，不设切换期，审计做成产品功能，对每个用户都可用。
- r7：已经沉淀的内容一律不动。
- r8：ontology 标签（七类 + 关系）并入流程。
- r9：写清每张卡的维度，加卡 0，卡 2 改为路由表。
- r10：部分冲突只做标注，三处直接删除改为先归档。**这两条在 r11 都已作废。**
- **r11：吸收第三轮独立评审**（代码可行性 5 BLOCKER + 4 MAJOR + 1 MINOR，设计 4 BLOCKER + 9 MAJOR + 1 MINOR）：
  - 唯一写入口改为三层强制：适配器、`governed_corpus_gate`、静态测试；Memory Health 的修改阶段并入退役清单。
  - 卡 2 改为先看 `form` 再分支；补齐 `user_kind`、`agent_kind`、`project_topic`、`reference_kind`；EVOLUTION 用真实段名；目标段写入前校验存在。
  - 卡 1 按 rule / reference 分支提问，新增 `not_global`。
  - 层只由 `layer_for()` 一处决定。
  - 卡 3 改为逐个命中、逐条主张判定；动作由规则算出；新增"窄例外"；部分冲突改为"完整有效单元 + 确定性跳过"，取代 r10 的标注方案。
  - 关系图 schema v2（带 `scope`），`superseded_anchors` 接入三个读取点，改用 `utils.file_lock`。
  - 来源信封由受信入口生成，污染随派生传递；被污染的内容永远进不了每轮注入的文件。
  - 候选表成为唯一的状态机，采用"准备 → 提交"协议并支持恢复；SQLite 迁移 v16。
  - 上线即带硬配额和熔断。
  - 基线清单；会改动已沉淀内容的维护路径退役；追加式插入原语；验收改为按单元哈希 + 子序列核对。
  - 触发时机改为"启动 + idle + 定时"三处，状态只有一份。
  - 候选里存工作区相对路径。
  - 审计接口补上隔离、转义、打码、分页上限。
  - 验收改用可提交进仓库的合成夹具；门槛改为以写入精确率为主；增加模型行为测试和全平台矩阵。

- **r12：吸收第三轮独立评审**（代码可行性 6 个 BLOCKER + 7 个 MAJOR，设计 5 个 BLOCKER + 4 个 MAJOR），并按 Gawan 的三条决定修改：
  - 写入口改成"拦截 + 检测"，带外修改记为 `writer=unknown`；`ingest submit` 改从 stdin 读入；写入者清单改为从调用点扫描生成，补上 ContextHealth、两处成熟度写入和 `annotate_entry`。
  - 卡 1 改成 7 种形状的判别联合，去掉 `status`；卡 2 每种形状对应一行，补上 DDD 段矩阵；DDD 模板只保留一个来源。
  - 卡 3 改为逐个命中输出，动作由 `relation_for_hit` 和 `aggregate_hits` 两个纯函数计算，证据强弱是全序；替换前先列出旧单元的完整主张清单，逐条映射。
  - 统一的单元解析器；关系图 v2 的两端都用锚点；supersedes 的图约束；读取侧不再先剔除旧单元，而是把命中转发给链头。
  - 来源改为基于输入事件和输入账本；工具输出默认 `external`；不再声称能区分"亲手打的"和"粘贴的"；被污染的 rule 一律拒绝。
  - 提交协议先存旧版本快照，恢复时做三态 CAS，不覆盖他人的写入；新增租约和 `id_collision`。
  - 熔断只看系统故障，并设最小样本量；所有未结束状态和每轮注入文件都有硬上限；计数器跨进程共享。
  - 每轮注入文件改为确定性的预算选取（修改 AGENTS.md 里"永不截断"的规定）；用户编辑算基线修订；日志类文件不纳入字节不变的检查范围。
  - 验收改成同一查询成对回放；夹具分层，分 train/dev/test，并事先估算样本量；门槛取置信区间下界；模型行为测试从真实检索入口开始；对需要按新不变量重写的现有测试列出清单。

## 11. 验收结果

（实施后填写。）
