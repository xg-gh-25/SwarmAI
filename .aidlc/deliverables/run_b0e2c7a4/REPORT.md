# 起 pipeline：P0+P1 修复。Make SwarmAI's own autonomy numbers honest and close run-in… · Audit through DELIVER

Delivered: 起 pipeline：P0+P1 修复。Make SwarmAI's own autonomy numbers honest and close run-integrity holes found by today's audit against the CNTech &amp; IVT v0.2 standard. Decided by XG: run end state stays main-land. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 1h 14m wall · 1h 14m active · 48.6% budget |
| Quality | WARN | 1/1 verification · 1 not run · 3/3 findings closed (2 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 779k comparable · 617k input · 163k output · 55.5M cache reads, not counted · 56.3M billed · host-session responses in the run window · 19.1% budget |

Project Agent: SwarmAI · code-change · 11 checks · 0 rules · 2 watches

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 2 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · Does a bare continue nudge typed by the person count as a correction? → Yes, unchanged. Why: The run stopped and needed the person to go on: that is an intervention. Only system-origin turns are taken out (DESIGN\_SPEC, agent)
- D2 · Which provenance kinds take a message out of the corrections? → Only system\_prompt (system and host origin). Why: channel\_message is another human (a channel peer) steering the run: still a human intervention. A person's message has user\_message, or only external parts (an attachment or quote) when it has no typed text, and is then read by the text rule as before (DESIGN\_SPEC, agent)
- D3 · How are several learned ids of one short name priced? → One short-name fit over the union of their CLI rows, accepted by the same rule as any fit, kept under a key no Bedrock id can have ('\~' + short name). Why: Deterministic and principled; when the ids price differently the union fit fails MAX\_ERROR and the call stays unpriced, as today (DESIGN\_SPEC, agent)
- D4 · Re-price ledger rows already written with no cost? → No. Why: No write to the usage ledger; the usage report already lists them as unpriced. New rows and new run measurements are priced (DESIGN\_SPEC, agent)
- D5 · Scope cuts from the requested list. → Out: REVIEW-skip guard (fixed in ad2763ba9), stuck Hive runs (fixed in 01492960a), rewriting two old Swarm Out summaries, a Wilson interval in the Pipelines view, keeping cache-write tokens apart in run.json. Why: Each was already fixed, would need guessing an identity from timestamps, or is a deliberate design (comparable input figure; the view already shows unattended / known with its denominator) (DESIGN\_SPEC, agent)
- D6 · Where does the Agent-Run line sit in the message? → In the same trailer paragraph as the repository's trailer (appended to the message's last paragraph when that is already all trailer lines). Why: git reads trailers only from the last paragraph; a second paragraph would hide Co-Authored-By. The no-extra path keeps the old byte-for-byte message, so a plan frozen before the change still matches its hash (BUILD report · swarmai, agent)
- D7 · What does land\_blocker do when the run's result cannot be read? → Refuses nothing. Why: The landing's own checks (prepared tip, person-only paths, conflict) still decide; failing closed on a read error would push the landing to the person (BUILD report · swarmai, agent)
- D8 · A message with only an attachment or quote from the person (no typed text) has no user\_message provenance. → Only system\_prompt rows are left out; every other row keeps the text rule. Why: Taking out only what the ledger marks as system-built never drops a person's message (BUILD report · swarmai, agent)
- D9 · Fix review finding CF-518a053d (left residual by the closed review) or leave it? → Fixed in TEST, so the run goes back to REVIEW for that change. Why: It is a defect in the change itself and the fix is small: a full id with no \[1m\] now names the standard tier and is priced only at a fit that tier taught (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Write a new origin flag on every messages row at insert (metadata.origin for system turns) and read only that. Not taken: Rows written before the change would have no flag and would need a backfill of the chat store; session\_inputs already records origin for every user row since 2026-10-29 (measured: all user rows from 10-30 on have a provenance record), which covers every pipeline v1 run.
- A3 · Price by normalising the model id at the caller (run\_protocol/usage.py) to the full Bedrock id. Not taken: Only fixes one caller; usage\_accounting.\_price has the same miss for direct calls recorded as us.anthropic.claude-opus-5-5 (231 unpriced rows) and the full id to map to is not knowable from a short name.
- A4 · Put the Agent-Run line into the landing message text the engine builds. Not taken: The landing plan freezes each repo's message hash; a run whose plan was written before the change would fail as another immutable plan. Carrying it as a frozen plan-row field keeps old plans landing unchanged.
- A5 · Refuse an agent landing whenever a declared check is not\_seen or unknown too. Not taken: not\_seen means the host found no call that runs the check, not that it did not run (design 2026-10-31-swarm-out-agent-owns-its-runs §2.7); it would block checks run through a script and hand work to the person. errored is the Hive's own last exit code.

Requirements analysis:

- missing\_edge (R2.2): A bare continue nudge ("继续") the person typed is counted as a correction: the run stopped and needed the person to go on, which is the definition of an intervention. The audit's suggestion to drop it is not taken. → rewritten
- missing\_edge (R4.3): The landing plan freezes each repo's commit message hash; a run whose plan was written by the old code would fail as 'another immutable plan' if the message changed under it. → rewritten
- missing\_edge (R5.2): not\_seen means the host found no call that runs the check, not that it did not run (design 2026-10-31-swarm-out-agent-owns-its-runs §2.7); refusing on it would block checks run through a script and send work to the person. Only errored, the Hive's own last exit code, refuses. → rewritten
- missing\_edge (R3.1): Rows already written with a NULL cost are not re-priced (no write to the usage ledger); the usage report already lists them as unpriced. cache-write tokens stay folded into a run's comparable input figure by design (2026-10-26 pipeline-review-closure §6.2); the ledger keeps all four kinds. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 6/26 tool calls
- correctness · review r1: read 100% of its prompt · 21/26 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 285356e990b8: 11 pass, 0 fail, 0 not run
- test · swarmai @ 1706ec52c22a: 7 pass, 0 fail, 1 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- python -m pytest tests/test\_run\_interventions.py tests/test\_usage\_prices.py tests/test\_swarm\_out\_actor.py tests/test\_hive\_records.py tests/test\_repo\_pipeline\_config.py -q --timeout=60 → pass
- python -m pytest tests/test\_run\_protocol\_landing.py tests/test\_hive\_return.py tests/test\_pipeline\_v1\_conformance\_k2.py -q --timeout=60 → pass
- python -m pytest tests/test\_usage\_prices.py tests/test\_bedrock\_id\_region.py tests/test\_usage\_accounting.py -q --timeout=60 → pass
- python -m scripts.mutation\_check /tmp/p0p1/mut3.json --timeout 100 -- python -m pytest tests/test\_usage\_prices.py -q -x → pass (the standard-tier fix taken out turns test\_usage\_prices red (1 of 1 caught))
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- bash scripts/run\_bvt.sh → not\_run (the whole backend BVT is CI's run; the change's tests and every caller test of the changed contracts ran in BUILD and here)

Tests were not run again after main moved: tests carried over a main move: swarmai (main 60324a86d691-&gt;e7508df2fcfa): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: the whole backend BVT is CI's run; the change's tests and every caller test of the changed contracts ran in BUILD and here
