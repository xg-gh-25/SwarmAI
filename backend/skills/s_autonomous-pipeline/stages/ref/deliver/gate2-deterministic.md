# DELIVER ref — Gate-2 in a deterministic run

Read when: `stage-start --stage deliver` returned `gate2_flow` (run.json `convergence:
deterministic`, set by `run-create --convergence deterministic`). It replaces deliver.md
steps 3, 4, 5 and 7 (4b, the one full-scope regression run, stays). A run without it is
on the legacy flow and keeps those steps.

Models find, tests decide. Experts report findings; a finding closes only when its break
test is proven: it FAILs by an assertion on the round's snapshot (2 of 2 runs) and PASSes
on the current code. Nothing a model says blocks landing. A finding that is not proven
lowers push_ready and is listed in REPORT.

## The loop
Run `gate2-next --project <P> --run-id <R>` (the command `gate2_flow.next` printed) and do
exactly what its `next.action` says, then run `gate2-next` again:

| `next.action` | Do |
|---|---|
| `spawn` | spawn every `next.spawns[].spawn` text exactly, all in ONE message (Agent tool, `run_in_background: false`). R1 reviews the full change; R2 reviews only the fix since R1. |
| `fix` | for each listed finding: write a break test and register it, `gate2-next … --test <id>=<nodeid>`, or declare it, `--declare <id>=untestable:<reason>`. Fix by cause: the spec covers sibling sites, and break tests cover the class. Run the affected tests with `--mode tdd --files …`, then declare the pass done: `gate2-next … --fixed <pass>`. A fix that leaves a plan-declared reader neither changed nor declared is refused here, before the re-check (`ref/plan/consumers.md`). |
| `prove` | run each printed `pipeline_test.py --mode prove` command, one per Bash call; `continue: true` → the same command again. |
| `done` | no more experts in this run. Go to deliver.md step 4b (the one full-scope regression run), then step 6 and on. |

There are at most two review rounds and two fix passes, and `gate2-next` counts them:
answers to a third round are ignored, and a `--fixed` before the round has answered is refused.

A re-entered DELIVER (`gate2_flow.snapshot_R1.already`) hands out no spawn text of its
own: `gate2-next` alone says who reviews. The reviewers are frozen at the first fix; an
expert the grown diff adds later (e.g. the Red Team) is not spawned and is named as a
push_ready reason. The flow needs the run's own worktree: a run that ends up on the shared
checkout is switched to the legacy flow at its first stage-start (run.json
`convergence_fallback` says why).

## What does not apply here
- `gate2-fixspec` and `gate2-recheck` refuse in this flow, and `gate2-ledger` is not used:
  the ledger is rebuilt by `gate2-next` from the experts' answers, your declared events and
  the prove evidence, never written by hand.
- The fixing-spec skeptic, the round-2 RESOLVED / NOT RESOLVED verdicts, the meta-review
  (step 5) and the convergence loop (step 7) do not run, and the DELIVER artifact needs no
  `meta_review` or `convergence` block.
- Do not write `adversarial_review.findings`: stage-complete fills them from the ledger,
  each with its state and break test (resolved only when proven). The fix-by-class fields
  and run-commit's marker re-review check do not apply: a fix is re-reviewed by R2.

## push_ready
The tool bound is lowered by every finding that is not proven (`invalid_test`,
`untestable`, `not_reproduced`, `open`), by an expert that gave no usable output, by
ignored events, and by the usual non-model gates (tests, completion audit, required
experts). Landing is decided by the test evidence alone (deliver.md step 4b).
