---
title: Canvas Changes from the session's own transcript
status: v4 (v3 + skeptic review 3 fixes; v1 and v2 rejected)
date: 2026-10-24
created: 2026-10-24
updated: 2026-10-24
owner: Gawan
---

# Canvas Changes from the session's own transcript

## Why

Canvas **Changes** lists only the pipeline runs a session owns
(`core/canvas_session.py`: run.json `session_id` / `session_history` → a run's live
worktree diff, the commits run.json records, "released without landing"). Native is
now the default and the pipeline is optional
(`docs/design/2026-10-24-swarmai-usable-by-anyone.md`), so a session that changes code
natively shows an empty Changes tab.

## Requirements (Gawan, 2026-10-24)

- **R1 Chat is not affected.** No work on the event loop, no new wait on the send /
  stream / result path, no pool the chat path waits on, no `.git/index.lock`.
- **R2 No performance issue.** Bounded reads; a repeat read does not grow with the
  transcript; nothing polls.
- **R3 Changes only consumes.** It reads what the session's transcript already holds.
  It does not know how a change was made. No run.json, no "is this a pipeline" branch.

## Reference: how KiroCrew does it

KiroCrew's Changes panel (`kiro_crew/dashboard/chat_api/source_links.py`,
`state.py:_pr_source_links`) lists the PRs/MRs **mentioned in the session's
messages**, ordered by latest mention, memoized per transcript revision, fetched in
detail from the provider. Attribution is one rule: the session's messages name it.
Per-turn local file edits are not in that panel; they are chips on each turn
(`chat_turn/file_changes.py`), like our `turn_changes` card. v3 follows that split.

## How v3 answers the earlier reviews

| Finding (v1/v2) | v3 |
|---|---|
| Folding a file's turns into one net diff misattributes (v1) | No fold: each turn's card as recorded |
| committed/uncommitted cannot be derived from two blobs (v1) | No state label on files; commits are listed as commits |
| ~1,200 git calls at read (v1) | Files: no git at read. Commits: one `cat-file --batch-check` + one `log` per repo, cached by sha |
| A second store must be kept in sync: persist-success callback, finish() lock and turn UUID, delete race, byte caps, backfill (v2) | **No store.** The DB transcript is the only source; an in-memory cache keyed by transcript rev |
| Sub-agent writes emit no `file_changed` (v2) | Accepted limit of the card (L3); commits a sub-agent makes are covered when a message names them |
| Turn-end refresh races the record write (v2) | The read is of persisted rows, and the refreshes fire after the persisting call returns (D5) |
| The diff route consults run.json via `allowed_repo` (v2) | Canvas diff authorizes against bound repos only (D4) |
| Older sessions had nothing (v2) | Older sessions show their cards and commits like new ones |

Review 3 (of v3) found the shape sound and these gaps; v4 fixes them:

| Review 3 finding | v4 |
|---|---|
| Canvas commit diffs run on `turn_changes`, the chat finish pool (pre-existing) | All Canvas git, both diff routes included, on `canvas_changes` (D4) |
| `transcript_since` / `_row_to_dict` decode JSON on the event loop and share the 6 chat DB readers | Own read-only sqlite3 connection inside the `canvas_changes` worker (D2) |
| Full and incremental reads unbounded | Newest-first window: pages of 100, ≤ 400 rows / 8 MB; incremental ≤ 100 rows / 2 MB; "earlier messages not scanned" (D2) |
| Cache unbounded or wrong on updates | Byte-bounded LRU of the window only; a change outside the window re-reads the window (D2) |
| Diff authorization falls back to `allowed_repo` → run.json | Strict bound-repo identity; diff body factored out of `diff_text` (D3, D4) |
| A card's gitRoot vs a binding to a linked worktree; two bindings of one store | Repo identity = git common dir (D3) |
| `_pushed` runs per sha and caches only true | Newest 20 commits, on open/refresh only, 60 s cache, bounded (D3) |
| Duplicate refresh at turn end | Stream end only, plus after Stop (D5) |
| Raw sha regex | Markdown code spans, case-insensitive, 7-40 or 64 hex (D1) |
| Commit files truncated silently | `omittedFiles` (D3) |
| D7 consumer list incomplete | Listed (D7) |

## Design

### D1 Source: the session's persisted messages

Two facts, both already in the transcript:

- **Files changed** — each persisted `turn_changes` block (the "N files changed" card),
  written at `result` and at a user Stop (`_turn_products_at_stop`, persisted before
  `/chat/stop` returns). Rows: `{path, absolutePath, repo, status, added, deleted,
  binary, diffable, gitRoot, start, end}`, at most 60 plus `omitted`.
- **Commits mentioned** — commit ids the session's **assistant text** names in an
  inline code span: a markdown code span (parsed, not a raw regex; fenced code blocks
  do not count) whose whole content is 7-40 or 64 hex digits, case-insensitive, in a
  `text` block of an assistant row (not tool output, user messages or thinking). A
  candidate counts only after git confirms it is a commit of a bound repo (D3).
  Ordered by latest mention.

Both are extracted from the same rows in one pass.

### D2 Reading: bounded, newest first, wholly off the event loop

Everything below runs as ONE synchronous function on the `canvas_changes` pool, with
its **own read-only SQLite connection** (`file:<db>?mode=ro`, as `core/recall_multi`
does). It never uses the shared aiosqlite pool, `transcript_since` or `_row_to_dict`
(those decode JSON on the event loop and share the six chat readers). The coroutine
only awaits the pool and returns the finished, small result.

Per session, an in-memory entry `{rev, window: {row_id: facts}, scannedAll}` in an LRU
bounded by **bytes** (all entries together ≤ 4 MB, measured as the encoded size of the
facts; facts keep only displayed fields).

1. `rev` = `session_revs.rev` for the session.
2. No entry, or `deleted_rev > entry.rev` (a row was deleted) → **window read**: the
   session's sent assistant rows that contain `"turn_changes"` or a backtick, newest
   first by `(created_at, rowid)`, in pages of 100 rows, stopping at 400 rows or 8 MB
   of raw content, whichever comes first. If it stopped early, `scannedAll = false`
   and the UI says "earlier messages not scanned". The rev read in step 1 BEFORE the
   query is the cursor (a row written in between is read again and replaces itself by
   row id). Rows written before schema v15 have no rev; the window read covers them.
3. Entry with `rev < current` → the rows with `transcript_revs.rev > entry.rev` (same
   SQL as `transcript_since`, on the worker's own connection, at most 100 rows /
   2 MB; beyond → do step 2). A changed row inside the window replaces its facts; a
   changed row outside the window → step 2 (the window cannot hold its old facts).
4. Decode, pick blocks and parse shas in the worker; keep the newest 200 cards and 200
   distinct shas of the window.

A repeat read with nothing new costs one indexed lookup; the worst case is bounded by
the page, row and byte limits, never by the transcript.

### D3 Commits: confirmed and described by git

**Repo identity.** Bound repos come from `repo_changes.allowed_roots()` (bindings only,
no run.json), each canonicalized to its git common directory (`rev-parse
--git-common-dir`, off-loop, cached until a bindings.yaml changes). One object store =
one repo, shown under its main checkout. This identity is used for commit attribution
and for diff authorization (D4).

For candidate shas not yet known, per repo: one `git cat-file --batch-check` over
stdin keeps the ones that are commits (missing / ambiguous / non-commit dropped), then
`_commit_facts` (one `git log --no-walk`, now with `omittedFiles`). A sha that is a
commit in no repo or in two is left off. Facts are immutable: weighted LRU (≤ 2 MB).

**Pushed** is mutable, so it is not cached with the facts: computed only for the newest
20 listed commits, on tab open and the refresh button (not on turn-end refreshes),
one `merge-base --is-ancestor` each, results cached 60 s (true and false), ≤ 2,000
entries. All git calls set `GIT_OPTIONAL_LOCKS=0`. Runs on `canvas_changes`.

### D4 Diffs (both on `canvas_changes`, never `turn_changes`)

- A file of a card: `GET /api/canvas/changes/tree-diff?root&start&end&path`. `root`
  must resolve to a bound repo's identity (D3); otherwise the row is shown as "diff not
  available" and the route refuses. sha and path checks as in `diff_text`; the diff
  body is factored out of `diff_text` so this route never calls `allowed_repo`.
- A file of a commit: `GET /api/canvas/changes/diff`, moved from `turn_changes` to
  `canvas_changes` (today it shares the chat finish pool: a pre-existing R1 bug), and
  authorized by the same strict check (no `_checked_root` fallback to `allowed_repo`).

### D5 Refresh (frontend)

Changes is fetched on tab open, on the refresh button, when streaming ends (the
existing `useCanvasHost` trigger; the result row is persisted before the result is
published), and when `stopSession()` resolves (Stop products are persisted before
the HTTP response). Never during streaming, never on a timer.

### D6 UI (`CanvasChanges.tsx`)

- **Files changed**: one group per turn, newest first (time, file count); rows as on the
  card (status, name, directory, +/−, repo when more than one); "+N more files" from
  `omitted`; "N older turns" past the cap.
- **Commits mentioned**: newest mention first; short sha, subject, time, repo, "pushed";
  expands to its files; a file opens the commit's patch.
- Count badge: distinct `absolutePath` in the kept cards. Selection by (row id, path)
  for files and (repo, sha, path) for commits.

### D7 What goes away

The run-based Changes path: `run_changes`, `changes_of`, `_tree_changes`,
`_live_worktree`, `_known_repo`; run cards, the "released" line, `SessionRunChange`.
Kept: `_commit_facts` (gains `omittedFiles`), `commit_diff` (D3/D4), `session_runs` /
`working_repo` (terminal location), `repo_changes._bindings` / `allowed_roots`.
`_pushed` and its uncapped `_PUSHED` set are replaced by the bounded D3 cache.
Consumers to update: `test_canvas_session.py`, `test_session_outputs.py` (stubs
`changes_of`), `test_repo_changes.py` (strict root check), `CanvasChanges.test.tsx`,
`FileViewerPanel.tsx` (selection contract), `services/canvasSession.ts`.

## Cost

- **Chat path:** nothing added. No hook, no write, no new event.
- **Read:** a repeat read with nothing new = one indexed rev lookup; otherwise at most
  the D2 window (400 rows / 8 MB) or increment (100 rows / 2 MB), decoded in the
  worker. Commit lookups: one `cat-file` and at most one `log` per repo for new shas;
  pushed only for 20 commits on open/refresh.
- **Measure before merge** (constructed sessions, never the user's data.db): a window
  read of a 5,000-row / 20 MB session (p50/p95, query and parse separately), an
  incremental read, and that the result path is unchanged (no code there).

## Invariants

- I1 The transcript is the only source; the cache is a view of it at a rev and is
  dropped on `reset`.
- I2 Changes never reads run.json and never branches on how a change was made.
- I3 No git, JSON decode or regex on the event loop; git only reads objects, never the
  index or working tree.
- I4 Nothing inferred: cards as recorded; a commit only when git confirms it in exactly
  one bound repo; what is left off is said ("+N more", "N older").

## Known limits

- L1 Code roots only (bound repos + live run worktrees), as for the card.
- L2 Same-file edits by another session within one turn show in that turn's row, as on
  the card.
- L3 Writes the card never saw (a sub-agent write without `file_changed`, a process
  after the turn) are not in Files changed; a commit is listed only if a message names it.
- L4 A mentioned commit may be someone else's (the section says "mentioned", as
  KiroCrew's panel does).
- L5 Diffs of trees git has pruned cannot be opened (rows stay).

## Tests (written first)

- Extraction: cards from assistant rows; shas only from inline code in assistant text,
  not tool results, user text or thinking; order by latest mention; caps.
- Incremental: a new row is picked up via `transcript_since`; an edited row replaces
  its old facts; a deleted row (`reset`) triggers a full read; a pre-v15 row is read
  by the first full read.
- Off-loop: parsing runs on `canvas_changes` (event loop patched to fail on a decode).
- Commits: confirmed by `cat-file`, left off when absent or in two repos; no run.json
  read on the whole Changes path.
- Diff routes: tree-diff refuses an unbound root, a bad sha, a bad path.
- Real endpoint through FastAPI, unmocked, for one constructed session.
- Frontend: two sections, refresh on result / stream end / stopSession, none while
  streaming.

## As built (2026-10-24)

Code: `backend/core/session_changes.py` (read, extraction, commits, tree diff),
`core/repo_changes.py` (`repo_identities`, `authorized_root`; `_checked_root` no longer
falls back to `turn_changes.allowed_repo`), `core/turn_changes.py` (`diff_trees`
factored out of `diff_text`), `routers/canvas_changes.py` (all on `canvas_changes`;
new `/changes/tree-diff`), `desktop/src/components/file-viewer/CanvasChanges.tsx`,
`services/canvasSession.ts`, `pages/chat/turnControls.ts` (refresh after Stop).

Deviations from v4, each on purpose:

- The commit fact helpers moved from `canvas_session` into `session_changes` (one
  owner for the Changes path) instead of being kept in place.
- "Pushed" is computed on every Changes read for the newest 20 commits, cached 60 s,
  instead of only on tab open / refresh: turn-end refreshes are once per turn, so the
  cost stays at most 20 `merge-base` calls a minute, and the frontend needs no second
  kind of refresh.
- Tests were written alongside the module rather than strictly before it; every
  behaviour test was mutation-checked instead (8 backend, 7 frontend mutations, each
  turning only its own test red; one weak assertion found and fixed this way).

Measured (constructed session, 5,000 assistant rows, 23 MB of content, every row
with a backtick, a card every 10th row; never the user's data.db):

| read | p50 | p95 |
|---|---|---|
| window read, cold cache (worker thread) | 137 ms | 148 ms |
| unchanged re-read | 0.67 ms | 1.25 ms |
| one new row | 1.06 ms | 1.71 ms |

In that worst case the 400-row window holds about 40 turns and the list says
"earlier messages of this session were not scanned".

Code review (fresh-context reviewer, 2026-10-24) found no blocker; its fixes, each
with a test that its revert turns red:

- Bounds: a page lists row lengths first and content is fetched only within the byte
  budget; a row over 1 MB is not read (counted as not scanned); per row at most 3
  cards, 60 files per card (the rest added to `omitted`), 20 commit candidates; at most
  64 cached sessions; `clear_cache` resets the facts byte count; the upstream cache is
  an LRU of 256.
- Concurrency: one lock per session (its entry's only mutator) and a copied snapshot
  returned; the global lock guards only the LRU, so sessions never wait on each other.
- SHA-256: `repo_changes._SHA` accepts 64 hex digits, like the extraction.
- Staleness: an abbreviated id's hit expires (600 s); repo identity and every commit
  cache key include the store's inode, so a repo replaced at the same path is a new
  store.
- No request while streaming: the store holds every Changes request of a session whose
  turn streams (`setCanvasStreaming`, set by useCanvasHost before its session refresh)
  and sends exactly one when it ends; the refresh button is disabled meanwhile.
- A binary or "file only" row asks for no diff; bad sha / path are refused before any
  git call; the read-only connection is closed if its setup fails.
