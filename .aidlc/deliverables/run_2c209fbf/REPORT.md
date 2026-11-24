# ## Goal: when background cognition outside CDF fails for missing credentials, t… · Audit through DELIVER

Delivered: ## Goal: when background cognition outside CDF fails for missing credentials, the person sees the same clear message core/attention\_authority.py (\~:967-1058) turns CDF's credential failures (recorded. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 31m 11s wall · 31m 11s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 red at base · 1/4 findings closed (1 test-closed, confirmed by review) · 1 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 490k comparable · 385k input · 105k output · 18.2M cache reads, not counted · 18.6M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 4 (+3 found on demand) · searched the DDD 2×

## Decisions

- D1 · Which success clears the shared record? → Any jobs.bedrock call that got a reply from Bedrock (a StructuredOutputError reply included), plus read-side: a shared failure older than CDF's latest successful extraction attempt is ignored. Why: Every background caller signs with the same credentials (jobs.bedrock get\_client/build\_timeout\_client); a rarely-running source must not keep the item up after the person fixed the sign-in. Mirrors CDF's 'the latest word decides' (DESIGN\_SPEC, agent)
- D2 · How is the item worded for profile/SSO and API-key users? → One neutral title ('Background cognition's Bedrock credentials are not working'); detail, action and Settings tab follow aws\_account.get\_chat\_via(): bedrock\_key -&gt; LLM Models (ai-models) and the API key; aws -&gt; Swarm AWS Account (aws-account) and the AWS sign-in (account, profile or SSO). Why: The key is entered on LLM Models (CredentialBanner already sends key users there); NeedsYouRow opens context.settings\_tab for kind aws\_account, so no frontend change (DESIGN\_SPEC, agent)
- D3 · Cross-process writes → Process-local lock plus atomic os.replace of a temp file; an unparseable file is moved aside (.corrupt-&lt;ts&gt;) and the record starts empty. Why: A lost update between the daemon and a script at worst drops one entry until that source fails again; fcntl is not portable to the Windows backend (DESIGN\_SPEC, agent)
- D4 · Item id and source → Keep id ingestion:needs\_account and source ingestion. Why: One item whatever failed; the frontend source enum and existing consumers stay unchanged (DESIGN\_SPEC, agent)
- D5 · How are the failed callers named in the item? → A small label map (session summaries, session scoring, jobs), CDF features folded into 'Cognitive Distillation', other features shown as is. Why: The detail should say what stopped in plain words, not usage feature ids (BUILD report · swarmai, agent)
- D6 · How should the CDF materializer scan treat the record's write, reachable from every jobs.bedrock call? → Add core/background\_credentials.py to scripts/cdf\_write\_scan.py EXEMPT with the reason. Why: Allowlisting the seven callers would be false (they write no governed file) and every future bedrock caller in a marker module would trip it again; the module writes one fixed data-dir file (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Record in each swallowing caller's except block (summarization, session\_scorer, inbox, jobs, CDF health/user\_check). Not taken: The same code in eight callers, any future caller is missed, and clearing on success would need the same edit at each success point.
- A3 · Write the other callers' failures into CDF's extraction\_attempts / poison events. Not taken: Fakes CDF records (extraction\_attempts only accepts sources S0-S7) and skews CDF's own status and health views.

Requirements analysis:

- missing\_edge (R1.1): Where to record: per caller (each except block) or once in jobs.bedrock.\_call, the one place every structured()/text() call passes. Recording in \_call covers every caller that swallows (summarization, session\_scorer, inbox triage/trends/drafts, memory\_health, skill\_proposer, CDF health judge, user\_check, eval judges) and future ones, with no per-caller duplication; its source is the call's feature (else the enclosing usage\_accounting attribute). → rewritten
- missing\_edge (R2.3): Clear semantics: CDF clears on its latest attempt succeeding. The shared record clears on any jobs.bedrock call that got a reply (a StructuredOutputError reply counts: it authenticated), so a rarely-running source (weekly job) does not keep the item up after the person fixed credentials. The success path writes only when the record holds failures (a stat otherwise). → rewritten
- missing\_edge (R1.4): Cross-process writers (daemon, eval subprocess): an atomic replace under a process-local lock; a lost update between processes at worst drops one entry until the next failure. → rewritten
- vague (R3.1, R3.2): Settings: the AWS sign-in lives on the Swarm AWS Account tab (aws-account); a Bedrock API key is entered on LLM Models (ai-models), and CredentialBanner already sends key users there. The NeedsYouRow opens context.settings\_tab for kind aws\_account, so a per-mode tab needs no frontend change. → confirmed
- missing\_edge (R1.1): Two Bedrock calls bypass jobs.bedrock.\_call: routers/system.py \_verify\_bedrock (invoke\_model, the Settings verify the person clicks) and core/aws\_account.py converse (the account probe). Both are foreground or probe calls whose failure the person sees where they acted, so they are left out of the background record. → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 15/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 5/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 625110553e38: 4 pass, 0 fail, 0 not run
- test · swarmai @ 659ede4f1784: 4 pass, 0 fail, 0 not run
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
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' /home/swarm/.swarm-ai/worktrees/run\_2c209fbf/swarmai/backend/.venv/bin/python -m pytest tests/test\_cdf\_materializer\_static.py tests/test\_silent\_except\_baseline.py tests → pass (119 passed: the design's final set plus the two static scans BVT showed this change touches)
- python scripts/smoke\_import.py → pass (695 modules imported)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- bash scripts/run\_bvt.sh → pre\_existing (1344 of 1347 pass (run with --maxfail=1000 to see past the 3-failure stop); the 3 failures are tests/test\_precommit\_uv\_lock.py (TypeError: uv not found on this host), which fail the same way on the ru)

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 1344 of 1347 pass (run with --maxfail=1000 to see past the 3-failure stop); the 3 failures are tests/test\_precommit\_uv\_lock.py (TypeError: uv not found on this host), which fail the same way on the run's base tree

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/background\_credentials.py
- swarmai: backend/tests/test\_background\_credentials.py
