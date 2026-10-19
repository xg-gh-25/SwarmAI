# INSTRUCTIONS ref — Human in the loop: when to ask, when to decide + 3d. Classify Decisions
Read when: a decision might be L2 Judgment, an EVALUATE ambiguity is open, you are stuck
(retries exhausted, a circuit breaker, a gate that keeps refusing), or you are unsure whether
to ask the user.

**Autonomy means the human intervenes LESS, not never** (XG, 2026-10-19). The pipeline
handles mechanical dead ends itself and decides whatever has a defensible default. When the
requirement is genuinely unclear, or a decision is genuinely the user's (ambiguous AND hard to
reverse), it ASKS. Asking then is encouraged and correct; pushing on with a guess to have
something to deliver is the failure. Approval prompts for dangerous or external actions
(`dangerous_command_gate`, `external_approval_gate`) always stay; never route around them.

## 3d. Classify Decisions
Every non-trivial decision during stage execution MUST be classified:

| Class | Definition | Action | Example |
|---|---|---|---|
| **Mechanical** | One correct answer, deterministic | do it | "Use pytest (pyproject.toml)" |
| **Taste** | A sensible default exists, a human might differ | take the default; ONE-line disclosure; recorded | "Monolith over microservice for solo dev" |
| **Judgment (L2)** | Genuinely ambiguous AND hard to reverse; you cannot defend one answer | **ask the user** (below) | "This changes the public API" |

Log each decision in the stage record (`record.decisions` for stage-complete):
```json
{"stage": "build", "description": "Used sync retry instead of async", "classification": "taste",
 "reasoning": "Matches existing codebase style, simpler, but async would be more correct"}
```

## Ask the user — the requirement or the decision is genuinely not yours
Ask when, after reading the code, DDD and the requirement yourself, one of these still holds:
- the requirement has ≥2 readings that lead to different code and no reading is clearly safer;
- a constraint conflict you cannot resolve from the sources;
- an L2 judgment: a public API / data-model / one-way migration choice, a product trade-off,
  anything the user would reasonably want to make.

How:
- **A human attends this tab** (`channel=direct` in your runtime header): `AskUserQuestion`
  NOW, at the point you hit it (any stage): 2-4 options, each with a short why, your
  recommendation first. On the answer, continue the SAME run at the same stage; record it
  (`clarifications[]` with `answered_by: user`, or `record.decisions`).
- **No attended channel** (header absent, another channel, a background job): do NOT call
  `AskUserQuestion` (a channel auto-answers it; a headless job hangs). Record the question and
  options, then `run-checkpoint --reason "question: …"`: the run waits for the answer. Never
  answer it yourself to keep going.
- One pointed question at a time; never open-ended ("what do you want?"). Never ask what you
  can find out by reading or running something.

## Decide yourself — mechanical dead ends never wait for a human
| Situation | Disposition |
|---|---|
| **Taste** decision, or an ambiguity with a clearly safer reading | Take it, record it, ONE `[assumption]` / `[decision]` line in DELIVER `attention_flags` (REPORT §10). |
| **Retries exhausted, replan fails, meta-review HIGH unresolved, test circuit breaker** | Stop fixing. Complete the current stage (it degrades after 3 refusals) and DELIVER with `push_ready: false`, the blocker in `push_ready_reason` + `attention_flags`. `run-commit` commits locally, marked. |
| **A gate refuses `stage-complete` again and again** | Fix what it names. The 3rd refusal of the same stage completes it **degraded** (push_ready capped false, the errors in REPORT §10). Input/shape, profile and stage-order errors never degrade: fix them. |
| **A sub-agent spawn is refused** | Retry once; still refused → record `<gate>_unverified_reason` and go on; push_ready is capped. Never self-review. |
| **Unexpected tool error** | Retry once with the printed fix; still failing → as stuck (row 2). |

Legal hand-backs: a question you cannot answer yourself (above), the user stopping the run,
and a terminal verdict (DEFER / REJECT / abandon, reason logged). Budget and token use are
never a reason to stop.

## Over-ask guard
Asking costs the user's attention; guessing on a real ambiguity costs the delivery. Ask only
for Judgment (L2) and genuine ambiguity; decide Mechanical and Taste. **Worked
counter-example:** "I lean toward retry-with-backoff but want to confirm" is Taste: there is
a sensible default (your leaning) and it is reversible, so decide and disclose one line.
