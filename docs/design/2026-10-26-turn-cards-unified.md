---
title: One look for every turn card, and the code-changes card after native coding
date: 2026-10-26
created: 2026-10-26
updated: 2026-10-26
status: v3 draft (turn id via a backend-owned file; v2 refuted by review), awaiting review
owner: Gawan
---

# One look for every turn card, and the code-changes card after native coding

## 0. Goal and the hard constraint

Gawan, 2026-10-26, after a gallery of every chat card rendered from the real
components: the cards look like different products; "not ready to push" style cards
must go; pipeline deliverables must look like every other deliverable; a file is one
line and its name is never cut; and a native coding turn ends with no card for its
file changes and commits, which is a bug.

**Hard constraint: chat performance does not change.** Nothing is added to the send
path, to each streamed token, or before any tool call runs. New work runs in the
existing `turn_changes` pool. At turn end it stays inside the existing
`FINISH_BUDGET_S` (10 s), measured in §4.

Kept as they are (Gawan's earlier lines): the in-progress call and thought, the 2 s
fold delay, scroll-up freeze, the zone layers, the failure card (H) and the
background-agent card (I), the header dot.

## 1. What changes

| # | Surface | Today | After |
|---|---|---|---|
| A | Product rows (in turn + end card) | v3 box, a long name may wrap | same box; name `nowrap`, never cut; only the grey folder shrinks (middle cut) |
| B | Pipeline run: `RunResult`, `AttentionCard` ("Not ready to push", "Needs your judgement") | own cards, amber | **not rendered**; one grey run line (§5). Backend blocks unchanged, history untouched |
| C | Pipeline docs: `DocCard` under a stage, `SummaryDocRow` on the end card | own looks (Show full text / Open, purpose, REVIEW stats, skipped rows) | the product row under the stage row; the run's present docs join the one DELIVERABLES box. Skipped docs and stats are not shown |
| D | Act cards: AskUserQuestion, permission, escalation | three looks; "Claude needs your input" | one frame (border, primary left bar, header line, body, buttons right); "Swarm needs your decision"; once answered, one grey line |
| E | Code-changes card look | own rows | same box and row as A, plus `+N −M` and ▸ diff; a COMMITS section |
| F | Code-changes card data after native coding | empty: lands are stamped `turn=none`; session worktrees are not code trees | turn id from a backend-owned file; session worktrees are code trees (§3) |

Not changed: which file goes on which card (a file is on one card only), the
`turn_product` model, the stamp format (`session_landings` builds the landing facts as
today).

## 2. One row, one box (A, C, E)

One component family in `TurnCards.tsx`: `ItemBox` (border, radius 10, `w-fit
max-w-full`, optional header line) and `ItemRow` (badge · name · folder · tail ·
actions). Every card uses it:

- product file: tail empty, action Copy path; the row and the name open the Canvas
- remote product: name = action, folder = target, action ↗ when a URL is known
- pipeline doc: same as a product file (name = `REVIEW.md`, folder = run dir)
- changed file: tail = `added`/`deleted`/`binary` and `+N −M`; leading ▸ expands the
  diff below the row (today's on-demand `GET /api/chat/turn-diff`)
- commit: badge = short sha, name = subject (may truncate: it is not a file name),
  tail = `→ main` when it is on the checkout's branch, `not on main` otherwise

Name: `whitespace-nowrap shrink-0`. Folder: `shortFolder` (middle cut), `truncate
shrink-[100]`, full path on hover. If the name alone is wider than the column, the
box scrolls horizontally inside itself (`overflow-x-auto`); the name is still whole.

## 3. Code changes after native coding (F)

### 3.1 Root cause (verified 2026-10-26)

- **Commits / landings.** A landing is attributed to a turn by the turn id in its
  `swarm-land` stamp, and that id reaches `session_worktree land` only through
  `run_owner_gate`'s rewrite, which fires only when the land is its line's first
  segment (`2026-10-25-turn-result-card.md` §3.2). In main's reflog **all 33
  `swarm-land` stamps say `turn=none`**. Calling `_session_worktree_rewrite` directly:
  `cd <wt> && … land` and `W=$(…); … land $W` get no turn id. `TurnChanges` reads
  landings only by turn id and only when the gate counted one (`_turn_lands > 0`), so
  the card has no commits. The session key is always right: `SWARM_APP_SESSION_ID` is
  in the CLI process env from spawn. The turn id cannot be put there: the CLI process
  lives across turns.
- **File rows.** `code_roots()` is bound checkouts + run worktrees. A session worktree
  is neither, so its edits have no row.

Turn attribution by stamp is right (v2's reflog-offset window was refuted: async race
with the first land, reftable, branch switch mid-turn, gc / expire). Only the hand-off
of the id is wrong: it depends on the shape of the agent's command.

### 3.2 The turn id reaches `land` from a file the backend owns

- **Write.** Where `SessionUnit._reset_turn_state()` sets `ctx["_turn_id"]` (every turn
  admission: `send()`, `begin_injected_turn`; a question / permission continuation
  keeps it), the backend also writes the id to
  `<app data>/session-turns/<session key>` (32 hex bytes, `0600`, tmp + `os.replace`).
  The write runs on a pool thread and is **awaited before the query is written to the
  CLI**, so no tool call of the turn can run before it. Measured: 0.25 ms p50, 0.8 ms
  p99 (200 writes, this machine). That is the only addition to the send path; nothing
  is added per token or per tool call. A failed write logs and leaves the turn
  without an id (its lands show `turn=none`, as today); it never blocks the send.
- **Read.** `session_worktree._attribution()` reads `SWARM_TURN_ID` when set (kept for
  tests and the rewrite's old callers), else the file named by
  `session_key(SWARM_APP_SESSION_ID)`. Invalid or missing → `none`. Any command shape
  works: `cd && land`, `$(…)`, a script, a sub-agent.
- **Card.** `TurnChanges._compute` calls `session_landings.read_session(session,
  turn_id=…)` whenever the turn has an id, with no `expect` (its scan stops at the
  existing cap; a turn that landed nothing reads one bounded reflog page per bound
  checkout, inside `FINISH_BUDGET_S`, measured below). `read_session` goes through git
  commands, so reftable, branch switches and gc are handled as today.
- **Removed:** the `SWARM_TURN_ID` prefix in `_session_worktree_rewrite`,
  `ctx["_turn_lands"]` and its counting, the `expect` path. `SWARM_APP_SESSION_ID` is
  still handed (a prewarmed process may lack it). Canvas and ToDo call `read_session`
  without a turn and are unaffected; the stamp grammar (`turn=`) is unchanged.

A sub-agent's land carries the turn that is current when it lands. A stale file from
an ended turn can only be read by a land made between turns (no turn running), which
the card of the next turn does not read (its id differs).

### 3.3 Session worktrees are code trees

`session_worktree create` names its branch `session/<key>-<name>` when the session id
is valid (always, through the session env).

- **Turn start**, inside the existing start job on the `turn_snapshot` pool:
  `git worktree list --porcelain` per bound repo (21 ms measured); every worktree on a
  `refs/heads/session/<key>-…` branch is a code tree of this session and gets today's
  start snapshot, counted in `MAX_START_SNAPSHOTS`, with today's rule: a snapshot
  taken after the turn's first tool call is not used (that tree is left off, never
  diffed from a guess).
- **Created during the turn**: start = the commit in the branch reflog's creation entry
  (`git reflog show --format=%H%x00%gs refs/heads/<branch>`, the oldest entry,
  `branch: Created from …`; a git command, so reftable works). No such entry → left
  off.
- **Turn end**: rows as today for the session trees the turn wrote into. A worktree
  removed during the turn after its land: its committed rows arrive with the landing;
  uncommitted edits in it no longer exist and are not shown. A detached-HEAD worktree
  is not found by the prefix and is left off.

### 3.4 Pipeline runs

A pipeline landing makes its own merge without a `swarm-land` stamp; its sha is in
`pipeline_summary.landedSha`. The COMMITS section lists it (sha + "pipeline run
<run id>"), so a pipeline turn's commit is on the same card.

## 4. Cost (measured 2026-10-26 on this repo)

| Step | Where | Cost |
|---|---|---|
| send: write the turn id file | pool thread, awaited before the query goes to the CLI | 0.25 ms p50, 0.8 ms p99 |
| each token, each tool call | — | 0 |
| turn start: `git worktree list` per bound repo + snapshot of each session worktree | start job, `turn_snapshot` pool, async | 21 ms + ~0.2 s per worktree, as today's code roots |
| turn end: `read_session(turn_id)` | `turn_changes` pool, inside `FINISH_BUDGET_S` | measured in step 1 (one bounded reflog page per bound checkout); must stay < 0.3 s on this repo or the step stops |

A test pins that `note_tool()` and the per-token path start no subprocess.

## 5. The pipeline run line (B)

`RunResult` and `AttentionCard` are not rendered. One grey line takes their place after
the answer, from `pipeline_summary` only (no colour, no box, no "not ready to push"):

- landed: `run_8f2a · landed c063b99 · 4 documents`
- ended without landing: `run_8f2a · <status> · not landed` (status = completed /
  abandoned / cancelled / failed)

Why it cannot push, self-answered questions and assumptions are the agent's answer to
say. The audit card, REVIEW stats, skipped-doc reasons and inline "Show full text" go
(a document opens in the Canvas from its row). Backend blocks unchanged, history
untouched.

## 6. Removed

Frontend: `RunResult`, `AttentionCard`, `SummaryDocRow`, `DocCard`'s excerpt and
buttons, the amber styles, the separate answered/decided cards (one grey line now).
Backend: the turn-id rewrite, `_turn_lands`, `read_session(expect=)`. Tests of each are
rewritten to the new rule, reasons in the commit message.

## 7. Acceptance tests (written first, red on today's code)

Frontend
1. A long file name renders whole on one line in product, pipeline-doc and
   changed-file rows (`nowrap`, no ellipsis; the box's `scrollWidth > clientWidth`
   when narrower); only the folder truncates.
2. Those rows and the commit row share one `ItemRow`.
3. `pipeline_summary` + `pipeline_attention` → no run-result / attention card; one grey
   run line with status, landed sha, document count; present docs are rows in the
   DELIVERABLES box; skipped docs absent.
4. AskUserQuestion / permission / escalation: one frame; header says Swarm; answered →
   one grey line.

Backend (real git repos in tmp, the real `session_worktree` script)
5. `cd <wt> && … session_worktree land` and `W=$(…); land $W` in a turn → stamp has the
   turn id; card lists the commits and files.
6. Another session's land and a land in the previous turn → not on this card.
7. Edits in a session worktree present at turn start, not landed → rows.
8. A worktree created and edited in the turn → rows from its creation commit.
9. Another session's worktree → no rows; a detached worktree → no rows.
10. Branch switched mid-turn and land into the new branch → on the card.
11. The turn-id file write fails → turn proceeds, land stamps `turn=none`.
12. The query is not written to the CLI before the turn-id file exists; `note_tool()`
    starts no subprocess; `finish` stays inside its budget with a stalled tree.
13. A pipeline turn's `landedSha` is in COMMITS.
14. stream-order and liveReload properties stay green.

Each fix gets a mutation check.

## 8. Steps (one ff-merged commit each, no push)

1. Backend §3 + tests 5-13 (and the turn-end timing of `read_session`).
2. `ItemBox` / `ItemRow`; product rows and changes card on it (A, E); tests 1-2.
3. Pipeline (B, C, §5); test 3.
4. Act cards (D); test 4.
5. Gawan checks light and dark; then spec v6 §8, `2026-10-25-turn-result-card.md`
   §3.2 and the session-products doc are synced.

## 9. Residuals

- A land not made by `session_worktree` (a hand-written `merge --ff-only`) has no stamp
  and is not on the card. AGENT.md already forbids it.
- A background sub-agent from an earlier turn that lands during this turn is on this
  turn's card (the land happened in this turn).
- Uncommitted edits in a worktree removed during the turn are not shown.
- A session worktree whose start snapshot finished after the turn's first tool call is
  left off for that turn (existing rule).
- Stamps are unauthenticated (existing R2 of the prior design): display only.
- "Show full text" for a pipeline document is gone; the Canvas opens it.

## 10. Review history

- v1: worktree registry + per-tool HEAD tracking. Replaced after the root cause was
  found (§3.1).
- v2: no turn id; turn boundary from the branch reflog file's byte offset. Skeptic
  (2026-10-26) REDESIGN, verified: async offset can be taken after the first land (B1);
  creation line is in the branch reflog, not the worktree's `logs/HEAD` (B2, checked
  on this repo); files-backend only, no reftable (B3); branch switch, gc/expire (M1,
  M2); pipeline lands are unstamped (M6); removing RunResult loses run status (M5).
  v3 keeps the stamp, moves the id hand-off to a backend-owned file, reads through git,
  keeps one run status line.
