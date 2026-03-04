# Codebase Summary: SwarmWS Restructure + Git Init

**Spec:** `.kiro/specs/swarmws-restructure-git-init/`
**Commit:** `85a22bd` — `refactor: SwarmWS restructure + git init + TSCC simplification`
**Date:** 2026-03-05

---

## Change Metrics

| Metric | Count |
|--------|-------|
| Total files changed | 128 |
| Lines added | 2,215 |
| Lines deleted | 12,028 |
| Net lines removed | **9,813** |
| Backend files changed | 99 |
| Backend lines added | 1,522 |
| Backend lines deleted | 10,031 |
| Frontend files changed | 21 |
| Frontend lines added | 556 |
| Frontend lines deleted | 1,553 |
| Production files deleted | 6 |
| Test files deleted | 9 |
| New test files created | 1 |
| Config files changed | 3 |

## Spec Requirements Addressed

**6 requirement groups, 36 individual requirements — all addressed.**

| Req # | Title | Sub-reqs | Status |
|-------|-------|----------|--------|
| 1 | Simplified Folder Structure | 10 | ✅ Complete |
| 2 | Git Repository | 4 | ✅ Complete |
| 3 | Session Auto-Commit | 4 | ✅ Complete |
| 4 | Context Refresh | 5 | ✅ Complete |
| 5 | Delete All Legacy Context Code | 9 | ✅ Complete |
| 6 | Simplify TSCC to System Prompt Viewer | 8 | ✅ Complete |

## Tasks Completed

**10 top-level tasks, 34 sub-tasks — all complete.**

| Task | Description | Sub-tasks |
|------|-------------|-----------|
| 1 | Simplify swarm_workspace_manager.py | 8 |
| 2 | Add git initialization | 3 |
| 3 | Checkpoint — verify workspace creation | 0 |
| 4 | Add session auto-commit | 2 |
| 5 | Update ContextDirectoryLoader + AgentManager | 5 |
| 6 | Delete legacy context code | 7 |
| 7 | Delete legacy tests | 2 |
| 8 | Simplify TSCC backend | 6 |
| 9 | Simplify TSCC frontend | 4 |
| 10 | Final checkpoint | 0 |

## Files Deleted (Production)

| File | Purpose |
|------|---------|
| `backend/core/context_assembler.py` | Legacy context assembly pipeline |
| `backend/core/context_snapshot_cache.py` | Legacy snapshot caching |
| `backend/core/context_manager.py` | Legacy context manager |
| `backend/core/tscc_snapshot_manager.py` | Legacy TSCC snapshot tracking |
| `backend/core/telemetry_emitter.py` | Legacy telemetry event emission |
| `backend/routers/context.py` | Legacy context preview API |

## Files Deleted (Tests)

| File | Reason |
|------|--------|
| `backend/tests/test_context_assembler.py` | Tested deleted module |
| `backend/tests/test_context_manager.py` | Tested deleted module |
| `backend/tests/test_context_snapshot_cache.py` | Tested deleted module |
| `backend/tests/test_tscc_snapshot_manager.py` | Tested deleted module |
| `backend/tests/test_telemetry_emitter.py` | Tested deleted module |
| `backend/tests/test_agent_telemetry_integration.py` | Tested deleted telemetry |
| `backend/tests/test_context_preview_api.py` | Tested deleted router |
| `backend/tests/test_l0_filtering.py` | Tested deleted L0 logic |
| `backend/tests/test_property_context_file.py` | Tested deleted context files |

## Key Modified Files (Backend)

| File | Changes |
|------|---------|
| `core/swarm_workspace_manager.py` | Simplified constants, create_folder_structure, verify_integrity; added _ensure_git_repo, _cleanup_legacy_content, GITIGNORE_CONTENT |
| `core/agent_manager.py` | Added _auto_commit_workspace, system prompt metadata storage; removed telemetry emission, ContextAssembler refs |
| `core/context_directory_loader.py` | Updated _is_l1_fresh with git status + mtime fallback |
| `core/tscc_state_manager.py` | Simplified — removed apply_event and all telemetry handling |
| `routers/tscc.py` | Added GET /api/chat/{session_id}/system-prompt; removed snapshot endpoints |
| `routers/workspace_api.py` | Updated _should_include to show dot-files; hide only .git and chats |
| `schemas/tscc.py` | Added SystemPromptMetadata, SystemPromptFileInfo; deprecated unused TSCCLiveState fields |
| `schemas/workspace_config.py` | Removed is_system_managed from TreeNodeResponse |

## Key Modified Files (Frontend)

| File | Changes |
|------|---------|
| `components/workspace-explorer/VirtualizedTree.tsx` | Cleared ROOT_FILES; dot-dirs render before zones |
| `components/workspace-explorer/TreeNodeRow.tsx` | Removed isSystemManaged prop and lock badge |
| `pages/chat/components/TSCCModules.tsx` | Replaced 5 modules with single SystemPromptModule |
| `pages/chat/components/TSCCPanel.tsx` | Updated for SystemPromptModule; shows file count + tokens |
| `pages/chat/components/TSCCPopoverButton.tsx` | Updated for SystemPromptModule |
| `hooks/useTSCCState.ts` | Simplified — fetches metadata from endpoint; removed telemetry |
| `hooks/useChatStreamingLifecycle.ts` | Removed telemetry event handling |
| `pages/ChatPage.tsx` | Removed dead telemetry refs |
| `services/tscc.ts` | Added getSystemPromptMetadata; snake→camelCase conversion |
| `types/index.ts` | Added SystemPromptMetadata, SystemPromptFileInfo; removed isSystemManaged |

## Post-Scan Fixes (Code Quality + Security)

| Severity | Finding | Fix |
|----------|---------|-----|
| 🔴 High | _auto_commit_workspace used git diff --quiet (missed untracked files) | Changed to git status --porcelain |
| 🟡 Medium | Per-file truncation detection was global, not per-file | Fixed to check [Truncated: {section_name}] per file |
| 🟡 Medium | TSCCLiveState had dead fields after simplification | Documented as deprecated |
| 🟢 Low | Duplicate import in useTSCCState | Merged to single import line |
| 🟢 Low | Unbounded Maps in useTSCCState | Added _capMap with 200-entry cap |
| 🟢 Low | freshness() didn't handle invalid dates | Added NaN guard |

## Test Results

| Suite | Passed | Failed | Skipped |
|-------|--------|--------|---------|
| Backend (pytest) | 636 | 1 (pre-existing) | 40 |
| Frontend (vitest) | 909 | 0 | 0 |
| Frontend build (tsc + vite) | ✅ | — | — |
