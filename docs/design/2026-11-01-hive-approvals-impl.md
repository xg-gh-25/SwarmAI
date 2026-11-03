---
created: 2026-11-01
updated: 2026-11-03
title: "Hive 审批按责任链：实现方案（对照 2026-11-01 的代码）"
status: implemented — 已实现并经两轮专家审查（见 §4）；设计见 2026-10-31-hive-approvals-person-and-host.md。2026-11-02：§1.2 的 10 分钟兜底已删掉，由 Conductor 叫醒派单会话取代（commit 0959a2a6b，`core/swarm_out_wake.py`；XG 改为要叫醒、不按时间交人），H12 已改成"host 类的等待只有 escalate 或 owner 会话不在时才进 Needs You"（`tests/test_hive_approvals_host.py`）；§1.2、§2 里写 10 分钟的地方是当时的方案
related: docs/design/2026-10-31-hive-approvals-person-and-host.md · docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md
---

# Hive 审批按责任链：实现方案

设计（r2）写于 Swarm Run 的叫醒机制之前。对着今天的 main 读过以后，有四处和设计的前提不一样，这里定下怎么落地。

## 1. 和设计前提不一样的地方

1. **今天所有单都是 agent 派的**：App 没有派发入口，派发只经 `scripts.swarm_out dispatch`，记录里有 `dispatched_from`（派单的会话）。所以
   `dispatched_by` 不另存：有 `dispatched_from` 就是 `host`、`owner_session` 就是它；没有（更早的单）就是 `person`。在 `/runs` 行上派生出来。
2. **没有"按事件叫醒会话"**（Conductor、Swarm Run §5.5 都没有）。host 知道有等待，靠的是已经上线的两条：`watch` 和下一轮的接续块。
   所以设计 §2.3 的"送到 owner_session"就是这两条，加一个兜底：host 能处理的等待 **10 分钟**没人处理，就进 Needs You，标明是哪个会话的单。
   这不是截断工作的超时，只是"该往上交了"的门槛。
3. **host 怎么回答**：不新开路由给命令直接调，沿用"命令只走 CLI"（agent-owns-its-runs，XG 选 A）。新 CLI `answer`，它要求这个会话先用
   `card` 为这个等待建过卡（证明看到的是什么，卡里存着 Hive 的 `shown_sha256` 和选项映射），回答只能是卡上的选项（不收自由文本）。
4. **旧版本的 Hive 不认 `answered_by`**，会把 host 的回答记成人的。所以 host 只能回答 Hive 写明了 `approval_class` 的等待；没写的（旧 Hive）一律
   `unknown`，交给人。

## 2. 改动

**Hive 一侧**
- `core/approval_class.py`：一个函数，由 gate 已经算出的事实给出类别（设计 §2.2 的表）；`HOST_CLASSES = {question, decision, hive_local,
  run_authorization}`。危险命令 gate 的 `pattern:` 只认产品默认的那些模式，用户自己加的模式是 `unknown`。一条命令按它命中的**所有**规则定类别
  （`for_rules`）：只要有一条是人的类别，就归人；命令里任何位置出现 `curl/wget … | sh` 都是 `remote_code`。此外，危险命令只有
  **一条简单命令**（没有 `; & | 换行`、命令替换、进程替换）且程序在本机白名单（chmod、chown、kill、dd、mkfs、rm、git，或写设备、
  写 `/etc`）时才给 host 类别；`sudo`、`sh -c`、`env`、任何组合一律 `unknown`，归人（`security_hooks._host_answerable_shape`，fail closed）。
- 各 gate 发到 board 的 prompt 带 `approval_class`；桥的问题/审批记录、`decisions.json` 的条目原样带上。
- 回答文件多 `answered_by`。桥送达前按类别检查：`host` 回答了不在 `HOST_CLASSES` 的类别 → 拒收，原因 `not_host_class`（H3）。
- 送达时带上是谁：问题 → `continue_with_answer(answered_by="host")`，`ask_question_manager` 不再把 `host` 改成 `person`，ask gate 只在
  `person` 时记 `human_answer`；决定 → 引擎新事件 `host_answer`；审批 → 是谁随决定一起传下去（`continue_with_cmd_permission(..., by)` →
  `set_permission_decision(..., by)`，只在有 gate 在等时留下），gate 读出来：
  `required_approval` 只在人批时计数；v1 授权的 `auth` 事件多一个可选的 `authorized_by: host`。
- 引擎 `host_answer`：和 `human_answer` 一样结束等待，但计入 `host_decisions`，不计 `human_decisions`。两者共用这一单的一个
  `decision_cap`（`lifecycle.answered`，SOUL P5）：到上限后不再问任何人，由这一单自己的 agent 凭证据决定（DC-2）；已经问出去的决定，
  它的回答永远不会因上限被拒。审计里 Autonomy 的 `human_decisions` 不含 host，另报 `host_decisions`（交付报告那一行写 "+N by the
  dispatching agent"）；v1 授权中 `authorized_by: host` 的不算人工介入。provenance 里 host 的回答有自己的根 `host_answer`，信任级别
  `external`，不是用户的。

**Host 一侧**
- `_clean_ask` / `_clean_decision` 带上 `approval_class`（只收认识的值，否则 `unknown`）；`/runs` 的等待、`watch` 和接续块都显示它，以及
  "你来答" 或 "交给人"。
- `swarm_out.answer(..., answered_by)`：面板的路由写 `person`，聊天卡片转交写 `person`，CLI `answer` 写 `host`。
- `swarm_out_wait_card.answer_as_host(session, dispatch, request, answers, why)`：要求卡是这个会话建的、`dispatched_from == session`（H5）、
  类别在 `HOST_CLASSES`、回答映射到卡上的选项；写 `answered_by: host`，卡上记 `forward: {status: forwarded, by: host, why}`；同时记进这一单的
  `commands`（类别、卡上问了什么、选了什么、理由，H8）；`/runs/{id}/result` 带 `host_answers`，单子详情的"自主"页逐条列出（2026-11-01 补：最初只写不读）。
- ops 表加 `ask.answer_host`（调用方 owner_agent，经 CLI）；`run_owner_gate` 照旧拒绝命令里直接调 Swarm Out 的回答路由。
- CLI `escalate <dispatch> <request> --why`：记一条"host 交上来"，这个等待进 Needs You（阻塞），写明是哪个会话交上来的、为什么。
- Needs You：host 派的单，"交给人"的类别、已 `escalate` 的、owner 会话已不存在的、host 类别但 10 分钟没人处理的，各成一条，写明归哪个会话；
  人直接管的单照旧按数量汇总。10 分钟从 Host 第一次看到这个等待算起（Host 自己的时钟，`seen_at`），Hive 的 `asked_at` 只在不晚于现在时参与；
  两个都没有就当作已经超时。`escalate` 的理由只放在条目的说明里，不放进点击后以人的口吻发出的消息。
- `escalate` 只接受这一单当前打开的等待；`card` / `watch` 对已 escalate 的等待都说 "person"。
- 两条新路由按情况返回：输入不对或 Hive 拒绝 400，不是这个会话或不是它的类别 403，已经答过或已 escalate 409；`pick` 和 `answers`
  必须二选一（422）。Hive 的拒收原因 `not_host_class` 在 Host 和前端都认识。
- `s_swarm-out` 第 4 步改写：host 类别自己答（`card` 看清，`answer` 选项 + 理由）；拿不准、问的是人的意图、或超出授权，`card` 问人或 `escalate`；
  "交给人"的类别直接 `card` 问人。
- 介入计数（`swarm_out_results`、`runReview`）只数 `answered_by: person`，host 处理的另计。

## 3. 不变量（最后一起跑的测试集）

H1–H8 见设计 §5，另加：
- H9 旧 Hive（等待没写类别）：host 的 `answer` 被拒，等待照旧交给人。
- H10 host 的回答在 Hive 上永远不产生 `human_answer`，`ask_question_manager` 保留 `host`。
- H11 host 和人的回答共用一个 `decision_cap`：到上限后不再问，由这一单的 agent 凭证据决定；已问出去的决定的回答不会因上限被拒。
- H1b 命中多条规则的命令，只要有一条是人的类别就归人。
- H12 Needs You：host 能处理且 owner 在、10 分钟内的等待不打扰人；另外四种情况各进 Needs You，写明归属。

## 4. 审查

security（类别映射、Hive 侧拒收、CLI 的身份）+ state-machine/correctness（引擎 `host_answer`、计数）+ api-contract（回答文件、ask 记录的新字段，
新旧 Host/Hive 互通），最多两轮。

### 审查记录（第一轮）

三位专家（security、state-machine + correctness、api-contract）读同一份 diff。按原因归组，一次修完：

| 原因 | 发现 | 处理 |
|---|---|---|
| 类别只看第一条命中的规则 | HIGH：`sudo git push --force`、`kill -9 1; gh repo delete …` 被归成 `hive_local` | 按所有命中规则定类别，人的类别优先；不锚定的 remote-code 检查；gate 层和规则层都有测试 |
| host 单独的决定上限 | MED×3：到上限时 Hive 用 `not_recorded` 拒收、卡片已标成转交、卡片路径吞掉错误、DC-2 永远走不到 | 删掉 host 单独的上限，两者共用一个 `decision_cap`，到上限走 DC-2 |
| "是谁决定的" 放在旁边的表里 | MED + LOW：送达失败时标记留下，之后人的决定被记成 host 的 | `by` 随决定一起传到 `set_permission_decision`，只在有 gate 等待时保存；删掉 `mark/peek_decided_by` |
| 线上的约定不完整 | MED：`not_host_class` 不在 `REFUSAL_REASONS`；新路由一律 403；LOW：`pick`/`answers` 可同时给；escalate 后仍说 "you"；escalate 不查请求 id | 补原因码和 TS 类型；`CardError.status`；校验器；`_who` 看 escalation；只接受打开的等待 |
| 计时用的是 Hive 的时钟 | LOW×2：Hive 时钟快了或 `asked_at` 读不出，10 分钟规则永远不触发 | Host 记 `seen_at`，取较早者；都没有当作超时 |
| escalate 理由进入以人的口吻发出的消息 | LOW：注入的理由可能被一键发给聊天 agent | 理由只进 detail |
| 卡片锁 | LOW：`answer_as_host` 的锁不回收，并和 `_forward_once` 的回收方式冲突 | 一个带计数的 `_card_lock`，两条路径共用 |
| 记录不全 | MED：provenance 没有 host 的回答；LOW：`_by` 不修剪；`host_decisions` 不显示 | provenance 新根 `host_answer`（external）；修剪 `_by`；交付报告的 Autonomy 行显示 |

测试：`test_hive_approvals_host.py` 64 条；变更点的 25 个变异全部被抓到。相关套件（security_hooks、权限、run_protocol、
pipeline conformance k1–k5、swarm_out 各套件、attention、cdf provenance）全绿。`test_pipeline_attention.py::test_products_before_a_pause_reach_the_turns_result`
两条在 main 上同样是红的，不是这次引入的。

### 审查记录（第二轮，最后一轮）

只看修复部分。决定上限、`by` 的传递、计时、卡片锁、路由状态码都没有问题。又在同一处找到两个 HIGH：逐条检测看不穿
`sudo -H …`、`sudo -u x …`、`sudo sh -c "…"`、`bash -c "$(curl …)"`、`| python3` 这类写法，人的命令仍可能被归成 `hive_local`。
这是"逐条补检测"本身的问题，不再补规则，改成 fail closed：只有一条简单命令、程序在白名单里时 host 才能答，其余都归人（见 §2）。
代价：`sudo apt-get install …` 这类请求也交给人（和这次改动之前一样），`cd x && git reset --hard` 这种组合的丢弃也交给人。
两个 LOW 一并修：`_FIRST_SEEN` 去掉已经不在运行的 Hive 的条目；`escalate` 先刷新一次快照，并按 Hive 匹配。
审查发现的命令全部写进 `test_a_commands_class_comes_from_every_rule_it_trips`；新增的守卫和匹配各有变异测试，全部被抓到。

**记下不修（在这次范围外）：**
- 新路由从请求体拿 `session`，和其他 agent 限定的操作一样只靠 `run_owner_gate` 的命令文本匹配挡直接调用（审查 MED，已有的信任方式）。
  要真正绑定调用方会话，需要给所有 agent 限定的操作一起做服务端会话凭证，另起设计。类别限制在 Host 和 Hive 两边都照样生效。
- 危险命令的 `curl … | bash` 模式只在命令开头匹配，`cd /tmp && curl x|bash` 根本不进 gate（已有问题）。
- 一次批准会按命令原文缓存在会话里，host 批准也一样（已有机制；同一条命令类别相同，host 本来也能再批）。
- pipeline 看板的卡片不显示 `host_decisions`（Swarm Out 的结果里有 `host_handled`）。

## 5. 不在这次

- **叫醒派单会话（XG 2026-11-01 接受不做；2026-11-02 改为要做）：** 没有按事件叫醒空闲会话的机制，所以 agent 只在它正跟着这一单（`watch`、下一条消息的接续块）
  时处理等待；派完单就结束这一轮的，等待 10 分钟后进 Needs You，由人答。"agent 替你管"只在它守着的时候成立。2026-11-02 XG："谁发的谁负责，减少人的注意力
  浪费。"叫醒由 Swarm Run r7.11 §5.5 的 Conductor 做，对所有 agent 派的单生效，会话忙时不计时；`attention_authority` 的 `HOST_WAIT_UNHANDLED_S` 那一支
  随之删掉，H12 改为"host 类的等待只有 escalate 或 owner 会话不在时才进 Needs You"。
- **接管：** Swarm Out 没有接管功能，派单会话固定；会话不在了，等待交给人（H5 的"接管以后归新的 owner"没有对应的入口）。

- 设计 §4 第 1 层（Hive 上把外部内容当数据的防注入能力）另起设计；第 2、3 层由本次的结构化回答、`shown_sha256`、类别拒收覆盖。
- "人签名"（ToDo 69fa7b1c）。
