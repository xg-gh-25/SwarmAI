# Review — gates and experts

**Run** `run_772dd29a` · **profile** direct · **project** SwarmAI

> 产品级 你要改Repo 啊

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | PASS | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 6 finding(s), 0 open | Red Team: no |
| Meta-review | RISKS | 2 risk(s) |
| Push-ready | no | unverified: build: gate1 spawn NOT verified; accepted on the recorded gate1_unverified_reason: No gate1-skeptic was spawned for this plan: the plan is 'apply this reviewed text'; the text itself had … |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| adversarial | 6 | 3 | 6 | 0 | 0 |
| meta-review | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ Agent tool: 1 adversarial sub-agent on the exact patch before the run; this run's meta-review on the fixed text

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| adversarial | HIGH | backend/context/SELF.md My Gates: claimed the pipeline's findings become tests and only tests block, which is false for live Gate-2 (pipeline_validator._blocked_findings blocks on recorded HIGH/MED). Fixed: the sentence now lists the hooks' signals only, no pipeline claim. | fixed · inv I1 · class fix: both seeds state only the signal list; the design contract and its live-code pointer live in the DDD TECH.md |
| adversarial | HIGH | backend/context/SOUL.md: did not separate 'a review ran' (a fact a gate may require, adversarial_commit_gate) from 'what it said'; read literally it made R2 adversarial review optional. Fixed: the P5 text states the distinction outright. | fixed · inv I1 · class fix: one sentence in P5 covers every gate |
| adversarial | MEDIUM | backend/context/SOUL.md: the list of blocking signals omitted markers, path sets and human decisions and differed from SELF's list. Fixed: one list in both files. | fixed · inv I1 · class fix: identical list in SOUL P5 and SELF My Gates |
| adversarial | MEDIUM | backend/context/SOUL.md: a 16-line P7 corollary restated P5's kernel (Intake Gate new-axis test). Fixed: folded into P5 as one paragraph; D1-D6 detail moved to the DDD. | fixed |
| adversarial | LOW | backend/context/SOUL.md: the Tell called the live bounded Gate-2 loop a violation and clashed with P6 caps. Fixed: Tell targets an uncounted model loop; budget ends rounds not work (P6). | fixed |
| adversarial | LOW | backend/context/SELF.md: approval gates finally decide on a human answer. Fixed: 'a human's approval' added to the signal list. | fixed |

### Fixed by class

- **adversarial · HIGH** — backend/context/SELF.md My Gates: claimed the pipeline's findings become tests and only tests block, which is false for live Gate-2 (pipeline_validator._blocked_findings blocks on recorded HIGH/MED). Fixed: the sentence now lists the hooks' signals only, no pipeline claim.
  - invariant: I1
  - root cause: a design target was written as present behaviour
  - class fix: both seeds state only the signal list; the design contract and its live-code pointer live in the DDD TECH.md
  - break test: backend/tests/test_context_templates.py::TestTemplatesExist::test_template_is_readable_and_not_empty
- **adversarial · HIGH** — backend/context/SOUL.md: did not separate 'a review ran' (a fact a gate may require, adversarial_commit_gate) from 'what it said'; read literally it made R2 adversarial review optional. Fixed: the P5 text states the distinction outright.
  - invariant: I1
  - root cause: the principle named only the verdict side
  - class fix: one sentence in P5 covers every gate
  - break test: backend/tests/test_context_templates.py::TestTemplatesExist::test_template_is_readable_and_not_empty
- **adversarial · MEDIUM** — backend/context/SOUL.md: the list of blocking signals omitted markers, path sets and human decisions and differed from SELF's list. Fixed: one list in both files.
  - invariant: I1
  - root cause: two hand-written lists
  - class fix: identical list in SOUL P5 and SELF My Gates
  - break test: backend/tests/test_context_templates.py::TestTemplatesExist::test_template_is_readable_and_not_empty

## Open items

- 2 LOW wording follow-ups in SOUL P5 / SELF My Gates seeds
