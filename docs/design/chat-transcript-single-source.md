---
title: 会话内容唯一来源 — 结束聊天显示的打地鼠
date: 2026-10-22
created: 2026-10-22
updated: 2026-10-22
project: SwarmAI
status: DESIGN — 待 Gawan 审
related: streaming-spinner-ssot.md (2026-06-30，同类的 spinner 修复), liveReload.property.test.ts (5109df87)
---

# 会话内容唯一来源 — 结束聊天显示的打地鼠

## 0. 一句话

一个会话现在有**两种存法**（实时事件、库里的记录）和**一串写法**（前端 7 条路径往同一个
MessageStore 写，每条带自己的合并规则）。每种组合都是一个可能的分歧，今天修掉的聊天问题全是这种
分歧。修法是：**库里的记录是事件流的投影，显示也是同一个投影；发送幂等；会话状态由后端推送。**
验收是已有的「实时 == 刷新」属性测试：`KNOWN` 清空、补齐的场景全绿。

## 1. 问题清单（2026-10-22 一天碰到的，全部查证过）

| # | 问题 | 根因 | 状态 |
|---|---|---|---|
| 1 | CLI 自开回合停在审批上，卡片不显示，session 等了 18 分钟 | RC1 | 已补丁 1166f6d2 |
| 2 | 提问卡片和答案刷新后消失（R2） | RC1 | 未修 |
| 3 | 并行后台 agent：实时 N 个气泡，刷新后 1 个 | RC1 | 已补丁 cc2ceef6 |
| 4 | 续接回合单独成气泡，对账后内容显示两遍（R1） | RC1 | 已补丁 23697bf8 |
| 5 | 共用气泡里跨回合的相同文本被去重删掉（R3，#3 的补丁引入） | RC1 | 已补丁 23697bf8 |
| 6 | 已失效的审批卡片仍显示可点按钮 | RC1 | 已补丁 1166f6d2 |
| 7 | 消息排在审批后面，界面不说原因 | RC3 | 已补丁 a30d89f6 |
| 8 | SESSION_BUSY 碰上静默重试，同一条消息存两次（10-15 起 19 对） | RC2 | 已补丁 cc2ceef6 |
| 9 | 恢复后自动重发、断线重连、排队送出也会再发，是否重复未查 | RC2 | 未查 |
| 10 | CLI 自开的空回合当成 API 故障，进程被结束 | RC3 | 已修 1d060eee |
| 11 | 空响应日志的耗时是排队时间 | — | 已修 a30d89f6 |
| 12 | Stop 后 CLI 的占位话被模型当成自己的回复 | — | 已修 a30d89f6 |
| 13 | git `check-ignore --stdin` 管道死锁，4 个调用点 | RC4 | 已修 da200214 |
| 14 | code-intel 全量重建超时（即 #13） | RC4 | 已修 da200214 |
| 15 | worktree gate 超时放行 | RC4 | 已缓解 da200214 |
| 16 | bash 语法检查超时放行 | RC4 | 已缓解 da200214 |
| 17 | CORS 拒绝 If-None-Match，带缓存请求失败 134 次 | RC5 | 已修 da200214 |
| 18 | 参数校验日志不写字段 | RC5 | 已修 da200214 |
| 19 | main 上红测试无人发现 | RC5 | 已修 401627ec |
| 20 | arXiv 配成 http，被 SSRF 防护拦 | RC5 | 已修（配置） |
| 21 | SwarmWS `.git/index.lock` 反复残留 | RC5 | 未查到原因 |

"已补丁"的意思是：在现有机制上加了一条特殊规则，问题没再出现，但根因还在。#1–#9 是本设计要从
根上消掉的。

## 2. 根因

### RC1 会话内容没有唯一来源（#1–#6）

- **后端两种格式。** 实时事件（`session_events` 发布的信封）和库里的记录
  （`_persist_stream_event` 写的块）由两段代码各自生成。`ask_user_question` 只在事件里有；
  `decision: expired` 只在读历史时算出来；`turn_deliverables` / `turn_changes` 两边都有但时机不同。
- **前端七条写入路径**，都写同一个 MessageStore：

  | 路径 | 位置 | 自带的规则 |
  |---|---|---|
  | POST 流渲染 | `createStreamHandler(..., 'post')` | 占位气泡 id = client_id |
  | 订阅流渲染 | `beginSubscriptionTurn` + `createStreamHandler(..., 'sub')` | `local-sub-<turnId>`、`hostedTurn`、`turnStart` |
  | 回合结束对账 | `scheduleTurnEndReconcile` → `store.reconcile` | `_applyMerge` 按 client_id 关联、`isStalePlaceholder`、`_mergePreservingInteractive` |
  | 15 秒轮询补卡片 | `surfacePendingQuestion` / `surfacePendingPermission` | 按 requestId / toolUseId 去重、`_decidedPermissionId` |
  | 恢复 / 切 tab 重新拉取 | `resyncTabFromDb`、`mergeTabFromDb`、`initialize` | 整体替换或合并 |
  | 乐观插入 | `insertOptimisticMessages`、续接回合的数字 id 占位 | 数字 id 是"陈旧占位"的判据 |
  | 排队 | 排队气泡 `queued-<uuid>` + `drainSyntheticId` | 送出时改 client_id |

  合并规则另有：`_confirmed` 标记、`collapseAdjacentDuplicateBlocks`、"文本更多的一方赢"。
  每条规则都是某个组合的补丁，组合一多就互相冲突（#5 就是 #3 的补丁和去重规则冲突）。

### RC2 发送不是幂等的（#8、#9）

每条用户消息带 client_id，但后端 `run_conversation` 每次都 `db.messages.put` 一条新记录，从不按
client_id 去重。前端至少有 4 条会再发一次的路径：连接阶段静默重试、断线重连（最多 9 次）、
后端恢复后自动重发、SESSION_BUSY 后重新排队。

### RC3 会话状态靠前端拼（#7、#10）

后端知道"在等哪个提问 / 审批、排着几条、为什么不动"，前端只能从 15 秒轮询的 streaming-state
加零散事件推断。CLI 自开回合的特殊情况也没有明确的状态，后端自己把空回合误判成故障（#10）。

### RC4 基础设施没有统一防护（#13–#16）

同一个写法（`subprocess.run(input=...)` 喂边读边写的子进程）、同一个失败处理（超时就放行），
分散在各处，没有东西拦住下一次。

### RC5 报错没人看（#17–#21）

日志里一直有，没有汇总、没有提醒。

## 3. 方案

### S1 会话 = 事件日志，显示 = 投影（解 RC1）

**原则：一个会话的内容只由"按序号排列的回合事件"决定；库里的记录和屏幕上的消息都是同一个投影
函数 `project(events) → messages` 的结果。**

#### 3.1 投影规则（只定义这一次）

1. 用户消息是一条 user 消息，id 用 client_id 关联。
2. 一个回合的事件依次折进**当前助手消息**：`assistant` 的块按出现顺序追加（流式的
   `text_delta` / `thinking_delta` 是同一块的临时版本，由该回合下一个 `assistant` 事件确认）；
   `tool_result` 按 toolUseId 挂到对应 tool_use 后；`ask_user_question` /
   `cmd_permission_request` 是一个交互块；`turn_stop`、`background_finished`、
   `turn_deliverables`、`turn_changes`、`turn_notice` 是块。
3. **连续的助手回合是一条消息**：回合开始时，如果上一条是助手消息，就接着它；前面是用户消息
   （或会话开头）才新起一条。这就是现在历史接口 `_merge_consecutive_assistant_messages`
   的规则。
4. 去重只在一个回合内：同一回合重复发出的相同块只保留一份；不同回合的相同内容都保留。
5. 交互块的状态（答案、审批决定、失效）是对该块的更新事件，不是另一个块。

#### 3.2 后端

- **所有块都由投影写库。** `_persist_stream_event` 改为：把事件交给共用的回合投影器，写库的
  内容就是投影结果。提问卡片和答案随之存库（修掉 R2）；答案、审批决定都作为更新写回原块。
- **每条记录带水位。** `metadata.event_seq` = 写这条记录时该会话发布到的最后一个事件序号，
  `metadata.event_epoch` = 当前进程的 epoch（序号每次重启从 0 开始）。
- **历史接口返回水位。** 响应头 `X-Event-Watermark: <epoch>:<seq>`：这份数据反映到哪个事件
  为止。读取时算出的状态（`expired`）照旧，水位之后的变化由事件带。
- 历史接口的 `_merge_consecutive_assistant_messages` 保留：它就是 3.1 第 3 条在读取端的实现。

#### 3.3 前端

- **一个投影器** `transcriptProjector`（纯函数，TS）：输入"快照消息 + 水位之后的事件"，
  输出消息列表。MessageStore 只保存快照、水位和之后的事件，消息由投影器算出，不再有别的写入。
- **只有订阅流渲染回合。** POST 流只负责发送和拿 turnId，不渲染。订阅流断线时由重连后的
  `resync`（已有）补回，不再让 POST 兜底渲染。
- **对账 = 换快照。** 回合结束拉一次历史（带水位），快照替换，重放水位之后已收到的事件。
- **唯一的乐观状态**：发送时插一条 user 消息（id = client_id），收到该 client_id 的
  `turn_start` 或在快照里出现后即由投影接管。
- **轮询不再补卡片**：卡片来自库（快照）或事件；轮询只用于订阅流断开时判断要不要重连 / 重拉。

#### 3.4 删除清单（P4 完成时这些都不应存在）

`_mergePreservingInteractive`、`_INTERACTIVE_BLOCK_TYPES`、`isStalePlaceholder` 与数字 id 占位、
`hostedTurn` / `hostForInjectedTurn` / `turnStart`、`surfacePendingQuestion` 与
`surfacePendingPermission` 的轮询补卡分支、`shouldResurfaceQuestion` / `shouldResurfacePermission`、
`drainSyntheticId`、POST 渲染分支与 shadow 机制（`sessionTurnRouter` 的 post 渲染部分）、
`_confirmed` 标记、`collapseAdjacentDuplicateBlocks` 在流式路径上的调用。

### S2 发送幂等（解 RC2）

- **后端**：`run_conversation` 写用户消息前按 `(session_id, client_id)` 查。已存在：不再写、
  不再排队；该消息已被处理 → 返回 `duplicate` 及其 turnId；仍在排队 → 返回当前 SESSION_BUSY
  （不新增排队）。给 `messages` 加 `(session_id, json_extract(metadata,'$.client_id'))` 索引
  （只对 user 行，迁移）。
- **前端**：一个发送器 `sendOnce(clientId, payload)`，所有重发（静默重试、断线重连、恢复后重发、
  排队送出）都调它、都用同一个 client_id。后端幂等后，重试本身安全，各路径不再各自判断该不该发。
- 一次性清理已有的 19 对重复记录（只删后一条，且内容完全相同、时间差 < 2 秒），先给出清单再删。

### S3 会话状态由后端推送（解 RC3）

订阅流加 `session_state` 事件，状态变化时发：

```json
{"type": "session_state", "state": "idle|streaming|waiting|queued",
 "waitingFor": {"kind": "question|permission", "id": "...", "tool": "Bash", "summary": "make clean"},
 "pendingCount": 2}
```

转圈、"在等你批准…"提示、卡片能不能点，都只看这个状态。15 秒轮询只在订阅流断开时用。

### S4 防护做成测试（解 RC4）

- 扫描测试：`subprocess.run(... input=` 用于 `--stdin` 类命令即失败，指向 `utils/stdin_file`。
- gate 超时放行统一计数（`hook_timing` 已有），一天超过 N 次进巡检报告。
- CORS 契约测试：前端 `services/*.ts` 里设置的请求头都必须在 `allow_headers` 里。

### S5 日志巡检（解 RC5）

每日定时任务：WARNING/ERROR 按特征（去掉 id、数字）归类，和前 7 天比，新类别或数量翻倍就通知。
包括 uvicorn 的非 2xx 统计（CORS 那 134 次就是这里漏的）。

## 4. 验收

- **「实时 == 刷新」属性测试**（`liveReload.property.test.ts` + `test_live_reload_scenarios.py`）
  是总验收。P3 先把场景补齐：SESSION_BUSY 排队与送出、Stop（含停在审批上）、订阅流断线与
  `resync`、恢复后重发、两个 tab 看同一会话。补齐后跑出来的特征全部列进 `KNOWN`；之后每个阶段
  只许让 `KNOWN` 变短，不许变长。S1 完成 = `KNOWN` 为空。
- 新增两条性质，与上面同一套场景：
  - **每条用户消息在库里恰好一条**（S2）；
  - **交互块不丢**：每个提问 / 审批在实时、对账后、刷新后都在，且状态一致。
- 每个阶段照常：反向验证（去掉改动，对应测试变红）、前端全量测试、后端相关测试。

## 5. 实施顺序

| 阶段 | 内容 | 依赖 | 规模 | 风险 |
|---|---|---|---|---|
| P1 | S2 发送幂等：后端按 client_id 去重 + 索引；前端统一发送器；重复记录清单 | 无 | 小 | 低 |
| P2 | S1 后端：回合投影器写库、提问卡片与答案存库（R2）、记录带水位、历史接口返回水位 | 无 | 中 | 中（写库路径） |
| P3 | 场景生成补齐；新性质（用户消息一条、交互块不丢） | P1、P2 后跑才有意义 | 中 | 无（只加测试） |
| P4 | S1 前端：`transcriptProjector`、快照 + 重放、只由订阅流渲染；按 3.4 逐项删除 | P2、P3 | 大 | 高：分多个提交，每个提交 `KNOWN` 不变长 |
| P5 | S3 `session_state` 事件，前端改看它 | P4 | 中 | 中 |
| P6 | S4 防护、S5 巡检 | 无，可并行 | 小 | 低 |

P4 完成前，聊天渲染不加新功能；发现新的显示问题，先写成属性测试的场景，再决定放进哪个阶段。

## 6. 不做什么

- 不改 `session_events` 为持久化：它仍是内存里的实时通道，库仍是唯一持久记录；水位只用来
  衔接两者。重启后 epoch 变化，前端只拿快照，不重放。
- 不改多 tab 隔离、会话身份、Stop 语义等已有不变量（steering 文件里的规则照旧）。
- 不在这次范围：pipeline v1 的问题、模型行为（#12 这类）。

## 7. 待定

1. 历史接口按回合分页（`turns=`）时，水位是否仍可靠：分页只取旧消息，水位按整个会话算，
   需要确认翻页拿到的旧消息不会和重放的事件重叠。P2 里给出结论。
2. 频道（Slack 等）回合也走 `_published_turn`，投影写库对它们同样生效；要确认频道侧不依赖
   现在的写库格式。
3. 已有会话的旧记录没有水位：快照里没水位时，前端只用快照，不重放（与重启同处理）。
