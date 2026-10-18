# INSTRUCTIONS ref — Rule 23 (adversarial spawn) and Rule 24 (per-stage required fields)
Read when: a Gate-0/1/2 spawn is rejected or fails, the validator BLOCKS on `adversarial_review`
(`spawned` / `evidence`), or a publish / stage-complete / run-update / run-commit rejects a missing
field. Rule 23's bounded-spawn + foreground block is on the card ("Sub-agent spawns").

## Rule 23
<!-- R-instr-036 -->
**[GATE·cli] Adversarial review = Agent tool spawn (MANDATORY for full/bugfix).** Gate 2
(Adversarial Review) for full/bugfix profiles MUST use the Agent tool to spawn an independent
sub-agent. Self-review disguised as adversarial (declaring `spawned=true` without an Agent tool
invocation) is structurally equivalent to skipping the gate. If spawning is infeasible (context
limit), CHECKPOINT — don't fake it.

**For the three GATE spawns, do not compose the prompt — emit it.** Gate-0's skeptic, Gate-1's
Skeptic+SSA and Gate-2's specialists each have their prompt in `data/gate-prompts/`;
`scripts/spawn_prompt.py --gate <id>` prints it ready to paste (`stage-start --stage review|deliver`
returns ready prompt files too). Use the output ONLY on exit 0: exit 2 means the prompt states no
numeric cap and stdout is deliberately EMPTY, so a budget-less prompt cannot be emitted. Emitting
keeps the cap in code. An ad-hoc spawn (not one of the three gates) still needs you to write the
budget yourself, per the card's bounded-spawn rules.

**Spawn REJECTION is fail-closed (`gate_spawn_blocked`).** Distinct from "infeasible": the
harness/tool layer can REJECT an Agent-tool spawn mid-turn ("The user doesn't want to proceed with
this tool use" / "does not want to take this action"). This is a transient tool-layer signal (a
prior `interrupt()` poisons the warm subprocess), NOT a structural ban on spawning, and it is
INVISIBLE to the backend (zero records in daemon.log — only the orchestrator sees it as a
tool_result). Strict decision tree, no deviation:
```
Gate-1 or Gate-2 Agent spawn REJECTED
  → retry EXACTLY ONCE (a fresh Agent call this same turn)
    → succeeds → proceed normally
    → rejected again → CHECKPOINT, reason="gate_spawn_blocked"
                       (resume re-enters on a fresh subprocess — the poisoning
                        clears across the process boundary)
```
**NEVER** fall back to self-review. "The spawn failed, so I'll review it myself" is the CLASS A
bypass (R1/STEERING #13) this gate exists to prevent; the tree has no review-it-myself branch.
**Retry once, not a loop:** an in-turn retry LOOP on a poisoned subprocess reuses the same poisoned
process (STEERING #1). The only safe retry crosses a checkpoint→resume process boundary.
`gate_spawn_blocked` is a VALID non-completion outcome (a resumable checkpoint), never scored as
failure.

**Two-field enforcement (code-enforced):** the validator requires BOTH
`adversarial_review.spawned: true` (declares the spawn happened) and
`adversarial_review.evidence: "<description>"` (HOW, non-empty, e.g. `"Agent tool invocation:
adversarial code reviewer"`). Missing/empty `evidence` → validator BLOCKS completion. So even an
agent that tried to self-review cannot reach `status: completed` without a genuine spawn. A
golden-set trajectory case also verifies the Agent tool appears in the execution trace.

## Rule 24
<!-- R-instr-038 -->
**[GATE·validator+cli] Per-stage REQUIRED fields — the single reference (corrected to validator
ground truth, F1).** The fields below are what `pipeline_validator.STAGE_SCHEMAS` / `STAGE_DEPTH`
ACTUALLY require (source of truth). REQUIRED-column fields BLOCK at publish [validator]; the FLAT
completion fields in table B BLOCK at `run-update` / `run-commit` time [cli]; Recommended = WARN.
`stage-start` returns a valid example and `stage-complete` reports each missing field by `path`
with the expected shape — prefer those over this table.

**A. Artifact-schema required fields (BLOCK at publish AND completion):**

| Stage | REQUIRED (validator BLOCKS if missing) | Recommended (WARN only) |
|-------|----------------------------------------|-------------------------|
| evaluate | `recommendation`, `scope` (+ `understanding` on strict profiles; + `ambiguity_scan`; + `working_backwards`/`pre_mortem` on greenfield; + `migration_class` on a migration-keyword requirement) | `acceptance_criteria`, `scores` |
| think | `key_findings` | `alternatives`, `sources` |
| plan | `acceptance_criteria` (+ `exit_criteria` at stage-complete on strict profiles — plan.md; + `security_boundaries` when `cross_boundary` is true) | `approach`, `data_model`, `boundaries`, `success_criteria` |
| build | `files_changed`, `tdd` (`.green_pass`+`.smoke_tests`), `ac_coverage` (list, non-empty, covers every PLAN AC) | `commits`, `diff_summary` |
| review | `approved`, `litmus_gate`, `integration_trace`, `runtime_patterns`, `findings_count` | `findings`, `security_findings`, `ux_review` |
| test | `passed`, `layers` (`.ac_driven`) | `failed`, `fixed`, `coverage`, `regressions` |
| deliver | `title`, `quality`; + `adversarial_review` (dict: `profile_tier`,`findings`), `completion_audit` (`.all_green`), `ac_verification` (`.status`) via STAGE_DEPTH; + `meta_review`, `convergence` for full/bugfix | `decisions`, `report_path` |
| goal_cycle | `dod_met`, `adversarial_review` (dict with `findings`) | `cycles_run`, `progress_path`, `review_cadence` |
| reflect | (no artifact schema — but `lessons` must be substantive at completion) | — |

Common mistakes: evaluate has `acceptance_criteria` only as RECOMMENDED, but **plan REQUIRES it**
(BUILD's AC-coverage cross-check reads `plan.acceptance_criteria`). think does NOT require
`alternatives` or `approach_chosen`; plan does NOT require `spec_summary`/`files_planned`. Record
the recommended fields for a rich `run-report`, but only the REQUIRED column BLOCKS.

**B. FLAT completion/commit-time fields (on the STAGE RECORD, not the artifact; `stage-complete`
fills the derived ones):**

| Flat field | On stage | Gate that reads it |
|------------|----------|--------------------|
| `stage_doc_consumed: true` | evaluate/build/review/test/deliver/reflect | `run-update` per-stage gate (exit 1 if absent) |
| `push_ready: true` | deliver | `run-commit` refuses without it (exit 2) |
| `outputs_surfaced` — NOT a stage-record field: written by the `surface_run_outputs` tool (marker beside run.json) or `run-update --status completed --outputs-surfaced` (run-level); a deliver-record flag is read only as legacy. Never re-record deliver to add it | — (completion) | completion surface gate |
| `token_cost: <int>` | every stage | metrics/calibration (WARN if 0) |
| `skip_reason` / `notes` | any SKIPPED stage | completion skip gate |
| `lessons: [...]` (each >20 chars) | reflect | reflect quality gate |

`run-report` reads the stage record as its PRIMARY source; a missing recommended field = an empty
report section (not a block).
