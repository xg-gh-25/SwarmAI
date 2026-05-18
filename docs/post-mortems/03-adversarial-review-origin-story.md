# Why We Made Adversarial Review Mandatory (And What It Catches)

> The builder can't review their own assumptions.
> The assumptions are invisible to them.
> That's why a fresh perspective isn't optional — it's architecture.

## The Origin: C011

Our autonomous pipeline (9 stages, quality gates, TDD) shipped a feature that was 100% non-functional. See [Post-Mortem #1](./01-pipeline-confidence-illusion.md) for the full story.

The fix wasn't "review harder." It was **structural independence** — spawn a completely separate agent with zero context from the build, and have it review the code fresh.

## What Adversarial Review Actually Is

```
Builder finishes → Self-review passes → Tests pass → Type-check passes
  → NOW: Spawn fresh sub-agent
  → Zero shared context (no conversation history, no assumptions carried forward)
  → Reads code from scratch
  → Reads DDD docs independently
  → Reports findings
```

It's not "another pass of the same review." It's a fundamentally different *perspective* — the reviewer doesn't know:
- Why the builder made specific choices
- What alternatives were considered and rejected
- What "should" be there vs. what the builder forgot

This means the adversarial reviewer catches exactly what the builder structurally cannot: **things that look wrong from the outside but feel natural from the inside.**

## What It Catches (Real Examples)

| Category | Example | Why Builder Missed It |
|----------|---------|----------------------|
| **Zombie states** | Declared `'interrupted'` state never reachable | Builder knows *intent* to implement it, so it "feels" present |
| **Cross-boundary mismatches** | `voice_id="Zhiyu"` + `language="en-US"` | Builder traced happy path (English); never tested Chinese path |
| **Concurrent races** | `stop()` + `start()` without ordering guarantee | Builder tested sequentially; concurrent case "seemed unlikely" |
| **Happy-path bias** | Error path returns generic 500 instead of structured error | Builder focused on making it work, not making it fail gracefully |
| **Dead code confidence** | Handler registered but never called (import exists, wire missing) | Builder sees the import and assumes it's wired |

Key insight: 16 sequential self-checks missed what 1 fresh-context review found. It's not about quantity of review — it's about **independence of perspective.**

## Why Self-Review Has Systematic Blind Spots

The builder has context that helps them write code but *harms* their ability to review it:

1. **Intention masking** — "I meant to implement X" prevents seeing that X isn't actually implemented
2. **Familiarity blindness** — code you just wrote looks correct because you recognize your own patterns
3. **Assumption carry-forward** — you tested the happy path while building; you assume the sad path "obviously works" too
4. **Scope anchoring** — you know what's in scope, so you don't check things "outside" your change that your change affects

Adversarial review has none of these. It reads code like a new team member on their first day — confused by anything that doesn't make sense on its own terms.

## Implementation Pattern

If you want to add adversarial review to your pipeline:

```python
# Pseudo-code for the adversarial review gate

def adversarial_review(code_diff, project_docs):
    """
    BLOCKING gate — runs AFTER self-review passes.
    Fresh agent, zero shared history.
    """
    reviewer = spawn_fresh_agent(
        context=[],  # No conversation history
        instructions="""
        You are reviewing code you've never seen before.
        You have no context about why decisions were made.
        Report anything that looks wrong, incomplete, or suspicious.
        Focus on: state reachability, cross-boundary contracts,
        error paths, timing assumptions, dead code.
        """
    )
    
    findings = reviewer.review(
        code=code_diff,
        docs=project_docs,  # DDD docs for domain context
    )
    
    if findings.critical > 0:
        return BLOCK  # Fix before shipping
    return PASS
```

Key constraints:
- **Zero shared context** — the reviewer must not see builder's conversation
- **Domain docs are fair game** — DDD/specs provide *what should be true*, not *why it was built this way*
- **Findings are blocking** — if it catches something, it must be fixed (not "noted for later")

## The Recurrence Pattern

After establishing adversarial review as mandatory, it was bypassed twice:
- **C021:** Skipped adversarial under time pressure ("tests pass, it's fine")
- **C025:** Skipped entire pipeline under comfort bias ("I know this code")

Both times, bugs shipped. Both times, the bugs were exactly the class that adversarial review catches. This proved the gate isn't optional — it exists for the cases where you *feel* most confident, because those are precisely when your blind spots are largest.

## Measured Results

| Metric | Before Adversarial | After Adversarial |
|--------|-------------------|-------------------|
| Bugs shipped to user per release | ~3 | ~0.3 |
| Bug severity shipped | P0 catastrophic | P2 edge case |
| Bugs caught per pipeline run | ~5 (self-review) | ~8 (self + adversarial) |
| False positive rate | N/A | ~10% (over-flags on fresh context) |

The 10% false positive rate is acceptable — fresh context occasionally flags intentional choices as suspicious. Quick to dismiss, and the alternative (missed bugs) is much more expensive.

## Takeaways

1. **Self-review is necessary but not sufficient.** It catches mechanical issues (syntax, types, test failures). It cannot catch assumption blindness.
2. **Fresh context > more passes.** 16 self-checks by the builder < 1 check by a reviewer who's never seen the code.
3. **Make it structural, not cultural.** "We should do adversarial review" fails under pressure. "The pipeline physically won't advance without it" doesn't.
4. **The gate is most valuable when you're most confident.** High confidence = you're in a blind spot. That's when adversarial review earns its keep.

---

*For the original correction entry, see [C011 in EVOLUTION.md](../../backend/context/EVOLUTION.md). For the full pipeline spec including adversarial review, see [INSTRUCTIONS.md](../../backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md) DELIVER stage.*
