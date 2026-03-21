# Implementation Plan: Compaction Guard Redesign

## Overview

Complete rewrite of `CompactionGuard` with two-phase architecture (PASSIVE → ACTIVE), graduated escalation (MONITORING → SOFT_WARN → HARD_WARN → KILL), set-overlap loop detection, rich work summaries, and frontend SSE plumbing. Backend is Python, frontend is TypeScript.

## Tasks

- [-] 1. Rewrite CompactionGuard core class
  - [~] 1.1 Define enums and data models in `backend/core/compaction_guard.py`
    - Replace `LoopAction` with `GuardPhase` (PASSIVE/ACTIVE) and `EscalationLevel` (MONITORING/SOFT_WARN/HARD_WARN/KILL) enums
    - Replace `ToolCall` dataclass with `ToolRecord` dataclass (tool_name, input_hash, input_detail, timestamp)
    - Keep `_hash_input()` static method (unchanged logic)
    - Remove `_REPEAT_WHITELISTED_TOOLS`, `_EXACT_REPEAT_LIMIT`, `_TOOL_NAME_LIMIT`, `_CONTEXT_WARN_PCT`, `_CONTEXT_STOP_PCT` module constants
    - Add `_CONTEXT_ACTIVATION_PCT = 85`, `_OVERLAP_THRESHOLD = 0.60`, `_MIN_POST_COMPACTION_CALLS = 5`, `_SINGLE_TOOL_REPEAT_LIMIT = 5`, `_COMPACTION_DROP_THRESHOLD = 30` module constants
    - _Requirements: 1.1, 2.1, 3.2, 3.3, 4.4_

  - [ ] 1.2 Implement CompactionGuard __init__, properties, and state management
    - Initialize `_phase = GuardPhase.PASSIVE`, `_escalation = EscalationLevel.MONITORING`
    - Initialize `_context_pct`, `_context_tokens`, `_prev_context_pct` (for heuristic detection)
    - Initialize `_pre_compaction_set: set[tuple[str, str]]`, `_rolling_baseline_set: set[tuple[str, str]]`
    - Initialize `_post_compaction_sequence: list[tuple[str, str]]`, `_tool_records: list[ToolRecord]`
    - Implement `phase`, `escalation`, `context_pct` properties
    - _Requirements: 1.1, 8.4_

  - [ ] 1.3 Implement `record_tool_call()` method
    - Hash input via `_hash_input()`, create `ToolRecord` with first 200 chars of JSON-serialized input as `input_detail`
    - Append `(tool_name, input_hash)` to `_post_compaction_sequence`
    - Add `(tool_name, input_hash)` to `_rolling_baseline_set`
    - Append `ToolRecord` to `_tool_records`
    - _Requirements: 1.2, 5.1, 5.3_

  - [ ] 1.4 Implement `update_context_usage()` with heuristic compaction detection
    - Compute `context_pct` from `input_tokens / context_window * 100`
    - Use `PromptBuilder.get_model_context_window(model)` with 200K fallback
    - Snapshot `_rolling_baseline_set` on every call (copy current tool set BEFORE checking for drop)
    - Detect ≥30pt drop: if `_prev_context_pct - new_pct >= 30`, call `activate()` using the pre-drop snapshot
    - Update `_prev_context_pct` after detection check
    - _Requirements: 2.3, 2.4, 7.3, 7.4_

  - [ ] 1.5 Implement `activate()` method
    - Transition `_phase` from PASSIVE to ACTIVE (no-op if already ACTIVE, log debug)
    - Copy `_rolling_baseline_set` to `_pre_compaction_set`
    - Clear `_post_compaction_sequence`
    - Log info with baseline size and context_pct
    - _Requirements: 1.3, 1.5, 7.1, 7.2_

  - [ ] 1.6 Implement `_detect_loop()` internal method
    - Set-overlap: count post-compaction calls whose `(tool_name, input_hash)` exists in `_pre_compaction_set`. If count > 60% of total post-compaction calls AND total ≥ 5 → loop detected
    - Single-tool repetition: `Counter(_post_compaction_sequence).most_common(1)` — if top pair ≥ 5 → loop detected
    - Return bool
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ] 1.7 Implement `check()` method with graduated escalation
    - If PASSIVE phase: return `EscalationLevel.MONITORING` (no interference)
    - If ACTIVE phase AND `context_pct < 85`: return `EscalationLevel.MONITORING`
    - If ACTIVE phase AND `context_pct >= 85`: call `_detect_loop()`
    - If loop detected: escalate `_escalation` one step (MONITORING→SOFT_WARN→HARD_WARN→KILL), return new level
    - If no loop: return `EscalationLevel.MONITORING`
    - After KILL, subsequent calls continue returning KILL
    - Wrap in try/except — on error, log and return MONITORING
    - _Requirements: 1.4, 2.1, 2.2, 4.1, 4.2, 4.3, 4.4_

  - [ ] 1.8 Implement `work_summary()` with rich input details
    - Return empty string if no `_tool_records`
    - Group by tool_name, sort by count descending
    - For each group: include tool name, count, and up to 5 representative `input_detail` strings (truncated to 200 chars)
    - Include "CRITICAL: Do NOT re-run" instructions
    - Wrap per-tool extraction in try/except with fallback `"<tool_name>(...)"` 
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ] 1.9 Implement `build_guard_event()` SSE event builder
    - Accept `EscalationLevel` parameter
    - Return `None` for `MONITORING` (caller skips)
    - For SOFT_WARN/HARD_WARN/KILL: return dict with `type="compaction_guard"`, `subtype` matching level value, `context_pct`, `message`, `pattern_description`
    - Pattern description: "60% of post-compaction tool calls match pre-compaction baseline (N/M calls)" or "Tool X called N times with identical input"
    - _Requirements: 6.4_

  - [ ] 1.10 Implement `reset()` and `reset_all()` lifecycle methods
    - `reset()`: Reset `_escalation` to MONITORING, clear `_post_compaction_sequence`. Preserve `_phase`, `_pre_compaction_set`, `_context_pct`, `_tool_records`
    - `reset_all()`: Reset everything to initial state — PASSIVE phase, MONITORING escalation, zero context, empty collections
    - _Requirements: 4.5, 8.1, 8.2, 8.3_

  - [ ]* 1.11 Write property test: PASSIVE phase never interferes (Property 1)
    - **Property 1: PASSIVE phase never interferes**
    - Use Hypothesis to generate arbitrary tool names, inputs, token counts
    - Assert `check()` always returns `MONITORING` while phase is PASSIVE
    - Tag: `Feature: compaction-guard-redesign, Property 1: PASSIVE phase never interferes`
    - **Validates: Requirements 1.2, 1.4**

  - [ ]* 1.12 Write property test: Activation snapshots baseline (Property 2)
    - **Property 2: Activation snapshots baseline and clears post-compaction state**
    - Generate random tool call sequences, call `activate()`, verify baseline contains pre-activation pairs, post-compaction sequence is empty, phase is ACTIVE
    - Tag: `Feature: compaction-guard-redesign, Property 2: Activation snapshots baseline and clears post-compaction state`
    - **Validates: Requirements 1.5, 8.4**

  - [ ]* 1.13 Write property test: 85% context threshold gates detection (Property 3)
    - **Property 3: 85% context threshold gates detection**
    - In ACTIVE phase with loop pattern present, verify `check()` returns MONITORING when context < 85%, non-MONITORING when ≥ 85%
    - Tag: `Feature: compaction-guard-redesign, Property 3: 85% context threshold gates detection`
    - **Validates: Requirements 2.1, 2.2**

  - [ ]* 1.14 Write property test: Set-overlap loop detection (Property 4)
    - **Property 4: Set-overlap loop detection**
    - Generate baseline sets and post-compaction sequences, verify loop detected iff >60% overlap with ≥5 calls
    - Tag: `Feature: compaction-guard-redesign, Property 4: Set-overlap loop detection`
    - **Validates: Requirements 3.1, 3.2, 3.4**

  - [ ]* 1.15 Write property test: Single-tool repetition detection (Property 5)
    - **Property 5: Single-tool repetition detection**
    - Generate sequences with a single pair appearing ≥5 times, verify loop detected regardless of baseline
    - Tag: `Feature: compaction-guard-redesign, Property 5: Single-tool repetition detection`
    - **Validates: Requirements 3.3**

  - [ ]* 1.16 Write property test: Strict escalation order (Property 6)
    - **Property 6: Strict escalation order**
    - Trigger multiple `check()` calls with loop present, verify SOFT_WARN → HARD_WARN → KILL order, no skips
    - Tag: `Feature: compaction-guard-redesign, Property 6: Strict escalation order`
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4**

  - [ ]* 1.17 Write property test: Reset preserves phase and baseline (Property 7)
    - **Property 7: Reset preserves phase and baseline**
    - In ACTIVE phase with any escalation, call `reset()`, verify phase=ACTIVE, baseline unchanged, context_pct unchanged, escalation=MONITORING, post-compaction sequence empty
    - Tag: `Feature: compaction-guard-redesign, Property 7: Reset preserves phase and baseline`
    - **Validates: Requirements 4.5, 8.1, 8.3**

  - [ ]* 1.18 Write property test: Full reset restores initial state (Property 8)
    - **Property 8: Full reset restores initial state**
    - From any state, call `reset_all()`, verify phase=PASSIVE, escalation=MONITORING, context_pct=0, all collections empty
    - Tag: `Feature: compaction-guard-redesign, Property 8: Full reset restores initial state`
    - **Validates: Requirements 8.2**

  - [ ]* 1.19 Write property test: Heuristic compaction detection (Property 9)
    - **Property 9: Heuristic compaction detection**
    - Two consecutive `update_context_usage()` calls where second is ≥30pt lower, verify phase transitions to ACTIVE
    - Tag: `Feature: compaction-guard-redesign, Property 9: Heuristic compaction detection`
    - **Validates: Requirements 7.3**

  - [ ]* 1.20 Write unit tests for edge cases and SSE event structure
    - Test initialization state (PASSIVE, MONITORING)
    - Test unknown model fallback (200K)
    - Test empty work summary when no tools recorded
    - Test hash consistency (dict key order, None input, string input)
    - Test `build_guard_event(MONITORING)` returns None
    - Test `build_guard_event(SOFT_WARN/HARD_WARN/KILL)` returns correct structure with `type="compaction_guard"`
    - Test work summary includes input details (file paths, commands) truncated to 200 chars
    - Test work summary sorted by count descending
    - _Requirements: 1.1, 2.4, 5.3, 5.5, 5.6, 6.4_

- [ ] 2. Checkpoint — Verify backend guard implementation
  - Ensure all tests pass with `cd backend && pytest tests/test_compaction_guard.py -v`
  - Ask the user if questions arise.

- [ ] 3. Update SessionUnit integration points
  - [ ] 3.1 Update imports and type references in `backend/core/session_unit.py`
    - Replace `from .compaction_guard import LoopAction` with `from .compaction_guard import EscalationLevel, GuardPhase`
    - Update all `LoopAction` references to `EscalationLevel`
    - _Requirements: 1.1_

  - [ ] 3.2 Update `_stream_response()` guard check logic
    - Replace `LoopAction.THROTTLE_WARN/THROTTLE_STOP/LOOP_DETECTED` handling with `EscalationLevel` mapping
    - For `MONITORING`: skip (no event)
    - For `SOFT_WARN/HARD_WARN`: yield guard event via `self._compaction_guard.build_guard_event(level)`
    - For `KILL`: yield guard event, then interrupt session
    - _Requirements: 4.1, 4.2, 4.3, 6.4_

  - [ ] 3.3 Update `compact()` method to call `activate()` and emit `context_compacted`
    - After successful compact command: call `self._compaction_guard.activate()`
    - Inject `self._compaction_guard.work_summary()` into compact instructions
    - Emit `context_compacted` SSE event (already handled by frontend)
    - _Requirements: 5.4, 7.2_

  - [ ] 3.4 Update `_spawn()` to call `reset_all()` on COLD → IDLE
    - Ensure `self._compaction_guard.reset_all()` is called during subprocess spawn
    - _Requirements: 8.2_

  - [ ] 3.5 Update `continue_with_answer()` and `continue_with_permission()` to call `reset()`
    - Call `self._compaction_guard.reset()` at the start of these methods
    - _Requirements: 8.3_

  - [ ] 3.6 Remove old SSE event builder methods from CompactionGuard
    - Remove `build_throttle_warning_event()`, `build_throttle_stop_event()`, `build_loop_warning_event()` (replaced by `build_guard_event()`)
    - Update any SessionUnit code that called these old methods
    - _Requirements: 6.4_

- [ ] 4. Checkpoint — Verify backend integration
  - Ensure all backend tests pass with `cd backend && pytest -v`
  - Ask the user if questions arise.

- [ ] 5. Add frontend SSE plumbing
  - [ ] 5.1 Add `CompactionGuardEvent` interface and `StreamEvent` fields in `desktop/src/types/index.ts`
    - Add `CompactionGuardEvent` interface with `subtype: 'soft_warn' | 'hard_warn' | 'kill'`, `contextPct: number`, `message: string`, `patternDescription?: string`
    - Add `'compaction_guard'` to `StreamEvent.type` union
    - Add `subtype?: string`, `contextPct?: number`, `patternDescription?: string` fields to `StreamEvent` (if not already present)
    - _Requirements: 6.4, 6.5_

  - [ ] 5.2 Add `compactionGuard` field to `UnifiedTab` in `desktop/src/hooks/useUnifiedTabState.ts`
    - Add `compactionGuard: CompactionGuardEvent | null` to `UnifiedTab` interface
    - Initialize to `null` in `createDefaultTab()` and `hydrateTab()`
    - _Requirements: 6.5_

  - [ ] 5.3 Add `compaction_guard` SSE event handler in `desktop/src/hooks/useChatStreamingLifecycle.ts`
    - Add `compactionGuard` React state: `const [compactionGuard, setCompactionGuard] = useState<CompactionGuardEvent | null>(null)`
    - In `createStreamHandler`, add `else if (event.type === 'compaction_guard')` branch
    - Parse event into `CompactionGuardEvent` with camelCase conversion (snake_case SSE → camelCase TS)
    - Write to `tabMapRef` (authoritative) via `tabState.compactionGuard = guardEvent`
    - Mirror to React state if active tab: `setCompactionGuard(guardEvent)`
    - Handle unknown subtypes gracefully (ignore, don't crash)
    - Handle missing fields with defaults (`contextPct: 0`, `message: 'Guard event'`)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 5.4 Write frontend tests for SSE event handling
    - Test `compaction_guard` event parsing with valid subtypes (soft_warn, hard_warn, kill)
    - Test unknown subtype handling (no crash)
    - Test missing field defaults
    - Test display mirror pattern (tabMapRef write + React state mirror)
    - Run with `cd desktop && npm test -- --run`
    - _Requirements: 6.1, 6.2, 6.3, 6.5_

- [ ] 6. Final checkpoint — Ensure all tests pass
  - Run `cd backend && pytest -v` and `cd desktop && npm test -- --run`
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests (1.11–1.19) validate universal correctness properties from the design document
- Unit tests (1.20, 5.4) validate specific examples and edge cases
- The existing `test_compaction_guard.py` will be completely rewritten — old tests reference the removed `LoopAction` enum
