---
title: Review expert benchmark
date: 2026-11-18
created: 2026-11-18
updated: 2026-11-18
project: SwarmAI
status: REVISED after one premise review (2026-11-18); phase 1 = S1 + baseline, decided by the owner
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

- **Is:** a replay of the experts, the way `s_expert-review` calls them, on cases with
  known defects, scored the same way every time. It answers one question per run: did
  this prompt / model / dispatch change make the expert find more of the known defects,
  or cost less, compared with a plain reviewer on the same model?
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
 "base": "<B^>", "head": "<B>", "goal": "<B's commit message>", "language": "python",
 "labels": "partial", "reviewed_before": false, "diff_lines": 420,
 "defects": [{"path": "src/a.py", "from_line": 10, "to_line": 14,
              "category": "correctness", "note": "<F's message>", "fix": "<F>"}]}
```

`from_line` / `to_line` are in the coordinates of the reviewed tree (B), never of a later
commit.

### S1: defects seeded from history (phase 1)

1. **A real defect.** A commit F that changes non-test source files and test files is a
   candidate when its tests fail on the old source and pass on its own: build F's tree,
   check out `F^ -- <F's non-test files>`, run F's changed test files and expect red; at F
   expect green. A fix spanning several files is reverted as a whole (`mutation_check`
   reverts one exact text at a time, so it is not used here).
2. **The change that introduced it (SZZ).** `git blame --porcelain F^ -- <file>` on the
   lines F deleted or changed names the commits that wrote them. A case is kept only when
   one commit B owns at least 80% of the blamed lines, and B's diff is at most 800 changed
   lines (measured on this repository: 14 sampled fixes blamed Bs of 35–4,409 lines,
   half of them split across two or more commits). A fix that only adds lines has nothing
   to blame and is left out in phase 1.
3. **Labels in B's coordinates.** The defect lines are the original line numbers blame
   reports for those lines in B (`--porcelain` gives the line number in the commit that
   wrote it), so later commits between B and F cannot move them.
4. **Category.** Each case's defect gets one expert category (`correctness`,
   `concurrency`, `security`, ...) once, when the case is built, by a judged question
   recorded in the case file; it can be read and corrected by hand.
5. **Recorded per case:** B's diff size and whether B went through a pipeline review
   (its trailers), so results can be split by both.

Measured on this repository: 6,195 commits, 3,415 whose message says fix; 1,612 change
both Python source and Python tests, 1,056 of those touch at most 6 files. The filters
above decide how many become cases; the extractor reports each filter's count.

### S2 AACR-Bench (phase 2) and S3 regression (phase 3)

| Source | Ground truth | Answers | Notes |
|---|---|---|---|
| **S2 AACR-Bench** | Human-annotated issues: 151 pull requests, 10 languages, Apache-2.0 (`Alibaba-Aone/aacr-bench`; fields `pr_url`, `pr_target_commit`, `pr_source_commit`, `path`, `from_line`, `to_line`, `category`, `note`) | Recall across languages; a lower bound on precision | Only annotated issues are labelled, so an unmatched finding is unlabelled, not false. |
| **S3 regression** | Findings an earlier review raised and a person or a passing test confirmed | "Still finds what it used to find" | Not used for precision (§1). |

## 4. Runner

### The sealed case

The expert must not be able to read the answer. Each case is built as a fresh repository
with one commit: `git archive B^` into an empty directory, `git init`, commit, then apply
B's patch to the working tree (the change under review is uncommitted, as in a native
review). No refs, no later commits, no remote. The review runs with a temporary `HOME`
and without the cognition tools (`cognition map/find`): the project's DDD and IMPROVEMENT
notes describe many of these very defects. `core.code_intel.changed` is left out for both
arms in phase 1: the code graph is the main checkout's, built at today's HEAD, so its
callers would be future code.

### The harness

`pipeline-expert` is a sub-agent definition (`model="inherit"`, its tool-call cap a hook
that acts on `agent_type == pipeline-expert` with an `agent_id`). A top-level headless
query would run without that cap and with another system prompt. So each review is a
parent session opened with `agents=agent_definitions()`, the budget hook, an explicit
model and the sealed case as its working directory; the parent's prompt spawns exactly one
`pipeline-expert` with the `s_expert-review` §2 prompt and returns the sub-agent's reply
word for word. The parent turn's cost is recorded apart from the expert's.

### Two arms, matched

- **Expert arm:** the roster `s_expert-review` §1 would call for B (from the change's
  kind), each with its own prompt file.
- **Baseline arm:** the same agent definition, tools, cap, model and envelope; only the
  prompt file is a plain "review this change for defects" file with the same output
  format (`SEVERITY | file:line | what breaks | fix`).

Each case is run k = 3 times per arm on a fixed sample, so run-to-run variance is
measured.

## 5. Scoring

1. **Deterministic match first:** a finding matches a defect when the paths are equal
   and the line is inside the defect's range widened by 3 lines.
2. **One judged question per candidate pair:** "do this finding and this defect name the
   same failure?" The judge's model is recorded with the score.
3. **Phase 1 measures recall, not precision.** An S1 case labels the one defect a later
   fix found; every other finding in B is unlabelled. Phase 1 reports: recall (the
   roster's combined findings and the baseline's, at their recorded tokens; each expert
   also on the defects of its own category), location accuracy, tokens and time per
   case, split by B's diff size and by whether B was reviewed before. A precision lower
   bound comes with S2. A precise precision number needs a hand-labelled sample (every
   real defect in a case marked by a person); it is not part of phase 1.
4. `expert_bench compare <A> <B>` pairs two results case by case and gives a bootstrap
   interval over cases and over the k replies, so a difference smaller than the noise
   reads as no change.
5. **Reading the numbers:** many Bs already went through a review, so S1 is the set of
   defects that escaped review, and today's prompts were tuned partly on them. A
   prompt-versus-prompt gain on old cases is optimistic; the split by "reviewed before"
   shows how much.

## 6. Cost

One case-arm-reply costs one parent turn plus one `pipeline-expert` review. A phase-1
run of N cases is N × 3 replies × (roster size + 1) reviews. A run's size is chosen by
the question it answers (a prompt tweak: a fixed sample; a model change: the full set).
There is no spend limit (STEERING #2); each result reports what it cost.

## 7. Order of work

1. **Case extraction (no model):** S1 candidates, the red/green check, SZZ, the filters
   with their counts, B-coordinate labels, the sealed case builder. Its first output says
   how many usable cases this repository yields.
2. **Runner and scoring:** the parent-session harness, both arms, the parser, the
   matcher, the judge, `compare`.
3. S2 adapter, then S3.

## 8. Verification plan

- **Tests written before the code:** case-schema validation; on a fixture repository
  built in the test (a known B, a later unrelated commit that shifts lines, a fix F with a
  test): the red/green check, SZZ naming B, the labels in B's coordinates despite the
  shift, the size and ownership filters; the sealed case has one commit and no other
  refs, and does not contain F; the match rule (inside, edge, outside the widened range,
  other path); the runner end to end with a fake expert (no model); `compare` pairing
  and its interval on constructed results.
- **Invariants for the final run:** a result names its prompt hash, model and arm; an
  unlabelled finding is never counted as a false positive; the sealed case never holds a
  commit after B; the runner leaves no directory behind; nothing in the pipeline or
  `s_expert-review` calls the benchmark.
- **Review:** this design had one premise review (findings folded in here). `correctness`
  reviews the extractor and the runner code.
