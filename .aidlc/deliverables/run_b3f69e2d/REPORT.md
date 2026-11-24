# Replace version-string staleness with a build identity across package, Host, EC… · Audit through DELIVER

Delivered: Replace version-string staleness with a build identity across package, Host, EC2 update, bucket sync and Elastic image tag; bake the replay host setup into the Elastic image; place Feature Map tasks o. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 55m 7s wall · 55m 7s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 5 not run · 2 red at base · 7/7 findings closed (8 reported; within 10 lines of one file they count once) (5 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 1.1M comparable · 805k input · 258k output · 76.0M cache reads, not counted · 77.0M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 2 (+3 found on demand) · searched the DDD 2×

## Decisions

- D1 · Which files make the digest → git-tracked files (index blob ids, worktree blobs for modified ones via git hash-object --stdin-paths) under backend, hive, desktop minus desktop/src-tauri, plus VERSION; outside a git tree the same blob ids computed in Python over a walk. Why: git blob ids are line-ending normalised and identical for the same content on every builder (CI's Host build and Hive package jobs, a dev checkout); untracked files are left out so build outputs never change the id (DESIGN\_SPEC, agent)
- D2 · What a failed or pointless update does → per-Hive backoff (swarm\_out\_settle RETRY\_FIRST\_S doubling to RETRY\_MAX\_S) keyed by the target (version+build id); a same-version Hive whose build has no package raises before SSM. Why: the person wants retries, not one try per process; the backoff and the strict refusal bound churn when no package of the Host's build exists (DESIGN\_SPEC, agent)
- D3 · What Elastic compares → the runtime's image tag against the Host's tag &lt;version&gt;-b&lt;id&gt; (just &lt;version&gt; when the Host has no id); build\_image.sh pushes both tags. Why: the existing update path keys on the tag and pins its digest; a tag per build reuses it unchanged (DESIGN\_SPEC, agent)
- D4 · Can the Elastic image load the nftables rule → no: the image's replay step runs replay-host.sh image mode at build time (packages, user, sudoers), the firewall stays a run-time root step that the image never has; the existing probe then reports not\_run with a reason naming the rule. Why: the container runs as the non-root Hive user (USER swarm, supervisor as swarm) and nothing grants CAP\_NET\_ADMIN; the spike recorded the SDK's credential channel as iam-role (the metadata service), so the boundary is needed and must not be skipped (DESIGN\_SPEC, agent)
- D5 · How old Hives without a features\_schema field are read → as schema 1. Why: fail-safe: a Hive that does not say is never trusted with a schema-2 folder (DESIGN\_SPEC, agent)
- D6 · Test isolation from the checkout's own identity → an autouse conftest fixture pins the Host identity to unknown; tests that need one set it. Why: the identity is environment-derived (like time); without the pin every test's behaviour would depend on the checkout it runs in (DESIGN\_SPEC, agent)
- D7 · How the Host's own identity is warmed → main.py reads it once at start off the loop; async callers also read it through asyncio.to\_thread. Why: a source-run Host must identify the code it loaded, not later edits; the git call must never run on the event loop (BUILD report · swarmai, agent)
- D8 · Where the Elastic row keeps its build → the row's version column holds the image tag (&lt;version&gt;-b&lt;id&gt;), and fleet/status show display\_version of it. Why: the standby round's 'ready' check and the update path key on that column already; no schema change (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Digest of the package's own bytes (tarball sha256 or a hash of the staged tree). Not taken: A tarball is not reproducible and the Host is a PyInstaller binary or a checkout, not a package: the Host could never compute the digest it compares against; the staged tree includes generated files (frontend build, seed config) that differ between builders of the same source.
- A3 · Bump VERSION on every commit (a dev suffix) and keep the string compare. Not taken: Changes the release process for every user, leaves dirty trees indistinguishable, and Elastic images would still exist only per release; the person asked for the build to decide, with the version kept for display.
- A4 · The Host builds a Hive package itself whenever the bucket's differs. Not taken: A packaged Host has no checkout, npm or release.sh; building takes minutes and network on the chat machine. The Host instead finds a package of its build (bucket sidecar, dist/ beside a source-run Host, the GitHub release) and says which is missing.

Requirements analysis:

- undefined\_term (R1.1, R1.2): 'content digest of the package': a tarball sha256 is not reproducible and the Host is not a package. Defined as the digest of the git blob ids of the tracked files under backend/, hive/ and desktop/ (minus desktop/src-tauri) plus VERSION, i.e. the source the package is built from; the package's own sha256 is kept beside it (sidecar package\_sha256) to tie the identity to the bytes a Hive checks. → rewritten
- missing\_edge (R3.3): Host build has no package anywhere while the Hive is a version behind: the version's best package is still installed (progress), only a same-version Hive refuses, so an update never churns on a package that cannot make it current. → rewritten
- missing\_edge (R4.4): Elastic microVM: the container runs as the non-root Hive user (Dockerfile USER swarm) and nothing grants CAP\_NET\_ADMIN, and the spike recorded the SDK's credential channel as iam-role (the metadata service). So the rule cannot be loaded there and replay reports not\_run with the reason; whether the microVM would allow a root nft load is not in the docs and stays unverified. → confirmed
- missing\_edge (R2.1, R1.3): SSH-machine Hives follow the Host by version in ssh.py, which other runs are changing now; this run gives them the features check in dispatch only and leaves their update trigger as it is. → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 9/27 tool calls
- correctness · review r1: read 100% of its prompt · 14/27 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 13/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 2443d3fa8506: 9 pass, 0 fail, 0 not run
- test · swarmai @ 601664d1b94b: 7 pass, 0 fail, 5 not run
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
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- cd backend &amp;&amp; .venv/bin/python -m pytest tests/test\_hive\_build\_identity.py tests/test\_hive\_auto\_update.py tests/test\_hive\_replay\_host.py tests/test\_hive\_package\_publish.py tests/test\_agentcore\_supervi → pass (the design's exit-criteria files X1-X8 on the reviewed tree: 176 passed)
- cd backend &amp;&amp; .venv/bin/python -m pytest tests/test\_feature\_map\_replay\_user.py -q -p no:cacheprovider --timeout=90 --maxfail=1000 → pre\_existing (the 3 tests deselected above fail the same way on the run's base tree: this machine has no setfacl)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pre\_existing (every BVT manifest file passes except the 3 tests of tests/test\_precommit\_uv\_lock.py, which fail the same way on the base tree (no uv on this machine's PATH); run\_bvt.sh stops at them (--maxfail=3), s)
- npx tsc --noEmit → not\_run (the change has no desktop source (only desktop/scripts/build-backend.sh); run as the task's recorded check on the landed branch)
- 4 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · cd backend &amp;&amp; .venv/bin/python -m pytest tests/test\_feature\_map\_replay\_user.py -q -p no:cacheprovider --timeout=90 --maxfail=1000: the 3 tests deselected above fail the same way on the run's base tree: this machine has no setfacl
- swarmai · bash scripts/run\_bvt.sh: every BVT manifest file passes except the 3 tests of tests/test\_precommit\_uv\_lock.py, which fail the same way on the base tree (no uv on this machine's PATH); run\_bvt.sh stops at them (--maxfail=3), so the full list was run with --maxfail=1000 to see the rest

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: the change has no desktop source (only desktop/scripts/build-backend.sh); run as the task's recorded check on the landed branch
- swarmai · npm run lint:contract: no UI string or desktop source changed; run as the task's recorded check on the landed branch
- swarmai · npm run build: no desktop source changed; run as the task's recorded check on the landed branch
- swarmai · npx vitest run --reporter=verbose: no desktop source changed; run as the task's recorded check on the landed branch
- swarmai · cd backend &amp;&amp; .venv/bin/python -m scripts.feature\_map replay --affected --run run\_b3f69e2d: 16 reached walks not\_run for environment reasons on this machine, the very gap this change closes: the installed SwarmAI here reads features schema 1 (the daemon's feature\_map replay refused features.yaml schema 2), and with the worktree's own code the machine has no Chromium system libraries and no

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/build\_identity.py
- swarmai: backend/scripts/build\_identity.py
- swarmai: backend/tests/test\_hive\_build\_identity.py
- swarmai: backend/tests/test\_swarm\_out\_features\_schema.py
