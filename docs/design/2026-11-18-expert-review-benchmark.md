---
title: Review expert benchmark
date: 2026-11-18
created: 2026-11-18
updated: 2026-11-18
project: SwarmAI
status: DRAFT — for discussion; nothing built
related: skills s_expert-review (how experts are called); scripts/expert_verdicts.py (native verdict records); core/run_protocol/findings.py (pipeline finding states)
---

# Review expert benchmark

## 1. Problem

When an expert prompt, the model the experts run on, or the way a review is dispatched
changes, nothing says whether reviews got better or worse. The labels we keep today
cannot answer it, because they come from inside the review loop:

- Native reviews (`scripts.expert_verdicts stats`, measured 2026-11-18): 4 reviews,
  24 findings, 23 judged real, 0 false positives. The verdict is given by the agent that
  wrote the change and wants to commit it, so a 100% real share is a claim, not a
  measurement.
- Pipeline runs (the 27 runs still in `state/pipeline_agent_audit/runs`): 169 finding
  states, of which ADEQUATE 85, RESOLVED 60, OPEN 15, TEST_INADEQUATE 9, REFUTED 0.
  Closure records that a finding was fixed and proven, not that it was a real defect:
  an agent fixes most of what it is told.
- Neither source can count misses: a defect no expert raised leaves no record.

So precision is unmeasured and recall is unknown. A benchmark needs ground truth made
outside the loop.

## 2. What it is and is not

- **Is:** a replay of the experts, exactly as `s_expert-review` and the pipeline call
  them, on cases with known defects, scored the same way every time. It answers one
  question per run: did this prompt / model / dispatch change make the expert find more
  real defects, raise fewer false ones, or cost less?
- **Is not a gate.** Its numbers never block a commit, a pipeline stage or a landing
  (SOUL P5: reviewers find, they do not decide). It is not OS Eval (that scores the
  deployed system) and it never runs inside a pipeline run. A person or the agent working
  on an expert runs it on demand.
- **Not a bake-off.** Comparing with another reviewer (Claude Code's own review, Alibaba's
  open-code-review) is context for reading our numbers, never the goal.

## 3. Cases

One case shape for every source:

```json
{"id": "...", "source": "seeded|aacr|regression", "repo": "<url or local path>",
 "base": "<commit>", "head": "<commit>", "goal": "<what the change was for>",
 "language": "python", "labels": "complete|partial",
 "defects": [{"path": "src/a.py", "from_line": 10, "to_line": 14,
              "category": "correctness", "note": "<what breaks>"}]}
```

Three sources, each answering a different part:

| Source | Ground truth | Answers | Notes |
|---|---|---|---|
| **S1 seeded from history** | A fix commit F whose own test fails on F's parent (checked with `scripts.mutation_check`); `git blame` on the lines F changed finds the commit B that introduced them (SZZ). The case is B's real diff and message; the defect is the lines F fixed. | Recall on real defects in real changes, with a real goal | Works on any git repository with tests, any language (SOUL P7). Starts with SwarmAI's own history, where most expert reviews happen. |
| **S2 AACR-Bench** | Human-annotated issues: 151 pull requests, 10 languages, Apache-2.0 (`Alibaba-Aone/aacr-bench` on Hugging Face; fields `pr_url`, `pr_target_commit`, `pr_source_commit`, `path`, `from_line`, `to_line`, `category`, `note`) | Recall across languages, and a lower bound on precision | Labels cover annotated issues only, so an unmatched finding is unlabelled, not false. Comparable to published numbers. |
| **S3 regression** | Findings an earlier review raised and a person or a passing test confirmed | "Still finds what it used to find" | Not used for precision (§1). |

## 4. Runner

`expert_bench run --suite <file> --expert <id> [--model M] [--sample N --seed S]`

For each case: a temporary worktree at `base` with the change applied, the
`core.code_intel.changed` output, and the same prompt `s_expert-review` §2 writes
(`EXPERT-ID`, the prompt file, the diff command, the goal), dispatched headless to the
`pipeline-expert` agent definition. What is measured is what is used: no separate
benchmark prompt.

Each result records the prompt file's hash, the model, tokens, wall time, the parsed
findings (`SEVERITY | file:line | what breaks | fix`) and the raw reply.

## 5. Scoring

1. **Deterministic match first:** a finding matches a defect when the paths are equal
   and the line is inside the defect's range widened by 3 lines.
2. **One judged question per candidate pair:** "do this finding and this defect name the
   same failure?" The judge's model is recorded with the score. A person spot-checks the
   judge once on a sample before its numbers are trusted.
3. **Metrics per expert × prompt hash × model:** recall (S1, S2); precision lower bound
   (S2: matched / all findings); judged precision (unmatched findings asked "real defect
   in this diff?", reported separately and never mixed with the lower bound); location
   accuracy; tokens and time per case.
4. `expert_bench compare <A> <B>` pairs the two results case by case and gives a
   bootstrap interval for each difference, so a change smaller than the noise reads as
   no change.

## 6. Cost

One expert review costs what one `pipeline-expert` sub-agent costs today. A full S2 run
of one expert is 151 reviews; S1 on SwarmAI's history is as many cases as the
extraction finds. A run's size is chosen by the question it answers (a prompt tweak:
a fixed sample; a model change: the full set). There is no spend limit (STEERING #2);
the result reports what the run cost.

## 7. Order of work

1. Case schema, runner, scoring and `compare`, with S1 on the SwarmAI repository.
   The first numbers are about the reviews we actually run.
2. S2 adapter (clone at the pinned commits, map the fields), for languages and
   comparability.
3. S3 from `expert_verdicts.jsonl` and the pipeline event logs.

## 8. Verification plan

- **Tests written before the code:** case-schema validation; SZZ extraction on a small
  fixture repository built in the test (known fix, known introducing commit); the match
  rule (inside, edge, outside the widened range, other path); the runner end to end with
  a fake expert that returns fixed text (no model); `compare` pairing and its interval on
  constructed results.
- **Invariants for the final run:** a result names its prompt hash and model; an
  unlabelled finding is never counted as a false positive; the runner leaves no
  worktree behind; nothing in the pipeline or `s_expert-review` calls the benchmark.
- **Review:** one zero-context review of this design's premise before code; `correctness`
  on the runner and scoring code.

## 9. Open questions

1. Order: S1 (our repo, our own failure classes) first, or S2 (external labels) first?
2. Should a run also replay a baseline reviewer (a plain "review this diff" prompt on the
   same model) so each number has a reference point?
