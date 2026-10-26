---
title: One look for every turn card, and the code-changes card after native coding
date: 2026-10-26
created: 2026-10-26
updated: 2026-10-26
status: v4 draft (back to the root after three redesigns), awaiting review
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
| F | Code-changes card data after native coding | empty: lands are stamped `turn=none`; session worktrees are not code trees | the gate exports the turn id to the whole command; exact reflog search by turn id; session worktrees are code trees (§3) |

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

- **Commits.** A landing is attributed to a turn by the turn id in its `swarm-land`
  stamp. The id reaches `session_worktree land` only through `run_owner_gate`'s
  rewrite, which prefixes `SWARM_TURN_ID=<id>` only when the land is its line's first
  simple command (`2026-10-25-turn-result-card.md` §3.2). All 33 `swarm-land` stamps on
  main say `turn=none`; `cd <wt> && … land` and `W=$(…); … land $W` get no id
  (checked by calling `_session_worktree_rewrite`).
- **File rows.** `code_roots()` is bound checkouts + run worktrees; a session worktree
  is neither.

The stamp, the hook context that owns the turn id, and the git read are right. Only
two narrow pieces are wrong: the **shape** of the hand-off (a prefix that binds to one
simple command) and the **set of trees** the rows look at.

### 3.2 Hand-off: export, not prefix

`run_owner_gate` already sees every Bash call of the main agent and already holds the
turn id in its per-unit hook context (`ctx["_turn_id"]`, set at turn admission). The
only change: when a main-agent Bash command mentions `scripts.session_worktree` (the
gate's existing cheap substring prefilter), the gate prepends

```
export SWARM_APP_SESSION_ID=<id> SWARM_TURN_ID=<turn id>;
```

to the **whole** command line instead of putting `VAR=… ` before its first segment.
`export` applies to every later segment and to every subshell (`cd && land`,
`$(…)`, a script that calls `land`, a pipe), so the shape of the command no longer
matters. Unchanged:

- the per-unit context is the source (two units with one session id each hand their
  own id; no shared file, no restart overlap);
- a call carrying an `agent_id` (a sub-agent, which can outlive the turn) gets only
  `SWARM_APP_SESSION_ID`, so its land is on Canvas, not on a turn card (existing R1);
- a command that sets either variable itself is not rewritten;
- the gate stays the one Bash hook that returns `updatedInput`; when the pipeline path
  also rewrites the same line, the export goes in front of its result;
- the gate sets `ctx["_turn_sw"] = True` (any session_worktree mention this turn).

Cost: string work in a hook that already runs; nothing on send, per token, or per
other tool call.

### 3.3 Read: exact search by turn id, only when this turn could have landed

`_turn_lands` and `expect` go. At turn end, **only when `ctx["_turn_sw"]`**,
`TurnChanges` asks git for every reflog entry stamped with this turn:

```
git log -g --all --format=%H%x00%gs%x00%gd --fixed-strings --grep-reflog=turn=<turn id>
```

per bound repo, on the `turn_changes` pool, under the existing deadline. Each hit is
parsed and checked exactly as `session_landings` checks a stamp today (session key,
`onto..new` ancestry, row cap), and becomes a `landed` entry. Through git, so files and
reftable both work; `--all` covers every branch, so a branch switch before or after
the land does not matter; no entry cap, so a busy reflog cannot hide it and no
`truncated` block appears for a turn that landed nothing. Measured on this repo: 55 ms
(no hit), 61 ms (hits). A turn without a session_worktree command runs no git.

A reflog expired between the land and the turn's end (seconds) is the only way to lose
it; that repo is then simply absent, as today.

### 3.4 Session worktrees are code trees

At turn start, inside the existing async start job (`turn_snapshot` pool), `git
worktree list --porcelain` per bound repo (15-25 ms measured) finds worktrees on
`refs/heads/session/<key>-…`; each gets today's start snapshot and today's rule (a
snapshot finished after the turn's first tool call is not used). A worktree created
during the turn: start = the commit of the branch reflog's `branch: Created from` entry
(`git reflog show refs/heads/<branch>`, through git). No such entry → left off. Rows
are limited to the files the turn wrote (`note` / `note_tool`, as today), so base
changes brought in by a later rebase show only where they touch those files.

### 3.5 Pipeline runs

A pipeline landing writes no stamp; its sha is `pipeline_summary.landedSha`, listed in
COMMITS as `<sha> · pipeline run <run id>`.

## 4. Cost (measured 2026-10-26 on this repo)

| Step | Where | Cost |
|---|---|---|
| send, each token | — | 0 |
| each Bash call mentioning session_worktree | existing gate | string concat |
| turn start | existing async start job | `git worktree list` 15-25 ms per bound repo + today's snapshot per session worktree |
| turn end, only if the turn ran session_worktree | `turn_changes` pool, deadline-bounded | ~60 ms per bound repo |

## 5. The pipeline run line (B)

`RunResult` and `AttentionCard` are not rendered (Gawan, 2026-10-26: the agent says it
in its answer). One grey line from `pipeline_summary`: `run_8f2a · <completed |
abandoned | failed> · landed c063b99 | not landed · N documents`. Backend blocks and
history unchanged.

## 6. Removed

Frontend: `RunResult`, `AttentionCard`, `SummaryDocRow`, `DocCard` excerpt and buttons,
the amber styles, the separate answered/decided cards. Backend: the first-segment
`VAR=` prefix for session_worktree, `_turn_lands`, `read_session(turn_id=, expect=)`
in `TurnChanges` (Canvas and ToDo keep `read_session` without a turn). Tests rewritten
to the new rules, reasons in the commit message.

## 7. Acceptance tests (written first, red on today's code)

Frontend
1. A long file name renders whole on one line in product, pipeline-doc and changed-file
   rows (box `scrollWidth > clientWidth` when narrow); only the folder truncates.
2. Those rows and the commit row share one `ItemRow`.
3. `pipeline_summary` + `pipeline_attention` → no run-result / attention card; one grey
   run line; present docs are rows in the DELIVERABLES box.
4. AskUserQuestion / permission / escalation: one frame; header says Swarm; answered →
   one grey line.

Backend (real repos in tmp, the real `session_worktree` script, the real gate)
5. `cd <wt> && … land`, `W=$(…); … land $W`, `bash -c '… land'` from the main agent →
   stamp carries the turn id; the card lists commits and files.
6. The same from a sub-agent → `turn=none`, not on the card.
7. Another session's land and this session's previous-turn land → not on the card.
8. Land into a branch, then switch the checkout's branch → still on the card.
9. A reflog with > 200 newer entries → still found; a turn without session_worktree
   runs no git at its end and gets no block.
10. Session worktree present at start, edited, not landed → rows; created in the turn →
    rows from its creation commit; another session's worktree → none.
11. Two units with one session id hand different turn ids.
12. Pipeline `landedSha` is in COMMITS.
13. `note_tool()` and the token path start no subprocess; stream-order and liveReload
    properties stay green.

Each fix gets a mutation check.

## 8. Steps (one ff-merged commit each, no push)

1. Backend §3 + tests 5-12.
2. `ItemBox` / `ItemRow`; product rows and changes card (A, E); tests 1-2.
3. Pipeline (B, C, §5); test 3.
4. Act cards (D); test 4.
5. Gawan checks light and dark; then spec v6 §8 and `2026-10-25-turn-result-card.md`
   §3.2 are synced.

## 9. Residuals

- A CLI-initiated turn: if its first tool call is a land that runs before
  `begin_injected_turn` sets the new id, it carries the previous turn's id and is on no
  card (Canvas shows it).
- A sub-agent's land is on Canvas only (unchanged).
- A hand-written `merge --ff-only` has no stamp; AGENT.md forbids it.
- A reflog expired within the turn loses that landing from the card.
- Uncommitted edits in a worktree removed during the turn are not shown.
- Stamps are unauthenticated (prior R2): display only.

## 10. Review history

- v1: worktree registry + per-tool HEAD tracking; replaced once the root cause was found.
- v2: no turn id, branch-reflog byte offsets. REDESIGN (async race, reftable, branch
  switch, gc, creation line location).
- v3: turn id via a session-keyed file. REDESIGN: injected turns and prewarm compound
  commands not covered; one file shared by two units; removing `expect` with the
  200-entry cap misses lands and makes `truncated` blocks on every busy repo
  (`_block` keeps them, checked).
- v4 (this): after three redesigns, back to the root: the existing turn id, stamp and
  per-unit hook context were right. Two narrow changes: an `export` hand-off that
  any command shape inherits, and an exact `--grep-reflog=turn=<id>` search over all
  reflogs, run only when the turn used session_worktree.
