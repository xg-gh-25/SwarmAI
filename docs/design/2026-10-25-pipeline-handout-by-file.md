---
title: Pipeline v1：handout 改为传文件，绑定失败要说出来
created: 2026-10-25
updated: 2026-10-26
project: SwarmAI
status: r2 — XG 2026-10-25 确认，已实现
parent: docs/design/2026-10-25-pipeline-standalone.md
---
# Pipeline v1：handout 改为传文件，绑定失败要说出来

日期：2026-10-25 · 状态：已确认，已实现（r2；审查修订见 §7） · 落地方式：原生（不走 pipeline）

来源：用 pipeline 跑 `run_003e9c64`（channel 会话的 STEERING 只注入标记过的规则）时，
REVIEW 派不出专家。本文档只处理那次暴露的 pipeline 问题；`run_003e9c64` 的业务改动另算。

## 1. 问题（都在本次 run 里观察到）

| # | 现象 | 原因（代码位置） |
|---|---|---|
| H1 | Gate-0 第一轮专家答完了，run 里没有 `spawn` 也没有 `answer`，`stage-complete` 只回 `{"gate0": "awaiting_answer"}` | `hook_bridge.bind_spawn`（hook_bridge.py:94-111）要求 spawn 的 prompt 和 handout 的 `prompt_sha` 完全相等；我派发时丢了结尾一个换行。没绑上 spawn，`_record`（hook_bridge.py:137-140）就不记答案。两处都直接 `return None`，什么都不写 |
| H2 | REVIEW 的两份 handout 是 39,092 和 33,447 字符，带 6 行只有一个空格的 diff 行，主 agent 要把它们逐字节写进 Agent 工具的 `prompt` | `review.build_prompt`（review.py:50-57）把说明、diff、输出约定全部内联；`ReviewOrchestrator.handout`（review.py:205-241）把整段作为派发用的 prompt 返回。让模型逐字复刻几万字符本身就不可靠；H1 说明一个字符的偏差就会让答案静默丢失 |
| H3 | `pipeline-expert` 的系统提示说"你的第一条消息给出一个 prompt 文件：读它并照做" | `pipeline_expert_agent._SYSTEM_PROMPT`（pipeline_expert_agent.py:46-53）是按传文件写的，v1 的 handout 却是内联全文 |
| H4 | Gate-0 prompt 开头是 `The understanding is: <claim>. Work type: <work_type>.`，还有 `matching <evidence_kind>` | `data/gate-prompts/gate0-skeptic.md` 第 1、3 行是模板占位符，`engine.gate0_start`（engine.py:1041-1048）原样读进来，没有替换 |
| H5 | （审查发现）答案的绑定只看"这个 handout 有没有 spawn"，不看记答案的这个子 agent 是不是被那条 spawn 派出来的 | `_record` 用 transcript 开头的 `PIPELINE-HANDOUT` 行找 handout，再用 `_spawned` 取该 handout 的任意一个 `spawn`（hook_bridge.py:87-91、137-140）。主 agent 先正常派一个，再派第二个带同一绑定行、内容改过的子 agent，第二个可能先答完并被记下 |

已有、可复用的机制（不新造）：

- **审计 blob。** handout 的全文已经存成审计 blob，文件名就是它的 sha256，等于 handout 事件里的
  `prompt_sha`（store.py:239-243 写入时校验）。目录 `state/pipeline_agent_audit` 在 SwarmAI 会话里有
  `audit_marker_write_gate` 一律拒绝写、移、删，只放行读（hook_builder.py:393-399）。
- **子 agent 首条消息的哈希。** `runtime_hooks._prompt_reads`（runtime_hooks.py:1143-1222）已经算出子 agent
  第一条消息整体的 sha（`<first:N>`，带/不带结尾换行两种），也会识别 transcript 里的 `Read` 和 `cat`。
- **绑定行读取。** `subagent_transcript.read_prompt_head` 从 transcript 开头读出绑定行
  （host_hooks.py:50、runtime_hooks.py:1027-1028 调用）。

## 2. 方案比较

| 方案 | 做法 | 代价 / 风险 |
|---|---|---|
| A（推荐） | 派发用的 prompt 只有 4 行：`EXPERT-ID`、`PIPELINE-HANDOUT`、`PROMPT-FILE: <审计 blob 的绝对路径>`、一句"读这个文件并照做"。spawn 按这 4 行绑定；记答案时要求这个子 agent 的首条消息就是这 4 行，并且读过该文件 | 专家多一次 Read。读的是写保护、按内容寻址的文件，主 agent 改不了它看到的内容 |
| B | 保持内联，只放宽比较（去结尾换行、规范空白） | 修好 H1 这种情况，修不了 H2：几万字符照样会抄错；放宽比较还会让主 agent 往 prompt 里加话也能绑上 |
| C | 用 standalone §4.3 规划的 relayed 路径：主 agent 把子 agent 的回答交给 `review-answer` | 可信度降一档（答案经主 agent 转手）；spawn 时照样要复刻全文，H2 还在 |

选 A。它让 H3 的系统提示变成真话，复用审计 blob 和 `_prompt_reads` 的首条消息哈希，可信度不降：
答案仍由宿主 hook 从专家自己的 transcript 读出，主 agent 碰不到。

## 3. 设计

### 3.1 handout 的两段

`ReviewOrchestrator.handout` 产出两段：

- **全文**（不变）：`build_prompt(...)`。照旧存审计 blob（sha = `prompt_sha`），照旧写一份
  `evidence/prompts/<name>.md` 给人看（专家不再读它，所以同名覆盖的问题与绑定无关）。`lint_prompt` 照旧检查全文。
- **spawn prompt**（新，派发用）：

  ```
  EXPERT-ID: <expert_id>
  PIPELINE-HANDOUT: <run_id> <nonce>
  PROMPT-FILE: <realpath(store.paths.blobs / prompt_sha)>
  Read PROMPT-FILE from the top and follow it exactly. If the Read output is cut short, keep reading with offset until the end. Your report is your final message.
  ```

  spawn prompt 全文存进 handout 事件的新可选字段 `spawn_prompt`（几百字节，不走 blob），sha 存进
  `spawn_sha`。两个字段都加到 events.py:91 的 handout 可选字段里。

`handout()` 的返回值和 CLI 输出里，`prompt` 改为 spawn prompt；`path` 改为 PROMPT-FILE；`sha` 仍是
`prompt_sha`。全文不再出现在 CLI 输出里，免得主 agent 又去复制全文。

全文的头两行不变（`EXPERT-ID`、`PIPELINE-HANDOUT`）。

### 3.2 绑定 spawn

`bind_spawn(store, prompt, tool_use_id)`：

- handout 带 `spawn_sha`：比较 `sha256(prompt.rstrip("\n") + "\n")` 和 `spawn_sha`。只容忍结尾换行，
  4 行之外多一个字符都绑不上。
- handout 没有 `spawn_sha`（改动前发出的 handout）：按旧规则比较全文 `prompt_sha`，同样只容忍结尾换行。
- 绑不上，且 prompt 的 `PIPELINE-HANDOUT` 行指向本 run 里一个存在、还没答的 handout 时，写一条
  `spawn_rejected`（writer `hook`），payload `{handout_event_id, tool_use_id, reason: "prompt_differs",
  got_sha, got_len}`，不存 prompt 内容。每个 handout 最多记 3 条，之后不再写（防刷）。指向不存在或已答
  的 handout 时什么都不写。

### 3.3 记答案

`_record(...)` 非 `exact` 路径，在"该 handout 有 spawn"之外再要求两条（缺一条就不记，写 `answer_rejected`）：

1. **首条消息就是 spawn prompt。** 子 agent 第一条消息整体的 sha（`<first:N>`，含结尾换行的变体）里有一个
   等于 `spawn_sha`（旧 handout：等于 `prompt_sha`）。这堵住 H5：另派的改过内容的子 agent，首条消息对不上。
   reason `spawn_prompt_differs`。
2. **读过 PROMPT-FILE。**（仅带 `spawn_sha` 的 handout）transcript 里至少有一次 `Read`，`file_path`
   按字面（规范化，不跟随 symlink）等于 PROMPT-FILE，从头读（offset 为空、0 或 1），对应的 `tool_result`
   没有 `is_error`。允许分页读。只认 Read 工具：`cat` 在 echo 字符串、注释、丢弃输出里都会误判（实现审查）。
   reason `prompt_file_not_read`。
3. **spawn 之后没人再对它说话。** 首条之后的 user 记录里出现 text（协调者 SendMessage 进来的话），
   就不是这个 handout 的审查；中断标记 `[Request interrupted…` 不算。reason `instructed_after_spawn`。
   同时 PROMPT-FILE 此刻的 sha 等于 `prompt_sha`（外部宿主没有写保护 gate，用它发现事后篡改），否则
   reason `prompt_file_changed`。

transcript 读不了时 reason `transcript_unreadable`，同样不记。

实现上，把首条消息哈希和"某个文件被读过"的扫描从 runtime_hooks.py 挪到 `core/subagent_transcript.py`
的一个函数 `spawn_evidence(transcript_path, prompt_file) -> {first_shas, read_ok} | None`，一次扫描
同时得到两样。runtime_hooks 里 `_prompt_reads` 保留（旧 marker 仍在用），改为调用同一个扫描的底层。
实现时的两处调整：(1) 判定放在 `on_subagent_stop`（两个宿主入口共用的函数）里，`record_answer` 本身不变，
它是供 hook 调用的底层函数；(2) `on_subagent_stop` 自己扫一次 transcript（`spawn_evidence`），没有复用
runtime_hooks.py:1035 已算的 `_prompt_reads`：后者只认 `evidence/prompts/` 下的文件，也不看 tool_result，
复用它要改旧 marker 的行为。代价是进程内路径多扫一次 transcript（transcript 通常几百 KB，在 hook 线程里）。

`exact=True`（Kiro 宿主交全文的路径，standalone T5 尚未实现）：维持 main 上的规则，比较全文 `prompt_sha`，
不接受 4 行 spawn prompt（接受它就绕过了上面的读取检查）。Kiro 落地时再定。

`answer_rejected` payload `{handout_event_id, tool_use_id, reason}`，每个 handout 最多记 3 条。被拒不算答案，
同一 handout 可以重新派发。

### 3.4 新事件 kind 的注册

`spawn_rejected`、`answer_rejected` 两个 kind：

- events.py 注册 schema（writer `hook`）；
- `OCCURRENCE_FIELDS`（events.py:32）加 `("payload.handout_event_id", "payload.tool_use_id", "payload.reason")`，
  同一次重试不会被当成完整性冲突；
- lifecycle.py:83-88 的 `IGNORED` 加入两者，否则回放时 lifecycle.py:674 会抛 `unhandled lifecycle event`。

它们只用于诊断，不让任何检查通过或失败。

### 3.5 让"在等什么"看得见

新函数 `review.handout_status(events, handout_event) -> {state, reason, spawn_prompt}`，state 取值
`not_spawned`、`spawn_rejected`、`spawned_no_answer`、`answer_rejected`、`answered`。两处调用：

- Gate-0：`stage-complete` 返回 `awaiting_answer` 时，附上每个未答 handout 的状态；
- REVIEW：缺答案时 `next.blocking` 用同一函数给出逐 handout 状态。

状态里带 `spawn_prompt`（从 handout 事件读），主 agent 被拒后可以原样重派。review-start 遇到未答的 handout
会跳过（engine.py:2090-2091），所以不能靠再调一次 review-start 拿回 spawn prompt。

### 3.6 Gate-0 模板

改 `gate0-skeptic.md` 文字，不做模板替换：第 1 行改为
`You are a skeptic. The understanding is the "understanding.claim" in the JSON below; the work type is on the "WORK TYPE:" line.`；
第 3 行去掉 `matching <evidence_kind>`；`SCOPE BUDGET` 补一句"reading PROMPT-FILE does not count"。
`engine.gate0_start` 在 `GATE-0 ATTEMPT` 行后加 `WORK TYPE: <profile>`。

不加通用的"未替换占位符" lint：专家说明里本来就有 `<victim>`、`<project_root>` 这类合法写法
（experts/code/security.md:25、experts/code/integration.md:54），review_input 里的 diff 也会有 `<div>` 等。
只用测试钉住 gate0 模板里没有那三个占位符。

### 3.7 文档

- 技能源 `backend/skills/s_autonomous-pipeline/`：INSTRUCTIONS.md 的 REVIEW 一节、stages/review.md、
  stages/evaluate.md 写明派发的是 4 行 spawn prompt，专家会去读 PROMPT-FILE；绑定或答案被拒时看输出里的
  handout 状态，用其中的 `spawn_prompt` 重派。
- `docs/design/2026-10-25-pipeline-standalone.md` §4.3 补一句 handout 以文件交给专家。
- `pipeline_expert_agent._SYSTEM_PROMPT` 改成点名 `PROMPT-FILE:` 这一行。

## 4. 不做的

- 不放宽全文比较（方案 B），不加 relayed 路径（方案 C）。
- 不证明专家读了全文的每一行：文件写保护且按内容寻址，主 agent 改不了它；专家只读一部分是质量问题，
  和专家略读代码同类，由审查结论本身体现。
- 外部宿主里"专家读完后文件被改又改回"的情况不处理（那里没有写保护 gate；`prompt_file_changed`
  只能发现没改回的篡改）。
- **已知缺口（实现审查 security HIGH，main 上已存在）：transcript 是宿主写的普通文件，没有写保护。**
  一个不带 `EXPERT-ID` 行的子 agent 不受 `expert_type_gate` 约束，可以改写自己的 transcript，让首条消息、
  读取记录都"对上"。本次的检查挡住的是失误（派错 prompt、给专家追加指示），挡不住蓄意伪造，和事件日志
  "有校验和、无签名"（S6-3）是同一个威胁模型。修法候选：SubagentStop 的 `agent_id` 和绑定 spawn 的
  `tool_use_id` 对应起来；读取证据改由子 agent 内的 PreToolUse/PostToolUse 记录；带 `PIPELINE-HANDOUT` 行
  的 spawn 一律要求 `pipeline-expert`。另起任务。
- 外部宿主没有审计目录写保护，blob 在专家读时被换、停时换回，只靠停时的 sha 发现不了（TOCTOU）。
- 首条消息被宿主拆成多个 text block 时会被拒（`spawn_prompt_differs`）。2026-10-26 抽查 406 个真实
  Claude Code 子 agent transcript，首条消息全部是单个字符串，暂不处理。
- 不改 `run_003e9c64`。它的 REVIEW handout 已按旧形式发出；本次落地后用 `abandon` 收掉，业务改动重新起一个
  run 走完，顺带验证本修复。

## 5. 测试（先写，先看到红）

| 测试 | 断言 | 位置 |
|---|---|---|
| T1 | handout 返回的 `prompt` 是 4 行，`PROMPT-FILE` 是 realpath，指向的文件 sha 等于 `prompt_sha`；事件里有 `spawn_prompt` 和 `spawn_sha` | tests/test_run_protocol_review.py |
| T2 | spawn prompt 绑定成功；少结尾换行也成功；多一句话失败并写 `spawn_rejected`；同一 handout 第 4 次被拒不再写事件 | tests/test_run_protocol_hook_bridge.py |
| T3 | 没有 `spawn_sha` 的旧 handout：全文绑定成功，少结尾换行也成功 | 同上 |
| T4 | 首条消息是 spawn prompt 且 Read 过 PROMPT-FILE：答案记下；首条消息被改（H5 场景：同 handout 第二个子 agent）：`answer_rejected: spawn_prompt_differs`，第一个随后仍能记下；没读：`prompt_file_not_read`；Read 的 tool_result 是 error：`prompt_file_not_read`；分页读（offset=1 再 offset=500）：记下 | 同上（构造 transcript jsonl） |
| T5 | 外部宿主路径 `python-backend hook subagent-stop --host claude` 对同一 transcript 结果相同 | tests/test_host_hook_binding.py |
| T6 | 新 kind 写入后 run 能正常回放（`projection()` 不抛错），重复写入不报完整性冲突 | tests/test_run_protocol_hook_bridge.py |
| T7 | Gate-0 未答时 `stage-complete` 输出带逐 handout 状态（`not_spawned`、`spawn_rejected` 两种），状态里有 `spawn_prompt` | engine 测试 |
| T8 | Gate-0 全文里没有 `<claim>`、`<work_type>`、`<evidence_kind>`，有 `WORK TYPE: bugfix` | tests/test_run_protocol_review.py |
| T9 | `pipeline-expert` 系统提示含 `PROMPT-FILE` | tests/test_pipeline_expert_agent.py |

要随之更新的现有测试：`tests/pipeline_v1_rig.py:266-272` 构造的 transcript 加上一次对 PROMPT-FILE 的 Read
和成功的 tool_result；`test_pipeline_v1_direct_e2e.py:158`、`test_pipeline_v1_real_e2e.py:570`、
`test_pipeline_v1_rehearsal.py:84` 对 `handouts[*]["prompt"]` 内容的断言改为读 PROMPT-FILE；
`tests/review_bridge_helper.py` 的 `track_tool_use` 用 spawn prompt。实施时先 grep 全部 `["prompt"]` 消费方（R27）。

实现审查后补的测试：只认 Read（`cat … > /dev/null` 被拒）、读 symlink 被拒、spawn 后收到协调者消息被拒、
中断标记不算、被拒后重派期间状态是 `spawned_no_answer` 且答案记在新 spawn 名下、exact 路径仍要全文。

每个新测试在修复撤回后要变红（变异检查）。

## 6. 风险

- **外部宿主读不到文件。** PROMPT-FILE 在数据目录 `state/pipeline_agent_audit/runs/<run>/blobs/` 下。宿主若限制
  cwd 以外的 Read，专家可以用 `cat`（已识别）。SwarmAI 会话里没有这个问题。
- **路径形式。** PROMPT-FILE 生成时取 `realpath`，比对时 transcript 里的路径也先 `expanduser` 再 `realpath`。
- **影响范围。** 所有 run 的 Gate-0 和 REVIEW 派发都走新形式；改动前已发出、未答的 handout 走 §3.2 / §3.3 的旧规则。
- **事件日志兼容。** handout 的两个新字段可选，两个新 kind 是新增并已注册到回放和去重，旧 run 不受影响。

## 7. 审查修订记录（r1 → r2）

一轮零上下文审查，11 条，全部采纳：

- 通用占位符 lint 会误伤真实专家说明和 diff → 删掉，只测 gate0 模板（§3.6）。
- 第二个子 agent 能借第一个 spawn 的绑定记下伪造答案 → 要求首条消息等于 spawn prompt（H5，§3.3 第 1 条）。
- "完整读取"只看了 Read 的入参，失败的 Read 也算；不许分页又会让大文件读不全 → 专家改读写保护、按内容寻址的
  审计 blob，读取证据看 tool_result 是否出错，允许分页（§3.1、§3.3 第 2 条、§4）。
- 新 kind 没注册到 `IGNORED` 和 `OCCURRENCE_FIELDS`，回放会抛错 → §3.4。
- 漏列的测试消费方 → §5 末段。
- 被拒后拿不回 spawn prompt → spawn prompt 存进 handout 事件，状态里返回（§3.1、§3.5）。
- 路径按字符串比较 → 两侧 realpath（§6）。
- `spawn_rejected` 可被刷 → 每个 handout 最多 3 条，已答不写（§3.2、§3.3）。
- evidence 文件同名覆盖 → 专家改读 blob，不再依赖 evidence 文件（§3.1）。
- 进程内路径扫两遍 transcript → 复用已算的证据（§3.3）。
- 若干行号过期 → 已更正。

## 8. 实现审查（correctness + security，一轮）

采纳并修复：`handout_status` 按固定优先级判状态，重派后仍显示 rejected → 按日志顺序，重派绑定后为
`spawned_no_answer`；`_spawned` 取第一条 spawn → 取最新；Gate-0 状态只看最近一次人工回答之后；`cat`
识别误判 → 只认 Read；读取路径跟随 symlink → 字面比对；spawn 后追加指示仍记答案 → `instructed_after_spawn`；
`exact=True` 接受 4 行 spawn 而绕过读取检查 → 维持 main 的全文规则；`_reject` 在锁外计数 → 放进 run 锁，
`tool_use_id` 截断到 128，接住 `EventError`；几处过期 docstring。
记为已知缺口（§4）：transcript 可被子 agent 自己改写（HIGH，main 上已存在，另起任务）；外部宿主的 TOCTOU；
多 text block 首条消息。
