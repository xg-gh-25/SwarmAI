---
title: Hive 的 run 记录和 worker 角色 —— 一个 run 的产物封存一次、取回一次，Hive 不再自己长大脑
created: 2026-10-29
updated: 2026-10-29
status: accepted (r3), step 1 implemented
project: SwarmAI
extends:
  - docs/design/2026-10-28-project-agent-flow.md（第 7–10 步：派发、记录、代码回来、经验回流）
  - Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-27-swarm-out-hive-hld.md §5.2（worker 角色）、§17.4（封存记录与导入）、§17.5
---

# Hive 的 run 记录和 worker 角色

**r4（2026-10-29）：** 第 2–4 步已经实现。按实现和两轮评审（correctness、security，第二轮加 concurrency），和上一版不同的地方：
- **`turn` 的封存点：** 不在 `_write_turn_outcome` 里。放在里面会推迟这一轮的实时发布，还多出一个取消窗口。现在改成 `writer.close()` 之后（`close` 被取消也照样封存），`SessionRouter._spawn_turn_seal` 另起一个任务。同一个会话的轮次按锁排队，锁按引用计数清理，有人在等就不清。
  - **这一轮从哪一行开始：** 记录目录里的 `sealed-turns.json` 记下上一个 `turn` 条目的最后一行，这一轮从那之后开始。不再看 outcome 标记：outcome 可能没写成，而 `_outcome_recorded` 在写之前就已经置位。读不到行的一轮，封存成 `turn{error}`，标记不动，它的行由下一轮带上。
  - **这一轮到哪一行结束：** `until`（这一轮结束的时间，和消息表用同一个本地钟）把后一轮的行挡在外面。
  - **哪一轮是会话开头：** 标记的那一行已经不在读回的行里（行被删了，或者一轮超过 `TURN_ROWS`），这一轮就不算会话开头。
- **`bundle` 的封存：** `hive_bundle.make` 只把 bundle 写成记录目录里的临时文件。`_task_repo_facts` 等线程返回以后，由 `hive_bundle.seal_bundles` 封存。线程超时之后才回来的结果不封存（I9）。
- **bundle 不重复封存：** 每个 bundle 都装着整条分支。所以一轮里 `base..head` 没变时，沿用已经封存的那个（`sealed-bundles.json`），不再封一份。本机取回之后，删掉最新那个带仓库事实的 outcome 已经不引用的旧 bundle 文件（清单行留着），它之后封存的不删。预算只算还在盘上的文件。
- **本机的预算（security HIGH）：** 一个 Hive 可能是坏的，也可能是恶意的。所以每个派发最多存 2000 个条目、300 MB。清单最多读 256 KB，按流读，到上限就停。一个文件最多读到清单报的大小。一次 pull 最多 120 秒。`records-sweep` 两端都清：Hive 上清 30 天没动过的会话目录，本机清 30 天没动过的派发记录（这之后，没落地的代码和没收下的教训就取不回了，和 Hive 上的保留期一致），两边都会清过期的临时文件。时间是工作日 UTC 05:30，遵守任务的时间窗规则。
- **记录不全时的结果：** 只有记录完整（第一条 `turn` 是会话开头、没有 `turn{error}`），结果汇总才读记录。不全就回到消息接口。但只要有记录，就不再走 `take`。
- **D3 收下教训：** `take` 只把老 Hive 的教训存到本机，不入队。
  - **`/runs` 给什么：** 等待中的单元的 digest。这些是本机算的哈希，不带 Hive 的文字。
  - **`lessons/accept` 收什么：** 请求带上这组 digest，只入队人看到的那些。面板渲染之后才到的单元，等下一次收下。
  - **一次收下：** 入队和保存记录是一个 shield 住的步骤，请求断开也不会出现"入了队、没记下"。出错时返回 `not_queued`。
  - **界面：** `LessonsAccept` 按钮只有在返回没有错误、也没有剩下等待的单元时，才显示"已交给判官"。
- **还没定（交给 XG）：** 收下和落地一样，现在只有界面这一道门。本机的 agent 能用 curl 调本机接口，替人点了"收下"。代码层面的"只有人能点"的门，和落地的同一个问题一起定。
- **旧通道还没删：** 第 2–3 步表里"删掉什么"那一列，全部留到第 5 步。老 Hive 还要走回退，要等两台 Hive 都更新了才能删。
- **已知限制：** `seal_bundles` 进行中如果被取消，会留下一个孤立的临时 bundle，由 sweep 清掉。

**r3（XG 2026-10-29）：** D1–D4 按建议定（默认 worker、关 DailyActivity、人收下才交判官、保留 30 天）。第 1 步已经实现，按实现和它的评审改了 §3：
- **expiry 清理的位置：** 不放在调度器任务里，放进 `WorkerHandle` 自己的每日维护线程。
- **每轮的检查点：** 它也会写 DailyActivity，还会留下一个崩溃检查点，下一个会话的 resume 上下文会读它。所以在 worker 上不注册，也不读检查点。
- **启动失败的条件：** 只有拿到了 owner 锁、抽取却没启动起来时，才拒绝启动。锁被另一个活着的后端占着时照常启动，因为抽取由那个后端在做。

**r2（2026-10-29）：** r1 经过一次零上下文的前提审查（correctness），对着源码核实。审查结论：问题 A–C 都是真的，r1 引用的代码位置基本准确。审查找出 3 个 HIGH 和 6 个 MED，这一版按原因一次改完：

1. **封存点不一定会被执行到（HIGH）：** 会话任务失败、根本没排上、或者 pipeline 在会话复盘之后才落地，`closed` 和 S3 教训就永远不会封存。改成：教训按"还没封存过的"增量封存，有两个触发点（会话任务到终态、pipeline 落地）。`closed` 在会话任务的每个终态都写。本机再加一个按时间的兜底（§4.2）。
2. **开关放错了位置（HIGH）：** 在 lifespan 启动 CDF 的地方判断，会把 S2/S7 抽取一起关掉。收件箱的过期清理也不在 r1 说的地方。改成在 `WorkerHandle.start` 里判断（§3.2）。
3. **I1 的范围不对（HIGH）：** 每个会话的 prompt 组装都会往 `.context/` 写模板和缓存。改成只看受治理的认知文件，派生缓存按名字排除（§8 I1）。
4. **按轮封存的细节缺口（MED）：**
   - 打包线程超时后还会接着写，所以等线程结果回来再封存。
   - 封存失败要留下一个看得见的空位。
   - 一轮封存的内容改成定义好的投影，不存原始消息行，因为消息行写入之后还会被修订。
   - `prepare` 用哪个 bundle 要写清楚。
5. **落地顺序（MED）：** 审查提出可以先只封存教训，所以 §8 改成先做最小的一步。

## 0. 一句话

Hive 改成纯执行者（worker 角色）：不写自己的大脑，只把一个 run 的产物（每轮的结果、代码 bundle、教训）按顺序封存进一个只追加的 run 记录。本机按游标把记录取回来，核对一次，存在本机。之后结果面板、代码落地、教训回流都只读本机这份记录。

能删掉的：三条各自校验的取回通道、24 小时取回窗口、"Hive 改 Brain、下次同步再还原"这一层。

## 1. 现在的问题（2026-10-29 读代码核实）

**问题 A：一个 run 的产物分三条通道取，每条各验一遍。**

| 产物 | Hive 端 | 本机端 |
|---|---|---|
| 结果（回复、turn_outcome、仓库事实） | 会话消息接口 `routers/chat.py:1082` | `swarm_out_results._fetch` 用 etag 取最近 20 轮（`swarm_out_results.py:41,491,508`） |
| 代码 | `hive_bundle.make` 写 `Hive/bundles/<session>/<key>.bundle`（`hive_bundle.py:29,57`），下载接口 `routers/hive_brains.py:121` | `hive_return._download`（`hive_return.py:187-199`），自带一套大小和 sha256 检查 |
| 教训 | `hive_lessons.collect` 现读 Hive 的 CDF 收件箱（`hive_lessons.py:89`），接口 `routers/hive_brains.py:146` | `hive_lessons.take` 在取结果之后顺带调（`swarm_out_results.py:529-532`），自带一套上限和错误码 |

后果：
- **同一类防御写了三遍。**
- **"产物齐了"没有一个时间点。**
- **结果只看得到最近 20 轮：** 所以才有 `checks_partial` 和 `older_turns_not_read`。

**问题 B：取回靠一个从派发时刻算起的 24 小时窗口。**
- **窗口从派发时刻算：** `check_due` 用 `sent_at` 算年龄（`swarm_out_results.py:43,586-596`），不是从 run 停下算。一个跑了超过 24 小时的 run，停下后只会被检查一次（`kept is None` 那一次）。
- **晚到的教训取不到：** Hive 的会话复盘（S7）在会话关闭 8 分钟后才排队（`s2_jobs.py:31`），复盘本身还要跑一段时间，所以经常赶不上窗口。
- **会话没了，教训也拿不到：** Hive 上的会话被删以后，取消息返回 `session_gone`。而教训只在取消息成功之后才取（`swarm_out_results.py:529`），所以教训也拿不到了。

**问题 C：Hive 是一个完整的大脑，它写的东西事后被撤销。** 代码里没有任何认知写入路径检查 hive 模式（HLD §5.1，代码一致）。具体有：
- **CDF 判官和写入器照常启动：** `main.py:1414-1431` 启动一个 `WorkerHandle`，它同时拉起判官和写入器线程、run lane 和 S2/S7 抽取（`worker.py:880-902`）。判官把 Hive 自己抽出的教训写进 Hive 那份 Brain、MEMORY、EVOLUTION、Knowledge（`writer.py:721-777`）。
- **DailyActivity 照常写：** 会话关闭时由 `DailyActivityExtractionHook` 写（`daily_activity_writer.py:573`）。每轮的高信号捕获也写（`runtime_hooks.py:1645`）。启动时的崩溃恢复会把检查点补写进去（`main.py:1250-1256`）。每轮的检查点本身只写 `state/session_checkpoint.json`（`runtime_hooks.py:460`）。
- **跨派发任务的串扰：** 同一套 prompt builder 会把 Hive 的 DailyActivity 注入下一个会话（`prompt_builder.py:1342-1345`）。所以上一个派发任务的文字（可能被注入过）会进入下一个派发任务的上下文。
- **调度器照常跑写认知的任务：** daemon 和 hive 模式都会启动调度器（`main.py:1473`），其中 `ddd-refresh`、`ddd-self-audit`、`memory-health`、`evolution-cycle`、`signal-digest`、`skill-proposer` 等会往认知存储写东西（`jobs/system_jobs.py`）。
- **Brain 同步只能事后补救：** 同步时把白名单文件覆盖回来（`brain_sync.install`，`brain_sync.py:365-399`），并统计 Hive 改过几个文件（`installed_state`，`:402-419`）。这个计数混着两种来源：系统自己写的，和 agent 在任务里改的。

**问题 D：HLD §17.5 指出的注入入口，第 10 步还开着。** 导入的教训经判官后会自动写进项目 DDD，而判官只看质量，不是安全边界。第 10 步给这些教训加了外部标记，它们进不了每轮注入的文件，但 DDD 里的内容仍会被 recall 进本机以后的会话。所以这仍然是一个持久的注入入口。

## 2. 目标和不做的

**目标：**
- **G1：** worker Hive 上跑完一个会话（含 pipeline、会话复盘、调度器）之后，受治理的认知文件字节不变（范围见 §8 的 I1）。
- **G2：** 一个 run 的产物按顺序封存在 Hive 上的同一个地方，追加之后不再修改。
- **G3：** 本机按游标取回，每个文件只核对一次，存在本机。之后所有读取都只读本机这份。
- **G4：** "取完了"由记录本身说明，不靠时间窗。只有在记录说不清的时候，才用时间做兜底。
- **G5：** 不可信的规则不变：Hive 的文字不进 `/runs`，代码要人批才落地，教训只写进这个 run 的 Brain、带外部标记。

**不做：**
- **签名和真伪：** sha256 只能发现损坏和串号（同 HLD §17.4）。
- **用 S3 当通道：** 继续用 HTTPS 和 `hive_auth`。
- **其他：** branch-pr（9b）、并行拆解、参考实例的行为。

## 3. 设计 A：worker 角色

### 3.1 开关

新增环境变量 `SWARMAI_ROLE`，只在 `SWARMAI_MODE=hive` 时读：
- **`worker`：** 本设计描述的行为。
- **`reference`：** 和现在一样，公开展示用。

默认值见决策 D1。角色只在启动时读一次。所有地方都调同一个函数 `core/app_role.py: is_worker()` 来判断。

worker 不能没有抽取线程：拿到了 owner 锁、抽取却没能启动时，worker 拒绝启动并报错。没有抽取，就没有教训，也就没有 `closed`。owner 锁被另一个活着的后端占着时照常启动（和现在一样只记一条 info），因为抽取由那个后端在做。

### 3.2 worker 上关什么、留什么

| 路径 | worker 上 | 为什么 |
|---|---|---|
| S2/S7 抽取（`S2JobExecutor`，`worker.py:814-877`）。S3 在 pipeline 落地时入收件箱（`reflect_job.py:24-36`） | **留** | 教训从这里来 |
| CDF 判官和写入器：`IngestionWorker`（含 summary lane 和启动时的 `recover`、`requeue_*`，`worker.py:613-697`），以及 `RunLaneExecutor`（`:733-788`） | **关**。在 `WorkerHandle.start`（`worker.py:898-902`）里判断：worker 只启动 `S2JobExecutor` | Hive 不写自己的大脑。没有写入器，也就没有日志需要 `recover` |
| 收件箱过期清理 `cleanup_expired` | 现在只在 `IngestionWorker.run` 启动时调（`worker.py:620-629`）。worker 上由 `WorkerHandle` 的维护线程在启动时和之后每天调一次。顺带修了一个问题：一直没被拆成单元的候选（worker 上全是这种）过期时会撞 NOT NULL 约束，统计也会少算 | 收件箱不会无限增长 |
| DailyActivity 的写入和读取：会话关闭、高信号捕获、每轮检查点（它也往 DailyActivity 写一块，并留下 `state/session_checkpoint.json`）、启动时补写，以及 prompt 里的注入和 resume 上下文里的崩溃检查点 | **全关**（决策 D2）。分别在 `core/session_hooks.register_default_hooks`、`runtime_hooks.register_runtime_hooks`、`main.py` 的崩溃恢复、`prompt_builder` 的 DailyActivity 注入、`context_injector._build_checkpoint` 里判断。worker 上，启动时补跑会话钩子的扫描改成跳过已经有 S2 任务的会话 | 去掉跨派发任务的串扰：那里的"上一个会话"是另一个派发任务 |
| 会话关闭钩子 | 只留 `SessionExtractionHook`（排 S2/S7 任务）和 `IngestionSessionKickHook`。关掉：`ContextHealthHook`（deep pass 写 `AGENTS.md`、搬 DailyActivity，`context_health_hook.py:1293-1303,2063-2091`）、`WorkspaceAutoCommitHook`、`EvolutionMaintenanceHook`、`TodoLifecycleHook`、`SkillMetricsHook`（往 SkillEvals 写纠错，是 evolution 的输入） | 这些钩子写的是 Hive 自己的认知、工作区，或者 Hive 上没人用的状态 |
| 调度器 | 照常启动，系统任务改成白名单（`jobs/scheduler.WORKER_SYSTEM_JOBS`）：`worktree-sweep`、`log-patrol`、`session-health-probe`，第 2 步再加 `records-sweep`。用户任务也不跑。新加的系统任务默认不在 worker 上跑 | 运维要留，写认知的不跑。用白名单，新任务默认关 |
| 每轮的观察类钩子：纠错捕获、子 agent 捕获、错误模式检测（写 `state/corrections.jsonl`，`runtime_hooks.py:194,303,1326`） | **留** | 写的是 `state/` 下的运行数据，不是认知存储。它们的读者（evolution）在 worker 上不跑 |
| prompt 组装时写的 `.context/` 模板和 L1 缓存（`context_directory_loader.py:747-819,912,1359-1379`） | **留** | 派生缓存，每次都从模板重建。I1 按名字排除它们 |
| bridge、Brain 安装、会话、钩子、gate | **留** | 执行面本身 |

**完整会话的写入快照放到第 5 步：** 一次完整会话（pipeline 引擎、`run_knowledge`、code intel、skill 投影、S7 复盘用的 fork 会话）只能在真机 Hive 上跑，单元测试起不了 CLI。所以第 1 步的测试逐个覆盖上表的每个判断点，I1 的全会话快照在第 5 步的真机端到端里做。快照里出现表外的写入，要么补进上表，要么说明为什么可以留，不能靠扩大 I1 的排除范围消掉。

### 3.3 Brain 漂移计数的新含义

系统自己不再写 Brain 之后，"Hive 改过的文件数"就只剩一种来源：**agent 在任务里改了 Brain 的文件**，比如 pipeline 在 `2-understanding/knowledge/` 下写了一份设计。
- **控制台：** 这一项改成"自上次同步以来，有 run 改了 Brain 的 N 个文件"，作为告警，列出这段时间在这台 Hive 上用过这个 Brain 的派发（本机的派发记录里有 Brain、Hive 和时间）。`brain_use` 只按 turn 计数，不记是哪个 run，所以做不到更细的归属。
- **同步：** 照常还原。
- **不加新的拦截：** Hive 上把文件改成只读的方案之前已经否掉了。

## 4. 设计 B：run 记录

### 4.1 布局（Hive 上）

```
Hive/records/<session>/
  manifest.jsonl          # 一行一个条目，只追加
  000001-bundle-<key>.bundle
  000002-turn.json
  000003-lessons.json
  000004-closed.json
```

一行条目长这样：

```json
{"seq": 3, "kind": "lessons", "file": "000003-lessons.json", "sha256": "…", "size": 1234, "at": "2026-10-29T03:00:00Z"}
```

- **写入顺序：** 先把文件写成临时文件，fsync 后 rename。最后才往 `manifest.jsonl` 追加一行并 fsync。清单里有的，文件一定已经完整。
- **序号：** `seq` 在会话目录的文件锁里分配，严格递增，不会重复写。
- **文件名：** 只由 `seq`、`kind` 和本机算的 bundle key 组成。

### 4.2 条目种类和封存时机

| kind | 封存时机 | 内容 |
|---|---|---|
| `bundle` | 每轮结束时，等打包线程的结果回来以后，由调用方的协程封存。`add_bundles` 改成只返回临时文件的路径，不再自己写进 `Hive/bundles/`（`session_router.py:4280-4290`）。线程超时后才返回的结果直接丢掉，不封存 | 一个仓库这一轮的 bundle |
| `turn` | 紧接着这一轮的 `bundle` 之后，在 `_write_turn_outcome` 里（`session_router.py:4213`，正常路径 `:4175`，异常和取消路径 `:4190`）。用 `asyncio.shield` 保护，和 outcome 的写入分开。封存失败时补一个 `turn{error}`，让这个空位看得见 | 一个定义好的投影，不是原始消息行：`turn_outcome` 块、仓库事实、gate 拦截数、本轮引用的 `bundle` 序号、本轮的工具调用（工具名、Bash 命令、`is_error`）、本轮最后一段回复（有长度上限）、token 和成本 |
| `lessons` | 增量封存：每次封存时取出这个会话和它创建的 run 在收件箱里**还没封存过的**教训单元（筛选规则同现在的 `hive_lessons.collect`），封存过的收件箱 id 记在会话目录里。两个触发点：会话任务到终态时（`S2JobExecutor.drain` 里 `s2_jobs.finish` 返回 `done` 或 `failed` 之后，`worker.py:858-865`，`s2_jobs.py:123-135`），以及 pipeline 落地时（`reflect_job._process_run` 末尾，`reflect_job.py:24-36`） | 教训单元，最多 50 条，每条最多 8 KB |
| `closed` | 会话任务每次到终态都写，紧跟在 `lessons` 后面。会话被删除时也写（`SessionExtractionHook` 不排任务的那条路径，`s2_session.py:404`） | `{"reason": "reviewed" \| "review_failed" \| "deleted"}` |

**`closed` 的意思是"到目前为止齐了"，不是"永远不会再有"。**
- **会话被续上：** 比如本机回答了它的问题，会接着追加 `turn`，之后再来一组 `lessons` 和 `closed`。
- **pipeline 在复盘之后才落地：** 会追加一个 `lessons`，跟在 `closed` 后面。

**本机按时间兜底：** 记录最后一个条目超过 14 天没有新条目，并且派发行是停下的，就不再拉。这只用在记录说不清的时候，比如会话任务一直排在 admission 后面（`worker.py:853-854`）。

### 4.3 接口（Hive 上，只读，只在 hive 模式开）

- **`GET /api/hive/records/{session}?after=<seq>`：** 返回 `seq` 大于游标的条目，最多 100 条，外加 `more: bool`。
- **`GET /api/hive/records/{session}/{seq}`：** 返回一个文件，不跟随软链，按清单登记的大小读。

这两个接口替代 `/api/hive/bundles` 和 `/api/hive/lessons`。消息接口留着，给人在 Hive 网页上看。

### 4.4 本机导入（读取方先定，再写写入方）

新增 `core/hive_records.py: pull(row)`：
1. **问清单：** 按本机存的游标问清单。
2. **逐个取文件，只核对一次：**
   - **大小上限：** `turn` 1 MB、`bundle` 100 MB、`lessons` 512 KB、`closed` 4 KB。
   - **sha256：** 和清单一致。
   - **种类：** kind 在白名单里。
   - **序号：** `seq` 连续不跳号。
   - **全部通过才存：** 写进 `swarm-out/records/<dispatch>/`，再前移游标。
3. **核对失败就停：** 游标不动，记一条错误，下次再试，已经存下的不动。清单本身也不可信：它可以少报，但多报的条目过不了上面的检查。

三个读取方都只读本机这份记录：

| 读取方 | 现在读 | 改成读 |
|---|---|---|
| 结果汇总 | 消息接口的最近 20 轮，交给 `summarize` | 新的 `summarize_records`，读全部 `turn` 投影。校验比对用投影里的 Bash 命令，规则和现在的 `_checks` 一样。`checks_partial`、`older_turns_not_read`、etag 都可以删。投影之后才发生的消息修订（比如一个问题后来被回答了）不在记录里，只影响面板上的回复文字 |
| 代码落地 `hive_return.prepare` | 下载 bundle | 用最后一个已封存的 `turn` 为这个 key 引用的那个 `bundle`。本地还没有这个文件时报"还在取回"。原来的"已过期"分支删掉，因为封存的 bundle 不会被覆盖。其余核对（单一 ref、前置提交、fsck、`refs/hive/`、受保护路径）一条不少 |
| 教训 `hive_lessons.ingest` | `take` 现取 | 本机记录里所有的 `lessons` 条目。规则（scope、外部证据、锁定 Brain、预算、按 digest 去重）一条不少，入队时机见决策 D3 |

**什么时候拉：**
- **触发：** 沿用 `/runs` 轮询时启动后台检查的方式。
- **停止：** 最后一个条目是 `closed`，派发行也停下了，就不再拉，也不用再看 24 小时。派发行重新动起来，就接着拉。
- **成本：** 没有新条目时只是问一次清单。

### 4.5 Hive 上的保留期

记录保留 30 天（决策 D4），由调度器白名单里的 `records-sweep`（第 2 步新增，工作日 UTC 05:30）删掉过期的会话目录。会话被删不影响它的记录。

### 4.6 迁移完成后删掉什么

- **Hive 端：**
  - `/api/hive/bundles`、`/api/hive/lessons`。
  - `Hive/bundles/`。
  - `hive_bundle.add_bundles` 直接写盘的部分。
- **本机端：**
  - `swarm_out_results._fetch` 里取消息、etag、`FETCH_TURNS` 那一套。
  - `RECENT_S` 和 `check_due` 里的时间判断，换成 §4.4 的停止条件。
  - `checks_partial` 和 `older_turns_not_read`。
  - `hive_return._download` 和"已过期"分支。
  - `hive_lessons.take`。
- **控制台：** "Hive 改过的文件数"改成 §3.3 的告警。

## 5. 安全

- **Hive 的内容仍然不可信：** 记录只解决取回，不提升信任。每个条目在本机只检查一次，检查项和现在一样。sha256 只能发现损坏和串号。
- **路径：** Hive 上的文件名和本机的存放路径，都只由序号、种类和本机算的 key 组成。
- **清单：** 清单不可信。它可以少报，导致本机少拿东西，但多报的条目会被本机的检查挡下。
- **问题 D：** 由决策 D3 处理。

## 6. 迁移（strangler-fig，R26）

1. **本机先上读取方和回退：** 先问 `/api/hive/records`，返回 404（老 Hive）就走现在的通道。
2. **Hive 再上：** 两台 Hive 更新后，用真机跑一遍 §8 第 5 步的端到端测试。
3. **最后删旧通道：** 按 §4.6，一个提交只删一类。

## 7. 要你定的

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| D1 | hive 模式默认是什么角色 | A：默认 `worker`，参考实例要显式写 `reference`。B：默认 `reference`，worker 要显式写 | **A。** 现在真正接活的 Hive 都是 worker。默认参考实例的话，忘了配就会悄悄继续长大脑 |
| D2 | worker 上关不关 DailyActivity | A：关。B：留着给排查用 | **A。** 留着会把上一个派发任务的文字注入下一个任务。排查可以看会话、run 目录和本机的记录 |
| D3 | Hive 的教训什么时候交给本机判官 | A：现状，取回就交。B：人收下才交：每个有 `lessons` 条目的 run，结果详情里都有一个"收下教训"按钮，和代码落不落地分开（代码被拒了，教训照样可以收）。没收下之前，`/runs` 显示"有 N 条教训待你收下"。C：自动交，写进 DDD 时标"来自 Hive、没经人看"，recall 时带上这个标注 | **B。** 不可信来源写进持久认知，是一个只有人能回答的问题。人反正要看这个 run 的结果，多点一下成本很低。C 要新做一套标注和 recall 渲染，而且只能提醒，挡不住 |
| D4 | 记录在 Hive 上保留多久 | 14 天 / 30 天 | **30 天。** 本机关机两周也取得回来。本机的兜底停止时间（14 天没有新条目）要比它短 |

## 8. 分步落地和验证计划

每一步都是一次 native 改动：带测试、做变异检查、按下表评审、提交、land。顺序按"先做最小、能删东西的一步"排。

| 步 | 内容 | 删掉什么 | 评审 |
|---|---|---|---|
| 1 ✅ | worker 角色：`app_role.is_worker()`，以及 §3.2 的各个判断点（每个判断点一个测试，变异检查全部抓到） | 不再有"Hive 自己写 Brain、下次同步还原" | correctness（2 个 MED 已修：检查点漏关、启动失败条件太宽） |
| 2 ✅ | 记录的骨架和教训：会话目录、清单、`lessons`/`closed` 的增量封存、两个记录接口、`records-sweep`。本机的 `pull` 和教训读取方改读本机记录，老 Hive 回退 | 教训的 24 小时窗口和"会话没了就拿不到"，`hive_lessons.take`、`/api/hive/lessons` | correctness、security |
| 3 ✅ | `bundle` 和 `turn` 的封存，结果汇总和 `prepare` 改读记录 | 取消息那一套、`checks_partial`、`_download`、`/api/hive/bundles` | correctness、security |
| 4 ✅ | 按 D3 的选择改教训的入队时机，同时改第 10 步的测试 | — | security |
| 5 | 两台 Hive 更新后跑真机端到端（含 I1 的全会话快照），按 §4.6 删剩下的旧通道 | 回退路径 | correctness |

**最后要成立的不变量（测试在设计定下时一起写）：**
- **I1：** worker 上跑一个完整会话之后（含一个 pipeline run 和会话复盘），受治理的认知文件 sha256 和之前一致，收件箱里有这个会话的教训单元。
  - **在范围内：** `.context/{MEMORY,EVOLUTION,USER,STEERING,TOOLS,TIMELINE,KNOWLEDGE}.md` 及其 archive、`Knowledge/{Library,Learned,Notes,DailyActivity,Inbox}/`、Brain 的四份 DDD 文档及其 archive。
  - **按名字排除：** prompt 组装的模板和 L1 缓存、run 目录 `.artifacts/`、agent 在任务里写的文件（由 §3.3 的告警负责）。
- **I2：** 清单只追加。同一个 `seq` 不会写第二次。`closed` 之后续上的轮次，以及复盘之后才落地的 pipeline 的教训，从下一个 `seq` 开始写。
- **I3：** 本机导入时，大小超限、sha256 不对、跳号、未知种类的条目都不存，游标不动。下次重试成功后，记录完整。
- **I4：** 同一个条目导入两次，本机记录不变，教训不会重复入队。
- **I5：** Hive 不可达时，三个读取方照样能工作。
- **I6：** 老 Hive（记录接口返回 404）走现在的通道，现有测试全绿。
- **I7：** "Hive 的文字不进 `/runs`""教训锁定在 Brain 上""代码要人批"这三组现有测试，在新读取路径上照样全绿。
- **I8：** 会话任务的每个终态（`done`、三次都失败）和会话删除，都会留下 `closed`。最后一个条目是 `closed` 且派发行停下时，本机不再发请求。派发行重新动起来后，又会拉到新条目。
- **I9：** 一轮的打包线程超时后才返回的结果不会被封存。封存失败的一轮在记录里是一个 `turn{error}`。
