# 起 pipeline：Hive 一人一把钥匙。按设计稿 docs/design/2026-11-26-hive-access-credentials.md（r… · Audit through DELIVER

Delivered: 起 pipeline：Hive 一人一把钥匙。按设计稿 docs/design/2026-11-26-hive-access-credentials.md（r2，main 9ae19d7db）实现：Host 机器钥匙不给人看，每人一把可单独撤销的钥匙；Caddy 和后端读同一份名单文件；Caddy admin off，agent（swarm 用户）写不了认证文件；apply\_access 单一写入. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 1h 13m wall · 1h 13m active · 33.9% budget |
| Quality | WARN | 1/1 verification · 9/10 findings closed (12 reported; within 10 lines of one file they count once) (8 test-closed, confirmed by review) · 1 HIGH finding open · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 1.2M comparable · 934k input · 267k output · 80.0M cache reads, not counted · 81.2M billed · $28.81 book cost · host-session responses in the run window · 92.0% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 0 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · Where does the list file live, given /etc/swarmai is root 0700 and the middleware runs as swarm? → /etc/swarmai-auth/ (root:swarm 0750) with hive-users root:swarm 0640. Why: swarm must read it at runtime to pick up changes without a restart, and must not write it; /etc/swarmai stays root-only for hive-auth.env (DESIGN\_SPEC, agent)
- D2 · How does admin off reach existing Hives? → add admin off to the template's ORIGIN-GUARD-GLOBAL block. Why: the existing first update step (health\_exempt\_fix\_sh) re-inserts the guard blocks from the template on every update and \_lock\_origin inserts them on Hives without a secret, so no new editor is needed; a Hive whose guard was removed by the lock rollback keeps the admin API until its next lock (DESIGN\_SPEC, agent)
- D3 · How is the Host key rotated without a lockout? → the Host key user alternates between host and host-b: apply the list with both, check the new key through CloudFront, save it, apply without the old. Why: Caddy refuses a repeated user name, so two keys need two names; apply needs only AWS and SSM, so even a failed rotation is recoverable by rotating again (DESIGN\_SPEC, agent)
- D4 · How is an existing Hive migrated? → a new update step (access\_list\_fix\_sh, after the /health step, before the install backup) moves the inline admin line into the list file and switches the block to import, then the Host, after a healthy update, adds a host key and keeps admin as a legacy access. Why: the DDD decision 'an existing Hive's Caddyfile is fixed by the first step of its update' (667b02be): an editor generated from the template, caddy validate, restart, a both-ways check, restore on failure (DESIGN\_SPEC, agent)
- D5 · How are access rows removed with their Hive, given Hive rows are deleted in seven places? → a SQLite trigger AFTER DELETE ON hive\_instances deletes its hive\_access rows. Why: one mechanism instead of an edit at every delete site (DESIGN\_SPEC, agent)
- D6 · Does the export keep the Host key? → the backup snapshot copy has hive\_instances.auth\_password set to NULL before tables are dumped. Why: a restored Host rotates the key over SSM, which needs no old key (DESIGN\_SPEC, agent)
- D7 · Keep generate\_password (the 4-word passphrase)? → deleted with its tests. Why: no caller remains once deploy uses a 192-bit key (BUILD report · swarmai, agent)
- D8 · Should the manual hive/Caddyfile (env-based) get admin off too? → yes. Why: same threat, one line; it keeps the HIVE\_USER/HIVE\_PASS\_HASH pair because a manual setup has no list (BUILD report · swarmai, agent)
- D9 · Where else did the old password UI live? → SwarmOutSettings.tsx also rendered AuthDisplay; it now renders HiveAccess, which shows one line for a stopped Hive. Why: the build broke otherwise and the existing so-hive-login test stays valid (BUILD report · swarmai, agent)
- D10 · bcrypt cost for the new keys? → 14, unchanged. Why: Caddy compares an unknown user against a cost-14 fake hash, so a lower cost buys nothing (BUILD report · swarmai, agent)
- D11 · Two pre-existing red tests in the update-script area (test\_hive\_hardening install line, test\_hive\_tmp\_on\_disk restart anchor) → fixed in this change. Why: they check the update script this run changes and were red on the base for a stale anchor (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep two copies (Caddyfile inline lines plus a HIVE\_CREDENTIALS env var) written by one script, with a backend restart per change. Not taken: two consumers of one secret drift (the existing reset\_password already carries that warning), and a backend restart ends running work; Caddy 2.11.9 was verified locally to accept import inside the basic\_auth block, so one file serves both
- A3 · Put CloudFront + Cognito/OIDC with MFA in front of the Hive. Not taken: an IdP per account, a login page and an edge function for every user; the design doc 2026-11-26-hive-access-credentials §4 keeps it as a later layer on top of this one
- A4 · Block the Caddy admin API with an nftables rule for non-root users instead of admin off. Not taken: a new firewall mechanism where one Caddyfile line suffices; no code path uses the admin API (every Caddy change restarts the service)

Requirements analysis:

- missing\_edge (R5.1): a deploy retry clears auth fields and redeploys from user data; the user data must write the current list so hive\_access rows still match → rewritten
- missing\_edge (R2.1): fleet.replace creates a new Hive row; accesses are not carried over (hive\_access rows cascade with the old row) and the owner adds them again → rewritten
- missing\_edge (R8.1): after a restore the Host has no host key; the card offers rotate, which needs only AWS and SSM → rewritten
- missing\_edge (R3.5): whether Caddy 2.11 accepts import inside a basic\_auth block is not verified; if not, the same script renders the list lines into the block → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 8/27 tool calls
- correctness · review r1: read 61% of its prompt · 14/28 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 20/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- security · review r2: read 100% of its prompt · 10/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 7840353d7f46: 13 pass, 0 fail, 0 not run
- test · swarmai @ 6254b8c43759: 12 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not run
  - backend-bvt: pass
  - frontend-run-frontend-tests: pass

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_95169194/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: hive/Caddyfile, hive/swarmai-hive.service
- changed paths the targets ignore: backend/tests/test\_backup\_manager.py, backend/tests/test\_hive.py, backend/tests/test\_hive\_access.py, backend/tests/test\_hive\_auth.py, backend/tests/test\_hive\_hardening.py, backend/tests/test\_hive\_origin\_secret.py, backend/tests/test\_hive\_provisioner.py, backend/tests/test\_hive\_ssh\_routes.py, backend/tests/test\_hive\_tmp\_on\_disk.py, backend/tests/test\_swarm\_out\_ops.py


TEST · swarmai:
- CADDY\_BIN=&lt;caddy 2.11.9&gt; .venv/bin/python -m pytest tests/test\_hive\_access.py tests/test\_hive\_auth.py tests/test\_hive.py tests/test\_backup\_manager.py tests/test\_ssh\_machine\_migration.py tests/test\_hiv → pass (213 tests)
- .venv/bin/python -m pytest tests/test\_hive\_provisioner.py tests/test\_hive\_caddy\_health\_exemption.py tests/test\_hive\_hardening.py tests/test\_hive\_origin\_secret.py -q --timeout=60 --maxfail=1000 → pass (203 tests)
- .venv/bin/python -m pytest tests/test\_hive\_tmp\_on\_disk.py tests/test\_hive\_replay\_host.py tests/test\_tree\_sitter\_bundle\_setup.py tests/test\_hive\_ssh\_routes.py tests/test\_swarm\_out\_ops.py tests/test\_ela → pass (292 tests)
- scripts.mutation\_check (claim-held revoke, single-claim rotation, malformed list read once, rotation re-reads the Host key after its claim) -- pytest tests/test\_hive\_access.py tests/test\_hive\_auth.py → pass (4 caught, 0 missed (the 17 earlier mutations also caught))
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- npx vitest run src/components/settings src/components/swarm-out/\_\_tests\_\_/{SwarmOutSingleEntry,SettingsLoading,SwarmOutIdlePerHive}.test.tsx src/i18n → pass (17 files, 124 tests)
- 2 more in run_audit.json

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/hive/access.py
- swarmai: backend/tests/test\_hive\_access.py
- swarmai: desktop/src/components/settings/\_\_tests\_\_/HiveAccess.test.tsx
