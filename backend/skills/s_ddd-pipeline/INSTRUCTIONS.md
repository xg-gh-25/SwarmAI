# s_ddd-pipeline — Full Stage Protocol (file-state, portable)

You ARE the pipeline. Execute each stage inline. State is plain files under the
DDD's own `.artifacts/` — no `data.db`, no `artifact_cli.py`. This runs on ANY
runtime (Kiro / Claude Code / AIM package / SwarmAI) because it depends only on
the filesystem + the DDD's own docs.

## Locate the DDD

The DDD is the directory containing `AGENTS.md` + `PRODUCT/TECH/IMPROVEMENT/PROJECT.md`.
All state is relative to it: `<ddd>/.artifacts/runs/<run_id>/`.

## INIT

1. Parse requirement + pick profile (full / bugfix / trivial / goal / docs / research).
2. Create run dir: `<ddd>/.artifacts/runs/<run_id>/` and write `run.json`:
   ```json
   {"run_id":"run_<slug>","requirement":"...","profile":"full","stages":[]}
   ```
   `<slug>` = a short stable string derived from the requirement (do NOT use a
   clock/random — pass one in if you need determinism across resume).

## STAGE LOOP

For each stage in the profile, in order:

1. **Read** this DDD's stage-relevant docs (EVALUATE→all 4; BUILD→TECH+PROJECT; etc.).
2. **Execute** the stage behavior inline.
3. **Write** the stage artifact as a sibling file, e.g. `evaluate.json`, `design_doc.json`,
   `changeset.json`, `review.json`, `test_report.json`, `delivery.json`.
4. **Append** a stage record to `run.json.stages`:
   ```json
   {"stage":"build","status":"completed","artifact":"changeset.json"}
   ```
5. **Resume** = read `run.json`, skip `completed` stages, continue from the first
   non-completed one. A fresh agent on any runtime resumes with zero external state.

Stage inputs/outputs (same contract as s_autonomous-pipeline, file-backed):

| Stage | Reads | Writes |
|-------|-------|--------|
| evaluate | 4 DDD docs | `evaluate.json` (scope, recommendation, acceptance_criteria, understanding) |
| think | evaluate.json | `research.json` (alternatives, approach_chosen) |
| plan | evaluate+research | `design_doc.json` (change_spec, boundaries, test_strategy) |
| build | design_doc | `changeset.json` (files, tdd, commits) — TDD: RED→GREEN→VERIFY |
| review | changeset | `review.json` (findings) |
| test | changeset+review | `test_report.json` (pass/fail, regressions) |
| adversarial | changeset | fills `run.json.adversarial_review` (THE MOAT — below) |
| deliver | all above | `delivery.json` (push-ready verdict) |
| reflect | test+delivery | appends lessons to THIS DDD's ② IMPROVEMENT.md (养成 ladder) |

## THE MOAT (BLOCKING — never skip)

### Gate-2 Adversarial (before commit)
Spawn a fresh-context sub-agent (runtime's mechanism) with ZERO builder context to
REFUTE the changeset. Record:
```json
"adversarial_review": {
  "spawned": true,
  "evidence": "<how spawned — the runtime's sub-agent call>",
  "findings": [{"severity":"HIGH","resolved":true,"finding":"file:line — what. Fixed: how."}],
  "findings_remaining": 0
}
```
**Commit is FORBIDDEN while any HIGH/CRITICAL `findings_remaining > 0`.**
Runtime degradation (spec §1c): Kiro/Claude Code = real sub-agent; Quick/ChatGPT =
surface an explicit "adversarially review this diff before commit" step to the human.
Never silently skip — a silent skip is the empty-workflow failure mode.

### 养成 ladder (REFLECT)
Every REFLECT writes new pitfalls to THIS DDD's ② `IMPROVEMENT.md`. A pitfall that
recurs climbs: prose → rule → (3× recurrence) → an executable ③ gate under `gates/`
with a knockout test (exit-2 = BLOCK). This is what makes each run smarter than the
last — the self-養成 loop.

## DELIVER boundary

Stop at PUSH-READY. Push / CR creation / deploy are user-initiated post-pipeline
actions (mirrors s_autonomous-pipeline + STEERING #5). Never auto-push.

## Why file-state (the decoupling rationale)

A single DDD needs no cross-project SQLite index. `run.json` + sibling artifact files
ARE the state. This is what lets the pipeline ride to Kiro / Claude Code / an AIM
package unchanged — the machine room (`data.db` + `artifact_cli`) stayed behind in
SwarmAI; the brain + method traveled.
