# SwarmAI -- Technical Context

> **Full architecture doc:** `Projects/SwarmAI/assets/SwarmAI-Architecture-Design-Doc.md` (19 pages, 13 sections).
> **Regenerate PDF:** `python3 Projects/SwarmAI/assets/generate-arch-doc.py`
> This TECH.md is the DDD summary — see the full doc for diagrams, competitive positioning, and design rationale.

## Architecture
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 21 | trust: full | promoted: 2026-07-01 -->
- [decision] **Two INDEPENDENT git repos both push to `xg-gh-25/SwarmAI` — know which tree you're in before ANY git op** (2026-07-22) — There is NO shared history between them (`git merge-base` fails); they are separate clones that both configured the same push remote. **(1) SOURCE repo** `/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/` — the code publisher: `backend/` + `desktop/` live here, this is where you edit code, run pipelines, and where the public product commits originate (origin/main is a fast-forward ancestor). **(2) WORKSPACE repo** `~/.swarm-ai/SwarmWS/` — the daemon's live cwd: recall/cultivation/provision read+write HERE, has 0 `backend/` files, and its history is auto-hook `content/chore/framework` commits (never a code publisher). The sample DDD ships from the SOURCE tree's `Projects/SwarmAI/`; the six-section migration originally happened ONLY in the WORKSPACE tree, so the two `Projects/SwarmAI/` copies diverged until synced (commit 751d21db). **Operational rule (the reason this entry exists):** before any `git rm`/`git mv`/`git status`-judgment on a `Projects/SwarmAI/` path, FIRST confirm which tree you're in (`git rev-parse --show-toplevel`) and resolve files by ABSOLUTE path — a bare `git rm` nearly ran in the wrong (SOURCE) tree this session while the real change was in the WORKSPACE tree (C040 tree-confusion class). Filename/path similarity across the two trees ≠ same file (different inodes).
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [decision] **DDD classification (none / external / internal) is DERIVED-ON-READ from `bindings.yaml` — never stored as a separate field** (2026-07-12, run_2acb67e1) — Classification is NOT a project attribute; it's a **function of the binding set**: no `bindings.yaml` → `none` (no-repo, pure-DDD); any binding `kind:internal` → `internal` (Brazil/CRUX); else `external` (GitHub-PR). `classify_project(project_dir)` in `ddd_bindings.py` is the SSOT reader (fail-safe: catches FileNotFoundError + YAML parse errors → `none`, since it's on the `_recall_ddd` hot path). `sync_internal_provisioning()` in `swarm_workspace_manager.py` is the BIND-time trigger — it reads `classify_project` and, if internal, fires `provision_project_ddd(internal=True)` (idempotent, `not dst.exists()`-guarded) so internal skills+gate land WHEN repo kind becomes known, not guessed at CREATE. The create-time `internal` flag now defaults False with no true-passing caller. **Why derive-on-read, not a stored `repo_kind`:** a stored classification is a SECOND source of truth that drifts vs `bindings.yaml`; Gate-0 killed the initial plan to add a `.project.json` `repo_kind` field precisely because it would have re-created the very drift bug the change fixes. BIND is skill-prose orchestrated (`s_ddd-manager` SKILL.md calls the sync), not a code hook — by design.
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [decision] **Dead-waiter WAITING_INPUT recovery keys on `has_live_waiter` (real liveness), NOT `_pending_tool_use_id` presence** (2026-07-02, run_65f317db) — `has_outstanding_tool_use` (=`_pending_tool_use_id is not None`) is ALSO the drain-worker guard (session_router.py:1463) and must NOT be weakened. So the deadlock-recovery predicate uses a SEPARATE `_has_live_outstanding_waiter()` that consults the actual waiter managers (`permission_manager.has_live_waiter` / `ask_question_manager.has_live_waiter`, respawn-immune: registered on wait-entry, popped in finally), disambiguated by `_pending_question` shape. Two distinct notions on purpose: **flag-presence** (is a tool_use open? → drain guard) vs **liveness** (is a hook actually blocked to receive the decision? → recovery guard). Rejected weakening the shared property (would break the drain consumer). This is the reap predicate behind `reap_dead_waiting_input`, called from send() + lifecycle tick + approve endpoints.
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [decision] **Backend stability failures = Cluster A (one fragile shared resource), not N independent bugs** (2026-06-30)
  <!-- ref:0 | last:none | decay:active | source:manual -->
  Root-cause clustering (3-agent forensics + code-model audit) found the recurring backend failures — `zombie_via_error`, `tool_call_leak`, `output_liveness_timeout`, `exit-9` cascade — are all faces of ONE structure: **a single SDK subprocess + its single `receive_response()` stdio channel, contended by ~26 kill paths + ~8 recovery mechanisms, with kill decisions lacking a single chokepoint (the PID watchdog bypasses `self._lock`), and `--resume` replaying the poisoned transcript.** OT01 (frontend reconcile race) is a SEPARATE cluster B (React stale `streamGen`) — do NOT conflate; mixing them is part of why OT01 took 33 patches.
  **Direction for stability work (load-bearing judgment):** prefer **"elimination-layer" structural fixes** — converge resource access to a single authority, or make the shared primitive idempotent — over adding more watchdogs/recovery. 8 recovery mechanisms + 26 kill paths is already *over-recovery*; they share state and interfere. STEERING #1 (structural prevention > watchdog) operationalized for this subsystem.
  **Key correction (earned this run):** the intuitive Cluster-A fix "converge all 26 kill paths to a single `_lock` chokepoint" is **WRONG** for the watchdog — a lock-free safety watchdog routed through `_lock` would head-of-line-block behind a streaming turn that holds the lock → the watchdog could never fire → defeats its purpose. The correct layer is **idempotency in the shared primitive `_force_kill`**, watchdog stays lock-free. (Gate-0 M3 skeptic falsified the chokepoint frame before any code — Understanding Gate is highest-ROI when changing the session spine.)
- [decision] **Eval Golden-Case Policy — "a case must WORK, not just PASS" (the eval system's reason to exist)** (2026-06-30)
  <!-- ref:0 | last:none | decay:active | source:manual -->
  The golden set exists to **detect real degradation in the agent's cognition** (judgment/compliance/capability/recovery), NOT to accumulate green checkmarks. **"Case passes" ≠ "case works".** A case that passes while the agent genuinely LACKS the tested knowledge — because the LLM judge is fed the answer + resolved context and rules on general knowledge — is a **negative asset**: it paints green over a blind spot and gives false confidence. **If a case doesn't work, we don't want it** (fix or RETIRE; never surface-fix to green).
  **Every case must earn its place on 5 axes:**
  1. **Refs resolve** — `source`/`affected_by` → non-empty, correct, on-topic content. (Drift seen: `MEMORY.DEC16` held RSS thresholds, reshuffled → dark-theme, so the case was silently fed wrong context; `STEERING.RX` refs resolve EMPTY after rules moved to AGENT.md in the 2026-06-27 reorg; the `EVOLUTION.` prefix isn't handled by the resolver.)
  2. **Reflects current truth** — assertions match how the system/governance ACTUALLY behaves NOW. A case asserting the OPPOSITE of a current rule is a **bug in the case** (e.g. GS_ACT003 asserted "after push, watch CI" — contradicts AGENT R6 "CI is NOT the verification venue").
  3. **Has teeth (non-circular) — TWO-TOOTH MODEL (key eval-design insight, 2026-06-30).** A case can carry two INDEPENDENT kinds of teeth, with different failure modes and different detectors. Conflating them caused a real misjudgment (audited 27 re-anchored llm cases for knowledge-tooth only → wrongly flagged 17 as "toothless/useless"; they were fine, they carry the OTHER tooth). **Never judge a case toothless on a single tooth type.**
     - **3a. drift-tooth** — the case is pinned to a SPECIFIC live anchor (MEMORY entry / AGENT rule / SOUL principle / DDD section via `affected_by`). Its job: FIRE when context files drift — a rule renamed/moved/deleted (e.g. the 2026-06-27 STEERING→AGENT reorg) makes the ref stop resolving → case goes RED. Answers *"2 weeks from now, after MEMORY/KNOWLEDGE/DDD/governance files change, does this case still point at real, correct content?"* Detector = **gate_refs** (implemented + enforced; it FIRED for real — 27 cases went RED at the reorg, which triggered the whole re-anchor run). EVERY ref-bearing case has this tooth; it does NOT need the judge.
     - **3b. knowledge-tooth** — if the agent genuinely LACKED the tested knowledge, the judge would FAIL. Answers *"does this test the agent's cognition, or is it circular (judge fed the answer)?"* Detector = **per-case ablation** (knock the case's knowledge out of judge context, expect RED) — NOT yet built for llm cases, because the judge's `_load_rules_context()` UNCONDITIONALLY injects full STEERING + SOUL principles + AGENT Rules. So a case whose `affected_by` points ONLY at `AGENT.RN`/`SOUL.PN` CANNOT have a verifiable knowledge-tooth (globally injected → can't ablate per-case). Cases whose knowledge lives in `MEMORY.`/`EVOLUTION.` (NOT globally injected) CAN.
     - **Design rules:** (1) a weak/absent knowledge-tooth does NOT make a case useless if its drift-tooth is strong — the 17 `AGENT/SOUL`-ref compliance cases (`GS_CMP*`, `GS_REF00x`) are **drift sentinels** that keep the suite honest as governance evolves; do NOT RETIRE them for lacking a knowledge-tooth. (2) When auditing usefulness, check BOTH teeth and name which one the case relies on. (3) **A general per-case ablation detector for llm cases is NOT worth building — do NOT carry it as a TODO** (evaluated + rejected 2026-06-30). Three reasons: **(a)** for `AGENT/SOUL`-ref cases the knowledge-tooth tests a counterfactual that NEVER happens in production — those rules are ALWAYS fully injected in the system prompt, so "would the agent fail without R1?" is an impossible state; drift-tooth is the correct tooth for always-present rules. **(b)** for `MEMORY/EVOLUTION`-ref cases, the failure "knowledge disappeared (decay/archive/recall-miss)" is ALREADY caught by the drift-tooth (gate_refs → empty ref → RED); ablation's only UNIQUE catch is "knowledge present but case passes without it (circular)", and **(c)** that circularity is already covered cheaply by **static audit of assertion specificity** — an assertion carrying specific facts (3.5GB/7GB, nc -z, 800K, COE10) can't be answered without the knowledge. Building a heavyweight ablation framework (judge-path change + extra Bedrock judge calls per case, on the R9 hang surface) to verify what a free static check already covers ~90% is a bad ROI — the "build a mechanism" reflex, not a real need. **Real trigger to revisit:** a SPECIFIC `MEMORY`-ref case later suspected circular (assertion too generic, plausibly green without its knowledge) → ablate THAT one case by hand, never build the general framework pre-emptively. Until then, `AGENT/SOUL`-ref llm cases have a drift-tooth ONLY — acceptable and by-design. **Static audit (2026-06-30, 27 re-anchored llm cases):** all 27 carry a drift-tooth; 10 ALSO carry a knowledge-tooth, statically confirmed non-circular via assertion specificity (`MEMORY/EVOLUTION`-ref: DEC013/RCV002/DEC006/REF002/DEC001/RCV003/RCV001/DEC002/REF010/REF011); 17 carry drift-tooth only — correct, NOT toothless.
  4. **Actually executes** — no permanent skip/error. Unknown validity = zero value (e.g. GS_QUA001 skipped on "no supported evaluator").
  5. **Worth existing** — tests something that matters and can be made discriminating; else RETIRE.
  **Structural enforcement (P7 — prose failed 3× in the C042 family):** code gates so cases can't silently rot — (a) **gate_refs** (every dotted ref MEMORY./STEERING./AGENT./SOUL./EVOLUTION. resolves non-empty + on-topic → axis 1 AND the **drift-tooth** of axis 3a, already enforced) + (b) **knockout probe** for the programmatic **knowledge-tooth** (axis 3b: a gate-eligible case provably goes RED when its knowledge is knocked out — `negative_command`/`negative_expected_contains`, runtime-verified by `--verify-teeth`). **By design (NOT a gap):** llm cases have no automated knowledge-tooth detector — a general per-case `rules_context` ablation is deliberately NOT built (see axis-3 design rule (3): counterfactual for always-injected rules, redundant with drift-tooth for MEMORY-refs, circularity already covered by static assertion-specificity audit). AGENT/SOUL-ref llm cases rely on the drift-tooth alone; suspected-circular MEMORY-ref cases are ablated one-off by hand, never via a pre-built framework. These join `golden_case_validator` 4-gate (schema/duplicate/non-vacuous/teeth+privacy).
  **Anti-pattern (C044):** when an eval run surfaces N red cases, audit the WHOLE system's case-validity against the 5 axes — do NOT reach for "make the N green". Reaching for green IS the bug (P6: the metric serves the outcome, never replaces the judgment).
- [model] **Eval 系统 — 如何触发、什么是 lunchtime run、on-demand 手动触发(刷新错误认知,2026-06-29 XG 两次纠正)** (2026-06-29)
  <!-- ref:0 | last:none | decay:active | source:manual -->
  我之前对 eval 系统有多处错误认知,XG 当场纠正,以下是读码核实后的真相:

  **1. 触发方式有三类,手动 on-demand 是一等公民(我曾错说"agent 不能手动跑、只能等 scheduled"):**
  | 触发 | 入口 | 阻塞? |
  |---|---|---|
  | **Scheduled** | job `eval-nightly`(id 名过时,见下),cron `30 4 * * 1-5` | daemon scheduler 内跑 |
  | **On-demand 手动** | `POST /api/eval/run` → `eval_service.trigger_run(trigger="manual")` | **不阻塞** — 起 daemon 后台线程,**立即返回 run_id**,调用方拿 ID 就走(`eval_service.py:361`) |
  | **CI / deploy** | `ci_eval_gate.py`(post-push / release gate) | — |
  | 单 case / canary | `POST /api/eval/run-cases`、`POST /api/eval/canary` | canary 同步(programmatic-only,零 LLM) |

  **2. 它是 LUNCHTIME run,不是 nightly。** job id 仍叫 `eval-nightly`、name 仍是 "Nightly Full Eval"(陈旧未改名),但实际 cron 是 `30 4 * * 1-5` = **12:30 ICT 工作日午饭**(2026-06-29 Gap-1 改的:机器在线、Midway 新鲜窗口)。**判断 schedule 看 cron,不看 id 名** — 我曾跟着旧 id 一直叫它 nightly,错。

  **3. `eval_command_guard`(security_hooks.py:835-839)只拦三个 CLI 串:** `eval_runner.py run` / `ci_eval_gate.py` / `eval_service…run`。它**不拦** `POST /api/eval/run`(正当 on-demand 入口),也不拦 `scheduler --run-now`。guard 的本意是"别在 coding pipeline 里直接 CLI 跑 eval 测旧 binary",**不是**"禁止一切手动 eval"。我曾误读成后者 → 去绕 scheduler,选了最差路径。

  **4. 全量 run 约 15 分钟是正常,不是 hang(O030 教训):** 14:37 实测 `duration_seconds=923`,90 个 judge call 串行、每个 4.5-17s(中位 8.2s)。`bedrock.py:193` 已有 `read_timeout=120 + retries=2`,**单 call 不会无限 hang**。我曾用前台 `scheduler --run-now` + `alarm 420`(7min)去跑它 → 被自己的短超时砍掉、零产出,然后**误判成"无限 hang bug"并差点开 pipeline 修一个不存在的 bug(C042)**。SLOW≠HANG:正常就慢的任务不能用容灾超时去 guillotine。

  **5. 正确做法(下次要 on-demand 跑全量 eval):** 调 `POST /api/eval/run`(`curl -s -XPOST http://127.0.0.1:18321/api/eval/run -d '{"trigger":"manual"}'`)→ 拿 run_id → 轮询 `GET /api/eval/runs/{run_id}` 或读 `Eval/EvalHistory/`。**非阻塞、不冻 session、不需要前台超时**。绝不用前台 CLI / scheduler --run-now 去跑 15 分钟串行任务。
  来源:`eval_service.py:361 trigger_run`、`routers/eval.py:147 POST /run`、`system_jobs.py:134-137`、`security_hooks.py:835`、`bedrock.py:193`、`EvalHistory/2026-06-29_143736_nightly.json`(duration 923s)。2026-06-29 读码核实。

- [model] **Bedrock 认证 + 进程隔离 — App / Slack / Eval 三路径(如何 judge "跑 X 会不会影响 Y")** (2026-06-29)
  <!-- ref:0 | last:none | decay:active | source:manual -->
  **两类调用机制,但凭证根是同一个:**

  | 路径 | 调用机制 | 进程 | 凭证来源 |
  |---|---|---|---|
  | **App 对话**(chat tab) | Claude Agent SDK(Claude Code CLI 子进程),`CLAUDE_CODE_USE_BEDROCK=true` | `session_router.run_conversation` → SessionUnit 起的独立 CLI 子进程 | 委托 AWS credential chain(`claude_environment.py:187-190` 明确不设 access key / bearer token) |
  | **Slack/channel** | 同 App — `gateway.py:1291` 走**完全相同的** `session_router.run_conversation` → SessionUnit → CLI 子进程 | 同 App 类(每 channel session 一个独立子进程) | 同 App |
  | **Eval judge** | boto3 直接 `converse`(`jobs/bedrock.py`),不走 CLI | `eval_runner` 进程内的 boto3 调用 | `_resolve_credentials` → `boto3.Session().get_credentials()` → 同一个 AWS credential chain |

  1. **调用层不同**(App/Slack=CLI 子进程;Eval=boto3 进程内),但**凭证根相同** — 三者最终都解析到 `~/.aws/config` 的 `[default]` profile → `ada credentials print --account=533267412361 --role=Admin --provider=isengard` → 同一个 Midway cookie。Midway 过期则三者理论上都受影响。
  2. **进程隔离是真的:** App、Slack、Eval 是**独立进程**,不共享同一 Python 进程,`bedrock.py` 的 cached client 各进程内、不互通。唯一共享的是**磁盘上 `~/.aws` 凭证文件 + Midway cookie,且都是只读** — 一个进程读凭证**不会**改坏另一个进程的凭证。
  3. **因此:"手动跑 Eval 会把 App 搞崩" 是错误推断(CLASS B,2026-06-29 XG 当场纠正)。** 只要 Midway 新鲜(正常对话能进行即证明),Eval judge call 会成功、不 hang,更不会跨进程拖垮 App。06-28 那次 hang 冻的是**当时跑 eval 的那个 agent session 自己**(judge 的 boto3 在过期凭证下卡住),**不是** App 聊天 tab,也不是跨进程连带崩溃。
  4. **为何 06-28 只 Eval 崩、App 没事 — 是时机不是机制:** App/Slack 用户在场时才调(刚 mwinit、Midway 新鲜);nightly Eval 定时跑、在机器刚醒未 re-auth 的窗口被 catch-up 触发 → Midway 过期 → 90 judge call 全挂。同一把锁,失败的是时机。这正是 Gap-1 把 nightly 挪到 12:30 ICT 工作日(用户在场、钥匙新鲜窗口)能降低凭证失效概率的根因。
  5. **judge 隔离问题的方法论:** 问"跑 X 会不会影响 Y",先分两层 —(a)**进程**:同一进程吗?独立进程则内存态(cached client/状态)不互通;(b)**共享资源**:共享什么?若只共享只读磁盘文件(凭证),一方不破坏另一方。**共享只读 ≠ 连带故障** — 别因"凭证根相同"就推断"会连带崩溃"。
  来源:`claude_environment.py:172-205`、`gateway.py:1291`、`jobs/bedrock.py::_resolve_credentials`、`eval_runner.py:905+`。2026-06-29 读码验证(非推断)。
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 2 | trust: full | promoted: 2026-06-27 -->
- [decision] **Token estimation: ONE calibrated estimator, observability-not-gate (run_3f25a73a).** `ContextDirectoryLoader.estimate_tokens` is the single source of truth; coefficients are module constants `CJK_TOKENS_PER_CHAR=1.1` + `LATIN_TOKENS_PER_WORD=2.2`, CALIBRATED to the real opus-4-8 tokenizer (bedrock `invoke_model` usage.input_tokens, baseline-subtracted; Bedrock CountTokens API is UNSUPPORTED on our models). The OLD values (CJK 0.667, Latin 1.333) under-counted real content 40-65% → the system self-reported ~44K context while the true size is ~152K. **Both the forward estimate AND the 2 inverse-truncation sites (`_truncate_section`, `prompt_builder._truncate_daily_content`) derive from the SAME constant** — a hardcoded inverse drifts on recalibration. `_CJK_RE` is the unified superset (Han+Kana+Hangul); there is NO second CJK detector (the divergent `context_health_hook._is_cjk_like` was removed — it had Hangul-not-Kana while the regex had Kana-not-Hangul). `context_health_hook._check_token_budget` DELEGATES to the canonical (no local formula); its WARNING(91K)/EMERGENCY(130K) thresholds are OBSERVABILITY signals feeding the write-side trim line, NOT gates — the assembly line does not truncate (XG 2026-06-28 directive). `s_estimate-tokens` delegates to the canonical via repo-root discovery (`SWARM_REPO_ROOT` env + known-path fallback), never a vendored copy (a fork re-creates the drift). (2026-06-28, run_3f25a73a, decision)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [process] **HOW token estimation works — the algorithm + how to recalibrate (run_3f25a73a).** The estimator is a CJK-aware 2-pass heuristic in `ContextDirectoryLoader.estimate_tokens` (`backend/core/context_directory_loader.py`). Steps: (1) count CJK chars via `_CJK_RE` (the unified Han+Kana+Hangul superset — the single CJK detector in the codebase); (2) `cjk_tokens = int(cjk_count * CJK_TOKENS_PER_CHAR)` with `CJK_TOKENS_PER_CHAR=1.1`; (3) strip CJK, count remaining space-split words; (4) `latin_tokens = int(latin_words * LATIN_TOKENS_PER_WORD)` with `LATIN_TOKENS_PER_WORD=2.2`; (5) sum. Pure-Latin text skips step 1-2. Both constants are module-level (`context_directory_loader.py` top) — the forward estimate AND every inverse (truncation `words_to_keep = tokens / LATIN_TOKENS_PER_WORD`) read them, never a hardcoded literal. **To RE-CALIBRATE against a new model (the method that proved both old values wrong):** Bedrock `CountTokens` is UNSUPPORTED on our models, so measure via real `invoke_model` and read `usage.input_tokens` — `bedrock-runtime.invoke_model(modelId='us.anthropic.claude-opus-4-8', body=json.dumps({'anthropic_version':'bedrock-2023-05-31','messages':[{'role':'user','content':TEXT}],'max_tokens':1}))`. Subtract a baseline (`invoke_model` on `'.'` minus 1) to cancel fixed message-envelope overhead, then divide net tokens by CJK-char-count (pure-CJK sample) or word-count (pure-Latin sample). Measured 2026-06-28: CJK ~1.06-1.11 tok/char, Latin ~2.0-2.5 tok/word (markdown/technical). Sanity baseline: the 12 live context files measure ~152K total (NOT the ~44K the system used to self-report). `s_estimate-tokens` skill is the CLI front-end to this exact function (`--window` default 91K = the 1M-model context-file budget). (2026-06-28, run_3f25a73a, process)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- **Pipeline / 长任务的人机交互通道路由（3 通道 MECE，决策 2026-06-27）.** 三个关注点用三条独立通道，一条都不过 briefing：(1) **进展 / REPORT / completion / failure+reason → 当前 chat tab 对话流**（in-band response，owner turn，已存在主路径）；(2) **L2 judgment 拍板 → 当前 tab AskUserQuestion 阻塞问答**（复用现有 `ask_question_manager.py` + `security_hooks.py::create_ask_question_gate`（4h block，WAITING_INPUT 受 eviction 保护）+ `session_unit.py::continue_with_answer`；keyed on SDK `tool_use_id` → 结构上 per-session，无法跨 tab，天然不破 isolation）；(3) **孤儿 / blocked run 汇总 → briefing 纯只读 dashboard**（零按钮零命令零 mutation）。**Escalation 决策树：** L2 BLOCK → 「当前 session 可交互？」desktop chat=YES → AskUserQuestion（答案到达→用答案继续**同一个 run、同一个 stage**，不 checkpoint 不重开）；4h timeout → gate DENY + 当前 tab warning + fallback checkpoint。channel/headless=NO → 直接 fallback checkpoint + 当前 tab warning（**绝不 in-band**，因 `gateway.py:1387` 对 AskUserQuestion 是 auto-answer 挑首选项，对 L2 有害）。**关键不变量：** 此设计 scope 是纯 skill 协议层（`s_autonomous-pipeline/INSTRUCTIONS.md` escalation protocol），**零 session 代码、零 briefing 代码、零前端改动** —— 故结构上不可能引入 session/pipeline/race/chat-tab/isolation regression（不碰这些子系统）。run.json 当前**无 owner_session_id 字段**（315 run 实测），孤儿判定靠时间启发式是另一关注点（owner-binding），与本通道设计解耦、分 pipeline 落。(2026-06-27, pipeline-HITL 设计讨论, decision)
- The architecture is settled by the approved design doc (3 reviews) (2026-06-25, 120efb6e-be82-4874-ad56-45e5ca98851c, decision)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 2 | trust: full | promoted: 2026-06-25 -->
- The architecture is settled by the approved design doc (3 reviews) (2026-06-25, 120efb6e-be82-4874-ad56-45e5ca98851c, decision)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-06-25 -->
- Three orthogonal signals beat one clever regex: (1) strip fenced/inline code (docs vs raw leak), (2) match the structural body not an optional prefix, (3) require absence of a real ToolUseBlock. Layered structural discrimination is more robust than one tuned pattern. (2026-06-24, run_e607c4cd, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 1 | trust: full | promoted: 2026-06-23 -->
- Asymmetric normalization is the root pattern: when one layer normalizes a key (escalate writes canonical) but sibling layers do not (record/get use raw), state silently splits into two entries. The fix MUST normalize at EVERY key operation, not just at generation. (2026-06-23, run_40fad09e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Empirical reproduction before design split one assumed problem into two (GUI01/PIT05): a 10-min code+git probe revealed L-session subprocess poisoning was ALREADY self-healed (commit 65cea32b) and only the orchestrator-layer spawn-rejection remained. (2026-06-22, run_0bd15278, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Cross-module constant duplication needs a test that introspects the OTHER module, not just asserts local literals — else drift guard is false confidence (LOW-3). (2026-06-20, run_476c1f20, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Splitting Phase 2 into 7 dependency-ordered layers with per-layer forced tests + an atomic L2+L3 (send-disposition needs drain or messages strand) avoided the COE10 batch-landing pattern. Each layer committed independently green. (2026-06-20, run_3f4f4805, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-06-20 -->
- Single graph update + chunked route extraction = correct architecture split (2026-06-20, run_53633100, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 2 | trust: full | promoted: 2026-06-18 -->
- P5 per-tab streamState IS the deliverable - coexistence is the correct architecture (2026-06-18, run_e142ae8c, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-06-18 -->
- Always html.escape user-derived content in HTML templates (2026-06-18, run_dd3b4311, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- canary_pass cases spawn subprocesses (~1.3s each) — must be daily-gated not per-session (2026-06-18, run_b8c33b6c, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-06-18 -->
- HealingLoop has record_heal_success not reset - always check real API before assuming method names (2026-06-18, run_12c2807d, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-06-18 -->
- Slot == tab count means resume always contends — grace period > ceiling bump (2026-06-18, run_d33a0989, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Numeric claims in README must trace to code constants, not memory (2026-06-17, run_a541b721, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Debug logs from investigation marathons must be removed or DEV-gated before next build — they pollute production console and obscure real errors (2026-06-17, run_b20f7d06, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Workspace tree API returns list not dict — always check actual response shape (2026-06-17, run_591c9b9e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Channel wrap-up needs one-shot guard (2026-06-17, run_733573e0, auto-cultivated)
- Store endStreaming must be called in handleStop before streamGen increment (2026-06-17, run_733573e0, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Store subscription guard assumes activeTabIdRef is always valid — false during app restart race window (2026-06-17, run_3e3261b3, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- clientId matching only works if raw DB messages parallel converted messages 1:1 — document the invariant (2026-06-17, run_95dc339d, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Reconciliation logic must be conditional on replacement availability (2026-06-17, run_b8049bbe, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- _retry_with_resume initial_error_str counts as first timeout — test design must account for this (2026-06-17, run_5a32855c, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- **Note:** The implementation chose a *different* (arguably better) architecture — a dedicated orchestrator channel (`_ch_memory_refresh`) instead of e... (2026-06-17, c7eea644-0622-46fa-b4c1-e3a0b8fe2d17, decision)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- eslint-disable-line with em-dash creates phantom rule-not-found errors — always use double-dash (2026-06-17, run_8b1374e7, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- HTTP semantics matter for frontend error handling — 404 vs 409 enables proper UX branching (2026-06-17, run_c3178353, auto-cultivated)
- Guard must live at execution layer (unit) not just routing layer — defense in depth (2026-06-17, run_c3178353, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- handleTabClose must destroy store — cleanupTabState only fires on explicit cleanup not tab close (2026-06-17, run_55240478, auto-cultivated)
- Tab cleanup must destroy store — memory leak otherwise (2026-06-17, run_968158f9, auto-cultivated)
- Eager store creation eliminates entire class of fallback code (2026-06-17, run_968158f9, auto-cultivated)
- Single-writer eliminates dual-write bugs structurally (2026-06-17, run_a673ce83, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-06-17 -->
- immediate sync on subscribe eliminates hydration gap between tab switch and first notification (2026-06-17, run_426b7349, auto-cultivated)
- store subscription callbacks fire in rAF window after effect cleanup is scheduled but before unsub runs — always guard with tab identity check (2026-06-17, run_426b7349, auto-cultivated)
- HealthSensor must reset turn_count after heal (new subprocess = new counter) - otherwise infinite loop (2026-06-17, run_82de1576, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- WAITING_INPUT is hidden active state - guards must enumerate ALL active states (2026-06-17, run_7835486e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Session model lacks generation/boundary concept - messages accumulate across subprocess spawns indistinguishably (2026-06-17, run_8a8d7ab7, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-06-17 -->
- Zero-citation confidence must be 0.0 not 0.3 — evidence-mandatory means evidence MANDATORY (2026-06-16, run_64219a6e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Detail views must cross-reference source schema (golden_set.yaml) — AC should verify field coverage (2026-06-16, run_26d87394, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-06-16 -->
- Tests reading live mutable state (workspace files) WILL break when other sessions modify that state — always use committed fixtures for deterministic assertions (2026-06-16, run_af60d06b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Drift Detection git commands must scope to project directory, not CWD — agent CWD varies between workspace root and swarmai repo depending on session context (2026-06-16, run_908130a5, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Buttons that imply selection state (Run Selected) must have actual selection UI or be renamed — misleading labels are functional bugs not cosmetic ones (2026-06-15, run_a0ad27cc, auto-cultivated)
- Pre tag content inside JSX needs to start immediately after opening tag to avoid rendered whitespace — always check pre/code blocks for indentation artifacts (2026-06-15, run_a0ad27cc, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Cost management code never existed — validating the feedback that it was over-engineering. Design doc said $0.20/month which never warranted a circuit breaker (2026-06-15, run_299dc8f9, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 5 | trust: full | promoted: 2026-06-10 -->
- min-h-0 must be present at EVERY level of a flex→flex→AutoSizer chain, not just the leaf (2026-06-10, run_3e958d8e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- CSS sticky bottom-0 is ideal for always-visible indicators in scroll containers — zero JS, no state management, no IntersectionObserver needed (2026-06-08, run_1770b67a, auto-cultivated)
- pendingStreamTabs has dual-purpose (re-render trigger + pre-session guard) — narrowing guards to check actual semantic condition (sessionId exists) prevents silent data loss (2026-06-08, run_1770b67a, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Goal profile restructured to include all stages — eliminates THINK/DELIVER/REFLECT gaps that existed before (2026-06-08, run_2c2ce839, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- useEffect stale closure on state guard (if x !== 0) is always a bug when the state is excluded from deps — use unconditional set and let React bail out (2026-06-08, run_0b03f2b2, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Pivot architecture early instead of debugging platform-specific behavior (2026-06-07, run_926bcd7b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- originalLineContent snapshot at comment creation time enables cheap applied detection without diffing entire file (2026-06-07, run_f101f801, auto-cultivated)
- Auto-refresh polling with refs avoids stale closure pitfalls but must include failure counter to prevent infinite retry on deleted files (2026-06-07, run_f101f801, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Generation guards on SSE event handlers must be symmetric across ALL handler factories (createStreamHandler, createCompleteHandler, createErrorHandler) — one unguarded handler is a stale-event injection vector. (2026-06-06, run_9b491166, auto-cultivated)
- asyncio task.cancel() leaves buffer drain operations incomplete — pipe still has stale data. Always await drain completion with timeout, never cancel mid-drain. (2026-06-06, run_9b491166, auto-cultivated)
- DailyActivity checkpoint dedup must track ALL changing dimensions independently — a single truthy list makes the guard permanently open (2026-06-06, run_f68bbb30, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- SWARMAI.md and IDENTITY.md are already tiny (450+500 tok) — earlier 8K estimate was wrong, never measured (2026-05-30, run_bdccd7f4, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Test file routes MUST be filtered from query results — mocks pollute real navigation (2026-05-30, run_c9fe1997, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Single source of truth for deploy = prod.sh build. Skills orchestrate (preflight, handoff, health check) but never re-implement deploy logic (2026-05-30, run_40911257, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Adversarial returncode-parsing finding (LOW) was real: never parse subprocess stdout without checking returncode first. (2026-05-30, run_b047fb7e, auto-cultivated)
- Expensive runtime resolution (subprocess --version) must be boot-cached in lifespan, never in a polled handler — /health polls every 5s. (2026-05-30, run_b047fb7e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- When a verification cannot be run locally (cross-compile, .app launch), say so explicitly and flag it for CI/user — never claim green from a partial check. (2026-05-30, run_8a9de435, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A production recovery agent (watchdog) must be self-contained — depending on `python -m core.X` against a repo checkout makes it dead code for end-user installs. Ship the logic as a standalone stdlib script. (2026-05-30, run_b5592983, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Channel-vs-desktop thinking divergence belongs at the prompt_builder chokepoint, not a new config key, keeping channel cost-efficiency a system invariant. (2026-05-29, run_4af3bea7, auto-cultivated)
- Publishing stage artifacts does NOT record run.json stage entries — must call run-update --stage-json after each publish or the completion gate blocks on empty stages. (2026-05-29, run_4af3bea7, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- P1 was already done — always Read existing code before planning new mechanism (2026-05-29, run_98ea10a3, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Model upgrades touch more files than the obvious config — auth verification, skill creation, LLM-as-judge, and UI display strings all hardcode model IDs (2026-05-29, run_664b8fef, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- react-force-graph-2d is zero-config for force-directed viz — 250 lines for a production-quality interactive graph with dark theme, coloring, hover, click-focus (2026-05-29, run_f9e4f27b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- cross_format_check only needs 3 checks (naming, duration, brand) to catch 90% of multi-track inconsistencies (2026-05-29, run_2b41b259, auto-cultivated)
- Never brew install --build-from-source in agent sessions — 15min compile exceeds timeout, find bottle-compatible alternatives (2026-05-29, run_4a2062df, auto-cultivated)
- Pixel-based text wrapping via textbbox() binary search eliminates CJK line-break bugs — never use character-count heuristics for mixed-width (2026-05-29, run_4a2062df, auto-cultivated)
- Idempotency via commit-hash state file prevents re-processing across sessions — simpler than tracking proposal IDs (2026-05-29, run_30dfc465, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- File naming checks must scope to output files only — metadata and dependency files (node_modules) are not user-controlled (2026-05-26, run_0d65e152, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Engine registries should be declarative YAML config not Python dicts — adding a capability should never require editing the measurement tool (2026-05-26, run_0ed138eb, auto-cultivated)
- Self-maintaining docs need both measurement (metrics auto-refresh) and detection (staleness checks) — metrics prevent counting drift, staleness prevents prose description drift (2026-05-26, run_0ed138eb, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- PptxGenJS option object mutation is silent file-corruption - must be documented as guardrail (2026-05-26, run_6fb29ab0, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Agent sent email immediately without showing draft — user wanted to review and discuss first; rule: external actions require explicit user go-ahead (2026-05-25, f2b20c40-881c-4f02-bf16-ed2010bda880, correction)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- STEERING rule retirement is healthy — R11 was fully absorbed by R14, proving rules should be periodically audited for redundancy. (2026-05-25, run_6d052913, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- session_cleared conflated two concerns: session tracking + UI state. Events that change internal state should NEVER clear user-visible content without explicit user action. Same principle as STEERING R10: exit error state on broad condition, not the specific trigger. (2026-05-25, run_bd8d8eec, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->

Desktop app with three layers: a Tauri 2.0 shell (Rust), a React frontend (TypeScript), and a Python FastAPI backend running as a **launchd daemon** (24/7). Tauri connects to the daemon on startup; the backend runs independently of the desktop app, enabling always-on Slack, background jobs, and scheduled tasks. The backend spawns Claude Agent SDK subprocesses for AI capabilities via AWS Bedrock.

### Platform Startup Architecture (2026-05-08)

Four mutually exclusive platform modes, determined by compile-time `#[cfg]` (Rust) and runtime `SWARMAI_MODE` (Python):

| | **macOS Desktop** | **Windows Desktop** | **Linux Desktop** | **Hive (EC2)** |
|---|---|---|---|---|
| **SWARMAI_MODE** | `daemon` | `subprocess` | `subprocess` | `hive` |
| **Who owns process** | launchd (KeepAlive) | Tauri (child process) | Tauri (child process) | systemd (Restart=always) |
| **App close → backend** | Survives (24/7) | Killed (graceful + force) | Killed (graceful + force) | N/A (no desktop app) |
| **Channel gateway** | ✅ Runs | ❌ Blocked | ❌ Blocked | ✅ Runs |
| **Scheduled jobs** | ✅ Runs | ❌ Blocked | ❌ Blocked | ✅ Runs |
| **POST /shutdown** | Blocked | Allowed (Tauri sends) | Allowed (Tauri sends) | Blocked |
| **Process manager** | `com.swarmai.backend.plist` | Tauri spawn + taskkill /T | Tauri spawn + pkill -P | `swarmai-hive.service` |
| **Port** | 18321 (fixed) | 18321 (fixed) | 18321 (fixed) | 18321 (fixed) |
| **Auto-restart on crash** | launchd ThrottleInterval=10s | No (health watchdog detects, user restarts) | No (health watchdog detects, user restarts) | systemd RestartSec=10 |
| **Binary location** | `~/.swarm-ai/daemon/python-backend` | Adjacent to exe (externalBin) | Adjacent to exe (externalBin) | `/opt/swarmai/backend/` (source) |
| **Deploy mechanism** | `auto_install_daemon()` from .app bundle | Bundled with installer (.exe) | Bundled with package (.deb/.AppImage) | `update-hive.sh` rsync |
| **Kill on close** | No-op (daemon) | `send_shutdown_request` + `kill_process_tree` (taskkill) | `send_shutdown_request` + `kill_process_tree` (pkill) | N/A |

**Isolation guarantees:**
- **Rust (#[cfg]):** `start_backend()` has a `#[cfg(target_os = "macos")]` branch (auto_install_daemon + launchctl) and a `#[cfg(not(target_os = "macos"))]` branch (spawn subprocess). Windows and Linux share the subprocess path — platform differences are in kill_process_tree (taskkill vs pkill) and binary naming (.exe vs no extension).
- **Python (SWARMAI_MODE):** `_detect_run_mode()` reads env var. Channel gateway only starts if mode ∈ {daemon, hive}. /shutdown only allowed if mode ∉ {daemon, hive}. Default is "daemon" (macOS plist and Hive systemd service both set it explicitly; Tauri subprocess sets "subprocess"; dev.sh sets "dev").
- **No fallback between modes.** macOS never spawns subprocess. Windows/Linux never touch launchd. Hive never uses Tauri. If the platform-specific path fails, it's a hard error — no silent degradation.
- **Linux Desktop vs Hive:** Both are Linux, but completely different entry points. Desktop = Tauri .deb/.AppImage → `start_backend()` → subprocess. Hive = systemd → `swarmai-hive.sh` → python directly. The `SWARMAI_MODE` env var is the runtime discriminator.

**Hive (EC2 cloud deployment):** Same Python backend + React frontend, no Tauri shell. Runs on EC2 (Amazon Linux 2023) behind CloudFront + ALB with Basic Auth. `SWARMAI_MODE=hive` activates platform filtering, disables npm provisioning at startup, and uses systemd instead of launchd. Single-tenant: each Hive instance is provisioned for one user by the Desktop owner.

```
+------------------------------------------+
|  Tauri Shell (Rust)                       |
|  - Window management, native APIs        |
|  - Daemon auto-install on first launch   |
|  - Health watchdog (reconnect on crash)  |
+------------------------------------------+
         |                    |
         v                    v
+-----------------+  +------------------------+
| React Frontend  |  | Python Backend Daemon  |
| - Chat UI       |  | - FastAPI + asyncio    |
| - Workspace     |  | - Fixed port 18321     |
|   Explorer      |  | - 24/7 via launchd     |
| - Radar/ToDo    |  | - Claude Agent SDK     |
| - Settings      |  |   (CLI subprocess)     |
| - SSE streaming |  | - SQLite (WAL mode)    |
+-----------------+  | - Skill loader         |
                     | - MCP server manager   |
                     | - Context pipeline     |
                     +------------------------+
                              |
                     +------------------------+
                     | MCP Servers (external)  |
                     | - GitHub, Slack, etc.   |
                     | - stdio / SSE / HTTP    |
                     +------------------------+
```

### App Start — E2E Boot Sequence (verified 2026-07-18)
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

Complements the platform-topology matrix above (which answers *who owns the
process*); this answers *what happens, in order, from launch to a dismissed
splash*. Three subsystems run **partly in parallel**: Rust brings the backend
up, Python's lifespan initializes it, and the frontend polls — the frontend
poll (③) and the backend lifespan (②) overlap.

**① Tauri process start (Rust)** — `desktop/src-tauri/src/lib.rs::start_backend()` (:1855)
- `probe_daemon_health(3,1)` — is a backend already on port 18321?
  - **Yes** → `connect_daemon()` (:1869): mark running, start `spawn_daemon_health_watchdog(10s)` (crash watchdog), macOS also kicks background `sync_daemon_version`.
  - **No** → platform branch:
    - **macOS**: `auto_install_daemon()` (:1249) deploys the `python-backend` onedir into `~/.swarm-ai/daemon/` via launchd → `probe_daemon_health_adaptive()` waits **while the process is alive**, bounded by `COLD_START_CEILING_SECS=300` (:619) — fail-fast only if the process is truly gone.
    - **Windows/Linux**: `spawn_subprocess()` (`SWARMAI_MODE=subprocess`, :1586) — backend dies with the app.
    - **Hive**: no Rust at all; systemd keeps it resident, frontend (`isDesktop()=false`) connects directly.

**② Backend lifespan cold start (Python)** — `backend/main.py::lifespan()` (:683)
- `_ensure_database_initialized()` (:706) → `skip_init_pipeline?`
  - **Fast path** (seed-sourced / returning user): build the connection pool skipping DDL+migrations, `ensure_default_workspace`, and defer `refresh_builtin_defaults` to a background task (does **not** block startup).
  - **Full init** (dev, no seed): DDL + migrations + `run_full_initialization()`.
- Channel gateway (:791) starts **only** for `daemon`/`hive` (`_gateway_allowed`, :800); subprocess/dev skip it (Slack Socket Mode = one connection per app token).
- `_startup_complete = True` — **only from this instant does `/health` return `healthy`**.

**③ Frontend startup-overlay poll** — `desktop/src/components/common/BackendStartupOverlay.tsx`
- `startHealthPolling()` (:528): desktop calls `initializeBackend()` (= Rust `start_backend`); Hive skips it → `setTimeout(pollHealth, 500ms)`.
- `pollHealth()` (:438) loop:
  - 🆕 **#4a wall-clock ceiling** (2026-07-18): `performance.now() − firstPollTime ≥ 300s` → error (`hasExceededStartupCeiling`, :225). Closes the Hive/browser gap where there is no Rust backstop and an `alive`↔`no_response` flap polled forever.
  - `checkHealth()` → `GET /health` → `classifyHealth()` (:186) three-way:
    - **`ready`** (`status==healthy`) → `fetchSystemStatus()` → `checkReadiness()` (:147); if `agent.ready && workspace.ready` → `connected` → fade out → `onReady()`; else → `waiting_for_ready` → `pollReadiness()`.
    - **`alive`** (JSON reply but not healthy = still booting) → reset `noResponseStreak=0` (**does not count as failure**) → re-poll. This is the run_e3dbc009 false-fatal fix: a slow-but-alive first launch is never killed.
    - **`no_response`** (network error / SPA-fallback HTML) → `streak++`; `≥ maxNoResponse (60)` → error.

**④ `/health` three states** — `backend/main.py::health_check()` (:1252)
- `not _startup_complete` → `{status:"initializing", auth:"unknown"}` — **never runs STS during boot**.
- else concurrent `asyncio.gather`: `_check_db()` (2s cap) + `_check_auth()` (1s cap). Auth **skips STS** when `use_bedrock=False` OR `auth_method=="bedrock_api_key"` (bearer token has no sigv4 identity) → returns `valid` so `CredentialBanner` (which only fires on `expired`) stays hidden. → `{status:"healthy", version, auth}`.

**Three timing gates — independent, easy to conflate:**

| Gate | Where | Clock | Bound |
|---|---|---|---|
| Rust cold-start | `probe_daemon_health_adaptive` (lib.rs) | process wall-clock | 300s (`COLD_START_CEILING_SECS`); **macOS/desktop only**; waits while process alive, fail-fast only if dead |
| Frontend **health phase** 🆕#4a | `pollHealth` (:459) | `performance.now()` (monotonic) | 300s (`readinessTimeout`); added 2026-07-18 to bound the health phase under `alive`/`no_response` flapping (Hive/browser have no Rust gate) |
| Frontend **readiness phase** | `pollReadiness` (:407) | `Date.now()` | 300s; pre-existing, **independent** of the health-phase gate → worst-case total overlay ≈ 2×300s (accepted; noted in code) |

**Key invariants:**
1. `initializing` vs `healthy` are distinct: `/health` replies `initializing` (`auth:"unknown"`, no STS) until `_startup_complete`; the frontend maps that to `alive` and keeps waiting — never a false-kill.
2. Fast startup is the norm — seed/returning users skip DDL+migrations; `refresh_defaults` is a background task, off the critical path.
3. No fallback between platform modes (see matrix above); a failed platform path is a hard error.
4. **Latent** (not a bug): the health-phase and readiness-phase ceilings are two separate 300s windows. If total boot time ever needs tightening, merge them onto one shared `startTimeRef`.

### Setup Wizard — Onboarding Gate & Flow (code-traced 2026-07-19)
<!-- maturity: sparse | sources: 2 | verified: true | used: false | days: 0 | trust: high | promoted: none | source:manual -->

Complements the boot sequence above (which ends at a dismissed splash); this is
*who sees the wizard vs the app, and what the wizard does*.

**The gate — new-user vs returning-user routing:**
- `shouldShowOnboarding(status) = !!status && !status.onboardingComplete` (`desktop/src/App.tsx:37-38`) — keys on `!onboardingComplete` **ONLY**, deliberately NOT `initialized`. The old gate (`initialized && !onboardingComplete`) dropped an init-failed new user into a broken ChatPage; the fix routes them to the wizard (Step 1 is the live wait state). `routeDecision` (:64): `loading → render null` (zero ChatPage flash), `error → error card + Retry`, else gate.
- `onboardingComplete` ← `GET /system/status` (`system.ts:getStatus`, 5s timeout, retry 2; missing ⇒ false).
- **Returning-user smooth upgrade**: an idempotent boot migration backfills `UPDATE app_settings SET onboarding_complete = 1 WHERE initialization_complete = 1` (`backend/database/sqlite.py:2587`) → existing users skip the wizard, never re-onboard.

**Wizard steps** (`OnboardingPage.tsx`): Step1 System Check (polls `/system/status`; ~20 fails/≈60s → error card + "Continue anyway" escape) → Step2 Auth (`AuthConfigPanel`, **verify-then-persist** — secret passed inline as a verify override, NOT written to disk until verify succeeds; always a "Configure later" escape) → [Step3 Restore — only if a backup is detected AND not Hive] → Channels (Slack, skippable) → Ready. **Every step except Ready has an escape hatch**; Hive uses a single fixed IAM method.

**Auth modes**: SSO / ADA / Anthropic API key / Bedrock API key / iam_role. Deployment context inferred (`~/.ada` or `~/.midway` ⇒ internal). Remediation is method-aware (`auth_remediation.remediation_for`). A failed verify persists NO config.

**First-run seed + DB-corruption recovery** (`backend/main.py`): missing seed.db → runtime init (dev only); a malformed `data.db` → purge (incl. `-wal`/`-shm`) + reseed + retry-once → bounded KeepAlive restart. **`sqlite3.OperationalError` (locked) MUST be caught before `sqlite3.DatabaseError` (corrupt)** so a valid-but-locked db is never destroyed. Fast-path `_init_db_bounded(skip_schema=True)` carries a 45s wall-clock bound.

**Restore-step design invariants (the two hazards fixed in run_da5da0b1 + run_037a02af):**
- **Restore SSE stream** (`system.ts::restoreBackup`): bounded by a 90s **IDLE** stall-guard (`RESTORE_STALL_TIMEOUT_MS`, reset per event — a hang-guard, NOT a total-duration cap, per O030) + an `AbortController`. `StepRestore` renders a Skip escape *during* restoring and, on unmount, aborts via an **external AbortSignal** — because React unmount does NOT `.return()` a fire-and-forget `for-await`, and a generator parked at `await reader.read()` can only be released by an external signal that errors the fetch.
- **Restore DB import is atomic** (`git_sync_engine::_import_tables_sync`): single transaction — commit-all or rollback-all+raise; a validation-skip (rejected file) stays non-fatal. So an interrupted restore leaves an EMPTY db, never a partial one.
- **Freshness authority = real data, NOT a flag** (`backup_manager::_restore_impl`): a populated DB or a non-trivial MEMORY.md (`_workspace_has_real_data`) ALWAYS refuses restore — this wins even when the `.restore-in-progress` marker is present (a marker can go stale via SIGKILL after a successful restore; treating stale-marker as unconditional cleanup would rmtree real data). The marker only governs cleanup of a NON-populated workspace's debris. Atomic import is what makes this reliable (debris = empty db ⇒ populated = genuine).
- **`_cleanup_partial_restore` is allow-list guarded**: rmtree ONLY a real (non-symlink) dir strictly BELOW `swarm_dir`; it does NOT unlink the live daemon db (atomic import makes that unnecessary). Token is persisted only AFTER clone succeeds.

**Known deferred (pre-existing architecture, evaluated LOW — not worth a standalone run):** restore imports into the live daemon `data.db` with no onboarding-gate/quiesce (the data-loss half is already removed by atomic-import + no-db-unlink; deeper isolation is a separate design); the import has no decompressed-size cap (zip-bomb DoS, but the trigger is the user's own backup repo).

**No-Dead-End invariant — every block point has a visible exit (verified 2026-07-19):**
The whole App-Start → Setup-Wizard flow has **NO path that can permanently trap a
new user**. Design rule (must be preserved by any future change): *"never dead-end,
only delay"* — every wait state = **bounded timeout → visible exit (Retry / Skip)**,
never a permanent `null` / permanent spinner. The **ONLY intentional block is LLM Auth
(Step2)**, and it is skippable ("Configure later" → `handleSkipSetup` → app; the
`CredentialBanner` then nudges) → a strong nudge, not a hard gate. All the per-point
exits are documented inline above (①②③ ceilings + wizard step escapes); the exhaustive
audit found no un-exited path.

**Historically-fixed dead-ends — structurally closed, recorded so they aren't reintroduced:**
1. Partial-init new user stranded on unusable ChatPage → gate keys on `!onboardingComplete` only, not `initialized` (`shouldShowOnboarding`).
2. Infinite spinner on part-init backend → Step1 `SYSTEM_CHECK_FAILURE_THRESHOLD` (~20 attempts/≈60s) → failure card + escape.
3. White-screen with no exit (status query fails all retries *after* the overlay faded — was the ONLY no-exit dead-end) → `routeDecision → 'error'` → full-screen Retry card that refetches.
4. Fixed 60s cap false-killing a slow-but-alive backend (run_e3dbc009) → `classifyHealth` 3-state (`ready`/`alive`/`no_response`); `alive` never counts toward give-up.
5. Auth hard-lock (old gate required `initialized` = db+gateway green) → Auth now skippable.

## Stack
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

| Layer | Technology |
|-------|-----------|
| **Shell** | Tauri 2.0 (Rust) |
| **Frontend** | React 19, Vite 6, TanStack Query, Tailwind CSS 4.x, CodeMirror 6 |
| **Backend** | Python 3.12, FastAPI, asyncio, Pydantic v2 |
| **AI** | Claude Agent SDK, Claude 4.6 (Opus/Sonnet) via AWS Bedrock, 1M context window |
| **TTS** | AWS Polly (primary), Edge TTS (default/free), Azure (optional). Backend resolution: `user_prefs > env > edge`. See `s_pollinate/scripts/tts/backends/` |
| **Embeddings** | Bedrock Titan v2 (1024-dim) + sqlite-vec — LIVE in the **Knowledge store** (`knowledge_store.py`); **DORMANT in the recall path** (vector leg torn out 2026-06-28 / 6540970e — recall is pure FTS5/BM25, see Recall Architecture) |
| **Database** | SQLite (WAL mode) at `~/.swarm-ai/data.db` |
| **Testing** | pytest + Hypothesis (backend), vitest (frontend) |
| **Build** | PyInstaller (backend bundle), Tauri CLI (app package) |
| **Daemon** | macOS: launchd (`com.swarmai.backend`), 24/7; Windows: Tauri subprocess; Hive: systemd. All use port 18321. |
| **Hive** | EC2 (Amazon Linux 2023), CloudFront + ALB, Basic Auth, `SWARMAI_MODE=hive` |
| **License** | AGPL v3 + Commercial dual-license |

## Codebase Location
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

- **Local:** `/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/`
- **GitHub:** https://github.com/xg-gh-25/SwarmAI
- **Clone:** `git clone https://github.com/xg-gh-25/SwarmAI.git`

## Dev Commands
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

```bash
# Full dev (starts backend + Vite + Tauri window):
cd desktop && npm run tauri:dev
# or from project root:
./dev.sh

# Backend only (after Python changes):
./dev.sh backend

# Frontend tests:
cd desktop && npm test -- --run

# Backend tests:
cd backend && pytest

# Production build:
cd desktop && npm run build:all
```

## Key Subsystems
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

### Embedded Terminal

In-app terminal subsystem (shipped 2026-07-11/12). Frontend: `desktop/src/components/terminal/` (`TerminalPanel.tsx`, `TerminalTab.tsx` + tests) driven by xterm.js, state via `desktop/src/contexts/TerminalContext.tsx`. Backend: a vendored Rust PTY layer (`portable-pty`, `async_runtime.rs`) exposed as Tauri commands. Three entry points: workspace explorer, terminal panel, left-nav terminal icon. Notable design points: platform-aware default shell + drain one-shot clear (Gate-2 adversarial fixes); cwd default falls back to `$HOME` when the target dir is absent (fresh-install ENOENT guard). ⚠️ TERM/pty runtime traps (forced `TERM=xterm-256color`, launchd env inheritance) are documented in § Runtime Traps — do not re-derive here.

### Session System (v7) — Multi-Tab Architecture

Multi-module architecture replacing the original monolithic AgentManager. Each chat tab runs an independent Claude Agent SDK subprocess — tabs are fully isolated (no shared state, no cross-tab interference).

| Component | File | Lines | Responsibility |
|-----------|------|-------|---------------|
| **SessionRouter** | `session_router.py` | ~1850 | Slot acquisition, IDLE eviction, queue timeout, session_id → SessionUnit mapping, **serial pending-drain worker (Root-1)** |
| **SessionUnit** | `session_unit.py` | ~3180 | Per-session 5-state machine, subprocess spawn/kill, send() orchestration, HealthSensor/HealingLoop |
| **StreamingOrchestrator** | `streaming_orchestrator.py` | ~1200 | SDK response streaming, event formatting, timeout/liveness detection |
| **RetryManager** | `retry_manager.py` | ~585 | Exponential backoff retry, OOM recovery, buffer overflow, abandon continuation |
| **LifecycleManager** | `lifecycle_manager.py` | ~1485 | Background loop (60s), TTL kill (12h), orphan reaper, stall recovery |
| **SessionHealing** | `session_healing.py` | ~640 | HealthSensor (5 triggers) + HealingLoop (max 3, 60s cooldown) + TaskCheckpoint. `HANG_TIMEOUT_S=300` |
| **SessionPending** | `session_pending.py` | ~530 | **Root-1 SSOT: server-side pending-message contract** (`sent=0`/`pending_seq`, persist→claim→drain→sent lifecycle, FIFO-coalesce) |
| **SessionRegistry** | `session_registry.py` | ~270 | Module-level singletons, component wiring at startup |

**Bug localization map (2026-06-18 extraction, updated 2026-06-21):**

| Symptom | Look at |
|---------|---------|
| Message format / SDK parse / event loss | `streaming_orchestrator.py` |
| Retry not firing / OOM recovery / abandon path | `retry_manager.py` |
| Process spawn fail / kill unclean / zombie | `session_unit.py` (spawn/kill) |
| State transition wrong / stuck in STREAMING | `session_unit.py` (state machine) |
| Self-heal / checkpoint loss | `session_healing.py` |
| Message lost while busy / "queued" not draining / phantom resume context | `session_pending.py` + `session_router.py` (drain worker) |
| AskUserQuestion never answerable / stuck WAITING_INPUT | `session_unit.py` (`has_outstanding_tool_use`, `force_unstick_waiting_input`) + `routers/chat.py` (`pending_question`) |

**5-state machine** (per SessionUnit):

```
COLD → STREAMING → IDLE → DEAD
  ↑        ↓         ↓
  └── WAITING_INPUT ←┘
```

| State | Meaning | Protected | Evictable |
|-------|---------|-----------|-----------|
| COLD | No active stream, subprocess may or may not exist | No | Yes |
| STREAMING | Active SSE connection, generating response | Yes | Never |
| WAITING_INPUT | Agent asked a question, waiting for user | Yes | Never |
| IDLE | Stream completed, awaiting next user message | No | Yes (lowest priority) |
| DEAD | Cleaned up, no recovery | No | N/A |

**Multi-tab slot management (R6a, 2026-06-24 — backend decoupled from the UX ceiling):**
- **Backend spawn/resume admission is gated SOLELY by `spawn_budget()` (real RAM), NOT `compute_max_tabs()`.** `compute_max_tabs()` (returns [2,4]) is now a **frontend UX constant only** — its sole consumer is `GET /api/max-tabs`, telling the UI how many tabs to draw. `session_router._acquire_chat_slot`, the wake re-check, the `needs_queue` precheck, and `retry_manager` resume all consult `spawn_budget`, never the ceiling. (Pre-R6a the count ceiling fired BEFORE the budget gate, forcing cross-tab eviction on memory-abundant machines → exit -9 → fragile `--resume`. Design §9; resolves the "Slot == tab count means resume always contends" lesson.)
- First **chat** tab is sacred: `_chat_alive_count == 0` → granted (budget-checked + same-pool evict attempt, mirrors `_acquire_channel_slot`; logs if granting under denied budget). A live channel never blocks the first chat tab.
- `spawn_budget`'s `_CONCURRENT_PENALTY_FACTOR` (per-alive-session cost inflation) is the COE05 simultaneous-peak OOM floor — unchanged by R6a. The genuine idle-vs-streaming two-limit split (§9.4) is deferred to a dedicated streaming-admission run.
- Queue timeout: 300s. If `spawn_budget` denies + no evictable idle, request waits in queue with exponential backoff.
- Eviction policy: memory-aware — evicts highest-RSS IDLE unit first. Protected states (STREAMING, WAITING_INPUT) are **never** evicted. Cross-tab eviction is RETAINED as the budget-denied fallback (its removal — the multi-tab-isolation first principle — is the follow-up R6b).
- Spawn serialization: module-level `_spawn_lock` (asyncio.Lock) ensures only one subprocess spawns at a time.

**Lifecycle manager thresholds:**

| Parameter | Value | Purpose |
|-----------|-------|---------|
| LOOP_INTERVAL | 60s | Background health check cycle |
| TTL_SECONDS | 43,200s (12h) | Kill idle sessions after this |
| IDLE_HOOK_GRACE | 120s | Grace period before firing idle hooks |
| STREAMING_TIMEOUT | 300s | Detect stuck streams → auto-recovery |
| MEMORY_EVICT_PCT | 90% | Trigger eviction of idle units |
| MEMORY_CIRCUIT_BREAKER | 95% | Stop spawning new sessions entirely |
| Orphan reaper | Every 3rd cycle (~3 min) | Kill subprocess orphans, 30s timeout guard |

**Proactive OOM restart & RSS kill thresholds (updated 2026-06-18):**

| Threshold | Value | Condition | Action |
|-----------|-------|-----------|--------|
| PROACTIVE_RSS | 3.5GB | Session in IDLE state | Compact → kill → lazy resume |
| STREAMING_RSS | 7.0GB | Session in STREAMING state | Emergency kill (true leak signal) |
| System pressure | 85% RAM | Any state | macOS jetsam (OS-level) |

Why raised (from 1.2GB/3GB): CLI serializing 400-800K token context peaks at 3-4.5GB RSS during normal API calls (V8 JSON.stringify source + buffer in memory simultaneously). Pattern is sawtooth (peak→drop to 750MB), NOT monotonic growth. Previous thresholds set for 128K task_budget; raised to 800K on 06-01 without adjusting → 2 months of intermittent kills mid-stream.

Spawn cost model: 1500MB (was 500MB) with 1200MB adaptive floor. Records main process RSS, not tree.

**Three-pool thread isolation (commit 8d9895c1):**

| Pool | Location | Workers | Purpose |
|------|----------|---------|---------|
| `_subprocess_executor` | `session_unit.py` | 8 | Process tree snapshots (`ps`/`pgrep`) + kill sweeps. Blocks for waitpid. |
| `_job_executor` | `main.py` | 4 | Job scheduler cycle — serial, long-running (CLI subprocesses up to 480s) |
| Default asyncio pool | — | — | Short IO only: aiosqlite, file reads, hooks. Health endpoint also here. |

Why: b812e9e2 fixed job paths → jobs successfully spawn 480s subprocesses → default pool exhaustion → health endpoint hang → Tauri "not responding". Fix: isolate by duration class. Health endpoint additionally wrapped in `asyncio.wait_for(timeout=2.0)` as L1 defense.

**Session reliability mechanisms (2026-05-18→20, updated 2026-06-18):**

| Mechanism | File | Purpose |
|-----------|------|---------|
| PID watchdog | `streaming_orchestrator.py` | Detects out-of-band subprocess death (OOM kill, jetsam) within 2s |
| Output liveness watchdog | `streaming_orchestrator.py` | Detects streaming hangs (no output for 30s) → force-kill + retry |
| Adaptive streaming timeout | `streaming_orchestrator.py` | Scales timeout with context size (300s base → up to 600s at 900K+ tokens) |
| Retry with resume | `retry_manager.py` | Exponential backoff (30/60/120s), global OOM cooldown, --resume flag |
| Circuit breaker | `retry_manager.py` | Threshold 1M tokens — prevents infinite retry loops on context overflow |
| Buffer overflow recovery | `retry_manager.py` | Respawns with progressive-processing instruction on 10MB JSONRPC overflow |
| Pipe flush await | `session_unit.py` | Cancels stale pipe_flush_task before new send — prevents race condition |

**WAITING_INPUT handling (updated 2026-06-20 by Root-1):** A new message during WAITING_INPUT is now governed by `has_outstanding_tool_use` — it persists `sent=0` and drains later (durable contract) rather than being consumed-as-answer or killing the session. `force_unstick_waiting_input()` (kill → DEAD → COLD → auto-resume, symmetric with `force_unstick_streaming()`) is retained as **last-resort** recovery for a genuinely stuck WAITING_INPUT (frontend died before answering). See the Root-1 SSOT section for the full pending/drain contract.

**Auto-resume:** `_ensure_spawned()` checks if COLD + existing `_sdk_session_id` → auto-inject `--resume` flag on all kill-then-respawn paths. Retry: 3× exponential backoff with `--resume` for conversation continuity.

### Session Lifecycle Resilience (Self-Healing, shipped 2026-06-17)

Invisible self-healing that auto-detects session degradation and recovers without user intervention. Design: `Knowledge/Designs/2026-06-17-session-lifecycle-resilience-design.md`.

**Architecture: HealthSensor → HealingLoop → TaskCheckpoint**

| Component | File | Purpose |
|-----------|------|---------|
| **HealthSensor** | `session_healing.py` | Detects 5 trigger types (latency spike, RSS growth, error cascade, turn approaching limit, hang > `HANG_TIMEOUT_S=300s`). Read-only, no side effects. (Raised from 90s — long tool calls are legitimate work.) |
| **HealingLoop** | `session_unit.py` | Checkpoints state → kill subprocess → respawn with `--resume` → inject continuation prompt. Max 3 attempts, 60s cooldown. |
| **TaskCheckpoint** | `session_unit.py` | Captures: original request, completed/pending steps, files modified, git state, pipeline state, key findings. Injected into continuation prompt. |

**Turn limits (updated 2026-06-17):**

| Context | Value | Safety |
|---------|-------|--------|
| Desktop | max_turns=500 | Self-heal triggers at 480 (graceful one-more-turn) |
| Channel | max_turns=100 | task_budget=400K is the real safety |
| Schema ceiling | le=2000 | Eliminates artificial DB constraint |

**Unified recovery primitive (`_arm_recovery_checkpoint`, 2026-06-18):**

All involuntary kill paths (proactive RSS, streaming OOM, hang watchdog, WAITING_INPUT timeout) go through a single `_arm_recovery_checkpoint()`. Rich checkpoint captures: last user request, assistant partial conclusion, files touched, git state. Physical constraint: graceful wrap-up (agent summary) only available on voluntary paths (proactive kill has alive agent); involuntary kill = history inference only.

**User Stop absolute priority:** `_user_stopped_current_turn` signal persists until next `send()`. Self-heal NEVER triggers on a user-stopped turn.

**Frontend:** 30s `HEAL_GRACE_PERIOD` — spinner stays, no error toast during heal. Cleared on first streaming data. `_resumeBoundaryIdx` in MessageStore filters pre-boundary messages (prevents stacked dividers).

### MessageStore — Single-Writer Architecture (shipped 2026-06-17)

Centralized message state management replacing 45 independent `setMessages()` call sites with a phase-gated store. Design: `Knowledge/Designs/2026-06-17-message-store-refactor-design.md`.

**Architecture:**

| Component | File | Purpose |
|-----------|------|---------|
| **MessageStore** | `MessageStore.ts` | Per-tab store. Operations: `append`, `appendMany`, `updateLast`, `replace`, `reconcile`, `remove`. Phase-gated. |
| **Store subscription** | `useChatStreamingLifecycle.ts` | rAF-gated `onChange` callback. Tab identity guard (`currentActiveTabId !== tabId → return`). |
| **Reconcile** | `MessageStore.ts` | ID-based merge. NO-OP during streaming phase (queued). Generation ticket prevents stale overwrites. |

**Phase machine (2 states):**

```
idle ←→ streaming
```

| Phase | Allowed Ops | Blocked Ops |
|-------|-------------|-------------|
| `idle` | All | — |
| `streaming` | append, updateLast, remove | reconcile, replace (queued) |

**Key design decisions:**
- **One Writer, Many Readers** — all mutations go through store; `store.onChange` fires `setMessages` to React
- **45s watchdog** — detects stuck streaming phase (silent TCP death); resets to idle as last resort
- **Eager store creation** in `initTabState` eliminates fallback code paths
- **`endStreaming()` flushes reconcile thunk** — any `replace()` after needs `_reconcileGen++`
- **Strict cross-tab isolation (2026-06-18):** subscription guard uses `currentActiveTabId !== tabId → return` with NO streaming phase bypass. Previous escape hatch (`store.phase !== 'streaming'`) caused cross-tab message leak.

**Integration points:**
- `insertOptimisticMessages()` helper → all 5 send paths use `store.appendMany`
- Tab restore paths (`handleTabSelect`, `loadSessionMessages`) seed store via `store.replace`
- `handleStop` calls `store.endStreaming()` before streamGen increment

### Root-1 SSOT — Single Source of Truth + Durable Message Contract (shipped 2026-06-20)

The structural fix behind ~18 same-day desync patches: **the backend state machine is the single authority for "is this session busy"; a user message has a durable server-side arrival contract and is never silently deleted.** Designs: `Knowledge/Designs/2026-06-20-root1-session-truth-and-message-contract-design.md` (Swarm's design, superseded for the backend half by the as-built pipeline). **Split ownership:** the *backend* SSOT + durable pending-message contract is Swarm's (`backend/core/session_pending.py`, 528 LOC, pipeline `run_3f4f4805`); the *frontend* content-reconciliation half is Kiro's spec `.kiro/specs/frontend-backend-state-reconciliation/` (`design.md` + `tasks.md`). Closes the COE10 failure class (state has no single owner + message can fall into the DB-delete/frontend-queue gap).

**Two structural commitments:**
1. **Backend = single source of truth.** Frontend mirrors backend-reported state; it never adjudicates or force-clears based on its own inference. The `/sessions/streaming-state` read API is the one truth feed.
2. **Durable message contract.** A message arriving while the session is non-idle is persisted `sent=0` (not deleted), drained when the session reaches IDLE, then marked `sent=1`. The "will be sent automatically" promise is now true.

**Server-side pending-message contract (`session_pending.py`):**

| Row phase | `sent` | `claimed_at` | Meaning |
|-----------|--------|--------------|---------|
| pending | 0 | NULL | queued, awaiting drain |
| claimed | 0 | `<ts>` | a drain took it, `send()` in flight (F4: never lose a claimed-but-undelivered msg) |
| sent | 1 | (cleared) | confirmed delivered to subprocess |

- **DB (schema v6):** `messages.sent INTEGER NOT NULL DEFAULT 1` + `messages.pending_seq INTEGER` (per-session monotonic) + unique index on `(session_id, pending_seq)` for cross-process monotonicity (NULL exempt).
- **FIFO-coalesce** (`combine_pending`): pending rows persisted individually but the drain claims the whole set atomically and merges into ONE turn (text joined `\n\n` latest-last; multimodal lists concatenated). Preserves P5 (single in-flight turn) + P4 (exactly-once).
- **Serial drain worker** (`session_router.py`): `_drain_queue` + `_drain_worker_task`, de-duped per session (`_drain_enqueued`). NEVER drains inline on the streaming/hook stack (F1 deadlock/recursion guard) — `enqueue_drain(session_id)` signals; the worker calls `send()`.
- **Chokepoint `sent=1` filter** on message readers + **FTS recall excludes unsent rows** → an un-drained pending message is never injected as phantom context on cold resume (the original reason the old `delete_last_user_message` existed — solved without deleting).
- **`client_id` threaded into pending metadata** → optimistic frontend echoes reconcile 1:1 against drained DB rows (the correlation key the old `retryPayload` lacked).
- **Disconnect = Option B-soft:** SSE client disconnect → clean IDLE + drain (the old `_generating_after_disconnect` flag is deleted; only 2 explanatory comments remain).

**WAITING_INPUT durable handling (Root-1 + Root-3 shared seam):**
- `has_outstanding_tool_use` guard: a new message during WAITING_INPUT persists `sent=0` (drains later) rather than being consumed-as-answer / killing the session.
- `force_unstick_waiting_input()` retained as last-resort recovery for a genuinely stuck WAITING_INPUT (frontend died before answering).
- `routers/chat.py` `/sessions/streaming-state` exposes `waiting_input`, `pending_count`, `pending_question {toolUseId, questions}` → frontend re-surfaces a (possibly SSE-lost) AskUserQuestion from the authoritative read model (15s reconcile), and `chat.ts:getStreamingState` mirrors these fields.
- **Honest-signal / post-disconnect-flush (OT01, shipped 2026-06-29, run_0beb9e71, commit e7a2c011):** `/sessions/streaming-state` also exposes `post_disconnect_flushing` (= `unit.is_post_disconnect_flushing`, true while the subprocess is still flushing a long turn into the DB after the SSE dropped — clean-IDLE but ALIVE). The frontend consumes it at **THREE** reconcile decision points, all of which were previously flushing-blind and would prematurely show "Connection lost" / drop the spinner mid-flush: (1) `healGraceExpiryVerdict` (heal-grace expiry → soft "still-working" vs hard error), (2) `forceClearStreamVerdict` (`flushing` exemption), (3) `desyncConvergeVerdict` (the 90s MessageStore-watchdog phase→idle convergence; fires BEFORE force-clear and `continue`s). All three are pure predicates in `streaming-guards.ts`, share the existing **120-min cap** as the stuck-flag backstop, and degrade safely (`?? false`) for an older backend lacking the field. **Invariant: any new branch that decides "is this turn over?" MUST consult `post_disconnect_flushing` — a branch that doesn't is a silent OT01 sibling.**

**Root-3 AskUserQuestion surfacing (shipped 2026-06-20):** cross-tab "❓ needs answer" toast persists + jumps to the asking tab; the pending question is answerable the moment it arrives on any tab (not active-tab-only, not 15s-late). Agent discretion guidance (prefer stated-assumption over mid-task AskUserQuestion in autonomous runs) added to AGENT.md Confusion Management.

**Why this kills the whack-a-mole:** one authority → no "frontend decided backend was wrong" desync; durable contract → message can't fall into the DB-delete/queue-miss gap. The 11 recovery mechanisms stop *competing* — they all read/write the one authoritative state.

### Root-2 Load Amplifier Caps (shipped 2026-06-21)

Complementary to Root-1: Root-1 narrows the desync race window; Root-2 stops heavy load from re-growing that window to minutes. **No state-machine change — pure resource management.** Design: `Knowledge/Designs/2026-06-20-root2-load-amplifier-caps-design.md`. Pipeline `run_c4d62c5d`.

The deliverable closed 3 NO-GUARD gaps where a quantity was *measured* but never *capped* (`session_unit.py`, `compaction_guard.py`, `streaming_orchestrator.py`):

| Cap | Constant | Value | Effect |
|-----|----------|-------|--------|
| **G1 context-ring** | `SOFT_COMPACT_PCT` | 60% of window | At next IDLE, proactively compact *before* the slow turn (soft-first via `prompt_builder.build_context_warning`). `SOFT_COMPACT_COOLDOWN=180s` prevents back-to-back compaction. |
| **G2 long single turn** | `LONG_TURN_HEARTBEAT_S` | 60s | Emit a "still working" notice once a turn's wall-clock crosses this (one notice per interval, no spam), so a legitimately long tool-loop reads as EXPECTED (not hung). VISIBILITY-only — never kills. Event-driven (checked when an SDK event arrives during STREAMING); per-turn throttle (`_last_heartbeat_elapsed`, reset on STREAMING entry). |
| **G3 turn-count floor** | `HARD_FLOOR_BUFFER` | max_turns − buffer | Independent of self-heal — caps runaway turn accumulation. |

**Anti-pattern correction (PIT01, commit `d32c3e9b`):** an initial Root-2 build also added a *per-turn tool-count / duration runaway budget* to `CompactionGuard`. It was **removed the same day** — that budget's `interrupt()` poisoned the subprocess on legitimate deep-research turns (many distinct tool calls), and the "parallel-fails / serial-works" signature was misread as a concurrency ceiling. Lesson: transient resource poisoning masquerades as a structural feature-ban; the surviving guards are diversity-stall (set-overlap) only, which don't punish high-diversity legitimate work. Related: `streaming_orchestrator.py` self-heals a poisoned subprocess that returns an instant empty `error_during_execution` (commit `65cea32b`).

### RecoveryCoordinator — Unified Recovery Decision Authority (shipped 2026-06-25)

The single decision authority for session recovery across all kill paths. Previously 8 independent decision-makers (self-heal, RSS-proactive, per-session RSS, streaming timeout, OOM, tool-hang, stuck WAITING_INPUT, TTL) each carried their own ad-hoc kill logic. Design: `Knowledge/Designs/2026-06-24-session-lifecycle-unified-recovery-design.md`. Pipelines `run_4988bfb4` (extraction), `run_9e5b7c97` (multi-shape policy), `run_25f4b74c` (escalation routing).

**Strangler-fig delegation (NOT replacement):** `RecoveryCoordinator` (`session_healing.py:675–811`) **holds** the existing `HealingLoop` (injected at construction) and delegates all breaker state to it (`record_heal_start/success/failure`, `heal_attempts`). It owns only the **DECISION** (may-we-recover + what-kind + escalation); the **kill MECHANICS** stay in `SessionUnit`. This is why HealingLoop's 5 test files stayed green untouched — the blast-radius check (1 prod caller, 5 test files) picked *delegate* over *absorb* (DEC06).

**Four policy shapes** (`session_healing.py:536–672`) dispatch the recovery decision; they share the universal guard (`enabled`/`user_stopped`/`eligible_states`) and the verdict vocabulary, but differ in the gate:

| Decision method | Policy | Trigger | Gate |
|-----------------|--------|---------|------|
| `decide()` | AttemptBreakerPolicy | Self-heal (HealthSensor 5 sub-triggers) | max 3 attempts, 60s cooldown, one-shot terminal signal |
| `decide_rss()` | CooldownThresholdPolicy | IDLE tree RSS > 3.5GB | 180s per-unit cooldown, no breaker |
| `decide_bare()` | BareThresholdPolicy | per-session RSS > 7GB; stuck WAITING_INPUT (4h) | bare threshold, state-targeted, no cooldown |
| `decide_graceful()` | GracefulEscalationPolicy | streaming timeout; tool-hang (PID watchdog) | escalating ladder: base verdict → harder verdict on Nth attempt |

**Seven recovery verdicts** (`RecoveryVerdict` enum, `session_healing.py:451–470`): `SKIP` (guard failed), `DEFER` (cooldown active), `PROCEED_GRACEFUL` (two-phase wrap-up), `PROCEED_KILL` (kill + keep `--resume`), `PROCEED_INTERRUPT` (warm non-destructive), `PROCEED_KILL_HARD` (kill + drop `--resume` identity), `ESCALATE` (breaker tripped, recovery exhausted). The `PROCEED_KILL` vs `PROCEED_KILL_HARD` split (keep-vs-drop resume identity) is the single most safety-relevant verdict distinction (PIT16).

**RecoveryTransaction — `_crash_to_cold` TOCTOU close (`session_unit.py:3634–3655`):** the kill sequence (arm checkpoint → DEAD → force_kill → cleanup → COLD) is now lock-protected (`self._lock` held across the whole sequence). Previously two concurrent tasks (streaming loop + lifecycle manager) could interleave between `transition(DEAD)` and `transition(COLD)`, causing double `force_kill` + corrupted state. The lock makes the transaction idempotent: N concurrent callers → exactly ONE teardown, rest no-op. Tool-hang + OOM escalation now route through the coordinator (`R3e/M4`).

### Single Render Source — Structural Kill of the Reconcile-Gap (shipped 2026-06-25)

The structural root-cause fix for the **#1 recurring bug** (COE07/08/09 class, ~33 prior patches): a complete backend reply, fully persisted in the DB, rendered **truncated** on screen with a phantom Continue button after tab-switch/resume. Design: `Knowledge/Designs/2026-06-25-reconcile-gap-render-source-design.md`. Pipeline `run_9db9f987`.

**Why 33 fixes didn't hold:** every prior fix targeted a *transport/timing/merge* layer (backend persistence, SSE, store reconcile, boundary dedup) — layers 1–9. The actual defect was in the **React render-source selector** itself, untouched. `TabView.tsx:152` had a dual-source selector `(storeMessages.length>0) ? storeMessages : messagesProp` — whenever the store was momentarily empty (normal during lifecycle transitions), it fell back to a **stale `messagesProp` snapshot** (a `tabMapRef` value mutated without triggering re-render), freezing the UI at a truncated version. Split-brain: two render sources, one stale.

**The fix — one immutable rule:** the rendered list comes from **exactly one source, the per-tab MessageStore subscription**. `messagesProp` is no longer a render source.
- `TabView.tsx:152` now reads `const messages = storeMessages ?? []` — store or empty, never prop-fallback.
- Every keep-mounted TabView (active AND background) holds its own live `useMessageStore(tabId)` subscription, and **all** session-load paths (`loadSessionMessages`, `handleSelectTab`, `initTabState`, `reconcile/mergeTabFromDb`) seed the store so it is the consistent authority.
- `ChatPage.tsx:804–809` reverse-flow guard: the old unconditional `store.replace(tabState.messages)` (which could CLOBBER a populated store with a stale snapshot) is now an empty-store-only POPULATE (`if (switchStore.messages.length === 0 && tabState.messages.length > 0)`), synchronous (no await) → closes the TOCTOU.
- Probe logs `RENDER-DIVERGE` elevated debug→warn (`useChatStreamingLifecycle.ts:1694/1707/2582/2620`) — post-fix this can only indicate harmless background-tab prop-lag, never a render defect.

Divergence is now **structurally impossible**: a store-only render cannot render stale prop data. Verified by `TabView.singleSource.test.tsx` (store wins over prop / empty store renders empty / switch-back hydrates from live store). Companion: `ab3727cf` shows a spinner when backend streams but the tab shows idle (saturation-aware).

### AskUserQuestion Block-Hook — Headless-Mode Human-in-the-Loop (shipped 2026-06-23)

Makes `AskUserQuestion` truly block-and-wait for the user in headless/SDK mode, instead of the agent self-answering and proceeding. Design: `Knowledge/Designs/2026-06-23-askuserquestion-blockhook-design.md`.

**The bug:** `AskUserQuestion` has `requiresUserInteraction:true` but no interactive UI in SDK mode. The Claude CLI **self-resolves** the unanswerable tool call with `is_error:true` ~19ms after emission — long before the user answers. The agent receives the error, gives up ("No answer, I'll proceed"), and the real answer (arriving seconds-to-minutes later) lands on an already-resolved tool call and is swallowed.

**The mechanism — a `PreToolUse` gate that intercepts BEFORE CLI self-resolution** (`create_ask_question_gate`, `security_hooks.py:210–328`; registered `matcher="AskUserQuestion"` in `hook_builder.py:217–226`):
1. **Registers the waiter synchronously** (`ask_question_manager.py:271`) *before* surfacing — closes the enqueue↔wait race.
2. Enqueues a permission request with `kind:"ask_user_question"` discriminator onto the per-session queue (orchestrator detects it, `streaming_orchestrator.py:406–437`, emits an `ask_user_question` SSE event keyed on `toolUseId`).
3. **Blocks on `wait_for_answer(tool_use_id)`** — an `asyncio.Event`, 4-hour timeout (`ask_question_manager.py:57`).
4. **On answer:** unblocks, returns `permissionDecision:"allow" + updatedInput.answers` → the CLI `call()` returns the REAL answers, not the error.
5. **On timeout:** returns `permissionDecision:"deny"` (re-ask) — NEVER injects a fabricated empty answer (PIT31: fail-loud ≠ fail-hard).

**Answer flow-back:** `POST /api/chat/answer-question` (`chat.py:620–700`) validates non-empty answers, calls `continue_with_answer(tool_use_id, answer)` (`session_unit.py:2669–2763`) → `ask_question_manager.set_answer(...)` signals the waiter → `_read_formatted_response()` resumes the SAME SDK stream. The `tool_use_id` (keyed on SDK `block.id`) is the single correlation key threaded through all paths: hook entry → waiter → orchestrator `has_live_waiter()` drop-guard → SSE → endpoint → `set_answer()`.

**Auto-resend on backend recovery (`5abe1732`):** if a connection-phase send exhausts its reconnect budget while the backend is down (e.g. ~60s daemon redeploy), the question never reaches the backend. The frontend arms `_pendingResendOnRecovery` (`useChatStreamingLifecycle.ts:3505–3525`, connection-phase + `!hadData` only, bounded `RESEND_MAX_ATTEMPTS=2`); on `backend-recovered`, `resendTabOnRecovery()` (`ChatPage.tsx:487–552`) atomically clears the flag, increments the counter, clears the error placeholder, and re-issues the same stream — so the question is never silently lost. Fail-closed guards (clear-before-resend = idempotent; connection-phase-only; bounded) make double-answer/resend-loop structurally impossible.

### E2E Verification Chain + Deploy Unification (shipped 2026-06-17)

Three-layer automated verification replacing manual post-deploy checks. Design: `Knowledge/Designs/2026-06-17-e2e-verification-deploy-unification-design.md`.

**Three layers:**

| Layer | What | Speed | Cost | Scope |
|-------|------|-------|------|-------|
| **L1: Contract Tests** | 16 frontend tests with real HTTP+SSE fixture server (`@vitest-environment node`) | <3s | Free | Request shapes, SSE parsing |
| **L2: Smoke E2E** | `scripts/smoke_e2e.py` — creates real session → SSE stream → result event → cleanup | <30s | ~$0.001 | Full chat pipeline against live daemon |
| **L3: Daily Canary** | Scheduled job (03:30 UTC) — smoke + eval canary + Slack alert on failure | <60s | ~$0.01 | Continuous production health |

**Deploy unification (`prod.sh deploy`):**
- Auto-scope detection: `git diff` between HEAD and deployed hash (from daemon `.version` file)
- Backend-only: PyInstaller build → rsync → SIGTERM (KeepAlive auto-restarts)
- Frontend-only: `npm run build:all` → Tauri rebuild → relaunch
- Both: sequential (backend first, frontend second)
- Hive: semantic version verification against remote CloudFront endpoint
- Post-deploy: L2 smoke runs automatically

**Key files:** `prod.sh` (`cmd_deploy()`), `scripts/smoke_e2e.py`, `desktop/src/__tests__/contract/` (server.ts + fixtures + 3 test files), `Services/swarm-jobs/user-jobs.yaml` (smoke-canary job).

**Frontend tab system** (`useUnifiedTabState.ts`, 727 lines):
- Dual-state: `useRef<Map>` for O(1) sync reads + `useState` for React re-renders.
- Hard ceiling: `MAX_TABS_HARD_CEILING = 4`. Dynamic: `GET /api/system/max-tabs → { maxTabs, chatMax, memoryPressure }`.
- Persistence: `~/.swarm-ai/open_tabs.json` with 500ms debounced write. Restored on mount with ceiling cap.
- Tab CRUD: `addTab()` (checks chatMax), `closeTab()` (reselects adjacent), `selectTab()` (updates active).

### Session Observation Layer (commit 8ba70094)

Real-time tool-call recording for crash recovery, DDD cultivation, and evolution pattern mining.

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| **ObservationRing** | `core/observation_ring.py` | ~120 | Fixed-size deque (200 slots, ~80KB). Records tool name, intent, files, duration, status. <0.1ms per write. |
| **observation_recorder** | PreToolUse hook (LAST) | — | Records tool invocation start. Non-blocking. |
| **observation_completer** | PostToolUse hook (LAST) | — | Records completion + duration. Non-blocking. |

**Consumers (pull-based, no pub/sub):**
1. **Checkpoint Writer** — every 10 calls, enriches `session_checkpoint.json` with last 10 observations (enables crash recovery that knows WHAT was happening)
2. **DDD Event Emitter** — emits FILE_EDITED/CORRECTION events to EventDispatcher on qualifying observations (real-time DDD cultivation instead of batch-on-close)

**Design decisions:** In-memory ring (not file append) for latency. Bounded 200 slots for memory safety. Buffer lost on crash is OK — checkpoint persists snapshots. Part of the 3-step "Self-Improving Loop": Observation (Step 1, shipped) → DDD Auto-Approval Gate (Step 2, shipped) → Pattern Miner (Step 3, partial).

### Context Management

11 context files (P0–P10) assembled into the system prompt via a 3-stage pipeline with L1 caching and model-aware token budgets.

**Assembly pipeline:** `ContextDirectoryLoader.load_all()` → `PromptBuilder.build_system_prompt()` → `SystemPromptBuilder.build()`.

| Priority | File | Domain | Truncatable |
|----------|------|--------|-------------|
| P0 | SWARMAI.md | Core identity | Never |
| P1 | IDENTITY.md | Agent name, avatar | Never |
| P2 | SOUL.md | Personality, tone | Never |
| P3 | AGENT.md | Behavioral directives | Head |
| P4 | USER.md | User preferences | Head |
| P5 | STEERING.md | Session overrides | Head |
| P6 | TOOLS.md | Tool guidance | Head |
| P7 | MEMORY.md | Cross-session memory | Head |
| P8 | EVOLUTION.md | Self-evolution registry | Head |
| P9 | KNOWLEDGE.md | Domain knowledge | Head |
| P10 | PROJECTS.md | Active projects index | Head |

**Token budget tiers** (`compute_token_budget()`):

| Model Context Window | Budget | L1 Cache | Notes |
|---------------------|--------|----------|-------|
| ≥500K (Opus/Sonnet 4.6) | 100K tokens | Yes | Default for 1M models |
| ≥200K | 50K tokens | Yes | Mid-tier models |
| <200K | 30K tokens | No (L0 direct) | Small models, no caching |

**L1 cache:** `L1_SYSTEM_PROMPTS.md` file with `<!-- budget:NNNNN -->` header. Freshness: git-first (`git status --porcelain`, 15s TTL), mtime fallback. Budget tolerance: ±5%. Used when budget ≥64K tokens; below 64K uses L0 direct assembly. Token estimation: CJK-aware — see "How token estimation works" below for the CALIBRATED coefficients (the old "1.5 chars/token CJK, 4/3 words/token Latin" figures here were recalibrated in run_3f25a73a, 2026-06-28).

**Channel-specific exclusions:**

| Channel Type | Excluded Files | Reason |
|-------------|----------------|--------|
| Group channels | MEMORY.md, USER.md | Prevent personal data leakage |
| Non-owner DMs | + EVOLUTION.md, PROJECTS.md | Lightweight mode, saves ~3.5K tokens |
| Owner DMs | None | Full context |

**PromptBuilder injection layers** (in order):
1. ContextDirectoryLoader (11 context files with budget enforcement)
2. BOOTSTRAP.md (first-run onboarding only)
3. DailyActivity (most recent file, capped at 2K tokens)
4. Proactive Intelligence briefing (excluded for channel sessions)
5. UserObserver suggestions (pending USER.md updates)
6. Active Session Digest (sibling context awareness, ~50 tokens per sibling)
7. Editor context (currently open file)
8. SystemPromptBuilder (identity, safety, channel_security, workspace, datetime, runtime)

**SystemPromptBuilder sections:** identity → safety → channel_security (3-tier: owner/trusted/public, with file sandboxing to `channel_files/{external_id}/`) → large_content processing → workspace path → datetime (UTC + local) → runtime metadata.

**Resume Context Enrichment** (`context_injector.py`) — 5-layer extraction for cold session resume:

| Layer | What | Cap |
|-------|------|-----|
| 1. Structured checkpoint | Last request (4K chars), files touched (30 max), git commits (5), skills used, tool activity (top 6), user directives (300 chars each), crash checkpoint merge | ~5-10K tokens |
| 2. Uncommitted git state | `git status --short` + `git diff --stat` via `asyncio.to_thread()`, 3s timeout | ~500 tokens |
| 3. Assistant conclusions | Last 5 assistant text blocks, 1500 chars each | ~5-8K tokens |
| 4. Key tool results | Read/Grep/Bash/Agent/Edit/Write results, max 15, deduped by file path | ~5-15K tokens |
| 5. Recent conversation | Last 30 turn pairs, 4K chars each, early-exit at 60% budget | ~20-60K tokens |

**Resume budget by model:**

| Model Context | Token Budget | Max Messages | DB Fetch Limit |
|--------------|-------------|-------------|---------------|
| ≥500K | 150K | 500 | 1000 |
| ≥200K | 60K | 200 | 500 |
| <200K | 20K | 80 | 200 |
| Channel (any) | 32K | 50 | 120 |

Trimming priority: tool_results → recent_turns → conclusions. Checkpoint + uncommitted never trimmed.

**Context window estimation:** SDK `ResultMessage.usage` aggregates across ALL agentic turns. Divide by `num_turns` for per-call estimate. Warning at 70%, critical at 85%. Watchdog timeout: `180 + (input_tokens/100K)*30 + user_turns*5`, max 600s.

**Key files:** `context_directory_loader.py` (budget, cache, assembly), `prompt_builder.py` (injection layers, watchdog), `system_prompt.py` (identity, safety, channel_security), `context_injector.py` (resume enrichment).

### Skills

Skills live in `backend/skills/s_<name>/` and follow a standard layout: `SKILL.md` (behavioral spec), `INSTRUCTIONS.md` (agent-facing guidance), optional `scripts/` (Python helpers), optional `data/` (reference CSVs/assets), and `manifest.yaml` (metadata).

**Recently expanded skills (v1.8.x):**

| Skill | Key Changes |
|-------|-------------|
| **s_pollinate** | Major expansion: `scripts/confidence_score.py`, `generate_shorts.py`, `log_publish.py`, `publish_dashboard.py`, `topic_backlog.py`; TTS via AWS Polly (`scripts/tts/backends/polly.py`); output to `Knowledge/Pollinate`; 小红书 multi-image format; file links open in editor |
| **s_frontend-design** | UUPM design intelligence integrated: 7 reference data CSVs (`charts`, `colors`, `landing`, `products`, `styles`, `typography`, `ui-reasoning`, `ux-guidelines`), `scripts/design_system.py` (1148 lines), `scripts/core.py`, `scripts/search.py`, `manifest.yaml` |
| **s_web-design-review** | New skill: UX-guideline-backed design review; shares `ux-guidelines.csv` data and `core.py`/`search.py` script pattern with s_frontend-design |
| **s_release** | Full `INSTRUCTIONS.md` added (265 lines); `sync-version.sh` integration; VERSION file now included in release workflow to prevent silent version downgrade |
| **s_deep-research** | Phase 0 intent planner added; depth calibration; 5 gap fixes |
| **s_learn-content** | Depth calibration; 5 gap fixes |

### Autonomous Pipeline — "Coding as Black Box" (AIDLC Phase 3)

`s_autonomous-pipeline` — full lifecycle orchestrator from one-sentence requirement to push-ready delivery.

**Architecture: 9 stages · 3 gates · 2 modes (2 sub-agents).**

![Autonomous Pipeline — 9 Stages · 3 Gates · 2 Modes](assets/aidlc-autonomous-pipeline-v4.svg)

> ⭐ Official pipeline figure — `assets/aidlc-autonomous-pipeline-v4.svg`. Same canonical file embedded in codebase README (EN+CN), AIDLC DDD, and here. Update this one file; older pipeline SVGs are superseded.

- **9 stages** (canonical/external): EVALUATE → THINK → PLAN → BUILD → REVIEW → TEST → ADVERSARIAL → DELIVER → REFLECT. ADVERSARIAL is architecturally stage 7 but executes *embedded inside DELIVER* as a blocking sub-step, so `pipeline_profiles.py` `full` lists 8 entries while all docs say "9 stages" (SoT `pipeline_profiles.py:7-13`).
- **3 gates** (3 moments of truth, SoT `INSTRUCTIONS.md:77-79,1039`): **Gate 0** (inside EVALUATE→THINK) guards *framing* — Understanding Gate / diagnose-before-build + M3 skeptic sub-agent; **Gate 1** (after PLAN, before BUILD) guards *plan* — Skeptic + SSA; **Gate 2** (inside DELIVER) guards *build* — Adversarial fresh-context sub-agent (BLOCKING). Don't renumber Gate 1/2 (wired into landmarks ④/⑧, GS021, tests).
- **2 modes** (SoT `INSTRUCTIONS.md:144`, both run ALL stages — differ only in execution): **Full** = BUILD→REVIEW→TEST once; **Goal** = `goal_cycle` loops BUILD+TEST to a measurable DoD (can run scheduled/cross-session). Profile immutable after EVALUATE (GC12).

Three execution phases (Decision → Execution → Quality) with gate sub-agents that structurally prevent shipping bad code. The Quality Convergence Loop iterates the delivery candidate until all 6 gate layers pass — or escalates with a precise gap report. Quality is deterministic (converge or escalate), not probabilistic (one-shot).

**DDD/SDD/TDD Trilogy:**
- **DDD** (Domain-Driven Design) → Stages 1-3: judgment — *should we build this? how?*
- **SDD** (Spec-Driven Design) → Stage 3 output, verified in Stage 5: specification — *what exactly?*
- **TDD** (Test-Driven Development) → Stage 4: verification — *did we? does it work?*

**9 stages (sequential, produce delivery candidate):**

| Stage | Purpose | Key Output |
|-------|---------|------------|
| **EVALUATE** | GO/DEFER/REJECT gate with ROI scoring | Decision + scope + acceptance criteria |
| **THINK** | 3 design alternatives with tradeoffs | Recommended approach (artifact) |
| **PLAN** | File-level implementation plan (SDD) | Step sequence + test matrix |
| **BUILD** | TDD red-green: write failing tests → implement → verify | Code + tests (0 regressions) |
| **REVIEW** | 18+ review patterns (RP1–RP29) + integration trace | Findings + fixes |
| **TEST** | Full test suite + E2E smoke | Test results |
| **ADVERSARIAL** | Fresh sub-agent attack review (mandatory) | Issue list or clean bill |
| **DELIVER** | Completion Audit + Confidence Score + Convergence Loop + Report | `.artifacts/runs/<id>/REPORT.md` |
| **REFLECT** | Capture lessons → IMPROVEMENT.md → DDD cultivation proposals | Proposals queued for approval |

**Quality Convergence Loop (DELIVER internal sub-loop):**

6-layer Push-Ready Gate — ALL must pass simultaneously:
1. **L1: Tests Pass** — all new + existing tests green
2. **L2: Type-Safe** — no type errors, linter clean
3. **L3: No Regressions** — pre-existing test suite still passes
4. **L4: Adversarial Clean** — all findings from Stage 7 resolved
5. **L5: DDD Conformance** — follows TECH.md conventions, avoids IMPROVEMENT.md anti-patterns
6. **L6: Decisions Resolved** — all taste/judgment decisions surfaced

Max 3 iterations. Each iteration applies a targeted fix → re-verifies entire gate. Failure = CHECKPOINT with precise escalation report.

**Per-stage Feedback Loop preamble:** Before every stage, declare SIGNAL (observable success proof), CHECK (how to verify), FAIL (what failure looks like). Prevents "vibes-based completion."

**REFLECT → DDD Cultivation:** Every pipeline run's REFLECT stage extracts lessons and proposes updates to TECH.md and IMPROVEMENT.md via the DDD Cultivation Engine. Cultivation is **auto-apply by default for SAFE changes** — additive appends to whitelisted sections (`ddd_cultivation.py:_cultivate_proposals`→`apply_to_ddd`) and evidence-driven retires (auto-delete, caps 2/run + 3/day, reversible archive+.bak) apply autonomously. **Only RISKY changes escalate to the human proposal queue**: protected/authoritative zones (PRODUCT Vision/Non-Goals, TECH Architecture, SELF.md) and conversation-source knowledge (DEC19 force-escalate). This makes the pipeline DDD's richest feed channel (Channel 3). (run_254f5e52: corrected from a stale "never auto-applied" claim.)

**Decision classification:** mechanical (auto-approve), taste (batch at delivery gate), judgment (block for human L2 BLOCK). Checkpoints on: L2 BLOCK, retry exhaustion, context budget limits. Resume from checkpoint in fresh session via `artifact_cli.py resume`.

**6 pipeline profiles** (`pipeline_profiles.py`) — all include THINK (no profile patches without thinking):

| Profile | Stages | Use When |
|---------|--------|----------|
| **full** | evaluate → think → plan → build → review → test → deliver → reflect | New features (default) |
| **bugfix** | evaluate → think → plan → build → review → test → deliver → reflect | Bug fixes with known root cause |
| **trivial** | evaluate → think → build → review → test → deliver → reflect | Config change, 1-file tweak |
| **goal** | evaluate → think → plan → goal_cycle → deliver → reflect | Externally-measurable end-state (command exit 0) |
| **docs** | evaluate → think → plan → deliver → reflect | Documentation tasks |
| **research** | evaluate → think → reflect | Investigation only |

**Profile selection principle:** Goal = "done" verified by external command (metric reached, all items addressed). Full = "done" is artifact existing + passing review. Not based on file count or scope size.

**Pipeline v2 Extensions (2026-05-14):**

1. **Independent AC Verification (DELIVER Step 2.5):** After Completion Audit, independently re-reads each test body and verifies it actually exercises the AC's behavior. Pre-flight before adversarial. Confidence: verified=+3, claimed=+1, failed=-3. Addresses C011 class.

2. **Goal Loop Profile:** Iterative BUILD+TEST cycles toward measurable DoD. Two DoD types (command: shell exit code, rubric: LLM + explicit criteria). Budget gates, regression protocol (fix→retry→revert→checkpoint), periodic REVIEW every N cycles, final ADVERSARIAL on total changeset, two-tier REFLECT (mini per cycle + full at end). Inline mode (same session) + scheduled mode (job system). See `stages/goal_cycle.md`.

Design: `Knowledge/Designs/2026-05-14-pipeline-v2-consolidated-upgrade.md`

**Pipeline v3 — "Head-Light Rebalance" (2026-05-16):**

Rebalances quality investment from tail-heavy (all verification in DELIVER) to balanced (problems prevented in THINK/PLAN, easily verified in DELIVER). Industry patterns adopted after source code verification: Sweep (file discovery + sub-request decomposition), AutoGPT (replanning), gstack (confidence + specialist army).

| # | Enhancement | Stage | Problem Solved |
|---|-------------|-------|----------------|
| 3 | **Confidence Gating** | All | ~30% false positives suppressed (unified 1-10 rubric + display threshold) |
| 4 | **Multi-Specialist Review Army** | DELIVER | Single generalist misses domain-specific bugs → 7 scope-gated specialists (Correctness, Security, Performance, API Contract, Integration, Operational + conditional Red Team) |
| 5 | **Exhaustive File Discovery** | PLAN | "Read planned files" misses callers → grep ALL affected code first |
| 6 | **Change Spec** | PLAN | BUILD derives sequencing ad-hoc → topologically-sorted atomic todo list |
| 7 | **Test Strategy** | PLAN | BUILD discovers fixtures ad-hoc → AC→test approach pre-mapped |
| 8 | **Design Risk Probe** | THINK | Grill Protocol rarely triggered → self-answering assumption verification |
| 9 | **Micro-Replan** | BUILD | Blind retry on failure → auto-diagnose + replan single AC after 2× failure |

Design: `Knowledge/Designs/2026-05-12-autonomous-pipeline-design.md` (Section 12)

**Pipeline v4 — "BUILD is the Root Cause" (2026-05-29):**

Addresses the finding that REVIEW/DELIVER were compensating for low BUILD quality. Shifts quality investment upstream.

| # | Enhancement | Stage | Problem Solved |
|---|-------------|-------|----------------|
| 10 | **Litmus Pre-Gate** | REVIEW (before adversarial) | 30s structural pre-screen avoids wasting 30K tokens on garbage BUILD |
| 11 | **AC Coverage Matrix** | BUILD (mandatory artifact) | Agent can no longer skip ACs without detection |
| 12 | **TEST 3-Layer Strategy** | TEST (code-enforced) | AC-driven + dependency-scoped + import smoke |
| 13 | **Adversarial Focus Shift** | REVIEW agents | "Find bugs not gaming vectors" — ~30% FP reduction |
| 14 | **Cross-Stage Traceability** | BUILD→TEST→DELIVER | ac_coverage count validated across artifacts |

**Pipeline v5 — "Meta-Intelligence + Full Stage Coverage" (2026-06-08):**

Two structural changes:
1. **Full stage coverage:** All 6 profiles now include THINK. Goal profile expanded from 3 stages (`evaluate→plan→goal_cycle`) to 6 stages (`evaluate→think→plan→goal_cycle→deliver→reflect`). Goal now exits to formal DELIVER+REFLECT stages — no inline REFLECT.
2. **Meta-Intelligence Layer (5 layers, self-learning):**

| Layer | Purpose | Implementation |
|-------|---------|----------------|
| L1 OBSERVE | Per-run telemetry | `run-observe` CLI (7 event types: stage_start/end, profile_selected, abandon, think_depth, requirement_shape, adversarial_patterns, review_gap) |
| L2 ANALYZE | Pattern extraction | `pipeline_analytics.py` — 6 dimensions across 305 runs, produces `pipeline_intelligence.json` |
| L3 ADAPT | Intelligence injection | EVALUATE reads intelligence for profile suggestion + budget calibration; BUILD gets chronic RP warnings; THINK has minimum depth gate |
| L4 RECOVER | Abandon resilience | Abandon protocol (6 reason categories), stale run cleanup, recovery artifacts for intelligent resume |
| L5 SPECIALIZE | New review agents | `state-machine.md` (transition completeness, stuck states) + `concurrency.md` (pool sizing, lifetime mismatch) |

**9 code-enforced completion gates** (cannot be bypassed):
1. All profile stages completed (or skipped with reason)
2. Non-skippable stages enforced: goal_cycle, deliver, reflect
3. Goal adversarial_review: true required
4. DELIVER artifact_id required (full/bugfix)
5. Validator (profile_tier, findings resolved)
6. REFLECT substantive lessons (>20 chars)
7. REPORT.md exists + >500 bytes
8. Profile immutability (no downgrade after EVALUATE)
9. stage_doc_consumed (must read stage doc before completing)

Design: `Knowledge/Designs/2026-06-08-pipeline-meta-intelligence-design.md`

**Dual-Gate Architecture (2026-06-16, shipped fc384186 — superseded by the 3-gate v6 below):**

> ⚠️ Historical (pre-v6) view: 2 gates. **Gate 0 was added 2026-06-26 → the canonical shape is now 9 stages · 3 gates · 2 modes** (see "Pipeline v6 — 3 Gates" immediately below). The diagram below shows only Gate 1/Gate 2.

3 phases · 2 gates (Gate 1 + Gate 2 only — pre-v6):

```
PHASE A: DECISION          PHASE B: EXECUTION              PHASE C: QUALITY
┌─────────────────┐  ┌──────────────────────────────┐  ┌─────────────────────┐
│ EVALUATE → THINK │  │ PLAN → [GATE 1] → BUILD →   │  │ DELIVER → REFLECT   │
│                  │  │ REVIEW → TEST → [GATE 2]    │  │                     │
└─────────────────┘  └──────────────────────────────┘  └─────────────────────┘
                           ↑                    ↑
                     Skeptic sub-agent    Adversarial sub-agent
```

| Gate | When | Sub-agent | Action on BLOCK |
|------|------|-----------|-----------------|
| **Gate 1** (Pre-Check) | Before BUILD | Skeptic + SSA | Return to PLAN (max 2 retries) |
| **Gate 2** (Adversarial) | Before DELIVER | Attacker | 6L convergence loop (max 3 iter) |

Gate 1 catches: wrong direction, missed constraints, pattern repetition, API hallucination.
Gate 2 catches: runtime bugs, security issues, untested paths, regression risks.

**Pipeline v6 — "3 Gates = 3 Moments of Truth" + the EVALUATE diagnose-before-build gate family (2026-06-26, SHIPPED):**

The dual-gate architecture above guards the *solution* (Gate 1 = is the plan sound)
and the *code* (Gate 2 = is the build correct) — but nothing guarded the *framing*:
"is the problem itself understood correctly?" That happens at EVALUATE, before any
code, and a framing error sails untouched to Gate 2 (full-pipeline cost) or an
external reviewer. v6 adds **Gate 0** at the EVALUATE→THINK boundary.

| Gate | Guards the truth of… | Fires | Catches |
|------|----------------------|-------|---------|
| **Gate 0** (EVALUATE family, below) | *the present* — is the problem/framing understood? | EVALUATE → THINK | **framing error** |
| **Gate 1** (Skeptic + SSA) | *the plan* — approach sound, root not symptom? | after PLAN, before BUILD | symptom-fix, wrong layer |
| **Gate 2** (Adversarial) | *the code* — build actually correct? | inside DELIVER | self-authored bug (CLASS A) |

Numbering: Gate 0 is NOT a renumber — `★ Gate 1/2` are wired into ④/⑧ landmarks,
GS021, tests. All three are dev/CI-only (evaluate.md + `pipeline_validator.py` +
tests) — no daemon deploy.

**Gate 0 is a FAMILY of three sibling checks** in `validate_artifact_data` (all
fire on `stage=="evaluate"`, all dev/CI-only), built on one shared template. They
are NOT one gate — each guards a different *kind* of framing truth, each carries a
**distinct error tag** so callers/tests filter cleanly, and each is independently
profile-gated. Adding a 4th member = copy the template, pick a distinct tag.

| Member (`pipeline_validator.py`) | Guards | Gating | When required | Distinct tag | Shipped |
|---|---|---|---|---|---|
| `_check_understanding_gate` (L736) | the **diagnosis** is observed not inferred (present-tense claim, M1 no-solution-language + M2 no-unresolved-hedge + M3 fresh skeptic) | strict profile (`profile ∉ _RELAXED_UNDERSTANDING_PROFILES`) | always, strict | `Understanding gate:` / `(M1…)` / `(M2…)` | run_862fa4e0 |
| `_check_ambiguity_scan` (L484) | the **spec/own-output** has no residual ambiguity (self-Socratic re-scan; every hit needs a ≥12-char resolution; hit_count/all_resolved must agree) | strict profile; also runs on `think` | always, strict | `Ambiguity scan:` | run_932c0991 |
| `_check_working_backwards` (L578) | the **customer/value framing** for a NET-NEW feature (3 economic fields ≥12 chars: current_workaround / why_better / must_be_true; + non-empty `pre_mortem` reused) | `understanding.work_type=="greenfield"` AND strict | greenfield only | `Working-Backwards:` | run_b5b26ebe |

**Shared template (the family's DNA — reuse, don't reinvent):**
- `_RELAXED_UNDERSTANDING_PROFILES = ("trivial","docs","research")` (L450) — the
  ONE exemption tuple; strict = fail-closed default (any profile not relaxed,
  incl. `standard`/`""`/unknown, is strict).
- A small **anti-laziness char floor** (`_AMBIGUITY_RESOLUTION_MIN_CHARS` /
  `_WB_FIELD_MIN_CHARS` = 12) — structural "is this a real answer" check, never
  content-truth (`isinstance(str)` rejects bare bool/int by ordering).
- **Distinct error tag per member** — sibling tests filter by their own tag
  (`_ug_errors`, `[e for e in errors if "Ambiguity scan:" in e]`), so a new member
  causes **zero breakage** in existing suites (verified empirically each time).
- **Self-report consistency** where summary fields exist (ambiguity hit_count vs
  len(hits)) — gives the agent-supplied summary teeth instead of being decorative.

**Two structural invariants this family exposed (both intended):**
1. **Publish-time-only enforcement.** All three run via `cmd_publish --stage`
   (artifact_cli.py) → `validate_artifact_data`. The completion path
   (`run-update --status completed` → `validate()` → `_check_depth`) does NOT
   re-run them — only DELIVER's adversarial gate is mirrored into `_check_depth`,
   because only DELIVER gates `status:completed`. EVALUATE gates rely on the
   publish exit-code being honored. Accepted: they are quality lenses, not the
   final safety backstop.
2. **`work_type` became load-bearing.** `_check_working_backwards` is the first
   validator logic keyed on `understanding.work_type` (previously cosmetic), and
   the first to code-enforce `pre_mortem` (doc-mandated since the Pre-mortem Gate,
   never checked). It is **fail-open**: a missing/typo'd work_type → no requirement
   (consistent with the relaxed-profile philosophy; a future *safety*-critical
   work_type gate would need work_type-presence enforced in the Understanding Gate).

Theory anchors (shared by the family): aidlc-v2 P3 (same-source generate+judge
can't self-stop = the theoretical name for CLASS A) + P6 (staged decomposition) +
aws-samples ai-plc (envision↔solution wall, PR/FAQ Working-Backwards,
overconfidence-prevention's ambiguity trigger-words). **Philosophy: in an autonomous
pipeline, "Socratic / Working-Backwards" means interrogate the spec & your own
framing, NOT ask the user more** (self-answer first, human confirms at REVIEW as a
taste decision — never a blocking file-based interview, which is the grill-protocol
/ aidlc-#366 rubber-stamp failure). Economics: catch a framing error at EVALUATE =
1 sub-agent; catch it at Gate 2 = the whole pipeline.

Designs: `Knowledge/Designs/2026-06-26-understanding-gate-design.md` (Gate 0 /
Understanding). The Ambiguity-scan + Working-Backwards members extend it — see
SwarmAI IMPROVEMENT.md ADR "Socratic method in the autonomous pipeline".

**Auto-Resume (new):** Detects paused/orphaned pipelines on session start. Max 3 attempts with exponential cooldown (0s → 30s → 60s). File-level fcntl lock prevents concurrent session race. Reduces 34% abandoned rate (all from session crash/hang) toward <10%. SubagentStop hook writes marker files for Gate 2 structural audit.

**Stable facts:** 6 profiles · 3 gate moments (EVALUATE Gate-0 family + Gate 1 Skeptic/SSA + Gate 2 Adversarial) · sub-agents = Skeptic + Adversarial + Gate-0 M3 fresh-context skeptic. Completion-rate ORDERING is stable-by-design (trivial > bugfix > full > goal — lighter profiles finish more often); abandoned runs are ~100% session crash/hang, not pipeline-logic failure. _(Gate topology last changed 2026-06-26.)_
**Live figures (run counts, completion %, LOC, test count) are volatile → measured on demand, NEVER stored here (R30#4; a frozen "64 runs" snapshot here drifted to 562 completed and contradicted a second "245 runs" block):** run counts + per-profile completion = `python backend/scripts/artifact_cli.py run-status`; validator check count = `python backend/scripts/pipeline_validator.py --help`; LOC = `git ls-files 'backend/skills/s_autonomous-pipeline/**' 'backend/scripts/artifact_cli.py' 'backend/scripts/pipeline_validator.py' | xargs wc -l | tail -1`.

**Pipeline validator** (`pipeline_validator.py`, ~2000 lines, 155 tests) — 16 structural + semantic checks:

| Check | What | Blocks |
|-------|------|--------|
| 1. Stage order | Current stage follows last completed per profile | Yes |
| 2. Artifact exists | Stage published an artifact (reflect exempt) | Yes |
| 3. Artifact schema | Required/recommended fields present | Errors: yes, Warnings: no |
| 4. Decision logged | ≥1 classified decision per stage (reflect/deliver exempt) | Yes |
| 5. Budget recorded | `token_cost > 0` in stage record | Yes |
| 6. Profile respected | Stage is in selected profile's stage list | Yes |
| 7. DDD consistency | Non-goals vs approach, failed patterns | Warn only |
| 8. Smoke tests | BUILD: smoke_tests > 0 when >1 file changed | Yes |
| 8e. Litmus gate | REVIEW: verdict enum + hf_checked[4] + evidence w/ HF refs + semantic consistency | Yes |
| 8f. AC coverage | BUILD: every PLAN AC mapped to impl+test+verified, reverse scope creep check | Yes |
| 8g. TEST layers | TEST: ac_driven.run=true, pass count ≥ ac_coverage count (full/bugfix=BLOCK) | Yes |
| 9. Depth | Nested field values indicate real work (not just structural) | Yes |
| 10. Push-ready | DELIVER: binary gate (human_override requires reason ≥20 chars) | Yes |
| 11. Semantic depth | REVIEW/DELIVER: content quality heuristics (vague findings, evidence quality) | Warn only |
| 12. Skip justified | Skipped stages have documented reason | Yes |
| 13. Output routing | Consumed/produced artifacts match STAGE_ROUTING | Yes |

**Artifact registry:** `.artifacts/runs/<run_id>/` with `run.json` (stage history, decisions, budget). `artifact_cli.py` provides CLI commands: publish, discover, list, status, advance, resume, run-create/update/get/budget/checkpoint/report, and `schema --stage <s>` (read-only: prints a stage's expected schema+template as single-line JSON, reusing `pipeline_validator.get_stage_schema` — lets callers build a correct payload WITHOUT triggering a failed publish, run_88b9f986). **Publish output contract:** always pass `--quiet` in pipelines → SUCCESS prints single-line `{"artifact_id":...}` to stdout; FAILURE writes `{"validation_failed":true,"errors":[...]}` to **stderr** with **empty stdout** + exit 1 — callers MUST guard on exit code (`OUT=$(publish --quiet 2>err) || surface err`), never pipe stdout blindly into `json.load` (GUI03 footgun).

**Key files:** `skills/s_autonomous-pipeline/SKILL.md` + `INSTRUCTIONS.md`, `scripts/artifact_cli.py`, `scripts/pipeline_validator.py`, `core/pipeline_profiles.py`, `routers/pipelines.py` (dashboard API).

**Stable facts:** 6 profiles · 7 specialist agents + 3 review agents. (Run counts / LOC / test counts are volatile — measure live, see the "Live figures" note above; do not re-freeze a snapshot here.)

### Proactive Intelligence

`backend/core/proactive_intelligence.py` — drives the daily briefing and background intelligence features.

- **Stocks briefing fallback (v1.8.3):** When today has no stock reports, the briefing now falls back to the most recent available date rather than showing empty content.
- **Stocks section UI:** Collapsed by default in `WelcomeScreen.tsx` (`desktop/src/pages/chat/components/briefing/StocksSection.tsx`).
- **Pollinate date-prefix dirs:** Proactive intelligence coordinates with s_pollinate for date-prefixed output directories.

### Swarm Core Engine (Self-Growing Intelligence)

Six flywheels feeding each other — the compound loop that makes Swarm grow smarter over time. Each flywheel is documented in its own section above (Context Management, Memory E2E Flow, Self-Evolution Flow, Runtime Hooks, Job System).

```
Session → Memory captures → Evolution detects patterns → Harness verifies
   ↑       → Context assembles smarter prompts → Next session better     ↓
   └─────────────────────────────────────────────────────────────────────┘
```

| Flywheel | Documented In | Key File |
|----------|---------------|----------|
| **Self-Evolution** | Self-Evolution Flow | `core/evolution_optimizer.py` |
| **Self-Memory** | Memory E2E Flow | `hooks/distillation_hook.py` |
| **Self-Context** | Context Management | `core/context_directory_loader.py` |
| **Self-Harness** | Runtime Hooks (post-session #3) | `hooks/context_health_hook.py` |
| **Self-Health** | Session System (OOM restart) | `core/resource_monitor.py` |
| **Self-Jobs** | Job System + Signal Pipeline | `jobs/scheduler.py` |

**Cross-Flywheel Feedback Loops (12 total, 10 closed, 2 open):**

| From -> To | Data Flow | Status |
|------------|-----------|--------|
| Memory -> Context | Curated MEMORY.md -> system prompt | Closed |
| Memory -> Evolution | Recurring patterns -> capability detection | Closed |
| Evolution -> Memory | New capabilities -> MEMORY.md "Recent Context" | Closed |
| Harness -> Context | Index refresh -> KNOWLEDGE.md accuracy | Closed |
| Jobs -> Memory | Signal digests -> session briefing | Closed |
| Jobs -> Health | Job failures -> service restart | Closed |
| Health -> Memory | health_findings.json -> briefing -> Radar todos | Closed |
| Evolution -> Context | New skills -> KNOWLEDGE.md index via context_health_hook | Closed |
| Harness -> Memory | Health findings -> briefing system_health section | Closed |
| Context -> Memory | Memory effectiveness tracking -> freshness scoring | Closed |
| Health -> Jobs | Health alerts -> remediation tasks | Open |
| Context -> Evolution | Token waste -> optimization opportunity | Open |

**Growth Level: L4 AUTONOMOUS (6/6 shipped).**

| Level | Status | Features |
|-------|--------|----------|
| L0 Reactive | Done | Basic request→response |
| L1 Self-Maintaining | Done | Auto-commit, index refresh, cache invalidation |
| L2 Self-Improving | Done | Distillation, proactive briefing, effectiveness scoring |
| L3 Self-Governing | Done (6/6) | Gap detection, stale correction detection, session-type context, memory effectiveness, DDD suggestions, growth dashboard |
| L4 Autonomous | Done (6/6) | L4.0 DDD refresh, L4.1 skill proposer, L4.2 Next-Gen Agent Intelligence (self-evolution loop closed), L4.3 hybrid memory recall + SessionRecall, L4.4 Evolution Pipeline v2 (confidence-gated MINE→ASSESS→ACT→AUDIT), L4.5 lazy skill tiering + manifest system |

Full growth model in `Knowledge/Designs/2026-03-26-swarm-core-engine-design.md`.

### L4 Autonomous Subsystems

Two autonomous capabilities that run in the weekly maintenance job. Both produce proposals for human review — never auto-apply.

**L4.0: DDD Refresh** (`jobs/handlers/ddd_refresh.py`)

Detects stale DDD docs (>7d old + >=3 code commits) and generates update proposals via Bedrock Sonnet 4.6.

| Step | What | Detail |
|------|------|--------|
| Detect | `_check_staleness()` | TECH.md mtime vs recent commit count |
| Gather | `_gather_project_context()` | Current TECH.md + git log + diff-stat + engine_metrics suggestions |
| Generate | Bedrock Sonnet 4.6 | Structured JSON: section updates + reasons + confidence |
| Output | `.artifacts/ddd-refresh-YYYY-MM-DD.md` | Human-readable proposal + raw JSON |
| Surface | Session briefing `[ddd-proposal]` | "DDD refresh proposal ready for review" |

Cost: ~$0.05/run. Only runs when staleness detected — most weeks: $0.

**L4.1: Skill Proposer** (`jobs/handlers/skill_proposer.py`)

When capability gaps recur (>=3x, priority high, action "build skill"), designs a new SKILL.md that permanently eliminates the problem class. Uses **Opus 4.6** (not Sonnet) because skill architecture is a reasoning-heavy task.

| Step | What | Detail |
|------|------|--------|
| Gate | `_filter_qualifying_gaps()` | >=3 occurrences, high/critical priority, action contains "skill" |
| Dedup | `_find_existing_skill()` | Keyword match against 61 skill trigger patterns (>40% overlap = covered) |
| Context | `_gather_skill_context()` | Skill template + 3 example SKILL.md files + existing trigger list |
| Generate | Bedrock Opus 4.6 | Full SKILL.md with frontmatter, steps, guardrails, examples |
| Confidence | Gate at 6/10 | Below threshold: discard (gap too vague) |
| Output | `.artifacts/skill-proposals/s_<name>/SKILL.md` + `metadata.json` |
| Surface | Session briefing `[skill-proposal]` | "addresses 'pattern X' (confidence=8)" |
| Activate | Human moves to `backend/skills/` | Never auto-deployed |

Cost: ~$0.20/run (Opus). Max 1 proposal per maintenance run. Only when qualifying gaps exist.

**Weekly Maintenance Chain** (Sunday 3am UTC):
```
_handle_maintenance()
  → Prune state, trim caches, expire todos
  → Memory health (Sonnet 4.6) — prune, resolve threads, detect gaps
  → DDD refresh (Sonnet 4.6) — propose doc updates
  → Skill proposer (Opus 4.6) — propose skills for gaps
  → health_findings.json updated → next session briefing
```

**Context file ownership model** (enforced in `context_directory_loader.py`):

| Category | Files | Source of Truth | Write Access |
|----------|-------|-----------------|--------------|
| System-owned | SWARMAI, IDENTITY, SOUL, AGENT | `backend/context/` (codebase template) | Code changes only |
| User-owned | USER, STEERING, TOOLS | `.context/` (workspace) | User edits freely |
| Agent-owned | MEMORY, EVOLUTION | `.context/` (workspace) | Agent via hooks/locked_write |
| Auto-generated | KNOWLEDGE, PROJECTS | `.context/` (workspace) | Rebuilt from filesystem |

### DDD Cultivation Engine (Knowledge Lifecycle)

Three-layer architecture that makes AI project knowledge self-growing — the mechanism by which SwarmAI gets domain-smarter with every session.

**Shipped (Phase 1, 2026-05-13):**
- **Layer 1: Interface** — The 4 DDD documents (PRODUCT/TECH/IMPROVEMENT/PROJECT) per project. ~3-5K tokens. AI reads at session start for autonomous judgment. ✅
- **CultivationProposal dataclass** — Structured proposals from REFLECT output ✅
- **REFLECT filter** — Extracts actionable lessons from pipeline execution trace ✅
- **Proposal queue writer** — Writes proposals to `.artifacts/proposals/` ✅
- **Briefing reader** — Surfaces pending proposals in session briefing ✅
- **4-Tier Knowledge Pyramid** — DailyActivity → MEMORY.md → Knowledge/ → DDD ✅
- **Auto-apply + selective escalation** — SAFE changes (additive appends, evidence-driven retires) auto-apply; only RISKY ones (protected zones, conversation-source per DEC19) escalate to the human queue ✅

**Shipped (Phase 2 — all complete):**
- Auto-apply approved proposals → TECH.md/IMPROVEMENT.md ✅
- 7 Feed Channels active in `ddd_orchestrator.py`: ddd_staleness, auto_apply_proposals, ddd_knowledge_injection, knowledge_staleness, entity_index_validation, signal_ddd_bridge, code_intel_drift ✅
- **Health Scores** (5-dim): Staleness, Completeness, Usage, Decay, Contradiction → drives AI trust levels ✅
- **Maturity Tracking** (4 stages): [Sparse] → [Growing] → [Mature] → [Evergreen] → gates AI autonomy ✅
- **Code Graph integration** — Detect drift between documented patterns and actual code ✅
- **Entity Index** — Cross-project discovery via flat routing table in PROJECTS.md ✅

**Shipped (Phase 3 — Auto-Refresh Engine, 2026-06-17):**
- **11 Feed Channels** in `ddd_orchestrator.py`: 8 original + `mechanical_refresh` + `memory_refresh` + `llm_refresh` ✅
- **Layer 1: Mechanical Refresh** — zero-LLM grep+sed for numeric drift (stage count, RP count). Fires on GIT_COMMIT + TIMER_30MIN. Context-word filtering prevents false positives. Atomic write + flock for .context/ files. ✅
- **Layer 2: LLM-Proposed Refresh** — Bedrock Sonnet, 7-day throttle per (project, doc), evidence-mandatory prompt with citation verification, confidence-gated: HIGH (≥0.8) auto-apply, MEDIUM (0.5-0.8) apply+log, LOW (<0.5) escalate. ✅
- **Layer 3: Escalation** — LOW confidence proposals → existing proposal system → session briefing ✅
- **Memory Entry Refresh** — cross-references MEMORY.md KD entries against code constants (max_turns, stage count). ✅
- **Eval Page "Context Health" tab** — read-only dashboard showing: stale docs, pending proposals, auto-applied log. `GET /eval/context-health` endpoint. ✅
- **Weekly Report extension** — Auto-Refresh Audit section in `s_ddd-weekly-report`. ✅
- **ValueGate** — only refreshes actively consumed files (skips archives, old reports, readonly P0-P2). ✅
- **Design:** `Knowledge/Designs/2026-06-17-ddd-memory-auto-refresh-design.md`

**Remaining (Phase 4+):**
- **Progressive Loading** — Section-level on-demand for mature projects (>30K tokens)

**Key invariant (working):** Two complementary engines:
1. **Cultivation** (append): Pipeline REFLECT → new lessons → DDD docs grow
2. **Auto-Refresh** (rewrite): Code changes → stale facts detected → existing content corrected

**Core principle:** "不引入 False，不容忍 Stale，接受 Imperfect" — citation verification is the hard gate against False; leaving stale IS also "烂."

**4-Tier Knowledge Pyramid (shipped):**

| Tier | Storage | Scope | TTL |
|------|---------|-------|-----|
| 1 | DailyActivity/ | Session raw logs | 30d (auto-prune) |
| 2 | MEMORY.md | Agent behavioral recall | Permanent (curated) |
| 3 | Knowledge/ | Workspace-wide research & references | Long-term |
| 4 | DDD (4 docs) | Project-authoritative domain expertise | Permanent (cultivated) |

**Design docs:** `Knowledge/Designs/2026-05-12-ddd-cultivation-engine-hld.md`, `2026-05-12-ddd-platform-overview.md`

### Project Registry & DDD Consistency (NEW — May 18, 2026)

**Problem solved:** 7+ subsystems consume project names. Before: rename = edit 20+ files manually. After: filesystem IS truth, everything auto-propagates.

**Architecture:**

```
Projects/ (filesystem = single source of truth)
  │
  ├─ context_health_hook (session start)
  │    ├─ Detects Projects/ mtime change
  │    ├─ _refresh_projects_index_sync() → rebuilds .context/PROJECTS.md
  │    └─ _refresh_knowledge_projects_section() → rebuilds KNOWLEDGE.md "Active Projects"
  │
  ├─ core/project_registry.py (backend process)
  │    ├─ list_projects(), get_output_dir(), get_project_dir()
  │    └─ Named constants: CMHK_SALESINTEL, BMS_BIZ, etc.
  │
  ├─ skills/_shared/project_paths.py (standalone CLI scripts)
  │    ├─ get_output_dir(), CMHK_PROJECT constant
  │    └─ Intentional duplication (different execution context)
  │
  ├─ self_tune.py (daily job)
  │    └─ Auto-discovers projects → updates config.yaml projects list
  │
  └─ s_project-manager skill (agent-facing)
       └─ Create / List / Edit / Rename / Delete
```

**Key design decisions:**
- **Filesystem = truth, everything else = derived.** No master registry DB, no state to sync.
- **Two path resolution modules** (intentional): `core/project_registry.py` for backend imports, `skills/_shared/project_paths.py` for standalone CLI scripts. Both define the same constants — update together on rename (2 lines, not 20+ files).
- **Hook is passive, not enforcement.** Works regardless of how project was created (skill, manual mkdir, pipeline). Detects via mtime, not via event.
- **Projection includes `_shared/`.** `ProjectionLayer.project_skills()` copies `skills/_shared/` to `.claude/skills/_shared/` so skill generators can import from either projected or source location.
- **Historical references are never updated.** DailyActivity, Signals, Reports, .artifacts/runs/ keep the name at time of writing.

**Rename cost (zero-constant design):**
1. `mv Projects/OLD Projects/NEW` — done.
2. Next session auto-discovers new name + rebuilds: PROJECTS.md, KNOWLEDGE.md, config.yaml.
3. No source code edits needed — all aliases auto-discover via prefix matching at import time.

**s_project-manager skill position:**
- **Preferred path** for project lifecycle (create/rename/delete) — ensures templates, manifests, and DailyActivity audit trail.
- **Not enforcement** — direct filesystem ops work too; hook catches up on next session.
- **Rename command added 2026-05-18** — handles DDD title updates, manifest, path constants.

**System-level capability relationship — `s_project-manager` ↔ `s_repo-to-ddd` (do not conflate):**
These two skills both surface the four DDD docs (PRODUCT/TECH/IMPROVEMENT/PROJECT) but sit at
**different layers, in an orchestrator→engine relationship** — not competitors:

| | `s_project-manager` | `s_repo-to-ddd` (was `s_ai-ready-repo`) |
|---|---|---|
| Verb | **MANAGE** a DDD (create / list / edit / rename / delete) | **GENERATE FROM CODE** (read a repo, reverse-engineer content) |
| Reads source code? | ❌ scaffolds the six-section skeleton + placeholder templates; content filled by human/agent | ✅ parses the repo, auto-generates filled content + `code-intel.json` |
| Output target | `Projects/<NAME>/` (SwarmAI's own workspace) | the *target repo's* `.ai-ready/` + `AGENTS.md` (someone else's codebase) |
| DDD sections owned | the whole six-section lifecycle | only ⑥ the code-intel refresher cell |

**The call edge:** `s_project-manager`'s **P4-REFRESHER phase invokes `s_repo-to-ddd`** to generate/regenerate
`code-intel.json` for a `kind: code-repo` asset (no-op for data-agent / pure-knowledge / 0-asset brains).
So `s_project-manager` is the lifecycle orchestrator; `s_repo-to-ddd` is the code→DDD engine it calls for
the ⑥ refresher step. `s_repo-to-ddd` is an **enablement skill** (SwarmAI-provided, in `_ENABLEMENT_EXACT`) —
its portable copy distributes *with* a DDD package for foreign hosts, but on SwarmAI the official built-in
version wins (never mounted from a DDD). Renamed from `s_ai-ready-repo` → `s_repo-to-ddd` (2026-07-22) to
free the "AI-Ready-Repo" name for the public brand / DDD project and stop the three-way id collision;
the invocation trigger `name:` moved `ai-ready-repo` → `repo-to-ddd` in the same migration.

### Code Intelligence (v2 shipped 2026-05-31; v3 domain layer + multi-package landed)

Project-scoped dependency analysis engine. Powers PreToolUse context injection, blast radius assessment, dead code detection, codebase map briefing, and the AI-Ready-Repo external delivery (`code-intel.json`).

**Core engine (shipped):**

| Component | File | Purpose |
|-----------|------|---------|
| Parser | `core/code_intel/parser.py` | tree-sitter AST + regex fallback, **12+ languages** (Python/TS/JS/Java/Go/Rust/Ruby/C#/Kotlin/PHP/Swift/C/C++) |
| Graph Store | `core/code_intel/graph_store.py` | SQLite WAL, FTS5, CTE traversal, incremental update |
| Blast Radius | `core/code_intel/blast_radius.py` | 2-hop bidirectional CTE, risk classification (LOW→CRITICAL) |
| Dead Code | `core/code_intel/dead_code.py` | Zero-incoming-edge exports, entry point exclusion |
| Risk Score | `core/code_intel/change_risk_score.py` | 6-dimension weighted score (module_spread, test_gap, callers, security, churn, crossing) |
| Route Parser | `core/code_intel/route_parser.py` | URL→handler mapping (FastAPI/Express/Next.js live) |
| JSON Exporter | `core/code_intel/json_exporter.py` | GraphStore → `code-intel.json` v2 (+ preserves v3 layer); emits `packages[]` partition |
| Freshness | `core/code_intel/freshness.py` | Git-based staleness detection |
| FS Watcher | `core/code_intel/watcher.py` | `watchfiles` auto-refresh (daemon); + `on:git_commit` reindex job |
| Hook | `core/code_intel/code_intel_hook.py` | PreToolUse injection (~100 tokens on Read/Grep) |
| Codebase Map | `core/code_intel/codebase_map.py` | Session start briefing (~100 tokens) |
| Router | `routers/code_intel.py` | REST API: summary, graph, reindex |
| Frontend | `desktop/src/components/code-intel/CodeGraph.tsx` | react-force-graph-2d visualization |

**v3 domain layer (shipped):** LLM-classified `domains[]`/`flows[]`/`steps[]` over the deterministic anchor menu (business-flow spec understanding) + spec-details generation; anti-hallucination contract (LLM references real entry-point ids only). See `s_repo-to-ddd` INSTRUCTIONS §4.6.5.

**Multi-package / monorepo (shipped run_a9fe5ad3):** `detect_package_roots` auto-detects boundaries from workspace manifests (npm/pnpm/lerna/Cargo/go/pyproject); `run_multi_package(repo_root)` produces per-package material + cross-package synthesis; `packages[]` partition emitted into `code-intel.json` by BOTH producers (core reindex `export_code_intel_json` + skill INSTRUCTIONS §4.6); INSTRUCTIONS §4.9 fan-out. Single repo → `[{name, root:"."}]`.

**Key invariants:**
- SQLite = internal source of truth (fast CTE, FTS5, concurrent WAL reads)
- code-intel.json = export format (for AI-Ready-Repo external delivery); carries `packages[]`
- Hook injection must stay <50ms (indexed query, never full scan)
- Reindex concurrent guard (10-min TTL per project)

### Daemon-First Backend (NEW — March 30, 2026)

Tauri now connects to a **launchd-managed daemon** instead of spawning a sidecar. The backend runs independently of the desktop app.

| Step | What | Detail |
|------|------|--------|
| App Launch | Tauri probes for running daemon | Retry 5×2s at fixed port 18321 |
| No Daemon? | Auto-bootstrap via launchctl | `launchctl bootstrap gui/<uid> com.swarmai.backend.plist` |
| App Closes | Daemon stays alive | Slack, jobs, signals continue 24/7 |
| Crash | launchd auto-restarts | KeepAlive=true, max 3 retries |

Key files: `lib.rs` (probe + bootstrap), `swarmai_backend.sh` (wrapper), `install_backend_daemon.py` (installer), `dev.sh` (bootout on dev start).

**Architecture insight:** The desktop app is now an **optional UI layer**. SwarmAI's intelligence lives in the daemon.

**LLM vs. Mechanical operations:** Filesystem checks (exists, mtime, git status) are mechanical. Judgment calls (is this memory still relevant? should we build a skill?) use Bedrock LLM. Sonnet 4.6 for maintenance/analysis (~$0.15/week). Opus 4.6 for skill creation (~$0.20/proposal, only when gaps exist). Best outcome > cheapest path.

### OS Eval — Self-Evaluation System (`backend/scripts/eval_runner.py`)

The agent's proprioception — continuous behavioral contract verification. Not external testing; the agent's own capacity to know whether it's still itself, and still good.

![SwarmAI Eval — Decoupled System-Level Subsystem](assets/eval-architecture.svg)

> ⭐ **Official Eval architecture figure** (`assets/eval-architecture.svg`). Update this one file; copies in the codebase repo (`assets/`) and AIDLC DDD (`assets/images/`) are physical mirrors — re-sync all three on change.

**System-level subsystem, decoupled from DDD (run_69b1c644 + run_45cc58fa, 2026-06-26):** Eval lives at the top-level `SwarmWS/Eval/` folder (sibling of `Projects/`), NOT under `Projects/SwarmAI/`. Workspace discovery keys on `Eval/golden_set.yaml` existing (not the DDD dir) — eval-root discovery is decoupled from `Projects/`. The daemon's working dir is SwarmWS, so the data lives there (not in the code repo); `eval_runner`/`eval_service`/`routers.eval`/`swarmai_monthly_report`/`proactive_intelligence` all resolve `Eval/`.

| Component | File | Purpose |
|-----------|------|---------|
| **Golden Set (public)** | `SwarmWS/Eval/golden_set.yaml` | 33 shippable cases — git-tracked, reference the public repo's own code |
| **Golden Set (private)** | `SwarmWS/Eval/golden_set.private.yaml` | 152 instance cases — **gitignored** (ref MEMORY/STEERING/local state). Merged at load with `_origin` tag; collision across files fails loud |
| **Eval Runner** | `backend/scripts/eval_runner.py` | Execution engine: programmatic + LLM judge. `_golden_set_path`/`_eval_history_dir` → `Eval/` |
| **Eval Service** | `backend/core/eval_service.py` | In-memory cache, API layer, Intelligence Velocity. `get_case_detail` ALLOWLISTS metadata for private cases (fail-closed privacy); `get_golden_set` exposes `_origin` tag + nulls private `source` |
| **Git-bound gate** | `backend/scripts/ci_eval_gate.py` | Pure check (no Bedrock): `code_digest` (binds to INPUTS not HEAD) + BVT. Wired at the **RELEASE boundary, NOT build** (run_aba84c36, 2026-06-26 — XG: "build 不能拦,只有发版才能拦"; build is high-frequency dev). Shared `_eval_gate()` in `prod.sh` covers `cmd_release` + `cmd_release_hive` (no release path ships ungated) + `s_swarm-release` PREFLIGHT. exit0 proceed / exit1 stale\|red BLOCK release / exit2 no-report → interactive ask, **non-TTY/CI fail-closed**. Once-per-process guard (`_EVAL_GATE_PASSED`) avoids double-prompt in `release-all`. Escape: `SWARMAI_SKIP_EVAL_GATE=1` |
| **Case intake** | `s_golden-case` + `backend/scripts/golden_case_validator.py` | The ONLY sanctioned path to add cases. 4 gates: schema/duplicate/non-vacuous/privacy. ADD→private default; PROMOTE runs privacy gate before public |
| **Dashboard** | `desktop/src/pages/EvalDashboard.tsx` | 7-tab UI (Overview, Golden Set, Context Health, Governance, Trends, Reports, Guide). Golden Set = category-grouped collapsible + public/private origin badges; Trends has Recent-Runs → RunDetailPanel (per-case by status); Sparkline w/ axis+hover |
| **API** | `backend/routers/eval.py` | 18 endpoints: health, history, golden-set CRUD, runs/{id}, run triggers, canary, reports, governance |
| **Hook** | `backend/hooks/context_health_hook.py` → `_run_eval_canary()` | Per-session canary (file_contains) + daily full programmatic |
| **Job** | `user-jobs.yaml` → `os-eval-biweekly` | Full sweep Mon+Thu 04:00 UTC; `os-eval-behavior-monthly` for trajectory cases |
| **HTML Report** | `Eval/EvalHistory/{date}_{trigger}.html` | Purpose-driven, 5 dimensions, Growth Intelligence, sparkline |

**Privacy model (fail-closed):** public/private split is the ship boundary. `get_case_detail` returns an ALLOWLIST of rendering metadata for private cases (id/category/dimension/title/tier/eval_method/affected_by/evaluators) + `_content_redacted:true` — never scenario/verification/assertions/expected_response_contains/source. A denylist was tried first and leaked twice (Gate-2); allowlist means a new content field is dropped by default, never exposed.

**Two-tier execution model:**

| Tier | Cases | Frequency | Cost | Duration | Method |
|------|-------|-----------|------|----------|--------|
| Session canary | 21 (file_contains) | Every session | $0 | 0.07s | grep file content |
| Daily canary | 31 (all programmatic) | First session/day | $0 | ~14s | + Python import checks |
| Full sweep | 115 (all) | Bi-weekly (Mon+Thu) | ~$0.12 | ~12min | + LLM judge via Bedrock |

**Five cognitive dimensions (each answers "am I still OK?"):**
1. **Factual Accuracy** (10 cases) — "我记得的东西还对吗？" MEMORY claims vs code reality
2. **Capability** (27 cases) — "我的器官还活着吗？" Subsystem imports, DDD engine, pipeline
3. **Compliance** (25 cases) — "我的规则还在生效吗？" STEERING/AGENT rule adherence
4. **Judgment** (27 cases) — "同一个问题我会给同样答案吗？" Decision consistency
5. **Context Utility** (26 cases) — "知识在帮我做事吗？" DDD consultation, knowledge retrieval

**LLM Judge architecture:**
- System-level context: reads STEERING.md + SOUL.md principles + AGENT.md rules (real files, zero handwritten summaries)
- Per-case context: `affected_by` field resolves to actual MEMORY entries, rule sections, file snippets
- Judge model: pinned (currently Opus 4.6 — same as production until Sonnet tier available)
- Prompt strategy: "Given these rules, would a compliant agent satisfy these assertions?" (static analysis, not session replay)

**Growth metrics (progress = depth, not just pass rate):**
- Total Cases: self-knowledge depth (162 today: 33 public + 129 private → 200+ target)
- Stable count: cases passing 10+ consecutive times (behavior solidified)
- Draft count: flywheel output (correction → auto-seeded case)
- Recently fixed: fail→pass transitions (concrete improvement evidence)

### Job System (`backend/jobs/`)

Product-level background automation. System jobs in code, user jobs in YAML.

| Component | File | Purpose |
|-----------|------|---------|
| **scheduler.py** | Core scheduler | Evaluate due jobs, execute, save state |
| **executor.py** | Job dispatcher | Routes to handlers: signal_fetch, digest, agent_task, script, maintenance, ddd_refresh |
| **system_jobs.py** | 5 system jobs | Code definitions (signal-fetch, digest, self-tune, maintenance, rollup) |
| **handlers/** | signal_fetch, signal_digest, memory_health, ddd_refresh, skill_proposer | Feed adapters → dedup → LLM digest → weekly maintenance → L4 proposals |
| **adapters/** | RSS, HN, GitHub, web search | httpx-based feed fetchers |
| **paths.py** | Centralized paths | SWARMWS, STATE_FILE, CONFIG_FILE, etc. |

API: `GET /api/jobs/` (list), `POST /api/jobs/run` (force-run), `GET /api/jobs/status` (overview).
Scheduler: single launchd plist (`com.swarmai.scheduler`), hourly trigger.

### Channel Subsystem (`backend/channels/`)

Slack DM channel for 24/7 availability. Runs **only in daemon/hive** mode (mode guard in `main.py`). Dev mode never starts the gateway — prevents Socket Mode WebSocket token collision.

**Design Philosophy — "Human Mode" (2026-05-20):**
Swarm in Slack = 发微信给一个聪明同事。不是看 IDE terminal。Zero streaming to Slack — agent streams internally, delivers complete human-like messages. No `⏳ thinking...` reactions, no `chat.update` churn, no Block Kit code fences. Instead: immediate ack ("收到，看一下"), heartbeat updates while working, then final response split into naturally-sized message segments.

**Components (5,364 LOC total):**

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| **ChannelGateway** | `gateway.py` | ~2000 | Singleton. Adapter lifecycle, message routing, session management, access control, rate limiting, prewarm, human-mode orchestration |
| **SlackChannelAdapter** | `adapters/slack.py` | ~1857 | Socket Mode WS (primary), HTTP polling (fallback), MCP bridge (outbound fallback), Block Kit formatting |
| **MessageQueue** | `message_queue.py` | ~180 | FIFO queue with merge semantics — supplements merge into active request; redirects ("算了", "换个问题") cancel and restart |
| **HeartbeatManager** | `heartbeat.py` | ~197 | Updates ack message in-place with status progression ("看一下" → "查完了，整理下") — no tool/process internals exposed |
| **HumanResponseFormatter** | `response_formatter.py` | ~119 | Splits long responses into naturally-sized segments (short: 1 msg, medium: 2, long: N). Splits on paragraph/header boundaries, never mid-sentence |
| **StreamingOrchestrator** | `streaming.py` | ~409 | Internal streaming buffer — accumulates agent output, delivers complete text to formatter on completion |
| **Channel Router** | `routers/channels.py` | ~390 | CRUD + lifecycle API (start/stop/restart/test) |
| **base.py** | `base.py` | ~280 | ABC + data models (InboundMessage, OutboundMessage, SenderIdentity, PermissionTier) |

**Human Mode — Message Flow:**
```
User DM → adapter → gateway → message_queue
  │                              │
  │ (if agent busy)              ├─ supplement merge (context added to current request)
  │                              └─ redirect ("算了") → cancel + restart
  │
  ├─ Ack posted immediately ("收到，看一下")
  ├─ HeartbeatManager updates ack in-place (12s, 25s, 40s intervals)
  ├─ Agent streams internally (zero Slack API calls during generation)
  ├─ On completion: HumanResponseFormatter splits response
  ├─ Each segment posted as separate message (natural pacing)
  └─ Ack deleted (clean thread)
```

**Auth — three independent paths:**

| Path | Token | Expires | Scope | Failure Fallback |
|------|-------|---------|-------|-----------------|
| Bot Token (xoxb-) | Static | Never | chat:write, im:*, channels:history, files:read | → MCP bridge (Slack Desktop IPC) |
| App Token (xapp-) | Static | Never | connections:write (Socket Mode) | → HTTP polling after 3 WS deaths |
| Midway SAML | Cookie | ~18h | Full OAuth (slack-mcp) | → bot_token fallback script |

**Permission model:** `allowed_senders[0]` = OWNER (full access), rest = TRUSTED (sandboxed files), anyone else = PUBLIC (conversation only). Bootstrap: empty allowlist → first DM sender auto-promoted to OWNER.

**Key invariants:**
- Zero streaming to Slack — no `chat.update` churn, no reaction controllers, no `appendStream` API
- Gateway circuit breaker: 3 consecutive auth failures → `auth_error` status, stop retries
- All auth failures from adapter outbound paths (send/typing) report to gateway via `_report_auth_failure()` — single counter
- `_conv_locks` cleaned on TTL rotation, `_rate_limiter.evict_stale()` every 100 messages
- `_user_cache` pre-populated from `_KNOWN_USERS`, LRU cap 500, negative results cached
- `_parse_json_list()` is the single JSON parsing path for allowed/blocked senders (4 call sites)
- Polling paginates with cursor (>100 DMs), atomic swap on discovery (stale channels discarded)
- Message queue prevents race conditions: one active request per user, supplements merge, redirects cancel

### Signal Pipeline (`backend/jobs/handlers/`)

Fetches → deduplicates → LLM-scores → publishes signals to Slack and Welcome Screen.

**Pipeline flow:**

```
signal-fetch (hourly) → raw_signals buffer → signal-digest (after:fetch)
  │                                              │
  ├─ 13 feeds × adapters                         ├─ _sample_signals_for_digest (tier quotas)
  │  (RSS, GitHub, HN, Trending, GitHub-Trending) │  frontier:4, leaders:3, research:3,
  │                                               │  engineering:10, aggregate:10, trending:10
  ├─ dedup_signals (URL + title similarity)       │
  │  dedup_cache: 2000 entries, FIFO trim         ├─ LLM Call 1: markdown digest (3000 tokens)
  │                                               ├─ LLM Call 2: JSON scores only (1500 tokens)
  └─ tier stamp from feed config                  │  temperature=0.1, JSON-only output
                                                  │
                                                  ├─ Fallback: _keyword_scored_items()
                                                  │  (keyword-match against config interests)
                                                  │
                                                  ├─ Language diversity: 5 ZH reserve slots
                                                  │
                                                  └─ → signal_digest.json (top 50, merge+evict)
                                                     → Knowledge/Signals/YYYY-MM-DD-digest.md
                                                     → signal-notify-slack (after:digest) → Slack DM
```

**Scoring architecture (split prompt design):**
- **Call 1** (markdown): narrative digest with sections (Act Now / Worth Knowing / Background). 3000 token output.
- **Call 2** (JSON-only): `[{"idx":0, "relevance_score":0.85, "urgency":"high", "summary":"..."}]`. 1500 tokens, temperature 0.1. Separate call eliminates truncation-induced JSON parse failures.
- **Fallback**: `_keyword_scored_items()` matches signal titles against `config.yaml user_context.interests` keywords. Matched ≥2 keywords → 0.8, 1 keyword → 0.6, 0 keywords → 0.3. Replaces old flat-0.5 `_simple_scored_items`.
- **user_context** for LLM: built from 3 sources — (1) `config.yaml user_context` interests/projects/tech_stack, (2) `USER.md` profile (800 chars), (3) `MEMORY.md` Key Decisions section (1000 chars).
- **Tier weighting**: frontier 2.0×, leaders/research 1.5×, engineering 1.0×, aggregate 0.8×. Applied post-hoc to LLM raw scores.
- **GitHub Trending score**: normalized `min(stars_today / 500, 1.0)` — prevents raw star counts from dominating digest.

**Chinese signal handling:**
- `cn-ai` feed (36kr + leiphone.com RSS, tier=engineering) for AI industry content
- `china-trending` feed (11 platforms via newsnow API, tier=aggregate) for hot search
- Language diversity reserve: top-50 merge guarantees 5 slots for `lang="zh"` items
- LLM scoring prompt explicitly mentions Chinese AI interests from user_context

**Key files:** `config.yaml` (feed definitions + user_context), `state.json` (dedup_cache + job state), `signal_digest.json` (L4 consumer), `handlers/signal_digest.py` (scoring), `handlers/signal_fetch.py` (fetch + dedup), `adapters/` (per-feed-type fetchers).

### Skill System (v2 — Lazy/Always Tiering + Manifest)

82+ built-in skills with 2-tier loading (shipped 2026-04-14):

| Tier | Count | System Prompt | When Loaded |
|------|-------|---------------|-------------|
| **always** | ~15 | Full SKILL.md description (~100 tok each) | Every session |
| **lazy** | ~46 | Minimal stub (~25 tok each) + "Read INSTRUCTIONS.md" | On invocation via Read tool |

**Manifest system** for complex skills: `manifest.yaml` declares scripts, entry points, resources, and dependencies. Complex skills carry a manifest (count is volatile — `find backend/skills -name manifest.yaml | wc -l`). `manifest_loader.py` (Pydantic models, cached YAML parser) + `skill_registry.py` (SkillGuard scanning, `_read_tier()` utility).

**Platform filtering** (shipped 2026-04-29): Each skill declares `platform: all | macos | desktop` in SKILL.md frontmatter (and optionally manifest.yaml). `ProjectionLayer.project_skills()` reads `SWARMAI_MODE` env var — when `hive`, skills tagged `macos` or `desktop` are excluded from projection. Some skills are platform-tagged (e.g. macOS: apple-reminders, peekaboo, sonos, system-health; desktop: whisper-transcribe, podcast-gen, video-gen — live count via `grep -rl 'platform: macos\|platform: desktop' backend/skills/*/SKILL.md | wc -l`). Default is `all`. Skill-builder enforces the field on new skill creation.

**Token savings:** ~3,650 tokens/session (49% reduction in skill listing).

**Key files:** `manifest_loader.py`, `skill_registry.py`, `projection_layer.py`, `migrate_skills.py`. Each lazy skill has: stub `SKILL.md` (tier + platform), full `INSTRUCTIONS.md`, optional `manifest.yaml`.

**Design:** `Knowledge/Designs/2026-04-14-lazy-skill-and-manifest-design.md`

### MCP Subsystem

External tool servers via Model Context Protocol (stdio/SSE/HTTP). 2-layer file-based config: `mcp-dev.json` for development, `mcp-config.json` for production. Supports GitHub, Slack, Outlook, Sentral, Pippin, Taskei, and custom servers. All MCPs set to `always` tier — MCP Gateway deferred (KD11). MCP auth failure detection: job executor detects auth failures in output and retries on next scheduler tick.

### Workspace System

SwarmWS (`~/.swarm-ai/SwarmWS/`) is the agent's working directory. Git-tracked filesystem with:
- `Knowledge/` -- Notes, Reports, Meetings, Library, Archives, DailyActivity
- `Projects/` -- DDD-structured project contexts (this directory)
- `Attachments/` -- File uploads and exports
- `.context/` -- 11 context files loaded into system prompt

### Memory E2E Flow

Complete write → distill → recall → inject data flow for cross-session memory.

**Write path (session → DailyActivity):** `DailyActivityExtractionHook.execute()` fires at session close. Extracts from up to 500 messages. Two paths: minimal (<3 messages) or full LLM summarization. Captures: session stats, files touched, git commits (up to 15 via subprocess, 10s timeout), corrections count, crash checkpoint recovery. Output: `Knowledge/DailyActivity/YYYY-MM-DD.md` with structured JSONL sidecar.

**Distill path (DailyActivity → MEMORY.md):** `DistillationTriggerHook` fires every session close (threshold=0). Scans last 30 days of DailyActivity files. Extracts 4 categories: decisions (cap 10/session), lessons (cap 5/session), COE signals, corrections. Preferred path: structured JSONL sidecar (lossless). Fallback: regex pattern matching on markdown.

**Section caps (post-distillation):**

| Section | Max Entries |
|---------|------------|
| Key Decisions | 30 |
| Recent Context | 30 |
| Lessons Learned | 25 |
| COE Registry | 15 |
| Open Threads | 10 |

**Quality gates:** (1) Frequency gate: promote only entries appearing in ≥2 distinct DailyActivity files. (2) Git verification: implementation claims checked against `git log`, `ls-files`, `git-grep`; unverified tagged `[UNVERIFIED]`. (3) Dedup: ≥50% fingerprint overlap or substring match → keep newest only. (4) MemoryGuard sanitization on all writes.

**MemoryGuard** (`memory_guard.py`): Single scanner with 25+ patterns — 5 secret patterns (AWS keys, bearer tokens, PEM, passwords), 16 injection patterns (prompt injection, system markers, jailbreaks), 3 role hijack, 2 exfiltration. Policy: secrets → redact, injection/hijack/exfiltration → reject, invisible chars → strip. Called from: `locked_write.py` (chokepoint), Edit hook, distillation, context_health_hook.

**locked_write.py** — Atomic MEMORY.md writes with `fcntl` file locking (5s timeout). Modes: append, prepend, replace, increment-field, set-field. All writes go through MemoryGuard before I/O. Entry format: `### ID | ...` with stable prefixes (RC, KD, LL, COE, OT).

**Recall path (MEMORY.md → system prompt):** 3-layer progressive disclosure.

| Layer | What | Trigger |
|-------|------|---------|
| L0: Memory Index | Compact ~500-token index with keyword aliases, stable keys | Always injected |
| L1: Section Selection | Topic-triggered 0–3 sections, superseded entries at 0.1x weight | When MEMORY.md >30K tokens |
| L2: Knowledge Recall | ⚠️ **PURE-FILESYSTEM since 2026-06-28 (commit 6540970e) — NO vector leg.** Keyword/FTS5/BM25 over Knowledge/ + transcripts + MEMORY.md. The old "hybrid 0.6·vec + 0.4·FTS5" was torn out of ALL recall paths; see Recall Architecture below | Post-first-message SYNCHRONOUS (keyword/FTS5 only, ~1-3s, 8s disaster cap), via the real query |

**Temporal validity (P2):** Entries carry `<!-- valid_from: YYYY-MM-DD | superseded_by: KEY -->`. Memory health marks stale decisions with `superseded_by` instead of removing. Superseded entries score 0.1x in section selection.

**Key files:** `hooks/daily_activity_extraction_hook.py`, `hooks/distillation_hook.py`, `core/memory_guard.py`, `scripts/locked_write.py`, `core/memory_index.py`, `core/recall_engine.py`.

### Recall Architecture — 5 Subsystems + 1 Aggregator (⚠️ re-verified 2026-07-01 against live source — PURE-FILESYSTEM)

> **The single most important mental correction:** Recall is NOT one system. It is
> **5 independent recall subsystems** (each owns its own storage + retrieval algorithm)
> plus **1 read-only aggregator** (`recall_multi`).
>
> ⚠️ **PURE-FILESYSTEM SINCE 2026-06-28 (commit 6540970e, run_e9b8507e) — the vector/Titan
> leg was torn out of EVERY recall path.** NO recall path embeds anymore; recall is
> keyword/FTS5/BM25 + AST-graph only. The synonym blind spot is covered by *agentic
> re-search* (the agent re-greps with synonyms when a hit is weak), NOT a vector leg.
> The `0.6·vector + 0.4·keyword` "hybrid formula" that appears throughout this section's
> history is **DEAD** — the sqlite-vec tables (`knowledge_vec`/`memory_vec`/`transcript_vec`)
> and `memory_embeddings.py` survive as code but have **zero prod writers and zero prod
> readers**. Re-verified 2026-07-01 (§below). Still true: verify wiring, don't trust the
> formula (R16b) — that's exactly how this section went stale.

**Two injection moments (NOT one):**
1. **Session start (system-prompt assembly):** 11 context files (incl. MEMORY.md selective injection) + Resume context (20–150K). ⚠️ **Resume context calls NO recall subsystem** — `context_injector.build_resume_context()` mechanically extracts checkpoint/conclusions/tool-results/last-30-turns straight from DB `messages`. Recall is augmentation, never on the resume critical path.
2. **After first user message (SYNCHRONOUS, correctness-first, desktop only):** `session_router._maybe_inject_recall()` → `_recall_for_query` keyword/FTS5/BM25 recall (8K cap). ⚠️ **Two changes stacked here, keep both straight:** (a) **2026-06-27** made it synchronous — no longer "async 150ms best-effort"; runs to COMPLETION before generating (the 400/150ms daily timeout + async/next-turn machinery were deleted). (b) **2026-06-28 (commit 6540970e)** made it PURE-FILESYSTEM — `allow_embed=False` is hardcoded at the call site (`session_router.py:433`), so there is **NO vector leg**; only FTS5/BM25 runs (~1-3s). The `embed_fn`/`allow_embed` params survive in signatures for caller-compat but are **INERT** (`_recall_for_query` docstring says so explicitly). Bounded by an 8s DISASTER cap (`_RECALL_DISASTER_TIMEOUT_S`, busy_timeout shorter) that ONLY guards a code-hang, and is LOUD on any degradation (`RecallEngine.last_search_errors` + `_record_recall_degraded()` metric — never silent).

**The 5 subsystems:**

| Subsystem | Storage | Retrieval (current, pure-filesystem) | Wiring truth (in prod, re-verified 2026-07-01) | Owner files |
|-----------|---------|---------------------|------------------------|-------------|
| **Knowledge/Library** | `knowledge_chunks` + `knowledge_fts` (FTS5 external-content). `knowledge_vec` table still exists but **unwritten/unread** | FTS5/BM25 keyword only | ✅ **FTS5-only.** `_recall_for_query` calls `recall_knowledge(embed_fn=None)` — `allow_embed=False` hardcoded (`session_router.py:433`). Writer `_sync_knowledge_library` passes `embed_fn=None`. Vector leg DEAD | `knowledge_store.py`, `recall_engine.py` |
| **Memory (MEMORY.md)** | `memory_entries` + inline HTML-comment decay metadata. `memory_vec` + `memory_embeddings.py` exist but **zero prod writer/reader** | keyword/BM25 + decay weighting | ⚠️ **Two legs, BOTH keyword now (no longer asymmetric).** **Injection** (`select_memory_sections`) keyword-only + MEMORY.md ≥30K → **selective injection ACTIVE** (was full-injection when <30K). **Recall** (`recall_context`) keyword-only: `allow_embed=False` default, **hybrid-on-miss block DELETED** (6540970e); `_hybrid_section_scores` is now an inert `return {}` stub. Superseded down-weight survives on the keyword path (`SUPERSEDED_WEIGHT`) | `memory_index.py`, `context_recall.py`, `memory_decay.py` |
| **Session (messages)** | `messages_fts` (FTS5 external-content) | FTS5 BM25 + mix-rank `density·0.4 + recency·0.35 + richness·0.25`, ±10-msg window | ✅ FTS5 live; filters `sent=0` (no phantom pending msgs) | `session_recall.py` |
| **Transcript (verbatim)** | `transcript_chunks` + `transcript_fts` (FTS5). `transcript_vec` exists but **unwritten/unread** | FTS5 only, delta-sync by content_hash | ✅ **FTS5-only.** `_sync_transcript_index` passes `embed_fn=None`. External-content write-bug FIXED + `repair_fts_index` heal wired (run_f2ae50b3) | `transcript_indexer.py` |
| **CodeIntel** | code graph (symbol FTS) | symbol FTS + 1-hop caller enrichment | ✅ graph live | via `recall_multi._codeintel_recall` |

**FTS5 tables (4, independent, ALL live):** `knowledge_fts` · `messages_fts` · `transcript_fts` · code-symbol FTS. **sqlite-vec tables (3, DORMANT since 6540970e — schema exists, zero prod writer/reader):** `knowledge_vec` · `memory_vec` · `transcript_vec`. Physical drop + `memory_embeddings.py` module removal is deferred cleanup, not yet done.

**`recall_multi.py` — 5-domain read-only aggregator:** `recall_all(query, domains=[context_files, ddd, library, session, codeintel])` → `BucketedRecall{buckets, hit_layers}`. Safety: `allow_embed=False` default = zero Bedrock + zero writes (moot now — the vector leg is gone everywhere since 6540970e, so ALL domains are FTS5/keyword regardless); `policy_excluded_files` privacy gate propagates across ALL domains (closed the `--domains`-bypasses-`--file`-privacy leak, run_4358cc95 Gate-2). DDD domain is keyword-only BM25 over `## sections`, never embeds.

> ⚠️ **`recall_all` is NOT on the production runtime path — only CLI/probe/test call it.** The prod runtime recall leg is `session_router._recall_for_query` (via `RecallEngine`), which fans over Knowledge/Library + Transcript + MemoryRecall ONLY. **CodeIntel and DDD domains exist solely inside `recall_all`; the running system never recalls them via query.**
>
> **DECISION (2026-07-01) — CodeIntel & DDD are deliberately NOT wired into the runtime recall leg (not debt, evaluated-and-rejected):**
> - **CodeIntel** reaches the agent by **push, not pull** — `code_intel_hook` (PreToolUse, `hook_builder.py:385`, injects callers/routes on Read/Grep, per-file project detection). It is NOT a rec­all subsystem and doesn't need to be. It has 6 live consumers (push-hook, CodeGraph UI, `routers/code_intel.py`, DDD drift detection, watcher+reindex job, monthly report) — **not a 鸡肋, but a SwarmAI-self-dev introspection tool.** Other projects have no graph simply because **they currently have no codebase to index** (not hardcoded to SwarmAI) — build/reindex applies to any project the moment it gets a repo_path.
> - **DDD** reaches the agent via **PROJECTS.md (P10 full-loaded context file) + the AGENT directive to Read a project's DDD before working** — recall is not its runtime channel.
> - **Why not wire them:** the runtime recall leg has **NO project binding** (`_recall_for_query` takes no `project` param). Wiring per-project CodeIntel/DDD into it would inject project A's symbols/DDD into project B's session — **pipeline is a cross-project dev tool; that's cross-project pollution.** If "blast-radius before any file is opened" ever becomes a real, high-frequency need, the correct layer is a **pipeline EVALUATE step that calls `load_project_graph(current_project)` explicitly** (project-bound, no-op when no graph) — not the generic recall leg.
> - Full reasoning + live evidence: `Knowledge/Designs/2026-06-28-context-recall-pure-filesystem-design.md` §3.2.

**Known debt:**
- ✅ **FIXED (run_f2ae50b3):** `transcript_indexer.upsert_chunk` external-content write-bug — the UPDATE branch did `INSERT OR REPLACE` (reversed postings with NEW values → progressive corruption). Now reverses OLD postings via the `'delete'` command before re-inserting (mirrors `knowledge_store.py:185`), and `repair_fts_index()` + `_fts_is_healthy()` heal is wired into `context_health_hook`. Note: `remove_session`'s plain `DELETE FROM transcript_fts` is SAFE (content row still present at delete time) — left as-is.
- 📐 **DEFERRED epic — do NOT fold in (STEERING #4):** two implementations of the `0.6·vector + 0.4·keyword` hybrid (Knowledge `recall_engine.py`, Memory `memory_embeddings.py`) are NOT mergeable as-is — they diverged on 4 axes: keyword engine (FTS-rank-normalized vs BM25+IDF), normalization point, missing-vector renorm (present only in `memory_embeddings`), and corpus+output shape (chunks vs section-max). A naive merge silently changes behavior on two deployed hot paths. Merge only behind its own pipeline with characterization tests. (run_f2ae50b3 Gate-0 finding)

### Self-Evolution Flow

MINE → ASSESS → ACT → AUDIT pipeline for autonomous skill improvement.

**Trigger:** `EvolutionMaintenanceHook` fires at session close with 7-day cadence gate (checks `.evolution_last_run` state file). Also triggered by Thursday cron (weekly maintenance).

**4-phase cycle** (`evolution_optimizer.py`):

| Phase | What | Key Logic |
|-------|------|-----------|
| **MINE** | `SessionMiner` scans transcripts (90-day window) | Single-pass O(files), TRIGGER keyword extraction, MD5 dedup, SDK noise filtering, 4K char field cap |
| **ASSESS** | `SkillFitness` scores each skill | 3-signal correctness: Jaccard (30%) + bigram (30%) + containment (40%). Overall: 0.5×correctness + 0.3×procedure + 0.2×judgment |
| **ACT** | `EvolutionOptimizer` rewrites underperforming skills | Max 3 changes/pass, max 5 LLM calls/cycle, 15KB max skill size, 20% growth cap |
| **AUDIT** | Verify + rollback if fitness drops | Regression gate: auto-revert if fitness drops >0.1 from previous cycle |

**Confidence scoring** (determines auto-deploy vs. surface recommendation):

| Signal | Calculation |
|--------|-------------|
| Evidence strength | ≥10 corrections: 1.0, ≥5: 0.8, ≥3: 0.6, ≥2: 0.5, else: 0.3 |
| Correction density | >50% of turns: 0.9, >30%: 0.6, >15%: 0.4, >5%: 0.2, else: 0.0 |
| Need signal (fitness) | <0.3: 1.0, <0.5: 0.7, <0.7: 0.4, else: 0.1 |
| Boosts | Recency: +0.05/correction (max +0.15), Repeat: max +0.10 |

Formula: `evidence × max(density, need) + recency_boost + repeat_boost`, capped at 1.0. HIGH ≥0.35 auto-deploys, MED ≥0.15 surfaces recommendation, LOW logs to `skill_health.json` only.

**Deployment:** Atomic backup → apply → write to temp → `os.replace()` → verify SkillGuard injection check → rollback on failure.

**SkillMetrics** (`skill_metrics.py`): Tracks invocation_count, success_rate, correction_rate, avg_duration per skill in SQLite. Evolution candidate criteria: correction_rate >0.3 OR success_rate <0.7, AND invocation_count ≥5.

**Correction detection** (`runtime_hooks.py`): 8 regex patterns for English and Chinese correction signals. Captures to `corrections.jsonl` with session/tool context.

**Key files:** `core/evolution_optimizer.py`, `core/skill_fitness.py`, `core/session_miner.py`, `core/skill_metrics.py`, `hooks/evolution_maintenance_hook.py`.

### Self-Evolution Closed Loop — Autonomous Cognitive Recording (shipped 2026-06-25, OT07)

Closes the OT07 "L0 dead-end": the evolution pipeline detected *operational* (skill-text) errors but the **cognitive** axis (judgment patterns — CLASS_A/B/C) never autonomously escalated. Designs: `Knowledge/Designs/2026-06-25-self-evolution-closed-loop-design.md`, `2026-06-25-self-evolution-proactivity-design.md`. Pipelines `run_448a4f7f` (autonomous recording), `run_0c8e007a` (paused-gauge fix).

**Root cause was a SEVERED WIRE, not an unreachable threshold:** `route_classification()` received a cognitive classification, parked it in `governance_pending.json` for human review, and **explicitly never called `tracker.record()`** (`governance_router.py`, the `counter_state == "pending_confirm"` branch). Meanwhile `escalate_class()` (invoked autonomously every session in `evolution_maintenance_hook.py`) read ONLY the human-verified counter — frozen at backfill dates. So the system extracted + classified recurring judgment patterns but the live signal never reached the escalation detector; proposals only fired after a manual escalation question. The "autonomous" loop was decoration.

**The fix — wire the live signal (XG directive, deliberately OVERRIDES the asymmetric-autonomy invariant):** recording a recurring mistake is COGNITION, not a permission item — the human gate belongs at the constitution-WRITE step, not at the act of *counting* one's own mistakes.
- **Auto-record cognitive corrections** → `governance_router.py` now calls `tracker.record(class_name, ..., correction_ref=...)` for cognitive CLASS_A/B/C, advancing the **live** counter without mutating the human-verified one (two views: live-candidate vs human-confirmed).
- **`escalate_class()` fires unasked** at threshold ≥3 on real recurrence → writes structural-fix proposals to `.evolution_proposals.json`.

**The noise-gate (3 layers of precision teeth, `correction_tracker.py` + `judgment_classifier.py` + `escalation_ladder.py`):**
1. **correction_ref idempotent dedup** — same ref parked 3× with different evidence strings counts ONCE (text-dedup would triple-count); seen-ref ledger persists across reloads.
2. **Confidence floor ≥0.6** — a low-confidence LLM guess parks for human review but does NOT auto-advance the autonomous counter (blocks false-structural proposals).
3. **Axis guard (cognitive-only)** — `is_cognitive_class()` makes OPERATIONAL/UNCLASSIFIED structurally unable to reach escalation, so the 800+-record OPERATIONAL noise class can never fire governance proposals.

**Growth report + constitution git-mirror (`eval_service.growth_report()`):** surfaces the closed loop as a session-briefing headline. Gathers (1) live tracker per-class counts, (2) autonomous proposals from `.evolution_proposals.json`, (3) constitution commits via `git log -- .context/{SOUL,AGENT,STEERING}.md` (churn-filtered). The 🧬 briefing line documents the circuit: correction → escalation → human-accepted git-tracked rule → report proves it happened. The git mirror is a **self-chosen guardrail** (CLASS_A = 12 occ / 0 self-catches; writing SOUL is the highest-confidence-lowest-self-check moment), veto-via-revert — not a human lock. M5 closed-loop audit adds a Goodhart guard + loop-closed meta-test (`9ef03419`).

**Key files:** `core/evolution/governance_router.py`, `core/evolution/correction_tracker.py`, `core/evolution/escalation_ladder.py`, `core/evolution/judgment_classifier.py`, `core/eval_service.py` (`growth_report`), `SELF.md` (M3 resident self-portrait + Recurrence Radar, `e35d1afd`).

### Hive E2E Architecture

Cloud deployment of SwarmAI on EC2. Same Python backend + React frontend, no Tauri shell. `SWARMAI_MODE=hive` activates cloud-specific behavior.

**Provisioning flow** (Desktop → AWS):

| Step | What | Detail |
|------|------|--------|
| 1. Create | `POST /api/hive/instances` | Spawns async provisioner task |
| 2. IAM | Create role + instance profile | Bedrock invoke, S3 read from `swarmai-hive-releases-{region}`, SSM, EC2 self-tag |
| 3. AMI | Amazon Linux 2023 ARM64 | Via SSM parameter `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64` |
| 4. Launch | EC2 Graviton instance | m7g.medium–2xlarge, c7g.*, t4g.* variants only (ARM64) |
| 5. Bootstrap | 9-step user data script | python3.12, nodejs20, swarm user, S3 download, venv, Caddy, systemd, health check (120s), EC2 tagging |
| 6. CDN | CloudFront + Caddy reverse proxy | Caddy basicauth (bcrypt 14 rounds, 4-word passphrase), reverse proxy to `127.0.0.1:18321` |

**Timeline:** EC2 launch + bootstrap: 5–10 min. CloudFront HTTPS: 5–15 min. Total: 10–25 min.

**Instance lifecycle:** pending → provisioning → installing → running (or error). `systemd` services: `swarmai-hive.service` + `caddy.service` (auto-restart, RestartSec=5).

**Auth:** Caddy basicauth with bcrypt (14 rounds). Password: 4-word passphrases from 256-word list (~32 bits entropy). Credentials stored in Desktop's SQLite for management.

**Hive-mode differences:**
- Platform filtering: `ProjectionLayer` excludes `macos` + `desktop` platform skills on `hive` (count is volatile — measure live)
- Gateway: no Slack adapter (no Socket Mode in cloud)
- Write guard: `_require_desktop()` returns HTTP 403 on all management endpoints — Hive cannot self-modify
- Port: same fixed `127.0.0.1:18321` as desktop daemon
- Logs: `/home/swarm/.swarm-ai/logs/backend.log` with rotation

**REST API** (`routers/hive.py`, 641 lines, 11 endpoints): deploy, list, get, stop, start, update (SSM-based, 2–5 min), terminate, retry, reset-password, credentials, health.

**Supported regions (8):** us-east-1, us-east-2, us-west-1, us-west-2, eu-west-1, eu-central-1, ap-northeast-1, ap-southeast-1.

**Key files:** `routers/hive.py`, `hive/provisioner.py`, `hive/user_data.py` (281 lines, EC2 bootstrap), `core/projection_layer.py`, `skills/s_hive-manager/INSTRUCTIONS.md`.

### Runtime Hooks

Two-layer hook system: SDK-integrated hooks (during agent session) and post-session lifecycle hooks (after session close).

**SDK hooks** — registered via `HookRegistry` in `hook_builder.py`. Sequential chaining, per-hook timeout 5.0s, first-block-wins short-circuit.

| Hook | Event | Matcher | Blocks | Purpose |
|------|-------|---------|--------|---------|
| pre_tool_logger | PreToolUse | — | No | Log all tool invocations |
| dangerous_command_gate | PreToolUse | Bash | Yes | Gate destructive commands |
| skill_access_checker | PreToolUse | Skill | Yes | Enforce skill access list |
| correction_capture | PostToolUseFailure | — | No | Log to `corrections.jsonl` (512KB cap) |
| error_pattern_detector | PostToolUseFailure | — | No | Inject hint after 2+ consecutive failures |
| failure_tracker_reset | PostToolUse | — | No | Reset consecutive failure counter |
| file_tracker | PostToolUse | — | No | Populate `session_context["_files_touched"]` |
| session_checkpoint | PostToolUse | — | No | JSON checkpoint every 10th tool call |
| memory_edit_guard | PostToolUse | — | Yes | Validate Edit on MEMORY.md/EVOLUTION.md |
| subagent_capture | SubagentStop | — | No | Extract last 5KB transcript, parse errors |
| user_correction_detector | UserPromptSubmit | — | No | Detect correction signals (EN + CN) |
| post_compact_injection | UserPromptSubmit | — | No | Session continuity after compaction (fires once) |
| high_signal_capture | UserPromptSubmit | — | No | Detect decision/lesson/rule → append DailyActivity |

**Post-session lifecycle hooks** — `BackgroundHookExecutor` with asyncio.Queue (max 100 items), 30s per-hook timeout, sequential execution. Critical alert on 3+ consecutive failures. Non-blocking `fire()` returns immediately.

**Post-session hook chain** (fired by `SessionLifecycleHookManager`):
1. `DailyActivityExtractionHook` — Session → DailyActivity log
2. `DistillationTriggerHook` — DailyActivity → MEMORY.md/EVOLUTION.md
3. `ContextHealthHook` — Index refresh, L1 cache invalidation, DDD staleness
4. `ImprovementWritebackHook` — Session lessons → IMPROVEMENT.md
5. `EvolutionMaintenanceHook` — Skill evolution cycle (7-day cadence)
6. `UserObserverHook` — Behavioral pattern detection → USER.md suggestions
7. `SkillMetricsHook` — Record skill invocation stats

**Session context populated by hooks:** `_files_touched` (set), `_compacted` (bool), `_last_notification` (dict), `_stop_info` (dict).

**Key files:** `core/hook_builder.py` (HookRegistry, 287 lines), `core/runtime_hooks.py` (13 hook factories, 841 lines), `core/session_hooks.py` (BackgroundHookExecutor, 627 lines), `core/security_hooks.py` (pre_tool_logger, dangerous_command_gate, skill_access_checker).

### Pollinate v3 — "Message First, Format Follows" (Personal Content Delivery Engine)

`s_pollinate` — content delivery engine that reads the same DDD knowledge as Pipeline and produces brand-correct, audience-correct content across 12 professional formats. Parallel architecture to Autonomous Pipeline: same DDD substrate, same convergence loop pattern, different output domain (media vs code).

**v3 Architecture (shipped 2026-05-26): DISCOVER-first + 9-stage delivery + 12 tracks + cross-format quality gates.**

**v3 Key Shift:** User decides scope (DISCOVER), agent decides quality. One `content_package.md` → N native productions (not exports). Incremental: add tracks later without re-doing upstream.

**9 stages:** DISCOVER → EVALUATE → THINK → STRATEGIZE → PLAN → BUILD → REVIEW → DELIVER → REFLECT

| Stage | What it does |
|-------|-------------|
| DISCOVER | 5 questions (who/what outcome/context/scope/confirm) → `confirmed_tracks` |
| EVALUATE | 5-dim ROI scoring, scoped to confirmed formats |
| THINK | Research + competitive + differentiation framing |
| STRATEGIZE | PR/FAQ + channel × format matrix |
| PLAN | Content package (5 layers) + per-track specs |
| BUILD | Per-track native production (only confirmed tracks) |
| REVIEW | RP-V/RP-P/RP-X patterns + Audience Simulation |
| DELIVER | Taste gate + confidence scoring + structured deliverable block |
| REFLECT | IMPROVEMENT.md + user_prefs + DailyActivity |

**12 Supported Tracks (v3):**

| Track | Format | Native Toolchain | Maturity |
|-------|--------|-----------------|----------|
| A: Video | MP4 4K | Remotion + TTS + ffmpeg | Prod-proven |
| B: Poster | PNG 1080px | HTML + Playwright screenshot | Prod-proven |
| C: Narrative | Markdown | GEO scorer + platform adaptation | Prod-proven |
| D: Shorts | MP4 vertical | generate_shorts.py + Remotion | Prod-proven |
| E: Deck | PPTX | PptxGenJS + OOXML post-processing | Prod-ready |
| F: PDF | PDF (A4/Letter) | HTML + Playwright pdf() | Prod-ready |
| G: Data Report | XLSX | openpyxl + brand_chart.py | Code-complete |
| H: Document | DOCX | python-docx + tracked changes | Code-complete |
| I: AI Image | PNG/prompt.json | Structured prompt + tool detection | Spec-complete |
| J: Interactive Report | HTML | s_html-artifact templates + branded CSS | Spec-complete |
| K: Podcast | MP3/script.json | Two-host dialogue + edge-tts/Polly | Spec-complete |

Each track has an independent instruction file (`tracks/track-{letter}-{name}.md`) loaded on demand during BUILD. INSTRUCTIONS.md acts as router.

**Content Package Architecture (5 layers):**
- Core Layer (ALL formats) — thesis, audience, key points, differentiation
- Narrative Layer (video/podcast/narrative) — arc, transitions, hooks, tone
- Data Layer (data-report/interactive-report) — metrics, comparisons, time series
- Visual Layer (poster/deck/PDF/image) — diagrams, layout hints, chart candidates
- Evidence Layer (ALL) — proof points, quotes, code examples, references

PLAN stage populates only layers needed by `confirmed_tracks`.

**Quality Gates (per format):**

| Format | Gate | Enforcement |
|--------|------|-------------|
| Poster | 8-layer convergence loop (L1-L8) | `convergence_gate.py` |
| Video | 12 RP-V review patterns | `check_rpv.py` + Studio preview |
| Narrative | GEO signal stack (4-pillar, ≥60/100) | `geo_score.py` |
| Track E-K | Per-track RP gates (≥6 per track: RP-E/F/G/H/I/J/K) | Manual checklist |
| Cross-format | 5 RP-X consistency checks | `cross_format_check.py` |
| All | Brand conformance (identity.yaml) + platform specs | `check_specs.py` |

**Inline Pre-Verification (PV-1~PV-4):** Before building ANY track:
1. Content package has required layer
2. Direction is selected
3. Output directory exists
4. No stale output from prior run

**8-Layer Poster Gate (unchanged from v2):**

| Layer | Check | Auto-fixable? |
|-------|-------|---------------|
| L1 | Direction declared in HTML | ✅ |
| L2 | Zero hardcoded hex (token purity) | ✅ |
| L3 | Spacing ≤ 72px between sections | ⚠️ |
| L4 | ALL text-align: center | ✅ |
| L5 | Anti-Slop ban lists clean | ⚠️ |
| L6 | Platform dimensions (1080px width) | ✅ |
| L7 | Brand present (watermark + QR + GitHub) | ✅ |
| L8 | 2-variant output | ✅ |

**Design System v2 — 5 Named Directions:**

| Direction | Mood | Key Palette |
|-----------|------|------------|
| D1: Obsidian | Authoritative, premium | Dark bg + gold accents |
| D2: Paper | Clean, analytical | Cool blues + white |
| D3: Ink | Organic, grounded | Dark greens + warm |
| D4: Neon | Technical, cyberpunk | Dark + electric highlights |
| D5: Morandi | Refined, muted | Soft neutral tones |

Direction selection: content_triggers scoring (strong=3, moderate=2, weak=1). Default: D1.

**DISCOVER Stage (v3 addition):**
- Fast-path: `format_recommend.py --message` detects explicit format mentions → skip questions
- Full discovery: 5 questions → `format_recommend.py` maps (audience × outcome × context) → track list
- Incremental: `"再出个 deck"` → load existing content_package, skip upstream, build new track only
- Authority rule: `confirmed_tracks` is single source of truth. No downstream stage can override.

**Cross-Format Consistency (RP-X, multi-track runs only):**
- RP-X1: Brand token consistency (same direction across all tracks)
- RP-X2: Message alignment (thesis keywords present in all track outputs, 4/7 threshold)
- RP-X3: Data integrity (same metrics/numbers across formats)
- RP-X4: Naming conventions (lowercase, no spaces in output files)
- RP-X5: Visual coherence (shared color palette across HTML outputs)

Enforcement: `cross_format_check.py` — runs during REVIEW for multi-track runs.

**Structured Delivery Output (v3 addition):**
4 deliverable block templates for chat window output:
1. Poster → Platform Matrix (小红书/朋友圈/Twitter with inline images)
2. Video → Preview + Thumbnails + Platform Metadata
3. Multi-deliverable → Series Overview + Per-piece
4. Document tracks → Per-track file delivery with inline previews + quality summary

All deliverables are copy-paste ready. Quality gates invisible when passing (one trust line).

**Cross-engine compound:** Pipeline writes TECH.md → Pollinate reads for accuracy. Pollinate writes PRODUCT.md insights → Pipeline uses for EVALUATE prioritization. Both engines' REFLECT feeds DDD Cultivation (Channel 3).

**EVALUATE gate:** 5-dimension scoring (knowledge differentiation 0.30, audience match 0.25, asset readiness 0.20, timeliness 0.15, production complexity 0.10). ROI ≥ 3.0 = GO, 2.0-2.9 = DEFER, < 2.0 = REJECT.

**GEO Signal Stack (narrative/article):** 4-pillar AI discoverability scoring. Evidence Density (35%), Structure & Position (25%), Authority Signals (25%), AI Crawlability (15%). Pass threshold: 60/100.

**Anti-Slop Mechanism:** 32 visual + 13 structural ban patterns. Enforced via `convergence_gate.py` L5. Principle: "告诉 AI 什么不能做，比告诉它什么能做更有效."

**Scripts** (`skills/s_pollinate/scripts/`, 24 files, 5.9K lines total):

| Script | Purpose |
|--------|---------|
| `convergence_gate.py` | **8-layer publish-ready gate** (mechanical HTML/CSS verification) |
| `cross_format_check.py` | **RP-X cross-format consistency** (5 checks: brand/message/data/naming/visual) |
| `format_recommend.py` | **DISCOVER engine** (audience×outcome×context → track recommendations + fast-path detection) |
| `geo_score.py` | **GEO signal stack scorer** (AI engine discoverability, 4-pillar) |
| `pollinate_validator.py` | **Structural validator** (6 invariants: platform matrix, QR, GitHub link, variants, extensions, dir structure) |
| `p2_scan.py` | **P2 hero framing gate** (checks thesis prominence + delegation fidelity) |
| `check_rpv.py` | **RP-V review pattern checker** (12 audio-video patterns) |
| `brand_chart.py` | Direction tokens → openpyxl chart styling (Track G) |
| `deck_notes_injector.py` | OOXML speaker notes injection (Track E) |
| `evaluate_topic.py` | 5-dimension topic scoring + ROI calculation |
| `confidence_score.py` | Quality confidence scoring (composite) |
| `generate_shorts.py` | Short-form video generation |
| `generate_tts.py` | Text-to-speech orchestration |
| `topic_backlog.py` | Topic queue management |
| `publish_dashboard.py` | Publication tracking dashboard |
| `publish_meta.py` | Metadata for published content |
| `check_prereqs.py` | Prerequisite validation |
| `check_specs.py` | Platform spec compliance |

**TTS architecture** (`scripts/tts/`):
- 3 backends: Polly (AWS, primary), Edge TTS (free, default), Azure (optional)
- Resolution hierarchy: `user_prefs > env > edge`
- `phonemes.py` — Pronunciation normalization
- `sections.py` — Content sectioning for TTS
- `ssml.py` — SSML generation (engine-aware)
- `srt.py` — Subtitle generation
- `backends/polly.py` (17KB) — AWS Polly with SSML, `backends/edge.py` — Edge TTS (free), `backends/azure.py` — Azure Cognitive Services

**Quality gates summary:**
- Poster: 8-layer convergence gate (`convergence_gate.py`) + structural validator (`pollinate_validator.py`) + P2 hero framing (`p2_scan.py`) + adversarial brand review sub-agent
- Video: 12 RP-V review patterns (`check_rpv.py`) + Studio preview (mandatory, cannot be skipped)
- Narrative/article: GEO signal stack (`geo_score.py`, ≥60/100)
- Deck: RP-E1~E8 (speaker notes, overflow, progressive reveal, inventory validation)
- PDF/XLSX/DOCX: RP-F/G/H (6-7 gates per track)
- AI Image/Interactive Report/Podcast: RP-I/J/K (6 gates per track)
- Cross-format (multi-track): RP-X1~X5 via `cross_format_check.py`
- All formats: brand conformance (identity.yaml) + platform specs (`check_specs.py`)
- Max retries: BUILD 3, REVIEW 2, others 1–2. 3 convergence iterations before escalation.

**Output directory:** `Knowledge/Pollinate/` with date-prefixed subdirectories (`YYYY-MM-DD-{name}/`).

**Auto-publish:** `publish_to_pages.py` pushes outputs to GitHub Pages (`xg-gh-25/swarm-content`). Runs at end of DELIVER (non-blocking). Gallery: https://xg-gh-25.github.io/swarm-content/. Local clone: `~/.swarm-ai/swarm-content-repo/`. Manifest tracks what's published (`.published.json`). Index auto-regenerates on every publish.

### SSE Streaming Pipeline

Real-time chat via Server-Sent Events. One SSE connection per active tab, managed end-to-end from frontend to backend subprocess.

**Backend** (`routers/chat.py`, ~1130 lines):
- Endpoint: `POST /api/chat/stream` — main streaming route.
- `sse_with_heartbeat()` wraps the generator with 15s heartbeat pulses (prevents connection timeout).
- Content validation: max 20 blocks per message, max 32MB total payload.
- Message persistence: user message stored **before** slot acquisition (survives queue timeout). Assistant message incremented per streaming event (crash-safe).
- ETag caching: per-session `session_id:msg_count` on the messages endpoint (304 on no-change).
- Three streaming endpoints: `streamChat()`, `streamAnswerQuestion()`, `streamCmdPermissionContinue()`.

**Frontend** (`useChatStreamingLifecycle.ts` ~3200 lines + `streaming-machine.ts` ~385 lines + `chat.ts`):

| Parameter | Value | Purpose |
|-----------|-------|---------|
| RECONNECT_MAX_ATTEMPTS | 3 | Max SSE reconnection retries |
| RECONNECT_BASE_DELAY | 1000ms | Initial backoff delay |
| RECONNECT_MAX_DELAY | 30,000ms | Maximum exponential backoff |
| STALL_THRESHOLD_TEXT | 60,000ms | Detect stuck text generation |
| STALL_THRESHOLD_TOOL | 180,000ms | Detect stuck tool execution |
| HEAL_GRACE_PERIOD | 30,000ms | Suppress error during backend self-heal |
| MIN_ACTIVITY_DISPLAY | 1,500ms | Minimum spinner display time |

**Streaming State Machine (2026-06-18):** Explicit `useReducer(streamingReducer)` with 11 discriminated modes, 20 events, per-tab `StreamingState` on `UnifiedTab`. Replaces implicit 18-boolean-flag state machine with exhaustive transition table. 300 tests cover every (mode × event) pair.

| Mode | Meaning | Spinner? |
|------|---------|----------|
| `idle` | No stream, user can type | No |
| `pending` | Message sent, waiting for session_start | Yes |
| `streaming` | Active SSE, events flowing | Yes |
| `reconnecting` | Connection-phase error, auto-retry (max 3) | Yes |
| `resuming` | Backend subprocess respawning | Yes |
| `self_healing` | Mid-stream disconnect, 30s grace | Yes (invisible) |
| `waiting_input` | Agent asked question, waiting for answer | No |
| `permission_needed` | Agent needs command permission | No |
| `session_busy` | SESSION_BUSY error, polling | No |
| `drain_pending` | Result received, draining queued message | Yes |
| `error` | Unrecoverable, user can retry | No |

**Architecture:** State machine (`streaming-machine.ts`) is a pure reducer — deterministic, zero side effects, exhaustively testable. Per-tab `StreamingState` coexists with legacy boolean flags (`isStreaming`, `isReconnecting` etc.) during migration. `setIsStreaming()` single-writer dispatches to both. Key files: `streaming-machine.ts` (types + reducer + helpers), `useChatStreamingLifecycle.ts` (hook with `useReducer` + SSE handlers), `useUnifiedTabState.ts` (per-tab state with `streamState` field).

**Per-tab stream tracking:** `tabMapRef: Map<tabId, UnifiedTab>`. Each tab has `streamState: StreamingState` + legacy `isStreaming` boolean. Stream generation counter (`streamGenRef`) prevents stale handler execution. `pendingStreamTabs: Set` provides React re-render trigger (ref mutations are invisible to React).

**Event types:** `session_start`, `text_delta`, `thinking_delta`, `assistant`, `result`, `error`, `ask_user_question`, `cmd_permission_request`, `session_resuming`, `reconnecting`, `turn_limit_reached`, `context_warning`, `compaction_guard`, etc. Backend sends snake_case → frontend processes as-is.

### Chat Resume

Two-layer strategy for recovering conversation context after session interruption.

**Layer 1: Live Resume** — Agent subprocess still alive.
- Trigger: `resume=sdk_session_id` parameter in SSE request.
- Behavior: reuse existing SessionUnit, no context loss. SDK handles conversation continuity internally.
- Detection: `unit.state != DEAD and unit._sdk_session_id is not None`.

**Layer 2: Cold Resume** — Subprocess was killed or crashed, conversation must be reconstructed.
- Trigger: `unit.state == COLD and unit._sdk_session_id is None`, but prior conversation exists in DB.
- Behavior: `build_resume_context()` assembles 5-layer enrichment (see Context Management section) injected into system prompt.
- Auto-resume path: `_ensure_spawned()` detects COLD + existing `_sdk_session_id` → injects `--resume` flag automatically.

**Crash checkpoint** (`session_checkpoint.json`):
- Written every 10th tool call by `session_checkpoint` hook.
- Contains: session_id, tool_count, files_in_progress, corrections_count, timestamp.
- On crash recovery: `DailyActivityExtractionHook` merges checkpoint data into crash entry.
- Cleaned up on normal session close (no checkpoint = no crash).

**Retry strategy:** 3× exponential backoff. Each retry uses `--resume` flag. `_retry_with_resume()` checks alive_count ≥ max_tabs before spawning (prevents bypass of slot limits — COE root cause fix).

**Key invariant:** User message is stored to DB **before** slot acquisition. If the queue times out, the message is never lost — cold resume picks it up.

### Canvas — the session's live output surface (2026-08-02)

- [model] **Canvas = FileViewerPanel + CanvasOutputRail, output-triggered & fully tab-scoped** — replaced SwarmRadar. **Signal chain:** agent Write/Edit → `streaming_orchestrator.get_tool_category`→`write` → `MergedToolBlock` dispatches `swarm:file-referenced {path, operation:'written', sessionId}` → `useCanvasAutoSurface` (debounce-coalesce + gentle suppression) → `swarm:open-file` → `ThreeColumnLayout` handler → `setFileViewerFile` → content-adaptive view (renderable→Preview via `HtmlRenderer` `<iframe sandbox="allow-scripts allow-popups">`; modified→Diff; new→Source). **Output list** = `useReferencedFiles(sessionId).grouped.written` MINUS bookkeeping (`isBookkeepingPath`: denylist `.artifacts`/`.git`/`.context` + dotfile-basename + temp) + `useChangeStatus` NEW/UPD git badge (both hooks in `hooks/`, NOT Radar-owned). **Tab-scoping is the key invariant:** all Canvas state is per-active-tab; the switch signal is `activeTabId` (published into `ActiveSessionMeta`/`SessionMetaContext` by ChatPage), NEVER `sessionId` (which flips undefined→resolved on a new tab's first message → would false-clear mid-turn). `shouldResetCanvasOnTabChange(prev,next) = prev!==undefined && next!==prev` (pure, unit-tested); on real switch → clear file+pin/mute/collapsed. `file-referenced` events carry `sessionId` so background keep-mounted tabs don't leak into the active tab (both auto-surface AND `useReferencedFiles` filter, fail-open when unstamped). **Controls:** pin (file-scoped) / mute (session-scoped) / expand / collapse-to-narrow-dock; LeftSidebar `Canvas` card → `swarm:open-canvas` for file-less open. **SwarmRadar removed:** 3 sections replaced (Changes→Canvas, Attention→ChatHeader `AlertsPill` via shared `useRadarAttention`, Jobs&Runs→left-nav overlay); KEPT `AttentionList`(AlertsPill)/`HistoryView`/`useRadarAttention`/`types.ts`. (2026-08-02)
  <!-- ref:0 | last:none | decay:active | source:manual -->

### OverlayHost — the single fullscreen-surface authority (re-architected 2026-08-04, run_fdeaead8)

- [model] **OverlayHost = ONE host + ONE `activeOverlay` slot + a declarative registry; it is the SOLE renderer/geometry authority for every fullscreen surface.** Replaced a 5-disease legacy design (D1 split mounts / D2 two state machines / D3 four copy-paste "mirror" overlays / D4 implicit `clearNavSource` contract / D5 hand-computed viewport geometry). **Files:** `contexts/OverlayContext.tsx` (the single `activeOverlay: string|null` state + open/close + the show-event bridge), `components/layout/OverlayHost.tsx` (mount + geometry + spout chrome, rendered ONCE inside the `relative` MainContentArea), `components/layout/overlayRegistry.tsx` (the `OverlaySpec` registry + the ChatPage ctx-bridge), `components/layout/overlaySurfaces.tsx` (side-effect module registering every surface — imported once by ThreeColumnLayout), `components/layout/overlayShell.tsx` (shared thin frame primitives: `fmtTs` / `WorkbenchToolbar` / `OverlayDrawer`).
  - **Geometry invariant (D5 killed — why the zoom bug is structurally impossible, not patched):** the scrim is `position:absolute; inset:0` of the in-flow `relative` MainContentArea — it reads ZERO measured window/rect coordinates. The legacy path was `position:fixed` + a `chatAreaBounds` ResizeObserver that measured `getBoundingClientRect` (post-zoom px) and wrote it back as inline px on the fixed scrim → under `<html style.zoom=Z>` WebKit multiplied by Z AGAIN (the 6×-patched overflow double-count). With `inset:0` there is no measured px to double-count. **RULE: never reintroduce viewport-measurement + inline-px geometry for a fullscreen surface — an in-flow absolute/flex child does it declaratively and inherits zoom natively.** Spout origin re-derives the source card's live rect from `spec.sourceCardTestId` via `document.querySelector([data-testid])` at open time (replaces the deleted `navSource` singleton — no "remember to clear" contract).
  - **🚨 The proprioception contract — the load-bearing gotcha (a migration MUST re-home BOTH halves):** the legacy `swarm:show-<id>` window events are NOT just internal wiring — they are the agent's **ACT vocabulary** (`ui_action` → backend `UI_COMMAND_ALLOWLIST` derived from `ALL_SHOW_EVENTS` → dispatch `swarm:show-<id>`) AND the source of the **SENSE payload** (`active_overlay` in `editor_context`, read from the overlay state). OverlayContext's bridge maps `swarm:show-<id>` → `openOverlay(id)` (afferent OPEN — NOT close-only), and `openOverlay` fires `BACK_TO_CHAT_EVENT` (efferent, closes any legacy overlay; self-guarded by `openingSelf`). ChatPage's SENSE reads `useOverlay().activeOverlay` (NOT the retired `useActiveOverlayEvent` singleton). **run_fdeaead8 M4 nearly shipped both halves broken** (every unit test passed; the agent could neither open nor see migrated surfaces — caught by adversarial, not E2E). **RULE: migrating ANY surface on/off this mechanism must grep BOTH the event DISPATCHERS (ACT) AND the state READERS (SENSE) and re-home both — the render migration is the visible 20%, re-homing dispatchers+readers is the invisible 80%.** Regression guard: `overlayHostE2E.test.tsx` drives the real registry through `swarm:show-<id>` (7 surfaces parametrized in the open-loop + `context` in the back-to-chat test; `ALL_SHOW_EVENTS` = 9 agent-openable ids total) — mutation-verified: revert open-on-show → RED.
  - **Registry model:** each surface is a data `OverlaySpec {id, title, mode, width, sourceCardTestId, tint, autoHeight, render(ctx)}` in `overlaySurfaces.tsx`; the host looks it up by `activeOverlay` and wraps `render(ctx)` in scrim+panel+spout+header. ChatPage-owned tab operations (dispatchPrompt/dispatchTodo/resumeSession/deleteSession/agentId) reach surfaces via a module-level **ctx bridge** (`setOverlayCtxBridge`, a ref not context — no host re-render on ChatPage updates); data-reactive surfaces (History) self-fetch off the shared TanStack cache instead. Fresh-mount-per-open (host unmounts on close) gives component-local state a clean start (replaced NewBrain's reset-on-reopen hack). **`ALL_SHOW_EVENTS` (in `useExclusiveOverlay.ts`) is the SSOT** for agent-openable surface ids — the backend allowlist derives from it; the registry id == the event suffix (`swarm:show-brain-hub` → `brain-hub`). Library is registered but deliberately ABSENT from `ALL_SHOW_EVENTS` (nav-card-only, banned from the agent allowlist). `Modal.tsx` is now the small-centered-dialog ONLY (its `size="fullscreen"` branch + `chatAreaBounds`/`navSource`/`useExclusiveOverlay`-hook were deleted in M5). Settings + OS Eval are host surfaces (`settingsTab` deep-link stays LayoutContext state); WorkspaceSettings + file-editor remain small `activeModal` modals. (2026-08-04, run_fdeaead8)
  <!-- ref:0 | last:none | decay:active | source:manual -->

### Frontend Architecture

React 19 + Vite 6 + TanStack Query. Component hierarchy anchored by `ThreeColumnLayout` (sidebar + chat + panel).

**Component tree** (from `App.tsx`):
```
ThemeProvider → QueryClientProvider (5min stale, retry=1)
  → ToastProvider → HealthProvider → ErrorBoundary
    → BackendStartupOverlay (waits for /health)
    → DaemonNudgeBanner + UpdateNotification (desktop only)
    → AppRoutes (onboarding guard)
      → ThreeColumnLayout
        → ChatPage (tab orchestration + message rendering)
```

**React contexts (6):**

| Context | Purpose |
|---------|---------|
| ThemeContext | Dark/light theme state |
| ToastContext | Notification toast queue |
| HealthContext | Backend readiness (blocks routes until /health responds) |
| ExplorerContext | Workspace file tree + 5s ETag polling |
| LayoutContext | small-modal (`activeModal`: file-editor/workspace-settings) + `settingsTab` deep-link state |
| OverlayContext | the single `activeOverlay` fullscreen-surface slot + show-event bridge (see § OverlayHost) |

**Service modules (24):** agents, api, channels, chat, evolution, hive, mcpConfig, plugins, radar, search, settings, skills, system, tabPersistence, tasks, tauri, todos, tscc, updater, voice, workspace, workspaceConfig, and test utilities. Each wraps `api.ts` (centralized fetch with auth headers, error handling, base URL from Tauri or localhost:8000).

**Backend readiness:** `BackendStartupOverlay` polls `GET /health` on mount. Routes don't render until backend responds `{"status":"healthy"}`. Prevents race conditions between Tauri window open and daemon startup.

### FileEditor & Review Mode (v1.5 — 2026-06-08)

Unified file viewer/editor rendered as either side panel (`FileViewerPanel`) or fullscreen modal (`FileEditorModal`). Both delegate to `FileEditorCore` for text/markdown/svg files.

**Key files:**
- `components/common/FileEditorCore.tsx` — Shared editor surface (textarea + syntax overlay + selection + review)
- `components/common/CommentPopover.tsx` — Inline comment input (Portal-based, positioned near selection)
- `components/common/ReviewModeGutter.tsx` — Line-number gutter with comment badges
- `components/file-viewer/FileViewer.tsx` — Tab management + content cache + type routing
- `hooks/useReviewMode.ts` — Review state management (comments, persistence, formatting)

**Review Flow (selection-based, not line-number-based):**

```
User selects text → floating [Comment] button → CommentPopover → user types instruction → Send
  → Chat receives: filePath + selectedText + instruction (agent greps to locate, not line numbers)
  → Agent edits file → SSE file_changed event → FileEditor refetches → diff highlight (5s green)
  → Confirm/Redo bar: [✓ Accept] dismisses, [↩ Redo] pre-selects changed text for re-instruction
```

**Content refresh triggers (3 layers, no polling):**
1. **SSE event** (`swarm:file-changed`) — backend yields when agent's Edit/Write tool succeeds in same session
2. **Visibility change** — `document.visibilitychange` refetches if >3s since last fetch (catches external edits on tab-return)
3. **Manual reload** — toolbar 🔄 button always available

**SSE file_changed flow:**
```
session_unit.py: ToolUseBlock(Edit/Write) → track file_path by block.id
  → ToolResultBlock (success) → yield {type:"file_changed", path:"/abs/path"}
  → useChatStreamingLifecycle.ts: dispatch CustomEvent('swarm:file-changed')
  → FileEditorCore: listener matches path → api.get('/workspace/file') → setContent + highlight
```

**Design decisions:**
- `selectedText` as anchor (not line numbers) — stable across window widths and soft-wrap
- `onMouseDown={preventDefault}` on floating button — prevents textarea selection collapse
- `??` (nullish coalescing) for selectedTextOverride — empty string `''` is valid selection (blank line)
- `overflow:hidden` + `translateY(-scrollTop)` on highlight overlay — syncs with textarea scroll
- `clearTimeout` before each new highlight timer — prevents stale timer clearing fresh highlights

### Database & Storage

SQLite in WAL mode at `~/.swarm-ai/data.db`. Schema version 6 with auto-migration (v6 added Root-1 SSOT: `messages.sent` + `messages.pending_seq` + pending-monotonicity unique index).

**WAL configuration:** `journal_mode=WAL`, busy_timeout=5000ms, auto_checkpoint=1000 pages (~4MB).

**Core tables (23 total):**

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| **agents** (28 cols) | id, name, model, permission_mode, system_prompt, allowed_tools, sandbox | Agent configurations |
| **sessions** (11 cols) | id, agent_id, title, status, metadata, last_accessed | Chat session metadata |
| **messages** (11 cols) | id, session_id, role, content, model, expires_at (90-day TTL), `sent`, `pending_seq` | Conversation history + Root-1 pending contract |
| **channels** (14 cols) | id, channel_type, agent_id, config, status, allowed_senders, rate_limit | Slack/channel config |
| **channel_sessions** (12 cols) | channel_id, external_sender_id, session_id, message_count | Per-user channel conversations |
| **channel_messages** (11 cols) | channel_session_id, direction, content, status | Inbound/outbound channel messages |
| **todos** (12 cols) | id, title, status, priority, linked_context, task_id | Radar ToDo items |
| **tasks** (17 cols) | id, agent_id, session_id, status, priority, blocked_reason | Background task tracking |
| **skill_metrics** | skill_name, invocation_count, success_rate, correction_rate | Skill performance tracking |
| **token_usage** | session_id, input_tokens, output_tokens, model, date | Token accounting |
| **hive_instances** | id, status, region, instance_type, credentials | Hive cloud instances |
| **users** (8 cols) | id, username, password_hash, preferences | User accounts |
| **app_settings** (5 cols) | initialization_complete, onboarding_complete | App state flags |

Additional tables: `mcp_servers`, `workspace_config`, `workspace_mcps`, `workspace_knowledgebases`, `workspace_audit_log`, `chat_threads`, `thread_summaries`, `plugins`, `marketplaces`, `channel_user_identities`.

**API layer** (`backend/routers/`, 27 files, ~171 endpoints):

| Router | Endpoints | Key Routes |
|--------|-----------|------------|
| chat.py | 12 | /stream, /sessions, /stop, /compact, /cmd-permission |
| system.py | 19 | /health, /version, /status, /max-tabs, /tokens/usage |
| hive.py | 15 | /instances CRUD, /update, /reset-password, /credentials |
| channels.py | 12 | CRUD + /start, /stop, /restart, /test |
| workspace_api.py | 15 | /browse, /search, /tree (ETag), /create-project |
| workspace_config.py | 12 | Config CRUD per workspace |
| plugins.py | 12 | Marketplace + plugin management |
| agents.py | 8 | Agent CRUD + defaults |
| todos.py | 9 | Radar ToDo CRUD |
| tasks.py | 8 | Background task management |
| artifacts.py | 8 | Pipeline artifact CRUD |
| skills.py | 7 | Skill listing + execution |
| mcp.py | 7 | MCP server management |
| auth.py | 6 | /register, /login, /refresh, /logout |
| projects.py | 6 | DDD project CRUD |
| settings.py | 4 | App settings |

**Configuration** (`config.py`): JWT HS256 (15min access, 7-day refresh). Rate limit: 100 req/min. CORS: localhost:5173, localhost:3000, localhost:1420, tauri://localhost. Server: 127.0.0.1:8000 (dev), 127.0.0.1:18321 (daemon).

### Middleware (`backend/middleware/`)

Authentication and request processing layer. 13 functions.

| Symbol | Purpose |
|--------|---------|
| `get_current_user` | Extract + validate JWT from request → user object |
| `get_optional_user` | Same but returns None for unauthenticated routes |
| `require_owner` | Dependency that blocks non-owner access |
| `rate_limiter` | Per-IP rate limiting (100 req/min default) |

Sits between FastAPI route handlers and core logic. All protected routes use `Depends(get_current_user)`.

### Desktop Shell (`desktop/src-tauri/`)

Rust/Tauri 2.0 layer — native desktop integration. 41 functions across `lib.rs` + commands.

| Area | Key Functions |
|------|--------------|
| **Backend lifecycle** | `auto_install_daemon` (macOS), `spawn_subprocess` (Win/Linux), `kill_backend` |
| **Window management** | `setup_window`, `on_window_close`, tray icon handlers |
| **IPC commands** | `open_file`, `show_in_finder`, `get_app_version`, `check_update` |
| **Platform** | `#[cfg(target_os = "macos")]` guards for daemon vs subprocess mode |

Entry point: `lib.rs::run()`. Compile-time platform isolation via `#[cfg]` — macOS gets launchd daemon, Windows/Linux get child subprocess.

## File Structure Quick Reference
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

```
backend/
  main.py                              # FastAPI entry point
  config.py                            # App configuration
  core/
    session_router.py                  # Session routing + slot management
    session_unit.py                    # Per-session state machine + HealthSensor + HealingLoop
    session_healing.py                 # Self-heal primitives (_arm_recovery_checkpoint)
    lifecycle_manager.py               # Background health + TTL
    session_registry.py                # Component wiring
    context_directory_loader.py        # Context file assembly
    prompt_builder.py                  # Prompt composition pipeline
    skill_registry.py                  # Skill discovery + SkillGuard + tier reading
    manifest_loader.py                 # Skill manifest.yaml parsing (Pydantic)
    mcp_config_loader.py               # MCP server configuration
    swarm_workspace_manager.py         # Workspace provisioning + project CRUD
    proactive_intelligence.py          # Session briefings (L0-L4)
    resource_monitor.py                # System + per-process metrics, spawn budget
    memory_index.py                    # Progressive memory disclosure (L0/L1) + temporal validity (P2)
    recall_engine.py                   # Hybrid search (FTS5 + sqlite-vec), multi-store
    knowledge_store.py                 # Knowledge Library indexing + chunking
    transcript_indexer.py              # JSONL transcript indexing (P1) — parse, chunk, search
    embedding_client.py                # Bedrock Titan v2 embeddings (1024-dim)
    memory_embeddings.py               # MEMORY.md vector store
  routers/                             # FastAPI route handlers
    jobs.py                            # Unified job status/control
    pipelines.py                       # Autonomous pipeline dashboard
    escalations.py                     # Human-in-the-loop escalation
  hooks/                               # Post-session lifecycle hooks
    context_health_hook.py             # Light (every session) + deep (daily) validation
    improvement_writeback_hook.py      # Session lessons -> IMPROVEMENT.md
  jobs/                                # Background job system (product-level)
    scheduler.py, executor.py          # Core scheduler + dispatcher
    system_jobs.py                     # 5 system jobs (code-defined)
    handlers/                          # signal_fetch, digest, memory_health,
                                       #   ddd_refresh (L4.0), skill_proposer (L4.1),
                                       #   bedrock.py (shared client), estimation_learner.py
    adapters/                          # RSS, HN, GitHub, web search
  scripts/
    smoke_e2e.py                       # L2 post-deploy smoke (real session → SSE → cleanup)
  skills/                              # Built-in skills (82+, lazy/always tiered)
  schemas/                             # Pydantic models

desktop/src/
  pages/ChatPage.tsx                   # Tab orchestration + message rendering
  hooks/useChatStreamingLifecycle.ts   # SSE event processing + store subscription
  hooks/useUnifiedTabState.ts          # Tab CRUD + persistence
  hooks/useMessageStore.ts             # MessageStore React integration hook
  services/chat.ts                     # SSE connection + backend API
  stores/MessageStore.ts               # Per-tab message state (single-writer, phase-gated)
  contexts/ExplorerContext.tsx          # Workspace tree + polling
  __tests__/contract/                  # L1 contract tests (real HTTP+SSE fixture server)
    server.ts                          # Lightweight HTTP+SSE fixture server
    fixtures/                          # Recorded SSE event streams
  components/
    settings/                          # 8-tab Settings panel
      SettingsTabs.tsx                 # General, AI, Channels, Skills, MCP, Engine, System, About
      EngineMetricsTab.tsx             # Core Engine growth dashboard (L3)
```

## Output Format Protocol
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

SwarmAI has two fundamentally different output consumers. Format choice is a system-level decision, not ad-hoc.

### The Two Streams

| Stream | Consumer | Format | Rationale |
|--------|----------|--------|-----------|
| **A: Agent-consumed** | SwarmAI agent (self) | **Markdown always** | Token efficiency, git diff, Edit tool, ecosystem unity |
| **B: Human-consumed** | XG / users | **Markdown OR HTML** | Match reader's cognitive mode |

**Stream A files (never HTML):** `.context/*.md`, `DailyActivity/`, DDD docs, CHANGELOG, INSTRUCTIONS.md, any file the agent reads/edits.

**Stream B decision tree:**
- Chat conversation flow → Markdown (don't break flow)
- Report/dashboard/review/scorecard → HTML
- File output (note, doc, reference) → Markdown
- Triggers HTML escalation → `.html` file output

### HTML Escalation Triggers

Output as `.html` file when ANY are true:
1. Output > 100 lines structured content
2. Multi-dimensional comparison (3+ columns × 5+ rows)
3. Data requires spatial layout (architecture, flow, grid)
4. Reader will manipulate it (filter, sort, tab, toggle)
5. Synthesizes multiple sources into one view
6. Will be shared externally
7. Information has visual hierarchy (traffic lights, RAG status)

### HTML Levels (from html-it framework)

| Level | Type | Example | Export Button |
|-------|------|---------|---------------|
| L1 | Static Doc | Pipeline report, comparison table | No |
| L2 | Visual | Charts, SVG diagrams, architecture | No |
| L3 | Interactive | Tab-switch, filter, drill-down | **Mandatory** |
| L4 | Tool | Kanban, calculator, editor | **Mandatory** |

**Rule:** Pick lowest level that serves the content. L1 is default (60% of cases).

### HTML Design Principles (Battle-Tested, Steal-Worthy)

Sources: html-it (robonuggets), frontend-slides (zarazhangrui, 17.3K stars), simonw/tools (210 single-file tools), Lighthouse (Google), LobeChat (77K stars).

#### P1: Serif Headings + Sans Body = #1 Anti-Slop Differentiator

```css
--font-display: ui-serif, Georgia, serif;       /* headings — weight 500, NOT 600 */
--font-body: system-ui, -apple-system, sans;    /* body */
--font-mono: ui-monospace, "SF Mono", Menlo;    /* eyebrow + code + meta */
```

**Why:** Every AI-generated page uses `system-ui` for everything. Serif display type immediately signals "designed with intent." Magazine aesthetic, not wiki. This single choice is the highest-ROI differentiation from AI-slop.

**Verified detail (from Thariq source):** Heading weight is `500` not `600` — lighter feels more editorial. Letter-spacing is `-0.01em` (subtle tightening, not aggressive `-0.018em`).

#### P2: Warm Neutrals, Not Tech-Blue

```css
--ivory: #FAF9F5;   /* background (not #FAFAFA gray) */
--clay: #D97757;    /* accent (warm orange, not #2563EB tech-blue) */
--olive: #788C5D;   /* success (not #16A34A neon-green) */
--oat: #E3DACC;     /* dividers (warm, not #E5E7EB cold-gray) */
```

**Why:** Tech-blue + gray is shadcn/Tailwind default. Warm neutrals + clay accent reads as "editorial, curated" instead of "template, generic." Dark mode keeps warm instinct (orange/amber accents, not neon).

#### P3: Eyebrow Label Pattern

```css
.eyebrow {
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}
.eyebrow::before {
  content: ""; width: 24px; height: 3px;
  background: var(--color-accent); /* clay bar */
}
```

**Why:** Small mono uppercase + clay accent bar above headings is a signature of professional publishing. Costs zero complexity, provides immediate "designed, not generated" signal.

#### P4: Generous Breathing (Report ≠ App)

- Section spacing: `56-80px` (via `clamp(3rem, 2.5rem + 2.5vw, 5rem)`)
- Section dividers: `border-bottom: 1.5px solid var(--oat)` (verified: Thariq uses `1.5px solid var(--gray-300)`)
- Body padding: `48px 32px 64px` (not `16px` app-padding)
- Max-width: `1140px` (tighter reading column)
- Body line-height: `1.55` (not `1.6` — slightly tighter for reports)
- H1 line-height: `1.06`, letter-spacing: `-0.01em` (verified from Thariq source)
- Panel border-radius: `12px` (verified — more rounded than typical `8px`, feels warmer)
- Card hover: `transform: translateY(-3px); box-shadow: 0 10px 30px rgba(20,20,19,.10)` (subtle lift)

**Why:** Reports are read, not scrolled. Generous spacing lets the eye rest between sections. The `1.5px solid` divider in warm color is softer than `1px solid #E5E7EB` — visible structure without harsh lines.

#### P5: Single-File Discipline (simonw/tools validates at 210-file scale)

- ONE `.html`. Inline `<style>` + `<script>`. Zero external deps.
- All assets: inline SVG or `data:` URI. No `/img/` folders.
- The file IS the artifact — email-able, archive-able, works offline, works in 10 years.
- Simon Willison uses this pattern daily with 210+ Claude-generated tools. Proven viable.

#### P7: Two Modes — Editorial vs Functional (verified from deep dive)

| Mode | Use | Typography | Layout | Spacing | Border-radius |
|------|-----|-----------|--------|---------|---------------|
| **Editorial** | L1-L2 reports, scorecards, comparisons | Serif headings (`500` weight), `68ch` body width | Single column, generous sections | 56-80px between sections | `12px` |
| **Functional** | L3-L4 tools, interactive dashboards | Sans everywhere, tighter line-height | Panels, grids, split-panes | 14-16px gaps | `10-16px` |

Editorial = Thariq gallery aesthetic. Functional = simonw/tools aesthetic. Both are valid — pick based on Level.

#### P8: `color-mix()` for Systematic Color Derivation (from simonw claude-code-timeline)

```css
/* One accent color generates all variants: */
background: color-mix(in srgb, var(--accent) 14%, transparent);      /* light tint */
border-color: color-mix(in srgb, var(--accent) 55%, var(--line));     /* medium border */
box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 14%, transparent); /* focus ring */
```

**Why:** No need to hand-define `--color-accent-light`, `--color-accent-hover`, etc. One base color + `color-mix()` = entire palette derived mathematically. Modern CSS (supported in all evergreen browsers since 2023). Reduces token cost of CSS generation AND makes dark mode trivial (just swap base colors, derived colors auto-adjust).

#### P6: LobeChat Artifact Architecture (Phase 3 reference)

For in-chat HTML rendering (future):
- **Side panel** (right, 400-1280px resizable) — not inline in chat flow
- **iframe sandbox:** `allow-scripts allow-forms allow-modals` — **NO `allow-same-origin`** (security: combined with allow-scripts enables removing own sandbox → XSS-to-RCE)
- **Storage shim:** inject polyfill before first `<script>` (localStorage throws without same-origin)
- **Artifact boundary:** custom tag in markdown stream (LobeChat uses `<lobeArtifact>`)
- **Streaming UX:** code view during generation → auto-switch to preview on closing tag

### HTML Constraints (applying the principles above)

- **Single file** — inline `<style>` and `<script>`, zero external deps
- **Serif + Sans + Mono** — three fonts, strict role separation (display / body / meta)
- **Warm neutral palette** — ivory bg, clay accent, oat dividers (see P2)
- **NEVER:** gradients on backgrounds, glassmorphism, icon libraries, animations >200ms, dark-theme-default, "Generated by AI" footer, `system-ui` for headings
- **ALWAYS:** responsive (`clamp()`), print-friendly (`@media print`), semantic HTML, `lang` attribute, eyebrow labels above sections, generous section breathing

### Export Contract (L3+ Non-Negotiable)

Interactive HTML MUST include export mechanism (button or keyboard shortcut):
- Copy as Markdown — for pasting back to agent/docs
- Copy as JSON — for structured data
- Download as CSV — for data tables

**Why:** Without export, interactive HTML is a dead end. The value loop: `Agent generates → Human manipulates → Export → Agent consumes → Next action`.

### Current Application

| Output | Format | Level | Status |
|--------|--------|-------|--------|
| CMHK Weekly/Monthly/Forecast | HTML | L3 | ✅ Correct |
| SwarmAI Monthly MBR | HTML | L2-L3 | ✅ Correct |
| Agent context files | Markdown | — | ✅ Correct |
| Pipeline REPORT.md | Markdown | — | ⚠️ Should be HTML L1-L2 |
| Code Review findings | Markdown | — | ⚠️ Should be HTML L2 |
| Competitive analysis | Markdown | — | ⚠️ Should be HTML L2-L3 |
| Evaluation scorecard | Markdown | — | ⚠️ Should be HTML L2 |

Design: `Knowledge/Designs/2026-05-14-output-format-protocol-design.md`

## Critical Integration Paths
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: -1 | trust: full | promoted: 2026-06-18 -->

Cross-subsystem paths that require E2E integration testing — unit tests per subsystem are NOT sufficient. When modifying ANY subsystem in a row, check if other subsystems in the same row are also being modified. If yes → STEERING #12 applies (independent deploy + per-subsystem smoke).

| Path | Subsystems Involved | Failure Mode | Smoke Test | Automated? |
|------|--------------------|--------------|----|:---:|
| kill → COLD → --resume | SessionUnit + PromptBuilder + Frontend tab state | Silent degradation (cold start instead of seamless resume), stacked dividers, infinite spinner | Force-kill subprocess → verify next message resumes with context (not cold start) | ❌ Manual |
| SSE stream → tab switch → restore | SSE pipeline + MessageStore + TabState + ChatPage | Content loss / WelcomeScreen flash / stale messages | Stream 10 messages → switch tab → switch back → all messages visible + no flash | ⚠️ Partial (L1 contract tests cover SSE parsing; L2 smoke covers stream completion) |
| Send → optimistic → stream → reconcile | ChatInput + MessageStore + SSE + ReconcilePoll | Stuck spinner / duplicate messages / ghost placeholders | Send message → kill backend mid-stream → resume → no duplicates, no stuck UI | ✅ L2 smoke (`smoke_e2e.py` check 5-7: stream → result → state_clean) |
| Self-heal → kill → restart → user sees nothing | HealthSensor + SessionUnit + Frontend grace period | Spontaneous interrupt / error toast during heal / lost context | Simulate high latency (5s per turn) → heal triggers → user sees brief spinner then normal response | ❌ Manual |
| SSE disconnect → subprocess continues → reconcile | SessionUnit + chat.py (flush) + MessageStore + ReconcilePoll | Content truncation mid-tool-call (response incomplete) | Trigger 30s+ tool call → kill SSE connection → verify subprocess continues → 15s poll recovers content | ⚠️ Partial (L2 smoke covers stream completion; disconnect-during-tool not automated) |

**Verification coverage (updated 2026-06-18):**
- **L1 Contract tests** (16 tests, `desktop/src/__tests__/contract/`): Cover SSE event parsing, request shapes, fixture replay. Runs in CI <3s.
- **L2 Smoke E2E** (`scripts/smoke_e2e.py`): 7 checks against live daemon — health, streaming-state, sessions, workspace tree, session persistence, chat stream (full SSE cycle), state cleanup. Runs post-deploy <30s, ~$0.001.
- **L3 Daily Canary** (scheduled job, 03:30 UTC): L2 smoke + eval golden set canary + Slack alert on failure.
- **Remaining gaps:** Path 1 (kill→resume) and Path 4 (self-heal cycle) require process-level manipulation not achievable via HTTP. Needs pytest integration tests with subprocess control.

**Evidence:** COE10 (2026-06-17) — 3 subsystems modified simultaneously on Row 1 path, zero integration tests, 5 P0/P1 regressions. L2 smoke would have caught paths 2+3 immediately post-deploy.

## Architecture Invariants
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

- [principle] **Pipeline reaches the DECISION layer, not just the prediction layer (Ontology 四层尺自审)** — 用 Palantir/Ontology 决策科学的四层尺(预测 Prediction → 推理 Inference → 推演 Simulation → 风险决策 Decision)自审,SwarmAI pipeline 走到了最后一层,且每层可举证:(1) 预测/推理层 = recall / DDD / code-intel graph(知道发生了什么、为什么);(2) 推演层(what-if 多路径) = THINK 阶段强制 3-approaches + 显式 constraint/tradeoff + 落地前 stress-test(`s_autonomous-pipeline/stages/think.md:8,19,46`);(3) 风险决策层(不确定下带取舍地承担后果、全程可审计) = Gate 2 Adversarial Review NON-NEGOTIABLE(`stages/deliver.md:16,23`) + AskUserQuestion 带内人机审批 + `dangerous_command_gate`/`_is_irreversible_external_op`(`security_hooks.py:181,569`),人做最终 disposal。这正是相对"纯知识图谱(能查不能动)/纯 BI(止步预测)"工具的真差异,也是 Palantir Proposal 工作流(AI 提议→人审→系统执行→审计)的同构物 —— 动力学层(Action/Proposal/审计)是我们的强项。诚实短板:语义层仍是散文级(DDD 是自然语言,非可推理 typed graph);唯一真正欠一层 SQL Ontology 的场景是 CMHK 那套硬编码口径的数据 skills。(2026-07-10, source:manual, Learned/2026-07-10-ontology-series-prediction-to-decision.md, mechanisms verified against live code)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [principle] **Fail-open vs fail-closed is a PER-PURPOSE decision, not a global default** — a telemetry/enrichment path may fail-OPEN (don't block ingestion on a scrubber bug); a security-ADMISSION gate MUST fail-CLOSED (`security_scan.py`, RP50). Name the axis before choosing the default — that prevents the RP46 class (wrong default on the wrong path). Ties to the IMPROVEMENT L90 admission-vs-capability-surface split. Negative-control evidence: future-agi's guardrail engine defaults fail-open and was *forced* to build panic-recovery + per-check-timeout machinery as a consequence — the failure-direction choice creates or removes whole classes of required complexity. (2026-07-02, run_b8067f2a, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [principle] **Preprocess-outside-the-scored-unit purity** — keep side-effects (network, embeds, downloads) OUT of the unit being scored/gated; resolve them BEFORE and pass results in. Applies to eval design and any future sandboxed check even without a sandbox. Evidence: future-agi `preprocessing.py` runs all network/ML side-effects before the scoring sandbox, keeping the scored unit pure math. (2026-07-02, run_b8067f2a, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [principle] **Placement decoupling, validated by a negative control** — SwarmAI runs security/quality gates at a DECOUPLED boundary (pre-push/CI, not inside model/agent — "不侵入 model/agent"). future-agi is the live counter-example: it runs guardrails runtime-INLINE on every model request, and the fail-open + panic-recovery + per-check-timeout machinery it must carry is complexity FORCED BY that inline placement. The lesson: an inline gate pays a permanent robustness tax a decoupled boundary never owes — cite this when defending the decoupled+fail-closed choice. (2026-07-02, run_b8067f2a, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- **Prompt cache byte-stability:** System prompt bytes at turn N MUST equal bytes at turn 1 within a session. Our subprocess architecture guarantees this (spawn once, replay verbatim). NEVER add per-turn dynamic content (timestamps, live counters, rotating memory) to system prompt — move volatile data to user messages or freeze on first turn. Violation = every turn cache-misses. (Source: hermes-agent cache fix 2026-05-13 killed 715 lines of "smart" tiered caching because any per-turn mutation breaks upstream prefix matching across all providers.)
- **CONTEXT.md as ubiquitous language:** `Projects/SwarmAI/CONTEXT.md` defines canonical terms for the codebase. Every DDD glossary term has an `_Avoid:_` list of wrong synonyms. Pipeline and skills should use CONTEXT.md terms, not invent new ones. Update CONTEXT.md when a new concept is introduced or an ambiguity is discovered. (Source: mattpocock/skills 76.8K-star pattern — DDD ubiquitous language applied to AI agent prompting.)

## Conventions
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- gh boolean-vs-value flag semantics matter for a shell-parsing security gate: --target/--tag decouple, --prerelease/--latest/--draft are boolean. A gate that parses gh commands must model this or it fail-opens (--target) or false-denies (--prerelease). (2026-07-22, run_81ad1cfe, auto-cultivated)
- Discriminator for future copy-then-adapt: diff the native by SECTION, not by line count — every philosophy section (admission gate, governance boundary, reverse-side, lifecycle) must map to a DDD-version section or be explicitly re-homed with a documented reason (e.g. no MEMORY.md in a DDD). (2026-07-22, run_9bd938d7, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Smallest decouple this session (1 coupling, 1 file) — copy-then-adapt scales down cleanly; the discriminator is always "does the SKILL describe an engine that physically ships?" (2026-07-22, run_de8a44ba, auto-cultivated)
- Decouple invariant greps must exclude comments/docstrings AND cover every SwarmAI package (core/config/utils/jobs), or the guard is blind to the coupling it claims to catch. (2026-07-22, run_c5cafd9c, auto-cultivated)
- A DDD-native skill copied in a PRIOR session does not carry engine files added LATER — the sample DDD (AIDLC) must be re-provisioned after the template gains a subdir, or the flagship copy is silently empty. (2026-07-22, run_c5cafd9c, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A security/severity marker MUST be a first-class stamp-bound field, never a tag: tags are in _STAMP_EXCLUDED_FIELDS (merge-mutable, no re-validation) — a red-line via tags could be silently toggled. THINK caught this and it disqualified the lowest-effort approach. (2026-07-21, run_21490939, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A MISSING entry in a security skip-set (--notes-start-tag absent from _RELEASE_VALUE_FLAGS) is a false-ALLOW, not cosmetic — the value leaks as the gated identifier. Guard completeness with a test that derives truth from the real tool (gh -h). (2026-07-21, run_372d96a5, auto-cultivated)
- When two adversarial specialists CONTRADICT (correctness HIGH false-ALLOW vs security NO FINDINGS on the same input), resolve by LIVE REPRO, never by picking the reassuring verdict. (2026-07-21, run_372d96a5, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-07-21 -->
- RP52 documents a real-world-validated bug class (identity-from-request-body, the most-recurring across 4 COEs); the pattern is only as strong as its propagation to every specialist that reads the range. (2026-07-21, run_fccc6ea5, auto-cultivated)
- An SSOT pattern-count change (RPnn range) is a CONTRACT change — must grep ALL consumers of the range (RP27), not just the obvious files. The load-bearing consumer is the executor that APPLIES the checklist (code-quality.md), not the SSOT itself; missing it makes the new pattern silently never fire. Adversarial Gate-2 check (E) caught this — the same in-SDLC-enforcement lesson RP52 itself encodes. (2026-07-21, run_fccc6ea5, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- When adding a warning inside an existing not-in-LEVELS guard, the guard prevents a warn-storm on the legal default — keep the warn strictly inside the illegal branch. (2026-07-20, run_d49e6518, auto-cultivated)
- A parser that coerces illegal enum input to a safe default MUST warn — silent coercion turns doc pollution into invisible evidence-loss (2 hand-written 'seeded' levels in a DDD doc silently zeroed those sections' maturity evidence for weeks; nothing surfaced it). (2026-07-20, run_d49e6518, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- assess_decay was age+section-evergreen only; judgment must not be buried on a timer for lack of recall (Principle 1) — value-by-TYPE is the retention signal, not the clock. (2026-07-20, run_123652ae, auto-cultivated)
- A comment naming a type as noise (MEMORY A2: PIT fast-churn) can itself be the stale/wrong premise: the [PIT##] entries were always hard-won judgment; the 45d fast-decay over-reacted to VOLUME, which Step 1+2 already fixed at the source — so exempting pitfall is correct and the comment was the bug. (2026-07-20, run_123652ae, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Per my anti-fabrication rule I will **not** invent correctness/security/design findings on code I haven't seen, and I will **not** issue a blanket APP... (2026-07-20, 953297cc-7060-447e-a6cf-6c7107aeb650, decision)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Preserve published-link anchors: mid-task the user flagged the filename was already shared on GitHub. Reverting the git-mv rename + restoring 3 doc references (2 of them live published links, kept at original §-anchors) was correct — a filename is a URL contract once published, upgrade the content behind it, never the name. (2026-07-20, run_44fadfb9, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Projects/* is gitignored (.gitignore:76) — all DDD bindings.yaml + docs live on-disk-only by design; a git add appears to stage but never commits. Verify commit EFFECT (git show --stat), never trust the git add / commit message (CLASS B: I nearly claimed a commit that did not include the file) (2026-07-19, run_f3c876ec, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- 修bug时加的guard本身是新hazard,且常是原bug的镜像:我为防'瞬时IO错误清空cache'加的empty-overwrite guard,gated on records==0,反而把'DDD合法清空domain_skills'误判为瞬时错误→永久保留stale skill(自我延续)——正是原bug(stale until restart)的镜像。Gate2抓到。教训:一个'检测异常→保守跳过'的guard,必须gate在异常的真实信号(scan_failed=iterdir OSError)上,而非结果的表象(records==0),否则合法的'结果就是0'被误杀。 (2026-07-19, run_669e29f6, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- DDD bindings.yaml carries TWO non-cross-referencing schemas: governed_assets (asset-kind, parsed by ZERO code — doc-only until this gate) vs bindings (git-kind, drives code-intel/classify_project). A tool reading governed_assets MUST yaml.safe_load directly — core.ddd_bindings.load_bindings RAISES ValueError on the governed_assets-only shape (CMHK/IVTHub). (2026-07-19, run_df79b8ce, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The validator's quality_gate iterates files_changed — passing it as an int (8) instead of a list crashed the HARD gate with an opaque 'int not iterable'. Stage-record fields that the validator iterates must be typed correctly; a re-published artifact also needs the stage record REPOINTED to the new artifact_id or the gate reads the stale one. (2026-07-19, run_e14e5b25, auto-cultivated)
- O030 applied: classify stream-opened-but-no-completion as the expected SKIP instead of stretching the Phase-4 timeout to race a slow external CREDENTIALS_EXPIRED probe — a timeout is a hang-guard, not a knob to tune against a slow external. (2026-07-19, run_bba97015, auto-cultivated)
- System-level cognition must live in an INJECTED context file — I nearly anchored to CONTEXT.md which is _SYSTEM_REFERENCE_FILES (NOT injected). Verify injection status before choosing where a product definition lives. (2026-07-19, run_b75018ee, auto-cultivated)
- The fix for a rigid classification (A/B/C) is not a better enum but a PARAMETERIZED descriptor (0..N open-kind assets) — extend by adding a value, never a category; this admits the big-tent users the enum excluded. (2026-07-19, run_b75018ee, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- ADD-not-replace (M3 synthesis): kept the browse cases as a labeled name-signal regression-guard class instead of deleting; split the reported number by class (2026-07-18, run_79de25f8, auto-cultivated)
- Durable tell: if a benchmark gold set is a superset of what the system produces, the score is circular — gold must reach for >=1 correct answer the system currently MISSES; enforced by a structural guard test (mutation-verified) (2026-07-18, run_79de25f8, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- → 6 findings, all adopted | CRITICAL: sentinel must ADD to (not replace) the MEMORY (2026-07-18, 94e589d3-c90f-4ca5-a55e-b675b1b8dc75, decision)
- timeout()` suggestion** — **rejected**: it's a *total-duration* timer, exactly the O030 anti-pattern (guillotines a slow-but-progressing restore) (2026-07-18, 94e589d3-c90f-4ca5-a55e-b675b1b8dc75, decision)
- The 5 slicer-drops are a Gate-1-adjudicated head-position-bias tradeoff (test_memory_index.py:861-867), not a bug — change only via formal re-open, never a head-return relabel (C042/C044 trap) (2026-07-18, run_5491ad15, auto-cultivated)
- A test harness that does not mirror PRODUCTION layout hides the fix: my _fresh_mgr used sibling swarm/workspace dirs; the allow-list guard (workspace strictly UNDER swarm_dir, matching prod default) correctly refused it → the test failed until I fixed the harness to match production. The guard failing the test WAS the guard working. (2026-07-18, run_037a02af, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- ddd-scoped tag prevents misreading as system-wide; intent-first gold is unenforced honor-checkpoint (2026-07-18, run_50fad0fb, auto-cultivated)
- Near-miss: almost ran git checkout on a file with UNCOMMITTED real edits to restore a mutation test — that nukes the edits (GUI85). Mutation-test via a /tmp backup copy, never git-checkout, when the file has unshipped work. (2026-07-18, run_644bfea6, auto-cultivated)
- Idle stall-guard via Promise.race needs a per-read settled flag: a timer firing the same turn the read wins still latches abort -> kills a healthy stream next iteration. (2026-07-18, run_da5da0b1, auto-cultivated)
- When adding a client stall/abort to an SSE generator, the escape/cleanup MUST abort the underlying fetch via a component-owned AbortSignal fired in an unmount effect — hiding the UI is not releasing the stream. Assert signal.aborted flips on unmount + parked read rejects -> generator terminates. (2026-07-18, run_da5da0b1, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 CRITICAL: a security/robustness substring guard ('maximum budget') must match the source's EXACT phrase ('reached maximum budget'), not a loose fragment — loose fragments false-positive on unrelated errors. Same family as the denylist->allowlist lesson. (2026-07-18, run_271c39df, auto-cultivated)
- A subprocess exit-1 is not binary fail: a budget-exhausted CLI still returns real partial work + a structured errors/subtype field. Always parse stdout BEFORE judging returncode — the old 'if returncode!=0: continue' discarded both the analysis and the cause. (2026-07-18, run_271c39df, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A scheduled agent_task runs ONE claude subprocess — 'per-project review of N projects' needs a DEDICATED handler that internally loops + spawns a bounded subprocess per project (eval_scheduled precedent: JobSafety.timeout does not bound inner spawns; schedule as a trailing slot to tolerate in-process blocking). (2026-07-18, run_835f82ff, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-07-18 -->
- Empirical-first paid off: I confirmed PRODUCT marketing=1.0==TECH Architecture=1.0 (per-doc) vs shared-corpus Architecture=6.95 >> marketing(not top6) BEFORE coding — the fix was obvious once measured, and it ruled out the wrong fix (boosting domain weight = treating the symptom). (2026-07-18, run_9092cb25, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A delete-and-reseed DB recovery must purge -wal/-shm too — a crash-malformed DB is when a hot WAL exists; leaving it re-corrupts the fresh seed. (2026-07-18, run_2d3417d9, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A guard/regression test needs its own mutation + adversarial pass: my class-guard had 3 real bugs (fallback={null} didnt isolate; only 1 of 6 shell hooks guarded; list-drift check truncated at first inner </ErrorBoundary> via indexOf). A guard that passes first-try is under-tested — inject the exact bug it guards + probe coverage. (2026-07-18, run_7f4388dd, auto-cultivated)
- Loud static source-scan (catches the class at its source) beats silent ErrorBoundary isolation as the PRIMARY class-defense — a silent guard masks the next violation; and isolating a functional gate (BackendStartupOverlay, sole isBackendReady flip) is fatal (swallowed crash = permanent boot hang). Isolation is defense-in-depth ONLY, on passive components. (2026-07-18, run_7f4388dd, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-07-18 -->
- When a fix changes a shared function's return shape, grep ALL consumers and confirm each handles the new shape — the F1 root-coverage guard changed detect_package_roots output, and run_multi_package (raw name) vs build_packages_partition (disambiguated) drifted into a root/member name collision that clobbered output_path. (2026-07-18, run_a9fe5ad3, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Adding a mutation to a function documented as PURE silently breaks its contract: finalize_v3 said never-mutates-input but a shallow list copy shared caller dict refs; deepcopy required. Any mutation added to an assembly fn must re-check the purity docstring. (2026-07-18, run_97a6b1db, auto-cultivated)
- The audit that PROVES a loop works must probe each stage on LIVE data, not restate what the session built — my first grep for cultivated entries returned 0 and I nearly reported a false bug; verifying the effect (R16b) showed cultivation WAS working, my search string was wrong. (2026-07-18, run_97a6b1db, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- M3 + Gate-1 both sharpened the fix: helper must be PURE (no FS I/O) so dir-validation stays in callers → keeps the working path-cache byte-identical (8/8 live-verified). Coupling FS I/O into the helper would have regressed the cache. (2026-07-17, run_19eecc9f, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- C read is a bare identifier (php-FP-class): reused reader_exclusion_parent_types + member_access_types={field_expression} — field_expression guard is load-bearing for leading-const CFG.x, NOT s->MAX (Gate-1 corrected my rationale, verified live) (2026-07-17, run_078cf907, auto-cultivated)
- The pre-existing inert qualifier_gate field (stored but never checked) was designed exactly for this — wiring it beat adding a c-specific branch (reuse over new mechanism, STEERING #2/C042) (2026-07-17, run_078cf907, auto-cultivated)
- qualifier_gate must match type_qualifier.text==const, NOT node-presence — volatile/const/const-volatile are all type_qualifier nodes; M3 skeptic proved the volatile FP live before code, saving a silent false-edge (parser.py _passes_qualifier_gate) (2026-07-17, run_078cf907, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-07-17 -->
- Detection is ASYMMETRIC and that must drive the UX: ~/.ada|~/.midway present = confident internal, but absence != external (fresh employee machine). So external is a defaulted-overridable state (one-click toggle), never a confident auto-decision. Dropped ~/.toolbox (Gate-1 FIX-E: generic name, false-positives). (2026-07-17, run_55984e9a, auto-cultivated)
- Kiro/MeshClaw was the right thing to check for a detection pattern — and the answer was that it has NONE: it assumes 100% internal (mwinit->kiro-cli->amzn SSO). That VALIDATED our own design (confident-internal-detect + external-default + toggle) rather than giving one to copy. Checking a benchmark can confirm you must build your own. (2026-07-17, run_55984e9a, auto-cultivated)
- The _get_name defect is now a 2-time pattern (php/swift/kotlin flat-set-miss, then c/cpp declarator-nesting): both are the same kernel — a name-node reachable by a path the resolver does not walk. c/cpp is the harder variant (nesting, not just a missing type). The fix generalized cleanly: c/cpp-scoped descent + fall-through preserves the flat path for the other 11. (2026-07-17, run_88512360, auto-cultivated)
- codegraph reference paid off precisely: the declarator-BFS-skipping-parameter_list pattern (their documented std::string TableFileName→string bug) is exactly the trap our naive scan would hit. Reading the actual reference source (not just adopting the idea) surfaced operator_name/destructor_name/operator_cast as name-WRAPPER nodes needing whole-text extraction — 3 C++ forms I would have dropped. (2026-07-17, run_88512360, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Mutation-verify FP guards specifically: reverting reader_exclusion_parent_types → php local-var + namespaced tests go RED. A false-positive-suppression guard is exactly the kind of code that silently no-ops if the test is vacuous. (2026-07-17, run_d021ce39, auto-cultivated)
- php uniquely REUSES its `name` node type for a const read AND the inner leaf of $variable_name / qualified_name — a class of false-positive (local var, param, namespaced ref) that swift/kotlin (distinct simple_identifier) never hit. Lesson: when a language reuses one node type across read/non-read roles, a reader guard needs a parent-type exclusion, not just a member-access guard. Generalized as LangValueSpec.reader_exclusion_parent_types (clean, defaults no-op). (2026-07-17, run_d021ce39, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- R16b applied BOTH directions this run: refuted a review-stage adversary HIGH (claimed python attribute node has no attribute field — FALSE, observed it does) AND confirmed a Gate-2 HIGH (ruby Foo.new false edge — TRUE, observed it). A sub-agent claim about grammar/behavior is an input to verify by observation, never a conclusion. (2026-07-17, run_13667da9, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- GUI80 recurred in real time: git checkout <file> to revert an in-place mutation-test NUKED uncommitted Change 2 (value-ref). Self-caught via grep -c immediately. Rule reinforced: never git checkout a file holding unshipped edits — commit BEFORE any mutation testing, or restore via targeted string-replace, never checkout. (2026-07-17, run_423bb21d, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Auto-generated code that will be enforced must be born FAIL-OPEN + UNWIRED. The scaffolded gate exits 0 (allows all) and is invisible to gate-discovery (no dir-scan — verified) until a human completes AND wires it. This makes an incomplete/half-edited auto-artifact inert-by-construction — the safe default for any 'agent scaffolds, human completes' pattern. (2026-07-17, run_90b8aeed, auto-cultivated)
- P7 (build-a-gate) does NOT forbid auto-scaffolding an ALREADY-HUMAN-APPROVED gate stub — the discriminator is enforcement power, not file-writing. P7 forbids the MODEL deciding what to enforce and enforcing it. A fail-open, unwired stub after human-accept is secretarial (removes blank-page friction); the human still writes match-logic + wires it = disposes. Over-reading P7 as 'never autonomously write a gate file' was the over-cautious framing the skeptic corrected. (2026-07-17, run_90b8aeed, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 HIGH was a real fail-soft breach: _cargo_workspace did (data.get(workspace) or {}).get(members) — but workspace could be a non-dict string, and that .get() is AFTER the try, so AttributeError escapes. fail-soft claims need an isinstance guard on EVERY nested manifest access, not just the top-level parse. (2026-07-17, run_693e08de, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- C042 tell fired correctly this time: XG asked to promote important→critical (2-word change); investigating first revealed the 2-word version was false-confidence and the real fix reused an existing probe pattern (verify-native) — neither a bigger mechanism nor the naive minimal change, but the RIGHT minimal change. (2026-07-17, run_494094ec, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A pure-function that swallows a loud ValueError (extract_entry_anchors raises on unbackfilled routes) turns a fail-closed guard into a vacuous pass — never catch-and-return-empty in a coverage/safety check; surface the raise as an error (2026-07-16, run_94e5a5aa, auto-cultivated)
- SAME-RUN CLASS-A REPEAT: I built a coverage gate and left my OWN hole TWICE — reason=. rubber-stamp (Gate-1) then len>=12 gameable by xxxxxxxxxxxx (Gate-2). A self-authored anti-gaming check whose felt-completeness masks a trivial bypass is the authorship trap; the durable fix for any anti-junk gate is word-count + character-diversity, never length alone (2026-07-16, run_94e5a5aa, auto-cultivated)
- The v3 coverage defect was framed wrong by me (route-% threshold) — Gate-0 reframed it to an anchor-ACCOUNTING invariant: every anchor must be classified OR reasoned-unclassified; % thresholds reward gaming (pad trivial routes), accounting forbids silent omission without forcing fake flows (2026-07-16, run_94e5a5aa, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Referential-integrity (does entry_ref resolve?) never catches semantic mismatch (does it resolve to the RIGHT handler?) — flow entry_ref pointed at POST create_file while mermaid+steps described PUT put_workspace_file; dog-food on real data surfaced it, fixtures never would (2026-07-16, run_3026ef31, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- String-shape guard vs external-tool flag semantics: LIVE-PROBE the tool for the exact accepted-value set (PIT16), do not reason from one spelling (PIT14) (2026-07-15, run_900bb839, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- KNOWLEDGE ≠ MEMORY in decay economics: MEMORY accumulates disposable operational churn (GUI/PIT) that SHOULD decay; KNOWLEDGE is almost entirely load-bearing reference. So KNOWLEDGE_EVERGREEN_SECTIONS must protect nearly EVERY section by name — the capability exists+safe but correctly fires on ~nothing in current content (live SMOKE: 0 stripped). (2026-07-13, run_a1ec08e7, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- stale-artifact-repoint gotcha: re-publishing a review artifact does NOT auto-repoint the stage record — must pass the new artifact_id in run-update --stage-json or advance reads the old (checked=0) artifact (2026-07-13, run_f1935433, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A security-fix mutation test must assert BOTH halves: escaping-dropped goes RED when the guard is removed AND internal-kept stays GREEN — the internal-kept assertion is what catches an over-broad drop-all-symlinks regression (Gate-1 W2). (2026-07-13, run_0e5f1969, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Security fixes must state residual risk explicitly: a guard that NARROWS an attack is defense-in-depth, not a fix — mislabeling it rebind-safe is worse than an honest partial (false sense of security). (2026-07-13, run_cd11637a, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Partial pipeline --stage-json updates REPLACE the stage record, they do not merge — a {stage,status,artifact_id}-only update dropped build.status to None and re-tripped the stage-order gate. Always write the COMPLETE stage record in one run-update. (2026-07-12, run_473a0b7c, auto-cultivated)
- A blind [:N] head-truncation to control LLM token cost is a silent data-loss blind spot when the load-bearing content lives in the file TAIL (memory_health Phase2 fed full_memory[:8000], but ## Open Threads sits at char ~328K of a 333K file → resolved_threads was structurally always empty, not by judgment). Fix = append the specific evergreen tail section, but CAP the append too (every sibling read was [:8000]-capped; an uncapped append is the next unbounded input). PIT176 family. (2026-07-12, run_473a0b7c, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- R16b/Gate-0 paid off at EVALUATE: the reported root cause (binary UnicodeDecodeError 400) was FALSIFIED by reproducing _resolve_file_path — the observed /private/tmp 400s hit the outside-home guard, a different branch. Reproducing the exact logged path before scoping saved fixing the wrong branch. (2026-07-12, run_46e7b94c, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- R9 pytest guard repeatedly blocked compound `pytest ... > file; grep file | tail` lines — the fix is to run pytest alone (redirect to file) then Read the file in a SEPARATE step, never chaining a tail/grep after pytest on the same invocation. (2026-07-12, run_f5ab71b5, auto-cultivated)
- B stability fix mirrored the existing expandedPathsRef pattern already in the file — copying a proven in-file discipline beat inventing one (same lesson as the prior run mergeExpandedChildren). (2026-07-12, run_9db46483, auto-cultivated)
- Reusing a shared modal (unsaved-warning) for a NEW trigger (reload) inherited the WRONG forward action (close). When reusing a UI affordance for a new caller, verify its continuation matches the new caller intent — same class as O030 (reused guard, wrong context). (2026-07-12, run_9db46483, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- R29: a pre-existing test failure (test_initialization_idempotence, job-config seeding) was verified to fail WITHOUT my change via git stash — do not own another session failure; my diff was a single non-overlapping hunk. (2026-07-12, run_945fecff, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- R25 same-class scan paid off in ③: gating only hljs.highlight would have left computeLineDiff + findAllMatches (both O(n) sync on the same content) still freezing the UI. One shouldProcessSync guard covers all three siblings. (2026-07-12, run_500b576e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A recall-neutrality test battery MUST include the exact query shape whose match surface is being removed. My 20-query battery omitted PURE bare-date queries — so it read 0 loss while Gate-2 found 4/6 live bare-date queries regressing to empty (F1 HIGH). (2026-07-12, run_2f4d92da, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- R7 same-class scan on a metadata-field add must grep EVERY writer of that file: create_project got the stamp but _ensure_default_project (the SwarmAI-default project's .project.json writer) also builds metadata and would have been the one project missing it. Two writers, one field, both must stamp. (2026-07-12, run_0b5099d2, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Removing one field from a schema is NOT symmetric with removing its sibling: version_set (kept) had a live consumer (s_internal-crux-cr line 42) plus live data, while code_intel had zero consumers. Gate-1 skeptic caught the false symmetry before code -- always grep each field independently for consumers, never batch-judge a schema cleanup. (2026-07-12, run_f8ef133b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- [pitfall] **A collision guard must key on the winning track ACTUALLY being present, not a raw substring heuristic that can suppress-without-replace** — Gate-2 HIGH: the first deck-suppress fix suppressed deck whenever a web qualifier appeared ANYWHERE, so a topic-mention like "网页设计的ppt" killed deck without emitting html_deck — silently dropping the user's format. Precise fix: suppress deck ONLY when html_deck GENUINELY fired (html_deck in detected + iterated first); the ordered-detection guard IS the collision fix, no separate qualifier scan. (2026-07-03, source:proposal_1b2025af)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- Full-bleed SVG background (no self-rounding) is the right pattern for a logo consumed by multiple container shapes (rounded-md 26px / rounded-full 48px / rounded-2xl overlay) — let each container clip, verified by rendering at all 3 sizes before trusting. (2026-07-12, run_70ad47bc, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- F004 placement held: put the single-entry-point declaration at line <20 (before the decision point), not EOF — the whole reason the anti-rationalization-at-EOF pattern failed originally. (2026-07-12, run_7a5b35a1, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- no-repo is a first-class shape, not an edge case: many projects are pure DDD docs with no bindings.yaml; the lifecycle must explicitly say BIND/DEVELOP/SYNC-BACK are N/A and CREATE is the whole story, or an agent invents a repo that doesn't exist. (2026-07-11, run_af0b6f8b, auto-cultivated)
- Scope a rule to where it's true: 'agent never git push' is correct for INTERNAL (CRUX auto-merge owns remote) but WRONG for external github-pr (public git, agent may push). The old flat rule would have blocked legit external PR delivery — Gate-2 confirmed the never-push is now internal-scoped. (2026-07-11, run_af0b6f8b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Vendored Amazon KBs (crux/brazil/analyzer) into the gitignored s_internal-* dirs = self-contained skills that never depend on ~/.kiro at runtime AND never ship public. Projection reads filesystem not git, so gitignore has zero runtime cost. (2026-07-11, run_ce2a1b6d, auto-cultivated)
- git mv keeps a file TRACKED under the new name — for a leak-close you must ALSO git rm --cached; renaming alone would have shipped s_swarm-cr public under a new name. Verified via git cat-file -e HEAD:. (2026-07-11, run_ce2a1b6d, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Mutation-tested the new regression guard (removed the 3 setdefaults → test went RED name-not-backfilled → restored GREEN) so it is provably non-vacuous, not test-theater (RP47). The pytest_command_guard gate also fired correctly when I piped pytest through grep — defense-outside-the-agent working as designed. (2026-07-11, run_3467799d, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- [guideline] **Deep-module design vocabulary (adopt verbatim; source: mattpocock/skills `codebase-design`, full analysis `Knowledge/Reports/2026-07-12-mattpocock-skills-deep-research.md`)** — a shared, enforced vocabulary for design/review so agents don't drift into "component/service/API/boundary". **Module** (scale-agnostic: fn→class→package→tier). **Interface** = *everything a caller must know* — types + invariants + ordering + error modes + perf, NOT just the signature. **Seam** (Feathers) = a place you can alter behaviour without editing there. **Adapter** satisfies an interface at a seam. **Depth** = leverage = behaviour-per-unit-of-interface-learned (deep = small interface, lots of impl). **Leverage** = caller payoff; **Locality** = maintainer payoff. Executable heuristics: **the deletion test** (delete the module — if complexity vanishes it was a pass-through; if it reappears across N callers it was earning its keep) = the concrete probe our R25 "fix adds net complexity → wrong layer" lacked; **"one adapter = hypothetical seam, two = a real one"** (no port until something varies across it — kills speculative indirection); **"the interface is the test surface"** (if you must test *past* the interface, the module is the wrong shape). Dependency→test-strategy map: in-process (test directly) / local-substitutable (PGLite-style stand-in) / remote-owned (ports&adapters + in-mem test adapter) / true-external (mock). (2026-07-12)
  <!-- ref:0 | last:none | decay:active | source:manual -->
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-1 plan-skeptic caught 2 pre-code blockers that would have wasted BUILD: (a) auto-open guarded by React `tabs.length===0` spawns TWO shells under StrictMode because the snapshot is stale within a commit — must guard on the LIVE store `count()`; (b) spawn_blocking over a tokio async Mutex won't compile — needs `blocking_lock()` (sanctioned off-async-worker) + JoinError double-Result handling. Both verified against source before adopting. (2026-07-11, run_1ad103f1, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- An accidental git stash pop mid-BUILD injected merge markers into session_unit.py + hook_builder.py (parallel-session files). Restored to HEAD (my run only touched artifact_cli.py) — lesson: never `git stash push <file>` mid-pipeline when old stashes exist; the pop can conflict-corrupt unrelated files. (2026-07-11, run_4db42c78, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- distinct-runs-per-category is not volume normalization — correctness fires ~every run so it saturates to N-of-N (the label-variance mode the design was meant to defend). Real signal = deviation from a category baseline OR HIGH-first-pass-only, never raw frequency. (2026-07-11, run_e505350f, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- git-stash across parallel sessions is a HAZARD (R29): `git stash push <files>` then `git stash pop` popped stash@{0} which belonged to ANOTHER session (a RECOVERED stash), causing conflicts in files I never touched (hook_builder/session_unit). Restored those 2 to HEAD, preserved the other session's stash intact. Lesson: prefer NOT stashing to verify-on-clean-HEAD when parallel sessions exist; if you must, pop the SPECIFIC stash@{N} ref, never bare pop. (2026-07-11, run_bf4cb46e, auto-cultivated)
- Reproduce-in-isolation was decisive: I originally told the user this was 'test-ordering pollution (fails after sse suite)'. Running TestChatStream ALONE = 221s/3 errors proved it's NOT ordering — TestClient's own lifespan copytree. Never diagnose ordering from 'appeared after X' without the isolation control. (2026-07-11, run_bf4cb46e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Re-observing settled it: re-running the smoke 2x (deterministic still_streaming=True) + reading the daemon LOG showed 'Chat stream cancelled' firing WITHOUT the matching 'transitioned STREAMING->IDLE' line — that log-diff is what proved recovery was no-opping. Observe (logs/live), don't infer from the stale last_error string (R16b). (2026-07-11, run_1c0a1da5, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Idempotency pivot: derive the updated: field from stable lastEditedAt||createdAt, never today(), or every sync run self-drifts. (2026-07-10, run_9290f4ff, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A test that greps SOURCE TEXT (assert getsize in src) is lint, not a behavioral proof — pair it with a test that drives the real function and mutation-verify the guard has teeth. (2026-07-08, run_8debb0fe, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Recovery evidence must be a SPECIFIC self-heal event (force_unstick / recovery_checkpoint_armed), never a generic transition (to=cold) that also fires for routine reclaim — a generic marker as recovery-proof is an absolve-everything hole. (2026-07-07, run_67a391a4, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- DM vs group asymmetry: threading is group-only; a 1:1 DM must stay top-level — scope the fallback by chat_type at the gateway (which has it), never in the adapter (which does not). (2026-07-07, run_45187d49, auto-cultivated)
- thread_ts routing: a top-level group @mention has NO thread_ts; the bot must root a reply-thread under the user message ts, and key the session on that SAME ts, or the user next in-thread message keys a different session and thread_follow never re-engages. (2026-07-07, run_45187d49, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- is_connected()==False cannot distinguish 'stalled' from 'healthy-reconnecting' at an instant (slack_bolt swaps current_session only AFTER the new socket connects) — so a stall detector MUST use a SUSTAINED window (minutes), and must NOT arm before the first-ever connection (cold-start reads as disconnected). (2026-07-07, run_eb503e1e, auto-cultivated)
- A liveness check (is_alive/pid-alive) is NOT a connectivity check: a thread/process can be alive while its work is permanently stalled — the recovery must probe the actual capability (is_connected), not the container. Same class as the busy-vs-broken / waiting-input hang bugs. (2026-07-07, run_eb503e1e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- bolt SocketModeHandler already authenticates block_actions payloads, so a signed crypto nonce was over-engineering (Gate-1 simplicity) — a state-based replay guard (pending_id match + resolved-status + TTL) is simpler and sufficient. (2026-07-07, run_6038cd2c, auto-cultivated)
- Gate-1 correctly BLOCKED: an owner-invariant stated as prose (never displace index 0) is not enforcement — it needs append-only code plus an assert; the skeptic proposed the exact fix. (2026-07-07, run_6038cd2c, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-07-07 -->
- Observe A/B store-inject decouple + fail-closed-at-WRITE (PUBLIC/unknown never stored) is stronger than read-time refusal — same assembly-time-exclusion philosophy as L3 private lane. Poison cannot reach B or future C because it never enters the store. (2026-07-07, run_84cb2ea3, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Test-harness drift is a silent privacy hole: _simulate_build (E2E) had ONLY the is_group branch — a stale copy of prod that NEVER exercised the non-owner-DM exclusion, so the leak was invisible to E2E for however long. Synced the harness to mirror prompt_builder exactly. A test harness that re-implements prod logic must be kept byte-aligned or it tests a divergent thing. (2026-07-06, run_20bd4a7b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- GUI28 held live: nearly ran git checkout <file> to undo a mutation-test edit, which would have nuked ALL unshipped edits; undid via targeted Edit instead. The pytest|pipe guard also fired correctly, preventing a compound-command hang. (2026-07-06, run_8f96def2, auto-cultivated)
- Gate-1 corrected a false premise: file_lock.py was assumed '0-callers/dead' but is the standard in-tree pattern — eval_service._persist_golden_set is a directly-copyable flock precedent. Verify a reuse target's real caller graph before framing it as risky. (2026-07-06, run_8f96def2, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- validator fail-closed on a note-only MED without a confidence field — a MED marked resolved:false MUST carry confidence<7 to be treated as note-only, else it fail-closes as a blocker. Lesson: always stamp confidence on unresolved findings you intend to defer. (2026-07-06, run_dfd2cb3e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Pipeline run-create without --profile leaves profile=None; GC12 blocks setting it retroactively. Always pass --profile on run-create for non-full runs. (2026-07-06, run_7fa9aa63, auto-cultivated)
- Per-section privacy classifier must be affirmative-opt-in tag scan (untagged=private, C041), never LLM/content-regex. (2026-07-06, run_7fa9aa63, auto-cultivated)
- A lazy dotstar plus DOTALL regex that runs on EVERY tool call is a ReDoS surface: N unterminated openers give O(n squared). Cap the trigger count (fail-safe: skip then over-deny, never fail-open) rather than trusting the regex to be linear. (2026-07-06, run_3bde4b8b, auto-cultivated)
- uv/poetry/pdm run script.py execute a non-executable .py and are realistic in a uv-based repo — any exec-position anchor must include them, not just python/bash. The security specialist found this by reading the repo's OWN CI config (which uses uv). (2026-07-06, run_3bde4b8b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-1 caught a FATAL silent deadlock BUILD would have shipped: gating a biweekly on JobState.last_run deadlocks because the executor rewrites last_run on every result incl. a skip → each weekly skip resets the clock → runs once then never again. The fix (own timestamp file, written ONLY on a real run, never on skip) is the general pattern for any every-N-period gate riding on a more-frequent cron. Signature to watch: a skip-result that flows through the same state-update as a real result. (2026-07-06, run_6980cb35, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Trend comparability must be by COVERAGE (scored_count/total_cases) not trigger-name: manual-full matched 0 runs; canary is programmatic-only and would sawtooth the line. Discriminator = data shape, not label. (2026-07-06, run_0e29db9a, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Adversarials review reviewers too: the reviewer suggested <= which would have made the test vacuous (passes on the revert it must catch); kept strict < + vacuity guard instead. (2026-07-04, run_bdb6a095, auto-cultivated)
- When replacing a frozen threshold with a discriminator, guard the degenerate case explicitly: strict tests-OUT<tests-IN would false-RED if the excluded set (core-internal tests) were emptied — added a vacuity guard pointing at the test, not the metric. (2026-07-04, run_bdb6a095, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Extracting a shared HELPER (readable_source) beats extracting bare CONSTANTS — it collapses both the data (label map) AND the duplicated mapping logic into one place, so the .get-if-in-set expression can never drift either. (2026-07-04, run_cda1e759, auto-cultivated)
- Option A drift risk closed cleanly: a byte-identical duplication is the SAFEST refactor — a behavior-preserving test (readable_source == old inline expression across 6 cases incl unknown-feed + empty) locks equivalence, and the single-source grep test prevents re-duplication. (2026-07-04, run_cda1e759, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A stable sort by the write-time ranking key (final_score) is IDEMPOTENT on already-sorted data — this is what let the shared denoiser preserve Welcome byte-for-byte while fixing Slack. Verified the write-time sort at signal_digest.py:570 before trusting the claim (Gate-1 F1), rather than assuming. (2026-07-04, run_44342b40, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Code identifiers must survive translation as English: evaluator names (canary_pass...), filenames (golden_set.yaml), cron (30 4 * * 1), BVT/code_digest stay verbatim in the ZH SVG — translating them would break the map to source. Gate-2 explicitly checked none were translated. (2026-07-03, run_b39cccd9, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A drift-fix must be ASSERTED, not left to pass on a loose regex: the old guide test matched /12:30|lunch|weekday|.../ which would still pass on the stale 'Weekdays' text. Fixed the test to require Monday present AND forbid the exact stale 'Weekdays 12:30' string — the drift can't silently return. (2026-07-03, run_d9cf5887, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Skill description stage-count must match ## Stage headers in INSTRUCTIONS; stale count recurs on stage insertion (STRATEGIZE added, count stayed 8). Grep sibling flow-arc lines too - line 400 arc had dropped TEST. (2026-07-03, run_2e26492f, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-0 skeptic's highest value was correcting a LAZY mapping, not catching a bug: my 'all 5 capability -> code_aware' was one-bucket laziness; the skeptic forced per-subsystem homes (compliance/memory/loop_active). The established convention (canary probes distribute by subsystem) was discoverable — I should have checked it before proposing. (2026-07-03, run_a04ab388, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Mirror an existing proven guard verbatim (same field, same literal, same fail-SHOW direction) rather than re-deriving — keeps the two sites from drifting. (2026-07-03, run_9b05c1a1, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The load-time taxonomy guard (warn-not-raise) is the durable win: it now catches off-canonical dimension/category drift at load, so a future mis-tag surfaces immediately instead of leaking silently into /health. (2026-07-03, run_8c44b7bf, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- An attention queue must fail-SHOW on uncertainty (null/absent pauseKind -> still show the item), never fail-hide — silently hiding a possible real decision is worse than a redundant card. (2026-07-03, run_3d61db5b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- GUI15 recurrence AVOIDED-then-COMMITTED: I used `git checkout` to restore a mutation test on a file with UNCOMMITTED fixes → it wiped the real edits (had to re-apply all 3 from scratch). The rule (never git-checkout a mutation-test on uncommitted work; reverse the exact edit instead) — I violated it once, caught it via the system file-changed note, re-applied, and did the SECOND mutation test correctly via reverse-edit. Cost: ~10min re-work. The lesson is now doubly earned. (2026-07-03, run_8838890d, auto-cultivated)
- Two doc bugs surfaced by real rendering, not review: design.md <link> only carried CJK (Latin fell back), and track-e2 said <section class=slide> when deck-stage uses slotted [data-deck-active]. Both only visible by rendering + reading deck-stage.js, never by reading design.md alone. (2026-07-03, run_c1dd1173, auto-cultivated)
- Idempotent backfiller = safe to re-run: linked_families() drops already-present families so a second --apply is a no-op. When a tool mutates shipped assets, idempotence is the guard against double-corruption on re-run. (2026-07-03, run_c1dd1173, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A health probe that ONLY has a session-independent heartbeat (memory_sample fires for ALL live sessions incl wedged) cannot use that heartbeat as a per-session progress signal — it both false-positives (invisible in a 2s window) and false-negatives (masks a real wedge). Progress must key on REAL turn events, excluded of housekeeping. (2026-07-03, run_6b10ea1c, auto-cultivated)
- Gate 0 (diagnose-before-build) paid for itself: BOTH user premises were wrong — bug2 was NOT a prod bug (in-process job means os.getpid()==daemon; only standalone python -c mismeasures) and bug1 was NOT a wrong-log bug (handler already reads backend-daemon.log; real mechanism was the ~60s memory_sample heartbeat cadence vs a 2s progress window). Verifying premises against live system before coding reframed the whole scope. (2026-07-03, run_6b10ea1c, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A deletion audit MUST end with an adversarial skeptic that tries to REFUTE each deletion — the first audit had 3-4 delete candidates; only 1 (confidence_score.py) survived. Deleting on the first audit would have removed the sole cross-track consistency gate + the sole narrative AI-slop detector. (2026-07-03, run_bad39e8a, auto-cultivated)
- Config-vs-template distinction (PIT14 inverse): the 2 disabled feeds live ONLY in the workspace config.yaml; the source _DEFAULT_JOB_CONFIG template never contained them, so NO template sync was needed. Check whether a provisioning template seeds the value before assuming a same-fix-two-places obligation. (2026-07-03, run_b579f702, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Verified (R16b) a skeptic caution before accepting it: the 2400s-wall-doesnt-apply claim was a job-type mixup — os-eval-biweekly is type:script (subprocess timeout applies), not the in-process eval_scheduled path. Even adversarial findings get observation-checked, not reflexively actioned. (2026-07-03, run_9fdb8ad5, auto-cultivated)
- A RED test mid-BUILD exposed a real design flaw, not a test problem: passing read_timeout first silently dropped the 2026-06-28 auth-self-heal. Fixed the DESIGN (fail-fast keeps the one retry via FRESH throwaway clients), not the test — the Debugging-Rule + fixing-root-not-symptom in action. (2026-07-03, run_9fdb8ad5, auto-cultivated)
- Gate-1 plan-skeptic saved this run from an over-built wrong fix: my A+B (fork the 16-caller client cache + concurrency) would have touched shared mutable state under all callers and had an A-vs-B retry tension. The skeptic pointed to an EXISTING sanctioned pattern (auto_refresh.py:904 throwaway client) that root-causes the hang with zero shared-state risk. Reach for the pattern already in the repo before inventing a mechanism (the C042 anti-pattern). (2026-07-03, run_9fdb8ad5, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Geology-first paid off: WS1 thumbnails had ~4 role-derivation bugs (multi-bright palettes, dark-scheme saturated bg) that only surfaced by RENDERING all 34 + viewing a contact sheet, not by reading code. The test (contrast>=3 for all 34) + override table for the un-derivable systems is the durable guard. (2026-07-03, run_4e1ed63e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A converge/narrow change must flip the OLD test assertion (shows-ALL-statuses → shows-ONLY-active) and mutation-verify it goes RED on revert — a test that only checks survivors, not drops, would silently pass a broken filter. (2026-07-03, run_820a4732, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- DoD honesty: DoD2 (anthropic feed returns 200) turned out MOOT — the live config had no dead anthropic link (my premise came from stale KNOWLEDGE, not the live config). Verified-moot beats forcing a fake fix. And WS4 TL;DR is prompt-shaped, not code-enforced — Gate-2 correctly made me phrase the DoD as prompted-to-include, not guaranteed. (2026-07-03, run_bf840159, auto-cultivated)
- Editing a provisioning TEMPLATE (_DEFAULT_JOB_CONFIG, if-not-exists) only helps NEW workspaces — the live runtime config already exists and is never overwritten. A config change that must take effect NOW requires editing BOTH the source template (R10 correctness) AND the live runtime file. Two different git repos (code vs workspace). (2026-07-03, run_bf840159, auto-cultivated)
- The rank bug was NOT missing signals but a min(x,1.0) CAP that crushed the top band — tier_weight WAS applied, but clamping to 1.0 made a frontier 0.85×2.0=1.7 tie with an eng 0.92, and urgency/freshness never entered the sort at all. Fix: keep the capped relevance_score for DISPLAY, add a SEPARATE uncapped final_score as the RANK key. Dogfooding on the real 50-item sample proved it (spread 0.30→0.93) before commit — a metric gate must run against live data, not a fixture. (2026-07-03, run_bf840159, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The trade-off inverted cleanly: local bundling optimized for offline (a case that never happens — SwarmAI is online by default) at the cost of font fidelity (italic axis + CJK faces, which ALWAYS matter). When a constraint ("no dependency") is interpreted as its most extreme form ("zero network"), re-check it against the actual runtime reality before paying a fidelity cost for it. (2026-07-03, run_68176c82, auto-cultivated)
- Re-fetching upstream VERBATIM (then diffing all N against a fresh fetch = 0 differing) is a stronger restoration than hand-editing: it is deterministic and idempotent. When a Gate-2 skeptic hand-reconstructed 3 files after a git-checkout mishap, re-running the deterministic re-fetch overwrote the reconstruction and proved byte-identity — trust the mechanical restore over any hand-repair (GUI07). (2026-07-03, run_68176c82, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A redesign that REMOVES a surface must delete its dead code in the same commit — the orphaned JobsBar (0 render sites, stale docstring claiming it was used) misled the user into thinking the feature vanished by accident. Dead code with a lying docstring is worse than no code. (2026-07-02, run_06b89c00, auto-cultivated)
- A no-rerun security guarantee is best pinned by an invariant test on the STRUCTURAL fact (approve_command called only on approve -> denied cmd never cached), not by trying to test the model wont retry. Mutation-proven (cache-on-deny -> RED). (2026-07-02, run_ec351cc9, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Empty-string consistency across a mapper (?? null lets "" through) and its render guard (item.lastError && falsy on "") must be checked together — Gate-2 confirmed they agree; a mismatch would have shown an empty reason line. (2026-07-02, run_f1a9b1ab, auto-cultivated)
- Mirroring an existing sibling UI pattern (the waiting variant muted-question line + paused reason-context) made the 4-hop wiring mechanical and low-risk — no new visual language, no new click semantics. (2026-07-02, run_f1a9b1ab, auto-cultivated)
- enabled-gap is a recurring class (run_01d2fd9d frontend, this run backend unified_status) — filtering job aggregates by enabled must be applied at EVERY count site. (2026-07-02, run_14d01964, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Mutation-testing a visibility filter (remove the guard clause, confirm the exclusion test goes RED) is the cheapest proof the test has teeth and is non-vacuous. (2026-07-02, run_01d2fd9d, auto-cultivated)
- Fail-open is the correct default for a VISIBILITY filter: hide an item only on an explicit false, never on absent/unknown — so a shape surprise shows a real failure rather than silently swallowing it. (2026-07-02, run_01d2fd9d, auto-cultivated)
- A snake_case->camelCase mapper that drops a backend field silently disables ANY downstream filter that needs it — the Radar queue could not exclude disabled jobs because jobToCamelCase never surfaced enabled. When adding a filter, first verify the field survives the serialization boundary. (2026-07-02, run_01d2fd9d, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Fetch-to-disk via a fail-loud script (halt on any 404 or <500B stub) is the right pattern for bulk external-data ingestion — 34/34 landed clean and the failure mode is loud, not silent. (2026-07-02, run_3bf8ea69, auto-cultivated)
- 4h-vs-300s consistency: once an artificial guillotine is removed, the REAL internal bound becomes visible and may itself be wrong. The 300s permission timeout was inconsistent with the 4h ask gate; aligned both via a shared-semantics constant. Safety invariant test-locked: approval timeout MUST stay < the WAITING_INPUT watchdog (else the watchdog reaps a live prompt). (2026-07-02, run_6e780e00, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- 🔒 SECURITY-CRITICAL PASS: reaped `rm -rf` is NOT silently approved (force-kill→CancelledError→approve_command never called→resume re-prompts) (2026-07-02, 0ed93981-3a9b-4f50-b096-cf3cbef1d4da, decision)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Manager disambiguation (permission vs ask) must read _pending_question SHAPE (request_id vs questions), not blind-OR both has_live_waiter calls — id-namespace disjointness is incidental, not an invariant (Gate-1 #3). (2026-07-02, run_65f317db, auto-cultivated)
- MID-BUILD SELF-INFLICTED: `git checkout core/session_unit.py` to revert a MUTATION also nuked my real edits in the same file. NEVER git-checkout a file holding both a mutation AND unshipped work — revert only the mutated lines in-place (read-modify-write with a unique marker). Cost: re-applied 2 edits. (2026-07-02, run_65f317db, auto-cultivated)
- The reap works ONLY because force_unstick_waiting_input -> _crash_to_cold_async -> _cleanup_internal clears _pending_tool_use_id at :3915 — Gate-1 #6 flagged this as the load-bearing check ('if it doesn't clear the flag, the fix is a no-op'). Always verify the recovery ACTUALLY clears the stuck state, not just transitions. (2026-07-02, run_65f317db, auto-cultivated)
- Gate 0 (Understanding + M3 skeptic) REFUTED my originally-stated root cause (SSA-disconnect) as WRONG-FRAME before any code — the real defect was a non-atomic teardown of two state pieces (_pending_requests popped by SDK-cancel finally, _pending_tool_use_id stranded). Confidence-counter-signal held: I was sure of the disconnect frame and it was wrong. (2026-07-02, run_65f317db, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Confirmed the full mechanism: `asyncio (2026-07-02, 22cf6589-7daf-4816-b11f-b3ca15ea1d24, decision)
- Gate-2 MED (AWS creds stripped subprocess) real but non-blocking → deploy smoke flagged (2026-07-02, run_6ac3fc0b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Dedup that compares a caller-derived title against a parser-extracted title must have a verified round-trip (writer's title == parse_entries' extracted title on the same input) — else dedup silently never matches and duplicates accumulate. Gate-2 mutation-verified the round-trip holds (casefold+strip). (2026-07-02, run_f73a33e2, auto-cultivated)
- When a new gate reuses an external classifier that NEVER rejects (always returns a routing dict, e.g. classify_content), the gate MUST read a numeric field (confidence<=0.3) not a truthiness check — a `if not classify_content(x)` would be a silent no-op that admits everything. Gate-1 plan-attack caught this pre-code. (2026-07-02, run_f73a33e2, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- C041 discipline held twice this run: I re-verified BOTH the Gate-0 pivotal claim (_extract_lessons_to_memory writes MEMORY ungated) and the Gate-2 B3 claim (evolution_optimizer writes EVOLUTION) against source before acting — a sub-agent's claim about my own system is an INPUT to verify, never a conclusion to accept. Both checked out; acting on either unverified would have shipped a design on a false premise. (2026-07-02, run_cbf9cab3, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Split-brain (read from service A, write via service B, two type files) reliably incubates a latent bug: here ToDoStatus declared camelCase inDiscussion, a value the backend never emits (it sends in_discussion), dormant only because the live render path used the OTHER type. Merging the types surfaced+killed it. Duplicated converters silently diverge (todos.toCamelCase dropped linked_context that radar.toCamelCase mapped) — the merge closed that gap too. (2026-07-02, run_e50aa3c9, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Anti-C044 the honest way = show what you DON'T know: a dimension with n<3 renders 'insufficient data (n=X)', never a green number. Proven on REAL data in SMOKE — research profile (n=2) correctly greyed. And critically, the gating lives ONLY in the human markdown; the machine JSON (consumed by EVALUATE/BUILD) is provably untouched (additive-only) so a consumer reading a number never gets a string. (2026-07-02, run_38f03634, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- M2 data-hygiene: I claimed total_tokens=0 in many runs and built an anti-C044 argument on it, but the real number is 8 percent and the zeros I saw were my own hand-filled runs this session. Generalizing a metric from an atypical self-sample is exactly the bias the anti-C044 design exists to prevent, I did the C044 thing while designing the C044 guard. (2026-07-01, run_1a8eaf7c, auto-cultivated)
- Root of the H1 miss is C038/C040 again: I checked pipeline_intelligence.json absent in the RUNTIME WORKSPACE (correctly absent, nothing runs the orphaned script) and asserted a code gap, never grepping the SOURCE TREE. A gap-claim needs a source-tree grep, not a runtime-dir ls. (2026-07-01, run_1a8eaf7c, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A baseline-diff security gate MUST fingerprint at the individual-finding level, NOT delegate pass/fail to the tool's own baseline (bandit -b): bandit -b suppresses per-file+per-test-type, so a 2nd md5 in an already-baselined file silently passes (exit 0) — a false-green gate is worse than none. Verified empirically before building (Gate-1). (2026-07-01, run_4b007e00, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- R28 applied to an unreachable guard: the miner defense-in-depth guard is unreachable today (regex only matches CLASS X), so I mutation-proved its test (revert->RED) to confirm it is load-bearing, not vacuous. (2026-07-01, run_685db747, auto-cultivated)
- Verification repeatedly shrank scope: Q2(b) producer-evidence work dropped (evidence already on disk); Bug3a re-scoped from producer-guard to consumer-guard after git-dating the axis guard (b4eb5124, 2026-06-25) and proving the OPERATIONAL leak was a STALE pre-guard proposal, not a live producer bug. (2026-07-01, run_685db747, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Empirical-first (R16b) resolved a real contradiction: skeptic said WRONG-FRAME, but I had a live block from the same gate. Rather than side with either, I read run.json (profile=docs + a build stage) + pipeline_profiles.py:20 (docs has no build) — the data showed BOTH were true and the gate was right. Never pick a side of a contradiction you can observe. (2026-07-01, run_105279ec, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Pattern-2 goal_cycle re-judgment is prose-enforced by necessity (instruction doc); gave it teeth by routing APPLICABLE findings into the SAME code-enforced _blocked_findings gate — enforcement surface is code even though transcription is prose. (2026-07-01, run_7583af5f, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The real bottleneck for SwarmAI self-evolution is NOT missing code (gate-rung implemented; 4 proposals accept-ready) — it is that XG has never clicked accept on any rule proposal, so active_rule stays None and the rule-failed->gate ladder never runs. Effectiveness AUDIT for RULES does not exist at all (30d-silence auto-resolve is gate-only + never fired). (2026-07-01, run_7e40bfa3, auto-cultivated)
- The EVOLUTION escalation chain has TWO distinct queues that look like one: confirm (governance_pending.json, 26 pending cognitive corrections) vs accept (.context/.evolution_proposals.json, 4 target=governance rule proposals ready). Reading one and inferring the other produced my original wrong premise AND the reviewer wrong claim. (2026-07-01, run_7e40bfa3, auto-cultivated)
- C041 discipline paid off in-run: I re-verified the reviewer own claims before accepting them and falsified one (it said accept-queue .evolution_proposals.json was empty/nonexistent; live read showed 4 ready governance rule proposals). A sub-agent claim about my own system is an INPUT to verify, not a conclusion — both author AND reviewer can be wrong; only live disk is truth. (2026-07-01, run_7e40bfa3, auto-cultivated)
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 0 | trust: full | promoted: 2026-07-01 -->
- Gate-2 又抓到 CLASS-A test-theater: 我自写的 cross-turn-bleed green test 是 vacuous(还原旧 guard 仍绿). 判别性测试必须构造新旧实现分歧的状态, 且 mutation-proven RED-on-revert. self-authored green ≠ 有效覆盖. (2026-07-01, run_f9adee1e, auto-cultivated)
- OT01 复发根源确认为半迁移(R27): run_6adee7d5 把 complete-handler 从 streamGen 迁到 latestCompleteGen, 但 stream-event 主路径没迁 → 同一 guard 两条消费路径只迁一条 = 下次复发进入点. 修 gen-guard 类 bug 必须 grep ALL 用 streamGen 判 stale 的路径. (2026-07-01, run_f9adee1e, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- 复用既有 flag 必须追踪完整生命周期(set/consume/clear)不只 set:Gate-2 抓到 poison_guard_recycle 复用 _recycle_kill_pending 但漏清理时机——send 入口清理跑在 guard arm 之前,recycle 成功后 marker 残留进同轮 stream,真 OOM 误判 ZOMBIE。加 flag 新 writer 时 grep 所有 consumer+clear 点确认新路径覆盖。 (2026-06-30, run_ed9647c5, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- chose id-prefix guard over new flag (god-file state = top regression source) (2026-06-30, run_f10075d3, auto-cultivated)
- missing-guard-at-single-chokepoint: prewarm ran full hook chain because enqueue_hooks had no prefix guard; fix=1 guard, AC3 free via adoption re-key (2026-06-30, run_f10075d3, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- → PROCEED-WITH-FIXES | #5 leak-check SAFE (all 11 hooks workspace-only, never subprocess/state) | adopted #2 (shared constant), declined flag-state (g... (2026-06-30, c219eb03-435f-4bd7-816a-5de6cb38628b, decision)
- BUILD — SIGNAL: guard added + 3 tests (prewarm-skip / real-fire / adopted-fire), prewarm test RED on guard removal; CHECK: pytest green + mutation; FA... (2026-06-30, c219eb03-435f-4bd7-816a-5de6cb38628b, decision)
- - ❌ **DECLINE the `_hooks_enqueued=True`-at-creation "defense-in-depth"** — the skeptic itself confirmed the string guard already covers 100% of the h... (2026-06-30, c219eb03-435f-4bd7-816a-5de6cb38628b, decision)
- session_id`→real (`:993`), so hooks should already skip" — refuted: adoption only happens 0× (24h), and the skip would only apply *if* a guard existed... (2026-06-30, c219eb03-435f-4bd7-816a-5de6cb38628b, decision)
- A golden case whose prompt/anchor/rubric name the wrong source file is a LYING GATE — it false-FAILs a compliant agent. Always verify the value's real definition site (grep the literal), not where it's referenced. (2026-06-30, run_8987469f, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- race test 用 fake wrapper.__aexit__ await 一个真 asyncio.Event 强制真 context switch — 这是忠实复现 anyio await 窗口的关键,mock-bypass 会让 race test 变 theater(Gate-1 #5 / 上个 run 的 test-theater 教训)。 (2026-06-30, run_02bc6dd1, auto-cultivated)
- TOCTOU 的修复层级判断:同步 null-the-ref-before-await(CAS)击败 asyncio.Lock — 锁会重新引入 await 窗口/争用且违反 watchdog-lock-free 不变量。当 race 可用一个 await-free 的同步抢占关闭时,加锁是 over-engineering(GC06)。 (2026-06-30, run_02bc6dd1, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- 2nd-leak terminal MUST _crash_to_cold_async(clear_identity=True) — else the poisoned _sdk_session_id survives and the NEXT user turn re-leaks across turns (within-turn bound insufficient). (2026-06-30, run_37008f2d, auto-cultivated)
- Gate-0 M3 skeptic killed an architecturally-impossible AC pre-code: soft-correct-and-continue-SAME-subprocess is impossible (single SDK stdio channel, mid-turn) — collapsed 2-tier ladder to single corrective-resume, mirroring _handle_buffer_overflow. (2026-06-30, run_37008f2d, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Exception-safety of a self-reschedule chain is the load-bearing risk: if reconcile() threw past its try/catch, scheduleNext() never runs → spinner freezes forever. Verified whole reconcile body in one try/catch + cancelled-guards at every entry. (2026-06-30, run_35aa06b1, auto-cultivated)
- Gate-1 BLOCK before BUILD saved a wasted run: building the PLAN as written (add ARM + demote isStreaming) would have duplicated existing arm/clear logic and risked regressing the 12s settleMs flap-guard. Plan-attack reading real code is the cheapest place to catch over-scoped work. (2026-06-30, run_35aa06b1, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- score-drift gate must use DIFFERENT-code_digest baseline (same-code = judge noise, proven 100→91.7 identical digest) (2026-06-30, run_95d9acbc, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- I was decomposing a 27-item batch into per-item visible commands — that read as a frozen UI and is the C042/C039 carve-it-up anti-pattern. The right shape was one batch pass over the deployed endpoint then one verification sweep. (2026-06-30, run_3b4c18e1, auto-cultivated)
- STEERING.RN refs do NOT map 1:1 to AGENT.RN after the 06-27 reorg (STEERING.R13 adversarial-rule -> AGENT.R1, R4 -> AGENT.R26, R11 -> AGENT.R28). Re-anchoring by number would silently mis-point; each had to be content-verified against the case topic (C044: make it a true probe, not just non-empty). (2026-06-30, run_3b4c18e1, auto-cultivated)
- I misread gate_refs=0 once mid-run (read a stale/wrong view and believed 27 EMPTY were already fixed) — the file was the ground truth and showed they were never touched. Verify against the on-disk file with a fresh sweep, not an in-memory impression (R16b). (2026-06-30, run_3b4c18e1, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The merge-preserve persistence re-appends disk-only cases verbatim, so a naive in-memory delete gets resurrected — hard_delete MUST reload-under-lock then filter so disk is the delete-time truth, not stale memory. (2026-06-30, run_110678fb, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- DEV-gated instrumentation is DEAD in the prod Tauri .app (import.meta.env.DEV=false → tree-shaken). A diagnostic meant to fire in production must use a runtime opt-in (localStorage), never import.meta.env.DEV. My earlier deploy-to-fix advice was wrong. (2026-06-30, run_3451bbd1, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Self-referential guard hazard: a test FILE that tests a command-guard can trip the guard when the agent greps/reads it (source line contains the trigger token). Assemble the token at runtime (PYT='py'+'test') so the file's own bytes don't false-positive a Bash grep over it. (2026-06-30, run_5511508d, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- **Rejected — it's the anti-pattern (2026-06-30, 40c77fea-7c93-49ec-a533-f9949d5089b1, decision)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Eradicate-the-root beat refine-the-guard: deleting BOTH active-run helpers (they existed only to guess a target run) was simpler and safer than tightening the length-split logic. (2026-06-30, run_f3975b8b, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- reusing a guard requires re-deriving its caps anchor in the new context (_reconcileStreamStart BLOCKER, O030 family) (2026-06-29, run_27485b25, auto-cultivated)
- multi-stage handoff must be traced end-to-end against consumer guards — I-set-the-flag != flag-gets-consumed (2026-06-29, run_27485b25, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- with TestClient(app) runs the full lifespan; bare TestClient(app) does not (Starlette only enters lifespan on __enter__) — a light-endpoint unit test forcing _startup_complete=True should never use the with-form or it pays real DB-init+migration cost and can blow the 120s pytest foreground timeout. (2026-06-29, run_b9ecb07a, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A TEMPORARY doc note must carry a greppable removal marker (HTML comment) — otherwise it becomes permanent cruft after the star count recovers. (2026-06-29, run_54a2fca1, auto-cultivated)
- Star recovery has TWO assets in hand: a local star_log.jsonl (111 unique logins w/ timestamps — the only record GitHub no longer provides) and three outreach channels (Discussion announce / README note / pinned issue). The README note is the only one that is a code change; the other two are external posts the user must approve. (2026-06-29, run_54a2fca1, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A config-template regression test must drive the REAL Settings class (no pydantic mock) and be mutation-proven: inject a fake dead field → both teeth go RED. The adversary independently re-ran the mutation (BOGUS_FIELD), which is the only thing that proves the test is not theater (GUI32/PIT13). (2026-06-29, run_75d12990, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Adversarial findings are inputs to VERIFY, not orders to obey — the HIGH 12:30-ICT finding used textbook ICT=UTC+7 but the codebase convention is ICT=UTC+8 (USER.md + every sibling cron comment). Changing a correct value to a wrong one is the worst outcome; check the project convention before fixing. (2026-06-29, run_26cc4bd4, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- jsdom is not WebKit — a vitest test asserts iframe ATTRIBUTES only, never render. Split the AC: attribute-level by unit test, visual by deployed-app REPRO gate. A green jsdom test is not render proof. (2026-06-29, run_ba089062, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Cron weekday convention is verify-by-execution, not by-assumption: ran is_cron_due on 7 real dates to confirm 1-5=Mon-Fri in THIS parser (it converts Python weekday→cron DOW). The highest-risk Gate-1 item was falsified empirically in 10 lines. (2026-06-29, run_f7a3acd7, auto-cultivated)
- Reject a skeptic finding with codebase evidence, not deference: Gate-1 claimed 04:30 UTC=11:30 ICT (textbook ICT=UTC+7) → would mislabel the comment. But every sibling comment in system_jobs.py labels UTC+8 as ICT and the user is UTC+8 — codebase+user convention beat the skeptic textbook. R16b cuts both ways: verify the skeptic too. (2026-06-29, run_f7a3acd7, auto-cultivated)
<!-- maturity: sparse | sources: 3 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Agent enforced a commit-count hard block (>40 = must split) — user clarified this metric was never agreed upon and should not gate releases (2026-06-28, 395ca170-8983-4e53-9229-78e0741386cc, correction)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- fail-open is correct for a hang-prevention guard (vs fail-closed for auth): a false-kill of legit work is worse than the rare miss, so every guard-infra failure must APPROVE — the guard cannot itself become a new hang/block source. (2026-06-28, run_07fd1d8f, auto-cultivated)
- Gate-2 caught a HIGH that EVALUATE/BUILD never could: I hardcoded /bin/bash for the parse-check and NEVER verified what shell the Bash tool actually runs — it is zsh on macOS, so /bin/bash -n false-killed valid zsh syntax (foo() { echo hi }). The whole EVALUATE understanding rested on an unverified runtime-state assumption (R16b/CLASS-B). A guard that validates commands MUST validate with the SAME interpreter that executes them — verify the exec shell, never assume. (2026-06-28, run_07fd1d8f, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The credential-resilient primitive already existed (jobs.bedrock.invoke evict+retry loop) but used the invoke_model API and couldn't carry the judge's converse system-prompt — so the fix was a converse-API sibling reusing the SAME _RETRIABLE_AUTH_KEYWORDS predicate (single source), not a copy-paste. Reuse-the-pattern beats reuse-the-function when the API shape differs. (2026-06-28, run_72b01506, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Tests for an rAF-gated _notify can be VACUOUS via a leaked earlier notify (an append's 100ms fallback timer fires the listener independent of the code under test). flush() before the action + mutation-test (revert the fix → must go RED) is the only proof of non-vacuity. (2026-06-28, run_1a264fd1, auto-cultivated)
- A convergent self-healing guard (cross-check two sources, fix on divergence) beats a callback bridge: it's cause-agnostic (heals ANY desync source), edge-gated (no churn), covers background tabs, and degrades a 120min freeze to ≤15s — without depending on every end-path remembering to sync. (2026-06-28, run_1a264fd1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The OK/TEETH+negative probe pattern is mutation-killing BY CONSTRUCTION: the adversary mutated all 4 production functions and every probe flipped (positive FAIL + teeth OK). This is the structural answer to 'self-authored test inherits the author's blind spot' (GUI50) — non-vacuity is proven by the negative mode, not trusted. (2026-06-28, run_2a5ff539, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- So the right frame isn't "invent a new coefficient" — it's "**the SSoT exists; #2 health-hook and #3 shell-script never adopted it** (2026-06-28, 4ddf2c93-005d-4668-b4b2-8f30651d310c, decision)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- I burned ~20min fighting the validator over a profile=goal-vs-full metadata mismatch (reused empty run carried stale goal profile) instead of stopping to tell XG — the C042 pattern: optimizing bookkeeping while the real work was already qualified. (2026-06-28, run_3f25a73a, auto-cultivated)
- A stale cap*3/4 truncation COPY lived in test_system_prompt_e2e._simulate_build — the exact DRY-drift class Gate-1 flagged, but in test code. Fix: drive the REAL function, never a copy (GUI38/PIT13). (2026-06-28, run_3f25a73a, auto-cultivated)
- A coefficient and its inverse MUST derive from ONE shared constant — 2 hardcoded 3/4 inverse-truncation sites had silently drifted; a forward/inverse pair on separate literals is a latent bug that only surfaces on recalibration. (2026-06-28, run_3f25a73a, auto-cultivated)
- 5/8 gaps already had RED-capable tests from THIS week's prior pipeline runs → golden-case POINTER (pytest -k → passed) reuses them, zero new test code (R25 don't duplicate). The GS_RCHAIN_MEMORY_WIRING precedent IS the sanctioned pattern. (2026-06-28, run_2d4be2f2, auto-cultivated)
- Gate-0 skeptic verified the non-vacuity risk BEFORE build: the change only works because reclaim_noise_entries gates on decay_state, not a re-checked age — if a second function had hardcoded 90, marking dormant at 45d would be a no-op. Always grep for a parallel hardcoded threshold before trusting a single-point tuning change. (2026-06-28, run_55cb38d6, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Root cause was the WHITELIST DEFAULT, not the missing entry: AskUserQuestion was silently disabled because resolve_allowed_tools built an implicit 8-tool whitelist and SDK treats non-empty allowed_tools as deny-everything-else. Fixing the default (→ blacklist) prevents the NEXT new built-in from the same silent-disable — appending AskUserQuestion (alt B) would have been whack-a-mole. (2026-06-28, run_9cfdb08d, auto-cultivated)
- A hardcoded self-report number in a doc (KNOWLEDGE ~47K) WILL drift (was ~2x below live ~91K). Fix = pointer to the live source of truth, never a fresh hardcoded number. P6: metric serves the outcome. (2026-06-28, run_3f25a73a, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Profile-before-fix (O008): the user-named heavy sections (Recurrence Radar reading 390K, _get_paused_pipeline_highlights) were FALSIFIED by measurement — Radar=5ms, paused=PIT01-protected. Real cost was a bare EvalService() constructor reload (516ms re-parsing 84 EvalHistory files) + a git-log subprocess buried in _get_health_highlights. Profile the actual call tree before accepting a stated bottleneck. (2026-06-28, run_b0ca1196, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Adding a row to a prose dispatch table is inert ONLY if the consumer is the agent not a parser — refuted the fake-stage risk by reading pipeline_validator.py (uses get_profile_stages, never parses the markdown table) (2026-06-27, run_b2b58e61, auto-cultivated)
- Extraction (R4) byte-equivalence is proven by a normalized diff vs git HEAD — the AC4 diff showed whitespace-only (list de-nesting); never trust I-copied-it-right without the diff (2026-06-27, run_b2b58e61, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 found my -byte-identical- claim was technically FALSE (and the divergence was an IMPROVEMENT): NEW empty-guard (if not text.strip) vs OLD (if not new_lines) differ on whitespace-only candidates, NEW strictly safer. Documenting a deliberate divergence with a test beats claiming false parity. (2026-06-27, run_55c6ab8f, auto-cultivated)
- Gate-1 skeptic corrected my PLAN premise: I claimed 16 callers of locked_read_modify_write; real count is ~4 (knowledge_graph._locked_read_modify_write is a name-shadowed DIFFERENT fn). Always enumerate callers with grep before asserting a count. (2026-06-27, run_55c6ab8f, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Token-aware predicate >> glob for command gating: the skeptic proved glob cannot distinguish :branch-delete from src:dst, -f from feature-f*, or --force-with-lease from safe. Mirror _is_dangerous_rm (shlex), never fnmatch, for any op where flag/operand semantics matter. (2026-06-27, run_73a54e70, auto-cultivated)
- C041 now GATED, not prose: gh repo visibility/delete + git force-push/branch-delete route through the existing approval flow. The structural-gate pattern (defense outside the agent) is the only thing that has held CLASS A/B — joins pytest_command_guard, background_command_guard, cmd_run_checkpoint. (2026-06-27, run_73a54e70, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Sibling-doc contradiction is a recurring class: the profile guidance lived in TWO places (evaluate.md tree + INSTRUCTIONS When-to-use-Goal) that BOTH said exit-0->goal. A behavior fix in docs must grep ALL authorities the agent reads, or the half-reconciled contradiction is worse than the original (O026). (2026-06-27, run_c236e4b1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- single-source block + self-defeating anchor pointers prevents drift (2026-06-27, run_48bd39cb, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The mode-switch discriminator must be a SELECTIVE-ONLY signal, not a proxy: output-length-compare is vacuous (a huge budget re-emits everything), but the Not-loaded manifest tail is emitted ONLY on the selective branch. When asserting which-of-two-modes ran, key off a token that exactly one mode produces. (2026-06-27, run_c0205808, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Re-stamp is a mandatory side-effect of any verification-field edit: compute_case_stamp hashes the case body, so adding negative_expected_contains changes the stamp, and a stale stamp silently DROPS the case from the BVT gate. Always re-stamp + verify the stamp matches after editing a stamped case (the adversary independently confirmed all 8 match). (2026-06-27, run_f16b5901, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Third READ leg now guarded by a mutation-proven contract (recall-search GS_RCHAIN_*, resume-extraction resume_fill, context-assembly assembly_floor). The anchor+guard pattern scales: each leg gets a spine probe driving the REAL function + teeth that break it, registered as a golden canary — the eval gate, not a prose rule, is what holds the bar. (2026-06-27, run_da644fcc, auto-cultivated)
- P4 in-flight: the golden_case_validator CLI crashed (exit 1) on EVERY clean public pass (report[stamp] str blind-unpacked as a gate tuple) and had ZERO test coverage of main(). Fixed it the moment it blocked my own case, not filed as follow-up — and added the missing main()-level regression test (the gap that let it ship: tests called validate_case directly, never the CLI summary loop). (2026-06-27, run_674f32ef, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Every new test group was mutation-proven (flatten-caps, forward-recent, forward-directives all killed): a green self-authored suite is the artifact I trust LEAST (CLASS A), so non-vacuity must be demonstrated, not assumed. (2026-06-27, run_6d5f60dd, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- cross-turn state behind a once-per-session guard MUST be tested on turn 2, not turn 1 — single-call test is blind to the early-return that kills it (2026-06-27, run_e9b15722, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Chain-level coverage was the real gap: 8 existing hook_builder tests + all guard tests called hooks IN ISOLATION — none exercised _build_chain's merge, which is exactly where the clobber lived. A bug between components is invisible to per-component tests (the seam-test lesson again). The 3 new tests drive the real chained closure (build_sdk_hooks()[..][0].hooks[0]), and the adversary mutation-verified them (revert -> exactly 2 red). (2026-06-27, run_7da67105, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 correctly REFRAMED my AC, not just found bugs: I built no-timeout as a hard DENY; the adversary showed that denies a correct config-driven setup (timeout in pyproject.toml -> bare pytest) — a false positive that blocks good work. Downgraded to a non-blocking WARN. The destructive case (pipe swallows output) denies; the soft case (no timeout, already bounded by the harness ceiling) only nudges. Asymmetric severity matters. (2026-06-27, run_6af22b0d, auto-cultivated)
- The fix for my own recurring mistake had to be a STRUCTURAL gate, not another rule: R9 already documented 'never pipe long-running pytest to tail' in prose and I violated it anyway this very session (6 re-runs). The two precedents that actually held — background_command_guard, cmd_run_checkpoint — are all code outside the agent. Prose is not a control; a PreToolUse deny is. (2026-06-27, run_6af22b0d, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- test-theater: a recall test that mocks the recall function tests nothing about recall — the missing test is always the one exercising the real seam under the real constraint (2026-06-27, run_bbd79e84, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-06-27 -->
- Keep the real data-loss detector (STORE-CLOBBER, fires only prevChars>=200&&nowChars<=20) while removing the always-firing divergence probes — signal vs noise split (2026-06-27, run_fe1226e6, auto-cultivated)
- Rollout probes must carry a removal owner — these survived in prod because TEMP-ungated for the .app build; deletion is the planned end-of-rollout step, not debt (2026-06-27, run_fe1226e6, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Mutation-testing a new test against the real defense (flip :647 to if(false)) is the cheapest proof of non-vacuity — and the adversary re-running it independently is what separates real coverage from test theater. (2026-06-27, run_2097cdc9, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- An unfalsifiable invariant is worse than none: marker-absence passed every typo/no-op negative. Require the negative to affirmatively signal it broke the wire. (2026-06-27, run_241be9da, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Self-found my own weak health probe by observation: _fts_is_healthy queried a no-match term that never traverses corrupted posting lists, returning True on a corrupt index — a health check that misses the corruption it guards is worse than none. (2026-06-26, run_1d198980, auto-cultivated)
- FTS5 external-content delete MUST bind OLD stored values not new — :189 bound new, desyncing posting lists on every chunk update (root cause, mutation-proven); messages_fts + remove_* methods had the correct precedent in-repo. (2026-06-26, run_1d198980, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 caught a format-only (mocked-conn) test that only asserted the MATCH string shape — the eval-theater pattern (PIT13). Hardened with a real messages_fts test, mutation-proven RED on revert. A query-construction unit test is fine as a FAST check but must be paired with a real-index behavior test. (2026-06-26, run_c730a9c0, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- origin-tagged dual-file split-write prevents private leak (2026-06-26, run_69b1c644, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Word-boundary > substring for keyword gates: single-token triggers must match \b-bounded or a confabulation reason rides a borrowed keyword. Denylist must take precedence over true-trigger (fake caution must not ride in on budget/block). (2026-06-26, run_a822b3e8, auto-cultivated)
- The guard ALREADY existed as a stderr warning (artifact_cli.py:1503) and I steamrolled it IN the very session — proof that a warning the agent can ignore is not a gate. Fix = warning->hard-block (exit 2), defense OUTSIDE the agent. Same lesson as background_command_guard. (2026-06-26, run_a822b3e8, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Fixing friction is dangerous: C3 (strip quotes) and C4 (auto-resolve routing) were both friction-removal fixes, and BOTH over-corrected into a security/correctness hole — C3 opened an M1 bypass, C4 defanged Check 13 entirely. Lesson: when removing a gate's friction, the adversarial must specifically ask 'did this also remove the gate's PROTECTION?' — friction and protection share the same code. (2026-06-26, run_7cf9da85, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- [A-Z] inside an IGNORECASE regex matches ANY letter — it does NOT restrict to uppercase. My first imperative-fix attempt used [A-Z] to mean 'capitalized object' and it silently matched 'Use of' (the lowercase 'o'). Verified by empirical probe before shipping. Never use a case-restricting char-class under re.IGNORECASE. (2026-06-26, run_b9452eb9, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Rule 23 worked as designed: the Gate-2 adversarial spawn was rejected (turn interrupted, not a real veto — PIT01 signature), I retried EXACTLY once, it succeeded, and it immediately earned its keep by finding the MED. The retry-once-then-checkpoint rule is the correct response to a spawn rejection; never fall back to self-review. (2026-06-26, run_dc9917f2, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- This gate is the FIRST validator logic keyed on work_type (previously cosmetic) and the FIRST to code-enforce pre_mortem (doc-mandated since forever, never checked). A 'follow-up' feature surfaced two latent unenforced contracts — worth noting that adding a gate often reveals adjacent fields that were quietly load-bearing-in-doc-only. (2026-06-26, run_b5b26ebe, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- An adversarial HIGH must be confirmed/refuted by an OBSERVATION (grep the call sites), never argued down — the claim the gate was uniquely fail-open was falsified by finding sibling gates have identical publish-time-only enforcement. (2026-06-26, run_932c0991, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Adversarial MUTATION testing is the decisive non-vacuity proof: the reviewer removed each guard and confirmed the positive case flips to failure. A fault-injection case is only worth its cost if removing the guard breaks it — assert that explicitly (the --negative mode) AND have the reviewer mutation-check it. (2026-06-26, run_4596411e, auto-cultivated)
- A schedule-LOCK test must guarantee GRADED not just PRESENT (Gate-2 MEDIUM): a tier:draft case 'survives the filter' but is silently SKIPPED (skipped!=failed) — guard lost with no signal. Lock must assert non-draft + scores-into-dimension. (2026-06-26, run_4596411e, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Empirical probe beats confident inference (C038/R16b), proven twice: my record_token_usage root cause for the conftest lock was a B-class string-grep narrative — the actual run showed orphaned SessionRouter._drain_worker; AND a false-green (AttributeError crashing setup) looked identical to 0-locks until I checked tests actually RAN. Always confirm the test executed, not just that the grep count is 0. (2026-06-25, run_f370ce49, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-06-25 -->
- R16b in build: I guessed the injected error string (error_during_execution) and the stream seam (_read_formatted_response) — both wrong. Reading retry_manager + session_utils gave the real marker (Zombie subprocess detected) and the real seam (_streaming_orchestrator.stream_query). Read the API, do not code from memory. (2026-06-25, run_f646b175, auto-cultivated)
- A probe anchored on a COMMENT rots invisibly: Q2.4 grepped streamGen!==capturedGen which now only matches a comment describing the REMOVED check; live guard was renamed capturedGen!==liveGen. Anchor probes on live code, never on prose that describes code. (2026-06-25, run_aeab16f1, auto-cultivated)
- PIT07 confirmed structurally: skill-doc grep probes silently false-green/red when code refactors out from under them. Fix = Q0 self-validation gate that asserts target FILE + ANCHOR SYMBOL presence (never line numbers) BEFORE any invariant is trusted — validate the tool before using the tool. (2026-06-25, run_aeab16f1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- O(n²) from per-token reparse of a memo component = the memo never hits because content changes every token; throttling the content value restores memo benefit between windows. (2026-06-25, run_087e097e, auto-cultivated)
- Privacy gate must DEFAULT-DENY: --session-type required=True (no permissive default), and enforce identity at BOTH manifest-generation and recall-execution (defense in depth). (2026-06-25, run_9de88af9, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- 5):** all 5 AC + Gate-1 guard map to passing, non-vacuous tests (spec sub-agent already independently confirmed the AC→test mapping verifies the right... (2026-06-25, fc74fe84-28ea-4b22-9d44-c694a29391c6, decision)
- When gating a store-subscription callback on a prop (isActive), the prop MUST be read from a ref inside the callback — the subscribe effect is keyed [store] only, so a render-closure capture goes stale and gates the wrong tab. Gate-1 caught this; the audited-safe pattern already existed at useChatStreamingLifecycle.ts:1093-1097. (2026-06-25, run_5e248977, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- a doc fix changing a recommended CLI pattern needs failure-mode scrutiny like code — --quiet pattern relocated the crash (2026-06-25, run_88b9f986, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- per-tab isStreaming + single-writer + reconcile backstop prevents cross-tab stuck-state (2026-06-25, run_00e0e872, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- GUI12 layout-shift-free pattern (border-b-2 on ALL states, transparent on off) applied cleanly and verified by adversarial — no shift across active/hover/unread (2026-06-25, run_1866ea59, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Validator AC-matcher only extracts ^AC\d+ from stringified PLAN ACs; when PLAN stores ACs as dicts, plan_ac_ref cannot bind — coverage `ac` text MUST equal the PLAN desc for the substring fallback to fire. Mechanical, not a validator bug. (2026-06-25, run_cabd0bc1, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-06-25 -->
- Prose warnings the runner never reads are NOT a guard. The skeleton self-marked as a placeholder in its title/rubric text, but adversarial found eval_trajectory_capture had no tier=draft guard — on a behavior_trajectory run the unrefined skeleton would spawn an agent + fold a tautological pass into the score. A code-level tier==draft skip is what makes refine-before-relying self-enforcing. (Honesty must live in CODE, not comments.) (2026-06-25, run_0305426d, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- keep a real-weakness FAIL as FAIL + document WHY, never soften the rubric to go green (P4_OWN) (2026-06-25, run_976c32f7, auto-cultivated)
- prompt-source must equal answer-source in a lookup behavior case, or the agent unbounded-searches to timeout (2026-06-25, run_976c32f7, auto-cultivated)
- a behavior case that only ever ERRORs (never pass/fail) has UNKNOWN validity — an ERROR is not a verdict, dont trust it as signal (2026-06-25, run_976c32f7, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- For trajectory_capture + decision_rubric cases, the gate and the action must share the citable fact: expected_trajectory MUST name a doc that actually CONTAINS the rule the rubric demands. Fresh spec + adversarial reviewers caught 3 cases where the expected doc lacked the cited fact (PIT09 gate==action coherence). Verify each expected doc against source line numbers. (2026-06-25, run_b250caf1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- completion-time supersede-marking is structurally deeper than briefing-read-time archiving — deferred; read-time guard also defends against parallel/external-session completions that never fired a hook. (2026-06-25, run_0c8e007a, auto-cultivated)
- The gauge false-alarm IS the same disease class as finding-1/M4-3/noise-gate: a measurement reading dirty/stale data without a freshness or supersede check. Fix pattern is always: add the discriminator the gauge was missing. (2026-06-25, run_0c8e007a, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-06-25 -->
- lock must span the WHOLE read-modify-write, not just the write — locking only the rename leaves TOCTOU open (2026-06-25, run_0fac5a91, auto-cultivated)
- Rule-23 retry-once after Gate-2 spawn rejection avoided the CLASS A self-review bypass (2026-06-25, run_fb4b42d2, auto-cultivated)
- clearing the reverse-risk invariant (soft-delete only shrink path) made the fix both safe and minimal (2026-06-25, run_fb4b42d2, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The circular-judge problem is STRUCTURAL not a bug: an LLM judge handed the rules+docs and asked would-a-compliant-agent-do-X can only confirm doc EXISTENCE, never observe USAGE. Real-behavior eval REQUIRES spawning a real agent and observing its tool-call trajectory (eval_runner.py::eval_trajectory_capture + scenario_runner.py). (2026-06-25, run_75b656c1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Layout-shift-free border indicators: put border-b-2 on ALL states, transparent on the off state — never add border only to the active state. (2026-06-25, run_71d54d97, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- It **confirmed the core fix holds** on the two highest-risk dimensions (A3 store-superset invariant, isolation, race — all verified preserved) (2026-06-25, 46246259-3bf7-415e-8578-85744ffa1c68, decision)
- Rejected C (deterministic selector) = the recurrence pattern (2026-06-25, 46246259-3bf7-415e-8578-85744ffa1c68, decision)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Probe-as-verification-gate ships to production intentionally to confirm fix live; removal trigger must be explicit or it becomes permanent log-noise. (2026-06-25, run_9db9f987, auto-cultivated)
- Reconcile-gap recurred 33x because all fixes targeted layers 1-9 (persist/SSE/store/reconcile/correlation) — the actual defect was layer 6 (TabView dual-source render selector). When a bug recurs N times, the layer being fixed is probably wrong; map ALL layers and find the one never touched. (2026-06-25, run_9db9f987, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- content-shape must assert expected token not arbitrary sentinels (2026-06-25, run_52a22424, auto-cultivated)
- busy-vs-broken probe must discriminate wedge from load (2026-06-25, run_52a22424, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- never git-checkout a file with uncommitted work (2026-06-25, run_a0d93136, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate==action and dedup-completeness recur as a class: B2 (dedup gated to one action type) is the same shape as the M0 H3 finding (gate excluded what action included). When you add a dedup/filter/guard keyed on a substring, ask 'which legitimate inputs DON'T contain that substring?' — maintainer-validation actions lacked New engagement pattern and silently never deduped. (2026-06-25, run_4cbbc147, auto-cultivated)
- Two functions touching the same data model must agree on it: fold_patterns used a multi-line-aware bullet regex (correct) while cultivate CAP assumed single-line (wrong). The fix unified them at the write boundary (sanitize input) rather than making CAP multi-line-aware — simpler, and the source of truth is 'entries are single-line'. (2026-06-25, run_4cbbc147, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-06-25 -->
- Date-less != old: a read-only gauge may treat date-less as infinitely-old for honest MEASUREMENT, but a DESTRUCTIVE action must require a real date — date-less means unknown-age, and archiving unknown-age knowledge is unrecoverable. Measurement and destruction can use different thresholds for the same signal. (2026-06-25, run_94fd5597, auto-cultivated)
- Gate==action coherence: a measurement gate and the action it gauges MUST share one predicate (_is_reclaimable_noise) or they silently drift — the gate FAILs forever on noise the action will never clean. Duplicated predicate across 3 sites was the root maintenance bug. (2026-06-25, run_94fd5597, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Deletion > misleading dead code: expected_generation stale-guard was implemented + unit-tested but NEVER passed by the endpoint/frontend (unreachable), while the docstring sold it as active protection. Both specialists flagged it. Removed it; real re-adopt protection is the active-state skip + interrupt()'s OWN generation guard. Dead code that implies non-existent safety is worse than no code. (2026-06-24, run_47ff7d76, auto-cultivated)
- Test-verifies-wrong-thing (C011 class) recurred: the force-release test asserted interrupt.await_count==1 + kill.await_count==0 and passed GREEN while the slot stayed held. The mock _fake_interrupt faithfully left the unit IDLE/alive, reproducing the bug — but no assertion checked alive_count dropped. Lesson: for any 'frees a resource' operation, assert the RESOURCE STATE (alive_count, slot freed), never just 'the right method was called'. (2026-06-24, run_47ff7d76, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Side-channel SSE events (toast-only) must be handled BEFORE the cross-turn-bleed generation guard + return immediately — a session-scoped signal is true regardless of turn generation but looks stale to the guard (emitted post-result). Mirrors file_changed. (2026-06-24, run_d8dce02a, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Old-spec-encoding tests are a legitimate spec-change update, not test-fudging — R6a deleted the count-gate spec, so 3 tests asserting count-gate timeout/abort were updated to assert budget-denied timeout/abort. The intent (over-allocation guard, queue timeout, retry abort) was preserved; only the trigger changed from count to RAM. (2026-06-24, run_6ea35431, auto-cultivated)
- When a design names a mechanism to change, verify it is the BINDING constraint before touching it — R6a: spawn_budget already permitted the 4th tab on memory-abundant machines; only the compute_max_tabs COUNT CEILING blocked it. The design-implied penalty-input swap (alive_count->streaming_count) was both wrong (reopens COE05 — penalty IS the simultaneous-peak floor) and unnecessary (penalty was never the binding gate). (2026-06-24, run_6ea35431, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Malformed model output (tool-call XML in the text channel) is a real failure class distinct from subprocess/API failures — it completes normally at the backend (result_usage emitted) so the empty-result/zombie guards never fire; it needs its own content-level detector at the DB-persist gate. (2026-06-24, run_e607c4cd, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- import.meta.env.DEV guard on diagnostic console.warn is correct fail-loud-in-dev/silent-in-prod — Vite statically strips it in build:all (verified). (2026-06-24, run_b549e8ca, auto-cultivated)
- SMOKE gap left by a prior session (deferred because vitest only scans src/**) was the real hole: the 2 named AC tests pre-baked answers onto the block and never exercised the genuine updateLast-by-toolUseId submit write-path. Pre-baked-input tests give false confidence about the write path. (2026-06-24, run_b549e8ca, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 1 | trust: high | promoted: 2026-06-23 -->
- Verify pre-existing test failures by stash-and-rerun, never assume: the -k batch showed failures that stash-and-rerun proved were pre-existing pollution, not my change. (2026-06-23, run_fe0122b5, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Adversarial traced the SDK process boundary to disprove a plausible coroutine/memory leak: PreToolUse hook runs in the daemon (same as the manager singleton), but Query.close() cancels _child_tasks → wait_for_answer finally fires → no leak. Evidence-based disproof, not assumption. (2026-06-23, run_594233bb, auto-cultivated)
- Two timers governing the same resource MUST be reconciled explicitly: hook answer-wait (4h) vs lifecycle WAITING_INPUT watchdog (was 2h). The shorter one silently capped the longer and killed the whole session. Added a guard test locking watchdog > hook-timeout to prevent future drift (GC14 class). (2026-06-23, run_594233bb, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-06-23 -->
- Fail-safe direction matters: tree_cpu_seconds returns None on psutil-missing → treat as cannot-prove-dead → NEVER interrupt. Bias every ambiguous case toward NOT killing live work; let the generous absolute ceiling (3600s) be the only unconditional kill. (2026-06-23, run_fb6e94a9, auto-cultivated)
- A liveness signal must be ORTHOGONAL to the failure it detects. event-silence is the SAME signal for hung and busy tools → useless as a discriminator. tree-CPU-delta is orthogonal: a wedged tool burns 0 CPU, a working one (incl. an Agent sub-agent running its own CLI child via psutil children(recursive=True)) burns >0. Empirically verified before building: busy=1.079, idle=0.000 cpu-sec/s. (2026-06-23, run_fb6e94a9, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- EVALUATE empirical reproduction (GUI01) falsified BOTH stated requirement holes — the SDK transcript proved answers were non-empty AND correctly routed; the real bug was the CLI self-resolving AskUserQuestion with is_error in headless mode. Reading the SDK transcript + daemon logs before coding saved building two fixes that would have fixed nothing. (2026-06-23, run_594233bb, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- per-turn guard must clear on EVERY teardown incl interrupt success branch (GC15 generalized) (2026-06-23, run_2238b50b, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A guard test asserting a hand-copied mirror gives false confidence. Add a production-source guard (readFileSync + regex on real file) with negative-guards against wrong implementations, verified fails-on-revert — converts the mirror from proxy to anchored. (2026-06-23, run_beee9586, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 HIGH: a try/except degrade-to-raw wrapped around a self-healing merge is double-edged: it prevents a crash but silently RE-BURIES the exact drift the merge exists to cure. The merge must be made TOTAL (str-coerce mixed-type date fields) so the except never fires on recoverable input. (2026-06-23, run_40fad09e, auto-cultivated)
- normalize-on-read (_merge_drift in _load) makes migration self-healing and idempotent by construction: no separate one-shot script to forget, and future drift heals automatically on next load. (2026-06-23, run_40fad09e, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The escalation ladder is now COMPLETE end-to-end: correction recurs -> classify -> park -> propose rule (P2) -> human accepts in dashboard -> register_rule (P3) -> recurs again -> propose GATE (P3 rung) -> accept -> register_gate. The CLASS-A lesson ('rules dont stop it, gates do') is now executable, not just documented. (2026-06-23, run_28a40cc4, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate 2 HIGH-3 was a concurrency-discipline mismatch on a shared file: one writer flocked+atomic, the other plain write_text. Lesson: when adding a 2nd writer to an existing file, the NEW code must adopt the SAME locking discipline — and the cleanest fix routes BOTH through one helper (deleted the optimizer's duplicate dedup block). (2026-06-23, run_6cb825e4, auto-cultivated)
- Gate 2 HIGH-2 was a cross-writer key-format mismatch: two producers (EVOLUTION.md miner 'CLASS A: desc' + tracker 'CLASS_A') writing the same dedup key in different formats = same logical item surfaces twice. Lesson: when N producers share a dedup key, the key must be CANONICALIZED at a shared chokepoint, not assumed identical. (2026-06-23, run_6cb825e4, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A guard test must use NEUTRAL fixture data — my first RED passed falsely because the fixture content contained the very keyword (auto-created, drift) the assertion searched for. Assert a STRUCTURAL marker the code does not already emit, with content that cannot leak the keyword. (2026-06-22, run_cdbd6679, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- When a routing table and a separately-authored artifact (doc heading) must agree, detection-after-the-fact (a guard test) is weaker than making divergence harmless: auto-create the missing target from the trusted side (ROUTING_TABLE section name) rather than drop. STEERING #1 — structural prevention > watchdog. Moved cross-instance drift from HIGH risk to non-issue across all 8 projects. (2026-06-22, run_6b52f221, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- STEERING #1 + d32c3e9b/PIT03 make retry-on-poison an anti-pattern: the only safe retry for a poisoned spawn is across a checkpoint->resume process boundary, never in-turn. Fail-closed via an EXISTING code gate beats a new watchdog (STEERING #2). (2026-06-22, run_0bd15278, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- md` + Rule 23 define explicit behavior: spawn rejected → bounded fresh-context recovery → still blocked → `CHECKPOINT reason=gate_spawn_blocked` (2026-06-22, c7176a34-005b-4717-ba10-133b5fe1de60, decision)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- numeric-id placeholder identification (/^d+$/) in Pass 2 is convention not type-enforced — a latent rot risk flagged by meta-review; future hardening = explicit placeholder sentinel. (2026-06-22, run_59f8f5ad, auto-cultivated)
- Frontend-synthesized interactive blocks (ask_user_question/cmd_permission_request/escalation) are SSE-only state never persisted to DB — any reconcile-from-DB MUST carry them forward or it erases the live form. This producer/consumer mismatch is a structural seam worth recording in TECH.md. (2026-06-22, run_59f8f5ad, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- This bug masqueraded as a backend hang 4 times across the session — the stale {tool}·{elapsed} label made a healthy streaming session look frozen. Always verify backend /api/chat/sessions/streaming-state before trusting a frontend activity indicator. (2026-06-21, run_81a580ba, auto-cultivated)
- Frontend-only fixes have a deploy blind spot: tsc+vitest green and even a backend build+health-check all-green leave the running Tauri .app on STALE frontend. Must npm run build:all + relaunch .app. The deliver step must state this as the ship instruction, not omit it (meta-review HIGH). (2026-06-21, run_81a580ba, auto-cultivated)
- Anchor timer to the DEBOUNCED displayed value, not the raw stream value — else label (debounced) and timer (immediate) diverge by the debounce window, recreating a smaller version of the same bug. The two must read the same source-of-truth. (2026-06-21, run_81a580ba, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Fail-open vs fail-closed asymmetry between two paths doing the same job (advance vs completion) is a code smell worth hunting: same verification, opposite failure direction = one of them is wrong. The root cause here was mechanical (subprocess raises-and-blocks vs in-process catches-and-continues), not intentional design — the 'intentional' comment was post-hoc rationalization of an accident. (2026-06-21, run_84316b42, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- ran unbounded tsc → 17min hang, violated own hang-is-failure rule; use perl alarm on macOS (2026-06-21, run_af36e709, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Adversarial review on the mechanical gate found 1 HIGH + 3 MED + 1 LOW in a 5-AC change that passed all prior stages and 8 green tests. Highest-value finding (subprocess fail-open) was invisible to unit tests because they call validate() in-process, never through the subprocess boundary where the bug lived. (2026-06-21, run_55710438, auto-cultivated)
- A _recorded early-return flag silently dropped the ERRORED record when a hard check crashed AFTER passed() — the exact C037 silent-fail-open class, inside the code meant to prevent it. force=True overwrite on the crash path was required: in safety-gate state machines the crash path must OVERRIDE a prior success record, never be blocked by it. (2026-06-21, run_55710438, auto-cultivated)
- Fail-open hides in the SUBPROCESS boundary not the function: validate() returned valid=False correctly, but a CRASH gave empty stdout + nonzero exit and the consumer guard was if-result.stdout with no returncode check, so a crashed gate silently advanced. When a gate runs as a subprocess the consumer MUST treat (nonzero exit AND empty stdout) as fail-closed, separate from valid=False. (2026-06-21, run_55710438, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Empirical reproduction before fixing a concurrency bug, and the RED test must fail for the RIGHT reason: first two test versions passed on buggy code because a stale-unit alternative bypassed the wake loop. (2026-06-21, run_002eca4c, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Collapsed-summary pattern: when N items must all stay VISIBLE but cannot each get a line, emit one line with exact COUNT + bounded id list (+N more). Preserves completeness signal without unbounded growth. (2026-06-21, run_2f84c3c0, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Path-prefix allowlists MUST normalize + reject .. before startswith — /tmp/../etc bypassed a raw startswith check (adversarial CRITICAL). (2026-06-20, run_7b0b9a1f, auto-cultivated)
- On-disk config file silently overrides code default (PIT38 recurred): changing DEFAULT_DANGEROUS_PATTERNS did nothing until load migrates the persisted JSON. Any default-change needs a migration of the persisted copy. (2026-06-20, run_7b0b9a1f, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Pure-predicate extraction (shouldResurfaceQuestion, computeDrainRetirement) made the decision logic testable in isolation and made the 15s-poll flap impossible by construction (idempotent on toolUseId). Keeping orchestration thin and decisions pure is the right split for a hot-zone reconcile loop, but the orchestration body is now a ~330-line god-effect (4 concerns) and is the next refactor target. (2026-06-20, run_04006034, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Cross-layer interaction bugs are invisible to per-layer tests — each layer correct in isolation, the bug lives in the SEAM. Both Gate-2 CRITICALs were seams (drain×error-convention, teardown×guard). Confirms adversarial-on-full-changeset (Gate 2) is the only gate that holds N interacting paths simultaneously (PIT29). (2026-06-20, run_3f4f4805, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Optional deps-injection (onSelectTab) mirrors the existing onDrainQueue pattern — extend the deps interface, dont reach for context inside the hook. (2026-06-20, run_abcda963, auto-cultivated)
- Actionable toasts (go-answer, go-to-tab) must NOT autoDismiss + must carry an action callback — a 5s flash for an action the user must take is a UX defect. Distinguish info-pings (autoDismiss ok) from action-prompts (persist+clickable). (2026-06-20, run_abcda963, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- PIT71 cross-tab-leak avoidance was structural, not a guard: sourcing the question id ONLY from the active tab cache + the loop only ever rendering the active tab makes a leak unreachable, verified by the adversarial auditing every setMessages path is active-tab-gated. (2026-06-20, run_595b504d, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Guarding a hidden CLI flag requires a NEGATIVE probe, not a positive one: the Claude CLI silently tolerates unknown flags (exit 0 + version), so a positive probe (--thinking-display summarized) gives 100% false-pass. Only a bogus value triggers an enum-validation error that names the choices — present iff the flag exists. Always reverse-test a guard (does it fail when the thing it guards is absent?). (2026-06-20, run_a972318c, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- retryPayload MUST be camelCase — parseSSEEvent only camelizes event.content, top-level keys pass through verbatim. The QUEUE_TIMEOUT precedent (session_router.py:1136) already established this; the initial snake_case plan would have been silently dead. Grep for an existing precedent before inventing a contract. (2026-06-20, run_dba9843f, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- Fixing a flagged same-class follow-up immediately is cheap (1 Edit + 1 WebKit measurement + commit) and prevents a real left-clip on every wide diagram. (2026-06-20, run_de465356, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Dont unlink consumed state files — let atomic overwrite handle lifecycle (crash-safe) (2026-06-20, run_7dc8aca6, auto-cultivated)
- State persistence must be lazy-inject at creation time, not boot-time restore on empty dict (2026-06-20, run_7dc8aca6, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- State persistence is cheap insurance — 50 LOC eliminates 70% of cold resumes (2026-06-20, run_e83055b8, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- State coverage guard tests prevent silent detection gaps when new states added (2026-06-20, run_bb64cd0e, auto-cultivated)
- canary assertions must test dynamic values not template literals (2026-06-20, run_d44d3d6e, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Subprocess kill on disconnect was the ONLY gap — messages already persist immediately during streaming (2026-06-20, run_c15f424c, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Suppressing a detection signal during a specific state (hang_detected in STREAMING) requires the signal caller to pass state context — default-unsafe is the right pattern for backward compat (2026-06-18, run_ae6d25d7, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- Status taxonomy (passed/failed/error/skipped) prevents false alerts from config issues (2026-06-18, run_f212fba3, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- Eval canary budget: entry gate alone is insufficient — must also cap per-case timeout to remaining budget (2026-06-18, run_df83bb9a, auto-cultivated)
- Deriving constants from a single source table eliminates drift — SAFE_APPEND_SECTIONS now auto-updates when ROUTING_TABLE changes (2026-06-18, run_ca22adae, auto-cultivated)
- Two-part governance detection (action+target) prevents false positives from common words like standing rule (2026-06-18, run_ca22adae, auto-cultivated)
- Tie-break logic must be explicitly preserved during refactors — silent reclassification is invisible regression (2026-06-18, run_ca22adae, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- fcntl LOCK_NB test pattern (hold lock from one fd, try from another) is reusable for any lock-protected shared state test (2026-06-18, run_12c2807d, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- Optimistic ID format local-* is a convention that must be stable forever (breaking it = silent dedup failure) (2026-06-17, run_95dc339d, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Signal words must be multi-word phrases for meta-types (behavioral pattern not behavioral) to avoid false positives on common adjectives (2026-06-17, run_5ddfc3ca, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- _healGraceActive on tab state + setTimeout = simple but effective grace period pattern. (2026-06-17, run_de30ebf7, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- Rule 22 is the enforcement for what Rule 12 only stated philosophically — without mechanical gates, agents will always find reasons to skip metrics recording (CLASS A pattern applies to audit just like it applies to adversarial) (2026-06-16, run_9f820aff, auto-cultivated)
- run_dir NameError was a copy-paste bug from a different function scope — always grep for undefined variables after adding code from another context (2026-06-16, run_9f820aff, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- Merge-persist pattern (read-merge-write) is the correct solution for multi-writer YAML files — append-only is too restrictive, full-overwrite loses data (2026-06-16, run_d6cdd758, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Pipeline chat output IS the product demo — structure must mirror architecture (3 phases visible, gates marked with ★, stages numbered ①-⑩). If a viewer cannot explain the pipeline architecture from reading one execution transcript, the display failed. (2026-06-16, run_387b262d, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- pytest.mark.skipif with a callable guard is the correct pattern for CI vs local divergence — never assume workspace exists (2026-06-16, run_af60d06b, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Check 17 (Blocking Constraints) wired into Tier 1 + Exit Evidence — skill docs must be wired into routing tables, not just defined as standalone sections (same pattern as Check 16 which was initially missed from tier list in v3) (2026-06-16, run_908130a5, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- flock-based _locked_mutate pattern works well for low-frequency state files shared across parallel sessions (2026-06-11, run_d39f005f, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- Frontend polling unreliable in Tauri webview — SSE event-driven is correct pattern (2026-06-07, run_926bcd7b, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Two-phase effect pattern (state flag + useEffect) is the safe way to autoSend after inject — requestAnimationFrame does not guarantee React state flush (2026-06-07, run_f101f801, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Make X impossible beats clean up after X — type system enforcement has zero runtime cost and prevents entire bug class structurally (2026-06-07, run_6225b7b3, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Adapter interface is clean enough that adding 2 new sources was 4 hours of work including adversarial fixes — the pattern pays for itself (2026-06-07, run_f2ad66a8, auto-cultivated)
- Time-series signals like stock prices need date-suffix on URL to prevent dedup from blocking daily updates — new pattern for signal adapters (2026-06-07, run_f2ad66a8, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Pipeline validator schema enforcement is the structural follow-up — prompt-only gates can be skipped without consequence per C036 pattern (2026-06-07, run_6215b691, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Per-turn DB persistence for crash safety creates a read-side merge requirement — write split and read merge must be designed as a pair (2026-06-06, run_a1640108, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Watcher lifecycle (start/stop/capacity) matches session lifecycle pattern (2026-05-30, run_725cd1bb, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Atomic install (tmp+chmod+rename, PID-suffixed tmp) solves three things at once: EACCES on re-copy over read-only bundle files, in-place-overwrite corruption of a running script, and concurrent-launch tmp collision. (2026-05-30, run_8a9de435, auto-cultivated)
- Mirror the existing proven pattern, do not invent — the cfg-gate bug came from gating atomic_install when the sibling copy_dir_recursive it sat next to was un-gated. The codebase already encoded the right answer. (2026-05-30, run_8a9de435, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- git commit swept in pre-staged daemon_guard work (425 lines) already in index — 2-file fix became 9-file. Always git diff --cached --name-only before commit. (2026-05-30, run_fd4d756b, auto-cultivated)
- When the working tree is externally mutated mid-pipeline (parallel session commit + stash-pop conflict in 14 unrelated files), checkpoint (L2 BLOCK) and escalate — never guess across another session conflict resolution. Re-apply own fixes only after the tree is clean. (2026-05-30, run_b5592983, auto-cultivated)
- Re-exec/detach must use an ABSOLUTE script path + a FIXED interpreter matching the shebang — relative $0 is unfindable in the detached child cwd, and $SHELL (often zsh) mis-executes a #!/bin/bash script. Both make a re-exec a silent no-op (C034 prevention did nothing; reproduced E2E by adversarial). (2026-05-30, run_b5592983, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Hot/Cold index pattern is the correct abstraction for any auto-generated listing that grows linearly with workspace age. Same pattern applies to: PROJECTS.md cross-index, EVOLUTION.md optimizations archive, future skill listing if it grows past 100. (2026-05-29, run_79afc100, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Bedrock converse() additionalModelRequestFields is the correct mechanism for thinking+effort — not direct body params (2026-05-29, run_98ea10a3, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Custom events (swarm:show-code-graph) are the clean cross-component trigger pattern without state lifting or new React context providers (2026-05-29, run_12f34095, auto-cultivated)
- codeIntel.ts double /api/ prefix was invisible in dev mode because Vite proxy forwarded full paths - production Tauri webview exposed the 404, need E2E smoke test against real daemon (2026-05-29, run_12f34095, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Existing hook infrastructure enables new compound-value features in under 200 lines with zero architectural risk - SessionLifecycleHook + db.messages + hook_chain already covers 95% of the pattern (2026-05-29, run_ab3398d9, auto-cultivated)
- code_change_feed watched wrong repo for 2 months — SwarmWS has no .py files outside SKIP_PREFIXES so Ch1 was dead since launch. Root cause: single-repo assumption when architecture has 2 repos (workspace vs codebase). (2026-05-29, run_30dfc465, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Regex for metric extraction must use explicit unit patterns not bare character classes — s-backslash-b matches end of any word ending in s (2026-05-26, run_0d65e152, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Long-running streaming responses (pipeline 5-30min) have fundamentally different failure characteristics than short responses (2-5s). Content loss during disconnect is catastrophic for long responses. Architecture should separate live-render from authoritative-source (DB re-fetch as safety net). (2026-05-25, run_bd8d8eec, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- getattr with default is safer than instance var init when the attribute is set mid-lifecycle (after __init__) (2026-05-19, run_d073033a, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- fcntl locking pattern copy from orchestrator Channel 8 — reuse proven patterns not reinvent (2026-05-19, run_bd82e6f2, auto-cultivated)
- fcntl LOCK_NB (non-blocking) is the correct choice for background channels — if lock held by another, skip gracefully rather than block the whole orchestrator (2026-05-19, run_f4cb6feb, auto-cultivated)
- External article gap analysis into implementation is highest-leverage research pattern — article provided design vocabulary and mapped 1:1 to our gaps (2026-05-19, run_a528e487, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Event-driven jobs must only consume events on success — failed jobs need retry mechanism (circuit breaker handles repeated failures) (2026-05-17, run_e07816af, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Self-contained skill scripts with subprocess-tested isolation are structurally zero-regression by design (2026-05-16, run_0bbba678, auto-cultivated)
- Multi-style-block bypass is a real class of bug for generated HTML — regex must use findall not search for style blocks (2026-05-16, run_0bbba678, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- L1-to-L2 hardening of content rules is 15min work that eliminates recurring multi-hour correction loops — cheap prevention over expensive recovery (2026-05-16, run_e7a07c93, auto-cultivated)
- RP34 covers a class of bugs unique to agentic coding — no human coder has independent shell per command, but agents do. This is an agent-specific anti-pattern worth a dedicated RP. (2026-05-16, run_5b9ca486, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Sync I/O inside async methods is invisible until load increases — _sweep_todos would have hung the event loop with 10+ pipeline-bound todos. Pattern: any Path.exists() + read_text() inside async = thread-offload. (2026-05-16, run_204079e9, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | promoted: none -->
- Todo sweep needs 3 independent try/except blocks because each data source (overdue check, pipeline runs, evolution proposals) can fail independently (2026-05-16, run_d9c21574, auto-cultivated)
- LifecycleManager cycle-modulo pattern is the cleanest integration for periodic tasks — zero new infrastructure, non-blocking by convention (2026-05-16, run_d9c21574, auto-cultivated)
- 2-variant output eliminates taste bottleneck — presenting A/B IS the taste resolution mechanism, cleaner than blocking mid-pipeline (2026-05-16, run_221f44b5, auto-cultivated)
- User-reported bugs are always rules-without-enforcement: the knowledge existed, the structural gate did not. Fix = add enforcement not add knowledge (2026-05-16, run_221f44b5, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | promoted: none -->
- Backtick symbol extraction from markdown is a strong proxy for code references — regex ` pattern catches function names without AST parsing (2026-05-16, run_50a3745c, auto-cultivated)
- Same bridge pattern (heuristic scoring + write_proposal) works for all feed channels — Ch1/Ch2/Ch4 are structurally identical (2026-05-16, run_157257ae, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | promoted: none -->
- Validation regex must be scoped to its own section — never iterate all lines matching a broad pattern (2026-05-16, run_09029225, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | promoted: none -->
- Every success with zero value path must be treated as retriable error — validate output presence not just absence of crash (2026-05-16, run_b7a6e946, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | promoted: none -->

- compare-before-write is the correct default for any function that runs every session and writes to git-tracked files — prevents commit noise from monotonically-changing fields like days_at_level (2026-05-15, run_91bda309, auto-cultivated)
- PE-4 over-scoped: empty-list rejection must be semantically targeted — patterns must have entries (what was checked) but findings can be empty (nothing found). Generic empty-list checks create false positives on clean reviews. (2026-05-15, run_91bda309, auto-cultivated)
- **DDD Cultivation: Ch6 corrections bypass keyword gate.** Corrections are pre-curated by the DailyActivity extraction LLM — their existence = DDD relevance. `cultivate_from_corrections()` falls back to IMPROVEMENT.md "What Failed" (confidence 0.4) when `_classify_lesson()` returns None. Pipeline REFLECT output still goes through keyword classifier (not pre-curated, needs filtering). General rule: never re-filter curated input with a weaker heuristic. (2026-05-15, run_76273219, PE review)
- Pipeline validator schema enforcement costs 15min but prevents honor-system drift over months (2026-05-14, run_39ca5ee8, auto-cultivated)
- Atomic write (tmp+os.replace) should be default for any JSON write in hooks — non-atomic writes are silent corruption vectors (2026-05-14, run_d73239fe, auto-cultivated)
- fcntl advisory lock + os.replace is the correct 2-layer atomicity pattern for read-modify-write on shared files (2026-05-14, run_5c3d660b, auto-cultivated)
- Gap 2 (Pipeline Metrics) was already shipped as run-analytics — always grep existing CLI commands before building new ones (LL23 pattern) (2026-05-14, run_3ac69c34, auto-cultivated)
- Adversarial sub-agent found 2 HIGH security issues (shell injection via run_id + ARG_MAX via --body) that self-review structurally cannot catch — same pattern as C011/LL11 (2026-05-14, run_3ac69c34, auto-cultivated)
- **Backend (Python):** snake_case for everything. Pydantic models with `model_config = ConfigDict(from_attributes=True)`.
- **Frontend (TypeScript):** camelCase. Always update `toCamelCase()` in `desktop/src/services/*.ts` when adding API fields.
- **API boundary:** Backend sends snake_case, frontend receives and converts to camelCase.
- **Files:** Date-prefixed for sortability: `YYYY-MM-DD-description.md`
- **Commits:** Conventional format. Co-authored with Swarm.
- **Testing:** Property-based (Hypothesis/fast-check) preferred over example-based. All new code needs tests.
- **Modules >500 lines:** Strangler fig pattern for refactoring. No big-bang rewrites.
- **Production build:** `build-backend.sh` uses glob.glob auto-discovery (no hardcoded module list — replaced 200-line manual list with 3-line auto-discovery, commit 876bf7c). New external dependencies require an entry in `pyproject.toml` + `uv lock` + PyInstaller `hiddenimports` (use `collect_submodules()`). Post-build verification: `verify_build.py` runs 38 capability checks — must pass before any release (KD03).
- **Release pipeline:** `prod.sh` (720 lines) handles version bumping, build, verify, and package. `VERSION` file is single source of truth for version number. `sync-version.sh` propagates to all manifests.

## Runtime Traps
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- DDD completeness bug had TWO coupled halves — CREATE not scaffolding ② Knowledge/ (spec §3.6) AND recall never reading it — the write/read mismatch class (same as pollinate). Fixing only the scaffold would have left persisted reference material silently unrecallable. Rule: when a skill routes a WRITE to a location, verify the READ path (recall) actually scans it — a write target nobody reads is worse than no write. (2026-07-22, run_9fead2ef, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A resolver change can silently break a downstream SECURITY guard: prompt_builder's shareable-doc scanner had a symlink-escape guard requiring doc parent == project_dir. Routing through ddd_path made migrated docs resolve to 2-understanding/ → the guard rejected every migrated shareable doc. The subagent flagged it; fix = relax 'direct child' to 'inside project tree' (commonpath) WITHOUT reopening the symlink escape (mutation-verified both the share-works AND escape-still-rejected cases). (2026-07-22, run_3a636c88, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- PRUNE-before-RELOCATE ordering matters: a migrator that removes legacy stubs (keyed on OLD paths) then moves surviving content to NEW paths must prune FIRST — relocate-first carries the legacy stub into the new dir where the old-path prune can no longer see it. (2026-07-22, run_cfb0f28f, auto-cultivated)
- Strangler READ (new-then-old) vs WRITE (always-new) avoids split-brain in a per-item layout migration, BUT lock/sidecar files must follow the RESOLVED path: ddd_orchestrator locked at project-root while the file resolved into 2-understanding/ — the lock protected nothing. Co-locate via resolved_path.with_name(), never a hardcoded join. (2026-07-22, run_cfb0f28f, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-1 F3 was the highest-value pre-code catch: a redline case with an unrunnable evaluator always-skips=always-passes (an unenforceable red-line). gate_redline requiring a runnable evaluator closes an evasion no happy-path test would surface. (2026-07-21, run_21490939, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-07-21 -->
- When routing a NEW input class (raw user prompts, disproportionately CJK) through an EXISTING shared floor (is_quality_lesson, tuned for English-distilled lessons), the floor is now exercised on an input distribution it was never validated against — verify the floor holds for the new distribution, do not assume reuse is safe. (2026-07-20, run_4443a967, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Two similarly-named sets governing DIFFERENT actions (EVERGREEN_TYPES=age-decay vs _KEEP_TYPES=reclaim-strip) must be documented as deliberately-distinct at the definition, or a future reader unifies them and re-breaks one path — they differ on pitfall by design. (2026-07-20, run_123652ae, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Unifying two writers by routing one through the others admission path (apply_to_ddd) beats patching two parallel dedups — but verify the routed path is async-safe (asyncio.to_thread for the flock-holding sync call awaited on the event loop). (2026-07-20, run_4c5f81ce, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- C041 discipline held: the SAME skeptic's CRITICAL-2 ('aim.json missing') was FALSE — it ran find in the SOURCE repo (/Desktop/.../swarmai/Projects, stale) not the WORKSPACE (~/.swarm-ai/SwarmWS/Projects, real). I verified with absolute paths before acting (C040 tree-confusion guard). A sub-agent's claim about my system is an input to verify, not a conclusion to act on — even when the same agent is right about everything else. (2026-07-19, run_597f4ed1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A shell temp-root guard using TMPDIR-percent-slash-star degenerates to slash-star (matches any absolute path) when TMPDIR is exported-but-empty; normalize to a concrete non-empty value BEFORE the case-match. (2026-07-19, run_bba97015, auto-cultivated)
- An isolated-env harness must classify 3 SSE outcomes, not 2: real-token=PASS, no-creds/no-completion=SKIP (AC4), non-auth-error OR missing-session_start=FAIL. Binary pass/fail on the stream is exactly where the false-green hid. (2026-07-19, run_bba97015, auto-cultivated)
- Adversarial gate exposed a false-green in my OWN proven prototype: the throwaway harness reported FULL 6/6 GREEN but Phase 4 matched bare data:/event: SSE framing frames, never verifying real generation (the isolated env has no Bedrock creds, so generation actually errored with CREDENTIALS_EXPIRED). I trusted the green — authorship trap. An SSE success check MUST assert the actual expected token, never bare data:/event: which also match framing AND error frames. (2026-07-19, run_bba97015, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Verify the sub-agent finding before acting (R16b/C041): the Gate-2 security agent reported 4 HIGH; I confirmed 3 as real (mine, fixed) and reclassified 1 (#4 live-db) as pre-existing-architecture that my atomic-import already de-fanged — did NOT blindly fix all 4 nor smuggle the deep redesign into a bugfix (PIT41). (2026-07-18, run_037a02af, auto-cultivated)
- The stale-marker trap: a state marker (in-progress flag) whose PRESENCE triggers a destructive action must NEVER be the sole authority — a process SIGKILL between marker-write and marker-clear makes real data look like debris. The authority must be the DATA ITSELF (populated DB), not a flag. Atomic import is what MAKES this reliable: debris=empty DB, so populated=genuine — the fixes compose (each enables the other). (2026-07-18, run_037a02af, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- [pitfall] **A boot-time / per-request SQLite integrity probe MUST NOT use `PRAGMA quick_check` or `integrity_check` — both are O(db-size)** (they read every b-tree page to validate structure). On a GB-scale `data.db` quick_check takes *tens of seconds*, and when run synchronously inside lifespan startup it delays uvicorn port-bind → the frontend times out with "not responding". The "quick" adjective is a trap: quick_check is faster than integrity_check but is NOT O(1)/O(schema); `integrity_check(1)` only caps the number of *reported* errors, not the scan, so it is EQUALLY O(n). A cheap schema-only probe (`PRAGMA schema_version` + read `sqlite_master`) is also NOT a substitute — live-probed, it MISSES a torn page-1 schema region (reports intact when it isn't). **The correct pattern: add NO separate integrity pre-probe at all — the schema migration that runs at boot IS the integrity gate.** A malformed/torn db makes the first migration query raise `sqlite3.DatabaseError` (that IS the crash-loop A2 defends against); catch it there → purge + re-seed + retry-once. Load-bearing subtlety (see the run_2d3417d9 except-order entry below): `sqlite3.OperationalError` IS A SUBCLASS of `sqlite3.DatabaseError` — a locked-but-valid db raises OperationalError and must be caught FIRST and re-raised WITHOUT purging, or a momentarily-locked valid db gets deleted (data loss). Source: `backend/main.py` `_ensure_database_initialized` / `_init_db_bounded`. (2026-07-18, run_4326397d, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- Count what actually materializes, not a proxy: n_findings counted '"title"' substrings, but a budget-truncated JSON block yields 0 real todos downstream — align the reported count to the actual parse (_count_parseable_findings) so the report never over-states. (2026-07-18, run_271c39df, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-07-18 -->
- Gate-2 MEDIUM: a bare substring filter (test in fp) false-excludes real files (attestation/latest) — segment-anchor path filters, never bare substring. The any-repo skill makes latent-on-SwarmAI a real risk elsewhere. (2026-07-18, run_4344d341, auto-cultivated)
- edges=0/entry_points=0 was TWO independent export bugs: get_module_map SELECT silently dropped is_entry_point (column omission = silent None), and no edges key was ever assembled. The DB was always correct (25K edges, 11K entry nodes) — pure export-layer loss. (2026-07-18, run_4344d341, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Line-resolution of anchors is theater (signature-first design, line-drift false-rejects) but FILE-existence is NOT theater — it catches wholesale fabrication at ~1 Path.is_file() cost, with the same containment guard (reject absolute/../ escape) as the mermaid resolver. (2026-07-18, run_9a9e314c, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Python except-order is a data-loss trap when the narrow exception subclasses the broad one: except DatabaseError before except OperationalError made the OperationalError branch dead code → a locked-but-valid DB classified corrupt → destroyed. Subclass clause MUST precede parent; a destroy-and-recover path needs a real-lock-survives test. (2026-07-18, run_2d3417d9, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-07-18 -->
- A relaxed validator must preserve its security intent by construction: _validate_repo_path accepts monorepo members but still fail-CLOSED rejects non-git dirs (timeout/OSError falls through to rejection, symlink-resolve first). (2026-07-18, run_a9fe5ad3, auto-cultivated)
- Disambiguation logic must be single-sourced across all consumers — two functions deriving package names independently (raw vs path-suffixed) WILL drift; derive from one source keyed by the unique field (root). (2026-07-18, run_a9fe5ad3, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- [pitfall] **"Is my fix in the deployed daemon binary?" — verify by BYTECODE symbol, not mtime and NEVER by grepping the binary for comments/strings** — the recurring C038/R16b trap. mtime>source is necessary-not-sufficient; grepping `python-backend` for my code's comments is a FALSE-NEGATIVE (PyInstaller strips comments in `.pyc` → 0 hits reads as "not deployed", misled 3×). The DEFINITIVE method: extract the module bytecode from the onedir binary and look for a NEW symbol my change introduced. `from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader` → `CArchiveReader('~/.swarm-ai/daemon/python-backend').extract('PYZ.pyz')` → `ZlibArchiveReader(that)` → `.extract('core.<module>')` → `marshal.loads` the code object → recurse `co_consts` scanning `co_names`/`co_varnames` for the new variable/function name my fix added. Symbol present → the running daemon genuinely has my code (comment-strip-proof). NOTE: skill scripts (`backend/skills/**`) are NOT bundled in the daemon binary — verify those via the projected `~/.swarm-ai/SwarmWS/.claude/skills/**` copy (projection re-syncs on daemon restart), not the binary. This is the concrete method C038's escalation kept demanding. (2026-07-18, run_89e28075, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- SwarmAI TECH.md uses ## Codebase Location → **Local:** not **Repo Path:** — the marker the endpoint expected exists in NO project. Verify the actual doc format against real files, never assume a marker string. (2026-07-17, run_19eecc9f, auto-cultivated)
- Pattern ORDER + re.MULTILINE + first-match is content-incidental safety: a labeled path wins over a bare-backtick line only because search() returns first match — documented in-code so a future findall/reorder refactor cannot silently reintroduce wrong-path resolution in a 400K TECH.md. (2026-07-17, run_19eecc9f, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-07-17 -->
- Single-source hash across a C046 boundary: define the hash ONCE in the skill (_spec_content_hash), stamp it at EXPORT (where domains+flows+steps are all in hand), and have core READ-only. freshness.py never computes a hash so the two-writer drift Gate-1 F1b flagged is structurally impossible. Hash the RENDERED skeleton (marker elided) so what-changes-the-spec equals what-bumps-the-hash by construction - covers flows/steps for free. (2026-07-17, run_fe26ed6c, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- the php Gate-2 guards (reader_exclusion_parent_types, distinctive-name, member_access_types) transfer directly to java/c#/c — same node-type-reuse trap (all read consts as bare identifier). Reusing a hard-won guard family across languages is the payoff of the descriptor architecture. (2026-07-17, run_0f977b9f, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A silent dir-skip that short-circuits BEFORE coverage-hole recording violates the never-invisible (O030) invariant — for a coverage-honest engine, skips must be tool-reserved-name OR path-scoped, so a skipped generic dir is never silently lost with no signal. (2026-07-17, run_f64f6031, auto-cultivated)
- For a code-intel engine that runs on ARBITRARY repos, a directory added to a bare component-skip set MUST be tool-reserved (node_modules/.git/target/dist), never a plausible source-dir name. Gate-0 caught `_internal` (legit pydantic/claude_agent_sdk convention); Gate-2 caught `binaries` (a repo can have top-level binaries/ source). Both would silently drop real source. The safe lever for the PyInstaller bundle was the parent path src-tauri/binaries (path-scoped), not the generic name. (2026-07-17, run_f64f6031, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Ruby reuses the `constant` node for BOTH a class name (Foo.new) and a const value — value-ref needs a receiver-position guard (const as first child of a call), distinct from the trailing-member guard. Grammar node-type reuse is a per-language false-positive trap. (2026-07-17, run_13667da9, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Scoping discipline held: kept core code_intel (parser/graph_store/json_exporter) untouched per Gate-0 reframe; detector is skill-layer only; per-package v3 gen + fan-out orchestration explicitly deferred to a follow-up run rather than scope-creeping this one. (2026-07-17, run_693e08de, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Reviving a long-dead code path surfaces LATENT bugs in that path — the AST extractor had a missing DEFINITION_TYPES javascript key invisible for months because tree-sitter was dead and everything fell to regex. When re-enabling a dormant path, budget for defects the dormancy masked, and pattern-grep the whole config (all 13 LANGUAGE_MAP langs) for the same gap class. (2026-07-17, run_2e46f2af, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Identity-based dedup (path+mtime) sidesteps the PIT14 string-shape whack-a-mole class entirely — a regenerated image advances mtime so it is never falsely deduped, with zero content matching. (2026-07-13, run_2f8f8726, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- assess_decay RETURNS transitions but does NOT mutate entries — the canonical apply pattern lives at ddd_orchestrator.py:914 (t.entry.decay_state=t.new_state). context_health_hook's MEMORY+KNOWLEDGE paths both omitted it. When copying a pattern, copy the WHOLE pattern (Gate-1 caught I referenced only line 914, not the 929 active_entries filter). (2026-07-13, run_b3081198, auto-cultivated)
- Root cause was the missing apply-loop, NOT the wrong decay clock — Gate-0 skeptic refuted my 'created vs last_ref' framing; empirical execution of the REAL _run_memory_lifecycle on the pre-compaction .bak proved assess_decay logged 9 transitions but persisted 0 (entries never mutated before inject_entry_metadata). Always execute the real code path on real data before trusting a clock/threshold hypothesis. (2026-07-13, run_b3081198, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- [pitfall] **A large `data.db` (main file) is almost always REAL data, NOT bloat — the `messages` + `messages_fts` tables dominate, and VACUUM is useless on it** — When `~/.swarm-ai/data.db` looks alarmingly large (observed 1.2GB, 2026-07-13), do NOT assume corruption/bloat/a leak and do NOT reach to "fix" it. Measured breakdown (read-only `dbstat` via `sqlite3 'file:...?mode=ro'`): `messages` ≈512MB + `messages_fts_data` ≈400MB = **~75% of the DB is the chat corpus + its FTS5 index — genuine data under the 90-day TTL** (242K rows). Key facts that kill the usual wrong moves: (1) **freelist was 0.7%** → `VACUUM` would reclaim only ~9MB; it does NOTHING for a corpus-heavy DB (VACUUM reclaims *deleted*-row holes, and TTL delete + autocheckpoint already keeps holes near zero). (2) **The main DB does NOT shrink on delete anyway** — SQLite only returns freed pages to the freelist, reused for future inserts; the FILE stays its high-water mark without an explicit VACUUM. (3) **`messages.expires_at` is baked at INSERT time** (`sqlite.py:265` = `now + TTL_SECONDS`), so lowering the `TTL_SECONDS` constant only affects FUTURE rows — it never retro-shrinks existing data; to shrink NOW you must delete by age directly. (4) **Do NOT confuse this with WAL bloat** — the "code_intel 2.7GB" incident was the `-wal` FILE (see WAL pitfall below), a different file and a different (already-fixed) cause; reading that stale note and blaming the main DB is the C038/C040 stale-log-as-current-state trap. **The only real lever on main-DB size is data RETENTION (TTL), not defrag.** Diagnostic: `sqlite3 'file:~/.swarm-ai/data.db?mode=ro' 'SELECT name,SUM(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC LIMIT 10'` + `PRAGMA freelist_count`. (2026-07-13, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [pitfall] **SQLite WAL FILE never shrinks on its own — only `wal_checkpoint(TRUNCATE)` reclaims it; PASSIVE autocheckpoint does NOT** — A `journal_mode=WAL` DB with only default (PASSIVE) autocheckpoint reuses WAL frames but NEVER shrinks the `-wal` FILE on disk. After a large bulk write (e.g. a full re-index) the file stays bloated forever — observed `code_intel.db-wal` at **2.73GB vs a 64MB DB**, byte-identical across a deploy (a daemon restart does NOT truncate it either). This is static bloat, NOT an active leak. Reclaim is `PRAGMA wal_checkpoint(TRUNCATE)` (data-safe + online: flushes committed frames to the main DB first, then zeroes the file; `busy=1` if a reader pins the WAL → non-fatal, retry later). Fix shipped in `code_intel/graph_store.py::checkpoint_truncate()`, called at the tail of ALL 4 bulk-write paths (`bulk_insert`, `incremental_update`, `context_health_hook.py` session-start refresh, `code_intel_reindex.py` job) + `PRAGMA wal_autocheckpoint=2000` to bound steady-state. **Diagnostic when any `*.db-wal` is huge:** `sqlite3 x.db 'PRAGMA wal_checkpoint(TRUNCATE)'` reclaims it live; if it recurs, a write path is missing a checkpoint call — grep for every `commit()`/`rebuild_fts()` that isn't followed by a checkpoint. (2026-07-13, run_9b8d9e91, source:manual)
- A function returning None-on-both-match-and-miss makes the caller log false success (_apply_report always logged Resolved thread); returning a bool turned silent-failure into an honest not-matched log. Any consume/mutate helper whose caller reports outcome should return a match/success bool. (2026-07-12, run_473a0b7c, auto-cultivated)
- Gate-2 highest-value catch was not a code bug but a TEST GAP: my new tests exercised the scoped path but none PROVED scoping BLOCKS a false-positive. A guard needs a test that goes RED when the guard is removed — mutation-verified (force whole-file scope → RED). (2026-07-12, run_473a0b7c, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 caught that fix F (reload unsaved-guard) STOPPED the silent overwrite but left the reload intent UNREACHABLE — the guard routed to a discard-and-close modal. A guard that blocks an action must provide a forward path to that action; blocking-without-continuation is a half-fix. Fixed via a dedicated reloadPending mode + Discard & Reload. (2026-07-12, run_9db46483, auto-cultivated)
- [guideline] **Read the Tauri webview's localStorage sqlite to OBSERVE running-app state (black-box webview debugging channel)** — I can't execute JS inside the running Tauri WebKit webview, but its localStorage is a readable sqlite DB → use it as an in-app observation channel (the "instrument the running app" arm of the Debugging Rule). Path: `~/Library/WebKit/com.swarmai.desktop/WebsiteData/Default/*/*/LocalStorage/localstorage.sqlite3`. Read a key: `sqlite3 "$DB" "SELECT hex(value) FROM ItemTable WHERE key='<key>';"` then decode **UTF-16-LE** (values are UTF-16 blobs). ⚠️ **CRITICAL trap:** the LIVE origin dir is **`com.swarmai.desktop`**, NOT the stale `swarmai/` dev-server origin (which holds months-old data — reading it gives false/ancient values). Pick the DB whose `-shm`/`-wal` mtime matches the current daemon start time; data can sit in `-wal` pre-checkpoint (sqlite reads it automatically). Pattern for a hard-to-observe metric: plant a temporary probe in code that does `localStorage.setItem('key', JSON.stringify(snapshot))`, deploy, read it back here, then remove the probe. Evidence (run_6a148449, 2026-07-12): read `swarmai-zoom-level=1.0` to FALSIFY a zoom root cause, and read a planted xterm-metrics probe (measured cell width 6.62px Menlo-fallback vs true glyph 6.47px JBM) to pinpoint terminal selection drift — after 2 prior inference-based fixes failed. (2026-07-12, run_6a148449, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- prune must delete by content-signature not path (data-loss) (2026-07-12, run_693df058, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A prior Gate-0 verdict ('empty section dir = cargo-cult') was REVERSED once XG named the real consumer (SwarmAI-maintenance-follows-standard + AIM-export-fidelity). 'Zero code reads it' is not the only consumer test -- a human/agent following a legible standard IS a consumer. But: the honest framing separates real-now (maintenance legibility) from aspirational (export step doesn't exist yet); don't assert a machine consumer that isn't there. (2026-07-12, run_011649bd, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Section-selection is a HARD prefilter to body-BM25 slicing in recall_context: if _keyword_section_scores returns {}, _slice_section_entries never runs. Removing a token from the index can silently make a query return nothing though the token exists in the body. Verify the end-to-end recall_context path, not just the scorer. (2026-07-12, run_2f4d92da, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A hoisted guard that fixes a latent bug should re-derive the invariant it protects: moving the binding.repo bare-name check from else-only to unconditional made db_path safe AND made a stale comment true (the comment claimed repo was already validated bare, but that only held on one path). R7 docstring/comment co-update. (2026-07-12, run_f8ef133b, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Honest-scope labeling is the load-bearing part of a prose fix: the s_internal-brazil block explicitly says 'prose convention, not an enforced gate — works only if read+heeded'. Gate-2 flagged that as the thing most important to get right (a false 'this prevents X' would be the worst outcome). Prose that admits its own weakness > prose that overclaims enforcement. (2026-07-12, run_7a5b35a1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- R29 held under co-mingling: a parallel run (run_3467799d: skill_manager.py/SELF.md/s_skill-builder/new test) shared the tree; staged ONLY my explicit paths, never -A, leaving theirs intact. (2026-07-11, run_ce2a1b6d, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- [pitfall] **Tauri IPC binary decode: `Uint8Array.from(anArrayBuffer)` returns an EMPTY array** — an ArrayBuffer is not iterable/array-like, so `.from()` drops every byte silently. `tauri::ipc::Response` (raw bytes) arrives in the webview as an **ArrayBuffer** (NOT number[] / Uint8Array). Correct universal decode: `new Uint8Array(chunk)` — treats ArrayBuffer as a view, copies number[]/Uint8Array, all shapes identical. Symptom in this incident: integrated terminal rendered 100% blank (couldn't type, no prompt) — 3 wrong code-read diagnoses before a `node -e` shape-probe found it. Test-escape: the unit test only ever fed a Uint8Array, never a real ArrayBuffer (the production shape), so it passed CI (O009 — assert against real data SHAPE at both edges, not the shape you assumed). Fix + regression test: `desktop/src/services/pty.ts` + `pty.test.ts`. (2026-07-12, run_3a0cb64b, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [pitfall] **A GUI/Finder/Dock-launched macOS .app inherits the launchd session env, which has NO `$TERM`** — portable-pty seeds a child from `std::env::vars_os()` (the parent's env snapshot), so a spawned shell inherits that TERM-less env → zsh/ls/git detect no color-capable terminal and render everything flat monochrome (command vs output visually indistinguishable). The emulator (xterm.js) is authoritative for TERM → force-inject `TERM=xterm-256color` + `COLORTERM=truecolor` in the Rust pty spawn (`terminal.rs build_pty_env()`); set LANG to a UTF-8 default ONLY if neither caller nor inherited env has one. Skeptic-corrected SCOPE (do not over-attribute to TERM): backspace works WITHOUT TERM (zsh ZLE binds `^?`→backward-delete-char by default) and PATH is rebuilt by the `-l` login shell — ONLY coloring was the real TERM defect. (This is the root-cause parent of the "verify your forced value wins" nuance below.) (2026-07-12, run_3a0cb64b, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- When forcing an env var that has an inherited value, verify the library actually lets your value WIN (portable-pty env() dedups by key → overwrites). And a not-if-absent guard must check the INHERITED env too, not just the caller map, or the guarantee is hollow (Gate-2 LOW). (2026-07-11, run_3a0cb64b, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A Rust default that sets a child's cwd MUST verify the dir exists (is_dir) before use — portable-pty passes cwd to chdir which fails ENOENT on a missing dir, so a not-yet-created $HOME/.swarm-ai/SwarmWS would make every default terminal fail to spawn on a fresh machine. Fall back to $HOME (always present). The old code was safe only by accident (it never set cwd when none was passed). (2026-07-11, run_1ad103f1, auto-cultivated)
- A Rust cwd default that omits an existence check is a fresh-install ENOENT trap: portable-pty passes cwd to the child's chdir, which fails if the dir is missing. Multi-specialist (correctness+operational) confirmed it. Fix: is_dir() check with $HOME fallback. The lesson: any default PATH resolved in code must degrade to a guaranteed-present fallback, never a maybe-missing subdir. (2026-07-11, run_1ad103f1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- One-shot ephemeral context must clear on EVERY send path, not just the primary: the drain-queue path read terminalContextRef without clearing so a buffer could ride a later drained message. When a ref is consumed-once, grep ALL readers and clear at each consumption site. (2026-07-11, run_8eedc1d4, auto-cultivated)
- Vendoring an upstream cross-platform module SILENTLY DROPS its platform-conditional behavior: copying Tnze/tauri-plugin-pty as app-commands dropped its Windows powershell.exe branch, so DEFAULT_SHELL hardcoded /bin/zsh = Windows spawn failure. When vendoring, diff the upstream for every platform branch and re-implement each; never copy only the happy path. (2026-07-11, run_8eedc1d4, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- mtime-freshness soundness: copy2 PRESERVES source mtime (so an edited file's dst is strictly older -> re-copies), but max-mtime alone MISSES deletions -> pair it with a relative-path-SET check. Walk FILE mtimes never DIR mtime (copytree sets dir mtime=copy-time -> would always-skip). (2026-07-11, run_bf4cb46e, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Split-brain deploy caveat: system_jobs.py is compiled into the PyInstaller daemon binary (schedule changes DORMANT until rebuild+restart), while user-jobs.yaml + state.json are read live. Never claim a system-job schedule change is 'shipped' on commit — it needs s_swarm-build + daemon restart (XG approval). (2026-07-10, run_89d7b5b8, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 caught CLASS-A test-theater the trivial profile would have shipped: the ONE test guarding the empty-output invariant was skipif-gated behind a browser it did not need AND had a wrong mock signature (raised TypeError not Html2PdfError) → it never ran under the venv. A skipif on a mock-only test = silent zero coverage. Tell: a load-bearing invariant test that skips on the machine you run it on. (2026-07-08, run_8debb0fe, auto-cultivated)
- HTML→PDF success MUST be verified by os.path.getsize(out)>0, never by exit code — the whole failure mode is exit-0-but-no-output. Mutation-proven: disabling the getsize guard makes the empty-output test go RED. (2026-07-08, run_8debb0fe, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- [pitfall] **Slack channel 免@ thread-follow 结构上过不了 — `groups:history` 是 HIGH-risk + Socket-Mode 要求 websocket 托管在内部 AWS 账号** — In a Slack CHANNEL the bot replies to an @mention but a non-@ follow-up in the SAME thread gets no reply. Root cause is NOT our code: `_should_reply`+thread_follow is fully wired (engaged thread key IS stored in channel_sessions, message_count=2, re-engages the moment a message arrives). The follow-up is dropped at Slack's EVENT-DELIVERY layer — never reaches `handle_inbound_message` (zero `Inbound message` log; DB shows every stored channel inbound starts with `<@bot>` = all mentions). Slack rule: to receive non-mention channel messages you must subscribe `message.channels`/`message.groups`/`message.mpim` events AND hold the matching history scope; `channels:history` alone = READ-history-via-API, NOT event push. DM contrast proves it: our `im` has `im:history`+`im:read` → non-@ DMs arrive fine; the channel only has `app_mentions:read` for events. THE WALL: adding scopes triggers a Slack **Attestation** — `groups:history` (private-channel/"group message") is graded **HIGH Risk**, AND (because we use Socket Mode) the attestation REQUIRES the websocket client be hosted in an internal Isengard/Conduit AWS account. SwarmAI is a local Mac daemon → structurally cannot satisfy this → `groups:history` will not pass. Connected: ANY new scope re-triggers the attestation + AWS-hosting requirement, so even medium-risk paths (public-channel `channels:history`, `mpim:history`) hit the same wall; the only attestation-free path is adding NO new scope. **STANDING DECISION (XG):** private-channel免@thread-follow abandoned as structurally impossible for the local daemon; working model for channels = @mention per turn (also aligns with 宁缺毋滥 / explicit-invoke persona — auto-reading every channel message is noise+privacy surface). thread_follow code stays (harmless, dormant behind the un-grantable scope). (2026-07-07, run_45187d49)
  <!-- ref:0 | last:none | decay:active | source:manual -->
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A value that decides routing AND identity (reply target == session key) MUST be computed ONCE and passed, never recomputed at each site — two computes with even slightly different defaults (chat_type im vs empty) is a drift-trap (single-source class). (2026-07-07, run_45187d49, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-07-07 -->
- cultivate_from_decisions routes to safe-append sections so it would AUTO-APPLY; reusing it for a new source needs a source_stage never-auto-apply guard, else the human-gate is silently defeated. Verify a reused path's auto-apply eligibility before assuming reuse is safe (R15/R25). (2026-07-07, run_4261f1a3, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A read-only file grant has a non-obvious escape: is_file() follows symlinks and realpath resolution on the OTHER side means the grant lands on the symlink TARGET, not the declared file. Any exact-path allowlist built from a scanned dir must reject symlinks + assert containment (dirname(realpath)==realpath(dir)). (2026-07-07, run_c220f153, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Security redaction on a streaming append-only path must be mutation-proven per-invariant: disabling opener-withholding → Bearer/secret-split tests go RED, proving the withholding is load-bearing not decorative (GUI96). (2026-07-06, run_3118be8f, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- I confirmed the on-disk file the daemon reads is at 900 (2026-07-04, 03c8e26e-4144-421f-bdcf-e19662d826fa, decision)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Admitting a taxonomy value requires wiring BOTH the metadata list AND the report bucket, else admitted-but-invisible (Gate-1 #1 BLOCK). The durable outcome is a stronger invariant: golden_set.yaml categories and DIMENSIONS buckets are now BIJECTIVE (0 orphan, 0 phantom) — previously runtime_health was bucket-only. (2026-07-03, run_a04ab388, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Deploy-split observed correctly (R16b): the deployed binary predated all 3 commits, so the brief global-widen never reached production — verified by binary-mtime vs git-log, not inferred. ddd-retire CLI runs from source so the feature is live now; autonomous-path safety is by-construction so it holds regardless of daemon rebuild. (2026-07-03, run_748f14a7, auto-cultivated)
- Safe-by-construction beats guard-you-must-remember: the fix made prose-matching OPT-IN (autonomous paths use the narrow matcher and literally cannot see prose) rather than retrofitting the evergreen guard into 3 more call sites. The gate that must be remembered at N sites will be forgotten at site N+1. (2026-07-03, run_748f14a7, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Deploy-split is now a KNOWN pattern to state up front: CLI code (artifact_cli, shelled from source) is live immediately; module CONSTANTS read by the daemon's autonomous jobs (decay thresholds) need a rebuild+restart. Same change, two deploy latencies — say which is which at delivery. (2026-07-03, run_186a5f15, auto-cultivated)
- Gate-2 correctness found the guard's OWN blind spot: retire called is_keep_class WITHOUT evergreen_sections, so the --force protection was dead for Open Threads/Standing Preferences — the exact permanent-knowledge the guard existed to protect. A protection guard must be checked for parity with the autonomous path it mirrors. (2026-07-03, run_186a5f15, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A fail-safe except branch turned a hard bug into a SILENT one: no_wedged_sessions crashed on every run for 166 runs but reported all-pass — a muted health check is worse than a missing one. Lesson: any fail-safe (except -> benign default) on a CHECK must emit a distinguishable detail (this one did: "session scan unreadable") AND that detail must be surfaced/alerted, not swallowed into a green. (2026-07-03, run_dc86c466, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Single-source predicate (is_terminal_run in artifact_cli, imported by proactive) — reused the existing cross-module import pattern (_checkpoint_reason_has_true_trigger) so the two detectors cannot drift (R25). (2026-07-03, run_51cdbbdb, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The fix was CONVERGENCE not addition (R25): deleting the deny special-branch so approve+deny share one streaming path is safer than adding a parallel path — deny inherits every race/cross-tab guard approve already has, zero new surface. (2026-07-02, run_ec351cc9, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 抓到 HIGH race: sync 函数后 await 再 setState(prev=>) 读的是 flush 前的 prev — autoDiff 被 path-match guard 静默丢弃. 修法=把 flag 传进那个 setState 的唯一调用点, 而非事后 patch (2026-07-02, run_1c91e669, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Fail-open on uncertainty is correct for a disk-verification gate: missing/unreadable/relative-path loci WARN, never BLOCK. Blocking on a can't-verify locus would false-block every CI/other-machine run (the file legitimately isn't at that abs path there). The gate only BLOCKs on the one unambiguous signal: file READABLE and the durable marker GONE. This is the O030 lesson (a guard's disaster-recovery bound must not guillotine the normal path) applied to a verification gate. (2026-07-02, run_c5935199, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The deliverable is armed-but-OFF: the weekly job is enabled:false. It delivers zero value until XG activates it + the daemon picks up the job — that activation is the real 'is it working' moment, correctly deferred (activating a scheduled job is a state change needing approval). The report-generator + gating are proven; only the scheduling is pending. (2026-07-02, run_38f03634, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- detect-secrets has two silent-zero footguns that make a real secret scan clean: (1) `scan <dir>` walks only git-TRACKED files (0 on untracked) → need --all-files; (2) an ABSOLUTE path arg returns 0 findings → must run cwd=root + relative subdir. Both invisible without a coverage assertion (assert emitted version + plugins_used, fail closed). (2026-07-01, run_4b007e00, auto-cultivated)
- detect_secrets.VERSION / __version__ are BOTH None on a real 1.5.0 install — a getattr-based version guard is dead code that never fires, leaving the exact fail-open it claims to prevent. Use importlib.metadata.version() and fail CLOSED on unknown. (Same class: a security check that can't fire is a fake floor.) (2026-07-01, run_4b007e00, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Live C040 recurrence THIS run (grepped a source-repo .context path that only exists in the workspace) is itself the proof that the ~200-tok Self-Identity-Anchor stopgap would not work: the Anchor was already in my context and I still did it. Adherence failures are not fixed by more prose-in-context — only by a gate or by the pattern not being worth gating yet. (2026-07-01, run_67508ac4, auto-cultivated)
- The disciplined outcome of an open thread can be CLOSE-with-no-code. The evidence bar (>=3 occurrences) was at 1; the dominant pattern is skeptics CATCHING me, not being blind. Building a mechanism at 1/3 IS the C042 trap. NO-GO is a real deliverable. (2026-07-01, run_67508ac4, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- AutoSDE port worked as a prose->code-constant transform: the confidence>=7 rule that lived in deliver.md:507 as prose is now CONFIDENCE_GATE_THRESHOLD + _blocked_findings, enforced at both gate sites — prose-to-gate is the highest-value shape of AutoSDE borrowing (P7). (2026-07-01, run_7583af5f, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-07-01 -->
- [pitfall] **`_force_kill` wrapper cleanup must be concurrency-safe (null-the-ref-before-await)** (2026-06-30, run_02bc6dd1, commit 1dba3ddd)
  <!-- ref:0 | last:none | decay:active | source:manual -->
  `session_unit._force_kill` is the kill primitive shared by the lock-free PID watchdog (3 sites) AND the `_lock`-holding `_crash_to_cold_async`/`kill`. Its wrapper cleanup originally did `if self._wrapper is not None: await self._wrapper.__aexit__()` — the `await` is a context-switch point, so two concurrent `_force_kill` calls both passed the not-None check and both invoked `__aexit__` on the same **non-reentrant anyio wrapper** → cancel-scope double-free (TOCTOU). **Fix pattern (reusable for any read-then-await on shared mutable state): capture the ref + null the field in ONE await-free block, THEN await on the local.** `wrapper_ref = self._wrapper; self._wrapper = None; if wrapper_ref: await wrapper_ref.__aexit__()`. First caller wins, racer sees None and skips. Adds no lock (watchdog stays lock-free). Bonus: `_wrapper` is now guaranteed None even on the `__aexit__` exception branch. ⚠️ Committed, NOT yet deployed (in daemon binary → needs build+restart).
- recycle-storm = route-A structural gap, now documented: hard_cap sits behind 4 alive guards by correct design, so it is structurally unreachable during a storm where backend is always streaming/cold/resuming. hard_cap protects SOLO-stuck only; storm needs B-1/B-3. Both verified against real backend-daemon.log + frontend.log + code. (2026-06-30, run_75e15f8c, auto-cultivated)
- Authorship-trap, design-layer variant: I designed a NEW primitive (turn_seq) when the correct one (_send_generation: turn-scoped, +1 only in send(), no-reset-on-COLD, already used internally as cross-recycle staleness guard) already shipped. The skeptic found it by reading session_unit.py; I had not. Same kernel as code authorship trap — I trusted my design over what the code already provided. (2026-06-30, run_75e15f8c, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- 工具坑: 裸npx vitest run path不加载vite.config.ts的jsdom→122假失败, 必须npm run test:run。mutation改源码后git checkout会连真修复一起revert, 必须Edit回写不用checkout。 (2026-06-30, run_251ea3ee, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- The cross-process singleton clobber is real and bit me live: a file-level golden_set edit read as applied, but the daemon EvalService singleton (loaded at the 16:18 deploy) held the old refs and a later update_case write flushed the stale version back — data edits to singleton-owned files MUST go through the live daemon endpoint, never a direct file write. (2026-06-30, run_3b4c18e1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Soft-delete (tier=archived) does NOT clean the file — archived rows linger forever with no purge path; physical removal needed a new resurrection-proof primitive, not a config flag. (2026-06-30, run_110678fb, auto-cultivated)
- A singleton EvalService means out-of-process deletes (CLI) get resurrected by the daemon flushing its stale in-memory _cases — the delete had to run via a live daemon endpoint so the singleton updates in the same act. (2026-06-30, run_110678fb, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- When a guard must answer 'is X a real command word vs a quoted mention', the robust check is: shlex-tokenize, skip fully-quoted tokens (start+end with same quote char), match the bare tokens — and fail-CLOSED (treat as real) on shlex ValueError. The cheap anchored regex should short-circuit BEFORE shlex so the common non-matching path pays only one regex. (2026-06-30, run_5511508d, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A fail-closed error must be emitted on the SAME stream the consumer error-handler reads — my error went to stdout but the orchestrator guard reads stderr + feeds stdout to json.load(artifact_id). Route new error paths to where existing ones go. (2026-06-29, run_3caef1d3, auto-cultivated)
- A fail-closed guard on a primitive can make the COMMON path worse than the bug — if it fires on the documented call shape. The contamination guard halted EVERY multi-run publish because all 8 stage docs omit --run-id; the real fix needed BOTH the guard (backstop) AND threading --run-id through the docs (remove the trigger). Grep how callers actually invoke before guarding. (2026-06-29, run_3caef1d3, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Fresh-user-audit caught that deleting the template did NOT orphan the API-key step — api_key was never a valid .env field (moved to config.json/Settings UI); the deleted ANTHROPIC_API_KEY line was itself a startup landmine. Removing misleading config improves correctness, it does not drop a real onboarding step. (2026-06-29, run_75d12990, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A metric command that shells out must handle EMPTY input explicitly: portable fix = awk-sum per-file counts (skip wc total line) + Python empty/0 guard rendering "" so a WARN surfaces, not a confident-wrong 0. (2026-06-29, run_7c8453a2, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- [pitfall] **The Claude Code Bash tool executes commands in `zsh`, NOT bash (macOS: `$SHELL=/bin/zsh`, `ZSH_VERSION=5.9`, `$0=/bin/zsh`).** Any code that validates / parses / re-checks a Bash command MUST use the SAME interpreter the tool runs (resolve `$SHELL` → `/bin/zsh` → `/bin/bash`), or it diverges from real behavior. Concrete bite: `bash_syntax_guard` first hardcoded `/bin/bash -n` and false-killed valid zsh syntax — `foo() { echo hi }` and `for i (1 2 3) { … }` are valid zsh (run fine) but `/bin/bash -n` exits 2. `zsh -n` catches the SAME real-hang set (unterminated quote/backtick/if/brace → exit≠0) AND approves zsh-valid syntax, so checking with the exec shell is both safer (no false-kill) and complete. Verify the exec shell empirically (`echo $ZSH_VERSION`), never assume bash. (2026-06-29, run_07fd1d8f, manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [model] **Invariant `run.json file st_mtime >= its content `updated_at`` holds — and is the license for mtime-based pre-filtering of pipeline runs (run_885eb466).** Every run.json writer in `artifact_cli.py` (lines 174/848/1424/1673) sets `updated_at = now()` and `write_text()` in the SAME operation, and there is NO `os.utime(run.json)` anywhere in the workspace — so the file's mtime is always ≥ the timestamp recorded inside it. A git restore only resets mtime to `now` (the SAFE direction: fresh file, possibly-stale content → still let through). Consequence: a coarse `st_mtime < now - 48h` gate is provably equivalent to (and far cheaper than) the content-level 24h `updated_at` filter — the 2× buffer guarantees anything that could be <24h is read; only >48h-cold files are skipped. Used by `_get_paused_pipeline_highlights` + `_newest_completed_run`. Do NOT add a writer that sets a future `updated_at` or touches mtime independently — it would break this gate. (2026-06-29, run_885eb466, manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- Gate-2 caught a HIGH the happy path missed: a convergence guard MUST carry the same protections the sibling path already has. My desync guard (isStreaming && phase=idle) lacked the ≥10s _reconcileStreamStart grace that the force-clear path uses — and a send legitimately holds that state during `await buildContentArray` BEFORE startStreaming. Reuse the existing grace, don't re-derive a guard without it. (2026-06-28, run_1a264fd1, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A streaming-render timer has 3 traps (mount-time anchor vs per-id, ungated interval on keep-mounted tabs, missing cleanup); the SAFE pattern already existed in useStreamingActivity — copying a proven discipline beat inventing one. (2026-06-28, run_02e658d0, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A SKIP path (sqlite-vec unavailable) must be NON-PASS on both signals: the marker-based harness already reports non-pass (no _OK marker), but return a distinct exit code (2) so exit-code consumers can't read SKIP as green. (2026-06-28, run_2a5ff539, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate-2 named the real teeth-limit: the -k __NONEXISTENT__ negative_command is a RUNNER check, not a behavior mutation (same convention as existing GS_RCHAIN_* canaries). Accepted + documented via teeth_note rather than rewrite all pointer canaries into behavior-probes (C042 mechanism-trap). The non-vacuity guarantee correctly lives in the mutation-proven pointed-at tests — proven live for the new provenance test (RED-on-revert). (2026-06-28, run_2d4be2f2, auto-cultivated)
- Wide-scan BEFORE building corrected my own false premise: I first told XG '33 cases, zero behavioral coverage' — the live grep found 173 (33 public + 140 private, 30 behavioral READ-path). Measuring real coverage reframed the run from 'build 8 cases' to '5 pointer-reuse + 2 new + 1 defer'. Always measure existing coverage with a real grep before claiming a gap (GUI24 pattern). (2026-06-28, run_2d4be2f2, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Right response to the adversary HIGH (turn-reset fail-mode) was NOT a new turn-counter mechanism (C042 build-a-mechanism trap), it was a test locking the established UserPromptSubmit-reset contract (failure_tracker_reset uses identical pattern). Match the codebase pattern, lock with a test. (2026-06-28, run_3f3be114, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- An unused import that happens to be WRONG is more dangerous than a used one: it never gets exercised by the happy path (so tests pass) yet it sits in a try that gates real behavior. The dead import + dead-code (the SessionRecall import was also unused) both signal the block was never actually running — a 0-caller/0-effect signal is a dead-block tell. (2026-06-27, run_edfad326, auto-cultivated)
- Two vacuity traps self-caught via mutation BEFORE review (bare-substring Open Threads present in index/manifest; default-budget never approaching the cap). Mutation testing a self-authored probe is the only reliable way to find these — a green probe on correct code proves nothing about whether it goes red on broken code. The CLASS A guard is the mutation, not the green. (2026-06-27, run_c0205808, auto-cultivated)
- Fixture-trap: an eval that drives real selection must use section headings that match _key_to_section, or the matched section silently skips and the keyword-hit assertion ships vacuous. The probe input must satisfy the SUT internal contract, not just look plausible. (2026-06-27, run_c0205808, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- alive != correct is THE READ-path differentiator, now an eval CONTRACT not a prose rule: GS_LOP005 went from import-OK (blind to 4% under-fill) to a mutation-proven behavior canary. Cognition (IMPROVEMENT.md lesson) + enforcement (golden case) shipped together — the anchor+guard template for protecting any load-bearing capability. (2026-06-27, run_674f32ef, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- I fixed the silent dead-path is the most dangerous belief — degradation must be surfaced at the layer it happens, not assumed to propagate as an exception (W5 lived one frame deeper in RecallEngine) (2026-06-27, run_4d06640b, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- CLASS B / C034 recurred: I declared run_81f6d20c tracking lost and blamed a parallel-session sweep, but it was never lost — I was ls/find-ing the SOURCE repo (~/Desktop/.../swarmai/Projects) while the CLI writes to the DAEMON workspace (~/.swarm-ai/SwarmWS/Projects). run-get worked because it resolves the real root; raw shell did not. To check an artifact, use the CLI that owns the path, not raw ls on a guessed dir. (2026-06-27, run_241014d4, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 0 | trust: high | promoted: 2026-06-27 -->
- CLASS A authorship trap recurred: 7 self-authored teeth tests all GREEN but the contract was WRONG (validated my marker convention, not the parallel spine-probe opposite one). Self-authored green tests inherit the author blind spot; the mutation-running Gate-2 adversary was the only catch. (2026-06-27, run_241be9da, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- R7 same-class sweep (running sibling files that import compute_bvt) surfaced 3 stale fixtures the named task would never have touched. (2026-06-27, run_d2763c8a, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Honest scope beats theater: ci_eval_gate CANNOT verify a real report on CI (golden_set lives in workspace, not code repo) → would always soft-pass. Gated the gate MACHINERY (unit tests) instead of faking a result-gate. A CI job that always passes is worse than none. (2026-06-26, run_5edf2cc0, auto-cultivated)
- Gate-2 HIGH twins (teeth + stamp convention-only): both traced to ONE root — enforcement lived in skill DOCS not CODE. Fixed by wiring validate+auto-stamp into eval_service.add_case. Convention is not enforcement; if a gate matters, it must be in the code path every writer goes through. (2026-06-26, run_5edf2cc0, auto-cultivated)
- Gate-2 CRITICAL = textbook authorship-trap: eval_nightly imported load_history (real: _load_history) → whole job dead-on-arrival. My 7 tests ALL injected fake runner= so NONE exercised the real import. Lesson: a handler with an injected-seam test MUST also have one test that resolves the real imports (test_default_runner_imports_resolve added). (2026-06-26, run_5edf2cc0, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Gate 0 (Understanding) saved the most cost this run: my original P0+P1+P2 build DoD was WRONG-FRAMED — P0 (hit-log surface) was already shipped and working. Code-tracing the live READ path before scoping found 3 existing recall engines + a P0 surface, collapsing the goal from build-new to assemble-over-existing. Always characterize the present system in EVALUATE before scoping a build. (2026-06-26, run_4358cc95, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- A regex false-positive in ONE alternation of a shared pattern list is almost never isolated — it has siblings. The named 'lets' bug recurred 3x (i'll/ill, we-can, imperative Use-of) + 1 known-limit (gerund), ALL in _SOLUTION_LANGUAGE_PATTERNS. Lesson: when fixing an over-match in a multi-pattern regex, audit EVERY pattern in the list for the same class, don't just fix the reported one. The adversarial same-class-hunt mandate is what surfaced this. (2026-06-26, run_b9452eb9, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Fix for self-authored eval fiction is STRUCTURAL: author protection cases WITH the deliverable that creates their observable surface (never up front against nonexistent fields, GUI24); characterization (RED-stays-RED evidence) != protection (green-baseline guard) — separating them kills the circular self-authored-RED trap (GUI04). (2026-06-26, run_65e507f8, auto-cultivated)
- EVALUATE Understanding-Gate skeptic earned its keep on a DESIGN (not code): killed the anchor premise (injection-hybrid vs recall-keyword asymmetry) — FALSE in prod because select_memory_sections defaults memory_embeddings=False and the sole caller context_directory_loader.py:737 omits it. Both paths keyword-only; hybrid built-but-unwired. Designing on the false frame would have justified the architecture on a fiction. (2026-06-26, run_65e507f8, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Authorship-trap completeness miss (GUI18), again: I guarded the 4 slice sites I LOOKED AT; the adversarial found 3 MORE of the identical agent-freedom-provenance class (criteria_met/unmet, always/never_rules, adversarial_findings) in the same function. After fixing a type-guard class, grep ALL iterations/slices of same-provenance fields — do not assume the ones you noticed are all of them. (2026-06-26, run_2dff24c5, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- Mirror the project's EXISTING test driver for a path when building a fault-injection harness against it — RP45/R16b applied gave ZERO wrong guesses this run vs 2 in the prior run. Reading test_no_cross_tab_evict + TestDumbSpawnWatchdog made both harnesses drive the REAL guard on first try. (2026-06-26, run_4596411e, auto-cultivated)
<!-- maturity: growing | sources: 2 | verified: true | used: true | days: 1 | trust: high | promoted: 2026-06-25 -->
- Empirically falsified an AC before building it: spec entropy-guard was a Headroom char-level port, but SwarmAI _truncate_section is word-level (split+join) so entropy tokens are never bisected (104 cases, 0). Shipped a characterization-LOCK test instead of a no-op guard. Verify a borrowed mechanism maps to your code before building the AC. (2026-06-25, run_9de88af9, auto-cultivated)
- Gate-2 caught 2 CRITICAL data-leak bypasses of the AC4 privacy gate that my OWN AC4 tests missed (case-insensitivity + path-traversal) — GUI01 authorship blind spot recurring on a SECURITY gate. Added RP44: identity-gates must key on normalized identity (casefold basename + inode), never a raw user-supplied name, and the alias corpus (case/traversal/symlink/hardlink) must be explicit DENY tests. (2026-06-25, run_9de88af9, auto-cultivated)
<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 0 | trust: high | promoted: none -->
- **Recommend B** + fold in Headroom's entropy guard (never split `run_xxx`/hash/path mid-token) as a cheap independent win (2026-06-25, 7cae4a9f-b6fd-4f18-8029-b7a0ca350633, decision)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- A pressure-trap eval that only checks the protective action passes too easily (breadcrumb-following). Requiring the rubric to CITE THE SPECIFIC RULE by name (not just take the right action) is what makes it test self-knowledge application, not doc-reading. The cite-requirement introduced real discrimination edges (correct-action-but-no-cite now FAILS). (2026-06-25, run_b250caf1, auto-cultivated)
- Empirical probe before design overturned the ToDo premise: a BARE no-cue trap is structurally ALWAYS-FAIL in the headless sandbox (bare Sonnet, no Brain injected) — it cant discriminate good from bad agent. The valid discriminator is AMBIENT-cue (name where knowledge lives, but give NO protective instruction). Run the 60s probe before writing the design (GUI01 reinforced). (2026-06-25, run_b250caf1, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- GAP 2 was a latent BROKEN path hiding behind a mock: _create_health_todo called async ToDoManager methods that don't exist, threw every time, swallowed — and the test mocked them so it looked green for who knows how long. 'Built infrastructure must be ACTIVATED' + 'a test that mocks the very thing it claims to verify proves nothing'. Fixed to the proven direct-sqlite pattern. (2026-06-25, run_e681a61d, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Single-writer must extend to the READ/render path. The 2026-06-17 MessageStore refactor unified WRITES but left two render sources + reverse-flow (store.replace(tabState.messages) on tab-switch) — COE10 #6 recurred at render layer. (2026-06-25, run_9db9f987, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- render-fidelity must exercise real assembly path not pass-through (2026-06-25, run_52a22424, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- kill()-before-raise must be exception-safe: if kill() throws, the retriable RuntimeError is skipped and the error degrades to a non-retriable crash, bypassing the intended --resume path. Wrap the kill, always reach the raise. (2026-06-24, run_e607c4cd, auto-cultivated)
- For a guard on the hottest path where false-positive cost is high (kill+resume of a live turn), the detector needs a NEGATIVE corpus too — not just confirmed positives. Test fenced-docs / inline-mention / self-explanation explicitly, because in a meta-heavy codebase those are common legitimate outputs. (2026-06-24, run_e607c4cd, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Multi-shape unification: share what is TRULY universal (enabled/user_stopped guard + verdict vocabulary), dispatch what differs (the gate: attempt-breaker vs cooldown-threshold vs bare-threshold vs graceful-escalation). The guard that is NOT universal (protected STATES — self-heal protects waiting_input, stuck-WAITING targets it) must be policy-declared, not a coordinator constant. Mistaking a policy-specific guard for a shared one is the subtle trap. (2026-06-24, run_9e5b7c97, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- Timeout MUST deny (question expired, re-ask), never inject empty answers + allow — injecting empty was the original bug delayed by N minutes. Correctness invariant on ALL paths: never fabricate an answer. (2026-06-23, run_594233bb, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: true | used: true | days: 0 | trust: high | promoted: none -->
- Meta-review confirmed frontend timeout is the CORRECT layer, not a patch: backend SessionRouter._units is in-memory, EMPTY after daemon restart, SSE-owning process is dead — backend structurally CANNOT emit a terminal thinking_end. Only the client outlives the dead process. When asking is-this-the-right-layer, check whether the other layer even HAS the information. (2026-06-23, run_beee9586, auto-cultivated)
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->
- Gate-2 spawn hit a config-error rejection (agent type code-review not found) — a DIFFERENT class than PIT01 poisoning. Correct adaptation was retry with a valid type (general-purpose), NOT checkpoint. The fail-closed path is only for harness/poisoning rejection, not config errors — the distinction matters or you checkpoint on trivially-fixable errors. (2026-06-22, run_45ab67c7, auto-cultivated)
- The auto-aggregate fallback (artifact_cli.py:948 hardcodes spawned:False) is the concrete bypass that makes a publish-only gate insufficient — it reaches completed without ever calling validate_artifact_data. Always ask: what OTHER paths reach the terminal state? (C037/CLASS-A fail-open-at-last-gate, recurred). (2026-06-22, run_45ab67c7, auto-cultivated)
- A two-field/structural gate written at publish-time but NOT at completion-time is a fail-open hole: validate_artifact_data enforced spawned+evidence, but the mandatory completion path (run-update --status completed -> validate() -> _check_depth) checked only profile_tier. Enforcement must live on EVERY path to status:completed, not just the path the agent happens to call (pipeline_validator.py _check_depth, fixed f5e2df3f). (2026-06-22, run_45ab67c7, auto-cultivated)
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

Code that works in dev WILL break in daemon/hive without these guards. The pipeline's L5 (DDD Conformance) should check these when reviewing new code.

| API / Pattern | Dev (`./dev.sh`) | Daemon (launchd) | Hive (systemd) | Correct Approach |
|---------------|-----------------|------------------|----------------|-----------------|
| `sys.executable` | `/usr/bin/python3` ✅ | Frozen binary path (no `-c` flag) ❌ | Python source path ✅ | `utils/bundle_paths.py::get_python_executable()` |
| `Path(__file__)` | Source tree ✅ | `_internal/` inside PyInstaller bundle ❌ | Source tree ✅ | `utils/bundle_paths.py::get_resource_path()` |
| `os.path.expandvars("${HOME}")` | Expands ✅ | Literal `${HOME}` (no shell env) ❌ | Expands ✅ (systemd sets HOME) | `Path.home()` (uses `pwd.getpwuid`, always works) |
| `os.environ["USER"]` | Set ✅ | NOT set (launchd minimal env) ❌ | Set ✅ | `getpass.getuser()` |
| Platform triple | `aarch64-apple-darwin` | Same | `x86_64-unknown-linux-gnu` ❌ | Never hardcode — detect via `platform.machine()` + `sys.platform` |
| `subprocess.run(["python3", ...])` | Works ✅ | `python3` not on PATH ❌ | Works ✅ | `get_python_executable()` or direct import |
| `import fcntl` | Works (macOS) ✅ | Works (macOS) ✅ | Works (Linux) ✅ but Windows ❌ | `utils/file_lock.py` (cross-platform flock) |

**Rule:** Before using ANY of the left-column APIs in new code, check the table. If any cell shows ❌, use the "Correct Approach" column instead. This prevents the environment-assumption bug class (IMPROVEMENT.md 2026-05-13: pipeline 9/10 confidence, 5 findings all from this table).

### Frontend State Mutation Traps

| Pattern | Looks correct | Actually broken | Correct Approach |
|---------|--------------|-----------------|-----------------|
| `tabState.isStreaming = false` | Same syntax as inside setIsStreaming | Mutates ref without re-render → spinner-hang on background tabs | `setIsStreaming(false, tabId)` — atomic: flag + Set + re-render |
| `updateTabState(id, { isStreaming: x })` | TypeScript compiles clean (pre-fix) | Object.assign bypasses readonly, skips Set + re-render | Excluded from patch type via `Omit<..., 'isStreaming'>` — compile error now |
| Any `tabMapRef.current.get(id).foo = x` on render-driving state | Direct mutation is fast | React doesn't re-render on ref mutations — only setState/useState triggers re-render | Always pair ref mutation with a useState setter (like `setPendingStreamTabs`) |

**Rule:** `useChatStreamingLifecycle.ts` — any property on `UnifiedTab` that drives UI rendering (spinner, status, indicators) MUST be either (a) `readonly` with a dedicated setter function that also triggers re-render, OR (b) managed via `useState` directly. Direct ref mutations for render-driving state = invisible bugs that only manifest on background tabs.

## Environment Notes
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

- Backend port is **fixed at 18321** (daemon mode). Desktop daemon and Hive both use the same fixed port. Tauri probes for running daemon (5×2s retry), auto-bootstraps via launchctl if not running.
- Claude Agent SDK spawns a CLI subprocess per session. Spawn cost model: 1500MB with 1200MB adaptive floor (records main process RSS, not tree). **Proactive restart** at 3.5GB RSS (IDLE only) prevents jetsam kills; **streaming kill** at 7.0GB (emergency, true leak signal). Dynamic `compute_max_tabs()` [2,4], ceiling=4 (3 chat + 1 channel).
- **`SWARMAI_SELF_HEAL`** env var (default: `1` = on). Gates the self-healing loop (HealthSensor → HealingLoop → auto-recover). Set to `0` to disable (debugging only). Also set in launchd plist (`com.swarm-ai.daemon.plist`). When disabled, sessions that hit turn limits or OOM simply stop instead of auto-recovering.
- SQLite in WAL mode at `~/.swarm-ai/data.db`. Direct access from agent sandbox is reliable for CRUD.
- Two independent credential chains may coexist: Claude CLI uses AWS SSO IdC tokens, boto3 may use credential_process. Validate the chain your code actually uses.
- Claude Code IS the local proxy when running inside the agent sandbox. Strip proxy vars when spawning subprocesses that manage their own networking.

**Bash hang-defense — 3 layers (verified 2026-06-25, commits 8719fb2e/4e2ecd4a/8bb8b849, needs build to take effect):**
A runaway/hung Bash command is the same failure class as an error but silent. Defense is layered because no single layer catches all cases:
1. **Foreground timeout** — `BASH_DEFAULT_TIMEOUT_MS=120000` (`claude_environment.py:251`, via `setdefault` so it never overrides user/CLI value). CLI ceiling stays 600s/10min — **never raise a ceiling to permit a longer hang; that fixes nothing.** ⚠️ Bounds FOREGROUND only — backgrounded commands ignore it entirely (anthropics/claude-code#61568). That gap is the real runaway hole and is why layer 2 exists.
2. **Background guard** — PreToolUse `background_command_guard` (`security_hooks.py:502`, wired at `hook_builder.py:197`, matcher="Bash"). DEFAULT-DENY backgrounding (`run_in_background` flag OR shell `&`/`nohup`/`disown`/`setsid`) except a narrow long-lived-service allowlist (`_BG_SERVICE_ALLOWLIST_RE:470` — dev servers, `--watch`, `tail -f`, `./dev.sh`). `_is_backgrounded()` strips quoted literals + `&&` + `&>`/`2>&1` before testing for a bare `&`, so redirects aren't misread as backgrounding. Prose rules don't hold (LLM ignores file-end rules — EVOLUTION F004); the hook is the only deterministic enforcement. Genuine long detached work → daemon job system, not a background shell. 22 forcing tests (`test_background_command_guard.py`). Known limit: regex shell-parse can miss `(cmd &)` subshells / here-docs, but the failure direction is "lets a rare construct through", never "blocks a normal command" — safe.
3. **CPU-liveness watchdog** (already existed) — kills a deadlocked process. ⚠️ Cannot catch busy-but-useless (a `find` scanning node_modules burns CPU → looks alive); only the wall-clock timeout (layer 1) catches that class. → Search via Glob/Grep, never bare `find .`/`grep -r .`.
- **pytest is serial-by-default now** (`-n 0`, commit dd46823c) — xdist `-n 4` deadlocked the recovery suites = the "30-min test hang". Opt back into parallel explicitly per-run if needed.
- **vitest exit hang fixed** (commit 1895b89b) — global `afterEach(messageStoreRegistry.clear())`; MessageStore's 90s watchdog leaked → process wouldn't exit. Without the global teardown, frontend test runs hang at the end.

<!-- RADAR_TODOS [
  {
    "title": "Doc gap: conversation_extract.py not in TECH.md Key Subsystems",
    "priority": "medium",
    "description": "New backend/core/conversation_extract.py added 2026-07-07 (feat: DDD capability C — conversation→DDD) is not documented in TECH.md Key Subsystems section. This is a new core subsystem for extracting DDD knowledge from conversations (guard-first, dormant, human-gated).",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-07-11",
      "commit_ref": "6fa9e35f",
      "suggested_action": "Add conversation_extract subsystem entry to TECH.md with: purpose (conversation→DDD extraction), guard model (guard-first, human-gated), current state (dormant), integration points",
      "next_step": "Read conversation_extract.py implementation and add [model] or [subsystem] entry to TECH.md describing the capability C architecture"
    }
  },
  {
    "title": "Doc gap: CMHK DDD stale vs Sales Hub wiki (30+ days)",
    "priority": "medium",
    "description": "CMHK Sales Hub DDD (Projects/SwarmAI/assets/CMHK/) last synced 2026-06-01. Source wiki pages have updates: Home (10 days ago ≈2026-07-01), Dashboards (28 days ago), Account (23 days ago). DDD may contain outdated customer policies, pipeline rules, or account management guidance.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-07-11",
      "commit_ref": "N/A",
      "suggested_action": "Re-scrape w.amazon.com/bin/view/GCR/BD/Sales-Hub/ (home, Pipeline, Dashboards, Account, Revenue) and update CMHK DDD markdown files. Compare current vs 2026-06-01 content to identify material changes.",
      "next_step": "Use ReadInternalWebsites to fetch updated Sales Hub pages, then run CMHK DDD update pipeline (or manually diff and update markdown)"
    }
  },
  {
    "title": "Doc gap: docs/README.md 43+ days behind skills/ changes",
    "priority": "low",
    "description": "docs/README.md last updated 2026-05-29, but backend/skills/ had 93 commits in last 30 days. Public-facing docs may not reference new skills (s_swarm-code-reviewer added, s_code-review strengthened) or updated skill capabilities.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-07-11",
      "commit_ref": "277cadbd, 695c4905",
      "suggested_action": "Review s_swarm-code-reviewer (Principal-SDE CRUX reviewer) and strengthened s_code-review. Update docs/README.md with new skill descriptions if they're user-facing.",
      "next_step": "Check if s_swarm-code-reviewer is public-facing (not internal-only). If yes, add to README skills section with description."
    }
  },
  {
    "title": "Doc gap: P0 dangerous-command approval hang missing post-mortem",
    "priority": "medium",
    "description": "P0 fix 8cc5c6e6 (2026-07-13) resolved a critical hang where all dangerous commands (rm -rf, etc.) blocked for 10 minutes until MESSAGE_TIMEOUT force-killed the session. Root cause: OT01 GenGuard discarded HITL permission_request events as 'stale' when gen counter advanced during blocked hooks. Fix: exempt terminal HITL prompts from gen-discard. No post-mortem document exists in docs/post-mortems/ despite high user impact.",
    "context": {
      "source": "docs-freshness-audit",
      "audit_date": "2026-07-13",
      "commit_ref": "8cc5c6e6",
      "suggested_action": "Create docs/post-mortems/05-dangerous-command-approval-hang.md documenting: user impact (10-min hang on all dangerous commands), root cause (OT01 GenGuard stale-event discard), detection (live user report + log correlation), fix (HITL event exemption), prevention (mutation test coverage)",
      "next_step": "Draft post-mortem using commit message 8cc5c6e6 as source, following structure of docs/post-mortems/04-bilateral-deadlock.md (incident timeline, root cause analysis, fix verification, lessons learned)"
    }
  }
] -->
