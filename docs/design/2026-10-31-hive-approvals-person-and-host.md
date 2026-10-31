---
title: Hive 审批 —— 人和 SwarmAI host 都能批
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: r1 design only（无代码）；§6 有一个要 XG 拍板的治理问题
tags: [swarm-out, hive, approval, host, governance]
related:
  - docs/design/2026-10-31-swarm-out-hive-decisions.md（pipeline 决定的快照和回答）
  - docs/design/2026-10-27-swarm-out-v0-dispatch.md §3.3（S3 桥）
  - docs/design/2026-10-31-swarm-out-ops-registry.md 和 `backend/core/swarm_out_ops.py`（调用方和权限）
  - docs/design/2026-10-31-swarm-run.md（lead run 管自己的子单）
  - docs/design/pipeline-v1-human-loop.md（`human_answer` 和 v1 授权）
---

# Hive 审批：人和 SwarmAI host 都能批

## 0. 方向

**XG 2026-10-31：** "Hive run 得支持 human 审批，还有 SwarmAI host 审批。我把 Hive 作为 SwarmAI host 的 workers，我期望 SwarmAI host 能像管自己的
workers 一样管理它们，相当于给 SwarmAI 配的能力。"

一句话：Hive 上 run 停下来等的东西，除了人，**派它的那个 host session（owner agent）也能回答**；回答的人是谁，一路如实记录；有几类审批不论谁派都只给人。

## 1. 现状（对照代码）

| 事实 | 出处 |
|---|---|
| Hive 上 session 停下来等人的东西都放在 prompt board 上：AskUserQuestion，以及审批。审批来自四个 gate：危险命令、外部工具、销毁数据存储、v1 授权 | `core/security_hooks.py:1893-2070`（危险命令）、`:2102-2215`（外部工具）；`core/data_safety.py:460-480` |
| 危险命令 gate 知道拦下的原因（`rule`）：`pattern:<glob>`、`rm_unresolved_target`、`irreversible_external`、`irreplaceable_store`、`v1_authorization`、`reserved_action`、`discard_foreign_uncommitted` / `discard_unchecked`；但发到 board 的 prompt 只有 `toolName`、`toolInput`、`reason`，不带原因 | `core/security_hooks.py:1845-1867,1925-1980,2041-2050` |
| 默认危险模式里有本地破坏类（`sudo`、`chmod 777`、`kill -9`、`dd`、`mkfs`、写设备、写 `/etc`、递归 rm），也有执行远程代码类（`curl … \| sh`、`wget … \| bash`） | `core/security_hooks.py:125-147` |
| pipeline 的 DC-1 决定在 Hive 上写进 `decisions.json`，Host 只有人能答；不管是卡片还是直接记账，Hive 都记成 `human_answer` | `core/swarm_out_decisions.py`；`docs/design/2026-10-31-swarm-out-hive-decisions.md` |
| 桥送达 AskUserQuestion 的回答时 `answered_by="person"`；ask gate 只在 `person` 时把卡片上的选择记成 run 的 `human_answer` | `core/swarm_out_bridge.py`（`_deliver`）；`core/security_hooks.py:1800-1810` |
| v1 授权（`run-land --request` 恢复落地、`accept-finding`、`discard-snapshot`）按设计是"人对一条已记录请求的决定"，每次都问 | `core/security_hooks.py:485-508,1995-2002`；`stages/deliver.md:21` |
| Host 回答走 `ask.answer`，只给 person | `core/swarm_out_ops.py:81-84` |
| Hive 实例角色只有 Bedrock、自己 bridge 前缀的 S3、给自己打标签；Hive 上装着派过去的 Brain 副本和任务仓库的 clone | `hive/provisioner.py:62-125`；`routers/hive_brains.py` |
| SOUL P5："A gate decides from a parsed command, a diff or path set, a marker, a count or hash, an exit code or test outcome, or a human's decision, never from a model's opinion." | `backend/context/SOUL.md`（P5） |

## 2. 设计

### 2.1 每个等待都带一个确定的类别

拦下请求的那个 gate 在发 prompt 时写上 `approval_class`，桥原样镜像到 Host。类别只由 gate 已经算出的事实决定，不靠模型判断：

| 类别 | 由什么定 | 例子 | host 能批吗 |
|---|---|---|---|
| `question` | AskUserQuestion | 任务里的澄清问题 | **能** |
| `decision` | pipeline 的 DC-1 决定（`decisions.json`） | 两种存储方案选一个 | **能** |
| `hive_local` | 危险命令 gate 的本地破坏类：`pattern:` 里的 `sudo`、`chmod`、`chown`、`kill`、`dd`、`mkfs`、写设备、写 `/etc`，以及 `rm_unresolved_target`、`discard_*` | 清掉工作区重来、杀一个卡住的进程 | **能**：影响只在这台 Hive 上 |
| `run_authorization` | `v1_authorization`（恢复落地、接受 finding、丢弃快照） | Hive 上的 pipeline 子单接受一个剩余 finding | **能**：只作用于 Hive 上那个 run 的 clone，不碰 host 的 main |
| `remote_code` | `pattern:` 里的 `curl … \| sh`、`wget … \| bash` | 装一个脚本 | **不能**：执行外部来的代码，它能碰到 Hive 上的 Brain 副本和 bridge 凭证 |
| `off_machine` | 外部工具 gate、`reserved_action`（push、开 PR、发布、对外发消息） | `git push`、发 Slack | **不能**：产品级"出机器的事先问人" |
| `irreversible_external` | `irreversible_external` | 改 repo 可见性、force push | **不能** |
| `destructive_data` | `data_safety`、`irreplaceable_store` | 删一个不可替代的数据存储 | **不能**：STEERING #20，绝对只给人 |
| `unknown` | 以上都不是（新的 gate、旧版本的 Hive） | — | **不能**：默认只给人 |

"能"的意思是 host 可以回答，不是 host 必须回答：人随时可以先答，谁先写进 `answer/` 谁算数（现在就是只写一次）。

### 2.2 谁是 host 那一方：派这一单的 owner agent

- 每一单在派工单里多一项 `approver`：`person`（今天的行为）或 `host`。人从 chat 派单时由派单的 agent 按任务选，默认 `host`；Swarm Run 的子单固定是 `host`。
  选 `person` 时一切和今天一样。
- `approver = host` 时，host 那一方是**派这一单的 session**（owner agent；Swarm Run 里就是 lead run）。不新增调用方类型：owner agent 用 CLI 回答，op 记为
  `ask.answer_host`，调用方 `owner_agent`（Swarm Run 里经 `internal:conductor`）。只能答自己派的单子，只能答 §2.1 里"能"的类别。
- **怎么到 owner agent 手里：** 和 Swarm Run 的叫醒同一套（`run_conversation`、`system` 来源、按事件的 `client_id`、只在会话空闲时发，见 Swarm Run §5.5）。
  内容是结构化字段和位置；Hive 写的问题文字按数据给出，不内联（AGENT R32 B 类）。
- **答不了就交给人：** owner agent 觉得这其实是在问人的意图、或者没把握，就 `escalate`，这一条进 Needs You，和 `approver = person` 的一样。
  owner session 已经不在了（tab 关了、被丢弃），Conductor 直接把它交给人。
- **不能批的类别**（`remote_code`、`off_machine`、`irreversible_external`、`destructive_data`、`unknown`）不发给 owner agent，直接进 Needs You，和今天一样。

### 2.3 回答的人一路如实记录

回答文件只能由 Host 写（IAM），多一个字段 `answered_by: person | host`。Hive 的桥按它送达，并且**按类别检查**：`host` 回答了一个不能批的类别 → 拒收，
记为 `refused`，和 digest 不对一样处理。Host 侧的检查只是第一道，Hive 侧这道才是准的。

| 类别 | Hive 上怎么记 |
|---|---|
| `question` | `continue_with_answer(answered_by="host")`；ask gate 只在 `person` 时记 `human_answer`，所以不会被当成人的决定 |
| `decision` | 引擎新增 `host_answer` 事件（和 `human_answer` 分开）。`decision_cap` 只限制人的决定次数；host 的决定单独计数，Autonomy Judge 不把它算成人工介入 |
| `hive_local` | `continue_with_cmd_permission`；记录这次审批的人是 host，`required_approval` 计数只算人批的 |
| `run_authorization` | 引擎的一次性授权多一个 `authorized_by: host`；只有 `approver = host` 的 run 才接受 host 授权 |

所有读"人插手几次"的地方（Swarm Out 的自主度标签、大卡片、Brain Runs tab）只数 `person`；host 处理的另计为"host 处理 N 次"（Swarm Run §5.10 同一口径）。

### 2.4 安全

- **digest 绑定照旧：** host 回答审批和决定，也要带它看到的内容的 `shown_sha256`。Hive 按自己重建的内容核对，所以改写过的卡片骗不到 host，和骗不到人一样。
- **Hive 文字影响 host：** host agent 读 Hive 写的问题，就可能受影响。兜底有三层：
  1. host 能批的类别，影响都留在 Hive 上；
  2. host 自己的动作仍然受 host 上的产品级审批约束；
  3. Hive 的问题文字只按数据给出。
- **"只有人能做"的强度：** 不能批的类别靠 Hive 桥按类别拒收，这一点不依赖 host 侧。但 Host 上的 agent 如果读到账户凭证，可以自己写一个 `answered_by: person` 的回答；
  这是 ops registry 第一阶段已知的缺口（`2026-10-31-swarm-out-ops-registry.md` §2.4），要等第二阶段（`2026-10-31-human-proof-verification.md`）解决，本设计不改变它。

## 3. Swarm Run 的变化

Swarm Run r7.6 让 pipeline 子单 `decision_cap = 0`，不向 host 要决定，原因是当时 host 回答只能记成人的。有了本设计以后：

- 子单的决定、问题、`hive_local` 审批、`run_authorization` 都由 lead run 作为 host 回答，记为 host，不冒充人；
- 子单的 `decision_cap` 回到默认；`decision_cap = 0` 这一条撤掉；
- 不能批的类别照旧给人。

见 Swarm Run r7.7。

## 4. 分期

- **P0：**
  - gate 给 prompt 写 `approval_class`，桥镜像到 Host；
  - 回答文件加 `answered_by`，桥按类别检查；
  - `question` 和 `hive_local` 两类接通；
  - 派工单加 `approver`；
  - `ask.answer_host` 进 ops registry；
  - owner agent 的回答和 escalate 命令。
- **P1：** `decision` 类（引擎的 `host_answer` 事件、决定计数分开、Autonomy Judge 认它）、`run_authorization` 类（`authorized_by: host`）。
- **之后：** 人可以按 Brain 或按派单收窄 host 能批的类别（只收窄，不能把不能批的类别打开）。

## 5. 不变量（验证计划）

| # | 不变量 |
|---|---|
| H1 | 每个等待都有 `approval_class`；不认识的一律是 `unknown`，只给人 |
| H2 | `answered_by: host` 的回答只对 `question` / `decision` / `hive_local` / `run_authorization` 生效，对其它类别 Hive 一律拒收 |
| H3 | `approver = person` 的单子行为和今天完全一样 |
| H4 | host 的回答不会产生 `human_answer`，也不会算进人的介入次数；人的回答照旧 |
| H5 | owner agent 只能回答自己派的单子；不在了就交给人 |
| H6 | 人随时可以先答；同一个等待只认第一份回答 |
| H7 | host 回答也要带 `shown_sha256`，内容对不上就拒收 |

审查分工：类别映射和 Hive 侧拒收由 security 审；引擎的 `host_answer` / `authorized_by` 由 state-machine 加 correctness 审；ops 和 CLI 由 api-contract 审。

## 6. 要 XG 拍板的一件事：和 SOUL P5 的冲突

SOUL P5 说 gate 只从确定性的事实或**人的决定**里出结果，"never from a model's opinion"。host 批准 Hive 上的 `hive_local` 危险命令，或给出 `run_authorization`，
就是让模型（owner agent）的判断放行一个 gate。这和 P5 字面上冲突。`question` 和 `decision` 不冲突：它们是任务本身的问题，不是 gate；按 SOUL P10 交给 agent
本来就对。

**建议的处理：** 给 P5 补一句范围，而不是在这里绕开它：

> 一个 gate 保护的若只是 SwarmAI 自己管理的 worker（Hive）上的东西，派这一单的 owner agent 可以替人回答，回答如实记为 agent 的；凡是出机器、不可逆、
> 或碰到不可替代数据的，仍然只有人。

SOUL 是 system-owned，只能经 s_self-evolution（人的审批）改。所以 P0 只先做 `question`；`hive_local` 和 `run_authorization` 等 XG 同意这条 P5 补充后再接。
