# s_ddd-pipeline — Full Stage Protocol (file-state, portable)

You ARE the pipeline. Execute each stage inline. State is plain files under the DDD's
own `.artifacts/` — no `data.db`, no `artifact_cli.py`. Runs on ANY runtime (Kiro /
Claude Code / AIM package / SwarmAI) because it depends only on the filesystem + the
DDD's own docs.

## Locate the DDD

The DDD is the directory containing `AGENTS.md` + `PRODUCT/TECH/IMPROVEMENT/PROJECT.md`.
All state is relative to it: `<ddd>/.artifacts/runs/<run_id>/`.

## INIT

1. Parse requirement + pick profile (full / bugfix / trivial / goal / docs / research).
2. Create `<ddd>/.artifacts/runs/<run_id>/run.json`:
   ```json
   {"run_id":"run_<slug>","requirement":"...","profile":"full","stages":[]}
   ```

## STAGE LOOP

For each stage in the profile, in order:
1. **Read** this DDD's stage-relevant docs (EVALUATE→all 4; BUILD→TECH+PROJECT; …).
2. **Execute** the stage behavior inline.
3. **Write** the stage artifact as a sibling file (`evaluate.json`, `design_doc.json`,
   `changeset.json`, `review.json`, `test_report.json`, `delivery.json`).
4. **Append** `{"stage":"build","status":"completed","artifact":"changeset.json"}` to
   `run.json.stages`.
5. **Resume** = read `run.json`, skip `completed` stages, continue from the first
   non-completed one.

| Stage | Reads | Writes |
|-------|-------|--------|
| evaluate | 4 DDD docs | `evaluate.json` (scope, acceptance_criteria, understanding) |
| think | evaluate.json | `research.json` (alternatives, approach_chosen) |
| plan | evaluate+research | `design_doc.json` (change_spec, boundaries, test_strategy) |
| build | design_doc | `changeset.json` — TDD: RED→GREEN→VERIFY |
| review | changeset | `review.json` (findings) |
| test | changeset+review | `test_report.json` |
| adversarial | changeset | fills `run.json.adversarial_review` (THE MOAT) |
| deliver | all above | `delivery.json` (push-ready verdict) |
| reflect | test+delivery | sediment lessons to ② IMPROVEMENT.md via s_ddd-persist (养成 ladder) |

## THE MOAT (BLOCKING — never skip)

**Gate-2 Adversarial (before commit).** Spawn a fresh-context sub-agent with ZERO
builder context to REFUTE the changeset:
```json
"adversarial_review": {"spawned": true, "evidence": "<runtime sub-agent call>",
  "findings": [{"severity":"HIGH","resolved":true,"finding":"file:line — what. Fixed: how."}],
  "findings_remaining": 0}
```
Commit is FORBIDDEN while any HIGH/CRITICAL `findings_remaining > 0`. No sub-agent
primitive (Quick/ChatGPT) → surface an explicit adversarial-review step to the human.
Never silently skip.

**养成 ladder (REFLECT).** Write new pitfalls to ② `IMPROVEMENT.md`. A recurring pitfall
climbs: prose → rule → (3× recurrence) an executable ③ gate under `gates/` with a
knockout test (exit-2 = BLOCK).

## DELIVER boundary

Stop at PUSH-READY. Push / CR / deploy are user-initiated post-pipeline actions. Never
auto-push.

## Why file-state

A single DDD needs no cross-project SQLite index. `run.json` + sibling artifacts ARE the
state — that is what lets the pipeline ride to Kiro / Claude Code / an AIM package
unchanged. The machine room stayed behind in SwarmAI; the brain + method traveled.
