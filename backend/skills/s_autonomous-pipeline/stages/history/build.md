# BUILD — history and rationale (never read at runtime)

Why the BUILD stage looks the way it does. The runtime rules live in `stages/build.md`
(card) and `stages/ref/build/*.md`. Nothing here is a rule; anything here that reads like
one is already in the card or a ref.

## The 2026 split (design `docs/design/pipeline-speed-systematic-fix.md` §4-B, §4-C)

The old `stages/build.md` (~54.7K chars) was read in full on every BUILD entry. It split
into a 5K execution card, six refs (gate1, tdd, pre-checks, verify, smoke, artifact) and
this file. All 16 tagged rules (R-build-001..016) live in the refs with their markers.

Command translations made in the split (raw test commands are denied by
`pipeline_test_gate` while a run is active):

| Old command | Now |
|---|---|
| `pytest tests/test_foo.py tests/test_bar.py --timeout=60` (VERIFY) | `RUN --mode verify` |
| `grep -rl "from database\|import database\|SQLiteDatabase" tests/` then run those files | `--mode verify` reverse-import closure (cap 40) |
| `pytest --lf --timeout=60` | `RUN --mode recheck` |
| per-cycle `pytest <file>` / `npx vitest run <file>` in the RED→GREEN loop | `RUN --mode tdd --files <≤5>` |
| `go test -run`, `cargo test <name>`, `mvn test`, `npm test` (other stacks) | `RUN --mode probe --name <stack>-<step> -- <argv>` |
| `cd desktop && npm run build:all` (frontend geometry smoke) | `--mode probe --name frontend-build --deadline 600 -- <wrapper>` under `alarm 610` |
| `tsc --noEmit`, `cargo check`, `go build` as smoke compile steps | `--mode probe` |
| `publish --type changeset ... --data '...'` + `advance --state review` | `stage-complete --stage build --data @/tmp/<R>/build.json` (legacy path kept in `ref/build/artifact.md`) |
| `run-update --stage-json` for gate1 fields only | still written early, AND repeated in the stage-complete `record` |

Inconsistencies resolved in the split:
- Step 1 said "Commit: this is your tracer bullet" while Step 2 forbade `git commit`
  (`run-commit` is the sole commit point, `active_run_commit_gate` denies it). The tracer
  bullet is now staged, not committed.
- Gate 1 Execution said "Assemble the 5 input sections" while the input list has 6; the
  sixth (`understanding.work_type`) is the one Check 4 polarity depends on. Now "6".
- `stage-complete` replaces the build stage record and carries forward only
  `artifact_id`, `adversarial_review`, `lessons`; Gate 1 fields written only by
  `run-update --stage-json` would vanish, so they now also go in the `record`.

Dropped as pure duplication of INSTRUCTIONS:
- The long continuation-contract paragraph (`advance` / `next_stage_doc` wording): the
  card keeps the short form; the full contract is the Continuation contract in
  INSTRUCTIONS (Handle Result).
- The spawn-rejection decision tree: the ref points at the spawn-rejection rule
  (`gate_spawn_blocked`) in INSTRUCTIONS Rules and keeps the BUILD-specific consequence.
Concurrent edit kept: an uncommitted change made `run_in_background: false` (a background
spawn ends the visible stream) part of the Gate 1 spawn step; it is in the card step 1
and in `ref/build/gate1.md` Execution (`tests/test_pipeline_spawn_foreground.py` checks
the card).

## 🚨 "Are you about to skip TDD?" (the old opening table)

CLASS A (skipping TDD) had 11 occurrences and 0 self-corrections when this was written.

| What you're thinking | Why it's wrong |
|------|------|
| "This is just a config change / one-liner" | 3 files, 2 functions, "simple" → user caught 2 bugs |
| "I'll write tests after to verify" | Tests written after pass immediately; you never see them catch the bug |
| "I already know the implementation works" | 57 tests green, 10/10 confidence → feature 100% non-functional in production |
| "TDD is overkill for this change" | 5/5 HIGH adversarial findings in "trivial" session fixes came from untested code (2026-05-26) |
| "Let me get the code working first, then add tests" | You WILL rationalize test coverage to match existing code |
| "The pipeline profile is trivial, so less rigor" | Profile selects STAGES, not RIGOR within stages |
| "I'll do a quick prototype then formalize" | Prototypes never get formalized; the "quick" version ships |
| "The plan says X, but I see a better approach mid-implementation" | You haven't reviewed implications for other ACs; micro-replan or back to PLAN |

## Common rationalizations (the old closing table)

| Rationalization | Reality |
|---|---|
| "Tests pass, implementation is complete" | 57 tests green, feature non-functional: a state machine with 6 declared states had only 4 wired, and the tests only exercised those 4 |
| "TDD is overkill for this simple change" | A "simple" pytest hook took 5 iterations because tests came after code; final 55 lines vs a first attempt of 130+ with 3 bugs |
| "I'll write tests after — I need to explore first" | Tests written after validate IMPLEMENTATION, not BEHAVIOR; they pass by construction |
| "This is a refactor — no new tests needed" | STEERING "Extract ≠ Extend": a new caller of an extracted function has different invariants |
| "Smoke tests are redundant with unit tests" | Units verify logic; smoke verifies WIRING. All units green while the real entry point never reached the path |

## Incidents behind the steps

- **Iron law / tracer bullet:** one recorded case took 5 iterations because tests came
  after the code; another had 57 tests pass while testing the wrong thing.
- **Failure-path test (Step 2 #12):** `consume_events_for_job` consumed events
  unconditionally, so a failed job lost them. Adversarial review caught it, not TDD; a
  failure test would have forced the decision before implementation.
- **Micro-replan:** without a trigger the agent either retried the same broken approach
  indefinitely or checkpointed to the user over a simple approach mismatch. Modeled on
  AutoGPT-style replanning on failure, scoped to one AC.
- **API existence (1.5):** a run shipped `parse_result.nodes` (AttributeError) and
  `graph.bulk_replace()` (does not exist). Unit tests mocked the boundary and passed; the
  E2E force-run failed. A 10-second Read would have caught both.
- **Mechanism declaration (1.7):** a run used `lock_path.unlink()` to "break" a stale
  flock. flock is bound to the inode/fd, so deleting the path created a new inode and two
  processes could each hold "the lock". Adversarial review caught it as a known pitfall.
- **Path symmetry (2.5):** two `flock_exclusive_nb()` calls reached "lock acquired"; only
  the first wrote the PID. The test exercised the stale-break path's existence, not its
  postconditions. PE review caught it.
- **Real-data replay (2.7):** a label parser passed its hand-written table, then mis-handled
  362 real stored summaries (`2>/dev/null`, `|| exit`, quoted exports) at TEST, one stage
  later than necessary and after review had passed the code.
- **Caller verification (3.5):** PE review (2026-05-19) found `create_governance_file_gate()`
  fully tested and review-passed with zero production callers (never registered in
  `hook_builder.py`).
- **Interface seam (3.6):** PE review (2026-05-20): HeartbeatManager's Protocol required
  `send_message_raw` / `update_message_raw` / `delete_message_raw`; the Slack adapter had
  none. 24 mock-based unit tests passed and adversarial review called the Protocol fine.
  `_post_ack` called `send_typing_indicator`, which ignores `text` (always "Thinking...").
- **Proximity scan (3.8):** 2026-05-25, a constants-only change drew 4 adversarial
  findings: a docstring with the old formula, a test comment with the old value, and a
  priority skill reaching HIGH with 1 correction (callers send `min_examples=3`).
  Builder momentum ("I know what I changed") hides stale surrounding context.
- **SMOKE (Step 4):** `self.config_manager` (wrong attribute name) passed 8 pipeline
  stages because it sat inside try/except and no test exercised the runtime path.
- **Pattern grep:** a session fixed `"version":"` JSON parsing in one function and missed
  3 identical `contains("\"healthy\"")` patterns 200 lines away.
- **Resource Lifecycle:** added after Voice Input shipped 3 CRITICAL subprocess bugs past 14
  green unit tests + 4 smoke tests, because mocks replaced the whole subprocess lifecycle.
- **User-path trace (Step 5):** a run with 26 TDD tests and 10/10 confidence had 2
  CRITICAL bugs found in 5 minutes of tracing: `_on_error` fired before the health
  monitor, the gateway destroyed the adapter, `_ws_fail_count` reset and polling never
  activated; and `conversations.history` messages lack `channel`, so
  `external_chat_id=""` broke routing. Third recurrence of engineering-complete !=
  user-complete.
- **Wire PROBE (Step 6):** Voice Input's explicit Content-Type header broke the Axios
  multipart boundary. 14 unit tests + 4 smoke tests + an integration trace missed it
  because none sent a real multipart request through the ASGI stack.
- **Pre-change greps:** a 3-phase job split was right in the execute path but dry-run
  showed only 2 of 3 categories (PE review). Adding a regex to `render_user_data()` broke
  3 tests passing `s3_bucket="b"` and `auth_hash="$2a$..."`. A parallel commit added a
  stub of the method another session was adding: duplicate definition in one class.
- **Source verification (Step 7):** `asyncio.get_event_loop()` (deprecated in 3.12+) and a
  `date('now')` UTC mismatch passed the pipeline because nothing checked current docs.
- **AC Coverage Matrix:** 12 runs shipped "passed" with features broken in production.
  BUILD could skip ACs undetected: the validator checked only `tdd.green_pass` (tests that
  WERE written pass), not that all required tests exist. The matrix forces AC → code →
  test → verified.
