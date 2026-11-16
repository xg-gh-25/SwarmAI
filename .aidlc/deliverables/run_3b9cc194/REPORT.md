# # Fix: a listed file the agent deleted keeps a finished Hive run from ever sett… · Audit through DELIVER

Delivered: # Fix: a listed file the agent deleted keeps a finished Hive run from ever settling \*\*Evidence (2026-11-16).\*\* Two accepted research runs (so\_124822037fb2, so\_96b327f69f47) show \`settle.why\_not == \["f. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 25m 24s wall · 25m 24s active · no baseline yet (4/5 runs) |
| Quality | WARN | 1/1 verification · 3 not run · 3/7 findings closed (3 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 588k comparable · 461k input · 127k output · 14.0M cache reads, not counted · 14.6M billed · host-session responses in the run window · no baseline yet (4/5 runs) |

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 2 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · Which reasons release the hold? → only \`gone\` (ENOENT on the file or a directory on its way). Why: a link, a non-regular file, an over-size or secret file, or one under Hive/ or outside may still hold bytes only the Hive has, or is not provably absent; ELOOP/ENOTDIR are \`link\`, never \`gone\` (DESIGN\_SPEC, agent)
- D2 · A listed path reported gone that came back in another turn as a \`file\` entry → its placement decides; the gone report is ignored. Why: its bytes are in this host's record until placed; settling then could stop the retries (DESIGN\_SPEC, agent)
- D3 · Key name → \`not\_sealed\` (Gate-0: \`unsealed\` already names a repository state in settle). Why: no clash with \`\_code\`'s \`unsealed\` (DESIGN\_SPEC, agent)
- D4 · Cap of the list → at most 200 reports per turn (the turn\_deliverables cap), each \`listed\` at most LISTED\_MAX; kept whole in the turn body (rows are trimmed first). Why: bounded well under MAX\_BYTES\['turn'\] (DESIGN\_SPEC, agent)
- D5 · Keep the name seal\_files? → seal\_files now seals what stage\_files read; seal\_turn calls both around the turn entry. Why: the reasons have to be known before the turn entry, and the files sealed after it (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A new manifest entry kind (e.g. \`unsealed\`) per file not sealed. Not taken: an older host rejects an unknown kind (\`\_valid\_entry\` -&gt; unknown\_kind) and stops the pull at that seq for good, losing the lessons and close after it
- A3 · Reasons as a key of the turn entry's manifest line. Not taken: a listing page holds up to 100 lines in LISTING\_MAX (256 KB); up to 200 paths of 1000 characters per line can push a page past it, and an unreadable listing stops every pull of the run
- A4 · Seal the files before the turn entry so their reasons are known when the turn is written. Not taken: a kill between the file entries and the turn entry makes the next turn slice the same rows and seal the files twice; staging keeps the turn-then-files order and the single read
- A5 · Read the reasons in \`\_files\_here\` from the result summary only. Not taken: a summary is a display copy written by version (IMPROVEMENT pitfall 6989fdb2): settle reads the record kept here, as it does for placement

Requirements analysis:

- missing\_edge (R1.1): seal\_files runs after the turn entry is appended, so the reasons cannot be in that entry unless the files are read first; each file is read once into a temporary file of the record before the turn entry and sealed from it after, so a reason and a seal come from the same read and the order turn-then-files stays → rewritten
- missing\_edge (R2.3): an older host pulling from a newer Hive must not break: a new entry kind or a larger manifest line would stop its pull; the \`not\_sealed\` key in a turn body is ignored by older readers → rewritten
- missing\_edge (R2.1): a later turn may report a different reason for the same listed path; the newest report wins → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/26 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 13/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 3002e243f68a: 6 pass, 0 fail, 0 not run
- test · swarmai @ 903f274f5e34: 10 pass, 0 fail, 3 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_3b9cc194607c/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_hive\_files.py tests/test\_hive\_records.py tests/test\_ → pass (324 passed on the reviewed tree (hive\_files, hive\_records, settle, actor, results, swarm\_run\_children))
- python scripts/smoke\_import.py → pass (exit 0)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (exit 0)
- python scripts/lint\_skills.py → pass (exit 0)
- python scripts/lint\_experts.py → pass (exit 0)
- bash scripts/sync-version.sh check → pass (exit 0)
- npx tsc --noEmit → pass (exit 0)
- npm run lint:contract → pass (exit 0)
- perl -e 'alarm 110; exec @ARGV' npx vitest run src/components/swarm-out/runResult.test.tsx → pass (35 passed, the reason test among them)
- 3 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npm run build: run once through scripts.task\_checks on the task's branch after landing (the recorded check)
- swarmai · bash scripts/run\_bvt.sh: run once through scripts.task\_checks on the task's branch after landing (the recorded check)
- swarmai · npx vitest run --reporter=verbose: the full frontend suite runs once through scripts.task\_checks after landing (the recorded check)
