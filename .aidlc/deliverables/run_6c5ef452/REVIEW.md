# Review — gates and experts

**Run** `run_6c5ef452` · **profile** direct · **project** SwarmAI

> 我要疯了 你整个streaming response structure 1级 目录都不对 你找下我们的design, 对比看看 怎么上来就response 'Working Thinking'  是啥意思 都给我归到了PIPELINE是啥意思 我们还没run pipeline 呢  你能把我们的structure 搞对吗

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 5 finding(s), 4 open | Red Team: no |
| Meta-review | RISKS | 3 risk(s) |
| Push-ready | yes |  |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 4 | 1 | 1 | 0 | 3 |
| meta-review | 1 | 0 | 0 | 0 | 1 |
| security | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ Agent tool: correctness + security in one parallel batch (round 1), again on the fixed tree (round 2), then correctness-recheck + security + meta-review in one parallel batch (round 3)

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| correctness | MEDIUM | think → restate → ask → work hid the lead thought all stream and showed it above the restatement at the settle. Fixed: groupWork opts.top scans across the restatement and interrupts and merges the leading thoughts into the first work block, live and settled alike; test turnView.groupWork.test.ts 'think → restate → ask → work'. | fixed · inv I1 · class fix: turnView.ts::groupWork (opts.top): one scan decides where every leading thoughts-only block goes, the same for live and settled |
| correctness | MEDIUM | desktop/src/pages/chat/components/AssistantMessageView.tsx: a turn with no work (think, then answer) shows only the bee while streaming and adds one folded Thought line above the answer at the settle. Kept by design and documented in Surface 6 #7: the first text cannot be told apart from a restatement until a tool call comes, and keeping the restatement still is the common case. | OPEN |
| correctness | LOW | desktop/src/pages/chat/turnView.ts: a direct run's pipeline_summary card (verdict + documents) still renders after the result. Kept: it is the run's delivery record, not the PIPELINE checklist block; the needs-you attention card must keep rendering too. | OPEN |
| correctness | LOW | desktop/src/pages/chat/turnView.ts groupWork: a no-work turn that asks a question (think → ask → answer) shows its settled Thought line above the question, not just above the answer. Rare; left for a follow-up (place unjoined leads before the first item that is neither the restatement nor an interrupt). | OPEN |
| meta-review | MEDIUM | desktop/src/pages/chat/turnView.ts: the direct-run filter drops only pipeline_stage and pipeline_run; a direct run's pipeline_summary (and a DELIVER pipeline_attention) still render as cards after the result. Kept on purpose: the needs-you card must never be hidden and the summary is the run's delivery record, not the PIPELINE checklist block. Follow-up: state this in design-judgment.md Surface 6… | OPEN |

### Fixed by class

- **correctness · MEDIUM** — think → restate → ask → work hid the lead thought all stream and showed it above the restatement at the settle. Fixed: groupWork opts.top scans across the restatement and interrupts and merges the leading thoughts into the first work block, live and settled alike; test turnView.groupWork.test.ts 'think → restate → ask → work'.
  - invariant: I1
  - root cause: the merge was keyed on the restatement being directly followed by a work block, so any first-level item between them broke it while hideLead still hid the block
  - class fix: turnView.ts::groupWork (opts.top): one scan decides where every leading thoughts-only block goes, the same for live and settled
  - break test: desktop/src/pages/chat/turnView.groupWork.test.ts::think_restate_ask_work_the_thought_joins_the_work_after_the_question
