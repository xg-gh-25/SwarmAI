# Review — gates and experts

**Run** `run_303ec2d2` · **profile** direct · **project** SwarmAI

> direct run：context 改动固定派 security

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | skipped · 0 finding(s), 0 open | Red Team: no |
| Meta-review | CLEAR | 0 risk(s) |
| Push-ready | no | unverified: test: 186 continued test(s) passed without the earlier tests of their file |

## Open items

- [runner] Not tested in this run (verify): 518 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_adversarial_commit_gate.py, backend/tests/test_adversarial_meta.py, backen…
