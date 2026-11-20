---
title: Swarm Out 的派发层：一个入口，调度多种执行面
date: 2026-11-20
created: 2026-11-20
updated: 2026-11-20
project: SwarmAI
status: REVISED after one premise review (2026-11-20)
related: docs/design/2026-10-30-dispatch-center.md（派单中心，§7 把"执行机能力表"留给第二种执行机）；docs/design/2026-11-19-swarm-out-agentcore-carrier.md（第二种执行机，本文取代它的 §5.1、§5.7、§5.9）；Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-27-swarm-out-hive-hld.md（v0，决策 9：不重试、不排队）
---

# Swarm Out 的派发层

## 0. 结论

今天的 Swarm Out 没有派发层。"选哪台 Hive"是 `swarm_out.py` 里的两个排序函数；"怎么连上它"散在 10 个文件、
22 处调用里，每一处都默认对方是一台 EC2、经 CloudFront、用 Basic 密码。只有一种执行机时这样能用；要接
AgentCore（以及以后的 Cloud Desktop），就得有一层把这些收起来。这一层有四个概念：

1. **执行面（carrier）**：一种执行机一个实现。EC2 Hive 和 AgentCore 各一个。
2. **位置（location）**：执行面里能放 run 的地方。EC2 是一台台 Hive；AgentCore 是账户里的那个 runtime。
   机群管理、探测、Brain 同步、空闲自动停这些"对机器"的事经过位置。
3. **run 句柄（handle）**：开 run 时由执行面生成，存进派发记录。之后"对这个 run"的事（拉结果、问答、实时查看、
   停止、结算）都经过句柄。一次修正可能开新的 session，所以一个派发有一串句柄，每一轮一个。
4. **调度（placement）**：一个确定性的函数。输入是任务需要什么、各位置现在能给什么，输出是放在哪里、为什么、
   其他候选为什么没选。

调用方不再知道对面是 CloudFront 还是 session endpoint。这一层在 Host 进程里，不是一个云上服务（§3）。

## 1. 现状

下面每条都是 2026-11-20 读过的代码。

**选 Hive**（`backend/core/swarm_out.py:986-1005`）：

- 没点名时，`fleet()` 探测每台在跑的 Hive 的 `admission-state`（`:2334-2360`），`pick_hive`（`:813-855`）从
  "在跑、连得上、没满、版本够"的里面按 (本 Host 在它上面没结的 run 数, 在流的 session 数, 内存占用, 名字) 排序取第一个。
- 一台在跑的都没有时，`pick_stopped`（`:304-317`）挑一台停着的启动，启动最多等 270 秒（`:209`）。
- 派发前再查一次 `admission-state`，满了就报错（`:624-628`）。
- Swarm Run 正用另一个 Brain 版本的 Hive 会被排除（`held_hives`，`:984`）。
- 整次派发共用一个 480 秒的期限（`:153`），命令行 540 秒后放弃等待。

**连上 Hive**：`_instance(hive_ref)` 读 `hive_instances` 一行，`_client(inst)` 用 `https://{cloudfront_domain}`
和 Basic 密码（`:382-427`）。`core/`、`routers/`、`jobs/` 里有 22 处调用 `_instance` / `_instance_up`，
分在 10 个文件（按词边界 grep，2026-11-20）。其中一半是对某个 run 的（拉结果、bundle、实时查看、远程 tab、
结算、停止、修正），一半是对某台机器的（机群探测、问答前缀、Brain 同步、Hive 页、用量同步、空闲自动停）。

**缺口：**

| # | 缺口 | 证据 |
|---|---|---|
| G1 | 只有一种执行机能被选到，第二种没有地方接 | `pick_hive` 的候选就是 `hive_instances` 的行；`_client` 只认 CloudFront + Basic |
| G2 | 在跑的 Hive 都满了，不会启动停着的那台 | `pick_stopped` 只要有一台在跑就返回 None（`:310-311`）。结果是拒绝 |
| G3 | 选中的位置开 run 前就不可用，不会换一个 | 选中后到真正发出之间有一个窗口（再查 `admission-state`，`:624-628`）；窗口很短，所以对 EC2 少见。对 AgentCore 是常态的一类失败：`InvokeAgentRuntime` 被限流或到配额 |
| G4 | 同时发出的两个派发看到同一份负载 | 排序用的是已写入的派发记录（`open_runs_by_hive`）和探测到的 streaming 数；正在派发的 `_IN_FLIGHT`（`:215`）只给空闲自动停看（`core/hive_idle.py:159`），排序不看。多常见没量过 |
| G5 | 每个按机器运行的后台任务都自己假设 EC2 | 用量同步、空闲自动停、Brain 同步、诊断各自遍历 `hive_instances` 并发 HTTP / SSM |

G2 和 G4 各是一两行的改动，不需要新的一层就能修；它们放在这里，是因为调度函数重写时顺手改掉，而且各自在 §6 第 3 步
单独说明理由。需要新的一层的理由是 G1 和 G5：没有它，每多一种执行机，这 22 处调用和每个后台任务都要再改一遍。

## 2. 这一层管什么，不管什么

**管：** 选执行面和位置、负载均衡、需要时扩容（启动停着的 Hive、开一台 microVM）、开 run 前不可用时换一个、
记录每次放置的理由、给之后的调用一个统一的入口。

**不管：**

- **准入。** 任务能不能派（Project Agent 有没有问题、repo 能不能 clone、要不要私有网络）仍由
  `task_spec.admit`（`backend/core/task_spec.py:379`）决定，在调度之前。
- **Hive 上怎么跑。** 工单、等人、run record、pipeline 都在执行机内部。
- **按任务类型的限制。** 不按 profile、预计时长、repo 类型挑执行面（XG 2026-11-19：产品功能不设场景限制，
  能在上面跑的就可以用）。执行面之间唯一的硬差别是它们能做到什么（§4.2 的能力字段）。
- **排队。** 仍然不排队（v0 决策 9）。没有位置时照今天拒绝，并说明每个候选为什么不行。

## 3. 方案对比

| | A：Host 里的派发层（推荐） | B：云上的控制面 | C：在现有代码里按执行机分支 |
|---|---|---|---|
| 做法 | 执行面、位置、run 句柄、调度函数，都在 Host 进程里 | 在用户账户里放一个常驻服务（API Gateway + Lambda/Step Functions + DynamoDB），Host 把任务交给它，它来调度、排队 | `_instance`、`pick_hive` 和 22 处调用各自加 `if carrier == ...` |
| 能做到 | G1–G5 | 另外能排队、Host 合盖时也能派、多台 Host 共用一个池 | G1–G5 都能做到（G2、G4 本来就是小改动） |
| 代价 | 一次重构，分四步落地（§6） | 一个新的常驻系统要部署、升级、监控；Host 和它之间又是一层协议；和"Host 是指挥中心"（派单中心 2026-10-30）冲突 | 每多一种执行机，22 处调用和每个后台任务再改一遍；漏改的地方只在线上暴露（例如对已结束的 AgentCore run 发请求会新起一台空 microVM） |
| 判断 | 现在的派发都从 Host 发起，Host 合盖时本来就不会有新派发；运行中的 run 不依赖 Host | 它解决的是"排队"和"多 Host"，今天都没有真实需求 | 第一种执行机加进来时能用；第三种（Cloud Desktop）进来时代价翻倍 |

**选 A。** C 不是做不到，而是把同一个分支复制 20 多遍。调度写成纯函数（输入、输出都是数据），以后真要做 B，
同一个函数可以搬到云上。

## 4. 设计

### 4.1 四个概念怎么串起来

```
 派发请求 ─▶ 准入 task_spec.admit
               │
               ▼
            各执行面.offers() ──▶ 调度 place(needs, offers)
               │                     │
               │                     ▼
               │               位置.open_run(dispatch) ──开 run 前不可用──▶ 下一个候选
               │                     │
               │                     ▼
               │               run 句柄 ─▶ 写进派发记录（每一轮一个）
               ▼                     │
   对机器的事：位置.probe / brain_sync / idle_stop / usage / diagnose
                                     ▼
   对 run 的事：句柄.client / outputs / state / asks / answer / stream / stop_turn / release / rerun
```

### 4.2 执行面

每种执行机一个实现，都在 `core/carriers/` 下。只放今天有读者的东西：

| 字段 / 方法 | 作用 | EC2 Hive | AgentCore |
|---|---|---|---|
| `kind` | 名字 | `ec2` | `agentcore` |
| `reaches_private` | 能不能访问私有网络 | 否 | 否（拿不到内部权限） |
| `max_run_s` | 一个 run 最长能活多久 | 无上限 | 28800（8 小时） |
| `brain_lock_scope` | Brain 版本锁按什么算 | 按机器 | 按 run（不需要锁） |
| `offers()` | 现在能接 run 的位置，每个带负载和"多久能开始" | 见下 | 见下 |

能力字段只有三个，每个都有读它的地方：`reaches_private` 由调度读（今天对所有执行面都是否；Cloud Desktop
进来时是是），`max_run_s` 由 run 状态判断读（判"载体到期"，不用来挑执行面），`brain_lock_scope` 由调度读。
派单中心 §7 不做的"机型能力表"仍然不做：没有读者的字段不加。

**`offers()` 只列真能用的位置：**

- **账户**：只列 Host 能以 `manage` 身份操作的账户里的位置（`hive/accounts.py`；另一个账户的 Hive 今天只读，
  `swarm_out.py:430` 的 `_s3(…, "manage")` 拿不到，它上面的问题也答不了）。
- **EC2 在跑的 Hive**：今天 `fleet()` 探测的结果；版本够（`capable`）、没满。
- **EC2 停着的 Hive**：只列上次在跑时探测为 `capable` 的（停着的 Hive 探测不到，`fleet()` 给它 `capable: None`，
  `:2355-2357`）。所以位置要记下最近一次探测的结论和当时的版本。
- **AgentCore**：账户里有 runtime、runtime 的镜像版本是本 Host 的版本、活跃 session 没到配额时，给一个弹性 offer。

### 4.3 位置和 run 句柄

**位置** = 执行面里的一个地方，`location(hive_id)` 取得：

| 方法 | EC2 | AgentCore |
|---|---|---|
| `probe()` | `admission-state` | runtime 状态 + 活跃 session 数，不碰任何 run |
| `open_run(dispatch)` | 需要时启动（`_ensure_running`），返回句柄 | `InvokeAgentRuntime(start)`，返回句柄 |
| `brain_sync()`、`brain_status()` | 同今天（`brain_sync_host`） | 没有常驻 Brain，每次派发时经句柄同步 |
| 后台任务（§4.5） | 用量同步、空闲自动停、诊断 | 没有空闲自动停；诊断读 CloudWatch |
| Hive 页的操作（起停、升级、删除） | 同今天（`routers/hive.py`） | 开通、更新镜像版本 |

**run 句柄** = 一轮 run 的地址：

```
handle = {carrier, location_id, round, run_ref, address, auth_ref}
```

- `run_ref`：EC2 是 Hive 上的 session id；AgentCore 是 runtime session id。
- `address`：EC2 是 CloudFront 域名；AgentCore 是 session endpoint URL。
- `auth_ref`：指向凭据的引用，不是凭据本身。派发记录里不出现密码（v0 G4 的要求不变）。

派发记录存一串句柄 `rounds: [handle, ...]`。旧记录没有这个字段时，由 `hive_id` 和 `session_id` 现算一个 EC2 句柄。

| 句柄方法 | 取代今天的 | EC2 | AgentCore |
|---|---|---|---|
| `client()` | `_client(_instance(hive_id))` | 同今天 | SigV4 + 本 run 凭据；run 已结束就抛错 |
| `outputs()` | `hive_records.pull`、checks、lessons | HTTP | S3 |
| `state()` | `run_state` 里对整台 Hive 的 streaming-state 探测 | 同今天 | 读 `state.json` |
| `asks()`、`answer()` | `_read_hive`（`:1707`）、`answer`（`:2413`）用的 `bridge/<hive>/` 前缀 | `bridge/<hive>/` | `bridge/<sid>/` |
| `stream()`、`proxy()` | relay（`swarm_out_relay.acquire`）、远程 tab（`routers/swarm_out_remote.py`） | 同今天 | 经 session endpoint；它转发任意路径和 SSE |
| `stop_turn()`、`discard()` | `:2148`、`:2227` | 同今天 | 同样的 Hive API，经句柄；已结束时直接算完成 |
| `release()` | `POST /api/hive/work/{id}/release` | 同今天 | `StopRuntimeSession` |
| `rerun(text)` | `rerun`（`:2157`） | 发进同一个 session，返回同一个句柄 | microVM 在就同上；不在就开新的 session，返回新句柄，追加到 `rounds`（AgentCore 设计 §5.10） |

`_instance` 只留给执行面自己的实现用。一个测试按词边界（`(^|[^a-zA-Z_])_instance(_up)?\(`）检查 `core/carriers/`
以外没有这类调用；对机器的调用方改用 `location()`，对 run 的改用句柄。

### 4.4 调度

`place(needs, offers) -> Placement` 是纯函数，同样的输入一定得到同样的输出。

**1. 过滤（硬条件）**

- 能力：`needs.private` 非空，而执行面 `reaches_private` 为否，就排除（今天准入已经拒了，这里是第二道，
  为了以后有的执行面能、有的不能）。
- Brain 版本锁：`brain_lock_scope` 是按机器的执行面，排除被别的 Swarm Run 用另一个版本占着的机器（今天的
  `held_hives`）；按 run 的不受影响。
- 点名：点了名就只剩那一个。
- 版本和账户已经在 `offers()` 里过滤了（§4.2）。

**2. 排序：先按"多久能开始"，再按负载**

| 层 | 候选 | 多久能开始 | 同层内怎么排 |
|---|---|---|---|
| 1 | 在跑、有空的 EC2 Hive | 马上 | (没结的 run 数 + 正在派发数, 在流数, 内存, 名字) |
| 2 | 弹性 offer（AgentCore） | 冷启动加上传 Brain 和 repo bundle 的时间（spike 实测） | 只有一个 |
| 3 | 停着的 EC2 Hive | 启动时间（最多 270 秒） | (没结的 run 数 + 正在派发数, 名字) |

成本顺序和这个一致，但理由不同，不能混为一谈：在跑的 EC2 已经在花钱，多一个 run 不多花；弹性按用量付费
（AgentCore 设计 §8：一个 2 小时的 run 约 $0.24）；冷 EC2 按秒计费，但启动后会一直开到空闲 120 分钟自动停
（`core/hive_idle.py:44-45`），一个 2 小时的 run 实际开约 4 小时（m7g.xlarge 按需单价凭印象约 $0.16/小时，约 $0.64，
没核实）。

两处行为变化：

- **G2**：在跑的 Hive **都满了**时，第 3 层停着的 Hive 也是候选。在跑的 Hive 版本不够时不算"满"，不会因此启动一台
  停着的（停着的那台版本很可能更旧）。理由：Hive 的台数是人定的（`hive_fleet.desired_count`，`backend/hive/fleet.py`），
  用上它们是在人的同意之内；空闲 120 分钟后仍会自动停。
- **G4**：排序键里加"正在派发数"。`_IN_FLIGHT` 改成只按 hive id 记，在调度选定时加一次、派发结束时减一次
  （今天派发 spec 时会套两层 `_Dispatching`，`:620`、`:1007`，同一个派发记了两次）。

**3. 换一个：只在开 run 之前、对机器还什么都没做的时候**

只有一种失败会换下一个候选：`LocationUnavailable`，即在对这个位置做任何写操作之前就发现它不可用：

- EC2：派发前的 `admission-state` 说满了、连不上、版本不够（`:624-631`）；
- AgentCore：`InvokeAgentRuntime` 被限流（429）或到配额。

一旦开始对位置写东西（同步 Brain、传 repo bundle、改模型设置、准备工单、开 session），任何失败都直接报错，不换。
原因：这些失败大多是任务自己的问题（打不了包、包太大、clone 不下来），换一台还会再失败一次；而且每一步都在第一个
位置上留下东西（Brain 被换掉、全局模型被改、工单可能还在 clone），换走之后 Host 上没有任何记录知道这些。

另外两条：

- **启动不算"开 run 之前"**。启动一台停着的 Hive 失败或超时，不换下一个：启动本身会在后台继续，而 270 秒已经用掉了
  480 秒期限的一大半。
- **换之前看时间**：所有尝试共用一个 480 秒期限。剩下的时间不够下一个候选的"多久能开始"就不试。

这和"不自动重试"（派单中心 §7、v0 决策 9）不冲突：那条防的是同一个任务跑两遍。换一个只发生在对机器什么都没做的
时候，是放置，不是重跑。

**4. 结果**

```
Placement = {offer, layer, reason, tried: [{offer, error}], skipped: [{offer, why}]}
```

`reason` 沿用今天卡片认识的词（`named`、`only_free`、`idle`、`least_busy:<n>`、`started`），再加 `elastic`。
派发成功时 `tried` 和 `skipped` 写进派发记录，卡片默认不显示，展开才看。**全部候选都不行时**，它们写进拒收记录
（今天的 `_record_refusal`）并放在返回的错误里，让人和派发的 agent 看到每个候选为什么不行。

### 4.5 按执行面运行的后台任务

今天每个周期任务都自己遍历 `hive_instances`。改成遍历位置，由执行面决定对自己做什么：

| 任务 | EC2 | AgentCore |
|---|---|---|
| 机群探测（面板、调度的 offers） | 探测每台在跑的 Hive，记下结论和版本 | 查 runtime 状态和活跃 session 数，不碰任何 run |
| 空闲自动停 | 同今天 | 没有（microVM 自己结束） |
| 用量同步 | 同今天，按 Hive 游标 | 按派发记录读每个 run 的 `usage.json` |
| Brain 同步默认值和状态 | 同今天 | 没有（每次派发时同步） |
| 诊断 | SSM + journalctl | CloudWatch 日志 |

### 4.6 面板

机群一节按执行面分组，每组一行回答"还能接多少"：

- **EC2 Hive**：在跑 a 台（有空 b 台）、停着 c 台。
- **弹性 Hive**：可用；活跃 run k 个，配额 m。没开通时显示开通入口（左侧导航 System 区的 Hive 页）。

每张派发卡片上已有的"派到哪"后面加上放置理由的一个词；展开才看 `tried` 和 `skipped`。

## 5. 和已有决定的关系

| 已有决定 | 本文 |
|---|---|
| v0 决策 9：满了就拒，不排队；拿 session id 超时不重试 | 不排队，保留。可能已派出时不重试，保留。对位置什么都没做时换一个，新增（§4.4 第 3 步说明了为什么不冲突） |
| 派单中心 §7：不做执行机能力表，等第二种执行机来了连同传输一起设计 | 第二种来了，本文就是那份设计。只加有读者的三个字段 |
| 派单中心：Host 是指挥中心，Hive 是执行方 | 不变：派发层在 Host 里 |
| `pick_stopped`：有在跑的就不启动停着的 | 改：在跑的都满了时，停着的也是候选（G2）；版本不够不算满 |
| AgentCore 载体设计 §5.1、§5.7、§5.9 | 被本文取代：`_instance_for` 变成句柄，选路变成调度的第 2 层，那张表变成位置和句柄的方法 |

## 6. 分步落地

每一步单独提交、单独可用，先抽取再扩展（AGENT R4、R26）：

1. **抽出 EC2 执行面，行为不变。** 把 `_instance`、`_client`、`fleet()`、`_ensure_running`、release、拉结果包进
   `core/carriers/ec2.py`；`pick_hive` / `pick_stopped` 原样搬进 `place`。现有测试全绿，就是这一步的验收。
2. **位置和句柄。** 对机器的调用改成经 `location()`，对 run 的改成经句柄；派发记录写 `rounds`。先改读的一侧
   （旧记录没有 `rounds` 时现算），再让派发写它（R3 第 7 条）。加上"`core/carriers/` 以外没有 `_instance(`"的测试。
3. **调度的三处行为变化，各自说明理由：**
   - G2（启动停着的 Hive）：在跑的都满时，今天是拒绝，人得手动起一台再重发；
   - G4（正在派发数）：Swarm Run 一次发几个子任务时会挤到同一台；
   - G3（换一个）：只为 AgentCore 的限流和配额才真正需要，所以和第 4 步一起上。
4. **AgentCore 执行面。** 按 AgentCore 载体设计的 §5.2–§5.6、§5.10 实现，接到第 2 层。之前先做那份设计里的 spike。

## 7. 不做什么

- 云上控制面、排队、多 Host 共用一个池（§3 方案 B）。
- 按任务类型、预计时长、repo 类型选执行面。
- 开 run 过程中、对位置已经写过东西之后的失败换位置。
- 把一个任务拆到多台机器（Swarm Run 已经在做拆分，每个子任务各自走调度）。
- 用模型来选执行面。

## 8. 验证计划

**谁读新写的东西：** 派发记录里的 `rounds` 由所有对 run 的调用读；第 2 步先改读的一侧，再让派发写。

**最后必须成立的不变量：**

1. 第 1 步之后，`swarm_out`、`swarm_out_results`、`swarm_out_settle`、`hive_return`、`hive_records`、
   `usage_hive_sync`、`hive_idle` 的现有测试全绿，且没有改测试的断言。
2. `core/`、`routers/`、`jobs/` 里除 `core/carriers/` 外，没有匹配 `(^|[^a-zA-Z_])_instance(_up)?\(` 的调用。
3. `place` 是确定的：同样的 needs 和 offers 两次调用结果相同；层次顺序是在跑有空的 EC2 → 弹性 → 停着的 EC2。
4. 在跑的 EC2 都满、有一台上次探测为 capable 的停着、没有弹性 offer 时，选中停着的那台；在跑的只是版本不够时，不选停着的（G2）。
5. 两个派发同时进行时，第二个看到的负载里包含第一个，且每个派发只记一次（G4）。
6. `LocationUnavailable` 时派到下一个候选，`tried` 里有原因；Brain 同步、bundle、模型设置、准备工单、开 session 中任一步失败时不换；启动失败不换；剩余时间不够时不换（G3）。
7. 全部候选都不行时，拒收记录和返回的错误里有每个候选的原因。
8. 旧的、没有 `rounds` 的派发记录照样能拉结果、问答、结算。
9. 另一个账户的 Hive 不出现在 offers 里。

**评审：** 第 2 步（改 22 处调用）做 correctness + integration 专家审查；第 3 步（行为改变）做 correctness +
state-machine 专家审查。

## 9. 开放问题

| # | 问题 | 怎么定 |
|---|---|---|
| Q1 | 弹性 offer 的"多久能开始"取多少 | AgentCore spike 实测冷启动加传输 |
| Q2 | 第 3 层要不要受"最多同时启动几台"的约束 | 看第 3 步上线后的实际启动次数；先不加 |
| Q3 | 面板上"还能接多少"对 EC2 怎么算：今天只有"满/没满"，没有容量数 | 先显示有空的台数 |
| Q4 | 派发会改 Hive 的全局模型设置（`swarm_out_model.align`，`:637`），影响这台 Hive 上的其他 session | 和本文无关的现有行为，单独看 |
