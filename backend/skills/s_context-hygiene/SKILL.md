---
name: context-hygiene
description: "Clean and compress SwarmAI's 11 context files: which source to edit, what to cut, what never to cut. TRIGGER: \"clean the context files\", \"compress MEMORY/EVOLUTION\", \"瘦身 context\". NOT FOR: saving new knowledge (use s_persist)."
tier: lazy
---
# Context Hygiene — Clean & Compress the Context Files

The codified methodology for cleaning / 瘦身 SwarmAI's 11 context files: the
source-routing table (which file's SoT is `backend/context/` + needs rebuild vs
`.context/` direct vs auto-generated), the cleaning rules (drift numbers, echoed
titles, dated pointers, blow-by-blow→pattern+tell, CJK-in-system-prose), the red-line (never cut a correction's pattern+durable-tell or a principle kernel), and
the read-and-judge method (a read-only scanner surfaces candidates; the agent reads,
judges, and deletes by hand — never a batch auto pass).

Scopes AWAY from `context_health_hook` (autonomous decay/dedup/regen) and `s_persist`
(content-type routing for NEW knowledge) — this is the manual SEMANTIC sweep neither does.

**→ Read `INSTRUCTIONS.md` for the full methodology + `scripts/scan.py` for the read-only scanner.**
