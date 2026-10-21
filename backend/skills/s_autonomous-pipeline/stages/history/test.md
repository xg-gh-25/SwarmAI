# TEST — history and rationale (never read at runtime)

Why the TEST stage looks the way it does. The runtime rules live in `stages/test.md`
(card) and `stages/ref/test/*.md`. Nothing here is a rule; if something here reads like
one, it is already in the card or a ref.

## The 2026 split (design `docs/design/pipeline-speed-systematic-fix.md` §4-B, §4-C)

The old `stages/test.md` (~15.6K chars, read on every TEST entry) prescribed raw
commands per layer. Measured runs showed fragmentation as the main cost: pytest
called 10-58 times per run, a single test file re-run up to 34 times, and one run
spending 48 minutes on manual hang triage (ps/kill/faulthandler). The runner
`backend/scripts/pipeline_test.py` replaced all of it. Raw commands are allowed while
a run is active, but they do not count as landing or push_ready evidence. Command translations made in the split:

| Old command | Now |
|---|---|
| `artifact_cli.py discover --types changeset --full` + `pytest -xvs -k "<ac tests>"` | `--mode regression` (reads `ac_coverage` itself) |
| `grep -rl "from module_name\|import module_name" tests/ \| xargs pytest -x --timeout=60` | `--mode regression` (AST reverse-import graph, cap 40) |
| `grep -rl ... src/ \| xargs npx vitest run --reporter=verbose` | `--mode regression` (`vitest related` + `tsc`) |
| `python -c "import sys; sys.path.insert(0, '.'); import module_name"` | `--mode regression` (import smoke) |
| re-run failed tests after a fix | `--mode recheck`, then a final `--mode regression` |
| revert the contract line by hand, re-run the Layer-4 test | `--mode mutation --mutations spec.json` (backend) or `--mode probe` wrapper (frontend) |
| `go test -run`, `cargo test`, `mvn -Dtest`, `go build`, `cargo check`, `tsc --noEmit` (other stacks) | `--mode probe --name <x> -- <argv>` |
| `cargo check --target <other>` | `--mode probe` wrapper (see ref/test/cross-platform.md) |
| `artifact_cli.py publish --type test_report ... --data '{...}'` + `advance --state deliver` | `stage-complete --stage test --data @/tmp/<R>/test.json` (layers/regressions derived from runner evidence) |

The old "Exit Evidence Checklist" (Layer outputs pasted, WTF shown, fixes listed, ...)
became `record.exit_evidence` keys in the card; layer outputs no longer need pasting
because the runner evidence is the record.

## "STOP — are you about to skip test layers?" (the old opening table)

The temptation at TEST was never skipping testing entirely. It was running only Layer 1
(the AC tests you wrote) and declaring "tests pass". Layers 2 and 3 catch the bugs your
own tests can't, because you wrote both the code and the tests. The runner now runs all
three in one call, so most of these excuses no longer have a lever to pull.

| What you're thinking | Why it's wrong |
|------|------|
| "My unit tests pass, that's sufficient" | Unit tests verify YOUR assumptions. Layer 2 catches collateral damage to code that imports yours. |
| "Dependency-scoped grep found 0 files" | Wrong grep pattern? 0 results for a non-trivial change = suspicious, not reassuring. (The runner's AST graph replaced the grep.) |
| "Import smoke is pointless for existing files" | Circular imports, missing deps, wrong relative paths: invisible to unit tests that import individual functions. |
| "I'm running low on budget, skip Layer 3" | Layer 3 was one command per file, usually <10 s total. It cost less context than the sentence rationalizing the skip. |
| "Tests passed in BUILD, why run again?" | BUILD tests ran BEFORE the refactor step. Post-refactor state may differ. Fresh run = fresh evidence (SOUL P1, verify rather than infer). |
| "Full suite would be better but takes too long" | Never run the full suite (STEERING R9): it has deadlocked under pytest-xdist. Layers 1-3 are the scoped alternative; skipping them leaves NO regression signal. |
| "The fix was obvious, no need for WTF scoring" | Score every fix. The WTF gate is mechanical, not discretionary. "Obvious" fixes touching 4+ files are not obvious. |

## Common rationalizations and the incidents behind them

| Rationalization | Reality |
|---|---|
| "Tests pass, no need for a scoped re-run" | Pass in isolation ≠ pass together. Mock-based tests all passed while the real DB had zero matching rows: the function returned an empty string in production. |
| "This fix is simple, skip the WTF score" | One "simple" fix touching 4 files took 5 iterations because each fix revealed new scope. |
| "I'll adjust the test expectation to match the new behavior" | Changing expectations = changing the spec = back to PLAN. Tests define correct behavior. |
| "Pre-existing failure, not our problem" | Log it in IMPROVEMENT.md "Known Issues". A silently passed red test erodes the signal; today's "pre-existing" is tomorrow's "we thought it was fine". |
| "19 fixes done, just one more" | 20 is the hard cap. Historically the 21st fix introduces more bugs than it solves (WTF score data). |
| "Tests are flaky, re-run until green" | Flaky = non-deterministic = a real bug (race, shared state, time dependency). STEERING: a test passing 9/10 times FAILS. |
| "All units pass, the migration is done; Layer 4 is overkill" | Units pass ONE side of a seam. One delivery had every unit green while the ACT/SENSE contract was silently severed; adversarial review caught it with a real E2E. That incident created Layer 4. |
| "I wrote a Layer-4 test and it's green" | Green ≠ non-vacuous. A test that mocks the thing-under-change, or stays green when the contract line is reverted, is theater. Hence mutation-verification. |

## Incident notes

- **Circular import (Layer 3 origin):** a feature passed all unit tests but crashed on
  real import because of a circular dependency. Unit tests imported individual
  functions, never the module.
- **RP40 (Single-Platform Compile Trap origin):** a `#[cfg(target_os="macos")]`
  function referenced by an un-gated caller broke the Windows build (E0425) while
  `cargo check` on the dev Mac reported clean. Adversarial review caught it.
- **`overlayHostE2E.test.tsx` (canonical Layer-4 example):** 7 surfaces × real
  registry × real event; reverting open-on-show turned all 7 RED, which is what a
  non-vacuous seam test looks like.
- **Fuzz without an end (design §4-G):** a direct fix ran ~10 fuzz rounds (>40 min)
  after the fuzz had stopped finding anything, one round silently ran a stale alphabet
  (macOS `sed` escaping), and mutation testing ran 4 times because the change was not
  isolated and `--maxfail` truncated it. Probe convergence and mutation mode exist so
  the tool notices "same as last round" and "change not applied".

## Dropped as duplication of INSTRUCTIONS.md

- The long continuation-contract block (advance is only a state transition; read the
  next stage doc in the same turn; `next_action` is authoritative because the payload
  is measured at advance time and prose cannot see run state). INSTRUCTIONS.md §3f
  carries it in full; the card keeps the shared phrase and the legal hand-back list.
- The "Test Execution Summary" numbered list (L1 → L2 → L3 → fix → WTF → re-run). It
  restated the layer sections; the card's numbered steps replace it.
- The `[GATE·cli]` "read the stage doc" requirement and the schema-first advice. Both
  live in INSTRUCTIONS.md §3c; `stage-start` now returns the valid example directly.
