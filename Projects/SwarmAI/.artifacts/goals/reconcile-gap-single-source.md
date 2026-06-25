# Goal: Structurally eliminate reconcile-gap via single render source

## Definition of Done
- [ ] AC1: TabView renders from ONE source (store subscription); messagesProp removed from render (`grep -c "storeMessages.*messagesProp" TabView.tsx` == 0)
- [ ] AC2: tab-switch no reverse-flow (`grep "replace(tabState.messages)" ChatPage.tsx` non-comment == 0)
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

## Current State
- Next target: AC1 — TabView store-only selector + AC2 ChatPage reverse-flow (coherent — Risk 1 all-or-nothing)
- Lowest-hanging fruit: both render-source changes together (small diff, must be atomic)

## Blockers
(none)

## Cycle Log
