# THINK Stage

## Base Methodology

> **Reference:** `backend/skills/s_deep-research/SKILL.md`
>
> Follow the constraint-driven alternatives framework: research the requirement,
> summarize key findings, present 3 approaches (each with an explicit constraint),
> and recommend one with reasoning.

## Pipeline-Specific Behavior

### DDD Alignment

If DDD docs are available:
- Align with PRODUCT.md priorities
- Avoid IMPROVEMENT.md failures

### Constraint-Driven Alternatives (T2)

**Replace** generic Minimal/Ideal/Creative labels with **explicit constraints**
that force genuinely different designs:

| Constraint | Forces | Good for |
|-----------|--------|----------|
| **SPEED** | Ship in 1 session, cut scope ruthlessly | Urgent features, proven patterns |
| **QUALITY** | Survive 2 years, full test coverage, extensible | Core architecture |
| **SIMPLICITY** | Junior dev can maintain, minimal abstractions | Utility features |
| **FLEXIBILITY** | Support 3 future use cases you can imagine | Platform features |
| **DELETION** | Easiest to remove if wrong, minimal coupling | Experimental features |

**Selection logic:** Pick the 3 most relevant constraints based on the evaluation:
- High feasibility score → include SPEED
- High strategic score → include QUALITY
- Low feasibility → include SIMPLICITY
- Uncertain scope → include DELETION

Each approach: **Constraint** (which one), **What** (1-2 sentences), **Effort**
(T-shirt + sessions), **Risk**, **Tradeoff**. End with recommendation.

**Fallback:** If constraints don't fit the problem (pure research, docs-only),
revert to Minimal/Ideal/Creative.

### Grill Protocol (T1)

**After research, before presenting alternatives**, run a structured grilling
session to stress-test assumptions:

1. For each alternative, identify the **3 riskiest assumptions**
2. Ask the user **ONE question at a time** about each critical assumption
3. For each question, provide **YOUR recommended answer** (not open-ended)
4. User can: accept the recommendation, override with their answer, or discuss
5. Capture resolved decisions as they crystallize

**Grill rules:**
- One question at a time. Wait for the answer before asking the next.
- If a question can be answered by reading the codebase → read it, don't ask
- If user says "just pick" or "skip" → accept all recommended answers, proceed
- **Max 10 questions** per session (scarcity forces prioritization)
- Focus on assumptions that, if wrong, would change the recommendation

**Output (appended to research artifact `grill_results` field):**

```json
{
  "grill_results": [
    {
      "question": "Should we use regex or BeautifulSoup?",
      "recommendation": "Regex — zero deps, structure is simple",
      "resolution": "accepted",
      "rationale": "User agreed — simplicity over robustness"
    }
  ]
}
```

**Skip when:**
- Research shows only one viable approach (mechanical, not design)
- Scope is trivial (S effort, proven pattern)
- User already specified the approach ("use pipeline", "just do it")

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type research --producer s_autonomous-pipeline \
  --summary "3 alternatives for <topic>. Recommending: <approach>" \
  --data '{"key_findings":[...],"alternatives":[...],"recommendation":"...","sources":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state plan
```
