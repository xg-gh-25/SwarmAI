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

> **This is JUDGMENT, not a gate.** No regex or hook can decide it — the same string
> (`91K`) is a liability as a *usage snapshot* and fine as a *budget cap*. It cannot be
> mechanically enforced, so it lives here as a norm you must **apply, not check off**.
> Reading this as a denylist is the failure mode: don't pattern-match the examples
> below, ask the one question.

**THE ONE QUESTION (apply to every number / status / fact before storing it):**

> **Is this value dynamic — AND does it change no future judgment?**
> If both → **REJECT.** A value earns a place ONLY if it's *decision-relevant* (it will
> change how the agent or user judges/acts later) AND *stable* (it won't be wrong next
> week without anyone touching it). Both must hold. The conjunction is the whole test:
> - *dynamic + drives-no-judgment* → REJECT (a token count, a star snapshot).
> - *dynamic + decision-relevant* → keep, but store the **method** not the frozen value
>   (a version bump matters, so record "run `X` to get it", never the number itself).
> - *stable + decision-relevant* → keep (a convention, an architecture decision, a
>   failure lesson, a principle).
> - *stable + drives-no-judgment* → still SKIP (trivia is not knowledge).

**Examples of REJECT (illustrations of the question, NOT a checklist to match):**
- Continuously-drifting counts: LOC, file/test/skill counts, line numbers, star/fork
  snapshots, token sizes, "N sessions / N commits", percentages-of-the-moment.
- A raw number whose only use is "to know the number" — that's a measurement, taken live.
- Status true only right now: "daemon is up", "CI is green", "3 tabs open".
- One-off transient context with no cross-session reuse.

**For a drifting-but-occasionally-needed metric:** store the **reproducible method**
(the command / query that regenerates it), NEVER the frozen output. Or describe it
qualitatively ("runs in production daily") instead of with a number. A number stored
with a "⚠️ don't trust, re-measure" warning next to it is STILL a liability — the
warning does not travel with the value when it's later read and quoted.

> **Live anchor (2026-07-19):** a `~91K token` snapshot sat in KNOWLEDGE.md next to its
> own "⚠️ do NOT trust as current" warning. The agent read it, ignored the warning, and
> quoted `91K` as current to the user (real value: 50K). The warning failed; only NOT
> STORING the number would have. This is why the norm is "don't let it in", not "label it".

When in doubt → **SKIP** and say so briefly. Under-persisting costs a re-derivation;
over-persisting poisons recall with stale noise. **If Step 0 says SKIP → stop here, do
not route.** Only content that passes Step 0 continues to Step 1.

### Step 1: Is this a behavioral rule for SOUL/AGENT/STEERING?

**Governance boundary:** If the content is a behavioral rule, standing rule, or gate that would change how the agent acts across ALL sessions — it's governance, not knowledge.

**Signals:** "new rule for STEERING", "from now on always", "add to AGENT.md", "behavioral gate"

**If YES → REDIRECT:** Tell the user: "This looks like a governance rule — use `s_self-evolution` PROMOTE operation instead." Do NOT write it here.

**If NO → Continue to Step 2.**

### Step 2: Route by content type + project context

> **⚠️ Canonical-doc LOCATION (read before writing a DDD doc — split-brain guard):**
> the 4 canonical docs (PRODUCT/TECH/IMPROVEMENT/PROJECT) live under
> **`Projects/<X>/2-understanding/<doc>`**, NOT the project root. The root path is the
> PRE-migration location; the layout SSOT is `backend/core/ddd_paths.py` (`ddd_path()`
> reads new-first, so recall only ever reads `2-understanding/`). Writing to the root
> `Projects/<X>/TECH.md` creates an ORPHAN: git-tracked but never recalled (this is a
> real bug that stranded a principle — run_ff06972d). If a `2-understanding/` subdir is
> absent for an un-migrated project, fall back to root; when in doubt, resolve with
> `python -c "from core.ddd_paths import ddd_path; print(ddd_path('Projects/<X>','TECH.md'))"`.

| Content type | Project-scoped? | Target |
|---|---|---|
| **Failure/bug/regression lesson** | Yes → `Projects/<X>/2-understanding/IMPROVEMENT.md` § What Failed | No → skip (always project-scoped) |
| **Success/ROI/caught lesson** | Yes → `Projects/<X>/2-understanding/IMPROVEMENT.md` § What Worked | No → skip |
| **Risk/watch-for/pattern** | Yes → `Projects/<X>/2-understanding/IMPROVEMENT.md` § What to Watch For | No → skip |
| **Technical convention/rule** | Yes → `Projects/<X>/2-understanding/TECH.md` § Conventions | No → `.context/MEMORY.md` § Guidelines |
| **Runtime trap/env issue** | Yes → `Projects/<X>/2-understanding/TECH.md` § Runtime Traps | No → `.context/MEMORY.md` § Guidelines |
| **Architecture decision** | Yes → `Projects/<X>/2-understanding/TECH.md` § Architecture | No → `.context/MEMORY.md` § Models |
| **Strategic priority** | Yes → `Projects/<X>/2-understanding/PRODUCT.md` § Strategic Priorities | No → skip |
| **Non-goal/defer** | Yes → `Projects/<X>/2-understanding/PRODUCT.md` § Non-Goals | No → skip |
| **Project decision** | Yes → `Projects/<X>/2-understanding/PROJECT.md` § Recent Decisions | No → skip |
| **Cross-project principle** | — | `.context/MEMORY.md` § Principles |
| **Self-correction/bias** | — | `.context/EVOLUTION.md` § Corrections Captured |
| **Reference/fact/spec** | — | `Knowledge/Library/` (searchable store — recall can find it) |

> **⚠️ Routing fix (run_794adfaf, R4c):** reference/fact content goes to
> `Knowledge/Library/`, **NOT** `.context/KNOWLEDGE.md`. KNOWLEDGE.md is an
> always-injected *index/cache* file (one of the 11 context files) — it is a
> **sibling** of `Knowledge/`, so `sync_knowledge_index` (which scans `Knowledge/`
> only) never chunks it. Content written to KNOWLEDGE.md is injected-as-context but
> **invisible to FTS5/BM25 recall**. Write durable reference material to
> `Knowledge/Library/` so it enters the searchable store; KNOWLEDGE.md stays the
> curated index the agent reads at session start.

### The Key Question

> "换一个项目，这条经验还有用吗？"
> **YES → MEMORY.md** (cross-project cognitive knowledge)
> **NO → Projects/<X>/...** (project-specific DDD doc)

## RETIRE / MOVE — the sanctioned "out" side (do NOT hand-Edit to delete)

Persist has an **out** side, not just in. When an entry is resolved, stale, wrong,
or belongs in a different home, **retire it via the `ddd-retire` CLI — never a raw
`Edit`-to-delete.** A hand-Edit skips the archive (→ the entry is lost from FTS5
recall) and skips the `(title, section)` identity-strip (→ it can silently destroy a
different entry that merely shares the title). `ddd-retire` archives (recall-preserved)
+ strips by identity + writes a dated `.bak`, and is **fail-loud** (no match / ambiguous
duplicate → error, nothing removed).

```bash
# from the swarmai repo root, backend venv active
# 1) PREVIEW (default dry-run — always look first):
python backend/scripts/artifact_cli.py ddd-retire \
  --file .context/MEMORY.md --title "Exact entry title" --section "Open Threads"
# 2) APPLY:
python backend/scripts/artifact_cli.py ddd-retire \
  --file Projects/SwarmAI/2-understanding/IMPROVEMENT.md --title "..." --section "What Failed" --apply
# keep-class (decision/model/principle/correction/COE) is REFUSED unless you add --force
```

**RETIRE** = remove one named entry (archive + strip). **MOVE** across files/sections =
**add-to-target FIRST, retire-from-source SECOND** — so the entry is durable in its new
home before it leaves the old one (a crash between the two steps then leaves a
recoverable duplicate, never a loss):

1. `s_persist` the entry into its new home (this file's routing + dedup).
2. `ddd-retire --apply` the entry from its old home.

This mirrors the governance layer's symmetry (s_self-evolution PROMOTE↔RETIRE) at the
knowledge layer (s_persist add ↔ ddd-retire out). Autonomous time-based decay
(`ddd_entry_lifecycle`, 60d→dormant→150d→archived) still handles *un-attended* aging;
`ddd-retire` is for a *deliberate* "this is done / wrong-home, remove it NOW".

**Undo a retire:** a retire is recoverable, not permanent. The entry is preserved in
two places — the archive file (`<doc>-archive.md`, e.g. `MEMORY-archive.md`, which stays
FTS5-recallable) and a dated pre-strip snapshot (`<doc>.<YYYY-MM-DD>.bak`). To restore:
copy the entry block back from the archive (or the `.bak`) into the source doc's correct
section, then run `s_persist` on it so routing + dedup re-apply. (There is no `ddd-restore`
CLI yet — restore is a manual copy-back; the archive/​.bak guarantee it's never lost.)

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

> **[type] Title** → `Projects/SwarmAI/2-understanding/IMPROVEMENT.md` § What Failed
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
