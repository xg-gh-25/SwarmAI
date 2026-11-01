---
title: Project Agent 由 agent 自己确认，人只管"放宽给自己的权力"那几项
created: 2026-11-01
updated: 2026-11-01
project: SwarmAI
status: r0 design（无代码）
tags: [project-agent, brain-hub, autonomy, hive, dispatch]
related:
  - docs/design/2026-10-28-project-agent-flow.md（定义、revision、来源检查、D4"默认是草稿"；本设计改它的 §5 审核卡和 §7 ③ 准入）
  - docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md（问题卡由 Host 生成、daemon 转交人的选择；§4 照这个做法）
  - docs/design/2026-10-31-swarm-run.md（lead run 常事自己解决；本设计是同一原则落在 Project Agent 上）
---

# Project Agent 由 agent 自己确认

## 0. 为什么

XG 2026-11-01："Project Agent 你应该自己接管，不是有改动就让人去确认。任何时候拿最新的；有问题直接建议 update、revise；人没问题你就直接 confirm、finalize，
而不是等人。"

今天的做法（代码，2026-11-01 读的）：

| 事实 | 位置 |
|---|---|
| 派到 Hive 的准入：定义是草稿，或确认后又变了（`changed`），就拒绝派发，要人去 Brain Hub 确认 | `core/task_spec.py:312-317` |
| `changed` 的判定：`status: reviewed`，但当前 revision 没有来源检查记录 | `core/project_agent.py:649` `confirmation` |
| revision 是定义文件、各 mode 用到的 skill 目录、整个 `3-gates/` 的 sha256：改一个 skill、改一个 gate 脚本，都会变成 `changed` | `core/project_agent.py:444` `revision` |
| 确认只能从 App 表单发（要 `X-Swarm-User-Action` 头），`reviewed_by` 记本机登录名；来源检查只在确认时写 | `routers/ddd_brain.py:1332` `_agent_write`；`core/project_agent_review.py:301` `confirm` |
| 新 Brain 的草稿停在 Brain Hub 审核卡上，等人点确认 | `project-agent-flow.md` §5 |

结果：agent 平常改一个 skill、补一条 CI 命令，SwarmAI 的 Hive 派单就停下来等人去表单上点一次确认。人在那里能看的，大部分本来就是 agent 的事
（命令从 CI 原样抄、skill 列表由代码扫、mode 的 `when`）。真正需要人的只有一类：**给 agent 的权力有没有放宽**。

## 1. 人确认的到底是什么

`project-agent-flow` D4 说"默认是草稿"，理由是：发成 reviewed，就等于产品替每个用户批了**保留动作**。也就是说，人确认的核心是"哪些动作留给人"。
这一条要守：agent 不能替自己放宽权力（SOUL Safety"不扩大自己的权限"）。其余字段决定的是"活怎么干"，属于 agent 自己的判断。

所以把定义分成两部分：

| 部分 | 字段 | 谁能定 |
|---|---|---|
| **权力部分** | 共享和每个 mode 的 `reserved_for_human`；共享和每个 mode 的 `gates.enforce`；每个 mode 的 `delivery`、`pr_remote`（`branch-pr` 要往 GitHub 推，是出机器） | 收紧：agent 可以；放宽：只有人 |
| **其余** | `identity`、`intake`、`modes` 的 `id` / `when` / `repos` / `setup` / `validations` / `skills`、`placements`、`default_mode`、skill 目录的内容、gate 脚本的内容 | agent |

gate 脚本的**内容**算 agent 的：它是 Brain 自己的检查代码，agent 在对话里本来就能改它，改的时候走正常的测试和审查；能拿掉一个 gate 的是
`gates.enforce` 列表，那一项在权力部分。

**底线：** 人最近一次确认时的权力部分。从没被人确认过的 Brain，底线是产品默认：`RESERVED_ACTIONS` 四项在共享层全在（`core/project_agent.py:63`，
生成器默认也是四项都带），每个 mode `delivery: bundle`，`gates.enforce` 为空（从包导入的 Brain 默认一个都不勾，`project-agent-flow` §5）。

## 2. 规则

agent 确认一个 revision，要同时满足（全是确定性检查）：

1. `validate(doc, project_dir)` 没有问题；
2. 来源检查通过（`check_sources`，当场重跑）；
3. 权力部分不比底线松：每一层的 `reserved_for_human` 包含底线的那一层；`gates.enforce` 包含底线的；没有哪个 mode 从 `bundle` 变成 `branch-pr`，
   `pr_remote` 没变；底线里没有的新 mode，按共享部分的底线比。

满足 → agent 确认：写 `status: reviewed`、`confirmed_by: agent`、会话 id、一句原因、和上一版比改了哪些字段；来源检查记下这个 revision。**底线不变**。

不满足：
- 第 1、2 条不过：是 agent 自己的定义，agent 自己修（补命令来源、删不存在的 skill），修完再走一遍；修不了的（比如仓库里根本没有这条命令）在对话里告诉人，
  说清楚是哪一条。
- 第 3 条不过：**只问人放宽的那几项**（§4）。人同意 → 以人的名义确认，底线更新成这一版的权力部分。人不同意 → agent 把那几项改回去，再按上面自己确认。

人随时可以在 Brain Hub 表单上改、收紧、确认，和今天一样；人的确认也会更新底线。

## 3. 什么时候做：任何时候拿最新的

- **派单前：** `swarm_out spec` 先对这个 Brain 做一次"agent 确认"，再算 revision（确认会改写定义文件，所以必须在算 revision 之前）。过了，准入就看到 `reviewed`，
  照常派；没过，`spec` 的拒绝理由列出是哪一条、哪几项。准入（`task_spec.py:312`）本身不变：它仍然只认 `reviewed` 加当前 revision 的来源检查。
- **建完 Brain：** New Brain 流程最后，agent 对草稿做一次确认，在对话里一行报告："Project Agent 已确认，留给你的动作是 push、开 PR、发布、对外发消息"。
  审核卡不再挡在中间，人想看就点开。
- **agent 改了定义或它用的 skill 之后：** 改完顺手确认，命令 `artifact_cli project-agent-confirm --project <name> --why "<一句话>"`。

全部在本机进程里调同一个函数（`project_agent_review.agent_confirm`），不开 HTTP 路由；表单的确认路由照旧只给 App。

## 4. 放宽时问人：在对话里

复用 agent-owns-its-runs §2.5 的问题卡做法：

- `artifact_cli project-agent-card --project <name>` 生成一张问题卡：标题带固定标记 `[Project Agent · <name> · <revision 前 8 位>]`，正文逐项列出放宽了什么
  （"code-change 不再把 push 留给你"、"release 改成 branch-pr，会往 GitHub 推"），选项"同意这几项" / "不同意，改回去"。
- agent 原样调 AskUserQuestion。人选了以后，`ask_question_gate` 认出这张卡（标记、卡是 Host 生成的、在生成它的会话里、没被改过、revision 还是那一版、
  是人的回答不是自动的），由 daemon 以人的名义调 `confirm`，`reviewed_by` 记本机登录名，底线更新。
- agent 拿不到人的选择本身，只看到结果（已确认 / 没转交和原因）。卡被改过、会话不对、revision 已经变了、自由文字，都不确认。
- 会话里 AskUserQuestion 不可用时，告诉人去 Brain Hub 表单确认这一版（表单上标出放宽的几项）。

## 5. 人看到什么

- Brain Hub：Project Agent 一行显示"已确认 · agent · 时间"或"已确认 · 你 · 时间"；"你上次确认以来的变化"可以展开，列出 agent 确认过的每一次和原因。
  `changed` 只在 agent 也没能确认时出现（放宽了、或有问题没修好），并写明在等什么。
- 对话里：agent 确认时不另说（除非是建完 Brain 那一次）；放宽要问人时用问题卡；有修不了的问题时说一句。

## 6. 不变的

- 准入、Hive 上的检查、revision 的算法、来源检查的格式都不变。Hive 照旧只认 `reviewed` 加来源检查。
- 产品发的默认定义照旧是草稿（D4）：本机 agent 确认它时，底线是产品默认，保留动作一个都不会少。
- 威胁模型和 `project-agent-flow` §7 ④ 一样：本机 agent 能直接改工作区里的文件，这里防的是"正常路径上 agent 替自己放宽权力"，由确定性比较决定；
  不是防一个蓄意改文件的本机 agent。底线记在 `.project-agent-check.json` 旁边的 `floor` 字段，只有人的确认会改它。

## 7. 改哪些

| 文件 | 改动 |
|---|---|
| `core/project_agent.py` | `authority(doc)` 取权力部分；`looser_than(authority, floor)` 返回放宽的项；`floor(project_dir)` 读底线（没有就用产品默认） |
| `core/project_agent_review.py` | `agent_confirm(project_dir, session, why)`：§2 的三条，过了写确认，不改底线；`confirm`（人的）同时写底线 |
| `core/task_spec.py` 的 spec 入口 | 算 revision 前调 `agent_confirm`；拒绝理由带上它的结果 |
| `scripts/artifact_cli.py` | `project-agent-confirm`、`project-agent-card` |
| `core/project_agent_card.py`（新） | 生成卡、记录、转交；`security_hooks` 的 ask_question_gate 加一个分支，和 Hive 卡、pipeline 卡互斥 |
| `routers/ddd_brain.py`、`desktop` Brain Hub / 表单 | 显示确认人、变化记录；表单标出放宽项 |
| `skills/s_swarm-out/SKILL.md`、New Brain 流程 | 去掉"让人去 Brain Hub 确认"；改成 agent 确认、放宽时用卡 |
| `project-agent-flow.md` | §5 审核卡、§7 ③ 准入、D4 加一句指向本设计 |

## 8. 验证计划

不变式（最后一次跑的测试集，开工前定）：

| # | 必须成立 |
|---|---|
| V1 | agent 确认从不改底线；人的确认（表单或卡）才改 |
| V2 | 每一种放宽都让 agent 确认失败并列出那一项：共享层删一个保留动作、某个 mode 删一个、删一个 `gates.enforce`、`bundle`→`branch-pr`、改 `pr_remote`、新 mode 比共享底线松 |
| V3 | 只改了 skill 目录或 gate 脚本内容（`changed`）：`spec` 自动确认，准入通过，revision 是确认后的那一版 |
| V4 | `validate` 有问题或来源检查不过：不确认，理由写明 |
| V5 | 问题卡：人选"同意"→ 以人的名义确认、底线更新；自动回答、别的会话、卡被改、revision 变了、自由文字 → 都不确认 |
| V6 | 表单确认路由照旧只接 App 的请求 |
| V7 | 从没确认过的草稿：底线是产品默认，agent 能确认一份保留四个动作的草稿，不能确认少一个的 |

每条修复配 mutation check。审查：`security`（权力部分是否漏字段、卡的转交能否被伪造）和 `correctness`（确认改写文件与 revision 的先后、并发下的锁），一轮，
最多两轮。

## 9. 待 XG 定

- 权力部分是不是就这三样（保留动作、`gates.enforce`、`delivery`/`pr_remote`）。我的判断是 `placements` 加 `hive` 不算：派到 Hive 本来就是 agent 能做的，
  保留动作在 Hive 上照样生效。
- 建完 Brain 那一次，agent 确认后要不要还在对话里说一句（我建议说，一行）。
