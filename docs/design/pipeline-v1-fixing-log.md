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
