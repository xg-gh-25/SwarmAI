---
title: Pipeline 审核收口 — finding 关闭协议、run 可观察性、没跑过的测试、落地摩擦、度量
created: 2026-10-26
updated: 2026-10-29
project: SwarmAI
status: r4 — 并入第二个 run（run_85e81059）的问题，审查一轮已并入；未实施
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

run `run_85e81059`（full，eval 发布判定，2026-10-26）又遇到同一类问题，外加几条新的（每条都对照过它的事件日志或代码）：

- `review-address` 第一次调用失败，但已经写进去 8 条 address；重交又写了 8 条后被拒；第三次只交 1 条才成功，而 R2 handout 只发给了这 1 条（`finding_ids=["CF-c8dad894"]`）。其余已修的 finding 从没进 R2，审计写 `0/9 findings closed`。
- TEST 里改了一行 CI 清单（`.txt`），run 被退回 REVIEW；增量审查没有派出任何专家，但 agent 还是要走一遍 review-start、两次 stage-complete，再重交 TEST 报告。
- 落地时 main 被别的会话推进了两次，每次都要在同步后的 tree 上重跑测试；第 3 次落地才成功。LG-1 失败第 3 次时 run 会被判为不落地。
- worktree 门禁把一个往 `/tmp` 写 JSON 的 python heredoc 拦了，只因为 JSON 里有 `backend/tests/...` 这样的字符串。
- Speed 和 Token 都显示 `WARN · no baseline`；Token 记了 1.03 亿输入，绝大部分是缓存读。

审计是人决定要不要 push 的依据。它在这两次 run 上的 Quality 结论都是错的，而人从 REPORT 里看不出来。这是本文要先解决的；落地摩擦和度量放在后面（§5、§6）。

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
| A8 | R2 的名单只取**这一次** `review-address` 里的 finding（`orchestrator.r2_roster(set(handoff["addressed"]))`）。之前那次失败时已写进日志的 address，对应的 finding 不会进任何 R2 handout，于是按"R2 沉默"被改回 open（`run_85e81059` 的 0/9） | `engine.py:1162` |

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
| Gate-0 提出的任意类型（2026-10-29 加） | 无：REVIEW 不处理，`review-address` 点名会被拒 | — | — | MEDIUM / LOW：之后有一轮 Gate-0 判 SUPPORTED / SUPPORTED-WITH-CHANGES → `closed_gate0`。HIGH / CRITICAL，以及通过那一轮自己的 finding：只能人工 `accept-finding`（后一轮只读需求、没读到前一轮的 finding，判不了 HIGH） |

**"关闭"的口径**（审计、`findings` 命令、`next` 同一个函数）：
- 关闭：`closed_asserted`、`refuted`、`accepted`、`closed_gate0`、R2 判 ADEQUATE 之后的 `closed_proven`，以及 MEDIUM/LOW 的 `closed_unproven`。
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
7. **R2 名单从账本算，不从这一次调用算（A8）。** R2 的目标是"最近一次 address 之后还没有被任何 R2 handout 覆盖的 finding"，从事件里算（和 `findings.fold` 同一份账）；每条的 hunk、proof 从它自己那次 address 事件读，不从这次调用读（今天 `r2_input` 用的是这次的 `handoff["hunks"]`，`engine.py:1167-1169`）。第 1 点让半写不再发生，这一条让"哪些 finding 要 R2"不依赖调用怎么分批。旧 run 里已有没进 R2 的 address 时，agent 不一定还会再调 address，所以：`review-address` 接受空的 items（只派 R2），`next` 在有未覆盖的 address 时让 agent 这样调一次；REVIEW 完成的检查在还有未覆盖 address 时不通过，并列出它们。

按新规则，本次 run 会走成：2 条 `closed_proven` 等 R2，第一次答 `RESOLVED` → `UNANSWERED` → 重派一次答 `ADEQUATE` → 关闭；absence 那条用带 `tests_changed` 的 `reported_test` 关闭；docs、security_policy 两条 R2 判 RESOLVED，文件在那之后没再改 → `diff_assertion` 关闭 → `5/5 findings closed`。（这是推演；本次 run 的日志里没有这些事件，测试要按 §8 第 4 步造一个续跑场景，不能直接重放。）

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

## 5. 问题 D：run 的环境和落地摩擦

### 5.1 现状（对照过代码和 `run_85e81059` 的日志）

| # | 现象 | 代码 |
|---|---|---|
| D1 | 两个"这次改动审过没有"的判断规则不同：TEST 完成时的退回、LG-1 用 patch id（任何改动都算没审，`checks.py:341-352`，`engine.py:874-876,1879-1891`）；增量审查和 RV-4 用"上次审查的 tree 到现在、去掉 `.md/.txt/.rst` 后的文件"（`engine.py:55,855-869`，`checks.py:406-414`）。于是只改了文档类文件也会退回 REVIEW，而 REVIEW 里没有东西可审 | 同左 |
| D2 | 落地时 main 动了：同步后 tree 变了，LG-1 要求在新 tree 上有 TEST 报告；LG-1/LG-4 第 3 次失败就判为不落地（`final_stale`），不分是 run 自己的问题还是 main 在动 | `lifecycle.py:307-310`，`engine.py:2689-2706` |
| D3 | worktree 门禁把 interpreter heredoc 里**每一个**像路径的字符串都当作"可能写入"的目标，命中 main checkout 就拒绝（解析器分不清数据和路径）。拒绝信息不说是哪个字符串、也不说怎么绕过，agent 只能猜 | `file_change_classifier.py:190-234`，`worktree_write_gate.py:474-496` |
| D4 | `main_dirty_scan` 按 `[status, size, mtime]` 判断"新变化"；别的会话一直在改的同一个文件，每次 mtime 变都会被完整地再报一次 | `main_dirty_scan.py:52-57,150-163` |
| D5 | worktree 里跑的可能是 main 的代码：可编辑安装（这里是 `backend/.venv` 里的 `_editable_impl_swarmai_backend.pth`，指向 main checkout 的 `backend`）让脚本方式运行时 `import core` 拿到 main 的模块。pipeline 不跑测试，也没有跨语言的办法发现这一点 | repo 的环境，不是 pipeline 代码 |
| D6 | 两个 session 都把 CLI 的 stderr 合进了 stdout（`> f 2>&1`），stderr 上的一行警告让 JSON 解析失败。stdout 本身只有 JSON（`cli_main.py:117`）。说明里没写"不要合并 stderr" | `INSTRUCTIONS.md` |
| D7 | goal profile 删掉后，"大任务拆成几个 run"只有 `INSTRUCTIONS.md:37` 半句话，EVALUATE 的说明里没有 | `stages/evaluate.md` |

### 5.2 设计

1. **TEST 里只改了文档类文件：引擎自己完成那次增量审查（D1）。** 判断"要不要退回 REVIEW"改用增量审查同一份事实 `unreviewed`（`engine.py:855-869`，去掉 `_DOC_SUFFIXES` 后的文件）：
   - 不为空 → 照旧退回 REVIEW，agent 跑 review-start。
   - 为空（只改了 `.md/.txt/.rst`）→ 在同一次调用里写 `review_reopen`，再用已有的 `complete_stage` 完成 REVIEW（检查不变，RV-4 此时通过），记下新的 patch id，回到 TEST。返回值告诉 agent：tree 变了，在新 tree 上重交 TEST 报告（TS-1 不变）。`review_reopens` 照常计数。
   
   LG-1 不改，仍按 patch id 判断，所以 DELIVER 里改文档照旧挡落地。这一步只省掉"没有东西可审却要走一遍"的那几条命令，不扩大不审的范围。审计和 REPORT 列出"上次审查之后改过、按文档类后缀没送审的文件"（照实写）。`_DOC_SUFFIXES` 也盖住了 `requirements.txt`、`CMakeLists.txt` 这类会改变行为的文件，这是今天就有的问题，本文不改它的范围，记为另一个 ToDo。
   
   同一个判断用于 `stale_closures`（§2.3 第 5 点）：上次证明之后 tree 的变化只在文档类文件里时，代码类 finding 的关闭不失效；docs 类用 `diff_assertion` 关闭的 finding，看它自己的文件有没有再改。否则一次只改文档的 TEST 修改会让已关闭的 HIGH 变成"未关闭"，又回到本文要修的那类错误结论。
2. **main 在动，不耗 run 自己的落地次数（D2）。** 今天落地上限就是 land 的 attempt 序号（`lifecycle.py:308` 的 `number >= 3`），没有单独的计数器。改法：
   - 一次落地尝试满足以下全部条件时，LG-1 的 detail 写成 `base_moved: …`（和 LG-4 的 `merge_conflict:` 前缀同一种做法）：这次同步把至少一个仓库的 base 往前移了；同步前后 run 自己的 patch id 不变；没有合并冲突，其他守卫都通过；每个仓库最新 TEST 报告的 tree 等于同步前的 tree（同步前报告是新的，失败只因为同步）。
   - fold 只把**不带**这个前缀的 LG-1/LG-4 失败计入 3 次上限；带前缀的另计，上限 `BASE_MOVED_CAP = 10`（lifecycle 里一个常量），到了就以新原因 `base_kept_moving` 结束（不落地），`REASON_CAPS` 里映射到新的 cap 名 `base_moved`（`CAPS` 加这个名字）。这个原因进 `QUALITY_BLOCK_REASONS`，和其他不落地原因一样 Quality 为 BLOCK，REPORT 写明"main 一直在动"。
   - detail 里记下同步前的 base（同步会覆盖记录），`next` 和这次返回据此列出：上一份 TEST 报告的命令原文和 cwd；新进来的 main commit 里改到了这次 run 改过的文件的那些。后者只是信息，不放行任何东西。
3. **heredoc 拒绝时说清楚怎么过（D3）。** 拒绝照旧：heredoc 候选命中 main checkout 或任何 run 的 worktree 都拒绝。改的是拒绝信息：写出哪几个字符串被当成了可能的写入目标、为什么（这段 heredoc 交给了一个解释器，正文里有写入调用），以及两条通路：把脚本写进文件再运行（`python3 /tmp/x.py` 不经过 heredoc 解析）；或者先 `cd` 到 checkout 之外，让相对路径不再落进 checkout。不放松的原因：`main_dirty_scan` 只扫本会话 run 的 main checkout（`main_dirty_scan.py:134-145`），不扫别的 run 的 worktree，不报被 ignore 的文件，扫描忙时会跳过，所以它兜不住被放行的那部分写入。
4. **同一个文件不重复报（D4）。** 在 `main_dirty_scan` 的状态文件里另存一份"已报过的路径"（今天的 seen 只是每次整份替换的快照，`main_dirty_scan.py:153-155`；worktree 创建时记下的 baseline 路径算作已报过，因为它们在创建时已经报给 agent）。已报过的路径之后再变化，只在报告末尾汇成一行，写出文件名（"仍在变化、之前报过：a, b"）；新路径照旧完整报告。
5. **环境写进说明，不做机制（D5、D6、D7）。** 改三处说明，都是对所有仓库成立的话，不按语言分支：
   - `stages/build.md`、`stages/test.md`：依赖如果以可编辑方式装在 main checkout（例如 Python 的 editable install、npm/yarn link），在 worktree 里运行可能用到 main 的代码；从 worktree 的源码目录启动测试，或在 worktree 里装一份环境，并在报告的 `cwd` 写明。
   - `INSTRUCTIONS.md`：每条 `pipeline-v1` 命令的 stdout 是一个 JSON 对象，日志在 stderr，不要合并（不要 `2>&1`）。
   - `stages/evaluate.md`：需求里有几块能各自提交、各自验证的部分时，每块一个 run，按顺序做；不要在一个 run 里做完再拆。
   
   SwarmAI 自己的 `.pth` 是这个仓库的事，按 P7 写进它的 AGENTS.md，不写进产品。

## 6. 问题 E：Speed 和 Token 的度量

### 6.1 现状

- **基线从来没生效过**：create 时从 `core/run_protocol/baselines.json` 读每个 profile 的参考线（`engine.py:153-166,474`），但打包脚本没有把这个文件放进 daemon（`desktop/scripts/build-backend.sh:210-260` 的 `datas` 里没有它），装好的 daemon 读不到，本机 7 个 v1 run 的 `contract.json` 里 `budget_seconds`、`budget_tokens` 全是 null。即使打包了，文件里的 `budget_tokens` 也全是 null，`budget_seconds` 是旧 run 的总时长（created→updated），不是 active 时间。
- **这份文件本身违反 P7**：它是 `scripts/pipeline_baseline.py` 从这台机器的 run 算出来的，却随产品发给每个用户。
- **没有基线时给 WARN**：Speed "预算内 → PASS，否则 WARN"（`audit.py:335-337`），没有预算就落在 WARN；每个 run 都 WARN，这一格就没有信息。
- **Token 数没法跨 run 比**：`usage.measure` 把宿主会话在 run 时间窗内的所有回复加起来，输入里包含缓存读（`usage.py:31-39`）。宿主会话在 run 开始前已经有多长的上下文，每次调用就要读多少缓存，所以同样的工作在不同会话里差几倍。时间窗内宿主会话做的别的事也会被算进来。

### 6.2 设计

1. **基线按安装算，不随产品发。** create 时从本机审计区（`state/pipeline_agent_audit/runs/<id>/` 的 `contract.json` 和 `events.jsonl`）取同 profile、已落地的最近 20 个 run，至少 5 个才给参考线：Speed 用审计自己的函数从事件重算 active 秒数，取 p95；Token 取下面第 3 点那个数的 p95，只用记了 `cache_read_tokens` 的 run。参考线和用到的 run 数一起写进 contract（`baseline_runs`）。按目录修改时间只看最近 200 个 run 的 contract，读取量不随历史增长。删掉 `baselines.json` 和 `scripts/pipeline_baseline.py`。参考线仍然只是参考，不是上限（STEERING #2）。
2. **没有基线就是 N/A，排在其他判断之后。** 顺序：cap 被触发 → BLOCK；Token 一次都没测到 → BLOCK；时间不完整或只测到部分调用 → WARN（照旧）；以上都不是、但没有参考线 → `N/A`，文字写"no baseline yet (n/5 runs)"；有参考线 → PASS/WARN。Swarm Out 仪表板的汇总句（`desktop/src/components/swarm-out/judge.ts`）在 N/A 时不能写"在时间和 Token 警戒线内"，一起改。
3. **Token 用不含缓存读的数比较。** 两个写 Token 的地方（`usage.measure` 和专家答复的 `hook_bridge.record_usage`，`usage.py:35-40`、`hook_bridge.py:320-326`）今天都把缓存读并进了 `input_tokens`，没有单独记。新增一个 `metric`（`cache_read_tokens`，同一种事件），参考线和 verdict 用 `input + cache_creation + output`，缓存读单独列出。文字写明"host-session responses in the run window"。`transcript_usage.py` 头注释说 `measure` 已经不含缓存读，这是错的，同一个 commit 改掉。

## 7. 四个问题（two-ends §0.1）

| 设计 | 在前端或后端？ | 规定了怎么写、怎么测吗？ | 用了原生 / 最轻的吗？ | 判定是确定性的吗？ |
|---|---|---|---|---|
| A 关闭表 | 后端（审查） | 没有 | 复用 `append_batch`、R2 handout、完整性事件；只加两个可选字段（handout 的 `allowed_status`、答复的 `n_status_ignored`），不加事件类型 | 是：类型、状态、文件内容、命令文本 |
| B `findings` / `next` / 拒绝信息 | 后端（审查、报告） | 没有 | 只读命令，信息由表生成 | 不涉及判定 |
| C not_run 照实写 | 后端（报告） | 没有 | 只改 REPORT 和 Quality 的文字与 verdict | 是：报告里有没有 `not_run` |
| D1 只改文档时引擎完成增量审查 | 后端（审查） | 没有 | 复用 `review_reopen`、`complete_stage`、`unreviewed`；LG-1 不变 | 是：文件路径后缀 |
| D2 `base_moved` | 后端（落地） | 没有 | check detail 前缀（LG-4 已有先例）、一个常量、一个原因 | 是：同步前后的 base、patch id、报告的 tree |
| D3、D4 门禁和提示 | 会话层的门禁 | 没有 | 只改信息和提示的去重，不少拒绝 | 是：拒绝条件不变 |
| D5–D7 说明 | 前端（agent 的说明） | 只写对所有仓库成立的事实 | 不加机制 | 不涉及判定 |
| E 度量 | 后端（报告） | 没有 | 删一个文件和一个脚本 | 是：本机已完成 run 的记录 |

## 8. 实施步骤和验证计划

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
| 9 | R2 名单从账本算（§2.3 第 7 点）；空 items 的 `review-address` 只派 R2；REVIEW 完成在有未覆盖 address 时不通过 | 旧日志里有一批没进 R2 的 address：空 items 调用派出的 R2 包含它们，且 R2 输入里是它们自己的 hunk；分两次 address：两次的 finding 都进 R2；有未覆盖 address 时 REVIEW 不能完成 | 改回只取这次调用的 finding；hunk 改回取这次调用的；去掉 REVIEW 完成的检查 | correctness、state-machine |
| 10 | TEST 只改文档类文件时引擎完成增量审查；`stale_closures` 用同一判断；审计列出没送审的文档类文件 | TEST 只改 `.txt`：一次调用回到 TEST，新 patch id 已记录，TEST 仍要重交报告，LG-1 通过；改了代码文件：照旧退回；DELIVER 里改 `.md`：LG-1 照旧失败；只改文档后，已关闭的 HIGH（代码类）仍算关闭，docs 类 finding 自己的文件改了则失效 | 判断改回只看 patch id；`stale_closures` 不用新判断 | correctness、state-machine |
| 11 | `base_moved:` 前缀、`BASE_MOVED_CAP`、`base_kept_moving`；返回和 `next` 列出上次 TEST 命令与重叠文件 | main 连动 3 次（patch 不变、报告在同步前是新的）：不判 `final_stale`，第 4 次落地成功；run 自己改了代码后 LG-1 失败 3 次：照旧 `final_stale`；同一次尝试里有合并冲突：不带前缀，计入 3 次；报告在同步前就已过期：不带前缀；到 10 次：`base_kept_moving`、Quality BLOCK、cap 名 `base_moved` | 带前缀的失败也计入 3 次；去掉"报告在同步前是新的"条件；去掉 10 次上限 | correctness、state-machine |
| 12 | heredoc 拒绝信息写出候选和两条通路；`main_dirty_scan` 存"已报过"并汇总 | 拒绝信息含被当成目标的字符串和两条通路；照通路改写后的命令被放行；同一路径第二次变化只在汇总行且写出文件名；新路径完整报告 | 拒绝信息去掉候选；去掉"已报过"集合 | correctness |
| 13 | 基线按本机已落地 run 算；判断顺序与 N/A；`cache_read_tokens` 指标；删 `baselines.json` 和脚本；改 `transcript_usage` 注释；仪表板汇总句 | 4 个已落地 run → N/A；5 个 → 有参考线，`baseline_runs`=5；只测到部分调用且无基线 → 仍是 WARN；一次没测到 → BLOCK；缓存读不影响 verdict；cap 触发仍 BLOCK；N/A 时汇总句不说"在警戒线内" | 无基线改回 WARN；N/A 排到 WARN 之前；Token 改回含缓存读 | correctness |
| 14 | 说明：build/test（可编辑安装）、INSTRUCTIONS（stdout）、evaluate（拆 run） | `scripts/lint_skills.py` 通过 | — | — |

**旧 run**：新字段都是可选的。旧日志里没有 `allowed_status` 的 R2 handout，按答复时 finding 的状态算出允许的词（用错词同样记 `UNANSWERED` 或按 `TEST_INADEQUATE` 处理，只会更严）；没有 `n_status_ignored` 的答复按今天的完整性判定；已在日志里的 `accept_finding` 用旧的 hash 规则校验，新规则只用于新请求。第 3、6 步各加一条"旧日志照样能 fold"的测试。

**最终验证（整套做完后跑一次，在这里定好，不在最后扩大）**：
- 本文新增的测试文件；
- `test_run_protocol_findings`、`_audit`、`_checks`、`_events`、`_landing`、`_git_tree`、`_review`、`_lifecycle`（存在的那些），`test_pipeline_v1_review_tree`、`_human_loop`、`_direct_e2e`、`_knowledge_report`、`_untracked_report`、`_follow_next`，`test_reflect_in_cli`、`test_no_push_verdict`、`test_report_language`；
- worktree 门禁和 `main_dirty_scan` 自己的测试文件（第 12 步）；`scripts/lint_skills.py`（第 14 步）；
- 第 4 步的端到端续跑。

**必须成立的不变量**：
1. 被拒的 `review-address`（任何一步失败）不改变事件日志。
2. 一条 HIGH/CRITICAL 只有在关闭口径下关闭后才不挡 RV-2；用错状态词、R2 不答都不会让它被算作关闭。
3. 没有任何路径把 `closed_proven` 改回 `open`：R2 沉默时它保持 `closed_proven`、`r2_status` 为空（等 R2），由 `push_effect` 挡；只有 `TEST_INADEQUATE`（含按它处理的 `OPEN`）让它变 `invalid_test`。（实施时改：r4 的第 3 点写的是"沉默规则对真正没收到状态行的 finding 照旧生效"，那会让每条刚证明的 finding 在等 R2 时显示成 `open`，与本条矛盾；按本条实施。）
4. `findings`、`next`、审计、R2 handout 对同一条 finding 给出的"关没关、能怎么关"相同（同一个函数）。
5. 最新 TEST 报告里有 `not_run` 时，审计 Quality 不是 PASS，REPORT 列出每条命令和理由。
6. REVIEW 完成时，每条有 address 的 finding 都已进过某一次 R2 handout，与 address 怎么分批无关。
7. TEST 退回、增量审查、`stale_closures` 对"上次审查之后有没有要送审的改动"用同一个函数；LG-1 仍按 patch id。
8. 落地的 tree 上一定有通过的 TEST 报告，且它的 patch id 是某次 REVIEW 完成时记下的（LG-1 不放松）；main 移动只换计数器，不放行，且计数有上限。
9. worktree 门禁的拒绝条件不变。

## 9. 不做的

- 不让 pipeline 自己跑测试或在 base 上重跑（推翻 r7，需另议）；因此不做 `red_at_base`（§4.2）。
- 不把 `RESOLVED` 映射成 `ADEQUATE`：两个词判断的东西不同（修没修好 / 测试够不够），映射等于替专家放行。（`OPEN` → `TEST_INADEQUATE` 只会更严，所以做。）
- 不加第三轮审查；R2 重派每位专家最多一次。
- 不新增事件类型；不按语言、框架、测试运行器解析测试名或结果。
- 落地不放松 LG-1：不做"main 新进来的 commit 和这次改动没有文件交集就沿用旧 TEST 报告"。不重叠的改动照样能让测试变红，落地的组合就没被验证过；main 动得比测试快的仓库需要的是合并队列，那是 CI 的事。
- pipeline 不在同步后自己重跑测试（§0）。
- 不按需求条数或改动行数提示拆 run：这些数字只是代理指标（P6），说明里写判断标准。
- 不检测可编辑安装：没有不按语言分支的办法（P7）。
- 不放松 heredoc 候选的拒绝：事后检出兜不住（§5.2 第 3 点）。
- 不在本文改 `_DOC_SUFFIXES` 的范围（另记 ToDo）。

## 10. XG 的决定（2026-10-27）

1. absence 用 `reported_test` 关闭，要求报告里有改动过的测试（§2.3 表）；不走人工 accept。原话："我们要 Autonomous，不要人介入"。
2. R2 用错词时重派一次，算在 R2 内、不算第三轮（§2.3 第 3 点）：同意。
3. C 只做"照实写、`not_run` 时 Quality 不为 PASS"，不做 base 上的证明机制（§4）：同意。
4. 第二个 run 的问题并进本文（§5、§6）；知识召回不在本文，另一个 session 在做。

## 修订记录

- r1（2026-10-26）：初稿。
- r2（2026-10-26）：零上下文审查一轮（3 HIGH、7 MEDIUM、6 LOW），按原因一次并入：
  - 保留"R2 沉默就改回 open"的保护作用，改由 `push_effect` 挡未经 ADEQUATE 的 HIGH `closed_proven`；`OPEN` 按 `TEST_INADEQUATE` 收紧，只有 `RESOLVED` 记 `UNANSWERED`（HIGH 1）；
  - 重派改成新的 R2 handout，`conflict_id` 带答复 id（HIGH 3）；`n_status_ignored`（MEDIUM 4）；address、proof、reclassify、R2 handout 同一 batch（MEDIUM 5）；
  - `diff_assertion` 的条件收紧为"R2 之后未改且相对 R1 改过"，R2 附 git diff，按 handout `code_trees` 解析仓库（MEDIUM 8）；`allowed_status` 存进 handout（LOW 12）；
  - `closed_unproven` 只在 MEDIUM/LOW 时算关闭（MEDIUM 7）；`stale_closures` 加两种状态（LOW 13）；`accept-finding` 两侧 hash 一致（MEDIUM 10）；absence 要求 `tests_changed`（LOW 11）；
  - C 的 baseline 机制撤掉，改为照实写（HIGH 2、MEDIUM 6、LOW 14、15）；
  - §2.2 根因改写（LOW 16）；当时 §6（今 §8）的"重放本次 run"改成续跑场景测试（MEDIUM 9），补上审查列出的缺失测试。
- r3（2026-10-27）：当时 §8（今 §10）三条由 XG 决定，全部按 r2 的写法实施；补上 absence 检查需要的 `proof.tests_changed` 字段。
- r4（2026-10-27）：并入 `run_85e81059` 的问题：A8（R2 名单从账本算）；§5 问题 D（只改文档时引擎完成增量审查、`base_moved` 计数、heredoc 拒绝信息写清通路、重复提示合并、三处说明）；§6 问题 E（基线按安装算，修好从未打包的 `baselines.json`；无基线为 N/A；Token 不含缓存读）。核实后不改的：stdout 本来只有 JSON（两次都是调用方合并了 stderr）；知识召回由另一个 session 处理。
  - 对新增部分做了一轮零上下文审查（2 HIGH、6 MEDIUM、4 LOW），按原因一次并入：D3 不再放松拒绝，改为说清候选和通路（HIGH 1，`main_dirty_scan` 不扫别的 run 的 worktree、不报 ignore 文件、扫描会跳过，MEDIUM 8）；D1 不再动 LG-1，改为"只改文档时引擎自己完成增量审查"，`stale_closures` 用同一判断，审计列出没送审的文档类文件（HIGH 2、MEDIUM 3、4）；D2 写清 fold 怎么计数（detail 前缀、单独常量、新原因和 cap 名、Quality BLOCK、"报告在同步前是新的"条件、记下同步前的 base）（MEDIUM 5）；Token 新增 `cache_read_tokens` 指标，改错误的注释（MEDIUM 6）；N/A 排在 BLOCK 和不完整 WARN 之后，仪表板汇总句一起改（MEDIUM 7）；A8 补上空 items 派 R2 和 REVIEW 完成检查，hunk 取自各自的 address（LOW 9）；基线的数据源、"已落地"、`baseline_runs`、读取上限（LOW 10）；§6.1 措辞（LOW 11）；§8 补上审查列出的测试和变异（LOW 12）。
- r5（2026-10-27，实施后）：按 §8 实施完 14 步，与本文的出入如下（均已在代码和测试里）：
  - 不变量 3 按上面改后的写法实施（R2 沉默不把 `closed_proven` 改回 `open`）；旧 handout 的判定按上面改后的写法。
  - R2 账本（`review.r2_ledger`）是 `review-address`、`next`、RV-5 共用的一个函数：同一位专家写新 R2 handout 时带上它没答复的那份里的 finding；被新 handout 取代、永远不会有答复的那份里的 finding 算"未覆盖"；`UNANSWERED` 按每条 finding 判断能否重派（不再按专家只看最新答复）；已 accept 或 refute 的 finding 不再送 R2；`review-address` 在读完日志后日志又变了时拒绝写入。（第 1–3、9 步专家审查的发现。）
  - 第 5 步：同一个文件既有代码修改又有文档修改时，docs 类 finding 按文件判断永远关不掉；改为这种情况以 `closed_unproven` 关闭（MEDIUM/LOW 算关闭，HIGH 仍挡）。proof 事件记 `basis: "file"`。
  - 第 6 步：`stale_closures` 不按 patch id，而按 proof 判断过的文件的 blob（diff_assertion 看 finding 的文件，refuted 看每个 path 引用）判断；`closed_proven` 不放进来：它的证据是通过的测试，落地 tree 上的 TEST 报告（LG-1）会重新确立，之后的代码改动走增量审查（RV-4），而且 `closed_proven` 无法再证明，放进来只会让它永久挡住 RV-2。
  - 第 10 步：引擎自己完成 REVIEW 后，用同一次调用里已交的 TEST 报告继续完成 TEST（报告本来就在当前 tree 上），不再要求重交。
  - 第 7 步的命令是 `pipeline-v1 findings`；`next` 在 REVIEW 里的 `findings` 字段与它同源（`review.closure_view`）。
  - 顺带修了 `scripts/mutation_check.py`：按 AGENT 的写法用 `perl -e 'alarm N; exec …'` 限时，它收到 SIGALRM 时不恢复被改的源文件；现在 SIGALRM、SIGHUP 和 SIGTERM 一样恢复。
