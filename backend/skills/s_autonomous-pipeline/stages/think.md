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

### Design Risk Probe (T1)

**After research, before presenting alternatives**, stress-test each approach's
riskiest assumptions. Unlike the old "grill protocol" (which asked the user and
was almost always skipped), this is a **self-answering probe** — the agent
identifies risks and resolves them by reading code or DDD docs.

**Process:**

1. For each alternative, identify the **3 riskiest assumptions** — things that,
   if wrong, would change the recommendation
2. For each assumption, **try to verify or falsify it yourself:**
   - Can you Read the codebase to confirm? → do it
   - Can you check TECH.md/IMPROVEMENT.md? → do it
   - Is it genuinely unknowable without user input? → mark as "unresolved"
3. Present the probe results alongside alternatives

**Output (in research artifact `risk_probe` field):**

```json
{
  "risk_probe": [
    {
      "approach": "A (SIMPLICITY)",
      "assumption": "existing hook interface accepts new event type",
      "verification": "Read hook_executor.py — confirmed: dispatches any event dict",
      "status": "verified"
    },
    {
      "approach": "B (QUALITY)",
      "assumption": "sqlite-vec available in PyInstaller bundle",
      "verification": "Checked verify_build.py — NOT in hidden imports list",
      "status": "falsified — approach B not viable without build change"
    },
    {
      "approach": "C (SPEED)",
      "assumption": "user wants temporary solution replaced later",
      "verification": "Cannot determine from codebase — user intent",
      "status": "unresolved"
    }
  ]
}
```

**Rules:**
- Verified + falsified assumptions → update alternatives accordingly (remove
  non-viable approaches, adjust effort estimates, change recommendation)
- Unresolved assumptions → present to user as part of alternatives output:
  "I couldn't verify X — my recommendation assumes Y, override if wrong."
- If ALL assumptions are verified → skip user interaction, proceed with recommendation
- **Max 3 assumptions per approach × 3 approaches = 9 probes max** (scarcity)

**When to do a full grill instead (rare):**
If >50% of probes are "unresolved" AND the choice is high-stakes (judgment-class
decision), escalate to the interactive grill protocol: ask the user ONE question
at a time, provide your recommended answer, wait for confirmation. Max 5 questions.

**Skip entirely when:**
- Scope is trivial (S effort, proven pattern)
- User already specified the approach ("use pipeline", "just do it")
- Only one viable approach exists (mechanical, no design choice)

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type research --producer s_autonomous-pipeline \
  --summary "3 alternatives for <topic>. Recommending: <approach>" --stage think \
  --data '{"key_findings":[...],"alternatives":[...],"recommendation":"...","risk_probe":[...],"sources":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state plan
```
