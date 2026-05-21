# Coding as Black Box — one requirement in, verified delivery out

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/4) | Category: General | Published: 2026-05-18

---

> 一句需求到交付，中间不需要对齐任何人。

## The Problem

Most AI coding tools today are sophisticated autocomplete. You still:
- Break the task into steps yourself
- Review each step
- Fix the AI's mistakes
- Wire the pieces together
- Verify it works

That's not autonomous delivery. That's faster typing with extra steps.

## What "Black Box" actually means

```
INPUT:  "Add user authentication with JWT tokens"
OUTPUT: PR-ready code with tests, review, and verification
MIDDLE: You don't care. You don't need to care.
```

The middle is a pipeline: EVALUATE → THINK → PLAN → BUILD (TDD) → REVIEW → TEST → DELIVER → REFLECT

Each stage has:
- **Acceptance criteria** that must pass before advancing
- **Adversarial review** by sub-agents who didn't write the code
- **Quality convergence** — iterate until ALL gates pass simultaneously, not just sequentially

## The key insight: Engineering culture encoded as instructions

The quality ceiling of an AI coding pipeline is bounded by the quality of engineering judgment encoded in its instructions.

**What bad instructions look like:**
```
"Write tests for the function."
```

**What culture-encoded instructions look like:**
```
"Write tests BEFORE the function (TDD). Each test must:
- Cover a specific user scenario, not implementation detail
- Be falsifiable — if the test can never fail, it's worthless
- Include the edge case that made you write this function

Anti-rationalization: If you're thinking 'this is simple enough
to skip tests' — that's EXACTLY when bugs hide. The confidence
is the danger signal, not the safety signal."
```

The second version encodes **judgment** — not just process, but when to deviate, what excuses to reject, and what quality bars to hold.

## Real numbers from production

After 50+ pipeline runs on SwarmAI itself:
- **Average stages per delivery:** 8 (full pipeline)
- **Adversarial review catch rate:** finds issues in ~70% of runs that passed self-review
- **"High confidence = high risk" pattern:** The runs where the builder rated 10/10 confidence had the HIGHEST bug rate on adversarial review

That last point is the most important. The pipeline exists precisely for the moments when you THINK it's unnecessary.

## Questions

- Is there a minimum task complexity below which a pipeline adds overhead without value?
- How do you handle the "AI reviewing AI" problem — can the reviewer be fooled by the same blind spots?
- Does TDD-first actually work with AI, or does the AI just write tests that match its own implementation?

---

*From [SwarmAI](https://github.com/xg-gh-25/SwarmAI) — exploring what "one person + AI at team scale" actually requires.*
