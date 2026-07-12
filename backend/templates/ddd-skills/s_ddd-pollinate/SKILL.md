---
name: ddd-pollinate
description: "Express THIS DDD's product value to audiences — message-first, format follows — sourcing value from the DDD's own ② PRODUCT.md, file-based, no SwarmAI backend. DDD-native rewrite of SwarmAI's s_pollinate.\n  TRIGGER: \"ddd pollinate\", \"express this ddd's value\", \"make content from this ddd\".\n  NOT FOR: SwarmAI's full media engine (that's the native s_pollinate)."
tier: lazy
---
# DDD-Native Pollinate (s_ddd-pollinate)

The **value-expression** capability a DDD carries: turn what the product IS (its ②
Knowledge — vision, differentiation, proof) into audience-shaped messages.
**Message-first, format follows the audience.**

> **DDD-native rewrite of SwarmAI's `s_pollinate`.** The original is a 9-stage engine
> bound to SwarmAI's brand assets, channel adapters, and schemas. This keeps the
> *principle* (audience → message → format) and sources everything from the DDD's own
> docs + file-state — portable to any runtime after `aim` export.

## The principle (retained)

Do NOT start from "make a poster." Start from: **who is the audience**, what is the ONE
**message** that changes their mind, THEN pick the **format** that lands it.

## Process (file-backed)

1. **Read** THIS DDD's ② `PRODUCT.md` (Vision, What Makes It Different, Audience Map,
   Success Criteria) — the raw material. Don't invent claims not grounded in ② Knowledge.
2. **Audience** — one specific segment from the Audience Map. Never "users".
3. **Message** — one sentence: the value claim that changes THAT audience's mind, backed
   by a proof point cited from ② Knowledge.
4. **Format** — choose the format that fits audience+channel; justify why (one-pager /
   thread / README section / video script / deck outline).
5. **Draft + store** under `<ddd>/.artifacts/pollinate/<slug>/`: `message.md` (audience +
   claim + cited proof) then the format artifact.
6. **Deliver boundary** — emit a portable package; the human/runtime publishes it. NEVER
   auto-post to an external channel.

## Anti-slop quality bar (BLOCKING)

Before delivering, ALL three or it's not done:
- **Specific**: a concrete value claim, not a generic benefit.
- **Grounded**: the proof point is traceable to ② Knowledge — cite the file/section.
- **Justified format**: the format choice names the audience reason, not a default.

A structurally-complete but factually-empty output (Lorem-ipsum-shaped) FAILS.

## Why portable

No brand dir, no channel adapters — a DDD carries the message-first *method* and sources
value from the docs it owns. That is what lets it express value on Kiro / Claude Code
after export, without SwarmAI's media backend.
