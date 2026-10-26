---
title: One look for every turn card, and the code-changes card after native coding
date: 2026-10-26
created: 2026-10-26
updated: 2026-10-26
status: v1 draft, awaiting review
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
| F | Code-changes card data after native coding | empty: ad-hoc worktree is not a code root; plain `merge --ff-only` writes no stamp | rows and commits from the trees the turn wrote into (§3) |

Not changed: which file goes on which card (a file is on one card only), the
`turn_product` model, `session_landings` (stamped lands still work and are merged
with §3 by sha).

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

### 3.1 Which trees

A tree is on the card when **this turn wrote into it** (the paths `note` /
`note_tool` already collect) and it is a git tree: its toplevel is found by walking up
to a `.git` entry (filesystem only, 0.01 ms). Bound checkouts and run worktrees keep
today's path. Any other tree is a *turn tree*.

### 3.2 Where a turn tree starts

Only facts git keeps, else the tree is left off:

1. **Created during this turn**: the worktree's own reflog
   (`<gitdir>/logs/HEAD`, first line, `branch: Created from …`) gives the creation
   commit, and its time is after the turn started. Start = that commit.
2. **Written by this session's previous turn**: that turn's end state (HEAD and
   change tree) is kept in the session context and is this turn's start. In memory
   only: after a backend restart, rule 1 or off.
3. Otherwise (an older tree this session never wrote before): left off.

### 3.3 The tree may be gone at turn end

The usual flow lands and then runs `git worktree remove` + `git branch -d` in the same
turn, so at `result` there is no worktree, no reflog and no branch. Therefore:

- On the first write into a turn tree, record its gitdir, branch ref and start (§3.2).
- After every tool result of the turn, read each recorded tree's HEAD from the
  gitdir files (`HEAD`, the ref file or `packed-refs`: file reads only, no git
  process) in the `turn_changes` pool, fire-and-forget, and keep the last value.
  A tool result is never delayed.
- At `result`, per turn tree (in the pool, inside `FINISH_BUDGET_S`):
  - tree still there: rows = today's diff of the start tree against the current
    change tree; commits = `git log start..HEAD`.
  - tree gone: the objects are still in the shared object store. Rows = diff of
    `start^{tree}` against `last HEAD^{tree}` (only committed content; uncommitted
    edits in a removed tree are gone and are not shown); commits = `start..last HEAD`.
  - landed: `git merge-base --is-ancestor <last HEAD> <checkout branch>` → `→ main`.
- Commits already on the card from a stamped landing are not listed twice (by sha).

### 3.4 What is never done

No scan of main's reflog for a time window, no guess of which commit on main is
this turn's (v4-v7 of `2026-10-25-turn-result-card.md`), no snapshot before a write.
A tree whose start is unknown is simply not on the card.

## 4. Cost (measured 2026-10-26, this repo, best of 5)

| Step | Where | Cost |
|---|---|---|
| send | — | 0 (nothing added) |
| first write into a turn tree: toplevel walk + reflog first line | pool, async | 0.01 + 0.3 ms |
| after each tool result: HEAD from gitdir files | pool, fire-and-forget | < 0.1 ms per tree |
| turn end, per turn tree: change tree (as today) + `git log` + `is-ancestor` | pool, inside `FINISH_BUDGET_S` | ~0.2 s + 19 ms + 18 ms |
| frontend | render only | B/C remove components; A/E reuse one row |

The turn end already waits for this card; a native coding turn usually touches one
tree, so it adds about 40 ms to a turn that changed code, and nothing to any other
turn. A test pins that `send()` and `note_tool()` start no git process.

## 5. Removed

`RunResult`, `AttentionCard` and their rendering; `DocCard`'s Show full text and
Open buttons and excerpt; `SummaryDocRow`; the amber not-push-ready styles; the
separate answered/decided summary cards (replaced by the grey line). Their tests are
rewritten to the new rules, each reason in the commit message.

## 6. Acceptance tests (written first, red on today's code)

1. A long file name renders whole on one line (`nowrap`, no ellipsis) in all four row
   kinds; only the folder truncates.
2. Product, pipeline-doc, changed-file and commit rows share one `ItemRow` (same DOM
   shape and classes).
3. A turn with `pipeline_summary` + `pipeline_attention` renders neither a run-result
   nor an attention card; the run's present docs are rows in the DELIVERABLES box;
   skipped docs are absent.
4. AskUserQuestion / permission / escalation: one frame; header says Swarm; answered
   → one grey line.
5. Backend: a turn that creates a worktree with `git worktree add`, edits, commits,
   `merge --ff-only` into the checkout, then removes the worktree and deletes the
   branch → the block has the file rows and the commit, marked on main.
6. Same, worktree kept and not merged → rows + commit marked not on main.
7. An older worktree first written this turn by a session that never wrote it →
   no rows (left off, not guessed).
8. Another session's commit landed on main during the turn → not on this card.
9. `send()` / `note_tool()` start no subprocess; finish stays inside its budget with
   a stalled tree (existing hang test extended).
10. stream-order and liveReload properties stay green.

Each fix gets a mutation check.

## 7. Steps (one ff-merged commit each, no push)

1. Backend §3 + tests 5-9.
2. `ItemBox` / `ItemRow`; product rows and changes card on it (A, E); tests 1-2.
3. Pipeline (B, C); test 3.
4. Act cards (D); test 4.
5. Gawan checks light and dark; then spec v6 §8 and the session-products doc are
   synced.

## 8. Residuals

- A turn tree first written only by a Bash command that names no path is not seen
  (no `file_changed`); its changes are not on the card.
- Rule 2 is lost on a backend restart: the next turn in an old worktree shows no rows.
- Uncommitted edits in a worktree removed during the turn are not shown (they no
  longer exist).
- A sub-agent's writes count for the turn, as `note` sees them today.
