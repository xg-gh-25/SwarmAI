# Feature Map replay on a Hive must not depend on any access into the Hive user's… · Audit through DELIVER

Delivered: Feature Map replay on a Hive must not depend on any access into the Hive user's home: the runtime and task tools move to a root-made shared location, the home traverse ACL goes, the service and existi. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 1h 0m wall · 1h 0m active · no baseline yet (1/5 runs) |
| Quality | WARN | 1/1 verification · 4 not run · 1 red at base · 6/7 findings closed (3 test-closed, confirmed by review) · 2 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 832k comparable · 619k input · 213k output · 42.3M cache reads, not counted · 43.1M billed · $17.67 book cost · host-session responses in the run window · no baseline yet (1/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 2 (+4 found on demand) · searched the DDD 3×

## Decisions

- D1 · Where is the shared location and who owns it → /var/lib/swarmai (root:root 0755) and /var/lib/swarmai/replay (Hive user, group swarm-replay, 0750), made by replay-host.sh as root; SWARM\_REPLAY\_SHARED\_DIR overrides it (empty disables) for tests and a pre-provisioned machine. Why: root owns the parent so the Hive user cannot swap the child for a link; the group gives the replay user read and nobody else on the machine (DESIGN\_SPEC, agent)
- D2 · How does the replay user get read access to files created under UMask=0077 → resolve publishes the shared tree before the probe: as the Hive user, go+rX on its own directories and files (go+x where the owner may run), lstat first and symbolic links skipped. Why: one place at the consumer covers every writer (runtime installer, mise, pip in a setup line); a default ACL does not survive pip's chmod (DESIGN\_SPEC, agent)
- D3 · What does the private probe check → open() of &lt;app data&gt;/data.db and listing &lt;app data&gt;, for both the effective app data dir and the default \~/.swarm-ai, as the replay user; any success is a problem; any OSError counts as private. Why: the task names data.db and the app data dir; checking the default dir too keeps a SWARM\_DATA\_DIR override from hiding the real store (DESIGN\_SPEC, agent)
- D4 · How does root tighten the existing tree → runuser -u &lt;Hive user&gt; -- find -P &lt;dir&gt; -xdev -ignore\_readdir\_race \! -type l -perm /077 -exec chmod go= {} +, for \~/.swarm-ai and \~/.claude, once (root-owned marker /var/lib/swarmai/home-private.v1). Why: running as the Hive user means a planted link can never make root change another file; go= keeps the owner's bits so the Hive's own processes keep working (DESIGN\_SPEC, agent)
- D5 · What happens on a Hive whose host side is not yet updated → the new probe refuses the replay there (not\_run, the readable path named) until the next update applies the host side. Why: running untrusted code that can read the person's data is the defect being fixed (DESIGN\_SPEC, agent)
- D6 · Does the SSH machine carrier change → no: its image (hive/ssh\_image.py) has no replay user and never runs replay-host.sh, so replay there is not\_run. Why: checked in backend/hive/ssh\_image.py and ssh\_provisioner.py (DESIGN\_SPEC, agent)
- D7 · How to verify the replay on this Hive whose host side cannot be applied from a task → emulate the shared location in /var/tmp via SWARM\_REPLAY\_SHARED\_DIR, run the changed backend from a copy outside the home with /opt's interpreter, and close \~/.swarm-ai (0700) only for the run, restoring 0755 after. Why: the replay user may not read anything under the home, so code and helper run from under \~/.swarm-ai cannot start; on a Hive they live in /opt (BUILD report · swarmai, agent)
- D8 · Guard for a home of / → outside\_home ignores a home equal to /. Why: otherwise every PATH folder would be dropped (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep the traverse ACL and only make \~/.swarm-ai and \~/.claude 0700/0600 (umask + tightening). Not taken: any later 0755 file or a new folder in the home (\~/.cache, \~/.local, \~/.npm) re-exposes data; the task asks for the exposure to be removed by structure, not by chasing modes
- A3 · At replay time copy every PATH tool directory and the runtime out of the home into a shared folder. Not taken: a copied venv breaks (pyvenv.cfg home and script shebangs hold absolute paths), and copying hundreds of MB per replay; installing in place outside the home has neither problem
- A4 · Per-path ACL grants on the runtime and tool folders inside the home. Not taken: still needs traverse on the home and every parent, which is the exposure itself

Requirements analysis:

- missing\_edge (R2.4): the backend runs with UMask=0077 after this change, so the runtime installer and setup lines (pip install uv) create files only the Hive user can read in the shared location → rewritten
- missing\_edge (R2.5): setup lines can put paths inside the home on PATH (the repo's own .venv/bin); the Hive user resolves a program there and the replay user then cannot run it → rewritten
- missing\_edge (R1.3): root changing modes on a path the Hive user could make a link must not follow it → rewritten
- missing\_edge (R2.1): the SSH machine carrier's image (backend/hive/ssh\_image.py) has no replay user and never runs replay-host.sh, so the replay is not\_run there; it needs no change → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 6/26 tool calls
- correctness · review r1: read 100% of its prompt · 21/26 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ fffaf70d72d6: 6 pass, 0 fail, 0 not run
- test · swarmai @ 546a8e270eda: 9 pass, 0 fail, 4 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5efedc1f/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: docs/design/2026-11-18-feature-map.md, hive/agentcore/Dockerfile, hive/agentcore/supervisor.py, hive/bin/task-tools, hive/replay-host.sh, hive/swarmai-hive.service
- changed paths the targets ignore: backend/tests/conftest.py, backend/tests/test\_agentcore\_supervisor.py, backend/tests/test\_aws\_account\_consumers.py, backend/tests/test\_feature\_map\_replay\_user.py, backend/tests/test\_feature\_map\_runtime.py, backend/tests/test\_hive\_replay\_host.py, backend/tests/test\_hive\_service\_unit.py, backend/tests/test\_hive\_task\_tools.py, backend/tests/test\_hive\_workspace.py


TEST · swarmai:
- python -m pytest tests/test\_hive\_replay\_host.py tests/test\_hive\_service\_unit.py tests/test\_feature\_map\_replay\_user.py tests/test\_feature\_map\_runtime.py tests/test\_hive\_task\_tools.py tests/test\_hive\_wo → pass
- python -m pytest tests/test\_agentcore\_supervisor.py::test\_the\_image\_runs\_the\_packages\_replay\_host\_setup\_as\_root → pre\_existing (the Dockerfile step's own 'rm -rf /var/cache/dnf' cannot remove root's files on this Hive; same failure on the base checkout, line unchanged)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pass
- npx tsc --noEmit → not\_run (no desktop file changed)
- 4 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · python -m pytest tests/test\_agentcore\_supervisor.py::test\_the\_image\_runs\_the\_packages\_replay\_host\_setup\_as\_root: the Dockerfile step's own 'rm -rf /var/cache/dnf' cannot remove root's files on this Hive; same failure on the base checkout, line unchanged

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed
- swarmai · npm run lint:contract: no desktop file or UI string changed
- swarmai · npm run build: no desktop file changed
- swarmai · npx vitest run --reporter=verbose: no desktop file changed
