---
title: Turn attention tiers (what in a chat turn gets the user's attention, decided in one place)
date: 2026-10-25
created: 2026-10-25
updated: 2026-10-25
status: draft v2, after one fresh-context review (v1 verdict "needs redesign"); for Gawan's review
---

# Turn attention tiers

Governs how loudly each part of a chat turn is drawn. Files: `desktop/src/pages/chat/turnZones.ts`,
`components/TurnZones.tsx`, `TurnCards.tsx`, `AssistantMessageView.tsx`, `ChangesCard.tsx`,
`EscalationBlock.tsx`, `InlinePermissionRequest.tsx`, `StageDocs.tsx`, `CompactNotice.tsx`,
`NextSteps.tsx`, `AssistantHeader.tsx`, `components/chat/ChatErrorMessage.tsx`,
`components/common/AskUserQuestion.tsx`, and one backend observer. Display spec:
`Knowledge/Designs/2026-10-19-chat-turn-display-spec.md` (v6); §7 lists which v6 lines change.

## 1. The problem

Gawan, 2026-10-25: only what the user cares about gets colour; agent chatter must not take
attention; key outputs (what was committed) must show where they happen; a general scheme
covering every place, not one more row; no regressions.

Nothing decides attention today. Each component picks its own weight and colour: 63 Tailwind
palette classes in 7 turn files alone (counted 2026-10-25), plus hard-coded colours in the
renderers they import (`ChatErrorMessage`, `AskUserQuestion`, `AssistantHeader`), status
variables and inline styles. The same meaning gets different colours ("Needs you" is
`orange-500` on a failed call, the primary colour elsewhere). A note between two WORKED
groups is drawn like the answer. A commit landed mid-turn leaves no mark until the end card.
Showing every call's outcome is not the fix: most outcomes are the last line of a `sed` or
`grep` (`320-}`, `urlparse:`, session `db83772e`).

## 2. Two axes, one policy

v1 used one axis and could not say "a process row that is running" or "a landed fact that is
an error" without exceptions. v2 separates them:

- **attention** (how loud): `act` | `know` | `process`.
  - `act`: the user must do something now. Card, primary-colour label, buttons. Never folds.
  - `know`: a result the user should know. Normal text weight. Never folds.
  - `process`: how the agent got there. Secondary or dim text. Folds (v6 rules).
- **tone** (what a colour means, only from a record or a live state the app owns):
  `neutral` | `action` | `success` | `error` | `running` | `agent` | `diffAdd` | `diffDelete`.

One file, `desktop/src/pages/chat/attention.ts`, holds the policy: for every part (§3) its
attention, and its tone as a function of that part's own recorded state. It also holds the
allowed pairs, enforced by the type:

| attention | allowed tones |
|---|---|
| act | action, error |
| know | neutral, success, error, diffAdd, diffDelete |
| process | neutral, running, agent |

So `process` + `success` (a green test line guessed from output) cannot be written.
`running` (amber on the call running now) and `agent` (the Agents panel tint) are v6 §3.1 /
§3.2 textures and stay as they are, now named.

Tokens, defined once in `index.css`: `--attn-tone-<tone>` (text) and `--attn-tone-<tone>-tint`
(`color-mix(<tone> 8%, transparent)`), mapped onto the existing theme variables
(`--color-primary`, `--color-status-success/error`, the current amber and purple). Plus
`.attn-act`, `.attn-know`, `.attn-process` for weight and text colour. Components ask the
policy and use its class; they do not pick colours.

## 3. Every part, with its attention and tone

The boundary is everything `AssistantMessageView` renders for one assistant message, the
renderers it imports included. Rows `TabView` draws between messages (compaction divider,
reconnect, turn-phase line, busy-wait, back-to-latest) are outside the turn; they are listed
in §3.3 so they are not forgotten, and are out of scope.

### 3.1 Zones (`Zone['kind']` in `turnZones.ts`, ten kinds)

| Part | attention | tone | Change |
|---|---|---|---|
| `needs`: question / permission / escalation, **pending** | act | action | colours from tokens |
| `needs`, **answered / decided / resolved** | process | neutral | one-line receipt, secondary text (v6 §4 already folds it to one line) |
| `failure`: a call that failed and ended the turn | act | error | `orange-500` → tokens |
| `text` answer | know | neutral | none (no RESULT label exists, and none is added: a test asserts it, and colour by position would guess success) |
| `text` restatement | process | neutral | secondary text |
| `text` note (between groups) | process | neutral | **body colour → secondary** |
| `thinking` / thought rows | process | neutral | **the thought being written shows its first line; open it for all** |
| `phase` (WORKING / WORKED) | process | neutral | — |
| tool strip | process | running while running, else neutral | ▸ when it has detail; the detail stays the persisted outcome |
| Agents panel (inside a phase or stage) | process | agent | — |
| `pipelineStart` | process | neutral | — |
| `stage` group | process | running while running, else neutral | — |
| `stageResult` (summary + docs) | know | neutral | — |
| `gate` PASS | know | neutral | — |
| `gate` BLOCK | know | error | a BLOCK that stops the run is also an `act` card already |
| `stopped` | know | neutral | — |
| **landing row (new zone kind `landing`)** | know | success, or error when its record says it is not on the destination (`state: off`) | new, §4 |

### 3.2 Parts `AssistantMessageView` adds around the zones

| Part | attention | tone |
|---|---|---|
| Background-finished card | know | neutral |
| `RunResult` | know | success / error from the run's recorded verdict, else neutral |
| `AttentionCard` (pipeline needs-you) | act | action |
| Output card: files, deliverables, landings | know | diffAdd / diffDelete on counts; success / error on a landing's recorded state |
| Assumptions disclosure | process | neutral |
| Terminal error (`ChatErrorMessage`), with Retry | act | error |
| Compaction failure that offers a retry | act | error; one that offers nothing: know, error |
| Next-step chips | know | neutral |
| Assistant header, avatar, action buttons, bee | process | neutral; the bee and avatar are brand artwork, exempt from the guard by name |

### 3.3 Outside the turn (out of scope, listed)

`TabView`: compaction divider, reconnect / resume, turn-phase line, busy-wait, background-agent
row, back-to-latest. Tab notice bar, toasts, Canvas.

### 3.4 How completeness is checked

- Zones: `type ZoneKind = Zone['kind']`; the policy is `Record<ZoneKind, …>`, so adding a zone
  kind without a policy entry is a type error.
- Parts of §3.2: each first-level part of a turn renders inside `<TurnPart part="…">`, which
  reads the policy and sets `data-attn` / `data-tone`. Test: render a fixture that holds every
  part of §3.1 and §3.2 (one turn of each shape) and assert every first-level child of the turn
  carries `data-attn`. A new part that bypasses `TurnPart` fails it.

## 4. Mid-turn landings (the only backend change)

### 4.1 Trigger and read

- Trigger: `_turn_lands`, the per-turn counter `run_owner_gate` already increments when it
  allows a `session_worktree land` that was handed the turn's id (`run_owner_gate.py` ~548;
  reset at turn start, `session_unit.py` ~1442). No command parsing, no output parsing: the
  gate already recognised the land, a land through a variable included.
- When a ToolResult arrives and `_turn_lands` is higher than the count this turn has already
  read, the orchestrator calls `session_landings.read_session(session_id, turn_id=…,
  deadline=Deadline(…))` in the **`turn_changes` pool** (it runs several git commands; the
  end card uses the same pool), with a short deadline and an outer wait bound. Both
  ToolResult delivery paths are handled, the way `_pipeline_stage_events` is
  (`streaming_orchestrator.py` ~2106, ~2528).
- Each landing in the result not yet emitted this turn becomes one `turn_landing` block,
  carrying that landing as `read_session` returns it (`repo, gitRoot, dest, branch, onto, new,
  count, subjects, state`) plus `at`. Dedupe key: `(gitRoot, dest, new)`, held on the
  `SessionUnit` and reset with the turn's state, so retries and question / permission
  continuations of the same turn share it.
- Nothing stamped → nothing shown (same rule as the end card). A refused or up-to-date land
  is not counted by the gate's land outcome or has no stamp, so it shows nothing. Two sessions
  landing in one repo: git's ref lock serialises them; each read filters by its own
  session/turn stamp.
- The block is persisted before it is forwarded, like `pipeline_stage`
  (`session_router.py` ~2524), so live == reload. It is added to `ContentBlock`,
  `DISPLAY_TYPES` and `deriveTurnView` (frontend) and to `context_injector`'s
  non-conversational list (backend).

Cost: nothing on send or per token. The same git reads the end card makes, once per land, in
the pool the end card already uses.

### 4.2 Shown once

The landing row shows the commits: `⎇ main · <sha> <subject>` per commit (up to the
reader's subject cap, then "N more commits"). The end card's landing section then shows, for a
landing already shown as a row in this turn, only its files and a "landed above" link to the
row, not the subjects again. Which landings were shown is read from the turn's own
`turn_landing` blocks (same `new`), not guessed.

## 5. Not recognised, on purpose (never guess)

- Test or build pass / fail (would mean parsing tool output). A failure the agent cannot
  handle already reaches the user as an `act` card.
- Commits that were not landed (turn-result-card §1).
- A call's outcome line is never promoted out of the strip's detail.
- Raw tool output: `content` is live only and not persisted (`types/index.ts` ~242), so the
  strip detail stays the persisted outcome. Showing more would need its own persisted, bounded,
  redacted field and is not part of this design.

## 6. Guard

`turnAttention.test.ts` (the `navVisualSystem.test.ts` pattern) scans every file of the turn
render closure (§3.1 and §3.2 files, listed in the test) for: Tailwind palette colour
classes, raw hex / rgb / hsl, inline `color` / `background` / `border` styles, and
`--color-status-*` / `--color-primary` used outside the tier tokens. Per-file counts must equal
`KNOWN`; step 1 records today's real counts, each migration step lowers its entry, the end
state is `{}` apart from the named brand-artwork exemptions.

## 7. Spec v6 changes

- §0.6 adds: "colour carries a tone read from a record or a live state the app owns; only
  `act` uses the primary colour."
- §0.8 / §2.3: the thought being written shows its first line (it only grows, so the stream
  stays monotonic); open it for the full text. Tests that assert a full live thought
  (`AssistantMessageView.zones.test.tsx` AC1 / AC2) change, listed in that commit.
- §2.4: a note between groups is secondary colour.
- §3.1: a strip with detail shows ▸.
- §4: an answered card is a process receipt.
- §1 table: new row "landing, at the call that landed"; §8: the end card does not repeat a
  landing's subjects shown above.

## 8. Regression control

1. Acceptance tests first, red on today's code by assertion, each fix mutation-checked:
   - backend: a counted land yields one `turn_landing` block from the stamp; a second read in
     the turn emits nothing; an unstamped or refused land emits nothing; a continuation of the
     turn does not re-emit; the read runs in `turn_changes`, never on the event loop.
   - frontend: §3.4 completeness (type + runtime fixture); a `landing` zone renders at its
     call's place, live == reload, in the stream-order, replay and column-grid suites; the
     end card does not repeat a shown landing's subjects; the live thought shows its first
     line and opens to full; a note's colour differs from the answer's; the §6 guard.
2. Existing suites green. A test changes only where §7 changes the rule it asserts, each
   listed in the commit message: today `src/pages/chat` (74 files, 868 tests),
   `test_pipeline_stage_events.py`, `test_session_landings.py`, `test_turn_changes.py`,
   `test_context_injector*.py`.
3. Light, dark and both locales: the pilot (step 2) is checked by Gawan in light and dark;
   new strings exist in `en.json` and `zh.json` (the i18n parity test covers it).
4. Data is not touched. Presentation changes for every turn, old ones included (they re-render
   with the new look); old turns have no `turn_landing` blocks and show no landing rows.

## 9. Steps (one ff-merged commit each, own worktree, no push, no build)

1. `attention.ts` policy + tokens + `TurnPart` + guard at today's counts. No visual change.
2. Pilot: note → secondary, live thought → first line, strip ▸, answered card → receipt.
   Stop for Gawan's light/dark check.
3. `turn_landing`: backend observer, block, frontend `landing` zone, end-card "shown above".
4. Migrate every colour in the closure to tokens, file by file, `KNOWN` → `{}`.
5. Spec v6 edits of §7; status → implemented.

## 10. Review history

- v1 (2026-10-25) → fresh-context review: needs redesign. Taken: two axes instead of one;
  boundary and completeness check over every rendered part, not a hand list; stateful tiers
  (answered card, compaction failure by action); guard over the whole render closure and all
  colour forms; trigger by `_turn_lands` + `read_session` (v1 called `read_repo` with the wrong
  shape) in the `turn_changes` pool; dedupe by `(gitRoot, dest, new)` on the `SessionUnit`;
  strip detail stays the persisted outcome (raw content is live only; v1 broke live == reload);
  no RESULT label and no positional colour (v1 assumed a label that does not exist); first
  line, not latest line, for the live thought (monotonic); end card does not repeat subjects;
  "old turns look as today" corrected.
