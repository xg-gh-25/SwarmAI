---
title: "全平台用量账本：一个口径、一本账，钱花在哪一眼可查"
created: 2026-10-29
updated: 2026-10-29
status: draft r2（r1 经一轮零上下文对抗审查，15 条 finding 按原因并入本版，见 §12；待确认）
---

# 全平台用量账本

## 0. 为什么要做

接下来要基于历史数据找出 cost 花在哪里，做系统性优化。前提是三件事：每一笔模型调用都在**同一本账**里；token 和花费用**同一个口径**；每一笔都能追溯到**是谁花的**（哪个功能、哪个 session、哪个 run、哪个 job、哪台机器）。现在三件都不满足。

## 1. 现状盘点（读源码 + 读 `data.db` / `ingestion.db`）

| 入口 | 记在哪 | token 口径 | 花费 | 归属 | 问题 |
|---|---|---|---|---|---|
| Chat / channel 的 CLI result（`streaming_orchestrator`：4 处写入、8 条路径，一个用户 turn 可能产出多行：续写、steer、api-error 续跑、leak 续跑） | `token_usage` | 四种，但**只记一个模型**（`_model_name`），没用 SDK 的 `ResultMessage.model_usage` | `total_cost_usd`：进程累计值 | session；channel 和 chat 分不开 | 顶栏曾把累计值相加（已修，`bdd516f91`）。CLI 的旁路调用（如 haiku）和其他模型上的子 agent，都被算在一个 Opus 行里，这就是按单价反推时出现负数的原因 |
| CDF 的 Bedrock 调用（`ingestion/llm.py:66`、`extract_llm.py:95`、`s7_review.py:316`） | **`ingestion.db` 的 `ingestion_llm_usage`**（按 UTC 天 × source） | 只记 input / output | 不记 | CDF source（S2/S3/S7） | 已经是一本按功能归属的账，但和 `token_usage` 分开；当天就有 2,445 次调用、7.3M 输入 |
| 其他直接调 Bedrock（`jobs/bedrock.py` 的其余调用方：session 打分、summarization、eval 判官、signal digest、skill proposer……） | `token_usage`，`source=background_job` | 只记 input / output | 不记 | 没有 | `record_usage` 默认 False，只有 6 处传了 True |
| 其他 CLI 进程：`s7_review` 的 CLI 评审、`ddd_self_audit` 的 `claude --print`、job 结束发 Slack DM 的 `claude --print` | 只有 s7 进 `ingestion_llm_usage`，其余哪都不记 | — | 丢掉 | — | 完全看不到 |
| Agent job（`jobs/executor.py`） | 只进调度器 `monthly_spend_usd` | `modelUsage` 各模型取 max，不是相加 | CLI 报的总额；没有时按 sonnet 单价估 | job | 不进 `token_usage`；进程非零退出时直接 return，失败 run 的花费丢掉 |
| Pipeline run（`run_protocol.usage` / `hook_bridge.record_usage`） | run 的事件 → `run_audit.json`、REPORT | transcript 统计；"总数"不含缓存读，缓存写并进输入；不分模型 | 不记 | run、stage | 报告的总数约为实际计费 token 的 2%；它的 token 本来就在 chat 的 result 里 |
| Swarm Out（Hive） | Hive 自己的库；Host 只有聊天 turn 的 `turn_outcome` 副本 | 四种（`172759169`） | 累计值的增量 | dispatch | Hive 上的 CDF、job、Bedrock 花费 Host 看不到 |
| 读 `token_usage` 的地方 | 顶栏（`get_token_usage_summary`）、在线打分（`token_cost_rows` → `session_quality._turn_costs`）、备份（`backup_manager.L2_TABLES`） | | | | |

可以复用的现成机制：`turn_outcome.cost_increase`（累计值 → 增量）；`ingestion.llm_usage.attribute()`（用 contextvar 给一段代码里的所有 Bedrock 调用打上归属，正好就是"feature"该有的样子）；`transcript_usage.usage_in_window`（按时间窗口统计每条响应，含子 agent）；`PRAGMA user_version` 的迁移框架（`sqlite.py`，当前 v17）。

**一个必须说清的事实：** 这里的"花费"是 CLI 按它自带的单价表算出来的**账面花费**（`ModelUsage.canonicalModel` 用来查价），不是 AWS 账单。区域价、协议价、网关都会让两者有差。账本里叫"账面花费"，和账单对账是另一件事。

## 2. 统一口径（全平台唯一定义，`core/usage_accounting.py`）

一条用量行 = **一个模型**在一次计费调用（或一个 CLI result 的增量）里的用量：

| 字段 | 含义 |
|---|---|
| `input` · `output` · `cache_read` · `cache_write` | 四种 token 分开记 |
| `cost_usd` | **这一行自己的**账面花费，永远不存累计值 |
| `cost_source` | `cli`：CLI 报的（`model_usage[m].costUSD` 的增量）；`priced`：按单价表算；`none` |

派生量只在这个模块里算：**计费 token** = 四项之和；**缓存命中率** = cache_read ÷（input + cache_read + cache_write）；**可比 token** = input + cache_write + output（只给 pipeline 的 Token Cost 判定和跨 run baseline 用，不再叫"总 token"）。

## 3. 一本账：`usage_ledger`（schema v18）

```
usage_ledger(
  id INTEGER PRIMARY KEY,
  at_utc TEXT NOT NULL,              -- UTC；显示时按本机时区换算，跨机器、跨夏令时不乱
  machine TEXT NOT NULL,             -- 'host' 或 Hive 名
  surface TEXT NOT NULL,             -- chat | channel | pipeline | job | background | cdf | eval | hive
  feature TEXT,                      -- 后台调用的功能：cdf.s2 / judge.session / summarize / ...
  session_id TEXT, run_id TEXT, stage TEXT, job_id TEXT, dispatch_id TEXT,
  model TEXT,
  input INTEGER, output INTEGER, cache_read INTEGER, cache_write INTEGER,
  cost_usd REAL, cost_source TEXT,
  cli_pid INTEGER, cli_cost_total REAL,   -- CLI 行才有：复算增量、审计
  kind TEXT NOT NULL,                -- spend（参与求和）| attribution（拆分，不参与求和）
  imported INTEGER NOT NULL DEFAULT 0,    -- 从旧表导入的估算行
  dedup_key TEXT UNIQUE
)
```

- **spend 行是账**，所有"总花费"只加 spend 行。一个 CLI result 按 `model_usage` 每个模型一行，`dedup_key = cli session + cli_pid + 该进程里的 result 序号 + model`。一次 Bedrock 调用一行。
- **attribution 行是拆分**，指向它拆的 spend 行。花费**按 token 占比分摊这些 spend 行的 CLI 花费**，不另外按单价算，所以"chat = spend − pipeline attribution"是同一个基数，不会出现负数。

## 4. 每个入口怎么改

| 入口 | 改成 |
|---|---|
| CLI result（8 条路径） | 收成一个 `usage_accounting.record_cli_result(message, session, pid, surface)`：读 `model_usage`，每个模型的四种 token 和 `costUSD` 都对上一行（同一 pid、同一模型）取增量；没有 `model_usage` 的老 CLI 退回到 `usage` + `total_cost_usd`。channel 的 turn 标 `surface=channel` |
| 所有 Bedrock 调用 | `jobs/bedrock.py` 每次都记（删掉 `record_usage` 开关），四种 token 都取；归属沿用 `llm_usage.attribute()` 的 contextvar 做法，推广成 `usage_accounting.attribute(feature=...)`，CDF 的现有 source 直接映射成 `cdf.s2/s3/s7`；`ingestion_llm_usage` 停止写入，历史导入后保留只读。现在没有一个调用用 `cachePoint`，缓存字段暂时都是 0 |
| 其他 CLI 进程（s7 评审、ddd_self_audit、Slack DM、agent job） | 共用一个 `record_cli_json(output, surface, feature/job_id)`，读 `--output-format json` 里的 `modelUsage` / `total_cost_usd`，**包括非零退出**（有输出就记）；job 的模型用量按各模型相加，不取 max |
| 调度器预算 | 读账本里 `surface=job` 的本月花费，不再另记一份（预算范围不变） |
| Pipeline run | run 结束时由 `usage.measure` 写 attribution 行（run 一行、每 stage 一行），按 transcript 里每条响应的 `message.model` 分模型，再按 token 占比分摊对应 spend 行的花费；`run_audit` 的判定继续用可比 token，报告另列计费 token 和账面花费 |
| Hive | Hive 跑的是同一个产品，它自己也写 `usage_ledger`；Host 通过一个新的只读接口，按 Hive 那边的 `id` 增量拉取（`dedup_key = Hive 名 + Hive 行 id`），所以 Hive 上的 CDF、job、Bedrock 花费都能拉回来。老版本的 Hive 退回到导入 `turn_outcome` |

## 5. 读的一侧（先设计，再写写入方）

| 读的一侧 | 改成 |
|---|---|
| 顶栏 | 今天 / 总计 = spend 行之和；悬停显示四种 token 和命中率 |
| 在线打分 `session_quality` | `token_cost_rows` 改读账本的 spend 行（`cost_usd` 已经是增量，不用再算基线）；同一份夹具，新旧结果一致 |
| Swarm Out 卡片、History | 读账本里该 dispatch 的 spend 行 |
| Pipeline 报告和面板 | 计费 token、账面花费、可比 token、每 stage 拆分 |
| 备份 | `backup_manager.L2_TABLES` 加上 `usage_ledger` |
| **新的"用量"视图**（做系统性优化就看这里） | 按天 × surface / feature / model 的花费；最贵的 session、run、job、功能；每个 feature 的缓存命中率；单次调用平均花费；每行都能点回明细。先出 mockup |

## 6. 单价表（只给没有 CLI 花费的 Bedrock 直接调用用）

- 一个产品配置文件，按模型 × 区域 × 四种 token；用户可以改
- **校准**：每个 CLI result 的 `model_usage` 每个模型都带四种 token 和 `costUSD`，一个模型、一组数，可以直接验证单价表算出来对不对（不用再拿混了多个模型的行去做回归）。偏差超过阈值，用量视图上标"单价表可能过时"
- 表里没有的模型：`cost_source=none`，花费显示"—"，不猜

## 7. 迁移（v18，不改写任何已有数据）

- `sqlite.py` 新增 v18 迁移块（建表、建索引，可重复执行）
- 旧数据导入放在 daemon 启动后的后台任务里，分块提交（和现有大迁移一样），不和写入池抢锁；导入前先备份 `data.db` 和 `ingestion.db`，并核对备份能打开、行数一致
- `token_usage` 的旧行按 `session_id` 算增量导入（旧表没有 pid，进程重启后的第一行可能少算），标 `imported=1`；`ingestion_llm_usage` 按天导入成 `surface=cdf` 的行；Host 上已有的 Swarm Out records 也导入
- 旧表都保留，只是不再写入；回退到旧版本时顶栏仍能读旧表
- 历史上没记的（job、大部分 Bedrock 调用、其他 CLI 进程、pipeline 的花费）补不回来；用量视图写清"某天以前这几类没有记录"，导入的行显示"估算"

## 8. 方案对比

| 方案 | 做法 | 工作量 | 风险 | 取舍 |
|---|---|---|---|---|
| **A. 一本账 + 一个口径模块** ⭐ | §3–§7 | 大 | 中：写入点多，但每处都是"换成调用一个记账函数" | 一次做对，之后任何分析都是 SQL；`ingestion_llm_usage` 的做法被推广，而不是另起一套 |
| B. 各自保留，只统一定义 + 读的时候汇总 | 每个库不动，加汇总层 | 中 | 高：job、其他 CLI 进程、大部分 Bedrock 调用根本没记，汇总不出来；模型分不开；pipeline 和 chat 去重靠时间窗口 | 缺口补不上 |
| C. 只从 transcript 推算 | 读的时候扫 transcript | 小 | 高：CLI 会删旧 transcript；Bedrock 直接调用没有 transcript | 排除 |

推荐 A。

## 9. 分步落地（每步可单独提交、单独验证）

1. **口径模块 + v18 表 + 备份清单**：`usage_accounting.py`（定义、增量、去重键）、`usage_ledger`、`L2_TABLES`。先用一个真实进程连续两次 result 确认 `model_usage` 是否和 `total_cost_usd` 一样是进程累计值（SDK 类型注释只说"原样透传 CLI 的 `modelUsage`"，没说是累计还是单次），增量规则按测出来的结果定
2. **写入方**：CLI result（`model_usage`）、Bedrock（`attribute(feature)`，删开关）、其他 CLI 进程和 job（`record_cli_json`）、调度器读账本
3. **读的一侧切到账本**：顶栏、在线打分、Swarm Out，三者和旧口径对拍
4. **旧数据导入**（后台、分块、先备份）
5. **Pipeline attribution** + 报告改口径
6. **Hive 账本同步**（新接口 + 老 Hive 退回方案）
7. **单价表 + 按模型校准**；**用量视图**（先 mockup）

## 10. 验证计划

**最后必须守住的不变量（测试）**
1. 同一笔只有一行 spend：重复导入、重放、续写（一个用户 turn 产出多个 result）、重试都不多不少
2. 所有"总花费"只加 spend 行；加进 attribution 行总和不变；chat − attribution 不会为负
3. 任何一行 `cost_usd` 都不是累计值：同一 pid、同一模型连续几个 result，行的和 = 最后一个累计值 − 进程开始前
4. 多模型：一个 result 的 `model_usage` 有两个模型时，记两行、各自的四种 token 和花费
5. 每个入口都有四种 token：CLI result、Bedrock、其他 CLI 进程、job（含非零退出）、Hive 拉取
6. 每条 Bedrock 调用都有 `feature`：调用方漏了就测试失败（扫 `jobs/bedrock.py` 全部调用方）
7. 顶栏、在线打分、Swarm Out、用量视图读同一份夹具，得到同一个数
8. 迁移：导入前后 `token_usage`、`ingestion_llm_usage` 一字节不改；导入可重复执行；备份里有 `usage_ledger`
9. 单价表校准：同一模型，表算的花费和 `model_usage` 的 `costUSD` 偏差在阈值内

**每步的检查**：每个修复配一个 mutation 检查；提交前跑改到的模块的已有测试。

**Review**：第 1、2、4 步动的是数据形状、写入路径和迁移，跑一次 correctness + operational 专家审查；用量视图先出 mockup。

## 11. 需要你定的

1. **Hive 的花费算不算进平台总账？** 推荐算，钱是同一个人付的
2. **调度器月度预算要不要扩大到全平台？** 这一版只让它读账本里的 job 花费，范围不变；扩大意味着 chat 花多了 job 会被停，另做决定
3. **走 pipeline 还是直接做？** 跨 6 个模块、新表、迁移，属于高风险改动

## 11b. 实现时改了的决定（2026-10-29）

- **`model_usage` 是进程累计值，已实测**：同一进程连续两次 result，第二次的 `model_usage` 含第一次的 token 和花费（`costUSD` 等于 `total_cost_usd`），`costBasis` 是 `"list"`。所以 CLI 行按"同一进程、同一模型的增量"记，token 和花费都一样。
- **`token_usage` 继续写**（不是 r2 写的"不再写入"）：在线打分和顶栏的旧逻辑还读它，回退到旧版本时也要能用。等读的一侧都切到账本以后，再决定什么时候停写。
- **调度器预算这一版不改来源**：仍是 `monthly_spend_usd`，只是 job 的花费同时写进账本。改预算读哪个数、管多宽，属于花费限制，按 STEERING #2 要你明确批准。
- **记账只交给一个写线程**（第 2 步专家审查后定的）：所有调用方只是把行放进一个有上限的队列，立刻返回；一个后台线程用自己的 sqlite3 连接（`mode=rw`，不会创建数据库文件）成批写入，数据库忙时重试，失败的行计数（`usage_accounting.stats()`），每分钟最多记一条日志。不碰按事件循环建的异步连接池：临时事件循环每次都会留下一个连接池和它的线程，而后台调用每天有几千次。
- **每个 CLI result 只记一次**：结果分支最先记（在任何分支 yield、kill 或 return 之前），其他看见同一个 result 的路径因为它已被标记而不再记。去重键里有一个每次启动的随机串，daemon 重启后不会和旧行撞键；跟踪状态按 session 记，pid 变了就是新进程。
- **CDF 的 `ingestion_llm_usage` 照写**：它是 CDF 预算回放在读的数；CDF 的调用现在也会进账本（`cdf.<source>`），平台总数只算账本。

- **未决：账本的保留期**。账本不清理，每天几千行；备份把整张表导出成压缩 dump。等用量视图做出来、知道要查多久的明细之后，再定"明细保留多少天、更早的按天汇总"。

## 12. r1 审查的 finding 怎么处理的

| # | finding | 处理 |
|---|---|---|
| 1 | CDF 已有 `ingestion_llm_usage` | §1 列入；§4 推广它的 `attribute()` 做法，导入后停写 |
| 2 | 漏了 s7 CLI、ddd_self_audit、Slack DM、system 探针 | §1 列入；§4 `record_cli_json`（探针可以忽略） |
| 3 | `model_usage` 已经按模型给出四种 token 和花费 | §4 改用它；删掉 r1 的"先查子 agent"步骤；§6 校准按模型做 |
| 4 | 漏了在线打分这个读的一侧 | §5 列入，切到账本并对拍 |
| 5 | 旧表没有 pid，按 session 导入会少算 | §7 标 `imported=1`、显示"估算"；删掉 r1 那条会把少算锁死的不变量 |
| 6 | 一个用户 turn 产出多行 result | §3 去重键用 pid + 进程里的 result 序号 + 模型 |
| 7 | 备份清单没有新表 | §5、§9 第 1 步加进 `L2_TABLES` |
| 8 | 没走 `user_version` 迁移框架；schema 笔误 | §3、§7：v18 块、分块后台导入、schema 修正 |
| 9 | 差值用了两种基数 | §3：attribution 按 token 占比分摊 CLI 花费 |
| 10 | 失败的 job 花费丢失；max 不是 sum | §4：非零退出也记；按模型相加 |
| 11 | Hive 只有聊天 turn，去重键不稳 | §4：Hive 自己写账本，Host 按 Hive 行 id 增量拉取 |
| 12 | "真实花费"其实是 CLI 的账面估算 | §1 写明；字段和界面都叫"账面花费" |
| 13 | 本地时间跨机器会乱 | §3：`at_utc` |
| 14 | channel 和 chat 分不开 | §4：`surface=channel` |
| 15 | 没有 `cachePoint`，Bedrock 缓存字段恒为 0 | §4 写明；要不要给 Bedrock 调用开 prompt cache 是之后的优化项 |
