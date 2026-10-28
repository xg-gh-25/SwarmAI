---
title: Swarm Out 派发带上 Project Agent —— 任务带着它的大脑出门
created: 2026-10-28
updated: 2026-10-28
status: proposed (r2)
project: SwarmAI
extends:
  - docs/design/2026-10-28-swarm-out-run-results.md
  - Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-27-swarm-out-hive-hld.md §17.2（Context Pack）
  - docs/design/project-agent-delivery-os-hld.md §6.2–6.3
depends_on:
  - docs/design/2026-10-28-ddd-map-and-on-demand-knowledge.md（`core/ddd_map.render`，另一个会话在做）
---

# Swarm Out 派发带上 Project Agent

**r2（一次前提审查之后）：** 审查对着源码核实了 16 条论断，找到 7 个 HIGH，按原因归成五组，一次改完：
- **快照的放法换了（§4、§6）。** r1 的 A 方案假设"用到项目根的地方都走一个函数"，不成立：
  - `project_registry.get_projects_dir` 只有 4 个调用方，另外约 65 处、约 50 个文件自己拼 `Projects/` 路径。
  - 这个函数是进程级的，不带会话。pipeline 命令更是在没有会话的独立进程里跑。
  - run 目录写死在 `Projects/<p>/.artifacts/runs/` 下，安全钩子也只扫这棵树。

  r2 改成：把简报装进 Hive 自己的 `Projects/<brain>/`，替换其中受管的 DDD 文件。本机的那几条路在 Hive 上原样可用，不用改，也不会再读到一份过期的副本。
- **简报在 turn 开始之前就装好（§4）。** r1 把它挂在 `task_repos` 那一处，那是 turn 开始之后才执行的，第一个 turn 就会在没有简报的情况下跑完。r2 用一个单独的接口先装好，再派发。旧版 Hive 没有这个接口，本机就拒绝派发，不会在没有简报的情况下悄悄跑。
- **准入（§8、§9）：** 要求 `status: reviewed`，并且 `placements` 里有 `hive`，草稿不能出本机。写明 `placements` 和 `aim.json` 的 `distribution` 各管什么。落地方式以 Project Agent 的 `landing` 为准。
- **复用已有的（§3、§5）：**
  - `ddd_packager` 的路径改写和 `split_skills`。DDD 生命周期的 skill（`s_ddd-*`）不进简报。
  - `release.sh --with-live-ddd` 的"文档正在被写就不打包"检查。
  - `ddd_map` 需要能传项目目录，这一点要和那个会话对齐。
- **校验证据（§7）：** 只记"跑了 / 报错了"，不记"过了"。比较前先去掉命令里的环境变量前缀和注释，并且要求是在最后一次改动之后、在那个仓库里跑的。
- **§1 改了三处事实：**
  - 读 `project-agent.yaml` 的还有 `verify_ddd_complete.py`。
  - `release.sh --with-live-ddd` 本来就会打一份 DDD 快照。
  - 唯一有定义的 itsdangerous 是 `placements: [chat-tab]`、`status: draft`，现在派不出去。

## 0. 两个问题的回答

**"跑完后统计这些信息，怎么统计，要不要给任务指引？"**
- 不让任务自己统计，这次也没有让它统计。
- 结局、commit、改了的文件和行数、工具调用和失败、问了几次人、用时和花费，全部由 Hive 后端在 turn 结束时从它自己的记录里算出来（`core/turn_outcome.py`、`core/task_repos.py`），本机再归纳（`core/swarm_out_results.py`）。
- 模型说"我跑了测试、都过了"不是证据，记录才是。
- 任务真正缺的指引是**怎么做、做到什么程度、怎么交付**：跑哪些校验、改动怎么落地、文档放哪、什么算做完。这些本来就该来自这个项目的 Project Agent，可现在派发根本没用它。

**"你用到 Project Agent 了吗？"** 没有。这是本设计要补的缺口。

## 1. 现状（2026-10-28 读代码，经前提审查核实）

| 事实 | 位置 |
|---|---|
| 派发就是一段文字：无人值守的前言加任务原文，可选一份仓库列表 | `core/swarm_out.py:69`（`UNATTENDED_PREAMBLE`）、`:184`（`task_message`）、`:191`（`dispatch`） |
| 任务写什么全靠本机 agent 按 skill 手写，skill 只要求"自包含" | `backend/skills/s_swarm-out/SKILL.md` 第 2 步 |
| Project Agent 定义是 DDD ⑤ 的 `project-agent.yaml`，内容有概述、默认 profile 和选择规则、审查重点、校验命令、允许在哪跑、怎么落地。`validate()` 接受 `draft`，也接受 `reviewed` | `core/project_agent.py`（只读并校验，`:57-62`） |
| 读它的只有 Brain Hub 和 DDD 完整性检查。pipeline、聊天、派发都不读 | `routers/ddd_brain.py`、`skills/s_project-manager/scripts/verify_ddd_complete.py:408` |
| 11 个 Brain 里只有 `itsdangerous` 有这个定义，它是 `placements: [chat-tab]`、`status: draft` | `Projects/itsdangerous/project-agent.yaml` |
| 仓库和交付约定写在 `bindings.yaml`：clone 地址、分支、交付方式。其中 `worktree:` 是本机的绝对路径 | 各 Brain 根目录 |
| 一个 DDD 能不能离开本机、给谁，由 `aim.json` 的 `distribution` 决定，没写就不允许。`ddd_packager` 会把本机路径改写成可移植的路径，`split_skills` 会把 skill 分成几类 | `core/ddd_distribution_policy.py:81`、`core/ddd_packager.py:103,209,678` |
| 发布包的 seed 只带五个公开 context。`release.sh --with-live-ddd` 会额外把本机的 SwarmAI DDD 打进 seed，打包前检查内容不少于下限、文档不在写入中。它用的是排除列表（rsync `--exclude`） | `hive/release.sh:14-23,166-231` |
| 实测：explorer 上的 pipeline run 写在 `Projects/SwarmAI/.artifacts/` 下，说明它有一份用 `--with-live-ddd` 打进去的 SwarmAI DDD，和本机不同步（HLD §5.1 说的"第二个大脑"） | 本轮 E2E，`run_3af72d97` |
| 本机会话拿 DDD 的几条路，都按 `Projects/<name>` 找项目：<br>- 按活动项目注入（`session_router.py:1771`），检测 fail-closed，候选项目来自本机的 `Projects/`（`recall_multi.py:1353,1401`）<br>- 进仓库时注入 AGENTS.md 和 DDD 路径（`repo_guidance.py:248`）<br>- pipeline 按用途召回、写 `knowledge.md`（`run_protocol/run_knowledge.py`），run 目录在 `Projects/<p>/.artifacts/runs/`（`run_protocol/engine.py:240,425,478`），安全钩子只扫这棵树（`security_hooks.py:3133,3407`） | 见左 |
| 新会话的 `task_repos` 要等到第一条流式消息带回会话 id 之后才写入，因为它到 turn 结束才被读 | `routers/chat.py:374-386,466-471`、`session_router.py:4125` |
| 旧版 Hive 收到不认识的请求字段时直接丢掉，不报错 | `schemas/message.py:151`（没有设置禁止多余字段） |
| DDD 地图（目录、大小、行号、怎么按需读）正在另一个会话里设计和实现，`core/ddd_map.py` 还不存在，它的 `render` 按项目名在 workspace 下找项目 | `2026-10-28-ddd-map-and-on-demand-knowledge.md` §2.1 |
| 远期设计里已经写了 Context Pack：DDD 快照、五个公开 context、解析后的 Project Agent、任务原文和验收、改写过的 bindings、仓库地址和交付分支。不带代码，按内容寻址，准入是确定性检查 | HLD §17.2（无代码） |

结论：派到 Hive 的任务不知道自己属于哪个 Brain，也拿不到这个 Brain 的身份、知识、规则、能力和交付约定。
Hive 上的 agent 要么在一份过期的 DDD 上猜，要么什么都没有。

## 2. 目标与不做

**目标：**
- 派发时指定一个 Brain（`--brain <name>`）。本机由它的 Project Agent 把这个任务需要的全部上下文编成一份**任务简报**（brief，即 HLD Context Pack 的第一版），先装到 Hive 上，再派任务。
- Hive 上的 agent 一开始就知道：这是哪个项目，它的知识在哪、怎么读，有哪些规则和能力，这个任务要做到什么，怎么交付。
- 结果记录带上 Brain 和简报版本，面板可以按 Brain 看 run（XG 10-27 提的"按 Brain / Project Agent 维度"）。

**不做（留给 HLD §17）：** Hive 的 worker 角色和认知写入关闭、封存记录和经验导入、Hive 自己的 Conductor、中心协调。本设计只解决"任务带着上下文出门"，不解决"经验带回来"。§6 写明这带来的后果。

## 3. 简报里有什么

本机编简报，**只读本机磁盘、确定性生成**，不调模型。每一部分都注明来源，Hive 上的 agent 能看到每条话出自哪个文件。

| 部分 | 内容 | 来源 |
|---|---|---|
| ① 身份 | Brain 名、一句话概述、`AGENTS.md`（门牌）或 `SYSTEM_PROMPT.md` | Brain 根目录，`project-agent.yaml.summary` |
| ② 知识 | DDD 的文档（`2-understanding/` 下的 canonical 文档和 `knowledge/`），加 **DDD 地图**：每份文档的路径、大小、`##` 的行号，以及"先 grep 定位行号再 Read"的读法 | `core/ddd_map.render`（要能传项目目录，见 §10） |
| ③ 规则 | 项目自己的门槛，加审查重点 | `3-gates/`，`project-agent.yaml.review_focus` |
| ④ 能力 | 这个 Brain 的**领域** skill（只当文档，见问题 4）。DDD 生命周期的 skill（`s_ddd-*`，用来写 DDD 的）不进 | `4-capabilities/`，`ddd_packager.split_skills` |
| ⑤ 走法和交付 | 选 profile 的规则（或本机已选好的 profile），要跑的校验命令，怎么落地。落地以 `project-agent.yaml.landing` 为准，`bindings.yaml.delivery_contract` 只提供分支和远端类型 | `project-agent.yaml`，`bindings.yaml` |
| ⑥ 仓库 | `bindings.yaml` 里 code-repo 资产的 clone 地址和分支，自动变成派发的 `repos`（不再靠人手写 `--repo`）。装到 Hive 的 `bindings.yaml` 里，`worktree` 改写成 Hive 上的目录（`Hive/repos/<host>/<owner>/<name>`，和 `task_repos.repo_dir` 是同一个函数） | `bindings.yaml`，`ddd_packager.rewrite_host_paths` 的做法 |
| ⑦ 任务要求 | 任务原文、验收标准（"做到什么算完"）、交付物放哪 | 本机 agent 按 skill 写，派发时必须带 `--acceptance` |

**收文件用白名单（目录加后缀）：** `AGENTS.md`、`SYSTEM_PROMPT.md`、`CONTEXT.md`、`REFRESHER.md`、`project-agent.yaml`、`bindings.yaml`，`2-understanding/**/*.md`，`3-gates/**`，`4-capabilities/` 里领域 skill 的文本。私有 context（MEMORY、USER、EVOLUTION、STEERING、TOOLS、KNOWLEDGE）、`.artifacts/`、`code_intel.db*`、`assets/` 下的二进制、`*-archive*`、`*.md.lock` 都不在白名单里，所以都不会进去。`release.sh --with-live-ddd` 用的是排除列表，`core/brief.py` 不照搬它，只照搬"文档正在被写就不打包"的检查：用 `flock -n` 试锁，锁不上就拒绝，并说明原因。

## 4. 怎么送、放哪、什么时候装

**送：**
- 本机把简报打成 tar，算 sha256，上传到 `bridge/<hive>/brief/<sha256>.tar.gz`。
- 这个前缀只有本机写，只有那台 Hive 读，做法照 bridge 已有的 `answer/`：Hive 角色的 IAM 按 `${aws:PrincipalTag/HiveName}` 限定前缀（`hive/provisioner.py:99`），加一条 GetObject 即可。策略在每次更新 Hive 时都会重写（`:142-155`），所以更新一次 Hive 就生效。
- 内容相同，digest 就相同，不重复上传。

**装（turn 开始之前）：**
- 本机先调 Hive 的新接口 `POST /api/swarm-out/brief {brain, digest}`。Hive 下载、核对 sha256、装进 `Projects/<brain>/`（见下），返回 `{installed, digest}`。
- 成功之后，本机才发派发请求，请求里带 `brief: {brain, digest}`。Hive 在新建会话时把它写进 `metadata.brief`，并核对当前装好的 digest 和请求里的一致，不一致就拒绝。
- 这个接口有自己的超时，和派发时等会话 id 的 60 秒分开。
- 旧版 Hive 没有这个接口，返回 404，本机就拒绝这次派发："这台 Hive 的版本不能接收简报，请先更新"。

**放哪：装进 Hive 的 `Projects/<brain>/`。**
- 只替换白名单里的文件：每个文件先写临时文件再 rename。简报里没有、但落在白名单范围内的旧文件删掉。`.artifacts/`（Hive 上的 run 记录）和白名单以外的东西不动。
- 写一个 `Projects/<brain>/.brief.json`，内容是 `{digest, brain, installed_at}`。
- 这样本机的那几条路在 Hive 上原样可用：检测、注入、召回、pipeline 的 project 和 run 目录、安全钩子、仓库绑定。它们找到的就是刚装好的这一份，不会再读到过期的副本（审查 HIGH 5 的那种静默回退）。

**并发：** 同一台 Hive 上，如果同一个 Brain 有一个 run 还在跑，而它用的是另一个 digest，就拒绝这次安装："<brain> 在这台 Hive 上有一个用旧简报的 run 还没结束"。digest 相同就直接用，不重装。在跑的 run 只有在自己的会话里读不到简报才会受影响，所以这个检查只需要看会话的 `metadata.brief`。

## 5. 任务说明怎么写（Hive 上的 agent 读到什么）

前言之后、任务原文之前，加一段由简报生成的**项目段**，例如：

```
Project: itsdangerous — A small, widely used signing library; ...
This project's brain is installed at Projects/itsdangerous/ (brief <digest>, from the owner's machine on <date>; read-only for you):
- Identity and conventions: Projects/itsdangerous/AGENTS.md
- Knowledge map (every document, its size and section line numbers): Projects/itsdangerous/MAP.md — grep a keyword, then Read at that line
- Gates: Projects/itsdangerous/3-gates/ · review focus: security (constant-time checks) · backward compatibility · time handling
- Capabilities: (domain skills, one line each)
How this task is run: profile bugfix (rule: "..."); before you finish, run each validation in the repository and keep its output:
  tests: uv run --locked tox run -e py3.13 · typing: uv run --locked tox run -e typing · style: uv run --locked tox run -e style
How it is delivered: commit on a branch from main; do not push (landing: human, via github-pr)
Done means: <acceptance>
```

- 规则只写一次，项目段只从简报生成：前言是通用的，项目段同一个 Brain 每次一样，任务每次不同。
- 本机 agent 不再手写项目背景。skill 第 2 步改成"指定 `--brain` 和 `--acceptance`，任务正文只写这次要做的事"。
- 校验命令在项目段里去掉了只对本机有效的部分：`project-agent.yaml` 里的 `UV_NO_CONFIG=1` 和行尾注释，是本机 uv 镜像的变通写法，不该发到 Hive 上。去掉的规则在 §7 里，和比对用的是同一个函数。

## 6. 这样装的后果

- **Hive 自己对这个 Brain 的认知写入会被下一次安装覆盖。** Hive 现在没有 worker 模式，它的 cultivation 和 REFLECT 会往 `Projects/<brain>/` 里写（HLD §5.1）。这些写入现在本来就回不到本机，覆盖之后连 Hive 上也没有了。等 HLD §17.4 的导入做出来，它们会作为记录交回本机。在那之前，这是有意接受的丢失：本机是唯一的大脑，Hive 上那份只是缓存。
- **Hive 上的 agent 能改装好的文件**（项目段写了"只读"，但没有用权限强制）。下一次安装会还原它们，`.brief.json` 的 digest 也能看出装的是哪一版。要不要在 Hive 上把这些文件设成只读权限，是问题 5。

## 7. 统计和证据：在已有的记录上加什么

已有的记录（run-results 设计）：结局、按仓库的 commit 和行数、交付物、工具调用、问人、花费、pipeline 五维。这次加三样，都是记录，不是模型自报：

1. **Brain 和简报版本：** `turn_outcome` 加 `brief: {brain, digest}`，来自会话的 `metadata.brief`。本机的结果带上它，面板按 Brain 分组。`/runs` 里的 Brain 名用本机派发记录里的那个，不用 Hive 写的。
2. **profile：** 简报定了 profile 的，结果里记下"派发时定的 profile"。pipeline run 另有它实际用的 profile，两者不一致时标出来。
3. **校验跑了没有：** 简报列出了校验命令。turn 结束时，把这个 turn 的 Bash 调用和这些命令比对，每条校验记一行 `{id, ran, errored}`。
   - **比对之前两边都规范化：** 去掉开头的 `VAR=value` 赋值和行尾的 `# 注释`，再去掉 `cd <dir> &&` 前缀。去掉这些之后要求逐字相同。
   - **只算有效的那一次：** 命令的工作目录（或 `cd` 的目标）要在那个仓库里，而且要在这个 turn 最后一次 Write/Edit 之后跑。
   - **只记跑了没有、报没报错：** 工具结果只有一个报错标志，没有退出码。命令后面接了 `| tail` 或 `|| true` 时，这个标志也不可信。所以记成"跑了 / 报错了 / 没跑"，从不记"过了"。
   - **新的输入：** `OutcomeRecorder` 现在只看 result、error 和暂停事件，比对需要它也看工具调用和工具结果事件。
   - **后端不执行这些命令：** 执行仓库的命令，就是上一轮刚删掉的那一类风险（仓库内容决定执行什么）。agent 在审批之内跑，后端只读它跑过的记录。
   - **面板上的效果：** 没跑的写"没跑"，于是能看出"native coding 改了代码，但没跑测试"。
   - pipeline run 有自己的校验证据（它的 validate 阶段），这时以 pipeline 的为准，不重复比对。动手前先核实 validate 阶段记了什么。

## 8. 准入（本机派发前，确定性检查）

拒绝时说明是哪一条，不悄悄换地方跑：

- Brain 存在，有 `project-agent.yaml`，`project_agent.validate` 没有问题，`status: reviewed`，`placements` 里有 `hive`。
- 简报能打出来：白名单里的文档都不在写入中，大小不超过上限。上限拟定 20 MB。实测文本部分 SwarmAI 约 5 MB、CMHK_SalesIntel 约 1 MB、SecDLC 约 0.75 MB、itsdangerous 56 KB。
- `bindings.yaml` 里要用的每个仓库都有 Hive 拉得到的远端，`task_repos.parse` 通过。
- 带了 `--acceptance`。
- 目标 Hive 能接收简报（§4 的接口存在），而且没有同一个 Brain 用别的简报在跑的 run。

**`placements` 和 `distribution` 各管什么：**
- `aim.json` 的 `distribution` 管这个 DDD 能不能打包给**别人**（AIM 包、插件）。
- Hive 在用户自己的账户里，归同一个人所有，不算分发，所以不看 `distribution`。
- 能不能派到 Hive，只看 Project Agent：人审过（`reviewed`），并且写了 `hive`。

现在 11 个 Brain 里能派的是 0 个：itsdangerous 也要先由人审一遍，并加上 `hive`。补定义的路径已经有了：s_project-manager 生成，人审（`core/project_agent.py` 模块说明）。这是问题 3。

## 9. 安全

- 简报是用户自己的 DDD，送到用户自己账户里的 Hive，不经过第三方。私有 context 不在白名单里，永远进不去。
- DDD 本身可能有敏感内容，比如客户名、内部链接。能不能派到 Hive，由人在 Project Agent 里写 `hive`、并把它审成 `reviewed` 来决定。没审过的草稿出不了本机。
- 简报前缀只有本机能写。Hive 核对 sha256 之后才装，digest 来自本机的请求。
- Hive 写回来的东西照旧只当数据（run-results §4.7 不变）。本机 agent 读 `/runs` 时看到的 Brain 名，来自本机的派发记录。

## 10. 分步落地

1. **和 ddd_map 对齐接口：** `render` 要能接收一个项目目录（不只是项目名），返回相对这个目录的路径，地图写成 `MAP.md` 放进简报。在它落地之前，第 2 步可以先只放文档，不放地图。
2. **简报编译器 `core/brief.py`（纯本机）：** 读 Brain，按白名单收文件，做写入中检查，改写 bindings，生成项目段，打成 tar 并算 digest。单元测试覆盖白名单（私有文件、二进制、`.artifacts/` 不在包里）、digest 稳定、每一部分的来源。
3. **派发：** 加 `--brain`、`--acceptance`。做准入。上传。调用安装接口。`repos` 从 bindings 来。改写 skill。
4. **Hive：** 加安装接口（下载、核对、按文件替换、`.brief.json`、并发检查），新会话写 `metadata.brief` 并核对 digest。IAM 加 `brief/*` 的读权限。
5. **记录：** `turn_outcome.brief`、profile、校验比对。本机结果和面板按 Brain 分组。
6. **真机验证：** 先把 itsdangerous 审成 `reviewed` 并加上 `hive`，再派一个 bugfix。要看到：
   - Hive 上的 agent 读了 `AGENTS.md` 和 `MAP.md`（transcript 里有 Read）。
   - 它按 profile 跑，跑了三条校验，按 landing 只提交不推。
   - 面板上这个 run 归在 itsdangerous 下，三条校验都显示"跑了"。

## 11. 验证计划

| 写的一方 | 读的一方 | 最终要成立的 |
|---|---|---|
| `core/brief.py` | Hive 的安装、项目段 | 私有 context、二进制、`.artifacts/`、lock 文件不在包里。同一个 Brain 内容相同时 digest 相同。文档在写入中时拒绝打包。bindings 里没有本机路径 |
| 派发和准入 | Hive 的安装接口、`routers/chat.py` | 草稿或没有 `hive` 的 Brain 被拒。旧 Hive（404）被拒。digest 不符被拒。同一个 Brain 用别的 digest 在跑时，安装被拒。没有简报不会跑 |
| 安装 | 检测、注入、召回、pipeline、钩子、绑定（不改它们，只验证它们读到新的这份） | 白名单内的文件被替换或删除，`.artifacts/` 没有变。装完之后，过期副本里的内容找不到了 |
| 校验比对 | 本机结果、面板 | 规范化后相同、在仓库里、在最后一次编辑之后，记"跑了"。报错的记"报错了"。在别的目录跑、在编辑之前跑，或命令被改过一个字符，都记"没跑"。从不出现"过了" |

审查：实现完成后请 security（简报内容、S3 前缀、安装接口）和 correctness（安装和比对）两位专家审。设计只审这一轮，之后靠测试验证。

## 12. 要你定的

1. **方向：** 简报装进 Hive 自己的 `Projects/<brain>/`（r2），代价是 Hive 自己对这个 Brain 的认知写入会被下一次安装覆盖（§6）。r1 的独立目录方案因为要改约 65 处路径，已经放弃。我建议按 r2 做。
2. **能不能派到 Hive：** 只看 Project Agent，即人审成 `reviewed` 并写了 `hive`。不另设名单，也不看 `distribution`。我建议这样，规则只有一处。
3. **没有定义的 Brain：** 先用 s_project-manager 给常用的几个（SwarmAI、SecDLC、CMHK_SalesIntel）生成草稿给你审，itsdangerous 也请你审一遍。派发时不提供"只带 DDD、不带走法"的降级。我建议这样：没有定义，就不知道该怎么交付。
4. **领域 skill：** v1 只把 SKILL.md 当文档放进简报（agent 读了照做），还是在 Hive 上注册成真的 skill。我建议 v1 只当文档。
5. **Hive 上的简报文件要不要设成只读权限：** 设了以后，Hive 自己的 cultivation 写这个 Brain 会失败并留下 ERROR，这样能看出第二个大脑在哪里还想写。不设的话，写入会被下一次安装悄悄覆盖。我建议设只读，让问题看得见。
