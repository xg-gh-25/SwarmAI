# Goal: Eval 物理解耦 — Projects/SwarmAI/ → SwarmWS/Eval/

## Definition of Done
- [x] DoD1: SwarmWS/Eval/ exists + public golden_set (met C2)
- [x] DoD2: old location cleared (met C2)
- [x] DoD3: path constants changed, 0 stale prod refs (met C2)
- [x] DoD4: 144+ eval tests green + live E2E (met C2 — 153 green, 161 cases load from Eval/)
- [x] DoD5: gitignore two-repo (private ignored, public tracked) (met C1)

## Configuration
- Profile: goal | Start commit: f994ab60
- Cycles: 2 (C1 code, C2 data+test+cleanup)

## Metrics
| Cycle | Action | Result |
|-------|--------|--------|
| 1 | 7 prod/hardening + 3 test fixtures → Eval/; gitignore both repos | 90 pass, 4 skip (data not moved) |
| 2 | git mv data; fix 6 test fixtures; docstring/HTML; dead-code; skill docs | 153 green, 161 E2E |

## Cycle Log
**Cycle 1:** Gate-1 BLOCK caught 5 findings (2 missed prod readers, marker landmine, private-mv, race) — all adopted before any code. Marker→Eval/golden_set.yaml truly decouples.
**Cycle 2:** migration surfaced 9 stale test-fixture paths across 6 files (incremental discovery). Gate-2 PASS, 0 blocking; fixed 2 stale skill docs + 1 dead candidate.

## Adversarial (Gate 2)
PASS, 0 blocking. Read/write symmetric on Eval/, gitignore airtight, digest binds. 2 non-blocking (skill docs, dead candidate) fixed in 396f4acc.

## Pre-existing (out of scope)
2 runtime_health AC6 tests fail on SessionUnit._recycle_kill_pending AttributeError — parallel-session recovery bug, zero eval-path dependency.
