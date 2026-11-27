---
title: "岗位（Duty）：让 Job 带着 Brain 干活，再升级为一个 Brain 的长期职责"
created: 2026-11-27
updated: 2026-11-27
project: SwarmAI
status: draft r2（r1 经一次零上下文审查后改写；待 XG 定 §7）
related:
  - docs/design/2026-11-09-project-agent-contract.md
  - Knowledge/Designs/2026-07-19-ddd-jobs-tools-registry-design.md（J2：job 进 Brain）
---

# 岗位（Duty）

> **r1 → r2**：一次零上下文审查推翻了 r1 的证据，也找出 4 个 HIGH。
> 证据方面：GitHub_Community 的「取 → 想 → 发」三段拆分是今天上午才建的；之前的 140 次运行是
> 一个带 Bash/Write 的 agent_task 自己发评论。两轮之间也共享 `engagement_log.jsonl`，并非互不知道。
> 而且 Brain 名下 job 的成功结果已经会以 REVIEW 条目进入 Needs You，再被沉淀进 Brain
> （`backend/core/attention_authority.py:589-660`）。
> 结论是：真实缺口比 r1 说的小。r2 因此拆成两期：第一期是小改动，拿到大部分价值、不新增执行面；
> 第二期才是完整的岗位，并带上审查要求的全部修正，另有进入条件。

## 0. 缺口到底是什么（2026-11-27 读代码和本机数据）

- **agent_task 醒来时不知道自己属于哪个 Brain。** 系统提示只有三行，跑的时候用 `--no-session-persistence`，
  拿不到 Brain 的 Project Agent 约定，也看不到自己或同一个 Brain 下其他 job 上次的结果
  （`backend/jobs/executor.py:906-916`）。连续性只能靠 skill 自己维护的文件（GitHub_Community 用 `engagement_log.jsonl`）。
- **没有目标，也没有进度。** 一项长期职责做得怎么样，没有一个地方能看到；周报靠每个 skill 自己写
  （GitHub_Community 的 `report.py`）。
- **每次醒来都要调一次模型，没有「有没有变化」的预检。**（Kiro Bot 也有同样的弱点。）
- **已有、不用重做的：** Brain 对 job 的所有权（`bindings.yaml` 的 `kind: job`，由 `ddd_job_registry` 建索引）；
  成功结果回流到 Brain（`_collect_job_digests`）；连续失败熔断后进 Needs You，由 attention_auto 先自己修
  （`attention_authority.py:366-406`，`backend/core/unattended_gate.py:22-27`）。

## 1. 必须遵守的已定决策

| 决定 | 约束 |
|---|---|
| Project Agent 是角色，**不是常驻进程**（`2026-11-09-project-agent-contract.md` §0、§6） | 每次醒来都是一次会话；不做独立 agent 身份，也不做独立记忆 |
| 单 agent 切换角色，优于多 agent | 同上 |
| 人只在两头 | 开头由人定目标，结尾由人读报告 |
| 「default 待命，自己操控」「不要滥用 Needs You」 | 日常醒来一律不找人 |
| 不设花费上限（STEERING #2） | 不设预算；花费按岗位记进 usage ledger |
| 读外部不可信内容的那一段，手里不能有能动手的工具，也不能拿到私人记忆 | 见 §3.3 |

## 2. 第一期：让 agent_task 带着 Brain 干活（小改动，现在做）

1. **job 配置加 `config.brain: <Brain 名>`。** 执行器在 prompt 前面加上两样东西：一是这个 Brain 的
   `identity.summary` 和 `watches`（来自 `project-agent.yaml`）；二是这个 job，以及同一个 Brain 名下其他 job，
   最近 N 次 JobResults 的摘录。**不加** MEMORY、USER、召回，也不加 rules。理由：
   这类 job 的输出常常直接交给发布脚本，而发布脚本的泄漏检查只认凭据和路径
   （`publish.py:67-120`），拦不住私人文字。
2. **取 → 想 的顺序改成 `after:`。** GitHub_Community 的「55 7 取 / 0 8 想」现在只是碰巧有序，改成
   `after:github-community-morning-fetch`。
3. **fetch、publish 两个 job 也声明进 `bindings.yaml`**，让 `ddd_job_registry` 知道它们和这个 Brain 是一组。
4. **Brain Hub 的 Brain 页加一张只读卡片「这个 Brain 的定时工作」。** 数据来自 `ddd_job_registry`、
   `state.json`，以及 `report.py` 的周报：每个 job 的上次结果、最近趋势、是否熔断。

第一期不新增任何可执行入口：执行的仍是 `user-jobs.yaml` 里受保护的 job 体（`backend/jobs/paths.py:50-73`）。

**验收：**
- 带 `config.brain` 的 agent_task，prompt 里有 Brain 约定和前几次结果，没有 MEMORY、USER（单元测试断言 prompt 文本）；
- 不带 `config.brain` 的 job，行为与今天完全一致；
- 卡片在 job 熔断时显示熔断。

## 3. 第二期：岗位（满足 §4 的进入条件后再做）

### 3.1 定义

岗位是一个 Brain 交给它的 Project Agent 的一项长期职责，包括：人定的目标、能测的进度、醒来规则、
可选的「取 → 想 → 做」三段、暂停态、周报。

```yaml
# project-agent.yaml（agent 维护）——只放引用，不放可执行文本
duties:
- id: community
  keeps: "每个工作日找出相关社区讨论，过质量关后参与，并跟进回复"
  goal_key: <goals 记录的 key，见 3.2>
  measures: [replies-7d]          # 引用 4-capabilities/ 下的脚本 id
  wake: {schedule: "0 0,1 * * 1-5", probe: monitor-fingerprint}   # UTC；probe 也是脚本 id
  stages: {fetch: monitor-fetch, act: publish}                     # 岗位自己的脚本，不引用现有 job
  report: weekly
```

### 3.2 两样东西放在 agent 写不到的地方（审查 H1、M1）

- **目标**：复用 `project_agent_rules` 的 `whole_sentences` 和 `quote_key`，以及它的审计目录，
  但单独建一份 `goals` 记录，按岗位 id 存。目标**不是 rule**：rule 会送到这个 Brain 的每一次聊天、每次审查和
  每次 Hive 派单（`project_agent.py:543-588`），还占 `MAX_RULES`。`duties[].goal_key` 只放 key，不放原文。
- **可执行集合**：建岗位时，把它引用的脚本 id 和各脚本的内容 hash，一起记进同一份人核实过的记录。
  醒来时 hash 对不上就拒绝执行，在岗位卡上标「脚本变了，待确认」。
  原因是 `project-agent.yaml` 由 agent 维护、跟着 Brain 打包，而 `Projects/*/project-agent.yaml` 不在
  `runs_later_rules` 的保护里（`backend/core/protected_paths.py:157-172`）。如果允许里面写命令，任何会话、
  或者一个从别处装来的 Brain，都能埋下一条下次醒来时以完整用户权限执行的命令。
  脚本本身放在 `4-capabilities/` 下，那里已经受保护。

### 3.3 一次醒来

1. **预检**（确定性，不调模型）：跑 probe 脚本得到指纹，执行器跑一次 measures 得到读数，再看账本里有没有到期事项。
   三样都没变，就记一行 quiet 后结束。
2. **取**：跑 `stages.fetch`。
3. **想**：起一个无人值守会话。要做的前置工作：
   - 从 `attention_auto.run_session` 里抽出一个共用核心 `run_unattended(message, preamble, tools, scope) -> 全文`（审查 H2）。
     现在的 `run_session` 只返回 `RESULT:` 行，preamble 写死成「处理 Needs You 条目」，也不接受工具列表。
   - 它跑在后台主事件循环上；从独立 `--run-now` 进程发起的岗位醒来一律拒绝，因为那个进程里没有 session router。

   **有 `stages.act`，或者要读外部内容时**，这一段这样配（审查 H3、L1）：
   - prompt：最小系统提示 + 岗位定义 + 目标原话 + 预检读数 + 账本最近几次记录，没有 MEMORY、USER、召回；
   - 工具：Read / Grep / Glob；
   - Bash：只放白名单，不含 `cognition`。

   **都没有时**，可以用完整的 Project Agent 约定。

   结尾输出一个结构化块：做了什么、未结事项、`needs_person`（只能是 §3.4 中的一种，或为空）。
4. **做**：执行器把块交给 `stages.act` 脚本，发不发由脚本自己的 gate 决定。要说清楚：
   在岗位里，对外发送**只有** act 脚本的 gate 把关，`_is_reserved_action` 只对 Hive 派单的会话生效
   （审查 L2，`backend/core/security_hooks.py:3023-3040`）。
5. **记账**：每个岗位一份 JSONL，放在 app 数据目录，**只有执行器写**。读数用第 1 步执行器自己跑出的值，
   不做二次核对（审查 M5：要求 agent 报读数，它也只能照抄执行器给的值；而且网络类指标重跑会因为时间差对不上）。

### 3.4 人什么时候被找

| 情况 | 去哪 |
|---|---|
| 日常醒来，不管有没有进展 | 不找人，记账本 |
| 每周 | 周报：Brain Hub 上的一份 artifact，聊天里不出声 |
| 连续失败被熔断 | Needs You（BLOCKING，带 `brain=`）。attention_auto 的白名单 `unattended_scope` 要扩到岗位，否则它修不了（审查 M4） |
| 只有人能做的事：目标不再合适、脚本 hash 变了、要离开本机 | 一条 REVIEW。每个岗位同时最多一条 |
| 连续多次醒来，读数和未结事项都没动 | 写进周报「这个目标可能不合适」 |

### 3.5 状态、存储与调度（审查 H4、M4、L3）

- **暂停/恢复的状态放在调度器的 `state.json`**，不写回 `project-agent.yaml`：执行器不去改一个 agent 维护、受 git 跟踪的文件。
- **`project_agent_draft._merge` 像保留 `rules` 一样保留 `duties`**，并加测试。否则每次绑定仓库重新起草，岗位都会无声消失
  （`backend/core/project_agent_draft.py:582-650, 733-742`）。
- **调度器把 `duty:<brain>/<id>` 当成第三个来源。** 解析失败时跳过并给出提示，因为每 5 分钟一次的 Needs You collect 也会调用 `load_jobs`。
- **岗位的三段是它自己的脚本**，不引用会按自己 cron 重复运行的现有 job。
- **时间与运行环境**：调度按 UTC，每小时一轮，只在 macOS 后台和 Hive 上运行。Windows/Linux 桌面版不会唤醒岗位，岗位卡上要写明。

## 4. 第二期的进入条件

满足以下两条再开工，以免为一个用户造一层机制：

1. 第一期上线后，GitHub_Community 的三段链干净跑满 3 周；
2. 出现第二个需要「长期职责 + 目标」的 Brain。

满足后按 R1 走 pipeline（full）：跨调度器、执行器、Project Agent、attention_auto 和前端。

## 5. 和 Kiro Bot 的取舍

**取：**
- 目标在 Bot 写不到的地方（§3.2）
- 默认安静
- 每周报告
- 真正的暂停态（Kiro 没有）

**改进 Kiro 的弱点：**
- 确定性预检（Kiro 每次心跳都调模型）
- 心跳失败也熔断（Kiro 没有）

**不取：**
- 独立身份、独立记忆和 workspace
- 默认 `trust`
- 四条醒来路径
- 触发词路由
- 很长的固定排版报告提示

## 6. 验证计划

**第一期：** §2 的验收三条。

**第二期**，结束时必须成立：
1. 预检全不变时，没有模型调用，usage ledger 也没有新行；
2. 醒来的会话写不到 goals 记录、账本、`state.json`；
3. 脚本 hash 漂移时拒绝执行，且只产生一条 REVIEW；
4. 有 act 段时，想那一段的 prompt 里没有 MEMORY、USER、召回内容，Bash 白名单里没有 `cognition`；
5. 重新起草后 `duties` 还在；
6. 日常醒来不产生 Needs You；熔断只产生一条，带 `brain=`；
7. 从 `--run-now` 进程发起的醒来被拒绝。

## 7. 要 XG 定

1. 先做第一期，第二期按 §4 的条件再开工：同意吗？（我的建议是同意。）
2. 名字：中文「岗位」，英文 `duty`。
