---
title: Session landings read from git's own stamped record
status: implemented v9 (2026-10-25; commits 562b28830, 0f70c5231, 979d7ac22, 38d3702de, 770c92563, 8e4eb3d94, a15ec88cc, acefc5cbb)
date: 2026-10-25
created: 2026-10-25
updated: 2026-10-25
owner: Gawan
---

# Session landings read from git's own stamped record

## 1. Goal and scope

A native coding session that lands its work through `session_worktree land` should end
its turn with a card that says which commits landed on main and which files they
changed. Today the card is empty and the user finds a sha in prose.

**In scope (v8, minimal):** landings made by `session_worktree land`, on the turn card
and on Canvas; the frontend fix so a card with only landings is shown; removing
Canvas "Commits mentioned".

**Out of scope, by decision (2026-10-25):** unlanded work in session worktrees, other
updates of main, in-place commits, and the durability of card persistence. Each was
tried in v4-v7 and could not be read from a record git keeps (§9). They are left as
they are today and may get their own design later.

Principles kept (`docs/design/2026-10-24-swarmai-usable-by-anyone.md`): no gate is
added and nothing is denied (P1, P3); the switch to a worktree stays the agent's choice
(§3.8); nothing recognises the SwarmAI repo (P7).

## 2. The rule

Every fact shown is either a value git wrote, or a value `session_worktree` wrote into
git's own record at the moment it acted. Nothing is inferred from timing, adjacency,
overlap or merge-base. What has no such record is not shown.

## 3. The record

### 3.1 What `land` stamps

`land` already pins the base tip `target`, rebases the branch onto it, runs
`check_no_revert`, re-checks the tip and runs `merge --ff-only` (`56df81144`). v8 adds
one environment variable to that merge:

```
GIT_REFLOG_ACTION="swarm-land <branch> session=<s> turn=<turn id> onto=<target sha>"
```

and runs the merge with `-c core.logAllRefUpdates=always`, so the destination branch
gets a reflog entry even in a repo that turned reflogs off. After the merge, `land`
checks `git reflog exists refs/heads/<dest>`; when it does not, its JSON says
`"record": "missing"` (the landing happened; nothing will show it).

Git writes the stamp, under its own ref lock, into the destination branch's reflog entry:
`swarm-land <branch> session=<s> turn=<id> onto=<sha>: Fast-forward` (checked on git
2.48.1, files and reftable backends, with spaces and `=` in the action). The entry's
own sha is `new`, the commit the branch now points to, which is the session branch head.

- `session=<s>`: first 12 hex chars of `sha256(session id)`. Attribution reads this
  field, never the branch name, so a worktree created before v9 (`session/<name>`)
  still lands attributable. `create` names new branches `session/<s>-<name>` for the
  user's benefit only.
- `turn=<id>` is the turn id of §3.2, or `turn=none` when the call has none.
- `onto=<target>` is the commit the branch was rebased onto.
- `<dest>`: the main checkout's current branch, read by `land` as today. The card shows
  that name; nothing assumes it is called `main`.

**What the record claims:** the commits `target..new` of this session's branch were
merged into main in that turn. It does **not** claim main moved from `target`: another
writer can move main between `land`'s tip re-check and the merge, and the reflog's
old sha is not exposed by `git reflog show` (v6/v7 reviews). Whatever main held before,
`target..new` is exactly this branch's rebased commits, and after a successful
ff-merge `new` is on main.

"Already up to date" writes no reflog entry and therefore no landing. `land` reports
that in its JSON (`"landed": false`).

### 3.2 The turn id

- `SessionUnit._reset_turn_state()` (run at every turn admission: `send()` and
  `begin_injected_turn`) sets `_hook_session_context["_turn_id"] = uuid4().hex`. A
  question or permission continuation is the same turn and keeps it. The
  `turn_changes` block carries it as `turn_id`.
- `run_owner_gate`, the one Bash hook allowed to return `updatedInput`, gains a
  separate, strict rewriter for `session_worktree` calls. It does not use
  `_token_binding` or `_with_session` (those accept a heredoc head before a pipeline
  call) and leaves the pipeline path unchanged. It rewrites only when the line's first
  segment is exactly `"$SWARM_BACKEND_BIN" py -m scripts.session_worktree
  <create|land|remove> ...` (the form `shared_checkout.py` prints) or
  `python|python3 -m scripts.session_worktree ...`, with no heredoc, no substitution and
  no wrapper in front. It prefixes `SWARM_APP_SESSION_ID=<id>` and, **only for a call
  from the main agent**, `SWARM_TURN_ID=<turn id>`. A call whose hook input carries an
  `agent_id` (a sub-agent, which can outlive the turn that started it) gets no turn
  id, so its landing can never be stamped with a later turn (v8 review B1). The
  rewriter never denies. `pipeline_command.mentions / is_create / is_land` do not
  change; the cheap prefilter gains one substring (`scripts.session_worktree`).
- `session_worktree` validates both: the session id with the existing app-session
  grammar `[A-Za-z0-9_.:-]{1,128}` (today duplicated in `RunEngine._create` and
  `run_owner_gate`; moved into one new helper, not `core.session_ids`, whose grammar is
  for SDK ids), the turn id as 32 lowercase hex chars. An invalid or missing value
  becomes `none` and the JSON says so (`"attribution": {"session": ..., "turn": ...}`).

A call that is not the first command of its line (`cd x && ... land`), or that comes
from a sub-agent, gets no turn id: its landing is on Canvas, not on a turn card.
`land` reports `"turn": "none"`, so the agent sees it.

**Counting (built, `acefc5cbb`).** For each `land` it hands the turn id, the rewriter
adds 1 to `_hook_session_context["_turn_lands"]` (reset to 0 with the turn id; the
verb comes from the parsed first segment). `TurnChanges` reads landings only when that
count is above 0, so a turn with no land runs no git at its end, and passes it as
`expect` (§4).

## 4. The reader: `backend/core/session_landings.py`

One module, one deadline-aware git runner (`GIT_OPTIONAL_LOCKS=0`, `LC_ALL=C`, each
call's timeout = time left on one shared deadline; no call starts after it ran out; a
cancelled future does not stop git, so the per-call timeout bounds it). Repos come from
`_bound_worktree_roots()` only.

`landings(session_id, turn_id=None, deadline)` per bound repo:

1. `symbolic-ref -q HEAD` gives the checkout's branch `<dest>`; detached or unborn: the
   repo is reported `unknown: no-branch`.
2. `git reflog exists refs/heads/<dest>`; when it does not exist the repo is reported
   `unknown: no-reflog` (never an empty "no landings").
3. `git reflog show --date=unix --format=%H%x00%gs%x00%gd -n <cap> refs/heads/<dest>`.
   An entry is a landing of this session when its message matches exactly
   `^swarm-land (session/[A-Za-z0-9._/-]+) session=([0-9a-f]{12}) turn=([0-9a-f]{32}|none) onto=([0-9a-f]{40}): Fast-forward$`
   with `session=` equal to this session's `<s>`. With `turn_id` given, only entries
   with that turn id. No other entry is read for meaning, and no entry's meaning depends
   on its neighbour.
4. Per landing (`onto` = `T`, entry sha = `N`), each a git fact checked now:
   - `merge-base --is-ancestor T N` (exit 128 also covers an unknown `T`); failing,
     the landing is dropped and counted in `unverified` (a forged or damaged stamp);
   - commits: `rev-list --count T..N`, `log --format=%h%x00%s -10 T..N`;
   - files: `diff --numstat -z --no-renames T N` and `diff --name-status -z --no-renames
     T N` (the two existing parsers), rows with `start = T^{tree}`, `end = N^{tree}`
     (one `rev-parse`), `gitRoot` = the bound checkout, so the existing
     `/canvas/changes/tree-diff` and turn-diff endpoints serve them unchanged;
   - chip: **on `<dest>`** when `merge-base --is-ancestor N <dest>` holds, else **not
     on `<dest>`** (the branch was reset or rewound since).

That is 3 calls per repo plus 7 per landing. `code_roots`, `_worktree_roots` and
`allowed_repo` do not change.

`<cap>`: the turn card reads 200 entries; Canvas reads 2000. When the read returns
exactly `<cap>` entries, the result is `truncated` whether or not something matched:
completeness is proved only when the reflog ended before the cap (v8 review), or, for
a turn, when it found `expect` stamps (the lands the gate handed this turn's id, §3.2).
Measured on a clone of this repo: main's reflog has ~1700 entries, so without
`expect` every turn read hit the cap and every card said "more landings may exist".
Canvas's 30-day cutoff uses the reflog date in the selector (`%gd` with
`--date=unix`), never a commit date, judged on every entry (not only this session's);
reaching 30 days before the cap is complete. A turn reads at most `MAX_LANDINGS` (4);
a fifth is `truncated`. The limit and `expect` are the session's, not each repo's:
every bound checkout is scanned (one reflog read each), the stamps are merged newest
first by reflog date, and only the ones shown are expanded (the 7 calls). The turn end
has one `FINISH_BUDGET_S` deadline for its rows and its landings together.

## 5. Where it is shown

### 5.1 Turn card

At `finish()` (the existing call on `result` and on Stop), `TurnChanges` calls
`landings(session, turn_id, expect)` on the `turn_changes` pool inside
`FINISH_BUDGET_S`, at most `MAX_LANDINGS` (4) expanded. The block gains
`turn_id`, `landed: [{repo, gitRoot, dest, branch, turn, onto, new, count, subjects,
state, files, omitted}]`, `landed_truncated`, `unverified`,
`unknown: [{repo, reason}]`. One total row budget, `MAX_FILES`, spans the in-place
rows and all landed groups. `finish()` no longer returns `None` early when no file
was written but a landing, an `unknown` or a `truncated` exists.

No turn window is computed: the turn id in the stamp is the attribution. A landing
made by a sub-agent is stamped `turn=none` (§3.2) and is on Canvas only. Nothing has to
be reconciled.

**`finish()` is computed once per turn (v8 review M1).** Today it swaps its file set
out before its first await, so a second call (Stop and `result` both reach it) returns
a block without the in-place rows, and "last block wins" in the frontend would then
drop them. v9: the first call starts one shielded task; every call of that turn awaits
it and gets the same block (a copy). Nothing else about persistence changes.

**Persistence order at Stop (fixed separately, `562b28830`).** Stop writes the
running turn's held rows, the receipt and, after the interrupt, the turn's products;
a message sent meanwhile is stored at once, so it could land before the receipt or
between the receipt and the products, and the card then showed under the next turn.
`core/transcript_order` holds one asyncio lock per session: the Stop holds it from the
flush to the products' write, and the send path writes the user row under it (waiting
at most 30 s, then writing anyway, so a hung Stop delays a message's place but never
loses it). The rows are still separate new rows, as the live transcript feed expects.
(A first attempt wrote the products into the receipt's own row; review 9 rejected it:
it left the window before the receipt open, and the feed drops a same-row update of
blocks with no identity.) With that, and with one block per turn, the frontend's
existing "last `turn_changes` block of the turn wins" (`turnView.ts`) sees identical
blocks. A swallowed DB failure still loses that card, as it does today (R3).

### 5.2 Frontend

- `turnView.ts` kept a `turn_changes` block only when `files` was non-empty, and so
  did `AssistantMessageView` after dropping deliverables from the rows. Both use one
  predicate, `turnView.hasTurnChanges(block)`: true when `files`, `landed` or
  `unknown` is non-empty, `unverified > 0` or `landedTruncated`.
- `ChangesCard` renders **Landed** groups first (header `<branch> → <dest>`, commit
  count and subjects, chip **on `<dest>`** / **not on `<dest>`**, rows), then today's
  rows, then one line each for `unknown` ("could not read landings in <repo>:
  <reason>"), `landed_truncated` ("more landings may exist, see Canvas") and
  `unverified` ("N landing records could not be verified"). Its title: "N files
  changed · M commits landed", or "M commits landed" alone. A block without the new
  fields renders as today.
- One `toCamelCase` mapping in `services/chat.ts` serves the SSE and history routes.
- An integration test goes through `buildTurnView` and `TurnCards`, not only the
  component.

### 5.3 Canvas

`session_changes.read` returns
`{turns, olderTurns, scannedAll, landed, landedTruncated, unverified, unknown}`:
`landed` = `landings(session)` with no turn id, newest 20 within 30 days. Each landed
entry has the stable key `land:<repo>:<new>`; its file rows open through
`/canvas/changes/tree-diff` with `T^{tree}` and `N^{tree}`. Files changed still come
from the turn cards. The Canvas cache key adds each bound repo's branch tip. Canvas
renders the same `unknown` / truncated / unverified lines as the card.

## 6. Cost

| path | added git calls | where |
|---|---|---|
| send | none | — |
| each streamed token | none | — |
| each Bash call | `run_owner_gate` cheap prefilter: one more substring (`scripts.session_worktree`); no git | already in the hook |
| `session_worktree land` | 1 (`reflog exists` after the merge); the stamp is one env var and one `-c` on the existing merge | the agent's own command |
| turn end, no land handed the turn id | none | — |
| turn end, with lands | 3 per bound repo (`symbolic-ref`, `reflog exists`, one reflog read of ≤ 200 entries), until `expect` stamps are found | `turn_changes` pool, `FINISH_BUDGET_S` |
| turn end, per landing (≤ 4) | 7 | same |
| Canvas | 3 per bound repo + 7 per landing (≤ 20); cached per session while the bound checkouts' branch tips stay put, at most 60 s | `canvas_changes` pool |

Measured 2026-10-25 on a shallow clone of this repo plus 300 extra commits (median of
5 reads; `scratch/measure_landings.py`): a turn with 1 landing 155 ms, with 4
landings 510 ms (budget 10 s); Canvas with 20 landings and the 2000-entry cap 2.5 s,
off the chat path. A 2000-entry reflog read alone is ~230 ms. Before `expect`, every
turn read also paid the full 200-entry scan.

Tests: `tests/conftest.py` pins `session_landings._bound_checkouts` to `[]` for every
test (in tests `get_swarmws()` is the live workspace; a card or Canvas read must never
run git in the user's real repos).

## 7. What is deleted (full shape)

- v1-v8 of this design (none was built).
- **Canvas "Commits mentioned"**, end to end: in `session_changes` `code_span_shas`,
  the prose scan in `row_facts` / `_read_window`, `_confirm`, `_facts`, `_pushed`,
  `_commits`, and `commits` in `read()`; the route `GET /api/canvas/changes/diff` in
  `routers/canvas_changes.py` and `repo_changes.commit_diff` (its only product caller;
  `allowed_roots`, `repo_identities`, `authorized_root`, `RepoChangesError` and the
  tree-diff authorization stay, they have other callers); in the frontend
  `MentionedCommit`, `commits` in `SessionChanges` and `NO_CHANGES`,
  `canvasSessionService.commitDiff`, and the Commits mentioned list, pushed chip,
  selection and detail view in `CanvasChanges.tsx`, whose empty check and count then
  read `turns` and `landed`.
- Their tests: the commit cases in `backend/tests/test_session_changes.py`, the
  `commit_diff` cases in `backend/tests/test_repo_changes.py`, the route tests, and
  `CanvasChanges.test.tsx` plus service fixtures.
- Stale descriptions: the module docstrings of `backend/core/repo_changes.py`,
  `backend/routers/canvas_changes.py` and `backend/core/canvas_session.py`, and
  `docs/design/canvas-changes-from-turns.md` (updated, with a pointer here).
- The early `return None` in `TurnChanges.finish()` when no file was written.

`shared_checkout`, the file tracker and `check_no_revert` stay as they are.

## 8. Acceptance tests (written first, red on current code)

1. `create` → edit → commit → `land` in one turn: one Landed group with `onto..new`,
   its commits and rows, chip **on `<dest>`**; the reflog message matches §4.3 exactly.
2. Main moves to a commit between `onto` and our head before our merge: ff-only
   succeeds and the landing lists only `onto..new`; main moves elsewhere: ff-only
   refuses and there is no landing.
3. "Already up to date": no landing, `land` JSON says `"landed": false`.
4. Two landings in one turn and one in the next: each on its own turn's card only,
   including same-second updates and a `GIT_COMMITTER_DATE` in the past.
5. Reset back and forth on main, and `git reflog delete` of a middle entry, between
   landings: every landing still shows the same `onto..new`.
6. A turn that only landed: the card is built, kept by `buildTurnView`, title
   "1 commit landed".
7. `finish()` called by Stop and by `result`, in both orders and concurrently: both get
   the same block, in-place rows included.
8. A sub-agent's `land` (hook input with `agent_id`), during and after its parent turn:
   stamped `turn=none`, on no turn card, on Canvas.
9. Prewarmed, then adopted session: the printed `"$SWARM_BACKEND_BIN" py -m
   scripts.session_worktree land ...` as the first command gets the adopted session id
   and the turn id. Not rewritten: `cd x && ... land`, `echo scripts.session_worktree
   land`, a heredoc head before `land`, a `$(...)` substitution, a wrapper in front.
   `pipeline_command.mentions / is_create / is_land` and the pipeline rewrite are
   unchanged for all of them.
10. Session id grammar: the shared helper accepts and rejects exactly what today's
    `RunEngine._create` does.
11. A worktree created before v9 (`session/<name>`) and landed after: matched by its
    `session=` field.
12. Forged stamps (`onto` unknown or not an ancestor, another session's `session=`,
    malformed turn id): dropped or not matched; forged ones counted in `unverified`,
    and shown.
13. A repo with `core.logAllRefUpdates=false`: `land` still writes the stamped entry
    (`-c core.logAllRefUpdates=always`); with the reflog removed afterwards, the card
    says `unknown: no-reflog`, never an empty list.
14. A reflog read that returns exactly the cap: `landed_truncated`, even with a match.
15. Main reset below a landing: chip **not on `<dest>`**; a checkout on a branch not
    called `main`: the card names that branch.
16. Reftable repo: tests 1 and 4 pass unchanged. Detached HEAD: `unknown: no-branch`.
17. `code_roots()`, `_worktree_roots()`, `allowed_repo()` unchanged; old persisted
    blocks render as today; Canvas has no Commits mentioned and no
    `/api/canvas/changes/diff`.

Each fix is mutation-checked (revert it, its test goes red).

## 9. Residuals and what is left out

- **R1** A landing from a sub-agent, or from a `land` that is not the first command of
  its line, is on no turn card; Canvas shows it.
- **R2** Stamps are not authenticated: anyone with the session's Bash can write a
  message naming this session. Every shown fact (`onto`, `new`, commits, files, chip)
  is checked against git; a forged stamp can at most attribute a real, ancestry-valid
  range to this session. Display only.
- **R3** A swallowed DB failure still loses that turn's card (as it loses today's file
  card); Canvas still shows the landing. Ordering at Stop is fixed (`562b28830`).
- **R4** A `land` handed the turn id that lands nothing (refused, or up to date) is
  counted, so the read finds fewer stamps than `expect`; on a reflog longer than the
  cap the card then says more landings *may* exist. Never a false landing.
- **R5** Canvas's landing cache keys on the checkouts' loose ref files; a packed ref
  reads as unknown, so that repo's landings are re-read after at most 60 s.
- **Left out (v4-v7 found no git record to read):** unlanded session-worktree work,
  other updates of main and in-place commits, a turn window over the reflog. In-place
  file rows stay as today.

## 10. What the check confirmed (review 9, then implementation)

1. A sub-agent call never carries a turn id (`agent_id` is set only inside a
   Task-spawned sub-agent in the pinned SDK). The Stop products are ordered by the
   `transcript_order` lock, not by sharing the receipt's row (review 9 rejected that).
2. The strict rewriter is not reached by a heredoc head, a substitution, a `cd`, a
   wrapper or an assignment, and the pipeline rewrite is unchanged.
3. `-c core.logAllRefUpdates=always` on the merge creates the destination's reflog
   entry in a repo that turned reflogs off (files and reftable), and `reflog exists`
   is the "unknown" test.
4. Computing `finish()` once per turn (one shielded task, a copy per caller) keeps the
   in-place rows.
5. Nothing shown is inferred; nothing blocks native coding or recognises the SwarmAI
   repo.
