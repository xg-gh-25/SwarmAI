---
title: Code intel r5 — the end-to-end pass
created: 2026-10-29
updated: 2026-10-29
status: built (A–D), reviewed (correctness on A+B, security on C+D), fixed in one pass
follows: 2026-10-29-code-intel-auto.md (r4)
---

# Code intel r5: what the end-to-end audit found, fixed in one pass

r4 made the graph build and refresh itself and put the callers report into every
review. This pass ran the whole path on the deployed build (18:39) with real changes
and real data, collected every problem first, and fixes them together. The test the
work is held to: **a change that breaks a caller is named as breaking before it
lands, with few enough false leads that an expert reads the list.**

## 1. Findings (each observed this turn)

**The report**

- **F1. A docstring-only edit ranks as a body change.** On commit `a6effd24b`
  (`ReclaimReport` fields removed, docstrings rewritten) the first symbol listed is
  `ddd_entry_lifecycle.parse_entries`, "body changed", 33 outside callers. Its only
  change was a docstring line.
- **F2. "Names it without a call" lists comments.** All five files listed for
  `parse_entries`, `reclaim_duplicate_entries` and `retire_entry` name them only in a
  comment or docstring (`ddd_usage.py:24`, `ingestion/writer.py:318`,
  `knowledge_graph.py:399`, `ingestion_gate.py:789`, `conversation_extract.py:184`).
- **F3. The report cannot say what breaks.** Commit `dcd95e518` removed the
  `dry_run` parameter from `reclaim_noise_entries`, `reclaim_duplicate_entries` and
  `retire_entry`. Three sites kept passing it:
  - `artifact_cli.cmd_ddd_retire`: a TypeError on every `ddd-retire`;
  - `context_brain._entry_health_counts`: the TypeError is caught, so the C&M
    entry-health counts silently became None for a day;
  - `probe_memory`: the `MEM_RECLAIM` probe crashed.

  The report lists callers, but nothing says "this call passes an argument that no
  longer exists". A reviewer has to open every caller.
- **F4. On a large change the budget drops the breaking symbols first.** On
  `dcd95e518`, 430 changed symbols were not looked up, 177 of them signature
  changes. The `dry_run` removals were among them. The order was "first lines
  touched, then file order", and nothing ranked a removal above an edit.
- **F5. A removed member is invisible.** `ReclaimReport.archived` and
  `.new_content` were removed; the report said `ReclaimReport` has "no caller and no
  mention outside this change". It has no notion of a removed field, method or
  function.

**The graph**

- **F6. Most name-only edges are calls on library objects.** Of 11,026 name-only
  `calls` edges in the live SwarmAI graph:
  - 7,953 are on a local or parameter (`cmd.extend`, `conn.execute`);
  - 1,092 on a call result (`Path(x).resolve()`);
  - 742 on a dotted value;
  - 360 on `self.x` (`self._last.clear()`, `this.repos.get()`).

  The top targets are `AppConfigManager.get` (795, i.e. every `dict.get`),
  `GraphStore.resolve` (718, every `Path.resolve`) and `MessageStore.replace` (634,
  every `str.replace`). The receiver's value is a literal, a builtin or an object of
  an outside library, which the source states (`cmd = []`,
  `conn = sqlite3.connect()`, `p: Path`, `private repos = new Map()`). No repo
  method can be the target.

**The pipeline**

- **F7. A second-round review has no callers report.** In `run_8a1af4d4`,
  `security-r2-a2` was written at 18:40, after the deploy, without one.
  `review_address` never builds it; r4 D4 said every round should have it.
- **F8. A Hive review never has a graph.** On a Hive the run's repo is the task
  clone `<SwarmWS>/Hive/repos/<host>/<owner>/<name>` (`task_repos.repo_dir`, cloned
  by the agent). Brain sync strips `worktree`. No code builds a graph for that
  clone. `owned_graphs` points at `<bindings>/<repo>`, which does not exist. So every
  Hive review gets "this checkout has no code graph".
- **F9 (withdrawn on reading the code).** The usage join was thought to count
  findings on the changed files. `changed.listed_files` lists only outside callers,
  mentions, name-only leads and field reads; a changed file is never listed. No change;
  the join line now also carries the report's break count.

**Operations**

- **F10. Brain Hub shows "stale" with nothing refreshing it.** `/summary` reports
  stale and asks for nothing. The graph stays stale until the half-hourly job or a
  reader updates it.
- **F11. An outdated graph whose rebuild parses empty starts a child every ten
  minutes, for ever** (r4 residual).
- **F12. Two pieces of git hardening are wrong.**
  - `repo_read.GIT_HARDENING`'s `-c diff.external=` makes a patch diff fail.
  - `ddd_bindings._git_toplevel` runs git with the repo's own hooks and fsmonitor
    config live.

**Checked and fine:**
- New Brain classification on the live daemon: repo root, inside a repo, a folder
  of two repos, a plain folder, `$HOME` and `~/.ssh` (the last two are SHELF).
- The parser-version rebuild ran by itself after the deploy (`parser_version=1`,
  39,078 nodes, no one touched it).
- Brain Hub's `graphs` endpoint and repo picker.
- The native callers report on a real commit, in 6.5 s.

## 2. The fixing spec (grouped by cause)

### A. The report answers "what breaks", not only "who calls" (F1, F3, F4, F5)

New module `core/code_intel/interface.py`. For each changed code file it parses the
**base** version and the **new** version (tree-sitter, the parser's grammars) into a
per-symbol interface:

- **For functions:** parameters with name, kind (positional / keyword-only / `*args`
  / `**kwargs` / TS optional or rest) and whether each has a default.
- **For classes:** fields (Python class-level annotations, dataclass fields,
  `self.x =` in methods; TS property declarations, `this.x =`, constructor parameter
  properties), method names, and the constructor's parameters (`__init__`, or a
  dataclass's fields).
- **For every symbol:** a hash of its tokens without comments and without a Python
  docstring.

`delta(old, new)` gives per symbol:
- removed; removed parameters; parameters that became required;
- `*args` / `**kwargs` dropped;
- removed fields; removed methods;
- **comments-only**: the token hash is unchanged.

Then the report:

- **Ranks and budgets by delta.** The order is:
  1. removed symbols;
  2. removed or now-required parameters, removed fields or methods;
  3. other signature changes;
  4. body changes.

  Comments-only changes are folded into one count and never looked up (F1). The
  delta needs only the changed files, so it is computed for all of them before any
  caller lookup, and the caller budget runs in that order (F4).
- **Checks each call site against the delta (F3).** It parses each outside caller's
  file, finds the call at the edge's line whose callee is the symbol, and reads its
  arguments:
  - a keyword naming a removed parameter;
  - more positionals than the new signature takes;
  - a new required parameter that is not passed.

  A `*`/`**` splat or spread is never judged. Each site that passes one of these
  checks is listed under **"will break"** with the reason. A method call counts only
  through an instance, never through an unbound `Cls.m(obj, …)`. A class's callers
  are checked against its constructor.
- **Removed members (F5).** For a removed function or method, every outside caller
  will break. For a removed field, the report lists outside files that read
  `.field` in code (not in comments) and also refer to the class, a function
  returning it, or its module, as "reads a removed field, by name".
- **Code-only mentions (F2).** A file counts as naming a symbol only if the name
  occurs in a code token: an identifier or a property. A string counts only when it
  holds the dotted path (`pkg.mod.f`, `Cls.m`), as a mock does. Comments and
  docstrings do not count.
- **Render.** Definite breaks go at the top: "N call sites will break", each with
  its reason. JSON adds `breaks` and per-symbol `delta`.

### B. The graph drops calls on library values (F6)

`receivers.py` evaluates the type of a receiver **expression**, not only a single
name. The result is a repo class, `EXTERNAL`, or unknown:

- **`EXTERNAL`:**
  - literals and comprehensions;
  - a builtin constructor that is not shadowed (`list()`, `open()`);
  - a call to, or attribute of, a name imported from a module outside the repo;
  - annotations that name builtins or outside types (`str`, `list[X]`, `Path`,
    `string`, `T[]`, `Map<…>`);
  - TS globals under `new` (`Map`, `Set`, `Date`, …);
  - any member or call on an `EXTERNAL` value.

  A module is outside the repo when its top-level name is in
  `sys.stdlib_module_names`, or is not the name of any directory or file stem among
  the repo's source files. For TS/JS: a bare specifier whose first segment is not
  such a name.
- **Repo class, from a stated type:**
  - `self.x` / `this.x` take the type of the class's attributes: every binding of
    the attribute in the class gives the same type (class-level annotation,
    `self.x = Store()`, `self.x = db` with `db: Database`, TS property
    declarations, `private store: Store` constructor properties);
  - `f()` / `x.m()` take the function's or method's return annotation when it is
    defined in the same file;
  - `x as T` takes `T`.
- **Unknown:** as today, the call keeps the name-only link.

A call on an `EXTERNAL` receiver gets **no** edge. The rule "every binding the same
type" is unchanged, and `EXTERNAL` counts as one type. `PARSER_VERSION` becomes 2, so
every graph rebuilds itself after the deploy (r4 D3).

### C. The pipeline gets the report in every round, on a Hive too (F7, F8, F9)

- **F7:** `review_address` builds the callers block of the fix diff, from the trees
  the round-1 handouts answered to the current trees, and puts it first in each R2
  `review_input`.
- **F8:** on a Hive, a task clone under `<SwarmWS>/Hive/repos/` whose `origin`
  matches a binding's `clone` URL (the same match `task_repos` already uses) is that
  Brain's repo. `owned_graphs` adds it, and its db lives under
  `<app data>/bindings/hive/`. The path must resolve inside `Hive/repos`. This is
  the binding declaring ownership, not a document. When a pipeline run starts, the
  engine asks for a build child for each run repo whose owned graph is missing or
  stale, so the graph is there by REVIEW. This holds on the desktop too.
- **F9:** withdrawn (see §1); the join line adds `breaks`.

### D. Operations (F10, F11, F12)

- **F10:** `/summary` on a stale graph asks for a `--if-stale` update child, with
  the same ten-minute marker as `request_rebuild`, and reads "updating".
- **F11:** an empty parse by the current parser records
  `empty_parse_version = PARSER_VERSION`, and `parser_outdated` treats that as
  current.
- **F12:**
  - `repo_read.git` passes `--no-ext-diff --no-textconv` to `diff`, `show` and
    `log`, and the empty `diff.external` goes;
  - `_git_toplevel` passes `core.hooksPath=/dev/null` and `core.fsmonitor=false`.

### E. Out of scope (recorded)

- Return types from another file; container element types.
- Interface deltas for languages other than Python, TS and JS. For those the delta
  is "removed symbol" plus comments-only, read from the graph's own nodes, and
  their parameters are not compared.
- The three tests on main that are red from other sessions.

## 3. Verification plan (decided now)

**Tests written with the fix, each mutation-checked:**
- interface delta per kind: removed / required / kw-only / `*args` / fields /
  methods / comments-only, for Python and TS;
- call-site checks: a keyword removed, too many positionals, a required parameter
  missing, a splat not judged, an unbound method not judged;
- the ranking under a budget of one: a break first, comments-only never looked up;
- code-only mentions: a comment is not a mention, a mock path is;
- receiver `EXTERNAL`: `cmd = []`, `sqlite3.connect()`, `p: Path`,
  `Path(x).resolve()`, `new Map()`, `x: string[]`: no edge;
- `self.x` typed from `__init__` and from an annotated parameter;
- a same-file return annotation;
- R2 handout carries the block;
- Hive ownership: an origin match is owned, a mismatch or a path outside
  `Hive/repos` is not;
- run start spawns the build;
- join outside-callers;
- `/summary` stale asks for an update;
- empty parse stops asking;
- git hardening: a repo whose config sets `diff.external` to a script does not run
  it, and a patch diff works.

**Invariants at the end (final set, run once):**
- all `tests/test_code_intel_*`;
- `test_pipeline_review_callers.py`, plus the pipeline review tests of
  `engine.review_address`;
- `test_brain_bootstrap*` (survey git);
- `test_ddd_bindings*`;
- the eval product-set tests.

**Value, measured on real data:**
1. **Replay `dcd95e518`:** build a temporary graph at its parent and report the
   commit. `artifact_cli.cmd_ddd_retire` and `context_brain._entry_health_counts`
   must be listed as "will break" (passing `dry_run`). `probe_memory` must be listed
   as passing the function as a value.
2. **Real graph before and after:** name-only edges and the share of them on
   library values; total edges; dead-code count.
3. **Report on `a6effd24b`:** `parse_entries` no longer listed (comments only), and
   no comment-only mentions.

**Golden case:** `CI_IMPACT` (count unchanged) also requires that a caller passing a
removed keyword is listed as "will break". The negative turns the call-site check
off, and the case must then fail.

**Reviews:**
- correctness on A + B (interface, call-site checks, receiver evaluation);
- security on C's Hive ownership.

One round each, fixed in one pass.

**After the build:**
- one real pipeline run on the deployed system, profile `trivial`, on a change that
  removes a parameter. Its review handouts (R1 and R2) must carry the block with the
  break listed, and the usage log must have the join;
- the live graph rebuilds to `parser_version=2` by itself.

## 4. As built (where it differs from §2, and why)

- **Every interface change is checked for breaks, however large the change.** The
  first replay of `dcd95e518` checked nothing. The change has 1,200 symbols, and the
  `MAX_EXAMINED` cap fell before the `dry_run` removals. The check is cheap (the
  graph's edges plus call sites from cached parses), so it now runs over all of them
  first, before the capped listing. Every symbol with a break is listed whatever the
  caps are.
- **Callers in a file the change edits are checked too, against the new version.**
  In `dcd95e518` both real breaks (`artifact_cli.cmd_ddd_retire`,
  `context_brain._entry_health_counts`) sat in files the same commit edited. The
  author worked around the call and left it as it was. The graph's line is carried to
  the new version through the diff's hunks. A call the change fixed is not named.
- **A compatible signature change is its own rank.** For example, an optional
  parameter added: it is looked up after the breaking ones and before body changes,
  and reads "signature changed; existing calls still fit it".
- **A name-only call named like a builtin type's method** (`data.get()`,
  `s.replace()`, `rows.push()`) gets its own band, `BUILTIN_NAME_CONFIDENCE = 0.3`.
  The Python names are read from the builtin types themselves; TS/JS uses the standard
  prototypes. Dropping these calls would have left 21 real repo methods with no edge
  (`BaseTable.get`, `MessageStore.remove`, …), so they are kept for reachability, but
  never listed as callers or leads.
- **A class whose fields went away inside a method** (`self.x = …` removed from
  `__init__`) is added to the report: its first lines were not touched.
- **Mentions:** a string that is exactly the name counts (`getattr(store, "save_all")`,
  a registry key); a docstring never does.
- **The `_git_toplevel` hardening (F12) cannot be observed.** `rev-parse` runs no
  hook and no fsmonitor, so a test cannot tell it apart. It is kept as defence in
  depth, the same as r4's `rev-parse` fsmonitor case.

**Measured on the SwarmAI repo (same tree, the r4 parser against this one):**

| | r4 parser | this pass |
|---|---|---|
| name-only calls (leads) | 11,026 | 4,826 |
| calls named like a builtin method (0.3 band, not leads) | — | 3,391 |
| calls on a stated type | 7.0K | 8.9K |
| total edges | 91.2K | 91.8K |

**Replay `dcd95e518`** (`scripts/code_intel_replay.py`, a throwaway graph of its
parent): **11 callers will break**. They include:
- `context_brain._entry_health_counts` passing `dry_run=` twice (the silent None in
  the C&M counts);
- `artifact_cli.cmd_ddd_retire` passing `dry_run=`;
- `summarization._enrich_with_llm` now requiring `open_next_items`, which
  `test_enrichment_truncation` does not pass;
- six tests still calling functions the commit removed.

Two of those tests are still red on main today:
- `test_pipeline_v1_knowledge_report.py` calls `reflect_job.candidates_for` /
  `annotate_entry`;
- `test_ddd_orchestrator.py:465` calls `_ch_entry_lifecycle`.

The `probe_memory` reference is a `**{…}` splat and a function passed as a value, so
it is not judged and is listed as a reference.

**Replay `a6effd24b`:** `parse_entries`, `_match_entry_line` and
`reclaim_noise_entries` fold into "changed only in comments or docstrings". The
mentions found earlier only in comments are gone.

## 5. Reviews (one round each, fixed in one pass)

**Correctness (A + B): 1 HIGH, 10 MEDIUM, 3 LOW.** Most were confirmed by a
reproduction. Grouped by cause and fixed:

- **A moved symbol read as removed (HIGH).** A function re-exported from its old
  module, a method pulled up into a base, or a def moved into an `if` / `try` block
  was read as removed. Now "removed" is a break only when the name is no longer
  reachable. `bound_names` covers imports, assignments and definitions inside compound
  statements. A method counts as removed only if its class has no base, in this file or
  elsewhere, that could still provide it. Definitions inside `if` / `try` / `with` are
  read too.
- **Other false "will break" cases.** A call is judged only when it is the one call of
  that name the edge can stand for: two calls of the name on the line, or in a
  rewritten hunk, are not judged. Arguments are counted only from an argument list (a
  generator argument counts as one). JavaScript reports only removals, because a
  missing or extra argument never fails at runtime. A method called through any class
  name (a subclass, an alias) is not judged. A dataclass with bases is not judged, and
  `kw_only` / `KW_ONLY` fields are keyword-only. The last definition of a name is the
  one that binds, so `@overload` stubs are replaced by the implementation.
- **False EXTERNAL that dropped repo edges.**
  - `cast` / `copy` / `deepcopy` / `replace` return their argument's type.
  - A repo generic (`Node[int]`, `Response<T>`) is checked before the builtin and
    global names, each set used only for its own language.
  - A TS import named like a global (`import * as crypto`) is not a global.
- **A non-UTF-8 source escaped `report()`.** The report's git reads now decode with
  `errors="replace"`.
- **Field reads.** `self.a, self.b = …` sets fields too.
- **Declined, with the reason in the code.**
  - Stdlib-first for an absolute Python import: `import types` reads the stdlib, and
    the repo-names set holds names at any depth.
  - The per-process repo-names cache: every parse runs in a short-lived process,
    never in the daemon.

**Security (C + D): 1 MEDIUM, 3 LOW, no HIGH.** The git hardening held against
every repo-controlled program tried: external diff, textconv, a pager, fsmonitor,
filters through `include.path`.

- **MEDIUM: Hive ownership trusts state the agent can write.** Now the path may hold
  no link (one inside `Hive/repos` would give another clone this binding's name). The
  directory must be the top of its own tree, and must have fetched from its origin
  (`refs/remotes/origin/*`), so a bare `git init` no longer counts.
  - **What remains, recorded:** the origin and the refs are still git state the
    Hive's agent can write. That agent can write the Brain's documents directly too,
    so ownership adds no reach it lacked.
  - **Not built:** a daemon-made clone record. The daemon does not clone task repos
    today (the agent does), so the record would need a new clone step.
- **LOW: the rebuild marker was check-then-act.** It is now claimed under an `flock`
  before the child starts. When the marker cannot be written, no child is started.
- **LOW: the replay tool.** It uses the hardened git, refuses an option-shaped
  commit and a repo with a filter program, and removes only the two worktrees it made.
- **LOW: missing tests.** Added tests for a bare init, a link inside `Hive/repos`, a
  subdirectory of another tree, concurrent readers (one spawn), textconv on `show` /
  `log -p`, and a latin-1 source.

**Mutation check.** Every fix of the pass and of the review round, 60 mutations in
all, was caught by its test. Three needed a sharper test first: two calls on a line,
the marker race, and tuple-assigned fields. One protection is not observable by a
test: `_git_toplevel`'s hooks/fsmonitor settings, since `rev-parse` runs neither.
One review-fix guard turned out to be a no-op (the `@overload` skip, made redundant
by "the last definition binds") and was deleted.

**After the review round, replay `dcd95e518`:** still the same 11 callers will break,
every one a real break.
