---
title: Recall 语料的写入与准入 — Memory、DDD、Knowledge 三块统一的准入、身份、使用信号与退出
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r5 — 定稿，已过两轮独立评审（r1 Knowledge；r3 三张答题卡；r4 context 提案位置；r5 吸收评审，见 §10）
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
| Knowledge Library（Notes / Library / Learned） | 是（召回时排名） | 离开原会话也能独立读懂、可复用的参考材料 | 新加（一个文件） | 不自动退出；复审只出提案 |
| 用户 context 文件（USER / STEERING / TOOLS） | 是（每轮整份注入） | 改的是这个用户"怎么判断" | **只能提案，人工批准**（§4.9） | 人工 |
| DailyActivity | 是（日志，按日期规则召回） | 不过答题卡，它是日志 | 按会话 id 覆盖写自己的块 | 不退出 |
| 候选 WAL（§4.3） | **否** | — | 追加 / 标记结束 | 结束后 30 天压缩 |
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
| 语义写入（必须走 `submit`） | REFLECT 的新加 / 强化 / 细化（`reflect_job.py:248-282`）、REFLECT 写 pipeline-lessons（`reflect_library.py:161-207`）、自动蒸馏、即时纠正、`memory_extractor.py:351`、`evolution_optimizer.py:899`、DailyActivity 决策/纠正 → DDD、backflow、人工批准 cultivation 提案、`s_persist` / `s_self-evolution` / `s_ddd-persist` | 三张卡 |
| 结构刷新和元数据 | `ddd_orchestrator.py:566-590` 的机械替换、`ddd_maturity.py:521-539` 的成熟度元数据、生命周期状态 | 不改语义；只走受控模块里的结构写函数，不跑答题卡 |
| 日志和过程产出 | DailyActivity、checkpoint、JobResults（`jobs/executor.py:2007-2056`）、Signals 摘要、Reports、Pollinate 包 | 不是知识存储。在契约里标明召回规则（Signals 和 DailyBriefs 现在已经不自动注入） |
| 初始化 | `ensure_directory` 复制模板、工作区骨架 | 不变 |
| 人工直接编辑 | 两个工作区编辑接口 | 改受控路径时走 `write_context_cas`（§4.9）或受控的整文件写入，不跑判官 |
| 删除的写入者 | session 结束 writeback（置信度 0.5 永远过不了 0.7）、`s_memory-distill` skill | 先看 writeback 过去 30 天丢弃的候选，确认没有价值再删 |

### 4.2 三张答题卡

**输入和输出都有固定的结构**，带版本号（契约版本、prompt 版本、模型 id），每次决定都记下这三个版本。
每张卡输出 `PASS` / `REJECT` / `DEFER` 三者之一，加一个原因码。

- `REJECT`：判断过了，不该写，不重试。
- `DEFER`：没法判断（判官预算用完、LLM 出错、输出解析失败、召回不可用或索引过期、拿不到锁、
  会话碰了多个项目分不清该算哪个），候选留在 WAL 里，稍后重试（§4.3）。
- 拿不准时判官必须给 `REJECT`，并标原因码 `uncertain`。这就是"宁缺毋滥"：判断不了的内容不写。

**卡 1：质量。** 五个问题分别作答，每题给一个原因码；任意一题答"否"，结果就是 `REJECT`。

| 问题 | 原因码 | 答"否"的例子 |
|---|---|---|
| 一条只讲一件事、去掉了复述和过程？ | `not_concise` | 把整段会话结论原样贴进来 |
| 以后遇到同类情况会改变做法？ | `no_judgment_value` | "已完成 X"、"测试 56 个全绿" |
| 自洽、不依赖"这里/刚才/上面"？ | `not_self_contained` | "按上面的方案改" |
| 没有会话 id、临时路径、会变的数字？ | `noisy` | 带 scratch 路径和 commit 统计的流水 |
| 不记下来以后会有损失？ | `not_worth_keeping` | 只在这次会话里有用的状态 |

其中"会话 id、临时路径、PID、hash"这类噪音先用规则检测，判官只处理剩下的。

**卡 2：去向。** 卡 1 和卡 2 合在一次判官调用里做。去向按固定顺序判断，命中第一条就停：

1. 改的是这个用户"怎么判断"（偏好、做事方式），不是一个知识点 → 用户 context 文件，只生成提案。
2. 关于 agent 自己的能力或纠正，不针对任何项目 → EVOLUTION。
3. 只影响某一个项目的判断 → 那个项目的 DDD，并指明是哪份文档。项目取自候选随身带的项目快照（§4.5）。
   快照里有多个项目分不清时 → `DEFER`（原因码 `ambiguous_project`），不猜。
4. 跨项目的判断规则 → MEMORY。
5. 可以独立阅读的参考材料，而不是规则 → Knowledge Library。
6. 都不是 → `REJECT`。

**卡 3：准入。** 先在卡 2 选定的去向里检索，再决定动作。

- **检索**：写入时专用的语料适配层，**不调用**读取侧的注入入口。读取侧会排除已经在 prompt 里的内容，
  这和写入时去重的需求正好相反。适配层复用读取侧的打分函数，检索范围按契约：
  MEMORY 要检索 live MEMORY、STEERING、SOUL、AGENT 和目标项目的 DDD；DDD 检索整份文档；Library 检索同目录。
  检索是三路的并集：内容指纹完全相同、标题或锚点相同、BM25 前 8 条。
- **检索结果带类型**：`OK_EMPTY`、`OK_HITS`、`UNAVAILABLE`、`STALE`。只有 `OK_EMPTY` 可以不调判官直接"新加"；
  `UNAVAILABLE` 和 `STALE` 一律 `DEFER`。
- **动作**：不变 / 新加 / 补充 / 替换 / 改投，必须指出对应的旧条目锚点和它当前的版本（§4.4 用它做校验）。
- **已知的局限**：系统不用向量库（memory sovereignty），所以 BM25 找不到换了说法、或者跨中英文的重复。
  这类重复交给复审兜底（§4.8），验收时单独统计卡 3 能找回多少重复（§7）。

**人工写入**：卡 1、卡 2 的结论和卡 3 的建议动作都展示给人，人可以改，然后确认。人只能决定判断结果，
不能跳过检索。

### 4.3 候选 WAL 和人工权限

**候选 WAL**：r4 的"拿不准就不写"会把"判断过、不该写"和"没能判断"混在一起，后者会让人工纠正、
REFLECT 和 backflow 这些只出现一次的候选直接丢掉。r5 改成：

- `submit` 在跑任何答题卡之前，先把候选追加进 WAL（`<app data>/state/admission/candidates.jsonl`）。
  每条记录包括 `candidate_id`、来源、正文、正文哈希、项目快照、权限、走到哪张卡、错误类型、重试次数、下次重试时间。
- WAL **不进召回、不进索引**。它只是为了"没判断完的候选能恢复"，不是候选收件箱，也没有"提升"这一步。
- 一个后台 worker 按 `candidate_id` 幂等重试 `DEFER` 的候选，退避间隔逐次加长，最多 5 次。
  5 次之后标记为 `expired`，出现在每周报告里。`REJECT` 不重试。
- WAL 写入用加锁追加，带 schema 版本；状态变化追加新记录，不改旧行。候选结束 30 天后压缩，
  只留哈希和决定，不留正文。

**人工权限**：r4 让调用方自己传 `authority="human"`，任何代码都可以冒充。r5 改成：

- `submit` 不接受调用方声明权限。只有一种情况算人工批准：用户在 dashboard 上点了批准。
  批准时产生一条记录，包含 candidate 哈希、目标、卡的答案和一个一次性 nonce，`submit` 校验这条记录才执行。
- 内容、目标或版本对不上，或者 nonce 已经用过，就拒绝。
- agent 调用 skill（比如用户说"记住"，agent 去运行 `s_persist`）属于 agent 写入，**三张卡完整跑**。
  被拒的内容会变成一条提案，需要用户在 dashboard 上批准。
- cultivation 提案的批准接口现在没有认证。它改成只接受同一种批准记录，不再直接调用 `apply_to_ddd`。

### 4.4 执行：校验版本，再归档，再写入

在卡 3 检索和真正写入之间，别的写入者或复审 job 可能已经改过目标文件。执行分这几步：

1. 拿到目标存储的锁（MEMORY、EVOLUTION、context 文件用各自的 `.md.lock`，DDD 用文档锁）。
2. 在锁里重新读取，比较卡 3 记下的条目版本（条目内容哈希；新加时用整文件哈希）。不一致就 `DEFER`，重新跑卡 3。
3. 需要归档的（替换），先归档，再从原文件移除。用 `archive_and_strip_lines`（`ddd_entry_lifecycle.py:1256`）；
   EVOLUTION 的 `###` 块用 `archive_raw_lines`（`:1284`），整份 context 文件用整文件快照。r4 写的 `archive_entries`
   只追加，不移除，用错了。这三种归档统一包成一个 `archive_then_strip()` 入口。
4. 写入新内容，原子替换文件。
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

### 4.8 退出：复审

- **只有两类自动执行**：(a) 内容指纹完全相同的重复；(b) 切换日（§6）之后写入、并且满足契约里写明的时间和证据条件的条目。
  两类都按 §4.4 先归档再移除，每次有数量上限，记进 `EVOLUTION_CHANGELOG.jsonl` 和每周报告。
- **需要判断的**（改投、替换、过时）**只出提案**，你批准后才执行。提案的通过率连续几轮稳定之后，再讨论要不要放开自动执行。
- 复审读**完整**文件，分段送判官，不再只看前 8K 字符。TECH 也包括在内。
- 每条提案都绑定契约、prompt、模型的版本，以及生成时的文件哈希。任何一项变了，提案就过期。
- 复审用单独的维护预算（§4.10）。
- 现在直接删除的三处改成走 `archive_then_strip()`：Memory Health（`memory_health.py:823-887`、`:1018-1064`）、
  EVOLUTION prune（`evolution_maintenance_hook.py:921-954`）、DDD 矛盾替换（`ddd_cultivation.py:978-1007`）。

### 4.9 context 文件：提案写到用户的 `.context/`，不写源码仓库

context 文件有两份，位置不同，很容易混：

| | 位置 | 归谁 |
|---|---|---|
| 模板 | `backend/context/*.md`，代码里叫 `templates_dir`，由 `Path(__file__)` 推出来（`prompt_builder.py:1244`、`initialization_manager.py:438`） | 产品的。dev 模式和 worktree 里运行时，它**就在源码仓库里** |
| 用户的 live 文件 | `<workspace>/.context/*.md`，workspace 来自 `get_workspace_path()` | 当前用户的。prompt 读的是它 |

1. **能接受提案的文件用显式字段标出来**：`ContextFileSpec` 加 `proposal_eligible`，只有 USER、STEERING、TOOLS 为真。
   不能从 `user_customized` 推出来，因为 SELF、MEMORY、EVOLUTION、KNOWLEDGE 的这个标记也是真。
   SWARMAI、IDENTITY、SOUL、AGENT 是产品文件，每次会话开始都会从模板覆盖回去，而且是只读的。
   针对这个用户的判断规则一律改投 STEERING。
2. **目标路径在生成提案时就定下来**：`get_workspace_path()/.context/<文件名>` 解析成绝对路径，和当时的文件哈希一起存进提案。
   批准和执行都只用这个路径，不在执行时重新推算。
3. **执行前在锁里再校验一次**：路径必须正好在 live `.context/` 目录下；不能在 `templates_dir` 下；
   文件必须没被 git 跟踪或已被忽略。SwarmWS 本身就是 git 仓库，`.context/` 是被 `.gitignore` 排除的，
   所以 r4 写的"在任何 git 仓库里就拒绝"会把正确的目标也拒掉。哈希要和提案时一致，不一致就按当前内容重新出提案。
4. **写入原语** `write_context_cas()`：加锁 → 锁内比较哈希 → 归档整份旧文件 → 用 `MemoryGuard` 做安全扫描
   → 按文件名做整份文档的格式校验 → 原子替换 → 权限保持 0o644。r4 把 `MemoryGuard` 写成了格式校验，这不对，
   它只做安全扫描。提案执行和两个工作区编辑接口都用这一个原语。
5. **源码仓库里的模板**只在开发时、由人明确指定时，按普通的代码修改流程去改（worktree、commit）。
   受控写入模块没有任何写 `templates_dir` 的能力。

测试：在 worktree 里运行（`templates_dir` 在仓库里）时，提案执行被拒；提案目标是 SOUL 时被拒；
生成提案后用户改了 STEERING，会重新出提案；以上每种情况下，`backend/context/` 都没被写过。

### 4.10 判官预算和成本

- 判官调用都在 daemon 进程里。现在的预算（60 次 / 300 秒）只在进程内有效，而且不是线程安全的。改成加锁，并分成几个优先级通道：
  人工纠正 > 会话结束 > REFLECT > 维护（复审）。复审单独一份预算，用完就等下一轮，不会挤占写入。
- 预算用完返回 `DEFER`，候选留在 WAL 里，不会丢。
- 每天有多少候选、每个候选调几次判官、用多少 token、WAL 最长积压多久，先在影子阶段（§7）实测，再定上限和告警阈值。
  这里不先拍数字。

### 4.11 可观测性

- 决定日志 `admission_decisions.jsonl`：带版本的追加接口，加锁，按月轮转，保留 180 天。
  只存哈希、原因码、版本、目标，外加最多 200 字的节选，不存全文，不进召回。
- 每周报告新增：按写入者 × 存储 × 卡 × 动作 × 原因码的计数，`DEFER` 积压量和最长积压时间，版本校验冲突次数，
  架构测试和运行时拦下的绕过尝试次数。
- 判官或检索连续失败超过一小时，或者出现一次绕过尝试，就推送一条通知。

## 5. 每个事实在哪、谁写谁读

| 事实 | 唯一来源 | 写 | 读 |
|---|---|---|---|
| 每个存储的准入问题、动作、检索范围、退出；来源策略 | `store_contracts` | 人改 | `governed_store`、读侧、复审 |
| 哪些文件能接收 context 提案 | `ContextFileSpec.proposal_eligible` | 人改 | `governed_store` |
| 一个候选的状态 | 候选 WAL | `submit`、重试 worker | 重试 worker、每周报告 |
| 一次决定的依据 | 决定日志 | `submit` | 校准、每周报告 |
| 会话属于哪个项目 | `HookContext` 里的项目快照，以及 DailyActivity 记录里的同一份快照 | 入队前的快照 | 卡 2 |
| 人工批准 | dashboard 批准记录（哈希、目标、nonce） | 批准接口 | `submit` |
| 一个条目有没有送达过模型 | `.ddd-usage.json` | 注入后的批量记录 | 复审 |
| context 提案的目标 | 提案里的绝对路径和文件哈希 | 生成提案 | 执行前校验 |

热路径：答题卡、WAL、复审都只在后台 hook、REFLECT、skill 和定时任务里跑，不碰 chat 发送路径。
读侧的改动（§4.6、§4.7）在发送路径上，要求延迟实测不比现在差：多取 24 条只多一次 FTS 查询，
DDD 少了一次重复加载，使用信号从 N 次文件事务降到 1 次。

## 6. 历史数据

**切换日**定义为阶段 2 第一个写入者从影子模式切到正式执行的那一天，记在 `store_contracts` 里。

- **不可变**：切换日之前已有的 Note、DailyActivity 文件（包括里面已有的 checkpoint 块）、会话、Reports、Signals，
  一律不改名、不删除、不改写。它们的问题在读取侧处理（§4.7）。
- **派生索引**：`node_modules` 之类的 chunk 下次同步时从索引里删掉，源文件不动。索引本来就是从文件派生的。
- **live 知识文件**（MEMORY、EVOLUTION、DDD）本来就会被系统修改（size valve 等）。对这些文件里切换日之前的条目：
  不自动移动，只出提案，你批准后才按归档优先执行。切换日之后写入的条目按 §4.8 的规则执行。
  MEMORY 里那 51 条项目专属条目和 12 条重复，属于前一种。
- 验收时对不可变集合做 SHA-256 清单，比较前后，要求完全一致。

## 7. 上线顺序和验收

每一步一个 worktree，ff-merge 一个 commit，不 push、不 rebuild。每个阶段都要验收通过，才进下一个阶段。

**阶段 1：读侧和索引**（不改语料）
1. `store_contracts` 的来源策略部分，替换现有的几组常量，加依赖目录排除。
2. Library：多取、过滤、补足名额；标题在渲染时加上；旧 checkpoint 块不召回。
3. DDD：一次检索出正文，校验 section，批量记录使用信号。
- 验收：
  - 固定回放清单：第 101–200 个会话，清单的哈希写进文档。
  - 盲评：A/B 标签随机，判官的评分标准和模型 id 固定，判官看不到哪边是新的。
  - 通过条件：噪音比例下降至少 3 个百分点；"有用"比例和"需要的内容拿到了"比例下降都不超过 1 个百分点（约等于判官的重测误差）；
    注入总字符数不超过基线；recall 的 p90 延迟不超过基线。
  - 盲评 DDD 时单独评注入的正文。

**阶段 2：写入准入**（顺序很重要）
4. `store_contracts` 加上存储契约、四种身份、WAL 和决定日志的格式，以及 `governed_store.submit`（三张卡、§4.4 的执行步骤）。
5. **影子模式**：每类写入者都调用 `submit(shadow=True)`，三张卡照常跑、记录决定，但不执行。原来的写入照常进行。
   每类写入者跑够一周，或者攒满 50 个候选，就把影子决定和原来的写入结果做对比。
6. **逐类切换**：每类写入者有一个开关（`store_contracts` 里），一次切一类，从 backflow 开始，然后是 REFLECT、自动蒸馏、即时纠正、
   DailyActivity → DDD、各个 skill。如果 `DEFER` 比例超过 20%、WAL 积压超过 24 小时，或者有一个绕过，就停下，把这一类切回去。
   切回去是安全的：WAL 和事务日志都是幂等的。
7. 全部切换之后，把底层写函数改成私有，启用架构测试，删除旧路径（writeback 先看丢弃日志再删）。
- 验收：
  - 夹具：今天审计里已经分好类的样本（MEMORY 97 条、DDD 40 条、backflow 15 篇）。下面的阈值先写死，不看结果再改：
    卡 1 对"该留"的放行率 ≥ 85%，对"不是持久内容"的拦截率 ≥ 80%；卡 2 把 51 条项目专属 MEMORY 条目送到正确 DDD 的比例 ≥ 80%；
    卡 3 对审计确认的重复对给出"不变"（全部），对 4 条过时条目给出"替换" ≥ 3 条。卡 3 检索的召回率单独报告。
  - 黄金用例：每条去向规则的边界、多项目歧义、检索 `UNAVAILABLE` / `STALE`、空检索结果，都有固定的预期输出。
  - 验收矩阵：写入者 × 卡 × 动作 × 失败类型，每格写明预期结果和留下的证据。故障注入覆盖：预算用完、LLM 出错、输出格式错、
    检索不可用、日志写失败、拿不到锁、批准记录对不上。
  - 并发和崩溃：两个写入者同时写、复审和写入同时进行、在 §4.4 每一步之后让进程崩溃，验证都能恢复。
  - 架构测试：故意加一个直接写 MEMORY 的调用，测试必须失败。
  - 每条规则都做 mutation 检查：撤掉修复，对应的测试必须变红。
  - 不可变集合的 SHA-256 清单前后一致。

**阶段 3：退出**
8. `archive_then_strip()`，替换三处直接删除。
9. 复审并进每周 Memory Health：读完整文件，按 §4.8 出提案、自动执行两类。
- 验收：复审对审计样本的判断和审计结论一致的比例 ≥ 80%；每条退出路径都有"先归档、再移除、崩溃可恢复"的测试。

## 8. 不做

- **不做候选收件箱和提升流程。** WAL 只负责"没判断完的候选能恢复"，不进召回，也没有"提升"这一步。
- **不做自动的语义合并。** 近似内容由卡 3 在写入前判断；没找到的交给复审出提案。不做批量的相似度合并。
- **不引入向量库。** 换了说法和跨语言的重复，卡 3 的 BM25 找不到，这是已知局限，由复审兜底，验收时单独统计。
- **不整类排除** Pollinate 或 `Knowledge/Archives/`，只排除其中的依赖目录。
- **不改 DailyActivity 的 LLM 提炼门槛。**
- **不重切、不重建索引。**

## 9. 待确认

1. 切换日之前的 live 知识条目（包括 MEMORY 里那 51 + 12 条）只出提案，你批准后执行；切换日之后的条目，按 §4.8 自动执行两类。可以吗？
2. 删除 `s_memory-distill`；session 结束 writeback 先看过去 30 天它丢弃的候选，再决定删不删。
3. context 提案只针对 USER / STEERING / TOOLS，写到用户的 `.context/`；SOUL / AGENT 这些产品文件不接受提案。
4. agent 调用 skill 写入（包括你说"记住"）也要过完整的三张卡，被拒的内容变成提案，需要你在 dashboard 上批准。
5. 阶段 1 不改任何语料，可以先做。

## 10. 评审记录

r4 之后，两位独立 reviewer 各审了一遍。两人都没有本会话的上下文，只读代码和这份文档。
- **代码事实和接入**：3 个 BLOCKER、9 个 MAJOR。已全部吸收：可提案文件的判定、git 仓库检查、项目快照、
  入口合并、写入时检索的语料、REFLECT 的另外两条直写路径、使用信号批量化、DDD 一次检索、Library 补足名额、
  checkpoint 跨午夜、归档函数用错、`MemoryGuard` 不是格式校验、写入者清单不全。
- **设计本身**：5 个 BLOCKER、8 个 MAJOR。已全部吸收：结构上只有一个写入口、`DEFER` 和 WAL、人工权限不能由调用方声明、
  四种身份、确定性的卡、带类型的检索结果、历史数据的定义、写入前校验版本、复审的权限、预算通道、验收矩阵、
  有阈值的盲评、影子模式和逐类切换、可观测性。
- 有一条没有照搬："跨进程的配额服务"。判官调用都在 daemon 这一个进程里，所以只加锁、分优先级通道，不另建服务。

## 11. 验收结果

（实施后填写。）
