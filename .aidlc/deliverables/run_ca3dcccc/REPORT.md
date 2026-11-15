# 改规则：agent 可重启空闲 Hive. Let the owner's agent restart (stop then start) one of it… · Audit through DELIVER

Delivered: 改规则：agent 可重启空闲 Hive. Let the owner's agent restart (stop then start) one of its Hives when nothing of this Host runs on that Hive, so the person no longer has to click Stop/Start in the Hive panel fo. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 29m 10s wall · 29m 10s active · 19.1% budget |
| Quality | WARN | 1/1 verification · 1 not run · 10/12 findings closed (8 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 500k comparable · 383k input · 117k output · 32.1M cache reads, not counted · 32.6M billed · host-session responses in the run window · 12.3% budget |

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 4 (+2 found on demand) · searched the DDD 2×

## Decisions

- Q1 · Should a restart record a decision? → No new record: decisions are recorded per run (core/swarm\_out\_record) and a restart belongs to no run; update-hive records none either. The daemon logs the stop and start, and the CLI prints what it did (REQUIREMENTS, agent)
- D1 · Which Hives may restart-hive restart? → Only a Hive whose fleet status is running. Why: Starting a stopped Hive starts a machine the person may have stopped on purpose (a cost); start-hive already covers starting when nowhere else can take runs (DESIGN\_SPEC, agent)
- D2 · What counts as idle? → open\_runs is an int equal to 0 (unknown refuses, like update-hive), and streaming is not an int above 0 (a session is mid-turn on the Hive, perhaps the person's own on its web page). Why: open\_runs is this Host's runs; streaming also catches a live session started elsewhere. An unreachable Hive reports no streaming count, and a restart is exactly what such a Hive may need, so unknown streaming does not refuse (DESIGN\_SPEC, agent)
- D3 · Is a restart recorded as a decision? → No new record: the CLI prints what it did and the daemon logs the stop and the start. Why: Decisions are recorded per run (core/swarm\_out\_record) and a restart belongs to no run; update-hive records none either (DESIGN\_SPEC, agent)
- D4 · HTTP timeouts for the two calls → 600 s each, as hang guards. Why: The stop route waits for EC2 to stop (waiter up to 600 s) and the start route waits for EC2 running plus a 180 s health check; the CLI default of 120 s would report a timeout on a normal restart (DESIGN\_SPEC, agent)
- D5 · What does test\_cli\_calls\_only\_owner\_agent\_ops expect of restart-hive? → Two calls (stop, start), each an owner-agent op; the fleet mock reports running for that command only. Why: Every other command makes one call; restart-hive is the only one that needs the Hive running rather than stopped (BUILD report · swarmai, agent)
- D6 · R2's new MEDIUM: a dispatch still syncing its Brain is not yet in the open-runs log, so a restart can claim that Hive → Left as a residual risk, not fixed in this run. Why: Closing it needs a new per-Hive dispatch-in-flight marker shared by dispatch and restart (a new mechanism); it needs two sessions acting on the same Hive within one Brain sync, and the run that is cut off settles as hive\_stopped rather than being lost silently (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A new backend route POST /api/hive/instances/{id}/restart that checks idleness server-side and runs stop then start. Not taken: A new route, ops row and provisioner path for what two existing routes already do; the person's Stop button must still stop a busy Hive, so the idle check cannot move into the stop route itself, and every other agent-limited check (start-hive, update-hive) lives in the CLI.
- A3 · Reboot the operating system through SSM instead of EC2 stop and start. Not taken: A new SSM command path with its own failure handling, health wait and status bookkeeping the provisioner does not have; stop and start already exist, are tested and are what the person's buttons do.

Requirements analysis:

- missing\_edge (R1.5): stop succeeds and start fails, leaving the Hive stopped or in error; start-hive then refuses while another Hive runs, so the agent cannot retry on its own → rewritten
- missing\_edge (R1.1): the stop and start routes wait for EC2 and the health check and can exceed the CLI's default 120s HTTP timeout → rewritten
- missing\_edge (R1.3): restarting a stopped Hive would start a machine the person stopped on purpose, a cost the person decides → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 10/25 tool calls
- correctness · review r1: read 100% of its prompt · 15/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 16/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 9ed668d3e518: 5 pass, 0 fail, 0 not run
- test · swarmai @ 6306ea6e2842: 7 pass, 0 fail, 1 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- perl -e 'alarm 118; exec @ARGV' .venv/bin/python -m pytest tests/test\_swarm\_out\_ops.py tests/test\_zero\_touch.py tests/test\_swarm\_out\_card.py tests/test\_hive\_provisioner.py tests/test\_hive\_update.py te → pass (352 passed)
- "$SWARM\_BACKEND\_BIN" py -m scripts.mutation\_check /tmp/pl-ca3/m3.json -- .venv/bin/python -m pytest tests/test\_zero\_touch.py -k stop\_that\_fails → pass (1 of 1 caught: dropping the error status leaves the Hive in stopping)
- "$SWARM\_BACKEND\_BIN" py -m scripts.mutation\_check /tmp/pl-ca3/m4.json -- .venv/bin/python -m pytest tests/test\_zero\_touch.py -k start\_that\_fails → pass (1 of 1 caught: dropping start's error write leaves the Hive in starting)
- backend/.venv/bin/python scripts/smoke\_import.py → pass
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass
- backend/.venv/bin/python scripts/security\_scan.py → pass
- backend/.venv/bin/python scripts/lint\_skills.py → pass
- bash scripts/run\_bvt.sh → not\_run (the 81-file BVT is CI's broad coverage (it runs on push); the tests of every changed function and its direct callers ran above)

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: the 81-file BVT is CI's broad coverage (it runs on push); the tests of every changed function and its direct callers ran above
