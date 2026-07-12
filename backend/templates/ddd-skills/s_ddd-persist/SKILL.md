---
name: ddd-persist
description: "Sediment knowledge into THIS DDD's docs — with the FULL routing discipline of SwarmAI's s_persist: a Step-0 admission gate (most facts should NOT be persisted), a governance boundary, content-type routing, and a sanctioned RETIRE/MOVE reverse side. Additive, honors human-authored content, never overwrites judgment. Uses the bundled locked-write engine (scripts/locked_write.py, no SwarmAI backend).\n  TRIGGER: \"persist to ddd\", \"remember in this ddd\", \"sediment this lesson\", \"update the ddd docs\".\n  NOT FOR: SwarmAI's own MEMORY/EVOLUTION routing (that's native s_persist); behavioral RULES (→ governance, not knowledge)."
tier: lazy
---
# DDD Persist (s_ddd-persist) — sediment cognition into the DDD, with discipline

Route knowledge into the correct section of THIS DDD, additively, under a lock. This is
the WRITE side of the self-養成 loop: what the `s_ddd-pipeline` REFLECT stage learns gets
sedimented here so the next run is smarter. **But persisting is a judgment, not a reflex**
— most passing facts should be SKIPPED (see Step 0).

> **DDD-native decouple of SwarmAI's `s_persist`.** Same routing discipline + admission
> philosophy, re-homed to the DDD's own files. The locked read-modify-write engine ships
> bundled (`scripts/locked_write.py`, pure-stdlib + fcntl); the SwarmAI injection-guards
> (`core.memory_guard`) are fail-soft absent — a DDD's docs aren't SwarmAI's always-injected
> MEMORY.md. No `data.db`, no SwarmAI memory index, no backend.

## The Routing Decision Tree (3 steps — do them in order)

### Step 0: Should this be persisted AT ALL? (admission gate — default is SKIP)

Persisting is not free. A stored fact that drifts becomes a **liability**: stale → it
misleads; fresh → it costs upkeep; and if it never drove a judgment, it was worthless the
moment it was written. **Most passing facts should NOT be persisted.**

**REJECT (do not persist) — volatile, zero-decision-value data:**
- Counts that drift: LOC, file/test counts, line numbers, star/fork snapshots, token sizes,
  "N sessions / N commits", percentages-of-the-moment.
- A raw number whose only use is "to know the number." If it doesn't change a future
  *decision*, it's a measurement — take measurements live, don't store them.
- Status true only right now: "build is green", "3 tabs open".
- One-off transient context with no cross-session reuse.

**For a drifting-but-occasionally-needed metric:** store the **reproducible method** (the
command that regenerates it), NEVER the frozen output. Or describe it qualitatively.

**ADMIT (persist) only if BOTH hold:**
1. **Decision-relevant** — it will change how the agent or user judges/acts later.
2. **Stable** — it won't be wrong next week without anyone touching it (a convention, an
   architecture decision, a failure lesson, a principle — not a live metric).

When in doubt → **SKIP** and say so briefly. Under-persisting costs a re-derivation;
over-persisting poisons recall with stale noise. **If Step 0 says SKIP → stop here.**

### Step 1: Is this a behavioral RULE (governance), not knowledge?

If the content is a behavioral rule / standing rule / gate that would change how the agent
ACTS across all work — it's **governance, not knowledge**. Signals: "from now on always",
"add a rule", "behavioral gate".

**If YES → REDIRECT**, don't write it here: governance belongs in the DDD's ③ `gates/`
(an executable gate + knockout test) or the project's rule doc — promoted deliberately,
via the 养成 ladder, not dropped into a knowledge section. Only NON-rule knowledge continues.

### Step 2: Route by content type

| Content type | Destination (section) |
|---|---|
| **Failure / bug / regression lesson** | ② `IMPROVEMENT.md` § What Failed |
| **Success / ROI / caught-a-bug lesson** | ② `IMPROVEMENT.md` § What Worked |
| **Risk / watch-for / recurring pattern** | ② `IMPROVEMENT.md` § What to Watch For |
| **Technical convention / rule** | ② `TECH.md` § Conventions |
| **Runtime trap / env issue** | ② `TECH.md` § Runtime Traps |
| **Architecture decision** | ② `TECH.md` § Architecture |
| **Strategic priority** | ② `PRODUCT.md` § Strategic Priorities |
| **Non-goal / defer** | ② `PRODUCT.md` § Non-Goals |
| **Project decision (+ rejected alternative)** | ② `PROJECT.md` § Recent Decisions |
| **Deep reference material / spec** | `Knowledge/` (a standalone, indexed note) |

> **The key question when unsure which doc:** *"这条经验，对这个 DDD 的未来判断还有用吗？"*
> A DDD's four docs ARE its cross-run memory — there is no separate MEMORY.md here. Route
> by *which kind of judgment* it informs (product / technical / lesson / decision), per the
> table above. If it's not about THIS DDD's domain at all → it probably fails Step 0.

## How to write (use the bundled locked engine — never a raw Edit for appends)

```bash
python scripts/locked_write.py --file <ddd>/IMPROVEMENT.md --section "What Failed" \
  --prepend '- [pitfall] **Title** — concise description (YYYY-MM-DD)
  <!-- ref:0 | last:none | decay:active | source:manual -->'
```
`locked_write.py` does an fcntl-locked read-modify-write (+ intra-batch dedup) so concurrent
writers never corrupt a section. `--prepend` = newest-first. Modes: `--append` / `--prepend`
/ `--replace`.

**Entry format** (all targets, same shape):
```markdown
- [type] **Title** — concise description (YYYY-MM-DD)
  <!-- ref:0 | last:none | decay:active | source:manual -->
```
`[type]` ∈ `guideline · pitfall · decision · principle · process · model`.

**Receipt (MANDATORY — show the user):**
```
**Persisted:**
> **[pitfall] Title** → `IMPROVEMENT.md` § What Failed
> _(Why: one-sentence reason this knowledge compounds for THIS DDD)_
```

## RETIRE / MOVE — the sanctioned "out" side (persist has an OUT, not just IN)

When an entry is resolved / stale / wrong / belongs elsewhere, **retire it deliberately —
do NOT hand-Edit-to-delete**. A raw delete skips the archive (lost from recall) and can
destroy a different entry that merely shares the title.

- **RETIRE** = remove one named entry: archive it (recall-preserved) + strip by
  `(title, section)` identity + write a dated `.bak`. Fail-loud on no-match / ambiguous.
- **MOVE** across files/sections = **add-to-target FIRST, retire-from-source SECOND** — so
  the entry is durable in its new home before it leaves the old (a crash between the two
  leaves a recoverable duplicate, never a loss).
- **Undo** = the entry survives in the archive + the `.bak`; copy it back, re-persist.

(On SwarmAI this is the `ddd-retire` CLI; a portable DDD without that CLI does the same by
hand: copy the block to the doc's `-archive.md` first, then remove from source — never a
bare delete.)

## Rules

- **Additive, never destructive** — append/prepend; a human-authored block (`<!-- user -->`)
  is sovereign, preserved byte-for-byte.
- **Confident-only** — persist what's verified; tag speculation `[UNVERIFIED]`, don't assert it.
- **Newest-first** — prepend to the section.
- **Don't duplicate** — the engine dedups intra-batch; also check by title before writing.
- **Source-stamp** — every entry carries `(YYYY-MM-DD)` + `source:manual`.
- **The 养成 ladder** — a recurring pitfall climbs: prose (here, IMPROVEMENT.md) → rule →
  (3× recurrence) an executable ③ `gates/` gate with a knockout test. This skill writes the
  prose rungs; promotion to a gate is a `s_ddd-pipeline` act.

## Verification

- [ ] Passed Step 0 (decision-relevant AND stable; not a volatile metric)
- [ ] Not a behavioral rule (else → governance, Step 1)
- [ ] Routed to the correct doc + section (Step 2 table)
- [ ] Written via `locked_write.py`, newest-first, `source:manual` stamped
- [ ] No duplicate; receipt shown to the user
