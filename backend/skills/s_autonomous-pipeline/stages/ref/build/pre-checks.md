# BUILD ref — checks before and between code changes

Read before writing code that calls another module or an OS mechanism, before changing a
shared variable/function/file, after each GREEN on stateful code, and when the change
reads persisted data. `RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`.

## Step 1.5: API Existence Check (BLOCKING for handler/integration code) `[MUST]` <!-- R-build-005 -->

**Before writing any code that calls functions from another module**, for each external
call in the Change Spec:
1. **Read** the target module file (not from memory).
2. **Confirm** the function exists with the expected name.
3. **Verify** the return type matches your destructuring.
4. **Verify** the argument signature matches your planned call.

```
API CHECK:
- parse_repo(repo_root) → returns list[ParseResult] (NOT single ParseResult)
- GraphStore.bulk_insert(parse_results) → exists ✓
- GraphStore.bulk_replace() → DOES NOT EXIST ✗ (use clear() + bulk_insert())
```

Why blocking: "I assumed the API shape from memory" bugs are structurally undetectable by
unit tests that mock the very API being called wrong; a 10-second Read catches them.

**Skip when** every call targets code YOU wrote in this session.

## Step 1.7: Mechanism Declaration (BLOCKING for system API usage) `[MUST]` <!-- R-build-006 -->

**When code depends on an OS/system mechanism** (flock, signals, atomicity, file
ordering, process lifecycle, env var inheritance), declare it before coding:

```
MECHANISM: <what system behavior you rely on>
ASSUMPTION: <what you believe is true about that mechanism>
VERIFY: <how you confirmed — man page, empirical test, or citation>
```

Mechanisms that need a declaration:
- File locking (flock, fcntl, lockf): inode vs path semantics (deleting a lock file's
  path does NOT release a flock; the lock is bound to the inode/fd)
- Signal delivery (SIGTERM, SIGKILL): timing, child propagation
- Environment inheritance: what launchd/systemd/cron pass to children
- File atomicity: rename vs write, O_CREAT|O_EXCL
- Process death detection: PID reuse window, waitpid vs kill(0)

The declarations become the adversarial reviewer's attack-surface list: instead of
reviewing the diff cold, the reviewer asks of each ASSUMPTION "is this actually true?".

**Skip when** the code only uses standard library functions with well-understood
behavior (dict, list, pathlib basic ops, string formatting).

## Pre-Change Checks `[MUST]` <!-- R-build-012 -->

> The "BLOCKING" grep-before-you-change rules in this section are agent-discipline
> `[MUST]` (no validator enforces them) — they encode R27 (grep all consumers) at BUILD
> time. <!-- R-build-013 -->

### When splitting a variable into multiple categories

**BLOCKING: grep ALL references to the original variable/path.** <!-- R-build-014 -->

When one list/path becomes several (e.g. `jobs` → `time_based_jobs` + `dep_based_jobs` +
`event_based_jobs`), grep EVERY code path that consumed the original
(`grep -n "due_jobs\|time_based\|dep_based" backend/jobs/scheduler.py`); each must now
handle all categories. Common misses are the PARALLEL paths: dry-run/display, error
handlers, logging, metrics, serialization, status. The execute path is the obvious one.

### Before adding validation/constraints to existing functions

**BLOCKING: grep all callers before adding input validation.** <!-- R-build-015 -->

`grep -rn "function_name(" . --include="*.py" --include="*.ts"`. Every existing call
site (including tests) is a potential breakage: read the arguments they actually pass and
make the validation accept existing valid inputs.

### Before editing a file modified by parallel sessions

Check `git log -5 --oneline -- <file>`. If another session modified it since your last
read, re-read the whole file (a parallel commit can add a stub of the method you are
adding, leaving a duplicate definition in one class).

## Step 2.5: Path Symmetry Check (after each GREEN)

**After implementing any stateful operation** (lock acquire, file write, state
transition, resource allocation), enumerate ALL code paths that reach the same logical
end state:

```
PATH SYMMETRY for: <operation, e.g. "acquire evolution lock">
  ✓ Happy path: flock succeeds → write PID → flush
  ? Retry path: flock succeeds after stale break → ???
  ? Error recovery path: ???
  Postconditions that MUST hold on ALL paths: PID written; lock_fd open and held
  MISSING: retry path doesn't write PID ← FIX NOW
```

Mechanical process (count paths, list postconditions, check each; ~30 seconds):
1. Identify all `if/else/except` branches that reach the same "success" state.
2. List the postconditions the happy path establishes.
3. For each alternate path: does it establish the SAME postconditions?
4. A missing postcondition on any path = write a test + fix NOW.

Trigger: any code with `try: ... except: ... # retry` (the retry often misses setup),
`if stale: break_and_retry` (re-acquires but skips initialization), multiple
`return success` points (earlier returns skip later setup/cleanup), or fallback paths
(reach the goal differently but may skip side effects).

## Step 2.7: Real-Data Replay (when the change reads PERSISTED data) `[MUST]` <!-- R-build-007 -->

**Trigger:** the change parses, projects, classifies or renders data that already exists
on disk: DB rows, transcripts, artifact/run JSON, log lines, stored tool summaries, any
file a previous run wrote.

**Do:** before leaving BUILD, run the new code over the REAL persisted corpus (all of it,
or a sample big enough to hit the long tail) and count the outcomes that matter:
exceptions, unparsed/fallback shapes, values that come out empty or wrong. Fix what it
finds and add the shapes it found to the unit tests. Read the corpus; never write to it
(copy it first if the code under test writes). A replay you will repeat can run as
`RUN --mode probe --name replay-<x> -- <script>` (JSON findings, converges at 0 new).

**Why at BUILD and not only at TEST Layer-4:** unit tests from imagined inputs test the
shapes you thought of; the real corpus is the specification of which inputs exist. Read
it while the code is still cheap to change.

**Skip when** the change reads no persisted data (a pure function over new input, a docs
change, a UI change with no stored-data path).
