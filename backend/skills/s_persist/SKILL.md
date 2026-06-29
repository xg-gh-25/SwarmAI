---
name: persist
description: "Persist knowledge to the correct destination — routes to DDD docs (PRODUCT/TECH/IMPROVEMENT/PROJECT), MEMORY.md, Knowledge/Library/ (searchable store), or EVOLUTION.md based on content type. Unified routing for both manual saves and auto hooks.\n  TRIGGER: \"remember\", \"save\", \"persist\", \"沉淀\", \"record this\", \"save to DDD\", \"save lessons\".\n  NOT FOR: save-context, save-activity, self-evolution (governance rules) use cases."
tier: always
---
# Persist — Unified Knowledge Routing

Persist knowledge to the correct destination. Routes content based on type and project context to the appropriate file and section. Replaces the old `s_save-memory` (which wrote everything to MEMORY.md).

## Routing Decision Tree (3 steps)

### Step 0: Should this be persisted AT ALL? (admission gate — default is SKIP)

Persisting is not free. A stored fact that drifts becomes a **liability**: stale → it
misleads; fresh → it costs upkeep; and if it never drove a judgment, it was worthless
the moment it was written. The best fix for a misleading stale entry is to never store
it. **Most passing facts should NOT be persisted.** (AGENT R30#4 / P6.)

**REJECT (do not persist) — volatile, zero-decision-value data:**
- Counts that drift continuously: LOC, file/test/skill counts, line numbers, star/fork
  snapshots, token sizes, "N sessions / N commits", percentages-of-the-moment.
- A raw number whose only use is "to know the number." If it doesn't change a future
  *decision*, it's not knowledge — it's a measurement, and measurements are taken live.
- Status that's true only right now: "daemon is up", "CI is green", "3 tabs open".
- One-off transient context with no cross-session reuse.

**For a drifting-but-occasionally-needed metric:** store the **reproducible method**
(the command / query that regenerates it), NEVER the frozen output. Or describe it
qualitatively ("runs in production daily") instead of with a number.

**ADMIT (persist) only if BOTH hold:**
1. **Decision-relevant** — it will change how the agent or user judges/acts later.
2. **Stable** — it won't be wrong next week without anyone touching it (a convention,
   an architecture decision, a failure lesson, a principle — not a live metric).

When in doubt → **SKIP** and say so briefly. Under-persisting costs a re-derivation;
over-persisting poisons recall with stale noise. **If Step 0 says SKIP → stop here, do
not route.** Only content that passes Step 0 continues to Step 1.

### Step 1: Is this a behavioral rule for SOUL/AGENT/STEERING?

**Governance boundary:** If the content is a behavioral rule, standing rule, or gate that would change how the agent acts across ALL sessions — it's governance, not knowledge.

**Signals:** "new rule for STEERING", "from now on always", "add to AGENT.md", "behavioral gate"

**If YES → REDIRECT:** Tell the user: "This looks like a governance rule — use `s_self-evolution` PROMOTE operation instead." Do NOT write it here.

**If NO → Continue to Step 2.**

### Step 2: Route by content type + project context

| Content type | Project-scoped? | Target |
|---|---|---|
| **Failure/bug/regression lesson** | Yes → `Projects/<X>/IMPROVEMENT.md` § What Failed | No → skip (always project-scoped) |
| **Success/ROI/caught lesson** | Yes → `Projects/<X>/IMPROVEMENT.md` § What Worked | No → skip |
| **Risk/watch-for/pattern** | Yes → `Projects/<X>/IMPROVEMENT.md` § What to Watch For | No → skip |
| **Technical convention/rule** | Yes → `Projects/<X>/TECH.md` § Conventions | No → `.context/MEMORY.md` § Guidelines |
| **Runtime trap/env issue** | Yes → `Projects/<X>/TECH.md` § Runtime Traps | No → `.context/MEMORY.md` § Guidelines |
| **Architecture decision** | Yes → `Projects/<X>/TECH.md` § Architecture | No → `.context/MEMORY.md` § Models |
| **Strategic priority** | Yes → `Projects/<X>/PRODUCT.md` § Strategic Priorities | No → skip |
| **Non-goal/defer** | Yes → `Projects/<X>/PRODUCT.md` § Non-Goals | No → skip |
| **Project decision** | Yes → `Projects/<X>/PROJECT.md` § Recent Decisions | No → skip |
| **Cross-project principle** | — | `.context/MEMORY.md` § Principles |
| **Self-correction/bias** | — | `.context/EVOLUTION.md` § Corrections Captured |
| **Reference/fact/spec** | — | `Knowledge/Library/` (searchable store — recall can find it) |

> **⚠️ Routing fix (run_794adfaf, R4c):** reference/fact content goes to
> `Knowledge/Library/`, **NOT** `.context/KNOWLEDGE.md`. KNOWLEDGE.md is an
> always-injected *index/cache* file (one of the 11 context files) — it is a
> **sibling** of `Knowledge/`, so `sync_knowledge_index` (which scans `Knowledge/`
> only) never chunks it. Content written to KNOWLEDGE.md is injected-as-context but
> **invisible to FTS5/vector recall**. Write durable reference material to
> `Knowledge/Library/` so it enters the searchable store; KNOWLEDGE.md stays the
> curated index the agent reads at session start.

### The Key Question

> "换一个项目，这条经验还有用吗？"
> **YES → MEMORY.md** (cross-project cognitive knowledge)
> **NO → Projects/<X>/...** (project-specific DDD doc)

## How to Write

### Entry format (all targets use same format)

```markdown
- [type] **Title** — concise description (YYYY-MM-DD)
  <!-- ref:0 | last:none | decay:active | source:manual -->
```

Where `[type]` is one of: `guideline`, `pitfall`, `decision`, `principle`, `correction`, `process`, `model`.

### Mechanics

1. Read the target file to find the correct `## Section`
2. Use the Edit tool to prepend the new entry at the top of the section
3. Include the metadata comment with `source:manual`
4. Show the structured receipt to the user

### Receipt format (MANDATORY)

```
**Persisted:**

> **[type] Title** → `Projects/SwarmAI/IMPROVEMENT.md` § What Failed
> _(Why: one-sentence reason this knowledge compounds)_
```

## Rules

- **Always use the Edit tool** — never use Bash scripts for file writes
- **Always date-prefix** — every entry includes `(YYYY-MM-DD)` at the end
- **Always add `source:manual` metadata** — distinguishes from auto-cultivated entries
- **Newest first** — prepend to section, don't append
- **Don't duplicate** — check if content already exists (match by title/content)
- **Governance boundary** — if it's a behavioral rule → redirect to `s_self-evolution`
- **Project detection** — infer from: current file being edited, pipeline context, user mention, or ask

## Verification

- [ ] **Passed Step 0 admission gate** — decision-relevant AND stable; not a volatile metric (LOC/counts/status/snapshot). If it's a drifting number, stored the method not the value.
- [ ] Entry saved to correct file + section
- [ ] `source:manual` metadata tag present
- [ ] No duplicates (checked before writing)
- [ ] Receipt shown to user with destination + why
