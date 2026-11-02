---
title: Hive 审批 —— 按责任链：谁派的单，谁负责、谁审批
created: 2026-10-31
updated: 2026-11-02
project: SwarmAI
status: r2 P0–P2 implemented（2026-11-01，见 2026-11-01-hive-approvals-impl.md）；未做：§2.3 叫醒（2026-11-02 XG 改为要做：由 Swarm Run r7.11 的 Conductor 叫醒所有 agent 派的单，同时去掉 impl 的 10 分钟交人）、§3（Swarm Run 未建）、§4 第 1 层；§6 的 SOUL P5 补充 XG 已批（2026-10-31）
superseded_in_part: 派出去的会话上，归人的那类等待不再发出、当场拒绝（2026-11-01-brain-to-hive-zero-touch r3，XG 2026-11-01「我们要 zero-touch」）；host 类的回答路径不变
tags: [swarm-out, hive, approval, host, governance, accountability]
related:
  - docs/design/2026-10-31-swarm-out-hive-decisions.md（pipeline 决定的快照和回答）
  - docs/design/2026-10-27-swarm-out-v0-dispatch.md §3.3（S3 桥）
  - docs/design/2026-10-31-swarm-out-ops-registry.md 和 `backend/core/swarm_out_ops.py`（调用方和权限）
  - docs/design/2026-10-31-swarm-run.md（lead run 管自己的子单）
  - docs/design/pipeline-v1-human-loop.md（`human_answer` 和 v1 授权）
  - docs/design/2026-10-31-human-proof-verification.md（人专用动作的更强保证）
---

# Hive 审批：按责任链

## 0. 方向：责任链

**XG 2026-10-31：**
- "Hive run 得支持 human 审批，还有 SwarmAI host 审批。我把 Hive 作为 SwarmAI host 的 workers，我期望 SwarmAI host 能像管自己的 workers 一样管理它们。"
- "SwarmAI host 既然被委派了，自己去指挥或者管理 Hive run 做执行，那就得自己 take responsibility 来 manage 它们。Hive run 的事情 Swarm host 自己负责，负责不了了再
  escalate 到人。……人找 Swarm host 问责，Swarm host 找它用的 Hive run 问责；除非是人直接派的 Hive run，那人只找 Hive run 问责。"
- "Hive 被注入是我们应该做的安全问题，不能因为有这种潜在的问题就把问题给人，是应该让 Hive 做到不被注入。"

由此三条，全文都从这里推：

1. **谁派的单，谁负责，谁审批。** host 派的单（人在 chat 里让 host 去派、lead run 派子单），由派它的 host session 管到底，搞不定的才 escalate 给人。
   人直接派的单（Swarm Out 界面上点派发），人直接管它。
2. **委派出去的权力不超过委派方自己的权力。** host 能替 worker 批的，就是 host 自己不用问人就能做的事。host 自己做之前也要问人的事（出机器、不可逆、
   碰到人的数据），worker 做时也要交给人，原因是 host 没有这个授权，**不是**怕 host 被骗。
3. **防注入是 Hive 和桥自己的安全能力**（§4），不是把事情交给人的理由。

## 1. 现状（对照代码）

> 2026-10-31 写本文时的代码；之后的改动见 2026-11-01-hive-approvals-impl.md。

| 事实 | 出处 |
|---|---|
| Hive 上 session 停下来等的东西都在 prompt board 上：AskUserQuestion，以及四个 gate 的审批：危险命令、外部工具、销毁数据存储、v1 授权 | `core/security_hooks.py:1893-2070`、`:2102-2215`；`core/data_safety.py:460-480` |
| 危险命令 gate 知道拦下的原因（`rule`）：`pattern:<glob>`、`rm_unresolved_target`、`irreversible_external`、`irreplaceable_store`、`v1_authorization`、`reserved_action`、`discard_foreign_uncommitted` / `discard_unchecked`；发到 board 的 prompt 只有 `toolName`、`toolInput`、`reason`，不带原因 | `core/security_hooks.py:1845-1867,1925-1980,2041-2050` |
| 默认危险模式：本地破坏类（`sudo`、`chmod 777`、`chown -R`、`kill -9`、`dd`、`mkfs`、写设备、写 `/etc`、递归 rm），以及执行远程代码类（`curl … \| sh`、`wget … \| bash`） | `core/security_hooks.py:125-147` |
| pipeline 的 DC-1 决定在 Hive 上写进 `decisions.json`，Host 只有人能答；Hive 一律记成 `human_answer` | `core/swarm_out_decisions.py` |
| 桥送达 AskUserQuestion 的回答时 `answered_by="person"`；ask gate 只在 `person` 时把卡片上的选择记成 `human_answer` | `core/swarm_out_bridge.py`；`core/security_hooks.py:1800-1810` |
| v1 授权（恢复落地、`accept-finding`、`discard-snapshot`）每次都问，按设计是人的决定 | `core/security_hooks.py:485-508,1995-2002`；`stages/deliver.md:21` |
| Host 回答走 `ask.answer`，只给 person | `core/swarm_out_ops.py:81-84` |
| 派发记录有 `origin` 和 Hive 上的 `session_id`，但**不记谁派的**（人还是 host 的哪个 session）；agent 的命令前面会被加上 `SWARM_APP_SESSION_ID` / `SWARM_TURN_ID`，CLI 知道是哪个 session 在调用 | `core/swarm_out.py:260,346-358`；`core/run_owner_gate.py:531-549` |
| Hive 实例角色只有 Bedrock、自己 bridge 前缀的 S3、给自己打标签；Hive 上装着派过去的 Brain 副本（人的私有知识）和任务仓库的 clone | `hive/provisioner.py:62-125`；`routers/hive_brains.py` |
| SOUL P5："A gate decides from … or a human's decision, never from a model's opinion." | `backend/context/SOUL.md`（P5） |

## 2. 设计

### 2.1 谁派的单：派发记录写明，由调用方推出，不另设开关

> 实现：App 没有派发入口，`dispatched_by` 不另存，由 `dispatched_from` 推出（impl §1.1）。

- 派发记录加 `dispatched_by: person | host`，以及 `host` 时的 `owner_session`（派单的 host session）。
  - 由调用路径定：Swarm Out 界面的派发路由是 `person`；`scripts.swarm_out dispatch`（agent 的 CLI，命令前带着 `SWARM_APP_SESSION_ID`）是 `host`，`owner_session` 取那个
    session；lead run 经引擎派子单是 `host`，`owner_session` 是 lead run 的会话。
  - 写进派发记录以后不再改。接管（`owner_transfer`）会改 lead run 的 owner，这时 `owner_session` 跟着 lead run 当前的 owner 走（从 run 读），不从记录读。
- `dispatched_by = person` 的单子：今天的行为，人直接管它。host 不碰它的等待。
- `dispatched_by = host` 的单子：由 `owner_session` 管（§2.3）。人仍然可以随时先答（§2.5）。

### 2.2 每个等待都带一个确定的类别

拦下请求的那个 gate 在发 prompt 时写上 `approval_class`，桥原样镜像到 Host。类别只由 gate 已经算出的事实决定，不靠模型判断。哪些类别 host 自己能处理，
按"它影响到什么"定，不按"在哪台机器上执行"定：只影响 host 为这一单管着的 worker，就在 host 的授权里；碰到人的东西或外面的世界，就不在。

| 类别 | 由什么定 | 影响到什么 | host 派的单 |
|---|---|---|---|
| `question` | AskUserQuestion | 任务本身 | host 处理 |
| `decision` | pipeline 的 DC-1 决定 | 任务本身 | host 处理 |
| `hive_local` | `pattern:` 里的 `chmod`、`chown`、`kill`、`dd`、`mkfs`、写设备、写 `/etc`；`rm_unresolved_target`；`discard_*`。只限一条简单命令；`sudo`、`sh -c` 和任何组合命令归人（实现文档 2026-11-01 §2） | 这台 Hive 和它的工作区 | host 处理 |
| `run_authorization` | `v1_authorization` | Hive 上那个 run 的 clone，不碰 host 的 main | host 处理 |
| `remote_code` | `pattern:` 里的 `curl … \| sh`、`wget … \| bash` | 执行外面来的代码，能碰到 Hive 上人的 Brain 副本和 bridge 凭证 | 交给人 |
| `off_machine` | 外部工具 gate；`reserved_action`（push、开 PR、发布、对外发消息） | 外面的世界 | 交给人 |
| `irreversible_external` | `irreversible_external` | 外面的世界，不可逆 | 交给人 |
| `destructive_data` | `data_safety`、`irreplaceable_store` | 不可替代的数据（STEERING #20） | 交给人 |
| `unknown` | 以上都不是（新的 gate、旧版本的 Hive） | 不知道 | 交给人 |

"交给人"的这几类，在 host 自己的机器上 host 做之前也要问人（AGENT § How to Act 的 Ask first、STEERING #5、#20、危险命令 gate）。所以这里没有给 worker
另设规则，只是让 worker 的边界和 host 自己的边界一致。以后人要放宽 host 自己的授权，worker 跟着放宽；要收窄，也跟着收窄。

### 2.3 host 怎么管：owner session 负责到底

> 实现与此不同：没有叫醒，靠 watch 和接续块，10 分钟没人处理进 Needs You（impl §1.2、§5）；派出去的会话上归人的类别当场拒绝，不进 Needs You（zero-touch r3）。
> 2026-11-02（XG："谁发的谁负责，不要什么都往 human 上放"）：叫醒要做，按本节原意，由 Swarm Run r7.11 §5.5 的 Conductor 实现，对所有 agent 派的单生效；
> 10 分钟交人随之删掉，等待进 Needs You 只剩 `escalate` 和 owner session 不在了两种。

- **送到谁手里：** `owner_session`。和 Swarm Run 的叫醒同一套：`run_conversation`、`system` 来源、按事件的 `client_id`、只在会话空闲时发
  （Swarm Run §5.5）。内容是结构化字段和位置，Hive 写的问题文字按数据给出。
- **怎么回答：** owner agent 用 CLI 回答，op 记为 `ask.answer_host`，调用方 `owner_agent`（lead run 经 `internal:conductor`）。只能答 `owner_session` 是自己的单子，
  只能答 host 处理的类别。审批和决定照旧要带它看到的内容的 `shown_sha256`。
- **搞不定才往上：** owner agent 觉得这其实是在问人的意图、或者没把握、或者已经超出它这一单的授权，就 `escalate`，进 Needs You，标明"host <session 标题> 交上来的"。
  超出授权的类别（"交给人"那几类）不经过 owner agent，直接进 Needs You，同样标明是哪个 host session 的单子，因为人问责的对象仍然是那个 host session。
- **owner session 不在了**（tab 关了、被丢弃）：它的等待进 Needs You，标明原来归谁（没有 Conductor，也没有接管，impl §5）。
- **向人交代：** host 在 worker 上处理过的每一件事都记下（谁、什么类别、问了什么、答了什么、为什么），进 owner session 的交付总结；Swarm Run 里进 lead run 的 REPORT。人问责 host，
  host 拿得出账。实现：每次回答记进这一单的结算记录，单子详情的"自主"页逐条列出（`swarm_out_actor.host_answers`）。

### 2.4 回答的人一路如实记录

回答文件只能由 Host 写（IAM），多一个字段 `answered_by: person | host`。Hive 的桥按它送达，并且**在 Hive 侧按类别检查**：`host` 回答了"交给人"的类别 →
拒收，记为 `refused`，和 digest 不对一样处理。Host 侧的检查只是第一道。

| 类别 | Hive 上怎么记 |
|---|---|
| `question` | `continue_with_answer(answered_by="host")`；ask gate 只在 `person` 时记 `human_answer` |
| `decision` | 引擎新增 `host_answer` 事件，和 `human_answer` 分开；人和 host 的回答各自计数，但共用这一单的一个 `decision_cap`（到上限由这一单的 agent 凭证据决定，DC-2；实现文档 2026-11-01 §2）；Autonomy Judge 不把它算成人工介入 |
| `hive_local` | `continue_with_cmd_permission`；记下批的是 host；`required_approval` 计数只算人批的 |
| `run_authorization` | 引擎的一次性授权多一个 `authorized_by: host`；只有 `dispatched_by = host` 的 run 才接受 host 授权 |

所有读"人插手几次"的地方只数 `person`；host 处理的另计为"host 处理 N 次"（Swarm Run §5.10 同一口径）。

### 2.5 人随时可以先答

人看得到所有单子的等待（Swarm Out 和 Needs You），随时可以直接回答，同一个等待只认第一份回答（`answer/` 现在就是只写一次）。人答了 host 派的单子，算人对 host
的介入，记在 host 那一单的账上。

## 3. Swarm Run

lead run 派的子单 `dispatched_by = host`，`owner_session` 是 lead run。子单的问题、决定、Hive 本地审批和 v1 授权都由 lead run 处理，如实记为 host；
"交给人"的几类交给人，标明是哪个 Swarm Run 的。见 Swarm Run r7.8。

## 4. 防注入：Hive 和桥自己的安全能力

host 管 worker，就要读 worker 写的东西；所以防注入要做在这条链上，不是靠把事情交给人。三层，前两层是能力，第三层是兜底：

1. **Hive 上：** 外部内容（网页、下载的文件、仓库里别人写的文字）按 SECURITY-BASELINE 的 B 类规则处理：
   - 当数据，不当指令；
   - 防"读入投毒 → 外传"的链路；
   - 不削弱已有的控制；
   - 不往状态里写投毒内容。
   这些作为 Hive 的安全能力单独设计和测试，不只是提示词。
2. **Hive 到 host 之间：**
   - Hive 写的问题、报告、选项文字进 host 时只按数据给出，叫醒消息不内联原文；
   - 审批绑定 `shown_sha256`，改写过的卡片骗不到 host；
   - host 回答用的是结构化的选项 id，不是自由文本。
3. **不靠模型的兜底：**
   - Hive 的 IAM 权限很窄；
   - "交给人"的类别由 Hive 桥确定性拒收 host 的回答；
   - host 自己的动作仍受 host 上的产品级审批约束。

   模型做不到百分之百防注入，所以这一层必须是确定性的。

"只有人能做"的强度：Host 上的 agent 如果读到账户凭证，可以自己写一个 `answered_by: person` 的回答。这是 ops registry 第一阶段已知的缺口
（`2026-10-31-swarm-out-ops-registry.md` §2.4），要等第二阶段（`2026-10-31-human-proof-verification.md`）解决，本设计不改变它。

## 5. 分期和不变量

- **P0：**
  - 派发记录写 `dispatched_by` / `owner_session`；
  - gate 给 prompt 写 `approval_class`，桥镜像；
  - 回答文件带 `answered_by`，Hive 桥按类别检查；
  - `question` 类接通；
  - `ask.answer_host` 进 ops registry；
  - owner agent 的回答和 escalate 命令；
  - 超出授权的类别直接进 Needs You，标明归属。
- **P1：** `decision` 类：引擎的 `host_answer` 事件、决定计数分开、Autonomy Judge 认它。
- **P2：** `hive_local`、`run_authorization` 两类（§6 的 P5 补充已批）。
- **另起：** §4 第 1 层的 Hive 防注入能力。

| # | 不变量 |
|---|---|
| H1 | 每个等待都有 `approval_class`；不认识的一律是 `unknown`，交给人 |
| H2 | 每个派发记录都有 `dispatched_by`；`person` 派的单子行为和今天完全一样 |
| H3 | `answered_by: host` 的回答只对 host 处理的类别生效，对"交给人"的类别 Hive 一律拒收 |
| H4 | host 的回答不产生 `human_answer`，不算进人的介入次数；人的回答照旧 |
| H5 | 只有这一单的 `owner_session` 能以 host 身份回答；不在了就交给人，并标明原来归谁 |
| H6 | 人随时可以先答；同一个等待只认第一份回答 |
| H7 | host 回答也要带 `shown_sha256`，对不上就拒收 |
| H8 | host 在 worker 上处理的每件事都进它的交付总结或 REPORT |

审查分工：类别映射和 Hive 侧拒收由 security 审；`dispatched_by` 的来源由 security 加 api-contract 审；引擎的 `host_answer` / `authorized_by` 由 state-machine 加 correctness 审。

## 6. SOUL P5 的补充（XG 2026-10-31 已批）

`question` 和 `decision` 不涉及 P5：它们是任务本身的问题，不是 gate，按 SOUL P10 本来就是 agent 的事。`hive_local` 和 `run_authorization` 是让 host 放行一个 gate。
P5 原句是 gate 只从确定性事实"or a human's decision, never from a model's opinion"里出结果，现在改为（`backend/context/SOUL.md` P5；`SELF.md` 的 My Gates 同步）：

> … or the decision of whoever answers for the work, never from a model's opinion of its own work. Who answers follows delegation: a person answers for what
> they run or dispatch; an agent that dispatches workers answers for them. It decides their waits, records each as its own decision (never as a person's), and
> escalates what it cannot handle. It may never decide more than it may do itself without asking: anything off-machine, irreversible, or touching the person's
> data still goes to the person.

本设计和这句一一对应：
- "of its own work"：agent 仍然不能给自己的命令开 gate；
- "records each as its own decision"：`answered_by: host`、`host_answer`；
- 最后一句：§2.2 里"交给人"的那几类，由 Hive 桥确定性拒收。

## 7. 修订记录

- r1：类别和 host 回答；`approver` 是派工单上的一个开关；"不能批"的理由写成"怕 host 被注入"。
- r2（P5 补充随后获批）：按 XG 的责任链重写：
  - `approver` 开关改为由谁派的推出（`dispatched_by` / `owner_session`）；
  - 边界的理由改为"委派出去的权力不超过委派方自己的"，类别按"影响到什么"定；
  - 超出授权的照旧交给人，但标明是哪个 host session 的单子；
  - 防注入单列为 Hive 和桥的安全能力（§4）；
  - 加上 host 向人交代（H8）。
