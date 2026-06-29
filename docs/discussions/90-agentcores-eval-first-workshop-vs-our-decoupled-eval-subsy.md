---
title: "AgentCore's Eval-First Workshop vs Our Decoupled Eval Subsystem — Where Two Designs Agree, and Diverge"
created: 2026-06-29
updated: 2026-06-29
status: published
---
<!-- GitHub Discussion #90: https://github.com/xg-gh-25/SwarmAI/discussions/90 -->
> 🌐 English | 中文版 → #91 · Related: #74 OS Eval vs AgentCore · #88 Cognitive Evolution

# AgentCore's Eval-First Workshop vs Our Decoupled Eval Subsystem

> AWS shipped a hands-on sample — *Eval-First: Building Enterprise Agents with
> AgentCore* — with two **custom code-based evaluators** you can actually read:
> **THELMA** (single-turn RAG quality) and **Mind the Goal** (multi-turn goal
> success). We build a self-evolving agent OS with its own eval subsystem. I read
> their evaluator *code* (not the README) and put it next to ours. The interesting
> part isn't where we differ — it's that two independent teams converged on the
> same non-obvious conviction.

## The convergence: a score without attribution is worthless

Most eval tooling stops at a number: "groundedness 0.62." Useless on its own —
0.62 *because of what?* Bad retrieval? A fabricating model? Dirty source docs?
You can't act on the score; you can only act on the *cause*.

Both of AWS's evaluators are built around exactly this, and so is ours:

- **THELMA** doesn't just emit a groundedness score. Its `interplay.py` reads the
  *combination* of its 7 sub-scores as a differential diagnosis: `SP1 high + SP2
  low` → your source chunks are dirty (relevant-looking, mostly noise); `SQC↓ +
  RQC↑ + GR↓` → the model is fabricating, fix the prompt; `SQC↓ + RQC↓` → retrieval
  failure, fix the retriever. The score is the symptom; the interplay is the
  diagnosis.
- **Mind the Goal** doesn't just emit a Goal Success Rate. Every failed goal gets an
  **RCOF code** (Root Cause of Failure) from a fixed taxonomy — E1 language
  understanding / E2 refusal / E3 incorrect retrieval / E4 retrieval failure / E5
  system error / E6 incorrect routing. The GSR tells you *how much* is broken; the
  RCOF distribution tells you *what to fix first*.

This is the same conviction our pipeline's REPRO gate enforces internally:
**diagnosis before action.** We block a fix at publish unless it carries
observation evidence, not an inferred cause — because fast convergence to a wrong
diagnosis is worse than slow convergence to a right one. Seeing AWS's *evaluators*
encode the identical "score → cause → action" shape, from a completely different
starting point (enterprise RAG QA, not a coding agent), is the strongest signal
we've had that the shape is right.

## Three places their design taught us something concrete

I went in expecting to validate our approach. I came out with three borrowable ideas.

**1. The capability-vs-regression split.** Their workshop separates *capability*
eval (low starting score, "a mountain to climb," drives improvement) from
*regression* eval (near-100%, maintains a baseline, goes on CI). A mature
capability case **graduates** into the regression suite. We had all our cases in
one pool — which is exactly why a partially-failing run reads as an ambiguous
"0.0" instead of "capability frontier at 60%, regression baseline holding at 100%."
Two different questions, two different gates.

**2. RCOF as a runtime failure taxonomy.** Our correction registry has a
*cognition*-facing taxonomy (CLASS A "I wrote it so it works", CLASS B "inferred
without verifying", CLASS C "wrong layer"). What we lacked is an *agent-runtime*
taxonomy for eval failures — exactly what Mind the Goal's E1–E6 provides. The two
are complementary: theirs attributes a *run* failure, ours attributes a *judgment*
failure.

**3. Bias mitigation as a first-class judge property.** The workshop is explicit
about LLM-judge biases — position bias (swap A/B, verdict flips), verbosity bias,
authority bias — and mitigations (bidirectional scoring, panel-of-judges). We pin
our judge (fixed model + T=0) but don't yet do bidirectional scoring. Cheap to add,
and it directly hardens the L2 (model-scored) evidence layer.

## Where we diverge — and why

This isn't "they're ahead." Our subsystem makes three architectural bets theirs
doesn't, by design, because we're a different kind of system:

| Dimension | AgentCore sample | Our subsystem |
|-----------|------------------|---------------|
| **Where eval runs** | Lambda, invoked by the AgentCore eval service on a trace | Decoupled top-level subsystem (`Eval/`), invoked by CI / deploy / schedule — never inside the work it judges |
| **Eval IP ownership** | Your evaluator code is yours; platform schedules it | Same conviction, taken further: golden set split into **public** (git-tracked, references public code) + **private** (gitignored, references internal state), merged at load with an `_origin` tag, collision fails loud |
| **What it judges** | The agent's task output (RAG answers, goal completion) | The agent's output **and its own cognition** — a "behavior tier" that evals whether the OS followed its own constitution |
| **Trigger discipline** | On-demand / online / batch | **Git-bound, fail-closed gate** — eval is a system concern triggered post-deploy, structurally forbidden from running inside a coding pipeline (it would test the un-deployed binary) |

The deepest shared principle, stated by AWS as *"evaluation platforms can be
purchased, evaluation content must be self-controlled,"* is one we'd already made a
first principle (we call it eval-as-IP). Their split of *managed platform* +
*your evaluators* is the productized version of the same idea our decoupled `Eval/`
subsystem expresses in a single-builder OS.

## The one-line takeaway

If you're building agent eval: **make the evaluator emit a cause, not just a
score.** THELMA's score-interplay diagnosis and Mind the Goal's RCOF taxonomy are
two clean, readable reference implementations of that — and they're MIT-0. The
number tells you something's wrong; only the attribution tells you what to do
Monday morning.

Reference: AWS sample (MIT-0) —
https://github.com/aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore
· THELMA paper arXiv:2505.11626 · Mind the Goal paper arXiv:2510.03696
