---
title: Swarm Cognitive Distillation Flow — SwarmAI 认知系统的核心引擎：提取与 ingestion 解耦，Memory、DDD、Knowledge 统一的 Inbox、答题卡、写入与 Flow 视图
created: 2026-10-26
updated: 2026-10-27
project: SwarmAI
status: r16 — 设计（source 与提取重写，待 Gawan 审），已吸收 r14.2 两份独立评审；执行计划三阶段；r15.2 补入阶段 A 的实测发现（修订记录见 §18）
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
各 source  ──只负责提取──▶  Inbox（ingestion.db）──按各自的节奏──▶  Ingestion worker  ──▶  MEMORY / EVOLUTION / DDD / Library / 用户 context
（蒸馏、纠正、REFLECT、         候选、状态、审计        整理 → 答题卡 → 目的地规则         唯一写入者
 backflow、DailyActivity、     只保留 30 天           → 检索已有内容 → routing → 写入
 cultivation、skill、"记住"）                                    │
                                                                ▼
                                     Need You overlay 的 Flow 视图（CDF 的统一 dashboard）
```

- **source** 只做两件事：按自己定制的格式提取，然后入队。入队是一次插库操作，毫秒级完成，不会打断会话、run 或 chat。
- **Ingestion** worker 是唯一的自主写入者（用户编辑走 `governed_edit`，两者共用同一个落盘接口，§3.2），在后台按自己的节奏和要求处理：整理、分类、打分、执行目的地规则、和已有内容比对、routing、写入。
- **Inbox** 只保留 30 天，更早的行先汇总成统计数据，再删除（§4）。
- **dashboard** 放在 Need You overlay 里：这条流程是认知系统的核心，不再另开一个页面（§10）。

r13 起取代 r12。r14 和 r15 补上了"唯一写入者"的保证机制和恢复协议。r12 里大部分复杂机制，起因都是"多个写入者各自当场写"：准备→提交、租约、多写入者之间的版本冲突、多 worker 的分布式配额计数、受控写入的三层强制。解耦之后这些都不需要了。配额只保留 DB 里的持久计量（§12）。
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

### 3.1 唯一 owner：一个数据目录只有一个自主写入者

daemon 和 dev/subprocess 两种 backend 可以同时运行（见 `main.py:1474-1482`：检测到已有实例时，只是不写 `backend.json`，进程照常启动），并且默认使用同一个 app 数据目录（`config.py:47-67`）。所以"只有一个 worker"不能靠约定，必须由机制来保证：

- **owner 锁**：worker 启动前，用 `utils.file_lock` 对 `<app data>/state/ingestion_owner.lock` 加非阻塞的独占锁，worker 存活期间一直持有。进程崩溃时，操作系统会释放这把锁。
- **owner epoch**：拿到锁之后，把 `ingestion_owner.epoch` 加 1，同时记下 pid 和启动时间。之后每一次状态推进都写成 `UPDATE … WHERE id=? AND state=? AND owner_epoch=?`，并检查影响行数；影响行数不是 1，就放弃这一步。
- **文件写入也受 epoch 约束**：writer 每做一步文件操作（归档、替换、写基线），都在语料锁内先执行一次 `UPDATE ingestion_writes SET step_owner=? WHERE id=? AND step=? AND owner_epoch=(当前 epoch)`，影响行数为 1 才执行这一步，否则立刻退出。这样，一个已经被新 epoch 取代的旧 owner，即使手里有算好的输出，也执行不了 `os.replace`。
- **释放锁之前先停稳**：正常退出时，先停止接新任务，等 worker 任务、writer 和它用到的 executor 全部 join 完，再释放 owner 锁。新 owner 拿到锁之前，不做恢复。
- **接管遗留任务**：新 owner 拿到锁后，在一个事务里把所有还没结束的单元和写入计划的 `owner_epoch` 改成自己的 epoch，然后才开始恢复（§4.3）。新入队的行 `owner_epoch` 为 NULL，worker 领取时再写成当前 epoch。
- **拿不到锁的进程**：只能入队、读 dashboard，不启动 worker。
- **谁能当 owner**：只有完整的 HTTP backend（daemon、subprocess、hive、dev 四种模式）能在 FastAPI lifespan 里启动 worker。`py`、`pipeline-v1`、`hook`、`ingest` 这些短命子命令只能入队和读取。
- 故障注入测试要覆盖这个场景：writer 卡在"比较版本"和"替换文件"之间时，发生 shutdown 或 owner 交接。

### 3.2 并发边界和 materializer

**术语**：
- **唯一的自主写入者**是 ingestion worker。
- **唯一的落盘接口**是 `core/corpus_materializer.py`，writer 和 `governed_edit` 共用它。

受控语料的所有文件操作（写、追加、替换、归档、删除、移动、复制）都只能经过 materializer。materializer 在运行时判断路径是否受控：只要解析后的真实路径落在受控根目录下，就必须带上写入计划或编辑操作的 id，否则拒绝执行。

worker 内部是串行处理的。除了 worker 之外，受控语料只剩两类并发来源：

1. **应用内的用户编辑**。下面这些接口修改受控路径时，一律走 `governed_edit`：
   - 两个编辑器接口（`workspace_api.py:1759-1807`、`workspace.py:715-777`）；
   - 文件的 create、delete、trash、rename 和上传；
   - Library 的清理和 Inbox 复制；
   - 项目的 rename 和 delete（§8）。

   读取时返回 `revision`（内容的 SHA-256），保存时必须带上 `expected_revision`，不一致就返回 409。用户编辑和 worker 共用同一把语料锁、同一套写入日志（§4.3）。

   首版**不允许对包含受控文件的目录做目录级的 delete、trash、rename**，返回 409 并提示逐个文件操作。项目 rename 和 delete 是例外，走专门的树级操作：先把目录下所有受控文件的清单（相对路径、哈希）记进写入日志，再执行移动，崩溃后按日志继续做完。
2. **外部编辑器和其他程序**（不经过我们的锁）只能做到尽力而为，下面这段时间窗口在文档里明确列为已知限制：
   - writer 在锁内先保存原文件和预期输出，替换前紧接着再比一次 `expected_hash`，然后执行 `os.replace`。
   - 替换后立刻重读文件。内容等于预期输出，才算成功。不等于预期输出，说明这期间有外部写入：**保留磁盘上的现状，不用快照回写**，候选记为 `DEFER user_race`，原文件和预期输出都留作恢复副本，在 Need You 里提示。
   - 外部写入如果正好落在"最后一次比较版本"和 `os.replace` 之间，会被覆盖，而且这种情况检测不出来。只有从原文件备份里才能找回内容。需要强保证的编辑必须走 `governed_edit`。
   - maintenance 每天把文件内容和"基线 + 已写入的版本"做一次比对，对不上的部分记一行 `writer=unknown`（§8）。

本文所说的"版本检查"，指的都是在锁内比较 `expected_hash`，不是跨平台的原子 compare-and-swap。

**静态检查**（§8）的作用是保证 materializer 以外的生产代码不直接调用写文件的函数，包括 `write_text`、`open(…, "w")`、`os.replace`、`shutil.*`、`unlink`、`rename`。路径是否受控由 materializer 在运行时判断，静态检查只负责拦住绕过它的调用。PreToolUse 的 `governed_corpus_gate` 拦截 agent 直接写受控路径，并提示改用 `ingest submit`；这道 gate 只起提醒作用，不是安全边界。

## 4. Inbox：存储、状态、恢复、清理

CDF 的所有表放在一个独立的 SQLite 文件里：`<app data>/ingestion.db`。每个用户一份，路径从 app 数据目录取，不写死。chat 用的 `data.db` 里不放这些表。

**为什么单独建库**：
- SQLite 同一时刻只允许一个写事务。CDF 用自己的库，入队、清理和恢复就不会和 chat 写消息抢同一把锁，也不会受 `data.db` 的迁移、备份和体量拖累（本机实测 `data.db` 有 1.5 GB，过去 24 小时写入 7,307 条消息）。
- 新库从建表起就能开启 `auto_vacuum=INCREMENTAL`。
- 迁移版本号自己管理，和 `data.db` 互不影响。
- 不需要跨库 join。和消息绑定的 `session_inputs` 仍放在 `data.db`（§9），CDF 只按事件 id 去只读查询。SQLite 不是瓶颈，基准数据见附录 A。

**建库**：
1. 打开空文件，此时还没有开启任何事务。依次设置 `PRAGMA auto_vacuum=INCREMENTAL`、`PRAGMA journal_mode=WAL`、`PRAGMA synchronous=NORMAL`，每设一项都读回来确认生效。
2. 在一个显式事务里建好全部表和索引，并把 `user_version` 设为 1。
3. 任何一步失败，就删掉这个半成品文件，下次启动重新建。

以后升级 schema 时，按 `user_version` 逐级做幂等迁移。连接参数：WAL；`synchronous=NORMAL`（`data.db` 用的是默认值，这一项两个库不一样）；入队连接的 busy timeout 为 500 ms，worker 连接为 5 s。

**备份和恢复：`ingestion.db` 属于不可替代的状态**。理由是 30 天审计是这套设计里唯一的事后监督。具体做法：
- `backup_manager` 增加第二个 store，用 SQLite 的 online backup API 生成一份和 WAL 一致的快照，导出和恢复都与 `data.db` 走同一个流程。
- `data_safety` 把 `ingestion.db` 列为不可替代的数据。
- 恢复之后，owner 先跑一遍 §4.3 的恢复流程。备份时间点之后才写入的 `cand:` 单元，在库里找不到对应记录，就在审计里补一行 `writer=restored_orphan`，文件本身不动。
- 文件损坏时，先改名为 `ingestion.db.corrupt-<ts>`，再从最近一份备份恢复；没有备份就新建一个空库，并在 Need You 里提示。

**表**：

| 表 | 内容 |
|---|---|
| `ingestion_inbox`（候选） | `id`、`idem_key`（UNIQUE）、`source`、`source_ref`、`lane`、`payload`、`payload_schema`、`envelope`、`project_snapshot`、`project_candidates`、`state`、`owner_epoch`、`versions`、`created_at`（UTC） |
| `ingestion_units`（单元） | `unit_id = candidate_id + "." + 稳定槽位`、`claim_group`、每张卡的答案和理由、目的地规则的结果、去向、`project`、动作、`state`、`attempts`、`next_attempt_at`、`error_class`、`error_fingerprint`、`owner_epoch`、`merged_into` |
| `ingestion_writes`（写入日志） | 一行对应一次写操作，见 §4.3 |
| `ingestion_relations` | 只记 `supersedes` 一种关系（§7.3） |
| `ingestion_quota` | 配额预留和扣减的流水（§12） |
| `ingestion_lanes` | 每个 lane 的 `next_due_at`、`last_started_at`、`last_completed_at`（UTC） |
| `ingestion_owner` | `epoch`、`pid`、`started_at` |
| `ingestion_daily_stats` | 30 天之前的汇总数据（§4.4） |
| `ingestion_access_log` | Flow 视图的详情访问记录（§10）。只由 worker 异步写入，只读接口从不写库。同样保留 30 天 |

### 4.1 入队和幂等

- 入队是一条短事务：`INSERT … ON CONFLICT(idem_key) DO NOTHING RETURNING id`。冲突时返回已有那一行的 id 和状态；如果已有那一行的内容哈希不同，就另外记一条 `id_collision`，并在 Need You 里提示。
- 每个 source 必须在契约里声明 `idem_key` 的算法，例如 `(source, event_id, schema 版本, 语义槽位)`。不能用数组下标做 key：一旦拆分数量变了，下标就会变。backflow 用 `message_id`，REFLECT 用 `(run_id, lesson 的哈希)`。
- 入队的 busy timeout 是 500 ms，失败时最多重试 2 次；还是失败，就给 source 返回 `ENQUEUE_BUSY`，由 source 决定下次再提取。这样入队最多拖慢 source 约 1.5 秒，chat 路径永远不会等它（§6.4）。

### 4.2 状态

**单元的状态转移表**。每一次转移都写成 `UPDATE … WHERE unit_id=? AND state=<from> AND owner_epoch=<当前 epoch>`，并要求影响行数等于 1：

| 从 | 到 | 条件 |
|---|---|---|
| `queued` | `merged` | 整理时发现，同一批里有另一个单元和它完全相同，或者可以合并进去。合并到哪个单元记在 `merged_into` |
| `queued` | `judged` | 卡 0 到卡 3 全部算完，结果是要写入 |
| `queued` | `rejected` | 某一张卡或某一条规则拒绝。拿不准时也走这里，原因码 `uncertain` |
| `queued` | `reinforced` | 卡 3 判定和一条合格的已有条目相同，且证据独立（§5.7）。只写一条 `reinforces` 关系，不改任何文件 |
| `queued` | `deferred` | 基础设施类错误，比如 LLM、检索、DB busy、版本冲突、项目有歧义 |
| `queued` | `failed_poison` | 同一个 `error_fingerprint` 连续出现 3 次 |
| `judged` | `writing` | 已经生成写入计划（§4.3） |
| `writing` | `written` | 写入计划进行到 `done` |
| `writing` | `deferred` | 写入计划过期，或者遇到 `user_race` |
| `deferred` | `queued` | 到了 `next_attempt_at`。总共最多 5 次，超过就转为 `failed_poison`，原因码 `retries_exhausted` |
| 任何非终态 | `expired_unprocessed` | 30 天清理执行时（§4.4） |

终态有六种：`merged`、`written`、`reinforced`、`rejected`、`failed_poison`、`expired_unprocessed`。`deferred` 不是终态。

**候选的状态由它所有单元的状态聚合得出**，规则是确定的，按顺序判断：
1. 只要有一个单元是 `writing` 或 `judged`，候选就是 `in_progress`；
2. 否则，只要有一个单元是 `queued` 或 `deferred`，候选就是 `partial_pending`；
3. 否则全部单元都已终态，候选就是 `done`，并附上各终态的单元数。

重试时只处理还没到终态的单元。统计和比率一律按单元计算。

**错误边界**：
- payload 不符合 schema 或解析失败：直接 `REJECT malformed_payload` / `unsupported_schema`，不重试；
- 其余代码异常：记下 `error_fingerprint`，同一个指纹连续出现 3 次就转为 `failed_poison`，在 Need You 里提示，附上样本和栈指纹；
- 同一批里每个候选各用一个 savepoint，一个候选出错不影响同批的其他候选。

### 4.3 写入日志和重启恢复

每一次写操作都有一份**不可变的计划**。worker 写入一个单元是一次操作，用户的一次编辑也是一次操作。计划在第一个副作用发生之前就提交进 `ingestion_writes`。

| 字段 | 内容 |
|---|---|
| `op_id`、`op_kind` | `op_kind` 取值：`ingestion_unit`、`user_edit`、`user_tree_move` |
| `target_path`、`before_hash` | 目标文件，以及计划生成时它的哈希 |
| `intended_bytes`、`after_hash` | 目标文件写完之后的完整内容和哈希 |
| `archive_path`、`archive_hash` | 需要归档时，归档文件的位置和内容哈希 |
| `anchors_present`、`anchors_absent` | 写完之后必须存在的锚点、必须消失的锚点 |
| `relations` | 要写的 `supersedes` 关系集合 |
| `baseline_patch` | 只有 `user_edit` 才有：基线清单要做的修改，以及要设为失效的关系 |
| `step`、`step_owner`、`owner_epoch` | 进行到哪一步，由哪个 owner 执行 |

**步骤按固定顺序执行**，用不到的步骤跳过：

`planned → archive_applied → target_applied → relations_applied → baseline_applied → done`

先归档，后替换。每一步执行前都在语料锁里抢占这一步的执行权（§3.1），执行完才把 `step` 往前推一格。

**每一步怎么判断已经完成**：

| 步骤 | 完成的判定 |
|---|---|
| `archive_applied` | 归档文件存在，并且哈希等于 `archive_hash` |
| `target_applied` | 目标文件哈希等于 `after_hash`，并且 `anchors_present` 全部存在、`anchors_absent` 全部不在。只看到 `cand:` 标记不算完成 |
| `relations_applied` | 关系表里已经有 `relations` 里的每一条 |
| `baseline_applied` | 基线清单里已经包含 `baseline_patch` 的修改 |

**启动时恢复**：owner 先接管所有还没完成的计划（§3.1），再逐份检查：

- 某一步已经满足完成判定：直接推进 `step`，不再执行一次。
- 某一步没完成，目标文件哈希仍等于 `before_hash`：照着计划执行这一步。
- 目标文件哈希既不是 `before_hash`，也不是 `after_hash`：说明中间被别的写入改过。不覆盖，这份计划作废；单元转为 `deferred`，原因码 `stale_plan`。如果是用户编辑，就保留现场，在 Need You 里提示。
- 停在 `judged`、还没生成计划的单元，回到 `queued`。

**故障注入测试**：在每一个 fsync、rename、DB commit 的边界杀掉进程，再加上 owner 交接的场景，恢复之后的结果必须和没出故障时一致。

### 4.4 清理：只保留 30 天

- **规则**：`created_at` 早于"当前 UTC 日期减 30 天"那一天 00:00 的候选，连同它的单元和写入计划，整体删除。按整天切，所以同一天的行总是在同一次清理里处理。保留天数写在契约里，可以配置。
- **删除前先汇总**：把要删的行汇总进 `ingestion_daily_stats`，主键是（UTC 日期、source、lane、目的地、状态、原因码、规则版本摘要），空值统一记成 `-`；同时记下候选数、单元数、写入字节数、LLM 调用次数和 token 数。汇总用累加式 upsert（`count = count + excluded.count`），并且和删除放在同一个事务里。事务一旦回滚，就不会多算；成功删掉的行也不会再被统计第二次。
- **没处理完的候选也一样删**：到期时还没处理完的候选，在删除之前先把状态记为 `expired_unprocessed`，再计入汇总。它们并没有做完 5 次重试，dashboard 也不会这样显示。
- **清理一定会执行**：清理不调 LLM，也不受资源门控。它属于 maintenance lane 的保底时隙（§6.2），backend 启动后会第一个执行。
- **磁盘空间**：`ingestion.db` 建库时就开启了 `auto_vacuum=INCREMENTAL`。每次清理后，先读 `freelist_count`，按这个页数分块执行 `incremental_vacuum(N)`，每块最多 1000 页；跑完再做一次 `wal_checkpoint(TRUNCATE)`。验收时主文件和 WAL 文件的大小分开测量。
- **Inbox 只存提取结果**：source 自己的原始数据（会话 transcript、DailyActivity、run 记录）仍然归 source 自己管。Inbox 只存提取出来的内容和来源 id。
- 超过 30 天的数据，dashboard 只显示汇总统计（§10）。

**不进召回，也不进索引**：Inbox 是状态和审计记录，不是知识。

## 5. Source 与提取（r16 重写）

**第一准则：语义完整，人和 AI 都能读懂。** 提取出来的每一条认知内容，都必须能脱离原会话、原 run 单独读懂，人能读懂，AI 也能读懂。任何环节都不允许截断语义：
- **提取**：一条单元说不完整，就拆成几条各自完整的单元，或者不提取。绝不输出说了一半的句子、省略号结尾的结论、缺了主语或指代不明的片段。
- **入队和存储**：语义字段（`what`、`how`、`why`、`applicability`、event 的各字段）一个字都不截。放不下时整条拒绝（`too_large_incomplete`，§5.8），由 source 重新拆分。
- **写入和渲染**：writer 原样写入完整的单元，不缩写，不改写成摘要。
- **注入和召回**：只能以整条单元为单位取舍（§12 本来就是按单元选取），不能截掉单元的一部分。DailyActivity 的注入同样以整个 session 行、整段日汇总为单位（§5.6）。
- **展示**：Flow 视图的详情可以因为体积上限折叠显示，但必须明确标出"已折叠"，并能展开或打开原文。展示上的折叠不影响存储内容。
- **验收**：每个提取器的输出都要跑一次"可独立读懂"检查（卡 1 的 `self_contained` 题）；静态测试确认语义字段的写入和注入路径上没有任何截断调用。

**原则**：源头的质量决定一切，worker 只能挑选和整理，补不出 source 没有提取到的认知。所以每个 source 在提取时就要按 ontology 分好类、写清 what、how、why，并指明依据的证据。系统只在语义记忆这一层产生复利，复利只累积有判断力、有 taste 的认知。流水账和一次性事实不产生复利。

### 5.1 三层记忆：各管各的

| 层 | 载体 | 内容 | 谁写 | 生命周期 | 产生复利 |
|---|---|---|---|---|---|
| 工作记忆（短期叙事） | `Knowledge/DailyActivity/<date>.md` | 每个 session 做了什么、交付了什么、下一步是什么 | session 结束时的 DailyActivity 生成器，**不经过 CDF** | 注入最近 7 天（§5.6） | 不产生，只负责衔接 |
| 情节记忆（长期） | `.context/TIMELINE.md` | 关键事件：事故、关键决定、里程碑、方向调整、重要交付 | CDF 的 `event` 单元（§5.5） | 只追加，按月汇总 | 间接：经验教训通过 `derived_from` 挂到事件上，供 Retro 使用 |
| 语义记忆（认知） | MEMORY、EVOLUTION、DDD、USER、STEERING、TOOLS、Knowledge | 三层七类的认知单元 | CDF | 按层褪色，按证据强化（§5.7） | **产生** |

### 5.2 Source 收拢：按证据从哪来划分，不按旧模块划分

旧入口一共 13 个，同一份证据（一次会话）会被 distillation、memory_extractor、improvement_writeback、conversation→DDD、user_observer 各提一遍。r16 把它们收成 6 个：

| Source | 合并掉的旧入口 | 证据 | 默认信任 | lane | 主要产出 |
|---|---|---|---|---|---|
| **S1 用户指令** | correction、remember / `s_persist` / `ingest submit`、Save to Memory 按钮 | 用户原话（先记成 `user` 事件，再引用） | `user_explicit` | `immediate` | 纠错、原则、偏好、事件（"记住这件事"） |
| **S2 会话** | distillation、memory_extractor、improvement_writeback、conversation→DDD cultivation、user_observer、backflow | 带消息 id 的完整对话记录 | 逐条计算（§9） | `session` | 决定、经验、坑、心智模型、taste，事件；可附带 Reference 文档（原 backflow） |
| **S3 Run** | REFLECT（`reflect-prepare` + `reflect_job`） | run 的事件日志和产物：需求、design/plan、review 发现及其处理、人工回答、validation 报告、landing commit | 按引用逐条计算：人工回答 → `user_explicit`，review/测试 → `measured`，文档 → `observed` | `run` | 五段知识报告，写进 DDD（§5.4） |
| **S4 代码变更** | `ddd_refresh` job、`code_change_feed` 的技术漂移检测 | git diff 和 commit message（只限绑定到 DDD 的仓库） | `measured` | `scheduled` | 架构决定、架构形状、约定 |
| **S5 系统度量** | `evolution_optimizer`、eval governance | skill 适应度、eval 结果、run 统计 | `measured` | `scheduled` | agent 能力上的纠错和经验，以及改进点 |
| **S6 外部信号** | `signal_ddd_bridge`、Library inbox 里的外部文档 | 网页、RSS、外部文档 | `external` | `scheduled` | 只能产出 Reference，写进 Knowledge |

**停用和改造**：
- DailyActivity 不再是 source，降级为工作记忆（§5.6）。按行切片的提取方式（实测该写比例 0.8%）删掉。
- `user_observer_hook` 的"建议更新 USER.md"文件、`ddd_refresh` 的 proposal 文件、cultivation 的人工审批路径，全部删掉。这些需求由 S2、S4 覆盖。
- REFLECT 的 `route`、`annotate_entry` 和 `reflect_library` 的直接写入删掉。强化、取代和写入一律由 CDF 完成。

### 5.3 认知单元：所有 source 输出同一种格式

source 输出的每一条都是一个 claim unit。必填字段缺失的，**source 当场丢弃，不入队**；`enqueue()` 再校验一次，不合格就 `rejected`。

| 字段 | 要求 |
|---|---|
| `kind` | `cognition`（认知）、`event`（事件，§5.5）、`gap`（改进点或待办，只由 S3、S5 产出） |
| `layer` + `type` | cognition 必填，取三层七类之一：元认知（`principle` 原则、`correction` 纠错）、认知（`decision` 决定、`model` 心智模型）、操作（`lesson` 经验、`pitfall` 坑、`process` 流程）。源码里的映射沿用 `ddd_entry_lifecycle.MEMORY_SECTIONS` 和契约里的 `VALID_TYPES` |
| `scope` | `user`、`agent`、`project:<name>`、`world`。决定去 USER/STEERING/TOOLS、EVOLUTION、DDD 还是 MEMORY |
| `what` / `how` / `why` | `what` 是结论；`how` 是怎么做，或在什么条件下成立；`why` 是原因。决定类必须写上被否掉的方案和原因；坑必须写触发条件；写进 DDD 的三项必须齐全（§7.2） |
| `applicability` | 什么时候用得上 |
| `evidence` | 依据的输入事件 id，或 run 产物的锚点（`finding:<id>`、`event:<seq>`、`doc:<path>#<heading>`、`commit:<sha>`）。至少一个，并且必须在本次会话的账本或本次 run 的记录里能找到。找不到 → 整条无效 |
| `relation_hint` | 可选：`reinforces` / `refines` / `supersedes` / `derived_from`，加上目标锚点。只是提示，最终由卡 3 判定 |

卡 1 从"猜分类"改为**复核分类**：source 给出的 `type` 和 `scope` 是输入，卡 1 判断它是否成立，不成立时按 `REROUTE_ALLOWED` 改投，或者拒绝。答题卡的其余部分不变。

**什么不提取**（所有 source 通用，写进每个提取器的 prompt，并配反例）：
- 操作流水：例如"12 小时 38 个 commit、改了 263 个文件"，"build 时间比 commit 早 3 秒"。
- 没有被验证的推测，例如"也许以后可以试试向量库"。
- agent 对注入给它的已有记忆的复述。只要证据就是那条已有条目本身，就不提取。
- 只在当次任务里有意义的状态，例如"今天的 dist 少了一个 commit"。这类内容属于 DailyActivity。

### 5.4 各 source 的提取规范

**S1 用户指令**：原话转成单元，不做推断。入口先调用 `record_session_input(..., trust="user")`，再把这个事件 id 作为 `evidence`。`s_persist` 和 `ingest submit` 必须传 `--evidence <event_id>`，CLI 会校验这个 id 在账本里存在，并且 trust 是 `user`。没有有效 evidence 的提交一律拒绝，不再兜底成 `inferred`。Save to Memory 按钮记一条 `user` 事件，内容是"用户要求保存本会话"，然后对整段对话跑 S2 的提取器。

**S2 会话**：直接读对话记录，每条单元引用它依据的消息 id。重点提取：
- 用户做出的决定和取舍，连同被否掉的方案及原因；
- 用户的纠正，包括叙述式的纠正；
- 用户的品味和偏好，例如"先出 mockup"、"不打补丁，系统化地捋一遍"。这一类复利价值最高，写进 USER/STEERING 的偏好或原则；
- 经过验证才成立的经验，以及踩到的坑（带触发条件）；
- 能解释"为什么这样设计"的心智模型；
- 关键事件（§5.5）；
- agent 产出的、能独立成篇的文档，作为 Reference 写进 Knowledge（原 backflow 的作用）。

S2 和 DailyActivity 是同一份对话记录的两个并列产物。**S2 不从 DailyActivity 里提取**，因为 DailyActivity 是有损的二手摘要，也没有证据指针。

**S3 Run 知识报告**：run 是执行引擎，它的事件日志和统计（耗时、重试、token）留在 run 自己的记录和 REPORT 里，不进知识库。REFLECT 的目的是给 DDD 提供产品、技术、改进三方面的信息。agent 在 DELIVER 阶段按下面 5 段写，每条都引用 run 产物；任何一段都可以为空，不为了填满而凑内容：

| 段 | 内容 | 目的地 | 证据 |
|---|---|---|---|
| 1. 做了什么 | 交付了什么能力或改动、为什么做、结果（落地的 commit） | `TIMELINE`（带项目标签的事件）；改变了产品能力 → `PRODUCT`；改变了架构形状 → `TECH` Key Subsystems | 需求 / contract、landing commit |
| 2. 关键决定 | 定了什么、怎么做、为什么，被否掉的方案及原因 | 项目层面 → `PROJECT` Recent Decisions；产品取舍 → `PRODUCT` Strategic Priorities / Non-Goals；技术取舍 → `TECH` Architecture | design/plan 段落、人工回答 |
| 3. 纠正 | 哪里走错了、被谁纠正、正确做法是什么（review 发现被接受、人工推翻、测试变红后找到的根因） | 技术陷阱 → `TECH` Runtime Traps；做法失败 → `IMPROVEMENT` What Failed；agent 自身行为 → `EVOLUTION` | review finding 及其处理、人工回答、validation 报告 |
| 4. 经验 | 验证有效、下次应当复用的做法 | `IMPROVEMENT` What Worked，或 `TECH` Conventions | validation 报告、review 通过记录 |
| 5. 改进点 | 被延后的发现、技术债、风险，以及暴露出的流程问题 | `gap` 单元 → `IMPROVEMENT` What to Watch For / Known Issues；明确的后续工作 → `PROJECT` Open Items | 延后处理的 finding、run 统计 |

`gap` 不参与强化。之后某个 run 解决了它，就写一条 `supersedes` 标为已解决，原文不改。项目就是这个 run 绑定的 DDD（`explicit_project`），不靠猜。`PROJECT` 的 Current Focus 和 Blocked By 是实时状态，S3 不写，由 Projects 页面按状态渲染。

**S4 代码变更**：只读取绑定到 DDD 的仓库的 diff 和 commit message。只提"架构形状变了什么、为什么变"，以及新形成的约定。按文件列出的改动清单不提取。

**S5 系统度量**：只提有数字支持的结论，例如"skill X 在 Y 类任务上连续 5 次失败，原因是 Z"。作为 agent 能力相关的纠错或经验写进 EVOLUTION，或者作为改进点写进 `gap`。原始统计留在原处。

**S6 外部信号**：只能产出 Reference，写进 Knowledge，并在 frontmatter 里标 `taint: external`。永远不形成规则。

### 5.5 TIMELINE：长期的关键事件

- **文件**：`.context/TIMELINE.md`，新增为受管文件，按月分节（`## 2026-10`），每个事件是一个块，带 `cand:` 标记。
- **event 单元的字段**：`when`、`what`、`impact`、`outcome`、`project?`、`refs`（session、run、commit）、`follow_up?`、`evidence`。
- **判断标准**：event 不走"可长期复用"这道质量关，只判断是否重要。满足下面任意一条才收：改变了方向、造成了损失或返工、是里程碑或重要交付、做出了关键决定、发生了事故。日常操作不收。卡 1 对 `kind=event` 用专门的一组题。
- **生命周期**：只追加，不删除。一个月结束后，由 maintenance lane 生成当月摘要（作为新单元写在该月节的开头，原事件不改）。
- **注入**：默认只注入最近 10 个事件加上当月摘要（上限 1.5K tokens），其余的走 recall。
- **Retro**：每条经验教训都可以带 `derived_from → <event anchor>`（§7.3）。Retro 时按时间范围列出事件，顺着关系找到从中提炼出的教训，以及这些教训后来被强化了几次。

### 5.6 DailyActivity：一周的工作记忆

- **生成**：仍然在 session 结束时由 DailyActivity 生成器根据对话记录生成，不经过 CDF，也不属于受管的认知文件。
  - 保留 Delivered / Outputs / Validation / Next 四段。**去掉 Lessons 段**，经验教训由 S2 产出。
  - 每个 session 增加一行一句话摘要（30–50 字）。它必须是一句完整、能单独读懂的话，由生成器直接写成这个长度，不能从长句截断得到。
  - 当天结束后由 maintenance 生成一段当日汇总（主线、交付、仍未解决的事项），写进当天文件的 frontmatter。写入后不再修改。
  - 只写 session 里发生过的事，不做推断。
- **注入**（取代原来的"最近两份，每份截取最新 2000 tokens"）：

| 范围 | 注入什么 | 估算（按 2026-10-21 到 10-27 实测：7 天 92 个 session） |
|---|---|---|
| 今天和昨天 | 每个 session 一行：时间、标题、一句话摘要、尚未完成的 Next | 约 29 个 session × 60 ≈ 1.7K tokens |
| 第 3–7 天 | 每天一段汇总 | 5 天 × 200 ≈ 1K tokens |

  - 合计约 2.7K tokens，少于现在的约 4K，但覆盖 7 天内的每个 session。
  - 已经被解决的 Next 不再注入：后续某个 session 的 Delivered 引用了它，或者 TIMELINE / CDF 里记录它已解决。
  - 这是注入侧的改动，DDD 的注入不受影响。实施时实测改动前后的 token 数，并跑 prompt 和 context 相关的测试和 eval。

### 5.7 复利：强化有价值的认知

- **强化（`reinforces`）**：卡 3 判定新单元和已有条目说的是同一件事（`same` / `contained`），并且新证据是独立的时，不再 `REJECT duplicate`，而是：
  - 记一条 `reinforces` 关系（§7.3），带上新证据的引用和时间；
  - 单元进入终态 `reinforced`，在 Flow 视图里单独显示为"强化"，不计入拒绝；
  - 已有条目的正文不改。
- **只有合格的认知才能被强化**：新单元必须先通过卡 1 的质量题（原子、有价值、有依据、长期有效、带 why），被强化的已有条目本身也必须合格。`event` 和 `gap` 不参与强化。
- **证据必须独立**：同一个 session 或同一个 run 里重复出现，只算一次。如果新单元的证据就是被强化的那条条目本身，例如 agent 复述了注入给它的记忆，就不计数，按 `duplicate` 拒绝。这和 holdout 的"排除源头"是同一条规则。
- **权重**：用户的判断和取舍、度量证据，高于 agent 推断。
- **读取侧**：注入预算（§12）的排序和褪色，在层级生命周期的基础上，再乘上独立证据的次数。元认知不褪色，操作层褪得最快；被反复独立证实的条目排在前面。这一步放在阶段 C 实施。

### 5.9 从目的地反推：什么进哪里，多快生效

去向由卡 1 的形态和 scope 决定（`route_for`），source 只给提示。

| 目的地 | 放什么 | 卡 1 形态 | 主要来源 | 要求多快生效 | 读取方式 |
|---|---|---|---|---|---|
| `STEERING` | 用户希望 agent 怎么工作：偏好、品味、工作规则（例如"先出 mockup"） | UserCross / preference | S1、S2 | **下一轮**（S1 立即；S2 在 session 结束后） | 每轮注入 |
| `USER` | 用户是谁：角色、背景、长期目标 | UserCross / profile | S1、S2 | 下一个 session | 每轮注入 |
| `TOOLS` | 用户的环境：机器、账号、工具、路径约定 | UserCross / environment | S1、S2 | 下一个 session | 每轮注入 |
| `EVOLUTION` | agent 自身的纠错和能力，跨项目通用 | Agent | S1（纠正）、S2、S3 第 3 段、S5 | 纠正类**下一轮**；能力类按天 | 每轮注入 |
| `MEMORY` | 跨项目都成立的认知：原则、决定、经验、坑、心智模型 | WorldCrossGlobal（`global_relevance=true`） | S2、S3（跨项目的结论）、S1 | 下一个 session | 每轮注入（阶段 C 按预算选取） |
| `TIMELINE` | 关键事件 | `kind=event` | S1、S2、S3 第 1 段、S4 | 下一个 session | 注入最近 10 条和当月摘要，其余走 recall |
| `DDD`（PROJECT / PRODUCT / TECH / IMPROVEMENT） | 只属于某个项目的决定、产品取舍、架构、约定、陷阱、经验、改进点 | ProjectSingle、UserSingle、WorldSingle，以及 `gap` | S3（主要）、S2、S4 | **同一项目的下一个 run 之前** | 按项目注入 section 指针（不变） |
| `Knowledge/Learned` | 可复用的技术知识和知识卡（某个库怎么用、某类问题怎么查、文章要点） | Reference / `learned` | S2、S6、S1（`s_learn-content`） | 按天 | 只走 recall，不注入 |
| `Knowledge/Reports` | 调研和分析的结论报告（原 backflow 里能独立成篇的部分） | Reference / `analysis` | S2 | 按天 | 只走 recall |
| `Knowledge/Designs` | 设计和架构方案文档 | Reference / `design` | S2、S3 | 按天 | 只走 recall |
| `Knowledge/Library` | 长期参考：规格、标准、契约、外部文档；所有被污染的 Reference | Reference / `spec` | S6、S2、S1（`s_library`） | 按天 | 只走 recall，被污染的放在不可信数据边界内渲染 |
| `Knowledge/Notes` | 其他能独立读懂、但不属于上面几类的短笔记 | Reference / `note` | S2 | 按天 | 只走 recall |

**通用 Knowledge 只放 Reference**，也就是"查得到就够"的资料，不放规则类认知。规则类认知只能进上面每轮注入的文件或 DDD。

**run 没有绑定 DDD 时**（只有仓库，没有对应项目）：
- 跨项目成立的结论照常进 MEMORY 或 EVOLUTION；
- 只属于这个仓库的决定、经验、陷阱，**不进 MEMORY**（避免项目内容污染全局，§2.1 实测 57% 是项目专属），也不进通用 Knowledge 当规则用。它们写成 Reference 放进 `Knowledge/Learned/<repo>/`，frontmatter 标 `project_hint: <repo>`，只走 recall；
- 同一个仓库累计到 3 条以上时，Need You 出一条提示："这个仓库已经积累了 N 条经验，要不要建一个 DDD"；
- 建好 DDD 之后，系统不搬已有文件（§13，已写内容不动）。之后的 run 直接写进 DDD；DDD 规则在召回时可以引用 `project_hint` 相同的 Reference。
- `gap`（改进点）没有项目可去时，只留在 run 的记录和 REPORT 里。

**时效分档**（和 §6 的 lane 对应）：

| 档 | lane | 目标 | 例子 |
|---|---|---|---|
| 下一轮 | `immediate` | 秒级 | 用户纠正、"记住"、偏好 |
| 下一个 session | `session` | session 结束后 30 分钟内 | 会话中的决定、经验、事件 |
| 下一个 run | `run` | run 结束后 30 分钟内，同一项目的下一个 run 开始前必须完成 | REFLECT 五段报告 |
| 按天 | `scheduled` | 12 小时内 | 代码变更、系统度量、外部信号 |
| 按月 / 按天汇总 | `maintenance` | 36 小时内 | TIMELINE 月度摘要、DailyActivity 日汇总 |

同一项目的下一个 run 启动时，如果该项目还有没处理完的 `run` lane 候选，worker 先处理它们，再允许新 run 读取 DDD。

### 5.10 参考资料的索引：两层，都不靠人工逐条维护

`Knowledge/` 下的文件夹分两类：

| 类别 | 文件夹 | 谁写 | 是否经过 CDF |
|---|---|---|---|
| 认知参考（受管） | `Learned`、`Reports`、`Designs`、`Library`、`Notes`、`Inbox` | 只有 CDF 的 writer；用户自己通过 `governed_edit` 编辑 | 是 |
| 日志和产物（不受管） | `DailyActivity`、`DailyBriefs`、`Signals`、`JobResults`、`Handoffs`、`Meetings`、`Pollinate`、`Mockups`、`assets`、`Archives` | 各自的系统任务、skill 或用户 | 否。它们是日志或产物，不是认知；需要从中提炼认知时，由对应的 source 去提取 |

`Knowledge/` 原来就由 skill 写入的认知参考（`s_learn-content` 写 Learned，`s_library` 写 Library），改成调用 `ingest submit`：用户主动要求时走 S1；内容来自网页或外部文档时，信任是 `external`，按 S6 处理，只能写成 Reference。`is_governed()` 的受管列表和这张表保持一致（§3.2）。

**第一层：目录索引 `.context/KNOWLEDGE.md`**（P9，每轮注入，约 2 KB）
- 只写"什么问题去哪里找"和"每个文件夹放什么、谁写"，不列具体文档。它描述的是结构，不是认知，所以**CDF 不写它**；它仍然是用户可编辑文件（复制一次，之后不覆盖）。
- 模板随 r16 更新，与上表一致：DailyActivity 改为"一周工作记忆，不再作为提取来源"；Reports、Designs 标为 CDF 写入；Learned、Library 写明经由 `ingest submit`。已有用户的 `KNOWLEDGE.md` 不自动覆盖，在 Need You 里提示一次"目录索引和新结构不一致"，附上差异，由用户决定是否用 `governed_edit` 更新。

**第二层：文档索引，从文件派生**
- `knowledge_store.sync_knowledge_index()` 扫描整个 `Knowledge/`，按标题切块，增量写入 FTS5（recall 的 `library` 域）。**索引唯一的来源就是文件本身和它的 frontmatter**，不另外维护一份清单。CDF 新建的文件在下一次同步时进入索引；writer 写完一个 Knowledge 文件后，触发一次针对该文件的增量同步，不等定时任务。
- CDF 写的每个 Knowledge 文件，frontmatter 必须完整（遵守第一准则，`summary` 是一句完整、能单独读懂的话，供 recall 结果展示）：

| 字段 | 内容 |
|---|---|
| `title` | 标题 |
| `summary` | 一句话摘要，生成器直接写成完整的一句，不截断 |
| `reference_kind` | `learned` / `analysis` / `design` / `spec` / `note` |
| `ontology_types` | 文中涉及的七类，可以为空 |
| `source` | S1–S6，加上 session id 或 run id |
| `ingestion_candidate` | 写入它的单元 id |
| `project_hint` | 可选；无 DDD 的 run 产出时必填 |
| `taint` | 只有被污染时才有，值为 `external` |
| `created` | 写入时间 |

- recall 的结果展示 `title`、`summary`、`reference_kind`，以及被污染的标记。

**审计层**：哪个单元写进了哪个文件，记录在 `ingestion.db`，Flow 视图可查，保留 30 天。之后只能靠文件 frontmatter 里的 `ingestion_candidate` 和 `source` 长期追溯。

### 5.8 长度上限

source 不把整段会话原样塞进 payload。payload 有长度上限，但遵守第一准则：
- 信封、JSON 结构和所有语义字段永不截断；
- 只有非语义的辅助字段（例如原文摘录 `excerpt`、调试用的上下文片段）可以截，schema 里逐个声明截到多长（按 UTF-8 计），截断处标明 `[已截断]`；
- 语义字段放不下时，直接 `REJECT too_large_incomplete`，不把截断后的文本交给判官，也不写入。source 应当把它拆成几条各自完整的单元重新提交。

**为什么 r16 要重写 source**：阶段 A 的 holdout 实测了旧 source 的"该写"比例（多数票），分别是：session distillation 的 lessons 26%、DDD 条目 46%、backflow Note 8%、用户消息 4%、DailyActivity 按行切出的"决定" 0.8%。阶段 B 的 E2E 又发现，旧 source 没有逐条的证据，按 §9 的兜底规则，几乎所有候选都被判为污染：实际上只有 correction 能写进规则类目的地。阶段 B 的验收里，每个 source 的该写比例单独统计，低于 5% 的 source 会在 Need You 里提示。

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
- **唤醒 worker**：同一进程内用 `asyncio.Event`。跨进程时（CLI、hook 子进程），先读 `backend.json` 拿到端口，再调用 owner 的 `POST /api/ingestion/wake`。这个接口有两层保护：一是专用凭证，backend 每次启动时随机生成，写进权限为 0600 的 `<app data>/state/ingestion_wake.key`，不写进 `backend.json`；调用方在 `X-Swarm-Wake` header 里带上它，backend 用常数时间比较来校验。二是来源地址必须是 loopback，否则返回 403。凭证文件只有本机同一用户的进程能读。hive 模式下 Caddy 有自己的 Basic auth，这个接口不依赖它。接口不接收任何数据，作用只是让 worker 去检查一次。四种运行模式下都要测：远端调用被拒，凭证错误被拒。调用失败也没关系，worker 每 60 秒还会轮询一次 `next_due_at` 和 `immediate` 队列。
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

1. **整理**（批内处理，不调 LLM）：根据信封和项目快照，用确定性规则算出 `project_candidates`，规则见本节末尾。然后解析 payload，去掉会话 id、scratch 路径、PID、哈希这类噪音；批内内容完全相同的候选标为 `merged`。输出是若干 **claim-group**：一个结论，加上与它绑定的 why、how、依据和适用条件。拆分只在 claim-group 之间进行，同一个 group 内部的内容永远不拆开；没把握该怎么拆时，整条候选作为一个 group 交给卡 1。r16 起每个 source 都按 §5.3 的认知单元提取，一个认知单元就是一个 group。
2. **卡 0：规则**（不调 LLM）：检查污染、注入扫描、噪音、长度、内容指纹完全相同、会话已删除、配额。
3. **卡 1：内容本身**：调 1 次 LLM，一批候选可以打包成一次调用。输出是 7 种形状之一，再加上 `content_form ∈ {rule, reference}` 和一个可选的 `reroute_to`（只能取契约允许的形状），并回答 rule 或 reference 对应的那组质量题。
4. **污染约束**（确定性，在卡 2 之前）：被污染的 `rule` 记 `REJECT tainted_rule`。被污染的 `reference` 只有 `Reference` 这一种形状是合法的；如果卡 1 给的是别的形状，记 `REJECT invalid_shape_for_trust`，不会改投到别处。这条约束由代码执行，不依赖 prompt。
5. **卡 2：路由表**（确定性）：每种形状只对应一个去向。写入前会解析目标段是否存在。
6. **目的地规则**（§7.2）：规则不通过时 `REJECT`，原因码标明是哪条规则。
7. **改投**（可选，最多 1 次）：卡 1 可以给出 `reroute_to`，目的地规则 D2 也可能判定去向不对，这两种情况会触发改投。改投后的去向必须在契约允许的"形状 → 形状"映射表里，例如 `UserSingle ↔ ProjectSingle`、`WorldSingle → WorldCrossGlobal`（还需要 `global_relevance` 为真）。改投后从第 5 步重新走一遍：路由、目的地规则、卡 3。第二次再需要改投，或者映射表里没有合法目标，就记 `REJECT destination_mismatch`。
8. **卡 3：和已有内容比对**：在目的地里检索。有命中时调 1 次 LLM，再由 `relation_for_hit` 和 `aggregate_hits` 两个纯函数算出动作。
9. **写入**：由 writer 按写入日志（§4.3）完成。新单元带 `cand:<unit_id>` 和类型；需要的话，先归档旧单元再移除；最后写关系。

**项目怎么定**：在整理阶段，按下面三条的顺序收集 `project_candidates`：source 给的显式绑定；source 所在 repo 对应的 DDD；项目快照里唯一涉及的那个 repo。卡 1 只能从这个列表里选项目。到了卡 2 之前，再由确定性的 resolver 核对一遍：候选列表为空，或者卡 1 选的项目不在列表里，就记 `DEFER ambiguous_project`。改投时，项目只能换成同一个列表里的另一个。

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
| architecture | `TECH.md` § Architecture（principle）/ § Key Subsystems (architecture shape)（model） | `TECH.md` § Architecture | `TECH.md` § Conventions | `TECH.md` § Runtime Traps |
| lesson | `IMPROVEMENT.md` § What Worked | `IMPROVEMENT.md` § What Worked | `IMPROVEMENT.md` § What Worked | `IMPROVEMENT.md` § What Failed |

- 这张矩阵的唯一真实来源是契约，文档里这一份是从契约生成的。测试要求矩阵里每个段名都和模板里的标题**逐字相同**（只做空白归一化），模板标题一旦改动，测试就会变红。DDD 模板也只保留一个来源 `backend/templates/ddd/*.md`：`swarm_workspace_manager.py:379-467` 里内嵌的那份 `DDD_TEMPLATES` 改成从这些文件读取。测试检查矩阵里的每个段在模板里都存在。
- 写入前解析目标文件里实际存在的标题。找不到目标段就 `DEFER missing_section`，不会自己新建段。用户的项目如果删过段，这条候选会留在审计里，看得见。
- 层由 `layer_for(target_section, type)` 唯一决定（§7.3）。

#### 卡 3：和已有内容的关系

**检索语料**：一个专用的单元语料，范围是卡 2 选定的去向，加上每轮都会注入的 MEMORY、EVOLUTION、STEERING、USER、TOOLS、SOUL、AGENT；去向是 DDD 时，再加上该项目的另外三份文档。语料由**统一的单元解析器**生成（§7.3），用 `memory_index._bm25_scores` 打分。召回取三路结果的并集：内容指纹、标题或锚点、BM25 前 8 个。被取代的单元**不剔除**，它们的命中按 §7.3 转交给当前生效的那一条。

**检索结果带类型**：`OK_EMPTY` 直接新加，不调 LLM；`OK_HITS` 继续往下走；`UNAVAILABLE` 和 `STALE` 一律 `DEFER`。语料摘要由受控文件的单元哈希直接算出；只有当这个摘要和写入计划里的目标版本对不上时，才判为 `STALE`，下一次处理时重建语料。批内维护一个增量的内存索引：writer 每成功写入一条，就把它加进去，所以同一批里后面的候选能检索到刚写进去的内容。

**判官对每个命中输出**（不是只对最接近的一个）。判官只填下面四个语义字段；`score`、双方的证据强弱、`observed_at` 由 pipeline 按 anchor 填入并覆盖判官的任何输出，判官给出不存在的 anchor 记 `untyped`（r15.2，阶段 A 实测时发现原实现允许判官自报证据强弱）：

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
6. 有 `exception` → 新加，作为例外。和被命中的单元之间的关系只记在审计里，首版不写关系表
7. 有一个或多个 `refine` → 选分数最高的那一条，分数相同时取锚点字典序最小的那条。不管它是 `cand:` 单元还是基线单元，都**不原地修改**，按下面的"替换"流程生成一个新的有效单元（完整保留旧单元的全部主张；如果是 DDD 内容，保留 what、how、why），然后加一条 `supersedes`
8. 其余 → 新加

改投不在这两个函数里处理，它是 pipeline 的第 7 步。

两个函数都要做全枚举的属性测试，包括命中顺序打乱后结果不变。

**替换**：
- 旧单元是 `cand:`：先归档再移除，写入新单元，加 `supersedes` 边。
- 旧单元是基线单元，分三步：
  1. **列出旧单元的全部主张**：这一步叫卡 3b，是一次单独的判官调用，成本单独统计。为旧单元生成一份主张清单，每条带原文句子的位置。条数不设上限。
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

- **统一的单元解析器** `parse_units(path) -> [Unit]`。每个 Unit 带这几个字段：`path`（工作区相对路径）、`section`、`byte_start`、`byte_end`、`identity`、`byte_hash`。`byte_start` 和 `byte_end` 只表示这一次解析时的位置，同一个文件里任何地方插入内容都会让它们整体漂移，所以不能当作持久身份；能持久引用的只有 `identity` 和 `byte_hash`。
  - bullet 形式的单元（MEMORY、DDD）和 `###` 块形式的单元（EVOLUTION），都由它来解析。
  - 基线生成、卡 3 语料、MEMORY 组装、DDD 段语料和渲染，都只用这一个解析器。`context_directory_loader.py:256-302` 和 `recall_multi.py:100-220` 改为调用它。
  - `byte_hash` 是原始字节的哈希。`identity` 对 `cand:` 单元取 `cand:<unit_id>`，对基线单元取基线清单里记下的 `byte_hash`。
- **单元锚点**：`<path>#<section>#<identity>`。
- **层**：由 `layer_for(target_section, type)` 决定。所有合法组合列在一张表里，表里没有的组合直接拒绝。
- **关系**（首版只做最小的一套，不做通用图 v2）：
  - **存储**：放在 SQLite 的 `ingestion_relations` 表，字段为 `subject_anchor`、`predicate`、`object_anchor`、`evidence`（JSON，`reinforces` 用）、`created`、`inactive`。r16 起 `predicate` 有三种：`supersedes`（取代）、`reinforces`（独立证据强化同一条，§5.7）、`derived_from`（教训来自某个 TIMELINE 事件，§5.5）。主键是 `(subject, predicate, object)`，写入用 upsert。`.knowledge-graph.yaml` 和 `knowledge_graph.py` 现有的边不迁移，读取侧继续照旧使用它们。
  - **约束**（在锁内校验，不满足就 `REJECT incompatible_hits`；下面的环和链头约束只对 `supersedes` 生效，`reinforces` 和 `derived_from` 只要求两端可解析）：
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

## 8. 写入者迁移：source 只入队，worker 是唯一的自主写入者

| 现在的写入路径 | r15 |
|---|---|
| 第 5 节表里所有 source 的直接写入 | 改成 `enqueue()`，原来的写入代码删掉 |
| `ingestion_gate()`、`admission_band()` | 并进 worker 的卡 1 和 DDD 规则，不再是公开入口 |
| cultivation 提案的人工批准接口 | 删掉。三路输入直接作为候选入队 |
| session 结束的 writeback（置信度 0.5 永远过不了 0.7） | 先看过去 30 天它丢弃了哪些候选，再决定是删掉还是改成一个 source |
| `s_memory-distill` | 改成手动触发入口：立即运行一次 `session` 和 `maintenance` lane，然后汇总这次审计 |
| 两个编辑器接口、create / delete / trash / rename、上传（`workspace_api.py:1758-1807`、`1876`、`1937-1939`、`2019`、`2076`；`workspace.py:692`、`714-777`） | 涉及受控路径时，一律改走 `governed_edit`（带 `expected_revision`，§3.2），记为 `writer=user_edit`，并修订基线（§13） |
| 项目的 rename / delete（`swarm_workspace_manager.py:3073`、`3142-3195`，HTTP 入口 `routers/projects.py:143-197`） | 改走 `governed_edit` 的树级操作 `user_tree_move`（§3.2、§4.3） |
| Library 的清理（`library_health.py:191-244`，入口 `library_api.py:426-447`）、Library Inbox 复制（`library_inbox.py:69-103`，入口 `library_api.py:384-392`） | 改走 `governed_edit`，算用户编辑。Inbox 复制进 `Knowledge/Inbox` 也一样 |
| eval governance 改写 EVOLUTION（`evolution/governance_miner.py:120-163`，调用方 `eval_service.py:1287-1289`） | 改成 source：判定结果入队，由 worker 写入 |
| 随 DDD 下发的 `s_ddd-persist` skill（`templates/ddd-skills/s_ddd-persist/SKILL.md:91-113`、`scripts/locked_write.py:554`） | 改成调用 `ingest submit`，删掉直接写入的模式。模板和已经下发到各 DDD 的副本，都要被扫描规则覆盖 |
| loops-health 的自动修复（`s_loops-health/scripts/loops_health_check.py:786`、`858`、`877`、`999`） | 只保留诊断，删除修改部分 |
| `artifact_cli ddd-retire --apply`（`artifact_cli.py:526-529`） | 删除 `--apply`，只保留报告 |
| 迁移脚本 `migrate_memory_lifecycle.py:185`、`migrate_cultivated_titles.py:135` | 已经跑完的一次性迁移，删除 |
| workspace 初始化、codebase scan 创建 DDD 文档（`swarm_workspace_manager.py:1264`、`1450`、`1920`） | 允许"文件不存在时才创建"，这类写入作为显式豁免；已经存在的文件不覆盖 |
| 每周 Memory Health 的修改阶段（`memory_health.py:584`、`796`、`867`、`1005`、`1056`）、EVOLUTION prune 和维护（`evolution_maintenance_hook.py:422`、`866`、`950`）、context health 里的修改（`context_health_hook.py` 中写 MEMORY、EVOLUTION、KNOWLEDGE、DDD 的各处，行号由扫描器生成）、DDD 矛盾剥离、size valve 驱逐基线单元、`locked_write` 清理旧索引 | 这些都会改动已沉淀的内容，和 §13 冲突，**在阶段 B 全部关成只读**，只留下只读统计。size 控制改成只作用于 `cand:` 单元，并放进读取侧的注入预算（§12） |
| `ddd_orchestrator` 的机械替换、`ddd_maturity` 的成熟度注释 | 首版改成只读，不再写入 |
| DailyActivity、checkpoint、JobResults、Signals、Reports | 它们是日志，不进 Inbox。新的 checkpoint 写到恢复文件，不再写进 DailyActivity |

**写入者的边界靠 materializer 保证**（§3.2）。AST 扫描证明不了"所有写入点都找到了"，因为路径是在运行时拼出来的。所以分两层来做：
- **静态检查**：materializer 以外的生产代码，不能调用任何写文件的函数，例如 `write_text`、`open('w'|'a')`、`os.replace` / `rename` / `unlink`、`shutil.*`。随产品发布的非 Python 脚本单独扫描。
- **运行时判断**：由 materializer 判断路径是否受控。

AST 夹具只用来做回归，保证上表列出的每一个写入点都能被检出。上表里的行号是扫描器生成的证据，不是规范的一部分。豁免只有三类：writer、`governed_edit`、"文件不存在时才创建"。

**切换时的不变量**：只要有任何一个 source 开始用 Inbox，就不能再有任何非 writer 的自动写入在运行。所以阶段 B 上线时，上表所有旧的修改路径都会同时关闭。

**带外修改检测**：maintenance lane 每天把各受控文件的单元集合和"基线 + writer 写过的版本"做比对，对不上的部分在审计里记一行 `writer=unknown` 并附上 diff，同时在 Need You 里出现一条记录（§10）。

**切换方式**：所有 source 在阶段 B 一次切换（§14），旧的写入路径同时删掉，不做影子模式，也不做双写。上线后，按 source 分别统计观察指标；哪个 source 不达标，就单独修那个 source 的提取或规则。确实需要回滚时，回退的是整个阶段 B 的版本。

## 9. 来源和污染（沿用 r12，入口改为 `enqueue()`）

**信任从哪里来**：后端没法知道一段文字是用户亲手打的，还是粘贴进来的。所以不声称能区分这两种情况。所有信任判断都基于"这段内容来自哪个输入事件"。

- **唯一入口**：`record_session_input()` 是写入输入事件的唯一函数。必须调用它的地方有：dashboard 发送消息、已授权的 channel 消息、引用和附件（拆成单独的事件）、工具结果落库、会话压缩、会话恢复、REFLECT 的 run 证据。每一处都要有端到端测试。还没接上这个函数、拿不出逐条依据的 source，它的单元在 `enqueue()` 直接拒绝（r16，见下面"source 不给依据时"）。r15 的整会话兜底会让任何会话来源的 rule 都被拒（阶段 A 的 holdout 里，364 条会话来源候选全部落在这条兜底上），r16 已删除。所以阶段 B 的切换条件是：每个 source 先接好 `record_session_input()`，并且能为每个单元返回依据的事件 id，然后这个 source 才切到 Inbox。没接好的 source 保持旧路径只读关闭，不能以"全部被污染"的状态切过去。
- **输入事件**：每次有内容进入会话，都记一条事件，带不可变的 `event_id` 和 `trust`。会话里所有事件 id 都记进会话的**输入账本**（`session_inputs` 表）。账本随会话持久化，压缩和恢复会话之后仍然保留，`HookContext` 和 run 记录都会带上它。

| 输入事件 | trust |
|---|---|
| 用户在 dashboard 或已绑定频道里**本人发出**的消息（已认证的发送者） | `user`。如果消息里有引用块、转发块、附件，或者有 fenced 代码块，这些部分单独记为 `external` 事件 |
| 白名单里的结构化测量器（pytest 的通过/失败计数、`/health` 字段、exit code），只取结构化字段；run 的 review 结论和 validation 结果 | `measured` |
| 读取用户自己的受管内容：工作区 `.context/`、`Projects/*`，以及绑定到 DDD 的仓库（`bindings.yaml`）里的文件；run 自己的 design/plan 文档（r16 新增） | `observed` |
| 其他所有工具或命令的输出、网页抓取、MCP 返回、读入的其他文件（包括未绑定的第三方仓库）、他人发的频道消息 | `external`（这些内容可能把外部文本原样带进来） |
| agent 自己产生的文本 | 不是输入事件，信任等级继承它所依据的输入 |

- **候选必须声明依据**：`enqueue()` 要求 source 给出这条候选所依据的 `source_event_ids`，并且只能引用这个会话账本里已有的 id。
  - **证据**：所依据的事件全部是 `user`，证据为 `user_explicit`；全部是 `user` 或 `measured`，证据为 `measured`；含 `observed` 但不含 `external` 时，证据按写入者类型取 `review` / `run_lesson` / `inferred`，且不算污染。
  - **污染**：所依据的事件里只要有一个 `external`，就是 `tainted`。
- **source 不给依据时**（r16 修订）：不再退回看整个会话账本。缺少依据的单元在 `enqueue()` 直接 `rejected missing_evidence`，并计入该 source 的统计。阶段 B 的 E2E 证明，整会话兜底会让几乎所有会话候选被判为污染（工具结果每轮都有）。
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
- **健康区**：各 lane 的积压和最老一条的等待时间、熔断记录、每天的 LLM 调用次数和 token、Inbox 大小和上次清理时间、MEMORY 与 EVOLUTION 的大小及注入时省略的单元数。

**第一版的范围**：只做审计闭环，包括两个 tab、候选列表和详情、Need You 里的 ingestion 异常、各 lane 积压、上次清理时间、按天的统计。流程节点之间的联动筛选、周和月的切换、预取，这些放到下一版。

**安全和上限**（正文规定，不只写在修订记录里）：
- 查询连接以 SQLite 的 `mode=ro` 打开，并设置 `query_only`。
- 只读当前实例自己的 `ingestion.db`。需要来源信息时，再只读地查 `data.db` 的 `session_inputs`。
- 列表每页最多 50 条，时间窗最长 30 天；详情接口里的 payload 和理由字段，单个最多 16 KB。
- 写入 Inbox 之前，先用现有的密钥扫描规则做脱敏；返回给前端时再扫一遍。
- 所有文本都作为数据渲染（转义），从不返回存好的 HTML。
- "打开原会话"和"打开文件"会先校验路径确实在工作区根目录下。
- 每次访问详情都记一行访问日志：只读接口把这条记录交给 worker 的一个有界队列，由 worker 异步写进 `ingestion_access_log`，所以只读连接本身从不写库。

**实现**：后端在 `attention_authority.collect()` 里加 `_collect_ingestion()`；新增只读接口 `GET /api/ingestion/flow`（总览）、`/api/ingestion/items`（列表）、`/api/ingestion/items/{id}`（详情）、`/api/ingestion/stats`（历史趋势，读汇总表）。四个接口都跑在 `executors.run_nav` 上，做同步、参数化的 SQLite 只读查询，主要读当前实例自己的 `ingestion.db`；需要追溯来源时，另开一个只读连接，按事件 id 点查 `data.db` 里的 `session_inputs`。两个库不 ATTACH，也不假设两者之间有事务一致性。
另外新增 `POST /api/ingestion/wake`：要求专用凭证，并且只接受 loopback 来源；请求不带任何数据（§6.1）。前端改动：`NeedYouOverlay` 加 Tabs；新增 `CognitiveDistillationFlow` 视图；`AttentionEntry.source` 加 `ingestion`，`ATTENTION_SOURCE_LABEL` 加对应标签；每个 tab 用独立的 query key，徽标计数仍沿用现有的 attention 轮询；补中英文 i18n；遵守 Nav visual system，并通过 nav visual 测试和相等性测试。

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
- **写入容量**：每个每轮注入的文件，`cand:` 单元每天新增不超过 4 KB、总量不超过 40 KB；每个项目的 DDD 每天新增不超过 20 KB；Library 每天新增不超过 50 个文件或 5 MB。超出时 `REJECT quota`。配额按 UTC 日计算，计量的是最终新增的 UTF-8 字节数：replace 只算新单元的字节，归档不算。卡 0 先在 `ingestion_quota` 里写一条预留，writer 写成功后，在同一个事务里把这条预留转成扣减。预留 1 小时后自动失效；重启时，没有对应写入计划的预留会被回收。同一个 `unit_id` 只能扣减一次。
- **熔断**：同一个 source 1 小时内至少处理 20 条，且基础设施类的 `DEFER` 超过 50%，就暂停这个 source 1 小时，同时在 Need You 里出一条提示。内容被拒绝不计入熔断。
- **索引排除依赖目录**：`node_modules`、`.venv`、`dist`、`build`。
- 不重切 chunk，不重建索引。

## 13. 已经沉淀的内容：系统不动，用户可以改（沿用 r12）

- **基线**：上线后 backend 第一次启动时，对每个受控文件跑一遍 `parse_units()`，把每个单元的锚点和 `byte_hash` 记进 `baseline.json`。这一步只读不写。
- **系统不动**：基线单元不会被任何系统路径修改、移动、删除或打标记。这包括新功能、所有维护机制（§8 的退役清单），以及插入原语，后者只往段末追加字节。
- **用户可以改**：用户通过编辑器或上传修改受控文件时，`governed_edit` 生成一份 `op_kind=user_edit` 的写入计划（§4.3），其中带上 `baseline_patch`，然后依次完成下面三件事。中途崩溃的话，按同一个 `op_id` 继续往前做完：
  1. 对修改后的文件重跑 `parse_units()`；
  2. 更新 `baseline.json` 里这个文件的单元表。被删的单元移出基线；被改过的单元换成新的 `byte_hash`；新出现、但没有 `cand:` 的单元也算作基线，因为它们是用户亲手写的；
  3. 关系图里指向已消失单元的边设为失效。

  这次修订在审计里记一行 `writer=user_edit`，附 diff。
- **带外修改**：绕过适配器的改动，由 maintenance 的比对检测出来，记作 `writer=unknown`（§8），只作为一次观察结果记录下来。基线**不会**因此自动修订，包括启动时也不会，否则来历不明的内容会被静默认定为用户写的。只有用户下一次在编辑器里保存这个文件时，基线才会随之修订。
- **基线文件本身**：`baseline.json` 带版本号，用原子写入。损坏时从上一份备份恢复，并在 Need You 里出一条提示。文件被重命名时，基线按内容哈希重新对应到新文件。
- **新旧共存**：卡 3 检索到的基线单元只用于判断。补充和替换都新加一个完整的有效单元，并加一条 `supersedes`，读取侧把命中转发给链头（§7.1、§7.3）。
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

实施只分三个阶段。每个阶段在 worktree 里做完，ff-merge 进本地 main；不 push，也不 rebuild。每个阶段末尾只有一道验收门，过了再进下一阶段。

**阶段 A：离线 go/no-go（不改生产代码）**
1. 实现 worker 的纯逻辑：整理、卡 0、卡 1 的 prompt、污染约束、卡 2、目的地规则、改投、卡 3 的检索和两个纯函数。全部放在 `core/ingestion/`，不接任何 source，也不写任何受控文件。
2. 实现离线工具 `scripts/ingestion_replay.py`。它把候选灌进一个**临时** Inbox，在只读的语料副本上运行，输出每个单元的决定。回放有两种模式：
   - **A，录制模式**：卡 1 和卡 3 的结构化输出用事先录好的，只测卡 2、规则、改投和聚合这些部分，结果必须完全确定。
   - **B，在线模式**：固定版本向量（见第 4 条）和批内顺序，同一份数据跑 3 次，报告结果和稳定率。
3. **数据分两份，互不重叠**：
   - **dev**：审计里用过的样本（MEMORY 97 条、DDD 40 条、backflow 15 篇）加上合成夹具。prompt 和规则只能在 dev 上反复调。
   - **holdout**：从会话历史、DDD 文档、backflow Note 里另外抽样，并排除 dev 里已有的条目。抽样前先冻结 parser、claim-group 的划分规则和"重复对"的构造方法，否则代码一改，分母也会跟着变。
     - **抽样单位是簇**：同一个会话、同一份文档或同一个 run 算一簇，按簇抽取，用固定的随机种子。置信区间用 cluster bootstrap 复核：如果它比 Clopper–Pearson 更严，以 bootstrap 的结果为准。
     - **标注**：由 3 个互相看不到对方结果的盲评 subagent 独立标注，标注规则和卡 1、卡 3、DDD 规则的定义一致，按多数票定标签。三票全不一致的样本标为 `unresolved`，**交给 Gawan 裁定**，不得丢弃。
     - **分层抽检，必做**：按 source、指标、"三票一致 / 二比一"三个维度分层，共抽 60 条，也由 Gawan 裁定。60 条里至少 54 条和多数票一致（单侧 95% 下界 ≥ 80%），这份 holdout 才有效；否则作废，修订标注规则后重新标注。按以往的分歧比例估计，Gawan 总共要看 60 条抽检，外加 20 到 40 条 `unresolved`。
     - **只用一次**：holdout 冻结后带上版本号，只在最终判定 go/no-go 时跑一次。没通过就回到 dev 上修改，最多再换 1 份新的 holdout。换新 holdout 时预注册 α 分配：两次判定各用单侧 α = 0.025。
   - **holdout 的预注册分母和门槛**：统一用 95% 单侧 Clopper–Pearson 下界判定。用了第二份 holdout 时改为 97.5%，对应的分母按同一个脚本重新计算。

     | 指标 | 单位 | 分母 | 门槛（下界） | 预注册分母 | 需要对的数 |
     |---|---|---|---|---|---|
     | 写入精确率 | 单元 | 判为写入的单元 | ≥ 93% | 210 | 202 |
     | 该写的召回率 | 单元 | 标签为该写的单元 | ≥ 75% | 180 | 145 |
     | 项目专属内容送到正确的 DDD | 单元 | 标签为项目专属的单元 | ≥ 80% | 150 | 129 |
     | D1 拦住缺 why 的条目 | 单元 | 标签为缺 why 的决定类条目 | ≥ 90% | 130 | 123 |
     | 已知重复被判为"不变" | 对 | 标签为重复的对 | ≥ 90% | 90 | 86 |

     预注册分母是保守取值：在真实准确率比门槛高 4 到 8 个点的前提下（精确率 97%、召回率 83%、路由 88%、D1 96%、重复 97%），通过的概率约为 82%、84%、81%、85%、87%。满足同样功效的最小分母更小一些，约为 185、167、135、116、76。计算脚本是 `scripts/ingestion_holdout_size.py`，它的输出由测试固定下来。作为参照：全对时，要证明 93% 至少需要 42 个样本，证明 90% 至少需要 29 个；现有的 15 篇 backflow 即使全对，下界也只有约 82%。缺 why 的条目和重复对这两类，真实样本不够时，可以通过改写真实条目来补，但补的部分不能超过分母的一半，并且单独报告。
   - **"被污染的 rule 不会进入每轮注入的文件"不做抽样统计**，因为有限样本证明不了"等于 0"。这一条改成安全不变量来保证：对形状、信任级别和去向的全部组合做全枚举测试和属性测试，并做 mutation 检查，要求零违规。
3a. **holdout 标注协议的补充**（r15.2，阶段 A 实际执行中发现）：
   - **排除自身来源**：从语料里抽出的候选（DDD 条目、EVOLUTION 记录），它在语料里的原文就是它自己。标注者和引擎都必须把这一条排除在比对之外（引擎用 `--exclude-origin`），否则会被判成"已存在"。阶段 A 第一轮标注没有这条说明，209 条 DDD/EVOLUTION 候选里有 132 条被三方一致判成"已存在"，重标后 DDD 的"该写"从 25 条变成 93 条。
   - **污染兜底不进 holdout**：会话来源候选在 holdout 里按 `tainted=false` 标注，并标记 `taint_fallback_in_production=true`。holdout 测的是判官质量；污染门由全枚举测试和 mutation 检查保证。
   - **正例补抽**：自然分布下正例太少（主池 6%），允许按簇补抽高产出来源，并在 manifest 里记录。补抽范围按分母缺口决定，必须在看到任何引擎结果之前冻结。
   - **判官 prompt 必须携带完整的输出契约**（字段说明、一个能通过解析器的示例、精确的 JSON schema），并且允许回复外面包一层 ```json 代码块。回放要记录每次调用的原始回复，包括解析失败的。阶段 A 的第一次在线探测因为缺这两点，全部回复都是 `untyped`。
4. **通过条件**：
   - 每项指标都达到门槛。
   - 在线模式的**稳定率** ≥ 95%。稳定率指同一个单元跑 3 次，最终动作（写入、拒绝、DEFER，以及去向）完全相同的单元所占的比例。另外单独报告每张卡各字段的一致率。
   - 录制模式要求结果完全确定，这是另一道独立的门，和稳定率不使用同一套置信表述。
   - 报告列出每项的分子、分母、点估计和下界；`DEFER` 和 `uncertain` 单独计数。
   - 每次运行都记录完整的版本向量：模型 id 和 provider 版本、temperature、top-p、seed（模型支持时才有）、prompt 哈希、normalizer / 规则 / 契约的版本、检索语料摘要、运行时版本。

**阶段 B：引擎上线，所有 source 一次切换**

这一阶段一次性交付下面这些内容：

- **存储和引擎**：
  - 新建 `ingestion.db`，包含 §4 的全部表；`data.db` 只加一张 `session_inputs`（v16 迁移）。
  - owner 锁和 epoch（§3.1），`enqueue()`。
  - `ingest` CLI 和 wake 凭证。
  - lane 调度、公平性、负载感知、清理（§6、§4.4）。
  - 写入日志和恢复（§4.3），writer，materializer。
  - `governed_edit`：包括编辑器 revision、树级操作，以及对目录操作的限制。
  - `ingestion.db` 的备份和恢复，`record_session_input()`。
- **provenance 先于切换**：`record_session_input()` 和每个单元的依据 id 是切换的前置条件（§9），不是附带工作。
- **r16 source 重写**：6 个 source 按 §5 的认知单元格式提取；TIMELINE 与 `event` 单元；`reinforces` / `derived_from` 关系；S3 五段报告；DailyActivity 新格式和一周注入（§5.6）。
- **holdout 重做**：提取方式变了，候选分布也跟着变。holdout 改为从新提取器的输出里按簇抽样重标（第一版已作废，见 §14.1 T1），dev 同样换成新提取器的输出。
- **全部 source 一次切换**：§5 表里的每个 source 都改成入队，原来的写入代码删掉。§8 表里的每条旧修改路径，要么关成只读，要么改走新路径。必须同时切换，因为 §8 要求"只要有一个 source 开始用 Inbox，就不能再有任何非 writer 的自动写入"；如果逐个迁移，还没迁的 source 仍在直接写，正好违反这条不变量。
- **Flow 视图**：Need You 里的 Cognitive Distillation Flow 页（第一版范围），以及 `AttentionAuthority` 的 `ingestion` source。

**验收门**（一次性检查，全部通过才算完成）：

- **自动化测试**：
  - 故障注入：在每个写入步骤的边界杀进程，恢复后结果一致；
  - 两个完整的 backend 同时启动，只有一个 worker 在运行；
  - 被新 epoch 取代的旧 owner 写不成任何文件；
  - 外部编辑器在替换前、替换后各写入一次，两种时机都不会被系统自动回写；
  - 备份恢复之后，审计记录完整，恢复流程能补齐中断的写入；
  - 只含基线内容的夹具跑完所有 lane 后，基线单元的字节哈希完全不变；
  - 静态检查确认：materializer 以外没有任何直接写文件的调用；
  - **每个 source 一条真实链路 E2E**：造一个真实的会话账本或 run 记录（含 user、observed、external 事件），调用真实的 source 代码，经过 worker 和 writer 写入临时工作区，校验落盘内容、证据、污染判定和关系；7 类目的地，加上 refine、supersedes、reinforces、卡 3b 都要覆盖；
  - **daemon 冒烟**：用预览 gateway（`pod_up`）指向临时数据目录，跑一个真实 session，确认 kick → worker → 写入 → Flow 视图自动完成。
- **性能**：chat、CLI、清理、dashboard 同时运行时，实测入队延迟 p99 < 200 ms；chat 首 token 的 p90 和基线相比，差异在误差范围内。
- **真实流量观察**：上线后观察 7 天，或者等每个 source 至少产生 30 个候选（哪个先满足以哪个为准），然后每个 source 都要满足下面这些条件：
  - 每个候选在 Flow 视图里都能看到完整的处理过程；
  - 重复率 < 2%；
  - 最老的 `deferred` 不超过 24 小时；
  - 抽查 20 条写入，错的不超过 1 条；
  - 没有出现带外修改。

  某个 source 不达标时，只修这个 source 的提取或规则，然后重新观察这一个 source，不回退整个阶段。

**阶段 C：注入预算（读取侧）**

MEMORY 和 EVOLUTION 的确定性注入预算（§12）改的是读取侧，按惯例要单独做成对回放和盲评，所以单独列为一个阶段。

**验收门**：
- MEMORY 超出预算时，选取结果是确定的；
- 元认知层完整保留；
- 成对回放加盲评：useful 和 noise 两项都不比基线差。

**每个阶段都要满足**
- 安全类的不变量（§7.2 末尾，以及"被污染的 rule 不会进入每轮注入的文件"）都做 mutation 检查：撤掉修复，对应的测试必须变红。其余规则用表驱动测试和 Hypothesis 属性测试；
- `swarm_select_tests.py` 选出的测试全部通过；
- Windows CI 通过；
- 四种完整的 HTTP backend 模式下，lane 都能被触发；CLI 和 hook 子进程从不处理候选；
- Inbox 只保留 30 天。删掉的行在汇总表里的计数和删除前一致；连续两次跨越午夜的清理、以及事务回滚，都不会多算或漏算；新建的 `ingestion.db` 上 `PRAGMA auto_vacuum` 为 INCREMENTAL；清理之后文件确实变小；文件损坏时能重建，并发出提示。

上线后：每次改 prompt 或规则，都要（1）用 holdout 的当前版本跑一次在线模式，各项门槛不能退化；（2）对 Inbox 里现有的 30 天真实数据跑一次录制模式，列出决定前后的差异。30 天内某个 source 的样本太少时，以 holdout 为准。30 天以前的数据只剩统计数字，不能回放。

### 14.1 调优待办（单独窗口做；Gawan 2026-10-27 决定先完成 E2E，接受跳过 go/no-go 的风险）

阶段 A 的离线判定还没完成。E2E 先按当前 prompt v3 打通；在下面这些项目做完、go/no-go 通过之前，**不 rebuild 上线**。

| # | 待办 | 现状 / 入口 |
|---|---|---|
| T1 | holdout 重标 | 第一版作废：人工确认的抽检一致 51/60，门槛 54。重标方案：标注规则 r6；约 305 条 2 比 1 的条目重标 should_write；141 条"缺 why"重标；再做一次新的 60 条分层抽检。人工裁定方式待定（Gawan 全裁，或 GPT-sol 初筛） |
| T2 | 在 dev 上调 prompt | 基线：Gawan 终端跑的 2b（prompt v2）。当前 v3（`722758dc6`）给卡 1 加了来源上下文，并明确了"有依据"的定义。原因是冒烟测试里 4/5 被判成"推测" |
| T3 | holdout 在线回放一次，打分 | `cdf_go_no_go.py`，按 §14 的门槛判定；评分脚本已经用合成数据验证过 |
| T4 | 42 条 shape 分歧 | 暂时采用 GPT-sol 的裁定，标为模型裁定；重标时一并复核 |
| T5 | 标签噪声和精确率门槛 | 多数票的 should_write 错误率约 4.2%，集中在 2 比 1 的条目。T1 后重新估计；如果仍超过 2%，就评估 93% 这个门槛能否测出来 |
| T6 | 各 source 的"该写"比例 | E2E 上线后按 source 统计；低于 5% 的 source 要改提取方式 |
| T7 | 卡 3 / 卡 3b 的真实判官质量 | dev 冒烟只走到卡 1，卡 3 和 3b 还没有真实回复 |

## 15. 不做

- **不做人工审批和提案**：答题卡就是最终决定，人通过审计来看结果。
- **不让 source 自己判断或写入**：source 只负责提取。
- **不做影子模式和切换日**：每个 source 迁移时直接生效。
- **不做 OS 层的写入隔离**：用"唯一的自主写入者 + materializer + 提示性的 gate + 带外修改检测"代替。
- **不引入向量库**：换了说法的重复只靠卡 3 的 BM25，漏掉的先接受，并在 holdout 里计数。
- **首版不做 maintenance 语义复审**：maintenance 只做清理、恢复、带外修改检测和统计。
- **首版不做 `extends` 关系**（例外和补充的关系只记在审计里），不做通用关系图 v2 和跨项目边，`ddd_orchestrator` 首版只读，也不做 `classify_entry_type` 对照，不做插件化的 source 注册。
- **不改 DDD 的注入方式。**
- **系统不整理已经沉淀的内容。**
- **不重切 chunk，不重建索引。**

## 16. 每个事实在哪、谁写谁读

| 事实 | 唯一来源 | 写 | 读 |
|---|---|---|---|
| source 的登记信息（lane、payload 格式、提示） | `store_contracts` | 人改 | `enqueue()`、worker |
| 标准答题卡、路由表、DDD 段矩阵、配额、注入预算、Inbox 保留期 | `store_contracts` | 人改 | worker、读取侧 |
| 目的地规则 | `core/ingestion_rules/*.py` | 人改 | worker |
| 候选的原始内容和信封（最近 30 天） | `ingestion.db` 的 `ingestion_inbox` | `enqueue()` | worker、dashboard |
| 单元的每张卡结果、去向、理由、状态（最近 30 天） | `ingestion.db` 的 `ingestion_units` | worker | worker、dashboard |
| 30 天以前的统计 | `ingestion_daily_stats` | maintenance lane，在删除前汇总 | dashboard 的历史趋势 |
| 输入事件和它的 trust | `data.db` 的 `session_inputs` | 消息入口、工具结果入口 | `enqueue()`、dashboard |
| 每个 lane 的到期时间 | `ingestion_lanes` 表 | worker | 触发点、调度 |
| 谁是 worker | owner 锁 + `ingestion_owner.epoch` | 完整的 backend 启动时 | 每一次状态推进 |
| 写入进行到哪一步 | `ingestion_writes` | writer、`governed_edit` | 启动时的恢复 |
| supersedes / reinforces / derived_from 关系 | `ingestion_relations` | writer | 读取侧、卡 3、Retro |
| 关键事件 | `.context/TIMELINE.md` | writer（`event` 单元）、maintenance（月度摘要） | 注入、recall、Retro |
| 一周工作记忆 | `Knowledge/DailyActivity/<date>.md` | DailyActivity 生成器（session 结束、日终汇总） | 注入（§5.6）、人 |
| 配额的预留和扣减 | `ingestion_quota` | 卡 0、writer | 卡 0、dashboard |
| 详情访问日志 | `ingestion_access_log` | worker（异步） | 审计 |
| 唤醒凭证 | `<app data>/state/ingestion_wake.key`（权限 0600） | owner 启动时 | CLI、hook 子进程 |
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
6. 实施分三个阶段：A 离线 go/no-go → B 引擎上线，所有 source 一次切换 → C 注入预算。每个阶段只有一道验收门（r15.1）。
7. holdout 由 3 个盲评 subagent 独立标注，按多数票定标签（r14）；三票都不同的标为 `unresolved` 交给 Gawan 裁定，另做 60 条必做的分层抽检（r15）。
8. 首版的删减（r15 扩充）：首版不做 `extends`，`ddd_orchestrator` 只读；最小关系表、不做语义复审、mutation 检查只覆盖安全类不变量、dashboard 只做审计闭环、不做类型对照、source 用静态类型注册（r14）。
9. 整个设计定名为 **Swarm Cognitive Distillation Flow**：它是 SwarmAI 认知系统的核心引擎，也是对所有用户可用的产品级能力（r14.1）。
10. `ingestion.db` 是不可替代的状态，纳入备份和恢复；holdout 里标为 `unresolved` 的样本和分层抽检由 Gawan 裁定（r15）。
11. r16（Gawan 2026-10-27）：**第一准则：提取的所有认知内容，人和 AI 都能读懂，任何环节不截断语义**（§5 开头）；source 收拢为 6 个，按证据来源划分；提取即按三层七类分类，带 what/how/why 和逐条证据；复利只累积有判断力、有 taste 的认知，重复改为独立证据强化（Flow 里显示"强化"）；三层记忆：DailyActivity 一周工作记忆、TIMELINE 单独成文件供 Retro、语义记忆；S3 是给 DDD 的五段知识报告，run 统计留在 run 里；以上问题解决前不合入 main。

## 18. 修订记录（非规范，仅供追溯，不按这里的旧说法实现）

- **r1 到 r12（已被 r13 取代，只留摘要）**：
  - r1 到 r3：从只管 Knowledge，扩展到 Memory、DDD、Knowledge 三块共用一份契约，并引入答题卡。
  - r4 到 r8：只写用户的 `.context/`；全自动，加上审计；已经沉淀的内容一律不动；ontology 标签。
  - r9 到 r12：卡的维度和路由表、7 种形状、两个纯函数、来源和污染、基线、注入预算，经过四轮独立评审。
  - r12 里为"多个写入者各自当场写"设计的准备→提交、租约、三层强制、分布式配额计数，都已经作废；不要按这些说法实现。原文在 git 历史里可以查到（`99bcc4ea3` 之前的版本）。

- **r16**（阶段 B E2E 之后的重设计）：§5.9 目的地反推和时效分档；§5.10 参考资料两层索引，Knowledge 文件夹分为受管和不受管，`reference_kind` 扩为五种并与实际文件夹对齐；§5 重写（三层记忆、6 个 source、认知单元、各 source 提取规范、S3 五段报告、TIMELINE、DailyActivity 一周注入、强化）；§9 新增 `observed`，删除整会话污染兜底；§7.3 关系扩为三种；§4.2 新增终态 `reinforced`；§14 阶段 B 增加 source 重写、holdout 重做、逐 source 真实链路 E2E 和 daemon 冒烟。
- **r15.3**（Gawan：先 E2E 后调优）：新增 §14.1 调优待办 T1–T7；go/no-go 改为 rebuild 上线前的门槛，不再作为阶段 B 开工的前提。
- **r15.2**（阶段 A 执行中的发现）：§5 加入各 source 的实测该写比例，逐行切片的提取方式停用；§9 和阶段 B 明确 provenance 是切换的前置条件；卡 3 的证据强弱和 observed_at 改由 pipeline 按 anchor 填入；§14 补充 holdout 标注协议（排除自身来源、污染兜底不进 holdout、正例补抽的冻结规则、判官 prompt 的契约要求）。
- **r15.1**（Gawan：执行计划不要拆得太碎）：§14 合并为三个阶段：A 离线 go/no-go；B 引擎上线，所有 source 一次切换，观察 7 天后统一验收；C 注入预算。逐个迁移 source 的计划同时作废，原因是它和"只要有一个 source 上了 Inbox，就不能再有非 writer 的自动写入"这条不变量冲突。
- **r15：吸收 r14.2 的两份独立评审**（`review_r142_code.md`、`review_r142_design.md`），并按 Gawan 的两项决定修改：`ingestion.db` 是不可替代的状态，要纳入备份和恢复；holdout 中标为 `unresolved` 的样本和分层抽检由 Gawan 裁定。具体改动：
  - 文件副作用也受 owner epoch 约束：每一步执行前先抢占这一步；旧 owner 停下并 join 完成之后，才释放锁；新 owner 先接管遗留任务，再开始处理。
  - 删除"检测到外部编辑后自动恢复用户版本"的做法，改成：保留现状，标记 `DEFER user_race`，保留恢复副本；同时明确写出检测不到的那段时间窗口。
  - 新增 materializer，作为唯一的落盘接口，运行时判断路径是否受控；静态检查只禁止绕过它直接调用写函数。
  - 首版禁止目录级操作；项目 rename 和 delete 改走树级写入计划。
  - 写入日志的顺序改为先归档、再写目标、再写关系、最后更新基线；计划里保存预期的字节和哈希，以及必须存在、必须不存在的锚点；恢复时检查所有后置条件，不能只看 `cand:` 标记；用户编辑也走同一套日志。
  - 补全状态转移表（加上 `merged`、owner 接管）；明确候选状态如何聚合。
  - 唤醒改用专用的 0600 凭证，并且只接受 loopback 来源。
  - `ingestion.db` 的 PRAGMA 在事务之外设置；明确 `synchronous=NORMAL` 和 `data.db` 不一致；`incremental_vacuum` 按 `freelist_count` 执行。
  - 补上漏掉的写入者：项目 CRUD、Library 清理和 Inbox、eval governance、`s_ddd-persist`。
  - 修正 DDD 段名，改为和模板逐字比对。
  - `project_candidates` 在卡 1 之前算好；`reroute_to` 写进卡 1 的 schema。
  - refine 不再原地修改单元，改为新的有效单元加 `supersedes`；首版去掉 `extends`。
  - `record_session_input()` 成为唯一入口。
  - 配额预留单独建流水表；访问日志改为异步写入。
  - holdout 按簇抽样，`unresolved` 交给人裁定，分层抽检 60 条且必做，最多换一次 holdout 并预先分配 α；"预注册分母"附上实测功效；污染零容忍改由测试保证；明确稳定率的定义和完整的版本向量。
  - 删减：`ddd_orchestrator` 只读；去掉分类器不一致的统计；基准数据挪到附录 A；修订记录压缩成摘要。
- **r14.2**（Gawan）：CDF 的表改放在独立的 `ingestion.db`，`session_inputs` 留在 `data.db`。理由是和 chat 的写锁隔离，并且新库可以开启 `auto_vacuum`，这样代码评审 B4 不再成立。附上实测的入队延迟。受控语料仍然是文件系统。
- **r14.1**（Gawan）：整个设计定名为 Swarm Cognitive Distillation Flow（CDF），定位为 SwarmAI 认知系统的核心引擎、产品级能力；文件从 `2026-10-26-recall-corpus-ingestion.md` 改名为 `2026-10-26-swarm-cognitive-distillation-flow.md`；明确了 CDF、ingestion、Flow 视图三个术语的边界。内容不变。
- **r14：吸收 r13b 的两份独立评审**（`review_r13b_code.md`、`review_r13b_design.md`）。新增：唯一 owner 锁和 epoch；写入日志和重启恢复（单写入者也需要）；`governed_edit` 和编辑器 revision；外部编辑只做到尽力而为（前后快照，检测到冲突就恢复用户版本）；`ingest` CLI 分支和 loopback 唤醒；idle 触发移到 hook 链的最后；公平调度和 maintenance 保底时隙；负载状态沿用代码的 `warning`；单元级状态、`failed_poison`、稳定的 `idem_key` 和 UNIQUE 约束；清理按整天切、累加汇总；`auto_vacuum` 只对新库生效；整理阶段改为 claim-group，DDD 的 why 不会被拆开；改投从 `aggregate_hits` 里移出，作为独立的一步；污染约束在卡 2 之前硬性执行；"更新"只认证据事件时间；写入者清单改为扫描生成，补上 6 处遗漏，旧修改路径在第 1 步全部关闭；基线不会因为 unknown 修改自动修订；审计接口的隔离、脱敏和上限写进正文；第 0 步改为 dev / holdout 分离，按 95% 单侧 Clopper–Pearson 预先定好分母和门槛，holdout 由盲评 subagent 标注。删减：最小关系表代替图 v2，不做语义复审，不做跨项目边。
- **r13b**（Gawan）：Inbox 清理简化为只保留 30 天，删除前汇总进 `ingestion_daily_stats`；dashboard 对超过 30 天的数据只显示统计；dashboard 定名为 Cognitive Distillation Flow（认知蒸馏流）。
- **r13：提取与 ingestion 解耦**（Gawan 提出）。source 只负责提取和入队，Inbox 有保留期并定期清理，ingestion worker 是唯一写入者；标准答题卡之外，每类目的地有定制规则（DDD 必须保住 what、how、why）；source 可以有定制的提取格式；按时效性分 lane 触发；统一 dashboard 放在 Need You overlay（「认知蒸馏流」页，加上 `AttentionAuthority` 的 ingestion 异常）。r12 里为多写入者并发设计的准备→提交、租约、跨进程配额、三层强制全部删除。DDD 注入方式和 Library 过滤不改（依据第 1 步的盲评结果）。新增第 0 步：先做离线 go/no-go。

## 附录 A. SQLite 基准（r14.2，2026-10-26，scratch）

在 WAL 模式下同时跑三路写入：chat 每秒写 20 条 4 KB 的消息；每 0.5 秒提交一个 500 行的批量事务；做 2,000 次 2 KB 的入队。测得入队延迟 p50 = 0.05 ms、p90 = 0.09 ms、p99 = 0.22 ms，最大 8.4 ms。三路写在同一个库里，SQLite 也不是瓶颈。分库是为了隔离锁和生命周期，不是为了吞吐。

## 19. 验收结果

（实施后填写。）
