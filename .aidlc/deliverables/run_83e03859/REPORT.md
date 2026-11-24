# ## Goal: a Swarm Out run on Elastic Workers whose AgentCore session expired can… · Audit through DELIVER

Delivered: ## Goal: a Swarm Out run on Elastic Workers whose AgentCore session expired can always be settled, and diagnose can read why it ended A real case (dispatch so\_7c86353825b8): an Elastic Workers (AgentC. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 50m 48s wall · 50m 48s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 not run · 2/11 findings closed (17 reported; within 10 lines of one file they count once) (2 test-closed, confirmed by review) · 2 fail-to-pass · CF-3a6dafb8: no mutation check (the fix is in the test itself: there is no product code to take out) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 935k comparable · 722k input · 213k output · 45.7M cache reads, not counted · 46.6M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 1 (+3 found on demand) · searched the DDD 2×

## Decisions

- D1 · How long after expiry may a result still arrive? → Until writes\_end\_at: the platform's maxLifetime (MAX\_RUN\_S) from the session start, which is at most START\_TIMEOUT\_S after opened\_at, plus END\_MARGIN\_S for clock skew. Why: bounded by the carrier's own constants; after it the microVM no longer exists, so nothing can write to bridge/&lt;sid&gt;/ (DESIGN\_SPEC, agent)
- D2 · What counts as 'the result store was checked'? → one S3 pull that started at or after writes\_end\_at, read the manifest to its end (drained) with no error. Why: a failed or partial pull is no proof of absence (fcc140a5: guards read the latest complete observation) (DESIGN\_SPEC, agent)
- D3 · Which stage/why does the card show? → stage stopped, why no\_result (existing i18n key 'it stopped without a result'), plus a backend note in the /runs row and the agent's run summary. Why: desktop locale files are out of scope for this task; the existing key is true and the note carries the specifics (DESIGN\_SPEC, agent)
- D4 · Where does diagnose read AgentCore logs? → CloudWatch Logs group(s) /aws/bedrock-agentcore/runtimes/&lt;runtime\_id&gt;-\*, filter on the round's runtime session id (and the chat session id when named) in the round's window, plus streams whose name carries the session id; through AgentCoreCarrier.\_session (hive.accounts.session\_for). Why: the provisioner's execution role writes there; the account layer is the only allowed AWS path (DESIGN\_SPEC, agent)
- D5 · Does the provisioner add a Host IAM permission? → no. Why: the provisioner manages only the runtime's execution role, which already writes its logs; the Host identity's template (core/aws\_permissions) is not provisioner-managed; an AccessDenied answers reachable false naming the logs actions (DESIGN\_SPEC, agent)
- D1 · Is the round\_end mark cleared when a later check fails? → no: kept once per round. Why: a read after writes\_end\_at that took every entry is final: nothing can be written after it; a result found later still wins because every reader checks \_final first (BUILD report · swarmai, agent)
- D2 · How is a lost round rechecked? → at CLOSED\_RECHECK\_S, like a closed record, only for the current round's mark. Why: keeps a late result takeable without an S3 pull on every poll forever; a stale earlier-round mark never slows the current round (BUILD report · swarmai, agent)
- D3 · Which CloudWatch lines are the session's? → events naming the runtime session id or the chat session id (quoted ?terms, ids checked against a strict pattern), plus streams whose name carries the runtime session id. Why: the supervisor writes the backend log to a file, so session-naming lines and a session-named stream are what can be attributed without guessing; lines of other sessions are never shown (BUILD report · swarmai, agent)
- D1 · Which Brain checks does this change touch? → backend-import-smoke, backend-problem-values, backend-security-scan, backend-bvt. Why: only backend Python files changed; desktop, skills, experts and version files are untouched (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A wall-clock timer: treat a carrier\_expired run with no result as lost N hours after expiry. Not taken: so\_5c67a1c90d65's result was taken hours later because nothing read the store sooner; a timer that is not tied to a successful read can declare lost a result sitting in S3
- A3 · Widen the existing stopped\_without\_result path (result\_status failed/not\_applicable) to carrier\_expired. Not taken: a failed S3 pull looks like an empty store (hive\_records.\_UNSUPPORTED) and result\_status says nothing about whether the store was read after the round stopped writing

Requirements analysis:

- missing\_edge (R1.1): an S3 pull that fails without anything kept returns the same shape as an empty store (hive\_records.\_UNSUPPORTED), so 'supported' alone is no proof the store was read; handled: the S3 pull reports drained=true only when it read the manifest to its end without error; only that counts as checked → rewritten
- missing\_edge (R1.1, R1.4): the Host counts a round as expired END\_MARGIN\_S before the platform's lifetime, the session starts up to START\_TIMEOUT\_S after opened\_at, and host/AWS clocks may differ; a result written in that span is late but real (so\_5c67a1c90d65 was taken hours later because nothing read the store sooner); handled: the grace is the code's own bounds: writes\_end\_at = opened\_at + MAX\_RUN\_S + START\_TIMEOUT\_S + END\_MARGIN\_S, and the check must start after it → rewritten
- missing\_edge (R3.3): the Host identity's IAM template (core/aws\_permissions) has no AgentCore actions and the provisioner manages only the runtime execution role, which already may write its logs; handled: no IAM change by the provisioner; an AccessDenied from CloudWatch answers reachable false naming the logs actions needed → rewritten
- conflict (R1.3, U1): Gate-0 proposed letting an agent discard every derive stopped/no\_result run, the EC2 session\_gone / not\_applicable path too. Rejected for this run: an EC2 run's workspace (Outputs/&lt;id&gt;) still exists on a live Hive and its files may still be pulled by the records path, so an agent discard there could lose what can still arrive, which the task's own rule forbids; an AgentCore round has no machine left once its lifetime passed. The exemption is keyed on the round\_end mark only. → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 9/26 tool calls
- correctness · review r1: read 100% of its prompt · 16/26 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 22bc2cc87620: 4 pass, 0 fail, 0 not run
- test · swarmai @ 5966421b4449: 6 pass, 0 fail, 1 not run
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
  - backend-bvt: pass
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_83e038592027/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_expired\_round.py tests/test\_hive\_diagnosti → pass
- perl -e 'alarm 280; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_83e038592027/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_expired\_round.py tests/test\_hive\_diagnosti → pass
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- bash scripts/run\_bvt.sh → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_83e038592027/repos/swarmai/backend/.venv/bin/python -m scripts.feature\_map replay --affected --run run\_83e03859 → not\_run (environment: the reached walk (api-health-q56c, the backend answers health checks) needs a replay user (swarm-replay) this machine does not have; the installed SwarmAI cannot read the repo's features.)

## Not run (the agent's reason, not checked)

- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_83e038592027/repos/swarmai/backend/.venv/bin/python -m scripts.feature\_map replay --affected --run run\_83e03859: environment: the reached walk (api-health-q56c, the backend answers health checks) needs a replay user (swarm-replay) this machine does not have; the installed SwarmAI cannot read the repo's features.yaml schema 2

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_hive\_diagnostics\_agentcore.py
- swarmai: backend/tests/test\_swarm\_out\_expired\_round.py
