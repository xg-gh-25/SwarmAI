# INSTRUCTIONS ref — Step 3 stage execution (3a–3c, 3f)
Read when: a stage's gate check is unclear (retry limits, pending L2), you are on the legacy
publish / run-update / advance path, you need the DDD-doc or upstream-artifact routing tables,
or `stage-start` is unavailable. The stage-start / stage-complete loop is on the card.

## Stage Feedback Loop (Per-Stage Preamble)
Before executing each stage, establish its feedback loop:
```
1. SIGNAL — What observable output proves this stage succeeded?
   (artifact published, test passing, file exists, command returns expected)
2. CHECK  — How do I verify the signal is real, not assumed?
3. FAIL   — What does failure look like? (define it so you recognize it immediately
   instead of rationalizing partial success)
```
Examples. BUILD: SIGNAL every AC has a passing test (`pipeline_test.py --mode tdd` green for it);
CHECK run it and read the output; FAIL any test red, any AC without a test, a test that tests the
wrong thing. REVIEW: SIGNAL 0 high/medium findings (confidence ≥ 7) in the specialist outputs;
CHECK count findings by severity after confidence gating; FAIL any finding ≥ medium with
confidence ≥ 7, or a specialist returned vague non-findings.
Output the SIGNAL/CHECK/FAIL as inline text in chat before executing the stage, so the user can
correct a wrong signal before tokens are spent against it. (Why: `stages/history/instructions.md`.)

## 3a. Gate Check
Before executing:
```
1. Retry exhaustion?  → stage retry_count >= max_retries → stop fixing, DELIVER with
   push_ready false (never a checkpoint)
2. Pending L2 decision? → ask the user (`escalation.md`): in-band if `channel=direct`, else
   the recorded question + checkpoint. A run resumed from a question checkpoint reads the
   answer first.
3. Pipeline cancelled? → EXIT
```
Max retries per stage: evaluate 2 · think 2 · plan 2 · build 3 · review 2 · test 3 · deliver 1 ·
reflect 1.

## 3b. Load Stage Context
**DDD documents (stage-scoped)** from `Projects/<PROJECT>/`; skip any that don't exist or hold only
template placeholders:

| Stage | DDD docs | Stage | DDD docs |
|-------|----------|-------|----------|
| evaluate | PRODUCT, TECH, IMPROVEMENT, PROJECT | review | TECH, IMPROVEMENT |
| think | PRODUCT, IMPROVEMENT | test | TECH, IMPROVEMENT |
| plan | PRODUCT, PROJECT | deliver | PROJECT |
| build | TECH, PROJECT | reflect | IMPROVEMENT |

**Progressive loading (T6):** for a large DDD doc (>~30K) don't read the whole file. Grep its `## `
headings for a section map, then read only the section(s) relevant to THIS stage's task with
`offset/limit` (e.g. BUILD on session code → TECH.md `Key Subsystems`, not the full file).

<!-- R-instr-002 -->
**[GATE·validator] Upstream artifacts (Output Routing — BLOCKING):** each stage has declared inputs
it MUST consume. The validator (Check 13) enforces this — if `consumed_artifacts` is present in the
stage record but doesn't include a declared input type, the stage is BLOCKED. `stage-start`
returns the upstream artifacts and `stage-complete` fills `consumed_artifacts` for you; on the
legacy path load them with
`python backend/scripts/artifact_cli.py discover --project <PROJECT> --types <comma-separated> --full`.

| Stage | Must consume (STAGE_ROUTING) | Must produce |
|-------|------------------------------|--------------|
| evaluate | — | evaluation (+ `understanding` block — Understanding Gate below; + `cross_boundary` flag — evaluate.md Cross-Boundary Classification) |
| think | evaluation (`understanding` must be SUPPORTED) | research |
| plan | evaluation, research | design_doc |
| build | design_doc | changeset |
| review | changeset | review |
| test | changeset, review | test_report (+ `cross_boundary_e2e` when EVALUATE set `cross_boundary=true` — test.md Layer 4) |
| deliver | changeset, review, test_report | delivery |
| reflect | test_report, delivery | — |

Legacy path: record what you loaded in the stage record's `consumed_artifacts` (routing
enforcement + freshness tracking), via `--stage-json`:
```bash
python backend/scripts/artifact_cli.py run-update --project <PROJECT> --run-id <RUN_ID> \
  --stage-json '{"stage":"<STAGE>","status":"completed","stage_doc_consumed":true,...,"consumed_artifacts":[{"type":"<TYPE>","id":"<ART_ID>"}]}'
```
A **stale** upstream artifact (DDD docs changed since it was created, or age > 7 days) makes the
validator WARN. You may proceed — staleness is a signal, not a blocker — but say so in the stage
notes.

<!-- R-instr-003 -->
### Understanding Gate (EVALUATE→THINK boundary — ALL work types) — [GATE·validator]
THINK (proposing *how* to fix/build) **cannot load** until EVALUATE produces an observation-backed,
refuted **understanding of the present**. This generalizes the old bug-only REPRO gate to every
work type — the evidence form varies (code-trace for existing-feature, repro for bugfix,
characterization for refactor, premortem for greenfield/research), the gate is universal.
`stages/evaluate.md` (Understanding Gate) has the `understanding` block shape, the work-type
evidence table and the M1/M2/M3 mechanisms.
Code-enforced at publish by `pipeline_validator.validate_artifact_data`: strict profiles
(full/bugfix/goal) BLOCK without an `understanding` block carrying real `evidence`
("Understanding gate:" marker); M1 blocks a solution-language `claim`, M2 blocks an unresolved
hedge. Bug-class evals may use the legacy `observation_evidence` alias (keeps the "REPRO gate:"
marker). Relaxed profiles (trivial/docs/research) aren't forced to carry the block, but a present
block is still scanned. M3 (the skeptic sub-agent) is behavioral, spawned in evaluate.md.

## 3c. Execute Stage Behavior — dispatch table
The BLOCKING read rule is on the card (read the execution card; read `stages/ref/` only when the
card's condition holds). All stage docs live in `backend/skills/s_autonomous-pipeline/stages/`.
Read from `backend/skills/` (source of truth), NOT `.claude/skills/` (a projected copy that can be
stale within the same session).

<!-- R-instr-005 -->
| Stage | Read (BLOCKING) | Scripts to Run |
|-------|-----------------|----------------|
| evaluate | `stages/evaluate.md` | `scripts/spawn_prompt.py --gate gate0-skeptic` (Gate-0 M3 skeptic) |
| think | `stages/think.md` | — |
| plan | `stages/plan.md` | — |
| build | `stages/build.md` | stage-start `gate1_prompt_path` (or `gate1_verdict`: already judged, do not respawn); fallback `scripts/spawn_prompt.py --gate gate1-skeptic` (Gate-1 Skeptic+SSA) |
| review | `stages/review.md` (it runs `spawn_prompt.py --patterns-for <files>` for the triggered `REVIEW_PATTERNS.md` rows instead of a full read; `OPERATIONAL_PATTERNS.md` is read where `stages/ref/review/checks-system.md` says so) | stage-start `review_knowledge_path` + `review_diff_path`; fallback `scripts/spawn_prompt.py --knowledge-only --project <P> --files <changed…>` (project-knowledge block per fan-out agent) |
| test | `stages/test.md` | `scripts/wtf_gate.py` (stage-complete computes it on the new path) |
| deliver | `stages/deliver.md` | stage-start expert `prompt_path`s + `meta_review_prompt_path`; fallback `scripts/spawn_prompt.py --gate gate2-adversarial` / `--gate meta-review` `--project <P> --files <changed…>` (Gate-2 specialists, Meta-review) |
| reflect | `stages/reflect.md` | — |
| goal_cycle | `stages/goal_cycle.md` | — |
| complete | `stages/complete.md` (Step 6 output contract; fresh-read at the decision point) | — |

`stage-start` already returns the build / review / deliver prompt files named above; the
`spawn_prompt.py` part of each row is the fallback when a path is missing. After reading, execute the stage behavior inline in this
session. DO NOT invoke sibling skills via slash commands — you ARE the pipeline.

**⚡ Schema-first — publish once, not thrice (legacy publish path).** Before you build a stage
artifact's `--data`, run `python backend/scripts/artifact_cli.py schema --stage <stage>` and fill
the payload FROM its template. It returns the `required` top-level fields (e.g. build's
`ac_coverage`, goal_cycle's `dod_met`+`adversarial_review`), the `depth` sub-fields those objects
must carry (deliver's `adversarial_review.{profile_tier,findings}`, review's dict-shaped
`litmus_gate`/`integration_trace`/`runtime_patterns`, build's `tdd.{green_pass,smoke_tests}`,
goal_cycle's `adversarial_review.{findings}`) and a ready `template`. Do NOT hand-assemble a
payload from memory and let the validator reject it — that is a send→reject→refix round-trip per
stage. On the new path `stage-start` returns valid examples and `stage-complete` validates before writing (no `--check` preflight).

## 3f. Handle Result
After verification:
- **All mechanical decisions → advance** to the next stage.
- **Taste decisions found → log them**, advance (listed in REPORT §10, never approved).
- **Judgment decision → ask the user** (`escalation.md`), then advance on the answer.

On the legacy path `advance` records the transition and prints `next_stage_doc` (absolute path)
and `next_action`; on the new path `stage-complete` returns `next_stage`, `next_card` and
`next_action`. Either way the card's **Continuation contract** applies: read the next card and
execute it in the same turn.
