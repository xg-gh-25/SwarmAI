---
title: "从 KiroCrew 借鉴什么：筛选结果，和要做的三组改动"
created: 2026-11-04
updated: 2026-11-04
project: SwarmAI
status: r2，已过一次零上下文审查（§8），待 XG 拍板
inputs:
  - Projects/SwarmAI/deliverables/hive/2026-11-04-so_f869035f78c6/…/kirocrew-remote-concurrency-vs-swarmai-hive.md（远程并发对比）
  - Projects/SwarmAI/deliverables/hive/2026-11-04-so_a2ecfd55710f/…/kirocrew-top5-borrow-report.md（整体 Top 5）
related:
  - docs/design/2026-10-31-human-proof-verification.md（XG 2026-10-31 的安全方向）
  - docs/design/2026-10-31-swarm-out-hive-decisions.md（Hive 决定已能跨重启送达）
  - docs/design/2026-11-02-swarm-out-hive-deaths-diagnostics-states.md（run 状态清理、diagnose）
---

# 从 KiroCrew 借鉴什么

## 0. 结论

两份 Hive 调研一共提出 15 条候选（10 条借鉴、5 条顺带发现）。逐条对照我们的代码（main
`e0df5e1a3`）、做了两次测量、再经过一次零上下文审查之后，**值得现在做的是 3 组 7 件改动，外加一批
Brain 纠正**：

- **A. 安全规则只有一处定义，默认值是不能被删的地板**（4 件，约 1 天）。顺带修掉两个已有缺陷：
  名单文件写坏一个字段就能让后端起不来；URL 脱敏规则在长输入上是平方级耗时（32 KB 时 2.4 秒，实测）。
- **B. Hive 重启时，把死掉那一刻还开着的 turn 记成"daemon 停了"**，不再靠 300 秒猜（1 件，约 1.5 天）。
- **C. Hive 源站只接受我们自己 CloudFront 发来的请求**（1 件，约 1 天，含真机验证）。
- **D. 纠正 Brain**：今天两份报告写进 Brain 的条目里有 3 条是错的，另有 2 条旧条目过时或放错位置。

其余候选不做：一条已经做完了，两条和已定的规则冲突，一条测出来几乎不发生，三条没有消费方。
"LLM 以新用户身份每晚用一遍 App"这条真有价值，但要 1–2 周，单独起设计（§5）。

## 1. 怎么筛的

每条候选问三个问题（SOUL P9）：问题是否真实存在（读代码，或测量）？是否已经有机制管着？
最小的正确改动是什么，还是不该做？报告里的说法只当线索，下面每个判断都附本轮读过的位置。

两次测量（脚本在 `/tmp/kc-measure/`，只读）：

- **turn 以工具调用结束、没有回答**：扫了本机 30 天的 SDK transcript（536 个会话，约 3,980 个
  turn）。以工具调用收尾的 311 个里，几乎全部后面接的是上下文压缩或健康刷新，不是用户；
  真正后面跟着用户再发消息的只有 8 个（其中 2 个是"continue"/"干嘛停了"），约 0.2%。
- **默认危险命令名单和本机文件**：本机 `~/.swarm-ai/dangerous_commands.json` 和代码里的 17 条默认
  完全一致（文件 2026-06-21 写入后代码默认没再变），所以"默认更新到不了老安装"现在是潜在问题，
  不是正在发生的问题。

## 2. 筛选结果

| # | 候选（出处） | 结论 | 依据 |
|---|---|---|---|
| 1 | agent 能改写自己的危险命令名单（Top5 #1） | **做（A1）** | `{"patterns": []}` 被原样接受，默认只在文件缺失或损坏时用（`security_hooks.py:1696-1733`）；名单在每个 session 建 gate 时读（`:2085`）。另外 `{"patterns": null}` 让 `list(None)` 抛 `TypeError`（`:1702`），没有 except 接住，启动时 `main.py:1221` 和每次建 gate 都会崩 |
| 2 | 出站脱敏漏掉 S3 预签名 URL（顺带） | **做（A2）** | 实测 `redact_credentials` 对带 `X-Amz-Signature` / `X-Amz-Security-Token` 的 URL 原样放过；URL 凭证规则有三份定义：`credential_patterns.py:69-71`、`ingestion/contracts_v2.py:421-424`、`tool_summarizer.py:85`。现有规则在长 URL 上是平方级（`"http://"+"a"*n`：8K 0.15 秒，32K 2.4 秒），`StreamRedactor` 每来一块都对未发出部分重跑（`egress_redactor.py:205-209`） |
| 3 | 派单提示说 push 等"每次问人"，代码是直接拒绝（并发 §7） | **做（A3）** | `task_spec.py:500-501` 对 `security_hooks.py:2175-2191`；`project_agent.py:49-52` 的注释也是旧说法 |
| 4 | 一次批准在整个 session 里有效（顺带） | **部分是缺陷，做（A4）** | 报告说的"agent 批的也缓存"不是问题：host 只能答它有权答的类别（`approval_class.HOST_CLASSES`）。审查发现的真问题：缓存只按命令文字哈希（`permission_manager.py:107-120`，`security_hooks.py:2210`），而危险判断看 cwd（`:2099`），所以在一个目录批准的 `rm -rf build` 到另一个目录也自动通过 |
| 5 | Hive 重启后 run 结局靠猜（并发 #1） | **做（B1）** | turn 的结局只在 `_published_turn` 的 `finally` 里写（`turn_outcome.py:1-20`）；进程被 kill（如 OOM）时不写，Host 看到 `no_record`，300 秒后才推断 interrupted（`swarm_out_settle.py:436-454`），并承认分不清（`swarm_out.py:1463`）。`restarted` 只是 CLI 子进程重启的提示（`retry_manager.py:529`），不是 daemon 重启 |
| 6 | Hive 源站对全球 CloudFront 开放、源站一跳是明文（并发 #5） | **做（C1）** | 安全组用全局 CloudFront 前缀列表（`provisioner.py:529-545`），`OriginProtocolPolicy: http-only`（`:869`）；之后是 Caddy 的 basic auth（实际运行的配置由 `hive/user_data.py:69` 的 heredoc 写出），后端还有一道 `middleware/hive_auth.py` |
| 7 | Hive pipeline 决定在 turn 结束/卡片过期/重启时丢失（并发 #4） | **已做** | `2026-10-31-swarm-out-hive-decisions.md`（status: implemented）：决定从 run 自己的事件日志读成快照，键跨重启不变，没有卡片时直接记到 run 上再发继续消息。报告依据的 TECH.md 条目过时了（D2） |
| 8 | 每个派单带 wall-clock 期限（并发 #2） | **不做** | STEERING #2：超时只用来防真正的挂死，不能截断正在进行的真实工作；没有记录到派单挂死 |
| 9 | Hive agent 进程套 Linux namespace 沙箱（并发 #3） | **不做** | XG 2026-10-31 定了方向：保留 agent 代他的完整权限，只防危险/不可逆动作和外部内容劫持（`human-proof-verification.md` status）。落到 main 的一切都经 Host 审 diff，这是边界 |
| 10 | turn 收尾契约：必须以回答或"无事可做"结束（Top5 #2） | **不做** | §1 测量：约 0.2%；空 turn 和文字形式的工具调用已有恢复（`streaming_orchestrator.py`）；后台 sub-agent 没结束时已有 `running · background` 状态（`swarm_out.py` `run_state`） |
| 11 | 探测到变化才唤醒的监视循环 + 会话账本（Top5 #4） | **不做** | 没有消费方：Swarm Out 的 `watch` 已经是"阻塞等变化、不花模型 turn" |
| 12 | 导入新用户的 Claude Code 配置（Top5 #5） | **暂不做** | 没有记录到的用户需求（PRODUCT.md "Evidence over adoption"） |
| 13 | review finding 必须写出 Trigger + Outcome（Top5 亚军） | **暂不做** | 先量最近 run 里 HIGH 被证伪的比例 |
| 14 | 每晚 LLM 以新用户身份做 GUI 测试（Top5 #3） | **单独设计（§5）** | 价值真实，量级不同 |
| 15 | PRODUCT.md:165 把一个缺陷放在 Non-Goals 下（顺带） | **做（D3）** | 已读到原文 |

## 3. 要做的

### A. 安全规则只有一处定义，默认值是地板

**根因相同**：一条安全规则有多份定义，或者它的输入能被一个文件、一段文字静默地改掉，于是改了
一处、另一处照旧。

**A1 危险命令名单：默认值是地板，文件只放用户自己加的。**
- `load_dangerous_patterns()` 返回 `DEFAULT_DANGEROUS_PATTERNS ∪ 文件里的 patterns`（默认在前，去重）。
- 只接受 `{"patterns": [str, …]}`。`patterns` 不是字符串列表（`null`、字符串、数字、里面有非字符串）
  一律按损坏处理：记 warning，只用默认值，不抛异常。修掉 §2 #1 的启动崩溃，也挡住
  `{"patterns": "*"}` 被当成一条 `*`。
- **一次性迁移**：现有文件当初是用默认值播种的，所以把文件里等于当前默认的条目删掉，只留用户自己
  加的。不迁移的话，代码以后删掉的默认会因为并集永远留着。`security_hooks.py:1705-1720` 的旧 glob
  清除并进这一步。迁移前把原文件备份成 `dangerous_commands.json.bak-<ts>`。
- 文件缺失时写 `{"patterns": []}`。`main.py:57` 的说明改成："在这里加你自己的模式；内置的始终生效"。
- 说清它不是什么：这**不是**挡住一个铁了心的 agent 的边界（它还能改启动脚本等，见
  human-proof §1、§3，XG 已决定不在这条线上建边界）。它堵的是最便宜、最像正常配置编辑的那条路：
  一次 JSON 写入就让之后所有 session 的危险命令不再问人；外部内容劫持最可能走的就是这条。
- 可能的反对：用户想删掉一条误报的默认。答：误报在产品默认里修（P7，参见
  `2026-10-26-approval-prompt-false-positives.md`），不让每台机器各自删。

**A2 URL 凭证：一处定义，所有出口共用，线性时间。**
- `credential_patterns` 成为 URL 凭证参数集合的唯一定义：把 `contracts_v2.URL_CREDENTIAL_PARAMS` /
  `PRESIGN_PARAMS` 移过来，`contracts_v2` 改为导入，语义不变。
- URL 规则改写：先用一个有界的、不回溯的模式找出 `https?://` 开头的连续非空白串，再对每个串用
  `urllib.parse.urlsplit` 拆参数（`contracts_v2.py:457` 已经这么做），参数名在集合里的只替换它的
  **值**（`X-Amz-Signature=[REDACTED]`），URL 其余部分保留。今天的规则会把整条 URL 换成
  `[REDACTED]`，改成只换值以后，读的人还看得出这是哪个链接，但链接已经不能用了。
- 任意 scheme 的 `user:pass@`（`tool_summarizer._ANY_URL_CREDENTIAL`）并进 `credential_patterns`，
  `tool_summarizer` 改为调用它。
- 受影响的读者（R27，已 grep）：`redact_credentials` 的调用方 `channels/egress_redactor.py`
  （含 `redact_exfiltration_urls` 和 `StreamRedactor`）、`core/daily_activity_writer.py`、
  `core/todo_manager.py`、`core/tool_summarizer.py`；`routers/tscc.py:173-194` 的安全扫描直接读
  `_CREDENTIAL_PATTERNS`，会多出预签名 URL 的发现，评级可能变；`contracts_v2` 只改导入。
  只会多脱敏、不会少脱敏。

**A3 派单提示和 gate 说同一件事。**
- `task_spec.py:501` 改成：这些动作在本任务上不会做，会被直接拒绝、不会问任何人；需要时换一种做法，
  或在结果里说明做不了的原因。
- `project_agent.py:49-52` 的注释同步改。注释里写明是哪两个 gate 在拒绝：`send_external_message`
  由外部工具 gate 拒绝（`security_hooks.py:2362-2370`），其余由危险命令 gate 的派单分支拒绝
  （`:2175-2191`）。绑定坏了（`pin.broken`）的 session 会问人，那是 gate 的兜底，提示不写这种情况。

**A4 session 批准按"命令 + cwd"缓存。**
- `approve_command` / `is_command_approved` 的哈希改成命令文字加 cwd，危险判断用到的输入它都用到。
- 同一个目录里的同一条命令照旧只问一次。

### B. Hive 重启时了结死掉那一刻还开着的 turn

**问题**：daemon 被 kill（OOM、实例重启）时，`_published_turn` 的 `finally` 不会运行，turn 没有
`turn_outcome`，也不会封进 run record。Host 只看到 `no_record`，等 300 秒才推断
interrupted，之后 dispatcher 还要自己 `diagnose` 才知道是怎么回事。

**B1 改动**（只在 Hive 上运行，`SWARMAI_MODE=hive`）：
- **在哪运行**：lifespan 里同步跑完，放在 `reopen_dangling_claims`（`main.py:1350`）之前，也在 bridge
  任务创建（`main.py:1567`）之前。这时还没有任何 turn 能开始：排队的消息没被重新放出来，bridge 也还
  没开始发继续消息。
- **双保险**：进程启动时记下时间。只处理"turn 开始的那一行早于这个时间"的 turn，所以即使以后启动顺序
  被人改了，B1 也不会去碰本次启动后才开始的 turn。
- **找哪些 session**：遍历 order（`hive_workspace._orders()`，`:623`），取绑定了 `session_id` 的。
- **判断要不要处理**：以 run record 的封存标记为准（`hive_records._sealed_through`，`:446`），不以
  "有没有 outcome"为准。封存标记之后还有行，说明有内容没封进去：
  - 最后一个 turn（用 `swarm_out_results._is_turn_start` 判断 turn 开始）没有 outcome，或者 outcome 是
    `paused`，且这个 session 没有等待中的 pipeline 决定 → 补写一个 `turn_outcome`，
    `ended = "daemon_stopped"`。`paused` 也算：进程死了以后问题卡片已被 bridge 的 `reconcile` 关掉，
    没人能接着答。有等待中的 pipeline 决定时不写，那个决定从 run 状态读、会照常送到（§2 #7）。
  - outcome 里的 `repos` 用和正常结束同一个函数算（`session_router._task_repo_facts`，`:4382`），
    这样被 kill 的那个 turn 提交的东西照常回到 Host（code return 读最新一个带 `repos` 的 outcome，
    `hive_records.py:841-858`）。`gate_refusals` 写成空列表并标 `partial`：daemon 死前的拒绝计数已经拿不到了。
  - 然后在 `_seal_turn_record` 同一把 per-session 锁下调 `hive_records.seal_turn` 封存。
- **幂等**：封存标记已经指到最新一行的 session 不碰；同一次启动跑两次，第二次什么都不改。
- **不自动重跑**。重跑还是 dispatcher 的决定（C099：决策形状最简单的那种）。Host 直接拿到事实，
  不用再等 300 秒。

**读者先于写者**（R3 #7）。`daemon_stopped` 的读者，要在写者落地之前全部改好：
- 后端：`turn_outcome.ENDED`；`swarm_out_results.summarize` 的白名单（`:340`，不在白名单里就会变成
  `no_record`，那是 provisional）和 `ENDED`（`:68`）；`swarm_out_settle` 的 `_DELIVERED`（`:383`），
  并且和被推断为 interrupted 的 `no_record` 走同一条路（`INTERRUPTED_AFTER_S` 留给还没升级的老 Hive）。
- 桌面端：`components/swarm-out/live.ts` 的 `ENDINGS`（`:215`）和 `outcomeBucket`（`:483`）；
  `tone.ts:27` 的 `ENDED_TONE`（`Record<SwarmOutEnded,…>`，不补就编译不过）；
  `services/swarmOut.ts:170`、`types/index.ts:611` 的类型；i18n `swarmOut.ended.*`（各语言）。
- `s_swarm-out` SKILL 第 5 步的表：`ended = daemon_stopped` → 先 `diagnose`，再决定是否 `rerun`。

### C. Hive 源站只接受我们自己的 CloudFront

**问题**：任何一个 CloudFront 分发都在那个全局前缀列表里，所以别人自己建的分发也能打到我们的源站。
这一点是 AWS 文档里写明的前缀列表行为，我们的实例上还没实测。剩下的只有 basic auth（Caddy 和后端
各一道），而且 CloudFront 到源站这一跳是明文 HTTP。

**C1 改动**：
- 每台 Hive 一个随机的源站密钥，**在启动 EC2 之前生成**：Caddyfile 在实例启动时由 user data 写出
  （`provisioner.py:1012`），而分发在之后才建（`:1055`）。
- **新 Hive**：密钥写进 `hive/user_data.py:69` 的 Caddyfile heredoc（和 basic auth 的 hash 一样内联；
  `caddy.service` 没有 `EnvironmentFile`，`user_data.py:160-171`，所以不能走 env）。规则：除 `/health`
  外，`X-Swarm-Origin` 不匹配就 403，匹配之后才进 basic auth。建分发时 origin 的 `CustomHeaders`（CloudFront API 字段名；`OriginCustomHeaders` 是 CloudFormation 的写法，API 不认）带上同一个值。
- **access log 不能记下这个头**：Caddy 的 log 用 `format filter` 删掉 `request>headers>X-Swarm-Origin`。
  Caddy 默认会不会记，没有核实；不管默认如何都显式删掉。
- **老 Hive**（`update-hive` 加一步）：先更新分发，加上这个头（Caddy 不认识这个头，加了也没影响），等分发
  `Deployed`；再通过 SSM 改 Caddyfile，加上 403 规则，`caddy validate` 通过后重载，失败就回滚。现有的
  `update_caddyfile`（`provisioner.py:1668`）已经是"SSM 改文件、validate、回滚"的做法，照它来。这个顺序
  任何时刻都不会把自己锁在外面。
- **回滚**顺序相反：先去掉 Caddy 的 403 规则并重载，再从分发里删掉这个头。
- `hive/Caddyfile` 不会被部署（`hive/update-hive.sh` 已废弃），只是参考副本；同步改它，免得读的人被误导。
- 残留风险（不在本设计里修，写给 XG）：源站这一跳仍是明文，密钥和 basic auth 凭证能被链路上的人
  看到。真正的修法是 CloudFront VPC origin（源站不再有公网地址），这会改变 Hive 的网络拓扑，
  要单独做决定。

参考：CloudFront 给源站请求加自定义头
<https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/add-origin-custom-headers.html>；
VPC origins
<https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html>。

### D. Brain 纠正

今天两份报告经 CDF 进了 SwarmAI Brain（`IMPROVEMENT.md:4057-4084`，`source:S6 | taint:external`）。
其中错的、过时的、放错的，按 XG 的批准逐条处理（`governed_edit.withdraw`，有记录、可撤销）：

| # | 条目 | 处理 |
|---|---|---|
| D1 | IMPROVEMENT.md:4078 "Hive pipeline 决定会丢" | `closed`：10-31 的设计已实现 |
| D2 | TECH.md:1244 同一说法的 [model] 条目 | 换成现在的机制（决定是快照，键跨重启不变）；旧条目 `overridden` |
| D3 | PRODUCT.md:165 Non-Goals 下的缺陷 | 移到 IMPROVEMENT.md 的 pitfall（原条目 `overridden`） |
| D4 | IMPROVEMENT.md:4063 "批准记一整个 session" | 换成 A4 的准确说法（缓存不看 cwd）；旧条目 `overridden`，A4 落地后 `closed` |
| D5 | IMPROVEMENT.md:4072 "派单没有 wall-clock 期限" | `withdrawn`（需 XG）：和 STEERING #2 冲突 |
| D6 | IMPROVEMENT.md:4075 "Hive 上没有 OS 沙箱" | 换成一条 decision：同一 OS 用户是有意的，边界是 Host 审 diff（XG 10-31）；旧条目 `overridden` |
| D7 | 4057 / 4060 / 4069 / 4081 / 4084 | A1 / A2 / B1 / C1 / A3 落地后 `closed` |

## 4. 不做的，什么时候重新考虑

| 候选 | 重新考虑的条件 |
|---|---|
| 派单期限 | 出现一次派单挂死（永远不结束），且现有逐步防护没抓到 |
| Hive namespace 沙箱 | Hive run record 被拿来当未经 Host 审查的依据；或 XG 改了 10-31 的方向 |
| turn 收尾契约 | 同样的测量在某个月超过 1%，或用户为此纠正超过 2 次 |
| 监视循环 + 账本 | 出现一个具体的、要求"长期盯一个外部状态"的任务流 |
| 导入 Claude Code 配置 | 外部用户上手时报告缺这个 |
| finding 的 Trigger + Outcome | 测出最近 run 里被证伪的 HIGH 占比明显（比如 ≥30%） |

## 5. 单独设计：LLM 以新用户身份每晚用一遍 App

为什么值得：UI 可发现性是 XG 纠正最多的一类（EVOLUTION C096 那一组），我们自己的直觉抓不到
（SELF.md "Known Judgment Gap — UI/UX"）。KiroCrew 的做法是只给模型看截图、只让它点鼠标敲键盘，
单开一个"新手卡点"通道，按（功能，控件）去重，每晚最多新开 5 条。

已经核实的前提：前端能脱离 Tauri 在浏览器里跑（`desktop/src/services/tauri.ts:17-27` 的
`isDesktop()`，Hive 就是这么服务 SPA 的）；OS Eval 的离线测试已有固定的 fixture 工作区，可以用来播种。

没核实的：确定性的假模型后端、成本。它属于 SwarmAI 自己仓库的 CI（P7：仓库自己的纪律放在仓库里），
只提建议、不阻塞合并。等 A–C 落地后，单独写一份设计，先用 5 个场景（Swarm Out 面板、Brain Hub、
Needs You）做一次试点再决定。

## 6. 验证计划

**每件改动的测试**（先写、先红，再改；每条都做 mutation：撤掉修复，测试变红）：

| 改动 | 测试 |
|---|---|
| A1 | `{"patterns": []}` → 结果包含全部默认；文件有额外模式 → 并集；`patterns` 是 `null` / 字符串 / 含非字符串 → 只用默认、不抛异常；迁移：文件里等于默认的条目被删、用户自加的保留、备份文件存在且内容和原文件相同；迁移后从默认里删一条 → 加载结果里没有它；文件缺失 → 写 `{"patterns": []}` |
| A2 | 预签名 URL 的 `X-Amz-Signature`、`X-Amz-Security-Token` 的值被替换，其余部分保留；`StreamRedactor` 分块输出和一次性输出相同；32 KB、没有凭证的 URL 在 0.1 秒内处理完（今天是 2.4 秒，用来证明回溯修掉了）；`contracts_v2.sanitize_source_url` 结果不变；任意 scheme 的 `user:pass@` 被替换 |
| A3 | 派单 segment 对 reserved 动作写的是"会被拒绝"；同一个 pinned session 上两个 gate 确实拒绝（绑在同一个用例里） |
| A4 | 同一条命令、同一 cwd 第二次不问；换一个 cwd 再问 |
| B1 | 强制执行恢复路径（R28）：session 有一个早于启动时间的 turn 开始、没有 outcome → 写出 `daemon_stopped`，`repos` 和正常结束时算法相同，然后被封存，`summarize` 读出 `ended = daemon_stopped` 且不是 provisional；trailing `paused` 且没有等待中的决定 → 同样处理；有等待中的决定 → 不碰；turn 开始晚于启动时间 → 不碰；已封存到最新一行 → 不碰；第二次运行不改任何东西 |
| C1 | `user_data` 生成的 Caddyfile 有 403 规则、`/health` 不受影响、log 里删掉这个头，`caddy validate` 通过（CI 里没有 caddy 时跳过这一项，真机上必跑）；分发配置带自定义头；`update-hive` 的步骤顺序（先分发、`Deployed` 后再改 Caddy）和回滚顺序，用 fake client 断言 |

**结束时必须成立的不变量**（最后跑一次这一组）：
- 上面 A1–A4、B1、C1 的测试。
- 已有的 `test_swarm_out_hive_decisions.py`、`test_hive_files.py`，以及 `credential_patterns`、
  `egress_redactor`、`swarm_out_results`、`swarm_out_settle` 已有的测试文件。
- 桌面端 `tsc` 通过（`ENDED_TONE` 的穷举由类型检查保证），加上 `live.ts` 的测试。

**真机验证**（R16，按顺序，每步单独 smoke）：
1. 本机 build 后端，跑 A 组；派一个 research 任务，看 Hive 侧 segment 的文字。
2. `update-hive` 一台 Hive（demo）：B1 —— 在 run 进行中通过 SSM `systemctl kill -s KILL`，重启后 Host
   立即读到 `daemon_stopped`，结果里带着 kill 前提交的 commit。
3. 同一台 Hive 做 C1：直接用源站域名 + 正确的 basic auth 访问 → 403；经 CloudFront → 200；
   Swarm Out 派单、watch、取结果全部正常；access log 里没有这个头。然后再更新第二台。

**谁审什么**（`s_expert-review`，最多两轮）：A1、A2、A4、C1 → security；B1 → correctness +
state-machine。A3 是文字，不用审。

**部署**：A、B 改的是同一个后端（本机和 Hive 共用），一次 build；Hive 要 `update-hive` 才生效。
C 要逐台改分发和 Caddyfile，回滚见 C1。

## 7. 待 XG 决定

1. §3 A–D 的范围是否同意。有两处会改变现在的行为：A1 之后内置危险命令不能再靠改文件删掉；
   A2 之后带凭证的 URL 只把凭证的值换掉，不再整条换掉。
2. D5 的 withdraw 只能由你做（`governed_edit` 只允许用户 withdraw），同意这份设计就当作你的批准。
3. 执行方式：A、B、C 各自都小、位置清楚，建议本地直接做，按 §6 请 security 和 correctness 专家审；
   要走 pipeline 也可以（一个需求一个 run，三组作为任务列表）。

## 8. 修订记录

- **r1 → r2**（一次零上下文审查，结论都在代码里复核过）：
  - C1：真正运行的 Caddyfile 是 user data 的 heredoc，不是 `hive/Caddyfile`；密钥要在启动 EC2 前生成；
    老 Hive 的顺序改成先分发、后 Caddy；回滚顺序修正；log 里删掉这个头。
  - B1：放在排队消息重放和 bridge 之前运行，加上启动时间作为双保险；改为以封存标记为准；处理
    trailing `paused`；outcome 带上 `repos`；读者清单补全（`summarize` 白名单、`_DELIVERED`、桌面 5 处）。
  - A1：只接受字符串列表（修启动崩溃）；一次性迁移，删掉文件里的默认副本。
  - A2：规则在长输入上是平方级（实测）→ 改成 `urlsplit`；只替换值；读者清单补上 `tscc` 和 `StreamRedactor`。
  - 新增 A4（批准缓存不看 cwd）；A3 写明是哪两个 gate 在拒绝；修正几处行号。
