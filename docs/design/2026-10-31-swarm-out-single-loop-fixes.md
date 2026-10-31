---
title: Swarm Out 单路畅通 —— 等待的回答链路、等待卡、命令面板，和单路验证发现的全部问题
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: r1 design-spec（无代码）
tags: [swarm-out, hive, ux, decision, bridge, records]
related:
  - docs/design/2026-10-31-swarm-out-hive-decisions.md（决定快照）
  - docs/design/2026-10-26-turn-cards-unified.md（chat 里等人的卡：ActFrame / ActOption / ActDone）
  - docs/design/2026-10-31-swarm-out-run-review.md（run 大卡片、命令面板、`derive` 下发的 actions）
  - docs/design/2026-10-31-hive-approvals-person-and-host.md（host 回答；本设计不实现它，但为它留好 `answered_by`）
  - Knowledge/Mockups/2026-10-31-swarm-out-wait-card-and-panel.html（mock：六个状态）
---

# Swarm Out 单路畅通

## 0. 为什么

2026-10-31 第一次做了真实的单路验证：派一个代码任务到 Hive（`so_8d2d6794450d`，Hive 上的 run 是 `run_8d2d6794`，pipeline bugfix），中间有一个 DC-1 决定，
最后 bundle 交回并落到本机 main（`c9ac8630c`）。链路跑通了，但这一路上暴露的问题，有的让人用不下去，有的让账记错。XG 的要求是先把它们全部解决、让单路畅通，
然后才开始 Swarm Run。

XG 对界面的三条反馈（原话）：
- 在 run overlay 里点 A，"那边发不出去"；
- Question card "做的太丑了，傻傻的分不清，也不知道是个选项卡，也不知道选了啥"，要和 chat 的卡统一；
- 右边的操作按钮、点击后的 confirm "挤在一起，button 不是 button，文本不是文本，没法区分"。

## 1. 问题清单（都对照过代码或记录）

| # | 问题 | 证据 | 原因 |
|---|---|---|---|
| P1 | 点 A 以后卡片不变，再点就报 "Invalid request" | 回答文件 `bridge/xg-swarm-remote-demo/answer/07372dc6…/dc-0d6ef….json` 写于 08:21:51Z，Hive 的 `decisions.json` 在 08:22:32Z 去掉了这个决定，run 按 A 继续：**第一次点击其实成功了** | ① Host 列决定时只读 `decisions.json`（`core/swarm_out.py` `_read_decisions`），不看 `answer/`，所以已经回答、Hive 还没取走的决定仍然显示成可以回答；第二次点击被拒（"this question was already answered"，400）。② 前端把 detail 是字符串的 400 一律显示成 "Invalid request"（`desktop/src/services/api.ts:60-72`），真实原因被藏掉 |
| P2 | 等待卡和 chat 的卡不是一回事 | Swarm Out 的 `AskCard`（`components/swarm-out/LiveDispatches.tsx:70-200`）是单独写的：选项是一块块文字、没有选中态、一点就直接发出、发出后没有"已送出"的样子；chat 用 `ActFrame` / `ActOption` / `ActDone`（`components/common/ActCard.tsx`、`AskUserQuestion.tsx`，设计 `2026-10-26-turn-cards-unified.md`） | 两处各写各的 |
| P3 | 卡片放在"交回的东西"tab 里面，右边却是一个指路的"Answer"大按钮 | `RunOverlay.tsx` 把 ask 交给 `DeliverableTab`；`swarm_out_settle._actions` 对 `needs_answer` 给出 `answer` 动作（`core/swarm_out_settle.py:297-298`），前端把它画成主按钮，点了只是切到那个 tab | 要做的决定被放在次要位置，指路的东西被画成了动作 |
| P4 | 命令面板按钮和文字分不清，确认流程挤在一起 | `RunOverlay.tsx:419-447`：每个命令是"名字 + 一行说明 + 快捷键"整块做成按钮；丢弃 / 修正重跑 / 手动落地的确认是一列整宽按钮，取消也是同样的整宽按钮（`:341-381`） | 没有区分"动作"和"说明"；确认没有统一的样子 |
| P5 | 经桥回答的问题被记成"没回答" | Hive 记录 `000001-turn.json` 第 43 行的 `ask_user_question` 块 `answers: null`；这一单的结果 `human: {unanswered: 1, answered_questions: 0}` | 后端保存问题块时不带答案，答案只在桌面前端的 MessageStore 里（`MessageStore.ts:905-915` 的注释）；Hive 没有前端，`swarm_out_results.summarize`（`:358-372`）就把它算成没回答。ToDo `5205bf49` |
| P6 | 检查格把跑过的检查显示成 `not_run` | 结果的 `checks` 里 smoke-import、各项 lint、security-scan、tsc、contract-lint、desktop-build 都是 `not_run`；run 自己的 REPORT（`.aidlc/deliverables/run_8d2d6794/REPORT.md` 的 TEST 段）写着都跑了、都通过 | 两处读的不是同一份记录（待实现时定位到行）。ToDo `9c10a116` |
| P7 | Hive 上 vitest 起不来 | REPORT："Node 18.20 here, jsdom needs Node >=20.19" | Hive 装的是 `nodejs20 npm`（`hive/setup.sh:46`、`backend/hive/user_data.py:26`），但默认的 `node` 仍是 18（AL2023 上 `nodejs20` 装的是 `node-20`，`npm` 包会带默认的 18；待在 Hive 上确认） |
| P8 | `test_swarm_out_hive_decisions.py` 在 Hive 上有 3 个失败（本机全过） | REPORT："--deselect the 3 test_swarm_out_hive_decisions" | 环境漏进了测试（待复现；嫌疑是 `get_app_data_dir` / 工作区路径读了 Hive 自己的环境） |
| P9 | 本机 main 的 BVT 起不来 | `scripts/run_bvt.sh` 遇到 manifest 列着但已删除的文件就退出；`982c32190`、`baeabf19f` 删了三个测试没改 manifest | **已修**（`14cc07670`、`d2f02ec89`）。还缺预防：删测试时没有任何东西提醒改 manifest |
| P10 | 结果里 `pipeline.run_id` 是空的 | 这一单的 `result.pipeline.run_id: null`，实际是 `run_8d2d6794` | `summarize` 从 `pipeline_summary` 块取 `runId`（`swarm_out_results.py:398`），这个块里没有；待查 |

## 2. 设计

### 2.1 一个等待从"等你"到"Hive 已收到"：Host 侧状态由事实推出

每个等待（问题、审批、决定）在 Host 上有四个状态，全部从桥上的文件推出来，不另存：

| 状态 | 由什么推出 | 卡片 | 这一单的状态 |
|---|---|---|---|
| 等你 | Hive 还列着它（`ask/` 未关 / `decisions.json` 里有），`answer/` 没有 | 可选可提交 | `waiting`，原因照旧（`question` / `decision` / `waiting_input`） |
| 已送出 | Hive 还列着它，`answer/` 已有 | 一行：选了什么，"等 Hive 接收"；不能再点 | `running`，原因 `answer_sent` |
| Hive 已收到 | Hive 不再列它（`closed/` 有、或从 `decisions.json` 里消失），且 `answer/` 有 | 一行：选了什么，"Hive 已收到"；保留到这一单结束 | 照 Hive 报的会话状态 |
| 送不出 | Hive 拒收（`closed/` 写 `answer_refused`，或决定条目 `refused: true`） | 卡片照旧可见，显示原因；选择保留 | `waiting`，原因 `refused` |

- `_open_asks` 今天对"已回答"的直接跳过，`_read_decisions` 不看 `answer/`。两处都改成读 `answer/`，把结果标成 `sent`；`run_state` 按上表给出状态。
- "Hive 已收到"那一行要知道选了什么：Host 写回答时本地记一份（`state/swarm-out/answers/<hive>/<session>/<key>.json`，写回答成功后写），卡片从这里读。
  不读 `answer/` 里的内容回显，因为 Hive 角色能读 `answer/`，而 Host 本地这份只有 Host 写。
- 这一套同时管问题、审批和决定，三种等待不再各有一套逻辑。

### 2.2 一张等待卡：Swarm Out 用 chat 的那张

- **组件：** Swarm Out 的问题、决定、审批都用 `ActFrame` / `ActOption` / `ActDone`，和 chat 的 AskUserQuestion、权限请求是同一套组件。
  `LiveDispatches.tsx` 里自己写的 `AskCard` 删掉，换成一个薄的 `SwarmOutWaitCard`：只负责把 Hive 的等待翻译成这几个组件的参数，以及调用 `swarmOutService.answer`。
  - 决定：标题"一个 pipeline run 需要你拍板"，meta 显示 `Decision · 2 分钟前`；正文是问题和"为什么它定不了"；每个选项一个 `ActOption`（单选圈）。
    选项框里第一行是 `<id> — <choice>`，第二行先放风险标签（高风险、不可撤回，小色块），再放后果；底部是"提交选择 ⌘↩"。
  - 问题：和 chat 的 AskUserQuestion 完全一样：选项、"其他（自定义回答）"、多选时用方框。
  - 审批：和 chat 的权限请求一样：命令原文、原因、"批准 / 拒绝"。命令太长、显示不全时，不出"批准"。
- **先选再提交**，和 chat 一样：选中的选项框高亮、圈变实心，可以改选；提交前不会发任何东西。
- **提交以后**（§2.1）：卡片立刻变成一行 `ActDone`。"已送出"时写"→ A — … · 等 Hive 接收（约 20 秒一轮）"；变成"Hive 已收到"时写"→ A — … · Hive 已收到 · 16:22"。
- **送不出**：卡片底部用红字写后端给的原因原文，选择保留，可以刷新或重选。
- **位置：** 大卡片里，等待卡放在左栏最上面（验收条下面、tab 上面），因为它是这一刻要做的决定；不再放在"交回的东西"tab 里。Swarm Out 列表页里不属于任何一单的等待照旧放在那里。

### 2.3 命令面板：按钮是按钮，说明是说明，确认是一个统一的样子

面板从上到下三个区，区和区之间的间距大于区内间距（design-judgment 第 6 条）：

1. **状态行：** "现在：<状态> · 多久"，状态用这一单的语气色。`answer_sent` 写"已回答，等 Hive 接收"。
2. **动作区：**
   - 按钮只放动作名和快捷键，一律是同一种按钮样式：主动作实底，其余描边。说明不放进按钮，放在这一组按钮下面，一行灰字。
   - `needs_answer` 的 `answer` 不是动作，是指路：不画成按钮，画成一块淡色提示"在左边的决定卡上选一个选项并提交，这一单就会继续"。快捷键把焦点移到卡片。后端的 actions 不变（`derive` 仍然下发它，前端按 key 决定画法）。
   - 次要动作放在"其它"下面，同样是描边按钮加一行说明。
3. **丢弃：** 放在最底下，单独隔开；红色描边按钮"丢弃…"（省略号表示还要确认），下面一行后果说明。
- **确认（丢弃、修正重跑、手动落地）用同一个 `ConfirmSheet`：** 在面板里原地展开，替换动作区。内容依次是：
  - 标题（危险操作用红色）、一段后果说明；
  - 需要文字时，一个输入框（标签在框上面）；
  - 最底一行两个并排的按钮：右边"确认动作名 ⌘↩"（危险操作红色实底），左边"取消 Esc"（描边）。
  - 出错时，错误原文显示在按钮行上面。
- **落地（land）** 的 ReturnPanel 也放进 `ConfirmSheet` 的正文里，底部按钮行同样是"完成 / 取消"。

### 2.4 错误原因不再被藏掉（全应用）

`desktop/src/services/api.ts`：4xx 的响应带字符串 `detail` 时，用它作为 message；只有既没有结构化错误、也没有 `detail` 时，才退回 "Invalid request" 这类通用文案。
这一处是全应用的，所有调用方都能看到后端写的原因。

### 2.5 问题的答案在后端落盘（P5）

`continue_with_answer` 送达答案时，后端把答案写进这个会话里对应的 `ask_user_question` 块（按 `toolUseId` 找到块），同时写 `answered_by`
（`person` / `host`，为 hive-approvals 设计预留）。这一改动对桌面和 Hive 一样：
- 桌面：MessageStore 的合并逻辑（`MessageStore.ts:905-915`）已经按 key 合并本地字段，后端有了 `answers` 以后，以后端为准。
- Hive：记录里的块带上答案，`summarize` 自然数对。
- `summarize` 的口径不变（`answers` 非空才算回答），只是输入对了。

### 2.6 检查格读 REPORT 用的那份记录（P6）

找到 `checks` 的状态从哪里来、REPORT 的 TEST 段从哪里来，让两处读同一份记录；以 REPORT 为准，因为它来自 run 自己的审计。实现时先写一个用这一单记录做的 fixture 测试（现在红），再改。

### 2.7 Hive 的 Node（P7）

Hive 的安装脚本在装完 `nodejs20` 之后，把默认的 `node` / `npm` / `npx` 指到 20（AL2023 用 `alternatives`，或把 `node-20` 的目录放到 PATH 最前）。装完要检查：
`node --version` 不低于前端 `package.json` 里 `engines` 写的版本，不够就让安装失败，别让 Hive 带着旧版本起来。已有的两台 Hive 跟下一次更新一起换。

### 2.8 测试不吃环境（P8）

先在一个带 Hive 环境变量的子进程里复现那 3 个失败（`SWARMAI_MODE=hive`，以及 Hive 的数据目录变量）。找到漏进来的环境以后，在测试 fixture 里把它清掉或固定住，
不在产品代码里为测试开后门。

### 2.9 删测试时提醒改 BVT manifest（P9 的预防）

在仓库自己的提交检查里（和 `lint_doc_frontmatter.py` 同一处）加一步 `bash scripts/run_bvt.sh --list`：manifest 里有不存在的文件就挡住提交，并说是哪个。
这是这个仓库自己的纪律，放在仓库里，不进产品的 gate（SOUL P7）。

### 2.10 结果里的 run id（P10）

查清 `pipeline_summary` 块里为什么没有 `runId`。修法是两选一：让写块的地方带上 run id；或者 `summarize` 从派发记录的 `project_agent.run_id` 补上。哪个对，取决于查出来的原因。

## 3. 不在本设计里

- host 回答 Hive 的等待（hive-approvals 设计的 P0–P2）。本设计只为它留好 `answered_by` 字段和 §2.1 的状态。
- Swarm Run。
- REPORT.md 随代码提交进仓库的做法（仓库里已有 205 个这样的文件，是现行约定）。

## 4. 验证计划（做之前定好）

| # | 不变量 | 怎么验证 |
|---|---|---|
| V1 | 一个等待只能被回答一次；回答后立刻显示"已送出"，不能再点；Hive 取走后显示"Hive 已收到" | 后端：`_open_asks` / `_read_decisions` / `run_state` 的单测覆盖 §2.1 的四个状态；前端：`SwarmOutWaitCard` 四个状态的 vitest |
| V2 | 后端的拒绝原因原样显示 | `api.ts` 单测：400 带字符串 detail → message 就是它 |
| V3 | Swarm Out 的等待卡和 chat 用同一套组件 | 结构测试：`SwarmOutWaitCard` 渲染出 `data-act-frame` / `ActOption`；`LiveDispatches.tsx` 里不再有自己的选项按钮 |
| V4 | 命令面板：按钮里只有动作名和快捷键；`answer` 不是按钮；确认都用 `ConfirmSheet` | vitest：每个 stage 的面板快照里按钮文本只含名字和快捷键；丢弃 / 修正 / 手动落地都渲染 `confirm-sheet` |
| V5 | 经 `continue_with_answer` 送达的答案落在块上；`summarize` 记为已回答 | 后端测试：驱动一次 continue_with_answer，读回块带 `answers` 和 `answered_by`；去掉写入这一步测试变红 |
| V6 | 检查格和 REPORT 一致 | 用这一单的记录做 fixture 的测试 |
| V7 | Hive 上 `node --version` ≥ engines；不够安装失败 | 安装脚本的单测（解析 engines、比较版本）；更新一台 Hive 后实测 |
| V8 | `test_swarm_out_hive_decisions.py` 在 Hive 环境变量下也全过 | 带 Hive 环境变量的子进程跑这个文件 |
| V9 | manifest 里有不存在的文件时提交被挡 | 在临时仓库里删一个列着的测试，提交检查失败并点名 |
| V10 | 结果带 run id | 用这一单的记录做 fixture |
| E2E | 单路再跑一次：派一单带决定的任务，点一次 A，看到"已送出"→"Hive 已收到"；跑完后结果里 `answered_questions = 1`、`unanswered = 0`，检查格和 REPORT 一致，`run_id` 有值 | 更新两台 Hive 后真跑 |

每一处修复都做 mutation 检查（去掉修复，对应测试变红）。审查：§2.1 和 §2.5 由 correctness 加 state-machine 审，§2.2–2.4 由 ux 审，§2.7 由 operational 审。

## 5. 顺序

1. 后端：§2.1 状态、§2.4 错误原文、§2.5 答案落盘、§2.6 检查格、§2.10 run id。
2. 前端：§2.2 等待卡（`SwarmOutWaitCard`）、§2.3 命令面板和 `ConfirmSheet`、§2.4 的 `api.ts`。
3. Hive 和仓库：§2.7 Node、§2.8 测试、§2.9 manifest 检查。
4. 重新打 Hive 包、更新两台 Hive、重启本机（重启要 XG 批准），然后跑 E2E。
