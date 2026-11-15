# # Pipeline v1: five changes that measured runs show waste a round or lower qual… · Audit through DELIVER

Delivered: # Pipeline v1: five changes that measured runs show waste a round or lower quality \*\*Evidence (event logs and transcripts of landed runs, 2026-11-15).\*\* 1. A fix's \`reported\_test\` proof often fails R2. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 2h 5m wall · 2h 5m active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 4 not run · 4 red at base · 11/11 findings closed (5 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 1.1M comparable · 869k input · 233k output · 72.6M cache reads, not counted · 73.7M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 3 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · Which expert judges a cluster in R2? → the expert of the cluster's highest-severity finding; ties by the expert with most findings in the cluster, then expert name. Why: deterministic, and the most severe claim is judged by the expert who raised it (DESIGN\_SPEC, agent)
- D2 · What does a missing fail-to-pass test do to the audit verdict? → one Quality open line and a WARN, never a BLOCK or a refused stage. Why: the request says missing is one line and never a block; WARN matches how an unrun command is treated (DESIGN\_SPEC, agent)
- D3 · Is an absence finding's reported\_test proof required to carry a mutation result? → no: optional for absence, required (result or reason) for behavior and contract. Why: the request names behaviour/contract fixes; an absence fix is often the added test itself (DESIGN\_SPEC, agent)
- D4 · Does a docs-only first review get correctness? → yes: correctness for every change with at least one changed file. Why: the request says always, whatever its size; an empty change still has no reviewer (DESIGN\_SPEC, agent)
- D5 · How are findings counted? → findings\_total = clusters over all findings, findings\_closed = clusters whose every finding is closed, findings\_reported = findings; findings\_confirmed stays a finding count. Why: duplicates count once and a cluster is closed only when nothing in it is open (DESIGN\_SPEC, agent)
- D6 · The REVIEW and BUILD cards would exceed the 5000-character stage-card limit with the new text: how to fit? → tighten the new sentences and a few adjacent phrases; and remove the 'fix once' prescription phrase in the REVIEW sentence I was editing (test\_pipeline\_v1\_no\_prescription was red on the base for it). Why: the limit is a ratchet that may not be raised; the phrase sat in the sentence being rewritten (BUILD report · swarmai, agent)
- D7 · How do existing e2e tests that relied on 'a small change dispatches no expert' keep their purpose? → answer the correctness handout through a shared test helper (answer\_review) or the rig, meter the rig's expert where a test is about the main transcript (Rig.METER\_EXPERTS), and complete REVIEW without an answer where a test is about the agent-only wording. Why: each test keeps what it tests; none is deleted (BUILD report · swarmai, agent)
- D10 · Should the audit's expert rows always carry a reason key? → only when the handout recorded one. Why: keeps the row shape older readers and the conformance test expect; the monitor reads it when present (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Have the pipeline run mutation\_check and the base-tree test itself, recording the outcomes as engine-observed facts. Not taken: S6-1 keeps the pipeline from running or watching tests (the agent tests with the repo's own commands; review judges the report); running user test commands from the engine adds a process and timeout surface for no new fact the agent's report cannot carry.
- A3 · Route R2 by cluster by merging duplicate findings into one canonical finding at fold time. Not taken: It loses each expert's own text and kind (the closure evidence differs by kind) and rewrites canonical ids that events, auths and REPORTs already reference; grouping at counting and routing keeps every finding whole.
- A4 · Attach the \`template design\_spec\` output to \`next\` when PLAN opens instead of stating the shapes on the card. Not taken: The template is already one command away and the measured refusals happened anyway: the card is what the agent reads first, so the rules belong there; a test ties each stated example to the validator.

Requirements analysis:

- missing\_edge (R1.1, R1.2): mutation\_check paths are relative to its --cwd (often backend/), not the repo root: the changed-file check matches a mutated path to a changed path on a '/' boundary suffix, in either direction → rewritten
- missing\_edge (R1.4): absence findings are closed by reported\_test too; the request names behaviour/contract fixes only, so an absence proof may carry a mutation result or reason but is not refused without one → rewritten
- missing\_edge (R3.3): logs written before this change carry R2 status lines from the finding's own expert; the fold keeps accepting those, so every existing run still folds → rewritten
- missing\_edge (R5.1): a docs-only change: 'whatever its size' is read as any non-empty change, so a docs-only first review also gets correctness; the incremental review already always lists correctness → rewritten
- missing\_edge (R2.3): the missing fail-to-pass line makes Quality WARN like an unrun command, never BLOCK; late stages stay lenient → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 7/26 tool calls
- correctness · review r1: read 100% of its prompt · 17/26 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 5f5ec09ae5a2: 5 pass, 0 fail, 0 not run
- test · swarmai @ d19e8762620d: 13 pass, 0 fail, 4 not run
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

TEST · swarmai:
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_run\_protocol\_\*.py tests/test\_doc\_paths.py tests/test\_expert\_merge.py tests/tes → pass
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_agent\_mode.py tests/test\_pipeline\_attention.py tests/test\_pipeline\_co → pass
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_command.py::test\_no\_other\_module\_matches\_pipeline\_command\_text -q → pre\_existing (fails the same way on the run's base (pipeline-v1 base-tree), unrelated to this change: core/swarm\_run\_card.py's child-dispatch regex is flagged as pipeline command text)
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_v1\_acceptance\_matrix.py tests/test\_pipeline\_v1\_api.py tests/test\_pipe → pass
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_v1\_deliver\_reopen.py tests/test\_pipeline\_v1\_diff\_assertion\_by\_file.py → pass
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_v1\_findings\_command.py tests/test\_pipeline\_v1\_follow\_next.py tests/te → pass
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_v1\_base\_moved.py tests/test\_pipeline\_v1\_deliver\_reopen.py tests/test\_ → pre\_existing (fails the same way on the run's base (pipeline-v1 base-tree), unrelated to this change: every flow where main moves under the run in DELIVER ends with LG-1 refusing the land on this host (11 tests))
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_v1\_local\_log.py tests/test\_pipeline\_v1\_measured\_fixes.py tests/test\_p → pass
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_v1\_local\_log.py::test\_an\_adopted\_prewarm\_session\_creates\_a\_run\_it\_own → pre\_existing (fails the same way on the run's base (pipeline-v1 base-tree), unrelated to this change: it expects SWARM\_APP\_SESSION\_ID in create's output)
- GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_v1\_research\_deliver.py tests/test\_pipeline\_v1\_retro\_fixes.py tests/te → pass
- 11 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_command.py::test\_no\_other\_module\_matches\_pipeline\_command\_text -q: fails the same way on the run's base (pipeline-v1 base-tree), unrelated to this change: core/swarm\_run\_card.py's child-dispatch regex is flagged as pipeline command text
- swarmai · GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_v1\_base\_moved.py tests/test\_pipeline\_v1\_deliver\_reopen.py tests/test\_: fails the same way on the run's base (pipeline-v1 base-tree), unrelated to this change: every flow where main moves under the run in DELIVER ends with LG-1 refusing the land on this host (11 tests)
- swarmai · GIT\_CONFIG\_GLOBAL=/tmp/so\_gitconfig SWARMAI\_SUITE=1 perl -e 'alarm 590; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_v1\_local\_log.py::test\_an\_adopted\_prewarm\_session\_creates\_a\_run\_it\_own: fails the same way on the run's base (pipeline-v1 base-tree), unrelated to this change: it expects SWARM\_APP\_SESSION\_ID in create's output
- swarmai · bash scripts/run\_bvt.sh: fails the same way on the run's base (pipeline-v1 base-tree), unrelated to this change: only tests/test\_precommit\_uv\_lock.py's 3 tests fail (a None path: no uv on PATH here); the other BVT files pass

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; run through task\_checks after landing
- swarmai · npm run lint:contract: no desktop file changed; run through task\_checks after landing
- swarmai · npm run build: no desktop file changed; run through task\_checks after landing
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run through task\_checks after landing

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_pipeline\_v1\_measured\_fixes.py
