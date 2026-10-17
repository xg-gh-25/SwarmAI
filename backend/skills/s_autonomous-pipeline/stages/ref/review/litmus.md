# REVIEW ref — Litmus Pre-Gate
Read when: running card step 1 (every REVIEW entry), or a litmus verdict is FAIL/BORDERLINE.

## Litmus Pre-Gate (< 2 min, no tools, no sub-agent) `[GATE·validator]` <!-- R-review-001 -->
The `litmus_gate` block is a REQUIRED review-artifact field and Check 8e BLOCKS a
missing/invalid one (verdict ∈ PASS/BORDERLINE/FAIL, 4 `hf_checked` booleans,
`evidence` > 20 chars). Run it **before** spawning any review sub-agent: a 30-second
structural read of BUILD output, not a deep review.

**Input:** the code diff (git or inline) + the PLAN acceptance criteria (stage-start
`acceptance_criteria`; `run.json` → stages → plan → acceptance_criteria).

### Hard failures (any one → FAIL, rework to BUILD)
| # | Criterion | How to check |
|---|-----------|-------------|
| HF1 | Majority of new code is scaffold/boilerplate with no domain logic | Real conditionals, error handling for THIS system's failure modes, domain decisions? Or empty stubs, pass-through functions, TODOs? |
| HF2 | Acceptance Criteria obviously incomplete | For each AC, name the implementing function/file within 5 seconds. No coverage identifiable for >30% of ACs on a quick pass → FAIL (recall test, not rigorous verification). |
| HF3 | Internal contradiction — code contradicts its own docstring/comments | Do names match behavior? Do comments describe a different algorithm? |
| HF4 | Missing error handling on external boundaries | Any new HTTP call, file I/O, subprocess or DB query without try/except or an explicit error path = not production-ready |

### Soft signals (3+ present → BORDERLINE)
| Signal | Ownership (good) | Abdication (bad) |
|--------|-----------------|-----------------|
| Error handling | Handles THIS system's known failure modes | Generic `try: ... except Exception: pass` |
| Test coverage | Boundary conditions + sad paths | Only happy-path tests |
| Style consistency | Matches codebase naming/structure | Noticeably different patterns imported |
| Constants/limits | Specific values with justification | Magic numbers without comments |
| Complexity match | Volume proportional to the problem | Over-engineered OR suspiciously minimal |

### Outcome routing
| Verdict | Action |
|---------|--------|
| **PASS** | Proceed to the Spec Compliance Gate, then fan-out (or single-pass). No modifications. |
| **BORDERLINE** | Proceed, BUT inject into the adversarial prompt: `"Builder flagged weak areas: {litmus_weak_signals}. ALSO examine areas NOT flagged — builder has known self-review bias and may have directed you away from real issues."` |
| **FAIL** | Do NOT spawn sub-agents. Return to BUILD with specific rework instructions: which HF failed, what needs fixing. Max 2 litmus failures per pipeline run (tracked in `run.json`). On the 3rd fail → escalate to full REVIEW with ALL sub-agents regardless of changeset size, plus the penalty signal: `"Code failed litmus 3 times. Apply maximum scrutiny — builder may be cycling rather than fixing."` |

### Retry accounting
- Litmus failures count against the REVIEW stage retry budget (not BUILD's).
- The 2-failure max is per pipeline run, persisted in `run.json` under
  `stages.review.litmus_fail_count`. The counter does NOT reset between attempts.
- After escalation (3rd fail → full review), an adversarial BLOCK uses the same REVIEW
  retry pool. Standard retry limit applies.

### Anti-gaming
- Litmus is NOT a substitute for adversarial review. PASS ≠ "code is good"; PASS =
  "code is worth spending 30K tokens to deep-review."
- You ran litmus on your own code; bias applies. Uncertain on ONE criterion → mark it
  uncertain and PASS overall. Uncertain on 2+ hard-failure criteria at once = BORDERLINE,
  not PASS.
- Never self-grant PASS to avoid rework: rework is cheaper than a wasted adversarial
  cycle that finds 10+ issues.
- **Code enforcement:** `pipeline_validator.py` Check 8e validates `litmus_gate`
  (verdict PASS/BORDERLINE/FAIL, `hf_checked` = 4 booleans, `evidence` > 20 chars).
  Missing or invalid litmus = BLOCK. Evidence should cite HF1-HF4 (see the stage-start
  artifact example).
- Litmus PASS on HF3/HF4 does NOT substitute for the full Security Scan (check 2) or the
  Runtime Pattern Checklist (check 6). Adversarial must re-examine error handling and
  code/comment consistency at full depth regardless of the litmus verdict.
