# Post-Milestone Cleanup Plan — Chat E2E Milestone

**Date**: 2026-03-01  
**Milestone**: Chat experience end-to-end enabled (streaming lifecycle bugfix complete)  
**Goal**: Remove dead code, legacy artifacts, and structural debt before next feature wave

---

## Priority 1: Legacy ContextFiles System (Full Removal)

**Status**: Legacy — replaced by `ContextAssembler` + `ContextSnapshotCache` 8-layer pipeline  
**Risk**: Low (only production caller is a fallback path in `agent_manager.py`)  
**Effort**: ~2 hours

The `ContextFiles/` directory and its entire supporting system is a pre-redesign artifact. The new design uses root-level `context-L0.md` / `context-L1.md` files and the 8-layer `ContextAssembler`. The old system reads/writes `ContextFiles/context.md` and `ContextFiles/compressed-context.md`.

### Files to delete:
| File | Lines | Reason |
|------|-------|--------|
| `backend/core/context_manager.py` | 388 | Entire legacy module — reads/writes ContextFiles/ |
| `backend/tests/test_context_manager.py` | ~270 | Tests for deleted module |
| `backend/tests/test_property_context_file.py` | ~200 | Property tests for deleted module |

### Code to modify:
| File | Change |
|------|--------|
| `backend/core/agent_manager.py` | Remove `ContextManager` import and fallback path (lines ~769-780). When no `project_id`, either skip context injection or use `ContextAssembler` with workspace-level context |
| `backend/core/swarm_workspace_manager.py` | Remove `read_context_files()` method (~50 lines). Remove `ContextFiles` from `SAMPLE_PROJECT_INSTRUCTIONS` template text |
| `backend/routers/workspace_config.py` | Remove 3 endpoints: `GET /{id}/context`, `PUT /{id}/context`, `POST /{id}/context/compress`. Remove `context_manager` import |
| `desktop/src/services/workspaceConfig.ts` | Remove `getContext()`, `updateContext()`, `compressContext()` methods |
| `desktop/src/services/__tests__/workspaceConfig.test.ts` | Remove context endpoint tests |
| `backend/tests/test_wiring_integration.py` | Remove `ContextManager` import and tests that create `ContextFiles/` dirs |
| `backend/tests/test_swarm_workspace_manager.py` | Remove entire `TestReadContextFiles` class |
| `backend/tests/test_property_folder_structure.py` | Update docstring (references Artifacts/, ContextFiles/, Transcripts/) |
| `backend/tests/test_property_workspace_folders.py` | Update docstring (references Artifacts/, ContextFiles/, Transcripts/) |

### Filesystem:
- Delete `~/.swarm-ai/SwarmWS/ContextFiles/` directory from disk

### Steering rule update:
- Update `swarmai-dev-rules.md` Storage Model: remove `ContextFiles/` from Filesystem line

---

## Priority 2: Dead Frontend Hooks (6 hooks + tests)

**Status**: Orphaned from earlier refactoring phases — no imports anywhere  
**Risk**: None  
**Effort**: ~30 minutes

| Hook | Lines | Confirmed Dead |
|------|-------|----------------|
| `desktop/src/hooks/useArchiveGuard.ts` | ~50 | No imports found |
| `desktop/src/hooks/useChatSession.ts` | ~120 | Only barrel export, no consumers (replaced by `useChatStreamingLifecycle`) |
| `desktop/src/hooks/useLoadingState.ts` | ~80 | Only barrel export, no consumers |
| `desktop/src/hooks/useSidebarState.ts` | ~200 | Only barrel export, no consumers (replaced by `useRightSidebarGroup`) |
| `desktop/src/hooks/useViewScope.ts` | ~150 | No imports found |
| `desktop/src/hooks/useWorkspaceId.ts` | ~30 | No imports found |

### Associated test files to delete:
- `desktop/src/hooks/useSidebarState.test.ts` (~1,194 lines)
- Any test files for the above hooks

### Code to modify:
- `desktop/src/hooks/index.ts` — remove exports for `useLoadingState`, `useMultiLoadingState`, `useChatSession`, `useSidebarState`
- `desktop/src/types/index.ts` — remove deprecated `SwarmWorkspace` interface (line ~704, kept "for backward compat with useChatSession")

---

## Priority 3: Dead Frontend Components

**Status**: Explicitly removed from WorkspaceExplorer but files still exist  
**Risk**: None  
**Effort**: ~20 minutes

| Component | Lines | Confirmed Dead |
|-----------|-------|----------------|
| `desktop/src/components/workspace-explorer/ArtifactsFileTree.tsx` | ~130 | Not rendered anywhere. References `ContextFiles`, `Transcripts`, `Artifacts` — all legacy |
| `desktop/src/components/workspace-explorer/OverviewContextCard.tsx` | ~? | Not rendered anywhere |
| `desktop/src/components/workspace-explorer/WorkspaceFooter.tsx` | ~? | Not rendered anywhere |
| `desktop/src/components/workspace-explorer/AddWorkspaceDialog.tsx` | ~? | Not rendered anywhere (multi-workspace is gone) |
| `desktop/src/components/workspace-explorer/FileTree.tsx` | ~15 | Just a re-export shim for `FileTreeItem` type |

### Code to modify:
- `desktop/src/components/workspace-explorer/index.ts` — remove exports for deleted components

**Note**: `FileTree.tsx` is a type re-export shim. Consumers import `FileTreeItem` from `FileTreeNode.tsx` directly. Can delete `FileTree.tsx` and update any remaining imports.

---

## Priority 4: Dead Backend Module

**Status**: Defined but never imported  
**Risk**: None  
**Effort**: ~5 minutes

| Module | Lines | Reason |
|--------|-------|--------|
| `backend/core/local_skill_manager.py` | 580 | `LocalSkillManager` class + global instance. Zero imports in routers, main, or any other module |

---

## Priority 5: Dead Frontend Service

**Status**: Re-export shim with no consumers  
**Risk**: None  
**Effort**: ~5 minutes

| File | Lines | Reason |
|------|-------|--------|
| `desktop/src/services/projects.ts` | 15 | Re-exports `projectService` from `workspace.ts` as `projectsService`. Zero imports |

---

## Priority 6: Explorer Tree Filtering (chats/ visibility)

**Status**: `chats/` directory appears in explorer under "Active Work" zone  
**Risk**: Low  
**Effort**: ~30 minutes

The `chats/` directory at workspace root is created by `TSCCSnapshotManager` for TSCC thread snapshots. It's internal runtime data, not user content. It appears in the explorer because `_build_tree` walks the entire filesystem.

### Options (pick one):
1. **Filter in `_should_include`**: Add `chats` to a hidden set — simplest
2. **Filter in `_build_tree`**: Skip top-level dirs not in `FOLDER_STRUCTURE` + `Projects/` children
3. **Move `chats/` to a dotfolder**: e.g., `.chats/` — automatically filtered by existing dotfile logic

Option 3 is cleanest long-term. Requires updating `TSCCSnapshotManager._get_snapshot_dir()` and `PROJECT_SYSTEM_FOLDERS`.

---

## Priority 7: Root-Level Demo/Test Files

**Status**: Intentional utilities but cluttering backend root  
**Risk**: None  
**Effort**: ~15 minutes

| File | Purpose |
|------|---------|
| `backend/demo_boto3_tool_use.py` | Bedrock tool-use examples |
| `backend/demo_reasoning_test.py` | Thinking model reasoning output |
| `backend/demo_simple_test.py` | Simplified weather tool demo |
| `backend/test_chat_tables.py` | Standalone CRUD test (not in pytest suite) |

### Recommendation:
Move to `backend/examples/` directory. Not blocking, just organization.

---

## Priority 8: Large File Monitoring (No Action Now)

These files are approaching complexity thresholds. No immediate action needed, but track for next milestone:

| File | Lines | Notes |
|------|-------|-------|
| `desktop/src/hooks/useChatStreamingLifecycle.ts` | 1,276 | Consider splitting after streaming bug is fully verified in live testing |
| `desktop/src/pages/ChatPage.tsx` | 1,146 | Streaming lifecycle already extracted; monitor |
| `backend/core/agent_manager.py` | 2,107 | Orchestrates 6 helpers; consider extracting session/permission logic |
| `backend/core/swarm_workspace_manager.py` | 1,706 | Project CRUD + templates; could split project ops |
| `desktop/src/pages/SkillsPage.tsx` | 1,296 | Large but cohesive |

---

## Priority 9: Stale Test Docstrings

Several test files reference the old folder structure (`Artifacts/`, `ContextFiles/`, `Transcripts/`) in their docstrings. These should be updated to reflect the current `Knowledge/` + `Projects/` structure:

- `backend/tests/test_property_folder_structure.py`
- `backend/tests/test_property_workspace_folders.py`

---

## Execution Sequence

**Phase A — Safe deletions (no behavioral change):**
1. Delete dead hooks (Priority 2)
2. Delete dead components (Priority 3)
3. Delete `local_skill_manager.py` (Priority 4)
4. Delete `services/projects.ts` re-export (Priority 5)
5. Update barrel exports (`hooks/index.ts`, `workspace-explorer/index.ts`)
6. Run frontend tests to confirm no breakage

**Phase B — ContextFiles removal (behavioral change):**
1. Remove `agent_manager.py` fallback path
2. Delete `context_manager.py` + tests
3. Remove workspace_config context endpoints + frontend methods
4. Delete `ContextFiles/` from disk
5. Update steering rules
6. Run full test suite (frontend + backend)

**Phase C — Explorer tree fix:**
1. Implement chosen filtering approach for `chats/`
2. Update property tests
3. Verify explorer renders correctly

**Phase D — Cosmetic:**
1. Move demo files to `backend/examples/`
2. Update stale docstrings
3. Regenerate `seed.db` if schema changed
