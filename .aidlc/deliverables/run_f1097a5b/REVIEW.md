# Review — gates and experts

**Run** `run_f1097a5b` · **profile** direct · **project** SwarmAI

> direct run：修 main 上 3 个红测试

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | skipped · 0 finding(s), 0 open | Red Team: no |
| Meta-review | CLEAR | 0 risk(s) |
| Push-ready | no | adversarial_review.profile_tier='skipped' but profile='direct' requires full adversarial review. Only trivial/research/docs exempt.; adversarial_review.spawned=False but profile='direct' requires sub… |

## Open items

- [degraded] deliver completed with 5 open gate error(s): adversarial_review.profile_tier='skipped' but profile='direct' requires full adversarial review. Only trivial/research/docs exempt.; adversarial_review.spawned=False but profile='direct' requires sub-agent to be actually spawned (spawned=true). Cannot skip adversarial execution.; Gate-2 findings: adversarial_review.findings[] must carry, one record each, every finding the experts reported (HIGH at conf >= 5, MED at conf >= 7) under its ` (+2)
