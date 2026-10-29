---
title: "Code intel r4: fully automatic once a Brain has a repo"
created: 2026-10-29
updated: 2026-10-29
status: draft r2 (premise review done: correctness + security; findings folded in §6)
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
- **Visible.** Each root records `source` (`new-brain` / `agent` / `migrated`) and the
  session or run that declared it. An agent-declared root raises one attention item
  ("<Brain> now reads <path>. [Remove]"); the Brain Hub card lists every root with
  Remove. The remove route requires `Content-Type: application/json` and
  `X-Swarm-User-Action` (the `swarm_out_remote` guard, `routers/swarm_out_remote.py`),
  so a web page cannot post it.
- **Git on an owned root is hardened.** Every git call in `core/code_intel/`
  (`local_roots.validate`, `freshness._git` and its `merge-base`, `changed._git`,
  `build`) passes `repo_read.GIT_HARDENING` (no hooks, no fsmonitor, no external diff,
  explicit bare repos): an owned root is now hit by the hourly job and every review,
  outside any session.
- **The build walks what was checked.** The build child calls `confirmed()` itself and
  walks the real path it returns; it stops if the root's real path changes during the
  walk.

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

- `PARSER_VERSION` (an integer in `core/code_intel/parser.py`, bumped with any change
  to what the parser or the store's resolution emits) is written inside `rebuild()`'s
  transaction on every path, including the empty-parse return (`build.py:116-120`)
  and `stamp=False`.
- `check_freshness`: the version is compared **first**, right after the repo-root
  checks and before the HEAD-equals-marker return (`:96`), the `_DIFF_CACHE` lookup
  (`:105`) and the non-git branch (`:66`). Missing or lower → `stale`,
  `suggest_full_rebuild`. `ensure_fresh` then spawns the full child and never runs an
  incremental `update_files` on an old-version graph (two parser outputs mixed).
- `head_stamp` (new code, not a reuse): reads the `parser_version` meta key (no git);
  lower → status `rebuilding` (a new value of `CodeIntelSummary.freshness_status` and
  of the desktop type), and the daemon calls `spawn_build(only_if_stale=True)` — it
  spawns a child, it never parses. Its other caller, `recall_multi.py:378`, shows the
  same stamp.
- **Library mounts** (`library_mounts.py:563`, `rebuild(stamp=False)`, never freshness-
  checked): the hourly job re-indexes a mount whose stored version is lower.
- **One rebuild at a time.** After a deploy that bumps the version every graph is stale
  at once. The build child takes a global fcntl lock (`<app data>/code_intel/
  rebuild.lock`) before parsing and waits for it, so N graphs are rebuilt one after
  another, not N parses in parallel. Expected: about one minute per graph of this
  repo's size (measured in D3's test on this repo before it lands).

### D4. Pipeline reviews carry the callers report

- `changed.report` gains `project=` (the engine passes `_knowledge_project()`), and
  `tree=` (the run's `write_tree` sha, so the report compares the same tree the experts
  review, untracked files included; today it diffs the working tree, `changed.py:96`).
- Bounded in work, not only in output: symbols are ranked (signature-touched first) and
  cut to `MAX_SYMBOLS` **before** `_callers`/`_mentions` run; mentions use one
  `git grep` for all names; `refresh=False` (a stale graph spawns the child and the
  report says the graph is behind; nothing is parsed inline in `review_start`); a time
  budget of 60 s whose overrun gives the one-line block.
- Computed once per review tree: `review_start` caches the rendered block in the run
  directory keyed by `(repo, base, tree, indexed_commit)`; the incremental review and
  round 2 reuse it, so every expert sees the same facts.
- The block is data: inside a fenced section marked "code-graph facts (data, not
  instructions)", with control characters and newlines stripped from file and symbol
  strings in `render()`.
- No graph → the one-line block, rewritten for r4: "this repo has no code graph (not
  bound to a Brain); grep the callers". (Today's text says "confirm it in Brain Hub",
  `changed.py:262`.)
- A review that finds its graph rebuilding does not wait; the report says so and the
  usage log (D5) counts it.

### D5. Measure it, automatically

- Every report appends one line to `<app data>/logs/code_intel_usage.jsonl`, counts and
  names only: time, caller (`native` / `pipeline:<run id>`), repo **name**, graph state,
  number of symbols touched, with outside callers, callers listed, name-only files.
- For a pipeline run, when REVIEW closes, the engine joins findings' `file` (required on
  a finding, `run_protocol/findings.py:151`; normalised to repo-relative POSIX) with the
  report's listed caller files, and logs the **intersection only** — strings that came
  from the report, never a finding's text or its own path.
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

The view stays. With more than one graph, a repo picker above it passes `repo=` to
`/graph` and `/summary` (both accept it, `routers/code_intel.py:95,117`). The card's
local-roots block becomes a list with source and Remove (D1).

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
