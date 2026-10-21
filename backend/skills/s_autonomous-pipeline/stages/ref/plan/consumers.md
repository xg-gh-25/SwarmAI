# PLAN ref — Contract readers (`implementation_constraints`)

Read when: writing the plan's `implementation_constraints`, BUILD or a Gate-2 fix was refused
by the consumers gate, or you need to declare a reader found after PLAN.

Gates: `[GATE·validator]` the shape at PLAN (strict profiles; direct: warnings) ·
`[GATE·cli]` the readers at BUILD stage-complete and before `gate2-next --fixed N` /
`gate2-recheck` — both from `design_spec.CONSTRAINTS_CUTOVER`.

## Why

The most frequent Gate-2 finding class is a reader of a changed contract left as it was.
It usually sits inside a file the change already touched, so checking files misses it, and
the "grep all consumers" rule (`ref/build/pre-checks.md`) was in context every time it
happened. So the readers are now declared and checked by code: `scripts/consumers_gate.py`,
at BUILD stage-complete and before `gate2-next --fixed N` / `gate2-recheck` hand out a
re-check.

## What to write

A contract is anything another piece of code reads: a function's signature or return
shape, a set of states or statuses, a file or record format, a field's meaning.

```json
"implementation_constraints": {"consumers": [
  {"id": "K1", "contract": "load_x now returns (value, problem)",
   "pattern": "load_x\\(", "paths": ["backend"],
   "readers": [{"at": "backend/a.py::read_a", "status": "migrated"},
               {"at": "backend/b.py::Cls.log_b", "status": "unaffected",
                "reason": "only logs the call, never reads the value"}]}]}
```

No shared contract changes → `{"no_contract_change_reason": "<why, >= 20 chars>"}` instead.

- `id`: `K<n>`.
- `pattern`: a `git grep -E` regex that finds every reader. Search the name the readers use
  (the function, the field, the status literal), not your own call site.
- `paths`: tree roots only (`.`, `backend`, `desktop`), read literally. A deeper path means
  you choose the files to search, which brings back the blind spot the search exists to
  remove; pathspec magic (`:!x`, `:(exclude)…`) and glob characters are refused.
- `readers[].at`: `path::function` (`Class.method`, `outer.inner` for nested defs),
  `path::<module>` for module-level code of a `.py`, `path` (= `path::<file>`) for a
  non-Python or unparsable file. Absolute paths of the main checkout or the worktree are
  accepted and normalized.
- `status`: `migrated` (the diff must change that unit) or `unaffected` (needs a `reason`,
  >= 10 chars).

## What the gate checks

1. Every unit (`path::function`) with a hit of the pattern is either changed by the run's
   diff (from the worktree's base commit, new files included) or declared `unaffected` with
   a reason.
2. Every unit declared `migrated` is changed by the diff itself: a change to another
   function of the same file does not count. A deleted file counts for every unit in it.
3. A pattern git rejects is refused, naming the `K<n>`.
4. A pattern with no hit now and none at the base is refused (it checks nothing). Hits at
   the base and none now pass: every reader was removed.

The gate cannot see the diff (no repo, the base not resolvable) → a warning that says the
readers were NOT verified. It never reports that as a pass.

Runs created before `design_spec.CONSTRAINTS_CUTOVER` get the findings as warnings.
Direct runs get the PLAN shape errors as warnings (a form about itself); the BUILD and fix
checks run on any profile that declares consumers.

## A reader found after PLAN

Migrate it, or declare it, then re-run the refused command:

```bash
python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> \
    --add-consumer-reader 'K1|backend/c.py::other|unaffected|reads only the length'
```

Stored in order in run.json `consumer_readers`. For each unit the latest declaration decides
(PLAN readers first, then these), so a later declaration can correct a PLAN reader; adding
an entry again makes it the latest again. An `unaffected` override still needs its reason.
DESIGN_SPEC.md shows every declaration the gate judges, each one made after PLAN marked
with the PLAN status it replaced (rendered again on each add and at completion).
