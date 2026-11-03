# 按 Knowledge/Reports/2026-11-03-swarm-run-consistency-audit.md 修 1–4 条（Swarm Run… · Audit through DELIVER

Delivered: 按 Knowledge/Reports/2026-11-03-swarm-run-consistency-audit.md 修 1–4 条（Swarm Run 的代码 E2E：拆给 Hive 的子单做，lead run 判、合并、落地）：1 非 owner 的 channel 会话里 pipeline-v1 child 一律拒绝（和 scripts.swarm\_out 的 RESTRICTED\_E. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 55m 59s wall · 55m 59s active · no baseline yet (1/5 runs) |
| Quality | WARN | 1/1 verification · 0/7 findings closed |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 350k comparable · 277k input · 73k output · 36.9M cache reads, not counted · 37.2M billed · host-session responses in the run window · no baseline yet (1/5 runs) |

Knowledge: design recalled · 7 · cited 0

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 16/26 tool calls
- security · review r1: read 100% of its prompt · 15/26 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 2681bf99eee6: 3 pass, 0 fail, 0 not run
- test · swarmai @ c86fd60b54b3: 4 pass, 0 fail, 0 not run

TEST · swarmai:
- perl -e 'alarm 580; exec @ARGV' python -m pytest -q -p no:cacheprovider tests/test\_swarm\_run\_cli.py tests/test\_swarm\_run\_children.py tests/test\_swarm\_out\_ops.py tests/test\_swarm\_out\_actor.py tests/tes → pass (490 passed)
- python scripts/lint\_skills.py → pass (skills and stage docs pass)
- perl -e 'alarm 400; exec @ARGV' npx vitest run src/pages/chat src/components/swarm-out → pass (109 files, 1295 passed)
- npx tsc --noEmit -p tsconfig.json → pass (no type errors)

## Hive children (Swarm Run)

- W1 · so\_81f3697ecc7d · xg-swarm-remote-demo · handled · review: take W1's commits into Swarm Run run\_79243d46; accept: W1 meets its acceptance: RESTRICTED\_ENV refusal at the entry of every child command before any daemon call, a registry test over every child subcommand (with its own four mutation checks), docstrings 
- W2 · so\_2119df749fb9 · xg-swarm-remote-explorer · handled · review: take W2's commits into Swarm Run run\_79243d46; accept: W2 meets its acceptance: the whole-run stop records the person's stop on each child it stopped and none on one that failed; diff is the route and its test only; its test fails without the record call 
- W3 · so\_4ca118d07f35 · xg-swarm-remote-demo · handled · review: take W3's commits into Swarm Run run\_79243d46; accept: W3 meets its acceptance: text-only diff (strings, comments, docstrings, test expectations), no condition changed; nothing parses the old digest line; diff read
