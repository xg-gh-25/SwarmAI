# INSTRUCTIONS ref — INIT, resume, profiles, goal orchestration
Read when: starting or resuming a run (Step 1 INIT), choosing or explaining a profile (Step 2
PROFILE), or running the goal profile / scheduled goal mode. The profile table itself is on the
card; the authoritative stage lists are `backend/core/pipeline_profiles.py`.

## Step 1: INIT — starting a new pipeline
Parse the user's message for:
- **Requirement:** one sentence to one paragraph describing what to build.
- **Project:** detect from context (file paths, explicit mention, chat binding). None detected →
  confirm with the user; the pipeline needs a project for artifact storage (L1+).

Check current state and paused pipelines first:
```bash
python backend/scripts/artifact_cli.py state --project <PROJECT>
python backend/scripts/artifact_cli.py discover --project <PROJECT> --types checkpoint --full
```
A paused pipeline exists for this project → ask "Resume the existing pipeline or start a new one?"

**Use `run-create`** (NEVER write run.json by hand):
```bash
python backend/scripts/artifact_cli.py run-create --project <PROJECT> --requirement "<parsed requirement>" [--profile direct]
```
It creates `Projects/<project>/.artifacts/runs/<run_id>/run.json` with defaults, sets
status=running, auto-abandons stale same-project runs (>2h) and returns the run_id. The profile is
set after EVALUATE classifies scope (except `direct`, chosen at creation — it has no EVALUATE).

**Briefing (emit at INIT — orient the user before any work runs).** The profile is unknown until
EVALUATE returns, so emit the run header now and the profile/mode line the moment EVALUATE
returns. Say *what is about to run and why*, not just that it started:
```
🚀 Pipeline started: <requirement> (run_<id>) · Project: <PROJECT>
   Architecture: 3 gates · 9 stages · 2 execution modes
   • Gate 0 (EVALUATE→THINK) — is the problem/framing understood? (diagnose-before-build family)
   • Gate 1 (after PLAN)      — is the plan sound, root not symptom? (Skeptic + SSA)
   • Gate 2 (in DELIVER)      — is the build actually correct? (Adversarial sub-agent)
   Profile: <set after EVALUATE> · Mode: <full = one-shot | goal = iterative loop>
```
After EVALUATE selects the profile, state it with the reason:
```
   Profile: <profile> (scope=<scope>) — <one-line why, e.g. "standard feature → full">
   Mode: <full: BUILD→REVIEW→TEST once | goal: goal_cycle iterates to a measurable DoD>
   Stages this run: <the profile's stage list>
```

**Profile vs mode (don't conflate):** there are **7 profiles** (full, trivial, research, docs,
bugfix, goal, direct — the *stage set* + rigor) and **2 execution modes** (full = one-shot,
goal = iterative; how BUILD/REVIEW/TEST run). Every profile passes through the gates that apply to
its stages.

## Resuming a pipeline
When the user says "resume pipeline" or drags a pipeline Radar todo:
1. Read the checkpoint artifact: `discover --types checkpoint --full`.
2. Load `runs/<id>/run.json` via `run-get`.
3. Check pending escalations — any still open → report and wait.
4. Skip completed stages; resume from the checkpoint stage (`stage-start` for it).
5. Announce:
```
Pipeline RESUMED: <requirement> (run_<id>)
Completed: evaluate, think, plan
Resuming from: build
```

## Step 2: PROFILE — why ≤8 stage entries, not 9
<!-- R-instr-001 -->
The canonical architecture is **9 stages across 3 gates** (EVALUATE → THINK → PLAN → BUILD →
REVIEW → TEST → ADVERSARIAL → DELIVER → REFLECT). Two of those are not separate orchestration rows:
- **ADVERSARIAL is architecturally stage 7 but executes inside DELIVER** (spawn a fresh-context
  sub-agent), **a mandatory blocking gate** — so it never appears as its own row in a profile's
  stage list. See `stages/deliver.md` (Gate-2 adversarial review, BLOCKING). This is why `full`
  lists 8 entries while docs say "9 stages".
- **Gate 0** — the diagnose-before-build family (Understanding + Ambiguity scan + greenfield
  Working-Backwards) — is not a stage; it fires *inside EVALUATE*, at the EVALUATE→THINK
  boundary. Code-enforced by `pipeline_validator.py` at publish time. See TECH.md
  "diagnose-before-build gate family" + `stages/evaluate.md`.

`COMPLETE` (Step 6, `stages/complete.md`) is the terminal *output-format* step, not a quality
stage, so it is not counted in the 9. A profile's list shows the orchestration steps (≤8); the 3
gates (Gate 0 in EVALUATE, Gate 1 after PLAN, Gate 2 in DELIVER) ride within them. `★ Gate 1` /
`★ Gate 2` are wired into the ④/⑧ landmarks, the golden-set trajectory case and tests — do not
renumber them; `★ Gate 0` is the EVALUATE-stage landmark.

Scope → profile mapping (EVALUATE's classification): standard/complex → full; trivial → trivial;
research-only → research; docs-only → docs; bugfix → bugfix; goal → goal. `direct`
(plan → build → deliver) is not chosen by EVALUATE: it is created up front for a direct fix (the
`direct_commit_gate` sends a no-run session committing >20 source lines to
`run-create --profile direct`). PLAN carries the exit contract; DELIVER is non-skippable; strict.

## Profile Selection Principle
**Full vs Goal** — both have all stages; only the execution mode differs:
- **Full:** BUILD once → REVIEW once → TEST once → DELIVER. One-shot.
- **Goal:** goal_cycle loops BUILD+TEST per cycle, periodic REVIEW, final adversarial inside
  goal_cycle, then exits to DELIVER → REFLECT.

**Use Goal when the work is ITERATION toward a moving target, not mere verifiability:**
- a **metric/threshold** to reach (coverage ≥90%, latency <X), OR a **bulk sweep** (fix all N
  warnings, migrate all call sites), OR **convergence** where you can't predict how many changes.
- "A command exits 0 when done" is NOT a goal signal by itself — almost every change can be
  wrapped in an exit-0 check. The question is whether you must ITERATE, not whether the result is
  checkable.

**Use Full (the default for a bounded change)** when "done" is the artifact existing and passing
review (feature implemented, committed, tests pass) and the work is bounded (you know the
approach, the files, the shape of done). ONE known change, even if a test verifies it, is full
(or bugfix/trivial if it matched those size gates).

**Prefer the lightest profile that fits** (docs/trivial/bugfix before full before goal). The
decision tree in `stages/evaluate.md` evaluates the size/bug gates FIRST and goal LAST on purpose.
EVALUATE classifies no scope → default **full**. The user can override ("skip research, I know the
approach" → bugfix) or force ("use goal", "use full pipeline").

## Goal Profile Orchestration
Goal has the same stages as full; goal_cycle replaces BUILD+REVIEW+TEST and loops internally:
1. **EVALUATE** — standard + goal_mode detection (DoD criteria, max_cycles).
2. **THINK** — standard (alternatives, risk probes).
3. **PLAN** — standard (approach for reaching the goal).
4. **GOAL_CYCLE** — each cycle: DoD check → pick step → BUILD+TEST → progress → mini-reflect
   (budget is NOT a cycle gate — STEERING #2); periodic REVIEW every N cycles on the accumulated
   diff; final ADVERSARIAL on the total changeset when the DoD is met. See `stages/goal_cycle.md`.
5. **DELIVER** — standard. 6. **REFLECT** — standard (reads mini-reflects from the progress file).

Quality assurance in goal_cycle (replaces full's single-shot gates): per-cycle TEST (immediate
regression check), periodic REVIEW (convention/pattern compliance on the accumulated diff), final
ADVERSARIAL (fresh-eyes attack on the total changeset, code-enforced gate).

**Scheduled mode** (goals spanning multiple sessions): after EVALUATE+PLAN, if the user asks for
overnight/unattended execution or EVALUATE estimates >10 cycles, offer scheduled mode:
```yaml
# Job template for scheduled goal execution
jobs:
  - id: goal-<slug>
    name: "<goal description>"
    type: agent_task
    schedule: "0 */4 * * *"
    enabled: true
    config:
      prompt: |
        Resume goal loop for <PROJECT>.
        Progress: <progress_path>
        1. Read progress → 2. Check DoD (if met: disable job, notify)
        3. Execute ONE cycle → 4. Save progress → exit
    safety:
      max_budget_usd: 1.50
      timeout_seconds: 600
```
