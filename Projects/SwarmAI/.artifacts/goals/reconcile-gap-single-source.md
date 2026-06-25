# Goal: Structurally eliminate reconcile-gap via single render source

## Definition of Done
- [ ] AC1: TabView renders from ONE source (store subscription); messagesProp removed from render (`grep -c "storeMessages.*messagesProp" TabView.tsx` == 0)
- [ ] AC2 (REFINED cycle 1): tab-switch has no UNCONDITIONAL reverse-flow clobber. BUILD discovered :721 also handles cold-restore (module store doesn't survive process restart). Refined: the unconditional `store.replace(tabState.messages)` is replaced by an empty-store-only guard (`if store.messages.length===0 && tabState.messages.length>0`). Store is authority; we POPULATE empty, never CLOBBER populated. Verify: grep shows the call is guarded, not unconditional.
- [ ] AC3: tabState.messages render-free (tsc --noEmit exits 0)
- [ ] AC4: store + tab tests green incl new state-transition coverage (`npm test src/stores src/pages/chat` exits 0)
- [ ] AC5 (rubric): adversarial verifies 4 guards + 2 risks + isolation/race clean (0 unresolved HIGH/CRITICAL)

## Configuration
- Max cycles: 6
- Review cadence: every 2 cycles
- Cycle scope: one of 3 scope items per cycle (TabView single-source / ChatPage reverse-flow / tests)
- Start commit: 8bb4c446
- Last review commit: 8bb4c446

## Metrics
| Cycle | Date | Metric | Delta | Action |
|-------|------|--------|-------|--------|
| 1 | 2026-06-25 | 0/5 → 4/5 DoD | +0.8 | AC1 store-only selector + AC2 guarded seed (no unconditional clobber) + 3 RED→GREEN tests; 71 regression tests pass; tsc clean |
| 2 | 2026-06-25 | 4/5 → 5/5 DoD | +0.2 | Periodic REVIEW (603 tests clean) + ADVERSARIAL (Agent spawn): core fix holds (A3 store-superset, isolation, race all verified). 1 MEDIUM (false cold-restore rationale — I inferred persistence carries messages; it does NOT, hydrateTab sets []) + 2 LOW (stale comment, probe cry-wolf on bg tabs) all fixed + re-verified. 74 tests green, tsc clean |

## Current State
- Next target: AC5 (adversarial isolation/race verification) — Cycle 2 + periodic REVIEW
- Done: AC1 (grep==0), AC2 (guarded), AC3 (tsc 0), AC4 (3 new tests green, probe retained)

## Blockers
(none)

## Cycle Log
**Cycle 1:** Risk 1 (all-or-nothing) handled by doing AC1+AC2 together. KEY DISCOVERY: :721 not purely redundant — it seeds cold-restore (module store dies on process restart, tabState.messages persists). Naive removal would WelcomeScreen-flash on restart. Surgical fix = empty-store-only guard: invert semantics so store is authority (populate empty, never clobber populated). RED test 2 (empty-store-no-prop-fallback) reproduced the split-brain in a unit test for the FIRST time — prior 33 fixes never had this test. → Cycle 2: adversarial must verify the guard doesn't reintroduce reverse-flow under any interleaving.
