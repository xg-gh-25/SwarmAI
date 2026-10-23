---
title: Pipeline v1 fixing log
date: 2026-10-22
created: 2026-10-22
updated: 2026-10-22
project: SwarmAI
status: OPEN
---

# Pipeline v1 修复记录

执行 `~/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-22-pipeline-v1-fixing-spec.md`（下称"方案"）时的新增发现、归组和偏离。每组 commit 的说明引用本文。

## 1. G 组的真实端到端测试找到的新问题

| # | 问题 | 归组 | 证明 |
|---|---|---|---|
| G-1 | BUILD 熔断（`checks_exhausted`）之后，DELIVER 上的 `land` 仍把没测过的代码提交到 main，run 以 `completed(landed)` 结束；REVIEW / TEST 根本没跑 | A2 | `test_checks_exhausted_ends_not_landed_and_never_commits` |
| G-2 | 仓库没有 `validations.json` 时，`pipeline_test.py` 直接报 `runner error`（`git show <base>:validations.json` 失败）；空的 `entries` 也被拒 | B6 | 手工复现，见下文 B6 |
| G-3 | `land` 在 `_carry_moved_finals` 里对缺 final 的 repo 抛 `KeyError`，CLI 打印 traceback（非 JSON） | A1 | 首次复现时 final 缺失 |

## 2. 第 4 路审查（方案 §8）的结论

子 agent `67d530841b4656f6` 只读审查，报 11 项（3 HIGH、8 MED）。逐条核对后：

| # | 结论 | 处理 |
|---|---|---|
| F-1 小改动可能完全不派 correctness | **不是问题**：expert 简化设计 E1 和 v13 §A1.4 规定 < 50 行、非安全风险的改动不派审查者；RV-1a 在没有 handout 时通过与之一致 | 不改 |
| F-2 `answer` 的发生键是 `handout_event_id`，设计 §A3.1 要求 `tool_use_id` | 属实。之前为防"一个 handout 记两次答案"改成了 handout；正确做法是发生键回到 `tool_use_id`，唯一性由 bridge 在锁内按 handout 检查 | 并入 C 组 |
| F-3 partial / unparsed 答案写出的 `finding_status` / `finding_occ` 让 `findings.fold` 抛错，run 读不出来 | 属实，属于"run 卡死"类 | 并入 A 组 |
| F-4 handout 没把 prompt 全文存进审计区 blob | 属实 | 并入 D 组 |
| F-5 Gate-0 第一行之后还能有第二个 VERDICT | 属实 | 并入 B 组 |
| F-6 "无输出只重派一次"没有执行路径 | 属实 | 并入 B 组 |
| F-7 答案复用（§C6.2）只有孤立 helper，RV 检查不认 `answer_reuse` | 属实 | 并入 B 组 |
| F-8 `.py/.ts/.js` 的纯注释改动被当成可执行，docs finding 关不掉 | 属实 | 并入 B 组 |
| F-9 ±10 行聚类不是强制事实，`review-address` 接受任意 items | 属实 | 并入 B 组 |
| F-10 FIXING_SPEC 不是由事件生成，结构不符 §B6 | 属实 | 并入 B 组 |
| F-11 REVIEW.md 缺审计字段；来源记录缺规则版本 | 属实 | REVIEW.md 并入 D5，来源记录并入 B7 |

## 3. A 组（run 卡死）

**根因和统一修法：** 事件写进日志之前没有人检查它是否合法，失败路径又会留下没关闭的 stage_attempt，两者都会让 `fold` 读不出 run。修法是两条规则：

1. **非法的事件写不进去。** `AuditStore` 在锁内先用 lifecycle（以及 finding 事件的 finding ledger）把"现有日志 + 新事件"折叠一遍，折叠失败就拒绝写入；事件触发的 `transition` 也在同一把锁里追加，别的写入者插不进中间。所有 v1 写入者都通过 `engine._store` 拿到这个 store。
2. **每个尝试都以一组完整的 check 结果关闭。** `complete_stage` 和 `normal_land` 打开尝试后，任何一个 check 没有求值（文档被 schema 拒绝、前面的守卫已失败、中途抛错）都记成 `not evaluated: <原因>` 的失败；lifecycle 分类失败原因时不看这些。

| 条目 | 处理 |
|---|---|
| A1 | `normal_land` 先打开尝试再做 carry；carry 失败记成 LG-4（rebase 冲突记 `merge_conflict:` 前缀）；任何异常前把剩余守卫记成 not evaluated。lifecycle 按 §A3.7 分类：LG-3/DV-1 → `report_mismatch`；LG-1/LG-4 可重试，第 3 次 → `land_conflict` / `final_stale`；LG-2 → `tests_red` / `tests_incomplete`；LG-5 → `validation_failed`。commit 本身失败（守卫通过后 main 又动了）时返回 `retry`，下一次 land 的尝试取代它；空的 landing-progress 文件会被清掉，否则下一次的新计划会被拒。缺 final 不再 `KeyError`（G-3）。`landing_bases` 取集成分支的真实 HEAD（B3 的前半，A1 的重试依赖它） |
| A2 | 新增 `engine.finish_not_landed()`：渲染 run 目录版 REPORT、写 `render`、开一个只有 DV-1 的 DELIVER 尝试、`stage_complete` → `reflecting`。所有 not_landed 结局和 research 的 no_code 都走它；`land` 遇到已有未落地结论时直接走它，lifecycle 也拒绝这种 run 的 `land landed`（G-1）。reason 由 `_set_pending` 只写一次 |
| A3 | 尝试编号只由 lifecycle 计算（engine `_next_attempt`）。编号是事件身份的一部分，所以人回答之后不再清零（清零会让新尝试和旧尝试的 event_id 相同，被当成重复丢掉）；改为记 `attempt_base`，熔断按回答之后的次数算 |
| A4 | Gate-0 的轮次和结论只看最近一次 human_answer 之后的事件 |
| A5 | schema 失败也写一次尝试：被拒的 check 记失败并带原因，其余记 not evaluated。只有本阶段带 `stage` 效果的 check 能挡住写入，DS-1、DS-3 等只记录 |
| A6 | RF-1~4 移出落地守卫，改在 `reflect-prepare` 时作为 DELIVER 尝试求值；第 3 次不过时经验置 0、`reflect: degraded`；`degraded` 不会被 `reflect_complete` 改回 `ok` |
| A8 | DC-2 从文档读：REQUIREMENTS 的 clarification 或 DESIGN_SPEC 的 `chosen` 带 `decided_by: agent_after_cap`（设计 §A1.2 的记法），不需要新事件 |
| F-3 | finding ledger 接受 `partial` 答案的行；`unparsed` 答案不再写 finding / status |
| A7 | **留到 C 组那批**：hookless 执行器得到 `needs_hooked_session` 需要 run_owner_gate 给 `land` 发 token（hook 改动）；`infrastructure_failed` 的生产者和审计区不可写的处理一起做 |
| A9 | **问 XG**：方案说"按设计 §A1.2"让 awaiting_human 时的 session_lost 进入 paused，但 §A1.2 表里 `崩溃 / 用户停止 → paused` 只从 `active` 出发；`fc6d2ab2`（人机交互设计 `docs/design/pipeline-v1-human-loop.md`）有意让等人的 run 继续等，问题不丢。两者冲突，属于协议本身，暂不改 |

证明：`tests/test_pipeline_v1_real_e2e.py` 中 A 组的用例全部转为必须通过（熔断不落地、Gate-0 两轮否定、Gate-0 否定后人回答、失败尝试后提 decision、任意命令序列后日志可折叠）。其余用例仍依赖 B1 或 C 组。

## 4. B1、B2、B4（含 B3 的依赖映射、B5）

**根因：** runner 记下的证据和它声称证明的东西没有逐项对上。trace 从旧格式的 run.json 读、整体盖章；verdict 看的是"有没有一次调用没跑完"，不是每个测试在当前树上的结果；probe 只有校验没有执行器；依赖映射的格式两边不一致。

| 条目 | 处理 |
|---|---|
| B1 | `_v1_trace` 删除。exit 从 run 目录的 `artifacts/design_spec.json` 读（`_v1_exits`），`V1Runner.trace` 让每条 exit 只匹配自己的 key：test 匹配自己的 nodeid 在当前树上的最后结果，probe / mutation 匹配 target 等于 exit 行 id 的调用。从没跑过的 exit 不出现在 trace 里，DS-9b / BX-1 / TS-2 按缺失判失败 |
| B2 | `V1Runner.node_results()` 按调用顺序读当前树上每次调用的封印回执，同一个 node 后面的结果覆盖前面的；没回执的调用记 `call:<seq>` 为 incomplete，直到后面有回执的调用；被预算跳过的文件记 `<file>::*` 为 incomplete，直到后面跑了它。`build_repo_final(node_results=...)` 按 node 求 verdict。基线分类为 `unknown` 的失败算 incomplete，不算 red |
| B3（部分） | 新增 `carry.test_dependency_map`，是 `selection.test_dependencies` 唯一的 schema：runner 写入时用它校验（测试文件 → 它传递 import 的仓库文件，来自现有的 source graph），engine 读取时也用它。LG-4 用落地时集成分支的真实 HEAD（A 组已做）。与依赖范围不相交时复用 final，相交时要求重跑 |
| B4 | `V1Runner.run_probe` 按 §A2.5 执行：执行前再校验一次、argv 列表、固定环境、stdin /dev/null、30 s 计时器强制结束、stdout 超过 256 KB 判 error、执行前后比对 code_tree（不一致判 error 并记 integrity）。结果是 runner 签名的 `probe` 调用，target 是 exit 行 id。`pipeline_test.py --mode probe` 改为执行 DESIGN_SPEC 的 probe exit（可用 `--name` 选一条）；旧的任意 argv 模式、`--` 之后的 argv、`probe:<name>` 引用及其合成结果行全部删除。这改动了冻结的 `_plan` / `_plan_ac_refs`，按方案 B4 的批准记入 `pipeline-test-speed.md` §11 |
| B5 | mutation 的结果也记成 runner 签名的调用（target 为 `--name`），可以进 final 的 trace |

证明：`test_every_code_profile_lands[direct/trivial/full/bugfix/goal]`、`test_tests_red_ends_not_landed_tests_red`、`test_red_then_green_rerun_lands`、`test_main_moves_disjoint_lands_on_new_main`、`test_main_moves_into_dependency_scope_reruns_then_lands`、`test_merge_conflict_three_times_ends_land_conflict`、`test_expired_approval_is_refused_and_a_fresh_one_lands`、新增的三个 probe 用例。

**C1 的现状（由 G 组用例发现，不是本组改的）：** `test_code_changed_after_prepare_cannot_land_even_with_edited_json` 现在已经通过——改了代码、再把 land-request JSON 改成和新树一致，`execute_request` 在 `auth` 比对 binding_digest 时拒绝（"authorization action/bindings mismatch"）。所以方案 C1 说的"没测过的树能落地"在当前代码上不成立；C1 剩下的是让执行只读事件、不读 JSON，以及审批卡显示树，放在 C 组。

## 5. N1（direct 去掉 PLAN）

**根因：** direct 照着 trivial 走 PLAN，要写 lite REQUIREMENTS / DESIGN_SPEC 和 exit criteria，让最小的改动背了完整的文档成本。XG 定（方案 §5）：direct 永远没有 exit criteria，需求含糊就弃掉、改用 trivial 重跑并在那里问。

| 条目 | 处理 |
|---|---|
| 阶段 | `lifecycle.PROFILE_STAGES["direct"]` = build → review → test → deliver → reflect。`pipeline_profiles.PIPELINE_PROFILES` 改为从 lifecycle 派生，阶段顺序只有一个来源 |
| 需求 | `create --requirement` 对 direct 必填（≤ 4000 字），写进受保护的 `contract.json`，REPORT 显示 "Requirement: …" 一行 |
| check | 读 REQUIREMENTS / DESIGN_SPEC 的 check（RQ-\*、DS-\*、BX-1、TS-2、TS-3）以及 DC-\* 不再为 direct 注册，并带豁免理由——不是"跑了再判过" |
| BX-2 | direct BUILD 新增：必须有改动；全是纯文档（`doc_paths.is_doc_only_path`）直接过；否则每个 repo 的 final 必须在当前 code tree 上，且 runner 为改动选出的每个测试文件（冻结的 `select_backend` + 改动的前端测试，`_v1_tdd_required`）在 `final.tests.nodes` 里都有 node 且全部 pass。TEST 不变 |
| 其它 | `_changed_paths` 用 `--untracked-files=all`，新建文件不漏；拒绝提示里的命令带 `--requirement`；卡片、INSTRUCTIONS、AGENTS.md 同步 |

trivial 不变：`tests/fixtures/pipeline_v1_trivial_profile.json` 是改动前 trivial 的阶段与 check 快照，`test_pipeline_v1_direct_profile.py` 逐项比对。原来用 direct 测"设计 / 决策 / probe"的端到端用例改用 trivial。

审查（独立子代理）发现 3 个 HIGH、1 个 MED，均已修：选择失败时 `_v1_tdd_required` 返回 None、BX-2 失败（不再退化成短名单）；没有选中任何测试时，必须有 agent 自己点名的测试文件整个跑过且通过；只跑某个 node 不能代表整个文件（回执记 `whole_files`，final 记 `tests.whole_files`）；direct 的 `decision-request` 在写事件前拒绝，日志不会被写坏；仍按 direct 有 PLAN 写的旧测试已改。

证明：`test_direct_starts_at_build_and_needs_the_users_words`、`test_a_doc_only_direct_run_lands_without_any_document`（真 CLI，REPORT 含需求，提交带 Swarm trailer）、`test_a_direct_code_change_needs_passing_tdd_to_leave_build`（没跑 / 红 → BUILD 不过，绿 → 进 REVIEW）、`test_one_passing_node_does_not_stand_for_its_file`、`test_a_direct_run_cannot_ask_and_its_log_still_folds`、`test_bx2_fails_closed_when_selection_failed_or_selected_nothing`。`test_pipeline_test.py::test_source_scan_suites_cover_every_test_that_globs_python_files` 在 main 上本来就红（另一个 session 新增的 `test_turn_writer.py`），与本组无关。

## 6. C 组（授权和信任边界，含 A7、F-2）

**根因：** 授权链里有两处信任了 agent 能写的东西：执行人工落地时比对的是工作区里没有封印的 JSON；任何持有密钥的进程（包括 CLI）都能以 hook 的身份签名，而 `auth` 事件正是 CLI 在消费 token 之后自己写的。

| 条目 | 处理 |
|---|---|
| C1 | `land_request` 事件带完整 `request`（`auth.make_request` 的结果，含每个 repo 的 code_tree / delivery_tree / report_sha、note、skip_tests）以及 `report_sha`、`bases`、`push_reasons`；lifecycle 重建 request，和 `binding_digest` 不一致就拒。执行（`landing.execute_request`、`execute_authorized_action`）只读这个事件，JSON 只用于显示，删了照样能落地。审批卡（`security_hooks.v1_land_card`）从事件读出 run、动作、两棵树的短 sha、REPORT 第一行、push 理由数、是否跳过测试 |
| C2 | 已由 `fc6d2ab2` 完成；补回归测试 `test_the_cli_has_no_way_to_answer_a_decision` |
| C3 | `events.allow_hook_writer()` 只在 `main.py`（以及扮演 daemon 的测试进程）调用；`pipeline_v1.py` 入口 `restrict_process("cli")`，`pipeline_test.py` 入口 `restrict_process("runner")`；检查放在 `WriterCapability` 构造里，绕过 `writer()` 也拿不到。`auth` 改由 `run_owner_gate` 在人批准命令之后写（`landing.approve_from_event`），写不进去就不发 token。strict xfail `test_a_cli_process_cannot_sign_as_the_hook` 转为必须通过 |
| C4 | worktree_write_gate 拒绝写 v1 run worktree 里的 `.aidlc/deliverables/`；land、`run-land --prepare`、`run-land --request` 持有 run 的 `landing.lock`（非阻塞，第二个调用直接被告知）；`create` 去掉 `--mode`，模式只由 `mode_enabled()` 决定；shared 的 v1 run 也能拿到 token |
| C5 | CLI 捕获 `LandingError`，输出 JSON。`--skip-tests` 不需要 final：LG-4 比的是 main 的 HEAD 和 run 的分叉点（merge-base）。人工落地的 LG-4 原来拿 final 的 base 比 final 的 base（恒真），现在比 main 的真实 HEAD |
| C6 | `landing.consume_authorization`：检查和追加在同一把锁里（store 的 validator 折叠整条链）；第二次消费被拒，并记 `integrity`（`auth_consumed_twice`） |
| A7 | `land` 要求 hook token（gate 为 `land` 发）；没有 token 时追加 `land(not_landed, needs_hooked_session)`，走不落地的路径，main 不动 |
| F-2 | `answer` 的发生键改回 `tool_use_id`；"一个 handout 一个答案"由 bridge 通过 `AuditStore.append_new(refuse=…)` 在锁内检查 |

**顺带发现并修复：** abandon 之后的 run 恢复 worktree 一直失败（`git worktree add -b` 遇到保留下来的 `run/<id>` 分支）。`run_worktree.create` 在恢复时复用这条分支，前提是它仍指向快照的 base。

**偏离：**
- 设计 §A4.3 写 `--prepare`"final 没覆盖当前 code_tree 时先跑一次测试"。实现为拒绝，并给出 `pipeline_test.py --mode regression` 命令：runner 的每次调用都受 110 s 上限和续跑机制约束（`pipeline_test_gate`），藏在 CLI 里的长子进程会越过这个上限。五维上 Quality 不变，Autonomy 不变（agent 照命令执行），Speed 不变。
- hookless 执行器没有单独的标记：代码里没有任何 hookless 执行器，判定就是"`land` 没有 hook token"。
- `infrastructure_failed` 仍没有生产者：审计区不可写、token 服务不在时，gate 拒绝命令，run 本身不变。要让 run 进入 `failed`，需要 daemon 侧写事件——而那正是写不进去的情况。留到 D 组再看。

**审查（独立子代理 `500ae800081a804f`）：** 3 HIGH、1 MED、1 LOW。
- **已修：** `discard-snapshot` 原来从可改的 JSON 读 `repo_name` 和 ledger 路径——现在 `repo_name` 进签名的 bindings，ledger 路径由 `(run_id, repo_name)` 推出，`meta.json` 自己的 run / repo 不符就拒（复制别的 repo 的快照无效）；`land_request` 的大小：note 限 600 字（超过会被挪进 blob、request 无法从事件重建），事件写不进（repo 太多超出 4 KiB）时报 `LandingError`；两个落地单元测试补上 `integration_roots`；C3 测试改为真正走 CLI 入口（`runpy` 执行 `pipeline_v1.py` 之后再尝试 `allow_hook_writer` 和 hook writer，必须失败）。
- **记为已知限制，不在本组修：** 任意 Python 代码（`python -c`）不进 CLI 入口时，仍可自己调用 `allow_hook_writer()`、读出密钥签任何事件，或直接调 `landing.commit_repos` 落地任意树。这就是方案 §9 的"签名只由 daemon 提供"那份单独设计；XG 已定先做 C3（代码层面分开）。§9 里"落地时对账每条 test_call / proof 与 hook 实际看到的 runner 调用"也属于那份设计，尚未做，最后交付时单列。

证明：`test_a_cli_process_cannot_sign_as_the_hook`、`test_landing_reads_its_request_from_the_event_not_the_json`、`test_the_approval_card_shows_what_lands`、`test_an_approval_is_consumed_once_and_a_second_try_is_integrity`、`test_skip_tests_lands_a_run_that_never_ran_tests`、`test_a_guard_failure_on_prepare_is_json_not_a_traceback`、`test_a_tested_prepare_on_an_untested_tree_names_the_runner`、`test_a_land_without_the_hook_ends_not_landed_needs_hooked_session`、`test_create_has_no_mode_option`、`test_an_agent_cannot_write_the_runs_deliverables`、`test_a_second_landing_of_the_same_run_is_refused_while_one_runs`、`test_the_cli_has_no_way_to_answer_a_decision`、`test_a_land_request_whose_request_does_not_match_its_digest_is_refused`、`test_an_unrecorded_approval_never_issues_the_token`。`test_pipeline_v1_real_e2e.py` 已没有 xfail。

## 7. B 组剩余项（B6–B8、F-5–F-11）

**根因：** 几处"run 的事实"取自错的位置——worktree 的 HEAD 被当成 run 的 base，所以 agent 在 worktree 里提交一次，验证清单、改动集、审查 diff、基线缓存就都跟着变了；缓存的身份只覆盖了一个文件；审查协议里几条规则（重派、聚类、FIXING_SPEC）只有描述，没有执行路径。

| 条目 | 处理 |
|---|---|
| B6 | run 的 base = worktree 从 main 分叉的位置（`merge-base HEAD main`；`pipeline_test._run_fork_point`，engine `_fork_base`）。runner 的 base、改动集（再加上分叉以来在 worktree 里提交的）、engine 的 `_changed_paths`、REVIEW 的 diff（base → 将要落地的 code_tree，含未跟踪文件）、proof 的 diff、基线结果全部按它算。QX-2 因此能看到被提交掉的 `validations.json` 改动。base 上没有 `validations.json` 时清单为空（G-2），空 `entries` 合法。顺带修了 `_changed_paths` 的 bug：`_git` 会 strip 输出，porcelain 第一行的前导空格被去掉后，第一个路径少一个字符 |
| B7 | `cache.pipeline_code_hash` 覆盖 `core/run_protocol/`、`pipeline_test.py`、`pipeline_v1.py`，contract、final、v1 结果缓存都用它。HEAD 基线缓存改为每个 node 一条 `put_once`，key = repo + base sha + 解释器 + 代码哈希 + 协议版本 + 规则版本 + nodeid；同一 key 写入不同结果即作废，之后重跑并给出警告 |
| B8 | runner 对 DESIGN_SPEC 里的 property / failure / exit 测试执行 `pytest --collect-only`（30 s 上限，按 code_tree 缓存），写进 final 的 `tests.collected`；DS-2b / DS-4b 读它和当前树上实际跑过的 node |
| F-5 | Gate-0 答案出现第二个 `VERDICT:` 行即为 unparsed |
| F-6 | 再次 `review-start`：某专家最近一次 R1 答案没有可用输出、且只派过一次时，重派一次（attempt+1）；不会有第三次，已答或未答的不重派 |
| F-7（部分） | RV-1a/1b/1c 把 `answer_reuse` 和 `answer` 一样计数。跨 run 的复用缓存没有接：§C6.2 的 key 需要模型 id，而 answer 事件里没有（专家 `model: inherit`，SubagentStop 侧也没记）。留到 D 组（需要先让 hook 记下模型 id） |
| F-8 | proof 的 `touches_executable` 由 diff 判断：文档，或 `.py/.sh/.ts/.tsx/.js/.jsx` 里每个增删行都是空行或整行注释，才算非可执行；docstring、行尾注释算代码 |
| F-9 | `review-address` 必须整簇处理：一个 pass 动了某个 ±10 行簇里的一条未关闭 finding，就要同时处理该簇其余未关闭的，否则整次拒绝、不写任何事件 |
| F-10 | `FIXING_SPEC.md` 由事件生成（§B6）：每个 pass 一节，簇、位置、修复者给的 cause 和 hunk、refuted、reclassify，以及每条 finding 的结果（折叠后的状态 + proof 的证据类型）。此前 v1 根本不写这份文档 |
| F-11 | final 的 provenance 记 `rules_version`（`runner-<v>/<risk rules v>`，与缓存 key 同一个字符串） |

**偏离和遗留：**
- B8 的 collect 结果放在 runner 封印的 final 里，不是单独的事件；final 已由 runner 封印，并被签名的 test_call 引用。
- v1 缓存目录（`cache/v1/test`、`cache/v1/baseline`）没有回收，baseline 现在每个 node 一个小文件。记入 E 组。

**审查（独立子代理 `81f1e422e202005b`）：** 1 HIGH、6 MED、2 LOW，全部已修：
- HIGH：base 原来用 main checkout 当前的 HEAD 求 merge-base，main 切到别的分支就会算错。现在 engine 在创建 worktree 时把集成分支记进审计区（`integration_branches.json`），base = `merge-base(worktree HEAD, 记录的分支)`（`runner.run_base`，engine 和 runner 共用）。
- F-9 的整簇规则只看 `open` / `invalid_test`，已证明的邻居不用重新处理；`review-address` 先校验全部条目、再写事件，坏条目不会留下半个 pass。
- 参数化测试按 `ref[...]` 算作已收集；shebang（`#!`）和 JS 生成器（`*gen()`）不算注释；proof 的 diff 改为 base → code_tree，新建的未跟踪文件也能证明。
- 分叉点只对本次调用确认是 run worktree 的仓库计算（v1 contract 或旧 run.json 的条目），不再把任意 linked worktree 当成 run。
- 代码哈希每个进程只算一次（实测 100 次约 0.85 s）；R1 重派的 attempt 编号按专家各自计算。

证明：`test_a_commit_in_the_worktree_does_not_move_the_base_or_hide_the_change`、`test_a_base_without_validations_json_requires_nothing`、`test_an_empty_entry_list_is_a_valid_manifest`、`test_pipeline_code_hash_moves_with_any_protocol_or_cli_file`、`test_a_conflicting_baseline_outcome_is_never_trusted`、`test_build_collects_the_designs_failure_mode_tests[…]`（两个方向）、`test_a_gate0_answer_with_two_verdicts_has_no_verdict`、`test_an_unparsed_review_answer_is_redispatched_once`、`test_a_committed_or_new_file_is_in_the_review_diff`、`test_comment_only_diffs_are_docs[…]`、`test_a_fix_pass_addresses_a_whole_cluster_or_nothing`、`test_fixing_spec_is_rendered_from_the_pass_events`、`test_the_final_records_the_rule_versions_it_selected_with`。

## 8. D 组（审计和 REFLECT，含 F-4）

**根因：** 审计的几个数字没有生产者或单位不一致（Token 被重复累加、主 agent 不计量、审批和 `accept_finding` 永远是 0、没有基线），REPORT 和卡片的格式与 §B8 / §E9 不符；REFLECT 的后台处理从未接线，由 agent 自己宣布完成。

| 条目 | 处理 |
|---|---|
| D1 | 新增 `core/run_protocol/reflect_job.py`：daemon 在本 session 的 pipeline 命令之后（PostToolUse `v1_reflect_trigger`），在一个后台线程里处理 `reflecting` 的 run——judge、按目标路由、逐条记 outcome、次数和时间上限（2 次 / 60 s），超限为 `degraded`——然后以 hook 身份写 `reflect_complete` / `reflect_exhausted`。这两个事件的 producer 改为 hook，CLI 的 `reflect-complete` / `reflect-exhausted` 命令删除，agent 无法自己宣布完成。查重候选由 CLI 用现有的 DDD 关键词召回（`recall_ddd_entries`）计算，agent 不再提供。新增只读命令 `pipeline_v1.py events`，列出可引用的事件 ID（RF-2） |
| D2 | `scripts/pipeline_baseline.py` 从已完成 run 的 created→completed 时间按 profile 求 p95（至少 5 个），生成 `core/run_protocol/baselines.json`；create 时写进 contract 的 `budget_seconds` / `budget_tokens`。旧 run 只有估算、没有实测 Token，所以 `budget_tokens` 全为 null，审计显示 "no baseline"。10 个 v1 run 之后重跑脚本 |
| D3 | create 和每次 CLI 调用自动记录本进程的 transcript id（`CLAUDE_CODE_SESSION_ID`，审计区 `transcripts.json`）；`measure-usage` 的最新一组替换之前的；有 transcript 计量时它已包含全部子 agent，SubagentStop 的专家记录不再相加；没有时只算专家，主 agent 为 null；一次计量都没有为 BLOCK；calls 的分子分母都是模型响应数 |
| D4 | 危险命令和外部发送的审批卡通过时，daemon 给本 session 拥有的 v1 run 记一次 `required_approval`（v1 的授权命令除外，那是人的决策）；`accept_finding` 计入 human_decisions（只在审计里，不占 decision 上限） |
| D5 | renderer 按 §B8：一句话结论（交付了什么、是否落地；未落地写 reason），去掉 "Landing guards: PASS" 行，"Full machine-readable audit is stored with this run."，push 理由里高危 security_policy 排第一，人工落地一行 request_id 和原话，未集成工作的保存位置和恢复命令；卡片末行 `Final audit through COMPLETED`；Quality N/A 显示 reason；`repos_isolated` 按实际创建了 worktree 的 repo 计；时间不足为 null；`% budget` / `no baseline` / `observed · x/y calls accounted`。人工落地 completed(not_landed) 的 run 后重建 run_audit 和卡片。chat 的 run 摘要卡片显示同一段卡片文本 |
| F-4 | handout 事件带 prompt 全文，按 `FORCED_BLOB_KEYS` 存进审计区 blob |

**偏离和遗留：**
- D1 的 judge 用现有的确定性门（`ddd_cultivation.is_noise` / `is_quality_lesson`），不调模型；MEMORY / EVOLUTION / self-evolution 的经验进项目的 proposal 队列，不由后台直接改受治理的 context 文件。领域经验直接 `apply_to_ddd`。
- D4 的 `manual_rescue` 生产者没有做：§E9.3 的三种信号都需要判断"这一步本该由系统完成"，现有事件里没有可靠依据；不靠推测补。
- F-7 的跨 run 答案复用仍待 answer 事件记录模型 id。

**对抗审查（1c1e2c20bd672ecd，3 HIGH + 6 MED）：**
- 已修：`domain:<project>` 只接受项目名（不能是路径），路由时再确认目标在 `Projects/` 下且存在；writer 的结果分为已写入（`applied` / `created_section` / `duplicate` / 入队）、拒绝（记 `rejected`，不算 applied）、忙（`locked`，重试后 degraded）；后台任务异常时以 `degraded` 结束 REFLECT，不会一直停在 reflecting；`measure-usage` 在 run 已 completed 时重建最终审计、REPORT 和卡片（REFLECT 可能先完成）；人工落地的 request_id 和原话改为每次从签名的 `auth_consumed` / `land_request` 事件推出，COMPLETED 重建不再丢；结论里"交付了什么"对非 direct 取 REQUIREMENTS 的 understanding.claim（否则第一条 story）；基线脚本对 v1 run 用终态的 `updated_at` 作结束时间，Token 样本只取有整段计量（calls 已知）的审计。
- 遗留（§9）：进程内任意 Python 可以自己调 `allow_hook_writer()` 再签 hook 事件（与 C 组相同的已知限制；需要把 hook 签名移到 daemon 的 IPC 后面）。60 秒上限只在每条经验前检查，阻塞的文件写不能中断；completed 之后重建审计失败不会自动修复。

## 9. E 组（残留）

**根因：** 退役命令和旧流程的文字没有跟着代码改，卡片 lint 只校验检查 ID，所以提示指向不存在的命令也不会变红。

| 条目 | 处理 |
|---|---|
| 拒绝提示 | active-run commit gate 指向 `pipeline_v1.py land`；session-link gate 写 `pipeline_v1.py create`；worktree stash 拒绝改为只读 git（`git diff <base>...HEAD` 等，`<base>` 由 `git worktree list` 得到）；runner 的共享树拒绝和 files_touched 提示改为开 v1 run。新测试 `test_deny_text_commands.py` 渲染真实文本，用 `pipeline_v1.parser()` 解析其中每条命令，并确认没有退役的 `artifact_cli run-*` |
| SKILL.md | 流程改为按 profile 的阶段表（来自 `pipeline_profiles`），Verification 改为 v1 的证据（status、engine 生成的 REPORT、run_audit、push-status、runner 结果） |
| 专家 | `experts/code/` 下 9 个不再派发的专家文件标注"Pipeline v1: not dispatched"（并入 correctness，E1），保留给 Experts 库；`pipeline-expert` 的描述不再提 Gate-1/2、meta-review、root-cause |
| docstring | `run_worktree.mode_enabled` / `create` 不再说有 shared 回退 |
| RV-1c | `review.md` 改为只记录（与 checks 注册的效果一致，v13 §B5） |
| Gate-0 marker | `runtime_hooks` 不再把 Gate-0 verdict 解析进旧的 session marker（没有读者；verdict 只在签名的 answer 事件里） |
| 卡片 lint | 新增三条：卡片、INSTRUCTIONS、SKILL 里的每条 `pipeline_v1.py` 命令用真实 parser 解析；每个 `--flag` 必须是两个 CLI 的真实选项，`--mode` 的值必须是 runner 的模式；direct 的 create 必须带 `--requirement`；反引号里的字段名必须在协议代码里出现。审查发现 INSTRUCTIONS 里的 `--mode shared`（已删）和缺 `--requirement` 的 direct create（已补），lint 现在能抓到（已做变异验证） |
| 风险扩展 | 共享配置还包括 backend 下任意 `conftest.py` 和 desktop 下任意 `vitest.config.*` / `vite.config.*`；`RISK_RULES_VERSION` 升到 `v13-2026-10-23`（进入缓存 key 和 provenance） |
| 其他 | runner 的 `_SOURCE_SCAN_SUITES` 补上 `test_guard_contracts`、`test_transcript_event_schema`、`test_turn_writer`、`test_pipeline_v1_cards`（之前 main 上的已知红）；`test_pipeline_v1_real_e2e.py` 去掉已无用的 `_xfail` 辅助，0 个 xfail |

**对抗审查（29988622faae386c，3 MED + 1 LOW）：** 全部已修（上表的 INSTRUCTIONS、stash 文本、lint 范围、runtime_hooks docstring）。lint 只校验字段名出现在协议源码中，没有对照结构化 schema，记为遗留。

**遗留：** v1 缓存 GC 没有做（缓存按 key 不可变，目前只增长，需要按 run 终态和时间回收的设计）。

## 10. 按 v13 §E7 的审核表（整次修复：G、A、B、N2、N1、C、D、E）

| 维度 | 当前基线 | 本次变化 | 证据 | 判定 | 处理 |
|---|---|---|---|---|---|
| Run Isolation | worktree 创建失败会退回共享 checkout；验证清单和 review / proof diff 读 main 的 HEAD；`discard-snapshot` 只按 id 绑定；hook 写者可被 CLI 进程签名 | 失败即 pause、mode 在 create 时固定；run base = 记录的集成分支 fork point（B6）；land 在 run 锁内（C4）；`.aidlc/deliverables/` 写保护；snapshot 绑定 `{snapshot_id, repo_name}`；CLI / runner 进程只能拿到自己的 writer（C3）；REFLECT 路由只写 `Projects/` 内（D） | `test_pipeline_v1_real_e2e.py`（0 xfail）、`test_pipeline_v1_rehearsal.py`、landing / worktree gate 测试；各组宽回归（c_all2、b_all2、d_fix3、e_all2，只有已知红，E 组已修） | WARN | 已知限制：进程内任意 Python 仍可自己 `allow_hook_writer()` 签 hook 事件（§6、§8 遗留，需把 hook 签名移到 daemon IPC 后）。没有跨 run 读写的新增路径 |
| Quality | 落地读 `latest-*`；BX 没有整文件运行要求；collect 不真实；finding 由模型声明关闭；拒绝提示指向退役命令 | BX-2 要求选中文件整文件运行且全过（N1）；DS-2b/DS-4b 真实 collect（B8）；缓存 key 含 runner 代码哈希和规则版本（B7）；聚类整体处理（F-9）；双 VERDICT 判 unparsed（F-5）；卡片和拒绝提示的命令用真实 parser 校验（E） | 每组先写失败测试再修；每组一次独立对抗审查（N1 3H+1M、C 3H+1M+1L、B 1H+6M+2L、D 3H+6M、E 3M+1L）且已修或记为遗留；card lint 变异验证 | PASS | 未做 v1 缓存 GC；字段 lint 只按名称 |
| Autonomy | 3 个 run 因 DELIVER 被拒 3 次放弃；REFLECT 由 agent 自己宣布完成；人工审批不计数；direct 需要 PLAN | direct 去掉 PLAN 和设计文档（N1）；纯文档改动不开 run（N2）；REFLECT 由 daemon 后台完成，agent 无命令（D1）；`required_approval`、`accept_finding` 计入审计（D4）；人工落地是两步、绑定树的一次性授权（C1、C6） | real_e2e 的 direct / 人工落地 / 拒绝路径；`test_the_agent_cannot_complete_reflect_and_the_daemon_does` | PASS | `manual_rescue` 生产者未做（没有可靠信号，不推测） |
| Speed | 无 p95 基线；验证 Gate-2 多派；每次判定扫描大量文件 | 按 profile 的 wall p95 基线写入 contract（D2）；结果按不可变 key 复用（B7）；REFLECT 不在关键路径（后台，单线程，有上限） | `core/run_protocol/baselines.json`（bugfix 8355 s、direct 3363 s、full 32691 s、goal 66502 s、research 1048 s、trivial 2710 s，均为旧 run 的 p95） | PARTIAL | v1 改进还没有实测：重建后跑满 10 个 v1 run，重跑 `scripts/pipeline_baseline.py` 对比 |
| Token Cost | 有阶段级估算；measure-usage 累加；专家用量重复计算 | 最新一次计量替换之前的；有 transcript 计量时不再加专家记录；没计量为 BLOCK（D3）；handout prompt 进 blob（F-4） | `test_run_protocol_audit.py` 的替换 / 未知 / BLOCK 测试 | PARTIAL | Token 基线为 null（旧 run 没有实测）；需要 v1 run 的实测数据；跨 run 答案复用（F-7）待模型 id |

**总体：PARTIAL。** 没有 BLOCK：Run Isolation 只有一项已知、已记录的进程内签名限制（WARN），Quality 和 Autonomy 有测试证据。Speed 和 Token Cost 的改进要在 XG 重建、重启后由真实 v1 run 实测，现在不能声称已验证。

## 11. 设计符合性修复 K1：事实绑定层（2026-10-23 符合性方案）

依据：`2026-10-23-pipeline-v1-conformance-fixing-spec.md` §3 K1。根因 R-A：判定只看证据有没有，不看证据对不对、是不是当前事实。

**做法：** 新增 `core/run_protocol/facts.py`，作为 check 读取证据的唯一入口。`engine.check_context` 只从这里取 final、base、必需验证和 decision。

| finding | 修法 |
|---|---|
| F1 refuted refs | `facts.resolve_refs`：每条 ref 必须能解析成三种之一：当前 code tree 里存在的 `path:L[-M]`、本 run 的 `event:<id>`、bound final 里有结果的 `test:<nodeid>`。解析结果写进 proof 事件；`findings.fold` 只接受已解析的 ref |
| F2 LG-5 自证 | `facts.required_validations` 读 base 上的 `validations.json`，按改动路径选出必需项；final 自己的列表只表示"跑了什么" |
| F3 QX-2 | `cache.PIPELINE_CODE` 作为唯一清单，同时决定 pipeline hash 和 `facts.RULE_PATHS`，`pipeline_test.py` 也包含在内；final 用的 pipeline hash 和 contract 不一致时，同样作为 QX-2 的 push 理由 |
| F4 旧树 final | `facts.bind_final`：final 的 code_tree 和 base 必须都等于当前值，否则 check 拿到的 final 为空；不一致的原因写进 LG-1 的 detail |
| F5 base 退回 HEAD | `facts.resolve_base` 是 engine 和 runner 唯一的 base 规则。isolated repo 没有记录 integration branch 时直接报错，不再用 `symbolic-ref` 或 HEAD 猜。`runner.run_base` 删除 |
| F14（并入 K1） | `engine._create_worktree` 是唯一创建 worktree 的地方，同时记录 integration branch；`ensure_worktrees` 和 `resume` 都走它 |
| F6 DC-1 空输入 | decision 内容写进 decision 事件的 `decision_json` blob（`FORCED_BLOB_KEYS`）。DC-1、`pending_decision`、聊天卡片回答、dashboard 都从事件读，`artifacts/decision-*.json` 不再写入。DC-1 的 known_refs 和提问时用同一个函数 `engine.decision_refs` 生成 |
| F7 Token 自证 | `total_calls` 以"模型会话"为单位，从事件计数：主 agent 算 1，每个 `answer` 算 1。只有对应的计量存在时才算 accounted：主 agent 要有 transcript 计量，每个专家要有自己的 SubagentStop 记录。accounted 少于 total 时判 WARN |
| F10 baseline 冲突 | `_v1_cache_conflict(kind)` 统一处理 test 和 baseline 两种 cache 的冲突，都写签名的 integrity 事件（`cross_run_baseline_cache`） |
| L3 TS-3 | trace 的覆盖要求扩到 R、P、F 全部（设计 §A2.3；TS-3 只记录，不影响判定） |

**先写的失败测试：** `tests/test_pipeline_v1_conformance_k1.py` 里的 6 个端到端用例，在修复前的代码上全部失败，失败原因都对；`test_run_protocol_audit.py::test_partial_metering_cannot_read_as_complete`；`test_run_protocol_cache.py::test_a_baseline_conflict_is_a_signed_integrity_event`。

**删除：** `runner.run_base`、`check_context` 里写死的 `validation_rule_paths`、engine 里重复的 decision refs 构造、landing 里 skip-tests 时自己算 base 的那一份。

**独立审查（两轮）：**

| 轮次 | 发现 | 修法 |
|---|---|---|
| R1 HIGH | QX-2 从错误的字段读 pipeline hash，导致每个 run 都不 push ready | 改读 `provenance.pipeline_code_hash`；补一个"未改动的 run 必须通过 QX-2"的正向测试 |
| R1 HIGH | isolated run 缺 worktree 时退回 main checkout 和 HEAD | `engine._current_code` 规定三种情况：已落地的 repo 取落地 commit 的树（`git_tree.commit_code_tree`），base 取它的父 commit；isolated 只看自己的 worktree，缺失即视为未知；shared 看 main。`pipeline_test._v1_runner` 拒绝把 isolated run 指向 main checkout |
| R1 HIGH / R2 HIGH | refutation 写入后不随代码树失效；多仓时不同 repo 的 tree id 可能相同 | proof 事件记下所属 repo；`facts.stale_closures` 只拿该 repo 的当前树比较，没记 repo 的 proof 只在单仓 run 中成立；RV-2 会拦下关闭依据已过期的 HIGH finding；`test:` 类型的 ref 只在该 repo 的 final 里解析 |
| R1 HIGH | 改动前写下的 decision 事件没有 `decision_json` | 该字段改为 optional，旧日志仍能折叠；DC-1 遇到没有内容的 decision 判失败 |
| R1 MED | pipeline 代码清单太窄 | `PIPELINE_CODE` 加入 `run_worktree`、`evidence_seal`、`pipeline_human`、`run_owner_gate` |
| R1 MED | Token 会话数漏算主 agent 多轮、崩溃的专家 | 会话数 = 1 + handout 数 − answer_reuse 数；专家是否 accounted 以它自己的 SubagentStop 记录为准 |
| R1 MED / R2 MED | worktree 创建和 branch 记录不是原子操作 | 先记录 branch 再创建 worktree；实际 branch 与记录不一致时删掉刚建的 worktree；resume 要求每个 worktree 都存在且已记录 branch |
| R1 MED | `decision-answers.json` 成了第二份事实 | 删掉这份写入 |
| R2 MED | `human_override` 的 run 在 COMPLETED 重算后 push_ready 变回 true | 是否评估 MD-3 由折叠出的 landing 决定，不再由调用参数决定 |
| R1 LOW | 遗留死代码 `_changed_paths` | 删除 |

**新增测试：** `tests/test_run_protocol_facts.py`；`test_pipeline_v1_conformance_k1.py` 新增 4 个审查回归用例；`test_run_protocol_lifecycle.py` 新增 DC-1 处理旧 decision 的用例。

## 12. 设计符合性修复 K2：多仓落地要么全部落地，要么都不落地

依据：符合性方案 §3 K2、v13 §A3.7。根因 R-B：落地写入 main 的那一步没有"要么全部、要么都不"的语义。

**做法：** `landing.commit_repos` 分两段执行。

- **第 1 段：** 所有仓库先逐个检查完，任何 ref 都还没动。每个仓库要求：
  - HEAD 等于 run 的 base；
  - main checkout 当前所在的分支，就是这个仓库的集成分支（run 记录的分支；shared run 取第 1 段时 checkout 所在的分支）；
  - commit 对象已经写好；
  - 用 git 自己做一次同样的检出演练 `read-tree -n -m -u HEAD <commit>`，确认 main checkout 允许这次 fast-forward。index-only 改动、未跟踪文件冲突、目录与文件互换、大小写别名、symlink、gitlink 都交给 git 判断，不再用 Python 预测。
- **第 2 段：** 只做 fast-forward，动手前再确认 checkout 仍在集成分支上。

| 情况 | 结果 |
|---|---|
| 第 1 段失败 | `LandingError`：没有任何仓库落地，attempt 关闭，可以重试 |
| 第 2 段失败，且已有仓库移动过 | 写一条签名的 `partial_land` 事件（repo -> commit、失败的仓库），它只能绑定一次落地：要么是一个全部通过的、仍开着的普通落地 attempt（run 以 `not_landed(partial_land)` 结束，算 Quality BLOCK，REPORT 列出已落地和未落地的仓库），要么是一个人工授权的 consumed 事件（只做记录，并刷新 REPORT） |
| 崩溃恢复 | 只认这次计划精确会生成的那个 commit（parent、tree、message、Swarm 身份全部一致）；空的 progress 只有在没有任何仓库持有该 commit 时才丢弃；修改前写下的 plan 只要字段一致仍可接受 |
| `landed_commits` | 只返回最终完成的那次落地（`human_override` 的 request，或普通落地）的 commit 表；没有完成的落地时，返回最近一次 `partial_land`。不再把各次尝试的历史合并在一起 |
| progress 文件写失败 | 不再中断落地，只记日志；commit 交给调用方签名 |

**删除：** `landing._commit`（第 2 段只调用 `_fast_forward`）；audit 里的 `quality_block_reasons` 副本（改为直接引用 `lifecycle.QUALITY_BLOCK_REASONS`）。

**独立审查（三轮，第 3 轮的发现在本 commit 内一次修完，不再派第 4 轮）：**

| 轮次 | 发现 | 修法 |
|---|---|---|
| R1 HIGH | 第 1 段用 Python 预测 git 的检出安全，漏掉 index-only 改动、目录/文件互换、ignored 文件、大小写别名 | 改为直接让 git 做 dry run：`read-tree -n -m -u` |
| R1 HIGH | progress 写失败后，main 上留下没有任何记录的 commit | progress 写失败不再中断落地；commit 映射由签名的 land 或 `partial_land` 事件携带 |
| R1 HIGH / R2 HIGH | 人工 run-land 部分落地时没有签名记录；在 `awaiting_human` 和 `reflecting` 状态下 fold 会拒绝该事件 | `partial_land` 必须绑定到一次 consumed 的落地授权，只做记录，不改 run 状态；action 只允许 `land` / `land_skip_tests`，涉及的仓库必须在该 request 的 bindings 里 |
| R1 MED / R3 MED | 崩溃恢复时，只要 parent 和 tree 相同就把 commit 认作自己的 | 精确比对：commit message 字节相同，author 和 committer 的 name 与 email 都是 Swarm；plan 里绑定 `message_sha` |
| R1 MED | 恢复时的普通落地绕过了 `PartialLanding` 处理 | 首次落地和恢复落地都走 `_commit_normal` |
| R1 MED / R2 MED / R3 MED | `partial_land` 和 REPORT 丢了 commit 身份；`landed_commits` 把多次尝试的历史混在一起；结果依赖 progress 文件 | land 事件带上 `commits` 映射（repo → commit）；`landed_commits` 只读签名事件：取最后一次完成的落地，若有更晚的 `partial_land` 则以它为准；progress 文件只在旧事件缺少映射时用来兜底 |
| R2 HIGH / R3 HIGH | 落到了错误的分支上（普通路径和人工路径都有） | 两条路径都按 run 记录的集成分支落地；第 1 段和第 2 段各核对一次当前所在分支 |
| R2 HIGH / R3 HIGH | 第 2 段里，ref 已经移动、merge 的后续步骤失败，结果被当成"什么都没动" | 失败后重新读一次 HEAD：如果已经等于准备好的 commit，就按已落地处理 |
| R2 MED | 修改前写下的 plan 无法恢复 | 新增 `_same_plan`：旧 plan 已有的字段一致即可接受 |
| R2 MED | 格式不对或孤立的 `partial_land` 被 fold 接受 | 严格校验：commit 必须是合法 sha 的映射；事件必须且只能绑定到"一个通过的普通落地 attempt"或"一个 consumed 授权"之一 |
| R2 MED / R3 HIGH | 人工落地只落了一部分时，REPORT 和 Quality 仍按旧状态显示 | audit 把"晚于最后一次完成落地的 `partial_land`"直接当作这次的结果（REPORT 列出已落地/未落地的仓库，Quality 判 BLOCK）；engine 同时刷新 REPORT 和 projection |

**残余风险：**
- fast-forward 完成到签名事件写入之间，进程如果崩溃，那一刻没有签名记录。恢复时依靠精确匹配 commit 来接手，这段缝隙消除不掉，因为任何记录都只能写在 ref 移动之后。
- 第 2 段里由 git 自身副作用引起的失败（例如 `ORIG_HEAD` lock）同样按 `partial_land` 记录，不再声称只有竞争窗口才会出现这种失败。
- shared run 的未暂存改动会被 git 拒绝 fast-forward。这在本次修改之前就是如此，留到 K5 的 shared 矩阵里验证。

**新增测试：** `tests/test_pipeline_v1_conformance_k2.py`，共 12 个用例，前 3 个在修改前的代码上失败。

## 13. 设计符合性修复 K5：按 v13 §D4 逐条验收

依据：符合性方案 §3 K5、v13 §D4、§C5.1。根因 R-E：验收测试是按"实现能推出成立"写的，没有按 §D4 逐条写。这次按 §D4 写测试，测试暴露出两处真缺陷。

**测试先失败，暴露出的缺陷：**

| 测试 | 修改前的结果 | 根因与修法 |
|---|---|---|
| profile × {isolated, shared} 矩阵 | shared 的 5 个 profile 全部卡在 DELIVER，一次都落不了地 | shared run 的代码就是 checkout 里尚未提交的文件，`merge --ff-only`（以及 K2 的检出演练）会拒绝这次落地自己的改动。新增 `landing._land_in_place`：先拿到 git 自己的 `index.lock`；index 必须仍然恰好等于 base；新 index 在旁边建好；分支用 `update-ref` 做 CAS 移动；然后把建好的 index 改名就位。checkout 里只新建 run 保留目录下缺失的 REPORT 文件（用 O_EXCL 创建），其余路径一律不碰。别的 session 在两段之间删掉的文件不会被写回；staged 的改动、或者 index 正被别的进程锁住时，什么都不落地。整个过程不用 `reset` |
| §D4 第 4 条：test 不能跨 run | 用 run A 的 id 去测 run B 的 worktree，返回 ok | `pipeline_test._v1_runner` 改为只按 contract 识别被测仓库：按 realpath 在 contract 里找，必须恰好对上一个仓库（isolated 对它自己的 `wt_path`，shared 对 contract 里的 root）。目录名不再当作身份 |
| events 枚举测试 | `owner_transfer`、`partial_land`、`gate0_exhausted`、`resume` 四种 kind 没有用例 | CASES 补齐这四种；`CASES == EVENT_SPECS` 作为测试固定下来，以后新增 kind 漏写用例会直接失败（F24） |

**新增验收（`tests/test_pipeline_v1_conformance_k5.py`，全部通过真实 CLI 和 hook 驱动）：**
- 5 个 profile × 2 种模式：落地后的整棵 tree 等于 final 的 `code_tree` 加上 REPORT；checkout 落地后干净；shared run 的 push_ready 为 false，原因含 MD-1。
- 两种模式各跑一遍：TEST 红走 `not_landed(tests_red)`；熔断走 `checks_exhausted`；人工 run-land 走 `human_override`，land 事件带 commit 表；decision 进入 `awaiting_human`，人回答后继续；abandon → restore → resume 后落地，tree 仍与 final 绑定。
- §D4 第 1、4、5、6 条：两个 isolated run 改同一文件，各自的 final 只含自己的内容；test、auth、abandon、restore、decision 都不跨 run；shared checkout 的改动不进入 isolated run 的 final；缓存 key 冲突写 integrity，两个值都不复用。
- shared 落地原语的单元测试：并发删除的文件不被写回；有 staged 改动或 index 被锁时不落地；`partial_land` 之后的 run-land 保留已落地的 commit，只落地剩下的仓库。

**审查第 1 轮（f77bb8ec）的修复：**
- **HIGH：** shared 落地会把并发删除的文件写回，还可能丢掉 index 里的改动。修法是上面的 index 锁事务。
- **HIGH：** `partial_land` 之后的 run-land 会重新渲染 REPORT，已落地仓库的 delivery tree 因此变了，落地无法补完。修法：引入 `adopted`，即最近一次晚于所有完成落地的 `partial_land`。这些 commit 必须满足三条：parent 等于 base、Swarm 身份、在集成分支上；满足才保留，只落地剩下的仓库。最终 land 事件记录两部分的并集。
- **MED：** ref 已移动但 checkout 没收尾。index 的安装是锁内的一次改名；REPORT 写失败只记日志，之后每次重试或接手都会再收尾一次（`_settle_in_place`）。
- **MED：** 测试按目录名推断仓库身份。改为只认 contract。
- **MED：** 测试断言过弱。改为上面的整树绑定，并补齐两种模式下的各个场景。

**审查第 2 轮（5cc6b9a2）的修复：**
- **HIGH：** CAS 之后的失败可能没留下任何记录（OSError 直接抛出），也可能被报告成"已完整收尾"。改为：`_land_in_place` 在 CAS 之后遇到任何失败都抛 `_Unsettled`，只有在什么都没移动时才抛 `LandingError`。`commit_repos` 返回 `Landed`，带上 `.unsettled`。签名的 land 事件用可选字段 `unsettled` 列出这些仓库，lifecycle 校验它必须是 `commits` 的子集，audit 的 details 和 REPORT 都会写出来。
  - 判定口径：分支已经移动就算落地，commit 已经在 main 上，不丢数据。checkout 没收尾记为 `unsettled`，不记成 `partial_land`；`partial_land` 的含义是有仓库的分支根本没动。
- **HIGH：** 写 REPORT 时可能顺着符号链接写到仓库外。改由 `_create_owned` 写：从仓库根开始按目录 fd 逐级打开，目录用 `O_DIRECTORY|O_NOFOLLOW`，文件用 `O_CREAT|O_EXCL|O_NOFOLLOW`；文件已存在就不碰。
- **MED：** 接手 partial 落地的 commit 时跳过了收尾。现在这类 commit、已记录的 commit、崩溃后接手的 commit 都要经过 `_settled()`；收尾没完成的，记为 unsettled。

**审查第 3 轮（c356d389，最后一轮；发现在本 commit 内一次修完，不派第 4 轮）：**
- **HIGH：** 普通落地在写 run 目录里的 REPORT 副本时失败，会让 main 上已有 commit，却既没有 `land` 也没有 `partial_land` 事件。修法是在落地编排里固定顺序：签名的 land 事件由 `_commit_normal` 写，紧跟在 refs 移动之后（首次落地和恢复落地共用这一处）。run 目录的 REPORT.md 降为投影，在事件之后用 `_copy_report` 写，写失败只记日志。
- **HIGH：** `partial_land` 丢了已落地仓库的 unsettled 信息。修法：`PartialLanding` 携带 `unsettled`；`partial_land` 事件加可选字段 `unsettled`，lifecycle 校验它是 `landed` 的子集；普通落地和人工落地两条路径都写这个字段；audit 从最新的 land 或 partial_land 读取。
- **MED：** REPORT 位置上原先就有的错误文件，被当成"已收尾"。修法：已存在的对象必须是普通文件，并且逐字节等于 commit 里的 blob，否则记为 unsettled，文件本身不动。写入也改为循环写，处理短写的情况。
- **MED：** 验收没有固定 §D4 要求的阶段链，缓存也不是真的并发写。修法：矩阵里每个 profile × 模式都核对签名链 `stage_attempt → 通过的 check → stage_complete → transition`，期望值取自 check 注册表 `checks.stage_checks`，而不是 run 自己写下的 roster；还要求 land attempt 通过全部 land 检查。缓存改用线程屏障，让两个写者在同一时刻写：值相同只留一个，值不同记 conflict，两个都不复用。

**残余风险：** refs 已经移动、签名 land 事件却追加失败时，审计区本身已经不可写。这种情况由恢复路径兜底：它接手 progress 里记下的 commit，再补签事件。审计区不可写时 run 进入 `failed`，这一步留到 K3 F11 的 `infrastructure_failed` 接线时补上。

**dev backend 预演（F25 / §6 Q2）：** 已在 :8000 启动本分支的 backend，`SWARM_DATA_DIR` 指向临时目录，`/health` 正常，`/api/pipelines` 读的是临时目录。但执行环境里没有 AWS 凭证：默认 profile 和 `AWS_PROFILE=bedrock` 都报 "Unable to locate credentials"，`/health` 显示 `auth: expired`。因此起不了 SDK 会话，daemon 侧的 hook 无法真正触发。按 §6 Q2 的备选方案，这一项移到 XG rebuild 之后由 XG 跑，步骤如下：

```bash
D=$(mktemp -d); cd <swarmai>/backend
SWARMAI_MODE=dev DATABASE_TYPE=sqlite SWARM_DATA_DIR=$D .venv/bin/python main.py --port 8000
# 在指向 :8000 的应用里：direct 跑 2 次，trivial / full / bugfix / goal / research 各跑 1 次，
# 用一个临时 git 仓库（--repo）；每个 run 结束后看 $D/state/pipeline_agent_audit/runs/<id>/run_audit.json，
# 五维都不能是 BLOCK
```

## 14. 设计符合性修复 K3：半截机制逐项接上或删除

依据：符合性方案 §3 K3、§5；v13 §A1.2、§A4.4、§B5.2、§B7、§C5.3、E4/E5。根因 R-C：机制只做了一半，有生产者没读者，或有读者没生产者。

| 项 | 修改前 | 现在 |
|---|---|---|
| F11 `infrastructure_failed` | 没有生产者 | 新模块 `infra.py` 定义三类基础设施故障：审计区写不进（`store.AuditUnwritable`，覆盖加锁、追加事件、写 blob）、hook token 服务连不上（`engine.HookTokenUnavailable`，三处取 token 统一走 `_consume_token`）、git 对象写不进（`git_tree.GitObjectWriteError`，覆盖 add、hash-object、write-tree）。碰到故障的那一层把它当作基础设施故障抛出，不当作"事实未知"吞掉；CLI 或 runner 捕获后写 `infrastructure_failed`，run 进入 `failed`。每个 run 只写一条：在 run 锁内用 `lifecycle.infrastructure_refusal` 去重。runner 签不了 transition，下一次以 CLI 身份 `load` 时补签。其他异常照常抛出 |
| F12 DC-2 | `resolve_after_cap` 只有测试在调 | decision 次数用完后，decision-request 带上 `after_cap {option_id, evidence_refs}`，由 `resolve_after_cap` 校验。decision 事件记下 `decided_by: agent_after_cap`，不转到 `awaiting_human`。这个选择会写进 REQUIREMENTS 的 clarification，DC-2 读到后令 push 失败。次数用完却没带选择，直接拒绝 |
| F13 回答写回 | `human_answer` 不写回源文档 | 写 `human_answer` 的同一处立即把答案写回源文档：REQUIREMENTS 写进 clarifications（qid = request_id），DESIGN_SPEC 写进 `chosen.decisions`，然后重新渲染。答案只从签名事件读，stage-complete 时也会并入。`decision-answers.json` 在 K1 已删除。**偏离：** trivial 的 lite DESIGN_SPEC 没有 `chosen` 字段，这一种不写回，答案只留在签名事件和 REPORT 里 |
| F14 resume 记录分支 | — | K1 已修（`_create_worktree` 是唯一的创建入口）。本簇只加测试固定这一点 |
| F15 受影响测试 | 算出来了，没交给 runner | engine 把受影响的测试写进审计区的 `carry_plan.json`（按仓库存，绑定同步后的 code tree）。runner 的 recheck 只跑 plan 里的测试。plan 为空表示点不出测试，此时拒绝 recheck，要求跑 regression |
| F16 确定性检查在前 | 先派专家 | `review-start` 先跑 REVIEW 阶段里不依赖专家回答的检查（DS-9b）。不通过就不发 handout |
| F17 caps | contract 字段没人读 | 只读 contract 里的 `regression_round_cap` 和 `recheck_cap`。`FULL_SCOPE_MAX_ROUNDS`、`RECHECK_MAX_FIXES`、旧的"只在 DELIVER"阶段门（`FULL_SCOPE_STAGES`、`_open_stage`）全部删除。旧阶段门对 v1 从未生效，因为 v1 的 run.json 没有 `stage_timing`。**行为变化：** v1 run 的完整范围轮数第一次真正受上限约束，第 3 次 regression 会被拒绝，修复要改走 recheck。两个 E2E 测试按此调整。冻结 digest 和 `pipeline-test-speed.md` §11 已同步 |
| F18 Speed/Token BLOCK | Speed 永远不会 BLOCK | 任何次数上限被打满都写一条 `metric(cap_exceeded.<cap>)`：not-landed 的原因（`checks_exhausted`、`land_conflict`、`final_stale`、`framing_rejected`）、第三次专家派发、完整范围轮数、修复 recheck 次数、熔断的各个计数器。同一 stage attempt 只记一次。audit 见到这条，Speed 判 BLOCK；超的是模型派发类上限时，Token 也判 BLOCK |
| F19 REFLECT 关系 | 三种关系处理成一样 | reinforces：在原条目下追加证据引用，不复制文本。refines：保留原文，在下面追加修订。contradicts：不写入，进待审队列并注明冲突的条目。lesson 的 statement 和 tell 必须是单行文本。写文档用随机 O_EXCL\|O_NOFOLLOW 临时文件，并校验目标在项目目录内 |

**Q3 未接线，等 XG 决定。** 前提不成立：pipeline-expert 的 AgentDefinition 写的是 `model="inherit"`（`core/pipeline_expert_agent.py`），模型不固定在 agent 定义里，CLI 在 handout 时拿不到确切的模型 id。`review_cache_key`、`record_reuse` 以及 `answer_reuse` 的 fold、检查、audit 分支暂不动，保持原样，等 XG 定下做法后一次处理。

**审查（第 1 轮，992de277）：** 发现 4 个 HIGH、3 个 MED，已在本簇内修复：
- git 写失败在收集事实时被当作"事实未知"吞掉；
- 两个 runner 同时报故障会写出无法 fold 的事件流；
- lesson 可以注入多行 Markdown，临时文件可能被符号链接覆盖；
- 空的 carry plan 被当成"没有 plan"；
- 加锁时的 OSError 没归为审计区写不进；
- cap 记录漏报、误报，且重复写入；
- Q3 半截路径（等 XG 决定）。

**审查（第 2 轮，a1ef90c9）：** 3 个 HIGH、1 个 MED，已修复：
- runner 用的是不带生命周期校验的裸 store，`infrastructure_failed` 之后还能插进别的事件，事件流从此无法 fold。改为 runner 和 CLI 用同一个校验 store（`engine._store(producer=)`），由写触发事件的一方在同一把锁里签它的 transition（`transition` 的 producer 放开为 cli/runner，内容仍由 lifecycle 决定）。上一版"load 时补签"的做法和 `events.may_sign` 一并删除；
- decision 写回文档失败后没有恢复路径。签名事件是事实，文档只是投影：`check_context` 读需求和设计时先叠加已决定的 decision，每次 `stage-complete` 再投影一次（失败只记日志，基础设施错误照常抛出）；
- proposal 队列写文件会跟随符号链接。改为从项目目录 fd 逐级 `O_DIRECTORY|O_NOFOLLOW` 打开，`O_CREAT|O_EXCL|O_NOFOLLOW` 新建并 fsync；
- 两个并发的完整范围调用都能通过次数检查。改为每个 run 一把非阻塞锁（`_full_scope_slot`），第二个并发调用直接拒绝。

**审查（第 3 轮，83d984da，最后一轮，发现一次修完，不再复审）：** 2 个 HIGH、3 个 MED：
- REFLECT 先看 relation 再看 destination，`product` 或别的项目的 refines 会直接改本项目 DDD。改为先由 destination 定存储：只有目标是本项目 DDD 时才改条目，其余 relation 进待审队列并注明相关条目；
- run index 写失败被吞掉，完整范围调用不计数，上限可无限绕过。完整范围模式下写失败直接让调用失败；
- 已存在的 proposal 文件一律当成"已入队"。改为只有内容相同的普通文件才算重放，否则 `rejected_integrity`；
- Gate-0 两轮后仍未通过、第三次 `gate0-start` 被拒时都没写 `cap_exceeded.gate0_round`。两个出口都补上；
- contract、seals、run 列表的 JSON 写失败没有归为审计区写不进，create 失败还会留下占着 run id 的半成品。`AuditStore._atomic_json` 统一转成 `AuditUnwritable`；create 在 `run_create` 签名前失败时，只删除这次调用自己创建的目录。

**登记的偏差：** trivial profile 的精简 DESIGN_SPEC 没有 `chosen` 字段，decision 不写回它（事件里仍有，检查读事件）。runner 单元测试的合成 run 现在写了 `run_create` 起始事件；proof 关联测试把 finding 账本的 fold 排除在该合成 store 之外，finding 校验由 findings 和真实 E2E 测试覆盖。

## 15. Q3：删除跨 run 审查答案复用（XG 2026-10-23 选 c）

**结论：** 设计 §C6.2 的跨 run 复用不实现，登记为偏差。删除 `review.review_cache_key`、`ReviewHookBridge.record_reuse`、`answer_reuse` 事件种类，以及它在 events、lifecycle、findings、checks、review、engine、audit 里的分支和对应测试。

**依据（按五个维度重新评估）：**
- 命中条件几乎不成立。缓存键包含被审的整棵代码树和 prompt（专家说明加 `git diff base..tree`），跨 run 命中要求同一 base 上产出逐字节相同的树。修 finding、`land_conflict`、`final_stale` 之后树或 base 都会变；同一 run 内的重复 `review-start` 已由 `engine.review_start` 处理（已答跳过，无输出最多再派一次）。
- 跨 run 引用无法验签。每个 run 有自己的 `authority.key`，被复用的答案事件属于另一条签名链，验证它要跨 run 读审计区，破坏 Isolation。
- 原前提不成立。pipeline-expert 是 `model="inherit"`，写死模型会让 expert 不再跟随会话模型（Quality）；按 transcript 观察到的模型做键可行，但为一条几乎不执行的路径加缓存索引、并发冲突处理和审计计数，Token 收益约等于零。

**Token 计数随之简化：** 会话数 = 1 + handout 数。

## 16. 设计符合性修复 K4：文档和审计只从事件生成（含 Q3 删除）

| 项 | 改动 |
|---|---|
| F21 | `address` 事件带 hunk（强制 audit blob）、cause、proof。FIXING_SPEC 只读这些事件，不再写也不再读 `review-handoff-*.json`。hunk 的 blob 读不出时，文档写明"hunk missing"，并记一条 `render_failed` |
| F20 | REVIEW.md 只从事件和 check 注册表渲染：每次派发的 prompt sha 和解析状态；每条 finding 的 R2 状态、改分类及是否确认、证据事件；已记录的检查；审查检查的决定规则（卡阶段、卡落地、卡 push）。在审查各步骤、接受 finding、REVIEW/TEST 阶段完成和 DELIVER 的 REPORT 生成前重新渲染 |
| F22 | `run_audit.details` 新增 `critical_path`（每阶段的墙钟时间、扣除人工等待后的 active 时间、专家派发区间的并集、attempt/派发/测试调用次数；测试调用没有开始时间，记为 unmeasured）、`experts`、`waits`、`trace_matrix`。`trace_matrix` 由新模块 `core/run_protocol/trace.py::matrix(design, final)` 计算，维度为 R/P/F → task → exit → 各 repo 的结果，以后 NE-4 复用同一个函数 |
| F23 | Markdown 渲染失败只记一条 `render_failed`，身份为 (document, 所渲染快照的最后一条事件)，同一快照只记一次，不影响阶段判定。基础设施错误照常抛出。REPORT 不走这条路，由 DV-1 负责 |
| L5 | final 增加 `generated_at`，计入 record hash。auth 和 auth_consumed 不另加时间字段：签名事件的 `at` 就是授权和消费的时间，`session_id` 就是消费者（设计文字修订，见后续 commit） |
| L6 | REPORT 标题改为 run 标题：direct 用 contract.requirement 的第一行，其余用 requirements 的 understanding.claim，都没有时用 run id。所有不受信任的文字（标题、需求、人的备注、检查的详情）经过统一的纯文本编码：HTML 转义，并转义 Markdown 的强调、代码、链接、图片和表格语法 |
| §5 | `manual_rescues` 输出 null（这个计数没有生产者），不再参与 Autonomy 判定。删除 `validate_fixing_spec`、`render_fixing_spec`、`write_documents(fixing_spec=)`（没有生产调用方） |
| Q3 | 见 §15 |

**审查**
- **第 1 轮（f260a839）：** 1 个 HIGH、4 个 MED。
  - HIGH：旧日志缺 hunk 读不出。XG 决定不兼容旧 run，hunk 保持必填。
  - MED：blob 丢失被静默吞掉、追溯矩阵缺 task、critical_path 混入人工等待、标题可注入。四条都按上表修复。
- **第 2 轮（312aac8c，最后一轮，发现一次修完）：** 0 个 HIGH，4 个 MED。
  - REVIEW.md 漏了 Gate-0 的 VERDICT：Dispatches 表补上 Verdict 列。
  - blob 内容被替换后仍会被渲染：新增 `AuditStore.read_blob(digest)`，统一校验文件名和 SHA-256，不匹配按读不出处理。REVIEW/FIXING_SPEC、REPORT 拷贝、land 时读取的 REPORT、hook 的落地卡片都改走它。
  - 事件化后丢了 §B6 的 solution 和 evidence_plan：address 的条目接受 `solution`（文字）和 `evidence_plan`（{type, ref?}，type ∈ test/probe/diff_assertion/refuted），写入签名事件；FIXING_SPEC 分别渲染 Cause、Solution、Evidence plan 和 Hunk。
  - FIXING_SPEC 渲染失败被记成 REVIEW：两份文档分开渲染，各自记录失败。表格单元和模型写的文字统一经过 audit 的纯文本编码。

## 17. 收尾 commit 2：效率（r6 §2.1）

**测量方法：** 用 `$KIROCREW_SCRATCH/friction*` 的 rig 跑真实 run，每个配置一次，冷启动。机器负载约 1.5–3/核，墙钟时间波动很大，同一命令的两次测量最多差 70%；git 进程数是确定的，以它为准。

| | direct 改前 | direct 改后 | full 改前 | full 改后 |
|---|---:|---:|---:|---:|
| git 子进程（整个 run） | 293 | 201 | 389 | 223 |
| `status`（有 worktree 时）的 git 子进程 | 16 | 11 | 16 | 11 |
| `land` 的 git 子进程 | 70 | 33 | 70 | 33 |
| `stage-complete` 的 git 子进程 | 16 | 11 | 32 | 11 |
| `pipeline_test` 每次调用的 git 子进程 | 32 | 28 | 32 | 28 |
| `land` 墙钟时间 | 2.0 s | 0.8–0.9 s | 2.0 s | 1.1–1.4 s |

**改动：**
- **CodeFacts：** 每个 engine（也就是一个命令）对同一处代码只计算一次。键为期望的 repo、各 worktree、landing 状态和已落地的 commit；命令自己移动代码时（建 worktree、restore、落地）键会变，于是重算。changed paths 改为对 code_tree 做一次 `git diff base tree`，原先是三次调用的并集。tree 由私有 index 上的 `add -A` 得到，所以已提交、已暂存、未暂存和未跟踪的改动都在里面。
- **fork base：** 同一组 worktree 只解析一次。
- **base 的 validations.json：** 按 (repo, commit) 在进程内缓存解析结果，上限 256 条，返回副本。commit 的内容不会变，所以这个缓存不会过期。
- **`pipeline_test` 的 changed set：** `git diff <fork|HEAD>` 已经覆盖"fork 以来已提交"和"当前已暂存或未暂存"，再加一次未跟踪文件，共两次调用，原先三次。
- **删除：** `engine._changed_by_repo`，已没有调用方。

**没有做的（审查 f18dbe20 的 HIGH 指出了原因）：**
- 曾尝试两种做法：按文件字节缓存已验签的事件，以及让已验证过的 chain 跳过重验。
- 审查证明，原地修改返回的 dict 就能绕过验签。实测整条 56 条事件的链验签只要 1.2 ms，解析只要 0.2 ms，比 deepcopy 一次（0.4 ms）还便宜。
- 收益不到毫秒级，所以两者都撤掉：日志照旧每次全量读取并验签。
- r6 §2.1 里的 EventSnapshot 和批量 append 同样不做。理由是数据：一个 run 的事件读写在关键路径里只占毫秒级，时间都花在 git 进程和 pytest 上。

**预算：** `status` 的目标是不超过 150 ms，没有达到。它在有 worktree 时仍需 11 个 git 进程：写 tree 4 个、ls-files 1 个、fork base 3 个、diff 1 个、manifest 2 个，负载下约 250–450 ms。再往下压要改 `git_tree.write_tree`（4 个进程），留到 NE-0 的真实数据出来后再决定是否值得。

## 18. 收尾 commit 3：并发（r6 §2.2）

- **操作锁：** 新增 `core/run_protocol/oplock.py`，锁文件是每个 run 的 `operation.lock`，同一线程内可重入，等待超时报 `RunBusy`。
  - **拿锁的：** `pipeline_v1.py` 除 `status` 以外的所有命令（先拿锁，再在锁内 load engine，判断用的是锁内读到的状态），以及 daemon 里 hook 写答案的事务（answer 和它的 findings 作为一个操作，超时 60 s）。
  - **不拿锁的：** `pipeline_test` 跑测试的过程，它每次 append 时只拿 store 锁。
  - **效果：** 并发的 CLI 调用按顺序执行，后到的一方按新状态被正常判定，不会读到旧状态后撞号报错。
- **ordinal：** `append_new(ordinal=NEXT)` 在 store 锁内按链长计算。原先 16 处 `len(read_events())+1` 是在锁外算的，全部换成 `NEXT`。finding_occ 和 finding_status 用的是答案里的行序号，不在这 16 处之内，保持不变。
- **投影的最后一行写明来源：** REQUIREMENTS 和 DESIGN_SPEC 写 JSON source 的 sha256，REVIEW 和 FIXING_SPEC 写事件数和最后一条事件，方便判断文件是否过期。投影的写入都在操作锁内，所以不再需要"同 count 不同 tail 就拒绝覆盖"的比较。

**审查 f227e2ed（1 轮，2 HIGH + 2 MED），一次修完：**
- **HIGH，`RunBusy` 会丢掉 reviewer 的答案：** 其他持锁方都是 CLI 调用，每次最多活一个 Bash 调用的时长（不超过 600 s），flock 又随进程死亡释放。所以 hook 等锁的上限改为 900 s，这次等待总能拿到锁。`_pending` 改为答案落盘之后才删。
- **HIGH，answer 和 findings 不是原子写入：** 新增 `AuditStore.append_batch`，在一次 store 锁内逐条验证，然后一次 write、一次 fsync 写入。另在检查层兜底：一个 parsed answer 只有在它声明的 `n_findings` 条 `finding_occ` 和 `n_status` 条 `finding_status` 都在账本上时才算数（`checks._answers`），否则 RV 检查失败。
- **MED，来源行与正文不是同一次读取（TOCTOU）：** `write_review` 和 `write_fixing_spec` 只读一次事件，正文和来源行都用这一份。REQUIREMENTS 和 DESIGN_SPEC 的哈希取自刚写入的那段文本。
- **MED，`operation()` 先建路径、后校验 run_id：** 入口先调 `validate_run_id`。

## 19. 收尾 commit 4：衔接（r6 §2.3）

- **`next`：** 新增 `core/run_protocol/next_step.py`。`pipeline_v1.py` 和 `pipeline_test.py` 的每个响应都带 `state`、`repos` 和 `next`。
  - `next` 的字段有 `action`、`command`、`why`、`blocking`、`card`。`action` 取值为 run、write_artifact、fix、dispatch、wait、ask_user、none。
  - 当这次调用让 run 进入了新阶段时，`next` 还带上该阶段 card 的全文（`card_text`）。
  - `next` 只读签名事件的 fold、engine 算出的检查结果、run 自己的文件和 stage card。`blocking` 由 `checks.evaluate` 计算，它是纯函数，不写任何事件。
- **删除 `stage-start`：** 改为 `engine.ensure_stage_worktrees()`，幂等，只在真建了 worktree 时才重写 run.json。
  - 每个写类命令结束时都会调它，所以进入代码阶段的那次调用会顺带建好 worktree。direct 的 `create` 返回时 worktree 就已经在了。
  - 如果在进入阶段和建 worktree 之间崩溃，下一次 `status` 会在操作锁内补建。
  - `worktree_write_gate` 的拒绝提示改为：先 `create`，再按 `next` 走；缺 worktree 时调 `status`。
- **只读命令：** `status`、`push-status`、`events` 不拿操作锁，也不再写 `transcripts.json`。这个文件只在写类命令里、锁内写入。
- **自动完成阶段：**
  - **触发条件：** `pipeline_test.py` 的 tdd、probe、regression、verify、recheck、validation 调用写完 final 之后，如果 run 处于 BUILD 或 TEST，并且该阶段的检查全部通过，就完成这个阶段。
  - **怎么完成：** 由 CLI 自己的 `stage-complete` 子进程完成（它以 cli 签名，并拿操作锁）。runner 进程只签 runner 的事实（C3），而且这时测试已经跑完，不存在持锁跑测试。
  - **检查没过时：** 不记录任何 stage attempt，`next.blocking` 列出没过的检查。TDD 先红后绿的过程因此不会耗掉阶段的尝试次数。
- **`--artifact -`：** 从 stdin 读。文档把 JSON source 存进 run 目录，事件则在 payload 里保存内容，所以不再另存一份。
- **usage：** 删除 `measure-usage` 子命令。daemon 在 `reflect_complete` 时从 run 的 transcript 测量用量。测不到时记一条 `metric(usage_not_available, reason)`，Token Cost 判 WARN（原先是 BLOCK），并在 `not_available` 字段写明原因。
- **`RunEngine.load(producer=)`：** runner 进程以 runner 身份加载 run，只读出下一步要做什么。
- **文档：** INSTRUCTIONS、SKILL 和 BUILD/TEST 的 stage card 都改为按 `next` 走。

**审查 4cfba9d6（1 轮，3 HIGH + 6 MED），一次修完：**
- **HIGH，TEST 第一次 regression 就会被自动完成，必需的 validation 被跳过：** 原因是 TS-1 对任何结束了的 final 都放行。现在 TEST 自动完成多了两个条件：每个 repo 的 final 都是 green，并且 LG-5 通过（所有必需 validation 都已跑过）。两项都写进 `next.blocking`。所以 red 或 incomplete 的 regression 不会结束 TEST，agent 可以修完再跑；也可以按 TS-1 手动完成。
- **HIGH，两个并发的 runner 会连着完成 BUILD 和 REVIEW：** 自动完成改为带 `--expect-stage <观察到的阶段>`。`stage_complete(expected_stage=)` 在操作锁内比对，阶段已变就报 `stale`，不再把这次完成算到新阶段头上。
- **HIGH，非 owner 会话能借 `pipeline_test` 推进别人的 run：** 现在只有本次 Bash 调用的 `SWARM_APP_SESSION_ID` 等于 `owner_session()` 时才自动完成。这个环境变量由 `session_link_gate` 保护，不能被改写。其他会话的测试调用只返回 `next`，不推进 run。
- **MED，`user_stop`/`session_lost` 暂停后 `next` 会让 agent 直接 resume：** 改为 `wait`，由人来决定是否恢复。
- **MED，REVIEW 的 handout 按阶段尝试次数取，编号用错了：** 改为按 (expert, round) 取最新的 handout。有未回答的就 `wait`（本次调用刚派发时为 `dispatch`）；全部答完且检查通过就 `stage-complete`。`dispatched` 由 engine 本次创建的 handout 数给出，roster 为空时直接指向 `stage-complete`。
- **MED，DELIVER 用 `reflect.json` 是否存在来判断：** 改为看签名事件：存在一次只含 RF 检查、且全部通过的 deliver attempt，或 reflect 已 degraded。
- **MED，GOAL_CYCLE 被指向 regression：** 改为 tdd，`why` 也不再说它会自动完成。
- **MED，已删除的命令仍在文字里出现：** 删掉了 INSTRUCTIONS 中的 measure-usage 一段，runner 缺 worktree 时的报错也不再提 stage-start。
- **MED，只读命令的 `next` 会写 git 对象，未修：** `write_tree` 用私有 index，写出的是内容寻址、没有 ref 指向的对象，可被 gc 回收，不改变任何 run 状态，也不需要锁。`status` 原本就这样做。
