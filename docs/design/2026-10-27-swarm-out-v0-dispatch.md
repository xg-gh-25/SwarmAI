---
title: "Swarm Out v0：把任务派到 Hive worker 上跑完，人工介入走云存储 bridge"
created: 2026-10-27
updated: 2026-10-27
project: SwarmAI
status: v0 已实现（2026-10-27）；§8 记录实现时对设计的改动
tags: [swarm-out, hive, dispatch, bridge, autonomy]
related:
  - docs/design/2026-10-27-swarm-out.md   # 远期方向（Brain 驱动的 context、记录回流、交付），不在 v0 范围
---

# Swarm Out v0

## 0. 范围

XG 2026-10-27 定的范围：**把任务派发出去，在 Hive worker 上跑完；中间需要人介入时，经云存储 bridge 通信，不要求实时。**

- **任务不限于 Pipeline。** native coding、非编码任务、或任务里写明"用 pipeline 做"的，都是一个普通的 Hive chat session。Hive 上怎么做由任务本身和 worker 的配置决定，以后可以定制 Hive。
- **v0 不做**：结果回流和 distill、监控和 dashboard 真数据、多 Hive 调度、定时任务、Slack 入口、代码交付方式（push / PR / CR，由任务自己写明，按 Hive 上现有能力做）。这些在 `2026-10-27-swarm-out.md` 里，留到 v0 跑通以后。

## 1. 已经实测或核对过的事实

| 事实 | 证据 | 对 v0 的意义 |
|---|---|---|
| **派发方发完就断开，Hive 上的 session 照样跑完。** 2026-10-27 在 explorer 上发了一个约 2 分钟的任务（6 次 `date; sleep 20`，写文件，最后回复 PROBE-DONE），发出 1.9 秒后断开连接；2.7 分钟后读 `/api/chat/sessions/<id>/messages`，最后一条回复带 PROBE-DONE | 实测；代码 `routers/chat.py:318-326`（客户端走了，turn 被 detach，不取消） | 派发只需要"发出去拿到 session id 就走" |
| Host 能拿到 Hive 凭据，Hive 上这个路由被禁用 | `routers/hive.py:57,594` | 凭据只在 Host 后端用 |
| Hive 上没人盯时，危险命令审批和 AskUserQuestion 会阻塞等人，最长 4 小时，超时后继续 | `core/permission_manager.py:57`、`core/ask_question_manager.py:57` | 这就是要接 bridge 的地方 |
| 等人的请求在两处产生、在两处被回答：`permission_manager.store_pending_request` → `set_permission_decision(request_id, decision)`；ask gate `register_waiter` → `ask_question_manager.set_answer(tool_use_id, answers)` | `core/permission_manager.py:185,338`；`core/ask_question_manager.py:93,135` | bridge 不新建等待机制，只在这两处镜像出去、把答案送回来 |
| agent 可以按需建：`POST /api/agents`，字段有 `system_prompt`、`allowed_tools`、`max_turns`、`enable_human_approval` | `routers/agents.py:123`、`schemas/agent.py:96-109` | worker 的定制点：一个专用的 worker agent |
| Hive 并发：`GET /api/chat/sessions/admission-state` 返回 `streaming_count`、`max_concurrent`、`saturated` | `routers/chat.py:777-838` | 派发前看一眼有没有空位 |
| 每个账户每个 region 一个桶 `swarmai-hive-{acct后4位}-{region}`；Hive 角色带 `HiveName` tag；Host 有 PutObject | `hive/provisioner.py:10,68-82,219-256,303,321` | bridge 用这个桶，按 Hive 名字分前缀 |

## 2. 流程

```text
 Host（Swarm Out tab 或任意 chat）                     S3 bridge                      Hive worker
 ─────────────────────────────────                    ─────────                      ───────────
 1. 澄清任务，写成一段完整的任务说明
 2. scripts.swarm_out dispatch --hive <名字> …    
    └─ Host 后端：取凭据 → 看 admission-state
       → POST /api/chat/stream（worker agent） ───────────────────────────────► session 开始跑
       → 拿到 session_id 就断开                                                  （detach，继续跑）
       → 记一条 dispatch 记录（本机）
                                                                                遇到要人决定的事：
                                                       ◄── ask/<session>/<id>.json ─ 镜像请求，照常等待
 3. Host 醒着时轮询 ask/，在 Swarm Out「等你」显示
 4. XG 回答 ─────────────────────────────────► answer/<session>/<id>.json
                                                                       ─────────► Hive 轮询 answer/，
                                                                                  调 set_answer /
                                                                                  set_permission_decision
                                                                                  → session 接着跑
                                                       ◄── done/<session>.json ──── 跑完写一个结束标记
```

## 3. 组件

### 3.1 派发（Host）

- **命令**：`"$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --hive <hive 名字或 id> --title <标题> --task-file <文件>`，它只调本机后端的路由。Swarm Out tab 的 agent（或任何 chat 的 agent）澄清好任务后调用它；人在 chat 里说"派出去"就是确认。
- **Host 后端路由** `POST /api/swarm-out/dispatch`：
  1. 从 `hive_instances` 取实例和凭据（只在后端用，不回给调用方）；
  2. `GET admission-state`，`saturated` 就拒绝并说明（v0 不排队）；
  3. 确保 Hive 上有 worker agent（§3.2），没有就 `POST /api/agents` 建一个；
  4. `POST /api/chat/stream`（`agent_id` = worker），读 SSE 直到拿到 `session_id`，然后断开；
  5. 往 `~/.swarm-ai/state/swarm-out/dispatches.jsonl` 追加 `{dispatch_id, hive, session_id, title, task_digest, sent_at}`；
  6. 返回 `session_id` 和 Hive 网页地址。
- **超时**：拿 `session_id` 的等待上限 60 秒，是防挂死的上限，超时就报失败，不重试（重试可能起两个 session）。

### 3.2 worker agent（Hive，定制点）

一个专用 agent，Host 派发时确保存在，配置随 dispatch 命令的版本走：

- `system_prompt`：说明这是无人值守的任务——能自己决定的就决定，把假设写进最后的回复；真需要人决定时才用 AskUserQuestion，问题要能脱离上下文看懂（因为回答的人在别处、隔很久才看到）；最后一条回复写清做了什么、结果在哪。
- `enable_human_approval: true`：危险命令仍然走审批，审批经 bridge 送到人手里（§3.3），不是直接拒绝。
- 其余（`allowed_tools`、`max_turns`、MCP）用默认值。以后定制 Hive，就是在这里换配置，或者换一个 worker 镜像。

### 3.3 Bridge（S3，异步）

前缀按 Hive 名字分：`s3://swarmai-hive-…/bridge/<HiveName>/`。

| 路径 | 谁写 | 谁读 | 内容 |
|---|---|---|---|
| `ask/<session_id>/<request_id>.json` | Hive daemon | Host | `{kind: question|approval, session_id, request_id, title, questions|command, options, asked_at, expires_at}` |
| `answer/<session_id>/<request_id>.json` | Host | Hive daemon | `{request_id, answers|decision, answered_by, answered_at}` |
| `done/<session_id>.json` | Hive daemon | Host | `{session_id, ended_at, outcome: completed|error|stopped, last_message_excerpt}` |

- **Hive 侧（只在 `SWARMAI_MODE=hive` 时启用）**：
  - **镜像出去**：在 `store_pending_request` 和 ask gate `register_waiter` 两处各加一个"请求产生了"的回调，回调把请求写到 `ask/`。等待本身不变，还是原来的 future。
  - **送回来**：一个后台轮询（30 秒一次，只在有未回答请求时跑）读 `answer/`，调用现有的 `set_permission_decision` / `set_answer`，和网页上回答走同一个函数。回答过的请求在本地记一笔，不重复送。
  - **等待时长**：worker 的等待上限从 4 小时改成可配置，默认 24 小时（不要求实时）。到期按现有的 timeout 路径继续。等待期间这个 session 占着一个并发位，v0 接受。
  - **结束标记**：session 一轮结束时写 `done/`。这是 v0 唯一的"跑完了"信号，不算监控。
- **Host 侧**：一个轮询（Mac 醒着时每 2 分钟，打开 Swarm Out 时立即一次）列出每台 Hive 的 `ask/`，没有对应 `answer/` 的显示在 Swarm Out 的"等你"里；回答时 Host 写 `answer/`。`done/` 用来把 dispatch 记录标成结束。
- **IAM**：Hive 角色加两条：`s3:PutObject` 到 `bridge/${aws:PrincipalTag/HiveName}/ask/*` 和 `.../done/*`；`s3:GetObject`、`s3:ListBucket`（带 `s3:prefix` 条件）到 `bridge/${aws:PrincipalTag/HiveName}/answer/*`。Hive 写不了 `answer/`，所以答案只能来自持有账户凭据的 Host。现有那条"读整个桶"的语句要删掉（不然加了也白加），这一条在 v0 一起做。
- **为什么走 S3，不让 Host 直接调 Hive 的 API**：Host 和 Hive 都可以不在线——Hive 提问时 Mac 合着，Mac 打开时 Hive 也可能在别的 session 里忙；问题和答案都落在 S3 上，谁先醒谁先处理。以后从手机或 Slack 回答，也只是另一个写 `answer/` 的人。

### 3.4 Swarm Out tab

- 左侧 dashboard 的"等你"和"在跑"接真数据：读 `dispatches.jsonl` + bridge 的 `ask/`、`done/`。其余（Judge、trace、历史统计）继续是示例数据。
- 点一行：v0 用浏览器打开 Hive 网页看那个 session（Hive 前端只有 `/`，打开后自己选 session）；在 app 里的浮层留到以后。

## 4. 安全

- 凭据只在 Host 后端；派发返回值和 dispatch 记录里没有密码。
- 任务说明是数据：Hive 照常把它当作用户消息。Host 不把 Hive 写回来的问题文字当指令，只当显示内容；回答卡由 Host 用 `ask/` 里的结构化字段渲染。
- 只有 owner 能派（v0 只有 Host 本机能调这个路由）。
- 已知、v0 不处理：Hive 上 agent 和 daemon 是同一个 OS 用户（usable P5，不防同用户 agent）；Hive 密码熵偏低；参考实例（explorer、demo）的 seed 含 live DDD。worker 最好单独起一台，不用参考实例。

## 5. 验证计划

| # | 要证明的 | 怎么测 |
|---|---|---|
| V1 | 发完就断开，任务照样跑完（核心前提） | 已实测（§1）；SO-v0 收尾时用 `scripts.swarm_out dispatch` 再跑一次 |
| V2 | 派发路由不泄露凭据、拿到 session id 就返回、超时报失败不重试、`saturated` 时拒绝 | 单元测试，Hive 用本地 fake server；mutation check |
| V3 | 提问和审批镜像到 `ask/`，`answer/` 能解开原来的等待 | Hive 侧单元测试：注入一个 pending 请求，写 answer 文件，断言 `set_answer` / `set_permission_decision` 被调用一次；重复的 answer 不重复送 |
| V4 | 端到端介入 | 在一台 worker Hive 上派一个必然要问一次的任务，合上 Host（停 Host 轮询），过几分钟再开，回答，确认 session 接着跑完、`done/` 出现 |
| V5 | IAM 前缀生效 | 用一台 Hive 的角色去读另一台的 `answer/`、写自己的 `answer/`，都被拒 |

审查：派发路由和 bridge 做 security + integration 两项（新的跨机器入口、凭据、外部写入）。

## 6. 分步

1. **派发**：命令 + Host 路由 + worker agent + dispatch 记录（V1、V2）。这一步做完就能"派出去跑完"，介入暂时在 Hive 网页上做。
2. **Bridge**：Hive 侧镜像和轮询、等待上限可配置、`done/`、IAM（V3、V5）。
3. **Swarm Out tab**："等你"和"在跑"接真数据，回答写 `answer/`（V4）。

## 7. Spikes

| ID | 问题 |
|---|---|
| S0-1 | 用 `set_answer(tool_use_id, answers)` 直接解开等待，和网页走 `/api/chat/answer-question` 的效果是否一致（后者还会经过 `session_unit` 续流，`session_unit.py:4532`） |
| S0-2 | 用 `POST /api/agents` 建的 agent，它的 `system_prompt` 和 `enable_human_approval` 在 `/api/chat/stream` 路径上是否真的生效 |
| S0-3 | 等待期间占着的并发位：一台 Hive 同时有几个 session 在等人时，新派发是否还能进来 |

## 8. 实现时对设计的改动（2026-10-27）

按 security、integration 两轮审查和实测，实现和上面的设计有这些不同，以本节为准：

- **答案的交付**：不直接调 `set_answer` / `set_permission_decision`。一个等人的 unit 已经停止读流（WAITING_INPUT，`streaming_orchestrator.py` 在问题和审批两处都是先切状态再 `return`），只调 `set_answer` 会解开 hook 却没人读这一轮剩下的输出。bridge 改走网页用的同一条续流：`session_router.continue_with_answer` / `continue_with_cmd_permission`，在一个保持引用的后台任务里读到结束；session id 用"正在等这个 key 的 unit"（`_pending_tool_use_id`），因为提示板可能用第一轮的临时 id 记它。续流失败或报 error 事件时，下一轮重试。
- **等待上限保持 4 小时**，没有改成 24 小时：它必须小于 lifecycle 的 WAITING_INPUT 看门狗（4h05m，`permission_manager.py` 的注释）。超时后 agent 会重新问，新问题照样经 bridge 送来。Host 不显示超过 4h05m 的问题。
- **没有 `done/` 结束标记**：Hive 活着时 Host 可以直接问它；Hive 没了标记也写不出来。改为 `closed/`：问题离开提示板时写，bridge 启动时把重启前留下、已经没有 waiter 的问题全部标记 closed（`reconcile`）。
- **命令行没有 `answer`**：回答只能在 Swarm Out 面板上点。读了 Hive 写回来的文字的 agent，不能离"批准那段文字要求的命令"只差一条命令。
- **没有专用 worker agent**：无人值守的说明作为任务正文的开头一起发出（`UNATTENDED_PREAMBLE`），用 Hive 的 default agent。
- **Host 把 Hive 写的问题按已知字段重建**，`hive` 字段用 Host 自己记录的名字；回答用 S3 条件写（`IfNoneMatch`），两个回答同时到时第二个被拒绝。
- **IAM**：发布包读权限收窄到 `v*`；bridge 读写按 `${aws:PrincipalTag/HiveName}` 限定；每次 update 都重写整份策略并补 `HiveName` tag。
- **已知、不在 v0**：桌面 daemon 没有 Host 头校验（DNS rebinding，之前就有）；夜间备份到 `backups/` 没有写权限（之前就有）；`bridge/` 前缀没有 lifecycle 规则（改它会整体替换桶上已有的规则）。
