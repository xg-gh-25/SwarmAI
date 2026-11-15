# # Fix: files a Hive run writes are lost when a turn writes more than 12 · Audit through DELIVER

Delivered: # Fix: files a Hive run writes are lost when a turn writes more than 12 \*\*Evidence (2026-11-15).\*\* A research run on a Hive wrote 14 files: 13 scratch files and its report. Only 12 came back to the ho. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 18m 34s wall · 18m 34s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 5 not run · 4/4 findings closed (2 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 370k comparable · 298k input · 71k output · 11.7M cache reads, not counted · 12.0M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Knowledge: requirement recalled · 10 · design recalled · 7 · cited 0 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · How large is the safety limit? → 200 files per turn. Why: it matches core/canvas\_session.MAX\_FILES (200 per session) and stays under session\_outputs.MAX\_ROWS (300) and hive\_records.MAX\_KEPT\_ENTRIES (2000); 200 entries of path + absolutePath stay far inside the 1 MiB turn entry and the 1 MiB SSE line (DESIGN\_SPEC, agent)
- D2 · Does the 12-row display limit apply before or after the end card filters out stage and run documents? → after the filter, in OutputCard. Why: the card shows up to 12 of what it actually lists; the title still counts every deliverable (DESIGN\_SPEC, agent)
- D3 · Raise core/swarm\_out\_results.MAX\_DELIVERABLES (50 per session) too? → no. Why: it bounds the run card's list, not what is sealed and placed; every file still comes back to the Brain and the card says how many it does not list. Smallest change (DESIGN\_SPEC, agent)
- D4 · What to do with the existing router test that asserted 12 of 20 files? → rewrite it to the new contract: 20 of 20 listed, and past the safety limit 200 listed with omitted 1. Why: it pinned the behaviour this fix removes; the cap it guards still exists at 200 (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Two lists: keep the 12-file block for the card and write a second, full list (a new block or field) that core/hive\_files seals. Not taken: two lists of the same turn's files can disagree, every reader has to pick one, and the block is already the per-turn list every reader uses; the task asks for one source of truth.
- A3 · core/hive\_files.seal\_files reads the files from core/session\_outputs instead of the block. Not taken: session\_outputs is per session, not per turn, and is written on another pool; mapping it back to a turn adds a second source and an ordering race.

Requirements analysis:

- missing\_edge (R1.3): summarize keeps at most MAX\_DELIVERABLES (50) files per session in work.files and counts the rest in files\_omitted; a turn with 50 to 200 files still seals and places every file on the host, only the run card lists 50. That limit is a card bound, not a return bound, and is left as is. → confirmed
- missing\_edge (R2.1): the end card filters deliverables (stage documents, run documents) before showing them; the 12-row limit applies after that filter, so a turn never shows fewer than 12 rows because of hidden documents. → rewritten
- missing\_edge (R1.1): core/hive\_records.turn\_projection copies the block into the turn entry with string fields capped, list length untouched; 200 entries stay well inside the 1 MiB turn entry. → confirmed
- missing\_edge (U4, U5): core/swarm\_out\_remote does not forward a turn\_deliverables event to the host tab (local paths); the assistant row that carries the block stays far below its 1 MiB line limit at 200 files. core/turn\_products, core/session\_unit and core/transcript\_order only name the block in comments or order its write; they do not read its files. → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 12/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 9af384e00f4a: 5 pass, 0 fail, 0 not run
- test · swarmai @ 097a3beb234b: 8 pass, 0 fail, 5 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c1a12344165f/repos/swarmai/backend/.venv/bin/python -m pytest -q --timeout=60 --maxfail=1000 -p no:cacheprovider tests/test\_turn\_changes.py::test\_deliverable → pass (147 tests)
- npx vitest run src/pages/chat/components/\_\_tests\_\_/AssistantMessageView.products.test.tsx src/pages/chat/turnView.test.ts → pass (187 tests)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c1a12344165f/repos/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c1a12344165f/repos/swarmai/backend/.venv/bin/python scripts/lint\_problem\_values.py → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c1a12344165f/repos/swarmai/backend/.venv/bin/python scripts/security\_scan.py → pass
- bash scripts/sync-version.sh check → pass
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c1a12344165f/repos/swarmai/backend/.venv/bin/python scripts/lint\_skills.py → not\_run (the change touches no skill file)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c1a12344165f/repos/swarmai/backend/.venv/bin/python scripts/lint\_experts.py → not\_run (the change touches no expert file)
- 3 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c1a12344165f/repos/swarmai/backend/.venv/bin/python scripts/lint\_skills.py: the change touches no skill file
- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c1a12344165f/repos/swarmai/backend/.venv/bin/python scripts/lint\_experts.py: the change touches no expert file
- swarmai · npm run build: run once through scripts.task\_checks on the landed branch, where its exit code is recorded for the task card
- swarmai · bash scripts/run\_bvt.sh: run once through scripts.task\_checks on the landed branch, where its exit code is recorded for the task card
- swarmai · npx vitest run --reporter=verbose: run once through scripts.task\_checks on the landed branch, where its exit code is recorded for the task card
