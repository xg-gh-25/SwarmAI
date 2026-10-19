# DELIVER — execution card
Prove the change push-ready (audits, Gate-2, meta-review, convergence), then commit it locally.
**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage deliver` → `experts` (+`prompt_path`), `meta_review_prompt_path`, `spawn_mode`, ACs, exit criteria, examples.
## Steps
1. **Audits** (`ref/deliver/audits.md`): Fresh User + User Path Latency if user-facing; Completion Audit + AC matrix.
2. **Stage first:** `git add` new files, unstage foreign ones, before any review (`ref/deliver/gate2-coverage.md`).
3. **Gate-2:** spawn EVERY `experts` entry (Agent tool, ONE message), prompt = the entry's `spawn` text, exactly (a steered round does not count); never paste the prompt file.
4. **Ledger, spec, fix** (`ref/deliver/gate2-findings.md`): `gate2-ledger` builds findings from the experts' answers (`findings_from: "ledger"` + a `disposition` per id, never by hand); Shared-State Override first; Red Team: never its own wave; before ANY fix, `gate2-fixspec` groups the open items by cause and its skeptic must PASS `[GATE·cli]`; fix as the spec says (by class `[GATE·validator]`), `pipeline_test.py --mode recheck`, then `gate2-recheck` (spawn its `recheck`).
5. **Meta-review:** one Agent call, prompt = `meta_review_spawn` exactly (no path → `spawn_prompt.py --gate meta-review --project <P> --files <changed…>`, exit 0 only; `ref/deliver/push-ready.md`).
6. **User-Value Probe** (`ref/deliver/audits.md`) + **Doc Sync** (`ref/deliver/doc-sync.md`, warning only).
7. **Convergence, max 3 iterations**, all 6 at once: L1 tests pass, L2 type/lint clean, L3 no regressions, L4 adversarial clean (fixes on disk), L5 DDD conformance, L6 decisions resolved.
8. **Seed lessons** into the reflect record; write `outcome` = {`problem_before`, `result_now`, `approach[]`} (validated) + open items in `attention_flags`; REPORT.md is projected (`ref/deliver/report-and-commit.md`).
9. **Exit** (below), then `python backend/scripts/artifact_cli.py run-commit --project <P> --run-id <R>` (local, never pushes).

**Sub-agents:** `run_in_background: false`, never pin a model; a failed spawn → go on (ALL rejected → `ref/deliver/gate2-dispatch.md`).

## Gates
- `[GATE·cli]` Adversarial review is NON-NEGOTIABLE: no "tests pass / simple code" exemption. <!-- R-deliver-001 -->
- `[GATE·cli]` Check 9 depth validation needs `adversarial_review.profile_tier == "full"` for full/bugfix; `run-update --status completed` BLOCKS on `skipped`/`lite`. <!-- R-deliver-002 R-deliver-005 -->
- Adversarial Review Gate (BLOCKING) — multi-specialist — `[GATE·cli]`: before the report and any commit. <!-- R-deliver-004 -->
- `[GATE·hook]` every round (round 1, each `gate2-recheck` batch) in ONE message: a solo spawn after a sibling finished is denied once. <!-- R-deliver-006 -->
- Meta-Review (BLOCKING) — `[GATE·validator]`: a full/bugfix deliver artifact without `meta_review`/`meta_review_verdict` is blocked; trivial/research/docs exempt. <!-- R-deliver-009 -->
- Push-Ready (binary) — `[GATE·cli]`: false → `run-commit` still commits, marked; never push. <!-- R-deliver-003 -->
- `[GATE·cli]` Run source committed → `run-update --status completed` BLOCKS until surface evidence: the `surface_run_outputs` marker naming every commit, or `--outputs-surfaced` on that call; docs-only exempt; never re-record deliver for it (`complete.md` Step 0). <!-- R-deliver-015 -->
- `[GATE·cli]` D7: `run-commit` refuses unless every `required_experts` entry reviewed this run.

## Read only if
- Dispatch rules, tiering, Red Team, rejected spawn → `ref/deliver/gate2-dispatch.md`
- Staging, or the commit gate DENIES after a review → `ref/deliver/gate2-coverage.md`
- Merging/gating/fixing findings → `ref/deliver/gate2-findings.md`
- Meta-review risks, convergence, push_ready capped, exit shape → `ref/deliver/push-ready.md`
- Lessons, REPORT.md, Canvas, `run-commit` refusal, boundary, PR → `ref/deliver/report-and-commit.md`

## Exit
`python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage deliver --data @/tmp/<R>/deliver.json` with `{"artifact": {...}, "record": {"decisions": [...]}}` (stage-start examples; no `--check` first). `push_ready` = tool-computed bound; you may only LOWER it (`"push_ready": false` + `push_ready_reason`). Writes REPORT.md if missing. `exit_criteria_met: true` → stop verifying.
> **⏭️ Continuation contract — advance is not the end of your turn.** stage-complete advances to REFLECT (COMPLETE when the profile ends at deliver, e.g. `direct`). Run `run-commit`, read `next_card`, execute it **in this same turn**. Only Legal hand-backs only: a question you cannot answer yourself, a user stop, or a terminal verdict (`ref/instructions/escalation.md`). `next_action` is AUTHORITATIVE.
