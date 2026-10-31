---
name: persist
description: "Save knowledge to the right store (DDD docs, MEMORY.md, Knowledge/Library, EVOLUTION.md) by content type. TRIGGER: \"remember\", \"save\", \"persist\", \"沉淀\", \"record this\", \"save lessons\". NOT FOR: governance rules (s_self-evolution)."
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

### Step 1: Locate the FILE LAYER (which file owns this — and via which APPROVAL PATH)

**Before choosing a section (Step 2), first decide WHICH FILE this content belongs to.**
Routing wrong at the file layer is the #1 mis-persist: a system-identity or product-level
design principle wrongly landed in a DDD's TECH.md (2026-08-15) because Step 1 used to
only fence SOUL/AGENT/STEERING and had no home for SWARMAI/IDENTITY/SELF.

> **SSOT for file ownership = `SWARMAI.md` § "My 11 Context Files — Position & Ownership".**
> The table below is the operational routing view of it — if they ever diverge, SWARMAI.md
> wins. Key consequence encoded there: **SwarmAI product/system architecture is NOT knowledge
> for KNOWLEDGE.md** (it goes to the SwarmAI DDD's TECH.md, or SWARMAI.md for charter-level
> statements); KNOWLEDGE.md is a thin `Knowledge/` directory index, never a prose home.

**The axis is NOT "can I write it?" — it is "which APPROVAL PATH does it change through?"**
Everything is changeable (that is what cognitive self-evolution IS); nothing is "forbidden".
The three paths differ only in WHO approves and WHERE the source lives. Two structural facts
drive this (SSOT: `context_directory_loader.py` `CONTEXT_FILES` + `user_customized` flag):

1. **Source vs projection.** SYSTEM-OWNED files (`user_customized=False`, chmod `0o444`)
   have their SOURCE in `backend/context/<f>.md` and their `.context/<f>.md` is a **read-only
   PROJECTION overwritten from the source every session** — hand-editing the projection is
   futile. Editing them = edit the `backend/context/` source **+ rebuild**.
2. **Governance never auto-writes.** SOUL/AGENT/STEERING change ONLY through
   `s_self-evolution` PROMOTE, which carries the human-approval gate (model proposes, OS
   gates, human approves). This is the promotion path, not a ban.

| Content it owns | File | Source location | s_persist writes directly? | Approval path |
|---|---|---|---|---|
| Product/system-level **design principle** (e.g. Darwinian decay), what SwarmAI IS | **SWARMAI.md** | `backend/context/` (projection is read-only) | ❌ no | edit `backend/context/` source **+ rebuild** — **user approve** (major action) |
| Agent name / avatar / identity | **IDENTITY.md** | `backend/context/` | ❌ no | edit source + rebuild — user approve |
| Personality / cognitive **principle** (P-level) — PRESCRIPTIVE: *how the agent must behave* across all sessions | **SOUL.md** | `backend/context/` | ❌ no | **→ `s_self-evolution` PROMOTE** (human-approve gate) |
| Behavioral **rule** / gate | **AGENT.md** | `backend/context/` | ❌ no | **→ `s_self-evolution` PROMOTE** (human-approve gate) |
| User standing **rule** / preference-as-rule | **STEERING.md** | `.context/` (live) | ❌ no | **→ `s_self-evolution` PROMOTE** (human-approve gate) |
| Runtime self-portrait | **SELF.md** | `.context/` (live, runtime-owned) | ❌ no | human/distill writes only (auto-cultivation code-blocked) — do not hand-write |
| User profile facts | **USER.md** | `.context/` (live) | ⚠️ user-domain — prefer asking | direct edit ok if user-directed |
| Tool / MCP guidance | **TOOLS.md** | `.context/` (live) | ⚠️ | direct edit ok |
| **Cross-project** cognitive knowledge / principle — DESCRIPTIVE: *what the agent has learned* (informs, doesn't prescribe) | **MEMORY.md** | `.context/` (live) | ✅ **yes — s_persist home** | agent-autonomous (Step 2 §Principles) |
| Self-correction / bias | **EVOLUTION.md** | `.context/` (live) | ✅ **yes — s_persist home** | agent-autonomous (Step 2 §Corrections) |
| (index/cache — never write prose here) | **KNOWLEDGE.md** | auto-rebuilt from fs | ❌ no | write the fact to `Knowledge/Library/` instead |
| **Project-domain** knowledge | **DDD** PRODUCT/TECH/IMPROVEMENT/PROJECT (`Projects/<X>/2-understanding/`) | project disk | ✅ yes | agent-autonomous (Step 2 table) |
| Cross-project searchable **reference/fact** | **Knowledge/Library/** | project disk | ✅ yes | agent-autonomous |

**Governance signals** ("new rule for STEERING", "from now on always", "add to AGENT.md",
"behavioral gate", a P-level principle, a system/product design principle): the content is
governance/identity, not agent-writable knowledge → route it to the approval path in the table
above (`s_self-evolution` for SOUL/AGENT/STEERING; `backend/context` source-edit+rebuild for
SWARMAI/IDENTITY), tell the user which path + why, do NOT hand-write the file. **If the file
layer is one of the ✅ (MEMORY/EVOLUTION/DDD/Library) → continue to Step 2** to pick the section.

### Step 2: Route by content type + project context

> **⚠️ Canonical-doc LOCATION (read before writing a DDD doc — split-brain guard):**
> the 4 canonical docs (PRODUCT/TECH/IMPROVEMENT/PROJECT) live under
> **`Projects/<X>/2-understanding/<doc>`**, NOT the project root. The root path is the
> PRE-migration location; the layout SSOT is `backend/core/ddd_paths.py` (`ddd_path()`
> reads new-first, so recall only ever reads `2-understanding/`). Writing to the root
> `Projects/<X>/TECH.md` <!-- layout-example: the wrong path, shown on purpose --> creates an ORPHAN: git-tracked but never recalled — a real bug that stranded a principle. If a `2-understanding/` subdir is
> absent for an un-migrated project, fall back to root; when in doubt, resolve with
> `"$SWARM_BACKEND_BIN" py -c "from core.ddd_paths import ddd_path; print(ddd_path('Projects/<X>','TECH.md'))"`.

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

> **⚠️ Routing fix(R4c):** reference/fact content goes to
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

## RETIRE / MOVE

Do not invoke a legacy mutator. User-directed retirement and moves go through the
workspace editor APIs, which require the revision returned by the latest read and use
`governed_edit`. If a directory contains governed files, edit the files individually;
only project tree moves are supported atomically.

## How to submit

Sources never choose a file or edit governed content. Build one complete
`contracts_v2` unit and bind it to the user message that asked for persistence.
Pass `--evidence last-user`: the CLI resolves this turn's user message from the
session. (Never pass `$SWARM_LAST_USER_EVENT_ID`: a reused CLI keeps the id of the turn
it was started on, which is stale from the second turn on.)

```bash
printf %s '{"kind":"cognition","type":"guideline","scope":"<scope>","title":"<complete title>","what":"<complete claim>","how":"<action when applicable>","why":"<why it matters>","applicability":"<trigger>","evidence":[]}' | \
  "$SWARM_BACKEND_BIN" ingest submit --source S1 \
  --evidence last-user --payload-json - --wait 10
```

The CLI validates that the evidence is the latest user event of this turn and
allows at most three units for it. A unit that does not fit the contract comes back as
its own `{"status": "rejected", "unit": N, "detail": "<field>: <why>"}` line and the CLI
exits 2: fix the field it names and submit that unit again. A cron, auto-nudge, or injected turn has no
eligible user event and must not call S1. Relay the CLI echo to the user
**unchanged**, including its complete text, actual destination, and hint. A timeout
means the candidate is visible in Cognitive Distillation Flow, not that it failed.

## Rules

- **Never edit a governed destination directly** — only run `ingest submit`.
- **Complete claim** — include the conclusion, applicability, and why/how needed to understand it later.
- **Current evidence** — pass `--evidence last-user`; never cite an older turn.
- **No pre-routing** — scope and project context are hints; CDF owns the final destination.
- **Governance detection** — behavioral governance still follows its explicit promotion path.
- **Reach decides the scope** — `"agent"` and `"world"` are read in every future session:
  use them only for what changes how the agent judges in most tasks (a principle, a
  decision, a model, a correction, a broad lesson). A fact that matters only when one tool,
  library, API or subject comes up, however reliable, is `"project:<that project or
  subject>"` (a subject that is no project goes to the General Brain under its name);
  the user's own rule or preference is `"user"`.
- **Project context** — a unit about one project says so in its scope: `"scope":"project:<name>"`
  plus `"topic"` (`decision` / `product` / `architecture` / `lesson`); there is no separate
  project field (an unknown field refuses the unit). Do not invent a project.

## Verification

- [ ] Payload is complete and self-contained.
- [ ] `--source S1` and `--evidence last-user` were supplied.
- [ ] The CLI echo was relayed to the user unchanged.
- [ ] No governed file was written directly.
