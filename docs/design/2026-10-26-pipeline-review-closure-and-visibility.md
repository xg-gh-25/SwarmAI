---
title: Pipeline 审核收口 — finding 关闭协议、run 可观察性、没跑过或说是 base 上就红的测试
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r3 — XG 已定 §8 三条（2026-10-27）；实施中
---

# Pipeline 审核收口

## 0. 主旨和约束

本文只改 pipeline 的**后端**（审查、测试证据、报告），遵守 `2026-10-26-pipeline-two-ends.md` §0 和 `2026-10-24-swarmai-usable-by-anyone.md` §1：

- 写代码、写测试、跑测试都是 agent 的 native 工作，pipeline 不观察、不重跑（usable-by-anyone §3.3.3）。
- pipeline 的确定性检查判定的是**证据是否成立**，不是测试是否真的通过（two-ends §0.2）。本文的每条规则只比较事件、tree、文件内容、命令文本这类确定的东西。
- 对所有用户、所有仓库、所有语言一样（P2、P7）：不认 SwarmAI，不按语言或框架分支。
- 拿不到确定证据的地方，不造一个看起来像证据的机制，只把事实照实写进报告，交给人判断。

## 1. 起因：一次 run 的实测

run `run_b34bf691`（trivial，改 `core/run_protocol/` 五个文件，2026-10-26）：

- 两位专家 R1 共报 5 条 finding（1 HIGH、1 MEDIUM、3 LOW），5 条都修了，R2 两位专家都写明"已修好"。
- 落地后的审计却是 `Quality WARN · 0/5 findings closed · 1 HIGH finding open · +1 more`，第二条是 "correctness review not answered"。
- agent 为了拿到 finding 的 ID，只能直接读 `events.jsonl` 再调 Python fold；`review-address` 失败两次，第一次失败已经写进了一半事件。
- TEST 遇到一个 base 上本来就红的测试（别的会话留下的），只能标成 `not_run` 并手写理由；审计里看不出来。

审计是人决定要不要 push 的依据。它在这次 run 上的 Quality 结论是错的，而人从 REPORT 里看不出来。这是本文要先解决的。

## 2. 问题 A：finding 的关闭协议

### 2.1 现状（每条都对照过代码，审查者逐条复核为真）

| # | 现象 | 代码 |
|---|---|---|
| A1 | `review-address` 不是原子的：先逐条写全部 `address`，再逐条写 `proof`；proof 合不合法要到写入时的 fold 才判断。本次第 3 条被拒时，日志里已有 5 条 address、2 条 proof，改好重交又写了两批 address | `review.py:382-403`，`engine.py:1157-1161` |
| A2 | R2 答复格式只有一行通用模板，五个状态词都列着，不说每条 finding 能用哪个；`closed_proven` 只接受 `ADEQUATE`/`TEST_INADEQUATE`，专家写了 `RESOLVED` | `review.py:25`，`review.py:446-460`，`findings.py:22-23,143-148` |
| A3 | 不合法的状态行被丢弃（记一条 `invalid_r2_status`），这条 finding 就算 R2 没答，"R2 没答 → `open`/`OPEN`"把它改回 open，**`closed_proven` 也一样** | `hook_bridge.py:311-318`，`findings.py:327-331` |
| A4 | RV-1a 只认"完整"的答复：记下的 `finding_status` 数要等于 `n_status`；`n_status` 把被丢弃的行也算进去，于是这份答复被当成没答 | `answers.py:50`，`checks.py:370-372,384-392` |
| A5 | `absence`（缺测试、缺检查）没有关闭路径：`reported_test` 只收 behavior/contract，`diff_assertion` 只收 docs/design/security_policy | `findings.py:184,193` |
| A6 | `diff_assertion` 的 `touches_finding` 看专家报的行号是否落在 base→当前 tree 的 hunk 里；专家报的是 handout 文件里的行号（本次 `git_tree.py:412`，文件在审查 tree 上只有 167 行），对不上；R2 看到的"修复"是 agent 自己写的 `hunk` 文字，不是 git 的 diff | `engine.py:1298-1314`，`review.py:364,456` |
| A7 | 审计只把 `closed_asserted`、`refuted`、`accepted` 和 R2 确认 ADEQUATE 的 `closed_proven` 算作关闭；`closed_unproven` 不算 | `audit.py:366-369` |

A2 → A3 → A4 是一条链：一个用错的状态词，让一条已证明的 HIGH 变回 open、RV-1a 失败，审计出两条错误结论。"R2 状态词不合法"10-26 已出现过一次（`ebb1ba864` 修了"整份答复被丢"，没修后面这两步）。

### 2.2 根因

"哪个状态词适用于哪条 finding"这条规则本身是共用的（`findings.r2_status_allowed`，账本和答复记录都用它），但它**没有告诉要用它的人**：专家的答复模板不分 finding 列全部五个词，agent 的 `next`、报错和命令都不说一条 finding 现在能怎么关。错了之后的处理又不一致：handoff 半写，状态行被丢后 finding 被改回 open，答复被算成没答。

"沉默就改回 open"本身是一道保护：R2 没确认测试够不够，HIGH 就继续挡。这道保护要留，只是不能让"用错一个词"触发它。

### 2.3 设计：一张关闭表，所有读者都用它

**A-T 关闭表**（`findings.py` 一处定义，常量 `CLOSURE`；`r2_status_allowed`、`_proof`、`_status`、`push_effect` 都改成读它）：

| finding 类型 | 关闭证据（agent 提交） | 提交后 | R2 允许的词 | R2 之后 |
|---|---|---|---|---|
| behavior、contract | `reported_test`：当前 tree 上验证报告里一条通过的命令 | `closed_proven` | `ADEQUATE` / `TEST_INADEQUATE` | ADEQUATE → 关闭；TEST_INADEQUATE → `invalid_test`（可再修再证） |
| **absence** | `reported_test`，且该报告的 `tests_changed` 非空（`fold` 不读报告 blob，所以 engine 在写 `proof` 事件时记下 `tests_changed` 的个数，新的可选字段；`events.py:146-152`） | `closed_proven` | 同上 | 同上 |
| behavior、contract、absence（没给测试） | 无 | `open` | `RESOLVED` / `OPEN` / `REFUTED` | RESOLVED 不关闭（行为类必须有测试）；REFUTED 后可提交 `refuted` |
| docs | `diff_assertion`（R2 判 RESOLVED 之后，见第 4 点） | — | `RESOLVED` / `OPEN` / `REFUTED` | `closed_asserted` |
| design、security_policy | 同上 | — | 同上 | `closed_unproven` |
| 任意 | `refuted`（R2 判 REFUTED 之后，带可解析的引用） | — | — | `refuted` |
| 任意 | 人工 `accept-finding` | — | — | `accepted` |

**"关闭"的口径**（审计、`findings` 命令、`next` 同一个函数）：
- 关闭：`closed_asserted`、`refuted`、`accepted`、R2 判 ADEQUATE 之后的 `closed_proven`，以及 MEDIUM/LOW 的 `closed_unproven`。
- 未关闭：`open`、`invalid_test`、R2 还没判 ADEQUATE 的 `closed_proven`（写作 `awaiting R2`）、HIGH/CRITICAL 的 `closed_unproven`（只经审查、没测试证明的 HIGH，和今天 `push_effect` 的判断一致）。
- `push_effect`（挡 RV-2、写进 Quality）：HIGH/CRITICAL 且未关闭。比今天多挡一种：R2 没判 ADEQUATE 的 `closed_proven`（今天它靠"沉默改回 open"间接被挡，见第 3 点）。

**读这张表的地方：**

1. **`review-address` 先算完、再一次写入（A1）。** 先按表检查 handoff 每一项（finding 存在、状态允许、证据适用于它的类型和状态、`reported_test` 的命令在报告里找得到、absence 的报告 `tests_changed` 非空、这条 finding 没有还在等的 R2 handout），再算出 R2 要用的 tree、diff 和每位专家的 R2 输入（含 prompt lint）。全部成功后，把 address、proof、reclassify 和 R2 handout 用已有的 `store.append_batch`（`store.py:409-461`：逐条对链校验，一次写入、一次 fsync）一次写进去。任何一步失败，日志不变，报错列出**所有**不合法的项和每项允许的做法。
2. **R2 handout 写明每条 finding 允许的词（A2）。** 写 handout 时按表算出每条的 `allowed_status`，存进 handout 的 payload，并写进专家看到的 R2 输入和答复说明（"每条只能用它的 allowed_status 里的词"）。答复记录按 handout 里存的这份判定，不再按答复时的状态重新推（两者之间状态可能已变）。
3. **答复里的词不合法时（A3、A4）**：
   - 对 `closed_proven` 答 `OPEN`：按 `TEST_INADEQUATE` 记（只会更严，不替专家放行），finding 变 `invalid_test`。
   - 对 `closed_proven` 答 `RESOLVED`：这一行记为 `UNANSWERED`，finding 状态不变。`push_effect` 照样挡 HIGH（未经 ADEQUATE），所以不会因为用错词而放行。
   - 其他不合法的组合：同样记 `UNANSWERED`、状态不变。
   - 有 `UNANSWERED` 时，`next` 让 agent 重派一次：写一个**新的** R2 handout 事件（下一个 attempt），只含这些 finding，并在提示里说明上次哪个词不合法。重派仍不合法就停在 `UNANSWERED`，审计照实写。这个重派属于 R2，不是第三轮；每位专家最多一次。
   - `invalid_r2_status` 的完整性事件把答复的 event id 写进 `conflict_id`，避免重派后同样的错词产生同 id 的事件。
   - 答复事件新增可选字段 `n_status_ignored`（写入前算好）。RV-1a 的"完整"改为：`finding_status` 数 = `n_status - n_status_ignored`。
   - "R2 沉默就改回 open"的规则保留，只对真正没有收到任何状态行的 finding 生效（没答复、答复没解析、答复漏了这一条）。
4. **`diff_assertion` 改成看文件，不看专家报的行号（A6）。** finding 所在的仓库和文件按它 R1 答复对应 handout 记下的 `code_trees` 解析（不再按"当前哪个仓库有这个路径"猜，文件被删除也能解析）。成立的条件：
   - 这个文件在"判 RESOLVED 的那次 R2 handout 的 tree"和当前 tree 里内容相同（R2 之后没再改）；
   - 并且和 R1 审查的 tree 里不同（确实改过）。
   R2 的输入里附上这个文件 R1 tree → 当前 tree 的 git diff，不只用 agent 写的 `hunk` 文字。旧 run 的 handout 没有 `code_trees` 时，退回今天按行号判断的规则。R1 答复格式写明"line 是该文件在审查 tree 里的行号"。
5. **树改了，关闭要重新算（审查者 LOW 13）。** `stale_closures`（`facts.py:120`）今天不含 `closed_proven`、`closed_unproven`；把两者加进去，和其他关闭一样，tree 变了（main 移动后不干净的 rebase、REVIEW 之后再改）就不再算关闭。干净的 rebase 按 patch id 判断，沿用 LG-1 的规则。
6. **`accept-finding` 的 state hash（审查者 MEDIUM 10）。** 请求和校验两边都对"沉默规则之后"的状态算 hash（今天请求一侧在之后、`_accept` 一侧在之前，`engine.py:1693-1698`，`findings.py:217-219`）。

按新规则，本次 run 会走成：2 条 `closed_proven` 等 R2，第一次答 `RESOLVED` → `UNANSWERED` → 重派一次答 `ADEQUATE` → 关闭；absence 那条用带 `tests_changed` 的 `reported_test` 关闭；docs、security_policy 两条 R2 判 RESOLVED，文件在那之后没再改 → `diff_assertion` 关闭 → `5/5 findings closed`。（这是推演；本次 run 的日志里没有这些事件，测试要按 §6 第 4 步造一个续跑场景，不能直接重放。）

## 3. 问题 B：agent 看不清 run 里的审查状态

### 3.1 现状

- 拿不到 finding 的 ID 和状态：`events` 只给 `event_id/kind/stage/at`，不给 payload（`cli_commands.py:403-406`）；`status` 返回 projection、owner、decision，不含 finding（`cli_commands.py:355-358`）。本次只能读 `events.jsonl` 再调 `findings.fold`。
- 被拒时不说规则：例如 `lesson L3 statement is volatile or invalid`，实际是超过 400 字符（`reflect.py:83-85`）；`proof cannot replace a terminal finding state` 不说是哪条 finding、它现在什么状态。
- 不是问题：CLI 的 stdout 只有 JSON（`cli_main.py:117`），警告走 stderr 的 logging（`engine.py:763`）；本次解析失败是 agent 自己把 stderr 合进了 stdout。

### 3.2 设计

- **`pipeline-v1 findings --project P --run-id R`**（只读，和 `status`、`events` 同属 `_READ_ONLY`）：每条 finding 一行 `{id, expert, severity, kind, file, line, status, r2_status, closed, close_with}`；`closed` 和 `close_with`（现在能用什么关闭，例如 `reported_test: report-tests then review-address`、`awaiting R2`、`re-dispatch R2`、`review-proof diff_assertion`）由 §2.3 的同一个函数算出。
- **`next` 在 REVIEW 里列出未关闭的 finding**，每条带 `close_with`，与 `findings` 同源。
- **拒绝信息带规则**：pipeline 由检查产生的拒绝（`ReviewError`、`FindingError`、`ReflectError` 和 engine 里对应的拒绝）一律写"哪一项 · 现在的值 · 允许的值或上限"。关闭相关的信息由关闭表生成；`reflect` 的长度、行数、易变词规则在信息里写出上限或命中的词。
- 测试不逐字比对文案：关闭相关的拒绝断言信息里含该 finding 的 `allowed`（来自表）；`reflect` 的几条拒绝断言信息里含上限数字。

## 4. 问题 C：没跑过、或说是 base 上就红的测试

### 4.1 现状

- TS-1、LG-1：TEST 报告里有 `fail` 就不能完成、不能落地（`checks.py:332-334`）。`not_run` 加理由可以（`validation_report.py:29`）。
- 本次 TEST 里一条测试在 run 的 base 上就是红的，agent 只能标 `not_run` 并写"base 上就红"。审计 Quality 的 verified 只看有没有通过、有没有 `fail`（`audit.py:361-363`），所以这条在审计里完全看不见；REPORT 也只有 `N not run` 的计数（`validation_report.report_lines`）。
- 一个被这次改动弄红的测试，也可以这样标；没人看得到。

### 4.2 r1 设计为什么不做

r1 设计了 base 报告（`report-tests --baseline`）加"同一条命令在 base 上也是 fail 就不挡"。审查指出，也核对过：

- base 报告的内容仍然是 agent 自己说的；tree 检查只证明"报告是在 base 上交的"，不证明测试在 base 上真红。要真证明，只能由 pipeline 自己在 base 上跑测试，这违反 §0。
- 按命令文本比对，一条跑整个测试集的命令（例如 `pytest tests/`）在 base 上红，就会盖住这次改动在同一测试集里新弄红的测试。
- TEST 在 REVIEW 之后，没有专家看 TEST 报告，"是不是同一个原因交给审查"并不会发生。

所以它会成为一个看起来像证据、其实不是证据的机制。按 P9，不做。

### 4.3 设计：照实写，不放行

- **REPORT 的测试一节列出每条 `not_run` 命令和它的理由**（今天只有计数），标题写明 "Not run (the agent's reason, not checked)"。
- **审计 Quality**：最新 TEST 报告里有 `not_run` 的命令时，Quality 不能是 PASS（WARN），这一格写 `n not run`。`fail` 照旧挡 TEST 和落地。
- 不加新的结果类型。"base 上就红"就是 `not_run` 的一种理由，写在 `reason` 里，REPORT 原样列出；人看到就能决定要不要先修 main。
- 不做：pipeline 自己在 base 上跑测试；按测试框架解析测试名；`red_at_base` 结果类型。

## 5. 四个问题（two-ends §0.1）

| 设计 | 在前端或后端？ | 规定了怎么写、怎么测吗？ | 用了原生 / 最轻的吗？ | 判定是确定性的吗？ |
|---|---|---|---|---|
| A 关闭表 | 后端（审查） | 没有 | 复用 `append_batch`、R2 handout、完整性事件；只加两个可选字段（handout 的 `allowed_status`、答复的 `n_status_ignored`），不加事件类型 | 是：类型、状态、文件内容、命令文本 |
| B `findings` / `next` / 拒绝信息 | 后端（审查、报告） | 没有 | 只读命令，信息由表生成 | 不涉及判定 |
| C not_run 照实写 | 后端（报告） | 没有 | 只改 REPORT 和 Quality 的文字与 verdict | 是：报告里有没有 `not_run` |

## 6. 实施步骤和验证计划

每步一个 commit，测试先行，变异验证（`scripts/mutation_check`）。

| 步 | 内容 | 新测试（先红后绿） | 变异 | 审查 |
|---|---|---|---|---|
| 1 | `findings.CLOSURE`；`r2_status_allowed`/`_proof`/`_status`/`push_effect` 读表；absence 进 `reported_test`（`proof` 事件新增可选 `tests_changed` 计数，absence 要求非零）；"关闭"口径函数 | 表驱动：类型 × 状态 × 证据 × R2 词 → 结果；HIGH `closed_proven` 未 ADEQUATE 时 `push_effect` 为真；HIGH `closed_unproven` 不算关闭 | 去掉 absence 一行；`push_effect` 不挡 awaiting R2；`closed_unproven` 不分严重度 | correctness |
| 2 | `review-address` 先算完再一次 `append_batch`（address、proof、reclassify、R2 handout） | 第 3 项不合法：日志不变，报错列出所有不合法项；在 R2 handout 生成处注入失败：日志不变 | 改回逐条写；handout 放到 batch 之后 | correctness、state-machine |
| 3 | R2 handout 存 `allowed_status`；答复按它判定；`OPEN`→`TEST_INADEQUATE`、`RESOLVED`→`UNANSWERED`；`n_status_ignored`；`conflict_id` 带答复 id；一次重派；沉默规则只管真没收到的 | HIGH `closed_proven` 分别在：R2 没答、答复没解析、漏了这条、答 `OPEN`、答 `RESOLVED` → 各自的结果；重派后同一个错词：不出现重复 id，答复照常记录；RV-1a 计数 | 恢复"沉默改 open 覆盖证明"；RV-1a 不扣 ignored；`OPEN` 不收紧 | correctness、state-machine |
| 4 | 续跑场景测试（rig）：R1 报 behavior、absence、docs、security_policy 各一条，按 `next` 走完 | 端到端：审计 `findings closed` 等于总数；中间一次 R2 用错词 → 重派后关闭 | — | — |
| 5 | `diff_assertion` 看文件（按 handout `code_trees` 解析仓库；R2 后未改且相对 R1 改过）；R2 输入附 git diff；旧 handout 退回按行号 | 行号对不上但条件成立 → 可关；R2 之后又改了 → 拒；没改过 → 拒；文件已删除 → 能解析；多仓库同名路径 → 按 handout 解析 | 恢复按行号；去掉"R2 后未改"条件 | correctness |
| 6 | `stale_closures` 加 `closed_proven`、`closed_unproven`；`accept-finding` 两侧 hash 同一状态 | rebase 不干净后两种关闭失效；对 awaiting R2 的 finding 做 accept，之后 fold 不报 stale | 去掉新加的两种状态；hash 改回沉默规则之前 | correctness、state-machine |
| 7 | `findings` 命令；`next` 列 `close_with`；拒绝信息带规则 | 命令字段；`next` 与 `findings` 一致；关闭类拒绝含 `allowed`；reflect 拒绝含上限 | `close_with` 改成常量 | correctness |
| 8 | REPORT 列出 `not_run` 命令和理由；有 `not_run` 时 Quality 不为 PASS | REPORT 内容；Quality verdict；`fail` 仍挡落地 | `not_run` 不影响 verdict | correctness |

**旧 run**：新字段都是可选的。旧日志里没有 `allowed_status` 的 R2 handout 按今天的规则判定；没有 `n_status_ignored` 的答复按今天的完整性判定；已在日志里的 `accept_finding` 用旧的 hash 规则校验，新规则只用于新请求。第 3、6 步各加一条"旧日志照样能 fold"的测试。

**最终验证（整套做完后跑一次，在这里定好，不在最后扩大）**：
- 本文新增的测试文件；
- `test_run_protocol_findings`、`_audit`、`_checks`、`_events`、`_landing`、`_git_tree`、`_review`（存在的那些），`test_pipeline_v1_review_tree`、`_human_loop`、`_direct_e2e`、`_knowledge_report`、`_untracked_report`、`_follow_next`，`test_reflect_in_cli`、`test_no_push_verdict`、`test_report_language`；
- 第 4 步的端到端续跑。

**必须成立的不变量**：
1. 被拒的 `review-address`（任何一步失败）不改变事件日志。
2. 一条 HIGH/CRITICAL 只有在关闭口径下关闭后才不挡 RV-2；用错状态词、R2 不答都不会让它被算作关闭。
3. 没有任何路径把 `closed_proven` 改回 `open`；只有 `TEST_INADEQUATE`（含按它处理的 `OPEN`）让它变 `invalid_test`，只有 tree 变化让它失效。
4. `findings`、`next`、审计、R2 handout 对同一条 finding 给出的"关没关、能怎么关"相同（同一个函数）。
5. 最新 TEST 报告里有 `not_run` 时，审计 Quality 不是 PASS，REPORT 列出每条命令和理由。

## 7. 不做的

- 不让 pipeline 自己跑测试或在 base 上重跑（推翻 r7，需另议）；因此不做 `red_at_base`（§4.2）。
- 不把 `RESOLVED` 映射成 `ADEQUATE`：两个词判断的东西不同（修没修好 / 测试够不够），映射等于替专家放行。（`OPEN` → `TEST_INADEQUATE` 只会更严，所以做。）
- 不加第三轮审查；R2 重派每位专家最多一次。
- 不新增事件类型；不按语言、框架、测试运行器解析测试名或结果。

## 8. XG 的决定（2026-10-27）

1. absence 用 `reported_test` 关闭，要求报告里有改动过的测试（§2.3 表）；不走人工 accept。原话："我们要 Autonomous，不要人介入"。
2. R2 用错词时重派一次，算在 R2 内、不算第三轮（§2.3 第 3 点）：同意。
3. C 只做"照实写、`not_run` 时 Quality 不为 PASS"，不做 base 上的证明机制（§4）：同意。

## 修订记录

- r1（2026-10-26）：初稿。
- r2（2026-10-26）：零上下文审查一轮（3 HIGH、7 MEDIUM、6 LOW），按原因一次并入：
  - 保留"R2 沉默就改回 open"的保护作用，改由 `push_effect` 挡未经 ADEQUATE 的 HIGH `closed_proven`；`OPEN` 按 `TEST_INADEQUATE` 收紧，只有 `RESOLVED` 记 `UNANSWERED`（HIGH 1）；
  - 重派改成新的 R2 handout，`conflict_id` 带答复 id（HIGH 3）；`n_status_ignored`（MEDIUM 4）；address、proof、reclassify、R2 handout 同一 batch（MEDIUM 5）；
  - `diff_assertion` 的条件收紧为"R2 之后未改且相对 R1 改过"，R2 附 git diff，按 handout `code_trees` 解析仓库（MEDIUM 8）；`allowed_status` 存进 handout（LOW 12）；
  - `closed_unproven` 只在 MEDIUM/LOW 时算关闭（MEDIUM 7）；`stale_closures` 加两种状态（LOW 13）；`accept-finding` 两侧 hash 一致（MEDIUM 10）；absence 要求 `tests_changed`（LOW 11）；
  - C 的 baseline 机制撤掉，改为照实写（HIGH 2、MEDIUM 6、LOW 14、15）；
  - §2.2 根因改写（LOW 16）；§6 的"重放本次 run"改成续跑场景测试（MEDIUM 9），补上审查列出的缺失测试。
- r3（2026-10-27）：§8 三条由 XG 决定，全部按 r2 的写法实施；补上 absence 检查需要的 `proof.tests_changed` 字段。
