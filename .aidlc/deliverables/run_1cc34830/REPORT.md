# 实现已定稿的设计 docs/design/2026-11-24-swarm-out-ssh-machine.md（r3）：Swarm Out 第三种执行面 S… · Audit through DELIVER

Delivered: 实现已定稿的设计 docs/design/2026-11-24-swarm-out-ssh-machine.md（r3）：Swarm Out 第三种执行面 SSH 机器（首个实例是 XG 的内部 Cloud Desktop）。D1–D8 已全部定（XG 2026-11-24，D8 默认放开）。按设计 §1. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 2h 15m wall · 2h 15m active · 62.8% budget |
| Quality | WARN | 1/1 verification · 4 not run · 1 red at base · 9/10 findings closed (14 reported; within 10 lines of one file they count once) (6 test-closed, confirmed by review) · 1 HIGH finding open · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | WARN | 1.5M comparable · 1.1M input · 340k output · 132.2M cache reads, not counted · 133.6M billed · host-session responses in the run window · 124.6% budget |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 5 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · generic or Cloud-Desktop-specific → generic 'ssh' carrier; Amazon details are per-machine config. Why: P7, XG 2026-11-24 (DESIGN\_SPEC, agent)
- D2 · how the Hive runs on an old-OS machine → image derived from hive/agentcore/Dockerfile with the package baked read-only; container mounts $HOME and configured paths. Why: spike: AL2 glibc 2.26 cannot install onnxruntime/pillow; read-only package keeps runs from editing the Hive's own gates (DESIGN\_SPEC, agent)
- D3 · how the Host reaches it → one ssh -N -L child per machine on a fixed local port, built on use, never inside offers. Why: spike 4 s build; a fixed port keeps long-held records valid; offers must not spend the shared 20 s budget (DESIGN\_SPEC, agent)
- D4 · AWS credentials of the Hive process → per-machine role SwarmAI-Hive-&lt;name&gt; with the EC2 Hive policy, assumed from the person's profile. Why: keeps a Hive bug from writing other prefixes; the person's identity itself is out of scope (design §7, Q4) (DESIGN\_SPEC, agent)
- D5 · certificate expiry → blocked at dispatch when expired; no remaining-time gate; mid-run failures surface as the run's questions; not a Needs You item. Why: XG 2026-11-24 (DESIGN\_SPEC, agent)
- D6 · private-network tasks → admission stops refusing; placement filters; a failed placement with private needs raises SwarmOutRefused(private=True) and counts. Why: keeps the 422/card/count consumers working (DESIGN\_SPEC, agent)
- D7 · updates → idle and behind -&gt; new image, swap container, roll back on failed /health; added as its own block in hive\_idle for non-EC2 carriers with follow\_host. Why: EC2 path untouched (DESIGN\_SPEC, agent)
- D8 · general tasks on the machine → allowed by default (general\_tasks true); a per-machine switch narrows it. Why: XG 2026-11-24 (DESIGN\_SPEC, agent)
- D9 · package integrity → checksums.txt as the EC2 path does; the r2 GitHub check is dropped. Why: a GitHub-only digest refuses self-built versions and cannot stop an Admin identity (design r3 §7.3) (DESIGN\_SPEC, agent)
- D1 · R1.3 asked whether the SwarmAI-account functions must learn about SSH machines → left unchanged. Why: an SSH machine is bound to its account at add time and the account re-point and keep\_old\_hives checks only act on EC2 rows already filtered by ec2\_only; changing them would widen the diff with no behaviour difference (BUILD report · swarmai, agent)
- D2 · release\_stale\_updates would reset an SSH machine left in 'updating' → the SSH update writes a heartbeat (updated\_at) during the update instead of filtering the release. Why: the release keys on a stale updated\_at; a live SSH update keeps it fresh, and a really dead one is released like an EC2 one (pinned in test\_ssh\_carrier\_ec2\_guards) (BUILD report · swarmai, agent)
- D3 · hive\_idle.\_sessions is EC2-shaped → left on EC2; SSH machines go through \_follow\_other\_carriers (follow-host update only, never idle stop). Why: an SSH machine is the person's own always-on desktop; SwarmAI never stops it (BUILD report · swarmai, agent)
- D4 · where the SSH image Dockerfile lives → a module constant in backend/hive/ssh\_image.py. Why: it is sent over stdin to a fixed build script; no file path on the remote side to validate (BUILD report · swarmai, agent)
- D5 · pin the AL2023 base image by digest → not pinned; tag amazonlinux:2023. Why: the image is built on the person's own machine from the public base each update; pinning would freeze security fixes. Noted for review (BUILD report · swarmai, agent)
- D6 · who may call the hive.ssh.\* ops → all six person-only, including list and update. Why: they spawn ssh with the person's identity on a corporate machine; an agent asks the person (BUILD report · swarmai, agent)
- D7 · how the agent learns whether a private task can be placed → /spec returns private\_reach (true when an SSH machine reaches a private network and takes general tasks). Why: the skill decides before dispatch whether to treat a private task as refused, without a new endpoint (BUILD report · swarmai, agent)
- D8 · should a failed placement of a private task be counted like an admission refusal → yes: \_record\_refusal counts it. Why: the refusal counter is the signal that private tasks are being turned away; dropping it from admit must not drop it from the count (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · a variant of the EC2 carrier that only swaps the address. Not taken: EC2 idle stop, SSM update, start, retry (which builds a whole EC2 Hive) would all act on the machine; each path would need its own branch (design §3 option B)
- A3 · no Hive on the machine; the Host drives a CLI over ssh. Not taken: loses run records, the question bridge, zero-touch gates and Brain sync; a second execution system (design §3 option C)

Requirements analysis:

- missing\_edge (R2.2): a machine without SSH certificates (no corporate SSH CA) must not be blocked as expired → rewritten
- missing\_edge (R3.4): the Host restarts while a tunnel child is alive; a new tunnel on the same fixed port would fail → rewritten
- missing\_edge (R5.3): an SSH offer is TIER\_WARM, so its blocked reason would stop a stopped EC2 Hive from being started under the existing D5 rule → rewritten
- missing\_edge (R7.4): a GitHub-only digest would refuse versions built by hive/release.sh that exist only in the account bucket, and cannot stop an identity that holds Admin; the design r3 keeps the existing checksums.txt check → rewritten

## Review coverage (from each expert's transcript)

- correctness · review r1: read 70% of its prompt · 23/28 tool calls
- security · review r1: read 48% of its prompt · 22/28 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 7/26 tool calls
- correctness · review r1: read 100% of its prompt · 10/26 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 1b487deb7129: 11 pass, 0 fail, 1 not run
- test · swarmai @ cf006b45a44c: 11 pass, 0 fail, 4 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: not_run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not_run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: pass

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✗ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] fail (red at the run's base too): Error: expect(locator).toHaveCount(expected) failed
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/24/0-0-test-failed-1.png
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/24/1-1-trace.zip
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/24/2-2-test-failed-1.png
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/24/3-3-trace.zip
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1cc34830/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: docs/design/2026-11-24-swarm-out-ssh-machine.md
- changed paths the targets ignore: backend/tests/\_ssh\_machine\_store.py, backend/tests/test\_carriers\_dispatch\_layer.py, backend/tests/test\_hive\_idle\_per\_hive.py, backend/tests/test\_hive\_ssh\_mode.py, backend/tests/test\_hive\_ssh\_routes.py, backend/tests/test\_ssh\_carrier\_ec2\_guards.py, backend/tests/test\_ssh\_carrier\_offers.py, backend/tests/test\_ssh\_carrier\_rounds.py, backend/tests/test\_ssh\_machine\_migration.py, backend/tests/test\_ssh\_provisioner.py


TEST · swarmai:
- perl -e 'alarm 280; exec @ARGV' .venv/bin/python -m pytest tests/test\_ssh\_carrier\_ec2\_guards.py tests/test\_hive\_ssh\_routes.py tests/test\_ssh\_carrier\_rounds.py tests/test\_ssh\_carrier\_offers.py tests/te → pass (33 passed: the design's §13 invariant set, the review fixes' tests and the v23 migration paths)
- /tmp/cdspike/runtests.sh tests/test\_swarm\_out\_ops.py tests/test\_carriers\_dispatch\_layer.py tests/test\_carriers\_ec2.py tests/test\_carriers\_placement.py tests/test\_carriers\_placement\_w2.py tests/test\_ca → pass (each file under its own 100s cap, every exit 0: existing tests of the touched carriers, placement, idle stop, usage sync and dispatch)
- /tmp/cdspike/runtests.sh tests/test\_brain\_sync.py tests/test\_elastic\_dispatch\_e2e.py tests/test\_hive\_retry.py tests/test\_hive\_update.py tests/test\_hive.py tests/test\_hive\_fleet.py tests/test\_hive\_swar → pass (each file under its own 100s cap, every exit 0: Brain sync, Hive routes and provisioner, admission, task spec and the dispatch callers. (The BUILD report's command line named some of these files by sh)
- backend/.venv/bin/python scripts/smoke\_import.py → pass
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass
- backend/.venv/bin/python scripts/security\_scan.py → pass
- backend/.venv/bin/python scripts/lint\_skills.py → pass
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- npm run build → pass
- 6 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_1cc34830: 16 reached features, 31 walks: 30 pass; chat-tabs-apart-x7tn (two tabs stream at once) fails here (regular) and fails the same way on the run's base, both projects, in the same replay (base\_results): main moved since the first replay, and this change touches no chat-tab code

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: the whole backend BVT is CI's broad coverage; the change's own tests and every touched module's tests ran above
- swarmai · python scripts/lint\_experts.py: no expert prompt changed
- swarmai · bash scripts/sync-version.sh check: no version file changed
- swarmai · R9 end-to-end on the real Cloud Desktop: needs this change built into the daemon and a restart, which need XG's approval (AGENT R12)

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_ssh\_machine\_migration.py
