---
title: Pipeline Friction & Effectiveness — Overall Design
date: 2026-07-01
run: run_1a8eaf7c
profile: docs
status: DESIGN (no code shipped — awaiting XG approval on roadmap ordering)
scope: s_autonomous-pipeline universal layer (all projects, zero project-specialization)
author: Swarm
---

# Pipeline Friction & Effectiveness — Overall Design

## 0. Why this doc exists

Across one session (five pipeline runs) six recurring pains surfaced. XG's directive:
*"系统化的解决"* — one coherent design, not six patches. This doc's FIRST job is a
**triage** — because the strongest pull here is the C042 trap (*"一遇到问题就想造个机制"*)
which I committed twice this same session. So every concern is first classified:

- **TRUE-GAP** — a real structural hole; design a fix.
- **MECHANISM-EXISTS** — the capability is already built, just wired to the wrong place;
  move it, don't rebuild it.
- **DEFER** — building now would be premature (insufficient evidence) or an adherence
  problem prose/hooks can't fix; record the trigger that would change the verdict.

**The measurement concern (#5) is the strategic core** — a tool that guarantees code
quality but can't measure its own effectiveness is flying blind. Everything else is
friction reduction; #5 is the missing feedback loop (the half of AutoSDE we didn't port).

---

## 1. Triage Table (verdict + code-traced evidence, not assertion)

| # | Concern | Verdict | Evidence (file:line, this session) | Design weight |
|---|---------|---------|-------------------------------------|---------------|
| 1 | Bookkeeping friction (publish fails, hand-assemble payload, guess fields) | **ALREADY-FIXED (common path) — near no-op** | `cmd_schema` (artifact_cli.py:385) exists AND the **non-quiet publish failure branch already echoes `expected_schema`** (artifact_cli.py:233, verified). Only the `--quiet` branch omits it (:218-226) — and that's BY DESIGN (orchestrators choke on a multi-KB indented template). So my "failure branch doesn't echo schema" claim was WRONG for the common path. Residual: at most a one-line "re-run without --quiet to see schema" hint in the quiet branch. | XS (near-noop) |
| 2 | Profile trap (published `--stage build` in a docs run; smoke gate fired downstream; misdiagnosed as validator bug) | **TRUE-GAP** | `_check_profile_respected` (defined :3062) is called ONLY at :2270 inside `validate()` (standalone check, SEVERITY_HARD) — NOT in the publish path `validate_artifact_data` (:918). The gate exists — it's just at the wrong (later) choke point. **Verified by Gate-2.** | S |
| 3 | cwd drift (hand-typed `grep`/`cat` on source-repo vs workspace paths — C040) | **DEFER (adherence, not pipeline)** | `SWARM_WORKSPACE` env (artifact_cli.py:64) makes the TOOL cwd-immune — that's why `run-get` always resolved correctly while my raw shell didn't. The gap is MY hand-typed paths = C040 adherence kernel. Same family as the just-closed skeptic-failure-history thread: prose/hooks documented-ineffective against adherence. **Verified by Gate-2 (sound defer).** | XS (defer) |
| 4 | Gate quality rides on "agent diligently plays skeptic" | **DEFER-MOSTLY + one enforceable slice** | Gate-2 spawn IS code-enforced (Agent tool + `spawned`+`evidence` two-field). But finding TRUTH is self-reported. Lower-bound ("skeptic gets lazy") = 1/3 evidence (run_67508ac4), building = C042. ONLY code-enforceable slice: L4 verify-against-disk of `resolved:true` findings (`INSTRUCTIONS.md:581` documents "BLOCKING — never trust the resolved flag"; `findings[].resolved` exists at validator :456, `_blocked_findings` at :103, but no `disk_verified` marker). **Verified by Gate-2 (sound).** | S (one slice) |
| 5 | Pipeline effectiveness unmeasured | **⚠️ MECHANISM-EXISTS-BUT-ORPHANED (was mis-triaged TRUE-GAP — corrected by Gate-2)** | **`backend/scripts/pipeline_analytics.py` ALREADY EXISTS** (22KB, committed 2026-06-08 `5306ef84` "Pipeline Meta-Intelligence — full E2E"): `analyze_all_runs()` (:398) scans `Projects/*/.artifacts/runs/*/METRICS.json`, `main()` (:513) writes `pipeline_intelligence.json`, `generate_report()` (:425) renders a markdown health report with per-profile completion/abandon + gate/adversarial catch counts. **My "aggregator absent" claim was FALSE** — I checked `pipeline_intelligence.json` only in the runtime workspace (absent because nothing RUNS the script), never the source tree (C038/C040 kernel: verified in the wrong place). Real gaps are NARROW: (a) **zero live callers** — `grep` finds no external importer, no job invokes it; (b) **no data-completeness GATING** (it emits `sample_count`/`telemetry_coverage` at :262/:408 but never greys a sparse metric). | S–M (wire, not build) |

**Triage summary (corrected post-Gate-2): 1 TRUE-GAP (2), 1 orphaned-mechanism-to-wire (5), 1 enforceable slice (4-L4), 1 near-noop (1), 2 DEFER (3, 4-lower-bound).** **Building NEW code: essentially only concern 2 + the L4 slice.** Concern 5 is WIRE-existing-code, concern 1 is a one-liner. This is far less "build" than the first draft claimed — the C042 self-check FAILED on the draft's flagship item (proposed rebuilding `pipeline_analytics.py`) and was caught only by Gate-2 reading the source tree.

---

## 2. Per-Concern Design

### Concern 1 — Bookkeeping: near-noop (ALREADY-FIXED on the common path)

**Problem (as I first framed it).** "publish failure names the missing field but not the
full shape → I hand-guess." This session that loop ran ~10× in one run.

**Correction (L2, Gate-2-verified).** The **non-quiet** publish failure branch ALREADY
appends `expected_schema` (artifact_cli.py:233, via `get_stage_schema`). The `--quiet`
branch (:218-226) omits it — deliberately, with a documented rationale (orchestrators choke
on a multi-KB indented template). So my ~10× loop this session was **self-inflicted**: I ran
`--quiet` (correct for a pipeline) and never re-ran without it to see the template that was
one flag away. This is NOT a code gap — it's me not using the tool that exists.

**Residual (optional, XS).** Add a one-line hint to the QUIET failure JSON:
`"hint": "re-run without --quiet, or 'artifact_cli.py schema --stage X', for the template"`.
That's the entire legitimate change. **Do NOT re-add `expected_schema` to the quiet branch**
(it exists non-quiet by design; duplicating it defeats the quiet-mode rationale).

**Blast radius.** One string in the quiet failure branch, or nothing at all.

### Concern 2 — Profile trap: move the profile check to publish (TRUE-GAP, P7)

**Problem.** Publishing an off-profile stage (`--stage build` in a `docs` run) is only
caught at completion-time `validate()`, not at publish. The downstream *symptom* (smoke
gate) fires instead, and reads like a validator bug (it isn't — the gate is correct).

**Design (P7 — gate at the earliest choke point).** Call `_check_profile_respected` (defined
:3062, currently called ONLY at :2270 inside `validate()`) inside `validate_artifact_data`
(the publish path, :918), fail-closed:
`"'build' is not in the 'docs' profile — did you mean profile 'trivial' (which has build+review+test)? Re-run run-create with the right profile, or use the profile whose stage-list matches your work."`
The check already exists; this is a **call-site addition**, not new logic. **This is the
ONE concern in the whole doc that is a genuine build-new-wiring on a real structural gap.**

**Divergence guard (R27).** The publish-path and completion-path profile checks must share
ONE helper (`_check_profile_respected`) — do not fork the logic. (This is the exact
divergence class that bit the smoke-gate twin at :1081 vs :2337.)

**Why this also prevents the misdiagnosis loop.** If publish had blocked me at
`--stage build` in a docs run, I would never have hand-forced it, never hit the smoke
gate, never spent a run misdiagnosing a correct gate as a bug. Fixing #2 structurally
removes the confusion that generated the phantom "#validator-bug".

### Concern 3 — cwd drift: DEFER (adherence, not a pipeline gap)

**Verdict: DEFER, lean do-not-build.** The tool is already cwd-immune (SWARM_WORKSPACE).
The failure is my hand-typed shell paths (C040), an *adherence* problem. This is the SAME
family as the skeptic-failure-history thread I closed this session: **prose/hooks are
documented-ineffective against adherence failures** (I committed C040 live mid-session
*with* the Self-Identity Anchor in context).

**Candidate (only if it recurs past the bar).** A lightweight PreToolUse warn: when a Bash
command contains a `.context/` or `Projects/` **relative** path AND cwd ≠ workspace → warn
(not deny). But:
- **C042 check:** is this "building a mechanism because a problem annoyed me"? Partly yes.
- **Evidence bar:** C040 is a known multi-occurrence class, so a *warn* (not a gate) is
  defensible IF it's near-zero-cost and fail-open. But it belongs to the C040 gate family
  (`pytest_command_guard` etc.), NOT this pipeline design.
- **Verdict:** carve OUT of this design. If built, it's a standalone hook PR in the C040
  family, decided on its own merits — not bundled with pipeline friction. Recorded here so
  it's not lost, deliberately NOT in the roadmap.

### Concern 4 — skeptic trust: one enforceable slice, rest deferred (honest)

**Verdict: DEFER the lower-bound, build ONLY the verify-against-disk slice.**

The honest truth (established by run_67508ac4, a full research run): the "skeptic gets
lazy / poisoned" lower-bound problem has **1/3 evidence**, the sanctioned fix (per-error
code gate) can't even be *designed* without ≥3 concrete errors, and recall-injection is
KILLED (2026-06-27). Building anything broad here = C042. **This design does NOT attempt
to solve "is the skeptic reliable" — that stays deferred with a REOPEN-at-3 trigger.**

**The ONE code-enforceable slice.** deliver.md's L4 already SAYS "verify-against-disk:
for each `resolved:true` finding, grep the durable change is actually on disk" — but it's
prose, not code-enforced. Design: `validate_artifact_data` (deliver stage) checks that each
`adversarial_review.findings[].resolved==true` carries a `disk_verified` marker (or the
completion gate warns). This converts a self-reported "I fixed it" into a checked "the fix
is on disk" — the exact C011-class ("record said done, disk said otherwise") the L4 prose
was written for. Small, bounded, high-value.

### Concern 5 — Pipeline effectiveness measurement (TRUE-GAP, strategic core)

**Problem (RE-STATED post-Gate-2).** The raw data is collected per-run (`METRICS.json`)
AND an aggregator that turns it into a cross-run health report **already exists** —
`backend/scripts/pipeline_analytics.py` (`analyze_all_runs` :398 → `pipeline_intelligence.json`
via `main` :513; `generate_report` :425 renders markdown with per-profile completion/abandon
+ gate/adversarial catch counts). **So this was NEVER a "build the aggregator" gap.** The
real reason I judge pipeline health by impression: **the aggregator is orphaned (zero live
callers, no scheduled job runs it) and it doesn't gate on data-completeness.** This is the
AutoSDE half we didn't *operationalize* (we built it, then never wired it).

**Metric set — MOSTLY ALREADY COMPUTED by `pipeline_analytics.py` (audit, don't rebuild):**
- completion rate + per-profile abandon rate — ✅ exists (`generate_report`)
- gate catch counts (Gate-0/1/2) — ✅ exists
- adversarial findings fixed/dismissed + severity — ⚠️ verify coverage; extend only if missing
- decisions mechanical/taste/judgment — ⚠️ verify
- **data-completeness gating — ❌ MISSING, this is the real additive work (below)**

**⚠️ Data-completeness / anti-C044 honesty design (the genuine additive work).**
Correction (M2): `total_tokens==0` is **~8% of runs (23/283 measured), not "many"** — I
overstated it ~10× because MY OWN hand-filled runs this session had 0 and I generalized from
bad data. Token blindness is minor; the genuinely sparse dimensions are others (`goal`
profile ≈ 0 runs; adversarial on relaxed profiles). `pipeline_analytics.py` already emits
`sample_count` (:262/:342/:357) + `telemetry_coverage` (:408) but **never greys a sparse
metric**. The additive design: `generate_report` renders any dimension whose
`sample_count / eligible_runs < 0.5` as **`insufficient data (n/N)`**, never a confident
number. **Reuse the proven prior art**: `eval_service.py:684 score = base_score * coverage`
(`compute_intelligence_velocity`, :627) already separates quality (pass_rate) from
measurement-completeness (coverage) and discounts the score by coverage — mirror that exact
pattern, don't reinvent it. Anti-C044: a metric rendered green over sparse data is the
"修表面让指标好看" failure; completeness is first-class.

**Architecture (phased — see roadmap; note: the CORE already exists):**
- **Core:** `pipeline_analytics.py` — **already built.** Work = AUDIT its metric set vs the
  list above + ADD completeness gating. Not "build an aggregator."
- **Surface A (phase 1):** a weekly job runs the EXISTING `pipeline_analytics.py --report`
  → lands markdown in `Knowledge/Reports/pipeline-weekly.md` (reuses ddd-weekly pattern).
  This is a **job-config + completeness-gating** task, not new core logic.
- **Surface B (phase 2, conditional):** backend route on `routers/eval.py` + a **Pipeline
  Tab** on `EvalDashboard.tsx` (clean activeTab pattern :247 — react-query polling, NOT
  streaming; joins golden-set/context/reports tabs).

**Why phased.** Surface B (frontend Tab) is a new backend route + an additive tab. **M1
correction: this is NOT the OT01 hot zone** — `EvalDashboard.tsx` uses react-query polling,
zero `EventSource`/SSE (verified); OT01 lives in the chat streaming path, unrelated. So Run
D's real risk is ordinary (route + additive tab), *lower* than the first draft claimed.
Phasing is still right, but for a narrower reason: **phase 1 proves the (existing) metric
set is useful & honest before spending on a tab** — not because the tab is dangerous.
The concrete gate for authorizing phase 2 is in §4 (Run D), not a vibe.

---

## 3. 3-Way Tradeoff (measurement layer)

| Constraint | What | Effort | Risk | Verdict |
|-----------|------|--------|------|---------|
*(Note: the "aggregator" already exists — `pipeline_analytics.py`. Every option below is
WIRE + completeness-gating on existing code, NOT build-the-core.)*

| Constraint | What | Effort | Risk | Verdict |
|-----------|------|--------|------|---------|
| **SPEED** | Just document + run the existing `pipeline_analytics.py --report` manually when curious | XS | Stays invisible/manual — same "measured-not-surfaced" gap | ❌ doesn't deliver the outcome |
| **QUALITY** | Weekly job + route + Pipeline Tab + completeness gating, all on the existing aggregator | M | Multi-surface (R16 — smoke each leg); tab is additive react-query (NOT OT01, M1) | ✅ the target, split into runs |
| **DELETION** | Weekly job runs existing aggregator → markdown report + completeness gating, NO Tab | S | Markdown less glanceable than a tab; trivially removable | ✅ **phase 1** — 80% value, no frontend surface |

**Recommendation: DELETION-first (phase 1), then QUALITY's Tab as a follow-up ONLY IF phase-1
markdown proves the metrics.** Rejects SPEED (stays manual/invisible). Note the effort
dropped from the first draft's "M-L" to "S/M" precisely because the core exists — the honest
work is wiring + completeness gating, not building an aggregator.

---

## 4. Roadmap (PRI07 — grouped by pipeline RUN, not tasks/hours)

Ordered by evidence-strength and dependency. Each run is independently committable + verifiable.

- **Run A — `bugfix`: profile-check forward to publish (concern 2 — the ONE real structural
  gap).** Call `_check_profile_respected` (:3062) inside `validate_artifact_data` (:918),
  fail-closed with a profile hint. **R27 note (L1a correction): today there is only ONE
  call site (:2270); Run A CREATES the second — so R27 is prospective (both new+old MUST
  call the one helper, never fork the logic — the divergence class that already bit the
  smoke-gate twin :1081 vs :2350).** Fold in concern 1 ONLY as a one-line "re-run without
  --quiet for schema" hint in the quiet branch (the non-quiet branch already echoes
  `expected_schema` at :233 — do NOT re-add it). Smoke: publish off-profile stage → blocked
  with hint.
- **Run B — `bugfix`: L4 verify-against-disk slice (concern 4's ONLY buildable piece).**
  Code-enforce `resolved:true` findings carry a `disk_verified` marker in the deliver-stage
  validator (`INSTRUCTIONS.md:581` already declares it BLOCKING; validator :456/:103 has the
  structure). Standalone; depends on nothing.
- **Run C — `bugfix`/`full`: WIRE the existing aggregator + completeness gating (concern 5,
  part 1). NOT "build the aggregator" — it exists (`pipeline_analytics.py`).** Work: (a)
  AUDIT `generate_report`'s metric set vs the §2 list, extend only what's genuinely missing
  (verify adversarial fixed/dismissed + decisions coverage); (b) ADD completeness gating
  (`sample_count/eligible_runs < 0.5 → "insufficient data (n/N)"`, mirroring
  `eval_service.py:684`); (c) a weekly job runs it → `Knowledge/Reports/pipeline-weekly.md`.
  Smoke: feed real METRICS.json, assert a sparse dimension renders greyed (not green).
- **Run D — CONDITIONAL: measurement phase-2 Tab (concern 5, part 2).** Backend route on
  `routers/eval.py` + additive Pipeline Tab on `EvalDashboard.tsx` (react-query, NOT OT01 —
  M1). **Concrete authorization gate (M3, not a vibe): Run D is authorized IFF (1) the
  Run-C weekly markdown has run ≥3 weeks, AND (2) ≥3 metric dimensions clear the 50%
  completeness bar, AND (3) XG reads a markdown and says the metrics are worth a glanceable
  home.** Absent all three, D stays deferred — not perpetually, but on a checkable trigger.
- **NOT in the roadmap — concern 3 (cwd warn).** Carved out: if built, a standalone hook in
  the C040 family, decided on its own merits. Recorded in §2 so it's not lost.

**Dependency graph:** A ⊥ B ⊥ C (all independent — C wires existing code, doesn't build a
core). D gated on C per the M3 trigger above. Suggested order: A (the one real gap) → C
(wire the existing aggregator = the strategic outcome) → B (slots anywhere) → D (only if the
3-part gate clears).

---

## 5. Non-Goals (explicit — prevent a future run re-introducing rejected ideas)

0. **NOT rebuilding `pipeline_analytics.py`.** ⚠️ The aggregator that scans METRICS.json →
   `pipeline_intelligence.json` + markdown report ALREADY EXISTS (committed 2026-06-08). The
   first draft of THIS doc proposed building it as new — the C042 trap on its own flagship
   item, caught by Gate-2. Concern 5 is WIRE + completeness-gating on existing code. Any
   future run that starts "let's build the pipeline aggregator" is re-committing this error —
   `grep pipeline_analytics.py` FIRST.
1. **NOT rewriting the bookkeeping/artifact layer to "cut ceremony."** The audit trail IS
   the product (C042 lesson, run_c236e4b1). Concern 1 is now known to be near-noop (the
   non-quiet failure branch already echoes `expected_schema`) — at most a one-line quiet hint.
2. **NOT building a gate/mechanism for the skeptic lower-bound at <3 evidence.** run_67508ac4
   established 1/3; recall-injection is KILLED (2026-06-27). REOPEN only at the 3rd distinct
   occurrence. Building now = C042.
3. **NOT shipping any effectiveness metric without its data-completeness.** An empty/partial
   metric rendered as a confident green number is C044 ("修表面让指标好看"). Completeness %
   is first-class; <50% → greyed/"insufficient data".
4. **NOT a cwd-drift fix inside this pipeline design** (concern 3 is adherence — belongs to
   the C040 hook family if built at all).
5. **NOT project-specialized.** Every element is universal pipeline-layer; metrics aggregate
   across ALL projects' runs.
6. **NOT task/hour estimation.** Progress is run pass/fail (PRI07).

---

## 6. Success Criteria (for the runs this design authorizes)

- Run A: publishing an off-profile stage is blocked AT PUBLISH with a profile hint;
  publish failure echoes the stage schema. (Removes the class of confusion that spawned the
  phantom validator-bug this session.)
- Run B: a `resolved:true` finding whose fix is NOT on disk is caught by the deliver gate.
- Run C: `pipeline-weekly.md` exists, shows Gate-catch counts + adversarial fixed/dismissed
  + per-profile completion/abandon, AND every metric shows its `n`/completeness %; a metric
  with sparse data renders greyed, not green.
- Run D (if reached): the same data is glanceable as a Tab without frontend regression.

**The meta success criterion:** after Run C, the answer to *"is the pipeline earning its
keep?"* is a measured number with a known confidence, not an impression.
