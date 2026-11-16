# # Fix: existing Hives keep their old Caddy binary, and the release bucket is na… · Audit through DELIVER

Delivered: # Fix: existing Hives keep their old Caddy binary, and the release bucket is named two ways \*\*Evidence (2026-11-16).\*\* 8969ed816 pinned Caddy to the v2.11.9 GitHub release with a SHA-512 check, but on. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 31m 15s wall · 31m 15s active · no baseline yet (4/5 runs) |
| Quality | WARN | 1/1 verification · 6 not run · 1 red at base · 2/7 findings closed (2 test-closed, confirmed by review) · 2 fail-to-pass · CF-e0aef8ef: no mutation check (the fix is in the tests themselves; RP62's harm is to later tests in the same process, which no assertion here observes) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 584k comparable · 445k input · 139k output · 21.9M cache reads, not counted · 22.5M billed · host-session responses in the run window · no baseline yet (4/5 runs) |

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 3 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · When both the 4-digit and the 12-digit bucket exist, which is found? → the 4-digit one. Why: it is the one the account's existing Hives and release uploads already use; a Hive recorded on the 12-digit one keeps it through its persisted s3\_bucket and gets published releases from GitHub as before (DESIGN\_SPEC, agent)
- D2 · Where does the binary step run in the update script? → after the Caddyfile /health step, before the backup of /opt/swarmai. Why: the new binary validates the final Caddyfile, and a failure leaves the Hive on its version with the old binary; the refused-side probe uses a path never exempt in any layout, so the step does not depend on the /health step having run (DESIGN\_SPEC, agent)
- D3 · How is a head-bucket error other than 403/404 treated? → it fails the deploy. Why: treating a network or throttling error as 'absent' would create a second bucket, the drift this change removes (DESIGN\_SPEC, agent)
- D4 · How does the update know the binary differs without downloading? → pin the SHA-512 of the caddy binary inside the verified v2.11.9 archive (measured here from the archive whose SHA-512 is the existing pin) and compare /usr/bin/caddy with it. Why: an already-pinned Hive's update does not depend on GitHub; after a download the unpacked binary is also checked against it (BUILD report · swarmai, agent)
- D5 · What IAM action does head\_bucket need? → s3:ListBucket, mapped in aws\_permissions.\_METHOD\_ACTION. Why: S3 authorizes HeadBucket as ListBucket; it is already in the storage action list, so the permission template does not change (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Caddy: compare \`caddy version\` output instead of bytes. Not taken: a caddyserver.com build of v2.11.9 reports the same version but is not the checked binary; a version string is not the check first boot does
- A3 · Caddy: download and cmp on every update. Not taken: makes every update of an already-pinned Hive depend on GitHub and spend 16 MB; the pinned binary SHA-512 (measured from the verified archive) answers 'differs' without a download
- A4 · Bucket: release.sh switches to the 12-digit name only. Not taken: existing Hives read their persisted 4-digit bucket, so release.sh uploads would stop reaching them; the live account has no 12-digit bucket (Brain 7339d799)
- A5 · Bucket: rename/migrate existing 4-digit buckets to the 12-digit name. Not taken: S3 cannot rename; copying objects and rewriting every Hive's persisted s3\_bucket and .hive-bucket (the Swarm Out bridge) is an irreversible migration for no user gain

Requirements analysis:

- missing\_edge (R1.1): Comparing only \`caddy version\` would treat a caddyserver.com build of the same version as pinned, and downloading on every update makes each update depend on GitHub. The step compares the installed binary's SHA-512 with the pinned binary's SHA-512 (measured from the verified v2.11.9 archive), so an already-pinned Hive downloads nothing. → rewritten
- missing\_edge (R1.2, R1.4): /usr/bin/caddy is running while it is replaced; writing into it can fail with text-file-busy. The new binary is written beside it and renamed over it, and the backup is renamed back on failure. → rewritten
- missing\_edge (R1.2): A Hive whose Caddyfile is still the pre-fix layout exempts /x/../health, so the refused-side check uses a path that is never exempt in any layout, which also keeps the step independent of the /health step's order. → rewritten
- missing\_edge (R2.2, R2.4): If both the 4-digit and the 12-digit bucket exist, the 4-digit one (the one the account's Hives and release uploads already use) is found first; a Hive already recorded on the 12-digit bucket keeps it through its persisted s3\_bucket and gets published releases from GitHub as before. → rewritten
- missing\_edge (R2.2): update() keeps reading the bucket persisted in the Hive's row (never recomputed), so this change does not move any existing Hive; only deploy/retry resolve the name. → rewritten
- missing\_edge (R2.1): The provisioner module docstring and the release.sh comment still describe the 4-digit name as the live scheme; both are rewritten to point at hive/bucket\_name.py, and test\_hive\_provisioner's 12-digit-only bucket test is updated to the new rule. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 17/26 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ ffb2ca25c6b6: 7 pass, 0 fail, 0 not run
- test · swarmai @ c498286e267f: 6 pass, 0 fail, 6 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV' python -m pytest tests/test\_hive\_caddy\_binary\_update.py tests/test\_hive\_bucket\_name.py -q --timeout=60 → pass (33 passed: the Caddy binary step and the bucket rule (fail-to-pass: both files fail to import on the base))
- perl -e 'alarm 115; exec @ARGV' python -m pytest tests/test\_hive\_provisioner.py tests/test\_hive\_update.py tests/test\_hive\_caddy\_health\_exemption.py tests/test\_hive\_hardening.py tests/test\_hive\_origin\_ → pass (314 passed: existing provisioner, update, user\_data, Caddy, permission and bridge tests)
- python scripts/smoke\_import.py → pass (641 modules import)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- bash scripts/sync-version.sh check → pass (all versions 2.5.4)
- bash scripts/run\_bvt.sh → pre\_existing (861 tests; only tests/test\_precommit\_uv\_lock.py (3) fails: it needs a \`uv\` executable on PATH, which this Hive does not have (shutil.which('uv') is None); the same file fails the same way on the run's)
- npx tsc --noEmit → not\_run (no desktop file changed)
- npm run lint:contract → not\_run (no desktop file changed)
- npm run build → not\_run (no desktop file changed)
- 3 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 861 tests; only tests/test\_precommit\_uv\_lock.py (3) fails: it needs a \`uv\` executable on PATH, which this Hive does not have (shutil.which('uv') is None); the same file fails the same way on the run's base

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed
- swarmai · npm run lint:contract: no desktop file changed
- swarmai · npm run build: no desktop file changed
- swarmai · npx vitest run --reporter=verbose: no desktop file changed
- swarmai · python scripts/lint\_skills.py: no skill changed
- swarmai · python scripts/lint\_experts.py: no expert prompt changed

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/hive/bucket\_name.py
- swarmai: backend/tests/test\_hive\_bucket\_name.py
- swarmai: backend/tests/test\_hive\_caddy\_binary\_update.py
