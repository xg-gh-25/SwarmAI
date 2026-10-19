# Review — gates and experts

**Run** `run_f043bc90` · **profile** direct · **project** SwarmAI

> 你可以起direct mode 直接去修 ‘一行改动仍会把约 300 个集成测试选进最后那一轮。 现在只跑一次，不再反复跑；但"选得准"（设计文档里的 T2 分层）和"慢测试文件本身提速"（T1）还没做。 ⏎ code ⏎ test_pipeline_worktree.py 只从 106 秒降到 89 秒。 ⏎ 没跑到的测试仍然标成 timeout。 输出里有一句说明，续跑也保证会有进展了，但标签本身没改。这是小事。 ⏎ 测试门禁仍然拦 ps | grep pytest。 这是有意的设计：不让手动排查进程，排查交给 runner。我实测确认这条现在还拦。要不要放开只读的 ps，需要你定。 ⏎ plan 时少写字段导致的记账错误，现在仍会降低 push_ready。 只是卡片上改成了好读的文字，记账本身还在。’

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | skipped · 0 finding(s), 0 open | Red Team: no |
| Meta-review | RISKS | 1 risk(s) |
| Push-ready | no | exit criteria unmet: ['(no exit contract declared at PLAN)']; unverified: test: 178 continued test(s) passed without the earlier tests of their file; adversarial_review.profile_tier='skipped' but pro… |

## Open items

- [runner] Not tested in this run (verify): 523 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py, backend/tests/test_adversarial_meta.py, backend/t…
- [degraded] deliver completed with 5 open gate error(s): adversarial_review.profile_tier='skipped' but profile='direct' requires full adversarial review. Only trivial/research/docs exempt.; adversarial_review.spawned=False but profile='direct' requires sub-agent to be actually spawned (spawned=true). Cannot skip adversarial execution.; Gate-2 findings: adversarial_review.findings[] must carry, one record each, every finding the experts reported (HIGH at conf >= 5, MED at conf >= 7) under its ` (+2)
