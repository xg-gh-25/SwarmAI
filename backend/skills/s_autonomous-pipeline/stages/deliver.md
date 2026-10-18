# DELIVER — execution card
Prove the change push-ready (audits, Gate-2, meta-review, convergence), then commit it locally.
**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage deliver` → `experts` (+`prompt_path`), `source_insertions`, `full_tier_override`, `spawn_mode`, ACs, exit criteria, examples.
## Steps
1. **Audits** (`ref/deliver/audits.md`): Fresh User + User Path Latency if user-facing; Completion Audit + AC matrix.
2. **Stage first:** `git add` new files, unstage foreign ones, before any review (`ref/deliver/gate2-coverage.md`).
3. **Gate-2:** spawn EVERY `experts` entry (Agent tool, ONE message), 2-line prompt: `EXPERT-ID: <EXPERT_ID>` (registry id) + `Adversarially review this changeset: read <prompt_path> first; follow it.` Never paste it.
4. **Merge, Red Team, fix** (`ref/deliver/gate2-findings.md`): dedup, Shared-State Override before tiers, Red Team if source > 200 lines or any specialist HIGH (each finding's `specialist` = its EXPERT-ID; gated by the red-team marker), fix HIGH + MED conf ≥ 7, `pipeline_test.py --mode recheck`.
5. **Meta-review:** `python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py --gate meta-review --project <P> --files <changed files>`; stdout only on exit 0; one Agent call (`ref/deliver/push-ready.md`).
6. **User-Value Probe** (`ref/deliver/audits.md`) + **Doc Sync** (`ref/deliver/doc-sync.md`, warning only).
7. **Convergence, max 3 iterations**, all 6 at once: L1 tests pass, L2 type/lint clean, L3 no regressions, L4 adversarial clean (fixes on disk), L5 DDD conformance, L6 decisions resolved.
8. **Seed lessons** into the reflect record; write `outcome` = {`problem_before`, `result_now`, `approach[]`} (any other shape is rejected) + open items in `attention_flags`; REPORT.md is projected (`ref/deliver/report-and-commit.md`).
9. **Exit** (below), then `python backend/scripts/artifact_cli.py run-commit --project <P> --run-id <R>` (local, never pushes).

**Sub-agent configuration:** every spawn `run_in_background: false`; never pin a model; a failed spawn → go on with the rest (ALL rejected → `ref/deliver/gate2-dispatch.md`).

## Gates
- `[GATE·cli]` Adversarial review is NON-NEGOTIABLE: no "tests pass / simple code" exemption. <!-- R-deliver-001 -->
- `[GATE·cli]` Check 9 depth validation needs `adversarial_review.profile_tier == "full"` for full/bugfix; `run-update --status completed` BLOCKS on `skipped`/`lite`. <!-- R-deliver-002 R-deliver-005 -->
- Adversarial Review Gate (BLOCKING) — multi-specialist — `[GATE·cli]`: before the report and any commit. <!-- R-deliver-004 -->
- `[MUST]` BLOCKING: spawn ALL selected specialists in a SINGLE message (parallel Agent calls). The tier is `[GATE·cli]`; the batching is discipline. <!-- R-deliver-006 -->
- Meta-Review (BLOCKING) — `[GATE·validator]`: a full/bugfix deliver artifact without `meta_review`/`meta_review_verdict` is blocked; trivial/research/docs exempt. <!-- R-deliver-009 -->
- Push-Ready Gate (binary) — `[GATE·cli]`: `run-commit` requires `push_ready: true`. <!-- R-deliver-003 -->
- `[GATE·cli]` MANDATORY + gated: run-scoped source committed (commits ∩ files_touched) → `run-update --status completed` BLOCKS until the deliver record has `outputs_surfaced: true`; docs-only runs exempt. <!-- R-deliver-015 -->
- `[GATE·cli]` D7: `run-commit` refuses unless every `required_experts` entry reviewed this run.

## COMPLETE-stage Canvas review
At COMPLETE: `run-surface-changes`, then the `surface_run_outputs` tool ONCE with this run_id, then `run-update --stage-json '{"stage":"deliver","status":"completed","outputs_surfaced":true,...}'`.

## Read only if
- Dispatch rules, tiering, Red Team, rejected spawn → `ref/deliver/gate2-dispatch.md`
- Staging, or the commit gate DENIES after a review → `ref/deliver/gate2-coverage.md`
- Merging/gating/fixing findings → `ref/deliver/gate2-findings.md`
- Meta-review risks, convergence, push_ready capped, exit shape → `ref/deliver/push-ready.md`
- Lessons, REPORT.md, Canvas, `run-commit` refusal, boundary, PR → `ref/deliver/report-and-commit.md`

## Exit
`python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage deliver --data @/tmp/<R>/deliver.json` with `{"artifact": {...}, "record": {"decisions": [...]}}` (stage-start examples; `--check` preflights). `push_ready` = tool-computed upper bound; you may only LOWER it (`"push_ready": false` + `push_ready_reason`). Writes REPORT.md if missing. `exit_criteria_met: true` → stop verifying.
> **⏭️ Continuation contract — advance is not the end of your turn.** stage-complete advances to REFLECT (COMPLETE when the profile ends at deliver, e.g. `direct`). Run `run-commit`, read `next_card`, execute it **in this same turn**. Only legal hand-backs: a stage-boundary **L2 Judgment** via the Escalation Routing Protocol, a **true checkpoint trigger** (retries exhausted, mid-stage L2), a pending user question, or an abandon verdict. `next_action` is AUTHORITATIVE.
