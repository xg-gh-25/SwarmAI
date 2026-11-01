---
title: Pipeline 交付摩擦 — 审查预算随规模、覆盖可见、单一输出契约、文档分类统一
created: 2026-11-01
updated: 2026-11-01
project: SwarmAI
status: r3 — XG 定稿（native 实施，「DELIVER / REFLECT 别整太严」）；已实施，见 §6
---

# Pipeline 交付摩擦

## 0. 一句话

run_dab63338（35 项、248 个文件、约 2.7 万行）跑通了，但暴露出 pipeline 在大改动上结构性失效：审查专家的工具预算固定 24 次、不随 diff 规模变，审不完也算审完且无人知道；专家 prompt 自带一套与引擎契约冲突的输出格式；"什么算文档"有三处定义（其中 `.txt` 把 `requirements.txt` 当成文档，另一处写死 SwarmAI 的目录）；基线上就红的测试没有归处；文档形状只写在代码里。本设计按根因各给一个结构性改法；单个 run 的规模本身靠 PLAN 时的拆分提示控制。

## 1. 现状与根因（2026-11-01 读源码核对，HEAD `92f81c531`）

| # | 现象（run_dab63338） | 根因（源码） |
|---|---|---|
| G1 | 两位专家都在额度用完前只审到高风险后端；前端 1643 处文案迁移、`lib.rs`、CI、系统文本基本没审 | `engine.review_start`（`core/run_protocol/engine.py:2861-2924`）每位专家一份 handout，内容是整份 diff；工具上限固定 `EXPERT_TOOL_CAP = 24`（`core/pipeline_expert_agent.py:41`），由 `create_expert_budget_gate` 按 agent_id 计数（`:123-150`）。27k 行的 PROMPT-FILE 光分页读就要十几次 Read |
| G2 | 审不完也被记为一次完整审查，审计看不出 | `hook_bridge._refusal` 只验证 PROMPT-FILE 从头读过（`core/run_protocol/hook_bridge.py:457-483`、`core/subagent_transcript.py:121-129` 的 `read_ok`），不看读了多少、是否撞上工具上限；RV-1a 是 `len(rows) >= len(handouts) and any(parsed)` 且只是 audit（`checks.py:142`、`:390-398`） |
| G3 | 专家回答要靠 handout 里的契约纠正 | 每个专家 prompt 都自带输出格式 `{"severity","confidence","path","category","summary","specialist"}`（`backend/experts/code/correctness.md:25-33`，concurrency / integration / api-contract / performance / state-machine 同样），与引擎的 `OUTPUT_CONTRACT`（`core/run_protocol/review.py:33-39`：`kind/file/claim`）冲突，`answers._finding` 拒收未知键；native 的 `s_expert-review` 又要第三种格式（`skills/s_expert-review/INSTRUCTIONS.md:66`）。预算 gate 还叫专家写 "verdict PARTIAL"（`pipeline_expert_agent.py:143-145`），而 REVIEW 回答带 VERDICT 会被判 unparsed（`answers.py:188-189`） |
| G4 | "文档"判断不一致，`.txt` 被当成文档 | 三处定义：`engine._DOC_SUFFIXES = (".md", ".txt", ".rst")`（`engine.py:58`，用于 `:985`、`:2495`、`:2881`、`:3172`）；`review.reviewers` 内联同样元组（`review.py:583`）；`core/doc_paths.is_doc_only_path`（`.md` + 写死的 `backend/context/`，除测试外无调用者，P7 违规；`lifecycle.REMOVED_PROFILES["docs"]` 的文案还引用它，`lifecycle.py:112-115`）。`.txt` 后缀让只改 `requirements.txt` / `CMakeLists.txt` 的 run 被当成纯文档、不派 correctness |
| G5 | 8 个前端测试在 base 上就红，只能手工 `git archive` 基线对照，再标 `not_run` | `validation_report.RESULTS = ("pass", "fail", "not_run")`（`validation_report.py:29`）：没有"跑了、和 base 一样红"的结果，它和"没跑"混在 REPORT 的同一类里 |
| G6 | requirements / design_spec / handoff / reflect 第一次提交多次被拒 | 形状只在代码里：`schemas.validate_requirements`、`validate_contract`（`schemas.py:298`、`:578`）、`validation_report.parse`（`:101`）、`engine._handoff_problems` / `_resolve_reported_tests`（`engine.py:1307-1308`）、`reflect.prepare`；需求上限是 `engine.REQUIREMENT_MAX = 4000`（`engine.py:315`）。卡片（`skills/s_autonomous-pipeline/stages/*.md`）只写规则名，不给样例 |
| G7 | 落地前几十次提示"主 checkout 变了"，几乎都是另一个 session 的文件 | `main_dirty_scan._scan`（`core/main_dirty_scan.py:126-195`）报告自上次调用以来主 checkout 里变动的文件，不区分归属；"哪个 live session 写过哪些文件"的事实在 `shared_checkout._live_contexts` / `_files_written`（`core/shared_checkout.py:52`、`:111-141`） |
| G8 | 一个需求塞进一个 run，改到 248 个文件 | `lifecycle.REMOVED_PROFILES["goal"]`（`lifecycle.py:110-111`）和 AGENT.md § Pipeline Profile 都写"大需求是一个 full run，PLAN 列出每部分"，没有规模提示；PLAN 完成时不看任务涉及多少文件 |
| G9 | 每个 BUILD 子 agent 都撞 "WRONG PYTHON" | 仓库自己的冲突：`.swarmai/pipeline.toml` 声明把 `backend/.venv` 软链进 worktree，`backend/tests/conftest.py:33-36` 用未解析的 `sys.prefix` 与 `realpath(.venv)` 比较；AGENTS.md:531 记的是绕过办法 |

## 2. 设计

### D1 审查预算随 handout 规模（G1）

- 专家的第一次工具调用总是 Read 它的 PROMPT-FILE（`_refusal` 已要求从头读）。`create_expert_budget_gate` 在看到某个 agent_id 第一次 Read 一个 `pipeline_agent_audit/runs/*/blobs/*` 文件时，按该文件的行数给这个 agent 定上限：`cap = EXPERT_TOOL_CAP + ceil(lines / READ_PAGE_LINES)`，`READ_PAGE_LINES = 2000`（Read 一页的行数），封顶 `EXPERT_TOOL_CAP_MAX = 64`。之后照旧按次数计。
- native 的 `s_expert-review` 不读 blob，仍用 24；它的 diff 由 agent 自己按改动范围给，本来就小。
- 这仍是一个计数器（P5），只是把"读 prompt 本身的成本"从核实预算里拿出来。不改 handout、事件 id、审查轮次——r1 的分片方案会破坏这三样（见修订记录）。
- 封顶 64 时一份 prompt 最多约 8 万行；更大的改动由 D3 在 PLAN 时提示拆分，D2 让审不完的部分可见。

### D2 覆盖情况用确定性事实记录（G2）

- `subagent_transcript.spawn_evidence` 增加两项：`prompt_lines_read`（对 PROMPT-FILE 的所有 Read 覆盖到的行号区间之并，按 offset/limit 和 tool_result 行数算）和 `tool_calls`（该专家发出的工具调用数）。
- `hook_bridge.record_answer` 把它们连同 prompt 文件总行数写进 answer 事件 payload：`prompt_read_fraction`、`tool_calls`、`budget_hit`（`tool_calls > cap`）。
- 新审计检查 RV-6（effect `audit`，不挡 stage、不挡落地，与 RV-2 同类）：任一专家 `prompt_read_fraction < 1` 或 `budget_hit` 时为 WARN，Quality 一格写"review coverage: correctness read 46% of its prompt"；REPORT 新增一节列出每位专家的覆盖。
- 不解析专家自己写的 `UNCHECKED`（三种写法，且是模型自述）；不自动补派（无终点的模型循环，P5、P10）。

### D3 PLAN 时的规模提示（G8）

- PLAN 完成时，engine 数 DESIGN_SPEC 任务涉及的文件数和顶层目录数；超过 `SPLIT_HINT_FILES = 40` 且跨 ≥ 3 个顶层目录时，`next` 带一条不阻断的提示：按任务文件算出哪几组任务彼此没有共同文件、可以各自落地，建议 `abandon` 后按组新建 run。
- `lifecycle.REMOVED_PROFILES["goal"]`、EVALUATE 卡片和 AGENT.md § Pipeline Profile 的"一个需求一个 run"改为：彼此不共享文件、各自可落地的部分分开跑；共享契约的部分留在一个 run。
- 提示晚于 EVALUATE/THINK，代价是那两步的少量 token；PLAN 是第一个知道文件范围的时刻。

### D4 单一输出契约（G3）

- 专家 prompt（`backend/experts/code/*.md`）删掉各自的 "Output:" 段和示例 JSON（例子中的发现内容改写成该类问题的文字描述），只讲审什么。
- 输出格式只由调用方给：pipeline 是 `review.OUTPUT_CONTRACT`（handout 里已有）；native 是 `s_expert-review` INSTRUCTIONS 的那一行。
- 预算 gate 的提示改为"写 report，未覆盖的写进 report"，不再要求 `verdict PARTIAL`。
- 结构测试：任何 expert prompt 不得包含输出格式（`"specialist"`、`"confidence"`、`Output:` 段）。

### D5 一个文档分类（G4，含一处 P7）

- `core/doc_paths.py` 改为唯一定义 `is_doc_path(rel)`：`*.md`、`*.rst`，不含 `*.txt`。`engine._DOC_SUFFIXES` 的四处、`review.reviewers` 的内联元组都改用它；删掉 `is_doc_only_path` 和写死的 `backend/context/`。
- `lifecycle.REMOVED_PROFILES["docs"]` 的文案同步改（不再提 `backend/context/`）。
- 不提供仓库自定义文档路径：SwarmAI 的 `backend/context/**` 是注入 prompt 的行为文件（`pipeline.toml [hive_return] protected` 也这样看待），应当被审查。

### D6 基线上就红的检查（G5）

- `validation_report` 新增结果 `pre_existing`：命令跑了，在本 run 的 base 上以同样的失败条目失败。必须写 `reason`；engine 在事件里记上 run 的 base commit。TS-1/LG-1 与今天的 `not_run` 一样不挡；REPORT 把它和 `not_run` 分成两节，Quality 一格分别计数。这是 agent 的陈述，REPORT 如实写明"由 agent 报告，未经核实"（TEST 在 REVIEW 之后，没有专家看 TEST 报告）。
- 新命令 `pipeline-v1 base-tree --repo <name>`：把 run 的 base commit 用 `git archive` 解到系统临时目录 `<tmp>/swarmai-base/<run_id>/<repo>`（不在 SwarmWS、不是 git worktree），按 `[worktree] link` 建软链，打印路径；同一 run 再调用复用已有目录。run 收尾（land / abandon / release）时 `run_worktree.release` 删除这个目录；系统重启清临时目录。

### D7 文档形状有样例（G6）

- 新命令 `pipeline-v1 template <kind>`，kind ∈ `requirements`、`requirements-lite`、`design_spec`、`design_spec-lite`、`validation_report`、`review_handoff`、`review_proof`、`reflect`，打印最小合法样例；需要 run 里真实 id 的地方用 `<CF-…>`、`<anchor>` 这类占位符并在样例旁注明从哪个命令取。
- 样例放在 `core/run_protocol/templates/`；测试在验收矩阵的 rig（`tests/test_pipeline_v1_acceptance_matrix.py` 的 `Rig`）里建一个真 run，把每个样例（占位符替换成 rig 里的真 id）走一遍 CLI 的真实入口（`stage-complete`、`report-tests`、`review-address`、`review-proof`、`reflect-prepare`），全部通过。schema 一改样例就红。
- 每张需要 artifact 的卡片加一行"形状：`pipeline-v1 template <kind>`"；各入口的校验报错末尾附同一行。超过 `REQUIREMENT_MAX` 的报错改为"把完整清单写成文档，在需求里给出路径"。

### D8 主 checkout 提示按归属过滤（G7）

- `main_dirty_scan._scan` 在报告前去掉这样的路径：另一个 live session 写过（`_files_written`）、git 仍报告未提交、且不在本 run 的改动路径里。这些是有主的并行工作，与本 run 无关；本 session 自己误写进主 checkout 的文件（无主，或与 run 重叠的）照报。
- "still changing" 行保留（原设计 2026-10-26 §5.2 的决定），只对过滤后剩下的路径。

### D9 仓库自己的 venv 守卫（G9，修在仓库，P7）

- `backend/tests/conftest.py`：比较 `os.path.realpath(sys.prefix)` 与 `realpath(.venv)`。AGENTS.md:531 的绕过说明删掉。

### E1 REFLECT 宽松（XG：别整太严）

- `reflect.prepare_lenient`：每条合法的 unit 保留，不合法的（形状、section、证据不属于本 run、关闭的 gap 不是 open）丢掉并在回复的 `dropped` 里写明原因；缺的 section 当空。只有旧的 lesson 列表整份拒收。RF 复查用的严格 `prepare` 是同一套校验加 raise。

### E2 main 只动了与 run 无关的文件时，TEST 报告保留

- 落地同步时，main 的新 commit 与 run 改动的文件没有交集、rebase 干净（patch 不变），同步前那份 TEST 报告继续有效：LG-1 读 `run["carried_test_trees"]`（engine 由两组 git 路径算出）。REPORT 写明"main 移动后没有重跑测试"。有交集仍按旧规则重测。这改了 2026-10-26 §5.2 第 2 点"main 动了就重测"，依据是 XG 的要求和 P5（测试由 agent 汇报，push 由人决定）。

### N1 main 与 run 冲突不再让 run 走向终止（swarmws-46 run_3fe60e51 的证据）

- 新的合并冲突（main 动进了 run 的行）计入 main 移动的计数（`BASE_MOVED_CAP`），不计入 run 自己的 3 次落地失败；一直没解决的冲突仍是 run 自己的（新 cause `conflict_unresolved`，3 次 `land_conflict`）。
- 本次落地 rebase 到了新 main、唯一问题是 TEST 报告不在新 tree 上（解决了冲突、或报告本就旧）→ `base_moved`：重测，不算 run 自己的失败。失败的报告不算。
- DELIVER 退回 REVIEW 一次之后，再出现与已审改动的差异时直接落地，LG-1 说明，REPORT 和 Quality 写"没有审查看过这部分差异"（P5：没跑的审查记录下来，不用来挡落地）。`next` 不再说"恢复已审代码"。

### N2 main 与 run 文件重叠提前说

- REVIEW / TEST 阶段 `next` 带 `main_overlap`：main 自 run base 以来的 commit 也改了 run 的哪些文件。只是信息，不挡。

### N3 手工 commit 在同步时折回

- `run_worktree.sync` 在 rebase 前把 run 分支上、base 之后手工做的 commit 用 `reset --soft base` 折回改动（文件内容不变，commit 仍在 reflog），不再报 "worktree HEAD is not the run's base"。commit gate 拒绝 run worktree 里的手工 commit 由 swarmws-46 做（35e9deb26）。

## 3. 不做

| 项 | 原因 |
|---|---|
| 审查按 diff 分片（r1 的 D1） | handout 事件 id 是 `(stage, round, expert_id, attempt)` 的哈希（`events.py:90`、`:509`），`first_round_series` 按 `(expert_id, code_trees)` 分组（`review.py:161-177`），同一专家的多片会互相当成重问，破坏去重、重问上限和 `review_phase`；且 27k 行要 18 片，cap 内仍派不完。预算随规模（D1）+ 拆 run（D3）+ 覆盖可见（D2）达到同样目的，不动协议 |
| 测试报告容忍纯文档差异（r1 的 D5；E2 只在 main 的改动与 run 文件无交集时保留报告，与文件类型无关） | 推翻已定决定"落地不放松 LG-1"（`2026-10-26-pipeline-review-closure-and-visibility.md:164`、`:246`、`:255`）；文档类文件也可能改变行为（测试读 `backend/context/**`）；main 移动已有 `base_moved` 机制（`lifecycle.py:46`） |
| worktree 写入 gate 对 heredoc 的误判 | `gate_decisions.jsonl` 里该 gate 共拒 36 次，heredoc 4 次；拒绝信息已给出路。度量不支持改解析（P5） |
| 解析专家写的 `UNCHECKED` | 写法不一、是模型自述；D2 用 transcript 的确定性事实 |
| 需求 4000 字上限 | 保护 recall 查询和专家 prompt 的体积；改的是报错指引（D7） |

## 4. 验证计划

| 改动 | 测试（先红后绿；回退修复变红） |
|---|---|
| D1 | `tests/test_expert_budget_gate.py`：首个 Read 指向 4000 行 blob 时上限 26，指向 20 万行时封顶 64；非 blob 的首个 Read 保持 24；超过上限后拒绝 |
| D2 | `tests/test_subagent_transcript.py` + `tests/test_run_protocol_review_coverage.py`：分页 Read 的区间并集算对；只读一半的 transcript 得 0.5；RV-6 在不完整 / 撞上限时 WARN、REPORT 列出；完整时 PASS |
| D3 | `tests/test_pipeline_v1_split_hint.py`：40 个文件跨 3 个顶层目录时 `next` 带提示并给出无共同文件的分组；否则没有；stage 结果不变 |
| D4 | `tests/test_expert_prompts_single_contract.py`：`experts/code/*.md` 无输出格式；pipeline handout 仍含 `OUTPUT_CONTRACT`；预算 gate 文案无 VERDICT |
| D5 | `tests/test_doc_paths.py`：`.md` / `.rst`（含 `backend/context/x.md`）是文档，`requirements.txt`、`CMakeLists.txt`、`backend/context/x.py` 不是；结构测试：`run_protocol/` 无 `_DOC_SUFFIXES` 或内联后缀元组；只改 `requirements.txt` 的 run 派 correctness |
| D6 | `tests/test_run_protocol_report_pre_existing.py` + `tests/test_pipeline_v1_base_tree.py`：`pre_existing` 要 reason、不挡、REPORT 单列；`base-tree` 解出 base、软链按声明、run release 后目录被删 |
| D7 | `tests/test_pipeline_v1_templates.py`：每个样例在 rig 里经真实入口通过；`template` 输出与文件一致；校验报错带模板提示 |
| D8 | `tests/test_main_dirty_scan.py`：另一 live session 写过、仍脏、不在 run 改动里的路径不报；与 run 重叠的照报；无主路径照报 |
| D9 | 仓库自己：软链 venv 下跑一个测试文件不退出 |

最终测试集：上表全部 + `test_pipeline_v1_acceptance_matrix.py`、`test_run_protocol_review.py`、`test_pipeline_expert_agent.py` 等读审查、答案、报告的现有测试。审查：correctness + security（改的是审查预算和落地判断本身）。

## 5. 实施方式

native（XG 2026-11-01 定），按 §4 先写测试，每项 mutation check（回退修复测试变红），最后 correctness + security 审查。

## 6. 实施记录

| 改动 | commit |
|---|---|
| D1 D2 D5 | f00b96005 |
| D4 | 025cd2e50 |
| D9 | 67a0c9b4f |
| E1 E2 | bc943e047 |
| N1 N3 | b005ee2d5 |
| N2 | 01cee171b |
| D6 | 5fddd4929 |
| D8 | 1f1acae8f |
| D7 | 178bd2de9 |
| D3 | d5fc87dd7 |

## 修订记录

- r1（2026-11-01）：初稿。
- r2（2026-11-01，零上下文审查 4 HIGH / 4 MEDIUM 按原因改）：撤掉 diff 分片（破坏事件 id、审查轮次分组和重问上限，且大 run 仍派不完）→ D1 改为预算随 PROMPT-FILE 规模、D2 覆盖改用 transcript 的确定性事实；撤掉"报告容忍纯文档差异"（推翻已定的 LG-1 决定，文档类也可能改变行为）；文档分类去掉 `.txt`、不提供仓库自定义路径；`base-tree` 放到系统临时目录并随 run release 删除，不进 run 目录（SwarmWS 会提交它）；D7 的样例改为在 rig 里走真实入口验证（r1 写的 validator 名字不对）；D8 改为只滤掉"别人写的、仍脏、与 run 无关"的路径，保留 still changing 行；新增 D4：专家 prompt 的输出格式与引擎契约冲突（审查员指出的漏掉的根因）。
- r3（2026-11-01）：XG 定稿 native 实施并要求 DELIVER / REFLECT 别太严 → 新增 E1、E2；并入 swarmws-46 从 run_3fe60e51 / run_ee603ceb 带来的 N1–N3（N3 的 gate 部分、not_landed 后的 worktree 与恢复提示由它实施）；实施记录 §6。
