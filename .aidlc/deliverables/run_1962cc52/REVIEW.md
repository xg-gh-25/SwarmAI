# Review — gates and experts

**Run** `run_1962cc52` · **profile** direct · **project** SwarmAI

> 直接修 修 14 条日期标题反复判定 ⏎ 看 KNOWLEDGE 和 principle 的 diff

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 0 finding(s), 0 open | Red Team: no |
| Meta-review | — | 0 risk(s) |
| Push-ready | no | unverified: build: completed with 10 open gate error(s): Consumers gate: K1: backend/core/ddd_entry_lifecycle.py::<module> matches 'inject_entry_metadata\\\\(' but the change does not touch it and no r… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ gate2-next ledger: 0 finding(s), 0 proven, 1 expert spawn(s)

## Gate 2 — deterministic ledger

0 finding(s): none · 1 expert spawn(s) · next: done

### Notes (do not block)

- LOW correctness: Entries are matched by (section, exact bullet line) and taken from a queue in document order. This assumes the caller passes EVERY parsed entry for identical lines. The decay pass does not: ddd_orchestrator.py:968 drops every entry whose st

## Open items

- Pre-existing red on base: test_silent_except_baseline (2), test_lint_skills_body (plan.md over 5,000 chars)
- Known issue LOW: identical-bullet twin with a superseded archived first copy
- [runner] Not tested in this run (regression): 572 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_active_run_commit_gate.py, backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py,…
