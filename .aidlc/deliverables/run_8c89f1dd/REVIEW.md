# Review — gates and experts

**Run** `run_8c89f1dd` · **profile** direct · **project** SwarmAI

> 你看看 是不是问题 如果是就direct-run 修掉 ‘改受控的 context 文件可能完全没人审（安全相关）。backend/context/ 下的 ⏎ description ⏎ SOUL.md、 ⏎ description ⏎ STEERING.md、 ⏎ description ⏎ AGENT.md、 ⏎ description ⏎ SECURITY-BASELINE.md 都是 .md，会被判成纯文档改动，于是不派专家，也不派 meta-review，测试一过就算 push-ready。这几个文件会进入以后每一个 session 的 system prompt。所以如果有一段注入内容诱导我"把这条规则记下来"，改动就会在没有任何人看过的情况下落地。这是我照着代码读出来的推断，还没有实测验证。 ⏎ deliver 开始之后改动又变大了，审查要求却不会跟着恢复。 现有的过滤只看…

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | skipped · 0 finding(s), 0 open | Red Team: no |
| Meta-review | CLEAR | 0 risk(s) |
| Push-ready | no | unverified: test: 95 continued test(s) passed without the earlier tests of their file |

## Open items

- [runner] Not tested in this run (verify): 549 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_active_run_commit_gate.py, backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py, bac…
