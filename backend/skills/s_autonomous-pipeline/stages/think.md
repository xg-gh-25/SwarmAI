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

### Self-Socratic Ambiguity Re-Scan (after the risk probe)

`INTERROGATE YOUR OWN ASSUMPTIONS — NOT THE USER`

After the Design Risk Probe produces its `verification` text and you've chosen a
recommendation, **re-scan the probe assumptions + recommendation** for residual
ambiguity. One self-answer round — the THINK-side mirror of EVALUATE's ambiguity
scan (see `evaluate.md` § "Self-Socratic Ambiguity Re-Scan" for the full
philosophy: interrogate the framing, not the user; this is the Understanding
Gate's "refute your claim" discipline at the design layer).

**Process (ONE round):**
1. Scan the `risk_probe[].verification` strings and the `recommendation` for
   ambiguity/hedge terms: `depends`, `maybe`, `not sure`, `mix of`, `somewhere
   between`, `standard`, `typical` + CJK `看情况 / 可能 / 大概 / 差不多 / 视情况 /
   标准做法 / 一般` (canonical: `pipeline_validator._AMBIGUITY_TERMS`).
2. For each hit: self-answer by reading code/DDD (e.g. a "standard pattern" must
   name the EXACT pattern + file). Escalate only genuinely user-intent ambiguity.
3. Record the scan in the research artifact `ambiguity_scan` field (same shape as
   EVALUATE — `{scanned_fields, terms_checked, hits[{term,where,resolution,kind}],
   hit_count, all_resolved}`).

**Validator-enforced (`_check_ambiguity_scan`) `[GATE·validator]`:** strict profiles REQUIRE the
block; trivial/docs/research exempt; every hit needs a non-empty resolution
(≥12 chars) or it BLOCKS. `hits: []` is valid (scanned, clean).

### Minimum Depth Gate (Meta-Intelligence L3)

THINK must produce substantive analysis, not a token-saving shortcut. Historical
data shows runs with deeper THINK (>10K tokens) have higher completion rates
and fewer adversarial findings.

**Minimum requirements (BLOCKING):** `[MUST]`

> ⚠️ **doc-code drift:** "BLOCKING" but only `key_findings` is a validator-required THINK
> field — `alternatives` is *recommended* (WARN, not BLOCK) and the 2-approach/3-probe COUNTS
> are not code-checked. `[MUST]` by discipline. Flagged in the drift table (AC4).

1. At least **2 distinct approaches** with explicit tradeoffs (not 1 approach + "don't do it")
2. At least **3 risk probes** attempted (verified, falsified, or unresolved)
3. Each alternative must state its **cost** (effort, risk, tradeoff) — not just benefits

**If the requirement is genuinely trivial:** The profile should be `trivial` (skips THINK).
If you're IN think, the requirement deserves depth. "This is obvious" is not a valid
reason to produce shallow output — it means EVALUATE mis-classified.

**Telemetry:** After completing THINK, record depth metrics:
```bash
python backend/scripts/artifact_cli.py run-observe --project <PROJECT> --run-id <RUN_ID> \
  --event think_depth --alternatives <N> --probes <N> --resolved <N> --escalated <N>
```

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> --run-id <RUN_ID> \
  --type research --producer s_autonomous-pipeline \
  --summary "3 alternatives for <topic>. Recommending: <approach>" --stage think \
  --data '{"key_findings":[...],"alternatives":[...],"recommendation":"...","risk_probe":[...],"ambiguity_scan":{"scanned_fields":["recommendation","risk_probe.verification"],"terms_checked":[...],"hits":[...],"hit_count":0,"all_resolved":true},"sources":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state plan --run-id <RUN_ID>
```

---

## Common Rationalizations

| Rationalization | Reality | Source |
|---|---|---|
| "I already know the best approach, skip alternatives" | Skipping alternatives means skipping the constraint that FORCES different designs. The "obvious" approach is often SPEED-optimized when the problem needs QUALITY or DELETION. One constraint you didn't consider = one class of failure you didn't prevent. | Pipeline design |
| "Research wastes tokens, let me just build" | C024: skipped research, read descriptions instead of code, shipped CSS from instructional text without rendering examples. "Understanding > Delivery" — building without understanding = building garbage. | C024 |
| "Only one approach exists — this is mechanical" | If truly mechanical, the evaluation scope should be "trivial" and the profile should be "trivial" (which skips THINK). If you're in THINK, the evaluation already determined multiple approaches are possible. Trust the profile. | Profile design |
| "I'll research as I build" | Research in BUILD = discovery mid-construction = rework. Research is cheap (read + think). Rework is expensive (undo + redo + retest). Separate phases exist to separate costs. | C024, LL14 |
