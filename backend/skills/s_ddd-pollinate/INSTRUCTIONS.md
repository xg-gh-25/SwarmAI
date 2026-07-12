# s_ddd-pollinate — Full Protocol (file-state, portable)

Express a DDD's product value to an audience. **Message first, format follows.**
Portable: sources everything from the DDD's own ② `PRODUCT.md` + file-state, no
SwarmAI media backend.

## Locate the DDD

The DDD is the directory with `AGENTS.md` + `PRODUCT/TECH/IMPROVEMENT/PROJECT.md`.
Output lands in `<ddd>/.artifacts/pollinate/<slug>/`.

## Protocol

1. **Read** `<ddd>/PRODUCT.md` — Vision, What Makes It Different, Target Users /
   Audience Map, Success Criteria. This is the raw material; do not invent value
   claims that aren't grounded in ② Knowledge.
2. **Audience** — pick ONE specific segment from the Audience Map. Never "users".
3. **Message** — one sentence: the single value claim that changes THAT audience's
   mind, backed by a proof point cited from ② Knowledge (`PRODUCT.md`/`IMPROVEMENT.md`).
4. **Format** — choose the format that lands the message for that audience+channel;
   write down WHY (one-pager / thread / README section / video script / deck outline).
5. **Draft + store**:
   - `message.md` — audience + one-sentence claim + cited proof point
   - `<format>.md` — the artifact (e.g. `onepager.md`, `thread.md`)
6. **Deliver boundary** — emit a portable package. The human/runtime publishes it.
   NEVER auto-post to an external channel (STEERING external-action rule).

## Anti-slop quality bar (BLOCKING)

Before delivering, the output MUST pass all three or it is not done:
- **Specific**: the message is a concrete value claim, not a generic benefit
  ("cuts context re-establishment 10×" not "improves productivity").
- **Grounded**: the proof point is traceable to the DDD's ② Knowledge — cite the
  file/section. An uncited claim is slop.
- **Justified format**: the format choice names the audience reason, not a default.

A structurally-complete but factually-empty output (Lorem-ipsum-shaped) FAILS —
same garbage-in filter the pipeline's AC quality gate applies.

## Why portable

No brand/ dir, no channel adapters, no scripts — a single DDD carries the
message-first *method* and sources its value from the docs it already owns. That is
what lets it express value on Kiro / Claude Code / an AIM package, without SwarmAI.
