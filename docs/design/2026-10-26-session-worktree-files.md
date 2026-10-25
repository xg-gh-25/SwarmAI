---
title: Files written in a session worktree (open from chat, show on the turn's card)
date: 2026-10-26
created: 2026-10-26
updated: 2026-10-26
status: draft, not reviewed; no code yet
---

# Files written in a session worktree

## 1. Problem (observed 2026-10-25, session with worktree `20261025-234616-de3c`)

A native session followed AGENTS.md and wrote a design doc in its own session worktree
(`scripts.session_worktree create`, under `<app data>/session-worktrees/<name>/<repo>`).
Its answer named the doc as `docs/design/2026-10-25-pipeline-handout-by-file.md`. Two
things went wrong:

1. **The reference does not open.** The chip is clickable (`MarkdownRenderer.InlineCode`,
   `role="button"`), but it is drawn like code, so it does not read as a link. A click
   calls `GET /workspace/file/resolve`, whose cascade (`resolve_path_to_physical`) looks in
   SwarmWS, `Projects/*/` and the bound checkouts. It never looks in a session worktree,
   so it answers 404 and the user gets "File not found". Worse case: when main also has
   an older copy at that path, the click opens main's copy, not the one the session wrote.
2. **The turn's output card is missing.** The card has two sources. `turn_changes` diffs
   only `code_roots()` = bound checkouts + pipeline run worktrees. `turn_deliverables`
   holds only SwarmWS content. A file in a session worktree is in neither, so it is
   dropped, the card is empty, and it is not drawn.

Root: session worktrees (added 2026-10-24, design `2026-10-24-swarmai-usable-by-anyone.md`
§3.8) were never made a code tree for the readers that already treat run worktrees as one.

## 2. The one fact: which session worktrees belong to session S

Single source: git's own record. `session_worktree create` names the branch
`session/<key>-<name>` with `key = app_session_id.session_key(S)` and checks it out under
`session_worktree.root()`. So the worktrees of S are the directories
`root()/<name>/<repo>` whose HEAD is `ref: refs/heads/session/<key>-...`.

New function `core/session_worktrees.py :: owned(session_id) -> list[str]`: list `root()`
two levels deep, read each `<wt>/.git` (`gitdir: ...`) and that gitdir's `HEAD`; keep the
ones on this session's prefix. No git subprocess. A missing or unreadable entry is skipped
(fail safe: that tree is not shown). `root()` is imported from `scripts.session_worktree`
so the location has one definition. Measured: 0.68 ms with one worktree, in a pool, never
on the event loop.

Nothing is written; no new store.

## 3. Changes

### 3.1 The output card (`core/turn_changes.py`)

- `TurnChanges` already knows `session_id`. Its code-tree discovery (in the
  `turn_snapshot` pool) becomes `code_roots() + session_worktrees.owned(session_id)`. A
  worktree that existed when the turn started is snapshotted at start like any tree, and
  diffed against that snapshot (a commit made in it during the turn does not hide its
  changes, same as today).
- A worktree created **during** the turn (the observed case: created 23:46, turn began
  23:43) has no start snapshot. `_group` today lets only run worktrees go "later". It also
  admits an owned session worktree, diffed from the commit its branch was created at: the
  first entry of the branch's reflog (`git reflog --format=%H refs/heads/<branch>`, last
  line). That is git's record of where the branch began, not HEAD, so commits made in the
  turn still show. No reflog entry: the tree is left off (never a guess).
- Only paths the turn touched are diffed (unchanged rule), so another session's edits
  never show.
- After `land` + `remove` in the same turn the worktree is gone at `finish()`; its diff
  fails and the tree is left off, and the landed commit shows through the existing
  `landed` section. Unchanged behaviour.
- Cost: one extra start snapshot per owned worktree (~0.2 s, `turn_snapshot` pool, in
  parallel with the model's first thinking), the same cost a run worktree has today.
  Nothing on the send path or per token.

### 3.2 Opening a reference (`/workspace/file/resolve` + `useCanvasHost`)

- The endpoint takes an optional `session_id`. For a **relative** path, a new stage runs
  first: for each `owned(session_id)` worktree, `<wt>/<path>` if it is a file. Hit:
  return its absolute path (`GET /workspace/file` already reads `$HOME` paths, no gate
  change). Miss: the existing cascade, unchanged. So the same link opens the session's
  copy before `land` and main's copy after the worktree is removed.
- Absolute paths and other callers are unchanged. The hot-path caller in
  `streaming_orchestrator` passes no session id and keeps its behaviour.
- `useCanvasHost` sends the session id of the tab the click landed on (`detail.sessionId`,
  else the active tab's session). The resolve call is already in the async open path.

### 3.3 The reference reads as a link (`MarkdownRenderer.InlineCode`)

The file chip uses the existing markdown link look (`text-primary`, underline with
`decoration-primary/50`, the same classes as the `a` renderer at line 975), keeps the
type badge and monospace, and gets `title="Open in Canvas"`. Applies to every inline file
reference, not just this one. Links already keep their colour under the attention tiers
(`2026-10-25-turn-attention-tiers.md`: `[data-prose=process]` overrides only
`--color-text`), so this needs no tier change.

Checked on the observed branch: its reflog's last line is
`d2e03702 branch: Created from HEAD`, and this repo has `core.logAllRefUpdates=true`.

## 4. Not in scope

- **The mid-turn document card** (mockup 2026-10-25). It needs this design's §2 to know a
  session-worktree file at all, so it comes after, as its own step with its own design.
- **Canvas rail classification** (`needs_human_review._worktree_roots`, which decides a
  written file's rail kind). Adding session worktrees there changes how existing Canvas
  rows are drawn; not needed for either bug.
- **Another session's worktree.** Only the turn's own session counts. A sub-agent shares
  its parent's session id, so its writes in the parent's worktree count; that is correct.
- **History.** Old turns are not recomputed. A reference in an old message opens by the
  new rule when clicked (resolve runs at click time).

## 5. Acceptance tests (written first, red on today's code)

Backend (`tests/test_session_worktrees.py`, `tests/test_turn_changes.py`,
`tests/test_workspace_resolve*.py`), on temp git repos with `SWARM_SESSION_WORKTREE_ROOT`:

1. `owned(S)` returns S's worktree and not one on another session's prefix, a non-session
   branch, or a broken `.git` file.
2. A file written in an owned worktree that existed at turn start appears on the card with
   correct +/-; a file in another session's worktree does not.
3. A worktree created mid-turn: its file appears, diffed from the branch's creation commit;
   a commit made in the worktree during the turn does not hide it.
4. Worktree removed before `finish()`: no file row, no error, turn result still sent.
5. `resolve?path=docs/x.md&session_id=S`: returns the worktree copy when it exists there,
   even when main also has `docs/x.md`; falls back to the old cascade when it does not;
   without `session_id` the result is exactly today's.
6. `..` traversal and absolute paths behave as today (400 / Stage 0).

Frontend:

7. A click on an inline file chip sends the tab's session id to `/resolve`.
8. The chip has the link classes and `title="Open in Canvas"`; a non-path inline code span
   does not.

Each fix gets a mutation check: revert it, its test must go red.

## 6. Regression guard

- Existing suites that must stay green unchanged: `test_turn_changes.py`,
  `test_session_landings.py`, the resolve tests, `MarkdownRenderer` tests, the chat
  `src/pages/chat` suite. Any test whose rule changes is listed in the commit message.
- Only existing pools are used (`turn_snapshot`, `turn_changes`, the endpoint's thread);
  nothing new on the event loop, the send path or the token path.
- No data migration, no historical data touched.

## 7. Steps (each one commit, ff-merged to local main, not pushed)

1. `core/session_worktrees.owned` + tests 1.
2. Card: discovery + mid-turn base + tests 2-4.
3. Resolve: `session_id` stage + `useCanvasHost` + tests 5-7.
4. Link look + test 8.
