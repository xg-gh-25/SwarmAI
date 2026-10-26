---
title: Swarm Cognitive Distillation Flow — SwarmAI 认知系统的核心引擎：提取与 ingestion 解耦，Memory、DDD、Knowledge 统一的 Inbox、答题卡、写入与 Flow 视图
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r14 — 设计，已吸收 r13b 两份独立评审（修订记录见 §18）；r14.1 定名
---

# Swarm Cognitive Distillation Flow

**Swarm Cognitive Distillation Flow（简称 CDF，认知蒸馏流）是 SwarmAI 认知系统的核心引擎。** 这是一项产品级能力，对每个 SwarmAI 用户都可用，在每种平台和运行模式下都能工作。它把会话、run、用户纠正和外部资料中值得长期保留的东西，按统一的标准蒸馏成 Memory、EVOLUTION、各项目的 DDD、Knowledge Library 和用户 context 文件。蒸馏全程自动完成，每一步都可审计。

AGENTS.md 说"认知和知识分开、模型提议、OS 决定、价值跨会话复利"，CDF 就是让这几句话在写入侧落地的那个机制。

**术语**：
- **CDF** 指整条流程：source 提取 → Inbox → ingestion（整理、答题卡、目的地规则、和已有内容比对、写入）→ 各目的地。
- **ingestion** 只指其中由 worker 完成的那一段。代码标识沿用 `ingestion_*`，比如表名、模块 `core/ingestion/`、CLI `ingest`。
- **Flow 视图** 指 Need You overlay 里的 dashboard（§10）。

## 0. 一句话

审计结论：三块写入问题的根源是同一个——**每个写入者都在提取内容的那一刻，自己判断、自己路由、自己写文件**。
所以准入可以被绕过、项目判断不出来、不同写入者之间会冲突，判官失败时还会卡住上游。

r13 改成**提取和 ingestion 解耦**：

```
各 source  ──只负责提取──▶  Inbox（data.db）  ──按各自的节奏──▶  Ingestion worker  ──▶  MEMORY / EVOLUTION / DDD / Library / 用户 context
（蒸馏、纠正、REFLECT、         候选、状态、审计        整理 → 答题卡 → 目的地规则         唯一写入者
 backflow、DailyActivity、     只保留 30 天           → 检索已有内容 → routing → 写入
 cultivation、skill、"记住"）                                    │
                                                                ▼
                                     Need You overlay 的 Flow 视图（CDF 的统一 dashboard）
```

- **source** 只做两件事：按自己定制的格式提取，然后入队。入队是一次插库操作，毫秒级完成，不会打断会话、run 或 chat。
- **Ingestion** 是唯一的写入者，在后台按自己的节奏和要求处理：整理、分类、打分、执行目的地规则、和已有内容比对、routing、写入。
- **Inbox** 只保留 30 天，更早的行先汇总成统计数据，再删除（§4）。
- **dashboard** 放在 Need You overlay 里：这条流程是认知系统的核心，不再另开一个页面（§10）。

r13 起取代 r12，r14 补上了"唯一写入者"的保证机制。r12 里大部分复杂机制，起因都是"多个写入者各自当场写"：准备→提交、租约、多写入者版本冲突、跨进程配额计数、受控写入的三层强制。解耦之后这些都不需要了。
r12 里经过评审、而且仍然成立的结论都保留下来：答题卡的维度、路由表、卡 3 的纯函数、来源与污染、已沉淀内容不动、注入预算。

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

## 3. 架构

| 角色 | 负责 | 不负责 |
|---|---|---|
| **source**（提取） | 从自己的上下文中提取候选，按自己的格式（§5）填写 payload 和稳定的幂等键，然后入队 | 判断值不值得写、该写到哪里、是否重复；不写任何受控文件 |
| **Inbox**（`ingestion_inbox` 等表） | 持久保存候选、单元、写入日志和处理状态，是唯一的事实来源，审计也只读这里 | 召回、索引 |
| **Ingestion worker** | 整理 → 卡 0/1 → 污染约束 → 卡 2 → 目的地规则 → 卡 3 → 写入 → 记录，按 lane 调度（§6） | 不阻塞任何上游，不占用 chat 正在等待的线程池 |
| **Writer**（worker 内部） | 唯一能写受控存储的代码：写入日志、加锁、版本检查、先归档后移除、原子写入、维护关系 | — |
| **Dashboard** | 在 Need You overlay 里展示 Cognitive Distillation Flow（§10） | 不提供审批按钮 |

### 3.1 唯一 owner：一个数据目录只有一个 worker

daemon 和 dev/subprocess 的 backend 可以同时运行（`main.py:1474-1482`：发现别的实例时，只是不写 `backend.json`，进程照样启动），而且默认共用同一个 app 数据目录（`config.py:47-67`）。所以"只有一个 worker"必须由机制来保证：

- **owner 锁**：worker 启动前，用 `utils.file_lock` 对 `<app data>/state/ingestion_owner.lock` 加非阻塞的独占锁，并在 worker 运行期间一直持有。锁跟着进程走，进程崩溃后操作系统会自动释放。
- **owner epoch**：拿到锁后，在 DB 的 `ingestion_owner` 表里把 epoch 加 1，并记下 pid 和启动时间。之后每一次状态推进都写成 `UPDATE … WHERE id=? AND state=? AND owner_epoch=?`，然后检查影响行数，不是 1 就放弃这一步。旧 owner 恢复过来时手里还是旧 epoch，它的写入不会生效。
- **拿不到锁的进程**：只能入队、读取 dashboard，不启动 worker。
- **只能由谁当 owner**：worker 只能在完整的 HTTP backend 的 FastAPI lifespan 里启动，也就是 daemon、subprocess、hive、dev 四种模式。`py`、`pipeline-v1`、`hook`、`ingest` 这些短命的子命令只能入队和读取。
- **接管**：owner 退出后锁自然释放，下一个启动的完整 backend 拿到锁后，先做恢复（§4.3），再开始处理。

### 3.2 并发边界

worker 内部是串行的。受控存储剩下的并发来源只有两个，各自的处理方式如下：

1. **应用内的用户编辑**：两个编辑器接口（`workspace_api.py:1758-1807`、`workspace.py:714-777`），以及 create / delete / trash / rename，修改受控路径时都改成走 `governed_edit` 适配层。读取时返回 `revision`，即内容的 SHA-256；保存时必须带上 `expected_revision`，对不上就返回 409。适配层和 writer 共用同一把语料锁和同一套写入日志。
2. **外部编辑器和其他程序**（不经过我们的锁）：只能做到尽力而为，文档如实写明这一点。writer 在锁内替换文件前，先保存当前文件的快照；`os.replace` 之后立刻重新读一次源文件的 mtime 和哈希，如果在比较版本和替换之间有外部写入，就用快照恢复用户的版本，并把这条候选标为 `DEFER user_race`。maintenance 每天把文件和"基线 + writer 写过的版本"做比对，发现的偏差记一行 `writer=unknown`（§8）。

"版本检查"在本文里一律指"在锁内比较 `expected_hash`"，不代表跨平台的原子 compare-and-swap。

受控路径的写函数只存在于 writer 和 `governed_edit` 里。静态检查（§8）保证其他生产代码不会写这些路径。PreToolUse 的 `governed_corpus_gate` 会拦截 agent 直接写这些路径，并提示改用 `ingest submit`；这道 gate 只起提醒作用，不是安全边界。

## 4. Inbox：存储、状态、恢复、清理

所有表都放在每个用户自己的 `data.db` 里。全新的库直接按 `SQLiteDatabase.SCHEMA` 建表；已有的库通过一次幂等的 `<16` 迁移加表，迁移成功后才把 `user_version` 改成 16。现在的版本号是 15（`sqlite.py:2103`）。

| 表 | 内容 |
|---|---|
| `ingestion_inbox`（候选） | `id`、`idem_key`（UNIQUE）、`source`、`source_ref`、`lane`、`payload`、`payload_schema`、`envelope`、`project_snapshot`、`state`、`attempts`、`next_attempt_at`、`error_class`、`error_fingerprint`、`owner_epoch`、`versions`、`created_at`（UTC） |
| `ingestion_units`（单元） | `unit_id = candidate_id + "." + 稳定槽位`、`claim_group`、每张卡的答案和理由、目的地规则的结果、去向、动作、`state`、`attempts` |
| `ingestion_writes`（写入日志） | 每个单元一行写入计划：目标文件、`expected_hash`、新单元的 `cand:<unit_id>`、要归档的旧单元、要写的关系、每一步的幂等键、`step`（`planned → file_applied → archived → relation_applied → done`） |
| `ingestion_lanes` | 每个 lane 的 `next_due_at`、`last_started_at`、`last_completed_at`（UTC） |
| `ingestion_owner` | `epoch`、`pid`、`started_at` |
| `ingestion_daily_stats` | 30 天之前的汇总数据（§4.4） |

### 4.1 入队和幂等

- 入队是一条短事务：`INSERT … ON CONFLICT(idem_key) DO NOTHING RETURNING id`。冲突时返回已有那一行的 id 和状态；如果已有那一行的内容哈希不同，就另外记一条 `id_collision`，并在 Need You 里提示。
- 每个 source 必须在契约里声明 `idem_key` 的算法，例如 `(source, event_id, schema 版本, 语义槽位)`。不能用数组下标做 key：一旦拆分数量变了，下标就会变。backflow 用 `message_id`，REFLECT 用 `(run_id, lesson 的哈希)`。
- 入队的 busy timeout 是 500 ms，失败时最多重试 2 次；还是失败，就给 source 返回 `ENQUEUE_BUSY`，由 source 决定下次再提取。这样入队最多拖慢 source 约 1.5 秒，chat 路径永远不会等它（§6.4）。

### 4.2 状态

**单元**的状态：`queued → judged → writing → written`，或者终止于以下几种：
- `rejected`：判断后决定不写。拿不准时也归入这里，原因码 `uncertain`。
- `deferred`：这次没能判断，基础设施类错误，按指数退避重试，最多 5 次。
- `failed_poison`：同一个确定性错误（`error_fingerprint` 相同）连续出现 3 次。
- `expired_unprocessed`：到 30 天还没处理完。

**候选**的状态是它所有单元状态的确定性聚合：
- 全部单元都到了终态，候选才算终态；
- 只要有一个单元是 `deferred`，候选就是 `partial_deferred`；
- 重试时只处理还没到终态的单元，已经写入的单元不会再写一次。

**错误边界**：
- payload 不符合 schema 或解析失败：`REJECT malformed_payload` / `unsupported_schema`，不重试；
- LLM、检索、DB busy、版本冲突：`DEFER`；
- 其余代码异常：记录 `error_fingerprint`，达到 3 次后转为 `failed_poison`，同时在 Need You 里出一条提示，附上样本和栈指纹。

一批候选里每个候选各用一个 savepoint，一个候选失败不会回滚其他候选。

### 4.3 写入日志和重启恢复

单个写入者也需要能在崩溃后恢复，因为一次写入会跨越 SQLite、受控文件、归档文件和关系表。

1. **先记录计划**：写文件之前，先把写入计划和 `step=planned` 提交进 `ingestion_writes`。
2. **每一步都可以重放**：
   - 写文件：先在锁内检查目标文件里有没有 `cand:<unit_id>`。有就说明这一步做过了，跳过；没有就检查 `expected_hash`，然后原子写入。
   - 归档：用"归档文件已存在，并且哈希一致"来判断是否已经完成。
   - 关系：按 `(subject, predicate, object)` upsert。

   每做完一步，就推进 `step`；全部完成后，单元标为 `written`。
3. **启动时恢复**：owner 拿到锁后，先扫描所有 `step != done` 的写入计划，按文件、归档和关系的实际状态继续往前做完，不会盲目重试。`expected_hash` 对不上，并且新单元也不在文件里，说明计划已经过时：放弃这份计划，单元改回 `deferred`。
4. 停留在 `judged` 状态的单元，没有写入计划，恢复时直接改回 `queued`。
5. **故障注入测试**：在每一个 fsync、rename、DB commit 的边界都杀一次进程，恢复之后的结果必须和没出故障时一致。

### 4.4 清理：只保留 30 天

- **规则**：`created_at` 早于"当前 UTC 日期减 30 天"那一天 00:00 的候选，连同它的单元和写入计划，整体删除。按整天切，所以同一天的行总是在同一次清理里处理。保留天数写在契约里，可以配置。
- **删除前先汇总**：把要删的行汇总进 `ingestion_daily_stats`，主键是（UTC 日期、source、lane、目的地、状态、原因码、规则版本摘要），空值统一记成 `-`；同时记下候选数、单元数、写入字节数、LLM 调用次数和 token 数。汇总用累加式 upsert（`count = count + excluded.count`），并且和删除放在同一个事务里。事务一旦回滚，就不会多算；成功删掉的行也不会再被统计第二次。
- **没处理完的候选也一样删**：到期时还没处理完的候选，在删除之前先把状态记为 `expired_unprocessed`，再计入汇总。它们并没有做完 5 次重试，dashboard 也不会这样显示。
- **清理一定会执行**：清理不调 LLM，也不受资源门控。它属于 maintenance lane 的保底时隙（§6.2），backend 启动后会第一个执行。
- **磁盘空间**：新建的库和 seed 在建第一张表之前设置 `PRAGMA auto_vacuum=INCREMENTAL`，每次清理后执行 `incremental_vacuum`。已有的库无法在不跑全量 `VACUUM` 的情况下切换这个设置，所以只依赖 SQLite 复用空闲页：库的大小会稳定在约 30 天数据的量，不会再缩小。这一点在验收里分别对新库、seed 复制出来的库和升级上来的库验证。
- **Inbox 只存提取结果**：source 自己的原始数据（会话 transcript、DailyActivity、run 记录）仍然归 source 自己管。Inbox 只存提取出来的内容和来源 id。
- 超过 30 天的数据，dashboard 只显示汇总统计（§10）。

**不进召回，也不进索引**：Inbox 是状态和审计记录，不是知识。

## 5. Source：各自定制提取格式，统一信封

每个 source 在 `store_contracts` 里登记一条：名字、lane、payload 格式、`idem_key` 算法（§4.1）、默认的去向提示。payload 格式在首版是静态的 Pydantic 判别联合类型，带版本号；首版不做插件化的注册机制，source 列表是已知的。
worker 用这个格式解析 payload；各 source 可以给出不同的提示，但最终怎么处理以答题卡为准。

| source | 现在的写入方式 | 提取格式（payload） | lane |
|---|---|---|---|
| 用户说"记住" / skill（`s_persist` 等） | Edit 直接写，或者 `locked_write` | `{text, user_intent?, target_hint?}` | `immediate` |
| 即时纠正（`runtime_hooks.py:300-447`） | 判官放行后写进 MEMORY | `{wrong, right, trigger}` | `immediate` |
| 会话结束蒸馏（`distillation_hook`） | 判官放行后写进 MEMORY 或 EVOLUTION | `{lessons: [{text, type_hint}]}` | `session` |
| DailyActivity 中的决策和纠正 → DDD（`context_health_hook.py:902-1041`） | 写 DDD，项目写死为 SwarmAI | `{kind: decision\|correction, text, why?}` | `session` |
| backflow（`knowledge_backflow_hook`） | 直接写 Note | `{title, body, message_id}` | `session` |
| `memory_extractor.py:351`、`evolution_optimizer.py:899` | 直接写 | `{text, type_hint}` / `{skill, change, result}` | `session` |
| REFLECT（`reflect_job.py:157-281`、`reflect_library.py:145-228`） | 直接调用 `apply_to_ddd`、`annotate_entry`，直接写 pipeline-lessons | `{lessons: [{statement, kind, relation_hint, evidence}], run_id, repo}` | `run` |
| cultivation：signal、learn、code-change 三路 | 生成提案，再由人工批准 | `{proposal: {doc_hint, section_hint, text, why}}` | `scheduled` |
| `ingest submit` CLI | — | 任选上面某个 source 的格式，必须声明 `source` | 按所声明的 source |

所有 source 共用同一个**信封**：`source_event_ids`（这条候选依据的是哪些输入事件）、`writer`、`project_snapshot`。由 `enqueue()` 统一补齐，source 自己不能填写信任级别（§9）。

**提取质量**：source 的提取可以粗一些，worker 的整理阶段会负责拆分、合并和去噪。
各 source 只需保证两点：第一，payload 符合自己登记的格式；第二，不把整段会话原样塞进 payload。payload 有长度上限，按字段截断：信封和 JSON 结构本身永不截断，每个 schema 声明哪些字段可以截、截到多长（按 UTF-8 计）。必填字段放不下时，不截断，直接 `REJECT too_large_incomplete`；比如一条决定类内容，它的 why 放不下，就整条拒绝，不把截断后的文本交给判官。

## 6. 触发策略：按时效性分 lane，不一律事件驱动

| lane | 需要多快生效 | 何时触发 | 每次处理多少 |
|---|---|---|---|
| `immediate` | 几秒内，用户在等结果 | 入队后唤醒 | 1 条 |
| `session` | 下一个会话开始前 | 会话的 hook 链跑完之后（§6.1），以及 backend 启动时 | 一个会话的全部候选，批内先合并去重 |
| `run` | run 结束后 | REFLECT 入队后唤醒，以及 backend 启动时 | 一个 run 的全部候选 |
| `scheduled` | 当天内 | 每 6 小时一次（`ingestion_lanes.next_due_at`） | 至多 50 条 |
| `maintenance` | 天级 | 每天一次 | 清理、恢复、带外修改比对、写入容量统计 |

### 6.1 触发点

- **会话结束**：在 `session_hooks` 的注册顺序里，最后加一个 `IngestionSessionKickHook`。它排在所有提取 hook 之后，所以执行时这个会话的提取已经全部入队。它负责封存这个会话的批次，然后唤醒 worker。`lifecycle_manager` 的 120 秒 idle 回调本身不当作批次已就绪的信号（`session_hooks.py:275-298` 只是把 hook 排进队列，真正执行要晚一些）。
- **唤醒 worker**：同一进程内用 `asyncio.Event`。跨进程时（CLI、hook 子进程），先读 `backend.json`，再调用 owner 的 loopback 接口 `POST /api/ingestion/wake`。这个接口只绑定 127.0.0.1，并且要求 backend 现有的本机 token；它不传任何数据，只是让 worker 去检查一次。调用失败也没关系，worker 每 60 秒还会轮询一次 `next_due_at` 和 `immediate` 队列。
- **backend 启动时**：按顺序执行：恢复（§4.3）→ 已逾期的 maintenance 清理 → 按优先级逐个处理积压的 lane。
- **四种运行模式都能跑**：上面这些触发点在四种完整的 HTTP backend 模式里都存在。daemon / hive 的调度器只是让 `scheduled` 和 `maintenance` 更准时，不是必需的。

### 6.2 调度：有优先级，也要公平

- **按权重轮转**：权重为 immediate 8、session 4、run 4、scheduled 2、maintenance 1。每个 lane 都有最长等待时间：session 和 run 是 30 分钟，scheduled 是 12 小时，maintenance 是 36 小时。超过这个时间，这个 lane 就插到队首。连续处理 immediate 不能超过 10 条，之后必须让给别的 lane。
- **maintenance 的保底时隙**：清理和恢复只做 DB 操作和文件 I/O，不调 LLM。它们不受资源门控限制，只在 chat 流式输出时让路，最长让 10 分钟。

### 6.3 负载感知

每批开始前先调用 `ingestion_admission()`。这个函数是 ingestion 读取负载状态的唯一入口，内部读两样东西：
- `resource_monitor` 的 `pressure_level`，取值为 `ok` / `warning` / `critical`；
- 一个新公开的 `session_unit.is_any_chat_streaming()`，它包装的是现在私有的 `_get_streaming_count`。

规则：
- `warning` / `critical`：只处理 `immediate` 和 maintenance 的保底部分。
- 有会话正在流式输出：除 `immediate` 外，所有需要调 LLM 的 lane 都等它结束。
- 每种"负载状态 × lane"的组合都有测试覆盖。

### 6.4 热路径

- 入队只执行一条短事务。任何 DB 事务都不会跨越 LLM 调用、检索、文件 I/O 或 vacuum，清理按每 500 行一块分批提交。
- 验收要实测入队延迟的 p50、p90、p99，场景是 chat 写入、CLI 提交、清理和 dashboard 读取同时发生。chat 的发送路径本身不入队：即时纠正和"记住"都在 hook 或工具调用里入队，不在 SSE 主循环里。

### 6.5 "记住"的反馈

agent 调用 `"$SWARM_BACKEND_BIN" ingest submit --source remember --wait 10`。这条命令由 `desktop_main` 新加的 `ingest` 分支处理，在导入 FastAPI 之前就分派出去，执行过程如下：

1. 入队；
2. 唤醒 owner；
3. 每 300 毫秒查一次这一行的状态。

10 秒内处理完，就输出写到了哪里，或者被拒的原因；超时则输出"已收到，结果见 Cognitive Distillation Flow"。CLI 进程自己从不处理候选。

## 7. Ingestion pipeline：整理 → 标准答题卡 → 目的地规则 → 和已有内容比对 → 写入

一批候选按下面的顺序处理，每一步的结果都写进 `ingestion_units`。

1. **整理**（批内处理，不调 LLM）：解析 payload，去掉会话 id、scratch 路径、PID、哈希这类噪音；批内内容完全相同的候选标为 `merged`。输出是若干 **claim-group**：一个结论，加上与它绑定的 why、how、依据和适用条件。拆分只在 claim-group 之间进行，同一个 group 内部的内容永远不拆开；没把握该怎么拆时，整条候选作为一个 group 交给卡 1。DailyActivity 这类 source 的 `{text, why}` 在提取时就是一个 group。
2. **卡 0：规则**（不调 LLM）：检查污染、注入扫描、噪音、长度、内容指纹完全相同、会话已删除、配额。
3. **卡 1：内容本身**：调 1 次 LLM，一批候选可以打包成一次调用。输出 7 种形状之一，外加 `content_form ∈ {rule, reference}`；还要回答质量题，rule 和 reference 各有一套。
4. **污染约束**（确定性，在卡 2 之前）：被污染的 `rule` 记 `REJECT tainted_rule`。被污染的 `reference` 只有 `Reference` 这一种形状是合法的；如果卡 1 给的是别的形状，记 `REJECT invalid_shape_for_trust`，不会改投到别处。这条约束由代码执行，不依赖 prompt。
5. **卡 2：路由表**（确定性）：每种形状只对应一个去向。写入前会解析目标段是否存在。
6. **目的地规则**（§7.2）：规则不通过时 `REJECT`，原因码标明是哪条规则。
7. **改投**（可选，最多 1 次）：卡 1 可以给出 `reroute_to`，目的地规则 D2 也可能判定去向不对，这两种情况会触发改投。改投后的去向必须在契约允许的"形状 → 形状"映射表里，例如 `UserSingle ↔ ProjectSingle`、`WorldSingle → WorldCrossGlobal`（还需要 `global_relevance` 为真）。改投后从第 5 步重新走一遍：路由、目的地规则、卡 3。第二次再需要改投，或者映射表里没有合法目标，就记 `REJECT destination_mismatch`。
8. **卡 3：和已有内容比对**：在目的地里检索。有命中时调 1 次 LLM，再由 `relation_for_hit` 和 `aggregate_hits` 两个纯函数算出动作。
9. **写入**：由 writer 按写入日志（§4.3）完成。新单元带 `cand:<unit_id>` 和类型；需要的话，先归档旧单元再移除；最后写关系。

第 7 步之后，`project` 由确定性规则决定，按下面的顺序取第一个能确定的：
1. source 给出的显式绑定；
2. source 所在的 repo 对应的 DDD；
3. 项目快照里唯一涉及的那个 repo。

三者都确定不了，就 `DEFER ambiguous_project`。卡 1 只能从项目快照里选项目，不能自己编造。

### 7.1 标准答题卡（沿用 r12，r14 修正了改投和"更新"两处）

#### 卡 0：规则（不调 LLM）

| 检查 | 不通过 |
|---|---|
| 来源信封（§9）里是"被污染"，并且候选是 rule | `REJECT tainted_rule`（被污染的规则一律不写，不会改投到别处） |
| `MemoryGuard` 扫描（附加检测，不是安全边界） | `REJECT unsafe` |
| 噪音规则：会话 id、scratch 路径、PID、提交哈希、"N 个测试通过"之类 | `REJECT noisy` |
| 长度：rule 超过 600 字；reference 超过契约上限 | `REJECT not_atomic` / `too_large` |
| 内容指纹和某个受控单元完全相同 | `REJECT already_exists` |
| 会话已被删除 | `REJECT session_gone` |
| 配额（§12） | `REJECT quota` |
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
- 层由 `layer_for(target_section, type)` 唯一决定（§7.3）。

#### 卡 3：和已有内容的关系

**检索语料**：一个专用的单元语料，范围是卡 2 选定的去向，加上每轮都会注入的 MEMORY、EVOLUTION、STEERING、USER、TOOLS、SOUL、AGENT；去向是 DDD 时，再加上该项目的另外三份文档。语料由**统一的单元解析器**生成（§7.3），用 `memory_index._bm25_scores` 打分。召回取三路结果的并集：内容指纹、标题或锚点、BM25 前 8 个。被取代的单元**不剔除**，它们的命中按 §7.3 转交给当前生效的那一条。

**检索结果带类型**：`OK_EMPTY` 直接新加，不调 LLM；`OK_HITS` 继续往下走；`UNAVAILABLE` 和 `STALE` 一律 `DEFER`。语料摘要由受控文件的单元哈希直接算出；只有当这个摘要和写入计划里的目标版本对不上时，才判为 `STALE`，下一次处理时重建语料。批内维护一个增量的内存索引：writer 每成功写入一条，就把它加进去，所以同一批里后面的候选能检索到刚写进去的内容。

**判官对每个命中输出**（不是只对最接近的一个）：

| 字段 | 取值 |
|---|---|
| `overlap` | `same` / `contained`（候选被旧单元包含）/ `contains`（候选包含旧单元）/ `related` / `unrelated` |
| `same_topic` | true / false：主题和适用条件都相同 |
| `claim_verdicts` | 候选的每条 claim 一个结论：`agrees` / `refines`（更细或更准）/ `refutes` / `independent`，并指出旧单元里对应的那一句 |
| `scope_relation` | `equal` / `narrower`（候选更窄）/ `wider` / `disjoint` |

**证据强弱**由来源信封决定（§9），是全序：`user_explicit` > `measured` > `review` > `run_lesson` > `inferred`。

**动作由两个纯函数算出**，判官只负责给出上面那张表：

`relation_for_hit(hit) →` 按顺序，命中第一条就停：
1. 有任何 `refutes`，并且 `scope_relation=narrower` → `exception`
2. 有任何 `refutes`，并且 `scope_relation ∈ {equal, wider}`：候选的证据更强，就 `replace`；证据一样强时，只有双方都有可信的 `observed_at`（证据事件发生的时间，不用文件 mtime，也不用入队时间），并且候选更新，才 `replace`；其他情况一律 `blocked`
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

改投不在这两个函数里处理，它是 pipeline 的第 7 步。

两个函数都要做全枚举的属性测试，包括命中顺序打乱后结果不变。

**替换**：
- 旧单元是 `cand:`：先归档再移除，写入新单元，加 `supersedes` 边。
- 旧单元是基线单元，分三步：
  1. **列出旧单元的全部主张**：第二次判官调用，为旧单元生成一份主张清单，每条带原文句子的位置。条数不设上限。
  2. **生成有效单元**：同一次调用里，对清单上每一条旧主张标明 `preserve` / `refute` / `refine`，然后写出新的有效单元。
  3. **校验**：每条 `preserve` 和 `refine` 的旧主张，在有效单元里都要能找到对应的句子；每条 `refute` 都要有候选里的一条 claim 支持。有效单元必须重新通过卡 1。任何一条旧主张没有对上，就 `REJECT conflict_unresolved`。
- 通过之后新加有效单元，加 `新 —supersedes→ 旧`。旧单元的文件不改。

**用户说"记住"**：同样过全部的卡，没有特权，只是证据强弱按来源信封走（§9）。结果通过 `ingest submit --wait` 回给 agent（§6）。

**已知局限**：没有向量库，换了说法或者跨中英文的重复，BM25 可能找不到。首版不做语义复审，这类漏网的重复先接受，并在 holdout 里单独计数（§14）。

### 7.2 目的地规则：标准答题卡之外的定制规则

每类目的地在 `store_contracts` 里有一组带版本的规则。规则跑在卡 2 之后、卡 3 之前，结果写进审计。

**DDD（最严格。DDD 的价值在于 what、how、why 三者放在一起）**
- **D1 结构完整**：决定类条目必须同时写清做了什么（what）、怎么做（how）、为什么（why），缺 why 就拒绝，原因码 `ddd_missing_why`。经验和坑类条目必须写清现象、原因、怎么避开。规则检查只看结构，判官同时检查内容是否真的回答了这三个问题。
- **D2 段落对位**：条目的语义要和目标段一致，比如 TECH 的 Conventions 段只放约定。不一致时，按 pipeline 第 7 步改投一次；没有合法的目标就 `REJECT destination_mismatch`。
- **D3 不拆散上下文**：条目必须能和它所在段的其他条目放在一起读懂。只有放进那一段才成立的补充，用"补充"动作挂到相关条目上，不新加一条孤立的条目。
- **D4 稳定性**：带具体行号、文件大小、测试数量这类会变数字的内容，必须写成"截至某版本"，否则 `REJECT ddd_volatile`。
- **D5 格式**：`- [type] **标题** 正文` 加元数据行，和 `parse_entries` 的解析格式一致。
- **D6 只写一个项目**：内容同时关系到两个项目时，按 §7 的确定性规则选出主项目，只写进主项目；另一个项目记在审计的 `secondary_projects` 里（首版不做跨项目边，§7.3）。
- **读取侧不改**：DDD 注入保持现在的 section 指针方式。教训 2026-10-26：只注入排名最高的几条条目，在盲评里 8 对中输了 7 对，因为丢掉了整段的上下文。

**MEMORY**
- **M1 全局相关**：`WorldCrossGlobal` 形状的 `global_relevance` 必须为真（卡 1）。
- **M2 一句话可执行**：正文不超过 600 字，必须包含触发条件和对应做法。
- **M3 写入容量**：每天新增不超过 4 KB，`cand:` 单元总量不超过 40 KB（§12）。
- **M4 不和每轮注入的产品文件重复**：卡 3 的检索范围包含 SOUL、AGENT、STEERING。

**EVOLUTION**
- **E1 主体是 agent**：内容必须是"我（agent）做错了什么、学会了什么"，不能是项目事实。
- **E2 格式**：使用 `### Cnnn` / `### Knnn` 块格式，编号由 writer 在锁里分配。

**用户 context 文件（STEERING / USER / TOOLS）**
- **C1 只写用户自己的文件**：目标必须是 `auto_write_eligible` 文件，路径落在用户的 live `.context/` 下，不能在 `templates_dir` 下，且没有被 git 跟踪（§11）。
- **C2 证据**：证据强度必须是 `user_explicit`，也就是依据的输入事件全部来自用户本人发出的消息。
- **C3 不覆盖用户的原话**：只追加；和已有内容冲突时 `REJECT conflicts_user_text`，由用户自己去改。

**Library（Notes / Library / Learned）**
- **L1 独立可读**：必须有标题，正文至少 200 字，离开原会话也能读懂。
- **L2 权威性**：已经有人写的同主题文档时，`REJECT superseded_by_human`。
- **L3 frontmatter**：写入 `source`、`ingestion_candidate`、`ontology_types`；内容被污染时加 `taint: external`。
- **L4 一条消息只产出一份**：backflow 用 `message_id` 做幂等键。

规则集是一个 Python 模块（`core/ingestion_rules/<destination>.py`）。每条规则是一个带版本号的纯函数，可以附带一个判官字段，每条规则都要有表驱动的单测。安全类的不变量还必须做 mutation 检查：污染约束、C1 路径、基线不变、写入幂等、owner epoch。新增一类目的地，只需要加一个模块和一张契约表。

### 7.3 单元、层、关系

- **统一的单元解析器** `parse_units(path) -> [Unit]`。每个 Unit 带这几个字段：`path`（工作区相对路径）、`section`、`byte_start`、`byte_end`、`identity`、`byte_hash`。
  - bullet 形式的单元（MEMORY、DDD）和 `###` 块形式的单元（EVOLUTION），都由它来解析。
  - 基线生成、卡 3 语料、MEMORY 组装、DDD 段语料和渲染，都只用这一个解析器。`context_directory_loader.py:256-302` 和 `recall_multi.py:100-220` 改为调用它。
  - `byte_hash` 是原始字节的哈希。`identity` 对 `cand:` 单元取 `cand:<unit_id>`，对基线单元取基线清单里记下的 `byte_hash`。
- **单元锚点**：`<path>#<section>#<identity>`。
- **层**：由 `layer_for(target_section, type)` 决定。所有合法组合列在一张表里，表里没有的组合直接拒绝。
- **关系**（首版只做最小的一套，不做通用图 v2）：
  - **存储**：放在 SQLite 的 `ingestion_relations` 表，字段为 `subject_anchor`、`predicate ∈ {supersedes, extends}`、`object_anchor`、`created`、`inactive`。主键是 `(subject, predicate, object)`，写入用 upsert。`.knowledge-graph.yaml` 和 `knowledge_graph.py` 现有的边不迁移，读取侧继续照旧使用它们。
  - **约束**（在锁内校验，不满足就 `REJECT incompatible_hits`）：
    - 不能有环；
    - 每个锚点最多有一个生效中的取代者，第二次取代同一个旧单元时，改为取代当前的链头；
    - 边的两端都必须能解析出单元；
    - 只有 writer 能写这张表。
  - **链头**：`active_head(anchor)` 沿着生效中的 `supersedes` 边一直走到终点。
    - 链头被归档（只会发生在 `cand:` 单元上）时，整个取代链标为"无生效头"。旧单元**不会**因此重新生效，读取侧对这条链的命中一律丢弃，并在审计里计数。
    - 链损坏（端点解析不出来）时，按"无生效头"处理，同时在 Need You 里出一条提示。
- **读取侧**：命中一个被取代的单元时，把分数和命中理由转给 `active_head`，同一个链头被命中多次时取最高分。被取代的单元本身不进入输出。所以查询只匹配旧措辞时，模型看到的是当前有效的那一条，不会同时看到新旧两种说法。
- **Library 文件**：frontmatter 里写 `ontology_types:`，可以为空。
- **跨项目**：首版不做跨项目边。一个单元只写进一个项目，另一个相关项目只记在审计的 `secondary_projects` 字段里。

## 8. 写入者迁移：source 只入队，worker 是唯一写入者

| 现在的写入路径 | r14 |
|---|---|
| 第 5 节表里所有 source 的直接写入 | 改成 `enqueue()`，原来的写入代码删掉 |
| `ingestion_gate()`、`admission_band()` | 并进 worker 的卡 1 和 DDD 规则，不再是公开入口 |
| cultivation 提案的人工批准接口 | 删掉。三路输入直接作为候选入队 |
| session 结束的 writeback（置信度 0.5 永远过不了 0.7） | 先看过去 30 天它丢弃了哪些候选，再决定是删掉还是改成一个 source |
| `s_memory-distill` | 改成手动触发入口：立即运行一次 `session` 和 `maintenance` lane，然后汇总这次审计 |
| 两个编辑器接口、create / delete / trash / rename、上传（`workspace_api.py:1758-1807`、`1876`、`1937-1939`、`2019`、`2076`；`workspace.py:692`、`714-777`） | 涉及受控路径时，一律改走 `governed_edit`（带 `expected_revision`，§3.2），记为 `writer=user_edit`，并修订基线（§13） |
| loops-health 的自动修复（`s_loops-health/scripts/loops_health_check.py:786`、`858`、`877`、`999`） | 只保留诊断，删除修改部分 |
| `artifact_cli ddd-retire --apply`（`artifact_cli.py:526-529`） | 删除 `--apply`，只保留报告 |
| 迁移脚本 `migrate_memory_lifecycle.py:185`、`migrate_cultivated_titles.py:135` | 已经跑完的一次性迁移，删除 |
| workspace 初始化、codebase scan 创建 DDD 文档（`swarm_workspace_manager.py:1264`、`1450`、`1920`） | 允许"文件不存在时才创建"，这类写入作为显式豁免；已经存在的文件不覆盖 |
| 每周 Memory Health 的修改阶段（`memory_health.py:584`、`796`、`867`、`1005`、`1056`）、EVOLUTION prune 和维护（`evolution_maintenance_hook.py:422`、`866`、`950`）、context health 里的修改（`context_health_hook.py:1278`、`1535`、`1597`、`1663`、`1847`、`3217`）、DDD 矛盾剥离、size valve 驱逐基线单元、`locked_write` 清理旧索引 | 这些都会改动已沉淀的内容，和 §13 冲突，**在第 1 步全部关成只读**，不再保留到第 3 步，只留下只读统计。size 控制改成只作用于 `cand:` 单元，并放进读取侧的注入预算（§12） |
| `ddd_orchestrator` 的机械替换、`ddd_maturity` 的成熟度注释 | 走 writer 的结构写函数，只能改 `cand:` 单元和段头注释 |
| DailyActivity、checkpoint、JobResults、Signals、Reports | 它们是日志，不进 Inbox。新的 checkpoint 写到恢复文件，不再写进 DailyActivity |

**写入者清单由代码扫描生成**：用 AST 和调用图扫描生产 Python 代码、随产品发布的 skill 脚本和 CLI 脚本，找出所有写受控路径的地方，而不是靠手工维护一份 grep 清单。清单只允许 writer、`governed_edit`、"不存在时才创建"这三类豁免，出现其他写入点测试就变红。测试夹具里保留上面每一个已知的写入点，用来证明扫描确实能把它们找出来。

**切换时的不变量**：只要有任何一个 source 开始用 Inbox，就不能再有任何非 writer 的自动写入在运行。所以第 1 步上线时，上表所有旧的修改路径都会被关闭。

**带外修改检测**：maintenance lane 每天把各受控文件的单元集合和"基线 + writer 写过的版本"做比对，对不上的部分在审计里记一行 `writer=unknown` 并附上 diff，同时在 Need You 里出现一条记录（§10）。

**迁移顺序**：一次改一个 source。改完后观察到这个 source 至少有 30 个候选、或者已经满 7 天（以先到者为准），确认以下几项再改下一个：重复率 < 2%，`deferred` 最老的不超过 24 小时，入队 p90 < 50 ms，抽查 20 条写入里错的不超过 1 条。不做影子模式：每个 source 改完就直接生效，旧的写入路径同时删掉。如果需要回滚，就退回到这个 source 改成入队之前的版本，不做双写。

## 9. 来源和污染（沿用 r12，入口改为 `enqueue()`）

**信任从哪里来**：后端没法知道一段文字是用户亲手打的，还是粘贴进来的。所以不声称能区分这两种情况。所有信任判断都基于"这段内容来自哪个输入事件"。

- **输入事件**：每次有内容进入会话，都记一条事件，带不可变的 `event_id` 和 `trust`。会话里所有事件 id 都记进会话的**输入账本**（`session_inputs` 表）。账本随会话持久化，压缩和恢复会话之后仍然保留，`HookContext` 和 run 记录都会带上它。

| 输入事件 | trust |
|---|---|
| 用户在 dashboard 或已绑定频道里**本人发出**的消息（已认证的发送者） | `user`。如果消息里有引用块、转发块、附件，或者有 fenced 代码块，这些部分单独记为 `external` 事件 |
| 白名单里的结构化测量器（pytest 的通过/失败计数、`/health` 字段、exit code），只取结构化字段 | `measured` |
| 其他所有工具或命令的输出、网页抓取、MCP 返回、读入的文件、他人发的频道消息 | `external`（工具和命令输出默认都算这一档，因为它们可能把外部文本原样打出来） |
| agent 自己产生的文本 | 不是输入事件，信任等级继承它所依据的输入 |

- **候选必须声明依据**：`enqueue()` 要求 source 给出这条候选所依据的 `source_event_ids`，并且只能引用这个会话账本里已有的 id。
  - **证据**：所依据的事件全部是 `user`，证据为 `user_explicit`；全部是 `user` 或 `measured`，证据为 `measured`；其余情况，证据按写入者类型取 `review` / `run_lesson` / `inferred`。
  - **污染**：所依据的事件里只要有一个 `external`，就是 `tainted`。
- **source 不给依据时**，按最坏情况处理：会话账本里有任何 `external` 事件，就算 `tainted`。蒸馏、backflow、REFLECT 这类自动写入者，如果拿不出逐条依据，就走这条兜底规则。会话里先读过网页、后来用户又亲口纠正过的情况：只要纠正那条候选的依据只指向用户那条消息，它就不算被污染。
- **转述不能降级**：agent 转述、REFLECT 总结、review expert 的结论，都继承原始输入的污染。review expert 和 pipeline run 也各自维护输入账本。
- **规则**：被污染的 rule 一律 `REJECT tainted_rule`（卡 0）；被污染的 reference 只能写进 Library，frontmatter 标 `taint: external`。从被污染的 Library 文件里再提炼出来的内容，依据会指向那个文件，所以仍然是被污染的。
- **召回**：被污染的 Library 内容放在不可信数据边界里渲染。
- `MemoryGuard` 只是附加检测，不是安全边界。

## 10. 统一 dashboard：扩展 Need You overlay

Need You 现在是"需要你处理"的统一入口：它从 `AttentionAuthority`（`GET /api/attention`）取数据，按 tier 加 brain 分组展示。
ingestion 是认知系统的核心流程，所以放在这里，不另开新页面。overlay 用共享的 `Tabs` 组件分成两页。

**第 1 页「需要你」**（现有内容，保持不变）。另外由 ingestion 往 `AttentionAuthority` 加一个新的 source `ingestion`，只在出现异常时产生条目：
- 某个 source 熔断；
- `deferred` 积压超过 24 小时；
- 出现 `id_collision`；
- worker 比对时发现绕过 writer 的带外修改（`writer=unknown`）；
- 判官或检索连续失败超过 1 小时。

每个条目附一句可以直接发进 chat 的 prompt（沿用现有的 dispatch 机制），例如"看下 ingestion 为什么积压"。
不会因为某个候选被拒就产生条目：被拒是正常结果，在第 2 页查看。

**第 2 页「Cognitive Distillation Flow」**（新增，即 Flow 视图）
- **流程总览**：source → Inbox → 整理 → 卡 0/1/2 → 目的地规则 → 卡 3 → 写入，每一段显示最近 24 小时、7 天、30 天的数量，按 lane 着色。点任意一段，下面的列表就按这一段筛选。
- **候选列表**：时间、source、lane、内容开头、状态、去向、原因码。可以按 source、目的地、状态、原因码、关键词、时间筛选，分页显示。只列最近 30 天的候选。
- **候选详情**：
  - 原始 payload，以及"打开原会话"链接；
  - 来源信封；
  - 整理阶段的拆分和合并；
  - 每个单元在卡 0–3 上每道题的答案和理由；
  - 目的地规则的结果；
  - 卡 3 的每个命中和最终算出的动作；
  - 写到了哪个文件的哪一条，可以点开；替换或跳过了哪一条，归档在哪里；
  - 写了哪些关系边。
- **口径**：除非另外说明，所有比率都按单元计算。写入率 = `written` ÷（单元总数 − `merged`）；拒绝率的分母和写入率相同；`deferred` 和 `failed_poison` 单独计数，不计入分母。
- **历史趋势**（30 天以前）：只读 `ingestion_daily_stats`，按周或按月显示每个 source 的入队量、写入率、拒绝的原因码分布、各目的地的写入量和 LLM 成本。不显示任何原始内容。
- **健康区**：各 lane 的积压和最老一条的等待时间、熔断记录、每天的 LLM 调用次数和 token、判官与关键词分类器不一致的次数、Inbox 大小和上次清理时间、MEMORY 与 EVOLUTION 的大小及注入时省略的单元数。

**第一版的范围**：只做审计闭环，包括两个 tab、候选列表和详情、Need You 里的 ingestion 异常、各 lane 积压、上次清理时间、按天的统计。流程节点之间的联动筛选、周和月的切换、预取，这些放到下一版。

**安全和上限**（正文规定，不只写在修订记录里）：
- 查询连接以 SQLite 的 `mode=ro` 打开，并设置 `query_only`。
- 只读当前实例自己的 `data.db`。
- 列表每页最多 50 条，时间窗最长 30 天；详情接口里的 payload 和理由字段，单个最多 16 KB。
- 写入 Inbox 之前，先用现有的密钥扫描规则做脱敏；返回给前端时再扫一遍。
- 所有文本都作为数据渲染（转义），从不返回存好的 HTML。
- "打开原会话"和"打开文件"会先校验路径确实在工作区根目录下。
- 每次访问详情都在审计里记一行。

**实现**：后端在 `attention_authority.collect()` 里加 `_collect_ingestion()`；新增只读接口 `GET /api/ingestion/flow`（总览）、`/api/ingestion/items`（列表）、`/api/ingestion/items/{id}`（详情）、`/api/ingestion/stats`（历史趋势，读汇总表）。四个接口都跑在 `executors.run_nav` 上，做同步、参数化的 SQLite 只读查询，并且只读当前实例自己的 `data.db`。
另外新增 `POST /api/ingestion/wake`：只绑定 loopback，要求本机 token，不带任何数据（§6.1）。前端改动：`NeedYouOverlay` 加 Tabs；新增 `CognitiveDistillationFlow` 视图；`AttentionEntry.source` 加 `ingestion`，`ATTENTION_SOURCE_LABEL` 加对应标签；每个 tab 用独立的 query key，徽标计数仍沿用现有的 attention 轮询；补中英文 i18n；遵守 Nav visual system，并通过 nav visual 测试和相等性测试。

**不提供审批按钮**：dashboard 只用来看。判断有误就去改契约、规则或 prompt，然后把版本号加一。可以对 Inbox 离线重放，验证修改的效果（§14）。

## 11. context 文件：写进用户的 `.context/`，不写源码仓库（沿用 r12）

| | 位置 | 归属 |
|---|---|---|
| 模板 | `backend/context/*.md`，即 `templates_dir`。dev 模式和 worktree 里它在源码仓库内，打包后在安装包内 | 产品 |
| 用户的 live 文件 | `<workspace>/.context/*.md` | 当前用户，prompt 读的是这一份 |

1. `ContextFileSpec` 新增 `auto_write_eligible`，只有 USER、STEERING、TOOLS 为真；不能从 `user_customized` 推出来。SWARMAI、IDENTITY、SOUL、AGENT 是产品文件，用户的规则一律写进 STEERING。
2. 候选里存工作区 id 和相对路径，执行时重新解析。绝对路径只作为审计里的快照。
3. 执行前在锁里校验：路径解析后必须正好在 live `.context/` 目录下，不在 `templates_dir` 下；工作区是 git 仓库时，目标文件必须是未跟踪或已忽略的；文件版本必须没变，变了就 `DEFER`。
4. 用户编辑走 `governed_edit`（§3.2）：带 `expected_revision` → 加锁 → 在锁里比较版本 → 记写入日志 → 归档整份旧文件 → 安全扫描 → 按文件名做格式校验 → 原子替换 → 保持 0o644 权限 → 触发基线修订（§13）。系统写入走 writer 的追加式插入，再经过同样的校验。
5. 受控模块没有写 `templates_dir` 的能力。

## 12. 读取侧和容量

- **每轮注入文件的确定性预算**（本次改变 AGENTS.md 里"读取侧永不截断"的规则）：
  - MEMORY、EVOLUTION 各有一个注入上限，默认 MEMORY 24K tokens、EVOLUTION 8K tokens，可配置。源文件一个字节都不改。
  - 文件超过上限时，按单元选取，规则固定：
    1. 先剔除被取代的单元。
    2. 元认知层整层保留。
    3. 认知层和操作层各自按本轮查询的 BM25 分数排序，再按单元在文件里的顺序排序，依次装入，装满为止。
    4. 输出时按单元原来的顺序拼接。
  - 没装进去的单元仍然能被检索到：recall 的 `context_files` 那一路（读取侧设计 `2026-10-26-recall-precision-and-coverage.md` D4：只检索 MEMORY 里不在 prompt 中的条目）照常能召回它们。
  - STEERING、USER、TOOLS 由用户自己管，不设上限，只在超过 8K tokens 时在 Flow 视图里提示。
  - `over_budget` 警告保留，额外记录被省略的单元数，显示在Flow 视图的健康区。
  - 实施时同步改 AGENTS.md 的 Context and Memory System 一节和 `context_directory_loader` 的文档，L1 缓存的预算头也跟着改。
- **DDD 注入不改**：仍然注入 section 指针，带上整段的 what、how、why。r12 计划改成只注入几条正文，2026-10-26 的成对盲评里 8 对输了 7 对，已经撤回。唯一的改动是：被 supersedes 的条目，命中时转交给它的链头（§7.3）。
- **Library 过滤不做**：多取后补足、过滤 frontmatter 和短正文，这几项在盲评里和基线持平，所以没有合并。等写入侧干净之后再评估。被污染的 Library 内容放进不可信数据边界里渲染。
- **写入容量**：每个每轮注入的文件，`cand:` 单元每天新增不超过 4 KB、总量不超过 40 KB；每个项目的 DDD 每天新增不超过 20 KB；Library 每天新增不超过 50 个文件或 5 MB。超出时 `REJECT quota`。配额按 UTC 日计算，计量的是最终新增的 UTF-8 字节数：replace 只算新单元的字节，归档不算。卡 0 先预留配额，writer 写成功后再正式扣减；重试时用 `unit_id` 防止重复扣减。
- **熔断**：同一个 source 1 小时内至少处理 20 条，且基础设施类的 `DEFER` 超过 50%，就暂停这个 source 1 小时，同时在 Need You 里出一条提示。内容被拒绝不计入熔断。
- **索引排除依赖目录**：`node_modules`、`.venv`、`dist`、`build`。
- 不重切 chunk，不重建索引。

## 13. 已经沉淀的内容：系统不动，用户可以改（沿用 r12）

- **基线**：上线后 backend 第一次启动时，对每个受控文件跑一遍 `parse_units()`，把每个单元的锚点和 `byte_hash` 记进 `baseline.json`。这一步只读不写。
- **系统不动**：基线单元不会被任何系统路径修改、移动、删除或打标记。这包括新功能、所有维护机制（§8 的退役清单），以及插入原语，后者只往段末追加字节。
- **用户可以改**：用户通过编辑器或上传修改受控文件时，`governed_edit` 按写入日志（§4.3）依次完成下面三件事，崩溃后可以接着做完：
  1. 对修改后的文件重跑 `parse_units()`；
  2. 更新 `baseline.json` 里这个文件的单元表。被删的单元移出基线；被改过的单元换成新的 `byte_hash`；新出现、但没有 `cand:` 的单元也算作基线，因为它们是用户亲手写的；
  3. 关系图里指向已消失单元的边设为失效。

  这次修订在审计里记一行 `writer=user_edit`，附 diff。
- **带外修改**：绕过适配器的改动，由 maintenance 的比对检测出来，记作 `writer=unknown`（§8），只作为一次观察结果记录下来。基线**不会**因此自动修订，包括启动时也不会，否则来历不明的内容会被静默认定为用户写的。只有用户下一次在编辑器里保存这个文件时，基线才会随之修订。
- **基线文件本身**：`baseline.json` 带版本号，用原子写入。损坏时从上一份备份恢复，并在 Need You 里出一条提示。文件被重命名时，基线按内容哈希重新对应到新文件。
- **新旧共存**：卡 3 检索到的基线单元只用于判断。补充时新加一条，加 `extends` 边；替换时新加一个完整的有效单元，加 `supersedes` 边，读取侧把命中转发给链头（§7.1、§7.3）。
- **不在不变范围内的**：DailyActivity、会话、Reports、Signals、checkpoint、派生索引。它们是持续追加或会被 upsert 的日志和派生数据，不属于基线。验收时只检查切换前已有的记录仍然存在、内容不变，粒度到记录或块。
- **预算**：MEMORY 不再按大小驱逐基线单元，文件会变大，但每轮注入有确定性的上限（§12），所以 prompt 大小有界。`cand:` 单元的增长另有配额（§12）。

### 13.1 对每个用户都可用，不依赖这台机器

- **路径**：只从 `config`、`get_workspace_path()`、app 数据目录取，不写死 `~/.swarm-ai`、仓库路径或项目名。候选里存的是工作区相对路径。项目归属只看用户自己的 DDD 和 `bindings.yaml`。
- **平台**：文件锁一律用 `utils.file_lock`，`knowledge_graph.py` 也一样。原子写和 fsync 用现有的跨平台工具。
- **运行模式**：daemon、subprocess、hive、dev 四种模式下都能触发全部 lane（§6）。
- **打包**：打包后 `templates_dir` 在安装包里，§11 的校验照样成立。不调用 `sys.executable -m`；`ingest` 走 `desktop_main.py` 的分发。
- **不依赖开发者自己的东西**：运行时不用 `Projects/SwarmAI` 里的 skill，不用回放或盲评脚本。
- **工作区不是 git 仓库**：跳过 §11 第 3 条里的 git 检查，只检查目录归属。

## 14. 实施和验收

每一步都在 worktree 里做，ff-merge 成一个 commit；不 push，也不 rebuild。

**第 0 步：go/no-go（离线，不改生产代码）**
1. 实现 worker 的纯逻辑：整理、卡 0、卡 1 的 prompt、污染约束、卡 2、目的地规则、改投、卡 3 的检索和两个纯函数。全部放在 `core/ingestion/`，不接任何 source，也不写任何受控文件。
2. 实现离线工具 `scripts/ingestion_replay.py`。它把候选灌进一个**临时** Inbox，在只读的语料副本上运行，输出每个单元的决定。回放有两种模式：
   - **A，录制模式**：卡 1 和卡 3 的结构化输出用事先录好的，只测卡 2、规则、改投和聚合这些部分，结果必须完全确定。
   - **B，在线模式**：固定模型 id、prompt 哈希、temperature 0 和批内顺序，同一份数据跑 3 次，同时报告结果和三次之间的一致率。
3. **数据分成两份，彼此不重叠**：
   - **dev**：审计里用过的样本（MEMORY 97、DDD 40、backflow 15）加上合成夹具。prompt 和规则只能在这一份上调。
   - **holdout**：从会话历史、DDD 文档、backflow Note 里另外抽样，排除 dev 里已有的条目，用种子固定的随机抽样。由 3 个互不知道彼此结果的盲评 subagent 独立标注，标注规则和卡 1、卡 3、DDD 规则的定义一致；按多数票定标签，三票都不一样的样本丢掉，并记下数量。holdout 冻结后带上版本号，不能拿来调参，只在判定 go/no-go 时跑一次。
   - **holdout 的最小样本量**：各项门槛都用 95% 单侧 Clopper–Pearson 下界来判，分母和需要的样本量如下表。抽样时，按审计看到的分布反推需要抽多少条，直到每个分母都够数为止。

     | 指标 | 单位 | 分母 | 门槛（下界） | 最少分母 | 至少要对几个 |
     |---|---|---|---|---|---|
     | 写入精确率 | 单元 | 判为写入的单元 | ≥ 93% | 210 | 202 |
     | 该写的召回率 | 单元 | 标签为该写的单元 | ≥ 75% | 180 | 145 |
     | 项目专属内容送对 DDD | 单元 | 标签为项目专属的单元 | ≥ 80% | 150 | 129 |
     | D1 拦住缺 why 的条目 | 单元 | 标签为缺 why 的决定类条目 | ≥ 90% | 130 | 123 |
     | 已知重复判为"不变" | 对 | 标签为重复的对 | ≥ 90% | 90 | 86 |
     | 被污染的 rule 进入每轮注入文件 | 单元 | 被污染的 rule | = 0 | 30（合成） | 30 |

     最少分母的算法：假设真实准确率比门槛高 4 到 8 个点（精确率按 97%，召回率按 83%，路由按 88%，D1 按 96%，重复按 97%），这时通过的概率至少要有 80%。计算脚本放在 `scripts/ingestion_holdout_size.py`。作为对照，如果样本全部判对，证明 93% 最少需要 42 个，证明 90% 最少需要 29 个。现在的 backflow 样本只有 15 篇，即使全对，下界也只有约 82%。缺 why 和重复这两类真实样本可能凑不够，凑不够时可以用改写真实条目的方法补足，但合成样本不能超过分母的一半，并且要单独报告。

   - **人工复核**：盲评给出的标签里，Gawan 可以随机抽 20 条复核。复核和多数票的一致率低于 85% 时，这份 holdout 作废，重新标注。
4. **通过的条件**：每项指标都达到门槛，并且在线模式下三次运行的一致率不低于 95%。报告里列出每项的分子、分母、点估计和下界，被判为 `DEFER` 或 `uncertain` 的单元单独计数。**没通过**时，只回到 dev 上改 prompt 和规则，然后用一份新版本的 holdout 再判一次，不进入第 1 步。

**第 1 步：Inbox、worker、writer、dashboard，加第一个 source**
5. v16 迁移（§4 的全部表，加上 `session_inputs`）、owner 锁和 epoch（§3.1）、`enqueue()`、`ingest` CLI 和唤醒接口、lane 调度、公平性、负载感知、清理、写入日志和恢复（§4.3）、writer、`governed_edit` 和两个编辑器接口的 revision。**同时把 §8 表里所有旧的修改路径关成只读**。
6. Need You 的 Cognitive Distillation Flow 页（第一版范围），以及 `AttentionAuthority` 的 `ingestion` source。
7. 第一个 source：backflow。它当前质量最差（15 条里 9 条只在原会话里有意义），而且只写 Library，风险最小。
- 验收：
  - 一天的真实流量里，backflow 每个候选在 Flow 视图里都能看到完整的处理过程；
  - 同一条消息只产出一份；
  - 被拒的原因码合理；
  - 在 chat、CLI、清理、dashboard 同时运行时，实测入队延迟 p99 < 200 ms；chat 首 token 的 p90 和基线相比，差值在误差范围内；
  - 故障注入：在每个写入步骤的边界杀进程，恢复后结果一致；两个完整 backend 同时启动，只有一个 worker 在跑。

**第 2 步：依次迁移其余 source**
顺序：REFLECT → DailyActivity 决策和纠正 → 会话结束蒸馏 → 即时纠正和"记住" → cultivation → 其余。每迁移一个，就删掉它原来的写入路径，在 Flow 视图里观察一天再继续。
- 每个 source 的验收：它的候选在 Flow 视图里能完整走完；它原来的写入路径已经删除（静态测试会因为直接写入而变红）；没有出现带外修改。

**第 3 步：注入预算和最终验收**（旧维护路径已经在第 1 步关掉）
- 验收：
  - 只含基线内容的夹具跑完所有 lane 后，基线单元的字节哈希完全不变；
  - MEMORY 超出预算时，选取结果是确定的；
  - 元认知层完整保留。

**所有步骤都要满足**
- 安全类的不变量（§7.2 末尾）都做 mutation 检查：撤掉修复，对应的测试必须变红。其余规则用表驱动测试和 Hypothesis 属性测试；
- `swarm_select_tests.py` 选出的测试全部通过；
- Windows CI 通过；
- 四种完整的 HTTP backend 模式下，lane 都能被触发；CLI 和 hook 子进程从不处理候选；
- Inbox 只保留 30 天。删掉的行在汇总表里的计数和删除前一致；连续两次跨越午夜的清理、以及事务回滚，都不会多算或漏算；新库、seed 复制的库、升级上来的库，都检查一遍 `PRAGMA auto_vacuum` 的值。

上线后：每次改 prompt 或规则，都要（1）用 holdout 的当前版本跑一次在线模式，各项门槛不能退化；（2）对 Inbox 里现有的 30 天真实数据跑一次录制模式，列出决定前后的差异。30 天内某个 source 的样本太少时，以 holdout 为准。30 天以前的数据只剩统计数字，不能回放。

## 15. 不做

- **不做人工审批和提案**：答题卡就是最终决定，人通过审计来看结果。
- **不让 source 自己判断或写入**：source 只负责提取。
- **不做影子模式和切换日**：每个 source 迁移时直接生效。
- **不做 OS 层的写入隔离**：用"唯一写入者 + 提示性的 gate + 带外修改检测"代替。
- **不引入向量库**：换了说法的重复只靠卡 3 的 BM25，漏掉的先接受，并在 holdout 里计数。
- **首版不做 maintenance 语义复审**：maintenance 只做清理、恢复、带外修改检测和统计。
- **首版不做通用关系图 v2 和跨项目边**，也不做 `classify_entry_type` 对照，不做插件化的 source 注册。
- **不改 DDD 的注入方式。**
- **系统不整理已经沉淀的内容。**
- **不重切 chunk，不重建索引。**

## 16. 每个事实在哪、谁写谁读

| 事实 | 唯一来源 | 写 | 读 |
|---|---|---|---|
| source 的登记信息（lane、payload 格式、提示） | `store_contracts` | 人改 | `enqueue()`、worker |
| 标准答题卡、路由表、DDD 段矩阵、配额、注入预算、Inbox 保留期 | `store_contracts` | 人改 | worker、读取侧 |
| 目的地规则 | `core/ingestion_rules/*.py` | 人改 | worker |
| 候选的原始内容、状态、每张卡的结果、去向、理由（最近 30 天） | `ingestion_inbox` | `enqueue()`、worker | worker、dashboard |
| 30 天以前的统计 | `ingestion_daily_stats` | maintenance lane，在删除前汇总 | dashboard 的历史趋势 |
| 输入事件和它的 trust | `session_inputs` | 消息入口、工具结果入口 | `enqueue()`、dashboard |
| 每个 lane 的到期时间 | `ingestion_lanes` 表 | worker | 触发点、调度 |
| 谁是 worker | owner 锁 + `ingestion_owner.epoch` | 完整的 backend 启动时 | 每一次状态推进 |
| 写入进行到哪一步 | `ingestion_writes` | writer、`governed_edit` | 启动时的恢复 |
| supersedes / extends 关系 | `ingestion_relations` | writer | 读取侧、卡 3 |
| 基线 | `<app data>/state/baseline.json` | 上线时生成一次，用户编辑时修订 | writer、比对、验收 |
| 单元、层 | `parse_units()`、`layer_for()` | — | writer、读取侧、dashboard |
| 需要人看的异常 | `AttentionAuthority` 的 `ingestion` source | 根据 Inbox 计算 | Need You |

**热路径**：source 一侧只多一次短事务插库（busy timeout 500 ms，§4.1），或一次 `session_inputs` 写入，都不在 chat 的等待池上执行；延迟靠实测（§6.4），不靠推断。worker 是后台任务，chat 流式输出期间会让路。
读取侧只有两处改动要实测：注入预算的选取、关系图的链头转发。

## 17. 已确认

1. 全自动：答题卡就是最终决定，人看审计（r6）。
2. 已经沉淀的内容系统不动（r7）。用户自己编辑时，修订基线（r12）。
3. 每轮注入的文件有确定性的预算，源文件不变（r12）。
4. 提取和 ingestion 解耦。Inbox 只保留 30 天，更早的只留统计。目的地有定制规则，source 有定制格式。dashboard 放在 Need You 里，叫 Cognitive Distillation Flow（Flow 视图）。按时效性分 lane 触发（r13）。
5. DDD 的注入方式不改；所有 DDD 规则都以保住 what、how、why 为前提（r13）。
6. 先做第 0 步（离线 go/no-go），通过之后再接入真实的 source。
7. holdout 由 3 个盲评 subagent 独立标注，按多数票定标签；Gawan 可以随机复核 20 条（r14）。
8. 首版的删减：最小关系表、不做语义复审、mutation 检查只覆盖安全类不变量、dashboard 只做审计闭环、不做类型对照、source 用静态类型注册（r14）。
9. 整个设计定名为 **Swarm Cognitive Distillation Flow**：它是 SwarmAI 认知系统的核心引擎，也是对所有用户可用的产品级能力（r14.1）。

## 18. 修订记录（非规范，仅供追溯，不按这里的旧说法实现）

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

- **r14.1**（Gawan）：整个设计定名为 Swarm Cognitive Distillation Flow（CDF），定位为 SwarmAI 认知系统的核心引擎、产品级能力；文件从 `2026-10-26-recall-corpus-ingestion.md` 改名为 `2026-10-26-swarm-cognitive-distillation-flow.md`；明确了 CDF、ingestion、Flow 视图三个术语的边界。内容不变。
- **r14：吸收 r13b 的两份独立评审**（`review_r13b_code.md`、`review_r13b_design.md`）。新增：唯一 owner 锁和 epoch；写入日志和重启恢复（单写入者也需要）；`governed_edit` 和编辑器 revision；外部编辑只做到尽力而为（前后快照，检测到冲突就恢复用户版本）；`ingest` CLI 分支和 loopback 唤醒；idle 触发移到 hook 链的最后；公平调度和 maintenance 保底时隙；负载状态沿用代码的 `warning`；单元级状态、`failed_poison`、稳定的 `idem_key` 和 UNIQUE 约束；清理按整天切、累加汇总；`auto_vacuum` 只对新库生效；整理阶段改为 claim-group，DDD 的 why 不会被拆开；改投从 `aggregate_hits` 里移出，作为独立的一步；污染约束在卡 2 之前硬性执行；"更新"只认证据事件时间；写入者清单改为扫描生成，补上 6 处遗漏，旧修改路径在第 1 步全部关闭；基线不会因为 unknown 修改自动修订；审计接口的隔离、脱敏和上限写进正文；第 0 步改为 dev / holdout 分离，按 95% 单侧 Clopper–Pearson 预先定好分母和门槛，holdout 由盲评 subagent 标注。删减：最小关系表代替图 v2，不做语义复审，不做跨项目边。
- **r13b**（Gawan）：Inbox 清理简化为只保留 30 天，删除前汇总进 `ingestion_daily_stats`；dashboard 对超过 30 天的数据只显示统计；dashboard 定名为 Cognitive Distillation Flow（认知蒸馏流）。
- **r13：提取与 ingestion 解耦**（Gawan 提出）。source 只负责提取和入队，Inbox 有保留期并定期清理，ingestion worker 是唯一写入者；标准答题卡之外，每类目的地有定制规则（DDD 必须保住 what、how、why）；source 可以有定制的提取格式；按时效性分 lane 触发；统一 dashboard 放在 Need You overlay（「认知蒸馏流」页，加上 `AttentionAuthority` 的 ingestion 异常）。r12 里为多写入者并发设计的准备→提交、租约、跨进程配额、三层强制全部删除。DDD 注入方式和 Library 过滤不改（依据第 1 步的盲评结果）。新增第 0 步：先做离线 go/no-go。

## 19. 验收结果

（实施后填写。）
