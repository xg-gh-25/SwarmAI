# A unit's verified quote of the person's words, confirmed by user\_check, admits … · Audit through DELIVER

Delivered: A unit's verified quote of the person's words, confirmed by user\_check, admits it to every-turn files whatever else it cites, and TIMELINE placement keys on explicitly third-party evidence (tool name. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 55m 41s wall · 55m 41s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 5 not run · 2 red at base · 4/8 findings closed (10 reported; within 10 lines of one file they count once) (3 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 1.0M comparable · 808k input · 240k output · 71.8M cache reads, not counted · 72.9M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · What counts as Hive-written text, with no Hive tool in a session? → The Hive lesson documents (agent\_document roots whose locator starts with the hive: prefix core/hive\_lessons registers); Hive results read through the swarm\_out CLI are Bash output and stay the accepted residual. Why: No command parsing is allowed; the lesson documents are the only Hive-written roots the ledger names as such (DESIGN\_SPEC, agent)
- D2 · Is an inline attachment block of the person's message third-party? → Yes (ledger kind attachment); quoted or fenced text the person pasted is not. Why: It is the same content as a file under Attachments/, which the task names; pasted quotes are not named (DESIGN\_SPEC, agent)
- D3 · What does a live event whose evidence origin cannot be told get? → Not injected (fail closed); the backfill keeps such an event where it is. Why: The month summary is read every turn; an unknown is not evidence that the content is the person's own work (DESIGN\_SPEC, agent)
- D4 · Does a month-section event keep the taint:external mark? → No: in TIMELINE the mark goes with Not injected; the recorded basis (unit\_basis) stays external, so trust registration is unchanged. Why: The task's backfill removes the mark on move; live and backfill then agree, and the baseline reads the basis from unit\_basis (DESIGN\_SPEC, agent)
- D5 · How does the backfill write a re-decided unit when its op id is already done? → A new candidate cand-&lt;sha256('cdf-backfill-b1:'+old)\[:24\]&gt; copying the old inbox row (same payload, envelope, evidence), decided by run\_unit\_v2 and written by writer.apply\_unit; then the DDD copy is withdrawn as overridden. The deterministic id makes a second run skip. Why: The writer's op id is per unit id and the identity regex is cand-&lt;hex&gt;.unit; a new candidate is the existing shape (several units per candidate) (DESIGN\_SPEC, agent)
- D6 · This run reverses the DDD decisions 'Tool-result trust is decided by origin' and 'relaxing pipeline\_v2 for quoted corrections rejected'. → Follow the person's newer task, which rejected that attempt; the DDD entries describe the rejected design. Why: The person's words in this task are the latest decision (DESIGN\_SPEC, agent)
- D7 · Is a channel\_message root always third-party? → Only with external trust (a peer); the channel owner's message is registered with user trust and is the person's. Why: channels/gateway records the owner's words as channel\_message with trust user (BUILD report · swarmai, agent)
- D8 · The pipeline run's own tool\_text root ('&lt;run&gt;:repo/tool context') has no ledger row. → Not third-party. Why: It is the run's own repo and tool work; treating it unknown would keep every run event out of the month (BUILD report · swarmai, agent)
- D9 · AGENT.md R22 says the every-turn files take 'nothing citing external content'. → Not edited here (governance, human gate); proposed in the delivery summary. Why: Governance text changes through s\_self-evolution with the person's approval (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Classify each tool call's origin (Bash command parsing, per-subcommand allowlists) and register local results as inferred trust. Not taken: Rejected by the person after review found seven bypasses; git log stays unverified and taints the rest of the session, so corrections still never reach EVOLUTION. The task forbids parsing shell commands and changing how tool results are registered.
- A3 · Store the third-party flag in a new session\_inputs column (data.db schema v24). Not taken: Other workers are adding data.db migrations at the same time (v22/v23 already collided once); test fixtures create session\_inputs with ad-hoc DDL. The ledger's free-text kind already says what an input is, nothing reads kind 'tool\_result', and the evidence root (kind tool\_text, trust external) is unchanged.
- A4 · Derive the third-party answer only on the read side from the transcript, recording nothing at write time. Not taken: Transcripts are pruned; the item asks for the flag recorded where the tool name and input are known. Derivation is kept only as the fallback for rows recorded before the change (and for the backfill).

Requirements analysis:

- undefined\_term (R2.1, R2.3): 'Hive-written text' has no tool of its own in a session (Hive results are read through a Bash CLI, which may not be parsed): it is taken as the Hive lesson documents (agent\_document roots registered by core/hive\_lessons with the hive: locator prefix). An inline attachment block of the person's message (ledger kind attachment) is counted as third-party like a Read under Attachments/; quoted or fenced text the person pasted is not. → confirmed
- missing\_edge (R2.3, R3.1): A root whose origin cannot be told (unknown root, a legacy tool result whose transcript is gone) keeps the event out of the injected month (fail closed) in the live path, and keeps it where it is in the backfill. → rewritten
- missing\_edge (R3.3): The S0 month summary cites the month's events as corpus\_unit roots, which are external trust once tool\_text events sit in the month; corpus units are not third-party, so the summary stays under its month instead of falling to Not injected. → rewritten
- missing\_edge (R4.3): Rerouted units whose inbox record was purged (30 days) or whose DDD copy is no longer in its file (the person removed it) are skipped and reported, never rewritten. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 12/26 tool calls
- correctness · review r1: read 100% of its prompt · 21/27 tool calls
- correctness · review r2: read 100% of its prompt · 15/25 tool calls
- security · review r2: read 100% of its prompt · 16/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 4c2fa349c54e: 4 pass, 0 fail, 0 not run
- test · swarmai @ ac9d3edbb48f: 8 pass, 0 fail, 5 not run
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

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_cdf\_user\_words\_every\_turn.py tests/test\_cdf\_third\_party\_timeline.py tests/test\_cdf\_backfill\_user\_words\_timeline.py -q -p no:cacheprovider --timeout=60 -x → pass
- .venv/bin/python -m pytest (cdf refactor, v2\_provenance, v2\_writer, writer\_core, timeline\_entry, v2\_memory\_summary, user\_check, v2\_pipeline, s7\_review, v2\_sources\_s2, sources\_core, launch\_security, ag → pass
- .venv/bin/python -m pytest tests/test\_cdf\_v2\_e2e.py -q --timeout=120 → pre\_existing (only test\_s3\_real\_pipeline\_lands\_reflects\_and\_writes\_five\_parts and test\_s3\_project\_without\_ddd\_goes\_to\_the\_general\_brain fail, identically on the base tree (the test repo has no git user identity on )
- bash scripts/run\_bvt.sh → pre\_existing (with --maxfail=1000 over the 82 manifest files, the only failures are test\_silent\_except\_baseline (core/carriers/ssh\*.py, core/swarm\_out.py: not this change) and test\_precommit\_uv\_lock (TypeError in i)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- 5 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · .venv/bin/python -m pytest tests/test\_cdf\_v2\_e2e.py -q --timeout=120: only test\_s3\_real\_pipeline\_lands\_reflects\_and\_writes\_five\_parts and test\_s3\_project\_without\_ddd\_goes\_to\_the\_general\_brain fail, identically on the base tree (the test repo has no git user identity on this machine)
- swarmai · bash scripts/run\_bvt.sh: with --maxfail=1000 over the 82 manifest files, the only failures are test\_silent\_except\_baseline (core/carriers/ssh\*.py, core/swarm\_out.py: not this change) and test\_precommit\_uv\_lock (TypeError in its git fixture); both fail identically on the base tree. test\_cdf\_materializer\_static, red after the

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; run through task\_checks on the task branch after landing
- swarmai · npm run lint:contract: no desktop file changed; run through task\_checks after landing
- swarmai · npm run build: no desktop file changed; run through task\_checks after landing
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run through task\_checks after landing
- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_2cc61cee: environment: the installed SwarmAI reads features.yaml schema 1 (the repo's is 2); with the worktree's own feature\_map all 16 reached walks are not\_run: the pinned Chromium lacks system libraries and this machine has no swarm-replay user

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/scripts/cdf\_backfill\_user\_words\_timeline.py
- swarmai: backend/tests/test\_cdf\_backfill\_user\_words\_timeline.py
- swarmai: backend/tests/test\_cdf\_third\_party\_timeline.py
- swarmai: backend/tests/test\_cdf\_user\_words\_every\_turn.py
