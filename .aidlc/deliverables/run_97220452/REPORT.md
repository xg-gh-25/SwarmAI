# # Fix: four session-runtime gaps left by 02a5b6c48 and c788c736c · Audit through DELIVER

Delivered: # Fix: four session-runtime gaps left by 02a5b6c48 and c788c736c \*\*Evidence (2026-11-16, from the two runs' own reports).\*\* (1) The model-access warmer (\`backend/core/model\_access\_warmer.py\`) learns h. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 1h 3m wall · 1h 3m active · no baseline yet (3/5 runs) |
| Quality | WARN | 1/1 verification · 1 red at base · 3/4 findings closed (5 reported; within 10 lines of one file they count once) (1 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 786k comparable · 600k input · 185k output · 45.9M cache reads, not counted · 46.7M billed · host-session responses in the run window · no baseline yet (3/5 runs) |

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 3 (+6 found on demand) · searched the DDD 3×

## Decisions

- D1 · How does the warmer know the person was active across a restart? → it persists the time of the last activity (and the learned key) in a small JSON in the app data dir, written by its own loop off the event loop when it changed; at boot start() loads it. Why: note\_activity stays synchronous and IO-free on the chat path; the loop already runs at most POLL\_S apart while active, so the persisted time is at most a minute old (DESIGN\_SPEC, agent)
- D2 · When does the boot warm-up start? → main.py lifespan calls model\_access\_warmer.start after the leftover-CLI sweep and after the thinking gateway's listen port is set. Why: earlier, the sweep could kill the warm-up's CLI and the built template's base URL would differ from chat's (DESIGN\_SPEC, agent)
- D3 · Count the warm-up in the budget or only skip when full? → both: it starts only when the budget admits one more CLI (as before) and counts as a staged spawn from that check until its close and kill finished. Why: only counting makes a chat spawn during a warm-up see the real number of CLIs; the warm-up lasts about 0.4-2 s (DESIGN\_SPEC, agent)
- D4 · Where do the warm template's fields come from? → PromptBuilder.warm\_up\_template, using the same resolve\_model, thinking display, gateway env and an extracted \_chat\_cli\_settings that build\_options now also calls. Why: one builder for the settings means the warm-up and chat cannot drift apart in what decides the cache key (DESIGN\_SPEC, agent)
- D5 · Extract and extend in separate commits (AGENT R4)? → one change: the worktree gate refuses commits in a run's worktree and landing makes one commit. Why: the extract (\_chat\_cli\_settings) is byte-identical and covered by the existing build\_options suites plus the new parity test (BUILD report · swarmai, agent)
- D6 · A loop that a chat turn starts warmed at once beside that turn's own cold spawn (seen in the first sandbox run: two probes, two places in the budget) → a loop started by activity waits AFTER\_ACTIVITY\_S (30 s) before its first warm-up; start() at boot does not. Why: the turn's own spawn re-probes meanwhile, so the deferred loop finds the entry fresh and sleeps to its expiry; mutation-checked (BUILD report · swarmai, agent)
- D7 · A unit whose hook enqueue raised at shutdown was never killed → its kill now runs anyway (same function, found while making it concurrent). Why: otherwise its CLI outlives the daemon (BUILD report · swarmai, agent)
- D8 · Fix CF-38a490bb (LOW, raised in R2 after the review's fixing pass) now or leave it residual? → fixed in TEST, which reopens an incremental review. Why: the path was made by this change's yield\_to\_chat; leaving it would orphan a warm-up CLI outside the budget (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Gap 1: persist the last chat spawn's whole WarmTemplate (with its environment) across restarts. Not taken: the chat env can carry an egress-proxy address, the app session id and other per-session values; writing it to disk on the spawn path is a hot-path write and a secret-on-disk risk, the last spawn may be a channel spawn with another key, and it does not help an install whose first chat comes after the restart
- A3 · Gap 1: warm at boot without the activity rule (always warm once at startup). Not taken: breaks the rule that the warmer runs only while the person is active: an unattended daemon restart would spend a CLI handshake for nobody
- A4 · Gap 4: move the pending claim before the prefix composition. Not taken: widens the window between the claim and the try/finally that rolls a claimed set back (an exception or a GeneratorExit in between strands claimed rows), and evaluates the claim's COLD precondition earlier than the spawn
- A5 · Gap 4: insert the pending text after the leading prefix by splitting the already-prefixed query. Not taken: string surgery on a composed query is fragile, and for the multimodal list form the prefix boundary is ambiguous
- A6 · Gap 3: one overall shutdown deadline that cancels slow closes. Not taken: the per-CLI grace already bounds each close and the kill follows it; a concurrent close is bounded by one grace, so a second deadline would only cut the exit records the grace exists for

Requirements analysis:

- missing\_edge (R1.1): a warm-up at boot before the leftover-CLI sweep would be killed by it, and before the thinking-gateway port is set the template's base URL would differ from chat's → rewritten
- missing\_edge (R2.1): counting the warm-up can refuse a chat spawn during the \~0.4-2 s handshake on a machine already at its budget; that is the budget's own judgment of real memory, and the warm-up itself only starts when the budget admits it → rewritten
- missing\_edge (R3.1): the /shutdown endpoint caps disconnect\_all at 8 s: with sequential closes 4 idle CLIs already reached it → rewritten
- missing\_edge (R4.1): the claimed rows' own terminal output stays the leading UNTRUSTED block, as today → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 6/26 tool calls
- correctness · review r1: read 100% of its prompt · 14/26 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 34a426f98810: 10 pass, 0 fail, 0 not run
- test · swarmai @ e26e97700f5c: 14 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: pass
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: pass

TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_early\_spawn.py tests/test\_graceful\_cli\_close.py tests/test\_model\_access\_warmer.py tests/test\_prompt\_cache\_stability.py tests/test\_ → pass (the change's tests (gaps 1-4 and the review fixes) plus the warmer, early-spawn, graceful-close, prompt-cache and persistence suites)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_prompt\_builder\_properties.py tests/test\_recall\_injection.py tests/test\_steer\_send.py tests/test\_ui\_state\_dedup.py tests/test\_proje → pass (suites the two earlier commits touched and the chat scenario E2E)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_commit\_attribution.py tests/test\_compaction\_tuning.py tests/test\_bedrock\_thinking\_gateway.py tests/test\_knowledge\_restricted\_chann → pass (build\_options and disconnect\_all callers, spawn gate and recovery paths)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_silent\_except\_baseline.py -q --timeout=60 -p no:cacheprovider → pass (silent-except baseline after the running\_count log fix)
- python scripts/smoke\_import.py → pass (brain check, run in the worktree with the task's venv on PATH)
- python scripts/lint\_problem\_values.py → pass (brain check, run in the worktree with the task's venv on PATH)
- python scripts/security\_scan.py → pass (brain check, run in the worktree with the task's venv on PATH)
- python scripts/lint\_skills.py → pass (brain check, run in the worktree with the task's venv on PATH)
- python scripts/lint\_experts.py → pass (brain check, run in the worktree with the task's venv on PATH)
- bash scripts/sync-version.sh check → pass (brain check, run in the worktree with the task's venv on PATH)
- 5 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 463 of 466 pass; the 3 tests of tests/test\_precommit\_uv\_lock.py fail the same way on the run's base (/tmp/swarmai-base/run\_97220452): \`uv\` is not on this machine's PATH, so UV is None in subprocess.run
