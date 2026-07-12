---
name: ddd-pollinate
description: "DDD-native value-expression engine — a portable, decoupled rewrite of s_pollinate that lets a SINGLE DDD express its product's value to audiences (message-first, format follows) using file-based state + the DDD's own ② Knowledge, with no SwarmAI backend coupling. Section ④ of a DDD.\n  TRIGGER: \"ddd pollinate\", \"express this DDD's value\", \"make content from this DDD\".\n  NOT FOR: SwarmAI's full media engine (use s_pollinate), one-off images (use image-gen)."
tier: lazy
---
# DDD-Native Pollinate (s_ddd-pollinate)

The **value-expression** capability a DDD carries: turn what the product IS (its ②
Knowledge — vision, differentiation, proof) into audience-shaped messages. The
DDD-native counterpart to `s_pollinate`: **message-first, format follows audience**,
minus SwarmAI's media backend.

> **Why a separate skill, not a copy.** `s_pollinate` is a 9-stage engine bound to
> SwarmAI's brand assets, phoneme/prefs schemas, channel adapters, and scripts dir.
> A DDD shipping to Kiro / Claude Code / an AIM package has none of that. `s_ddd-pollinate`
> keeps the *principle* (audience → message → format) and sources everything from the
> DDD's own docs + file-state.

## The decoupling (vs s_pollinate)

| Concern | s_pollinate (SwarmAI-bound) | s_ddd-pollinate (portable) |
|---------|------------------------------|----------------------------|
| Source of truth | brand/ + channels/ + prefs_schema.json | THIS DDD's ② `PRODUCT.md` (vision, differentiation, audience map) |
| State | SwarmAI scripts + backend | plain files in `<ddd>/.artifacts/pollinate/<slug>/` |
| Channel adapters | SwarmAI channels/ | the output is a **portable package** the human/runtime delivers |
| Principle | message-first, format follows | **identical — retained** |

## The principle (retained)

**Message first, format follows the audience.** Do NOT start from "make a poster."
Start from: who is the audience, what is the ONE message that changes their mind,
THEN pick the format that lands it (one-pager / thread / README section / short video
script / deck outline).

## Process (file-backed)

1. **Read** THIS DDD's ② `PRODUCT.md` (Vision, What Makes It Different, Target Users /
   Audience Map, Success Criteria) — the raw material for the message.
2. **Audience** — name the specific segment (from PRODUCT.md Audience Map). Not "users."
3. **Message** — the single value claim for that audience, in one sentence, backed by
   a proof point from ② Knowledge (not a generic benefit).
4. **Format** — choose the format that fits the audience + channel; justify why.
5. **Draft** — write it. Store under `<ddd>/.artifacts/pollinate/<slug>/`:
   `message.md` (audience + claim + proof), then the format artifact (`onepager.md`,
   `thread.md`, `readme-section.md`, `video-script.md`, `deck-outline.md`).
6. **Deliver boundary** — output a portable package; the human/runtime publishes it.
   Never auto-post to an external channel (mirrors s_pollinate + STEERING external rule).

## Quality bar (the anti-slop gate)

A DDD-pollinate output must pass: (a) the message is a SPECIFIC value claim, not a
generic benefit; (b) the proof point is traceable to the DDD's ② Knowledge (cite it);
(c) the format is justified by the audience, not defaulted. A "structurally correct but
factually empty" output (Lorem-ipsum-shaped) FAILS — same garbage-in filter the
pipeline's AC quality gate uses.

## Status

🆕 **Portability seed (2026-07-12).** Ships the decoupled message-first contract +
PRODUCT.md-sourced value extraction + the anti-slop quality bar. The full multi-format
generation engine iterates per spec §7 — this establishes the portable shape so a DDD
can express its own value on any runtime, without SwarmAI's media backend.
