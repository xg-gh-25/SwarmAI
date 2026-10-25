---
title: Turn attention tiers (what in a chat turn gets the user's attention, decided in one place)
date: 2026-10-25
created: 2026-10-25
updated: 2026-10-25
status: v5 accepted; step 1 (pilot) implemented `e391955ce`, awaiting Gawan's light/dark check; steps 2-4 not started
---

# Turn attention tiers

Governs how loudly each part of a chat turn is drawn. Display spec:
`Knowledge/Designs/2026-10-19-chat-turn-display-spec.md` (v6); §7 lists the v6 lines that change.

## 1. Why v1-v3 kept failing

Three reviews each found new blockers. They came from two root errors in the design, not
from missing details.

**R1. "Attention" was treated as "recolour every component".** v1-v3 planned to move every
colour in 13 components into a new tone system. That forced an import-closure guard, a tone
policy for every nested sub-part, an "is it actionable" rule across components, and each
review found another part the policy did not reach. But the audit of today's colours (§5)
shows they already mean something specific (diff counts, a card's own state or verdict, the
agent texture, the call running now, links and code in markdown); two break a v6 rule (§5). The
problems Gawan saw are elsewhere: agent chatter is drawn as loud as the answer, and a landed
commit leaves no mark in the turn. Recolouring was not needed for either.

**R2. A second, live source for landings.** v1-v3 added a mid-turn observer that read the
landings again while the turn ran. A second reader of the same fact brought every later
blocker: counting before the command ran, races between parallel calls, attribution,
dedupe, a new block type, live vs reload. The fact already exists exactly once: the
`turn_changes` block's `landed` list, read at turn end from git's stamp, persisted, the same
live and on reload. What it lacks is only **which call in the turn did the landing**.

v4 drops both: no colour migration, no second reader.

## 2. The rule

Every part of a turn has one attention level:

| Level | Meaning | Look |
|---|---|---|
| act | the user must do something | a card with controls (as today) |
| know | a result the user should know | normal text, never folds; a landed fact gets a faint success tint |
| process | how the agent got there | secondary / dim text, folds; no colour except the two v6 textures (§5) |

## 3. Attention is decided in the model, once

- `turnZones.ts` gets `ATTENTION: Record<Zone['kind'], Attention | ((z) => Attention)>`. A
  `Record` over the kind union: a new zone kind without an entry is a type error.
- Each zone's attention goes on the host node that already exists for it, as `data-attn`:
  `<div key={z.id} data-zone={z.kind}>` for top-level zones (`TurnZones.tsx` ~991) and the
  host `BodyZone` renders for a zone inside a stage (`TurnZones.tsx` ~894). No node is added,
  moved or re-keyed.
- One CSS rule in `index.css`: `[data-prose="process"] { --color-text: var(--color-text-secondary); }`.
  `data-prose="process"` is set on the agent's process prose wherever it renders: a process
  `text` zone's host, a note inside a phase (`PhaseNote`), a note inside a stage (`BodyZone`).
  (As built in step 1, `e391955ce`: v5 keyed the rule on text-zone hosts only, which missed the
  notes inside phases and stages.)
  Markdown sets every prose element to `text-[var(--color-text)]`
  (`MarkdownRenderer.tsx` ~909-1048, `index.css` ~1708), so overriding the variable on the host
  dims paragraphs, headings, lists and bold together, with no change to `MarkdownRenderer`.
  Theme values are literal colours (`index.css` ~12, ~123), so there is no variable cycle.
  Links and inline code keep their own colours. Only `text` zones get the rule: a `phase` or
  `stage` can hold cards and strips, which must not be dimmed.
- A stage's own summary line (`TurnZones.tsx` ~877-888) is a `know` slot of the stage; the
  stage's work is process. Its nested zones carry their own `data-attn`.

| Zone kind | Attention |
|---|---|
| `needs` | act while `isUnanswered(z.block)` (the predicate `hasOpenQuestion` already uses, `turnZones.ts` ~565); process once answered |
| `failure` | know: a "Failed" label in secondary text, no colour (it reports; it has no control; v6 §0.6 keeps red for what needs the user) |
| `text` answer | know |
| `text` restatement, `text` note | process |
| `thinking`, `phase`, `stage`, `pipelineStart` | process |
| `stageResult`, `gate`, `stopped`, `landing` (new, §4) | know |

Parts outside the zone list are fixed and already distinct cards; they keep their look:
background-finished (know), `RunResult` (know), `AttentionCard` (act), output card (know),
assumptions (process), terminal error (act when it renders Retry, else know), next steps
(know), header / actions (process). A test lists these by testid so a new part has to be
added to the list. A zone kind's tier is a function of the zone, not of its kind alone, where
the zone has state (`needs`) or parts (`stage`).

## 4. Where a landing happened: one source

### 4.1 Backend: the stamp names its call

- `run_owner_gate` already prefixes a main-agent `session_worktree land` with
  `SWARM_APP_SESSION_ID=… SWARM_TURN_ID=…` (`_session_worktree_rewrite`,
  `run_owner_gate.py` ~463). It adds `SWARM_TOOL_USE_ID=<tool_use_id>` the same way, under the
  same conditions (main agent, valid turn id). The gate already receives `tool_use_id`
  (`run_owner_gate.py` ~523).
- `session_worktree land` writes it into the stamp: `swarm-land <branch> session=<s>
  turn=<t> tool=<id> onto=<sha>`, after validating it (`^[A-Za-z0-9_]{1,64}$`, the shape of
  an SDK tool id such as `toolu_01…`; invalid or absent → `tool=` is left out, so a stamp can
  never carry a space or `=` from it).
- `session_landings._STAMP` accepts an optional `tool=<id>` field. A stamp without it (every
  stamp written before this change) parses exactly as today. Each returned landing gains
  `toolUseId` when its stamp has one.
- `turn_changes` passes it through; `TurnLanding` (`types/index.ts` ~506) gains optional
  `toolUseId`. Nothing else in the read changes: same reader, same pool, same time, same block.

### 4.2 Frontend: the row is placed at the call's group

- One pure function, `placeLandings(zones, changes, hold)`, returns `{ zones, shown }`:
  the zone list with `landing` zones inserted, and the set of `(gitRoot, dest, new)` keys it
  placed. The zone list and the end card both take it from this one call, so they cannot
  disagree. It places nothing while `hold` is true: `turn_changes` arrives as an assistant
  event just before `result`, while the turn is still streaming
  (`session_router.py` ~2558; `useChatStreamingLifecycle.ts` ~3180 vs ~3252), and inserting
  then would push later zones down mid-stream (v6 §0.8). With `hold` false, the rows appear in
  the same ending frame as the folded groups and the output card. On reload `hold` is false
  from the first render, so the rows are there from the first frame.
- For each `changes.landed[i]` with a `toolUseId`, it finds the top-level zone (a `phase`, or a
  `stage`) that contains that call and inserts a `landing` zone right after it (several
  landings after one group keep their reflog order). The phase is not split, so v6 §2.3's boundaries and every existing zone's key stay
  as they are.
- The row: `⎇ <dest> · <sha> <subject>` per commit (the subjects the reader already returns;
  "N more commits" beyond them), a faint success tint and left bar; `state: off` (the
  destination was rewound since) uses the error tint. Click opens the landing in Canvas.
- No `toolUseId`, or a call id not in this turn (sub-agent lands, which carry no turn id by
  design; turns before this change): no row. It shows only on the end card, as today.
- End card: for a key in `shown`, the landing lists its files only and "↑ landed above"; its
  subjects are not repeated (v6 §0.9). `ChangesCard` receives `shown`; it does not match ids
  itself.

### 4.3 Timing (a deliberate trade-off)

The landing row appears at its place in the turn's ending frame, not the moment the merge
happens. The ending frame is the one place v6 allows the turn to change shape (groups fold,
the output card appears); the landing row is one more change in that frame, above the
RESULT. During a long turn the user sees the
land call's strip, not yet the row. This keeps one source, live == reload, and no new
mid-turn machinery. Showing it the moment it lands would need the mid-turn reader v1-v3
could not make safe; it is not part of this design.

## 5. Colour audit (the whole list, once)

Today's colours in the turn's components (2026-10-25), by meaning:

| Colour | Where | Verdict |
|---|---|---|
| amber on the running strip / stage | `TurnZones` ~369 | keep: v6 §3.1 "running" texture |
| purple Agents panel, background-finished card | `TurnZones` ~540-616, `AssistantMessageView` ~106-112 | keep: v6 §3.2 agent texture |
| green / red diff counts and bars | `ChangesCard` ~37-72 | keep: know, the numbers themselves |
| red BLOCK, amber "Not ready to push", amber gate BLOCK, amber RunResult verdict | `TurnCards` ~59-98, ~239; `TurnZones` ~848 | keep: know, the run's recorded verdict |
| links (primary) and inline code in markdown | `MarkdownRenderer` ~833, ~975 | keep: content, not attention |
| avatar (bronze) and bee | `AssistantMessageView` ~545, `StreamingBee` | keep: brand artwork |
| questions / permissions / escalation colours | `AskUserQuestion`, `InlinePermissionRequest`, `EscalationBlock` | keep: act cards and their own states |
| error / warning tones, Retry countdown | `ChatErrorMessage` | keep: act / know error card |
| green / red memory-save icon, action note | `AssistantMessageView` ~598-677 | keep: the action's recorded result |
| primary focus rings | many | keep: accessibility, not attention |
| **blue pulsing dot in the header while streaming** | `AssistantHeader.tsx` ~56 | **remove**: v6 §0.7 allows one "is running" signal, the bee (spinners on action buttons after the turn are not a running signal) |
| **orange "Needs you" on a failed call** | `AssistantMessageView.tsx` ~428 | **change** to "Failed", secondary text, no colour: nothing for the user to do on it |

Scope of this audit: the components that render inside one assistant message
(`AssistantMessageView` and what it renders), light and dark. Rows `TabView` draws between
messages are not in it.

Guard: `turnAttention.test.ts` renders a fixture turn holding every zone kind (top-level and
inside a stage) and asserts on the DOM, not on source text: (a) every zone host carries
`data-attn`; (b) inside every `[data-attn="process"]` host, no element carries a colour class
other than the amber running texture, the purple agent texture, primary focus rings, and
markdown links / code; (c) a process text zone's host sets `--color-text` to the secondary
colour and the answer's does not. No closure scan, no colour migration.

## 6. The process-tier changes (the pilot)

- note and restatement text → secondary colour (via `data-attn`).
- a thought inside a phase shows one line, while written and after: the first line of its
  newest paragraph. Two thinking blocks in a row are joined into one passage
  (`turnView.ts` ~575), so "its first line" would hide the newest block, the step arriving now
  (found by the stream-order property test while building step 1). Open it for the full text. Today it is shown in full while written
  (`TurnZones.tsx` ~265-267); this changes that. The first THINKING passage stays full (v6 §2.1).
- a strip with detail shows ▸; the detail stays the persisted outcome (raw output is live only).

## 7. Spec v6 changes

- §2.4: a note between groups is secondary colour.
- §2.3: a thought being written inside a phase shows its first line.
- §3.1: a strip with detail shows ▸.
- §1 table + §8: a landing row after the group that holds the call; the end card does not
  repeat its subjects.
- §4: a failed call with nothing to decide is "Failed" in secondary text, not "Needs you".
- §0.8: the ending frame may also insert landing rows above RESULT.
- §0.7: the header's streaming dot is removed (the bee is the signal).
- `docs/design/2026-10-24-turn-column-grid.md`: the landing row is a text-column row (its
  marks hang in the gutter like a note's).

## 8. Regression control

1. Red tests first, by assertion, each fix mutation-checked:
   - backend: a land with `SWARM_TOOL_USE_ID` stamps `tool=`; the reader returns `toolUseId`;
     a stamp without `tool=` parses as before (existing stamp fixtures unchanged); an invalid
     id is left out; the gate adds the variable only where it adds the turn id (sub-agent:
     neither).
   - frontend: every zone has `data-attn`; a note's colour differs from the answer's; the
     landing row sits right after the group holding its call, in the stream-order, replay and
     column-grid suites, and existing zone keys are unchanged; no row without `toolUseId`; the
     end card omits subjects shown above (one `shown` set for both); no landing row while
     `hold` is true, rows in the ending frame, present from the first frame on reload; unmatched
     ids, several landings after one group, a call inside a stage; in-phase live thought first
     line; strip ▸; no blue dot; "Failed" without colour; an answered card is process; markdown
     paragraph / heading / list / bold dimmed in a note, not in the answer, light and dark.
   - `liveReload.property.test.ts` gains a `turn_changes` with a landed `toolUseId`, and its
     reducer compares placement and the `shown` set, not only the block type.
   - the replay tracker (`AssistantMessageView.replay.test.tsx` ~45-63) also tracks landing
     nodes and checks existing siblings keep identity and order across the ending frame.
2. Existing suites green; a test changes only where §7 changes its rule, listed in the
   commit: `AssistantMessageView.zones.test.tsx` AC2 (in-phase live thought), the
   `needs-failure` label assertions, any header-dot assertion, `turnGrid.test.tsx` row-kind
   list (~59-85, gains `landing`). Suites: `src/pages/chat`,
   `test_session_landings.py`, `test_session_worktree*.py`, `test_turn_changes.py`,
   `test_run_owner_gate*.py`.
3. Gawan checks the pilot in light and dark before step 3. New strings in `en.json` and `zh.json`.
4. Data is not touched. Old turns re-render with the new text weights; they have no
   `toolUseId`, so no landing rows.

## 9. Steps (one ff-merged commit each, own worktree, no push, no build)

1. `ATTENTION` + `data-attn` + the process CSS rule + §6 pilot + guard. Stop for Gawan's check.
2. §5 fixes: header dot, "Failed" label.
3. Landing placement: gate variable, stamp field, reader field, `landing` zone, end card.
4. Spec v6 edits; status → implemented.

## 10. Review history

- v4 → fresh-context review: backend half sound (the gate's `tool_use_id` is the stored block's
  id; an optional `tool=` keeps old stamps parsing; no charset issue). Frontend seam fixed in
  v5: the variable override instead of colour inheritance (markdown sets its own colour); no
  landing rows while `hold` (no mid-stream insertion); one `placeLandings` result for zones and
  end card; `needs` tiered by `isUnanswered`, stage summary a know slot, nested zones
  annotated; "Failed" without colour; audit completed and scoped; DOM-based guard;
  column-grid, live/reload and replay coverage added.

### v1-v3 findings

Findings tied to the removed mechanisms no longer apply: the mid-turn observer (counter
race, parallel calls, `read_session` shape, pool, dedupe key, continuation lifetime, new
block type, live/reload of that block), the colour migration (closure guard, nested tone
policy, `Decision` pair types, `color-mix` syntax, actionability across components, barrel
imports). Findings still in force and answered here: first THINKING stays full; raw
output not shown; no RESULT label or positional colour; answered cards keep their content;
no new wrapper nodes; end card does not repeat subjects; affected tests listed; old turns
re-render. Left as it is today, stated: sub-agent lands carry no turn id, so they get no row
(v3 review asked for it; it needs a turn attribution for agents that can outlive the turn,
which turn-result-card left out on purpose).
