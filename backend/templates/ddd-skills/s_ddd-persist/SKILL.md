---
name: ddd-persist
description: "Save knowledge into this DDD's docs with an admission gate, content-type routing and retire/move; additive, never overwrites. TRIGGER: \"persist to ddd\", \"remember in this ddd\", \"sediment this lesson\". NOT FOR: behavioral rules."
tier: lazy
---
# DDD Persist (s_ddd-persist) — sediment cognition into the DDD, with discipline

Route knowledge into the correct section of THIS DDD, additively, under a lock. This is
the WRITE side of the self-養成 loop: what a dev-loop's REFLECT stage learns (whatever
pipeline the host runtime runs over this DDD) gets sedimented here so the next run is
smarter. **But persisting is a judgment, not a reflex** — most passing facts should be
SKIPPED (see Step 0).

> **DDD-native decouple of SwarmAI's `s_persist`.** Same routing discipline + admission
> philosophy, re-homed to the DDD's own files. It runs in two places:
> - **Inside SwarmAI** (`$SWARM_BACKEND_BIN` is set): submit to Cognitive Distillation Flow
>   (below). CDF's writer is the only one that writes a DDD doc there, and
>   `scripts/locked_write.py` refuses to run.
> - **On a host without SwarmAI** (Kiro, Claude Code, an AIM package): there is no CDF,
>   so write with the bundled `scripts/locked_write.py` (pure-stdlib + fcntl lock).

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

| Content type | Destination (section) | Type |
|---|---|---|
| **Failure / bug / regression lesson** | ② `IMPROVEMENT.md` § What Failed | pitfall / correction |
| **Success / ROI / caught-a-bug lesson** | ② `IMPROVEMENT.md` § What Worked | guideline / principle / decision / model / process |
| **Risk / watch-for / gap with no evidence yet** | ② `IMPROVEMENT.md` § What to Watch For | guideline / pitfall |
| **Architecture decision or principle** | ② `TECH.md` § Architecture | decision / principle |
| **A load-bearing subsystem, what it owns** | ② `TECH.md` § Key Subsystems | model |
| **Technical convention / dev command** | ② `TECH.md` § Conventions | guideline / process |
| **Runtime trap / env issue** | ② `TECH.md` § Runtime Traps | pitfall / correction |
| **What it is, for whom, why** | ② `PRODUCT.md` § Vision | principle |
| **What it does that alternatives do not** | ② `PRODUCT.md` § What Makes It Different | model |
| **Strategic priority** | ② `PRODUCT.md` § Strategic Priorities | decision |
| **How anyone can tell it works** | ② `PRODUCT.md` § Success Criteria | guideline / process |
| **Non-goal / defer** | ② `PRODUCT.md` § Non-Goals | pitfall / correction |
| **Project decision (+ rejected alternative)** | ② `PROJECT.md` § Recent Decisions | any |
| **Deep reference material (tables, specs, long notes)** | `2-understanding/knowledge/<topic>.md` | — |

These are the sections and types CDF writes; nothing else goes in the four docs. Every
entry is `- [type] **Title** what …` followed by its metadata line.

> **The key question when unsure which doc:** *"这条经验，对这个 DDD 的未来判断还有用吗？"*
> A DDD's four docs ARE its cross-run memory — there is no separate MEMORY.md here. Route
> by *which kind of judgment* it informs (product / technical / lesson / decision), per the
> table above. If it's not about THIS DDD's domain at all → it probably fails Step 0.

## Inside SwarmAI: submit to Cognitive Distillation Flow

Use this whenever `$SWARM_BACKEND_BIN` is set. Do not write a DDD document or proposal
file. Build one complete project-scoped `contracts_v2` unit. The current user event is
exposed as `SWARM_LAST_USER_EVENT_ID` and is the only evidence S1 accepts.

```bash
printf %s '{"kind":"cognition","type":"guideline","scope":"project:<project>","title":"<complete title>","what":"<complete claim>","how":"<reusable method>","why":"<why it compounds>","applicability":"<trigger>","evidence":[]}' | \
  "$SWARM_BACKEND_BIN" ingest submit --source S1 \
  --evidence "$SWARM_LAST_USER_EVENT_ID" --payload-json - --wait 10
```

A cron, auto-nudge, or injected turn has no eligible user event and must not call
S1. Relay the CLI echo to the user **unchanged**, including the complete text,
actual destination, and hint. No human approval queue is created.

User edits, retirements and moves go through the workspace editor API with the
`expected_revision` returned by its latest GET. A stale revision returns 409 and must be
reloaded. Directory operations containing governed files are refused; project
rename/delete use the governed tree-move operation.

## On a host without SwarmAI: locked write

When `$SWARM_BACKEND_BIN` is not set, append the entry to the section Step 2 chose with
the bundled writer. It holds an advisory lock for the read-modify-write, so two writers
cannot lose each other's entry. It only adds; it never rewrites an existing entry.

```bash
python3 scripts/locked_write.py --file 2-understanding/TECH.md --section "Runtime Traps" \
  --prepend "- [pitfall] **<complete title>** <claim> **Why:** <why>.
  <!-- source:manual -->"
```

## Verification

- [ ] The entry is decision-relevant, stable, self-contained, and includes why.
- [ ] Inside SwarmAI: `ingest submit --source S1 --evidence "$SWARM_LAST_USER_EVENT_ID"`
      was used, the CLI echo was relayed unchanged, and no DDD file was written directly.
- [ ] Outside SwarmAI: the entry went through `scripts/locked_write.py` into the section
      Step 2 chose.
