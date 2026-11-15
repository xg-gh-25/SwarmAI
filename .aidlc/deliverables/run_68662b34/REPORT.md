# # Fix: Caddy's basic auth exempts /x/../health, not only the literal /health · Audit through DELIVER

Delivered: # Fix: Caddy's basic auth exempts /x/../health, not only the literal /health \*\*Evidence (2026-11-15).\*\* On a Hive, Caddy exempts \`/health\` from basic auth with \`@protected not path /health\` (\`hive/Cad. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | BLOCK | 49m 37s wall · 49m 37s active · no baseline yet (3/5 runs) |
| Quality | WARN | 1/1 verification · 3 not run · 14/17 findings closed (5 test-closed, confirmed by review) · 1 HIGH finding open |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | BLOCK | 977k comparable · 759k input · 219k output · 26.2M cache reads, not counted · 27.2M billed · host-session responses in the run window · no baseline yet (3/5 runs) |

Knowledge: requirement recalled · 5 · design recalled · 7 · cited 4 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · What does a failure of the Caddy fix do to the update? → The previous Caddyfile is restored, Caddy restarted with it, and the update stops before the install is touched (exit 1, the reason on stdout). Why: A Hive that cannot take a security fix should not report a clean update; nothing of the install has changed, and the reason shows on the card (DESIGN\_SPEC, agent)
- D2 · What about a Caddyfile with neither the old nor the new exemption line (hand-edited, or the oldest \`basicauth \*\` layout that has no exemption)? → Leave it as it is with a WARN line and continue the update. Why: The editor cannot know such a file is unsafe; the oldest layout protects /health too. Same stance as the origin guard editor (refuses unknown layouts) (DESIGN\_SPEC, agent)
- D3 · Does handle /health change? → No. Why: caddy adapt shows respond (the guard) and basic\_auth compile before every handle, so handle only routes a request already past both (DESIGN\_SPEC, agent)
- D4 · Where does the explanatory comment go so the update step can reproduce the fresh file byte for byte? → Above the guard's RULE block, with the new @protected line as the guard's anchor; the step works on the file with the guard blocks taken out, then re-inserts them from the template. Why: The result equals a fresh render by construction for guarded and unguarded Hives (checked against the base commit's real render) (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Fix only the basic-auth line and leave the origin guard on {http.request.uri.path}. Not taken: Measured on Caddy v2.11.4: {http.request.uri.path} is the decoded path, so /%68ealth passes the origin guard without the secret; two exemptions with two meanings is the drift that caused this bug.
- A3 · Ship the fix to existing Hives through update\_caddyfile (the dedicated SSM command). Not taken: Nothing calls update\_caddyfile: it only inserts route handles before /api/\*, and no update or UI path runs it, so existing Hives would never receive the fix. The per-update SSM script reaches every Hive on its next update.
- A4 · Exempt exactly {http.request.uri} == "/health" (no query allowed). Not taken: A monitor that adds a query string would start getting 401; the query is not part of the path and the backend sees /health either way.

Requirements analysis:

- missing\_edge (R2.1): Measured on Caddy v2.11.4: {http.request.uri.path} is the decoded path, so the origin guard's 'literal' exemption also lets /%68ealth through without the secret; only {http.request.uri} (RequestURI) keeps escapes and dot segments. → rewritten
- missing\_edge (R1.2): {http.request.uri} includes the query string; an exact compare would start asking monitors that add a query for the credential, so /health?&lt;query&gt; stays exempt (the path is still exactly /health). → rewritten
- missing\_edge (R3.1, R3.3): Nothing calls update\_caddyfile automatically; the per-update SSM script (provisioner.update) is what reaches every existing Hive, so the migration runs there, before rsync, and fails the update closed rather than leaving a Hive silently exposed. A Caddyfile with neither the old nor the new lines (hand-edited) is reported with a WARN and left alone. → rewritten
- missing\_edge (R2.1, E2, R1.2): Directive order checked with \`caddy adapt --config hive/Caddyfile\` (v2.11.4): route 1 is the origin guard's static\_response 403 (the global \`order respond before basic\_auth\`), route 2 is basic\_auth, and every handle (including handle /health) comes after both. So no request skips the guard or basic auth through handle /health; handle only routes a request already past both to the backend. A request without the secret gets 403 before basic auth runs, which matches the live probe (no secret, no credential: 403 on /x/../health, //health, /%68ealth). handle /health is left as it is: routing an authenticated /HEALTH to the backend exposes nothing. → rewritten
- missing\_edge (R1.1): The backend middleware (backend/middleware/hive\_auth.py) exempts the decoded scope path with a trailing slash stripped; that is the second layer, unchanged by this Caddy fix and out of its scope. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 14/25 tool calls
- correctness · review r1: read 100% of its prompt · 15/26 tool calls
- security · review r2: read 100% of its prompt · 10/25 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 6cc8d1c6a8a7: 6 pass, 0 fail, 0 not run
- test · swarmai @ 17a5d0e8a627: 4 pass, 0 fail, 3 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: not_run
  - backend-security-scan: not_run
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: pass
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_hive\_caddy\_health\_exemption.py tests/test\_hive\_origin\_secret.py tests/test\_hive\_provisioner.py tests/test\_hive\_tmp\_on\_disk.py test → pass (191 passed, 0 skipped on this tree (live Caddy v2.11.4 tests ran); covers every DESIGN\_SPEC exit test)
- "$SWARM\_BACKEND\_BIN" py -m scripts.mutation\_check mut5.json mut6.json -- pytest backend/tests/test\_hive\_caddy\_health\_exemption.py -k 'layout or unexpected' → pass (4 of 4 mutations of the TEST-stage fixes caught (allowlist branches, fail-closed catch-all))
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → not\_run (passed on this run's earlier trees (latest 85 s run before the last REVIEW fixes); the final tree differs only in user\_data.py comments, an echo string and a case arm; runs again through task\_checks o)
- python scripts/security\_scan.py → not\_run (passed on this run's earlier trees (latest 85 s run before the last REVIEW fixes); the final tree differs only in user\_data.py comments, an echo string and a case arm; runs again through task\_checks o)
- bash scripts/run\_bvt.sh → not\_run (run through the task's recorded checks (task\_checks) on the landed tree; its Hive files (test\_hive\_update.py) passed in the targeted run above)
- bash scripts/sync-version.sh check → pass (run on this tree)

## Not run (the agent's reason, not checked)

- swarmai · python scripts/lint\_problem\_values.py: passed on this run's earlier trees (latest 85 s run before the last REVIEW fixes); the final tree differs only in user\_data.py comments, an echo string and a case arm; runs again through task\_checks on the landed tree
- swarmai · python scripts/security\_scan.py: passed on this run's earlier trees (latest 85 s run before the last REVIEW fixes); the final tree differs only in user\_data.py comments, an echo string and a case arm; runs again through task\_checks on the landed tree
- swarmai · bash scripts/run\_bvt.sh: run through the task's recorded checks (task\_checks) on the landed tree; its Hive files (test\_hive\_update.py) passed in the targeted run above

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_hive\_caddy\_health\_exemption.py
