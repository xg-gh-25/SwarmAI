---
title: One column grid for a chat turn (position by row kind, never by nesting depth)
date: 2026-10-24
created: 2026-10-24
updated: 2026-10-24
status: accepted (after one independent review)
---

# One column grid for a chat turn

Governs `desktop/src/pages/chat/components/TurnZones.tsx` (and the turn wrappers in
`AssistantMessageView.tsx`). Display spec: `Knowledge/Designs/2026-10-19-chat-turn-display-spec.md`
§9.4 (alignment) and §9.5 (at most two levels: turn → group → strip).

## Problem (measured)

Every group body adds `ml-[22px]` to whatever it contains, so a row's x depends on how
deep it is nested, not on what it is. Measured with the class-path helper
(`__tests__/helpers/columnOffset.ts`) on today's main:

| Row kind | In a group body | Under a note in a group (the work running now) | Under a note in a pipeline stage |
|---|---|---|---|
| strip / thought row (box) | 22 | 44 | 44 |
| Agents panel | 22 | 44 | 44 |
| opened thought text | 43 | 65 | 65 |
| note / label / RESULT text | 22 | 22 | 22 |

While a turn streams, the newest note's segment is open (depth 2) and earlier ones fold,
so the work being written sits 22px right of the work already shown; the user sees the
live part out of line with the finished part (2026-10-24 screenshot). The segment's own
indent is also a third level, against §9.5. The 2026-10-24 RESULT fix (`bddc94681`)
aligned prose only; work rows were left on the per-depth scheme.

## Rule

A row's x is set by its KIND. Nesting never moves anything sideways; the fold (▸) and
the vertical order carry the structure.

| Column | x | Rows |
|---|---|---|
| gutter | 0 (16 wide) | every mark: ▸ of a group / note / stop receipt, stage ✓ ●, gate ✓ ◦, a direct run's stage-result ✓ |
| text | 22 | group / stage / gate labels, notes (first level and in a stage), restatement, answer, RESULT, stopped line, stop receipt, stage-result name |
| work | 22 (box) | strips, thought rows, Agents panel, a stage's result summary |
| detail | 43 (its column + `DETAIL_INSET`) | a row's own opened body: strip detail, thought text, stop receipt detail |
| card | 0, full width | needs-you / failure / question / permission cards (first level AND inside a stage), the pipeline divider, the bee, background-finished card, output card, run result, next steps |

Inside the Agents panel (itself a work row) its rows keep their own internal layout:
the agent's mark column, and `→ tool` / opened detail aligned with the agent's
description (`ml-6` = mark 16 + gap 8). They do not vary with nesting.

## Mechanism (one place adds space)

- `turnGrid` constants in `TurnZones.tsx`: `GROUP_BODY` (the only class that moves a
  group's content to the 22 column: thinking, phase, stage, stage-result bodies),
  `TEXT_COL` (a first-level text zone), `HANG` (a row inside a body that puts its mark in
  the gutter), `DETAIL_INSET` (a row's own detail).
- Nothing inside a group body adds left space for its children: the note segment body and
  a stage's nested phase render at the body's column. A card or a stage-result row inside
  a stage body backs out with `HANG` to the first-level columns (`BodyZone`).
- The stopped line and the stop receipt sit in the text column; the receipt's ▸ moves to
  the gutter like every other mark (`HANG` on a block wrapper, so the full-width button
  spans the whole row); its detail uses `DETAIL_INSET` like every other detail.

## Guards

1. Grid test (`__tests__/turnGrid.test.tsx`, 9 cases): a live chat turn (open segment,
   open thought, Agents panel), the same turn settled and opened by the user, a live
   pipeline stage with notes, a stopped turn with its receipt opened, a pipeline with a
   gate, the divider and a question card at the first level and inside a stage, a direct
   run's stage-result row, opened strip details, the bee. Every row of a kind has ONE x
   (the table above); live and settled positions are equal.
2. Source guard: in `TurnZones.tsx` the only arbitrary-px left offsets are the three grid
   constants; no inline left offset in a `style`; the remaining scale-unit insets are an
   explicit list (all row-internal).
3. Limits of the measuring helper (`helpers/columnOffset.ts`): it reads margin, padding,
   1px borders, gaps and fixed widths from class names. It cannot see stylesheet rules,
   transforms, positioned offsets or grid layouts; the source guard covers the inline
   ones, and a row kind not in the grid test is not measured at all, so a new row kind
   must be added to it.

## Not changed

Vertical spacing (§9.3), row internals (tool-name / object / time columns), cards.
