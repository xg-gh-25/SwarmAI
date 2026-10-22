---
title: Pipeline v1 cutover E7 review
date: 2026-10-22
created: 2026-10-22
updated: 2026-10-22
project: SwarmAI
status: PRE-CUTOVER PASS
related: pipeline-v1-plan.md; pipeline-v1-deviations.md; pipeline-structural-decisions v13 FINAL
---
# Pipeline v1 cutover E7 review

| Dimension | Baseline | Change | Evidence | Verdict | Handling |
|---|---|---|---|---|---|
| Run Isolation | Worktree default but creation could fall back shared; global marker scan | Per-run audit/event/blob/contract; no fallback; tree/auth/cache bound by run+tree; permanent snapshots | `test_pipeline_v1_isolation_e2e.py`, `test_run_protocol_store.py`, `test_run_protocol_cache.py`, `test_run_protocol_carry.py`, profile×mode E2E | PASS | Contamination is a hard BLOCK in `run_audit.json` |
| Quality | Latest-* reports, self-declared fields, model-closed findings | Base-pinned validations, complete code/delivery trees, sealed final, evidence-typed finding closure, deterministic risk expansion | direct real landing E2E; direct×2 + all profiles rehearsal; 334 Cycle-1 invariants; final/runner/risk/proof suites | PASS | Build/deploy/eval remain required outside the run as designed |
| Autonomy | Mechanical retry failures could pause for a person; no legal run-land | Bounded terminal paths; only DC-1 asks; normal and authorized landing; safe not-landed completion | lifecycle exhaustive tests, decision/auth/landing tests, all profile terminal tests | PASS | Production/destructive approvals remain human boundaries |
| Speed | Historical review/marker scans and repeated full-scope work | Per-run small files, deterministic checks first, R1/R2 only, immutable cache, dependency-scoped carry | cache/carry tests and finite lifecycle budgets | PASS | Runtime p50/p95 comparison begins after 10 production v1 runs |
| Token Cost | 27 average expert dispatches/run; incomplete expert attribution | Lightweight roster, no secondary/root review, exact answer reuse, SubagentStop token metrics including cache tokens | review roster/cache tests, `usage_for_subagent`, audit accounting tests | PASS | p95 alert remains a warning, never a work-stopping budget |

Overall verdict: **PASS for cutover readiness**. Isolation and Quality have no accepted degradation. Speed and Token post-launch deltas remain an observation obligation after 10 real v1 runs, not a cutover blocker. Production build/restart and direct smoke are Cycle 10 actions requiring XG approval.

## Final review (2026-10-22)

Three fresh-context reviews (authority/isolation, landing/provenance, cutover) returned BLOCK. All root causes were fixed before the ready commit: executable Gate-0/R2/proof/recovery/decision/run-land/accept-finding/discard-snapshot paths; SubagentStop answers bound by run + handout + tool_use_id + full prompt digest + nonce; runner-executed, runner-signed calls and old/old/new proofs; immutable cache with permanent conflict poison and cross-run integrity events; per-repo persisted landing progress with full commit map; commit REPORT cut off before `land_intent`; v1 owner/takeover, HITL one-use tokens, stage watcher, Cancel/Resume API and UI.

Evidence: split targeted backend suites (~880 tests), frontend 22 tests + tsc + production web build, 358-module import smoke, skill/expert/problem-value lints, eval freshness gate PASS. `security_scan` still blocks on the inherited main baseline; versus main it adds no new finding class (only line-number shifts in existing entries, one legacy B324 removed with `gate2_findings.py`).

Known residual: `authority.key` remains a file-backed trust root readable by trusted CLI processes; agent tool access is denied by gates (glob, variable, cwd, recursive and interpreter-script forms), matching the v13 threat table, which does not defend against a modified trusted writer.

## Pre-merge recheck (2026-10-22, second round)

A fresh cutover review (BLOCK) plus a 215-file impact sweep (every test importing a changed module, ~6000 tests, plus all source-scan/context suites) found seam bugs the v1-only batches had missed. All fixed in this commit:

- Liveness: `run_index.LIVE_STATUSES`, `run_worktree._LIVE` and `security_hooks._ACTIVE_RUN_STATUSES` knew only `running`/`paused`, so the hourly sweeper released an ACTIVE v1 run's worktree and the worktree write gate saw no owning run. Now every non-terminal v1 status is live; `stage_start` rewrites `run.json` as soon as worktrees exist (`test_pipeline_v1_live_gates`, `test_main_dirty_scan_v1`; both fail on the old sets).
- Landing trailer: v1 landing now always adds `Co-Authored-By: Swarm` (legacy `run-commit` did).
- Dashboard: Cancel/Resume act only on v1 runs; a historical run returns 410 and is never mutated.
- Every gate deny / hint (worktree write, main-dirty reporter, direct-commit, amend) now names runnable `pipeline_v1.py` commands; tests parse the printed command with the real CLI parser. Pipeline v1 has no claim / repo-add command, so main-checkout edits are re-applied in the worktree.
- `pipeline_test.py` printed output keeps its 3 KB budget (v1 selection lists are compacted only in the printout).
- REVIEW docs give the complete `prove` / `review-proof` forms and the exact proof envelope.
- `run_docs.required_docs` owes REVIEW.md only to profiles that run REVIEW.
- Async route no longer reads files on the event loop; two new broad excepts now log.
- Legacy suites: only failing tests for retired flows were removed (whole files only when every test drove retired commands); outdated expectations were updated to v13 values.

Inherited from main, not changed here: `test_silent_except_baseline` exceeds on `core/kill_audit.py` and `core/lifecycle_manager.py` (files untouched by this branch); `security_scan` baseline BLOCK.
