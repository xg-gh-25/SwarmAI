---
name: ddd-persist
description: "Sediment knowledge into THIS DDD's docs — incremental, additive, honors human-authored content, never overwrites judgment. The counterpart to code-intel refresh: docs are OWNed cognition, only grown. DDD-native rewrite of SwarmAI's s_persist (file-based, no backend).\n  TRIGGER: \"persist to ddd\", \"remember in this ddd\", \"sediment this lesson\", \"update the ddd docs\".\n  NOT FOR: SwarmAI's own MEMORY/EVOLUTION routing (that's the native s_persist)."
tier: lazy
---
# DDD Persist (s_ddd-persist) — sediment cognition into the DDD

Route a piece of knowledge into the correct section of THIS DDD, additively. This is
the WRITE side of the self-養成 loop: what the `s_ddd-pipeline` REFLECT stage learns
gets sedimented here so the next run is smarter.

> **DDD-native rewrite of SwarmAI's `s_persist`.** Learned from the original's routing
> discipline, re-designed to be portable: writes only to the DDD's own files, no
> `data.db`, no SwarmAI memory index, no backend. Ships inside every DDD.

## Routing (which doc gets the knowledge)

| Content type | Destination | Rule |
|--------------|-------------|------|
| A pitfall / what-failed | ② `IMPROVEMENT.md` "What Failed" | the 养成 ladder starts here (prose → rule → ③ gate) |
| A what-worked pattern | ② `IMPROVEMENT.md` "What Worked" | reusable technique |
| A design/strategy decision | ② `PROJECT.md` "Recent Decisions" or an ADR | with rejected alternative |
| A domain/technical fact | ② `TECH.md` | architecture, conventions, runtime traps |
| A product/priority/scope change | ② `PRODUCT.md` | vision, non-goals, success criteria |
| Deep reference material | `Knowledge/` | a standalone note, indexed |

## Rules (portable, additive-only)

1. **Additive, never destructive.** Append or insert; do NOT overwrite human-authored
   prose. A human edit is sovereign — preserve it byte-for-byte.
2. **Honor preservation markers.** Any `<!-- user -->` / hand-authored block is never
   rewritten by this skill.
3. **Confident-only.** Persist what is verified, not speculation. An uncertain lesson
   is tagged `[UNVERIFIED]`, not asserted as fact.
4. **Source-stamp.** Each entry carries a short provenance (date + what produced it).
5. **The 养成 ladder.** A pitfall that recurs climbs: prose in IMPROVEMENT.md → (recurs)
   a rule → (3× recurrence) an executable ③ gate under `gates/` with a knockout test.
   `s_ddd-persist` writes the prose rungs; promotion to a gate is a `s_ddd-pipeline` act.

## Why portable

No SwarmAI memory subsystem — the DDD's four docs + `Knowledge/` ARE the store. That is
what lets sedimentation work identically inside Kiro / Claude Code after `aim` export:
the DDD grows its own cognition wherever it runs.
