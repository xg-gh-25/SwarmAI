# REVIEW — execution card
Check the BUILD diff against the spec and the project's rules (conformance; bug-hunting is DELIVER's adversarial), fix what it finds, record it.
**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage review` → `changed_files` (status, `is_test`), `source_insertions`/`source_deletions` (non-test only), `full_tier_override`, `review_experts` (ids + reasons), `spawn_mode: foreground`, ACs, artifact example.
## Steps
1. **Litmus** (< 2 min, no tools, no sub-agent): HF1-HF4 + soft signals, diff vs ACs → PASS / BORDERLINE / FAIL. FAIL → back to BUILD, spawn nothing.
2. **Spec Compliance Gate (BLOCKING, in the SAME message as the fan-out) `[MUST]`**: a fresh sub-agent, `run_in_background: false`, prompt = its `review_experts` entry's `spawn` text (only the raw PLAN ACs + the diff + the knowledge block; add nothing). BLOCK → back to BUILD (the fan-out's findings go with it). WARNING → carry its EXTRA findings to step 5. No profile skips it. <!-- R-review-002 -->
## Parallel Fan-Out Review
3. **Spawn exactly `review_experts`** (fan-out rule applied: >3 non-test files, >100 source lines, or an auth/data/infra/config path; security only when Gate-2 will not own it). Issue ALL of them, spec-compliance included, as Agent calls in ONE message (`[GATE·hook]`: a solo spawn after a sibling finished is denied once), each its entry's `spawn` text (`EXPERT-ID: <id>` + read its file; checklist, ACs, diff, knowledge already in it). No `prompt_path` → compose it by hand (`ref/review/dispatch.md`).
4. **Inline checks, always, main context.** **BLOCKING: apply REVIEW_PATTERNS.md RP1-RP91** via `python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py --patterns-for <files…> [--diff-file D]`: each listed row gets pass / N/A / fail; trigger is present → `--patterns-rows RP<n>` first; whole file only if it fails. <!-- R-review-007 --> Then Tier 1 (0 code-intel if DB, 1 TECH.md, 2 security scan, 3 integration trace, 17 blocking constraints) + each triggered Tier 2 check.
### Phase B — merge
5. Dedup/cross-reference quality findings only; the spec verdict is a separate axis, never merged. Run check 14 (anti-rationalization) and check 15 (exit evidence) → GO or BLOCK.
6. **Fix loop:** fix now or escalate, never defer; fix **by class** `[GATE·validator]` (blocking finding → `invariant`, `root_cause`, `class_fix`, `break_test`; `ref/plan/design-spec.md`). Auto-fix security ≥ 8 Critical/High, every UX finding, every blast-radius ❌, missing inverses, RP / blocking-constraint violations. **Re-review:** after a fix, re-run `--patterns-for`/`--patterns-rows` on the fixed files and redo affected checks; after a return to BUILD, re-enter at step 1 with `stage-start` (counters persist).
## Gates
- `[GATE·validator]` artifact needs `approved`, `litmus_gate` (Check 8e: literal verdict, 4 `hf_checked` booleans, evidence > 20 chars), `integration_trace`, `runtime_patterns`, `findings_count`.
- `[MUST]` spec gate is discipline, not validator-checked; record `spawned_as_subagent: true` (inline self-assessment does not count).
- `[MUST]` checks 14 + 15 before exit. `[GATE·artifact_cli]` `cross_boundary.value == true` without a passing Layer-4 E2E → DELIVER exits 1.
- BLOCKING: check 7b whenever operational code changed.
- Retries (REVIEW budget): 2 litmus FAILs; the 3rd → full review with ALL agents. 2 spec BLOCKs → mandatory BUILD rework. Counters `stages.review.litmus_fail_count` / `spec_fail_count` never reset.
## Read only if
- Litmus criteria / BORDERLINE / FAIL → `stages/ref/review/litmus.md`
- Spec process + verdicts → `stages/ref/review/spec-compliance.md`
- More than spec-compliance listed, merging, or finding format → `stages/ref/review/dispatch.md`
- Check 0-4 or 6 → `stages/ref/review/checks-core.md`
- Frontend (5), frontend + backend (7), operational code (7b), path/env/fs/process (16) → `stages/ref/review/checks-surface.md`
- New files (8), infra/release/CI (9, 10), state transitions (11), sibling files (12), modified functions (13), TECH.md constraints (17) → `stages/ref/review/checks-system.md`
- Steps 5-6, check 14/15 wording, artifact shape → `stages/ref/review/exit.md`
## Exit
`python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage review --data @/tmp/<R>/review.json` with `{"artifact": {<review>}, "record": {"decisions": [...]}}` (shape: stage-start example; no `--check` first).
> **⏭️ Continuation contract — advance is not the end of your turn.** stage-complete advances to test; read `next_card` and run it **in this same turn**. Only Legal hand-backs only: a question you cannot answer yourself, a user stop, or a terminal verdict (`ref/instructions/escalation.md`). `next_action` is AUTHORITATIVE over this paragraph.
