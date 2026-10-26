---
name: context-hygiene
description: "Clean, compress and audit SwarmAI's context files: where to edit, what to cut, what never to cut. TRIGGER: \"clean the context files\", \"prompt audit\", \"瘦身 context\", default model changed. NOT FOR: saving new knowledge (s_persist)."
tier: lazy
---
# Context Hygiene — Clean & Compress the Context Files

The codified methodology for cleaning / 瘦身 SwarmAI's 11 context files: the
source-routing table (which file's SoT is `backend/context/` + needs rebuild vs
`.context/` direct vs auto-generated), the cleaning rules (drift numbers, echoed
titles, dated pointers, blow-by-blow→pattern+tell, CJK-in-system-prose), the red-line (never cut a correction's pattern+durable-tell or a principle kernel), and
the read-and-judge method (a read-only scanner surfaces candidates; the agent reads,
judges, and deletes by hand — never a batch auto pass), and an audit mode that re-reads the
governed files for contradictions, duplicates and instructions written for an older model.

Scopes AWAY from `context_health_hook` (autonomous decay/dedup/regen) and `s_persist`
(content-type routing for NEW knowledge) — this is the manual SEMANTIC sweep neither does.

**→ Read `INSTRUCTIONS.md` for the full methodology + `scripts/scan.py` for the read-only scanner.**
