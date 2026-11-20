# # Feature Map replay on a Hive · Audit through DELIVER

Delivered: # Feature Map replay on a Hive ## Goal Make SwarmAI's Feature Map replay (\`python -m scripts.feature\_map replay\` / \`verify\`, engine in \`backend/core/feature\_map/\`, design \`docs/design/2026-11-18-featu. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | WARN | 1h 59m wall · 1h 59m active · 112.6% budget |
| Quality | WARN | 1/1 verification · 4 not run · 10/11 findings closed (12 reported; within 10 lines of one file they count once) (5 test-closed, confirmed by review) · CF-888ca4b3: no mutation check (the fix is the test's own fake clock: taking it out makes the test spin for 3 s, not fail, so no mutation can turn it red) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | WARN | 1.4M comparable · 1.0M input · 380k output · 92.6M cache reads, not counted · 94.0M billed · host-session responses in the run window · 104.1% budget |

Project Agent: SwarmAI · code-change · 11 checks · 1 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 3 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · How does the engine know it must use the boundary, without recognising a Hive → a TCP connect from the engine's process to 169.254.169.254:80 or \[fd00:ec2::254\]:80 (0.5 s each) succeeding; then the replay user is required, else not\_run 'runtime unavailable'. Why: the credential source is the metadata service, on any host and any cloud; a desktop pays one bounded probe per replay; a cloud dev box without the replay user is refused with the reason instead of exposed (DESIGN\_SPEC, agent)
- D2 · How the replay user reaches the tools the Hive user runs (task venvs, task tools, the pinned runtime under the app data folder) → provisioning gives swarm-replay traverse-only (x) ACL on the Hive user's home; nothing is moved. Why: whatever runs as the Hive user must run in replay (the person's rule); the desktop sandbox already runs with the person's read access; the boundary added here is the metadata service and write access to the Hive user's files, both held (DESIGN\_SPEC, agent)
- D3 · How processes the replay user started are stopped, when the Hive user cannot signal them → every start carries FEATURE\_MAP\_SPAWN=&lt;sandbox&gt;/&lt;n&gt;; a helper run as the replay user reads /proc/\*/environ of its own processes and stops the matching ones (TERM, 3 s, KILL); close() stops the whole sandbox prefix. Why: kills the whole tree, also processes that left the process group, without depending on how sudo groups its child (DESIGN\_SPEC, agent)
- D4 · How the sandbox environment reaches the command through sudo's env\_reset → a 0660 JSON file in the sandbox's spawn folder, readable through the sandbox ACL, read and deleted by the helper, which then execs the command with exactly that environment. Why: no sudoers env options to depend on (secure\_path, always\_set\_home), and the values (a target's tokens) never appear in ps (DESIGN\_SPEC, agent)
- D5 · Which packages → the 12 packages ldd named for the missing sonames, plus fontconfig, liberation sans/serif/mono, noto sans CJK and noto color emoji, plus nftables. Why: with the libraries alone Chromium launched but every glyph was blank (no fontconfig or fonts on AL2023), so screenshots carry no evidence; CJK and emoji because the app's UI shows both (DESIGN\_SPEC, agent)
- D6 · What a host without the libraries reports → runtime.ensure runs ldd on the headless shell on Linux and raises RuntimeUnavailable naming the missing libraries. Why: today a launch failure is reported as fail on every web walk, which reads as product regressions (DESIGN\_SPEC, agent)
- D7 · How long the provisioner waits for the update script → UPDATE\_WAIT\_POLLS 60 -&gt; 150 (750 s), at least twice the grammar (120 s) plus replay-host (240 s) deadlines, under UPDATE\_STALE\_S (900 s). Why: the first update installs about 250 MB of packages; a slow step must not read as a failed update while the script still runs (DESIGN\_SPEC, agent)
- D8 · sudo behaviour for the replay user → Defaults&gt;swarm-replay \!use\_pty, \!requiretty, \!pam\_session; the drop-in is checked with visudo -cf before it is installed. Why: no pty (the command writes straight to the log file the engine opened), no logind session or user manager per spawn; an invalid file is never installed (DESIGN\_SPEC, agent)
- D9 · hive/setup.sh and hive/update-hive.sh exit 1 at their top → their preserved bodies call the same step, so the reference stays correct; the live paths are user\_data.py and the provisioner. Why: the task names them; a reference body that disagrees with the live path misleads (DESIGN\_SPEC, agent)
- D10 · first-run-setup-q8rx failed on this Hive whatever the boundary: it expects the ADA card, which the wizard shows only where ada is on PATH (core.aws\_account.find\_ada) → the walk waits for detection to finish and, when the external cards are shown, uses the wizard's own Switch before checking ADA and AWS SSO; the skill says a walk must give the same result on every machine. Why: the walk leaked host state into its result; the product behaves correctly on both kinds of machine (BUILD report · swarmai, agent)
- D11 · the longer update wait (750 s) can pass UPDATE\_STALE\_S, and the Hive list releases an 'updating' row older than that on every listing → every poll of the SSM wait refreshes the row (status updating), as the origin step's wait already does. Why: a long first update must not be ended as interrupted while it still runs; not in the plan, found reading release\_stale\_updates' caller (BUILD report · swarmai, agent)
- D12 · processes in this environment start with SIGTERM ignored (seen in the harness) → the helper's stop is TERM, 3 s, then KILL on everything still tagged. Why: a TERM-only stop would leave such a service running (BUILD report · swarmai, agent)
- D13 · the R2 security expert's LOW finding (a fork loop can outrun a list-then-kill) → fixed in TEST: after the TERM pass the helper sends SIGKILL to -1 as the replay user until none is left; refused as root or as the caller; Sandbox.stop\_user logs survivors (the next resolve refuses). Why: a cheap deterministic fix; leaving a fixable defect as residual is not done (TEST report · swarmai, agent)
- D14 · the security finding that the replay user can read what the Hive user's home leaves world-readable → not changed in this run; residual, with a ToDo to make the Hive's app data private from the replay user. Why: pre-existing exposure that this change narrows (before it the replayed app ran as the Hive user, with read and write); closing it needs its own design of where the task tools and the pinned runtime live (outside the home, or per-path grants), and the requirement of this run is the instance credentials (TEST report · swarmai, agent)
- D15 · the second visit's LOW finding: a non-dumpable process hides its /proc files → processes are counted by the real uid in /proc/&lt;pid&gt;/status and kill(-1) is sent once whatever the listing says. Why: on this kernel only the environment (the tag) was hidden, but kernels differ; kill(-1) does not depend on any listing (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A network namespace for the sandbox (unshare -n, or ip netns made by root). Not taken: an unprivileged namespace has no outbound network, so prepare (npm ci, uv sync) and the app's own server calls break, which the person's rule forbids (what runs on the host must run in replay); a root-made one needs a root helper to enter; and the process stays the Hive user, which with kernel.yama.ptrace\_scope=0 (measured on this Hive) can ptrace the backend or rewrite /opt/swarmai code the backend later runs with the role
- A3 · An owner-match rule on a group the sandbox enters with sg/newgrp. Not taken: a process can newgrp back to its login group without a password: no boundary
- A4 · A cgroup boundary: systemd IPAddressDeny on a unit per replay, or nft socket cgroupv2 on a delegated sub-cgroup. Not taken: starting system units needs a polkit grant that is root-equivalent (User= can be root); a delegated cgroup belongs to the Hive user, so the process can move itself out
- A5 · Turn IMDS off or lower the hop limit. Not taken: the Hive's own backend gets its Bedrock credentials from IMDS; the hop limit only stops containers, not host processes

Requirements analysis:

- conflict (R1.4): hive/setup.sh and hive/update-hive.sh exit 1 at their top (deprecated); the live paths are user\_data.py and the provisioner's SSM update. The task names them, so their preserved bodies get the same step to stay a correct reference. → confirmed
- missing\_edge (R1.2): the first update installs about 250 MB of packages; the provisioner waits 300 s for the whole script today and a test pins grammar deadline x2 inside it, so the wait grows to cover the new deadline too → rewritten
- missing\_edge (R3.2, R3.4): how the engine knows it is on a Hive without recognising a machine: the metadata service answering is the trigger, on any host; a cloud dev box without the replay user is refused rather than exposed → rewritten
- missing\_edge (R3.5): the Hive user cannot signal or delete what the replay user owns; stop and removal run through the replay user → rewritten
- missing\_edge (R1.3): libraries alone launch Chromium but every glyph renders blank (no fontconfig, no fonts on AL2023): screenshots would carry no evidence; fonts are part of the set → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 11/26 tool calls
- correctness · review r1: read 100% of its prompt · 11/26 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls
- security · review r2: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- security · review r2: read 100% of its prompt · 3/25 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 532e5622ae09: 10 pass, 0 fail, 1 not run
- test · swarmai @ 45f4172ff161: 12 pass, 0 fail, 4 not run
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

TEST · swarmai:
- bash scripts/run\_bvt.sh → pass (74 files, 1203 tests (with tests/test\_hive\_replay\_host.py now in the manifest))
- python scripts/smoke\_import.py → pass (696 modules import on Linux)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new HIGH findings or secrets)
- python scripts/lint\_skills.py → pass (the feature-map skill text changed; clean)
- python scripts/lint\_experts.py → pass (clean)
- bash scripts/sync-version.sh check → pass (versions match)
- npx tsc --noEmit → not\_run (no file under desktop/ changed; recorded through task\_checks on the branch after landing)
- npm run lint:contract → not\_run (no file under desktop/ changed; recorded through task\_checks on the branch after landing)
- npm run build → not\_run (no file under desktop/ changed (the walks' vite build of desktop/ ran and served the UI in the harness verify); recorded through task\_checks after landing)
- 6 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no file under desktop/ changed; recorded through task\_checks on the branch after landing
- swarmai · npm run lint:contract: no file under desktop/ changed; recorded through task\_checks on the branch after landing
- swarmai · npm run build: no file under desktop/ changed (the walks' vite build of desktop/ ran and served the UI in the harness verify); recorded through task\_checks after landing
- swarmai · npx vitest run --reporter=verbose: no file under desktop/ changed; recorded through task\_checks on the branch after landing

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/feature\_map/replay\_user.py
- swarmai: backend/core/feature\_map/replay\_user\_helper.py
- swarmai: backend/tests/test\_feature\_map\_replay\_user.py
- swarmai: backend/tests/test\_hive\_replay\_host.py
- swarmai: hive/replay-host.sh
