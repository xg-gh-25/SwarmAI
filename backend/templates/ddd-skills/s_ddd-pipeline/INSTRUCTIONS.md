# s_ddd-pipeline — Full Stage Protocol (engine-driven, portable)

You drive the pipeline through the **bundled engine** at `engine/artifact_cli.py` — a
copied-and-decoupled build of SwarmAI's real engine, with NO `data.db` and NO dependency
on a SwarmAI `core` package (proven: all 12 engine modules import with `core/` absent).
It runs on ANY runtime (Kiro / Claude Code / AIM package / SwarmAI) — filesystem only.

## Locate the DDD + point the engine at it

The DDD is the directory containing `AGENTS.md` + `PRODUCT/TECH/IMPROVEMENT/PROJECT.md`.
The engine resolves its workspace from `$SWARM_WORKSPACE` (fallback: cwd). Set it to the
workspace root that holds `Projects/<ddd>/`. All state lives at
`<workspace>/Projects/<ddd>/.artifacts/runs/<run_id>/`.

```bash
ENG=<this-skill>/engine
export SWARM_WORKSPACE=<workspace-root>       # holds Projects/<ddd>/
py() { PYTHONPATH="$ENG" python "$ENG/artifact_cli.py" "$@"; }
```

## INIT

```bash
py run-create --project <ddd> --requirement "..." --profile full   # → run_<id>
```
Profiles: full / bugfix / trivial / goal / docs / research (immutable after EVALUATE).

## STAGE LOOP

For each stage in the profile, in order:
1. **Read** this DDD's stage-relevant docs (EVALUATE→all 4; BUILD→TECH+PROJECT; …).
2. **Execute** the stage behavior inline (you are the reasoning; the engine is the state
   machine + gates).
3. **Publish** the stage artifact + mark the stage complete via the engine:
   ```bash
   py run-update --project <ddd> --run-id <id> \
     --stage-json '{"stage":"build","status":"completed","stage_doc_consumed":true}'
   ```
   The engine enforces mechanical gates (e.g. `stage_doc_consumed:true` is REQUIRED — no
   bypass) and tracks token budget.
4. **Resume** = `py run-get --project <ddd> --run-id <id>` → skip `completed` stages,
   continue from the first non-completed one. `py run-status` = cross-run dashboard.

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

**养成 ladder (REFLECT).** Write the reflect lessons into the reflect stage record, then
let the engine's cultivation trio route them into THIS DDD's ② docs:
```bash
py run-update --project <ddd> --run-id <id> \
  --stage-json '{"stage":"reflect","status":"completed","stage_doc_consumed":true,"lessons":["..."]}'
py run-cultivate --project <ddd> --run-id <id>    # reads reflect lessons → quality-gate → route → write
```
`run-cultivate` runs the REAL cultivation engine (quality gate + dedup + section routing +
auto-heal of a missing allowlisted section) — not a naive append. A recurring pitfall
climbs: prose → rule → (3× recurrence) an executable ③ gate under `gates/` with a
knockout test (exit-2 = BLOCK).

## DELIVER boundary

Stop at PUSH-READY. Push / CR / deploy are user-initiated post-pipeline actions. Never
auto-push.

## Why engine-in-the-skill (not a hand-written shell)

The engine is COPIED, not re-authored — so the moat (real adversarial gate wiring, the
cultivation trio's quality-gate/dedup/routing, the mechanical stage gates) travels intact.
What was decoupled: the `data.db` Radar side-effect (dropped) and the hardcoded SwarmAI
workspace (now `$SWARM_WORKSPACE`). `run.json` + sibling artifacts ARE the state, so a
single DDD needs no cross-project SQLite index. The machine room was rebuilt inside the
skill; the brain + method + engine all travel to Kiro / Claude Code / an AIM package.

## Decouple invariants (do not regress)

- `grep -rE 'sqlite3|data\.db|\.execute\(' engine/*.py` → only docstring mentions (0 live).
- `grep -rEn '^\s*(from|import) (core|config|utils|jobs)\b' engine/*.py` → 0 top-level
  (off-path SwarmAI imports — ddd_health / ddd_entry_lifecycle / adversarial_meta — are
  try/except fail-soft ONLY, inside a function, never at module top).
- `grep -rn '\.swarm-ai' engine/*.py | grep -vE '^\S+:[0-9]+:\s*#|"""'` → 0 hardcoded
  paths in live code (workspace resolves from `$SWARM_WORKSPACE` / cwd; docstring/comment
  mentions are fine).
- All 12 `engine/*.py` import with `core/` off `sys.path` (the portability proof — run it).
