---
created: 2026-10-29
updated: 2026-10-29
---

# Code intel: one way in, one way to build, one way to update

Status: r3 implemented (2026-10-29), after one premise review (§0.8); what differs from the plan is in §0.9. r2 (§1–§6 below) has landed in full: B1–B9, the deletions and `impact`. r3 (§0) changes **when** a graph is built and refreshed, and where that work runs; it supersedes the refresh paths of §3.2–§3.3 (watcher, health hook, commit hook).
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

1. **The daemon never parses or writes a repo's graph.** It reads: Brain Hub, its recall, the Library search. Every build and update runs in a separate process. The one parse left in the daemon is the build-verification probe (`main.py:2031`, one snippet, `verify_build` only), and the grammar pre-warm (`main.py:995`) stays for it.
2. **A graph is built for every local repo a Brain owns** when the Brain is created or updated, and ownership has one declaration (§0.3).
3. **A graph is refreshed when it is read out of the daemon** (`impact`, `analysis`, the survey, the recall CLI's `codeintel` leg). Nothing refreshes in the background, so a repo nobody reads costs nothing.
4. **Code symbols leave the per-turn auto-recall.** They stay on demand.

### 0.3 Build and ownership

- **Today:** only `bind_repo` creates a graph (`ddd_bindings.py:563`), plus `index_code_mount` for a Library mount (`library_mounts.py:553`). `load_project_graphs` opens only dbs that already exist (`code_intel_dbs`, `__init__.py:50`). So, as measured above:
  - an internal or Brazil binding raises `NotImplementedError`;
  - a binding whose worktree is the user's own checkout fails in `binding_worktree` (`ddd_bindings.py:277`);
  - a TECH.md `Local:` path has a graph only if a db was made by hand.
- **Today's ownership is read two ways** (`owned_repo_roots`, `__init__.py:326`):
  - **a TECH.md path**, checked only with `is_dir`, from a line `extract_repo_path` takes (`__init__.py:300`). TECH.md is written by cultivation and travels in Brain sync, so once r3 builds a graph for every owned root, a line in a doc would decide which directory gets parsed;
  - **a bind-managed clone.**
- **r3: one declaration, the local-root registry `bindings/local_roots.json`.**
  - **Shape:** project → `[{path, state: pending|confirmed, added_at}]`.
  - **Pending:** `artifact_cli bind --local <path>` writes an entry as `pending`. An agent can run that command.
  - **Confirmed:** only a person clicking Confirm on the Brain Hub review card moves it to `confirmed`. The card says which directory will be indexed, and that its symbols can reach the Brain's docs and a Hive. It is a daemon endpoint, the same pattern as the Project Agent review card. A model's output never confirms an entry; a person's click does (SOUL P5).
  - **Refused paths:** the path must be a git work-tree top (`git rev-parse --show-toplevel` equals it). It must not be `$HOME`, SwarmWS, a parent of SwarmWS, or anything inside SwarmWS or the bindings root.
  - **What it does and does not stop:** repo content, a synced Brain and a document cannot click, so they cannot make a directory owned. An agent editing the JSON by hand is the same-user limit the product already accepts elsewhere; the confirm step makes such an edit visible, not impossible.
  - **TECH.md stops granting ownership.** SwarmAI's one TECH.md root becomes a pending entry, and XG confirms it once.
  - `owned_repo_roots`, `code_intel_dbs` and `repo_root_is_owned` read the registry plus the bind-managed clones, so every existing guard covers local roots without change.
- **Db layout:**
  - a bind-managed clone: `bindings/<repo>.code_intel.db`, unchanged;
  - a confirmed local root: `bindings/local/<sha1(realpath)[:16]>.code_intel.db`, so two Brains naming the same checkout share one graph.
  - `Projects/<p>/code_intel.db` goes. `refresh_ai_docs.py:56` reads it by that path and moves to `project_graph`.
- **`build.ensure_graph(project_dir)`** builds a missing graph for each owned root; for one that exists, it is `ensure_fresh`. Callers: New Brain's bind step, `artifact_cli bind`, and Confirm on the card (the daemon starts it as a subprocess, §0.4).

### 0.4 Writing: one transaction, one process at a time, never inline-heavy

- **A rebuild is one SQLite transaction.**
  - Today `rebuild` holds `_conn_lock`, a thread lock, but `GraphStore.clear()` commits on its own (`graph_store.py:1460`), and `bulk_insert`, `rebuild_fts` and the meta writes commit again. A reader in another process can therefore see an empty graph with no `repo_root`, and a process killed after `clear()` leaves it so.
  - r3: `rebuild` parses outside the transaction, then runs `BEGIN IMMEDIATE`; clear; insert; FTS; meta; `COMMIT`. The writers get a no-commit path for this.
  - Under WAL, readers in any process see the old graph or the new one. A kill rolls back to the old graph.
  - Why not "write a temp db and rename it": a reader still holding the old file keeps its `-wal` open by path, and the renamed db would pair with it.
- **Incremental `update_files`** keeps its per-file commits. A reader sees each file either old or new.
- **One writer per db.**
  - **The lock:** `build` takes an `fcntl` lock on `<db>.write.lock`, and it does so only to avoid parsing the same repo twice.
  - **Who waits:** `ensure_fresh` tries without waiting; if another writer holds it, it answers from the graph as it is and says so. An explicit rebuild (bind, Confirm, Re-index, mount) waits for the lock.
  - `ensure_fresh` also skips a clone while `bind_in_progress`.
- **`build.ensure_fresh(graph, repo_root) -> Freshness`**, for a reader out of the daemon:
  - It runs `check_freshness` first (git only).
  - Up to `FULL_REBUILD_FILES` (100) changed files: `update_files`, then move the marker when it is `complete`. That is 0.3–1.1 s on this repo.
  - Above 100, or a base commit rebased away: no inline rebuild. The agent's Bash call has a 120 s timeout (`claude_environment.py:362`), and a full parse on a loaded machine has measured 81–114 s across all projects (`system_jobs.py:434`). So it starts the rebuild as a **detached child process** (`start_new_session`, the launcher the jobs use, `utils.backend_bin.py_command`): `python -m core.code_intel.build --db <path>`. It answers from the old graph: "N commits behind, a rebuild started, ask again in about a minute". A second start finds the lock held and exits.
  - `--no-refresh` skips all of this.
- **Started by a person:** the Brain Hub Re-index button (`_run_reindex`, today a BackgroundTask thread in the daemon), Confirm, and a Library code mount (`library_api.py:225` → `index_code_mount` in `asyncio.to_thread`) start the same child with `asyncio.create_subprocess_exec`. The endpoint returns 202, and "in progress" means the child is alive.
- **Deleted:**
  - the watcher (`watcher.py`, its start in `main.py:863`/`:1544` and its shutdown at `main.py:1671`);
  - the health hook's `_refresh_code_intel` (`context_health_hook.py:665`, called at `:292`);
  - the commit hook's code-repo leg (`code_change_feed.py:256` `_analyze_and_propose_codebase` and `_reindex_changed_files` at `:380`): with the reindex gone it only logs (`_generate_proposals`, `:342`);
  - both reindex jobs (`system_jobs.py:408`, `:424`) and `jobs/handlers/code_intel_reindex.py`. Today `on:git_commit` fires on SwarmWS auto-commits (`auto_commit_hook.py:340`), not on the code repo's commits, and `--full` rebuilds every graph of every project.
  - Text that would become false: the REFRESHER template (`swarm_workspace_manager.py:512` "rebuilt … on each commit", agent-facing), `code_change_feed.py:263`/`:357`, `workspace_surface_watcher.py:14`.

### 0.5 What the daemon reads, made light

| Reader | Today | r3 |
|---|---|---|
| Brain Hub `/summary` | `get_codebase_summary`: dead-code scan + module map (192 of the stall record) | counts and the marker from `graph_meta` and `COUNT(*)`; dead code and modules stay in `analysis` |
| Brain Hub `/graph` | top-N nodes and edges | unchanged (on expand) |
| Brain Hub recall (`ddd_brain.py:1728`, `codeintel` in its domains) and Library `/search` (`library_api.py:333`, `LIBRARY_DOMAINS`) | `check_freshness` per graph: 3–4 git subprocesses (`recall_multi.py:362`) | the stamp is one `git rev-parse HEAD` against the marker, no refresh |
| per-turn auto-recall (`session_router.py:1006`) | `codeintel` when the query has a code-like term | `codeintel` dropped |

The recall CLI (`context_recall_cli --domains codeintel`, out of the daemon) calls `ensure_fresh` before searching; the daemon readers pass `refresh=False`.

### 0.6 Verification plan (decided now)

- **New tests, each red before its fix:**
  - Ownership:
    - an unconfirmed entry, a TECH.md path or a `worktree` in `bindings.yaml` owns nothing;
    - a confirmed git top does;
    - `$HOME`, SwarmWS, inside the bindings root and a non-top path are refused at `bind --local`.
  - Confirm: the endpoint is the only writer of `confirmed`, and it starts the build child.
  - Atomic rebuild:
    - a second process reading during a rebuild never sees 0 nodes or a missing `repo_root`;
    - killing the writer after the clear leaves the old graph.
  - `ensure_fresh`:
    - no change → no write;
    - 1–100 files → incremental and the marker moves;
    - above 100 → a child starts once (a second call finds the lock) and the answer is flagged;
    - lock held → flagged answer, no wait;
    - bind in progress → skip.
  - `impact` after a new commit returns the new caller without a manual re-index.
  - No parse in the daemon: the re-index and mount endpoints start a child, and `parse_repo_with_coverage` is not called in-process.
  - Recall: auto-recall has no `codeintel`; the daemon stamp uses one git call; the recall CLI refreshes.
- **Final test set:** `core/code_intel/tests/`, the `test_code_intel_*.py` files, `test_code_change_feed*.py`, `test_context_health_hook.py`, `test_recall_multi.py`, `test_ddd_bindings.py`, `test_library_mounts*.py`, `test_brain_bootstrap.py`, the `session_router` recall tests, `test_ddd_brain_router.py`, and the Brain Hub vitest.
- **Eval:** one deterministic product case on a fixture repo, bind → commit → `impact` returns the caller the commit added. The set is at its cap of 50, so it replaces a case or the question goes to XG.
- **After deploy:**
  - re-run the stall attribution (§0.1 method): stalls with code-intel frames should be about 0;
  - at 30 days, count `impact` and `--domains codeintel` runs outside code-intel's own development. Near zero → the graph goes, and survey keeps a one-shot parse.
- **Review:**
  - a correctness expert on steps 2–3;
  - a security expert on step 1 (the registry is the ownership boundary, and indexed structure can reach a Hive through the survey);
  - the deletions get none.

### 0.7 Order (one commit each, each landed)

1. **Ownership:** the registry, `bind --local`, the Confirm card and endpoint, `ensure_graph`, migrating SwarmAI's TECH.md root; TECH.md no longer grants ownership.
2. **Writing:** the one-transaction rebuild, the write lock, `ensure_fresh`, the detached rebuild child; `impact`, `analysis`, the survey and the recall CLI call it.
3. **The daemon stops parsing:** the endpoints start the child; delete the watcher, the health-hook refresh, the commit hook's code-repo leg, both reindex jobs and their handler; light `/summary` and the daemon stamp; fix the false text.
4. **Auto-recall drops `codeintel`;** the eval case.

### 0.8 Premise review (r3, one round)

One correctness premise review: 2 HIGH, 5 MED, 3 LOW. All were checked against the code and are folded in above:
- **HIGH:** TECH.md as a second, unguarded ownership path → one registry with a human confirm. A rebuild was not atomic across processes → one transaction.
- **MED:**
  - inline full rebuild against the 120 s Bash timeout → a detached child;
  - lock losers that must not skip → explicit rebuilds wait;
  - two daemon readers missing from §0.5 → added;
  - the recall CLI never refreshed → it calls `ensure_fresh`;
  - the registry writable by an agent → pending/confirmed.
- **LOW:** the build-verification probe parses in the daemon → rule 1 names it and keeps the pre-warm; an incomplete deletion list → completed; delete rather than build → the hourly job, the full job and the commit hook's code-repo leg go instead of being retuned.

Left as they are:
- `governed_repo_roots` (`ddd_bindings.py:203`) still reads free paths for DDD refresh and self-audit, which are not code-intel ownership.
- The reviewer ran out of budget before checking Hive-side readers. A Hive receives no graph (Brain sync never carries one, `brain_sync.py` docstring), so it has none to read.

### 0.9 As implemented: where it differs from the plan

- **The hourly job stays, changed (departs from rule 3).** The repo's documents used to follow HEAD only through the reindex job (`doc_index.sync_repo_after_head_move`), so deleting the job would have frozen recall of repo docs at bind time. `code-intel-reindex` now runs `ensure_fresh` for every owned graph (one `git rev-parse` for a repo that did not move; up to 100 files inline; more, or a graph not built yet, a detached child), which also re-syncs the docs. It is a job process, never the daemon, so rule 1 holds. The full-rebuild job is gone.
- **One commit for steps 1–3.** Their tests interleave (every graph writer's test needs the new ownership), so they land together; step 4 (auto-recall, the eval case) lands on its own.
- **The children are `subprocess.Popen` with `start_new_session`,** started from `asyncio.to_thread` (`build.spawn_child`), not `asyncio.create_subprocess_exec`: the same launcher serves the daemon endpoints, the job and `ensure_fresh`, and the endpoint keeps the `Popen` to answer "already indexing".
- **`bind_repo` and the Library mount index take the graph's write lock** (bind waits, a second mount index answers `already_indexing`), and the rebuild child skips a clone while a bind holds it. The plan named the lock for `build` only; without it a child started before a bind raced the bind's rebuild.
- **The eval case** `CI_IMPACT_FRESH` (`scripts/probe_code_intel.py`) fits the cap: the product set was at 49 when it was added.
- **Tests:** the §0.6 invariants are in `tests/test_code_intel_r3_invariants.py` (a second process reads mid-rebuild; a SIGKILLed writer; every `ensure_fresh` state; Confirm as the only writer of `confirmed`; an AST scan that no daemon module references a graph writer) and `tests/test_code_intel_local_roots.py`; each was checked by reverting what it guards (`scripts.mutation_check`).

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
