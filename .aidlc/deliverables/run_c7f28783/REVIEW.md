# Review — gates and experts

**Run** `run_c7f28783` · **profile** direct · **project** SwarmAI

> 你为什么又停了

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 1 finding(s), 1 open | Red Team: no |
| Meta-review | — | 0 risk(s) |
| Push-ready | no | exit criteria unmet: ['X1']; gate2: security-R1-nooutput open: expert security gave no usable output; Depth: 1/1 adversarial findings are vague (no file path, function name, or line reference). Rule … |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| security | 1 | 0 | 0 | 0 | 1 |

_Dispatch:_ gate2-next ledger: 1 finding(s), 0 proven, 2 expert spawn(s)

## Gate 2 — deterministic ledger

1 finding(s): 1 open · 2 expert spawn(s) · next: done

| Finding | Round | Expert | State | Break test | Claim |
|---|---|---|---|---|---|
| security-R1-nooutput | R1 | security | open | — | expert security gave no usable output |

### Lowers push_ready

- gate2: security-R1-nooutput open: expert security gave no usable output

## Open items

- Gate-2 parser did not accept a security answer that starts with NO FINDINGS (it wants it as the last line): push_ready lowered for a review that did run
- Takes effect after rebuild (system-owned context file)
- [degraded] deliver completed with 1 open gate error(s): Depth: 1/1 adversarial findings are vague (no file path, function name, or line reference). Rule 18: findings must include file, what's wrong, and concrete fix.
