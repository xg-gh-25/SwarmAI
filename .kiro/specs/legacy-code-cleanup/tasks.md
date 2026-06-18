# Implementation Plan: Legacy Code Cleanup (no-risk path)

## Overview

This plan executes ONLY the confirmed, leaf, zero-risk removals from the
design's Per-Candidate Inventory Report, plus the small set of property-based
tests that directly guard those removals (Properties 9–13). It deliberately
excludes every Upgrade_Path_Candidate, every reclassified-RETAIN item, the
reference pattern, and any Regression-Prone-Area-adjacent risky removal — see
the "Out of scope this cycle" note.

Implementation languages follow the design: backend = Python + Hypothesis,
frontend = TypeScript + fast-check. No pseudocode is used, so no language
selection is required.

Each removal is one bisectable `Removal_Commit` (R10.1), recorded only in a
green state (R10.3), ending with `Co-Authored-By: Swarm <swarm@swarmai.dev>`
(R10.5). Every removal embeds the design's verification gate as sub-steps
before deletion: call-site search incl. tests using the `symbol(` pattern
(R4.2, R4.6), Code_Intel cross-check or written justification (R4.3/R4.4), and
reclassify-and-skip if any active reference is found (R4.5). All file writes use
`fsWrite`/`fsAppend` in small chunks; edits prefer `strReplace`.

## Tasks

- [ ] 1. Verification-gate & invariant safety guards
  - [ ] 1.1 Implement `backend/scripts/cleanup/safety_guards.py` pure helpers (evaluate_verification_gate, reclassify_on_active_refs, alters_platform_invariant, has_textual_safety_violation); module docstring (R11.5); inclusive language (R11.6) — _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.6, 5.2, 5.3, 8.1_
  - [ ]* 1.2 Property test — Property 9 (Removal requires complete evidence and zero active call sites); Hypothesis ≥100 iters; tag `# Feature: legacy-code-cleanup, Property 9` — Validates 4.1, 4.2
  - [ ]* 1.3 Property test — Property 10 (Code_Intel gate requires report-dead or written justification); tag Property 10 — Validates 4.3, 4.4
  - [ ]* 1.4 Property test — Property 11 (Active references exclude and reclassify); tag Property 11 — Validates 4.5
  - [ ]* 1.5 Property test — Property 12 (Invariant-altering removals excluded with no edit); tag Property 12 — Validates 5.1, 5.6
  - [ ]* 1.6 Property test — Property 13 (Modified-file textual safety invariants: no module-level import fcntl, no lsof, no noUnusedLocals suppression); Hypothesis/fast-check ≥100 iters; tag Property 13 — Validates 5.2, 5.3, 8.1

- [ ] 2. Backend leaf removal — stale `# install_launchd removed …` comment at `backend/jobs/scheduler.py:726` (row 11, CC, Dead_Candidate)
  - call-site gate `grep -rn "install_launchd(" backend/ desktop/ --include="*.py"` incl tests (R4.2,R4.6); written justification that dead-code report doesn't apply to a comment (R4.4); reclassify-and-skip on any active ref (R4.5); remove the single comment line, keep docstring (R11.5); targeted `pytest tests/test_scheduler.py -v --timeout=60` never full suite (R9.1,R9.2); CHANGELOG entry (R11.1); one green-state bisectable Removal_Commit, Swarm trailer (R10.1,R10.3,R10.5); inclusive language (R11.6)
  - _Requirements: 2.1, 4.2, 4.4, 4.6, 9.1, 9.2, 10.1, 10.3, 10.5, 11.1, 11.6_

- [ ] 3. Backend leaf removal — `backend/jobs/com.swarmai.scheduler.plist` template file (row 9, DM, Dead_Candidate)
  - call-site gate `grep -rn "TEMPLATE\|com.swarmai.scheduler.plist" backend/ desktop/` incl tests (R4.2,R4.6); confirm only the deprecated install path references it and install() no longer installs; reclassify-and-skip if any writer reads TEMPLATE (R4.5); written justification (file not symbol, R4.4); obsolete-test check on test_backend_daemon.py/test_daemon_first.py — only edit IN THIS COMMIT if deletion breaks an assertion (R7.1); delete the file; targeted tests --timeout=60 (R9.1,R9.2); CHANGELOG (R11.1); green-state bisectable commit, Swarm trailer; inclusive language
  - _Requirements: 2.1, 4.2, 4.4, 4.5, 4.6, 7.1, 9.1, 9.2, 10.1, 10.3, 10.5, 11.1, 11.6_

- [ ] 4. Checkpoint — backend leaves: ensure targeted backend tests pass, ask user if questions arise.

- [ ] 5. Frontend re-export removal — `MAX_OPEN_TABS` @deprecated alias + re-export (row 15, UE, Dead_Candidate)
  - [ ] 5.1 Migrate the deprecated test import off `MAX_OPEN_TABS` to `MAX_TABS_HARD_CEILING`/`fetchMaxTabs()`; call-site gate `grep -rn "MAX_OPEN_TABS" desktop/src` incl tests (R4.2,R4.6); reclassify-and-skip if any non-test consumer relies on it (R4.5) — _Requirements: 4.2, 4.5, 4.6_
  - [ ] 5.2 In the SAME commit delete the `@deprecated` alias in `useUnifiedTabState.ts` and the re-export in `useChatStreamingLifecycle.ts` (R8.2); remove now-unreferenced imports, no noUnusedLocals suppression (R8.1); multi-tab Regression-Prone-Area baseline identical pass/fail (R5.4,R5.5); tsc+eslint zero errors (R8.3,R8.4); `npm test -- --run` affected; CHANGELOG (R11.1); green-state bisectable commit, Swarm trailer — _Requirements: 4.5, 5.4, 5.5, 8.1, 8.2, 8.3, 8.4, 10.1, 10.3, 10.5, 11.1, 11.6_

- [ ] 6. Frontend re-export removal — `deriveStreamingActivity` backward-compat re-export in `ChatPage.tsx` (row 17, UE, Dead_Candidate)
  - [ ] 6.1 Migrate test imports of `deriveStreamingActivity` to the canonical source; call-site gate incl tests (R4.2,R4.6); reclassify-and-skip if non-test consumer depends on it (R4.5) — _Requirements: 4.2, 4.5, 4.6_
  - [ ] 6.2 In the SAME commit delete the re-export from `ChatPage.tsx` (R8.2); remove now-unreferenced imports, no suppression (R8.1); ChatPage is multi-tab Regression-Prone-Area — identical targeted-test baseline (R5.4,R5.5); tsc+eslint zero (R8.3,R8.4); `npm test -- --run`; CHANGELOG (R11.1); green-state bisectable commit, Swarm trailer — _Requirements: 4.5, 5.4, 5.5, 8.1, 8.2, 8.3, 8.4, 10.1, 10.3, 10.5, 11.1, 11.6_

- [ ] 7. Frontend gated component removal — `@deprecated` `FileTreeNode` COMPONENT only (row 13, DM); `FileTreeItem` TYPE and `toFileTreeItem.ts` STAY
  - gate on zero JSX usage: `grep -rn "<FileTreeNode" desktop/src` vs `grep -rn "FileTreeItem" desktop/src` incl tests; record evidence; reclassify-and-skip if any JSX usage (R4.5); remove only the component, preserve the type export + converter; remove now-unreferenced imports, no suppression (R8.1); tsc+eslint zero (R8.3,R8.4); `npm test -- --run`; CHANGELOG (R11.1); green-state bisectable commit, Swarm trailer
  - _Requirements: 3.5, 4.1, 4.2, 4.5, 8.1, 8.3, 8.4, 10.1, 10.3, 10.5, 11.1_

- [ ] 8. Final checkpoint — no-risk path complete: ensure all targeted tests pass, ask user if questions arise.

## Notes

- `*`-marked tasks (1.2–1.6) are optional property tests; core removal tasks are never optional.
- Each removal = its own bisectable Removal_Commit, green-state only, `Co-Authored-By: Swarm <swarm@swarmai.dev>`, never a Claude/Anthropic identity.
- Every removal bakes in the R4 verification gate; "active reference found → reclassify to retain and skip" is explicit.
- Backend verify = targeted `pytest tests/test_<module>.py -v --timeout=60`; full suite NEVER run proactively (R9.1, R9.2). Frontend verify = tsc+eslint zero + `npm test -- --run` affected.
- Frontend re-export removals (tasks 5, 6) migrate test imports and delete the re-export in the SAME commit (R8.2), no `noUnusedLocals` suppression (R8.1).
- Documentation kept lightweight: per-removal CHANGELOG entries (R11.1) + module-docstring compliance (R11.5). The anti-pattern-list → design-invariants replacement (row 20, R11.3/R11.4) is DEFERRED as out-of-scope follow-up.
- Out of scope this cycle (no tasks): all Upgrade_Path rows (1–6, 10), reclassified-retain rows (7, 8, 14, 16, 18, 19), the evolution_maintenance_hook reference (row 12), and row 20. P19 (toCamelCase bijection) omitted — no in-scope removal touches a toCamelCase mapping.
- All files written with fsWrite/fsAppend small chunks; edits prefer strReplace; inclusive language only (R11.6).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "1.5", "1.6"] },
    { "id": 2, "tasks": ["2"] },
    { "id": 3, "tasks": ["3"] },
    { "id": 4, "tasks": ["4"] },
    { "id": 5, "tasks": ["5.1"] },
    { "id": 6, "tasks": ["5.2", "6.1"] },
    { "id": 7, "tasks": ["6.2"] },
    { "id": 8, "tasks": ["7"] },
    { "id": 9, "tasks": ["8"] }
  ]
}
```
