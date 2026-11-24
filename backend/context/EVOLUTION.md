<!-- ⚠️ AGENT-OWNED — written by CDF (the knowledge flow), never by hand.
     Users: tell me a correction or a lesson ("log correction"); CDF records it. -->

# SwarmAI Evolution Registry

> **What this file is:** EVOLUTION.md is context-file slot 8 — the self-evolution /
> correction registry: the corrections I received and the competences I learned, so a
> corrected mistake stays corrected. AGENT-OWNED: CDF writes its entries, you direct it
> ("log correction"). Cross-session cognitive facts go in MEMORY.md; governance
> rule/principle changes route through s_self-evolution.

## Evolution Rules

1. **Agent-owned, written by CDF.** Every entry comes in as a candidate the agent that saw it declared (correction or competence, its evidence, and whether it is new, adds to, overrides or repeats an existing entry); CDF checks the facts and its writer appends it whole (AGENT R22). Nothing edits an entry by hand.
2. **Captured from the session that earned it.** A correction or a reusable competence is extracted from the session where it happened, with that session as its evidence, never reconstructed from memory later.
3. **No speculation.** Every entry must come from real execution. No placeholders, no "planned".
4. **Corrections > capabilities.** Preventing repeated mistakes has higher priority than adding new features.
5. **One format.** The writer renders every entry in the Entry Format below (ID → Date → kind → title → body).
6. **Failures are submitted too.** A failed attempt that taught something is a correction or a competence like any other: its cause and the condition for success.
7. **Evolve externally.** New abilities go into skills + registry. Never mutate core system code during evolution.
8. **Nothing is counted into buckets.** A repeat is declared against the entry it repeats; the share of corrections that repeat an earlier one is measured by the north star (`scripts.north_star`), not tallied here.

---

## Evolution Principles

_Only principles proven through repeated experience belong here._

1. **Verify reality, not memory.** Check workspace files and activity logs before making claims. Unverified memory that gets repeated becomes self-reinforcing false belief.
2. **Fix root causes, not symptoms.** Design flaws > surface bugs. If the fix adds a special case, step back.
3. **Corrections compound.** What I avoid matters more than what I build.
4. **Same-session closure.** If it's worth diagnosing, it's worth fixing now.
5. **Make failure impossible.** Prefer structural guarantees over recovery logic.
6. **Match action to failure mode.** Not all failures should be retried the same way.
7. **Solve the current problem.** Avoid premature generalization. A 50-line script beats a 500-line framework.

---

## Entry Format

CDF's writer appends each entry as one block:

```
### <prefix><NNN> | YYYY-MM-DD
- **<kind>**: **<title>** <body>
```

- **Prefix and kind**: `C` (Correction) in Corrections Captured, `K` (Competence) in Competence Learned
- **NNN**: one past the highest number of that prefix in its section
- A correction never ages out: it leaves only when a later entry overrides it or it is withdrawn (AGENT R24)

---

## Corrections Captured

_No corrections recorded yet._

---

## Competence Learned

_No competences recorded yet._

---

_This file is the seed template: structure only. The running instance accumulates its own corrections and competences from actual use._
