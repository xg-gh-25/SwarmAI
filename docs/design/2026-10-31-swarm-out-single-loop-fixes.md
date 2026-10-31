---
title: Swarm Out 单路畅通 —— 等待的回答链路、等待卡、命令面板，和单路验证发现的全部问题
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: r2 design-spec；实现见 pipeline run_8437ff7e（§7 记实现与设计的出入）
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
| P1 | 点 A 以后卡片不变，再点就报 "Invalid request" | 回答文件 `bridge/xg-swarm-remote-demo/answer/07372dc6…/dc-0d6ef….json` 写于 08:21:51Z，Hive 的 `decisions.json` 在 08:22:32Z 去掉了这个决定，run 按 A 继续：**第一次点击其实成功了** | ① Host 列决定时只读 `decisions.json`（`core/swarm_out.py` `_read_decisions`），不看 `answer/`，所以已经回答、Hive 还没取走的决定仍然显示成可以回答；第二次点击被拒（"this question was already answered"，400）。② 前端把 detail 是字符串的 400 一律显示成 "Invalid request"（`desktop/src/services/api.ts:60-72`），真实原因被藏掉。③ 问题（ask）这边也一样：回答以后卡片直接消失，看不到选了什么 |
| P2 | 等待卡和 chat 的卡不是一回事 | Swarm Out 的 `AskCard`（`components/swarm-out/LiveDispatches.tsx:70-200`）是单独写的：选项是一块块文字、没有选中态、一点就直接发出、发出后没有"已送出"的样子；chat 用 `ActFrame` / `ActOption` / `ActDone`（`components/common/ActCard.tsx`、`AskUserQuestion.tsx`，设计 `2026-10-26-turn-cards-unified.md`） | 两处各写各的 |
| P3 | 卡片放在"交回的东西"tab 里面，右边却是一个指路的"Answer"大按钮 | `RunOverlay.tsx` 把 ask 交给 `DeliverableTab`；`swarm_out_settle._actions` 对 `needs_answer` 给出 `answer` 动作（`core/swarm_out_settle.py:297-298`），前端把它画成主按钮，点了只是切到那个 tab | 要做的决定被放在次要位置，指路的东西被画成了动作 |
| P4 | 命令面板按钮和文字分不清，确认流程挤在一起 | `RunOverlay.tsx:419-447`：每个命令是"名字 + 一行说明 + 快捷键"整块做成按钮；丢弃 / 修正重跑 / 手动落地的确认是一列整宽按钮，取消也是同样的整宽按钮（`:341-381`） | 没有区分"动作"和"说明"；确认没有统一的样子 |
| P5 | 经桥回答的问题被记成"没回答" | 记录 `000001-turn.json` 第 43 行的 `ask_user_question` 块 `answers: null`；这一单的结果 `human: {unanswered: 1, answered_questions: 0}` | 答案**确实写进了** Hive 的数据库：决定镜像找到了 run 自己的问题卡，走 `continue_with_answer`，它先调 `question_prompt.record_answer`（`session_router.py:6140`）。但 Hive 的运行记录是**按轮封存、只追加**的：`_spawn_turn_seal` 在每一轮结束时把这一轮的行投影成一个 `turn` 条目（`hive_records.seal_turn`），下一轮从上一轮最后一行之后开始（`turn_slice` 的 `after_id`）。问题那一轮在 08:19:44 停下时就封存了（那时还没有答案），08:22 答案写到第 43 行上，可这一行已经在封存过的条目里，后面的条目不再包含它。Host 读的是记录，不是 Hive 的数据库，所以看到的永远是 `answers: null`。（ToDo `5205bf49` 写的"后端不保存答案"不对，已经保存了） |
| P6 | 检查格把跑过的检查显示成 `not_run` | 结果的 `checks` 里 smoke-import、各项 lint、security-scan、tsc、contract-lint、desktop-build 都是 `not_run`；run 自己的 REPORT 和 `pipeline/test_report.json` 写着都跑了、都通过 | `swarm_out_results._checks`（`:178-205`）只认"命令在一行开头或分隔符之后"（`_runs`）。这一单里每个检查都是这样跑的：`perl -e 'alarm 110; exec @ARGV' python scripts/smoke_import.py`（AGENT 规则要求每个测试命令都加墙钟上限），或者 `for c in "scripts/smoke_import.py" …; do … python $c`。前者命令前面有包装，后者命令名在循环变量里，都匹配不到。REPORT 读的是 run 自己的 `test_report.json`，那里每条命令都列着、带结果 |
| P7 | Hive 上 vitest 起不来 | REPORT："Node 18.20 here, jsdom needs Node >=20.19" | Hive 装的是 `nodejs20 npm`（`hive/setup.sh:46`、`backend/hive/user_data.py:26`），但默认的 `node` 仍是 18（AL2023 上 `nodejs20` 装的是 `node-20`，`npm` 包会带默认的 18；实现时先在 Hive 上确认） |
| P8 | `test_swarm_out_hive_decisions.py` 在 Hive 上有 3 个失败（本机全过） | `test_report.json`："--deselect the 3 test_swarm_out_hive_decisions … tests that fail identically on the untouched base checkout"；所有测试命令都标着 "(SWARMAI_MODE unset)" | Hive 的环境漏进了测试。run 自己要把 `SWARMAI_MODE` 去掉才能跑测试，说明至少 `SWARMAI_MODE=hive` 会改变被测代码的行为（`hive_records.on_hive()` 等）；那 3 个在去掉以后仍然失败，还有别的环境（嫌疑是 Hive 的数据目录 / 工作区路径）。实现时复现确认 |
| P9 | 本机 main 的 BVT 起不来 | `scripts/run_bvt.sh` 遇到 manifest 列着但已删除的文件就退出；`982c32190`、`baeabf19f` 删了三个测试没改 manifest | **已修**（`14cc07670`、`d2f02ec89`）。还缺预防：删测试时没有任何东西提醒改 manifest |
| P10 | `/runs` 里 `pipeline.run_id` 是空的 | 这一单的 `result.pipeline.run_id: null`，实际是 `run_8d2d6794` | 是有意的：`swarm_out_results.public()`（`:995`）不让任何 Hive 写的字符串进 `/runs`，所以直接写死 `None`。但 run id 有固定的样子（`run_` + 8 位十六进制），和 `landed_sha` 一样可以按样子校验后放进来；Swarm Run 的 host 要用它指认子 run |

## 2. 设计

### 2.1 一个等待的五个阶段：由 Hive 写的事实推出，Hive 明说"收到了"

问题、审批、决定三种等待在 Host 上用同一套阶段，每一个都从桥上的文件推出来，Host 不另存。

**Hive 写什么（`swarm_out_bridge`、`swarm_out_decisions`）：** 一个等待结束时，Hive 在 `closed/<session>/<key>.json` 写一个关闭记录，`why` 说明它是怎么结束的：

| `why` | 什么时候写 | 以后会不会变 |
|---|---|---|
| `delivered` | Hive 用上了这个回答：问题 / 审批是桥把回答交给会话（`_deliver`）以后，会话接走它、它离开等待列表的那一轮；决定是记录路径 `answer_from_dashboard` 成功的那一刻，或者卡片路径交出回答以后 run 不再等它的那一轮 | 不变：写了 `delivered` 的 key 不再写别的关闭（重启后决定这边先读已有的关闭；问题这边 `reconcile` 本来就跳过已关闭的 key） |
| `answer_refused`，带 `reason`（固定的几个代码） | 回答没有被用上：`no_option`（选的不是这个等待的选项）、`changed`（回答对应的卡片和现在的内容不一样）、`too_long`（内容太长没显示全，不收批准）、`run_refused`（run 已经往前走了，或者别处先回答了）、`not_recorded`（记录重试用完） | 可以变成没有 `why` 的关闭：被拒以后这个等待在别处了结了（Hive 自己的页面、过期、会话结束），拒绝不再重要 |
| 无 | 等待离开了，但不是因为 Host 的回答：在 Hive 自己的页面回答了、过期了、会话结束了、Hive 重启 | — |

- 决定今天没有关闭记录，只是从 `decisions.json` 里消失。改成和问题一样写 `closed/`（决定的 key 以 `dc-` 开头，桥的 `closed/*` 写权限已经覆盖，不改 IAM）。快照条目里的 `refused` 字段删掉，拒绝只看 `closed/`。
- `reason` 是代码，不是 Hive 写的句子；Host 按代码显示自己的文案（中英两份）。Host 列表里这个代码叫 `refused_reason`（审批自己的 `reason` 字段是它的说明文字，不能重名）。
- 交出回答到会话接走之间如果 Hive 重启，问题这边 `reconcile` 会写 `why: restart`，Host 显示"已不再等待"。这个窗口不超过一轮（约 20 秒），显示也是诚实的（Host 确实不知道它用没用上）。
- 只有 `delivered` 才说明 Hive 用了这个回答。等待从列表里消失、或者一个没有 `why` 的关闭，都**不能**当成"Hive 已收到"。

**Host 怎么推阶段（`core/swarm_out.py`）：** `_open_asks` 和 `_read_decisions` 合成一个 `_waits(hive, session)`，对每个 key 看四样东西：还在不在列表里（`ask/` 未关 / `decisions.json` 里有）、`answer/` 有没有、`closed/` 有没有、关闭的 `why`：

| 阶段 | 由什么推出 | 卡片 |
|---|---|---|
| 等你 | 还在列表里，没有 `answer/` | 可选，可提交 |
| 已送出 | 还在列表里，有 `answer/`，没有关闭记录 | 一行 `ActDone`：选了什么，"等 Hive 接收（约 20 秒一轮）"；不能再点 |
| Hive 已收到 | 关闭记录 `why: delivered` | 一行 `ActDone`：选了什么，"Hive 已收到 · 时间"；保留到这一单结束 |
| 没用上 | 关闭记录 `why: answer_refused` | 卡片可见，选择保留但不能再提交；红字写原因（按 `reason` 代码），下面一个"打开会话"链接：到 Hive 会话里直接回答 |
| 已不再等待 | 有 `answer/`，等待已离开列表，关闭记录没有 `why`（或者没有关闭记录） | 一行 `ActDone`，灰色："这个回答没有被用上，等待已经在别处了结" |

- "选了什么"从 Host 自己写的 `answer/` 里读：Hive 角色对 `answer/` 只有读权限（`hive/provisioner.py:102`），里面的内容只有 Host 写过，可以直接显示，不需要本地再存一份（r1 的本地副本删掉）。
- **不再重选。** 回答是一次写入（`IfNoneMatch`），一个 key 只能回答一次。被拒的几种原因里，没有一种是换个选项就能解决的：决定内容变了会出一个新的 key（`decision_key` 包含问题和选项），也就是一张新卡；另外几种原因是 run 已经往前走了，或者这个等待只能在 Hive 自己的页面回答。所以"没用上"的卡片只给原因和"打开会话"。
- **轮询开销：** `answer/` 和 `closed/` 今天每一轮已经各 LIST 一次，不增加。关闭记录和回答的内容都按 key 缓存（`delivered` 和回答写了就不会变）；只有被拒的关闭记录不缓存（它还会变成无 `why`）。新增的 GET 只发生在新的 key 上。
- **旧 Hive：** 不写 `why: delivered` 的 Hive，回答以后的等待最后都会显示成"已不再等待"。这是诚实的显示（Host 确实不知道它用没用上）；两台 Hive 跟本设计一起更新。

**这一单的状态（`run_state` / `swarm_out_settle.derive`）：** 同一个会话里可能有几个等待，按下面的顺序取第一条：

1. 有"等你"的等待 → `waiting`，原因照旧（`question` / `decision` / `approval`）；actions 照旧，`answer` 改成提示（§2.3）。
2. 有"没用上"的等待 → `waiting`，原因 `answer_refused`；actions：`open_session`（主）、`discard`。
3. 有"已送出"的等待 → `running`，原因 `answer_sent`；actions：`open_session`、`discard`，没有主动作（这一刻什么都不用做，等 Hive 下一轮）。
4. 否则照 Hive 报的会话状态。

### 2.2 一张等待卡：Swarm Out 用 chat 的那张

- **组件：** Swarm Out 的问题、决定、审批都用 `ActFrame` / `ActOption` / `ActDone`，和 chat 的 AskUserQuestion、权限请求是同一套组件。
  `LiveDispatches.tsx` 里自己写的 `AskCard` 删掉，换成一个薄的 `SwarmOutWaitCard`：只负责把 §2.1 的阶段和内容翻译成这几个组件的参数，以及调用 `swarmOutService.answer`。
  - 决定：标题"一个 pipeline run 需要你拍板"，meta 显示 `Decision · 2 分钟前`；正文是问题和"为什么它定不了"；每个选项一个 `ActOption`（单选圈）。
    选项框里第一行是 `<id> — <choice>`，第二行先放风险标签（高风险、不可撤回，小色块），再放后果；底部是"提交选择 ⌘↩"。
  - 问题：和 chat 的 AskUserQuestion 完全一样：选项、"其他（自定义回答）"、多选时用方框。
  - 审批：和 chat 的权限请求一样：命令原文、原因、"批准 / 拒绝"。命令太长、显示不全时，不出"批准"。
- **先选再提交**，和 chat 一样：选中的选项框高亮、圈变实心，可以改选；提交前不会发任何东西。
- **提交以后：** 后端写回答成功，卡片立刻变成"已送出"那一行（不等下一次轮询）；之后按 §2.1 的阶段变化。三种等待都一样，问题回答以后也不再直接消失。
- **提交失败**（网络、400、409）：卡片底部用红字写后端给的原因（§2.4），选择保留。如果原因是"已经回答过"，卡片在下一次轮询时按 §2.1 显示成对应的阶段。
- **位置：** 大卡片里，等待卡放在左栏最上面（验收条下面、tab 上面），因为它是这一刻要做的决定；不再放在"交回的东西"tab 里。Swarm Out 列表页里不属于任何一单的等待照旧放在那里，用同一个 `SwarmOutWaitCard`。

### 2.3 命令面板：按钮是按钮，说明是说明，确认是一个统一的样子

面板从上到下三个区，区和区之间的间距大于区内间距（design-judgment 第 6 条）：

1. **状态行：** "现在：<状态> · 多久"，状态用这一单的语气色。`answer_sent` 写"已回答，等 Hive 接收"；`answer_refused` 写"回答没用上"。
2. **动作区：**
   - 按钮只放动作名和快捷键，一律是同一种按钮样式：主动作实底，其余描边。说明不放进按钮，放在这一组按钮下面，一行灰字。
   - `answer` 不是动作，是指路：后端在 `derive` 里给它 `variant: "hint"`、`primary: false`；前端看到 `hint` 就不画成按钮，画成一块淡色提示"在左边的卡片上选一个选项并提交，这一单就会继续"，快捷键把焦点移到卡片。
   - 次要动作放在"其它"下面，同样是描边按钮加一行说明。
3. **丢弃：** 放在最底下，单独隔开；红色描边按钮"丢弃…"（省略号表示还要确认），下面一行后果说明。
- **确认（丢弃、修正重跑、手动落地）用同一个 `ConfirmSheet`：** 在面板里原地展开，替换动作区。内容依次是：
  - 标题（危险操作用红色）、一段后果说明；
  - 需要文字时，一个多行输入框（标签在框上面，至少 4 行）；
  - 最底一行两个并排的按钮：右边"确认动作名 ⌘↩"（危险操作红色实底），左边"取消 Esc"（描边）。
  - 出错时，错误原文（§2.4）显示在按钮行上面。
- **落地（land）** 的 ReturnPanel 也放进 `ConfirmSheet` 的正文里，底部按钮行同样是"完成 / 取消"。

### 2.4 错误原因不再被藏掉（全应用）

`desktop/src/services/api.ts` 的错误转换：任何状态码（400、404、409、422、504 等），只要响应里有后端写的原因就用它：`detail` 是字符串时用它本身，`detail` 是对象时用 `detail.message`。只有两样都没有时，才退回 "Invalid request" 这类通用文案。
现在断言 "Invalid request" 的两个前端测试改成：没有 `detail` 时才是通用文案，有 `detail` 时是 `detail`。这一处是全应用的，所有调用方都能看到后端写的原因。

### 2.5 回答写进运行记录（P5）

答案已经写进了 Hive 的数据库，缺的是把"这一行后来被回答了"带进运行记录。改两处：

- **数据库：** `update_question_answers` 同时写 `answered_by`（`person` / `host`，`continue_with_answer` 已经有这个参数），为 hive-approvals 设计预留。
- **封存一轮时（`hive_records.seal_turn` / `turn_projection`）：** 除了这一轮自己的行，再带一个 `answers` 列表：在读到的行（`list_by_session_paginated` 本来就读最近 `TURN_ROWS` 行）里，**已经封存过的**那些行上，有答案的 `ask_user_question` 块，各取 `{toolUseId, answers, answered_by}`。被回答的问题一定在回答之前的那一轮里，所以回答之后的下一轮封存时就会带上它。同一个答案出现在几个条目里没关系，按 `toolUseId` 去重。
- **Host 读（`hive_records.turn_rows`，`summarize` 的输入就是它）：** 把读到的所有 `turn` 条目里的 `answers` 按 `toolUseId` 合到块上；`summarize` 不改，按现在的口径计数（`answers` 非空才算回答）。问题所在的条目因为读取上限没读到时，答案不会凭空造一个块。

不改成重新封存旧条目：记录是只追加的（`append` 带 sha256 进 manifest），Host 也已经拉走了旧条目。

### 2.6 检查格：先读 run 自己的测试记录，再认包装过的命令（P6）

`_checks` 每个检查的状态按两处来源定：

1. **run 自己的测试记录。** `Outputs/<dispatch>/pipeline/test_report.json` 只是那一单的 agent 自己选的位置，不是约定。run 自己的记录是它的 TEST 校验报告：DELIVER 时写进 run 目录的 `run_audit.json`（`details.test_reports`），REPORT 的测试段就是从这里渲染的。所以 run 结束时写的 `pipeline_summary` 块带上这些命令（`tests`: `{repo, command, result}`，命令截到 500 字、最多 60 条），它随运行记录交回 Host。对每个检查，在这些命令里找跑了这个检查的那条（同一个 `_runs`，先去掉包装）：`pass` → `ran`，`fail` → `errored`，其它 → `unknown`。
2. **会话里的 Bash 调用**（现在的做法），用于没有测试记录、或者测试记录里没有这一项的检查。`_runs` 在匹配前先去掉命令前面的常见包装：`perl -e '…' `（`exec @ARGV` 那种）、`timeout` / `gtimeout <n>`、`env [-u X] [K=V …]`、`K=V`、`nice`、`time`。循环变量里的命令（`for c in …; python $c`）仍然认不出来，这时第一处来源会补上。

状态的词照旧（`ran` / `errored` / `unknown` / `not_run`），不出现"passed"：两处来源都是 Hive 自己报的，Host 只说它跑没跑、报没报错。

### 2.7 Hive 的 Node（P7）

`hive/ensure-node.sh`：在 `/usr/bin` 里找已装的 Node（`node-<major>` 和 `node`），把最新的一个和它自己的 `npm` / `npx` 链接到 `/usr/local/bin`（服务、`sudo -u swarm` 和登录 shell 的 PATH 里都排在 `/usr/bin` 前面），再检查版本不低于前端要求。前端 `desktop/package.json` 原来没有 `engines`，现在写上 `"node": ">=20.19.0"`（jsdom 自己的下限），脚本里的下限由测试保持和它一致。
新装 Hive（`user_data` 下载包之后）不够就安装失败；更新（SSM 更新脚本）不够只打 WARN、继续更新，因为后端不需要 Node，不该因为前端测试跑不了就挡住整个更新。已有的两台 Hive 跟下一次更新一起换。

### 2.8 测试不吃环境（P8）

本机复现不了：全新 venv（`uv pip install -e ".[dev]"`，和 Hive 上 run 的做法一样）、`SWARMAI_MODE=hive`、空 `HOME`，这个文件都全过。run 的反思里写的是"在全新 venv 里、当前 main 上照样失败"，所以原因在 Hive 机器本身（Linux、它的 Python 或 git），不在本机能造的环境里。
做法：E2E 那一单在 Hive 上跑这个文件，并把失败的测试名和报错原文带回来；按带回的原因修 fixture，不在产品代码里为测试开后门。Hive 上的 run 每次都要手动 `env -u SWARMAI_MODE` 才能跑测试，这本身也要在那时一起看。

### 2.9 删测试时提醒改 BVT manifest（P9 的预防）

`scripts/precommit_bvt_manifest.sh`（进仓库，有测试）：暂存区里有 `backend/tests/` 的改动时跑 `bash scripts/run_bvt.sh --list`，manifest 里有不存在的文件就挡住提交，并说是哪个。本机的 pre-commit 钩子（`.git/hooks/pre-commit`，不进仓库）调用它，AGENTS.md 的钩子表里加了一行；CI 的 BVT 步骤本来就会因为同样的原因失败，不另加。
这是这个仓库自己的纪律，放在仓库里，不进产品的 gate（SOUL P7）。

### 2.10 `/runs` 带上 run id（P10）

`public()` 里 `pipeline.run_id` 不再写死 `None`，而是 `_match(_RUN_ID_RE, p.get("run_id"))`，`_RUN_ID_RE = run_[0-9a-f]{8}`。和 `landed_sha` 同一种做法：只有固定样子的值能进来，Hive 写的其它字符串照旧进不来。`summarize` 里的 `run_id` 已经有值（来自 `pipeline_summary` 块的 `runId`），不用改。

## 3. 不在本设计里

- host 回答 Hive 的等待（hive-approvals 设计的 P0–P2）。本设计只为它留好 `answered_by` 字段和 §2.1 的阶段。
- Swarm Run。
- REPORT.md 随代码提交进仓库的做法（仓库里已有 205 个这样的文件，是现行约定）。

## 4. 验证计划（做之前定好）

| # | 不变量 | 怎么验证 |
|---|---|---|
| V1 | 一个等待只回答一次；回答后立刻显示"已送出"，不能再点；只有 Hive 写了 `why: delivered` 才显示"Hive 已收到"；离开列表但没有 `delivered` 时显示"已不再等待" | 后端：`_waits` 对 §2.1 五个阶段各一个单测，外加"离开列表、无关闭记录"和"被拒后又被无 `why` 的关闭覆盖"两个；桥：问题、决定两条路径各断言写出的 `why`；前端：`SwarmOutWaitCard` 五个阶段的 vitest |
| V2 | 这一单的状态和 actions 按 §2.1 的顺序 | `derive` 单测：等你 + 已送出同时有时是 `waiting`；只有已送出时是 `answer_sent`、没有主动作；没用上时是 `answer_refused`、主动作 `open_session` |
| V3 | 后端的原因原样显示 | `api.ts` 单测：400 / 409 / 422 带字符串 `detail`、带 `detail.message`、两样都没有，三种各一个 |
| V4 | Swarm Out 的等待卡和 chat 用同一套组件 | 结构测试：`SwarmOutWaitCard` 渲染出 `data-act-frame` / `ActOption`；`LiveDispatches.tsx` 里不再有自己的选项按钮 |
| V5 | 命令面板：按钮里只有动作名和快捷键；`hint` 不是按钮；确认都用 `ConfirmSheet` | vitest：每个 stage 的面板里按钮文本只含名字和快捷键；丢弃 / 修正 / 手动落地都渲染 `confirm-sheet` |
| V6 | 回答之后的那一轮封存时带上答案；`summarize` 记为已回答 | 后端测试：在 Hive 模式下走一遍"一轮停在问题 → 封存 → `continue_with_answer` → 下一轮封存"，读回第二个条目带 `answers`（含 `answered_by`），`summarize` 得 `answered_questions = 1`、`unanswered = 0`；去掉封存时带答案这一步，测试变红 |
| V7 | 检查格和测试记录一致 | 用这一单的 `000004-turn.json` 和 `test_report.json` 做 fixture：smoke-import、lint、security-scan、tsc、contract-lint、desktop-build 都是 `ran`；只用 Bash 调用时，`perl -e 'alarm 110; exec @ARGV' python scripts/smoke_import.py` 认成 `ran`，`grep "python scripts/smoke_import.py"` 仍然不认 |
| V8 | Hive 上 `node --version` ≥ engines；不够安装失败 | 安装脚本的单测（解析 engines、比较版本）；更新一台 Hive 后实测 |
| V9 | `test_swarm_out_hive_decisions.py` 在 Hive 环境变量下也全过 | 带 Hive 环境变量的子进程跑这个文件 |
| V10 | manifest 里有不存在的文件时提交被挡 | 在临时仓库里删一个列着的测试，提交检查失败并点名 |
| V11 | `/runs` 带 run id，样子不对的值进不来 | `public()` 单测：`run_8d2d6794` 进来，`run_x; rm` 是 `None` |
| E2E | 单路再跑一次：派一单带决定的任务，点一次 A，看到"已送出"→"Hive 已收到"；跑完后结果里 `answered_questions = 1`、`unanswered = 0`，检查格和 REPORT 一致，`/runs` 的 `run_id` 有值 | 更新两台 Hive 后真跑 |

每一处修复都做 mutation 检查（去掉修复，对应测试变红）。审查：§2.1 和 §2.5 由 correctness 加 state-machine 审，§2.2–2.4 由 ux 审，§2.7 由 operational 审。

## 5. 顺序

1. 后端和桥：§2.1（Hive 写 `why`、Host 的 `_waits` 和状态）、§2.5 记录带答案、§2.6 检查格、§2.10 run id。
2. 前端：§2.2 等待卡（`SwarmOutWaitCard`）、§2.3 命令面板和 `ConfirmSheet`、§2.4 的 `api.ts`。
3. Hive 和仓库：§2.7 Node、§2.8 测试、§2.9 manifest 检查。
4. 重新打 Hive 包、更新两台 Hive、重启本机（重启要 XG 批准），然后跑 E2E。

## 6. r1 → r2

一次前提审查（零上下文 correctness reviewer）对照代码找出的问题，按原因合并后一次改完：

- **P5 的原因写错了。** r1 说后端不保存答案；其实 `record_answer` 早就在保存。真正的原因是运行记录按轮封存、只追加（§1 P5、§2.5 重写）。
- **"Hive 已收到"缺少证据。** r1 把"从列表里消失"或者一个普通的关闭当成收到；这两种情况也可能是在别处回答了。改成只有 Hive 写的 `why: delivered` 才算，另加"已不再等待"阶段（§2.1）。
- **"被拒后重选"做不到。** 回答只能写一次；而且被拒的原因里没有一种是重选能解决的。删掉重选，改成原因加"打开会话"（§2.1、§2.2）。
- **`answer_sent` 的条件和 actions 没有定义。** 补上同一会话几个等待时的取值顺序，`answer_sent` 没有主动作（§2.1）。
- **本地副本不需要。** Hive 对 `answer/` 只有读权限，Host 直接读自己写的回答（§2.1）。
- **错误原文要覆盖所有状态码和对象形式的 `detail`**，两个旧测试一起改（§2.4）。
- **`answer` 的画法由后端标出**（`variant: "hint"`），前端不再按 key 猜（§2.3）。
- **P6、P10 查清了原因**，修法从"二选一待查"定成具体的一种（§2.6、§2.10）。

## 7. 实现与本设计的出入（run_8437ff7e）

- §2.1 `delivered` 的写入时机改成"会话接走、离开等待列表时"（问题 / 审批）和"run 记下或不再等待时"（决定），不是"交出的那一刻"：交出以后续接失败会重试，那时写 `delivered` 会说错。
- §2.1 Host 列表里拒绝原因的字段名是 `refused_reason`（`reason` 已经是审批的说明）。
- §2.5 合并答案放在 `hive_records.turn_rows`，`summarize` 不改。
- §2.6 来源改成 run 自己的 TEST 校验报告（`pipeline_summary.tests`），不读 agent 自己放在 `Outputs/` 下的文件。
- §2.7 更新路径只警告不失败；`engines` 是新加的。
- §2.8 本机复现不了，放到 E2E 在 Hive 上复现。
