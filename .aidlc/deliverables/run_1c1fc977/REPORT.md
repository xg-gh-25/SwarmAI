# 让 SwarmAI pipeline 交付的证据包带上两份结构化内容（借鉴 AgentSpaces Project Agent 的 Evidence and … · Audit through DELIVER

Delivered: 让 SwarmAI pipeline 交付的证据包带上两份结构化内容（借鉴 AgentSpaces Project Agent 的 Evidence and review，https://docs.hub.amazon.dev/docs/agentspaces/user-guide/howto-project-agent.html）：(1) findings——调查中学到了什么，包括考虑过但放弃的. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 40m 16s wall · 40m 16s active · 26.4% budget |
| Quality | WARN | 0/1 verification · 1 red at base · 6/8 findings closed (4 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 673k comparable · 522k input · 151k output · 44.5M cache reads, not counted · 45.1M billed · host-session responses in the run window · 16.5% budget |

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 3×

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 12/26 tool calls
- correctness · review r1: read 100% of its prompt · 20/26 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls
- security · review r2: read 100% of its prompt · 12/25 tool calls
- security · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 20/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 12/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ f55bb1351468: 5 pass, 0 fail, 0 not run
- test · swarmai @ 5a8e6ca4a7f7: 0 pass, 0 fail, 0 not run

TEST · swarmai:
- perl -e 'alarm 118; exec @ARGV' .venv/bin/python -m pytest tests/test\_run\_evidence\_findings\_decisions.py tests/test\_run\_protocol\_schemas.py tests/test\_run\_protocol\_documents.py tests/test\_design\_spec\_ → pre\_existing (287 of 289 pass; the two failures are test\_pipeline\_attention.py::test\_products\_before\_a\_pause\_reach\_the\_turns\_result\[answer\] and \[permission\], red the same way on the run's base tree (pipeline-v1 bas)

## Red at the run's base too (the agent's report, not checked)

- swarmai · perl -e 'alarm 118; exec @ARGV' .venv/bin/python -m pytest tests/test\_run\_evidence\_findings\_decisions.py tests/test\_run\_protocol\_schemas.py tests/test\_run\_protocol\_documents.py tests/test\_design\_spec\_: 287 of 289 pass; the two failures are test\_pipeline\_attention.py::test\_products\_before\_a\_pause\_reach\_the\_turns\_result\[answer\] and \[permission\], red the same way on the run's base tree (pipeline-v1 base-tree); they cover turn deliverables before a pause, which this change does not touch.

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_run\_evidence\_findings\_decisions.py
