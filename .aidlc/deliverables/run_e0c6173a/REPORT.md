# # Make the system prompt byte-stable across sessions so new sessions reuse the … · Audit through DELIVER

Delivered: # Make the system prompt byte-stable across sessions so new sessions reuse the prompt cache \*\*Evidence (measured on two Hives, 2026-11-15).\*\* Across 37 new sessions, the first model call read only 11,. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 1h 36m wall · 1h 36m active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 4 not run · 1 red at base · 10/11 findings closed (5 test-closed, confirmed by review) · a requirement not covered by the design |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 1.3M comparable · 1.0M input · 300k output · 109.2M cache reads, not counted · 110.5M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 4 (+4 found on demand) · searched the DDD 3×

## Decisions

- D1 · Does the Onboarding section (BOOTSTRAP.md) stay in the system prompt? → It moves into the session-context block with the other ephemeral sections, first, as before. Why: BOOTSTRAP.md is not one of the governed context files; moving it keeps the rule 'system prompt = framing + governed files' and keeps the order of everything the model sees (DESIGN\_SPEC, agent)
- D2 · Where does the UI-state block go on the first query? → The router renders it with \_decide\_ui\_block after the session-context block and the recall stash, the order a warm turn already uses; build\_system\_prompt no longer renders it. Why: One prefix builder and one UI decision for cold and warm turns; the first query records what the subprocess received, so the next warm turn can send the one-line 'unchanged' form (DESIGN\_SPEC, agent)
- D3 · How do the hooks that read the user's words tell the block from typed text? → The session-context block (moved sections plus the first turn's DDD, Project Agent and recall blocks) is wrapped in SESSION\_CONTEXT\_HEADER and a line-start SESSION\_CONTEXT\_FOOTER from core.injected\_blocks; strip\_injected\_blocks cuts through the last footer line. Why: DailyActivity and the DDD block have no closing shape of their own; the language classifier would otherwise read them as typed text on every first turn. The last footer is used because the block's own content (recall of this very design) can quote the footer (DESIGN\_SPEC, agent)
- D4 · What keeps the moved parts after a context compaction, which keeps the system prompt but summarizes the first message? → The wrapped block is kept on the hook session context and appended once to the existing post-compaction UserPromptSubmit context (the Project Agent block is skipped when it is already inside); the kept copy is cleared at the start of every first-query turn. Why: Parity with the system prompt, which survived compaction; reset-on-every-attempt follows the recorded pitfall c1a77bce (DESIGN\_SPEC, agent)
- D5 · Does the system-prompt destination of recall stay as an option? → No: should\_mutate\_system\_prompt is removed from \_deliver\_block and the recall/DDD/contract helpers; every injected block goes to the per-turn query stash. Why: send() is the only production caller and would always pass False; one mechanism for cold and warm turns, no dead branch (DESIGN\_SPEC, agent)
- D6 · Is the legacy resume branch (SWARM\_RESUME\_VIA\_QUERY off) changed? → No; with the flag off a cold resume still appends history to the system prompt. Why: Only resumed sessions reach it, never a new session; retiring the flag (and setting it on the Hive service) is a separate change, noted as a follow-up (DESIGN\_SPEC, agent)
- D7 · Where is the first API call's cache usage taken from? → The first main-agent message\_start stream event after each spawn: SessionUnit arms a flag in \_spawn and logs one 'session\_unit.first\_call\_cache' line with input, cache\_read and cache\_write, then disarms. Why: message\_start carries the per-call usage and arrives before any token; one attribute check per API call, nothing per token, no IO beyond one log line (DESIGN\_SPEC, agent)
- D8 · How is the real-daemon before/after measured without restarting the person's daemon? → A second daemon (uvicorn main:app, SWARMAI\_MODE=dev) on its own port, SWARM\_DATA\_DIR and HOME, with a copy of the live .context files; three new sessions about a minute apart for each code version; first-call usage read from the CLI transcripts, TTFT and assembly time from the daemon log. Why: Same machine, same context files, same model; the live service is not touched (AGENT R12) (DESIGN\_SPEC, agent)
- D9 · Does backend/context/SELF.md need the T10 edit? → No edit: the repo's SELF.md source says nothing about where the date/time tail or recall sit, so nothing there is contradicted. Why: R4.1 holds without touching a governed context file (BUILD report · swarmai, agent)
- D10 · What about the one respawn that starts without the transcript (an abandoned --resume after two timeouts)? → Found while testing the design: it would have lost the session context the system prompt used to give it; retry\_manager now prefixes the kept block on that respawn (SessionUnit.\_with\_kept\_session\_context), with an execution test. Why: AGENT R28 recovery paths; every other respawn resumes the transcript, which already holds the first query (BUILD report · swarmai, agent)
- D11 · How is the after-daemon run without the hooks writing into the repo? → The after-daemon ran from a copy of the worktree's backend in /tmp; the baseline daemon ran from the main checkout and its context\_health hook rewrote docs/CODEBASE\_METRICS.md there (left uncommitted, reported). Why: Keep the run's tree clean (BUILD report · swarmai, agent)
- D12 · Fix the R2 residual stale docstrings and the probes in TEST? → Yes; it reopens REVIEW for the incremental change. Why: AGENT R7: a comment describing changed behaviour is fixed in the same change; the probes are product eval cases that would go red on this change (TEST report · swarmai, agent)
- D13 · Fix the R2 note on recall\_replay's dedup text in TEST? → Yes, one line: reuse session\_router.\_recall\_seen\_text. Why: The comment I wrote was wrong; the production helper makes the replay match by construction (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep the sections in the system prompt but freeze the volatile ones (date rounded to the day, sibling digest dropped). Not taken: DailyActivity, the North Star headline, the deferred MCP list, the UI state and the cold-start recall/DDD block still differ per session, and the CLI sends the system prompt as one cache block, so the first call would still write the whole block; it also deletes information instead of moving it.
- A3 · Place our own cache breakpoint after the context files, or use the SDK preset prompt with exclude\_dynamic\_sections. Not taken: The CLI owns the cache\_control breakpoints (no cache\_control anywhere in our code) and SwarmAI passes a custom string prompt, not the claude\_code preset, so neither is available without forking the CLI.
- A4 · Move only the date/time tail to the query and leave the other ephemeral sections and recall in the system prompt. Not taken: Measured prompts of consecutive sessions differ in the sibling list, DailyActivity and recall too, so the block would still be rewritten on every new session.

Requirements analysis:

- missing\_edge (R2.6): A context compaction keeps the system prompt but summarizes the first user message, so the moved sections (deferred MCP instructions, DailyActivity, North Star, recall) would be lost after compaction where today they survive. The session-context block is kept on the hook session context and appended once to the existing post-compaction UserPromptSubmit context, next to the Project Agent block it already re-sends. → rewritten
- missing\_edge (R2.5): The hooks that read the prompt as the user's words (language classifier, correction detector, high-signal capture, run-intent reader) would see DailyActivity and the DDD block as typed text on every first turn, flipping the R19 language check. The block is wrapped in a header and a line-start footer like the resume block, and strip\_injected\_blocks cuts it. → rewritten
- missing\_edge (R2.3): Pre-warmed units spawn with build\_options and are reused on their first message, so the block built at pre-warm time would be discarded with the unused system prompt. The first message to a pre-warmed or adopted unit is treated as the first query of a new process and carries a freshly built block. → rewritten
- conflict (R1.1): With SWARM\_RESUME\_VIA\_QUERY off (the code default, and the Hive service file does not set it) a cold resume still appends the history block to the system prompt. That path is only reached by resumed sessions, not new ones; retiring the flag is a separate change and is left as a follow-up. → confirmed
- vague (R1.2): The Onboarding section comes from BOOTSTRAP.md, which is not one of the governed context files; it moves into the session-context block with the rest so that the cold system prompt equals the pre-warm one and the governed-files-only rule holds. → confirmed
- vague (R2.1): The UI-state block moves from the middle of the old ephemeral group to after the recall block, the order a warm turn already uses, so cold and warm turns share one prefix builder and the UI dedup state (\_last\_ui\_block) is set by the first query. → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 5/27 tool calls
- correctness · review r1: read 100% of its prompt · 14/27 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 17/25 tool calls
- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 74ed4b70ce9b: 9 pass, 0 fail, 0 not run
- test · swarmai @ 024e3cb34c0c: 10 pass, 0 fail, 4 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV' ../../../../SwarmWS/Hive/work/so\_e0c6173a4c3d/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_prompt\_cache\_stability.py tests/test\_steer\_send.py tests/test\_ → pass
- perl -e 'alarm 110; exec @ARGV' ../../../../SwarmWS/Hive/work/so\_e0c6173a4c3d/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_recall\_replay.py -q --timeout=60 --maxfail=1000 -p no:cachepro → pass
- env -u SWARMAI\_MODE perl -e 'alarm 60; exec @ARGV' ../../../../SwarmWS/Hive/work/so\_e0c6173a4c3d/repos/swarmai/backend/.venv/bin/python -m scripts.probe\_recall privacy → pass
- env -u SWARMAI\_MODE perl -e 'alarm 115; exec @ARGV' ../../../../SwarmWS/Hive/work/so\_e0c6173a4c3d/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_eval\_fresh\_install.py -q --timeout=100 --m → pass
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- 5 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 860 tests: only tests/test\_precommit\_uv\_lock.py (3) fail, the same way on the run's base (pipeline-v1 base-tree); eval fresh-install passes after the probes were moved to the new contract

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; run on the landed tree through task\_checks
- swarmai · npm run lint:contract: no desktop file changed; run on the landed tree through task\_checks
- swarmai · npm run build: no desktop file changed; run on the landed tree through task\_checks
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run on the landed tree through task\_checks

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_prompt\_cache\_stability.py
