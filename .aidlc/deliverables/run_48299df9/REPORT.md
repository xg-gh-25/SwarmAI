# # Hive cost and hardening: stop idle Hives, keep CloudFront on, verify what is … · Audit through DELIVER

Delivered: # Hive cost and hardening: stop idle Hives, keep CloudFront on, verify what is installed, time each dispatch step \*\*Evidence (2026-11-15).\*\* A Hive ran all day from Nov 10 to 14 while sessions started. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 1h 29m wall · 1h 29m active · no baseline yet (2/5 runs) |
| Quality | WARN | 1/1 verification · 1 not run · 21/24 findings closed (11 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 1.4M comparable · 1.1M input · 313k output · 68.9M cache reads, not counted · 70.3M billed · host-session responses in the run window · no baseline yet (2/5 runs) |

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 5 (+4 found on demand) · searched the DDD 4×

## Decisions

- D1 · What does an unreadable condition (dispatch log, session probe, missing snapshot) mean for idle stop? → busy: the Hive is kept and its idle time starts again. Why: stopping wrongly ends work (bounded but real); keeping wrongly costs one more period (DESIGN\_SPEC, agent)
- D2 · Where does the idle time start? → the latest of: last busy seen, the row's updated\_at (a start or any status change), the Host's own start. Why: an in-memory timer alone would see a Hive stopped and started between two checks, or a Host just restarted, as idle for hours (DESIGN\_SPEC, agent)
- D3 · How is a dispatch that has not yet written its row protected? → a per-Hive in-flight counter set before the Hive's status is read and cleared when dispatch\_spec / dispatch returns; idle stop checks it before and after claim\_stop and gives the claim back (stopping -&gt; running) when a dispatch arrived in between; the restart route uses the same marker. Why: no asyncio lock shared across event loops; one marker for both stoppers (IMPROVEMENT c316d02e) (DESIGN\_SPEC, agent)
- D4 · Setting shape → app config hive\_idle\_stop\_minutes: missing -&gt; 120, positive number -&gt; minutes, 0 or false -&gt; off, anything else -&gt; 120 with a warning. Why: same config store as other host settings; a bad value never silently turns cost control off (DESIGN\_SPEC, agent)
- D5 · What does start do with CloudFront? → nothing when the distribution is enabled; enable it once when an older version had disabled it (no wait). Why: Hives stopped by the old code would otherwise stay unreachable (DESIGN\_SPEC, agent)
- D6 · How is Caddy pinned? → CADDY\_VERSION and the SHA-512 of caddy\_&lt;v&gt;\_linux\_arm64.tar.gz copied from that release's checksums file are constants; the box checks the download against the constant. Why: a pinned value cannot be swapped together with the asset; v2.11.9 validated the rendered Caddyfile (DESIGN\_SPEC, agent)
- D7 · Unnamed dispatch with every Hive stopped → start the stopped Hive with the fewest open runs, then by name; hive\_reason started. Why: with idle stop on, a person's only Hive is usually stopped (DESIGN\_SPEC, agent)
- D8 · SWARM\_RESUME\_VIA\_QUERY: set or retire? → set it in hive/swarmai-hive.service; the update script already installs a changed unit. Why: the flag still gates prompt\_builder, retry\_manager and session\_router (default off), so not every path behaves as if it were set (DESIGN\_SPEC, agent)
- D9 · Key the in-flight marker by what? → the ref the dispatch was given (id or name); dispatch\_in\_flight checks both. Why: it must be set before the Hive's status is read, when only the ref is known (BUILD report · swarmai, agent)
- D10 · Should the restart route use the marker too? → yes: refused before the claim, claim given back if a dispatch began during it. Why: same race (IMPROVEMENT c316d02e), same mechanism, two lines (BUILD report · swarmai, agent)
- D11 · Fix hive/setup.sh's unpinned Caddy fallback? → no. Why: the script exits at its top as deprecated; it is not a live install path (BUILD report · swarmai, agent)
- D12 · Existing Hives' Caddy → left as installed; only new Hives get the pinned release. Why: the task scopes Caddy to first boot; replacing a running Hive's only entry on update is a separate, riskier change (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Hive-side self-stop: the Hive shuts its own instance down after idle time. Not taken: the Hive cannot see the Host's open runs or the lessons the Host still has to take, the Host's row would still say running, and the Hive role would need ec2:StopInstances on itself
- A3 · A CloudWatch alarm on CPU that stops the instance. Not taken: CPU says nothing about open runs, a waiting question or late lessons; it is a second lifecycle authority the Host's status gate does not know about
- A4 · Asynchronous dispatch to a stopped Hive (queue it, start, send later). Not taken: the person chose a synchronous dispatch bounded by one deadline under the CLI's wait (PROJECT decision 7e66a6c5); a queue is new job state lost on restart

Requirements analysis:

- missing\_edge (R1.1, R2.1): a dispatch in flight to a Hive has no row or open run until its session starts, so an idle check in that window would stop the Hive under it; an in-flight counter and one lock around check-and-claim close it → rewritten
- missing\_edge (R1.5): an in-memory idle timer would see a Hive stopped and started between two checks as idle for hours; the idle start is the latest of last busy, the row's updated\_at and the Host's own start → rewritten
- missing\_edge (R3.3): Hives stopped by the old code have a disabled distribution; never enabling it again would leave them unreachable → rewritten
- missing\_edge (R5.3): a package taken from GitHub Releases reached S3 without checksums.txt, so the new box check would refuse every such Hive → rewritten
- missing\_edge (R2.3): cancelling provisioner.start part way would leave the Hive in starting forever; the start runs in its own task and the dispatch only stops waiting → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 8/26 tool calls
- correctness · review r1: read 100% of its prompt · 19/27 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 16/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r1: read 100% of its prompt · 10/25 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 4026edd8daa7: 9 pass, 0 fail, 0 not run
- test · swarmai @ c4c5b9000f0d: 11 pass, 0 fail, 1 not run
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
  - backend-bvt: not_run
  - frontend-run-frontend-tests: pass

TEST · swarmai:
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_48299df96f08/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_hive\_idle\_stop.py tests/test\_swarm\_out\_start\_then\_dispatch.py tests/test\_hive\_harden → pass (676 passed on this tree: the design's test set and every existing provisioner / Swarm Out suite the change touches)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass (no skill files changed)
- python scripts/lint\_experts.py → pass (no expert files changed)
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → not\_run (run once through scripts.task\_checks on the landed tree (its exit code is what the card shows); passed on the tree before the last fixing pass, which changed only backend/hive/provisioner.py's deploy )
- npx tsc --noEmit → pass (ran on this tree's desktop files: only backend files changed in TEST and REVIEW since)
- npm run lint:contract → pass (ran on this tree's desktop files: only backend files changed in TEST and REVIEW since)
- 2 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: run once through scripts.task\_checks on the landed tree (its exit code is what the card shows); passed on the tree before the last fixing pass, which changed only backend/hive/provisioner.py's deploy message and docstrings

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/hive\_idle.py
- swarmai: backend/tests/test\_hive\_hardening.py
- swarmai: backend/tests/test\_hive\_idle\_stop.py
- swarmai: backend/tests/test\_swarm\_out\_start\_then\_dispatch.py
- swarmai: desktop/src/pages/chat/components/\_\_tests\_\_/DispatchCard.hiveReason.test.ts
