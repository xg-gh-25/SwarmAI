---
name: context-hygiene
description: "Clean and compress SwarmAI's 12 context files (SWARMAI/IDENTITY/SOUL/SELF/AGENT/USER/STEERING/TOOLS/MEMORY/EVOLUTION/KNOWLEDGE/PROJECTS) — knows WHICH source file to edit (system→backend/context+rebuild vs runtime→.context direct vs auto-generated), WHAT to cut (drift numbers, echoed titles, dated pointers, blow-by-blow→pattern+tell, CJK-in-system-prose), and the C046 red-line (never cut a correction's pattern+tell or a principle kernel). Read-and-judge, not batch-auto; ships a read-only candidate scanner.\n  TRIGGER: \"clean the context files\", \"瘦身 context\", \"compress MEMORY/EVOLUTION\", \"context hygiene\", \"clean up SOUL/AGENT\".\n  NOT FOR: routing NEW knowledge (use s_persist); autonomous decay/dedup (context_health_hook already does it); non-context-file cleanup."
tier: lazy
---
# Context Hygiene — Clean & Compress the Context Files

The codified methodology for cleaning / 瘦身 SwarmAI's 12 context files: the
source-routing table (which file's SoT is `backend/context/` + needs rebuild vs
`.context/` direct vs auto-generated), the cleaning rules (drift numbers, echoed
titles, dated pointers, blow-by-blow→pattern+tell, CJK-in-system-prose), the C046
red-line (never cut a correction's pattern+durable-tell or a principle kernel), and
the read-and-judge method (a read-only scanner surfaces candidates; the agent reads,
judges, and deletes by hand — never a batch auto pass).

Scopes AWAY from `context_health_hook` (autonomous decay/dedup/regen) and `s_persist`
(content-type routing for NEW knowledge) — this is the manual SEMANTIC sweep neither does.

**→ Read `INSTRUCTIONS.md` for the full methodology + `scripts/scan.py` for the read-only scanner.**
