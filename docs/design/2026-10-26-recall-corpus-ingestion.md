---
title: Recall 语料的写入与准入 — Memory、DDD、Knowledge 三块统一的准入、身份、使用信号与退出
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r11 — 定稿，已过三轮独立评审（修订记录见 §10）
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
| MEMORY | 是（每轮整份注入） | 跨项目、不依赖查询、多数会话都会影响判断 | 新加 / 补充 / 替换 | 复审（§4.10） |
| EVOLUTION | 是（每轮整份注入） | 关于 agent 自己的能力或纠正，不针对某个项目 | 新加 / 补充 / 替换 | 复审 |
| 项目 DDD（PRODUCT / TECH / IMPROVEMENT / PROJECT） | 是（按项目按需注入） | 离开这次会话或 run 以后，还会影响这个项目的判断 | 新加 / 补充 / 替换 | 复审 |
| Knowledge Library（Notes / Library / Learned） | 是（召回时排名） | 离开原会话也能独立读懂、可以复用的参考材料 | 新加（一个文件） | 复审 |
| 用户 context 文件（USER / STEERING / TOOLS） | 是（每轮整份注入） | 改变的是这个用户"怎么判断" | 新加 / 补充 / 替换，写到用户的 `.context/`（§4.11） | 复审 |
| DailyActivity | 是（日志，按日期规则召回） | 不过答题卡，它是日志 | 按会话 id 覆盖写自己的块 | 不退出 |
| 候选表 `ingestion_candidates` | **否**（状态和审计） | — | 状态机迁移（§4.5） | 保留 180 天 |

**身份**：r4 把这几样混在了一起，r11 分开定义。

| 身份 | 用途 | 定义 |
|---|---|---|
| `candidate_id` | 同一个候选重放时不重复处理（幂等） | `writer:来源 id:序号` 的哈希，例如 backflow 用消息 id |
| 来源信封 | 出处、证据强度、是否被污染（§4.4） | 由受信入口根据原始事件生成，候选自己不能声明 |
| 内容指纹 | 完全相同的内容去重 | 规范化后正文的哈希（复用 `content_signature`） |
| 单元锚点 | 卡 3 的比较对象、关系图的端点、注入时跳过哪条 | `工作区相对路径 # 段标题 # 单元身份`。单元身份对条目用 `cand:<id>`；已沉淀内容用基线清单里的单元哈希（§6）。光靠标题不够唯一 |
| 基线 | 哪些内容属于"已沉淀" | 上线时一次性生成的清单（§6） |

## 4. 设计

### 4.1 唯一写入口，靠三层强制

r10 只靠"把底层写函数改成私有，再加一个静态扫描测试"。这挡不住 SDK 工具调用、shell 重定向、HTTP 写入、别名导入和动态路径。r11 改成三层强制：

1. **运行时写入适配器**：受控路径（MEMORY、EVOLUTION、USER、STEERING、TOOLS、各项目 `2-understanding/` 下的四份 DDD、`Knowledge/{Notes,Library,Learned}`、`.knowledge-graph.yaml`）只有 `governed_store` 里的适配器能写。适配器负责加锁、版本校验、原子替换和记审计。backend 里其他所有写这些路径的代码都改成调用适配器。
2. **PreToolUse 门** `governed_corpus_gate`（Write / Edit / MultiEdit / Bash）：agent 直接写受控路径时拒绝，并提示改用 `"$SWARM_BACKEND_BIN" ingest submit --text … --why …`。这条命令把内容作为候选交给 `submit()`。判断 Bash 是否写入时，复用 `governance_file_gate` 现有的写入检测（sed -i、重定向、解释器 heredoc）。这个门符合 AGENTS.md 对产品级 gate 的要求：每个用户都受益（记忆不被绕过），正常工作仍然有路可走（submit 命令）。它不碰代码、测试和提交。实施时要把它登记进 `hook_builder.py`，并更新 AGENTS.md 的 Security Architecture 列表。
3. **静态架构测试**：作为补充防线，扫描 `backend/` 中写受控路径或导入适配器私有函数的代码，必须在白名单里并标明类别。另外加运行时绕过测试，覆盖直接 `Path.write_text`、rename、subprocess、symlink 和路径穿越。

用户自己在编辑器里改受控文件，走适配器的整文件 CAS 写入，记为 `writer=user_edit`，不跑答题卡。上传文件到受控目录同样处理。

**写入者分类**：

| 类别 | 写入者 | 规则 |
|---|---|---|
| 语义写入（必须走 `submit`） | REFLECT 的新加 / 强化 / 细化（`reflect_job.py:157-281`）、REFLECT 写 pipeline-lessons（`reflect_library.py:145-228`）、会话结束蒸馏、即时纠正、`memory_extractor.py:351`、`evolution_optimizer.py:899`、DailyActivity 决策和纠正 → DDD、backflow、cultivation 的候选生成（signal、learn、code-change 三路）、agent 调用的 skill（`s_persist`、`s_self-evolution`、`s_ddd-persist`，以及改造后的 `s_memory-distill`） | 全部答题卡 |
| 结构刷新和元数据 | `ddd_orchestrator.py:566-590` 的机械替换、`ddd_maturity.py:521-539` 的成熟度注释 | 通过适配器的结构写函数；只能动带 `cand:` 的单元和文件头注释，不能动基线单元（§6） |
| 退出 | 复审（§4.10） | 只动带 `cand:` 的单元 |
| 退役 | 每周 Memory Health 的修改阶段（`memory_health.py:500-1057`：删 MEMORY 条目、移动 Open Threads、标记 superseded、删除和压缩 EVOLUTION）、EVOLUTION prune（`evolution_maintenance_hook.py:921-954`）、DDD 矛盾剥离（`ddd_cultivation.py:978-1007`、`:1096-1125`）、size valve 和 section cap 对已沉淀条目的驱逐（`distillation_hook.py:2470-2915`）、`locked_write` 每次写入时删除旧 Memory Index（`locked_write.py:570-608`）、session 结束 writeback（置信度 0.5 永远过不了 0.7） | 这些都会修改已沉淀内容，和 §6 冲突。改动方式：修改阶段删掉，只保留只读的统计和报告；size valve 改成只能驱逐带 `cand:` 的单元，进入复审 |
| 日志和过程产出 | DailyActivity、checkpoint、JobResults（`jobs/executor.py:2007-2056`）、Signals 摘要、Reports、Pollinate 包 | 不是知识存储，在契约里写明召回规则 |
| 初始化 | `ensure_directory` 复制模板、工作区骨架 | 不变 |

### 4.2 答题卡：卡 0 规则 → 卡 1 内容本身 → 卡 2 路由表 → 卡 3 和已有内容的关系

**输入和输出都有固定结构**，每次决定都记下契约版本、prompt 版本和模型 id。每道题的结果是 `PASS`、`REJECT`、`DEFER` 之一，外加一个原因码。

- `REJECT`：判断过了，不该写，不重试。判官拿不准时也给 `REJECT`，原因码 `uncertain`。
- `DEFER`：没法判断，比如判官预算用完、LLM 出错、输出解析失败、检索不可用或过期、拿不到锁、版本冲突、项目有歧义。候选留在候选表里，稍后重试（§4.5）。

#### 顺序

卡 0 规则（不调 LLM）→ 卡 1 内容本身（1 次 LLM）→ 卡 2 路由表（不调 LLM）→ 卡 3 和已有内容的关系（只有检索到命中时才调 1 次 LLM）→ 必要时改投回卡 2，最多 1 次 → 执行。

- 卡 2 依赖卡 1 给出的分类字段，卡 3 的检索范围依赖卡 2 选定的去向，所以这个顺序由依赖关系决定。
- "值得记吗"和"重复吗"是相对已有内容才能回答的问题，所以放在卡 3。
- 不采用"先检索再定去向"：现有内容里 57% 放错了地方，照着它们找去向，只会接着放错。

#### 卡 0：规则（不调 LLM）

| 检查 | 不通过时 |
|---|---|
| 来源信封（§4.4）：标记为被污染，且去向可能是 MEMORY、EVOLUTION、context 文件 | 只允许进 Library；其他去向在卡 2 给 `REJECT tainted_source` |
| `MemoryGuard` 扫描（附加检测，不是安全边界） | `REJECT unsafe` |
| 噪音规则：会话 id、scratch 路径、PID、提交哈希、"N 个测试通过" | `REJECT noisy` |
| 长度：rule 类候选超过 600 字；reference 类超过契约上限 | `REJECT not_atomic` / `too_large` |
| 内容指纹和任何受控单元完全相同 | `REJECT already_exists` |
| 会话已经删除 | `REJECT session_gone` |
| 配额（§4.6） | `REJECT quota` |

#### 卡 1：内容本身（1 次 LLM）

**分类字段**：都从固定枚举里选，选不出来就 `REJECT untyped`。

| 字段 | 取值 |
|---|---|
| `form` | `rule`（会改变做法）· `reference`（供查阅的材料） |
| `type`（只有 rule 才有） | `principle` · `correction` · `decision` · `model` · `guideline` · `pitfall` · `process`，取自 `VALID_TYPES` |
| `subject`（只有 rule 才有） | `user`：和这个用户怎么协作 · `agent`：agent 自身 · `project`：某个项目 · `world`：领域知识 |
| `user_kind`（subject=user） | `preference`（偏好和做事方式）· `profile`（用户本人的事实）· `environment`（用户的工具和环境） |
| `agent_kind`（subject=agent） | `correction`（自己犯过的错）· `competence`（学会的能力） |
| `scope` | `single_project:<名字>`，名字只能取自项目快照 · `cross_project`。分不清是哪个项目时 `DEFER ambiguous_project` |
| `project_topic`（scope=single_project） | `decision` · `product`（定位、目标、非目标、用户）· `architecture`（架构、机制、约定）· `lesson`（经验、坑、做对和做错的事）· `status`（当前进展） |
| `reference_kind`（form=reference） | `learned`（外部内容的学习卡）· `analysis`（研究和分析）· `spec`（规格和参考） |
| `applicability`（rule） | 一句话写明适用条件，卡 3 判断冲突范围时用 |
| `claims` | 把内容拆成 1–5 个原子主张，卡 3 逐条比较时用 |

**质量题**：按 `form` 分支，任何一题答"否"就 `REJECT`。

| 维度 | rule | reference | 原因码 |
|---|---|---|---|
| 原子 | 只讲一件事（强相关的两件算一件） | 只讲一个主题 | `not_atomic` |
| 价值 | 下次遇到同样的情况会改变做法，并且写清了触发条件和做法 | 以后会有人检索到并读它，读懂不需要原会话 | `no_judgment_value` / `no_reference_value` |
| 自洽 | 不依赖"上面""刚才"这类指代 | 同左 | `not_self_contained` |
| 持久 | 会话结束后仍然成立，不是进度或状态 | 不是某次 review 或某个 run 的流水 | `not_durable` |
| 有依据 | 来自用户明说、实测、失败、评审，而不是推测 | 有出处 | `speculative` |
| 内部一致 | 前后不矛盾 | 同左 | `self_contradictory` |
| 精炼 | 去掉复述、过程和寒暄 | 同左 | `not_concise` |
| 全局相关（只在 scope=cross_project 时问） | **不依赖具体查询，大多数会话里都会影响判断**。只在某类任务里有用的，不算 | — | `not_global` |

最后一题直接对应 MEMORY 的准入问题。只要 `cross_project` 就能进每轮都注入的 MEMORY，这条路堵住了：`not_global` 的规则不会进 MEMORY；如果它和某个项目有关，可以重新按项目路由。

七类的判别句式，判官 prompt 和黄金用例共用这一份：

| 类型 | 判别句式 |
|---|---|
| 原则 | 任何领域都成立的思考规则 |
| 纠错 | 我曾经这样做错了 → 正确的做法 |
| 决定 | 在几个方案里选了 X，理由是… |
| 心智模型 | X 是这样运作的（机制） |
| 经验 | 做 X 时这样做效果好 |
| 坑 | 现象 → 原因 → 怎么避开 |
| 流程 | 有先后顺序的步骤 |

#### 卡 2：路由表（确定性，不调 LLM）

先看 `form`，再看其他字段。每个合法的字段组合**恰好命中一行**，用属性测试穷举验证。

| # | 条件 | 去向：文件 § 段（真实标题） |
|---|---|---|
| R1 | form=reference | Library：`learned` → `Knowledge/Learned/`；`analysis` → `Knowledge/Notes/`；`spec` → `Knowledge/Library/` |
| R2 | rule，subject=user | `preference` → `STEERING.md`；`profile` → `USER.md`；`environment` → `TOOLS.md`。段名取自文件里已有的标题；没有合适的段，就追加到契约指定的默认段 |
| R3 | rule，subject=agent | `EVOLUTION.md`：`correction` → `## Corrections Captured`；`competence` → `## Competence Learned` |
| R4 | rule，subject ∈ {project, world}，scope=single_project | 项目 DDD：`decision` → `PROJECT.md` § Recent Decisions；`status` → `PROJECT.md` § Current Focus；`product` → `PRODUCT.md`；`architecture` → `TECH.md`；`lesson` → `IMPROVEMENT.md`。具体段由 `type` 加模板标题表（`backend/templates/ddd/*.md`）决定，契约里写一张完整的表 |
| R5 | rule，scope=cross_project，并且通过了 `not_global` 那一题 | `MEMORY.md`，段由 `MEMORY_TYPE_TO_SECTION[type]` 给出（`ddd_entry_lifecycle.py:133-141`） |
| R6 | rule，scope=cross_project，`not_global` 那一题没通过 | 不写，`REJECT not_global`（它在卡 1 就已经被拒了，这一行是为了让路由表把所有组合都覆盖到） |

- `subject=user` 但 `scope=single_project`（例如"在 SwarmAI 仓库里用中文写 commit"）：走 R4，写进项目 DDD，归为 `project_topic=architecture`（约定），不进 STEERING。STEERING 只放跨项目的用户偏好。
- 写入前校验目标段是否真的存在：解析目标文件的实时标题，找不到就 `DEFER missing_section`，不会自己新建段。
- SOUL、AGENT、IDENTITY、SWARMAI 是产品文件，不会是去向（§4.11）。
- 层由一个函数唯一决定：`layer = layer_for(target_section, type)`，列出全部合法组合，组合不合法就拒绝。审计、文件元数据和复审都只读这个函数的结果（§4.3）。

#### 卡 3：和已有内容的关系（先检索，有命中才调 LLM）

**检索语料**：卡 3 有自己专用的语料，不调用读取侧的注入函数。读取侧的 `context_files` 那一路只读 MEMORY，还会排除已经在 prompt 里的内容，正好和这里的需求相反。语料范围是：卡 2 选定的去向，加上每轮都会注入的 MEMORY、EVOLUTION、STEERING、USER、TOOLS、SOUL、AGENT；去向是 DDD 时，再加上这个项目的另外三份文档。按单元锚点（§3）把这些文件解析成可比较的单元，用 `memory_index._bm25_scores` 统一打分。检索结果是三路的并集：内容指纹、标题或锚点、BM25 的前 8 个。

**检索结果带类型**：`OK_EMPTY` 直接新加，不调 LLM；`OK_HITS` 进入下一步；`UNAVAILABLE`、`STALE` 一律 `DEFER`。

**逐个命中判定**：判官拿到全部命中，对每个命中分别输出下面几项，执行前再检查这些判定彼此相容。

| 维度 | 输出 |
|---|---|
| 重复度 | `same` / `contained`（新内容被旧内容包含）/ `contains`（新内容包含旧内容）/ `unrelated` |
| 主张对照 | 对候选的每一条 `claims`：`agrees` / `refutes` / `refines` / `independent`，并指出对应旧单元里的哪一句 |
| 适用范围 | 新旧两条的 `applicability` 关系：相同 / 新的更窄 / 新的更宽 / 不重叠 |
| 证据 | 新旧两条各自来源信封里的证据强度（§4.4） |
| 能否改动 | 旧单元带不带 `cand:`；基线单元一律不能改 |

**动作**（由上表按规则算出来，判官只给上表的判定）：

| 条件 | 动作 |
|---|---|
| 有一个命中 `same` 或 `contained` | 不变，`REJECT already_exists` |
| 没有 `refutes`；有一个命中主题和适用范围都相同，且是 `cand:` 单元 | 补充那一条 |
| 没有 `refutes`；有一个命中主题和适用范围都相同，但是基线单元 | 新加一条，加 `extends` 边 |
| 没有 `refutes`，其余情况 | 新加 |
| 有 `refutes`，适用范围相同，新证据更强，或证据一样强且新的直接反驳了旧的 | 替换（见下文） |
| 有 `refutes`，新的适用范围更窄 | 作为例外新加：正文写明适用条件，加 `extends` 边；旧条目在它自己的范围内照样有效，不跳过 |
| 有 `refutes`，旧证据更强 | `REJECT conflicts_stronger`，审计里把两条并排列出 |
| 各个命中之间的动作不相容（比如要替换两条彼此不同的旧条目），或者关系会形成环 | `REJECT incompatible_hits` |
| 命中明显表明内容属于别的去向 | 改投回卡 2，只允许一次 |

**替换**：
- 旧单元带 `cand:`：先归档旧单元再移除，然后写入新单元，加 `supersedes` 边。
- 旧单元是基线单元：判官生成一个**完整的有效单元**，包含旧单元里仍然成立的全部主张，加上新主张。这个有效单元要重新过一遍卡 1，并且检查旧单元每一条没被反驳的主张都还在（逐条对照 `claims`）。通过之后新加这一条，加 `新 —supersedes→ 旧`。读取侧在注入前跳过旧单元（§4.3）。旧单元的文件一个字节不改。
- 有效单元生成不出来，或者对照发现丢了主张：`REJECT conflict_unresolved`。宁可这一次不写。

r10 的"部分冲突只加标注、旧条目照常注入"取消了。独立评审指出：新旧两条可能出现在不同的注入块里，可能因为预算截断只剩旧的一条，也可能被模型忽略。这样模型照样会按旧的过时说法去做。改成"完整有效单元 + 确定性跳过"之后，模型永远只看到一个说法。

**用户说"记住"**：同样要过全部的卡，没有任何特权。区别只在来源信封里的证据强度是 `user_explicit`（§4.4）。被拒绝时，agent 当场把原因码告诉用户。

**已知局限**：系统不用向量库，换个说法或者中英文混用的重复，BM25 找不到。这类重复交给复审兜底，卡 3 端到端的重复召回率要有门槛（§7）。

### 4.3 Ontology：类型、层、关系，以及读取侧怎么用

- **类型** 由卡 1 判定，`classify_entry_type()` 只作为对照信号写进审计；两者不一致时单独计数，不阻断写入。
- **层** 只来自 `layer_for(target_section, type)` 这一个函数，不再有别的来源。例如 `COE Registry` 段里的 pitfall 属于元认知层。
- **Library 文件** 的 frontmatter 写 `ontology_types:`，允许为空。
- **关系图 schema v2**（`knowledge_graph.py`）：`Relation` 新增可选字段 `scope`、`attrs`、`unit_anchor`；YAML 读写加上这几个字段；去重身份改为 `(s, p, o, scope)`；旧边读入时 `scope=None`。`batch_add_relations()` 改为接收带类型的 `RelationInput`。
  `knowledge_graph.py` 现在在模块顶部直接 `import fcntl`，Windows 上会失败。改成用 `utils.file_lock`。
- **卡 3 写的边**：`supersedes`、`extends`、`applies_to`（路径必须真实存在）、`produced_by`。被拒绝的冲突只写进审计，不写进关系图。
- **读取侧跳过被取代的单元**：新增 `superseded_anchors(graph) -> set`，只返回有效 `supersedes` 边的目标。
  不用 `query_related_entries()`，它是双向的，不适合做压制。
  这个集合传给 MEMORY 组装（`context_directory_loader.py:256-305`、`:1066-1078`，和剥掉 dormant/archived 走同一处）、DDD 条目解析（`recall_multi.py:124-166`）、DDD 段打分（`:170-220`，打分**之前**先剔除），以及渲染。
  被跳过的单元既不参与打分，也不出现在输出里。测试要覆盖：预算截断、条目在不同注入块里、BM25 只召回到旧单元，这三种情况下模型都看不到旧单元。
- **复审** 用同一套规则，判断 `cand:` 单元的类型和层是否还对，以及有没有新的 `supersedes`。

### 4.4 来源信封、污染和证据强度

全自动之后，答题卡是唯一的把关。"证据强度"和"是否被污染"如果能由候选自己声明，就会被伪造。所以：

- **来源信封由受信入口生成**，`submit()` 不接受调用方自己填的证据和信任字段。入口和它们给出的信封：

| 入口 | 证据强度 | 污染 |
|---|---|---|
| 用户在 dashboard / 已绑定频道里**本人发出**的消息，且候选正文能在这条消息原文里找到（排除引用块、粘贴的代码块、转发内容） | `user_explicit` | 否 |
| 用户本人的消息，但候选来自其中的引用、粘贴或转发内容 | `inferred` | 是 |
| 测试或命令的实际输出 | `measured` | 否 |
| 评审结论（pipeline expert、独立 reviewer） | `review` | 视输入而定 |
| REFLECT | `run_lesson` | 视输入而定 |
| agent 自己的推断 | `inferred` | 视输入而定 |
| 网页抓取、MCP 返回、别人发的频道消息、读进来的外部文件 | — | 是 |

- **污染会传递**：一个会话、run 或 Library 文件只要读过被污染的内容，从它派生出的候选就继承污染，信任等级取所有输入里最低的那个。污染标记写进候选行，也写进 Library 文件的 frontmatter（`taint: external`）。
- **被污染的候选永远进不了每轮注入的文件**，只能进 Library。从被污染的 Library 文件再蒸馏出来的规则也带着污染，所以同样进不了 MEMORY。这就把"外部内容 → Library → 召回或蒸馏 → 长期规则"这条路堵死了。
- **召回时**，被污染的 Library 内容放在不可信数据边界里渲染（现有的 untrusted-data 包装），模型只把它当数据，不当指令。
- `MemoryGuard` 只是附加检测，不是安全边界。

### 4.5 候选表：一个状态机管重试、执行和审计

- **`ingestion_candidates` 表**：加进 `SQLiteDatabase.SCHEMA`（新库直接建表），同时加一个幂等的 v16 迁移，把 `CURRENT_SCHEMA_VERSION` 改成 16（`database/sqlite.py:2103`、`:2166-2805`），并提供专用的 repository API。测试覆盖 `initialize(skip_init=True)`。

| 字段 | 内容 |
|---|---|
| `candidate_id`、`created_at`、`decided_at` | 幂等键和时间 |
| `writer`、`envelope` | 谁产出的；来源信封（§4.4） |
| `content`、`content_hash` | 全文和哈希 |
| `project_snapshot` | §4.8 的快照 |
| `card0`–`card3`、`ontology` | 每张卡的结构化答案、原因码、理由；类型、层、关系 |
| `state` | `received → judged → prepared → committed`，或者结束于 `rejected` / `deferred` / `expired` |
| `target` | 存储、工作区相对路径、段、单元锚点、动作 |
| `staged` | 只在 `prepared` 状态用：要写的文件新版本和关系图新版本的暂存路径及其校验和 |
| `replaced` | 被替换或跳过的旧单元锚点，以及归档位置 |
| `versions` | 契约、prompt、模型的版本 |
| `retry_count`、`next_attempt_at`、`error_class` | 给 `deferred` 用 |

- **提交协议**：一条候选会同时改三处，语料文件、关系图和这张表，没有现成的跨三处原子操作。所以用"准备再提交"：
  1. 拿到目标文件锁和关系图锁，在锁里比较版本。
  2. 把目标文件的新版本和关系图的新版本写进暂存文件，在同一个 SQLite 事务里把状态改成 `prepared`，并记下 `staged`。
  3. 需要归档的，先写归档。
  4. 用原子替换装上目标文件，再装上关系图。
  5. 状态改成 `committed`，清理暂存文件。
- **恢复**：启动时，以及每次 drain 之前，检查所有 `prepared` 状态的行。两个暂存文件都在、校验和也对，就把剩下的步骤做完；否则用记录下的原版本把已经装上的那一处还原，状态改回 `deferred`。每个崩溃点的预期状态列成一张表，逐个写测试。r5 里的"事务日志"就是这个状态机，不另外再存一份。
- **重试**：`deferred` 按指数退避，最多 5 次，之后变成 `expired`；`rejected` 不重试。
- **保留**：180 天（可配置）。过期行由 drain 顺带清理。
- **不进召回，也不进索引。**

### 4.6 配额和预算

上线时就带着硬上限，数值都写在契约里，可以配置：

| 上限 | 默认 |
|---|---|
| 每个写入者每小时最多多少候选 | 60 |
| 每个来源（同一会话 / run）最多多少候选 | 30 |
| 同时处于 `deferred` 的候选数 | 500 |
| 候选表大小 | 50 MB，超过后最旧的已结束行只保留哈希和决定，正文删掉 |
| 每天新增的 Library 文件 / 字节 | 50 个 / 5 MB |
| 每次提交新增的关系边 | 10 |

- 超过上限的候选直接 `REJECT quota`，只记哈希和原因，不存全文。
- **熔断**：同一个写入者 1 小时内 `REJECT` 和 `DEFER` 占比超过 80%，就暂停这个写入者 1 小时，并发一条通知。
- 判官调用的预算：现有的进程内预算加锁，按优先级分通道：用户纠正 > 会话结束 > REFLECT > 复审。复审另有一份维护预算。预算用完就 `DEFER`。
- 每天的候选数、LLM 调用次数、token 用量、积压时长，都从候选表统计出来，显示在审计页面顶部。

### 4.7 执行顺序（汇总）

卡 0 → 卡 1 → 卡 2 → 卡 3 → 提交协议（§4.5）。提交时：新单元带 `cand:<id>` 和类型标签；Library 文件的 frontmatter 带 `ingestion_candidate`、`ontology_types`，被污染的还带 `taint`。插入用适配器里的 **追加式插入原语**：

- 只在目标段的末尾插入新行，已有单元的每一个字节都原样保留。
- 不触发 `locked_write` 的旧索引清理。
- 不触发 size valve 对基线单元的驱逐。

### 4.8 Knowledge 写入者和项目快照

- **项目快照**：hook 入队之前（`lifecycle_manager.enqueue_hooks`），把会话碰过的仓库根目录、对应的绑定，以及"是否有歧义"一起写进 `HookContext`，同一份快照也写进 DailyActivity 的每条 JSONL 记录。延迟执行的扫描只读这份快照。`context_health_hook.py:961-969` 里写死 SwarmAI 的那段删掉。
- **backflow**：用消息 id 当 `candidate_id`，再过全部答题卡。
- **checkpoint**：新的 checkpoint 写进不进索引的恢复文件 `<app data>/state/checkpoints/<session>.json`，会话正常结束时删掉。已有的 checkpoint 块留在原处不动，读取侧不再召回它们。
- **已删除的会话**：DailyActivity 和 backflow 两个 hook 开头先检查会话还在不在，不在就不写。
- **DailyActivity 块格式**：`upsert_session_block` 加断言。另外追查 `## Session Resume` 独立块是谁写的。

### 4.9 使用信号和读取侧

- 注入块渲染完之后，收集最终块里实际出现的单元锚点，调用一次批量的 `record_ddd_deliveries()`（一把锁、一次原子写），删掉 `record_ddd_hit`。MEMORY 不做使用统计。这一步计入 recall 延迟。
- **DDD 注入条目正文**：一次检索直接返回选中的条目正文、经过校验的 section 和排名。渲染、长度上限、来源、使用信号都用这一个对象，不做第二次检索。先剔除被取代的单元（§4.3），再打分。
- **Library 多取再补足**：先取 24 条，依次丢掉只有 frontmatter 的、正文少于 200 字的、旧 checkpoint 块，再用 `RecallEngine.search()` 现有的哈希去重，最后补足到 8 条。被污染的内容放在不可信数据边界里渲染。标题在渲染时加到来源行上。
- **索引排除依赖目录**：`node_modules`、`.venv`、`dist`、`build`。
- 不重切、不重建索引。

### 4.10 退出：复审只管新能力写入的单元

- 复审范围只包括带 `cand:` 的单元和带 `ingestion_candidate` 的 Library 文件。基线内容不读、不判、不动。
- 按存储契约逐条判断：保留 / 改投 / 替换 / 归档 / 重复，直接执行，走 §4.5 的提交协议，每一个动作都是一行审计记录（`writer=review`）。
- 读完整文件，分段送判官；每次处理的条数有上限；复审有单独的维护预算。
- **触发方式**：判断"到期没有"的地方只有一处，即 `<app data>/state/review.json` 里的上次运行时间，每 7 天最多跑一次。三个时机都会去检查它：
  1. backend 启动时，所有模式都会。
  2. 会话 idle hook。它在会话空闲 120 秒后触发，不是会话关闭时（`lifecycle_manager.py:65`、`:968-1000`）。
  3. daemon / hive 的定时 job，只是让检查更及时。
- **重试和 `prepared` 恢复** 也用这三个时机。这样 subprocess 和 dev 模式不依赖下一次 idle，启动时就会补跑。关闭时不等这些后台任务做完，没做完的状态留在候选表里，下次启动接着做。
- 复审改了某个正在使用的会话已经加载的 MEMORY/context 时，那个会话下一次组装 prompt 才会读到新内容，和现在的 L1 缓存失效机制一致。

### 4.11 context 文件：自动写进用户的 `.context/`，不写源码仓库

| | 位置 | 归属 |
|---|---|---|
| 模板 | `backend/context/*.md`，即 `templates_dir`，由 `Path(__file__)` 推出 | 产品。dev 模式和 worktree 里它就在源码仓库里，打包后在安装包里 |
| 用户的 live 文件 | `<workspace>/.context/*.md` | 当前用户。prompt 读的就是这一份 |

1. `ContextFileSpec` 新增 `auto_write_eligible`，只有 USER、STEERING、TOOLS 为真。这个标记不能从 `user_customized` 推出来。SWARMAI、IDENTITY、SOUL、AGENT 是产品文件，用户的规则一律改写到 STEERING。
2. **候选里存工作区 id 和相对路径**（`.context/STEERING.md`），不存绝对路径。执行时用当时的 `get_workspace_path()` 重新解析，所以工作区搬家、换盘符、容器挂载都不会让候选失效。绝对路径只作为审计里的快照。
3. **执行前在锁里校验**：解析出的路径必须正好落在 live `.context/` 下，不在 `templates_dir` 下；文件必须是 git 未跟踪或已忽略的；版本必须一致，不一致就 `DEFER`。
4. **写入原语** `write_context_cas()`：加锁 → 锁里比较版本 → 归档整份旧文件 → 安全扫描 → 按文件名做格式校验 → 原子替换 → 保持 0o644 权限。自动写入和两个工作区编辑接口都用它。对已有内容只追加，不改动（§4.7 追加式插入）。
5. 受控模块没有写 `templates_dir` 的能力。源码仓库里的模板只在开发时、由人明确指定时，走普通的代码修改流程来改。

### 4.12 Ingestion 审计（产品功能）

- **数据来源**：只读 `ingestion_candidates` 一张表，不新建任何存储。
- **接口**
  - `GET /api/ingestion/candidates`：按时间、写入者、存储、状态、原因码、关键词筛选，分页，单页最多 100 行。列表里正文截到前 200 字。
  - `GET /api/ingestion/candidates/{id}`：返回一行的完整内容。
  - 两个接口都是只读的，跑在 `nav` 线程池上（`executors.run_nav`）。
- **隔离和安全**
  - 服务端只读当前 backend 实例自己的 `data.db`，调用方不能传路径或用户参数。
  - 接口沿用 dashboard 现有的本机认证，CORS 继续只放行明确列出的来源。
  - 正文、来源、路径一律当纯文本渲染，不解析 Markdown 或 HTML。
  - 疑似密钥由 `MemoryGuard` 的密钥规则打码后再返回。
  - 访问本身也写进日志。
- **界面**：在 Left Nav 加一个 overlay，要更新 `overlayIds.ts` 的 ID 元组、`navTable.ts` 的 `NAV`、chunk loader 和 `registerOverlay`，相关的相等性测试也要过。界面遵守 Nav visual system，用共享组件，中英文都做 i18n。
  - **列表**：时间、写入者、内容开头、去向、状态、原因码。
  - **详情**：完整内容；来源信封（证据强度、是否被污染）；卡 0 到卡 3 每道题的答案和理由；类型和层；写了哪些关系边；写到了哪一个单元，可以点开；替换或跳过了哪个旧单元、归档到哪里。
  - **顶部统计**：最近 7 天按状态、去向、原因码的计数；`deferred` 积压量；每天的 LLM 调用次数和 token 用量；判官和关键词分类器结论不一致的次数；熔断记录。
- **不提供审批按钮**。发现判断有错，就去改契约、prompt 或规则，然后把版本号加一。
- **通知**：判官或检索连续失败超过 1 小时、触发熔断、运行时拦下一次绕过写入，都会通知。

### 4.13 对每个用户都可用，不依赖这台机器

- **路径**：只从 `config`、`get_workspace_path()`、app 数据目录取，不写死 `~/.swarm-ai`、仓库路径或项目名。候选里存的是工作区相对路径（§4.11）。项目归属只看用户自己的 DDD 和 `bindings.yaml`。
- **平台**：文件锁一律用 `utils.file_lock`，包括 `knowledge_graph.py`；原子写用现有的跨平台工具。
- **运行模式**：daemon、subprocess、hive、dev 四种模式下，写入、重试、恢复、复审、审计都能跑（§4.10）。
- **打包**：打包后 `templates_dir` 在安装包里，§4.11 的校验照样成立。不调用 `sys.executable -m`。
- **不依赖开发者自己的东西**：运行时不用 `Projects/SwarmAI` 里的 skill、回放脚本，也不用盲评脚本。
- **工作区不是 git 仓库时**：§4.11 第 3 条的 git 检查跳过，只检查目录归属。
- **`s_memory-distill`**：改造成一个手动触发入口，调用 `governed_store` 的"立即蒸馏"和"立即复审"，然后汇总这一次的审计结果。skill 里不再写任何阈值或段名。

## 5. 每个事实在哪、谁写谁读

| 事实 | 唯一来源 | 写 | 读 |
|---|---|---|---|
| 准入问题、路由表、段标题表、配额、来源策略 | `store_contracts` | 人改 | `governed_store`、读取侧、复审 |
| 哪些 context 文件能自动写 | `ContextFileSpec.auto_write_eligible` | 人改 | `governed_store` |
| 基线（哪些内容已沉淀） | `<app data>/state/baseline.json` | 上线时生成一次 | 适配器、复审、验收 |
| 候选的状态、卡、去向、理由、暂存 | `ingestion_candidates` | `submit`、drain、复审 | drain、审计接口 |
| 来源信封 | 候选行的 `envelope` | 受信入口 | 卡 0、卡 3、审计 |
| 会话属于哪个项目 | `HookContext` 快照（DailyActivity 记录里有同一份） | 入队前 | 卡 1、卡 2 |
| 条目的类型和层 | 条目上的类型标签；`layer_for()` | 卡 1、卡 2 | 读取侧、生命周期、复审 |
| 关系 | `.knowledge-graph.yaml`（schema v2） | 提交协议 | 读取侧（`superseded_anchors`、`query_related_entries`）、审计 |
| 条目有没有送达过模型 | `.ddd-usage.json` | 注入后批量写 | 复审 |

热路径：答题卡、候选表、复审都只在后台跑，不碰 chat 的发送路径。读取侧的改动在发送路径上，要实测：多取一次 FTS，DDD 少一次重复加载，使用信号从 N 次文件事务变成 1 次，`superseded_anchors` 在关系图加载时缓存一次。验收要求不比现在慢。

## 6. 已经沉淀的内容：一律不动

- **基线**：上线后 backend 第一次启动时，生成一次 `baseline.json`，记录每个受控文件里每个已有单元的锚点和字节哈希，以及每个非受控记录文件（Note、DailyActivity、会话、Reports、Signals）的整文件哈希。这一步只读，不改任何文件。
- **规则**：基线里的单元和文件，新功能和现有维护机制都不读来改、不移动、不删除、不加标记。
  - §4.1 里那些会改动已沉淀内容的维护路径（Memory Health 的修改阶段、EVOLUTION prune、DDD 矛盾剥离、size valve 和 section cap 对基线单元的驱逐、`locked_write` 的旧索引清理）全部退役，或者改成只作用于 `cand:` 单元。
  - 上一版（r10）决定的"三处直接删除改成先归档"随之作废：这几处不再碰基线内容，所以没有可删的。
- **新旧内容怎么相处**：卡 3 检索得到基线单元，只拿来判断。补充时写一条新单元，加 `extends`；替换时写一条完整的有效单元，加 `supersedes`，读取侧跳过旧单元（§4.2、§4.3）。
- **读取侧**（§4.9）只改变召回什么、怎么渲染。
- **派生索引**：`node_modules` 之类的 chunk 下次同步时从索引里删掉，源文件不动。
- **已知后果**：MEMORY 不再按大小驱逐基线条目，所以这个文件只会变大。新条目要先过 `not_global`，复审也会处理 `cand:` 条目，所以增长会比现在慢很多。每轮注入的预算超了，读取侧照现在的做法只报 `over_budget` 警告，不截断。审计页面顶部显示 MEMORY 的大小。

## 7. 实施和验收

每一步一个 worktree，ff-merge 成一个 commit，不 push，不 rebuild。

**第 1 步：读取侧和索引**（不改语料）
1. `store_contracts` 里的来源策略部分，加上依赖目录排除。
2. Library：多取、过滤、补足名额；标题在渲染时加上；旧 checkpoint 块不召回；被污染的内容放在不可信边界里渲染。
3. DDD：一次检索拿到条目正文，校验 section，批量记录使用信号。
- 验收：用固定的回放清单（第 101–200 个会话，清单哈希写进文档），A/B 随机分组盲评，判官的评分标准和模型版本固定。
  通过条件：噪音比例下降至少 3 个百分点；"有用"比例和"需要且拿到"比例各自下降不超过 1 个百分点；注入总字符数不超过基线；recall p90 不超过基线。

**第 2 步：受控写入、答题卡、候选表、审计**（一次切换）
4. 契约、身份、基线生成、`ingestion_candidates` 迁移、来源信封、配额。
5. `governed_store`：适配器、追加式插入原语、`submit` 和四张卡、提交协议和恢复、drain（启动时、idle 时、定时）。
6. 关系图 schema v2、`superseded_anchors` 接进三个读取点、`knowledge_graph` 改用 `utils.file_lock`。
7. 所有语义写入者一次切过来；会改动已沉淀内容的维护路径退役或缩小范围；`governed_corpus_gate` 登记进 `hook_builder.py`，AGENTS.md 同步更新；静态测试和运行时绕过测试。
8. 项目快照、backflow 身份、checkpoint 改写到恢复文件、已删除会话不写、DailyActivity 块断言。
9. 审计接口和 overlay；`s_memory-distill` 改造。
- 验收：
  - **夹具**：一份可以提交进仓库的合成语料，放在 `backend/tests/fixtures/ingestion/`，不含任何私人内容，CI 必须能跑。内容包括：
    - 七类 × 两种形式 × 四种主体，按卡 1 的维度标注 200 条；
    - 留出 50 条不参与 prompt 调试；
    - 30 对"换了说法的重复"、20 对"部分冲突"、10 对"整条冲突"、10 条"窄例外"；
    - 中英文都有。
    今天审计用的私人样本只作为本地补充，不是 CI 的必需输入。
  - **门槛**（预先定好，跑之前不再改）：
    - 写入内容的精确率 ≥ 95%（写进去的内容里，人工标注为"该写"的比例）；
    - 卡 1 对"该写"的召回率 ≥ 80%；
    - 写进 MEMORY 的内容里，项目专属或不具全局相关性的 ≤ 3%；
    - 卡 3 对换了说法的重复，端到端检出率 ≥ 70%；
    - 冲突对的动作正确率 ≥ 90%；
    - 类型一致率 ≥ 85%，层一致率 ≥ 95%。
    结果按写入者、语言分组报告置信区间。
  - **路由属性测试**：穷举卡 1 字段的所有合法组合，每个组合恰好命中一行；目标段在模板里真实存在。
  - **读取侧行为测试**：被 `supersedes` 的旧单元，在预算截断、不同注入块、BM25 只召回旧单元三种情况下都不出现。再加一组模型行为测试：把有效单元和问题一起交给模型，模型按新说法行动；对照组同时给新旧两条，用来量化差别。
  - **安全测试**：
    - 被污染的候选进不了 MEMORY、EVOLUTION、context 文件；
    - 从被污染的 Library 文件蒸馏出的候选同样进不去；
    - 引用或粘贴内容拿不到 `user_explicit`；
    - 覆盖中文、改写、分步诱导三类注入样本；
    - 审计页面渲染恶意内容时不执行；
    - 跨工作区读不到别人的候选表。
  - **配额和熔断**：一个恶意写入者持续换 `candidate_id`，另一个持续出故障，各跑 1 小时 soak。候选表和 Library 的增长不超过上限，熔断正常触发。
  - **提交协议**：在每个崩溃点（暂存之后、归档之后、文件装上之后、关系图装上之后）杀掉进程，恢复后的状态和预期表一致。再加两个写入者并发、复审和写入并发两种情况。
  - **唯一写入口**：静态测试中故意加一处直接写 MEMORY，测试失败；运行时用 `Path.write_text`、rename、subprocess、symlink、路径穿越各写一次，都被拒；agent 用 Write 和 Bash 直接写受控文件，都被 `governed_corpus_gate` 拒，并提示 submit 命令。
  - **已沉淀内容不变**：上线前后对比基线里每个单元的字节哈希；对每个受控文件，检查基线单元按原顺序构成新文件的一个子序列（新单元只能插在段末）；非受控记录文件比对整文件哈希。
  - **全平台矩阵**：daemon、subprocess、hive、dev × macOS、Windows、Linux × 源码运行、打包安装 × 工作区在 git 里、不在 git 里、搬过家。每个组合都要跑通写入、重试、恢复、复审、审计。CI 覆盖其中能自动化的部分（backend 和 backend-windows 两个 job），其余列成发布检查表。
  - 每条规则都做 mutation 检查：撤掉修复，对应的测试必须变红。

**第 3 步：复审**
10. 复审只针对 `cand:` 单元，按三个时机触发。
- 验收：在合成夹具上，复审结论和标注的一致率 ≥ 80%；用只含基线内容的夹具跑复审，所有字节不变；每个动作都能在审计里看到。

上线后第一周每天看一次审计统计。某一类判断持续出错，就改契约或 prompt，然后把版本号加一。

## 8. 不做

- **不做人工审批和提案。** 判断交给答题卡，人看审计。cultivation 原来的提案审批状态机删除，它的三路输入改成产出普通候选。
- **不做候选收件箱，也不做"提升"流程。**
- **不做批量的语义合并。**
- **不引入向量库。** 换了说法的重复靠卡 3 的 BM25 加复审来抓，并设检出率门槛。
- **不做影子模式、切换日，也不让新旧写入路径并存。**
- **不整理已经沉淀的内容。**
- **不整类排除** Pollinate 或 `Knowledge/Archives/`，只排除依赖目录。
- **不改 DailyActivity 的 LLM 提炼门槛。**
- **不重切、不重建索引。**

## 9. 已确认

1. 全自动：答题卡就是最终决定，人看审计（r6）。
2. 已经沉淀的内容一律不动，只让新内容走新流程（r7）。会改动已沉淀内容的维护路径退役，或者缩小到只作用于 `cand:` 单元（r11，见 §6）。
3. 先实施第 1 步。

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

## 11. 验收结果

（实施后填写。）
