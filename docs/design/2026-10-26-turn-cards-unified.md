---
title: One look for every turn card, and the code-changes card after native coding
date: 2026-10-26
created: 2026-10-26
updated: 2026-10-26
status: v2 draft (§3 rewritten from the root cause), awaiting review
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
| B | Pipeline run: `RunResult`, `AttentionCard` ("Not ready to push", "Needs your judgement") | own cards, amber | **not rendered**. The agent says it in its normal answer. Backend blocks unchanged, history untouched |
| C | Pipeline docs: `DocCard` under a stage, `SummaryDocRow` on the end card | own looks (Show full text / Open, purpose, REVIEW stats, skipped rows) | the product row under the stage row; the run's present docs join the one DELIVERABLES box. Skipped docs and stats are not shown |
| D | Act cards: AskUserQuestion, permission, escalation | three looks; "Claude needs your input" | one frame (border, primary left bar, header line, body, buttons right); "Swarm needs your decision"; once answered, one grey line |
| E | Code-changes card look | own rows | same box and row as A, plus `+N −M` and ▸ diff; a COMMITS section |
| F | Code-changes card data after native coding | empty: lands are stamped `turn=none`; session worktrees are not code trees | the backend reads git records by session and turn boundary (§3) |

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

## 3. Code changes after native coding (F): the backend observes, the agent carries nothing

### 3.1 Root cause (verified 2026-10-26)

What a turn did is today attributed by **identifiers the agent's command must carry**:

- **Commits / landings.** `run_owner_gate` writes `SWARM_TURN_ID` into a
  `session_worktree land` command only when the land is the line's first segment
  (`2026-10-25-turn-result-card.md` §3.2). The stamp then carries the turn id;
  `TurnChanges` reads landings by turn id, and only when the gate counted a land
  (`_turn_lands > 0`). In main's reflog **all 33 `swarm-land` stamps say `turn=none`**
  (`git reflog show -n 3000 refs/heads/main`). Calling `_session_worktree_rewrite`
  directly: `cd <wt> && … land` and `W=$(…); … land $W` both get no turn id. The
  session key is always right: `SWARM_APP_SESSION_ID` is in the CLI process env from
  spawn. A turn id cannot be put there: the CLI process lives across turns.
- **File rows.** `code_roots()` is bound checkouts + run worktrees. A session worktree
  (`session_worktree create`, the tool AGENT.md prescribes) is neither, so its edits
  have no row.

So the card depends on command shape and on a registry of trees. The fix removes both
dependencies: the backend, which knows when each turn starts and ends, reads git's own
records.

### 3.2 Which commits are this turn's

Two facts, both recorded by git, plus the turn's start and end the backend owns:

1. A `swarm-land` reflog entry names its session (`session=<key>`, `key =
   session_key(session id)`). That is the attribution to the session.
2. The entry's **position** in the destination branch's reflog file
   (`<common dir>/logs/refs/heads/<branch>`, append-only by git) says when it was
   written relative to the turn.

- **Turn start**, inside the existing start job on the `turn_snapshot` pool (async,
  alongside today's snapshots): for each bound main checkout, read its current branch
  (`HEAD` file) and `os.stat` that branch's reflog file → `(branch, inode, size)`.
- **Turn end** (`finish`, `turn_changes` pool, inside `FINISH_BUDGET_S`): read only the
  bytes after `size` of the same file. Each appended line is `<old> <new> <ident> <ts>
  <tz>\t<message>`; a line whose message is a stamp with this session's key is this
  turn's landing. Its commits, files and checks are what `session_landings` builds
  today from a stamp (`onto..new`, ancestry checks, the same row cap), so the card
  shows the same facts as a stamped land does now.
- The file was replaced or shrank (inode changed, size smaller: `reflog expire`, a
  rewrite) → that repo is `unknown: reflog-rewritten`, never an empty list.
- Not a reflog window over main in the v4-v7 sense: unstamped entries are never read
  for meaning and nothing on main is attributed by time. The window only says which
  turn a stamp *already naming this session* belongs to.

Removed: the turn id in the stamp's meaning, `SWARM_TURN_ID` in the rewrite,
`_turn_lands` and `expect`. The stamp keeps `turn=` for old readers; nothing reads it.
`run_owner_gate` keeps handing `SWARM_APP_SESSION_ID` (a prewarmed process may lack it).

### 3.3 Which file rows are this turn's

A session's worktrees are known by name: `session_worktree create` names the branch
`session/<key>-<name>`.

- **Turn start**, same start job: list each bound repo's `<common dir>/worktrees/*/`
  (file reads: `gitdir`, `HEAD`); every worktree whose `HEAD` is
  `ref: refs/heads/session/<key>-…` is a code tree of this session and gets today's
  start snapshot (`_snapshot_root`). Counted in the existing `MAX_START_SNAPSHOTS`.
- **Created during the turn** (not present at start): its start is the creation commit,
  the first line of `<common dir>/worktrees/<id>/logs/HEAD` (`branch: Created from`).
  No such line → left off.
- **Turn end**: rows as today for every session tree the turn wrote into (`note` /
  `note_tool` paths). If the worktree was removed during the turn (land, then
  `remove`), its committed rows are already on the card through its landing (§3.2);
  its uncommitted edits no longer exist and are not shown.
- A row and a landing row for the same file are merged as today (one row).

### 3.4 What is never done

No snapshot before a write, nothing on send or per tool call, no attribution by time
of anything unstamped, no reading of the agent's command text for identity, no guess of
a start. A tree or repo whose facts are missing is left off or marked unknown.

## 4. Cost (measured 2026-10-26 on this repo, best of 5)

| Step | Where | Cost |
|---|---|---|
| send, each token, each tool call | — | 0 (nothing added) |
| turn start: per bound checkout, read `HEAD` + `stat` the branch reflog | start job, `turn_snapshot` pool, async | < 0.1 ms |
| turn start: list `worktrees/*/HEAD` | same | < 1 ms |
| turn start: snapshot each of this session's worktrees | same | ~0.2 s each, as today's code roots, in parallel with the model's first thought |
| turn end: read appended reflog bytes | `turn_changes` pool | < 1 ms (a turn appends a few lines) |
| turn end: per landing, today's `session_landings` facts | same, inside `FINISH_BUDGET_S` | as today (~40 ms) |

A turn that lands nothing reads a few appended bytes and starts no git process at its
end. A test pins that `send()` and `note_tool()` start no subprocess.

## 5. Removed

Frontend: `RunResult`, `AttentionCard` and their rendering; `DocCard`'s Show full text
/ Open / excerpt; `SummaryDocRow`; the amber not-push-ready styles; the separate
answered/decided cards (now one grey line). Backend: `SWARM_TURN_ID` in
`_session_worktree_rewrite`, `_turn_lands` counting, the `expect` path of
`read_session`. Tests of each are rewritten to the new rule, reasons in the commit
message.

## 6. Acceptance tests (written first, red on today's code)

Frontend
1. A long file name renders whole on one line (`nowrap`, no ellipsis) in product,
   pipeline-doc and changed-file rows; only the folder truncates.
2. Those rows and the commit row share one `ItemRow`.
3. `pipeline_summary` + `pipeline_attention` render neither a run-result nor an
   attention card; the run's present docs are rows in the DELIVERABLES box; skipped
   docs are absent.
4. AskUserQuestion / permission / escalation: one frame; header says Swarm; answered
   → one grey line.

Backend (real git repos in tmp, real `session_worktree` script)
5. `cd <wt> && … session_worktree land` (no turn id reaches it) in a turn → the card
   lists the landing's commits and files.
6. Another session's stamped land and an unstamped `merge --ff-only` on main during
   the turn → not on this card.
7. This session's land in the previous turn → not on this turn's card.
8. Edits in a session worktree that existed at turn start, not landed → rows.
9. A worktree created and edited in the turn, not landed → rows from its creation
   commit.
10. Another session's worktree edited → no rows.
11. The branch reflog expired mid-turn → `unknown: reflog-rewritten`.
12. `send()` / `note_tool()` start no subprocess; `finish` stays in budget with a
    stalled tree.
13. stream-order and liveReload properties stay green.

Each fix gets a mutation check.

## 7. Steps (one ff-merged commit each, no push)

1. Backend §3 + tests 5-12.
2. `ItemBox` / `ItemRow`; product rows and changes card on it (A, E); tests 1-2.
3. Pipeline (B, C); test 3.
4. Act cards (D); test 4.
5. Gawan checks light and dark; then spec v6 §8, `2026-10-25-turn-result-card.md`
   §3.2 and the session-products doc are synced.

## 8. Residuals

- A land not made by `session_worktree` (a hand-written `merge --ff-only`) has no
  stamp and is not on the card. AGENT.md already forbids it.
- A background sub-agent started in an earlier turn that lands during this turn is on
  this turn's card: the land did happen in this turn.
- Uncommitted edits in a worktree removed during the turn are not shown.
- A write into a session worktree only by a Bash command that names no path is not in
  `note` (as today) and has no row; its land still shows.
