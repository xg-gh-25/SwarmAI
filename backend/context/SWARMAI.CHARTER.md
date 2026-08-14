<!-- ⚙️ COMPANION REFERENCE — NOT an injected context file (not in CONTEXT_FILES).
     Same class as CONTEXT.md: it lives in backend/context/ but is NEVER part of the
     assembled system prompt. Read this BEFORE editing SWARMAI.md. -->

# SWARMAI.md — Editing Charter

This is the governance contract for `SWARMAI.md`. `SWARMAI.md` has NO auto-writer — its
only ingestion path is a HUMAN/AGENT hand-edit of the codebase template
(`backend/context/SWARMAI.md`). Therefore drift there is a **governance failure, not a
pipeline leak**, and this charter is the only thing that prevents it.

## Position (why the bar is this high)
`SWARMAI.md` is **priority-0, never-truncated, first-injected** — the most expensive slot
in EVERY session prompt. It holds ONLY the highest-altitude, most-stable **ANCHOR + MAP**:
who I am, how my workspace + body are shaped, where things live. It is the MAP, never the
terrain.

## MAY ENTER (only these)
- Core identity / role / priority hierarchy (the constitution's preamble).
- The defining IDEAS (Agent OS · Compound · Self-Evolution · Brain-First · Deliver-Anything
  · Proprioception) — the CONCEPT in 2-4 lines, NEVER its mechanism.
- The workspace + body MAP (SwarmWS · Projects · Knowledge · the nav zones · Canvas · TSCC)
  — a roster, one line each, NOT its internals.
- A capability ROSTER (Skills · MCPs · Jobs · Pipeline · Pollinate · Eval OS) — the KINDS I
  wield, one line each, NOT any single skill's spec.
- ONE-LINE anchors to canonical homes + FAQ pointers.
- The CLAUDE.md/AGENTS.md non-consumption declaration (security — its home).

## MUST NEVER ENTER (route to the file named)
- ❌ Any MECHANISM / algorithm / full spec        → KNOWLEDGE.md or a DDD doc
- ❌ UI-surface INTERNALS (props, events, layout)  → SELF.md (my body map)
- ❌ A single skill/MCP/job's spec or full roster  → TOOLS.md / the skill's SKILL.md
- ❌ Behavioral RULES                              → AGENT.md / STEERING.md
- ❌ Cognitive PRINCIPLES                          → SOUL.md
- ❌ A full COPY of a table/paradigm owned elsewhere (12-files table, the DDD R31 paradigm)
     → keep the 1-line pointer, delete the copy. (P8: one brain, many doors — a duplicated
     body across files drifts apart.)
- ❌ The SAME surface/capability listed in two sections here (e.g. Pipeline under both a
     capability roster AND a body-layout roster) → Body draws PHYSICAL layout only; function
     lives in Capabilities / Brain. Never both.
- ❌ Any drifting, decision-inert NUMBER (LOC / counts / sizes / %) — store the reproducible
     METHOD, never the frozen value. A dynamic cap → describe it ("up to 3 chat + 1 channel,
     dynamic by RAM"),
     don't freeze one machine's value. (R30#4)
- ❌ Session-local provenance / run-ids / "directive same day" notes → MEMORY / EVOLUTION.
- ❌ CJK prose in the body (system-file convention: English prose).

## Before adding a line
Is this a stable, highest-altitude anchor — or a mechanism/rule/principle/number/copy that
belongs in another file? If the latter, put a 1-line pointer here and the content there.
When in doubt, it does NOT belong in priority-0.

## How to edit
System-owned. Edit `backend/context/SWARMAI.md` (the template), never the `.context/` copy
(overwritten on startup). Takes effect on new sessions after rebuild+restart.
