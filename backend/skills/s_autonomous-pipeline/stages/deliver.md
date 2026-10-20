# DELIVER — execution card
Prove the change push-ready (audits, Gate-2, meta-review, convergence), then commit it locally.
**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage deliver` → `experts` (+`prompt_path`), `meta_review_prompt_path`, `spawn_mode`, ACs, exit criteria, examples.
## Steps
1. **Audits** (`ref/deliver/audits.md`): Fresh User + User Path Latency if user-facing; Completion Audit, AC matrix.
2. **Stage first:** `git add` new files, unstage foreign ones, before review (`ref/deliver/gate2-coverage.md`).
3. **Gate-2:** spawn EVERY `experts` entry that has a `spawn` (Agent tool, ONE message), prompt = the `spawn` text, exactly. Re-entry: a reviewer gets a delta prompt or none.
4. **Ledger, spec, fix** (`ref/deliver/gate2-findings.md`): `gate2-ledger` builds findings from the experts' answers (`findings_from: "ledger"` + a `disposition` per id, never by hand); Shared-State Override first; Red Team: never its own wave; before ANY fix, `gate2-fixspec` groups the open items by cause and its skeptic must PASS `[GATE·cli]`; fix as the spec says (by class `[GATE·validator]`), run the tests it affects (`--mode tdd --files` / `prove`), then `gate2-recheck` (spawn its `recheck`).
4b. **The ONE full-scope run** after the last fix: `pipeline_test.py --mode regression` (`continue: true` → same command). Red → one fix spec (`ref/test/fixing.md`), fix, tdd, `--mode recheck` (fixing.md step 4). Landing reads only it.
5. **Meta-review:** one Agent call, prompt = `meta_review_spawn` exactly (no path → `spawn_prompt.py --gate meta-review --project <P> --files <changed…>`, exit 0 only; `ref/deliver/push-ready.md`).
6. **User-Value Probe** (`ref/deliver/audits.md`) + **Doc Sync** (`ref/deliver/doc-sync.md`, warns).
7. **Convergence, max 3 iterations**, all 6 at once: L1 tests pass, L2 type/lint clean, L3 no regressions, L4 adversarial clean (fixes on disk), L5 DDD conformance, L6 decisions resolved.
8. **Seed lessons** into the reflect record; write `outcome` = {`problem_before`, `result_now`, `approach[]`} (validated) + open items in `attention_flags`; REPORT.md is projected (`ref/deliver/report-and-commit.md`).
9. **Exit** (below), then `python backend/scripts/artifact_cli.py run-commit --project <P> --run-id <R>` (local, never pushes).

**Sub-agent configuration:** `run_in_background: false`, never pin a model; failed spawn → go on (all rejected → `ref/deliver/gate2-dispatch.md`).

## Gates
- `[GATE·cli]` Adversarial review is NON-NEGOTIABLE: no "tests pass / simple" exemption. <!-- R-deliver-001 -->
- `[GATE·cli]` Check 9 depth validation needs `adversarial_review.profile_tier == "full"` for full/bugfix; `run-update --status completed` BLOCKS on `skipped`/`lite`. <!-- R-deliver-002 R-deliver-005 -->
- Adversarial Review Gate (BLOCKING), multi-specialist `[GATE·cli]`: before report + commit. <!-- R-deliver-004 -->
- `[GATE·hook]` every round (round 1, each `gate2-recheck` batch) in ONE message: a solo spawn after a sibling finished is denied once. <!-- R-deliver-006 -->
- Meta-Review (BLOCKING) `[GATE·validator]`: full/bugfix deliver needs `meta_review`/`meta_review_verdict`; trivial/research exempt. <!-- R-deliver-009 -->
- Push-Ready (binary) — `[GATE·cli]`: false → `run-commit` still commits, marked; never push. <!-- R-deliver-003 -->
- `[GATE·cli]` Run source committed → `run-update --status completed` BLOCKS until surface evidence: the `surface_run_outputs` marker naming every commit, or `--outputs-surfaced` on that call; docs-only exempt; never re-record deliver for it (`complete.md` Step 0). <!-- R-deliver-015 -->
- `[GATE·cli]` D7: a `required_experts` entry that did not review sets push_ready false; never blocks landing.

## Read only if
- Dispatch rules, tiering, Red Team, rejected spawn → `ref/deliver/gate2-dispatch.md`
- Staging, or the commit gate DENIES → `ref/deliver/gate2-coverage.md`
- Merging/gating/fixing findings → `ref/deliver/gate2-findings.md`
- Meta-review risks, convergence, push_ready capped, exit shape → `ref/deliver/push-ready.md`
- Lessons, REPORT.md, Canvas, `run-commit` refusal, boundary, PR → `ref/deliver/report-and-commit.md`

## Exit
`python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage deliver --data @/tmp/<R>/deliver.json` with `{"artifact": {...}, "record": {"decisions": [...]}}` (stage-start examples; no `--check` first). `push_ready` = tool bound; only LOWER it (`"push_ready": false` + `push_ready_reason`). Writes REPORT.md if missing.
> **⏭️ Continuation contract — advance is not the end of your turn.** stage-complete advances to REFLECT (COMPLETE when the profile ends at deliver, e.g. `direct`). Run `run-commit`, read `next_card`, execute it **in this same turn**. Legal hand-backs only: an L2 or unclear-requirement question (ask, or checkpoint), a user stop, or a terminal verdict (`ref/instructions/escalation.md`). `next_action` is AUTHORITATIVE.
