# Autonomous Pipeline Report: Jobs: an exit-0 CLI API error is never a success; refuse a CLI too old for the model

**Run ID:** run_2cb579ce | **Project:** SwarmAI | **Profile:** bugfix
**Date:** 2026-10-17 09:21 UTC | **Duration:** 20.5 min (in progress) | **Tokens:** 180,000
**Gate 1:** WARN | **Files:** 5 | **Tests:** True/1

## TL;DR
Stop headless jobs reporting success when the Claude CLI returned an API error, and refuse to launch a job CLI too old for the resolved model: (1) jobs/executor._handle_agent_task maps a returncode-0 

## 1. Requirement
Stop headless jobs reporting success when the Claude CLI returned an API error, and refuse to launch a job CLI too old for the resolved model: (1) jobs/executor._handle_agent_task maps a returncode-0 result whose JSON has is_error=true or result starting 'API Error' to status=success ('Completed in Ns') — classify it as failed with the error text; same class in jobs/handlers/ddd_self_audit._classify_review_result and the Slack-DM CLI tier; (2) jobs use the system claude on PATH with no version check while claude-opus-5-5 (flagship) needs CLI >= 2.1.280 — add a registry-owned per-model minimum CLI version and a preflight at every job CLI launch site that fails loudly with an explicit message.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> Stop headless jobs reporting success when the Claude CLI returned an API error, and refuse to launch a job CLI too old for the resolved model: (1) jobs/executor._handle_agent_task maps a returncode-0 result whose JSON has is_error=true or result starting 'API Error' to status=success ('Completed in Ns') — classify it as failed with the error text; same class in jobs/handlers/ddd_self_audit._classify_review_result and the Slack-DM CLI tier; (2) jobs use the system claude on PATH with no version check while claude-opus-5-5 (flagship) needs CLI >= 2.1.280 — add a registry-owned per-model minimum CLI version and a preflight at every job CLI launch site that fails loudly with an explicit message.

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | bug | user-stated: XG 2026-10-17: 修第1条 | confirmed | ● | Given CLI exit 0 with {"is_error": true, "result": "API Error: 400 ..."}, when _handle_agent_task finishes, then JobResult.status == 'failed' and the error text is in the summary |
| FR-2 | bug | user-stated: XG 2026-10-17: 修第1条 (CLI version gate) | confirmed | ● | Given system claude 2.1.190 and default model claude-opus-5-5, when an agent task runs, then it fails before spawning with 'CLI 2.1.190 < 2.1.280 required by claude-opus-5-5' |

### 澄清问答 (Clarifications — who decided what)
| Q | 问题 (question) | 回答 (answer) | 答者 (answered_by) |
|---|------------------|---------------|---------------------|
| Q1 | Too-old CLI: fail the job, or fall back to an older model? | A. Fail loudly | 🧠 self |
| Q2 | Where does the minimum-version table live? | A. model_registry (model SSOT) | 🧠 self |

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
- [SELF-ANSWERED] Q1: A. Fail loudly (🧠 AI 替你拍的板 — resolution: SOUL P4: a silent fallback is disaster-recovery masking the root cause (old CLI); the fix is upgrading the CLI, which a loud failure tells the user.)
- [SELF-ANSWERED] Q2: A. model_registry (model SSOT) (🧠 AI 替你拍的板 — resolution: Promotion of a model already happens in model_registry; its CLI floor is a property of the model.)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 7.00 | |
| Priority | 8.00 | |
| Feasibility | 9.00 | |

**Acceptance Criteria:**
1. AC1: _handle_agent_task with rc=0 and JSON is_error=true (or result starting 'API Error') returns status='failed' with the error text in summary/error, not 'success'.
2. AC2: ddd_self_audit._classify_review_result returns 'failed' with error_detail for rc=0 + is_error/API Error.
3. AC3: _send_slack_dm tier-3 CLI returns False when the text output starts with 'API Error'.
4. AC4: model_registry exposes min_cli_version_for(model) -> tuple|None (claude-opus-5-5 -> (2,1,280); accepts short name, Bedrock id, [1m] suffix; unknown -> None).
5. AC5: every job CLI launch site (agent task, ddd self-audit, Slack-DM tier-3) checks the resolved CLI version against min_cli_version_for(resolved model) before spawning; too old -> fails loudly with a message naming both versions; unparseable version -> proceeds (the result classification still catches a rejection).
6. AC6: one shared version parser; _cli_supports_bare uses it.


## 3. Design & Approach
**Research Findings (THINK):**
- Three CLI launch sites resolve the system claude and the model via _resolve_job_model: executor._handle_agent_task, executor._send_slack_dm (tier 3), handlers/ddd_self_audit.run_ddd_self_audit.
- _classify_review_result is a pure classifier already unit-tested — the natural shared home for 'is this CLI JSON result an error' is a pure helper both executor and the audit handler call.
- model_registry is stdlib-only and already owns per-model capability predicates (supports_disabled_thinking, context_window_for) — a min-CLI-version lookup fits the same shape.
- The CLI hung 60s on an unrecognized model id in a probe (2026-10-17) — a too-old CLI is not guaranteed to fail fast, so a pre-spawn version check is the reliable gate.

**Alternatives Evaluated:**
- SIMPLICITY — effort: S-M
- QUALITY — effort: M-L
- DELETION — effort: S

**Chosen Approach:** Pure result-error helper shared by executor + audit; Slack tier text check; registry-owned min CLI version + executor preflight at the 3 launch sites.

**Success Criteria:**
- targeted job/audit/registry tests green
- mutation: removing the is_error check turns AC1 test RED

**Always:**
- model_registry stays stdlib-only
- classification never adds a cost/budget branch (run_271c39df)

**Never:**
- silently fall back to another model
- block a job because the version string is unparseable


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | bugfix profile | mechanical | known root cause |
| think | No shared spawn wrapper | mechanical | 3 sites differ in format/timeout/MCP |
| plan | min CLI table in model_registry | taste | model capability SSOT |
| build | Leaf module jobs/cli_result for pure helpers (Gate-1) | mechanical | executor import side effects |
| build | auth-like API error -> auth_failed; transient -> failed/api_ | taste | breaker semantics preserved for transient auth |
| review | Keep api_transient in opt-in retry | taste | only safety.retryable jobs; one retry |
| test | 524 dependency tests green | mechanical | exit 0 |

**Summary:** 4 mechanical, 3 taste, 0 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**4 findings** (verdict: ?)
- [MEDIUM] backend/jobs/handlers/ddd_self_audit.py: 0-reviewed 'failed' carried no cause in
- [LOW] backend/jobs/cli_result.py: CLI subtype short-circuit labelled overload-in-error
- [LOW] backend/jobs/cli_result.py: 'API Error: Connection error.' classified non-retrya
- [LOW] backend/jobs/executor.py: api_transient retry has no backoff. Accepted: opt-in o

### 7.6 Completion Audit
(No completion audit data)


## 8. Files Changed
- `backend/jobs/cli_result.py`
- `backend/jobs/executor.py`
- `backend/jobs/handlers/ddd_self_audit.py`
- `backend/model_registry.py`
- `backend/tests/test_job_cli_result.py`

## 9. Lessons
- [pitfall] Judging a headless CLI run by its exit code alone records failures as successes: the Claude CLI can return an API error inside an exit-0 JSON envelope (is_error true, subtype success), so every launch site must also read the result envelope — and there were three launch sites, not one.
- [pitfall] A too-old or unknown-model CLI is not guaranteed to fail fast — a probe with an unrecognized model id hung for 60s — so a model's minimum CLI version has to be enforced before spawning, from a registry-owned table, not inferred from the run's error afterwards.
- [principle] When a classifier feeds a circuit breaker, every new failure class must be routed by what a retry can fix: auth-shaped errors go to the auth path, throttling/overload to retry, model-access denials and other 4xx to hard failure — and the human-facing error field must carry the detail, because alert surfaces read the code field first.
- [guideline] Keep pure classifiers that tests and HTTP-path handlers import in a side-effect-free leaf module; importing the runtime module that owns them (here a module that rewrites PATH at import time) from a pure classifier silently couples test collection to environment probing.


## 10. Known Gaps & Attention Flags
None identified.


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-17 09:21 UTC*
