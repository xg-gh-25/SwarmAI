---
title: "SwarmAI OS Eval Function — Continuous Self-Awareness Engine"
created: 2026-06-08
updated: 2026-07-29
tags: [eval, self-awareness, golden-set, cognitive-health, three-layer]
project: SwarmAI
status: live — reflects shipped implementation (source-of-truth = code, see §Source-of-Truth Map)
---

# SwarmAI OS Eval Function — Continuous Self-Awareness Engine

> **Thesis:** An AI OS without eval is an organism without proprioception — it
> doesn't know its own state until something breaks. Eval is not testing; it is
> *the capacity to know whether you're still you, and still good.*

> **This doc reflects the SHIPPED system (refreshed 2026-07-29 against source).**
> Where any prose here disagrees with code, **code wins** — see the
> §Source-of-Truth Map at the end for the exact file:line of every claim.
> **No volatile counts are frozen into this doc** (case counts, category counts,
> context sizes drift daily — a stored number silently lies the day after). Where
> a number matters, this doc gives the **command to measure it live** (R30#4).

---

## Origin & Inspiration

Rob Chu's ADLC deck (AgenticEngineering, Jun 2026) defines:
- **Eval** = the spec, the gate, the monitor, AND the reward function
- **Golden Set** = real production data, SME-labeled, your eval IP
- **Gate** = statistical quality gate in the CI/deploy path

Rob's model targets enterprise agent products (millions of users, model drift,
A/B canary). We adapt these concepts for **a self-evolving single-user OS** where
the "product" is judgment quality, the "user" is one builder, and the "drift"
includes context/memory/knowledge/rules changes — not just LLM version bumps.

## What Makes SwarmAI Different from Rob's Model

| Dimension | Enterprise Agent (Rob) | SwarmAI OS |
|-----------|----------------------|------------|
| What drifts | Model weights | Model + Context + Memory + Knowledge + Rules + Time |
| What to eval | Output quality on golden set | **Cognitive quality** (judgment, recall, compliance, relevance) |
| Where cases come from | SME-labeled prod traces | **Corrections + real low-scoring sessions** — crystallized failure history |
| Golden set | Fixed labeled trajectories | **Living behavioral contract** that grows from corrections AND real sessions |
| Gate | Statistical pass rate blocks deploy | **Git-bound freshness+green gate** + red-line veto + post-deploy canary |
| Feedback loop | Prod traces → eval set → training data | Corrections/sessions → golden drafts → human ratify → prevent recurrence |
| Improve the… | Model weights | **Harness** (SOUL/AGENT/STEERING), not the model |

---

## Architecture: Three Layers, One Subsystem

The eval function is **not** one exam. It is a **system-level decoupled
subsystem** spanning **three layers** that answer three different questions.
Only Layer ① is a fixed exam; ② and ③ turn *real production behavior* into
eval signal.

```
┌───────────────────────────────────────────────────────────────────────┐
│                    SwarmAI OS EVAL SUBSYSTEM                            │
│         (decoupled — triggered by CI / deploy / scheduled,             │
│          NEVER by the agent inside a coding pipeline)                  │
│                                                                        │
│  ① GOLDEN SET  ─ human-authored exam questions                        │
│     "If I ask the canonical hard question, do I still answer right?"  │
│     run programmatically + LLM-judged + (biweekly) real-agent spawn   │
│                                                                        │
│  ② SESSION HARVEST ─ weekly, low-scoring REAL sessions → golden DRAFTS │
│     "What did I actually get wrong last week that I should probe for?"│
│     discover failure → crystallize as regression probe → HUMAN ratify │
│                                                                        │
│  ③ ONLINE SESSION SCORING ─ score what ACTUALLY happened, no answer key│
│     "On real work, was the goal met and were the tools well-chosen?"  │
│     feeds the drift radar; its low-scorers feed Layer ②'s harvest     │
│                                                                        │
│   ─────────────────────────────────────────────────────────────────  │
│   Each case/session scored on 6 DIMENSIONS ▼                          │
│   factual · judgment · utility · compliance · capability · recovery   │
│                                                                        │
│   GATES ▼   git-bound freshness+green (ci_eval_gate) · red-line veto  │
│            · post-deploy canary (non-blocking)                         │
│                                                                        │
│   FLYWHEEL ▼  corrections + Layer② drafts → (human ratify) → golden   │
└───────────────────────────────────────────────────────────────────────┘
```

**Key architectural facts (all verified in code):**
- **Layers ②③ never gate and never auto-promote.** Only Layer ① feeds the
  CI/deploy gate; ②③ are discovery/monitoring. A harvested draft lands
  `tier=draft` and waits for a **human Promote** in the dashboard.
- **Eval runs in an isolated clean session** — same 11 context files + hooks +
  model as prod, but **zero chat history**. It measures the OS, not a conversation.
- **The agent is code-blocked from running eval** (`eval_command_guard`
  PreToolUse hook) — eval on un-deployed changes tests the OLD binary, proves
  nothing, and once hung the judge's Bedrock call. Triggers are CI/deploy/scheduled.

---

## The Six Dimensions

Every case (①) and every scored session (③) attributes its result to **one of
six dimensions**. Short ids live in `eval_runner.py` `DIMENSIONS`; the long-form
snapshot keys live in `golden_set.yaml` `dimensions:` (mapped by
`_DIM_TO_SNAPSHOT_KEY`).

| # | id (`eval_runner`) | snapshot key (`golden_set`) | Question | How it drifts |
|---|--------------------|------------------------------|----------|---------------|
| 1 | `factual` | `factual_accuracy` | "Is what I remember still true?" | Files/systems change; a claim goes stale |
| 2 | `judgment` | `judgment_quality` | "Same hard question → same good call?" | Principle erosion, inconsistent decisions |
| 3 | `utility` | `context_utility` | "Is the context I carry actually helping?" | Bloat, lost-in-the-middle, stale injection |
| 4 | `compliance` | `compliance` | "Are my rules still in force?" | Rule bypass under confidence (CLASS A) |
| 5 | `capability` | `capability` | "Is each organ still alive?" | A skill/job/pipeline silently breaks |
| 6 | `recovery` | `recovery` | "Do my crash/resume/self-heal paths run?" | Recovery code that compiles but never executes |

`recovery` is a **first-class, test-protected 6th dimension** (older drafts
folded it into capability — it is now separate).

---

## Layer ① — The Golden Set: Living Behavioral Contract

### What it is
A YAML corpus of cases, each a small probe of the OS's cognition. It lives in the
**workspace, not the code repo** (the code repo has no `Eval/`):

- `~/.swarm-ai/SwarmWS/Eval/golden_set.yaml` — **public**, git-tracked, shippable
- `~/.swarm-ai/SwarmWS/Eval/golden_set.private.yaml` — **private**, gitignored,
  instance-specific (cases that cite internal governance / instance paths)

> **Measure the live corpus** (never trust a frozen count):
> ```bash
> python backend/scripts/golden_case_validator.py --validate-corpus \
>   --root ~/.swarm-ai/SwarmWS
> ```
> For a category/tier/dimension breakdown, the dashboard **Golden Set** tab
> computes it live from `cases[]` (`eval-breakdowns.ts`) — it never stores a snapshot.

### Case schema (the real fields)
```yaml
- id: GS001                       # unique
  category: ddd_informed          # taxonomy is data-driven (see note below)
  dimension: context_utility      # one of the 6 snapshot keys
  level: session                  # session | trace | tool_call
  title: "Editing session_unit.py should read SwarmAI TECH.md first"
  source: "STEERING R1 + C011"    # governance provenance (private-leaning)
  scenario:
    turns:
      - input: "Fix the retry logic in session_unit.py"
  # ── THREE LAYERS OF GROUND TRUTH (compose freely) ──
  expected_response_contains: [pipeline]          # ① output-level  → keyword_match
  expected_trajectory: [Read SwarmAI/TECH.md, Read session_unit.py]  # ② behavior
  trajectory_match: in_order                      #    exact | in_order | any_order
  assertions: [Agent reads DDD doc before editing code]  # ③ judgment → LLM judge
  decision_rubric: "PASS only if fix is strangler-fig, not big-bang"  # behavior-judge
  evaluators: [trajectory_in_order, goal_success] # which evaluators run
  affected_by: [STEERING.R1, TECH.md]             # diff-scope + judge context refs
  tier: active                                    # draft | active | stable | archived
  eval_method: behavior                           # programmatic | llm | behavior
  redline: false                                  # true → any FAIL/ERROR = eval NO-GO
  # verification: {command, file, grep, expected_contains, negative_command}
  #   ↑ for programmatic evaluators
  # _origin: public|private        ← injected on load, STRIPPED on write (routing only)
  # validated_by_4gate: <sha256[:16]>  ← content-bound stamp; edited-outside-gate → drops from BVT
```

### Three layers of ground truth (borrowed from AgentCore, all wired)
They **compose** — one case can carry all three, each consumed by a different evaluator:

| Layer | Field(s) | Evaluator(s) | What it proves |
|-------|----------|--------------|----------------|
| ① Output | `expected_response_contains` | `keyword_match` | The answer contains the required substance |
| ② Behavior | `expected_trajectory` + `trajectory_match` | `trajectory_exact / _in_order / _any_order`, `trajectory_capture` | The right tools, in the right order |
| ③ Judgment | `assertions` (+ `decision_rubric`) | `goal_success`, `quality_score`, decision-direction judge | The behavior/decision was actually correct |

### Category taxonomy — data-driven, not a fixed enum
The taxonomy is **read from the live corpus**, not hard-coded to a fixed number
(the count grows; this doc deliberately does not freeze it). Load-time validation
in `eval_service.py` detects taxonomy drift. To see the current set:
```bash
python3 - <<'PY'
import yaml, os
base = os.path.expanduser('~/.swarm-ai/SwarmWS/Eval')
cats = {}
for f in ('golden_set.yaml', 'golden_set.private.yaml'):
    p = os.path.join(base, f)
    if os.path.exists(p):
        for c in (yaml.safe_load(open(p)) or {}).get('cases', []):
            cats[c.get('category', '?')] = cats.get(c.get('category', '?'), 0) + 1
for k in sorted(cats): print(f"{k:16} {cats[k]}")
PY
```
Representative categories in use: `decision`, `compliance`, `recall`,
`knowledge`, `ddd_informed`, `code_aware`, `quality`, `loop_active`,
`cultivation`, `action`, `recovery`, `runtime_health`, `safety`, `memory`,
`refusal`, `faithfulness`. (Enumerated live by the snippet above — do not treat
this list as authoritative or complete; the corpus is the source of truth.)

### The 4-gate admission (via `s_golden-case` skill + `golden_case_validator.py`)
`s_golden-case` is the **only sanctioned path** to modify the golden set. Its
operations: **ADD** (lands private by default, fail-closed), **PROMOTE**
(private→public, runs the privacy gate), **VALIDATE** (re-check a case/corpus for
drift), **AUDIT** (scan corpus for rot). Every case must clear these gates:

| Gate | Kills | Where |
|------|-------|-------|
| **schema** | malformed cases (missing required fields) | `golden_case_validator.py` `_REQUIRED` |
| **duplicate** | corpus bloat (same verification target) | validator dup-check |
| **non-vacuous** | a grep that matches anything / echo-its-own-literal (GUI21 vacuous-pass) | validator non-vacuous check |
| **privacy** (PROMOTE only) | shipping instance data — sensitive words / instance paths / DDD refs | validator privacy check |
| + **gate_teeth** | gate-eligible cases with no `negative_command` (no RED proof) | validator teeth check |
| + **gate_refs** | dotted refs (`STEERING.R1`, `MEMORY.DEC12`) that resolve to EMPTY (C044 drift) | validator ref-resolver |
| + **gate_redline** | `redline: true` with no runnable evaluator (unenforceable veto) | validator redline check |

**Public/private is a privacy gate, not two corpora:** cases carry an in-memory
`_origin` tag; on write they're **partitioned** — public → `golden_set.yaml`,
private → `golden_set.private.yaml`; the tag is stripped before serialization and
a private case is **never** written into the tracked public file. `get_case_detail`
projects private cases through a **fail-closed allowlist** (unknown fields dropped,
never leaked).

### Tier lifecycle
`draft` (auto-seeded / harvested, unratified) → `active` (human-ratified, runs
every cycle, P1-alerts on fail) → `stable` (10+ consecutive passes → monthly
cadence, `promote_stable_cases`) → `archived` (underlying rule/code removed).

---

## Evaluators — the dispatch waterfall

`evaluate_case()` tries evaluators **cheapest-first and short-circuits** on the
first definitive (pass/fail) result — so a case that a deterministic check can
settle **never costs an LLM call or an agent spawn**:

```
FOR each case:
  Phase 1 — PROGRAMMATIC (instant, $0, deterministic)
    canary_pass · file_contains · keyword_match · recall_at_k ·
    trajectory_exact/in_order/any_order · runtime_health
    → first definitive verdict returns immediately
  Phase 2 — BEHAVIOR (spawn a real headless agent; ~17–120s; opt-in only)
    trajectory_capture  (+ decision-direction judge if decision_rubric set)
  Phase 3 — LLM JUDGE (semantic; ~5s; ~$0.02–0.10/case; pinned judge model)
    goal_success · quality_score
  else → status: skipped (no supported evaluator)
```

### The evaluator catalog (everything actually implemented)

**Programmatic (gate-eligible)** — deterministic, cost 0, can enter the BVT gate:
- `keyword_match` — response contains all `expected_response_contains` (case-insensitive)
- `file_contains` — a file matches a grep pattern + expected text
- `canary_pass` — a shell command's output contains an expected marker (+ a
  **teeth** layer: a `negative_command` must ALSO run and emit a FAIL token)
- `trajectory_exact / _in_order / _any_order` — expected tool steps vs actual,
  three match modes; step-matching is substring **and** key-token, order-aware
- `recall_at_k` — mechanical recall@K on a gold-annotated corpus (numeric)

**Programmatic (not gate-eligible)** — real but slow/non-binary:
- `runtime_health` — fault-injection harness: spawns a subprocess, asserts exit 0 + marker (Recovery dimension)

**Behavior** — spawns a REAL agent (this is what makes cases non-circular):
- `trajectory_capture` — runs a clean headless agent on the scenario, records the
  **actual** tool calls, matches with **tool-name-anchored** logic so a
  `Grep {pattern:"read"}` can't fake a `Read` (the substring false-positive fix).
  If the trajectory passes AND a `decision_rubric` is declared, a **decision-direction
  judge** reads the agent's ACTUAL final text and rules whether the conclusion went
  the right way (observation, not the hypothetical "would a compliant agent…?").

**LLM judge** — semantic, pinned model (`eval_judge_model`, default a pinned Opus;
the judge is **pinned even as the prod model drifts**, so both can't degrade
blind together), temperature 0:
- `goal_success` — given the agent's rules + the case's resolved `affected_by`
  context + `assertions`, judge whether each assertion holds → JSON verdict
- `quality_score` — multi-dimensional rubric score

> **Judge-infra failures are `error`, never `skipped`.** A Bedrock/auth/parse
> failure surfaces as a red `error` (and a coverage-collapse alert), because
> silently "skipping" once let a subset produce a fake 100% score. `error` is a
> red light, not an omission.

### Two gates computed per run
- **BVT (Build Verification Test)** — binary green/red over **gate-eligible**
  evaluators, `tier ∈ {active, stable}`, AND a matching `validated_by_4gate`
  stamp. `green = total>0 AND passed>0 AND failed==0 AND error==0`. A case edited
  outside the 4-gate path has a stale stamp → **drops from BVT** (drift defense).
- **RED-LINE veto** — any `redline: true` case that FAILs or ERRORs → `violated`,
  regardless of aggregate score. A skipped red-line is reported but is **not** a
  violation (fail-closed: unknown ≠ proven-unsafe).

---

## Layer ②③ — the real-session quality loop

The newest subsystem: turn **real production sessions** into eval signal. Runs as
a **weekly job** (`session-quality`), **offset** from the golden-set schedule.

```
   weekly job: session-quality  (Sunday, ICT 16:00)
   ┌──────────────────────────────────────────────────────────────┐
   │ SAMPLE   select ≤N real sessions (deterministic, XG-fixed):    │
   │          has-a-correction  OR  turn-anomalous (>20 turns)      │
   │            one aggregate count query, hydrate ONLY the selected │
   │            (kills the old N+1 per-session read)                 │
   │   ▼                                                            │
   │ ③ SCORE  session_scorer.score_session(prompt, response, tools) │
   │          judge → {goal_score, tool_score, dimension, reason}   │
   │          empty prompt → skipped;  bad judge output → error      │
   │          (never fabricates a passing score)                    │
   │   ▼                                                            │
   │ RECORD   low scores → correction_tracker (drift radar)         │
   │   ▼                                                            │
   │ ② HARVEST  low-scorers (min(goal,tool) < threshold) →          │
   │          session_harvest.harvest_draft → an assertion-style    │
   │          golden case, tier=DRAFT, source=session_id            │
   │   ▼                                                            │
   │ HUMAN    ratify in dashboard (Promote) → tier=active           │
   │          NEVER auto-promoted                                    │
   └──────────────────────────────────────────────────────────────┘
```

- **`session_scorer.py`** — scores one real session on two axes (goal + tool
  selection) and attributes the primary weakness to one of the 6 dimensions.
  Injected `judge_fn` keeps it pure/testable (prod uses Bedrock).
- **`session_harvest.py`** — drafts a golden case from a low-score session via a
  fixed skeleton (`category=quality`, `eval_method=llm`, `tier=draft`,
  `affected_by=[]` — honest, no fabricated ref). **Never** auto-promotes.
- **`session_quality.py`** (job handler) — orchestrates sample→score→record→harvest;
  the sampler is **fail-loud** (an exception returns `sampler_error` → executor
  maps to `partial`, never a silent "clean pass on 0 sessions").

> This loop shipped once **inert** (name-mismatch bugs masked by a blanket
> `except` → 0 sessions enumerated, reported as success). The lesson: a scheduled
> job tested only with injected fakes never hit the real DAO boundary. It is now
> repaired + fail-loud + covered by DAO-contract tests.

---

## Triggers & Gates — WIRED vs NOT

Eval is a **decoupled system-level subsystem** (DEC05/PIT179). It is triggered by
**CI (post-push) / deploy / scheduled jobs** — never by the agent mid-pipeline.

### WIRED (real, in production)

| Trigger | When | Runs | Blocking? |
|---------|------|------|-----------|
| **Change-triggered** (agent PostToolUse hook) | Edit/Write to SOUL/AGENT/STEERING/MEMORY/EVOLUTION | only `affected_by`-matched cases, **behavior excluded**, background thread | No |
| **Scheduled full eval** (`eval-scheduled` job) | Monday ICT 18:30 (`30 10 * * 1`), **biweekly-gated** | full golden set incl. `include_behavior=True` (real agent spawns); alerts on BVT-red / score-drift / coverage-collapse | No (monitoring + Slack) |
| **Session quality** (`session-quality` job) | Sunday ICT 16:00 (`0 8 * * 0`) | Layer ②③ real-session loop | No |
| **Git-bound gate** (`ci_eval_gate.py`) | pre-push (dev) + `prod.sh release` | a **check, not a run**: is the committed report FRESH (code_digest matches) AND GREEN (bvt) AND no red-line? | **YES** — blocks release |
| **Post-deploy canary** | after daemon restart in `prod.sh release` | programmatic-only cases (fast) | No (warning) |

**The `ci_eval_gate` "stale" state is expected and self-clearing.** `code_digest`
= a hash of the eval-relevant working-tree (eval_runner / ci_eval_gate /
golden_case_validator / eval_service + public golden_set). Editing eval code or
the golden set makes the committed report **stale** → gate blocks until eval
re-runs **as a system concern (post-deploy/scheduled)** and a fresh report is
committed. It does **not** clear by the agent re-running eval against the old
binary — that path is hook-denied.

**`eval_command_guard`** (PreToolUse Bash hook) DENIES `eval_runner.py run`,
`ci_eval_gate.py` (at exec position), and `eval_service … run` in the agent's
Bash path — because eval on un-deployed changes tests the old binary, wastes
tokens, and once hung the judge's Bedrock call. There is no legitimate agent-side
use, so the deny has zero cost.

### NOT wired (deliberately)
- **Full eval in GitHub Actions CI.** `eval-gate.yml` runs only the **gate
  machinery unit tests** (stamp canonicaliser, BVT admission, validator teeth) —
  it honestly soft-passes the result check, because the golden set is
  private/gitignored and CI has no workspace. The real result-gate is local
  (`prod.sh release`).
- **Full eval post-deploy.** Deploy runs the fast **canary** (programmatic-only),
  not the full ~biweekly behavior sweep — the full run is costly + slow and would
  block the release flow.

---

## The EvalDashboard (in-product surface)

`desktop/src/pages/EvalDashboard.tsx` — the operable lens over the whole
subsystem. Tabs:

| Tab | Shows |
|-----|-------|
| **Overview** | OS Health Score, Intelligence Velocity, cases passed/failed, dimension grid, recent runs, Run-Canary / Run-Full buttons |
| **Golden Set** | live breakdowns (category/tier/eval_method/dimension), filter bar, grouped case table with **public/private origin badges**, case-detail drawer, Add/Import/Archive/Run |
| **Session Quality** | Layer ②③ overview (scored / low / drafts / pending), weekly low-rate trend, **pending-draft queue with Promote/Discard** (the human-ratify gate), low-score session table |
| **Context Health** | semantic drift (DDD self-audit), stale docs, pending proposals, auto-refresh log, at-risk golden cases |
| **Governance** | pending rule/gate proposals + evidence + Accept/Reject/Defer |
| **Trends** | overall + per-dimension sparklines, per-run detail |
| **Reports** | HTML eval report list (open in browser) |
| **Guide** | the authoritative in-product 3-layer methodology doc + architecture diagrams |

The **Guide tab is the authoritative in-product description** and stays aligned to
the 3-layer model; this design doc and the Guide should agree — if they diverge,
reconcile both against code.

### Read APIs (`backend/routers/eval.py`)
`GET /eval/health` · `/eval/history` · `/eval/golden-set[/{id}]` (private cases
allowlist-projected) · `/eval/runs/{id}` · `/eval/session-quality[/drafts]` ·
`/eval/context-health` · `/eval/governance/pending` · `/eval/reports[/{file}]`.
Mutations: case CRUD, `/eval/run`, `/eval/run-cases`, `/eval/canary`,
`/eval/promote-stable`, `/eval/governance/decision`.

---

## Diagrams (rendered in the Guide tab)

Live SVGs under `desktop/public/` (EN + ZH), rendered inline by `GuideTab()`:
- `eval-architecture.svg` / `eval-architecture-zh.svg` — the WRITE → GOLDEN SET →
  EXECUTE → CONSUME flow + the Layer ②③ real-session loop.
- `eval-sequence.svg` / `eval-sequence-zh.svg` — one run end-to-end: trigger →
  load+guard → spawn clean session → per-case dispatch → score + BVT → persist
  (code_digest-bound) → consume (gate hard / scheduled soft).

These are hand-authored assets — when the model changes, **re-sync the SVGs in the
same change** (a stale diagram is the highest-risk doc rot; nothing auto-regenerates it).

---

## Result storage & health score

- **Per-run snapshot:** `~/.swarm-ai/SwarmWS/Eval/EvalHistory/{date}_{trigger}.json`
  — run_id, trigger, overall_score, per-dimension scores, per-case
  {status, evaluator, eval_method, duration_ms, notes}, bvt block, redline block,
  `code_digest` (git-binds the report to the commit).
- **OS Health Score** = weighted aggregate of the 6 dimensions (weights live in
  code, adjustable). **Alive ≠ correct** — BVT green means the spine passed, not
  that every dimension is perfect.
- **Intelligence Velocity** — a compound "is the OS getting smarter, or just
  maintaining?" metric (pass-rate × coverage-growth × correction-decay ÷ window).

---

## Key Design Decisions

1. **Eval is decoupled + agent-blocked.** System concern, not a pipeline step —
   enforced by `eval_command_guard`, not just prose.
2. **Cheapest-evaluator-first waterfall.** Deterministic short-circuits before any
   LLM/agent cost.
3. **Behavior cases spawn REAL agents with tool-name-anchored matching.** This is
   what makes a case non-circular — it proves the agent *used* memory/DDD, not that
   a judge handed the answer.
4. **Judge pinned while prod drifts.** Prevents both degrading blind together.
5. **Judge-infra failure = `error`, not `skipped`.** No fake 100% on a subset.
6. **Two orthogonal gates:** BVT (coverage + drift-stamp) and red-line (zero-tolerance).
7. **Privacy by split-write + allowlist projection.** Instance data never ships.
8. **Layers ②③ never auto-promote.** Machine finds WHAT; human decides HOW/whether.
9. **No frozen volatile numbers** — counts/sizes are measured live (R30#4).

## Non-Goals
- Not an external test suite — it evaluates cognition, not code correctness (that's pytest/CI).
- Not a per-commit full eval — the full behavior sweep is biweekly; per-edit is affected-cases only.
- Not agent-runnable — the agent never triggers eval by hand.
- Not a fixed-size corpus — the golden set grows from corrections + harvested drafts.

---

## Source-of-Truth Map (verify here, don't trust prose)

| Claim | Source (file) |
|-------|---------------|
| Run engine, evaluator dispatch, scoring, BVT, redline, code_digest | `backend/scripts/eval_runner.py` |
| 6 dimensions (`DIMENSIONS`, `_DIM_TO_SNAPSHOT_KEY`) | `backend/scripts/eval_runner.py` |
| 4-gate admission (schema/dup/non-vacuous/privacy + teeth/refs/redline) | `backend/scripts/golden_case_validator.py` |
| Case CRUD, split-write, allowlist projection, singleton, APIs | `backend/core/eval_service.py` |
| Layer ③ scorer | `backend/core/session_scorer.py` |
| Layer ② harvester | `backend/core/session_harvest.py` |
| Layer ②③ job orchestration + sampler | `backend/jobs/handlers/session_quality.py` |
| Job schedules (`eval-scheduled`, `session-quality`) | `backend/jobs/system_jobs.py` |
| Scheduled-eval handler (biweekly gate, alerts) | `backend/jobs/handlers/eval_scheduled.py` |
| Git-bound gate (freshness+green+drift) | `backend/scripts/ci_eval_gate.py` |
| CI workflow (machinery unit tests, soft-pass) | `.github/workflows/eval-gate.yml` |
| Agent-block on eval | `backend/core/security_hooks.py` (`eval_command_guard`) |
| Change-triggered affected-cases hook | `backend/core/eval_hooks.py` |
| `s_golden-case` operations (ADD/PROMOTE/VALIDATE/AUDIT) | `backend/skills/s_golden-case/` |
| Dashboard tabs, Guide, breakdowns | `desktop/src/pages/EvalDashboard.tsx`, `eval-breakdowns.ts` |
| Read/mutation APIs | `backend/routers/eval.py` |
| Diagrams | `desktop/public/eval-*.svg` |

---

## Appendix A: Rob's Deck vs Our Design

| Rob Says | We Do |
|----------|-------|
| "Buy the tooling, own the content" | We own the harness AND the content — no platform dependency |
| "Programmatic where you can, judge where you must" | Cheapest-first waterfall: deterministic short-circuits before LLM/agent |
| "Golden set = your eval IP" | Ours = crystallized correction + real-session-harvest history |
| "Make it a CI gate" | Git-bound freshness+green gate (`ci_eval_gate`) + red-line veto |
| "Feed failures back in" | Corrections + Layer② drafts → human ratify → prevent recurrence |
| "Improve the harness, not the weights" | We evolve SOUL/AGENT/STEERING, not the model |
| "Eval is spec + gate + monitor + reward" | Exactly — defines good, gates change, monitors drift, rewards self-improvement |

## Appendix B: AgentCore — what we borrowed (and what we didn't)

> Source: AWS Bedrock AgentCore Evaluations docs (Jun 2026). We study it for
> proven patterns, not to adopt (wrong scale for a single-user OS).

**Borrowed:**
- **Three-layer ground truth** (output / trajectory / assertions that *compose*)
  → our `expected_response_contains` / `expected_trajectory` / `assertions`.
- **Three evaluation levels** (tool-call / trace / session) → our `level:` field.
- **Custom LLM-as-judge + code-based evaluators** → our `goal_success`/`quality_score`
  + programmatic evaluators.
- **Dataset + online eval split** → our Layer ① (dataset) + Layer ③ (online sessions).
- **Simulation (actor persona)** → our behavior-tier `trajectory_capture` (a clean
  agent driven by the scenario).

**Not adopted:** OpenTelemetry span pipeline, managed service, 10%-traffic online
sampling, A/B canary — enterprise-scale machinery a single-user OS doesn't need.
Our differentiators AgentCore lacks: **diff-scoped `affected_by` eval**, a
**correction/session → golden flywheel**, and **harness-not-weights** improvement.
