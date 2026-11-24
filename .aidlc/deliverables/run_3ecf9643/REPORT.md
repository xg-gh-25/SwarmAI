# XG: 「用户不应该去操心 EC2, CloudDesktop, Hive 的各种配置, SwarmAI 应该把所有Hive 的配置的setting 搞定」。… · Audit through DELIVER

Delivered: XG: 「用户不应该去操心 EC2, CloudDesktop, Hive 的各种配置, SwarmAI 应该把所有Hive 的配置的setting 搞定」。落到 SSH 机器（Cloud Desktop）的添加：人只填 SSH 主机，其余由 SwarmAI 从机器上自己拿到并填好——(1) AWS 源 profile：读机器上的 profile，在容器里逐个查身份，用落到 SwarmAI 账户的. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 52m 17s wall · 52m 17s active · 24.2% budget |
| Quality | WARN | 1/1 verification · 4 not run · 1 red at base · 5/6 findings closed (7 reported; within 10 lines of one file they count once) (3 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 386k comparable · 295k input · 92k output · 37.4M cache reads, not counted · 37.8M billed · host-session responses in the run window · 29.6% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 2 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · which profile when several reach the SwarmAI account → the first in name order, recorded in carrier\_config.source\_profile. Why: deterministic across installs and retries; the card shows which one is used (DESIGN\_SPEC, agent)
- D2 · which directories under / are mounted → every directory that is not in a fixed list of standard system directories (bin, boot, dev, etc, home, lib, lib32, lib64, libx32, lost+found, media, mnt, opt, proc, root, run, sbin, snap, srv, sys, tmp, usr, var), not the home or a directory above its real path, and that passes \_mount\_ok; read-write when the user can write it, else read-only. Why: a machine's own top-level directories are where its toolchains and workspaces live (/apollo and /workplace on a Cloud Desktop); the standard ones are the OS's own and the container has its own (DESIGN\_SPEC, agent)
- D3 · when the found mounts are refreshed → at every install and update when the machine was added without mounts (carrier\_config.mounts\_auto); mounts given at add are kept as given. Why: a directory added to the machine later reaches the container at the next update; an explicit choice through the API is never overridden (DESIGN\_SPEC, agent)
- D4 · what happens to the certificate hint and the private-network flag in the form → both leave the add form; private-network reach defaults to on and has a switch on the machine card; the expired-certificate line uses the generic text when no hint is set; the API keeps both fields. Why: the person never has to set them; the API stays compatible for an agent or a script that does (DESIGN\_SPEC, agent)
- D5 · how many identity checks may the profile search run → at most 8 candidate profiles, each one container run with the existing 120 s cap. Why: each check is one docker run of a few seconds; a machine with many profiles cannot make the install take unbounded time (the cap is a hang guard on the search, not a cost limit) (DESIGN\_SPEC, agent)
- D1 · R3.1 says writable directories mount read-write: what about /workplace, which the user cannot write but whose per-user folder they can → a directory counts as writable when the user can write it or a folder directly in it. Why: mounting read-only would make /workplace/&lt;user&gt; read-only in the container; permissions inside the container stay the machine's own (same uid), so read-write grants nothing extra (BUILD report · swarmai, agent)
- D2 · the frontend locale keys for the removed fields → removed (profile, profileHint, mounts, mountsPlaceholder, mountsHint, certHint, certHintPlaceholder); new keys for what the card shows. Why: no component reads them any more; the locale key checks pass (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A separate probe call before adding: the form takes the host, asks the machine, then shows the found profile and mounts for the person to confirm. Not taken: it puts the settings back in front of the person, which the requirement removes; the install already runs a preflight on the machine, so a second round trip adds a step and a state for nothing
- A3 · Mount the whole root filesystem (or every directory under /) and use the default AWS profile. Not taken: the whole filesystem exposes /var/run/docker.sock and /tmp (where the env file lives for a moment) and other users' homes; 'default' is often not the profile that reaches the SwarmAI account (on the Cloud Desktop the only profile is a named one)

Requirements analysis:

- missing\_edge (R2.1): several profiles may reach the SwarmAI account; the first in name order is taken and recorded, so the choice is the same on every install → rewritten
- missing\_edge (R3.1): the home directory can be a symlink (on a Cloud Desktop /home/&lt;user&gt; points to /local/home/&lt;user&gt;); mounting /local would show other users' homes; directories above the home's real path are left out → rewritten
- missing\_edge (R3.1): a directory added to the machine later; the list is found again at every install and update when the mounts were not given → rewritten
- vague (R1.2): EC2 Hives and Elastic Workers settings already have defaults the person need not touch (instance type, idle stop, region shown, account); the legacy other-account dialog keeps its fields; out of scope for this run, listed in the report → confirmed

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 5/26 tool calls
- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 2aeae4a15864: 8 pass, 0 fail, 0 not run
- test · swarmai @ 04c3aad619d9: 8 pass, 0 fail, 4 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not_run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: pre_existing

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_3ecf9643/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: docs/design/2026-11-24-swarm-out-ssh-machine.md
- changed paths the targets ignore: backend/tests/test\_ssh\_machine\_autoconfig.py, backend/tests/test\_ssh\_provisioner.py, desktop/src/components/swarm-out/\_\_tests\_\_/SshMachines.test.tsx


TEST · swarmai:
- perl -e 'alarm 280; exec @ARGV' .venv/bin/python -m pytest tests/test\_ssh\_machine\_autoconfig.py tests/test\_ssh\_provisioner.py tests/test\_hive\_ssh\_routes.py tests/test\_ssh\_carrier\_ec2\_guards.py tests/t → pass (215 passed)
- backend/.venv/bin/python scripts/smoke\_import.py → pass
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass
- backend/.venv/bin/python scripts/security\_scan.py → pass
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- npm run build → pass
- npx vitest run src/components/swarm-out src/i18n → pre\_existing (334 of 335 pass; funnel.test.tsx 'a run landed by hand says who landed it' fails the same way on the run's base (another session's manual-landing change); SshMachines and the locale checks pass)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_3ecf9643 → pass (16 reached features, 31 walks, all pass)
- bash scripts/run\_bvt.sh → not\_run (the whole BVT is CI's; the change's tests and every SSH-carrier test ran above)
- 3 more in run_audit.json

Tests were not run again after main moved: tests carried over a main move: swarmai (main bb3803e1406e-&gt;7f861ce0947e): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Red at the run's base too (the agent's report, not checked)

- swarmai · npx vitest run src/components/swarm-out src/i18n: 334 of 335 pass; funnel.test.tsx 'a run landed by hand says who landed it' fails the same way on the run's base (another session's manual-landing change); SshMachines and the locale checks pass

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: the whole BVT is CI's; the change's tests and every SSH-carrier test ran above
- swarmai · python scripts/lint\_skills.py: no skill changed
- swarmai · python scripts/lint\_experts.py: no expert prompt changed
- swarmai · bash scripts/sync-version.sh check: no version file changed

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_ssh\_machine\_autoconfig.py
