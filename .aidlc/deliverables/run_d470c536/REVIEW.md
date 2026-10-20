# Review — gates and experts

**Run** `run_d470c536` · **profile** direct · **project** SwarmAI

> 起 direct run：砍 stage-start 的 git 调用

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 2 finding(s), 0 open | Red Team: no |
| Meta-review | CLEAR | 0 risk(s) |
| Push-ready | no | unverified: build: completed with 2 open gate error(s): SMOKE step skipped: build touched 2 code files but smoke_tests=0 — runtime crashes (AttributeError, NameError) may be hidden by mocks. Run smok… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 1 | 0 | 1 | 0 | 0 |
| security | 1 | 0 | 1 | 0 | 0 |

_Dispatch:_ Agent tool: 2 specialist sub-agents (EXPERT-ID: correctness, security) spawned in one parallel batch

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| correctness | MEDIUM | backend/core/run_worktree.py _symbolic_head / _worktree_list_key: in a reftable repo HEAD is a fixed stub, so the HEAD stat key misses a branch switch and the memo returns the old branch. Fixed: _files_refs(common) skips the HEAD memos when <gitdir>/reftable exists. | fixed · inv I1 · class fix: both HEAD-derived memos (symbolic-ref and worktree list) check _files_refs before building a key |
| security | MEDIUM | backend/core/run_worktree.py same reftable case, reachable from the long-lived backend through own_isolated_run -> is_registered. Fixed by the same _files_refs check. | fixed · inv I1 · class fix: same |

### Fixed by class

- **correctness · MEDIUM** — backend/core/run_worktree.py _symbolic_head / _worktree_list_key: in a reftable repo HEAD is a fixed stub, so the HEAD stat key misses a branch switch and the memo returns the old branch. Fixed: _files_refs(common) skips the HEAD memos when <gitdir>/reftable exists.
  - invariant: I1
  - root cause: the key named the file git reads HEAD from only for the files ref backend
  - class fix: both HEAD-derived memos (symbolic-ref and worktree list) check _files_refs before building a key
  - break test: backend/tests/test_run_worktree_memo.py::test_reftable_repo_follows_branch_switches
- **security · MEDIUM** — backend/core/run_worktree.py same reftable case, reachable from the long-lived backend through own_isolated_run -> is_registered. Fixed by the same _files_refs check.
  - invariant: I1
  - root cause: same as the correctness finding
  - class fix: same
  - break test: backend/tests/test_run_worktree_memo.py::test_reftable_repo_follows_branch_switches

## Open items

- [runner] Not tested in this run (verify): 566 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_active_run_commit_gate.py, backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py, bac…
- [degraded] deliver completed with 1 open gate error(s): Gate-2 findings: adversarial_review.findings[] must carry, one record each, every finding the experts reported (HIGH at conf >= 5, MED at conf >= 7) under its `
