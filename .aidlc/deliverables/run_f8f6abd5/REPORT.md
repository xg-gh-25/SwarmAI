# 循环检测看结果是否在变。compaction\_guard 的循环检测（backend/core/compaction\_guard.py，由 core/stre… · Audit through DELIVER

Delivered: 循环检测看结果是否在变。compaction\_guard 的循环检测（backend/core/compaction\_guard.py，由 core/streaming\_orchestrator.py 在每个主 agent 工具调用时 record\_tool\_call + check）只按 (工具名, 输入 hash) 判断重复：同一调用连续 7 次直接 KILL。一个长任务用同一个可续跑命令一段. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 35m 46s wall · 35m 46s active · no baseline yet (4/5 runs) |
| Quality | WARN | 1/1 verification · 1/4 findings closed (10 reported; within 10 lines of one file they count once) · 1 fail-to-pass · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 543k comparable · 415k input · 128k output · 52.9M cache reads, not counted · 53.5M billed · $16.47 book cost · host-session responses in the run window · no baseline yet (4/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 1 rules · 2 watches

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 3 · searched the DDD 2×

## Decisions

- D1 · Which differences in a result are noise rather than progress? → durations (2.34s, 120 ms, 3m), clock times and ISO timestamps, UUIDs, hex ids of 8+ characters, pid=/session= values; every other number is kept. Why: the pytest dead-loop prints a new duration each run and must still be caught, while a resumable job's progress is a count that must count as change (DESIGN\_SPEC, agent)
- D2 · How is the newest call compared before its result exists? → by (tool, input hash) against the last completed call's (tool, input hash); a match extends the streak only if the completed calls before it share one result. Why: check() runs at tool-use time; this keeps today's behaviour for the call in flight (DESIGN\_SPEC, agent)
- D3 · Does a long result cost anything on the hot path? → the fingerprint reads at most the first and last 4,000 characters of the result text and hashes them once, when the result arrives. Why: a tool result is already in memory there; bounding the read keeps the per-result cost flat however large the output (DESIGN\_SPEC, agent)
- D4 · Does fingerprinting run on the event loop? → yes, inline when the result arrives. Why: measured: 0.85 ms for a 34 KB result (the read is bounded to 8,000 characters) and 15 us for a small one, once per tool result, never per token; a tool call itself takes tens of ms or more (BUILD report · swarmai, agent)
- D5 · What is the run for a progressing command? → at most 2: the call in flight is matched on input against the last completed call. Why: check() runs before the in-flight result exists (design D2); 2 stays below every SOFT threshold (3, or 5 for read-only tools) (BUILD report · swarmai, agent)
- D6 · Fix R2's LOW findings now or leave them as residual risk? → fix them in TEST and let the run go back to REVIEW for the incremental review. Why: the epoch one brings back the false KILL this run exists to remove, and both sit in this change (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Reset only the consecutive-repeat counter when a repeated call's result differs from the previous one. Not taken: covers one of four layers: the diversity stall, set-overlap and single-tool repeat would still read seven progressing slices as a loop
- A3 · Treat any changed result as a progress signal that lowers the escalation level. Not taken: too weak a signal: a real loop that prints a new duration each time would keep resetting itself, and it adds a second escalation path instead of fixing what a call is

Requirements analysis:

- missing\_edge (R2.2): a real dead loop can print changing timings, so 'result differs' alone would let the pytest dead-loop pattern escape → rewritten
- missing\_edge (R2.3): check() runs at tool-use time, before the result exists → rewritten
- missing\_edge (R1.1, R2.1): thresholds and the PASSIVE/ACTIVE caps must not change → rewritten
- missing\_edge (R1.3): \_rolling\_baseline\_set (compaction\_guard.py:386) holds the same pair and becomes the set-overlap baseline at activate(); the heuristic compaction detection only compares context percentages → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- correctness · review r2: read 100% of its prompt · 16/25 tool calls
- correctness · review r1: read 100% of its prompt · 10/25 tool calls
- correctness · review r1: read 100% of its prompt · 12/25 tool calls
- correctness · review r2: read 100% of its prompt · 13/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 5d4eb234a9f9: 4 pass, 0 fail, 0 not run
- test · swarmai @ 22a1c0cdf7c3: 6 pass, 0 fail, 0 not run
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
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_compaction\_guard\_results.py -q --timeout=60 -p no:cacheprovider → pass
- .venv/bin/python -m pytest tests/test\_compaction\_guard\_results.py tests/test\_compaction\_guard.py tests/test\_compaction\_guard\_bugfix.py tests/test\_compaction\_tuning.py -q --timeout=60 -p no:cacheprovid → pass
- .venv/bin/python -m pytest tests/test\_compaction\_live\_events.py tests/test\_tool\_call\_leak\_guard.py tests/test\_api\_error\_continuation.py tests/test\_orchestrator\_tool\_free\_extend.py tests/test\_session\_u → pass
- backend/.venv/bin/python scripts/smoke\_import.py → pass
- backend/.venv/bin/python scripts/security\_scan.py → pass
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass

Tests were not run again after main moved: tests carried over a main move: swarmai (main 1d532a806e60-&gt;406ac14720b1): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_compaction\_guard\_results.py
