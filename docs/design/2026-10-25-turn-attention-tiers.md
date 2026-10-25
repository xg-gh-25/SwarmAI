---
title: Turn attention tiers (what in a chat turn gets the user's attention, decided in one place)
date: 2026-10-25
created: 2026-10-25
updated: 2026-10-25
status: draft v1, for Gawan's review; not reviewed independently yet
---

# Turn attention tiers

Governs how loudly each element of a chat turn is drawn: `desktop/src/pages/chat/turnZones.ts`,
`components/TurnZones.tsx`, `TurnCards.tsx`, `AssistantMessageView.tsx`, `ChangesCard.tsx`,
`EscalationBlock.tsx`, `InlinePermissionRequest.tsx`, and one new backend observer. Display
spec: `Knowledge/Designs/2026-10-19-chat-turn-display-spec.md` (v6). Where they disagree,
this doc says which v6 line changes (§6).

## 1. The problem (why this is not one more row)

Gawan, 2026-10-25: only what the user cares about gets colour; agent chatter must not take
attention; key outputs (e.g. what was committed) must show where they happen.

Today nothing decides attention. Each component picks its own weight and colour:

- 63 hard-coded palette colours (`text-red-400`, `text-amber-600`, `text-green-500`,
  `text-orange-500` …) across 7 turn files, measured 2026-10-25. The same meaning gets
  different colours (the "Needs you" label is `orange-500` on a failed call and the primary
  colour on other cards).
- A process note between two WORKED groups is drawn exactly like the answer (body size, body
  colour), so the answer does not stand out.
- The thought being written is shown in full while it streams: the largest moving text on
  the screen, and the user does not need to read it.
- A result that happens mid-turn (a commit landed on main) leaves no mark in the turn; it
  shows only in the RESULT prose and the end card. In session `db83772e`'s last turn the
  commit, the land and the worktree removal ran in one Bash whose outcome line was the
  removal's JSON.
- Showing every call's outcome is not the fix: in the same turn most outcomes are the last
  line of a `sed` or `grep` (`320-}`, `urlparse:`).

So the fix is a model, not a row: every element of a turn belongs to exactly one tier, the
tier decides its weight and colour, and adding an element without a tier does not compile.

## 2. The three tiers

| Tier | Meaning | Look | Fold |
|---|---|---|---|
| **act** | the user must do something | card, primary-colour label, buttons | never |
| **know** | a result the user should know: something finished or landed | normal text weight; a faint success tint only on a landed fact | never |
| **process** | how the agent got there | secondary / dim text, no colour | folds (v6 rules) |

Colour rules:
- `act` owns the primary colour (labels, primary button). Nothing else in a turn uses it,
  except links.
- `know` may use `--color-status-success`, faint: an 8% tint and a 3px left bar on a landed
  row, the colour on the `RESULT` label, and `+a` on diff counts (`−d` uses
  `--color-status-error`, on the number only). Only for facts read from a record, never for
  a state guessed from output (§5).
- `process` uses no colour at all. A failed call the agent handled itself stays uncoloured
  (v6 §0.6).
- A turn file draws a colour only through the tier tokens of §4.2, never a palette class.

## 3. Every element, with its tier

This is the whole list of what a turn draws (zone kinds in `turnZones.ts`, plus the parts
`AssistantMessageView` adds around the zone list). "Change" says what moves; empty means
the element keeps today's look.

| Element | Source (unchanged) | Tier | Change |
|---|---|---|---|
| `needs`: question, permission, escalation | interrupt blocks | act | colours from tier tokens |
| `failure`: a call that failed and ended the turn | `unrecoveredFailure` | act | label `orange-500` → act token |
| Pipeline attention card (`AttentionCard`) | `pipeline_attention` | act | tokens |
| Terminal error (`ChatErrorMessage`) the user must retry | `message.isError` | act | tokens |
| `text` role `answer` (`RESULT` + answer) | text after the last call | know | `RESULT` label success colour |
| **Landing row (new)** | `turn_landing` display block (§4.1) | know | new, mid-turn |
| `stageResult` (stage summary + docs) | `pipeline_stage` | know | tokens |
| `gate` verdict line | `pipeline_stage.gate` | know | PASS is plain; BLOCK that stops the run is already an `act` card |
| Run result (`RunResult`) | `pipeline_summary` | know | tokens |
| Output card (files, landings, deliverables) | `turn_changes`, `turn_deliverables` | know | `+a` / `−d` colours from tokens |
| `stopped` ("Stopped by you …") | stop marker / receipt | know | — |
| Background-finished card | `background_finished` | know | — |
| Next-step chips | `nextSteps` | know | — |
| `text` role `restatement` (before the first call) | first text | process | secondary colour |
| `text` role `note` (between groups) | text between calls | process | **body colour → secondary** |
| `thinking` / thought rows | thinking blocks | process | **the one being written shows one line, not the full text** |
| `phase` (WORKING / WORKED) | calls | process | — |
| tool strip | tool_use / tool_result | process | ▸ when it has detail; detail shows the kept content, not only the outcome line |
| Agents panel | Agent calls, daemon store | process | its own tint stays (v6 §3.2 texture, not attention) |
| `pipelineStart` separator, stage group header | pipeline | process | — |
| Assumptions disclosure | `pipeline_attention` verification items | process | — |
| Compact notice, turn-phase line, bee | session state | process | — |

Not in a turn, so out of scope: the tab notice bar, toasts, Canvas.

## 4. Mechanism

### 4.1 Backend: mid-turn results come from records, through the existing observer

There is already one way the backend adds a block in the middle of a turn: after a Bash that
names a pipeline run returns, the streaming orchestrator reads the run's record (`run.json`)
off the event loop and emits `pipeline_stage` blocks (`core/pipeline_stage_events.py`).
Landings use the same path. No second mechanism.

- Pre-filter (cheap, on the call's input only): the command runs `session_worktree` `land`.
  The command is recognised in one place, next to `parse_watched_run_id`.
- After the call returns: `executors.run_in("io", session_landings.read_repo, …, turn_id=…)`
  for the repo land printed as `main`. It reads git's own reflog stamps, the same reader
  the end card uses (`docs/design/2026-10-25-turn-result-card.md` §3). Landings already
  emitted in this turn are skipped (keyed by `new` sha).
- Each new landing → one `turn_landing` block carrying the landing exactly as `read_repo`
  returns it for the end card (`repo, branch, dest, onto, new, count, subjects, files`), plus
  `at`. One shape for both places, so the row and the card cannot say different things.
  Persisted like `pipeline_stage`, and non-conversational (`context_injector`'s list).
- What land printed is only the trigger. A landing that has no stamp in the reflog is not
  shown, exactly as on the end card. A `land` that refused, or that said "Already up to
  date", produces no block.
- The end card keeps its own read and stays the complete list. A land the pre-filter does
  not recognise (for example run through a shell variable) gets no mid-turn row but still
  shows on the end card. The row shows the commits (sha + subject) only; the files stay on
  the end card, so the turn does not list them twice.

Cost: nothing on the send path or per token. The same git reads the end card makes for one
repo (reflog, `rev-list`, `log`, two diffs), in the `io` pool, only after a `land` call,
which happens about once per change.

### 4.2 Frontend: one table, three styles, a guard

- `desktop/src/pages/chat/attention.ts`: `type Tier = 'act' | 'know' | 'process'` and
  `TIER: Record<ZoneKind | 'textAnswer' | 'textNote' | 'textRestatement' | ExtraPart, Tier>`.
  A `Record` over the kind union means a new zone kind without a tier is a type error.
- Tier tokens, defined once in `index.css`: `--color-attn-act` (= primary),
  `--color-attn-know` (= status-success), `--color-attn-know-tint`
  (`color-mix(know 8%, transparent)`), and `.attn-process` text = `--color-text-secondary`.
  Components read the tier and use its class; they do not pick colours.
- Guard test `turnAttention.test.ts`, the same pattern as `navVisualSystem.test.ts`: counts
  palette classes per turn file and must equal `KNOWN`. It starts at today's counts (63) and
  each migration step lowers its `KNOWN` entry, ending at `{}`. A new palette colour in a
  turn file fails the test.

### 4.3 What does not change

The zone layout (`layOut`), the column grid, fold timing, merging, the stream order, the
data each element reads, and every behaviour not named in §3's Change column.

## 5. Not recognised, on purpose (never guess)

- Test or build pass/fail. It would mean parsing pytest / vitest / tsc output. A failure the
  agent cannot handle already reaches the user as an `act` card; a pass is said in the agent's
  own words.
- Commits that were not landed (a worktree commit, an in-place commit): the end-card design
  left them out on purpose (turn-result-card §1). They stay process.
- The generic outcome line of a call is never promoted: it stays in the strip's detail.

## 6. Spec v6 changes

- §0.6 adds: "results already recorded (a landing, a stage result, the answer) may use the
  success colour, faintly; nothing else is coloured."
- §2.4: a note between groups is secondary colour (was: normal colour).
- §0.8 / §2.3: the thought being written shows its latest line; open it for the full text.
- §3.1: a strip with detail shows ▸; its detail is the kept content.
- §1 table: new row "landing row, at the call that landed".

## 7. Regression control

1. **Acceptance tests first, red on today's code** (the landing row ones fail because the
   block type does not exist, everything else by assertion):
   - backend: a land call yields exactly one `turn_landing` block, with commits from the
     reflog stamp; a refused or up-to-date land yields none; a second read in the same turn
     emits nothing again; an unstamped merge emits nothing; the reader runs in the `io` pool.
   - frontend: every zone kind has a tier (type-level + one runtime check over a fixture of
     every kind); the answer's text colour differs from a note's; a landing row renders at
     its call's place in the stream (live == reload); the live thought shows one line and
     opens to full; the guard of §4.2.
   - Each fix is mutation-checked (revert it, its test goes red).
2. **Existing suites stay green unchanged**: `src/pages/chat` (74 files, 868 tests today),
   `tests/test_pipeline_stage_events*.py`, `tests/test_session_landings*.py`,
   `tests/test_turn_changes*.py`. A test edit is allowed only where §6 changes the rule it
   asserts, and each such edit is listed in the commit message.
3. **Pilot first**: step 2 below changes the look of one turn's tiers; Gawan checks it in
   light and dark before the palette migration (step 4) touches the remaining cards.
4. No historical data is touched. Old turns have no `turn_landing` blocks and show as today.

## 8. Steps (one ff-merged commit each, own worktree, no push, no build)

1. `attention.ts` + tier tokens + the guard at today's counts (no visual change).
2. Pilot: `text` note → secondary, `RESULT` label → know colour, live thought → one line,
   strip ▸ + kept content. Stop for Gawan's visual check.
3. Backend `turn_landing` observer + frontend landing row.
4. Migrate the 63 palette colours to tier tokens file by file (`KNOWN` → `{}`), the act cards
   included.
5. Spec v6 edits of §6; this doc's status → implemented.
