---
title: What reads a session's landings besides the turn card
status: implemented v4 (2026-10-25; three design reviews)
date: 2026-10-25
created: 2026-10-25
updated: 2026-10-25
owner: Gawan
---

# What reads a session's landings besides the turn card

## 1. Why

`docs/design/2026-10-25-turn-result-card.md` (implemented) gave every
`session_worktree land` one record: the destination branch's reflog entry, stamped
`swarm-land session/<key>-<name> session=<key> turn=<id|none> onto=<sha>`
(`core/session_landings`). The turn card and Canvas "Landed" read it. Three other
places miss it or guess instead:

| # | Where | Today | Effect |
|---|---|---|---|
| D1 | Opening a file recorded inside a session worktree (Canvas Artifacts rail, a turn card's in-place row) after `land` + `remove` | every read route opens the recorded path | "File not found"; Artifacts shows the row gone, no NEW/UPD badge. A released pipeline-run worktree is already mapped (`run_worktree.landed_path`); a session worktree is not |
| D2 | Terminal's default location (`session_changes.working_repo`) | bound main checkouts named by the newest card's **file rows** only | a turn whose card holds only a landing (files written by Bash in a worktree) names no repo: the terminal opens an older card's repo or SwarmWS |
| D3 | ToDo hook (`hooks/todo_lifecycle_hook.py`) | `git rev-list --count --since=<session start>` and `git log --since` in SwarmWS and one code repo | every session's commits in that window count as this one's; a bound ToDo is set `handled` (Completed) and its notes list other sessions' commits |

D3 is a guess the change discipline forbids. D1 and D2 are missing reads of records
that exist. Principles kept: no gate, nothing denied, nothing SwarmAI-specific, the
worktree stays the agent's choice (usable-by-anyone P1/P3/P7).

Review 1 (v1, rework) found: D1 could map to the wrong checkout and widened the
external-path allowlist, and covered only two of five read routes; D2 compared a
reflog date git takes from `GIT_COMMITTER_DATE` with a naive local message time; D3
turned "a file was touched" into "Completed", breaking the ToDo invariant that only
Confirm sets `handled`, and dropped SwarmWS outputs. Review 2 (v2, rework) found: D1's
exact-basename rule is not a name identity on a case-insensitive file system; D2
leaked root-only cards into Canvas; D3 double-counted one file across sources, could
claim an exact count after a silent cap, and could reopen a finished ToDo. Review 3
(v3) found: a delete racing the hook can move a ToDo to `in_discussion` that had work;
implicit matching over every repo widened the old basename match. v4 below.

## 2. D1 — a removed session worktree's file opens from the main checkout

**Rule (fail closed).** A read of path *p* that does not exist maps to `<main>/<rel>`
only when all hold:

1. *p* is under `session_worktree.root()` with parts `<name>/<repo dir>/<rel>`, and
   `run_worktree.run_for_path(p)` is None (never a pipeline path, also when the two
   roots are configured to overlap);
2. the candidates are the bound main checkouts (`session_landings._bound_checkouts`)
   whose basename equals `<repo dir>` after Unicode NFC and `casefold()` (a
   case-insensitive volume names one directory by either spelling). More than one
   candidate maps nothing, whatever their exact spelling;
3. the one candidate's current-branch reflog (`CANVAS_CAP` entries) holds a stamp
   whose branch is `session/<k>-<name>` (or `session/<name>` for `none`), *k* the
   request's session key when it has a `session_id`, any key otherwise; stamps of two
   different keys for that name map nothing. Several stamps of ONE branch (landed
   twice, or the name reused by the same session) all name the same checkout, so the
   same `<rel>` maps to the same file: that is not an ambiguity;
4. `rel` has no `..`, `<main>/<rel>` resolves under *main* and is a file.

The map grants no access: the mapped target passes the route's own guard on its own
(home-only, or its own exact membership in `allowed_external`; the original path's
membership does not carry over). A main checkout outside `$HOME` is not mapped.

The file shown is main's current version, as for a pipeline run (pipeline-run-worktree
§6.4); a turn card's diff still shows the turn's own trees.

**Routes (read-only, all five).** `GET /workspace/file`, `/file/preview`,
`/file/meta`, `/file/raw`, `/file/committed` resolve through `_resolve_file_path`
with `map_landed=True`, which tries `run_worktree.landed_path`, then
`session_landings.landed_path(p, session_id)`. Write routes are unchanged. Of the
callers, only `/file` and `/file/preview` already send `session_id` (the Artifacts
rail does; a turn card's open does not; `rawFileUrl` deliberately sends none); the
others use rule 2's "any key" and so map only an unambiguous name.

**Artifacts.** `canvas_session.artifact_rows(rows, workspace, session_id)` computes
`exists` and `inHead` on the mapped path when the recorded one is gone. The row keeps
its recorded `absolutePath` (its identity in session_outputs) and opens through the
routes above: one mapping, in one place.

**Cost.** Only for a missing path under the session-worktree root: per candidate
checkout one `symbolic-ref`, one `reflog exists`, one reflog read (~230 ms for 2000
entries, measured), under a 2 s deadline. An LRU of 64 entries keyed on
`(checkout, name, key)` and that checkout's branch tip (`session_changes._tips`
reading) avoids repeats. Never on send or the stream.

## 3. D2 — the newest card that names a repo, landings included

The record is the card itself: since the turn-result-card change a `turn_changes`
block also carries `landed[]` with each landing's `gitRoot`. No reflog read and no
time comparison are added.

`session_changes.row_facts` keeps, per card, `roots`: the card's landed groups'
`gitRoot`s in order, then its file rows' `gitRoot`s. Two projections of the cached
window: **all cards** (any `roots`) for `working_repo` and D3; **display cards**
(non-empty `files`) for `read()`'s `turns` and `olderTurns`, which counts display cards
only. A landing-only card is therefore never a "0 files" turn on Canvas; its landings
are listed under "Landed". `working_repo` walks cards newest first, each
card's `roots` in order, and returns the first bound main checkout (as today:
`authorized_root`, `.git` a directory). Within one card, a landing comes before an
in-place row (the card shows it first).

A landing made by a sub-agent or by a `land` that is not first on its line is on no
card (turn-result-card R1) and does not move the terminal (R3 below). Cost: none
added; the same cached window as today.

## 4. D3 — the ToDo hook reads the session's own records and never completes a ToDo

**Outcome.** One read, `session_outcome(session_id) -> {files, commits, partial}`:

- source edits: every file row of the session's cards (`session_changes._window`, on
  the `canvas_changes` pool);
- SwarmWS and surfaced outputs: `session_outputs.read` rows not marked `deleted`, on
  the single-worker `canvas_session` pool (so writes queued before it are seen, the
  store's ordering contract);
- landings: `read_session(cap=CANVAS_CAP)`, no day limit, at most 20, on
  `canvas_changes`: each landing's commits (`count`, `subjects`) and files.

A file's identity is `(realpath gitRoot, path)` for card rows and landing files
(both name the main checkout: a card row's `gitRoot` is `_main_checkout`), and the
realpath of `absolutePath` for an output. One file recorded both as a card row and as
an output can still be two entries, so the count is never called exact: the notes say
"N recorded files". `partial` is true when the window did not scan everything, a card
has `omitted`, the outputs hold `MAX_ROWS` rows (the store drops the oldest silently),
the landings read is truncated or unknown, or any part could not be read; the notes
then say "at least N recorded files".

**Explicit binding (a ToDo dragged into the chat).** The hook no longer sets
`handled`: the ToDo model's locked invariant is that only Confirm sets it
(`routers/todos.py` review). It changes only an active ToDo (`pending`, `overdue`,
`in_discussion`); `handled`, `cancelled` and `deleted` are left as they are. Each write
is one conditional UPDATE (new `db.todos.update_if(id, updates, status_in=...,
review_state_null=True)`), so a person's Confirm / Reject meanwhile wins. With an
outcome (any file or commit): `review_state = 'completed'`, `completed_at = now`,
status unchanged, as `LifecycleManager._sweep_auto_complete_todos` does, and the notes
are written: "Session outcome: N recorded files" (or "at least N recorded files"), then per landing
`<branch> → <dest>` and its first subjects, at most 10 lines. Without one:
`in_discussion` (the same conditional write), as today. **Behaviour change:** a session with commits used to set
`handled` directly; it now goes to review (③ Completed), where the user confirms.

**Implicit matching.** The pool keeps today's scope, SwarmWS plus the session's one
working repo (`working_repo`, D2), so the old basename rule of `_files_overlap` (kept
as is, an existing aid that only moves a pending ToDo to `in_discussion`) is not
widened to every bound repo: the outputs' paths, and the repo-relative paths of the
card rows and landing files whose `gitRoot` is that working repo.

Removed: `_get_session_commit_count`, `_get_session_changed_files`,
`_get_oneline_commits`, the `since` they take (session start, or "2 hours ago"), and
`_session_code_repo`. A record that cannot be read gives an empty part of the outcome,
sets `partial` and is logged; the other parts still count.

**Deleted sessions.** `DELETE /sessions/{id}` enqueues the hooks and deletes the
messages, the session row and its outputs right after, so a hook that started first
can read a half-deleted session and see "no outcome". `HookContext` gains
`deleting: bool = False`; the delete route sets it, and the ToDo hook returns at once
for it: a deleted session changes no ToDo, deterministically (today it also does
nothing in practice: the row it reads is usually gone). Other hooks ignore the field.

## 5. Tests (written first, red on current code)

- D1: land + remove, then `landed_path(<wt>/b.py, sid)` → `<main>/b.py`; None for:
  another session's key, two keys for one name without a session id, two bound
  checkouts with the same basename, a basename differing only in case, a name with
  no stamp, a `..` path, a pipeline worktree path under an overlapping root, an
  existing path. Per route class (`/file`, `/file/preview`, `/file/meta`,
  `/file/raw` with a binary file, `/file/committed`): the removed path is served;
  an allowlisted removed path whose mapped target is outside `$HOME` is refused.
  Artifacts `exists` / `inHead` on the mapped path.
- D1 (review 2): two bound checkouts whose basenames differ only in case map nothing;
  the same session landing one name twice (two stamps, one branch) still maps.
- D2: a card with only a landing names that landing's repo; a mixed card names its
  landing's repo; a newer card without roots does not hide an older card's repo; a
  landing-only card is neither in `turns` nor counted in `olderTurns`.
- D3: another session's commit in the same hour leaves a bound ToDo `in_discussion`;
  a card row gives `review_state='completed'`, status not `handled`; a SwarmWS output
  from session_outputs counts; a landing's subjects are in the notes; a partial
  window says "at least"; implicit match from a landing's file and from an output;
  no `git log --since` / `rev-list --since` runs (subprocess spy); a `handled` or
  `cancelled` bound ToDo is not changed; a Confirm between the hook's read and write
  wins; one file as a card row and a landing file is one entry; 300 outputs or an
  unreadable outputs store say "at least"; a context with `deleting=True` changes
  nothing; a basename match in a second bound repo (not the working repo) does not
  move a pending ToDo.
- Replaced on purpose (`tests/test_todo_lifecycle_hook.py`): "commits → handled",
  "commit lookup in SwarmWS plus the code repo", "unreadable change record still
  checks SwarmWS"; their SwarmWS and failure coverage moves to the new tests above.

Each fix gets a mutation check; the selected backend suites
(`swarm_select_tests.py`) and the touched frontend tests run.

## 6. Residuals

- **R1** D1 without a session id maps only an unambiguous name; a name reused in one
  repo by two sessions opens nothing from a turn card or a raw URL.
- **R2** D1 shows main's current version, which may differ from the turn's.
- **R3** D2: a landing on no card (sub-agent, not first on its line) does not move the
  terminal.
- **R4** D3 reads a bounded transcript window and at most 20 landings: the rest is
  left out and the notes say "at least".
- **R5** A session worktree removed without `land` (only for work merged by hand) has
  no stamp: its files stay "not found".
- **R6** D3 does nothing for a session deleted before its hook ran (as today).
- **R7** D1: two bound repos whose names differ only in case open nothing from a
  removed worktree. A worktree path that carried a repo key would remove R1 and R7;
  left out (it changes `create`'s layout and `shared_checkout`'s printed command).
