# Fix the two red checks the vendored modern-web-guidance files left on main (XG:… · Audit through DELIVER

Delivered: Fix the two red checks the vendored modern-web-guidance files left on main (XG: "修 modern-web-guidance 导致的扫描和 BVT 失败"). (1) BVT: tests/test\_frontend\_design\_web\_guidance.py::test\_vendored\_guides\_match\_. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 9h 22m wall · 9h 22m active · no baseline yet (3/5 runs) |
| Quality | WARN | 1/1 verification · 1 not run · 1 red at base · 3/6 findings closed (8 reported; within 10 lines of one file they count once) (2 test-closed, confirmed by review) · 2 fail-to-pass · review coverage: security r1 read 52% of its prompt; correctness r1 read 52% of its prompt · +2 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 700k comparable · 600k input · 100k output · 43.1M cache reads, not counted · 43.8M billed · host-session responses in the run window · no baseline yet (3/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 0 rules · 2 watches

Knowledge: requirement recalled · 10 · design recalled · 7 · cited 0 · searched the DDD 0×

## Decisions

- D1 · How narrow is the digest exclusion? → whole-line shapes only: ^\\s\*"sha256":\\s\*"\[0-9a-f\]{64}",?\\s\*$ and an integrity="sha256\|384\|512-&lt;base64 of that length&gt;" or integrity("...") value. Why: the same value under any other key, or a secret beside it in another shape, is still scanned, like validated\_by\_4gate (DESIGN\_SPEC, agent)
- D2 · Where does the guide come from? → raw.githubusercontent.com at commit 64a6d708f5a43f2cd3fde5d4d7db3905ba1bdf65, kept only if its sha256 equals the PROVENANCE.json entry. Why: the provenance file pins both; a mismatched download is refused (DESIGN\_SPEC, agent)
- D3 · The upstream commit pin also reads as a secret: skip it too? → yes, the same way: a JSON "commit" field of exactly 40 lowercase hex on its own line. Why: PROVENANCE.json records one per source, and the packaged apps' pins were hand-kept baseline entries too (BUILD report · swarmai, agent)
- D4 · An integrity value inside a longer line of the vendored guide → not skipped by the rule; baselined as one verified false positive. Why: widening the rule to part of a line would also skip a secret beside it (BUILD report · swarmai, agent)
- D5 · The plan's exit X5 (a test that scans the whole repository) → not written; the repository scan is run as the backend-security-scan check. Why: a full scan inside the test suite takes as long as the check itself and adds nothing it does not show (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Exclude the whole vendored modern-web-guidance directory from the secrets scan. Not taken: it drops every other line of 211 files from the scan, and the packaged apps' PROVENANCE.json digests would still need hand-kept baseline entries
- A3 · Regenerate the baseline to accept the 214 digests. Not taken: every provenance refresh then adds new entries by hand, the treadmill the apps\_index exclusion was made to end
- A4 · Anchor the planning-note patterns to the repository root. Not taken: a session that writes its planning notes in backend/ or desktop/ would then commit them; a negation scoped to skill data trees keeps that protection

Requirements analysis:

- missing\_edge (R2.3): the exclusion is by whole-line shape, so a secret pasted onto a digest line in another form is still scanned → rewritten
- missing\_edge (R1.2): the ignore rule was written for root planning notes; narrowing it must keep that → rewritten
- missing\_edge (R2.4): the apps' PROVENANCE.json digests are in the baseline today; regenerating drops those entries, which must be the only removals → rewritten
- missing\_edge (R1.1): the file is absent on disk too: it is fetched from upstream at the pinned commit and its sha256 checked against PROVENANCE.json before it is committed (Gate-0) → rewritten
- missing\_edge (R1.2, R1.3): task\_plan.md and findings.md have the same unanchored shape (Gate-0); the planning-note patterns stay ignored everywhere and are un-ignored only inside skill data trees, rather than anchored to the root, so notes a session writes in backend/ or desktop/ stay out of git → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 5/26 tool calls
- security · review r1: read 100% of its prompt · 5/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 52% of its prompt · 13/27 tool calls
- correctness · review r1: read 52% of its prompt · 13/27 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 6f2dc4e6b7fa: 4 pass, 0 fail, 0 not run
- test · swarmai @ 463b8a11d8f3: 4 pass, 0 fail, 1 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest tests/test\_frontend\_design\_web\_guidance.py tests/test\_security\_scan.py tests/test\_app\_provenance.py tests/test\_app\_reg → pass (63 passed, including the provenance test that was red on main and the App index freshness test)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/security\_scan.py → pass (PASS on the tree rebased onto 548c09cba, baseline regenerated with the new exclusions)
- bash scripts/run\_bvt.sh → pre\_existing (909 passed, 1 failed: test\_eval\_fresh\_install (the CI\_IMPACT probe times out at 20 s on a fresh install; red on main the same way, out of this run's scope). 55 files in three parts with the repo venv,)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass (all modules imported)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/lint\_problem\_values.py → pass (clean)
- npx vitest run --reporter=verbose → not\_run (no desktop file changed)

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 909 passed, 1 failed: test\_eval\_fresh\_install (the CI\_IMPACT probe times out at 20 s on a fresh install; red on main the same way, out of this run's scope). 55 files in three parts with the repo venv, on the rebased tree

## Not run (the agent's reason, not checked)

- swarmai · npx vitest run --reporter=verbose: no desktop file changed
