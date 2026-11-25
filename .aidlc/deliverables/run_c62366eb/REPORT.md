# XG: 「起 pipeline 实现方案 B」。按已定稿的设计 docs/design/2026-11-24-ssh-machine-roles-anywhe… · Audit through DELIVER

Delivered: XG: 「起 pipeline 实现方案 B」。按已定稿的设计 docs/design/2026-11-24-ssh-machine-roles-anywhere.md（r2）实现：SSH 机器的 Hive 用 IAM Roles Anywhere 拿自己窄角色的凭据，机器上不需要任何 AWS 设置，Host 关着也能用。Host 当小 CA（每 Host×账户×区域一个，EC P-256，2 年. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 2h 59m wall · 2h 59m active · 82.9% budget |
| Quality | WARN | 1/1 verification · 3 not run · 1 red at base · 5/7 findings closed (8 reported; within 10 lines of one file they count once) (4 test-closed, confirmed by review) · 1 HIGH finding open · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 1 required approval (approvals are oversight, not interventions) |
| Token Cost | PASS | 1.3M comparable · 1.0M input · 243k output · 52.6M cache reads, not counted · 53.9M billed · $23.48 book cost · host-session responses in the run window · 96.2% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 0

## Decisions

- D1 · where the identity code lives → a new module backend/hive/ssh\_identity.py (CA, CSR signing, trust policy, Roles Anywhere create/delete, helper download and check); ssh\_provisioner calls it. Why: keeps the provisioner's install flow readable and lets the crypto and policy shapes be tested without SSH (DESIGN\_SPEC, agent)
- D2 · where the CA key is stored → \~/.swarm-ai/state/hive-ca/&lt;account&gt;-&lt;region&gt;/ca.key (0600, directory 0700), not in backups. Why: the Host runs on macOS, Windows and Linux; the same file rules as the Host's other secrets; a lost CA is replaced (design §4.6) (DESIGN\_SPEC, agent)
- D3 · how the machine makes its key and request → a fixed script run in the Hive image's Python (cryptography) writes a new P-256 PKCS#8 key to $HOME/.swarmai-hive/identity/key.pem (0600) and prints only the CSR. Why: the key never leaves the machine; the image already carries cryptography (DESIGN\_SPEC, agent)
- D4 · the helper's version and checksums → aws\_signing\_helper 1.8.6, SHA256 per architecture written in code from the official credential-helper page; the Host downloads once and caches it under its state directory. Why: the official page publishes the checksums; Linux binaries are not code-signed (DESIGN\_SPEC, agent)
- D5 · how renewal is triggered → a pass in hive\_idle.check\_once before the idle-stop work, for running SSH machines whose recorded not\_after is under 10 days. Why: follow\_host returns early when nothing is behind or on a dev build; renewal must not depend on that (DESIGN\_SPEC, agent)
- D6 · whether run\_hive\_checks also checks the Roles Anywhere permissions (design §9) → no; a refused Roles Anywhere call is named at install and renewal (ssh\_identity.\_aws). Why: run\_hive\_checks is the settings line for EC2 Hives; an account on the older template would read as unable to build EC2 Hives (BUILD report · swarmai, agent)
- D7 · how the template holds the new statements → iam:UpdateAssumeRolePolicy joins iam:PutRolePolicy in one statement on SwarmAI-Hive-\* with the boundary condition; Roles Anywhere create only tagged swarmai:managed, other calls only on tagged resources, lists unscoped. Why: the template must stay under IAM's 10,240-character inline policy limit (10,101 now); anchor and profile ids are random, so the tag is the only scope (verified against the Service Authorization Reference) (BUILD report · swarmai, agent)
- D8 · how the new container's identity is checked in every update → WHOAMI\_SH runs sts get-caller-identity inside the running container after /health, on every update, before COMMIT\_SH. Why: the design asked for it in the migration update; it costs one SSH call and catches a broken profile on any update (BUILD report · swarmai, agent)
- D9 · cryptography as a dependency → listed directly in pyproject.toml. Why: the Host's CA and the machine's key generation inside the image both import it; it was only present transitively (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · The Host assumes the narrow role and sends temporary credentials over SSH. Not taken: the machine stops within at most 12 hours of the Host going off
- A3 · Keep finding the person's AWS profile on the machine. Not taken: machines without one need the person to configure AWS, which the requirement removes
- A4 · AWS Private CA as the CA. Not taken: a fixed monthly cost per CA for every user; an external certificate bundle is free
- A5 · Keep the key in a root-only sidecar container and use the helper's serve mode. Not taken: stronger against key theft but adds a container and a different SDK credential path; kept as later hardening (design §5)

Requirements analysis:

- missing\_edge (R4.1): the Host is off for longer than the certificate's remaining time; the card says the identity certificate expired and the next round after the Host is back renews it → rewritten
- missing\_edge (R1.2): two Hosts manage one account: each has its own CA and trust anchor named with its host id → rewritten
- missing\_edge (R7.1): a permission missing at install: the error names the action → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 8/27 tool calls
- correctness · review r1: read 100% of its prompt · 9/27 tool calls
- security · review r2: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/27 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 615c1f2c3347: 6 pass, 0 fail, 1 not run
- test · swarmai @ 9b50e32e09c1: 11 pass, 0 fail, 3 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not_run
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c62366eb/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: docs/design/2026-11-24-ssh-machine-roles-anywhere.md
- changed paths the targets ignore: backend/tests/\_ssh\_machine\_store.py, backend/tests/test\_aws\_permissions.py, backend/tests/test\_ssh\_identity.py, backend/tests/test\_ssh\_machine\_autoconfig.py, backend/tests/test\_ssh\_preflight\_hints.py, backend/tests/test\_ssh\_provisioner.py, desktop/src/components/swarm-out/\_\_tests\_\_/SshMachines.test.tsx


TEST · swarmai:
- perl -e 'alarm 400; exec @ARGV' .venv/bin/python -m pytest -q -p no:cacheprovider tests/test\_ssh\_identity.py tests/test\_ssh\_provisioner.py tests/test\_ssh\_machine\_autoconfig.py tests/test\_ssh\_preflight → pass (183 passed on the final tree: design §8 items 1-10, the SSH machine, idle loop and AWS-module tests)
- python scripts/smoke\_import.py → pass (711 modules import)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (the two published helper checksums and a profile name in a test are marked as not credentials)
- python scripts/lint\_skills.py → pass (clean)
- python scripts/lint\_experts.py → pass (clean)
- bash scripts/run\_bvt.sh → pass (1380 tests on the final tree)
- npx tsc --noEmit → pass (clean (run on this tree's desktop files: no desktop file changed after that run))
- npm run lint:contract → pass (clean (run on this tree's desktop files: no desktop file changed after that run))
- npm run build → pass (bundle within its caps (run on this tree's desktop files: no desktop file changed after that run))
- 5 more in run_audit.json

Tests were not run again after main moved: tests carried over a main move: swarmai (main 95a27ec9d792-&gt;018eb2b6fc10): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Red at the run's base too (the agent's report, not checked)

- swarmai · npx vitest run src/components/swarm-out: 313 passed, 1 failed: funnel.test.tsx 'a run landed by hand says who landed it' fails the same way on the run's base tree (checked there); SshMachines.test.tsx passes (run on this tree's desktop files: no desktop file changed after that run)

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/sync-version.sh check: the change touches no version
- swarmai · npx vitest run --reporter=verbose: the full frontend suite: the change reaches only the Swarm Out components, run above
- swarmai · real-machine end to end (design §8 item 11): needs this change built into the Host daemon and a restart, which XG approves; done after landing

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/hive/ssh\_identity.py
- swarmai: backend/tests/test\_ssh\_identity.py
