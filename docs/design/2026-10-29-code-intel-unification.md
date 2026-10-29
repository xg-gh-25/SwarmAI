---
created: 2026-10-29
updated: 2026-10-29
---

# Code intel: one way in, one way to build, one way to update

Status: r3 proposed (2026-10-29). r2 (§1–§6 below) has landed in full: B1–B9, the deletions and `impact`. r3 (§0) changes **when** a graph is built and refreshed, and where that work runs; it supersedes the refresh paths of §3.2–§3.3 (watcher, health hook, commit hook).
- r1 had one correctness premise review: 3 HIGH, 7 MED, 6 LOW. All are folded in here, and each was checked against the code.

## 0. r3 — build where the repo lives, refresh when it is read

### 0.1 Why: what the graph earns and what it costs (measured 2026-10-29)

Method, reproducible: the last 14 days of session transcripts (`~/.claude/projects/<workspace>/*.jsonl`) and the four rotated daemon logs (10-17 → 10-29); a full build of the SwarmAI repo into a temp db.

**Value.**
- **Auto-recall:** 289 recall blocks; 16 carried code symbols (5.5%), and after one of them the agent read the file. The hits are mostly test functions or names like `main.py::root`, often stamped STALE.
- **The CLIs:** `analysis` and `impact` were run 31 times, every one by work on code-intel itself.
- **Real reader:** New Brain's survey (TECH Key Subsystems); two repos have ever been bound.
- **Unproven:** the reason to keep it is `impact`. The class it answers, a contract change missing a caller (tests included), recurs in MEMORY and is what AGENT R27 exists for.

**Cost.**
- **Stalls:** in 224 of 562 event-loop stalls (40%), a daemon thread was inside code-intel at the time. 190 of those were `get_codebase_summary` from the BottomBar's 60-second poll, already deleted (`d481dcbb4`); 9 were the TECH-drift channel, also deleted. The rest: watcher batches and incremental updates. This is co-occurrence, not proof: re-measure after deploy (§0.6).
- **Build:** full build of SwarmAI (2,356 files, 38,173 symbols, 305 routes) takes 25 s wall, 22 s CPU, 234 MB peak RSS, 123 MB db. While it runs in the daemon it holds the GIL.
- **Incremental update:** 1 file 0.26 s, 3 files 0.28 s, 14 files 0.6 s, 50 files 1.1 s. About 0.2 s of that is a fixed cost (`rebuild_fts` rebuilds the whole FTS table).
- **Commit rate (this repo):** 86 commits a day (median), 164 at most. A commit touches a median of 3 code files (p90 14, max 306); 26% touch none.

**Conclusion.** Refreshing on every save or every commit buys nothing: nobody reads the graph between commits. The cost was never the parse arithmetic (86 × 0.3 s a day); it was **where** the parse ran.

### 0.2 Rules

1. **The daemon never parses or writes a graph.** It only reads: Brain Hub, the Library overlay. Every build and update runs in a separate process, either a CLI the agent runs or a job, so it never holds the GIL the chat loop needs.
2. **A graph is built where the repo lives**, for every local repo a Brain owns, when the Brain is created or updated.
3. **A graph is refreshed when it is read**, and at most once an hour in the background, and only when its repo's HEAD moved. A repo nobody works on costs nothing.
4. **Code symbols leave the per-turn auto-recall.** They stay on demand: `impact`, and `context_recall_cli --domains codeintel`.

### 0.3 Build: one entry for every local repo

- **Today:** only `bind_repo` creates a graph (`ddd_bindings.py:563`), plus `index_code_mount` for a Library mount (`library_mounts.py:553`). `load_project_graphs` opens only dbs that already exist (`code_intel_dbs`, `__init__.py:50`). So, as measured above:
  - an internal or Brazil binding raises `NotImplementedError`;
  - a binding whose worktree is the user's own checkout fails in `binding_worktree` (`ddd_bindings.py:277`);
  - a TECH.md `Local:` path has a graph only if a db was made by hand.
- **r3: `build.ensure_graph(project_dir) -> list[result]`.** It builds a missing graph for each owned local root; for a graph that exists, it is `ensure_fresh`. The db of each kind of root:
  - a bind-managed clone: unchanged, `bindings/<repo>.code_intel.db`;
  - a TECH.md root: `Projects/<p>/code_intel.db`, as SwarmAI has today;
  - a **local root** (the user's own checkout, internal or not): `bindings/local/<sha1(realpath)[:16]>.code_intel.db`. It is keyed by the path, so two Brains naming the same checkout share one graph.
- **Who calls it:** New Brain's bind step and `artifact_cli bind`, in their own process.
- **Ownership** (the security boundary, `owned_repo_roots` at `__init__.py:326`):
  - A free `worktree` path in `bindings.yaml` still grants nothing, because that file can be written from repo content.
  - A local root is owned only through a **machine-local registry the user writes**: `bindings/local_roots.json`, which maps a project to absolute paths. `artifact_cli bind --local <path>` writes it, from New Brain when the person names a checkout.
  - The registry lives outside SwarmWS and outside any repo, so neither sync (Brain sync strips `worktree` anyway, `brain_sync.py:125`) nor a repo can add to it.
  - A registered root must be a git work-tree top (`git rev-parse --show-toplevel` equals the path) and must not be inside SwarmWS or the bindings root.
  - `owned_repo_roots`, `code_intel_dbs` and `repo_root_is_owned` read the registry, so every existing guard covers local roots without change.

### 0.4 Refresh

- **On read: `build.ensure_fresh(graph, repo_root) -> Freshness`.**
  - It runs `check_freshness` first (git only: `rev-parse`, `merge-base`, `diff --no-renames`, `rev-list`).
  - Up to `FULL_REBUILD_FILES` (100) changed files: `update_files`, then move the commit marker when it is `complete`.
  - Above 100, or a base commit rebased away: a full `rebuild` inline when the repo has at most `INLINE_REBUILD_FILES` (5,000) indexed files. That is about 10 ms a file, so about 50 s at the limit; SwarmAI takes 25 s.
  - Larger than that: answer from the old graph, say how far behind it is, and emit `code_intel_full_reindex` (§0.4.2).
  - Callers, all of them separate processes: `impact`, `analysis` (CLI), the survey (`survey_repo`, which `doc_index.sync_project` calls from the CLI and from the job).
  - `--no-refresh` skips it, for a caller that must not wait.
- **Background:**
  - The incremental job moves from `on:git_commit` to a cron schedule, hourly. Today that event fires on SwarmWS auto-commits, not on the code repo's commits (`auto_commit_hook.py:340`); the scheduler runs event jobs on its hourly tick anyway (`main.py:857`).
  - The job already updates only graphs whose repo moved (`check_freshness` per graph).
  - The full job (`on:code_intel_full_reindex`) rebuilds only the graphs whose freshness asks for it (`suggest_full_rebuild`, or never indexed). Today `--full` rebuilds every graph of every project.
- **Deleted:**
  - the watcher (`watcher.py`, its start in `main.py:863`/`:1544`, and its tests);
  - the health hook's `_refresh_code_intel` (`context_health_hook.py:665`, call at `:292`), which runs on the daemon's executor;
  - the commit hook's `_reindex_changed_files` (`code_change_feed.py:380`, call at `:318`);
  - the daemon's tree-sitter pre-warm (`main.py:1285`), if nothing in the daemon parses any more. `verify_build`'s native probe loads grammars on its own (`verify_build.py:394`), so it is checked separately.
- **Started by a person:**
  - The Brain Hub Re-index button (`routers/code_intel.py` `_run_reindex`, now a daemon BackgroundTask thread) and a Library code mount (`library_api.py:225` → `index_code_mount` in `asyncio.to_thread`) start a **subprocess** with the same launcher the jobs use (`utils.backend_bin.py_command`). It is `python -m core.code_intel.build --project P` or `--mount <id>`, run with `asyncio.create_subprocess_exec`.
  - The endpoint returns 202. Its in-progress guard becomes "the child is still alive".
- **Two writers on one db** (the CLI and the job): `ensure_fresh` and `rebuild` take a non-blocking `fcntl` lock on `<db>.write.lock`. The one that loses does not wait: a reader answers from the graph as it is and says it is being updated; the job skips that graph this hour. SQLite WAL keeps the daemon's readers consistent with a writer in another process; `busy_timeout` is 5 s.

### 0.5 What the daemon still reads, made light

- **Brain Hub `/summary`:** `get_codebase_summary` runs `find_dead_code` (a full scan) and the module map, 192 times in the stall record. `/summary` now returns only counts and freshness from `graph_meta` plus `COUNT(*)`. Dead code and modules stay in `analysis`.
- **`/graph`** (Brain Hub, on expand) and the Library overlay's mount symbols (`library_api.py:337`, `search_symbols`): unchanged, read-only and user-started.
- **Auto-recall:** `session_router.py:1006` drops `codeintel` from the per-turn domains. That removes, every turn, the symbol search plus 3–4 git subprocesses per graph for the freshness stamp (`recall_multi.py:362`).

### 0.6 Verification plan (decided now)

- **New tests, each red before its fix:**
  - `ensure_graph`: a local root gets a graph; an unregistered `worktree` path in `bindings.yaml` gets none (the ownership boundary); a registered path inside SwarmWS or not a work-tree top is refused.
  - `ensure_fresh`: no change → no write; 1–100 files → incremental and the marker moves; above 100 → inline rebuild under the size limit, otherwise stale answer plus event; the write lock held → answer flagged, no wait.
  - `impact` on a repo with a new commit returns the new caller without a manual re-index.
  - The full job rebuilds only the graph that asked.
  - The re-index endpoint and mount indexing start a subprocess; no parse runs in the daemon process (assert `parse_repo_with_coverage` is not called in-process).
  - Auto-recall's domains no longer include `codeintel`.
- **Final test set:** `core/code_intel/tests/`, the `test_code_intel_*.py` files, `test_code_change_feed*.py`, `test_context_health_hook.py`, `test_recall_multi.py`, `test_ddd_bindings.py`, `test_library_mounts*.py`, `test_brain_bootstrap.py`, `test_session_router` recall tests, and the Brain Hub vitest.
- **Eval:** one deterministic product case on a fixture repo, bind → commit → `impact` returns the caller the commit added. The golden set is at its cap of 50: it replaces a case, or the cap question goes to XG.
- **After deploy:**
  - Re-run the stall attribution (§0.1 method): stalls with code-intel frames should be about 0.
  - At 30 days, count `impact` and `--domains codeintel` runs outside code-intel's own development (§0.1 method). Near zero → the graph goes, and survey keeps a one-shot parse.
- **Review:**
  - one correctness expert on the build/refresh step;
  - one security expert on the local-root registry, since it is the ownership boundary;
  - the deletions get none.

### 0.7 Order (one commit each, each landed)

1. **Ownership and build:** the local-root registry, `ensure_graph`, `bind --local`, and New Brain's bind step calling `ensure_graph`.
2. **Refresh on read:** `ensure_fresh` + the write lock; `impact`, `analysis` and the survey call it; the job becomes hourly and `--full` targets.
3. **The daemon stops parsing:** the re-index endpoint and mount indexing go to a subprocess; delete the watcher, the health-hook refresh, the commit-hook reindex and the pre-warm; light `/summary`.
4. **Auto-recall drops `codeintel`;** the eval case.

## 1. What the graph is for: the readers that reach someone

| Reader | Reads | Who sees it |
|---|---|---|
| `core/recall_multi.py:300` `_project_codeintel_recall` | `search_symbols` in every graph (`load_project_graphs`), plus 1-hop `find_callers` of the top hit | the agent (recall `codeintel` domain) |
| `routers/code_intel.py:167` `/graph` → `get_graph_data` | nodes + edges, capped at 1000 | the human: `CodeGraph.tsx` in Brain Hub (`BrainHub.tsx:809`) and the BottomBar (`BottomBar.tsx:291`) |
| `core/code_intel/analysis.py:139` `analyze_project` | summary, modules, module edges, layers, routes, dead code | Brain Hub code view via `routers/code_intel.py`; the CLI `py -m core.code_intel.analysis <project>` named in `s_repo-to-ddd/INSTRUCTIONS.md` |
| `core/brain_bootstrap/survey.py` → `analyze_graph_store` | the same analysis, every db (`code_intel_dbs`) | New Brain's first TECH entries |
| `core/code_intel_feed.py:74` `detect_tech_drift` → `ddd_orchestrator` | `get_module_map` | TECH.md drift proposals |
| `core/library_mounts.py:550` | a mounted library's own graph, `search_symbols` | library recall |

Nothing reaches anyone through transitive impact (`GraphStore.blast_radius`) or the per-file risk scores. Their only consumer was the PreToolUse injection hook `code_intel_hook.py`, which was taken out of the hook chain on 2026-08-16 (`ae1a4d776`) because it blocked the event loop and broke prompt-cache reuse. That removal stands; §5 covers what replaces it.

## 2. Bugs (each confirmed at source)

- **B1. `.tsx` parsed with the TypeScript grammar.** Fixed (`8388480a8`): `grammar_for(suffix)` picks the grammar, and the rule tables stay keyed by language. `desktop/src`: 397 files, 380 now ok (was 0), 38874 call edges (was 0).

- **B2. The single-graph loader serves the first repo only, and in-process readers go stale after a re-bind.**
  - `load_project_graph` (`code_intel/__init__.py:119`) opens the first of `code_intel_dbs`.
  - Its `_graph_cache` is keyed by project name with no inode check. `load_project_graphs` (`:147`) does check the inode.
  - Callers:
    - `main.py:885` `_start_code_intel_watchers`, plus `watcher.py:74`, which re-resolves through the same loader
    - `analysis.py:139`, which backs the API, the survey's project path and the CLI
    - `routers/code_intel.py:110,159,179,260`
    - `code_intel_feed.py:74,205,255`
    - `code_change_feed.py:396`
    - `context_health_hook.py:722`
    - `bvt_probe.py:53,165,174`
    - `recall_chain_probe.py:51`
    - the reindex job `code_intel_reindex.py:91`. It runs as a fresh process, so it is never stale, but it is still first-repo-only.
  - Effect: a second bound repo is never watched, re-indexed, analysed or drift-checked, and an in-process reader keeps a re-bound db's deleted file open.

- **B3. `GraphStore.clear()` leaves `code_routes`** (`graph_store.py:1612`). `insert_routes` is INSERT OR REPLACE by id, so after any full rebuild the routes of deleted or renamed handlers survive. That includes the bind and API rebuilds, which store no routes of their own, so those graphs serve the previous job run's routes.

- **B4. One syntax error drops every edge of the file** (`parser.py`, the `_tree_has_error` branch of `parse_file_with_status`).
  - The stated reason is dangling targets. `resolve_and_prune` (`graph_store.py:1624`) already removes edges to missing targets.
  - It does not remove a wrong edge to an existing node, so the rule in §3 is the guard for that.
  - Measured on this repo, gitignored dirs excluded: `.py` 0 of 1744 degraded, `.ts` 1 of 381, `.tsx` 17 of 501. The `.tsx` cases are grammar quirks: a bare `&` in JSX text, and `typeof import(...)` as a type argument.
  - Among them is the 2143-line `FileEditorCore.tsx`, which loses all its edges because of an `&` on line 2062.

- **B5. Lists of which files count as code, copied by hand and drifting.**
  - `watcher.py:33` `_WATCHED_EXTENSIONS` lacks `.sql/.pks/.pkb`, and `watcher.py:40` `_SKIP_DIRS` lacks `target`.
  - `code_change_feed.py:371` `_REINDEX_EXTENSIONS` has 5 languages, so an agent's edit to `.go/.rs/.java/…` is not re-indexed by that hook. The same hook also skips `tests/` and `test_*` (`_skipped`, `:53`), while full rebuilds index them, so test nodes go stale after an edit.
  - `parser.py` lists `.sql` both in `LANGUAGE_MAP` (`:55`) and in `_NON_SOURCE_EXTENSIONS` (`:516`).

- **B6. The BottomBar always shows SwarmAI's graph.** `BottomBar.tsx:149,182,186` call `getCodeIntelSummary('SwarmAI')` / `triggerReindex('SwarmAI')`, and `:291` renders `<CodeGraph project="SwarmAI">`.

- **B7. The API rebuild marks an empty graph fresh.**
  - `routers/code_intel.py:301-310`: when the parse returns nothing, it calls `clear()` and still calls `mark_indexed(full=True, head)`.
  - The reindex job keeps the old graph and does not stamp it in that case (`code_intel_reindex.py:166`, a Gate-2 HIGH fix).
  - The bind clears the graph and does not stamp it.

- **B8. The health hook moves the commit marker past files it never indexed.**
  - `context_health_hook.py:798`: `incremental_update(repo_root, changed_files[:50])`, then `last_indexed_commit = current_head` unconditionally, including when the update reported failures.
  - A full rebuild is suggested only above 100 changed files (`freshness.py:160`), so for 51–100 changed files the files from the 51st on are never indexed until a full rebuild.

- **B9. A rename leaves the old path's nodes.** `code_change_feed.py:410` says so explicitly: the old path is not in `file_changes`, so its nodes stay until a full rebuild.

## 3. One version of each

**3.1 Full rebuild: `rebuild(graph, repo_root, *, head, routes=True) -> RebuildResult`** in a new `core/code_intel/build.py`.

Steps:
1. Parse with coverage.
2. If the parse produced no files, leave the graph untouched and unstamped, and return `empty=True`. This is the job's rule; it fixes B7, and the bind adopts it.
3. Otherwise: `clear()` (which now includes routes, B3), `bulk_insert`, routes and prefixes when `routes`, coverage holes and status into meta, and `mark_indexed(full=True, head=head)`.

Contract:
- **HEAD:** the caller captures `head` before the parse, so a commit landing mid-parse reads as unindexed. Today the bind captures it after the parse; that changes.
- **Locking:** the caller holds the lock. The API keeps `_bind_lock` + `graph._conn_lock`; the bind keeps its private store; the job runs in its own process.
- **Callers:** all four.
  - The library mount passes `routes=False`, because a library's routes have no reader.
  - The mount keeps in its own code what is specific to it: `mount_id`, the `_mount_graph_cache` swap, and its "indexed_empty, no last_synced" result. It also skips the `mark_indexed` stamp (`stamp=False`), because mount freshness is `last_synced`.

**3.2 Incremental: one function, only for what all paths share.**

The three paths do differ on purpose:
- The job grades each file (`grading.file_grade`) and leaves a COSMETIC change's nodes untouched. That is right for a commit-range job, but on every save it would leave line numbers wrong.
- The watcher chunks its route pass (20 files, an `asyncio.sleep` and a `_running` check between chunks: event loop, memory, stop), and it skips a batch while `bind_in_progress`.

So there is no merged grading path. Instead, there are two functions in `build.py`:
- **`update_files(graph, repo_root, files) -> UpdateResult`**:
  1. If there are more files than `FULL_REBUILD_THRESHOLD` (one constant, shared with `freshness.py`), store nothing and return `needs_full=True`.
  2. Otherwise run `incremental_update` (store, `resolve_and_prune(files)`, FTS, `mark_indexed(full=False)`, which it already does), then `routes_for(files)` and prefixes.
  3. Report `failed` per file.
  4. It never writes `last_indexed_commit`.
- **`routes_for(graph, repo_root, files)`**: the watcher calls it per chunk, after one `incremental_update`, keeping its chunking and its gate.

`last_indexed_commit` is written only when an update covered every changed file with no failure (fixes B8). The health hook calls `update_files`; on `needs_full` it schedules the reindex job instead of indexing the first 50 files. The job keeps its graded path and calls `routes_for`. `code_change_feed` calls `update_files` and passes the old path of a rename as a deletion (B9).

**3.3 One way in.** `load_project_graph` is deleted, and every caller in B2 moves to `load_project_graphs` (inode-checked, every repo):
- The API's single-graph routes, `/graph` and the BottomBar take an optional `repo`, defaulting to the first. The analysis CLI gets `--repo`, defaulting to every repo, one block each.
- `main.py` starts one watcher per graph, with the ownership guard kept per `ref.repo_root`.
- The watcher registry is keyed by db path. `_MAX_WATCHERS` counts dbs. `stop_watcher(project)` stops all of a project's watchers. The `_graph` property re-resolves by db path.
- `load_project_graphs` must not close a store a watcher thread is using: on an inode change it drops the stale store from the cache and the watcher re-resolves on its next batch. It closes the stale store only after the watcher has re-resolved, or never (the store is garbage-collected).
- The reindex job, the feed and the health hook loop over the graphs.

As built (step 3): `project_graph(project, repo=None)` is the single-graph accessor over `load_project_graphs`. The analysis CLI defaults to the first graph, like the API, and names the others on stderr (`--repo` picks one): one JSON shape for one and for several repos. `invalidate_cache` is a reset for tests and tools; the app never calls it. Two readers with no consumer go instead of being made multi-repo: the `/routes` endpoint (nothing calls it) and `code_intel_feed` (the TECH-drift channel wrote `code_drift_health.json`, which nothing reads, and only for the project named SwarmAI; its two coverage helpers had no caller).

**3.4 Which files are code: one membership source; purpose-specific lists stay and are renamed.**
- Membership is `parser.LANGUAGE_MAP` keys, `parser.SKIP_DIRS` and `_SKIP_PATH_SUFFIXES`. `.sql` comes out of `_NON_SOURCE_EXTENSIONS`.
- The watcher filter and the incremental targets of `code_change_feed` derive from it, and test files are indexed on every path, as the full rebuild does.
- These stay, renamed for their purpose:
  - the watcher's `_SKIP_PATH_SEGMENTS` (Knowledge, `.context`… a jetsam fix, matching absolute paths);
  - `code_change_feed._CODE_EXTENSIONS`, which drives architecture-change detection, not graph membership;
  - `graph_analysis._SOURCE_EXT_SUFFIXES`, if it is a different question (checked at implementation time).
- A guard test fails on any set, frozenset or tuple outside `parser.py` that contains both `".py"` and `".ts"` and is not on a named allowlist with its purpose.

**3.5 B4: keep a degraded file's edges, except those whose call site is inside an ERROR or MISSING node's byte range.** The test is on the call site, not on the source node's span, because recovery often puts the ERROR as a top-level sibling or cuts a function short. Status stays `degraded`. Tests cover `FileEditorCore.tsx`'s shape (a stray `&` in JSX text), a top-level ERROR, and a function cut short by an error. The "Edges are DROPPED" comment is rewritten.

## 4. Deletions

**Modules, about 1700 lines.** They have no production importer, and no dynamic or `-m` use was found. Their tests go with them.

| Module | Lines |
|---|---|
| `blast_radius.py` | 316 |
| `change_risk_score.py` | 309 |
| `dead_code.py` | 255 |
| `module_map.py` | 259 |
| `codebase_map.py` | 185 |
| `code_intel_hook.py` | 378 |

`bvt_probe.py`:
- its `code_intel_hook` step (`:43`) goes;
- its other steps move to `load_project_graphs`;
- its use of `detect_project_from_path` (`:76`) moves to a project lookup by owned repo root (`owned_repo_roots`), after which `detect_project_from_path` is deleted.

`.kiro/specs/legacy-code-cleanup` (2026-06-18) names `dead_code.find_dead_code()`. The capability itself, `GraphStore.find_dead_code`, which `analysis.py` uses, stays.

**GraphStore methods whose only callers were the modules above:** `count_callers_by_file`, `top_callers_by_file`, `get_routes_for_handler`, `imported_files`. The list is re-grepped at implementation time.

Kept:
- `get_graph_data`, because `/graph` reads it;
- `find_dependents`, because `incremental_update` calls it (`graph_store.py:1805`);
- `blast_radius`, kept or deleted by §5.

## 5. Value: what replaces the removed hook

The agent gets the graph only from recall: location, signature, and 1-hop callers of the top hit. Grep finds a symbol too. What grep cannot give is resolved callers across files (aliased imports, method calls, TS imports), which is what AGENT R27 ("grep ALL consumers") and review scoping need. Both options below are on demand, so neither touches the prompt cache:

- **(a) Nothing new.** Fix the data and keep recall as the only door; delete `blast_radius`. Cost 0.
- **(b) One read-only command, `code_intel_cli.py impact <file|symbol> --project P [--repo R]`.** It returns direct and transitive callers (depth ≤ 2), grouped by file, from every graph, freshness-stamped like recall. A stale graph says so and names the files changed since indexing. R27 and `s_expert-review`'s scope step name it as the first step, with grep as the cross-check. Cost: about 150 lines plus two doc lines. It keeps `blast_radius`.

As built: `python -m core.code_intel.impact` (not a separate `code_intel_cli.py`, the same entry form as `core.code_intel.analysis`). It walks callers only (`GraphStore.find_callers`); `blast_radius`, which also walked callees, had no other caller and is deleted.

Recommendation: **(b)**. Since B1 the call graph covers the frontend, where most cross-file regressions have come from, and without a reader those edges are cost with no use.

## 6. Order and verification

Five commits, each landed on its own:

1. **Parser side:** B4 + B5's membership (`.sql`, the derived lists, test files indexed).
2. **`build.py`:** `rebuild` / `update_files` / `routes_for` plus B3, B7, B8, B9; the four rebuild callers and the three incremental callers move to it. Old code is deleted in the same commit, after the callers' existing tests pass on the new functions.
3. **One loader:** B2, including the watcher registry; B6.
4. **Deletions** (§4).
5. **§5 (b)**, if accepted.

New tests, each red before its fix and mutation-checked:
- an empty parse leaves the graph and its freshness stamp untouched, from all four rebuild entry points (B7);
- `clear()` empties routes, and a rebuild from each entry point yields the same routes (B3);
- with 51–100 changed files every file is indexed and `last_indexed_commit` moves only when none failed; above `FULL_REBUILD_FILES` nothing is stored and a full rebuild is scheduled (B8);
- a rename removes the old path's nodes (B9);
- a second bound repo is watched and re-indexed after a file change, and a re-bind is seen by every in-process reader (B2);
- a degraded file keeps the edges outside its error ranges and drops the ones inside (B4);
- the membership guard (B5);
- the BottomBar asks about the active project (B6, vitest);
- an edit to a `.go` file is re-indexed by `code_change_feed` (B5).

Final test set, decided now:
- `core/code_intel/tests/` (parser, graph_store, watcher, grading, analysis)
- `tests/test_tree_sitter_grammars.py`
- `tests/test_code_intel_reindex_handler.py`, `_guard.py`, `tests/test_code_change_feed_reindex.py`
- the `context_health_hook` code-intel tests
- the recall_multi codeintel tests
- the `ddd_bindings` bind tests, the `library_mounts` tests, the `routers/code_intel` tests
- the BottomBar and CodeGraph vitest

Review:
- commit 2 gets one correctness expert, since it moves seven write paths;
- commit 3 gets a concurrency expert, for the watcher registry and the cache close;
- deletions get none.
