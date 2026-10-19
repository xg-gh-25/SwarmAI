# INSTRUCTIONS ref — Autonomous Disposition (no human hand-off) + 3d. Classify Decisions
Read when: a decision might be L2 Judgment, an EVALUATE ambiguity is open, you are stuck
(retries exhausted, a circuit breaker, a gate that keeps refusing), or a card still says
"checkpoint" / "ask the user".

**The pipeline is fully autonomous (2026-10-19).** One requirement in → a local commit out.
It never asks the user (`AskUserQuestion`), never parks a run on a checkpoint for a decision,
and never waits for a human. Every exit below has an autonomous disposition; the human's
control point is the commit itself (REPORT §10 says what was assumed, decided or left open)
and the push, which stays theirs. Where any older card text says "checkpoint", "escalate" or
"ask the user", this file wins.

## 3d. Classify Decisions
Every non-trivial decision during stage execution MUST be classified:

| Class | Definition | Action | Example |
|---|---|---|---|
| **Mechanical** | One correct answer, deterministic | do it | "Use pytest (pyproject.toml)" |
| **Taste** | Reasonable default, a human might differ | take the default; ONE-line disclosure; recorded | "Monolith over microservice for solo dev" |
| **Judgment** | Genuinely ambiguous AND hard to reverse | take the **most reversible** option; record it + an `attention_flags` line | "This changes the public API" |
Log each decision in the stage record (`record.decisions` for stage-complete):
```json
{"stage": "build", "description": "Used sync retry instead of async", "classification": "taste",
 "reasoning": "Matches existing codebase style, simpler, but async would be more correct"}
```

## Autonomous disposition (every former hand-off point)
| Situation | Disposition |
|---|---|
| **L2 Judgment** (any stage, boundary or mid-stage) | Choose the most reversible option: additive over breaking, behind a flag over always-on, keep the old path. Record it (`classification: judgment`, the alternative, how to revert) and put ONE `attention_flags` line in DELIVER: `[decision] chose X over Y because Z; revert: …`. Continue. |
| **EVALUATE ambiguity / constraint conflict** | Self-answer with the most conservative reading (`answered_by: self`, `status: assumed`), `[assumption]` flag, continue (`ref/evaluate/clarification.md`). Every reading unsafe → DEFER. |
| **Retries exhausted, replan fails, meta-review HIGH unresolved, test circuit breaker** | Stop fixing. Complete the current stage and go to DELIVER; complete it with `push_ready: false`, the blocker in `push_ready_reason` + `attention_flags`. `run-commit` commits locally, marked. |
| **A gate refuses `stage-complete` again and again** | Fix what it names. The 3rd refusal of the same stage completes it **degraded** by the tool (`degraded` in the record, push_ready capped false, the errors in REPORT §10). Input/shape errors never degrade: fix the payload. |
| **A sub-agent spawn is refused** | Retry once; still refused → record `<gate>_unverified_reason` and go on; the gate's missing evidence caps push_ready. Never self-review. |
| **Unexpected tool error** | Retry once with the printed fix; still failing → treat as stuck (row 3). |

Legal hand-backs are only: the user stopping the run, and a terminal verdict (DEFER /
REJECT / abandon, with the reason logged). Budget and token use are never a reason to stop.

## Taste vs L2 — nothing raises
No class asks the user; the difference is only what gets recorded.

| Class | Handling | Raises? |
|-------|----------|:-------:|
| **Mechanical** (one correct answer) | just do it | no |
| **Taste** (a sensible default exists, a human might differ) | decide the default + disclose in ONE line in the current tab ("chose X because Y; say so to override"); recorded in `record.decisions` | no |
| **Judgment / L2** (genuinely ambiguous AND hard to reverse — a public API change, a one-way data migration) | most reversible option + `[decision]` attention flag (table above) | no |

**Worked counter-example:** "I lean toward retry-with-backoff but want to confirm" is **Taste, not
L2** — there is a sensible default (your leaning) and it is reversible. Decide, disclose one line,
do NOT call AskUserQuestion. Raising it transfers your judgment cost onto the user (判断 ≠ 甩给人).
Disclosure is ONE line, never a paragraph — a wall of decision notes is its own flow-pollution.
