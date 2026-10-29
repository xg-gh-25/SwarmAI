---
title: "Code intel r4: fully automatic once a Brain has a repo"
created: 2026-10-29
updated: 2026-10-29
status: built (D1–D7), reviewed (security on D1+D2, correctness on D3–D5), fixes in
---

# Code intel r4: fully automatic once a Brain has a repo

The person gives a Brain its repository. They do nothing else, ever: the graph is
built, kept current, rebuilt when the parser changes, reaches every review (native
and pipeline), and its use is measured. The Brain Hub graph view stays, as the
visible proof of the capability and of the knowledge it holds.

Follows `2026-10-29-code-intel-unification.md` (r3, §0.9–§0.10). This changes one
r3 decision: ownership of a local checkout no longer waits for a person's Confirm.

## 1. What stands in the way today (read at source, 2026-10-29)

| # | Gap | Where |
|---|---|---|
| G1 | A local checkout owns nothing until a person clicks Confirm in Brain Hub; existing installs hold `pending` entries that only that click promotes. | `local_roots.propose` writes `pending` (`core/code_intel/local_roots.py:203`); `confirm_local_root` (`routers/ddd_brain.py:1856`) is the only promotion and starts the build |
| G1b | The TECH.md suggestion runs only when someone opens the Brain Hub card. | `suggest_from_tech_md`, sole caller `_local_roots_view` (`ddd_brain.py:1831`) |
| G2 | New Brain files a local repo folder as SHELF unless the person flips its role pill; a local path containing `github.com` reads as a remote URL. | `desktop/src/utils/newBrainDispatch.ts:37` `GIT_URL_RE` (unanchored), `:48` `detectKind`, `classifyStarterItem` (from `:81`) |
| G3 | The New Brain manifest tells the agent to bind; `bind --local` stops at pending; `bind` of a local path clones it. | `NewBrainOverlay.tsx:132`, `newBrainDispatch.ts:168-180`; `s_project-manager/SKILL.md:248,551`; `ddd_bindings._bind_locked` (`:537`) |
| G4 | A graph built by an older parser reads as fresh, in the CLI (`check_freshness`) and in the daemon (`head_stamp`, which compares HEAD only). | `core/code_intel/freshness.py:53`, `:234`; callers `routers/code_intel.py:105`, `core/recall_multi.py:378` |
| G5 | Pipeline expert reviews get no callers. | `RunEngine.review_start` `review_input` (`core/run_protocol/engine.py:2630-2645`); incremental `:2779-2794`; round 2 `review_address` `:1205`, `:1261`. No code-intel reference in `core/run_protocol/` |
| G6 | One code-intel golden case (`CI_IMPACT_FRESH`, freshness only); the product set is at its cap of 50. | `backend/templates/eval/golden_set.yaml:534`; `core/golden_set_store.py:36` |
| G7 | Nothing records whether the callers report is used or helps. | `core/code_intel/changed.py` |
| G8 | The Brain Hub graph shows the first graph only. | `desktop/src/services/codeIntel.ts:87` sends no `repo`; `project_graph(project, None)` → `graphs[0]` |

## 2. Decisions

### D1. Ownership: a root declared on this machine is owned at once (replaces r3's Confirm)

| | What | Effort | What it gives up |
|---|---|---|---|
| A | **Declared = owned.** `bind --local` (and New Brain through it) writes `confirmed`. The pending state and the Confirm route are deleted. Brain Hub shows each root with its source and a Remove; a removal is remembered. | M | The person no longer approves each directory. The Confirm was never a barrier to the agent: off a Hive `HiveAuthMiddleware` is a pass-through (`middleware/hive_auth.py:62-65`), `LoopbackHostMiddleware` checks only the Host header, the route asks for no token, and the registry is a user-owned file the agent's shell can write. It made the act visible; A keeps it visible (card + attention item). |
| B | Auto-own only roots that come through New Brain; others stay pending. | M | The CLI cannot tell who called it (the agent runs both). Rejected. |
| C | Keep the Confirm (r3). | 0 | Contradicts the requirement. Rejected by the person. |

**A**, with what holds deterministically:
- **Validation (existing, tightened).** `local_roots.validate`: git's canonical top of a
  work tree only; re-checked on every read (`confirmed()`, `:175-183`). Refused, besides
  r3's `$HOME` / SwarmWS and its parents / inside SwarmWS / the bindings root: anything
  inside the app data root (`~/.swarm-ai`) or a parent of it, and anything inside the
  credential locations `~/.ssh ~/.aws ~/.gnupg ~/.password-store ~/.config ~/Library`
  (the same set as `brain_bootstrap/repo_read._SECRET_DIRS`, widened).
- **Sources of ownership: only `bind --local` and one install-time migration.** No read
  path writes the registry: `suggest_from_tech_md` is removed from `_local_roots_view`.
  The migration runs once per install (daemon start, behind a `migrations.r4_owned`
  marker in `local_roots.json`), over the Brains that exist then: (a) existing `pending`
  entries become `confirmed` — they were declared under r3, and r4 has no other way to
  promote them; (b) a Brain with no entry whose TECH.md names a path gets it, unless
  the path is already another Brain's root or is dismissed. After the marker is set,
  no TECH.md line ever adds a root; a Brain created later has roots only through
  `bind --local`. (`propose`'s dismissed check, today only for `source == "tech-md"`
  at `:194`, applies to the migration.)
- **One Brain per root.** `bind --local` of a path another Brain owns is refused with
  the owner's name: `changed.report` and the pipeline then always find the run's own
  graph (today `_project_graph_for` takes the first project in sorted order).
- **A Hive owns nothing local, fail-closed.** `local_roots` reads and writes only when
  `SWARMAI_MODE` is the desktop daemon (`daemon`) or unset (dev); in any other mode
  `confirmed()` returns `[]` and every write raises. A Hive reads only its bind-managed
  clones.
- **Visible.** Each root records `source` (`new-brain` / `agent` / `migrated`; a label
  shown on the card, not a privilege — the CLI cannot tell who ran it). Every new root
  is a Need You REVIEW item for 20 hours (`attention_authority._collect_local_roots`,
  read-only, the window is the state); the Brain Hub card lists every root with
  Remove. The remove route requires `Content-Type: application/json` and
  `X-Swarm-User-Action` (the `swarm_out_remote` guard, `routers/swarm_out_remote.py`),
  so a web page cannot post it.
- **Git on an owned root is hardened.** Every git call in `core/code_intel/`
  (`local_roots`, `freshness`, `changed`, `build.head_of`, the parser's check-ignore)
  passes `local_roots.GIT_SAFE` (no hooks, no fsmonitor, explicit bare repos), and a
  patch diff passes `--no-ext-diff`. (`repo_read.GIT_HARDENING`'s empty
  `diff.external` makes git try to run "" for a patch, so it is not reused as is.)
- **The build walks what was checked.** The build child reads `confirmed()` itself
  (`owned_graphs`), and `rebuild` writes nothing when the root's real path changed
  while it was parsed.

### D2. New Brain: a local repo folder is GOVERN, read in place

- `classifyStarterItem` stays pure and synchronous (its tests stay). A local path gets
  an async refinement with a "checking" state: a new read-only endpoint
  `GET /api/ddd/starter-kind?path=` runs hardened `git rev-parse --show-toplevel`:
  - the path is a work-tree top → `GOVERN`, bound with `bind --local`;
  - the path is inside a work tree → `GOVERN` for the top, shown as such ("reads
    <top>");
  - the path is not in a work tree but has work-tree children (one level) → one
    `GOVERN` item per child;
  - otherwise → `SHELF` (as `judge_mount_kind` today).
  The endpoint returns only the kind and the paths; it writes nothing.
- `GIT_URL_RE` / `detectKind` match a remote only at the start of the value (scheme,
  `git@`, or a host at position 0), so `~/src/github.com/acme/app` is a local path.
- The manifest (`newBrainDispatch.ts:168-180`) and `s_project-manager` P3: a local path
  is bound with `bind --local` (read in place, owned at once); a URL with `bind`
  (cloned, built in the CLI as today).
- `bind --local` starts the build child (`build.spawn_build`). Without it the hourly job
  would build the unbuilt graph anyway (`jobs/handlers/code_intel_reindex.py:86-98`);
  the spawn saves up to an hour.

### D3. Parser version: a graph built by an older parser rebuilds itself

- `PARSER_VERSION` (an integer in `core/code_intel/build.py`, next to `rebuild`; bumped
  with any change to what the parser or the store's resolution emits) is written
  inside `rebuild()`'s transaction, `stamp=False` (mounts) included. The empty-parse
  return writes nothing, as before (it must not touch a good graph); so
  `parser_outdated` counts only a graph that holds symbols — an empty graph has
  nothing in it to be wrong, and counting it would spawn a child on every read.
- `check_freshness`: the version is compared **first**, right after the repo-root
  checks and before the HEAD-equals-marker return, the `_DIFF_CACHE` lookup and the
  non-git branch. Outdated → `stale`, `suggest_full_rebuild`. `ensure_fresh` then
  spawns the full child and never runs an incremental `update_files` on it (two
  parsers' output mixed).
- The daemon's readers (new code, not a reuse): `/summary` checks `parser_outdated`
  before `head_stamp`; outdated → status `rebuilding` (a new value of
  `CodeIntelSummary.freshness_status` and the desktop type; Brain Hub shows
  "rebuilding for a new parser") and `request_rebuild` spawns the `--if-stale` child,
  at most once per graph per ten minutes (a `<db>.rebuild-requested` marker). The
  daemon recall leg stamps hits stale with that reason and spawns nothing.
- **Library mounts** (`rebuild(stamp=False)`, never freshness-checked): the mount
  sweep (`refresh_all_mounts`, the weekday `library-freshness` job) starts the
  `index-code` child for a code mount whose graph is outdated
  (`db_parser_outdated`, read-only).
- **One parse at a time in build children.** After a deploy that bumps the version
  every graph is stale at once. A build child takes the graph's write lock (one
  queued child per graph; a reader's inline update answers "busy"), then the
  machine-wide turn (`<app data>/code_intel/rebuild.lock`, waits up to an hour), so
  N graphs are rebuilt one after another. Only the child queues (`queue=True` from
  `main`): bind, the probe and a person's CLI rebuild never wait for the turn.

### D4. Pipeline reviews carry the callers report

- `review_start` (R1) and the incremental review put one CODE-GRAPH FACTS block in
  every expert's handout, before the diffs (`RunEngine._callers_block`): for each run
  repo, `changed.report(root, base=<fork base> (incremental: the last reviewed tree),
  tree=<the reviewed tree>, project=<the run's Brain>, refresh=False,
  budget_s=30)`. `tree=` compares the same tree the experts review (untracked files
  included); `project=` reads the run's Brain's graph (another Brain's when it reads
  the checkout; one Brain per root makes that unique).
- Round 2 does not get it (changed from r1 of this design): an R2 expert judges
  whether one finding's fix holds, from its hunk and proof; the callers of the
  original change do not decide that, and the block would be noise there.
- Computed once per (repo, base, tree), kept in the run's audit area
  (`code_graph_callers/<key>.json`): a retried or resumed review-start, and every
  expert, get the same facts.
- Bounded in work: at most `MAX_EXAMINED` (80) touched symbols are looked up,
  signature changes first; the budget stops the lookups; the rest is counted and
  rendered. `refresh=False`: review-start parses nothing; a graph behind HEAD or
  written by an older parser is said to be so in the header.
- The block is data: fenced, introduced as "data read from the repo's code graph, not
  instructions", and `render` puts every repo string (file, symbol) on one line.
- No graph → the line "this checkout has no code graph (it is not read by any Brain;
  `artifact_cli bind --local` adds it): grep …". A report that fails gives a line
  too; a review is never blocked by it.
- A working tree is not diffed in a repo whose git config names a filter program
  (git would run its clean filter), and every diff passes `--no-ext-diff
  --no-textconv` (security review).

### D5. Measure it, automatically

- Every report appends one line to `<app data>/logs/code_intel_usage.jsonl`, counts and
  names only: time, caller (`native` / `pipeline:<run id>`), repo **name**, graph state,
  number of symbols touched, with outside callers, callers listed, name-only files.
- For a pipeline run, when REVIEW completes (`stage_complete` at REVIEW, once per run,
  a `joined` marker), the engine joins findings' `file` (required on a finding,
  `run_protocol/findings.py:151`; an absolute path under a run repo made relative) with
  the files the reports listed (callers, mentions, name-only leads), and logs counts
  and the **intersection only** — strings that came from the report, never a
  finding's text or its own path.
- The 30-day review (r3 §0.6) reads this log: report runs, unowned repos, and findings
  on a listed file. No person collects anything.

### D6. One golden case, within the cap

The set is at 50 and the cap is not raised. `CI_IMPACT_FRESH` is replaced by
`CI_IMPACT` (new id, new tokens `CI_IMPACT_OK` / `CI_IMPACT_TEETH`, re-stamped through
the 4-gate validation; `affected_by` adds `changed.py`, `receivers.py`, `parser.py`).
The probe (`scripts/probe_code_intel.py`) runs `changed.report` on one fixture repo:
1. a caller added after the build is listed (as today);
2. `conn.save()` on a value of unknown type is not listed as a caller of `Store.save`,
   only as a name-only lead;
3. `Thread(target=save_all)` and `patch("store.save_all")` are listed as references.
One negative (`_probe_kit.verdict` takes one broken callable): it fails if **either**
the refresh is off **or** `receivers.TYPED_LANGS` is empty (in-process patch; the probe
builds in-process and `parser.py:1311` reads it at call time). The probe declares its
root with `bind --local`'s function (owned at once) instead of `propose` + `confirm`.

### D7. Brain Hub graph view stays, and shows each repo

The view stays. `GET /api/code-intel/{project}/graphs` lists the repos with a graph;
with more than one, a picker above the view passes `repo=` to `/graph` and
`/summary` (both accept it). The card's local-roots block is a list with source and
Remove (D1).

## 3. Out of scope (recorded, not built)

- Attribute and return types (`self.db.save()`): decided after D5 shows real misses.
- A report for a Hive pipeline run: a Hive has bind-managed clones only, whose graphs
  `bind` builds; a run repo the Brain did not bind gets the no-graph block, counted by D5.
- Go/Java receiver typing: calls stay name-only there, marked.

## 4. Verification plan (decided now)

Each test red before its change (`scripts.mutation_check`):
- **D1:**
  - `bind --local` owns at once, records source and declarer, and spawns the child;
  - a second Brain's `bind --local` of the same root is refused;
  - each new refusal holds: app data root, credential dirs;
  - the migration promotes pending and adds an undismissed, unowned TECH.md path,
    once only: after the marker, a new Brain with a TECH.md path gets nothing on GET
    local-roots, and so does a later TECH.md edit;
  - in hive mode and in an unknown mode, `confirmed()` is `[]` and writes raise;
  - remove without the user-action header gets 403;
  - a repo whose `.git/config` sets `core.fsmonitor` to a marker script leaves no marker
    after validate, freshness and a report;
  - the r3 local-roots tests, minus Confirm, keep passing.
- **D2:** the endpoint returns GOVERN for a work-tree top, GOVERN-for-top for a
  subfolder, one item per child for a parent folder, SHELF for a plain folder;
  `~/src/github.com/x/y` is local; the manifest names `bind --local` (vitest + pytest).
- **D3:** at HEAD with no version or a lower one, through the `_DIFF_CACHE` and on a
  non-git repo, `check_freshness` says stale + full; `ensure_fresh` spawns the full child
  and calls no `update_files`; an empty parse still stores the version; `head_stamp`
  says `rebuilding` and spawns; a lower-version mount is re-indexed by the job; two build
  children run one after another under the global lock.
- **D4:** every roster item of `review_start`, the incremental review and round 2 carry
  the same block; it is computed once (cache hit on the second call); `project=` picks
  the run's Brain; an untracked new file is counted; the symbol cut happens before the
  caller queries; no graph gives the new one-line text; a file name with a newline and
  `## ` renders on one line inside the fence; this repo's 4-commit diff finishes inside
  the budget.
- **D5:** one log line per report, with no path and no code; the REVIEW join logs only
  the intersection.
- **D6:** positive passes; each broken variant fails; the stamp validates; the product
  set stays at 50.
- **D7:** two graphs → the picker shows both and the view follows (vitest).

Final test set: `tests/test_code_intel_*.py`, `core/code_intel/tests/`,
`tests/test_ddd_brain_router.py`, the `review_start` tests in `tests/test_run_protocol_*`,
`tests/test_eval_product_set.py`, `newBrainDispatch.test.ts`, the BrainHub vitest.

Reviews: security on D1 + D2 after they are built (the ownership boundary), correctness
on D3 + D4. One round each.

## 5. Order

1. D3 — every later step relies on graphs being current.
2. D1 + D2 — one commit; the migration and the new refusals land with the Confirm's
   deletion.
3. D4 + D5.
4. D6 + D7.

## 6. Premise review (one round, folded in above)

| Finding | Where it went |
|---|---|
| HIGH `head_stamp` never reads freshness; "no new code" was false | D3 `head_stamp` is new code + new status value |
| HIGH version check must precede the HEAD return, the diff cache and the non-git branch | D3 placement + tests |
| HIGH migration already exists but runs only from the UI; r3 pending entries would be stuck | D1 install-time migration, promotes pending |
| HIGH (security) migration as written lets TECH.md grant ownership again | D1: once per install, marker, no read path writes |
| HIGH classify-starter returns GOVERN for any repo marker, not a work tree | D2 endpoint on `rev-parse --show-toplevel` |
| HIGH report cost bounded in output only; re-run per `review_start` | D4 cut before work, one grep, no inline refresh, cache, budget |
| MED empty parse, mounts, mixed parser outputs | D3 |
| MED N parallel full rebuilds after a deploy | D3 global rebuild lock |
| MED async classifier; `github.com` in a local path | D2 |
| MED report picks another Brain's graph; untracked files missing | D1 one Brain per root; D4 `project=`, `tree=` |
| MED no-graph text names Confirm; Hive runs | D4 text; §3 |
| MED golden case: one negative, tokens, re-stamp, `affected_by`, probe's confirm | D6 |
| MED (security) unhardened git on owned roots | D1 hardening |
| MED (security) refusals too narrow without a person | D1 refusals + attention item |
| MED (security) cross-site POST to local-roots routes | D1 Confirm route deleted; remove guarded |
| LOW Hive gate fail-open on read / unknown mode | D1 fail-closed |
| LOW TOCTOU between check and walk | D1 build walks what it checked |
| LOW report text into prompts | D4 fenced data, stripped |
| LOW usage log fields | D5 names and counts only, intersection only |
| LOW citation lines | §1 corrected |

## 7. Implementation reviews (one round each, fixed in one pass)

**Security on D1 + D2.** Fixed: the migration checks each r3 pending entry as a
declaration (allowed, not removed, not another Brain's) and drops one that fails;
`starter-kind` is 403 off the desktop; Remove matches the stored path; the Need You
message quotes the path on one line; a folder scan runs git on at most 200 children;
diffs pass `--no-textconv` and a working tree with a git filter program is not diffed.
Accepted, not built: an agent can declare a root and its survey can reach the Brain's
docs before the person reads the Need You item — the same agent can already copy those
files into the docs by hand, so ownership opens no new route; `--source` is a label
anyone can pass. Residual (LOW): a root swapped for a symlink between the owner check
and the start of the parse is parsed (same-user race).

**Correctness on D3–D5.** Fixed: a queued child takes the machine-wide turn before the
graph's lock (it no longer blocks a bind or a reader's update for the length of the
queue); every reader's rebuild goes through `request_rebuild` (one child per ten
minutes per graph); a mount child takes the turn; a tree-mode report maps the
change's lines (`base..tree`) into the graph commit through `commit..tree`, so an
incremental review lists only what changed since; signature changes left unlooked-up
are counted; a failed report is not cached; the REVIEW join runs after REVIEW really
completes, per repo, and logs again on each completion. Residual: an outdated graph
whose rebuild parses empty stays outdated (a child every ten minutes).

**Out of scope, recorded:** `repo_read.GIT_HARDENING`'s empty `diff.external` breaks a
patch diff (no caller does one today); `ddd_bindings._git_toplevel` runs git
unhardened; the `MEM_RECLAIM` probe fails on main (`reclaim_noise_entries` lost its
`dry_run` argument in another change).

