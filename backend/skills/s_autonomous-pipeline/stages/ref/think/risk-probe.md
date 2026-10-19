# THINK ref — Design Risk Probe

Read when: any probe is "unresolved", you think the probe can be skipped, or more than half
the probes are unresolved on a high-stakes choice. The card (`stages/think.md` steps 3-4) has
the core procedure.

## Process

The probe runs **after research, before presenting alternatives**. It is self-answering: the
agent names the risks and resolves them by reading code or DDD docs, instead of asking the user.

1. For each alternative, identify the **3 riskiest assumptions**: things that, if wrong, would
   change the recommendation.
2. For each assumption, **try to verify or falsify it yourself:**
   - Can you Read the codebase to confirm? → do it
   - Can you check TECH.md/IMPROVEMENT.md? → do it
   - Is it genuinely unknowable without user input? → mark as "unresolved"
3. Present the probe results alongside the alternatives.

Scarcity: **max 3 assumptions per approach × 3 approaches = 9 probes max.**

## Output (`risk_probe` field of the research artifact)

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

## Acting on the results

- Verified + falsified assumptions → update the alternatives (remove non-viable approaches,
  adjust effort estimates, change the recommendation).
- Unresolved assumptions → present them to the user as part of the alternatives output:
  "I couldn't verify X — my recommendation assumes Y, override if wrong."
- If ALL assumptions are verified → skip user interaction, proceed with the recommendation.

## Many unresolved probes on a high-stakes choice (rare)

If **>50% of probes are "unresolved" AND the choice is high-stakes** (judgment-class decision),
ask the user ONE question at a time with your recommended answer (`escalation.md`:
in-band if `channel=direct`, else the recorded question + checkpoint). **Max 5 questions.**

## When the probe can be skipped entirely

- Scope is trivial (S effort, proven pattern)
- User already specified the approach ("use pipeline", "just do it")
- Only one viable approach exists (mechanical, no design choice)

Note the tension with the card's minimum-depth gate (3 probes attempted); the pre-split doc
stated both. If you skip, say why in `record.decisions[]` so the gap is visible. A
skip-worthy requirement usually means EVALUATE should have picked the `trivial` profile.
